#!/usr/bin/env python3
"""Hermetic behavior checks for the Jev outage health row and bound loop."""
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import jev_outage_health as health  # noqa:E402


def receipt(ts):
    return {"ts": ts, "ok": True, "usable": True, "schema_valid": True,
            "http_status": 200, "model": "jev-test",
            "usage": {"input_tokens": 10, "output_tokens": 2}}


class OutageTests(unittest.TestCase):
    def test_health_action_names_remedy_and_clear_condition(self):
        self.assertIn("add TypeSafe credits", health.action("billing_exhausted"))
        self.assertIn("repair the Jev response contract", health.action("invalid_data"))
        self.assertIn("auto-clear on that judgment", health.action("invalid_data"))

    def test_complete_state_event_transition_table(self):
        """Explicit policy matrix: only a known failure after healthy gets grace."""
        now = health.parse_time("2026-09-28T14:00:00Z")
        old = health.parse_time("2026-09-28T11:00:00Z")
        cases = {
            "healthy": [
                ("usable_success", "healthy", "ok"),
                ("unusable_success", "outage_open", "warn"),
                ("classified_failure", "failing_in_grace", "skip"),
                ("log_missing", "unknown", "warn"),
                ("log_corrupt", "unknown", "warn"),
                ("log_truncated", "unknown", "warn"),
                ("legacy_incomplete", "outage_open", "warn"),
                ("state_missing", "outage_open", "warn"),
                ("state_corrupt", "outage_open", "warn"),
            ],
            "failing_in_grace": [
                ("usable_success", "healthy", "ok"),
                ("unusable_success", "outage_open", "warn"),
                ("classified_failure", "outage_open", "warn"),
                ("log_missing", "outage_open", "warn"),
                ("log_corrupt", "outage_open", "warn"),
                ("log_truncated", "outage_open", "warn"),
                ("legacy_incomplete", "outage_open", "warn"),
                ("state_missing", "outage_open", "warn"),
                ("state_corrupt", "outage_open", "warn"),
            ],
            "outage_open": [
                ("usable_success", "healthy", "ok"),
                ("unusable_success", "outage_open", "warn"),
                ("classified_failure", "outage_open", "warn"),
                ("log_missing", "outage_open", "warn"),
                ("log_corrupt", "outage_open", "warn"),
                ("log_truncated", "outage_open", "warn"),
                ("legacy_incomplete", "outage_open", "warn"),
                ("state_missing", "outage_open", "warn"),
                ("state_corrupt", "outage_open", "warn"),
            ],
            "unknown": [
                ("usable_success", "healthy", "ok"),
                ("unusable_success", "outage_open", "warn"),
                ("classified_failure", "outage_open", "warn"),
                ("log_missing", "unknown", "warn"),
                ("log_corrupt", "unknown", "warn"),
                ("log_truncated", "unknown", "warn"),
                ("legacy_incomplete", "outage_open", "warn"),
                ("state_missing", "outage_open", "warn"),
                ("state_corrupt", "outage_open", "warn"),
            ],
        }
        self.assertEqual(sum(map(len, cases.values())), 36)
        self.assertEqual(set(cases), health.STATES)
        for before, rows in cases.items():
            self.assertEqual({event for event, _, _ in rows}, health.EVENTS)
            for event, after, verdict in rows:
                with self.subTest(before=before, event=event):
                    initial = {"state": before, "loop_id": "loop-123", "version": 1, "status": "open"} if before == "outage_open" else {"state": before}
                    if before == "failing_in_grace":
                        initial["first_failure_at"] = old.isoformat()
                    result = health.transition(initial, event, at=now, now=now,
                                               legacy_mtime=old, reason="billing_exhausted",
                                               evidence=receipt(now.isoformat()) if
                                               event == "usable_success" else None)
                    self.assertEqual((result["state"], result["status"]),
                                     (after, verdict))
                    if event == "legacy_incomplete":
                        self.assertEqual(result["first_failure_at"], old.isoformat())
                    if before == "outage_open" and event != "usable_success":
                        self.assertEqual(result["loop_id"], "loop-123")
        for invalid in ({"ts": now.isoformat(), "ok": True},
                        {**receipt(now.isoformat()), "http_status": 302},
                        {**receipt(now.isoformat()), "schema_valid": False},
                        {**receipt(now.isoformat()), "usage": None}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                health.clear({"state": "outage_open"}, invalid)
        with self.assertRaises(ValueError):
            health.clear({"state": "outage_open", "attempt_at": now.isoformat()},
                         receipt(now.isoformat()))

    def test_failure_during_grace_survives_lost_log_until_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            judge = root / "judge.jsonl"
            state = root / "outage-state.json"
            state.write_text(json.dumps({"state": "healthy",
                "last_success_at": "2026-09-28T10:00:00Z",
                "event_at": "2026-09-28T10:00:00Z"}))
            calls.write_text(json.dumps(receipt("2026-09-28T10:00:00Z")) + "\n")
            judge.write_text(json.dumps({"at": "2026-09-28T11:00:00Z",
                                         "error": "TypeSafe returned HTTP 402"}) + "\n")
            first = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T11:30:00Z"), state_path=state)
            self.assertEqual(first["status"], "skip")
            self.assertTrue(first["pending"])
            self.assertEqual(health.reconcile(first, state,
                lambda name, payload: self.fail(f"premature {name}")), "none")
            self.assertEqual(json.loads(state.read_text())["first_failure_at"],
                             "2026-09-28T11:00:00+00:00")

            judge.write_text(judge.read_text() + json.dumps({
                "at": "2026-09-28T12:00:00Z", "error": "TypeSafe returned HTTP 402"}) + "\n")
            retried = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T12:01:00Z"), state_path=state)
            self.assertEqual(health.reconcile(retried, state,
                lambda name, payload: self.fail(f"premature {name}")), "none")
            self.assertEqual(json.loads(state.read_text())["first_failure_at"],
                             "2026-09-28T11:00:00+00:00")

            judge.unlink()
            during_grace = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T12:30:00Z"), state_path=state)
            self.assertEqual((during_grace["state"], during_grace["status"]),
                             ("outage_open", "warn"))
            expired = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T13:00:00Z"), state_path=state)
            self.assertEqual((expired["status"], expired["reason"]),
                             ("warn", "log_unreadable"))

            calls.write_text(calls.read_text() + json.dumps(
                receipt("2026-09-28T13:02:00Z")) + "\n")
            recovered = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T13:03:00Z"), state_path=state)
            self.assertEqual(recovered["status"], "ok")
            self.assertEqual(health.reconcile(recovered, state,
                lambda name, payload: self.fail(f"unexpected {name}")), "cleared")
            self.assertEqual(json.loads(state.read_text())["state"], "healthy")

    def test_stale_success_with_expired_402_attempt_warns_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            judge = root / "judge.jsonl"
            calls.write_text(json.dumps(receipt("2026-09-28T01:00:00Z")) + "\n")
            judge.write_text(json.dumps({"at": "2026-09-28T11:00:00Z",
                                         "error": "TypeSafe returned HTTP 402: SECRET"}) + "\n")
            judge.write_text(judge.read_text() + json.dumps({
                "at": "2026-09-28T11:30:00Z", "model": "fake-jev",
                "answers": {"test": {"noul": 0.9}}}) + "\n")
            result = health.evaluate(judge, calls, now=health.parse_time("2026-09-28T14:00:00Z"),
                                     threshold_hours=2)
            self.assertEqual((result["status"], result["reason"]),
                             ("warn", "billing_exhausted"))
            self.assertNotIn("SECRET", json.dumps(result))
            calls.write_text(calls.read_text() + json.dumps(
                receipt("2026-09-28T14:01:00Z")) + "\n")
            self.assertEqual(health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T14:02:00Z"))["status"], "ok")

    def test_no_attempt_is_not_an_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            calls.write_text(json.dumps(receipt("2026-09-28T01:00:00Z")) + "\n")
            result = health.evaluate(root / "missing.jsonl", calls,
                                     now=health.parse_time("2026-09-28T12:00:00Z"))
            self.assertEqual(result["status"], "skip")

    def test_missing_persisted_state_alarms_without_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = health.evaluate(root / "judge.jsonl", root / "calls.jsonl",
                now=health.parse_time("2026-09-28T14:00:00Z"),
                state_path=root / "missing-state.json")
            self.assertEqual((result["state"], result["status"], result["reason"]),
                             ("outage_open", "warn", "state_unreadable"))

    def test_failed_402_stays_warn_until_a_later_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            judge = root / "judge.jsonl"
            calls.write_text(json.dumps(receipt("2026-09-28T01:00:00Z")) + "\n")
            judge.write_text(json.dumps({"at": "2026-09-28T11:00:00Z",
                                         "error": "TypeSafe returned HTTP 402"}) + "\n")
            for when in ("2026-09-28T12:00:00Z", "2026-09-28T14:00:00Z"):
                with self.subTest(when=when):
                    result = health.evaluate(judge, calls, now=health.parse_time(when))
                    self.assertEqual((result["status"], result["reason"]),
                                     ("skip", "billing_exhausted") if when.endswith("12:00:00Z")
                                     else ("warn", "billing_exhausted"))

    def test_open_outage_stays_warn_when_judge_log_is_missing_or_damaged(self):
        for damaged in (None, '{"at": "2026-09-28T11:00:00Z", "error":',
                        "this is not json\n", "valid_prefix"):
            with self.subTest(damaged=damaged), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                calls = root / "calls.jsonl"
                judge = root / "judge.jsonl"
                state = root / "outage-state.json"
                state.write_text(json.dumps({"state": "healthy",
                    "last_success_at": "2026-09-28T01:00:00Z",
                    "event_at": "2026-09-28T01:00:00Z"}))
                calls.write_text(json.dumps(receipt("2026-09-28T01:00:00Z")) + "\n")
                prefix = json.dumps({"at": "2026-09-28T10:00:00Z",
                                     "error": "TypeSafe returned HTTP 402"}) + "\n"
                judge.write_text(prefix + json.dumps({"at": "2026-09-28T11:00:00Z",
                                                      "error": "TypeSafe returned HTTP 402"}) + "\n")
                warning = health.evaluate(judge, calls,
                    now=health.parse_time("2026-09-28T14:00:00Z"), state_path=state)
                self.assertEqual(health.reconcile(warning, state,
                    lambda name, payload: {"ok": True, "loop_id": "loop-123", "version": 1, "status": "open"}), "opened")
                if damaged is None:
                    judge.unlink()
                else:
                    judge.write_text(prefix if damaged == "valid_prefix" else damaged)
                result = health.evaluate(judge, calls,
                    now=health.parse_time("2026-09-28T14:10:00Z"), state_path=state)
                self.assertEqual((result["status"], result["reason"]),
                                 ("warn", "log_unreadable"))
                updates = []
                def update(name, payload):
                    updates.append((name, payload))
                    return {"ok": True, "loop_id": "loop-123", "version": 1, "status": "open"}
                self.assertEqual(health.reconcile(result, state, update), "updated")
                self.assertIn("judgment log", next(payload["body"] for name, payload in updates if name == "update-loop"))
                calls.write_text(calls.read_text() + json.dumps(
                    receipt("2026-09-28T14:11:00Z")) + "\n")
                recovered = health.evaluate(judge, calls,
                    now=health.parse_time("2026-09-28T14:12:00Z"), state_path=state)
                self.assertEqual(recovered["status"], "ok")
                self.assertEqual(health.reconcile(recovered, state,
                    lambda name, payload: {"ok": True, "loop_id": "loop-123", "version": 1, "status": "open"}), "cleared")

    def test_one_loop_then_auto_close_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            calls = []
            def verb(name, payload):
                calls.append((name, payload))
                return {"ok": True, "loop_id": "loop-123", "version": 1, "status": "open", "number": "777"}
            warning = {"status": "warn", "reason": "billing_exhausted", "age_hours": 10}
            self.assertEqual(health.reconcile(warning, state, verb), "opened")
            self.assertEqual(health.reconcile(warning, state, verb), "open")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["owner"], "Joe")
            self.assertIn("add TypeSafe credits", calls[0][1]["body"])
            recovered = health.transition({"state": "outage_open"}, "usable_success",
                at=health.parse_time("2026-09-28T14:00:00Z"),
                now=health.parse_time("2026-09-28T14:00:00Z"),
                evidence=receipt("2026-09-28T14:00:00Z"))
            self.assertEqual(health.reconcile(recovered, state, verb), "cleared")
            self.assertEqual(calls[-1][0], "close-loop")
            self.assertEqual(calls[-1][1]["loop_id"], "loop-123")

    def test_recovery_remains_healthy_on_next_poll_with_unchanged_logs(self):
        for fail_close in (False, True):
            with self.subTest(fail_close=fail_close), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = root / "state.json"
                calls = root / "calls.jsonl"
                judge = root / "judge.jsonl"
                state.write_text(json.dumps({"state": "healthy",
                    "event_at": "2026-09-28T09:00:00Z",
                    "last_success_at": "2026-09-28T09:00:00Z"}))
                calls.write_text(json.dumps(receipt("2026-09-28T09:00:00Z")) + "\n")
                judge.write_text(json.dumps({"at": "2026-09-28T10:00:00Z",
                                             "error": "TypeSafe returned HTTP 402"}) + "\n")
                mutations = []
                loop_status = "open"
                close_failed = False
                def verb(name, payload):
                    nonlocal loop_status, close_failed
                    mutations.append((name, payload))
                    if name == "close-loop":
                        if fail_close and not close_failed:
                            close_failed = True
                            return {"ok": False}
                        loop_status = "done"
                    return {"ok": True, "loop_id": "loop-123", "version": 1,
                            "status": loop_status}
                def poll(when):
                    result = health.evaluate(judge, calls, state_path=state,
                        now=health.parse_time(when))
                    return result, health.reconcile(result, state, verb)
                self.assertEqual(poll("2026-09-28T13:00:00Z")[1], "opened")
                calls.write_text(calls.read_text() + json.dumps(
                    receipt("2026-09-28T13:01:00Z")) + "\n")
                result, outcome = poll("2026-09-28T13:02:00Z")
                self.assertEqual(result["status"], "ok")
                self.assertEqual(outcome, "error" if fail_close else "cleared")
                if fail_close:
                    self.assertEqual(poll("2026-09-28T13:03:00Z")[1], "cleared")
                    closes = [payload for name, payload in mutations if name == "close-loop"]
                    self.assertEqual(closes[0], closes[1])
                result, outcome = poll("2026-09-28T13:04:00Z")
                self.assertEqual((result["state"], result["status"], outcome),
                                 ("healthy", "ok", "none"))
                self.assertEqual(sum(name == "add-loop" for name, _ in mutations), 1)
                persisted = json.loads(state.read_text())
                self.assertNotIn("loop_id", persisted)
                self.assertNotIn("open_key", persisted)
                self.assertNotIn("close_payload", persisted)

    def test_existing_loop_without_attempt_time_requires_new_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            judge = root / "missing-judge.jsonl"
            calls = root / "calls.jsonl"
            state.write_text(json.dumps({"loop_id": "loop-123", "version": 1, "status": "open",
                                         "reason": "billing_exhausted"}))
            opened = health.parse_time("2026-09-28T11:00:00Z").timestamp()
            os.utime(state, (opened, opened))
            calls.write_text(json.dumps(receipt("2026-09-28T09:00:00Z")) + "\n")
            pending = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T12:00:00Z"), state_path=state)
            self.assertEqual((pending["status"], pending["reason"]),
                             ("warn", "log_unreadable"))
            calls.write_text(calls.read_text() + json.dumps(
                receipt("2026-09-28T11:01:00Z")) + "\n")
            recovered = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T12:00:00Z"), state_path=state)
            self.assertEqual(recovered["status"], "ok")

    def test_later_failed_attempt_moves_recovery_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            judge = root / "judge.jsonl"
            calls = root / "calls.jsonl"
            state.write_text(json.dumps({"state": "healthy",
                "last_success_at": "2026-09-28T09:00:00Z",
                "event_at": "2026-09-28T09:00:00Z"}))
            calls.write_text(json.dumps(receipt("2026-09-28T09:00:00Z")) + "\n")
            judge.write_text(json.dumps({"at": "2026-09-28T11:00:00Z",
                                         "error": "TypeSafe returned HTTP 402"}) + "\n")
            verb = lambda name, payload: {"ok": True, "loop_id": "loop-123", "version": 1, "status": "open"}
            first = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T14:00:00Z"), state_path=state)
            self.assertEqual(health.reconcile(first, state, verb), "opened")
            judge.write_text(judge.read_text() + json.dumps({
                "at": "2026-09-28T15:00:00Z", "error": "TypeSafe returned HTTP 402"}) + "\n")
            later = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T16:00:00Z"), state_path=state)
            self.assertEqual(health.reconcile(later, state, verb), "open")
            judge.unlink()
            calls.write_text(calls.read_text() + json.dumps(
                receipt("2026-09-28T14:00:00Z")) + "\n")
            still_open = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T16:00:00Z"), state_path=state)
            self.assertEqual((still_open["status"], still_open["reason"]),
                             ("warn", "log_unreadable"))

    def test_legacy_open_loop_keeps_mtime_anchor_after_fresh_failed_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            judge = root / "judge.jsonl"
            calls = root / "calls.jsonl"
            state.write_text(json.dumps({"loop_id": "loop-123", "version": 1, "status": "open", "reason": "billing_exhausted"}))
            opened = health.parse_time("2026-09-28T10:00:00Z")
            os.utime(state, (opened.timestamp(), opened.timestamp()))
            judge.write_text(json.dumps({"at": "2026-09-28T14:00:00Z",
                                         "error": "TypeSafe returned HTTP 402"}) + "\n")
            result = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T14:01:00Z"), state_path=state)
            self.assertEqual((result["state"], result["status"]), ("outage_open", "warn"))
            self.assertEqual(result["first_failure_at"], opened.isoformat())

    def test_http_200_empty_judgment_cannot_clear_open_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            judge = root / "judge.jsonl"
            calls = root / "calls.jsonl"
            state.write_text(json.dumps({"state": "outage_open", "loop_id": "loop-123", "version": 1, "status": "open",
                                         "reason": "billing_exhausted",
                                         "first_failure_at": "2026-09-28T10:00:00+00:00",
                                         "attempt_at": "2026-09-28T11:00:00+00:00"}))
            judge.write_text(json.dumps({"at": "2026-09-28T11:00:00Z",
                                         "error": "TypeSafe returned HTTP 402"}) + "\n")
            calls.write_text(json.dumps({"ts": "2026-09-28T14:00:00Z", "ok": False,
                                         "usable": False, "schema_valid": False,
                                         "http_status": 200, "usage": None}) + "\n")
            result = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T14:01:00Z"), state_path=state)
            self.assertEqual((result["state"], result["status"]), ("outage_open", "warn"))
            verbs = []
            def verb(name, payload):
                verbs.append(name)
                return {"ok": True, "version": 1, "status": "open"}
            self.assertIn(health.reconcile(result, state, verb), ("open", "updated"))
            self.assertNotIn("close-loop", verbs)

    def test_old_receipt_without_validation_alarms_as_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            calls.write_text(json.dumps({"ts": "2026-09-28T10:00:00Z",
                                         "ok": True}) + "\n")
            result = health.evaluate(root / "missing-judge.jsonl", calls,
                now=health.parse_time("2026-09-28T14:00:00Z"))
            self.assertEqual((result["state"], result["status"]),
                             ("outage_open", "warn"))

    def test_corrupt_state_is_preserved_and_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            state.write_text("broken-state")
            result = health.evaluate(root / "judge.jsonl", root / "calls.jsonl",
                now=health.parse_time("2026-09-28T14:00:00Z"), state_path=state)
            self.assertEqual((result["state"], result["status"]), ("outage_open", "warn"))
            self.assertEqual(health.reconcile(result, state,
                lambda name, payload: {"ok": True, "loop_id": "loop-123", "version": 1, "status": "open"}), "opened")
            saved = list(root.glob("state.json.corrupt-*"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_text(), "broken-state")


if __name__ == "__main__":
    unittest.main()
