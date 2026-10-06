#!/usr/bin/env python3
"""Sanctioned append-only dispositions and epoch starts for shadow evidence."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_shadow import (  # noqa:E402
    append_locked, can_start_epoch, current_identity, finding, inspect, locked_read,
    make_disposition, make_epoch, read_jsonl_handle, validate_epoch_append,
)

DEFAULT_LOG = REPO / "out/rule-delivery-shadow.jsonl"


class LedgerRefusal(RuntimeError):
    """A fixed, non-secret-bearing refusal code safe for stderr."""


def refuse(code: str) -> None:
    raise LedgerRefusal(code)


def append(path: Path, build) -> dict:
    return append_locked(path, build)


def routine_dsn() -> str:
    value = os.environ.get("CARR_DB_JOBS_URL", "").strip()
    if not value:
        refuse("jobs-credential-required")
    login = unquote(urlsplit(value).username or "").strip().lower()
    if login in {"carr_writer", "carr_owner", "owner", "writer", "postgres"}:
        refuse("least-privilege-jobs-credential-required")
    return value


def live_identity() -> dict:
    import psycopg

    with psycopg.connect(routine_dsn()) as conn, conn.cursor() as cur:
        cur.execute("begin transaction read only")
        cur.execute("select session_user,current_user")
        role = cur.fetchone()
        if not role or {str(value) for value in role} != {"carr_jobs"}:
            refuse("jobs-role-verification-failed")
        cur.execute("""select mode,changed_by,reason,changed_at
                         from ops.rule_delivery_policy where singleton""")
        policy = cur.fetchone()
        identity = epoch_identity(policy)
        conn.rollback()
        return identity


def epoch_identity(policy) -> dict:
    if not policy or policy[0] != "shadow":
        refuse("live-policy-is-not-shadow")
    return current_identity(REPO, policy)


def add_disposition(path: Path, args) -> dict:
    def build(rows: list[dict]) -> dict:
        state = inspect(rows)
        if state["errors"]:
            refuse("ledger-validation-failed")
        observed = state["observations"].get(args.event_id)
        if observed is None:
            refuse("unknown-observation-event-id")
        if not finding(observed[1]):
            refuse("observation-is-not-a-finding")
        if args.event_id in state["dispositions"]:
            refuse("duplicate-disposition")
        return make_disposition(
            args.event_id, args.disposition, owner=args.owner,
            remedy_ref=args.remedy_ref, evidence_ref=args.evidence_ref,
            rollback_ref=args.rollback_ref)
    return append(path, build)


def start_epoch(path: Path, args) -> dict:
    identity = live_identity()

    def build(rows: list[dict]) -> dict:
        allowed, reason = can_start_epoch(rows, identity)
        if not allowed:
            refuse("epoch-precondition-failed")
        epoch = make_epoch(identity, owner=args.owner, reason=args.reason,
                           remedy_ref=args.remedy_ref, rollback_ref=args.rollback_ref)
        validate_epoch_append(rows, epoch)
        return epoch
    return append(path, build)


def list_findings(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        rows, bad = read_jsonl_handle(handle)
    if bad:
        refuse("unreadable-ledger")
    state = inspect(rows)
    if state["errors"]:
        refuse("ledger-validation-failed")
    result = []
    for event_id, (_index, row) in state["observations"].items():
        if finding(row):
            disp = state["dispositions"].get(event_id)
            result.append({"event_id": event_id, "ts": row.get("ts"),
                           "session": row.get("session"),
                           "missed_rules": row.get("missed_rules", []),
                           "error": row.get("error"),
                           "disposition": disp[1] if disp else None})
    return result


def fields(parser: argparse.ArgumentParser, *, evidence: bool = False) -> None:
    parser.add_argument("--owner", required=True)
    parser.add_argument("--remedy-ref", required=True)
    parser.add_argument("--rollback-ref", required=True)
    if evidence:
        parser.add_argument("--evidence-ref", required=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-findings")
    disposition = commands.add_parser("disposition")
    disposition.add_argument("--event-id", required=True)
    disposition.add_argument("--disposition", choices=("explained", "remediated"),
                             required=True)
    fields(disposition, evidence=True)
    epoch = commands.add_parser("start-epoch")
    epoch.add_argument("--reason", required=True)
    fields(epoch)
    args = parser.parse_args()
    result: Any
    try:
        if args.command == "list-findings":
            result = list_findings(args.log)
        elif args.command == "disposition":
            result = add_disposition(args.log, args)
        else:
            result = start_epoch(args.log, args)
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    except LedgerRefusal as exc:
        print(f"rule-delivery-shadow-ledger: REFUSED — {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("rule-delivery-shadow-ledger: REFUSED — local-evidence-io-failed",
              file=sys.stderr)
        return 1
    except Exception:
        print("rule-delivery-shadow-ledger: REFUSED — database-or-authority-check-failed",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
