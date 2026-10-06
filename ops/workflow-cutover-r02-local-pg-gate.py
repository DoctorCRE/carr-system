#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-Postgres proof for the DoctorCRE V5-R02 SQL doors
(migrations 0602 and 0604), run ONLY under production-shaped logins.

WHY THE LOGINS MATTER. PR #1245's re-review found three defects this gate had
been passing over: it asked the doors under grants and routing production
does not have (a superuser session, `SET ROLE carr_authority` on a login, and
a test-only `grant carr_writer to carr_authority_joe`). So every door call
below -- refusal or success -- runs after `SET SESSION AUTHORIZATION` to one
of three logins holding exactly one runtime bundle each, as production does:

  carr_authority_joe  member of carr_authority only (0161/0273: the partner's
                      authority login that authorityOnly verbs connect as);
  r02gate_writer      member of carr_writer only (the MCP writer login);
  r02gate_jobs        member of carr_jobs only (the job runner's login).

The gate checks those memberships before it starts and refuses to run if
carr_authority_joe also holds carr_writer or carr_jobs. The superuser session
only writes FIXTURE rows the doors read (job definitions, acceptance rows,
legacy surfaces and disable receipts) and probes the append-only triggers as
table owner; it never calls a door.

Covered:
  P1-a  an open plan never stops an already-live workflow: live and canary
        enqueue keep working at read_legacy..shadow_compare; from
        single_write_authority only LIVE is gated, and only on disabled legacy
        surfaces; canary never is. Canary freshness is checked once, by
        advance into single_write_authority and cutover; monitor and
        recovery_ready need no new canary and never halt live scheduling.
        Cancel ends the gating at once.
        open / supersede / advance / cancel / retire are authority-only.
  P1-b  slice completion is reachable in production: register and complete
        on the authority login, progress on the writer login; each refused
        on the other login.
  P1-c  criteria cannot be beaten: [A, A] never satisfies {A, B}; evidence
        resolves only against each criterion's registered workflow, evidence
        type and mode/stage.
  P3    retire derives the census itself: no caller argument exists for it,
        and a fully valid plan + receipt is refused for the census, last.
  and the earlier review items: 'retired' is terminal for advance (the
  array_position NULL guard), retire's receipt checks (version, predates
  cutover, one-use, every legacy surface), record_workflow_caller's
  registered-workflow check and append-only history.
"""

from __future__ import annotations

import os
import sys
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from gate_runtime_role import rollback_only_connection

AUTHORITY = "carr_authority_joe"
WRITER = "r02gate_writer"
JOBS = "r02gate_jobs"
READER = "r02gate_reader"


def fail(message: str) -> int:
    print(f"workflow-cutover-r02-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


@contextmanager
def as_login(cur: Any, login: str) -> Iterator[None]:
    """Run the block as `login` itself: session_user and current_user both
    become the login, holding only the privileges its memberships give."""
    cur.execute(sql.SQL("set session authorization {}").format(sql.Identifier(login)))
    who = cur.execute("select session_user::text, current_user::text").fetchone()
    if who != (login, login):
        raise RuntimeError(f"expected session and current user {login!r}, got {who!r}")
    # No `finally`: after a database error the transaction is aborted and a
    # reset would only replace the real error with "current transaction is
    # aborted". The whole transaction rolls back on the way out anyway.
    yield
    cur.execute("reset session authorization")


def expect_refusal(cur: Any, query: str, params: tuple, label: str, *, match: str) -> None:
    cur.execute("savepoint r02_refusal")
    try:
        cur.execute(query, params)
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint r02_refusal")
        if match not in str(exc):
            raise RuntimeError(f"{label} was refused for the wrong reason: {exc}") from exc
        return
    cur.execute("rollback to savepoint r02_refusal")
    raise RuntimeError(f"{label} was accepted")


class Slots:
    """Distinct scheduled_for offsets, so no two enqueues share a slot."""

    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def insert_job_definition(cur: Any, key: str, *, canary_enabled: bool | None = True) -> None:
    contract: dict[str, object] = {"entrypoint": "fixture"}
    if canary_enabled is not None:
        contract["canary"] = {"enabled": canary_enabled}
    cur.execute(
        """insert into ops.job_definition
             (key,version,enabled,risk,execution_kind,execution_contract,
              recurrence,retry_policy,deduplication,completion_contract,legacy_schedule)
           values (%s,1,true,'green','deterministic',%s,
                   '{"cron":"* * * * *","timezone":"UTC"}'::jsonb,
                   '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}'::jsonb,
                   '{"key_template":"r02-gate-fixture"}'::jsonb,
                   '{"predicate":"fixture","receipt_kind":"fixture"}'::jsonb,
                   '{"status":"enabled"}'::jsonb)""",
        (key, Jsonb(contract)),
    )


def insert_acceptance(cur: Any, key: str, mode: str, ref: str, *, backdate: bool = False,
                      minutes_ago: int | None = None) -> str:
    """Fixture: an accepted acceptance row, written as table owner."""
    if minutes_ago is not None:
        created = f"now() - interval '{int(minutes_ago)} minutes'"
    else:
        created = "now() - interval '1 hour'" if backdate else "now()"
    return cur.execute(
        f"""insert into ops.workflow_acceptance (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by,created_at)
            values (%s,1,%s,'accepted',%s,'r02-gate', {created}) returning id::text""",
        (key, mode, ref),
    ).fetchone()[0]


def insert_disable_receipt(cur: Any, key: str, ref: str, *, version: int = 1, surface: str = "no-surface",
                           locator: str = "no-locator", approved_at: str = "now()") -> str:
    return cur.execute(
        f"""insert into ops.legacy_schedule_disable_receipt
              (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by,approved_at)
            values (%s,%s,%s,%s,%s,%s,'fixture','joe',{approved_at}) returning id::text""",
        (ref, f"{ref}-idem", key, version, surface, locator),
    ).fetchone()[0]


def set_transition_clock(cur: Any, plan_id: str, to_stage: str, minutes_ago: int) -> None:
    """Fixture clock. The whole gate runs in ONE rollback-only transaction, so
    now() never moves and every door-written timestamp is identical -- which
    would make any "recorded since entering the stage" comparison pass
    vacuously (the reviewer reproduced the re-review-2 P1-a halt with real
    elapsed time; this gate at first did not). As table owner, place the
    transition the door just wrote at an explicit point on the timeline."""
    cur.execute(
        """update ops.workflow_cutover_stage_transition
              set occurred_at = now() - make_interval(mins => %s)
            where plan_id = %s and to_stage = %s""",
        (minutes_ago, plan_id, to_stage),
    )


def open_plan(cur: Any, key: str) -> str:
    with as_login(cur, AUTHORITY):
        return cur.execute(
            "select id::text from ops.open_workflow_cutover_plan(%s,1,'fixture recovery plan',%s)",
            (key, uuid.uuid4()),
        ).fetchone()[0]


def advance(cur: Any, plan_id: str, to_stage: str, evidence_ref: str | None, reason: str) -> str:
    with as_login(cur, AUTHORITY):
        return cur.execute(
            "select stage from ops.advance_workflow_cutover_stage(%s,%s,%s,%s,%s)",
            (plan_id, to_stage, evidence_ref, reason, uuid.uuid4()),
        ).fetchone()[0]


def walk_to(cur: Any, key: str, stage: str, *, backdate_canary: bool = False) -> str:
    """Open a plan (authority) and advance it to `stage` with real accepted
    evidence at each gated transition. Returns the plan id."""
    order = ["build_projection", "shadow_compare", "single_write_authority", "cutover",
             "monitor", "recovery_ready"]
    plan_id = open_plan(cur, key)
    for step in order[: order.index(stage) + 1]:
        evidence = None
        if step == "shadow_compare":
            evidence = insert_acceptance(cur, key, "shadow", f"{key}-{step}-{uuid.uuid4().hex[:6]}")
        elif step in ("single_write_authority", "cutover"):
            evidence = insert_acceptance(cur, key, "canary", f"{key}-{step}-{uuid.uuid4().hex[:6]}",
                                         backdate=backdate_canary)
        advance(cur, plan_id, step, evidence, f"to {step}")
    return plan_id


def enqueue(cur: Any, slots: Slots, key: str, mode: str) -> str:
    with as_login(cur, JOBS):
        return cur.execute(
            "select mode from ops.enqueue_job(%s,1,now()+make_interval(hours => %s),'{}'::jsonb,%s,%s)",
            (key, slots.next(), f"{key}-{uuid.uuid4().hex}", mode),
        ).fetchone()[0]


def expect_enqueue_refused(cur: Any, slots: Slots, key: str, mode: str, label: str, match: str) -> None:
    with as_login(cur, JOBS):
        expect_refusal(
            cur, "select ops.enqueue_job(%s,1,now()+make_interval(hours => %s),'{}'::jsonb,%s,%s)",
            (key, slots.next(), f"{key}-{uuid.uuid4().hex}", mode), label, match=match,
        )


def provision_logins(cur: Any) -> None:
    cur.execute(f"""do $$ begin
      if not exists (select 1 from pg_roles where rolname='{AUTHORITY}') then create role {AUTHORITY} login; end if;
      if not exists (select 1 from pg_roles where rolname='{WRITER}') then create role {WRITER} login; end if;
      if not exists (select 1 from pg_roles where rolname='{JOBS}') then create role {JOBS} login; end if;
      if not exists (select 1 from pg_roles where rolname='{READER}') then create role {READER} login; end if;
    end $$""")
    # 0273's production membership for the authority login, and one bundle
    # each for the other three.
    cur.execute(f"grant carr_authority to {AUTHORITY}")
    cur.execute(f"grant carr_writer to {WRITER}")
    cur.execute(f"grant carr_jobs to {JOBS}")
    cur.execute(f"grant carr_reader to {READER}")
    shape = cur.execute(
        """select pg_has_role(%s,'carr_authority','member'),
                  pg_has_role(%s,'carr_writer','member'), pg_has_role(%s,'carr_jobs','member'),
                  pg_has_role(%s,'carr_writer','member'), pg_has_role(%s,'carr_authority','member'),
                  pg_has_role(%s,'carr_jobs','member'), pg_has_role(%s,'carr_authority','member'),
                  pg_has_role(%s,'carr_writer','member')""",
        (AUTHORITY, AUTHORITY, AUTHORITY, WRITER, WRITER, JOBS, JOBS, JOBS),
    ).fetchone()
    if shape != (True, False, False, True, False, True, False, False):
        raise RuntimeError(f"logins are not production-shaped (one bundle each): {shape!r}")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            provision_logins(cur)
            token = uuid.uuid4().hex[:8]
            slots = Slots()

            # ================================================================
            # ACCESS: which login reaches which door, as production routes it.
            # ================================================================
            key_a = f"r02gate-{token}-access"
            insert_job_definition(cur, key_a)
            plan_a = open_plan(cur, key_a)
            with as_login(cur, WRITER):
                for label, query, params in [
                    ("writer open", "select ops.open_workflow_cutover_plan(%s,1,'r',%s)", (key_a, uuid.uuid4())),
                    ("writer advance", "select ops.advance_workflow_cutover_stage(%s,'build_projection',null,'r',%s)",
                     (plan_a, uuid.uuid4())),
                    ("writer cancel", "select ops.cancel_workflow_cutover_plan(%s,'r',%s)", (plan_a, uuid.uuid4())),
                    ("writer retire", "select ops.retire_workflow_cutover_plan(%s,gen_random_uuid(),'r',%s)",
                     (plan_a, uuid.uuid4())),
                    ("writer register", "select ops.register_slice_checkable_done('s','[]'::jsonb,%s)", (uuid.uuid4(),)),
                    ("writer complete", "select ops.mark_slice_completion('s','[]'::jsonb,'r',%s)", (uuid.uuid4(),)),
                ]:
                    expect_refusal(cur, query, params, label, match="permission denied")
            with as_login(cur, AUTHORITY):
                expect_refusal(
                    cur, "select ops.mark_slice_progress('s','in_progress','[]'::jsonb,'r',%s,'joe')", (uuid.uuid4(),),
                    "authority login on the writer-only progress door", match="permission denied",
                )
            # No census argument exists for a caller to supply (P3).
            retire_args = cur.execute(
                """select array_to_string(proargnames, ',') from pg_proc
                    where oid = 'ops.retire_workflow_cutover_plan'::regproc"""
            ).fetchone()[0]
            if "census" in retire_args or "actor" in retire_args:
                return fail(f"retire still takes a caller-supplied census or actor: {retire_args}")

            # ================================================================
            # P1-a: an open plan never stops an already-live workflow.
            # ================================================================
            key_l = f"r02gate-{token}-live"
            insert_job_definition(cur, key_l)
            insert_acceptance(cur, key_l, "shadow", f"{key_l}-shadow-0", backdate=True)
            insert_acceptance(cur, key_l, "canary", f"{key_l}-canary-0", backdate=True)
            if enqueue(cur, slots, key_l, "live") != "live":
                return fail("P1-a fixture: live enqueue failed before any plan existed")
            plan_l = open_plan(cur, key_l)
            for step, evidence in [(None, None), ("build_projection", None),
                                   ("shadow_compare", insert_acceptance(cur, key_l, "shadow", f"{key_l}-shadow-1"))]:
                if step is not None:
                    advance(cur, plan_l, step, evidence, f"to {step}")
                    # Timeline: canary-0 at T-60, shadow_compare entered T-50.
                    set_transition_clock(cur, plan_l, step, 50)
                for mode in ("live", "canary"):
                    if enqueue(cur, slots, key_l, mode) != mode:
                        return fail(f"P1-a: {mode} enqueue refused with the plan at {step or 'read_legacy'}")
            # Canary freshness is checked ONCE, by advance, on the moves into
            # single_write_authority and cutover: a canary accepted before the
            # plan entered the stage it is leaving cannot carry it forward.
            stale_canary = cur.execute(
                """select id::text from ops.workflow_acceptance
                    where workflow_key=%s and mode='canary' and receipt_ref=%s""",
                (key_l, f"{key_l}-canary-0"),
            ).fetchone()[0]
            with as_login(cur, AUTHORITY):
                expect_refusal(
                    cur, "select ops.advance_workflow_cutover_stage(%s,'single_write_authority',%s,'r',%s)",
                    (plan_l, stale_canary, uuid.uuid4()),
                    "advancing into single_write_authority on a pre-transition canary",
                    match="canary_acceptance_evidence_predates_current_stage",
                )
            advance(cur, plan_l, "single_write_authority",
                    insert_acceptance(cur, key_l, "canary", f"{key_l}-canary-swa", minutes_ago=40), "to swa")
            set_transition_clock(cur, plan_l, "single_write_authority", 30)
            for mode in ("live", "canary"):
                if enqueue(cur, slots, key_l, mode) != mode:
                    return fail(f"P1-a: {mode} enqueue refused at single_write_authority with legacy disabled")
            with as_login(cur, AUTHORITY):
                expect_refusal(
                    cur, "select ops.advance_workflow_cutover_stage(%s,'cutover',%s,'r',%s)",
                    (plan_l, stale_canary, uuid.uuid4()),
                    "advancing into cutover on a pre-transition canary",
                    match="canary_acceptance_evidence_predates_current_stage",
                )
            advance(cur, plan_l, "cutover",
                    insert_acceptance(cur, key_l, "canary", f"{key_l}-canary-cutover", minutes_ago=20), "to cutover")
            set_transition_clock(cur, plan_l, "cutover", 10)
            canary_rows_at_cutover = cur.execute(
                "select count(*) from ops.workflow_acceptance where workflow_key=%s and mode='canary'", (key_l,)
            ).fetchone()[0]
            # PR #1245 re-review 2, P1-a: after cutover the new system is the
            # only writer. monitor and recovery_ready take NO new canary
            # receipt, and live scheduling must continue at every stage. They
            # are entered at T (now()), strictly after the newest canary
            # (T-20), which is exactly the reviewer's reproduction.
            if enqueue(cur, slots, key_l, "live") != "live":
                return fail("P1-a: live enqueue refused at cutover")
            for step in ("monitor", "recovery_ready"):
                advance(cur, plan_l, step, None, f"to {step}")
                for mode in ("live", "canary"):
                    if enqueue(cur, slots, key_l, mode) != mode:
                        return fail(f"P1-a: {mode} enqueue refused at {step} with no new canary receipt; "
                                    "an advance after cutover must never halt the live workflow")
            canary_rows_now = cur.execute(
                "select count(*) from ops.workflow_acceptance where workflow_key=%s and mode='canary'", (key_l,)
            ).fetchone()[0]
            if canary_rows_now != canary_rows_at_cutover:
                return fail("P1-a fixture: a canary receipt was added after cutover; the case proves nothing")
            cur.execute(
                """insert into ops.legacy_schedule_surface_registry (workflow_key,workflow_version,surface_id,locator,scheduler_kind)
                   values (%s,1,'launchd-live','com.carr.r02gate-live.plist','launchd')""",
                (key_l,),
            )
            expect_enqueue_refused(cur, slots, key_l, "live",
                                   "live enqueue at recovery_ready with an undisabled legacy surface",
                                   "legacy surface(s) still undisabled")
            # Cancel is the recovery door: the plan stops governing enqueue.
            with as_login(cur, AUTHORITY):
                cancelled = cur.execute(
                    "select status from ops.cancel_workflow_cutover_plan(%s,'recover the workflow',%s)",
                    (plan_l, uuid.uuid4()),
                ).fetchone()
                expect_refusal(
                    cur, "select ops.cancel_workflow_cutover_plan(%s,'again',%s)", (plan_l, uuid.uuid4()),
                    "cancelling a plan that is no longer active", match="workflow_cutover_plan_not_active",
                )
                expect_refusal(
                    cur, "select ops.advance_workflow_cutover_stage(%s,'cutover','x','r',%s)", (plan_l, uuid.uuid4()),
                    "advancing a cancelled plan", match="workflow_cutover_plan_not_active",
                )
            if cancelled != ("cancelled",):
                return fail(f"P1-a: cancel did not leave the plan cancelled: {cancelled}")
            if enqueue(cur, slots, key_l, "live") != "live":
                return fail("P1-a: live enqueue still refused after the plan was cancelled")
            # The slot is free: a new plan opens without superseding anything.
            reopened = open_plan(cur, key_l)
            prior_status = cur.execute(
                "select status from ops.workflow_cutover_plan where id=%s", (plan_l,)
            ).fetchone()[0]
            if prior_status != "cancelled" or not reopened:
                return fail(f"P1-a: reopen after cancel disturbed the cancelled plan: {prior_status}")

            # A workflow with no plan at all is untouched.
            key_n = f"r02gate-{token}-noplan"
            insert_job_definition(cur, key_n, canary_enabled=False)
            insert_acceptance(cur, key_n, "shadow", f"{key_n}-shadow")
            if enqueue(cur, slots, key_n, "live") != "live":
                return fail("a workflow with no cutover plan was gated")

            # ================================================================
            # Supersession, the retired-stage guard, and open's own checks.
            # ================================================================
            key_s = f"r02gate-{token}-supersede"
            insert_job_definition(cur, key_s)
            first = open_plan(cur, key_s)
            second = open_plan(cur, key_s)
            row = cur.execute(
                "select status, superseded_by::text from ops.workflow_cutover_plan where id=%s", (first,)
            ).fetchone()
            if row != ("superseded", second):
                return fail(f"supersession did not mark the prior plan: {row}")
            with as_login(cur, AUTHORITY):
                expect_refusal(
                    cur, "select ops.open_workflow_cutover_plan(%s,1,'r',%s)", (f"{key_s}-unregistered", uuid.uuid4()),
                    "opening a plan for an unregistered workflow", match="workflow_cutover_plan_unregistered_workflow",
                )
            # Fixture: force stage='retired' under status='active' (a state no
            # door produces) to reach the array_position(NULL) guard and the
            # supersede refusal directly.
            cur.execute("update ops.workflow_cutover_plan set stage='retired' where id=%s", (second,))
            with as_login(cur, AUTHORITY):
                expect_refusal(
                    cur, "select ops.advance_workflow_cutover_stage(%s,'monitor','ev','un-retire',%s)",
                    (second, uuid.uuid4()),
                    "advancing a plan at stage=retired", match="workflow_cutover_plan_stage_not_advanceable",
                )
                expect_refusal(
                    cur, "select ops.open_workflow_cutover_plan(%s,1,'r',%s)", (key_s, uuid.uuid4()),
                    "superseding a plan at stage=retired", match="workflow_cutover_plan_supersede_refused_retired_plan",
                )

            # ================================================================
            # Retire: receipt checks in order, then the derived census (P3).
            # ================================================================
            key_r = f"r02gate-{token}-retire"
            insert_job_definition(cur, key_r)
            cur.execute(
                """insert into ops.job_definition
                     (key,version,enabled,risk,execution_kind,execution_contract,
                      recurrence,retry_policy,deduplication,completion_contract,legacy_schedule)
                   select key,2,false,risk,execution_kind,execution_contract,recurrence,retry_policy,
                          '{"key_template":"r02-gate-fixture-v2"}'::jsonb,completion_contract,legacy_schedule
                     from ops.job_definition where key=%s and version=1""",
                (key_r,),
            )
            plan_r = walk_to(cur, key_r, "recovery_ready")
            wrong_version = insert_disable_receipt(cur, key_r, f"{key_r}-v2", version=2)
            stale = insert_disable_receipt(cur, key_r, f"{key_r}-stale", approved_at="now() - interval '1 year'")
            cur.execute(
                """insert into ops.legacy_schedule_surface_registry (workflow_key,workflow_version,surface_id,locator,scheduler_kind)
                   values (%s,1,'launchd-r','com.carr.r02gate-r.plist','launchd')""",
                (key_r,),
            )
            # (workflow, version, surface, locator) is unique per receipt, so
            # the consumed one names its own surface; one-use is checked
            # before surface coverage, so that does not change what refuses it.
            consumed = insert_disable_receipt(cur, key_r, f"{key_r}-consumed", surface="consumed-surface",
                                              locator="consumed-locator", approved_at="now() + interval '1 day'")
            other_plan = open_plan(cur, f"{key_n}")
            cur.execute("insert into ops.workflow_cutover_retire_receipt_use (receipt_id, plan_id) values (%s,%s)",
                        (consumed, other_plan))
            valid = insert_disable_receipt(cur, key_r, f"{key_r}-valid", surface="launchd-r",
                                           locator="com.carr.r02gate-r.plist", approved_at="now() + interval '1 day'")
            with as_login(cur, AUTHORITY):
                for label, receipt, match in [
                    ("a receipt that does not exist", "00000000-0000-0000-0000-000000000000",
                     "legacy_schedule_disable_receipt_not_found"),
                    ("a receipt for another workflow_version", wrong_version,
                     "legacy_schedule_disable_receipt_workflow_mismatch"),
                    ("a receipt approved before the cutover transition", stale,
                     "legacy_schedule_disable_receipt_predates_cutover_transition"),
                    ("a receipt already used to retire a plan", consumed, "legacy_schedule_disable_receipt_already_used"),
                    # Every earlier check passes for this one, so the
                    # derived census is what refuses it.
                    ("a fully valid plan and receipt", valid, "workflow_cutover_retire_refused_census_unknown"),
                ]:
                    expect_refusal(cur, "select ops.retire_workflow_cutover_plan(%s,%s,'r',%s)",
                                   (plan_r, receipt, uuid.uuid4()), label, match=match)
            if cur.execute("select stage, status from ops.workflow_cutover_plan where id=%s",
                           (plan_r,)).fetchone() != ("recovery_ready", "active"):
                return fail("a refused retire changed the plan")
            key_m = f"r02gate-{token}-surface"
            insert_job_definition(cur, key_m)
            plan_m = walk_to(cur, key_m, "recovery_ready")
            cur.execute(
                """insert into ops.legacy_schedule_surface_registry (workflow_key,workflow_version,surface_id,locator,scheduler_kind)
                   values (%s,1,'launchd-m','com.carr.r02gate-m.plist','launchd')""",
                (key_m,),
            )
            not_covering = insert_disable_receipt(cur, key_m, f"{key_m}-other", approved_at="now() + interval '1 day'")
            with as_login(cur, AUTHORITY):
                expect_refusal(cur, "select ops.retire_workflow_cutover_plan(%s,%s,'r',%s)",
                               (plan_m, not_covering, uuid.uuid4()),
                               "a receipt that does not cover the registered legacy surface",
                               match="legacy_schedule_disable_receipt_missing_for_")

            # ================================================================
            # P1-b / P1-c: slice criteria and completion.
            # ================================================================
            wf1 = f"r02gate-{token}-slice-wf1"
            wf2 = f"r02gate-{token}-slice-wf2"
            insert_job_definition(cur, wf1)
            insert_job_definition(cur, wf2)
            slice_id = f"r02gate-slice-{token}"
            criteria = [
                {"criterion": "A", "evidence_kind": "acceptance", "workflow_key": wf1, "workflow_version": 1,
                 "acceptance_mode": "canary"},
                {"criterion": "B", "evidence_kind": "transition", "workflow_key": wf1, "workflow_version": 1,
                 "transition_to_stage": "shadow_compare"},
            ]
            with as_login(cur, AUTHORITY):
                expect_refusal(
                    cur, "select ops.register_slice_checkable_done(%s,%s,%s)",
                    (slice_id, Jsonb([criteria[0], criteria[0]]), uuid.uuid4()),
                    "registering the same criterion twice", match="criteria_must_be_distinct",
                )
                expect_refusal(
                    cur, "select ops.register_slice_checkable_done(%s,%s,%s)",
                    (slice_id, Jsonb([{**criteria[0], "workflow_key": f"{wf1}-missing"}]), uuid.uuid4()),
                    "binding a criterion to an unregistered workflow", match="criterion_workflow_not_registered",
                )
                expect_refusal(
                    cur, "select ops.register_slice_checkable_done(%s,%s,%s)",
                    (slice_id, Jsonb([{**criteria[0], "acceptance_mode": None}]), uuid.uuid4()),
                    "an acceptance criterion without a mode", match="slice_checkable_done_registry",
                )
                reg_key = uuid.uuid4()
                registered = cur.execute(
                    "select count(*) from ops.register_slice_checkable_done(%s,%s,%s)",
                    (slice_id, Jsonb(criteria), reg_key),
                ).fetchone()[0]
                replay = cur.execute(
                    "select count(*) from ops.register_slice_checkable_done(%s,%s,%s)",
                    (slice_id, Jsonb(criteria), reg_key),
                ).fetchone()[0]
                expect_refusal(
                    cur, "select ops.register_slice_checkable_done(%s,%s,%s)",
                    (slice_id, Jsonb(criteria[:1]), uuid.uuid4()),
                    "re-registering a slice with a looser set", match="slice_checkable_done_already_registered",
                )
            if (registered, replay) != (2, 2):
                return fail(f"registration or its replay returned the wrong rows: {(registered, replay)}")

            good_a = insert_acceptance(cur, wf1, "canary", f"{wf1}-canary")
            wrong_mode_a = insert_acceptance(cur, wf1, "shadow", f"{wf1}-shadow")
            wrong_wf_a = insert_acceptance(cur, wf2, "canary", f"{wf2}-canary")
            plan_wf1 = walk_to(cur, wf1, "shadow_compare")
            good_b, wrong_stage_b = cur.execute(
                """select max(id::text) filter (where to_stage='shadow_compare'),
                          max(id::text) filter (where to_stage='build_projection')
                     from ops.workflow_cutover_stage_transition where plan_id=%s""",
                (plan_wf1,),
            ).fetchone()
            plan_wf2 = walk_to(cur, wf2, "shadow_compare")
            wrong_wf_b = cur.execute(
                "select id::text from ops.workflow_cutover_stage_transition where plan_id=%s and to_stage='shadow_compare'",
                (plan_wf2,),
            ).fetchone()[0]

            def slice_receipt(a: str | None, b: str | None) -> Jsonb:
                return Jsonb([{"criterion": "A", "evidence_ref": a}, {"criterion": "B", "evidence_ref": b}])

            with as_login(cur, AUTHORITY):
                complete = "select ops.mark_slice_completion(%s,%s,'r',%s)"
                expect_refusal(cur, complete, (f"{slice_id}-unknown", slice_receipt(good_a, good_b), uuid.uuid4()),
                               "completing an unregistered slice", match="slice_completion_unknown_slice_id")
                expect_refusal(
                    cur, complete,
                    (slice_id, Jsonb([{"criterion": "A", "evidence_ref": good_a},
                                      {"criterion": "A", "evidence_ref": good_a}]), uuid.uuid4()),
                    "[A, A] against the registered {A, B}", match="slice_completion_duplicate_criterion",
                )
                expect_refusal(
                    cur, complete, (slice_id, Jsonb([{"criterion": "A", "evidence_ref": good_a}]), uuid.uuid4()),
                    "a subset of the registered criteria", match="slice_completion_criteria_set_mismatch",
                )
                expect_refusal(
                    cur, complete,
                    (slice_id, Jsonb([{"criterion": "A", "evidence_ref": good_a}, {"criterion": "B", "evidence_ref": good_b},
                                      {"criterion": "C", "evidence_ref": good_a}]), uuid.uuid4()),
                    "an invented extra criterion", match="slice_completion_criteria_set_mismatch",
                )
                for label, a, b in [
                    ("A proven by another workflow's accepted canary", wrong_wf_a, good_b),
                    ("A proven by the right workflow in the wrong mode", wrong_mode_a, good_b),
                    ("B proven by another workflow's transition", good_a, wrong_wf_b),
                    ("B proven by a transition into the wrong stage", good_a, wrong_stage_b),
                    ("B with no evidence at all", good_a, None),
                ]:
                    expect_refusal(cur, complete, (slice_id, slice_receipt(a, b), uuid.uuid4()), label,
                                   match="slice_completion_complete_requires_every_criterion_proven")

            with as_login(cur, WRITER):
                # 0619: a progress mark records only the server-derived actor,
                # which the Worker sets for every writer call.
                cur.execute("select set_config('carr.acting_actor_slug','claude',true)")
                progress = cur.execute(
                    "select status, criteria_receipt from ops.mark_slice_progress(%s,'in_progress',%s,'partial',%s,'writer')",
                    (slice_id, slice_receipt(good_a, None), uuid.uuid4()),
                ).fetchone()
                expect_refusal(
                    cur, "select ops.mark_slice_progress(%s,'complete',%s,'r',%s,'writer')",
                    (slice_id, slice_receipt(good_a, good_b), uuid.uuid4()),
                    "the writer door writing complete", match="slice_progress_status_invalid",
                )
                expect_refusal(
                    cur, "select ops.mark_slice_progress(%s,'blocked',%s,null,%s,'writer')",
                    (slice_id, slice_receipt(good_a, None), uuid.uuid4()),
                    "blocked without a reason", match="slice_progress_blocked_requires_reason",
                )
            if progress[0] != "in_progress" or [el["pass"] for el in progress[1]] != [True, False]:
                return fail(f"progress mark did not recompute pass per criterion: {progress}")

            with as_login(cur, AUTHORITY):
                done = cur.execute(
                    "select id::text, status, marked_by_actor_slug, criteria_receipt from ops.mark_slice_completion(%s,%s,'all proven',%s)",
                    (slice_id, Jsonb([{"criterion": "B", "evidence_ref": good_b, "pass": False},
                                      {"criterion": "A", "evidence_ref": good_a}]), uuid.uuid4()),
                ).fetchone()
            if done[1:3] != ("complete", "joe") or not all(el["pass"] is True for el in done[3]) \
                    or {el["workflow_key"] for el in done[3]} != {wf1}:
                return fail(f"authority completion did not record a proven, workflow-bound mark: {done}")
            with as_login(cur, READER):
                current = cur.execute("select id::text, status from ops.read_slice_completion(%s)",
                                      (slice_id,)).fetchone()
            if current != (done[0], "complete"):
                return fail(f"read_slice_completion did not return the completion mark: {current}")
            expect_refusal(cur, "update ops.slice_completion_mark set status='blocked' where id=%s", (done[0],),
                           "rewriting a completion mark (as table owner)", match="append-only")
            expect_refusal(cur, "delete from ops.slice_checkable_done_registry where slice_id=%s", (slice_id,),
                           "deleting a registered criterion (as table owner)", match="immutable")

            # ================================================================
            # record_workflow_caller (writer): registered workflow, history.
            # ================================================================
            key_c = f"r02gate-{token}-caller"
            insert_job_definition(cur, key_c)
            with as_login(cur, WRITER):
                expect_refusal(
                    cur, "select ops.record_workflow_caller(%s,1,%s,'script','done',null,'commit-abc',null)",
                    (f"{key_c}-unregistered", "caller.py"),
                    "status=done for an unregistered workflow", match="caller_done_requires_registered_job_definition",
                )
                for status, blocked, evidence in [("remaining", None, None), ("blocked", "waiting", None),
                                                  ("done", None, "commit-xyz")]:
                    cur.execute(
                        "select id from ops.record_workflow_caller(%s,1,'caller-hist.py','script',%s,%s,%s,'writer')",
                        (key_c, status, blocked, evidence),
                    )
            with as_login(cur, AUTHORITY):
                expect_refusal(
                    cur, "select ops.record_workflow_caller(%s,1,'x','script','remaining',null,null,null)", (key_c,),
                    "the authority login on the writer-only caller door", match="permission denied",
                )
            with as_login(cur, READER):
                history = [r[0] for r in cur.execute(
                    """select status from ops.workflow_caller_history
                        where workflow_key=%s and caller_locator='caller-hist.py' order by recorded_at, id""",
                    (key_c,),
                ).fetchall()]
            if sorted(history) != ["blocked", "done", "remaining"] or len(history) != 3:
                return fail(f"workflow_caller_history did not keep every status change: {history}")
            expect_refusal(
                cur, "update ops.workflow_caller_history set status='remaining' where workflow_key=%s", (key_c,),
                "rewriting workflow_caller_history (as table owner)", match="append-only",
            )

        print("PASS: V5-R02 SQL doors hold under production-shaped logins (authority, writer, jobs, reader): "
              "an open plan never halts a live workflow, cancel recovers it, slice completion is reachable and "
              "unbeatable, retire derives its census")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
