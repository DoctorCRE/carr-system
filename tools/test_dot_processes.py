#!/usr/bin/env python3
"""Bounded review processes cannot leave detached workspace children behind."""
from __future__ import annotations

import fcntl
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import dot_processes as processes


class BoundedProcessTests(unittest.TestCase):
    def escaped_child(self, *, parent_delay, hide_attached=False, outside_cwd=False, initial_cwd=None):
        with tempfile.TemporaryDirectory(prefix='dot-process-test-') as tmp, \
                tempfile.TemporaryFile() as stdout, ExitStack() as stack:
            tree = Path(tmp).resolve()
            uid = os.geteuid()
            caller = processes._Process(os.getpid(), os.getppid(), uid, 1)
            root = processes._Process(700001, caller.pid, uid, 101)
            attached = processes._Process(700002, root.pid, uid, 102)
            orphan = processes._Process(attached.pid, 1, uid, attached.start)
            child = Mock(pid=root.pid, stdout=stdout, returncode=0)
            now = 0
            spawned = False
            observed = []

            def spawn(*args, **kwargs):
                nonlocal spawned
                self.assertEqual(kwargs['env'], {})
                self.assertTrue(kwargs['start_new_session'])
                spawned = True
                return child

            def inventory():
                rows = {caller.pid: caller}
                if spawned:
                    if now < parent_delay:
                        rows[root.pid] = root
                        if not hide_attached:
                            rows[attached.pid] = attached
                    else:
                        rows[orphan.pid] = orphan
                    observed.append(rows)
                return rows

            def identity(pid):
                return {caller.pid: caller, root.pid: root if now < parent_delay else None,
                        attached.pid: attached if now < parent_delay else orphan}.get(pid)

            def cwd(pid):
                if pid == attached.pid:
                    return Path('/outside') if outside_cwd else Path(initial_cwd or tree)
                return tree

            def select(timeout):
                nonlocal now
                now += timeout
                return []

            selector = Mock()
            selector.__enter__ = Mock(return_value=selector)
            selector.__exit__ = Mock(return_value=False)
            selector.select.side_effect = select
            stack.enter_context(patch.object(processes.time, 'monotonic', side_effect=lambda: now))
            stack.enter_context(patch.object(processes, '_start_boundary', return_value=100))
            stack.enter_context(patch.object(processes, '_inventory', side_effect=inventory))
            stack.enter_context(patch.object(processes, '_process', side_effect=identity))
            stack.enter_context(patch.object(processes, '_cwd', side_effect=cwd))
            stack.enter_context(patch.object(subprocess, 'Popen', side_effect=spawn))
            stack.enter_context(patch.object(processes.selectors, 'DefaultSelector', return_value=selector))
            kill = stack.enter_context(patch.object(os, 'kill'))
            status, output = processes.run_bounded(['fixture'], tree, {}, timeout=.3, limit=1024)
            self.assertEqual((status, output), ('timeout', ''))
            self.assertTrue(any(rows.get(orphan.pid) == orphan for rows in observed))
            if hide_attached:
                self.assertFalse(any(rows.get(attached.pid) == attached for rows in observed))
            elif parent_delay:
                self.assertTrue(any(rows.get(attached.pid) == attached for rows in observed))
            self.assertEqual(kill.call_args_list,
                             [call(orphan.pid, signal.SIGSTOP), call(orphan.pid, signal.SIGKILL)])
            child.kill.assert_called_once_with()
            child.wait.assert_called_once_with(timeout=1)

    def test_reparented_child_with_empty_environment_is_killed(self):
        self.escaped_child(parent_delay=.08)

    def test_immediate_orphan_found_without_observed_parent_link(self):
        self.escaped_child(parent_delay=0, hide_attached=True)

    def test_observed_child_is_retained_after_reparenting_and_leaving_workspace(self):
        self.escaped_child(parent_delay=.12, outside_cwd=True)

    def test_immediate_orphan_initially_at_root_with_fixture_inventory(self):
        self.escaped_child(parent_delay=0, hide_attached=True, initial_cwd='/')

    def test_immediate_orphan_initially_at_scratch_with_fixture_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / 'tree.scratch'
            scratch.mkdir()
            self.escaped_child(parent_delay=0, hide_attached=True, initial_cwd=str(scratch))

    def test_native_immediate_orphan_initially_at_root_is_killed(self):
        self.native_immediate_orphan('root')

    def test_native_immediate_orphan_initially_at_scratch_is_killed(self):
        self.native_immediate_orphan('scratch')

    def native_immediate_orphan(self, kind):
        with self.subTest(cwd=kind), tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / 'tree'
            tree.mkdir()
            scratch = Path(str(tree) + '.scratch')
            scratch.mkdir()
            marker, pidfile = Path(tmp) / 'escaped', Path(tmp) / 'pid'
            cwd = '/' if kind == 'root' else str(scratch)
            descendant = ('import os,time;from pathlib import Path;'
                          f'Path({str(pidfile)!r}).write_text(str(os.getpid()));'
                          f'time.sleep(.8);Path({str(marker)!r}).write_text("escaped")')
            parent = ('import subprocess,sys;'
                      f'subprocess.Popen([sys.executable,"-c",{descendant!r}],'
                      f'cwd={cwd!r},env={{}},start_new_session=True,'
                      'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)')
            try:
                processes.run_bounded([sys.executable, '-c', parent], tree, {}, timeout=.4, limit=1024)
                time.sleep(1)
                self.assertFalse(marker.exists(), f'orphan escaped with cwd={cwd}')
            finally:
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text()), signal.SIGKILL)
                    except (ProcessLookupError, ValueError):  # killed mid-write
                        pass

    def own_inventory(self):
        process = processes._process(os.getpid())
        return {process.pid: process}

    def test_normal_exit_preserves_status_and_output(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(processes, '_inventory', side_effect=self.own_inventory):
            result = processes.run_bounded([sys.executable, '-c', 'print("done")'],
                                          Path(tmp), {}, timeout=1, limit=1024)
        self.assertEqual(result, ('0', 'done\n'))

    def test_output_limit_stops_capture_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(processes, '_inventory', side_effect=self.own_inventory):
            result = processes.run_bounded(
                [sys.executable, '-c', 'import os\nwhile True: os.write(1,b"x"*65536)'],
                Path(tmp), {}, timeout=1, limit=32)
        self.assertEqual(result, ('output_limit', 'x' * 32))

    def test_unavailable_inventory_refuses_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(subprocess, 'run', side_effect=PermissionError('ps denied')), \
                patch.object(subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'descendant inventory unavailable; evidence execution refused'):
                processes.run_bounded(['untrusted'], Path(tmp), {}, timeout=1, limit=1024)
            spawn.assert_not_called()

    def test_workspace_sweep_and_signals_exclude_unrelated_and_reused_processes(self):
        uid = os.geteuid()
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp).resolve()
            baseline = processes._Process(700001, 1, uid, 120)
            root = processes._Process(700002, 1, uid, 150)
            old = processes._Process(700003, 1, uid, 90)
            outside = processes._Process(700004, os.getpid(), uid, 170)
            inside = processes._Process(700005, 1, uid, 170)
            descendant = processes._Process(700006, inside.pid, uid, 180)
            other_uid = processes._Process(700007, 1, uid + 1, 170)
            reused = processes._Process(700008, 1, uid, 170)
            sibling = processes._Process(700009, os.getpid(), uid, 170)
            rows = {row.pid: row for row in
                    (baseline, root, old, outside, inside, descendant, other_uid, reused, sibling)}
            rows[os.getpid()] = processes._process(os.getpid())
            tracker = processes._Tracker(tree, {baseline.pid: baseline}, 100)
            tracker.owned[root.pid] = root
            cwd = {pid: tree for pid in rows}
            cwd[outside.pid] = tree.parent
            cwd[descendant.pid] = tree.parent
            cwd[sibling.pid] = Path(str(tree) + '-sibling')
            with patch.object(processes, '_cwd', side_effect=cwd.get):
                tracker.observe(rows)
            current = {**rows, reused.pid: processes._Process(reused.pid, 1, uid, reused.start + 1)}
            with patch.object(processes, '_process', side_effect=current.get), \
                    patch.object(os, 'kill') as kill:
                for process in tracker.owned.values():
                    tracker.signal(process, signal.SIGKILL)
            self.assertEqual(sorted(call.args for call in kill.call_args_list),
                             [(root.pid, signal.SIGKILL), (inside.pid, signal.SIGKILL),
                              (descendant.pid, signal.SIGKILL)])

    def test_one_denied_signal_still_attempts_remaining_and_parent_cleanup(self):
        root = processes._Process(700001, 1, os.geteuid(), 120)
        descendant = processes._Process(700002, root.pid, root.uid, 130)
        tracker = processes._Tracker(Path('/unused'), {}, 100)
        tracker.owned = {row.pid: row for row in (root, descendant)}
        child = Mock()
        attempted = []
        def signal_process(process, sig):
            attempted.append((process.pid, sig))
            if process.pid == root.pid and sig == signal.SIGKILL:
                raise PermissionError('signal denied')
        with patch.object(processes, '_inventory', return_value={}), \
                patch.object(tracker, 'signal', side_effect=signal_process):
            with self.assertRaisesRegex(OSError, 'signal denied'):
                tracker.cleanup(child)
        self.assertEqual(attempted, [(root.pid, signal.SIGSTOP), (descendant.pid, signal.SIGSTOP),
                                     (root.pid, signal.SIGKILL), (descendant.pid, signal.SIGKILL)])
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=1)


if __name__ == '__main__':
    # run_bounded adopts and kills every new same-user orphan on the host
    # (lib/dot_processes.py _Tracker.observe), so test_dot_processes.py and
    # test_dot_verdicts.py kill each other's escaped fixture children when the
    # parallel gates pool runs them side by side. Hold one host lock instead.
    with open(Path('/tmp') / f'carr-dot-run-bounded-{os.getuid()}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        unittest.main()
