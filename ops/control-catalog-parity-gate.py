#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate: the control catalog matches the repository.

WHAT THIS DEFENDS. ops.enforcement_control_catalog decides which controls
approve-rule will accept as real enforcement. Until 2026-08-22 it was populated
by a hand-written migration per control, and the two copies drifted exactly as
two copies do: the repository's enforcement map declared 59 controls and the
table held 3, so approving a rule enforced by any of the other 56 required
shipping SQL first. That is the red tape the map-as-source change removes.

Removing the second transcription only helps if the projection cannot silently
fall behind again, which is what this gate is for. It compiles the catalog from
ops/config/rule-enforcement-map.json exactly as ops/sync_control_catalog.py
does — same module, so the gate cannot check a different rule than the sync
applies — and compares it against the database the migration set built.

WHAT IT REFUSES, and why each half matters:

  MISSING       a control the map declares that the table does not carry.
                approve-rule would refuse a rule the repository believes is
                enforced, and the refusal would name nothing useful.

  UNDECLARED    a control in the table that the map does not declare. This is
                reported, never auto-deleted: an orphan row may still be
                enforcing an active rule, and removing it is a decision. But it
                must not pass silently, because an unreviewed row in this table
                is an unreviewed claim about what counts as enforcement.

  DISAGREEMENT  a control whose class, implementation, test or installed flag
                differs between the two. Installed drift is the dangerous one:
                a control marked installed whose test nothing runs is precisely
                the "recitation is not enforcement" failure (rule ab814a26).

Writes nothing: every statement runs inside a transaction that is rolled back.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import psycopg

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from retained_pack_delivery import (
    ACCEPTED_STATE_LABEL, RETAINED_PACK_DELIVERY, RETAINED_PACK_DELIVERY_SQL,
)
# Loaded by path rather than imported by name: ops/ is not a package, and the
# point is that this gate runs THE SAME compiler the sync applies. If it grew its
# own copy of the rule, the gate could pass while the sync wrote something else.
_spec = importlib.util.spec_from_file_location(
    "sync_control_catalog", REPO / "ops" / "sync_control_catalog.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - a missing file is a broken checkout
    raise SystemExit("control-catalog-parity-gate: FAIL — ops/sync_control_catalog.py is unreadable")
_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sync)


def fail(message: str) -> int:
    print(f"control-catalog-parity-gate: FAIL — {message}", file=sys.stderr)
    return 1


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")

    try:
        expected = {r["control_key"]: r for r in _sync.compile_catalog()}
    except _sync.CatalogError as exc:
        return fail(f"the enforcement map itself is invalid: {exc}")

    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select control_key, implementation_ref, test_ref, enforcement_class, "
                "installed, verified_at is not null "
                "from ops.enforcement_control_catalog")
            actual = {row[0]: row for row in cur.fetchall()}

            missing = sorted(set(expected) - set(actual))
            if missing:
                return fail(
                    f"{len(missing)} control(s) the map declares are absent from the catalog, so "
                    f"approve-rule would refuse any rule they enforce: {', '.join(missing)}. "
                    "Run ops/sync_control_catalog.py --apply, or ship the generated migration.")

            undeclared = sorted(set(actual) - set(expected))
            if undeclared:
                return fail(
                    f"{len(undeclared)} control(s) in the catalog are not declared in "
                    f"ops/config/rule-enforcement-map.json: {', '.join(undeclared)}. "
                    "An unreviewed row here is an unreviewed claim about what counts as "
                    "enforcement. Declare them in the map, or retire them deliberately — "
                    "this gate will not delete a row that may still be enforcing a live rule.")

            cur.execute(f"select exists(select 1 from ops.enforcement_control_catalog c "
                        f"where {RETAINED_PACK_DELIVERY_SQL})")
            retained_row = cur.fetchone()
            assert retained_row is not None
            retained_pack = retained_row[0]
            problems = []
            for key, want in sorted(expected.items()):
                _, impl, test, klass, installed, verified = actual[key]
                accepted_historical = key == RETAINED_PACK_DELIVERY[0] and retained_pack
                if impl != want["implementation_ref"] and not accepted_historical:
                    problems.append(f"{key}: implementation drifted")
                if test != want["test_ref"] and not accepted_historical:
                    problems.append(f"{key}: test reference drifted")
                if klass != want["enforcement_class"]:
                    problems.append(
                        f"{key}: enforcement_class is {klass}, map says {want['enforcement_class']}")
                if bool(installed) != bool(want["installed"]):
                    problems.append(
                        f"{key}: installed={installed} but the repository "
                        f"{'can' if want['installed'] else 'cannot'} verify it"
                        + (f" ({want['not_installed_reason']})" if want["not_installed_reason"] else ""))
                if installed and not verified:
                    problems.append(f"{key}: installed with no verified_at")
            if problems:
                return fail(f"{len(problems)} disagreement(s) between the map and the catalog:\n  "
                            + "\n  ".join(problems))

            installed_count = sum(1 for r in expected.values() if r["installed"])
    except psycopg.Error as exc:
        return fail(str(exc))
    finally:
        conn.rollback()
        conn.close()

    print(f"control-catalog-parity-gate passed: {len(expected)} control(s) declared and present, "
          f"{installed_count} verified installed, "
          f"{len(expected) - installed_count} held back with a stated reason"
          + (f"; {ACCEPTED_STATE_LABEL}" if retained_pack else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
