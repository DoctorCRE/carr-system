#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only proof that carr_jobs can claim one exact Engineering slice.

The fixture uses the real SECURITY DEFINER claim and controller-binding
functions but rolls back every row, temporary role membership, lease, and
attempt. It protects against a PL/pgSQL output-variable ambiguity that is only
visible when PostgreSQL executes the claim CTE.
"""

from __future__ import annotations

import hashlib
from itertools import count
import json
import os
from pathlib import Path
import re
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


RUNTIME_ROLE = "carr_jobs"
# A transaction shares now(); random UUID prefixes can pick the same due slot.
# Every fixture in this interpreter instead receives its own microsecond offset.
_FIXTURE_SCHEDULE_OFFSETS = count()
STALE_PLAN_REF = "PLAN-954b03dd464d-v1"
STALE_PLAN_HASH = "sha256:954b03dd464dab9986bc705d01fdb197edd29b23ad10ca673da53ad0ac03c27d"


def fail(message: str) -> int:
    print(f"engineering-claim-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, query: str, params: tuple = ()):  # pragma: no cover - DB-only gate
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError(f"required fixture row was not returned: {query[:120]}")
    return row


def sha(char: str) -> str:
    return "sha256:" + char * 64


def canonical_json(value):
    """Match ops.guidance_import_canonical_json for JSON-safe fixture values."""
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


def canonical_digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()



def assert_non_codex_admission_refused(cur):
    """Direct envelope admission must reject a human executor before currentness."""
    cur.execute("savepoint non_codex_engineering_envelope_admission")
    try:
        fixture(cur, executor_slug="joe", executor_kind="human")
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint non_codex_engineering_envelope_admission")
        cur.execute("release savepoint non_codex_engineering_envelope_admission")
        if "engineering envelope executor actor is not current" not in str(exc):
            raise RuntimeError(f"non-Codex admission raised the wrong refusal: {exc}") from exc
    else:
        cur.execute("rollback to savepoint non_codex_engineering_envelope_admission")
        cur.execute("release savepoint non_codex_engineering_envelope_admission")
        raise RuntimeError("non-Codex executor was admitted into an Engineering envelope")

def fixture(cur, mutate_envelope=None, *, session_state: str = "claimed", lease_offset: str = "29 minutes", issued_offset: str = "0", slice_refs=None, slice_dependencies=None, planned_check_refs=None, executor_slug: str = "codex", executor_kind: str = "automation", stale_contract: bool = False, source_merge_paths=None):
    token = uuid.uuid4().hex
    accepted_plan_hash = STALE_PLAN_HASH if stale_contract else sha("c")
    accepted_plan_ref = STALE_PLAN_REF if stale_contract else f"PLAN-{token[:12]}-v1"
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    codex_id = one(cur, "select id from actor where slug=%s and active and kind=%s", (executor_slug, executor_kind))[0]
    document_id = one(
        cur,
        """insert into doctrine_document(slug,title,content_class,visibility,created_by)
             values (%s,'Engineering claim fixture','reference','shared',%s) returning id""",
        (f"engineering-claim-{token}", joe_id),
    )[0]
    section_id = one(
        cur,
        """insert into doctrine_section(document_id,section_key,title,ordinal,status,current_version)
             values (%s,'fixture','Engineering claim fixture',1,'active',1) returning id""",
        (document_id,),
    )[0]
    revision_id = one(
        cur,
        """insert into doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash,commit_message)
             values (%s,1,%s,%s,'Engineering claim fixture',%s,'fixture') returning id""",
        (section_id, joe_id, Jsonb({"text": "Engineering claim fixture"}), "a" * 64),
    )[0]
    # Source-merge acceptance captures a tenant-bound scope. Give that fixture
    # a complete synthetic sourced shape; ordinary legacy fixtures stay as-is.
    if source_merge_paths:
        shape_ref = f"sourced-plan:{accepted_plan_ref}#{accepted_plan_hash}"
        shape_rationale = (
            f"Accepted immutable plan {accepted_plan_ref} for doctrine:runbook#fixture "
            f"at sha256:{'b' * 64}"
        )
        # This gate tests ownership from an accepted synthetic lineage, while
        # the sourced-capture gate separately proves the captured-to-ready
        # transition. Bypass INSERT triggers only while placing this fixture;
        # the table CHECK still validates its complete sourced shape.
        cur.execute("set local session_replication_role=replica")
        try:
            work_request_id = one(
                cur,
                """insert into ops.work_request
                 (ref,state,title,requester_actor,owner_actor,organization_tenant_id,
                  origin_ref,doctrine_section_id,doctrine_revision_id,
                  capture_idempotency_key,sourced_capture_sequence,
                  triage_classification,triaged_by_actor_id,triaged_at,
                  shape_disposition,shape_fixed_surface_ref,shape_rationale,
                  shape_decided_by_actor_id,shape_decided_at)
                 values (%s,'ready','Engineering claim fixture','joe','joe','carr-internal',
                         %s,%s,%s,%s,nextval('ops.work_request_ref_seq'),
                         'operational',%s,now(),'not_required',%s,%s,%s,now()) returning id""",
                (f"WR-ENGINEERING-CLAIM-{token}", f"doctrine:engineering-claim-{token}#fixture",
                 section_id, revision_id, uuid.uuid4(), joe_id,
                 shape_ref, shape_rationale, joe_id),
            )[0]
        finally:
            cur.execute("set local session_replication_role=origin")
    else:
        shape_ref = "fixture:engineering-currentness"
        shape_rationale = "fixture currentness acceptance"
        work_request_id = one(
            cur,
            """insert into ops.work_request
                 (ref,state,title,requester_actor,owner_actor,shape_disposition,
                  shape_fixed_surface_ref,shape_rationale,shape_decided_by_actor_id,shape_decided_at)
                 values (%s,'ready','Engineering claim fixture','joe','joe','not_required',
                         'fixture:engineering-currentness','fixture currentness acceptance',%s,now()) returning id""",
            (f"WR-ENGINEERING-CLAIM-{token}", joe_id),
        )[0]
    plan_id = one(
        cur,
        """insert into ops.sourced_work_request_plan
             (work_request_id,plan_version,idempotency_key,work_request_version,preimage,
              scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,runbook_content_hash,
              dependency_refs,recovery_ref,observability_ref,caps,plan_hash,plan_ref)
             values (%s,1,%s,1,%s,'fixture scope','doctrine:runbook#fixture',%s,%s,%s,
                     %s,'safe:recovery:fixture','safe:observability:fixture',%s,%s,%s)
             returning id""",
        (work_request_id, uuid.uuid4(), Jsonb({}), section_id, revision_id, "b" * 64,
         Jsonb([]), Jsonb({"source_merge": {"schema_version": "source-merge-scope.v1",
           "repository": "jbookout/carr-system", "base_branch": "main",
           "authorized_paths": source_merge_paths}} if source_merge_paths else {}), accepted_plan_hash, accepted_plan_ref),
    )[0]
    one(
        cur,
        """insert into ops.sourced_work_request_plan_acceptance_receipt
             (work_request_id,plan_id,idempotency_key,base_version,plan_hash,
              accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
             values (%s,%s,%s,1,%s,%s,1,%s,%s)
             returning id""",
        (work_request_id, plan_id, uuid.uuid4(), accepted_plan_hash, joe_id,
         shape_ref, shape_rationale),
    )
    source = one(cur, "select ops.engineering_admission_source(%s)", (f"WR-ENGINEERING-CLAIM-{token}",))[0]
    if stale_contract:
        if (source is None or source.get("execution_authorized") is not False
                or not source.get("execution_refusal_reason")
                or source["accepted_plan"]["plan_ref"] != accepted_plan_ref
                or source["accepted_plan"]["digest"] != accepted_plan_hash):
            raise RuntimeError("stale accepted plan history lost exact readable bindings or its execution fence")
        record_digest = one(
            cur,
            """select 'sha256:'||encode(public.digest(jsonb_build_object(
                 'id',id,'ref',ref,'state',state,'version',version,'title',title,
                 'desired_outcome',desired_outcome,'acceptance_criteria',acceptance_criteria
               )::text,'sha256'),'hex') from ops.work_request where id=%s""",
            (work_request_id,),
        )[0]
        source = {
            "work_request": {
                "id": f"wr:{work_request_id}",
                "version": 1,
                "canonical_record_digest": record_digest,
            },
            "accepted_plan": {
                "digest": accepted_plan_hash,
                "plan_ref": accepted_plan_ref,
                "revision": 1,
            },
        }
    record_digest = source["work_request"]["canonical_record_digest"]
    slice_ref = "slice:claim-fixture"
    if slice_refs is not None:
        if not slice_refs:
            raise ValueError("fixture requires at least one slice reference")
        slice_ref = slice_refs[0]
    if slice_dependencies is None:
        slice_dependencies = {}
    active_refs = (slice_ref,) if slice_refs is None else tuple(slice_refs)
    slice_entries = []
    for ordinal, ref in enumerate(active_refs, start=1):
        check_ref = (planned_check_refs or {}).get(ref, f"check:fixture-{ordinal}")
        slice_entries.append({
            "baseline_evidence_refs": [{
                "content_digest": sha("e"),
                "redaction_class": "metadata_only",
                "ref": f"evidence:baseline-{ordinal}",
            }],
            "concurrency_posture": "parallel_safe",
            "declared_component_refs": [f"component:fixture-{ordinal}"],
            "declared_plan_step_refs": [f"step:fixture-{ordinal}"],
            "declared_resource_refs": [f"resource:fixture-{ordinal}"],
            "definition_of_done": "the typed fixture check passes",
            "dependency_refs": list(slice_dependencies.get(ref, ())),
            "forbidden_change_refs": [],
            "manual_qa_required": False,
            "objective": f"execute fixture slice {ordinal}",
            "ordinal": ordinal,
            "planned_checks": [{
                "check_ref": check_ref,
                "evidence_requirement": "metadata_only_sufficient",
                "failure_condition": "the fixture check does not pass",
            }],
            "release_requirement": "required",
            "risk_class": "R1",
            "scope_boundary": f"fixture scope {ordinal}",
            "slice_ref": ref,
        })
    plan_payload = {
        "accepted_plan_revision": {
            "digest": source["accepted_plan"]["digest"],
            "id": source["accepted_plan"]["plan_ref"],
            "revision": source["accepted_plan"]["revision"],
        },
        "schema_version": "engineering-slice-plan.v1",
        "slices": slice_entries,
        "work_request": {
            "canonical_record_digest": record_digest,
            "id": source["work_request"]["id"],
            "state_version": source["work_request"]["version"],
        },
    }
    plan_digest = canonical_digest(plan_payload)
    plan_payload["plan_digest"] = plan_digest
    if stale_contract:
        cur.execute("set local session_replication_role=replica")
    try:
        slice_plan_id = one(
            cur,
            """insert into ops.engineering_slice_plan
             (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,idempotency_key)
             values (%s,%s,%s,1,%s,%s,%s) returning id""",
            (work_request_id, plan_id, accepted_plan_hash, plan_digest,
             Jsonb(plan_payload), uuid.uuid4()),
        )[0]
    finally:
        if stale_contract:
            cur.execute("set local session_replication_role=origin")
    session_id = one(
        cur,
        """insert into ops.capability_agent_session
             (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,worktree_ref,scope_ref,lease_expires_at)
             values (%s,%s,%s,%s,'engineering:server-admission','slice:'||%s,date_trunc('second',now())+%s::interval) returning id""",
        (work_request_id, codex_id, joe_id, "0" * 40, slice_ref, lease_offset),
    )[0]
    job_id = one(
        cur,
        """insert into ops.job
             (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode,payload)
             values ('engineering-slice',1,%s,now()-interval '10 minutes'+%s*interval '1 microsecond',2,300,'shadow',%s)
             returning id""",
        (f"engineering-claim:{token}", next(_FIXTURE_SCHEDULE_OFFSETS), Jsonb({"work_request": f"WR-ENGINEERING-CLAIM-{token}",
                                                   "slice_ref": slice_ref, "plan_digest": plan_digest, "generation": 1})),
    )[0]
    envelope_digest = "sha256:" + token * 2
    issued_at = one(cur, "select to_char((date_trunc('second',now())+%s::interval) at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')", (issued_offset,))[0]
    expires_at = one(cur, "select to_char(lease_expires_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from ops.capability_agent_session where id=%s", (session_id,))[0]
    envelope_id = uuid.uuid4()
    envelope = {"schema_version": "execution-envelope.v1", "envelope_id": f"env:{envelope_id}", "work_request_id": f"wr:{work_request_id}", "issued_at": issued_at, "expires_at": expires_at,
                "agent_session": {"id": f"session:{session_id}", "lease_expires_at": expires_at},
                "state_binding": {"state_version": 1, "canonical_record_digest": record_digest},
                "plan_revision": {"id": source["accepted_plan"]["plan_ref"], "revision": source["accepted_plan"]["revision"], "digest": source["accepted_plan"]["digest"]}, "phase_binding": {"phase_id": f"phase:{slice_ref}"},
                "request": {"job_ref": f"job:{job_id}", "input_digest": sha("8"), "allowed_actions": ["repository:create-worktree","repository:create-branch","repository:write-declared-scope","repository:run-checks","repository:commit","repository:push-branch","repository:open-pr"]},
                "server_binding": {"authority": {"read_only": False, "capability_profile": "capability:engineering-repository-write"}, "adapter": {"surface": "codex_desktop", "adapter_id": "adapter:codex-desktop"}, "identity": {"agent_principal_id": "agent:codex", "runtime_principal": "runtime:codex"}}}
    if mutate_envelope:
        mutate_envelope(envelope)
    if stale_contract:
        cur.execute("set local session_replication_role=replica")
    try:
        envelope_id = one(
            cur,
            """insert into ops.engineering_execution_envelope
             (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
              state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at)
             values (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s::timestamptz,%s::timestamptz)
             returning id""",
            (envelope_id, job_id, work_request_id, plan_id, slice_plan_id, slice_ref,
             session_id, record_digest, envelope_digest, Jsonb(envelope), issued_at, expires_at),
        )[0]
    finally:
        if stale_contract:
            cur.execute("set local session_replication_role=origin")
    if session_state == "cancelled":
        cur.execute("update ops.capability_agent_session set state='cancelled',cancelled_at=now(),version=version+1 where id=%s", (session_id,))
    elif session_state == "completed":
        cur.execute(
            "update ops.capability_agent_session set state='in_progress',started_at=now(),version=version+1 where id=%s",
            (session_id,),
        )
        cur.execute(
            """update ops.capability_agent_session
                  set state='verification',prepared_at=now(),candidate_kind='built',
                      candidate_evidence=%s,version=version+1
                where id=%s""",
            (Jsonb({"fixture": "completed", "candidate": "prepared"}), session_id),
        )
        cur.execute(
            """update ops.capability_agent_session
                  set state='completed',completed_at=now(),version=version+1
                where id=%s""",
            (session_id,),
        )
    return job_id, envelope_id, session_id, codex_id, plan_digest, slice_ref, envelope_digest


def successor_fixture(cur, prior_job_id, prior_envelope_id):
    """Issue one fresh 29-minute successor for an expired predecessor."""
    # The replacement path explicitly closes only the predecessor's bound
    # server session.  This preserves the capability table's one-open-session
    # invariant without touching any unrelated workflow session.
    cur.execute(
        """update ops.capability_agent_session set state='cancelled',cancelled_at=now(),version=version+1
             where id=(select agent_session_id from ops.engineering_execution_envelope where id=%s and job_id=%s)
               and state not in ('completed','cancelled')""",
        (prior_envelope_id, prior_job_id),
    )
    row = one(
        cur,
        """select e.work_request_id,e.accepted_plan_id,e.slice_plan_id,e.slice_ref,
                         e.state_version,e.canonical_record_digest,e.envelope,
                         j.payload,s.executor_actor_id,s.created_by_actor_id,
                         w.ref
                    from ops.engineering_execution_envelope e
                    join ops.job j on j.id=e.job_id
                    join ops.capability_agent_session s on s.id=e.agent_session_id
                    join ops.work_request w on w.id=e.work_request_id
                   where e.id=%s and e.job_id=%s""",
        (prior_envelope_id, prior_job_id),
    )
    payload = dict(row[7])
    payload["generation"] = int(payload["generation"]) + 1
    token = uuid.uuid4().hex
    successor_job_id = one(
        cur,
        """insert into ops.job
             (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode,payload)
             values ('engineering-slice',1,%s,now()-interval '1 second',2,300,'shadow',%s)
             returning id""",
        (f"engineering-claim-successor:{token}", Jsonb(payload)),
    )[0]
    successor_session_id = one(
        cur,
        """insert into ops.capability_agent_session
             (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,
              worktree_ref,scope_ref,lease_expires_at)
             values (%s,%s,%s,%s,'engineering:server-admission','slice:' || %s,
                     date_trunc('second',now())+interval '29 minutes') returning id""",
        (row[0], row[8], row[9], "0" * 40, row[3]),
    )[0]
    issued_at = one(cur, "select to_char(date_trunc('second',now()) at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')")[0]
    expires_at = one(cur, "select to_char((date_trunc('second',now())+interval '29 minutes') at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')")[0]
    envelope_id = uuid.uuid4()
    envelope = dict(row[6])
    envelope["envelope_id"] = f"env:{envelope_id}"
    envelope["issued_at"] = issued_at
    envelope["expires_at"] = expires_at
    envelope["agent_session"] = {**envelope["agent_session"], "id": f"session:{successor_session_id}", "lease_expires_at": expires_at}
    envelope["request"] = {**envelope["request"], "job_ref": f"job:{successor_job_id}"}
    successor_digest = "sha256:" + token * 2
    successor_envelope_id = one(
        cur,
        """insert into ops.engineering_execution_envelope
             (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,
              agent_session_id,state_version,canonical_record_digest,envelope_digest,
              envelope,issued_at,expires_at,supersedes_envelope_id,supersession_reason)
             values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,%s,
                     'fixture successor for expired predecessor') returning id""",
        (envelope_id, successor_job_id, row[0], row[1], row[2], row[3], successor_session_id,
         row[4], row[5], successor_digest, Jsonb(envelope), issued_at, expires_at, prior_envelope_id),
    )[0]
    return successor_job_id, successor_envelope_id


def assert_static_lock_contract():
    """Pin the two production lock paths to the same lineage/session contract."""
    repo = Path(__file__).resolve().parents[1]
    currentness = (repo / "migrations/0335_engineering_controller_currentness.sql").read_text()
    successor = (repo / "migrations/0319_engineering_envelope_writer_successor.sql").read_text()
    runtime = (repo / "mcp-server/src/engineering-runtime.js").read_text()
    compact_currentness = re.sub(r"\s+", " ", currentness)
    compact_successor = re.sub(r"\s+", " ", successor)
    if not re.search(r"hashtextextended\(\s*'engineering-envelope:' \|\| e\.slice_plan_id::text \|\| ':' \|\| e\.slice_ref", compact_currentness):
        raise RuntimeError("receipt path lost the engineering lineage advisory-lock key")
    if not re.search(r"hashtextextended\(\s*'engineering-envelope:' \|\| new\.slice_plan_id::text \|\| ':' \|\| new\.slice_ref", compact_successor):
        raise RuntimeError("successor path lost the engineering lineage advisory-lock key")
    if "from ops.capability_agent_session where id=e.agent_session_id for update" not in compact_currentness:
        raise RuntimeError("receipt path lost the capability-session row lock")
    if "update ops.capability_agent_session set state='cancelled'" not in runtime:
        raise RuntimeError("admission cancellation path no longer updates the capability session")
    binding = currentness[
        currentness.index("create or replace function ops.engineering_controller_binding("):
        currentness.index("-- These are intentionally private predicate helpers")
    ]
    binding_positions = [
        binding.index("from ops.work_request where id=lineage_work_request for share"),
        binding.index("from ops.job_definition d"),
        binding.index("from ops.job where id=p_job_id for share"),
    ]
    if binding_positions != sorted(binding_positions):
        raise RuntimeError("controller binding lost Work Request -> job definition -> job lock ordering")
    if (
        "create trigger engineering_job_definition_currentness_guard_before_update" not in currentness
        or "Engineering job definition currentness is reserved by a live scoped lease" not in currentness
    ):
        raise RuntimeError("live Engineering job-definition reservation guard is missing")
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


def assert_blocked_by_lock(cur, query, params, blocker_query, blocker_params, dsn, label):
    """Use two owner connections to prove a lock conflict without committing fixtures."""
    with psycopg.connect(dsn) as blocker, blocker.cursor() as blocker_cur:
        blocker_cur.execute(blocker_query, blocker_params)
        cur.execute("savepoint engineering_lock_probe")
        cur.execute("set local lock_timeout='200ms'")
        try:
            cur.execute(query, params)
        except psycopg.errors.LockNotAvailable:
            cur.execute("rollback to savepoint engineering_lock_probe")
            cur.execute("release savepoint engineering_lock_probe")
            blocker.rollback()
            return
        except psycopg.Error as exc:
            cur.execute("rollback to savepoint engineering_lock_probe")
            cur.execute("release savepoint engineering_lock_probe")
            blocker.rollback()
            raise RuntimeError(f"{label} returned unexpected SQLSTATE {exc.sqlstate}") from exc
        cur.execute("rollback to savepoint engineering_lock_probe")
        cur.execute("release savepoint engineering_lock_probe")
        blocker.rollback()
    raise RuntimeError(f"{label} did not block on the expected lock")


def committed_session_id(dsn):
    """A second connection cannot see this gate's rollback-only fixtures."""
    with psycopg.connect(dsn) as probe, probe.cursor() as probe_cur:
        row = probe_cur.execute(
            "select id from ops.capability_agent_session where lease_expires_at is not null order by created_at limit 1"
        ).fetchone()
    return row[0] if row else None


def expect_lower_claim_lease_refusal(cur):
    """The controller owns a 960-second runway; callers may not lower it."""
    cur.execute("savepoint engineering_claim_lease_floor")
    try:
        cur.execute(
            "select * from ops.engineering_claim_slice(%s,1,900)",
            ("engineering-claim-local-too-short",),
        )
    except psycopg.Error as exc:
        detail = str(exc).lower()
        cur.execute("rollback to savepoint engineering_claim_lease_floor")
        cur.execute("release savepoint engineering_claim_lease_floor")
        if "960" not in detail and "lease" not in detail and "runway" not in detail:
            raise RuntimeError(f"lower claim lease returned the wrong refusal: {exc}") from exc
        return
    cur.execute("rollback to savepoint engineering_claim_lease_floor")
    cur.execute("release savepoint engineering_claim_lease_floor")
    raise RuntimeError("engineering claim accepted a caller-supplied 900-second lease")


def expect_job_definition_reservation(cur, job_id, lease_token):
    """A live scoped lease reserves Engineering v1 through adapter launch."""
    cur.execute("reset role")
    cur.execute("savepoint engineering_definition_live_reservation")
    try:
        cur.execute(
            """update ops.job_definition set enabled=not enabled
                 where key='engineering-slice' and version=1"""
        )
    except psycopg.Error as exc:
        detail = str(exc)
        cur.execute("rollback to savepoint engineering_definition_live_reservation")
        cur.execute("release savepoint engineering_definition_live_reservation")
        if "Engineering job definition currentness is reserved by a live scoped lease" not in detail:
            raise RuntimeError(f"live definition update returned the wrong refusal: {exc}") from exc
    else:
        cur.execute("rollback to savepoint engineering_definition_live_reservation")
        cur.execute("release savepoint engineering_definition_live_reservation")
        raise RuntimeError("Engineering v1 definition changed while its scoped lease was live")

    cur.execute("savepoint engineering_definition_release")
    # The supported wrapper intentionally reruns this gate after the
    # concurrency program has committed disposable fixtures. Isolate this
    # claim's release while the outer rollback preserves those rows.
    cur.execute(
        """update ops.job set leased_until=clock_timestamp()-interval '1 second'
             where id<>%s and definition_key='engineering-slice'
               and state='running' and lease_token is not null
               and leased_until>clock_timestamp()""",
        (job_id,),
    )
    set_local_role(cur, RUNTIME_ROLE)
    released = one(
        cur,
        "select ops.engineering_fail_claim(%s,%s,'definition_probe','rollback-only release proof')",
        (job_id, lease_token),
    )[0]
    if released not in ("retry_wait", "dead_lettered"):
        raise RuntimeError(f"scoped definition release returned unexpected state {released}")
    cur.execute("reset role")
    cur.execute(
        """update ops.job_definition set enabled=not enabled
             where key='engineering-slice' and version=1"""
    )
    if cur.rowcount != 1:
        raise RuntimeError("Engineering v1 definition was not mutable after scoped lease release")
    cur.execute("rollback to savepoint engineering_definition_release")
    cur.execute("release savepoint engineering_definition_release")
    set_local_role(cur, RUNTIME_ROLE)


def expect_multi_limit_refusal(cur, job_id):
    """The controller is intentionally single-item; refusal must be atomic."""
    before_job = one(
        cur,
        "select state,attempt,lease_owner,lease_token,leased_until from ops.job where id=%s",
        (job_id,),
    )
    before_attempts = one(cur, "select count(*) from ops.job_attempt where job_id=%s", (job_id,))[0]
    cur.execute("savepoint engineering_claim_limit")
    try:
        cur.execute(
            "select * from ops.engineering_claim_slice(%s,2,960)",
            ("engineering-claim-local-limit-two",),
        )
    except psycopg.Error as exc:
        detail = str(exc).lower()
        cur.execute("rollback to savepoint engineering_claim_limit")
        cur.execute("release savepoint engineering_claim_limit")
        if "limit" not in detail and "one" not in detail:
            raise RuntimeError(f"p_limit=2 returned the wrong refusal: {exc}") from exc
        after_job = one(
            cur,
            "select state,attempt,lease_owner,lease_token,leased_until from ops.job where id=%s",
            (job_id,),
        )
        after_attempts = one(cur, "select count(*) from ops.job_attempt where job_id=%s", (job_id,))[0]
        if after_job != before_job or after_attempts != before_attempts:
            raise RuntimeError("p_limit=2 changed job or attempt state before refusing")
        return
    cur.execute("rollback to savepoint engineering_claim_limit")
    cur.execute("release savepoint engineering_claim_limit")
    raise RuntimeError("engineering claim accepted p_limit=2")


def generic_fixture(cur, label: str, schedule_offset: int, *, mode: str = "shadow", max_attempts: int = 2):
    """Create one isolated non-Engineering row for the shared queue-door positives."""
    definition_key, definition_version = one(
        cur,
        """select key,version from ops.job_definition
             where enabled and key<>'engineering-slice'
               and not (key='calendar-prebrief-projection-joe-daily' and version=1)
             order by key,version limit 1""",
    )
    return one(
        cur,
        """insert into ops.job
             (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,
              timeout_seconds,mode,payload)
             values (%s,%s,%s,clock_timestamp()-interval '1 hour'+%s*interval '1 microsecond',
                     %s,300,%s,'{}'::jsonb)
             returning id""",
        (definition_key, definition_version, f"engineering-generic-gate:{label}:{uuid.uuid4()}",
         schedule_offset, max_attempts, mode),
    )[0]


def expect_engineering_generic_terminal_refusals(cur, job_id, lease_token):
    """Every shared terminal door must refuse Engineering before any mutation."""
    reset_role_snapshot = one(
        cur,
        """select j.state,j.attempt,j.lease_owner,j.lease_token,j.leased_until,
                  (select count(*) from ops.job_receipt r where r.job_id=j.id),
                  (select jsonb_agg(jsonb_build_array(a.state,a.ended_at,a.failure_class,a.detail)
                                    order by a.attempt) from ops.job_attempt a where a.job_id=j.id)
             from ops.job j where j.id=%s""",
        (job_id,),
    )
    set_local_role(cur, RUNTIME_ROLE)
    calls = (
        ("heartbeat_job", "select ops.heartbeat_job(%s,%s,300)", (job_id, lease_token)),
        ("complete_job", "select ops.complete_job(%s,%s,'{}'::jsonb,'gate:forbidden')", (job_id, lease_token)),
        ("fail_job", "select ops.fail_job(%s,%s,'gate_forbidden','must refuse')", (job_id, lease_token)),
        ("timeout_job", "select ops.timeout_job(%s,%s,'must refuse')", (job_id, lease_token)),
    )
    for name, sql, params in calls:
        cur.execute(f"savepoint engineering_generic_{name}")
        try:
            cur.execute(sql, params)
        except psycopg.Error as exc:
            detail = str(exc).lower()
            cur.execute(f"rollback to savepoint engineering_generic_{name}")
            cur.execute(f"release savepoint engineering_generic_{name}")
            if "engineering jobs require scoped controller functions" not in detail:
                raise RuntimeError(f"generic {name} returned the wrong Engineering refusal: {exc}") from exc
        else:
            cur.execute(f"rollback to savepoint engineering_generic_{name}")
            cur.execute(f"release savepoint engineering_generic_{name}")
            raise RuntimeError(f"generic {name} accepted an Engineering lease")
    cur.execute("reset role")
    if one(
        cur,
        """select j.state,j.attempt,j.lease_owner,j.lease_token,j.leased_until,
                  (select count(*) from ops.job_receipt r where r.job_id=j.id),
                  (select jsonb_agg(jsonb_build_array(a.state,a.ended_at,a.failure_class,a.detail)
                                    order by a.attempt) from ops.job_attempt a where a.job_id=j.id)
             from ops.job j where j.id=%s""",
        (job_id,),
    ) != reset_role_snapshot:
        raise RuntimeError("a generic terminal refusal mutated the Engineering job, attempt, or receipt ledger")


def prove_generic_non_engineering_paths(cur):
    """The additive fences must preserve the existing shared queue behavior."""
    cur.execute(
        """update ops.job set next_attempt_at=now()+interval '1 day'
             where definition_key<>'engineering-slice' and state in ('queued','retry_wait')"""
    )
    complete_id = generic_fixture(cur, "complete", 1)
    mode_id = generic_fixture(cur, "mode-fail", 2, mode="replay", max_attempts=1)
    timeout_id = generic_fixture(cur, "timeout", 3, max_attempts=1)
    set_local_role(cur, RUNTIME_ROLE)
    complete_claim = one(cur, "select job_id,lease_token from ops.claim_job(%s,1,300)",
                         ("engineering-generic-complete",))
    if complete_claim[0] != complete_id:
        raise RuntimeError("generic claim_job did not preserve the non-Engineering path")
    if one(cur, "select ops.heartbeat_job(%s,%s,300)", complete_claim)[0] is not True:
        raise RuntimeError("generic heartbeat did not preserve the non-Engineering path")
    if one(cur, "select ops.complete_job(%s,%s,'{}'::jsonb,'gate:generic-complete')", complete_claim)[0] is not True:
        raise RuntimeError("generic completion did not preserve the non-Engineering path")
    mode_claim = one(cur, "select job_id,lease_token from ops.claim_job_mode(%s,'replay',1,300)",
                     ("engineering-generic-mode",))
    if mode_claim[0] != mode_id:
        raise RuntimeError("generic claim_job_mode did not preserve the non-Engineering path")
    if one(cur, "select ops.fail_job(%s,%s,'gate_failure','expected generic failure')", mode_claim)[0] != "dead_lettered":
        raise RuntimeError("generic fail_job did not preserve the non-Engineering path")
    timeout_claim = one(cur, "select job_id,lease_token from ops.claim_job(%s,1,300)",
                        ("engineering-generic-timeout",))
    if timeout_claim[0] != timeout_id:
        raise RuntimeError("generic timeout fixture was not claimed")
    if one(cur, "select ops.timeout_job(%s,%s,'expected generic timeout')", timeout_claim)[0] != "dead_lettered":
        raise RuntimeError("generic timeout_job did not preserve the non-Engineering path")
    cur.execute("reset role")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        assert_static_lock_contract()
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, RUNTIME_ROLE)
            job_id, envelope_id, session_id, codex_id, plan_digest, slice_ref, envelope_digest = fixture(cur)
            overlong_job, overlong_envelope, *_ = fixture(cur, lease_offset="31 minutes")
            low_runway_job, low_runway_envelope, *_ = fixture(cur, lease_offset="10 minutes")
            predecessor_job, predecessor_envelope, *_ = fixture(cur, lease_offset="-1 hour", issued_offset="-2 hours")
            successor_job, successor_envelope = successor_fixture(cur, predecessor_job, predecessor_envelope)
            stale_job, stale_envelope, *_ = fixture(cur, stale_contract=True)
            invalid_fixtures = [
                fixture(cur, lambda e: e.__setitem__("expires_at", "not-a-time")),
                fixture(cur, lambda e: e.__setitem__("envelope_id", "env:wrong")),
                fixture(cur, lambda e: e["request"].__setitem__("job_ref", "job:wrong")),
                fixture(cur, lambda e: e["plan_revision"].__setitem__("digest", sha("0"))),
                fixture(cur, lambda e: e["phase_binding"].__setitem__("phase_id", "phase:wrong")),
                fixture(cur, lambda e: e["agent_session"].__setitem__("lease_expires_at", "2000-01-01T00:00:00Z")),
                fixture(cur, lambda e: e.__setitem__("issued_at", "2000-01-01T00:00:00Z")),
                fixture(cur, lambda e: e.__setitem__("expires_at", "2000-01-01T00:00:00Z")),
                fixture(cur, lambda e: e["server_binding"]["adapter"].pop("adapter_id")),
                fixture(cur, lambda e: e["server_binding"]["adapter"].__setitem__("adapter_id", "adapter:wrong")),
                fixture(cur, lambda e: e["server_binding"]["authority"].__setitem__("read_only", True)),
                fixture(cur, session_state="cancelled"),
                fixture(cur, session_state="completed"),
            ]
            invalid_jobs = [row[0] for row in invalid_fixtures] + [overlong_job, low_runway_job, stale_job]
            for invalid_job, invalid_envelope, *_ in invalid_fixtures:
                verdict = one(cur, "select ops.engineering_envelope_currentness(%s,%s)", (invalid_envelope, invalid_job))[0]
                if verdict.get("eligible"):
                    return fail(f"invalid currentness fixture was eligible: {verdict}")
            if one(cur, "select (expires_at-issued_at)>interval '30 minutes' from ops.engineering_execution_envelope where id=%s", (overlong_envelope,))[0] is not True:
                return fail("overlong fixture did not exceed the 30-minute runtime envelope bound")
            if one(cur, "select count(*) from ops.job where id=any(%s) and state='queued' and attempt=0", (invalid_jobs,))[0] != len(invalid_jobs):
                return fail("invalid currentness fixtures were not left unclaimed before the happy-path claim")
            if one(cur, "select ops.engineering_envelope_currentness(%s,%s)->>'reason'", (overlong_envelope, overlong_job))[0] != "envelope_expired_or_mismatched":
                return fail("overlong fixture returned the wrong currentness reason")
            low_runway = one(cur, "select ops.engineering_envelope_currentness(%s,%s)", (low_runway_envelope, low_runway_job))[0]
            if low_runway.get("dispatch_runway_sufficient") is not False:
                return fail(f"low-runway future fixture was not fenced before claim: {low_runway}")
            stale_verdict = one(
                cur, "select ops.engineering_envelope_currentness(%s,%s)",
                (stale_envelope, stale_job),
            )[0]
            if stale_verdict != {
                "eligible": False,
                "dispatch_runway_sufficient": False,
                "execution_authorized": False,
                "reason": "accepted_plan_retired",
            }:
                return fail(f"stale exact plan was not permanently fenced: {stale_verdict}")
            assert_non_codex_admission_refused(cur)
            if one(cur, "select ops.engineering_envelope_currentness(%s,%s)->>'reason'", (invalid_fixtures[-1][1], invalid_fixtures[-1][0]))[0] != "agent_session_not_active":
                return fail("completed-session fixture returned the wrong currentness reason")
            lock_probe_session = committed_session_id(dsn)
            if lock_probe_session:
                assert_blocked_by_lock(
                    cur,
                    "select id from ops.capability_agent_session where id=%s for update",
                    (lock_probe_session,),
                    "select id from ops.capability_agent_session where id=%s for update",
                    (lock_probe_session,),
                    dsn,
                    "capability-session row lock",
                )
            else:
                print("engineering claim local acceptance: session row lock probe skipped; disposable schema has no committed session row (static receipt/cancellation lock assertions still ran)")
            # Envelope-insert triggers already hold their real lineage locks in
            # this rollback-only transaction.  Use an otherwise-unclaimed key
            # to prove two-connection contention; static assertions above pin
            # receipt and successor to this exact lineage-key construction.
            lineage_key = f"engineering-envelope:rollback-lock-probe-{uuid.uuid4()}:slice:claim-fixture"
            assert_blocked_by_lock(
                cur,
                "select pg_advisory_xact_lock(hashtextextended(%s,0))",
                (lineage_key,),
                "select pg_advisory_xact_lock(hashtextextended(%s,0))",
                (lineage_key,),
                dsn,
                "engineering lineage advisory lock",
            )
            cur.execute("savepoint immutable_lease")
            try:
                cur.execute("update ops.capability_agent_session set state='in_progress',started_at=now(),version=version+1,lease_expires_at=lease_expires_at+interval '1 minute' where id=%s", (session_id,))
            except psycopg.Error as exc:
                if "capability agent session lease is immutable" not in str(exc):
                    raise
                cur.execute("rollback to savepoint immutable_lease")
                cur.execute("release savepoint immutable_lease")
            else:
                cur.execute("release savepoint immutable_lease")
                return fail("capability session lease update was accepted")
            currentness = one(cur, "select ops.engineering_envelope_currentness(%s,%s)", (envelope_id, job_id))[0]
            if not currentness.get("eligible"):
                return fail(f"fresh currentness fixture was rejected: {currentness}")
            expected_lease = one(cur, "select to_char(lease_expires_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from ops.capability_agent_session where id=%s", (session_id,))[0]
            private_functions = [
                "ops.engineering_envelope_is_executable(uuid,uuid)",
                "ops.engineering_safe_timestamptz(text)",
                "ops.capability_agent_session_lease_immutable()",
                "ops.engineering_receipt_exact_object(jsonb,text[])",
                "ops.engineering_receipt_identifier_array(jsonb)",
                "ops.engineering_receipt_identifier_sets_equal(jsonb,jsonb)",
                "ops.engineering_receipt_evidence_array(jsonb)",
                "ops.guard_engineering_reviewer_fact_insert()",
                "ops.guard_engineering_envelope_supersession()",
                "ops.guard_engineering_session_terminalization()",
                "ops.guard_engineering_job_definition_currentness_update()",
                "ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)",
            ]
            for role in ("public", "carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
                for function in private_functions:
                    if one(cur, "select has_function_privilege(%s,%s::regprocedure,'EXECUTE')", (role, function))[0]:
                        return fail(f"{function} is directly executable by {role}")
            for role in ("public", "carr_jobs", "carr_authority"):
                if one(cur, "select has_function_privilege(%s,'ops.engineering_envelope_currentness(uuid,uuid)'::regprocedure,'EXECUTE')", (role,))[0]:
                    return fail(f"engineering_envelope_currentness is directly executable by {role}")
            for role in ("carr_reader", "carr_writer"):
                if not one(cur, "select has_function_privilege(%s,'ops.engineering_envelope_currentness(uuid,uuid)'::regprocedure,'EXECUTE')", (role,))[0]:
                    return fail(f"engineering_envelope_currentness is unavailable to intended reader role {role}")
            if one(cur, "select to_regprocedure('ops.engineering_controller_binding(uuid,uuid)')")[0] is not None:
                return fail("obsolete two-argument controller binding still exists")
            if one(cur, "select to_regprocedure('ops.engineering_envelope_is_executable(uuid,uuid,integer)')")[0] is not None:
                return fail("obsolete three-argument executable predicate still exists")
            if one(cur, "select count(*) from pg_proc where pronamespace='ops'::regnamespace and proname='engineering_controller_binding'")[0] != 1:
                return fail("controller binding overload catalog is not exact")
            if one(cur, "select count(*) from pg_proc where pronamespace='ops'::regnamespace and proname='engineering_envelope_is_executable'")[0] != 1:
                return fail("executable predicate overload catalog is not exact")
            scoped_functions = [
                "ops.engineering_claim_slice(text,integer,integer)",
                "ops.engineering_controller_binding(uuid,uuid,uuid)",
                "ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid)",
                "ops.engineering_fail_claim(uuid,uuid,text,text)",
                "ops.engineering_retire_permanently_ineligible_jobs()",
            ]
            for function in scoped_functions:
                for role in ("public", "carr_reader", "carr_writer", "carr_authority"):
                    if one(cur, "select has_function_privilege(%s,%s::regprocedure,'EXECUTE')", (role, function))[0]:
                        return fail(f"{function} is directly executable by forbidden role {role}")
                if not one(cur, "select has_function_privilege('carr_jobs',%s::regprocedure,'EXECUTE')", (function,))[0]:
                    return fail(f"{function} is unavailable to carr_jobs")
            cur.execute(
                """update ops.job set next_attempt_at=now()+interval '1 day'
                     where definition_key<>'engineering-slice' and state in ('queued','retry_wait')"""
            )
            # The supported wrapper reruns this gate after DB programs that may
            # commit their own eligible Engineering fixtures. Isolate both
            # intended claims from those committed rows while leaving this
            # gate's invalid-currentness fixtures queued for the assertions
            # below. This transaction is rollback-only and does not change
            # runtime privileges or production queue ordering.
            cur.execute(
                """update ops.job set next_attempt_at=now()+interval '1 day'
                     where definition_key='engineering-slice'
                       and id<>all(%s)
                       and state in ('queued','retry_wait')""",
                ([job_id, successor_job],),
            )
            cur.execute(
                "update ops.job set scheduled_for=now()-interval '100 years' where id=%s",
                (job_id,),
            )
            set_local_role(cur, RUNTIME_ROLE)
            if one(cur, "select has_table_privilege(current_user,'ops.job','UPDATE')")[0]:
                return fail("carr_jobs gained direct UPDATE on ops.job")
            if one(cur, "select has_table_privilege(current_user,'ops.job_attempt','INSERT')")[0]:
                return fail("carr_jobs gained direct INSERT on ops.job_attempt")
            if not one(cur, "select has_function_privilege(current_user,'ops.engineering_claim_slice(text,integer,integer)'::regprocedure,'EXECUTE')")[0]:
                return fail("carr_jobs cannot execute the scoped Engineering claim")
            if not one(cur, "select has_function_privilege(current_user,'ops.engineering_controller_binding(uuid,uuid,uuid)'::regprocedure,'EXECUTE')")[0]:
                return fail("carr_jobs cannot read the scoped controller binding")
            if cur.execute("select * from ops.claim_job(%s,1,300)", ("engineering-generic-negative",)).fetchall():
                return fail("generic claim_job consumed an Engineering job")
            if cur.execute("select * from ops.claim_job_mode(%s,'shadow',1,300)", ("engineering-generic-mode-negative",)).fetchall():
                return fail("generic claim_job_mode consumed an Engineering job")
            cur.execute("select ops.reap_expired_jobs()")
            cur.execute("select ops.engineering_retire_permanently_ineligible_jobs()")
            expect_lower_claim_lease_refusal(cur)
            expect_multi_limit_refusal(cur, job_id)

            claimed = one(
                cur,
                "select job_id,lease_token,definition_key,attempt from ops.engineering_claim_slice(%s,1,960)",
                ("engineering-claim-local-acceptance",),
            )
            if claimed[0] != job_id or not isinstance(claimed[1], uuid.UUID) or claimed[2:] != ("engineering-slice", 1):
                return fail(f"claim did not return the exact eligible Engineering job and lease: {claimed!r}")
            if one(cur, "select state,attempt,lease_owner,lease_token=%s,leased_until>now() from ops.job where id=%s", (claimed[1], job_id)) != (
                    "running", 1, "engineering-claim-local-acceptance", True, True):
                return fail("claim did not persist one exact running job lease")
            if not one(cur, "select leased_until>=clock_timestamp()+interval '958 seconds' from ops.job where id=%s", (job_id,))[0]:
                return fail("claim lease was not anchored to the post-lock clock with the full 960-second runway")
            if one(cur, "select count(*),min(state),min(lease_owner),bool_and(lease_token=%s) from ops.job_attempt where job_id=%s", (claimed[1], job_id)) != (
                    1, "running", "engineering-claim-local-acceptance", True):
                return fail("claim did not persist one exact running attempt")
            cur.execute("reset role")
            expect_engineering_generic_terminal_refusals(cur, job_id, claimed[1])
            set_local_role(cur, RUNTIME_ROLE)
            if one(cur, "select ops.engineering_controller_binding(%s,%s,%s)",
                   (envelope_id, job_id, uuid.uuid4()))[0] is not None:
                return fail("controller binding accepted the wrong lease token")
            expected_job_lease = one(cur, "select to_char(leased_until at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from ops.job where id=%s",
                                     (job_id,))[0]
            binding = one(cur, "select ops.engineering_controller_binding(%s,%s,%s)",
                          (envelope_id, job_id, claimed[1]))[0]
            if binding is None:
                return fail("controller binding refused the fresh exact claim")
            if binding.get("envelope_id") != str(envelope_id) or binding.get("envelope_digest") != envelope_digest \
                    or binding.get("slice_ref") != slice_ref or binding.get("plan_digest") != plan_digest \
                    or binding.get("executor_actor") != {"id": str(codex_id), "slug": "codex"} \
                    or binding.get("agent_session_id") != str(session_id) or binding.get("agent_session_lease_expires_at") != expected_lease \
                    or binding.get("job_lease_expires_at") != expected_job_lease:
                return fail("controller binding did not remain exact after claim")
            expect_job_definition_reservation(cur, job_id, claimed[1])
            predecessor_state = one(cur, "select state,last_failure_class from ops.job where id=%s", (predecessor_job,))
            if predecessor_state != ("dead_lettered", "engineering_superseded_predecessor"):
                return fail(f"superseded predecessor was not retired audibly: {predecessor_state}")
            dead_letter = one(cur, "select kind,evidence->>'reason' from ops.job_receipt where job_id=%s order by id desc limit 1", (predecessor_job,))
            if dead_letter != ("dead_letter", "engineering_superseded_predecessor"):
                return fail(f"superseded predecessor lacks its dead-letter receipt: {dead_letter}")
            successor_claimed = cur.execute(
                "select job_id,lease_token from ops.engineering_claim_slice(%s,1,960)",
                ("engineering-claim-local-successor",),
            ).fetchone()
            if successor_claimed is None:
                successor_currentness = one(
                    cur, "select ops.engineering_envelope_currentness(%s,%s)",
                    (successor_envelope, successor_job),
                )[0]
                successor_job_state = one(
                    cur, "select state,attempt,max_attempts,next_attempt_at<=now() from ops.job where id=%s",
                    (successor_job,),
                )
                return fail(f"fresh successor had no claim candidate: currentness={successor_currentness}, job={successor_job_state}")
            if successor_claimed[0] != successor_job or not isinstance(successor_claimed[1], uuid.UUID):
                return fail(f"fresh successor was not claimable after predecessor retirement: {successor_claimed[0]}")
            if one(cur, "select state,attempt from ops.job where id=%s", (successor_job,)) != ("running", 1):
                return fail("fresh successor did not persist its running claim")
            if one(cur, "select count(*) from ops.job where id=any(%s) and state='queued' and attempt=0", (invalid_jobs,))[0] != len(invalid_jobs):
                return fail("invalid or low-runway rows were consumed after the valid claims")
            cur.execute("reset role")
            prove_generic_non_engineering_paths(cur)
    except Exception as exc:  # noqa: BLE001 - DB gates report exact refusal details
        return fail(str(exc))
    print("engineering claim local acceptance passed: scoped lease, attempt, and binding are exact and rollback-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
