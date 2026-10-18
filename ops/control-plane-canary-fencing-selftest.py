#!/usr/bin/env python3
"""Static contract checks for deterministic canary rollout fencing."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import migration_names, read_migration



REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "migrations" / "0169_control_plane_canary_fencing.sql"
failures: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        failures.append(label)


def main() -> int:
    source = read_migration(MIGRATION.parent.parent, MIGRATION.name).lower() if MIGRATION.name in migration_names(MIGRATION.parent.parent) else ""
    check("forward migration exists", bool(source))
    check("generic claim is replaced", "create or replace function ops.claim_job(" in source)
    check("mode-filtered claim is replaced", "create or replace function ops.claim_job_mode(" in source)
    check("claim candidates require an enabled definition",
          source.count("join ops.job_definition d") >= 2
          and source.count("d.enabled") >= 2)
    check("claims lock the definition row against a concurrent sync",
          source.count("for update of j,d skip locked") >= 2)
    check("definition disablement fences queued work",
          "create trigger job_definition_fence_queued_jobs" in source
          and "state='cancelled'" in source
          and "state in ('queued','retry_wait')" in source)
    check("fencing leaves immutable override evidence",
          "'override'" in source and "'definition_disabled'" in source)
    check("running jobs are not rewritten by the fencing trigger",
          "state in ('queued','retry_wait')" in source
          and "state in ('queued','retry_wait','running')" not in source)
    print(f"control-plane canary fencing selftest — {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
