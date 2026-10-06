#!/usr/bin/env python3
"""Prove frozen migration-number collisions cannot spread or lose a filename."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from migration_number_contract import (  # noqa: E402
    APPROVED_INTERSTITIAL_COLLISIONS,
    FROZEN_COLLISIONS,
    LEGACY_APPLIED_ALIASES,
    MigrationNumberError,
    PERMANENTLY_BURNED_MIGRATION_SLOTS,
    collision_report,
    validate_migration_names,
)
import migrate as migration_runner  # noqa: E402

NEXT_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "next_migration", REPO / "tools" / "next-migration.py"
)
assert NEXT_MIGRATION_SPEC and NEXT_MIGRATION_SPEC.loader
next_migration: Any = importlib.util.module_from_spec(NEXT_MIGRATION_SPEC)
NEXT_MIGRATION_SPEC.loader.exec_module(next_migration)


FROZEN_0169 = (
    "0169_control_plane_canary_fencing.sql",
    "0169_hermes_pilot_actor.sql",
    "0169_program5_release_binding.sql",
)
APPROVED_0494 = (
    "0494_codex_continuity_archive_registry.sql",
    "0494a_codex_continuity_reference_manifest.sql",
)
APPROVED_0507 = (
    "0507_export_views_one_row_per_subject.sql",
    "0507a_engineering_slice_plan_validators.sql",
)
APPROVED_0532 = (
    "0532_room_dispatch_spine_scac_successor.sql",
    "0532a_canonical_ownership_lease_activation.sql",
    "0532b_ready_plan_amendment_scac_successor.sql",
)
EXPECTED_BURNED_SLOTS = {
    533: "WR120 withdrawn 0533 slot",
    534: "WR120 withdrawn 0534 slot",
    535: "WR122 stale ready-plan amendment",
    536: "WR122 stale ready-plan SCAC successor",
}
EXPECTED_LEGACY_ALIASES = {
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


def refuses(names: tuple[str, ...], expected: str, **kwargs: Any) -> None:
    try:
        validate_migration_names(names, **kwargs)
    except MigrationNumberError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"expected migration-number refusal containing {expected!r}")


def allocator_refuses_interstitial(
    actual: tuple[str, ...], names: tuple[str, ...], expected: str,
    interstitial: tuple[str, ...],
) -> None:
    """Exercise the allocator's own-worktree rejection for an incomplete pair."""
    remote_names = [name for name in actual if name != interstitial[1]]
    slot = interstitial[0][:4]
    with tempfile.TemporaryDirectory(prefix=f"migration-number-contract-{slot}-") as tmp:
        migration_dir = Path(tmp) / "migrations"
        migration_dir.mkdir()
        for name in names:
            (migration_dir / name).touch()
        original_run = next_migration.run
        original_worktree_paths = next_migration.worktree_paths
        original_repo = next_migration.REPO
        try:
            next_migration.run = lambda args, cwd=None: (
                "\n".join(f"migrations/{name}" for name in remote_names)
                if args[:3] == ["git", "ls-tree", "--name-only"] else ""
            )
            next_migration.worktree_paths = lambda: [tmp]
            next_migration.REPO = tmp
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = next_migration.main()
        finally:
            next_migration.run = original_run
            next_migration.worktree_paths = original_worktree_paths
            next_migration.REPO = original_repo
        assert rc == 1, (rc, stdout.getvalue(), stderr.getvalue())
        assert expected in stderr.getvalue(), stderr.getvalue()


def main() -> int:
    actual = tuple(path.name for path in (REPO / "migrations").glob("*.sql"))
    expected_next = max(
        max(next_migration.numbers_from_names(actual)),
        max(PERMANENTLY_BURNED_MIGRATION_SLOTS),
    ) + 1
    validate_migration_names(actual, require_frozen=True)

    report = collision_report(actual)
    assert FROZEN_COLLISIONS | APPROVED_INTERSTITIAL_COLLISIONS == report, report
    assert report["0169"] == FROZEN_0169
    assert report["0494"] == APPROVED_0494
    assert report["0507"] == APPROVED_0507
    assert report["0532"] == APPROVED_0532
    assert APPROVED_INTERSTITIAL_COLLISIONS == {
        "0494": APPROVED_0494,
        "0507": APPROVED_0507,
        "0532": APPROVED_0532,
    }
    assert PERMANENTLY_BURNED_MIGRATION_SLOTS == EXPECTED_BURNED_SLOTS
    assert LEGACY_APPLIED_ALIASES == EXPECTED_LEGACY_ALIASES

    refuses(("0171_alpha.sql", "0171_beta.sql"), "unregistered collision 0171")
    refuses(("0170_next.sql", "0170a_escape.sql"), "unregistered collision 0170")
    refuses(FROZEN_0169[:2], "frozen collision 0169 changed")
    refuses(FROZEN_0169 + ("0169_fourth.sql",), "frozen collision 0169 changed")
    refuses(FROZEN_0169 + ("0169a_escape.sql",), "frozen collision 0169 changed")
    refuses(APPROVED_0494[:1], "approved interstitial collision 0494 changed")
    refuses((APPROVED_0494[1],), "approved interstitial collision 0494 changed")
    validate_migration_names(
        APPROVED_0494[:1], allow_approved_interstitial_base=True
    )
    refuses(
        (APPROVED_0494[1],),
        "approved interstitial collision 0494 changed",
        allow_approved_interstitial_base=True,
    )
    refuses(
        APPROVED_0494 + ("0494b_codex_continuity_unapproved.sql",),
        "approved interstitial collision 0494 changed",
    )
    allocator_refuses_interstitial(
        actual,
        tuple(name for name in actual if name != APPROVED_0494[0]),
        "approved interstitial collision 0494 changed",
        APPROVED_0494,
    )
    allocator_refuses_interstitial(
        actual,
        tuple(name for name in actual if name != APPROVED_0494[1]),
        "approved interstitial collision 0494 changed",
        APPROVED_0494,
    )
    allocator_refuses_interstitial(
        actual,
        actual + ("0494b_codex_continuity_unapproved.sql",),
        "approved interstitial collision 0494 changed",
        APPROVED_0494,
    )
    refuses(APPROVED_0507[:1], "approved interstitial collision 0507 changed")
    refuses((APPROVED_0507[1],), "approved interstitial collision 0507 changed")
    validate_migration_names(
        APPROVED_0507[:1], allow_approved_interstitial_base=True
    )
    refuses(
        (APPROVED_0507[1],),
        "approved interstitial collision 0507 changed",
        allow_approved_interstitial_base=True,
    )
    refuses(
        APPROVED_0507 + ("0507b_unapproved.sql",),
        "approved interstitial collision 0507 changed",
    )
    allocator_refuses_interstitial(
        actual,
        tuple(name for name in actual if name != APPROVED_0507[0]),
        "approved interstitial collision 0507 changed",
        APPROVED_0507,
    )
    allocator_refuses_interstitial(
        actual,
        tuple(name for name in actual if name != APPROVED_0507[1]),
        "approved interstitial collision 0507 changed",
        APPROVED_0507,
    )
    allocator_refuses_interstitial(
        actual,
        actual + ("0507b_unapproved.sql",),
        "approved interstitial collision 0507 changed",
        APPROVED_0507,
    )
    refuses(APPROVED_0532[:1], "approved interstitial collision 0532 changed")
    refuses(APPROVED_0532[:2], "approved interstitial collision 0532 changed")
    refuses((APPROVED_0532[1], APPROVED_0532[2]),
            "approved interstitial collision 0532 changed")
    refuses((APPROVED_0532[2],), "approved interstitial collision 0532 changed")
    validate_migration_names(
        APPROVED_0532[:1], allow_approved_interstitial_base=True
    )
    refuses(
        APPROVED_0532[:2],
        "approved interstitial collision 0532 changed",
        allow_approved_interstitial_base=True,
    )
    refuses(
        APPROVED_0532 + ("0532c_unapproved.sql",),
        "approved interstitial collision 0532 changed",
    )
    for missing in APPROVED_0532:
        allocator_refuses_interstitial(
            actual,
            tuple(name for name in actual if name != missing),
            "approved interstitial collision 0532 changed",
            APPROVED_0532,
        )
    for number in EXPECTED_BURNED_SLOTS:
        refuses(
            (f"{number:04d}_reuse.sql",),
            f"permanently burned migration slot {number:04d} cannot be reused",
        )
    missing_frozen = tuple(name for name in actual if name != "0074_deal_city_lane.sql")
    try:
        validate_migration_names(missing_frozen, require_frozen=True)
    except MigrationNumberError as exc:
        assert "frozen collision 0074 changed" in str(exc), str(exc)
    else:
        raise AssertionError("deleting one side of frozen collision 0074 was accepted")

    # The historical lettered repair is a frozen exception inside numeric slot
    # 0013. A new letter suffix cannot create another allocation escape hatch.
    validate_migration_names((
        "0013_active_book_derived.sql",
        "0013a_historical_client_status_vocabulary.sql",
    ))

    loaded = migration_runner.load_migrations()
    loaded_0169 = tuple(name for name, _sql, _digest in loaded if name.startswith("0169_"))
    assert loaded_0169 == FROZEN_0169, loaded_0169
    loaded_digests = {name: digest for name, _sql, digest in loaded}
    pending = migration_runner.pending_migrations(
        loaded,
        {
            "0169_control_plane_canary_fencing.sql":
                loaded_digests["0169_control_plane_canary_fencing.sql"]
        },
    )
    pending_names = {name for name, _sql, _digest in pending}
    assert "0169_control_plane_canary_fencing.sql" not in pending_names
    assert "0169_hermes_pilot_actor.sql" in pending_names
    assert "0169_program5_release_binding.sql" in pending_names

    selected, held_back = migration_runner.migrations_through(
        loaded, pending, "0170_guidance_import_lifecycle.sql"
    )
    selected_names = [name for name, _sql, _digest in selected]
    held_back_names = [name for name, _sql, _digest in held_back]
    assert "0169_program5_release_binding.sql" in selected_names
    assert selected_names[-1] == "0170_guidance_import_lifecycle.sql"
    assert held_back_names[0] == "0171_program5_provider_version.sql"
    assert all(name <= "0170_guidance_import_lifecycle.sql" for name in selected_names)
    assert all(name > "0170_guidance_import_lifecycle.sql" for name in held_back_names)
    try:
        migration_runner.migrations_through(loaded, pending, "0170_not_a_file.sql")
    except ValueError as exc:
        assert "exact checked-in migration filename" in str(exc), str(exc)
    else:
        raise AssertionError("unknown --through boundary was accepted")

    # 0480 creates a writer-visible authority surface and 0481 seals its SCAC
    # successor. They must be selected and committed together: the deferred
    # policy-epoch trigger correctly refuses the catalog between those files.
    continuity = [item for item in loaded if item[0].startswith(("0480_", "0481_"))]
    assert [item[0] for item in continuity] == [
        "0480_codex_continuity.sql",
        "0481_codex_continuity_registry_activation.sql",
    ]
    batches = migration_runner.migration_batches(continuity)
    assert len(batches) == 1
    assert [item[0] for item in batches[0]] == [
        "0480_codex_continuity.sql",
        "0481_codex_continuity_registry_activation.sql",
    ]
    before_continuity = [item for item in loaded if item[0] < "0480_codex_continuity.sql"]
    try:
        migration_runner.migrations_through(
            loaded,
            continuity,
            "0480_codex_continuity.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError("--through was allowed to expose the v10/v11 catalog gap")
    assert before_continuity
    resumed_batches = migration_runner.migration_batches([continuity[1]])
    assert resumed_batches == [[continuity[1]]]

    claude_continuity = [
        item for item in loaded if item[0].startswith(("0485_", "0486_"))
    ]
    assert [item[0] for item in claude_continuity] == [
        "0485_claude_continuity.sql",
        "0486_claude_continuity_registry_activation.sql",
    ]
    assert migration_runner.migration_batches(claude_continuity) == [claude_continuity]
    try:
        migration_runner.migrations_through(
            loaded,
            claude_continuity,
            "0485_claude_continuity.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError("--through was allowed to expose the v11/v12 catalog gap")

    # WR95 adds its live mutation surfaces in 0508-0511 and seals the resulting
    # SCAC successor in 0512. Production's deferred policy-epoch trigger must
    # never observe or commit one of those intermediate catalogs.
    foundation_assurance = [
        item for item in loaded if item[0].startswith(
            ("0508_", "0509_", "0510_", "0511_", "0512_")
        )
    ]
    assert [item[0] for item in foundation_assurance] == [
        "0508_foundation_assurance_minimum_receipt.sql",
        "0509_journey_one_clock_store.sql",
        "0510_journey_one_clock_input_store.sql",
        "0511_foundation_assurance_minimum_outcome.sql",
        "0512_foundation_assurance_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(foundation_assurance) == [
        foundation_assurance
    ]
    try:
        migration_runner.migrations_through(
            loaded,
            foundation_assurance,
            "0511_foundation_assurance_minimum_outcome.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError("--through was allowed to expose WR95 before its SCAC seal")

    # WR-000110 adds the program-controller seam tables, the one privileged
    # writer function and its grants in 0517, and seals the resulting SCAC v29
    # successor in 0518. The same deferred policy-epoch trigger refuses the
    # intermediate catalog, so the reviewed pair must commit as one transaction.
    program_controller = [
        item for item in loaded if item[0].startswith(("0517_", "0518_"))
    ]
    assert [item[0] for item in program_controller] == [
        "0517_program_controller_seams.sql",
        "0518_program_controller_seams_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(program_controller) == [
        program_controller
    ]
    try:
        migration_runner.migrations_through(
            loaded,
            program_controller,
            "0517_program_controller_seams.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError(
            "--through was allowed to expose WR-000110 before its SCAC seal"
        )

    # WR111/112/113 add the producer cost ledger, the Doc conversation store and
    # the R03 notification store in 0519-0521, and seal the resulting SCAC v30
    # successor in 0522. The same deferred policy-epoch trigger refuses every
    # intermediate catalog, so the reviewed FOUR must commit as one transaction.
    producer_trio = [
        item for item in loaded if item[0].startswith(("0519_", "0520_", "0521_", "0522_"))
    ]
    assert [item[0] for item in producer_trio] == [
        "0519_producer_cost_ledger.sql",
        "0520_doc_conversation_store.sql",
        "0521_r03_notifications.sql",
        "0522_producer_trio_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(producer_trio) == [producer_trio]
    for cut in (
        "0519_producer_cost_ledger.sql",
        "0520_doc_conversation_store.sql",
        "0521_r03_notifications.sql",
    ):
        try:
            migration_runner.migrations_through(loaded, producer_trio, cut)
        except ValueError as exc:
            assert "cuts reviewed atomic migration group" in str(exc), str(exc)
        else:
            raise AssertionError(
                f"--through {cut} was allowed to expose WR111/112/113 before its SCAC seal"
            )

    # WR-000114 adds the three Doc conversation write doors in 0523 and seals
    # the resulting SCAC v31 successor in 0524. The same deferred policy-epoch
    # trigger refuses the intermediate catalog, so the reviewed pair must commit
    # as one transaction and --through may not cut it.
    doc_conversation_write_doors = [
        item for item in loaded if item[0].startswith(("0523_", "0524_"))
    ]
    assert [item[0] for item in doc_conversation_write_doors] == [
        "0523_doc_conversation_write_doors.sql",
        "0524_doc_conversation_write_doors_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(doc_conversation_write_doors) == [
        doc_conversation_write_doors
    ]
    try:
        migration_runner.migrations_through(
            loaded,
            doc_conversation_write_doors,
            "0523_doc_conversation_write_doors.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError(
            "--through was allowed to expose WR-000114 before its SCAC seal"
        )

    # WR-000115 adds the Doc conversation list door in 0525 and seals the
    # resulting SCAC v32 successor in 0526.  The same deferred policy-epoch
    # trigger refuses the intermediate catalog, so the reviewed pair must commit
    # as one transaction and --through may not cut it.  A read verb owes no
    # completion-evidence gate entry; it still owes this group.
    doc_conversation_list = [
        item for item in loaded if item[0].startswith(("0525_", "0526_"))
    ]
    assert [item[0] for item in doc_conversation_list] == [
        "0525_doc_conversation_list.sql",
        "0526_doc_conversation_list_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(doc_conversation_list) == [
        doc_conversation_list
    ]
    try:
        migration_runner.migrations_through(
            loaded,
            doc_conversation_list,
            "0525_doc_conversation_list.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError(
            "--through was allowed to expose WR-000115 before its SCAC seal"
        )

    # WR-000116 adds the notification-preference pair in 0527 and seals the
    # resulting SCAC v33 successor in 0528.  The same deferred policy-epoch
    # trigger refuses the intermediate catalog, so the reviewed pair must commit
    # as one transaction and --through may not cut it.  The write verb's name
    # already classifies as a write through the completion-evidence gate's own
    # `set` prefix, so no gate entry is owed; the pair still owes this group.
    notification_preferences = [
        item for item in loaded if item[0].startswith(("0527_", "0528_"))
    ]
    assert [item[0] for item in notification_preferences] == [
        "0527_notification_preferences.sql",
        "0528_notification_preferences_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(notification_preferences) == [
        notification_preferences
    ]
    try:
        migration_runner.migrations_through(
            loaded,
            notification_preferences,
            "0527_notification_preferences.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError(
            "--through was allowed to expose WR-000116 before its SCAC seal"
        )

    # WR-000117 adds the session-identity read pair in 0529 and seals the
    # resulting SCAC v34 successor in 0530.  The same deferred policy-epoch
    # trigger refuses the intermediate catalog, so the reviewed pair must commit
    # as one transaction and --through may not cut it.  Both verbs are reads and
    # `read` is in neither of the completion-evidence gate's two collections, so
    # no gate entry is owed; the pair still owes this group.
    session_identity = [
        item for item in loaded if item[0].startswith(("0529_", "0530_"))
    ]
    assert [item[0] for item in session_identity] == [
        "0529_session_identity_reads.sql",
        "0530_session_identity_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(session_identity) == [session_identity]
    try:
        migration_runner.migrations_through(
            loaded,
            session_identity,
            "0529_session_identity_reads.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError(
            "--through was allowed to expose WR-000117 before its SCAC seal"
        )

    # WR-000119 adds the dispatch spine in 0531 -- two append-only relations
    # with their grants, the rewritten dispatch-history function and the two
    # write doors -- and seals the resulting SCAC v35 successor in 0532.  The
    # same deferred policy-epoch trigger refuses the intermediate catalog, so
    # the reviewed pair must commit as one transaction and --through may not cut
    # it.  Unlike the 0529/0530 pair this one owes a completion-evidence gate
    # entry, because acknowledge-dispatch is a durable append and `acknowledge`
    # is deliberately not a write prefix.
    dispatch_spine = [
        item for item in loaded if item[0].startswith(("0531_", "0532_"))
    ]
    assert [item[0] for item in dispatch_spine] == [
        "0531_room_dispatch_spine.sql",
        "0532_room_dispatch_spine_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(dispatch_spine) == [dispatch_spine]
    try:
        migration_runner.migrations_through(
            loaded,
            dispatch_spine,
            "0531_room_dispatch_spine.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError(
            "--through was allowed to expose WR-000119 before its SCAC seal"
        )

    # Production already carries the numeric 0532 v35 seal. WR-000125's two
    # lettered suffixes are one strict atomic group: no through-boundary or
    # pre-existing partial ledger may expose 0532a without the v36 successor.
    ready_plan_suffix = [
        item for item in loaded if item[0].startswith(("0532a_", "0532b_"))
    ]
    assert [item[0] for item in ready_plan_suffix] == list(APPROVED_0532[1:])
    assert migration_runner.migration_batches(ready_plan_suffix) == [
        ready_plan_suffix
    ]
    try:
        migration_runner.migrations_through(
            loaded,
            ready_plan_suffix,
            "0532a_canonical_ownership_lease_activation.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError("--through 0532a was allowed to expose an unsealed catalog")
    try:
        migration_runner.validate_applied_ledger(
            ready_plan_suffix,
            {ready_plan_suffix[0][0]: ready_plan_suffix[0][2]},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "partial strict atomic migration group is forbidden" in str(exc), str(exc)
    else:
        raise AssertionError("applied 0532a without 0532b was accepted")

    # WR-000130's assurance repair and its v37 catalog seal must commit as
    # one group, including on production where both are still pending.
    assurance_suffix = [
        item for item in loaded if item[0].startswith(("0538_", "0539_"))
    ]
    assert [item[0] for item in assurance_suffix] == [
        "0538_canonical_ownership_assurance_binding.sql",
        "0539_canonical_ownership_assurance_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(assurance_suffix) == [
        assurance_suffix
    ]
    try:
        migration_runner.migrations_through(
            loaded, assurance_suffix,
            "0538_canonical_ownership_assurance_binding.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError("--through 0538 was allowed to expose an unsealed catalog")
    try:
        migration_runner.validate_applied_ledger(
            assurance_suffix,
            {assurance_suffix[0][0]: assurance_suffix[0][2]},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "partial strict atomic migration group is forbidden" in str(exc), str(exc)
    else:
        raise AssertionError("applied 0538 without 0539 was accepted")

    # B09's reader and its v41 successor share the same deferred epoch
    # boundary.  The pair must batch on a fresh production ledger, reject a
    # through-boundary at 0546, and refuse an inherited one-file ledger.
    outcome_card_suffix = [
        item for item in loaded if item[0].startswith(("0546_", "0547_"))
    ]
    assert [item[0] for item in outcome_card_suffix] == [
        "0546_read_doc_outcome_cards_successor.sql",
        "0547_read_doc_outcome_cards_scac_successor.sql",
    ]
    assert migration_runner.migration_batches(outcome_card_suffix) == [
        outcome_card_suffix
    ]
    try:
        migration_runner.migrations_through(
            loaded, outcome_card_suffix,
            "0546_read_doc_outcome_cards_successor.sql",
        )
    except ValueError as exc:
        assert "cuts reviewed atomic migration group" in str(exc), str(exc)
    else:
        raise AssertionError("--through 0546 was allowed to expose an unsealed catalog")
    try:
        migration_runner.validate_applied_ledger(
            outcome_card_suffix,
            {outcome_card_suffix[0][0]: outcome_card_suffix[0][2]},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "partial strict atomic migration group is forbidden" in str(exc), str(exc)
    else:
        raise AssertionError("applied 0546 without 0547 was accepted")

    # A bounded prefix must not make an out-of-order ledger look safe.  If a
    # later file is already applied while an earlier file is absent, history
    # has drifted and the runner must stop before selecting anything.
    try:
        migration_runner.validate_applied_ledger(
            loaded,
            {
                name: digest
                for name, _sql, digest in loaded
                if name != "0170_guidance_import_lifecycle.sql"
                and name <= "0171_program5_provider_version.sql"
            },
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "ledger is reordered" in str(exc), str(exc)
        assert "0170_guidance_import_lifecycle.sql" in str(exc), str(exc)
        assert "0171_program5_provider_version.sql" in str(exc), str(exc)
    else:
        raise AssertionError("out-of-order applied ledger was accepted")

    # A ledger row whose file disappeared is a rename/deletion, not harmless
    # history. Only the exact pre-renumber Control Plane aliases are accepted,
    # and only while their mapped forward migrations remain in the tree.
    missing_release = [
        item for item in loaded if item[0] != "0134_release_abandon_reason.sql"
    ]
    try:
        migration_runner.validate_applied_ledger(
            missing_release,
            {"0134_release_abandon_reason.sql":
                loaded_digests["0134_release_abandon_reason.sql"]},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "0134_release_abandon_reason.sql" in str(exc), str(exc)
    else:
        raise AssertionError("deleted applied non-legacy migration was accepted")

    alias_prefix = {
        name: digest for name, _sql, digest in loaded
        if name < "0148_control_plane_admission.sql"
    }
    alias_prefix["0134_control_plane_admission.sql"] = "legacy-ledger-digest"
    migration_runner.validate_applied_ledger(loaded, alias_prefix)
    try:
        migration_runner.validate_applied_ledger(
            loaded,
            {"0169_control_plane_canary_fencing.sql": "edited-digest"},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "sha mismatch" in str(exc), str(exc)
    else:
        raise AssertionError("edited applied migration digest was accepted")
    missing_alias_target = [
        item for item in loaded if item[0] != "0148_control_plane_admission.sql"
    ]
    try:
        migration_runner.validate_applied_ledger(
            missing_alias_target,
            {"0134_control_plane_admission.sql": "legacy-ledger-digest"},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "0148_control_plane_admission.sql" in str(exc), str(exc)
    else:
        raise AssertionError("legacy ledger alias without its forward migration was accepted")

    allocation = subprocess.run(
        [sys.executable, str(REPO / "tools" / "next-migration.py")],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert "registered numeric collisions on origin/main" in allocation, allocation
    assert "0169 (historical frozen): " + ", ".join(FROZEN_0169) in allocation, allocation

    # The only permitted remote-collision repair is the current 0298 incident:
    # preserve the partner-room migration, replace only the memory migration
    # with 0299, and prove the replacement bytes differ only in ordinal labels.
    exact_frozen = [name for names in FROZEN_COLLISIONS.values() for name in names]
    exact_remote = exact_frozen + [
        "0298_partner_room_origin.sql",
        "0298_memory_kernel.sql",
    ]
    exact_current = exact_frozen + [
        "0298_partner_room_origin.sql",
        "0299_memory_kernel.sql",
    ]
    assert next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current, True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote + ["0298_unrelated.sql"], exact_current, True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current[:-1], True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current[1:], True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current, False, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_remote, True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current, True, False
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current + ["0300_memory_kernel.sql"], True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        [name for name in exact_remote if name != "0169_hermes_pilot_actor.sql"],
        exact_current,
        True,
        True,
    )

    # Exercise the actual allocator path with the real migration inventory:
    # red origin/main plus a clean checked tree succeeds and reserves 0298,
    # while a checked tree that still contains both 0298 files refuses. The
    # real inventory now also carries the 0301/0302 provenance migrations, so
    # the next available number after the repair is 0303.
    remote_inventory = [
        name if name != "0299_memory_kernel.sql" else "0298_memory_kernel.sql"
        for name in actual
    ]
    for colliding_current in (False, True):
        with tempfile.TemporaryDirectory(prefix="migration-number-contract-0298-repair-") as tmp:
            migration_dir = Path(tmp) / "migrations"
            migration_dir.mkdir()
            current_inventory = list(actual)
            if colliding_current:
                current_inventory.append("0298_memory_kernel.sql")
            for name in current_inventory:
                (migration_dir / name).touch()
            original_run = next_migration.run
            original_worktree_paths = next_migration.worktree_paths
            original_repo = next_migration.REPO
            original_head_check = next_migration._head_contains_origin_main
            original_content_check = next_migration._repair_contents_match
            try:
                next_migration.run = lambda args, cwd=None: (
                    "\n".join(f"migrations/{name}" for name in remote_inventory)
                    if args[:3] == ["git", "ls-tree", "--name-only"] else ""
                )
                next_migration.worktree_paths = lambda: [tmp]
                next_migration.REPO = tmp
                next_migration._head_contains_origin_main = lambda: True
                next_migration._repair_contents_match = lambda: True
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    repair_rc = next_migration.main()
            finally:
                next_migration.run = original_run
                next_migration.worktree_paths = original_worktree_paths
                next_migration.REPO = original_repo
                next_migration._head_contains_origin_main = original_head_check
                next_migration._repair_contents_match = original_content_check
            if colliding_current:
                assert repair_rc == 1, (repair_rc, stdout.getvalue(), stderr.getvalue())
                assert "origin/main violates" in stderr.getvalue(), stderr.getvalue()
            else:
                assert repair_rc == 0, (repair_rc, stdout.getvalue(), stderr.getvalue())
                assert f"next free migration number: {expected_next:04d}" in stdout.getvalue(), stdout.getvalue()
                assert "0298_memory_kernel.sql" in stdout.getvalue(), stdout.getvalue()

    # The same allocator path refuses if the red remote inventory is missing
    # one member of a canonical frozen collision.
    with tempfile.TemporaryDirectory(prefix="migration-number-contract-0298-frozen-gap-") as tmp:
        migration_dir = Path(tmp) / "migrations"
        migration_dir.mkdir()
        for name in actual:
            (migration_dir / name).touch()
        remote_missing_frozen = [
            name for name in remote_inventory if name != "0169_hermes_pilot_actor.sql"
        ]
        original_run = next_migration.run
        original_worktree_paths = next_migration.worktree_paths
        original_repo = next_migration.REPO
        original_head_check = next_migration._head_contains_origin_main
        original_content_check = next_migration._repair_contents_match
        try:
            next_migration.run = lambda args, cwd=None: (
                "\n".join(f"migrations/{name}" for name in remote_missing_frozen)
                if args[:3] == ["git", "ls-tree", "--name-only"] else ""
            )
            next_migration.worktree_paths = lambda: [tmp]
            next_migration.REPO = tmp
            next_migration._head_contains_origin_main = lambda: True
            next_migration._repair_contents_match = lambda: True
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                frozen_gap_rc = next_migration.main()
        finally:
            next_migration.run = original_run
            next_migration.worktree_paths = original_worktree_paths
            next_migration.REPO = original_repo
            next_migration._head_contains_origin_main = original_head_check
            next_migration._repair_contents_match = original_content_check
        assert frozen_gap_rc == 1, (frozen_gap_rc, stdout.getvalue(), stderr.getvalue())
        assert "origin/main violates" in stderr.getvalue(), stderr.getvalue()

    # A COLLISION IN SOMEONE ELSE'S WORKTREE WARNS AND STILL RESERVES THE
    # NUMBERS. It does not refuse.
    #
    # This assertion used to require rc 1, and that veto cost most of a night on
    # 2026-08-22. Two branches merged 0248 twice; main resolved it by renumber;
    # every other checkout on the machine still held the old filename on disk,
    # as stale worktrees harmlessly do. The allocator then refused for everyone,
    # this selftest failed, and the pre-push gate refused pushes from branches
    # that touched no migration at all — three of them, whose only way through
    # was skipping CI entirely, which is strictly worse than the thing being
    # guarded. The council ruled the class the same day: a machine-global
    # condition may open a loop, never veto unrelated work.
    #
    # What actually protects the caller is the claim merge, and that is asserted
    # here: the colliding numbers must still be reported as in-flight, so the
    # allocator never hands one out.
    with tempfile.TemporaryDirectory(prefix="migration-number-contract-") as tmp:
        migration_dir = Path(tmp) / "migrations"
        migration_dir.mkdir()
        (migration_dir / "0172_first.sql").touch()
        (migration_dir / "0172_second.sql").touch()
        original_run = next_migration.run
        original_worktree_paths = next_migration.worktree_paths
        try:
            next_migration.run = lambda _args, cwd=next_migration.REPO: "\n".join(actual)
            next_migration.worktree_paths = lambda: [tmp]
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                allocator_rc = next_migration.main()
        finally:
            next_migration.run = original_run
            next_migration.worktree_paths = original_worktree_paths
        assert allocator_rc == 0, (allocator_rc, stdout.getvalue(), stderr.getvalue())
        assert "WARNING" in stderr.getvalue(), stderr.getvalue()
        assert "unregistered collision 0172" in stderr.getvalue(), stderr.getvalue()
        assert "another session's checkout" in stderr.getvalue(), stderr.getvalue()
        # The load-bearing half: both names stay claimed, so 0172 is never
        # handed to the caller as free.
        assert "0172_first.sql" in stdout.getvalue(), stdout.getvalue()
        assert "0172_second.sql" in stdout.getvalue(), stdout.getvalue()

    # THE CALLER'S OWN TREE IS DIFFERENT, and still refuses. A collision here
    # means the number about to be handed out may itself be wrong, and it is
    # the caller's to fix rather than someone else's.
    with tempfile.TemporaryDirectory(prefix="migration-number-contract-own-") as tmp:
        migration_dir = Path(tmp) / "migrations"
        migration_dir.mkdir()
        (migration_dir / "0173_first.sql").touch()
        (migration_dir / "0173_second.sql").touch()
        original_run = next_migration.run
        original_worktree_paths = next_migration.worktree_paths
        original_repo = next_migration.REPO
        try:
            next_migration.run = lambda _args, cwd=None: "\n".join(actual)
            next_migration.worktree_paths = lambda: [tmp]
            next_migration.REPO = tmp          # the caller IS standing in this tree
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                own_rc = next_migration.main()
        finally:
            next_migration.run = original_run
            next_migration.worktree_paths = original_worktree_paths
            next_migration.REPO = original_repo
        assert own_rc == 1, (own_rc, stdout.getvalue(), stderr.getvalue())
        assert "this tree violates" in stderr.getvalue(), stderr.getvalue()

    print("migration number contract selftest: historical collisions frozen; "
          "own-tree collisions refused; a peer worktree's collision warns and still reserves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
