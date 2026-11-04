#!/usr/bin/env python3
"""Exercise the explicit receipt reader without accessing installed jobs."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from git_env import fixture_env

PROGRAM = Path(__file__).with_name('studio-proof-observe.py')


class StudioObservationTest(unittest.TestCase):
    def test_completed_receipt_and_blocked_prerequisites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt.txt'
            receipt = {'state': 'succeeded', 'runtime_sha': 'a' * 40,
                       'counts': {'run': {'processed': 2}}}
            def observe(value):
                path.write_text('outlook-intake-receipt ' + json.dumps(value))
                return subprocess.run([sys.executable, str(PROGRAM), '--receipt-file', str(path),
                    '--service', 'outlook-intake', '--runtime-sha', 'a' * 40,
                    '--count', 'run.processed', '--minimum', '1'],
                    capture_output=True, text=True, timeout=10, env=fixture_env())
            result = observe(receipt)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('OBSERVED', result.stdout)
            for changes in [{'state': 'failed'}, {'state': 'skipped'},
                            {'runtime_sha': 'aaaaaaa'}, {'runtime_sha': 'b' * 40},
                            {'counts': {}}, {'counts': {'run': {'processed': 0}}},
                            {'counts': {'run': {'processed': True}}}]:
                with self.subTest(changes=changes):
                    result = observe({**receipt, **changes})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('BLOCKED', result.stdout)
            path.write_text('refresh succeeded')
            command = [sys.executable, str(PROGRAM), '--receipt-file', str(path),
                       '--service', 'outlook-intake', '--runtime-sha', 'a' * 40,
                       '--count', 'run.processed', '--minimum', '1']
            for text in ['refresh succeeded', 'x' * 1048577]:
                path.write_text(text)
                self.assertNotEqual(subprocess.run(command, capture_output=True, env=fixture_env()).returncode, 0)
            path.unlink()
            self.assertNotEqual(subprocess.run(command, capture_output=True, env=fixture_env()).returncode, 0)


if __name__ == '__main__':
    unittest.main()
