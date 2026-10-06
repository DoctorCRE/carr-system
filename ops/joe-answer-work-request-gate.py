#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only gate for the needs_joe -> triaged human answer door.

Exercises ops.answer_work_request_for_joe (migration 0575) end to end against
a real PostgreSQL, the same way ops/program6-human-triage-gate.py exercises
its sibling ops.triage_sourced_work_request (0175): a generic writer role is
refused, only carr_authority (as one of the two provisioned human authority
logins) may call it, the answering human is session-derived rather than
caller-supplied, the compare-and-swap refuses a stale version or the wrong
state, an empty-acceptance-criteria row is refused, scope_confirmed=false is
refused, an idempotency-key replay returns the same receipt without a second
transition, and reusing a key for a materially different answer is refused.
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"joe-answer-work-request-gate: FAIL — {message}", file=sys.stderr)
    return 1


def expect_refusal(cur, sql: str, params: tuple, label: str) -> None:
    cur.execute("savepoint joe_answer_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint joe_answer_refusal")
        return
    cur.execute("rollback to savepoint joe_answer_refusal")
    raise RuntimeError(f"{label} was accepted")


def insert_general(cur, token: str, state: str, criteria):
    """A GENERAL (non-sourced, non-program) Work Request planted directly in
    the given state.

    ops.work_request_sourced_capture_shape (0426) admits three row shapes, and
    a general row (branch 1) is the one whose columns are ALL simply absent:
    capture_idempotency_key, organization_tenant_id, doctrine_section_id,
    doctrine_revision_id, sourced_capture_sequence, triage_classification,
    triaged_by_actor_id, triaged_at, program_key and program_ordinal must every
    one be null. Setting any of them (even just organization_tenant_id, as an
    earlier draft of this fixture did) tips the row into looking like an
    incomplete SOURCED or PROGRAM row and the CHECK refuses it outright. This
    door is explicitly scoped to general/program rows (see 0575's header), so
    the fixture is a plain insert, not a call through
    ops.capture_sourced_work_request. Branch 1 names no state restriction at
    all, so a general row may legally sit in any state -- captured included,
    which is exactly what proves 0577's card scoping: a general row outside
    needs_joe must still be refused.
    """
    return cur.execute(
        """insert into ops.work_request
             (ref,state,title,desired_outcome,acceptance_criteria,requester_actor,owner_actor,origin_ref)
           values ('WR-' || lpad(nextval('ops.work_request_ref_seq')::text,6,'0'),
                   %s,'Joe answer gate fixture','Decide the routing',%s,
                   'joe','joe',%s)
           returning id,ref,version""",
        (state, Jsonb(criteria), f"joe-answer-gate:{token}"),
    ).fetchone()


def insert_needs_joe(cur, token: str, criteria):
    return insert_general(cur, token, "needs_joe", criteria)


def insert_program(cur, token: str, state: str, criteria):
    """A PROGRAM Work Request (branch 2 of work_request_sourced_capture_shape):
    organization_tenant_id='carr-internal', a named program_key/program_ordinal,
    no doctrine linkage, requester and owner both 'joe'. Branch 2 also names no
    state restriction, so a program row may sit in 'ready' -- the shape 0577's
    card fix must refuse for lacking a shared doctrine document, the same as
    every other program-row state outside needs_joe (which a program row can
    never legally reach in the first place, since branch 2 forbids
    doctrine_section_id, matching general rows on that one point).
    """
    # ops.work_request_shape_gate() (0132) refuses an INSERT directly into
    # 'ready' with no shape_disposition, and work_request_shape_disposition_
    # complete requires shape_fixed_surface_ref, shape_rationale,
    # shape_decided_by_actor_id and shape_decided_at all set together once
    # shape_disposition='not_required' -- the same complete shape
    # engineering-claim-local-pg-gate.py's own fixture carries.
    return cur.execute(
        """insert into ops.work_request
             (ref,state,title,desired_outcome,acceptance_criteria,requester_actor,owner_actor,
              origin_ref,organization_tenant_id,program_key,program_ordinal,shape_disposition,
              shape_fixed_surface_ref,shape_rationale,shape_decided_by_actor_id,shape_decided_at)
           values ('WR-' || lpad(nextval('ops.work_request_ref_seq')::text,6,'0'),
                   %s,'Joe answer gate fixture','Decide the routing',%s,
                   'joe','joe',%s,'carr-internal','carr-ai-engineering-suite-v1',1,'not_required',
                   'fixture:joe-answer-gate-program', 'fixture currentness acceptance',
                   (select id from public.actor where slug='joe' and active and kind='human'), now())
           returning id,ref,version""",
        (state, Jsonb(criteria), f"joe-answer-gate:{token}"),
    ).fetchone()


def answer(cur, ref, version, answer_text, scope_confirmed, evidence_ref, key):
    return cur.execute(
        """select id,ref,state,version,answer_text,scope_confirmed,evidence_ref,
                  acceptance_criteria_digest,answered_by_actor_slug,answered_at,replayed
             from ops.answer_work_request_for_joe(%s,%s,%s,%s,%s,%s)""",
        (ref, version, answer_text, scope_confirmed, evidence_ref, key),
    ).fetchone()


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            joe = cur.execute("select id from actor where slug='joe' and active and kind='human'").fetchone()
            dell = cur.execute("select id from actor where slug='dell' and active and kind='human'").fetchone()
            if not joe or not dell:
                return fail("seeded active human actors joe and dell are required")
            cur.execute("""do $$ begin
              if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
              if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then create role carr_authority_dell login; end if;
            end $$""")
            cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
            grant_settable_runtime_roles(
                cur, "carr_authority_joe", "carr_authority_dell", "carr_writer", "carr_reader", "carr_jobs"
            )

            token = uuid.uuid4().hex
            criteria = [{"id": "JOE-ANSWER", "text": "A human decision is recorded"}]

            # Plain owner-connection insert, matching
            # engineering-claim-local-pg-gate.py's fixture pattern: general
            # (non-sourced) rows carry no INSERT trigger to bypass, and
            # carr_writer itself has no raw INSERT on ops.work_request -- the
            # whole point of routing writes through SECURITY DEFINER doors.
            request_id, ref, version = insert_needs_joe(cur, token, criteria)

            # Non-authority roles are refused outright, including the generic
            # writer that placed the fixture -- the same posture as 0175.
            for role in ("carr_writer", "carr_reader", "carr_jobs"):
                set_local_role(cur, role)
                expect_refusal(
                    cur, "select * from ops.answer_work_request_for_joe(%s,%s,%s,%s,%s,%s)",
                    (ref, version, "A generic role should not answer this.", True, None, uuid.uuid4()),
                    f"{role} answering Joe",
                )
                cur.execute("reset role")

            # scope_confirmed must be exactly true.
            cur.execute("set session authorization carr_authority_dell")
            expect_refusal(
                cur, "select * from ops.answer_work_request_for_joe(%s,%s,%s,%s,%s,%s)",
                (ref, version, "Scope was not confirmed.", False, None, uuid.uuid4()),
                "scope_confirmed=false",
            )
            cur.execute("reset session authorization")

            # An empty-acceptance-criteria row has nothing to revalidate.
            empty_id, empty_ref, empty_version = insert_needs_joe(cur, f"{token}-empty", [])
            cur.execute("set session authorization carr_authority_dell")
            expect_refusal(
                cur, "select * from ops.answer_work_request_for_joe(%s,%s,%s,%s,%s,%s)",
                (empty_ref, empty_version, "Nothing to revalidate.", True, None, uuid.uuid4()),
                "empty acceptance_criteria",
            )
            cur.execute("reset session authorization")

            # Real transition, under Dell's authority connection.
            key = uuid.uuid4()
            cur.execute("set session authorization carr_authority_dell")
            answered = answer(cur, ref, version, "Go with option B.", True, "loop:501", key)
            cur.execute("reset session authorization")
            digest_ok = isinstance(answered[7], str) and answered[7].startswith("sha256:") and len(answered[7]) == 71
            if not answered or answered[:9] != (
                request_id, ref, "triaged", version + 1, "Go with option B.", True, "loop:501",
                answered[7], "dell",
            ) or not digest_ok or answered[10] is not False:
                return fail(f"Dell authority did not persist the exact answer: {answered}")

            # Idempotent replay: same key, same everything -> same receipt.
            cur.execute("set session authorization carr_authority_dell")
            replay = answer(cur, ref, version, "Go with option B.", True, "loop:501", key)
            cur.execute("reset session authorization")
            if replay[:9] != answered[:9] or replay[10] is not True:
                return fail("exact replay did not return the persisted receipt")

            # Same key, different answer_text -> refused, not silently accepted.
            cur.execute("set session authorization carr_authority_dell")
            expect_refusal(
                cur, "select * from ops.answer_work_request_for_joe(%s,%s,%s,%s,%s,%s)",
                (ref, version, "A completely different answer.", True, "loop:501", key),
                "same key, different answer_text",
            )
            cur.execute("reset session authorization")

            # A stale version is refused after the row has already moved.
            cur.execute("set session authorization carr_authority_joe")
            expect_refusal(
                cur, "select * from ops.answer_work_request_for_joe(%s,%s,%s,%s,%s,%s)",
                (ref, version, "Too late, already triaged.", True, None, uuid.uuid4()),
                "post-answer stale version",
            )
            cur.execute("reset session authorization")

            # The answering human is session-derived: Joe cannot answer as
            # himself under a key already bound to Dell's answer.
            cur.execute("set session authorization carr_authority_joe")
            expect_refusal(
                cur, "select * from ops.answer_work_request_for_joe(%s,%s,%s,%s,%s,%s)",
                (ref, version, "Go with option B.", True, "loop:501", key),
                "a different authority replaying another human's key",
            )
            cur.execute("reset session authorization")

            set_local_role(cur, "carr_reader")
            stored = cur.execute(
                "select state,joe_answer_text,joe_answered_by_actor_id,version from ops.work_request where id=%s",
                (request_id,),
            ).fetchone()
            cur.execute("reset role")
            if stored != ("triaged", "Go with option B.", dell[0], version + 1):
                return fail(f"answer widened Work Request state or attribution: {stored}")

            # ops.work_request_card (0577) admits ONLY the general needs_joe
            # row shape -- never a general row in any other state, and never a
            # program row lacking a shared doctrine document. Proved directly
            # against the real predicate, under carr_reader, the role the card
            # is actually granted to.
            general_captured_id, general_captured_ref, _ = insert_general(cur, f"{token}-general-captured", "captured", criteria)
            program_ready_id, program_ready_ref, _ = insert_program(cur, f"{token}-program-ready", "ready", criteria)
            set_local_role(cur, "carr_reader")
            general_captured_card = cur.execute(
                "select * from ops.work_request_card(%s,%s)", (general_captured_ref, "carr-internal")
            ).fetchone()
            program_ready_card = cur.execute(
                "select * from ops.work_request_card(%s,%s)", (program_ready_ref, "carr-internal")
            ).fetchone()
            # empty_ref stayed in needs_joe throughout this gate -- every call
            # against it above was refused (scope_confirmed never true when it
            # was tried, and it carries no acceptance_criteria), so it is still
            # the exact general needs_joe row the card must still return.
            needs_joe_card = cur.execute(
                "select ref,state,version,acceptance_criteria from ops.work_request_card(%s,%s)",
                (empty_ref, "carr-internal"),
            ).fetchone()
            cur.execute("reset role")
            if general_captured_card is not None:
                return fail(f"a general captured row was returned by the card: {general_captured_card}")
            if program_ready_card is not None:
                return fail(f"a program ready row lacking a shared doctrine document was returned by the card: {program_ready_card}")
            if not needs_joe_card or needs_joe_card[:3] != (empty_ref, "needs_joe", empty_version) or needs_joe_card[3] != []:
                return fail(f"the general needs_joe row itself was not returned correctly by the card: {needs_joe_card}")

        print("PASS: needs_joe -> triaged is authority-bound, scope-and-evidence-checked, and idempotent; "
              "the card admits only the general needs_joe row shape")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
