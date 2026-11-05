#!/usr/bin/env python3
"""ops-spool.py — a local SQLite spool between high-frequency recorders and Neon.

WHY THIS EXISTS, measured 2026-08-18. Five launchd jobs record run rows through
tools/ops-record.py at cadences of 120s to 600s. Each recording opens a
connection to the production Neon database, so the database never sits idle for
the five minutes autosuspend needs, and compute is billed around the clock to
carry heartbeats that say "still fine". The same audit found the sharper cost of
recording synchronously: when the recorder cannot land a row — unreachable
network, missing credential, or the 2026-08-17 schema drift where the deployed
recorder wrote a column production did not have yet — the row is NOT retried
later. It is a line in out/run-scheduled.log and nothing else. 3,485 rows were
lost that way in 36 hours. An observation channel that drops its observations
whenever the far end wobbles is only durable in fair weather.

THE MOVE: record locally, flush in batches. `ops-spool.py run <args>` accepts
exactly the argument list `ops-record.py run` accepts and appends it to a local
SQLite queue — no network, no credential, a few milliseconds. A single scheduled
flusher (`ops-spool.py flush`, com.carr.run-spool-flush, every 30 min) replays
the queue through the REAL tools/ops-record.py, one row at a time, in order.
Rule a8c55a47 is why the flusher replays through ops-record rather than
inserting itself: ops-record's registry lookup, failure-class rules, and expiry
derivation are the single check body, and a second writer would drift from it.

FAILURES DO NOT WAIT. A state of failed/timed_out/cancelled is tried against
ops-record DIRECTLY first, because the registry notes for partner-ping and
capture-poll lean on "every FAILURE is still recorded immediately", and `assess`
turns the latest failed run into incident state — an incident delayed 30 minutes
is 30 minutes nobody is looking. Only when that direct write itself fails does
the failure row fall back to the queue, which is strictly better than today,
where it fell on the floor. Succeeded and skipped rows always queue: a
heartbeat's whole message is "nothing happened", and it can afford to be up to
one flush interval old — the registered cadence graces were widened to say so
(ops/config/services.json, 2026-08-18).

WHAT "RECORDED" MEANS NOW, for every caller reading an exit code: 0 means the
row is DURABLE — either landed in ops.run directly or queued in the spool with a
scheduled path to ops.run. Nonzero means it is not durable anywhere and the
caller's log line is the only trace, which is exactly what nonzero meant before.
bin/run-scheduled.sh's stamp-only-on-recorder-success throttle rule therefore
carries over unchanged: a stamp means the row will reach the ledger, not that it
already has.

WHAT THE SPOOL DELIBERATELY DOES NOT DO: it never edits, reorders, merges,
throttles or interprets rows. Whatever argv arrives is what ops-record replays.
Timeliness decisions (the wrapper's --heartbeat-interval) and correctness
decisions (ops-record's own refusals) stay where they already live.

POISON ROWS CANNOT WEDGE THE QUEUE. ops-record's exit codes are the sort key:
  0        landed — delete from the queue.
  2, 64    deterministic refusal (bad args, constraint shape) — will never land
           however often it is retried; moved to the dead table, kept readable.
  78       configuration state (unregistered service, absent schema). Retried,
           because sync-registry or a migration resolves it — but rows for that
           service are skipped for the rest of the pass so one unregistered
           selftest probe cannot starve real services, and after MAX_ATTEMPTS
           the row goes to the dead table instead of retrying forever.
  other    transient (unreachable database, absent credential — ops-record
           returns 1 for both, measured). The pass ABORTS and everything left
           retries next flush, preserving per-service ordering: landing row k+1
           before row k would make "latest run" flap backwards at the next
           successful flush.

THE SPOOL FILE lives at out/run-spool.sqlite3 (override: CARR_RUN_SPOOL_DB —
out/ is shared with every worktree, so a test that does not override it writes
production state; the same lesson CARR_RUN_SCHEDULED_STATE_DIR already
encodes). WAL mode so five launchd jobs enqueueing at once never block each
other; a flock alongside the file so two overlapping flushes cannot replay the
same row twice.

RUN IT:
    tools/ops-spool.py run --service partner-ping --key launchd.run ...
    tools/ops-spool.py flush
    tools/ops-spool.py status
"""

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPS_RECORD = os.path.join(REPO, "tools", "ops-record.py")

EX_USAGE = 64

# States whose row is an incident trigger, not a heartbeat. Tried directly
# first; queued only when the direct write fails.
DIRECT_STATES = {"failed", "timed_out", "cancelled"}

# A 78 row (unregistered service / absent schema) retries this many flushes
# before it stops pretending someone will register it.
MAX_ATTEMPTS = 10

# One replay may not hang the whole flush window.
REPLAY_TIMEOUT = 120


def spool_path() -> str:
    return os.environ.get("CARR_RUN_SPOOL_DB") or os.path.join(
        REPO, "out", "run-spool.sqlite3")


def open_spool() -> sqlite3.Connection:
    path = spool_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma busy_timeout=10000")
    conn.execute(
        """create table if not exists spool (
               id integer primary key autoincrement,
               enqueued_at text not null,
               service text,
               run_key text,
               state text,
               argv text not null,
               attempts integer not null default 0,
               last_error text)""")
    conn.execute(
        """create table if not exists dead (
               id integer primary key,
               enqueued_at text,
               dead_at text not null,
               service text,
               run_key text,
               state text,
               argv text not null,
               attempts integer,
               last_error text)""")
    conn.commit()
    return conn


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def flag_value(args: list[str], flag: str) -> str:
    """The value following a flag, or '' — no argparse, because this tool must
    accept whatever ops-record accepts today and tomorrow without keeping a
    copy of its surface (the drift rule a8c55a47 again, at the CLI layer)."""
    for i, a in enumerate(args[:-1]):
        if a == flag:
            return args[i + 1]
    return ""


def python_for_replay() -> str:
    py = os.path.join(REPO, ".venv", "bin", "python")
    return py if os.access(py, os.X_OK) else sys.executable


def replay(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [python_for_replay(), OPS_RECORD, "run", *args],
        capture_output=True, text=True, timeout=REPLAY_TIMEOUT)


# Raw recorder stderr can contain driver SQL, credentials or caller detail.
# Preserve only complete, known static diagnostics, never interpolated values.
SAFE_RECORDER_ERRORS = (
    "ops-record: a failed run must name its failure class (--failure-class)",
    "ops-record: the ops schema is not on this database — "
    "migration 0115 is unapplied here. Nothing recorded.",
    "ops-record: incident fingerprint columns and the recovery "
    "function are missing — migration 0293 is unapplied here. Nothing recorded.",
)
STDERR_LIMIT = 500


def recorder_stderr(stderr: str) -> str:
    lines = stderr.splitlines()
    safe = [line for line in lines if line in SAFE_RECORDER_ERRORS]
    if any(line.startswith("ops-record: no service registered with key ")
           for line in lines):
        safe.append("ops-record: no service registered with requested key; "
                    "check ops/config/services.json and sync-registry")
    if any(line not in SAFE_RECORDER_ERRORS for line in lines):
        safe.append("[other recorder stderr redacted]")
    return " | ".join(safe)[:STDERR_LIMIT]


def enqueue(conn: sqlite3.Connection, args: list[str], note: str = "") -> None:
    conn.execute(
        "insert into spool (enqueued_at, service, run_key, state, argv, last_error)"
        " values (?,?,?,?,?,?)",
        (utcnow(), flag_value(args, "--service"), flag_value(args, "--key"),
         flag_value(args, "--state"), json.dumps(args), note or None))
    conn.commit()


def cmd_run(args: list[str]) -> int:
    if not args:
        print("usage: ops-spool.py run <ops-record run arguments>", file=sys.stderr)
        return EX_USAGE

    service = flag_value(args, "--service")
    key = flag_value(args, "--key")
    state = flag_value(args, "--state")

    if state in DIRECT_STATES:
        # A failure wants to be an incident NOW. Falls back to the queue only
        # when the ledger cannot be reached, which is where today's behavior
        # loses the row outright.
        try:
            direct = replay(args)
        except subprocess.TimeoutExpired:
            direct = None
        if direct is not None and direct.returncode == 0:
            sys.stdout.write(direct.stdout)
            sys.stderr.write(direct.stderr)
            print(f"ops-spool: recorded direct: {service}/{key} state={state}")
            return 0
        detail = (f"direct rc={direct.returncode}: {recorder_stderr(direct.stderr)}" if direct is not None
                  else "direct timed out")

        try:
            conn = open_spool()
            enqueue(conn, args, note=f"queued after failed direct write ({detail})")
            conn.close()
        except Exception as e:
            print(f"ops-spool: could not record OR queue {service}/{key}: "
                  f"{detail}; spool: {e}", file=sys.stderr)
            return 1
        print(f"ops-spool: queued (deferred) after failed direct write: "
              f"{service}/{key} state={state} ({detail})")
        return 0

    try:
        conn = open_spool()
        enqueue(conn, args)
        conn.close()
    except Exception as e:
        print(f"ops-spool: could not queue {service}/{key}: {e}", file=sys.stderr)
        return 1
    print(f"ops-spool: queued: {service}/{key} state={state or '?'}")
    return 0


def cmd_flush() -> int:
    lock_file = spool_path() + ".lock"
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    lock = open(lock_file, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Not a failure: the queue is being drained, just not by us. Two
        # flushes replaying the same rows would double-record every one.
        print("ops-spool: another flush holds the lock — nothing to do")
        return 0

    try:
        conn = open_spool()
        rows = conn.execute(
            "select id, service, run_key, argv, attempts from spool order by id"
        ).fetchall()
        if not rows:
            print("ops-spool: queue empty")
            return 0

        landed = deadened = skipped = 0
        aborted = None
        skip_services: set[str] = set()
        for row_id, service, run_key, argv_json, attempts in rows:
            if service in skip_services:
                skipped += 1
                continue
            args = json.loads(argv_json)
            try:
                proc = replay(args)
                rc, err = proc.returncode, recorder_stderr(proc.stderr)
            except subprocess.TimeoutExpired:
                rc, err = -1, "replay timed out"

            if rc == 0:
                conn.execute("delete from spool where id = ?", (row_id,))
                conn.commit()
                landed += 1
            elif rc in (2, EX_USAGE):
                conn.execute(
                    """insert into dead (id, enqueued_at, dead_at, service,
                                         run_key, state, argv, attempts, last_error)
                       select id, enqueued_at, ?, service, run_key, state, argv,
                              attempts + 1, ? from spool where id = ?""",
                    (utcnow(), f"rc={rc}: {err}", row_id))
                conn.execute("delete from spool where id = ?", (row_id,))
                conn.commit()
                deadened += 1
            elif rc == 78:
                if attempts + 1 >= MAX_ATTEMPTS:
                    conn.execute(
                        """insert into dead (id, enqueued_at, dead_at, service,
                                             run_key, state, argv, attempts, last_error)
                           select id, enqueued_at, ?, service, run_key, state, argv,
                                  attempts + 1, ? from spool where id = ?""",
                        (utcnow(), f"rc=78 after {attempts + 1} attempts: {err}",
                         row_id))
                    conn.execute("delete from spool where id = ?", (row_id,))
                    deadened += 1
                else:
                    conn.execute(
                        "update spool set attempts = attempts + 1, last_error = ?"
                        " where id = ?", (f"rc=78: {err}", row_id))
                    skip_services.add(service)
                    skipped += 1
                conn.commit()
            else:
                conn.execute(
                    "update spool set attempts = attempts + 1, last_error = ?"
                    " where id = ?", (f"rc={rc}: {err}", row_id))
                conn.commit()
                aborted = f"{service}/{run_key} rc={rc}: {err}"
                break

        remaining = conn.execute("select count(*) from spool").fetchone()[0]
        conn.close()
        summary = (f"ops-spool: flushed {landed}, dead-lettered {deadened}, "
                   f"skipped {skipped}, remaining {remaining}")
        if aborted:
            print(f"{summary} — ABORTED on transient error at {aborted}",
                  file=sys.stderr)
            return 1
        print(summary)
        return 0
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def cmd_status() -> int:
    conn = open_spool()
    queued = conn.execute("select count(*) from spool").fetchone()[0]
    oldest = conn.execute("select min(enqueued_at) from spool").fetchone()[0]
    dead = conn.execute("select count(*) from dead").fetchone()[0]
    by_service = conn.execute(
        "select service, count(*) from spool group by service order by 2 desc"
    ).fetchall()
    conn.close()
    print(f"queued: {queued}  (oldest: {oldest or '—'})   dead: {dead}")
    for service, n in by_service:
        print(f"  {service or '?':<24} {n}")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("run", "flush", "status"):
        print("usage: ops-spool.py run <ops-record-run-args> | flush | status",
              file=sys.stderr)
        return EX_USAGE
    if sys.argv[1] == "run":
        return cmd_run(sys.argv[2:])
    if sys.argv[1] == "flush":
        return cmd_flush()
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
