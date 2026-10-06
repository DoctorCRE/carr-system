#!/usr/bin/env python3
"""Behavioral contract for ops/rule-jit-compile.py (WR-000019 slice S9).

PINS:
  1. The committed ops/config/rule-jit-triggers.v1.json is exactly what the
     current inputs compile to (--check passes on the real repo state).
  2. Determinism: compiling the same inputs twice yields byte-identical output.
  3. Every trigger's rule_ids is non-empty, sorted, unique, and at most
     max_rules_per_trigger long (the over-delivery cap).
  4. Every rule_id referenced by a seeded_detector or pack_fallback trigger is
     a real JIT-home id in rule-triage.v1.json.
  5. A rule carrying a `detector` field is covered by exactly one
     seeded_detector trigger group (never split across two).
  6. A synthetic 6th rule sharing one (kind, pattern) with 5 already-seeded
     rules is truncated to 5 by the cap, deterministically (lowest ids kept).
  7. fallback_narrowing removes its excluded term from the compiled pack
     fallback pattern, and a pack with every trigger word excluded contributes
     no fallback trigger for that pack at all.
  8. structural_extra rows are stable and independent of rule-triage content
     (present even if no rule anywhere carries a detector).

RUNNING IT. No database, no network:
    python3 ops/rule-jit-compile-selftest.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compiler = load("rule_jit_compile", REPO / "ops" / "rule-jit-compile.py")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL  {name}: {detail}")


TRIAGE = compiler.load_json(compiler.TRIAGE_PATH)
MAP = compiler.load_json(compiler.MAP_PATH)
JIT_IDS = {r["id"] for r in TRIAGE["rules"] if r.get("home") == "jit"}

# 1. the committed file matches a fresh compile
document = compiler.build_document(TRIAGE, MAP)
fresh = compiler.canonical_bytes(document)
check("committed rule-jit-triggers.v1.json matches a fresh compile",
      compiler.OUTPUT_PATH.exists() and compiler.OUTPUT_PATH.read_bytes() == fresh,
      "run ops/rule-jit-compile.py and commit the result")

# 2. determinism
again = compiler.canonical_bytes(compiler.build_document(TRIAGE, MAP))
check("compiling the same inputs twice is byte-identical", fresh == again)

triggers = document["triggers"]
check("at least one trigger compiled", len(triggers) > 0, len(triggers))

# 3. cap and shape invariants
cap = document["max_rules_per_trigger"]
bad_shape = [t["trigger_id"] for t in triggers
             if not t["rule_ids"]
             or len(t["rule_ids"]) > cap
             or t["rule_ids"] != sorted(set(t["rule_ids"]))]
check("every trigger's rule_ids is non-empty, sorted, unique, and capped",
      not bad_shape, bad_shape)

# 4. every referenced rule id from a triage-derived source is a real JIT id
derived = [t for t in triggers if t["source"] in {"seeded_detector", "pack_fallback"}]
unknown = sorted({rid for t in derived for rid in t["rule_ids"]} - JIT_IDS)
check("every seeded/fallback rule id is a real JIT-home id", not unknown, unknown)

# 5. no rule with a detector field is split across two seeded groups
seeded_groups: dict[str, set[str]] = {}
for rule in TRIAGE["rules"]:
    detector = rule.get("detector")
    if isinstance(detector, dict):
        key = f"{detector.get('kind')}|{detector.get('pattern')}"
        seeded_groups.setdefault(rule["id"], set()).add(key)
split = {rid: keys for rid, keys in seeded_groups.items() if len(keys) > 1}
check("no rule carries two different detector keys", not split, split)
seeded_trigger_keys = {(t["kind"], t["pattern"]) for t in triggers if t["source"] == "seeded_detector"}
for rid, keys in seeded_groups.items():
    kind, pattern = next(iter(keys)).split("|", 1)
    check(f"rule {rid}'s detector produced a compiled trigger",
          (kind, pattern) in seeded_trigger_keys, (kind, pattern))

# 6. truncation determinism: 6 rules sharing one detector -> capped at 5, lowest ids kept
fake_triage = copy.deepcopy(TRIAGE)
fake_ids = [f"zz{i:06x}" for i in range(6)]
base = next(r for r in fake_triage["rules"] if r["home"] == "jit")
for fid in fake_ids:
    row = copy.deepcopy(base)
    row["id"] = fid
    row["detector"] = {"kind": "content_regex", "pattern": "zzsynthetictesttrigger"}
    fake_triage["rules"].append(row)
capped_doc = compiler.build_document(fake_triage, MAP)
capped_row = next(t for t in capped_doc["triggers"]
                  if t["kind"] == "content_regex" and t["pattern"] == "zzsynthetictesttrigger")
check("a 6-rule group is truncated to the cap, keeping the lowest ids",
      capped_row["rule_ids"] == sorted(fake_ids)[:cap], capped_row["rule_ids"])

# 7. fallback_narrowing removes an excluded term; exhausting all terms drops the pack
narrowed_triage = copy.deepcopy(TRIAGE)
# Pick a pack that actually produces a pack_fallback trigger today, so the
# "term disappears from the pattern" half of this check is not silently
# skipped depending on which pack happens to sort first.
some_pack = next(t["packs"][0] for t in triggers
                  if t["source"] == "pack_fallback" and t["packs"])
some_terms = [t for t in MAP["rule_packs"][some_pack]["triggers"]]
narrowed_triage["fallback_narrowing"] = {some_pack: {"exclude_terms": [some_terms[0]]}}
narrowed_doc = compiler.build_document(narrowed_triage, MAP)
narrowed_pattern = next(
    (t["pattern"] for t in narrowed_doc["triggers"]
     if t["source"] == "pack_fallback" and some_pack in t["packs"]), None)
if narrowed_pattern is not None:
    import re as _re
    check(f"excluded term {some_terms[0]!r} is absent from {some_pack}'s narrowed fallback pattern",
          _re.escape(some_terms[0]) not in narrowed_pattern, narrowed_pattern)

exhausted_triage = copy.deepcopy(TRIAGE)
exhausted_triage["fallback_narrowing"] = {some_pack: {"exclude_terms": some_terms}}
exhausted_doc = compiler.build_document(exhausted_triage, MAP)
check(f"excluding every trigger word for {some_pack} drops its fallback trigger entirely",
      not any(t["source"] == "pack_fallback" and some_pack in t["packs"]
              for t in exhausted_doc["triggers"]))

# 8. structural extras are present regardless of triage detector content
bare_triage = copy.deepcopy(TRIAGE)
for rule in bare_triage["rules"]:
    rule.pop("detector", None)
bare_triage.pop("fallback_narrowing", None)
bare_doc = compiler.build_document(bare_triage, MAP)
check("structural_extra triggers survive even with no seeded detectors at all",
      any(t["source"] == "structural_extra" for t in bare_doc["triggers"]))
check("structural_extra count matches the hand-reviewed list",
      sum(1 for t in bare_doc["triggers"] if t["source"] == "structural_extra")
      == len(compiler.STRUCTURAL_EXTRA_TRIGGERS))

# 9. rule ede4b241 (cloud model choice, taught 2026-09-29): JIT-home, delivered
# by the delegation-council pack on the moment a session dispatches work to
# another model or a cloud session, and on nothing else.
import re

MODEL_CHOICE = "ede4b241"
routing_rows = [t for t in triggers if MODEL_CHOICE in t["rule_ids"]
                and t["kind"] == "verb"]
check("cloud model choice rule ede4b241 is in the enforcement map",
      MODEL_CHOICE in MAP["rule_controls"] and MODEL_CHOICE in MAP["active_rule_ids"]["shared"])
check("cloud model choice rule is a JIT-home rule delivered by delegation-council",
      MODEL_CHOICE in JIT_IDS
      and MAP["rule_load_layers"].get(MODEL_CHOICE)
      == {"load_layer": "pack", "packs": ["delegation-council"],
          "why": MAP["rule_load_layers"].get(MODEL_CHOICE, {}).get("why")})
check("cloud model choice rule has exactly one verb trigger, seeded from its detector",
      len(routing_rows) == 1 and routing_rows[0]["kind"] == "verb"
      and routing_rows[0]["source"] == "seeded_detector"
      and routing_rows[0]["packs"] == ["delegation-council"]
      and routing_rows[0]["rule_ids"] == [MODEL_CHOICE], routing_rows)
if len(routing_rows) == 1:
    routing_pattern = re.compile(routing_rows[0]["pattern"])
    dispatch_verbs = ["Agent", "mcp__Claude_Code_Remote__create_session",
                      "mcp__Claude_Code_Remote__create_trigger"]
    routine_verbs = ["Read", "Grep", "Glob", "Bash", "Edit", "Write",
                     "mcp__Claude_Code_Remote__list_sessions",
                     "mcp__Claude_Code_Remote__get_session",
                     "mcp__Claude_Code_Remote__list_repos",
                     "mcp__Claude_Code_Remote__get_trigger",
                     "mcp__github__get_file_contents"]
    check("model-choice trigger matches every dispatch verb",
          all(routing_pattern.search(v) for v in dispatch_verbs),
          [v for v in dispatch_verbs if not routing_pattern.search(v)])
    check("model-choice trigger matches no routine read or edit verb",
          not any(routing_pattern.search(v) for v in routine_verbs),
          [v for v in routine_verbs if routing_pattern.search(v)])

if FAILURES:
    print("rule-jit-compile-selftest: FAIL")
    for failure in FAILURES:
        print("  " + failure)
    sys.exit(1)
print("rule-jit-compile-selftest: all cases passed")
