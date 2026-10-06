#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only behavioral acceptance for SIEP B0's sole-ledger DAG."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid

from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"siep-program-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, query: str, params: tuple = ()):
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError("required row was not returned")
    return row


def canonical_json(value):
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


def superseding_envelope_probe(cur, predecessor_job_id, predecessor_envelope_id):
    """Add one rollback-only successor so SIEP must reject the predecessor."""
    row = one(
        cur,
        """select e.work_request_id,e.accepted_plan_id,e.slice_plan_id,e.slice_ref,
                  e.state_version,e.canonical_record_digest,e.envelope,
                  s.executor_actor_id,s.created_by_actor_id,s.source_commit_sha,
                  s.worktree_ref,s.scope_ref
             from ops.engineering_execution_envelope e
             join ops.capability_agent_session s on s.id=e.agent_session_id
            where e.id=%s and e.job_id=%s""",
        (predecessor_envelope_id, predecessor_job_id),
    )
    successor_job_id = one(
        cur,
        """insert into ops.job
             (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode,payload)
             values ('engineering-slice',1,%s,now(),2,300,'shadow',%s) returning id""",
        (f"siep-supersession-probe:{uuid.uuid4()}", Jsonb({"probe": "superseded-siep-leaf"})),
    )[0]
    cur.execute(
        """update ops.capability_agent_session
              set state='cancelled',cancelled_at=now(),version=version+1
            where id=(select agent_session_id
                        from ops.engineering_execution_envelope where id=%s)""",
        (predecessor_envelope_id,),
    )
    successor_session_id = one(
        cur,
        """insert into ops.capability_agent_session
             (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,
              worktree_ref,scope_ref,lease_expires_at)
             values (%s,%s,%s,%s,%s,%s,now()+interval '1 hour') returning id""",
        (row[0], row[7], row[8], row[9], row[10], row[11]),
    )[0]
    successor_envelope_id = uuid.uuid4()
    envelope = dict(row[6])
    envelope["envelope_id"] = f"env:{successor_envelope_id}"
    envelope["agent_session"] = {
        **envelope.get("agent_session", {}),
        "id": f"session:{successor_session_id}",
    }
    envelope["request"] = {
        **envelope.get("request", {}),
        "job_ref": f"job:{successor_job_id}",
    }
    cur.execute(
        "alter table ops.engineering_execution_envelope "
        "disable trigger engineering_envelope_supersession_guard"
    )
    try:
        cur.execute(
            """insert into ops.engineering_execution_envelope
                 (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,
                  agent_session_id,state_version,canonical_record_digest,envelope_digest,
                  envelope,issued_at,expires_at,supersedes_envelope_id,supersession_reason)
                 values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now()+interval '1 hour',
                         %s,'rollback-only SIEP supersession probe')""",
            (
                successor_envelope_id, successor_job_id, row[0], row[1], row[2], row[3],
                successor_session_id, row[4], row[5], "sha256:" + uuid.uuid4().hex * 2,
                Jsonb(envelope), predecessor_envelope_id,
            ),
        )
    finally:
        cur.execute(
            "alter table ops.engineering_execution_envelope "
            "enable trigger engineering_envelope_supersession_guard"
        )
    return successor_job_id, successor_envelope_id


def refusal(cur, query: str, params: tuple, fragment: str) -> None:
    savepoint = "siep_refusal_" + uuid.uuid4().hex[:10]
    cur.execute(f"savepoint {savepoint}")
    try:
        cur.execute(query, params)
    except Exception as exc:  # noqa: BLE001 - exact DB refusal is the assertion
        cur.execute(f"rollback to savepoint {savepoint}")
        cur.execute(f"release savepoint {savepoint}")
        if fragment not in str(exc):
            raise RuntimeError(f"expected refusal containing {fragment!r}, got {exc}") from exc
        return
    cur.execute(f"rollback to savepoint {savepoint}")
    cur.execute(f"release savepoint {savepoint}")
    raise RuntimeError(f"expected refusal containing {fragment!r}")


PACKAGES = {
    "00", "B0", "01", "02", "03", "04", "05", "06A", "06B",
    *{str(value) for value in range(10, 24)}, "24A", "25", "26",
    *{str(value) for value in range(30, 38)}, "24B", *{str(value) for value in range(40, 45)},
}

ALIASES = {**{f"SCAC-{index:02d}": key for index, key in enumerate(
    [*map(str, range(10, 24)), "24A", "25", "26"]
)}, **{f"MPE-17{letter}": str(30 + index) for index, letter in enumerate("ABCDEFGH")}}

EDGES = {
    ("B0", "00"), ("01", "B0"), ("02", "01"), ("06A", "B0"), ("10", "B0"),
    *((str(value), str(value - 1)) for value in range(11, 23)), ("26", "25"),
    *((str(value), str(value - 1)) for value in range(31, 38)), ("41", "40"),
    *(("40", dependency) for dependency in ("05", "06B", "24A", "24B", "25", "26", "37")),
    *(("03", dependency) for dependency in ("02", "12", "15", "17", "20")),
    *(("04", dependency) for dependency in ("03", "11", "18")),
    *(("05", dependency) for dependency in ("04", "17", "18", "21", "23")),
    *(("23", dependency) for dependency in ("06A", "12", "17", "18", "19", "20", "21", "22")),
    *(("06B", dependency) for dependency in ("06A", "04", "23")),
    *(("24A", dependency) for dependency in ("14", *map(str, range(16, 24)))),
    ("25", "24A"), ("30", "15"), ("30", "23"),
    *(("24B", dependency) for dependency in ("24A", *map(str, range(30, 38)))),
    *(("42", dependency) for dependency in ("05", "06B", "24A", "24B", "25", "26", "37", "41")),
    ("43", "42"), ("44", "43"),
}


def set_siep_fixture_state(cur, work_request_id, state):
    """Model historical state while restoring the real transition trigger."""
    cur.execute(
        "alter table ops.work_request "
        "disable trigger siep_program_transition_guard_before_update"
    )
    try:
        cur.execute(
            "update ops.work_request set state=%s where id=%s",
            (state, work_request_id),
        )
    finally:
        cur.execute(
            "alter table ops.work_request "
            "enable trigger siep_program_transition_guard_before_update"
        )
    if one(
        cur,
        """select tgenabled from pg_trigger
             where tgrelid='ops.work_request'::regclass
               and tgname='siep_program_transition_guard_before_update'""",
    )[0] != "O":
        raise RuntimeError("SIEP transition guard was not restored after historical fixture")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            actual_packages = {row[0] for row in cur.execute("select package_key from ops.siep_package_contract")}
            actual_aliases = dict(cur.execute("select alias_key,package_key from ops.siep_component_alias"))
            actual_edges = set(cur.execute("select package_key,depends_on_package_key from ops.siep_program_dependency"))
            if actual_packages != PACKAGES:
                raise RuntimeError(f"package set drift: missing={PACKAGES-actual_packages}, extra={actual_packages-PACKAGES}")
            if actual_aliases != ALIASES:
                raise RuntimeError("component alias mapping differs from the reviewed manifest")
            if actual_edges != EDGES:
                raise RuntimeError(f"DAG drift: missing={EDGES-actual_edges}, extra={actual_edges-EDGES}")
            if len(EDGES) != 88:
                raise RuntimeError(f"gate fixture expected 88 exact edges, got {len(EDGES)}")
            if one(cur, "select ops.siep_resolve_package('06') is null,ops.siep_resolve_package('24') is null") != (True, True):
                raise RuntimeError("aggregate labels 06 or 24 resolved as packages")
            manifest = one(cur, "select ops.siep_manifest_digest()")[0]
            if not isinstance(manifest, str) or not manifest.startswith("sha256:") or len(manifest) != 71:
                raise RuntimeError("manifest digest was not canonical SHA-256")

            wr00 = one(cur, "select id from ops.work_request where ref='WR-SIEP-00'")[0]
            joe_id = one(cur, "select id from actor where slug='joe' and active")[0]
            raw_flag_id = one(
                cur,
                """insert into record_flag(subject_type,subject_id,kind,value,source,created_by)
                     values ('repo',%s,'siep_source_evidence',%s,'local-pg:forgery',%s) returning id""",
                (wr00, Jsonb({"program": "SIEP v1", "node": "SIEP-00", "status": "pass",
                              "commit_sha": "f" * 40}), joe_id),
            )[0]
            raw_flag_digest = one(
                cur,
                """select 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(to_jsonb(r)),'sha256'),'hex')
                     from record_flag r where id=%s""",
                (raw_flag_id,),
            )[0]
            grant_settable_runtime_roles(cur, "carr_writer")
            set_local_role(cur, "carr_writer")
            if not one(cur, "select has_function_privilege(current_user,'ops.siep_read_program()'::regprocedure,'EXECUTE')")[0]:
                raise RuntimeError("carr_writer cannot read the typed SIEP projection")
            if one(cur, "select jsonb_array_length(ops.siep_read_program()->'packages')")[0] != 40:
                raise RuntimeError("typed program projection did not return 40 packages")
            cur.execute("update ops.work_request set title='forged' where ref='WR-SIEP-00'")
            if cur.rowcount != 0:
                raise RuntimeError("raw carr_writer update reached a SIEP Work Request")

            b0_lock_key = uuid.uuid4()
            b0_lock = one(cur, "select ops.siep_acquire_lane_lock('B0','session:b0-gate',300,%s)", (b0_lock_key,))[0]
            refusal(cur, "select ops.siep_claim_package('B0','session:b0-gate',%s,%s)",
                    (uuid.UUID(b0_lock["lease_token"]), uuid.uuid4()), "unresolved dependencies")
            b0_release_key = uuid.uuid4()
            if not one(cur, "select ops.siep_release_lane_lock('B0','session:b0-gate',%s,%s)",
                       (uuid.UUID(b0_lock["lease_token"]), b0_release_key))[0]:
                raise RuntimeError("ready B0 reservation did not release")
            if not one(cur, "select ops.siep_release_lane_lock('B0','session:b0-gate',%s,%s)",
                       (uuid.UUID(b0_lock["lease_token"]), b0_release_key))[0]:
                raise RuntimeError("lane release exact replay changed its result")

            lock_key = uuid.uuid4()
            lock = one(cur, "select ops.siep_acquire_lane_lock('00','session:siep00-gate',300,%s)", (lock_key,))[0]
            lease_token = uuid.UUID(lock["lease_token"])
            public_acquire = one(
                cur,
                """select idempotency_key,new_value ? 'session_ref',new_value->>'session_digest'
                     from public.event where verb='siep-acquire-lane-lock' and subject_id=%s
                     order by occurred_at desc limit 1""",
                (wr00,),
            )
            if public_acquire[0] is not None or public_acquire[1] or not public_acquire[2].startswith("sha256:"):
                raise RuntimeError("lane acquisition leaked replay authority or raw session identity")
            replay = one(cur, "select ops.siep_acquire_lane_lock('00','session:siep00-gate',300,%s)", (lock_key,))[0]
            if not replay["replayed"] or "lease_token" in replay or replay["lease_digest"] != lock["lease_digest"]:
                raise RuntimeError("lane-lock replay was not a safe no-op")
            cur.execute("reset role")
            stored_acquire = one(cur, "select result from ops.siep_command_receipt where idempotency_key=%s", (lock_key,))[0]
            if "lease_token" in stored_acquire:
                raise RuntimeError("lane-lock command receipt persisted a raw lease token")
            set_local_role(cur, "carr_writer")
            refusal(cur, "select ops.siep_acquire_lane_lock('00','session:changed',300,%s)", (lock_key,), "inputs changed")
            claim_key = uuid.uuid4()
            cur.execute(
                """insert into public.event
                     (occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause,agent_rationale,idempotency_key)
                     values (now(),%s,'siep-claim-package','work_request',%s,'state','\"ready\"'::jsonb,%s,
                             'system','forged replay fixture',%s)""",
                (joe_id, wr00, Jsonb({"package_key": "00", "state": "claimed", "version": 999}), str(claim_key)),
            )
            claim = one(cur, "select ops.siep_claim_package('00','session:siep00-gate',%s,%s)", (lease_token, claim_key))[0]
            if claim["state"] != "claimed" or claim["version"] != 2 or claim["replayed"]:
                raise RuntimeError("root package claim was not exact")
            if not one(cur, "select (ops.siep_claim_package('00','session:siep00-gate',%s,%s)->>'replayed')::boolean", (lease_token, claim_key))[0]:
                raise RuntimeError("claim exact replay was not a no-op")
            refusal(cur, "select ops.siep_acquire_lane_lock('00','session:siep00-gate',300,%s)",
                    (claim_key,), "inputs changed")
            refusal(cur, "select ops.siep_claim_package('B0','session:siep00-gate',%s,%s)", (lease_token, claim_key), "inputs changed")
            cur.execute("reset role")
            cur.execute("update ops.siep_lane_lock set acquired_at=now()-interval '10 minutes',expires_at=now()-interval '1 minute' where package_key='00'")
            set_local_role(cur, "carr_writer")
            refusal(cur, "select ops.siep_acquire_lane_lock('B0','session:expired-takeover',300,%s)",
                    (uuid.uuid4(),), "recovery must resume")
            cur.execute("reset role")
            cur.execute("update ops.siep_lane_lock set acquired_at=now(),expires_at=now()+interval '5 minutes' where package_key='00'")
            set_local_role(cur, "carr_writer")
            refusal(cur, "select ops.siep_transition_package('00',2,'in_progress','session:stolen',%s,%s)",
                    (lease_token, uuid.uuid4()), "holder-bound")
            refusal(cur, "select ops.siep_transition_package('00',2,'in_progress','session:siep00-gate',%s,%s)",
                    (uuid.uuid4(), uuid.uuid4()), "holder-bound")

            for base, target in ((2, "in_progress"), (3, "verification"), (4, "awaiting_release")):
                result = one(cur, "select ops.siep_transition_package('00',%s,%s,'session:siep00-gate',%s,%s)",
                             (base, target, lease_token, uuid.uuid4()))[0]
                if result["version"] != base + 1:
                    raise RuntimeError("typed transition did not advance exactly one version")
            refusal(cur, "select ops.siep_acquire_lane_lock('00','session:siep00-gate',300,%s)",
                    (uuid.uuid4(),), "already locked")

            cur.execute("reset role")
            codex_id = one(cur, "select id from actor where slug='codex' and active")[0]
            fixture_token = uuid.uuid4().hex
            document_id = one(
                cur,
                """insert into doctrine_document(slug,title,content_class,visibility,created_by)
                     values (%s,'SIEP B0 evidence fixture','reference','shared',%s) returning id""",
                (f"siep-b0-evidence-{fixture_token}", joe_id),
            )[0]
            section_id = one(
                cur,
                """insert into doctrine_section(document_id,section_key,title,ordinal,status,current_version)
                     values (%s,'fixture','SIEP B0 evidence fixture',1,'active',1) returning id""",
                (document_id,),
            )[0]
            revision_id = one(
                cur,
                """insert into doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                     values (%s,1,%s,%s,'SIEP B0 evidence fixture',%s,'fixture') returning id""",
                (section_id, joe_id, Jsonb({"text": "SIEP B0 evidence fixture"}), "a" * 64),
            )[0]
            receipt_specs = {
                "source": {"status": "pass", "operation": "source", "commit_sha": "a" * 40},
                "tests": {"status": "pass", "operation": "tests", "result_digest": "sha256:" + "b" * 64},
                "readback": {"status": "pass", "operation": "readback", "target_ref": "safe:local-pg:siep00"},
                "rollback": {"status": "pass", "operation": "rollback", "recovery_ref": "safe:rollback:siep00"},
                "independent_review": {"status": "pass", "operation": "independent_review",
                                       "reviewed_artifact_digest": "sha256:" + "c" * 64},
            }
            historical_slice_ref = "slice:siep00:historical-replay"
            plan_slices = [{
                "declared_component_refs": [f"component:siep00:{kind}"],
                "declared_resource_refs": [f"resource:siep00:{kind}"],
                "planned_checks": [{
                    "check_ref": f"check:siep00:{kind}",
                    "evidence_requirement": "metadata_only_sufficient",
                }],
                "slice_ref": f"slice:siep00:{kind}",
            } for kind in receipt_specs] + [{
                "declared_component_refs": ["component:siep00:historical-replay"],
                "declared_resource_refs": ["resource:siep00:historical-replay"],
                "planned_checks": [{
                    "check_ref": "check:siep00:historical-replay",
                    "evidence_requirement": "metadata_only_sufficient",
                }],
                "slice_ref": historical_slice_ref,
            }]
            plan_id = one(
                cur,
                """insert into ops.sourced_work_request_plan
                     (work_request_id,plan_version,idempotency_key,work_request_version,preimage,
                      scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,runbook_content_hash,
                      dependency_refs,recovery_ref,observability_ref,caps,plan_hash,plan_ref)
                     values (%s,1,%s,5,%s,'SIEP B0 evidence fixture','doctrine:runbook#fixture',%s,%s,%s,
                             %s,'safe:recovery:siep-b0','safe:observability:siep-b0',%s,%s,%s)
                     returning id""",
                (wr00, uuid.uuid4(), Jsonb({}), section_id, revision_id, "b" * 64,
                 Jsonb([]), Jsonb({}), "sha256:" + "c" * 64, f"PLAN-{fixture_token[:12]}-v1"),
            )[0]
            prior_siep_state = one(
                cur, "select state from ops.work_request where id=%s", (wr00,)
            )[0]
            if prior_siep_state != "awaiting_release":
                raise RuntimeError(
                    f"SIEP evidence fixture expected awaiting_release, got {prior_siep_state}"
                )
            set_siep_fixture_state(cur, wr00, "ready")
            one(
                cur,
                """insert into ops.sourced_work_request_plan_acceptance_receipt
                     (work_request_id,plan_id,idempotency_key,base_version,plan_hash,
                      accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
                     values (%s,%s,%s,5,%s,%s,5,'fixture:siep-b0','fixture acceptance')
                     returning id""",
                (wr00, plan_id, uuid.uuid4(), "sha256:" + "c" * 64, joe_id),
            )
            source = one(
                cur, "select ops.engineering_admission_source('WR-SIEP-00')"
            )[0]
            record_digest = source["work_request"]["canonical_record_digest"]
            plan_payload = {
                "accepted_plan_revision": {
                    "digest": source["accepted_plan"]["digest"],
                    "id": source["accepted_plan"]["plan_ref"],
                    "revision": source["accepted_plan"]["revision"],
                },
                "schema_version": "engineering-slice-plan.v1",
                "slices": plan_slices,
                "work_request": {
                    "canonical_record_digest": record_digest,
                    "id": source["work_request"]["id"],
                    "state_version": source["work_request"]["version"],
                },
            }
            plan_digest = canonical_digest(plan_payload)
            plan_payload["plan_digest"] = plan_digest
            slice_plan_id = one(
                cur,
                """insert into ops.engineering_slice_plan
                     (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,idempotency_key)
                     values (%s,%s,%s,5,%s,%s,%s) returning id""",
                (wr00, plan_id, "sha256:" + "c" * 64, plan_digest,
                 Jsonb(plan_payload), uuid.uuid4()),
            )[0]
            capability_session_id = one(
                cur,
                """insert into ops.capability_agent_session
                     (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,worktree_ref,scope_ref)
                     values (%s,%s,%s,%s,'siep-b0-gate','slice:siep-b0') returning id""",
                (wr00, codex_id, joe_id, "e" * 40),
            )[0]
            evidence: dict[str, tuple[uuid.UUID, str, str]] = {}
            evidence_jobs: dict[str, uuid.UUID] = {}
            evidence_envelopes: dict[str, uuid.UUID] = {}
            for evidence_kind, values in receipt_specs.items():
                job_id = one(
                    cur,
                    """insert into ops.job
                         (definition_key,definition_version,idempotency_key,scheduled_for,mode,state,payload,
                          attempt,max_attempts,timeout_seconds,started_at,ended_at)
                         values ('engineering-slice',1,%s,%s,'shadow','succeeded',%s,1,1,300,now(),now())
                         returning id""",
                    (f"siep-b0-gate:{uuid.uuid4()}",
                     f"2099-01-{1 + len(evidence):02d} 00:00:00+00",
                    Jsonb({"work_request": "WR-SIEP-00", "package_key": "00",
                            "manifest_digest": manifest})),
                )[0]
                slice_ref = f"slice:siep00:{evidence_kind}"
                envelope_digest = "sha256:" + uuid.uuid4().hex * 2
                envelope_id = uuid.uuid4()
                envelope = {
                    "agent_session": {"id": f"session:{capability_session_id}"},
                    "envelope_id": f"env:{envelope_id}",
                    "request": {"job_ref": f"job:{job_id}"},
                    "server_binding": {
                        "adapter": {"adapter_id": "adapter:codex-desktop"},
                        "identity": {"agent_principal_id": "agent:codex"},
                    },
                    "work_request_id": f"wr:{wr00}",
                    "state_binding": {"state_version": 5,
                                      "canonical_record_digest": record_digest},
                }
                one(
                    cur,
                    """insert into ops.engineering_execution_envelope
                         (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
                          state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at)
                         values (%s,%s,%s,%s,%s,%s,%s,5,%s,%s,%s,now()-interval '1 minute',now()+interval '1 hour')
                         returning id""",
                    (envelope_id, job_id, wr00, plan_id, slice_plan_id, slice_ref, capability_session_id,
                     record_digest, envelope_digest,
                     Jsonb(envelope)),
                )
                job_attempt_id = one(
                    cur,
                    """insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state,ended_at)
                         values (%s,1,%s,%s,'succeeded',now()) returning id""",
                    (job_id, "joe-authority-review" if evidence_kind == "independent_review" else "siep-b0-gate",
                     uuid.uuid4()),
                )[0]
                typed_evidence = {
                    "content_digest": "sha256:" + "f" * 64,
                    "redaction_class": "metadata_only",
                    "ref": f"evidence:siep00:{evidence_kind}",
                }
                receipt_payload = {
                    "actual_component_refs": [f"component:siep00:{evidence_kind}"],
                    "actual_resource_refs": [f"resource:siep00:{evidence_kind}"],
                    "artifact_refs": [f"artifact:siep00:{evidence_kind}"],
                    "attribution": {"actor_ref": "agent:codex", "adapter_ref": "adapter:codex-desktop",
                                    "session_ref": f"session:{capability_session_id}"},
                    "attempt_id": "attempt:1",
                    "checks": [{"check_ref": f"check:siep00:{evidence_kind}",
                                "evidence_refs": [typed_evidence], "state": "passed"}],
                    "deviations": [],
                    "envelope_digest": envelope_digest,
                    "evidence_refs": [typed_evidence],
                    "executor_claim": {"claim_state": "executor_claim", "claimed_at": "2026-08-26T00:00:00Z",
                                       "claimed_by": "codex"},
                    "independent_verification_required": True,
                    "outcome": "claimed_complete",
                    "plan_digest": plan_digest,
                    "planned_component_refs": [f"component:siep00:{evidence_kind}"],
                    "planned_resource_refs": [f"resource:siep00:{evidence_kind}"],
                    "reset_reconstruction": {"fresh_session": True, "inherited_transcript_used": False,
                                             "reconstruction_free": True, "remediation_action": None},
                    "schema_version": "engineering-slice-receipt.v1",
                    "slice_ref": slice_ref,
                    "source_evidence": {"branch_ref": "branch:siep00", "evidence_refs": [typed_evidence],
                                        "source_sha": "e" * 40, "worktree_ref": "worktree:siep00"},
                }
                engineering_receipt_id = one(
                    cur,
                    """insert into ops.engineering_slice_receipt
                         (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,
                          executor_actor_id,receipt_digest,outcome,receipt)
                         values (%s,%s,%s,%s,'attempt:1',%s,%s,'claimed_complete',%s) returning id""",
                    (job_attempt_id, envelope_id, wr00, slice_ref, codex_id,
                     canonical_digest(receipt_payload), Jsonb(receipt_payload)),
                )[0]
                review_evidence = {**typed_evidence, "ref": f"evidence:siep00-review:{evidence_kind}"}
                reviewer_fact = {
                    "attempt_id": "attempt:1", "evidence_refs": [review_evidence], "is_independent": True,
                    "resolved_deviation_refs": [], "reviewed_deviation_refs": [], "reviewer_ref": "joe",
                    "session_ref": "session:siep00-review", "slice_ref": slice_ref, "state": "passed",
                }
                cur.execute(
                    """insert into ops.engineering_reviewer_fact
                         (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,
                          state,fact,idempotency_key)
                         values (%s,%s,%s,%s,'session:siep00-review','passed',%s,%s)""",
                    (engineering_receipt_id, wr00, slice_ref, joe_id,
                     Jsonb(reviewer_fact), uuid.uuid4()),
                )
                receipt_id = one(
                    cur,
                    """insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
                         values (%s,1,'completion',%s,%s) returning id""",
                    (job_id, f"safe:siep00:{evidence_kind}", Jsonb(values)),
                )[0]
                evidence[evidence_kind] = (receipt_id, "job_receipt", "")
                evidence_jobs[evidence_kind] = job_id
                evidence_envelopes[evidence_kind] = envelope_id
            generic_job_id = one(
                cur,
                """insert into ops.job
                     (definition_key,definition_version,idempotency_key,scheduled_for,mode,state,payload,
                      attempt,max_attempts,timeout_seconds,started_at,ended_at)
                     select key,version,%s,'2099-02-01 00:00:00+00','shadow','succeeded',%s,1,2,300,now(),now()
                       from ops.job_definition where key='engineering-slice' and enabled
                      order by version desc limit 1 returning id""",
                (f"siep-b0-forged:{uuid.uuid4()}", Jsonb({"work_request": "WR-SIEP-00", "package_key": "00"})),
            )[0]
            cur.execute(
                """insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state,ended_at)
                     values (%s,1,'siep-b0-gate',%s,'succeeded',now())""",
                (generic_job_id, uuid.uuid4()),
            )
            stale_receipt_id = one(
                cur,
                """insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence,created_at)
                     values (%s,1,'completion','safe:siep00:stale-source',%s,now()-interval '1 day') returning id""",
                (evidence_jobs["source"], Jsonb({"status": "pass", "operation": "source", "commit_sha": "e" * 40})),
            )[0]
            generic_receipt_id = one(
                cur,
                """insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
                     values (%s,1,'completion','safe:siep00:generic-source',%s) returning id""",
                (generic_job_id, Jsonb({"status": "pass", "operation": "source", "commit_sha": "f" * 40})),
            )[0]
            generic_receipt_digest = "sha256:" + "f" * 64
            set_local_role(cur, "carr_writer")
            if one(cur, "select has_table_privilege(current_user,'ops.siep_job_evidence_binding','INSERT')")[0]:
                raise RuntimeError("carr_writer gained raw SIEP evidence-binding DML")
            refusal(cur, "select ops.siep_bind_evidence_job('00',5,'source',%s,%s)",
                    (generic_job_id, uuid.uuid4()), "permission denied")
            cur.execute("reset role")
            source_job_id = evidence_jobs["source"]
            cur.execute("update ops.job set attempt=2 where id=%s", (source_job_id,))
            cur.execute(
                """insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state,ended_at)
                     values (%s,2,'siep-b0-later-attempt',%s,'succeeded',now())""",
                (source_job_id, uuid.uuid4()),
            )
            cur.execute(
                """insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
                     values (%s,2,'completion','safe:siep00:later-attempt',%s)""",
                (source_job_id, Jsonb({"status": "pass", "operation": "source", "commit_sha": "9" * 40})),
            )
            cur.execute("set session authorization carr_authority_dell")
            refusal(cur, "select ops.siep_bind_evidence_job('00',5,'source',%s,%s)",
                    (source_job_id, uuid.uuid4()), "authenticated Joe authority session")
            cur.execute("reset session authorization")
            cur.execute("set session authorization carr_authority_joe")
            refusal(cur, "select ops.siep_bind_evidence_job('00',5,'source',%s,%s)",
                    (source_job_id, uuid.uuid4()), "independently reviewed engineering envelope")
            cur.execute("reset session authorization")
            cur.execute("update ops.job set attempt=1 where id=%s", (source_job_id,))
            cur.execute("set session authorization carr_authority_joe")
            refusal(cur, "select ops.siep_bind_evidence_job('00',5,'source',%s,%s)",
                    (generic_job_id, uuid.uuid4()), "independently reviewed engineering envelope")
            cur.execute("reset session authorization")
            # This job was deliberately bare for the preceding negative
            # assertions. Complete it now as a separately reviewed Engineering
            # job for the historical NULL-stamp replay; it is not in evidence_jobs.
            historical_envelope_id = uuid.uuid4()
            historical_envelope_digest = "sha256:" + uuid.uuid4().hex * 2
            historical_envelope = {
                "agent_session": {"id": f"session:{capability_session_id}"},
                "envelope_id": f"env:{historical_envelope_id}",
                "request": {"job_ref": f"job:{generic_job_id}"},
                "server_binding": {"adapter": {"adapter_id": "adapter:codex-desktop"},
                                   "identity": {"agent_principal_id": "agent:codex"}},
                "work_request_id": f"wr:{wr00}",
                "state_binding": {"state_version": 5, "canonical_record_digest": record_digest},
            }
            one(cur, """insert into ops.engineering_execution_envelope
                     (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
                      state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at)
                     values (%s,%s,%s,%s,%s,%s,%s,5,%s,%s,%s,now()-interval '1 minute',now()+interval '1 hour')
                     returning id""",
                (historical_envelope_id, generic_job_id, wr00, plan_id, slice_plan_id, historical_slice_ref,
                 capability_session_id, record_digest, historical_envelope_digest, Jsonb(historical_envelope)))
            set_siep_fixture_state(cur, wr00, prior_siep_state)
            historical_attempt_id = one(cur, "select id from ops.job_attempt where job_id=%s and attempt=1", (generic_job_id,))[0]
            historical_evidence = {"content_digest": "sha256:" + "f" * 64,
                                   "redaction_class": "metadata_only", "ref": "evidence:siep00:historical-replay"}
            historical_receipt_payload = {
                "actual_component_refs": ["component:siep00:historical-replay"],
                "actual_resource_refs": ["resource:siep00:historical-replay"],
                "artifact_refs": ["artifact:siep00:historical-replay"],
                "attribution": {"actor_ref": "agent:codex", "adapter_ref": "adapter:codex-desktop",
                                "session_ref": f"session:{capability_session_id}"},
                "attempt_id": "attempt:1",
                "checks": [{"check_ref": "check:siep00:historical-replay", "evidence_refs": [historical_evidence], "state": "passed"}],
                "deviations": [], "envelope_digest": historical_envelope_digest, "evidence_refs": [historical_evidence],
                "executor_claim": {"claim_state": "executor_claim", "claimed_at": "2026-08-26T00:00:00Z", "claimed_by": "codex"},
                "independent_verification_required": True, "outcome": "claimed_complete", "plan_digest": plan_digest,
                "planned_component_refs": ["component:siep00:historical-replay"],
                "planned_resource_refs": ["resource:siep00:historical-replay"],
                "reset_reconstruction": {"fresh_session": True, "inherited_transcript_used": False,
                                         "reconstruction_free": True, "remediation_action": None},
                "schema_version": "engineering-slice-receipt.v1", "slice_ref": historical_slice_ref,
                "source_evidence": {"branch_ref": "branch:siep00", "evidence_refs": [historical_evidence],
                                    "source_sha": "e" * 40, "worktree_ref": "worktree:siep00"},
            }
            historical_receipt_id = one(cur, """insert into ops.engineering_slice_receipt
                     (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,executor_actor_id,
                      receipt_digest,outcome,receipt)
                     values (%s,%s,%s,%s,'attempt:1',%s,%s,'claimed_complete',%s) returning id""",
                (historical_attempt_id, historical_envelope_id, wr00, historical_slice_ref, codex_id,
                 canonical_digest(historical_receipt_payload), Jsonb(historical_receipt_payload)))[0]
            historical_reviewer_fact = {
                "attempt_id": "attempt:1", "evidence_refs": [{**historical_evidence,
                    "ref": "evidence:siep00-review:historical-replay"}], "is_independent": True,
                "resolved_deviation_refs": [], "reviewed_deviation_refs": [], "reviewer_ref": "joe",
                "session_ref": "session:siep00-review", "slice_ref": historical_slice_ref, "state": "passed",
            }
            cur.execute("""insert into ops.engineering_reviewer_fact
                         (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
                         values (%s,%s,%s,%s,'session:siep00-review','passed',%s,%s)""",
                (historical_receipt_id, wr00, historical_slice_ref, joe_id, Jsonb(historical_reviewer_fact), uuid.uuid4()))
            historical_key = uuid.uuid4()
            refusal(cur, """insert into ops.siep_job_evidence_binding
                         (job_id,package_key,work_request_version,manifest_digest,evidence_kind,definition_key,definition_version,bound_by_actor_id,idempotency_key,engineering_contract_version)
                       values (%s,'00',5,%s,'migration','engineering-slice',1,%s,%s,'engineering-review.v1')""",
                    (generic_job_id, manifest, joe_id, uuid.uuid4()), "caller-controlled")
            cur.execute("alter table ops.siep_job_evidence_binding disable trigger siep_engineering_evidence_binding_contract_guard")
            try:
                cur.execute("""insert into ops.siep_job_evidence_binding
                             (job_id,package_key,work_request_version,manifest_digest,evidence_kind,definition_key,definition_version,bound_by_actor_id,idempotency_key)
                           values (%s,'00',5,%s,'migration','engineering-slice',1,%s,%s)""",
                            (generic_job_id, manifest, joe_id, historical_key))
            finally:
                cur.execute("alter table ops.siep_job_evidence_binding enable trigger siep_engineering_evidence_binding_contract_guard")
            if one(cur, "select tgenabled from pg_trigger where tgrelid='ops.siep_job_evidence_binding'::regclass and tgname='siep_engineering_evidence_binding_contract_guard'")[0] != "O":
                raise RuntimeError("SIEP Engineering contract guard was not restored after historical fixture")
            cur.execute("set session authorization carr_authority_joe")
            refusal(cur, "select ops.siep_bind_evidence_job('00',5,'migration',%s,%s)",
                    (generic_job_id, historical_key), "historical or superseded SIEP Engineering evidence binding is not 0335 verified")
            cur.execute("reset session authorization")
            cur.execute("savepoint siep_superseded_unstamped_probe")
            superseding_envelope_probe(
                cur, evidence_jobs["tests"], evidence_envelopes["tests"]
            )
            cur.execute("set session authorization carr_authority_joe")
            refusal(
                cur, "select ops.siep_bind_evidence_job('00',5,'tests',%s,%s)",
                (evidence_jobs["tests"], uuid.uuid4()),
                "SIEP evidence binding requires a 0335-verified Engineering receipt and review",
            )
            cur.execute("reset session authorization")
            if one(
                cur,
                "select count(*) from ops.siep_job_evidence_binding where job_id=%s",
                (evidence_jobs["tests"],),
            )[0] != 0:
                raise RuntimeError("superseded Engineering predecessor gained a SIEP v1 stamp")
            cur.execute("rollback to savepoint siep_superseded_unstamped_probe")
            cur.execute("release savepoint siep_superseded_unstamped_probe")
            cur.execute("set session authorization carr_authority_joe")
            binding_keys: dict[str, uuid.UUID] = {}
            for kind, bound_job_id in evidence_jobs.items():
                binding_key = uuid.uuid4()
                binding_keys[kind] = binding_key
                bound = one(cur, "select ops.siep_bind_evidence_job('00',5,%s,%s,%s)",
                            (kind, bound_job_id, binding_key))[0]
                if bound["replayed"] or bound["evidence_kind"] != kind:
                    raise RuntimeError("typed SIEP evidence binding did not admit the exact purpose")
                cur.execute("reset session authorization")
                if one(cur, "select engineering_contract_version from ops.siep_job_evidence_binding where job_id=%s", (bound_job_id,))[0] != "engineering-review.v1":
                    raise RuntimeError("valid SIEP evidence binding was not stamped by the database")
                cur.execute("set session authorization carr_authority_joe")
            replayed_binding = one(
                cur, "select ops.siep_bind_evidence_job('00',5,'source',%s,%s)",
                (evidence_jobs["source"], binding_keys["source"]),
            )[0]
            if not replayed_binding["replayed"]:
                raise RuntimeError("evidence-binding exact replay was not a no-op")
            cur.execute("reset session authorization")
            for kind, (receipt_id, ledger_kind, _) in tuple(evidence.items()):
                digest = one(cur, "select ops.siep_current_evidence_digest('job_receipt',%s)", (receipt_id,))[0]
                evidence[kind] = (receipt_id, ledger_kind, digest)
            stale_receipt_digest = one(
                cur, "select ops.siep_current_evidence_digest('job_receipt',%s)", (stale_receipt_id,)
            )[0]
            cur.execute("savepoint siep_superseded_replay_probe")
            superseding_envelope_probe(
                cur, evidence_jobs["tests"], evidence_envelopes["tests"]
            )
            cur.execute("set session authorization carr_authority_joe")
            refusal(
                cur, "select ops.siep_bind_evidence_job('00',5,'tests',%s,%s)",
                (evidence_jobs["tests"], binding_keys["tests"]),
                "historical or superseded SIEP Engineering evidence binding is not 0335 verified",
            )
            cur.execute("reset session authorization")
            if one(
                cur,
                "select ops.siep_current_evidence_digest('job_receipt',%s)",
                (evidence["tests"][0],),
            )[0] is not None:
                raise RuntimeError("superseded SIEP v1 evidence remained digest-authoritative")
            cur.execute("rollback to savepoint siep_superseded_replay_probe")
            cur.execute("release savepoint siep_superseded_replay_probe")
            set_local_role(cur, "carr_writer")

            evidence_keys = {}
            refusal(cur, "select ops.siep_attach_evidence('00','source',%s,'finding',%s,'safe:forged:flag',%s)",
                    (raw_flag_id, raw_flag_digest, uuid.uuid4()), "valid package-bound canonical fact")
            refusal(cur, "select ops.siep_attach_evidence('00','source',%s,'job_receipt',%s,'safe:stale:source',%s)",
                    (stale_receipt_id, stale_receipt_digest, uuid.uuid4()), "predates the package execution boundary")
            refusal(cur, "select ops.siep_attach_evidence('00','source',%s,'job_receipt',%s,'safe:generic:source',%s)",
                    (generic_receipt_id, generic_receipt_digest, uuid.uuid4()), "valid package-bound canonical fact")
            for kind in ("source", "tests", "readback", "rollback"):
                key = uuid.uuid4()
                evidence_keys[kind] = key
                ledger_id, ledger_kind, digest = evidence[kind]
                one(cur, "select ops.siep_attach_evidence('00',%s,%s,%s,%s,%s,%s)",
                    (kind, ledger_id, ledger_kind, digest, f"safe:siep00:{kind}", key))
            independent_id, independent_ledger, independent_digest = evidence["independent_review"]
            refusal(cur, "select ops.siep_attach_evidence('00','independent_review',%s,%s,%s,'safe:siep00:independent_review',%s)",
                    (independent_id, independent_ledger, independent_digest, uuid.uuid4()),
                    "authenticated Joe authority session")
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            independent_key = uuid.uuid4()
            evidence_keys["independent_review"] = independent_key
            one(cur, "select ops.siep_attach_evidence('00','independent_review',%s,%s,%s,'safe:siep00:independent_review',%s)",
                (independent_id, independent_ledger, independent_digest, independent_key))
            cur.execute("reset session authorization")
            set_local_role(cur, "carr_writer")
            source_id, _, source_digest = evidence["source"]
            replay = one(cur, "select ops.siep_attach_evidence('00','source',%s,'job_receipt',%s,'safe:siep00:source',%s)",
                         (source_id, source_digest, evidence_keys["source"]))[0]
            if not replay["replayed"]:
                raise RuntimeError("evidence exact replay was not a no-op")
            refusal(cur, "select ops.siep_attach_evidence('00','source',%s,'job_receipt',%s,'safe:siep00:changed',%s)",
                    (source_id, source_digest, evidence_keys["source"]), "inputs changed")
            refusal(cur, "select ops.siep_attach_evidence('00','tests',%s,'job_receipt',%s,'unsafe prose',%s)",
                    (source_id, source_digest, uuid.uuid4()), "safe reference")
            refusal(cur, "select ops.siep_attach_evidence('00','tests',%s,'job_receipt',%s,'safe:siep00:relabel',%s)",
                    (source_id, source_digest, uuid.uuid4()), "valid package-bound canonical fact")

            cur.execute("reset role")
            source_job_id = evidence_jobs["source"]
            cur.execute("update ops.job set payload=jsonb_set(payload,'{package_key}','\"forged\"'::jsonb) where id=%s", (source_job_id,))
            set_local_role(cur, "carr_writer")
            refusal(cur, "select ops.siep_transition_package('00',5,'released','session:siep00-gate',%s,%s)",
                    (lease_token, uuid.uuid4()), "missing evidence")
            cur.execute("reset role")
            cur.execute("update ops.job set payload=%s where id=%s",
                        (Jsonb({"work_request": "WR-SIEP-00", "package_key": "00",
                                "manifest_digest": manifest}), source_job_id))
            set_local_role(cur, "carr_writer")
            one(cur, "select ops.siep_transition_package('00',5,'released','session:siep00-gate',%s,%s)", (lease_token, uuid.uuid4()))
            close = one(cur, "select ops.siep_transition_package('00',6,'confirmed_closed','session:siep00-gate',%s,%s)", (lease_token, uuid.uuid4()))[0]
            if close["state"] != "confirmed_closed" or close["version"] != 7:
                raise RuntimeError("evidence-gated package closure was not exact")
            if one(cur, "select ops.siep_release_lane_lock('00','session:siep00-gate',%s,%s)",
                   (lease_token, uuid.uuid4()))[0]:
                raise RuntimeError("terminal package transition left its lane locked")

            b0_lock = one(cur, "select ops.siep_acquire_lane_lock('B0','session:b0-after-root',300,%s)", (uuid.uuid4(),))[0]
            b0_claim = one(cur, "select ops.siep_claim_package('B0','session:b0-after-root',%s,%s)",
                           (uuid.UUID(b0_lock["lease_token"]), uuid.uuid4()))[0]
            if b0_claim["state"] != "claimed" or b0_lock["lane_key"] != "program-control":
                raise RuntimeError("dependency-first successor did not become claimable")
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_dell")
            refusal(cur, "select ops.siep_record_joe_decision('25','joe_approval','approved',%s)",
                    (uuid.uuid4(),), "authenticated Joe authority session")
            cur.execute("reset session authorization")
            cur.execute("set session authorization carr_authority_joe")
            approval_key = uuid.uuid4()
            approval_event_id = one(
                cur, "select ops.siep_record_joe_decision('25','joe_approval','approved',%s)",
                (approval_key,),
            )[0]
            if one(cur, "select ops.siep_record_joe_decision('25','joe_approval','approved',%s)",
                   (approval_key,))[0] != approval_event_id:
                raise RuntimeError("Joe decision exact replay changed its event identity")
            cur.execute("reset session authorization")
            approval_digest = one(
                cur, "select ops.siep_current_evidence_digest('decision_event',%s)", (approval_event_id,)
            )[0]
            cur.execute("set session authorization carr_authority_joe")
            one(cur, "select ops.siep_attach_evidence('25','joe_approval',%s,'decision_event',%s,'safe:siep25:approval',%s)",
                (approval_event_id, approval_digest, uuid.uuid4()))
            cur.execute("reset session authorization")
            if not one(cur, "select ops.siep_current_approval('25',1,'joe_approval')")[0]:
                raise RuntimeError("fresh typed Joe approval did not become current")
            set_local_role(cur, "carr_writer")
            cur.execute("update public.actor set active=false where id=%s", (joe_id,))
            cur.execute("update public.actor set slug='joe-disabled-fixture' where id=%s", (joe_id,))
            refusal(cur, "update public.event set new_value=jsonb_set(new_value,'{decision}','\"revoked\"') where id=%s",
                    (approval_event_id,), "decision events are immutable")
            refusal(
                cur,
                """insert into public.event
                     (actor_id,verb,subject_type,subject_id,field,new_value,cause,agent_rationale)
                     values (%s,'siep-joe-decision','work_request',%s,'decision',%s,'joe','forged decision')""",
                (joe_id, wr00, Jsonb({"program_key": "carr-system-integrity-elimination-v1",
                                      "package_key": "25", "gate": "joe_approval", "decision": "approved"})),
                "authenticated Joe authority session",
            )
            cur.execute("update public.actor set slug='joe' where id=%s", (joe_id,))
            cur.execute("update public.actor set active=true where id=%s", (joe_id,))
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            one(cur, "select ops.siep_record_joe_decision('25','joe_approval','revoked',%s)", (uuid.uuid4(),))
            cur.execute("reset session authorization")
            if one(cur, "select ops.siep_current_approval('25',1,'joe_approval')")[0]:
                raise RuntimeError("later typed Joe revocation did not supersede approval")
            set_local_role(cur, "carr_writer")
            terminal = one(cur, "select ops.siep_terminal_status()")[0]
            if terminal["complete"] or terminal["open_packages"] != 39:
                raise RuntimeError("terminal authority made a premature success claim")
    except Exception as exc:  # noqa: BLE001 - gate reports the exact failing assertion
        return fail(str(exc))
    print("siep program local acceptance passed: exact DAG, typed locks/claims, provenance, CAS, closure, and false terminal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
