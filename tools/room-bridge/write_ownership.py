"""Allow one dispatched writer per checkout. Separate worktrees do not contend."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys

from desks import DeskError
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.git_env import scrubbed_env

CHECKOUT_FD = None


def process_owner() -> dict:
    return {'host': socket.gethostname(), 'pid': os.getpid(),
            'start_time': process_start(os.getpid())}


def _local_identity(identity: dict) -> bool:
    return identity.get('host') == socket.gethostname()


def process_start(pid: int) -> str | None:
    """Read kernel start time; an unavailable identity never authorizes release."""
    try:
        if sys.platform == 'linux':
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            return f'{boot}:{fields[19]}'
        if sys.platform == 'darwin':
            # PROC_PIDTBSDINFO: two uint64 start fields follow 120 bytes.
            buf = ctypes.create_string_buffer(136)
            lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            size = lib.proc_pidinfo(pid, 3, 0, buf, len(buf))
            if size == len(buf):
                import struct
                sec, usec = struct.unpack_from('=QQ', buf.raw, 120)
                return f'{sec}:{usec}'
    except (OSError, ValueError, IndexError):
        pass
    return None


def process_terminated(identity: dict, *, group: bool = False) -> bool:
    pid = identity.get('pid')
    if (not _local_identity(identity) or type(pid) is not int or pid <= 0
            or not identity.get('start_time')):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        dead = True
    except OSError:
        return False
    else:
        current = process_start(pid)
        dead = current is not None and current != identity['start_time']
    if not dead or not group:
        return dead
    pgid = identity.get('pgid')
    if type(pgid) is not int or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass
    return False


def termination_evidence(row: dict) -> str | None:
    executor = row.get('executor') or {}
    kind = executor.get('kind')
    if kind == 'process_group' and process_terminated(executor, group=True):
        return 'process group terminated'
    if kind == 'codex_turn' and executor.get('socket') and executor.get('turn_id'):
        import codex_wire
        if codex_wire.turn_terminated(executor['socket'], executor['thread_id'], executor['turn_id']):
            return 'Codex turn terminated (thread/read)'
    if kind == 'codex_desktop' and executor.get('thread_id') and executor.get('marker'):
        import codex_wire
        if codex_wire.desktop_turn_terminated(executor['thread_id'], executor['marker']):
            return 'Codex Desktop turn terminated (marked thread/read)'
    if kind == 'no_launch':
        return 'executor did not launch'
    return None


def declaration(task: str, explicit: list[str] | None) -> list[str]:
    patterns = list(explicit or [])
    for line in task.splitlines():
        match = re.match(r'^\s*Writes:\s*(.*)$', line, re.I)
        if match and match[1].strip().lower() != 'none':
            lexer = shlex.shlex(match[1].replace('`', ''), posix=True)
            lexer.whitespace += ','
            lexer.whitespace_split = True
            lexer.commenters = ''
            try:
                patterns.extend(lexer)
            except ValueError as exc:
                raise DeskError('bad_write_set', 'Writes: has invalid quoting') from exc
    result = []
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise DeskError('bad_write_set', 'write globs must be strings')
        pattern = pattern.removeprefix('./')
        if (not pattern or pattern.startswith('/') or '..' in pattern.split('/')
                or any(part in ('', '.') for part in pattern.split('/'))
                or '\n' in pattern or '\r' in pattern):
            raise DeskError('bad_write_set', 'write globs must be nonempty repository-relative paths')
        if pattern not in result:
            result.append(pattern)
    return result


@contextmanager
def checkout_writer(cwd: str):
    global CHECKOUT_FD
    gitdir = subprocess.run(['git', '-C', cwd, 'rev-parse', '--absolute-git-dir'],
                            env=scrubbed_env(), capture_output=True, text=True, check=True).stdout.strip()
    with (Path(gitdir) / 'room-writer.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeskError('checkout_busy', 'another writer is using this checkout; use a separate worktree') from exc
        lock.seek(0)
        pending = lock.read()
        if pending and not termination_evidence({'executor': json.loads(pending)}):
            raise DeskError('checkout_busy', 'the previous executor is still using this checkout')
        previous_fd, CHECKOUT_FD = CHECKOUT_FD, lock.fileno()
        executor = None

        def bind(identity):
            nonlocal executor
            executor = identity
            lock.seek(0)
            lock.truncate()
            json.dump(identity, lock)
            lock.flush()
            os.fsync(lock.fileno())

        try:
            yield bind
        finally:
            if executor is None or termination_evidence({'executor': executor}):
                lock.seek(0)
                lock.truncate()
            CHECKOUT_FD = previous_fd


def record(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.write(json.dumps(row, separators=(',', ':')) + '\n')
