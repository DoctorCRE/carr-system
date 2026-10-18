#!/usr/bin/env python3
"""Exercise the scheduled wrapper and durable spool through the real CLI."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ScheduledRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("bin/run-scheduled.sh", "tools/ops-spool.py"):
            target = self.root / name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").symlink_to(sys.executable)
        self.spool = self.root / "out/run-spool.sqlite3"
        self.env = {"HOME": str(self.root), "PATH": os.environ["PATH"],
                    "CARR_RUN_SPOOL_DB": str(self.spool)}

    def invoke(self, code):
        return subprocess.run(["/bin/zsh", str(self.root / "bin/run-scheduled.sh"),
                               "test-service", "launchd.run", "/bin/sh", "-c",
                               f"printf child-out; printf child-err >&2; exit {code}"],
                              cwd=self.root, env=self.env, text=True, capture_output=True, timeout=15)

    def rows(self):
        with sqlite3.connect(self.spool) as connection:
            return connection.execute("select service,run_key,state,argv from spool").fetchall()

    def test_success_records_one_result_without_a_second_receipt(self):
        result = self.invoke(0)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "child-out", "child-err"))
        rows = self.rows()
        self.assertEqual([r[:3] for r in rows], [("test-service", "launchd.run", "succeeded")])
        self.assertNotIn("--evidence-ref", json.loads(rows[0][3]))
        self.assertFalse((self.root / "out/run-scheduled-receipts").exists())

    def test_failed_delivery_queues_the_failure_and_preserves_the_child_exit(self):
        result = self.invoke(7)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "child-out", "child-err"))
        rows = self.rows()
        self.assertEqual([r[:3] for r in rows], [("test-service", "launchd.run", "failed")])
        args = json.loads(rows[0][3])
        self.assertEqual(args[args.index("--failure-class") + 1], "exit_7")

    def test_configuration_skip_and_timeout_keep_their_meaning(self):
        for code, state in ((78, "skipped"), (124, "timed_out"), (143, "cancelled")):
            with self.subTest(code=code):
                result = self.invoke(code)
                self.assertEqual(result.returncode, code)
                self.assertEqual(self.rows()[-1][2], state)


if __name__ == "__main__":
    unittest.main()
