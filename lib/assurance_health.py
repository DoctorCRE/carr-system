"""Deterministic assurance health and scoped degradation (V5-A01, pure domain phase).

WHAT THIS IS. A pure function from one exactly bound scope plus six DISTINCT
evidence inputs to one closed display ``state``, one closed ``capability_stage``,
and the owner/evidence/impact/recovery a reader needs to act.  It reads nothing,
writes nothing, holds no authority, performs no effect, and stores no state.

AND BECAUSE IT HOLDS NO AUTHORITY, IT IS SPLIT IN TWO.  The label logic is a
MODULE-PRIVATE PREDICATE (``_label_predicate_row`` / ``_label_predicate``): it
decides what evidence MEANS and verifies no receipt, so a caller that handed it
six well-shaped dictionaries would be authoring a health label rather than
reading one.  It is therefore not exported, not in ``__all__``, and reachable
from the acceptance suite only through ``_test_only_hypothetical_row`` at the
foot of this module, whose verdict comes back under
``would_be_healthy_if_authoritative`` -- a hypothetical, never a label.

THE PUBLIC SURFACE IS ``_assurance_health_row`` / ``_assurance_health``, and it
admits evidence only from a registered evidence owner (``_EVIDENCE_OWNERS``).
THAT REGISTRY IS EMPTY, and emptily on purpose: admitting evidence means READING
the store that owns it, this module reads nothing, and the F09 adapter it
consumes reads nothing either -- so there is no route in this repository by
which any of the six layers could be admitted.  Every value a caller supplies is
reported ``unreadable`` with the durable seam it is owed
(``_OWED_EVIDENCE_OWNER_SEAMS``) named, ``passing`` is therefore unreachable on
every layer, and ``act`` and green are unreachable by construction rather than
by convention.  A surface driven by this module can say what it does not know;
it cannot say a scope is healthy.

THE SIX EVIDENCE SLOTS ARE SIX SEPARATE FACTS, and the whole point of this module
is that none of them is ever derived from another (Q043):

    artifact_assessment       independently reviewed artifact/evidence digest bound
                              to an exact repository commit and tree
    execution_assessment      an exact attempt/engineering receipt plus deterministic
                              execution evidence (envelope digest, plan hash)
    controller_assessment     current server/controller readback; an ENABLED
                              CONFIGURATION IS NEVER THIS FACT (Q053)
    candidate_outcome_oracle  a preactivation oracle receipt bound to governed data,
                              environment, expected result, equivalence comparator,
                              component versions and its own expiry
    activation_readback       a separate current controller/provider readback taken
                              AFTER activation
    actual_business_outcome   a separate accepted sourced outcome-feedback receipt
                              (ref + hash + acceptance receipt) joined through the
                              SAME exact Work Request identity

THREE INDEPENDENT GUARDS KEEP THE LAST SLOT HONEST, because filling it by
inference is the exact false-green this slice exists to remove:

 1. every evidence record declares its own ``layer``, and a record declaring one
    layer cannot be handed to another slot (refusal, not a guess);
 2. every record declares its ``basis``, and only one basis is admissible per
    slot -- ``enforcement_closure``, ``completion_lifecycle``, ``merge_or_ci_result``,
    ``enabled_configuration``, ``telemetry``, ``self_attestation`` and
    ``candidate_outcome_pass`` are named in the vocabulary precisely so that
    offering one is reported as ``refused_substitute`` rather than silently read
    as an outcome;
 3. the six slots must hold six DISTINCT exact identities; reusing one receipt's
    ref or digest in a second slot is ``indistinct``, never two facts.

WHAT IT DELIBERATELY IS NOT.

* It is not a workflow, status, receipt, completion or incident registry.  The
  only workflow authority is ``lib/control_plane_workflow_truth`` (V5-F09); this
  module CONSUMES one of its projected rows and refuses any other shape, so a
  second census cannot grow here.  Incident, impact and recovery references are
  echoed from the caller's existing records; none is invented.
* It is not an activation, a deployment, a page, or any other effect.  A
  ``recovery`` block names what evidence the bound owner must obtain; it never
  claims something was done.
* It invents no freshness.  Every evidence record must arrive with the expiry its
  own source defines; a record without one is refused rather than treated as
  eternally current.
* It never guesses a degradation stage.  The act/draft/read/unavailable ladder is
  a fixed table of capability requirements evaluated against supplied evidence.

FAIL-CLOSED, AND THE TWO KINDS OF ABSENCE.  ``_UNREADABLE`` (reused from F09)
means "the read did not happen or failed"; ``None`` means "the read happened and
there is genuinely no evidence".  They are different facts and are reported
differently, and neither is ever green.  A malformed input raises
``_AssuranceHealthContractError`` rather than being guessed at: the caller owns
mapping an absent row onto ``None``, and this module owns identities,
distinctness, currentness, precedence, scope and state.

SCOPE IS EXACT, AND SCOPE IS A FENCE.  A scope is bound by
(workflow_key, workflow_version, work_request_id) -- identities only.  No human
label, description or free-text field participates in any join, and evidence
carrying a different identity is ``mismatched``, never "close enough".  Each row
is computed from its own bound scope's inputs alone, so a failure in one scope
cannot move an unrelated bound scope's evidence-derived state (Q006).

DISPLAY PRECEDENCE, most conservative first, evaluated in this exact order:

    1. unknown              authoritative workflow truth is indeterminate
    2. disabled             F09 says the definition exists and is not enabled
    3. failed               a determinate non-pass withdrew even read capability,
                            and it did so BY ITSELF -- capability that is missing
                            only because a layer could not be read is unproven,
                            not withdrawn, and never reported as failed
    4. degraded             a determinate non-pass withdrew act and/or draft
    5. not-yet-operational  nothing failed, and the scope has not yet earned live
                            admission or has no accepted outcome evidence yet
    6. healthy              full act capability on six current, distinct,
                            independent, exactly bound passing facts
    7. unknown              anything else: evidence is incomplete, so nothing is claimed
"""
from __future__ import annotations

# EVERY IMPORT IS ALIASED PRIVATE, for the reason ``lib/assurance_health_sources``
# already carries: an imported name is a PUBLIC name of the importing module, so
# an unaliased ``UNREADABLE`` or ``datetime`` would put a name this module does
# not own onto the surface a sweep must clear.
from datetime import datetime as _datetime, timezone as _timezone
from typing import Any as _Any

from lib.control_plane_workflow_truth import (  # the ONE workflow authority (V5-F09)
    SCHEMA_VERSION as _WORKFLOW_TRUTH_SCHEMA_VERSION,
    STATES as _WORKFLOW_TRUTH_STATES,
    UNREADABLE as _UNREADABLE,
    completion_subject_key as _completion_subject_key,
)

SCHEMA_VERSION = "assurance-health.v1"

# THE PUBLIC SURFACE, EXHAUSTIVELY, AND IT CARRIES NO STATE AT ALL.
#
# WHAT CHANGED IN THE ELEVENTH CORRECTION, AND WHY.  This module used to export
# twenty-six names: the whole domain vocabulary (``DISPLAY_STATES``,
# ``GREEN_STATE``, ``CAPABILITY_STAGES``, the evidence states and bases) plus
# ``assurance_health_row`` and ``assurance_health``, the two callables that turn
# caller-supplied scopes, workflow truth and evidence into ``{"state":
# "healthy", "green": true}``.  The standing authority rule forbids exactly that
# shape: "no exported function may return the privileged outcome from caller
# input, under ANY name".  A review's fresh traversal of this module's exported
# values found thirty-three privileged hits -- ``healthy``, ``read``, ``pass``,
# ``passing``, ``operational``, ``green`` -- and the exported route reproduced
# green from a hand-written bundle.
#
# THE FIX IS NOT AN ALLOWLIST AND NOT A RENAME.  The domain vocabulary moved
# behind module-private names (the table below), and so did both projection
# entry points.  What is left public is one schema identifier, which is a string
# no caller can turn into an outcome and which carries no word from the
# privileged union.  This is deliberately STRONGER than the opaque-token option
# the correction offered: a token is still a value a consumer could learn to
# treat as green, whereas a surface with no state on it has nothing to learn.
#
# WHO READS THE PRIVATE NAMES, because "private" here means private to the
# surface and not unreachable: ``lib/assurance_health_sources`` imports the four
# it needs under its own underscored aliases, and the acceptance suite reaches
# the classifier only through ``_test_only_hypothetical_row`` /
# ``_test_only_hypothetical_census``, whose answers come back under
# ``would_be_*_if_authoritative`` names.  ``ops/assurance-health-selftest.py ::
# public_surface_guard_checks`` parses this file with ``ast`` and fails if any of
# it becomes public again; ``closed_union_sweep_checks`` records each private
# name against the consumer that reads it, one by one, with no blanket clause.
__all__ = [
    "SCHEMA_VERSION",
]

# The six display states, exactly as the settled contract names them.  There is no
# seventh, and only one of them is green.
_DISPLAY_STATES = (
    "healthy",
    "degraded",
    "failed",
    "unknown",
    "disabled",
    "not-yet-operational",
)
_GREEN_STATE = "healthy"

# The scoped degradation ladder.  A failure walks DOWN this ladder and stops; it
# never jumps, and it never leaves the bound scope.
_CAPABILITY_STAGES = ("act", "draft", "read", "unavailable")

_PREACTIVATION_SLOTS = (
    "artifact_assessment",
    "execution_assessment",
    "controller_assessment",
    "candidate_outcome_oracle",
)
_POSTACTIVATION_SLOTS = ("activation_readback", "actual_business_outcome")
_EVIDENCE_SLOTS = _PREACTIVATION_SLOTS + _POSTACTIVATION_SLOTS

# The exact identity a scope is bound by.  Identities only: adding a label-shaped
# field here is what "no name/title heuristic" forbids.
#
# THE WORK REQUEST IDENTITY IS OPTIONAL ON THE SCOPE AND REQUIRED FOR THE LAYER
# THAT JOINS THROUGH IT.  The authoritative workflow census this projection is
# wired to carries workflow identities and no Work Request identity, so making a
# Work Request part of every scope key would have left every real scope
# unbindable -- and a surface that can bind no scope is silent, not honest.  A
# scope may therefore be bound by workflow identity alone; the only consequence,
# and it is the correct one, is that ``actual_business_outcome`` then has no
# admissible join, so act capability and green are unreachable for it.
_SCOPE_IDENTITY_FIELDS = ("workflow_key", "workflow_version", "work_request_id")
_REQUIRED_SCOPE_IDENTITY_FIELDS = ("workflow_key", "workflow_version")
_OPTIONAL_SCOPE_IDENTITY_FIELDS = ("work_request_id",)

# The exact scope identity each layer must be able to join through.  A layer whose
# required identity the scope does not carry is ``unbindable``: it is not a
# finding against the scope, and it is never a pass.
_SLOT_REQUIRED_SCOPE_IDENTITY = {"actual_business_outcome": "work_request_id"}

# What a source can say about its own evidence.  Anything else is refused.
_EVIDENCE_STATUSES = ("pass", "fail", "skipped", "untested", "error", "conflicting")

# Ordered most-conservative first: the first condition that holds wins, so a
# contradiction, a refused substitute or an expiry can never be outranked by the
# record's own claim to pass.
_EVIDENCE_STATES = (
    "unreadable",          # the caller could not read the source
    "missing",             # read, and there is genuinely no evidence
    "mismatched",          # evidence exists but binds a different exact scope
    "unbindable",          # this scope carries no identity this layer can join through
    "conflicting",         # the record contradicts itself or its neighbours
    "refused_substitute",  # a basis that is never sufficient for THIS slot
    "failed",              # a real, current, exactly bound non-pass
    "error",               # the assessment could not complete
    "skipped",
    "untested",
    "self_attested",       # the evaluator and the subject are one identity
    "indistinct",          # reuses another slot's exact evidence identity
    "stale",               # past the expiry its own source defined
    "passing",             # the ONLY state that can contribute to healthy
)

# "missing" and "unbindable" belong to neither class: they are the two absences
# that "not-yet-operational" is allowed to describe, and neither is a finding on
# its own.  Neither is ever green either -- both block the act stage.
_INDETERMINATE_EVIDENCE_STATES = ("unreadable", "mismatched", "conflicting", "indistinct")
_UNEARNED_EVIDENCE_STATES = ("missing", "unbindable")
_DETERMINATE_NONPASS_EVIDENCE_STATES = (
    "refused_substitute", "failed", "error", "skipped", "untested", "self_attested", "stale",
)

# Every basis a caller may declare.  The forbidden ones are IN this vocabulary on
# purpose: an unnamed basis is a malformed input, while a named-but-inadmissible
# basis must be reported as the exact refusal it is.
_EVIDENCE_BASES = (
    "independent_artifact_review",
    "attempt_receipt_execution_evidence",
    "controller_readback",
    "candidate_outcome_oracle_receipt",
    "activation_readback",
    "accepted_sourced_outcome_feedback_receipt",
    # never sufficient for the slot that offers them
    "enforcement_closure",
    "completion_lifecycle",
    "merge_or_ci_result",
    "enabled_configuration",
    "telemetry",
    "self_attestation",
    "candidate_outcome_pass",
)

_SLOT_ADMISSIBLE_BASES = {
    "artifact_assessment": ("independent_artifact_review",),
    "execution_assessment": ("attempt_receipt_execution_evidence",),
    "controller_assessment": ("controller_readback",),
    "candidate_outcome_oracle": ("candidate_outcome_oracle_receipt",),
    "activation_readback": ("activation_readback",),
    "actual_business_outcome": ("accepted_sourced_outcome_feedback_receipt",),
}

# The exact binding each layer must carry, drawn from the records that already
# prove these edges (0451 assurance extensions, 0303 attempt/activation, the
# activation-reliability evaluation plan, 0179 sourced outcome feedback).
_SLOT_REQUIRED_FIELDS = {
    "artifact_assessment": ("repository_commit_sha", "repository_tree_sha", "reviewer_fact_id"),
    "execution_assessment": ("attempt_id", "envelope_digest", "plan_hash"),
    "controller_assessment": ("controller_state", "readback_source", "readback_at"),
    "candidate_outcome_oracle": ("governed_data_ref", "environment", "expected_result_ref",
                                 "equivalence_comparator", "component_versions"),
    "activation_readback": ("activation_id", "readback_source", "readback_at"),
    "actual_business_outcome": ("outcome_feedback_ref", "outcome_feedback_hash",
                                "acceptance_receipt_id"),
}
_INSTANT_FIELDS = ("readback_at",)
_VERSION_MAP_FIELDS = ("component_versions",)

# What the bound owner must obtain to clear a non-passing slot.  These are
# requirements, not effects: nothing here is performed.
_SLOT_REQUIREMENT = {
    "artifact_assessment":
        "a current independently reviewed artifact assessment bound to the exact "
        "repository commit and tree of this scope",
    "execution_assessment":
        "a current exact attempt receipt with deterministic execution evidence "
        "(envelope digest and plan hash) for this scope",
    "controller_assessment":
        "a current controller/provider readback for this scope; enabled configuration "
        "is never this fact",
    "candidate_outcome_oracle":
        "a current passing candidate-outcome oracle receipt bound to governed data, "
        "environment, expected result, equivalence comparator, versions and its own TTL",
    "activation_readback":
        "a current controller/provider readback taken after activation of this scope",
    "actual_business_outcome":
        "a current accepted sourced outcome-feedback receipt (ref, hash and acceptance "
        "receipt) joined through this scope's exact Work Request identity",
}

# The ladder as a table, evaluated top down.  The first stage whose every
# requirement is proven is the stage the scope actually holds.
_STAGE_REQUIREMENTS = (
    ("act", ("workflow_readable", "workflow_coherent", "workflow_live_admissible",
             "artifact_assessment", "execution_assessment", "controller_assessment",
             "candidate_outcome_oracle", "activation_readback", "actual_business_outcome")),
    ("draft", ("workflow_readable", "workflow_coherent", "workflow_enabled",
               "artifact_assessment", "execution_assessment", "controller_assessment")),
    ("read", ("workflow_readable", "workflow_coherent", "artifact_assessment")),
)

# What a failure took away, given the highest stage the failure alone still allows.
_STAGE_DOWN_TO = {stage: list(_CAPABILITY_STAGES[:index])
                  for index, stage in enumerate(_CAPABILITY_STAGES)}

# F09 states this projection reads as authoritative dispositions of the workflow
# itself.  Nothing else in this module decides these three facts.
_TRUTH_INDETERMINATE = ("unknown", "conflict", "undeclared")
_TRUTH_NOT_ENABLED = ("declared_disabled",)

_WORKFLOW_TRUTH_PUBLIC_FIELDS = (
    "state", "disposition", "enabled", "admissible_modes", "operational",
    "false_operational", "duplicate_open", "completion_lifecycle_state", "reasons",
)


class _AssuranceHealthContractError(ValueError):
    """A caller supplied an input this projection refuses to guess at."""


def _utc(value: _Any, *, field: str) -> _datetime:
    if isinstance(value, _datetime):
        return value if value.tzinfo else value.replace(tzinfo=_timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise _AssuranceHealthContractError(f"{field} must be an ISO-8601 instant")
    try:
        parsed = _datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise _AssuranceHealthContractError(f"{field} is not an ISO-8601 instant: {exc}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_timezone.utc)


def _require_mapping(value: _Any, *, field: str) -> dict[str, _Any]:
    if not isinstance(value, dict):
        raise _AssuranceHealthContractError(f"{field} must be an object")
    return value


def _require_text(value: _Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _AssuranceHealthContractError(f"{field} must be a non-empty string")
    return value


def _require_refs(value: _Any, *, field: str) -> list[str]:
    """Existing incident/recovery references, echoed exactly or not at all."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise _AssuranceHealthContractError(f"{field} must be a list of existing evidence refs")
    return [_require_text(item, field=f"{field}[]") for item in value]


def _scope_identity(scope: _Any, *, field: str = "scope") -> dict[str, _Any]:
    """The exact identity a scope or an evidence record binds.

    Identities only.  There is deliberately no route by which a human label could
    stand in for one of these values.
    """
    mapping = _require_mapping(scope, field=field)
    missing = [name for name in _REQUIRED_SCOPE_IDENTITY_FIELDS if name not in mapping]
    if missing:
        raise _AssuranceHealthContractError(
            f"{field} is missing the exact scope-binding identities {sorted(missing)}; "
            "an unbound record is refused, never joined by any other field")
    version = mapping["workflow_version"]
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise _AssuranceHealthContractError(f"{field}.workflow_version must be a positive integer")
    # Absent and explicitly null are the same fact: this scope is not bound to a
    # Work Request.  Anything else present must be an exact identity -- an empty
    # or non-string value is refused rather than quietly read as unbound, because
    # "no binding" and "a broken binding" are different facts.
    work_request_id = mapping.get("work_request_id")
    if work_request_id is not None:
        work_request_id = _require_text(work_request_id, field=f"{field}.work_request_id")
    return {
        "workflow_key": _require_text(mapping["workflow_key"], field=f"{field}.workflow_key"),
        "workflow_version": version,
        "work_request_id": work_request_id,
    }


def _component_versions(value: _Any, *, field: str) -> list[str]:
    if not isinstance(value, dict) or not value:
        raise _AssuranceHealthContractError(
            f"{field} must be a non-empty object of component name to version")
    return sorted(f"{_require_text(name, field=field)}="
                  f"{_require_text(version, field=f'{field}.{name}')}"
                  for name, version in value.items())


def _evidence_record(slot: str, value: _Any) -> dict[str, _Any]:
    """Validate one supplied evidence mapping into its exact, public identity.

    Only the fields named here are ever carried into the projection, so a caller
    that hands over a row with a secret alongside its identity cannot leak it
    through this surface.
    """
    supplied = _require_mapping(value, field=f"evidence[{slot}]")

    layer = _require_text(supplied.get("layer"), field=f"evidence[{slot}].layer")
    if layer != slot:
        raise _AssuranceHealthContractError(
            f"evidence declaring layer {layer!r} cannot fill the {slot!r} slot: "
            "no layer may be inferred from another")

    basis = _require_text(supplied.get("basis"), field=f"evidence[{slot}].basis")
    if basis not in _EVIDENCE_BASES:
        raise _AssuranceHealthContractError(
            f"evidence[{slot}].basis {basis!r} is outside the closed vocabulary")

    status = supplied.get("status")
    if status not in _EVIDENCE_STATUSES:
        raise _AssuranceHealthContractError(
            f"evidence[{slot}].status must be one of {_EVIDENCE_STATUSES}")

    record: dict[str, _Any] = {
        "slot": slot,
        "layer": layer,
        "basis": basis,
        "status": status,
        "evidence_ref": _require_text(supplied.get("evidence_ref"),
                                      field=f"evidence[{slot}].evidence_ref"),
        "evidence_digest": _require_text(supplied.get("evidence_digest"),
                                         field=f"evidence[{slot}].evidence_digest"),
        "evaluator_identity": _require_text(supplied.get("evaluator_identity"),
                                            field=f"evidence[{slot}].evaluator_identity"),
        "subject_identity": _require_text(supplied.get("subject_identity"),
                                          field=f"evidence[{slot}].subject_identity"),
        "observed_at": _utc(supplied.get("observed_at"), field=f"evidence[{slot}].observed_at"),
        # REQUIRED. This module defines no window of its own; evidence that arrives
        # without the expiry its own source defines is refused rather than treated
        # as current forever.
        "expires_at": _utc(supplied.get("expires_at"), field=f"evidence[{slot}].expires_at"),
        "scope": _scope_identity(supplied.get("scope"), field=f"evidence[{slot}].scope"),
    }
    for name in _SLOT_REQUIRED_FIELDS[slot]:
        field = f"evidence[{slot}].{name}"
        if name in _INSTANT_FIELDS:
            record[name] = _utc(supplied.get(name), field=field)
        elif name in _VERSION_MAP_FIELDS:
            record[name] = _component_versions(supplied.get(name), field=field)
        else:
            record[name] = _require_text(supplied.get(name), field=field)
    return record


def _classify(record: dict[str, _Any], *, bound: dict[str, _Any], now: _datetime,
              shared: dict[str, list[str]]) -> tuple[str, list[str]]:
    """Classify one validated record.  Every condition that holds is reported."""
    slot = record["slot"]
    flags: set[str] = set()
    reasons: list[str] = []

    if record["scope"] != bound:
        flags.add("mismatched")
        reasons.append(
            f"{slot}: evidence binds workflow {record['scope']['workflow_key']}"
            f":v{record['scope']['workflow_version']} / work request "
            f"{record['scope']['work_request_id']}, which is not this bound scope")

    required_identity = _SLOT_REQUIRED_SCOPE_IDENTITY.get(slot)
    if required_identity is not None and bound.get(required_identity) is None:
        flags.add("unbindable")
        reasons.append(
            f"{slot}: this scope carries no {required_identity}, so there is no identity "
            "this layer can be joined through; no receipt can fill it until the binding exists")

    for name in ("observed_at",) + _INSTANT_FIELDS:
        instant = record.get(name)
        if isinstance(instant, _datetime) and instant > now:
            flags.add("conflicting")
            reasons.append(f"{slot}: {name} is after the projection instant")

    if record["status"] == "conflicting":
        flags.add("conflicting")
        reasons.append(f"{slot}: the source reports contradictory evidence")

    if record["basis"] not in _SLOT_ADMISSIBLE_BASES[slot]:
        flags.add("refused_substitute")
        reasons.append(
            f"{slot}: {record['basis']} is refused as a substitute for this layer; "
            f"only {list(_SLOT_ADMISSIBLE_BASES[slot])} can fill it")

    if record["evaluator_identity"] == record["subject_identity"]:
        flags.add("self_attested")
        reasons.append(
            f"{slot}: evaluator {record['evaluator_identity']} is the subject it assessed")

    others = sorted(set(shared.get(slot, [])))
    if others:
        flags.add("indistinct")
        reasons.append(
            f"{slot}: reuses the exact evidence identity of {others}; one receipt is "
            "one fact and can never satisfy two layers")

    if now > record["expires_at"]:
        flags.add("stale")
        reasons.append(
            f"{slot}: evidence expired at {record['expires_at'].isoformat()} "
            f"and is no longer current")

    status_state = {"fail": "failed", "error": "error", "skipped": "skipped",
                    "untested": "untested"}.get(record["status"])
    if status_state:
        flags.add(status_state)
        reasons.append(f"{slot}: the source reports {record['status']}")

    for state in _EVIDENCE_STATES:
        if state in flags:
            return state, reasons
    return "passing", reasons


def _shared_identities(records: dict[str, dict[str, _Any]]) -> dict[str, list[str]]:
    """Slots that collide on an exact evidence ref or digest.

    Two layers holding one identity is not two facts; it is one fact counted
    twice, which is exactly the cross-layer inference this slice forbids.
    """
    shared: dict[str, list[str]] = {slot: [] for slot in records}
    for field in ("evidence_ref", "evidence_digest"):
        index: dict[str, list[str]] = {}
        for slot, record in records.items():
            index.setdefault(record[field], []).append(slot)
        for slots in index.values():
            if len(slots) > 1:
                for slot in slots:
                    shared[slot].extend(other for other in slots if other != slot)
    return shared


def _public_evidence(slot: str, state: str, record: dict[str, _Any] | None,
                     reasons: list[str]) -> dict[str, _Any]:
    layer_class = "preactivation" if slot in _PREACTIVATION_SLOTS else "postactivation"
    public: dict[str, _Any] = {
        "slot": slot,
        "layer_class": layer_class,
        "state": state,
        "present": record is not None,
        "requirement": _SLOT_REQUIREMENT[slot],
        "reasons": sorted(set(reasons)),
    }
    if record is None:
        return public
    public.update({
        "basis": record["basis"],
        "status": record["status"],
        "evidence_ref": record["evidence_ref"],
        "evidence_digest": record["evidence_digest"],
        "evaluator_identity": record["evaluator_identity"],
        "subject_identity": record["subject_identity"],
        "observed_at": record["observed_at"].isoformat(),
        "expires_at": record["expires_at"].isoformat(),
        "bound_scope": dict(record["scope"]),
    })
    for name in _SLOT_REQUIRED_FIELDS[slot]:
        value = record[name]
        public[name] = value.isoformat() if isinstance(value, _datetime) else value
    return public


def _workflow_truth_facts(workflow_truth: _Any, bound: dict[str, _Any]) -> tuple[
        dict[str, _Any] | None, dict[str, _Any]]:
    """Read F09's projected row.  This module derives no workflow truth of its own."""
    if workflow_truth is _UNREADABLE:
        return None, {"state": None, "indeterminate": True, "not_enabled": False,
                      "readable": False, "coherent": False, "enabled": False,
                      "live_admissible": False}
    row = _require_mapping(workflow_truth, field="workflow_truth")
    if row.get("schema_version") != _WORKFLOW_TRUTH_SCHEMA_VERSION:
        raise _AssuranceHealthContractError(
            "workflow_truth must be a row projected by lib/control_plane_workflow_truth "
            f"({_WORKFLOW_TRUTH_SCHEMA_VERSION}); this projection builds no second "
            "workflow, status or receipt registry")
    state = row.get("state")
    if state not in _WORKFLOW_TRUTH_STATES:
        raise _AssuranceHealthContractError(
            f"workflow_truth.state {state!r} is outside the F09 vocabulary")
    key = _require_text(row.get("workflow_key"), field="workflow_truth.workflow_key")
    version = row.get("workflow_version")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise _AssuranceHealthContractError(
            "workflow_truth.workflow_version must be a positive integer")
    if key != bound["workflow_key"] or version != bound["workflow_version"]:
        raise _AssuranceHealthContractError(
            "workflow_truth row does not bind the exact scope being projected")
    modes = row.get("admissible_modes")
    if not isinstance(modes, list):
        raise _AssuranceHealthContractError("workflow_truth.admissible_modes must be a list")
    facts = {
        "state": state,
        "indeterminate": state in _TRUTH_INDETERMINATE,
        "not_enabled": state in _TRUTH_NOT_ENABLED,
        "readable": state != "unknown",
        "coherent": state not in ("conflict", "undeclared"),
        "enabled": bool(row.get("enabled")),
        # F09 owns the evidence ladder: "live" appears only when accepted acceptance
        # evidence earned it, so an enabled row alone can never reach act here.
        "live_admissible": "live" in modes,
    }
    return row, facts


def _label_predicate_row(*, scope: _Any, workflow_truth: _Any, evidence: _Any,
                         now: _Any,
                         unreadable_reasons: _Any = None) -> dict[str, _Any]:
    """THE PURE LABEL PREDICATE. NOT WIRED, AND NOT A ROUTE TO A DISPLAYED LABEL.

    This is the decision logic alone: a total function from one exactly bound
    scope plus six evidence shapes to one display state and one capability stage.
    It holds no authority and verifies no receipt, so IT IS NOT THE PUBLIC
    SURFACE.  It exists to be unit-tested against fixture shapes -- including the
    green shape, which is the only way to prove the ladder actually requires all
    six layers -- and nothing that renders a label to a human may call it.

    The wired entry points are ``_assurance_health_row`` and ``_assurance_health``
    below: they admit evidence only from a registered evidence owner and hand
    everything else here as ``_UNREADABLE``, so no caller-supplied shape reaches
    ``passing`` on a layer this repository does not own.

    ``workflow_truth`` is one row from ``lib.control_plane_workflow_truth`` (or
    ``_UNREADABLE``).  ``evidence`` must name every one of the six slots
    explicitly: a caller that did not read a layer says so with ``_UNREADABLE``
    rather than inheriting a permissive default.  ``unreadable_reasons`` lets the
    wired caller say WHY a layer is unread, in place of the generic reason.
    """
    unread_reason_for: dict[str, str] = {}
    if unreadable_reasons is not None:
        unread_reason_for = {
            _require_text(slot, field="unreadable_reasons key"):
                _require_text(reason, field=f"unreadable_reasons[{slot}]")
            for slot, reason in _require_mapping(
                unreadable_reasons, field="unreadable_reasons").items()}
    bound = _scope_identity(scope)
    scope_mapping = _require_mapping(scope, field="scope")
    owner = _require_text(scope_mapping.get("owner"), field="scope.owner")
    incident_refs = _require_refs(scope_mapping.get("incident_refs"), field="scope.incident_refs")
    recovery_refs = _require_refs(scope_mapping.get("recovery_refs"), field="scope.recovery_refs")
    instant = _utc(now, field="now")

    supplied = _require_mapping(evidence, field="evidence")
    unnamed = sorted(set(supplied) - set(_EVIDENCE_SLOTS))
    if unnamed:
        raise _AssuranceHealthContractError(
            f"evidence carries slots outside the closed set: {unnamed}")
    absent = [slot for slot in _EVIDENCE_SLOTS if slot not in supplied]
    if absent:
        raise _AssuranceHealthContractError(
            f"evidence must name every layer explicitly; {absent} were not supplied. "
            "Pass lib.control_plane_workflow_truth.UNREADABLE for a layer that could "
            "not be read and None for a layer "
            "that was read and has no evidence")

    truth_row, truth = _workflow_truth_facts(workflow_truth, bound)

    records: dict[str, dict[str, _Any]] = {}
    states: dict[str, str] = {}
    slot_reasons: dict[str, list[str]] = {slot: [] for slot in _EVIDENCE_SLOTS}
    for slot in _EVIDENCE_SLOTS:
        value = supplied[slot]
        if value is _UNREADABLE:
            states[slot] = "unreadable"
            slot_reasons[slot].append(
                unread_reason_for.get(slot, f"{slot}: the caller could not read this layer"))
        elif value is None:
            states[slot] = "missing"
            slot_reasons[slot].append(f"{slot}: read, and there is no evidence")
        else:
            # A record for a layer this scope cannot join is still VALIDATED, so a
            # malformed one is refused rather than excused; _classify then reports
            # the join it cannot make rather than the claim it makes.
            records[slot] = _evidence_record(slot, value)

    for slot, required_identity in _SLOT_REQUIRED_SCOPE_IDENTITY.items():
        if slot in records or bound.get(required_identity) is not None:
            continue
        states[slot] = "unbindable"
        slot_reasons[slot].append(
            f"{slot}: this scope carries no {required_identity}, so there is no identity "
            "this layer can be joined through; no receipt can fill it until the binding exists")

    shared = _shared_identities(records)
    for slot, record in records.items():
        state, record_reasons = _classify(record, bound=bound, now=instant, shared=shared)
        states[slot] = state
        slot_reasons[slot].extend(record_reasons)

    # ORDERING COHERENCE. An accepted business outcome for a scope with no current
    # passing activation readback cannot have happened the way it claims; reporting
    # it as passing would let the last slot be earned out of order.
    if states["actual_business_outcome"] == "passing" \
            and states["activation_readback"] != "passing":
        states["actual_business_outcome"] = "conflicting"
        slot_reasons["actual_business_outcome"].append(
            "actual_business_outcome: an accepted business outcome is claimed for a scope "
            "with no current passing activation readback")

    capability_inputs = {
        "workflow_readable": truth["readable"],
        "workflow_coherent": truth["coherent"],
        "workflow_enabled": truth["enabled"],
        "workflow_live_admissible": truth["live_admissible"],
    }
    capability_inputs.update({slot: states[slot] == "passing" for slot in _EVIDENCE_SLOTS})

    def _stage(inputs: dict[str, bool]) -> str:
        for name, requirements in _STAGE_REQUIREMENTS:
            if all(inputs[requirement] for requirement in requirements):
                return name
        return "unavailable"

    stage = _stage(capability_inputs)
    stage_index = _CAPABILITY_STAGES.index(stage)
    retained = list(_CAPABILITY_STAGES[stage_index:-1])
    withdrawn = list(_CAPABILITY_STAGES[:stage_index])

    requirements_of = dict(_STAGE_REQUIREMENTS)
    blocking_next = ([] if stage == "act" else
                     sorted(requirement
                            for requirement in requirements_of[_CAPABILITY_STAGES[stage_index - 1]]
                            if not capability_inputs[requirement]))
    blocking_act = sorted(requirement for requirement in requirements_of["act"]
                          if not capability_inputs[requirement])

    indeterminate = sorted(slot for slot in _EVIDENCE_SLOTS
                           if states[slot] in _INDETERMINATE_EVIDENCE_STATES)

    # CAPABILITY LOST TO AN UNREAD LAYER IS NOT CAPABILITY A FAILURE TOOK AWAY.
    # A false red is the same defect as a false green wearing the other colour:
    # both report something the evidence does not say.  So the stage is computed
    # a second time with every INDETERMINATE layer granted, and the difference
    # between the two answers is exactly the capability that is unproven rather
    # than withdrawn.  Only what survives that counterfactual is attributed to
    # the failure; the reported capability_stage stays the conservative one.
    attributable_stage = _stage({**capability_inputs,
                                 **{slot: True for slot in indeterminate}})
    determinate_nonpass = sorted(slot for slot in _EVIDENCE_SLOTS
                                 if states[slot] in _DETERMINATE_NONPASS_EVIDENCE_STATES)
    missing = sorted(slot for slot in _EVIDENCE_SLOTS if states[slot] == "missing")
    unbindable = sorted(slot for slot in _EVIDENCE_SLOTS if states[slot] == "unbindable")
    preactivation_passing = all(states[slot] == "passing" for slot in _PREACTIVATION_SLOTS)

    reasons: list[str] = []
    for slot in _EVIDENCE_SLOTS:
        reasons.extend(slot_reasons[slot])

    # ---- display state, most conservative predicate first --------------------
    if truth["indeterminate"]:
        state = "unknown"
        state_reason = (
            "authoritative workflow truth is indeterminate "
            f"(F09 state {truth['state']!r}); nothing is claimed about this scope")
    elif truth["not_enabled"]:
        # Only F09 emits disabled, and a deliberately chosen state is not a failure.
        # Any evidence finding stays visible in evidence/reasons below.
        state = "disabled"
        state_reason = "F09 authoritative workflow truth reports the definition is not enabled"
    elif determinate_nonpass and attributable_stage == "unavailable":
        state = "failed"
        state_reason = (
            f"a current exactly bound non-pass in {determinate_nonpass} withdrew every "
            "capability of this scope")
    elif determinate_nonpass:
        state = "degraded"
        state_reason = (
            f"a current exactly bound non-pass in {determinate_nonpass} withdrew "
            f"{_STAGE_DOWN_TO[attributable_stage]} capability; {attributable_stage} is what "
            "the failure itself leaves standing")
        if stage != attributable_stage:
            state_reason += (
                f". Capability below {attributable_stage} is unproven rather than withdrawn: "
                f"{indeterminate} could not be read, so {retained} is what this scope can "
                "currently be shown to hold")
    elif not determinate_nonpass and not indeterminate and (
            not truth["live_admissible"]
            or any(states[slot] in _UNEARNED_EVIDENCE_STATES for slot in _POSTACTIVATION_SLOTS)):
        state = "not-yet-operational"
        state_reason = (
            "nothing failed and nothing is operational yet: "
            + ("the F09 evidence ladder has not admitted live execution"
               if not truth["live_admissible"] else
               f"{sorted(slot for slot in _POSTACTIVATION_SLOTS if states[slot] in _UNEARNED_EVIDENCE_STATES)} "
               "has no evidence the bound scope can yet carry"))
    elif stage == "act":
        state = "healthy"
        state_reason = (
            "six distinct, current, independently evaluated, exactly bound passing facts "
            "and F09 live admission")
    else:
        state = "unknown"
        state_reason = (
            f"evidence is incomplete or indeterminate (indeterminate={indeterminate}, "
            f"missing={missing}); no state is claimed")
    reasons.append(state_reason)

    if state != _GREEN_STATE and stage == "act":  # pragma: no cover - guards a future edit
        raise _AssuranceHealthContractError(
            "full act capability must be reported as healthy or not at all")
    if state == _GREEN_STATE and not all(states[slot] == "passing" for slot in _EVIDENCE_SLOTS):
        raise _AssuranceHealthContractError(  # pragma: no cover - guards a future edit
            "healthy requires every layer to be passing")

    impact = {
        "capability_stage": stage,
        "capabilities_retained": retained,
        "capabilities_withdrawn": withdrawn,
        "blocking_next_stage": blocking_next,
        "blocking_act": blocking_act,
        # A failure is fenced to exactly this identity; no other scope is touched.
        "scope_limited_to": dict(bound),
    }
    recovery = {
        "owner": owner,
        "required_evidence": [
            {"slot": slot, "evidence_state": states[slot], "requirement": _SLOT_REQUIREMENT[slot]}
            for slot in _EVIDENCE_SLOTS if states[slot] != "passing"],
        "incident_refs": incident_refs,
        "recovery_refs": recovery_refs,
        "authority_path": (
            "the bound owner obtains the named evidence through its existing authority; "
            "this projection performs no activation, effect or notification"),
    }

    row: dict[str, _Any] = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": instant.isoformat(),
        "scope": {
            **bound,
            "completion_subject_key": _completion_subject_key(
                bound["workflow_key"], bound["workflow_version"]),
            "owner": owner,
        },
        "state": state,
        "green": state == _GREEN_STATE,
        "state_reason": state_reason,
        "capability_stage": stage,
        "capabilities_retained": retained,
        "capabilities_withdrawn": withdrawn,
        "workflow_truth": (
            {"source": "lib/control_plane_workflow_truth.py", "available": False}
            if truth_row is None else
            {"source": "lib/control_plane_workflow_truth.py", "available": True,
             **{name: truth_row.get(name) for name in _WORKFLOW_TRUTH_PUBLIC_FIELDS}}),
        "evidence": {slot: _public_evidence(slot, states[slot], records.get(slot),
                                            slot_reasons[slot])
                     for slot in _EVIDENCE_SLOTS},
        "preactivation_receipts_passing": preactivation_passing,
        "indeterminate_layers": indeterminate,
        "failing_layers": determinate_nonpass,
        "missing_layers": missing,
        "unbindable_layers": unbindable,
        "capability_stage_attributable_to_findings": attributable_stage,
        "impact": impact,
        "recovery": recovery,
        "reasons": sorted(set(reasons)),
    }

    if row["state"] not in _DISPLAY_STATES:  # pragma: no cover - guards a future edit
        raise _AssuranceHealthContractError(
            f"state {row['state']!r} is outside the closed vocabulary")
    if row["capability_stage"] not in _CAPABILITY_STAGES:  # pragma: no cover
        raise _AssuranceHealthContractError(
            f"capability stage {row['capability_stage']!r} is outside the closed vocabulary")
    for slot, public in row["evidence"].items():  # pragma: no cover
        if public["state"] not in _EVIDENCE_STATES:
            raise _AssuranceHealthContractError(
                f"{slot} evidence state {public['state']!r} is outside the vocabulary")
    return row


def _label_predicate(*, scopes: _Any, now: _Any,
                     unreadable_reasons: _Any = None) -> dict[str, _Any]:
    """THE PURE LABEL PREDICATE over a census. NOT WIRED (see the row predicate).

    Each entry is ``{"scope": ..., "workflow_truth": ..., "evidence": ...}``.  Rows
    are computed independently of one another: a failure in one scope has no route
    by which it could alter another scope's evidence-derived state.
    """
    instant = _utc(now, field="now")
    if not isinstance(scopes, list):
        raise _AssuranceHealthContractError("scopes must be a list of bound scope bundles")

    rows = []
    seen: set[tuple[str, int, str]] = set()
    for entry in scopes:
        bundle = _require_mapping(entry, field="scope bundle")
        for name in ("scope", "workflow_truth", "evidence"):
            if name not in bundle:
                raise _AssuranceHealthContractError(f"scope bundle is missing {name!r}")
        identity = _scope_identity(bundle["scope"])
        key = (identity["workflow_key"], identity["workflow_version"],
               identity["work_request_id"])
        if key in seen:
            raise _AssuranceHealthContractError(
                f"scope {key} is bound twice; one exact scope has one projected state")
        seen.add(key)
        rows.append(_label_predicate_row(
            scope=bundle["scope"], workflow_truth=bundle["workflow_truth"],
            evidence=bundle["evidence"], now=instant,
            unreadable_reasons=unreadable_reasons))

    # An unbound scope sorts before its Work Request-bound siblings; None is not
    # orderable against a string, so the absence is spelled out rather than
    # crashing the whole census on one unbound row.
    rows.sort(key=lambda row: (row["scope"]["workflow_key"], row["scope"]["workflow_version"],
                               row["scope"]["work_request_id"] is not None,
                               row["scope"]["work_request_id"] or ""))
    summary = {
        "scopes": len(rows),
        "states": {state: sum(1 for row in rows if row["state"] == state)
                   for state in _DISPLAY_STATES},
        "capability_stages": {stage: sum(1 for row in rows if row["capability_stage"] == stage)
                              for stage in _CAPABILITY_STAGES},
        "green": sum(1 for row in rows if row["green"]),
        # KEYED ON THE IDENTITY EVERY SCOPE ACTUALLY HAS. A workflow-only scope
        # carries no Work Request identity, and a census of them is the normal
        # case on a real surface -- naming the list by an absent field made two
        # degraded scopes unsortable against each other.
        "degraded_scopes": sorted(
            f"{row['scope']['workflow_key']}@v{row['scope']['workflow_version']}"
            + (f"/{row['scope']['work_request_id']}"
               if row["scope"]["work_request_id"] else "")
            for row in rows if row["state"] in ("degraded", "failed")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": instant.isoformat(),
        "states": list(_DISPLAY_STATES),
        "capability_stages": list(_CAPABILITY_STAGES),
        "evidence_slots": list(_EVIDENCE_SLOTS),
        "workflow_truth_source": "lib/control_plane_workflow_truth.py",
        "rows": rows,
        "summary": summary,
    }


# =============================================================================
# THE PUBLIC SURFACE — evidence enters only through a registered evidence owner
# =============================================================================
#
# WHY THIS SECTION EXISTS.  Everything above is a PREDICATE: it decides what a
# set of evidence shapes means.  It verifies no receipt, because verifying a
# receipt means reading the store that owns it, and this module reads nothing.
# A predicate exposed directly as a public surface is a direct-green route: six
# caller-supplied dictionaries of the right shape would render ``healthy``, and a
# caller-supplied string is never authority for a health label a human reads.
#
# So the public surface is here, and it enforces ONE rule:
#
#     A LAYER IS PASSING ONLY IF A REGISTERED EVIDENCE OWNER IN THIS REPOSITORY
#     ADMITTED IT.  Everything else -- every mapping, flag, holder or object a
#     caller can construct -- is _UNREADABLE, with the owed seam named.
#
# AND THERE ARE NO REGISTERED EVIDENCE OWNERS.  ``_EVIDENCE_OWNERS`` is empty, and
# that is the honest state of this repository rather than an oversight.  An
# admission function can only stand on a READ THIS MODULE PERFORMED: a receipt
# object handed in by a caller is the caller's assertion about a store, not the
# store.  Every candidate route was checked and each one ends at a caller:
#
#   * this module is pure by contract -- it opens no file, runs no process and
#     holds no database route, which is what makes its verdicts reproducible;
#   * ``lib/control_plane_workflow_truth`` (V5-F09) is the only workflow
#     authority, and it too "reads nothing, writes nothing, holds no authority":
#     its rows are projections of rows ITS caller read;
#   * the one real read of ``ops.legacy_schedule_observation_receipt`` in this
#     repository is a SQL query inside ``tools/health-check.py``, whose result
#     reaches this module as an argument like any other.
#
# An earlier cut of this module admitted a controller readback from an F09-SHAPED
# ROW passed in by its caller, and a review reproduced ``controller_assessment:
# passing`` from data a caller wrote by hand.  Shape was standing in for
# authority.  So that admission function is gone rather than renamed: until a
# reader inside this seam performs the read itself, the controller layer -- like
# the other five -- is _UNREADABLE, and the surface prints what it does not know.

# slot -> the authoritative source that owns it and the function that admits it.
# EMPTY BY CONSTRUCTION: see above.  A future entry here is an admission point,
# so adding one means adding a read this module performs, not a shape it trusts.
_EVIDENCE_OWNERS: dict[str, str] = {}

# slot -> the durable evidence-owner seam that is OWED before it could ever pass.
# Naming it is the difference between an honest gap and a silent one.
_OWED_EVIDENCE_OWNER_SEAMS: dict[str, str] = {
    "artifact_assessment":
        "a durable independent-artifact-review store that can be read back by "
        "repository commit and tree and that verifies the reviewer identity",
    "execution_assessment":
        "a durable attempt-receipt store that verifies the envelope digest and "
        "plan hash against the attempt it claims",
    "controller_assessment":
        "a surface that VERIFIES a reading of ops.legacy_schedule_observation_receipt "
        "as the live state of that scheduler; lib/control_plane_workflow_truth_reader "
        "does query that table and tools/health-check.py renders what it read, but "
        "reading a receipt row is not the same act as vouching that it is current, and "
        "a readback reaching this module is still an argument rather than that vouching",
    "candidate_outcome_oracle":
        "a durable preactivation candidate-outcome oracle store that verifies the "
        "governed data, environment, comparator, component versions and its own TTL",
    "activation_readback":
        "a durable activation store that can return the controller readback taken "
        "after a named activation id",
    "actual_business_outcome":
        "a durable accepted sourced outcome-feedback store that verifies the "
        "acceptance receipt and joins through an exact Work Request identity",
}


def _admitted_only(evidence: _Any) -> tuple[dict[str, _Any], dict[str, str]]:
    """Reduce a caller's evidence mapping to what a registered owner admitted.

    Returns the evidence the predicate may see, plus the reason each downgraded
    layer is unreadable.  ``_UNREADABLE`` and ``None`` pass through unchanged --
    they are the caller's own honest statements about a read, not claims about a
    receipt -- and EVERY OTHER VALUE becomes ``_UNREADABLE``, because no evidence
    owner is registered and therefore nothing is admissible.  A mapping, a dict
    subclass, a flag, an object wearing an admitted shape and a receipt this
    module itself once minted are all the same thing here: a caller's assertion.
    """
    supplied = _require_mapping(evidence, field="evidence")
    admitted: dict[str, _Any] = {}
    reasons: dict[str, str] = {}
    for slot, value in supplied.items():
        if value is _UNREADABLE or value is None:
            admitted[slot] = value
            continue
        admitted[slot] = _UNREADABLE
        owner = _EVIDENCE_OWNERS.get(slot)
        if owner:  # pragma: no cover - _EVIDENCE_OWNERS is empty by construction
            reasons[slot] = (
                f"{slot}: supplied evidence is not authority — this layer has an evidence "
                f"owner ({owner}) and only that owner's own reading can make it readable")
            continue
        owed = _OWED_EVIDENCE_OWNER_SEAMS.get(slot, "a durable store that owns this fact")
        reasons[slot] = (
            f"{slot}: supplied evidence is not authority — this layer has no evidence "
            f"owner in this repository that could verify its refs, digest, evaluator "
            f"identity, status or expiry, so it is unread until one exists ({owed})")
    return admitted, reasons


def _assurance_health_row(*, scope: _Any, workflow_truth: _Any, evidence: _Any,
                         now: _Any) -> dict[str, _Any]:
    """THE PUBLIC single-scope projection.  There is no other way in.

    Identical to the module-private predicate except that evidence no registered
    owner admitted is reported ``unreadable`` with the owed seam named -- and no
    owner is registered, so every supplied record is unreadable and no
    caller-supplied shape can reach ``passing``, ``act`` or green.
    """
    admitted, reasons = _admitted_only(evidence)
    row = _label_predicate_row(scope=scope, workflow_truth=workflow_truth,
                               evidence=admitted, now=now,
                               unreadable_reasons=reasons)
    _refuse_unowned_green(row)
    return row


def _assurance_health(*, scopes: _Any, now: _Any) -> dict[str, _Any]:
    """THE PUBLIC census projection.  There is no other way in."""
    if not isinstance(scopes, list):
        raise _AssuranceHealthContractError("scopes must be a list of bound scope bundles")
    admitted_scopes = []
    reasons: dict[str, str] = {}
    for entry in scopes:
        bundle = _require_mapping(entry, field="scope bundle")
        if "evidence" not in bundle:
            raise _AssuranceHealthContractError("scope bundle is missing 'evidence'")
        admitted, row_reasons = _admitted_only(bundle["evidence"])
        reasons.update(row_reasons)
        admitted_scopes.append({**bundle, "evidence": admitted})
    projection = _label_predicate(scopes=admitted_scopes, now=now,
                                  unreadable_reasons=reasons)
    for row in projection["rows"]:
        _refuse_unowned_green(row)
    return projection


def _refuse_unowned_green(row: dict[str, _Any]) -> None:
    """Green on the public surface requires six OWNED passing layers.

    Unreachable while NO layer has an owner, and stated as an invariant anyway:
    this is the exact outcome a future edit must not reopen by accident, and a
    raised refusal is the only honest thing to print in its place.
    """
    unowned_passing = sorted(
        slot for slot in _EVIDENCE_SLOTS
        if row["evidence"][slot]["state"] == "passing" and slot not in _EVIDENCE_OWNERS)
    if unowned_passing:  # pragma: no cover - no admission point exists for any slot
        raise _AssuranceHealthContractError(
            f"{unowned_passing} reached passing with no registered evidence owner")
    if row["green"] or row["capability_stage"] == "act":  # pragma: no cover - same
        raise _AssuranceHealthContractError(
            "a public assurance-health row reached green without six owned passing layers")


# =============================================================================
# THE TEST-ONLY HOOK — not public, and it returns a hypothetical, not a label
# =============================================================================
#
# The acceptance suite has to exercise the label logic against fixture shapes,
# INCLUDING the six-passing shape, because that is the only way to prove the
# ladder really requires all six layers.  Exporting a predicate to make that
# possible would put a direct-green route back on the public surface under a new
# name, so the suite reaches the classifier here instead.  Two properties make
# this not a route:
#
#   1. neither hook is in ``__all__``, and both are underscore-private, so no
#      consumer imports them as part of this module's surface; and
#   2. the verdict comes back under ``would_be_healthy_if_authoritative`` -- a
#      question about a hypothetical world in which the supplied shapes were
#      admitted by an owner that does not exist.  Nothing that renders a state
#      to a human may read that key, and nothing in this repository does.


def _test_only_hypothetical_row(*, scope: _Any, workflow_truth: _Any, evidence: _Any,
                                now: _Any,
                                unreadable_reasons: _Any = None) -> dict[str, _Any]:
    """TEST-ONLY. What the label logic WOULD say if these shapes were authority."""
    return {"would_be_healthy_if_authoritative": _label_predicate_row(
        scope=scope, workflow_truth=workflow_truth, evidence=evidence, now=now,
        unreadable_reasons=unreadable_reasons)}


def _test_only_hypothetical_census(*, scopes: _Any, now: _Any,
                                   unreadable_reasons: _Any = None) -> dict[str, _Any]:
    """TEST-ONLY. The same hypothetical over a census of bound scopes."""
    return {"would_be_healthy_if_authoritative": _label_predicate(
        scopes=scopes, now=now, unreadable_reasons=unreadable_reasons)}
