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
            path.write_text(json.dumps({"key": "hang", "kind": "job_hang", "reason": "stdin stalled",
                                        "next_action": "inspect log", "cleared_at": None}) + "\n")
            result = subprocess.run([sys.executable, str(ROOT / "hooks/job-watchdog-digest.py")],
                                    input="{}", text=True, capture_output=True,
                                    env=dict(os.environ, CARR_JOB_ROOT=directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("stdin stalled", payload["hookSpecificOutput"]["additionalContext"])
        hooks = json.loads((ROOT / "ops/config/hooks.json").read_text())
        self.assertTrue(any("job-watchdog-digest.py" in h["command"] for entry in hooks["UserPromptSubmit"] for h in entry["hooks"]))

    def test_banner_only_repeats_after_findings_change_in_each_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out/watchdog/findings.jsonl"
            path.parent.mkdir(parents=True)
            row = {"key": "hang", "kind": "job_hang", "reason": "stdin stalled",
                   "next_action": "inspect log", "cleared_at": None, "last_seen": 1}
            def invoke(session):
                result = subprocess.run([sys.executable, str(ROOT / "hooks/job-watchdog-digest.py")],
                    input=json.dumps({"session_id": session}), text=True, capture_output=True,
                    env=dict(os.environ, CARR_JOB_ROOT=directory))
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            path.write_text(json.dumps(row) + "\n")
            self.assertIn("stdin stalled", invoke("one"))
            self.assertEqual(invoke("one"), "")
            row["last_seen"] = 2
            path.write_text(json.dumps(row) + "\n")
            self.assertEqual(invoke("one"), "")
            self.assertIn("stdin stalled", invoke("two"))
            row["reason"] = "process exited"
            path.write_text(json.dumps(row) + "\n")
            self.assertIn("process exited", invoke("one"))
            row["cleared_at"] = 3
            path.write_text(json.dumps(row) + "\n")
            self.assertEqual(invoke("one"), "")
            row["cleared_at"] = None
            path.write_text(json.dumps(row) + "\n")
            self.assertIn("process exited", invoke("one"))

    def test_idle_branches_collapse_without_hiding_other_findings(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / config["paths"]["findings"]
            path.parent.mkdir(parents=True)
            rows = [{"key": f"branch:{i}", "kind": "branch_idle", "reason": f"branch {i} is idle",
                     "next_action": "inspect branch", "cleared_at": None} for i in range(20)]
            rows.append({"key": "job:hang", "kind": "job_hang", "reason": "stdin stalled",
                         "next_action": "inspect log", "cleared_at": None})
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            context = watchdog.digest(root, config)
            self.assertEqual(context.count("branch_idle"), 1)
            self.assertIn("branch_idle: 20", context)
            self.assertIn("stdin stalled", context)
            self.assertIn("21 open finding(s)", context)

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
