"""CARR's offline-only Policy Learning Envelope v1.

This module makes policy-learning claims inspectable before CARR ever considers
an experiment.  It is deliberately *not* a trainer, router, policy store, or
promotion actuator: it consumes frozen, metadata-only decision evidence and
returns a fail-closed recommendation for human review.

The estimator is contextual-bandit OPE over named outcome dimensions.  It never
collapses dimensions into a reward scalar and never claims causal improvement:
the evidence can be observational even when propensities are known.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import execution_contract as contract


class PolicyLearningError(contract.ContractError):
    """Policy-learning evidence is unsafe, incomplete, or not comparable."""


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "control-room" / "contracts" / "policy-learning-formulation-registry.v1.json"

ROOT_FIELDS = {"schema_version", "assessment_id", "data_class", "generated_at", "domain", "reference_policy", "candidate_policy", "evaluation_window", "promotion_request", "decisions", "notes"}
POLICY_FIELDS = {"policy_id", "version", "digest", "frozen_at"}
DOMAIN_FIELDS = {"domain_id", "formulation", "learnability"}
WINDOW_FIELDS = {"rubric_id", "rubric_version", "rubric_digest", "criteria_frozen_at", "held_out", "independent_verifier", "judge_provenance", "outcome_horizon_ends_at", "expires_at", "criterion_mutated_during_window", "candidate_builder_actor", "judge_actor", "promoter_actor"}
PROMOTION_FIELDS = {"current_state", "requested_state", "policy_version_cas", "human_approval", "exploration_budget", "allowlisted_actions", "risk_ceiling", "rollback_reference_digest", "kill_switch", "expires_at"}
DECISION_FIELDS = {"decision_id", "decision_digest", "work_request_id", "correlation_id", "observed_at", "risk_class", "policy", "context_projection", "eligible_actions", "selected_action", "behavior", "candidate_action_probabilities", "action_trace", "outcome"}
CONTEXT_FIELDS = {"projection_digest", "data_class", "bounded_metadata", "field_allowlist"}
BEHAVIOR_FIELDS = {"selection_method", "propensity"}
TRACE_FIELDS = {"agent_actions", "tool_observations", "masked_tool_observation_count"}
OUTCOME_FIELDS = {"terminal", "process"}
SIGNAL_SET_FIELDS = {"availability", "observed_at", "signals"}
SIGNAL_FIELDS = {"dimension_id", "value", "status", "critical", "verifier_kind", "verifier_ref", "judge_provenance", "human_accepted"}

LIFECYCLE = ("observed", "shadow", "eligible_for_human_review", "bounded_exploration", "active")
SELECTION_METHODS = {"deterministic", "human", "exploration"}
VERIFIERS = {"deterministic", "judge", "human_acceptance"}
SIGNAL_STATUS = {"passed", "failed", "blocked", "unknown"}
RISK = {f"R{i}" for i in range(7)}


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    try:
        return contract._expect_exact(value, fields, label)
    except contract.ContractError as exc:
        raise PolicyLearningError(str(exc)) from exc


def _id(value: Any, label: str) -> str:
    try:
        return contract._string(value, label, identifier=True)
    except contract.ContractError as exc:
        raise PolicyLearningError(str(exc)) from exc


def _digest(value: Any, label: str) -> str:
    try:
        return contract._digest(value, label)
    except contract.ContractError as exc:
        raise PolicyLearningError(str(exc)) from exc


def _timestamp(value: Any, label: str) -> str:
    try:
        return contract._timestamp(value, label)
    except contract.ContractError as exc:
        raise PolicyLearningError(str(exc)) from exc


def _probability(value: Any, label: str, *, strictly_positive: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise PolicyLearningError(f"{label} must be a finite probability")
    value = float(value)
    if value < 0 or value > 1 or (strictly_positive and value == 0):
        raise PolicyLearningError(f"{label} must be {'in (0,1]' if strictly_positive else 'in [0,1]'}")
    return value


def canonical_digest(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PolicyLearningError("policy-learning evidence must be JSON serializable") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def formulation_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text())


def _policy(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, POLICY_FIELDS, label)
    _id(row["policy_id"], f"{label} policy_id")
    contract._string(row["version"], f"{label} version")
    _digest(row["digest"], f"{label} digest")
    _timestamp(row["frozen_at"], f"{label} frozen_at")
    return row


def _registry_domain(domain: dict[str, Any]) -> dict[str, Any]:
    registry = formulation_registry()
    match = next((row for row in registry["domains"] if row["domain_id"] == domain["domain_id"]), None)
    if match is None:
        raise PolicyLearningError("policy-learning domain is not registered")
    if any(domain[field] != match[field] for field in ("formulation", "learnability")):
        raise PolicyLearningError("policy-learning domain formulation does not match the CARR registry")
    return match


def _window(value: Any) -> dict[str, Any]:
    row = _exact(value, WINDOW_FIELDS, "policy-learning evaluation window")
    for field in ("rubric_id", "judge_provenance", "candidate_builder_actor", "judge_actor", "promoter_actor"):
        _id(row[field], f"policy-learning evaluation window {field}")
    contract._string(row["rubric_version"], "policy-learning evaluation window rubric_version")
    _digest(row["rubric_digest"], "policy-learning evaluation window rubric_digest")
    for field in ("criteria_frozen_at", "outcome_horizon_ends_at", "expires_at"):
        _timestamp(row[field], f"policy-learning evaluation window {field}")
    if row["held_out"] is not True or row["independent_verifier"] is not True:
        raise PolicyLearningError("policy-learning evaluation must be held out and independently verified")
    if row["criterion_mutated_during_window"] is not False:
        raise PolicyLearningError("policy-learning criteria may not mutate inside their evaluation window")
    actors = (row["candidate_builder_actor"], row["judge_actor"], row["promoter_actor"])
    if len(set(actors)) != len(actors):
        raise PolicyLearningError("candidate builder, judge, and promoter must be distinct actors")
    return row


def _promotion(value: Any, reference: dict[str, Any], as_of: str | None) -> dict[str, Any]:
    row = _exact(value, PROMOTION_FIELDS, "policy-learning promotion request")
    if row["current_state"] not in LIFECYCLE or row["requested_state"] not in LIFECYCLE:
        raise PolicyLearningError("policy-learning promotion lifecycle state is invalid")
    if row["requested_state"] not in {"observed", "shadow", "eligible_for_human_review"}:
        raise PolicyLearningError("offline policy-learning v1 cannot request bounded exploration or active policy")
    _id(row["policy_version_cas"], "policy-learning policy_version_cas")
    if row["human_approval"] is not False:
        raise PolicyLearningError("offline assessment cannot carry human approval")
    if not isinstance(row["exploration_budget"], int) or isinstance(row["exploration_budget"], bool) or row["exploration_budget"] != 0:
        raise PolicyLearningError("offline policy-learning v1 requires zero exploration budget")
    if not isinstance(row["allowlisted_actions"], list) or not row["allowlisted_actions"]:
        raise PolicyLearningError("policy-learning promotion request needs explicit allowlisted actions")
    for action in row["allowlisted_actions"]: _id(action, "policy-learning allowlisted action")
    if row["risk_ceiling"] not in RISK:
        raise PolicyLearningError("policy-learning promotion request risk ceiling is invalid")
    if row["rollback_reference_digest"] != reference["digest"]:
        raise PolicyLearningError("policy-learning rollback must name the exact frozen reference policy")
    if row["kill_switch"] is not True:
        raise PolicyLearningError("policy-learning promotion request requires a kill switch")
    _timestamp(row["expires_at"], "policy-learning promotion expiry")
    if as_of is not None and as_of > row["expires_at"]:
        raise PolicyLearningError("policy-learning promotion request is expired")
    return row


def _signal_set(value: Any, label: str, window: dict[str, Any], decision_observed_at: str) -> dict[str, Any]:
    row = _exact(value, SIGNAL_SET_FIELDS, label)
    if row["availability"] not in {"available", "unavailable", "stale"}:
        raise PolicyLearningError(f"{label} availability is invalid")
    _timestamp(row["observed_at"], f"{label} observed_at")
    if row["observed_at"] < decision_observed_at:
        raise PolicyLearningError(f"{label} cannot precede the decision evidence")
    if row["observed_at"] > window["outcome_horizon_ends_at"]:
        raise PolicyLearningError(f"{label} exceeds the frozen outcome horizon")
    if not isinstance(row["signals"], list) or not row["signals"]:
        raise PolicyLearningError(f"{label} needs one or more named signals")
    ids = set()
    for signal in row["signals"]:
        checked = _exact(signal, SIGNAL_FIELDS, f"{label} signal")
        dimension = _id(checked["dimension_id"], f"{label} signal dimension_id")
        if dimension in ids: raise PolicyLearningError(f"{label} repeats a named dimension")
        ids.add(dimension)
        _probability(checked["value"], f"{label} signal value")
        if checked["status"] not in SIGNAL_STATUS or not isinstance(checked["critical"], bool):
            raise PolicyLearningError(f"{label} signal status/critical is invalid")
        if checked["verifier_kind"] not in VERIFIERS: raise PolicyLearningError(f"{label} signal verifier kind is invalid")
        _id(checked["verifier_ref"], f"{label} signal verifier_ref")
        _id(checked["judge_provenance"], f"{label} signal judge_provenance")
        if checked["verifier_kind"] == "judge" and checked["judge_provenance"] != window["judge_provenance"]:
            raise PolicyLearningError("judge signal provenance must bind the frozen evaluation window")
        if checked["verifier_kind"] == "human_acceptance" and checked["human_accepted"] is not True:
            raise PolicyLearningError("human acceptance signal must name human acceptance")
        if checked["verifier_kind"] == "deterministic" and checked["human_accepted"] is not False:
            raise PolicyLearningError("deterministic verifier signal cannot claim human acceptance")
    return row


def _decision_preimage(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "decision_digest"}


def _decision(value: Any, *, reference: dict[str, Any], candidate: dict[str, Any], domain: dict[str, Any], window: dict[str, Any], data_class: str) -> dict[str, Any]:
    row = _exact(value, DECISION_FIELDS, "policy-learning decision evidence")
    for field in ("decision_id", "work_request_id", "correlation_id"):
        _id(row[field], f"policy-learning decision {field}")
    _digest(row["decision_digest"], "policy-learning decision digest")
    if row["decision_digest"] != canonical_digest(_decision_preimage(row)):
        raise PolicyLearningError("policy-learning decision digest does not bind its immutable evidence")
    _timestamp(row["observed_at"], "policy-learning decision observed_at")
    if row["observed_at"] < reference["frozen_at"] or row["observed_at"] < window["criteria_frozen_at"]:
        raise PolicyLearningError("policy-learning decision evidence predates the frozen policy or criteria")
    if row["observed_at"] > window["outcome_horizon_ends_at"]:
        raise PolicyLearningError("policy-learning decision evidence exceeds the frozen outcome horizon")
    if row["risk_class"] not in RISK: raise PolicyLearningError("policy-learning decision risk class is invalid")
    if int(row["risk_class"][1:]) > int(domain["max_risk_class"][1:]):
        raise PolicyLearningError("policy-learning decision exceeds this domain's risk ceiling")
    policy = _policy(row["policy"], "policy-learning decision policy")
    if policy != reference:
        raise PolicyLearningError("policy-learning decision must bind the exact frozen reference policy")
    context = _exact(row["context_projection"], CONTEXT_FIELDS, "policy-learning bounded context projection")
    _digest(context["projection_digest"], "policy-learning context projection digest")
    if context["data_class"] != data_class or context["bounded_metadata"] is not True or not isinstance(context["field_allowlist"], list):
        raise PolicyLearningError("policy-learning context must be bounded metadata matching the assessment class")
    forbidden = {"transcript", "message_content", "business_content", "private_content", "tool_output"}
    if forbidden & set(context["field_allowlist"]):
        raise PolicyLearningError("policy-learning context refuses private content or tool output")
    if not isinstance(row["eligible_actions"], list) or not row["eligible_actions"]:
        raise PolicyLearningError("policy-learning decision needs an eligible action set")
    actions = row["eligible_actions"]
    if len(set(actions)) != len(actions): raise PolicyLearningError("policy-learning eligible actions must be unique")
    registry = formulation_registry()
    for action in actions:
        _id(action, "policy-learning eligible action")
        kind = action.split(":", 1)[0]
        if kind in registry["ineligible_action_classes"] or kind not in domain["allowed_action_kinds"]:
            raise PolicyLearningError("policy-learning action kind is ineligible for this reversible domain")
    _id(row["selected_action"], "policy-learning selected action")
    if row["selected_action"] not in actions: raise PolicyLearningError("policy-learning selected action is not eligible")
    behavior = _exact(row["behavior"], BEHAVIOR_FIELDS, "policy-learning behavior policy evidence")
    if behavior["selection_method"] not in SELECTION_METHODS: raise PolicyLearningError("policy-learning selection method is invalid")
    propensity = _probability(behavior["propensity"], "policy-learning behavior propensity", strictly_positive=True)
    if behavior["selection_method"] == "deterministic" and propensity != 1.0:
        raise PolicyLearningError("deterministic policy evidence needs exact singleton propensity 1.0")
    if behavior["selection_method"] == "deterministic" and len(actions) != 1:
        raise PolicyLearningError("deterministic propensity 1.0 is valid only for a singleton eligible action set")
    candidate_probs = row["candidate_action_probabilities"]
    if not isinstance(candidate_probs, dict) or set(candidate_probs) != set(actions):
        raise PolicyLearningError("candidate probabilities must exactly cover the eligible action set")
    if abs(sum(_probability(prob, "policy-learning candidate action probability") for prob in candidate_probs.values()) - 1.0) > 1e-9:
        raise PolicyLearningError("candidate action probabilities must sum to 1")
    trace = _exact(row["action_trace"], TRACE_FIELDS, "policy-learning agentic action trace")
    if trace["agent_actions"] != [row["selected_action"]]:
        raise PolicyLearningError("only the selected agent action may be evaluated; tool observations are masked")
    if not isinstance(trace["tool_observations"], list) or not isinstance(trace["masked_tool_observation_count"], int) or trace["masked_tool_observation_count"] != len(trace["tool_observations"]):
        raise PolicyLearningError("tool/environment observations must be present only as masked observations")
    outcome = _exact(row["outcome"], OUTCOME_FIELDS, "policy-learning outcome projection")
    _signal_set(outcome["terminal"], "policy-learning terminal outcome", window, row["observed_at"])
    _signal_set(outcome["process"], "policy-learning process outcome", window, row["observed_at"])
    return row


def validate_policy_learning_envelope(raw: Any, *, as_of: str | None = None) -> dict[str, Any]:
    """Validate frozen OPE evidence and every policy-learning safety boundary."""
    if as_of is not None: _timestamp(as_of, "policy-learning assessment as_of")
    value = _exact(raw, ROOT_FIELDS, "policy-learning envelope")
    if value["schema_version"] != "carr-policy-learning-envelope.v1":
        raise PolicyLearningError("unsupported policy-learning envelope schema_version")
    _id(value["assessment_id"], "policy-learning assessment_id")
    if value["data_class"] not in {"synthetic_only", "metadata_only"}:
        raise PolicyLearningError("policy-learning v1 accepts only synthetic or bounded metadata evidence")
    _timestamp(value["generated_at"], "policy-learning generated_at")
    domain = _exact(value["domain"], DOMAIN_FIELDS, "policy-learning domain")
    _id(domain["domain_id"], "policy-learning domain_id")
    registered_domain = _registry_domain(domain)
    if registered_domain["learnability"] != "offline_policy_evaluation_only":
        raise PolicyLearningError("trajectory/process formulations are observational only in policy-learning v1")
    reference, candidate = _policy(value["reference_policy"], "policy-learning reference policy"), _policy(value["candidate_policy"], "policy-learning candidate policy")
    if reference == candidate: raise PolicyLearningError("candidate policy must differ from the frozen reference policy")
    window = _window(value["evaluation_window"])
    if reference["frozen_at"] > value["generated_at"] or candidate["frozen_at"] > value["generated_at"] or window["criteria_frozen_at"] > value["generated_at"]:
        raise PolicyLearningError("policy-learning assessment cannot precede its frozen policy or criteria")
    if value["generated_at"] > window["expires_at"] or value["generated_at"] > window["outcome_horizon_ends_at"]:
        raise PolicyLearningError("policy-learning assessment is generated after its window or outcome horizon")
    if as_of is not None and as_of < value["generated_at"]:
        raise PolicyLearningError("policy-learning assessment is from the future relative to as_of")
    if as_of is not None and (as_of > window["expires_at"] or as_of > window["outcome_horizon_ends_at"]):
        raise PolicyLearningError("policy-learning evaluation window or outcome horizon is stale")
    _promotion(value["promotion_request"], reference, as_of)
    if not isinstance(value["decisions"], list) or not value["decisions"]:
        raise PolicyLearningError("policy-learning assessment needs decision evidence")
    ids, digests = set(), set()
    decisions = []
    for raw_decision in value["decisions"]:
        row = _decision(raw_decision, reference=reference, candidate=candidate, domain=registered_domain, window=window, data_class=value["data_class"])
        if row["decision_id"] in ids or row["decision_digest"] in digests:
            raise PolicyLearningError("policy-learning decision evidence must be uniquely immutable")
        ids.add(row["decision_id"]); digests.add(row["decision_digest"]); decisions.append(row)
    return value


def _reasoned_failure(reasons: list[str], *, estimators: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"schema_version": "carr-policy-learning-assessment.v1", "decision": "not_eligible", "reason_codes": sorted(set(reasons)), "estimators": estimators or [], "causal_claim": "not_established_without_randomized_or_known_assignment"}


def evaluation_kernel_blockers(assessment: Any) -> list[str]:
    """Project a learning refusal into the shared Evaluation Kernel vocabulary.

    It intentionally projects refusals only.  A safe learning assessment is
    still merely eligible for *human* review and must not change an otherwise
    independent evaluation decision.
    """
    row = _exact(assessment, {"schema_version", "decision", "reason_codes", "estimators", "causal_claim"}, "policy-learning assessment")
    if row["schema_version"] != "carr-policy-learning-assessment.v1":
        raise PolicyLearningError("unsupported policy-learning assessment schema_version")
    if row["decision"] not in {"eligible_for_human_review", "not_eligible"}:
        raise PolicyLearningError("policy-learning assessment decision is invalid")
    if not isinstance(row["reason_codes"], list) or not all(isinstance(item, str) and item for item in row["reason_codes"]):
        raise PolicyLearningError("policy-learning assessment reason codes are invalid")
    if row["causal_claim"] != "not_established_without_randomized_or_known_assignment":
        raise PolicyLearningError("policy-learning assessment may not claim causal improvement")
    if row["decision"] == "eligible_for_human_review": return []
    return ["policy_learning_not_eligible"] + [f"policy_learning_blocker:{code}" for code in row["reason_codes"]]


def offline_candidate_evaluation(raw: Any, *, as_of: str | None = None, max_importance_weight: float = 10.0, min_effective_sample_size: float = 5.0, critical_tolerance: float = 0.0) -> dict[str, Any]:
    """Evaluate a candidate policy against a frozen reference, per dimension.

    IPS and self-normalized IPS are reported separately for each terminal and
    process dimension.  Clipping is explicitly disclosed; it does not make the
    result causal or authorize a policy change.
    """
    if max_importance_weight <= 0 or min_effective_sample_size <= 0 or critical_tolerance < 0:
        raise PolicyLearningError("offline evaluation bounds must be positive and explicit")
    value = validate_policy_learning_envelope(raw, as_of=as_of)
    reasons: list[str] = []
    decisions = value["decisions"]
    observed_actions = {row["selected_action"] for row in decisions}
    candidate_actions = {action for row in decisions for action, probability in row["candidate_action_probabilities"].items() if probability > 0}
    missing_coverage = sorted(candidate_actions - observed_actions)
    if missing_coverage: reasons.append("candidate_action_without_observed_support:" + ",".join(missing_coverage))
    dimensions: dict[tuple[str, str], list[tuple[float, float, float, bool]]] = {}
    for row in decisions:
        propensity = row["behavior"]["propensity"]
        if propensity is None or propensity <= 0:
            reasons.append("unknown_or_zero_behavior_propensity")
            continue
        candidate_probability = row["candidate_action_probabilities"][row["selected_action"]]
        raw_weight = candidate_probability / propensity
        for signal_kind in ("terminal", "process"):
            signals = row["outcome"][signal_kind]
            if signals["availability"] != "available":
                reasons.append(f"{signal_kind}_outcome_{signals['availability']}")
                continue
            for signal in signals["signals"]:
                if signal["status"] != "passed":
                    reasons.append(f"{signal_kind}_signal_not_passed:{signal['dimension_id']}:{signal['status']}")
                dimensions.setdefault((signal_kind, signal["dimension_id"]), []).append((raw_weight, float(signal["value"]), 1.0, bool(signal["critical"])))
    estimators: list[dict[str, Any]] = []
    for (signal_kind, dimension), rows in sorted(dimensions.items()):
        raw_weights = [r[0] for r in rows]
        clipped = [min(weight, max_importance_weight) for weight in raw_weights]
        rewards = [r[1] for r in rows]
        reference_mean = sum(rewards) / len(rewards)
        sum_weights = sum(clipped)
        ips = sum(weight * reward for weight, reward in zip(clipped, rewards)) / len(rows)
        snips = None if sum_weights == 0 else sum(weight * reward for weight, reward in zip(clipped, rewards)) / sum_weights
        ess = 0.0 if not sum_weights else (sum_weights * sum_weights) / sum(weight * weight for weight in clipped)
        clipped_count = sum(1 for weight in raw_weights if weight > max_importance_weight)
        critical = any(r[3] for r in rows)
        coverage = sum(1 for weight in raw_weights if weight > 0) / len(rows)
        estimator = {"signal_kind": signal_kind, "dimension_id": dimension, "reference_mean": reference_mean, "ips": ips, "snips": snips, "effective_sample_size": ess, "matched_action_coverage": coverage, "raw_max_weight": max(raw_weights), "weight_clip": max_importance_weight, "clipped_count": clipped_count, "critical": critical, "uncertainty": "observational_estimate_not_causal"}
        estimators.append(estimator)
        if ess < min_effective_sample_size: reasons.append(f"low_effective_sample_size:{signal_kind}:{dimension}")
        if coverage < 1.0: reasons.append(f"incomplete_matched_action_coverage:{signal_kind}:{dimension}")
        if clipped_count: reasons.append(f"importance_weight_clipped_bias_variance_tradeoff:{signal_kind}:{dimension}")
        if critical and snips is not None and snips < reference_mean - critical_tolerance:
            reasons.append(f"critical_dimension_regression:{signal_kind}:{dimension}")
    if not dimensions: reasons.append("no_available_outcome_dimensions")
    if any(reason.startswith(("unknown_or_zero", "candidate_action_without", "low_effective", "critical_dimension", "terminal_outcome", "process_outcome")) for reason in reasons):
        return _reasoned_failure(reasons, estimators=estimators)
    if any("signal_not_passed" in reason for reason in reasons):
        return _reasoned_failure(reasons, estimators=estimators)
    return {"schema_version": "carr-policy-learning-assessment.v1", "decision": "eligible_for_human_review", "reason_codes": sorted(set(reasons + ["offline_observational_evidence_only", "human_approval_required_before_any_exploration"])), "estimators": estimators, "causal_claim": "not_established_without_randomized_or_known_assignment"}


SHADOW_FIELDS = {
    "comparison_id", "case_ref", "context_binding", "baseline_route", "candidate_route",
    "baseline_binding_digest", "candidate_binding_digest", "route_dimensions",
    "evaluation_dimensions", "dimension_results", "candidate_execution", "outcome_horizon", "result_provenance",
    "state", "promotion_state", "requested_state", "policy_version_cas",
    "kill_switch_ref", "rollback_ref", "expires_at", "evidence_refs",
}
SHADOW_SHARED_BINDING_FIELDS = {
    "work_request_id", "input_binding_digest", "context_binding_digest", "case_ref",
    "case_digest", "case_set_digest", "bounded_metadata", "field_allowlist",
}
SHADOW_ROUTE_BINDING_FIELDS = {
    "route_ref", "runtime_profile", "runtime_binding_digest", "route_binding_digest",
}
SHADOW_RUNTIME_PROFILE_FIELDS = {
    "provider_ref", "model_ref", "reasoning_effort_ref", "topology_digest", "grounding_digest",
}
SHADOW_EXECUTION_FIELDS = {
    "allowed_actions", "external_side_effects", "side_effect_attempted",
    "capability_refusal_evidence_refs",
}
SHADOW_HORIZON_FIELDS = {"state", "ends_at", "evidence_refs"}
SHADOW_PROVENANCE_FIELDS = {
    "baseline_evidence_refs", "candidate_evidence_refs", "evaluator_ref", "evaluator_kind",
    "independence_state", "redaction_class", "raw_content_present", "private_content_present",
}
SHADOW_DIMENSION_FIELDS = {
    "dimension_id", "baseline_status", "candidate_status", "critical", "verifier_kind",
    "baseline_value", "candidate_value", "direction", "evidence_refs",
}
SHADOW_LIFECYCLE = {"observed", "shadow", "eligible_for_human_review", "bounded_exploration", "active"}
SHADOW_STATUSES = {"passed", "failed", "blocked", "unknown"}
SHADOW_VERIFIERS = {"deterministic", "judge", "human_acceptance"}
SHADOW_FORBIDDEN_FIELDS = {
    "raw_prompt", "raw_transcript", "raw_output", "prompt", "transcript", "tool_payload",
    "tool_input", "tool_output", "message_content", "business_content", "private_content",
}
SHADOW_RUNTIME_DIMENSIONS = {
    "provider": "provider_ref",
    "model": "model_ref",
    "reasoning_effort": "reasoning_effort_ref",
    "topology": "topology_digest",
    "grounding": "grounding_digest",
}


def _refs(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise PolicyLearningError(f"{label} must be a non-empty list of references")
    output = []
    for index, item in enumerate(value):
        output.append(_id(item, f"{label}[{index}]"))
    if len(set(output)) != len(output):
        raise PolicyLearningError(f"{label} references must be unique")
    return output


def _bounded_value(value: Any, label: str) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        raise PolicyLearningError(f"{label} must not be boolean")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise PolicyLearningError(f"{label} must be finite")
        return value
    return _id(value, label)


def _validate_shadow_shared_binding(value: Any) -> dict[str, Any]:
    row = _exact(value, SHADOW_SHARED_BINDING_FIELDS, "shadow comparison shared binding")
    _id(row["work_request_id"], "shadow comparison work_request_id")
    _id(row["case_ref"], "shadow comparison frozen case_ref")
    for field in ("input_binding_digest", "context_binding_digest", "case_digest", "case_set_digest"):
        _digest(row[field], f"shadow comparison {field}")
    if row["bounded_metadata"] is not True or not isinstance(row["field_allowlist"], list) or not row["field_allowlist"]:
        raise PolicyLearningError("shadow comparison binding must be bounded metadata")
    for field in row["field_allowlist"]:
        if not isinstance(field, str) or not field or field in SHADOW_FORBIDDEN_FIELDS:
            raise PolicyLearningError("shadow comparison binding refuses raw or private fields")
    return row


def _validate_shadow_route_binding_metadata(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, SHADOW_ROUTE_BINDING_FIELDS, f"shadow comparison {label} route binding")
    _id(row["route_ref"], f"shadow comparison {label} route_ref")
    profile = _exact(row["runtime_profile"], SHADOW_RUNTIME_PROFILE_FIELDS, f"shadow comparison {label} runtime profile")
    for field in ("provider_ref", "model_ref", "reasoning_effort_ref"):
        _id(profile[field], f"shadow comparison {label} {field}")
    for field in ("topology_digest", "grounding_digest"):
        _digest(profile[field], f"shadow comparison {label} {field}")
    _digest(row["runtime_binding_digest"], f"shadow comparison {label} runtime_binding_digest")
    expected_runtime_digest = canonical_digest({"route_ref": row["route_ref"], "runtime_profile": profile})
    if row["runtime_binding_digest"] != expected_runtime_digest:
        raise PolicyLearningError(f"shadow comparison {label} runtime digest is not tied to its route/profile")
    _digest(row["route_binding_digest"], f"shadow comparison {label} route_binding_digest")
    expected_route_digest = canonical_digest({"route_ref": row["route_ref"], "runtime_binding_digest": row["runtime_binding_digest"]})
    if row["route_binding_digest"] != expected_route_digest:
        raise PolicyLearningError(f"shadow comparison {label} route digest is not tied to its runtime binding")
    return row


def _validate_shadow_binding(value: Any) -> dict[str, Any]:
    row = _exact(value, {"baseline", "candidate"}, "shadow comparison frozen binding")
    baseline = _exact(row["baseline"], SHADOW_SHARED_BINDING_FIELDS | SHADOW_ROUTE_BINDING_FIELDS, "shadow comparison baseline binding")
    candidate = _exact(row["candidate"], SHADOW_SHARED_BINDING_FIELDS | SHADOW_ROUTE_BINDING_FIELDS, "shadow comparison candidate binding")
    baseline_shared = _validate_shadow_shared_binding({key: baseline[key] for key in SHADOW_SHARED_BINDING_FIELDS})
    candidate_shared = _validate_shadow_shared_binding({key: candidate[key] for key in SHADOW_SHARED_BINDING_FIELDS})
    if baseline_shared != candidate_shared:
        raise PolicyLearningError("baseline and candidate shared Work Request/input/context/case bindings must match")
    baseline_route = _validate_shadow_route_binding_metadata({key: baseline[key] for key in SHADOW_ROUTE_BINDING_FIELDS}, "baseline")
    candidate_route = _validate_shadow_route_binding_metadata({key: candidate[key] for key in SHADOW_ROUTE_BINDING_FIELDS}, "candidate")
    if baseline_route["route_binding_digest"] == candidate_route["route_binding_digest"]:
        raise PolicyLearningError("baseline and candidate route binding digests must differ")
    if baseline_route["runtime_binding_digest"] == candidate_route["runtime_binding_digest"]:
        raise PolicyLearningError("baseline and candidate runtime binding digests must differ")
    return {"baseline": baseline, "candidate": candidate, "shared": baseline_shared}


def _shadow_runtime_changes(binding: dict[str, Any]) -> set[str]:
    baseline = binding["baseline"]["runtime_profile"]
    candidate = binding["candidate"]["runtime_profile"]
    return {dimension for dimension, field in SHADOW_RUNTIME_DIMENSIONS.items() if baseline[field] != candidate[field]}


def _validate_shadow_dimension(value: Any, index: int, declared_dimensions: set[str]) -> dict[str, Any]:
    row = _exact(value, SHADOW_DIMENSION_FIELDS, f"shadow comparison dimension[{index}]")
    dimension_id = _id(row["dimension_id"], f"shadow comparison dimension[{index}] id")
    if dimension_id not in declared_dimensions:
        raise PolicyLearningError("shadow comparison dimension is not declared in route or evaluation dimensions")
    if row["baseline_status"] not in SHADOW_STATUSES or row["candidate_status"] not in SHADOW_STATUSES:
        raise PolicyLearningError("shadow comparison dimension status is invalid")
    if not isinstance(row["critical"], bool) or row["verifier_kind"] not in SHADOW_VERIFIERS:
        raise PolicyLearningError("shadow comparison dimension verifier/critical flag is invalid")
    if row["direction"] not in {"higher_is_better", "lower_is_better", "equivalent_only", "not_comparable"}:
        raise PolicyLearningError("shadow comparison dimension direction is invalid")
    _bounded_value(row["baseline_value"], f"shadow comparison dimension[{index}] baseline_value")
    _bounded_value(row["candidate_value"], f"shadow comparison dimension[{index}] candidate_value")
    _refs(row["evidence_refs"], f"shadow comparison dimension[{index}] evidence_refs")
    if row["critical"] and row["verifier_kind"] not in {"deterministic", "human_acceptance"}:
        raise PolicyLearningError("critical shadow dimensions require deterministic or human evidence")
    return row


def validate_shadow_route_binding(comparison: Any, *, as_of: str | None = None) -> dict[str, Any]:
    """Validate a side-effect-free comparison over one frozen input.

    Both routes point at the same immutable shared input/context/case binding,
    while each has a distinct, digest-tied route/runtime binding. The candidate
    is explicitly read-only and every comparison result is redacted, referenced
    evidence. This function validates the existing policy-learning/CAS seam;
    it never changes lifecycle state or promotes a route.
    """
    if not isinstance(comparison, dict):
        raise PolicyLearningError("shadow comparison must be an object")
    if as_of is not None:
        _timestamp(as_of, "shadow comparison as_of")
    row = _exact(comparison, SHADOW_FIELDS, "shadow comparison")
    _id(row["comparison_id"], "shadow comparison id")
    _id(row["case_ref"], "shadow comparison case_ref")
    binding = _validate_shadow_binding(row["context_binding"])
    shared = binding["shared"]
    if row["case_ref"] != shared["case_ref"]:
        raise PolicyLearningError("shadow comparison case_ref must match the frozen case binding")
    for field, side in (("baseline_binding_digest", "baseline"), ("candidate_binding_digest", "candidate")):
        _digest(row[field], f"shadow comparison {field}")
        if row[field] != binding[side]["route_binding_digest"]:
            raise PolicyLearningError(f"shadow comparison {side} route digest does not match its frozen binding")
    _id(row["baseline_route"], "shadow comparison baseline_route")
    _id(row["candidate_route"], "shadow comparison candidate_route")
    if row["baseline_route"] != binding["baseline"]["route_ref"] or row["candidate_route"] != binding["candidate"]["route_ref"]:
        raise PolicyLearningError("shadow comparison route labels must match their exact route bindings")
    if row["baseline_route"] == row["candidate_route"]:
        raise PolicyLearningError("shadow routes must differ")
    if not isinstance(row["route_dimensions"], list) or not row["route_dimensions"]:
        raise PolicyLearningError("shadow comparison needs route dimensions")
    route_dimensions = set()
    for index, dimension in enumerate(row["route_dimensions"]):
        dimension_id = _id(dimension, f"shadow comparison route_dimensions[{index}]")
        if dimension_id in route_dimensions:
            raise PolicyLearningError("shadow comparison route dimensions must be unique")
        route_dimensions.add(dimension_id)
    actual_route_changes = _shadow_runtime_changes(binding)
    if route_dimensions != actual_route_changes:
        raise PolicyLearningError("shadow route_dimensions must exactly match changed runtime profile fields")
    if len(actual_route_changes) != 1:
        raise PolicyLearningError("shadow comparisons must isolate one changed runtime dimension")
    if not isinstance(row["evaluation_dimensions"], list) or not row["evaluation_dimensions"]:
        raise PolicyLearningError("shadow comparison needs evaluation dimensions")
    evaluation_dimensions = set()
    for index, dimension in enumerate(row["evaluation_dimensions"]):
        dimension_id = _id(dimension, f"shadow comparison evaluation_dimensions[{index}]")
        if dimension_id in evaluation_dimensions or dimension_id in route_dimensions:
            raise PolicyLearningError("shadow comparison evaluation dimensions must be unique and separate from route dimensions")
        evaluation_dimensions.add(dimension_id)
    declared_dimensions = route_dimensions | evaluation_dimensions
    if not isinstance(row["dimension_results"], list) or not row["dimension_results"]:
        raise PolicyLearningError("shadow comparison needs independently evidenced dimensions")
    dimensions = [_validate_shadow_dimension(item, index, declared_dimensions) for index, item in enumerate(row["dimension_results"])]
    if {item["dimension_id"] for item in dimensions} != declared_dimensions:
        raise PolicyLearningError("shadow comparison must evidence every declared route/evaluation dimension exactly once")
    execution = _exact(row["candidate_execution"], SHADOW_EXECUTION_FIELDS, "shadow candidate execution")
    if execution["allowed_actions"] != []:
        raise PolicyLearningError("shadow candidate must have allowed_actions=[]")
    if execution["external_side_effects"] is not False:
        raise PolicyLearningError("shadow candidate external side effects are forbidden")
    if not isinstance(execution["side_effect_attempted"], bool):
        raise PolicyLearningError("shadow candidate side_effect_attempted must be boolean")
    refusal_refs = _refs(execution["capability_refusal_evidence_refs"], "shadow candidate capability_refusal_evidence_refs", allow_empty=True)
    if execution["side_effect_attempted"] and not refusal_refs:
        raise PolicyLearningError("side-effect attempt requires capability refusal security evidence")
    horizon = _exact(row["outcome_horizon"], SHADOW_HORIZON_FIELDS, "shadow comparison outcome horizon")
    if horizon["state"] not in {"mature", "immature", "unavailable", "stale"}:
        raise PolicyLearningError("shadow comparison outcome horizon is invalid")
    _timestamp(horizon["ends_at"], "shadow comparison outcome horizon ends_at")
    _refs(horizon["evidence_refs"], "shadow comparison outcome horizon evidence_refs", allow_empty=horizon["state"] != "mature")
    provenance = _exact(row["result_provenance"], SHADOW_PROVENANCE_FIELDS, "shadow comparison result provenance")
    _refs(provenance["baseline_evidence_refs"], "shadow baseline evidence_refs")
    _refs(provenance["candidate_evidence_refs"], "shadow candidate evidence_refs")
    _id(provenance["evaluator_ref"], "shadow comparison evaluator_ref")
    if provenance["evaluator_kind"] not in SHADOW_VERIFIERS or provenance["independence_state"] not in {"independent", "not_independent"}:
        raise PolicyLearningError("shadow comparison evaluator provenance is invalid")
    if provenance["redaction_class"] not in {"metadata_only", "redacted_evidence"} or provenance["raw_content_present"] is not False or provenance["private_content_present"] is not False:
        raise PolicyLearningError("shadow comparison provenance must be redacted evidence")
    if row["state"] not in {"matched", "mismatch", "unknown"} or row["promotion_state"] != "not_promoted":
        raise PolicyLearningError("shadow comparison state/promotion is invalid")
    if row["requested_state"] not in {"observed", "shadow", "eligible_for_human_review"}:
        raise PolicyLearningError("shadow comparison cannot request bounded exploration or active routing")
    _id(row["policy_version_cas"], "shadow comparison policy_version_cas")
    _id(row["kill_switch_ref"], "shadow comparison kill_switch_ref")
    _id(row["rollback_ref"], "shadow comparison rollback_ref")
    _timestamp(row["expires_at"], "shadow comparison expires_at")
    _refs(row["evidence_refs"], "shadow comparison evidence_refs")
    if row["state"] == "matched" and any(_shadow_regression(item) for item in dimensions):
        raise PolicyLearningError("shadow comparison declares matched despite a critical regression")
    return row


def _shadow_regression(dimension: dict[str, Any]) -> bool:
    if not dimension["critical"]:
        return False
    if dimension["candidate_status"] in {"failed", "blocked", "unknown"}:
        return True
    baseline, candidate = dimension["baseline_value"], dimension["candidate_value"]
    if not isinstance(baseline, (int, float)) or isinstance(baseline, bool) or not isinstance(candidate, (int, float)) or isinstance(candidate, bool):
        return False
    if dimension["direction"] == "higher_is_better":
        return candidate < baseline
    if dimension["direction"] == "lower_is_better":
        return candidate > baseline
    if dimension["direction"] == "equivalent_only":
        return candidate != baseline
    return False


def evaluate_shadow_comparison(comparison: Any, *, as_of: str | None = None) -> dict[str, Any]:
    """Return a fail-closed review recommendation without promoting anything."""
    row = validate_shadow_route_binding(comparison, as_of=as_of)
    reasons: list[str] = []
    if as_of is None:
        reasons.append("insufficient_evidence:as_of_required_for_expiry")
    elif as_of > row["expires_at"]:
        reasons.append("insufficient_evidence:comparison_expired")
    elif row["outcome_horizon"]["state"] == "mature" and as_of < row["outcome_horizon"]["ends_at"]:
        reasons.append("insufficient_evidence:outcome_horizon_immature")
    if row["outcome_horizon"]["state"] != "mature":
        reasons.append("insufficient_evidence:outcome_horizon_" + row["outcome_horizon"]["state"])
    if row["result_provenance"]["independence_state"] != "independent":
        reasons.append("insufficient_evidence:non_independent_results")
    if row["candidate_execution"]["side_effect_attempted"]:
        reasons.append("security_capability_refusal_observed")
    for dimension in row["dimension_results"]:
        if _shadow_regression(dimension):
            reasons.append("critical_dimension_regression:" + dimension["dimension_id"])
        elif dimension["candidate_status"] != "passed":
            reasons.append("dimension_not_passed:" + dimension["dimension_id"])
    if any(code.startswith("critical_dimension_regression") or code.startswith("security_") or code.startswith("dimension_not_passed") or code.startswith("insufficient_evidence") for code in reasons):
        return {"decision": "not_eligible", "reason_codes": sorted(set(reasons)), "promotion_state": "not_promoted"}
    return {"decision": "eligible_for_human_review", "reason_codes": ["human_authority_required_before_any_exploration"], "promotion_state": "not_promoted"}
