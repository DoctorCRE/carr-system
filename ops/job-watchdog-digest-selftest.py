#!/usr/bin/env python3
# Test job watchdog digest hooks.
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import job_watchdog as watchdog

ROOT = Path(__file__).resolve().parents[1]


class HookTests(unittest.TestCase):
    def test_findings_reach_inbox_once_until_content_changes_or_reminder_is_due(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = {"jobs": [{"label": "fixture", "exit_code": 7, "log_line": "first error"}],
                     "disk_free_percent": 50}
            inbox = root / "out/watchdog/orchestrator-inbox.jsonl"
            def scan(now):
                with patch.object(watchdog, "collect", return_value=facts), patch.object(watchdog.time, "time", return_value=now):
                    self.assertEqual(watchdog.scan(root, config), 0)
                return [json.loads(line) for line in inbox.read_text().splitlines()] if inbox.exists() else []
            first = scan(1000)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["owner"], "orchestrator")
            self.assertIn("first error", first[0]["finding"]["reason"])
            self.assertEqual(scan(1001), first)
            facts["jobs"][0]["log_line"] = "changed error"
            changed = scan(1002)
            self.assertEqual(len(changed), 2)
            self.assertIn("changed error", changed[-1]["finding"]["reason"])
            age = 86400
            self.assertEqual(len(scan(1002 + age - 1)), 2)
            self.assertEqual(len(scan(1002 + age)), 3)
            facts["jobs"][0]["exit_code"] = 0
            self.assertEqual(len(scan(1003 + age)), 3)
            facts["jobs"][0]["exit_code"] = 7
            self.assertEqual(len(scan(1004 + age)), 4)

    def test_partner_prompt_has_no_watchdog_hook(self):
        hooks = json.loads((ROOT / "ops/config/hooks.json").read_text())
        self.assertFalse(any("job-watchdog-digest.py" in h["command"] for entry in hooks["UserPromptSubmit"] for h in entry["hooks"]))
        self.assertFalse((ROOT / "hooks/job-watchdog-digest.py").exists())

    def test_digest_is_five_lines_and_ignores_retired_findings(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / config["paths"]["findings"]
            path.parent.mkdir(parents=True)
            rows = [{"key": f"branch:{i}", "kind": "branch_idle", "reason": "old branch", "cleared_at": None} for i in range(20)]
            rows.extend({"key": f"job_failed:fixture-{i}:exit_7", "kind": "job_failed", "exit_code": 7,
                         "reason": f"fixture-{i} exit 7: first line\nsecond line", "cleared_at": None} for i in range(10))
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            context = watchdog.digest(root, config)
            self.assertEqual(len(context.splitlines()), 5)
            self.assertIn("10 open finding(s)", context)
            self.assertIn("7 more", context)
            self.assertNotIn("branch", context)

    def test_launchd_cadence_is_config_projection(self):
        config = json.loads((ROOT / "ops/config/job-watchdog.json").read_text())
        plist = plistlib.loads((ROOT / "ops/launchd/com.carr.job-watchdog.plist").read_bytes())
        result = subprocess.run([sys.executable, "-m", "lib.launchd_calendar", "audit",
                                 "ops/launchd/com.carr.job-watchdog.plist"],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("StartInterval", plist)
        # Two-minute cadence covers the entire hour exactly once.
        self.assertEqual(plist["StartCalendarInterval"],
                         [{"Minute": minute} for minute in range(0, 60, 2)])
        self.assertEqual(plist["ProgramArguments"][-1], "scan")
        self.assertIn("{{REPO}}/bin/run-scheduled.sh", plist["ProgramArguments"])
        services = json.loads((ROOT / "ops/config/services.json").read_text())
        declaration = next(s for s in services["services"] if s["key"] == "job-watchdog")["environments"][0]
        self.assertEqual(declaration["expected_cadence_seconds"], config["thresholds"]["scan_seconds"])
        self.assertEqual(declaration["cadence_grace_seconds"], config["thresholds"]["scan_grace_seconds"])


if __name__ == "__main__":
    unittest.main()
