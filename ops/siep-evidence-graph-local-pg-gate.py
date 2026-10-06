#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only behavioral acceptance for the SIEP-06A evidence projection."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid

from psycopg.types.json import Jsonb

from gate_runtime_role import rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"siep-evidence-graph-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, query: str, params: tuple = ()):
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError("required row was not returned")
    return row


def canonical_digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def refusal(cur, query: str, params: tuple, fragment: str) -> None:
    savepoint = "siep06a_refusal_" + uuid.uuid4().hex[:10]
    cur.execute(f"savepoint {savepoint}")
    try:
        cur.execute(query, params)
    except Exception as exc:  # noqa: BLE001 - refusal text is the assertion
        cur.execute(f"rollback to savepoint {savepoint}")
        cur.execute(f"release savepoint {savepoint}")
        if fragment not in str(exc):
            raise RuntimeError(f"expected refusal containing {fragment!r}, got {exc}") from exc
        return
    cur.execute(f"rollback to savepoint {savepoint}")
    cur.execute(f"release savepoint {savepoint}")
    raise RuntimeError(f"expected refusal containing {fragment!r}")


def forbidden_keys(value):
    forbidden = {"lease_token", "session_ref", "payload", "evidence", "fact", "envelope"}
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                yield key
            yield from forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from forbidden_keys(child)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            refusal(cur, "select ops.siep_read_evidence_graph('06')", (),
                    "known SIEP package or component alias")
            refusal(cur, "select ops.siep_read_evidence_graph('24')", (),
                    "known SIEP package or component alias")
            refusal(cur, "select ops.siep_read_evidence_graph('unknown')", (),
                    "known SIEP package or component alias")

            direct = one(cur, "select ops.siep_read_evidence_graph('10')")[0]
            alias = one(cur, "select ops.siep_read_evidence_graph('SCAC-00')")[0]
            if direct != alias:
                raise RuntimeError("SCAC-00 alias projection differed from package 10")
            if direct["scope"] != "package:10" or direct["integrity"]["link_count"] != 0:
                raise RuntimeError("empty package projection did not remain truthful")

            manifest = one(cur, "select ops.siep_manifest_digest()")[0]
            wr_id, wr_version, captured_at, shape_ref, shape_rationale = one(
                cur, """select id,version,captured_at,shape_fixed_surface_ref,shape_rationale
                          from ops.work_request where ref='WR-SIEP-06A'"""
            )
            joe_id = one(cur, "select id from public.actor where slug='joe' and active")[0]
            codex_id = one(cur, "select id from public.actor where slug='codex' and active")[0]
            system_id = one(cur, "select id from public.actor where slug='system' and active")[0]
            token = uuid.uuid4().hex

            document_id = one(
                cur,
                """insert into public.doctrine_document(slug,title,content_class,visibility,created_by)
                     values (%s,'SIEP 06A graph fixture','reference','shared',%s) returning id""",
                (f"siep-06a-graph-{token}", joe_id),
            )[0]
            section_id = one(
                cur,
                """insert into public.doctrine_section(document_id,section_key,title,ordinal,status,current_version)
                     values (%s,'fixture','SIEP 06A graph fixture',1,'active',1) returning id""",
                (document_id,),
            )[0]
            revision_id = one(
                cur,
                """insert into public.doctrine_revision
                     (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                     values (%s,1,%s,%s,'SIEP 06A graph fixture',%s,'fixture') returning id""",
                (section_id, joe_id, Jsonb({"text": "SIEP 06A graph fixture"}), "a" * 64),
            )[0]
            plan_hash = "sha256:" + "b" * 64
            plan_id = one(
                cur,
                """insert into ops.sourced_work_request_plan
                     (work_request_id,plan_version,idempotency_key,work_request_version,preimage,
                      scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,runbook_content_hash,
                      dependency_refs,recovery_ref,observability_ref,caps,plan_hash,plan_ref)
                     values (%s,1,%s,%s,%s,'SIEP 06A graph fixture','doctrine:runbook#fixture',%s,%s,%s,
                             %s,'safe:recovery:siep-06a','safe:observability:siep-06a',%s,%s,%s)
                     returning id""",
                (wr_id, uuid.uuid4(), wr_version, Jsonb({}), section_id, revision_id, "c" * 64,
                 Jsonb([]), Jsonb({}), plan_hash, f"PLAN-{token[:12]}-v1"),
            )[0]
            one(
                cur,
                """insert into ops.sourced_work_request_plan_acceptance_receipt
                     (work_request_id,plan_id,idempotency_key,base_version,plan_hash,
                      accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
                     values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (wr_id, plan_id, uuid.uuid4(), wr_version, plan_hash, joe_id, wr_version,
                 shape_ref, shape_rationale),
            )
            source = one(
                cur, "select ops.engineering_admission_source('WR-SIEP-06A')"
            )[0]
            slice_ref = "slice:siep-06a:source"
            component_ref = "component:siep06a:source"
            resource_ref = "resource:siep06a:source"
            check_ref = "check:siep06a:source"
            plan_payload = {
                "accepted_plan_revision": {
                    "digest": source["accepted_plan"]["digest"],
                    "id": source["accepted_plan"]["plan_ref"],
                    "revision": source["accepted_plan"]["revision"],
                },
                "schema_version": "engineering-slice-plan.v1",
                "slices": [
                    {
                        "declared_component_refs": [component_ref],
                        "declared_resource_refs": [resource_ref],
                        "planned_checks": [
                            {
                                "check_ref": check_ref,
                                "evidence_requirement": "metadata_only_sufficient",
                            }
                        ],
                        "slice_ref": slice_ref,
                    }
                ],
                "work_request": {
                    "canonical_record_digest": source["work_request"]["canonical_record_digest"],
                    "id": source["work_request"]["id"],
                    "state_version": source["work_request"]["version"],
                },
            }
            plan_digest = canonical_digest(plan_payload)
            plan_payload["plan_digest"] = plan_digest
            slice_plan_id = one(
                cur,
                """insert into ops.engineering_slice_plan
                     (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,
                      plan_digest,plan,idempotency_key)
                     values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                (wr_id, plan_id, plan_hash, source["work_request"]["version"], plan_digest,
                 Jsonb(plan_payload), uuid.uuid4()),
            )[0]
            capability_session_id = one(
                cur,
                """insert into ops.capability_agent_session
                     (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,worktree_ref,scope_ref)
                     values (%s,%s,%s,%s,'siep-06a-gate','slice:siep-06a') returning id""",
                (wr_id, codex_id, joe_id, "e" * 40),
            )[0]
            job_id = one(
                cur,
                """insert into ops.job
                     (definition_key,definition_version,idempotency_key,scheduled_for,mode,state,payload,
                      attempt,max_attempts,timeout_seconds,started_at,ended_at)
                     values ('engineering-slice',1,%s,'2099-03-01 00:00:00+00','shadow','succeeded',
                             %s,1,1,300,now(),now()) returning id""",
                (f"siep-06a-gate:{uuid.uuid4()}", Jsonb({"work_request": "WR-SIEP-06A",
                                                        "package_key": "06A",
                                                        "manifest_digest": manifest})),
            )[0]
            envelope_digest = "sha256:" + "f" * 64
            envelope_id = uuid.uuid4()
            envelope = {
                "agent_session": {"id": f"session:{capability_session_id}"},
                "envelope_id": f"env:{envelope_id}",
                "request": {"job_ref": f"job:{job_id}"},
                "server_binding": {
                    "adapter": {"adapter_id": "adapter:codex-desktop"},
                    "identity": {"agent_principal_id": "agent:codex"},
                },
                "state_binding": {
                    "state_version": source["work_request"]["version"],
                    "canonical_record_digest": source["work_request"]["canonical_record_digest"],
                },
                "work_request_id": f"wr:{wr_id}",
            }
            envelope_id = one(
                cur,
                """insert into ops.engineering_execution_envelope
                     (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
                      state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at)
                     values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                             now()-interval '1 minute',now()+interval '1 hour') returning id""",
                (envelope_id, job_id, wr_id, plan_id, slice_plan_id, slice_ref,
                 capability_session_id,
                 source["work_request"]["version"],
                 source["work_request"]["canonical_record_digest"], envelope_digest,
                 Jsonb(envelope)),
            )[0]
            attempt_id = one(
                cur,
                """insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state,ended_at)
                     values (%s,1,'siep-06a-gate',%s,'succeeded',now()) returning id""",
                (job_id, uuid.uuid4()),
            )[0]
            typed_evidence = {
                "content_digest": "sha256:" + "1" * 64,
                "redaction_class": "metadata_only",
                "ref": "evidence:siep06a:source",
            }
            engineering_receipt = {
                "actual_component_refs": [component_ref],
                "actual_resource_refs": [resource_ref],
                "artifact_refs": ["artifact:siep06a:source"],
                "attribution": {
                    "actor_ref": "agent:codex",
                    "adapter_ref": "adapter:codex-desktop",
                    "session_ref": f"session:{capability_session_id}",
                },
                "attempt_id": "attempt:1",
                "checks": [
                    {"check_ref": check_ref, "evidence_refs": [typed_evidence], "state": "passed"}
                ],
                "deviations": [],
                "envelope_digest": envelope_digest,
                "evidence_refs": [typed_evidence],
                "executor_claim": {
                    "claim_state": "executor_claim",
                    "claimed_at": "2026-08-26T00:00:00Z",
                    "claimed_by": "codex",
                },
                "independent_verification_required": True,
                "outcome": "claimed_complete",
                "plan_digest": plan_digest,
                "planned_component_refs": [component_ref],
                "planned_resource_refs": [resource_ref],
                "reset_reconstruction": {
                    "fresh_session": True,
                    "inherited_transcript_used": False,
                    "reconstruction_free": True,
                    "remediation_action": None,
                },
                "schema_version": "engineering-slice-receipt.v1",
                "slice_ref": slice_ref,
                "source_evidence": {
                    "branch_ref": "branch:siep06a",
                    "evidence_refs": [typed_evidence],
                    "source_sha": "e" * 40,
                    "worktree_ref": "worktree:siep06a",
                },
            }
            engineering_receipt_id = one(
                cur,
                """insert into ops.engineering_slice_receipt
                     (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,executor_actor_id,
                      receipt_digest,outcome,receipt)
                     values (%s,%s,%s,%s,'attempt:1',%s,%s,
                             'claimed_complete',%s) returning id""",
                (attempt_id, envelope_id, wr_id, slice_ref, codex_id,
                 canonical_digest(engineering_receipt), Jsonb(engineering_receipt)),
            )[0]
            reviewer_session_ref = "session:siep-06a-review"
            reviewer_fact = {
                "attempt_id": "attempt:1",
                "evidence_refs": [
                    {
                        "content_digest": "sha256:" + "2" * 64,
                        "redaction_class": "metadata_only",
                        "ref": "evidence:siep06a:independent-review",
                    }
                ],
                "is_independent": True,
                "resolved_deviation_refs": [],
                "reviewed_deviation_refs": [],
                "reviewer_ref": "reviewer:joe",
                "session_ref": reviewer_session_ref,
                "slice_ref": slice_ref,
                "state": "passed",
            }
            reviewer_fact_id = one(
                cur,
                """insert into ops.engineering_reviewer_fact
                     (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,
                      state,fact,idempotency_key)
                     values (%s,%s,%s,%s,%s,'passed',%s,%s)
                     returning id""",
                (engineering_receipt_id, wr_id, slice_ref, joe_id, reviewer_session_ref,
                 Jsonb(reviewer_fact), uuid.uuid4()),
            )[0]
            receipt_id, receipt_created_at = one(
                cur,
                """insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
                     values (%s,1,'completion','https://signed.example/?token=receipt-secret',%s)
                     returning id,created_at""",
                (job_id, Jsonb({"status": "pass", "operation": "source", "commit_sha": "3" * 40,
                                "target_ref": "https://signed.example/?token=extra-field-secret"})),
            )
            cur.execute(
                """insert into ops.siep_job_evidence_binding
                     (job_id,package_key,work_request_version,manifest_digest,evidence_kind,
                      definition_key,definition_version,bound_by_actor_id,idempotency_key)
                     values (%s,'06A',%s,%s,'source','engineering-slice',1,%s,%s)""",
                (job_id, wr_version, manifest, joe_id, uuid.uuid4()),
            )
            evidence_digest = one(
                cur, "select ops.siep_current_evidence_digest('job_receipt',%s)", (receipt_id,)
            )[0]
            evidence_link_id = one(
                cur,
                """insert into ops.siep_evidence_link
                     (package_key,evidence_kind,ledger_kind,ledger_id,work_request_version,
                      manifest_digest,evidence_digest,note,linked_actor_id,
                      attested_session_principal,source_observed_at,idempotency_key)
                     values ('06A','source','job_receipt',%s,%s,%s,%s,'safe:siep-06a:source',%s,
                             'fixture-owner',%s,%s) returning id""",
                (receipt_id, wr_version, manifest, evidence_digest, system_id, receipt_created_at, uuid.uuid4()),
            )[0]

            graph = one(cur, "select ops.siep_read_evidence_graph('06A')")[0]
            graph_again = one(cur, "select ops.siep_read_evidence_graph('06A')")[0]
            if graph["graph_digest"] != graph_again["graph_digest"]:
                raise RuntimeError("graph digest was not deterministic")
            if not graph["integrity"]["immutable_integrity_valid"] \
                    or graph["integrity"]["current_link_count"] != 1:
                raise RuntimeError("valid canonical attachment was not current")
            if graph["integrity"]["valid"] or graph["integrity"]["current_coverage_complete"]:
                raise RuntimeError("partial package evidence was presented as complete coverage")
            package_keys = {node["attributes"].get("package_key") for node in graph["nodes"]
                            if node["type"] == "package"}
            if not {"06A", "B0", "00"}.issubset(package_keys):
                raise RuntimeError("transitive dependency closure was not projected")
            node_types = {node["type"] for node in graph["nodes"]}
            expected_types = {"package", "work_request", "evidence_link", "job_receipt", "job",
                              "job_attempt", "evidence_binding", "engineering_envelope",
                              "engineering_slice_plan", "accepted_plan", "engineering_receipt",
                              "reviewer_fact", "actor"}
            if not expected_types.issubset(node_types):
                raise RuntimeError("exact engineering lineage was not projected")
            relations = {edge["relation"] for edge in graph["edges"]}
            if not {"has_evidence", "attests", "receipt_for", "records_attempt", "authorized_by",
                    "projects", "derives_from", "closes", "reviews", "reviewed_by"}.issubset(relations):
                raise RuntimeError("exact engineering lineage was not projected")
            leaked = sorted(set(forbidden_keys(graph)))
            if leaked:
                raise RuntimeError(f"graph leaked forbidden material: {leaked}")
            serialized = json.dumps(graph)
            if "receipt-secret" in serialized or "extra-field-secret" in serialized:
                raise RuntimeError("graph leaked forbidden material from an unconstrained receipt field")
            if any(not node.get("node_digest", "").startswith("sha256:") for node in graph["nodes"]):
                raise RuntimeError("a physical graph node lacked its canonical row digest")
            if str(evidence_link_id) not in json.dumps(graph):
                raise RuntimeError("physical evidence edge identity was not projected")

            cur.execute("savepoint newer_invalid_coverage")
            invalid_event_id, invalid_event_at = one(
                cur,
                """insert into public.event
                     (occurred_at,actor_id,verb,subject_type,subject_id,field,new_value,cause,agent_rationale)
                     values (now(),%s,'siep06a-invalid-source-fixture','work_request',%s,'evidence',%s,
                             'system','newer invalid attachment coverage fixture')
                     returning id,occurred_at""",
                (system_id, wr_id, Jsonb({})),
            )
            invalid_digest = one(
                cur, "select ops.siep_current_evidence_digest('decision_event',%s)", (invalid_event_id,)
            )[0]
            cur.execute(
                """insert into ops.siep_evidence_link
                     (package_key,evidence_kind,ledger_kind,ledger_id,work_request_version,
                      manifest_digest,evidence_digest,note,linked_actor_id,
                      attested_session_principal,source_observed_at,idempotency_key)
                     values ('06A','source','decision_event',%s,%s,%s,%s,
                             'safe:siep-06a:newer-invalid',%s,'fixture-owner',%s,%s)""",
                (invalid_event_id, wr_version, manifest, invalid_digest, system_id,
                 invalid_event_at, uuid.uuid4()),
            )
            mixed_graph = one(cur, "select ops.siep_read_evidence_graph('06A')")[0]
            source_coverage = next(item for item in mixed_graph["current_coverage"]
                                   if item["package_key"] == "06A"
                                   and item["evidence_kind"] == "source")
            if not source_coverage["covered"] or mixed_graph["integrity"]["immutable_integrity_valid"]:
                raise RuntimeError("newer invalid evidence shadowed older current purpose coverage")
            cur.execute("rollback to savepoint newer_invalid_coverage")
            cur.execute("release savepoint newer_invalid_coverage")

            cur.execute("savepoint manifest_successor")
            cur.execute("alter table ops.siep_package_contract disable trigger siep_package_contract_append_only")
            cur.execute(
                """update ops.siep_package_contract
                      set rollback_contract=rollback_contract||%s
                    where package_key='44'""",
                (Jsonb({"successor_fixture": True}),),
            )
            cur.execute("alter table ops.siep_package_contract enable trigger siep_package_contract_append_only")
            successor_manifest = one(cur, "select ops.siep_manifest_digest()")[0]
            if successor_manifest == manifest:
                raise RuntimeError("manifest successor fixture did not change the canonical digest")
            successor_graph = one(cur, "select ops.siep_read_evidence_graph('06A')")[0]
            if not successor_graph["integrity"]["immutable_integrity_valid"] \
                    or successor_graph["integrity"]["manifest_mismatch_count"] != 1:
                raise RuntimeError("manifest successor mislabeled historical lineage as structural corruption")
            cur.execute("rollback to savepoint manifest_successor")
            cur.execute("release savepoint manifest_successor")

            cur.execute("update ops.job set mode='replay' where id=%s", (job_id,))
            stale_graph = one(cur, "select ops.siep_read_evidence_graph('06A')")[0]
            reasons = {reason for node in stale_graph["nodes"] if node["type"] == "evidence_link"
                       for reason in node["attributes"]["health_reasons"]}
            if "digest_mismatch" not in reasons or stale_graph["integrity"]["immutable_integrity_valid"]:
                raise RuntimeError("source digest mutation was not surfaced")
            cur.execute("update ops.job set mode='shadow' where id=%s", (job_id,))

            original_receipt_evidence = Jsonb({"status": "pass", "operation": "source",
                                               "commit_sha": "3" * 40,
                                               "target_ref": "https://signed.example/?token=extra-field-secret"})
            cur.execute("alter table ops.siep_evidence_link disable trigger siep_evidence_link_append_only")
            cur.execute("alter table ops.job_receipt disable trigger job_receipt_append_only")
            cur.execute("update ops.job_receipt set evidence=%s where id=%s",
                        (Jsonb({"status": "fail", "operation": "tests",
                                "commit_sha": "3" * 40, "target_ref": "purpose-secret"}), receipt_id))
            relabel_digest = one(
                cur, "select ops.siep_current_evidence_digest('job_receipt',%s)", (receipt_id,)
            )[0]
            cur.execute("update ops.siep_evidence_link set evidence_digest=%s where id=%s",
                        (relabel_digest, evidence_link_id))
            cur.execute("alter table ops.job_receipt enable trigger job_receipt_append_only")
            cur.execute("alter table ops.siep_evidence_link enable trigger siep_evidence_link_append_only")
            relabel_graph = one(cur, "select ops.siep_read_evidence_graph('06A')")[0]
            if relabel_graph["integrity"]["purpose_or_lineage_mismatch_count"] != 1 \
                    or relabel_graph["integrity"]["immutable_integrity_valid"]:
                raise RuntimeError("failed or relabeled receipt was presented as valid evidence")
            if "purpose-secret" in json.dumps(relabel_graph):
                raise RuntimeError("graph leaked forbidden material from a cross-purpose evidence field")
            cur.execute("alter table ops.siep_evidence_link disable trigger siep_evidence_link_append_only")
            cur.execute("alter table ops.job_receipt disable trigger job_receipt_append_only")
            cur.execute("update ops.job_receipt set evidence=%s where id=%s", (original_receipt_evidence, receipt_id))
            restored_digest = one(
                cur, "select ops.siep_current_evidence_digest('job_receipt',%s)", (receipt_id,)
            )[0]
            cur.execute("update ops.siep_evidence_link set evidence_digest=%s where id=%s",
                        (restored_digest, evidence_link_id))
            cur.execute("alter table ops.job_receipt enable trigger job_receipt_append_only")
            cur.execute("alter table ops.siep_evidence_link enable trigger siep_evidence_link_append_only")

            envelope_node_before = next(node["node_digest"] for node in graph["nodes"]
                                        if node["type"] == "engineering_envelope")
            cur.execute("alter table ops.engineering_execution_envelope disable trigger engineering_execution_envelope_append_only")
            cur.execute("update ops.engineering_execution_envelope set slice_ref='slice:siep-06a:cross-link' where id=%s",
                        (envelope_id,))
            cur.execute("alter table ops.engineering_execution_envelope enable trigger engineering_execution_envelope_append_only")
            cross_link_graph = one(cur, "select ops.siep_read_evidence_graph('06A')")[0]
            envelope_node_after = next(node["node_digest"] for node in cross_link_graph["nodes"]
                                       if node["type"] == "engineering_envelope")
            if cross_link_graph["integrity"]["purpose_or_lineage_mismatch_count"] != 1 \
                    or envelope_node_before == envelope_node_after:
                raise RuntimeError("cross-slice lineage mutation was not surfaced and digest-bound")
            cur.execute("alter table ops.engineering_execution_envelope disable trigger engineering_execution_envelope_append_only")
            cur.execute("update ops.engineering_execution_envelope set slice_ref='slice:siep-06a:source' where id=%s",
                        (envelope_id,))
            cur.execute("alter table ops.engineering_execution_envelope enable trigger engineering_execution_envelope_append_only")

            cur.execute("set session authorization carr_authority_joe")
            approval_event = one(
                cur, "select ops.siep_record_joe_decision('25','joe_approval','approved',%s)",
                (uuid.uuid4(),),
            )[0]
            cur.execute("reset session authorization")
            approval_digest = one(
                cur, "select ops.siep_current_evidence_digest('decision_event',%s)", (approval_event,)
            )[0]
            cur.execute("set session authorization carr_authority_joe")
            one(cur, "select ops.siep_attach_evidence('25','joe_approval',%s,'decision_event',%s,%s,%s)",
                (approval_event, approval_digest, "safe:siep-25:approval", uuid.uuid4()))
            cur.execute("reset session authorization")
            approved_graph = one(cur, "select ops.siep_read_evidence_graph('25')")[0]
            approved_attachment = next(item for item in approved_graph["attachments"]
                                       if item["evidence_kind"] == "joe_approval")
            if not approved_attachment["current"]:
                raise RuntimeError("fresh Joe approval was not current")
            cur.execute("set session authorization carr_authority_joe")
            one(cur, "select ops.siep_record_joe_decision('25','joe_approval','revoked',%s)",
                (uuid.uuid4(),))
            cur.execute("reset session authorization")
            revoked_graph = one(cur, "select ops.siep_read_evidence_graph('25')")[0]
            if revoked_graph["integrity"]["superseded_authority_count"] != 1 \
                    or not revoked_graph["integrity"]["immutable_integrity_valid"]:
                raise RuntimeError("later Joe revocation was not surfaced")
            decision_nodes = [node for node in revoked_graph["nodes"] if node["type"] == "decision"]
            if len(decision_nodes) < 2:
                raise RuntimeError("later Joe decision lineage was not projected")
            cur.execute("set session authorization carr_authority_joe")
            renewed_event = one(
                cur, "select ops.siep_record_joe_decision('25','joe_approval','approved',%s)",
                (uuid.uuid4(),),
            )[0]
            cur.execute("reset session authorization")
            renewed_digest = one(
                cur, "select ops.siep_current_evidence_digest('decision_event',%s)", (renewed_event,)
            )[0]
            cur.execute("set session authorization carr_authority_joe")
            one(cur, "select ops.siep_attach_evidence('25','joe_approval',%s,'decision_event',%s,%s,%s)",
                (renewed_event, renewed_digest, "safe:siep-25:renewed-approval", uuid.uuid4()))
            cur.execute("reset session authorization")
            renewed_graph = one(cur, "select ops.siep_read_evidence_graph('25')")[0]
            approval_coverage = next(item for item in renewed_graph["current_coverage"]
                                     if item["package_key"] == "25"
                                     and item["evidence_kind"] == "joe_approval")
            if not approval_coverage["covered"] or not renewed_graph["integrity"]["immutable_integrity_valid"]:
                raise RuntimeError("fresh replacement approval did not recover current purpose coverage")

            cur.execute("update public.actor set slug='joe-retired-fixture',active=false where id=%s", (joe_id,))
            successor_joe_id = one(
                cur,
                """insert into public.actor(slug,kind,display_name,email,active)
                     values ('joe','human','Joe authority successor',null,true) returning id""",
            )[0]
            rollover_graph = one(cur, "select ops.siep_read_evidence_graph('25')")[0]
            if not rollover_graph["integrity"]["immutable_integrity_valid"]:
                raise RuntimeError("Joe identity rollover mislabeled historical decisions as structural corruption")
            cur.execute("set session authorization carr_authority_joe")
            successor_event = one(
                cur, "select ops.siep_record_joe_decision('25','joe_approval','approved',%s)",
                (uuid.uuid4(),),
            )[0]
            cur.execute("reset session authorization")
            successor_digest = one(
                cur, "select ops.siep_current_evidence_digest('decision_event',%s)", (successor_event,)
            )[0]
            cur.execute("set session authorization carr_authority_joe")
            one(cur, "select ops.siep_attach_evidence('25','joe_approval',%s,'decision_event',%s,%s,%s)",
                (successor_event, successor_digest, "safe:siep-25:successor-approval", uuid.uuid4()))
            cur.execute("reset session authorization")
            successor_graph = one(cur, "select ops.siep_read_evidence_graph('25')")[0]
            successor_coverage = next(item for item in successor_graph["current_coverage"]
                                      if item["package_key"] == "25"
                                      and item["evidence_kind"] == "joe_approval")
            if not successor_coverage["covered"] or not successor_graph["integrity"]["immutable_integrity_valid"]:
                raise RuntimeError("fresh approval did not recover after Joe identity rollover")
            if successor_joe_id == joe_id:
                raise RuntimeError("Joe identity rollover did not use a distinct authority actor")

            set_local_role(cur, "carr_reader")
            if one(cur, "select has_function_privilege(current_user,'ops.siep_read_evidence_graph(text)','execute')")[0] is not True:
                raise RuntimeError("reader lacks typed graph access")
            if one(cur, "select has_table_privilege(current_user,'ops.siep_evidence_link','insert')")[0]:
                raise RuntimeError("graph read authority widened raw evidence DML")
    except Exception as exc:  # noqa: BLE001 - gate reports the exact assertion
        return fail(str(exc))
    print("siep evidence graph local acceptance passed: deterministic, redacted, exact lineage, and reasoned currentness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
