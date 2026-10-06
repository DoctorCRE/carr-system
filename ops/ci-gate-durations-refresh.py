#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
"""ci-gate-durations-refresh.py -- regenerate the gates shard durations file.

THE LOOP, with no human editing a list anywhere in it:

  1. Every hosted gates job (ci.yml and main-canary.yml) writes its per-test wall
     seconds with `ops/ci.sh` and uploads them as an artifact named
     ci-gate-timings-<job index>. One document per shard; together they cover the
     whole gates class.
  2. This script takes the artifacts of the last few successful main-canary runs,
     takes the median per test across them, drops tests that no longer exist in
     the tree, and rewrites ops/config/ci-gate-durations.v1.json.
  3. ops/ci-gate-shards.py reads that file to balance the next runs.
  4. .github/workflows/ci-durations-refresh.yml runs step 2 weekly (or by hand) and
     opens a pull request when the file changes. The file only moves through that
     PR, so the split a PR was tested with is the split the file in its tree gives.

HOW TO RUN IT BY HAND (needs `gh` logged in; pauses 3s between gh calls):

    ops/ci-gate-durations-refresh.py                  # last 5 green canary runs
    ops/ci-gate-durations-refresh.py --runs 8
    ops/ci-gate-durations-refresh.py --run-id 123 --run-id 456   # named runs (a PR's CI run works too)
    ops/ci-gate-durations-refresh.py --from-dir ./timings         # offline: any dir of timing JSON

A refresh with no usable timing input exits non-zero and leaves the committed file
alone. A stale file is safe (see ops/ci-gate-shards.py); a wiped one would not be.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GH_PAUSE_SECONDS = 3


def _shards():
    spec = importlib.util.spec_from_file_location("ci_gate_shards", REPO / "ops/ci-gate-shards.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("ci_gate_shards", module)
    spec.loader.exec_module(module)
    return module


SH = _shards()
TIMINGS_SCHEMA = SH.TIMINGS_SCHEMA
SCHEMA = SH.SCHEMA


def merge(paths) -> tuple[dict, list]:
    """(median seconds per test, notes). Unreadable or malformed inputs are named
    in the notes and skipped; they never abort the merge."""
    samples: dict[str, list[float]] = {}
    notes: list[str] = []
    for path in paths:
        path = Path(path)
        try:
            doc = json.loads(path.read_text())
            tests = doc["tests"]
            if doc.get("schema") != TIMINGS_SCHEMA or not isinstance(tests, dict):
                raise ValueError("not a timings document")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            notes.append(f"skipped {path.name}: {type(exc).__name__}: {exc}")
            continue
        for test, value in tests.items():
            if isinstance(test, str) and SH.valid_seconds(value):
                samples.setdefault(test, []).append(float(value))
    return {t: round(statistics.median(v), 3) for t, v in sorted(samples.items())}, notes


def write(path, durations: dict) -> None:
    """Sorted keys, fixed formatting: the same data is always the same bytes, so a
    refresh that learned nothing new produces no diff and opens no PR."""
    doc = {"schema": SCHEMA, "unit": "seconds", "tests": dict(sorted(durations.items()))}
    Path(path).write_text(json.dumps(doc, indent=1) + "\n")


def _gh(*args) -> str:
    time.sleep(GH_PAUSE_SECONDS)  # one gh call at a time, never in parallel
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=300).stdout


def download(run_ids, dest: Path) -> list[str]:
    notes = []
    for run_id in run_ids:
        try:
            _gh("run", "download", str(run_id), "--pattern", "ci-gate-timings-*",
                "--dir", str(dest / str(run_id)))
        except (subprocess.SubprocessError, OSError) as exc:
            notes.append(f"run {run_id}: no timings downloaded ({type(exc).__name__})")
    return notes


def recent_canary_runs(limit: int) -> list[int]:
    out = _gh("run", "list", "--workflow", "main-canary.yml", "--branch", "main",
              "--status", "success", "--limit", str(limit), "--json", "databaseId")
    return [row["databaseId"] for row in json.loads(out)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", default=str(SH.DURATIONS_FILE))
    parser.add_argument("--from-dir", help="merge every *.json under this directory instead of using gh")
    parser.add_argument("--run-id", action="append", type=int, default=[],
                        help="a workflow run to take timings from (repeatable)")
    parser.add_argument("--runs", type=int, default=5, help="how many recent green canary runs")
    parser.add_argument("--keep-missing", action="store_true",
                        help="keep tests whose file is no longer in the tree")
    args = parser.parse_args()

    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ci-gate-timings-") as scratch:
        if args.from_dir:
            source = Path(args.from_dir)
        else:
            source = Path(scratch)
            try:
                run_ids = args.run_id or recent_canary_runs(args.runs)
            except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
                print(f"refresh: could not list canary runs ({type(exc).__name__}); file left untouched",
                      file=sys.stderr)
                return 1
            notes += download(run_ids, source)
        files = sorted(source.rglob("*.json")) if source.is_dir() else []
        merged, merge_notes = merge(files)
    notes += merge_notes
    if not args.keep_missing:
        merged = {t: v for t, v in merged.items()
                  if t == SH.STATIC_KEY or (REPO / t).exists()}
    for note in notes:
        print(f"refresh: {note}", file=sys.stderr)
    if not merged:
        print("refresh: no usable timings; the committed file was not changed", file=sys.stderr)
        return 1
    write(args.output, merged)
    print(f"refresh: wrote {len(merged)} test durations from {len(files)} timing file(s) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
