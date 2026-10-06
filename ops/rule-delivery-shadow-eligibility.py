#!/usr/bin/env python3
"""Fail-closed seven-day eligibility check for rule-delivery enforcement."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_shadow import (  # noqa:E402
    can_start_epoch, current_identity, finding, inspect, make_disposition,
    make_epoch, make_error_observation, make_observation, observation_id,
    require_identity, scoped, stamp,
)

DEFAULT_LOG = REPO / "out" / "rule-delivery-shadow.jsonl"
WINDOW = timedelta(days=7)
MAX_GAP = timedelta(hours=48)


def load(path: Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    bad = 0
    if not path.exists():
        return rows, bad
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError
            rows.append(row)
        except ValueError:
            bad += 1
    return rows, bad


def _base(reasons: list[str]) -> dict:
    return {"eligible": False, "reasons": reasons, "qualifying_observations": 0,
            "open_findings": 0, "closed_findings": 0, "open": [], "closed": [],
            "miss_count": 0, "gate_errors": 0}


def evaluate(rows: list[dict], now: datetime | None = None,
             identity: dict | None = None) -> dict:
    """Evaluate only the latest explicit, identity-bound append-only epoch."""
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    try:
        require_identity(identity)
    except ValueError as exc:
        reasons.append(str(exc))

    state = inspect(rows)
    reasons.extend(state["errors"])
    for index, row in enumerate(rows):
        seen = stamp(row) if isinstance(row, dict) else None
        if seen is not None and seen > now:
            reasons.append(f"future ledger timestamp at row {index + 1}")
    if not state["epochs"]:
        result = _base(reasons + ["no valid shadow epoch"])
        result["legacy_findings"] = sum(
            1 for _event_id, (_index, row) in state["observations"].items()
            if finding(row))
        return result

    epoch_index, epoch = state["epochs"][-1]
    epoch_identity = {key: epoch[key] for key in
                      ("policy_digest", "map_digest", "source_digest")}
    if identity is not None:
        for key, expected in identity.items():
            if epoch_identity.get(key) != expected:
                reasons.append(f"shadow epoch {key} differs from current {key}")

    after = [(event_id, index, row) for event_id, (index, row)
             in state["observations"].items() if index > epoch_index]
    epoch_seen = stamp(epoch)
    assert epoch_seen is not None
    for event_id, _index, row in after:
        seen = stamp(row)
        if seen is None or seen < epoch_seen:
            reasons.append(f"observation {event_id} has invalid pre-epoch timestamp")
        for key in ("map_digest", "source_digest"):
            if row.get(key) != epoch_identity[key]:
                reasons.append(f"observation {event_id} {key} differs from current epoch")
    observations: list[tuple[datetime, str, dict]] = []
    for event_id, _index, row in after:
        seen = stamp(row)
        if scoped(row) and seen is not None:
            observations.append((seen, event_id, row))
    observations.sort(key=lambda item: item[0])
    if not observations:
        reasons.append("no actual scoped shadow observations after current epoch")
    else:
        first, last = observations[0][0], observations[-1][0]
        if now - first < WINDOW:
            reasons.append(
                f"scoped shadow window is {(now-first).total_seconds()/3600:.1f}h, needs 168h")
        if now - last > MAX_GAP:
            reasons.append(f"newest scoped observation is {(now-last).total_seconds()/3600:.1f}h old")
        gaps = [(b[0] - a[0]).total_seconds() / 3600
                for a, b in zip(observations, observations[1:])]
        if gaps and max(gaps) > MAX_GAP.total_seconds() / 3600:
            reasons.append(f"scoped observation gap is {max(gaps):.1f}h, maximum is 48h")

    open_findings: list[dict] = []
    closed_findings: list[dict] = []
    miss_count = 0
    gate_errors = 0
    for event_id, _index, row in after:
        if not finding(row):
            continue
        if row.get("missed_rules"):
            miss_count += 1
        if row.get("error"):
            gate_errors += 1
        disposition_entry = state["dispositions"].get(event_id)
        summary = {"event_id": event_id, "ts": row.get("ts"),
                   "session": row.get("session"),
                   "kind": "error" if row.get("error") else "miss"}
        if disposition_entry is None:
            open_findings.append(summary)
            continue
        disposition = disposition_entry[1]
        closed_findings.append({**summary,
                                "disposition": disposition["disposition"],
                                "owner": disposition["owner"],
                                "remedy_ref": disposition["remedy_ref"],
                                "evidence_ref": disposition["evidence_ref"],
                                "rollback_ref": disposition["rollback_ref"]})
        if disposition["disposition"] == "remediated":
            reasons.append(
                f"remediated finding {event_id} requires a new epoch after its remedy")
    if open_findings:
        reasons.append(f"{len(open_findings)} unresolved/unexplained finding(s) in current epoch")

    result = {"eligible": not reasons, "reasons": reasons,
              "epoch_id": epoch["record_id"], "epoch_started": epoch["ts"],
              "qualifying_observations": len(observations),
              "open_findings": len(open_findings),
              "closed_findings": len(closed_findings),
              "open": open_findings, "closed": closed_findings,
              "miss_count": miss_count, "gate_errors": gate_errors}
    if observations:
        first, last = observations[0][0], observations[-1][0]
        result.update({"window_started": first.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "newest": last.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "window_hours": round((now-first).total_seconds()/3600, 3)})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--identity-json",
                        help="current policy/map/source identity from a sanctioned reader")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows, unreadable = load(args.log)
    identity = json.loads(args.identity_json) if args.identity_json else None
    result = evaluate(rows, identity=identity)
    if unreadable:
        result["reasons"].append(f"{unreadable} unreadable telemetry line(s)")
        result["eligible"] = False
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["eligible"]:
        print("rule-delivery-shadow-eligibility: ELIGIBLE — "
              f"{result['window_hours']:.1f}h, {result['qualifying_observations']} "
              f"scoped observations, {result['closed_findings']} explained finding(s)")
    else:
        print("rule-delivery-shadow-eligibility: NOT ELIGIBLE")
        for reason in result["reasons"]:
            print("  " + reason)
        for finding_row in result["open"]:
            print(f"  OPEN {finding_row['kind']} event={finding_row['event_id']} "
                  f"ts={finding_row['ts']} session={finding_row['session']}")
        for finding_row in result["closed"]:
            print(f"  CLOSED {finding_row['kind']} event={finding_row['event_id']} "
                  f"disposition={finding_row['disposition']} owner={finding_row['owner']} "
                  f"remedy={finding_row['remedy_ref']}")
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
