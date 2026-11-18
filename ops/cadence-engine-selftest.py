#!/usr/bin/env python3
"""Exercise the nightly cadence entry point with synthetic database and door replies."""
import contextlib
import io
import json
import os
from datetime import date
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.engine_mail import Reply

with patch.dict(sys.modules, {"psycopg": types.ModuleType("psycopg")}):
    from pipelines import cadence_engine as cadence


def subject(party, *, status="engaged", owner="owner-a", lease=None, old_ids=()):
    return dict(id=party, name="Example subject", ref=None, client_status=status,
                owner="partner" if owner else None, owner_party_id=owner, estimated_lease_on=lease,
                old_ids=[{"value": value} for value in old_ids])


def done(step, party, owner="owner-b", at="2026-01-10T12:00:00Z"):
    return dict(id=step, party_id=party, owner_party_id=owner, done_at=at, description="Initial step")


def rule(key, lane, kind, trigger, interval, scope=None):
    return key, lane, kind, trigger, interval, "Follow up with {subject}", True, scope


class CadenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        self.report = self.home / "cadence.md"
        self.config = self.home / ".config/carr/outlook.json"
        self.rules = []
        self.spawns = []
        self.sql = []
        self.items = {}
        self.calls = []
        self.receipts = {}
        self.failures = []
        self.refuse_read = None
        self.legacy_rows = {}

    def execute(self, sql, params=()):
        statement = " ".join(sql.split())
        self.sql.append(statement)
        if "from cadence_rule" in statement:
            rows = self.rules
        elif "from system_config" in statement:
            rows = []
        elif "from event" in statement:
            rows = [(key,) for key in self.spawns]
        elif not self.config.exists():
            rows = next((rows for fragment, rows in self.legacy_rows.items() if fragment in statement), [])
        else:
            self.fail(f"Unexpected old database access: {statement}")
        return types.SimpleNamespace(fetchone=lambda: next(iter(rows), None), fetchall=lambda: rows)

    def door(self, method, path, body):
        self.calls.append((method, path, body))
        if method == "GET":
            if self.refuse_read:
                return self.refuse_read
            self.assertEqual(urlsplit(path).path, "/jobs/cadence")
            query = {key: values[0] for key, values in parse_qs(urlsplit(path).query).items()}
            rows = self.items.get((query["collection"], query.get("status")), [])
            page = [item for item in rows if item["id"] > query.get("after", "")][:1]
            more = page and page[-1] != rows[-1]
            return Reply(200, {"items": page, "next_cursor": page[-1]["id"] if more else None})

        self.assertEqual((method, path), ("POST", "/jobs/next-steps"))
        request = json.loads(body)
        key = request["idempotency_key"]
        self.assertEqual(UUID(key).version, 5)
        if self.failures:
            refusal, saved = self.failures.pop(0)
            if saved:
                self.receipts[key] = request
            return refusal
        if key in self.receipts:
            if request != self.receipts[key]:
                return Reply(409, {"error": "key_reuse"})
            return Reply(201, {"next_step_id": "new-step", "change_id": "first-change", "replayed": True})
        self.receipts[key] = request
        return Reply(201, {"next_step_id": "new-step", "change_id": "first-change"})

    def run_cadence(self, day="2026-01-15", apply=True):
        transaction = types.SimpleNamespace(execute=self.execute, commit=lambda: None)
        argv = ["cadence_engine.py", "--today", day] + (["--apply"] if apply else [])
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.object(cadence, "OUT", self.home), patch.object(cadence, "REPORT", self.report),
              patch.object(Path, "home", return_value=self.home),
              patch.dict(os.environ, {"CARR_DB_JOBS_URL": "synthetic-db"}, clear=True),
              patch.object(sys, "argv", argv),
              patch.object(cadence.psycopg, "connect", return_value=contextlib.nullcontext(transaction), create=True),
              patch.object(cadence.outlook_settings, "load_settings", return_value=types.SimpleNamespace(
                  engine_cadence=True, engine_credentials=self.home / "access.env")),
              patch("lib.engine_mail.connect", return_value=self.door) as connect,
              contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr)):
            result = cadence.main()
        if self.config.exists():
            connect.assert_called_once_with(self.home / "access.env", job="cadence")
        else:
            connect.assert_not_called()
        return result, stdout.getvalue() + stderr.getvalue()

    def switch_on(self):
        self.config.parent.mkdir(parents=True)
        self.config.touch()

    @property
    def posts(self):
        return [json.loads(body) for method, _, body in self.calls if method == "POST"]

    def test_switch_off_keeps_old_queries_and_no_http(self):
        self.rules = [rule("l-1", "lease_event", "lead", "on_date", 30)]
        self.legacy_rows = {
            "from actor": [("automation",)],
            "join party p on p.id = l.party_id": [("old-lead", None, "Example Lead", None, "owner-a")],
            "from lead where": [("old-lead", date(2026, 2, 1))],
            "insert into next_action": [("legacy-step",)],
        }
        json_types = types.SimpleNamespace(json=types.SimpleNamespace(Jsonb=dict))
        with patch.object(cadence.psycopg, "types", json_types, create=True):
            self.assertEqual(self.run_cadence()[0], 0)
        self.assertTrue(any("from next_action" in sql for sql in self.sql))
        self.assertTrue(any("insert into next_action" in sql for sql in self.sql))
        self.assertTrue(any("insert into event" in sql for sql in self.sql))
        self.assertEqual(self.calls, [])

    def test_switch_on_pages_and_keeps_cadence_guards(self):
        self.switch_on()
        self.rules = [rule("n-1", "nurture", "client", "on_complete", 10, ["engaged"]),
                      rule("v-1", "maintain", "vendor", "on_complete", 20),
                      rule("l-1", "lease_event", "lead", "on_date", 30)]
        self.items = {
            ("clients", None): [subject("a"), subject("b", status="cold"), subject("c"), subject("d")],
            ("vendors", None): [subject("v", owner=None)],
            ("leads", None): [subject("a", lease="2026-02-01"), subject("c", owner="owner-b", lease="2026-02-01"),
                              subject("e", status="paused", lease="2026-02-01")],
            ("next_steps", "open"): [dict(id="held", party_id="c", owner_party_id="owner-b")],
            ("next_steps", "done"): [done("1", "a"), done("2", "b"), done("3", "c"),
                                      done("4", "v"), done("5", "d", at=None)],
        }
        self.assertEqual(self.run_cadence(apply=False)[0], 0)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.run_cadence()[0], 0)
        self.assertEqual([(p["party_id"], p["due_on"], p["owner_party_id"]) for p in self.posts],
                         [("a", "2026-01-20", "owner-b"), ("a", "2026-01-15", "owner-a")])
        self.assertIn("skipped 2 per the abundance rule", self.report.read_text())
        self.assertIn("no owner on record", self.report.read_text())

    def test_legacy_receipts_survive_cutover(self):
        self.switch_on()
        self.rules = [rule("n-1", "nurture", "client", "on_complete", 10),
                      rule("l-1", "lease_event", "lead", "on_date", 30)]
        self.items = {("clients", None): [subject("a")],
                      ("leads", None): [subject("a", lease="2026-02-01", old_ids=("old-lead",))],
                      ("next_steps", "done"): [done("1", "a")]}
        self.spawns = ["cadence:n-1:1", "cadence:l-1:date:old-lead:2026-02-01"]
        self.assertEqual(self.run_cadence()[0], 0)
        self.assertEqual(self.posts, [])

    def test_retry_and_a_later_lease_run_converge(self):
        self.switch_on()
        self.rules = [rule("l-1", "lease_event", "lead", "on_date", 30)]
        self.items[("leads", None)] = [subject("a", lease="2026-02-01")]
        self.failures = [(Reply(503, {"error": "database_unavailable"}), True)]
        result, output = self.run_cadence()
        self.assertEqual(result, 0)
        self.assertEqual(self.posts[0], self.posts[1])
        self.assertIn("spawned 0", output)
        self.assertEqual(self.run_cadence("2026-01-16")[0], 0)
        self.assertEqual(len(self.receipts), 1)

    def test_final_refusals_never_fall_back_to_old_writes(self):
        self.switch_on()
        self.rules = [rule("l-1", "lease_event", "lead", "on_date", 30)]
        self.items[("leads", None)] = [subject("a", lease="2026-02-01")]
        self.refuse_read = Reply(403, {"error": "unknown_job"})
        result, output = self.run_cadence()
        self.assertEqual(result, 1)
        self.assertIn("403 unknown_job", output)
        self.assertEqual(len(self.calls), 1)
        self.refuse_read = None
        self.failures = [(Reply(422, {"error": "database_refused_the_statement"}), False)]
        result, output = self.run_cadence()
        self.assertEqual(result, 1)
        self.assertIn("422 database_refused_the_statement", self.report.read_text() + output)
        self.assertEqual(len(self.posts), 1)


if __name__ == "__main__":
    unittest.main()
