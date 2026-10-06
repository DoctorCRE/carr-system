#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-Postgres proof for the slice done-record doors
(migration 0628), every door call made as a production-shaped login.

Logins (as ops/workflow-cutover-r02-local-pg-gate.py provisions them):
  carr_authority_joe   member of carr_authority only (partner authority);
  sdmgate_writer       member of carr_writer only (the MCP writer login);
  sdmgate_reader       member of carr_reader only.
The automated seat is the writer login with the server-derived
carr.acting_actor_slug set to a slug in ops.slice_marker_seat (joe-local),
exactly as mcp.js's setWriterActorContext sets it for the machine token.
The superuser session only writes FIXTURE rows (a catalog doctrine revision,
production releases, job receipts) and probes append-only triggers.

Covered:
  AUTHORITY  the seat doors refuse a writer with no acting actor and with a
             non-seat actor (claude); partner-only doors refuse the writer;
             seat-only doors refuse the authority login.
  ALLOWLIST  ops.slice_criterion_allowed_kinds derives the kinds the seat may
             bind from the criterion's wording (restore -> staging restore
             live_check; refusals/negatives -> nothing; acyclicity -> the
             accepted portfolio record; zero effects -> effect-free live_check;
             runtime/human outcomes -> nothing; else shipped_release), and the
             bind door refuses the seat anything else, any refusal_proof, and an
             accepted-record/effect-free key other than the catalog portfolio.
  CATALOG    registration from the catalog registers exactly the catalog's
             criteria, unbound, and refuses a slice the catalog lacks, a
             second registration and an unrelated idempotency replay.
  MEMBERS    record_release_slice_members refuses a non-complete or staging
             release, a slice the catalog lacks, a subject that does not name
             the slice (the reviewer's "typo fix in README" forge) and an invalid
             sha; members are append-only.
  BINDING    a seat shipped_release binding must name one member of THIS slice
             in a complete production release and is only a PROPOSAL: nothing
             proves it until a partner confirms it by rebinding; the seat binds
             a criterion once; a partner rebinds (and unbinds) at any time and
             wins; a partner hold refuses a seat bind.
  RESOLVER   shipped_release passes only for the bound member; live_check
             (job_receipt) only for a completion receipt of the bound job;
             unbound never passes, even with a ref that would prove another
             binding.
  TERMINAL   automation can never SET complete: no auto-complete door exists,
             the writer holds no complete door, the shared complete body refuses
             any non-partner caller, and a CHECK refuses a complete row not
             marked_via='authority' even when the owner writes it.
  MARKER     a proposal refuses while any criterion is unproven; once all are
             proven the seat PROPOSES and the slice stays unmarked-complete;
             ops.confirm_slice_completions (partner) re-evaluates at confirm time
             (a partner unbind after the proposal -> not_proven), refuses a held
             slice (held) and a proposal older than the latest mark
             (stale_proposal), confirms in a batch with per-slice outcomes
             (confirmed / no_proposal / unknown_slice), replays idempotently and
             reports already_complete; progress records the server-derived actor
             only; a partner hold refuses every non-authority mark, bind and
             proposal until released; a partner complete holds too.
  PROBES     the round-2 reviewer's four holes as refusals or proposal-only:
             A1 a partner shipped_release registration naming no member is
             refused, and a seat-forged member only ever yields a proposal;
             A2 a catalog the seat rewrote itself, registered, bound and proposed
             yields a proposal and no complete mark; A3 wording that names a
             refusal or a human observation allows nothing, and the rest can only
             be proposed; A4 a partner-confirmed member re-bound by the seat to
             another criterion stays a proposal.
  RACES      (committed, two logins) are in ops/slice-done-marker-race-local-pg-gate.py.
  PORTFOLIO  accepted_record passes only for the CURRENT, INTACT accepted
             revision of the catalog portfolio (a real propose / independent
             review / partner accept through the portfolio doors) and fails the
             moment a row is tampered with or the ref names another portfolio;
             live_check portfolio_acceptance_effect_free fails once a job is
             created in an acceptance window; refusal_proof is refused at
             registration and at every bind (no server-recorded gate result
             exists), so a negatives criterion stays unbound until a partner
             decides it; read_slice_done_state reports candidate_passes from the
             live rows.
"""

from __future__ import annotations

import hashlib
import itertools
import json
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
WRITER = "sdmgate_writer"
READER = "sdmgate_reader"
SEAT = "joe-local"
CATALOG_DOC = "doctorcre-v5-astra-integration-review"
CATALOG_SECTION = "v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09"
SLICE = "V5-ZT01"
SLICE_OTHER = "V5-ZT02"
SLICE_PF = "V5-ZT03"
CRITERIA = ["behaviour ships", "live job completes", "runtime outcome observed"]
PF_CRITERIA = ["counts and acyclicity pass", "write-verb negatives refuse", "acceptance creates zero effects"]
CATALOG_PORTFOLIO = "DoctorCre-v5"
PF_CHILDREN = ("foundation-and-control-plane", "assurance-fabric", "product-journeys", "rollout-and-retirement")
PLACEHOLDER = "sha256:" + "0" * 64


def fail(message: str) -> int:
    print(f"slice-done-marker-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


@contextmanager
def as_login(cur: Any, login: str, acting: str | None = None) -> Iterator[None]:
    cur.execute(sql.SQL("set session authorization {}").format(sql.Identifier(login)))
    who = cur.execute("select session_user::text, current_user::text").fetchone()
    if who != (login, login):
        raise RuntimeError(f"expected session and current user {login!r}, got {who!r}")
    cur.execute("select set_config('carr.acting_actor_slug', %s, true)", (acting or "",))
    yield
    cur.execute("select set_config('carr.acting_actor_slug', '', true)")
    cur.execute("reset session authorization")


def expect_refusal(cur: Any, query: str, params: tuple, label: str, *, match: str) -> None:
    cur.execute("savepoint sdm_refusal")
    try:
        cur.execute(query, params)
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint sdm_refusal")
        if match not in str(exc):
            raise RuntimeError(f"{label} was refused for the wrong reason: {exc}") from exc
        return
    cur.execute("rollback to savepoint sdm_refusal")
    raise RuntimeError(f"{label} was accepted")


def provision_logins(cur: Any) -> None:
    cur.execute(f"""
      do $$ begin
        if not exists (select 1 from pg_roles where rolname='{AUTHORITY}') then create role {AUTHORITY} login; end if;
        if not exists (select 1 from pg_roles where rolname='{WRITER}') then create role {WRITER} login; end if;
        if not exists (select 1 from pg_roles where rolname='{READER}') then create role {READER} login; end if;
      end $$;
    """)
    cur.execute(f"grant carr_authority to {AUTHORITY}")
    cur.execute(f"grant carr_writer to {WRITER}")
    cur.execute(f"grant carr_reader to {READER}")
    for login, bundle in ((AUTHORITY, "carr_authority"), (WRITER, "carr_writer"), (READER, "carr_reader")):
        held = {r[0] for r in cur.execute(
            """select b.rolname from pg_auth_members m join pg_roles b on b.oid=m.roleid
                 join pg_roles l on l.oid=m.member where l.rolname=%s""", (login,)).fetchall()}
        runtime = held & {"carr_authority", "carr_writer", "carr_jobs", "carr_reader"}
        if runtime != {bundle}:
            raise RuntimeError(f"{login} must hold exactly {bundle}, holds {sorted(runtime)}")


def install_catalog(cur: Any, extra: list[dict] | None = None) -> None:
    """Point the catalog doctrine section at a fixture revision naming two
    slices, creating the document and section when this database has none."""
    actor_id = cur.execute(
        """insert into actor(slug,kind,display_name) values ('sdm-gate-fixture','automation','slice gate fixture')
           on conflict (slug) do update set active=true returning id""").fetchone()[0]
    doc = cur.execute("select id from doctrine_document where slug=%s", (CATALOG_DOC,)).fetchone()
    if doc is None:
        doc = cur.execute(
            """insert into doctrine_document(slug,title,content_class,visibility,created_by)
               values (%s,'catalog fixture','reference','shared',%s) returning id""", (CATALOG_DOC, actor_id)).fetchone()
    section = cur.execute("select id, current_version from doctrine_section where document_id=%s and section_key=%s",
                          (doc[0], CATALOG_SECTION)).fetchone()
    if section is None:
        section = cur.execute(
            """insert into doctrine_section(document_id,section_key,title,ordinal,status,current_version)
               values (%s,%s,'catalog fixture',1,'active',0) returning id, current_version""",
            (doc[0], CATALOG_SECTION)).fetchone()
    body = json.dumps({"slices": [
        {"proposed_id": SLICE, "checkable_done": CRITERIA},
        {"proposed_id": SLICE_OTHER, "checkable_done": ["other criterion"]},
        {"proposed_id": SLICE_PF, "checkable_done": PF_CRITERIA},
    ] + (extra or [])})
    version = int(section[1] or 0) + 1
    revision = cur.execute(
        """insert into doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash,commit_message)
           values (%s,%s,%s,%s,%s,%s,'slice gate fixture') returning id""",
        (section[0], version, actor_id, Jsonb({"text": body}), body, hashlib.sha256(body.encode()).hexdigest()),
    ).fetchone()[0]
    cur.execute("update doctrine_section set current_revision_id=%s, current_version=%s, status='active' where id=%s",
                (revision, version, section[0]))


def insert_release(cur: Any, key: str, *, environment: str = "production", state: str = "complete") -> str:
    """Fixture release, written as owner with triggers off (their approval
    machinery is not what this gate proves); every CHECK still applies."""
    cur.execute("set local session_replication_role=replica")
    rid = cur.execute(
        """insert into ops.release
             (release_key,service_id,environment,state,git_sha,maker_actor,source_kind,source_ref,
              artifact_digest,dependency_lock_digest,test_evidence_ref,security_evidence_ref,
              maker_verification_ref,plan_hash,approved_by_actor,approved_at,approval_expires_at,ended_at,
              performance_budget_ref,performance_budget_ms,recovery_strategy,provider,provider_version_id,
              verifier_actor,verifier_evidence_ref,rollback_ready,rollback_plan_ref)
           values (%s,gen_random_uuid(),%s,%s,%s,'sdm-gate','operator','ops/slice-done-marker-local-pg-gate.py',
                   'sha256:a','sha256:b','test:x','sec:x','maker:x','plan:x','joe',now()-interval '1 hour',
                   now()+interval '1 hour', now(), 'budget:x', 1000, 'forward_fix', 'cloudflare-workers',
                   gen_random_uuid()::text, 'sdm-verifier', 'verify:x', true, 'rollback:x') returning id::text""",
        (key, environment, state, hashlib.sha1(key.encode()).hexdigest()),
    ).fetchone()[0]
    cur.execute("set local session_replication_role=origin")
    return rid


_JOB_SLOTS = itertools.count(1)


def insert_job_receipt(cur: Any, definition_key: str, kind: str) -> str:
    cur.execute("set local session_replication_role=replica")
    job = cur.execute(
        """insert into ops.job (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,
                                 created_at)
           values (%s,1,%s,now() - make_interval(secs => %s),1,30,now() - make_interval(days => 1, secs => %s))
           returning id""",
        (definition_key, uuid.uuid4().hex, (slot := next(_JOB_SLOTS)), slot)).fetchone()[0]
    rid = cur.execute(
        "insert into ops.job_receipt (job_id,attempt,kind,receipt_ref) values (%s,1,%s,%s) returning id::text",
        (job, kind, f"sdm-gate:{uuid.uuid4().hex}")).fetchone()[0]
    cur.execute("set local session_replication_role=origin")
    return rid


def member(sha_seed: str, slice_id: str = SLICE, subject: str | None = None) -> dict:
    return {"slice_id": slice_id, "commit_sha": hashlib.sha1(sha_seed.encode()).hexdigest(),
            "pr_number": 1, "subject": subject or f"{slice_id}: fixture {sha_seed}", "attribution": "explicit"}


def receipt(refs: dict[str, str | None]) -> Jsonb:
    return Jsonb([{"criterion": c, "evidence_ref": refs.get(c)} for c in CRITERIA])


BIND = "select ops.bind_slice_criterion_evidence(%s,%s,%s,%s,%s,%s,%s,%s,%s)"
REBIND = "select ops.rebind_slice_criterion_evidence(%s,%s,%s,%s,%s,%s,%s,%s,%s)"


def bind_args(slice_id: str, criterion: str, kind: str, source: str | None = None, key: str | None = None,
              write_reason: str | None = None, member_id: str | None = None, reason: str = "r") -> tuple:
    return (slice_id, criterion, kind, source, key, write_reason, member_id, reason, uuid.uuid4())


ALLOWED = [
    ("staging restore succeeds", ["live_check:staging_restore_only_result"]),
    ("write-verb negatives refuse", []),
    ("bypass attempts are refused", []),
    ("counts and acyclicity pass", ["accepted_record:portfolio_revision_acceptance"]),
    ("acceptance creates zero effects", ["live_check:portfolio_acceptance_effect_free"]),
    ("Joe uses it for a week", []),
    ("pilot outcome observed", []),
    ("behaviour ships", ["shipped_release:"]),
    ("live job completes", ["shipped_release:"]),
]

SUBJECTS = [
    ("V5-ZT01: fixture", "V5-ZT01", True),
    ("F03 live admission validators", "V5-F03", True),
    ("V5-J101 daily workspace (#1259)", "V5-J101", True),
    ("typo fix in README", "V5-F03", False),
    ("F031 is another id", "V5-F03", False),
    ("V5-F03X is another id", "V5-F03", False),
    ("ZT01 bare ids only for F/A/S/J", "V5-ZT01", False),
]


def run(cur: Any) -> str | None:
    provision_logins(cur)
    install_catalog(cur)
    token = uuid.uuid4().hex[:8]
    job_key = f"sdm-gate-job-{token}"

    # ---------------------------------------------------------------- AUTHORITY
    seat_doors = [
        ("register from catalog", "select * from ops.register_slice_criteria_from_catalog(%s,%s)", (SLICE, uuid.uuid4())),
        ("bind", BIND, bind_args(SLICE, CRITERIA[0], "shipped_release", member_id=str(uuid.uuid4()))),
        ("record members", "select * from ops.record_release_slice_members(%s,%s)", ("none", Jsonb([member("a")]))),
        ("propose complete", "select ops.propose_slice_completion(%s,%s,'r',%s)", (SLICE, receipt({}), uuid.uuid4())),
    ]
    for acting in (None, "claude"):
        with as_login(cur, WRITER, acting):
            for label, query, params in seat_doors:
                expect_refusal(cur, query, params, f"writer ({acting or 'no actor'}) {label}",
                               match="slice_marker_seat_required")
    with as_login(cur, WRITER, SEAT):
        for label, query, params in [
            ("rebind", REBIND, bind_args(SLICE, CRITERIA[0], "unbound")),
            ("hold", "select ops.set_slice_mark_hold(%s,'hold','blocked','r',%s)", (SLICE, uuid.uuid4())),
            ("partner complete", "select ops.mark_slice_completion(%s,%s,'r',%s)", (SLICE, receipt({}), uuid.uuid4())),
            ("partner register", "select ops.register_slice_checkable_done(%s,'[]'::jsonb,%s)", (SLICE, uuid.uuid4())),
            ("partner confirm", "select * from ops.confirm_slice_completions(array[%s],'r',%s)", (SLICE, uuid.uuid4())),
            ("the shared complete body", "select ops.slice_mark_complete_insert(%s,%s,'r',%s,'joe-local','authority')",
             (SLICE, receipt({}), uuid.uuid4())),
        ]:
            expect_refusal(cur, query, params, f"seat {label}", match="permission denied")

    # ----------------------------------------------------------------- TERMINAL
    # No automation door to complete exists, and the writer can execute no
    # function that writes a complete mark.
    if cur.execute("select to_regprocedure('ops.auto_mark_slice_completion(text,jsonb,text,uuid)')").fetchone()[0]:
        return "an automation complete door (ops.auto_mark_slice_completion) still exists"
    writer_complete = cur.execute(
        """select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'ops' and p.proname in ('mark_slice_completion', 'confirm_slice_completions',
                                                      'slice_mark_complete_insert')
              and has_function_privilege('carr_writer', p.oid, 'execute')""").fetchall()
    if writer_complete:
        return f"the writer can execute a complete door: {writer_complete}"
    expect_refusal(cur, "select ops.slice_mark_complete_insert(%s,%s,'r',%s,'joe-local','automation')",
                   (SLICE, receipt({}), uuid.uuid4()), "the shared complete body called for automation (owner)",
                   match="slice_complete_is_a_partner_act")
    expect_refusal(cur, """insert into ops.slice_completion_mark (slice_id,status,criteria_receipt,reason,marked_by_actor_slug,
                                                                  idempotency_key,marked_via)
                           values (%s,'complete','[]'::jsonb,'r','joe-local',%s,'automation')""",
                   (SLICE, uuid.uuid4()), "an automation complete row written directly (owner)",
                   match="slice_completion_mark_complete_is_partner")
    # NULL-safe: a complete row with no marked_via at all is refused too (a
    # plain `marked_via = 'authority'` CHECK passes on NULL).
    expect_refusal(cur, """insert into ops.slice_completion_mark (slice_id,status,criteria_receipt,reason,marked_by_actor_slug,
                                                                  idempotency_key)
                           values (%s,'complete','[]'::jsonb,'r','joe-local',%s)""",
                   (SLICE, uuid.uuid4()), "a complete row with NULL marked_via written directly (owner)",
                   match="slice_completion_mark_complete_is_partner")
    # VALID, not NOT VALID: the constraint was checked against every existing row.
    validated = cur.execute("""select convalidated, pg_get_constraintdef(oid) from pg_constraint
                                where conrelid = 'ops.slice_completion_mark'::regclass
                                  and conname = 'slice_completion_mark_complete_is_partner'""").fetchone()
    if not validated or validated[0] is not True or "DISTINCT FROM" not in validated[1].upper():
        return f"the complete-is-partner CHECK is not a validated NULL-safe constraint: {validated}"
    with as_login(cur, AUTHORITY, SEAT):
        for label, query, params in seat_doors:
            expect_refusal(cur, query, params, f"authority login {label}", match="permission denied")

    # --------------------------------------------------------------- ALLOWLIST
    with as_login(cur, READER):
        for wording, kinds_expected in ALLOWED:
            kinds_got = cur.execute("select ops.slice_criterion_allowed_kinds(%s)", (wording,)).fetchone()[0]
            if kinds_got != kinds_expected:
                return f"allowlist: {wording!r} allows {kinds_got}, expected {kinds_expected}"
    for subject, slice_id, names_expected in SUBJECTS:
        names_got = cur.execute("select ops.slice_subject_names_slice(%s,%s)", (subject, slice_id)).fetchone()[0]
        if names_got is not names_expected:
            return f"subject check: {subject!r} naming {slice_id} gave {names_got}, expected {names_expected}"

    # ------------------------------------------------------------------ CATALOG
    with as_login(cur, READER):
        pre = cur.execute("select ops.read_slice_done_state(%s)", (SLICE,)).fetchone()[0]
        nope = cur.execute("select ops.read_slice_done_state('V5-NOPE')").fetchone()[0]
    if pre["registered"] or pre["catalog_allowed_kinds"] != {
            CRITERIA[0]: ["shipped_release:"], CRITERIA[1]: ["shipped_release:"], CRITERIA[2]: []} \
            or nope["catalog_allowed_kinds"] is not None:
        return f"read_slice_done_state catalog_allowed_kinds wrong before registration: {pre} / {nope}"
    reg_key = uuid.uuid4()
    with as_login(cur, WRITER, SEAT):
        expect_refusal(cur, "select * from ops.register_slice_criteria_from_catalog(%s,%s)", ("V5-NOPE", uuid.uuid4()),
                       "a slice the catalog lacks", match="slice_not_in_catalog")
        rows = cur.execute("select criterion, evidence_kind from ops.register_slice_criteria_from_catalog(%s,%s)",
                           (SLICE, reg_key)).fetchall()
        replay = cur.execute("select count(*) from ops.register_slice_criteria_from_catalog(%s,%s)",
                             (SLICE, reg_key)).fetchone()[0]
        expect_refusal(cur, "select * from ops.register_slice_criteria_from_catalog(%s,%s)", (SLICE, uuid.uuid4()),
                       "a second registration", match="slice_checkable_done_already_registered")
        expect_refusal(cur, "select * from ops.register_slice_criteria_from_catalog(%s,%s)", (SLICE_OTHER, reg_key),
                       "the idempotency key replayed for another slice", match="idempotency_key_reused")
    if sorted(rows) != sorted((c, "unbound") for c in CRITERIA) or replay != len(CRITERIA):
        return f"catalog registration did not register exactly the catalog's criteria, unbound: {rows} / {replay}"
    reg = cur.execute("""select registered_via, registered_by_actor_slug, catalog_revision_id is not null
                           from ops.slice_checkable_done_registration where slice_id=%s""", (SLICE,)).fetchone()
    if reg != ("automation", SEAT, True):
        return f"registration audit fields wrong: {reg}"

    # ------------------------------------------------------------------ MEMBERS
    rel_ok = f"r-sdm-{token}-ok"
    insert_release(cur, rel_ok)
    insert_release(cur, f"r-sdm-{token}-cand", state="candidate")
    insert_release(cur, f"r-sdm-{token}-stg", environment="staging")
    with as_login(cur, WRITER, SEAT):
        for key, label in ((f"r-sdm-{token}-cand", "a candidate release"), (f"r-sdm-{token}-stg", "a staging release")):
            expect_refusal(cur, "select * from ops.record_release_slice_members(%s,%s)", (key, Jsonb([member("a")])),
                           label, match="release_not_complete_production")
        expect_refusal(cur, "select * from ops.record_release_slice_members(%s,%s)",
                       (rel_ok, Jsonb([member("a", "V5-NOPE")])), "a non-catalog slice",
                       match="slice_not_in_catalog")
        # The reviewer's forge: a member whose subject never names the slice.
        expect_refusal(cur, "select * from ops.record_release_slice_members(%s,%s)",
                       (rel_ok, Jsonb([member("forge", SLICE, "typo fix in README")])),
                       "a member whose subject does not name the slice", match="member_subject_does_not_name_slice")
        bad_sha = dict(member("a"), commit_sha="not-a-sha")
        expect_refusal(cur, "select * from ops.record_release_slice_members(%s,%s)", (rel_ok, Jsonb([bad_sha])),
                       "a member with an invalid commit sha", match="member_commit_sha_invalid")
        members = cur.execute("select id::text, slice_id from ops.record_release_slice_members(%s,%s)",
                              (rel_ok, Jsonb([member("a"), member("b", SLICE_OTHER)]))).fetchall()
        again = cur.execute("select count(*) from ops.record_release_slice_members(%s,%s)",
                            (rel_ok, Jsonb([member("a")]))).fetchone()[0]
    if len(members) != 2 or again != 2:
        return f"membership recording is not idempotent per (release, slice, commit): {members} / {again}"
    mine = next(m for m, s in members if s == SLICE)
    theirs = next(m for m, s in members if s == SLICE_OTHER)
    with as_login(cur, WRITER, SEAT):
        mine2 = cur.execute("select id::text from ops.record_release_slice_members(%s,%s) where commit_sha=%s",
                            (rel_ok, Jsonb([member("c")]), hashlib.sha1(b"c").hexdigest())).fetchone()[0]
    # A member of a non-complete release, written as owner, must never prove.
    cur.execute("set local session_replication_role=replica")
    cand_id = cur.execute("select id, git_sha from ops.release where release_key=%s", (f"r-sdm-{token}-cand",)).fetchone()
    stale = cur.execute(
        """insert into ops.release_slice_member(release_id,release_git_sha,slice_id,commit_sha,subject,attribution,recorded_by_actor_slug)
           values (%s,%s,%s,%s,'V5-ZT01: fixture','explicit','owner') returning id::text""",
        (cand_id[0], cand_id[1], SLICE, hashlib.sha1(b"cand").hexdigest())).fetchone()[0]
    cur.execute("set local session_replication_role=origin")
    expect_refusal(cur, "delete from ops.release_slice_member where id=%s", (mine,), "deleting a member (owner)",
                   match="append-only")

    # ------------------------------------------------------------------ BINDING
    with as_login(cur, WRITER, SEAT):
        for args, label, match in (
            (bind_args(SLICE, CRITERIA[0], "shipped_release"), "a shipped_release naming no member",
             "shipped_release_binding_requires_this_slice_member"),
            (bind_args(SLICE, CRITERIA[0], "shipped_release", member_id=theirs), "another slice's member",
             "shipped_release_binding_requires_this_slice_member"),
            (bind_args(SLICE, CRITERIA[0], "shipped_release", member_id=stale), "a member of a candidate release",
             "shipped_release_binding_requires_this_slice_member"),
            (bind_args(SLICE, CRITERIA[1], "live_check", "job_receipt", job_key), "a kind the wording does not allow",
             "automation_binding_kind_not_allowed"),
            (bind_args(SLICE, CRITERIA[2], "shipped_release", member_id=mine), "shipped_release for a runtime outcome",
             "automation_binding_kind_not_allowed"),
            (bind_args(SLICE, CRITERIA[2], "refusal_proof", "ci_gate", "ci", "writes"), "a refusal_proof binding",
             "refusal_proof_has_no_server_gate_source"),
        ):
            expect_refusal(cur, BIND, args, f"seat bind: {label}", match=match)
        proposed = cur.execute(
            "select evidence_kind, bound_member_id::text, bound_via from ops.bind_slice_criterion_evidence(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            bind_args(SLICE, CRITERIA[0], "shipped_release", member_id=mine, reason="Jev matched")).fetchone()
        expect_refusal(cur, BIND, bind_args(SLICE, CRITERIA[0], "shipped_release", member_id=mine2),
                       "a second automation binding", match="criterion_already_bound")
    if proposed != ("shipped_release", mine, "automation"):
        return f"seat shipped_release proposal not recorded with its member: {proposed}"
    with as_login(cur, AUTHORITY):
        cur.execute(REBIND, bind_args(SLICE, CRITERIA[1], "live_check", "job_receipt", job_key,
                                      reason="partner: the job proves it"))
        expect_refusal(cur, REBIND, bind_args(SLICE, CRITERIA[2], "live_check", "job_receipt", job_key, member_id=mine),
                       "a member on a non-shipped_release binding", match="bound_member_only_for_shipped_release")

    # ----------------------------------------------------------------- RESOLVER
    good_job = insert_job_receipt(cur, job_key, "completion")
    failed_job = insert_job_receipt(cur, job_key, "failure")
    other_job = insert_job_receipt(cur, f"{job_key}-other", "completion")

    def evaluate(refs: dict[str, str | None]) -> list[bool]:
        got = cur.execute("select ops.slice_completion_evaluate(%s,%s)", (SLICE, receipt(refs))).fetchone()[0]
        by = {el["criterion"]: el["pass"] for el in got}
        return [by[c] for c in CRITERIA]

    # An automated shipped_release binding is only a proposal: nothing proves it.
    if evaluate({CRITERIA[0]: mine}) != [False, False, False]:
        return "an unconfirmed automation shipped_release proposal already proved its criterion"
    with as_login(cur, READER):
        c0 = cur.execute("select ops.read_slice_done_state(%s)", (SLICE,)).fetchone()[0]["criteria"]
    c0 = next(c for c in c0 if c["criterion"] == CRITERIA[0])
    if c0["evidence_kind"] != "unbound" or (c0.get("proposal") or {}).get("bound_member_id") != mine \
            or c0["candidate_passes"] is not False:
        return f"read_slice_done_state did not report the pending proposal: {c0}"
    with as_login(cur, AUTHORITY):
        cur.execute(REBIND, bind_args(SLICE, CRITERIA[0], "shipped_release", member_id=mine,
                                      reason="partner confirms the proposed PR"))

    cases = [
        ({CRITERIA[0]: mine}, [True, False, False], "the confirmed member of a complete production release"),
        ({CRITERIA[0]: mine2}, [False, False, False], "this slice's other member, not the one bound"),
        ({CRITERIA[0]: theirs}, [False, False, False], "another slice's member"),
        ({CRITERIA[0]: stale}, [False, False, False], "a member of a candidate release"),
        ({CRITERIA[0]: good_job}, [False, False, False], "a job receipt against a shipped_release binding"),
        ({CRITERIA[1]: good_job}, [False, True, False], "a completion receipt of the bound job"),
        ({CRITERIA[1]: failed_job}, [False, False, False], "a failure receipt"),
        ({CRITERIA[1]: other_job}, [False, False, False], "another job's completion receipt"),
        ({CRITERIA[2]: mine}, [False, False, False], "any ref against an unbound criterion"),
        ({CRITERIA[2]: good_job}, [False, False, False], "a live receipt against an unbound criterion"),
    ]
    for refs, expected, label in cases:
        got = evaluate(refs)
        if got != expected:
            return f"resolver: {label}: expected {expected}, got {got}"
    # The bound member proves only while its release is a complete production one.
    cur.execute("savepoint sdm_superseded")
    cur.execute("set local session_replication_role=replica")
    cur.execute("update ops.release set state='verifying', ended_at=null where release_key=%s", (rel_ok,))
    cur.execute("set local session_replication_role=origin")
    if evaluate({CRITERIA[0]: mine}) != [False, False, False]:
        return "a bound member of a release that is no longer complete still proved its criterion"
    cur.execute("rollback to savepoint sdm_superseded")

    # ------------------------------------------------------------------- MARKER
    full = {CRITERIA[0]: mine, CRITERIA[1]: good_job, CRITERIA[2]: mine}
    propose = "select id::text, proposed_by_actor_slug from ops.propose_slice_completion(%s,%s,%s,%s)"
    confirm = "select slice_id, outcome, mark_id::text, proposal_id::text from ops.confirm_slice_completions(%s,%s,%s)"
    confirm_pinned = ("select slice_id, outcome, mark_id::text, proposal_id::text"
                      " from ops.confirm_slice_completions(%s,%s,%s,%s)")

    def latest() -> tuple:
        return cur.execute("""select status, marked_via, marked_by_actor_slug, confirmed_proposal_id::text
                                from ops.slice_completion_mark where slice_id=%s order by mark_seq desc limit 1""",
                           (SLICE,)).fetchone() or ()

    def confirm_one(slice_id: str = SLICE, key: uuid.UUID | None = None) -> tuple:
        with as_login(cur, AUTHORITY):
            return cur.execute(confirm, ([slice_id], "partner confirms", key or uuid.uuid4())).fetchone()

    with as_login(cur, WRITER, SEAT):
        expect_refusal(cur, propose, (SLICE, receipt(full), "r", uuid.uuid4()),
                       "a proposal with an unbound criterion", match="every_criterion_proven")
        prog = cur.execute(
            "select status, marked_via, marked_by_actor_slug from ops.mark_slice_progress(%s,'in_progress',%s,'partial',%s,'ignored')",
            (SLICE, receipt(full), uuid.uuid4())).fetchone()
    if prog != ("in_progress", "automation", SEAT):
        return f"seat progress mark did not record automation and the server-derived actor: {prog}"
    # The actor is the server-derived one only: none is refused, a caller slug is ignored.
    with as_login(cur, WRITER):
        expect_refusal(cur, "select ops.mark_slice_progress(%s,'in_progress',%s,'r',%s,'joe')",
                       (SLICE, receipt(full), uuid.uuid4()), "writer progress with no acting actor",
                       match="acting_actor_required")
    with as_login(cur, WRITER, "claude"):
        prog = cur.execute(
            "select marked_via, marked_by_actor_slug from ops.mark_slice_progress(%s,'in_progress',%s,'r',%s,'joe')",
            (SLICE, receipt(full), uuid.uuid4())).fetchone()
    if prog != ("writer", "claude"):
        return f"writer progress recorded a caller-supplied actor: {prog}"
    if confirm_one()[1] != "no_proposal":
        return "a slice with no proposal was not reported no_proposal"
    # Partner binds the runtime criterion to the job too; partner wins.
    with as_login(cur, AUTHORITY):
        cur.execute(REBIND, bind_args(SLICE, CRITERIA[2], "live_check", "job_receipt", job_key,
                                      reason="partner: job proves it"))
    full[CRITERIA[2]] = good_job
    # Every criterion proven: automation PROPOSES; the slice is not complete.
    with as_login(cur, WRITER, SEAT):
        p1 = cur.execute(propose, (SLICE, receipt(full), "all proven", uuid.uuid4())).fetchone()
    if p1[1] != SEAT or latest()[0] != "in_progress":
        return f"a proven proposal changed the slice's mark or lost its actor: {p1} / {latest()}"
    with as_login(cur, READER):
        st = cur.execute("select ops.read_slice_done_state(%s)", (SLICE,)).fetchone()[0]
        pending = cur.execute("select ops.pending_slice_completion_proposals()").fetchone()[0]
    if not st["awaiting_partner_confirmation"] or st["latest_proposal"]["id"] != p1[0] \
            or [x["slice_id"] for x in pending if x["slice_id"] == SLICE] != [SLICE] \
            or not next(x for x in pending if x["slice_id"] == SLICE)["passes_now"]:
        return f"the proposal is not reported as awaiting partner confirmation: {st.get('latest_proposal')} / {pending}"
    # Confirmation re-evaluates NOW: a partner unbind after the proposal makes it not_proven.
    with as_login(cur, AUTHORITY):
        cur.execute(REBIND, bind_args(SLICE, CRITERIA[0], "unbound", reason="partner: not a source criterion"))
    if evaluate(full) != [False, True, True]:
        return f"a partner unbind did not override the binding: {evaluate(full)}"
    rechecked = confirm_one()
    if rechecked[1] != "not_proven" or rechecked[2] is not None or latest()[0] != "in_progress":
        return f"confirmation did not re-check the bindings at confirm time: {rechecked} / {latest()}"
    with as_login(cur, AUTHORITY):
        cur.execute(REBIND, bind_args(SLICE, CRITERIA[0], "shipped_release", member_id=mine, reason="partner: restore"))

    # A hold committed after the proposal blocks its confirmation; the release
    # leaves it stale, so automation must propose again.
    with as_login(cur, AUTHORITY):
        held = cur.execute("select status, marked_via from ops.set_slice_mark_hold(%s,'hold','blocked','partner says wait',%s)",
                           (SLICE, uuid.uuid4())).fetchone()
        expect_refusal(cur, "select ops.set_slice_mark_hold(%s,'hold','complete','r',%s)", (SLICE, uuid.uuid4()),
                       "a hold at complete", match="slice_hold_status_invalid")
    if held != ("blocked", "authority_hold"):
        return f"partner hold was not recorded: {held}"
    if confirm_one()[1] != "held":
        return "a held slice's proposal was confirmed"
    with as_login(cur, WRITER, SEAT):
        expect_refusal(cur, propose, (SLICE, receipt(full), "r", uuid.uuid4()),
                       "a proposal over a partner hold", match="slice_mark_held_by_partner")
        expect_refusal(cur, "select ops.mark_slice_progress(%s,'in_progress',%s,'r',%s,'x')", (SLICE, receipt(full), uuid.uuid4()),
                       "seat progress over a partner hold", match="slice_mark_held_by_partner")
    with as_login(cur, WRITER, "claude"):
        expect_refusal(cur, "select ops.mark_slice_progress(%s,'in_progress',%s,'r',%s,'x')", (SLICE, receipt(full), uuid.uuid4()),
                       "writer progress over a partner hold", match="slice_mark_held_by_partner")
    with as_login(cur, AUTHORITY):
        cur.execute("select ops.set_slice_mark_hold(%s,'release',null,'go ahead',%s)", (SLICE, uuid.uuid4()))
        expect_refusal(cur, "select ops.set_slice_mark_hold(%s,'release',null,'again',%s)", (SLICE, uuid.uuid4()),
                       "releasing an unheld slice", match="slice_mark_not_held")
    if confirm_one()[1] != "stale_proposal":
        return "a proposal made before a hold/release was confirmed"
    # Any later mark (here automation's own progress) also leaves a proposal stale.
    with as_login(cur, WRITER, SEAT):
        cur.execute(propose, (SLICE, receipt(full), "re-proven", uuid.uuid4()))
        cur.execute("select ops.mark_slice_progress(%s,'in_progress',%s,'later',%s,'x')", (SLICE, receipt(full), uuid.uuid4()))
    if confirm_one()[1] != "stale_proposal":
        return "a proposal older than the latest mark was confirmed"
    with as_login(cur, WRITER, SEAT):
        p3 = cur.execute(propose, (SLICE, receipt(full), "re-proven", uuid.uuid4())).fetchone()
    # The partner names the proposal it reviewed: an older one (p1, since
    # superseded by p3) is reported proposal_superseded and confirms nothing.
    with as_login(cur, AUTHORITY):
        superseded = cur.execute(confirm_pinned, ([SLICE], "partner confirms what it reviewed", uuid.uuid4(),
                                                  Jsonb({SLICE: p1[0]}))).fetchall()
        expect_refusal(cur, confirm_pinned, ([SLICE], "r", uuid.uuid4(), Jsonb({"V5-NOPE": p3[0]})),
                       "an expected proposal for a slice not in the batch", match="expected_proposal_id_for_unlisted_slice")
        expect_refusal(cur, confirm_pinned, ([SLICE], "r", uuid.uuid4(), Jsonb({SLICE: "not-a-uuid"})),
                       "an expected proposal id that is not a uuid", match="expected_proposal_id_not_a_uuid")
    if [(r[0], r[1], r[2], r[3]) for r in superseded] != [(SLICE, "proposal_superseded", None, p3[0])] \
            or latest()[0] == "complete":
        return f"a confirmation naming a superseded proposal was not refused per slice: {superseded} / {latest()}"
    # One partner act, many slices: per-slice outcomes, idempotent replay. The
    # slice is pinned to the proposal actually reviewed (p3), which matches.
    batch_key = uuid.uuid4()
    with as_login(cur, AUTHORITY):
        rows = cur.execute(confirm_pinned, ([SLICE, "V5-NOPE", SLICE_PF, SLICE], "partner confirms the batch", batch_key,
                                            Jsonb({SLICE: p3[0]}))).fetchall()
        replay = cur.execute(confirm, ([SLICE, "V5-NOPE", SLICE_PF], "partner confirms the batch", batch_key)).fetchall()
        actor = cur.execute("select ops.authority_actor_slug()").fetchone()[0]
    outcomes = {r[0]: r[1] for r in rows}
    if outcomes != {SLICE: "confirmed", "V5-NOPE": "unknown_slice", SLICE_PF: "unknown_slice"} or len(rows) != 3:
        return f"batch confirmation outcomes wrong: {rows}"
    mark = next(r for r in rows if r[0] == SLICE)
    if mark[3] != p3[0] or latest() != ("complete", "authority", actor, p3[0]):
        return f"the confirmation did not write a partner complete mark for the proposal: {mark} / {latest()}"
    if {r[0]: (r[1], r[2]) for r in replay} != {r[0]: (r[1], r[2]) for r in rows}:
        return f"replaying the batch key did not return the same marks: {replay} / {rows}"
    if confirm_one()[1] != "already_complete":
        return "a second confirmation of a complete slice was not reported already_complete"
    # A partner complete holds: no automated mark or proposal over it.
    with as_login(cur, WRITER, SEAT):
        expect_refusal(cur, "select ops.mark_slice_progress(%s,'in_progress',%s,'r',%s,'x')", (SLICE, receipt(full), uuid.uuid4()),
                       "seat progress over a partner complete", match="slice_mark_held_by_partner")
        expect_refusal(cur, propose, (SLICE, receipt(full), "r", uuid.uuid4()),
                       "a proposal over a partner complete", match="slice_mark_held_by_partner")
    # The direct partner door still works (and holds).
    with as_login(cur, AUTHORITY):
        cur.execute("select ops.mark_slice_completion(%s,%s,'partner confirms',%s)", (SLICE, receipt(full), uuid.uuid4()))

    # A partner hold also blocks an automated BIND (on a fresh slice).
    with as_login(cur, WRITER, SEAT):
        cur.execute("select * from ops.register_slice_criteria_from_catalog(%s,%s)", (SLICE_OTHER, uuid.uuid4()))
    with as_login(cur, AUTHORITY):
        cur.execute("select ops.set_slice_mark_hold(%s,'hold','blocked','partner decides this one',%s)",
                    (SLICE_OTHER, uuid.uuid4()))
    with as_login(cur, WRITER, SEAT):
        expect_refusal(cur, BIND, bind_args(SLICE_OTHER, "other criterion", "shipped_release", member_id=theirs),
                       "a seat bind over a partner hold", match="slice_mark_held_by_partner")
    with as_login(cur, AUTHORITY):
        cur.execute("select ops.set_slice_mark_hold(%s,'release',null,'go ahead',%s)", (SLICE_OTHER, uuid.uuid4()))
    with as_login(cur, WRITER, SEAT):
        cur.execute(BIND, bind_args(SLICE_OTHER, "other criterion", "shipped_release", member_id=theirs))

    # ------------------------------------------------------------------- PROBES
    problem = probes_a1_a3_a4(cur, rel_ok, mine)
    if problem:
        return problem

    # -------------------------------------------------------------- READ STATE
    with as_login(cur, READER):
        state = cur.execute("select ops.read_slice_done_state(%s)", (SLICE,)).fetchone()[0]
        listed = cur.execute("select release_key from ops.list_shipped_releases(null)").fetchall()
    kinds = {c["criterion"]: c["evidence_kind"] for c in state["criteria"]}
    if kinds != {CRITERIA[0]: "shipped_release", CRITERIA[1]: "live_check", CRITERIA[2]: "live_check"} \
            or not state["held_by_partner"] or state["latest_mark"]["marked_via"] != "authority" \
            or sorted(m["id"] for m in state["release_members"]) != sorted([mine, mine2]):
        return f"read_slice_done_state is wrong: {json.dumps(state)[:600]}"
    by = {c["criterion"]: c for c in state["criteria"]}
    if by[CRITERIA[1]]["live_check_candidate"] != good_job or by[CRITERIA[0]]["live_check_candidate"] != mine \
            or by[CRITERIA[0]]["bound_member_id"] != mine or by[CRITERIA[0]].get("proposal") is not None:
        return f"read_slice_done_state candidates wrong: {json.dumps(state['criteria'])[:600]}"
    keys = {k for (k,) in listed}
    if rel_ok not in keys or f"r-sdm-{token}-cand" in keys or f"r-sdm-{token}-stg" in keys:
        return f"list_shipped_releases returned the wrong releases: {sorted(keys)[-5:]}"
    expect_refusal(cur, "update ops.slice_criterion_binding set reason='x' where slice_id=%s", (SLICE,),
                   "rewriting a binding (owner)", match="append-only")
    return portfolio_section(cur, token, rel_ok, mine)


def portfolio_payload(portfolio_ref: str) -> tuple[list, list, list, dict]:
    """The same synthetic 21-node, four-child shape ops/work-portfolio-local-pg-gate.py uses."""
    nodes, edges = [], []
    for index in range(1, 22):
        nodes.append({
            "node_ref": f"step:sdm-milestone-{index:02d}", "node_kind": "milestone",
            "ordinal": index, "parent_ref": portfolio_ref, "child_ref": PF_CHILDREN[(index - 1) % 4],
            "authority_class": "synthetic_authority", "effect_class": "synthetic_no_effect",
            "data_class": "synthetic_record_layer", "budget_identity": f"synthetic:budget-{index}",
            "budget_ceiling": index * 1000,
            "model_floor": {"provider": "synthetic", "model": "synthetic", "version": "1", "effort": "high"},
            "recovery_ref": f"recovery:synthetic-{index}", "terminal_predicate": "synthetic terminal",
        })
        if index > 1:
            edges.append({"from_node_ref": f"step:sdm-milestone-{index - 1:02d}",
                          "to_node_ref": f"step:sdm-milestone-{index:02d}"})
    children = [{"child_ref": ref, "child_ordinal": position, "child_version": 1,
                 "child_digest": PLACEHOLDER, "accepted_plan_ref": None}
                for position, ref in enumerate(PF_CHILDREN)]
    sources = {key: "sha256:" + str(number) * 64 for number, key in
               enumerate(("constitution", "design", "integration", "requirements"), start=1)}
    return nodes, edges, children, sources


def accept_portfolio(cur: Any, portfolio: str) -> tuple[str, str]:
    """Propose (codex), independently review (dell), accept (joe's authority
    login) one synthetic revision through the real portfolio doors. Returns
    (revision_id, acceptance_receipt_id)."""
    nodes, edges, children, sources = portfolio_payload(portfolio)

    def propose(graph: str, accepted: str) -> str:
        cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
        return cur.execute(
            "select ops.portfolio_propose_revision(%s,1,gen_random_uuid(),%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)",
            (portfolio, json.dumps(sources), graph, accepted, json.dumps(children), json.dumps(nodes),
             json.dumps(edges))).fetchone()[0]

    cur.execute("set constraints all deferred")
    cur.execute("savepoint sdm_pf")
    rev = propose(PLACEHOLDER, PLACEHOLDER)
    graph = cur.execute("select ops.portfolio_graph_digest(%s)", (rev,)).fetchone()[0]
    for position, ref in enumerate(PF_CHILDREN):
        children[position]["child_digest"] = cur.execute(
            "select ops.portfolio_child_digest(%s,%s)", (rev, ref)).fetchone()[0]
    cur.execute("rollback to savepoint sdm_pf")
    rev = propose(graph, PLACEHOLDER)
    accepted = cur.execute("select ops.portfolio_accepted_digest(%s)", (rev,)).fetchone()[0]
    cur.execute("rollback to savepoint sdm_pf")
    rev = propose(graph, accepted)
    cur.execute("set constraints all immediate")
    with as_login(cur, WRITER, "dell"):
        cur.execute("select set_config('carr.verified_human_actor_slug','dell',true)")
        review = cur.execute("select ops.portfolio_review_revision(%s,gen_random_uuid(),%s,'pass','sdm gate review')",
                             (rev, accepted)).fetchone()[0]
        cur.execute("select set_config('carr.verified_human_actor_slug','',true)")
    with as_login(cur, AUTHORITY):
        cur.execute("select ops.portfolio_accept_revision(%s,gen_random_uuid(),%s,%s)", (rev, accepted, review))
    receipt_id = cur.execute("select id::text from ops.portfolio_revision_acceptance_receipt where portfolio_revision_id=%s",
                             (rev,)).fetchone()[0]
    return str(rev), receipt_id


def portfolio_section(cur: Any, token: str, release_key: str, zt01_member: str) -> str | None:
    portfolio = CATALOG_PORTFOLIO
    other = f"SDM-OTHER-{token}"
    counts, negatives, effects = PF_CRITERIA
    if cur.execute("select exists (select 1 from ops.portfolio_revision where portfolio_ref=%s)",
                   (portfolio,)).fetchone()[0]:
        return f"this gate needs a database with no {portfolio} portfolio (it proposes one inside the rollback)"

    # Registry CHECK (partner registration): each kind needs its own source and
    # key; refusal_proof is refused outright (no server gate-result source).
    bad = [
        ({"criterion": counts, "evidence_kind": "accepted_record", "live_check_source": "portfolio_revision_acceptance"},
         "accepted_record with no key", "slice_checkable_done_registry_binding_check"),
        ({"criterion": counts, "evidence_kind": "accepted_record", "live_check_source": "ci_gate", "live_check_key": portfolio},
         "accepted_record on the ci_gate source", "slice_checkable_done_registry_binding_check"),
        ({"criterion": negatives, "evidence_kind": "refusal_proof", "live_check_source": "ci_gate", "live_check_key": "ci",
          "write_required_reason": "writes"}, "a refusal_proof registration", "refusal_proof_has_no_server_gate_source"),
        ({"criterion": effects, "evidence_kind": "live_check", "live_check_source": "portfolio_acceptance_effect_free"},
         "effect_free live_check with no key", "slice_checkable_done_registry_binding_check"),
        ({"criterion": effects, "evidence_kind": "live_check", "live_check_source": "job_receipt", "live_check_key": "x",
          "write_required_reason": "nope"}, "a live_check carrying a write_required_reason",
         "slice_checkable_done_registry_binding_check"),
    ]
    with as_login(cur, AUTHORITY):
        for element, label, match in bad:
            expect_refusal(cur, "select * from ops.register_slice_checkable_done(%s,%s,%s)",
                           (SLICE_PF, Jsonb([element]), uuid.uuid4()), f"registry: {label}", match=match)
        # Binding CHECK, on the partner door (the seat's allowlist refuses first).
        for args, label, match in (
            (bind_args(SLICE_OTHER, "other criterion", "accepted_record", "portfolio_revision_acceptance"),
             "accepted_record with no key", "slice_criterion_binding"),
            (bind_args(SLICE_OTHER, "other criterion", "accepted_record", "portfolio_revision_acceptance", portfolio, "r"),
             "accepted_record with a write reason", "slice_criterion_binding"),
            (bind_args(SLICE_OTHER, "other criterion", "live_check"), "a live_check with no source",
             "slice_criterion_binding"),
            (bind_args(SLICE_OTHER, "other criterion", "refusal_proof", "ci_gate", "ci", "writes"),
             "a partner refusal_proof", "refusal_proof_has_no_server_gate_source"),
            (bind_args(SLICE_OTHER, "other criterion", "shipped_release"), "a partner shipped_release naming no member",
             "shipped_release_binding_requires_this_slice_member"),
        ):
            expect_refusal(cur, REBIND, args, f"binding: {label}", match=match)

    rev, accepted = accept_portfolio(cur, portfolio)
    other_rev, other_accepted = accept_portfolio(cur, other)

    # The seat registers the slice from the catalog and binds only what the
    # wording allows, only on the catalog portfolio.
    with as_login(cur, WRITER, SEAT):
        cur.execute("select * from ops.register_slice_criteria_from_catalog(%s,%s)", (SLICE_PF, uuid.uuid4()))
        pf_member = cur.execute("select id::text from ops.record_release_slice_members(%s,%s) where slice_id=%s",
                                (release_key, Jsonb([member("pf", SLICE_PF)]), SLICE_PF)).fetchone()[0]
        for args, label, match in (
            (bind_args(SLICE_PF, counts, "accepted_record", "portfolio_revision_acceptance", other),
             "accepted_record on another portfolio", "automation_portfolio_key_not_catalog_portfolio"),
            (bind_args(SLICE_PF, effects, "live_check", "portfolio_acceptance_effect_free", other),
             "effect-free on another portfolio", "automation_portfolio_key_not_catalog_portfolio"),
            (bind_args(SLICE_PF, counts, "shipped_release", member_id=pf_member),
             "shipped_release for an acyclicity criterion", "automation_binding_kind_not_allowed"),
            (bind_args(SLICE_PF, negatives, "shipped_release", member_id=pf_member),
             "shipped_release for a negatives criterion", "automation_binding_kind_not_allowed"),
            (bind_args(SLICE_PF, negatives, "refusal_proof", "ci_gate", "ci:merge-gate", "writes"),
             "refusal_proof for a negatives criterion", "refusal_proof_has_no_server_gate_source"),
        ):
            expect_refusal(cur, BIND, args, f"seat bind: {label}", match=match)
        cur.execute(BIND, bind_args(SLICE_PF, counts, "accepted_record", "portfolio_revision_acceptance", portfolio))
        cur.execute(BIND, bind_args(SLICE_PF, effects, "live_check", "portfolio_acceptance_effect_free", portfolio))

    def pf_eval(refs: dict[str, str | None]) -> list[bool]:
        got = cur.execute("select ops.slice_completion_evaluate(%s,%s)", (SLICE_PF, Jsonb(
            [{"criterion": c, "evidence_ref": refs.get(c)} for c in PF_CRITERIA]))).fetchone()[0]
        by = {el["criterion"]: el["pass"] for el in got}
        return [by[c] for c in PF_CRITERIA]

    all_refs = {counts: accepted, negatives: pf_member, effects: accepted}
    cases = [
        (all_refs, [True, False, True], "the current intact acceptance; the negatives stay unbound"),
        ({counts: other_accepted, effects: other_accepted}, [False, False, False],
         "another portfolio's acceptance against this key"),
        ({counts: pf_member, negatives: accepted, effects: pf_member}, [False, False, False],
         "refs swapped across kinds"),
        ({counts: rev, effects: rev}, [False, False, False], "the revision id instead of the acceptance receipt"),
    ]
    for refs, expected, label in cases:
        got = pf_eval(refs)
        if got != expected:
            return f"portfolio resolver: {label}: expected {expected}, got {got}"
    with as_login(cur, READER):
        live = {c["criterion"]: (c["live_check_candidate"], c["candidate_passes"])
                for c in cur.execute("select ops.read_slice_done_state(%s)", (SLICE_PF,)).fetchone()[0]["criteria"]}
    if live != {counts: (accepted, True), negatives: (None, False), effects: (accepted, True)}:
        return f"read_slice_done_state candidates/candidate_passes wrong before any effect: {live}"
    with as_login(cur, WRITER, SEAT):
        expect_refusal(cur, "select ops.propose_slice_completion(%s,%s,'r',%s)",
                       (SLICE_PF, Jsonb([{"criterion": c, "evidence_ref": all_refs[c]} for c in PF_CRITERIA]),
                        uuid.uuid4()), "a proposal with the negatives unbound", match="every_criterion_proven")

    # Only a partner decides the negatives (here: the shipped PR whose CI gate
    # exercised them).
    with as_login(cur, AUTHORITY):
        cur.execute(REBIND, bind_args(SLICE_PF, negatives, "shipped_release", member_id=pf_member,
                                      reason="partner: the PR's gate exercised the refusals"))
    if pf_eval(all_refs) != [True, True, True]:
        return f"the partner-decided negatives did not resolve: {pf_eval(all_refs)}"
    if pf_eval({negatives: zt01_member}) != [False, False, False]:
        return "another slice's shipped member proved the negatives"

    # An effect created inside an acceptance window fails the effect-free check.
    cur.execute("savepoint sdm_effect")
    cur.execute("set local session_replication_role=replica")
    cur.execute("""insert into ops.job (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds)
                   values ('sdm-gate-effect',1,%s,now(),1,30)""", (uuid.uuid4().hex,))
    cur.execute("set local session_replication_role=origin")
    if pf_eval(all_refs) != [True, True, False]:
        return "a job created in the acceptance window did not fail portfolio_acceptance_effect_free"
    with as_login(cur, READER):
        live = {c["criterion"]: c["candidate_passes"]
                for c in cur.execute("select ops.read_slice_done_state(%s)", (SLICE_PF,)).fetchone()[0]["criteria"]}
    if live[effects] is not False:
        return f"read_slice_done_state kept passing an effect-bearing acceptance: {live}"
    cur.execute("rollback to savepoint sdm_effect")

    # Tampering with an accepted row fails accepted_record (and effect-free,
    # which needs the same current, intact acceptance).
    cur.execute("savepoint sdm_tamper")
    cur.execute("set local session_replication_role=replica")
    cur.execute("update ops.portfolio_node set budget_ceiling = budget_ceiling + 1 where portfolio_revision_id=%s and ordinal=1",
                (rev,))
    cur.execute("set local session_replication_role=origin")
    if pf_eval(all_refs) != [False, True, False]:
        return "a tampered accepted revision still passed accepted_record"
    cur.execute("rollback to savepoint sdm_tamper")

    pf_receipt = Jsonb([{"criterion": c, "evidence_ref": all_refs[c]} for c in PF_CRITERIA])
    with as_login(cur, WRITER, SEAT):
        cur.execute("select ops.propose_slice_completion(%s,%s,'all proven',%s)", (SLICE_PF, pf_receipt, uuid.uuid4()))
    # An effect created after the proposal, before confirmation: not_proven.
    cur.execute("savepoint sdm_late_effect")
    cur.execute("set local session_replication_role=replica")
    cur.execute("""insert into ops.job (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds)
                   values ('sdm-gate-effect',1,%s,now(),1,30)""", (uuid.uuid4().hex,))
    cur.execute("set local session_replication_role=origin")
    with as_login(cur, AUTHORITY):
        late = cur.execute("select outcome from ops.confirm_slice_completions(array[%s],'confirm',%s)",
                           (SLICE_PF, uuid.uuid4())).fetchone()[0]
    cur.execute("rollback to savepoint sdm_late_effect")
    if late != "not_proven":
        return f"an effect created after the proposal did not stop its confirmation: {late}"
    with as_login(cur, AUTHORITY):
        done = cur.execute("select outcome from ops.confirm_slice_completions(array[%s],'confirm',%s)",
                           (SLICE_PF, uuid.uuid4())).fetchone()[0]
    if done != "confirmed" or cur.execute(
            "select status, marked_via from ops.slice_completion_mark where slice_id=%s order by mark_seq desc limit 1",
            (SLICE_PF,)).fetchone() != ("complete", "authority"):
        return f"a partner could not confirm a slice proven by the new kinds: {done}"
    return probe_a2(cur, token, accepted)


def set_catalog_as_seat(cur: Any, slices: list[dict]) -> None:
    """The reviewer's A2 setup: the WRITER login with the seat actor rewrites the
    catalog section body (the rows write-doctrine-section writes)."""
    body = json.dumps({"slices": slices})
    with as_login(cur, WRITER, SEAT):
        sec = cur.execute("""select s.id, s.current_version from doctrine_section s join doctrine_document d on d.id=s.document_id
                              where d.slug=%s and s.section_key=%s""", (CATALOG_DOC, CATALOG_SECTION)).fetchone()
        actor = cur.execute("select id from actor where slug='sdm-gate-fixture'").fetchone()[0]
        version = int(sec[1]) + 1
        rev = cur.execute(
            """insert into doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash,commit_message)
               values (%s,%s,%s,%s,%s,%s,'seat rewrite') returning id""",
            (sec[0], version, actor, Jsonb({"text": body}), body, hashlib.sha256(body.encode()).hexdigest())).fetchone()[0]
        cur.execute("update doctrine_section set current_revision_id=%s, current_version=%s where id=%s", (rev, version, sec[0]))


def no_complete_mark(cur: Any, slice_id: str) -> bool:
    return not cur.execute("select exists (select 1 from ops.slice_completion_mark where slice_id=%s and status='complete')",
                           (slice_id,)).fetchone()[0]


A3_WORDING = [
    # The reviewer's A3 wordings. Refusal and human-observation wording allows
    # nothing; D04's still maps to the effect-free check, which is harmless now
    # that a seat binding can only ever be PROPOSED complete.
    ("restore into production is refused", []),
    ("Joe observes zero side effects for a week", []),
    ("inactive registration has zero analytics/network effect", ["live_check:portfolio_acceptance_effect_free"]),
]


def probes_a1_a3_a4(cur: Any, release_key: str, confirmed_member: str) -> str | None:
    # A1: a partner registration of shipped_release names no member, so it is
    # refused; a member the seat forged can only ever back a PROPOSAL.
    probe = "V5-ZT04"
    install_catalog(cur, extra=[{"proposed_id": probe, "checkable_done": ["the change ships"]}])
    with as_login(cur, AUTHORITY):
        expect_refusal(cur, "select * from ops.register_slice_checkable_done(%s,%s,%s)",
                       (probe, Jsonb([{"criterion": "the change ships", "evidence_kind": "shipped_release"}]), uuid.uuid4()),
                       "A1 a partner shipped_release registration naming no member",
                       match="shipped_release_registration_needs_a_named_member")
    with as_login(cur, WRITER, SEAT):
        cur.execute("select * from ops.register_slice_criteria_from_catalog(%s,%s)", (probe, uuid.uuid4()))
        forged = cur.execute("select id::text from ops.record_release_slice_members(%s,%s) where slice_id=%s",
                             (release_key, Jsonb([{"slice_id": probe, "commit_sha": "e" * 40, "subject": f"{probe}: forged",
                                                   "attribution": "explicit"}]), probe)).fetchone()[0]
        cur.execute(BIND, bind_args(probe, "the change ships", "shipped_release", member_id=forged))
        expect_refusal(cur, "select ops.propose_slice_completion(%s,%s,'r',%s)",
                       (probe, Jsonb([{"criterion": "the change ships", "evidence_ref": forged}]), uuid.uuid4()),
                       "A1 a proposal on a forged, unconfirmed member", match="every_criterion_proven")
    if not no_complete_mark(cur, probe):
        return "A1: a forged member reached complete"
    # A3: the reviewer's wordings.
    with as_login(cur, READER):
        for wording, kinds_expected in A3_WORDING:
            kinds_got = cur.execute("select ops.slice_criterion_allowed_kinds(%s)", (wording,)).fetchone()[0]
            if kinds_got != kinds_expected:
                return f"A3: {wording!r} allows {kinds_got}, expected {kinds_expected}"
    # A4: the seat re-binds a partner-confirmed member to ANOTHER criterion: a
    # proposal only, so the other criterion stays unproven and nothing can be
    # proposed, let alone completed.
    a4 = "V5-ZT05"
    install_catalog(cur, extra=[{"proposed_id": a4, "checkable_done": ["part one ships", "part two ships"]}])
    with as_login(cur, WRITER, SEAT):
        cur.execute("select * from ops.register_slice_criteria_from_catalog(%s,%s)", (a4, uuid.uuid4()))
        m = cur.execute("select id::text from ops.record_release_slice_members(%s,%s) where slice_id=%s",
                        (release_key, Jsonb([member("a4", a4)]), a4)).fetchone()[0]
        cur.execute(BIND, bind_args(a4, "part one ships", "shipped_release", member_id=m))
    with as_login(cur, AUTHORITY):
        cur.execute(REBIND, bind_args(a4, "part one ships", "shipped_release", member_id=m, reason="partner confirms"))
    with as_login(cur, WRITER, SEAT):
        cur.execute(BIND, bind_args(a4, "part two ships", "shipped_release", member_id=m))
        a4_receipt = Jsonb([{"criterion": c, "evidence_ref": m} for c in ("part one ships", "part two ships")])
        expect_refusal(cur, "select ops.propose_slice_completion(%s,%s,'r',%s)", (a4, a4_receipt, uuid.uuid4()),
                       "A4 a proposal on a member re-bound across criteria", match="every_criterion_proven")
    got = {e["criterion"]: e["pass"] for e in cur.execute(
        "select ops.slice_completion_evaluate(%s,%s)", (a4, a4_receipt)).fetchone()[0]}
    if got != {"part one ships": True, "part two ships": False} or not no_complete_mark(cur, a4):
        return f"A4: a re-bound confirmed member proved another criterion: {got}"
    del confirmed_member
    return None


def probe_a2(cur: Any, token: str, accepted: str) -> str | None:
    """A2: the seat writes the catalog itself, registers, binds kinds automation
    may make effective, and proposes: a proposal, never a complete mark."""
    s2 = "V5-ZT09"
    criteria = ["portfolio acyclic", "acceptance has zero effect"]
    set_catalog_as_seat(cur, [{"proposed_id": s2, "checkable_done": criteria}])
    with as_login(cur, WRITER, SEAT):
        cur.execute("select * from ops.register_slice_criteria_from_catalog(%s,%s)", (s2, uuid.uuid4()))
        cur.execute(BIND, bind_args(s2, criteria[0], "accepted_record", "portfolio_revision_acceptance", CATALOG_PORTFOLIO))
        cur.execute(BIND, bind_args(s2, criteria[1], "live_check", "portfolio_acceptance_effect_free", CATALOG_PORTFOLIO))
        proposal = cur.execute("select id::text from ops.propose_slice_completion(%s,%s,'r',%s)",
                               (s2, Jsonb([{"criterion": c, "evidence_ref": accepted} for c in criteria]),
                                uuid.uuid4())).fetchone()[0]
    with as_login(cur, READER):
        st = cur.execute("select ops.read_slice_done_state(%s)", (s2,)).fetchone()[0]
        marked = cur.execute("select slice_id from ops.read_slice_completion(%s)", (s2,)).fetchone()
    if not no_complete_mark(cur, s2) or st["latest_mark"] is not None or (marked and marked[0]) \
            or not st["awaiting_partner_confirmation"] or st["latest_proposal"]["id"] != proposal:
        return f"A2: a slice the seat wrote, bound and proposed is not proposal-only: {st}"
    del token
    return None


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            problem = run(cur)
            if problem:
                return fail(problem)
    except Exception as exc:  # noqa: BLE001 — a gate reports, it never crashes silently
        return fail(f"{type(exc).__name__}: {exc}")
    print("slice-done-marker-local-pg-gate: PASS — automation proposes and never sets complete (no door, refused body, "
          "table CHECK); partner batch confirmation re-evaluates at confirm time and refuses held, stale and unproven "
          "proposals; reviewer probes A1-A4 are refusals or proposal-only; seat/partner authority, catalog-only "
          "registration, a wording-derived allowlist, forged-subject members refused, refusal_proof refused, "
          "server-resolved evidence, once-only automation binding with partner override, and the partner hold over "
          "marks, binds and proposals, all under production-shaped logins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
