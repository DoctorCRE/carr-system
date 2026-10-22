#!/usr/bin/env python3
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import job_watchdog as watchdog

ROOT = Path(__file__).resolve().parents[1]


class HookTests(unittest.TestCase):
    def test_digest_hook_and_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out/watchdog/findings.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"key": "job_failed:fixture:exit_7", "kind": "job_failed", "exit_code": 7, "reason": "fixture exit 7: last error",
                                        "next_action": "inspect log", "cleared_at": None}) + "\n")
            result = subprocess.run([sys.executable, str(ROOT / "hooks/job-watchdog-digest.py")],
                                    input="{}", text=True, capture_output=True,
                                    env=dict(os.environ, CARR_JOB_ROOT=directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("last error", payload["hookSpecificOutput"]["additionalContext"])
        hooks = json.loads((ROOT / "ops/config/hooks.json").read_text())
        self.assertTrue(any("job-watchdog-digest.py" in h["command"] for entry in hooks["UserPromptSubmit"] for h in entry["hooks"]))

    def test_banner_only_repeats_after_findings_change_in_each_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out/watchdog/findings.jsonl"
            path.parent.mkdir(parents=True)
            row = {"key": "job_failed:fixture:exit_7", "kind": "job_failed", "exit_code": 7, "reason": "fixture exit 7: last error",
                   "next_action": "inspect log", "cleared_at": None, "last_seen": 1}
            def invoke(session):
                result = subprocess.run([sys.executable, str(ROOT / "hooks/job-watchdog-digest.py")],
                    input=json.dumps({"session_id": session}), text=True, capture_output=True,
                    env=dict(os.environ, CARR_JOB_ROOT=directory))
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            path.write_text(json.dumps(row) + "\n")
            self.assertIn("last error", invoke("one"))
            self.assertEqual(invoke("one"), "")
            row["last_seen"] = 2
            path.write_text(json.dumps(row) + "\n")
            self.assertEqual(invoke("one"), "")
            self.assertIn("last error", invoke("two"))
            row["reason"] = "process exited"
            path.write_text(json.dumps(row) + "\n")
            self.assertIn("process exited", invoke("one"))
            row["cleared_at"] = 3
            path.write_text(json.dumps(row) + "\n")
            self.assertEqual(invoke("one"), "")
            row["cleared_at"] = None
            path.write_text(json.dumps(row) + "\n")
            self.assertIn("process exited", invoke("one"))

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
