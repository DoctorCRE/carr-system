#!/usr/bin/env python3
"""Behavioral contract for ops/rule-delivery-adjudication-apply.py (WR-000019 S9).

PINS:
  1. extract_findings grounds every finding in the pack's OWN closed keyword
     vocabulary — a word that is not one of the pack's configured triggers is
     never proposed, no matter how the reason text is phrased.
  2. Multiple "<pack> fired on ..." spans in one reason each contribute their
     own finding; an unknown pack name is ignored.
  3. Occurrence counting aggregates across files and only "explained" events
     count — "remediated" events never contribute.
  4. proposed_changes respects --min-occurrences and skips a (pack, word)
     pair already present in fallback_narrowing.
  5. apply_changes is idempotent: applying the same changes twice never
     duplicates a term, and a second pack's exclusion does not disturb the
     first's.
  6. End to end, --dry-run (the default) writes nothing; --apply writes the
     narrowing into a triage file AND recompiles a trigger table from it,
     fully isolated from the real repo's own config files.
  7. Smoke test: running in dry-run mode against the REAL committed adjudication
     files and config exits 0 and writes nothing (the real repo's files are
     read, never written, in this mode).

RUNNING IT. No database, no network:
    python3 ops/rule-delivery-adjudication-apply-selftest.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load("rule_delivery_adjudication_apply", REPO / "ops" / "rule-delivery-adjudication-apply.py")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL  {name}: {detail}")


RULE_PACKS = {
    "records-intake": {"triggers": ["party", "vendor", "merge", "duplicate"]},
    "engineering-git": {"triggers": ["git", "branch", "push", "gate"]},
    "joe-comms": {"triggers": ["email", "mail"]},
}

# 1 & 2. extraction grounded in closed vocabulary; multiple spans; unknown pack ignored
single = mod.extract_findings(
    "The only missing domain was records-intake fired on the word merge in a "
    "Git/PR merge status line.", RULE_PACKS)
check("a reason naming one known pack and one of its own words yields exactly that finding",
      single == [("records-intake", "merge")], single)

multi = mod.extract_findings(
    "records-intake fired on Git merge, engineering-git fired on the push status, "
    "and unknown-pack fired on something else entirely.", RULE_PACKS)
check("multiple '<pack> fired on ...' spans each contribute their own finding",
      set(multi) == {("records-intake", "merge"), ("engineering-git", "push")}, multi)

no_vocab_word = mod.extract_findings(
    "records-intake fired on an entirely unrelated situation with no pack words at all.",
    RULE_PACKS)
check("a span containing none of the pack's own words yields no finding for that pack",
      no_vocab_word == [], no_vocab_word)

case_insensitive = mod.extract_findings(
    "records-intake fired on a GIT MERGE status line.", RULE_PACKS)
check("word matching inside a span is case-insensitive",
      case_insensitive == [("records-intake", "merge")], case_insensitive)

# 3. occurrence counting: only "explained" counts, aggregated across files
with tempfile.TemporaryDirectory() as tmp_str:
    tmp_dir = Path(tmp_str)

    def adjudication_file(name: str, events: list[dict]) -> Path:
        path = tmp_dir / name
        path.write_text(json.dumps({
            "schema": "rule-delivery-shadow-adjudication/v1",
            "created_on": "2026-08-27", "events": events,
        }), encoding="utf-8")
        return path

    file_a = adjudication_file("a.json", [
        {"event_id": "e1", "proposed_disposition": "explained",
         "reason": "records-intake fired on Git merge status."},
        {"event_id": "e2", "proposed_disposition": "remediated",
         "reason": "records-intake fired on Git merge status."},
    ])
    file_b = adjudication_file("b.json", [
        {"event_id": "e3", "proposed_disposition": "explained",
         "reason": "records-intake fired on another Git merge line."},
    ])
    occurrences = mod.collect_occurrences([file_a, file_b], RULE_PACKS)
    check("only 'explained' events contribute; 'remediated' is excluded",
          occurrences.get(("records-intake", "merge")) == ["e1", "e3"], occurrences)

    # 4. threshold and already-excluded skip
    triage_none_excluded: dict[str, Any] = {"rules": []}
    changes_at_2 = mod.proposed_changes(occurrences, triage_none_excluded, min_occurrences=2)
    check("a pair reaching the threshold is proposed",
          changes_at_2 == [{"pack": "records-intake", "term": "merge", "occurrences": 2,
                            "event_ids": ["e1", "e3"]}], changes_at_2)
    changes_at_3 = mod.proposed_changes(occurrences, triage_none_excluded, min_occurrences=3)
    check("a pair below the threshold is not proposed",
          changes_at_3 == [], changes_at_3)
    triage_already_excluded = {"fallback_narrowing": {"records-intake": {"exclude_terms": ["merge"]}}}
    changes_excluded = mod.proposed_changes(occurrences, triage_already_excluded, min_occurrences=2)
    check("a pair already excluded is skipped even above threshold",
          changes_excluded == [], changes_excluded)

# 5. apply_changes idempotency and multi-pack independence
base_triage: dict[str, Any] = {"rules": []}
one_change = [{"pack": "records-intake", "term": "merge", "occurrences": 6, "event_ids": []}]
applied_once = mod.apply_changes(copy.deepcopy(base_triage), one_change)
applied_twice = mod.apply_changes(copy.deepcopy(applied_once), one_change)
check("applying the same change twice does not duplicate the excluded term",
      applied_twice["fallback_narrowing"]["records-intake"]["exclude_terms"] == ["merge"],
      applied_twice)
two_pack_changes = [
    {"pack": "records-intake", "term": "merge", "occurrences": 6, "event_ids": []},
    {"pack": "engineering-git", "term": "push", "occurrences": 4, "event_ids": []},
]
applied_two = mod.apply_changes(copy.deepcopy(base_triage), two_pack_changes)
check("two different packs' exclusions do not disturb each other",
      applied_two["fallback_narrowing"]["records-intake"]["exclude_terms"] == ["merge"]
      and applied_two["fallback_narrowing"]["engineering-git"]["exclude_terms"] == ["push"],
      applied_two)
existing_entry_triage = {"fallback_narrowing": {"records-intake": {"exclude_terms": ["party"]}}}
applied_onto_existing = mod.apply_changes(copy.deepcopy(existing_entry_triage), one_change)
check("a new excluded term is added to, not replacing, an existing entry's list",
      applied_onto_existing["fallback_narrowing"]["records-intake"]["exclude_terms"]
      == ["merge", "party"], applied_onto_existing)

# 6. end-to-end, fully isolated from the real repo's config
with tempfile.TemporaryDirectory() as tmp_str2:
    tmp_dir2 = Path(tmp_str2)
    map_path = tmp_dir2 / "rule-enforcement-map.json"
    triage_path = tmp_dir2 / "rule-triage.v1.json"
    output_path = tmp_dir2 / "rule-jit-triggers.v1.json"

    map_data = {
        "rule_packs": {
            "records-intake": {"triggers": ["party", "vendor", "merge", "duplicate"]},
        },
        "rule_load_layers": {
            "aaaaaaaa": {"packs": ["records-intake"]},
            "bbbbbbbb": {"packs": ["records-intake"]},
        },
    }
    triage_data = {
        "rules": [
            {"id": "aaaaaaaa", "home": "jit"},
            {"id": "bbbbbbbb", "home": "jit"},
        ],
    }
    map_path.write_text(json.dumps(map_data), encoding="utf-8")
    triage_path.write_text(json.dumps(triage_data, indent=1), encoding="utf-8")

    adjudication_path = tmp_dir2 / "adjudication.json"
    adjudication_path.write_text(json.dumps({
        "schema": "rule-delivery-shadow-adjudication/v1", "created_on": "2026-08-27",
        "events": [
            {"event_id": "e1", "proposed_disposition": "explained",
             "reason": "records-intake fired on Git merge status."},
            {"event_id": "e2", "proposed_disposition": "explained",
             "reason": "records-intake fired on another Git merge line."},
        ],
    }), encoding="utf-8")

    mod.MAP_PATH, saved_map = map_path, mod.MAP_PATH
    mod.TRIAGE_PATH, saved_triage = triage_path, mod.TRIAGE_PATH
    mod.compiler.MAP_PATH, saved_c_map = map_path, mod.compiler.MAP_PATH
    mod.compiler.TRIAGE_PATH, saved_c_triage = triage_path, mod.compiler.TRIAGE_PATH
    mod.compiler.OUTPUT_PATH, saved_c_output = output_path, mod.compiler.OUTPUT_PATH
    try:
        before_bytes = triage_path.read_bytes()
        rc = mod.main([str(adjudication_path)])
        check("dry run (default) exits 0", rc == 0)
        check("dry run writes nothing to the triage file",
              triage_path.read_bytes() == before_bytes)
        check("dry run never creates the trigger table output",
              not output_path.exists())

        rc = mod.main([str(adjudication_path), "--apply"])
        check("--apply exits 0", rc == 0)
        written_triage = json.loads(triage_path.read_text(encoding="utf-8"))
        check("--apply writes the narrowing into the isolated triage file",
              written_triage.get("fallback_narrowing", {}).get(
                  "records-intake", {}).get("exclude_terms") == ["merge"],
              written_triage)
        check("--apply recompiles the trigger table in the same run",
              output_path.exists())
        compiled = json.loads(output_path.read_text(encoding="utf-8"))
        check("the recompiled table's records-intake fallback no longer contains 'merge'",
              not any("merge" in row["pattern"] for row in compiled["triggers"]
                      if "records-intake" in row.get("packs", [])),
              compiled["triggers"])

        # idempotent re-run: no duplicate term, still exactly one entry
        rc = mod.main([str(adjudication_path), "--apply"])
        again = json.loads(triage_path.read_text(encoding="utf-8"))
        check("re-running --apply is idempotent (no duplicated exclude term)",
              again["fallback_narrowing"]["records-intake"]["exclude_terms"] == ["merge"],
              again)
    finally:
        mod.MAP_PATH = saved_map
        mod.TRIAGE_PATH = saved_triage
        mod.compiler.MAP_PATH = saved_c_map
        mod.compiler.TRIAGE_PATH = saved_c_triage
        mod.compiler.OUTPUT_PATH = saved_c_output

# 7. smoke test against the real, committed repo state — dry run only, writes nothing
real_triage_before = mod.TRIAGE_PATH.read_bytes()
real_output_before = (mod.compiler.OUTPUT_PATH.read_bytes()
                      if mod.compiler.OUTPUT_PATH.exists() else None)
rc = mod.main([])
check("dry run against the real committed adjudication files exits 0", rc == 0)
check("dry run against the real repo never writes the real triage file",
      mod.TRIAGE_PATH.read_bytes() == real_triage_before)
check("dry run against the real repo never writes the real trigger table",
      (mod.compiler.OUTPUT_PATH.read_bytes() if mod.compiler.OUTPUT_PATH.exists() else None)
      == real_output_before)

if FAILURES:
    print("rule-delivery-adjudication-apply-selftest: FAIL")
    for failure in FAILURES:
        print("  " + failure)
    raise SystemExit(1)
print("rule-delivery-adjudication-apply-selftest: all cases passed")
