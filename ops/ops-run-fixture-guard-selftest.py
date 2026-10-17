#!/usr/bin/env python3
"""Acceptance test for migration 0225's fabricated-drill-row guard.

WHAT IT DEFENDS. ops/ci.sh globs ops/*-selftest.py, so the key-recovery
selftest runs on any ordinary sweep. In a worktree whose base predates the
PR #340 credential belts, that selftest still reaches the production ledger,
because tools/ops-record.py's own _load_db_env() re-supplies the real jobs DSN
for any credential name the caller left unset. A guard in any file on main
cannot help there — a stale checkout runs its own copy of every file. So the
refusal lives in the database, and this test states what it must and must not
refuse.

TIER 1 (always runs; no database, no credential): the predicate in the
migration is read as text and checked against the rows it must reject and the
rows it must never reject. The second half is the point. The one-time August
purge also matched "paper-copy key%" whenever detail did not name a carr-202608
dump; that pairing was correct for one afternoon and is wrong forever, because a
genuine September key-recovery row satisfies both halves. A permanent guard
carrying that clause would silently delete real backup-failure history.

This test never opens a connection, so it cannot itself become the thing it is
testing for.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration


import re

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "migrations" / "0225_ops_run_fixture_row_guard.sql"

GUARDED_SOURCES = ("bin/key-recovery-test.sh", "bin/restore-rehearse.sh")

# details the guard MUST refuse — every one is a real fixture string taken from
# the 24 rows that reached production on 2026-08-20
MUST_REFUSE = (
    "dump=carr-20260101.sql.age (123456B, taken 20260101) restored 99.8% of rows",
    "dump=carr-20260201.sql.age (999B, taken 20260201) restored 0 rows",
    "dump=carr-20260301.sql.age (999B, taken 20260301) failed to decrypt",
)

# details the guard MUST accept — genuine drill output, including the shapes a
# real run produces in a month later than August 2026
MUST_ACCEPT = (
    "paper-copy key MATCHES backups-public-key.txt; restore VERIFIED using dump=carr-20260901.sql.age (41203344B)",
    "paper-copy key does NOT match backups-public-key.txt — the offline paper copy is stale",
    "interrupted before the typed key could be compared to backups-public-key.txt",
    "dump=carr-20260817.sql.age (39118002B, taken 20260817) restored 100% of rows",
    "could not create the rehearsal branch",
    "no age identity present",
    # the trap: a genuine dump whose byte count happens to end in 999
    "dump=carr-20260903.sql.age (41203999B, taken 20260903) restored 100% of rows",
    # and one whose byte count happens to contain the fixture size
    "dump=carr-20260904.sql.age (8123456B, taken 20260904) restored 100% of rows",
)


def patterns(sql: str) -> list[str]:
    """The LIKE patterns the guard tests detail against."""
    body = sql.split("returns trigger", 1)[1]
    body = body.split("return new", 1)[0]
    found = re.findall(r"detail like '([^']+)'", body)
    if not found:
        raise AssertionError("no detail LIKE patterns found in the guard body")
    return found


def matches(pattern: str, detail: str) -> bool:
    """SQL LIKE with only % wildcards, which is all this guard uses."""
    if "_" in pattern.replace("_", "_"):
        pass  # no underscore wildcards are used; % is the only metacharacter
    parts = pattern.split("%")
    regex = ".*".join(re.escape(p) for p in parts)
    return re.fullmatch(regex, detail, flags=re.DOTALL) is not None


def main() -> int:
    sql = read_migration(MIGRATION.parent.parent, MIGRATION.name)

    for source in GUARDED_SOURCES:
        assert source in sql, f"the guard must be scoped to {source}"

    assert "before insert on ops.run" in sql, (
        "the guard must fire BEFORE INSERT, or the fabricated row is already in "
        "the ledger by the time it raises"
    )

    pats = patterns(sql)

    banned = [p for p in pats if "paper-copy" in p or "interrupted" in p]
    assert not banned, (
        "the guard must NOT match on drill prose — a genuine key-recovery run "
        f"writes these same words every month. Offending patterns: {banned}"
    )

    for detail in MUST_REFUSE:
        assert any(matches(p, detail) for p in pats), (
            f"the guard fails to refuse a known fixture row: {detail!r}"
        )

    for detail in MUST_ACCEPT:
        hit = [p for p in pats if matches(p, detail)]
        assert not hit, (
            f"the guard would refuse a GENUINE drill row: {detail!r} "
            f"(matched {hit}). Rejecting a real backup failure is worse than "
            f"accepting a fixture one — that record is what a live restore "
            f"emergency is read from."
        )

    print(
        f"ops-run-fixture-guard-selftest: DONE — {len(pats)} patterns, "
        f"{len(MUST_REFUSE)} fixture rows refused, {len(MUST_ACCEPT)} genuine rows accepted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
