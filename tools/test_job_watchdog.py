#!/usr/bin/env python3
"""Read-only local watchdog behavior through its collection and scan interfaces."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import job_watchdog as watchdog


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        self.source = self.root / "source"
        self.plists = self.source / "ops/launchd"
        self.plists.mkdir(parents=True)
        self.log = self.root / "job.log"
        self.log.write_text("older detail\nlast failure\n")
        (self.plists / "com.carr.fixture.plist").write_bytes(plistlib.dumps({
            "Label": "com.carr.fixture", "StartCalendarInterval": {"Minute": 0},
            "StandardErrorPath": str(self.log)}))

    def test_only_failed_scheduled_jobs_and_low_disk_are_collected(self):
        response = subprocess.CompletedProcess([], 0,
            "PID\tStatus\tLabel\n-\t7\tcom.carr.fixture\n-\t1\tcom.apple.other\n", "")
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog.subprocess, "run", return_value=response) as calls, \
             patch.object(watchdog.shutil, "disk_usage", return_value=(100, 91, 9)):
            found = watchdog.detect(watchdog.collect(self.root, self.config))
        self.assertEqual({f["kind"] for f in found}, {"job_failed", "disk_low"})
        failure = next(f for f in found if f["kind"] == "job_failed")
        self.assertEqual(failure["subject"], "com.carr.fixture")
        self.assertEqual(failure["exit_code"], 7)
        self.assertEqual(failure["log_line"], "last failure")
        self.assertEqual([call.args[0] for call in calls.call_args_list], [["launchctl", "list"]])

    def test_missing_job_cannot_clear_its_previous_failed_run(self):
        failed = {"jobs": [{"label": "com.carr.fixture", "exit_code": 7, "log_line": "failed"}], "disk_free_percent": 50}
        absent = {"jobs": [], "disk_free_percent": 50}
        with patch.object(watchdog, "collect", side_effect=[failed, absent]):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertIn("com.carr.fixture exit 7", watchdog.digest(self.root, self.config))

    def test_repeated_failure_updates_one_cause_and_success_clears_it(self):
        def facts(code, line):
            return {"jobs": [{"label": "com.carr.fixture", "exit_code": code, "log_line": line}], "disk_free_percent": 50}
        with patch.object(watchdog, "collect", side_effect=[facts(7, "first"), facts(7, "second"), facts(0, "ok")]):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            context = watchdog.digest(self.root, self.config)
            self.assertIn("1 open finding", context)
            self.assertIn("second", context)
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertEqual(watchdog.digest(self.root, self.config), "")
        rows = watchdog.read_latest(self.root / self.config["paths"]["findings"])
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(next(iter(rows.values()))["cleared_at"])

    def test_failed_collection_keeps_findings_without_collection_error_finding(self):
        failed = {"jobs": [{"label": "com.carr.fixture", "exit_code": 7, "log_line": "failed"}], "disk_free_percent": 50}
        with patch.object(watchdog, "collect", side_effect=[failed, OSError("launchctl unavailable")]):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            self.assertEqual(watchdog.scan(self.root, self.config), 1)
        self.assertIn("1 open finding", watchdog.digest(self.root, self.config))
        rows = watchdog.read_latest(self.root / self.config["paths"]["findings"])
        self.assertEqual({f["kind"] for f in rows.values()}, {"job_failed"})

    def test_disk_clears_at_ten_percent(self):
        for free, count in ((9.99, 1), (10, 0), (50, 0)):
            with self.subTest(free=free):
                self.assertEqual(len(watchdog.detect({"jobs": [], "disk_free_percent": free})), count)

    def test_missing_loaded_job_is_not_reported_as_success(self):
        reply = subprocess.CompletedProcess([], 0, "PID\tStatus\tLabel\n", "")
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog.subprocess, "run", return_value=reply), \
             patch.object(watchdog.shutil, "disk_usage", return_value=(100, 50, 50)):
            self.assertEqual(watchdog.collect(self.root, self.config)["jobs"], [])

    def test_keepalive_servers_are_not_scheduled_jobs(self):
        (self.plists / "server.plist").write_bytes(plistlib.dumps({"Label": "com.carr.server", "KeepAlive": True}))
        reply = subprocess.CompletedProcess([], 0, "PID\tStatus\tLabel\n-\t1\tcom.carr.server\n", "")
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog.subprocess, "run", return_value=reply), \
             patch.object(watchdog.shutil, "disk_usage", return_value=(100, 50, 50)):
            self.assertEqual(watchdog.collect(self.root, self.config)["jobs"], [])

    def test_next_completed_run_replaces_the_previous_exit_cause(self):
        observations = [{"jobs": [{"label": "com.carr.fixture", "exit_code": code, "log_line": "failed"}], "disk_free_percent": 50} for code in (7, 8)]
        with patch.object(watchdog, "collect", side_effect=observations):
            watchdog.scan(self.root, self.config)
            watchdog.scan(self.root, self.config)
        context = watchdog.digest(self.root, self.config)
        self.assertIn("1 open finding", context)
        self.assertIn("exit 8", context)
        self.assertNotIn("exit 7", context)

    def test_retired_findings_leave_the_prompt_and_history_survives(self):
        path = self.root / self.config["paths"]["findings"]
        path.parent.mkdir(parents=True)
        legacy = json.dumps({"key": "branch_idle:old", "kind": "branch_idle", "reason": "old warning", "cleared_at": None}) + "\n"
        path.write_text(legacy)
        with patch.object(watchdog, "collect", return_value={"jobs": [], "disk_free_percent": 50}):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertTrue(path.read_text().startswith(legacy))
        self.assertEqual(watchdog.digest(self.root, self.config), "")


if __name__ == "__main__":
    unittest.main()
