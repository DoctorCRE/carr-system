#!/usr/bin/env python3
"""Hermetic regression checks for the DB gate's authority-login boundary."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration




REPO = Path(__file__).resolve().parents[1]
GATE = (REPO / "ops" / "control-plane-db-gate.py").read_text(encoding="utf-8")
MIGRATION = read_migration(REPO, "0161_control_plane_authority_boundary.sql")
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def main() -> int:
    compact_gate = " ".join(GATE.lower().split())
    compact_migration = " ".join(MIGRATION.lower().split())
    check("DB gate does not impersonate externally provisioned authority logins",
          "set session authorization carr_authority_" not in compact_gate)
    check("DB gate leaves authority-login provisioning outside its rollback fixture",
          "create role carr_authority_joe" not in compact_gate
          and "create role carr_authority_dell" not in compact_gate)
    check("DB gate still proves an unmapped owner session is refused",
          "authority_actor_mismatch" in compact_gate
          and "unmapped database session was accepted as human authority" in compact_gate)
    check("authority migration derives the actor from the authenticated session",
          "case session_user" in compact_migration
          and "when 'carr_authority_joe' then return 'joe'" in compact_migration
          and "when 'carr_authority_dell' then return 'dell'" in compact_migration)
    check("authority migration leaves partner login provisioning external",
          "create role carr_authority_joe login" not in compact_migration
          and "create role carr_authority_dell login" not in compact_migration)
    check("DB gate labels the authority result as structural plus owner refusal only",
          "owner cutover refusal exercised" in compact_gate
          and "externally provisioned real authority-dsn probe" in compact_gate
          and "positive partner identity tests require" not in compact_gate)
    print(f"control-plane DB gate authority selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
