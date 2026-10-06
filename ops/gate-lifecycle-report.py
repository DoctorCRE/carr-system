#!/usr/bin/env python3
"""gate-lifecycle-report.py — weekly lifecycle report for every wired gate.

    ./.venv/bin/python ops/gate-lifecycle-report.py [--days 7] [--json]

WHAT THIS IS, AND WHAT IT IS NOT. WR-000019 slice S5 asked for gate lifecycle
MACHINERY: a named failure class, a catch metric drawn from the gate's own log,
a review date, and auto-downgrade logic (quiet window -> announce -> two quiet
windows -> retirement proposal), plus a weekly report. It is deliberately built
on top of, not instead of, ops/hook-telemetry-rollup.py: that script already
answers "what did the enforcement stack cost, live" and carries the council's
retire-candidate rule (zero denies + catch-class owned elsewhere + hot path).
This script answers a different, longer-horizon question — for EVERY wired
gate, not just retire candidates: is it still catching anything, against the
metric its own metadata says to trust, and if it has gone quiet, what should
happen next. Same "report, never act" contract as the rollup: nothing here
edits ops/config/hooks.json, a gate's mode, or its wiring. A proposal is a line
in this report, not a side effect.

METADATA. ops/config/gate-lifecycle.json holds one entry per gate actually
wired in ops/config/hooks.json: {failure_class, catch_metric, review_date,
mode}. mode is enforcing|announce|shadow, matching hooks/hook-meter-run.py's
own register vocabulary (block/reopen vs announce vs silent) — see that file's
_register_from_output docstring for why the register is recorded directly
rather than re-derived from an exit code later.

CATCH METRICS ARE HETEROGENEOUS ON PURPOSE, because the gates are. Most fire
through hooks/hook-meter-run.py into the shared out/hook-telemetry.jsonl and
are matched there by hook filename; several (staging-attribution-gate.py,
completion-evidence-gate.py, map-architecture-gate.py, context-handoff-gate.py)
keep their own dedicated log where every row already IS the catch
("row_exists"); rule-pack-drift-gate.py's shadow-mode record needs a field
check (its 'missing' list non-empty); drift-claim-gate.py and
drift-assertion-gate.py are execve'd by hooks/run-record-gate.py before
hook-meter-run.py's own telemetry write ever runs (confirmed: zero rows under
hook=="run-record-gate.py" in the live stream), so their only trace is the
plain-text out/hook-guard.log, matched by regex instead of a JSON field. One
gate, context-handoff-gate.py, logs no timestamp at all in its rows — that is
reported as a genuine data gap (data_available: false), never guessed at zero.

Five true_positive kinds, evaluated by is_true_positive() below:
  row_exists     every (filtered) row in the window is a catch
  field_in       row[field] in values
  field_not_in   row[field] not in values
  field_truthy   bool(row[field])
  text_regex     (text logs only) the line matches `pattern`

THE DOWNGRADE RULE, exactly the shape WR-000019 S5 asked for. A "quiet window"
is a --days-wide window with zero true positives for that gate's own metric.
Scanning backward from the most recent window, count how many consecutive
windows are quiet (capped at MAX_LOOKBACK_WINDOWS so one very old gate does not
force scanning the whole log):

  mode == shadow                          -> never proposes anything. A
                                              shadow gate has no live effect to
                                              downgrade; it is pre-enforcement
                                              instrumentation, not a control
                                              with a blast radius to shrink.
  consecutive_quiet_windows == 0           -> no proposal (active).
  consecutive_quiet_windows == 1
    and mode == enforcing                  -> propose downgrade_to_announce.
    and mode == announce                   -> no proposal yet (needs a second
                                              consecutive quiet window).
  consecutive_quiet_windows >= 2           -> propose retirement, regardless of
                                              current mode (this supersedes the
                                              single-window announce proposal).

A gate whose metric cannot be windowed (data_available: false) never receives
a proposal either way — see context-handoff-gate.py.

REPORT, NEVER ACT. This prints a markdown table to stdout and appends one JSON
summary row to out/gate-lifecycle-report.jsonl. Neither write touches
ops/config/hooks.json, ops/config/gate-lifecycle.json, or any hooks/*.py file.
Promotion or retirement EXECUTION — actually changing a gate's wiring or mode
— stays a human/PR act, exactly like the rollup's retire-candidate rule.

COVERAGE CHECK. Every gate this script finds wired in ops/config/hooks.json
must have an entry in ops/config/gate-lifecycle.json (check:s5-lifecycle-
fields-present). A gap there is a real defect in this slice's own deliverable,
so — unlike the softer --strict budget check in the rollup — a missing entry
fails this script's exit code by default; there is no flag that silences it.
A gate-lifecycle.json entry naming a gate no longer wired is reported as a
stale-metadata WARNING instead, since a parallel slice (S3) may retire or
rewire a gate while this report runs; that case is not this script's mistake
to fail on.

NO NEW SCHEDULED JOB WIRED BY THIS SLICE. bin/nightly.sh (see its "step" calls,
e.g. line ~1261: `step "hook telemetry rollup (reports, never retires)"
./.venv/bin/python ops/hook-telemetry-rollup.py --days 7`) is where this repo's
existing scheduled chain is declared, and it runs NIGHTLY. This report is
WEEKLY by design (a "quiet window" is a --days=7 window), so the ready step for
a follow-up to add, gated to run once a week, is:

    step "gate lifecycle report (weekly; reports, never retires)" \\
        env CARR_GATE_LIFECYCLE_WEEKLY=1 ./.venv/bin/python \\
        ops/gate-lifecycle-report.py --days 7

guarded the way this repo already gates weekly-vs-nightly steps elsewhere in
bin/nightly.sh (day-of-week test around the `step` call), or as its own
launchd/scheduled-tasks entry alongside the others under
ops/config/control-plane-*.json. Deliberately NOT added in this slice: wiring
a new job is exactly the kind of "convert or rewire" action the S5 scope
boundary reserves for a follow-up, not for the machinery itself.

Fixtures: ops/gate-lifecycle-report-selftest.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

REPO = os.environ.get("CARR_LIFECYCLE_REPO") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "out")
METADATA_PATH = os.path.join(REPO, "ops", "config", "gate-lifecycle.json")
WIRING_PATH = os.path.join(REPO, "ops", "config", "hooks.json")
REPORT_LOG = os.path.join(OUT, "gate-lifecycle-report.jsonl")

MAX_LOOKBACK_WINDOWS = 8   # ~2 months of weekly windows; bounds the scan-back


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def wired_gate_files():
    """The real gate SCRIPT names wired in ops/config/hooks.json.

    Unwraps hooks/hook-meter-run.py (the timing wrapper — never a gate itself)
    and hooks/run-record-gate.py (a dispatcher that execve's the NAMED gate,
    e.g. drift-claim-gate.py, as its one bare argument — the wrapper is not a
    gate either, and its argument IS one). One gate, rule-pack-drift-gate.py,
    is invoked with no wrapper at all, and unwraps to itself.
    """
    data = load_json(WIRING_PATH, {}) or {}
    names = set()
    for event, blocks in data.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            for hook in block.get("hooks", []):
                cmd = hook.get("command", "")
                toks = [part.rsplit("/", 1)[-1] for part in cmd.split()
                        if part.endswith(".py")]
                toks = [t for t in toks if t != "hook-meter-run.py"]
                if not toks:
                    continue
                if toks[0] == "run-record-gate.py" and len(toks) > 1:
                    names.add(toks[1])
                else:
                    names.add(toks[0])
    return names


def parse_ts(value):
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


LEADING_TS_RE = re.compile(r"^(\S+)\s")


def read_rows(metric, status=None):
    """Read timestamped rows and expose incomplete collection to the consumer.

    A torn line remains nonfatal, but it makes coverage partial and cannot
    establish a quiet window for an enforcement downgrade.
    """
    status = status if status is not None else {}
    status.update(files_read=0, unreadable_files=0, invalid_rows=0, invalid_timestamps=0)
    path = os.path.join(REPO, metric["log_path"])
    fmt = metric.get("log_format", "jsonl")
    ts_field = metric.get("ts_field")
    hook_filter = metric.get("hook_filter")
    rows = []
    for candidate in (path + ".1", path):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8", errors="strict") as fh:
                status['files_read'] += 1
                for line in fh:
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    if fmt == "text":
                        if hook_filter and hook_filter.get("kind") == "text_prefix_field":
                            parts = line.split()
                            idx = hook_filter["field_index"]
                            if len(parts) <= idx:
                                status['invalid_rows'] += 1
                                continue
                            if parts[idx] != hook_filter["equals"]:
                                continue
                        match = LEADING_TS_RE.match(line) if ts_field == 'leading_iso' else None
                        ts = parse_ts(match.group(1)) if match else None
                        row = line
                    else:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            status['invalid_rows'] += 1
                            continue
                        if not isinstance(row, dict):
                            status['invalid_rows'] += 1
                            continue
                        if hook_filter and hook_filter.get("field") is not None:
                            if row.get(hook_filter["field"]) != hook_filter["equals"]:
                                continue
                        ts = parse_ts(row.get(ts_field)) if ts_field else None
                    if ts is None:
                        status['invalid_timestamps'] += 1
                    rows.append((row, ts))
        except (OSError, UnicodeError):
            status['unreadable_files'] += 1
    status['coverage'] = ('missing' if not status['files_read'] else
                          'partial' if any(status[key] for key in ('unreadable_files', 'invalid_rows', 'invalid_timestamps')) else 'complete')
    return rows


def is_true_positive(row, metric):
    tp = metric["true_positive"]
    kind = tp["kind"]
    fmt = metric.get("log_format", "jsonl")
    if kind == "row_exists":
        return True
    if kind == "text_regex":
        return fmt == "text" and re.search(tp["pattern"], row) is not None
    if fmt == "text":
        return False
    if kind == "field_in":
        return row.get(tp["field"]) in tp["values"]
    if kind == "field_not_in":
        return row.get(tp["field"]) not in tp.get("values", [])
    if kind == "field_truthy":
        return bool(row.get(tp["field"]))
    raise ValueError(f"unknown true_positive kind: {kind!r}")


def windows_back(now, days, count):
    """[(start, end), ...] oldest-first is NOT how this is used — index 0 is
    the most recent (current) window, index 1 the one before it, and so on."""
    out = []
    for k in range(count):
        end = now - timedelta(days=days * k)
        start = now - timedelta(days=days * (k + 1))
        out.append((start, end))
    return out


def evaluate_gate(gate, entry, days, now):
    metric = entry["catch_metric"]
    ts_field = metric.get("ts_field")
    log_path = os.path.join(REPO, metric["log_path"])
    data_available = (ts_field is not None and
                      any(os.path.isfile(path) for path in (log_path, log_path + ".1")))
    result = {
        "gate": gate,
        "mode": entry["mode"],
        "failure_class": entry["failure_class"],
        "review_date": entry["review_date"],
        "log_path": metric["log_path"],
        "data_available": data_available,
        "current_window_true_positives": None,
        "consecutive_quiet_windows": None,
        "proposal": None,
        "note": metric.get("note") or "",
    }
    coverage = {}
    rows = read_rows(metric, coverage)
    data_available = data_available and coverage['coverage'] == 'complete'
    result['data_available'] = data_available
    result['telemetry_status'] = coverage
    if not data_available:
        result["total_rows_seen"] = len(rows)
        return result

    windows = windows_back(now, days, MAX_LOOKBACK_WINDOWS)
    counts = [0] * len(windows)
    for row, ts in rows:
        if ts is None:
            continue
        tp = is_true_positive(row, metric)
        if not tp:
            continue
        for i, (start, end) in enumerate(windows):
            if start <= ts < end:
                counts[i] += 1
                break

    result["current_window_true_positives"] = counts[0]
    result["window_true_positive_counts"] = counts

    consecutive_quiet = 0
    for c in counts:
        if c == 0:
            consecutive_quiet += 1
        else:
            break
    result["consecutive_quiet_windows"] = consecutive_quiet

    mode = entry["mode"]
    proposal = None
    if mode != "shadow":
        if consecutive_quiet >= 2:
            proposal = {
                "action": "propose_retirement",
                "reason": f"{consecutive_quiet} consecutive quiet {days}-day windows "
                          f"(no true positives for {gate}'s catch metric).",
            }
        elif consecutive_quiet == 1 and mode == "enforcing":
            proposal = {
                "action": "downgrade_to_announce",
                "reason": f"1 quiet {days}-day window (no true positives for "
                          f"{gate}'s catch metric).",
            }
    result["proposal"] = proposal
    return result


def build(days):
    now = datetime.now(timezone.utc)
    metadata = load_json(METADATA_PATH, {}) or {}
    gates = metadata.get("gates", {})
    wired = wired_gate_files()

    missing_metadata = sorted(g for g in wired if g not in gates)
    stale_metadata = sorted(g for g in gates if g not in wired)

    results = {}
    for gate, entry in sorted(gates.items()):
        results[gate] = evaluate_gate(gate, entry, days, now)

    proposals = [r for r in results.values() if r["proposal"]]
    data_gaps = [r for r in results.values() if not r["data_available"]]

    return {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": days,
        "wired_gate_count": len(wired),
        "metadata_gate_count": len(gates),
        "missing_metadata": missing_metadata,
        "stale_metadata": stale_metadata,
        "gates": results,
        "proposals": proposals,
        "data_gaps": [r["gate"] for r in data_gaps],
    }


def render(report):
    lines = []
    add = lines.append
    add(f"gate lifecycle report — {report['days']}-day windows, generated "
        f"{report['generated']}")
    add(f"  gates wired in ops/config/hooks.json: {report['wired_gate_count']}   "
        f"gates with lifecycle metadata: {report['metadata_gate_count']}")

    if report["missing_metadata"]:
        add("")
        add("  MISSING LIFECYCLE METADATA (check:s5-lifecycle-fields-present FAILS):")
        for g in report["missing_metadata"]:
            add(f"    {g}")
    else:
        add("  check:s5-lifecycle-fields-present — every wired gate has lifecycle fields. OK")

    if report["stale_metadata"]:
        add("")
        add("  STALE METADATA (entry present, gate no longer wired — not this")
        add("  script's mistake to fail on; may be a parallel slice's rewiring):")
        for g in report["stale_metadata"]:
            add(f"    {g}")

    add("")
    add(f"  {'gate':38} {'mode':10} {'tp('+str(report['days'])+'d)':>9} "
        f"{'quiet':>6}  {'review':>10}  proposal")
    for gate, r in sorted(report["gates"].items()):
        tp = "n/a" if not r["data_available"] else str(r["current_window_true_positives"])
        quiet = "n/a" if not r["data_available"] else str(r["consecutive_quiet_windows"])
        prop = r["proposal"]["action"] if r["proposal"] else "—"
        add(f"  {gate[:38]:38} {r['mode']:10} {tp:>9} {quiet:>6}  "
            f"{r['review_date']:>10}  {prop}")

    add("")
    if report["proposals"]:
        add("  DOWNGRADE / RETIREMENT PROPOSALS (report only — nothing here changes")
        add("  a gate's wiring or mode; promotion/retirement execution stays human/PR):")
        for r in report["proposals"]:
            add(f"    {r['gate']}: {r['proposal']['action']}")
            add(f"      {r['proposal']['reason']}")
            add(f"      catches: {r['failure_class']}")
    else:
        add("  DOWNGRADE / RETIREMENT PROPOSALS  none this window.")

    if report["data_gaps"]:
        add("")
        add("  DATA GAPS (catch metric has no timestamp field — windowed count is")
        add("  unavailable, never guessed at zero; excluded from proposals):")
        for g in report["data_gaps"]:
            add(f"    {g}: {report['gates'][g]['note']}")

    return "\n".join(lines)


def append_report_row(report):
    os.makedirs(OUT, exist_ok=True)
    row = {
        "ts": report["generated"],
        "days": report["days"],
        "wired_gate_count": report["wired_gate_count"],
        "metadata_gate_count": report["metadata_gate_count"],
        "missing_metadata": report["missing_metadata"],
        "stale_metadata": report["stale_metadata"],
        "proposals": [
            {"gate": r["gate"], "action": r["proposal"]["action"],
             "reason": r["proposal"]["reason"]}
            for r in report["proposals"]
        ],
        "data_gaps": report["data_gaps"],
    }
    with open(REPORT_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--no-append", action="store_true",
                    help="skip the out/gate-lifecycle-report.jsonl append (tests)")
    args = ap.parse_args()

    report = build(max(1, args.days))

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))

    if not args.no_append:
        append_report_row(report)

    return 1 if report["missing_metadata"] else 0


if __name__ == "__main__":
    sys.exit(main())
