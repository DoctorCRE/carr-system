#!/usr/bin/env python3
"""control-plane-registry-gate compare(): superseded versions are history, not drift.

tools/control-plane.py sync disables a superseded workflow version and keeps
its row (jobs reference it), so a version bump leaves the old row behind. The
2026-10-19 release run refused staging on exactly that: notes-sweep-hourly v3
disabled beside the manifest's v4.
"""
from __future__ import annotations

import pathlib
import runpy
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
compare = runpy.run_path(str(REPO / "ops/control-plane-registry-gate.py"))["compare"]

WORKFLOW = {"key": "w", "version": 2, "enabled": False, "risk": "low",
            "execution": {"kind": "command"}, "inventory": {}, "recurrence": {},
            "routing": {}, "filtering": {}, "validation": {}, "retry": {},
            "deduplication": {}, "completion": {}, "legacy_schedule": {}}
MANIFEST = {"workflows": [WORKFLOW], "cognition_jobs": []}


def row(key, version, enabled):
    return (key, version, enabled, "low", "command", {}, {}, {}, {}, {}, {}, {}, {}, {}, {})


class Cursor:
    def __init__(self, rows):
        self.rows, self.sql = rows, ""

    def execute(self, sql, *_):
        self.sql = sql

    def fetchall(self):
        return self.rows if "ops.job_definition" in self.sql else []

    def fetchone(self):
        return (None,) if "to_regclass" in self.sql else (0,)


def failures(rows):
    return compare(Cursor(rows), MANIFEST)[0]


def main() -> int:
    checks = {
        "exact registry passes": failures([row("w", 2, False)]) == [],
        "disabled superseded version passes": failures([row("w", 1, False), row("w", 2, False)]) == [],
        "enabled superseded version fails": failures([row("w", 1, True), row("w", 2, False)]) != [],
        "undeclared key fails even disabled": failures([row("w", 2, False), row("x", 1, False)]) != [],
        "missing declared version fails": failures([row("w", 1, False)]) != [],
    }
    bad = [name for name, ok in checks.items() if not ok]
    for name in bad:
        print(f"FAIL: {name}", file=sys.stderr)
    print(f"control-plane-registry-gate-selftest: {len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
