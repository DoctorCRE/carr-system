#!/usr/bin/env python3
"""Export the DB-native rule-admission classification as committed JSON.

WHY THIS EXISTS (WR-000019 slice S10, part 3). ops.rule_admission carries the
database's own three-value enforcement_class (machine_enforceable,
judgment_advisory, human_only) for every rule admit-rule or approve-rule has
processed. ops/config/rule-enforcement-map.json carries a completely separate,
finer-grained classification (five categories, six enforcement_class values)
that a session hand-authors and CI validates for internal consistency. Nothing
ever compared the two against each other, and by design bin/sync-rule-admission-
prod.sh only pushes the file's classification INTO the database — there was no
door for reading the database's own classification back out. Historically the
two have disagreed structurally roughly 4x over (218 rules in the file, 4
admitted in the database the day this was discovered).

ops/rule-classification-parity-check.py is the CI-runnable comparison; it reads
this export and the file map, and neither requires a database connection at
check time — the check is repository content only, matching every other
inventory gate in ops/ci.sh's loop. This script is the ONLY thing that talks to
the database, and it writes what it read, honestly: it does not editorialize,
does not backfill anything, and refuses to write a file claiming coverage it
did not actually read.

Scope matches ops/config/rule-enforcement-map.json's own active_rule_ids: only
rules that are r.status='active' AND carry an admitted (a.state='admitted')
ops.rule_admission row. A rule with no admission row at all is not a
classification disagreement — it is the SEPARATE finding
ops/rule-admission-audit.py already owns (missing=...), and this export leaves
it out rather than inventing a bucket for it.

RUN IT (needs DATABASE_URL; wired through bin/sync-rule-admission-prod.sh
--export, which derives the production DSN the same way --apply does):
    DATABASE_URL=... python3 tools/export-rule-admission.py
    DATABASE_URL=... python3 tools/export-rule-admission.py --out path.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "ops" / "config" / "rule-admission-export.v1.json"


def fetch_admitted(cur) -> dict[str, dict[str, str]]:
    """short_id -> {enforcement_class, state} for every active, admitted rule.

    left(id::text, 8) is the SAME short form every other verb and check in
    this repository already uses (tools.js's resolveRuleId, ops/config/
    rule-enforcement-map.json's active_rule_ids, standing-context's gist
    index) -- not a new convention invented for this export.
    """
    cur.execute(
        """select left(r.id::text, 8) as short_id, a.enforcement_class, a.state
             from rule r
             join ops.rule_admission a on a.rule_id = r.id
            where r.status = 'active' and a.state = 'admitted'
            order by short_id"""
    )
    out: dict[str, dict[str, str]] = {}
    for short_id, enforcement_class, state in cur.fetchall():
        if short_id in out:
            # Two active rules sharing an 8-character prefix would make this
            # export ambiguous the same way a short-id lookup would be; refuse
            # loudly rather than silently keeping one and dropping the other.
            raise SystemExit(
                f"export-rule-admission: REFUSED — short id {short_id!r} is not "
                "unique among active admitted rules"
            )
        out[short_id] = {"enforcement_class": enforcement_class, "state": state}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                         help="where to write the export (default: %(default)s)")
    parser.add_argument("--source", default="production",
                         help="label recorded in the export's 'source' field")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("export-rule-admission: DATABASE_URL is required", file=sys.stderr)
        return 2

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        rules = fetch_admitted(cur)

    payload = {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": args.source,
        "rule_count": len(rules),
        "rules": rules,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"export-rule-admission: wrote {len(rules)} admitted active rule(s) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
