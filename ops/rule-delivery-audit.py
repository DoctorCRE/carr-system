#!/usr/bin/env python3
"""Exit audit: every active rule has a reviewed delivery tag, and shadow mode is
still doing its job.

The delivery twin of ops/rule-admission-audit.py. That audit answers "can
anything refuse this rule"; this one answers "will this rule ever reach a
session", which is the question the 2026-08-23 rules council found nobody was
asking. Split the same way for the same reason: counts() takes a cursor so the
nightly watch and this audit count once rather than twice (rule a8c55a47).

WHAT COUNTS AS A FAILURE, and each is a way the delivery half could go quiet:
  untagged   an active rule no layer covers — it would be omitted by omission
  orphaned   a tag naming a rule that is not active — dead law in a live pack
  wildcarded a pack tag of '*', the scoping-costume failure the chair named
  packless   a rule tagged for pack delivery that names no pack
  emptypack  a defined pack no active rule is in — a trigger that loads nothing
  scope      a delivery tag disagrees with the rule's personal owner
  layer0     the shared Layer 0 set over its reviewed cap

MODE IS REPORTED, NEVER FAILED ON. 'shadow' is the correct state for the first
week and 'enforced' is the correct state after it; a check that called either
one wrong would be asserting a calendar it cannot read.

  0  every active rule carries a deliverable tag
  1  a gap (the counts say which shape)
  2  could not run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import psycopg

DEFAULT_LAYER0_CAP = 35


def counts(cur: Any, cap: int = DEFAULT_LAYER0_CAP) -> dict[str, Any]:
    """Read the delivery contract's numbers. Read-only, and it reads no rule text."""
    fields = (
        "total", "untagged", "orphaned", "layer0", "control", "pack",
        "layer0_shared", "layer0_shared_cap", "wildcarded", "packless",
        "packs", "emptypack", "scope_mismatch", "mode",
    )
    cur.execute("select * from ops.rule_delivery_audit_counts(%s)", (cap,))
    row = cur.fetchone()
    if row is None:
        return {**{field: 0 for field in fields[:-1]}, "mode": "(none)"}
    return dict(zip(fields, row, strict=True))


def failing(c: dict[str, Any], *, allow_empty_store: bool = False) -> bool:
    """The one place that decides whether these numbers are a failure."""
    if c["total"] == 0:
        return not allow_empty_store
    return bool(c["untagged"] or c["orphaned"] or c["wildcarded"] or c["packless"]
                or c["emptypack"] or c["scope_mismatch"] or c["mode"] == "(none)"
                or c["layer0_shared"] > c["layer0_shared_cap"])


def render(c: dict[str, Any]) -> str:
    return (f"total={c['total']} untagged={c['untagged']} orphaned={c['orphaned']} "
            f"layer0={c['layer0']}(shared {c['layer0_shared']}/{c['layer0_shared_cap']}) "
            f"control={c['control']} pack={c['pack']} packs={c['packs']} "
            f"wildcarded={c['wildcarded']} packless={c['packless']} "
            f"emptypack={c['emptypack']} scope_mismatch={c['scope_mismatch']} "
            f"mode={c['mode']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-empty-store", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("rule-delivery-audit: DATABASE_URL required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            c = counts(cur)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedFunction):
        # "NOT INSTALLED" AND "INCOMPLETE" ARE DIFFERENT FINDINGS (rule 88e9b5eb).
        # Absent tables mean migration 0291 has not been applied here, which has a
        # different remedy from tags that are missing rows — and letting the
        # traceback through would report the first as the second.
        print("rule-delivery-audit: the delivery tables are absent — migration "
              "0291_rule_delivery_layers.sql has not been applied to this database. "
              "That is not the same finding as incomplete tags: apply the migration "
              "first, then re-run.", file=sys.stderr)
        return 2
    bad = failing(c, allow_empty_store=args.allow_empty_store)
    if args.json:
        print(json.dumps({"ok": not bad, **c}, sort_keys=True))
    else:
        print("rule-delivery-audit: " + render(c))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
