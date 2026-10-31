"""Bound concurrent disposable PostgreSQL proofs without sharing their clusters.

A proof may need two clusters to demonstrate role isolation. Hold the host-wide
budget before initializing its first cluster and through final shutdown. Nested
fixtures in that proof reuse the lease; unrelated processes wait for teardown.
The primary CI database is outside this lease because its child proofs need it.
"""
from contextlib import contextmanager
import atexit
import fcntl
import json
import os
import pwd
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading


def postgres_bin_dir(names=('initdb', 'pg_ctl', 'createdb', 'psql', 'postgres')):
    candidates = []
    for key in ('CARR_TEST_POSTGRES_BIN', 'CARR_LOCAL_PG_BIN_DIR'):
        override = os.environ.get(key, '').strip()
        if override:
            candidates.append(Path(override))
    located = shutil.which('initdb')
    if located:
        candidates.append(Path(located).resolve().parent)
    candidates.extend(Path(path) for path in (
        '/opt/homebrew/opt/postgresql@17/bin', '/usr/local/opt/postgresql@17/bin',
        '/opt/homebrew/opt/postgresql@16/bin', '/usr/local/opt/postgresql@16/bin',
        '/usr/lib/postgresql/17/bin', '/usr/lib/postgresql/16/bin'))
    for directory in candidates:
        if not directory.is_absolute():
            raise ValueError('PostgreSQL binary directory must be an absolute path')
        if all((directory / name).is_file() and os.access(directory / name, os.X_OK)
               for name in names):
            return directory
    raise FileNotFoundError('PostgreSQL client/server binaries were not found')

LOCK_PATH = Path('/tmp') / f'carr-disposable-postgres-{os.getuid()}.lock'
_thread_lock = threading.RLock()
_depth = 0
_fixtures: list['DisposablePostgres'] = []
_main_launches = 0
_pending_signal = None
_supervising = False


def _fixture_segment_ids(directory, prefix):
    segments = set()
    for root in directory.iterdir():
        if not root.name.startswith(prefix) or root.is_symlink() or not root.is_dir():
            continue
        if root.stat().st_uid != os.getuid():
            continue
        for pid_file in root.rglob('postmaster.pid'):
            try:
                if pid_file.is_symlink() or not pid_file.resolve().is_relative_to(root):
                    continue
                fields = pid_file.read_text().splitlines()[6].split()
                if len(fields) == 2 and all(field.isdecimal() for field in fields):
                    segments.add(str(int(fields[1])))
            except (OSError, UnicodeError, IndexError):
                continue
    return segments


def _orphan_segments(directory, prefix):
    candidates = _fixture_segment_ids(directory, prefix)
    rows = {}
    columns = set()
    commands = [['ipcs', '-m', '-a']] if sys.platform == 'darwin' else [
        ['ipcs', '-m'], ['ipcs', '-m', '-p']]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=5, check=True, env={**os.environ, 'LC_ALL': 'C'})
        header = []
        for line in result.stdout.splitlines():
            fields = line.split()
            names = [field.lower() for field in fields]
            if 'owner' in names and ('id' in names or 'shmid' in names):
                header = ['id' if field == 'shmid' else field for field in names]
                columns.update(header)
                continue
            if not header or len(fields) <= max(header.index('id'), header.index('owner')):
                continue
            row = dict(zip(header, fields))
            if not row['id'].isdigit():
                continue
            previous = rows.setdefault(row['id'], row)
            if previous['owner'] != row['owner']:
                raise ValueError('shared-memory owner changed during inspection')
            previous.update(row)
    if not {'id', 'owner', 'nattch', 'cpid'} <= columns:
        raise ValueError('unrecognized shared-memory table')
    owner = pwd.getpwuid(os.getuid()).pw_name
    orphans = set()
    for ident, row in rows.items():
        if ident not in candidates:
            continue
        if row['owner'] not in (owner, str(os.getuid())) or row.get('nattch') != '0':
            continue
        creator = int(row.get('cpid', '0'))
        if creator <= 0:
            continue
        try:
            os.kill(creator, 0)
        except ProcessLookupError:
            orphans.add((ident, creator))
        except PermissionError:
            pass
    return orphans


def _reclaim_orphan_segments(directory, prefix):
    try:
        for ident, creator in _orphan_segments(directory, prefix):
            if (ident, creator) in _orphan_segments(directory, prefix):
                subprocess.run(['ipcrm', '-m', ident], capture_output=True, text=True,
                               timeout=5, check=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'disposable PostgreSQL shared-memory preflight unavailable: {exc}', file=sys.stderr)


def _diagnose_shared_memory_failure(output, directory, prefix):
    output = str(output).lower()
    if 'no space left on device' not in output or not any(
            term in output for term in ('shared memory', 'shared-memory', 'shmget')):
        return
    try:
        count = f'{len(_orphan_segments(directory, prefix))} orphan segments (owned, unattached, dead creator)'
    except (OSError, ValueError, subprocess.SubprocessError):
        count = 'orphan segment count unavailable'
    raise RuntimeError(
        f'disposable PostgreSQL environment fault: System V shared-memory exhaustion; {count}; {output}')


def _close_all():
    failures = []
    for fixture in list(reversed(_fixtures)):
        try:
            fixture.close()
        except Exception as exc:
            failures.append(str(exc))
    if failures:
        print('\n'.join(failures), file=sys.stderr)
    return bool(failures)


def _on_signal(signum, frame):
    global _pending_signal
    if _main_launches:
        _pending_signal = signum
        return
    _close_all()
    raise SystemExit(128 + signum)


atexit.register(_close_all)
if threading.current_thread() is threading.main_thread():
    for _signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(_signum, _on_signal)


class DisposablePostgres:
    def __init__(self, prefix, pg_ctl, env=None, runner=None, directory='/tmp'):
        self._prefix = prefix
        self.pg_ctl = Path(pg_ctl)
        self.env = dict(env or os.environ, LC_ALL='C')
        self.runner = runner
        # Real launches run in the same stdin-held supervisor the Node fixture
        # uses: it owns the root before initdb and stops every cluster it
        # started when this process closes it or dies. A custom runner or a
        # stub pg_ctl path (selftest seams) keeps the in-process path.
        self._supervisor = None
        if runner is None and not _supervising and os.access(pg_ctl, os.X_OK):
            self._supervisor = subprocess.Popen(
                [sys.executable, '-u', __file__, '--supervise', '--prefix', prefix,
                 '--pg-ctl', str(pg_ctl), '--directory', str(directory)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=self.env,
                start_new_session=True)  # terminal signals reach only this process
            line = self._supervisor.stdout.readline().strip()
            if not line.startswith('/'):
                self._supervisor.stdin.close()
                self._supervisor.wait()
                raise RuntimeError('disposable PostgreSQL supervisor failed to start')
            self.root = Path(line)
        else:
            self.root = Path(tempfile.mkdtemp(prefix=prefix, dir=directory)).resolve()
        self._identity = self.root.stat().st_ino
        self._data = []
        self._lock = threading.RLock()
        self._closed = False
        _fixtures.append(self)

    def register(self, data):
        data = Path(data).resolve()
        if not data.is_relative_to(self.root.resolve()):
            raise ValueError('cluster data must be inside its owned temporary root')
        if data not in self._data:
            self._data.append(data)

    def _failure_output(self, args, output):
        try:
            if '-l' in args:
                log = Path(args[args.index('-l') + 1]).resolve()
                if log.is_relative_to(self.root) and log.is_file():
                    with log.open('rb') as handle:
                        handle.seek(max(0, log.stat().st_size - 8192))
                        output += str(handle.read())
        except OSError:
            pass
        return output

    def _supervised(self, args, kwargs):
        cwd = kwargs.get('cwd')
        request = {'command': args, 'env': kwargs['env'], 'cwd': cwd and str(cwd),
                   'timeout': kwargs.get('timeout')}
        self._supervisor.stdin.write(json.dumps(request) + '\n')
        self._supervisor.stdin.flush()
        line = self._supervisor.stdout.readline()
        if not line:
            raise RuntimeError('disposable PostgreSQL supervisor exited during a launch')
        reply = json.loads(line)
        out, err = reply['stdout'], reply['stderr']
        if not any(kwargs.get(key) for key in ('text', 'universal_newlines', 'encoding')):
            out, err = out.encode(), err.encode()
        if kwargs.get('capture_output') or kwargs.get('stdout') == subprocess.PIPE:
            result = subprocess.CompletedProcess(args, reply['returncode'], out, err)
        else:
            if kwargs.get('stdout') != subprocess.DEVNULL:
                sys.stdout.write(reply['stdout'])
            if kwargs.get('stderr') != subprocess.DEVNULL:
                sys.stderr.write(reply['stderr'])
            result = subprocess.CompletedProcess(args, reply['returncode'])
        if kwargs.get('check'):
            result.check_returncode()
        return result

    def run(self, command, **kwargs):
        global _main_launches, _pending_signal
        args = [str(arg) for arg in command]
        if Path(args[0]).name == 'initdb' and '-D' in args and not any(
                arg.startswith('-E') or arg == '--encoding'
                or arg.startswith('--encoding=') for arg in args[1:]):
            args.append('--encoding=UTF8')
        launch = Path(args[0]).name == 'initdb' or (Path(args[0]).name == 'pg_ctl' and 'start' in args)
        if launch:
            self.register(args[args.index('-D') + 1])
        main_launch = launch and threading.current_thread() is threading.main_thread()
        with self._lock:
            if self._closed:
                raise RuntimeError('temporary PostgreSQL fixture is closed')
            if main_launch:
                _main_launches += 1
            try:
                # A caller-supplied env (e.g. a scrubbed one with no locale) must
                # not drop LC_ALL: PostgreSQL 17 on recent macOS refuses to start
                # ("postmaster became multithreaded") without a valid locale.
                kwargs['env'] = {**(kwargs.get('env') or self.env), 'LC_ALL': 'C'}
                if launch and self._supervisor:
                    result = self._supervised(args, kwargs)
                    if result.returncode:
                        _diagnose_shared_memory_failure(f'{result.stdout} {result.stderr}',
                                                        self.root.parent, self._prefix)
                    return result
                if launch:
                    _reclaim_orphan_segments(self.root.parent, self._prefix)
                try:
                    result = (self.runner or subprocess.run)(args, **kwargs)
                except Exception as exc:
                    if launch:
                        _diagnose_shared_memory_failure(
                            self._failure_output(args,
                                f'{exc} {getattr(exc, "stdout", "")} {getattr(exc, "stderr", "")}'),
                            self.root.parent, self._prefix)
                    raise
                if launch and result.returncode:
                    _diagnose_shared_memory_failure(
                        self._failure_output(args,
                            f'{getattr(result, "stdout", "")} {getattr(result, "stderr", "")}'),
                        self.root.parent, self._prefix)
                return result
            finally:
                if main_launch:
                    _main_launches -= 1
                    if _pending_signal is not None and not _main_launches:
                        signum, _pending_signal = _pending_signal, None
                        _on_signal(signum, None)

    def close(self, *, remove_root=True):
        # remove_root=False keeps evidence when the caller cannot prove it owned
        # every process; closing still deregisters it from the exit hook.
        with self._lock:
            if self._closed:
                return
            try:
                if self._supervisor:
                    self._close_supervisor(remove_root)
                for data in reversed(self._data if not self._supervisor else []):
                    kwargs = {'env': self.env, 'capture_output': True, 'timeout': 60}
                    if self.runner:
                        kwargs = {'env': self.env, 'capture': True}
                    run = self.runner or subprocess.run
                    stopped = run([str(self.pg_ctl), '-D', str(data), '-m', 'fast', '-w', 'stop'], **kwargs)
                    if stopped.returncode or (data / 'PG_VERSION').exists():
                        status = run([str(self.pg_ctl), '-D', str(data), 'status'], **kwargs)
                        if status.returncode != 3 and (data / 'PG_VERSION').exists():
                            raise RuntimeError('postmaster shutdown not verified')
                if remove_root and self.root.exists() and not self._supervisor:
                    if self.root.is_symlink() or self.root.stat().st_ino != self._identity:
                        raise RuntimeError('temporary root identity changed')
                    shutil.rmtree(self.root)
            except Exception as exc:
                if self in _fixtures:
                    _fixtures.remove(self)
                message = f'disposable PostgreSQL cleanup failed; retained {self.root}; log: {self.root / "postgres.log"}: {exc}'
                print(message, file=sys.stderr)
                raise RuntimeError(message) from exc
            self._closed = True
            if self in _fixtures:
                _fixtures.remove(self)

    def _close_supervisor(self, remove_root):
        supervisor = self._supervisor
        if supervisor.poll() is None and not supervisor.stdin.closed:
            try:
                supervisor.stdin.write(json.dumps({'close': {'remove_root': remove_root}}) + '\n')
                supervisor.stdin.close()
            except BrokenPipeError:
                pass
        code = supervisor.wait(timeout=180)
        supervisor.stdout.close()
        if code:
            raise RuntimeError(f'supervisor exited {code}')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@contextmanager
def postgres_fixture_group():
    global _depth
    with _thread_lock:
        if _depth:
            _depth += 1
            try:
                yield
            finally:
                _depth -= 1
            return
        # Never unlink: waiters must continue to lock the same inode. Kernel
        # ownership releases on exit/crash, so no stale PID cleanup is needed.
        with LOCK_PATH.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _depth = 1
            try:
                yield
            finally:
                _depth = 0
                fcntl.flock(handle, fcntl.LOCK_UN)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--supervise', action='store_true')
    parser.add_argument('--prefix', default='carr-local-pg-ci.node-')
    parser.add_argument('--pg-ctl')
    parser.add_argument('--data-name')
    parser.add_argument('--directory', default='/tmp')
    parser.add_argument('--snapshot', action='store_true')
    parser.add_argument('--snapshot-template', action='store_true')
    parser.add_argument('--snapshot-copy')
    parser.add_argument('--snapshot-key')
    parser.add_argument('--snapshot-baseline', action='store_true')
    parser.add_argument('--service-database', action='store_true')
    args = parser.parse_args()
    snapshot_schema = Path(__file__).resolve().parents[1] / 'db/schema.sql'
    if args.snapshot or args.snapshot_template:
        from postgres_snapshot_fixture import snapshot_key
        key = snapshot_key(snapshot_schema, args.snapshot_baseline)
        source_baseline = args.snapshot_baseline or args.snapshot_template
        expected = key.rsplit(':', 1)[0] + (':baseline' if source_baseline else ':current')
        if args.snapshot_copy and args.snapshot_key != expected:
            args.snapshot_copy = None
        migrate = not args.snapshot_baseline and (not args.snapshot_copy or args.snapshot_template)
    if args.supervise and (args.snapshot or args.snapshot_template or args.service_database) and os.environ.get('CARR_CI_DATABASE_URL'):
        from postgres_service_fixture import service_database
        with service_database(os.environ['CARR_CI_DATABASE_URL'], args.prefix,
                              snapshot_schema if not args.service_database else None,
                              args.snapshot_copy, template=args.snapshot_template,
                              migrate=migrate if not args.service_database else False) as metadata:
            if not args.service_database:
                metadata['key'] = key
            print(json.dumps(metadata), flush=True)
            for line in sys.stdin:
                if 'close' in json.loads(line):
                    break
                print(json.dumps({'returncode': 1, 'stdout': '',
                                  'stderr': 'CI-service snapshots support SQL connections only'}), flush=True)
        sys.exit(0)
    if args.supervise:
        # Callers hold stdin open. EOF (caller closed or died) stops every
        # cluster this process launched; a close request lets the caller retain
        # the root as evidence.
        _supervising = True
        binaries = postgres_bin_dir() if args.snapshot or args.snapshot_template else None
        if binaries:
            args.pg_ctl = str(binaries / 'pg_ctl')
        with DisposablePostgres(args.prefix, args.pg_ctl, directory=args.directory) as fixture:
            if args.data_name:
                fixture.register(fixture.root / args.data_name)
            if binaries:
                from postgres_snapshot_fixture import start_snapshot
                start_snapshot(fixture, binaries, snapshot_schema, args.snapshot_copy,
                               template=args.snapshot_template, migrate=migrate)
            if binaries:
                from urllib.parse import quote
                print(json.dumps({'root': str(fixture.root),
                    'connection': {'host': str(fixture.root), 'user': 'carr_ci', 'database': 'carr_ci'},
                    'dsn': f'postgresql://carr_ci@localhost/carr_ci?host={quote(str(fixture.root), safe="")}',
                    'snapshot': str(fixture.root / 'data'), 'key': key}), flush=True)
            else:
                print(fixture.root, flush=True)
            for line in sys.stdin:
                request = json.loads(line)
                if 'close' in request:
                    fixture.close(remove_root=request['close'].get('remove_root', True))
                    break
                options = {key: request[key] for key in ('env', 'cwd') if request.get(key)}
                try:
                    result = fixture.run(request['command'], capture_output=True, text=True,
                                         timeout=request.get('timeout') or 120, **options)
                    response = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
                except Exception as exc:
                    response = {'returncode': 1, 'stdout': '', 'stderr': str(exc)}
                try:
                    print(json.dumps(response), flush=True)
                except BrokenPipeError:
                    break  # caller died mid-launch; stop what it started
        sys.exit(0)
    # Node fixtures hold this subprocess's stdin open until their PostgreSQL
    # teardown completes. EOF also releases the lease if the caller exits.
    with postgres_fixture_group():
        print('ready', flush=True)
        sys.stdin.read()
