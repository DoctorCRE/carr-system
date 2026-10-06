"""Pure row-to-truth adapter for the clean-start workflow census (V5-F09).

WHAT THIS IS. A deterministic function from already-authoritative rows to one
closed ``state`` and one closed ``disposition`` per workflow.  It reads nothing,
writes nothing, holds no authority, and is not a registry: every input is
supplied by a caller that read an existing authoritative surface, and every
output is a projection of those rows.

WHAT IT DELIBERATELY IS NOT.

* It is not an admission path.  ``ops.enqueue_job`` (migrations 0149 + 0334) is
  the only route into ``ops.job``, and this module never widens it.  The
  ``admissible_modes`` field mirrors that function's ladder exactly so a test
  can prove the projection is never MORE permissive than the database; a
  projection label can therefore never weaken the canary/live evidence fence.
* It is not an effect.  ``disable_false_operational`` and ``retire_duplicate``
  are REQUESTED dispositions.  Actually disabling a native scheduler still runs
  through ``ops.disable_legacy_schedule`` with Joe authority and its immutable
  enabled-to-disabled observation receipts (migrations 0176/0180/0182/0184).
  Nothing here can claim a native surface stopped firing.
* It invents no freshness.  ``ops.workflow_acceptance`` (0149 lines 195-211) has
  no expiry column, so acceptance evidence is never reported ``stale``.  The one
  place staleness is real is a native scheduler observation, and the window used
  is the registry's own ``observation_max_age_seconds`` -- the same value
  ``lib/control_plane_scheduler_cutover._require_observation`` enforces.
  Completion staleness comes from ``ops.completion_projection`` itself, which
  already derives ``unknown_stale`` from ``expires_at`` (migration 0431).

FAIL-CLOSED. An input the caller could not read is passed as ``UNREADABLE`` and
produces ``state='unknown'`` with ``disposition='hold_for_disposition'``.  A
malformed input raises ``WorkflowTruthContractError`` rather than being guessed
at.  Absent classification is ``hold_for_disposition``, never a repair claim.

THE DUPLICATE BOUNDARY, which is the correction this module exists to carry.
``ops.enqueue_job`` collapses two deliveries of the SAME workflow identity into
one canonical job (unique ``definition_key,definition_version,scheduled_for``
plus a unique idempotency key).  That is retry deduplication and nothing more.
It does NOT prevent two DISTINCT registered workflow identities that share one
``duplicate_group`` from each producing an executable job, because their
definition keys differ and no unique index spans the group.  Both cases are
modelled separately in ``duplicate_exclusion`` and must never be conflated:

    same_slot_idempotency  -- the unique canonical-slot index, inside ops.enqueue_job
    distinct_identity      -- the group-scoped exclusion migration 0498 added to
                              that SAME function: an advisory transaction lock on
                              the exact duplicate_group and canonical slot, then a
                              refusal when another identity in the group already
                              holds an executable job for it. Concurrent callers
                              serialize on the lock, so the loser observes the
                              winner's committed row instead of racing it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA_VERSION = "control-plane-workflow-truth.v1"

# A caller that could not read an authoritative surface passes this sentinel.
# ``None`` means "read it, and there is genuinely no row"; UNREADABLE means "the
# read did not happen or failed", which is a different fact and fails closed.
UNREADABLE = "unreadable"

# Ordered most-conservative first.  The first predicate that holds wins, so a
# contradiction or an unreadable input can never be outranked by a positive
# signal further down the list.
STATES = (
    "unknown",                  # a required authoritative input was unreadable
    "conflict",                 # authoritative evidence contradicts itself
    "undeclared",               # a registered definition the manifest never declared
    "unregistered",             # declared, but no ops.job_definition row exists
    "declared_disabled",        # the definition exists and is not enabled
    "enabled_shadow_only",      # enabled; only a shadow evidence run is admissible
    "enabled_canary_eligible",  # accepted shadow; canary admissible, live is not
    "enabled_live_eligible",    # live admissible; no operational completion evidence
    "operational",              # live admissible AND completion evidence says operational
)

DISPOSITIONS = (
    "hold_for_disposition",       # the default; absent classification is never a claim
    "admit_via_governed_lifecycle",
    "retire_duplicate",           # requested only; 0184 receipt performs it
    "disable_false_operational",  # requested only; 0176/0184 receipt performs it
    "repair_first_path",          # only from a bound first-path evidence relation
    "retain_operational",
)

EVIDENCE_STATES = (
    "accepted",
    "observed",
    "rejected",
    "conflicting",
    "stale",
    "missing",
    "not_required",
)

ACCEPTANCE_MODES = ("shadow", "canary")
ACCEPTANCE_STATUSES = ("observed", "accepted", "rejected")
SCHEDULER_KINDS = ("launchd", "claude-code")
NATIVE_SCHEDULER_STATES = ("enabled", "disabled")

# ops.completion_projection lifecycle states this projection consumes.  Any
# other value is carried through as "observed" evidence and never promoted.
COMPLETION_CONFLICTING = "conflicting"
COMPLETION_STALE = "unknown_stale"
COMPLETION_OPERATIONAL = "operational"

# The completion subject key convention.  This is a lookup convention over the
# existing register, not a new identity store: ops.completion_subject.stable_key
# is checked against '^[a-z0-9][a-z0-9:._/-]{0,239}$' by migration 0431.
def completion_subject_key(workflow_key: str, workflow_version: int) -> str:
    return f"workflow:{workflow_key}:v{int(workflow_version)}"


class WorkflowTruthContractError(ValueError):
    """A caller supplied an input this adapter refuses to guess at."""


def _utc(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise WorkflowTruthContractError(f"{field} must be an ISO-8601 instant")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkflowTruthContractError(f"{field} is not an ISO-8601 instant: {exc}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _require_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowTruthContractError(f"{field} must be an object")
    return value


def _canary_contractually_disabled(execution_contract: Any) -> bool:
    """Mirror 0334: only an EXPLICIT canary.enabled=false is a contractual refusal.

    A missing canary key -- every cognition contract, and any deterministic
    contract that never named one -- is not the same claim, and 0334 says so in
    its own comment.  Getting this wrong in either direction would make the
    projection disagree with the function that actually admits work.
    """
    if not isinstance(execution_contract, dict):
        return False
    canary = execution_contract.get("canary")
    return isinstance(canary, dict) and canary.get("enabled") is False


def _acceptance_evidence(rows: list[dict[str, Any]], mode: str) -> str:
    """Classify ops.workflow_acceptance rows for one mode.

    NEVER 'stale': migration 0149 gives ops.workflow_acceptance created_at and no
    expiry, and 0334 reads it with no time bound.  Inventing a wall-clock expiry
    here would make the projection refuse work the database admits.
    """
    statuses = {str(row.get("status")) for row in rows if row.get("mode") == mode}
    if not statuses:
        return "missing"
    accepted = "accepted" in statuses
    rejected = "rejected" in statuses
    if accepted and rejected:
        return "conflicting"
    if accepted:
        return "accepted"
    if rejected:
        return "rejected"
    return "observed"


def _native_evidence(surfaces: list[dict[str, Any]], *, provider: str,
                     max_age_seconds: int, now: datetime) -> tuple[str, str | None]:
    """Classify native scheduler observation evidence for a workflow's surfaces.

    The freshness window is the checked-in registry's own
    ``observation_max_age_seconds``; this module defines no window of its own.
    """
    if not surfaces:
        return ("not_required" if provider == "none" else "missing", None)
    states: set[str] = set()
    freshest: datetime | None = None
    stale_seen = False
    missing_seen = False
    for surface in surfaces:
        observation = surface.get("observation")
        if observation is None:
            missing_seen = True
            continue
        observation = _require_mapping(observation, field="surface observation")
        state = str(observation.get("scheduler_state"))
        if state not in NATIVE_SCHEDULER_STATES:
            raise WorkflowTruthContractError(
                f"scheduler observation state must be one of {NATIVE_SCHEDULER_STATES}")
        observed_at = _utc(observation.get("observed_at"), field="observation.observed_at")
        if now - observed_at > timedelta(seconds=max_age_seconds):
            stale_seen = True
            continue
        states.add(state)
        if freshest is None or observed_at > freshest:
            freshest = observed_at
    if len(states) > 1:
        # Two registered surfaces of one workflow disagree about whether the
        # native schedule is live. That is a contradiction, not a majority vote.
        return "conflicting", None
    if missing_seen and not states:
        return "missing", None
    if stale_seen and not states:
        return "stale", None
    if not states:
        return "missing", None
    return ("observed" if not (stale_seen or missing_seen) else "stale"), states.pop()


def _completion_evidence(completion: Any) -> tuple[str, str | None, bool]:
    """Classify the ops.completion_projection row for this workflow's subject.

    Returns (evidence_state, lifecycle_state, first_path_bound).  ``first_path``
    is read only from the caller's bound register relation; when the register
    said nothing, it is False and the disposition falls to hold.
    """
    if completion is UNREADABLE:
        return "missing", None, False
    if completion is None:
        return "missing", None, False
    completion = _require_mapping(completion, field="completion")
    lifecycle = completion.get("lifecycle_state")
    if lifecycle is not None and not isinstance(lifecycle, str):
        raise WorkflowTruthContractError("completion.lifecycle_state must be a string")
    first_path = completion.get("first_path")
    if not isinstance(first_path, bool):
        raise WorkflowTruthContractError(
            "completion.first_path must be an explicit boolean from a bound register relation")
    if lifecycle is None:
        return "missing", None, first_path
    if lifecycle == COMPLETION_CONFLICTING:
        return "conflicting", lifecycle, first_path
    if lifecycle == COMPLETION_STALE:
        return "stale", lifecycle, first_path
    if lifecycle == COMPLETION_OPERATIONAL:
        return "accepted", lifecycle, first_path
    return "observed", lifecycle, first_path


def workflow_truth_row(
    *,
    workflow_key: str,
    workflow_version: int,
    declaration: Any,
    definition: Any,
    acceptances: Any,
    surfaces: Any,
    completion: Any,
    duplicate_group_identities: Any,
    observation_max_age_seconds: int,
    now: Any,
) -> dict[str, Any]:
    """Project one workflow's authoritative rows onto one state and disposition.

    Every argument is required and keyword-only: a census that forgot to read a
    surface must say so with UNREADABLE rather than inherit a permissive default.
    """
    if not isinstance(workflow_key, str) or not workflow_key.strip():
        raise WorkflowTruthContractError("workflow_key is required")
    if not isinstance(workflow_version, int) or isinstance(workflow_version, bool) \
            or workflow_version <= 0:
        raise WorkflowTruthContractError("workflow_version must be a positive integer")
    if not isinstance(observation_max_age_seconds, int) or observation_max_age_seconds <= 0:
        raise WorkflowTruthContractError(
            "observation_max_age_seconds must come from the checked-in scheduler registry")
    instant = _utc(now, field="now")

    # TWO KINDS OF UNREADABLE INPUT, and collapsing them would make the census
    # useless in the ordinary case.  The declaration, the definition and the
    # acceptance rows decide the governed LADDER, so losing one of them makes the
    # state unknowable.  The scheduler surfaces and the Completion Register
    # decide what should be DONE about a workflow; losing one of those still
    # leaves the ladder readable, caps promotion below operational, and forces
    # the disposition to hold rather than inventing an action from absence.
    reasons: list[str] = []
    unreadable: list[str] = []
    disposition_unreadable: list[str] = []

    if declaration is UNREADABLE:
        unreadable.append("declaration")
        declaration_row: dict[str, Any] | None = None
    elif declaration is None:
        declaration_row = None
    else:
        declaration_row = _require_mapping(declaration, field="declaration")

    if definition is UNREADABLE:
        unreadable.append("definition")
        definition_row: dict[str, Any] | None = None
    elif definition is None:
        definition_row = None
    else:
        definition_row = _require_mapping(definition, field="definition")
        if str(definition_row.get("key")) != workflow_key \
                or int(definition_row.get("version", -1)) != workflow_version:
            raise WorkflowTruthContractError(
                "definition row does not bind the workflow key/version being projected")

    if acceptances is UNREADABLE:
        unreadable.append("acceptances")
        acceptance_rows: list[dict[str, Any]] = []
    else:
        if not isinstance(acceptances, list):
            raise WorkflowTruthContractError("acceptances must be a list")
        acceptance_rows = [_require_mapping(row, field="acceptance row") for row in acceptances]
        for row in acceptance_rows:
            if row.get("mode") not in ACCEPTANCE_MODES:
                raise WorkflowTruthContractError(
                    f"acceptance mode must be one of {ACCEPTANCE_MODES}")
            if row.get("status") not in ACCEPTANCE_STATUSES:
                raise WorkflowTruthContractError(
                    f"acceptance status must be one of {ACCEPTANCE_STATUSES}")

    if surfaces is UNREADABLE:
        disposition_unreadable.append("surfaces")
        surface_rows: list[dict[str, Any]] = []
    else:
        if not isinstance(surfaces, list):
            raise WorkflowTruthContractError("surfaces must be a list")
        surface_rows = [_require_mapping(row, field="surface row") for row in surfaces]
        for row in surface_rows:
            if row.get("scheduler_kind") not in SCHEDULER_KINDS:
                raise WorkflowTruthContractError(
                    f"surface scheduler_kind must be one of {SCHEDULER_KINDS}")
            if not isinstance(row.get("surface_id"), str) or not row["surface_id"]:
                raise WorkflowTruthContractError("surface_id is required")

    if completion is UNREADABLE:
        disposition_unreadable.append("completion")
    if not isinstance(duplicate_group_identities, dict):
        raise WorkflowTruthContractError("duplicate_group_identities must be an object")

    legacy_declaration = _require_mapping(
        (declaration_row or {}).get("legacy_schedule", {}), field="declaration.legacy_schedule")
    provider = str(legacy_declaration.get("provider") or "none")
    declared_native_status = str(legacy_declaration.get("status") or "disabled")

    execution_contract = (definition_row or {}).get("execution_contract")
    canary_disabled = _canary_contractually_disabled(execution_contract)
    enabled = bool((definition_row or {}).get("enabled")) if definition_row else False

    shadow_evidence = _acceptance_evidence(acceptance_rows, "shadow")
    canary_evidence = _acceptance_evidence(acceptance_rows, "canary")
    shadow_accepted = shadow_evidence == "accepted"
    canary_accepted = canary_evidence == "accepted"

    # EXACTLY ops.enqueue_job's ladder (0334 lines 44-82).  Stated as the modes
    # the database would admit today, so a test can prove this projection never
    # claims an admission the function refuses.
    canary_admissible = enabled and not canary_disabled and shadow_accepted
    live_admissible = enabled and (shadow_accepted if canary_disabled else canary_accepted)
    admissible_modes = []
    if enabled:
        admissible_modes.append("shadow")
        if canary_admissible:
            admissible_modes.append("canary")
        if live_admissible:
            admissible_modes.append("live")
        # 0334 adds no replay rule; an enabled definition may still replay.
        admissible_modes.append("replay")

    native_evidence, native_state = _native_evidence(
        surface_rows, provider=provider,
        max_age_seconds=observation_max_age_seconds, now=instant)
    legacy_disabled_at = (definition_row or {}).get("legacy_disabled_at")
    native_schedule_enabled = bool(
        declared_native_status.startswith("enabled") and not legacy_disabled_at)

    completion_evidence, completion_lifecycle, first_path_bound = _completion_evidence(completion)

    # DUPLICATE. A registered duplicate_group stays duplicate until the immutable
    # 0184 disable evidence closes it -- a receipt on every surface in the group.
    groups = sorted({str(row["duplicate_group"]) for row in surface_rows
                     if row.get("duplicate_group")})
    duplicate_group = groups[0] if len(groups) == 1 else None
    if len(groups) > 1:
        reasons.append("workflow surfaces span more than one duplicate_group")
    duplicate_open = bool(groups) and any(
        not row.get("disable_receipt_ref")
        for row in surface_rows if row.get("duplicate_group"))

    identities: list[str] = []
    if duplicate_group is not None:
        raw = duplicate_group_identities.get(duplicate_group, [])
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raise WorkflowTruthContractError(
                "duplicate_group_identities values must be sequences of workflow identities")
        identities = sorted({str(value) for value in raw})

    # THE MANDATORY BOUNDARY. Same-slot idempotency is retry deduplication; it
    # says nothing about two registered identities in one group.
    if duplicate_group is None:
        distinct_identity = "not_applicable_no_group"
    elif len(identities) > 1:
        distinct_identity = "enforced_by_ops_enqueue_job_group_exclusion"
        reasons.append(
            f"duplicate_group {duplicate_group} spans {len(identities)} registered workflow "
            "identities; ops.enqueue_job excludes all but one of them per canonical slot")
    else:
        distinct_identity = "not_applicable_single_identity"
    duplicate_exclusion = {
        "same_slot_idempotency": "enforced_by_ops_enqueue_job",
        "distinct_identity": distinct_identity,
        "group_identities": identities,
    }

    # FALSE OPERATIONAL. An enabled definition whose required acceptance is
    # absent cannot reach live; calling that enabled row operational is the
    # untruth Q062 asks the clean start to remove.
    false_operational = bool(enabled and not live_admissible)

    conflicting_evidence = sorted(
        name for name, value in (
            ("shadow_acceptance", shadow_evidence),
            ("canary_acceptance", canary_evidence),
            ("native_schedule", native_evidence),
            ("completion", completion_evidence),
        ) if value == "conflicting")
    if conflicting_evidence:
        reasons.extend(f"{name} evidence is conflicting" for name in conflicting_evidence)

    # ---- state, most conservative predicate first ---------------------------
    if unreadable:
        state = "unknown"
        reasons.append("unreadable authoritative input: " + ", ".join(sorted(unreadable)))
    elif conflicting_evidence or (len(groups) > 1):
        state = "conflict"
    elif declaration_row is None and definition_row is not None:
        state = "undeclared"
        reasons.append("a registered definition that the checked-in manifest does not declare")
    elif definition_row is None:
        state = "unregistered"
        reasons.append("declared in the manifest with no ops.job_definition row")
    elif not enabled:
        state = "declared_disabled"
    elif live_admissible and completion_lifecycle == COMPLETION_OPERATIONAL:
        state = "operational"
    elif live_admissible:
        state = "enabled_live_eligible"
        reasons.append("live admission is permitted; completion evidence does not say operational")
    elif canary_admissible:
        state = "enabled_canary_eligible"
    else:
        state = "enabled_shadow_only"
        reasons.append("only a shadow evidence run is admissible; shadow is not operation")

    if disposition_unreadable:
        reasons.append(
            "unreadable disposition input: " + ", ".join(sorted(disposition_unreadable))
            + "; the ladder state stands, promotion is capped and the action holds")

    # ---- disposition, requested action only ---------------------------------
    requested_effect: dict[str, Any] | None = None
    if state in ("unknown", "conflict") or disposition_unreadable:
        disposition = "hold_for_disposition"
    elif state == "unregistered":
        disposition = "admit_via_governed_lifecycle"
    elif state == "undeclared":
        disposition = "hold_for_disposition"
    elif duplicate_open:
        disposition = "retire_duplicate"
        requested_effect = {
            "action": "retire_duplicate_scheduler_group",
            "authority_path": "ops.disable_legacy_schedule (Joe authority, 0184 sibling receipts)",
            "applied": False,
            "surfaces": sorted(row["surface_id"] for row in surface_rows
                               if row.get("duplicate_group")),
        }
    elif false_operational and native_schedule_enabled:
        disposition = "disable_false_operational"
        requested_effect = {
            "action": "disable_native_schedule",
            "authority_path": "ops.disable_legacy_schedule (Joe authority, 0176/0184 receipts)",
            "applied": False,
            "surfaces": sorted(row["surface_id"] for row in surface_rows),
        }
    elif first_path_bound and state != "operational":
        disposition = "repair_first_path"
    elif state == "operational":
        disposition = "retain_operational"
    else:
        disposition = "hold_for_disposition"
        if not first_path_bound:
            reasons.append(
                "no bound first-path relation in the Completion Register; absent classification "
                "holds rather than claiming repair_first_path")

    row = {
        "schema_version": SCHEMA_VERSION,
        "workflow_key": workflow_key,
        "workflow_version": workflow_version,
        "completion_subject_key": completion_subject_key(workflow_key, workflow_version),
        "state": state,
        "disposition": disposition,
        "declared": declaration_row is not None,
        "registered": definition_row is not None,
        "enabled": enabled,
        "declaration_enabled": (None if declaration_row is None
                                else bool(declaration_row.get("enabled"))),
        "canary_contractually_disabled": canary_disabled,
        "admissible_modes": admissible_modes,
        "operational": state == "operational",
        "false_operational": false_operational,
        "duplicate": bool(groups),
        "duplicate_open": duplicate_open,
        "duplicate_group": duplicate_group,
        "duplicate_exclusion": duplicate_exclusion,
        "native_provider": provider,
        "native_schedule_enabled": native_schedule_enabled,
        "native_scheduler_state": native_state,
        "legacy_disabled_at": legacy_disabled_at,
        "first_path_bound": first_path_bound,
        "completion_lifecycle_state": completion_lifecycle,
        "evidence": {
            "shadow_acceptance": shadow_evidence,
            "canary_acceptance": canary_evidence,
            "native_schedule": native_evidence,
            "completion": completion_evidence,
        },
        "requested_effect": requested_effect,
        "reasons": sorted(set(reasons)),
    }
    if row["state"] not in STATES:  # pragma: no cover - guards a future edit
        raise WorkflowTruthContractError(f"state {row['state']!r} is outside the closed vocabulary")
    if row["disposition"] not in DISPOSITIONS:  # pragma: no cover
        raise WorkflowTruthContractError(
            f"disposition {row['disposition']!r} is outside the closed vocabulary")
    for name, value in row["evidence"].items():
        if value not in EVIDENCE_STATES:  # pragma: no cover
            raise WorkflowTruthContractError(f"{name} evidence {value!r} is outside the vocabulary")
    return row


def workflow_truth(
    *,
    declarations: Any,
    definitions: Any,
    acceptances: Any,
    surfaces: Any,
    completion: Any,
    observation_max_age_seconds: int,
    now: Any,
) -> dict[str, Any]:
    """Project every declared and every registered workflow, in a stable order.

    ``declarations`` are the checked-in manifest workflows; ``definitions`` are
    ops.job_definition rows.  Both sides are covered so a registered workflow the
    manifest never declared is visible rather than silently absent -- Q062 asks
    the clean start to classify EVERY existing workflow, not every declared one.
    """
    instant = _utc(now, field="now")
    if not isinstance(declarations, list):
        raise WorkflowTruthContractError("declarations must be a list")
    declaration_index: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in declarations:
        entry = _require_mapping(entry, field="declaration")
        declaration_index[(str(entry["key"]), int(entry["version"]))] = entry

    definitions_unreadable = definitions is UNREADABLE
    definition_index: dict[tuple[str, int], dict[str, Any]] = {}
    if not definitions_unreadable:
        if not isinstance(definitions, list):
            raise WorkflowTruthContractError("definitions must be a list")
        for entry in definitions:
            entry = _require_mapping(entry, field="definition")
            definition_index[(str(entry["key"]), int(entry["version"]))] = entry

    acceptances_unreadable = acceptances is UNREADABLE
    acceptance_index: dict[tuple[str, int], list[dict[str, Any]]] = {}
    if not acceptances_unreadable:
        if not isinstance(acceptances, list):
            raise WorkflowTruthContractError("acceptances must be a list")
        for entry in acceptances:
            entry = _require_mapping(entry, field="acceptance row")
            key = (str(entry["workflow_key"]), int(entry["workflow_version"]))
            acceptance_index.setdefault(key, []).append(entry)

    surfaces_unreadable = surfaces is UNREADABLE
    surface_index: dict[tuple[str, int], list[dict[str, Any]]] = {}
    duplicate_group_identities: dict[str, set[str]] = {}
    if not surfaces_unreadable:
        if not isinstance(surfaces, list):
            raise WorkflowTruthContractError("surfaces must be a list")
        for entry in surfaces:
            entry = _require_mapping(entry, field="surface row")
            key = (str(entry["workflow_key"]), int(entry["workflow_version"]))
            surface_index.setdefault(key, []).append(entry)
            group = entry.get("duplicate_group")
            if group:
                duplicate_group_identities.setdefault(str(group), set()).add(
                    f"{key[0]}:v{key[1]}")

    completion_unreadable = completion is UNREADABLE
    completion_index: dict[str, dict[str, Any]] = {}
    if not completion_unreadable:
        if not isinstance(completion, dict):
            raise WorkflowTruthContractError(
                "completion must be an object keyed by completion subject key")
        for subject_key, value in completion.items():
            completion_index[str(subject_key)] = _require_mapping(value, field="completion")

    identities = sorted(set(declaration_index) | set(definition_index))
    rows = []
    for workflow_key, workflow_version in identities:
        identity = (workflow_key, workflow_version)
        subject_key = completion_subject_key(workflow_key, workflow_version)
        rows.append(workflow_truth_row(
            workflow_key=workflow_key,
            workflow_version=workflow_version,
            declaration=declaration_index.get(identity),
            definition=(UNREADABLE if definitions_unreadable
                        else definition_index.get(identity)),
            acceptances=(UNREADABLE if acceptances_unreadable
                         else acceptance_index.get(identity, [])),
            surfaces=(UNREADABLE if surfaces_unreadable
                      else surface_index.get(identity, [])),
            completion=(UNREADABLE if completion_unreadable
                        else completion_index.get(subject_key)),
            duplicate_group_identities=duplicate_group_identities,
            observation_max_age_seconds=observation_max_age_seconds,
            now=instant,
        ))

    summary = {
        "workflows": len(rows),
        "states": {state: sum(1 for row in rows if row["state"] == state) for state in STATES},
        "dispositions": {name: sum(1 for row in rows if row["disposition"] == name)
                         for name in DISPOSITIONS},
        "false_operational": sum(1 for row in rows if row["false_operational"]),
        "duplicate_open": sum(1 for row in rows if row["duplicate_open"]),
        "distinct_identity_excluded_groups": sorted({
            row["duplicate_group"] for row in rows
            if row["duplicate_exclusion"]["distinct_identity"]
            == "enforced_by_ops_enqueue_job_group_exclusion" and row["duplicate_group"]}),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": instant.isoformat(),
        "observation_max_age_seconds": observation_max_age_seconds,
        "states": list(STATES),
        "dispositions": list(DISPOSITIONS),
        "rows": rows,
        "summary": summary,
    }
