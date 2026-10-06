#!/usr/bin/env python3
"""cutover-watch.py — the query-and-report half of the launchd cutover watch.

WHY THIS EXISTS (2026-08-27). Session-scheduled wakeups only fire while a
Claude conversation is alive and idle. The control plane worked all day
2026-08-27 while nothing reported it; Joe had to ask twice. Rule 1f3a7372: an
unattended run logs its findings to the record before it ends. Rule 847f9995:
no unattended path may depend on an interactive credential. This is the
observer that closes that gap without depending on a live session at all —
bin/cutover-watch.sh (invoked hourly-scale by launchd through
bin/run-scheduled.sh) loads only the narrow jobs-ledger credential and hands
off to this script.

WHAT IT READS, under CARR_DB_JOBS_URL alone (the narrow routine credential —
see bin/routine-credential-env.sh; never db.env, never an authority DSN):
  (a) completed jobs (ops.job.state='succeeded') whose completion receipt
      (ops.job_receipt.kind='completion') has no matching 'accepted' row in
      ops.workflow_acceptance for that workflow key/version/mode;
  (b) jobs currently ops.job.state='dead_lettered';
  (c) the full current ops.workflow_acceptance table (context for the note).

WHAT IT DOES WITH THAT. Builds a SNAPSHOT — the set of (a) and the set of
(b), by identity, not by count — and diffs it against the sentinel this same
script wrote the last time it ran (bin/cutover-watch.sh passes --sentinel;
default out/cutover-watch/last-report.json). A run where the sets agree is
silent: nothing prints past the one NOCHANGE status line, and the sentinel is
still refreshed so the next run's "since last time" stays accurate. A run
where they differ — a receipt newly lacking acceptance, a job newly
dead-lettered, or a previously-pending receipt that cleared — composes ONE
summary and writes it as one loop update to open loop #532, through the SAME
call path every other bin/ script uses to reach a verb: a subprocess call to
run.sh call (see bin/calendar-eventkit-capture.sh and
ops/cutover-readiness.py's call_verb() for the two existing callers this
mirrors), never a second implementation of that path.

WHAT IT NEVER DOES. It never calls accept-workflow, disable-legacy-schedule,
promote-*, or any verb that accepts, promotes, disables or dispatches
anything. The only two verbs it ever calls are read-loop and update-loop,
both scoped to loop #532, and update-loop's own base_version guard means a
concurrent edit to that loop is a version_conflict this script treats as a
real failure — never retried, never silently overwritten (the record
layer's own house rule: version_conflict is a question for a human, not
something an unattended script resolves for itself). Acceptance is a
directed authority act (ops.record_workflow_acceptance requires Joe's own
authority session for a canary 'accepted' status) and stays in an attended
session; this script only reads and reports.

EXIT CODES, read by bin/cutover-watch.sh and passed straight through by
bin/run-scheduled.sh so a failure is durably recorded:
  0  clean — no change, or a change was composed and the loop update landed
  1  a real error — the ledger was unreachable, or a change was found but
     writing it to the record failed. Never improvises a file in that case;
     the caller prints the finding it could not land and stops.
  78 missing or misconfigured credential (CARR_DB_JOBS_URL absent, or the
     connection did not come back as the expected routine role)

    tools/cutover-watch.py --sentinel PATH
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib import record_call  # noqa: E402
DEFAULT_SENTINEL = REPO / "out" / "cutover-watch" / "last-report.json"
LOOP_NUMBER = "532"

EX_OK = 0
EX_ERROR = 1
EX_CONFIG = 78

# The one role the narrow jobs credential is ever provisioned as. Anything
# else means this script was handed a wider credential than the routine
# loader is supposed to produce — refuse before running a single query
# rather than trust the caller's env scoping alone.
ALLOWED_ROLE = "carr_jobs"

UNACCEPTED_SQL = """
    select j.definition_key as workflow_key, j.mode,
           j.definition_version, r.receipt_ref, r.created_at
      from ops.job j
      join ops.job_receipt r on r.job_id = j.id and r.kind = 'completion'
     where j.state = 'succeeded'
       and not exists (
         select 1 from ops.workflow_acceptance a
          where a.workflow_key = j.definition_key
            and a.workflow_version = j.definition_version
            and a.mode = j.mode
            and a.receipt_ref = r.receipt_ref
            and a.status = 'accepted')
     order by r.created_at desc
"""

DEAD_LETTER_SQL = """
    select j.id, j.definition_key as workflow_key, j.mode,
           j.ended_at, j.last_failure_class, j.last_failure_detail
      from ops.job j
     where j.state = 'dead_lettered'
     order by j.ended_at desc
"""

ACCEPTANCE_SQL = """
    select workflow_key, workflow_version, mode, status,
           receipt_ref, accepted_by, created_at
      from ops.workflow_acceptance
     order by created_at desc
"""


class ConfigError(RuntimeError):
    """CARR_DB_JOBS_URL is absent, malformed, or does not name the routine role."""


# ---------------------------------------------------------------------------
# pure functions — no DB, no subprocess. ops/cutover-watch-selftest.py
# exercises these directly, hermetically, without a live database.
# ---------------------------------------------------------------------------

def unaccepted_key(row: dict) -> str:
    return f"{row['workflow_key']}/{row['mode']}#{row['receipt_ref']}"


def dead_letter_key(row: dict) -> str:
    return str(row["id"])


def build_snapshot(unaccepted_rows, dead_letter_rows, acceptance_rows) -> dict:
    """The comparable shape of "what the ledger says right now". Set-based,
    not count-based, on purpose: a same-sized set that traded one member for
    another (one receipt accepted, a different one landed the same wake)
    must still read as a change, and a naive count would miss it."""
    return {
        "unaccepted": sorted({unaccepted_key(r) for r in unaccepted_rows}),
        "dead_letters": sorted({dead_letter_key(r) for r in dead_letter_rows}),
        "acceptance_rows": len(acceptance_rows),
    }


def diff_snapshot(old: dict | None, new: dict) -> list[str]:
    """[] means a quiet run: nothing worth speaking about. `old` is None on
    the very first run (no sentinel on disk yet) — the entire current state
    is then "new" relative to nothing ever having been reported, which is
    the correct read, not a bug: if the board is already clear on that first
    run, the diff is still correctly empty."""
    old = old or {}
    old_unaccepted = set(old.get("unaccepted") or [])
    old_dead = set(old.get("dead_letters") or [])
    new_unaccepted = set(new["unaccepted"])
    new_dead = set(new["dead_letters"])

    changes: list[str] = []

    newly_unaccepted = sorted(new_unaccepted - old_unaccepted)
    if newly_unaccepted:
        changes.append(
            f"{len(newly_unaccepted)} completed job(s) with a receipt not yet "
            f"accepted: {', '.join(newly_unaccepted)}")

    cleared = sorted(old_unaccepted - new_unaccepted)
    if cleared:
        changes.append(
            f"{len(cleared)} previously-unaccepted receipt(s) no longer pending "
            f"(accepted, or left the succeeded state): {', '.join(cleared)}")

    newly_dead = sorted(new_dead - old_dead)
    if newly_dead:
        changes.append(
            f"{len(newly_dead)} job(s) newly dead-lettered: {', '.join(newly_dead)}")

    return changes


# ---------------------------------------------------------------------------
# the DB half
# ---------------------------------------------------------------------------

def routine_dsn() -> str:
    value = os.environ.get("CARR_DB_JOBS_URL", "").strip()
    if not value:
        raise ConfigError("CARR_DB_JOBS_URL is required")
    login = unquote(urlsplit(value).username or "").strip().lower()
    if login and login != ALLOWED_ROLE:
        raise ConfigError(f"expected the {ALLOWED_ROLE} login, got '{login}'")
    return value


def fetch_state(dsn: str):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("begin transaction read only")
            cur.execute("select session_user, current_user")
            role_row = cur.fetchone()
            if role_row is None:
                conn.rollback()
                raise ConfigError("the ledger did not report a connected role")
            seen = {str(role_row["session_user"]), str(role_row["current_user"])}
            if seen != {ALLOWED_ROLE}:
                conn.rollback()
                raise ConfigError(f"connected as {sorted(seen)}, expected only ['{ALLOWED_ROLE}']")

            cur.execute(UNACCEPTED_SQL)
            unaccepted_rows = cur.fetchall()
            cur.execute(DEAD_LETTER_SQL)
            dead_letter_rows = cur.fetchall()
            cur.execute(ACCEPTANCE_SQL)
            acceptance_rows = cur.fetchall()
            conn.rollback()  # read-only throughout; nothing was ever written

    return unaccepted_rows, dead_letter_rows, acceptance_rows


# ---------------------------------------------------------------------------
# the record-write half — the SAME call path bin/calendar-eventkit-capture.sh
# and ops/cutover-readiness.py already use to reach a verb: a subprocess call
# to run.sh call, which runs tools/call-verb.py against the deployed Worker
# under this Mac's LOCAL_TOKENS bearer. No CARR_DB_JOBS_URL crosses into this
# subprocess — that credential answers what the ledger says, never who is
# allowed to write the record of it, and the two must not blur.
# ---------------------------------------------------------------------------

def call_verb(verb: str, args: dict):
    """Never raises — a verb call that fails is a finding, not a crash. Only an
    ok outcome is success: a refused write is reported, never accepted."""
    child_env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", ""),
                 "LANG": os.environ.get("LANG", "C")}
    result = record_call.call_verb(verb, args, env=child_env, timeout=60)
    return (True, result.reply) if result.ok else (False, result.describe())


def write_loop_update(changes: list[str]) -> tuple[bool, str]:
    ok, cur = call_verb("read-loop", {"number": LOOP_NUMBER})
    if not ok:
        return False, f"read-loop #{LOOP_NUMBER} failed: {cur}"
    loop = (cur or {}).get("loop") or {}
    base_version = loop.get("version")
    body = loop.get("body") or ""
    if base_version is None:
        return False, f"read-loop #{LOOP_NUMBER} returned no version: {cur}"

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    note = f"cutover-watch ({stamp}) — " + "; ".join(changes)
    new_body = (body + "\n\n" + note) if body else note

    ok, res = call_verb("update-loop", {
        "idempotency_key": str(uuid.uuid4()),
        "number": LOOP_NUMBER,
        "base_version": base_version,
        "body": new_body,
    })
    if not ok:
        return False, f"update-loop #{LOOP_NUMBER} failed: {res}"
    return True, f"loop #{LOOP_NUMBER} updated (was version {base_version})"


# ---------------------------------------------------------------------------
# sentinel I/O
# ---------------------------------------------------------------------------

def load_sentinel(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def save_sentinel(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    tmp.replace(path)


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sentinel", default=str(DEFAULT_SENTINEL))
    args = ap.parse_args()
    sentinel_path = Path(args.sentinel)

    try:
        dsn = routine_dsn()
        unaccepted_rows, dead_letter_rows, acceptance_rows = fetch_state(dsn)
    except ConfigError as exc:
        print(f"STATUS: FAIL config — {exc}")
        return EX_CONFIG
    except Exception as exc:  # noqa: BLE001 — the ledger being unreachable IS the finding
        print(f"STATUS: FAIL ledger unreachable — {type(exc).__name__}: {exc}")
        return EX_ERROR

    new_snapshot = build_snapshot(unaccepted_rows, dead_letter_rows, acceptance_rows)
    old_snapshot = load_sentinel(sentinel_path)
    changes = diff_snapshot(old_snapshot, new_snapshot)

    if not changes:
        save_sentinel(sentinel_path, new_snapshot)
        print(f"STATUS: NOCHANGE unaccepted={len(new_snapshot['unaccepted'])} "
              f"dead_letters={len(new_snapshot['dead_letters'])} "
              f"acceptance_rows={new_snapshot['acceptance_rows']}")
        return EX_OK

    ok, detail = write_loop_update(changes)
    if not ok:
        # The rule: if the record layer is unreachable, print the finding and
        # exit nonzero. The sentinel is deliberately NOT written here — an
        # un-recorded finding must be retried next wake, never marked as
        # already reported.
        print("STATUS: FAIL record-write — " + detail)
        for c in changes:
            print("  finding: " + c)
        return EX_ERROR

    save_sentinel(sentinel_path, new_snapshot)
    print("STATUS: CHANGE " + "; ".join(changes) + " — " + detail)
    return EX_OK


if __name__ == "__main__":
    sys.exit(main())
