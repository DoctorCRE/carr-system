"""Scope, environment and viewer contracts used by current callers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


class ContractError(ValueError):
    """A portable execution artifact failed its closed v1 contract."""


SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

ENVELOPE_FIELDS = {
    "schema_version", "envelope_id", "work_request_id", "plan_revision", "agent_session",
    "issued_at", "expires_at", "state_binding", "phase_binding", "evaluation_context", "request",
    "server_binding", "handoff",
}
ENVELOPE_OPTIONAL_FIELDS = {
    "activation_binding", "reliability_policy_binding", "context_activation_ref",
    "runtime_profile", "execution_topology", "evaluation_plan",
}
REQUEST_FIELDS = {"job_ref", "input_digest", "data_class", "allowed_actions", "declared_expectations"}
ENGINEERING_REPOSITORY_ACTIONS = (
    "repository:create-worktree", "repository:create-branch", "repository:write-declared-scope",
    "repository:run-checks", "repository:commit", "repository:push-branch", "repository:open-pr",
)
DECLARED_EXPECTATION_FIELDS = {"plan_step_refs", "component_refs", "component_dependencies", "resource_refs"}
COMPONENT_DEPENDENCY_FIELDS = {"component_ref", "depends_on_component_ref"}
PLAN_FIELDS = {"id", "revision", "digest"}
PLAN_OPTIONAL_FIELDS = {"context_binding"}
SESSION_FIELDS = {"id", "lease_expires_at"}
SERVER_BINDING_FIELDS = {"identity", "authority", "adapter"}
IDENTITY_FIELDS = {
    "organization_tenant_id", "sponsoring_human_id", "agent_principal_id", "runtime_principal",
    "personal_brain_scope", "personal_brain_version", "personal_rule_count", "derived_by",
    "client_mutable",
}
AUTHORITY_FIELDS = {
    "environment", "risk_class", "capability_profile", "capability_grant_ref", "read_only",
    "derived_by", "client_mutable",
}
ADAPTER_FIELDS = {
    "surface", "adapter_id", "adapter_version", "harness_id", "harness_version", "provider_id",
    "model_id", "native_session_ref", "configuration_fingerprint",
}
HANDOFF_FIELDS = {"mode", "replaces_agent_session_id", "capability_inherited"}
STATE_BINDING_FIELDS = {"state_version", "canonical_record_digest", "accepted_resource_revisions", "compare_and_swap_required"}
RESOURCE_REVISION_FIELDS = {"resource_ref", "revision_ref", "digest"}
PHASE_BINDING_FIELDS = {"phase_id", "session_affinity", "switch_conditions", "native_session_transfer"}
EVALUATION_CONTEXT_FIELDS = {"experiment_arm", "auditor_mode", "evaluation_kernel_ref", "workflow_rubric_digest", "case_set_digest"}
VERSIONED_METADATA_FIELDS = {"ref", "digest"}
RUNTIME_METADATA_OPTIONAL_FIELDS = {"profile_key", "profile_version", "provider_id", "model_id", "desk", "policy_ref", "policy_digest", "modality", "reasoning_effort_ref", "sampling_profile_ref", "context_budget", "cache_policy_ref", "knowledge_cutoff_posture", "tool_calling_mode", "environment_provider_ref", "environment_provider_version", "environment_provider_digest", "environment_requirement_digest", "environment_configuration_digest", "environment_backend_kind", "environment_source_class", "environment_isolation_class", "environment_capability_refs", "environment_conformance_ref", "environment_conformance_digest", "environment_binding_digest"}
TOPOLOGY_METADATA_FIELDS = {"kind", "harness_digest", "parallelism", "code_model_step_refs", "fallback_policy_ref", "stop_condition_refs", "context_refresh_policy_ref", "memory_policy_ref", "sandbox_ref", "guardrail_ref", "threat_model_ref"}
EVALUATION_METADATA_FIELDS = {"lane_ref", "risk_class", "rubric_digest", "case_set_digest", "evaluator_policy_digest", "evaluator_ref", "rubric_ref", "evaluator_version", "evaluator_digest", "required_rungs", "required_deterministic_check_refs", "critical_dimensions", "human_acceptance_required", "outcome_horizon_ref", "outcome_horizon_not_before", "requirements"}

OBSERVATION_FIELDS = {
    "progress_state", "coverage_state", "activity_fidelity", "declared_refs_observed",
    "unmapped_activity_refs", "deviation_candidates", "uncertainty",
}
DEVIATION_CANDIDATE_FIELDS = {"candidate_id", "basis", "confidence", "requires_review"}
PROFILE_FIELDS = {"profile_id", "display_label"}
OBSERVATORY_PROJECTION_FIELDS = {
    "schema_version", "projection_digest", "generated_at", "work_request_id", "source_state", "declared_intent", "component_map", "attempt_lane",
    "state", "observed_movement", "timeline", "evidence_refs",
}
COMPONENT_FIELDS = {"component_ref", "depends_on_component_refs", "current"}
ATTEMPT_LANE_FIELDS = {"attempt_id", "persistent_profile", "actual_staffing"}
OBSERVATORY_STATE_FIELDS = {"lifecycle", "progress", "verification"}
TIMELINE_FIELDS = {"sequence", "occurred_at", "event_type", "declared_step_ref", "observed_resource_ref", "observed_component_ref", "tool_class", "state", "evidence_refs", "retention"}
SOURCE_STATE_FIELDS = {"state_version", "canonical_record_digest", "plan_revision_digest"}

TERMINAL_STATES = {"succeeded", "failed", "timed_out", "cancelled", "partial", "unknown"}
VERIFICATION_STATES = {"verified_success", "verified_failure", "partial", "unknown", "not_attempted"}
SURFACES = {"claude_desktop", "codex_desktop", "hermes_desktop", "grok_x_native"}


def _expect_exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContractError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ContractError(f"{label} is missing fields: {', '.join(missing)}")
    return value


def _string(value: Any, label: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    if identifier and not ID.fullmatch(value):
        raise ContractError(f"{label} must be an opaque identifier")
    return value


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ContractError(f"{label} must be an ISO UTC timestamp")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ContractError(f"{label} must be a sha256 digest")
    return value


def _list_of_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ContractError(f"{label} must be a list of non-empty strings")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContractError(f"{label} must be a non-negative integer")
    return value


def _finite_nonnegative(value: Any, label: str) -> float | int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ContractError(f"{label} must be a finite non-negative number")
    return value


def canonical_digest(value: Any) -> str:
    """Return a stable digest independent of JSON key ordering or whitespace."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError("canonical digest input must be JSON serializable") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_request(value: Any) -> dict[str, Any]:
    request = _expect_exact(value, REQUEST_FIELDS, "envelope request")
    _string(request["job_ref"], "envelope request job_ref", identifier=True)
    _digest(request["input_digest"], "envelope request input_digest")
    if request["data_class"] not in {"synthetic_only", "metadata_only"}:
        raise ContractError("envelope request data_class is not supported")
    if not isinstance(request["allowed_actions"], list) or any(
        action not in ENGINEERING_REPOSITORY_ACTIONS for action in request["allowed_actions"]
    ) or len(set(request["allowed_actions"])) != len(request["allowed_actions"]):
        raise ContractError("execution envelope contains an unsupported or duplicate allowed action")
    declared = _expect_exact(request["declared_expectations"], DECLARED_EXPECTATION_FIELDS,
                              "envelope declared_expectations")
    for field in DECLARED_EXPECTATION_FIELDS - {"component_dependencies"}:
        _list_of_strings(declared[field], f"envelope declared_expectations {field}")
    if not isinstance(declared["component_dependencies"], list):
        raise ContractError("envelope declared_expectations component_dependencies must be a list")
    for edge in declared["component_dependencies"]:
        pair = _expect_exact(edge, COMPONENT_DEPENDENCY_FIELDS, "envelope component dependency")
        for field in COMPONENT_DEPENDENCY_FIELDS:
            _string(pair[field], f"envelope component dependency {field}")
    return request


def _validate_adapter(value: Any) -> dict[str, Any]:
    adapter = _expect_exact(value, ADAPTER_FIELDS, "adapter")
    if adapter["surface"] not in SURFACES:
        raise ContractError("adapter surface is not a supported v1 surface")
    for field in ADAPTER_FIELDS - {"surface", "configuration_fingerprint"}:
        _string(adapter[field], f"adapter {field}", identifier=(field == "native_session_ref"))
    _digest(adapter["configuration_fingerprint"], "adapter configuration_fingerprint")
    return adapter


def _validate_server_binding(value: Any) -> dict[str, Any]:
    binding = _expect_exact(value, SERVER_BINDING_FIELDS, "server_binding")
    identity = _expect_exact(binding["identity"], IDENTITY_FIELDS, "server_binding identity")
    for field in IDENTITY_FIELDS - {"personal_rule_count", "derived_by", "client_mutable"}:
        _string(identity[field], f"server_binding identity {field}", identifier=True)
    _nonnegative_int(identity["personal_rule_count"], "server_binding identity personal_rule_count")
    if identity["derived_by"] != "server_identity_resolution" or identity["client_mutable"] is not False:
        raise ContractError("identity must be server-derived and client immutable")
    authority = _expect_exact(binding["authority"], AUTHORITY_FIELDS, "server_binding authority")
    if authority["environment"] not in {"local", "rehearsal", "staging", "production"}:
        raise ContractError("server_binding authority environment is invalid")
    if authority["risk_class"] not in {f"R{i}" for i in range(7)}:
        raise ContractError("server_binding authority risk_class is invalid")
    for field in ("capability_profile", "capability_grant_ref"):
        _string(authority[field], f"server_binding authority {field}", identifier=True)
    if not isinstance(authority["read_only"], bool):
        raise ContractError("execution envelope authority read_only must be boolean")
    if authority["derived_by"] != "server_capability_resolution" or authority["client_mutable"] is not False:
        raise ContractError("authority must be server-derived and client immutable")
    _validate_adapter(binding["adapter"])
    return binding


def _validate_state_binding(value: Any) -> dict[str, Any]:
    binding = _expect_exact(value, STATE_BINDING_FIELDS, "state_binding")
    if not isinstance(binding["state_version"], int) or isinstance(binding["state_version"], bool) or binding["state_version"] < 1:
        raise ContractError("state_binding state_version must be a positive integer")
    _digest(binding["canonical_record_digest"], "state_binding canonical_record_digest")
    if binding["compare_and_swap_required"] is not True:
        raise ContractError("state_binding requires compare_and_swap_required")
    if not isinstance(binding["accepted_resource_revisions"], list):
        raise ContractError("state_binding accepted_resource_revisions must be a list")
    for row in binding["accepted_resource_revisions"]:
        resource = _expect_exact(row, RESOURCE_REVISION_FIELDS, "accepted resource revision")
        _string(resource["resource_ref"], "accepted resource revision resource_ref", identifier=True)
        _string(resource["revision_ref"], "accepted resource revision revision_ref", identifier=True)
        _digest(resource["digest"], "accepted resource revision digest")
    return binding


def _validate_phase_binding(value: Any) -> dict[str, Any]:
    phase = _expect_exact(value, PHASE_BINDING_FIELDS, "phase_binding")
    _string(phase["phase_id"], "phase_binding phase_id", identifier=True)
    if phase["session_affinity"] not in {"same_native_session_required", "same_native_session_preferred", "fresh_native_session_required"}:
        raise ContractError("phase_binding session_affinity is invalid")
    if not isinstance(phase["switch_conditions"], list) or not phase["switch_conditions"]:
        raise ContractError("phase_binding switch_conditions must be a non-empty list")
    allowed = {"verified_checkpoint", "native_session_unavailable", "phase_boundary", "capability_expired"}
    if not set(phase["switch_conditions"]).issubset(allowed) or "verified_checkpoint" not in phase["switch_conditions"]:
        raise ContractError("phase_binding only permits a switch at a verified_checkpoint")
    if phase["native_session_transfer"] != "semantic_state_only":
        raise ContractError("phase_binding cannot promise portable native session internals")
    return phase


def _validate_evaluation_context(value: Any) -> dict[str, Any]:
    context = _expect_exact(value, EVALUATION_CONTEXT_FIELDS, "evaluation_context")
    if context["experiment_arm"] not in {
        "fixed_native_pair", "same_pair_audited_state", "audited_state_routed_executors", "diverse_auditor",
    }:
        raise ContractError("evaluation_context experiment_arm is invalid")
    if context["auditor_mode"] not in {"none", "same_pair_auditor", "diverse_read_only_auditor"}:
        raise ContractError("evaluation_context auditor_mode is invalid")
    _string(context["evaluation_kernel_ref"], "evaluation_context evaluation_kernel_ref", identifier=True)
    _digest(context["workflow_rubric_digest"], "evaluation_context workflow_rubric_digest")
    _digest(context["case_set_digest"], "evaluation_context case_set_digest")
    return context


def _validate_versioned_metadata(value: Any, label: str) -> dict[str, Any]:
    extra = RUNTIME_METADATA_OPTIONAL_FIELDS if label == "execution runtime_profile" else (TOPOLOGY_METADATA_FIELDS if label == "execution execution_topology" else (EVALUATION_METADATA_FIELDS if label == "execution evaluation_plan" else set()))
    allowed = VERSIONED_METADATA_FIELDS | extra
    if not isinstance(value, dict) or set(value) - allowed:
        raise ContractError(f"{label} has unknown fields")
    row = _expect_exact({key: value[key] for key in VERSIONED_METADATA_FIELDS}, VERSIONED_METADATA_FIELDS, label)
    _string(row["ref"], f"{label} ref", identifier=True)
    _digest(row["digest"], f"{label} digest")
    if label == "execution runtime_profile":
        for field in ("profile_key", "provider_id", "model_id", "desk", "policy_ref", "modality", "reasoning_effort_ref", "sampling_profile_ref", "cache_policy_ref", "knowledge_cutoff_posture", "tool_calling_mode"):
            if field in value: _string(value[field], f"{label} {field}")
        if "profile_version" in value and (not isinstance(value["profile_version"], int) or value["profile_version"] < 1):
            raise ContractError("execution runtime_profile profile_version must be positive")
        if "policy_digest" in value: _digest(value["policy_digest"], "execution runtime_profile policy_digest")
        environment_fields = {
            "environment_provider_ref", "environment_provider_version", "environment_provider_digest",
            "environment_requirement_digest", "environment_configuration_digest", "environment_backend_kind",
            "environment_source_class", "environment_isolation_class", "environment_capability_refs",
            "environment_conformance_ref", "environment_conformance_digest", "environment_binding_digest",
        }
        present = environment_fields & set(value)
        if present and present != environment_fields:
            raise ContractError("execution runtime_profile environment binding must be complete")
        if present:
            import execution_environment
            execution_environment.validate_environment_binding({
                "provider_ref": value["environment_provider_ref"],
                "provider_version": value["environment_provider_version"],
                "provider_digest": value["environment_provider_digest"],
                "requirement_digest": value["environment_requirement_digest"],
                "configuration_digest": value["environment_configuration_digest"],
                "backend_kind": value["environment_backend_kind"],
                "source_class": value["environment_source_class"],
                "isolation_class": value["environment_isolation_class"],
                "capability_refs": value["environment_capability_refs"],
                "conformance_ref": value["environment_conformance_ref"],
                "conformance_digest": value["environment_conformance_digest"],
                "binding_digest": value["environment_binding_digest"],
            })
    return row


def validate_execution_envelope(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ContractError("execution envelope must be an object")
    unknown = sorted(set(envelope) - (ENVELOPE_FIELDS | ENVELOPE_OPTIONAL_FIELDS))
    if unknown:
        raise ContractError(f"execution envelope has unknown fields: {', '.join(unknown)}")
    value = _expect_exact({key: envelope[key] for key in ENVELOPE_FIELDS}, ENVELOPE_FIELDS, "execution envelope")
    value.update({key: envelope[key] for key in ENVELOPE_OPTIONAL_FIELDS if key in envelope})
    if value["schema_version"] != "execution-envelope.v1":
        raise ContractError("unsupported execution envelope schema_version")
    for field in ("envelope_id", "work_request_id"):
        _string(value[field], f"execution envelope {field}", identifier=True)
    raw_plan = value["plan_revision"]
    if not isinstance(raw_plan, dict):
        raise ContractError("plan_revision must be an object")
    unknown_plan = sorted(set(raw_plan) - (PLAN_FIELDS | PLAN_OPTIONAL_FIELDS))
    if unknown_plan:
        raise ContractError(f"plan_revision has unknown fields: {', '.join(unknown_plan)}")
    plan = _expect_exact({key: raw_plan[key] for key in PLAN_FIELDS}, PLAN_FIELDS, "plan_revision")
    _string(plan["id"], "plan_revision id", identifier=True)
    if not isinstance(plan["revision"], int) or isinstance(plan["revision"], bool) or plan["revision"] < 1:
        raise ContractError("plan_revision revision must be a positive integer")
    _digest(plan["digest"], "plan_revision digest")
    if "context_binding" in raw_plan:
        import activation_reliability
        activation_reliability.validate_plan_context_binding(raw_plan["context_binding"])
    session = _expect_exact(value["agent_session"], SESSION_FIELDS, "agent_session")
    _string(session["id"], "agent_session id", identifier=True)
    _timestamp(session["lease_expires_at"], "agent_session lease_expires_at")
    _timestamp(value["issued_at"], "execution envelope issued_at")
    _timestamp(value["expires_at"], "execution envelope expires_at")
    _validate_state_binding(value["state_binding"])
    _validate_phase_binding(value["phase_binding"])
    _validate_evaluation_context(value["evaluation_context"])
    request = _validate_request(value["request"])
    server = _validate_server_binding(value["server_binding"])
    read_only = server["authority"]["read_only"]
    if read_only and request["allowed_actions"]:
        raise ContractError("read-only authority cannot carry allowed actions")
    if not read_only and not request["allowed_actions"]:
        raise ContractError("write authority requires at least one allowed action")
    if not read_only and server["authority"]["capability_profile"] != "capability:engineering-repository-write":
        raise ContractError("write authority requires the engineering repository capability profile")
    runtime_fields = ("runtime_profile", "execution_topology", "evaluation_plan")
    if any(field in envelope for field in runtime_fields):
        if not all(field in envelope for field in runtime_fields):
            raise ContractError("activation envelope requires runtime, topology, and evaluation bindings together")
        for field in runtime_fields:
            _validate_versioned_metadata(envelope[field], f"execution {field}")
    if "activation_binding" in envelope or "reliability_policy_binding" in envelope:
        import activation_reliability
        activation_reliability.validate_envelope_bindings(envelope)
        if not all(field in envelope for field in runtime_fields):
            raise ContractError("activated envelope requires exact runtime, topology, and evaluation bindings")
    handoff = _expect_exact(value["handoff"], HANDOFF_FIELDS | {"checkpoint_ref", "native_session_transfer"}, "handoff")
    if handoff["mode"] not in {"original", "replacement"}:
        raise ContractError("handoff mode is invalid")
    if handoff["capability_inherited"] is not False:
        raise ContractError("handoff capability_inherited must be false")
    replacement = handoff["replaces_agent_session_id"]
    if handoff["native_session_transfer"] != "semantic_state_only":
        raise ContractError("handoff cannot promise portable native session internals")
    if handoff["mode"] == "original" and (replacement is not None or handoff["checkpoint_ref"] is not None):
        raise ContractError("original envelope cannot name a replacement session or checkpoint")
    if handoff["mode"] == "replacement":
        _string(replacement, "handoff replaces_agent_session_id", identifier=True)
        _string(handoff["checkpoint_ref"], "handoff checkpoint_ref", identifier=True)
        if replacement == session["id"]:
            raise ContractError("replacement envelope must use a new agent session")
    return value


def execution_envelope_digest(envelope: Any) -> str:
    return canonical_digest(validate_execution_envelope(envelope))


def _validate_observation(value: Any) -> dict[str, Any]:
    observation = _expect_exact(value, OBSERVATION_FIELDS, "receipt observation")
    if observation["progress_state"] not in {
        "active", "quiet", "stale", "blocked", "failed", "unknown", "verified_complete",
    }:
        raise ContractError("receipt observation progress_state is invalid")
    if observation["coverage_state"] not in {"covered", "partial", "none_observed", "unknown", "not_applicable"}:
        raise ContractError("receipt observation coverage_state is invalid")
    if observation["activity_fidelity"] not in {"native_hook", "filesystem", "mixed", "none"}:
        raise ContractError("receipt observation activity_fidelity is invalid")
    _list_of_strings(observation["declared_refs_observed"], "receipt observation declared_refs_observed")
    _list_of_strings(observation["unmapped_activity_refs"], "receipt observation unmapped_activity_refs")
    if not isinstance(observation["deviation_candidates"], list):
        raise ContractError("receipt observation deviation_candidates must be a list")
    for row in observation["deviation_candidates"]:
        candidate = _expect_exact(row, DEVIATION_CANDIDATE_FIELDS, "receipt deviation candidate")
        _string(candidate["candidate_id"], "receipt deviation candidate candidate_id", identifier=True)
        if not isinstance(candidate["basis"], list) or not candidate["basis"]:
            raise ContractError("receipt deviation candidate basis must be a non-empty list")
        allowed_basis = {"declared_binding_mismatch", "native_tool_event", "filesystem_change", "human_report"}
        if not set(candidate["basis"]).issubset(allowed_basis):
            raise ContractError("receipt deviation candidate basis is invalid")
        if set(candidate["basis"]) == {"filesystem_change"}:
            raise ContractError("filesystem movement alone cannot establish a deviation candidate")
        if candidate["confidence"] not in {"low", "medium", "high"} or candidate["requires_review"] is not True:
            raise ContractError("receipt deviation candidates remain uncertain and require review")
    if observation["uncertainty"] not in {"none", "native_event_gap", "filesystem_only", "stale_signal", "scope_ambiguous", "unknown"}:
        raise ContractError("receipt observation uncertainty is invalid")
    return observation


def validate_observatory_projection(projection: Any) -> dict[str, Any]:
    """Validate a complete, transcript-free projection before it reaches a viewer.

    This validates a read model only. It does not promote an executor claim or
    make the browser an authority source.
    """
    value = _expect_exact(projection, OBSERVATORY_PROJECTION_FIELDS, "observatory projection")
    if value["schema_version"] != "observatory-attempt-projection.v1":
        raise ContractError("unsupported observatory projection schema_version")
    _digest(value["projection_digest"], "observatory projection projection_digest")
    without_digest = {key: item for key, item in value.items() if key != "projection_digest"}
    if value["projection_digest"] != canonical_digest(without_digest):
        raise ContractError("observatory projection digest does not bind its exact content")
    _string(value["work_request_id"], "observatory projection work_request_id", identifier=True)
    _timestamp(value["generated_at"], "observatory projection generated_at")
    source_state = _expect_exact(value["source_state"], SOURCE_STATE_FIELDS, "observatory source_state")
    if not isinstance(source_state["state_version"], int) or isinstance(source_state["state_version"], bool) or source_state["state_version"] < 1:
        raise ContractError("observatory source_state state_version must be a positive integer")
    _digest(source_state["canonical_record_digest"], "observatory source_state canonical_record_digest")
    _digest(source_state["plan_revision_digest"], "observatory source_state plan_revision_digest")
    _validate_request({"job_ref": "job:projection", "input_digest": "sha256:" + "0" * 64,
                       "data_class": "metadata_only", "allowed_actions": [],
                       "declared_expectations": value["declared_intent"]})
    if not isinstance(value["component_map"], list):
        raise ContractError("observatory projection component_map must be a list")
    for component in value["component_map"]:
        row = _expect_exact(component, COMPONENT_FIELDS, "observatory component")
        _string(row["component_ref"], "observatory component component_ref")
        _list_of_strings(row["depends_on_component_refs"], "observatory component dependencies")
        if not isinstance(row["current"], bool):
            raise ContractError("observatory component current must be boolean")
    lane = _expect_exact(value["attempt_lane"], ATTEMPT_LANE_FIELDS, "observatory attempt lane")
    _string(lane["attempt_id"], "observatory attempt lane attempt_id", identifier=True)
    profile = _expect_exact(lane["persistent_profile"], PROFILE_FIELDS, "observatory profile")
    _string(profile["profile_id"], "observatory profile profile_id", identifier=True)
    _string(profile["display_label"], "observatory profile display_label")
    _validate_adapter(lane["actual_staffing"])
    state = _expect_exact(value["state"], OBSERVATORY_STATE_FIELDS, "observatory state")
    if state["lifecycle"] not in TERMINAL_STATES or state["progress"] not in {"active", "quiet", "stale", "blocked", "failed", "unknown", "verified_complete"} or state["verification"] not in VERIFICATION_STATES:
        raise ContractError("observatory projection state is invalid")
    _validate_observation(value["observed_movement"])
    if not isinstance(value["timeline"], list):
        raise ContractError("observatory projection timeline must be a list")
    previous_sequence = 0
    for item in value["timeline"]:
        event = _expect_exact(item, TIMELINE_FIELDS, "observatory timeline event")
        if not isinstance(event["sequence"], int) or event["sequence"] <= previous_sequence:
            raise ContractError("observatory projection timeline must be strictly sequence ordered")
        previous_sequence = event["sequence"]
        _timestamp(event["occurred_at"], "observatory timeline occurred_at")
        _string(event["event_type"], "observatory timeline event_type")
        for field in ("declared_step_ref", "observed_resource_ref", "observed_component_ref", "tool_class"):
            if event[field] is not None:
                _string(event[field], f"observatory timeline {field}")
        _string(event["state"], "observatory timeline state")
        _list_of_strings(event["evidence_refs"], "observatory timeline evidence_refs")
        if event["retention"] not in {"ephemeral", "material_redacted"}:
            raise ContractError("observatory timeline retention is invalid")
    _list_of_strings(value["evidence_refs"], "observatory projection evidence_refs")
    return value
