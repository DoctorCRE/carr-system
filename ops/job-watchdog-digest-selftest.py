#!/usr/bin/env python3
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
