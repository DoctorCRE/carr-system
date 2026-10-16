#!/usr/bin/env python3
"""The nightly launcher bounds jobs and continues after an independent failure."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NightlyTests(unittest.TestCase):
    def test_a_failed_job_does_not_prevent_the_other_jobs_from_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("bin/nightly.sh", "bin/run-lock.sh", "bin/step-timeout.zsh", "bin/with-timeout.py"):
                target = root / name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(ROOT / name, target)
            (root / "out").mkdir()
            (root / ".venv/bin").mkdir(parents=True)
            (root / ".venv/bin/python").symlink_to(sys.executable)
            (root / "bin/routine-credential-env.sh").write_text(
                'carr_clear_routine_db_env() { return 0; }\ncarr_load_routine_db_env() { return 0; }\ncarr_routine_exec() { "$@"; }\n')
            (root / "bin/run-scheduled.sh").write_text(
                '#!/bin/zsh\nprint -r -- "$*" >> "$PWD/out/attempts"\nexit 7\n')
            result = subprocess.run(["/bin/zsh", str(root / "bin/nightly.sh")], cwd=root,
                                    env={"HOME": str(root), "PATH": os.environ["PATH"],
                                         "CARR_LOCK_DIR": str(root / "locks")},
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = (root / "out/attempts").read_text().splitlines()
            self.assertTrue(any("nightly.encrypted-backup" in row for row in rows))
            self.assertTrue(any("nightly.credential-health" in row for row in rows))
            self.assertTrue(any("nightly.cadence-engine" in row for row in rows))
            self.assertTrue(all("bin/with-timeout.py" in row for row in rows))
            self.assertFalse((root / "out/nightly-runs").exists())


    def test_backup_failure_pings_only_the_backup_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".config/carr").mkdir(parents=True)
            (root / ".config/carr/healthchecks.env").write_text(
                "HC_PING_BACKUP=https://example.test/backup\n"
                "HC_PING_EXPORTS=https://example.test/exports\n"
                "HC_PING_MCP=https://example.test/worker\n")
            curl = root / "curl"
            curl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CARR_TEST_CALLS"\n')
            curl.chmod(0o755)
            calls = root / "calls"
            result = subprocess.run(["/bin/zsh", str(ROOT / "bin/hc-ping.sh")],
                                    env={"HOME": str(root), "PATH": f"{root}:/usr/bin:/bin",
                                         "HC_BACKUP_RC": "7", "CARR_TEST_CALLS": str(calls)},
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(calls.read_text().splitlines()), 1)
            self.assertIn("https://example.test/backup/fail", calls.read_text())


if __name__ == "__main__":
    unittest.main()
