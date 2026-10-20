"""Tests and smoke probes must opt into an isolated work queue."""
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from work_queue import WorkQueue, board_projection, default_path


class QueueIsolationTests(unittest.TestCase):
    def test_test_process_without_explicit_store_refuses_before_resolving_default(self):
        with patch.dict(os.environ):
            os.environ.pop('CARR_WORK_QUEUE_DB', None)
            with patch.object(Path, 'home', side_effect=AssertionError('resolved live home')):
                with self.assertRaisesRegex(RuntimeError, 'CARR_WORK_QUEUE_DB'):
                    default_path()

    def test_explicit_temp_store_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.sqlite3'
            with patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': str(path)}):
                self.assertEqual(default_path(), path.resolve())

    def test_explicit_non_temp_store_is_rejected_before_sqlite_connects(self):
        with patch('work_queue.sqlite3.connect') as connect:
            with self.assertRaisesRegex(RuntimeError, 'isolated temporary'):
                WorkQueue(Path('/non-temporary/work-queue.sqlite3'))
            connect.assert_not_called()

    def test_projection_rejects_non_temp_path_before_checking_its_existence(self):
        with patch.object(Path, 'exists', return_value=False) as exists:
            with self.assertRaisesRegex(RuntimeError, 'isolated temporary'):
                board_projection(Path('/non-temporary/work-queue.sqlite3'))
            exists.assert_not_called()

    def test_standalone_probe_and_fixture_child_cannot_fall_back_to_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe = root / 'test_probe.py'
            probe.write_text('from work_queue import default_path\ndefault_path()\n')
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parent),
                   'CARR_HERMES_RESULTS': str(root / 'results.jsonl')}
            env.pop('CARR_WORK_QUEUE_DB', None)
            for command, environment in (
                ([sys.executable, str(probe)], env),
                ([sys.executable, '-c', probe.read_text()], {**env, 'CARR_HOOK_FIXTURE': '1'}),
            ):
                with self.subTest(command=command):
                    result = subprocess.run(command, env=environment, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('isolated temporary CARR_WORK_QUEUE_DB', result.stderr)
            self.assertFalse((root / 'work-queue.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
