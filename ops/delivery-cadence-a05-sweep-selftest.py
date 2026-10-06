#!/usr/bin/env python3
"""Focused deterministic tests for the V5-A05 delivery-cadence sweep's
call_verb-facing decision logic (ops/delivery-cadence-a05-sweep.py).

Review finding 4 (Opus adversarial review of PR #1236, round 1): the sweep's
fail-open surface was untested -- an unrecognized status, a non-object
payload, a missing expires_at, and a "neither minted nor duplicate" response
from raise-delivery-cadence-alert all had to be inferred from reading the
source. This monkeypatches call_verb so every branch is exercised without a
real Postgres or run.sh subprocess.
"""

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "ops" / "delivery-cadence-a05-sweep.py"
SPEC = importlib.util.spec_from_file_location("delivery_cadence_a05_sweep", MODULE_PATH)
assert SPEC and SPEC.loader
sweep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sweep)

SUBJECT = {"subject_type": "engineering_program", "subject_ref": "doctorcre-v5"}


class SweepSubjectTests(unittest.TestCase):
    def test_status_read_failure_is_not_a_crash(self):
        with mock.patch.object(sweep, "call_verb", return_value=(False, "boom")):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "status_read_failed")
        self.assertEqual(result["detail"], "boom")

    def test_current_status_is_no_action(self):
        with mock.patch.object(sweep, "call_verb", return_value=(True, {"status": "current"})):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "no_action")
        self.assertEqual(result["status"], "current")

    def test_no_receipt_on_record_is_no_action(self):
        with mock.patch.object(sweep, "call_verb",
                                return_value=(True, {"status": "no_receipt_on_record"})):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "no_action")

    def test_non_object_status_payload_does_not_raise(self):
        with mock.patch.object(sweep, "call_verb", return_value=(True, "not an object")):
            result = sweep.sweep_subject(SUBJECT)  # must not raise AttributeError
        self.assertEqual(result["outcome"], "status_read_failed")
        self.assertIn("non-object payload", result["detail"])

    def test_null_status_payload_does_not_raise(self):
        with mock.patch.object(sweep, "call_verb", return_value=(True, None)):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "status_read_failed")

    def test_unrecognized_status_fails_closed(self):
        with mock.patch.object(sweep, "call_verb",
                                return_value=(True, {"status": "somehow_else"})):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "status_read_failed")
        self.assertIn("unrecognized status", result["detail"])

    def test_missed_with_no_expires_at_refuses_to_alert(self):
        with mock.patch.object(sweep, "call_verb",
                                return_value=(True, {"status": "missed"})) as mocked:
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "escalation_failed")
        self.assertIn("no expires_at", result["detail"])
        # cadence-status was read once; raise-delivery-cadence-alert must
        # never have been called with a missing expires_at.
        self.assertEqual(mocked.call_count, 1)

    def test_missed_calls_raise_and_reports_escalated_on_fresh_mint(self):
        calls = []

        def fake_call_verb(verb, args):
            calls.append((verb, args))
            if verb == "cadence-status":
                return True, {"status": "missed", "expires_at": "2026-09-01T00:00:00Z",
                              "days_since_last_receipt": 20}
            return True, {"duplicate": False, "routing": {"routing": "morning_batch"},
                          "notification": {"ok": True, "minted": True}}

        with mock.patch.object(sweep, "call_verb", side_effect=fake_call_verb):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "escalated")
        self.assertEqual(calls[0][0], "cadence-status")
        self.assertEqual(calls[1][0], "raise-delivery-cadence-alert")
        self.assertEqual(calls[1][1]["reason_id"], "cadence_miss_replan_required")

    def test_missed_calls_raise_and_reports_escalated_on_duplicate(self):
        def fake_call_verb(verb, args):
            if verb == "cadence-status":
                return True, {"status": "missed", "expires_at": "2026-09-01T00:00:00Z"}
            return True, {"duplicate": True, "notification": {"minted": False,
                          "reason_id": "duplicate_alert"}}

        with mock.patch.object(sweep, "call_verb", side_effect=fake_call_verb):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "escalated")
        self.assertTrue(result["duplicate"])

    def test_raise_verb_call_failure_is_escalation_failed(self):
        def fake_call_verb(verb, args):
            if verb == "cadence-status":
                return True, {"status": "missed", "expires_at": "2026-09-01T00:00:00Z"}
            return False, "run.sh call raise-delivery-cadence-alert exit 1"

        with mock.patch.object(sweep, "call_verb", side_effect=fake_call_verb):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "escalation_failed")

    def test_neither_minted_nor_duplicate_is_escalation_failed(self):
        """Review finding 4's explicit case: reaching nobody is not success."""
        def fake_call_verb(verb, args):
            if verb == "cadence-status":
                return True, {"status": "missed", "expires_at": "2026-09-01T00:00:00Z"}
            return True, {"duplicate": False,
                          "notification": {"ok": True, "minted": False,
                                           "reason_id": "no_sponsoring_partner"}}

        with mock.patch.object(sweep, "call_verb", side_effect=fake_call_verb):
            result = sweep.sweep_subject(SUBJECT)
        self.assertEqual(result["outcome"], "escalation_failed")
        self.assertIn("neither minted nor a recognized duplicate", result["detail"])

    def test_raise_verb_non_object_payload_does_not_raise(self):
        def fake_call_verb(verb, args):
            if verb == "cadence-status":
                return True, {"status": "missed", "expires_at": "2026-09-01T00:00:00Z"}
            return True, "not an object"

        with mock.patch.object(sweep, "call_verb", side_effect=fake_call_verb):
            result = sweep.sweep_subject(SUBJECT)  # must not raise
        self.assertEqual(result["outcome"], "escalation_failed")

    def test_main_returns_nonzero_when_any_subject_fails(self):
        with mock.patch.object(sweep, "SUBJECTS", [SUBJECT]), \
             mock.patch.object(sweep, "call_verb", return_value=(False, "boom")):
            self.assertEqual(sweep.main(), 1)

    def test_main_returns_zero_when_all_subjects_are_no_action(self):
        with mock.patch.object(sweep, "SUBJECTS", [SUBJECT]), \
             mock.patch.object(sweep, "call_verb", return_value=(True, {"status": "current"})):
            self.assertEqual(sweep.main(), 0)


if __name__ == "__main__":
    unittest.main()
