"""Offline Engineering Passport v1 contracts and deterministic projection.

The passport is a typed projection over an already accepted Work Request,
Plan Revision, ExecutionEnvelope and redacted receipts.  It is deliberately
not a task store, scheduler, transcript archive, or authority source.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable

import execution_contract as base


class EngineeringContractError(base.ContractError):
    """An engineering passport artifact failed its fail-closed contract."""


ID = base.ID
DIGEST = base.SHA256


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EngineeringContractError(f"{label} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise EngineeringContractError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise EngineeringContractError(f"{label} is missing fields: {', '.join(missing)}")
    return value


def _str(value: Any, label: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or (identifier and not ID.fullmatch(value)):
        raise EngineeringContractError(f"{label} must be a non-empty {'opaque identifier' if identifier else 'string'}")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise EngineeringContractError(f"{label} must be a sha256 digest")
    return value


def _posint(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EngineeringContractError(f"{label} must be a positive integer")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise EngineeringContractError(f"{label} must be boolean")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EngineeringContractError(f"{label} must be a list")
    return value


def _ids(value: Any, label: str) -> list[str]:
    rows = _list(value, label)
    for item in rows:
        _str(item, label, identifier=True)
    return rows


def _evidence(value: Any, label: str) -> list[dict[str, str]]:
    rows = _list(value, label)
    for raw in rows:
        row = _exact(raw, {"ref", "redaction_class", "content_digest"}, label + " item")
        _str(row["ref"], label + " ref", identifier=True)
        if row["redaction_class"] not in {"metadata_only", "redacted_evidence"}:
            raise EngineeringContractError(f"{label} redaction_class is invalid")
        _digest(row["content_digest"], label + " content_digest")
    return rows


def _binding(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"id", "state_version", "canonical_record_digest"}, label)
    _str(row["id"], label + " id", identifier=True)
    _posint(row["state_version"], label + " state_version")
    _digest(row["canonical_record_digest"], label + " canonical_record_digest")
    return row


def _plan_ref(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"id", "revision", "digest"}, label)
    _str(row["id"], label + " id", identifier=True)
    _posint(row["revision"], label + " revision")
    _digest(row["digest"], label + " digest")
    return row


SLICE_FIELDS = {
    "slice_ref", "ordinal", "objective", "definition_of_done", "dependency_refs",
    "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs",
    "baseline_evidence_refs", "planned_checks", "scope_boundary", "forbidden_change_refs",
    "concurrency_posture", "manual_qa_required", "risk_class", "release_requirement",
}
CHECK_FIELDS = {"check_ref", "failure_condition", "evidence_requirement"}

RISK_CLASSES = frozenset(f"R{index}" for index in range(7))
CONCURRENCY_POSTURES = frozenset({"parallel_safe", "serial_after_dependencies", "exclusive_resource"})
RELEASE_REQUIREMENTS = frozenset({"required", "not_required"})
EVIDENCE_REQUIREMENTS = frozenset({"redacted_evidence_required", "metadata_only_sufficient"})


def _validate_check(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, CHECK_FIELDS, label)
    _str(row["check_ref"], label + " check_ref", identifier=True)
    _str(row["failure_condition"], label + " failure_condition")
    if row["evidence_requirement"] not in EVIDENCE_REQUIREMENTS:
        raise EngineeringContractError(f"{label} evidence_requirement is invalid")
    return row


# --- V5-F03 deep-module execution contract -----------------------------------
#
# engineering-slice-plan.v1 remains exactly as accepted: the same closed field
# set, the same refusals and the same canonical digest.  The Q046.D1 machine
# readable slice contract, the Q016.D1/Q029.D1 code-versus-model bindings and
# the Q035.D1 design-depth classifier arrive as the explicit successor
# engineering-slice-plan.v2, so no established v1 producer is silently
# reinterpreted and no second slice-contract authority is created.

SLICE_PLAN_V1 = "engineering-slice-plan.v1"
SLICE_PLAN_V2 = "engineering-slice-plan.v2"
ENGINEERING_SLICE_PLAN_VERSIONS = (SLICE_PLAN_V1, SLICE_PLAN_V2)
DESIGN_CONTRACT_VERSION = "engineering-design-contract.v1"

# Q035.D1 classifier inputs.  These are the only slice facts the classifier may
# read.  planned_checks is deliberately absent: every declared check stays
# mandatory and adding verification can never move a slice from SHORT to FULL.
# Free text (objective, definition_of_done, scope_boundary, failure conditions)
# is never parsed, because interpreting prose would recreate model judgment.
DESIGN_DEPTH_INPUT_FIELDS = (
    "risk_class", "concurrency_posture", "manual_qa_required", "release_requirement",
    "dependency_refs", "declared_resource_refs", "declared_component_refs",
    "declared_plan_step_refs",
)
SHORT_RISK_CLASSES = frozenset({"R0", "R1", "R2", "R3"})


def _short_predicate_v1(row: dict[str, Any]) -> bool:
    return (
        row["risk_class"] in SHORT_RISK_CLASSES
        and row["concurrency_posture"] == "parallel_safe"
        and row["manual_qa_required"] is False
        and row["release_requirement"] == "not_required"
        and row["dependency_count"] == 0
        and row["declared_resource_count"] <= 1
        and row["declared_component_count"] <= 1
        and row["declared_plan_step_count"] <= 1
    )


# The accepted Q035.D1 predicate is frozen to the design-contract version that
# sealed it.  A registered plan is immutable and is revalidated on every read,
# so a predicate that quietly changed underneath a stored contract would
# reclassify already sealed work and make its passport unreadable with nothing
# able to amend the row.  A different boundary ships as an explicit successor
# contract version with its own entry here; the v1 entry never moves.
DESIGN_DEPTH_PREDICATES = {DESIGN_CONTRACT_VERSION: _short_predicate_v1}
DESIGN_DEPTH_PREDICATE_VERSIONS = tuple(DESIGN_DEPTH_PREDICATES)

# The agent may never hand the classifier its own answer.  The closed field set
# already refuses unknown keys; this named refusal makes the bypass explicit.
SELF_LABEL_FIELDS = frozenset({
    "design_depth", "depth", "template", "template_kind", "complexity", "complexity_class",
    "simple", "is_simple", "classification", "classifier_override", "bypass",
})

DESIGN_CONTRACT_FIELDS = {
    "contract_version", "rationale", "dependency_rationale", "code_model_decision", "routing",
    "authority", "isolation", "tests", "review", "failure", "evidence", "deployment",
    "completion", "seam_decision", "full_design_refs", "short_template",
}
MODEL_STEP_FIELDS = {
    "step_ref", "responsibility_class", "input_contract_ref", "output_contract_ref",
    "rationale", "selection_basis",
}
FULL_DESIGN_REF_FIELDS = {
    "design_interview_ref", "authority_envelope_ref", "failure_model_ref", "fixture_refs",
    "oracle_ref",
}
SHORT_TEMPLATE_FIELDS = {"template_ref", "objective_summary", "verification_ref"}

# Q016.D1: deterministic code owns these outright; a model judgment step that
# claims one of them is refused rather than reviewed.
RESERVED_CODE_RESPONSIBILITIES = frozenset({
    "identity", "policy", "permissions", "state", "validation", "idempotency", "execution",
})
TYPED_UNCERTAINTY_CLASSES = frozenset({
    "classification", "extraction", "summarization", "ranking", "drafting", "disambiguation",
})
# Q029.D1: cost may appear alongside a capability reason but never alone.
SELECTION_BASIS_VALUES = frozenset({
    "typed_uncertainty", "capability_gain", "quality_gain", "adaptability_gain", "cost",
})
EXECUTOR_CLASSES = frozenset({"deterministic_code", "attended_human", "model_assisted"})
# A slice packet narrows one server-issued adapter envelope, so a contract that
# routes the work to an attended human names an executor no packet can carry.
ADMISSIBLE_EXECUTOR_CLASSES = frozenset({"deterministic_code", "model_assisted"})
AUTHORITY_ENVIRONMENTS = frozenset({"local", "rehearsal", "staging", "production"})
VERIFICATION_LANES = frozenset({"unit", "contract", "integration", "manual_qa"})
# The only reviewer provider at this seam records one independent automation
# actor's typed fact, and nothing anywhere checks that a reviewer is a human.
# Accepting independent_human would seal a requirement into an immutable plan
# that no code can satisfy or refuse, so the unsupported class is refused until
# a human-review provider exists.
REVIEWER_CLASSES = frozenset({"independent_agent"})
EVIDENCE_REDACTION_CLASSES = frozenset({"metadata_only", "redacted_evidence"})
EVIDENCE_RETENTIONS = frozenset({"ephemeral", "material_redacted"})
COMPLETION_VERIFIERS = frozenset({"independent_review", "independent_review_and_manual_qa"})
# Q063.D1 / Q122.D1: extend a proven deep module, or replace it cleanly; a new
# module needs a real seam and no plan may create two owners for one seam.
SEAM_MODES = frozenset({"reuse", "extend", "replace", "new_module"})
NEW_MODULE_JUSTIFICATIONS = frozenset({"authority", "lifecycle", "failure_isolation", "multi_adapter"})
MEASUREMENT_BASES = frozenset({
    "complexity_reduction", "defect_rate", "coverage", "latency", "operator_effort",
})


def _refuse_self_label(value: Any, label: str) -> None:
    if isinstance(value, dict):
        found = sorted(set(value) & SELF_LABEL_FIELDS)
        if found:
            raise EngineeringContractError(
                f"{label} cannot self-label design depth or a bypass: {', '.join(found)}")


def _unique_ids(value: Any, label: str) -> list[str]:
    rows = _ids(value, label)
    if len(set(rows)) != len(rows):
        raise EngineeringContractError(f"{label} contains duplicate references")
    return rows


def design_depth_inputs(slice_row: Any) -> dict[str, Any]:
    """Return the exact bound Q035.D1 classifier inputs for one accepted slice.

    Cardinality is the only thing read from the counted closed arrays, and the
    planned check count is never returned, so extra verification can never be
    penalised by the classifier.
    """
    if not isinstance(slice_row, dict):
        raise EngineeringContractError("design depth inputs require an accepted slice object")
    _refuse_self_label(slice_row, "design depth input")
    missing = sorted(set(DESIGN_DEPTH_INPUT_FIELDS) - set(slice_row))
    if missing:
        raise EngineeringContractError(
            f"design depth inputs are missing fields: {', '.join(missing)}")
    if slice_row["risk_class"] not in RISK_CLASSES:
        raise EngineeringContractError("design depth input risk_class is invalid")
    if slice_row["concurrency_posture"] not in CONCURRENCY_POSTURES:
        raise EngineeringContractError("design depth input concurrency_posture is invalid")
    _bool(slice_row["manual_qa_required"], "design depth input manual_qa_required")
    if slice_row["release_requirement"] not in RELEASE_REQUIREMENTS:
        raise EngineeringContractError("design depth input release_requirement is invalid")
    counted = {}
    for field in ("dependency_refs", "declared_resource_refs", "declared_component_refs",
                  "declared_plan_step_refs"):
        counted[field] = _ids(slice_row[field], "design depth input " + field)
    return {
        "risk_class": slice_row["risk_class"],
        "concurrency_posture": slice_row["concurrency_posture"],
        "manual_qa_required": slice_row["manual_qa_required"],
        "release_requirement": slice_row["release_requirement"],
        "dependency_count": len(counted["dependency_refs"]),
        "declared_resource_count": len(counted["declared_resource_refs"]),
        "declared_component_count": len(counted["declared_component_refs"]),
        "declared_plan_step_count": len(counted["declared_plan_step_refs"]),
    }


def classify_design_depth(slice_row: Any, contract_version: str = DESIGN_CONTRACT_VERSION) -> str:
    """Deterministic Q035.D1 design-depth classifier.

    SHORT requires every accepted condition: R0-R3, parallel-safe, no manual QA,
    no release requirement, zero dependencies, and at most one declared
    resource, component and plan step.  Every other valid combination is FULL,
    and an invalid slice never reaches this function at all.

    SHORT changes design-template depth only.  It grants no action authority,
    waives no R0-R6 operating gate, reduces no verification and activates no
    effect; the ordinary attended source route is decided by real effects, not
    by this classifier.

    The predicate is selected by the slice's own sealed contract_version, so a
    stored contract keeps the exact predicate it was accepted under.
    """
    row = design_depth_inputs(slice_row)
    predicate = DESIGN_DEPTH_PREDICATES.get(contract_version)
    if predicate is None:
        raise EngineeringContractError(
            f"design depth has no frozen predicate for contract version {contract_version!r}")
    return "short" if predicate(row) else "full"


def _validate_selection_basis(value: Any, label: str) -> list[str]:
    rows = _list(value, label)
    if not rows:
        raise EngineeringContractError(f"{label} must record why behavior is code or model work")
    for item in rows:
        if not isinstance(item, str):
            raise EngineeringContractError(f"{label} must be a list of strings")
    if len(set(rows)) != len(rows):
        raise EngineeringContractError(f"{label} contains duplicate entries")
    unknown = sorted(set(rows) - SELECTION_BASIS_VALUES)
    if unknown:
        raise EngineeringContractError(f"{label} has unknown entries: {', '.join(unknown)}")
    if set(rows) == {"cost"}:
        raise EngineeringContractError(
            f"{label} cannot rest on cost alone; price never justifies degraded capability or quality")
    return rows


def _validate_code_model_decision(value: Any, slice_row: dict[str, Any], label: str) -> dict[str, Any]:
    row = _exact(value, {"rationale", "selection_basis", "model_judgment_steps"}, label)
    _str(row["rationale"], label + " rationale")
    _validate_selection_basis(row["selection_basis"], label + " selection_basis")
    steps = _list(row["model_judgment_steps"], label + " model_judgment_steps")
    declared = set(slice_row["declared_plan_step_refs"])
    seen: set[str] = set()
    for index, raw in enumerate(steps):
        step_label = f"{label} model_judgment_steps[{index}]"
        step = _exact(raw, MODEL_STEP_FIELDS, step_label)
        _str(step["step_ref"], step_label + " step_ref", identifier=True)
        if step["step_ref"] in seen:
            raise EngineeringContractError(f"{step_label} duplicates a model judgment step")
        seen.add(step["step_ref"])
        if step["step_ref"] not in declared:
            raise EngineeringContractError(
                f"{step_label} is not a declared plan step of this slice")
        if step["responsibility_class"] in RESERVED_CODE_RESPONSIBILITIES:
            raise EngineeringContractError(
                f"{step_label} assigns {step['responsibility_class']} to model judgment; deterministic "
                "code owns identity, policy, permissions, state, validation, idempotency and execution")
        if step["responsibility_class"] not in TYPED_UNCERTAINTY_CLASSES:
            raise EngineeringContractError(f"{step_label} is not a typed uncertainty seam")
        for field in ("input_contract_ref", "output_contract_ref"):
            _str(step[field], f"{step_label} {field}", identifier=True)
        _str(step["rationale"], step_label + " rationale")
        _validate_selection_basis(step["selection_basis"], step_label + " selection_basis")
    return row


def _validate_design_routing(value: Any, steps: list[Any], label: str) -> dict[str, Any]:
    row = _exact(value, {"executor_class", "adapter_ref", "fresh_session_required"}, label)
    if row["executor_class"] not in EXECUTOR_CLASSES:
        raise EngineeringContractError(f"{label} executor_class is invalid")
    _str(row["adapter_ref"], label + " adapter_ref", identifier=True)
    if row["fresh_session_required"] is not True:
        raise EngineeringContractError(f"{label} requires a fresh native session")
    if row["executor_class"] == "deterministic_code" and steps:
        raise EngineeringContractError(f"{label} deterministic_code route cannot carry model judgment steps")
    if row["executor_class"] == "model_assisted" and not steps:
        raise EngineeringContractError(f"{label} model_assisted route must declare a typed model judgment step")
    return row


def _validate_design_authority(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"capability_profile", "read_only", "environment"}, label)
    _str(row["capability_profile"], label + " capability_profile", identifier=True)
    _bool(row["read_only"], label + " read_only")
    if row["environment"] not in AUTHORITY_ENVIRONMENTS:
        raise EngineeringContractError(f"{label} environment is invalid")
    if not row["read_only"] and row["capability_profile"] != "capability:engineering-repository-write":
        raise EngineeringContractError(
            f"{label} write authority requires the engineering repository capability profile")
    return row


def _validate_design_isolation(value: Any, slice_row: dict[str, Any], label: str) -> dict[str, Any]:
    row = _exact(value, {"worktree_required", "branch_required", "shared_resource_refs"}, label)
    for field in ("worktree_required", "branch_required"):
        if row[field] is not True:
            raise EngineeringContractError(f"{label} requires an isolated worktree and branch")
    shared = _unique_ids(row["shared_resource_refs"], label + " shared_resource_refs")
    if not set(shared).issubset(set(slice_row["declared_resource_refs"])):
        raise EngineeringContractError(f"{label} names a resource the slice never declared")
    if shared and slice_row["concurrency_posture"] == "parallel_safe":
        raise EngineeringContractError(f"{label} shared resources contradict a parallel_safe posture")
    return row


def _validate_design_tests(value: Any, slice_row: dict[str, Any], label: str) -> dict[str, Any]:
    row = _exact(value, {"planned_check_refs", "verification_lanes"}, label)
    refs = _unique_ids(row["planned_check_refs"], label + " planned_check_refs")
    if refs != [check["check_ref"] for check in slice_row["planned_checks"]]:
        raise EngineeringContractError(f"{label} must bind exactly the accepted planned checks")
    lanes = _list(row["verification_lanes"], label + " verification_lanes")
    if not lanes or len(set(lanes)) != len(lanes) or not set(lanes).issubset(VERIFICATION_LANES):
        raise EngineeringContractError(f"{label} verification_lanes is invalid")
    if ("manual_qa" in lanes) is not slice_row["manual_qa_required"]:
        raise EngineeringContractError(f"{label} manual QA lane must match the accepted slice")
    return row


def _validate_design_review(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"independent_review_required", "reviewer_class"}, label)
    if row["independent_review_required"] is not True:
        raise EngineeringContractError(f"{label} requires independent review of every slice")
    if row["reviewer_class"] not in REVIEWER_CLASSES:
        raise EngineeringContractError(f"{label} reviewer_class is invalid")
    return row


def _validate_design_failure(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"failure_modes"}, label)
    modes = _list(row["failure_modes"], label + " failure_modes")
    if not modes:
        raise EngineeringContractError(f"{label} must model at least one failure mode")
    seen: set[str] = set()
    for index, raw in enumerate(modes):
        mode_label = f"{label} failure_modes[{index}]"
        mode = _exact(raw, {"failure_ref", "detection", "compensation"}, mode_label)
        _str(mode["failure_ref"], mode_label + " failure_ref", identifier=True)
        if mode["failure_ref"] in seen:
            raise EngineeringContractError(f"{mode_label} duplicates a failure mode")
        seen.add(mode["failure_ref"])
        for field in ("detection", "compensation"):
            _str(mode[field], f"{mode_label} {field}")
    return row


def _validate_design_evidence(value: Any, slice_row: dict[str, Any], label: str) -> dict[str, Any]:
    row = _exact(value, {"redaction_class", "retention", "evidence_refs"}, label)
    if row["redaction_class"] not in EVIDENCE_REDACTION_CLASSES:
        raise EngineeringContractError(f"{label} redaction_class is invalid")
    if row["retention"] not in EVIDENCE_RETENTIONS:
        raise EngineeringContractError(f"{label} retention is invalid")
    rows = _evidence(row["evidence_refs"], label + " evidence_refs")
    if any(item["redaction_class"] != row["redaction_class"] for item in rows):
        raise EngineeringContractError(f"{label} evidence does not match the declared redaction class")
    if any(check["evidence_requirement"] == "redacted_evidence_required"
           for check in slice_row["planned_checks"]) and row["redaction_class"] != "redacted_evidence":
        raise EngineeringContractError(
            f"{label} must retain redacted evidence because a planned check requires it")
    return row


def _validate_design_deployment(value: Any, slice_row: dict[str, Any], label: str) -> dict[str, Any]:
    row = _exact(value, {"release_requirement", "rollback_ref", "confirmation_required"}, label)
    if row["release_requirement"] != slice_row["release_requirement"]:
        raise EngineeringContractError(f"{label} release_requirement must match the accepted slice")
    if row["release_requirement"] == "required":
        _str(row["rollback_ref"], label + " rollback_ref", identifier=True)
    elif row["rollback_ref"] is not None:
        _str(row["rollback_ref"], label + " rollback_ref", identifier=True)
    _bool(row["confirmation_required"], label + " confirmation_required")
    if slice_row["risk_class"] not in {"R0", "R1"} and row["confirmation_required"] is not True:
        raise EngineeringContractError(
            f"{label} retains the canonical explicit confirmation gate above R1")
    return row


def _validate_design_completion(value: Any, slice_row: dict[str, Any], label: str) -> dict[str, Any]:
    row = _exact(value, {"completion_predicate", "verified_by"}, label)
    _str(row["completion_predicate"], label + " completion_predicate")
    if row["verified_by"] not in COMPLETION_VERIFIERS:
        raise EngineeringContractError(f"{label} verified_by is invalid")
    expected = "independent_review_and_manual_qa" if slice_row["manual_qa_required"] else "independent_review"
    if row["verified_by"] != expected:
        raise EngineeringContractError(f"{label} verification must match the accepted manual QA requirement")
    return row


def _validate_seam_decision(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {
        "mode", "target_seam_ref", "measurement", "new_module_justification",
        "replaced_seam_refs", "residual_authority_refs",
    }, label)
    if row["mode"] not in SEAM_MODES:
        raise EngineeringContractError(f"{label} mode is invalid")
    _str(row["target_seam_ref"], label + " target_seam_ref", identifier=True)
    measurement = _exact(row["measurement"], {"basis", "note"}, label + " measurement")
    if measurement["basis"] not in MEASUREMENT_BASES:
        raise EngineeringContractError(f"{label} measurement basis is invalid")
    _str(measurement["note"], label + " measurement note")
    replaced = _unique_ids(row["replaced_seam_refs"], label + " replaced_seam_refs")
    residual = _unique_ids(row["residual_authority_refs"], label + " residual_authority_refs")
    if row["mode"] == "new_module":
        if row["new_module_justification"] not in NEW_MODULE_JUSTIFICATIONS:
            raise EngineeringContractError(
                f"{label} creates a module without a real authority, lifecycle, failure-isolation or multi-adapter seam")
    elif row["new_module_justification"] is not None:
        raise EngineeringContractError(f"{label} only a new module may carry a module justification")
    if row["mode"] == "replace":
        if not replaced:
            raise EngineeringContractError(f"{label} replacement must name the seam it retires")
        if row["target_seam_ref"] in replaced:
            raise EngineeringContractError(f"{label} seam cannot replace itself")
        if residual:
            raise EngineeringContractError(
                f"{label} replacement leaves residual authority; half-replacement is refused")
    else:
        if replaced:
            raise EngineeringContractError(f"{label} only a replacement may retire a seam")
        if residual:
            raise EngineeringContractError(
                f"{label} residual authority would create a duplicate authority")
    return row


def _validate_design_depth_material(contract: dict[str, Any], depth: str, label: str) -> None:
    if depth == "full":
        if contract["short_template"] is not None:
            raise EngineeringContractError(
                f"{label} complex or high-risk work cannot use the shorter governed template")
        refs = _exact(contract["full_design_refs"], FULL_DESIGN_REF_FIELDS, label + " full_design_refs")
        for field in ("design_interview_ref", "authority_envelope_ref", "failure_model_ref", "oracle_ref"):
            _str(refs[field], f"{label} full_design_refs {field}", identifier=True)
        if not _unique_ids(refs["fixture_refs"], label + " full_design_refs fixture_refs"):
            raise EngineeringContractError(f"{label} full design depth requires a fixture reference")
        return
    if contract["full_design_refs"] is not None:
        raise EngineeringContractError(
            f"{label} simple work uses the shorter governed template, not the full design envelope")
    template = _exact(contract["short_template"], SHORT_TEMPLATE_FIELDS, label + " short_template")
    for field in ("template_ref", "verification_ref"):
        _str(template[field], f"{label} short_template {field}", identifier=True)
    _str(template["objective_summary"], label + " short_template objective_summary")


def _validate_design_contract(value: Any, slice_row: dict[str, Any], label: str) -> dict[str, Any]:
    """Validate the closed Q046.D1 slice contract for one accepted v2 slice."""
    _refuse_self_label(value, label)
    row = _exact(value, DESIGN_CONTRACT_FIELDS, label)
    if row["contract_version"] != DESIGN_CONTRACT_VERSION:
        raise EngineeringContractError(f"{label} contract_version is unsupported")
    _str(row["rationale"], label + " rationale")
    _str(row["dependency_rationale"], label + " dependency_rationale")
    decision = _validate_code_model_decision(row["code_model_decision"], slice_row, label + " code_model_decision")
    _validate_design_routing(row["routing"], decision["model_judgment_steps"], label + " routing")
    _validate_design_authority(row["authority"], label + " authority")
    _validate_design_isolation(row["isolation"], slice_row, label + " isolation")
    _validate_design_tests(row["tests"], slice_row, label + " tests")
    _validate_design_review(row["review"], label + " review")
    _validate_design_failure(row["failure"], label + " failure")
    _validate_design_evidence(row["evidence"], slice_row, label + " evidence")
    _validate_design_deployment(row["deployment"], slice_row, label + " deployment")
    _validate_design_completion(row["completion"], slice_row, label + " completion")
    _validate_seam_decision(row["seam_decision"], label + " seam_decision")
    _validate_design_depth_material(row, classify_design_depth(slice_row, row["contract_version"]), label)
    return row


def _assert_seam_authority(slices: Iterable[dict[str, Any]]) -> None:
    """Refuse duplicate seam authority and half-replacement across one plan."""
    owners: dict[str, str] = {}
    retired: dict[str, str] = {}
    for row in slices:
        seam = row["design_contract"]["seam_decision"]
        target = seam["target_seam_ref"]
        if seam["mode"] in {"new_module", "replace"}:
            if target in owners:
                raise EngineeringContractError(
                    f"seam {target} already has an authority owner in slice {owners[target]}; "
                    "a plan cannot create duplicate authorities")
            owners[target] = row["slice_ref"]
        for seam_ref in seam["replaced_seam_refs"]:
            if seam_ref in retired:
                raise EngineeringContractError(f"seam {seam_ref} is retired more than once")
            retired[seam_ref] = row["slice_ref"]
    for row in slices:
        seam = row["design_contract"]["seam_decision"]
        target = seam["target_seam_ref"]
        if seam["mode"] in {"reuse", "extend"} and target in retired:
            raise EngineeringContractError(
                f"slice {row['slice_ref']} builds on seam {target} that slice {retired[target]} retires; "
                "half-replacement is refused")


def _transitive_dependencies(slices: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """Every slice a slice depends on, directly or transitively."""
    direct = {row["slice_ref"]: list(row["dependency_refs"]) for row in slices}
    resolved: dict[str, set[str]] = {}

    def visit(ref: str, stack: set[str]) -> set[str]:
        if ref in resolved:
            return resolved[ref]
        if ref in stack:
            return set()
        stack.add(ref)
        closure: set[str] = set()
        for dependency in direct.get(ref, ()):
            closure.add(dependency)
            closure |= visit(dependency, stack)
        stack.discard(ref)
        resolved[ref] = closure
        return closure

    for ref in direct:
        visit(ref, set())
    return resolved


def _assert_parallel_resource_isolation(slices: Iterable[dict[str, Any]]) -> None:
    """Refuse two concurrently admissible slices that own one declared resource.

    declared_resource_refs are the mutable resources the envelope binds by
    revision under compare_and_swap_required, and a parallel_safe slice has
    already had to state isolation shared_resource_refs as empty -- "nothing I
    touch is shared".  Two such slices naming one resource are two contradictory
    statements inside one sealed plan, and both become eligible at once.  A
    dependency edge between them removes the contradiction, because the
    dependent slice cannot run until the other is verified complete, so ordered
    work is left alone.  Serial and exclusive postures, which is how contention
    is meant to be declared, are untouched, and so are v1 plans.
    """
    ordered = _transitive_dependencies(slices)
    owners: dict[str, list[str]] = {}
    for row in slices:
        if row["concurrency_posture"] != "parallel_safe":
            continue
        # declared_resource_refs is not required to be unique, and one slice
        # repeating its own resource is not contention with anyone.
        for resource_ref in dict.fromkeys(row["declared_resource_refs"]):
            for other in owners.get(resource_ref, []):
                if other in ordered.get(row["slice_ref"], set()) or row["slice_ref"] in ordered.get(other, set()):
                    continue
                raise EngineeringContractError(
                    f"slices {other} and {row['slice_ref']} are both parallel_safe and both own "
                    f"resource {resource_ref}; order them with a dependency, or declare the "
                    "contention with a serial_after_dependencies or exclusive_resource posture")
            owners.setdefault(resource_ref, []).append(row["slice_ref"])


def _assert_design_execution_binding(slice_row: dict[str, Any], envelope: dict[str, Any]) -> None:
    """Refuse a v2 slice whose accepted contract contradicts the server binding.

    routing and authority are validated at registration and sealed inside
    plan_digest, but the packet copied server_binding verbatim and never
    compared the two, so a slice whose accepted contract said read-only work on
    a human desk could be packaged against a write-capable automation envelope
    with both statements recorded as true.  This compares and refuses only: it
    never rewrites, widens, or narrows the server binding to match a contract.
    """
    contract = slice_row.get("design_contract")
    if not contract:
        return
    routing = contract["routing"]
    authority = contract["authority"]
    binding = envelope["server_binding"]
    if routing["executor_class"] not in ADMISSIBLE_EXECUTOR_CLASSES:
        raise EngineeringContractError(
            f"accepted slice {slice_row['slice_ref']} routes execution to {routing['executor_class']}, "
            "which this adapter packet seam cannot execute")
    if routing["adapter_ref"] != binding["adapter"]["adapter_id"]:
        raise EngineeringContractError(
            f"accepted slice {slice_row['slice_ref']} declares adapter {routing['adapter_ref']} but the "
            f"server envelope binds {binding['adapter']['adapter_id']}")
    for field in ("environment", "capability_profile", "read_only"):
        if authority[field] != binding["authority"][field]:
            raise EngineeringContractError(
                f"accepted slice {slice_row['slice_ref']} declares authority {field} "
                f"{authority[field]!r} but the server envelope binds {binding['authority'][field]!r}")


def _validate_slice(value: Any, label: str = "engineering slice", *, version: str = SLICE_PLAN_V1) -> dict[str, Any]:
    _refuse_self_label(value, label)
    fields = SLICE_FIELDS | {"design_contract"} if version == SLICE_PLAN_V2 else SLICE_FIELDS
    row = _exact(value, fields, label)
    _str(row["slice_ref"], label + " slice_ref", identifier=True)
    _posint(row["ordinal"], label + " ordinal")
    for field in ("objective", "definition_of_done", "scope_boundary"):
        _str(row[field], label + " " + field)
    for field in ("dependency_refs", "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs", "forbidden_change_refs"):
        _ids(row[field], label + " " + field)
    _evidence(row["baseline_evidence_refs"], label + " baseline_evidence_refs")
    checks = _list(row["planned_checks"], label + " planned_checks")
    if not checks:
        raise EngineeringContractError(f"{label} must plan at least one check before execution")
    check_refs: set[str] = set()
    for index, check in enumerate(checks):
        validated_check = _validate_check(check, f"{label} planned_checks[{index}]")
        if validated_check["check_ref"] in check_refs:
            raise EngineeringContractError(f"{label} has duplicate planned check refs")
        check_refs.add(validated_check["check_ref"])
    if row["concurrency_posture"] not in CONCURRENCY_POSTURES:
        raise EngineeringContractError(f"{label} concurrency_posture is invalid")
    _bool(row["manual_qa_required"], label + " manual_qa_required")
    if row["risk_class"] not in RISK_CLASSES:
        raise EngineeringContractError(f"{label} risk_class is invalid")
    if row["release_requirement"] not in RELEASE_REQUIREMENTS:
        raise EngineeringContractError(f"{label} release_requirement is invalid")
    if version == SLICE_PLAN_V2:
        _validate_design_contract(row["design_contract"], row, label + " design_contract")
    return row


def validate_engineering_slice_plan(plan: Any) -> dict[str, Any]:
    fields = {"schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slices"}
    value = _exact(plan, fields, "engineering slice plan")
    if value["schema_version"] not in ENGINEERING_SLICE_PLAN_VERSIONS:
        raise EngineeringContractError("unsupported engineering slice plan schema_version")
    version = value["schema_version"]
    _binding(value["work_request"], "engineering slice plan work_request")
    _plan_ref(value["accepted_plan_revision"], "engineering slice plan accepted_plan_revision")
    _digest(value["plan_digest"], "engineering slice plan plan_digest")
    slices = _list(value["slices"], "engineering slice plan slices")
    if not slices:
        raise EngineeringContractError("engineering slice plan slices cannot be empty")
    refs: set[str] = set()
    ordinals: set[int] = set()
    for index, raw in enumerate(slices):
        row = _validate_slice(raw, f"engineering slice plan slices[{index}]", version=version)
        if row["slice_ref"] in refs or row["ordinal"] in ordinals:
            raise EngineeringContractError("engineering slice plan has duplicate slice refs or ordinals")
        refs.add(row["slice_ref"]); ordinals.add(row["ordinal"])
    for row in slices:
        missing = set(row["dependency_refs"]) - refs
        if missing:
            raise EngineeringContractError(f"slice {row['slice_ref']} has missing dependencies: {', '.join(sorted(missing))}")
    _assert_acyclic(slices)
    if version == SLICE_PLAN_V2:
        _assert_seam_authority(slices)
        _assert_parallel_resource_isolation(slices)
    without_digest = {key: item for key, item in value.items() if key != "plan_digest"}
    if value["plan_digest"] != base.canonical_digest(without_digest):
        raise EngineeringContractError("engineering slice plan digest does not bind exact content")
    return value


def _validate_plan_envelope_binding(plan: dict[str, Any], envelope: dict[str, Any]) -> None:
    """Require the accepted plan and server envelope to describe one authority state."""
    if plan["work_request"]["id"] != envelope["work_request_id"]:
        raise EngineeringContractError("accepted plan work request does not match execution envelope")
    state = envelope["state_binding"]
    if plan["work_request"]["state_version"] != state["state_version"] or plan["work_request"]["canonical_record_digest"] != state["canonical_record_digest"]:
        raise EngineeringContractError("accepted plan state binding does not match execution envelope")
    if plan["accepted_plan_revision"] != envelope["plan_revision"]:
        raise EngineeringContractError("accepted plan revision does not match execution envelope")


def _validate_authoritative_envelopes(plan: dict[str, Any], values: Any) -> dict[str, dict[str, Any]]:
    """Validate and index server-issued envelopes persisted by a passport."""
    rows = _list(values, "engineering passport execution_envelopes")
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        envelope = base.validate_execution_envelope(raw)
        _validate_plan_envelope_binding(plan, envelope)
        for revision in envelope["state_binding"]["accepted_resource_revisions"]:
            _str(revision["resource_ref"], "accepted resource revision resource_ref", identifier=True)
            _str(revision["revision_ref"], "accepted resource revision revision_ref", identifier=True)
        digest = base.execution_envelope_digest(envelope)
        if digest in result:
            raise EngineeringContractError(f"engineering passport duplicates execution envelope digest at index {index}")
        result[digest] = envelope
    return result


def _receipt_envelope(receipt: dict[str, Any], envelopes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    envelope = envelopes.get(receipt["envelope_digest"])
    if envelope is None:
        raise EngineeringContractError("receipt envelope_digest does not resolve to one authoritative envelope")
    return envelope


def _assert_envelope_retention(receipts: list[dict[str, Any]], envelopes: dict[str, dict[str, Any]]) -> None:
    receipt_digests = [receipt["envelope_digest"] for receipt in receipts]
    if not receipts:
        if envelopes:
            raise EngineeringContractError("a pre-execution passport cannot retain unreferenced execution envelopes")
        return
    if set(receipt_digests) != set(envelopes) or len(set(receipt_digests)) == 0:
        raise EngineeringContractError("persisted execution envelopes must exactly equal receipt envelope digests")


def _assert_acyclic(slices: Iterable[dict[str, Any]]) -> None:
    graph = {row["slice_ref"]: set(row["dependency_refs"]) for row in slices}
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(ref: str) -> None:
        if ref in visiting:
            raise EngineeringContractError("engineering slice dependencies contain a cycle")
        if ref in visited:
            return
        visiting.add(ref)
        for dep in graph[ref]: visit(dep)
        visiting.remove(ref); visited.add(ref)
    for ref in graph: visit(ref)


RECEIPT_FIELDS = {
    "schema_version", "envelope_digest", "attempt_id", "slice_ref", "plan_digest", "attribution",
    "planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs",
    "checks", "outcome", "artifact_refs", "evidence_refs", "deviations", "source_evidence",
    "reset_reconstruction", "executor_claim", "independent_verification_required",
}


def _validate_receipt(value: Any, plan: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    row = _exact(value, RECEIPT_FIELDS, "engineering slice receipt")
    if row["schema_version"] != "engineering-slice-receipt.v1":
        raise EngineeringContractError("unsupported engineering slice receipt schema_version")
    _digest(row["envelope_digest"], "engineering receipt envelope_digest")
    _str(row["attempt_id"], "engineering receipt attempt_id", identifier=True)
    _str(row["slice_ref"], "engineering receipt slice_ref", identifier=True)
    if row["plan_digest"] != plan["plan_digest"]:
        raise EngineeringContractError("engineering receipt plan_digest does not match accepted plan")
    slices = {item["slice_ref"]: item for item in plan["slices"]}
    if row["slice_ref"] not in slices:
        raise EngineeringContractError("engineering receipt names an unknown slice")
    attribution = _exact(row["attribution"], {"actor_ref", "session_ref", "adapter_ref"}, "engineering receipt attribution")
    for field in attribution: _str(attribution[field], "engineering receipt attribution " + field, identifier=True)
    for field in ("planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "artifact_refs"):
        _ids(row[field], "engineering receipt " + field)
    checks = _list(row["checks"], "engineering receipt checks")
    planned = {check["check_ref"] for check in slices[row["slice_ref"]]["planned_checks"]}
    seen: set[str] = set()
    for index, raw in enumerate(checks):
        item = _exact(raw, {"check_ref", "state", "evidence_refs"}, f"engineering receipt checks[{index}]")
        _str(item["check_ref"], "engineering receipt check_ref", identifier=True)
        if item["check_ref"] not in planned: raise EngineeringContractError("receipt ran an unplanned check")
        if item["check_ref"] in seen: raise EngineeringContractError("receipt duplicates a check")
        seen.add(item["check_ref"])
        if item["state"] not in {"passed", "failed", "blocked", "not_run"}:
            raise EngineeringContractError("engineering receipt check state is invalid")
        evidence_rows = _evidence(item["evidence_refs"], "engineering receipt check evidence_refs")
        if item["state"] == "passed":
            if not evidence_rows:
                raise EngineeringContractError("passed check requires evidence")
            requirement = next(check["evidence_requirement"] for check in slices[row["slice_ref"]]["planned_checks"] if check["check_ref"] == item["check_ref"])
            required_class = "redacted_evidence" if requirement == "redacted_evidence_required" else "metadata_only"
            if not any(evidence["redaction_class"] == required_class for evidence in evidence_rows):
                raise EngineeringContractError(f"passed check requires {required_class}")
    if planned - seen:
        raise EngineeringContractError("receipt omits a planned check")
    if row["outcome"] not in {"claimed_complete", "failed", "blocked", "reopened"}:
        raise EngineeringContractError("engineering receipt outcome is invalid")
    _evidence(row["evidence_refs"], "engineering receipt evidence_refs")
    deviations = _list(row["deviations"], "engineering receipt deviations")
    deviation_refs: set[str] = set()
    for index, raw in enumerate(deviations):
        item = _exact(raw, {"deviation_ref", "category", "reason", "impact", "plan_revision_required", "evidence_refs", "out_of_scope_resource_refs", "out_of_scope_component_refs", "review_state"}, f"engineering receipt deviations[{index}]")
        _str(item["deviation_ref"], "deviation_ref", identifier=True)
        if item["deviation_ref"] in deviation_refs: raise EngineeringContractError("receipt duplicates a deviation")
        deviation_refs.add(item["deviation_ref"])
        for field in ("category", "reason", "impact"): _str(item[field], "deviation " + field)
        _bool(item["plan_revision_required"], "deviation plan_revision_required")
        _evidence(item["evidence_refs"], "deviation evidence_refs")
        _ids(item["out_of_scope_resource_refs"], "deviation out_of_scope_resource_refs")
        _ids(item["out_of_scope_component_refs"], "deviation out_of_scope_component_refs")
        if item["review_state"] not in {"unreviewed", "reviewed", "resolved"}:
            raise EngineeringContractError("deviation review_state is invalid")
    planned_slice = slices[row["slice_ref"]]
    if set(row["planned_resource_refs"]) != set(planned_slice["declared_resource_refs"]) or set(row["planned_component_refs"]) != set(planned_slice["declared_component_refs"]):
        raise EngineeringContractError("receipt planned scope must exactly match accepted slice declarations")
    declared_resources = set(planned_slice["declared_resource_refs"])
    declared_components = set(planned_slice["declared_component_refs"])
    allowed_extra_resources = {ref for item in deviations for ref in item["out_of_scope_resource_refs"] if item["review_state"] == "resolved"}
    allowed_extra_components = {ref for item in deviations for ref in item["out_of_scope_component_refs"] if item["review_state"] == "resolved"}
    if not set(row["actual_resource_refs"]).issubset(declared_resources | allowed_extra_resources) or not set(row["actual_component_refs"]).issubset(declared_components | allowed_extra_components):
        raise EngineeringContractError("receipt actual scope contains unaccounted out-of-scope refs")
    if row["outcome"] == "claimed_complete" and (any(item["state"] != "passed" for item in checks) or not row["artifact_refs"] or not row["evidence_refs"]):
        raise EngineeringContractError("claimed completion requires passing checks, artifacts, and evidence")
    source = _exact(row["source_evidence"], {"worktree_ref", "branch_ref", "source_sha", "evidence_refs"}, "engineering receipt source_evidence")
    for field in ("worktree_ref", "branch_ref", "source_sha"): _str(source[field], "source_evidence " + field, identifier=(field != "source_sha"))
    _evidence(source["evidence_refs"], "source_evidence evidence_refs")
    reset = _exact(row["reset_reconstruction"], {"fresh_session", "inherited_transcript_used", "reconstruction_free", "remediation_action"}, "engineering receipt reset_reconstruction")
    for field in ("fresh_session", "inherited_transcript_used", "reconstruction_free"): _bool(reset[field], "reset_reconstruction " + field)
    if reset["inherited_transcript_used"] or not reset["fresh_session"]: raise EngineeringContractError("engineering slice execution requires a fresh native session")
    if not reset["reconstruction_free"] and not reset["remediation_action"]: raise EngineeringContractError("failed reconstruction-free metric requires remediation")
    _str(reset["remediation_action"], "reset_reconstruction remediation_action") if not reset["reconstruction_free"] else (None if reset["remediation_action"] is None else _str(reset["remediation_action"], "reset_reconstruction remediation_action"))
    claim = _exact(row["executor_claim"], {"claim_state", "claimed_by", "claimed_at"}, "engineering receipt executor_claim")
    if claim["claim_state"] != "executor_claim": raise EngineeringContractError("executor claim state is invalid")
    _str(claim["claimed_by"], "executor claim claimed_by", identifier=True); _str(claim["claimed_at"], "executor claim claimed_at")
    if row["independent_verification_required"] is not True: raise EngineeringContractError("engineering receipt requires independent verification")
    bound = base.validate_execution_envelope(envelope)
    _validate_plan_envelope_binding(plan, bound)
    if row["envelope_digest"] != base.execution_envelope_digest(bound): raise EngineeringContractError("receipt does not bind exact execution envelope")
    return row


def validate_engineering_slice_receipt(receipt: Any, plan: Any, envelope: Any) -> dict[str, Any]:
    return _validate_receipt(receipt, validate_engineering_slice_plan(plan), base.validate_execution_envelope(envelope))


def build_engineering_slice_packet(envelope: Any, plan: Any, slice_ref: str) -> dict[str, Any]:
    bound = base.validate_execution_envelope(envelope)
    accepted = validate_engineering_slice_plan(plan)
    _validate_plan_envelope_binding(accepted, bound)
    row = next((item for item in accepted["slices"] if item["slice_ref"] == slice_ref), None)
    if row is None: raise EngineeringContractError("cannot build packet for unknown slice")
    packet_envelope = _narrowed_envelope(bound, row)
    packet_envelope["phase_binding"]["session_affinity"] = "fresh_native_session_required"
    packet = {
        "schema_version": "engineering-slice-packet.v1", "slice_ref": slice_ref,
        "plan_digest": accepted["plan_digest"], "envelope_digest": base.execution_envelope_digest(bound),
        "fresh_native_session_required": True, "objective": row["objective"],
        "definition_of_done": row["definition_of_done"], "planned_checks": row["planned_checks"],
        "scope_boundary": row["scope_boundary"], "forbidden_change_refs": row["forbidden_change_refs"],
        "envelope": packet_envelope,
    }
    packet["packet_digest"] = base.canonical_digest({key: item for key, item in packet.items() if key != "packet_digest"})
    return packet


def _narrowed_envelope(source: dict[str, Any], slice_row: dict[str, Any]) -> dict[str, Any]:
    """Derive the only legal slice narrowing from source and accepted plan."""
    _assert_design_execution_binding(slice_row, source)
    packet = copy.deepcopy(source)
    expected = source["request"]["declared_expectations"]
    source_steps, source_components, source_resources = map(set, (expected["plan_step_refs"], expected["component_refs"], expected["resource_refs"]))
    for name, requested, available in (("plan steps", slice_row["declared_plan_step_refs"], source_steps), ("components", slice_row["declared_component_refs"], source_components), ("resources", slice_row["declared_resource_refs"], source_resources)):
        if not set(requested).issubset(available):
            raise EngineeringContractError(f"accepted slice {name} are not contained in source envelope")
    allowed_components = set(slice_row["declared_component_refs"])
    edges = [edge for edge in expected["component_dependencies"] if edge["component_ref"] in allowed_components and edge["depends_on_component_ref"] in allowed_components]
    packet["request"]["declared_expectations"] = {
        "plan_step_refs": sorted(set(slice_row["declared_plan_step_refs"])),
        "component_refs": sorted(allowed_components),
        "component_dependencies": sorted(edges, key=lambda edge: (edge["component_ref"], edge["depends_on_component_ref"])),
        "resource_refs": sorted(set(slice_row["declared_resource_refs"])),
    }
    packet["phase_binding"]["session_affinity"] = "fresh_native_session_required"
    return packet


def validate_engineering_slice_packet(packet: Any, plan: Any, source_envelope: Any) -> dict[str, Any]:
    row = _exact(packet, {"schema_version", "slice_ref", "plan_digest", "envelope_digest", "fresh_native_session_required", "objective", "definition_of_done", "planned_checks", "scope_boundary", "forbidden_change_refs", "envelope", "packet_digest"}, "engineering slice packet")
    if row["schema_version"] != "engineering-slice-packet.v1" or row["fresh_native_session_required"] is not True:
        raise EngineeringContractError("engineering slice packet must require a fresh native session")
    accepted = validate_engineering_slice_plan(plan); source = base.validate_execution_envelope(source_envelope)
    _validate_plan_envelope_binding(accepted, source)
    _str(row["slice_ref"], "engineering packet slice_ref", identifier=True); _digest(row["plan_digest"], "engineering packet plan_digest"); _digest(row["envelope_digest"], "engineering packet envelope_digest"); _digest(row["packet_digest"], "engineering packet packet_digest")
    if row["plan_digest"] != accepted["plan_digest"] or row["envelope_digest"] != base.execution_envelope_digest(source): raise EngineeringContractError("engineering packet source binding does not match")
    expected = next((item for item in accepted["slices"] if item["slice_ref"] == row["slice_ref"]), None)
    if expected is None: raise EngineeringContractError("engineering packet names an unknown slice")
    for field in ("objective", "definition_of_done", "scope_boundary", "forbidden_change_refs"):
        if row[field] != expected[field]: raise EngineeringContractError("engineering packet content does not match accepted slice")
    _list(row["planned_checks"], "engineering packet planned_checks")
    if row["planned_checks"] != expected["planned_checks"]:
        raise EngineeringContractError("engineering packet checks do not match accepted slice")
    envelope = base.validate_execution_envelope(row["envelope"])
    expected_envelope = _narrowed_envelope(source, expected)
    if envelope != expected_envelope:
        raise EngineeringContractError("engineering packet changed source scope, authority, state, or plan binding")
    without = {key: item for key, item in row.items() if key != "packet_digest"}
    if row["packet_digest"] != base.canonical_digest(without): raise EngineeringContractError("engineering packet digest does not bind exact content")
    return row


def _validated_reviewers(plan: dict[str, Any], receipts: list[dict[str, Any]], reviewer_facts: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    receipt_by_slice = {row["slice_ref"]: row for row in receipts}
    result: dict[str, dict[str, Any]] = {}
    reviewer_keys: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(reviewer_facts):
        fact = _exact(raw, {"slice_ref", "attempt_id", "reviewer_ref", "session_ref", "state", "evidence_refs", "is_independent", "reviewed_deviation_refs", "resolved_deviation_refs"}, f"reviewer fact[{index}]")
        _str(fact["slice_ref"], "reviewer fact slice_ref", identifier=True); _str(fact["attempt_id"], "reviewer fact attempt_id", identifier=True); _str(fact["reviewer_ref"], "reviewer fact reviewer_ref", identifier=True); _str(fact["session_ref"], "reviewer fact session_ref", identifier=True)
        if fact["slice_ref"] not in receipt_by_slice: raise EngineeringContractError("reviewer fact names a slice without a receipt")
        receipt = receipt_by_slice[fact["slice_ref"]]
        if fact["attempt_id"] != receipt["attempt_id"]: raise EngineeringContractError("reviewer fact is not bound to the receipt attempt")
        if fact["reviewer_ref"] == receipt["attribution"]["actor_ref"] or fact["session_ref"] == receipt["attribution"]["session_ref"] or fact["is_independent"] is not True: raise EngineeringContractError("executor cannot self-verify")
        if (fact["slice_ref"], fact["reviewer_ref"], fact["session_ref"]) in reviewer_keys: raise EngineeringContractError("duplicate reviewer fact")
        reviewer_keys.add((fact["slice_ref"], fact["reviewer_ref"], fact["session_ref"]))
        if fact["state"] not in {"passed", "failed", "blocked"}: raise EngineeringContractError("reviewer fact state is invalid")
        reviewer_evidence = _evidence(fact["evidence_refs"], "reviewer fact evidence_refs")
        if fact["state"] == "passed" and not reviewer_evidence:
            raise EngineeringContractError("passed independent review requires evidence")
        _ids(fact["reviewed_deviation_refs"], "reviewer fact reviewed_deviation_refs"); _ids(fact["resolved_deviation_refs"], "reviewer fact resolved_deviation_refs")
        deviation_refs = {item["deviation_ref"] for item in receipt["deviations"]}
        if set(fact["reviewed_deviation_refs"]) != deviation_refs or not set(fact["resolved_deviation_refs"]).issubset(deviation_refs):
            raise EngineeringContractError("reviewer fact must cover every receipt deviation")
        if fact["state"] == "passed" and (set(fact["resolved_deviation_refs"]) != deviation_refs or any(item["review_state"] != "resolved" for item in receipt["deviations"])):
            raise EngineeringContractError("passed review requires every deviation resolved")
        if fact["slice_ref"] in result:
            raise EngineeringContractError("more than one reviewer verdict for a slice is ambiguous")
        result[fact["slice_ref"]] = fact
    return result


def eligible_slices(plan: Any, receipts: Iterable[Any] = (), reviewer_facts: Iterable[dict[str, Any]] = (), *, execution_envelopes: Iterable[Any]) -> list[str]:
    accepted = validate_engineering_slice_plan(plan)
    envelope_by_digest = _validate_authoritative_envelopes(accepted, list(execution_envelopes))
    receipt_rows = []
    for item in receipts:
        if not isinstance(item, dict) or "envelope_digest" not in item:
            raise EngineeringContractError("every eligibility receipt must resolve an authoritative envelope")
        receipt_rows.append(_validate_receipt(item, accepted, _receipt_envelope(item, envelope_by_digest)))
    _assert_envelope_retention(receipt_rows, envelope_by_digest)
    if len({row["slice_ref"] for row in receipt_rows}) != len(receipt_rows) or len({row["attempt_id"] for row in receipt_rows}) != len(receipt_rows):
        raise EngineeringContractError("duplicate slice or attempt receipt is ambiguous")
    receipts_by_slice = {row["slice_ref"]: row for row in receipt_rows}
    reviews = _validated_reviewers(accepted, receipt_rows, reviewer_facts)
    verified = {ref for ref, fact in reviews.items() if fact["state"] == "passed" and receipts_by_slice[ref]["outcome"] == "claimed_complete"}
    result = []
    for row in sorted(accepted["slices"], key=lambda item: item["ordinal"]):
        if row["slice_ref"] not in receipts_by_slice and set(row["dependency_refs"]).issubset(verified): result.append(row["slice_ref"])
    return result


def _disposition(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    row = _exact(value, {"state", "evidence_refs", "note"}, field)
    if row["state"] not in allowed: raise EngineeringContractError(f"{field} state is invalid")
    _evidence(row["evidence_refs"], field + " evidence_refs")
    _str(row["note"], field + " note")
    if row["state"] in {"resolved", "complete", "passed", "released", "proposed", "rejected", "nothing_durable"} and not row["evidence_refs"] and row["state"] not in {"nothing_durable", "rejected"}:
        raise EngineeringContractError(f"{field} resolved state requires evidence")
    return row


def route_learning_disposition(kind: str) -> str:
    allowed = {"regression_test", "gate_or_validator", "decision_record", "skill_or_workflow", "memory_or_rule_candidate", "incident_finding", "speculative_finding", "nothing_durable"}
    if kind not in allowed: raise EngineeringContractError("learning disposition route is invalid")
    return kind


def _learning(value: Any) -> dict[str, Any]:
    row = _exact(value, {"state", "route", "evidence_refs", "note"}, "learning disposition")
    if row["state"] not in {"unresolved", "proposed", "rejected", "nothing_durable"}: raise EngineeringContractError("learning disposition state is invalid")
    if row["route"] is not None: route_learning_disposition(row["route"])
    elif row["state"] != "unresolved": raise EngineeringContractError("resolved learning requires an explicit route")
    _evidence(row["evidence_refs"], "learning disposition evidence_refs"); _str(row["note"], "learning disposition note")
    if row["state"] in {"proposed", "rejected"} and not row["evidence_refs"]: raise EngineeringContractError("learning disposition requires evidence")
    return row


def _qa_facts(plan: dict[str, Any], receipts: list[dict[str, Any]], facts: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    known = {row["slice_ref"] for row in receipts}; result = {}
    for index, raw in enumerate(facts):
        fact = _exact(raw, {"slice_ref", "state", "evidence_refs", "note"}, f"manual QA fact[{index}]")
        if fact["slice_ref"] not in known: raise EngineeringContractError("manual QA fact names a slice without a receipt")
        if fact["slice_ref"] in result: raise EngineeringContractError("duplicate manual QA fact")
        if fact["state"] not in {"passed", "failed", "blocked"}: raise EngineeringContractError("manual QA state is invalid")
        qa_evidence = _evidence(fact["evidence_refs"], "manual QA fact evidence_refs"); _str(fact["note"], "manual QA fact note")
        if fact["state"] == "passed" and not qa_evidence:
            raise EngineeringContractError("passed manual QA requires evidence")
        result[fact["slice_ref"]] = fact
    return result


def _derived_operator_receipt(slices: list[dict[str, Any]], receipts: list[dict[str, Any]], qa: dict[str, dict[str, Any]]) -> dict[str, Any]:
    evidence_by_ref = {item["ref"]: item for receipt in receipts for item in receipt["evidence_refs"]}
    return {
        "what_changed": [item["slice_ref"] for item in slices if item["state"] == "verified_complete"],
        "why": "derived from accepted plan and typed receipts",
        "evidence_refs": [evidence_by_ref[key] for key in sorted(evidence_by_ref)],
        "deviations": sorted({deviation["deviation_ref"] for receipt in receipts for deviation in receipt["deviations"]}),
        "remaining_risk": [item["slice_ref"] for item in slices if item["state"] != "verified_complete"],
        "manual_qa_items": [item["slice_ref"] for item in slices if item["manual_qa_required"] and item["slice_ref"] not in qa],
    }


def _closure_complete(slices: list[dict[str, Any]], receipts: list[dict[str, Any]], reviewers: dict[str, dict[str, Any]], qa: dict[str, dict[str, Any]], closure: dict[str, dict[str, Any]], conflict: dict[str, Any]) -> bool:
    if conflict["state"] != "none" or not slices or len(receipts) != len(slices): return False
    if any(item["state"] != "verified_complete" for item in slices): return False
    if any(receipt["outcome"] != "claimed_complete" or not receipt["artifact_refs"] or not receipt["evidence_refs"] for receipt in receipts): return False
    if any(slice_row["slice_ref"] not in reviewers or reviewers[slice_row["slice_ref"]]["state"] != "passed" for slice_row in slices): return False
    for receipt in receipts:
        for deviation in receipt["deviations"]:
            review = reviewers.get(receipt["slice_ref"], {})
            if deviation["plan_revision_required"] or deviation["review_state"] != "resolved" or deviation["deviation_ref"] not in review.get("resolved_deviation_refs", []): return False
    if any(slice_row["manual_qa_required"] and (qa.get(slice_row["slice_ref"], {}).get("state") != "passed") for slice_row in slices): return False
    release_ok = closure["release"]["state"] == "released" or (closure["release"]["state"] == "not_required" and all(item["release_requirement"] == "not_required" for item in slices))
    learning_ok = closure["learning"]["state"] in {"proposed", "rejected", "nothing_durable"} and closure["learning"]["route"] is not None
    return closure["work"]["state"] == "complete" and closure["proof"]["state"] == "complete" and closure["explanation"]["state"] == "complete" and release_ok and learning_ok


def project_engineering_passport(plan: Any, receipts: Iterable[Any], *, execution_envelopes: Iterable[Any], reviewer_facts: list[dict[str, Any]] | None = None, qa_facts: list[dict[str, Any]] | None = None, explanation: dict[str, Any] | None = None, release: dict[str, Any] | None = None, learning: dict[str, Any] | None = None, stale_conflict: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project from accepted state plus server-issued envelopes; an empty envelope set is valid only when receipts are empty."""
    accepted = validate_engineering_slice_plan(plan)
    envelope_by_digest = _validate_authoritative_envelopes(accepted, list(execution_envelopes))
    receipt_rows = []
    for item in receipts:
        raw = item
        if not isinstance(raw, dict) or "envelope_digest" not in raw:
            raise EngineeringContractError("every engineering receipt must resolve an authoritative envelope")
        receipt_rows.append(_validate_receipt(raw, accepted, _receipt_envelope(raw, envelope_by_digest)))
    _assert_envelope_retention(receipt_rows, envelope_by_digest)
    if len({row["slice_ref"] for row in receipt_rows}) != len(receipt_rows) or len({row["attempt_id"] for row in receipt_rows}) != len(receipt_rows): raise EngineeringContractError("passport cannot select duplicate receipt or attempt")
    reviewer_facts = reviewer_facts or []; qa_facts = qa_facts or []
    reviewer_by = _validated_reviewers(accepted, receipt_rows, reviewer_facts); qa_by = _qa_facts(accepted, receipt_rows, qa_facts)
    slices = []
    for row in sorted(accepted["slices"], key=lambda item: item["ordinal"]):
        receipt = next((item for item in receipt_rows if item["slice_ref"] == row["slice_ref"]), None)
        review = reviewer_by.get(row["slice_ref"]); qa = qa_by.get(row["slice_ref"])
        if receipt is None: state = "eligible" if row["slice_ref"] in eligible_slices(accepted, receipt_rows, reviewer_facts, execution_envelopes=list(envelope_by_digest.values())) else "blocked"
        elif review and review["state"] == "passed" and (not row["manual_qa_required"] or (qa and qa["state"] == "passed")): state = "verified_complete"
        elif receipt["outcome"] in {"failed", "reopened"} or (qa and qa["state"] == "failed"): state = "reopened"
        else: state = "claimed"
        slices.append({"slice_ref": row["slice_ref"], "ordinal": row["ordinal"], "dependency_refs": row["dependency_refs"], "state": state, "planned_check_refs": [check["check_ref"] for check in row["planned_checks"]], "deviation_refs": [item["deviation_ref"] for item in (receipt or {}).get("deviations", [])], "manual_qa_required": row["manual_qa_required"], "release_requirement": row["release_requirement"]})
    closure = {
        "work": _disposition((explanation or {}).get("work", {"state": "unresolved", "evidence_refs": [], "note": "work disposition pending"}), "work disposition", {"unresolved", "complete"}),
        "proof": _disposition((explanation or {}).get("proof", {"state": "unresolved", "evidence_refs": [], "note": "proof disposition pending"}), "proof disposition", {"unresolved", "complete"}),
        "explanation": _disposition((explanation or {}).get("explanation", {"state": "unresolved", "evidence_refs": [], "note": "operator explanation pending"}), "explanation disposition", {"unresolved", "complete"}),
        "release": _disposition(release or {"state": "unresolved", "evidence_refs": [], "note": "release disposition pending"}, "release disposition", {"unresolved", "released", "not_required"}),
        "learning": _learning(learning or {"state": "unresolved", "route": None, "evidence_refs": [], "note": "learning disposition pending"}),
    }
    closure_complete = _closure_complete(slices, receipt_rows, reviewer_by, qa_by, closure, stale_conflict or {"state": "none", "reason": None})
    projection = {"schema_version": "engineering-passport.v1", "work_request": accepted["work_request"], "accepted_plan_revision": accepted["accepted_plan_revision"], "plan_digest": accepted["plan_digest"], "slice_plan": accepted, "execution_envelopes": list(envelope_by_digest.values()), "slices": slices, "receipts": receipt_rows, "reviewer_facts": list(reviewer_by.values()), "qa_facts": list(qa_by.values()), "operator_receipt": _derived_operator_receipt(slices, receipt_rows, qa_by), "closure": closure, "closure_state": "complete" if closure_complete else "blocked", "stale_conflict": stale_conflict or {"state": "none", "reason": None}}
    projection["projection_digest"] = base.canonical_digest(projection)
    return projection


def validate_engineering_passport(value: Any) -> dict[str, Any]:
    fields = {"schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slice_plan", "execution_envelopes", "slices", "receipts", "reviewer_facts", "qa_facts", "operator_receipt", "closure", "closure_state", "stale_conflict", "projection_digest"}
    row = _exact(value, fields, "engineering passport")
    if row["schema_version"] != "engineering-passport.v1": raise EngineeringContractError("unsupported engineering passport schema_version")
    _binding(row["work_request"], "engineering passport work_request"); _plan_ref(row["accepted_plan_revision"], "engineering passport accepted_plan_revision"); _digest(row["plan_digest"], "engineering passport plan_digest"); _digest(row["projection_digest"], "engineering passport projection_digest")
    accepted = validate_engineering_slice_plan(row["slice_plan"])
    if accepted["work_request"] != row["work_request"] or accepted["accepted_plan_revision"] != row["accepted_plan_revision"] or accepted["plan_digest"] != row["plan_digest"]:
        raise EngineeringContractError("engineering passport plan binding does not match")
    envelope_by_digest = _validate_authoritative_envelopes(accepted, row["execution_envelopes"])
    receipt_rows = []
    for item in _list(row["receipts"], "engineering passport receipts"):
        if not isinstance(item, dict) or "envelope_digest" not in item:
            raise EngineeringContractError("every engineering receipt must resolve an authoritative envelope")
        receipt_rows.append(_validate_receipt(item, accepted, _receipt_envelope(item, envelope_by_digest)))
    _assert_envelope_retention(receipt_rows, envelope_by_digest)
    if len({item["slice_ref"] for item in receipt_rows}) != len(receipt_rows) or len({item["attempt_id"] for item in receipt_rows}) != len(receipt_rows): raise EngineeringContractError("engineering passport receipts are ambiguous")
    reviewer_by = _validated_reviewers(accepted, receipt_rows, _list(row["reviewer_facts"], "engineering passport reviewer_facts"))
    qa_by = _qa_facts(accepted, receipt_rows, _list(row["qa_facts"], "engineering passport qa_facts"))
    if row["closure_state"] not in {"blocked", "complete"}: raise EngineeringContractError("engineering passport closure_state is invalid")
    projected_slices = _list(row["slices"], "engineering passport slices")
    if len(projected_slices) != len(accepted["slices"]): raise EngineeringContractError("engineering passport must project every accepted slice")
    expected_order = [item["slice_ref"] for item in sorted(accepted["slices"], key=lambda item: item["ordinal"])]
    projected_refs = []
    projected_ordinals = []
    for projected in projected_slices:
        if not isinstance(projected, dict): raise EngineeringContractError("engineering passport slice state must be an object")
        if "slice_ref" not in projected or "ordinal" not in projected: raise EngineeringContractError("engineering passport slice state binding is incomplete")
        projected_refs.append(projected["slice_ref"]); projected_ordinals.append(projected["ordinal"])
    if projected_refs != expected_order or projected_ordinals != [item["ordinal"] for item in sorted(accepted["slices"], key=lambda item: item["ordinal"])]:
        raise EngineeringContractError("engineering passport slice coverage or order is not exact")
    expected_eligible = set(eligible_slices(accepted, receipt_rows, list(reviewer_by.values()), execution_envelopes=list(envelope_by_digest.values())))
    receipt_by_slice = {item["slice_ref"]: item for item in receipt_rows}
    for projected in projected_slices:
        state = _exact(projected, {"slice_ref", "ordinal", "dependency_refs", "state", "planned_check_refs", "deviation_refs", "manual_qa_required", "release_requirement"}, "engineering passport slice state")
        source = next((item for item in accepted["slices"] if item["slice_ref"] == state["slice_ref"]), None)
        if source is None: raise EngineeringContractError("engineering passport has unknown slice state")
        receipt = receipt_by_slice.get(state["slice_ref"]); review = reviewer_by.get(state["slice_ref"]); qa = qa_by.get(state["slice_ref"])
        expected_state = "eligible" if receipt is None and state["slice_ref"] in expected_eligible else ("verified_complete" if receipt and review and review["state"] == "passed" and (not source["manual_qa_required"] or (qa and qa["state"] == "passed")) else ("reopened" if receipt and (receipt["outcome"] in {"failed", "reopened"} or (qa and qa["state"] == "failed")) else "claimed" if receipt else "blocked"))
        if state["ordinal"] != source["ordinal"] or state["dependency_refs"] != source["dependency_refs"] or state["state"] != expected_state or state["planned_check_refs"] != [check["check_ref"] for check in source["planned_checks"]] or state["deviation_refs"] != [item["deviation_ref"] for item in (receipt or {}).get("deviations", [])] or state["manual_qa_required"] != source["manual_qa_required"] or state["release_requirement"] != source["release_requirement"]:
            raise EngineeringContractError("engineering passport slice state is not derived from bound facts")
    operator = _exact(row["operator_receipt"], {"what_changed", "why", "evidence_refs", "deviations", "remaining_risk", "manual_qa_items"}, "engineering passport operator_receipt")
    _ids(operator["what_changed"], "operator what_changed"); _str(operator["why"], "operator why"); _ids(operator["deviations"], "operator deviations"); _ids(operator["remaining_risk"], "operator remaining_risk"); _ids(operator["manual_qa_items"], "operator manual_qa_items"); _evidence(operator["evidence_refs"], "operator evidence_refs")
    expected_operator = _derived_operator_receipt(projected_slices, receipt_rows, qa_by)
    if operator != expected_operator:
        raise EngineeringContractError("operator receipt is not derived from bound engineering facts")
    closure = _exact(row["closure"], {"work", "proof", "explanation", "release", "learning"}, "engineering passport closure")
    for field, allowed in (("work", {"unresolved", "complete"}), ("proof", {"unresolved", "complete"}), ("explanation", {"unresolved", "complete"}), ("release", {"unresolved", "released", "not_required"})):
        _disposition(closure[field], field, allowed)
    _learning(closure["learning"])
    conflict = _exact(row["stale_conflict"], {"state", "reason"}, "stale_conflict")
    if conflict["state"] not in {"none", "stale", "conflict", "uncertain"}: raise EngineeringContractError("stale_conflict state is invalid")
    if conflict["state"] == "none" and conflict["reason"] is not None: raise EngineeringContractError("none stale_conflict cannot carry a reason")
    if conflict["state"] != "none": _str(conflict["reason"], "stale_conflict reason")
    if row["closure_state"] == "complete" and not _closure_complete(projected_slices, receipt_rows, reviewer_by, qa_by, closure, conflict):
        raise EngineeringContractError("complete engineering passport does not satisfy closure contract")
    if row["closure_state"] != ("complete" if _closure_complete(projected_slices, receipt_rows, reviewer_by, qa_by, closure, conflict) else "blocked"):
        raise EngineeringContractError("engineering passport closure_state is not derived from facts")
    without = {key: item for key, item in row.items() if key != "projection_digest"}
    if row["projection_digest"] != base.canonical_digest(without): raise EngineeringContractError("engineering passport digest does not bind exact content")
    return row


def engineering_passport_wire(payload: Any) -> dict[str, Any]:
    return {"job_passport": {"schema_version": "job-passport-wire.v1", "kind": "engineering_passport", "payload": validate_engineering_passport(payload)}}
