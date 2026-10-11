"""Reviewed-rule baseline shared by rollback-only policy acceptance gates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from policy_epoch_fixture import analyze_rule_projection

REPO = Path(__file__).resolve().parents[1]


def seed_reviewed_rule_projection(cur, *, slug: str, title: str, rule_label: str, uuid_tail: str) -> None:
    """Construct a full reviewed map; bootstrap once through the restored observer."""
    def uuid_for(short: str) -> str:
        return f"{short}-0000-4000-8000-{uuid_tail}"

    raw = (REPO / "ops/config/rule-enforcement-map.json").read_bytes()
    reviewed = json.loads(raw)
    map_digest = hashlib.sha256(raw).hexdigest()
    scope_by_short = {
        short: scope
        for scope, short_ids in reviewed["active_rule_ids"].items()
        for short in short_ids
    }
    cur.execute(
        """insert into public.actor(slug,kind,display_name) values ('joe','human','Joe')
             on conflict(slug) do update set display_name=excluded.display_name
             returning id"""
    )
    joe = cur.fetchone()[0]
    document_id = cur.execute(
        """insert into public.doctrine_document(slug,title,content_class,created_by)
             values (%s,%s,'reference',%s)
             returning id""",
        (slug, title, joe),
    ).fetchone()[0]
    generation = cur.execute("select generation from public.doctrine_meta where id=1").fetchone()[0]
    cur.execute(
        """insert into public.doctrine_snapshot(document_id,generation,snapshot_json,content_hash)
             values (%s,%s,%s::jsonb,%s)""",
        (document_id, generation, json.dumps({"document": {"slug": slug}, "sections": []}),
         hashlib.sha256(slug.encode()).hexdigest()),
    )
    cur.execute("alter table public.rule disable trigger user")
    try:
        for short, scope in sorted(scope_by_short.items()):
            cur.execute(
                """insert into public.rule(id,statement,taught_by,status,activated_by,personal_to)
                     values (%s,%s,%s,'active',%s,%s)""",
                (uuid_for(short), f"{rule_label} {short}", joe, joe,
                 joe if scope == "joe" else None),
            )
    finally:
        cur.execute("alter table public.rule enable trigger user")
    layers = sorted(reviewed["rule_load_layers"].items())
    if not layers:
        raise RuntimeError("reviewed rule projection has no bootstrap layer")

    def insert_layer(short, contract):
        cur.execute(
            """insert into ops.rule_load_layer
                 (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                 values (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (uuid_for(short), short, contract["load_layer"], contract.get("packs", []),
             scope_by_short[short], contract.get("why"),
             "ops/config/rule-enforcement-map.json", map_digest),
        )

    # All validation and FK triggers stay active. Only the epoch observers are
    # suspended while constructing the baseline: each fingerprints every sealed
    # registry, so queuing one per seed row makes fixture cost grow with history.
    observers = (
        ("ops.rule_pack", "scac_epoch_rule_pack"),
        ("ops.rule_load_layer", "scac_epoch_rule_load_layer"),
    )
    for table, trigger in observers:
        cur.execute(f"alter table {table} disable trigger {trigger}")
    try:
        for pack, contract in sorted(reviewed["rule_packs"].items()):
            cur.execute(
                """insert into ops.rule_pack(pack,title,description,triggers,source)
                     values (%s,%s,%s,%s,%s)""",
                (pack, contract["title"], contract["description"], contract["triggers"],
                 "ops/config/rule-enforcement-map.json"),
            )
        for short, contract in layers[:-1]:
            insert_layer(short, contract)
    finally:
        for table, trigger in observers:
            cur.execute(f"alter table {table} enable trigger {trigger}")
    # The last insert uses the actual constraint trigger, proving it observes
    # the complete projection after restoration rather than a manual refresh.
    insert_layer(*layers[-1])
    analyze_rule_projection(cur)
    cur.execute("set constraints ops.scac_epoch_rule_load_layer immediate")
    cur.execute("set constraints all immediate")
    cur.execute("set constraints all deferred")
