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
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

LOCK_PATH = Path('/tmp') / f'carr-disposable-postgres-{os.getuid()}.lock'
_thread_lock = threading.RLock()
_depth = 0
_fixtures: list['DisposablePostgres'] = []
_main_launches = 0
_pending_signal = None


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

    def run(self, command, **kwargs):
        global _main_launches, _pending_signal
        args = [str(arg) for arg in command]
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
                kwargs.setdefault('env', self.env)
                return (self.runner or subprocess.run)(args, **kwargs)
            finally:
                if main_launch:
                    _main_launches -= 1
                    if _pending_signal is not None and not _main_launches:
                        signum, _pending_signal = _pending_signal, None
                        _on_signal(signum, None)

    def close(self):
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
                if self.root.exists():
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
