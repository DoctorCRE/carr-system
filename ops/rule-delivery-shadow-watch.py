#!/usr/bin/env python3
"""Nightly watch: is the shadow comparison still running, and has it found a miss?

WHY THIS EXISTS. Both 2026-08-23 council chairs required the same thing before
any rule stops being recited: one week of running the scoped selector beside the
full recitation, logging which rules would have been omitted against which the
work actually needed, and flipping enforcement on only at zero unexplained
misses on consequential actions.

A week of measurement has one failure mode, and this system has already paid for
it twice. The rule-admission contract sat unmeasured in Production for months
because the only thing that could measure it was a door a human had to open; the
same day that was found, its nightly watch was built for exactly this reason. A
shadow week that quietly stops recording produces the same clean-looking silence
as a shadow week that found nothing, and the second one is what people act on.

SO THIS REPORTS TWO THINGS, and the first matters more than the second:

  1. IS THE RECORDER ALIVE. out/rule-delivery-shadow.jsonl is written by
     hooks/rule-pack-drift-gate.py, once per turn that implied or loaded a pack.
     An empty log, or a newest row older than the staleness window, is reported
     as a FINDING rather than as a quiet pass — because "no misses" and "no
     measurement" print identically otherwise.
  2. WHAT THE WEEK FOUND. Turns observed, packs implied, and MISSES: a rule the
     turn's own work needed that a scoped boot would not have delivered. That
     number is the enforcement gate. It is printed with the sessions and rules
     behind it so a human can read the miss rather than trust the count.

WHAT IT MAY READ IN THE DATABASE: the delivery tag counts and the policy row,
through ops/rule-delivery-audit.py's counts(), under the routine jobs role in a
read-only transaction. It reads no rule text — migration 0285 already refused
that role one, and a watch that only counts should not carry more.

EXIT CONTRACT, the same one its siblings in bin/nightly.sh use:
  78  no routine credential configured — a SKIP, not a failed night
  0   ran; findings are PRINTED rather than raised, because a step that fails
      every night until someone acts trains people to stop reading it
  1   the watch itself could not run — a wrong role, a missing grant
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_shadow import current_identity  # noqa:E402
LOG = REPO / "out" / "rule-delivery-shadow.jsonl"
EX_CONFIG = 78
STALE_HOURS = 48


def load_audit():
    path = REPO / "ops" / "rule-delivery-audit.py"
    spec = importlib.util.spec_from_file_location("rule_delivery_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the delivery audit from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_eligibility():
    path = REPO / "ops" / "rule-delivery-shadow-eligibility.py"
    spec = importlib.util.spec_from_file_location("rule_delivery_shadow_eligibility", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the shadow eligibility check from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_log(path: Path) -> tuple[list[dict], int]:
    """Rows and the count of lines that could not be parsed."""
    rows: list[dict] = []
    unreadable = 0
    if not path.exists():
        return rows, unreadable
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                unreadable += 1
    return rows, unreadable


def summarize(rows: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    misses: list[dict] = []
    packs: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    errors = 0
    newest: datetime | None = None
    for row in rows:
        if row.get("record_type") not in {None, "observation"}:
            continue
        if row.get("error"):
            errors += 1
        for pack in row.get("needed", []) or []:
            packs[pack] += 1
        if row.get("mode"):
            modes[row["mode"]] += 1
        if row.get("missed_rules"):
            misses.append({"session": row.get("session"), "ts": row.get("ts"),
                           "packs": row.get("missing", []),
                           "rules": row["missed_rules"]})
        stamp = row.get("ts")
        if isinstance(stamp, str):
            try:
                seen = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
            except ValueError:
                continue
            if newest is None or seen > newest:
                newest = seen
    age_hours = None if newest is None else (now - newest).total_seconds() / 3600
    stale = age_hours is None or age_hours > STALE_HOURS
    turns = sum(1 for row in rows if row.get("record_type") in {None, "observation"})
    return {"turns": turns, "misses": misses, "miss_count": len(misses),
            "packs_seen": dict(packs.most_common()), "modes": dict(modes),
            "gate_errors": errors, "newest": newest.strftime("%Y-%m-%dT%H:%M:%SZ")
            if newest else None, "age_hours": age_hours, "stale": stale}


def routine_dsn() -> str | None:
    value = os.environ.get("CARR_DB_JOBS_URL", "").strip()
    if not value:
        return None
    try:
        login = unquote(urlsplit(value).username or "").strip().lower()
    except ValueError:
        login = ""
    if login in {"carr_writer", "carr_owner", "owner", "writer", "postgres"}:
        print("rule-delivery-shadow-watch: CARR_DB_JOBS_URL names an owner or writer "
              "login; refusing to watch Production under it", file=sys.stderr)
        raise SystemExit(1)
    return value


def report(summary: dict, eligibility: dict | None = None) -> None:
    if summary["stale"]:
        # THE FINDING THAT LOOKS LIKE A PASS IF NOBODY PRINTS IT.
        if summary["newest"] is None:
            print("rule-delivery-shadow-watch: NO SHADOW OBSERVATIONS AT ALL — the "
                  f"recorder has written nothing to {LOG.relative_to(REPO)}. Either the "
                  "Stop gate is not installed in the live client settings, or no session "
                  "has run since it was. A shadow week with no rows is not a clean week.")
        else:
            print("rule-delivery-shadow-watch: SHADOW OBSERVATIONS ARE STALE — newest "
                  f"row {summary['newest']} ({summary['age_hours']:.0f}h old, window is "
                  f"{STALE_HOURS}h). The comparison has stopped running; nothing below "
                  "describes today.")
    if summary["gate_errors"]:
        print(f"  {summary['gate_errors']} turn(s) recorded a gate error — the drift gate "
              "fails open by design, so these are turns it did not actually measure.")
    if summary["miss_count"]:
        print(f"rule-delivery-shadow-watch: {summary['miss_count']} MISS(es) — a scoped "
              "boot would not have delivered a rule the turn's own work needed. "
              "Enforcement does not flip on while this is above zero.")
        for miss in summary["misses"][-10:]:
            print(f"  {miss['ts']} session={miss['session']} "
                  f"missing_pack={','.join(miss['packs'])} "
                  f"rules={','.join(miss['rules'])}")
    elif not summary["stale"]:
        # Only say this when the measurement is actually current. "0 misses"
        # under a stale log is the reassuring sentence that hides the finding
        # above it, which is the whole failure this watch exists to prevent.
        # "turns that implied or loaded a pack", NOT "turns". The gate writes no
        # row for a turn with no pack signal at all, so calling this the turn
        # count would report a denominator nobody measured.
        print(f"rule-delivery-shadow-watch: {summary['turns']} turn(s) with a pack "
              "signal, 0 misses.")
    if summary["packs_seen"]:
        print("  packs the observed work implied: "
              + ", ".join(f"{k}={v}" for k, v in summary["packs_seen"].items()))
    if summary["modes"]:
        print("  delivery modes seen: "
              + ", ".join(f"{k}={v}" for k, v in summary["modes"].items()))
    if eligibility is None:
        print("rule-delivery-shadow-watch: ENFORCEMENT BLOCKED — current live "
              "policy/map/source identity was not available")
    elif eligibility["eligible"]:
        print("rule-delivery-shadow-watch: ENFORCEMENT ELIGIBLE — "
              f"epoch={eligibility['epoch_id']} "
              f"scoped={eligibility['qualifying_observations']} "
              f"closed_findings={eligibility['closed_findings']}")
    else:
        print("rule-delivery-shadow-watch: ENFORCEMENT BLOCKED")
        for reason in eligibility["reasons"]:
            print("  " + reason)
        for item in eligibility["open"]:
            print(f"  OPEN {item['kind']} event={item['event_id']} owner=UNASSIGNED "
                  "remedy=UNRECORDED")
        for item in eligibility["closed"]:
            print(f"  CLOSED {item['kind']} event={item['event_id']} "
                  f"disposition={item['disposition']} owner={item['owner']} "
                  f"remedy={item['remedy_ref']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=str(LOG))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows, unreadable = read_log(Path(args.log))
    summary = summarize(rows)
    if unreadable:
        summary["unreadable_lines"] = unreadable

    counts = None
    identity = None
    dsn = routine_dsn()
    if dsn:
        import psycopg

        audit = load_audit()
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("begin transaction read only")
            cur.execute("select session_user, current_user")
            row = cur.fetchone()
            if not row or {str(v) for v in row} != {"carr_jobs"}:
                print(f"rule-delivery-shadow-watch: not a provisioned jobs identity ({row}); "
                      "refusing to read Production", file=sys.stderr)
                return 1
            try:
                counts = audit.counts(cur)
                cur.execute("""select mode,changed_by,reason,changed_at
                                 from ops.rule_delivery_policy where singleton""")
                identity = current_identity(REPO, cur.fetchone())
            except psycopg.errors.InsufficientPrivilege as exc:
                print("rule-delivery-shadow-watch: the jobs role cannot read the delivery "
                      f"tags — migration 0291 may not be applied here ({exc})",
                      file=sys.stderr)
                return 1
            except psycopg.errors.UndefinedTable:
                print("rule-delivery-shadow-watch: the delivery tables are absent — "
                      "migration 0291 has not been applied here", file=sys.stderr)
                return 1

    eligibility = load_eligibility().evaluate(rows, identity=identity)
    if unreadable:
        eligibility["eligible"] = False
        eligibility["reasons"].append(f"{unreadable} unreadable telemetry line(s)")

    if args.json:
        print(json.dumps({"log": summary, "database": counts,
                          "eligibility": eligibility}, sort_keys=True, default=str))
        return 0

    report(summary, eligibility)
    if counts is not None:
        audit = load_audit()
        prefix = ("PRODUCTION'S DELIVERY TAGS ARE INCOMPLETE — "
                  if audit.failing(counts) else "delivery tags: ")
        print("rule-delivery-shadow-watch: " + prefix + audit.render(counts))
        if audit.failing(counts):
            print("  Reconcile with ./bin/sync-rule-admission-prod.sh (bare to read, "
                  "--apply once the reviewed map tags every active rule).")
    elif not dsn:
        print("rule-delivery-shadow-watch: NOT CONFIGURED for the database half "
              "(no CARR_DB_JOBS_URL); the log half above still ran", file=sys.stderr)
        return EX_CONFIG
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
