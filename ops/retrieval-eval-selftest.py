#!/usr/bin/env python3
"""Self-test the retrieval benchmark with deterministic synthetic metadata."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile


REPO = pathlib.Path(__file__).resolve().parent.parent
RUNNER = REPO / "ops" / "retrieval_eval.py"
GOLDEN = REPO / "evals" / "retrieval" / "golden-queries.v1.json"
BASELINE = REPO / "evals" / "retrieval" / "baselines" / "lexical.v1.json"
BASELINE_INDEX = REPO / "evals" / "retrieval" / "fixtures" / "section-index.baseline.v1.tsv"


def check(label: str, condition: bool) -> None:
    print(f"  {'ok' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    index = root / "section-index.tsv"
    index.write_text(
        "# synthetic\n"
        "doctrine:current-source\t1\t1\t1\tProtected trunk integration\tCARR Software\tExact current source\tstore\n"
        "Archive/dead.md\t1\t3\t1\tProtected trunk integration\tOld\tSuperseded copy\tfile\n"
        "doctrine:other\t1\t1\t1\tUnrelated retrieval\tCARR Software\tNothing relevant\tstore\n",
        encoding="utf-8",
    )
    suite = root / "suite.json"
    suite.write_text(json.dumps({
        "schema_version": "carr-retrieval-golden-v1",
        "suite_id": "synthetic-retrieval-selftest",
        "data_class": "D1_synthetic",
        "scope_ref": "carr-internal",
        "cases": [{
            "id": "RET-SELF-001",
            "query": "protected trunk integration",
            "engines": {
                "section_index_lexical": {
                    "required_targets": ["doctrine:current-source"],
                    "top_k": 1,
                },
                "doctrine_postgres_fts": {
                    "required_targets": ["current-source#protected-trunk"],
                    "top_k": 1,
                },
            },
            "forbidden_target_patterns": ["Archive/", "superseded"],
        }],
    }), encoding="utf-8")
    observed = root / "doctrine-results.json"
    observed.write_text(json.dumps({
        "schema_version": "carr-doctrine-fts-observation-v1",
        "suite_id": "synthetic-retrieval-selftest",
        "scope_ref": "carr-internal",
        "scope_applied_before_rank": True,
        "status": "measured",
        "cases": {"RET-SELF-001": [{
            "target": "current-source#protected-trunk",
            "rank": 1,
            "current": True,
            "provenance_complete": True,
        }]},
    }), encoding="utf-8")
    report = root / "report.json"
    proc = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(suite), "--section-index", str(index),
        "--doctrine-results", str(observed), "--output", str(report),
    ], text=True, capture_output=True)
    check("a current, sourced result passes both lexical engines", proc.returncode == 0)
    payload = json.loads(report.read_text(encoding="utf-8"))
    check("report binds the suite digest", len(payload["suite_digest"]) == 64)
    check("report keeps engine results separate", set(payload["engines"]) == {"section_index_lexical", "doctrine_postgres_fts"})
    check("all measured current-source hits pass", payload["summary"]["passed_cases"] == 2)
    check("scope-before-rank evidence is explicit", payload["engines"]["doctrine_postgres_fts"]["scope_applied_before_rank"] is True)

    synthetic_baseline = root / "baseline.json"
    synthetic_baseline.write_text(json.dumps({
        "schema_version": "carr-retrieval-baseline-v1",
        "suite_digest": payload["suite_digest"],
        "engine_summary": {
            engine: {
                "passed": len(value["cases"]), "failed": 0, "unknown": 0,
                "mean_reciprocal_rank": 1.0,
                "case_status": {case["case_id"]: "pass" for case in value["cases"]},
            } for engine, value in payload["engines"].items()
        },
    }), encoding="utf-8")
    gated = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(suite), "--section-index", str(index),
        "--doctrine-results", str(observed), "--baseline", str(synthetic_baseline),
        "--output", str(report),
    ], text=True, capture_output=True)
    check("an unchanged measured baseline passes the regression gate", gated.returncode == 0)

    bad = json.loads(observed.read_text(encoding="utf-8"))
    bad["cases"]["RET-SELF-001"][0]["current"] = False
    observed.write_text(json.dumps(bad), encoding="utf-8")
    bad_proc = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(suite), "--section-index", str(index),
        "--doctrine-results", str(observed), "--output", str(report),
    ], text=True, capture_output=True)
    check("a stale expected result fails the scorecard", bad_proc.returncode == 1)

    unknown = dict(bad)
    unknown["status"] = "unavailable"
    unknown["cases"] = {}
    observed.write_text(json.dumps(unknown), encoding="utf-8")
    unknown_proc = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(suite), "--section-index", str(index),
        "--doctrine-results", str(observed), "--output", str(report),
    ], text=True, capture_output=True)
    unknown_payload = json.loads(report.read_text(encoding="utf-8"))
    check("an unavailable store is unknown, never a passing zero", unknown_proc.returncode == 1 and unknown_payload["summary"]["unknown_cases"] == 1)

golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
import hashlib
legacy = dict(golden)
legacy["cases"] = golden["cases"][:11]
canonical = json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
check("the original eleven cases remain byte-for-byte bound to their baseline",
      baseline["suite_digest"] == hashlib.sha256(canonical).hexdigest())
check("the committed baseline records every engine-case outcome",
      baseline["summary"]["total_engine_cases"] == sum(len(case["engines"]) for case in golden["cases"][:11]))
check("the baseline names measured misses instead of interpreting them as success",
      baseline["summary"]["failed"] == len(baseline["measured_misses"]) and baseline["summary"]["failed"] > 0)

with tempfile.TemporaryDirectory() as tmp:
    committed_report = pathlib.Path(tmp) / "candidate-report.json"
    committed = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(GOLDEN),
        "--section-index", str(BASELINE_INDEX), "--doctrine-results",
        str(REPO / "evals/retrieval/baselines/doctrine-fts.2026-08-16.title-v1.json"),
        "--output", str(committed_report),
    ], text=True, capture_output=True)
    committed_payload = json.loads(committed_report.read_text(encoding="utf-8"))
    title_case = next(row for row in committed_payload["engines"]["doctrine_postgres_fts"]["cases"]
                      if row["case_id"] == "RET-TITLE-001")
    check("the dated D2 candidate title baseline validates the title case",
          committed.returncode == 1 and title_case["status"] == "pass")

print("PASS: retrieval evaluation self-test")
