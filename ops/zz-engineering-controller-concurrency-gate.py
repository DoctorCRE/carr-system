#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Committed-fixture disposable proof for the Engineering receipt seam.

This gate deliberately crosses real PostgreSQL connections.  The fixture rows
are committed so peers can see them; the disposable carr_ci database is the
cleanup boundary.  Every behavioural assertion uses the production receipt,
claim, completion/failure, or successor-trigger path rather than a proxy lock.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from threading import Event, Thread
import uuid
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROLE = "carr_jobs"
SUCCESSOR_INSERT_SQL = "insert into ops.engineering_execution_envelope(id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at,supersedes_envelope_id,supersession_reason) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,%s,'concurrency gate successor')"
DAG_ENVELOPE_INSERT_SQL = "insert into ops.engineering_execution_envelope(id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz)"
REAPER_RECEIPT_BARRIER = "engineering-controller-reaper-receipt-barrier"
REAPER_RECEIPT_EXECUTABLE_SQL = f"""
create or replace function ops.engineering_envelope_is_executable(p_envelope_id uuid,p_job_id uuid)
returns boolean language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_sleep_seconds double precision;
begin
  perform pg_advisory_xact_lock(hashtextextended('{REAPER_RECEIPT_BARRIER}',0));
  select greatest(0::numeric,extract(epoch from
           (least(e.expires_at,s.lease_expires_at,j.leased_until)-clock_timestamp())))::double precision+0.5
    into v_sleep_seconds from ops.engineering_execution_envelope e
    join ops.capability_agent_session s on s.id=e.agent_session_id
    join ops.job j on j.id=e.job_id
   where e.id=p_envelope_id and e.job_id=p_job_id;
  perform pg_sleep(coalesce(v_sleep_seconds,0.5));
  return coalesce((ops.engineering_envelope_currentness(p_envelope_id,p_job_id)->>'eligible')::boolean,false);
end;
$$
"""

DELAYED_EXECUTABLE_SQL = """
create or replace function ops.engineering_envelope_is_executable(p_envelope_id uuid,p_job_id uuid)
returns boolean language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
begin
  perform pg_sleep(3);
  return coalesce((ops.engineering_envelope_currentness(p_envelope_id,p_job_id)->>'eligible')::boolean,false);
end;
$$
"""


def assert_gate_sql_arity():
    if SUCCESSOR_INSERT_SQL.count("%s") != 14:
        raise RuntimeError("successor envelope INSERT must have 14 bound values plus its literal reason")
    if DAG_ENVELOPE_INSERT_SQL.count("%s") != 13:
        raise RuntimeError("DAG envelope INSERT must have 13 bound values")


def one(cur, query, params=()):
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError(f"required row missing: {query[:120]}")
    return row


def load_fixture():
    path = ROOT / "ops/engineering-claim-local-pg-gate.py"
    spec = importlib.util.spec_from_file_location("engineering_claim_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the committed Engineering fixture builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fixture


def hard_fence(cur, dsn):
    """The dedicated carr_ci local/hosted disposable fence (copied verbatim)."""
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("engineering controller acceptance requires loopback CARR_LOCAL_PG_DSN or DATABASE_URL")
    database_name, role_name, data_directory, is_superuser = one(
        cur, "select current_database(),current_user,current_setting('data_directory'),(select rolsuper from pg_roles where rolname=current_user)"
    )
    data_path = os.path.realpath(str(data_directory))
    local_disposable = (os.path.isfile(os.path.join(data_path, "PG_VERSION"))
                        and os.path.basename(os.path.dirname(data_path)).startswith("carr-local-pg-ci."))
    hosted_disposable = (os.environ.get("GITHUB_ACTIONS") == "true"
                         and os.environ.get("CI") == "true"
                         and os.environ.get("CARR_CI_PORTABLE_ONLY") == "1"
                         and os.environ.get("GITHUB_REPOSITORY") == "jbookout/carr-system"
                         and bool(os.environ.get("GITHUB_RUN_ID"))
                         and data_path == "/var/lib/postgresql/data")
    if database_name != "carr_ci" or role_name != "carr_ci" or is_superuser is not True or not (local_disposable or hosted_disposable):
        raise RuntimeError("engineering controller acceptance requires a dedicated disposable carr_ci database")


def sha(char):
    return "sha256:" + char * 64


def unique_sha():
    token = uuid.uuid4().hex
    return "sha256:" + token + token


def canonical_json(value):
    """Match ops.guidance_import_canonical_json for receipt digests."""
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, separators=(",", ":")) + ":" + canonical_json(value[key])
            for key in sorted(value)
        ) + "}"
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonical_digest(value):
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def set_jobs(cur):
    cur.execute("set role carr_jobs")
    if one(cur, "select current_user")[0] != RUNTIME_ROLE:
        raise RuntimeError("receipt assertions did not run as carr_jobs")


def reset_role(cur):
    cur.execute("reset role")


def claim_one(conn, expected, worker):
    """Claim exactly `expected` through the production claim.

    The fence must cover every row the production claim can select, in the
    same transaction as the claim: ops.engineering_claim_slice picks from
    state in ('queued','retry_wait') with next_attempt_at<=now().  Gates share
    one disposable cluster, and a row an earlier gate failed into retry_wait
    becomes claimable again 30 seconds later (the constant engineering-slice
    backoff).  Its fixture is scheduled earlier, so it wins the ordering.
    Fencing only the caller's own queued rows let that leftover be selected
    whenever a claim ran more than 30 seconds after the failure (WR-000164:
    the A2 ownership gate's terminal engineering_fail_claim row stole the A3a
    gate's claim on slow hosted runners).
    """
    with conn.cursor() as cur:
        cur.execute("""update ops.job set next_attempt_at=now()+interval '1 day'
                        where definition_key='engineering-slice'
                          and state in ('queued','retry_wait') and id<>%s""", (expected,))
        cur.execute("update ops.job set next_attempt_at=now()-interval '1 second' where id=%s", (expected,))
        set_jobs(cur)
        row = one(cur, "select job_id,lease_token,attempt from ops.engineering_claim_slice(%s,1,960)", (worker,))
        reset_role(cur)
    conn.commit()
    if row[0] != expected:
        raise RuntimeError(f"claim selected {row[0]} instead of committed fixture {expected}")
    return row


def receipt_payload(cur, fixture, claim, outcome):
    envelope_id, plan_digest, slice_ref, envelope_digest = fixture[1], fixture[4], fixture[5], fixture[6]
    row = one(
        cur,
        """select e.envelope,e.envelope#>>'{server_binding,identity,agent_principal_id}',
                         e.envelope#>>'{agent_session,id}',e.envelope#>>'{server_binding,adapter,adapter_id}',
                         sp.plan
                    from ops.engineering_execution_envelope e
                    join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
                   where e.id=%s""",
        (envelope_id,),
    )
    _envelope, actor_ref, session_ref, adapter_ref, plan = row
    slice_plan = next(item for item in plan["slices"] if item["slice_ref"] == slice_ref)
    planned_check = slice_plan["planned_checks"][0]
    evidence = {
        "content_digest": sha("f"),
        "redaction_class": "metadata_only",
        "ref": "evidence:receipt-fixture",
    }
    return {
        "actual_component_refs": list(slice_plan["declared_component_refs"]),
        "actual_resource_refs": list(slice_plan["declared_resource_refs"]),
        "artifact_refs": ["artifact:engineering-fixture"],
        "attribution": {"actor_ref": actor_ref, "adapter_ref": adapter_ref, "session_ref": session_ref},
        "attempt_id": f"attempt:{claim[2]}",
        "checks": [{"check_ref": planned_check["check_ref"], "evidence_refs": [evidence], "state": "passed"}],
        "deviations": [],
        "envelope_digest": envelope_digest,
        "evidence_refs": [evidence],
        "executor_claim": {"claim_state": "executor_claim", "claimed_at": "2026-08-26T00:00:00Z", "claimed_by": "codex"},
        "independent_verification_required": True,
        "outcome": outcome,
        "plan_digest": plan_digest,
        "planned_component_refs": list(slice_plan["declared_component_refs"]),
        "planned_resource_refs": list(slice_plan["declared_resource_refs"]),
        "reset_reconstruction": {"fresh_session": True, "inherited_transcript_used": False, "reconstruction_free": True, "remediation_action": None},
        "schema_version": "engineering-slice-receipt.v1",
        "slice_ref": slice_ref,
        "source_evidence": {
            "branch_ref": "branch:engineering-fixture",
            "evidence_refs": [evidence],
            "source_sha": "0" * 40,
            "worktree_ref": "worktree:engineering-fixture",
        },
    }


def receipt(cur, fixture, claim, outcome):
    envelope_id, codex_id = fixture[1], fixture[3]
    payload = receipt_payload(cur, fixture, claim, outcome)
    cur.execute(
        "select * from ops.engineering_finalize_slice_receipt(%s::uuid,%s::uuid,%s::jsonb,%s::text,%s::uuid)",
        (envelope_id, claim[1], Jsonb(payload), canonical_digest(payload), codex_id),
    )
    return one(cur, "select id from ops.engineering_slice_receipt where envelope_id=%s", (envelope_id,))[0]


AUTO_DIGEST = object()


def refused_receipt(cur, fixture, claim, mutate=None, digest=AUTO_DIGEST):
    envelope_id, codex_id = fixture[1], fixture[3]
    payload = receipt_payload(cur, fixture, claim, "claimed_complete")
    if mutate:
        mutate(payload)
    if digest is AUTO_DIGEST:
        digest = canonical_digest(payload)
    cur.execute("savepoint engineering_receipt_refusal")
    try:
        cur.execute("select * from ops.engineering_finalize_slice_receipt(%s::uuid,%s::uuid,%s::jsonb,%s::text,%s::uuid)",
                    (envelope_id, claim[1], Jsonb(payload), digest, codex_id))
    except psycopg.Error as exc:
        if not str(exc).strip():
            raise RuntimeError("receipt refusal had no deterministic database error")
        cur.execute("rollback to savepoint engineering_receipt_refusal")
        cur.execute("release savepoint engineering_receipt_refusal")
        reset_role(cur)
        if one(cur, "select count(*) from ops.engineering_slice_receipt where envelope_id=%s", (envelope_id,))[0] != 0:
            raise RuntimeError("refused receipt left immutable evidence")
        if one(cur, "select state,leased_until>now() from ops.job where id=%s", (fixture[0],)) != ("running", True):
            raise RuntimeError("refused receipt changed the live job lease")
        if one(cur, "select state from ops.job_attempt where job_id=%s and attempt=%s", (fixture[0], claim[2]))[0] != "running":
            raise RuntimeError("refused receipt changed the live attempt")
        if one(cur, "select state from ops.capability_agent_session where id=%s", (fixture[2],))[0] not in ("claimed", "in_progress"):
            raise RuntimeError("refused receipt changed the bound session")
        if one(cur, "select count(*) from ops.job_receipt where job_id=%s and kind in ('completion','failure')", (fixture[0],))[0] != 0:
            raise RuntimeError("refused receipt left a terminal job receipt")
        return
    cur.execute("rollback to savepoint engineering_receipt_refusal")
    cur.execute("release savepoint engineering_receipt_refusal")
    raise RuntimeError("malformed engineering receipt was accepted")


def reviewer_fact_payload(slice_ref, attempt_id="attempt:1", *, evidence=True, reviewed_deviation_refs=None, session_ref=None, state="passed"):
    review_evidence = [{
        "content_digest": sha("c"),
        "redaction_class": "metadata_only",
        "ref": "evidence:reviewer-fixture",
    }] if evidence else []
    return {
        "attempt_id": attempt_id,
        "evidence_refs": review_evidence,
        "is_independent": True,
        "resolved_deviation_refs": [],
        "reviewed_deviation_refs": list(reviewed_deviation_refs or ()),
        "reviewer_ref": "reviewer:joe",
        "session_ref": session_ref or f"session:reviewer:{uuid.uuid4()}",
        "slice_ref": slice_ref,
        "state": state,
    }


def refuse_reviewer_fact(cur, *, receipt_id, work_request_id, slice_ref, reviewer_actor_id, reviewer_session_ref, fact, label):
    idempotency_key = uuid.uuid4()
    cur.execute("savepoint engineering_reviewer_refusal")
    try:
        cur.execute(
            """insert into ops.engineering_reviewer_fact
                 (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
               values (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (receipt_id, work_request_id, slice_ref, reviewer_actor_id, reviewer_session_ref, fact["state"], Jsonb(fact), idempotency_key),
        )
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint engineering_reviewer_refusal")
        cur.execute("release savepoint engineering_reviewer_refusal")
        if one(cur, "select count(*) from ops.engineering_reviewer_fact where idempotency_key=%s", (idempotency_key,))[0] != 0:
            raise RuntimeError(f"{label} left an immutable reviewer row") from exc
        return
    cur.execute("rollback to savepoint engineering_reviewer_refusal")
    cur.execute("release savepoint engineering_reviewer_refusal")
    raise RuntimeError(f"{label} reviewer fact was accepted")


def assert_reviewer_fact_guards(cur, complete_fixture, failed_fixture, complete_receipt_id, failed_receipt_id):
    """Direct carr_writer rows must be canonical, independently stamped, and complete-bound."""
    complete_work_request_id = one(cur, "select work_request_id from ops.engineering_execution_envelope where id=%s", (complete_fixture[1],))[0]
    failed_work_request_id = one(cur, "select work_request_id from ops.engineering_execution_envelope where id=%s", (failed_fixture[1],))[0]
    complete_slice = complete_fixture[5]
    failed_slice = failed_fixture[5]
    codex_id = complete_fixture[3]
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    inactive_id = one(cur, "insert into actor(slug,kind,display_name,active) values(%s,'human','Engineering inactive reviewer fixture',false) returning id", (f"engineering-inactive-reviewer-{uuid.uuid4().hex}",))[0]
    executor_session_ref = one(cur, "select envelope#>>'{agent_session,id}' from ops.engineering_execution_envelope where id=%s", (complete_fixture[1],))[0]
    valid_fact = reviewer_fact_payload(complete_slice)
    cur.execute("set local role carr_writer")
    prestamped_key = uuid.uuid4()
    cur.execute("savepoint reviewer_prestamped_contract")
    try:
        cur.execute("""insert into ops.engineering_reviewer_fact
                     (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key,contract_version)
                   values (%s,%s,%s,%s,%s,'passed',%s,%s,'engineering-review.v1')""",
                    (complete_receipt_id, complete_work_request_id, complete_slice, joe_id, valid_fact["session_ref"], Jsonb(valid_fact), prestamped_key))
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint reviewer_prestamped_contract")
        cur.execute("release savepoint reviewer_prestamped_contract")
        if "caller-controlled" not in str(exc):
            raise RuntimeError(f"pre-stamped reviewer contract returned the wrong refusal: {exc}") from exc
    else:
        cur.execute("rollback to savepoint reviewer_prestamped_contract")
        cur.execute("release savepoint reviewer_prestamped_contract")
        raise RuntimeError("caller-supplied reviewer contract_version was accepted")
    cases = [
        ("reviewer receipt_id mismatch", failed_receipt_id, complete_work_request_id, complete_slice, joe_id, valid_fact),
        ("reviewer work_request mismatch", complete_receipt_id, failed_work_request_id, complete_slice, joe_id, valid_fact),
        ("reviewer slice mismatch", complete_receipt_id, complete_work_request_id, "slice:wrong", joe_id, valid_fact),
        ("reviewer attempt mismatch", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, reviewer_fact_payload(complete_slice, "attempt:2")),
        ("reviewer self-review", complete_receipt_id, complete_work_request_id, complete_slice, codex_id, valid_fact),
        ("reviewer inactive actor", complete_receipt_id, complete_work_request_id, complete_slice, inactive_id, valid_fact),
        ("reviewer executor session reuse", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, reviewer_fact_payload(complete_slice, session_ref=executor_session_ref)),
        ("reviewer empty evidence", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, reviewer_fact_payload(complete_slice, evidence=False)),
        ("reviewer malformed evidence", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, {**valid_fact, "evidence_refs": [{"ref": "evidence:malformed"}]}),
        ("reviewer_ref mismatch", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, {**valid_fact, "reviewer_ref": "reviewer:other"}),
        ("reviewer malformed session", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, reviewer_fact_payload(complete_slice, session_ref="not-a-session")),
        ("reviewer duplicate reviewed deviation refs", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, {**valid_fact, "reviewed_deviation_refs": ["deviation:duplicate", "deviation:duplicate"]}),
        ("reviewer duplicate resolved deviation refs", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, {**valid_fact, "resolved_deviation_refs": ["deviation:duplicate", "deviation:duplicate"]}),
        ("reviewer unresolved deviation", complete_receipt_id, complete_work_request_id, complete_slice, joe_id, reviewer_fact_payload(complete_slice, reviewed_deviation_refs=["deviation:unresolved"])),
        ("reviewer passed noncomplete", failed_receipt_id, failed_work_request_id, failed_slice, joe_id, reviewer_fact_payload(failed_slice)),
    ]
    for label, receipt_id, work_request_id, slice_ref, reviewer_actor_id, fact in cases:
        refuse_reviewer_fact(cur, receipt_id=receipt_id, work_request_id=work_request_id, slice_ref=slice_ref, reviewer_actor_id=reviewer_actor_id, reviewer_session_ref=fact["session_ref"], fact=fact, label=label)
    stamped_fact = reviewer_fact_payload(complete_slice)
    stamped = one(cur, """insert into ops.engineering_reviewer_fact
                         (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
                       values (%s,%s,%s,%s,%s,'passed',%s,%s) returning contract_version""",
                  (complete_receipt_id, complete_work_request_id, complete_slice, joe_id, stamped_fact["session_ref"], Jsonb(stamped_fact), uuid.uuid4()))[0]
    if stamped != "engineering-review.v1":
        raise RuntimeError("valid reviewer fact was not stamped by the database")
    cur.execute("reset role")


def seed_legacy_failed_predecessor_review(cur, fixture_row, receipt_id):
    """Seed only the historical collision shape, then restore the guard."""
    work_request_id = one(cur, "select work_request_id from ops.engineering_execution_envelope where id=%s", (fixture_row[1],))[0]
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    fact = reviewer_fact_payload(fixture_row[5])
    cur.execute("alter table ops.engineering_reviewer_fact disable trigger engineering_reviewer_fact_contract_guard")
    try:
        cur.execute(
            """insert into ops.engineering_reviewer_fact
                 (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
               values (%s,%s,%s,%s,%s,'passed',%s,%s)""",
            (receipt_id, work_request_id, fixture_row[5], joe_id, fact["session_ref"], Jsonb(fact), uuid.uuid4()),
        )
    finally:
        cur.execute("alter table ops.engineering_reviewer_fact enable trigger engineering_reviewer_fact_contract_guard")
    if one(cur, "select tgenabled from pg_trigger where tgrelid='ops.engineering_reviewer_fact'::regclass and tgname='engineering_reviewer_fact_contract_guard'")[0] != "O":
        raise RuntimeError("reviewer contract guard was not restored after legacy collision fixture")


def assert_weak_receipt_review_refused(cur, fixture_row, claim, mutate, label):
    """Insert a canonical-digest legacy receipt with weak attribution, then refuse its pass."""
    payload = receipt_payload(cur, fixture_row, claim, "claimed_complete")
    mutate(payload)
    receipt_digest = canonical_digest(payload)
    work_request_id = one(cur, "select work_request_id from ops.engineering_execution_envelope where id=%s", (fixture_row[1],))[0]
    attempt_id = one(cur, "select id from ops.job_attempt where job_id=%s and attempt=%s", (fixture_row[0], claim[2]))[0]
    receipt_id = one(
        cur,
        """insert into ops.engineering_slice_receipt
             (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,executor_actor_id,receipt_digest,outcome,receipt)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (attempt_id, fixture_row[1], work_request_id, fixture_row[5], f"attempt:{claim[2]}", fixture_row[3], receipt_digest, "claimed_complete", Jsonb(payload)),
    )[0]
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    fact = reviewer_fact_payload(fixture_row[5])
    cur.execute("set local role carr_writer")
    refuse_reviewer_fact(cur, receipt_id=receipt_id, work_request_id=work_request_id, slice_ref=fixture_row[5],
                         reviewer_actor_id=joe_id, reviewer_session_ref=fact["session_ref"], fact=fact, label=label)
    cur.execute("reset role")


def seed_malformed_dependency_refusal(cur, fixture_row, claim, dependent_slice_ref, receipt_mutate, envelope_label,
                                      reviewed_deviation_refs=None):
    """Seed pre-0335 malformed A evidence, then prove five-arg B enqueue stays empty."""
    payload = receipt_payload(cur, fixture_row, claim, "claimed_complete")
    receipt_mutate(payload)
    receipt_digest = canonical_digest(payload)
    work_request_id = one(cur, "select work_request_id from ops.engineering_execution_envelope where id=%s", (fixture_row[1],))[0]
    attempt_id = one(cur, "select id from ops.job_attempt where job_id=%s and attempt=%s", (fixture_row[0], claim[2]))[0]
    receipt_id = one(
        cur,
        """insert into ops.engineering_slice_receipt
             (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,executor_actor_id,receipt_digest,outcome,receipt)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (attempt_id, fixture_row[1], work_request_id, fixture_row[5], f"attempt:{claim[2]}", fixture_row[3], receipt_digest, "claimed_complete", Jsonb(payload)),
    )[0]
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    # Keep the reviewer row structurally canonical so the receipt/envelope
    # mutation below is the sole reason dependency admission must refuse it.
    malformed_fact = reviewer_fact_payload(fixture_row[5])
    if reviewed_deviation_refs is not None:
        malformed_fact["reviewed_deviation_refs"] = list(reviewed_deviation_refs)
        malformed_fact["resolved_deviation_refs"] = list(reviewed_deviation_refs)
    cur.execute("alter table ops.engineering_reviewer_fact disable trigger engineering_reviewer_fact_contract_guard")
    try:
        cur.execute(
            """insert into ops.engineering_reviewer_fact
                 (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
               values (%s,%s,%s,%s,%s,'passed',%s,%s)""",
            (receipt_id, work_request_id, fixture_row[5], joe_id, malformed_fact["session_ref"], Jsonb(malformed_fact), uuid.uuid4()),
        )
    finally:
        cur.execute("alter table ops.engineering_reviewer_fact enable trigger engineering_reviewer_fact_contract_guard")
    if one(cur, "select tgenabled from pg_trigger where tgrelid='ops.engineering_reviewer_fact'::regclass and tgname='engineering_reviewer_fact_contract_guard'")[0] != "O":
        raise RuntimeError(f"reviewer guard was not restored after {envelope_label} fixture")
    work_ref = one(cur, "select ref from ops.work_request where id=%s", (work_request_id,))[0]
    idempotency_key = f"malformed-dependency:{uuid.uuid4()}"
    cur.execute("set local role carr_writer")
    cur.execute("savepoint malformed_dependency_refusal")
    try:
        cur.execute("select * from ops.engineering_enqueue_slice_job(%s,%s,%s,%s,%s)",
                    (work_ref, dependent_slice_ref, fixture_row[4], str(idempotency_key), 1))
    except psycopg.Error:
        cur.execute("rollback to savepoint malformed_dependency_refusal")
        cur.execute("release savepoint malformed_dependency_refusal")
        if one(cur, "select count(*) from ops.job where idempotency_key=%s", (idempotency_key,))[0] != 0:
            raise RuntimeError(f"{envelope_label} malformed dependency left a B job")
    else:
        cur.execute("rollback to savepoint malformed_dependency_refusal")
        cur.execute("release savepoint malformed_dependency_refusal")
        raise RuntimeError(f"{envelope_label} malformed dependency admitted B")
    cur.execute("reset role")


def assert_static_contract():
    migration = (ROOT / "migrations/0335_engineering_controller_currentness.sql").read_text()
    engineering_migrations = "\n".join(
        path.read_text() for path in (ROOT / "migrations").glob("*_engineering*.sql")
    )
    runtime = (ROOT / "mcp-server/src/engineering-runtime.js").read_text()
    raw_body = migration[migration.index("create or replace function ops.engineering_record_slice_receipt"):
                         migration.index("create or replace function ops.engineering_finalize_slice_receipt")]
    raw_positions = [
        raw_body.index("from ops.capability_agent_session where id=e.agent_session_id for update"),
        raw_body.index("from public.actor actor"),
        raw_body.index("pg_advisory_xact_lock"),
        raw_body.index("from ops.engineering_execution_envelope where id=p_envelope_id for key share"),
        raw_body.index("from ops.engineering_slice_plan where id=e.slice_plan_id for key share"),
        raw_body.index("from ops.work_request where id=e.work_request_id for share"),
        raw_body.index("select * into j from ops.job"),
        raw_body.index("select * into a from ops.job_attempt"),
        raw_body.index("v_checked_at := clock_timestamp()"),
        raw_body.index("v_append_at := clock_timestamp()"),
        raw_body.index("insert into ops.engineering_slice_receipt"),
    ]
    if raw_positions != sorted(raw_positions):
        raise RuntimeError("raw receipt validator lost session -> actor -> lineage -> envelope/plan -> Work Request -> job -> attempt -> clock -> append ordering")
    if raw_body.count("clock_timestamp()") < 2:
        raise RuntimeError("receipt seam must sample clock_timestamp after locks and again before append")
    if raw_body.index("v_append_at := clock_timestamp()") < raw_body.index("engineering receipt typed contract is invalid"):
        raise RuntimeError("receipt seam samples append currentness before typed validation")
    finalizer = migration[migration.index("create or replace function ops.engineering_finalize_slice_receipt"):
                          migration.index("create or replace function ops.engineering_fail_claim")]
    if "ops.complete_job(" in finalizer or "ops.fail_job(" in finalizer:
        raise RuntimeError("atomic Engineering finalizer calls a newly fenced generic terminal door")
    success = finalizer[finalizer.index("if row.outcome='claimed_complete' then"):finalizer.index("else")]
    success_positions = [
        success.index("update ops.job_attempt"),
        success.index("update ops.job set state='succeeded'"),
        success.index("insert into ops.job_receipt"),
        success.index("update ops.capability_agent_session"),
    ]
    if success_positions != sorted(success_positions) or "set state='cancelled'" not in success:
        raise RuntimeError("claimed-complete finalization lost attempt -> job -> receipt -> exact-session retirement ordering")
    noncomplete = finalizer[finalizer.index("else"):finalizer.rindex("end if;")]
    noncomplete_positions = [
        noncomplete.index("update ops.job_attempt"),
        noncomplete.index("update ops.job set state=terminal_state"),
        noncomplete.index("insert into ops.job_receipt"),
    ]
    if noncomplete_positions != sorted(noncomplete_positions) or "update ops.capability_agent_session" in noncomplete:
        raise RuntimeError("non-complete receipt path changed ordering or retired the reusable session")
    if not re.search(r"p_generation\s+is\s+null", migration, re.I):
        raise RuntimeError("enqueue seam does not reject a null generation")
    if not re.search(r"jsonb_typeof\(p_receipt->'deviations'\)\s+is\s+distinct\s+from\s+'array'", migration, re.I):
        raise RuntimeError("receipt seam does not require deviations to be an array")
    if not re.search(r"group\s+by\s+deviation->>'deviation_ref'\s+having\s+count\(\*\)>1", migration, re.I):
        raise RuntimeError("receipt seam does not reject duplicate deviation identifiers")
    if not re.search(r"envelope->>'envelope_id'\s*=\s*'env:'", migration, re.I) or not re.search(r"envelope->'request'->>'job_ref'\s*=\s*'job:'", migration, re.I):
        raise RuntimeError("currentness seam does not pin envelope id and job ref")
    if "update ops.capability_agent_session set state='cancelled'" not in runtime:
        raise RuntimeError("admission no longer has the exact session cancellation path")
    submit = runtime[runtime.index("export async function submitEngineeringReceipt"):runtime.index("function controllerActor")]
    if "ops.complete_job(" in submit or "ops.fail_job(" in submit:
        raise RuntimeError("runtime still owns post-receipt job finalization")
    if not re.search(r"(?:v\.receipt_id\s*=\s*r\.id|review\.receipt_id\s*=\s*receipt\.id)", engineering_migrations, re.I):
        raise RuntimeError("dependent admission does not bind reviewer receipt_id to the exact receipt row")
    if not re.search(r"successor\.supersedes_envelope_id\s*=\s*envelope\.id[\s\S]*successor\.supersedes_envelope_id\s*=\s*leaf\.id", engineering_migrations, re.I):
        raise RuntimeError("database dependency admission does not require one unsuperseded envelope leaf")
    if not re.search(r"create\s+(?:or\s+replace\s+)?function\s+ops\.[A-Za-z0-9_]*(?:review|reviewer|fact)[A-Za-z0-9_]*|create\s+trigger\s+[A-Za-z0-9_]*(?:review|reviewer|fact)[A-Za-z0-9_]*", engineering_migrations, re.I):
        raise RuntimeError("reviewer facts lack a database trigger/guard")
    reviewer_guard = migration[migration.index("create or replace function ops.guard_engineering_reviewer_fact_insert"):migration.index("drop trigger if exists engineering_reviewer_fact_contract_guard")]
    if ("actor.id=r.executor_actor_id and actor.active and actor.kind='automation' and actor.slug='codex'" not in reviewer_guard
            or "actor.id=new.reviewer_actor_id and actor.active" not in reviewer_guard
            or "order by actor.id for share" not in reviewer_guard):
        raise RuntimeError("reviewer guard lost deterministic active executor-and-Joe actor locking")
    if "latestReceiptForSlice" not in runtime or "leaves.length !== 1" not in runtime or "successor.supersedes_envelope_id === envelope.id" not in runtime:
        raise RuntimeError("runtime projection/admission does not require one unsuperseded envelope leaf")
    if "row.receipt_id === receiptRow.id" not in runtime:
        raise RuntimeError("runtime reviewer admission does not pin the exact receipt identifier")


    admission = runtime[
        runtime.index("export async function admitEngineeringSlice"):
        runtime.index("export async function claimEngineeringSlice")
    ]
    admission_positions = [
        admission.index("where id=$1::uuid for update"),
        admission.index("where id=$1::uuid and slug='codex' and active and kind='automation' for share"),
        admission.index("engineering-slice:"),
        admission.index("engineering-envelope:"),
    ]
    if admission_positions != sorted(admission_positions):
        raise RuntimeError("runtime admission lost session -> exact actor -> slice -> lineage ordering")
    if admission.count("engineering_passport_facts") < 3 or "engineering_admission_serialization_restart" not in admission:
        raise RuntimeError("runtime admission no longer revalidates source after serialization")


def expect_lock_timeout(conn, query, params, label):
    with conn.cursor() as cur:
        cur.execute("set local lock_timeout='300ms'")
        try:
            cur.execute(query, params)
        except psycopg.errors.LockNotAvailable:
            conn.rollback()
            return
        except psycopg.Error as exc:
            conn.rollback()
            raise RuntimeError(f"{label} returned SQLSTATE {exc.sqlstate}, expected lock_timeout") from exc
    conn.rollback()
    raise RuntimeError(f"{label} did not serialize")


def lineage(conn, envelope_id):
    with conn.cursor() as cur:
        return one(cur, "select slice_plan_id,slice_ref,agent_session_id,work_request_id from ops.engineering_execution_envelope where id=%s", (envelope_id,))


def lock_key(plan_id, slice_ref):
    return f"engineering-envelope:{plan_id}:{slice_ref}"


def admission_lock_sql():
    return "select pg_advisory_xact_lock(hashtextextended(%s,0))"


def insert_successor(cur, prior_fixture, *, support_fixture=None, cancel_prior=False):
    """Prepare legal session state, then execute the real envelope INSERT."""
    prior_id = prior_fixture[1]
    row = one(cur, "select e.work_request_id,e.accepted_plan_id,e.slice_plan_id,e.slice_ref,e.state_version,e.canonical_record_digest,e.envelope,j.payload,s.executor_actor_id,s.created_by_actor_id from ops.engineering_execution_envelope e join ops.job j on j.id=e.job_id join ops.capability_agent_session s on s.id=e.agent_session_id where e.id=%s", (prior_id,))
    token = uuid.uuid4().hex
    payload = dict(row[7])
    payload["generation"] = int(payload.get("generation", 1)) + 1
    successor_job = one(cur, "insert into ops.job(definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode,payload) values('engineering-slice',1,%s,now()-interval '1 second',2,300,'shadow',%s) returning id", (f"engineering-controller-successor:{token}", Jsonb(payload)))[0]
    if cancel_prior:
        # Admission and the successor trigger share the global
        # session -> exact actor -> lineage order.
        prior_session = one(cur, "select s.id from ops.capability_agent_session s join ops.engineering_execution_envelope e on e.agent_session_id=s.id where e.id=%s for update of s", (prior_id,))[0]
        one(cur, "select id from public.actor where id=%s and active and kind='automation' and slug='codex' for share", (row[8],))
        cur.execute(admission_lock_sql(), (lock_key(row[2], row[3]),))
        cur.execute("update ops.capability_agent_session set state='cancelled',cancelled_at=now(),version=version+1 where id=%s and work_request_id=%s and state not in ('completed','cancelled')", (prior_session, row[0]))
        successor_session = one(cur, "insert into ops.capability_agent_session(work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,worktree_ref,scope_ref,lease_expires_at) values(%s,%s,%s,%s,'engineering:server-admission','slice:'||%s,date_trunc('second',now())+interval '29 minutes') returning id", (row[0], row[8], row[9], "0" * 40, row[3]))[0]
    else:
        if support_fixture is None:
            raise RuntimeError("forward successor proof requires a distinct committed support session")
        successor_session = support_fixture[2]
    envelope = dict(row[6])
    successor_envelope_id = uuid.uuid4()
    envelope["envelope_id"] = f"env:{successor_envelope_id}"
    envelope["issued_at"] = one(cur, "select to_char(date_trunc('second',now()) at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')")[0]
    support_lease = one(cur, "select to_char(lease_expires_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from ops.capability_agent_session where id=%s", (successor_session,))[0]
    envelope["expires_at"] = support_lease
    envelope["agent_session"] = {**envelope["agent_session"], "id": f"session:{successor_session}", "lease_expires_at": support_lease}
    envelope["request"] = {**envelope["request"], "job_ref": f"job:{successor_job}"}
    cur.execute("set local role carr_writer")
    # SET LOCAL intentionally remains in force until the caller rolls back;
    # after lock_timeout the transaction is failed and RESET ROLE would mask
    # the expected 55P03 with 25P02.
    successor_digest = unique_sha()
    cur.execute(SUCCESSOR_INSERT_SQL, (successor_envelope_id, successor_job, row[0], row[1], row[2], row[3], successor_session, row[4], row[5], successor_digest, Jsonb(envelope), envelope["issued_at"], envelope["expires_at"], prior_id))
    return (successor_job, successor_envelope_id, successor_session, row[8], prior_fixture[4], row[3], successor_digest)


def manual_running_claim(cur, fixture_row):
    token = uuid.uuid4()
    cur.execute("update ops.job set state='running',attempt=1,lease_owner='engineering-controller-manual',lease_token=%s,leased_until=now()+interval '5 minutes',started_at=now() where id=%s", (token, fixture_row[0]))
    cur.execute("insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state) values(%s,1,'engineering-controller-manual',%s,'running')", (fixture_row[0], token))
    return (fixture_row[0], token, 1)


def manual_near_expiry_claim(cur, fixture_row, *, job_lease_seconds=None):
    """Create a committed running attempt without the dispatch runway gate.

    The receipt seam must re-check currentness after it waits.  A deliberately
    short envelope/session lease therefore cannot be obtained through the
    960-second dispatch claim, so this owner-only fixture preparation creates
    the same running job/attempt rows that a real claim leaves behind.  The
    production receipt call remains carr_jobs and is never bypassed.
    """
    token = uuid.uuid4()
    if job_lease_seconds is None:
        cur.execute(
            """update ops.job j set state='running',attempt=1,
                        lease_owner='engineering-controller-expiry',lease_token=%s,
                        leased_until=(select e.expires_at from ops.engineering_execution_envelope e
                                      where e.id=%s),started_at=clock_timestamp()
                   where j.id=%s""",
            (token, fixture_row[1], fixture_row[0]),
        )
    else:
        cur.execute(
            """update ops.job set state='running',attempt=1,
                        lease_owner='engineering-controller-expiry',lease_token=%s,
                        leased_until=clock_timestamp()+make_interval(secs=>%s),
                        started_at=clock_timestamp() where id=%s""",
            (token, job_lease_seconds, fixture_row[0]),
        )
    cur.execute(
        "insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state) values(%s,1,'engineering-controller-expiry',%s,'running')",
        (fixture_row[0], token),
    )
    return (fixture_row[0], token, 1)


def expiry_epoch(conn, fixture_row):
    with conn.cursor() as cur:
        value = one(
            cur,
            """select extract(epoch from least(e.expires_at,s.lease_expires_at,j.leased_until))
                  from ops.engineering_execution_envelope e
                  join ops.capability_agent_session s on s.id=e.agent_session_id
                  join ops.job j on j.id=e.job_id where e.id=%s""",
            (fixture_row[1],),
        )[0]
    return float(value)


def wait_past_expiry(expiry):
    deadline = expiry + 0.25
    while time.time() < deadline:
        time.sleep(min(0.05, max(0.001, deadline - time.time())))


def assert_unfinalized(cur, fixture_row, label):
    if one(cur, "select state from ops.job where id=%s", (fixture_row[0],))[0] != "running":
        raise RuntimeError(f"{label} terminalized the job")
    if one(cur, "select state from ops.job_attempt where job_id=%s", (fixture_row[0],))[0] != "running":
        raise RuntimeError(f"{label} terminalized the attempt")
    if one(cur, "select count(*) from ops.engineering_slice_receipt where envelope_id=%s", (fixture_row[1],))[0] != 0:
        raise RuntimeError(f"{label} left an engineering receipt")
    if one(cur, "select count(*) from ops.job_receipt where job_id=%s and kind in ('completion','failure')", (fixture_row[0],))[0] != 0:
        raise RuntimeError(f"{label} left a completion/failure job receipt")


def post_wait_expiry_case(conn, dsn, fixture_row, claim):
    """A real receipt starts before expiry, waits on lineage, then refuses."""
    expiry = expiry_epoch(conn, fixture_row)
    conn.commit()
    if expiry - time.time() <= 1.0:
        raise RuntimeError("post-wait expiry fixture was already too close to expiry")
    plan_id, slice_ref, _session_id, _work_request_id = lineage(conn, fixture_row[1])
    key = lock_key(plan_id, slice_ref)
    conn.commit()
    holder_ready, receipt_started = Event(), Event()
    result, errors = {}, []

    def holder():
        try:
            with psycopg.connect(dsn) as held:
                with held.cursor() as cur:
                    cur.execute(admission_lock_sql(), (key,))
                    holder_ready.set()
                    if not receipt_started.wait(5):
                        raise RuntimeError("receipt peer did not start before holder timeout")
                    wait_past_expiry(expiry)
                    held.rollback()
        except BaseException as exc:  # surfaced by the main gate thread
            errors.append(("lineage-holder", exc))
            holder_ready.set()
            receipt_started.set()

    def receipt_peer():
        try:
            with psycopg.connect(dsn) as peer:
                with peer.cursor() as cur:
                    cur.execute("set local lock_timeout='15000ms'")
                    cur.execute("set local statement_timeout='18000ms'")
                    receipt_started.set()
                    result["started_before_expiry"] = one(
                        cur,
                        "select clock_timestamp() < (select least(e.expires_at,s.lease_expires_at,j.leased_until) from ops.engineering_execution_envelope e join ops.capability_agent_session s on s.id=e.agent_session_id join ops.job j on j.id=e.job_id where e.id=%s)",
                        (fixture_row[1],),
                    )[0]
                    set_jobs(cur)
                    try:
                        receipt(cur, fixture_row, claim, "claimed_complete")
                    except psycopg.Error as exc:
                        result["sqlstate"] = exc.sqlstate
                        result["error"] = str(exc)
                        peer.rollback()
                    else:
                        result["accepted"] = True
                        peer.rollback()
        except BaseException as exc:  # surfaced by the main gate thread
            errors.append(("receipt-peer", exc))
            receipt_started.set()

    holder_thread = Thread(target=holder, name="engineering-expiry-lineage-holder")
    peer_thread = Thread(target=receipt_peer, name="engineering-expiry-receipt-peer")
    holder_thread.start()
    if not holder_ready.wait(5):
        raise RuntimeError("lineage holder did not acquire the exact advisory lock")
    peer_thread.start()
    if not receipt_started.wait(5):
        raise RuntimeError("receipt peer did not begin before envelope expiry")
    peer_thread.join(20)
    holder_thread.join(20)
    if peer_thread.is_alive() or holder_thread.is_alive():
        raise RuntimeError("post-wait expiry race exceeded bounded timeout")
    if errors:
        raise RuntimeError(f"post-wait expiry race failed in {errors[0][0]}") from errors[0][1]
    if result.get("started_before_expiry") is not True:
        raise RuntimeError("receipt did not begin while the envelope/session/job were current")
    if result.get("accepted") or result.get("sqlstate") in {"55P03", "40P01"}:
        raise RuntimeError(f"post-wait expiry returned an unsafe result: {result}")
    if not result.get("sqlstate"):
        raise RuntimeError(f"post-wait expiry returned no deterministic refusal: {result}")
    with conn.cursor() as cur:
        assert_unfinalized(cur, fixture_row, "post-wait expiry")
    conn.commit()


def reaper_receipt_contention_case(conn, dsn, fixture_row, claim):
    """Maintenance must skip a job locked by the real receipt path."""
    with conn.cursor() as cur:
        original_executable_sql = one(cur, "select pg_get_functiondef('ops.engineering_envelope_is_executable(uuid,uuid)'::regprocedure)")[0]
        cur.execute(REAPER_RECEIPT_EXECUTABLE_SQL)
    conn.commit()
    expiry = expiry_epoch(conn, fixture_row)
    result, errors = {}, []

    def receipt_peer():
        try:
            with psycopg.connect(dsn) as peer:
                with peer.cursor() as cur:
                    cur.execute("set local lock_timeout='12000ms'")
                    cur.execute("set local statement_timeout='30000ms'")
                    set_jobs(cur)
                    try:
                        receipt(cur, fixture_row, claim, "claimed_complete")
                    except psycopg.Error as exc:
                        result["sqlstate"] = exc.sqlstate
                        result["error"] = str(exc)
                    else:
                        result["accepted"] = True
                peer.rollback()
        except BaseException as exc:  # surfaced by the main gate thread
            errors.append(exc)

    peer_thread = Thread(target=receipt_peer, name="engineering-reaper-receipt-peer")
    peer_thread.start()
    barrier_seen = False
    with psycopg.connect(dsn) as maintenance:
        # The peer has to open a fresh PostgreSQL connection, assume carr_jobs,
        # and enter the production receipt function before the deliberately
        # injected advisory-lock barrier becomes visible. Six seconds was
        # shorter than that setup path under ordinary concurrent local CI load,
        # so the fixture could expire before it tested the lock ordering it was
        # written to prove. Keep this bounded, but leave enough setup runway.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            with maintenance.cursor() as cur:
                acquired = one(
                    cur,
                    "select pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                    (REAPER_RECEIPT_BARRIER,),
                )[0]
            if not acquired:
                barrier_seen = True
                break
            maintenance.rollback()
            time.sleep(0.05)
        if not barrier_seen:
            maintenance.rollback()
            peer_thread.join(16)
            raise RuntimeError("receipt did not reach the maintenance contention barrier")
        wait_past_expiry(expiry)
        with maintenance.cursor() as cur:
            cur.execute("set local lock_timeout='300ms'")
            cur.execute("set local statement_timeout='1500ms'")
            cur.execute("set local role carr_jobs")
            cur.execute("select ops.reap_expired_jobs()")
            cur.execute("select ops.engineering_retire_permanently_ineligible_jobs()")
            job_state = one(
                cur,
                "select state,lease_token,attempt,leased_until<clock_timestamp() from ops.job where id=%s",
                (fixture_row[0],),
            )
            if job_state != ("running", claim[1], claim[2], True):
                raise RuntimeError(f"maintenance changed the receipt-locked Engineering job: {job_state}")
            attempt_state = one(
                cur,
                "select state,lease_token from ops.job_attempt where job_id=%s and attempt=%s",
                (fixture_row[0], claim[2]),
            )
            if attempt_state != ("running", claim[1]):
                raise RuntimeError(f"maintenance changed the receipt-locked Engineering attempt: {attempt_state}")
            if one(cur, "select count(*) from ops.engineering_slice_receipt where envelope_id=%s", (fixture_row[1],))[0] != 0:
                raise RuntimeError("maintenance bypassed the receipt lock with immutable Engineering evidence")
            if one(cur, "select count(*) from ops.job_receipt where job_id=%s", (fixture_row[0],))[0] != 0:
                raise RuntimeError("maintenance bypassed the receipt lock with a job receipt")
        maintenance.rollback()
    peer_thread.join(40)
    if peer_thread.is_alive():
        raise RuntimeError("reaper-versus-receipt probe exceeded its bounded timeout")
    if errors:
        raise RuntimeError("reaper-versus-receipt peer failed") from errors[0]
    if result.get("accepted") or result.get("sqlstate") in {"55P03", "40P01", "57014"}:
        raise RuntimeError(f"reaper-versus-receipt returned an unsafe result: {result}")
    if not result.get("sqlstate"):
        raise RuntimeError(f"reaper-versus-receipt returned no deterministic expiry refusal: {result}")
    with conn.cursor() as cur:
        assert_unfinalized(cur, fixture_row, "reaper-versus-receipt")
        if one(cur, "select count(*) from ops.job_receipt where job_id=%s", (fixture_row[0],))[0] != 0:
            raise RuntimeError("reaper-versus-receipt left a job receipt after rollback")
    conn.commit()
    return original_executable_sql


def delayed_append_expiry_case(conn, dsn, fixture_row, claim, original_executable_sql):
    """Use only the disposable DB to force validation across the append clock."""
    with conn.cursor() as cur:
        cur.execute(DELAYED_EXECUTABLE_SQL)
    conn.commit()
    with conn.cursor() as cur:
        set_jobs(cur)
        try:
            receipt(cur, fixture_row, claim, "claimed_complete")
        except psycopg.Error as exc:
            sqlstate, detail = exc.sqlstate, str(exc)
            conn.rollback()
            with conn.cursor() as check_cur:
                reset_role(check_cur)
                if sqlstate in {"55P03", "40P01"} or "receipt append" not in detail:
                    raise RuntimeError(f"delayed append expiry refused unsafely: SQLSTATE {sqlstate}: {detail}")
                assert_unfinalized(check_cur, fixture_row, "delayed append expiry")
            conn.commit()
            # This disposable database continues into the wrapper acceptance
            # programs after the db-gate loop. Restore the exact helper the
            # fixture observed rather than leaking a timing wrapper downstream.
            with conn.cursor() as restore_cur:
                reset_role(restore_cur)
                restore_cur.execute(original_executable_sql)
            conn.commit()
            return
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.rollback()
    raise RuntimeError("delayed append expiry crossed the append boundary")


def create_dag_b(conn, a_fixture, a_claim, b_ref):
    """Prepare B only after A receipt retirement, review, and dependency pass."""
    with conn.cursor() as cur:
        row = one(
            cur,
            """select e.work_request_id,e.accepted_plan_id,e.slice_plan_id,e.state_version,
                              e.canonical_record_digest,e.envelope,e.job_id,w.ref,
                              s.executor_actor_id,s.created_by_actor_id
                         from ops.engineering_execution_envelope e
                         join ops.job j on j.id=e.job_id
                         join ops.work_request w on w.id=e.work_request_id
                         join ops.capability_agent_session s on s.id=e.agent_session_id
                        where e.id=%s""",
            (a_fixture[1],),
        )
        b_session = one(
            cur,
            """insert into ops.capability_agent_session
                 (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,
                  worktree_ref,scope_ref,lease_expires_at)
                 values (%s,%s,%s,%s,'engineering:server-admission','slice:'||%s,
                         date_trunc('second',now())+interval '29 minutes') returning id""",
            (row[0], row[8], row[9], "0" * 40, b_ref),
        )[0]
        receipt_id = one(cur, "select id from ops.engineering_slice_receipt where envelope_id=%s", (a_fixture[1],))[0]
        joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
        review_fact = reviewer_fact_payload(a_fixture[5], f"attempt:{a_claim[2]}")
        cur.execute("set local role carr_writer")
        # The public schema has no separate review SECURITY DEFINER helper;
        # this is the canonical carr_writer append with the exact A attempt
        # binding consumed by engineering_enqueue_slice_job.
        cur.execute("savepoint dag_dependency_gate")
        try:
            cur.execute(
                "select * from ops.engineering_enqueue_slice_job(%s,%s,%s,%s,%s)",
                (row[7], b_ref, a_fixture[4], f"dag-before-review:{uuid.uuid4()}", 1),
            )
        except psycopg.Error as exc:
            detail = str(exc).lower()
            cur.execute("rollback to savepoint dag_dependency_gate")
            cur.execute("release savepoint dag_dependency_gate")
            if "independently verified" not in detail and "depend" not in detail:
                raise RuntimeError(f"dependent slice returned the wrong pre-review refusal: {exc}") from exc
        else:
            cur.execute("rollback to savepoint dag_dependency_gate")
            cur.execute("release savepoint dag_dependency_gate")
            raise RuntimeError("dependent slice was admitted without an independent pass fact")
        cur.execute(
            """insert into ops.engineering_reviewer_fact
                 (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
                 values (%s,%s,%s,%s,%s,'passed',%s,%s)""",
            (receipt_id, row[0], a_fixture[5], joe_id, review_fact["session_ref"], Jsonb(review_fact), uuid.uuid4()),
        )
        b_job = one(
            cur,
            "select * from ops.engineering_enqueue_slice_job(%s,%s,%s,%s,%s)",
            (row[7], b_ref, a_fixture[4], f"dag-after-review:{uuid.uuid4()}", 1),
        )[0]
    conn.commit()

    with conn.cursor() as cur:
        issued_at = one(cur, "select to_char(date_trunc('second',now()) at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')")[0]
        session_lease = one(cur, "select to_char(lease_expires_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from ops.capability_agent_session where id=%s", (b_session,))[0]
        # 0335 compares the JSON expiry to the relational session lease.  Do
        # not independently sample now() here: crossing a second boundary
        # would mint an envelope that the real claim gate correctly refuses.
        expires_at = session_lease
        envelope = dict(row[5])
        envelope_id = uuid.uuid4()
        envelope["envelope_id"] = f"env:{envelope_id}"
        envelope["issued_at"] = issued_at
        envelope["expires_at"] = expires_at
        envelope["agent_session"] = {**envelope["agent_session"], "id": f"session:{b_session}", "lease_expires_at": session_lease}
        envelope["phase_binding"] = {**envelope["phase_binding"], "phase_id": f"phase:{b_ref}"}
        envelope["request"] = {**envelope["request"], "job_ref": f"job:{b_job}"}
        b_digest = unique_sha()
        cur.execute("set local role carr_writer")
        cur.execute(
            DAG_ENVELOPE_INSERT_SQL,
            (envelope_id, b_job, row[0], row[1], row[2], b_ref, b_session, row[3], row[4], b_digest,
             Jsonb(envelope), issued_at, expires_at),
        )
    conn.commit()
    return (b_job, envelope_id, b_session, a_fixture[3], a_fixture[4], b_ref, b_digest)


def create_dag_b_after_exact_review(conn, a_fixture, b_ref):
    """Enqueue and issue B only after the exact same-slice successor is reviewed."""
    with conn.cursor() as cur:
        row = one(
            cur,
            """select e.work_request_id,e.accepted_plan_id,e.slice_plan_id,e.state_version,
                              e.canonical_record_digest,e.envelope,e.job_id,w.ref,
                              s.executor_actor_id,s.created_by_actor_id
                         from ops.engineering_execution_envelope e
                         join ops.job j on j.id=e.job_id
                         join ops.work_request w on w.id=e.work_request_id
                         join ops.capability_agent_session s on s.id=e.agent_session_id
                        where e.id=%s""",
            (a_fixture[1],),
        )
        b_session = one(
            cur,
            """insert into ops.capability_agent_session
                 (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,
                  worktree_ref,scope_ref,lease_expires_at)
                 values (%s,%s,%s,%s,'engineering:server-admission','slice:'||%s,
                         date_trunc('second',now())+interval '29 minutes') returning id""",
            (row[0], row[8], row[9], "0" * 40, b_ref),
        )[0]
        b_job = one(cur, "select * from ops.engineering_enqueue_slice_job(%s,%s,%s,%s,%s)",
                    (row[7], b_ref, a_fixture[4], f"dag-exact-review:{uuid.uuid4()}", 1))[0]
        issued_at = one(cur, "select to_char(date_trunc('second',now()) at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')")[0]
        session_lease = one(cur, "select to_char(lease_expires_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from ops.capability_agent_session where id=%s", (b_session,))[0]
        # Reuse the stored lease for the JSON envelope; a second independent
        # date_trunc(now()) can drift across a second and lose B at claim time.
        expires_at = session_lease
        envelope = dict(row[5])
        envelope_id = uuid.uuid4()
        envelope["envelope_id"] = f"env:{envelope_id}"
        envelope["issued_at"] = issued_at
        envelope["expires_at"] = expires_at
        envelope["agent_session"] = {**envelope["agent_session"], "id": f"session:{b_session}", "lease_expires_at": session_lease}
        envelope["phase_binding"] = {**envelope["phase_binding"], "phase_id": f"phase:{b_ref}"}
        envelope["request"] = {**envelope["request"], "job_ref": f"job:{b_job}"}
        b_digest = unique_sha()
        cur.execute("set local role carr_writer")
        cur.execute(DAG_ENVELOPE_INSERT_SQL,
                    (envelope_id, b_job, row[0], row[1], row[2], b_ref, b_session, row[3], row[4], b_digest,
                     Jsonb(envelope), issued_at, expires_at))
    conn.commit()
    return (b_job, envelope_id, b_session, a_fixture[3], a_fixture[4], b_ref, b_digest)


def assert_dag_progression(cur, a_fixture, a_claim, a_receipt_id, b_fixture, b_claim):
    a_job, a_envelope, a_session = a_fixture[0], a_fixture[1], a_fixture[2]
    b_job, b_envelope, b_session = b_fixture[0], b_fixture[1], b_fixture[2]
    if a_session == b_session:
        raise RuntimeError("DAG successor reused the predecessor's capability session")
    if one(cur, "select state from ops.capability_agent_session where id=%s", (a_session,))[0] != "cancelled":
        raise RuntimeError("A receipt did not atomically retire the exact server session")
    if one(cur, "select state from ops.job where id=%s", (a_job,))[0] != "succeeded":
        raise RuntimeError("A receipt did not atomically terminalize the A job")
    if one(cur, "select state from ops.job_attempt where job_id=%s and attempt=%s", (a_job, a_claim[2]))[0] != "succeeded":
        raise RuntimeError("A receipt did not terminalize the exact A attempt")
    if one(cur, "select count(*) from ops.engineering_slice_receipt where id=%s and envelope_id=%s and attempt_id=%s", (a_receipt_id, a_envelope, f"attempt:{a_claim[2]}"))[0] != 1:
        raise RuntimeError("A receipt was not persisted against the exact attempt")
    if one(cur, "select count(*) from ops.job_receipt where job_id=%s and kind='completion'", (a_job,))[0] != 1:
        raise RuntimeError("A receipt did not leave exactly one completion job receipt")
    if one(cur, "select ops.engineering_envelope_currentness(%s,%s)->>'reason'", (a_envelope, a_job))[0] != "already_receipted":
        raise RuntimeError("A envelope was not terminally recognized as already receipted")
    if one(cur, "select state from ops.job where id=%s", (b_job,))[0] != "running":
        raise RuntimeError("B was not claimed through engineering_claim_slice")
    if one(cur, "select state from ops.job_attempt where job_id=%s and attempt=%s", (b_job, b_claim[2]))[0] != "running":
        raise RuntimeError("B claim did not persist its exact running attempt")
    if one(cur, "select state from ops.capability_agent_session where id=%s", (b_session,))[0] != "claimed":
        raise RuntimeError("B claim did not use the fresh claimed server session")
    if one(
        cur,
        """select count(*) from ops.engineering_execution_envelope e
             join ops.capability_agent_session s on s.id=e.agent_session_id
            where e.id=%s and e.expires_at=s.lease_expires_at
              and e.envelope->>'expires_at'=to_char(e.expires_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')
              and e.envelope#>>'{agent_session,lease_expires_at}'=to_char(s.lease_expires_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')""",
        (b_envelope,),
    )[0] != 1:
        raise RuntimeError("B envelope expiry was not bound exactly to its stored session lease")
    if one(cur, "select count(*) from ops.capability_agent_session where work_request_id=(select work_request_id from ops.engineering_execution_envelope where id=%s) and state not in ('cancelled','completed')", (b_envelope,))[0] != 1:
        raise RuntimeError("same-work-request open-session invariant was violated")
    if one(cur, "select count(*) from ops.engineering_slice_receipt where envelope_id=%s", (b_envelope,))[0] != 0:
        raise RuntimeError("B unexpectedly received evidence before its worker receipt")


def main():
    dsn = os.environ.get("CARR_LOCAL_PG_DSN") or os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    assert_static_contract()
    assert_gate_sql_arity()
    fixture = load_fixture()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            hard_fence(cur, dsn)
            cur.execute("grant carr_jobs,carr_writer to carr_ci with set true")
            if not one(cur, "select has_table_privilege('carr_writer','ops.engineering_execution_envelope','insert')")[0]:
                raise RuntimeError("carr_writer lacks the production envelope INSERT capability")
            private_helpers = (
                "ops.engineering_receipt_exact_object(jsonb,text[])",
                "ops.engineering_receipt_identifier_array(jsonb)",
                "ops.engineering_receipt_identifier_sets_equal(jsonb,jsonb)",
                "ops.engineering_receipt_evidence_array(jsonb)",
            )
            for role in ("public", "carr_jobs", "carr_writer", "carr_reader", "carr_authority"):
                for signature in private_helpers:
                    if one(cur, "select has_function_privilege(%s,%s::regprocedure,'EXECUTE')", (role, signature))[0]:
                        raise RuntimeError(f"private receipt helper {signature} is executable by {role}")
            dag_a_ref = f"slice:dag-a-{uuid.uuid4().hex[:12]}"
            dag_b_ref = f"slice:dag-b-{uuid.uuid4().hex[:12]}"
            lineage_a_ref = f"slice:lineage-a-{uuid.uuid4().hex[:12]}"
            lineage_b_ref = f"slice:lineage-b-{uuid.uuid4().hex[:12]}"
            good = fixture(cur)
            dag_a = fixture(cur, slice_refs=(dag_a_ref, dag_b_ref), slice_dependencies={dag_b_ref: [dag_a_ref]})
            lineage_a = fixture(cur, slice_refs=(lineage_a_ref, lineage_b_ref), slice_dependencies={lineage_b_ref: [lineage_a_ref]})
            weak_missing = fixture(cur)
            weak_malformed = fixture(cur)
            malformed_deviation_a_ref = f"slice:malformed-deviation-a-{uuid.uuid4().hex[:12]}"
            malformed_deviation_b_ref = f"slice:malformed-deviation-b-{uuid.uuid4().hex[:12]}"
            malformed_envelope_a_ref = f"slice:malformed-envelope-a-{uuid.uuid4().hex[:12]}"
            malformed_envelope_b_ref = f"slice:malformed-envelope-b-{uuid.uuid4().hex[:12]}"
            duplicate_deviation_a_ref = f"slice:duplicate-deviation-a-{uuid.uuid4().hex[:12]}"
            duplicate_deviation_b_ref = f"slice:duplicate-deviation-b-{uuid.uuid4().hex[:12]}"
            malformed_deviation = fixture(cur, slice_refs=(malformed_deviation_a_ref, malformed_deviation_b_ref), slice_dependencies={malformed_deviation_b_ref: [malformed_deviation_a_ref]})
            malformed_envelope = fixture(cur, lambda envelope: (envelope.__setitem__("envelope_id", "env:wrong"), envelope["request"].__setitem__("job_ref", "job:wrong")), slice_refs=(malformed_envelope_a_ref, malformed_envelope_b_ref), slice_dependencies={malformed_envelope_b_ref: [malformed_envelope_a_ref]})
            duplicate_deviation = fixture(cur, slice_refs=(duplicate_deviation_a_ref, duplicate_deviation_b_ref), slice_dependencies={duplicate_deviation_b_ref: [duplicate_deviation_a_ref]})
            retry = fixture(cur)
            dead = fixture(cur)
            scoped_retry = fixture(cur)
            scoped_dead = fixture(cur)
            stale = fixture(cur)
            hold_admission = fixture(cur)
            hold_reverse = fixture(cur)
            hold_successor = fixture(cur)
            reverse_successor = fixture(cur, lambda envelope: envelope["server_binding"]["authority"].__setitem__("read_only", True), lease_offset="-1 hour", issued_offset="-2 hours")
            malformed_read_only = fixture(
                cur,
                lambda envelope: envelope["server_binding"]["authority"].__setitem__("read_only", "not-a-boolean"),
            )
            negative_fixtures = [
                (fixture(cur), lambda payload: payload.pop("envelope_digest")),
                (fixture(cur), lambda payload: payload.__setitem__("envelope_digest", None)),
                (fixture(cur), lambda payload: payload.pop("slice_ref")),
                (fixture(cur), lambda payload: payload.__setitem__("slice_ref", None)),
                (fixture(cur), lambda payload: payload.__setitem__("schema_version", "wrong")),
                (fixture(cur), lambda payload: payload.pop("schema_version")),
                (fixture(cur), lambda payload: payload.__setitem__("plan_digest", "sha256:" + "0" * 63)),
                (fixture(cur), lambda payload: payload.pop("plan_digest")),
                (fixture(cur), lambda payload: payload.__setitem__("attribution", "wrong-container")),
                (fixture(cur), lambda payload: payload.__setitem__("attribution", None)),
                (fixture(cur), lambda payload: payload.__setitem__("independent_verification_required", "true")),
                (fixture(cur), lambda payload: payload.__setitem__("artifact_refs", [])),
                (fixture(cur), lambda payload: payload.__setitem__("evidence_refs", [])),
                (fixture(cur), lambda payload: payload.__setitem__("checks", [])),
                (fixture(cur), lambda payload: payload["checks"][0].__setitem__("state", "failed")),
                (fixture(cur), lambda payload: payload["checks"][0].__setitem__("check_ref", "check:bogus")),
                (fixture(cur), lambda payload: payload["checks"][0].pop("check_ref")),
                (fixture(cur), lambda payload: payload["checks"][0]["evidence_refs"][0].__setitem__("redaction_class", "redacted_evidence")),
                (fixture(cur), lambda payload: payload.__setitem__("planned_resource_refs", ["resource:wrong"])),
                (fixture(cur), lambda payload: payload.__setitem__("planned_component_refs", ["component:wrong"])),
                (fixture(cur), lambda payload: payload["source_evidence"].pop("source_sha")),
                (fixture(cur), lambda payload: payload["reset_reconstruction"].pop("reconstruction_free")),
                (fixture(cur), lambda payload: payload["executor_claim"].pop("claimed_at")),
                (fixture(cur), lambda payload: payload.__setitem__("unexpected_field", True)),
                (fixture(cur), lambda payload: None),  # explicit NULL receipt digest
                (fixture(cur), lambda payload: None),  # explicit malformed receipt digest
                (fixture(cur), lambda payload: None),  # explicit valid but mismatched receipt digest
            ]
            cur.execute("update ops.job set max_attempts=1 where id=%s", (dead[0],))
            cur.execute("update ops.job set max_attempts=1 where id=%s", (scoped_dead[0],))
            # 0325 cast this authority flag directly to boolean while deciding
            # whether a live predecessor could be superseded.  The malformed
            # historical row must now be treated as ineligible and replaced,
            # without an uncaught cast error or a fallback to the old row.
            malformed_read_only_successor = insert_successor(
                cur,
                malformed_read_only,
                support_fixture=hold_reverse,
            )
            reset_role(cur)
        conn.commit()  # committed fixtures are required for the peer connections below

        with conn.cursor() as cur:
            if one(cur, "select supersedes_envelope_id from ops.engineering_execution_envelope where id=%s", (malformed_read_only_successor[1],))[0] != malformed_read_only[1]:
                raise RuntimeError("malformed read_only predecessor was not superseded by the exact successor")
        good_claim = claim_one(conn, good[0], "engineering-controller-receipt-good")
        # Binding requires 930 seconds of runway from a 960-second claim.  Prove
        # the exact live binding at the launch boundary, before the deliberately
        # long negative-receipt fixture loop consumes that 30-second margin.
        with conn.cursor() as cur:
            set_jobs(cur)
            if one(cur, "select ops.engineering_controller_binding(%s,%s,%s) is not null", (good[1], good[0], good_claim[1]))[0] is not True:
                raise RuntimeError("actor lifecycle fixture could not read the exact live binding")
            reset_role(cur)
        dag_a_claim = claim_one(conn, dag_a[0], "engineering-controller-dag-a")
        lineage_a_claim = claim_one(conn, lineage_a[0], "engineering-controller-lineage-a")
        weak_missing_claim = claim_one(conn, weak_missing[0], "engineering-controller-weak-missing")
        weak_malformed_claim = claim_one(conn, weak_malformed[0], "engineering-controller-weak-malformed")
        malformed_deviation_claim = claim_one(conn, malformed_deviation[0], "engineering-controller-malformed-deviation")
        with conn.cursor() as cur:
            malformed_envelope_claim = manual_running_claim(cur, malformed_envelope)
        duplicate_deviation_claim = claim_one(conn, duplicate_deviation[0], "engineering-controller-duplicate-deviation")
        retry_claim = claim_one(conn, retry[0], "engineering-controller-receipt-retry")
        dead_claim = claim_one(conn, dead[0], "engineering-controller-receipt-dead")
        scoped_retry_claim = claim_one(conn, scoped_retry[0], "engineering-controller-scoped-retry")
        scoped_dead_claim = claim_one(conn, scoped_dead[0], "engineering-controller-scoped-dead")
        stale_claim = claim_one(conn, stale[0], "engineering-controller-receipt-stale")
        hold_claim = claim_one(conn, hold_admission[0], "engineering-controller-lock-admission")
        reverse_claim = claim_one(conn, hold_reverse[0], "engineering-controller-lock-reverse")
        successor_claim = claim_one(conn, hold_successor[0], "engineering-controller-lock-successor")
        with conn.cursor() as cur:
            reverse_claim = manual_running_claim(cur, reverse_successor)
        conn.commit()
        negative_claims = [claim_one(conn, row[0][0], f"engineering-controller-negative-{index}") for index, row in enumerate(negative_fixtures)]

        null_digest_index = len(negative_fixtures) - 3
        invalid_digest_index = len(negative_fixtures) - 2
        mismatch_digest_index = len(negative_fixtures) - 1
        for index, (fixture_row, mutate) in enumerate(negative_fixtures):
            if index == null_digest_index:
                receipt_digest = None
            elif index == invalid_digest_index:
                receipt_digest = "not-a-sha256-digest"
            elif index == mismatch_digest_index:
                receipt_digest = "sha256:" + "f" * 64
            else:
                receipt_digest = AUTO_DIGEST
            with conn.cursor() as cur:
                set_jobs(cur)
                refused_receipt(cur, fixture_row, negative_claims[index], mutate, receipt_digest)
                reset_role(cur)
            conn.commit()

        with conn.cursor() as cur:
            work_request_id = one(cur, "select work_request_id from ops.engineering_execution_envelope where id=%s", (good[1],))[0]
            cur.execute("savepoint engineering_work_request_reservation")
            try:
                cur.execute("update ops.work_request set title=title||' stale' where id=%s", (work_request_id,))
            except psycopg.Error as exc:
                if "reserved by a live Engineering claim" not in str(exc):
                    raise RuntimeError(f"live Engineering Work Request reservation returned the wrong refusal: {exc}") from exc
                cur.execute("rollback to savepoint engineering_work_request_reservation")
                cur.execute("release savepoint engineering_work_request_reservation")
            else:
                cur.execute("rollback to savepoint engineering_work_request_reservation")
                cur.execute("release savepoint engineering_work_request_reservation")
                raise RuntimeError("live Engineering claim did not reserve Work Request currentness")
            cur.execute("savepoint engineering_live_actor_authority")
            try:
                cur.execute("update public.actor set active=false where id=%s", (good[3],))
            except psycopg.Error as exc:
                cur.execute("rollback to savepoint engineering_live_actor_authority")
                cur.execute("release savepoint engineering_live_actor_authority")
                if "reserved by a live scoped lease" not in str(exc):
                    raise RuntimeError(f"live actor deactivation returned the wrong refusal: {exc}") from exc
            else:
                cur.execute("rollback to savepoint engineering_live_actor_authority")
                cur.execute("release savepoint engineering_live_actor_authority")
                raise RuntimeError("live Engineering lease allowed actor deactivation")
        conn.commit()

        with conn.cursor() as cur:
            set_jobs(cur)
            receipt(cur, good, good_claim, "claimed_complete")
            reset_role(cur)
        conn.commit()
        with conn.cursor() as cur:
            if one(cur, "select state from ops.job where id=%s", (good[0],))[0] != "succeeded":
                raise RuntimeError("complete receipt did not finalize the job")
            if one(cur, "select state from ops.job_attempt where job_id=%s", (good[0],))[0] != "succeeded":
                raise RuntimeError("complete receipt did not finalize the attempt")
            if one(cur, "select kind from ops.job_receipt where job_id=%s order by created_at desc limit 1", (good[0],))[0] != "completion":
                raise RuntimeError("complete receipt did not append the completion job receipt")
            cur.execute("savepoint engineering_terminal_actor_authority")
            cur.execute("""update ops.job set leased_until=clock_timestamp()-interval '1 second'
                            where definition_key='engineering-slice' and definition_version=1
                              and state='running' and lease_token is not null
                              and leased_until>clock_timestamp()""")
            cur.execute("update public.actor set active=false where id=%s", (good[3],))
            if one(cur, "select active from public.actor where id=%s", (good[3],))[0] is not False:
                raise RuntimeError("all Engineering leases terminal or expired did not release actor authority mutation")
            cur.execute("rollback to savepoint engineering_terminal_actor_authority")
            cur.execute("release savepoint engineering_terminal_actor_authority")

        with conn.cursor() as cur:
            set_jobs(cur)
            dag_a_receipt_id = receipt(cur, dag_a, dag_a_claim, "claimed_complete")
            reset_role(cur)
        conn.commit()
        with conn.cursor() as cur:
            set_jobs(cur)
            receipt(cur, lineage_a, lineage_a_claim, "failed")
            reset_role(cur)
        conn.commit()
        with conn.cursor() as cur:
            lineage_failed_receipt_id = one(cur, "select id from ops.engineering_slice_receipt where envelope_id=%s", (lineage_a[1],))[0]
            seed_legacy_failed_predecessor_review(cur, lineage_a, lineage_failed_receipt_id)
        conn.commit()
        dag_b = create_dag_b(conn, dag_a, dag_a_claim, dag_b_ref)
        dag_b_claim = claim_one(conn, dag_b[0], "engineering-controller-dag-b")
        with conn.cursor() as cur:
            assert_dag_progression(cur, dag_a, dag_a_claim, dag_a_receipt_id, dag_b, dag_b_claim)
        conn.commit()

        for fixture_row, claim_row, expected_state in ((retry, retry_claim, "retry_wait"), (dead, dead_claim, "dead_lettered")):
            with conn.cursor() as cur:
                set_jobs(cur)
                receipt(cur, fixture_row, claim_row, "failed")
                reset_role(cur)
            conn.commit()
            with conn.cursor() as cur:
                if one(cur, "select state from ops.job where id=%s", (fixture_row[0],))[0] != expected_state:
                    raise RuntimeError(f"noncomplete receipt did not finalize atomically into {expected_state}")
                if one(cur, "select count(*) from ops.engineering_slice_receipt where envelope_id=%s", (fixture_row[1],))[0] != 1:
                    raise RuntimeError("noncomplete receipt was not appended exactly once")
                if one(cur, "select state from ops.capability_agent_session where id=%s", (fixture_row[2],))[0] not in ("claimed", "in_progress"):
                    raise RuntimeError("noncomplete receipt incorrectly retired its exact server session")

        retry_success_claim = claim_one(conn, retry[0], "engineering-controller-receipt-retry-success")
        with conn.cursor() as cur:
            set_jobs(cur)
            receipt(cur, retry, retry_success_claim, "claimed_complete")
            reset_role(cur)
        conn.commit()
        with conn.cursor() as cur:
            if one(cur, "select state from ops.job where id=%s", (retry[0],))[0] != "succeeded":
                raise RuntimeError("noncomplete Engineering attempt could not retry to success")
            if one(cur, "select count(*) from ops.engineering_slice_receipt where envelope_id=%s", (retry[1],))[0] != 2:
                raise RuntimeError("retry-to-success did not preserve both immutable typed receipts")
            if one(cur, "select state from ops.capability_agent_session where id=%s", (retry[2],))[0] != "cancelled":
                raise RuntimeError("retry-to-success did not retire the exact session only after success")

        for fixture_row, claim_row, expected_state in (
                (scoped_retry, scoped_retry_claim, "retry_wait"),
                (scoped_dead, scoped_dead_claim, "dead_lettered")):
            with conn.cursor() as cur:
                set_jobs(cur)
                state = one(cur, "select ops.engineering_fail_claim(%s,%s,'engineering_adapter_failed','scoped gate failure')",
                            (fixture_row[0], claim_row[1]))[0]
                reset_role(cur)
            conn.commit()
            with conn.cursor() as cur:
                if state != expected_state or one(cur, "select state from ops.job where id=%s", (fixture_row[0],))[0] != expected_state:
                    raise RuntimeError(f"scoped Engineering failure did not transition into {expected_state}")
                if one(cur, "select count(*) from ops.engineering_slice_receipt where envelope_id=%s", (fixture_row[1],))[0] != 0:
                    raise RuntimeError("receipt-less scoped failure appended a typed Engineering receipt")
                if one(cur, "select state from ops.capability_agent_session where id=%s", (fixture_row[2],))[0] not in ("claimed", "in_progress"):
                    raise RuntimeError("receipt-less scoped failure retired the reusable session")
                if one(cur, "select count(*) from ops.job_receipt where job_id=%s", (fixture_row[0],))[0] != 1:
                    raise RuntimeError("receipt-less scoped failure did not append one canonical job receipt")

        with conn.cursor() as cur:
            complete_receipt_id = one(cur, "select id from ops.engineering_slice_receipt where envelope_id=%s", (good[1],))[0]
            failed_receipt_id = one(cur, "select id from ops.engineering_slice_receipt where envelope_id=%s and outcome='failed'", (retry[1],))[0]
            assert_reviewer_fact_guards(cur, good, retry, complete_receipt_id, failed_receipt_id)
            assert_weak_receipt_review_refused(cur, weak_missing, weak_missing_claim, lambda payload: payload.pop("attribution"), "reviewer missing receipt attribution")
            assert_weak_receipt_review_refused(cur, weak_malformed, weak_malformed_claim, lambda payload: payload["attribution"].__setitem__("session_ref", "session:wrong"), "reviewer malformed receipt session")
            seed_malformed_dependency_refusal(cur, malformed_deviation, malformed_deviation_claim, malformed_deviation_b_ref, lambda payload: payload.__setitem__("deviations", {}), "deviations-object")
            seed_malformed_dependency_refusal(cur, malformed_envelope, malformed_envelope_claim, malformed_envelope_b_ref, lambda payload: None, "envelope-id-job-ref-mismatch")
            seed_malformed_dependency_refusal(cur, duplicate_deviation, duplicate_deviation_claim, duplicate_deviation_b_ref,
                                               lambda payload: payload.__setitem__("deviations", [{
                                                   "category": "scope", "deviation_ref": "deviation:duplicate",
                                                   "evidence_refs": [{"content_digest": sha("f"), "redaction_class": "metadata_only", "ref": "evidence:receipt-fixture"}],
                                                   "impact": "low", "out_of_scope_component_refs": [],
                                                   "out_of_scope_resource_refs": [], "plan_revision_required": False,
                                                   "reason": "duplicate fixture deviation", "review_state": "resolved",
                                               }] * 2),
                                               "duplicate-deviation-ref", ["deviation:duplicate"])
        conn.commit()

        # A failed predecessor and an unreviewed same-slice attempt: B must
        # remain closed until the canonical pass is bound to the successor.
        with conn.cursor() as cur:
            lineage_successor = insert_successor(cur, lineage_a, cancel_prior=True)
        conn.commit()
        lineage_successor_claim = claim_one(conn, lineage_successor[0], "engineering-controller-lineage-successor")
        with conn.cursor() as cur:
            work_ref = one(cur, "select ref from ops.work_request where id=(select work_request_id from ops.engineering_execution_envelope where id=%s)", (lineage_successor[1],))[0]
            cur.execute("set local role carr_writer")
            cur.execute("savepoint lineage_no_receipt_refusal")
            try:
                cur.execute("select * from ops.engineering_enqueue_slice_job(%s,%s,%s,%s,%s)",
                            (work_ref, lineage_b_ref, lineage_a[4], f"lineage-before-successor-receipt:{uuid.uuid4()}", 1))
            except psycopg.Error as exc:
                if "depend" not in str(exc).lower() and "verified" not in str(exc).lower():
                    raise RuntimeError(f"no-receipt same-slice successor returned the wrong refusal: {exc}") from exc
                cur.execute("rollback to savepoint lineage_no_receipt_refusal")
                cur.execute("release savepoint lineage_no_receipt_refusal")
            else:
                cur.execute("rollback to savepoint lineage_no_receipt_refusal")
                cur.execute("release savepoint lineage_no_receipt_refusal")
                raise RuntimeError("dependent B was admitted from a successor without a receipt")
            cur.execute("reset role")
        conn.commit()

        # Once the leaf has a claimed-complete receipt it must still remain
        # closed until an exact independent review is bound to that receipt.
        with conn.cursor() as cur:
            set_jobs(cur)
            lineage_successor_receipt_id = receipt(cur, lineage_successor, lineage_successor_claim, "claimed_complete")
            reset_role(cur)
        conn.commit()
        with conn.cursor() as cur:
            work_ref = one(cur, "select ref from ops.work_request where id=(select work_request_id from ops.engineering_execution_envelope where id=%s)", (lineage_successor[1],))[0]
            cur.execute("set local role carr_writer")
            cur.execute("savepoint lineage_dependent_refusal")
            try:
                cur.execute("select * from ops.engineering_enqueue_slice_job(%s,%s,%s,%s,%s)",
                            (work_ref, lineage_b_ref, lineage_a[4], f"lineage-before-review:{uuid.uuid4()}", 1))
            except psycopg.Error as exc:
                if "depend" not in str(exc).lower() and "verified" not in str(exc).lower():
                    raise RuntimeError(f"unreviewed same-slice successor returned the wrong refusal: {exc}") from exc
                cur.execute("rollback to savepoint lineage_dependent_refusal")
                cur.execute("release savepoint lineage_dependent_refusal")
            else:
                cur.execute("rollback to savepoint lineage_dependent_refusal")
                cur.execute("release savepoint lineage_dependent_refusal")
                raise RuntimeError("dependent B was admitted from an unreviewed successor")
            joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
            successor_work_request_id = one(cur, "select work_request_id from ops.engineering_execution_envelope where id=%s", (lineage_successor[1],))[0]
            successor_fact = reviewer_fact_payload(lineage_a_ref)
            cur.execute(
                """insert into ops.engineering_reviewer_fact
                     (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
                   values (%s,%s,%s,%s,%s,'passed',%s,%s)""",
                (lineage_successor_receipt_id, successor_work_request_id, lineage_a_ref, joe_id,
                 successor_fact["session_ref"], Jsonb(successor_fact), uuid.uuid4()),
            )
            cur.execute("reset role")
        conn.commit()
        lineage_b = create_dag_b_after_exact_review(conn, lineage_successor, lineage_b_ref)
        lineage_b_claim = claim_one(conn, lineage_b[0], "engineering-controller-lineage-b")
        with conn.cursor() as cur:
            if one(cur, "select state from ops.job where id=%s", (lineage_b[0],))[0] != "running":
                raise RuntimeError("exact successor review did not permit B claim")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("update ops.job set leased_until=now()-interval '1 second' where id=%s", (stale[0],))
        conn.commit()
        with conn.cursor() as cur:
            set_jobs(cur)
            try:
                receipt(cur, stale, stale_claim, "claimed_complete")
            except psycopg.Error as exc:
                if "lease" not in str(exc).lower() and "current" not in str(exc).lower():
                    raise
                conn.rollback()
            else:
                raise RuntimeError("expired job lease accepted a receipt")
            if one(cur, "select count(*) from ops.engineering_slice_receipt where envelope_id=%s", (stale[1],))[0] != 0:
                raise RuntimeError("expired lease left a false slice receipt")

        # Start this short-lived fixture only after the preceding committed
        # evidence, so the peer can prove it entered while all three clocks
        # were current rather than merely observing an already-expired row.
        with conn.cursor() as cur:
            reset_role(cur)
            near_wait = fixture(cur, lease_offset="12 seconds")
        conn.commit()
        with conn.cursor() as cur:
            near_wait_claim = manual_near_expiry_claim(cur, near_wait)
        conn.commit()
        post_wait_expiry_case(conn, dsn, near_wait, near_wait_claim)

        # A real receipt holds session, actor, and lineage; the matching
        # admission order must block on the exact session before lineage.
        plan_id, slice_ref, session_id, work_request_id = lineage(conn, hold_admission[1])
        key = lock_key(plan_id, slice_ref)
        with psycopg.connect(dsn) as held, psycopg.connect(dsn) as peer:
            with held.cursor() as cur:
                set_jobs(cur)
                receipt(cur, hold_admission, hold_claim, "claimed_complete")
                reset_role(cur)
            with peer.cursor() as cur:
                cur.execute("set local lock_timeout='300ms'")
                try:
                    cur.execute("select id from ops.capability_agent_session where id=%s and work_request_id=%s for update", (session_id, work_request_id))
                    cur.execute("select a.id from public.actor a join ops.capability_agent_session s on s.executor_actor_id=a.id where s.id=%s and a.active and a.kind='automation' and a.slug='codex' for share of a", (session_id,))
                    cur.execute(admission_lock_sql(), (key,))
                except psycopg.errors.LockNotAvailable:
                    peer.rollback()
                except psycopg.Error as exc:
                    raise RuntimeError(f"admission-vs-receipt produced {exc.sqlstate}") from exc
                else:
                    raise RuntimeError("admission ordering did not wait behind receipt")
            held.rollback()

        # Matching order: admission owns the exact session and actor before
        # lineage; receipt waits on the session row and cannot form a cycle.
        # The production transition itself is not attempted because 0325
        # correctly rejects terminalization while the dispatch lease is live.
        plan_id, slice_ref, session_id, work_request_id = lineage(conn, hold_reverse[1])
        key = lock_key(plan_id, slice_ref)
        with psycopg.connect(dsn) as admission, psycopg.connect(dsn) as receipt_peer:
            with admission.cursor() as cur:
                cur.execute("select id from ops.capability_agent_session where id=%s and work_request_id=%s for update", (session_id, work_request_id))
                cur.execute("select a.id from public.actor a join ops.capability_agent_session s on s.executor_actor_id=a.id where s.id=%s and a.active and a.kind='automation' and a.slug='codex' for share of a", (session_id,))
                cur.execute(admission_lock_sql(), (key,))
            with receipt_peer.cursor() as cur:
                set_jobs(cur)
                cur.execute("set local lock_timeout='300ms'")
                try:
                    receipt(cur, hold_reverse, reverse_claim, "claimed_complete")
                except psycopg.errors.LockNotAvailable:
                    receipt_peer.rollback()
                except psycopg.Error as exc:
                    raise RuntimeError(f"receipt-vs-admission produced {exc.sqlstate}") from exc
                else:
                    raise RuntimeError("receipt did not wait behind admission ordering")
            admission.rollback()

        # The actual successor INSERT/trigger and actual receipt call share
        # the lineage advisory lock in both directions.
        plan_id, slice_ref, _session_id, _work_request_id = lineage(conn, hold_successor[1])
        key = lock_key(plan_id, slice_ref)
        with psycopg.connect(dsn) as held, psycopg.connect(dsn) as peer:
            with held.cursor() as cur:
                set_jobs(cur)
                receipt(cur, hold_successor, successor_claim, "claimed_complete")
                reset_role(cur)
            with peer.cursor() as cur:
                cur.execute("set local lock_timeout='300ms'")
                try:
                    insert_successor(cur, hold_successor, support_fixture=hold_reverse)
                except psycopg.errors.LockNotAvailable:
                    peer.rollback()
                except psycopg.Error as exc:
                    raise RuntimeError(f"successor-vs-receipt produced {exc.sqlstate}") from exc
                else:
                    raise RuntimeError("successor trigger did not wait behind receipt")
            held.rollback()

        plan_id, slice_ref, _session_id, _work_request_id = lineage(conn, reverse_successor[1])
        key = lock_key(plan_id, slice_ref)
        with psycopg.connect(dsn) as successor_conn, psycopg.connect(dsn) as receipt_peer:
            with successor_conn.cursor() as cur:
                # A real successor may not terminalize a session while 0325's
                # dispatch lease is live.  Expire this rollback-only fixture's
                # job lease first, then prove the successor trigger still owns
                # the shared lineage lock before the receipt can inspect it.
                cur.execute("update ops.job set leased_until=now()-interval '1 second' where id=%s", (reverse_successor[0],))
                insert_successor(cur, reverse_successor, cancel_prior=True)
            with receipt_peer.cursor() as cur:
                set_jobs(cur)
                cur.execute("set local lock_timeout='300ms'")
                try:
                    receipt(cur, reverse_successor, reverse_claim, "claimed_complete")
                except psycopg.errors.LockNotAvailable:
                    receipt_peer.rollback()
                except psycopg.Error as exc:
                    raise RuntimeError(f"receipt-vs-successor produced {exc.sqlstate}") from exc
                else:
                    raise RuntimeError("receipt did not wait behind successor trigger")
            successor_conn.rollback()

        # A real receipt holds the ordered authority/job locks while both
        # maintenance functions run under bounded timeouts and skip the target.
        with conn.cursor() as cur:
            reset_role(cur)
            # This is fixture runway, not the production lease contract. The
            # prior four-second value could elapse while the peer connection
            # was still starting, causing a false "barrier not reached" result.
            near_reaper = fixture(cur, lease_offset="15 seconds")
        conn.commit()
        with conn.cursor() as cur:
            near_reaper_claim = manual_near_expiry_claim(cur, near_reaper)
        conn.commit()
        original_executable_sql = reaper_receipt_contention_case(conn, dsn, near_reaper, near_reaper_claim)

        # This is deliberately the final production-path call.  The wrapper
        # exists only inside disposable carr_ci and forces the naturally
        # called currentness helper to span the final append clock; no
        # persistent database or production hook is changed.
        with conn.cursor() as cur:
            reset_role(cur)
            near_append = fixture(cur, lease_offset="2 seconds")
        conn.commit()
        with conn.cursor() as cur:
            near_append_claim = manual_near_expiry_claim(cur, near_append, job_lease_seconds=8)
        conn.commit()
        delayed_append_expiry_case(conn, dsn, near_append, near_append_claim, original_executable_sql)

    print("engineering controller concurrency gate passed: atomic receipt seam and production lock order are serialized")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - gate reports exact acceptance failure
        print(f"engineering-controller-concurrency-gate: FAIL — {exc}", file=sys.stderr)
        raise
