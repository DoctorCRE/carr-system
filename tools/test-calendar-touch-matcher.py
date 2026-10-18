#!/usr/bin/env python3
"""The calendar matcher reads record contacts from the record layer's export views.

Regression for 2026-10-07/08: the matcher read the OneDrive xlsx projections, and
when OneDrive evicted lead-registry.xlsx to an online-only placeholder every read
returned EDEADLK, openpyxl raised BadZipFile, and calendar capture failed two
days running. Contacts now come from v_export_clients / v_export_leads /
v_export_vendors; an unreachable view fails closed and nothing falls back to a
file. Earlier regression (2026-09-27): an empty contact book must refuse loudly.
Fixture rows only; example.test addresses.
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

REPO = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calendar_touch_matcher", REPO / "tools" / "calendar-touch-matcher.py")
assert spec is not None and spec.loader is not None
matcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matcher)


def live_views():
    return {
        "v_export_clients": [{"Client ID": "C-1", "Name": "Client One",
                              "Practice / Entity": "Practice A", "Email": "one@clinic-a.example.test"}],
        "v_export_leads": [{"Lead ID": "L-1", "Contact Name": "Lead Two",
                            "Practice": "Practice B", "Email": "two@gmail.com"}],
        "v_export_vendors": [],
    }


def reader_for(views):
    def read(view):
        value = views[view]
        if isinstance(value, BaseException):
            raise value
        cols = list(value[0]) if value else [
            *next(fields[1:] for fields in matcher.RECORD_VIEWS if fields[0] == view), "Email"]
        return cols, value
    return read


class RecordViewSchema(unittest.TestCase):
    def test_empty_view_with_wrong_columns_fails_closed(self):
        sys.path.insert(0, str(REPO))
        for malformed in ("v_export_clients", "v_export_leads", "v_export_vendors"):
            with self.subTest(view=malformed):
                views = live_views()
                views[malformed] = []
                columns = {
                    "v_export_clients": ["Client ID", "Name", "Practice / Entity", "Email"],
                    "v_export_leads": ["Lead ID", "Contact Name", "Practice", "Email"],
                    "v_export_vendors": ["ID", "Name", "Company", "Email"],
                }
                columns[malformed] = ["Email"]
                cursor = mock.MagicMock()
                def execute(sql):
                    view = sql.rsplit(" ", 1)[1]
                    cursor.description = [(name,) for name in columns[view]]
                    cursor.fetchall.return_value = [
                        tuple(row.get(name) for name in columns[view]) for row in views[view]]
                cursor.execute.side_effect = execute
                conn = mock.MagicMock()
                conn.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
                with mock.patch("exporters.common.connect", return_value=conn), \
                        self.assertRaises(matcher.NoRecordContacts) as caught:
                    matcher.load_record_contacts()
                self.assertIn(f"{malformed} (required columns missing)", str(caught.exception))


class RecordViews(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.views = live_views()
        patcher = mock.patch.object(matcher, "read_view", side_effect=lambda v: reader_for(self.views)(v))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_contacts_from_the_record_views(self):
        by_email, by_domain = matcher.load_record_contacts()
        self.assertEqual(sorted(by_email), ["one@clinic-a.example.test", "two@gmail.com"])
        self.assertEqual(by_email["one@clinic-a.example.test"], "C-1 / Client One")
        # Freemail never becomes a domain match.
        self.assertEqual(sorted(by_domain), ["clinic-a.example.test"])

    def test_vendor_view_is_an_exact_contact_source(self):
        self.views["v_export_vendors"] = [
            {"ID": "V-1", "Name": "Synthetic Vendor", "Company": "Synthetic Service",
             "Email": "vendor@service.example.test", "_out_of_market": False},
            {"ID": "V-2", "Name": "Far Vendor", "Company": "Elsewhere",
             "Email": "far@elsewhere.example.test", "_out_of_market": True}]
        emails, domains = matcher.load_record_contacts()
        self.assertEqual(emails.get("vendor@service.example.test"), "V-1 / Synthetic Vendor")
        self.assertIn("service.example.test", domains)
        # Out-of-market vendors never reach the Vendors sheet, so not here either.
        self.assertNotIn("far@elsewhere.example.test", emails)

    def test_any_unreachable_view_fails_closed(self):
        for view in ("v_export_clients", "v_export_leads", "v_export_vendors"):
            for failure in (OSError(11, "Resource deadlock avoided"), RuntimeError("connection refused"),
                            SystemExit("no CARR_DB_EXPORTER_URL")):
                self.views = live_views()
                self.views[view] = failure
                with self.subTest(view=view, failure=type(failure).__name__), \
                        self.assertRaises(matcher.NoRecordContacts) as caught:
                    matcher.load_record_contacts()
                self.assertIn(view, str(caught.exception))
                self.assertIn("unreachable", str(caught.exception))

    def test_view_missing_required_columns_fails_closed(self):
        self.views["v_export_leads"] = [{"Email": "two@gmail.com"}]
        with self.assertRaises(matcher.NoRecordContacts) as caught:
            matcher.load_record_contacts()
        self.assertIn("v_export_leads (required columns missing)", str(caught.exception))

    def test_never_falls_back_to_the_xlsx_projection(self):
        # A perfectly readable projection on disk must not rescue an unreachable view.
        source = (REPO / "tools" / "calendar-touch-matcher.py").read_text(encoding="utf-8")
        self.assertNotIn("openpyxl", source)
        self.assertNotIn(".xlsx", source)
        self.assertNotIn("EXPORT_HOME", source)
        self.views["v_export_clients"] = OSError(11, "Resource deadlock avoided")
        with mock.patch.dict(os.environ, {"CARR_EXPORT_HOME": str(self.root)}), \
                self.assertRaises(matcher.NoRecordContacts):
            matcher.load_record_contacts()

    def test_views_and_columns_match_the_exporters(self):
        sys.path.insert(0, str(REPO))
        from exporters import targets
        text = (REPO / "exporters" / "targets.py").read_text(encoding="utf-8")
        columns = {"v_export_clients": targets.ROSTER_COLS, "v_export_leads": targets.REGISTRY_COLS,
                   "v_export_vendors": targets.VENDORS_COLS}
        for view, id_col, name_col, org_col in matcher.RECORD_VIEWS:
            with self.subTest(view=view):
                self.assertIn(f"from {view}", text)
                self.assertLessEqual({id_col, name_col, org_col, "Email"}, set(columns[view]))

    def test_unreachable_view_message_carries_no_address_or_dsn(self):
        self.views["v_export_clients"] = RuntimeError(
            "postgresql://carr_exporter:secret@host/db rejected one@clinic-a.example.test")  # ci-secret-scan: allow (synthetic fixture)
        with self.assertRaises(matcher.NoRecordContacts) as caught:
            matcher.load_record_contacts()
        message = str(caught.exception)
        self.assertNotIn("postgresql://", message)
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", message))

    def test_empty_book_refuses_loudly_without_addresses(self):
        self.views = {"v_export_clients": [], "v_export_leads": [], "v_export_vendors": []}
        dump = self.root / "dump.json"
        dump.write_text('{"Meeting|2026-09-25": ["someone@else.example.test"]}', encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["matcher", "7", "--json", "--from-dump", str(dump)]), \
                redirect_stderr(err):
            code = matcher.main()
        self.assertEqual(code, 5)
        self.assertIn("FATAL: record contacts: none loaded", err.getvalue())
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", err.getvalue()))

    def test_newest_touch_and_nearest_future_event_are_reported(self):
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
        with mock.patch.object(sys, "argv", ["matcher", "7", "--json", "--from-dump", str(dump)]), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(matcher.main(), 0)
        proposal = json.loads(output.getvalue())["exact"][0]
        self.assertEqual(proposal["last_seen"], latest)
        self.assertEqual([e["day"] for e in proposal["events"]], [latest, old])
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["matcher", "7", "--from-dump", str(dump)]), \
                redirect_stdout(output):
            self.assertEqual(matcher.main(), 0)
        self.assertIn("Near synthetic meeting", output.getvalue())
        self.assertNotIn("Far synthetic meeting", output.getvalue())

    def test_nearest_same_day_future_meeting_uses_start_instant(self):
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
