#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Two-connection COMMITTED race proof for the slice done-record's per-slice
lock (migration 0628): a partner hold is never silently overridden by an
automated proposal or by a concurrent confirmation.

One transaction cannot prove this: the loser has to observe the winner's
COMMITTED row. So, like ops/control-plane-db-gate.py's duplicate_group race,
this commits fixtures, and it runs ONLY on the dedicated disposable carr_ci
database (local-pg-ci's throwaway cluster, or the hosted CI service
container); anywhere else it prints NOT RUN. It removes every row and login it
committed before it exits.

It has to PROVE it ran. A db-gate that returns 0 without doing anything looks
exactly like one that passed (the hosted log once showed this gate at "=0s"
and nothing else), so on success it prints

    db-gate-proof: slice-done-marker-race-local-pg-gate race gate ran 6 scenarios (...)

which ops/ci.sh echoes into the lane log, and it fails unless all six
scenarios actually executed. On a hosted GitHub Actions lane, where the
disposable database is expected, NOT RUN is a failure, not a pass.

Logins: carr_authority_joe (partner; ops.authority_actor_slug() -> joe), two
sessions of it, and sdmrace_writer (member of carr_writer only) with the
server-derived carr.acting_actor_slug set to the seat actor joe-local.

  SEQUENTIAL  a proposal, then a hold COMMITTED after it: confirmation reports
              held; after the release it reports stale_proposal. Nothing is
              marked complete.
  RACE 1      partner session A holds the slice (uncommitted) while the seat
              proposes: the seat BLOCKS on the slice lock (observed in
              pg_stat_activity), and once A commits the proposal is refused
              (slice_mark_held_by_partner).
  RACE 2      A holds (uncommitted) while partner session C confirms a pending
              proposal: C blocks, and once A commits C reports held and writes
              no complete mark.
  RACE 3      C confirms (uncommitted) while A holds: A blocks, and once C
              commits A's hold lands AFTER the complete mark, so the latest mark
              is the hold -- the hold still wins; nothing overrides it.
  RACE 4/5    the seat's bind and its progress mark each racing an uncommitted
              hold: each waits on the slice lock and is refused once the hold
              commits, so no automated row lands after (and over) the hold.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

AUTHORITY = "carr_authority_joe"
WRITER = "sdmrace_writer"
SEAT = "joe-local"
CRITERION = "the race job completes"
BIND_CRITERION = "staging restore succeeds"
LOCK_WAIT_SECONDS = 20
SCENARIOS = ("sequential", "race1", "race2", "race3", "race4", "race5")


def one(row: tuple | None) -> tuple:
    if row is None:
        raise RuntimeError("expected one row, got none")
    return row


def fail(message: str) -> int:
    print(f"slice-done-marker-race-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def disposable_ci_database(dsn: str) -> bool:
    """The same guard as ops/control-plane-db-gate.py: loopback, carr_ci as a
    superuser, and a local-pg-ci throwaway cluster or the hosted CI container."""
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    with psycopg.connect(dsn) as probe, probe.cursor() as cur:
        row = cur.execute("select current_database(), current_user, current_setting('data_directory'),"
                          " (select rolsuper from pg_roles where rolname=current_user)").fetchone()
    if row is None:
        return False
    database_name, role_name, data_directory, is_superuser = row
    data_path = os.path.realpath(str(data_directory))
    local_disposable = (os.path.isfile(os.path.join(data_path, "PG_VERSION"))
                        and os.path.basename(os.path.dirname(data_path)).startswith("carr-local-pg-ci."))
    hosted_disposable = (os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("CI") == "true"
                         and os.environ.get("GITHUB_REPOSITORY") == "jbookout/carr-system"
                         and data_path == "/var/lib/postgresql/data")
    return (database_name == "carr_ci" and role_name == "carr_ci" and is_superuser is True
            and (local_disposable or hosted_disposable))


def session(dsn: str, login: str, acting: str | None = None) -> psycopg.Connection:
    """A connection whose session user IS the login (as a production login is)."""
    conn = psycopg.connect(dsn, autocommit=True)
    conn.execute(sql.SQL("set session authorization {}").format(sql.Identifier(login)))
    who = conn.execute("select session_user::text, current_user::text").fetchone()
    if who != (login, login):
        raise RuntimeError(f"expected session and current user {login!r}, got {who!r}")
    if acting:
        conn.execute("select set_config('carr.acting_actor_slug', %s, false)", (acting,))
    conn.autocommit = False
    return conn


class Racer:
    """Run one call on its own connection in a thread; record its result."""

    def __init__(self, conn: psycopg.Connection, query: str, params: tuple) -> None:
        self.conn, self.query, self.params = conn, query, params
        self.pid = conn.info.backend_pid
        self.result: tuple[str, Any] | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            rows = self.conn.execute(self.query, self.params).fetchall()
            self.conn.commit()
            self.result = ("ok", rows)
        except psycopg.Error as exc:
            self.conn.rollback()
            self.result = ("refused", str(exc).strip().splitlines()[0])

    def start(self) -> "Racer":
        self.thread.start()
        return self

    def join(self) -> tuple[str, Any]:
        self.thread.join(timeout=60)
        if self.result is None:
            raise RuntimeError(f"racer never finished: {self.query}")
        return self.result


def wait_blocked_on_slice_lock(admin: psycopg.Connection, pid: int) -> bool:
    """True once backend `pid` is waiting on an advisory lock (the slice lock)."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        row = admin.execute("select wait_event_type, wait_event from pg_stat_activity where pid=%s", (pid,)).fetchone()
        if row == ("Lock", "advisory"):
            return True
        time.sleep(0.05)
    return False


def run(dsn: str, admin: psycopg.Connection, slice_id: str, bind_slice: str, job_ref: str,
        ran: list[str]) -> str | None:
    receipt = Jsonb([{"criterion": CRITERION, "evidence_ref": job_ref}])
    propose = "select id::text from ops.propose_slice_completion(%s,%s,'race proposal',%s)"
    confirm = "select outcome from ops.confirm_slice_completions(array[%s],'race confirm',%s)"
    hold = "select marked_via from ops.set_slice_mark_hold(%s,'hold','blocked','race hold',%s)"
    release = "select marked_via from ops.set_slice_mark_hold(%s,'release',null,'race release',%s)"

    def marks() -> list[tuple]:
        return admin.execute("select status, marked_via from ops.slice_completion_mark where slice_id=%s order by mark_seq",
                             (slice_id,)).fetchall()

    def complete_count() -> int:
        return sum(1 for status, _ in marks() if status == "complete")

    seat = session(dsn, WRITER, SEAT)
    part_a = session(dsn, AUTHORITY)
    part_c = session(dsn, AUTHORITY)
    try:
        def call(conn: psycopg.Connection, query: str, params: tuple) -> Any:
            row = conn.execute(query, params).fetchone()
            conn.commit()
            return row[0] if row else None

        # ------------------------------------------------------------ SEQUENTIAL
        call(seat, propose, (slice_id, receipt, uuid.uuid4()))
        call(part_a, hold, (slice_id, uuid.uuid4()))
        if (got := call(part_c, confirm, (slice_id, uuid.uuid4()))) != "held":
            return f"a hold committed after the proposal did not block its confirmation: {got}"
        call(part_a, release, (slice_id, uuid.uuid4()))
        if (got := call(part_c, confirm, (slice_id, uuid.uuid4()))) != "stale_proposal":
            return f"a proposal made before a hold was confirmed after the release: {got}"
        if complete_count():
            return f"the sequential hold still let a complete mark through: {marks()}"
        ran.append("sequential")

        # ---------------------------------------------------------------- RACE 1
        # A holds (uncommitted, holding the slice lock); the seat proposes.
        part_a.execute(hold, (slice_id, uuid.uuid4()))
        r1 = Racer(seat, propose, (slice_id, receipt, uuid.uuid4())).start()
        blocked = wait_blocked_on_slice_lock(admin, r1.pid)
        part_a.commit()
        outcome = r1.join()
        if not blocked:
            return f"the seat's proposal did not wait on the slice lock behind an uncommitted hold: {outcome}"
        if outcome[0] != "refused" or "slice_mark_held_by_partner" not in outcome[1]:
            return f"a proposal racing an uncommitted hold was not refused once the hold committed: {outcome}"
        call(part_a, release, (slice_id, uuid.uuid4()))
        ran.append("race1")

        # ---------------------------------------------------------------- RACE 2
        # A pending proposal; A holds (uncommitted); C confirms.
        call(seat, propose, (slice_id, receipt, uuid.uuid4()))
        part_a.execute(hold, (slice_id, uuid.uuid4()))
        r2 = Racer(part_c, confirm, (slice_id, uuid.uuid4())).start()
        blocked = wait_blocked_on_slice_lock(admin, r2.pid)
        part_a.commit()
        outcome = r2.join()
        if not blocked:
            return f"the confirmation did not wait on the slice lock behind an uncommitted hold: {outcome}"
        if outcome != ("ok", [("held",)]) or complete_count():
            return f"a confirmation racing an uncommitted hold overrode it: {outcome} / {marks()}"
        call(part_a, release, (slice_id, uuid.uuid4()))
        ran.append("race2")

        # ---------------------------------------------------------------- RACE 3
        # A pending proposal; C confirms (uncommitted); A holds.
        call(seat, propose, (slice_id, receipt, uuid.uuid4()))
        first = part_c.execute(confirm, (slice_id, uuid.uuid4())).fetchone()
        if first != ("confirmed",):
            part_c.rollback()
            return f"the race-3 confirmation was not confirmed: {first}"
        r3 = Racer(part_a, hold, (slice_id, uuid.uuid4())).start()
        blocked = wait_blocked_on_slice_lock(admin, r3.pid)
        part_c.commit()
        outcome = r3.join()
        if not blocked:
            return f"the hold did not wait on the slice lock behind an uncommitted confirmation: {outcome}"
        latest = marks()[-2:]
        if outcome != ("ok", [("authority_hold",)]) or latest != [("complete", "authority"), ("blocked", "authority_hold")]:
            return f"a hold racing a confirmation did not land after it as the latest mark: {outcome} / {latest}"
        state = one(admin.execute("select ops.read_slice_done_state(%s)", (slice_id,)).fetchone())[0]
        if not state["held_by_partner"] or state["latest_mark"]["marked_via"] != "authority_hold":
            return f"the done-state does not report the partner hold as current: {state['latest_mark']}"
        if any(via == "automation" for status, via in marks() if status == "complete"):
            return f"an automated complete mark exists: {marks()}"
        ran.append("race3")

        # ------------------------------------------------------------- RACE 4/5
        bind = ("select id from ops.bind_slice_criterion_evidence(%s,%s,'live_check','staging_restore_only_result',"
                "null,null,null,'race bind',%s)")
        progress = "select id from ops.mark_slice_progress(%s,'in_progress',%s,'race progress',%s,null)"
        bind_receipt = Jsonb([{"criterion": BIND_CRITERION, "evidence_ref": None}])
        for scenario, label, query, params in (
                ("race4", "bind", bind, (bind_slice, BIND_CRITERION, uuid.uuid4())),
                ("race5", "progress mark", progress, (bind_slice, bind_receipt, uuid.uuid4()))):
            part_a.execute(hold, (bind_slice, uuid.uuid4()))
            racer = Racer(seat, query, params).start()
            blocked = wait_blocked_on_slice_lock(admin, racer.pid)
            part_a.commit()
            outcome = racer.join()
            if not blocked:
                return f"the seat's {label} did not wait on the slice lock behind an uncommitted hold: {outcome}"
            if outcome[0] != "refused" or "slice_mark_held_by_partner" not in outcome[1]:
                return f"the seat's {label} racing an uncommitted hold was not refused once it committed: {outcome}"
            call(part_a, release, (bind_slice, uuid.uuid4()))
            ran.append(scenario)
        return None
    finally:
        for conn in (seat, part_a, part_c):
            try:
                conn.rollback()
            finally:
                conn.close()


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    if not disposable_ci_database(dsn):
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # The hosted lane always provides the disposable database; a NOT RUN
            # there means the guard or the lane broke, and a silent 0 would hide it.
            return fail("NOT RUN on the hosted CI lane, where the disposable carr_ci database is expected; "
                        "a race gate that did not run is not a pass")
        print("slice-done-marker-race-local-pg-gate: NOT RUN — it commits fixtures and requires the dedicated "
              "disposable carr_ci database; the rolled-back slice-done-marker gate still ran")
        return 0
    ran: list[str] = []
    token = uuid.uuid4().hex[:8]
    slice_id = f"V5-ZR{token}"
    bind_slice = f"V5-ZB{token}"
    definition_key = f"sdmrace-{token}"
    created_authority = False
    problem: str | None = None
    with psycopg.connect(dsn, autocommit=True) as admin:
        try:
            created_authority = not one(admin.execute("select exists (select 1 from pg_roles where rolname=%s)",
                                                      (AUTHORITY,)).fetchone())[0]
            if created_authority:
                admin.execute(sql.SQL("create role {} login").format(sql.Identifier(AUTHORITY)))
            admin.execute(sql.SQL("grant carr_authority to {}").format(sql.Identifier(AUTHORITY)))
            admin.execute(sql.SQL("create role {} login").format(sql.Identifier(WRITER)))
            admin.execute(sql.SQL("grant carr_writer to {}").format(sql.Identifier(WRITER)))
            with admin.transaction():
                admin.execute("set local session_replication_role=replica")
                job = admin.execute(
                    """insert into ops.job (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,
                                            timeout_seconds,created_at)
                       values (%s,1,%s,now()-interval '1 day',1,30,now()-interval '1 day') returning id""",
                    (definition_key, uuid.uuid4().hex)).fetchone()
                job = one(job)[0]
                job_ref = admin.execute(
                    "insert into ops.job_receipt (job_id,attempt,kind,receipt_ref) values (%s,1,'completion',%s) returning id::text",
                    (job, f"sdmrace:{hashlib.sha1(token.encode()).hexdigest()}")).fetchone()
                job_ref = one(job_ref)[0]
            with session(dsn, AUTHORITY) as reg:
                reg.execute("select * from ops.register_slice_checkable_done(%s,%s,%s)",
                            (slice_id, Jsonb([{"criterion": CRITERION, "evidence_kind": "live_check",
                                               "live_check_source": "job_receipt", "live_check_key": definition_key}]),
                             uuid.uuid4()))
                reg.execute("select * from ops.register_slice_checkable_done(%s,%s,%s)",
                            (bind_slice, Jsonb([{"criterion": BIND_CRITERION, "evidence_kind": "unbound"}]), uuid.uuid4()))
                reg.commit()
            problem = run(dsn, admin, slice_id, bind_slice, job_ref, ran)
        except Exception as exc:  # noqa: BLE001 — a gate reports, it never crashes silently
            problem = f"{type(exc).__name__}: {exc}"
        finally:
            # A committing test that leaks is worse than one that fails.
            admin.execute("select pg_terminate_backend(pid) from pg_stat_activity where usename=%s and pid<>pg_backend_pid()",
                          (WRITER,))
            with admin.transaction():
                admin.execute("set local session_replication_role=replica")
                admin.execute("delete from ops.slice_completion_mark where slice_id = any(%s)", ([slice_id, bind_slice],))
                admin.execute("delete from ops.slice_completion_proposal where slice_id = any(%s)", ([slice_id, bind_slice],))
                admin.execute("delete from ops.slice_criterion_binding where slice_id = any(%s)", ([slice_id, bind_slice],))
                admin.execute("delete from ops.slice_checkable_done_registry where slice_id = any(%s)", ([slice_id, bind_slice],))
                admin.execute("delete from ops.slice_checkable_done_registration where slice_id = any(%s)", ([slice_id, bind_slice],))
                admin.execute("delete from ops.job_receipt where job_id in (select id from ops.job where definition_key=%s)",
                              (definition_key,))
                admin.execute("delete from ops.job where definition_key=%s", (definition_key,))
            admin.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(WRITER)))
            if created_authority:
                admin.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(AUTHORITY)))
            leaked = admin.execute(
                """select (select count(*) from ops.slice_completion_mark where slice_id = any(%(s)s))
                        + (select count(*) from ops.slice_completion_proposal where slice_id = any(%(s)s))
                        + (select count(*) from ops.slice_criterion_binding where slice_id = any(%(s)s))
                        + (select count(*) from ops.slice_checkable_done_registration where slice_id = any(%(s)s))
                        + (select count(*) from ops.job where definition_key = %(d)s)""",
                {"s": [slice_id, bind_slice], "d": definition_key}).fetchone()
            leaked = one(leaked)[0]
            if leaked and not problem:
                problem = f"the committed race left {leaked} fixture row(s) behind"
    if problem:
        return fail(problem)
    if tuple(ran) != SCENARIOS:
        return fail(f"only {len(ran)} of {len(SCENARIOS)} scenarios ran: {ran}")
    print(f"db-gate-proof: slice-done-marker-race-local-pg-gate race gate ran {len(ran)} scenarios "
          f"({', '.join(ran)})")
    print("slice-done-marker-race-local-pg-gate: PASS — committed, two logins: a hold committed after a proposal "
          "blocks its confirmation (held, then stale_proposal); a proposal and a confirmation racing an uncommitted "
          "hold wait on the slice lock and are refused/held once it commits; a hold racing a confirmation waits and "
          "lands as the latest mark; the seat's bind and progress mark wait and are refused behind an uncommitted hold. "
          "No automated complete mark; fixtures removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
