#!/usr/bin/env python3
"""Selftest for ops/rule-load-layer-check.py.

Every case here is a MUTATION: take a map that passes, break exactly one thing,
and assert the check names it. A suite that only proves the good case passes is
the shape rule e65efc68 was written about — it cannot tell a working check from
one whose loop never runs.

The last case is the one that matters most: the check must go red on THIS
repository's real map when the map is mutated, not only on a synthetic fixture.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "rule_load_layer_check", REPO / "ops" / "rule-load-layer-check.py")
assert SPEC and SPEC.loader
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

FAILURES: list[str] = []


def expect(label: str, errors: list[str], fragment: str | None) -> None:
    """fragment None means 'this map must be clean'."""
    if fragment is None:
        if errors:
            FAILURES.append(f"{label}: expected clean, got {errors}")
        return
    if not any(fragment in e for e in errors):
        FAILURES.append(f"{label}: expected an error containing {fragment!r}, got {errors}")


def good() -> dict:
    return {
        "active_rule_ids": {"shared": ["aaaaaaaa", "bbbbbbbb", "cccccccc"],
                            "joe": ["dddddddd"], "dell": []},
        "rule_controls": {
            "aaaaaaaa": {"enforcement_class": "judgment_ambient"},
            "bbbbbbbb": {"enforcement_class": "deny_gate"},
            "cccccccc": {"enforcement_class": "surfacing"},
            "dddddddd": {"enforcement_class": "judgment_ambient"},
        },
        "layer0_shared_cap": 1,
        "rule_packs": {
            "demo-pack": {"title": "Demo", "description": "d", "triggers": ["demo"]},
        },
        "rule_load_layers": {
            "aaaaaaaa": {"load_layer": "layer0", "packs": [], "why": "binds every session"},
            "bbbbbbbb": {"load_layer": "control", "packs": []},
            "cccccccc": {"load_layer": "pack", "packs": ["demo-pack"]},
            "dddddddd": {"load_layer": "layer0", "packs": [], "why": "joe, every turn"},
        },
    }


def mutate(fn) -> list[str]:
    data = good()
    fn(data)
    return check.validate(data)


expect("the unmutated fixture is clean", check.validate(good()), None)

expect("an untagged active rule is named",
       mutate(lambda d: d["rule_load_layers"].pop("cccccccc")),
       "carries no load layer")

expect("a wildcard pack is refused",
       mutate(lambda d: d["rule_load_layers"]["cccccccc"].update(packs=["*"])),
       "wildcard pack is not scoping")

expect("an undefined pack is refused",
       mutate(lambda d: d["rule_load_layers"]["cccccccc"].update(packs=["nope"])),
       "undefined pack")

expect("a pack rule with no pack is refused",
       mutate(lambda d: d["rule_load_layers"]["cccccccc"].update(packs=[])),
       "never delivered")

expect("control on an advisory rule is refused",
       mutate(lambda d: d["rule_load_layers"]["aaaaaaaa"].update(
           load_layer="control", why=None)),
       "would remove its only bind")

expect("control on a surfacing rail is refused",
       mutate(lambda d: d["rule_load_layers"]["cccccccc"].update(
           load_layer="control", packs=[])),
       "would remove its only bind")

expect("layer0 carrying a pack is refused",
       mutate(lambda d: d["rule_load_layers"]["aaaaaaaa"].update(packs=["demo-pack"])),
       "cannot carry a pack")

expect("layer0 with no stated reason is refused",
       mutate(lambda d: d["rule_load_layers"]["aaaaaaaa"].update(why="  ")),
       "needs a stated reason")

expect("layer0 over the shared cap is refused",
       mutate(lambda d: (d["rule_load_layers"]["cccccccc"].update(
           load_layer="layer0", packs=[], why="x"),
           d["rule_controls"].__setitem__("cccccccc", {"enforcement_class": "surfacing"}))),
       "over the reviewed cap")

expect("a pack with no triggers is refused",
       mutate(lambda d: d["rule_packs"]["demo-pack"].update(triggers=[])),
       "can never load")

expect("a wildcard trigger is refused",
       mutate(lambda d: d["rule_packs"]["demo-pack"].update(triggers=["*"])),
       "not a trigger")

expect("a pack with no description is refused",
       mutate(lambda d: d["rule_packs"]["demo-pack"].update(description="")),
       "needs a description")

expect("an unknown layer name is refused",
       mutate(lambda d: d["rule_load_layers"]["cccccccc"].update(load_layer="someday")),
       "unknown load layer")

expect("an unknown personal scope is refused",
       mutate(lambda d: d["active_rule_ids"].update(other=[])),
       "unknown scope")

expect("one rule cannot belong to two scopes",
       mutate(lambda d: d["active_rule_ids"]["dell"].append("aaaaaaaa")),
       "appears in both")

expect("a tag for a rule that is not active is refused",
       mutate(lambda d: d["rule_load_layers"].__setitem__(
           "eeeeeeee", {"load_layer": "layer0", "packs": [], "why": "x"})),
       "not an active rule")

# ── the live map, and the live map mutated ──────────────────────────────────
live = json.loads((REPO / "ops" / "config" / "rule-enforcement-map.json").read_text())
expect("this repository's own map is deliverable", check.validate(live), None)

broken = copy.deepcopy(live)
first = sorted(k for k, v in broken["rule_load_layers"].items()
               if v["load_layer"] == "pack")[0]
broken["rule_load_layers"][first]["packs"] = ["*"]
expect("the live map goes red when one real rule is wildcarded",
       check.validate(broken), "wildcard pack is not scoping")

dropped = copy.deepcopy(live)
dropped["rule_load_layers"].pop(sorted(dropped["rule_load_layers"])[0])
expect("the live map goes red when one real rule loses its tag",
       check.validate(dropped), "carries no load layer")

if FAILURES:
    print("rule-load-layer-check-selftest: FAIL", file=sys.stderr)
    for line in FAILURES:
        print(f"  {line}", file=sys.stderr)
    raise SystemExit(1)
print("rule-load-layer-check-selftest: 20 cases passed")
