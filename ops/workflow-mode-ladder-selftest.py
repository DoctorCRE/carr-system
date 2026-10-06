#!/usr/bin/env python3
"""Hermetic checks for the workflow mode acceptance ladder.

Covers the three surfaces migration 0334 and tick --mode auto together own:
the DB guard's refusal branches (read as text; this selftest never touches a
database), the pure ladder resolver tick uses to pick a tier per workflow, and
the wrapper pinning the ladder-aware auto mode instead of a fixed one.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane import resolve_auto_mode  # noqa: E402

MIGRATION = REPO / "migrations" / "0334_workflow_mode_ladder.sql"
WRAPPER = REPO / "bin" / "control-plane-tick.sh"

FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def check_migration() -> None:
    check("migration 0334 exists", MIGRATION.is_file(), str(MIGRATION))
    if not MIGRATION.is_file():
        return
    source = MIGRATION.read_text(encoding="utf-8")
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
