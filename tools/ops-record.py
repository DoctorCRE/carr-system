#!/usr/bin/env python3
"""
ops-record.py — THE ONE WRITER for the operational ledger, and the read path
until the Control Room exists.

WHY ONE FILE. Rule a8c55a47: a manual path and an automated path that do the same
job must be the same code. bin/nightly.sh records a step through this; a human
recording a deploy by hand records it through this; the golden-workflow runner
records a check through this. There is no second way to put a row in ops.run, so
the two paths cannot drift apart the way the export path and the manual export
once did.

WHAT IT DOES

  sync-registry   Apply ops/config/services.json into ops.service,
                  ops.service_environment and ops.service_dependency. The repo
                  file is the source; the tables are its render. A service that
                  disappears from the file is RETIRED, never deleted — nothing
                  silently rots (rule def3e84e).

  run             Append one row to the job-run / check-run ledger.

  deployment      Append one deployment marker. /release answers what is serving
                  now; this answers what was serving then.

  trace           Read one correlation id back as a chain. This is the terminal
                  form of the Program 3 gate view, and it is the honest interim
                  answer to "without terminal archaeology": one query against the
                  record, not a hunt through a text log on one Mac.

  health          Read the derived health of every registered service and
                  environment, with the freshness that produced it.

  pr-evidence     Attach recovery_evidence_ref to any incident a merged PR's
                  title or body names (WR-000019 slice S1). Same carr_jobs
                  boundary as assess: it can attach evidence, never close.

WHAT IT DELIBERATELY DOES NOT DO

  It never invents a service. An unknown --service is refused with the registry
  named in the message, because a catalog that mints a row for every typo stops
  being a catalog. Register it in ops/config/services.json and sync.

  It never fails the job it is recording. `run` exits non-zero and says why when
  it cannot write, and callers are expected to ignore that exit code — but the
  failure is NOT hidden, because a missing run row makes that service's health
  read `unknown` on the next look rather than staying green. That property is
  the whole design of ops.v_service_environment_health: silence is visible.

  It writes no business payload. detail is one redacted line and evidence_ref is
  a pointer. Client content, secrets and raw transcripts are absent from
  ordinary telemetry by the observability contract.

CREDENTIALS. unattended run and assess require CARR_DB_JOBS_URL — the
carr_jobs role, which holds narrow operational grants because a ledger whose
routine writer can rewrite history is not a ledger. Explicit deployment
and settings operations preserve the deliberate DATABASE_URL path;
reads prefer the exporter credential. Registry sync needs the owner
(DATABASE_URL) and is a HUMAN, OWNER-CREDENTIAL step, run directly — NOT
through tools/db-tap.py. db-tap.py's "READ-ONLY BY DEFAULT" phase-1 hardening
(2026-08-13) puts every ordinary `run` or `sql` invocation, this one included,
behind `default_transaction_read_only=on` unless CARR_BREAK_GLASS=1 is set
with a --reason — sync-registry is routine maintenance, not a break-glass
event, so it is run straight, from an up-to-date main checkout after the
declaring PR has merged, by whoever holds DATABASE_URL:

  .venv/bin/python tools/ops-record.py sync-registry

(An older revision of this docstring said to route that call through
tools/db-tap.py; that line predated db-tap's read-only default and would
silently no-op the write. See PR #1241 round 5.)

  bin/nightly.sh                                   (records every step)
  .venv/bin/python tools/ops-record.py trace <correlation-id>
  .venv/bin/python tools/ops-record.py health
"""

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.credential_file import carr_config, read_env_file  # noqa: E402
REGISTRY = REPO / "ops" / "config" / "services.json"

# Routine ledger writes are jobs-only.  The broader write mode remains the
# explicit operator path used by db-tap and disposable DB tests.
DSN_FOR = {
    "routine": ("CARR_DB_JOBS_URL",),
    "write": ("DATABASE_URL", "CARR_DB_JOBS_URL"),
    "owner": ("DATABASE_URL", "CARR_DB_EXPORTER_URL"),
    "read":  ("DATABASE_URL", "CARR_DB_EXPORTER_URL", "CARR_DB_JOBS_URL"),
    "authority": ("CARR_DB_AUTHORITY_JOE_URL",),
    "forward_fix_verifier": ("CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL",),
}

TERMINAL_RUN_STATES = {"succeeded", "failed", "timed_out", "cancelled", "skipped"}


def credential_names() -> tuple[str, ...]:
    """Every environment variable this recorder will read a DSN from, in
    declaration order.

    Public because a suite that must prove "no reachable database" has to blind
    EVERY one of them, and blinding the ones it remembers is not the same thing.
    That distinction is not theoretical: from 2026-08-16 to 2026-08-18, the two
    scheduled-run selftests set DATABASE_URL to a dead port and deleted
    CARR_DB_JOBS_URL, which was airtight while `run` was connect("write") and
    stopped being airtight the moment it became connect("routine") —
    _load_db_env() below simply re-supplied the PRODUCTION jobs DSN by
    setdefault. ops/scheduled-run-record-selftest.py then recorded 46 fabricated
    SUCCEEDED rows into production's ops.run against loop-drain-weekdays and
    radar-weekly, one pair per CI run, each one a false observation of a job
    that had not run. Reading this list means a future mode added to DSN_FOR is
    blinded by both suites without either one being edited.
    """
    return tuple(dict.fromkeys(
        name for names in DSN_FOR.values() for name in names))


def _load_db_env() -> None:
    """Read ~/.config/carr/db.env through lib/credential_file, the reader every
    other Python job uses. Values already in the environment win."""
    try:
        values = read_env_file(carr_config("db.env"))
    except OSError:
        return
    for name, value in values.items():
        os.environ.setdefault(name, value)


def dsn(kind: str) -> str:
    _load_db_env()
    for name in DSN_FOR[kind]:
        value = os.environ.get(name)
        if value:
            if kind == "routine" and not _is_jobs_dsn(value):
                raise SystemExit("ops-record: CARR_DB_JOBS_URL must authenticate as carr_jobs")
            if kind == "authority" and not _is_authority_dsn(value):
                raise SystemExit("ops-record: CARR_DB_AUTHORITY_JOE_URL must authenticate as carr_authority_joe")
            if kind == "forward_fix_verifier" and not _is_forward_fix_verifier_dsn(value):
                raise SystemExit("ops-record: CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL must authenticate as carr_program5_forward_fix_verifier")
            return value
    raise SystemExit(
        f"ops-record: no credential — set one of {', '.join(DSN_FOR[kind])} "
        f"(they live in ~/.config/carr/db.env)")


def _is_jobs_dsn(value: str) -> bool:
    """Reject a misleading jobs variable before it reaches psycopg."""
    parsed = urlsplit(value)
    if parsed.scheme:
        return unquote(parsed.username or "") == "carr_jobs"
    return any(part == "user=carr_jobs" for part in value.split())


def _is_authority_dsn(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme:
        return unquote(parsed.username or "") == "carr_authority_joe"
    return any(part == "user=carr_authority_joe" for part in value.split())


def _is_forward_fix_verifier_dsn(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme:
        return unquote(parsed.username or "") == "carr_program5_forward_fix_verifier"
    return any(part == "user=carr_program5_forward_fix_verifier" for part in value.split())


def connect(kind: str):
    try:
        import psycopg
    except ImportError:
        raise SystemExit("ops-record: psycopg not installed (pip install 'psycopg[binary]')")
    conn = psycopg.connect(dsn(kind), autocommit=True)
    if kind in ("routine", "authority", "forward_fix_verifier"):
        with conn.cursor() as cur:
            if kind == "forward_fix_verifier":
                cur.execute("select session_user,current_user,pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member')")
            else:
                cur.execute("select session_user,current_user")
            row = cur.fetchone()
        expected = ("carr_jobs" if kind == "routine" else "carr_authority_joe"
                    if kind == "authority" else "carr_program5_forward_fix_verifier")
        exact_row = (expected, expected) if kind != "forward_fix_verifier" else (expected, expected, True)
        if row != exact_row:
            conn.close()
            raise SystemExit(f"ops-record: {kind} connection is not the exact scoped identity")
    return conn


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    # `now` is accepted because the callers are SHELL scripts, and portable
    # ISO-8601 out of `date` differs between BSD and GNU. Making every wrapper
    # get that right is how a read-back timestamp ends up missing on the one
    # machine whose date(1) took the other flag.
    if value == "now":
        return datetime.now(timezone.utc)
    try:
        # Accept the ISO-8601 the shell produces, Z suffix included.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"ops-record: not a timestamp: {value!r}")


def correlation_of(explicit: str | None) -> str:
    """One id threads a whole journey. The chain's own id wins; then the ambient
    one the caller exported; then a fresh one, because a run that cannot be
    correlated is still a chain of one and must never be a chain of none."""
    for candidate in (explicit, os.environ.get("CARR_CORRELATION_ID")):
        if candidate:
            try:
                return str(uuid.UUID(candidate))
            except ValueError:
                raise SystemExit(f"ops-record: correlation id is not a uuid: {candidate!r}")
    return str(uuid.uuid4())


def service_id(cur, key: str) -> str:
    cur.execute("select id from ops.service where key = %s", (key,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(
            f"ops-record: no service registered with key {key!r}. Add it to "
            f"ops/config/services.json and run sync-registry — this tool does not "
            f"invent services, because a catalog that mints a row per typo is not "
            f"a catalog.")
    return row[0]


# ── sync-registry ────────────────────────────────────────────────────────────
def cmd_sync_registry(args) -> int:
    spec = json.loads(REGISTRY.read_text(encoding="utf-8"))
    services = spec.get("services", [])
    deps = spec.get("dependencies", [])
    declared = {s["key"] for s in services}

    changes: list[str] = []
    with connect("owner") as conn, conn.cursor() as cur:
        for s in services:
            cur.execute(
                """insert into ops.service
                       (key, name, purpose, family, criticality, owner_actor,
                        repo_path, runtime, runbook_ref, retired_at, updated_at)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s, null, now())
                   on conflict (key) do update set
                       name = excluded.name, purpose = excluded.purpose,
                       family = excluded.family, criticality = excluded.criticality,
                       owner_actor = excluded.owner_actor, repo_path = excluded.repo_path,
                       runtime = excluded.runtime, runbook_ref = excluded.runbook_ref,
                       retired_at = null, updated_at = now()
                   returning (xmax = 0) as inserted""",
                (s["key"], s["name"], s.get("purpose"), s.get("family"),
                 s.get("criticality", "medium"), s["owner_actor"],
                 s.get("repo_path"), s.get("runtime"), s.get("runbook_ref")))
            if cur.fetchone()[0]:
                changes.append(f"registered service {s['key']}")

            sid = service_id(cur, s["key"])
            for e in s.get("environments", []):
                cur.execute(
                    """insert into ops.service_environment
                           (service_id, environment, endpoint, deploy_mechanism,
                            expected_cadence_seconds, cadence_grace_seconds, notes, updated_at)
                       values (%s,%s,%s,%s,%s,%s,%s, now())
                       on conflict (service_id, environment) do update set
                           endpoint = excluded.endpoint,
                           deploy_mechanism = excluded.deploy_mechanism,
                           expected_cadence_seconds = excluded.expected_cadence_seconds,
                           cadence_grace_seconds = excluded.cadence_grace_seconds,
                           notes = excluded.notes, updated_at = now()
                       returning (xmax = 0) as inserted""",
                    (sid, e["environment"], e.get("endpoint"), e.get("deploy_mechanism"),
                     e.get("expected_cadence_seconds"), e.get("cadence_grace_seconds", 0),
                     e.get("notes")))
                if cur.fetchone()[0]:
                    changes.append(f"registered {s['key']} in {e['environment']}")

        for d in deps:
            cur.execute(
                """insert into ops.service_dependency (service_id, depends_on_id, note)
                   select a.id, b.id, %s from ops.service a, ops.service b
                    where a.key = %s and b.key = %s
                   on conflict do nothing""",
                (d.get("note"), d["service"], d["depends_on"]))

        # A service that left the file is RETIRED, not deleted. Its runs stay
        # readable, and the retirement is visible rather than a silent absence.
        cur.execute("select key from ops.service where retired_at is null")
        for (key,) in cur.fetchall():
            if key not in declared:
                cur.execute(
                    "update ops.service set retired_at = now(), updated_at = now() where key = %s",
                    (key,))
                changes.append(f"RETIRED {key} — no longer declared in ops/config/services.json")

    print(f"ops-record: registry synced — {len(services)} service(s) declared")
    for c in changes:
        print(f"  {c}")
    if not changes:
        print("  (no change)")
    return 0


# ── run ──────────────────────────────────────────────────────────────────────
RUN_REAP_BATCH = 1000


def _reap_expired_runs(conn, cur, service_id, environment, run_key) -> None:
    """Keep ops.run bounded from the one path that grows it (migration 1051).

    A savepoint, so a reaper failure never costs the run row just written."""
    try:
        with conn.transaction():
            cur.execute("select ops.reap_expired_runs(%s, %s, %s, %s)",
                        (service_id, environment, run_key, RUN_REAP_BATCH))
    except Exception as e:                                   # noqa: BLE001
        print(f"ops-record: run retention skipped: {type(e).__name__}",
              file=sys.stderr)


def cmd_run(args) -> int:
    if args.state in ("failed", "timed_out") and not args.failure_class:
        # The database refuses this too. Failing here first gives the caller a
        # sentence instead of a constraint name.
        print("ops-record: a failed run must name its failure class (--failure-class)",
              file=sys.stderr)
        return 2

    release_key = getattr(args, "release_key", None)
    budget_ms = getattr(args, "budget_ms", None)
    duration_ms = getattr(args, "duration_ms", None)
    is_performance = args.key.startswith("performance.")
    is_recovery = args.key.startswith("recovery.rehearsal.")

    if duration_ms is not None and duration_ms < 0:
        print("ops-record: --duration-ms must be zero or greater", file=sys.stderr)
        return 2
    if duration_ms is not None and (args.started_at or args.ended_at):
        print("ops-record: --duration-ms cannot be combined with --started-at or "
              "--ended-at", file=sys.stderr)
        return 2
    if is_performance:
        if (args.environment != "production" or not release_key
                or budget_ms is None or budget_ms <= 0 or not args.evidence_ref
                or duration_ms is None):
            print("ops-record: performance.* requires Production, --release-key, "
                  "positive --budget-ms, --duration-ms, and --evidence-ref",
                  file=sys.stderr)
            return 2
        if args.state == "succeeded":
            if duration_ms == 0:
                print("ops-record: a successful performance run must have a "
                      "positive measured duration", file=sys.stderr)
                return 2
            if duration_ms > budget_ms:
                print("ops-record: an over-budget performance run cannot be "
                      "recorded as succeeded", file=sys.stderr)
                return 2
    elif budget_ms is not None:
        print("ops-record: --budget-ms is only valid for performance.* runs",
              file=sys.stderr)
        return 2
    if is_recovery and (args.environment not in ("staging", "rehearsal")
                        or not release_key or not args.evidence_ref):
        print("ops-record: recovery.rehearsal.* requires staging/rehearsal, "
              "--release-key, and --evidence-ref", file=sys.stderr)
        return 2

    started: datetime | None
    ended: datetime | None
    if duration_ms is not None:
        ended = datetime.now(timezone.utc)
        started = ended - timedelta(milliseconds=duration_ms)
    else:
        started = parse_ts(args.started_at)
        ended = parse_ts(args.ended_at)
    if args.state in TERMINAL_RUN_STATES and ended is None:
        ended = datetime.now(timezone.utc)
    if ended is not None and started is None:
        started = ended

    corr = correlation_of(args.correlation)
    try:
        with connect("routine") as conn, conn.transaction(), conn.cursor() as cur:
            try:
                sid = service_id(cur, args.service)
            except SystemExit as e:
                # AN UNREGISTERED SERVICE IS A CONFIGURATION STATE, NOT A FAILED
                # STEP — on the COLLECTOR path only. service_id() refuses loudly
                # because that is right for an operator typing a command: they
                # want the registry named and the run rejected. A wrapper calling
                # this once per step wants the opposite. Without this, a database
                # that has the ops schema but an unseeded catalog makes
                # bin/nightly.sh print the same refusal ten times a night, which
                # is strictly worse than the missing-schema case it already
                # handles — and worse than silence, because a log that cries
                # every night is a log nobody reads. 78 is the same EX_CONFIG
                # code the missing schema returns, so the wrapper's existing
                # "say it once and stop" path covers both without new logic.
                print(str(e), file=sys.stderr)   # already carries the prefix
                return 78
            release_id = None
            recovery_strategy = None
            recovery_plan_ref = None
            if release_key:
                cur.execute(
                    """select id, service_id, performance_budget_ms,
                              recovery_strategy, rollback_plan_ref
                         from ops.release
                        where release_key = %s""",
                    (release_key,))
                release = cur.fetchone()
                if not release:
                    print(f"ops-record: no release {release_key!r}", file=sys.stderr)
                    return 2
                (release_id, release_service_id, release_budget_ms,
                 recovery_strategy, recovery_plan_ref) = release
                if release_service_id != sid:
                    print("ops-record: run service does not match its release",
                          file=sys.stderr)
                    return 2
                if is_performance and release_budget_ms != budget_ms:
                    print("ops-record: performance budget does not match the "
                          "approved release budget", file=sys.stderr)
                    return 2
                if is_recovery and (recovery_strategy not in ("rollback", "forward_fix")
                                    or not recovery_plan_ref):
                    print("ops-record: recovery rehearsal requires the release's "
                          "exact strategy and recovery plan", file=sys.stderr)
                    return 2
            cur.execute(
                """insert into ops.run
                       (kind, correlation_id, service_id, release_id, environment,
                        run_key, state,
                        failure_class, exit_code, attempt, started_at, ended_at,
                        budget_ms, recovery_strategy, recovery_plan_ref,
                        source_kind, source_ref, observed_at, expires_at,
                        evidence_ref, detail)
                   select %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(),
                          -- THE OBSERVATION DECLARES ITS OWN EXPIRY, taken from
                          -- the registry at write time when the caller does not
                          -- name one. Without this the row reads `unknown`
                          -- forever in ops.v_trace while ops.v_service_environment_
                          -- health calls the same row stale — two views over one
                          -- row disagreeing, which is how a reader stops trusting
                          -- both. A service with no registered cadence still gets
                          -- null, and null still means unknown, honestly.
                          case
                            when %s::int is not null
                              then now() + make_interval(secs => %s::int)
                            when se.expected_cadence_seconds is not null
                              then now() + make_interval(secs =>
                                     se.expected_cadence_seconds + se.cadence_grace_seconds)
                          end,
                          %s,%s
                     from (select 1) _
                     left join ops.service_environment se
                       on se.service_id = %s and se.environment = %s
                   returning id""",
                (args.kind, corr, sid, release_id, args.environment, args.key, args.state,
                 args.failure_class, args.exit_code, args.attempt, started, ended,
                 budget_ms, recovery_strategy if is_recovery else None,
                 recovery_plan_ref if is_recovery else None,
                 args.source_kind, args.source_ref,
                 args.expires_in, args.expires_in,
                 args.evidence_ref, (args.detail or None),
                 sid, args.environment))
            run_id = cur.fetchone()[0]
            recovered = []
            if args.state == "succeeded":
                # THE NONZERO-TO-ZERO TRANSITION, on the path that already runs.
                # The council preferred this to the spool flusher precisely
                # because it is where the transition is visible: the flusher
                # replays argv it is forbidden to interpret, while this is the
                # writer that already decides what a failed run means and is
                # the only place both halves of the rule can stay together.
                recovered = _record_success_recovery(
                    cur=cur, service_key=args.service, environment=args.environment,
                    run_key=args.key, run_id=run_id)
            elif args.state in ("failed", "timed_out"):
                cur.execute("select criticality from ops.service where id = %s", (sid,))
                criticality = cur.fetchone()[0]
                _record_failure_incident(
                    cur=cur, correlation_id=corr, service_id=sid,
                    service_key=args.service, criticality=criticality,
                    environment=args.environment, source_kind="run",
                    source_id=run_id, source_label=args.key, state=args.state,
                    failure_class=args.failure_class, detail=args.detail)
            _reap_expired_runs(conn, cur, sid, args.environment, args.key)
    except SystemExit:
        raise
    except Exception as e:                                   # noqa: BLE001
        # 78 = EX_CONFIG, this codebase's own "it ran, found a thing it needs
        # absent, wrote nothing and said so" convention (bin/nightly.sh treats it
        # as SKIP rather than FAIL). The ops schema being absent means migration
        # 0115 has not been applied to THIS database — a configuration state, not
        # a failed night, and callers use the distinct code to say it once rather
        # than once per step.
        # Any ops.* relation being absent means the same thing, and the first one
        # touched is ops.service (the registry lookup), not ops.run — matching on
        # ops.run alone would have missed the case this exists for.
        if 'relation "ops.' in str(e) and "does not exist" in str(e):
            print("ops-record: the ops schema is not on this database — "
                  "migration 0115 is unapplied here. Nothing recorded.",
                  file=sys.stderr)
            return 78
        # THE SAME STATE, ONE MIGRATION LATER. 0293 added the occurrence
        # counters this writer now sets on every incident and the
        # clear_recovered_incident function it calls on a recovery, so a
        # database carrying 0115 but not 0293 fails on a column or a function
        # rather than on a relation. That is still "not provisioned here" and
        # still deserves 78 — a deploy that runs ahead of its migration should
        # say which migration, once, not fail every step of the night with a
        # constraint name.
        if ("clear_recovered_incident" in str(e)
                or ('column "occurrence_count"' in str(e)
                    or 'column "last_seen_at"' in str(e)
                    or 'column "first_seen_at"' in str(e))):
            print("ops-record: incident fingerprint columns and the recovery "
                  "function are missing — migration 0293 is unapplied here. "
                  "Nothing recorded.", file=sys.stderr)
            return 78
        # Otherwise the caller ignores this exit code on purpose. The failure is
        # still not hidden: an absent run row makes this service read unknown on
        # the next health look, which is the honest outcome and the reason
        # nothing here needs a spool to stay truthful.
        print(f"ops-record: could not record {args.service}/{args.key}: "
              f"{str(e).splitlines()[0][:200]}", file=sys.stderr)
        return 1

    print(f"{corr} {run_id}")
    for ref, action, reason in recovered:
        print(f"  {'cleared' if action == 'clear' else 'recovering'} {ref}  {reason}")
    return 0


# ── assess: failures become incidents ────────────────────────────────────────
# THE LAST UNBUILT ELEMENT OF PROGRAM 3. ops.incident shipped in 0115 with a
# lifecycle, separated facts and hypotheses, and constraints that refuse a
# dishonest close — and nothing ever wrote to it. On the night of 2026-08-14 the
# nightly chain failed five steps; the ledger caught all five and not one became
# an incident, because a table with no writer is a schema.
#
# IT JUDGES THE LATEST OBSERVATION, NOT THE HISTORY. For each job the question is
# "what did this thing do most recently?" — not "has it ever failed in the
# window". Alarming on history means an incident that can never clear while a
# week-old failure is still inside the window, and a service that recovered an
# hour ago keeps paging. The latest terminal run is the state of the world.
#
# IT NEVER WRITES A HYPOTHESIS. A machine reading an exit code has no theory
# about the cause, and putting a guess where a human looks for evidence is the
# thing the facts/hypotheses split exists to prevent. Facts only, each with the
# run that produced it as its source.

SEVERITY_BY_CRITICALITY = {
    "critical": "SEV-1",   # service unavailable
    "high":     "SEV-2",   # major workflow degraded
    "medium":   "SEV-3",   # contained, with a workaround
    "low":      "SEV-3",
}

# How long a recovered service is watched before a human may call it resolved.
# The doctrine requires a monitoring interval on resolution; this is the interval
# the machine proposes, never the resolution itself.
MONITORING_HOURS = 24


# ── the fingerprint, and why a raw exit code is not one ──────────────────────
# THE MEASUREMENT, 2026-08-23 (process-audit council, recommendation 3, marked
# safe by both chairs). 26 incidents open, and the ledger showed exactly which
# ones the machine should never have asked a human about:
#
#   nightly.vault-drift-watch   open twice — exit_2 and exit_69
#   nightly.portability-mirror  failed exit_1, exit_2 and exit_69 across four days
#
# 0116 made the signature service|environment|run_key|failure_class and made two
# open incidents with the same signature impossible, which is right. What it
# could not know is that `failure_class` arrives in two different registers.
# Some callers name a diagnosis — pubkey_mismatch, keepalive_not_accepting,
# performance_budget_exceeded — and two of those on one job really are two
# problems with two remedies. bin/run-scheduled.sh and bin/nightly.sh instead
# pass the wrapper's exit code through as `exit_<n>`, and an exit code is not a
# diagnosis: exit_1 and exit_2 from one step mean "it returned nonzero" twice.
# Splitting a row on that number pages a human a second time for the same job
# failing the same way, which is the churn the council measured.
#
# SO NORMALIZATION IS DELIBERATELY NARROW. It touches ONLY the `exit_<n>` shape,
# and even there it keeps every code this codebase has given its own meaning —
# 69 is "a dependency was unavailable" and 78 is "not configured here", which
# call for different work than a plain nonzero and must not be folded into it.
# A named class is never rewritten. That is the council's kill-test condition
# ("distinct failure classes on the same job must NOT collapse into one row")
# expressed as a rule rather than a hope, and ops/incident-fingerprint-selftest.py
# is where it is held.
NAMED_EXIT_CLASSES = {
    64:  "usage",                   # EX_USAGE — the caller invoked it wrong
    69:  "dependency_unavailable",  # EX_UNAVAILABLE — a seam it needs was down
    77:  "permission_denied",       # EX_NOPERM
    78:  "configuration",           # EX_CONFIG — this repo's "not provisioned here"
    124: "timed_out",               # coreutils timeout(1)
    137: "killed",                  # SIGKILL
    143: "terminated",              # SIGTERM
}

# What an unnamed nonzero exit collapses to. It says exactly what is known —
# the step returned nonzero — and no more. The exact code stays on the run row
# and in the incident's facts, so nothing is lost, only un-paged.
GENERIC_EXIT_CLASS = "exit_status"

_EXIT_CLASS_RE = re.compile(r"^exit[_-]?([0-9]{1,3})$", re.IGNORECASE)

# A failure with no class at all. The `run` subcommand refuses one, but
# deployments and hand-written rows can still arrive without it, and a
# fingerprint ending in an empty field silently matched every other classless
# failure on the same job.
UNCLASSIFIED = "unclassified"


def normalize_failure_class(failure_class: str | None) -> str:
    """The failure class as the fingerprint should see it.

    Named classes pass through untouched — that is the whole guard. Only the
    `exit_<n>` shape is rewritten, and only where the number carries no meaning
    of its own.
    """
    raw = (failure_class or "").strip()
    if not raw:
        return UNCLASSIFIED
    m = _EXIT_CLASS_RE.match(raw)
    if not m:
        return raw
    return NAMED_EXIT_CLASSES.get(int(m.group(1)), GENERIC_EXIT_CLASS)


def incident_fingerprint(service_key: str, environment: str, operation: str,
                         failure_class: str | None) -> str:
    """service|environment|operation|failure-class — the identity of one problem.

    Kept in the `signature` column 0116 already constrains, in 0116's exact
    four-field shape, so the partial unique index over open incidents remains
    the guarantee and every existing reader (ops.v_trace, the sweep, the
    Worker's own recordWorkerFailure) keeps working unchanged. The only thing
    that moved is which string goes in the fourth field.
    """
    return "|".join((service_key, environment, operation,
                     normalize_failure_class(failure_class)))


def fingerprint_job(signature: str | None) -> tuple[str, str, str] | None:
    """(service, environment, run_key) for a run-sourced fingerprint, or None.

    The failure class is dropped on purpose: "has this job recovered?" is a
    question about the job, not about the way it last broke.
    """
    parts = (signature or "").split("|", 3)
    if len(parts) != 4 or not all(parts[:3]):
        return None
    return parts[0], parts[1], parts[2]


# ── success-clears ───────────────────────────────────────────────────────────
# THE OTHER HALF OF THE SAME MEASUREMENT. On 2026-08-23 five incidents sat open
# with twelve consecutive green runs behind them: partner-ping, rules-refresh,
# run-spool-flush, room-bridge and doc-engine's liveness probe. Nothing was
# wrong with any of them and nothing could say so, because the only close path
# in the repo (`sweep`) needs the owner credential — 0117 withholds resolved_at
# from carr_jobs — and bin/nightly.sh, which runs as carr_jobs, therefore prints
# `incident sweep (admin capability unavailable)` every single night instead of
# sweeping. A close path a scheduled job cannot reach never runs.
#
# AND THE 24-HOUR WINDOW CANNOT BE MET BY A TICKER. sweep_decision asks for a
# full MONITORING_HOURS with no failure recorded. partner-ping runs every 120s;
# one bad minute anywhere in a day resets that, so a job that flaps hourly and
# is healthy in between can never clear on the clock even though it is fine
# right now. The council asked for a success SEQUENCE instead — three
# consecutive healthy RUN ROWS — which a genuinely broken job never satisfies
# at all and a recovered one satisfies in bounded time.
#
# HOW LONG THAT ACTUALLY IS, measured rather than assumed: it is three recorded
# rows, not three wakes. bin/run-scheduled.sh's --heartbeat-interval throttles
# a SUCCEEDED row to one per 1800s for partner-ping and capture-poll and one
# per 900s for room-bridge, because recording ~720 fires a day from a healthy
# channel is noise. So partner-ping clears about 90 minutes after it recovers,
# not six. That is the right trade and worth stating plainly: the alternative
# is counting wakes nobody recorded. Every FAILURE is still recorded
# immediately regardless of the throttle, so nothing delays the incident — only
# the all-clear.
#
# SEV-1 IS UNTOUCHED, HERE AND IN THE DATABASE. This function refuses it, and
# ops.clear_recovered_incident (migration 0293) refuses it again in a
# SECURITY DEFINER body the job role cannot edit — so the automatic path cannot
# close a critical incident even if this Python is wrong. Evidence-required,
# human-approved SEV-1 closure stays exactly where 0117 put it.
HEALTHY_RUNS_TO_CLEAR = 3

# Severities the machine may close on its own. SEV-0 and SEV-1 are absent on
# purpose and the absence is the rule.
AUTO_CLEARED_SEVERITIES = frozenset({"SEV-2", "SEV-3", "SEV-4"})


def recovery_decision(incident, healthy_streak, required=HEALTHY_RUNS_TO_CLEAR):
    """('clear' | 'monitor' | 'none', reason) for one open incident.

    Pure, and separated from the write for the same reason
    resolve_preconditions is: the guards ARE the substance, and a clear path
    that rubber-stamps anything is worse than none because then the pile only
    LOOKS handled. ops/incident-fingerprint-selftest.py holds every branch.

    `healthy_streak` is how many consecutive terminal runs of this job ended
    succeeded, counting back from the most recent one. It is derived from
    ops.run by the caller and re-derived inside the database function before
    anything closes, so a wrong number here cannot close anything.
    """
    state = (incident.get("state") or "")
    if state in ("resolved", "reviewed"):
        return "none", f"already {state}"
    if (incident.get("source_kind") or "") != "collector":
        return "none", ("opened by hand, so a human closes it — the machine only "
                        "clears what the machine opened")
    if not incident.get("signature"):
        return "none", "no fingerprint, so there is no job whose health to read"
    if healthy_streak <= 0:
        return "none", "the latest run of this job is not green"

    severity = (incident.get("severity") or "")
    if severity not in AUTO_CLEARED_SEVERITIES:
        return "monitor", (
            f"{severity} never closes on a machine's say-so — recovery is recorded "
            f"and the close stays with a human")
    if healthy_streak < required:
        return "monitor", (
            f"{healthy_streak} of {required} consecutive healthy runs — recovery "
            f"recorded, watching for the rest")
    return "clear", (
        f"the job has run green {healthy_streak} consecutive times, "
        f"{required} being the sequence this system calls recovered. Closed by "
        f"the success-clears path with the green runs as evidence.")


# THE CLOSE PATH, added 2026-08-14. Until this existed, nothing in the repo
# could resolve an incident: collectors opened them, `assess` moved a recovered
# one to monitoring and deliberately left resolved_at "for a human", and that
# human had no tool to act with — no verb (all 106 checked), no subcommand, no
# script. So the pile only ever grew, and the nightly assessment reprinted it
# whole every night. One of the entries was a DELIBERATE acceptance probe that
# could never clear on its own, because no green run for an induced failure is
# ever coming.
#
# Kept PURE and separate from the write so the guards can be tested without a
# database, the same reason mcp-server/src/trace.js exports its classifiers.
# The guards are the substance: a close path that rubber-stamps anything is
# worse than none, because then the pile LOOKS handled.
def resolve_preconditions(incident, root_cause, evidence=None,
                          allow_early=False, now=None):
    """(ok, error, fields_to_write). Decides whether one incident may close.

    `incident` is a mapping with ref/state/recovery_evidence_ref/monitoring_until.
    """
    now = now or datetime.now(timezone.utc)
    state = (incident.get("state") or "").strip()
    if state in ("resolved", "reviewed"):
        return False, f"{incident.get('ref')} is already {state}", {}

    if not (root_cause or "").strip():
        return False, ("a root cause is required — 'close with an outcome' means the "
                       "outcome is recorded, not that the row is cleared"), {}

    # Evidence: prefer what assess already recorded off a real green run; fall
    # back to what the caller supplies, which is the only option for an incident
    # that never recovered because nothing was ever broken.
    ref = incident.get("recovery_evidence_ref") or (evidence or "").strip() or None
    if not ref:
        return False, ("no recovery evidence on the incident and none supplied — pass "
                       "--evidence naming what shows it is safe to close"), {}

    until = incident.get("monitoring_until")
    if until is not None and until > now and not allow_early:
        return False, (f"still inside its monitoring window until {until:%Y-%m-%d %H:%M}Z — "
                       f"a green run says the symptom stopped, not that the cause is "
                       f"understood. Pass --allow-early with a reason if the window "
                       f"cannot apply."), {}

    # monitoring_until is NOT NULL under the resolved constraint. An incident
    # closed early, or one that never had a window, still needs a value: stamp
    # now, so the row says the watching ended here rather than implying a wait
    # that never happened.
    return True, None, {
        "recovery_evidence_ref": ref,
        "monitoring_until": until or now,
        "resolved_at": now,
        "root_cause": root_cause.strip(),
    }


# ONE CLOCK, not two. Until 2026-08-18 this read the day twice: the sequence
# counted rows whose ref matched to_char(now(), ...), which Postgres evaluates
# in the SERVER's timezone, while the ref itself was formatted from the
# CLIENT's datetime.now(timezone.utc). The two agree only when the server runs
# UTC, so production was correct by luck (Neon is UTC) rather than by design.
# Against a local Postgres 17 inheriting the Mac's US/Central zone the split
# was reproduced on 2026-08-18 19:22 CDT: the count matched prefix
# INC-20260818- (no rows, so seq 01) while the ref written said
# INC-20260819-01. Every incident opened in that 5-hour window is numbered 01,
# and the second one dies on incident_ref_key — which is exactly how
# ops/program3-incident-gate.py failed.
#
# So the query now derives BOTH the prefix and the sequence, and the caller
# formats nothing: the label cannot disagree with the number it was counted
# against, and changing the server's zone later cannot reintroduce the split.
# `at time zone 'UTC'` is what pins the day, so the numbering space stays UTC
# regardless of what zone the cluster happens to inherit.
def _next_incident_ref(cur) -> str:
    cur.execute(
        """select to_char(now() at time zone 'UTC', 'YYYYMMDD') as day,
                  coalesce(max(substring(ref from '[0-9]+$')::int), 0) + 1 as seq
             from ops.incident
            where ref like 'INC-'
                          || to_char(now() at time zone 'UTC', 'YYYYMMDD')
                          || '-%'""")
    day, seq = cur.fetchone()
    return f"INC-{day}-{seq:02d}"


def _record_failure_incident(
        *, cur, correlation_id, service_id, service_key, criticality,
        environment, source_kind, source_id, source_label, state,
        failure_class, detail=None):
    """Open or append one incident immediately after a failed ledger write.

    Correlation joins the failed run and the deployment it caused into one
    journey. Signature still preserves 0116's recurring-failure deduplication
    when the same failure arrives under another correlation. Caller detail is
    deliberately ignored: incident facts repeat only typed, redacted ledger
    fields and point back to the source row for everything else.
    """
    del detail  # Explicitly never copy an open-ended caller string into a fact.
    if state not in ("failed", "timed_out"):
        return None, False, False
    if source_kind not in ("run", "deployment"):
        raise ValueError("incident source must be run or deployment")

    signature_label = source_label if source_kind == "run" else "deployment"
    signature = incident_fingerprint(service_key, environment, signature_label,
                                     failure_class)

    # ONE JOURNEY FIRST. A golden/performance check and the failed deployment
    # that follows it have different signatures but one correlation, so they
    # are two links on one incident rather than two pages for one event.
    # correlation_id has no unique incident index: serialize the first lookup
    # and insert inside the caller's transaction so a simultaneous failed run
    # and deployment cannot both observe absence and open two incidents.
    cur.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0))",
        (correlation_id,))
    cur.execute(
        """select i.id from ops.incident i
            where i.state not in ('resolved','reviewed')
              and (
                i.correlation_id = %s
                or exists (
                  select 1 from ops.incident_fact f
                   where f.incident_id = i.id
                     and f.source_ref = 'correlation:' || %s::text
                )
              )
         order by i.detected_at
            limit 1""",
        (correlation_id, correlation_id))
    row = cur.fetchone()
    if row is None:
        # Preserve 0116's cross-run recurrence rule as the second dedupe key.
        cur.execute(
            """select id from ops.incident
                where signature = %s and state not in ('resolved','reviewed')""",
            (signature,))
        row = cur.fetchone()

    if row is None:
        # Refs share one max+1 namespace across EVERY correlation. Take its
        # global lock only after the correlation lock (one consistent order),
        # then recheck both dedupe keys: another transaction with a different
        # correlation may have opened this signature while we waited.
        cur.execute(
            "select pg_advisory_xact_lock("
            "hashtextextended('ops.incident.ref-allocation', 0))")
        cur.execute(
            """select i.id from ops.incident i
                where i.state not in ('resolved','reviewed')
                  and (
                    i.correlation_id = %s
                    or exists (
                      select 1 from ops.incident_fact f
                       where f.incident_id = i.id
                         and f.source_ref = 'correlation:' || %s::text
                    )
                  )
             order by i.detected_at
                limit 1""",
            (correlation_id, correlation_id))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """select id from ops.incident
                    where signature = %s
                      and state not in ('resolved','reviewed')""",
                (signature,))
            row = cur.fetchone()

    opened = row is None
    if opened:
        cur.execute(
            """insert into ops.incident
                   (ref, correlation_id, title, severity, state, environment,
                    owner_actor, next_action, detected_source, detected_at,
                    source_kind, source_ref, signature, observed_at, expires_at,
                    occurrence_count, first_seen_at, last_seen_at)
               values (%s,%s,%s,%s,'detected',%s,'joe',%s,%s, now(),
                       'collector','tools/ops-record.py immediate',%s, now(),
                       now() + make_interval(hours => %s),
                       1, now(), now())
               returning id""",
            (_next_incident_ref(cur), correlation_id,
             f"{source_label} {state} on {service_key} ({environment})",
             SEVERITY_BY_CRITICALITY.get(criticality, "SEV-3"), environment,
             f"read the trace: ops-record trace {correlation_id}",
             f"{source_kind} ledger: {source_label}", signature,
             MONITORING_HOURS))
        incident_id = cur.fetchone()[0]
    else:
        incident_id = row[0]

    # Signature dedupe can reuse an incident opened by an older correlation.
    # Persist the new journey edge in the exact shape ops.v_trace's 0123
    # recurrence arm consumes, then let later observations in this correlation
    # find the same incident before considering their different signatures.
    correlation_ref = f"correlation:{correlation_id}"
    cur.execute(
        """insert into ops.incident_fact (incident_id, text, source_ref)
           select %s, %s, %s
            where exists (
                    select 1 from ops.incident i
                     where i.id = %s
                       and i.correlation_id is distinct from %s
                  )
              and not exists (
                    select 1 from ops.incident_fact f
                     where f.incident_id = %s and f.source_ref = %s
                  )
           returning id""",
        (incident_id,
         f"recurrence on {service_key} ({environment}) under correlation "
         f"{correlation_id}",
         correlation_ref, incident_id, correlation_id,
         incident_id, correlation_ref))

    cur.execute(
        """insert into ops.incident_link (incident_id, kind, ref, note)
           values (%s, %s, %s, %s)
           on conflict do nothing
           returning incident_id""",
        (incident_id, source_kind, str(source_id), source_label))
    linked = cur.fetchone() is not None
    if linked:
        fact = f"{source_label} on {service_key} ({environment}) ended {state}"
        if failure_class:
            fact += f", failure class {failure_class}"
        cur.execute(
            """insert into ops.incident_fact (incident_id, text, source_ref)
               values (%s, %s, %s)""",
            (incident_id, fact, f"ops.{source_kind}:{source_id}"))

    # A RECURRENCE IS A HEARTBEAT ON THE OPEN ROW, NOT A SECOND PAGE (0293).
    # Before this, the append wrote a fact and nothing else, so all 26 open
    # incidents read alike on 2026-08-23: nothing distinguished partner-ping's
    # 89 failures from a verb that threw once. last_seen_at and the count are
    # the two numbers that separate a fire from a blip, and they cost one UPDATE
    # on a row this transaction already holds.
    #
    # COUNTED OFF THE LINK, not off the call, so it counts DISTINCT evidence
    # rows. ops.incident_link's primary key already refuses a second link to the
    # same run, and the spool replays a row it could not land — without this
    # condition a retried flush would inflate the count while adding no new
    # evidence, and a count that drifts from the evidence is worse than none.
    #
    # IT ALSO ENDS A LIE THE LEDGER WAS TELLING. `assess` moves an incident to
    # monitoring the moment its job goes green and never moves it back, so
    # partner-ping and room-bridge both read `monitoring` while actively
    # failing. A failure recorded against a monitoring incident means the watch
    # found something: the row returns to detected and drops the recovery
    # evidence it can no longer stand on. carr_jobs holds a column-scoped update
    # on exactly these fields (0117, widened for the counters in 0293), so this
    # runs on the collector path without any escalation. This transition can
    # only reopen the watch: it never writes resolved_at/root_cause and never
    # moves state toward resolved. A replay has linked=False, so it cannot
    # invalidate recovery or alter readiness.
    if linked and not opened:
        cur.execute(
            """update ops.incident
                  set occurrence_count = occurrence_count + 1,
                      last_seen_at = now(),
                      observed_at = now(),
                      state = case when state = 'monitoring'
                                   then 'detected' else state end,
                      recovery_evidence_ref = case when state = 'monitoring'
                                   then null else recovery_evidence_ref end,
                      monitoring_until = case when state = 'monitoring'
                                   then null else monitoring_until end,
                      next_action = case when state = 'monitoring'
                                   then 'failed again during its watch — read the '
                                        'trace: ops-record trace ' || %s::text
                                   else next_action end
                where id = %s""",
            (str(correlation_id), incident_id))
    return incident_id, opened, linked


def _healthy_streak(cur, service_key, environment, run_key,
                    limit=HEALTHY_RUNS_TO_CLEAR) -> int:
    """Consecutive succeeded runs of one job, counting back from the latest.

    skipped and cancelled runs are neither health nor failure and are left out
    of the sequence entirely rather than breaking it — a nightly step that was
    gated out is not evidence that anything recovered, and it is not evidence
    that anything broke either.
    """
    cur.execute(
        """select r.state
             from ops.run r join ops.service s on s.id = r.service_id
            where s.key = %s and r.environment = %s and r.run_key = %s
              and r.state in ('succeeded','failed','timed_out')
         order by r.observed_at desc, r.id desc
            limit %s""",
        (service_key, environment, run_key, limit))
    streak = 0
    for (state,) in cur.fetchall():
        if state != "succeeded":
            break
        streak += 1
    return streak


def _record_success_recovery(*, cur, service_key, environment, run_key, run_id):
    """Let a green run speak for the incidents its own job opened.

    CALLED FROM THE WRITE PATH THAT ALREADY RUNS — every `ops-record.py run`
    that records a success, which is also every row tools/ops-spool.py replays
    at its 30-minute flush. No new scheduled job, no agent, no LLM: the council
    ruled out a 21st launchd entry for a fleet that already fails daily, and a
    recovery nobody is running is the state this is fixing.

    Returns [(ref, action, reason)] for the caller to print. Silent when the
    job has no open incidents, which is the overwhelmingly common case and the
    reason this costs one indexed lookup on a healthy fleet.
    """
    prefix = f"{service_key}|{environment}|{run_key}|"
    cur.execute(
        # The predicate is 0116's partial-index predicate verbatim, so this
        # reads the open rows through incident_one_open_per_signature rather
        # than the whole history of everything that ever broke.
        """select ref, state, severity, source_kind, signature, occurrence_count
             from ops.incident
            where state not in ('resolved','reviewed')
              and starts_with(signature, %s)
         order by detected_at""",
        (prefix,))
    cols = ("ref", "state", "severity", "source_kind", "signature",
            "occurrence_count")
    open_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if not open_rows:
        return []

    streak = _healthy_streak(cur, service_key, environment, run_key)
    outcomes = []
    for inc in open_rows:
        action, reason = recovery_decision(inc, streak)
        if action == "none":
            continue
        if action == "monitor":
            # 0117's column grant covers exactly these fields. This is the same
            # move `assess` has always made on a green run, with two
            # differences: it now also refreshes an incident ALREADY in
            # monitoring, so recovery_evidence_ref points at the latest green
            # run instead of the first one ever seen, and it says how far
            # through the sequence the job has got.
            cur.execute(
                """update ops.incident
                      set state = 'monitoring',
                          recovery_evidence_ref = %s,
                          monitoring_until = now() + make_interval(hours => %s),
                          next_action = %s,
                          observed_at = now()
                    where ref = %s and state not in ('resolved','reviewed')""",
                (f"ops.run:{run_id}", MONITORING_HOURS, reason, inc["ref"]))
        elif action == "clear":
            # THE ONLY AUTOMATIC CLOSE, AND THE DATABASE IS STILL THE GATE.
            # carr_jobs has no grant on resolved_at or root_cause and does not
            # get one here (0117 stands). It gets EXECUTE on one SECURITY
            # DEFINER function that re-derives the success sequence from
            # ops.run itself and refuses SEV-1, refuses anything a human
            # opened, and refuses anything whose evidence does not hold up —
            # so a wrong number in this Python cannot close anything.
            #
            # IT TAKES NO STORY FROM ITS CALLER. The root_cause written on a
            # closed incident is built inside the function out of the numbers
            # the function verified, so the sentence a human reads on a closed
            # row can never be one this process made up.
            cur.execute("select ops.clear_recovered_incident(%s, %s)",
                        (inc["ref"], HEALTHY_RUNS_TO_CLEAR))
            if not cur.fetchone()[0]:
                action, reason = "none", "the database declined the close"
        outcomes.append((inc["ref"], action, reason))
    return outcomes


def assess(cur, environment: str | None = None, window_hours: int = 24) -> int:
    """Turn the latest run of every job into incident state. Returns how many
    incidents were OPENED (recoveries and appends are not openings)."""
    opened = 0

    cur.execute(
        """select distinct on (r.service_id, r.environment, r.run_key)
                  r.id, r.service_id, s.key, s.criticality, r.environment,
                  r.run_key, r.state, r.failure_class, r.correlation_id, r.detail
             from ops.run r
             join ops.service s on s.id = r.service_id
            where r.state in ('succeeded','failed','timed_out','cancelled','skipped')
              and r.observed_at > now() - make_interval(hours => %s)
              and (%s::text is null or r.environment = %s)
              and s.retired_at is null
         order by r.service_id, r.environment, r.run_key, r.observed_at desc""",
        (window_hours, environment, environment))
    latest = cur.fetchall()

    for (run_id, service_id_, service_key, criticality, env, run_key,
        state, failure_class, correlation_id, detail) in latest:

        if state in ("failed", "timed_out"):
            _, was_opened, _ = _record_failure_incident(
                cur=cur, correlation_id=correlation_id,
                service_id=service_id_, service_key=service_key,
                criticality=criticality, environment=env,
                source_kind="run", source_id=run_id, source_label=run_key,
                state=state, failure_class=failure_class, detail=detail)
            opened += int(was_opened)

        elif state == "succeeded":
            # RECOVERY IS NOT RESOLUTION — for anything a machine may not close.
            # One green run says the symptom stopped, not that the cause is
            # understood, so a SEV-1 still moves only as far as monitoring and
            # keeps its human close. What changed in 0293 is that a SEV-2 or
            # SEV-3 job incident with a full success SEQUENCE behind it now
            # closes here, because "watch until 24h clear" was a promise this
            # chain could not keep: the sweep that performs it needs the owner
            # credential 0117 withholds from carr_jobs, so bin/nightly.sh has
            # printed `incident sweep (admin capability unavailable)` every
            # night since it shipped and five fully-recovered incidents were
            # still open with twelve green runs behind them on 2026-08-23.
            #
            # SHARED WITH THE `run` PATH ON PURPOSE. This nightly pass is the
            # backstop, not the mechanism: the same function runs inside every
            # successful `ops-record.py run`, so a ticker that recovers at
            # 09:02 clears at 09:06 rather than waiting for the night.
            _record_success_recovery(
                cur=cur, service_key=service_key, environment=env,
                run_key=run_key, run_id=run_id)

        # skipped and cancelled raise nothing. exit 78 means a step ran, found
        # something it needs absent, wrote nothing and said so — alarming on that
        # fires every night until a credential lands, which is exactly how a
        # system teaches people to stop reading its alarms.

    return opened


# THE ELAPSED-WINDOW SWEEP, added 2026-08-14. Every incident carried the line
# "watch until 24h clear, then close with an outcome" and nothing ever performed
# that close: assess only moves a recovered incident INTO monitoring (its update
# targets detected/triaged/investigating/mitigating), so a row already there was
# never touched again, and no job, agent or service entry called the close path.
# The windows expired and the pile stayed, reprinted whole every night.
#
# ONLY THE CLOCK-WATCHING IS AUTOMATED — the judgment is not. This closes an
# incident only when nothing is left to decide: it recovered against real
# evidence, the window ran out, and nothing failed again for the whole window.
# Everything else — never recovered, still flapping, no evidence — stays open
# and keeps its human outcome, which is what the `resolve` subcommand is for.
# A failure that recurs mid-window is the case this must never close, because
# that is precisely the judgment the human close exists to make.
def sweep_decision(incident, job_clean, now=None):
    """(close, reason). Whether one monitoring incident may close on the clock.

    `job_clean` answers the question every incident's own next_action asks —
    "watch until 24h clear" — of the LAST 24 HOURS UP TO NOW: latest run for
    that signature succeeded, and no failed or timed-out run in the window.

    IT IS DELIBERATELY ANCHORED ON NOW, not on the recovery pointer. assess
    writes recovery_evidence_ref when it moves an incident detected -> monitoring
    and never updates it again, so a job that recovers, fails again and recovers
    once more still points at the FIRST recovery. Anchoring there counts long-
    healed failures forever and the incident never closes — which a read-only
    proof against the live ledger showed for three of the eight open rows before
    this shipped. Anchored on now, the test is self-correcting: whatever went
    wrong, 24 clean hours is 24 clean hours.
    """
    now = now or datetime.now(timezone.utc)
    if (incident.get("state") or "") != "monitoring":
        return False, f"state is {incident.get('state')!r}, not monitoring"

    if not incident.get("recovery_evidence_ref"):
        return False, "no recovery evidence recorded, so there is nothing to stand on"

    until = incident.get("monitoring_until")
    if until is None:
        # Never treat a missing window as an elapsed one — that would sweep
        # every incident that has no window at all.
        return False, "no monitoring window recorded"
    if until > now:
        return False, f"monitoring window still open until {until:%Y-%m-%d %H:%M}Z"

    if not job_clean:
        return False, (f"not yet {MONITORING_HOURS}h clear — a failure is still recorded "
                       f"inside the window, or the latest run is not green")

    return True, (f"its monitoring window ended {until:%Y-%m-%d %H:%M}Z and the job has run "
                  f"clean for a full {MONITORING_HOURS}h since — no failure recorded, latest "
                  f"run green. That is the watch every incident asks for. Closed by the "
                  f"elapsed-window sweep.")


def resolve_authority(env):
    """(ok, error). Closing needs owner privileges, and says so before connecting.

    THE DATABASE IS THE GATE, not this function. carr_jobs — the role every
    scheduled job runs as — holds a COLUMN-SCOPED update on ops.incident
    (state, next_action, monitoring_until, recovery_evidence_ref, observed_at,
    expires_at) and no grant at all on resolved_at or root_cause. So a machine
    can move an incident to monitoring and can never mark it closed, which is
    "closing an incident is a human's call" enforced in grants rather than in
    prose. Running this under the job role earns a bare `permission denied for
    table incident` with nothing saying why or what to do instead.
    """
    if not env.get("DATABASE_URL"):
        return False, (
            "closing an incident needs owner privileges, which the job role does not "
            "have: carr_jobs may write state and monitoring_until but has no grant on "
            "resolved_at or root_cause. Run it through the receipted break-glass path, "
            "which supplies the owner credential and logs why:\n\n"
            "  CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason \"why\" \\\n"
            "    run tools/ops-record.py resolve --ref INC-... --root-cause \"...\"")
    return True, None


def cmd_resolve(args) -> int:
    ok, err = resolve_authority(os.environ)
    if not ok:
        print(err, file=sys.stderr)
        return 1
    with connect("owner") as conn, conn.cursor() as cur:
        cur.execute(
            """select ref, state, recovery_evidence_ref, monitoring_until
                 from ops.incident where ref = %s""", (args.ref,))
        row = cur.fetchone()
        if not row:
            print(f"no incident {args.ref}", file=sys.stderr)
            return 1
        incident = dict(zip(("ref", "state", "recovery_evidence_ref", "monitoring_until"), row))

        ok, err, fields = resolve_preconditions(
            incident, root_cause=args.root_cause, evidence=args.evidence,
            allow_early=bool(args.allow_early))
        if not ok:
            print(f"REFUSED — {err}", file=sys.stderr)
            return 1

        cur.execute(
            """update ops.incident
                  set state = 'resolved', resolved_at = %s, monitoring_until = %s,
                      recovery_evidence_ref = %s, root_cause = %s,
                      next_action = 'review and record a followup disposition'
                where ref = %s""",
            (fields["resolved_at"], fields["monitoring_until"],
             fields["recovery_evidence_ref"], fields["root_cause"], args.ref))
        # The reason an early close was allowed belongs ON the incident, not in
        # a shell history nobody reads back.
        if args.allow_early:
            cur.execute(
                """insert into ops.incident_fact (incident_id, text, source_ref)
                   select id, %s, %s from ops.incident where ref = %s""",
                (f"closed before its monitoring window elapsed: {args.allow_early}",
                 "ops-record.py resolve --allow-early", args.ref))
        conn.commit()
    print(f"{args.ref} resolved — {fields['root_cause']}")
    return 0


def cmd_sweep(args) -> int:
    ok, err = resolve_authority(os.environ)
    if not ok:
        print(err, file=sys.stderr)
        return 1
    closed = skipped = 0
    with connect("owner") as conn, conn.cursor() as cur:
        cur.execute(
            """select ref, state, recovery_evidence_ref, monitoring_until, title, signature
                 from ops.incident
                where state = 'monitoring'
                  and (%s::text is null or environment = %s)
             order by ref""",
            (args.environment, args.environment))
        cols = ("ref", "state", "recovery_evidence_ref", "monitoring_until", "title", "signature")
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        for inc in rows:
            # "Failed again during the window" is asked of the RUN LEDGER, not
            # of the incident: the incident is not re-opened by a repeat
            # failure under the same signature (0116's partial unique index
            # collapses it), so the incident alone cannot answer this.
            # "Watch until 24h clear", asked of the run ledger over the last 24
            # hours UP TO NOW — not of the incident, which is not re-opened by a
            # repeat failure under the same signature (0116's partial unique
            # index collapses it), and not of recovery_evidence_ref, which assess
            # never updates after the move into monitoring.
            sig = (inc.get("signature") or "").split("|")
            job_clean = False
            if len(sig) >= 3:
                cur.execute(
                    """select (select r.state from ops.run r
                                 join ops.service s on s.id = r.service_id
                                where s.key=%s and r.environment=%s and r.run_key=%s
                             order by r.observed_at desc limit 1) = 'succeeded'
                          and not exists (
                              select 1 from ops.run r
                                join ops.service s on s.id = r.service_id
                               where s.key=%s and r.environment=%s and r.run_key=%s
                                 and r.state in ('failed','timed_out')
                                 and r.observed_at > now() - make_interval(hours => %s))""",
                    (sig[0], sig[1], sig[2], sig[0], sig[1], sig[2], MONITORING_HOURS))
                job_clean = bool(cur.fetchone()[0])

            close, reason = sweep_decision(inc, job_clean=job_clean)
            if not close:
                skipped += 1
                if args.verbose:
                    print(f"  keep  {inc['ref']}  {reason}")
                continue

            ok2, err2, fields = resolve_preconditions(
                inc, root_cause=reason, evidence=inc["recovery_evidence_ref"])
            if not ok2:
                skipped += 1
                print(f"  keep  {inc['ref']}  {err2}")
                continue

            cur.execute(
                """update ops.incident
                      set state = 'resolved', resolved_at = %s, monitoring_until = %s,
                          recovery_evidence_ref = %s, root_cause = %s,
                          next_action = 'review and record a followup disposition'
                    where ref = %s and state = 'monitoring'""",
                (fields["resolved_at"], fields["monitoring_until"],
                 fields["recovery_evidence_ref"], fields["root_cause"], inc["ref"]))
            closed += 1
            print(f"  close {inc['ref']}  {inc['title']}")
        conn.commit()
    print(f"incident sweep: {closed} closed, {skipped} left open for a human")
    return 0


def cmd_assess(args) -> int:
    with connect("routine") as conn, conn.transaction(), conn.cursor() as cur:
        opened = assess(cur, environment=args.environment, window_hours=args.window_hours)
        cur.execute(
            """select ref, severity, state, title, next_action,
                      occurrence_count, last_seen_at
                 from ops.incident
                where state not in ('resolved','reviewed')
                  and (%s::text is null or environment = %s)
             order by severity, detected_at""",
            (args.environment, args.environment))
        live = cur.fetchall()

    print(f"assess: {opened} incident(s) opened · {len(live)} live incident(s)")
    for ref, severity, state, title, next_action, count, last_seen in live:
        print(f"  {severity}  {state:<12} {ref}  {title}")
        # THE TWO NUMBERS THAT SEPARATE A FIRE FROM A BLIP. Before 0293 this
        # list printed 26 lines that all read alike, so partner-ping failing 89
        # times and a verb that threw once were the same single line and a
        # reader had no way to sort the pile by anything but date.
        if count and count > 1:
            seen = f", last {last_seen:%Y-%m-%d %H:%M}Z" if last_seen else ""
            print(f"        {count} occurrences{seen}")
        if next_action:
            print(f"        next: {next_action}")
    if not live:
        print("  nothing is broken that the ledger can see")
    return 0


# ── PR-NAMED RECOVERY EVIDENCE (WR-000019 slice S1, 2026-08-27) ─────────────
# The gap this closes: recovery_evidence_ref stayed null on almost every
# non-SEV-1 incident until a human typed something in at close time (or `run`/
# `assess` happened to touch the same job again). But the incident is often
# ALREADY fixed by then — a merged PR that names the incident IS the evidence,
# and until now nothing carried that fact onto the row. `resolve`/`sweep` still
# do the actual closing; this only makes sure the evidence is sitting there
# when a human runs one of them, exactly the way assess() above pre-populates
# it for the run-ledger's own recoveries.
#
# THE SAME BOUNDARY AS assess(), REUSED RATHER THAN REINVENTED. connect
# ("routine") is carr_jobs, and 0117's grant already covers exactly the column
# this writes (recovery_evidence_ref) — no new grant, no new credential, no
# new CI secret. carr_jobs cannot write resolved_at or root_cause, so this
# cannot close, reclassify, or theorise about an incident even if the code
# tried to; the interlock 0117 built is what makes that a database fact
# instead of a promise about this file's behaviour.
#
# NEVER CLOBBERS. An incident that already carries recovery_evidence_ref (from
# assess(), a duplicate adjudication, or a human's own resolve --evidence)
# keeps what it has — a PR mentioning INC-x in passing is corroboration, not a
# reason to overwrite investigation another writer already recorded.
INCIDENT_REF_RE = re.compile(r"\bINC-\d{8}-\d{2,}\b")


def extract_incident_refs(*texts: str | None) -> list[str]:
    """Pure. Every distinct INC-<8-digit-day>-<seq> reference named across the
    given strings (title, body, ...), in first-seen order. No I/O, so this is
    exercised directly by tools/test-pr-incident-evidence.py without a gh
    binary, a database, or a network in the loop."""
    seen: dict[str, None] = {}
    for text in texts:
        for match in INCIDENT_REF_RE.findall(text or ""):
            seen.setdefault(match, None)
    return list(seen.keys())


def pr_evidence_decision(existing_evidence: str | None, state: str, pr_number: int) -> tuple[str, str | None]:
    """Pure. (action, evidence_ref) for one incident a merged PR names —
    "attach" carries the ref to write, everything else is a reason to leave
    the row alone. Factored out so tools/test-pr-incident-evidence.py can
    prove the never-clobber and never-close rules directly, the same way
    resolve_preconditions above is tested apart from the connection that
    executes it."""
    if state in ("resolved", "reviewed"):
        return ("skip_closed", None)
    if existing_evidence:
        return ("skip_evidenced", None)
    return ("attach", f"pr:{pr_number}")


def _fetch_prs_for_evidence(pr_number: int | None, since_hours: int, repo: str | None) -> list[dict]:
    """One explicit PR (any state — a pre-merge probe is a legitimate dry run
    and reports "not merged" rather than erroring), or every PR merged within
    the trailing window. Same gh-CLI shape as ops/pr-hygiene-check.py's own
    fetch(), so a reader who knows that one already knows this one."""
    fields = "number,title,body,url,mergedAt,state"
    if pr_number is not None:
        cmd = ["gh", "pr", "view", str(pr_number), "--json", fields]
    else:
        cmd = ["gh", "pr", "list", "--state", "merged", "--limit", "100", "--json", fields]
    if repo:
        cmd += ["--repo", repo]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "gh failed").strip()[:300])
    data = json.loads(p.stdout or ("null" if pr_number is not None else "[]"))
    rows = [data] if pr_number is not None else data
    if pr_number is None and since_hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        rows = [r for r in rows if (merged_at := parse_ts(r.get("mergedAt"))) is not None and merged_at >= cutoff]
    return rows


def cmd_pr_evidence(args) -> int:
    try:
        prs = _fetch_prs_for_evidence(args.pr, args.since_hours, args.repo)
    except Exception as e:                                    # noqa: BLE001
        print(f"ops-record: could not read pull requests ({type(e).__name__}: {e})",
              file=sys.stderr)
        return 1

    attached = already_evidenced = closed_already = not_found = 0
    with connect("routine") as conn, conn.cursor() as cur:
        for pr in prs:
            if pr.get("state") != "MERGED":
                print(f"  skip  PR #{pr.get('number')} is not merged (state={pr.get('state')})")
                continue
            refs = extract_incident_refs(pr.get("title"), pr.get("body"))
            for ref in refs:
                cur.execute(
                    """select recovery_evidence_ref, state from ops.incident where ref = %s""",
                    (ref,))
                row = cur.fetchone()
                if not row:
                    not_found += 1
                    print(f"  ?     {ref} — no such incident (named in PR #{pr['number']}, nothing to attach)")
                    continue
                existing_evidence, state = row
                action, evidence_ref = pr_evidence_decision(existing_evidence, state, pr["number"])
                if action == "skip_closed":
                    closed_already += 1
                    print(f"  --    {ref} is already {state}; evidence not needed")
                    continue
                if action == "skip_evidenced":
                    already_evidenced += 1
                    print(f"  --    {ref} already carries recovery evidence — not overwritten")
                    continue
                cur.execute(
                    """update ops.incident set recovery_evidence_ref = %s
                        where ref = %s and recovery_evidence_ref is null""",
                    (evidence_ref, ref))
                if cur.rowcount:
                    cur.execute(
                        """insert into ops.incident_fact (incident_id, text, source_ref)
                           select id, %s, %s from ops.incident where ref = %s""",
                        (f"recovery evidence attached: PR #{pr['number']} "
                         f"\"{(pr.get('title') or '').strip()[:200]}\" merged "
                         f"{pr.get('mergedAt') or ''} — {pr.get('url') or ''}".strip(),
                         "ops-record.py pr-evidence", ref))
                    attached += 1
                    print(f"  +     {ref}  recovery_evidence_ref = {evidence_ref}")
                else:
                    # Lost a race with another writer between the select and the
                    # update above (assess(), a duplicate adjudication, a human's
                    # resolve) — the row now has evidence from somewhere, which is
                    # the same outcome this call was trying to produce.
                    already_evidenced += 1
                    print(f"  --    {ref} gained recovery evidence from another writer just now — not overwritten")
        conn.commit()

    print(f"pr-evidence: {attached} attached · {already_evidenced} already evidenced · "
          f"{closed_already} already closed · {not_found} not found")
    return 0


# ── deployment ───────────────────────────────────────────────────────────────
def _validate_provider_identity(args, subject: str) -> bool:
    """Keep provider-version evidence exclusive to Production promotion.

    Staging is a source rehearsal.  Giving it the same immutable provider
    version identity as Production would collapse two different facts into one
    label, so non-Production writers refuse the fields instead of ignoring them.
    """
    provider = (getattr(args, "provider", None) or "").strip()
    version_id = (getattr(args, "provider_version_id", None) or "").strip()
    args.provider = provider or None
    args.provider_version_id = version_id or None
    if args.environment == "production":
        if not provider or not version_id:
            print(f"ops-record: Production {subject} requires --provider and "
                  "--provider-version-id", file=sys.stderr)
            return False
        if any(ch.isspace() for ch in provider + version_id):
            print("ops-record: provider identity may not contain whitespace",
                  file=sys.stderr)
            return False
        if provider == "cloudflare-workers":
            try:
                parsed_version = uuid.UUID(version_id)
            except ValueError:
                parsed_version = None
            if parsed_version is None or str(parsed_version) != version_id.lower():
                print("ops-record: cloudflare-workers provider version must be an "
                      "exact UUID", file=sys.stderr)
                return False
            args.provider_version_id = version_id.lower()
    elif provider or version_id:
        print("ops-record: --provider and --provider-version-id are only valid for "
              "Production; staging/rehearsal are source rehearsals, not the same "
              "provider version", file=sys.stderr)
        return False
    return True


def cmd_deployment(args) -> int:
    if not _validate_provider_identity(args, "deployment"):
        return 2
    if args.environment == "production" and not args.release_key:
        print("ops-record: Production deployment requires --release-key", file=sys.stderr)
        return 2
    if args.state == "complete" and not args.read_back_at:
        print("ops-record: complete requires --read-back-at. A successful deploy "
              "command without live verification is Verifying, never Complete.",
              file=sys.stderr)
        return 2
    corr = correlation_of(args.correlation)
    # A TERMINAL DEPLOYMENT HAS ENDED — 0115 refuses one that has not, and the
    # wrapper calling this knows the answer is "just now". Defaulting it here
    # keeps every caller from having to produce a portable timestamp, and the
    # explicit --ended-at still wins when a caller has a truer one.
    ended_at = args.ended_at
    if not ended_at and args.state in ("complete", "failed", "aborted",
                                       "rolled_back", "superseded"):
        ended_at = "now"
    try:
        with connect("write") as conn, conn.transaction(), conn.cursor() as cur:
            sid = service_id(cur, args.service)
            release_id = None
            if getattr(args, "release_key", None):
                cur.execute("select to_regclass('ops.release')")
                if cur.fetchone()[0] is not None:
                    cur.execute(
                        """select id, environment, git_sha, provider, provider_version_id
                             from ops.release where release_key = %s""",
                                (args.release_key,))
                    row = cur.fetchone()
                    if not row:
                        print(f"ops-record: no release {args.release_key!r}", file=sys.stderr)
                        if args.environment == "production":
                            return 2
                    else:
                        release_id = row[0]
                        if args.environment == "production":
                            _, release_env, release_sha, release_provider, release_version = row
                            if (release_env != args.environment
                                    or release_sha != args.git_sha
                                    or release_provider != args.provider
                                    or release_version != args.provider_version_id):
                                print("ops-record: Production deployment identity does not "
                                      "exactly match its release (environment, git SHA, "
                                      "provider, and provider version must all agree)",
                                      file=sys.stderr)
                                return 2
            cur.execute(
                """insert into ops.deployment
                       (correlation_id, service_id, environment, state, git_sha,
                        provider, provider_version_id, release_ref, release_id,
                        deployed_by_actor, verb_count,
                        schema_highest_migration, doctrine_generation,
                        started_at, ended_at, read_back_at, verification_evidence_ref,
                        failure_class, source_kind, source_ref, observed_at, detail)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)
                   returning id""",
                (corr, sid, args.environment, args.state, args.git_sha,
                 args.provider, args.provider_version_id, args.release_ref, release_id,
                 args.actor, args.verb_count,
                 args.schema_migration, args.doctrine_generation,
                 parse_ts(args.started_at), parse_ts(ended_at),
                 parse_ts(args.read_back_at), args.verification_evidence_ref,
                 args.failure_class, args.source_kind, args.source_ref,
                 (args.detail or None)))
            dep_id = cur.fetchone()[0]
            if args.state == "failed":
                cur.execute("select criticality from ops.service where id = %s", (sid,))
                criticality = cur.fetchone()[0]
                _record_failure_incident(
                    cur=cur, correlation_id=corr, service_id=sid,
                    service_key=args.service, criticality=criticality,
                    environment=args.environment, source_kind="deployment",
                    source_id=dep_id, source_label="deployment",
                    state=args.state, failure_class=args.failure_class,
                    detail=args.detail)
    except SystemExit:
        raise
    except Exception as e:                                   # noqa: BLE001
        print(f"ops-record: could not record deployment: "
              f"{str(e).splitlines()[0][:200]}", file=sys.stderr)
        return 1
    print(f"{corr} {dep_id}")
    return 0


def cmd_settings_change(args) -> int:
    """Record one control-plane change. Called by hooks/settings-change-gate.py at
    the moment of the change, never afterwards — the 2026-08-14 ruleset incident
    was an authorised change whose only account died with an interrupted
    session."""
    try:
        with connect("write") as conn, conn.cursor() as cur:
            cur.execute(
                """insert into ops.settings_change
                       (kind, target, reason, outcome, session_id, actor, command, environment)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)
                   returning id""",
                (args.kind, args.target, args.reason, args.outcome, args.session,
                 args.actor, args.command, args.environment))
            row = cur.fetchone()
    except SystemExit:
        raise
    except Exception as e:                                   # noqa: BLE001
        # The change has ALREADY HAPPENED by the time this runs. Failing loudly
        # is right; failing in a way the caller treats as "so it did not happen"
        # is not. The gate spools locally on any non-zero exit.
        print(f"ops-record: could not record the settings change: "
              f"{str(e).splitlines()[0][:200]}", file=sys.stderr)
        return 1
    print(row[0] if row else "")
    return 0


# ── trace ────────────────────────────────────────────────────────────────────
def cmd_trace(args) -> int:
    try:
        corr = str(uuid.UUID(args.correlation))
    except ValueError:
        raise SystemExit(f"ops-record: not a uuid: {args.correlation!r}")

    with connect("read") as conn, conn.cursor() as cur:
        cur.execute(
            """select kind, ref, state, occurred_at, environment, service_key,
                      failure_class, detail, source_kind, source_ref, freshness_state
                 from ops.v_trace
                where correlation_id = %s
             order by occurred_at""",
            (corr,))
        rows = cur.fetchall()

    if not rows:
        print(f"no trace for {corr}")
        return 1

    print(f"trace {corr}   ({len(rows)} link(s))\n")
    for kind, ref, state, at, env, svc, fclass, detail, skind, sref, fresh in rows:
        stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if at else "no time"
        head = f"  {stamp}  {kind:<12} {state:<12} {ref}"
        print(head)
        line2 = f"      {env or 'no environment'}"
        if svc:
            line2 += f" · {svc}"
        line2 += f" · via {skind}:{sref} · {fresh}"
        print(line2)
        if fclass:
            print(f"      failure class: {fclass}")
        if detail:
            print(f"      {detail}")
    return 0


# ── health ───────────────────────────────────────────────────────────────────
def cmd_health(args) -> int:
    with connect("read") as conn, conn.cursor() as cur:
        cur.execute(
            """select service_key, environment, health, freshness_state,
                      last_run_state, last_failure_class, observed_at, criticality
                 from ops.v_service_environment_health
             order by case health when 'unavailable' then 0 when 'degraded' then 1
                                  when 'unknown' then 2 else 3 end,
                      case criticality when 'critical' then 0 when 'high' then 1
                                       when 'medium' then 2 else 3 end,
                      service_key""")
        rows = cur.fetchall()

    if not rows:
        print("no registered service/environment rows — run sync-registry first")
        return 1

    worst = 0
    for key, env, health, fresh, last_state, fclass, observed, crit in rows:
        mark = {"healthy": "ok  ", "degraded": "WARN", "unavailable": "DOWN",
                "unknown": "?   "}.get(health, "?   ")
        if health in ("unavailable",):
            worst = max(worst, 2)
        elif health in ("degraded", "unknown"):
            worst = max(worst, 1)
        seen = observed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if observed else "never"
        line = f"{mark} {key:<24} {env:<11} {health:<12} {fresh:<8} last seen {seen}"
        if last_state and last_state != "succeeded":
            line += f"  [{last_state}{'/' + fclass if fclass else ''}]"
        print(line)
    print(f"\n{len(rows)} registered service/environment row(s). "
          f"`unknown` means nobody has observed it inside its cadence — not that it is well.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync-registry", help="apply ops/config/services.json into the catalog")

    r = sub.add_parser("run", help="append one job or check run")
    r.add_argument("--service", required=True)
    r.add_argument("--key", required=True, help="run_key, e.g. nightly.exports")
    r.add_argument("--state", required=True,
                   choices=["scheduled", "queued", "running", "succeeded", "failed",
                            "timed_out", "cancelled", "skipped", "stale", "unknown"])
    r.add_argument("--kind", default="job", choices=["job", "check"])
    r.add_argument("--environment", default="production",
                   choices=["local", "rehearsal", "staging", "production"])
    r.add_argument("--failure-class")
    r.add_argument("--exit-code", type=int)
    r.add_argument("--attempt", type=int, default=1)
    r.add_argument("--started-at")
    r.add_argument("--ended-at")
    r.add_argument("--duration-ms", type=int,
                   help="measured elapsed milliseconds; derives exact start/end times")
    r.add_argument("--release-key",
                   help="release this assurance run proves, resolved to ops.release.id")
    r.add_argument("--budget-ms", type=int,
                   help="approved performance budget; performance.* only")
    r.add_argument("--correlation")
    r.add_argument("--source-kind", default="wrapper",
                   choices=["collector", "registry", "wrapper", "operator"])
    r.add_argument("--source-ref", required=True, help="e.g. bin/nightly.sh")
    r.add_argument("--expires-in", type=int,
                   help="seconds this observation stays believable; omit to fall back "
                        "to the environment's registered cadence")
    r.add_argument("--evidence-ref")
    r.add_argument("--detail", help="ONE redacted line — no secrets, no client content")

    d = sub.add_parser("deployment", help="append one deployment marker")
    d.add_argument("--service", required=True)
    d.add_argument("--environment", required=True,
                   choices=["local", "rehearsal", "staging", "production"])
    d.add_argument("--state", required=True,
                   choices=["planned", "rehearsing", "ready", "awaiting_approval",
                            "deploying", "verifying", "complete", "failed", "aborted",
                            "rolled_back", "superseded"])
    d.add_argument("--git-sha")
    d.add_argument("--provider", help="Production provider, e.g. cloudflare-workers")
    d.add_argument("--provider-version-id", dest="provider_version_id",
                   help="immutable Production provider version actually promoted")
    d.add_argument("--release-ref", help="SUPERSEDED by --release-key (0131); kept "
                                         "so nothing that wrote it breaks")
    d.add_argument("--release-key", help="the release this deploy is shipping, by key. "
                                         "Resolved to ops.release.id, which is the "
                                         "edge that makes a deploy traceable to an "
                                         "approved plan.")
    d.add_argument("--actor")
    d.add_argument("--verb-count", type=int)
    d.add_argument("--schema-migration")
    d.add_argument("--doctrine-generation", type=int)
    d.add_argument("--started-at")
    d.add_argument("--ended-at")
    d.add_argument("--read-back-at", help="when production was read back; required for complete")
    d.add_argument("--verification-evidence-ref")
    d.add_argument("--failure-class")
    d.add_argument("--correlation")
    d.add_argument("--source-kind", default="wrapper",
                   choices=["collector", "registry", "wrapper", "operator"])
    d.add_argument("--source-ref", default="tools/ops-record.py")
    d.add_argument("--detail")

    sc = sub.add_parser("settings-change", help="record one control-plane change")
    sc.add_argument("--kind", required=True)
    sc.add_argument("--target", required=True)
    sc.add_argument("--reason", required=True)
    sc.add_argument("--outcome", required=True, choices=["applied", "failed"])
    sc.add_argument("--session", required=True)
    sc.add_argument("--actor")
    sc.add_argument("--command")
    sc.add_argument("--environment",
                    choices=["local", "rehearsal", "staging", "production"],
                    default="production")

    t = sub.add_parser("trace", help="read one correlation id back as a chain")
    t.add_argument("correlation")

    sub.add_parser("health", help="derived health of every registered service")
    rs = sub.add_parser("resolve", help="close one incident, with its outcome recorded")
    rs.add_argument("--ref", required=True, help="e.g. INC-20260814-09")
    rs.add_argument("--root-cause", required=True,
                    help="what actually happened — recorded on the incident")
    rs.add_argument("--evidence",
                    help="what shows it is safe to close; required only when the "
                         "incident carries no recovery evidence of its own")
    rs.add_argument("--allow-early", metavar="REASON",
                    help="close before the monitoring window elapses, stating why "
                         "the window cannot apply (e.g. an induced failure that will "
                         "never produce a green run). Recorded as an incident fact.")

    sw = sub.add_parser("sweep", help="close monitoring incidents whose window elapsed clean")
    sw.add_argument("--environment",
                    choices=["local", "rehearsal", "staging", "production"])
    sw.add_argument("--verbose", action="store_true",
                    help="also print why each incident was left open")

    a = sub.add_parser("assess", help="turn the latest run of every job into incident state")
    a.add_argument("--environment",
                   choices=["local", "rehearsal", "staging", "production"])
    a.add_argument("--window-hours", type=int, default=24,
                   help="how far back to look for each job's latest run")

    pe = sub.add_parser("pr-evidence",
                         help="attach recovery evidence to any incident a merged PR names")
    pe.add_argument("--pr", type=int, default=None,
                     help="one explicit PR number; default is every PR merged in --since-hours")
    pe.add_argument("--since-hours", type=int, default=48,
                     help="how far back to look for merged PRs when --pr is not given")
    pe.add_argument("--repo", default=None, help="OWNER/REPO; default is gh's own repo resolution")

    args = p.parse_args()
    return {
        "sync-registry": cmd_sync_registry,
        "run": cmd_run,
        "deployment": cmd_deployment,
        "trace": cmd_trace,
        "health": cmd_health,
        "assess": cmd_assess,
        "pr-evidence": cmd_pr_evidence,
        "resolve": cmd_resolve,
        "sweep": cmd_sweep,
        "settings-change": cmd_settings_change,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
