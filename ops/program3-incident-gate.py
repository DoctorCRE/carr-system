#!/usr/bin/env python3
"""
program3-incident-gate.py — the acceptance test for incident raising, written
before the thing it tests (rule e65efc68), same as the trace gate before it.

WHY THIS IS THE LAST PIECE. ops.incident has existed since migration 0115 with a
lifecycle, separated facts and hypotheses, and constraints that refuse a
dishonest close — and NOTHING has ever written a row to it. On the night of
2026-08-14 the nightly chain failed five steps. The ledger recorded all five
perfectly and not one of them became an incident, because a table with no writer
is a schema, not a mechanism. That is the "built, never fed" failure this system
has already caught twice in other lanes.

THE DOCTRINE THIS ENCODES, and each assertion below is one line of it:

  "Repeated identical failures deduplicate into one incident or work item."
      — one failure opens one incident; the same failure again appends a FACT to
        that incident rather than opening a second. Without this, a job that
        fails hourly produces twenty-four incidents a day and the incident list
        becomes the thing people stop reading.

  "Every incident separates facts from hypotheses."
      — the failure that caused it is a FACT with a source. The tool never
        writes a hypothesis; a machine observing an exit code has no theory,
        and inventing one would put a guess where a human looks for evidence.

  "Resolution requires recovery evidence and a monitoring interval."
      — a recovered service moves the incident to MONITORING, never straight to
        resolved. The database already refuses the dishonest version; this
        proves the writer does not try.

  "Alerts page only when a human can take an immediate action."
      — a SKIPPED step is not a failure. exit 78 means a step ran, found
        something it needs absent, wrote nothing and said so. Raising an
        incident for that would fire every night until a credential lands,
        which is the alarm-fatigue failure this chain has already been burned by
        once.

EVERY ROW IT WRITES IS ROLLED BACK. Run it against staging as often as you like:

    .venv/bin/python tools/db-tap.py --project staging run ops/program3-incident-gate.py
"""

# ci: db-gate — ops/ci.sh runs this against the throwaway Postgres in its
# migration class. Remove the marker only with a reason; an unrun acceptance
# gate is a document with assertions in it.

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

try:
    import psycopg
except ImportError:
    sys.exit("program3-incident-gate: psycopg not installed")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def one(cur, what: str) -> tuple:
    """fetchone() that says WHICH read came back empty. psycopg types it Optional
    because a query legitimately may return no row; in this file every one of
    these is a read that must have produced a row, so an empty result is a broken
    assertion and should say so rather than raising a subscript error."""
    row = cur.fetchone()
    if row is None:
        raise AssertionError(f"program3-incident-gate: {what} returned no row")
    return row


FAILURES: list[str] = []
PASSES: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(failures=FAILURES, passes=PASSES)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("program3-incident-gate: DATABASE_URL is not set")

    # lib/loadpy.py, not a seventh copy of the importlib incantation:
    # spec_from_file_location returns None for a path that moved, and the copies
    # failed with "NoneType has no attribute exec_module" instead of naming the
    # file. Extracted 2026-08-14 when 21 of the tripwire's 47 errors turned out
    # to be those three lines in seven files.
    sys.path.insert(0, os.path.join(REPO, "lib"))
    from loadpy import load_module_from_path                      # noqa: E402
    ops_record = load_module_from_path(
        "ops_record", os.path.join(REPO, "tools", "ops-record.py"))

    now = datetime.now(timezone.utc)
    env = "staging"

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        # A service of our own so the assessment cannot be confused by whatever
        # else staging holds.
        cur.execute(
            """insert into ops.service (key, name, family, criticality, owner_actor)
               values ('incident-gate-probe', 'Program 3 incident gate probe',
                       'Data', 'critical', 'joe')
               returning id""")
        service_id = one(cur, "the probe service insert")[0]
        cur.execute(
            """insert into ops.service_environment
                   (service_id, environment, expected_cadence_seconds)
               values (%s, %s, 3600)""",
            (service_id, env))

        def record(run_key, state, failure_class=None, corr=None, at=None):
            cur.execute(
                """insert into ops.run
                       (kind, correlation_id, service_id, environment, run_key, state,
                        failure_class, started_at, ended_at, source_kind, source_ref,
                        observed_at, expires_at)
                   values ('job', %s, %s, %s, %s, %s, %s, %s, %s,
                           'wrapper', 'bin/nightly.sh', %s, %s)
                   returning correlation_id""",
                (corr or uuid.uuid4(), service_id, env, run_key, state, failure_class,
                 at or now, at or now, at or now, (at or now) + timedelta(hours=1)))
            return one(cur, f"the {run_key} run insert")[0]

        # ── 1. A FAILURE RAISES EXACTLY ONE INCIDENT ────────────────────────
        print("1. a failure raises one incident, carrying the correlation of the run")
        corr1 = record("nightly.exports", "failed", "exporter_error")
        opened = ops_record.assess(cur, environment=env, window_hours=24)
        check("one incident was opened", opened == 1, f"opened {opened}")

        cur.execute(
            """select id, ref, severity, state, correlation_id
                 from ops.incident where environment = %s and state <> 'reviewed'""",
            (env,))
        rows = cur.fetchall()
        check("exactly one incident row exists", len(rows) == 1, f"found {len(rows)}")
        inc_id, ref, severity, state, inc_corr = rows[0]
        check("it carries the failing run's correlation id", inc_corr == corr1)
        check("it starts in the detected state", state == "detected", f"state {state!r}")

        # ── 2. SEVERITY FOLLOWS CRITICALITY ─────────────────────────────────
        print("\n2. severity follows the service's criticality")
        check("a critical service raises SEV-1", severity == "SEV-1", f"got {severity}")

        # ── 3. THE SAME FAILURE AGAIN DOES NOT OPEN A SECOND ────────────────
        print("\n3. repeated identical failures deduplicate into the one incident")
        record("nightly.exports", "failed", "exporter_error",
               at=now + timedelta(minutes=5))
        again = ops_record.assess(cur, environment=env, window_hours=24)
        check("no second incident was opened", again == 0, f"opened {again}")
        cur.execute("select count(*) from ops.incident where environment = %s", (env,))
        check("still exactly one incident", one(cur, "the incident count")[0] == 1)

        cur.execute("select count(*) from ops.incident_fact where incident_id = %s", (inc_id,))
        facts = one(cur, "the fact count")[0]
        check("the recurrence was appended as a fact", facts >= 2, f"{facts} fact(s)")

        cur.execute("select count(*) from ops.incident_hypothesis where incident_id = %s", (inc_id,))
        check("the tool wrote no hypotheses", one(cur, "the hypothesis count")[0] == 0,
              "a machine reading an exit code has no theory and must not invent one")

        # ── 4. A DIFFERENT FAILURE IS ITS OWN INCIDENT ──────────────────────
        print("\n4. a different failure is a different incident")
        record("nightly.backup", "failed", "pg_dump_error", at=now + timedelta(minutes=6))
        third = ops_record.assess(cur, environment=env, window_hours=24)
        check("a distinct failure opened its own incident", third == 1, f"opened {third}")

        # ── 5. A SKIP IS NOT A FAILURE ──────────────────────────────────────
        print("\n5. a skipped step raises nothing")
        cur.execute("select count(*) from ops.incident where environment = %s", (env,))
        before = one(cur, "the incident count before the skip")[0]
        record("nightly.cadence-engine", "skipped", at=now + timedelta(minutes=7))
        skipped = ops_record.assess(cur, environment=env, window_hours=24)
        cur.execute("select count(*) from ops.incident where environment = %s", (env,))
        after = one(cur, "the incident count after the skip")[0]
        check("a skip opened no incident", skipped == 0 and after == before,
              "exit 78 means not-configured, and alarming on it fires every night")

        # ── 6. RECOVERY MOVES TO MONITORING, NEVER STRAIGHT TO RESOLVED ─────
        print("\n6. recovery moves the incident to monitoring, with evidence")
        record("nightly.exports", "succeeded", at=now + timedelta(minutes=10))
        ops_record.assess(cur, environment=env, window_hours=24)
        cur.execute(
            """select state, recovery_evidence_ref, monitoring_until, resolved_at
                 from ops.incident where id = %s""", (inc_id,))
        state, evidence, monitoring_until, resolved_at = one(cur, "the recovered incident")
        check("the incident moved to monitoring", state == "monitoring", f"state {state!r}")
        check("it carries recovery evidence", bool(evidence))
        check("it carries a monitoring interval", monitoring_until is not None)
        check("it was NOT marked resolved", resolved_at is None,
              "a machine watching one green run has not proven recovery")

        # And the other incident, whose service has not recovered, is untouched.
        cur.execute(
            """select count(*) from ops.incident
                where environment = %s and state = 'detected'""", (env,))
        check("the unrecovered incident stayed open", one(cur, "the open-incident count")[0] == 1)

        # ── 7. THE INCIDENT IS REACHABLE FROM THE TRACE ─────────────────────
        print("\n7. the incident appears in the same trace as the run that caused it")
        cur.execute(
            """select kind from ops.v_trace where correlation_id = %s order by occurred_at""",
            (corr1,))
        kinds = [r[0] for r in cur.fetchall()]
        check("the trace holds both the job and the incident",
              "job" in kinds and "incident" in kinds, f"got {kinds}")

        conn.rollback()

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nINCIDENT GATE NOT MET:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("INCIDENT GATE MET: failures become incidents, once each, and recovery is honest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
