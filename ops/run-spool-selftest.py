#!/usr/bin/env python3
"""ops/run-spool-selftest.py — the acceptance test for tools/ops-spool.py, the
local SQLite queue between high-frequency recorders and the Neon ledger.

WHAT THE SPOOL EXISTS TO HOLD (2026-08-18 audit): five launchd jobs recording
run rows every 120–600 seconds held the Neon database awake around the clock,
and a recording that failed was not retried — 3,485 rows were dropped during
the 2026-08-17/18 schema-drift outage because the recorder had nowhere durable
to put them. The spool queues locally and a scheduled flusher replays through
the REAL tools/ops-record.py (rule a8c55a47: the flusher must not grow a second
copy of ops-record's checks).

SAME NO-MOCK DISCIPLINE as ops/run-scheduled-selftest.py: every check drives
the real tools/ops-spool.py as a subprocess, against a throwaway spool file
(CARR_RUN_SPOOL_DB — the default lives under the shared out/, which is
production state) and, where a database is needed to FAIL, a loopback port that
refuses instantly. The deterministic-refusal check feeds the real ops-record a
row it really refuses (a failed state with no failure class, its own documented
rc=2), not a stub that pretends to.

WHAT IS DELIBERATELY NOT COVERED HERE: the full staging round trip — wrapper →
spool → flush → a real ops.run row read back — lives in
ops/run-scheduled-selftest.py tier 2, where the wrapper integration it proves
already lives. This file owns the queue mechanics.

RUN IT:
    python3 ops/run-spool-selftest.py
"""
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "lib"))
from selftest_harness import temporary_directory

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPOOL_TOOL = os.path.join(REPO, "tools", "ops-spool.py")

FAILED: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail="\n        {detail}", failures=FAILED)


def fresh_spool() -> str:
    return os.path.join(temporary_directory(prefix="carr-spool-selftest-"),
                        "spool.sqlite3")


def env_for(spool: str, reachable_db: bool = False) -> dict:
    env = dict(os.environ)
    env["CARR_RUN_SPOOL_DB"] = spool
    if not reachable_db:
        # ops-record's routine (run) connection reads ONLY CARR_DB_JOBS_URL,
        # and ~/.config/carr/db.env fills it via setdefault — so an explicit
        # value here wins over the real credential on a configured Mac. The
        # user must be carr_jobs (the jobs-DSN check refuses others before
        # connecting) and port 1 on loopback refuses instantly, so this is a
        # genuine attempted-and-refused connection, not a validation refusal.
        env["CARR_DB_JOBS_URL"] = "postgresql://carr_jobs@127.0.0.1:1/nothing"
        env["DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/nothing"
        for leak in ("CARR_DB_URL", "PGSERVICE"):
            env.pop(leak, None)
    return env


def run_tool(spool: str, *args: str, reachable_db: bool = False):
    return subprocess.run(
        [sys.executable, SPOOL_TOOL, *args],
        capture_output=True, text=True, timeout=300,
        env=env_for(spool, reachable_db), cwd=REPO)


def rows(spool: str, table: str = "spool") -> list:
    if not os.path.exists(spool):
        return []
    conn = sqlite3.connect(spool)
    try:
        return conn.execute(
            f"select service, run_key, state, argv, attempts from {table}"
            " order by id").fetchall()
    finally:
        conn.close()


HEARTBEAT_ARGS = ["--service", "carr-spool-selftest", "--key", "selftest.hb",
                  "--state", "succeeded", "--exit-code", "0",
                  "--source-kind", "wrapper",
                  "--source-ref", "ops/run-spool-selftest.py",
                  "--detail", "spool selftest heartbeat"]


def main() -> int:
    print("run-spool-selftest — a row handed to the spool is durable, ordered, "
          "and cannot wedge the queue\n")

    # ── enqueue: a heartbeat queues locally, touching no database ────────────
    spool = fresh_spool()
    proc = run_tool(spool, "run", *HEARTBEAT_ARGS)
    check("a succeeded row enqueues with exit 0 (no database anywhere near)",
          proc.returncode == 0, f"rc={proc.returncode} err={proc.stderr[:200]!r}")
    queued = rows(spool)
    check("...and the row is really in the queue",
          len(queued) == 1, repr(queued))
    if queued:
        service, run_key, state, argv, attempts = queued[0]
        check("...with service, key and state parsed for the queue's own index",
              (service, run_key, state)
              == ("carr-spool-selftest", "selftest.hb", "succeeded"),
              repr((service, run_key, state)))
        check("...and the argv stored VERBATIM — the flusher replays exactly "
              "what the recorder was asked, no reconstruction",
              json.loads(argv) == HEARTBEAT_ARGS, argv[:300])

    # ── a failed state falls back to the queue when direct write fails ───────
    fail_args = ["--service", "carr-spool-selftest", "--key", "selftest.down",
                 "--state", "failed", "--exit-code", "1",
                 "--failure-class", "selftest_down",
                 "--source-kind", "collector",
                 "--source-ref", "ops/run-spool-selftest.py",
                 "--detail", "selftest forced failure"]
    proc = run_tool(spool, "run", *fail_args)
    check("a FAILED row with the ledger unreachable still exits 0 — queued "
          "after the direct attempt, never dropped (the 2026-08-17 loss)",
          proc.returncode == 0, f"rc={proc.returncode} err={proc.stderr[:200]!r}")
    check("...the direct-first attempt is named in its output",
          "direct" in proc.stdout, proc.stdout[:200])
    check("...and the queue now holds both rows in arrival order",
          [r[1] for r in rows(spool)] == ["selftest.hb", "selftest.down"],
          repr(rows(spool)))

    # ── nothing durable anywhere is the ONLY failure ─────────────────────────
    broken = os.path.join(os.path.devnull, "spool.sqlite3")
    proc = run_tool(broken, "run", *HEARTBEAT_ARGS)
    check("an unwritable spool (and no database) exits nonzero — the caller "
          "must know this row is durable nowhere",
          proc.returncode != 0, f"rc={proc.returncode}")

    # ── flush: transient failure aborts and retains, in order ────────────────
    proc = run_tool(spool, "flush")
    check("a flush against an unreachable database exits nonzero",
          proc.returncode != 0, f"rc={proc.returncode} out={proc.stdout[:200]!r}")
    retained = rows(spool)
    check("...and retains every row for the next flush — an outage delays "
          "the ledger, it no longer edits it",
          [r[1] for r in retained] == ["selftest.hb", "selftest.down"],
          repr(retained))
    check("...counting the attempt on the row it stopped at",
          bool(retained) and retained[0][4] == 1, repr(retained))

    # ── flush: a deterministic refusal dead-letters instead of wedging ───────
    # The poison row is one the REAL ops-record refuses before ever touching a
    # database: a failed state with no failure class, its own documented rc=2.
    # It sits FIRST so the check also proves the queue keeps moving past it.
    spool2 = fresh_spool()
    poison = ["--service", "carr-spool-selftest", "--key", "selftest.poison",
              "--state", "failed", "--exit-code", "1",
              "--source-kind", "wrapper",
              "--source-ref", "ops/run-spool-selftest.py",
              "--detail", "no failure class on purpose"]
    conn = sqlite3.connect(spool2)  # planted directly: run would refuse it too
    conn.execute("""create table spool (id integer primary key autoincrement,
                    enqueued_at text not null, service text, run_key text,
                    state text, argv text not null,
                    attempts integer not null default 0, last_error text)""")
    conn.execute("insert into spool (enqueued_at, service, run_key, state, argv)"
                 " values ('2026-08-18T00:00:00Z', 'carr-spool-selftest',"
                 " 'selftest.poison', 'failed', ?)", (json.dumps(poison),))
    conn.execute("insert into spool (enqueued_at, service, run_key, state, argv)"
                 " values ('2026-08-18T00:00:01Z', 'carr-spool-selftest',"
                 " 'selftest.hb2', 'succeeded', ?)",
                 (json.dumps(HEARTBEAT_ARGS),))
    conn.commit()
    conn.close()
    proc = run_tool(spool2, "flush")
    check("the poison row is dead-lettered, not retried forever",
          [r[1] for r in rows(spool2, "dead")] == ["selftest.poison"],
          repr(rows(spool2, "dead")))
    check("...its refusal reason is kept readable on the dead row",
          any("rc=2" in (r[3] or "") for r in
              sqlite3.connect(spool2).execute(
                  "select id, service, run_key, last_error from dead").fetchall()),
          repr(rows(spool2, "dead")))
    check("...and the queue moved PAST it to the next row (which then hit the "
          "unreachable database and was retained — abort, not loss)",
          [r[1] for r in rows(spool2)] == ["selftest.hb2"], repr(rows(spool2)))

    # ── flush: the empty queue is quiet ──────────────────────────────────────
    spool3 = fresh_spool()
    proc = run_tool(spool3, "flush")
    check("an empty queue flushes clean (exit 0)",
          proc.returncode == 0 and "empty" in proc.stdout,
          f"rc={proc.returncode} out={proc.stdout[:200]!r}")

    # ── two flushes cannot replay the same row twice ─────────────────────────
    lock_path = spool + ".lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        proc = run_tool(spool, "flush")
        check("a flush that finds the lock held stands down harmlessly "
              "(exit 0, no rows touched)",
              proc.returncode == 0 and "lock" in proc.stdout
              and len(rows(spool)) == 2,
              f"rc={proc.returncode} out={proc.stdout[:200]!r}")

    # ── status answers without a database ────────────────────────────────────
    proc = run_tool(spool, "status")
    check("status reports the queue without touching any database",
          proc.returncode == 0 and "queued: 2" in proc.stdout,
          f"rc={proc.returncode} out={proc.stdout[:200]!r}")

    # ── misuse is loud ───────────────────────────────────────────────────────
    proc = run_tool(spool, "run")
    check("run with no arguments is a usage error (64)",
          proc.returncode == 64, f"rc={proc.returncode}")
    proc = subprocess.run([sys.executable, SPOOL_TOOL, "not-a-mode"],
                          capture_output=True, text=True,
                          env=env_for(spool), cwd=REPO, timeout=60)
    check("an unknown mode is a usage error (64)",
          proc.returncode == 64, f"rc={proc.returncode}")

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} check(s):")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
