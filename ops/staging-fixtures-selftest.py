#!/usr/bin/env python3
"""Focused safety tests for the private-plan invoice staging fixture installer."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import pathlib
import io
import subprocess
import sys
import tempfile
import unittest
import uuid
import datetime as dt
import copy
import re
import ast
from types import SimpleNamespace
from unittest.mock import Mock, patch

tempfile.tempdir = str(pathlib.Path(tempfile.gettempdir()).resolve())

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("staging_fixtures", ROOT / "tools/staging-fixtures.py")
assert SPEC and SPEC.loader
fixture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture
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
        if "set transaction read only" in low: self.result = None
        elif "select session_user,current_user,current_database()" in low:
            self.result = ("neondb_owner", "neondb_owner", "neondb")
        elif "select session_user,current_user" in low: self.result = ("neondb_owner", "neondb_owner")
        elif "select id from public.actor where slug='system'" in low: self.result = ("actor-system",)
        elif "from public.deal d" in low: self.result = self.db.deal
        elif "from public.commission" in low and "deal_id=%s" in low:
            self.result = [self.db.existing_commission] if self.db.existing_commission else []
        elif "insert into public.commission" in low:
            self.db.insert_count += 1
            if self.db.conflict is None:
                self.db.commission = (args[0], args[1], args[2], args[3], args[4], None, 1, args[5], args[6])
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
    def __init__(self, *, commission=None, conflict=None, tracker=None, deal=None, events=None):
        self.sql, self.insert_count, self.commission = [], 0, commission
        self.conflict, self.tracker, self.existing_commission = conflict, tracker, commission
        self.events = events
        self.deal = deal or (str(DEAL), "Synthetic staging QA qa_run-20261014 closed deal",
                             "closed", None, "joe", "a606d644-26f4-4cd2-a7d6-b762a0f00e5f",
                             None, str(PARTY), "Synthetic Staging Fixture", None, None)
        self.commits, self.rollbacks = 0, 0
    def cursor(self): return Cursor(self)
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def commit(self):
        self.commits += 1
        if self.events is not None: self.events.append("commit")
    def rollback(self):
        self.rollbacks += 1
        if self.events is not None: self.events.append("rollback")


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

    def test_refuses_hardlinked_plan_before_provider_or_database_effects(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            path, alias = root / "plan.json", root / "plan-alias.json"
            path.write_text(json.dumps(plan())); path.chmod(0o600)
            os.link(path, alias)
            proof_parent = root / "proofs"
            proof_parent.mkdir(mode=0o700); proof_parent.chmod(0o700)
            args = ["staging-fixtures.py", "--apply", "--invoice-plan", str(path),
                    "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA,
                    "--proof-file", str(proof_parent / "fixture.json")]
            provider = Mock(side_effect=AssertionError("linked plan reached provider"))
            connect = Mock(side_effect=AssertionError("linked plan reached database"))
            stderr = io.StringIO()
            environment = {"DATABASE_URL": "postgresql://neondb_owner:dummy@ep-test.neon.tech/neondb",
                           "CARR_STAGING_REPLACEMENT_PROJECT_ID": "prj-test",
                           "CARR_STAGING_REPLACEMENT_ENDPOINT_ID": "ep-test"}
            with patch.object(sys, "argv", args), patch.object(sys, "stderr", stderr), \
                    patch.dict(fixture.os.environ, environment, clear=True), \
                    patch.object(fixture, "validate_provider_binding", provider), \
                    patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
                self.assertEqual(fixture.main(), 2)
            self.assertEqual(provider.call_count, 0)
            self.assertEqual(connect.call_count, 0)
            self.assertIn("invoice plan", stderr.getvalue())

    def test_fifo_plan_refuses_without_blocking(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.fifo"
            os.mkfifo(path, 0o600)
            command = [sys.executable, str(ROOT / "tools/staging-fixtures.py"),
                       "--invoice-plan", str(path), "--expected-app-sha", APP_SHA,
                       "--expected-carr-sha", CARR_SHA]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=2)
            except subprocess.TimeoutExpired as exc:
                self.fail(f"opening a private FIFO blocked instead of refusing: {exc}")
            self.assertEqual(result.returncode, 2)
            self.assertIn("invoice plan must be an owned private regular file", result.stderr)

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
            for aged in (False, True):
                args = ["staging-fixtures.py", "--invoice-plan", str(path),
                        "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA]
                if aged: args.append("--aged-invoice")
                output = io.StringIO()
                with patch.object(sys, "argv", args), patch.object(sys, "stdout", output), \
                        patch.object(fixture, "validate_provider_binding", side_effect=AssertionError("dry run called provider")):
                    self.assertEqual(fixture.main(), 0)
                expected = "staging-fixtures: PLAN invoice_rows=1 mutated=false aged=true\n" if aged \
                    else "staging-fixtures: PLAN invoice_rows=1 mutated=false\n"
                self.assertEqual(output.getvalue(), expected)

    def test_production_target_refuses_before_provider_or_database_connect(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"
            path.write_text(json.dumps(plan())); path.chmod(0o600)
            args = ["staging-fixtures.py", "--apply", "--invoice-plan", str(path),
                    "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA,
                    "--proof-file", str(path.parent / "proof.json")]
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
        row = (expected.commission_id, expected.deal_id, expected.gross_amount, expected.status,
               expected.invoiced_on, None, 1, "actor-system", expected.due_on)
        tracker = (DEAL, expected.commission_id, expected.gross_amount, expected.status,
                   expected.invoiced_on, None, expected.due_on)
        db = Conn(tracker=tracker)
        self.assertEqual(fixture.install_invoice_fixture(db, value), 1)
        self.assertEqual(db.insert_count, 1)
        self.assertEqual(db.commits, 1)
        replay = Conn(commission=row, tracker=tracker)
        self.assertEqual(fixture.install_invoice_fixture(replay, value), 0)
        self.assertEqual(replay.insert_count, 0)
        self.assertEqual(replay.commits, 1)
        mismatch = Conn(tracker=(DEAL, expected.commission_id, expected.gross_amount,
                                 "received", expected.invoiced_on, "2026-10-14", expected.due_on))
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_invoice_fixture(mismatch, value)
        self.assertEqual(mismatch.commits, 0)
        self.assertEqual(mismatch.rollbacks, 1)

    def test_aged_invoice_dates_are_bounded_from_canonical_observation(self):
        ordinary = fixture.invoice_commission_values(plan())
        aged = fixture.invoice_commission_values(plan(), aged=True)
        self.assertEqual(ordinary.invoiced_on, dt.date(2026, 10, 14))
        self.assertIsNone(ordinary.due_on)
        self.assertEqual(aged.invoiced_on, dt.date(2026, 8, 30))
        self.assertEqual(aged.due_on, dt.date(2026, 9, 29))
        self.assertEqual((aged.observation_date - aged.invoiced_on).days, 45)
        self.assertEqual((aged.observation_date - aged.due_on).days, 15)

    def test_aged_mode_has_exact_due_on_collision_and_tracker_readback(self):
        value = plan()
        expected = fixture.invoice_commission_values(value, aged=True)
        row = (expected.commission_id, expected.deal_id, expected.gross_amount, "invoiced",
               expected.invoiced_on, None, 1, "actor-system", expected.due_on)
        tracker = (DEAL, expected.commission_id, expected.gross_amount, "invoiced",
                   expected.invoiced_on, None, expected.due_on)
        db = Conn(tracker=tracker)
        self.assertEqual(fixture.install_invoice_fixture(db, value, aged=True), 1)
        insert = next(sql for sql, _ in db.sql if "insert into public.commission" in sql.lower())
        self.assertIn("due_on", insert.lower())
        replay = Conn(commission=row, tracker=tracker)
        self.assertEqual(fixture.install_invoice_fixture(replay, value, aged=True), 0)
        self.assertEqual(replay.insert_count, 0)
        paid = (*row[:3], "received", expected.invoiced_on, expected.observation_date, 2,
                "actor-system", expected.due_on)
        with self.assertRaises(fixture.FixtureRefusal):
            fixture.install_invoice_fixture(Conn(conflict=paid, tracker=tracker), value, aged=True)

    def test_aged_mode_refuses_future_recorded_observation_date(self):
        value = plan(observed_at="2026-10-15T00:00:00.000Z")
        db = Conn()
        with self.assertRaises(fixture.FixtureRefusal):
            fixture.install_invoice_fixture(db, value, aged=True)
        self.assertEqual(db.insert_count, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_proof_is_required_for_invoice_apply_but_not_dry_run_or_party_mode(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"
            path.write_text(json.dumps(plan())); path.chmod(0o600)
            args = ["staging-fixtures.py", "--apply", "--invoice-plan", str(path),
                    "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA]
            with patch.object(sys, "argv", args), self.assertRaises(SystemExit) as raised:
                fixture.main()
            self.assertEqual(raised.exception.code, 2)

    def test_proof_publication_is_private_atomic_exact_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = pathlib.Path(raw) / "proofs"
            parent.mkdir(mode=0o700); parent.chmod(0o700)
            path = parent / "fixture.json"
            proof = {"schema": "doctorcre-staging-fixture-proof.v1", "fixture_run": RUN}
            self.assertEqual(fixture.publish_private_proof(path, proof), "created")
            self.assertEqual(path.stat().st_uid, os.geteuid())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            before = path.read_bytes()
            self.assertEqual(fixture.publish_private_proof(path, proof), "already-exact")
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(fixture.FixtureRefusal):
                fixture.publish_private_proof(path, {"schema": "different"})
            self.assertEqual(path.read_bytes(), before)

    def test_linked_existing_proof_is_refused_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = pathlib.Path(raw) / "proofs"
            parent.mkdir(mode=0o700); parent.chmod(0o700)
            path, alias = parent / "fixture.json", parent / "proof-alias.json"
            proof = {"schema": fixture.PROOF_SCHEMA, "fixture_run": RUN}
            self.assertEqual(fixture.publish_private_proof(path, proof), "created")
            before = path.read_bytes()
            os.link(path, alias)
            with self.assertRaises(fixture.FixtureRefusal):
                fixture.publish_private_proof(path, proof)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(alias.read_bytes(), before)
            self.assertEqual(path.stat().st_ino, alias.stat().st_ino)

    def test_proof_refuses_unprivate_parent_symlink_and_oversized_content(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = pathlib.Path(raw) / "proofs"
            parent.mkdir(mode=0o755); parent.chmod(0o755)
            with self.assertRaises(fixture.FixtureRefusal):
                fixture.publish_private_proof(parent / "fixture.json", {"schema": fixture.PROOF_SCHEMA})
            parent.chmod(0o700)
            outside = pathlib.Path(raw) / "outside.json"
            outside.write_text("private"); outside.chmod(0o600)
            link = parent / "link.json"; link.symlink_to(outside)
            with self.assertRaises(fixture.FixtureRefusal):
                fixture.publish_private_proof(link, {"schema": fixture.PROOF_SCHEMA})
            with self.assertRaises(fixture.FixtureRefusal):
                fixture.publish_private_proof(parent / "large.json", {"data": "x" * fixture.MAX_PROOF_BYTES})

    def test_main_commits_then_independently_reads_before_proof_publication(self):
        value = plan()
        values = fixture.invoice_commission_values(value)
        row = (values.commission_id, values.deal_id, values.gross_amount, values.status,
               values.invoiced_on, None, 1, "actor-system", values.due_on)
        tracker = (DEAL, values.commission_id, values.gross_amount, values.status,
                   values.invoiced_on, None, values.due_on)
        events = []
        first = Conn(tracker=tracker, events=events)
        second = Conn(commission=row, tracker=tracker, events=events)
        target = {"project": {"id": "replacement"}, "branch": {"id": "branch"},
                  "endpoint": {"id": "ep-staging"}, "database": "neondb"}
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            plan_path, proof_path = root / "plan.json", root / "proofs" / "fixture.json"
            plan_path.write_text(json.dumps(value)); plan_path.chmod(0o600)
            proof_path.parent.mkdir(mode=0o700); proof_path.parent.chmod(0o700)
            args = ["staging-fixtures.py", "--apply", "--invoice-plan", str(plan_path),
                    "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA,
                    "--proof-file", str(proof_path)]
            def bind(*_args, **_kwargs):
                events.append("provider")
                return target
            def connect(_dsn):
                events.append("connect")
                return first if events.count("connect") == 1 else second
            def publish(path, proof):
                events.append("publish")
                self.assertEqual(path, proof_path)
                self.assertEqual(proof["schema"], fixture.PROOF_SCHEMA)
                self.assertEqual(proof["fixture_run"], RUN)
                self.assertLess(events.index("commit"), events.index("rollback"))
                self.assertLess(events.index("rollback"), events.index("publish"))
                return "created"
            stderr = io.StringIO()
            with patch.object(sys, "argv", args), patch.dict(fixture.os.environ, {
                "DATABASE_URL": "postgresql://neondb_owner:dummy@ep-staging.neon.tech/neondb",
                "CARR_STAGING_REPLACEMENT_PROJECT_ID": "replacement",
                "CARR_STAGING_REPLACEMENT_ENDPOINT_ID": "ep-staging",
            }, clear=True), patch.object(sys, "stderr", stderr), \
                    patch.object(fixture, "validate_provider_binding", side_effect=bind), \
                    patch.object(fixture, "publish_private_proof", side_effect=publish), \
                    patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
                self.assertEqual(fixture.main(), 0)
            self.assertEqual(events, ["provider", "connect", "commit", "connect", "rollback", "publish"])

    def test_failed_independent_readback_or_publish_never_claims_proof(self):
        value = plan()
        values = fixture.invoice_commission_values(value)
        tracker = (DEAL, values.commission_id, values.gross_amount, values.status,
                   values.invoiced_on, None, values.due_on)
        for fail_publish in (False, True):
            with self.subTest(fail_publish=fail_publish), tempfile.TemporaryDirectory() as raw:
                root = pathlib.Path(raw)
                plan_path, proof_path = root / "plan.json", root / "proofs" / "fixture.json"
                plan_path.write_text(json.dumps(value)); plan_path.chmod(0o600)
                proof_path.parent.mkdir(mode=0o700); proof_path.parent.chmod(0o700)
                first = Conn(tracker=tracker)
                bad_tracker = tracker[:-1] + (dt.date(2026, 9, 29),) if not fail_publish else tracker
                second = Conn(commission=None, tracker=bad_tracker)
                if fail_publish:
                    second.commission = (values.commission_id, values.deal_id, values.gross_amount,
                                         values.status, values.invoiced_on, None, 1, "actor-system", values.due_on)
                    second.existing_commission = second.commission
                args = ["staging-fixtures.py", "--apply", "--invoice-plan", str(plan_path),
                        "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA,
                        "--proof-file", str(proof_path)]
                connections = iter((first, second))
                connect = Mock(side_effect=lambda _dsn: next(connections))
                publish = Mock(side_effect=fixture.FixtureRefusal("publish refused")) if fail_publish else Mock()
                stderr = io.StringIO()
                with patch.object(sys, "argv", args), patch.dict(fixture.os.environ, {
                    "DATABASE_URL": "postgresql://neondb_owner:dummy@ep-staging.neon.tech/neondb",
                    "CARR_STAGING_REPLACEMENT_PROJECT_ID": "replacement",
                    "CARR_STAGING_REPLACEMENT_ENDPOINT_ID": "ep-staging",
                }, clear=True), patch.object(sys, "stderr", stderr), \
                        patch.object(fixture, "validate_provider_binding", return_value={"database": "neondb"}), \
                        patch.object(fixture, "publish_private_proof", publish), \
                        patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
                    self.assertEqual(fixture.main(), 2)
                self.assertEqual(first.commits, 1)
                self.assertEqual(second.rollbacks, 1)
                self.assertFalse(proof_path.exists())
                self.assertEqual(publish.call_count, 1 if fail_publish else 0)

    def test_independent_readback_refuses_mismatch_without_claiming_proof(self):
        values = fixture.invoice_commission_values(plan(), aged=True)
        conn = Conn(tracker=(DEAL, values.commission_id, values.gross_amount, "invoiced",
                             values.invoiced_on, None, dt.date(2026, 9, 28)))
        with self.assertRaises(fixture.FixtureRefusal):
            fixture.read_invoice_fixture(conn, plan(), aged=True)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_independent_readback_returns_actual_commission_and_tracker(self):
        values = fixture.invoice_commission_values(plan(), aged=True)
        conn = Conn(commission=(values.commission_id, values.deal_id, values.gross_amount, "invoiced",
                                values.invoiced_on, None, 1, "actor-system", values.due_on),
                    tracker=(DEAL, values.commission_id, values.gross_amount, "invoiced",
                             values.invoiced_on, None, values.due_on))
        readback = fixture.read_invoice_fixture(conn, plan(), aged=True)
        self.assertEqual(readback["commission"]["base_version"], 1)
        self.assertEqual(readback["tracker"]["due_on"], "2026-09-29")
        self.assertEqual(conn.commits, 0)

    def test_proof_binds_actual_readbacks_source_pair_and_verified_target(self):
        values = fixture.invoice_commission_values(plan(), aged=True)
        row = (values.commission_id, values.deal_id, values.gross_amount, values.status,
               values.invoiced_on, None, 1, "actor-system", values.due_on)
        tracker = (DEAL, values.commission_id, values.gross_amount, values.status,
                   values.invoiced_on, None, values.due_on)
        readback = fixture.read_invoice_fixture(Conn(commission=row, tracker=tracker), plan(), aged=True)
        target = {"project": {"id": "prj-safe", "name": "carr-staging-replacement-qa"},
                  "branch": {"id": "br-safe", "name": "main"},
                  "endpoint": {"id": "ep-safe", "host": "ep-safe.neon.tech"},
                  "database": "neondb"}
        proof = fixture.build_fixture_proof(plan(), target, readback, aged=True)
        self.assertEqual(proof["schema"], "doctorcre-staging-fixture-proof.v1")
        self.assertEqual(proof["provenance"], {"kind": "raw-sql-fixture"})
        self.assertEqual(proof["fixture_run"], RUN)
        self.assertEqual(proof["source_pair"], {"source_commit": APP_SHA, "carr_source_commit": CARR_SHA})
        self.assertEqual(proof["verified_target"], target)
        self.assertEqual(proof["fixture"]["commission_id"], str(values.commission_id))
        self.assertEqual(proof["fixture"]["base_version"], 1)
        self.assertEqual(proof["fixture"]["date_offsets_days"]["due_on_before_observation"], 15)
        self.assertEqual(proof["fixture"]["tracker_readback"]["due_on"], "2026-09-29")
        self.assertRegex(proof["tool_source_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("DATABASE_URL", json.dumps(proof))

    def test_never_rewrites_paid_or_conflicting_commission(self):
        values = fixture.invoice_commission_values(plan())
        paid = (values.commission_id, values.deal_id, values.gross_amount, "received",
                values.invoiced_on, dt.date(2026, 10, 14), 2, "actor-system", values.due_on)
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


class TargetTests(unittest.TestCase):
    dsn = "postgresql://neondb_owner:dummy@ep-staging.neon.tech/neondb"
    project = "replacement-project"
    endpoint = "ep-staging"

    def test_preserves_ssl_and_auth_uri_parameters(self):
        fixture.validate_target(self.dsn + "?sslmode=verify-full&channel_binding=require",
                                self.project, self.endpoint)

    def test_provider_binding_returns_only_verified_nonsecret_target_fields(self):
        replies = iter((
            [{"id": self.project, "name": "carr-staging-replacement-qa"}],
            [{"id": "br-main", "name": "main", "default": True, "project_id": self.project}],
            [{"id": self.endpoint, "branch_id": "br-main", "type": "read_write",
              "host": "ep-staging.neon.tech"}],
        ))
        def run(args, **_kwargs):
            return subprocess.CompletedProcess(args, 0, json.dumps(next(replies)), "")
        binding = fixture.validate_provider_binding(
            self.dsn, self.project, self.endpoint, run=run, environ={"NEON_API_KEY": "dummy"})
        self.assertEqual(binding, {
            "project": {"id": self.project, "name": "carr-staging-replacement-qa"},
            "branch": {"id": "br-main", "name": "main"},
            "endpoint": {"id": self.endpoint, "host": "ep-staging.neon.tech"},
            "database": "neondb",
        })
        self.assertNotIn("NEON_API_KEY", json.dumps(binding))

    def test_rejects_libpq_uri_routing_overrides(self):
        for query in ("host=/tmp/pr1767-pg-socket", "hostaddr=127.0.0.1",
                      "service=local", "servicefile=%2Ftmp%2Fpg_service.conf",
                      "port=6543", "dbname=postgres"):
            with self.subTest(query=query), self.assertRaises(fixture.FixtureRefusal):
                fixture.validate_target(f"{self.dsn}?{query}", self.project, self.endpoint)

    def test_rejects_ambient_libpq_target_overrides(self):
        for name in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGSERVICE", "PGSERVICEFILE"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "/tmp/pr1767-pg-socket"}, clear=True), \
                    self.assertRaises(fixture.FixtureRefusal):
                fixture.validate_target(self.dsn, self.project, self.endpoint)

    def test_query_host_override_refuses_before_provider_or_connect(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"
            path.write_text(json.dumps(plan())); path.chmod(0o600)
            args = ["staging-fixtures.py", "--apply", "--invoice-plan", str(path),
                    "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA,
                    "--proof-file", str(path.parent / "proof.json")]
            environment = {
                "DATABASE_URL": self.dsn + "?host=/tmp/pr1767-pg-socket",
                "CARR_STAGING_REPLACEMENT_PROJECT_ID": self.project,
                "CARR_STAGING_REPLACEMENT_ENDPOINT_ID": self.endpoint,
                "NEON_API_KEY": "dummy",
            }
            connect = Mock(side_effect=AssertionError("redirected target reached database connect"))
            provider = Mock(side_effect=AssertionError("redirected target reached provider lookup"))
            stderr = io.StringIO()
            with patch.object(sys, "argv", args), patch.dict(os.environ, environment, clear=True), \
                    patch.object(sys, "stderr", stderr), patch.object(fixture, "validate_provider_binding", provider), \
                    patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
                self.assertEqual(fixture.main(), 2)
            self.assertEqual(connect.call_count, 0)
            self.assertEqual(provider.call_count, 0)
            self.assertIn("libpq target override", stderr.getvalue())


class VendorReferenceFixtureTests(unittest.TestCase):
    expected = (fixture.VENDOR_REFERENCE_SLUG, fixture.VENDOR_REFERENCE_LABEL,
                fixture.VENDOR_REFERENCE_SORT)

    class Cursor:
        def __init__(self, db): self.db, self.result = db, None
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, sql, args=None):
            low = " ".join(sql.lower().split())
            self.db.sql.append((low, args))
            if low == "select session_user,current_user,current_database()":
                self.result = self.db.identity
            elif low.startswith("select slug,label,sort from public.vendor_stage"):
                self.result = self.db.row
            elif low.startswith("insert into public.vendor_stage"):
                self.db.insert_count += 1
                if self.db.row is None:
                    self.db.row = tuple(args)
                    self.result = (args[0],)
                else:
                    self.result = None
            elif low == "set transaction read only":
                self.result = None
            else:
                raise AssertionError(f"unexpected vendor reference SQL: {low}")
        def fetchone(self): return self.result

    class Connection:
        def __init__(self, row=None, identity=("neondb_owner", "neondb_owner", "neondb")):
            self.row, self.identity, self.sql = row, identity, []
            self.insert_count = self.commits = self.rollbacks = 0
        def cursor(self): return VendorReferenceFixtureTests.Cursor(self)
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def commit(self): self.commits += 1
        def rollback(self): self.rollbacks += 1

    def test_install_is_narrow_idempotent_and_exact(self):
        empty = self.Connection()
        self.assertEqual(fixture.install_vendor_reference_data(empty), 1)
        self.assertEqual(empty.row, self.expected)
        self.assertEqual(empty.commits, 1)
        self.assertEqual(empty.insert_count, 1)
        self.assertTrue(all("public.vendor_stage" in sql or "session_user" in sql for sql, _ in empty.sql))

        exact = self.Connection(row=self.expected)
        self.assertEqual(fixture.install_vendor_reference_data(exact), 0)
        self.assertEqual(exact.insert_count, 0)
        self.assertEqual(exact.commits, 1)

        different = self.Connection(row=(self.expected[0], "different label", self.expected[2]))
        with self.assertRaises(fixture.FixtureRefusal):
            fixture.install_vendor_reference_data(different)
        self.assertEqual(different.insert_count, 0)
        self.assertEqual(different.commits, 0)
        self.assertEqual(different.rollbacks, 1)

        wrong_owner = self.Connection(identity=("joe", "joe", "neondb"))
        with self.assertRaises(fixture.FixtureRefusal):
            fixture.install_vendor_reference_data(wrong_owner)
        self.assertEqual(wrong_owner.insert_count, 0)
        self.assertEqual(wrong_owner.rollbacks, 1)

    def test_separate_readback_requires_exact_owner_database_and_row(self):
        conn = self.Connection(row=self.expected)
        self.assertEqual(fixture.read_vendor_reference_data(conn), {
            "slug": self.expected[0], "label": self.expected[1], "sort": self.expected[2]})
        self.assertEqual(conn.rollbacks, 1)
        with self.assertRaises(fixture.FixtureRefusal):
            fixture.read_vendor_reference_data(self.Connection(identity=("other", "other", "neondb"),
                                                                 row=self.expected))
        with self.assertRaises(fixture.FixtureRefusal):
            fixture.read_vendor_reference_data(self.Connection(row=None))

    def test_proof_binds_unique_operation_source_target_readback_and_tool(self):
        operation_id = uuid.UUID("791a9bc4-177a-42ed-91b6-9469e9a102a2")
        target = {"project": {"id": "project"}, "branch": {"id": "branch"},
                  "endpoint": {"id": "endpoint"}, "database": "neondb"}
        readback = {"slug": self.expected[0], "label": self.expected[1], "sort": self.expected[2]}
        proof = fixture.build_vendor_reference_proof(operation_id, APP_SHA, CARR_SHA, target, readback)
        self.assertEqual(proof["operation_id"], str(operation_id))
        self.assertEqual(proof["source_pair"], {"source_commit": APP_SHA, "carr_source_commit": CARR_SHA})
        self.assertEqual(proof["verified_target"], target)
        self.assertEqual(proof["fixture"]["row_readback"], readback)
        self.assertRegex(proof["recorded_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual(proof["tool_source_sha256"],
                         hashlib.sha256(pathlib.Path(fixture.__file__).read_bytes()).hexdigest())

    def test_plan_mode_is_read_only_and_production_target_refuses_before_provider_or_connect(self):
        args = ["staging-fixtures.py", "--vendor-reference-data",
                "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA]
        output = io.StringIO()
        provider = Mock(side_effect=AssertionError("plan mode reached provider"))
        with patch.object(sys, "argv", args), patch.object(sys, "stdout", output), \
                patch.object(fixture, "validate_provider_binding", provider):
            self.assertEqual(fixture.main(), 0)
        self.assertEqual(output.getvalue(), "staging-fixtures: PLAN vendor_reference_rows=1 mutated=false\n")
        self.assertEqual(provider.call_count, 0)

        with tempfile.TemporaryDirectory() as raw:
            proof_parent = pathlib.Path(raw) / "proofs"
            proof_parent.mkdir(mode=0o700); proof_parent.chmod(0o700)
            proof_path = proof_parent / "fixture.json"
            args += ["--apply", "--proof-file", str(proof_path)]
            environment = {"DATABASE_URL": "postgresql://neondb_owner:dummy@ep-prod.neon.tech/neondb",
                           "CARR_STAGING_REPLACEMENT_PROJECT_ID": fixture.PRODUCTION_PROJECT_ID,
                           "CARR_STAGING_REPLACEMENT_ENDPOINT_ID": "ep-prod"}
            provider = Mock(side_effect=AssertionError("production target reached provider"))
            connect = Mock(side_effect=AssertionError("production target reached database"))
            stderr = io.StringIO()
            with patch.object(sys, "argv", args), patch.object(sys, "stderr", stderr), \
                    patch.dict(fixture.os.environ, environment, clear=True), \
                    patch.object(fixture, "validate_provider_binding", provider), \
                    patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
                self.assertEqual(fixture.main(), 2)
            self.assertEqual(provider.call_count, 0)
            self.assertEqual(connect.call_count, 0)
            self.assertIn("forbidden", stderr.getvalue())


class TourFixtureTests(unittest.TestCase):
    def tour_plan(self):
        value = plan()
        value["keys"]["tour"] = "33333333-3333-4333-8333-333333333333"
        value["names"]["tour"] = f"Synthetic staging QA {RUN} draft tour"
        value["records"]["tour"] = {
            "id": "11111111-1111-4111-8111-111111111111",
            "name": value["names"]["tour"],
        }
        return value

    def test_tour_plan_binds_app_created_identity_without_tour_receipt(self):
        value = self.tour_plan()
        self.assertNotIn("tour", value["receipts"])
        tour = fixture.excluded_tour_values(value)
        self.assertEqual(str(tour.tour_id), value["records"]["tour"]["id"])
        self.assertEqual(tour.name, value["names"]["tour"])
        self.assertEqual(str(tour.client_id), value["records"]["client"]["id"])
        self.assertEqual(tour.creation_key, value["keys"]["tour"])
        self.assertEqual(tour.property_id, fixture.excluded_tour_values(value).property_id)
        self.assertEqual(tour.stop_id, fixture.excluded_tour_values(value).stop_id)
        value["keys"]["tour"] = value["keys"]["tour"].upper()
        self.assertEqual(fixture.excluded_tour_values(value).creation_key, value["keys"]["tour"])

    def test_tour_plan_rejects_missing_key_and_mismatched_name(self):
        for change in ("missing_key", "wrong_name", "wrong_id"):
            with self.subTest(change=change):
                value = self.tour_plan()
                if change == "missing_key": del value["keys"]["tour"]
                if change == "wrong_name": value["records"]["tour"]["name"] = "Other"
                if change == "wrong_id": value["records"]["tour"]["id"] = "invalid"
                with self.assertRaises(fixture.FixtureRefusal):
                    fixture.excluded_tour_values(value)

    def test_tour_dry_run_uses_private_plan_and_never_connects(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"
            path.write_text(json.dumps(self.tour_plan()))
            path.chmod(0o600)
            args = ["staging-fixtures.py", "--tour-plan", str(path),
                    "--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA]
            stdout = io.StringIO()
            with patch.object(sys, "argv", args), patch.object(sys, "stdout", stdout), \
                    patch.object(fixture, "validate_provider_binding", side_effect=AssertionError("provider called")):
                self.assertEqual(fixture.main(), 0)
            self.assertIn("excluded_tour_stops=1 mutated=false", stdout.getvalue())

    def test_tour_proof_reports_raw_sql_and_no_promotion(self):
        value = self.tour_plan()
        readback = {"active_stop_count": 0, "excluded_stop_count": 1,
                    "route_accepted": False, "route_sequence": None, "route_label": None}
        proof = fixture.build_tour_fixture_proof(value, {"database": "neondb"}, readback)
        self.assertEqual(proof["provenance"]["kind"], "raw-sql-fixture")
        self.assertEqual(proof["fixture"]["mode"], "excluded-tour-stop")
        self.assertEqual(proof["fixture"]["tour_readback"], readback)
        self.assertNotIn("receipt", proof["fixture"])

    def test_install_rerun_refusals_and_independent_readback(self):
        value = self.tour_plan()
        expected = fixture.excluded_tour_values(value)
        route_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

        class TourCursor:
            def __init__(self, db): self.db, self.result = db, None
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def fetchone(self): return self.result
            def fetchall(self): return self.result
            def execute(self, sql, args=None):
                low = " ".join(sql.lower().split())
                self.db.sql.append(low)
                point = lambda role: {"latitude": 30.4, "longitude": -87.2,
                                      "position_role": role, "precision_class": "approximate",
                                      "source_ref": "synthetic-staging-qa-point-v1"}
                if low == "set transaction read only": self.result = None
                elif low.startswith("select session_user,current_user,current_database()"):
                    self.result = ("neondb_owner", "neondb_owner", "neondb")
                elif "from public.actor" in low: self.result = [("joe",), ("system",)]
                elif "from public.tool_call" in low:
                    key, tenant, tour_id = args
                    self.result = ((1,) if any(
                        row == (key, "create-tour-domain", "joe", tenant,
                                {"ok": True, "tour_id": tour_id})
                        for row in self.db.tool_calls) else None)
                elif "from public.client cl" in low:
                    self.result = (str(expected.client_id), "engaged", None, str(fixture.FIXTURE_ID),
                                   "Synthetic Staging Fixture", None, None)
                elif "from ops.tour where" in low:
                    self.result = (expected.tour_id, expected.name, self.db.tour_status, 1,
                                   "synthetic-staging-qa-v1", "client", str(expected.client_id), "bound")
                elif "from ops.tour_route_version where" in low:
                    self.result = [(route_id, 1, None, point("start"), point("end"),
                                    "manual", None, None, {}, None, self.db.route_actor, None)]
                elif "from ops.tour_route_version_acceptance" in low: self.result = (0,)
                elif any(f"from ops.{table}" in low for table in
                         ("tour_property_membership", "tour_public_projection", "tour_selection_cart_version",
                          "tour_field_assertion", "tour_property_coordinate_candidate")):
                    self.result = (0,)
                elif "from ops.tour_property where" in low:
                    self.result = ((expected.property_id, fixture.TOUR_TENANT, self.db.property_status, None)
                                   if self.db.property_exists else None)
                elif "from ops.tour_route_stop where route_version_id" in low:
                    self.result = ([(expected.stop_id, fixture.TOUR_TENANT, route_id, expected.property_id,
                                     None, None, self.db.stop_state, None, None, False, 0, 0, "unknown",
                                     fixture.EMPTY_ASSERTION_SET_DIGEST, "system")]
                                   if self.db.stop_exists else [])
                elif "from ops.tour_route_stop where property_id" in low: self.result = (0,)
                elif "insert into ops.tour_property" in low: self.db.property_exists = True
                elif "insert into ops.tour_route_stop" in low:
                    if self.db.fail_stop: raise RuntimeError("forced stop insert failure")
                    self.db.stop_exists = True
                else: raise AssertionError(f"unexpected Tour SQL: {low}")

        class TourConn:
            def __init__(self):
                self.property_exists = self.stop_exists = self.fail_stop = False
                self.property_status, self.stop_state = "unknown", "excluded"
                self.tour_status, self.route_actor = "draft", "joe"
                self.tool_calls = [(value["keys"]["tour"], "create-tour-domain", "joe",
                                    fixture.TOUR_TENANT,
                                    {"ok": True, "tour_id": str(expected.tour_id)})]
                self.commits = self.rollbacks = 0
                self.sql = []
            def cursor(self): return TourCursor(self)
            def commit(self): self.commits += 1
            def rollback(self): self.rollbacks += 1

        for rows in ([],
                     [(str(uuid.uuid4()), "create-tour-domain", "joe", fixture.TOUR_TENANT,
                       {"ok": True, "tour_id": str(expected.tour_id)})],
                     [(value["keys"]["tour"], "create-tour-domain", "dell", fixture.TOUR_TENANT,
                       {"ok": True, "tour_id": str(expected.tour_id)})],
                     [(value["keys"]["tour"], "create-tour-domain", "joe", "other-tenant",
                       {"ok": True, "tour_id": str(expected.tour_id)})],
                     [(value["keys"]["tour"], "other-verb", "joe", fixture.TOUR_TENANT,
                       {"ok": True, "tour_id": str(expected.tour_id)})],
                     [(value["keys"]["tour"], "create-tour-domain", "joe", fixture.TOUR_TENANT,
                       {"ok": True, "tour_id": str(uuid.uuid4())})],
                     [(value["keys"]["tour"], "create-tour-domain", "joe", fixture.TOUR_TENANT,
                       {"ok": False, "tour_id": str(expected.tour_id)})]):
            with self.subTest(tool_calls=rows):
                missing = TourConn()
                missing.tool_calls = rows
                with self.assertRaises(fixture.FixtureRefusal):
                    fixture.install_excluded_tour_fixture(missing, value)
                self.assertFalse(any("insert into ops.tour_property" in sql for sql in missing.sql))
                self.assertEqual(missing.commits, 0)
        db = TourConn()
        self.assertEqual(fixture.install_excluded_tour_fixture(db, value), 1)
        self.assertEqual(fixture.install_excluded_tour_fixture(db, value), 0)
        self.assertEqual(db.commits, 2)
        self.assertTrue(any("insert into ops.tour_property" in sql for sql in db.sql))
        self.assertTrue(any("insert into ops.tour_route_stop" in sql for sql in db.sql))
        proof = fixture.read_excluded_tour_fixture(db, value)
        self.assertEqual((proof["active_stop_count"], proof["excluded_stop_count"]), (0, 1))
        self.assertIn("set transaction read only", db.sql)
        canonical_call = db.tool_calls
        db.tool_calls = []
        with self.assertRaises(fixture.FixtureRefusal):
            fixture.read_excluded_tour_fixture(db, value)
        db.tool_calls = canonical_call
        for field, changed in (("tour_status", "active"), ("route_actor", "dell"),
                               ("stop_state", "active"), ("property_status", "active")):
            with self.subTest(field=field):
                original = getattr(db, field)
                setattr(db, field, changed)
                with self.assertRaises(fixture.FixtureRefusal):
                    fixture.install_excluded_tour_fixture(db, value)
                setattr(db, field, original)
        self.assertEqual(db.commits, 2)
        db.property_exists = db.stop_exists = False
        db.fail_stop = True
        with self.assertRaises(RuntimeError): fixture.install_excluded_tour_fixture(db, value)
        self.assertGreaterEqual(db.rollbacks, 5)


def relationship_plan():
    value = plan()
    value["names"]["deal"] = f"Synthetic staging QA {RUN} active deal"
    value["records"]["deal"] = {"id": str(DEAL), "name": value["names"]["deal"]}
    for number, step in enumerate(("client", "deal", "vendor_party", "vendor", "vendor_entry"), 10):
        value["keys"][step] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"relationship-test-key:{number}"))
    value["names"]["vendor"] = f"QA Vendor {value['keys']['vendor_party']}"
    for number, step in enumerate(("vendor_party", "vendor", "vendor_entry"), 20):
        value["records"][step] = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"relationship-test-record:{number}")), "name": value["names"]["vendor"]}
    value["records"]["vendor_entry"]["link_id"] = str(uuid.UUID(int=23))
    for step, field in (("client", "client_id"), ("deal", "deal_id"), ("vendor_party", "party_id"),
                        ("vendor", "vendor_id"), ("vendor_entry", "activity_id")):
        value["receipts"][step] = {field: value["records"][step]["id"]}
    return value


class RelationshipConnection:
    def __init__(self):
        self.plan = relationship_plan()
        p = self.plan
        self.system = str(uuid.UUID(int=30))
        self.joe = str(uuid.UUID(int=31))
        self.identity = ("neondb_owner", "neondb_owner", "neondb")
        self.rows = {name: [] for name in ("party", "client", "deal", "vendor", "party_link", "party_link_deal", "event", "activity")}
        common = {"merged_into": None, "deleted_at": None, "version": 1,
                  "created_by": self.joe, "updated_by": self.joe, "created_at": p["observed_at"]}
        self.rows["party"] = [dict(common, id=str(PARTY), name="Synthetic Staging Fixture", kind="org",
            notes_path="fixtures/clean-staging-replacement-v1", city=None, contact_state="active", created_by=self.system),
            dict(common, id=p["records"]["vendor_party"]["id"], name=p["names"]["vendor"], kind="org", city=None)]
        self.rows["client"] = [dict(common, id=p["records"]["client"]["id"], party_id=str(PARTY),
            status="engaged", acquisition_source="referral", acquisition_detail="Explicitly fictional staging QA fixture only")]
        self.rows["deal"] = [dict(common, id=str(DEAL), client_id=p["records"]["client"]["id"],
            name=p["names"]["deal"], owner="joe", phase="pending", lane="territory", deal_type="startup", outcome=None)]
        self.rows["vendor"] = [dict(common, id=p["records"]["vendor"]["id"], party_id=p["records"]["vendor_party"]["id"],
            category="cpa", stage="prospect_uncontacted", territory=None, deal_history_verified_at=None)]
        self.rows["activity"] = [{"id": p["records"]["vendor_entry"]["id"], "vendor_id": p["records"]["vendor"]["id"],
            "kind": "note", "summary": "Synthetic staging QA introduction entry",
            "detail": "Fictional QA evidence only; no contact, referral, or send occurred."}]
        self.rows["party_link"] = [{"id": p["records"]["vendor_entry"]["link_id"],
            "from_party": p["records"]["vendor_party"]["id"], "to_party": str(PARTY), "kind": "can_introduce",
            "via_party": None, "note": "Synthetic staging QA suggested introduction only; no real-world introduction occurred."}]
        for step, verb, table in (("client", "new-client", "client"), ("deal", "new-deal", "deal"),
                ("vendor_party", "add-party", "party"), ("vendor", "new-vendor", "vendor"), ("vendor_entry", "log-activity", "vendor")):
            self.rows["event"].append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, step)), "verb": verb,
                "subject_type": table, "subject_id": p["records"]["vendor" if step == "vendor_entry" else step]["id"],
                "idempotency_key": p["keys"][step], "new_value": {"activity": p["records"]["vendor_entry"]["id"]}})
        self.commits = self.rollbacks = self.writes = 0
        self.sql = []
        self.fail_insert = False
        self.actor_rows = None
        self.omit_inserts = False
        self.project_mutation = lambda network: network
        self.saved = copy.deepcopy(self.rows)

    def cursor(self): return RelationshipCursor(self)
    def commit(self): self.commits += 1; self.saved = copy.deepcopy(self.rows)
    def rollback(self): self.rollbacks += 1; self.rows = copy.deepcopy(self.saved)

    def snapshot(self):
        nodes = []
        for p in self.rows["party"]:
            role = next((r for r in self.rows["vendor"] if r["party_id"] == p["id"]), None)
            kind = "vendor" if role else "client"
            role = role or next(r for r in self.rows["client"] if r["party_id"] == p["id"])
            nodes.append({"id": "party:" + p["id"], "record_id": role["id"], "kind": kind,
                          "name": p["name"], "territory": role.get("territory") or p.get("city"), "owner": "joe", "verticals": []})
        nodes += [{"id": "deal:" + d["id"], "record_id": d["id"], "kind": "deal", "name": d["name"], "verticals": []} for d in self.rows["deal"]]
        edges = [{"id": "client-deal:" + d["id"], "from": "party:" + next(c["party_id"] for c in self.rows["client"] if c["id"] == d["client_id"]),
                  "to": "deal:" + d["id"], "kind": "client_deal", "via": None} for d in self.rows["deal"]]
        edges += [{"id": "link:" + r["id"], "from": "party:" + r["from_party"], "to": "party:" + r["to_party"],
                   "kind": r["kind"], "via": None, "detail": r["note"], "summary": r["note"][:180]} for r in self.rows["party_link"]]
        edges += [{"id": f"evidence:{r['referred_by']}:{r['deal_id']}:referred", "from": "party:" + r["referred_by"],
                   "to": "deal:" + r["deal_id"], "kind": "referred", "via": None, "detail": r["note"]} for r in self.rows["party_link_deal"]]
        referrals = [{"node_id": "party:" + r["referred_by"], "deals": 1, "won": 0, "lost": 0} for r in self.rows["party_link_deal"]]
        return self.project_mutation({"nodes": nodes, "edges": edges, "referrals": referrals})


class RelationshipCursor:
    def __init__(self, db): self.db = db; self.result = []
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def fetchone(self): return self.result[0] if self.result else None
    def fetchall(self): return self.result
    def execute(self, sql, args=()):
        low = " ".join(sql.lower().split())
        args = tuple(str(a) if isinstance(a, uuid.UUID) else a for a in (args or ()))
        self.db.sql.append((low, args))
        self.result = []
        if low.startswith("set transaction") or low.startswith("lock table"): return
        if low.startswith("select session_user"): self.result = [self.db.identity]; return
        if "from public.actor" in low:
            if self.db.actor_rows is not None: self.result = self.db.actor_rows; return
            self.result = [(self.db.system if "'system'" in low else self.db.joe,)]; return
        if low.startswith("with live as"): self.result = [(self.db.snapshot(),)]; return
        if low.startswith("insert into"):
            if self.db.fail_insert: raise RuntimeError("synthetic insert failure")
            if self.db.omit_inserts: return
            match = re.search(r"insert into public\.(\w+)\s*\(([^)]+)\)", low)
            table, columns = match.groups()
            row = dict(zip((c.strip() for c in columns.split(',')), args))
            row["created_at"] = self.db.plan["observed_at"]
            row.setdefault("version", 1)
            if table in ("party", "client"): row.setdefault("merged_into", None)
            if table == "party": row.setdefault("deleted_at", None)
            if table == "deal": row.setdefault("outcome", None)
            self.db.rows[table].append(row); self.db.writes += 1
            return
        if low.startswith("update public."):
            table = low.split('.')[1].split()[0]
            row = next(r for r in self.db.rows[table] if r["id"] == args[-1])
            row["city" if table == "party" else "territory"] = args[0]
            row["updated_by"] = args[1]; row["version"] += 1
            if table == "vendor": row["deal_history_verified_at"] = None
            self.db.writes += 1; return
        match = re.search(r"from public\.(\w+) r where (.+?)(?: order by| for update|$)", low)
        if not match: raise AssertionError(f"unexpected relationship SQL: {low}")
        table, condition = match.groups()
        clauses = re.findall(r"r\.(\w+)=%s", condition)
        rows = self.db.rows[table]
        selected = []
        for row in rows:
            comparisons = [row.get(key) == value for key, value in zip(clauses, args)]
            if (any(comparisons) if " or " in condition else all(comparisons)):
                selected.append((copy.deepcopy(row),))
        self.result = selected


class RelationshipFixtureTests(unittest.TestCase):
    def test_complete_proof_matches_revised_app_logical_rows_receipts_and_hashes(self):
        db = RelationshipConnection(); fixture.install_relationship_fixture(db, db.plan)
        proof = fixture.build_relationship_fixture_proof(db.plan, {"database": "neondb"}, fixture.read_relationship_fixture(db, db.plan))
        namespace = uuid.UUID("439206ab-d73a-5c1b-8a20-531e536e5804")
        identifier = lambda role: str(uuid.uuid5(namespace, f"relationship:{role}:{RUN}"))
        note = lambda role: f"Fictional staging QA {role} referral for fixture {RUN}."
        expected = {
            "destination_party": {"id": identifier("destination_party"), "name": f"Synthetic staging QA {RUN} referred client", "kind": "org", "city": "Synthetic Staging QA North"},
            "destination_client": {"id": identifier("destination_client"), "party_id": identifier("destination_party"), "status": "engaged"},
            "destination_deal": {"id": identifier("destination_deal"), "name": f"Synthetic staging QA {RUN} referred deal", "client_id": identifier("destination_client"),
                "owner": "joe", "phase": "pending", "lane": "territory", "deal_type": "startup"},
            "fixed_party": {"id": str(PARTY), "city": "Synthetic Staging QA North"},
            "owned_vendor": {"id": db.plan["records"]["vendor"]["id"], "party_id": db.plan["records"]["vendor_party"]["id"], "territory": "Synthetic Staging QA South"},
        }
        for role, party, to, deal in (("referral", str(PARTY), identifier("destination_party"), identifier("destination_deal")),
                ("vendor", db.plan["records"]["vendor_party"]["id"], str(PARTY), str(DEAL))):
            association = role + "_association"; attribution = role + "_attribution"
            common = {"kind": "referred", "via_party_id": None, "occurred_on": "2026-10-14", "note": note(association), "attribution": "system"}
            expected[association] = dict(common, id=identifier(association), from_party_id=party, to_party_id=to)
            expected[attribution] = dict(common, id=identifier(attribution), party_id=party, deal_id=deal)
        self.assertEqual(proof["rows"], expected)
        hashes = {role: hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':')).encode()).hexdigest() for role, row in expected.items()}
        self.assertEqual(proof["row_sha256"], hashes)
        for role, row in expected.items():
            field = "city" if role == "fixed_party" else "territory" if role == "owned_vendor" else None
            table = "party" if role in ("destination_party", "fixed_party") else "client" if role == "destination_client" else "deal" if role == "destination_deal" else "vendor" if role == "owned_vendor" else "party_association" if role.endswith("association") else "deal_attribution"
            receipt = proof["receipts"][role]
            self.assertEqual(receipt, {"operation": "update" if field else "insert", "table": table, "row_id": row["id"],
                "before_sha256": hashes[role] if field else None, "after_sha256": hashes[role]})
        self.assertEqual(proof["original_note_sha256"], {role: hashlib.sha256(note(role + "_association").encode()).hexdigest() for role in ("referral", "vendor")})
        self.assertEqual(proof["projection"]["response_sha256"], hashlib.sha256(json.dumps(proof["projection"]["response"], sort_keys=True, separators=(',', ':')).encode()).hexdigest())

    def test_cli_refuses_missing_controls_and_conflicting_modes(self):
        base = ["staging-fixtures.py", "--relationship-plan", "missing.json"]
        pins = ["--expected-app-sha", APP_SHA, "--expected-carr-sha", CARR_SHA]
        for extra in ([], pins + ["--apply"], pins + ["--proof-file", "proof.json"],
                      pins + ["--invoice-plan", "invoice.json"], pins + ["--tour-plan", "tour.json"],
                      pins + ["--vendor-reference-data"], pins + ["--aged-invoice"]):
            with self.subTest(extra=extra), patch.object(sys, "argv", base + extra), \
                    patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as raised:
                fixture.main()
            self.assertEqual(raised.exception.code, 2)

    def test_cli_dry_run_and_source_refusals_never_reach_target(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"; path.write_text(json.dumps(relationship_plan())); path.chmod(0o600)
            args = ["staging-fixtures.py", "--relationship-plan", str(path), "--expected-app-sha", APP_SHA,
                    "--expected-carr-sha", CARR_SHA]
            with patch.object(fixture, "validate_target") as target, patch.object(sys, "argv", args), \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(fixture.main(), 0)
                self.assertIn("mutated=false", output.getvalue())
                target.assert_not_called()
            for bad in ("z" * 40, "0" * 40):
                with patch.object(fixture, "validate_target") as target, patch.object(sys, "argv", args[:-1] + [bad]), \
                        patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(fixture.main(), 2)
                    target.assert_not_called()

    def test_cli_rerun_retains_proof_without_writes(self):
        self._check_cli_rerun(changed_state=False)

    def test_cli_rerun_refuses_changed_committed_state(self):
        self._check_cli_rerun(changed_state=True)

    def _check_cli_rerun(self, *, changed_state):
        db = RelationshipConnection()
        clock = dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc)
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None): return clock
        def connect(*args):
            context = Mock()
            context.__enter__ = Mock(return_value=db)
            context.__exit__ = Mock(return_value=False)
            return context
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"
            path.write_text(json.dumps(db.plan)); path.chmod(0o600)
            proof = pathlib.Path(raw) / "relationship-fixture-proof.json"
            args = ["staging-fixtures.py", "--relationship-plan", str(path), "--expected-app-sha", APP_SHA,
                    "--expected-carr-sha", CARR_SHA, "--apply", "--proof-file", str(proof)]
            with patch.object(sys, "argv", args), patch.dict(sys.modules, psycopg=SimpleNamespace(connect=connect)), \
                    patch.object(fixture, "validate_target"), \
                    patch.object(fixture, "validate_provider_binding", return_value={"database": "neondb"}), \
                    patch.object(fixture.dt, "datetime", Clock), \
                    patch("sys.stdout", new_callable=io.StringIO) as output, \
                    patch("sys.stderr", new_callable=io.StringIO) as errors:
                self.assertEqual(fixture.main(), 0)
                self.assertIn("proof=created", output.getvalue())
                self.assertEqual(db.writes, 9)
                original = proof.read_bytes(); info = proof.stat()
                clock += dt.timedelta(seconds=1)
                output.truncate(0); output.seek(0)
                self.assertEqual(fixture.main(), 0, errors.getvalue())
                self.assertIn("relationship_rows_changed=0", output.getvalue())
                self.assertEqual(db.writes, 9)
                self.assertEqual(proof.read_bytes(), original)
                self.assertEqual((proof.stat().st_ino, proof.stat().st_mtime_ns), (info.st_ino, info.st_mtime_ns))
                if changed_state:
                    # This field is allowed by fixture ownership checks and appears in physical receipts.
                    db.rows["party"][0]["version"] += 1
                    db.saved = copy.deepcopy(db.rows)
                    self.assertEqual(fixture.main(), 2)
                    self.assertIn("existing proof does not exactly match", errors.getvalue())
                    self.assertEqual(db.writes, 9)
                    self.assertEqual(proof.read_bytes(), original)
                    return
                for keys in (("fixture_run",), ("source_pair", "source_commit"), ("verified_target", "database"),
                        ("row_sha256", "fixed_party"), ("receipts", "fixed_party", "after_sha256"),
                        ("projection", "response_sha256"), ("projection", "response", "observed_at"),
                        ("projection", "response", "valid_until"),
                        ("projection_evidence", "response_sha256"), ("projection_evidence", "content_sha256"),
                        ("projection_evidence", "network_response_sha256"), ("projection_evidence", "observed_at")):
                    changed = json.loads(original)
                    field = changed
                    for key in keys[:-1]: field = field[key]
                    field[keys[-1]] = "different"
                    proof.write_text(json.dumps(changed))
                    with self.subTest(tampered=keys):
                        self.assertEqual(fixture.main(), 2)
                        self.assertEqual(db.writes, 9)
                        self.assertEqual(json.loads(proof.read_text()), changed)
                proof.write_bytes(original)

    def test_cli_target_validation_precedes_connections_and_private_proof(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "plan.json"; path.write_text(json.dumps(relationship_plan())); path.chmod(0o600)
            proof = pathlib.Path(raw) / "relationship-fixture-proof.json"
            args = ["staging-fixtures.py", "--relationship-plan", str(path), "--expected-app-sha", APP_SHA,
                    "--expected-carr-sha", CARR_SHA, "--apply", "--proof-file", str(proof)]
            connect = Mock()
            with patch.object(sys, "argv", args), patch.dict(sys.modules, psycopg=SimpleNamespace(connect=connect)), \
                    patch.object(fixture, "validate_provider_binding") as provider, patch.dict(os.environ, DATABASE_URL="", clear=True), \
                    patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(fixture.main(), 2)
                provider.assert_not_called(); connect.assert_not_called()
                self.assertFalse(proof.exists())
            events = []
            writer, reader = Mock(), Mock()
            writer.__enter__ = Mock(return_value=writer); writer.__exit__ = Mock(return_value=False)
            reader.__enter__ = Mock(return_value=reader); reader.__exit__ = Mock(return_value=False)
            target = {"database": "neondb", "endpoint": {"id": "verified"}}
            with patch.object(sys, "argv", args), patch.dict(sys.modules, psycopg=SimpleNamespace(connect=Mock(side_effect=[writer, reader]))), \
                    patch.object(fixture, "validate_target", side_effect=lambda *a: events.append("target")), \
                    patch.object(fixture, "validate_provider_binding", side_effect=lambda *a: events.append("provider") or target), \
                    patch.object(fixture, "install_relationship_fixture", side_effect=lambda *a: events.append("install") or 9), \
                    patch.object(fixture, "read_relationship_fixture", side_effect=lambda *a: events.append("read") or {"projection": {}}), \
                    patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(fixture.main(), 0)
            self.assertEqual(events, ["target", "provider", "install", "read"])
            self.assertEqual(json.loads(proof.read_text())["verified_target"], target)
            self.assertEqual(proof.stat().st_mode & 0o777, 0o600)

    def test_refuses_existing_partial_territories_and_destination_vendor_precedence(self):
        for table, field in (("party", "city"), ("vendor", "territory")):
            db = RelationshipConnection(); fixture.install_relationship_fixture(db, db.plan)
            db.rows[table][0][field] = None; db.saved = copy.deepcopy(db.rows)
            with self.subTest(table=table), self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
        db = RelationshipConnection(); fixture.install_relationship_fixture(db, db.plan)
        dest = fixture.relationship_fixture_values(db.plan)["destination"]
        db.rows["vendor"].append(dict(db.rows["vendor"][0], party_id=dest["party_id"]))
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)

    def test_projection_hashes_match_node_serialization_and_consumer_counts(self):
        projected = fixture._relationship_projection("project", {"snapshot": {"nodes": [], "edges": [], "referrals": []},
            "observed_at": "2026-10-14T11:15:00.000Z"})
        expected = ('{"schema":"carr-relationship-network.v1","observed_at":"2026-10-14T11:15:00.000Z",'
                    '"valid_until":"2026-10-14T11:16:00.000Z","nodes":[],"edges":[],"referrals":[],"suggestions":[]}')
        self.assertEqual(projected["response_sha256"], hashlib.sha256(expected.encode()).hexdigest())
        db = RelationshipConnection(); fixture.install_relationship_fixture(db, db.plan)
        readback = fixture.read_relationship_fixture(db, db.plan)
        projection = readback["projection_evidence"]
        self.assertEqual(projection["same_territory_referrals"], 1)
        self.assertEqual(projection["other_territory_referrals"], 1)
        self.assertEqual(projection["territory_sha256"], hashlib.sha256(b"Synthetic Staging QA North").hexdigest())
        self.assertEqual(projection["network_response_sha256"], projection["response_sha256"])
        self.assertEqual(projection["evidence_detail_sha256"], hashlib.sha256(readback["rows"]["referral_association"]["note"].encode()).hexdigest())

    def test_values_bind_run_and_distinct_destination(self):
        value = fixture.relationship_fixture_values(relationship_plan())
        self.assertEqual(value["destination"]["name"], f"Synthetic staging QA {RUN} referred deal")
        self.assertNotEqual(value["destination"]["party_id"], str(PARTY))
        self.assertNotEqual(value["destination"]["client_id"], relationship_plan()["records"]["client"]["id"])
        other = relationship_plan(); other["run"] = "different-run"; other["names"]["deal"] = other["records"]["deal"]["name"] = "Synthetic staging QA different-run active deal"
        self.assertNotEqual(value["destination"]["id"], fixture.relationship_fixture_values(other)["destination"]["id"])

    def test_refuses_incomplete_or_mismatched_plan(self):
        for section, key in (("records", "vendor"), ("records", "vendor_party"), ("records", "vendor_entry"),
                             ("receipts", "vendor"), ("keys", "client")):
            value = relationship_plan(); del value[section][key]
            with self.subTest(section=section, key=key), self.assertRaises(fixture.FixtureRefusal):
                fixture.relationship_fixture_values(value)
        for step in ("client", "deal", "vendor_party", "vendor", "vendor_entry"):
            value = relationship_plan(); value["receipts"][step] = {"wrong_id": str(DEAL)}
            with self.subTest(step=step), self.assertRaises(fixture.FixtureRefusal): fixture.relationship_fixture_values(value)

    def test_install_read_prove_and_repeat_without_effect(self):
        db = RelationshipConnection(); value = db.plan
        self.assertEqual(fixture.install_relationship_fixture(db, value), 9)
        first = copy.deepcopy(db.rows); writes = db.writes
        self.assertEqual(fixture.install_relationship_fixture(db, value), 0)
        self.assertEqual(db.rows, first)
        self.assertEqual(db.writes, writes)
        readback = fixture.read_relationship_fixture(db, value)
        proof = fixture.build_relationship_fixture_proof(value, {"database": "neondb"}, readback)
        self.assertEqual(proof["schema"], "doctorcre-staging-relationship-fixture.v1")
        self.assertEqual(proof["source_pair"], value["release"])
        self.assertEqual(proof["verified_target"], {"database": "neondb"})
        self.assertEqual(len(proof["creation_receipts"]), 7)
        self.assertEqual(len(proof["update_receipts"]), 2)
        self.assertEqual(proof["projection_evidence"]["territories"]["inside"]["referral_count"], 1)
        self.assertEqual(proof["projection_evidence"]["territories"]["outside"]["referral_count"], 1)
        self.assertEqual(len(proof["projection_evidence"]["nodes"]), 5)
        self.assertEqual(len(proof["projection_evidence"]["edges"]), 6)
        self.assertEqual(proof["original_note_sha256"]["referral"], hashlib.sha256(proof["rows"]["referral_association"]["note"].encode()).hexdigest())
        for receipt in proof["creation_receipts"] + proof["update_receipts"]:
            self.assertEqual(receipt["row_sha256"], hashlib.sha256(json.dumps(receipt["row"], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest())
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "relationship-fixture-proof.json"
            self.assertEqual(fixture.publish_private_proof(path, proof), "created")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), proof)

    def test_refuses_unowned_ambiguous_and_mismatched_rows_before_writes(self):
        cases = [("party", "notes_path", "shared"), ("party", "city", "Real City"),
                 ("client", "id", str(uuid.UUID(int=99))), ("client", "merged_into", str(DEAL)),
                 ("deal", "owner", "dell"), ("deal", "client_id", str(DEAL)), ("deal", "phase", "closed"),
                 ("vendor", "territory", "Real Territory"), ("vendor", "category", "lender"),
                 ("event", "idempotency_key", "other-run"), ("activity", "detail", "Changed original note")]
        for table, key, bad in cases:
            db = RelationshipConnection(); db.rows[table][0][key] = bad; db.saved = copy.deepcopy(db.rows)
            with self.subTest(table=table, key=key), self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
            self.assertEqual(db.writes, 0)
            self.assertEqual(db.rollbacks, 1)
        for table in ("client", "vendor", "event"):
            db = RelationshipConnection(); db.rows[table].append(copy.deepcopy(db.rows[table][0]))
            with self.subTest(ambiguous=table), self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
            self.assertEqual(db.writes, 0)
        db = RelationshipConnection(); db.rows["vendor"].append(dict(db.rows["vendor"][0], party_id=str(PARTY)))
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
        self.assertEqual(db.writes, 0)

    def test_refuses_partial_collision_changed_and_missing_readback(self):
        db = RelationshipConnection()
        with self.assertRaises(fixture.FixtureRefusal): fixture.read_relationship_fixture(db, db.plan)
        fixture.install_relationship_fixture(db, db.plan)
        complete = copy.deepcopy(db.rows)
        for table in ("party", "client", "deal", "party_link", "party_link_deal"):
            db.rows = copy.deepcopy(complete); db.rows[table].pop(); db.saved = copy.deepcopy(db.rows)
            with self.subTest(partial=table), self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
        for table, field in (("party", "notes_path"), ("client", "party_id"), ("deal", "deal_type"),
                             ("party_link", "note"), ("party_link_deal", "referred_by")):
            db.rows = copy.deepcopy(complete); db.rows[table][-1][field] = "collision"; db.saved = copy.deepcopy(db.rows)
            with self.subTest(collision=table), self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
        db.rows = copy.deepcopy(complete); db.rows["deal"][-1]["id"] = str(uuid.UUID(int=99)); db.saved = copy.deepcopy(db.rows)
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)

    def test_refuses_bad_identity_and_rolls_back_database_failures(self):
        db = RelationshipConnection(); db.identity = ("carr_writer", "carr_writer", "neondb")
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
        self.assertEqual(db.writes, 0)
        db = RelationshipConnection(); db.fail_insert = True
        with self.assertRaises(RuntimeError): fixture.install_relationship_fixture(db, db.plan)
        self.assertEqual(db.rows, db.saved)
        self.assertEqual(db.rollbacks, 1)

    def test_refuses_missing_ambiguous_actors_and_failed_insert_readback(self):
        for actors in ([], [("system",), ("duplicate",)]):
            db = RelationshipConnection(); db.actor_rows = actors
            with self.subTest(actors=actors), self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
            self.assertEqual(db.writes, 0)
        db = RelationshipConnection(); db.omit_inserts = True
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
        self.assertEqual(db.rows, db.saved)
        self.assertEqual(db.commits, 0)

    def test_refuses_self_referral_plans_and_deterministic_identity_collisions(self):
        value = relationship_plan(); value["records"]["vendor_party"]["id"] = str(PARTY)
        value["receipts"]["vendor_party"]["party_id"] = str(PARTY)
        with self.assertRaises(fixture.FixtureRefusal): fixture.relationship_fixture_values(value)
        value = relationship_plan()
        with patch.object(fixture.uuid, "uuid5", return_value=PARTY), self.assertRaises(fixture.FixtureRefusal):
            fixture.relationship_fixture_values(value)

    def test_accepts_exact_existing_territory_values_without_updating_them(self):
        db = RelationshipConnection()
        db.rows["party"][0]["city"] = "Synthetic Staging QA North"
        db.rows["vendor"][0]["territory"] = "Synthetic Staging QA South"
        self.assertEqual(fixture.install_relationship_fixture(db, db.plan), 7)
        self.assertEqual(db.rows["party"][0]["version"], 1)
        self.assertEqual(db.rows["vendor"][0]["version"], 1)

    def test_projection_rejects_wrong_nodes_edges_notes_metrics_and_territory(self):
        mutations = [lambda n: n["nodes"].pop(), lambda n: n["nodes"][0].update(kind="vendor"),
                     lambda n: n["nodes"][0].update(record_id=str(DEAL)), lambda n: n["edges"].pop(),
                     lambda n: n["edges"][-1].update(via="party:" + str(PARTY)),
                     lambda n: n["edges"][-1].update(detail="changed"), lambda n: n.update(referrals=[]),
                     lambda n: n["nodes"][0].update(territory="Real City")]
        for mutate in mutations:
            db = RelationshipConnection(); fixture.install_relationship_fixture(db, db.plan)
            def change(n): mutate(n); return n
            db.project_mutation = change
            with self.subTest(mutation=mutations.index(mutate)), self.assertRaises(fixture.FixtureRefusal): fixture.read_relationship_fixture(db, db.plan)

    def test_projection_failure_before_commit_rolls_back_every_fixture_write(self):
        db = RelationshipConnection()
        def broken(n): n["referrals"] = []; return n
        db.project_mutation = broken
        with self.assertRaises(fixture.FixtureRefusal): fixture.install_relationship_fixture(db, db.plan)
        self.assertEqual(db.rows, db.saved)
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_outside_membership_matches_consumer_across_all_other_territories(self):
        db = RelationshipConnection(); fixture.install_relationship_fixture(db, db.plan)
        def additional(n):
            n["nodes"].append({"id": "party:another", "record_id": "another", "kind": "vendor", "territory": "West"})
            n["referrals"].append({"node_id": "party:another", "deals": 2, "won": 0, "lost": 0})
            return n
        db.project_mutation = additional
        projection = fixture.read_relationship_fixture(db, db.plan)["projection_evidence"]
        self.assertEqual(projection["other_territory_referrals"], 2)
        self.assertEqual(projection["total_referrals"], 3)

    def test_projection_runtime_refuses_and_never_exposes_output(self):
        for failure in (OSError("synthetic secret"), subprocess.TimeoutExpired("node", 30),
                        subprocess.CalledProcessError(1, "node", stderr="synthetic secret")):
            with patch.object(fixture.subprocess, "run", side_effect=failure), \
                    self.assertRaises(fixture.FixtureRefusal) as raised:
                fixture._relationship_projection("sql")
            self.assertNotIn("synthetic secret", str(raised.exception))


def mutate_relationship_assertions():
    source = pathlib.Path(__file__).read_text()
    tree = ast.parse(source)
    subject = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RelationshipFixtureTests")
    opposites = {"assertEqual": "assertNotEqual", "assertNotEqual": "assertEqual", "assertIn": "assertNotIn",
        "assertNotIn": "assertIn", "assertFalse": "assertTrue", "assertTrue": "assertFalse",
        "assert_not_called": "assert_called", "assertRaises": "assertRaises"}
    targets = [(method.name, node.lineno, node.col_offset, node.func.attr)
        for method in subject.body if isinstance(method, ast.FunctionDef)
        for node in ast.walk(method) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in opposites]
    survivors = []
    for method, line, column, assertion in targets:
        mutant = ast.parse(source)
        call = next(node for node in ast.walk(mutant) if isinstance(node, ast.Call)
                    and node.lineno == line and node.col_offset == column)
        call.func.attr = opposites[assertion]
        if assertion == "assertRaises":
            call.args[0] = ast.Name(id="AssertionError", ctx=ast.Load())
        ast.fix_missing_locations(mutant)
        namespace = {"__name__": "relationship_assertion_mutant", "__file__": __file__}
        exec(compile(mutant, __file__, "exec"), namespace)
        result = unittest.TestResult()
        namespace["RelationshipFixtureTests"](method).run(result)
        if result.wasSuccessful(): survivors.append(f"{method}:{line}:{assertion}")
    sys.modules[SPEC.name] = fixture
    print(f"Relationship assertion mutations killed={len(targets) - len(survivors)} total={len(targets)}")
    for survivor in survivors: print("SURVIVED " + survivor)
    return 1 if survivors else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--mutate-relationship-assertions"]:
        raise SystemExit(mutate_relationship_assertions())
    unittest.main()
