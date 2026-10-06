#!/usr/bin/env python3
"""rule-triage-selftest.py — acceptance test for WR-000019 slice S7's triage
artifact, ops/config/rule-triage.v1.json, plus a smoke test of the two tools
that read it (rule-triage-report.py, rule-triage-apply.py).

WHAT THIS PINS, per the slice's own definition of done:
  1. Complete coverage — every id in ops/config/rule-enforcement-map.json's
     rule_controls (the full active-rule set) appears exactly once in the
     triage, and the triage names no id the enforcement map does not know.
  2. No rule sits in two homes (trivially true from dict construction, but
     pinned here so a future hand-edit of the JSON cannot reintroduce it).
  3. core count <= 35 (the slice's hard cap) and a warning line (not a
     failure) if it drifts from the 15-20 target band.
  4. Every GATE rule names a carrying_control, and that control is a real key
     in the enforcement map's control_catalog — never an invented name.
  5. Every GONE rule names a merge_target that (a) exists among the active ids
     and (b) is not itself GONE — a merge chain into a retired rule would be
     a dangling reference the moment Joe's batch actually retires anything.
  6. Every JIT rule carries a jit_trigger_hint (non-empty).
  7. rule-triage-report.py runs clean on the real artifact (smoke test).
  8. rule-triage-apply.py runs clean, is dry-run only (asserts no subprocess/
     network usage got introduced, and --json's "executed" field is false),
     and its retire-rule batch size matches the triage's GONE count exactly.

RUNNING IT. No database, no network:

    python3 ops/rule-triage-selftest.py
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRIAGE_PATH = REPO / "ops" / "config" / "rule-triage.v1.json"
ENFORCEMENT_MAP_PATH = REPO / "ops" / "config" / "rule-enforcement-map.json"
REPORT_SCRIPT = REPO / "ops" / "rule-triage-report.py"
APPLY_SCRIPT = REPO / "ops" / "rule-triage-apply.py"

CORE_CAP = 35
CORE_TARGET_LOW, CORE_TARGET_HIGH = 15, 20

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def warn(name, cond, detail=""):
    # informational only -- never fails the suite
    print(("  ok    " if cond else "  note  ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("\nWR-000019 slice S7 — rule-triage.v1.json acceptance test")

    if not TRIAGE_PATH.exists():
        print(f"  FAIL  triage artifact missing at {TRIAGE_PATH}")
        return 1
    if not ENFORCEMENT_MAP_PATH.exists():
        print(f"  FAIL  enforcement map missing at {ENFORCEMENT_MAP_PATH}")
        return 1

    triage = json.loads(TRIAGE_PATH.read_text())
    emap = json.loads(ENFORCEMENT_MAP_PATH.read_text())

    rules = triage.get("rules", [])
    ids = [r["id"] for r in rules]
    id_set = set(ids)
    expected_ids = set(emap.get("rule_controls", {}).keys())

    # 1. coverage. The live corpus changes as rules are admitted and retired;
    # compare both reviewed representations without freezing a stale count.
    active_ids = {rid for ids in emap.get("active_rule_ids", {}).values() for rid in ids}
    check("control map covers every declared active rule",
          expected_ids == active_ids,
          f"controls-only: {sorted(expected_ids-active_ids)[:10]}, "
          f"active-only: {sorted(active_ids-expected_ids)[:10]}")
    check("triage carries exactly one row per rule (no duplicates)",
          len(ids) == len(id_set), f"{len(ids)} rows, {len(id_set)} unique ids")
    missing = expected_ids - id_set
    extra = id_set - expected_ids
    check("triage covers every id the enforcement map knows",
          not missing, f"missing: {sorted(missing)[:10]}")
    check("triage names no id the enforcement map does not know",
          not extra, f"extra: {sorted(extra)[:10]}")

    # 2. no rule in two homes
    homes_by_id = {}
    dupes = []
    for r in rules:
        rid = r["id"]
        if rid in homes_by_id:
            dupes.append(rid)
        homes_by_id[rid] = r["home"]
    check("no rule id appears more than once (so never in two homes)",
          not dupes, f"duplicated ids: {dupes}")
    valid_homes = {"gate", "jit", "core", "gone"}
    bad_homes = [r["id"] for r in rules if r["home"] not in valid_homes]
    check("every row's home is one of gate/jit/core/gone",
          not bad_homes, f"bad rows: {bad_homes}")

    # 3. core cap
    core_rows = [r for r in rules if r["home"] == "core"]
    check(f"core count <= {CORE_CAP} (hard cap)",
          len(core_rows) <= CORE_CAP, f"core count is {len(core_rows)}")
    warn(f"core count within {CORE_TARGET_LOW}-{CORE_TARGET_HIGH} target band",
         CORE_TARGET_LOW <= len(core_rows) <= CORE_TARGET_HIGH,
         f"core count is {len(core_rows)}")

    # 4. gate rules name a real carrying_control
    control_catalog = set(emap.get("control_catalog", {}).keys())
    gate_rows = [r for r in rules if r["home"] == "gate"]
    missing_control = [r["id"] for r in gate_rows if not r.get("carrying_control")]
    check("every GATE rule names a carrying_control",
          not missing_control, f"missing on: {missing_control[:10]}")
    unknown_control = [r["id"] for r in gate_rows
                        if r.get("carrying_control") and r["carrying_control"] not in control_catalog]
    check("every GATE rule's carrying_control is a real control_catalog key",
          not unknown_control, f"unknown controls on: {unknown_control[:10]}")

    # 5. gone rules name a real, non-gone merge_target
    gone_rows = [r for r in rules if r["home"] == "gone"]
    missing_target = [r["id"] for r in gone_rows if not r.get("merge_target")]
    check("every GONE rule names a merge_target",
          not missing_target, f"missing on: {missing_target}")
    bad_target = [r["id"] for r in gone_rows
                  if r.get("merge_target") and r["merge_target"] not in id_set]
    check("every GONE rule's merge_target exists among the active ids",
          not bad_target, f"dangling targets on: {bad_target}")
    chained_into_gone = [r["id"] for r in gone_rows
                         if r.get("merge_target") in homes_by_id
                         and homes_by_id[r["merge_target"]] == "gone"]
    check("no GONE rule's merge_target is itself GONE (no merge chains)",
          not chained_into_gone, f"chained rows: {chained_into_gone}")

    # 6. jit rules carry a trigger hint
    jit_rows = [r for r in rules if r["home"] == "jit"]
    missing_hint = [r["id"] for r in jit_rows if not r.get("jit_trigger_hint")]
    check("every JIT rule carries a jit_trigger_hint",
          not missing_hint, f"missing on: {missing_hint[:10]}")

    # counts object matches actual rows
    counts = triage.get("counts", {})
    actual_counts = {h: len([r for r in rules if r["home"] == h]) for h in valid_homes}
    check("declared counts object matches the actual row counts",
          all(counts.get(h, 0) == actual_counts[h] for h in valid_homes),
          f"declared={counts} actual={actual_counts}")

    # 7. report script smoke test
    if REPORT_SCRIPT.exists():
        p = subprocess.run([sys.executable, str(REPORT_SCRIPT)],
                            capture_output=True, text=True, cwd=str(REPO))
        check("rule-triage-report.py exits 0 on the real artifact",
              p.returncode == 0, f"rc={p.returncode} stderr={p.stderr[:200]}")
        check("rule-triage-report.py output mentions every home section",
              all(h.upper() in p.stdout for h in ["core", "gate", "jit", "gone"]),
              "one or more HOME headers missing from output")
    else:
        check("rule-triage-report.py exists", False, str(REPORT_SCRIPT))

    # 8. apply script smoke test + dry-run guarantee
    if APPLY_SCRIPT.exists():
        src = APPLY_SCRIPT.read_text()
        check("rule-triage-apply.py never imports subprocess",
              "import subprocess" not in src, "found a subprocess import")
        check("rule-triage-apply.py never imports requests/urllib/http client libs",
              not any(tok in src for tok in ("import requests", "import urllib", "import http.client",
                                              "socket.")),
              "found a network-capable import")
        p = subprocess.run([sys.executable, str(APPLY_SCRIPT), "--emit-receipts-plan", "--json"],
                            capture_output=True, text=True, cwd=str(REPO))
        check("rule-triage-apply.py exits 0", p.returncode == 0,
              f"rc={p.returncode} stderr={p.stderr[:200]}")
        try:
            plan = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            plan = None
            check("rule-triage-apply.py --json prints valid JSON", False, str(e))
        if plan is not None:
            check("apply plan declares executed=false", plan.get("executed") is False,
                  f"executed={plan.get('executed')}")
            check("apply plan's retire-rule batch size matches triage GONE count",
                  len(plan.get("retire_rule_batch", [])) == len(gone_rows),
                  f"plan has {len(plan.get('retire_rule_batch', []))}, triage has {len(gone_rows)}")
            check("apply plan includes the receipts plan when asked",
                  "receipts_plan" in plan, "receipts_plan missing")
    else:
        check("rule-triage-apply.py exists", False, str(APPLY_SCRIPT))

    print(f"\n{passed} checks passed, {len(failures)} failed")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
