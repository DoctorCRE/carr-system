#!/usr/bin/env python3
"""Hermetic fresh-order tests for the FK-bound scheduler surface projection."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration


import json
from typing import Any, Literal

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import (scheduler_launchd_rows, scheduler_provider_rows,
                                                  scheduler_surface_rows)
from lib.loadpy import load_module_from_path

CONTROL_PLANE = load_module_from_path(
    "control_plane_scheduler_sync_under_test", str(REPO / "tools" / "control-plane.py"))
MANIFEST = json.loads((REPO / "ops" / "config" / "control-plane-workflows.v1.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((REPO / "ops" / "config" / "control-plane-scheduler-cutover.v1.json").read_text(encoding="utf-8"))
MIGRATION = read_migration(REPO, "0176_legacy_schedule_disable_receipt.sql").lower()
REBUILD_GATE = (REPO / "ops" / "p1-rebuild-gate.py").read_text(encoding="utf-8").lower()
DB_GATE = (REPO / "ops" / "control-plane-db-gate.py").read_text(encoding="utf-8").lower()
CI = (REPO / "ops" / "ci.sh").read_text(encoding="utf-8").lower()
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


class Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((" ".join(sql.lower().split()), params))


def main() -> int:
    expected = scheduler_surface_rows(REGISTRY, manifest=MANIFEST)
    expected_provider = scheduler_provider_rows(REGISTRY, manifest=MANIFEST, repo=REPO)
    expected_launchd = scheduler_launchd_rows(REGISTRY, manifest=MANIFEST, repo=REPO)
    check("complete checked-in inventory converts to exactly 20 typed surface rows",
          len(expected) == 20 and len({row[2] for row in expected}) == 20)

    missing = json.loads(json.dumps(REGISTRY))
    missing["surfaces"] = missing["surfaces"][1:]
    cursor = Cursor()
    try:
        CONTROL_PLANE.sync_scheduler_surface_registry(cursor, manifest=MANIFEST, registry=missing)
        refused = False
    except RuntimeError:
        refused = True
    check("incomplete registry refuses before any database registry write", refused and cursor.calls == [])

    cursor = Cursor()
    count = CONTROL_PLANE.sync_scheduler_surface_registry(cursor, manifest=MANIFEST, registry=REGISTRY)
    inserts = [call for call in cursor.calls if "insert into ops.legacy_schedule_surface_registry" in call[0]]
    check("sync upserts every exact workflow/version/kind/surface/locator tuple",
          count == len(expected) and [call[1] for call in inserts] == expected)
    provider_inserts = [call for call in cursor.calls if "insert into ops.legacy_schedule_provider_contract" in call[0]]
    check("sync derives all Claude provider contracts from manifest recurrence and tracked definitions",
          len(expected_provider) == sum(row["scheduler_kind"] == "claude-code" for row in REGISTRY["surfaces"])
          and [call[1] for call in provider_inserts] == [
              (row[2], row[0], row[1], row[3], row[4], row[5], row[6], row[7]) for row in expected_provider])
    launchd_inserts = [call for call in cursor.calls if "insert into ops.legacy_schedule_launchd_contract" in call[0]]
    check("sync derives every launchd contract from tracked plists and native recurrences",
          len(expected_launchd) == sum(row["scheduler_kind"] == "launchd" for row in REGISTRY["surfaces"])
          and [call[1] for call in launchd_inserts] == [
              (row[2], row[0], row[1], row[3], row[4], row[5], row[6], row[7], row[8], row[9])
              for row in expected_launchd])
    check("sync prunes only after all complete exact upserts",
          cursor.calls[0][0] == "delete from ops.legacy_schedule_provider_contract"
          and next(call for call in cursor.calls if call[0].startswith("delete from ops.legacy_schedule_surface_registry"))[1]
          == ([row[2] for row in expected],)
          and cursor.calls[-1][0].startswith("delete from ops.legacy_schedule_launchd_contract")
          and cursor.calls[-1][1] == ([row[2] for row in expected_launchd],))
    evolved = json.loads(json.dumps(REGISTRY))
    claude_index = next(i for i, row in enumerate(evolved["surfaces"]) if row["scheduler_kind"] == "claude-code")
    evolved_surface_id = evolved["surfaces"][claude_index]["surface_id"] + ".evolved"
    evolved["surfaces"][claude_index]["surface_id"] = evolved_surface_id
    evolved_expected = scheduler_surface_rows(evolved, manifest=MANIFEST)
    evolved_cursor = Cursor()
    CONTROL_PLANE.sync_scheduler_surface_registry(evolved_cursor, manifest=MANIFEST, registry=evolved)
    evolved_surface_inserts = [call for call in evolved_cursor.calls
                               if "insert into ops.legacy_schedule_surface_registry" in call[0]]
    check("a complete versioned registry evolution updates every bound surface field before pruning",
          [call[1] for call in evolved_surface_inserts] == evolved_expected
          and any(row[2] == evolved_surface_id for row in evolved_expected)
          and "workflow_key=excluded.workflow_key,workflow_version=excluded.workflow_version," in evolved_surface_inserts[0][0]
          and "locator=excluded.locator,scheduler_kind=excluded.scheduler_kind" in evolved_surface_inserts[0][0])
    check("current provider projection is removed before any parent tuple evolution",
          evolved_cursor.calls[0][0] == "delete from ops.legacy_schedule_provider_contract"
          and evolved_cursor.calls[1][0] == "delete from ops.legacy_schedule_launchd_contract"
          and evolved_cursor.calls.index(evolved_surface_inserts[0]) > 1)
    check("migration keeps FK and contains no pre-definition surface seed",
          "foreign key (workflow_key, workflow_version) references ops.job_definition(key, version)" in MIGRATION
          and "insert into ops.legacy_schedule_surface_registry" not in MIGRATION)
    check("fresh rebuild explicitly runs control-plane sync before DB registry parity gate",
          'str(repo / "tools" / "control-plane.py"), "sync"' in REBUILD_GATE
          and REBUILD_GATE.find('str(repo / "tools" / "control-plane.py"), "sync"')
          < REBUILD_GATE.find('"control-plane-db-gate.py"'))
    check("DB gate rejects empty or stale surface registry against exact checked-in tuples",
          "scheduler surface registry is empty, stale" in DB_GATE
          and "from ops.legacy_schedule_surface_registry" in DB_GATE
          and "actual_surfaces != expected_surfaces" in DB_GATE)
    migration_apply = 'tools/migrate.py --apply --yes'
    authority_sync = '"$py" tools/control-plane.py sync'
    db_gate_loop = 'for g in ops/*-gate.py; do'
    check("CI orders throwaway migration, real authority sync, then DB acceptance gates",
          migration_apply in CI and authority_sync in CI and db_gate_loop in CI
          and CI.find(migration_apply) < CI.find(authority_sync) < CI.find(db_gate_loop))
    check("CI fails the migration class when the real authority sync fails",
          'migration-control-plane-sync.log' in CI
          and 'bad migration "control-plane registry sync failed after migrations"' in CI
          and 'database_url="$dsn" run_quiet "$logdir/migration-control-plane-sync.log"' in CI)
    print(f"control-plane scheduler cutover sync selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
