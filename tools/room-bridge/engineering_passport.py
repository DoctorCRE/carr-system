"""Validate the plan scopes consumed by the assurance compiler."""

from __future__ import annotations

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


SLICE_PLAN_V1 = "engineering-slice-plan.v1"


SLICE_PLAN_V2 = "engineering-slice-plan.v2"


ENGINEERING_SLICE_PLAN_VERSIONS = (SLICE_PLAN_V1, SLICE_PLAN_V2)


DESIGN_CONTRACT_VERSION = "engineering-design-contract.v1"


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


DESIGN_DEPTH_PREDICATES = {DESIGN_CONTRACT_VERSION: _short_predicate_v1}


DESIGN_DEPTH_PREDICATE_VERSIONS = tuple(DESIGN_DEPTH_PREDICATES)


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


RESERVED_CODE_RESPONSIBILITIES = frozenset({
    "identity", "policy", "permissions", "state", "validation", "idempotency", "execution",
})


TYPED_UNCERTAINTY_CLASSES = frozenset({
    "classification", "extraction", "summarization", "ranking", "drafting", "disambiguation",
})


SELECTION_BASIS_VALUES = frozenset({
    "typed_uncertainty", "capability_gain", "quality_gain", "adaptability_gain", "cost",
})


EXECUTOR_CLASSES = frozenset({"deterministic_code", "attended_human", "model_assisted"})


ADMISSIBLE_EXECUTOR_CLASSES = frozenset({"deterministic_code", "model_assisted"})


AUTHORITY_ENVIRONMENTS = frozenset({"local", "rehearsal", "staging", "production"})


VERIFICATION_LANES = frozenset({"unit", "contract", "integration", "manual_qa"})


REVIEWER_CLASSES = frozenset({"independent_agent"})


EVIDENCE_REDACTION_CLASSES = frozenset({"metadata_only", "redacted_evidence"})


EVIDENCE_RETENTIONS = frozenset({"ephemeral", "material_redacted"})


COMPLETION_VERIFIERS = frozenset({"independent_review", "independent_review_and_manual_qa"})


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
    ordered = _transitive_dependencies(slices)
    owners: dict[str, list[str]] = {}
    for row in slices:
        if row["concurrency_posture"] != "parallel_safe":
            continue
        for resource_ref in dict.fromkeys(row["declared_resource_refs"]):
            for other in owners.get(resource_ref, []):
                if other in ordered.get(row["slice_ref"], set()) or row["slice_ref"] in ordered.get(other, set()):
                    continue
                raise EngineeringContractError(
                    f"slices {other} and {row['slice_ref']} are both parallel_safe and both own "
                    f"resource {resource_ref}; order them with a dependency, or declare the "
                    "contention with a serial_after_dependencies or exclusive_resource posture")
            owners.setdefault(resource_ref, []).append(row["slice_ref"])


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
