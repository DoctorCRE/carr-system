#!/usr/bin/env python3
"""ops/vendor-level-drift-selftest.py — acceptance test for the vendor-level check.

WHAT IT PROTECTS. Joe defined the relationship levels by countable events
(rule faf1b643) precisely so they stop being impressions: "you can email fifty
people and have fifty relationships that do not exist." The check exists to show
where the recorded level and the counted evidence disagree. The way that check
fails is not by crashing — it is by reporting so much that nobody reads it.

THE NOISE PROBLEM, measured on real data 2026-08-15: the crude comparison
`recorded is distinct from suggested` returns 239 of 301 active vendors. The
view's own `disagrees` column returns 16. The difference is 243 vendors with no
countable events at all, most of them Prospective vendors correctly recorded as
Prospective. A check that reports 239 findings on its first run is a check
somebody mutes on its first run, so the separation is the thing under test here.

IT MUST ALSO NEVER CHANGE A LEVEL. The rule says the level stays a human
judgment and is stored, not computed — a relationship can matter for reasons no
event count can see. This suite pins that the check is read-only in shape: it
classifies and ranks, and the arithmetic that decides what the evidence supports
lives in the view, not re-derived here where the two could drift apart.

Run with: ./.venv/bin/python ops/vendor-level-drift-selftest.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(REPO, "ops", "vendor-level-drift-check.py")

spec = importlib.util.spec_from_file_location("vendor_level_drift_check", CHECK)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules["vendor_level_drift_check"] = mod
spec.loader.exec_module(mod)

FAILED: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail="\n        {detail}", failures=FAILED)


def row(ref, recorded, suggested, signal, two_way=0, attempts=0, they=0, we=0, name=None):
    return {"vendor_ref": ref, "name": name or ref, "recorded": recorded,
            "suggested": suggested, "signal": signal,
            "disagrees": (recorded is not None and suggested is not None
                          and recorded != suggested),
            "evidence_events": two_way + attempts + they + we,
            "two_way": two_way, "attempts_only": attempts,
            "they_gave": they, "we_gave": we}


print("vendor-level-drift-selftest — the 16 that matter, not the 239 that do not")

# ── the noise separation, which is the whole point ─────────────────────────
NOISY = (
    [row(f"V-P-{i}", 0, None, "no_evidence") for i in range(200)]          # prospective, nothing yet
    + [row(f"V-U-{i}", None, 1, "unjudged_with_evidence", two_way=1) for i in range(31)]
    + [row("V-REAL-1", 3, 1, "recorded_exceeds_evidence", two_way=2)]
    + [row("V-REAL-2", 0, 1, "evidence_exceeds_recorded", two_way=1)]
)
res = mod.classify(NOISY)
check("a vendor with no countable events is NOT a finding",
      all(f["vendor_ref"].startswith("V-REAL") for f in res["findings"]),
      str([f["vendor_ref"] for f in res["findings"]])[:200])
check("...and only the genuinely disagreeing rows are findings",
      len(res["findings"]) == 2, str(len(res["findings"])))
check("a vendor with evidence but NO recorded level is reported separately",
      len(res["unjudged_with_evidence"]) == 31, str(len(res["unjudged_with_evidence"])))
check("...and is not counted as a disagreement, because nobody has judged it yet",
      all(not f["vendor_ref"].startswith("V-U-") for f in res["findings"]))
check("the quiet majority is still COUNTED, so the report can say what it skipped",
      res["counts"].get("no_evidence") == 200, str(res["counts"]))

# ── ranking: the biggest claim on the thinnest evidence goes first ─────────
MIXED = [
    row("V-LOW", 0, 1, "evidence_exceeds_recorded", two_way=1),
    row("V-TWO", 2, 1, "recorded_exceeds_evidence", two_way=3),
    row("V-CORE", 3, 1, "recorded_exceeds_evidence", two_way=1),
]
order = [f["vendor_ref"] for f in mod.classify(MIXED)["findings"]]
check("a Core vendor with the thinnest evidence is listed first",
      order[0] == "V-CORE", str(order))
check("...then the overstated Established one",
      order[1] == "V-TWO", str(order))
check("...and the under-recorded vendor comes after the overstated ones",
      order[-1] == "V-LOW", str(order))

# ── the remedy has to be actionable, and the two directions differ ─────────
overstated_no_value = mod.remedy(row("V-X", 2, 1, "recorded_exceeds_evidence", two_way=4))
check("an overstated level with no recorded value movement points at the MISSING EDGES first",
      "link-parties" in overstated_no_value and "never written down" in overstated_no_value,
      overstated_no_value[:200])
check("...and does not simply assert the level is wrong",
      "or the level is generous" in overstated_no_value, overstated_no_value[:200])
under = mod.remedy(row("V-Y", 0, 1, "evidence_exceeds_recorded", two_way=2))
check("an under-recorded level reads as a promotion, with the contact count in it",
      "promotion" in under and "2 event(s)" in under, under[:200])

# ── it reports; it never decides ───────────────────────────────────────────
src = open(CHECK, encoding="utf-8").read()
for forbidden in ["update vendor", "update  vendor", "set relationship_level", "insert into"]:
    check(f"the check contains no write: {forbidden!r}", forbidden not in src.lower())
check("the level arithmetic is NOT re-derived here — it lives in the view",
      "they_gave > 1" not in src,
      "re-deriving the thresholds in two places is how they drift apart")
check("it says out loud that the level stays a human judgment",
      "human judgment" in src.lower())

# ── the empty case must read as a clean pass, not a silent one ─────────────
empty = mod.render(mod.classify([row("V-Q", 1, 1, "agrees", two_way=1)]))
check("a clean run says how many vendors it checked",
      any("1 active vendor" in l or "1 active" in l for l in empty), str(empty)[:220])

# ── a missing credential SKIPS rather than failing ─────────────────────────
import subprocess
env = {k: v for k, v in os.environ.items()
       if k not in ("CARR_DB_EXPORTER_URL", "DATABASE_URL")}
env["HOME"] = "/nonexistent-home-for-this-test"
p = subprocess.run([sys.executable, CHECK], capture_output=True, text=True, timeout=120, env=env)
check("no database credential exits 78 (SKIP), never a hard failure",
      p.returncode == 78, f"rc={p.returncode} {p.stdout[:150]}{p.stderr[:150]}")

print()
if FAILED:
    print(f"FAILED {len(FAILED)} check(s):")
    for label in FAILED:
        print(f"  - {label}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
