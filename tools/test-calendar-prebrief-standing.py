#!/usr/bin/env python3
"""Offline proof of health-check's Joe calendar-prebrief standing check.

health-check.py cannot be imported (its top level dials production and exits),
so, like tools/health-check-findings-selftest.py, this lifts the shipped
function and its constants out of the file's AST and runs them hermetically on
fixture snapshots. No database, no calendar, no addresses.
"""
import ast
import json
import os
import tempfile
import pathlib
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SOURCE = pathlib.Path(__file__).resolve().parent / "health-check.py"
WANT_FUNCS = {"_iso", "_canonical_now", "_live_jobs", "_calendar_prebrief_standing", "_calendar_prebrief_unknowns"}
WANT_NAMES = {"CALENDAR_PREBRIEF_KEY", "CALENDAR_PREBRIEF_JUDGED_AFTER", "CALENDAR_PREBRIEF_BREACH",
              "CALENDAR_PREBRIEF_LAST_RUN", "CALENDAR_PREBRIEF_UNKNOWN_BREACH"}


def _load():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    body = [node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in WANT_FUNCS)
            or (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in WANT_NAMES for t in node.targets))]
    found = {n.name for n in body if isinstance(n, ast.FunctionDef)} | {
        t.id for n in body if isinstance(n, ast.Assign) for t in n.targets}
    assert found == WANT_FUNCS | WANT_NAMES, found
    ns = {"json": json, "os": os, "REPO_ROOT": "/nonexistent-repo", "datetime": datetime, "timedelta": timedelta, "timezone": timezone, "ZoneInfo": ZoneInfo}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


NS = _load()
CHI = ZoneInfo("America/Chicago")
KEY = NS["CALENDAR_PREBRIEF_KEY"]


def slot(day):
    return datetime(2026, 9, day, 6, 30, tzinfo=CHI).astimezone(timezone.utc)


def job(i, day, state="succeeded", attempt=1):
    return {"id": f"job-{i}", "definition_key": KEY, "mode": "live", "state": state,
            "attempt": attempt, "scheduled_for": slot(day).isoformat()}


def snap(now, jobs=(), receipts=(), activated="2026-09-27T03:44:37+00:00", enabled=True):
    return {"observed_at": now.isoformat(),
            "job_definitions": [{"key": KEY}] if enabled else [],
            "jobs": list(jobs),
            "calendar_prebrief": {"activated_at": activated, "receipts": list(receipts)}}


def rc(i, events, attempt=1):
    return {"job_id": f"job-{i}", "attempt": attempt, "event_count": events}


# Activation Sat 2026-09-26 22:44 CT; first slot Mon 09-28, then Tue 09-29.
MON_LATE = datetime(2026, 9, 28, 8, 0, tzinfo=CHI)
TUE_LATE = datetime(2026, 9, 29, 8, 0, tzinfo=CHI)


class StandingCheck(unittest.TestCase):
    def keys(self, value):
        return [k for k, _ in NS["_calendar_prebrief_standing"](value)]

    def test_silent_before_the_first_slot_is_judged(self):
        self.assertEqual(self.keys(snap(datetime(2026, 9, 28, 7, 0, tzinfo=CHI))), [])

    def test_silent_when_not_activated_or_definition_disabled(self):
        self.assertEqual(self.keys(snap(MON_LATE, activated=None)), [])
        self.assertEqual(self.keys(snap(MON_LATE, enabled=False)), [])

    def test_never_scheduled_slot_is_a_missed_run(self):
        found = NS["_calendar_prebrief_standing"](snap(MON_LATE))
        self.assertEqual([k for k, _ in found], ["calendar_prebrief_missed_run"])
        self.assertIn("never scheduled", found[0][1])
        self.assertIn("on breach:", found[0][1])

    def test_dead_lettered_slot_is_a_missed_run(self):
        self.assertEqual(self.keys(snap(MON_LATE, [job(1, 28, "dead_lettered")])),
                         ["calendar_prebrief_missed_run"])

    def test_success_without_projection_receipt_is_a_missed_run(self):
        self.assertEqual(self.keys(snap(MON_LATE, [job(1, 28)])), ["calendar_prebrief_missed_run"])

    def test_receipted_success_is_healthy(self):
        self.assertEqual(self.keys(snap(MON_LATE, [job(1, 28)], [rc(1, 7)])), [])

    def test_one_zero_event_day_is_not_yet_a_finding(self):
        self.assertEqual(self.keys(snap(MON_LATE, [job(1, 28)], [rc(1, 0)])), [])

    def test_zero_events_two_runs_running_is_a_finding(self):
        found = self.keys(snap(TUE_LATE, [job(1, 28), job(2, 29)], [rc(1, 0), rc(2, 0)]))
        self.assertEqual(found, ["calendar_prebrief_zero_events"])

    def test_zero_then_nonzero_is_healthy(self):
        self.assertEqual(self.keys(snap(TUE_LATE, [job(1, 28), job(2, 29)], [rc(1, 0), rc(2, 3)])), [])

    def test_weekend_is_not_a_slot(self):
        # Friday 10-02 succeeded; Sunday 10-04 must not be judged as a miss.
        sunday = datetime(2026, 10, 4, 9, 0, tzinfo=CHI)
        jobs = [job(i, d) for i, d in enumerate((28, 29, 30), 1)] + [
            {**job(4, 1), "scheduled_for": datetime(2026, 10, 1, 6, 30, tzinfo=CHI).astimezone(timezone.utc).isoformat()},
            {**job(5, 2), "scheduled_for": datetime(2026, 10, 2, 6, 30, tzinfo=CHI).astimezone(timezone.utc).isoformat()}]
        receipts = [rc(i, 4) for i in range(1, 6)]
        self.assertEqual(self.keys(snap(sunday, jobs, receipts)), [])

    def test_retry_attempt_receipt_counts(self):
        self.assertEqual(self.keys(snap(MON_LATE, [job(1, 28, attempt=2)], [rc(1, 5, attempt=2)])), [])

    def test_malformed_receipts_are_unreadable_not_green(self):
        found = self.keys(snap(MON_LATE, [job(1, 28)], [{"job_id": "job-1", "attempt": 1}]))
        self.assertEqual(found, ["calendar_prebrief_unreadable"])

    def test_psql_offset_text_matches_the_slot(self):
        # db-tap prints timestamptz as '2026-09-28 11:30:00+00'.
        row = {**job(1, 28), "scheduled_for": "2026-09-28 11:30:00+00"}
        self.assertEqual(self.keys(snap(MON_LATE, [row], [rc(1, 2)])), [])


class UnknownAttendeeCheck(unittest.TestCase):
    """Migration 0735: skipped unknown attendees surface as intake debt."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "last-run.json")

    def tearDown(self):
        self.tmp.cleanup()

    def found(self, body, now=MON_LATE):
        if body is not None:
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write(body if isinstance(body, str) else json.dumps(body))
        return NS["_calendar_prebrief_unknowns"](now, self.path)

    def summary(self, count, day=28):
        return {"scheduled_for": slot(day).isoformat(), "job_id": "job-1", "receipt_id": "r-1",
                "unknown_attendees": {"count": count, "attendee_keys": ["a" * 64] * min(count, 1)}}

    def test_no_summary_yet_is_silent(self):
        self.assertEqual(self.found(None), [])

    def test_zero_skipped_is_silent(self):
        self.assertEqual(self.found(self.summary(0)), [])

    def test_skipped_attendees_are_a_finding_with_a_bound_action_and_no_address(self):
        found = self.found(self.summary(9))
        self.assertEqual([k for k, _ in found], ["calendar_prebrief_unknown_attendees"])
        self.assertIn("skipped 9 unknown", found[0][1])
        self.assertIn("on breach:", found[0][1])
        self.assertNotIn("@", found[0][1])

    def test_stale_summary_is_left_to_the_missed_run_check(self):
        self.assertEqual(self.found(self.summary(9), now=MON_LATE + timedelta(days=5)), [])

    def test_malformed_summary_is_unreadable_not_silent(self):
        self.assertEqual([k for k, _ in self.found("{not json")], ["calendar_prebrief_unknowns_unreadable"])
        self.assertEqual([k for k, _ in self.found({"scheduled_for": slot(28).isoformat()})],
                         ["calendar_prebrief_unknowns_unreadable"])
        self.assertEqual([k for k, _ in self.found(self.summary(-1))], ["calendar_prebrief_unknowns_unreadable"])


if __name__ == "__main__":
    unittest.main()
