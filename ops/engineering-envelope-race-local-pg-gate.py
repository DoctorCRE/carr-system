#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Disposable two-session proof for live Engineering lease terminalization fencing."""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import uuid
from pathlib import Path

import psycopg


RUNTIME_ROLE = "carr_jobs"


def fail(message: str) -> int:
    print(f"engineering-envelope-race-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def load_claim_gate():
    path = Path(__file__).with_name("engineering-claim-local-pg-gate.py")
    spec = importlib.util.spec_from_file_location("engineering_claim_local_pg_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Engineering claim fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn or not any(host in dsn for host in ("127.0.0.1", "localhost")):
        return fail("a disposable loopback DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    gate = load_claim_gate()
    try:
        with psycopg.connect(dsn) as setup, setup.cursor() as cur:
            gate.grant_settable_runtime_roles(cur, RUNTIME_ROLE)
            cur.execute(
                """update ops.job set next_attempt_at=now()+interval '1 day'
                     where definition_key='engineering-slice' and state in ('queued','retry_wait')"""
            )
            job_id, envelope_id, session_id, _, _, _, _ = gate.fixture(cur)
            # Deferred doctrine/epoch work belongs to fixture setup. Finish it
            # before the scoped claim starts its bounded binding runway.
            setup.commit()
            gate.set_local_role(cur, RUNTIME_ROLE)
            claimed = gate.one(cur, "select job_id,lease_token from ops.engineering_claim_slice(%s,1,960)",
                               ("engineering-envelope-race",))
            if claimed[0] != job_id:
                return fail("fresh race fixture was not claimed")
            lease_token = claimed[1]
            if gate.one(cur, "select ops.engineering_controller_binding(%s,%s,%s)",
                        (envelope_id, job_id, uuid.uuid4()))[0] is not None:
                return fail("controller binding accepted a non-claim lease token")
            setup.commit()

        binding_ready = threading.Event()
        allow_binding_commit = threading.Event()
        terminal_started = threading.Event()
        terminal_result: list[str] = []
        binding_result: list[str] = []

        def bind() -> None:
            try:
                with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                    gate.set_local_role(cur, RUNTIME_ROLE)
                    binding = gate.one(cur, "select ops.engineering_controller_binding(%s,%s,%s)",
                                       (envelope_id, job_id, lease_token))[0]
                    if binding is None:
                        # The explanatory predicate is private to owner/read
                        # roles; keep the runtime binding attempt least-privilege.
                        cur.execute("reset role")
                        currentness = gate.one(cur, "select ops.engineering_envelope_currentness(%s,%s)",
                                               (envelope_id, job_id))[0]
                        raise RuntimeError(f"live fixture unexpectedly had no controller binding: {currentness}")
                    binding_result.append("ready")
                    binding_ready.set()
                    if not allow_binding_commit.wait(15):
                        raise RuntimeError("terminal race did not begin")
                    conn.commit()
            except Exception as exc:  # noqa: BLE001 - surface thread failures to the gate
                binding_result.append(str(exc))
                binding_ready.set()

        def terminalize() -> None:
            with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                terminal_started.set()
                try:
                    cur.execute("set local statement_timeout='3s'")
                    cur.execute("update ops.capability_agent_session set state='cancelled',cancelled_at=now() where id=%s",
                                (session_id,))
                    conn.commit()
                    terminal_result.append("unexpectedly succeeded")
                except psycopg.Error as exc:
                    terminal_result.append(str(exc))
                    conn.rollback()

        binding_thread = threading.Thread(target=bind, daemon=True)
        terminal_thread = threading.Thread(target=terminalize, daemon=True)
        binding_thread.start()
        if not binding_ready.wait(15):
            return fail("controller binding did not acquire its lineage lock")
        if binding_result != ["ready"]:
            return fail(f"controller binding failed before the race: {binding_result!r}")
        terminal_thread.start()
        if not terminal_started.wait(15):
            return fail("terminalization session did not start")
        allow_binding_commit.set()
        binding_thread.join(15)
        terminal_thread.join(15)
        if binding_thread.is_alive() or terminal_thread.is_alive():
            return fail("two-session terminalization race did not finish")
        if len(terminal_result) != 1 or "engineering session terminalization deferred while its dispatch lease is live" not in terminal_result[0]:
            return fail(f"terminalization race did not fail closed: {terminal_result!r}")
    except Exception as exc:  # noqa: BLE001 - report exact disposable DB failure
        return fail(str(exc))
    print("engineering envelope race acceptance passed: a concurrent current binding cannot bypass live-lease terminalization fencing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
