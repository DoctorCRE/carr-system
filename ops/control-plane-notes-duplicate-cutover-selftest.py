#!/usr/bin/env python3
"""Contract tests for atomic retirement of the duplicate Notes schedulers."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration


import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import (  # noqa: E402
    CutoverRefusal,
    prepare_duplicate_disable,
    scheduler_surface_rows,
    verify_duplicate_disabled,
)

REGISTRY = json.loads(
    (REPO / "ops/config/control-plane-scheduler-cutover.v1.json").read_text(encoding="utf-8")
)
MANIFEST = json.loads(
    (REPO / "ops/config/control-plane-workflows.v1.json").read_text(encoding="utf-8")
)
NOW = datetime(2026, 8, 17, 1, 30, tzinfo=timezone.utc)
NOW_TEXT = "2026-08-17T01:30:00Z"
LATER_TEXT = "2026-08-17T01:31:00Z"
FAILED = False


def check(label: str, condition: bool) -> None:
    global FAILED
    if condition:
        print(f"  OK  {label}")
    else:
        FAILED = True
        print(f"FAIL  {label}")


def refuses(label: str, fn: Callable[[], Any]) -> None:
    try:
        fn()
    except CutoverRefusal:
        check(label, True)
    else:
        check(label, False)


def observation(surface_id: str, state: str, at: str) -> dict[str, Any]:
    surface = next(item for item in REGISTRY["surfaces"] if item["surface_id"] == surface_id)
    ref_key = "provider_receipt_ref" if surface["scheduler_kind"] == "claude-code" else "launchd_receipt_ref"
    ref = f"native:{surface_id}:{state}:{at}"
    source_key = "provider_receipt_read" if surface["scheduler_kind"] == "claude-code" else "launchd_receipt_read"
    return {
        "schema_version": 1,
        "contract": "control-plane-scheduler-cutover",
        "kind": "scheduler_observation",
        "surface_id": surface_id,
        "workflow_key": "notes-sweep-hourly",
        "workflow_version": 4,
        "scheduler_kind": surface["scheduler_kind"],
        "locator": surface["locator"],
        "scheduler_state": state,
        "observed_at": at,
        "sources": {source_key: True, "device_principal_bound": True, "registered_contract_matches": True},
        "source_fingerprint": hashlib.sha256(ref.encode()).hexdigest(),
        ref_key: ref,
    }


def acceptance(ref: str) -> dict[str, Any]:
    return {
        "kind": "workflow_acceptance_receipt",
        "receipt_ref": ref,
        "workflow_key": "notes-sweep-hourly",
        "workflow_version": 4,
        "mode": ref.rsplit(":", 1)[-1],
        "status": "accepted",
        "immutable": True,
    }


def replacement() -> dict[str, Any]:
    return {
        "workflow_key": "notes-sweep-hourly",
        "workflow_version": 4,
        "healthy": True,
        "accepted_receipt_refs": ["accept:notes:shadow", "accept:notes:canary"],
    }


def approval(pre: dict[str, Any], post: dict[str, Any], sibling_pre: dict[str, Any], sibling_post: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "human_authority_receipt",
        "receipt_ref": "legacy-disable:notes-group",
        "immutable": True,
        "authority_subject": "joe",
        "action": "disable-legacy-schedule",
        "subject": {
            "workflow_key": "notes-sweep-hourly",
            "workflow_version": 4,
            "surface_id": pre["surface_id"],
            "locator": pre["locator"],
            "sibling_surface_id": sibling_pre["surface_id"],
            "sibling_locator": sibling_pre["locator"],
        },
        "observation_refs": {
            "pre": pre["provider_receipt_ref"],
            "post": post["provider_receipt_ref"],
            "sibling_pre": sibling_pre["launchd_receipt_ref"],
            "sibling_post": sibling_post["launchd_receipt_ref"],
        },
    }


def main() -> int:
    rows = scheduler_surface_rows(REGISTRY, manifest=MANIFEST)
    notes_rows = [row for row in rows if row[0] == "notes-sweep-hourly"]
    check("DB registry projection binds both Notes rows to the same duplicate group",
          len(notes_rows) == 2 and all(row[5] == "notes-sweep-hourly.legacy" for row in notes_rows))
    split_registry = deepcopy(REGISTRY)
    next(row for row in split_registry["surfaces"]
         if row["surface_id"] == "notes-sweep-hourly.launchd.v1")["duplicate_group"] = "wrong-group"
    refuses("authority sync refuses split duplicate-group declarations", lambda: scheduler_surface_rows(
        split_registry, manifest=MANIFEST))
    claude_enabled = observation("notes-sweep-hourly.claude-code.v1", "enabled", NOW_TEXT)
    claude_disabled = observation("notes-sweep-hourly.claude-code.v1", "disabled", LATER_TEXT)
    launchd_enabled = observation("notes-sweep-hourly.launchd.v1", "enabled", NOW_TEXT)
    launchd_disabled = observation("notes-sweep-hourly.launchd.v1", "disabled", LATER_TEXT)
    prepared = prepare_duplicate_disable(
        REGISTRY,
        duplicate_group="notes-sweep-hourly.legacy",
        observations=[claude_enabled, launchd_enabled],
        replacement=replacement(),
        receipt_verifier=acceptance,
        now=NOW,
    )
    check("prepare binds both exact enabled surfaces", len(prepared["binding"]["surfaces"]) == 2)
    check("prepare binds one shared accepted replacement", prepared["binding"]["replacement_receipts"] == ["accept:notes:canary", "accept:notes:shadow"])
    verified = verify_duplicate_disabled(
        REGISTRY,
        prepared=prepared,
        pre_disable_observations=[claude_enabled, launchd_enabled],
        post_disable_observations=[claude_disabled, launchd_disabled],
        human_approval_ref="legacy-disable:notes-group",
        approval_verifier=lambda _ref: approval(claude_enabled, claude_disabled, launchd_enabled, launchd_disabled),
        now=NOW,
    )
    check("verification binds both native disabled readbacks", len(verified["receipt"]["surfaces"]) == 2)
    check("verification is a single group retirement receipt", verified["receipt"]["duplicate_group"] == "notes-sweep-hourly.legacy")

    refuses("one enabled observation cannot prepare duplicate retirement", lambda: prepare_duplicate_disable(
        REGISTRY, duplicate_group="notes-sweep-hourly.legacy", observations=[claude_enabled],
        replacement=replacement(), receipt_verifier=acceptance, now=NOW))
    refuses("two observations from the same surface cannot prepare retirement", lambda: prepare_duplicate_disable(
        REGISTRY, duplicate_group="notes-sweep-hourly.legacy", observations=[claude_enabled, deepcopy(claude_enabled)],
        replacement=replacement(), receipt_verifier=acceptance, now=NOW))
    refuses("wrong duplicate group cannot prepare retirement", lambda: prepare_duplicate_disable(
        REGISTRY, duplicate_group="invented", observations=[claude_enabled, launchd_enabled],
        replacement=replacement(), receipt_verifier=acceptance, now=NOW))
    refuses("one native scheduler remaining enabled refuses retirement", lambda: verify_duplicate_disabled(
        REGISTRY, prepared=prepared, pre_disable_observations=[claude_enabled, launchd_enabled],
        post_disable_observations=[claude_disabled, launchd_enabled],
        human_approval_ref="legacy-disable:notes-group",
        approval_verifier=lambda _ref: approval(claude_enabled, claude_disabled, launchd_enabled, launchd_disabled), now=NOW))
    forged = approval(claude_enabled, claude_disabled, launchd_enabled, launchd_disabled)
    forged["observation_refs"]["sibling_post"] = "native:forged"
    refuses("Joe receipt must bind all four native observations", lambda: verify_duplicate_disabled(
        REGISTRY, prepared=prepared, pre_disable_observations=[claude_enabled, launchd_enabled],
        post_disable_observations=[claude_disabled, launchd_disabled],
        human_approval_ref="legacy-disable:notes-group", approval_verifier=lambda _ref: forged, now=NOW))
    refuses("single-surface verifier remains unavailable for duplicate group", lambda: verify_duplicate_disabled(
        REGISTRY, prepared={"kind": "scheduler_disable_prepare"}, pre_disable_observations=[claude_enabled],
        post_disable_observations=[claude_disabled], human_approval_ref="legacy-disable:notes-group",
        approval_verifier=lambda _ref: forged, now=NOW))

    migration = read_migration(REPO, "0184_notes_duplicate_schedule_cutover.sql").lower()
    check("migration records canonical duplicate group and four immutable native refs",
          all(token in migration for token in (
              "duplicate_group", "sibling_surface_id", "sibling_locator",
              "sibling_pre_observation_ref", "sibling_post_observation_ref")))
    check("migration replaces the single-surface authority signature atomically",
          "drop function if exists ops.disable_legacy_schedule(text,text,text,text,text,text,text,text)" in migration
          and "create function ops.disable_legacy_schedule(" in migration
          and "p_sibling_pre_observation_ref text" in migration
          and "p_sibling_post_observation_ref text" in migration)
    check("migration requires both Notes scheduler kinds and current four-receipt evidence",
          "notes duplicate retirement requires exact claude-code and launchd surfaces" in migration
          and "native duplicate scheduler evidence is not a current enabled-to-disabled readback" in migration)

    print("PASS" if not FAILED else "FAIL")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
