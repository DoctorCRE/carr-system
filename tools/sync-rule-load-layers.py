#!/usr/bin/env python3
"""Install the reviewed delivery tags — which rules reach a session, and when.

The companion to tools/sync-rule-admission.py. That one compiles the reviewed
map's ENFORCEMENT half into ops.rule_admission; this one compiles its DELIVERY
half into ops.rule_pack and ops.rule_load_layer, so standing-context can compute
a Layer 0 boot and a pack instead of reciting everything.

THREE RAILS, and each one is a way the tags could quietly stop being true:

  1. IT REFUSES ON ANY ACTIVE RULE WITH NO TAG. A rule taught after the map was
     last extended has no delivery decision behind it, and defaulting it either
     way is wrong: default to layer0 and the boot payload grows back one rule at
     a time, default to a pack and the rule goes silent. So it stops and names
     the rule, exactly as the admission backfill does for a missing contract.
  2. IT REFUSES A TAG FOR A RULE THAT IS NOT ACTIVE. A retired rule keeping a
     delivery row is how a pack quietly carries dead law.
  3. IT DELETES TAGS THE MAP NO LONGER CARRIES. The map is the reviewed
     inventory; a row the map dropped must not survive in the database, or the
     two disagree and the database wins silently.

The map digest is stamped on every row, so a session can ask which reviewed map
produced the delivery it got.

SCOPE IS NOT MAP CONFIGURATION. The durable rule row's personal_to owner is the
authority for shared/Joe/Dell scope. active_rule_ids is the reviewed coverage
inventory, but older render lanes can flatten a partner-only surface into that
inventory. The compiler therefore proves the id is reviewed, then writes scope
from rule.personal_to; the production audit independently compares them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "ops" / "config" / "rule-enforcement-map.json"
SOURCE = "ops/config/rule-enforcement-map.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-empty-store", action="store_true",
                        help="staging only: accept a sanitized store with zero active rules")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")

    raw = MAP.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw)
    packs = data["rule_packs"]
    layers = data["rule_load_layers"]
    reviewed_ids = {rid for ids in data["active_rule_ids"].values() for rid in ids}

    counts = {"packs": 0, "tagged": 0, "removed": 0}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""select r.id, owner.slug
                         from rule r
                         left join actor owner on owner.id = r.personal_to
                        where r.status='active'""")
        active = {str(row[0]): {"short": str(row[0])[:8],
                               "scope": str(row[1]) if row[1] else "shared"}
                  for row in cur.fetchall()}
        if not active and args.allow_empty_store:
            print(json.dumps({"active_rules": 0, **counts,
                              "note": "sanitized empty store; nothing to tag"}, sort_keys=True))
            return 0
        short_to_uuid: dict[str, str] = {}
        for uuid, rule in active.items():
            short = rule["short"]
            if short in short_to_uuid:
                raise RuntimeError(f"two active rules share the short id {short}")
            short_to_uuid[short] = uuid

        untagged = sorted(short for short in short_to_uuid if short not in layers)
        if untagged:
            raise RuntimeError(
                "active rule(s) carry no reviewed delivery tag: " + ", ".join(untagged)
                + " — extend rule_load_layers in " + SOURCE + " and have it reviewed")
        stale = sorted(short for short in layers if short not in short_to_uuid)
        if stale:
            raise RuntimeError(
                "delivery tag(s) name a rule that is not active: " + ", ".join(stale))
        missing_inventory = sorted(
            rule["short"] for rule in active.values() if rule["short"] not in reviewed_ids)
        if missing_inventory:
            raise RuntimeError(
                "active rule(s) are absent from the reviewed inventory: "
                + ", ".join(missing_inventory))

        for name, pack in sorted(packs.items()):
            cur.execute("""insert into ops.rule_pack (pack,title,description,triggers,source)
                           values (%s,%s,%s,%s,%s)
                           on conflict (pack) do update set
                             title=excluded.title, description=excluded.description,
                             triggers=excluded.triggers, source=excluded.source,
                             updated_at=now()""",
                        (name, pack["title"], pack["description"], pack["triggers"], SOURCE))
            counts["packs"] += 1
        cur.execute("delete from ops.rule_pack where pack <> all(%s)", (sorted(packs),))
        counts["removed"] += cur.rowcount

        for short, entry in sorted(layers.items()):
            rule_id = short_to_uuid[short]
            cur.execute("""insert into ops.rule_load_layer
                             (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                           values (%s,%s,%s,%s,%s,%s,%s,%s)
                           on conflict (rule_id) do update set
                             short_id=excluded.short_id, load_layer=excluded.load_layer,
                             packs=excluded.packs, scope=excluded.scope, why=excluded.why,
                             source=excluded.source, map_digest=excluded.map_digest,
                             updated_at=now()""",
                        (rule_id, short, entry["load_layer"], entry.get("packs", []),
                         active[rule_id]["scope"], entry.get("why"), SOURCE, digest))
            counts["tagged"] += 1
        cur.execute("delete from ops.rule_load_layer where short_id <> all(%s)", (sorted(layers),))
        counts["removed"] += cur.rowcount
        conn.commit()

    print(json.dumps({"active_rules": len(active), "map_digest": digest, **counts},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
