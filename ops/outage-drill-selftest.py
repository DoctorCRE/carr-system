#!/usr/bin/env python3
"""A recording failure must not change a scheduled product job's exit status."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RecorderOutageTests(unittest.TestCase):
    def test_unwritable_spool_reports_recording_failure_and_preserves_child_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("bin/run-scheduled.sh", "tools/ops-spool.py"):
                target = root / name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(ROOT / name, target)
            (root / ".venv/bin").mkdir(parents=True)
            (root / ".venv/bin/python").symlink_to(sys.executable)
            env = {"HOME": directory, "PATH": os.environ["PATH"],
                   "CARR_RUN_SPOOL_DB": os.path.join(os.devnull, "spool.sqlite3")}
            for code in (0, 7):
                with self.subTest(child_exit=code):
                    result = subprocess.run(
                        ["/bin/zsh", str(root / "bin/run-scheduled.sh"), "test-service",
                         "outage.test", "/bin/sh", "-c", f"exit {code}"],
                        cwd=root, env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, code)
                    log = (root / "out/run-scheduled.log").read_text().splitlines()[-1]
                    self.assertIn(f"child_exit={code}", log)
                    self.assertRegex(log, r"recorder_exit=[1-9][0-9]* ")


if __name__ == "__main__":
    unittest.main()
