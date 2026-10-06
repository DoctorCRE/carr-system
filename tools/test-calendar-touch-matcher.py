#!/usr/bin/env python3
"""The calendar matcher reads the LIVE exports and refuses an empty book.

Regression for 2026-09-27: the matcher read the exporters' draft directory
(out/exports), which the live nightly chain never writes, so every run loaded
0 record contacts and reported every attendee as unknown. Fixture workbooks
only; example.test addresses.
"""
import datetime
import json
import importlib.util
import io
import os
import pathlib
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import openpyxl

REPO = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calendar_touch_matcher", REPO / "tools" / "calendar-touch-matcher.py")
assert spec is not None and spec.loader is not None
matcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matcher)


def workbook(path, sheet, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(row)
    wb.save(path)


class LiveExports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def seed_live(self):
        workbook(self.root / matcher.ROSTER_REL, "Clients",
                 ["Client ID", "Name", "Practice / Entity", "Email"],
                 [["C-1", "Client One", "Practice A", "one@clinic-a.example.test"]])
        workbook(self.root / matcher.REGISTRY_REL, "Registry",
                 ["Lead ID", "Contact Name", "Practice", "Email"],
                 [["L-1", "Lead Two", "Practice B", "two@gmail.com"]])
        workbook(self.root / matcher.VENDORS_REL, "Vendors",
                 ["ID", "Name", "Company", "Email"], [])

    def test_partial_export_failure_refuses_even_with_client_contacts(self):
        self.seed_live()
        for damage in ("missing", "sheet", "headers"):
            path = self.root / matcher.VENDORS_REL
            if damage == "missing":
                path.unlink()
            elif damage == "sheet":
                workbook(path, "Wrong", ["ID", "Name", "Company", "Email"], [])
            elif damage == "headers":
                workbook(path, "Vendors", ["Email"], [["vendor@service.example.test"]])
            with self.subTest(damage=damage), self.assertRaises(matcher.NoRecordContacts):
                matcher.load_record_contacts(root=str(self.root))

    def test_relative_paths_match_the_exporters(self):
        text = (REPO / "exporters" / "targets.py").read_text(encoding="utf-8")
        self.assertIn(f'ROSTER_REL = "{matcher.ROSTER_REL}"', text)
        self.assertIn(f'REGISTRY_REL = "{matcher.REGISTRY_REL}"', text)
        self.assertIn(f'VENDORS_REL = "{matcher.VENDORS_REL}"', text)

    def test_default_root_is_the_exporters_export_home(self):
        with mock.patch.dict(os.environ, {"CARR_EXPORT_HOME": str(self.root)}):
            sys.modules.pop("exporters.common", None)
            sys.modules.pop("exporters", None)
            self.assertEqual(matcher.export_home(), str(self.root))
        sys.modules.pop("exporters.common", None)

    def test_loads_contacts_from_the_live_layout(self):
        self.seed_live()
        by_email, by_domain = matcher.load_record_contacts(root=str(self.root))
        self.assertEqual(sorted(by_email), ["one@clinic-a.example.test", "two@gmail.com"])
        # Freemail never becomes a domain match.
        self.assertEqual(sorted(by_domain), ["clinic-a.example.test"])

    def test_vendor_export_is_an_exact_contact_source(self):
        self.seed_live()
        workbook(self.root / "DNA/Network/vendors.xlsx", "Vendors",
                 ["ID", "Name", "Company", "Email"],
                 [["V-1", "Synthetic Vendor", "Synthetic Service", "vendor@service.example.test"]])
        emails, domains = matcher.load_record_contacts(root=str(self.root))
        self.assertEqual(emails.get("vendor@service.example.test"), "V-1 / Synthetic Vendor")
        self.assertIn("service.example.test", domains)

    def test_draft_flat_layout_is_not_read(self):
        workbook(self.root / "client-roster.xlsx", "Clients",
                 ["Client ID", "Name", "Practice / Entity", "Email"],
                 [["C-1", "Client One", "Practice A", "one@clinic-a.example.test"]])
        with self.assertRaises(matcher.NoRecordContacts) as caught:
            matcher.load_record_contacts(root=str(self.root))
        self.assertIn("absent", str(caught.exception))

    def test_empty_book_refuses_loudly_without_addresses(self):
        dump = self.root / "dump.json"
        dump.write_text('{"Meeting|2026-09-25": ["someone@else.example.test"]}', encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(matcher, "export_home", return_value=str(self.root)), \
                mock.patch.object(sys, "argv", ["matcher", "7", "--json", "--from-dump", str(dump)]), \
                redirect_stderr(err):
            code = matcher.main()
        self.assertEqual(code, 5)
        self.assertIn("FATAL: record contacts: none loaded", err.getvalue())
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", err.getvalue()))

    def test_newest_touch_and_nearest_future_event_are_reported(self):
        self.seed_live()
        today = datetime.date.today()
        old = (today - datetime.timedelta(days=3)).isoformat()
        latest = (today - datetime.timedelta(days=1)).isoformat()
        near = (today + datetime.timedelta(days=1)).isoformat()
        far = (today + datetime.timedelta(days=8)).isoformat()
        dump = self.root / "dump.json"
        dump.write_text(json.dumps({
            f"Old synthetic meeting|{old}": ["one@clinic-a.example.test"],
            f"New synthetic meeting|{latest}": ["one@clinic-a.example.test"],
            f"Near synthetic meeting|{near}": ["one@clinic-a.example.test"],
            f"Far synthetic meeting|{far}": ["one@clinic-a.example.test"],
        }))
        output = io.StringIO()
        with mock.patch.object(matcher, "export_home", return_value=str(self.root)), \
                mock.patch.object(sys, "argv", ["matcher", "7", "--json", "--from-dump", str(dump)]), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(matcher.main(), 0)
        proposal = json.loads(output.getvalue())["exact"][0]
        self.assertEqual(proposal["last_seen"], latest)
        self.assertEqual([e["day"] for e in proposal["events"]], [latest, old])
        output = io.StringIO()
        with mock.patch.object(matcher, "export_home", return_value=str(self.root)), \
                mock.patch.object(sys, "argv", ["matcher", "7", "--from-dump", str(dump)]), \
                redirect_stdout(output):
            self.assertEqual(matcher.main(), 0)
        self.assertIn("Near synthetic meeting", output.getvalue())
        self.assertNotIn("Far synthetic meeting", output.getvalue())

    def test_nearest_same_day_future_meeting_uses_start_instant(self):
        self.seed_live()
        now = datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.timezone.utc)
        # Offset spellings deliberately reverse lexical and instant ordering.
        for near, far in (("2026-10-02T13:00:00+00:00", "2026-10-02T18:00:00+00:00"),
                          ("2026-10-02T15:00:00+02:00", "2026-10-02T14:00:00-04:00")):
            meetings = [
                {"event_id": "near", "start_at": near, "title": "Near future meeting",
                 "emails": ["one@clinic-a.example.test"]},
                {"event_id": "far", "start_at": far, "title": "Far future meeting",
                 "emails": ["one@clinic-a.example.test"]},
            ]
            for ordered in (meetings, meetings[::-1]):
                with self.subTest(near=near, first=ordered[0]["event_id"]):
                    dump = self.root / "dump.json"
                    dump.write_text(json.dumps({"schema": "calendar-events/v2", "events": ordered}))
                    output = io.StringIO()
                    with mock.patch.object(matcher.time, "time", return_value=now.timestamp()), \
                            mock.patch.object(matcher, "export_home", return_value=str(self.root)), \
                            mock.patch.object(sys, "argv", ["matcher", "7", "--from-dump", str(dump)]), \
                            redirect_stdout(output), redirect_stderr(io.StringIO()):
                        self.assertEqual(matcher.main(), 0)
                    report = output.getvalue()
                    self.assertIn("UPCOMING", report)
                    self.assertIn("Near future meeting", report)
                    self.assertNotIn("Far future meeting", report)
                    self.assertIn("distinct attendee emails: 0", report)

    def test_timestamped_dump_excludes_later_today_and_preserves_occurrences(self):
        now = datetime.datetime(2026, 10, 1, 23, 30, tzinfo=datetime.timezone.utc)
        dump = self.root / "dump.json"
        dump.write_text(json.dumps({"schema": "calendar-events/v2", "events": [
            {"event_id": "series/one", "start_at": "2026-10-01T18:00:00-05:00", "title": "Same", "emails": ["a@example.test"]},
            {"event_id": "series/two", "start_at": "2026-10-01T19:00:00-05:00", "title": "Same", "emails": ["a@example.test"]},
            {"event_id": "next/day", "start_at": "2026-10-02T01:00:00+02:00", "title": "UTC boundary", "emails": ["a@example.test"]},
        ]}))
        with mock.patch.object(matcher.time, "time", return_value=now.timestamp()):
            rows = matcher.read_dump(str(dump), 7)
        self.assertEqual([r[3] for r in rows], ["past", "upcoming", "past"])
        self.assertEqual([r[4] for r in rows], ["series/one", "series/two", "next/day"])

    def test_producer_preserves_same_title_events_and_stable_id_after_edit(self):
        fake_modules = {"EventKit": mock.Mock(), "Foundation": mock.Mock()}
        with mock.patch.dict(sys.modules, fake_modules):
            spec = importlib.util.spec_from_file_location("dump_producer", REPO / "tools/calendar-attendee-dump.py")
            producer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(producer)
        def event(identifier, start):
            ev = mock.Mock()
            ev.title.return_value = "Same"
            ev.calendarItemIdentifier.return_value = identifier
            ev.calendarItemExternalIdentifier.return_value = f"server-{identifier}"
            ev.calendar.return_value.calendarIdentifier.return_value = "calendar-1"
            ev.hasRecurrenceRules.return_value = False
            ev.occurrenceDate.return_value = None
            ev.startDate.return_value.timeIntervalSince1970.return_value = start
            ev.attendees.return_value = []
            ev.organizer.return_value.URL.return_value.resourceSpecifier.return_value = "a@example.test"
            return ev
        a, b = event("id-one", 1790874000), event("id-two", 1790877600)
        b.organizer.return_value.URL.return_value.resourceSpecifier.return_value = "b@example.test"
        store = fake_modules["EventKit"].EKEventStore.alloc.return_value.init.return_value
        store.eventsMatchingPredicate_.return_value = [a, b]
        producer.OUT = str(self.root / "produced.json")
        with mock.patch.object(producer, "request_access", return_value=True), redirect_stdout(io.StringIO()):
            self.assertEqual(producer.main(), 0)
        first = json.loads(pathlib.Path(producer.OUT).read_text())
        triage_spec = importlib.util.spec_from_file_location("triage_reader", REPO / "tools/calendar-triage-plan.py")
        triage = importlib.util.module_from_spec(triage_spec)
        triage_spec.loader.exec_module(triage)
        with mock.patch.object(triage, "ATTENDEES", producer.OUT):
            local = triage.load_local_attendees()
        ingest = {"summary": "Same", "starts_at": "2026-10-01T12:00:00-05:00"}
        # Legacy input and the actual new producer must return the same address.
        self.assertEqual(triage.emails_in(ingest, {"Same|2026-10-01": ["a@example.test"]}), ["a@example.test"])
        self.assertEqual(triage.emails_in(ingest, local), ["a@example.test"])
        ingest["starts_at"] = "2026-10-01T13:00:00-05:00"
        self.assertEqual(triage.emails_in(ingest, local), ["b@example.test"])
        pathlib.Path(producer.OUT).unlink()
        a.title.return_value = "Edited"
        with mock.patch.object(producer, "request_access", return_value=True), redirect_stdout(io.StringIO()):
            self.assertEqual(producer.main(), 0)
        second = json.loads(pathlib.Path(producer.OUT).read_text())
        self.assertEqual(len(first["events"]), 2)
        self.assertEqual(first["events"][0]["event_id"], second["events"][0]["event_id"])
        self.assertNotEqual(first["events"][0]["event_id"], first["events"][1]["event_id"])

        # The same series has a separate stable identity for each original recurrence slot.
        pathlib.Path(producer.OUT).unlink()
        a.hasRecurrenceRules.return_value = b.hasRecurrenceRules.return_value = True
        b.calendarItemIdentifier.return_value = "id-one"
        b.calendarItemExternalIdentifier.return_value = "server-id-one"
        a.occurrenceDate.return_value = mock.Mock()
        b.occurrenceDate.return_value = mock.Mock()
        a.occurrenceDate.return_value.timeIntervalSince1970.return_value = 1790874000
        b.occurrenceDate.return_value.timeIntervalSince1970.return_value = 1790960400
        with mock.patch.object(producer, "request_access", return_value=True), redirect_stdout(io.StringIO()):
            self.assertEqual(producer.main(), 0)
        recurring = json.loads(pathlib.Path(producer.OUT).read_text())["events"]
        self.assertNotEqual(recurring[0]["event_id"], recurring[1]["event_id"])

        # Detaching/rescheduling one recurrence can drop its recurrence rules
        # and change the local item ID; server UID and original slot persist.
        pathlib.Path(producer.OUT).unlink()
        a.hasRecurrenceRules.return_value = False
        a.calendarItemIdentifier.return_value = "detached-local-id"
        a.startDate.return_value.timeIntervalSince1970.return_value += 3600
        with mock.patch.object(producer, "request_access", return_value=True), redirect_stdout(io.StringIO()):
            self.assertEqual(producer.main(), 0)
        detached = json.loads(pathlib.Path(producer.OUT).read_text())["events"]
        self.assertEqual(recurring[0]["event_id"], detached[0]["event_id"])

    def test_snapshot_path_is_unchanged(self):
        emails, domains = matcher.load_record_contacts(
            [{"email": "z@same.example.test", "ref": "C-1", "name": "Client", "org": "Org"}])
        self.assertEqual(list(emails), ["z@same.example.test"])
        self.assertEqual(list(domains), ["same.example.test"])

    def test_all_day_feed_rows_do_not_abort_timed_triage(self):
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, REPO / path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        feed = load("calendar_feed", "bin/pull-gmail-calendar.py")
        triage = load("calendar_triage", "tools/calendar-triage-plan.py")
        ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:all-day
DTSTART;VALUE=DATE:20261001
SUMMARY:Same
END:VEVENT
BEGIN:VEVENT
UID:all-day-with-email
DTSTART;VALUE=DATE:20261001
SUMMARY:All day contact
ORGANIZER:mailto:embedded@example.test
END:VEVENT
BEGIN:VEVENT
UID:timed-one
DTSTART:20261001T170000Z
SUMMARY:Same
END:VEVENT
BEGIN:VEVENT
UID:timed-two
DTSTART:20261001T180000Z
SUMMARY:Same
END:VEVENT
END:VCALENDAR
"""
        payloads = [feed.normalize(row, "joe", "fixture.ics")
                    for row in feed.parse_events(ics)]
        self.assertTrue(payloads[0]["event"]["all_day"])
        self.assertEqual(payloads[0]["event"]["starts_at"], "2026-10-01")
        snapshot = {"schema": "calendar-events/v2", "events": [
            {"title": "Same", "start_at": "2026-10-01T12:00:00-05:00",
             "emails": ["first@example.test"]},
            {"title": "Same", "start_at": "2026-10-01T13:00:00-05:00",
             "emails": ["second@example.test"]},
        ]}
        snapshot_path = self.root / "triage-snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        database = mock.MagicMock()
        cursor = database.connect.return_value.__enter__.return_value.cursor.return_value
        cursor.fetchall.side_effect = [
            [(f"{name}@example.test", f"C-{i}", name, "client")
             for i, name in enumerate(("embedded", "first", "second"), 1)],
            [(str(i), row["external_id"], row["event"])
             for i, row in enumerate(payloads, 1)],
        ]
        output, error = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules, {"psycopg": database}), \
                mock.patch.dict(os.environ, {"DATABASE_URL": "fixture-only"}), \
                mock.patch.object(triage, "ATTENDEES", str(snapshot_path)), \
                redirect_stdout(output), redirect_stderr(error):
            triage.main()
        plan = json.loads(output.getvalue())
        self.assertEqual([row["item_id"] for row in plan["rejected"]], ["1"])
        self.assertEqual(plan["rejected"][0]["why"], "no attendee addresses at all")
        self.assertEqual([row["refs"] for row in plan["filed"]],
                         [["C-1"], ["C-2"], ["C-3"]])
        self.assertEqual(plan["left"], [])
        self.assertIn("total 4", error.getvalue())

    def test_all_day_handling_does_not_relax_invalid_or_timed_starts(self):
        spec = importlib.util.spec_from_file_location("calendar_triage", REPO / "tools/calendar-triage-plan.py")
        triage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(triage)
        snapshot = {"schema": "calendar-events/v2", "events": []}
        for start, all_day in (("2026-10-01T12:00:00", False),
                               ("2026-10-01T12:00:00", True),
                               ("2026-10-01", False),
                               ("2026-02-30", True)):
            with self.subTest(start=start, all_day=all_day), self.assertRaises(ValueError):
                triage.emails_in({"starts_at": start, "all_day": all_day}, snapshot)


if __name__ == "__main__":
    unittest.main()
