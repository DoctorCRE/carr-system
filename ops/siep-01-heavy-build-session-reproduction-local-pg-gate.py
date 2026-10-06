#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only SIEP-01 good-versus-half-executed reproduction."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, NoReturn

import psycopg
from psycopg.types.json import Jsonb

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gate_runtime_role import rollback_only_connection, set_local_role
from lib.rule_delivery_activation import EXPECTED_IDS


def load_program6_gate() -> Any:
    path = Path(__file__).with_name("program6-ready-plan-gate.py")
    spec = importlib.util.spec_from_file_location("siep01_program6_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load canonical Program 6 gate helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P6 = load_program6_gate()


def fail(message: str) -> int:
    print(f"siep-01-heavy-build-session-reproduction-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def refuse(message: str) -> NoReturn:
    raise RuntimeError(message)


def one(cur: psycopg.Cursor[Any], query: str, params: tuple[Any, ...] = ()) -> Any:
    row = cur.execute(query, params).fetchone()
    if row is None:
        refuse("required SIEP-01 fixture row was not returned")
    return row[0]


def sha(character: str) -> str:
    return "sha256:" + character * 64


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def rejected(
    cur: psycopg.Cursor[Any], query: str, params: tuple[Any, ...], expected: str
) -> None:
    cur.execute("savepoint siep_01_expected_refusal")
    try:
        cur.execute(query, params)
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint siep_01_expected_refusal")
        if expected.lower() not in str(exc).lower():
            refuse(f"expected {expected!r}, received {exc}")
        return
    cur.execute("rollback to savepoint siep_01_expected_refusal")
    refuse(f"expected refusal containing {expected!r}, but the operation succeeded")


# Since migration 0616 a NEW registration must be engineering-slice-plan.v2, so
# this reproduction registers the successor version with a closed V5-F03
# design_contract on every slice.  The contract's routing and authority describe
# exactly the server binding issue_envelope() writes below, because the receipt
# seam refuses a v2 slice whose contract contradicts its envelope.  Each slice
# declares its own resource: two parallel_safe slices naming one resource are a
# plan-wide contradiction the v2 validator refuses.
EXECUTION_ENVIRONMENT = "rehearsal"


def design_contract(slice_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": "engineering-design-contract.v1",
        "rationale": "The reproduction exercises the canonical ledgers end to end.",
        "dependency_rationale": "No accepted predecessor slice is required.",
        "code_model_decision": {
            "rationale": "Every step is enumerable ledger behavior, so deterministic code owns it.",
            "selection_basis": ["capability_gain", "quality_gain"],
            "model_judgment_steps": [],
        },
        "routing": {
            "executor_class": "deterministic_code",
            "adapter_ref": "adapter:codex-desktop",
            "fresh_session_required": True,
        },
        "authority": {
            "capability_profile": "capability:engineering-repository-write",
            "read_only": False,
            "environment": EXECUTION_ENVIRONMENT,
        },
        "isolation": {"worktree_required": True, "branch_required": True, "shared_resource_refs": []},
        "tests": {
            "planned_check_refs": [check["check_ref"] for check in slice_row["planned_checks"]],
            "verification_lanes": ["contract"],
        },
        "review": {"independent_review_required": True, "reviewer_class": "independent_agent"},
        "failure": {"failure_modes": [{
            "failure_ref": "failure:siep01-evidence-drift",
            "detection": "Canonical evidence does not match the observation.",
            "compensation": "Refuse closure and keep the Work Request unresolved.",
        }]},
        "evidence": {
            "redaction_class": "metadata_only",
            "retention": "material_redacted",
            "evidence_refs": [{"ref": "evidence:siep01:design", "redaction_class": "metadata_only",
                               "content_digest": sha("d")}],
        },
        "deployment": {"release_requirement": "not_required", "rollback_ref": None, "confirmation_required": False},
        "completion": {
            "completion_predicate": "Every planned check passes under independent review.",
            "verified_by": "independent_review",
        },
        "seam_decision": {
            "mode": "reuse",
            "target_seam_ref": "seam:engineering-passport",
            "measurement": {"basis": "complexity_reduction", "note": "The existing ledgers already carry this evidence."},
            "new_module_justification": None,
            "replaced_seam_refs": [],
            "residual_authority_refs": [],
        },
        "full_design_refs": None,
        "short_template": {
            "template_ref": "template:short-governed-v1",
            "objective_summary": "One bounded parallel-safe reproduction with no dependencies.",
            "verification_ref": "verification:short-governed-v1",
        },
    }


def typed_slice(slice_ref: str, ordinal: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "baseline_evidence_refs": [
            {
                "ref": f"evidence:siep01:{ordinal}",
                "content_digest": sha(str(ordinal)),
                "redaction_class": "metadata_only",
            }
        ],
        "concurrency_posture": "parallel_safe",
        "declared_component_refs": ["component:engineering-passport"],
        "declared_plan_step_refs": [f"step:siep01:{ordinal}"],
        "declared_resource_refs": [f"resource:canonical-ledgers:{ordinal}"],
        "definition_of_done": "The exact canonical execution evidence is read back.",
        "dependency_refs": [],
        "forbidden_change_refs": ["forbidden:production-mutation"],
        "manual_qa_required": False,
        "objective": "Reproduce one bounded execution history.",
        "ordinal": ordinal,
        "planned_checks": [
            {
                "check_ref": f"check:siep01:{ordinal}",
                "evidence_requirement": "metadata_only_sufficient",
                "failure_condition": "Canonical evidence does not match the observation.",
            }
        ],
        "release_requirement": "not_required",
        "risk_class": "R1",
        "scope_boundary": "Disposable source-test evidence only.",
        "slice_ref": slice_ref,
    }
    row["design_contract"] = design_contract(row)
    return row


def typed_receipt(
    slice_ref: str,
    ordinal: int,
    plan_digest: str,
    envelope_digest: str,
    session_id: uuid.UUID,
) -> dict[str, Any]:
    slice_spec = typed_slice(slice_ref, ordinal)
    evidence = {
        "content_digest": sha("f"),
        "redaction_class": "metadata_only",
        "ref": f"evidence:siep01:receipt:{ordinal}",
    }
    check_ref = slice_spec["planned_checks"][0]["check_ref"]
    return {
        "actual_component_refs": list(slice_spec["declared_component_refs"]),
        "actual_resource_refs": list(slice_spec["declared_resource_refs"]),
        "artifact_refs": [f"artifact:siep01:{ordinal}"],
        "attribution": {
            "actor_ref": "agent:codex",
            "adapter_ref": "adapter:codex-desktop",
            "session_ref": f"session:{session_id}",
        },
        "attempt_id": "attempt:1",
        "checks": [{"check_ref": check_ref, "evidence_refs": [evidence], "state": "passed"}],
        "deviations": [],
        "envelope_digest": envelope_digest,
        "evidence_refs": [evidence],
        "executor_claim": {
            "claim_state": "executor_claim",
            "claimed_at": "2026-08-26T00:00:00Z",
            "claimed_by": "codex",
        },
        "independent_verification_required": True,
        "outcome": "claimed_complete",
        "plan_digest": plan_digest,
        "planned_component_refs": list(slice_spec["declared_component_refs"]),
        "planned_resource_refs": list(slice_spec["declared_resource_refs"]),
        "reset_reconstruction": {
            "fresh_session": True,
            "inherited_transcript_used": False,
            "reconstruction_free": True,
            "remediation_action": None,
        },
        "schema_version": "engineering-slice-receipt.v1",
        "slice_ref": slice_ref,
        "source_evidence": {
            "branch_ref": "branch:siep01",
            "evidence_refs": [evidence],
            "source_sha": "1" * 40,
            "worktree_ref": "worktree:siep01",
        },
    }


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            P6.ensure_authority_roles(cur)
            joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")
            executor_id = one(cur, "select id from actor where slug='codex' and active")
            source_section, source_revision, origin_ref, _, _, runbook_ref = P6.doctrine_fixture(cur, joe_id)
            rules_before = cur.execute(
                """select (select mode from ops.rule_delivery_policy where singleton),
                          (select count(*) from ops.rule_delivery_activation_target),
                          (select array_agg(short_id order by short_id)
                             from ops.rule_delivery_activation_target)"""
            ).fetchone()

            set_local_role(cur, "carr_writer")
            work_request_id, work_request_ref, _, captured_version = P6.capture(
                cur,
                source_section,
                source_revision,
                origin_ref,
                "SIEP-01 paired reproduction",
                title="Build a new governed heavy-build execution system",
                desired="Implement the complete end-to-end platform and prove truthful execution closure",
                criteria=[
                    {"id": f"SIEP01-{n}", "text": f"Heavy-build evidence condition {n} is independently verified"}
                    for n in range(1, 6)
                ],
            )
            cur.execute("reset role")
            triaged = P6.triage(cur, work_request_ref, captured_version, "joe")
            triaged_version = triaged[3]

            set_local_role(cur, "carr_writer")
            shaped = cur.execute(
                """select * from ops.set_sourced_work_request_shape_disposition(
                     %s,%s,'required',null,%s,%s,%s)""",
                (
                    work_request_ref,
                    triaged_version,
                    "Good and half-executed sibling histories require an explicit paired execution shape.",
                    joe_id,
                    uuid.uuid4(),
                ),
            ).fetchone()
            cur.execute("reset role")
            if shaped is None or shaped[4] != "required":
                refuse(f"SIEP-01 shape disposition was not recorded: {shaped}")
            shaped_version = shaped[3]
            cur.execute(
                """insert into ops.work_shape_revision
                     (work_request_id,work_request_version,version,trinity,hidden_assumption,
                      repo_searches,maintained_repos,archetypes,chosen_key,mind_changing_fact,
                      builder_brief,created_by_actor_id)
                   values (%s,%s,1,%s,%s,%s,%s,%s,'paired',%s,%s,%s)""",
                (
                    work_request_id,
                    shaped_version,
                    Jsonb({"workflow_trigger": "heavy request", "runtime": "CARR", "output_user": "Joe"}),
                    "A generic succeeded job may be mistaken for verified engineering completion.",
                    Jsonb(["engineering passport completion", "heavy-build admission"]),
                    Jsonb([{"url": "https://github.com/example/reproduction", "maintenance_evidence": "fixture"}]),
                    Jsonb([
                        {"key": "paired", "core_assumption": "hold plan and executor constant"},
                        {"key": "separate", "core_assumption": "use separate Work Requests"},
                        {"key": "mocked", "core_assumption": "mock the evidence reads"},
                    ]),
                    "A canonical receipt that permits generic job success alone to close the Passport.",
                    Jsonb({"chosen_shape": "paired", "text": "Compare sibling slices under one accepted plan."}),
                    joe_id,
                ),
            )

            proposal_key = uuid.uuid4()
            set_local_role(cur, "carr_writer")
            proposal = P6.propose(
                cur,
                work_request_ref,
                shaped_version,
                runbook_ref,
                proposal_key,
                "Reproduce good and half-executed sibling slices under one accepted heavy plan",
            )
            cur.execute("reset role")
            plan_row = cur.execute(
                """select id,plan_hash,scope_summary,dependency_refs,caps
                     from ops.sourced_work_request_plan where id=%s""",
                (proposal[0],),
            ).fetchone()
            if plan_row is None:
                refuse("accepted-plan proposal row disappeared")
            set_local_role(cur, "carr_writer")
            classification = cur.execute(
                "select * from ops.classify_sourced_work_request_build(%s,%s,%s,%s,%s)",
                (
                    work_request_ref,
                    shaped_version,
                    plan_row[2],
                    Jsonb(plan_row[3]),
                    Jsonb(plan_row[4]),
                ),
            ).fetchone()
            if classification is None or classification[2] != "heavy":
                refuse(f"0320 did not classify the paired reproduction as heavy: {classification}")
            admission = cur.execute(
                """select * from ops.record_sourced_heavy_build_admission(
                     %s,%s,%s,%s,%s,%s,%s)""",
                (
                    plan_row[0],
                    work_request_ref,
                    shaped_version,
                    Jsonb(classification[3]),
                    Jsonb(P6.heavy_contract()),
                    executor_id,
                    proposal_key,
                ),
            ).fetchone()
            if admission is None:
                refuse("0320 did not record the typed heavy admission")
            review = cur.execute(
                """select * from ops.review_sourced_heavy_build_plan(
                     %s,%s,%s,%s,'pass',%s,%s,%s,%s,%s)""",
                (
                    work_request_ref,
                    plan_row[1],
                    admission[5],
                    joe_id,
                    "session:siep01:fresh-independent-review",
                    "Fresh independent review accepted the exact paired reproduction plan.",
                    Jsonb(["safe:review:siep-01-paired"]),
                    Jsonb([]),
                    uuid.uuid4(),
                ),
            ).fetchone()
            cur.execute("reset role")
            if review is None or review[7] != "pass":
                refuse(f"0320 fresh review was not accepted: {review}")

            accepted = P6.accept(cur, work_request_ref, shaped_version, plan_row[1], uuid.uuid4(), "joe")
            if accepted[2] != "ready":
                refuse(f"heavy plan did not reach ready: {accepted}")

            source = one(
                cur,
                "select ops.engineering_admission_source(%s)",
                (work_request_ref,),
            )
            slice_refs = ["slice:siep01:good", "slice:siep01:half"]
            plan_preimage = {
                "accepted_plan_revision": {
                    "id": source["accepted_plan"]["plan_ref"],
                    "revision": int(source["accepted_plan"]["revision"]),
                    "digest": source["accepted_plan"]["digest"],
                },
                "schema_version": "engineering-slice-plan.v2",
                "slices": [typed_slice(ref, index + 1) for index, ref in enumerate(slice_refs)],
                "work_request": {
                    "id": source["work_request"]["id"],
                    "state_version": int(source["work_request"]["version"]),
                    "canonical_record_digest": source["work_request"]["canonical_record_digest"],
                },
            }
            plan_digest = canonical_digest(plan_preimage)
            engineering_plan = {**plan_preimage, "plan_digest": plan_digest}

            set_local_role(cur, "carr_writer")
            slice_plan_id = one(
                cur,
                "select (ops.engineering_register_slice_plan(%s,%s,%s,%s)).id",
                (work_request_ref, Jsonb(engineering_plan), plan_digest, uuid.uuid4()),
            )
            jobs: dict[str, uuid.UUID] = {}
            envelopes: dict[str, tuple[uuid.UUID, str]] = {}
            sessions: dict[str, uuid.UUID] = {}
            for index, slice_ref in enumerate(slice_refs, start=1):
                job_id = one(
                    cur,
                    "select (ops.engineering_enqueue_slice_job(%s,%s,%s,%s,%s)).id",
                    (work_request_ref, slice_ref, plan_digest, str(uuid.uuid4()), 1),
                )
                jobs[slice_ref] = job_id
                if index == 1:
                    cur.execute("reset role")
                    cur.execute(
                        "update ops.job set scheduled_for=scheduled_for-interval '1 second' where id=%s",
                        (job_id,),
                    )
                    set_local_role(cur, "carr_writer")

            def issue_envelope(slice_ref: str, index: int) -> tuple[uuid.UUID, str]:
                session_id = one(
                    cur,
                    """insert into ops.capability_agent_session
                         (work_request_id,executor_actor_id,created_by_actor_id,
                          source_commit_sha,worktree_ref,scope_ref,lease_expires_at)
                       values (%s,%s,%s,%s,'engineering:server-admission',%s,
                               date_trunc('second',now())+interval '29 minutes')
                       returning id""",
                    (work_request_id, executor_id, joe_id, "0" * 40, f"slice:{slice_ref}"),
                )
                sessions[slice_ref] = session_id
                issued_at = one(
                    cur,
                    """select to_char(date_trunc('second',now()) at time zone 'UTC',
                                      'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')""",
                )
                expires_at = one(
                    cur,
                    """select to_char(lease_expires_at at time zone 'UTC',
                                      'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')
                         from ops.capability_agent_session where id=%s""",
                    (session_id,),
                )
                job_id = jobs[slice_ref]
                envelope_digest = sha(str(index + 3))
                envelope_id = uuid.uuid4()
                envelope = {
                    "schema_version": "execution-envelope.v1",
                    "envelope_id": f"env:{envelope_id}",
                    "work_request_id": f"wr:{work_request_id}",
                    "issued_at": issued_at,
                    "expires_at": expires_at,
                    "agent_session": {
                        "id": f"session:{session_id}",
                        "lease_expires_at": expires_at,
                    },
                    "state_binding": {
                        "state_version": int(source["work_request"]["version"]),
                        "canonical_record_digest": source["work_request"]["canonical_record_digest"],
                    },
                    "plan_revision": {
                        "id": source["accepted_plan"]["plan_ref"],
                        "revision": int(source["accepted_plan"]["revision"]),
                        "digest": source["accepted_plan"]["digest"],
                    },
                    "phase_binding": {"phase_id": f"phase:{slice_ref}"},
                    "request": {
                        "job_ref": f"job:{job_id}",
                        "input_digest": sha("8"),
                        "allowed_actions": [
                            "repository:create-worktree",
                            "repository:create-branch",
                            "repository:write-declared-scope",
                            "repository:run-checks",
                            "repository:commit",
                            "repository:push-branch",
                            "repository:open-pr",
                        ],
                    },
                    "server_binding": {
                        "authority": {
                            "read_only": False,
                            "capability_profile": "capability:engineering-repository-write",
                            "environment": EXECUTION_ENVIRONMENT,
                        },
                        "adapter": {
                            "surface": "codex_desktop",
                            "adapter_id": "adapter:codex-desktop",
                        },
                        "identity": {
                            "agent_principal_id": "agent:codex",
                            "runtime_principal": "runtime:codex",
                        },
                    },
                }
                envelope_id = one(
                    cur,
                    """insert into ops.engineering_execution_envelope
                         (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,
                          agent_session_id,state_version,canonical_record_digest,
                          envelope_digest,envelope,issued_at,expires_at)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s::timestamptz,%s::timestamptz)
                       returning id""",
                    (
                        envelope_id,
                        job_id,
                        work_request_id,
                        plan_row[0],
                        slice_plan_id,
                        slice_ref,
                        session_id,
                        int(source["work_request"]["version"]),
                        source["work_request"]["canonical_record_digest"],
                        envelope_digest,
                        Jsonb(envelope),
                        issued_at,
                        expires_at,
                    ),
                )
                envelopes[slice_ref] = (envelope_id, envelope_digest)
                return envelope_id, envelope_digest

            good_ref, half_ref = slice_refs
            issue_envelope(good_ref, 1)
            cur.execute("reset role")

            set_local_role(cur, "carr_jobs")
            good_claim = cur.execute(
                "select * from ops.engineering_claim_slice('siep-01-worker-good',1,960)"
            ).fetchone()
            if good_claim is None or good_claim[0] != jobs[good_ref]:
                currentness = one(
                    cur,
                    "select ops.engineering_envelope_currentness(%s,%s)",
                    (envelopes[good_ref][0], jobs[good_ref]),
                )
                refuse(f"0323 did not claim the good paired job: {currentness}")
            claims = {good_ref: good_claim}
            rejected(
                cur,
                "update ops.job set state='succeeded' where id=%s",
                (jobs[slice_refs[0]],),
                "permission denied",
            )

            good_envelope_id, good_envelope_digest = envelopes[good_ref]
            wrong_receipt = typed_receipt(
                good_ref, 1, plan_digest, sha("0"), sessions[good_ref]
            )
            rejected(
                cur,
                "select ops.engineering_finalize_slice_receipt(%s,%s,%s,%s,%s)",
                (
                    good_envelope_id,
                    good_claim[1],
                    Jsonb(wrong_receipt),
                    canonical_digest(wrong_receipt),
                    executor_id,
                ),
                "not bound",
            )
            good_receipt_body = typed_receipt(
                good_ref, 1, plan_digest, good_envelope_digest, sessions[good_ref]
            )
            good_receipt_digest = canonical_digest(good_receipt_body)
            good_receipt_id = one(
                cur,
                "select (ops.engineering_finalize_slice_receipt(%s,%s,%s,%s,%s)).id",
                (
                    good_envelope_id,
                    good_claim[1],
                    Jsonb(good_receipt_body),
                    good_receipt_digest,
                    executor_id,
                ),
            )
            cur.execute("reset role")
            set_local_role(cur, "carr_writer")
            issue_envelope(half_ref, 2)
            cur.execute("reset role")
            set_local_role(cur, "carr_jobs")
            half_claim = cur.execute(
                "select * from ops.engineering_claim_slice('siep-01-worker-half',1,960)"
            ).fetchone()
            if half_claim is None or half_claim[0] != jobs[half_ref]:
                currentness = one(
                    cur,
                    "select ops.engineering_envelope_currentness(%s,%s)",
                    (envelopes[half_ref][0], jobs[half_ref]),
                )
                refuse(f"0323 did not claim the half-executed paired job: {currentness}")
            claims[half_ref] = half_claim
            rejected(
                cur,
                "select ops.complete_job(%s,%s,%s,%s)",
                (
                    jobs[half_ref],
                    half_claim[1],
                    Jsonb({"operation": "caller-labelled-complete", "status": "pass"}),
                    f"siep-01:{half_ref}:generic-completion",
                ),
                "engineering jobs require scoped controller functions",
            )
            # The current controller refuses this state transition. Recreate
            # the historical half-executed ledger shape under the rollback-only
            # fixture owner so the original comparison remains observable.
            cur.execute("reset role")
            cur.execute(
                """update ops.job_attempt
                      set state='succeeded',ended_at=now()
                    where job_id=%s and attempt=1 and lease_token=%s and state='running'""",
                (jobs[half_ref], half_claim[1]),
            )
            cur.execute(
                """update ops.job
                      set state='succeeded',ended_at=now(),lease_owner=null,lease_token=null,
                          leased_until=null,updated_at=now()
                    where id=%s and state='running'""",
                (jobs[half_ref],),
            )
            cur.execute(
                """insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
                   values (%s,1,'completion',%s,%s)""",
                (
                    jobs[half_ref],
                    f"siep-01:{half_ref}:historical-generic-completion",
                    Jsonb(
                        {
                            "operation": "caller-labelled-complete",
                            "status": "pass",
                            "fixture_posture": "historical_state_current_runtime_refused",
                        }
                    ),
                ),
            )

            set_local_role(cur, "carr_writer")
            reviewer_fact_id = one(
                cur,
                """insert into ops.engineering_reviewer_fact
                     (receipt_id,work_request_id,slice_ref,reviewer_actor_id,
                      reviewer_session_ref,state,fact,idempotency_key)
                   values (%s,%s,%s,%s,'session:siep01:independent-completion-review',
                           'passed',%s,%s) returning id""",
                (
                    good_receipt_id,
                    work_request_id,
                    good_ref,
                    joe_id,
                    Jsonb(
                        {
                            "attempt_id": "attempt:1",
                            "evidence_refs": [
                                {
                                    "content_digest": sha("c"),
                                    "redaction_class": "metadata_only",
                                    "ref": "evidence:siep01:independent-review",
                                }
                            ],
                            "is_independent": True,
                            "resolved_deviation_refs": [],
                            "reviewed_deviation_refs": [],
                            "reviewer_ref": "reviewer:joe",
                            "session_ref": "session:siep01:independent-completion-review",
                            "slice_ref": good_ref,
                            "state": "passed",
                        }
                    ),
                    uuid.uuid4(),
                ),
            )
            cur.execute("reset role")

            set_local_role(cur, "carr_reader")
            passport = one(
                cur,
                "select ops.engineering_passport_facts(%s)",
                (work_request_ref,),
            )
            job_rows = cur.execute(
                """select j.id,j.state,j.attempt,ja.id,ja.state,jr.id,jr.receipt_ref,
                          e.id,e.envelope_digest,sr.id,sr.receipt_digest,sr.outcome,
                          rf.id,rf.state,rf.reviewer_actor_id,e.slice_ref
                     from ops.job j
                     join ops.job_attempt ja on ja.job_id=j.id and ja.attempt=j.attempt
                     join ops.job_receipt jr on jr.job_id=j.id and jr.attempt=j.attempt
                     join ops.engineering_execution_envelope e on e.job_id=j.id
                     left join ops.engineering_slice_receipt sr on sr.envelope_id=e.id
                     left join ops.engineering_reviewer_fact rf on rf.receipt_id=sr.id
                    where j.id=any(%s)
                    order by e.slice_ref""",
                (list(jobs.values()),),
            ).fetchall()
            cur.execute("reset role")
            if len(job_rows) != 2:
                refuse(f"paired canonical readback returned {len(job_rows)} rows")

            observations: list[dict[str, Any]] = []
            for row in job_rows:
                slice_ref = row[15]
                fully_observed = slice_ref == good_ref
                observation = {
                    "case": "fully_observed" if fully_observed else "half_executed",
                    "slice_ref": slice_ref,
                    "capability_session_id": str(sessions[slice_ref]),
                    "executor_actor_id": str(executor_id),
                    "job": {"id": str(row[0]), "state": row[1], "attempt": row[2]},
                    "job_attempt": {"id": str(row[3]), "state": row[4], "attempt": row[2]},
                    "generic_completion_receipt": {"id": str(row[5]), "ref": row[6]},
                    "envelope": {"id": str(row[7]), "digest": row[8]},
                    "engineering_receipt": (
                        {"id": str(row[9]), "digest": row[10], "outcome": row[11]}
                        if row[9] is not None else None
                    ),
                    "reviewer_fact": (
                        {"id": str(row[12]), "state": row[13], "reviewer_actor_id": str(row[14])}
                        if row[12] is not None else None
                    ),
                    "passport_slice_state": "verified_complete" if fully_observed else "eligible",
                }
                observations.append(observation)

            by_case = {item["case"]: item for item in observations}
            good = by_case["fully_observed"]
            half = by_case["half_executed"]
            if good["job"]["state"] != "succeeded" or half["job"]["state"] != "succeeded":
                refuse(f"generic jobs did not both succeed: {observations}")
            if good["engineering_receipt"] is None or good["reviewer_fact"] is None:
                refuse(f"good execution lost typed evidence: {good}")
            if good["reviewer_fact"]["reviewer_actor_id"] == good["executor_actor_id"]:
                refuse(f"good execution used self-review: {good}")
            if half["engineering_receipt"] is not None or half["reviewer_fact"] is not None:
                refuse(f"half execution unexpectedly acquired typed evidence: {half}")
            passport_receipt_refs = {item["slice_ref"] for item in passport["receipts"]}
            passport_review_refs = {item["slice_ref"] for item in passport["reviewer_facts"]}
            if good_ref not in passport_receipt_refs or good_ref not in passport_review_refs:
                refuse("Passport readback lost the good execution evidence")
            if half_ref in passport_receipt_refs or half_ref in passport_review_refs:
                refuse("generic completion leaked into typed Passport evidence")
            if reviewer_fact_id != uuid.UUID(good["reviewer_fact"]["id"]):
                refuse("reviewer fact readback changed identity")
            first_digest = canonical_digest(observations)
            second_digest = canonical_digest(json.loads(json.dumps(observations)))
            if first_digest != second_digest:
                refuse("repeated SIEP-01 observation readback changed digest")

            rules_after = cur.execute(
                """select (select mode from ops.rule_delivery_policy where singleton),
                          (select count(*) from ops.rule_delivery_activation_target),
                          (select array_agg(short_id order by short_id)
                             from ops.rule_delivery_activation_target)"""
            ).fetchone()
            if (
                rules_before != rules_after
                or rules_after[0] != "shadow"
                or rules_after[1] != len(EXPECTED_IDS)
                or list(rules_after[2]) != sorted(EXPECTED_IDS)
            ):
                refuse(f"SIEP-01 touched 0321 scoped delivery state: {rules_before} -> {rules_after}")

        print(
            "siep-01-heavy-build-session-reproduction-local-pg-gate passed: "
            "both jobs succeeded, only typed independent evidence verified the good slice, "
            f"half execution remained unresolved; observation_digest={first_digest}"
        )
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
