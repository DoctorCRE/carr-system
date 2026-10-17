"""Capture bounded review output and reap newly started same-user orphan processes."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import struct
import subprocess
import sys
import time


@dataclass(frozen=True)
class _Process:
    pid: int
    ppid: int
    uid: int
    start: int

    @property
    def identity(self):
        return self.pid, self.uid, self.start


_LIBPROC = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True) if sys.platform == 'darwin' else None
if _LIBPROC is not None:
    _LIBPROC.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                    ctypes.c_void_p, ctypes.c_int]
    _LIBPROC.proc_pidinfo.restype = ctypes.c_int


def _process(pid):
    """Kernel start identity, independent of argv and inherited environment."""
    try:
        if sys.platform == 'darwin':
            # PROC_PIDTBSDINFO's fixed ABI: uid at 20, start seconds/useconds at 120.
            buf = ctypes.create_string_buffer(136)
            if _LIBPROC.proc_pidinfo(pid, 3, 0, buf, len(buf)) != len(buf):
                return None
            state, current_pid, ppid, uid = struct.unpack_from('=I4xIII', buf.raw, 4)
            if state == 5 or current_pid != pid:  # SZOMB
                return None
            sec, usec = struct.unpack_from('=QQ', buf.raw, 120)
            return _Process(pid, ppid, uid, sec * 1_000_000 + usec)
        if sys.platform == 'linux':
            root = Path(f'/proc/{pid}')
            fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                return None
            uid_line = next(line for line in (root / 'status').read_text().splitlines()
                            if line.startswith('Uid:'))
            return _Process(pid, int(fields[1]), int(uid_line.split()[2]), int(fields[19]))
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    raise ValueError('process identity unavailable; evidence execution refused')


def _cwd(pid):
    try:
        if sys.platform == 'linux':
            return Path(os.readlink(f'/proc/{pid}/cwd')).resolve()
        # PROC_PIDVNODEPATHINFO: two 1176-byte vnode_info_path records; each path follows 152 bytes.
        buf = ctypes.create_string_buffer(2352)
        if _LIBPROC.proc_pidinfo(pid, 9, 0, buf, len(buf)) == len(buf):
            return Path(os.fsdecode(buf.raw[152:1176].split(b'\0', 1)[0])).resolve()
    except OSError:
        pass
    return None


def _start_boundary():
    if sys.platform == 'linux':
        return int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf('SC_CLK_TCK'))
    return time.time_ns() // 1000


def _inventory():
    """Metadata only: never inspect or capture process commands or environments."""
    try:
        result = subprocess.run(['ps', '-A', '-o', 'pid=,ppid=,uid='],
                                capture_output=True, text=True, timeout=.25, check=True)
        found = {}
        for line in result.stdout.splitlines():
            pid, _ppid, uid = map(int, line.split())
            if uid == os.geteuid():
                process = _process(pid)
                if process is not None:
                    found[pid] = process
        if os.getpid() not in found:
            raise ValueError('current process absent from inventory')
        return found
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError('descendant inventory unavailable; evidence execution refused') from exc


class _Tracker:
    def __init__(self, tree, baseline, started):
        self.tree = tree
        self.baseline = {process.identity for process in baseline.values()}
        self.started = started
        self.owned = {}
        self.observed = {}

    def observe(self, rows):
        live_owned = {pid for pid, process in self.owned.items()
                      if pid in rows and rows[pid].identity == process.identity}
        # Recover even an immediate orphan whose attached ancestry was never sampled.
        for pid, process in rows.items():
            if (pid == os.getpid() or process.uid != os.geteuid() or
                    process.identity in self.baseline or process.start < self.started):
                continue
            previous = self.observed.setdefault(process.identity, process)
            if pid not in live_owned:
                cwd = _cwd(pid)
                orphan = (process.ppid == 1 or process.ppid not in rows or
                          previous.ppid != process.ppid and previous.ppid in self.owned)
                workspace = cwd is not None and any(cwd.is_relative_to(root) for root in
                                                    (self.tree, Path(str(self.tree) + '.scratch')))
                if orphan or workspace:
                    self.owned[pid] = process
                    live_owned.add(pid)
        # Keep an observed identity after it changes session, parent, or cwd.
        while True:
            added = {pid for pid, process in rows.items()
                     if pid not in live_owned and process.ppid in live_owned and
                     process.uid == os.geteuid() and process.start >= self.started and
                     process.identity not in self.baseline}
            if not added:
                break
            for pid in added:
                self.owned[pid] = rows[pid]
            live_owned.update(added)

    def signal(self, process, sig):
        current = _process(process.pid)
        if current is not None and current.identity == process.identity:
            try:
                os.kill(process.pid, sig)
            except ProcessLookupError:
                pass

    def cleanup(self, child):
        error = None
        stopped = set()
        try:
            # Stop before killing, and rescan to catch forks that raced the first snapshot.
            for _ in range(8):
                try:
                    self.observe(_inventory())
                except ValueError as exc:
                    error = exc
                new = [process for process in self.owned.values() if process.identity not in stopped]
                for process in new:
                    self.signal(process, signal.SIGSTOP)
                    stopped.add(process.identity)
                if error is not None or not new:
                    break
        finally:
            for process in self.owned.values():
                try:
                    self.signal(process, signal.SIGKILL)
                except OSError as exc:
                    error = error or exc
            # Popen owns an unreaped child PID; it cannot be reused while still running.
            try:
                child.kill()
            except OSError as exc:
                error = error or exc
            child.wait(timeout=1)
        if error is not None:
            raise error


def run_bounded(argv, tree, env, *, timeout, limit):
    """Return (exit/timeout/output_limit, text); tree must be this job's exclusive scratch workspace."""
    if timeout <= 0 or limit <= 0:
        raise ValueError('timeout and output limit must be positive')
    tree = Path(tree).resolve()
    baseline = _inventory()
    if _process(os.getpid()) is None or _cwd(os.getpid()) is None:
        raise ValueError('process identity or cwd unavailable; evidence execution refused')
    tracker = _Tracker(tree, baseline, _start_boundary())
    child = subprocess.Popen(argv, cwd=tree, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    assert child.stdout is not None
    root = _process(child.pid)
    if root is not None:
        tracker.owned[child.pid] = root
    data = bytearray()
    status = None
    end = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            next_inventory = 0
            while True:
                now = time.monotonic()
                if now >= end:
                    status = 'timeout'
                    break
                if now >= next_inventory:
                    tracker.observe(_inventory())
                    next_inventory = time.monotonic() + .01
                events = selector.select(max(0, min(next_inventory, end) - time.monotonic()))
                if events:
                    block = os.read(child.stdout.fileno(), min(65536, limit - len(data) + 1))
                    if not block:
                        break
                    data.extend(block[:limit - len(data)])
                    if len(data) >= limit:
                        status = 'output_limit'
                        break
    finally:
        try:
            tracker.cleanup(child)
        finally:
            child.stdout.close()
    return status or str(child.returncode), data.decode('utf-8', errors='replace')
