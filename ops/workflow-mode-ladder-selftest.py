#!/usr/bin/env python3
"""Hermetic checks for the workflow mode acceptance ladder.

Covers the three surfaces migration 0334 and tick --mode auto together own:
the DB guard's refusal branches (read as text; this selftest never touches a
database), the pure ladder resolver tick uses to pick a tier per workflow, and
the wrapper pinning the ladder-aware auto mode instead of a fixed one.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import migration_names, read_migration



REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane import resolve_auto_mode, validate_manifest  # noqa: E402

MIGRATION = REPO / "migrations" / "0334_workflow_mode_ladder.sql"
WRAPPER = REPO / "bin" / "control-plane-tick.sh"
MANIFEST = REPO / "ops" / "config" / "control-plane-workflows.v1.json"

FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def check_migration() -> None:
    check("migration 0334 exists", MIGRATION.name in migration_names(MIGRATION.parent.parent), str(MIGRATION))
    if not MIGRATION.name in migration_names(MIGRATION.parent.parent):
        return
    source = read_migration(MIGRATION.parent.parent, MIGRATION.name)
    check("migration replaces ops.enqueue_job", "create or replace function ops.enqueue_job(" in source)
    check("migration branches on canary mode", "if p_mode='canary' then" in source)
    check("migration branches on live mode", "elsif p_mode='live' then" in source)
    check("canary refusal names the contract-disabled reason",
          "cannot enqueue canary mode: canary is contractually disabled" in source)
    check("canary refusal names the missing shadow evidence",
          "cannot enqueue canary mode: no accepted shadow acceptance evidence" in source)
    check("live refusal names the contract-disabled exception's missing shadow evidence",
          "cannot enqueue live mode: canary is contractually disabled and no accepted shadow acceptance evidence" in source)
    check("live refusal names the missing canary evidence",
          "cannot enqueue live mode: no accepted canary acceptance evidence" in source)
    check("shadow mode carries no acceptance gate",
          "p_mode='shadow'" not in source)
    check("replay mode is left exactly as today (no new branch)",
          "p_mode='replay'" not in source and "p_mode = 'replay'" not in source)
    check("migration keeps the original duplicate-delivery reconciliation",
          "duplicate delivery conflicts with the canonical scheduled job" in source)
    check("migration wraps the change in begin/commit", "\nbegin;\n" in source and "\ncommit;\n" in source)
    check("migration ends with a self-check DO block", "do $$" in source and "0334 FAILED" in source)


def check_resolver() -> None:
    enabled = {"enabled": True}
    disabled = {"enabled": False, "reason": "test fixture"}
    no_evidence: list[dict] = []
    accepted_shadow = [{"mode": "shadow", "status": "accepted"}]
    accepted_canary = [{"mode": "shadow", "status": "accepted"}, {"mode": "canary", "status": "accepted"}]
    observed_only_shadow = [{"mode": "shadow", "status": "observed"}]

    check("no acceptance evidence resolves to shadow",
          resolve_auto_mode(enabled, no_evidence) == "shadow")
    check("no acceptance evidence resolves to shadow when canary is disabled",
          resolve_auto_mode(disabled, no_evidence) == "shadow")
    check("missing canary contract (cognition workflows) resolves to shadow with no evidence",
          resolve_auto_mode(None, no_evidence) == "shadow")
    check("accepted shadow resolves to canary when canary is enabled",
          resolve_auto_mode(enabled, accepted_shadow) == "canary")
    check("accepted shadow resolves to live when canary is contractually disabled",
          resolve_auto_mode(disabled, accepted_shadow) == "live")
    check("accepted canary resolves to live",
          resolve_auto_mode(enabled, accepted_canary) == "live")
    check("an observed-only (not accepted) shadow row does not advance the tier",
          resolve_auto_mode(enabled, observed_only_shadow) == "shadow")
    check("a disabled contract never resolves past live even with accepted canary evidence",
          resolve_auto_mode(disabled, accepted_canary) == "live")


def check_notes_sweep_is_pinned_at_shadow() -> None:
    """notes-sweep-hourly lost its canary tier; that must not make it live.

    A contract with canary disabled lets accepted SHADOW alone satisfy live,
    and the production tick resolves tiers with ``--mode auto``.  So retiring
    the Notes canary would silently turn one routine shadow acceptance (Dell
    may record those) into automatic live ingestion.  The manifest must pin the
    workflow at shadow for the automatic tick; going live stays an explicit
    person-made ``schedule --mode live``.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    workflow = next(w for w in manifest["workflows"] if w["key"] == "notes-sweep-hourly")
    canary = workflow["execution"]["canary"]
    accepted_shadow = [{"mode": "shadow", "status": "accepted"}]
    accepted_all = accepted_shadow + [{"mode": "canary", "status": "accepted"}]

    check("notes-sweep-hourly has no canary tier", canary.get("enabled") is False, str(canary))
    check("notes-sweep-hourly sender has no canary guard registered",
          "isolation_guard" not in canary and "args" not in canary or canary.get("args") == [], str(canary))
    check("notes-sweep-hourly with accepted shadow never auto-resolves to live",
          resolve_auto_mode(canary, accepted_shadow) == "shadow", str(canary))
    check("notes-sweep-hourly with every acceptance row never auto-resolves to live",
          resolve_auto_mode(canary, accepted_all) == "shadow")
    check("notes-sweep-hourly with no evidence stays at shadow",
          resolve_auto_mode(canary, []) == "shadow")
    check("the shipped manifest still validates", validate_manifest(manifest, repo=REPO) == [],
          str(validate_manifest(manifest, repo=REPO)))

    unpinned = deepcopy(manifest)
    target = next(w for w in unpinned["workflows"] if w["key"] == "notes-sweep-hourly")
    target["execution"]["canary"].pop("auto_promote_live", None)
    check("removing the pin is what would open the live path (control)",
          resolve_auto_mode(target["execution"]["canary"], accepted_shadow) == "live")

    sloppy = deepcopy(manifest)
    next(w for w in sloppy["workflows"] if w["key"] == "notes-sweep-hourly")["execution"]["canary"]["auto_promote_live"] = "no"
    check("a non-boolean auto_promote_live pin is a manifest error",
          any("auto_promote_live" in e for e in validate_manifest(sloppy, repo=REPO)))

    others = [w["key"] for w in manifest["workflows"]
              if w["key"] != "notes-sweep-hourly"
              and isinstance(w.get("execution", {}).get("canary"), dict)
              and w["execution"]["canary"].get("auto_promote_live") is False]
    check("the pin is not smeared onto other workflows", others == [], str(others))


def check_wrapper() -> None:
    check("wrapper exists", WRAPPER.is_file(), str(WRAPPER))
    if not WRAPPER.is_file():
        return
    source = WRAPPER.read_text(encoding="utf-8")
    check("wrapper pins the ladder-resolving auto mode", "tick --mode auto" in source)
    check("wrapper no longer pins the retired fixed shadow mode", "tick --mode shadow" not in source)


def main() -> int:
    print("workflow-mode-ladder-selftest — acceptance ladder for shadow/canary/live\n")
    check_migration()
    check_resolver()
    check_notes_sweep_is_pinned_at_shadow()
    check_wrapper()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} check(s):")
        for label in FAILED:
            print(f"  - {label}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
