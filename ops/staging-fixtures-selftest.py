#!/usr/bin/env python3
"""Focused safety tests for the private-plan invoice staging fixture installer."""
from __future__ import annotations

import importlib.util
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
from types import SimpleNamespace
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
