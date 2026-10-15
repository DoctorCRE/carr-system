#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import activation_reliability as ar  # noqa: E402
import execution_contract as contract  # noqa: E402
import execution_environment as environment  # noqa: E402

FIXTURES = ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric"


def envelope():
    return json.loads((FIXTURES / "codex_desktop.execution-envelope.v1.json").read_text())


def bundle():
    env = envelope()
    header = {
        "tenant_id": "tenant:carr-synthetic",
        "work_request_id": env["work_request_id"],
        "accepted_plan_revision_id": env["plan_revision"]["id"],
        "accepted_plan_revision": env["plan_revision"]["revision"],
        "accepted_plan_digest": env["plan_revision"]["digest"],
        "issued_at": "2026-08-24T12:00:00Z",
        "mode": "canary",
        "retrieval_policy": "policy:bounded-doctrine-v1",
        "retrieval_policy_version": "v1",
    }
    items = [
        {"kind": "rule", "canonical_ref": "rule:scope", "revision": "rev:r1", "digest": "sha256:" + "1" * 64, "required": True, "trigger": "work-request-admission", "consumer": "executor", "enforcement": "must-apply", "redaction_class": "metadata_only", "freshness": "fresh"},
        {"kind": "memory", "canonical_ref": "memory:prior", "revision": "rev:r4", "digest": "sha256:" + "2" * 64, "required": False, "trigger": "work-request-admission", "consumer": "executor", "enforcement": "advisory-only", "redaction_class": "metadata_only", "freshness": "fresh"},
    ]
    return ar.make_context_bundle(header, items)


def activation(mode="canary"):
    b = bundle()
    return {
        "bundle_digest": b["bundle_digest"],
        "canonical_binding": {"work_request_id": "wr-synthetic-read-only", "work_request_version": 1, "accepted_plan_digest": "sha256:" + "a" * 64, "envelope_digest": "sha256:" + "0" * 64, "activation_binding_ref": "ctx:synthetic"},
        "item_dispositions": [
            {"item_ref": "rule:scope", "disposition": "applied", "evidence_refs": ["evidence:rule"], "stage_ref": "stage:retrieve", "tool_ref": "tool:context", "reason_ref": "reason:applied"},
            {"item_ref": "memory:prior", "disposition": "not_applicable", "evidence_refs": [], "reason_ref": "reason:not-applicable"},
        ],
        "closure": {"state": "closed" if mode == "canary" else "not_activated", "unresolved_required_item_refs": [], "derived_by": "server"},
        "mode": mode,
    }


def bound_envelope(b):
    env = envelope()
    env["activation_binding"] = {"bundle_digest": b["bundle_digest"], "item_refs": ["rule:scope", "memory:prior"], "mode": "canary", "retrieval_policy_version": "v1"}
    env["context_activation_ref"] = "ctx:synthetic"
    env["reliability_policy_binding"] = {"policy_ref": "policy:reliability-v1", "policy_digest": "sha256:" + "4" * 64, "risk_class": "R2", "mode": "canary"}
    environment_binding = {"provider_ref": "environment-provider:hermes-local:v1", "provider_version": 1, "provider_digest": "sha256:" + "1" * 64, "requirement_digest": "sha256:" + "2" * 64, "configuration_digest": "sha256:" + "3" * 64, "backend_kind": "local", "source_class": "built_in", "isolation_class": "host_process", "capability_refs": ["environment:exec", "environment:filesystem", "environment:process"], "conformance_ref": "conformance-run:hermes-local-v1", "conformance_digest": "sha256:" + "4" * 64}
    environment_binding["binding_digest"] = environment.environment_binding_digest(environment_binding)
    env["runtime_profile"] = {"ref": "profile:runtime-v1", "digest": "sha256:" + "5" * 64, "profile_key": "profile:runtime", "profile_version": 1, "provider_id": "provider:synthetic", "model_id": "model:synthetic", "desk": "desk:synthetic", "policy_ref": "policy:reliability-v1", "policy_digest": "sha256:" + "4" * 64, "modality": "modality:text", "reasoning_effort_ref": "effort:standard", "sampling_profile_ref": "sampling:fixed", "context_budget": 8192, "cache_policy_ref": "cache:bounded", "knowledge_cutoff_posture": "cutoff:declared", "tool_calling_mode": "tool-mode:governed", "environment_provider_ref": environment_binding["provider_ref"], "environment_provider_version": environment_binding["provider_version"], "environment_provider_digest": environment_binding["provider_digest"], "environment_requirement_digest": environment_binding["requirement_digest"], "environment_configuration_digest": environment_binding["configuration_digest"], "environment_backend_kind": environment_binding["backend_kind"], "environment_source_class": environment_binding["source_class"], "environment_isolation_class": environment_binding["isolation_class"], "environment_capability_refs": environment_binding["capability_refs"], "environment_conformance_ref": environment_binding["conformance_ref"], "environment_conformance_digest": environment_binding["conformance_digest"], "environment_binding_digest": environment_binding["binding_digest"]}
    env["execution_topology"] = {"ref": "topology:single-agent-v1", "digest": "sha256:" + "6" * 64, "kind": "single_agent_loop", "harness_digest": "sha256:" + "8" * 64, "parallelism": "sequential", "code_model_step_refs": ["step:model"], "fallback_policy_ref": "fallback:stop", "stop_condition_refs": ["stop:complete"], "context_refresh_policy_ref": "refresh:bounded", "memory_policy_ref": "memory:context-only", "sandbox_ref": "sandbox:metadata", "guardrail_ref": "guardrail:default", "threat_model_ref": "threat:default"}
    env["evaluation_plan"] = {"ref": "plan:evaluation-v1", "digest": "sha256:" + "7" * 64, "lane_ref": "lane:synthetic", "risk_class": "R2", "rubric_digest": "sha256:" + "9" * 64, "case_set_digest": "sha256:" + "a" * 64, "evaluator_policy_digest": "sha256:" + "b" * 64, "evaluator_ref": "evaluator:authority", "rubric_ref": "rubric:synthetic", "evaluator_version": "version:v1", "evaluator_digest": "sha256:" + "c" * 64, "required_rungs": ["rung:smoke", "rung:regression"], "required_deterministic_check_refs": ["check:binding"], "critical_dimensions": ["dimension:correctness"], "human_acceptance_required": True, "outcome_horizon_ref": "horizon:synthetic", "outcome_horizon_not_before": "2026-08-24T12:00:00Z", "requirements": {"required_evaluator_kinds": ["deterministic", "judge", "human_acceptance"], "minimum_held_out_case_count": 1, "minimum_calibration_ref_count": 1, "maximum_critical_failure_count": 0, "maximum_critical_failure_rate": 0, "confidence_posture": "lower_bound_required", "drift_tolerance": "no_critical_regression", "independent_review_required": True, "human_acceptance_required": True, "outcome_horizon_required": True}}
    return env


def reliability():
    environment_binding = bound_envelope(bundle())["runtime_profile"]
    return {
        "route_digest": "sha256:" + "5" * 64, "topology_digest": "sha256:" + "6" * 64, "evaluation_plan_digest": "sha256:" + "7" * 64,
        "grounding_sufficiency": {"state": "sufficient", "evidence_refs": ["evidence:grounding"], "required_supplied": ["rule:scope"], "required_used": ["rule:scope"], "required_missing": [], "advisory_supplied": ["memory:prior"], "advisory_used": [], "freshness_failures": [], "retrieval_failures": []},
        "deterministic_checks": [{"check_id": "check:binding", "state": "passed", "critical": True, "evidence_refs": ["evidence:check"]}],
        "model_judgement": {"state": "pass", "judge_ref": "actor:model-judge", "evidence_refs": ["evidence:judge"]},
        "human_acceptance": {"state": "accepted", "actor_ref": "actor:joe", "evidence_refs": ["evidence:human"], "outcome_feedback_ref": "OUTCOME-abcdef123456-v1", "outcome_feedback_hash": "sha256:" + "f" * 64},
        "trajectory": [{"sequence": 1, "stage_ref": "stage:execute", "parent_event_ref": None, "decision_class": "decision:execute", "tool_class": "tool:metadata", "result_state": "succeeded", "fallback_state": "not_used", "guardrail_state": "clear", "latency_ms": 1, "evidence_refs": ["evidence:trace"]}],
        "evaluator_results": [{"kind": "deterministic", "evaluator_ref": "evaluator:deterministic", "rubric_ref": "rubric:synthetic", "evaluator_version": "v1", "evaluator_digest": "sha256:" + "c" * 64, "status": "passed", "confidence": "high", "critical": True, "independence_state": "not_independent", "held_out_case_count": 1, "check_refs": ["check:binding"], "dimension_refs": ["dimension:correctness"], "evidence_refs": ["evidence:deterministic"], "judge_provenance": "provenance:deterministic", "calibration_evidence_refs": []}, {"kind": "judge", "evaluator_ref": "evaluator:judge", "rubric_ref": "rubric:synthetic", "evaluator_version": "v1", "evaluator_digest": "sha256:" + "d" * 64, "status": "passed", "confidence": "high", "critical": False, "independence_state": "not_independent", "held_out_case_count": 1, "check_refs": [], "dimension_refs": ["dimension:correctness"], "evidence_refs": ["evidence:judge"], "judge_provenance": "provenance:judge", "calibration_evidence_refs": ["evidence:calibration"]}, {"kind": "human_acceptance", "evaluator_ref": "evaluator:human", "rubric_ref": "rubric:synthetic", "evaluator_version": "v1", "evaluator_digest": "sha256:" + "e" * 64, "status": "passed", "confidence": "high", "critical": False, "independence_state": "not_independent", "held_out_case_count": 1, "check_refs": [], "dimension_refs": ["dimension:correctness"], "evidence_refs": ["evidence:human"], "judge_provenance": "provenance:human", "calibration_evidence_refs": []}],
        "corrections": [], "defects": [], "incidents": [],
        "downstream_outcome": {"state": "observed", "brokerage_ref": "deal:synthetic", "evidence_refs": ["evidence:outcome"], "outcome_feedback_ref": "OUTCOME-abcdef123456-v1", "outcome_feedback_hash": "sha256:" + "f" * 64},
        "outcome_horizon": {"state": "mature", "ends_at": "2026-08-24T12:00:00Z", "as_of": "2026-08-24T12:00:00Z", "evidence_refs": ["evidence:horizon"]},
        "process_metrics": {"latency_ms": 1, "cost_usd": 0, "input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0, "retry_count": 0, "recovery_count": 0, "context_reconstruction_ms": 0, "human_intervention_count": 0, "security_event_refs": []},
        "eval_candidates": [],
        "shadow_comparisons": [],
        "environment_binding_digest": environment_binding["environment_binding_digest"],
        "environment_evidence": {"binding_digest": environment_binding["environment_binding_digest"], "session_ref": "environment-session:synthetic", "lease_state": "released", "operation_count": 1, "policy_refusal_refs": [], "security_event_refs": [], "cleanup_state": "verified", "cleanup_evidence_refs": ["evidence:cleanup"], "side_effect_state": "none", "resource_usage": {"cpu_ms": 1, "memory_peak_mb": 1, "disk_peak_mb": 0, "network_egress_bytes": 0}, "evidence_refs": ["evidence:environment"]},
        "telemetry": [],
        "learning_disposition": "none", "closure": {"state": "insufficient_evidence", "reasons": ["reason:authority_evaluation_evidence_missing"], "derived_by": "server"},
    }


def expect_refusal(fn, fragment):
    try:
        fn()
    except (ar.ActivationReliabilityError, contract.ContractError) as exc:
        assert fragment in str(exc), exc
        return
    raise AssertionError("expected refusal")


def test_bundle_is_bounded_and_deterministic():
    first = bundle()
    shuffled = copy.deepcopy(first)
    shuffled["items"] = list(reversed(shuffled["items"]))
    expect_refusal(lambda: ar.validate_context_bundle(shuffled), "does not bind")
    assert first["bundle_digest"] == ar.validate_context_bundle(first)["bundle_digest"]
    reissued = copy.deepcopy(first)
    reissued["header"]["issued_at"] = "2026-08-25T12:00:00Z"
    assert ar.validate_context_bundle(reissued)["bundle_digest"] == first["bundle_digest"]
    oversized = copy.deepcopy(first)
    oversized["items"] = oversized["items"] * 33
    oversized["bundle_digest"] = ar.context_bundle_digest(oversized)
    expect_refusal(lambda: ar.validate_context_bundle(oversized), "1..64")


def test_required_omission_stale_conflict_block_and_advisory_not_applicable():
    b = bundle()
    value = activation()
    value["item_dispositions"] = value["item_dispositions"][:1]
    expect_refusal(lambda: ar.validate_knowledge_activation(value, b), "every bundle item")
    value = activation()
    value["item_dispositions"][0]["disposition"] = "stale"
    value["closure"] = {"state": "blocked", "unresolved_required_item_refs": ["rule:scope"], "derived_by": "server"}
    ar.validate_knowledge_activation(value, b)
    assert value["item_dispositions"][1]["disposition"] == "not_applicable"
    value = activation()
    value["item_dispositions"][0].pop("stage_ref")
    value["item_dispositions"][0].pop("tool_ref")
    expect_refusal(lambda: ar.validate_knowledge_activation(value, b), "stage/tool link")


def test_bundle_cross_binding_and_plan_memory_authority():
    b = bundle()
    wrong = copy.deepcopy(b)
    wrong["header"]["work_request_id"] = "wr-other"
    wrong["bundle_digest"] = ar.context_bundle_digest(wrong)
    expect_refusal(lambda: ar.validate_context_bundle(wrong, binding={"work_request_id": "wr-synthetic-read-only"}), "does not bind canonical plan")
    env = bound_envelope(b)
    contract.validate_execution_envelope(env)
    ar.validate_envelope_bindings(env, bundle=b)
    env["activation_binding"]["item_refs"] = ["memory:prior"]
    expect_refusal(lambda: ar.validate_envelope_bindings(env, bundle=b), "does not match bundle")


def test_context_is_compiled_before_plan_hash_and_acceptance_recomputes_it():
    b = bundle()
    preimage, plan_hash = ar.bind_context_into_plan_preimage({"scope": "synthetic", "steps": ["step:one"]}, b)
    accepted = {"preimage": preimage, "plan_hash": plan_hash}
    assert ar.recompute_accepted_plan_context(accepted, b)["context_binding"]["bundle_digest"] == b["bundle_digest"]
    tampered = copy.deepcopy(accepted); tampered["preimage"]["context_activation"]["item_refs"] = ["memory:prior"]
    expect_refusal(lambda: ar.recompute_accepted_plan_context(tampered, b), "immutable")


def test_existing_attempt_receipt_embeds_both_sections_and_binds_exact_envelope():
    b = bundle()
    env = bound_envelope(b)
    receipt = json.loads((FIXTURES / "codex_desktop.attempt-receipt.v1.json").read_text())
    receipt["envelope_digest"] = contract.execution_envelope_digest(env)
    receipt["knowledge_activation"] = activation()
    receipt["knowledge_activation"]["canonical_binding"]["envelope_digest"] = receipt["envelope_digest"]
    receipt["reliability"] = reliability()
    contract.validate_attempt_receipt(receipt, env, b)
    wrong = copy.deepcopy(b)
    wrong["header"]["accepted_plan_digest"] = "sha256:" + "9" * 64
    wrong["bundle_digest"] = ar.context_bundle_digest(wrong)
    expect_refusal(lambda: contract.validate_attempt_receipt(receipt, env, wrong), "does not bind")


def test_environment_receipt_must_bind_exact_provider_and_cleanup_evidence():
    b = bundle(); env = bound_envelope(b); value = reliability()
    ar.validate_reliability(value, envelope=env)
    forged = copy.deepcopy(value)
    forged["environment_binding_digest"] = "sha256:" + "f" * 64
    expect_refusal(lambda: ar.validate_reliability(forged, envelope=env), "does not bind envelope")
    missing = copy.deepcopy(value)
    del missing["environment_evidence"]
    expect_refusal(lambda: ar.validate_reliability(missing, envelope=env), "requires receipt environment evidence")


def test_legacy_receipt_posture_is_not_activated():
    receipt = json.loads((FIXTURES / "codex_desktop.attempt-receipt.v1.json").read_text())
    assert ar.legacy_activation_state(receipt) == "not_activated"


def test_migration_uses_existing_control_plane_doors_and_append_only_projection():
    migration = (ROOT / "migrations" / "0303_evidence_activation_reliability.sql").read_text()
    assert "security definer" in migration.lower()
    assert "grant select, insert" not in migration.lower()
    assert "before update or delete" in migration.lower()
    assert "activate_context_bundle" in migration
    assert "compile_context_bundle" in migration
    assert "read_context_activation" in migration
    assert "context_activation_bundle_body" in migration
    assert "triaged_at" in migration
    assert "proposed_eval_candidate_event" in migration
    assert "created_at timestamptz not null default clock_timestamp()" in migration
    db_gate = (ROOT / "ops" / "evidence-activation-db-gate.py").read_text()
    assert "# ci: db-gate" in db_gate
    assert "p6.propose" in db_gate
    assert "accept_sourced_work_request_plan" in db_gate
    assert "compile_context_bundle" in db_gate
    assert "duplicate disposition coverage forgery" in db_gate
    assert "judge cannot override critical deterministic failure" in db_gate
    assert "authority evaluation attestations did not derive eligible human-review posture" in db_gate
    assert "context_activation_receipt_binding" in migration
    extension = json.loads((ROOT / "control-room" / "contracts" / "activation-reliability-extension.v1.schema.json").read_text())
    reliability_schema = extension["$defs"]["Reliability"]["properties"]
    assert reliability_schema["eval_candidates"] == {"$ref": "#/$defs/EvalCandidate"}
    assert extension["$defs"]["EvalCandidate"]["maxItems"] == 0
    assert reliability_schema["shadow_comparisons"]["items"] == {"$ref": "#/$defs/ShadowComparison"}
    assert extension["$defs"]["ShadowComparison"]["x-carr-external-validator"].endswith("#validate_shadow_route_binding")
    assert "activate_context_bundle" in db_gate


def test_reliability_withholds_unknown_and_critical_failure_from_judge():
    value = reliability()
    value["grounding_sufficiency"]["state"] = "unknown"
    value["closure"] = {"state": "insufficient_evidence", "reasons": ["reason:authority_evaluation_evidence_missing", "reason:grounding_insufficient"], "derived_by": "server"}
    ar.validate_reliability(value)
    assert ar.derived_reliability_state(value) == "insufficient_evidence"
    value = reliability()
    value["deterministic_checks"][0]["state"] = "failed"
    value["closure"] = {"state": "blocked", "reasons": ["reason:authority_evaluation_evidence_missing", "reason:critical_deterministic_or_evaluator_failure"], "derived_by": "server"}
    ar.validate_reliability(value)
    assert ar.derived_reliability_state(value) == "blocked"


def test_risk_policy_keeps_immature_evidence_insufficient_and_does_not_overrequire_disabled_rungs():
    value = reliability()
    value["evaluator_results"][0]["status"] = "unknown"
    value["closure"] = {"state": "insufficient_evidence", "reasons": ["reason:authority_evaluation_evidence_missing", "reason:critical_evidence_incomplete"], "derived_by": "server"}
    ar.validate_reliability(value)
    assert ar.derived_reliability_state(value) == "insufficient_evidence"
    b = bundle(); env = bound_envelope(b)
    requirements = env["evaluation_plan"]["requirements"]
    requirements["human_acceptance_required"] = False
    requirements["independent_review_required"] = False
    requirements["outcome_horizon_required"] = False
    value = reliability()
    value["human_acceptance"] = {"state": "absent", "actor_ref": "actor:none", "evidence_refs": [], "outcome_feedback_ref": None, "outcome_feedback_hash": None}
    value["outcome_horizon"] = {"state": "unavailable", "ends_at": "2026-08-24T12:00:00Z", "as_of": "2026-08-24T12:00:00Z", "evidence_refs": []}
    for evaluator in value["evaluator_results"]: evaluator["independence_state"] = "not_independent"
    value["closure"] = {"state": "insufficient_evidence", "reasons": ["reason:authority_evaluation_evidence_missing"], "derived_by": "server"}
    ar.validate_reliability(value, envelope=env)


def test_reliability_requires_independent_human_and_evidence_free_pass_refuses():
    value = reliability()
    value["human_acceptance"]["actor_ref"] = value["model_judgement"]["judge_ref"]
    expect_refusal(lambda: ar.validate_reliability(value), "independent")
    value = reliability()
    value["model_judgement"]["evidence_refs"] = []
    expect_refusal(lambda: ar.validate_reliability(value), "evidence")


def test_eval_candidates_and_telemetry_are_action_bound_and_never_promoted():
    value = reliability()
    ar.validate_reliability(value)
    value["eval_candidates"] = [{"candidate_id": "eval-candidate:caller"}]
    expect_refusal(lambda: ar.validate_reliability(value), "cannot self-propose")
    value = reliability()
    value["telemetry"] = [{"signal_id": "telemetry:forged", "state": "cleared", "trigger": "trigger:forged", "consumer": "consumer:forged", "enforcement": "enforcement:forged", "owner": "owner:forged", "remedy": "remedy:forged", "verification": "verification:forged", "auto_clear": True}]
    expect_refusal(lambda: ar.validate_reliability(value), "telemetry must be empty")
    value = reliability()
    value["shadow_comparisons"] = [{"promotion_state": "active", "side_effect_ref": "effect:forged"}]
    expect_refusal(lambda: ar.validate_reliability(value), "cannot carry governed Policy Learning shadow")


def test_python_receipt_admission_refuses_raw_sentences_in_all_reference_bearing_slots():
    receipt = json.loads((FIXTURES / "codex_desktop.attempt-receipt.v1.json").read_text())
    contract.validate_attempt_receipt(receipt)
    attacks = []
    for path in (("interventions",), ("handoff_proposal", "reason"), ("result", "evidence_refs")):
        value = copy.deepcopy(receipt)
        if path == ("interventions",):
            value["interventions"] = [{"kind": "human", "occurred_at": "2026-08-24T12:00:00Z", "summary": "raw operator sentence is forbidden"}]
        elif path[-1] == "reason":
            value["handoff_proposal"] = {"proposed": True, "reason": "raw operator sentence is forbidden", "replacement_session_ref": "session:replacement", "checkpoint_ref": "checkpoint:one", "requires_independent_verification": True}
        else:
            value["result"]["evidence_refs"] = ["raw operator sentence is forbidden"]
        attacks.append(value)
    for value in attacks:
        expect_refusal(lambda value=value: contract.validate_attempt_receipt(value), "raw")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
