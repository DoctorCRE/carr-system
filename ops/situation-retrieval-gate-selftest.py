#!/usr/bin/env python3
"""Seeded failures for the WR-AI-006 release-blocking retrieval gate."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile


REPO = pathlib.Path(__file__).resolve().parent.parent
GATE = REPO / "ops" / "situation_retrieval_gate.py"


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(ok="  ok  {label}", detail=None, raises="{label}")


def run_gate(root: pathlib.Path, observation: dict) -> subprocess.CompletedProcess[str]:
    suite = {
        "schema_version": "carr-situation-retrieval-suite-v1",
        "suite_id": "synthetic-situation-retrieval",
        "scope_ref": "carr-internal",
        "status": "measured",
        "cases": [
            {"id": "POS", "required_targets": ["runbook#diagnosis"], "top_k": 3},
            {"id": "NEG", "expect_no_targets": ["runbook#diagnosis"], "top_k": 3},
            {"id": "AMB", "required_targets": ["runbook#diagnosis", "review#preamble"],
             "require_all_targets": True, "top_k": 3},
        ],
    }
    suite_path = root / "suite.json"
    observation_path = root / "observation.json"
    report_path = root / "report.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    return subprocess.run([
        sys.executable, str(GATE), "--suite", str(suite_path),
        "--observation", str(observation_path), "--output", str(report_path),
    ], text=True, capture_output=True)


def good_observation() -> dict:
    hit = lambda target, rank: {
        "target": target, "rank": rank, "current": True,
        "provenance": {
            "complete": True, "policy_id": "lexical-dominant-v1",
            "lexical_score": 0, "concept_score": 1, "final_score": 0.25,
            "phrase_ids": ["p1"], "concept_ids": ["c1"], "mapping_ids": ["m1"],
        },
    }
    return {
        "schema_version": "carr-situation-retrieval-observation-v1",
        "suite_id": "synthetic-situation-retrieval",
        "scope_ref": "carr-internal",
        "status": "measured",
        "scope_applied_before_rank": True,
        "deterministic_replay_mismatches": 0,
        "policy_id": "lexical-dominant-v1",
        "cases": {
            "POS": [hit("runbook#diagnosis", 1)],
            "NEG": [hit("other#section", 1)],
            "AMB": [hit("runbook#diagnosis", 1), hit("review#preamble", 2)],
        },
    }


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    good = good_observation()
    check("complete current top-3 results pass", run_gate(root, good).returncode == 0)

    stale = json.loads(json.dumps(good))
    stale["cases"]["POS"][0]["current"] = False
    check("stale leakage at any rank blocks", run_gate(root, stale).returncode == 1)

    late_scope = json.loads(json.dumps(good))
    late_scope["scope_applied_before_rank"] = False
    check("scope applied after ranking blocks", run_gate(root, late_scope).returncode == 1)

    incomplete = json.loads(json.dumps(good))
    incomplete["cases"]["POS"][0]["provenance"]["mapping_ids"] = []
    check("incomplete reconstructible provenance blocks", run_gate(root, incomplete).returncode == 1)

    nondeterministic = json.loads(json.dumps(good))
    nondeterministic["deterministic_replay_mismatches"] = 1
    check("a deterministic replay mismatch blocks", run_gate(root, nondeterministic).returncode == 1)

    shadow = json.loads(json.dumps(good))
    shadow["cases"]["NEG"] = [good["cases"]["POS"][0]]
    check("a near-miss negative returning the governed target blocks", run_gate(root, shadow).returncode == 1)

with tempfile.TemporaryDirectory() as tmp:
    output = pathlib.Path(tmp) / "selection.json"
    committed = subprocess.run([
        sys.executable, str(GATE),
        "--suite", str(REPO / "evals/retrieval/situation-golden-queries.2026-08-16.v1.json"),
        "--observations",
        str(REPO / "evals/retrieval/baselines/situation-retrieval.lexical-dominant.2026-08-16.v1.json"),
        str(REPO / "evals/retrieval/baselines/situation-retrieval.coequal-normalized.2026-08-16.v1.json"),
        "--output", str(output),
    ], text=True, capture_output=True)
    selection = json.loads(output.read_text(encoding="utf-8"))
    check("both committed policy observations select one reproducible candidate default",
          committed.returncode == 0 and selection["status"] == "candidate"
          and selection["default_policy_id"] == "coequal-normalized-v1")

print("PASS: situation retrieval gate self-test")
