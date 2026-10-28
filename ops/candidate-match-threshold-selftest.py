#!/usr/bin/env python3
"""Exercise the two-field identity threshold used by candidate-pool ingest."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "pipelines" / "import_candidate_pool.py"
spec = importlib.util.spec_from_file_location("candidate_pool_import", SOURCE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

bad: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(
    ok="  ok   {label}",
    fail="  FAIL {label}",
    detail=" {detail}",
    detail_on_ok=True,
    failures=bad,
)


known = [{
    "type": "lead", "ref": "L-101", "email": "jane@smithdental.example",
    "first": "jane", "last": "smith", "ptoks": {"smith", "dental", "practice"},
    "dnc": False,
}]

print("candidate identity threshold")
g, basis, tier = mod.match_known("Jane Smith", "", "", known, {})
check("name alone is visible review, never suppression",
      g is known[0] and tier == "review" and basis.startswith("REVIEW"), repr((basis, tier)))
g, basis, tier = mod.match_known("Jane Smith", "Smith Dental Practice", "", known, {})
check("corroborated practice remains visible for human confirmation",
      g is known[0] and tier == "review", repr((basis, tier)))
g, basis, tier = mod.match_known("Unrelated", "", "jane@smithdental.example", known,
                                  {"jane@smithdental.example": known[0]})
check("exact email can suppress", g is known[0] and tier == "suppressed", repr((basis, tier)))
g, basis, tier = mod.match_known("Jane Smith", "", "", known, {}, strict=True)
check("strict-suppression flag cannot waive name-only ban",
      g is known[0] and tier == "review", repr((basis, tier)))

print("OK all checks passed" if not bad else "FAIL " + ", ".join(bad))
raise SystemExit(bool(bad))
