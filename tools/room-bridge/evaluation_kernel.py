"""Offline, deterministic shared CARR evaluation kernel v1.

This is an evidence read model and deterministic admission recommendation, not
an experiment tracker, router, store, or promotion authority. A controller may
use its result as an input to existing policy; executor and judge claims never
self-commit canonical CARR state.
"""
from __future__ import annotations

from typing import Any

import execution_contract as contract
import design_kernel
import policy_learning


WORKFLOW_RUBRICS = {
    "workflow:job-passport": {
        "rubric_id": "rubric:job-passport-visual",
        "stages": {"typed_receipt_ingestion", "freshness_cas_selection", "projection", "visual_interaction_accessibility", "evidence_promotion_display"},
        "critical_dimensions": {"receipt_integrity", "freshness_integrity", "visual_accessibility", "visual_comprehension", "telemetry_truth", "layout_authority_separation"},
    },
    "workflow:claude-desktop-readonly": {
        "rubric_id": "rubric:claude-desktop-readonly",
        "stages": {"typed_receipt_ingestion", "projection"},
        "critical_dimensions": {"native_hook_attribution", "receipt_integrity"},
    },
    "workflow:codex-desktop-readonly": {
        "rubric_id": "rubric:codex-desktop-readonly",
        "stages": {"typed_receipt_ingestion", "freshness_cas_selection", "projection"},
        "critical_dimensions": {"adapter_configuration_binding", "freshness_integrity"},
    },
    "workflow:hermes-orchestration": {
        "rubric_id": "rubric:hermes-orchestration",
        "stages": {"typed_receipt_ingestion", "projection"},
        "critical_dimensions": {"profile_staffing_separation", "handoff_checkpoint"},
    },
    "workflow:grok-x-native-retrieval": {
        "rubric_id": "rubric:grok-x-native-retrieval",
        "stages": {"typed_receipt_ingestion", "projection"},
        "critical_dimensions": {"x_native_provenance", "retrieval_evidence_binding"},
    },
}


class EvalPortfolioError(contract.ContractError):
    """An evaluation portfolio is malformed, unbound, or too vague to act on."""


RUNGS = {"smoke", "regression", "hill_climb", "launch"}
OUTCOMES = {"passed", "failed", "blocked", "unknown"}
DIRECTIONS = {"improved", "equivalent", "regressed", "not_compared"}
PORTFOLIO_FIELDS = {"schema_version", "portfolio_id", "data_class", "generated_at", "workflow", "policy", "provenance", "outcome_horizon", "drift", "migration", "binding", "case_set", "taxonomy", "cases", "results", "frontier_comparisons", "experiments", "behavior_findings", "notes"}
WORKFLOW_FIELDS = {"workflow_id", "rubric_id", "rubric_version", "rubric_digest", "case_set_digest"}
POLICY_FIELDS = {"policy_id", "policy_version", "policy_digest", "default_effect", "risk_requirements"}
RISK_REQUIREMENT_FIELDS = {"risk_class", "lifecycle", "required_rungs", "required_evaluator_kinds", "min_held_out_cases", "max_critical_failure_count", "max_critical_failure_rate", "confidence_posture", "drift_tolerance", "independent_review_required", "human_acceptance_required", "outcome_horizon_maturity"}
PROVENANCE_FIELDS = {"source_class", "production_trace_review", "expires_at"}
OUTCOME_HORIZON_FIELDS = {"status", "matures_at", "evidence_refs"}
DRIFT_FIELDS = {"status", "observed_delta", "baseline_ref", "evidence_refs"}
MIGRATION_FIELDS = {"canonical_contract", "legacy_aliases"}
BINDING_FIELDS = {"work_request_id", "plan_revision_digest", "state_version", "canonical_record_digest", "accepted_resource_revisions", "projection_digest", "risk_class", "lifecycle"}
CASE_SET_FIELDS = {"case_set_id", "version", "refresh_state", "production_trace_review", "golden_sets"}
GOLDEN_SET_FIELDS = {"golden_set_ref", "workflow_lane", "risk_class", "split", "case_ids"}
TAXONOMY_FIELDS = {"taxonomy_version", "refresh_state", "production_trace_review", "unknown_posture", "failure_modes"}
FAILURE_MODE_FIELDS = {"failure_mode_id", "parent_id", "class_name", "status", "affected_stages", "affected_dimensions", "evidence_refs", "provenance", "refresh_state"}
CASE_FIELDS = {"case_id", "rung", "phase_id", "workflow_lane", "risk_class", "split", "golden_set_ref", "target_golden_set_ref", "case_provenance", "case_kind", "human_label_ref", "lifecycle", "lifecycle_history", "job_stages", "adapter_configuration", "evaluator_version", "evidence_refs", "refresh_state", "diagnosis_required"}
LIFECYCLE_EVENT_FIELDS = {"from", "to", "evidence_ref"}
RESULT_FIELDS = {"result_id", "case_id", "rung", "attempt_id", "status", "confidence", "evidence_refs", "dimension_results", "stage_results", "telemetry", "evaluator_results", "failure_mode_ids"}
DIMENSION_FIELDS = {"dimension_id", "status", "direction_vs_baseline", "critical", "evidence_refs"}
EVALUATOR_RESULT_FIELDS = {"kind", "evaluator_ref", "rubric_ref", "provenance", "calibration", "lower_bound_evidence_ref", "status", "confidence", "critical", "independence_state", "evidence_refs", "human_accepted"}
CALIBRATION_FIELDS = {"status", "calibration_ref", "sample_count"}
STAGE_RESULT_FIELDS = {"stage_id", "status", "dimension_ids", "evidence_refs"}
TELEMETRY_FIELDS = {"latency_ms", "cost_usd", "usage", "recovery_count", "intervention_count", "reset_tax"}
COMPARISON_FIELDS = {"comparison_id", "baseline_result_id", "candidate_result_id", "required_dimensions", "dimension_tolerances", "evidence_required", "promotion_state"}
TOLERANCE_FIELDS = {"dimension_id", "required_status", "accepted_directions"}
EXPERIMENT_FIELDS = {"experiment_id", "primary_dimension", "baseline_result_id", "hypothesis", "non_regression_dimensions", "phase_id", "session_affinity", "switch_condition", "tool_context", "result"}
TOOL_CONTEXT_FIELDS = {"eligible_count", "exposed_count", "selected_count", "used_count", "provenance"}
BEHAVIOR_FINDING_FIELDS = {"finding_id", "root_cause_key", "failure_mode_id", "evidence_refs"}


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    try:
        return contract._expect_exact(value, fields, label)  # shared strict contract primitive
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def _id(value: Any, label: str) -> str:
    try:
        return contract._string(value, label, identifier=True)
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def _refs(value: Any, label: str) -> list[str]:
    try:
        return contract._list_of_strings(value, label)
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def _digest(value: Any, label: str) -> str:
    try:
        return contract._digest(value, label)
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def _outcome(value: Any, label: str) -> str:
    if value not in OUTCOMES:
        raise EvalPortfolioError(f"{label} must be passed, failed, blocked, or unknown")
    return value


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(forbidden & set(value)) or any(_contains_key(item, forbidden) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _validate_binding(value: Any) -> dict[str, Any]:
    row = _exact(value, BINDING_FIELDS, "eval portfolio binding")
    _id(row["work_request_id"], "eval portfolio work_request_id")
    for field in ("plan_revision_digest", "canonical_record_digest", "projection_digest"):
        _digest(row[field], f"eval portfolio {field}")
    if not isinstance(row["state_version"], int) or isinstance(row["state_version"], bool) or row["state_version"] < 1:
        raise EvalPortfolioError("eval portfolio state_version must be a positive integer")
    if row["risk_class"] not in {f"R{i}" for i in range(7)} or row["lifecycle"] not in {"rehearsal", "change", "experiment", "launch"}:
        raise EvalPortfolioError("eval portfolio risk/lifecycle binding is invalid")
    if not isinstance(row["accepted_resource_revisions"], list): raise EvalPortfolioError("eval portfolio accepted resource revisions must be a list")
    for resource in row["accepted_resource_revisions"]:
        checked = _exact(resource, {"resource_ref", "revision_ref", "digest"}, "eval accepted resource revision")
        _id(checked["resource_ref"], "eval accepted resource resource_ref"); _id(checked["revision_ref"], "eval accepted resource revision_ref"); _digest(checked["digest"], "eval accepted resource digest")
    return row


def _validate_adapter(value: Any) -> dict[str, Any]:
    try:
        return contract._validate_adapter(value)
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def validate_eval_portfolio(raw: Any, projection: Any | None = None) -> dict[str, Any]:
    """Fail closed on anonymous scores, vague failures, or dangling evidence.

    The record deliberately has no score/aggregate field. Launch is represented
    for portfolio parity but cannot claim online traffic in this offline v1.
    """
    value = _exact(raw, PORTFOLIO_FIELDS, "eval portfolio")
    if value["schema_version"] != "carr-evaluation-kernel.v1":
        raise EvalPortfolioError("unsupported shared evaluation kernel schema_version")
    _id(value["portfolio_id"], "eval portfolio portfolio_id")
    if value["data_class"] not in {"synthetic_only", "metadata_only", "redacted_evidence"}:
        raise EvalPortfolioError("eval portfolio data_class is invalid")
    if value["data_class"] == "redacted_evidence" and _contains_key(value, {"raw_prompt", "raw_transcript", "tool_payload", "raw_output"}):
        raise EvalPortfolioError("redacted evidence cannot contain raw prompt, transcript, tool payload, or output")
    contract._timestamp(value["generated_at"], "eval portfolio generated_at")
    workflow = _exact(value["workflow"], WORKFLOW_FIELDS, "evaluation workflow rubric")
    for field in ("workflow_id", "rubric_id", "rubric_version"):_id(workflow[field], f"evaluation workflow {field}")
    for field in ("rubric_digest", "case_set_digest"): _digest(workflow[field], f"evaluation workflow {field}")
    rubric = WORKFLOW_RUBRICS.get(workflow["workflow_id"])
    if rubric is None or rubric["rubric_id"] != workflow["rubric_id"]:
        raise EvalPortfolioError("evaluation workflow/rubric is not registered")
    policy = _exact(value["policy"], POLICY_FIELDS, "evaluation policy")
    for field in ("policy_id", "policy_version"): _id(policy[field], f"evaluation policy {field}")
    _digest(policy["policy_digest"], "evaluation policy digest")
    if policy["default_effect"] != "deny" or not isinstance(policy["risk_requirements"], list): raise EvalPortfolioError("evaluation policy must default deny")
    policy_rows = set()
    for raw_requirement in policy["risk_requirements"]:
        requirement = _exact(raw_requirement, RISK_REQUIREMENT_FIELDS, "evaluation risk requirement")
        key = (requirement["risk_class"], requirement["lifecycle"])
        if key in policy_rows or requirement["risk_class"] not in {f"R{i}" for i in range(7)} or requirement["lifecycle"] not in {"rehearsal", "change", "experiment", "launch"} or not set(requirement["required_rungs"]).issubset(RUNGS) or not requirement["required_rungs"] or not isinstance(requirement["independent_review_required"], bool) or not isinstance(requirement["human_acceptance_required"], bool): raise EvalPortfolioError("evaluation risk requirement is invalid")
        if not isinstance(requirement["required_evaluator_kinds"], list) or not requirement["required_evaluator_kinds"] or not set(requirement["required_evaluator_kinds"]).issubset({"deterministic", "judge", "human_acceptance"}): raise EvalPortfolioError("evaluation required evaluator kinds are invalid")
        for field in ("min_held_out_cases", "max_critical_failure_count"):
            if not isinstance(requirement[field], int) or isinstance(requirement[field], bool) or requirement[field] < 0: raise EvalPortfolioError("evaluation risk sample/failure bounds are invalid")
        if not isinstance(requirement["max_critical_failure_rate"], (int, float)) or isinstance(requirement["max_critical_failure_rate"], bool) or not 0 <= requirement["max_critical_failure_rate"] <= 1: raise EvalPortfolioError("evaluation critical failure rate bound is invalid")
        if requirement["confidence_posture"] not in {"descriptive", "lower_bound_required"} or not isinstance(requirement["drift_tolerance"], (int, float)) or isinstance(requirement["drift_tolerance"], bool) or requirement["drift_tolerance"] < 0 or requirement["outcome_horizon_maturity"] not in {"not_required", "required"}: raise EvalPortfolioError("evaluation statistical gate posture is invalid")
        policy_rows.add(key)
    provenance = _exact(value["provenance"], PROVENANCE_FIELDS, "evaluation provenance")
    if provenance["source_class"] not in {"synthetic_only", "production_redacted"} or provenance["production_trace_review"] not in {"unavailable_no_production_traces", "redacted_production_review"}: raise EvalPortfolioError("evaluation provenance is invalid")
    contract._timestamp(provenance["expires_at"], "evaluation provenance expires_at")
    if value["data_class"] in {"synthetic_only", "metadata_only"} and (provenance["source_class"] != "synthetic_only" or provenance["production_trace_review"] != "unavailable_no_production_traces"):
        raise EvalPortfolioError("synthetic or metadata evaluation must declare unavailable production provenance")
    if value["data_class"] == "redacted_evidence" and (provenance["source_class"] != "production_redacted" or provenance["production_trace_review"] != "redacted_production_review"):
        raise EvalPortfolioError("redacted evidence must declare redacted production provenance")
    horizon = _exact(value["outcome_horizon"], OUTCOME_HORIZON_FIELDS, "evaluation outcome horizon")
    if horizon["status"] not in {"mature", "immature", "unavailable", "stale"}: raise EvalPortfolioError("evaluation outcome horizon status is invalid")
    contract._timestamp(horizon["matures_at"], "evaluation outcome horizon matures_at")
    _refs(horizon["evidence_refs"], "evaluation outcome horizon evidence_refs")
    if horizon["status"] == "mature" and (value["generated_at"] < horizon["matures_at"] or not horizon["evidence_refs"]):
        raise EvalPortfolioError("mature outcome horizon cannot precede maturity or omit evidence")
    if horizon["status"] != "mature" and value["generated_at"] >= horizon["matures_at"]:
        raise EvalPortfolioError("outcome horizon cannot remain immature after its maturity timestamp")
    drift = _exact(value["drift"], DRIFT_FIELDS, "evaluation drift evidence")
    if drift["status"] not in {"available", "insufficient", "exceeds_tolerance", "unavailable"} or not isinstance(drift["observed_delta"], (int, float)) or isinstance(drift["observed_delta"], bool) or drift["observed_delta"] < 0: raise EvalPortfolioError("evaluation drift evidence is invalid")
    if drift["status"] in {"available", "exceeds_tolerance"}:
        if drift["baseline_ref"] is None or not drift["evidence_refs"]: raise EvalPortfolioError("available drift requires baseline and evidence")
        _id(drift["baseline_ref"], "evaluation drift baseline_ref")
    elif drift["baseline_ref"] is not None or drift["evidence_refs"] or drift["observed_delta"] != 0:
        raise EvalPortfolioError("insufficient or unavailable drift cannot carry useful measurements")
    _refs(drift["evidence_refs"], "evaluation drift evidence_refs")
    migration = _exact(value["migration"], MIGRATION_FIELDS, "evaluation migration")
    _id(migration["canonical_contract"], "evaluation migration canonical contract"); _refs(migration["legacy_aliases"], "evaluation migration aliases")
    binding = _validate_binding(value["binding"])
    if projection is not None:
        checked = contract.validate_observatory_projection(projection)
        expected = {"work_request_id": checked["work_request_id"], "plan_revision_digest": checked["source_state"]["plan_revision_digest"], "state_version": checked["source_state"]["state_version"], "canonical_record_digest": checked["source_state"]["canonical_record_digest"], "projection_digest": checked["projection_digest"]}
        if any(binding[field] != expected[field] for field in expected):
            raise EvalPortfolioError("eval portfolio must bind the exact current observatory projection")
    case_set = _exact(value["case_set"], CASE_SET_FIELDS, "eval case set")
    _id(case_set["case_set_id"], "eval case set id"); _id(case_set["version"], "eval case set version")
    allowed_refresh = {"current_synthetic", "refresh_due", "unknown"} if value["data_class"] != "redacted_evidence" else {"current_synthetic", "current_redacted", "refresh_due", "unknown"}
    allowed_trace_review = {"unavailable_no_production_traces"} if value["data_class"] != "redacted_evidence" else {"unavailable_no_production_traces", "redacted_production_review"}
    if case_set["refresh_state"] not in allowed_refresh or case_set["production_trace_review"] not in allowed_trace_review:
        raise EvalPortfolioError("eval case set must preserve synthetic drift posture")
    if (value["data_class"] == "redacted_evidence") != (case_set["production_trace_review"] == "redacted_production_review"):
        raise EvalPortfolioError("case set production provenance disagrees with portfolio data class")
    golden_refs: dict[str, dict[str, Any]] = {}
    golden_keys: set[tuple[Any, Any, Any]] = set()
    for raw_golden in case_set["golden_sets"]:
        golden_set = _exact(raw_golden, GOLDEN_SET_FIELDS, "eval golden set")
        _id(golden_set["golden_set_ref"], "eval golden set ref"); _id(golden_set["workflow_lane"], "eval golden set workflow lane")
        if golden_set["risk_class"] not in {f"R{i}" for i in range(7)} or golden_set["split"] not in {"development", "held_out", "canary"} or not isinstance(golden_set["case_ids"], list) or not golden_set["case_ids"]: raise EvalPortfolioError("eval golden set key or membership is invalid")
        golden_key = (golden_set["workflow_lane"], golden_set["risk_class"], golden_set["split"])
        if golden_key in golden_keys: raise EvalPortfolioError("eval golden set key is duplicated")
        golden_keys.add(golden_key)
        golden_refs[golden_set["golden_set_ref"]] = golden_set
    taxonomy = _exact(value["taxonomy"], TAXONOMY_FIELDS, "eval taxonomy")
    _id(taxonomy["taxonomy_version"], "eval taxonomy version")
    if taxonomy["refresh_state"] not in allowed_refresh or taxonomy["production_trace_review"] not in allowed_trace_review or taxonomy["unknown_posture"] != "unclassified_requires_triage":
        raise EvalPortfolioError("eval taxonomy must preserve unavailable production trace review")
    if (value["data_class"] == "redacted_evidence") != (taxonomy["production_trace_review"] == "redacted_production_review"):
        raise EvalPortfolioError("taxonomy production provenance disagrees with portfolio data class")
    modes = set()
    if not isinstance(taxonomy["failure_modes"], list) or not taxonomy["failure_modes"]:
        raise EvalPortfolioError("eval taxonomy needs named failure modes")
    for raw_mode in taxonomy["failure_modes"]:
        mode = _exact(raw_mode, FAILURE_MODE_FIELDS, "failure mode")
        _id(mode["failure_mode_id"], "failure mode id")
        if mode["failure_mode_id"] in modes or mode["class_name"] in {"bad_answer", "failed", "generic_failure"}:
            raise EvalPortfolioError("failure mode must be unique and actionable, never generic")
        modes.add(mode["failure_mode_id"])
        if mode["parent_id"] is not None and mode["parent_id"] not in modes:
            raise EvalPortfolioError("failure mode parent must precede and exist")
        _id(mode["class_name"], "failure mode class_name"); _refs(mode["affected_stages"], "failure mode affected_stages")
        _refs(mode["affected_dimensions"], "failure mode affected_dimensions"); _refs(mode["evidence_refs"], "failure mode evidence_refs")
        if mode["status"] not in {"active", "unclassified", "retired"} or mode["provenance"] not in {"synthetic_fixture", "behavior_audit", "human_correction", "production_defect", "security_incident", "negative_knowledge", "accepted_outcome"} or mode["refresh_state"] not in allowed_refresh:
            raise EvalPortfolioError("failure mode vocabulary is invalid")
    cases: dict[str, dict[str, Any]] = {}
    if not isinstance(value["cases"], list) or not value["cases"]:
        raise EvalPortfolioError("eval portfolio needs cases")
    for raw_case in value["cases"]:
        case = _exact(raw_case, CASE_FIELDS, "eval case")
        _id(case["case_id"], "eval case id")
        if case["case_id"] in cases or case["rung"] not in RUNGS:
            raise EvalPortfolioError("eval case id/rung is invalid")
        cases[case["case_id"]] = case
        _id(case["phase_id"], "eval case phase_id"); _id(case["workflow_lane"], "eval case workflow_lane")
        if case["risk_class"] not in {f"R{i}" for i in range(7)} or case["split"] not in {"development", "held_out", "canary"} or case["case_provenance"] not in {"synthetic_fixture", "human_correction", "production_defect", "security_incident", "negative_knowledge", "accepted_outcome"} or case["case_kind"] not in {"task", "evaluator_quality"} or case["lifecycle"] not in {"proposed", "triaged", "accepted", "retired"}: raise EvalPortfolioError("eval case lane, split, provenance, or lifecycle is invalid")
        if case["lifecycle"] in {"proposed", "triaged"}:
            if case["golden_set_ref"] is not None: raise EvalPortfolioError("proposed or triaged case cannot enter a golden set")
            if case["target_golden_set_ref"] is not None and case["target_golden_set_ref"] not in golden_refs: raise EvalPortfolioError("case target golden set is unknown")
        else:
            if case["golden_set_ref"] not in golden_refs or case["case_id"] not in golden_refs[case["golden_set_ref"]]["case_ids"]: raise EvalPortfolioError("accepted/retired case must bind an existing golden-set membership")
            if case["target_golden_set_ref"] not in {None, case["golden_set_ref"]}: raise EvalPortfolioError("case target golden set must match accepted membership")
        golden: dict[str, Any] | None = golden_refs.get(case["golden_set_ref"] or case["target_golden_set_ref"])
        if golden and (golden["workflow_lane"] != case["workflow_lane"] or golden["risk_class"] != case["risk_class"] or golden["split"] != case["split"]): raise EvalPortfolioError("case lane/risk/split does not match golden set")
        if case["case_kind"] == "evaluator_quality" and not case["human_label_ref"]: raise EvalPortfolioError("evaluator-quality case must bind a human-labeled sample")
        if case["case_kind"] == "task" and case["human_label_ref"] is not None: raise EvalPortfolioError("task case cannot claim evaluator-quality human label")
        if not isinstance(case["lifecycle_history"], list): raise EvalPortfolioError("eval case lifecycle history must be append-only list")
        state = "proposed"
        allowed_transitions = {"proposed": {"triaged"}, "triaged": {"accepted"}, "accepted": {"retired"}, "retired": set()}
        for raw_event in case["lifecycle_history"]:
            event = _exact(raw_event, LIFECYCLE_EVENT_FIELDS, "eval case lifecycle event")
            if event["from"] != state or event["to"] not in allowed_transitions[state]: raise EvalPortfolioError("eval case lifecycle transition is invalid")
            _id(event["evidence_ref"], "eval case lifecycle evidence_ref"); state = event["to"]
        if state != case["lifecycle"]: raise EvalPortfolioError("eval case lifecycle does not match append-only history")
        _refs(case["job_stages"], "eval case job_stages")
        if not set(case["job_stages"]).issubset(rubric["stages"]): raise EvalPortfolioError("eval case has unknown workflow rubric stage")
        _validate_adapter(case["adapter_configuration"]); _id(case["evaluator_version"], "eval case evaluator_version"); _refs(case["evidence_refs"], "eval case evidence_refs")
        if case["refresh_state"] not in {"current_synthetic", "refresh_due", "unknown"} or not isinstance(case["diagnosis_required"], bool): raise EvalPortfolioError("eval case refresh/diagnosis is invalid")
        if case["split"] == "held_out":
            forbidden = {"expected_output", "expected_answer", "reference_answer", "golden_answer"}
            if _contains_key(case, forbidden): raise EvalPortfolioError("held-out expected output leaked into executor-visible case projection")
    results: dict[str, dict[str, Any]] = {}
    result_cases: set[str] = set()
    for raw_result in value["results"]:
        result = _exact(raw_result, RESULT_FIELDS, "eval result")
        _id(result["result_id"], "eval result id")
        if result["result_id"] in results or result["case_id"] in result_cases or result["case_id"] not in cases or result["rung"] != cases[result["case_id"]]["rung"]: raise EvalPortfolioError("eval result binding is invalid or has multiple current results")
        results[result["result_id"]] = result; _id(result["attempt_id"], "eval result attempt_id"); _outcome(result["status"], "eval result status")
        result_cases.add(result["case_id"])
        if result["confidence"] not in {"high", "medium", "low", "unknown"}: raise EvalPortfolioError("eval result confidence is invalid")
        _refs(result["evidence_refs"], "eval result evidence_refs")
        dimensions = set()
        for raw_dimension in result["dimension_results"]:
            dimension = _exact(raw_dimension, DIMENSION_FIELDS, "eval dimension result"); _id(dimension["dimension_id"], "eval dimension id")
            if dimension["dimension_id"] in dimensions: raise EvalPortfolioError("eval dimensions cannot duplicate")
            dimensions.add(dimension["dimension_id"]); _outcome(dimension["status"], "eval dimension status")
            if not isinstance(dimension["critical"], bool): raise EvalPortfolioError("eval dimension critical flag is invalid")
            if dimension["direction_vs_baseline"] not in DIRECTIONS: raise EvalPortfolioError("eval dimension direction is invalid")
            _refs(dimension["evidence_refs"], "eval dimension evidence_refs")
        if not dimensions: raise EvalPortfolioError("eval result needs named dimensions, not an aggregate")
        stages = set()
        for raw_stage in result["stage_results"]:
            stage = _exact(raw_stage, STAGE_RESULT_FIELDS, "eval stage result"); _id(stage["stage_id"], "eval stage id")
            if stage["stage_id"] in stages or stage["stage_id"] not in cases[result["case_id"]]["job_stages"]: raise EvalPortfolioError("eval stage result is dangling")
            stages.add(stage["stage_id"]); _outcome(stage["status"], "eval stage status"); _refs(stage["dimension_ids"], "eval stage dimensions"); _refs(stage["evidence_refs"], "eval stage evidence_refs")
            if not set(stage["dimension_ids"]).issubset(dimensions): raise EvalPortfolioError("eval stage dimension is not named in result")
        if cases[result["case_id"]]["diagnosis_required"] and (len(stages) < 2 or not stages): raise EvalPortfolioError("final-only evaluation is not diagnosable")
        if not isinstance(result["evaluator_results"], list) or not result["evaluator_results"]: raise EvalPortfolioError("eval result needs evaluator provenance")
        evaluator_kinds = set()
        for raw_evaluator in result["evaluator_results"]:
            evaluator = _exact(raw_evaluator, EVALUATOR_RESULT_FIELDS, "evaluator result")
            if evaluator["kind"] not in {"deterministic", "judge", "human_acceptance"} or evaluator["kind"] in evaluator_kinds: raise EvalPortfolioError("evaluator result kind is invalid or duplicated")
            evaluator_kinds.add(evaluator["kind"]); _id(evaluator["evaluator_ref"], "evaluator result evaluator_ref"); _id(evaluator["rubric_ref"], "evaluator result rubric_ref")
            if evaluator["provenance"] not in {"synthetic_fixture", "redacted_evidence", "human_labeled_sample"} or evaluator["independence_state"] not in {"independent", "same_actor", "not_required"} or evaluator["status"] not in OUTCOMES or evaluator["confidence"] not in {"high", "medium", "low", "unknown"} or not isinstance(evaluator["critical"], bool) or not isinstance(evaluator["human_accepted"], bool): raise EvalPortfolioError("evaluator result provenance/calibration is invalid")
            calibration = _exact(evaluator["calibration"], CALIBRATION_FIELDS, "evaluator calibration")
            if calibration["status"] not in {"calibrated", "uncalibrated", "not_applicable"} or not isinstance(calibration["sample_count"], int) or isinstance(calibration["sample_count"], bool) or calibration["sample_count"] < 0: raise EvalPortfolioError("evaluator calibration is invalid")
            if calibration["status"] == "calibrated":
                _id(calibration["calibration_ref"], "evaluator calibration ref")
                if calibration["sample_count"] == 0: raise EvalPortfolioError("calibrated evaluator needs a positive calibration sample")
            elif calibration["calibration_ref"] is not None: raise EvalPortfolioError("uncalibrated evaluator cannot name a calibration ref")
            _refs(evaluator["evidence_refs"], "evaluator result evidence_refs")
            if evaluator["lower_bound_evidence_ref"] is not None: _id(evaluator["lower_bound_evidence_ref"], "evaluator lower-bound evidence ref")
            if evaluator["kind"] == "human_acceptance" and ((evaluator["status"] in {"passed"} and evaluator["human_accepted"] is not True) or (evaluator["status"] in {"failed", "blocked", "unknown"} and evaluator["human_accepted"] is not False)): raise EvalPortfolioError("human acceptance evaluator status and acceptance disagree")
            if evaluator["kind"] == "deterministic" and evaluator["human_accepted"] is not False: raise EvalPortfolioError("deterministic evaluator cannot claim human acceptance")
        critical_deterministic_failure = any(item["kind"] == "deterministic" and item["critical"] and item["status"] == "failed" for item in result["evaluator_results"])
        judge_pass = any(item["kind"] == "judge" and item["status"] == "passed" for item in result["evaluator_results"])
        critical_judge_failure = any(item["kind"] == "judge" and item["critical"] and item["status"] in {"failed", "blocked"} for item in result["evaluator_results"])
        critical_dimension_failure = any(item["critical"] and item["status"] in {"failed", "blocked"} for item in result["dimension_results"])
        if (critical_deterministic_failure or critical_judge_failure or critical_dimension_failure) and result["status"] == "passed": raise EvalPortfolioError("critical evaluator or dimension failure cannot be declared passed")
        telemetry = _exact(result["telemetry"], TELEMETRY_FIELDS, "eval telemetry")
        for field in ("latency_ms", "cost_usd", "recovery_count", "intervention_count"): contract._finite_nonnegative(telemetry[field], f"eval telemetry {field}")
        _exact(telemetry["usage"], {"input_tokens", "output_tokens", "cached_input_tokens"}, "eval telemetry usage")
        _exact(telemetry["reset_tax"], {"context_reconstruction_ms", "duplicated_tool_calls", "repeated_failed_approach_count", "human_correction_count", "switch_overhead_ms"}, "eval telemetry reset_tax")
        _refs(result["failure_mode_ids"], "eval result failure modes")
        if not set(result["failure_mode_ids"]).issubset(modes): raise EvalPortfolioError("eval result names unknown failure mode")
    for raw_comparison in value["frontier_comparisons"]:
        comparison = _exact(raw_comparison, COMPARISON_FIELDS, "cost curve comparison"); _id(comparison["comparison_id"], "comparison id")
        if comparison["baseline_result_id"] not in results or comparison["candidate_result_id"] not in results: raise EvalPortfolioError("cost curve comparison has dangling result")
        _refs(comparison["required_dimensions"], "comparison required dimensions")
        tolerances = {}
        if not isinstance(comparison["dimension_tolerances"], list): raise EvalPortfolioError("cost curve dimension tolerances must be a list")
        for raw_tolerance in comparison["dimension_tolerances"]:
            tolerance = _exact(raw_tolerance, TOLERANCE_FIELDS, "cost curve dimension tolerance")
            _id(tolerance["dimension_id"], "cost curve tolerance dimension")
            if tolerance["dimension_id"] in tolerances or tolerance["required_status"] not in OUTCOMES or not isinstance(tolerance["accepted_directions"], list) or not set(tolerance["accepted_directions"]).issubset(DIRECTIONS):
                raise EvalPortfolioError("cost curve dimension tolerance is invalid")
            tolerances[tolerance["dimension_id"]] = tolerance
        if set(tolerances) != set(comparison["required_dimensions"]): raise EvalPortfolioError("cost curve needs explicit tolerance for every critical dimension")
        if not isinstance(comparison["evidence_required"], bool) or comparison["promotion_state"] not in {"eligible_for_human_review", "not_eligible", "blocked"}: raise EvalPortfolioError("cost curve comparison state is invalid")
    for raw_experiment in value["experiments"]:
        experiment = _exact(raw_experiment, EXPERIMENT_FIELDS, "hill climb experiment"); _id(experiment["experiment_id"], "experiment id")
        _id(experiment["primary_dimension"], "experiment primary dimension")
        if experiment["baseline_result_id"] not in results or not isinstance(experiment["hypothesis"], str) or not experiment["hypothesis"]: raise EvalPortfolioError("hill climb experiment binding is invalid")
        _refs(experiment["non_regression_dimensions"], "experiment non-regression dimensions"); _id(experiment["phase_id"], "experiment phase")
        if experiment["session_affinity"] not in {"required", "preferred", "none"} or experiment["switch_condition"] != "verified_checkpoint": raise EvalPortfolioError("experiment must preserve phase affinity and verified checkpoint switching")
        tool_context = _exact(experiment["tool_context"], TOOL_CONTEXT_FIELDS, "experiment tool context")
        for field in ("eligible_count", "exposed_count", "selected_count", "used_count"): contract._nonnegative_int(tool_context[field], f"experiment tool context {field}")
        _id(tool_context["provenance"], "experiment tool context provenance"); _outcome(experiment["result"], "experiment result")
    roots = set()
    for raw_finding in value["behavior_findings"]:
        finding = _exact(raw_finding, BEHAVIOR_FINDING_FIELDS, "eval behavior finding"); _id(finding["finding_id"], "eval behavior finding id"); _id(finding["root_cause_key"], "eval behavior root cause")
        if finding["root_cause_key"] in roots or finding["failure_mode_id"] not in modes: raise EvalPortfolioError("behavior findings must dedupe and map failure taxonomy")
        roots.add(finding["root_cause_key"]); _refs(finding["evidence_refs"], "eval behavior finding evidence")
    if not isinstance(value["notes"], str): raise EvalPortfolioError("eval portfolio notes must be a string")
    return value


def cost_curve_gate(portfolio: Any) -> list[dict[str, Any]]:
    """Evaluate named critical dimensions without calculating an aggregate score."""
    value = validate_eval_portfolio(portfolio)
    results = {row["result_id"]: row for row in value["results"]}
    output = []
    for comparison in value["frontier_comparisons"]:
        baseline, candidate = results[comparison["baseline_result_id"]], results[comparison["candidate_result_id"]]
        index = {row["dimension_id"]: row for row in candidate["dimension_results"]}
        blockers = []
        tolerance_by_dimension = {row["dimension_id"]: row for row in comparison["dimension_tolerances"]}
        for dimension_id in comparison["required_dimensions"]:
            row = index.get(dimension_id)
            tolerance = tolerance_by_dimension[dimension_id]
            if row is None or row["status"] != tolerance["required_status"] or row["direction_vs_baseline"] not in tolerance["accepted_directions"]: blockers.append(dimension_id)
            elif comparison["evidence_required"] and not row["evidence_refs"]: blockers.append(dimension_id)
        state = "eligible_for_human_review" if not blockers else ("blocked" if candidate["status"] in {"blocked", "unknown"} else "not_eligible")
        if comparison["promotion_state"] != state: raise EvalPortfolioError("declared cost curve promotion state disagrees with deterministic critical-dimension gate")
        output.append({"comparison_id": comparison["comparison_id"], "promotion_state": state, "blocked_dimensions": blockers,
                       "baseline": baseline["telemetry"], "candidate": candidate["telemetry"]})
    return output


def critical_dimension_blockers(dimension_results: Any) -> list[str]:
    """Name every critical dimension that blocks, whatever any overall score did.

    The same no-aggregate rule the portfolio applies, exposed for callers that
    hold dimension rows but not a whole portfolio (ops/check-eval-receipt.py,
    the LLM-steering eval receipt). A critical dimension blocks when it failed,
    was blocked, or regressed against baseline. Rows are validated against
    DIMENSION_FIELDS; extra measurement fields are the caller's to strip.
    """
    if not isinstance(dimension_results, list) or not dimension_results:
        raise EvalPortfolioError("eval result needs named dimensions, not an aggregate")
    blockers, seen = [], set()
    for raw in dimension_results:
        row = _exact(raw, DIMENSION_FIELDS, "eval dimension result"); _id(row["dimension_id"], "eval dimension id")
        if row["dimension_id"] in seen: raise EvalPortfolioError("eval dimensions cannot duplicate")
        seen.add(row["dimension_id"]); _outcome(row["status"], "eval dimension status")
        if not isinstance(row["critical"], bool): raise EvalPortfolioError("eval dimension critical flag is invalid")
        if row["direction_vs_baseline"] not in DIRECTIONS: raise EvalPortfolioError("eval dimension direction is invalid")
        _refs(row["evidence_refs"], "eval dimension evidence_refs")
        if row["critical"] and (row["status"] in {"failed", "blocked"} or row["direction_vs_baseline"] == "regressed"):
            blockers.append(row["dimension_id"])
    return blockers


def _active_case(value: dict[str, Any], case: dict[str, Any]) -> bool:
    """Return whether a case has current accepted golden-set membership.

    Lifecycle history is retained for audit and validation, but only an
    accepted case whose case id is still present in its golden set is usable
    as admission evidence.  Retired cases may remain in the portfolio and in
    the append-only golden-set history; they are not active evidence.
    """
    if case["lifecycle"] != "accepted":
        return False
    golden_ref = case["golden_set_ref"]
    if golden_ref is None:
        return False
    golden = next((row for row in value["case_set"]["golden_sets"] if row["golden_set_ref"] == golden_ref), None)
    return golden is not None and case["case_id"] in golden["case_ids"]


def _risk_gate_reasons(value: dict[str, Any], requirement: dict[str, Any], as_of: str | None) -> list[str]:
    """Apply explicit sample, evaluator, confidence, and critical-failure gates.

    This is intentionally a reason-code projection.  It never emits an
    aggregate score and it treats immature or missing evidence as unknown.
    """
    reasons: list[str] = []
    active_case_ids = {case["case_id"] for case in value["cases"] if _active_case(value, case)}
    held_out = [case for case in value["cases"] if case["case_id"] in active_case_ids and case["split"] == "held_out"]
    if len(held_out) < requirement["min_held_out_cases"]:
        reasons.append(f"held_out_sample_insufficient:{len(held_out)}<{requirement['min_held_out_cases']}")
    required_rungs = set(requirement["required_rungs"])
    relevant = [row for row in value["results"] if row["rung"] in required_rungs and row["case_id"] in active_case_ids]
    kinds = {item["kind"] for row in relevant for item in row["evaluator_results"]}
    for kind in requirement["required_evaluator_kinds"]:
        if kind not in kinds: reasons.append(f"required_evaluator_missing:{kind}")
    if requirement["independent_review_required"] and not any(item["independence_state"] == "independent" for row in relevant for item in row["evaluator_results"]):
        reasons.append("independent_review_missing")
    if requirement["human_acceptance_required"] and not any(item["kind"] == "human_acceptance" and item["human_accepted"] for row in relevant for item in row["evaluator_results"]):
        reasons.append("human_acceptance_missing")
    if requirement["confidence_posture"] == "lower_bound_required" and any(row["confidence"] in {"low", "unknown"} for row in relevant):
        reasons.append("confidence_lower_bound_insufficient")
    if requirement["confidence_posture"] == "lower_bound_required" and any(item["lower_bound_evidence_ref"] is None for row in relevant for item in row["evaluator_results"]):
        reasons.append("confidence_lower_bound_evidence_missing")
    if value["drift"]["status"] in {"insufficient", "unavailable"}:
        reasons.append("drift_evidence_insufficient")
    elif value["drift"]["status"] == "exceeds_tolerance" or value["drift"]["observed_delta"] > requirement["drift_tolerance"]:
        reasons.append("drift_tolerance_exceeded")
    critical_dimensions = [dimension for row in relevant for dimension in row["dimension_results"] if dimension["critical"]]
    failed_critical = sum(1 for dimension in critical_dimensions if dimension["status"] == "failed")
    if failed_critical > requirement["max_critical_failure_count"]:
        reasons.append(f"critical_failure_count_exceeded:{failed_critical}>{requirement['max_critical_failure_count']}")
    if critical_dimensions and failed_critical / len(critical_dimensions) > requirement["max_critical_failure_rate"]:
        reasons.append("critical_failure_rate_exceeded")
    if requirement["outcome_horizon_maturity"] == "required" and (value["outcome_horizon"]["status"] != "mature" or as_of is None or as_of < value["outcome_horizon"]["matures_at"]):
        reasons.append("outcome_horizon_immature")
    return reasons


def admission_decision(kernel: Any, *, as_of: str | None = None, visual_gate_report: Any | None = None,
                       design_contract: Any | None = None, policy_learning_assessment: Any | None = None) -> dict[str, Any]:
    """Return a deterministic recommendation with evidence-backed reason codes.

    It never promotes canonical state.  `eligible_for_controller_review` means
    only that the shared evidence contract meets the selected workflow policy.
    """
    value = validate_eval_portfolio(kernel)
    requirement = next((row for row in value["policy"]["risk_requirements"]
                        if row["risk_class"] == value["binding"]["risk_class"] and row["lifecycle"] == value["binding"]["lifecycle"]), None)
    evidence = []
    reasons = []
    if as_of is not None:
        contract._timestamp(as_of, "evaluation admission as_of")
        if as_of > value["provenance"]["expires_at"]:
            reasons.append("evidence_stale_or_revalidation_required")
    if requirement is None:
        reasons.append("risk_lifecycle_unmapped_default_deny")
    else:
        reasons.extend(_risk_gate_reasons(value, requirement, as_of))
    results_by_case = {row["case_id"]: row for row in value["results"]}
    for rung in requirement["required_rungs"] if requirement else []:
        cases = [case for case in value["cases"] if case["rung"] == rung]
        if not cases:
            reasons.append(f"required_rung_missing:{rung}")
            continue
        for case in cases:
            if not _active_case(value, case):
                reasons.append(f"required_case_not_active:{case['case_id']}")
                continue
            result = results_by_case.get(case["case_id"])
            if result is None:
                reasons.append(f"required_case_not_run:{case['case_id']}")
            elif result["status"] != "passed":
                reasons.append(f"required_case_not_passed:{case['case_id']}:{result['status']}")
            else:
                evidence.extend(result["evidence_refs"])
    result_index = {row["result_id"]: row for row in value["results"]}
    for comparison in cost_curve_gate(value):
        candidate = result_index[next(row["candidate_result_id"] for row in value["frontier_comparisons"] if row["comparison_id"] == comparison["comparison_id"])]
        if requirement and candidate["rung"] not in requirement["required_rungs"]:
            continue
        if comparison["promotion_state"] != "eligible_for_human_review":
            reasons.append(f"cost_curve_not_eligible:{comparison['comparison_id']}:{comparison['promotion_state']}")
            for dimension in comparison["blocked_dimensions"]:
                reasons.append(f"critical_dimension_not_equivalent:{dimension}")
    if (visual_gate_report is None) != (design_contract is None):
        raise EvalPortfolioError("visual gate report and exact design contract must travel together")
    if visual_gate_report is not None:
        try:
            reasons.extend(design_kernel.evaluation_blockers(
                visual_gate_report, design_contract,
                expected_work_request_id=value["binding"]["work_request_id"],
                expected_projection_digest=value["binding"]["projection_digest"],
            ))
        except design_kernel.DesignKernelError as exc:
            raise EvalPortfolioError(str(exc)) from exc
    if policy_learning_assessment is not None:
        try:
            # This is a reason-code projection, not a score contribution: a
            # learning refusal cannot be averaged away by other dimensions.
            reasons.extend(policy_learning.evaluation_kernel_blockers(policy_learning_assessment))
        except policy_learning.PolicyLearningError as exc:
            raise EvalPortfolioError(str(exc)) from exc
    if value["provenance"]["source_class"] == "synthetic_only":
        reasons.append("synthetic_evidence_not_controller_promotion")
    evidence_insufficient = any(reason.startswith(("held_out_sample_insufficient", "outcome_horizon_immature", "required_evaluator_missing", "human_acceptance_missing", "confidence_lower_bound_insufficient", "confidence_lower_bound_evidence_missing", "drift_evidence_insufficient")) for reason in reasons)
    return {
        "schema_version": "carr-evaluation-admission-decision.v1",
        "workflow_id": value["workflow"]["workflow_id"],
        "portfolio_id": value["portfolio_id"],
        "portfolio_digest": contract.canonical_digest(value),
        "decision": "eligible_for_controller_review" if not reasons else ("insufficient_evidence" if evidence_insufficient else "not_admitted"),
        "reason_codes": sorted(set(reasons)),
        "evidence_refs": sorted(set(evidence)),
        "controller_promotion": "not_performed",
    }


# Canonical shared names. The legacy aliases below keep the prior Job Passport
# call sites read-compatible while migrations are in progress.
validate_evaluation_kernel = validate_eval_portfolio
evaluate_admission = admission_decision
