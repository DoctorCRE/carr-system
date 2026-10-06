#!/usr/bin/env python3
"""ops/rule-load-layer-check.py — the delivery half of the enforcement map must
be honest before it is allowed anywhere near a session's boot payload.

WHY THIS EXISTS. `rule_controls` in ops/config/rule-enforcement-map.json says how
each rule is ENFORCED. It said nothing about how the rule's text is DELIVERED, so
the loader had exactly one mode: recite all 204 into every session, whatever the
session turned out to be doing. The 2026-08-23 rules council ruled the delivery
half into three layers, and one chair named the way it fails:

    "Do not flip applicable-rules on with wildcard tags and call it scoping.
     That would silently drop rules on undeclared sessions — the exact failure
     347a9ca6 was taught to prevent."

Wildcards are how a scoping change looks finished while delivering nothing, so
this check refuses them structurally rather than trusting a reviewer to notice.

WHAT IT REFUSES, and each refusal is a way the tags could lie:

  1. AN UNTAGGED ACTIVE RULE. Every id in active_rule_ids carries a load layer.
     An untagged rule is invisible to the compiler, and invisible means never
     delivered — the silent drop, arriving as an omission rather than a decision.
  2. A WILDCARD PACK. `packs: ["*"]` is a tag that selects everything, which is
     the current behaviour wearing a scoping costume.
  3. `control` ON A RULE NOTHING CAN REFUSE. This layer means "an installed deny,
     stop or schema control prints this rule at the moment it binds, so boot
     recitation is belt-and-suspenders." That claim is only true when the rule's
     own enforcement_class is one of the built ones. Claiming it for an advisory
     rule removes the rule's ONLY bind and calls the removal an optimisation.
  4. A PACK RULE WITH NO PACK, or a pack this map does not define. A rule tagged
     for delivery-on-trigger with no trigger is never delivered.
  5. A PACK WITH NO TRIGGERS. A pack nothing can fire is a pack nothing loads.
  6. A LAYER 0 RULE CARRYING PACKS. Layer 0 is unconditional; a pack on it is a
     contradiction that reads as a narrowing.
  7. LAYER 0 OVER ITS CAP. The council's target is <=35 shared. A cap nobody
     checks drifts back to 204 one defensible addition at a time.

WHAT IT DELIBERATELY DOES NOT CHECK: whether a given rule is in the RIGHT pack.
That is a judgment, it was made from the full rule bodies, and the `why` field on
every layer0 and control row is where it is recorded for review. This check is
the mechanical half only (rule 5e89c211).

Exit 0 clean · 1 the tags are not deliverable · 2 the map could not be read.

    python3 ops/rule-load-layer-check.py
    python3 ops/rule-load-layer-check.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")

LAYERS = {"layer0", "control", "pack"}
SCOPES = {"shared", "joe", "dell"}
# The enforcement classes that actually refuse or interrupt. `surfacing` is
# deliberately absent: a rail surfaces context, it does not deliver a rule at the
# moment the rule binds, so a surfacing rule tagged `control` would be scoped out
# of the boot payload and land nowhere at all.
DELIVERING_CLASSES = {"deny_gate", "stop_gate", "schema"}
DEFAULT_CAP = 35


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    layers = data.get("rule_load_layers")
    packs = data.get("rule_packs")
    if not isinstance(layers, dict) or not layers:
        return ["rule_load_layers: missing or empty"]
    if not isinstance(packs, dict) or not packs:
        return ["rule_packs: missing or empty"]

    for name, pack in sorted(packs.items()):
        if name == "*":
            errors.append("rule_packs: '*' is not a pack")
        if not isinstance(pack, dict):
            errors.append(f"{name}: pack must be an object")
            continue
        triggers = pack.get("triggers")
        if not isinstance(triggers, list) or not triggers:
            errors.append(f"{name}: a pack with no triggers can never load")
        elif any(not isinstance(t, str) or not t.strip() for t in triggers):
            errors.append(f"{name}: every trigger must be a non-empty string")
        elif "*" in triggers:
            errors.append(f"{name}: '*' is not a trigger")
        for field in ("title", "description"):
            if not str(pack.get(field, "")).strip():
                errors.append(f"{name}: pack needs a {field}")

    scope_by_id: dict[str, str] = {}
    for scope, ids in data.get("active_rule_ids", {}).items():
        if scope not in SCOPES:
            errors.append(f"active_rule_ids: unknown scope {scope!r}")
        if not isinstance(ids, list):
            errors.append(f"active_rule_ids.{scope}: must be a list")
            continue
        for rid in ids:
            if rid in scope_by_id:
                errors.append(
                    f"{rid}: appears in both {scope_by_id[rid]!r} and {scope!r} scopes")
            scope_by_id[rid] = scope
    controls = data.get("rule_controls", {})

    for rid in sorted(scope_by_id):
        if rid not in layers:
            errors.append(f"{rid}: active rule carries no load layer")

    for rid, entry in sorted(layers.items()):
        if rid not in scope_by_id:
            errors.append(f"{rid}: tagged but not an active rule in this map")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{rid}: load-layer entry must be an object")
            continue
        layer = entry.get("load_layer")
        if layer not in LAYERS:
            errors.append(f"{rid}: unknown load layer {layer!r}")
            continue
        tagged = entry.get("packs", [])
        if not isinstance(tagged, list) or any(not isinstance(p, str) for p in tagged):
            errors.append(f"{rid}: packs must be a list of names")
            continue
        if "*" in tagged:
            errors.append(f"{rid}: a wildcard pack is not scoping")
        for name in tagged:
            if name not in packs:
                errors.append(f"{rid}: names undefined pack {name!r}")
        if layer == "layer0":
            if tagged:
                errors.append(f"{rid}: layer0 is unconditional and cannot carry a pack")
            if not str(entry.get("why", "")).strip():
                errors.append(f"{rid}: layer0 needs a stated reason it binds every session")
        elif layer == "control":
            found = controls.get(rid, {}).get("enforcement_class")
            if found not in DELIVERING_CLASSES:
                errors.append(
                    f"{rid}: load layer 'control' claims an installed control delivers this "
                    f"rule, but its enforcement_class is {found!r} — that would remove its "
                    f"only bind")
        elif layer == "pack" and not tagged:
            errors.append(f"{rid}: a pack rule with no pack is never delivered")

    cap = data.get("layer0_shared_cap", DEFAULT_CAP)
    if not isinstance(cap, int) or cap < 1:
        errors.append("layer0_shared_cap must be a positive integer")
    else:
        shared = sum(1 for rid, e in layers.items()
                     if isinstance(e, dict) and e.get("load_layer") == "layer0"
                     and scope_by_id.get(rid) == "shared")
        if shared > cap:
            errors.append(f"layer0 holds {shared} shared rules, over the reviewed cap of {cap}")
    return errors


def counts(data: dict) -> dict[str, int]:
    layers = data.get("rule_load_layers", {})
    scope_by_id = {rid: scope
                   for scope, ids in data.get("active_rule_ids", {}).items()
                   for rid in ids}
    out: Counter[str] = Counter()
    for rid, entry in layers.items():
        if not isinstance(entry, dict):
            continue
        out[entry.get("load_layer", "?")] += 1
        if entry.get("load_layer") == "layer0":
            out[f"layer0_{scope_by_id.get(rid, '?')}"] += 1
    out["packs"] = len(data.get("rule_packs", {}))
    return dict(sorted(out.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--map", default=MAP)
    args = parser.parse_args()
    try:
        with open(args.map, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"rule-load-layer: could not read the map: {exc}", file=sys.stderr)
        return 2
    errors = validate(data)
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors, "counts": counts(data)},
                         indent=2, sort_keys=True))
        return 1 if errors else 0
    if errors:
        print(f"rule-load-layer: FAIL — {len(errors)} problem(s)", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        return 1
    tally = counts(data)
    print("rule-load-layer: OK — "
          + ", ".join(f"{k}={v}" for k, v in tally.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
