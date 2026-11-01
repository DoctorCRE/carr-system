#!/usr/bin/env python3
"""
program3-trace-gate.py — THE ACCEPTANCE TEST FOR PROGRAM 3, written before the
thing it tests (rule e65efc68).

THE GATE, in the roadmap's own words: "one traceable view explains a failed
critical journey without terminal archaeology."

That sentence is not directly executable, so this file is the executable form of
it, and it is deliberately the FIRST artifact of Program 3. Everything the
migration builds exists to make these five assertions pass. If a later change
makes any of them pass for the wrong reason, the assertion is wrong and gets
fixed here — never loosened to match the code.

WHAT "WITHOUT TERMINAL ARCHAEOLOGY" MEANS, MECHANICALLY. Today the answer to
"why did last night's chain fail" lives in out/nightly.log, a text file on one
Mac, readable only by a human with a shell on that Mac, holding no environment,
no correlation to the deploy that preceded it, and no link to the incident it
caused. The gate is met when ONE query, against the record layer, returns that
whole chain. So assertion 1 is literally: one query, one correlation id, every
link in the journey.

THE FIVE ASSERTIONS.

  1. ONE QUERY RETURNS THE WHOLE CHAIN. A single select on ops.v_trace, keyed by
     one correlation id, returns every link of a failed journey — the deployment
     that preceded it, the synthetic golden-workflow check that caught it, the
     job run that failed, and the incident raised from it — in time order.

  2. EVERY ROW NAMES ITS SOURCE AND ITS FRESHNESS. Not one row in that chain may
     report a state without also reporting where the state came from and how old
     it is. A number with no provenance is the thing this program exists to
     replace.

  3. A FAILURE NAMES ITS CLASS. A run that failed and cannot say what class of
     failure it was is not an observation, it is a shrug. The database refuses
     it.

  4. A SILENT COLLECTOR READS UNKNOWN, NEVER LAST-GREEN. This is the premortem's
     second-most-likely catastrophe in the Control Room BDUF ("false health from
     stale collectors") and the risk register's named proof: "Collector-off
     fixture renders Unknown and alert". A service whose last observation has
     aged past its cadence must read unknown/stale — the previous success must
     NOT survive as current health.

  5. THE READ ROLE CANNOT WRITE. Program 3's Control Room foundation is
     READ-ONLY by phase rule ("no mutation routes exist"). The database half of
     that is least privilege: carr_reader may select and may not insert.

WHERE IT RUNS. Against any database whose DSN is in DATABASE_URL. Intended
target is the isolated staging project:

    .venv/bin/python tools/db-tap.py --project staging run ops/program3-trace-gate.py

EVERY ROW IT WRITES IS ROLLED BACK. The test seeds a complete failed journey and
then aborts the transaction, so it can run against a live database as often as
you like without leaving residue. Nothing here is a fixture file that can drift
from the schema; the seed IS the schema's contract, exercised.
"""

# ci: db-gate — ops/ci.sh runs this against the throwaway Postgres in its
# migration class. Remove the marker only with a reason; an unrun acceptance
# gate is a document with assertions in it.

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.pgrow import fetch_one  # noqa: E402



try:
    import psycopg
except ImportError:
    sys.exit("program3-trace-gate: psycopg not installed (pip install 'psycopg[binary]')")

FAILURES: list[str] = []
PASSES: list[str] = []


from lib.gate_check import Check  # noqa: E402

check = Check(failures=FAILURES, passes=PASSES)


def refuses(cur, sql: str, params: tuple, name: str) -> None:
    """Assert the database REFUSES a write. A constraint nobody has watched bite
    is a comment with punctuation (the house rule from migration 0114)."""
    cur.execute("savepoint s")
    try:
        cur.execute(sql, params)
    except psycopg.errors.Error:
        cur.execute("rollback to savepoint s")
        check(name, True)
        return
    cur.execute("rollback to savepoint s")
    check(name, False, "the write was ACCEPTED")


def chain_in_time_order(chain):
    return all(
        chain[i][3].astimezone(timezone.utc) <= chain[i + 1][3].astimezone(timezone.utc)
        for i in range(len(chain) - 1)
    )


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("program3-trace-gate: DATABASE_URL is not set")

    corr = uuid.uuid4()
    now = datetime.now(timezone.utc)
    env = "staging"

    print(f"program3-trace-gate: correlation {corr}")
    print("  (every row below is rolled back before exit)\n")

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        # ── seed one complete failed critical journey ────────────────────────
        # A deploy, then a golden-workflow check that fails, then the job run it
        # explains, then the incident. One correlation id threads all four.
        cur.execute(
            """insert into ops.service (key, name, family, criticality, owner_actor)
               values ('gate-probe', 'Program 3 gate probe', 'Data', 'high', 'joe')
               returning id""")
        service_id = fetch_one(cur)[0]

        cur.execute(
            """insert into ops.service_environment
                   (service_id, environment, expected_cadence_seconds)
               values (%s, %s, 86400)""",
            (service_id, env))

        cur.execute(
            """insert into ops.deployment
                   (correlation_id, service_id, environment, state, git_sha,
                    started_at, ended_at, read_back_at,
                    source_kind, source_ref, observed_at, expires_at)
               values (%s, %s, %s, 'complete', %s, %s, %s, %s,
                       'wrapper', 'bin/deploy-worker.sh', %s, %s)""",
            (corr, service_id, env, "0" * 40,
             now - timedelta(minutes=30), now - timedelta(minutes=29),
             now - timedelta(minutes=28), now - timedelta(minutes=28),
             now + timedelta(days=30)))

        cur.execute(
            """insert into ops.run
                   (kind, correlation_id, service_id, environment, run_key, state,
                    failure_class, started_at, ended_at,
                    source_kind, source_ref, observed_at, expires_at)
               values ('check', %s, %s, %s, 'golden.record-lookup', 'failed',
                       'assertion_failed', %s, %s,
                       'collector', 'ops/golden-workflows.py', %s, %s)""",
            (corr, service_id, env,
             now - timedelta(minutes=10), now - timedelta(minutes=10),
             now - timedelta(minutes=10), now + timedelta(days=1)))

        cur.execute(
            """insert into ops.run
                   (kind, correlation_id, service_id, environment, run_key, state,
                    failure_class, exit_code, started_at, ended_at,
                    source_kind, source_ref, observed_at, expires_at)
               values ('job', %s, %s, %s, 'nightly.exports', 'failed',
                       'exporter_error', 1, %s, %s,
                       'wrapper', 'bin/nightly.sh', %s, %s)""",
            (corr, service_id, env,
             now - timedelta(minutes=9), now - timedelta(minutes=8),
             now - timedelta(minutes=8), now + timedelta(days=1)))

        cur.execute(
            """insert into ops.incident
                   (ref, correlation_id, title, severity, state, environment,
                    owner_actor, next_action, detected_source, detected_at,
                    source_kind, source_ref, observed_at, expires_at)
               values (%s, %s, 'Nightly export chain failed', 'SEV-2', 'triaged',
                       %s, 'joe', 'read the trace', 'golden-workflow', %s,
                       'collector', 'ops/golden-workflows.py', %s, %s)
               returning id""",
            (f"INC-GATE-{corr.hex[:8]}", corr, env,
             now - timedelta(minutes=8), now - timedelta(minutes=8),
             now + timedelta(days=1)))
        incident_id = fetch_one(cur)[0]
        cur.execute("insert into ops.incident_service (incident_id, service_id) values (%s, %s)",
                    (incident_id, service_id))

        # ── 1. ONE QUERY RETURNS THE WHOLE CHAIN ────────────────────────────
        print("1. one query explains the journey")
        cur.execute(
            """select kind, ref, state, occurred_at, source_kind, source_ref,
                      freshness_state, environment
                 from ops.v_trace
                where correlation_id = %s
             order by occurred_at""",
            (corr,))
        chain = cur.fetchall()
        kinds = [r[0] for r in chain]
        check("the chain has every link", set(kinds) == {"deployment", "check", "job", "incident"},
              f"got {sorted(set(kinds))}")
        check("the chain is in time order",
              chain_in_time_order(chain))
        check("the deploy precedes the failure",
              bool(kinds) and kinds[0] == "deployment", f"first link was {kinds[0] if kinds else 'nothing'}")

        # ── 2. EVERY ROW NAMES SOURCE AND FRESHNESS ─────────────────────────
        print("\n2. every link names its source and its freshness")
        check("no link is missing source_kind", all(r[4] for r in chain))
        check("no link is missing source_ref", all(r[5] for r in chain))
        check("no link is missing a freshness state", all(r[6] for r in chain))
        check("no link is missing its environment", all(r[7] for r in chain))

        # ── 3. A FAILURE NAMES ITS CLASS ────────────────────────────────────
        print("\n3. a failure that cannot name its class is refused")
        refuses(cur,
                """insert into ops.run (kind, correlation_id, service_id, environment,
                                        run_key, state, started_at, ended_at,
                                        source_kind, source_ref, observed_at)
                   values ('job', %s, %s, %s, 'no-class', 'failed', now(), now(),
                           'wrapper', 'x', now())""",
                (corr, service_id, env),
                "a failed run with no failure_class is refused")
        refuses(cur,
                """insert into ops.run (kind, correlation_id, service_id, environment,
                                        run_key, state, started_at,
                                        source_kind, source_ref, observed_at)
                   values ('job', %s, %s, %s, 'no-end', 'succeeded', now(),
                           'wrapper', 'x', now())""",
                (corr, service_id, env),
                "a terminal run with no ended_at is refused")
        refuses(cur,
                """insert into ops.deployment (correlation_id, service_id, environment,
                                               state, started_at, ended_at,
                                               source_kind, source_ref, observed_at)
                   values (%s, %s, %s, 'complete', now(), now(),
                           'wrapper', 'x', now())""",
                (corr, service_id, env),
                "a deployment claiming complete with no read-back is refused")

        # ── 4. A SILENT COLLECTOR READS UNKNOWN, NEVER LAST-GREEN ───────────
        print("\n4. a silent collector reads unknown, never the last green result")
        cur.execute(
            """insert into ops.service (key, name, family, criticality, owner_actor)
               values ('gate-probe-silent', 'Program 3 silent-collector probe',
                       'Data', 'high', 'joe')
               returning id""")
        silent_id = fetch_one(cur)[0]
        cur.execute(
            """insert into ops.service_environment
                   (service_id, environment, expected_cadence_seconds)
               values (%s, %s, 3600)""",
            (silent_id, env))
        # It SUCCEEDED — two days ago, and nothing has been heard since.
        cur.execute(
            """insert into ops.run
                   (kind, correlation_id, service_id, environment, run_key, state,
                    started_at, ended_at, source_kind, source_ref,
                    observed_at, expires_at)
               values ('job', %s, %s, %s, 'silent.job', 'succeeded', %s, %s,
                       'wrapper', 'bin/nightly.sh', %s, %s)""",
            (uuid.uuid4(), silent_id, env,
             now - timedelta(days=2), now - timedelta(days=2),
             now - timedelta(days=2), now - timedelta(days=2) + timedelta(hours=1)))

        cur.execute(
            """select health, freshness_state
                 from ops.v_service_environment_health
                where service_id = %s and environment = %s""",
            (silent_id, env))
        row = cur.fetchone()
        check("a service with a two-day-old success exists in the health view", row is not None)
        if row:
            health, freshness = row
            check("its health is NOT healthy", health != "healthy", f"health read {health!r}")
            check("its health is unknown", health == "unknown", f"health read {health!r}")
            check("its freshness is stale", freshness == "stale", f"freshness read {freshness!r}")

        # And the control: a fresh success DOES read healthy, or the rule above
        # is just a view that always says unknown.
        cur.execute(
            """insert into ops.run
                   (kind, correlation_id, service_id, environment, run_key, state,
                    started_at, ended_at, source_kind, source_ref,
                    observed_at, expires_at)
               values ('job', %s, %s, %s, 'silent.job', 'succeeded', %s, %s,
                       'wrapper', 'bin/nightly.sh', %s, %s)""",
            (uuid.uuid4(), silent_id, env, now, now, now, now + timedelta(hours=1)))
        cur.execute(
            """select health from ops.v_service_environment_health
                where service_id = %s and environment = %s""",
            (silent_id, env))
        check("a fresh success reads healthy", fetch_one(cur)[0] == "healthy",
              "the view may be answering unknown unconditionally")

        # ── 5. THE READ ROLE CANNOT WRITE ───────────────────────────────────
        print("\n5. the read role may select and may not write")
        for table in ("run", "deployment", "incident", "service"):
            cur.execute(
                """select has_table_privilege('carr_reader', %s, 'select'),
                          has_table_privilege('carr_reader', %s, 'insert')""",
                (f"ops.{table}", f"ops.{table}"))
            can_read, can_write = fetch_one(cur)
            check(f"carr_reader may read ops.{table}", can_read)
            check(f"carr_reader may NOT write ops.{table}", not can_write)

        conn.rollback()

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nPROGRAM 3 GATE NOT MET:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("PROGRAM 3 GATE MET: one traceable view explains a failed critical journey.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
