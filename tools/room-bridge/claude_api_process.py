"""Identify a job across process groups and reparenting without logging argv/env."""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import write_ownership


def tagged(pid, tag):
    marker = ('CARR_CLAUDE_API_JOB=' + tag).encode() + b'\0'
    if sys.platform == 'linux':
        return marker in (Path('/proc') / str(pid) / 'environ').read_bytes()
    if sys.platform != 'darwin':
        raise OSError('process identity unavailable')
    # Only the marker verdict leaves this function. No argv/env is logged.
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t(1024 * 1024)
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), 'process marker unavailable')
    return marker in buffer.raw[:size.value]


class JobTree:
    def __init__(self, proc, tag):
        self.proc, self.tag = proc, tag
        self.starts = {proc.pid: write_ownership.process_start(proc.pid)}
        self.birth = self.starts[proc.pid]
        self.complete = self.birth is not None
        self.done = threading.Event()
        self.lock = threading.RLock()
        self.thread = threading.Thread(target=self.watch, daemon=True)
        self.thread.start()

    def snapshot(self):
        rows = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,uid=,stat='],
                              capture_output=True, text=True, check=True, timeout=2)
        table = {}
        for row in rows.stdout.splitlines():
            pid, parent, uid, state = row.split()
            if int(uid) == os.getuid() and not state.startswith('Z'):
                table[int(pid)] = int(parent)
        with self.lock:
            for pid, parent in table.items():
                if pid in self.starts:
                    continue
                start = write_ownership.process_start(pid)
                if start is None:
                    continue
                if parent in self.starts and write_ownership.process_start(parent) == self.starts[parent]:
                    self.starts[pid] = start
                    continue
                started = tuple(int(x) for x in start.split(':') if x.isdecimal())
                born = tuple(int(x) for x in (self.birth or '').split(':') if x.isdecimal())
                if born and started < born:
                    continue
                try:
                    if tagged(pid, self.tag):
                        self.starts[pid] = start
                except OSError:
                    if write_ownership.process_start(pid) == start:
                        self.complete = False
            return {pid for pid in self.starts if pid in table
                    and write_ownership.process_start(pid) == self.starts[pid]}

    def watch(self):
        while not self.done.is_set():
            try:
                self.snapshot()
            except (OSError, ValueError, subprocess.SubprocessError):
                self.complete = False
            self.done.wait(0.02)

    def terminate(self):
        self.done.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            self.complete = False
        try:
            stopped = set()
            for _ in range(100):
                alive = self.snapshot()
                pending = alive - stopped
                if not pending:
                    break
                for pid in pending:
                    if pid == os.getpid():
                        raise RuntimeError('caller is not a job process')
                    try:
                        os.kill(pid, signal.SIGSTOP)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        self.complete = False
                    stopped.add(pid)
            else:
                self.complete = False
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                alive = self.snapshot()
                for pid in alive:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        self.complete = False
                self.proc.poll()
                if not alive:
                    self.proc.wait(timeout=1)
                    return self.complete
                time.sleep(0.02)
            return False
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
