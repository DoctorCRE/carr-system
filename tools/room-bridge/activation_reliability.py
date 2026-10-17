"""Fail-closed activation and reliability contracts for the existing passport.

This module deliberately contains validators and deterministic projections only.
It does not create a second receipt, workflow engine, memory store, or authority
source.  The ContextBundle is an immutable plan-bound list of canonical refs;
the two sections validated here are embedded in the existing envelope/receipt.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import execution_contract as base


class ActivationReliabilityError(base.ContractError):
    """A plan-bound activation or reliability fact is malformed or forged."""


KINDS = {"doctrine", "rule", "decision", "memory", "skill", "prior_failure", "architecture_constraint"}
DISPOSITIONS = {"applied", "not_applicable", "conflicted", "stale", "missing"}
MODES = {"shadow", "canary", "live", "enforced"}
FRESHNESS = {"fresh", "stale", "unknown"}
MAX_ITEMS = 64


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    try:
        return base._expect_exact(value, fields, label)
    except base.ContractError as exc:
        raise ActivationReliabilityError(str(exc)) from exc


def _id(value: Any, label: str) -> str:
    try:
        return base._string(value, label, identifier=True)
    except base.ContractError as exc:
        raise ActivationReliabilityError(str(exc)) from exc


def _text(value: Any, label: str) -> str:
    try:
        return base._string(value, label)
    except base.ContractError as exc:
        raise ActivationReliabilityError(str(exc)) from exc


def _digest(value: Any, label: str) -> str:
    try:
        return base._digest(value, label)
    except base.ContractError as exc:
        raise ActivationReliabilityError(str(exc)) from exc


def _refs(value: Any, label: str) -> list[str]:
    try:
        return base._list_of_strings(value, label)
    except base.ContractError as exc:
        raise ActivationReliabilityError(str(exc)) from exc


def _timestamp(value: Any, label: str) -> str:
    try:
        return base._timestamp(value, label)
    except base.ContractError as exc:
        raise ActivationReliabilityError(str(exc)) from exc


BUNDLE_HEADER_FIELDS = {
    "tenant_id", "work_request_id", "accepted_plan_revision_id", "accepted_plan_revision",
    "accepted_plan_digest", "issued_at", "mode", "retrieval_policy", "retrieval_policy_version",
}
BUNDLE_HEADER_OPTIONAL_FIELDS = {"binding_id", "expires_at", "compiler_id", "compiler_version", "compiler_digest", "query_basis_digest", "grounding_plan"}
BUNDLE_ITEM_FIELDS = {
    "kind", "canonical_ref", "revision", "digest", "required", "trigger", "consumer",
    "enforcement", "redaction_class", "freshness",
}
BUNDLE_ITEM_OPTIONAL_FIELDS = {"artifact_kind", "scope_redaction", "trigger_ref", "consumer_ref", "delivery_mode", "representation_kind", "freshness_sla", "selection_reason", "selection_rank", "requirement_class"}
BUNDLE_FIELDS = {"schema_version", "header", "items", "bundle_digest"}
_BUNDLE_RUNTIME_HEADER_FIELDS = {"issued_at", "expires_at", "binding_id"}


def context_bundle_digest_preimage(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return the pure compiler output that the bundle digest commits to.

    The plan binding fields and every item revision/content digest remain in
    the preimage.  Runtime issuance metadata does not.  This is intentionally
    shared by proposal/acceptance, post-accept render, and activation callers.
    """
    header = dict(bundle["header"])
    for field in _BUNDLE_RUNTIME_HEADER_FIELDS:
        header.pop(field, None)
    return {"schema_version": bundle["schema_version"], "header": header, "items": bundle["items"]}


def _canonical_json(value: Any) -> str:
    """Match ops.guidance_import_canonical_json(jsonb), without its newline.

    All activation/envelope digests use the existing portable CARR canonical
    JSON compiler: UTF-8, lexical key order, compact separators.  `jsonb::text`
    is intentionally not used because its key ordering/whitespace is a
    PostgreSQL rendering detail rather than this cross-runtime contract.
    """
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ActivationReliabilityError("context bundle compiler preimage must be canonical JSON") from exc


def context_bundle_digest(bundle: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(context_bundle_digest_preimage(bundle)).encode("utf-8")
    ).hexdigest()


def _validate_bundle_item(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ActivationReliabilityError(f"context bundle item[{index}] must be an object")
    unknown = sorted(set(value) - (BUNDLE_ITEM_FIELDS | BUNDLE_ITEM_OPTIONAL_FIELDS))
    if unknown:
        raise ActivationReliabilityError(f"context bundle item[{index}] has unknown fields: {', '.join(unknown)}")
    item = _exact({key: value[key] for key in BUNDLE_ITEM_FIELDS}, BUNDLE_ITEM_FIELDS, f"context bundle item[{index}]")
    if "artifact_kind" in value and value["artifact_kind"] != item["kind"]:
        raise ActivationReliabilityError("context bundle artifact_kind must equal kind")
    if "requirement_class" in value and value["requirement_class"] != ("required" if item["required"] else "advisory"):
        raise ActivationReliabilityError("context bundle requirement_class must match required")
    for field in ("scope_redaction", "trigger_ref", "consumer_ref", "delivery_mode", "representation_kind", "selection_reason"):
        if field in value: _text(value[field], f"context bundle {field}")
    if "selection_rank" in value and (not isinstance(value["selection_rank"], int) or value["selection_rank"] < 0):
        raise ActivationReliabilityError("context bundle selection_rank must be non-negative")
    if item["kind"] not in KINDS:
        raise ActivationReliabilityError("context bundle item kind is invalid")
    _id(item["canonical_ref"], "context bundle canonical_ref")
    _id(item["revision"], "context bundle revision")
    _digest(item["digest"], "context bundle item digest")
    if not isinstance(item["required"], bool):
        raise ActivationReliabilityError("context bundle required must be boolean")
    for field in ("trigger", "consumer", "enforcement", "redaction_class"):
        _text(item[field], f"context bundle {field}")
    if item["freshness"] not in FRESHNESS:
        raise ActivationReliabilityError("context bundle freshness is invalid")
    return item


def validate_context_bundle(value: Any, *, binding: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a bounded immutable bundle and its deterministic digest."""
    bundle = _exact(value, BUNDLE_FIELDS, "context bundle")
    if bundle["schema_version"] != "context-bundle.v1":
        raise ActivationReliabilityError("unsupported context bundle schema_version")
    raw_header = bundle["header"]
    if not isinstance(raw_header, dict): raise ActivationReliabilityError("context bundle header must be an object")
    unknown_header = sorted(set(raw_header) - (BUNDLE_HEADER_FIELDS | BUNDLE_HEADER_OPTIONAL_FIELDS))
    if unknown_header: raise ActivationReliabilityError(f"context bundle header has unknown fields: {', '.join(unknown_header)}")
    header = _exact({key: raw_header[key] for key in BUNDLE_HEADER_FIELDS}, BUNDLE_HEADER_FIELDS, "context bundle header")
    _id(header["tenant_id"], "context bundle tenant_id")
    _id(header["work_request_id"], "context bundle work_request_id")
    _id(header["accepted_plan_revision_id"], "context bundle accepted_plan_revision_id")
    if not isinstance(header["accepted_plan_revision"], int) or isinstance(header["accepted_plan_revision"], bool) or header["accepted_plan_revision"] < 1:
        raise ActivationReliabilityError("context bundle accepted_plan_revision must be positive")
    _digest(header["accepted_plan_digest"], "context bundle accepted_plan_digest")
    _timestamp(header["issued_at"], "context bundle issued_at")
    if header["mode"] not in MODES:
        raise ActivationReliabilityError("context bundle mode is invalid")
    _id(header["retrieval_policy"], "context bundle retrieval_policy")
    _text(header["retrieval_policy_version"], "context bundle retrieval_policy_version")
    if "binding_id" in raw_header: _id(raw_header["binding_id"], "context bundle binding_id")
    if "expires_at" in raw_header: _timestamp(raw_header["expires_at"], "context bundle expires_at")
    if "compiler_id" in raw_header: _id(raw_header["compiler_id"], "context bundle compiler_id")
    if "compiler_version" in raw_header: _text(raw_header["compiler_version"], "context bundle compiler_version")
    if "compiler_digest" in raw_header: _digest(raw_header["compiler_digest"], "context bundle compiler_digest")
    if "query_basis_digest" in raw_header: _digest(raw_header["query_basis_digest"], "context bundle query_basis_digest")
    if "grounding_plan" in raw_header and not isinstance(raw_header["grounding_plan"], dict): raise ActivationReliabilityError("context bundle grounding_plan must be an object")
    items = bundle["items"]
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ActivationReliabilityError(f"context bundle must contain 1..{MAX_ITEMS} items")
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(items):
        item = _validate_bundle_item(raw, index)
        key = (item["kind"], item["canonical_ref"], item["revision"])
        if key in seen:
            raise ActivationReliabilityError("context bundle contains duplicate item")
        seen.add(key)
    _digest(bundle["bundle_digest"], "context bundle bundle_digest")
    preimage = context_bundle_digest_preimage(bundle)
    if bundle["bundle_digest"] != context_bundle_digest(bundle):
        raise ActivationReliabilityError("context bundle digest does not bind exact items")
    if binding is not None:
        if not isinstance(binding, dict):
            raise ActivationReliabilityError("context bundle binding must be an object")
        expected = {
            "tenant_id": binding.get("tenant_id"), "work_request_id": binding.get("work_request_id"),
            "accepted_plan_revision_id": binding.get("accepted_plan_revision_id"),
            "accepted_plan_revision": binding.get("accepted_plan_revision"),
            "accepted_plan_digest": binding.get("accepted_plan_digest"),
        }
        for binding_field, expected_value in expected.items():
            if expected_value is not None and header[binding_field] != expected_value:
                raise ActivationReliabilityError(f"context bundle {binding_field} does not bind canonical plan")
    return bundle


def make_context_bundle(header: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Create the canonical digest once; callers still must persist immutably."""
    bundle = {"schema_version": "context-bundle.v1", "header": header, "items": items}
    bundle["bundle_digest"] = context_bundle_digest(bundle)
    return validate_context_bundle(bundle)


PLAN_CONTEXT_FIELDS = {"bundle_digest", "item_refs"}


def validate_plan_context_binding(value: Any, bundle: Any | None = None) -> dict[str, Any]:
    binding = _exact(value, PLAN_CONTEXT_FIELDS, "plan context binding")
    _digest(binding["bundle_digest"], "plan context bundle_digest")
    refs = _refs(binding["item_refs"], "plan context item_refs")
    if len(refs) != len(set(refs)):
        raise ActivationReliabilityError("plan context item_refs must be unique")
    if bundle is not None:
        checked = validate_context_bundle(bundle)
        if binding["bundle_digest"] != checked["bundle_digest"]:
            raise ActivationReliabilityError("plan context binding does not match bundle")
        expected = {item["canonical_ref"] for item in checked["items"]}
        if set(refs) != expected:
            raise ActivationReliabilityError("plan context item_refs must exactly match bundle")
    return binding


def bind_context_into_plan_preimage(plan_preimage: dict[str, Any], bundle: Any) -> tuple[dict[str, Any], str]:
    """Compiler seam used before plan acceptance; digest includes exact refs."""
    checked = validate_context_bundle(bundle)
    if not isinstance(plan_preimage, dict):
        raise ActivationReliabilityError("plan preimage must be an object")
    context = {"bundle_digest": checked["bundle_digest"], "item_refs": [item["canonical_ref"] for item in checked["items"]]}
    preimage = dict(plan_preimage)
    if "context_activation" in preimage and preimage["context_activation"] != context:
        raise ActivationReliabilityError("plan preimage context activation is immutable")
    preimage["context_activation"] = context
    return preimage, base.canonical_digest(preimage)


def recompute_accepted_plan_context(plan: Any, bundle: Any) -> dict[str, Any]:
    """Acceptance readback: recompute the exact plan preimage, never trust a flag."""
    if not isinstance(plan, dict) or "preimage" not in plan or "plan_hash" not in plan:
        raise ActivationReliabilityError("accepted plan must expose preimage and plan_hash")
    preimage, digest = bind_context_into_plan_preimage(plan["preimage"], bundle)
    if plan["plan_hash"] != digest:
        raise ActivationReliabilityError("accepted plan hash does not include context activation")
    return {"preimage": preimage, "plan_hash": digest, "context_binding": preimage["context_activation"]}


ENVELOPE_ACTIVATION_FIELDS = {"bundle_digest", "item_refs", "mode", "retrieval_policy_version"}
ENVELOPE_RELIABILITY_FIELDS = {"policy_ref", "policy_digest", "risk_class", "mode"}
RUNTIME_PROFILE_FIELDS = {"ref", "digest", "profile_key", "profile_version", "provider_id", "model_id", "desk", "policy_ref", "policy_digest", "modality", "reasoning_effort_ref", "sampling_profile_ref", "context_budget", "cache_policy_ref", "knowledge_cutoff_posture", "tool_calling_mode"}
RUNTIME_ENVIRONMENT_FIELDS = {"environment_provider_ref", "environment_provider_version", "environment_provider_digest", "environment_requirement_digest", "environment_configuration_digest", "environment_backend_kind", "environment_source_class", "environment_isolation_class", "environment_capability_refs", "environment_conformance_ref", "environment_conformance_digest", "environment_binding_digest"}
TOPOLOGY_FIELDS = {"ref", "digest", "kind", "harness_digest", "parallelism", "code_model_step_refs", "fallback_policy_ref", "stop_condition_refs", "context_refresh_policy_ref", "memory_policy_ref", "sandbox_ref", "guardrail_ref", "threat_model_ref"}
EVALUATION_PLAN_FIELDS = {"ref", "digest", "lane_ref", "risk_class", "rubric_digest", "case_set_digest", "evaluator_policy_digest", "evaluator_ref", "rubric_ref", "evaluator_version", "evaluator_digest", "required_rungs", "required_deterministic_check_refs", "critical_dimensions", "human_acceptance_required", "outcome_horizon_ref", "outcome_horizon_not_before", "requirements"}
EVALUATION_REQUIREMENTS_FIELDS = {"required_evaluator_kinds", "minimum_held_out_case_count", "minimum_calibration_ref_count", "maximum_critical_failure_count", "maximum_critical_failure_rate", "confidence_posture", "drift_tolerance", "independent_review_required", "human_acceptance_required", "outcome_horizon_required"}


def _validate_runtime_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - (RUNTIME_PROFILE_FIELDS | RUNTIME_ENVIRONMENT_FIELDS):
        raise ActivationReliabilityError("execution runtime_profile has unknown fields")
    row = _exact({key: value[key] for key in RUNTIME_PROFILE_FIELDS}, RUNTIME_PROFILE_FIELDS, "execution runtime_profile")
    for field in ("ref", "profile_key", "provider_id", "policy_ref", "modality", "reasoning_effort_ref", "sampling_profile_ref", "cache_policy_ref", "knowledge_cutoff_posture", "tool_calling_mode"):
        _id(row[field], f"execution runtime_profile {field}")
    for field in ("model_id", "desk"):
        _text(row[field], f"execution runtime_profile {field}")
    _digest(row["digest"], "execution runtime_profile digest"); _digest(row["policy_digest"], "execution runtime_profile policy_digest")
    if not isinstance(row["profile_version"], int) or isinstance(row["profile_version"], bool) or row["profile_version"] < 1: raise ActivationReliabilityError("execution runtime profile_version must be positive")
    if not isinstance(row["context_budget"], int) or isinstance(row["context_budget"], bool) or row["context_budget"] < 1: raise ActivationReliabilityError("execution runtime context_budget must be positive")
    present = RUNTIME_ENVIRONMENT_FIELDS & set(value)
    if present and present != RUNTIME_ENVIRONMENT_FIELDS:
        raise ActivationReliabilityError("execution runtime environment binding must be complete")
    if present:
        import execution_environment
        binding = {
            "provider_ref": value["environment_provider_ref"], "provider_version": value["environment_provider_version"],
            "provider_digest": value["environment_provider_digest"], "requirement_digest": value["environment_requirement_digest"],
            "configuration_digest": value["environment_configuration_digest"], "backend_kind": value["environment_backend_kind"],
            "source_class": value["environment_source_class"], "isolation_class": value["environment_isolation_class"],
            "capability_refs": value["environment_capability_refs"], "conformance_ref": value["environment_conformance_ref"],
            "conformance_digest": value["environment_conformance_digest"], "binding_digest": value["environment_binding_digest"],
        }
        execution_environment.validate_environment_binding(binding)
        row.update({key: value[key] for key in RUNTIME_ENVIRONMENT_FIELDS})
    return row


def _validate_topology(value: Any) -> dict[str, Any]:
    row = _exact(value, TOPOLOGY_FIELDS, "execution topology")
    for field in ("ref", "kind", "parallelism", "fallback_policy_ref", "context_refresh_policy_ref", "memory_policy_ref", "sandbox_ref", "guardrail_ref", "threat_model_ref"):
        _id(row[field], f"execution topology {field}")
    _digest(row["digest"], "execution topology digest"); _digest(row["harness_digest"], "execution topology harness_digest")
    if row["kind"] not in {"fixed_workflow", "single_agent_loop", "multi_agent"} or row["parallelism"] not in {"sequential", "parallel"}: raise ActivationReliabilityError("execution topology kind/parallelism is invalid")
    for field in ("code_model_step_refs", "stop_condition_refs"):
        _refs(row[field], f"execution topology {field}")
    return row


def _validate_evaluation_plan(value: Any) -> dict[str, Any]:
    row = _exact(value, EVALUATION_PLAN_FIELDS, "execution evaluation_plan")
    for field in ("ref", "lane_ref", "outcome_horizon_ref", "evaluator_ref", "rubric_ref", "evaluator_version"):
        _id(row[field], f"execution evaluation_plan {field}")
    for field in ("digest", "rubric_digest", "case_set_digest", "evaluator_policy_digest", "evaluator_digest"):
        _digest(row[field], f"execution evaluation_plan {field}")
    _timestamp(row["outcome_horizon_not_before"], "execution evaluation plan outcome_horizon_not_before")
    if row["risk_class"] not in {f"R{i}" for i in range(7)} or not isinstance(row["human_acceptance_required"], bool): raise ActivationReliabilityError("execution evaluation plan risk/human requirement invalid")
    _refs(row["required_rungs"], "execution evaluation plan required_rungs")
    required_checks = _refs(row["required_deterministic_check_refs"], "execution evaluation plan required deterministic check refs")
    critical_dimensions = _refs(row["critical_dimensions"], "execution evaluation plan critical_dimensions")
    if not required_checks or len(required_checks) != len(set(required_checks)):
        raise ActivationReliabilityError("evaluation plan must name unique required deterministic check refs")
    if not critical_dimensions or len(critical_dimensions) != len(set(critical_dimensions)):
        raise ActivationReliabilityError("evaluation plan must name unique critical dimensions")
    requirements = _exact(row["requirements"], EVALUATION_REQUIREMENTS_FIELDS, "execution evaluation plan requirements")
    if not isinstance(requirements["required_evaluator_kinds"], list) or set(requirements["required_evaluator_kinds"]) - {"deterministic", "judge", "human_acceptance"}: raise ActivationReliabilityError("evaluation required evaluator kinds invalid")
    for field in ("minimum_held_out_case_count", "minimum_calibration_ref_count", "maximum_critical_failure_count"):
        if not isinstance(requirements[field], int) or isinstance(requirements[field], bool) or requirements[field] < 0: raise ActivationReliabilityError(f"evaluation requirement {field} invalid")
    if not isinstance(requirements["maximum_critical_failure_rate"], (int, float)) or isinstance(requirements["maximum_critical_failure_rate"], bool) or not 0 <= requirements["maximum_critical_failure_rate"] <= 1: raise ActivationReliabilityError("evaluation critical failure rate invalid")
    if requirements["confidence_posture"] not in {"none", "lower_bound_required"} or requirements["drift_tolerance"] not in {"no_critical_regression", "bounded"}: raise ActivationReliabilityError("evaluation confidence/drift policy invalid")
    for field in ("independent_review_required", "human_acceptance_required", "outcome_horizon_required"):
        if not isinstance(requirements[field], bool): raise ActivationReliabilityError(f"evaluation requirement {field} must be boolean")
    return row


def validate_envelope_bindings(value: Any, *, bundle: Any | None = None) -> dict[str, Any] | None:
    activation = value.get("activation_binding") if isinstance(value, dict) else None
    reliability = value.get("reliability_policy_binding") if isinstance(value, dict) else None
    if activation is not None:
        row = _exact(activation, ENVELOPE_ACTIVATION_FIELDS, "execution activation_binding")
        _digest(row["bundle_digest"], "execution activation bundle_digest")
        _refs(row["item_refs"], "execution activation item_refs")
        if row["mode"] not in MODES:
            raise ActivationReliabilityError("execution activation mode is invalid")
        _text(row["retrieval_policy_version"], "execution retrieval policy version")
        if bundle is not None:
            checked = validate_context_bundle(bundle)
            if row["bundle_digest"] != checked["bundle_digest"] or set(row["item_refs"]) != {i["canonical_ref"] for i in checked["items"]}:
                raise ActivationReliabilityError("execution activation binding does not match bundle")
    if reliability is not None:
        row = _exact(reliability, ENVELOPE_RELIABILITY_FIELDS, "execution reliability_policy_binding")
        _id(row["policy_ref"], "execution reliability policy_ref")
        _digest(row["policy_digest"], "execution reliability policy_digest")
        if row["risk_class"] not in {f"R{i}" for i in range(7)} or row["mode"] not in MODES:
            raise ActivationReliabilityError("execution reliability policy binding is invalid")
    if activation is not None and reliability is not None and activation["mode"] != reliability["mode"]:
        raise ActivationReliabilityError("activation and reliability modes must agree")
    if activation is not None or reliability is not None:
        if not isinstance(value, dict): raise ActivationReliabilityError("execution envelope must be an object")
        runtime = _validate_runtime_metadata(value.get("runtime_profile"))
        topology = _validate_topology(value.get("execution_topology"))
        evaluation = _validate_evaluation_plan(value.get("evaluation_plan"))
        if reliability is not None and (reliability["policy_digest"] != runtime["policy_digest"] or reliability["risk_class"] != evaluation["risk_class"]):
            raise ActivationReliabilityError("execution reliability policy must bind runtime and evaluation plan")
    return activation


DISPOSITION_FIELDS = {"item_ref", "disposition", "evidence_refs", "reason_ref"}
DISPOSITION_OPTIONAL_FIELDS = {"stage_ref", "tool_ref"}
CLOSURE_FIELDS = {"state", "unresolved_required_item_refs", "derived_by"}
KNOWLEDGE_FIELDS = {"bundle_digest", "item_dispositions", "closure", "mode", "canonical_binding"}
KNOWLEDGE_BINDING_FIELDS = {"work_request_id", "work_request_version", "accepted_plan_digest", "envelope_digest", "activation_binding_ref"}


def validate_knowledge_activation(value: Any, bundle: Any | None = None, *, envelope: Any | None = None, attempt_id: str | None = None) -> dict[str, Any]:
    row = _exact(value, KNOWLEDGE_FIELDS, "receipt knowledge_activation")
    _digest(row["bundle_digest"], "receipt knowledge bundle_digest")
    if row["mode"] not in MODES:
        raise ActivationReliabilityError("receipt knowledge activation mode is invalid")
    canonical_binding = _exact(row["canonical_binding"], KNOWLEDGE_BINDING_FIELDS, "receipt knowledge canonical binding")
    _id(canonical_binding["work_request_id"], "receipt knowledge binding work_request_id")
    _digest(canonical_binding["accepted_plan_digest"], "receipt knowledge binding accepted_plan_digest")
    _digest(canonical_binding["envelope_digest"], "receipt knowledge binding envelope_digest")
    _id(canonical_binding["activation_binding_ref"], "receipt knowledge binding activation_binding_ref")
    if not isinstance(canonical_binding["work_request_version"], int) or isinstance(canonical_binding["work_request_version"], bool) or canonical_binding["work_request_version"] < 1:
        raise ActivationReliabilityError("receipt knowledge binding work_request_version must be positive")
    if bundle is None:
        raise ActivationReliabilityError("knowledge activation requires the exact context bundle")
    checked = validate_context_bundle(bundle)
    if row["bundle_digest"] != checked["bundle_digest"]:
        raise ActivationReliabilityError("receipt knowledge activation does not bind bundle")
    if envelope is not None:
        validate_envelope_bindings(envelope, bundle=checked)
        activation = envelope.get("activation_binding")
        if activation is None:
            raise ActivationReliabilityError("receipt knowledge activation requires envelope activation binding")
        if activation["bundle_digest"] != row["bundle_digest"]:
            raise ActivationReliabilityError("receipt knowledge activation does not bind envelope")
        expected_binding = {
            "work_request_id": envelope["work_request_id"],
            "work_request_version": envelope["state_binding"]["state_version"],
            "accepted_plan_digest": envelope["plan_revision"]["digest"],
            "envelope_digest": base.execution_envelope_digest(envelope),
            "activation_binding_ref": envelope["context_activation_ref"],
        }
        if canonical_binding != expected_binding:
            raise ActivationReliabilityError("receipt knowledge activation canonical binding does not match exact envelope")
    dispositions = row["item_dispositions"]
    if not isinstance(dispositions, list):
        raise ActivationReliabilityError("knowledge item_dispositions must be a list")
    expected = {item["canonical_ref"]: item for item in checked["items"]}
    seen: set[str] = set()
    for index, raw in enumerate(dispositions):
        if not isinstance(raw, dict) or set(raw) - (DISPOSITION_FIELDS | DISPOSITION_OPTIONAL_FIELDS):
            raise ActivationReliabilityError(f"knowledge disposition[{index}] has unknown fields")
        item = _exact({key: raw[key] for key in DISPOSITION_FIELDS}, DISPOSITION_FIELDS, f"knowledge disposition[{index}]")
        _id(item["item_ref"], "knowledge disposition item_ref")
        if item["item_ref"] in seen or item["item_ref"] not in expected:
            raise ActivationReliabilityError("knowledge disposition has duplicate or dangling item_ref")
        seen.add(item["item_ref"])
        if item["disposition"] not in DISPOSITIONS:
            raise ActivationReliabilityError("knowledge disposition is invalid")
        _refs(item["evidence_refs"], "knowledge disposition evidence_refs")
        _id(item["reason_ref"], "knowledge disposition reason_ref")
        for field in DISPOSITION_OPTIONAL_FIELDS:
            if field in raw: _id(raw[field], f"knowledge disposition {field}")
        required = expected[item["item_ref"]]["required"]
        if item["disposition"] == "applied" and (not item["evidence_refs"] or not any(field in raw for field in DISPOSITION_OPTIONAL_FIELDS)):
            raise ActivationReliabilityError("applied knowledge disposition needs evidence and stage/tool link")
        if required and item["disposition"] in {"applied", "conflicted", "stale", "missing"} and not item["evidence_refs"]:
            raise ActivationReliabilityError("required knowledge disposition needs evidence")
    if seen != set(expected):
        raise ActivationReliabilityError("knowledge activation must disposition every bundle item")
    closure = _exact(row["closure"], CLOSURE_FIELDS, "knowledge activation closure")
    if closure["derived_by"] != "server" or closure["state"] not in {"open", "blocked", "closed", "not_activated"}:
        raise ActivationReliabilityError("knowledge closure must be server-derived")
    unresolved = sorted(item["canonical_ref"] for item in checked["items"] if item["required"] and next(d for d in dispositions if d["item_ref"] == item["canonical_ref"])["disposition"] != "applied")
    if closure["unresolved_required_item_refs"] != unresolved:
        raise ActivationReliabilityError("knowledge closure unresolved refs are not server-derived")
    expected_state = "closed" if not unresolved and row["mode"] in {"canary", "live", "enforced"} else ("not_activated" if row["mode"] == "shadow" else "blocked")
    if closure["state"] != expected_state:
        raise ActivationReliabilityError("knowledge closure state is not derived from dispositions and mode")
    return row


RELIABILITY_FIELDS = {"route_digest", "topology_digest", "evaluation_plan_digest", "grounding_sufficiency", "deterministic_checks", "model_judgement", "human_acceptance", "trajectory", "evaluator_results", "corrections", "defects", "incidents", "downstream_outcome", "outcome_horizon", "process_metrics", "eval_candidates", "shadow_comparisons", "learning_disposition", "telemetry", "closure"}
RELIABILITY_ENVIRONMENT_FIELDS = {"environment_binding_digest", "environment_evidence"}
GROUNDING_FIELDS = {"state", "evidence_refs", "required_supplied", "required_used", "required_missing", "advisory_supplied", "advisory_used", "freshness_failures", "retrieval_failures"}
CHECK_FIELDS = {"check_id", "state", "critical", "evidence_refs"}
JUDGE_FIELDS = {"state", "judge_ref", "evidence_refs"}
HUMAN_FIELDS = {"state", "actor_ref", "evidence_refs", "outcome_feedback_ref", "outcome_feedback_hash"}
EVENT_FIELDS = {"event_ref", "kind", "evidence_refs", "summary"}
OUTCOME_FIELDS = {"state", "brokerage_ref", "evidence_refs", "outcome_feedback_ref", "outcome_feedback_hash"}
CANDIDATE_FIELDS = {"candidate_id", "case_ref", "golden_set_ref", "context_binding", "basis", "state", "promotion_state", "evidence_refs"}
SHADOW_COMPAT_FIELDS = {"comparison_id", "case_ref", "context_binding", "baseline_route", "candidate_route", "route_dimensions", "evaluation_dimensions", "dimension_results", "candidate_execution", "baseline_binding_digest", "candidate_binding_digest", "state", "promotion_state", "requested_state", "policy_version_cas", "kill_switch_ref", "rollback_ref", "expires_at", "outcome_horizon", "result_provenance", "evidence_refs"}
TELEMETRY_FIELDS = {"signal_id", "state", "trigger", "consumer", "enforcement", "owner", "remedy", "verification", "auto_clear"}
TRAJECTORY_FIELDS = {"sequence", "stage_ref", "parent_event_ref", "decision_class", "tool_class", "result_state", "fallback_state", "guardrail_state", "latency_ms", "evidence_refs"}
EVALUATOR_FIELDS = {"kind", "evaluator_ref", "rubric_ref", "evaluator_version", "evaluator_digest", "status", "confidence", "critical", "independence_state", "held_out_case_count", "check_refs", "dimension_refs", "evidence_refs", "judge_provenance", "calibration_evidence_refs"}
OUTCOME_HORIZON_FIELDS = {"state", "ends_at", "as_of", "evidence_refs"}
PROCESS_METRIC_FIELDS = {"latency_ms", "cost_usd", "input_tokens", "output_tokens", "cached_input_tokens", "retry_count", "recovery_count", "context_reconstruction_ms", "human_intervention_count", "security_event_refs"}
RELIABILITY_CLOSURE_FIELDS = {"state", "reasons", "derived_by"}


def _validate_action_telemetry(rows: Any) -> None:
    if not isinstance(rows, list):
        raise ActivationReliabilityError("reliability telemetry must be a list")
    if rows:
        raise ActivationReliabilityError("executor receipt telemetry must be empty; server derives canonical signals")
    for index, raw in enumerate(rows):
        row = _exact(raw, TELEMETRY_FIELDS, f"reliability telemetry[{index}]")
        _id(row["signal_id"], "reliability telemetry signal_id")
        if row["state"] not in {"open", "verified", "cleared", "unknown"}:
            raise ActivationReliabilityError("reliability telemetry state is invalid")
        for field in ("trigger", "consumer", "enforcement", "owner", "remedy", "verification"):
            _text(row[field], f"reliability telemetry {field}")
        if not isinstance(row["auto_clear"], bool):
            raise ActivationReliabilityError("reliability telemetry auto_clear must be boolean")
        if row["state"] == "cleared" and row["verification"] != "verified":
            raise ActivationReliabilityError("telemetry cannot clear before verification")


def _validate_evidence_state(row: dict[str, Any], fields: set[str], label: str, states: set[str]) -> None:
    checked = _exact(row, fields, label)
    if checked["state"] not in states:
        raise ActivationReliabilityError(f"{label} state is invalid")
    _refs(checked["evidence_refs"], f"{label} evidence_refs")


def validate_reliability(value: Any, *, envelope: Any | None = None, attempt_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict): raise ActivationReliabilityError("receipt reliability must be an object")
    if set(value) - (RELIABILITY_FIELDS | RELIABILITY_ENVIRONMENT_FIELDS):
        raise ActivationReliabilityError("receipt reliability has unknown fields")
    row = _exact({key: value[key] for key in RELIABILITY_FIELDS}, RELIABILITY_FIELDS, "receipt reliability")
    for field in ("route_digest", "topology_digest", "evaluation_plan_digest"):
        _digest(row[field], f"reliability {field}")
    if envelope is not None:
        validate_envelope_bindings(envelope)
        for receipt_field, envelope_field in (("route_digest", "runtime_profile"), ("topology_digest", "execution_topology"), ("evaluation_plan_digest", "evaluation_plan")):
            if row[receipt_field] != envelope[envelope_field].get("digest"):
                raise ActivationReliabilityError(f"reliability {receipt_field} does not bind the server-issued envelope")
        runtime = envelope.get("runtime_profile", {})
        has_environment = bool(RUNTIME_ENVIRONMENT_FIELDS & set(runtime))
        if has_environment:
            if not RELIABILITY_ENVIRONMENT_FIELDS <= set(value):
                raise ActivationReliabilityError("environment-bound envelope requires receipt environment evidence")
            import execution_environment
            binding = {
                "provider_ref": runtime["environment_provider_ref"], "provider_version": runtime["environment_provider_version"],
                "provider_digest": runtime["environment_provider_digest"], "requirement_digest": runtime["environment_requirement_digest"],
                "configuration_digest": runtime["environment_configuration_digest"], "backend_kind": runtime["environment_backend_kind"],
                "source_class": runtime["environment_source_class"], "isolation_class": runtime["environment_isolation_class"],
                "capability_refs": runtime["environment_capability_refs"], "conformance_ref": runtime["environment_conformance_ref"],
                "conformance_digest": runtime["environment_conformance_digest"], "binding_digest": runtime["environment_binding_digest"],
            }
            if value["environment_binding_digest"] != binding["binding_digest"]:
                raise ActivationReliabilityError("receipt environment binding digest does not bind envelope")
            execution_environment.validate_environment_evidence(value["environment_evidence"], binding)
            row.update({key: value[key] for key in RELIABILITY_ENVIRONMENT_FIELDS})
        elif RELIABILITY_ENVIRONMENT_FIELDS & set(value):
            raise ActivationReliabilityError("legacy envelope cannot accept environment evidence")
    grounding = _exact(row["grounding_sufficiency"], GROUNDING_FIELDS, "grounding sufficiency")
    if grounding["state"] not in {"sufficient", "insufficient", "unknown"}:
        raise ActivationReliabilityError("grounding sufficiency is invalid")
    _refs(grounding["evidence_refs"], "grounding evidence_refs")
    for field in GROUNDING_FIELDS - {"state", "evidence_refs"}:
        refs = _refs(grounding[field], f"grounding {field}")
        if len(refs) != len(set(refs)): raise ActivationReliabilityError(f"grounding {field} must be unique")
    if set(grounding["required_used"]) - set(grounding["required_supplied"]):
        raise ActivationReliabilityError("grounding required_used must be supplied")
    if set(grounding["required_missing"]) & set(grounding["required_used"]):
        raise ActivationReliabilityError("grounding required item cannot be used and missing")
    if grounding["state"] == "sufficient" and (grounding["required_missing"] or grounding["freshness_failures"] or grounding["retrieval_failures"]):
        raise ActivationReliabilityError("grounding cannot claim sufficient with missing, stale, or retrieval failures")
    if not isinstance(row["deterministic_checks"], list):
        raise ActivationReliabilityError("deterministic_checks must be a list")
    seen_checks: set[str] = set(); critical_failure = False; critical_check_incomplete = False
    for index, raw in enumerate(row["deterministic_checks"]):
        check = _exact(raw, CHECK_FIELDS, f"deterministic check[{index}]")
        _id(check["check_id"], "deterministic check id")
        if check["check_id"] in seen_checks: raise ActivationReliabilityError("deterministic checks must be unique")
        seen_checks.add(check["check_id"])
        if check["state"] not in {"passed", "failed", "unknown", "not_run"}: raise ActivationReliabilityError("deterministic check state is invalid")
        if not isinstance(check["critical"], bool): raise ActivationReliabilityError("deterministic check critical must be boolean")
        _refs(check["evidence_refs"], "deterministic check evidence_refs")
        if check["state"] == "passed" and not check["evidence_refs"]:
            raise ActivationReliabilityError("passed deterministic check requires evidence")
        critical_failure = critical_failure or (check["critical"] and check["state"] == "failed")
        critical_check_incomplete = critical_check_incomplete or (check["critical"] and check["state"] in {"unknown", "not_run"})
    judge = _exact(row["model_judgement"], JUDGE_FIELDS, "model judgement")
    if judge["state"] not in {"pass", "fail", "unknown", "not_run"}: raise ActivationReliabilityError("model judgement state is invalid")
    _id(judge["judge_ref"], "model judgement judge_ref"); _refs(judge["evidence_refs"], "model judgement evidence_refs")
    if judge["state"] == "pass" and not judge["evidence_refs"]:
        raise ActivationReliabilityError("pass judgement requires evidence")
    human = _exact(row["human_acceptance"], HUMAN_FIELDS, "human acceptance")
    if human["state"] not in {"accepted", "rejected", "absent", "unknown"}: raise ActivationReliabilityError("human acceptance state is invalid")
    _id(human["actor_ref"], "human acceptance actor_ref"); _refs(human["evidence_refs"], "human acceptance evidence_refs")
    if human["state"] == "accepted" and (not human["evidence_refs"] or human["actor_ref"] == judge["judge_ref"] or not isinstance(human["outcome_feedback_ref"], str) or not isinstance(human["outcome_feedback_hash"], str)):
        raise ActivationReliabilityError("human acceptance requires independent evidence and actor")
    if human["state"] == "accepted": _id(human["outcome_feedback_ref"], "human acceptance outcome feedback ref"); _digest(human["outcome_feedback_hash"], "human acceptance outcome feedback hash")
    elif human["outcome_feedback_ref"] is not None or human["outcome_feedback_hash"] is not None: raise ActivationReliabilityError("non-accepted human state cannot bind outcome feedback")
    for field in ("corrections", "defects", "incidents"):
        if not isinstance(row[field], list): raise ActivationReliabilityError(f"reliability {field} must be a list")
        for index, raw in enumerate(row[field]):
            event = _exact(raw, EVENT_FIELDS, f"reliability {field}[{index}]")
            _id(event["event_ref"], f"reliability {field} event_ref"); _refs(event["evidence_refs"], f"reliability {field} evidence_refs"); _id(event["summary"], f"reliability {field} summary")
            if event["kind"] != field[:-1]: raise ActivationReliabilityError("reliability event kind must match its canonical fact lane")
    outcome = _exact(row["downstream_outcome"], OUTCOME_FIELDS, "downstream brokerage outcome")
    if outcome["state"] not in {"observed", "not_observed", "unknown"}: raise ActivationReliabilityError("downstream outcome state is invalid")
    _id(outcome["brokerage_ref"], "downstream brokerage_ref"); _refs(outcome["evidence_refs"], "downstream outcome evidence_refs")
    if outcome["state"] == "observed":
        if not isinstance(outcome["outcome_feedback_ref"], str) or not isinstance(outcome["outcome_feedback_hash"], str): raise ActivationReliabilityError("observed outcome requires accepted feedback binding")
        _id(outcome["outcome_feedback_ref"], "outcome feedback ref"); _digest(outcome["outcome_feedback_hash"], "outcome feedback hash")
    elif outcome["outcome_feedback_ref"] is not None or outcome["outcome_feedback_hash"] is not None: raise ActivationReliabilityError("unobserved outcome cannot bind feedback")
    if not isinstance(row["eval_candidates"], list) or row["eval_candidates"]:
        raise ActivationReliabilityError("executor receipts cannot self-propose evaluation candidates")
    if not isinstance(row["trajectory"], list): raise ActivationReliabilityError("reliability trajectory must be a list")
    previous = 0
    for index, raw in enumerate(row["trajectory"]):
        event = _exact(raw, TRAJECTORY_FIELDS, f"trajectory[{index}]")
        if event["sequence"] <= previous or isinstance(event["sequence"], bool): raise ActivationReliabilityError("trajectory must be strictly ordered")
        previous = event["sequence"]
        for field in ("stage_ref", "decision_class", "tool_class"): _id(event[field], f"trajectory {field}")
        if event["parent_event_ref"] is not None: _id(event["parent_event_ref"], "trajectory parent_event_ref")
        if event["result_state"] not in {"succeeded", "failed", "blocked", "unknown"} or event["fallback_state"] not in {"not_used", "used", "unavailable", "unknown"} or event["guardrail_state"] not in {"clear", "blocked", "triggered", "unknown"}:
            raise ActivationReliabilityError("trajectory state is invalid")
        if not isinstance(event["latency_ms"], int) or isinstance(event["latency_ms"], bool) or event["latency_ms"] < 0: raise ActivationReliabilityError("trajectory latency must be non-negative")
        _refs(event["evidence_refs"], "trajectory evidence_refs")
    if not isinstance(row["evaluator_results"], list) or not row["evaluator_results"]: raise ActivationReliabilityError("evaluator_results must be a non-empty list")
    evaluator_kinds: set[str] = set(); held_out = 0; independent = False; critical_eval_failure = False; critical_eval_incomplete = False
    for index, raw in enumerate(row["evaluator_results"]):
        evaluator = _exact(raw, EVALUATOR_FIELDS, f"evaluator result[{index}]")
        if evaluator["kind"] not in {"deterministic", "judge", "human_acceptance"} or evaluator["status"] not in {"passed", "failed", "blocked", "unknown", "not_run"} or evaluator["confidence"] not in {"high", "medium", "low", "unknown"} or evaluator["independence_state"] not in {"independent", "not_independent", "unknown"}:
            raise ActivationReliabilityError("evaluator result posture is invalid")
        for field in ("evaluator_ref", "rubric_ref", "judge_provenance"): _id(evaluator[field], f"evaluator {field}")
        _text(evaluator["evaluator_version"], "evaluator version"); _digest(evaluator["evaluator_digest"], "evaluator digest")
        if not isinstance(evaluator["held_out_case_count"], int) or isinstance(evaluator["held_out_case_count"], bool) or evaluator["held_out_case_count"] < 0: raise ActivationReliabilityError("evaluator held_out_case_count must be non-negative")
        _refs(evaluator["evidence_refs"], "evaluator evidence_refs"); _refs(evaluator["calibration_evidence_refs"], "evaluator calibration evidence_refs")
        check_refs = _refs(evaluator["check_refs"], "evaluator check_refs")
        dimension_refs = _refs(evaluator["dimension_refs"], "evaluator dimension_refs")
        if len(check_refs) != len(set(check_refs)) or len(dimension_refs) != len(set(dimension_refs)):
            raise ActivationReliabilityError("evaluator check/dimension refs must be unique")
        if evaluator["status"] == "passed" and not evaluator["evidence_refs"]: raise ActivationReliabilityError("passed evaluator needs evidence")
        if evaluator["kind"] == "judge" and not evaluator["calibration_evidence_refs"]: raise ActivationReliabilityError("judge evaluator needs calibration evidence")
        evaluator_kinds.add(evaluator["kind"]); held_out += evaluator["held_out_case_count"]
        if evaluator["independence_state"] == "independent":
            raise ActivationReliabilityError("executor receipt cannot self-attest evaluator independence")
        critical_eval_failure = critical_eval_failure or (evaluator["critical"] and evaluator["status"] in {"failed", "blocked"})
        critical_eval_incomplete = critical_eval_incomplete or (evaluator["critical"] and evaluator["status"] in {"unknown", "not_run"})
    horizon = _exact(row["outcome_horizon"], OUTCOME_HORIZON_FIELDS, "outcome horizon")
    if horizon["state"] not in {"mature", "immature", "unavailable", "stale", "unknown"}: raise ActivationReliabilityError("outcome horizon is invalid")
    _timestamp(horizon["ends_at"], "outcome horizon ends_at"); _timestamp(horizon["as_of"], "outcome horizon as_of"); _refs(horizon["evidence_refs"], "outcome horizon evidence_refs")
    if horizon["state"] == "mature" and horizon["as_of"] < horizon["ends_at"]: raise ActivationReliabilityError("mature outcome horizon cannot precede its end")
    metrics = _exact(row["process_metrics"], PROCESS_METRIC_FIELDS, "process metrics")
    for field in PROCESS_METRIC_FIELDS - {"cost_usd", "security_event_refs"}:
        if not isinstance(metrics[field], int) or isinstance(metrics[field], bool) or metrics[field] < 0: raise ActivationReliabilityError(f"process metric {field} must be non-negative integer")
    if not isinstance(metrics["cost_usd"], (int, float)) or isinstance(metrics["cost_usd"], bool) or metrics["cost_usd"] < 0: raise ActivationReliabilityError("process metric cost_usd must be non-negative")
    _refs(metrics["security_event_refs"], "process metric security_event_refs")
    if row["learning_disposition"] not in {"none", "proposed_eval", "triage_required", "unknown"}: raise ActivationReliabilityError("learning disposition is invalid")
    if not isinstance(row["shadow_comparisons"], list) or row["shadow_comparisons"]:
        raise ActivationReliabilityError("executor receipts cannot carry governed Policy Learning shadow comparisons")
    _validate_action_telemetry(row["telemetry"])
    closure = _exact(row["closure"], RELIABILITY_CLOSURE_FIELDS, "reliability closure")
    if closure["derived_by"] != "server" or closure["state"] not in {"blocked", "insufficient_evidence", "eligible_for_human_review"}: raise ActivationReliabilityError("reliability closure must be server-derived")
    reasons = _refs(closure["reasons"], "reliability closure reasons")
    requirements = (envelope or {}).get("evaluation_plan", {}).get("requirements", {})
    evaluation_plan = (envelope or {}).get("evaluation_plan", {})
    required_checks = set(evaluation_plan.get("required_deterministic_check_refs", []))
    critical_dimensions = set(evaluation_plan.get("critical_dimensions", []))
    if envelope is not None:
        if set(seen_checks) != required_checks:
            raise ActivationReliabilityError("deterministic checks do not exactly cover plan-required checks")
        if any(not check["critical"] for check in row["deterministic_checks"]):
            raise ActivationReliabilityError("plan-required deterministic checks cannot be caller-demoted")
        deterministic_results = [item for item in row["evaluator_results"] if item["kind"] == "deterministic"]
        if len(deterministic_results) != len(required_checks) or {item["check_refs"][0] for item in deterministic_results if len(item["check_refs"]) == 1} != required_checks or any(len(item["check_refs"]) != 1 or set(item["dimension_refs"]) != critical_dimensions for item in deterministic_results):
            raise ActivationReliabilityError("deterministic evaluator results do not exactly bind required checks and critical dimensions")
        if any(item["kind"] != "deterministic" and (item["check_refs"] or set(item["dimension_refs"]) != critical_dimensions) for item in row["evaluator_results"]):
            raise ActivationReliabilityError("non-deterministic evaluator results do not exactly bind critical dimensions")
    required_kinds = set(requirements.get("required_evaluator_kinds", ["deterministic", "judge", "human_acceptance"]))
    blockers: list[str] = []
    if critical_failure or critical_eval_failure: blockers.append("critical_deterministic_or_evaluator_failure")
    if critical_check_incomplete or critical_eval_incomplete: blockers.append("critical_evidence_incomplete")
    if grounding["state"] != "sufficient": blockers.append("grounding_insufficient")
    if requirements.get("outcome_horizon_required", True) and horizon["state"] != "mature": blockers.append("outcome_horizon_" + horizon["state"])
    if not required_kinds <= evaluator_kinds: blockers.append("required_evaluator_kind_missing")
    if held_out < requirements.get("minimum_held_out_case_count", 1): blockers.append("held_out_evidence_insufficient")
    blockers.append("authority_evaluation_evidence_missing")
    if requirements.get("confidence_posture", "lower_bound_required") == "lower_bound_required" and any(not item["calibration_evidence_refs"] for item in row["evaluator_results"] if item["kind"] == "judge"):
        blockers.append("lower_bound_or_calibration_evidence_missing")
    if judge["state"] != "pass": blockers.append("judge_not_passed")
    if requirements.get("human_acceptance_required", True) and human["state"] != "accepted": blockers.append("human_acceptance_missing")
    expected_state = "blocked" if critical_failure or critical_eval_failure else ("insufficient_evidence" if blockers else "eligible_for_human_review")
    expected_reason_refs = sorted("reason:" + item for item in blockers)
    if closure["state"] != expected_state or sorted(reasons) != expected_reason_refs:
        raise ActivationReliabilityError("reliability closure is not derived from evidence and risk policy")
    return row


def derived_reliability_state(value: Any) -> str:
    row = validate_reliability(value)
    return row["closure"]["state"]


def legacy_activation_state(receipt: Any) -> str:
    """Name the posture of old receipts without upgrading their evidence."""
    if not isinstance(receipt, dict) or receipt.get("schema_version") != "attempt-receipt.v1":
        raise ActivationReliabilityError("legacy receipt must be attempt-receipt.v1")
    return "not_activated" if "knowledge_activation" not in receipt else "unknown"
