"""Tests and smoke probes must opt into an isolated work queue."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from work_queue import default_path


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


if __name__ == '__main__':
    unittest.main()
