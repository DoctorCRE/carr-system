"""Lifecycle tests for tools/pitr-restore-proof.py (V5-F08, the RPO proof).

The provider API and the database are replaced by in-memory fakes; the proof's
own code runs unchanged. What is held:
  * the metering admission runs before any branch POST, and a refusal means no
    POST is ever made;
  * every branch is created with a provider-side expiry (no stand-in mode);
  * the branch name is in the local state file BEFORE the create call, and a
    create interrupted by ANY BaseException (Ctrl-C included) is found by that
    unique name and deleted, the original interrupt then re-raised;
  * a delete counts only when the provider answers 404 afterwards, and a 423
    (an operation still running) is retried with backoff;
  * the sweep deletes prefixed-and-stale branches and state-listed ones, by id
    or, for an interrupted create, by name; never the default or a protected one;
  * teardown runs with SIGINT and SIGHUP ignored and restores them after;
  * teardown also ignores SIGTERM, and interrupts are BLOCKED from the create
    POST until the new id is in the teardown list (review H4);
  * prove always tears down the branches it created, on failure too, and asks
    for the branch with parent_id = production and parent_timestamp = T;
  * prove performs EVERY decisive read itself in one process (review H1): the
    probes and the parent point on the branch while it exists, production
    re-read, create and delete from the provider's OPERATIONS LOG, then binds;
    there is no verify mode and no file a caller could hand it.

  .venv/bin/python -m unittest tools/test_pitr_restore_proof.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib import recovery_evidence  # noqa: E402

spec = importlib.util.spec_from_file_location("pitr_restore_proof", REPO / "tools" / "pitr-restore-proof.py")
if spec is None or spec.loader is None:
    raise ImportError("cannot load tools/pitr-restore-proof.py")
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

P = f"/projects/{proof.PROJECT_ID}"


def ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeProvider:
    def __init__(self):
        self.branches = {
            "br-prod": {"id": "br-prod", "name": "production", "default": True, "created_at": ago(days=90)},
        }
        self.calls: list[tuple[str, str, dict | None]] = []
        self.post_raises: BaseException | None = None
        self.delete_sticks = False
        self.delete_locked = 0          # answer 423 this many times before accepting a delete
        self.counter = 0
        self.operations: list[dict] = []  # newest first, as the provider lists them
        self.page_size = 2

    def log(self, branch_id, action):
        self.operations.insert(0, {"id": f"op-{len(self.operations) + 1}", "branch_id": branch_id, "action": action,
                                   "status": "finished", "created_at": ago(seconds=0)})

    def api(self, method, path, body=None, query=None):
        self.calls.append((method, path, body))
        if method == "GET" and path == f"{P}/branches":
            return 200, {"branches": list(self.branches.values())}
        if method == "GET" and path == P:
            return 200, {"project": {"history_retention_seconds": 604800}}
        if method == "GET" and path == f"{P}/operations":
            start = int((query or {}).get("cursor") or 0)
            page = self.operations[start:start + self.page_size]
            nxt = start + self.page_size
            return 200, {"operations": page, "pagination": {"cursor": str(nxt) if nxt < len(self.operations) else ""}}
        if method == "POST" and path == f"{P}/branches":
            self.counter += 1
            new = {"id": f"br-new-{self.counter}", "name": body["branch"]["name"], "default": False,
                   "created_at": ago(seconds=0), "parent_id": body["branch"]["parent_id"],
                   "parent_timestamp": body["branch"].get("parent_timestamp"), "parent_lsn": "0/1A2B3C4D",
                   "current_state": "ready"}
            self.branches[new["id"]] = new
            self.log(new["id"], "create_branch")
            if self.post_raises is not None:
                raise self.post_raises
            return 201, {"branch": new}
        if path.startswith(f"{P}/branches/"):
            bid = path.rsplit("/", 1)[1]
            if method == "DELETE":
                if self.delete_locked:
                    self.delete_locked -= 1
                    return 423, {"message": "branch has running operations"}
                if bid in self.branches and not self.delete_sticks:
                    del self.branches[bid]
                    self.log(bid, "delete_timeline")
                return 200, {}
            return (200, {"branch": self.branches[bid]}) if bid in self.branches else (404, {})
        raise AssertionError(f"unexpected {method} {path}")

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]

    def deletes(self):
        return [c for c in self.calls if c[0] == "DELETE"]


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeProvider()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / "state.json"
        for target, value in (("api", self.fake.api), ("STATE", state), ("say", lambda _m: None)):
            patcher = mock.patch.object(proof, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.slept: list[float] = []
        sleep = mock.patch.object(proof.time, "sleep", self.slept.append)
        sleep.start()
        self.addCleanup(sleep.stop)

    def admission(self, returncode=0):
        result = mock.Mock(returncode=returncode, stderr="refused: too many branches")
        return mock.patch.object(proof.subprocess, "run", return_value=result)


class Lifecycle(Base):
    def test_admission_refusal_means_no_post(self):
        with self.admission(77):
            with self.assertRaisesRegex(proof.ProofError, "metering admission refused"):
                proof.create_branch("pitr-proof-x", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"}, [])
        self.assertEqual(self.fake.posts(), [])

    def test_create_asks_for_production_at_T_with_an_expiry_and_records_the_name_first(self):
        seen_state = []
        real_api = self.fake.api

        def spying(method, path, body=None, query=None):
            if method == "POST":
                seen_state.append(json.loads(proof.STATE.read_text()))
            return real_api(method, path, body, query)

        with self.admission(), mock.patch.object(proof, "api", spying):
            branch = proof.create_branch("pitr-proof-x", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"}, [])
        self.assertEqual(seen_state[0][0]["name"], "pitr-proof-x")
        body = self.fake.posts()[0][2]
        self.assertEqual(body["branch"]["parent_id"], "br-prod")
        self.assertEqual(body["branch"]["parent_timestamp"], "2026-09-24T12:00:00Z")
        expires = datetime.fromisoformat(body["branch"]["expires_at"].replace("Z", "+00:00"))
        self.assertLessEqual(expires - datetime.now(timezone.utc), timedelta(minutes=proof.LIFETIME_MINUTES))
        self.assertEqual(json.loads(proof.STATE.read_text())[0]["id"], branch["id"])

    def test_the_stand_in_mode_is_gone(self):
        self.assertFalse(hasattr(proof, "STAND_IN_OUT"))
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                proof.main(["prove", "--stand-in-parent"])
        wrapper = (REPO / "bin" / "pitr-restore-proof.sh").read_text()
        self.assertNotIn("MODE_ARGS", wrapper)

    def test_an_ambiguous_create_is_found_by_name_and_deleted(self):
        self.fake.post_raises = TimeoutError("response lost")
        with self.admission():
            with self.assertRaisesRegex(proof.ProofError, "branch create failed"):
                proof.create_branch("pitr-proof-lost", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"}, [])
        self.assertEqual([b["name"] for b in self.fake.branches.values()], ["production"])

    def test_a_create_interrupted_by_ctrl_c_is_found_by_name_deleted_and_the_interrupt_re_raised(self):
        for interrupt in (KeyboardInterrupt(), SystemExit(130)):
            self.fake.post_raises = interrupt
            with self.admission():
                with self.assertRaises(type(interrupt)):
                    proof.create_branch("pitr-proof-cc", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"}, [])
            self.assertEqual([b["name"] for b in self.fake.branches.values()], ["production"])

    def test_a_create_interrupted_while_the_provider_is_unreachable_leaves_the_name_for_the_sweep(self):
        self.fake.post_raises = KeyboardInterrupt()
        real_api = self.fake.api

        def down_after_post(method, path, body=None, query=None):
            if method == "GET" and self.fake.posts():
                raise OSError("network down")
            return real_api(method, path, body, query)

        with self.admission(), mock.patch.object(proof, "api", down_after_post):
            with self.assertRaises(KeyboardInterrupt):
                proof.create_branch("pitr-proof-orphan", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"}, [])
        self.assertEqual(json.loads(proof.STATE.read_text()), [
            {"name": "pitr-proof-orphan", "id": None, "requested_at": mock.ANY}])
        # The next run's sweep finds it by that recorded name even though it is young.
        proof.sweep({"br-prod"})
        self.assertEqual(list(self.fake.branches), ["br-prod"])
        self.assertEqual(json.loads(proof.STATE.read_text()), [])

    def test_delete_counts_only_on_a_404_readback_and_retries_a_423(self):
        self.fake.branches["br-x"] = {"id": "br-x", "name": "pitr-proof-x", "default": False, "created_at": ago(seconds=5)}
        self.fake.delete_sticks = True
        self.assertFalse(proof.delete_and_confirm("br-x", {"br-prod"}))
        self.fake.delete_sticks = False
        self.fake.delete_locked = 3
        self.slept.clear()
        self.assertTrue(proof.delete_and_confirm("br-x", {"br-prod"}))
        self.assertEqual(len(self.fake.deletes()), 1 + 4)  # the sticky one, then three 423s and the accepted one
        self.assertEqual(self.slept, [1, 2, 4])            # exponential backoff between 423s; 404 at once after
        self.fake.branches["br-y"] = {"id": "br-y", "name": "pitr-proof-y", "default": False, "created_at": ago(seconds=5)}
        self.fake.delete_locked = proof.DELETE_RETRIES
        self.assertFalse(proof.delete_and_confirm("br-y", {"br-prod"}))  # still locked after every retry
        with self.assertRaises(proof.ProofError):
            proof.delete_and_confirm("br-prod", {"br-prod"})

    def test_sweep_deletes_only_stale_proof_branches_and_state_listed_ones(self):
        self.fake.branches.update({
            "br-old": {"id": "br-old", "name": "pitr-proof-20260101", "default": False, "created_at": ago(hours=2)},
            "br-young": {"id": "br-young", "name": "pitr-proof-20260924", "default": False, "created_at": ago(minutes=5)},
            "br-dev": {"id": "br-dev", "name": "dev-work", "default": False, "created_at": ago(hours=5)},
            "br-listed": {"id": "br-listed", "name": "odd-name", "default": False, "created_at": ago(minutes=1)},
            "br-named": {"id": "br-named", "name": "pitr-proof-interrupted", "default": False, "created_at": ago(minutes=1)},
            "br-guard": {"id": "br-guard", "name": "pitr-proof-guard", "default": False, "protected": True, "created_at": ago(hours=9)},
        })
        proof.save_state([{"id": "br-listed", "name": "odd-name"}, {"id": None, "name": "pitr-proof-interrupted"},
                          {"id": "br-dev-lookalike", "name": "dev-work"}])
        proof.sweep({"br-prod"})
        # a row WITH an id is swept by that id only, never by a name another branch happens to share
        self.assertEqual(sorted(self.fake.branches), ["br-dev", "br-guard", "br-prod", "br-young"])

    def test_teardown_ignores_sigint_and_sighup_and_restores_them(self):
        original = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGHUP)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in original.items()])
        # Known handlers first: a background test runner may start with SIGINT already ignored.
        before = {signal.SIGINT: proof._interrupt, signal.SIGHUP: proof._interrupt}
        for s, h in before.items():
            signal.signal(s, h)
        with proof.teardown_signals_deferred():
            seen = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGHUP)}
        self.assertEqual(seen, {signal.SIGINT: signal.SIG_IGN, signal.SIGHUP: signal.SIG_IGN})
        self.assertEqual({s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGHUP)}, before)

    def test_sighup_exits_129_and_sigint_130(self):
        with self.assertRaises(SystemExit) as hup:
            proof._interrupt(signal.SIGHUP, None)
        self.assertEqual(hup.exception.code, 129)
        with self.assertRaises(SystemExit) as intr:
            proof._interrupt(signal.SIGINT, None)
        self.assertEqual(intr.exception.code, 130)


class OperationsLog(Base):
    def test_create_and_delete_are_read_from_the_log_across_pages_and_bounded_by_the_proof_start(self):
        start = ago(minutes=1)
        self.fake.operations = [
            {"id": "op-9", "branch_id": "br-a", "action": "delete_timeline", "status": "finished", "created_at": ago(seconds=10)},
            {"id": "op-8", "branch_id": "br-other", "action": "create_branch", "status": "finished", "created_at": ago(seconds=20)},
            {"id": "op-7", "branch_id": "br-a", "action": "create_branch", "status": "finished", "created_at": ago(seconds=30)},
            {"id": "op-6", "branch_id": "br-a", "action": "create_branch", "status": "finished", "created_at": ago(hours=3)},
        ]
        ops = proof.operations_since(start, "br-a")
        self.assertEqual([o["id"] for o in ops], ["op-9", "op-7", "op-6"])
        self.assertEqual(proof.confirmed_operation(ops, "create_branch", start), "op-7")  # op-6 predates the proof
        self.assertEqual(proof.confirmed_operation(ops, "delete_timeline", start), "op-9")
        # an unfinished operation, or none at all (an invented id), confirms nothing
        self.fake.operations[0]["status"] = "running"
        self.assertIsNone(proof.confirmed_operation(proof.operations_since(start, "br-a"), "delete_timeline", start))
        self.assertEqual(proof.operations_since(start, "br-invented"), [])
        # two finished creates for one id since the start is ambiguous
        dup = [*ops, {"id": "op-10", "branch_id": "br-a", "action": "create_branch", "status": "finished", "created_at": ago(seconds=5)}]
        self.assertIsNone(proof.confirmed_operation(dup, "create_branch", start))

    def test_the_lifecycle_confirms_nothing_for_an_invented_id_or_a_branch_that_still_answers(self):
        start = ago(minutes=1)
        self.fake.operations = [
            {"id": "op-2", "branch_id": "br-a", "action": "delete_timeline", "status": "finished", "created_at": ago(seconds=5)},
            {"id": "op-1", "branch_id": "br-a", "action": "create_branch", "status": "finished", "created_at": ago(seconds=20)},
        ]
        # an id the log never saw answers 404 like a deleted branch, but confirms nothing
        self.assertEqual(proof.confirm_lifecycle("br-invented", start, attempts=1), {"create": None, "delete": None})
        # a logged delete does not count while the provider still returns the branch
        self.fake.branches["br-a"] = {"id": "br-a", "name": "back", "default": False, "created_at": ago(seconds=1)}
        self.assertEqual(proof.confirm_lifecycle("br-a", start, attempts=1), {"create": "op-1", "delete": None})
        del self.fake.branches["br-a"]
        self.assertEqual(proof.confirm_lifecycle("br-a", start, attempts=1), {"create": "op-1", "delete": "op-2"})

    def test_a_log_that_never_reaches_back_to_the_proof_start_is_refused(self):
        self.fake.operations = [{"id": f"op-{i}", "branch_id": "br-x", "action": "noop", "status": "finished",
                                 "created_at": ago(seconds=1)} for i in range(proof.OPERATIONS_MAX_PAGES * 2 + 2)]
        with self.assertRaisesRegex(proof.ProofError, "did not reach back"):
            proof.operations_since(ago(hours=1), "br-a")


class ProveAndVerify(Base):
    def fake_db(self, *, branch_rows, production_rows=None, clock_offset=0.0):
        """A connect() whose sessions answer the proof's queries; the probe writes return server-stamped rows."""
        clock = {"t": time.time() - 120 + clock_offset}  # tracks the real clock, as a server would
        written = {}

        class Conn:
            def __init__(self, uri, read_only):
                self.uri, self.read_only = uri, read_only

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(inner, sql, params=None):
                res = mock.Mock()
                if "clock_timestamp()" in sql and "write_pitr_probe" not in sql:
                    clock["t"] += 7.3
                    res.fetchone.return_value = (clock["t"],)
                elif "write_pitr_probe" in sql:
                    assert not inner.read_only
                    role = params[0]
                    iso = datetime.fromtimestamp(clock["t"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    row = (f"00000000-0000-4000-8000-00000000000{len(written) + 1}", ("a" if role == "positive" else "b") * 32, iso)
                    written[role] = row
                    res.fetchone.return_value = row
                elif "from ops.pitr_probe" in sql:
                    assert inner.read_only
                    if "branch" in inner.uri:
                        rows = branch_rows(written)
                    else:
                        rows = (production_rows or (lambda w: list(w.values())))(written)
                    res.fetchall.return_value = rows
                elif "count(*)" in sql:
                    res.fetchone.return_value = (12,)
                return res

        return lambda uri, read_only, attempts=40: Conn(uri, read_only)

    def run_prove(self, branch_rows, now=None, production_rows=None, branch_host="branch-{bid}-host", clock_offset=0.0):
        uris = {"br-prod": "postgresql://u@prod-host/neondb"}
        self.db = self.fake_db(branch_rows=branch_rows, production_rows=production_rows, clock_offset=clock_offset)
        with self.admission(), \
             mock.patch.object(proof, "connection_uri", lambda bid: uris.get(bid, f"postgresql://u@{branch_host.format(bid=bid)}/neondb?branch")), \
             mock.patch.object(proof, "connect", self.db):
            return proof.prove(now)

    def test_prove_reads_everything_itself_and_returns_bound_evidence(self):
        before = int(datetime.now(timezone.utc).timestamp())
        now = datetime(2026, 9, 24, 12, 30, tzinfo=timezone.utc)
        ev = self.run_prove(lambda w: [w["positive"]], now)
        facts = {k: v for k, v in ev.items() if k != "verification"}
        self.assertEqual(ev["verification"], {"verifier": "tools/pitr-restore-proof.py prove",
                                              "verified_at": "2026-09-24T12:30:00Z",
                                              "facts_digest": recovery_evidence.facts_digest(facts)})
        self.assertEqual(ev["production_branch_id"], "br-prod")
        self.assertEqual(ev["branch_parent_id"], "br-prod")
        self.assertEqual(ev["branch_parent_timestamp"], ev["requested_parent_timestamp"])
        self.assertEqual(ev["branch_parent_lsn"], "0/1A2B3C4D")
        pos_epoch = proof.epoch_of(ev["positive_probe"]["written_at"])
        t_epoch = proof.epoch_of(ev["requested_parent_timestamp"])
        neg_epoch = proof.epoch_of(ev["negative_probe"]["written_at"])
        self.assertGreaterEqual(t_epoch - pos_epoch, 60)
        self.assertGreaterEqual(neg_epoch - t_epoch, 5)
        # probes and core rows were read ON THE BRANCH, while it existed
        self.assertEqual(ev["positive_on_branch"], ev["positive_probe"])
        self.assertFalse(ev["negative_present_on_branch"])
        self.assertEqual(ev["branch_core_table_rows"], {t: 12 for t in proof.CORE_TABLES})
        # the lifecycle as the provider LOGGED it, bounded by this proof's start
        ops = ev["branch_operations"]
        logged = {o["id"]: (o["branch_id"], o["action"]) for o in self.fake.operations}
        self.assertEqual(logged[ops["create"]], (ev["branch_id"], "create_branch"))
        self.assertEqual(logged[ops["delete"]], (ev["branch_id"], "delete_timeline"))
        self.assertGreaterEqual(proof.epoch_of(self.fake.operations[-1]["created_at"]), before - 1)
        # production re-read (read-only) and retention
        self.assertEqual(ev["production_readback"]["positive"], ev["positive_probe"])
        self.assertEqual(ev["production_readback"]["negative"], ev["negative_probe"])
        self.assertEqual(ev["history_retention_seconds"], 604800)
        self.assertEqual(list(self.fake.branches), ["br-prod"])  # torn down
        self.assertEqual(json.loads(proof.STATE.read_text()), [])

    def test_a_negative_probe_leaked_onto_the_branch_is_recorded_as_seen(self):
        ev = self.run_prove(lambda w: list(w.values()))
        self.assertTrue(ev["negative_present_on_branch"])

    def test_the_parent_point_is_the_providers_readback_not_the_request(self):
        real_api = self.fake.api

        def drifting(method, path, body=None, query=None):
            status, payload = real_api(method, path, body, query)
            if method == "GET" and path.startswith(f"{P}/branches/br-new-") and status == 200:
                payload = {"branch": {**payload["branch"], "parent_timestamp": "2026-09-24T11:00:00.5Z"}}
            return status, payload

        with mock.patch.object(proof, "api", drifting):
            ev = self.run_prove(lambda w: [w["positive"]])
        self.assertEqual(ev["branch_parent_timestamp"], "2026-09-24T11:00:00.500000Z")
        self.assertNotEqual(ev["branch_parent_timestamp"], ev["requested_parent_timestamp"])

    def test_the_production_readback_is_a_fresh_read_not_the_written_rows(self):
        ev = self.run_prove(lambda w: [w["positive"]], production_rows=lambda w: [w["negative"]])
        self.assertIsNone(ev["production_readback"]["positive"])
        self.assertEqual(ev["production_readback"]["negative"], ev["negative_probe"])

    def test_a_branch_that_resolves_to_the_parents_host_is_refused_and_torn_down(self):
        with self.assertRaisesRegex(proof.ProofError, "parent's host"):
            self.run_prove(lambda w: [w["positive"]], branch_host="prod-host")
        self.assertEqual(list(self.fake.branches), ["br-prod"])

    def test_k4_a_local_clock_skewed_from_the_servers_is_refused_and_torn_down(self):
        for offset in (3600.0, -3600.0):
            with self.assertRaisesRegex(proof.ProofError, "local clock differs from the production server"):
                self.run_prove(lambda w: [w["positive"]], clock_offset=offset)
            self.assertEqual(list(self.fake.branches), ["br-prod"])
        self.run_prove(lambda w: [w["positive"]], clock_offset=100.0)  # inside the limit

    def test_a_branch_that_survives_teardown_confirms_no_delete(self):
        self.fake.delete_sticks = True
        ev = self.run_prove(lambda w: [w["positive"]])
        self.assertTrue(ev["branch_operations"]["create"])
        self.assertIsNone(ev["branch_operations"]["delete"])

    def test_prove_tears_down_even_when_a_check_raises(self):
        with mock.patch.object(proof, "read_back_ready", side_effect=proof.ProofError("boom")):
            with self.assertRaises(proof.ProofError):
                self.run_prove(lambda w: [w["positive"]])
        self.assertEqual(list(self.fake.branches), ["br-prod"])

    def test_prove_tears_down_on_ctrl_c_too(self):
        with mock.patch.object(proof, "read_probes", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.run_prove(lambda w: [w["positive"]])
        self.assertEqual(list(self.fake.branches), ["br-prod"])

    def test_no_caller_file_or_mode_can_feed_the_verdict(self):
        # There is no verify mode and no file argument (review H1).
        for argv in (["verify", "obs.json"], ["verify"], ["prove", "obs.json"]):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                proof.main(argv)
        self.assertFalse(hasattr(proof, "verify"))
        # A doctored evidence file sitting at OUT has no influence: prove never reads it.
        out = Path(self.tmp.name) / "pitr-proof.json"
        doctored = {"source": "pitr_branch_proof", "negative_present_on_branch": False, "doctored": True}
        out.write_text(json.dumps(doctored))
        printed = []
        self.db = self.fake_db(branch_rows=lambda w: list(w.values()))
        uris = {"br-prod": "postgresql://u@prod-host/neondb"}
        saved = {s: signal.getsignal(s) for s in proof.INTERRUPTS}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in saved.items()])
        with self.admission(), mock.patch.object(proof, "OUT", out), \
             mock.patch.object(proof, "connection_uri", lambda bid: uris.get(bid, f"postgresql://u@branch-{bid}-host/neondb")), \
             mock.patch.object(proof, "connect", self.db), \
             mock.patch("builtins.print", lambda *a, **k: printed.append(a[0])):
            self.assertEqual(proof.main(["prove"]), 0)
        ev = json.loads(printed[0])
        self.assertNotIn("doctored", ev)
        self.assertTrue(ev["negative_present_on_branch"])  # the branch's own answer, not the file's
        self.assertEqual(json.loads(out.read_text()), ev)  # OUT is overwritten, only ever written


class InterruptWindows(Base):
    """Review H4: no signal can land between the branch existing and teardown knowing it."""

    def setUp(self):
        super().setUp()
        saved = {s: signal.getsignal(s) for s in proof.INTERRUPTS}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in saved.items()])
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
        self.addCleanup(lambda: signal.pthread_sigmask(signal.SIG_SETMASK, mask))

    def test_interrupts_are_blocked_during_the_create_and_released_after(self):
        during = []
        real_api = self.fake.api

        def spying(method, path, body=None, query=None):
            if method == "POST":
                during.append(signal.pthread_sigmask(signal.SIG_BLOCK, []))
            return real_api(method, path, body, query)

        created: list[str] = []
        with self.admission(), mock.patch.object(proof, "api", spying):
            proof.create_branch("pitr-proof-m", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"}, created)
        self.assertTrue(set(proof.INTERRUPTS) <= during[0])
        self.assertFalse(set(proof.INTERRUPTS) & signal.pthread_sigmask(signal.SIG_BLOCK, []))
        self.assertEqual(created, ["br-new-1"])

    def test_a_sigterm_during_the_create_lands_only_after_the_id_is_in_created(self):
        class Term(BaseException):
            pass

        def raise_term(_s, _f):
            raise Term()

        signal.signal(signal.SIGTERM, raise_term)
        real_api = self.fake.api

        def killed(method, path, body=None, query=None):
            if method == "POST":
                os.kill(os.getpid(), signal.SIGTERM)  # pending, not delivered, while blocked
            return real_api(method, path, body, query)

        created: list[str] = []
        with self.admission(), mock.patch.object(proof, "api", killed):
            with self.assertRaises(Term):
                proof.create_branch("pitr-proof-t", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"}, created)
        self.assertEqual(created, ["br-new-1"])  # the teardown list already holds it
        self.assertEqual(json.loads(proof.STATE.read_text())[0]["id"], "br-new-1")

    def test_teardown_ignores_sigterm_too(self):
        for s in proof.INTERRUPTS:
            signal.signal(s, proof._interrupt)
        self.assertIn(signal.SIGTERM, proof.teardown_signals_deferred.SIGNALS)
        with proof.teardown_signals_deferred():
            self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)
        self.assertIs(signal.getsignal(signal.SIGTERM), proof._interrupt)


def _pg_bins():
    names = ("initdb", "pg_ctl", "psql", "postgres")
    for d in [Path(p).parent for p in filter(None, [shutil.which("postgres")])] + [
            Path("/opt/homebrew/opt/postgresql@17/bin"), Path("/usr/local/opt/postgresql@17/bin"),
            Path("/usr/lib/postgresql/17/bin"), Path("/usr/lib/postgresql/16/bin")]:
        if all((d / n).exists() for n in names):
            return {n: str(d / n) for n in names}
    return None


@unittest.skipUnless(_pg_bins(), "PostgreSQL server binaries not installed")
class Migration0597OnADisposableCluster(unittest.TestCase):
    """G5: the table itself stamps id, nonce and write instant; a direct owner insert cannot choose them."""

    @classmethod
    def setUpClass(cls):
        cls.pg_budget = postgres_fixture_group()
        cls.pg_budget.__enter__()
        cls.addClassCleanup(cls.pg_budget.__exit__, None, None, None)
        cls.bins = _pg_bins()
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        cls.fixture = DisposablePostgres("carr-f08-0597-", cls.bins["pg_ctl"], env=env)
        cls.addClassCleanup(cls.fixture.close)
        cls.tmp = cls.fixture.root
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = str(s.getsockname()[1])
        cls.fixture.run([cls.bins["initdb"], "-D", str(cls.tmp / "data"), "-U", "owner", "--auth=trust", "--locale=C"],
                       check=True, capture_output=True, env=env)
        cls.fixture.run([cls.bins["pg_ctl"], "-D", str(cls.tmp / "data"), "-l", str(cls.tmp / "log"), "-w", "-o",
                        f"-p {cls.port} -k {cls.tmp} -c listen_addresses=''", "start"], check=True, capture_output=True, env=env)
        cls.sql("create schema ops; create extension pgcrypto schema public; "
                "create role carr_reader; create role carr_writer; create role carr_jobs; create role carr_authority;")
        cls.sql((REPO / "migrations" / "0597_pitr_probe.sql").read_text())

    @classmethod
    def sql(cls, text, check=True):
        return subprocess.run([cls.bins["psql"], "-h", str(cls.tmp), "-p", cls.port, "-U", "owner", "-d", "postgres",
                               "-v", "ON_ERROR_STOP=1", "-Atq", "-c", text], capture_output=True, text=True, check=check)

    def test_a_direct_insert_cannot_choose_the_id_nonce_or_write_instant(self):
        chosen_id, chosen_nonce = "00000000-0000-4000-8000-000000000042", "c" * 32
        row = self.sql(f"insert into ops.pitr_probe (id, nonce, role, written_at) values "
                       f"('{chosen_id}', '{chosen_nonce}', 'positive', '2000-01-01 00:00:00+00') "
                       "returning id, nonce, written_at > now() - interval '1 minute'").stdout.strip().split("|")
        self.assertNotEqual(row[0], chosen_id)
        self.assertNotEqual(row[1], chosen_nonce)
        self.assertRegex(row[1], r"^[0-9a-f]{32}$")
        self.assertEqual(row[2], "t")  # not backdated to 2000

    def test_the_function_still_writes_and_rows_stay_append_only(self):
        row = self.sql("select nonce, role from ops.write_pitr_probe('negative')").stdout.strip().split("|")
        self.assertRegex(row[0], r"^[0-9a-f]{32}$")
        self.assertEqual(row[1], "negative")
        for statement in ("update ops.pitr_probe set role = 'positive'", "delete from ops.pitr_probe",
                          "truncate ops.pitr_probe"):
            got = self.sql(statement, check=False)
            self.assertNotEqual(got.returncode, 0, statement)
            self.assertIn("append-only", got.stderr)
        self.assertNotEqual(self.sql("select ops.write_pitr_probe('sideways')", check=False).returncode, 0)


class Formatting(unittest.TestCase):
    def test_utc_exact_keeps_fractions_and_refuses_empty(self):
        self.assertEqual(proof.utc_exact("2026-09-24T12:00:00Z"), "2026-09-24T12:00:00Z")
        self.assertEqual(proof.utc_exact("2026-09-24T07:00:00.5-05:00"), "2026-09-24T12:00:00.500000Z")
        with self.assertRaises(proof.ProofError):
            proof.utc_exact("")


if __name__ == "__main__":
    unittest.main()
