"""Bound concurrent disposable PostgreSQL proofs without sharing their clusters.

A proof may need two clusters to demonstrate role isolation. Hold the host-wide
budget before initializing its first cluster and through final shutdown. Nested
fixtures in that proof reuse the lease; unrelated processes wait for teardown.
The primary CI database is outside this lease because its child proofs need it.
"""
from contextlib import contextmanager
import atexit
import fcntl
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
        self.root = Path(tempfile.mkdtemp(prefix=prefix, dir=directory)).resolve()
        self._prefix = prefix
        self._identity = self.root.stat().st_ino
        self.pg_ctl = Path(pg_ctl)
        self.env = dict(env or os.environ, LC_ALL='C')
        self.runner = runner
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
                for data in reversed(self._data):
                    kwargs = {'env': self.env, 'capture_output': True, 'timeout': 60}
                    if self.runner:
                        kwargs = {'env': self.env, 'capture': True}
                    run = self.runner or subprocess.run
                    stopped = run([str(self.pg_ctl), '-D', str(data), '-m', 'fast', '-w', 'stop'], **kwargs)
                    if stopped.returncode or (data / 'PG_VERSION').exists():
                        status = run([str(self.pg_ctl), '-D', str(data), 'status'], **kwargs)
                        if status.returncode != 3 and (data / 'PG_VERSION').exists():
                            raise RuntimeError('postmaster shutdown not verified')
                if remove_root and self.root.exists():
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
    parser.add_argument('--data-name', default='data')
    args = parser.parse_args()
    if args.supervise:
        import json
        with DisposablePostgres(args.prefix, args.pg_ctl) as fixture:
            fixture.register(fixture.root / args.data_name)
            print(fixture.root, flush=True)
            for line in sys.stdin:
                request = json.loads(line)
                try:
                    result = fixture.run(request['command'], capture_output=True, text=True, timeout=120)
                    response = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
                except Exception as exc:
                    response = {'returncode': 1, 'stdout': '', 'stderr': str(exc)}
                print(json.dumps(response), flush=True)
        sys.exit(0)
    # Node fixtures hold this subprocess's stdin open until their PostgreSQL
    # teardown completes. EOF also releases the lease if the caller exits.
    with postgres_fixture_group():
        print('ready', flush=True)
        sys.stdin.read()
