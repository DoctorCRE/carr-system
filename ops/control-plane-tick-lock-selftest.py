#!/usr/bin/env python3
"""Exercise the tick wrapper with real processes and synthetic local files."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TickLockTests(unittest.TestCase):
    def test_abandoned_directory_does_not_skip_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "tick.lock"
            lock.mkdir()
            result = subprocess.run(["/bin/zsh", str(ROOT / "bin/control-plane-tick.sh")],
                env={"PATH": "/usr/bin:/bin", "HOME": directory,
                     "CARR_CONTROL_PLANE_LOCK_DIR": str(lock)},
                text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 78, result.stderr)
            self.assertIn("CARR_DB_JOBS_URL is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
