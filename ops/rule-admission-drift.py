#!/usr/bin/env python3
"""Nightly watch: does every active rule still carry an admission contract?

WHY THIS EXISTS (2026-08-23). ops/rule-admission-audit.py is the whole of the
control-plane roadmap's Phase 1 exit condition, and until today nothing had ever
run it against Production. Run there it read 218 active rules with 4 admitted
and 214 carrying no contract at all. bin/sync-rule-admission-prod.sh now installs
the reviewed contract and reports the same numbers when a human runs it bare —
but a door only reports when someone opens it, and the gap reopens on its own:
the rule about autonomous finalization of planned delivery work was taught on
2026-08-21, sat outside the reviewed enforcement map, and nothing noticed for two
days. Every rule taught from now on reopens it the day it lands.

WHAT IT MAY READ. It connects with CARR_DB_JOBS_URL, the routine-scoped role the
unattended boundary already carries, opens a READ ONLY transaction before issuing
anything, and asserts it is running as carr_jobs afterwards. Migration 0285
grants that role exactly two columns of `rule` — id and status — plus the
ops.rule_admission select it already had. It can count active rules and it cannot
read one: an unattended job that never needed the doctrine text should not carry
a credential that can fetch it.

EXIT CONTRACT, the same one its sibling the job monitor and
the checks around it in bin/nightly.sh use:
  78  no routine credential configured — a SKIP, not a failed night
  0   ran; a gap is PRINTED, not raised, because it is a prompt for a human and
      a step that fails every night until someone acts trains people to stop
      reading the alarm
  1   the watch itself could not run — a wrong role, a missing grant
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

EX_CONFIG = 78


def load_audit():
    """Import the audit, whose filename is not an importable module name."""
    path = REPO / "ops" / "rule-admission-audit.py"
    spec = importlib.util.spec_from_file_location("rule_admission_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the admission audit from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def routine_dsn() -> str | None:
    value = os.environ.get("CARR_DB_JOBS_URL", "").strip()
    if not value:
        return None
    try:
        login = unquote(urlsplit(value).username or "").strip().lower()
    except ValueError:
        login = ""
    if login in {"carr_writer", "carr_owner", "owner", "writer", "postgres"}:
        print("rule-admission-drift: CARR_DB_JOBS_URL names an owner or writer "
              "login; refusing to watch Production under it", file=sys.stderr)
        raise SystemExit(1)
    return value


def main() -> int:
    dsn = routine_dsn()
    if not dsn:
        print("rule-admission-drift: NOT CONFIGURED (no CARR_DB_JOBS_URL)", file=sys.stderr)
        return EX_CONFIG

    import psycopg

    audit = load_audit()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # BEGIN must be the first statement of the session; psycopg opens a
        # read-write transaction on the first query and a later BEGIN READ ONLY
        # does not retrofit it.
        cur.execute("begin transaction read only")
        cur.execute("select session_user, current_user")
        row = cur.fetchone()
        if not row or {str(v) for v in row} != {"carr_jobs"}:
            print(f"rule-admission-drift: not a provisioned jobs identity ({row}); "
                  "refusing to read Production", file=sys.stderr)
            return 1
        try:
            counts = audit.counts(cur)
        except psycopg.errors.InsufficientPrivilege as exc:
            print("rule-admission-drift: the jobs role cannot read what the audit "
                  f"needs — migration 0285 may not be applied here ({exc})",
                  file=sys.stderr)
            return 1

    if audit.failing(counts):
        print("rule-admission-drift: PRODUCTION'S ADMISSION CONTRACT IS INCOMPLETE — "
              + audit.render(counts))
        if counts["missing"]:
            print(f"  {counts['missing']} active rule(s) carry no admission row at all — "
                  "most often a rule taught since ops/config/rule-enforcement-map.json "
                  "was last extended.")
        if counts["needs_revision"]:
            print(f"  {counts['needs_revision']} admitted against a control that is not installed.")
        if counts["incomplete"]:
            print(f"  {counts['incomplete']} admitted without applicability, projection "
                  "or reachability recorded.")
        print("  Reconcile with ./bin/sync-rule-admission-prod.sh (bare to read, "
              "--apply once the map covers every active rule).")
        return 0
    print("rule-admission-drift: every active rule carries a complete admission "
          "contract — " + audit.render(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
