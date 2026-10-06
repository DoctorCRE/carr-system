#!/usr/bin/env python3
"""Coordinator side of migration 0735: skip and count unknown attendees.

One test per resolver outcome: a known attendee keeps its ref; an unknown one
(resolver returns NULL) is skipped, counted and reported only as an opaque key;
an ambiguous or tombstoned one (resolver raises) still refuses the whole
snapshot. example.test addresses only.
"""
import contextlib
import importlib.util
import io
import json
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location("calendar_prebrief_coordinator", REPO / "tools" / "calendar-prebrief-coordinator.py")
assert spec is not None and spec.loader is not None
coordinator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)

KNOWN, UNKNOWN, AMBIGUOUS = "known@clinic.example.test", "stranger@else.example.test", "twin@shared.example.test"
COLLEAGUE = "partner@carr.us"


def payload(*attendee_lists):
    events = []
    for i, emails in enumerate(attendee_lists):
        events.append({"sponsor": "joe", "calendar_key": "a" * 64, "event_key": f"{i:064x}",
                       "occurrence_key": f"{i + 100:064x}", "starts_at": "2026-09-28T15:00:00Z",
                       "ends_at": "2026-09-28T16:00:00Z", "title": "Meeting", "location": None,
                       "attendee_emails": list(emails)})
    return {"version": 1, "window": {"starts_at": "2026-09-21T11:30:00Z", "ends_at": "2026-11-12T11:30:00Z"},
            "observed_calendars": [{"sponsor": "joe", "calendar_key": "a" * 64}], "events": events}


class Ambiguous(RuntimeError):
    """Stands in for the resolver's 22023 refusal."""


def resolve(email):
    if email == KNOWN:
        return "C-100"
    if email == UNKNOWN:
        return None
    if email == AMBIGUOUS:
        raise Ambiguous("22023 exactly one live unmerged canonical ref")
    raise AssertionError("resolver called for an unexpected address")


class UnknownAttendees(unittest.TestCase):
    def test_known_attendee_keeps_its_ref(self):
        unknown = set()
        snap = coordinator._snapshot_from_raw(payload([KNOWN, COLLEAGUE]), "joe", resolve, unknown)
        self.assertEqual(snap["events"][0]["participant_refs"], ["C-100"])
        self.assertEqual(unknown, set())

    def test_unknown_attendee_is_skipped_counted_and_opaque(self):
        unknown = set()
        snap = coordinator._snapshot_from_raw(payload([KNOWN, UNKNOWN], [UNKNOWN]), "joe", resolve, unknown)
        self.assertEqual([e["participant_refs"] for e in snap["events"]], [["C-100"], []])
        report = coordinator.unknown_report(unknown)
        self.assertEqual(report["count"], 1)  # the same person in two events counts once
        self.assertEqual(report["attendee_keys"], [coordinator.attendee_key(UNKNOWN)])
        self.assertIsNone(re.search(r"@", repr(report)))
        self.assertTrue(coordinator._valid_unknown_report(report))
        # The DB-bound snapshot still has the exact shape the ingest boundary accepts.
        ingest_spec = importlib.util.spec_from_file_location("ingest", REPO / "tools" / "calendar-prebrief-ingest.py")
        assert ingest_spec is not None and ingest_spec.loader is not None
        ingest = importlib.util.module_from_spec(ingest_spec)
        ingest_spec.loader.exec_module(ingest)
        observed, events = ingest.normalize_snapshot(snap, "joe")
        self.assertEqual(observed, ["a" * 64])
        self.assertEqual([e["participant_refs"] for e in events], [["C-100"], []])

    def test_attendee_key_normalises_case_and_space(self):
        self.assertEqual(coordinator.attendee_key(" Stranger@Else.Example.Test "), coordinator.attendee_key(UNKNOWN))

    def test_ambiguous_attendee_still_refuses_the_whole_snapshot(self):
        with self.assertRaises(Ambiguous):
            coordinator._snapshot_from_raw(payload([KNOWN], [AMBIGUOUS]), "joe", resolve, set())

    def test_unknown_without_a_sink_refuses(self):
        with self.assertRaises(coordinator.Refusal):
            coordinator._snapshot_from_raw(payload([UNKNOWN]), "joe", resolve)

    def test_colleagues_are_never_resolved(self):
        unknown = set()
        snap = coordinator._snapshot_from_raw(payload([COLLEAGUE]), "joe", resolve, unknown)
        self.assertEqual(snap["events"][0]["participant_refs"], [])
        self.assertEqual(unknown, set())

    def test_report_is_bounded_and_validated(self):
        keys = {coordinator.attendee_key(f"p{i}@x.example.test") for i in range(100)}
        report = coordinator.unknown_report(keys)
        self.assertEqual(report["count"], 100)
        self.assertEqual(len(report["attendee_keys"]), coordinator.UNKNOWN_KEYS_CAP)
        self.assertTrue(coordinator._valid_unknown_report(report))
        for bad in ({"count": 1, "attendee_keys": []}, {"count": 1, "attendee_keys": [UNKNOWN]},
                    {"count": -1, "attendee_keys": []}, {"count": 0}):
            self.assertFalse(coordinator._valid_unknown_report(bad), bad)

    def test_capped_child_result_fits_the_parent_stdout_bound(self):
        keys = {coordinator.attendee_key(f"p{i}@x.example.test") for i in range(10_000)}
        result = {"sponsor": "joe", "mode": "live", "attestation_id": "0" * 36, "receipt_id": "0" * 36,
                  "unknown_attendees": coordinator.unknown_report(keys)}
        self.assertLess(len(json.dumps(result, sort_keys=True).encode() + b"\n"), 8192)


class LastRunSummary(unittest.TestCase):
    def setUp(self):
        rt_spec = importlib.util.spec_from_file_location("calendar_prebrief_joe_runtime", REPO / "tools" / "calendar-prebrief-joe-runtime.py")
        assert rt_spec is not None and rt_spec.loader is not None
        self.runtime = importlib.util.module_from_spec(rt_spec)
        rt_spec.loader.exec_module(self.runtime)
        self.dir = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.dir.name) / "last-run.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_failed_write_never_leaves_an_older_summary_standing(self):
        receipt = {"scheduled_for": "2026-09-28T11:30:00Z", "job_id": "j", "receipt_id": "r"}
        self.runtime.write_last_run(receipt, {"count": 3, "attendee_keys": []}, path=self.path)
        self.assertEqual(json.loads(self.path.read_text())["unknown_attendees"]["count"], 3)
        with mock.patch.object(self.runtime.os, "replace", side_effect=OSError("disk")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.runtime.write_last_run(receipt, {"count": 0, "attendee_keys": []}, path=self.path)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
