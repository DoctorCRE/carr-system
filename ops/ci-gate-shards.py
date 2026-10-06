#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
"""ci-gate-shards.py -- split the gates class across hosted jobs by measured cost.

ops/ci.sh calls this for `--only gates --shard i/N`. It is the ONLY place the
split is decided, so the hosted job and a local `ops/ci.sh --shard` run cannot
disagree (rule a8c55a47).

THE SPLIT IS GREEDY LONGEST-PROCESSING-TIME. Tests are sorted by recorded
duration, heaviest first, and each goes to the shard with the least load so far.
That bounds the spread: the busiest shard exceeds the idlest by at most the
longest single test. Nothing is hand-maintained; the durations come from
ops/config/ci-gate-durations.v1.json, which ops/ci-gate-durations-refresh.py
regenerates from timings that CI itself uploads (see that script's header).

FAIL-SAFE, which is the point of the design. A missing, unreadable or malformed
durations file, or a test with no recorded duration, can only make the split
worse BALANCED. It can never make it SMALLER: every test is assigned to exactly
one shard from the test list ci.sh enumerated, whatever the file says. The
same durations file and the same list always give the same split, whatever
order the list arrives in, so a re-run reproduces the shard it re-runs.

    ops/ci-gate-shards.py select --shard 2/3 [--durations FILE] < paths > mine
    ops/ci-gate-shards.py record --logdir DIR --out FILE [--static-seconds S] < paths
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DURATIONS_FILE = REPO / "ops/config/ci-gate-durations.v1.json"
SCHEMA = "carr-ci-gate-durations/v1"
TIMINGS_SCHEMA = "carr-ci-gate-timings/v1"
# What a test with no recorded duration weighs. Measured: the ~500 ordinary
# suites average under a second of interpreter start-up, the handful of heavy
# ones run tens of seconds. A new suite is most likely ordinary; 2s leans a
# little heavy so a pile of new tests spreads rather than clumps.
DEFAULT_WEIGHT_SECONDS = 2.0
# The repository-wide static checks that close the gates class (gate-integrity,
# the inventory checks) are one more unit of work. It is assigned like a test so
# no shard is silently the one that also carries them.
STATIC_KEY = "ci:gates-static-checks"


def parse_shard(text: str) -> tuple[int, int]:
    """'2/3' -> (2, 3). Anything else, including 0/N and i>N, raises ValueError."""
    match = re.fullmatch(r"([0-9]+)/([0-9]+)", text) if isinstance(text, str) else None
    if not match:
        raise ValueError(f"shard must look like i/N, got {text!r}")
    index, total = int(match.group(1)), int(match.group(2))
    if total < 1 or not 1 <= index <= total:
        raise ValueError(f"shard {text!r} is out of range (need 1 <= i <= N)")
    return index, total


def valid_seconds(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def load_durations(path) -> tuple[dict, str | None]:
    """(durations, warning). Never raises: a bad file is an empty mapping plus
    a warning, which select() turns into the deterministic no-durations split."""
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        return {}, f"durations file {path} unusable ({type(exc).__name__}); using the default-weight split"
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or not isinstance(raw.get("tests"), dict):
        return {}, f"durations file {path} is not a {SCHEMA} document; using the default-weight split"
    tests = raw["tests"]
    good = {k: float(v) for k, v in tests.items() if isinstance(k, str) and valid_seconds(v)}
    dropped = len(tests) - len(good)
    return good, (f"{dropped} unusable duration value(s) in {path} ignored" if dropped else None)


def assign(tests, total: int, durations: dict) -> dict:
    """path -> zero-based shard, by greedy longest-processing-time.

    Sort key is (-weight, path): ties break on the path, never on arrival order,
    so the split depends only on the durations and the SET of tests."""
    weight = {t: float(durations.get(t, DEFAULT_WEIGHT_SECONDS)) for t in set(tests)}
    load = [0.0] * total
    placed = {}
    for test in sorted(weight, key=lambda t: (-weight[t], t)):
        lightest = min(range(total), key=lambda b: (load[b], b))
        placed[test] = lightest
        load[lightest] += weight[test]
    return placed


def select(tests, index: int, total: int, durations: dict) -> list:
    """The tests shard `index` of `total` runs, in the order they were given."""
    tests = list(tests)
    if total == 1:
        return tests  # today's behaviour, untouched: no filtering, no reordering
    placed = assign(tests, total, durations)
    return [t for t in tests if placed[t] == index - 1]


def _cmd_select(args) -> int:
    try:
        index, total = parse_shard(args.shard)
    except ValueError as exc:
        print(f"ci-gate-shards: {exc}", file=sys.stderr)
        return 64
    tests = [line.strip() for line in sys.stdin if line.strip()]
    durations: dict = {}
    if total > 1:
        durations, warning = load_durations(args.durations)
        if warning:
            print(f"ci-gate-shards: WARNING {warning}", file=sys.stderr)
    for test in select(tests, index, total, durations):
        print(test)
    return 0


def _cmd_record(args) -> int:
    """Write this run's per-test wall seconds, read from ci-quarantine's .secs
    files, as a timings document the refresh script can merge."""
    tests = [line.strip() for line in sys.stdin if line.strip()]
    seconds = {}
    for test in tests:
        secs = Path(args.logdir) / f"gate-{Path(test).name}.log.secs"
        try:
            value = float(secs.read_text().strip())
        except (OSError, ValueError):
            continue  # no timing is a gap in the data, never a failure of the class
        if valid_seconds(value):
            seconds[test] = round(value, 3)
    if args.static_seconds is not None and valid_seconds(args.static_seconds):
        seconds[STATIC_KEY] = round(args.static_seconds, 3)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": TIMINGS_SCHEMA, "unit": "seconds",
                               "tests": dict(sorted(seconds.items()))}, indent=1) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pick = sub.add_parser("select", help="print this shard's tests from stdin")
    pick.add_argument("--shard", required=True)
    pick.add_argument("--durations", default=str(DURATIONS_FILE))
    rec = sub.add_parser("record", help="write this run's timings document")
    rec.add_argument("--logdir", required=True)
    rec.add_argument("--out", required=True)
    rec.add_argument("--static-seconds", type=float, default=None)
    args = parser.parse_args()
    return _cmd_select(args) if args.command == "select" else _cmd_record(args)


if __name__ == "__main__":
    sys.exit(main())
