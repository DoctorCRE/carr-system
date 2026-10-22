"""Process termination receipts under process-inventory races; no paid requests."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_api_process as process


class JobTreeTests(unittest.TestCase):
    def termination_after_marker_failure(self, after):
        worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        def stop_worker():
            if worker.poll() is None:
                worker.kill()
            worker.wait()
        self.addCleanup(stop_worker)
        original_start = process.write_ownership.process_start
        birth = original_start(worker.pid)
        self.assertIsNotNone(birth)
        prefix, tick = birth.rsplit(':', 1)
        candidate = 987654321
        marker_attempted = threading.Event()
        state = ['R']

        def inventory(*args, **kwargs):
            if state[0] == 'error':
                raise subprocess.TimeoutExpired('fixture process inventory', 2)
            rows = '' if worker.poll() is not None else f'{worker.pid} 1 {os.getuid()} R\n'
            if state[0] is not None:
                rows += f'{candidate} 1 {os.getuid()} {state[0]}\n'
            return subprocess.CompletedProcess(args, 0, rows, '')

        def start(pid):
            if pid == candidate:
                return None if state[0] is None else f'{prefix}:{int(tick) + 1}'
            return original_start(pid)

        def marker(pid, tag):
            self.assertEqual(pid, candidate)
            state[0] = after
            marker_attempted.set()
            raise PermissionError('fixture marker unavailable')

        with patch.object(process.subprocess, 'run', side_effect=inventory), \
                patch.object(process.write_ownership, 'process_start', side_effect=start), \
                patch.object(process, 'tagged', side_effect=marker):
            tree = process.JobTree(worker, 'fixture-job')
            self.assertTrue(marker_attempted.wait(2))
            return tree.terminate()

    def test_unrelated_process_becoming_zombie_does_not_poison_termination(self):
        self.assertTrue(self.termination_after_marker_failure('Z'))

    def test_live_unreadable_process_keeps_termination_unconfirmed(self):
        self.assertFalse(self.termination_after_marker_failure('R'))

    def test_vanished_process_does_not_poison_termination(self):
        self.assertTrue(self.termination_after_marker_failure(None))

    def test_failed_inventory_keeps_termination_unconfirmed(self):
        self.assertFalse(self.termination_after_marker_failure('error'))

    @unittest.skipUnless(sys.platform == 'linux', 'Linux process identity probe')
    def test_zombie_retains_identity_without_a_readable_job_marker(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        try:
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
            first = process.write_ownership.process_start(child.pid)
            self.assertIsNotNone(first)
            try:
                marker = process.tagged(child.pid, 'fixture-job')
            except OSError as exc:
                print(f'Linux zombie marker read: {type(exc).__name__}, errno={exc.errno}')
            else:
                print('Linux zombie marker read: no marker')
                self.assertFalse(marker)
            self.assertEqual(process.write_ownership.process_start(child.pid), first)
        finally:
            child.wait()


if __name__ == '__main__':
    unittest.main()
