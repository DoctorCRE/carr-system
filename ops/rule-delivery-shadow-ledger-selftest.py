#!/usr/bin/env python3
"""File-level acceptance for the sanctioned append-only shadow ledger door."""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import multiprocessing
import sys
import tempfile
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "ledger", REPO / "ops/rule-delivery-shadow-ledger.py")
assert spec and spec.loader
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)
gate_spec = importlib.util.spec_from_file_location(
    "rule_pack_drift_gate_race", REPO / "hooks/rule-pack-drift-gate.py")
assert gate_spec and gate_spec.loader
gate = importlib.util.module_from_spec(gate_spec)
gate_spec.loader.exec_module(gate)

identity = {"policy_digest": "1" * 64, "map_digest": "2" * 64,
            "source_digest": "3" * 64}
observation = {"ts": "2026-08-26T01:00:00Z", "hook": "rule-pack-drift-gate",
               "session": "s1", "mode": "shadow", "loaded": [],
               "would_omit_count": 4, "missed_rules": ["deadbeef"]}
raw = json.dumps(observation, separators=(",", ":"))
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "shadow.jsonl"
    path.write_text(raw + "\n", encoding="utf-8")
    event_id = ledger.list_findings(path)[0]["event_id"]
    args = Namespace(event_id=event_id, disposition="explained", owner="owner-a",
                     remedy_ref="WR-000007", evidence_ref="INC-1",
                     rollback_ref="retain raw")
    receipt = ledger.add_disposition(path, args)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == raw, "raw observation was rewritten"
    assert receipt["event_id"] == event_id and receipt["owner"] == "owner-a"
    assert len(lines) == 2, "disposition was not one append"
    try:
        ledger.add_disposition(path, args)
    except RuntimeError as exc:
        assert "duplicate-disposition" in str(exc)
    else:
        raise AssertionError("duplicate disposition was accepted")

    def add_epoch(rows):
        allowed, reason = ledger.can_start_epoch(rows, identity)
        if not allowed:
            raise ValueError(reason)
        value = ledger.make_epoch(identity, owner="owner-a", reason="initial epoch",
                                  remedy_ref="WR-000007", rollback_ref="retain prior")
        ledger.validate_epoch_append(rows, value)
        return value

    epoch = ledger.append(path, add_epoch)
    assert epoch["record_type"] == "epoch"
    assert path.read_text(encoding="utf-8").splitlines()[0] == raw

    # Every authority field and digest is validated before any append.
    for field in ("owner", "reason", "remedy_ref", "rollback_ref"):
        values = {"owner": "o", "reason": "r", "remedy_ref": "m",
                  "rollback_ref": "b"}
        values[field] = ""
        try:
            ledger.make_epoch(identity, **values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"empty epoch {field} was accepted")
    for field in ("owner", "remedy_ref", "evidence_ref", "rollback_ref"):
        values = {"owner": "o", "remedy_ref": "m", "evidence_ref": "e",
                  "rollback_ref": "b"}
        values[field] = ""
        try:
            ledger.make_disposition("a" * 64, "explained", **values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"empty disposition {field} was accepted")
    try:
        ledger.make_disposition("not-a-digest", "explained", owner="o",
                                remedy_ref="m", evidence_ref="e", rollback_ref="b")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid disposition event id was accepted")

    future = {**observation, "missed_rules": [],
              "ts": (datetime.now(timezone.utc) + timedelta(hours=1))
              .strftime("%Y-%m-%dT%H:%M:%SZ")}
    future_path = Path(directory) / "future.jsonl"
    future_path.write_text(json.dumps(future) + "\n", encoding="utf-8")
    try:
        ledger.append(future_path, add_epoch)
    except ValueError as exc:
        assert "earlier than prior" in str(exc)
    else:
        raise AssertionError("epoch reset past a future prior row")

    # A newly created ledger fsyncs both its append FD and containing directory.
    import lib.rule_delivery_shadow as shadow
    calls: list[object] = []
    real_fsync = shadow.os.fsync
    shadow.os.fsync = lambda fd: calls.append(fd)
    try:
        durable_path = Path(directory) / "durable.jsonl"
        shadow.append_locked(durable_path, lambda _rows: observation)
    finally:
        shadow.os.fsync = real_fsync
    assert len(calls) == 2 and durable_path.read_text().count("\n") == 1

    ordered_path = Path(directory) / "ordered.jsonl"
    base_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    ordered_epoch = ledger.make_epoch(
        identity, owner="o", reason="ordered", remedy_ref="m", rollback_ref="b",
        at=base_time)
    ledger.append(ordered_path, lambda _rows: ordered_epoch)
    later = gate.make_observation(
        session="later", map_digest=identity["map_digest"],
        source_digest=identity["source_digest"], at=base_time + timedelta(minutes=2),
        result={"mode": "shadow", "needed": [], "loaded": ["engineering-git"],
                "missing": [], "triggers": {}, "would_omit_count": 1,
                "missed_rules": []})
    reverse = gate.make_observation(
        session="reverse", map_digest=identity["map_digest"],
        source_digest=identity["source_digest"], at=base_time + timedelta(minutes=1),
        result={"mode": "shadow", "needed": [], "loaded": ["engineering-git"],
                "missing": [], "triggers": {}, "would_omit_count": 1,
                "missed_rules": []})
    ledger.append(ordered_path, lambda _rows: later)
    try:
        ledger.append(ordered_path, lambda _rows: reverse)
    except ValueError as exc:
        assert "ledger order" in str(exc)
    else:
        raise AssertionError("append door accepted a reverse-order observation")
    state = ledger.inspect([ordered_epoch, later, reverse])
    assert any("reverse-order strict timestamp" in error for error in state["errors"])

    # Simultaneous hook miss vs epoch is serialized: an epoch can only win
    # before the miss, never reset past an undispositioned miss.
    def hook_writer(target, start):
        gate.LOG = str(target)
        start.wait()
        miss = gate.make_observation(
            session="race", map_digest=identity["map_digest"],
            source_digest=identity["source_digest"],
            result={"mode": "shadow", "needed": ["engineering-git"],
                    "loaded": [], "missing": ["engineering-git"],
                    "triggers": {"engineering-git": ["git"]},
                    "would_omit_count": 1, "missed_rules": ["deadbeef"]})
        gate.audit(miss)

    def epoch_writer(target, start):
        start.wait()
        try:
            ledger.append(target, add_epoch)
        except (RuntimeError, ValueError):
            pass

    ctx = multiprocessing.get_context("fork")
    for attempt in range(8):
        race_path = Path(directory) / f"race-{attempt}.jsonl"
        start = ctx.Event()
        hook_process = ctx.Process(target=hook_writer, args=(race_path, start))
        epoch_process = ctx.Process(target=epoch_writer, args=(race_path, start))
        hook_process.start(); epoch_process.start(); start.set()
        hook_process.join(5); epoch_process.join(5)
        assert hook_process.exitcode == 0 and epoch_process.exitcode == 0
        rows = [json.loads(line) for line in race_path.read_text().splitlines()]
        kinds = [row.get("record_type") for row in rows]
        assert tuple(kinds) in (
            ("observation",), ("epoch",), ("epoch", "observation")), kinds
        if "observation" in kinds and "epoch" in kinds:
            assert kinds.index("epoch") < kinds.index("observation"), kinds

    # Deterministic cutover boundary: a finding writer already inside the
    # exclusive append transaction completes before the cutover shared read.
    boundary_path = Path(directory) / "cutover-boundary.jsonl"
    inside, release = ctx.Event(), ctx.Event()
    result_queue = ctx.Queue()

    def slow_finding_writer(target, entered, proceed):
        def build(_rows):
            entered.set(); proceed.wait(5)
            return gate.make_observation(
                session="before-write", map_digest=identity["map_digest"],
                source_digest=identity["source_digest"],
                result={"mode": "shadow", "needed": ["engineering-git"],
                        "loaded": [], "missing": ["engineering-git"],
                        "triggers": {"engineering-git": ["git"]},
                        "would_omit_count": 1, "missed_rules": ["deadbeef"]})
        ledger.append(target, build)

    def cutover_reader(target, entered, result):
        entered.wait(5)
        with ledger.locked_read(target) as rows:
            result.put(any(ledger.finding(row) for row in rows))

    writer = ctx.Process(target=slow_finding_writer,
                         args=(boundary_path, inside, release))
    reader = ctx.Process(target=cutover_reader,
                         args=(boundary_path, inside, result_queue))
    writer.start(); assert inside.wait(5); reader.start()
    reader.join(.2)
    assert reader.is_alive(), "cutover read crossed an in-flight append lock"
    release.set(); writer.join(5); reader.join(5)
    assert writer.exitcode == 0 and reader.exitcode == 0
    assert result_queue.get(timeout=1) is True

    try:
        ledger.epoch_identity(("enforced", "x", "y", datetime.now(timezone.utc)))
    except ledger.LedgerRefusal as exc:
        assert str(exc) == "live-policy-is-not-shadow"
    else:
        raise AssertionError("enforced policy was allowed to start an epoch")

# Arbitrary provider/auth failures never reach stdout, stderr, or a traceback.
secret = "postgresql://user:SUPER-SECRET@example.invalid/db"  # ci-secret-scan: allow
old_argv = sys.argv
old_identity = getattr(ledger, "live_identity")
setattr(ledger, "live_identity", lambda: (_ for _ in ()).throw(RuntimeError(secret)))
sys.argv = ["ledger", "--log", "/tmp/not-written-shadow-test.jsonl", "start-epoch",
            "--owner", "o", "--reason", "r", "--remedy-ref", "m",
            "--rollback-ref", "b"]
stdout, stderr = io.StringIO(), io.StringIO()
try:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = ledger.main()
finally:
    setattr(ledger, "live_identity", old_identity)
    sys.argv = old_argv
rendered = stdout.getvalue() + stderr.getvalue()
assert rc == 1 and secret not in rendered and "Traceback" not in rendered

wrapper = (REPO / "bin/rule-delivery-shadow-ledger-prod.sh").read_text()
assert "carr_load_routine_db_env CARR_DB_JOBS_URL" in wrapper
assert "CARR_DB_JOBS_URL=\"$CARR_DB_JOBS_URL\"" in wrapper

print("rule-delivery-shadow-ledger-selftest: 36 cases passed")
