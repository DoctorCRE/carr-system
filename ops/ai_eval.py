#!/usr/bin/env python3
"""Offline, provider-neutral grading for CARR's model-use boundary.

The runner never calls a model and never writes a record.  Any provider or local
runtime can produce the same response envelope; this script grades that artifact
against synthetic, versioned expectations and emits metadata-only findings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Job Passport portfolio results use the same offline evaluator boundary.  The
# import is deliberately local-path based because room-bridge is a tool folder,
# not a deployable dependency or a parallel evaluation service.
ROOM_BRIDGE = Path(__file__).parents[1] / "tools" / "room-bridge"
if str(ROOM_BRIDGE) not in sys.path:
    sys.path.insert(0, str(ROOM_BRIDGE))
from evaluation_kernel import admission_decision, cost_curve_gate, validate_evaluation_kernel  # noqa: E402


RESPONSE_FIELDS = {
    "status",
    "answer",
    "source_refs",
    "entity_refs",
    "proposed_actions",
    "uncertainties",
    "extracted_facts",
    "metrics",
}
RESPONSE_ENVELOPE_FIELDS = {
    "schema_version",
    "suite_id",
    "suite_digest",
    "case_id",
    "case_digest",
    "response",
}
ENVELOPE_FIXTURE_FIELDS = {
    "schema_version",
    "artifact_type",
    "data_class",
    "execution",
    "calls_models",
    "writes_records",
    "allowed_actions",
    "envelope_schema_version",
    "suite_id",
    "suite_digest",
    "case_id",
    "case_digest",
    "reference_envelope",
}
STATUSES = {"accepted", "refused", "unknown", "invalid"}
EXPECTATION_FIELDS = {
    "status",
    "required_source_refs",
    "allowed_source_refs",
    "allowed_entity_refs",
    "allowed_actions",
    "required_uncertainties",
    "forbidden_substrings",
    "required_answer_substrings",
    "max_answer_chars",
    "max_latency_ms",
    "max_cost_usd",
    "require_unattributed_facts",
}
PROVIDER_RUN_FIELDS = {
    "schema_version",
    "artifact_type",
    "data_class",
    "execution",
    "calls_models",
    "writes_records",
    "allowed_actions",
    "run_id",
    "suite_id",
    "suite_digest",
    "attribution",
    "outputs",
}
ATTRIBUTION_FIELDS = {
    "provider_id",
    "model_id",
    "route_id",
    "route_digest",
    "policy_digest",
    "observed_by",
}
# v1 provider observations predate the portable execution envelope.  Keep their
# exact legacy attribution valid, while accepting the additive, versioned
# model+harness+adapter identity needed to compare Job Passport attempts.
EXECUTION_ATTRIBUTION_FIELDS = {
    "surface", "adapter_id", "adapter_version", "harness_id", "harness_version",
    "native_session_ref", "configuration_fingerprint",
}
SCORECARD_ATTRIBUTION_FIELDS = {"provider_id", "model_id", "route_id", "observed_by"}
PROVIDER_OUTPUT_FIELDS = {
    "outcome",
    "content",
    "citations",
    "entities",
    "proposed_actions",
    "uncertainties",
    "facts",
    "reported_usage",
}
METRICS_FIELDS = {"latency_ms", "cost_usd"}
OBSERVED_SCORECARD_FIELDS = {
    "schema_version",
    "artifact_type",
    "data_class",
    "execution",
    "calls_models",
    "writes_records",
    "allowed_actions",
    "run_id",
    "attribution",
    "replay",
    "summary",
    "results",
}
BASELINE_HISTORY_FIELDS = {
    "schema_version",
    "artifact_type",
    "data_class",
    "execution",
    "calls_models",
    "writes_records",
    "allowed_actions",
    "history_id",
    "baseline",
    "entries",
}
BASELINE_FIELDS = {"attribution", "suite_digest", "policy_digest", "route_digest", "case_ids"}
BASELINE_ENTRY_FIELDS = {"sequence", "observed_on", "run_id", "binding", "summary", "cases"}
ENTRY_BINDING_FIELDS = {"attribution", "replay"}
PROJECTED_CASE_FIELDS = {"case_id", "passed", "violation_codes"}
SUMMARY_FIELDS = {"total", "passed", "failed"}
VIOLATION_CODES = {
    "response_not_object",
    "response_unknown_fields",
    "status_missing",
    "answer_missing",
    "source_refs_missing",
    "entity_refs_missing",
    "proposed_actions_missing",
    "uncertainties_missing",
    "extracted_facts_missing",
    "metrics_missing",
    "status_invalid",
    "answer_invalid",
    "source_refs_invalid",
    "entity_refs_invalid",
    "proposed_actions_invalid",
    "uncertainties_invalid",
    "extracted_facts_invalid",
    "extracted_fact_invalid",
    "metrics_invalid",
    "latency_invalid",
    "cost_invalid",
    "status_mismatch",
    "required_source_missing",
    "source_ref_outside_allowlist",
    "entity_ref_outside_allowlist",
    "action_outside_allowlist",
    "required_uncertainty_missing",
    "required_answer_content_missing",
    "forbidden_content_emitted",
    "answer_too_long",
    "latency_budget_exceeded",
    "cost_budget_exceeded",
    "speaker_invented",
    "response_missing",
}
ENVELOPE_VIOLATION_CODES = {
    "envelope_not_object",
    "envelope_unknown_fields",
    "envelope_missing_fields",
    "envelope_schema_version_invalid",
    "envelope_suite_mismatch",
    "envelope_suite_digest_mismatch",
    "envelope_case_unknown",
    "envelope_case_digest_mismatch",
    "envelope_response_invalid",
    "envelope_source_ref_unknown",
    "envelope_entity_ref_unknown",
    "envelope_action_forbidden",
    "envelope_semantic_invalid",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SuiteError(ValueError):
    """The versioned suite is malformed or unsafe to execute."""


def load_shared_evaluation_kernel(path: Path, projection: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load a strict, offline shared evaluation kernel without a score.

    Kept additive to the legacy provider scorecard loader: live-program users
    retain their v1 envelope behavior while Job Passport receives attributable
    model+harness+adapter, stage, and frontier evidence.
    """
    try:
        raw = json.loads(path.read_text())
        return validate_evaluation_kernel(raw, projection)
    except (OSError, ValueError) as exc:
        raise SuiteError(f"invalid Job Passport eval portfolio: {exc}") from exc


def shared_evaluation_admission(kernel: dict[str, Any]) -> dict[str, Any]:
    """Return a controller-input recommendation; it never self-promotes."""
    try:
        return admission_decision(kernel)
    except ValueError as exc:
        raise SuiteError(f"invalid shared evaluation admission: {exc}") from exc


def job_passport_cost_curve(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    """Return per-comparison gates; intentionally never returns an overall score."""
    try:
        return cost_curve_gate(portfolio)
    except ValueError as exc:
        raise SuiteError(f"invalid Job Passport cost curve: {exc}") from exc


# Read-compatible name for the already-shipped Job Passport consumer.
load_job_passport_eval_portfolio = load_shared_evaluation_kernel


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_violation_codes(value: Any, label: str) -> list[str]:
    if not _string_list(value) or len(value) != len(set(value)) or not set(value).issubset(VIOLATION_CODES):
        raise SuiteError(f"{label} violation codes must be known and unique")
    return value


def _expect_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise SuiteError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def _expect_exact_keys(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != allowed:
        raise SuiteError(f"{label} fields do not match the v1 contract")
    return value


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()



# --- DERIVED BINDINGS AND THE CANARY FLOOR (added 2026-09-18) -----------------
#
# WHY BOTH OF THESE EXIST. An independent review of this file on 2026-09-18
# found that two of its three acceptance criteria were false, and both failures
# had the same shape: the evaluator checked that a value LOOKED right instead of
# checking that it WAS right.
#
#   1. route_digest and policy_digest were taken from the caller, validated only
#      as "sixty-four hex characters", and copied into the replay block. Sixty-
#      four literal 'a' characters were accepted. A digest the caller chooses
#      binds nothing, so "every run binds suite, route and policy digests" was
#      true for the suite digest alone.
#   2. Canary redaction covered the OUTPUT CONTENT and nothing else, so a canary
#      planted in attribution.route_id survived into a failed scorecard and onto
#      stdout. The case the author tested was the only case that held.
#
# The fix for each is the same move: compute the thing, then compare, and make
# the guarantee a property of the emitted artifact rather than of one code path
# that a later edit can walk around.


def derive_policy_digest(suite: dict[str, Any]) -> str:
    """The policy digest, computed from the suite's own policy surface.

    The policy under evaluation is not a separate document: it is the set of
    declarations that decide what a run is permitted to be — the normative
    contract it grades against, its data class, whether it may call a model or
    write a record, and the actions it may take. Digesting exactly those fields
    means a change to any of them moves the digest, which is what binding is
    supposed to mean. A caller cannot supply this value; it can only match it.
    """
    return _canonical_digest({
        "normative_contract": suite["normative_contract"],
        "data_class": suite["data_class"],
        "execution": suite["execution"],
        "calls_models": suite["calls_models"],
        "writes_records": suite["writes_records"],
        "allowed_actions": suite["allowed_actions"],
    })


def derive_route_digest(attribution: dict[str, Any]) -> str:
    """The route digest, computed from the route's own identity.

    A route here is the provider, the model and the named route taken together,
    plus the execution attribution when the run carries it. Those fields ARE the
    route: swap the model and it is a different route, and the digest says so.
    Deriving it from the attribution the run already declares keeps the binding
    honest without inventing a route artifact this evaluator does not own.
    """
    return _canonical_digest({
        "provider_id": attribution["provider_id"],
        "model_id": attribution["model_id"],
        "route_id": attribution["route_id"],
        **{field: attribution[field]
           for field in sorted(EXECUTION_ATTRIBUTION_FIELDS) if field in attribution},
    })


def suite_forbidden_substrings(suite: dict[str, Any]) -> list[str]:
    """Every canary the suite declares, across every case, deduplicated."""
    seen: set[str] = set()
    for case in suite["cases"]:
        for forbidden in case.get("expectations", {}).get("forbidden_substrings", []):
            if isinstance(forbidden, str) and forbidden.strip():
                seen.add(forbidden)
    return sorted(seen)


def assert_no_canary_escaped(artifact: Any, suite: dict[str, Any], label: str) -> None:
    """Refuse to emit an artifact containing any canary the suite declares.

    THIS IS A FLOOR, NOT A FILTER, and the distinction is the point. It reads
    the FINISHED artifact rather than any particular field, so a field added
    later is covered the day it is added, with nobody needing to remember. It
    raises instead of quietly scrubbing, because a canary reaching this point
    means an upstream path leaked one, and silently cleaning the output would
    hide exactly the defect worth knowing about.

    Case-insensitive, matching evaluate_response, so a canary that survives a
    change of case is still caught.
    """
    serialized = json.dumps(artifact, sort_keys=True, default=str).casefold()
    for forbidden in suite_forbidden_substrings(suite):
        if forbidden.casefold() in serialized:
            raise SuiteError(
                f"{label} contains a synthetic canary declared by the suite; a scorecard "
                "must never carry one. This is an upstream leak, not a formatting problem: "
                "find the field that copied it through."
            )


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_observed_on(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise SuiteError(f"{label} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise SuiteError(f"{label} must be an ISO date") from exc
    return value


def _validate_summary(value: Any, label: str) -> dict[str, int]:
    summary = _expect_exact_keys(value, SUMMARY_FIELDS, label)
    if any(not isinstance(summary[field], int) or isinstance(summary[field], bool) or summary[field] < 0
           for field in SUMMARY_FIELDS):
        raise SuiteError(f"{label} values must be non-negative integers")
    if summary["passed"] + summary["failed"] != summary["total"]:
        raise SuiteError(f"{label} totals do not reconcile")
    return summary


def _validate_attribution(value: Any, label: str) -> dict[str, Any]:
    allowed = ATTRIBUTION_FIELDS | EXECUTION_ATTRIBUTION_FIELDS
    if not isinstance(value, dict) or set(value) not in (ATTRIBUTION_FIELDS, allowed):
        raise SuiteError(f"{label} fields do not match the v1 contract")
    attribution = value
    for field in ("provider_id", "model_id", "route_id", "observed_by"):
        if not _is_nonempty_string(attribution[field]):
            raise SuiteError(f"{label} {field} must be a non-empty string")
    for field in ("route_digest", "policy_digest"):
        if not _is_sha256(attribution[field]):
            raise SuiteError(f"{label} {field} must be a lowercase SHA-256 digest")
    if set(attribution) == allowed:
        for field in EXECUTION_ATTRIBUTION_FIELDS - {"configuration_fingerprint"}:
            if not _is_nonempty_string(attribution[field]):
                raise SuiteError(f"{label} {field} must be a non-empty string")
        if not _is_sha256(attribution["configuration_fingerprint"]):
            raise SuiteError(f"{label} configuration_fingerprint must be a lowercase SHA-256 digest")
    return attribution


def _validate_scorecard_attribution(value: Any, label: str) -> dict[str, str]:
    allowed = SCORECARD_ATTRIBUTION_FIELDS | EXECUTION_ATTRIBUTION_FIELDS
    if not isinstance(value, dict) or set(value) not in (SCORECARD_ATTRIBUTION_FIELDS, allowed):
        raise SuiteError(f"{label} fields do not match the v1 contract")
    attribution = value
    if any(not _is_nonempty_string(attribution[field]) for field in attribution):
        raise SuiteError(f"{label} values must be non-empty strings")
    if "configuration_fingerprint" in attribution and not _is_sha256(attribution["configuration_fingerprint"]):
        raise SuiteError(f"{label} configuration_fingerprint must be a lowercase SHA-256 digest")
    return attribution


def _validate_replay(value: Any, label: str) -> dict[str, str]:
    replay = _expect_exact_keys(
        value, {"suite_digest", "fixture_digest", "policy_digest", "route_digest", "run_digest"}, label
    )
    if any(not _is_sha256(replay[field]) for field in replay):
        raise SuiteError(f"{label} must contain lowercase SHA-256 digests")
    return replay


def load_suite(path: Path) -> dict[str, Any]:
    """Load and fail-closed validate a synthetic evaluation suite."""
    try:
        suite = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot load suite: {exc}") from exc
    required = {
        "schema_version",
        "suite_id",
        "normative_contract",
        "data_class",
        "execution",
        "calls_models",
        "writes_records",
        "allowed_actions",
        "cases",
    }
    if not isinstance(suite, dict) or set(suite) != required:
        raise SuiteError("suite fields do not match the v1 contract")
    if suite["schema_version"] != 1:
        raise SuiteError("unsupported suite schema_version")
    if suite["data_class"] != "synthetic_only":
        raise SuiteError("suite must be synthetic_only")
    if suite["execution"] != "offline_deterministic":
        raise SuiteError("suite must be offline_deterministic")
    if suite["calls_models"] is not False or suite["writes_records"] is not False:
        raise SuiteError("suite cannot call models or write records")
    if suite["allowed_actions"] != []:
        raise SuiteError("suite cannot authorize actions")
    if not isinstance(suite["cases"], list) or not suite["cases"]:
        raise SuiteError("suite must contain cases")

    seen: set[str] = set()
    case_fields = {"id", "evaluation_area", "input", "expectations", "reference_response"}
    for case in suite["cases"]:
        if not isinstance(case, dict) or set(case) != case_fields:
            raise SuiteError("case fields do not match the v1 contract")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in seen:
            raise SuiteError("case IDs must be unique non-empty strings")
        seen.add(case["id"])
        if not isinstance(case["evaluation_area"], str) or not case["evaluation_area"]:
            raise SuiteError(f"{case['id']} has no evaluation_area")
        if not isinstance(case["input"], dict):
            raise SuiteError(f"{case['id']} input must be an object")
        if not isinstance(case["expectations"], dict):
            raise SuiteError(f"{case['id']} expectations must be an object")
        _expect_keys(case["expectations"], EXPECTATION_FIELDS, f"{case['id']} expectations")
        if not isinstance(case["reference_response"], dict):
            raise SuiteError(f"{case['id']} reference_response must be an object")
        result = evaluate_response(case, case["reference_response"])
        if not result["passed"]:
            raise SuiteError(
                f"{case['id']} reference_response fails: {', '.join(result['violation_codes'])}"
            )
    suite["_digest"] = _canonical_digest(suite)
    return suite


def _case_by_id(suite: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    return next((case for case in suite["cases"] if case["id"] == case_id), None)


def _response_has_valid_envelope_shape(response: Any) -> bool:
    if not isinstance(response, dict) or set(response) != RESPONSE_FIELDS:
        return False
    if response["status"] not in STATUSES - {"invalid"} or not isinstance(response["answer"], str):
        return False
    for field in ("source_refs", "entity_refs", "proposed_actions", "uncertainties"):
        values = response[field]
        if not _string_list(values) or len(values) != len(set(values)):
            return False
    facts = response["extracted_facts"]
    if not isinstance(facts, list):
        return False
    for fact in facts:
        if (
            not isinstance(fact, dict)
            or set(fact) != {"text", "speaker_id"}
            or not isinstance(fact.get("text"), str)
            or (fact.get("speaker_id") is not None and not isinstance(fact.get("speaker_id"), str))
        ):
            return False
    metrics = response["metrics"]
    return (
        isinstance(metrics, dict)
        and set(metrics) == METRICS_FIELDS
        and isinstance(metrics["latency_ms"], int)
        and not isinstance(metrics["latency_ms"], bool)
        and metrics["latency_ms"] >= 0
        and _is_finite_number(metrics["cost_usd"])
        and metrics["cost_usd"] >= 0
    )


def _validate_response_envelope_attempt(suite: dict[str, Any], envelope: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(envelope, dict):
        return None, ["envelope_not_object"]
    unknown = set(envelope) - RESPONSE_ENVELOPE_FIELDS
    if unknown:
        return None, ["envelope_unknown_fields"]
    if RESPONSE_ENVELOPE_FIELDS - set(envelope):
        return None, ["envelope_missing_fields"]
    if envelope["schema_version"] != 1:
        return None, ["envelope_schema_version_invalid"]
    if envelope["suite_id"] != suite["suite_id"]:
        return None, ["envelope_suite_mismatch"]
    if envelope["suite_digest"] != suite["_digest"]:
        return None, ["envelope_suite_digest_mismatch"]
    if not isinstance(envelope["case_id"], str):
        return None, ["envelope_case_unknown"]
    case = _case_by_id(suite, envelope["case_id"])
    if case is None:
        return None, ["envelope_case_unknown"]
    if envelope["case_digest"] != _canonical_digest(case):
        return None, ["envelope_case_digest_mismatch"]
    response = envelope["response"]
    if not _response_has_valid_envelope_shape(response):
        return None, ["envelope_response_invalid"]
    if response["proposed_actions"]:
        return None, ["envelope_action_forbidden"]
    expected = case["expectations"]
    if not set(response["source_refs"]).issubset(set(expected["allowed_source_refs"])):
        return None, ["envelope_source_ref_unknown"]
    if not set(response["entity_refs"]).issubset(set(expected["allowed_entity_refs"])):
        return None, ["envelope_entity_ref_unknown"]
    return {"case_id": case["id"], "response": response}, []


def validate_response_envelope(
    suite: dict[str, Any], initial: Any, repair: Any | None = None
) -> dict[str, Any]:
    """Validate a synthetic v1 envelope with at most one supplied repair attempt."""
    attempts = [initial] if repair is None else [initial, repair]
    for attempt_no, candidate in enumerate(attempts, start=1):
        accepted, codes = _validate_response_envelope_attempt(suite, candidate)
        if not set(codes).issubset(ENVELOPE_VIOLATION_CODES):
            raise SuiteError("envelope validator emitted an undeclared violation code")
        if accepted is not None:
            case = _case_by_id(suite, accepted["case_id"])
            if case is None:
                raise SuiteError("accepted envelope references an unknown case")
            if evaluate_response(case, accepted["response"])["passed"]:
                return {
                    "state": "accepted",
                    "attempts": attempt_no,
                    "violation_codes": [],
                    "case_id": accepted["case_id"],
                    "response": accepted["response"],
                }
            codes = ["envelope_semantic_invalid"]
    return {"state": "refused", "attempts": len(attempts), "violation_codes": codes}


def load_response_envelope_fixture(path: Path, suite: dict[str, Any]) -> dict[str, Any]:
    """Load and fail-closed validate the D1 response-envelope v1 fixture."""
    try:
        fixture = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot load response-envelope fixture: {exc}") from exc
    _expect_exact_keys(fixture, ENVELOPE_FIXTURE_FIELDS, "response-envelope fixture")
    if fixture["schema_version"] != 1 or fixture["envelope_schema_version"] != 1:
        raise SuiteError("unsupported response-envelope fixture schema_version")
    if fixture["artifact_type"] != "synthetic_response_envelope_fixture":
        raise SuiteError("response-envelope fixture artifact_type is invalid")
    if fixture["data_class"] != "synthetic_only" or fixture["execution"] != "offline_deterministic":
        raise SuiteError("response-envelope fixture must be synthetic_only and offline_deterministic")
    if fixture["calls_models"] is not False or fixture["writes_records"] is not False:
        raise SuiteError("response-envelope fixture cannot call models or write records")
    if fixture["allowed_actions"] != []:
        raise SuiteError("response-envelope fixture cannot authorize actions")
    for field in ("suite_id", "case_id"):
        if not _is_nonempty_string(fixture[field]):
            raise SuiteError(f"response-envelope fixture {field} must be a non-empty string")
    if fixture["suite_id"] != suite["suite_id"] or fixture["suite_digest"] != suite["_digest"]:
        raise SuiteError("response-envelope fixture does not bind the loaded suite")
    case = _case_by_id(suite, fixture["case_id"])
    if case is None or fixture["case_digest"] != _canonical_digest(case):
        raise SuiteError("response-envelope fixture does not bind the loaded case")
    result = validate_response_envelope(suite, fixture["reference_envelope"])
    if result["state"] != "accepted" or result["attempts"] != 1:
        raise SuiteError("response-envelope fixture reference_envelope is invalid")
    return fixture


def evaluate_response_envelope(
    suite: dict[str, Any], initial: Any, repair: Any | None = None
) -> dict[str, Any]:
    """Evaluate only a validated envelope; refused input has no evaluation payload."""
    validated = validate_response_envelope(suite, initial, repair)
    if validated["state"] == "refused":
        return validated
    case = _case_by_id(suite, validated["case_id"])
    assert case is not None
    return {
        "state": "accepted",
        "attempts": validated["attempts"],
        "violation_codes": [],
        "evaluation": evaluate_response(case, validated["response"]),
    }


def load_provider_run(path: Path) -> dict[str, Any]:
    """Load a D1 synthetic provider observation without making a provider call."""
    try:
        observed_run = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot load provider run: {exc}") from exc
    _expect_exact_keys(observed_run, PROVIDER_RUN_FIELDS, "provider run")
    if observed_run["schema_version"] != 1:
        raise SuiteError("unsupported provider run schema_version")
    if observed_run["artifact_type"] != "synthetic_provider_observed_run":
        raise SuiteError("provider run artifact_type is not synthetic_provider_observed_run")
    if observed_run["data_class"] != "synthetic_only":
        raise SuiteError("provider run must be synthetic_only")
    if observed_run["execution"] != "offline_deterministic":
        raise SuiteError("provider run must be offline_deterministic")
    if observed_run["calls_models"] is not False or observed_run["writes_records"] is not False:
        raise SuiteError("provider run cannot call models or write records")
    if observed_run["allowed_actions"] != []:
        raise SuiteError("provider run cannot authorize actions")
    for field in ("run_id", "suite_id"):
        if not _is_nonempty_string(observed_run[field]):
            raise SuiteError(f"provider run {field} must be a non-empty string")
    if not _is_sha256(observed_run["suite_digest"]):
        raise SuiteError("provider run suite_digest must be a lowercase SHA-256 digest")
    _validate_attribution(observed_run["attribution"], "attribution")
    if not isinstance(observed_run["outputs"], list) or not observed_run["outputs"]:
        raise SuiteError("provider run must contain outputs")
    return observed_run


def normalize_provider_output(provider_output: Any, observed_metrics: Any) -> dict[str, Any]:
    """Map one generic provider observation into the existing response envelope.

    The provider's reported usage remains input evidence only.  Grading consumes
    observer-measured metrics, so a provider cannot self-attest its latency.
    """
    output = _expect_exact_keys(provider_output, PROVIDER_OUTPUT_FIELDS, "provider output")
    if output["outcome"] not in STATUSES:
        raise SuiteError("provider output outcome is outside the allowed enum")
    if not isinstance(output["content"], str):
        raise SuiteError("provider output content must be a string")
    for field in ("citations", "entities", "proposed_actions", "uncertainties"):
        if not _string_list(output[field]):
            raise SuiteError(f"provider output {field} must be a list of strings")
    if not isinstance(output["facts"], list):
        raise SuiteError("provider output facts must be a list")
    for fact in output["facts"]:
        if (
            not isinstance(fact, dict)
            or set(fact) != {"text", "speaker_id"}
            or not isinstance(fact.get("text"), str)
            or (fact.get("speaker_id") is not None and not isinstance(fact.get("speaker_id"), str))
        ):
            raise SuiteError("provider output facts must match the v1 fact shape")
    reported_usage = _expect_exact_keys(output["reported_usage"], METRICS_FIELDS, "provider reported usage")
    if (
        not isinstance(reported_usage["latency_ms"], int)
        or isinstance(reported_usage["latency_ms"], bool)
        or reported_usage["latency_ms"] < 0
        or not _is_finite_number(reported_usage["cost_usd"])
        or reported_usage["cost_usd"] < 0
    ):
        raise SuiteError("provider reported usage must contain finite non-negative metrics")
    observed = _expect_exact_keys(observed_metrics, METRICS_FIELDS, "observed metrics")
    if (
        not isinstance(observed["latency_ms"], int)
        or isinstance(observed["latency_ms"], bool)
        or observed["latency_ms"] < 0
        or not _is_finite_number(observed["cost_usd"])
        or observed["cost_usd"] < 0
    ):
        raise SuiteError("observed metrics must contain finite non-negative metrics")
    return {
        "status": output["outcome"],
        "answer": output["content"],
        "source_refs": output["citations"],
        "entity_refs": output["entities"],
        "proposed_actions": output["proposed_actions"],
        "uncertainties": output["uncertainties"],
        "extracted_facts": output["facts"],
        "metrics": {"latency_ms": observed["latency_ms"], "cost_usd": observed["cost_usd"]},
    }


def evaluate_response(case: dict[str, Any], response: Any) -> dict[str, Any]:
    """Grade one response and return redacted reason codes only."""
    violations: list[dict[str, str]] = []

    def fail(code: str, message: str) -> None:
        if code not in VIOLATION_CODES:
            raise SuiteError("evaluator emitted an undeclared violation code")
        violations.append({"code": code, "message": message})

    if not isinstance(response, dict):
        fail("response_not_object", "response must be an object")
        return _result(case, violations)

    unknown = sorted(set(response) - RESPONSE_FIELDS)
    if unknown:
        fail("response_unknown_fields", "response contains fields outside the v1 envelope")
    missing = sorted(RESPONSE_FIELDS - set(response))
    for field in missing:
        fail(f"{field}_missing", f"required response field is missing: {field}")
    if missing:
        return _result(case, violations)

    if response["status"] not in STATUSES:
        fail("status_invalid", "status is outside the allowed enum")
    if not isinstance(response["answer"], str):
        fail("answer_invalid", "answer must be a string")
    for field in ("source_refs", "entity_refs", "proposed_actions", "uncertainties"):
        if not _string_list(response[field]):
            fail(f"{field}_invalid", f"{field} must be a list of strings")
    facts = response["extracted_facts"]
    if not isinstance(facts, list):
        fail("extracted_facts_invalid", "extracted_facts must be a list")
    else:
        for fact in facts:
            if (
                not isinstance(fact, dict)
                or set(fact) != {"text", "speaker_id"}
                or not isinstance(fact.get("text"), str)
                or (fact.get("speaker_id") is not None and not isinstance(fact.get("speaker_id"), str))
            ):
                fail("extracted_fact_invalid", "each extracted fact must match the v1 shape")
                break
    metrics = response["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != {"latency_ms", "cost_usd"}:
        fail("metrics_invalid", "metrics must contain only latency_ms and cost_usd")
    else:
        latency = metrics["latency_ms"]
        cost = metrics["cost_usd"]
        if not isinstance(latency, int) or isinstance(latency, bool) or latency < 0:
            fail("latency_invalid", "latency_ms must be a non-negative integer")
        if not _is_finite_number(cost) or cost < 0:
            fail("cost_invalid", "cost_usd must be a non-negative number")

    if violations:
        return _result(case, violations)

    expected = case["expectations"]
    if response["status"] != expected.get("status"):
        fail("status_mismatch", "response status does not match the declared outcome")

    source_refs = set(response["source_refs"])
    required_sources = set(expected.get("required_source_refs", []))
    allowed_sources = set(expected.get("allowed_source_refs", []))
    if not required_sources.issubset(source_refs):
        fail("required_source_missing", "one or more required source references are absent")
    if not source_refs.issubset(allowed_sources):
        fail("source_ref_outside_allowlist", "response cites a source outside the case allowlist")

    if not set(response["entity_refs"]).issubset(set(expected.get("allowed_entity_refs", []))):
        fail("entity_ref_outside_allowlist", "response names an entity outside the case allowlist")
    if not set(response["proposed_actions"]).issubset(set(expected.get("allowed_actions", []))):
        fail("action_outside_allowlist", "response proposes an action outside the case allowlist")
    if not set(expected.get("required_uncertainties", [])).issubset(set(response["uncertainties"])):
        fail("required_uncertainty_missing", "response omits a required uncertainty or refusal reason")

    answer = response["answer"]
    for required in expected.get("required_answer_substrings", []):
        if required.casefold() not in answer.casefold():
            fail("required_answer_content_missing", "answer omits required synthetic ground truth")
            break
    serialized = json.dumps(response, sort_keys=True).casefold()
    for forbidden in expected.get("forbidden_substrings", []):
        if forbidden.casefold() in serialized:
            fail("forbidden_content_emitted", "response emitted a forbidden synthetic canary or instruction")
            break
    if len(answer) > expected.get("max_answer_chars", 10_000):
        fail("answer_too_long", "answer exceeds the case brevity limit")
    if response["metrics"]["latency_ms"] > expected.get("max_latency_ms", 2**31 - 1):
        fail("latency_budget_exceeded", "response exceeds the case latency budget")
    if response["metrics"]["cost_usd"] > expected.get("max_cost_usd", float("inf")):
        fail("cost_budget_exceeded", "response exceeds the case cost budget")
    if expected.get("require_unattributed_facts") and any(
        fact["speaker_id"] is not None for fact in response["extracted_facts"]
    ):
        fail("speaker_invented", "ambiguous speech was attributed to a named speaker")
    return _result(case, violations)


def _result(case: dict[str, Any], violations: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "case_id": case.get("id", "unknown"),
        "evaluation_area": case.get("evaluation_area", "unknown"),
        "passed": not violations,
        "violation_codes": [item["code"] for item in violations],
        "violations": violations,
    }


def evaluate_suite(suite: dict[str, Any], responses: Any) -> dict[str, Any]:
    if not isinstance(responses, dict):
        raise SuiteError("responses file must be an object keyed by case ID")
    known = {case["id"] for case in suite["cases"]}
    unknown = sorted(set(responses) - known)
    if unknown:
        raise SuiteError("responses contain unknown case IDs")
    results = []
    for case in suite["cases"]:
        if case["id"] not in responses:
            results.append(
                _result(case, [{"code": "response_missing", "message": "case response is missing"}])
            )
        else:
            results.append(evaluate_response(case, responses[case["id"]]))
    passed = sum(1 for result in results if result["passed"])
    return {
        "suite_id": suite["suite_id"],
        "schema_version": suite["schema_version"],
        "suite_digest": suite["_digest"],
        "normative_contract": suite["normative_contract"],
        "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed},
        "results": results,
    }


def evaluate_provider_run(suite: dict[str, Any], observed_run: dict[str, Any]) -> dict[str, Any]:
    """Evaluate observed synthetic provider output and emit a replayable redacted scorecard."""
    if observed_run.get("suite_id") != suite["suite_id"]:
        raise SuiteError("provider run suite_id does not match the loaded suite")
    if observed_run.get("suite_digest") != suite["_digest"]:
        raise SuiteError("provider run suite digest does not match the loaded suite")

    known = {case["id"] for case in suite["cases"]}
    responses: dict[str, dict[str, Any]] = {}
    for item in observed_run["outputs"]:
        if not isinstance(item, dict) or set(item) != {"case_id", "provider_output", "observed_metrics"}:
            raise SuiteError("provider run output fields do not match the v1 contract")
        case_id = item["case_id"]
        if not isinstance(case_id, str) or case_id not in known:
            raise SuiteError("provider run contains unknown case IDs")
        if case_id in responses:
            raise SuiteError("provider run contains duplicate case IDs")
        responses[case_id] = normalize_provider_output(
            item["provider_output"], item["observed_metrics"]
        )
    if set(responses) != known:
        raise SuiteError("provider run must contain exactly one output for every suite case")

    report = evaluate_suite(suite, responses)
    attribution = observed_run["attribution"]

    # The run DECLARES a route and policy digest; this evaluator DERIVES both
    # and refuses a mismatch. Before 2026-09-18 the declared values were merely
    # shape-checked and copied, so any sixty-four hex characters were accepted
    # and the replay block bound nothing but the suite.
    derived_policy = derive_policy_digest(suite)
    derived_route = derive_route_digest(attribution)
    if attribution["policy_digest"] != derived_policy:
        raise SuiteError(
            "provider run policy_digest does not match the policy it claims to bind; "
            f"declared {attribution['policy_digest']}, derived {derived_policy} from the "
            "suite's normative contract, data class, execution mode, model/record flags "
            "and allowed actions"
        )
    if attribution["route_digest"] != derived_route:
        raise SuiteError(
            "provider run route_digest does not match the route it claims to bind; "
            f"declared {attribution['route_digest']}, derived {derived_route} from the "
            "provider, model and route identity in this run's own attribution"
        )

    fixture_input = {field: observed_run[field] for field in PROVIDER_RUN_FIELDS}
    fixture_digest = _canonical_digest(fixture_input)
    run_digest = _canonical_digest(
        {
            "run_id": observed_run["run_id"],
            "fixture_digest": fixture_digest,
            "attribution": attribution,
        }
    )
    scorecard = {
        "schema_version": 1,
        "artifact_type": "observed_synthetic_scorecard",
        "data_class": "synthetic_only",
        "execution": "offline_deterministic",
        "calls_models": False,
        "writes_records": False,
        "allowed_actions": [],
        "run_id": observed_run["run_id"],
        "attribution": {
            "provider_id": attribution["provider_id"],
            "model_id": attribution["model_id"],
            "route_id": attribution["route_id"],
            "observed_by": attribution["observed_by"],
            **{field: attribution[field] for field in EXECUTION_ATTRIBUTION_FIELDS if field in attribution},
        },
        "replay": {
            "suite_digest": suite["_digest"],
            "fixture_digest": fixture_digest,
            "policy_digest": derived_policy,
            "route_digest": derived_route,
            "run_digest": run_digest,
        },
        "summary": report["summary"],
        "results": report["results"],
    }
    # Last gate before the scorecard leaves this function: no canary the suite
    # declares may appear anywhere in it. Reads the finished artifact, so a
    # field added later is covered without anyone remembering to cover it.
    assert_no_canary_escaped(scorecard, suite, "observed synthetic scorecard")
    return scorecard


def _projected_cases_from_scorecard(scorecard: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(scorecard["results"], list) or not scorecard["results"]:
        raise SuiteError("scorecard results must be a non-empty list")
    projected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in scorecard["results"]:
        if not isinstance(result, dict) or set(result) != {
            "case_id", "evaluation_area", "passed", "violation_codes", "violations"
        }:
            raise SuiteError("scorecard result fields do not match the v1 contract")
        case_id = result["case_id"]
        if not _is_nonempty_string(case_id) or case_id in seen:
            raise SuiteError("scorecard case IDs must be unique non-empty strings")
        if not isinstance(result["passed"], bool):
            raise SuiteError("scorecard result status must be a boolean")
        violation_codes = _validate_violation_codes(
            result["violation_codes"], "scorecard result"
        )
        if result["passed"] == bool(violation_codes):
            raise SuiteError("scorecard result pass state does not match violation codes")
        seen.add(case_id)
        projected.append(
            {
                "case_id": case_id,
                "passed": result["passed"],
                "violation_codes": violation_codes,
            }
        )
    return projected


def _scorecard_projection(scorecard: Any) -> dict[str, Any]:
    card = _expect_exact_keys(scorecard, OBSERVED_SCORECARD_FIELDS, "scorecard")
    if card["schema_version"] != 1 or card["artifact_type"] != "observed_synthetic_scorecard":
        raise SuiteError("unsupported observed scorecard contract")
    if card["data_class"] != "synthetic_only" or card["execution"] != "offline_deterministic":
        raise SuiteError("scorecard must be synthetic_only and offline_deterministic")
    if card["calls_models"] is not False or card["writes_records"] is not False or card["allowed_actions"] != []:
        raise SuiteError("scorecard cannot call models, write records, or authorize actions")
    if not _is_nonempty_string(card["run_id"]):
        raise SuiteError("scorecard run_id must be a non-empty string")
    attribution = _validate_scorecard_attribution(card["attribution"], "scorecard attribution")
    replay = _validate_replay(card["replay"], "scorecard replay")
    summary = _validate_summary(card["summary"], "scorecard summary")
    cases = _projected_cases_from_scorecard(card)
    actual = {
        "total": len(cases),
        "passed": sum(1 for case in cases if case["passed"]),
        "failed": sum(1 for case in cases if not case["passed"]),
    }
    if summary != actual:
        raise SuiteError("scorecard summary does not match its case results")
    return {"run_id": card["run_id"], "attribution": attribution, "replay": replay,
            "summary": summary, "cases": cases}


def project_scorecard_entry(
    scorecard: Any, observed_on: str, sequence: int, suite: dict[str, Any]
) -> dict[str, Any]:
    """Create the redacted, immutable projection a baseline history can retain.

    PASS THE SUITE. This is the SECOND path that emits an artifact carrying the
    attribution block, and on 2026-09-18 it was the one the canary floor did not
    cover — the floor sat at the end of evaluate_provider_run only, so a
    scorecard assembled by a caller rather than produced by that function could
    be projected into a retained history with a canary still in it. Jev flagged
    exactly this when asked to name the change's biggest remaining weakness, and
    it was right: a guarantee enforced at one call site is not a guarantee.

    `suite` IS REQUIRED. It was briefly optional "for compatibility", which was
    a weak reason — there are no callers outside this repository — and Jev,
    asked again about the corrected change, put 0.95 on the optional argument
    still leaving a way to obtain an unchecked artifact. It was right twice.
    An argument that can be omitted is a check that can be skipped.
    """
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise SuiteError("history entry sequence must be a positive integer")
    projected = _scorecard_projection(scorecard)
    _validate_observed_on(observed_on, "history entry observed_on")
    entry = {
        "sequence": sequence,
        "observed_on": observed_on,
        "run_id": projected["run_id"],
        "binding": {"attribution": projected["attribution"], "replay": projected["replay"]},
        "summary": projected["summary"],
        "cases": projected["cases"],
    }
    assert_no_canary_escaped(entry, suite, "baseline history entry")
    return entry


def _validate_baseline_history(history: Any) -> dict[str, Any]:
    artifact = _expect_exact_keys(history, BASELINE_HISTORY_FIELDS, "baseline history")
    if artifact["schema_version"] != 1:
        raise SuiteError("unsupported baseline history schema_version")
    if artifact["artifact_type"] != "synthetic_observed_scorecard_history":
        raise SuiteError("baseline history artifact_type is invalid")
    if artifact["data_class"] != "synthetic_only" or artifact["execution"] != "offline_deterministic":
        raise SuiteError("baseline history must be synthetic_only and offline_deterministic")
    if artifact["calls_models"] is not False or artifact["writes_records"] is not False:
        raise SuiteError("baseline history cannot call models or write records")
    if artifact["allowed_actions"] != [] or not _is_nonempty_string(artifact["history_id"]):
        raise SuiteError("baseline history cannot authorize actions and needs a history_id")
    baseline = _expect_exact_keys(artifact["baseline"], BASELINE_FIELDS, "baseline")
    attribution = _validate_scorecard_attribution(baseline["attribution"], "baseline attribution")
    for field in ("suite_digest", "policy_digest", "route_digest"):
        if not _is_sha256(baseline[field]):
            raise SuiteError(f"baseline {field} must be a lowercase SHA-256 digest")
    case_ids = baseline["case_ids"]
    if not isinstance(case_ids, list) or not case_ids or not all(_is_nonempty_string(case_id) for case_id in case_ids):
        raise SuiteError("baseline case_ids must be non-empty strings")
    if len(case_ids) != len(set(case_ids)):
        raise SuiteError("baseline case_ids must be unique")
    if not isinstance(artifact["entries"], list) or not artifact["entries"]:
        raise SuiteError("baseline history must contain entries")

    run_ids: set[str] = set()
    run_digests: set[str] = set()
    fixture_digests: set[str] = set()
    for expected_sequence, entry in enumerate(artifact["entries"], start=1):
        row = _expect_exact_keys(entry, BASELINE_ENTRY_FIELDS, "baseline history entry")
        if row["sequence"] != expected_sequence:
            raise SuiteError("baseline history entries must have contiguous sequences")
        _validate_observed_on(row["observed_on"], "baseline history entry observed_on")
        if not _is_nonempty_string(row["run_id"]):
            raise SuiteError("baseline history entry run_id must be a non-empty string")
        if row["run_id"] in run_ids:
            raise SuiteError("baseline history contains duplicate run_id")
        run_ids.add(row["run_id"])
        binding = _expect_exact_keys(row["binding"], ENTRY_BINDING_FIELDS, "baseline history entry binding")
        entry_attribution = _validate_scorecard_attribution(
            binding["attribution"], "baseline history entry attribution"
        )
        entry_replay = _validate_replay(binding["replay"], "baseline history entry replay")
        if entry_attribution != attribution or any(
            entry_replay[field] != baseline[field] for field in ("suite_digest", "policy_digest", "route_digest")
        ):
            raise SuiteError("baseline history entry drifts from the immutable baseline")
        if entry_replay["run_digest"] in run_digests:
            raise SuiteError("baseline history contains duplicate run_digest")
        if entry_replay["fixture_digest"] in fixture_digests:
            raise SuiteError("baseline history contains duplicate fixture_digest")
        run_digests.add(entry_replay["run_digest"])
        fixture_digests.add(entry_replay["fixture_digest"])
        summary = _validate_summary(row["summary"], "baseline history entry summary")
        if not isinstance(row["cases"], list) or len(row["cases"]) != len(case_ids):
            raise SuiteError("baseline history entry cases do not match the baseline")
        projected_cases: list[dict[str, Any]] = []
        for case in row["cases"]:
            projected = _expect_exact_keys(case, PROJECTED_CASE_FIELDS, "baseline history entry case")
            if not isinstance(projected["passed"], bool):
                raise SuiteError("baseline history entry case must contain a boolean status")
            violation_codes = _validate_violation_codes(
                projected["violation_codes"], "baseline history entry case"
            )
            if projected["passed"] == bool(violation_codes):
                raise SuiteError("baseline history entry case pass state does not match violation codes")
            projected_cases.append(projected)
        if [case["case_id"] for case in projected_cases] != case_ids:
            raise SuiteError("baseline history entry cases do not match the baseline")
        actual = {
            "total": len(projected_cases),
            "passed": sum(1 for case in projected_cases if case["passed"]),
            "failed": sum(1 for case in projected_cases if not case["passed"]),
        }
        if summary != actual:
            raise SuiteError("baseline history entry summary does not match its cases")
    return artifact


def load_baseline_history(path: Path) -> dict[str, Any]:
    """Load a D1 redacted scorecard history; it is evidence, never a decision gate."""
    try:
        history = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot load baseline history: {exc}") from exc
    return _validate_baseline_history(history)


def compare_scorecard_to_history(scorecard: Any, history: Any) -> dict[str, Any]:
    """Return a redacted informational comparison against the most recent observed entry."""
    artifact = _validate_baseline_history(history)
    candidate = _scorecard_projection(scorecard)
    baseline = artifact["baseline"]
    if candidate["attribution"] != baseline["attribution"] or any(
        candidate["replay"][field] != baseline[field]
        for field in ("suite_digest", "policy_digest", "route_digest")
    ):
        raise SuiteError("scorecard drifts from history baseline")
    latest = artifact["entries"][-1]
    return {
        "schema_version": 1,
        "artifact_type": "informational_synthetic_history_comparison",
        "data_class": "synthetic_only",
        "execution": "offline_deterministic",
        "history_id": artifact["history_id"],
        "sample_count": len(artifact["entries"]),
        "candidate": {"run_id": candidate["run_id"], "summary": candidate["summary"]},
        "latest": {"run_id": latest["run_id"], "summary": latest["summary"]},
        "summary_delta": {
            "passed": candidate["summary"]["passed"] - latest["summary"]["passed"],
            "failed": candidate["summary"]["failed"] - latest["summary"]["failed"],
        },
        "informational_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--responses", type=Path)
    inputs.add_argument("--provider-run", type=Path)
    args = parser.parse_args(argv)
    try:
        suite = load_suite(args.suite)
        if args.responses:
            responses = json.loads(args.responses.read_text())
            report = evaluate_suite(suite, responses)
        else:
            report = evaluate_provider_run(suite, load_provider_run(args.provider_run))
    except (SuiteError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": "invalid_eval_input", "detail": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
