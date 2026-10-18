#!/usr/bin/env python3
"""Offline acceptance tests for the provider-neutral execution fabric v1."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import execution_contract as contract  # noqa: E402
import dispatch  # noqa: E402
import bridge  # noqa: E402
import evaluation_kernel  # noqa: E402
import spatial_surface  # noqa: E402


FIXTURES = ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric"


def check(name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - standalone test runner reports every assertion
        raise AssertionError(f"FAIL {name}: {exc}") from exc
    print(f"ok  {name}")


def envelope(surface="codex_desktop"):
    raw = json.loads((FIXTURES / f"{surface}.execution-envelope.v1.json").read_text())
    contract.validate_execution_envelope(raw)
    return raw




def expect_refusal(fn, contains):
    try:
        fn()
    except contract.ContractError as exc:
        assert contains in str(exc), exc
        return
    raise AssertionError("expected ContractError")


def schemas_are_versioned_and_fail_closed():
    for name in ("execution-envelope.v1.schema.json", "attempt-receipt.v1.schema.json", "carr-evaluation-kernel.v1.schema.json", "spatial-surface-projection.v1.schema.json", "telemetry-measurement.v1.schema.json", "visual-extension-manifest.v1.schema.json"):
        schema = json.loads((ROOT / "control-room" / "contracts" / name).read_text())
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["schema_version"]["const"].endswith(".v1")
    eval_schema = json.loads((ROOT / "control-room" / "contracts" / "carr-evaluation-kernel.v1.schema.json").read_text())
    assert "drift" in eval_schema["required"] and eval_schema["properties"]["drift"]["$ref"] == "#/$defs/drift"
    legacy = json.loads((ROOT / "control-room" / "contracts" / "job-passport-eval-portfolio.v1.schema.json").read_text())
    assert legacy["x-carr-migration"]["canonical_schema"] == "carr-evaluation-kernel.v1.schema.json"








def digest_is_canonical_and_deterministic():
    first = envelope()
    second = json.loads(json.dumps(first, sort_keys=True))
    assert contract.execution_envelope_digest(first) == contract.execution_envelope_digest(second)
    assert contract.execution_envelope_digest(first).startswith("sha256:")






def server_issued_repository_write_capability_is_closed_and_bounded():
    value = envelope()
    value["request"]["allowed_actions"] = list(contract.ENGINEERING_REPOSITORY_ACTIONS)
    value["server_binding"]["authority"].update({
        "capability_profile": "capability:engineering-repository-write",
        "capability_grant_ref": "grant:engineering-codex-repository-v1",
        "read_only": False,
    })
    contract.validate_execution_envelope(value)
    widened = copy.deepcopy(value)
    widened["request"]["allowed_actions"].append("repository:merge")
    expect_refusal(lambda: contract.validate_execution_envelope(widened), "allowed action")
    mismatched = copy.deepcopy(value)
    mismatched["server_binding"]["authority"]["read_only"] = True
    expect_refusal(lambda: contract.validate_execution_envelope(mismatched), "read-only authority")
    mismatched = copy.deepcopy(value)
    mismatched["request"]["allowed_actions"] = []
    expect_refusal(lambda: contract.validate_execution_envelope(mismatched), "write authority")






def phase_binding_preserves_native_sessions_and_reserves_evaluation_arms():
    value = envelope()
    assert value["phase_binding"]["native_session_transfer"] == "semantic_state_only"
    assert value["evaluation_context"]["experiment_arm"] == "same_pair_audited_state"
    value["phase_binding"]["switch_conditions"] = ["phase_boundary"]
    expect_refusal(lambda: contract.validate_execution_envelope(value), "verified_checkpoint")
















def spatial_surface_separates_layout_from_canonical_state_and_rejects_stale_views():
    projection = json.loads((FIXTURES / "codex_desktop.observatory-projection.v1.json").read_text())
    surface = json.loads((FIXTURES / "codex_desktop.spatial-surface.v1.json").read_text())
    assert spatial_surface.validate_spatial_surface(surface, projection) == surface
    assert spatial_surface.project_job_passport_surface(projection) == surface
    changed = copy.deepcopy(surface)
    changed["nodes"][0]["geometry"]["user_layout_preference"] = {"x": 33, "y": 44, "width": 150, "height": 72}
    changed["projection_digest"] = contract.canonical_digest({k: v for k, v in changed.items() if k != "projection_digest"})
    spatial_surface.validate_spatial_surface(changed, projection)
    assert changed["canonical_binding"] == surface["canonical_binding"], "layout is not canonical state"
    expect_refusal(lambda: spatial_surface.select_newer_surface(surface, surface), "stale spatial surface")
    conflict = copy.deepcopy(surface); conflict["canonical_binding"]["canonical_record_digest"] = "sha256:" + "f" * 64
    conflict["projection_digest"] = contract.canonical_digest({k: v for k, v in conflict.items() if k != "projection_digest"})
    expect_refusal(lambda: spatial_surface.select_newer_surface(surface, conflict), "same-version spatial conflict")


def telemetry_truth_does_not_cross_convert_or_turn_unavailable_into_zero():
    base = {"schema_version":"telemetry-measurement.v1","measurement_id":"metric:elapsed","metric_kind":"elapsed_time","unit":"ms","scope":"attempt","attribution":{"provider_id":"provider:openai","model_id":"model:codex-synthetic","harness_id":"harness:codex","adapter_id":"adapter:codex-desktop","attempt_id":"attempt-synthetic-codex","native_session_ref":"native:codex-thread-1"},"source":{"type":"deterministic_local_clock","priority":4,"provenance_ref":"evidence:local-clock"},"observed_at":"2026-08-24T12:00:05Z","source_window":{"started_at":"2026-08-24T12:00:00Z","ended_at":"2026-08-24T12:00:05Z"},"freshness":"fresh","value":{"kind":"actual","amount":5000,"estimate_method":None,"uncertainty":None,"unavailable_reason":None}}
    assert spatial_surface.validate_telemetry_measurement(base) == base
    unavailable = copy.deepcopy(base); unavailable["measurement_id"] = "metric:quota"; unavailable["metric_kind"] = "subscription_quota"; unavailable["unit"] = "percent"; unavailable["source"] = {"type":"unavailable","priority":5,"provenance_ref":"evidence:provider-unavailable"}; unavailable["value"] = {"kind":"unavailable","amount":None,"estimate_method":None,"uncertainty":None,"unavailable_reason":"provider did not expose quota"}; unavailable["freshness"] = "unknown"
    spatial_surface.validate_telemetry_measurement(unavailable)
    unavailable["value"]["amount"] = 0
    expect_refusal(lambda: spatial_surface.validate_telemetry_measurement(unavailable), "never zero")
    tokens = copy.deepcopy(base); tokens["measurement_id"] = "metric:tokens"; tokens["metric_kind"] = "session_tokens"; tokens["unit"] = "quota_tokens"
    expect_refusal(lambda: spatial_surface.validate_telemetry_measurement(tokens), "masquerade")




def visual_extensions_are_inspectable_but_untrusted_or_unsafe_packages_are_refused():
    manifest = {"schema_version":"visual-extension-manifest.v1","extension_id":"extension:synthetic-map","version":"v1","api_version":"carr-visual-projection-api.v1","contributions":[{"contribution_id":"contribution:map","kind":"visual_projection","entry_path":"index.html"}],"permissions":["sanitized_projection_data"],"package":{"content_digest":"sha256:" + "a" * 64,"files":[{"path":"index.html","size_bytes":12,"digest":"sha256:" + "b" * 64}]},"provenance":{"publisher_id":"publisher:carr","signature_status":"verified","trust_status":"trusted"},"enablement":{"installed":False,"enabled":False,"human_authorization_ref":None}}
    assert spatial_surface.validate_visual_extension_manifest(manifest) == manifest
    unsafe = copy.deepcopy(manifest); unsafe["package"]["files"][0]["path"] = "../escape.html"
    expect_refusal(lambda: spatial_surface.validate_visual_extension_manifest(unsafe), "path containment")
    untrusted = copy.deepcopy(manifest); untrusted["provenance"]["trust_status"] = "unknown"
    expect_refusal(lambda: spatial_surface.validate_visual_extension_manifest(untrusted), "not trusted")


def eval_portfolio_is_multidimensional_bound_and_rejects_cheap_critical_regression():
    projection = json.loads((FIXTURES / "codex_desktop.observatory-projection.v1.json").read_text())
    portfolio = json.loads((FIXTURES / "carr-evaluation-kernel.synthetic.v1.json").read_text())
    assert evaluation_kernel.validate_evaluation_kernel(portfolio, projection) == portfolio
    gates = evaluation_kernel.cost_curve_gate(portfolio)
    assert gates[0]["promotion_state"] == "not_eligible"
    assert gates[0]["blocked_dimensions"] == ["visual_accessibility"]
    assert gates[1]["promotion_state"] == "blocked"
    assert {case["rung"] for case in portfolio["cases"]} == {"smoke", "regression", "hill_climb", "launch"}
    assert {case["adapter_configuration"]["surface"] for case in portfolio["cases"]} == {
        "claude_desktop", "codex_desktop", "hermes_desktop", "grok_x_native",
    }
    changed = copy.deepcopy(portfolio)
    changed["results"][0]["score"] = 99
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "unknown fields")
    changed = copy.deepcopy(portfolio)
    changed["taxonomy"]["failure_modes"][0]["class_name"] = "bad_answer"
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "never generic")
    changed = copy.deepcopy(portfolio)
    changed["results"][2]["stage_results"] = [changed["results"][2]["stage_results"][0]]
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "not diagnosable")
    changed = copy.deepcopy(portfolio)
    changed["cases"][0]["expected_output"] = "must never reach an executor"
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "unknown fields")
    changed = copy.deepcopy(portfolio)
    changed["cases"][0]["lifecycle_history"][0]["to"] = "accepted"
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "lifecycle transition")
    changed = copy.deepcopy(portfolio)
    changed["cases"][0]["case_provenance"] = "not-a-valid-provenance"
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "provenance")
    changed = copy.deepcopy(portfolio)
    changed["results"][0]["evaluator_results"][0]["calibration"]["status"] = "calibrated"
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "calibration ref")
    changed = copy.deepcopy(portfolio)
    changed["results"][5]["status"] = "passed"
    changed["results"][5]["evaluator_results"].append({"kind":"judge","evaluator_ref":"evaluator:judge-v1","rubric_ref":"rubric:job-passport-visual","provenance":"synthetic_fixture","calibration":{"status":"calibrated","calibration_ref":"calibration:judge-v1","sample_count":10},"lower_bound_evidence_ref":None,"status":"passed","confidence":"high","critical":True,"independence_state":"independent","evidence_refs":["evidence:judge"],"human_accepted":False})
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "critical evaluator or dimension failure")
    proposal = copy.deepcopy(portfolio["cases"][0])
    proposal["case_id"] = "case:proposed-from-defect"
    proposal["case_provenance"] = "production_defect"
    proposal["case_kind"] = "task"
    proposal["human_label_ref"] = None
    proposal["lifecycle"] = "proposed"
    proposal["lifecycle_history"] = []
    proposal["golden_set_ref"] = None
    proposal["target_golden_set_ref"] = "golden:job-passport-r0-heldout"
    proposed_portfolio = copy.deepcopy(portfolio)
    proposed_portfolio["cases"].append(proposal)
    assert evaluation_kernel.validate_evaluation_kernel(proposed_portfolio, projection) == proposed_portfolio
    redacted = copy.deepcopy(portfolio)
    redacted["data_class"] = "redacted_evidence"
    redacted["provenance"]["source_class"] = "production_redacted"
    redacted["provenance"]["production_trace_review"] = "redacted_production_review"
    redacted["case_set"]["refresh_state"] = "current_redacted"
    redacted["case_set"]["production_trace_review"] = "redacted_production_review"
    redacted["taxonomy"]["refresh_state"] = "current_redacted"
    redacted["taxonomy"]["production_trace_review"] = "redacted_production_review"
    assert evaluation_kernel.validate_evaluation_kernel(redacted, projection) == redacted
    leaked = copy.deepcopy(redacted)
    leaked["cases"][1]["adapter_configuration"]["native_session_ref"] = {"raw_transcript":"secret"}
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(leaked), "redacted evidence")
    forged = copy.deepcopy(portfolio)
    forged["outcome_horizon"]["status"] = "mature"
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(forged), "mature outcome horizon")
    forged = copy.deepcopy(portfolio)
    forged["drift"]["baseline_ref"] = None
    forged["drift"]["evidence_refs"] = []
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(forged), "available drift requires")
    forged = copy.deepcopy(portfolio)
    forged["data_class"] = "redacted_evidence"
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(forged), "redacted evidence must declare")


def critical_dimension_gate_blocks_regression_whatever_the_headline():
    def row(dim_id, critical, status, direction):
        return {"dimension_id": dim_id, "critical": critical, "status": status,
                "direction_vs_baseline": direction, "evidence_refs": [f"evidence:{dim_id}"]}
    rows = [row("accuracy", True, "passed", "improved"), row("precision", True, "passed", "regressed"),
            row("brevity", False, "passed", "regressed"), row("safety", True, "failed", "equivalent")]
    assert evaluation_kernel.critical_dimension_blockers(rows) == ["precision", "safety"]
    assert evaluation_kernel.critical_dimension_blockers(rows[:1] + rows[2:3]) == []
    expect_refusal(lambda: evaluation_kernel.critical_dimension_blockers([]), "not an aggregate")
    expect_refusal(lambda: evaluation_kernel.critical_dimension_blockers(rows[:1] * 2), "duplicate")
    expect_refusal(lambda: evaluation_kernel.critical_dimension_blockers([dict(rows[0], score=0.9)]), "unknown fields")
    expect_refusal(lambda: evaluation_kernel.critical_dimension_blockers([dict(rows[0], direction_vs_baseline="up")]), "direction")


def shared_kernel_policy_is_risk_scaled_and_default_deny():
    kernel = json.loads((FIXTURES / "carr-evaluation-kernel.synthetic.v1.json").read_text())
    decision = evaluation_kernel.admission_decision(kernel)
    assert decision["decision"] == "not_admitted"
    assert "synthetic_evidence_not_controller_promotion" in decision["reason_codes"]
    changed = copy.deepcopy(kernel)
    changed["binding"]["risk_class"] = "R3"
    changed["binding"]["lifecycle"] = "launch"
    decision = evaluation_kernel.admission_decision(changed)
    assert "required_case_not_passed:case:launch-representation:blocked" in decision["reason_codes"]
    assert "independent_review_required" not in decision["reason_codes"]
    assert "independent_review_missing" not in decision["reason_codes"]
    assert decision["decision"] == "insufficient_evidence"
    changed = copy.deepcopy(kernel)
    changed["binding"]["risk_class"] = "R6"
    changed["binding"]["lifecycle"] = "launch"
    decision = evaluation_kernel.admission_decision(changed)
    assert "risk_lifecycle_unmapped_default_deny" in decision["reason_codes"]
    decision = evaluation_kernel.admission_decision(kernel, as_of="2026-10-01T00:00:00Z")
    assert "evidence_stale_or_revalidation_required" in decision["reason_codes"]
    changed = copy.deepcopy(kernel)
    changed["drift"]["status"] = "insufficient"
    changed["drift"]["observed_delta"] = 0
    changed["drift"]["baseline_ref"] = None
    changed["drift"]["evidence_refs"] = []
    decision = evaluation_kernel.admission_decision(changed)
    assert decision["decision"] == "insufficient_evidence"
    changed["drift"]["status"] = "exceeds_tolerance"
    changed["drift"]["observed_delta"] = 1
    changed["drift"]["baseline_ref"] = "baseline:synthetic-v1"
    changed["drift"]["evidence_refs"] = ["evidence:synthetic-drift"]
    decision = evaluation_kernel.admission_decision(changed)
    assert decision["decision"] == "not_admitted"
    changed = copy.deepcopy(kernel)
    changed["workflow"]["rubric_id"] = "rubric:retrieval-only"
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "not registered")
    changed = copy.deepcopy(kernel)
    changed["cases"][0]["job_stages"] = ["not-a-shared-stage"]
    expect_refusal(lambda: evaluation_kernel.validate_evaluation_kernel(changed), "unknown workflow rubric stage")
    rubrics = evaluation_kernel.WORKFLOW_RUBRICS
    assert {"workflow:claude-desktop-readonly", "workflow:codex-desktop-readonly", "workflow:hermes-orchestration", "workflow:grok-x-native-retrieval"}.issubset(rubrics)
    assert len({tuple(sorted(rubrics[key]["critical_dimensions"])) for key in rubrics}) == len(rubrics)
    assert {"visual_comprehension", "telemetry_truth", "layout_authority_separation"}.issubset(rubrics["workflow:job-passport"]["critical_dimensions"])


def required_rungs_only_accept_active_golden_membership():
    fixture = json.loads((FIXTURES / "carr-evaluation-kernel.synthetic.v1.json").read_text())
    case_id = "case:identity-doc-grok"
    evidence_ref = "evidence:profile-doc-staffing-grok"

    active = evaluation_kernel.admission_decision(fixture)
    assert not any(code.startswith(f"required_case_not_active:{case_id}") for code in active["reason_codes"])
    assert evidence_ref in active["evidence_refs"]

    lifecycle_variants = {
        "proposed": [],
        "triaged": [{"from": "proposed", "to": "triaged", "evidence_ref": "evidence:case-triage-only"}],
        "retired": [
            {"from": "proposed", "to": "triaged", "evidence_ref": "evidence:case-triage"},
            {"from": "triaged", "to": "accepted", "evidence_ref": "evidence:case-acceptance"},
            {"from": "accepted", "to": "retired", "evidence_ref": "evidence:case-retirement"},
        ],
    }
    for lifecycle, history in lifecycle_variants.items():
        changed = copy.deepcopy(fixture)
        case = next(row for row in changed["cases"] if row["case_id"] == case_id)
        case["lifecycle"] = lifecycle
        case["lifecycle_history"] = history
        if lifecycle in {"proposed", "triaged"}:
            case["golden_set_ref"] = None
        decision = evaluation_kernel.admission_decision(changed)
        assert f"required_case_not_active:{case_id}" in decision["reason_codes"]
        assert evidence_ref not in decision["evidence_refs"]

    inactive_membership = copy.deepcopy(fixture)
    case = next(row for row in inactive_membership["cases"] if row["case_id"] == case_id)
    case["golden_set_ref"] = "golden:job-passport-r1-heldout"
    expect_refusal(lambda: evaluation_kernel.admission_decision(inactive_membership), "accepted/retired case must bind")

    triaged_retirement = copy.deepcopy(fixture)
    case = next(row for row in triaged_retirement["cases"] if row["case_id"] == case_id)
    case["lifecycle"] = "retired"
    case["lifecycle_history"] = [
        {"from": "proposed", "to": "triaged", "evidence_ref": "evidence:case-triage"},
        {"from": "triaged", "to": "retired", "evidence_ref": "evidence:invalid-shortcut"},
    ]
    expect_refusal(lambda: evaluation_kernel.admission_decision(triaged_retirement), "lifecycle transition")










if __name__ == "__main__":
    for name, fn in [
        ("schemas are versioned and fail closed", schemas_are_versioned_and_fail_closed),
        ("canonical digest is deterministic", digest_is_canonical_and_deterministic),
        ("server-issued repository write capability is closed and bounded", server_issued_repository_write_capability_is_closed_and_bounded),
        ("phase binding preserves native sessions", phase_binding_preserves_native_sessions_and_reserves_evaluation_arms),
        ("spatial surface preserves canonical/layout separation and CAS", spatial_surface_separates_layout_from_canonical_state_and_rejects_stale_views),
        ("telemetry truth preserves unavailable and metric distinctions", telemetry_truth_does_not_cross_convert_or_turn_unavailable_into_zero),
        ("visual extension manifest denies unsafe or untrusted packages", visual_extensions_are_inspectable_but_untrusted_or_unsafe_packages_are_refused),
        ("evaluation ladder is multidimensional and rejects masked critical regression", eval_portfolio_is_multidimensional_bound_and_rejects_cheap_critical_regression),
        ("critical dimension gate blocks a regression whatever the headline", critical_dimension_gate_blocks_regression_whatever_the_headline),
        ("shared kernel policy is risk-scaled and defaults deny", shared_kernel_policy_is_risk_scaled_and_default_deny),
        ("required rungs accept only active golden membership", required_rungs_only_accept_active_golden_membership),
    ]:
        check(name, fn)
