"""Shared migration-slot policy for the allocator, runner, and CI.

Migration identity in PostgreSQL is the full filename. Numeric slots are still
globally allocated so concurrent work cannot create ambiguous history. Historical
exceptions remain frozen here by their exact filename sets. A separately named,
approved interstitial pair is kept out of that historical register.
"""
from __future__ import annotations

import re
from collections.abc import Iterable


SLOT_RE = re.compile(r"^(\d{4})[a-z]?_[a-z0-9_]+\.sql$")

# WR120 burned 0533/0534 and WR122's withdrawn contract burned 0535/0536.
# They are reservations without files: no allocator or explicit reservation may
# ever hand one back, and no ledger alias may make one executable later.
PERMANENTLY_BURNED_MIGRATION_SLOTS: dict[int, str] = {
    533: "WR120 withdrawn 0533 slot",
    534: "WR120 withdrawn 0534 slot",
    535: "WR122 stale ready-plan amendment",
    536: "WR122 stale ready-plan SCAC successor",
}
FROZEN_COLLISIONS: dict[str, tuple[str, ...]] = {
    "0013": (
        "0013_active_book_derived.sql",
        "0013a_historical_client_status_vocabulary.sql",
    ),
    "0074": (
        "0074_deal_city_lane.sql",
        "0074_outside_model_actors.sql",
    ),
    "0078": (
        "0078_exporter_doctrine_select.sql",
        "0078_writer_participant_role_grant.sql",
    ),
    "0079": (
        "0079_deal_room_api.sql",
        "0079_review_clock_backfill.sql",
    ),
    "0080": (
        "0080_deal_room_board_view.sql",
        "0080_reader_briefing_grants.sql",
    ),
    "0081": (
        "0081_capture_bridge.sql",
        "0081_loop_blocker.sql",
    ),
    "0095": (
        "0095_sponsor_runtime_audit.sql",
        "0095_vendor_lookup_grants.sql",
    ),
    "0169": (
        "0169_control_plane_canary_fencing.sql",
        "0169_hermes_pilot_actor.sql",
        "0169_program5_release_binding.sql",
    ),
}

# Approved by codex-compaction-continuity-design
# ef786b17-b695-4d0d-8d15-604e0b02ef24@6,
# sha256:341ccee0fb477acbca0a6a9015fe8405e81560f77d50f839cdbb29b4c7f1a936.
# This is a reviewed forward release pair, not historical migration history.
# Its lettered member may appear only with the exact base member. The allocator
# admits origin/main's predecessor state (the base member alone) only while the
# pair is awaiting its approved merge; every checked worktree must carry both.
APPROVED_INTERSTITIAL_COLLISIONS: dict[str, tuple[str, ...]] = {
    "0494": (
        "0494_codex_continuity_archive_registry.sql",
        "0494a_codex_continuity_reference_manifest.sql",
    ),
    # WR-000106 / PLAN-f9c7165052ad-v2. Production already carries the
    # 0507 base migration; this exact reviewed validator companion is the only
    # permitted lettered member of the slot.
    "0507": (
        "0507_export_views_one_row_per_subject.sql",
        "0507a_engineering_slice_plan_validators.sql",
    ),
    # WR-000125 / PLAN-b9bd96dd692c-v1. Production already carries the v35
    # registry successor at 0532. The two exact lettered members are the only
    # approved continuation of that slot and themselves form one no-split
    # database transaction.
    "0532": (
        "0532_room_dispatch_spine_scac_successor.sql",
        "0532a_canonical_ownership_lease_activation.sql",
        "0532b_ready_plan_amendment_scac_successor.sql",
    ),
}

# These twelve filenames were applied to isolated Control Plane staging before
# that branch was renumbered. They are absent from the repository by design;
# their mapped forward migrations are idempotent convergence files and must
# remain present. No other missing ledger filename is grandfathered.
LEGACY_APPLIED_ALIASES: dict[str, str] = {
    "0134_control_plane_admission.sql": "0148_control_plane_admission.sql",
    "0135_control_plane_jobs.sql": "0149_control_plane_jobs.sql",
    "0136_control_plane_job_fixes.sql": "0150_control_plane_job_fixes.sql",
    "0137_control_plane_admission_grants.sql": "0151_control_plane_admission_grants.sql",
    "0138_rule_writer_grants.sql": "0152_rule_writer_grants.sql",
    "0139_control_plane_resilience.sql": "0153_control_plane_resilience.sql",
    "0140_control_plane_cost_release.sql": "0154_control_plane_cost_release.sql",
    "0141_rule_applicability_wildcard.sql": "0155_rule_applicability_wildcard.sql",
    "0142_control_plane_input_grants.sql": "0156_control_plane_input_grants.sql",
    "0143_control_plane_runtime_guards.sql": "0157_control_plane_runtime_guards.sql",
    "0144_job_timeout_receipts.sql": "0158_job_timeout_receipts.sql",
    "0145_control_plane_evidence_grants.sql": "0159_control_plane_evidence_grants.sql",
}


class MigrationNumberError(ValueError):
    """The migration tree violates the global slot contract."""


def collision_report(names: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Return lexical migration slots claimed by more than one filename."""
    claims: dict[str, set[str]] = {}
    for name in names:
        match = SLOT_RE.match(name)
        if match:
            claims.setdefault(match.group(1), set()).add(name)
    return {
        slot: tuple(sorted(slot_names))
        for slot, slot_names in sorted(claims.items())
        if len(slot_names) > 1
    }


def validate_migration_names(
    names: Iterable[str], *, require_frozen: bool = False,
    allow_frozen_subset: bool = False,
    allow_approved_interstitial_base: bool = False,
) -> None:
    """Allow exact historical collisions and the approved interstitial pair."""
    materialized = tuple(names)
    for name in materialized:
        match = SLOT_RE.match(name)
        if not match:
            continue
        slot = match.group(1)
        burned_reason = PERMANENTLY_BURNED_MIGRATION_SLOTS.get(int(slot))
        if burned_reason is not None:
            raise MigrationNumberError(
                f"permanently burned migration slot {slot} cannot be reused: "
                f"{name} ({burned_reason})"
            )
        known_names = FROZEN_COLLISIONS.get(slot)
        label = "frozen"
        if known_names is None:
            known_names = APPROVED_INTERSTITIAL_COLLISIONS.get(slot)
            label = "approved interstitial"
        if known_names is not None:
            if name not in known_names:
                raise MigrationNumberError(
                    f"{label} collision {slot} changed: "
                    f"unexpected filename {name}"
                )
    for slot, slot_names in collision_report(materialized).items():
        registered_names = FROZEN_COLLISIONS.get(slot)
        label = "frozen"
        if registered_names is None:
            registered_names = APPROVED_INTERSTITIAL_COLLISIONS.get(slot)
            label = "approved interstitial"
        if registered_names is None:
            raise MigrationNumberError(
                f"unregistered collision {slot}: {', '.join(slot_names)}; "
                "allocate a new migration number"
            )
        if slot_names != registered_names and not (
            allow_frozen_subset
            and label == "frozen"
            and set(slot_names).issubset(registered_names)
        ):
            raise MigrationNumberError(
                f"{label} collision {slot} changed: expected {', '.join(registered_names)}; "
                f"found {', '.join(slot_names)}"
            )
    present_names = set(materialized)
    for slot, interstitial in APPROVED_INTERSTITIAL_COLLISIONS.items():
        present = tuple(name for name in interstitial if name in present_names)
        if present and present != interstitial and not (
            allow_approved_interstitial_base and present == interstitial[:1]
        ):
            raise MigrationNumberError(
                f"approved interstitial collision {slot} changed: "
                f"expected {', '.join(interstitial)}; "
                f"found {', '.join(present)}"
            )
    if require_frozen:
        for slot, frozen in FROZEN_COLLISIONS.items():
            present = tuple(name for name in frozen if name in present_names)
            if present != frozen:
                raise MigrationNumberError(
                    f"frozen collision {slot} changed: expected {', '.join(frozen)}; "
                    f"found {', '.join(present) if present else 'none'}"
                )


def allocate_integration_successors(
    main_names: Iterable[str], pending_names: Iterable[str],
) -> dict[str, str]:
    """Allocate a lexical pending batch only after its integration base is pinned.

    Peer worktrees are deliberately absent: the serialized integration owner
    allocates against delivered main, rather than reserving draft numbers that
    cause every contender to move repeatedly. Applied filenames never move.
    """
    main = tuple(main_names)
    validate_migration_names(main, allow_approved_interstitial_base=True)
    pending = tuple(pending_names)
    if len(set(pending)) != len(pending):
        raise MigrationNumberError("duplicate pending migration identity")
    if any(not re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", n) for n in pending):
        raise MigrationNumberError("pending successors require ordinary numeric filenames")
    if set(pending).intersection(main):
        raise MigrationNumberError("applied main migration cannot be reallocated")
    highest = max((int(match.group(1)) for n in main if (match := SLOT_RE.fullmatch(n))), default=0)
    result = {}
    for name in sorted(pending):
        highest += 1
        while highest in PERMANENTLY_BURNED_MIGRATION_SLOTS:
            highest += 1
        if highest > 9999:
            raise MigrationNumberError("migration slot namespace exhausted")
        result[name] = f"{highest:04d}{name[4:]}"
    if len(set(result.values())) != len(result):
        raise MigrationNumberError("pending successor names are ambiguous")
    validate_migration_names([*main, *result.values()], allow_approved_interstitial_base=True)
    return result


def allocate_registry_successor(main_versions: Iterable[int]) -> tuple[int, int]:
    """Return the exact current-main predecessor and its immediate successor."""
    versions = tuple(main_versions)
    if not versions or any(type(v) is not int or v < 1 for v in versions):
        raise MigrationNumberError("current-main registry predecessor is missing or invalid")
    predecessor = max(versions)
    return predecessor, predecessor + 1


def validate_integration_union(main: dict[str, str] | dict[str, bytes], candidate: dict[str, str] | dict[str, bytes]) -> None:
    """Compare immutable main hashes/bytes before checking the union's ordering."""
    for name, content in main.items():
        if candidate.get(name) != content:
            raise MigrationNumberError(f"current-main migration missing or edited: {name}")
    validate_migration_names(candidate, allow_approved_interstitial_base=True)
    highest = max((int(match.group(1)) for n in main if (match := SLOT_RE.fullmatch(n))), default=0)
    for name in candidate.keys() - main.keys():
        match = SLOT_RE.fullmatch(name)
        if not match:
            raise MigrationNumberError(f"invalid pending migration: {name}")
        if int(match.group(1)) <= highest:
            raise MigrationNumberError(f"pending migration is not a forward successor: {name}")
