#!/usr/bin/env python3
"""Plan Jev duplicate consolidation; apply a reviewed snapshot only after merge."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import record_call  # noqa: E402
from tools.jev_outage_health import INCIDENT_IDENTITY, _body  # noqa: E402

QUERY = {"kind": "open_loop", "domain": "system", "status": "open",
         "search": "Jev outage remains open:", "limit": 300}
BODY = _body("state_unreadable")
FIELDS = ("loop_id", "number", "kind", "domain", "status", "owner", "body",
          "blocker_class", "blocker_detail", "source_note", "created_at", "version")


def call(name, payload):
    result = record_call.call_verb(name, payload)
    old_contract = (result.error == "need_number_or_id" or
                    result.error == "unregistered_operation_fields" and
                    result.reply.get("fields") == ["source_note"])
    if name == "read-loop" and "source_note" in payload and old_contract:
        return {"loop": None, "identity_lookup": "unavailable_before_merge"}
    if not result.ok or not isinstance(result.reply, dict) or result.reply.get("error"):
        raise RuntimeError(result.describe())
    return result.reply


def matches(row):
    return (row.get("kind") == "open_loop" and row.get("domain") == "system" and
            row.get("owner", "").lower() == "claude" and row.get("blocker_class") == "other_lane" and
            row.get("body", row.get("label")) == BODY)


def snapshot(row):
    tagged = (row.get("source_note") == INCIDENT_IDENTITY and row.get("kind") == "open_loop" and
              row.get("domain") == "system")
    if not (matches(row) or tagged) or not row.get("loop_id") or not row.get("created_at") or not row.get("version"):
        raise ValueError("Jev candidate changed or lacks a complete record")
    if row.get("source_note") not in (None, INCIDENT_IDENTITY):
        raise ValueError("Jev candidate already carries another source")
    return {key: row.get(key) for key in FIELDS}


def collect(verb):
    board = verb("loop-board", QUERY)
    if board["count"] >= QUERY["limit"] or board["count"] != len(board["loops"]):
        raise ValueError("loop-board selection may be truncated")
    rows = [snapshot(verb("read-loop", {"number": row["number"], "kind": "open_loop"})["loop"])
            for row in board["loops"] if matches(row)]
    identity_read = verb("read-loop", {"source_note": INCIDENT_IDENTITY})
    active = identity_read["loop"]
    selected = len(rows)
    if active and active["loop_id"] not in {row["loop_id"] for row in rows}:
        rows.append(snapshot(active))
    if not rows or any(row["status"] != "open" for row in rows):
        raise ValueError("no complete open Jev incident selection")
    tagged = [row for row in rows if row["source_note"] == INCIDENT_IDENTITY]
    if len(tagged) > 1:
        raise ValueError("multiple tagged incidents require a fresh review")
    canonical = tagged[0] if tagged else min(rows, key=lambda row: (row["created_at"], int(row["number"]), row["loop_id"]))
    return {"identity": INCIDENT_IDENTITY, "query": QUERY,
            "identity_lookup": identity_read.get("identity_lookup", "supported"),
            "canonical": canonical, "superseded": [row for row in rows if row != canonical],
            "counts": {"matched": len(rows), "retain": 1, "supersede": len(rows) - 1,
                       "excluded": board["count"] - selected}}


def outcome(canonical):
    return (f"SUPERSEDED by Jev outage loop #{canonical['number']} ({canonical['loop_id']}). "
            "Duplicate created without durable incident identity. The canonical loop carries "
            "the unresolved outage; this row and its history remain preserved.")


def key(row, purpose):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{INCIDENT_IDENTITY}:{purpose}:{row['loop_id']}:{row['version']}"))


def verify_merge(plan):
    def run(*args):
        return subprocess.check_output(args, cwd=ROOT, text=True).strip()
    if run("git", "rev-parse", "HEAD") != plan["source_commit"]:
        raise ValueError("apply requires the exact reviewed source checkout")
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "lib/repair_loop.py",
                    "tools/jev_outage_health.py", "mcp-server/src/tools.js", __file__],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    pr = json.loads(run("gh", "pr", "view", plan["source_branch"], "--repo", "jbookout/carr-system",
                        "--json", "state,headRefOid"))
    if pr["state"] != "MERGED" or pr["headRefOid"] != plan["source_commit"]:
        raise ValueError("apply is refused until this exact PR head is merged")


def apply(plan, verb, merged=verify_merge):
    merged(plan)
    canonical = snapshot(plan["canonical"])
    duplicates = [snapshot(row) for row in plan["superseded"]]
    if any(not matches(row) for row in duplicates):
        raise ValueError("only exact generated duplicate rows may be superseded")
    rows = [canonical, *duplicates]
    if plan["identity"] != INCIDENT_IDENTITY or len({row["loop_id"] for row in rows}) != len(rows):
        raise ValueError("invalid consolidation identity or duplicate targets")
    identity_read = verb("read-loop", {"source_note": INCIDENT_IDENTITY})
    if identity_read.get("identity_lookup") == "unavailable_before_merge":
        raise ValueError("deploy the reviewed incident identity contract before applying")
    active = identity_read["loop"]
    if active and active["loop_id"] != canonical["loop_id"]:
        raise ValueError("another canonical incident exists; regenerate and review the plan")
    current = [verb("read-loop", {"loop_id": row["loop_id"]})["loop"] for row in rows]
    for expected, observed in zip(rows, current):
        if expected == canonical and observed.get("source_note") == INCIDENT_IDENTITY:
            expected = dict(expected, source_note=INCIDENT_IDENTITY,
                            version=expected["version"] + (expected["source_note"] != INCIDENT_IDENTITY))
        elif expected != canonical and observed.get("status") == "dropped" and observed.get("close_outcome") == outcome(canonical):
            continue
        if snapshot(observed) != expected:
            raise ValueError(f"Jev loop #{expected['number']} changed; regenerate and review the plan")
    if current[0]["source_note"] != INCIDENT_IDENTITY:
        reply = verb("update-loop", {"loop_id": canonical["loop_id"], "base_version": canonical["version"],
                     "source_note": INCIDENT_IDENTITY, "idempotency_key": key(canonical, "adopt")})
        if not reply.get("ok"):
            raise RuntimeError("canonical adoption failed; read records before retrying")
    for row, observed in zip(duplicates, current[1:]):
        if observed["status"] == "dropped":
            continue
        reply = verb("close-loop", {"loop_id": row["loop_id"], "base_version": row["version"],
                     "resolution": "dropped", "outcome": outcome(canonical),
                     "successor_loop": canonical["loop_id"], "idempotency_key": key(row, "supersede")})
        if not reply.get("ok"):
            raise RuntimeError("supersession failed; read records before retrying")
    for row in duplicates:
        observed = verb("read-loop", {"loop_id": row["loop_id"]})["loop"]
        if observed["status"] != "dropped" or observed.get("close_outcome") != outcome(canonical):
            raise RuntimeError("supersession readback failed")
    remaining = collect(verb)
    if remaining["counts"]["matched"] != 1 or remaining["canonical"]["loop_id"] != canonical["loop_id"]:
        raise RuntimeError("additional duplicates remain; produce a new dry-run plan")
    return {"retained": canonical["number"], "superseded": len(duplicates), "verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", type=Path, metavar="REVIEWED_PLAN")
    args = parser.parse_args()
    if args.apply:
        result = apply(json.loads(args.apply.read_text()), call)
    else:
        result = collect(call)
        result.update(observed_at=datetime.now(ZoneInfo("America/Chicago")).isoformat(),
                      source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                      source_branch=subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
