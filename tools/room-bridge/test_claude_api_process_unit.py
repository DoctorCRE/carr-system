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
        self.addCleanup(lambda: worker.poll() is None and worker.kill())
        self.addCleanup(worker.wait)
        original_start = process.write_ownership.process_start
        birth = original_start(worker.pid)
        self.assertIsNotNone(birth)
        prefix, tick = birth.rsplit(':', 1)
        candidate = 987654321
        marker_attempted = threading.Event()
        state = ['R']

        def inventory(*args, **kwargs):
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


if __name__ == '__main__':
    unittest.main()
