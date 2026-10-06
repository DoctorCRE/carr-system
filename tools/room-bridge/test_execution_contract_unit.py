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
import job_passport_artifact as artifact_renderer  # noqa: E402
import bridge  # noqa: E402
import evaluation_kernel  # noqa: E402
import spatial_surface  # noqa: E402
import admission  # noqa: E402


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


def receipt(surface="codex_desktop"):
    raw = json.loads((FIXTURES / f"{surface}.attempt-receipt.v1.json").read_text())
    contract.validate_attempt_receipt(raw, envelope(surface))
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


def unknown_fields_fail_closed():
    value = envelope()
    value["model_selected_by_client"] = "nope"
    expect_refusal(lambda: contract.validate_execution_envelope(value), "unknown fields")
    value = receipt()
    value["raw_transcript"] = "never persisted"
    expect_refusal(lambda: contract.validate_attempt_receipt(value, envelope()), "unknown fields")


def client_identity_and_capability_selection_is_refused():
    safe = {"job_ref": "job:synthetic-read-only", "input_digest": "sha256:" + "1" * 64}
    assert contract.validate_client_job_request(safe) == {**safe, "data_class": "synthetic_only"}
    for field in (
        "organization_tenant_id", "sponsoring_human_id", "runtime_principal", "environment",
        "risk_class", "capability_profile", "personal_brain_scope", "provider_id", "model_id",
    ):
        request = {**safe, field: "client-chosen"}
        expect_refusal(lambda request=request: contract.validate_client_job_request(request), field)


def server_admission_derives_envelope_from_closed_canonical_records():
    source = envelope()
    record = {"envelope_id": source["envelope_id"], "issued_at": source["issued_at"], "expires_at": source["expires_at"],
              "work_request": {"work_request_id": source["work_request_id"], **source["state_binding"] | {"accepted_resource_revisions": source["state_binding"]["accepted_resource_revisions"]}},
              "plan_revision": source["plan_revision"], "agent_session": source["agent_session"], "request": source["request"], "server_binding": source["server_binding"], "handoff": source["handoff"], "phase_binding": source["phase_binding"], "evaluation_context": source["evaluation_context"]}
    record["work_request"].pop("compare_and_swap_required")
    assert admission.admit_execution_envelope(record) == source
    record["server_binding"]["authority"]["client_mutable"] = True
    expect_refusal(lambda: admission.admit_execution_envelope(record), "server-derived")


def digest_is_canonical_and_deterministic():
    first = envelope()
    second = json.loads(json.dumps(first, sort_keys=True))
    assert contract.execution_envelope_digest(first) == contract.execution_envelope_digest(second)
    assert contract.execution_envelope_digest(first).startswith("sha256:")


def different_envelope_receipt_is_refused():
    first = envelope("codex_desktop")
    second = envelope("claude_desktop")
    value = receipt("codex_desktop")
    assert value["envelope_digest"] == contract.execution_envelope_digest(first)
    expect_refusal(lambda: contract.validate_attempt_receipt(value, second), "does not bind")


def replacement_cannot_inherit_capability():
    previous = envelope()
    replacement = copy.deepcopy(previous)
    replacement["envelope_id"] = "env-synthetic-replacement"
    replacement["agent_session"]["id"] = "session-synthetic-replacement"
    replacement["handoff"] = {
        "mode": "replacement", "replaces_agent_session_id": previous["agent_session"]["id"],
        "capability_inherited": False, "checkpoint_ref": "checkpoint:synthetic-verified",
        "native_session_transfer": "semantic_state_only",
    }
    replacement["server_binding"]["authority"]["capability_grant_ref"] = "grant-synthetic-replacement"
    contract.validate_replacement_envelope(previous, replacement)
    replacement["server_binding"]["authority"]["capability_grant_ref"] = \
        previous["server_binding"]["authority"]["capability_grant_ref"]
    expect_refusal(lambda: contract.validate_replacement_envelope(previous, replacement), "cannot inherit")


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


def state_binding_requires_compare_and_swap_and_handoff_checkpoint():
    value = envelope()
    assert value["state_binding"]["state_version"] == 1
    assert value["state_binding"]["compare_and_swap_required"] is True
    value["state_binding"]["compare_and_swap_required"] = False
    expect_refusal(lambda: contract.validate_execution_envelope(value), "compare_and_swap_required")
    previous = envelope()
    replacement = copy.deepcopy(previous)
    replacement["envelope_id"] = "env-synthetic-checkpoint-replacement"
    replacement["agent_session"]["id"] = "session-synthetic-checkpoint-replacement"
    replacement["server_binding"]["authority"]["capability_grant_ref"] = "grant-synthetic-checkpoint-replacement"
    replacement["handoff"] = {
        "mode": "replacement", "replaces_agent_session_id": previous["agent_session"]["id"],
        "capability_inherited": False, "checkpoint_ref": None, "native_session_transfer": "semantic_state_only",
    }
    expect_refusal(lambda: contract.validate_replacement_envelope(previous, replacement), "checkpoint_ref")


def receipts_are_claims_not_canonical_promotion_and_measure_reset_tax():
    value = receipt()
    assert value["attestation"]["canonical_promotion_state"] == "not_promoted"
    assert set(value["telemetry"]["reset_tax"]) == {
        "context_reconstruction_ms", "duplicated_tool_calls", "repeated_failed_approach_count",
        "human_correction_count", "switch_overhead_ms",
    }
    value["negative_knowledge"] = [{
        "approach_ref": "approach:synthetic-failed", "evidence_refs": ["evidence:synthetic-check"],
        "applicability": "scope:synthetic-read", "revalidate_after": "2026-08-25T12:00:00Z",
        "expires_at": "2026-09-01T12:00:00Z",
    }]
    contract.validate_attempt_receipt(value, envelope())
    value["attestation"]["canonical_promotion_state"] = "promoted"
    expect_refusal(lambda: contract.validate_attempt_receipt(value, envelope()), "not canonical verified state")


def phase_binding_preserves_native_sessions_and_reserves_evaluation_arms():
    value = envelope()
    assert value["phase_binding"]["native_session_transfer"] == "semantic_state_only"
    assert value["evaluation_context"]["experiment_arm"] == "same_pair_audited_state"
    value["phase_binding"]["switch_conditions"] = ["phase_boundary"]
    expect_refusal(lambda: contract.validate_execution_envelope(value), "verified_checkpoint")


def self_contained_visual_artifact_is_bound_to_projection_and_staleness():
    bound = envelope()
    value = receipt()
    value["visual_artifacts"] = [{
        "artifact_ref": "artifact:observatory-map", "media_type": "text/html", "self_contained": True,
        "external_service_dependency": False, "visual_form": "topology",
        "source_binding": {
            "work_request_id": bound["work_request_id"], "plan_revision_digest": bound["plan_revision"]["digest"],
            "state_version": bound["state_binding"]["state_version"],
            "canonical_record_digest": bound["state_binding"]["canonical_record_digest"],
            "projection_schema_version": "observatory-attempt-projection.v1",
            "projection_digest": "sha256:" + "e" * 64,
        },
        "generation": {
            "generating_attempt_id": value["attempt_id"],
            "adapter_configuration_fingerprint": bound["server_binding"]["adapter"]["configuration_fingerprint"],
            "skill_id": "skill:html-diagram", "skill_version": "candidate-v1",
        },
        "generated_at": "2026-08-24T12:00:05Z", "freshness": {"state": "stale", "valid_through": "2026-08-24T12:00:05Z"},
        "redaction_class": "metadata_only", "content_digest": "sha256:" + "f" * 64,
        "evidence_refs": ["evidence:synthetic-check"],
        "accessibility": {"color_independent_meaning": True, "reduced_motion_supported": True, "responsive_verified": True, "keyboard_accessible": True},
    }]
    contract.validate_attempt_receipt(value, bound)
    assert value["visual_artifacts"][0]["freshness"]["state"] == "stale"
    value["visual_artifacts"][0]["external_service_dependency"] = True
    expect_refusal(lambda: contract.validate_attempt_receipt(value, bound), "no external service dependency")


def native_transports_have_comparable_receipt_shapes():
    rows = [receipt(surface) for surface in (
        "claude_desktop", "codex_desktop", "hermes_desktop", "grok_x_native",
    )]
    assert len({tuple(sorted(row)) for row in rows}) == 1
    assert len({tuple(sorted(row["adapter"])) for row in rows}) == 1
    assert all(row["result"]["job_ref"] == "job:synthetic-read-only" for row in rows)


def terminal_and_verification_states_remain_distinct():
    states = {
        "success": ("succeeded", "verified_success"),
        "failure": ("failed", "verified_failure"),
        "timeout": ("timed_out", "unknown"),
        "cancellation": ("cancelled", "not_attempted"),
        "partial": ("partial", "partial"),
        "unknown": ("unknown", "unknown"),
    }
    base = receipt()
    seen = set()
    for label, expected in states.items():
        value = copy.deepcopy(base)
        value["attempt_id"] = f"attempt-{label}"
        value["lifecycle"]["state"] = expected[0]
        value["result"]["outcome"] = label
        value["result"]["verification_state"] = expected[1]
        contract.validate_attempt_receipt(value, envelope())
        seen.add((value["lifecycle"]["state"], value["result"]["verification_state"]))
    assert len(seen) == len(states)


def declared_vs_observed_is_uncertain_and_filesystem_alone_is_not_a_deviation():
    value = receipt()
    value["observation"] = {
        "progress_state": "quiet", "coverage_state": "partial", "activity_fidelity": "filesystem",
        "declared_refs_observed": ["step:synthetic-read"], "unmapped_activity_refs": ["resource:worktree-b"],
        "deviation_candidates": [{
            "candidate_id": "candidate-synthetic-1", "basis": ["filesystem_change"],
            "confidence": "low", "requires_review": True,
        }], "uncertainty": "filesystem_only",
    }
    expect_refusal(lambda: contract.validate_attempt_receipt(value, envelope()), "filesystem movement alone")
    value["observation"]["deviation_candidates"][0]["basis"] = ["filesystem_change", "declared_binding_mismatch"]
    contract.validate_attempt_receipt(value, envelope())


def progress_event_is_redacted_observational_and_can_stay_ephemeral():
    event = {
        "schema_version": "progress-event.v1", "attempt_id": "attempt-progress-1", "sequence": 1,
        "occurred_at": "2026-08-24T12:00:01Z", "event_type": "observed_filesystem",
        "declared_step_ref": "step-synthetic-read", "observed_resource_ref": "resource-worktree-b",
        "observed_component_ref": None, "tool_class": "filesystem_watcher", "state": "quiet",
        "correlation_id": "corr-synthetic-1", "causation_id": "cause-dispatch-1",
        "redaction_class": "metadata_only", "evidence_refs": ["evidence:fs-summary-1"], "retention": "ephemeral",
    }
    contract.validate_progress_event(event)


def observatory_projection_groups_by_work_request_and_separates_profile_from_staffing():
    event = {
        "schema_version": "progress-event.v1", "attempt_id": "attempt-synthetic-codex", "sequence": 1,
        "occurred_at": "2026-08-24T12:00:01Z", "event_type": "observed_tool",
        "declared_step_ref": "step:synthetic-read", "observed_resource_ref": "resource:worktree-a",
        "observed_component_ref": "component:execution-fabric", "tool_class": "tool:codex-event",
        "state": "active", "correlation_id": "corr:synthetic-1", "causation_id": "cause:dispatch-1",
        "redaction_class": "metadata_only", "evidence_refs": ["evidence:synthetic-check"], "retention": "ephemeral",
    }
    actual = contract.project_observatory_attempt(
        envelope(), receipt(), [event], {"profile_id": "profile:doc", "display_label": "Doc"},
    )
    expected = json.loads((FIXTURES / "codex_desktop.observatory-projection.v1.json").read_text())
    assert actual == expected
    assert actual["work_request_id"] == "wr-synthetic-read-only"
    assert actual["attempt_lane"]["persistent_profile"]["display_label"] == "Doc"
    assert actual["attempt_lane"]["actual_staffing"]["model_id"] == "model:codex-synthetic"
    assert actual["projection_digest"] == contract.canonical_digest({key: value for key, value in actual.items() if key != "projection_digest"})
    assert actual["source_state"]["state_version"] == 1
    assert "raw provider answer" not in json.dumps(actual)


def wire_receipts_validate_projection_and_keep_typed_facts_distinct():
    projection = json.loads((FIXTURES / "codex_desktop.observatory-projection.v1.json").read_text())
    wire = contract.job_passport_wire_receipt("observatory_projection", projection)
    assert wire["job_passport"]["schema_version"] == "job-passport-wire.v1"
    assert wire["job_passport"]["payload"]["projection_digest"] == projection["projection_digest"]
    projection["source_state"]["state_version"] = 2
    expect_refusal(lambda: contract.validate_observatory_projection(projection), "does not bind")


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


def typed_telemetry_wire_binds_attempt_and_preserves_unavailable_cost():
    elapsed = json.loads((FIXTURES / "codex_desktop.elapsed-time.telemetry-measurement.v1.json").read_text())
    cost = json.loads((FIXTURES / "codex_desktop.billed-cost.telemetry-measurement.v1.json").read_text())
    assert contract.job_passport_wire_receipt("telemetry_measurement", elapsed)["job_passport"]["payload"] == elapsed
    posted = []
    rehearsal = bridge.rehearse_job_passport(envelope(), receipt(), [], {"profile_id": "profile:doc", "display_label": "Doc"}, telemetry_measurements=[elapsed, cost], add_room_turn=lambda **row: posted.append(row) or {"recorded": True})
    assert rehearsal["published"][-2]["kind"] == "telemetry_measurement"
    assert json.loads(posted[-1]["body"])["job_passport"]["payload"]["value"]["kind"] == "unavailable"
    wrong = copy.deepcopy(elapsed); wrong["attribution"]["attempt_id"] = "attempt:other"
    expect_refusal(lambda: bridge.rehearse_job_passport(envelope(), receipt(), [], {"profile_id": "profile:doc", "display_label": "Doc"}, telemetry_measurements=[wrong], add_room_turn=lambda **row: {}), "does not bind rehearsal attempt")
    derived = spatial_surface.measurements_from_attempt_receipt(receipt())
    assert [(row["metric_kind"], row["value"]["kind"]) for row in derived] == [("elapsed_time", "actual"), ("billed_cost", "unavailable")]
    assert derived[0]["value"]["amount"] == 5000


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


def synthetic_read_only_rehearsal_publishes_every_typed_fact_to_the_existing_wire():
    event = {
        "schema_version": "progress-event.v1", "attempt_id": "attempt-synthetic-codex", "sequence": 1,
        "occurred_at": "2026-08-24T12:00:01Z", "event_type": "observed_tool", "declared_step_ref": "step:synthetic-read",
        "observed_resource_ref": "resource:worktree-a", "observed_component_ref": "component:execution-fabric",
        "tool_class": "tool:codex-event", "state": "active", "correlation_id": "corr:synthetic-1",
        "causation_id": "cause:dispatch-1", "redaction_class": "metadata_only", "evidence_refs": ["evidence:synthetic-check"], "retention": "ephemeral",
    }
    posted = []
    kernel = json.loads((FIXTURES / "carr-evaluation-kernel.synthetic.v1.json").read_text())
    rehearsal = bridge.rehearse_job_passport(
        envelope(), receipt(), [event], {"profile_id": "profile:doc", "display_label": "Doc"},
        evaluation_kernel=kernel,
        add_room_turn=lambda **row: posted.append(row) or {"recorded": True},
    )
    assert rehearsal["mode"] == "synthetic_read_only_rehearsal"
    assert [json.loads(row["body"])["job_passport"]["kind"] for row in posted] == [
        "execution_envelope", "progress_event", "attempt_receipt", "observatory_projection", "evaluation_kernel", "telemetry_measurement", "telemetry_measurement",
    ]
    assert all(row["seat"] == "hermes" and row["kind"] == "receipt" for row in posted)
    projection_wire = next(row for row in posted if json.loads(row["body"])["job_passport"]["kind"] == "observatory_projection")
    assert rehearsal["projection"]["projection_digest"] == json.loads(projection_wire["body"])["job_passport"]["payload"]["projection_digest"]


def self_contained_job_passport_artifact_binds_content_and_is_stale_visible():
    projection = json.loads((FIXTURES / "codex_desktop.observatory-projection.v1.json").read_text())
    behavior = json.loads((FIXTURES / "codex_desktop.job-passport.behavior-verification.v1.json").read_text())
    contract.validate_product_behavior_verification(behavior)
    portfolio = json.loads((FIXTURES / "carr-evaluation-kernel.synthetic.v1.json").read_text())
    surface = json.loads((FIXTURES / "codex_desktop.spatial-surface.v1.json").read_text())
    telemetry = [json.loads((FIXTURES / name).read_text()) for name in ("codex_desktop.elapsed-time.telemetry-measurement.v1.json", "codex_desktop.billed-cost.telemetry-measurement.v1.json")]
    document, artifact = artifact_renderer.build_visual_artifact(envelope(), receipt(), projection, behavior, portfolio, surface, telemetry)
    assert artifact_renderer.verify_visual_artifact(document, artifact)
    assert artifact["content_digest"] == contract.canonical_digest(document)
    assert "https://" not in document and "fetch(" not in document
    assert "prefers-reduced-motion" in document and "<details>" in document and "Behavior audit" in document and "Evaluation ladder" in document and "No aggregate score" in document and "Spatial Home Zone" in document and "elapsed_time (ms): 5000" in document and "no approved provider billing source" in document
    stale = copy.deepcopy(projection)
    stale["state"]["progress"] = "stale"
    stale["observed_movement"]["progress_state"] = "stale"
    stale["projection_digest"] = contract.canonical_digest({key: value for key, value in stale.items() if key != "projection_digest"})
    _, stale_artifact = artifact_renderer.build_visual_artifact(envelope(), receipt(), stale)
    assert stale_artifact["freshness"]["state"] == "stale"
    assert artifact_renderer.verify_visual_artifact(document + "tampered", artifact) is False


def behavior_audit_fails_closed_on_dangling_claim_or_fake_live_verification():
    behavior = json.loads((FIXTURES / "codex_desktop.job-passport.behavior-verification.v1.json").read_text())
    behavior["items"][0]["claim_id"] = "claim:missing"
    expect_refusal(lambda: contract.validate_product_behavior_verification(behavior), "dangling")
    behavior = json.loads((FIXTURES / "codex_desktop.job-passport.behavior-verification.v1.json").read_text())
    behavior["items"][0]["status"] = "passed"
    expect_refusal(lambda: contract.validate_product_behavior_verification(behavior), "live browser evidence")
    eval_behavior = json.loads((FIXTURES / "codex_desktop.job-passport-eval.behavior-verification.v1.json").read_text())
    assert contract.validate_product_behavior_verification(eval_behavior) == eval_behavior


def compatibility_wrapper_uses_existing_dispatch_with_a_fake_and_redacts_result():
    calls = []

    def fake_dispatch(name, task, **kwargs):
        calls.append((name, task, kwargs))
        return {
            "msg_id": "dispatch-message-1", "desk": name, "kind": "codex-session",
            "dispatched_at": "2026-08-24T12:00:00Z", "status": "completed",
            "result": "a raw provider answer must not land in the receipt",
            "thread_id": "native-thread-1",
        }

    out = dispatch.dispatch_envelope(
        "codex-desk", envelope(), "perform synthetic read only work", dispatch_fn=fake_dispatch,
    )
    assert calls and calls[0][0] == "codex-desk"
    assert out["dispatch"]["result"].startswith("a raw provider")
    serialized = json.dumps(out["attempt_receipt"], sort_keys=True)
    assert "raw provider answer" not in serialized
    contract.validate_attempt_receipt(out["attempt_receipt"], envelope())


if __name__ == "__main__":
    for name, fn in [
        ("schemas are versioned and fail closed", schemas_are_versioned_and_fail_closed),
        ("unknown fields fail closed", unknown_fields_fail_closed),
        ("client identity/capability selection is refused", client_identity_and_capability_selection_is_refused),
        ("server admission derives only from canonical records", server_admission_derives_envelope_from_closed_canonical_records),
        ("canonical digest is deterministic", digest_is_canonical_and_deterministic),
        ("receipt for another envelope is refused", different_envelope_receipt_is_refused),
        ("handoff replacement cannot inherit capability", replacement_cannot_inherit_capability),
        ("server-issued repository write capability is closed and bounded", server_issued_repository_write_capability_is_closed_and_bounded),
        ("state binding requires CAS and verified handoff checkpoint", state_binding_requires_compare_and_swap_and_handoff_checkpoint),
        ("receipts are claims and carry reset-tax metrics", receipts_are_claims_not_canonical_promotion_and_measure_reset_tax),
        ("phase binding preserves native sessions", phase_binding_preserves_native_sessions_and_reserves_evaluation_arms),
        ("visual artifacts bind exact source state and remain stale-visible", self_contained_visual_artifact_is_bound_to_projection_and_staleness),
        ("native transports produce comparable receipt shapes", native_transports_have_comparable_receipt_shapes),
        ("lifecycle and verification states stay distinct", terminal_and_verification_states_remain_distinct),
        ("declared versus observed remains uncertain", declared_vs_observed_is_uncertain_and_filesystem_alone_is_not_a_deviation),
        ("progress event is ephemeral and observational", progress_event_is_redacted_observational_and_can_stay_ephemeral),
        ("observatory projection preserves profile/staffing distinction", observatory_projection_groups_by_work_request_and_separates_profile_from_staffing),
        ("room wire accepts only validated typed Job Passport facts", wire_receipts_validate_projection_and_keep_typed_facts_distinct),
        ("spatial surface preserves canonical/layout separation and CAS", spatial_surface_separates_layout_from_canonical_state_and_rejects_stale_views),
        ("telemetry truth preserves unavailable and metric distinctions", telemetry_truth_does_not_cross_convert_or_turn_unavailable_into_zero),
        ("typed telemetry wire binds current attempt and preserves unavailable", typed_telemetry_wire_binds_attempt_and_preserves_unavailable_cost),
        ("visual extension manifest denies unsafe or untrusted packages", visual_extensions_are_inspectable_but_untrusted_or_unsafe_packages_are_refused),
        ("evaluation ladder is multidimensional and rejects masked critical regression", eval_portfolio_is_multidimensional_bound_and_rejects_cheap_critical_regression),
        ("critical dimension gate blocks a regression whatever the headline", critical_dimension_gate_blocks_regression_whatever_the_headline),
        ("shared kernel policy is risk-scaled and defaults deny", shared_kernel_policy_is_risk_scaled_and_default_deny),
        ("required rungs accept only active golden membership", required_rungs_only_accept_active_golden_membership),
        ("synthetic read-only rehearsal publishes typed facts on the existing wire", synthetic_read_only_rehearsal_publishes_every_typed_fact_to_the_existing_wire),
        ("self-contained Job Passport artifact has verified content and stale posture", self_contained_job_passport_artifact_binds_content_and_is_stale_visible),
        ("behavior audit fails closed on dangling or non-live verification", behavior_audit_fails_closed_on_dangling_claim_or_fake_live_verification),
        ("compatibility wrapper uses a fake without persisting raw result", compatibility_wrapper_uses_existing_dispatch_with_a_fake_and_redacts_result),
    ]:
        check(name, fn)
