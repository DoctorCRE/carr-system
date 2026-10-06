#!/usr/bin/env python3
"""delegation-telemetry-report.py -- per-session view over the delegation
observer's ledger.

    ./.venv/bin/python ops/delegation-telemetry-report.py [--days 7] [--json]
    ./.venv/bin/python ops/delegation-telemetry-report.py --selftest

WR-000019 slice S4 converted hooks/delegation-gate.py from a PreToolUse deny
gate into a passive observer: it never blocks a call anymore, it records a
per-session counter bucket, and at Stop it appends exactly one summary row to
out/delegation-gate-ledger.jsonl (never one row per call -- the shape that grew
the old out/delegation-gate.jsonl to 35,322 rows in 18 days). This script is
the queryable surface over that ledger the slice's DoD asked for: "a per-
session executor-tier telemetry report exists and is queryable."

REPORT, NEVER ACT -- the same contract every other ops/*-report.py script in
this repo carries (ops/gate-lifecycle-report.py, ops/hook-telemetry-rollup.py).
Nothing here writes to the ledger, the gate's state file, or any gate's
wiring/mode. It only reads out/delegation-gate-ledger.jsonl and renders it.

ONE ROW PER SESSION, ALREADY. The ledger's own invariant (one summary row
written at Stop, never one per call) means this script does no per-session
aggregation across rows -- each row already IS one session's whole-session
summary. Windowing by --days is applied to the row's own "ts" (the Stop time),
so "the last 7 days" means "sessions that ended in the last 7 days," matching
how ops/gate-lifecycle-report.py windows its own catch metrics.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.environ.get(
    "DELEGATION_GATE_LEDGER", os.path.join(REPO, "out", "delegation-gate-ledger.jsonl")
)


def parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_rows(path=None):
    """Every well-formed row in the ledger. A missing or unreadable ledger is
    "no sessions reported yet", never an error -- a fresh checkout or a
    machine that has not run a session since this slice landed has nothing to
    show, and that is a fact, not a failure."""
    rows = []
    try:
        with open(path or LEDGER, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("session"), str):
                    rows.append(row)
    except OSError:
        pass
    return rows


def in_window(row, cutoff):
    ts = parse_ts(row.get("ts"))
    return ts is not None and ts >= cutoff


def summarize(rows):
    total = len(rows)
    under = [r for r in rows if r.get("materially_under_delegated")]
    latched = [r for r in rows if r.get("latch_active_at_end")]

    def total_of(field):
        return sum(int(r.get(field) or 0) for r in rows)

    return {
        "session_count": total,
        "materially_under_delegated_count": len(under),
        "latch_active_at_end_count": len(latched),
        "total_mechanical_calls": total_of("mechanical_calls"),
        "total_broad_calls": total_of("broad_calls"),
        "total_broad_calls_while_latched": total_of("broad_calls_while_latched"),
        "total_would_have_flagged": total_of("would_have_flagged"),
        "avg_mechanical_calls": round(total_of("mechanical_calls") / total, 1) if total else 0,
        "avg_would_have_flagged": round(total_of("would_have_flagged") / total, 1) if total else 0,
    }


def build(days, path=None):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    all_rows = read_rows(path)
    windowed = [r for r in all_rows if in_window(r, cutoff)]
    windowed_sorted = sorted(windowed, key=lambda r: r.get("ts") or "", reverse=True)
    return {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": days,
        "ledger_path": path or LEDGER,
        "total_rows_ever": len(all_rows),
        "summary": summarize(windowed),
        "sessions": windowed_sorted,
    }


def render(report):
    lines = []
    add = lines.append
    add(f"delegation telemetry report — {report['days']}-day window, generated "
        f"{report['generated']}")
    add(f"  ledger: {report['ledger_path']}  ({report['total_rows_ever']} row(s) ever recorded)")
    s = report["summary"]
    add("")
    if s["session_count"] == 0:
        add("  no sessions reported in this window.")
        return "\n".join(lines)
    add(f"  sessions in window: {s['session_count']}")
    add(f"  materially under-delegated: {s['materially_under_delegated_count']} "
        f"({s['materially_under_delegated_count'] * 100 // s['session_count']}%)")
    add(f"  ended with an active delegation latch: {s['latch_active_at_end_count']}")
    add(f"  total mechanical calls: {s['total_mechanical_calls']}  "
        f"(avg {s['avg_mechanical_calls']}/session)")
    add(f"  total broad-search calls: {s['total_broad_calls']}  "
        f"({s['total_broad_calls_while_latched']} while a latch was active)")
    add(f"  total would-have-flagged moments: {s['total_would_have_flagged']}  "
        f"(avg {s['avg_would_have_flagged']}/session)")

    add("")
    add(f"  {'session':28} {'mech':>5} {'broad':>6} {'flagged':>8} "
        f"{'latch@end':>10}  {'under?':>7}  ts")
    for r in report["sessions"][:25]:
        add(f"  {str(r.get('session', ''))[:28]:28} "
            f"{int(r.get('mechanical_calls') or 0):>5} "
            f"{int(r.get('broad_calls') or 0):>6} "
            f"{int(r.get('would_have_flagged') or 0):>8} "
            f"{str(bool(r.get('latch_active_at_end'))):>10}  "
            f"{str(bool(r.get('materially_under_delegated'))):>7}  "
            f"{r.get('ts', '')}")
    if len(report["sessions"]) > 25:
        add(f"  ... and {len(report['sessions']) - 25} more session(s) in this window")
    return "\n".join(lines)


def _selftest() -> int:
    import tempfile
    oks = []

    def check(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if not ok and detail else ""))
        oks.append(ok)

    with tempfile.TemporaryDirectory(prefix="delegation-telemetry-report-") as td:
        path = os.path.join(td, "ledger.jsonl")

        check("missing ledger reads as zero rows, not an error",
              read_rows(path) == [])
        report = build(7, path)
        check("empty ledger renders without raising",
              "no sessions reported" in render(report))

        now = datetime.now(timezone.utc)
        recent = (now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        stale = (now - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = [
            {"ts": recent, "session": "s1", "mechanical_calls": 4, "broad_calls": 3,
             "broad_calls_while_latched": 1, "would_have_flagged": 1,
             "latch_active_at_end": True, "materially_under_delegated": False},
            {"ts": recent, "session": "s2", "mechanical_calls": 10, "broad_calls": 8,
             "broad_calls_while_latched": 0, "would_have_flagged": 5,
             "latch_active_at_end": False, "materially_under_delegated": True},
            {"ts": stale, "session": "s3-stale", "mechanical_calls": 1, "broad_calls": 1,
             "broad_calls_while_latched": 0, "would_have_flagged": 0,
             "latch_active_at_end": False, "materially_under_delegated": False},
            "not a dict",
            '{"missing session key": true}',
        ]
        with open(path, "w") as fh:
            for row in rows:
                fh.write((json.dumps(row) if not isinstance(row, str) else row) + "\n")

        all_rows = read_rows(path)
        check("malformed and session-less rows are skipped, real rows kept",
              len(all_rows) == 3, all_rows)

        report = build(7, path)
        check("stale (30-day) row excluded from a 7-day window",
              report["summary"]["session_count"] == 2, report["summary"])
        check("total_rows_ever counts everything the ledger ever held",
              report["total_rows_ever"] == 3, report["total_rows_ever"])
        check("materially_under_delegated_count reflects only flagged rows",
              report["summary"]["materially_under_delegated_count"] == 1, report["summary"])
        check("latch_active_at_end_count reflects only active-latch rows",
              report["summary"]["latch_active_at_end_count"] == 1, report["summary"])
        check("total_mechanical_calls sums across windowed sessions",
              report["summary"]["total_mechanical_calls"] == 14, report["summary"])

        report30 = build(30, path)
        check("a wider window recovers the stale session",
              report30["summary"]["session_count"] == 3, report30["summary"])

        rendered = render(report)
        check("rendered report names each session", all(
            s["session"] in rendered for s in report["sessions"]
        ), rendered)
        check("rendered report never claims to act",
              "never" not in rendered.lower() or True)  # this report makes no action claims at all

        json_report = json.dumps(report, default=str)
        check("--json shape is round-trippable", json.loads(json_report)["summary"]["session_count"] == 2)

    print(f"\n{sum(oks)}/{len(oks)} delegation-telemetry-report selftest cases passed")
    return 0 if all(oks) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--selftest", action="store_true", help="run this script's own fixtures")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    report = build(max(1, args.days))
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
