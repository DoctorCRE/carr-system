"""gate_scorecard.py — every hook gate judged by what it caught against what it cost.

Joe's ruling, 2026-10-12 (decision 1e957d61): a gate or rule earns its place
with a measured true-positive count against its wall time, context cost and
turns held open; one that only costs is demoted to log-only or retired.
Before this command that measurement took transcript forensics (the
Orchestrator.macbook scorecard took an afternoon). Now it is one read of the
hook meter's own telemetry.

    ./run.sh gate-scorecard [--days N] [--json] [--telemetry PATH ...]

SOURCE. out/hook-telemetry.jsonl and its rotations (hooks/hook_meter.py
writes one row per hook invocation: hook, event, elapsed_ms, outcome,
register, reopen, error_tail). Nothing else is read, so the card says the
same thing on every machine that has the meter.

WHAT A ROW MEANS. `fires` is invocations; `catches` is the rows whose
register was block or reopen (the gate held a turn or refused a call);
`announces` is advisory text that cost context without holding anything;
`errors` is crashes and timeouts (the meter's error register, or a non-zero
exit). `cost` is wall-clock seconds the partner waited, summed.

THE BOUND ACTION (rule 590b11e1: no metric without one). Each row carries a
verdict computed from the numbers, and the verdict names the next step:

    KEEP      it caught something, or it is cheap and quiet
    MEASURE   fewer than MIN_FIRES fires: too little evidence to judge
    DEMOTE    many fires, zero catches, and real cost (seconds or announces):
              candidate for log-only; retire through the gate cascade
    FIX       it errored on more than ERROR_RATE of its fires: a crashing
              gate protects nothing and costs every turn (the stale-claim
              gate ran dead on 224 of 224 Stops before anyone looked)

Context characters are not in the meter yet; the card says so rather than
estimating them.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_FIRES = 20
ERROR_RATE = 0.05
SLOW_MS = 1000.0
DEMOTE_SECONDS = 30.0
DEMOTE_ANNOUNCES = 50


def telemetry_paths(explicit: list[str] | None = None, out_dir: str | None = None) -> list[str]:
    if explicit:
        return [p for p in explicit if os.path.exists(p)]
    base = out_dir or os.path.join(REPO, "out")
    found = sorted(glob.glob(os.path.join(base, "hook-telemetry*.jsonl*")))
    return found


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_rows(paths: list[str], since: datetime | None = None):
    for path in paths:
        try:
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or not row.get("hook"):
                    continue
                if since is not None:
                    ts = _parse_ts(row.get("ts"))
                    if ts is None or ts < since:
                        continue
                yield row


def tally(rows) -> dict[tuple[str, str], dict]:
    cards: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "fires": 0, "catches": 0, "announces": 0, "errors": 0,
        "total_ms": 0.0, "max_ms": 0.0, "sessions": set()})
    for row in rows:
        key = (str(row.get("hook")), str(row.get("event") or "?"))
        card = cards[key]
        card["fires"] += 1
        register = str(row.get("register") or "")
        if register in ("block", "reopen") or row.get("reopen") is True:
            card["catches"] += 1
        elif register == "announce":
            card["announces"] += 1
        exit_code = row.get("exit")
        if register == "error" or (isinstance(exit_code, int) and exit_code not in (0, 2)):
            card["errors"] += 1
        try:
            ms = float(row.get("elapsed_ms") or 0.0)
        except (TypeError, ValueError):
            ms = 0.0
        card["total_ms"] += ms
        card["max_ms"] = max(card["max_ms"], ms)
        if row.get("session"):
            card["sessions"].add(str(row["session"]))
    return cards


def verdict(card: dict) -> tuple[str, str]:
    fires = card["fires"]
    if fires and card["errors"] / fires > ERROR_RATE:
        return "FIX", f"errored on {card['errors']} of {fires} fires; a crashing gate protects nothing"
    if fires < MIN_FIRES:
        return "MEASURE", f"only {fires} fires; not enough evidence to judge"
    seconds = card["total_ms"] / 1000.0
    avg = card["total_ms"] / fires
    if card["catches"] == 0 and (seconds >= DEMOTE_SECONDS or card["announces"] >= DEMOTE_ANNOUNCES
                                 or avg >= SLOW_MS):
        why = []
        if seconds >= DEMOTE_SECONDS:
            why.append(f"{seconds:.0f}s of waiting")
        if card["announces"] >= DEMOTE_ANNOUNCES:
            why.append(f"{card['announces']} advisory lines")
        if avg >= SLOW_MS:
            why.append(f"{avg:.0f}ms a fire")
        return "DEMOTE", "zero catches for " + ", ".join(why) + "; candidate for log-only"
    if card["catches"]:
        return "KEEP", f"{card['catches']} catches"
    return "KEEP", "cheap and quiet"


def build(paths: list[str], days: int | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)) if days else None
    cards = tally(read_rows(paths, since))
    rows = []
    for (hook, event), card in cards.items():
        v, why = verdict(card)
        rows.append({
            "hook": hook, "event": event, "fires": card["fires"], "catches": card["catches"],
            "announces": card["announces"], "errors": card["errors"],
            "seconds": round(card["total_ms"] / 1000.0, 1),
            "avg_ms": round(card["total_ms"] / card["fires"], 1) if card["fires"] else 0.0,
            "max_ms": round(card["max_ms"], 1), "sessions": len(card["sessions"]),
            "verdict": v, "why": why,
        })
    order = {"FIX": 0, "DEMOTE": 1, "MEASURE": 3, "KEEP": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], -r["seconds"], r["hook"]))
    return {"schema": "gate-scorecard/v1", "generated_at": now.isoformat(), "days": days,
            "sources": paths, "rows": rows,
            "not_measured": ["context characters per fire (the meter does not record output size yet)"]}


def render(card: dict) -> str:
    rows = card["rows"]
    if not rows:
        return ("gate-scorecard: no telemetry rows found in "
                + (", ".join(card["sources"]) or "out/hook-telemetry*.jsonl")
                + "\n  on breach: hooks/hook-meter-run.py is not wrapping the gates on this machine; "
                "run ./run.sh config check")
    head = f"{'verdict':8s} {'hook':36s} {'event':16s} {'fires':>6s} {'catch':>5s} {'annc':>5s} {'err':>4s} {'secs':>7s} {'avg_ms':>7s} {'sess':>4s}  why"
    lines = [f"gate-scorecard: {len(rows)} wirings"
             + (f", last {card['days']} day(s)" if card["days"] else ", all telemetry")
             + f"; generated {card['generated_at'][:19]}Z", head]
    for r in rows:
        lines.append(f"{r['verdict']:8s} {r['hook'][:36]:36s} {r['event'][:16]:16s} {r['fires']:6d} "
                     f"{r['catches']:5d} {r['announces']:5d} {r['errors']:4d} {r['seconds']:7.1f} "
                     f"{r['avg_ms']:7.0f} {r['sessions']:4d}  {r['why']}")
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["verdict"]] += 1
    lines.append("  " + "  ".join(f"{k}={counts[k]}" for k in ("FIX", "DEMOTE", "KEEP", "MEASURE")))
    lines.append("  on FIX: repair the gate or remove it from ops/config/hooks.json and reinstall; "
                 "on DEMOTE: switch the gate to log-only, then retire through the gate cascade; "
                 "not measured: " + "; ".join(card["not_measured"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=None, help="only rows from the last N days")
    parser.add_argument("--json", action="store_true", help="print the card as JSON")
    parser.add_argument("--telemetry", nargs="*", help="telemetry files to read instead of out/")
    args = parser.parse_args(argv)
    paths = telemetry_paths(args.telemetry)
    card = build(paths, args.days)
    if args.json:
        print(json.dumps(card, indent=1, sort_keys=True))
    else:
        print(render(card))
    return 0


if __name__ == "__main__":
    sys.exit(main())
