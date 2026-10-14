#!/usr/bin/env python3
"""Focused safety tests for the private-plan invoice staging fixture installer."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import io
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("staging_fixtures", ROOT / "tools/staging-fixtures.py")
fixture = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(fixture)

APP_SHA = "57df4bf973c598f39765c3eefa7eb6bde758e59f"
CARR_SHA = "5ddaa8f39507de3f3343c5cc5177d3d07168e9eb"
RUN = "qa_run-20261014"
PARTY = fixture.FIXTURE_ID
DEAL = uuid.UUID("aaf041e9-53a5-4260-a79f-0f679ca4747d")


def plan(**changes):
    value = {
        "schema": "doctorcre-staging-records-plan.v1",
        "origin": "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev",
        "release": {"source_commit": APP_SHA, "carr_source_commit": CARR_SHA},
        "state": "complete", "run": RUN, "current_step": "readback", "inflight": None,
        "keys": {}, "receipts": {},
        "observed_at": "2026-10-14T11:15:00.000Z",
        "names": {"invoice": f"Synthetic staging QA {RUN} closed deal"},
        "records": {
            "party": {"id": str(PARTY), "name": "Synthetic Staging Fixture"},
            "client": {"id": "a606d644-26f4-4cd2-a7d6-b762a0f00e5f", "name": "Synthetic Staging Fixture"},
            "invoice": {"deal_id": str(DEAL), "name": f"Synthetic staging QA {RUN} closed deal"},
        },
    }
    value.update(changes)
    return value


class Cursor:
    def __init__(self, db): self.db, self.result = db, None
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, args=None):
        self.db.sql.append((sql, args))
        low = " ".join(sql.lower().split())
        if "select session_user,current_user" in low: self.result = ("neondb_owner", "neondb_owner")
        elif "select id from public.actor where slug='system'" in low: self.result = ("actor-system",)
        elif "from public.deal d" in low: self.result = self.db.deal
        elif "from public.commission" in low and "deal_id=%s" in low:
            self.result = [self.db.existing_commission] if self.db.existing_commission else []
        elif "insert into public.commission" in low:
            self.db.insert_count += 1
            if self.db.conflict is None:
                self.db.commission = (args[0], args[1], args[2], "invoiced", args[3], None, 1, args[4])
                self.result = (args[0],)
            else:
                self.result = None
        elif "from public.commission" in low: self.result = self.db.conflict or self.db.commission
        elif "from public.v_invoice_tracker" in low: self.result = self.db.tracker
        elif "select current_date" in low: self.result = (fixture.dt.date(2026, 10, 14),)
        elif "insert into public.party" in low: self.result = None
        elif "select count(*) from public.party" in low: self.result = (1,)
        else: raise AssertionError(f"unexpected SQL shape: {low}")
    def fetchone(self): return self.result
    def fetchall(self): return self.result


class Conn:
    def __init__(self, *, commission=None, conflict=None, tracker=None, deal=None):
        self.sql, self.insert_count, self.commission = [], 0, commission
        self.conflict, self.tracker, self.existing_commission = conflict, tracker, commission
        self.deal = deal or (str(DEAL), "Synthetic staging QA qa_run-20261014 closed deal",
                             "closed", None, "joe", "a606d644-26f4-4cd2-a7d6-b762a0f00e5f",
                             None, str(PARTY), "Synthetic Staging Fixture", None, None)
        self.commits, self.rollbacks = 0, 0
    def cursor(self): return Cursor(self)
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


class PlanTests(unittest.TestCase):
    def test_loads_private_plan_and_accepts_documented_run_id_syntax(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"
            path.write_text(json.dumps(plan()))
            path.chmod(0o600)
            loaded = fixture.load_private_plan(path, APP_SHA, CARR_SHA)
            self.assertEqual(loaded["run"], RUN)
            self.assertEqual(loaded["records"]["invoice"]["deal_id"], str(DEAL))

    def test_refuses_symlink_and_non_private_plan(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw); target = root / "real.json"
            target.write_text(json.dumps(plan())); target.chmod(0o600)
            link = root / "link.json"; link.symlink_to(target)
            with self.assertRaises(fixture.FixtureRefusal): fixture.load_private_plan(link, APP_SHA, CARR_SHA)
            target.chmod(0o644)
            with self.assertRaises(fixture.FixtureRefusal): fixture.load_private_plan(target, APP_SHA, CARR_SHA)

    def test_refuses_stale_pins_partial_plan_and_bad_run_id(self):
        for changes in ({"release": {"source_commit": APP_SHA, "carr_source_commit": "c06bdf2788fb69daefb59364ea4fd798fa94f9a4"}},
                        {"state": "pending"}, {"inflight": {"name": "x"}}, {"run": "bad/run"},
                        {"observed_at": "2026-02-30T00:00:00Z"}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as raw:
                path = pathlib.Path(raw) / "plan.json"; path.write_text(json.dumps(plan(**changes))); path.chmod(0o600)
                with self.assertRaises(fixture.FixtureRefusal): fixture.load_private_plan(path, APP_SHA, CARR_SHA)

    def test_dry_run_plan_is_zero_write(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"
            path.write_text(json.dumps(plan())); path.chmod(0o600)
            args = ["staging-fixtures.py", "--invoice-plan", str(path),
                    "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA]
            output = io.StringIO()
            with patch.object(sys, "argv", args), patch.object(sys, "stdout", output), \
                    patch.object(fixture, "validate_provider_binding", side_effect=AssertionError("dry run called provider")):
                self.assertEqual(fixture.main(), 0)
            self.assertEqual(output.getvalue(), "staging-fixtures: PLAN invoice_rows=1 mutated=false\n")

    def test_production_target_refuses_before_provider_or_database_connect(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"
            path.write_text(json.dumps(plan())); path.chmod(0o600)
            args = ["staging-fixtures.py", "--apply", "--invoice-plan", str(path),
                    "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA]
            environment = {
                "DATABASE_URL": "postgresql://neondb_owner:dummy@ep-prod.neon.tech/neondb",
                "CARR_STAGING_REPLACEMENT_PROJECT_ID": fixture.PRODUCTION_PROJECT_ID,
                "CARR_STAGING_REPLACEMENT_ENDPOINT_ID": "ep-prod",
            }
            connect = Mock(side_effect=AssertionError("production target reached database connect"))
            stderr = io.StringIO()
            with patch.object(sys, "argv", args), patch.dict(fixture.os.environ, environment, clear=True), \
                    patch.object(sys, "stderr", stderr), \
                    patch.object(fixture, "validate_provider_binding", side_effect=AssertionError("production target reached provider")), \
                    patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
                self.assertEqual(fixture.main(), 2)
            self.assertEqual(connect.call_count, 0)
            self.assertIn("replacement project identity is absent or forbidden", stderr.getvalue())

    def test_insert_is_exact_idempotent_and_tracker_readback_is_required(self):
        value = plan()
        expected = fixture.invoice_commission_values(value)
        row = (expected[0], expected[1], expected[2], "invoiced", expected[3], None, 1, "actor-system")
        tracker = (DEAL, expected[0], expected[2], "invoiced", expected[3], None)
        db = Conn(tracker=tracker)
        self.assertEqual(fixture.install_invoice_fixture(db, value), 1)
        self.assertEqual(db.insert_count, 1)
        self.assertEqual(db.commits, 1)
        replay = Conn(commission=row, tracker=tracker)
        self.assertEqual(fixture.install_invoice_fixture(replay, value), 0)
        self.assertEqual(replay.insert_count, 0)
        self.assertEqual(replay.commits, 1)
        mismatch = Conn(tracker=(DEAL, expected[0], expected[2], "received", expected[3], "2026-10-14"))
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_invoice_fixture(mismatch, value)
        self.assertEqual(mismatch.commits, 0)
        self.assertEqual(mismatch.rollbacks, 1)

    def test_never_rewrites_paid_or_conflicting_commission(self):
        values = fixture.invoice_commission_values(plan())
        paid = (values[0], values[1], values[2], "received", values[3], "2026-10-14", 2, "actor-system")
        db = Conn(conflict=paid, tracker=None)
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_invoice_fixture(db, plan())
        self.assertEqual(db.insert_count, 1)  # ON CONFLICT DO NOTHING; no UPDATE path exists.
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_refuses_wrong_party_or_open_deal_before_commission_insert(self):
        for deal in ((str(DEAL), "wrong", "closed", None, "joe", "a606d644-26f4-4cd2-a7d6-b762a0f00e5f", None, str(PARTY), "Synthetic Staging Fixture", None, None),
                     (str(DEAL), "Synthetic staging QA qa_run-20261014 closed deal", "pending", None, "joe", "a606d644-26f4-4cd2-a7d6-b762a0f00e5f", None, str(PARTY), "Synthetic Staging Fixture", None, None),
                     (str(DEAL), "Synthetic staging QA qa_run-20261014 closed deal", "closed", "lost", "joe", "a606d644-26f4-4cd2-a7d6-b762a0f00e5f", None, str(PARTY), "Synthetic Staging Fixture", None, None),
                     (str(DEAL), "Synthetic staging QA qa_run-20261014 closed deal", "closed", None, "dell", "a606d644-26f4-4cd2-a7d6-b762a0f00e5f", None, str(PARTY), "Synthetic Staging Fixture", None, None),
                     (str(DEAL), "Synthetic staging QA qa_run-20261014 closed deal", "closed", None, "joe", "a606d644-26f4-4cd2-a7d6-b762a0f00e5f", None, str(uuid.uuid4()), "wrong", None, None)):
            db = Conn(deal=deal)
            with self.assertRaises(fixture.FixtureRefusal): fixture.install_invoice_fixture(db, plan())
            self.assertEqual(db.insert_count, 0)


if __name__ == "__main__":
    unittest.main()
