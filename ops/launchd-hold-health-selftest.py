#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import launchd_hold, launchd_hold_health as health


class HealthTests(unittest.TestCase):
    def test_age_and_bound_loop_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / ".config/carr/launchd-hold"
            path.parent.mkdir(parents=True)
            path.write_text("com.carr.job-watchdog API quota exhausted\n")
            now = time.time()
            os.utime(path, (now - 8 * 86400, now - 8 * 86400))
            holds = launchd_hold.read_holds(home)
            calls = []
            def verb(name, payload):
                calls.append((name, payload))
                if name == "add-loop":
                    self.assertEqual(payload["owner"], "claude")
                return {"ok": True, "loop_id": "fixture-loop", "version": 4, "status": "open"}
            line, rc = health.check(home, verb, now=now)
            self.assertEqual(rc, 1)
            for term in ("8.0d", "owner claude", "repair", "verify", "auto-clear", "API quota exhausted"):
                self.assertIn(term, line)
            health.check(home, verb, now=now)
            self.assertEqual(sum(name == "add-loop" for name, _ in calls), 1)
            path.write_text("")
            line, rc = health.check(home, verb, now=now)
            self.assertEqual(rc, 0)
            self.assertEqual(calls[-1][0], "close-loop")
            self.assertEqual(calls[-1][1]["base_version"], 4)
            health.check(home, verb, now=now)
            self.assertEqual(sum(name == "close-loop" for name, _ in calls), 1)

    def test_unrelated_edit_cannot_renew_undated_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".config/carr/launchd-hold"
            path.parent.mkdir(parents=True)
            path.write_text("com.carr.a stuck\n")
            now = time.time()
            os.utime(path, (now - 9 * 86400, now - 9 * 86400))
            calls = []
            def verb(name, payload):
                calls.append(name)
                return {"ok": True, "loop_id": "fixture", "version": 1, "status": "open"}
            self.assertEqual(health.check(tmp, verb, now)[1], 1)
            path.write_text("com.carr.a stuck\ncom.carr.b unrelated\n")
            self.assertEqual(health.check(tmp, verb, now)[1], 1)
            self.assertNotIn("close-loop", calls)
            path.write_text("com.carr.a @" + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60)) + " reviewed\n")
            self.assertEqual(health.check(tmp, verb, now)[1], 0)
            self.assertIn("close-loop", calls)

    def test_corrupt_state_is_quarantined_and_alarm_still_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / ".config/carr"
            directory.mkdir(parents=True)
            (directory / "launchd-hold").write_text("com.carr.a @2020-01-01T00:00:00Z stuck\n")
            state = directory / "launchd-hold-health.json"
            state.write_text("{broken")
            calls = []
            def verb(name, payload):
                calls.append(name)
                return {"ok": True, "loop_id": "fixture", "version": 1, "status": "open"}
            line, code = health.check(tmp, verb)
            self.assertEqual(code, 1)
            self.assertIn("repair loops recorded", line)
            self.assertEqual(calls, ["add-loop"])
            preserved = list(directory.glob("launchd-hold-health.json.corrupt-*"))
            self.assertEqual(len(preserved), 1)
            self.assertEqual(preserved[0].read_text(), "{broken")

    def test_recent_holds_and_exact_threshold_are_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".config/carr/launchd-hold"
            path.parent.mkdir(parents=True)
            path.write_text("com.carr.fixture repair in progress\n")
            now = time.time()
            os.utime(path, (now - 7 * 86400, now - 7 * 86400))
            line, rc = health.check(tmp, lambda *_: self.fail("must not file a loop"), now=now)
            self.assertEqual(rc, 0)

    def test_record_failure_cannot_claim_loop_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".config/carr/launchd-hold"
            path.parent.mkdir(parents=True)
            path.write_text("com.carr.fixture @2020-01-01T00:00:00Z repair\n")
            line, rc = health.check(tmp, lambda *_: {"ok": False})
            self.assertEqual(rc, 1)
            self.assertIn("response failed", line)

    def test_lost_response_replays_identical_payload_and_closed_loop_reopens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".config/carr/launchd-hold"
            path.parent.mkdir(parents=True)
            path.write_text("com.carr.fixture @2020-01-01T00:00:00Z repair\n")
            calls = []
            closed = False
            def verb(name, payload):
                calls.append((name, payload.copy()))
                if len(calls) == 1:
                    raise RuntimeError("lost response")
                return {"ok": True, "loop_id": "fixture-loop", "version": 4,
                        "status": "done" if closed else "open"}
            health.check(tmp, verb)
            path.write_text("com.carr.fixture @2020-01-01T00:00:00Z corrected reason\n")
            health.check(tmp, verb)
            self.assertEqual(calls[0], calls[1])
            health.check(tmp, verb)
            closed = True
            health.check(tmp, verb)
            additions = [payload for name, payload in calls if name == "add-loop"]
            self.assertEqual(len(additions), 3)
            self.assertNotEqual(additions[-1]["idempotency_key"], additions[0]["idempotency_key"])

    def test_removed_hold_resolves_pending_add_before_auto_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".config/carr/launchd-hold"
            path.parent.mkdir(parents=True)
            path.write_text("com.carr.fixture @2020-01-01T00:00:00Z repair\n")
            calls = []
            def verb(name, payload):
                calls.append((name, payload.copy()))
                if len(calls) == 1:
                    return {"ok": False}
                return {"ok": True, "loop_id": "fixture-loop", "version": 4, "status": "open"}
            health.check(tmp, verb)
            path.write_text("")
            line, rc = health.check(tmp, verb)
            self.assertEqual(rc, 0, line)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(calls[-1][0], "close-loop")


if __name__ == "__main__":
    unittest.main()
