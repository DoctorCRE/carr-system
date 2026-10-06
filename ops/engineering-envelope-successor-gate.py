#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only proof that carr_writer can replace an expired envelope.

The serving role must create an immutable successor without UPDATE permission.
This gate builds the minimum FK-valid fixture as owner, then inserts the expired
predecessor and successor as carr_writer.  All rows and temporary membership
changes are rolled back.
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


RUNTIME_ROLE = "carr_writer"
TABLE = "ops.engineering_execution_envelope"


def fail(message: str) -> int:
    print(f"engineering-envelope-successor-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, query: str, params: tuple = ()):  # pragma: no cover - exercised through the DB gate
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError("required fixture row was not returned")
    return row


def sha(char: str) -> str:
    return "sha256:" + char * 64


def fixture(cur):
    token = uuid.uuid4().hex
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    codex_id = one(cur, "select id from actor where slug='codex' and active and kind='automation'")[0]
    document_id = one(
        cur,
        """insert into doctrine_document(slug,title,content_class,visibility,created_by)
             values (%s,'Engineering envelope fixture','reference','shared',%s) returning id""",
        (f"engineering-envelope-{token}", joe_id),
    )[0]
    section_id = one(
        cur,
        """insert into doctrine_section(document_id,section_key,title,ordinal,status,current_version)
             values (%s,'fixture','Engineering envelope fixture',1,'active',1) returning id""",
        (document_id,),
    )[0]
    revision_id = one(
        cur,
        """insert into doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash,commit_message)
             values (%s,1,%s,%s,'Engineering envelope fixture',%s,'fixture') returning id""",
        (section_id, joe_id, Jsonb({"text": "Engineering envelope fixture"}), "a" * 64),
    )[0]
    work_request_id = one(
        cur,
        """insert into ops.work_request
             (ref,state,title,requester_actor,owner_actor,shape_disposition,
              shape_fixed_surface_ref,shape_rationale,shape_decided_by_actor_id,shape_decided_at)
             values (%s,'ready','Engineering envelope fixture','joe','joe','not_required',
                     'fixture:engineering-envelope','fixture currentness acceptance',%s,now())
             returning id""",
        (f"WR-ENGINEERING-{token}", joe_id),
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
         Jsonb([]), Jsonb({}), sha("c"), f"PLAN-{token[:12]}-v1"),
    )[0]
    one(
        cur,
        """insert into ops.sourced_work_request_plan_acceptance_receipt
             (work_request_id,plan_id,idempotency_key,base_version,plan_hash,
              accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
             values (%s,%s,%s,1,%s,%s,1,'fixture:engineering-envelope','fixture acceptance')
             returning id""",
        (work_request_id, plan_id, uuid.uuid4(), sha("c"), joe_id),
    )
    source = one(
        cur,
        "select ops.engineering_admission_source(%s)",
        (f"WR-ENGINEERING-{token}",),
    )[0]
    record_digest = source["work_request"]["canonical_record_digest"]
    plan_payload = {
        "accepted_plan_revision": {
            "digest": source["accepted_plan"]["digest"],
            "id": source["accepted_plan"]["plan_ref"],
            "revision": source["accepted_plan"]["revision"],
        },
        "plan_digest": sha("d"),
        "schema_version": "engineering-slice-plan.v1",
        "slices": [{"slice_ref": "slice:fixture"}],
        "work_request": {
            "canonical_record_digest": record_digest,
            "id": source["work_request"]["id"],
            "state_version": source["work_request"]["version"],
        },
    }
    slice_plan_id = one(
        cur,
        """insert into ops.engineering_slice_plan
             (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,idempotency_key)
             values (%s,%s,%s,1,%s,%s,%s) returning id""",
        (work_request_id, plan_id, sha("c"), sha("d"), Jsonb(plan_payload), uuid.uuid4()),
    )[0]
    old_session_id = one(
        cur,
        """insert into ops.capability_agent_session
             (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,worktree_ref)
             values (%s,%s,%s,%s,'fixture-old') returning id""",
        (work_request_id, codex_id, joe_id, "e" * 40),
    )[0]
    cur.execute(
        "update ops.capability_agent_session set state='cancelled', cancelled_at=now() where id=%s",
        (old_session_id,),
    )
    new_session_id = one(
        cur,
        """insert into ops.capability_agent_session
             (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,worktree_ref)
             values (%s,%s,%s,%s,'fixture-new') returning id""",
        (work_request_id, codex_id, joe_id, "f" * 40),
    )[0]
    old_job_id = one(
        cur,
        """insert into ops.job(definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode)
             values ('engineering-slice',1,%s,now(),1,60,'shadow') returning id""",
        (f"engineering-envelope-old:{token}",),
    )[0]
    new_job_id = one(
        cur,
        """insert into ops.job(definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode)
             values ('engineering-slice',1,%s,now()+interval '1 minute',1,60,'shadow') returning id""",
        (f"engineering-envelope-new:{token}",),
    )[0]
    return work_request_id, plan_id, slice_plan_id, record_digest, old_session_id, new_session_id, old_job_id, new_job_id


def insert_envelope(cur, *, job_id, work_request_id, plan_id, slice_plan_id, session_id, record_digest, digest, expires_sql, supersedes=None):
    envelope = Jsonb({
        "work_request_id": f"wr:{work_request_id}",
        "state_binding": {"state_version": 1, "canonical_record_digest": record_digest},
    })
    return one(
        cur,
        f"""insert into {TABLE}
             (job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
              state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at,
              supersedes_envelope_id,supersession_reason)
             values (%s,%s,%s,%s,'slice:fixture',%s,1,%s,%s,%s,now()-interval '2 hours',{expires_sql},%s,%s)
             returning id""",
        (job_id, work_request_id, plan_id, slice_plan_id, session_id, record_digest, digest, envelope,
         supersedes, "expired fixture replacement" if supersedes else None),
    )[0]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, RUNTIME_ROLE)
            work_request_id, plan_id, slice_plan_id, record_digest, old_session_id, new_session_id, old_job_id, new_job_id = fixture(cur)
            set_local_role(cur, RUNTIME_ROLE)
            if one(cur, "select has_table_privilege(current_user,%s,'UPDATE')", (TABLE,))[0]:
                return fail("carr_writer gained UPDATE on append-only engineering envelopes")
            predecessor_id = insert_envelope(
                cur, job_id=old_job_id, work_request_id=work_request_id, plan_id=plan_id, record_digest=record_digest,
                slice_plan_id=slice_plan_id, session_id=old_session_id, digest=sha("1"),
                expires_sql="now()-interval '1 hour'",
            )
            successor_id = insert_envelope(
                cur, job_id=new_job_id, work_request_id=work_request_id, plan_id=plan_id, record_digest=record_digest,
                slice_plan_id=slice_plan_id, session_id=new_session_id, digest=sha("2"),
                expires_sql="now()+interval '1 hour'", supersedes=predecessor_id,
            )
            if successor_id == predecessor_id:
                return fail("expired successor insert did not create a distinct immutable envelope")
    except Exception as exc:  # noqa: BLE001 - acceptance gates report their refusal
        return fail(str(exc))
    print("engineering-envelope-successor-gate passed: carr_writer replaced an expired envelope without UPDATE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
