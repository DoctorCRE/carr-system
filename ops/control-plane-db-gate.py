#!/usr/bin/env python3
# ci: db-gate
"""Database acceptance gate for control-plane admission and job execution.

Runs inside one transaction and rolls back every fixture.  It exercises the
public functions as a caller would, including refusal paths; catalog presence
alone is not evidence that a state machine works.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
import json
from pathlib import Path
from typing import Any, NoReturn

import psycopg
from psycopg import sql
from urllib.parse import urlparse

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.control_plane_scheduler_cutover import scheduler_launchd_rows, scheduler_provider_rows  # noqa: E402

SCHEDULER_REGISTRY_PATH = REPO / "ops" / "config" / "control-plane-scheduler-cutover.v1.json"
WORKFLOW_MANIFEST_PATH = REPO / "ops" / "config" / "control-plane-workflows.v1.json"


REQUIRED_TABLES = [
    "ops.guidance_intake", "ops.rule_admission", "ops.rule_enforcement_point",
    "ops.authority_receipt", "ops.legacy_schedule_disable_receipt", "ops.legacy_schedule_surface_registry",
    "ops.legacy_schedule_provider_contract", "ops.legacy_schedule_launchd_contract",
    "ops.legacy_schedule_observation_receipt", "ops.job_definition", "ops.job",
    "ops.job_attempt", "ops.job_receipt", "ops.cognition_job",
    "ops.cognition_result_cache", "ops.cognition_cache_observation", "ops.workflow_acceptance",
    "ops.provider_route", "ops.provider_observation",
    "ops.cost_reservation", "ops.cost_refusal", "ops.npi_device_evidence_receipt",
]

REQUIRED_FUNCTIONS = [
    "ops.enqueue_job(text,integer,timestamp with time zone,jsonb,text,text)",
    "ops.claim_job(text,integer,integer)",
    "ops.claim_job_mode(text,text,integer,integer)",
    "ops.heartbeat_job(uuid,uuid,integer)",
    "ops.complete_job(uuid,uuid,jsonb,text)",
    "ops.fail_job(uuid,uuid,text,text)",
    "ops.timeout_job(uuid,uuid,text)",
    "ops.reap_expired_jobs()",
    "ops.record_workflow_acceptance(text,text,text,text)",
    "ops.disable_legacy_schedule(text,text,text,text,text,text,text,text,text,text,text)",
    "ops.authority_actor_slug()",
    "ops.select_provider_routes(text[])",
    "ops.get_cognition_cache(text)",
    "ops.put_cognition_cache(text,text,integer,integer,jsonb,text[],integer)",
    "ops.invalidate_cognition_cache(text)",
    "ops.get_cognition_cache_for_job(uuid,uuid,text)",
    "ops.put_cognition_cache_for_job(uuid,uuid,text,text,integer,integer,jsonb,text[],integer)",
    "ops.invalidate_cognition_cache_for_job(uuid,uuid,text)",
    "ops.record_provider_observation(text,text,integer,text,integer,text)",
    "ops.reserve_job_cost(uuid,uuid,text,numeric)",
    "ops.admit_job_cost(uuid,uuid,text,numeric)",
    "ops.record_npi_device_evidence(uuid,timestamp with time zone,text,text,jsonb,text)",
    "ops.record_claude_scheduler_observation(text,text,text,text,boolean,text,text,text,timestamp with time zone,text)",
    "ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamp with time zone,text)",
    "ops.settle_job_cost(uuid,uuid,uuid,integer,integer,numeric)",
    "ops.release_job_cost(uuid,uuid,uuid)",
]


def fail(message: str) -> NoReturn:
    print(f"control-plane-db-gate FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def fetchone_required(row: tuple[Any, ...] | None, context: str) -> tuple[Any, ...]:
    """Turn an unexpected empty SELECT into the gate's normal failure path."""
    if row is None:
        fail(f"expected one row for {context}")
        raise AssertionError("fail exits")
    return row


def _load_control_plane_cli() -> Any:
    """Load tools/control-plane.py by path; its filename is not an identifier."""
    import importlib.util
    path = REPO / "tools" / "control-plane.py"
    spec = importlib.util.spec_from_file_location("carr_control_plane_cli", path)
    if spec is None or spec.loader is None:
        fail("could not load tools/control-plane.py for its census reader")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _definition_fixture(cur: Any, key: str, *, canary: str = "null") -> None:
    """Insert one enabled deterministic job definition as the owner."""
    contract = ('\'{"entrypoint":"fixture"}\'' if canary == "null"
                else f'\'{{"entrypoint":"fixture","canary":{{"enabled":{canary}}}}}\'')
    cur.execute(f"""
        insert into ops.job_definition
          (key,version,enabled,risk,execution_kind,execution_contract,
           recurrence,retry_policy,deduplication,completion_contract,legacy_schedule)
        values (%s,1,true,'green','deterministic',{contract},
                '{{"cron":"* * * * *","timezone":"UTC"}}',
                '{{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}}',
                '{{"key_template":"workflow-truth-fixture"}}',
                '{{"predicate":"fixture","receipt_kind":"fixture"}}',
                '{{"status":"enabled"}}')
    """, (key,))


def _accept(cur: Any, key: str, mode: str, ref: str) -> None:
    cur.execute("""
        insert into ops.workflow_acceptance
          (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
        values (%s,1,%s,'accepted',%s,'db-gate-fixture')
    """, (key, mode, ref))


def _job_count(cur: Any, key: str) -> int:
    cur.execute("select count(*) from ops.job where definition_key=%s", (key,))
    return int(fetchone_required(cur.fetchone(), f"job count for {key}")[0])


def _refused(cur: Any, savepoint: str, sql: str, args: tuple[Any, ...],
             expected: str, context: str) -> None:
    """Assert one enqueue is refused for its exact stated reason."""
    cur.execute(sql_savepoint(savepoint))
    try:
        cur.execute(sql, args)
        fail(f"{context} was admitted")
    except psycopg.Error as exc:
        if expected not in str(exc):
            fail(f"{context} raised the wrong refusal: {exc}")
        cur.execute(sql_rollback_to(savepoint))


def sql_savepoint(name: str) -> Any:
    return sql.SQL("savepoint {}").format(sql.Identifier(name))


def sql_rollback_to(name: str) -> Any:
    return sql.SQL("rollback to savepoint {}").format(sql.Identifier(name))


def _disposable_ci_database(dsn: str) -> bool:
    """The committing race may run ONLY on a dedicated disposable carr_ci database.

    ops/control-plane-ledger-chaos.py points this same gate at an opt-in STAGING
    database and its own contract is rollback-only, so a committing section must
    refuse there rather than leave fixtures behind. This is the identical guard
    ops/calendar-prebrief-projection-local-pg-gate.py uses for the same reason.
    """
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or \
            parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    with psycopg.connect(dsn) as probe, probe.cursor() as cur:
        cur.execute("select current_database(),current_user,current_setting('data_directory'),"
                    "(select rolsuper from pg_roles where rolname=current_user)")
        database_name, role_name, data_directory, is_superuser = fetchone_required(
            cur.fetchone(), "local cluster identity")
    data_path = os.path.realpath(str(data_directory))
    local_disposable = (os.path.isfile(os.path.join(data_path, "PG_VERSION"))
                        and os.path.basename(os.path.dirname(data_path))
                        .startswith("carr-local-pg-ci."))
    hosted_disposable = (os.environ.get("GITHUB_ACTIONS") == "true"
                         and os.environ.get("CI") == "true"
                         and os.environ.get("GITHUB_REPOSITORY") == "jbookout/carr-system"
                         and data_path == "/var/lib/postgresql/data")
    return (database_name == "carr_ci" and role_name == "carr_ci" and is_superuser is True
            and (local_disposable or hosted_disposable))


def workflow_truth_race_gate(dsn: str) -> None:
    """The distinct-identity exclusion under REAL concurrency, two connections.

    One transaction cannot prove this: the loser has to observe the winner's
    COMMITTED row, so the fixtures must commit. It therefore runs only on the
    disposable CI database and cleans up after itself.
    """
    if not _disposable_ci_database(dsn):
        print("control-plane-db-gate: two-connection duplicate_group race NOT RUN — it commits "
              "fixtures and requires the dedicated disposable carr_ci database; the rolled-back "
              "sequential exclusion above still ran")
        return
    group = f"db-gate-race-group-{uuid.uuid4()}"
    twins = [f"db-gate-race-{side}-{uuid.uuid4()}" for side in ("a", "b")]
    slot = "2026-08-15T12:11:00Z"
    try:
        with psycopg.connect(dsn, autocommit=True) as setup, setup.cursor() as cur:
            grant_settable_runtime_roles(cur, "carr_jobs")
            for key, kind in zip(twins, ("launchd", "claude-code")):
                _definition_fixture(cur, key)
                cur.execute("""insert into ops.legacy_schedule_surface_registry
                     (workflow_key,workflow_version,surface_id,locator,scheduler_kind,duplicate_group)
                   values (%s,1,%s,%s,%s,%s)""",
                    (key, f"{key}.surface.v1", f"locator:{key}", kind, group))

        barrier = threading.Barrier(2)
        outcome: dict[str, tuple[str, str]] = {}

        def racer(name: str, key: str) -> None:
            try:
                with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                    set_local_role(cur, "carr_jobs")
                    barrier.wait(timeout=60)
                    # Shadow: the group exclusion is mode-independent, and shadow
                    # needs no acceptance evidence, so this committing test leaves
                    # no append-only row behind to clean up.
                    cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                                (key, slot, '{"fixture":"race"}', f"race-{name}-{uuid.uuid4()}"))
                    job = fetchone_required(cur.fetchone(), "concurrent enqueue result")[0]
                    time.sleep(0.25)
                    conn.commit()
                    outcome[name] = ("committed", str(job))
            except Exception as exc:  # noqa: BLE001 - the refusal IS the result
                outcome[name] = ("refused", str(exc).strip().splitlines()[0])

        threads = [threading.Thread(target=racer, args=(name, key))
                   for name, key in zip(("A", "B"), twins)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        with psycopg.connect(dsn, autocommit=True) as check, check.cursor() as cur:
            cur.execute("""select count(*) from ops.job
                            where definition_key=any(%s) and scheduled_for=%s
                              and state<>'cancelled'""", (twins, slot))
            executable = int(fetchone_required(cur.fetchone(), "race executable count")[0])
        committed = sorted(n for n, (state, _) in outcome.items() if state == "committed")
        refused = sorted(n for n, (state, _) in outcome.items() if state == "refused")
        if executable != 1:
            fail(f"two concurrent connections created {executable} executable jobs for one "
                 f"canonical slot in one duplicate_group: {outcome}")
        if len(committed) != 1 or len(refused) != 1:
            fail(f"the concurrent duplicate_group race had no single deterministic loser: {outcome}")
        if "duplicate_group" not in outcome[refused[0]][1]:
            fail(f"the concurrent loser was not refused by the group exclusion: {outcome}")
        print("control-plane-db-gate passed: two concurrent connections, two distinct registered "
              "identities, one duplicate_group, one canonical slot -> exactly one executable job "
              f"and one refused loser ({outcome[refused[0]][1][:80]})")
    finally:
        with psycopg.connect(dsn, autocommit=True) as cleanup, cleanup.cursor() as cur:
            cur.execute("delete from ops.job where definition_key=any(%s)", (twins,))
            cur.execute("delete from ops.legacy_schedule_surface_registry "
                        "where duplicate_group=%s", (group,))
            cur.execute("delete from ops.job_definition where key=any(%s)", (twins,))
            # A COMMITTING test that leaks is worse than one that fails: every
            # gate ordered after this one would inherit two phantom workflows.
            cur.execute("select count(*) from ops.job_definition where key=any(%s)", (twins,))
            leaked = int(fetchone_required(cur.fetchone(), "race fixture cleanup")[0])
            if leaked:
                fail(f"the two-connection race left {leaked} fixture definition(s) behind")


def _accepted_outcome_actionability(cur: Any) -> None:
    """Accepted outcome removes ACTIONABILITY and nothing else.

    The Work Request fixture chain is built with ops/program6-outcome-feedback-gate.py's
    own helpers rather than a second copy of it: one fixture shape, one place to
    correct. Everything here stays inside the caller's rolled-back transaction.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "carr_p6_outcome_gate", REPO / "ops/program6-outcome-feedback-gate.py")
    if spec is None or spec.loader is None:
        fail("could not load the Program 6 outcome fixture helpers")
    p6 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p6)
    from psycopg.types.json import Jsonb

    def refs():
        return [row[0] for row in cur.execute(
            "select ref from ops.current_sourced_work_requests('carr-internal')").fetchall()]

    p6.authority_roles(cur)
    joe_id = fetchone_required(cur.execute(
        "select id from actor where slug='joe' and active and kind='human'").fetchone(),
        "joe actor")[0]
    source_section, source_rev, origin_ref, runbook_ref = p6.fixture(cur, joe_id)
    set_local_role(cur, "carr_writer")
    request_id, ref, _, captured_version, *_ = fetchone_required(cur.execute(
        """select * from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)""",
        (origin_ref, "F09 queue actionability", "Observe one result",
         Jsonb([{"id": "OBSERVED", "text": "The stated outcome has an evidence reference"}]),
         source_section, source_rev, uuid.uuid4())).fetchone(), "captured work request")
    cur.execute("reset role")
    triaged_version = p6.as_authority(cur, "dell",
        "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)",
        (ref, captured_version, uuid.uuid4()))[3]
    set_local_role(cur, "carr_writer")
    plan = fetchone_required(cur.execute(
        """select * from ops.propose_sourced_work_request_plan(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ref, triaged_version, "Observe one bounded result", runbook_ref, Jsonb([]),
         "safe:recovery:stop", "safe:observability:record",
         Jsonb({"max_steps": 2, "max_duration_minutes": 15}), uuid.uuid4())).fetchone(), "plan")
    cur.execute("reset role")
    ready = p6.as_authority(cur, "dell",
        "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
        (ref, triaged_version, plan[2], uuid.uuid4()))
    ready_version = ready[3]
    if ref not in refs():
        fail("a ready sourced Work Request with no accepted outcome was not actionable")
    state_before = fetchone_required(cur.execute(
        "select state,version from ops.work_request where id=%s", (request_id,)).fetchone(),
        "work request state")
    set_local_role(cur, "carr_writer")
    proposal = p6.propose(cur, ref, ready_version, plan[2], uuid.uuid4())
    cur.execute("reset role")
    if ref not in refs():
        fail("a PENDING outcome proposal removed actionability; the exclusion must bind to "
             "the accepted receipt, not to feedback existing at all")
    p6.accept(cur, ref, ready_version, proposal[2], uuid.uuid4(), "dell")
    if ref in refs():
        fail("an accepted outcome did not remove the Work Request from the actionable queue")
    state_after = fetchone_required(cur.execute(
        "select state,version from ops.work_request where id=%s", (request_id,)).fetchone(),
        "work request state after acceptance")
    if state_after != state_before or state_after[0] != "ready":
        fail(f"the queue correction changed Work Request state: {state_before} -> {state_after}")
    if cur.execute("select * from ops.work_request_card(%s,'carr-internal')",
                   (ref,)).fetchone() is None:
        fail("direct card lookup stopped returning a Work Request with an accepted outcome")


def workflow_truth_gate(cur: Any) -> None:
    """V5-F09: prove the projection agrees with the one admission path.

    Everything below runs inside the caller's rolled-back transaction, so this
    gate keeps working under ops/control-plane-ledger-chaos.py's opt-in staging
    drill, which is explicitly rollback-only. No fixture is committed and no
    native scheduler, provider or Work Request is touched.
    """
    from lib.control_plane_workflow_truth import workflow_truth  # noqa: E402

    now = "2026-08-15T12:00:00+00:00"
    slot = "2026-08-15T12:07:00Z"

    # ---- 1. carr_jobs cannot reach ops.job except through ops.enqueue_job ---
    cur.execute("""select has_table_privilege('carr_jobs','ops.job','insert'),
                          has_function_privilege('carr_jobs',
                            'ops.enqueue_job(text,integer,timestamptz,jsonb,text,text)'::regprocedure,
                            'execute')""")
    can_insert, can_enqueue = fetchone_required(cur.fetchone(), "jobs admission seam")
    if can_insert or not can_enqueue:
        fail("ops.enqueue_job is not the sole carr_jobs insertion seam into ops.job")

    # ---- 2. enabled, no evidence: canary AND live refuse, zero rows ---------
    # The existing ladder block above proves the refusal messages. This proves
    # the OTHER half checkable_done asks for: the refusals leave no job behind,
    # so an enabled definition with no accepted evidence has literally never run.
    bare = f"db-gate-truth-bare-{uuid.uuid4()}"
    _definition_fixture(cur, bare)
    set_local_role(cur, "carr_jobs")
    _refused(cur, "truth_bare_canary",
             "select (ops.enqueue_job(%s,1,%s,%s,%s,'canary')).id",
             (bare, slot, '{"fixture":"no-evidence"}', f"truth-bare-canary-{uuid.uuid4()}"),
             "no accepted shadow acceptance evidence",
             "canary enqueue for an enabled definition with no acceptance evidence")
    _refused(cur, "truth_bare_live",
             "select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
             (bare, slot, '{"fixture":"no-evidence"}', f"truth-bare-live-{uuid.uuid4()}"),
             "no accepted canary acceptance evidence",
             "live enqueue for an enabled definition with no acceptance evidence")
    if _job_count(cur, bare) != 0:
        fail("a refused canary/live enqueue still created a job row")

    # SHADOW IS AN EVIDENCE RUN, NOT OPERATION. Enqueuing and even completing a
    # shadow job must not unlock live: only an accepted acceptance row does.
    cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                (bare, slot, '{"fixture":"shadow-run"}', f"truth-bare-shadow-{uuid.uuid4()}"))
    if fetchone_required(cur.fetchone(), "shadow evidence run")[0] is None:
        fail("a shadow evidence run was refused for an enabled definition")
    _refused(cur, "truth_shadow_run_is_not_evidence",
             "select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
             (bare, "2026-08-15T12:08:00Z", '{"fixture":"shadow-ran"}',
              f"truth-bare-live2-{uuid.uuid4()}"),
             "no accepted canary acceptance evidence",
             "live enqueue after an unaccepted shadow RUN")

    # ---- 3. an unregistered definition is refused outright -----------------
    _refused(cur, "truth_unregistered",
             "select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
             (f"db-gate-truth-absent-{uuid.uuid4()}", slot, "{}",
              f"truth-absent-{uuid.uuid4()}"),
             "is not enabled",
             "enqueue for a definition that was never admitted")
    cur.execute("reset role")

    # ---- 4. canonical same-slot delivery: ONE job, conflicting reuse refused -
    # This is the SAME-WORKFLOW case. It is retry deduplication and nothing more.
    same = f"db-gate-truth-same-{uuid.uuid4()}"
    _definition_fixture(cur, same)
    _accept(cur, same, "shadow", f"fixture:truth-same-shadow-{uuid.uuid4()}")
    _accept(cur, same, "canary", f"fixture:truth-same-canary-{uuid.uuid4()}")
    set_local_role(cur, "carr_jobs")
    cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
                (same, slot, '{"fixture":"canonical"}', "truth-same-scheduler-a"))
    first = fetchone_required(cur.fetchone(), "canonical same-slot enqueue")[0]
    cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
                (same, slot, '{"fixture":"canonical"}', "truth-same-scheduler-b"))
    if fetchone_required(cur.fetchone(), "second same-slot delivery")[0] != first:
        fail("two deliveries of one canonical slot produced two jobs")
    if _job_count(cur, same) != 1:
        fail("the canonical same-slot dedup left more than one job row")
    _refused(cur, "truth_conflicting_reuse",
             "select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
             (same, slot, '{"fixture":"changed"}', "truth-same-scheduler-c"),
             "duplicate delivery conflicts with the canonical scheduled job",
             "a conflicting duplicate delivery for one canonical slot")
    cur.execute("reset role")

    # ---- 5. THE DISTINCT-IDENTITY DUPLICATE BOUNDARY -----------------------
    # THE CORRECTION THIS GATE CARRIES, now ENFORCED by migration 0498.
    #
    # Case 4 above proved that ONE workflow identity cannot produce two jobs for
    # one slot. That is the unique (definition_key,definition_version,
    # scheduled_for) index doing retry deduplication, and it proves NOTHING
    # about two DISTINCT registered identities sharing one duplicate_group:
    # their definition_key values differ, so no unique index spans them.
    #
    # 0498 closed that separately, inside ops.enqueue_job itself: an advisory
    # transaction lock on the exact duplicate_group and canonical slot, then a
    # refusal when another identity in the group already holds an executable job
    # for that slot. The two mechanisms stay separately named and separately
    # tested, because conflating them is how the gap survived unnoticed.
    group = f"db-gate-truth-group-{uuid.uuid4()}"
    twins = []
    for side in ("a", "b"):
        key = f"db-gate-truth-twin-{side}-{uuid.uuid4()}"
        _definition_fixture(cur, key)
        _accept(cur, key, "shadow", f"fixture:truth-twin-{side}-shadow-{uuid.uuid4()}")
        _accept(cur, key, "canary", f"fixture:truth-twin-{side}-canary-{uuid.uuid4()}")
        cur.execute("""
            insert into ops.legacy_schedule_surface_registry
              (workflow_key,workflow_version,surface_id,locator,scheduler_kind,duplicate_group)
            values (%s,1,%s,%s,%s,%s)
        """, (key, f"{key}.surface.v1", f"locator:{key}",
              "launchd" if side == "a" else "claude-code", group))
        twins.append(key)

    set_local_role(cur, "carr_jobs")
    cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
                (twins[0], slot, '{"fixture":"duplicate-group"}',
                 f"truth-twin-a-{uuid.uuid4()}"))
    if fetchone_required(cur.fetchone(), "duplicate-group first identity enqueue")[0] is None:
        fail("the first identity in a duplicate_group did not enqueue")
    _refused(cur, "truth_distinct_identity_excluded",
             "select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
             (twins[1], slot, '{"fixture":"duplicate-group"}',
              f"truth-twin-b-{uuid.uuid4()}"),
             "duplicate_group",
             "a SECOND registered identity in one duplicate_group at one canonical slot")
    # The exclusion is scoped to the slot, not to the group: the excluded
    # identity must still be able to run at a different canonical slot.
    cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
                (twins[1], "2026-08-15T12:09:00Z", '{"fixture":"duplicate-group"}',
                 f"truth-twin-b-other-slot-{uuid.uuid4()}"))
    if fetchone_required(cur.fetchone(), "excluded identity at another slot")[0] is None:
        fail("the group exclusion leaked past its canonical slot")
    cur.execute("reset role")
    observed_jobs = sum(
        int(fetchone_required(cur.execute(
            "select count(*) from ops.job where definition_key=%s and scheduled_for=%s",
            (key, slot)).fetchone(), f"slot job count for {key}")[0]) for key in twins)
    cur.execute("""select count(*) from ops.legacy_schedule_surface_registry
                    where duplicate_group=%s""", (group,))
    group_members = int(fetchone_required(cur.fetchone(), "duplicate group membership")[0])
    if group_members != 2:
        fail("the distinct-identity duplicate_group fixture did not register two surfaces")
    if observed_jobs != 1:
        fail("two distinct registered workflow identities in one duplicate_group both "
             f"reached an executable job for one canonical slot ({observed_jobs} jobs)")

    # THE EXACT SEAM. Phase B must add the group-scoped exclusion INSIDE this
    # function -- never a second queue, a trigger on ops.job, or a scheduler-side
    # check -- so that every enqueue path keeps answering to one ladder.
    cur.execute("select pg_get_functiondef("
                "'ops.enqueue_job(text,integer,timestamptz,jsonb,text,text)'::regprocedure)")
    enqueue_body = str(fetchone_required(cur.fetchone(), "enqueue definition")[0]).lower()
    for retained in ("duplicate delivery conflicts with the canonical scheduled job",
                     "no accepted shadow acceptance evidence",
                     "no accepted canary acceptance evidence"):
        if retained not in enqueue_body:
            fail(f"ops.enqueue_job lost its existing guard: {retained}")
    for required in ("legacy_schedule_surface_registry", "duplicate_group",
                     "pg_advisory_xact_lock"):
        if required not in enqueue_body:
            fail(f"ops.enqueue_job lost its distinct-identity group exclusion: {required}")
    # The exclusion must live in the ONE admission function, never in a second
    # queue, a trigger on ops.job, or a scheduler-side check.
    cur.execute("""select count(*) from pg_trigger t join pg_class c on c.oid=t.tgrelid
                    where c.relname='job' and not t.tgisinternal""")
    if int(fetchone_required(cur.fetchone(), "ops.job trigger count")[0]) != 0:
        fail("a trigger on ops.job appeared; the group exclusion belongs inside ops.enqueue_job")

    # ---- 6. accepted outcome feedback: today's queue behaviour, pinned ------
    # ops.current_sourced_work_requests (0245) selects state in
    # ('captured','triaged','ready') with no accepted-outcome exclusion, so a
    # ready request whose outcome was accepted still reads as actionable. The
    # phase-B fix belongs in that function; this gate pins the CURRENT shape so
    # the change is deliberate and card/history behaviour is not weakened here.
    cur.execute("select to_regclass('ops.sourced_work_request_outcome_feedback_acceptance_receipt')")
    if fetchone_required(cur.fetchone(), "outcome acceptance receipt")[0] is None:
        fail("the accepted-outcome receipt relation the phase-B queue fix must read is missing")
    cur.execute("select pg_get_functiondef('ops.current_sourced_work_requests(text)'::regprocedure)")
    queue_body = " ".join(str(fetchone_required(
        cur.fetchone(), "sourced work request queue")[0]).lower().split())
    if "w.state in ('captured', 'triaged', 'ready')" not in queue_body:
        fail("ops.current_sourced_work_requests no longer selects the three actionable states; "
             "re-pin this assertion against its new shape")
    # The exact relation name, not the bare word "outcome": this function already
    # prints 'Record or review outcome evidence' as a human next action, and
    # matching that string would make the held assertion fire on today's source.
    if "sourced_work_request_outcome_feedback_acceptance_receipt" not in queue_body:
        fail("ops.current_sourced_work_requests no longer excludes accepted outcome feedback")
    # ACTIONABILITY ONLY. The correction may not reach Work Request state, and it
    # must bind to the ACCEPTED receipt rather than to feedback existing at all.
    if "update" in queue_body or "insert" in queue_body or "delete" in queue_body:
        fail("the sourced work-request queue projection is no longer read-only")
    _accepted_outcome_actionability(cur)

    # ---- 7. the projection never claims an admission the database refuses ---
    # One adapter, one truth: the census the CLI and Operations health render is
    # replayed here against the rows this transaction actually created, and its
    # admissible_modes are checked against what ops.enqueue_job really did.
    cur.execute("""select key,version,enabled,execution_contract,legacy_disabled_at
                     from ops.job_definition where key = any(%s)""",
                ([bare, same] + twins,))
    definitions = [{"key": key, "version": int(version), "enabled": bool(enabled),
                    "execution_contract": contract,
                    "legacy_disabled_at": None if disabled is None else disabled.isoformat()}
                   for key, version, enabled, contract, disabled in cur.fetchall()]
    cur.execute("""select workflow_key,workflow_version,mode,status
                     from ops.workflow_acceptance where workflow_key = any(%s)""",
                ([bare, same] + twins,))
    acceptances = [{"workflow_key": key, "workflow_version": int(version),
                    "mode": mode, "status": status}
                   for key, version, mode, status in cur.fetchall()]
    cur.execute("""select workflow_key,workflow_version,surface_id,locator,scheduler_kind,
                          duplicate_group
                     from ops.legacy_schedule_surface_registry where workflow_key = any(%s)""",
                ([bare, same] + twins,))
    surfaces = [{"workflow_key": key, "workflow_version": int(version),
                 "surface_id": surface_id, "locator": locator, "scheduler_kind": kind,
                 "duplicate_group": duplicate_group, "disable_receipt_ref": None,
                 "observation": None}
                for key, version, surface_id, locator, kind, duplicate_group in cur.fetchall()]
    census = workflow_truth(
        declarations=[{"key": row["key"], "version": row["version"],
                       "enabled": row["enabled"],
                       "legacy_schedule": {"provider": "launchd", "status": "enabled"}}
                      for row in definitions],
        definitions=definitions, acceptances=acceptances, surfaces=surfaces,
        completion={}, observation_max_age_seconds=900, now=now)
    projected = {row["workflow_key"]: row for row in census["rows"]}
    if "live" in projected[bare]["admissible_modes"] or projected[bare]["operational"]:
        fail("the projection claimed live admission the database refused")
    if not projected[bare]["false_operational"]:
        fail("the projection did not label an enabled definition without evidence false_operational")
    if "live" not in projected[same]["admissible_modes"]:
        fail("the projection refused a live admission the database granted")
    if projected[same]["operational"]:
        fail("the projection called a workflow operational with no completion evidence")
    for key in twins:
        exclusion = projected[key]["duplicate_exclusion"]
        if exclusion["distinct_identity"] != "enforced_by_ops_enqueue_job_group_exclusion":
            fail("the projection did not report the distinct-identity duplicate_group exclusion "
                 "as enforced, though ops.enqueue_job now enforces it")
        if exclusion["same_slot_idempotency"] == exclusion["distinct_identity"]:
            fail("the projection conflated same-slot idempotency with distinct-identity exclusion")
    if census["summary"]["distinct_identity_excluded_groups"] != [group]:
        fail("the census summary did not name the excluded duplicate_group")

    # ---- 8. the census CLI's real reads, against the real schema -----------
    # The projection above was fed rows this gate assembled. That proves the
    # adapter and proves nothing about the SQL `tools/control-plane.py census`
    # actually runs, which would otherwise meet the schema for the first time in
    # front of a partner. The reader is cursor-scoped precisely so it can be
    # exercised here; its Completion Register refusal is savepoint-contained, so
    # a tenant-less read cannot take this gate's fixtures down with it.
    cli = _load_control_plane_cli()
    registry = json.loads(SCHEDULER_REGISTRY_PATH.read_text(encoding="utf-8"))
    manifest_config = json.loads(WORKFLOW_MANIFEST_PATH.read_text(encoding="utf-8"))
    inputs = cli.workflow_census_inputs(cur, registry=registry)
    read_keys = {row["key"] for row in inputs["definitions"]}
    if not {bare, same}.issubset(read_keys) or not set(twins).issubset(read_keys):
        fail("the census reader did not return the definitions this transaction created")
    cur.execute("select 1")
    if fetchone_required(cur.fetchone(), "transaction still usable after the census read")[0] != 1:
        fail("the census reader left the caller's transaction aborted")
    live = cli.workflow_census_projection(inputs, manifest=manifest_config, registry=registry)
    if live["schema_version"] != census["schema_version"]:
        fail("the census CLI and this gate disagree about the workflow-truth schema")
    if len(live["rows"]) != len({(row["workflow_key"], row["workflow_version"])
                                 for row in live["rows"]}):
        fail("the census returned more than one row for a workflow identity")
    expected_source = "unreadable" if inputs["completion"] == cli.UNREADABLE \
        else "ops.completion_projection"
    if live["completion_source"] != expected_source:
        fail("the census misreported where its completion evidence came from")
    if live["completion_source"] == "unreadable" and not live.get("completion_error"):
        fail("an unreadable Completion Register was reported without saying why")
    print("control-plane-db-gate passed: workflow truth agrees with ops.enqueue_job on every "
          "fixture, the census CLI's own reads run against this schema "
          f"(completion evidence: {live['completion_source']}), and both former phase-B "
          "characterizations are now enforced")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        fail("DATABASE_URL is required")
        return 2
    try:
        registry = json.loads(SCHEDULER_REGISTRY_PATH.read_text(encoding="utf-8"))
        manifest = json.loads(WORKFLOW_MANIFEST_PATH.read_text(encoding="utf-8"))
        expected_surfaces = sorted(
            (str(surface["workflow_key"]), int(surface["workflow_version"]), str(surface["surface_id"]),
             str(surface["locator"]), str(surface["scheduler_kind"]), surface.get("duplicate_group"))
            for surface in registry["surfaces"]
        )
        expected_provider = sorted(
            (row[2], row[0], row[1], row[3], row[4], row[5], row[6], row[7])
            for row in scheduler_provider_rows(registry, manifest=manifest, repo=REPO)
        )
        expected_launchd = sorted(
            (row[2], row[0], row[1], row[3], row[4], row[5], json.loads(row[6]),
             row[7], row[8], row[9])
            for row in scheduler_launchd_rows(registry, manifest=manifest, repo=REPO)
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        fail(f"could not load checked-in scheduler surface registry: {exc}")
        return 2

    with rollback_only_connection(dsn) as conn:
        with conn.cursor() as cur:
            for table in REQUIRED_TABLES:
                cur.execute("select to_regclass(%s)", (table,))
                if fetchone_required(cur.fetchone(), f"table {table}")[0] is None:
                    fail(f"missing table {table}")
            for function in REQUIRED_FUNCTIONS:
                cur.execute("select to_regprocedure(%s)", (function,))
                if fetchone_required(cur.fetchone(), f"function {function}")[0] is None:
                    fail(f"missing function {function}")
            cur.execute("""select workflow_key,workflow_version,surface_id,locator,scheduler_kind,duplicate_group
                             from ops.legacy_schedule_surface_registry
                             order by workflow_key,workflow_version,surface_id""")
            actual_surfaces = [tuple(row) for row in cur.fetchall()]
            if actual_surfaces != expected_surfaces:
                fail("scheduler surface registry is empty, stale, or does not exactly match checked-in sync inventory")
            cur.execute("""select surface_id,workflow_key,workflow_version,locator,cron_expression,
                                  timezone,definition_relpath,definition_sha256
                             from ops.legacy_schedule_provider_contract
                             order by surface_id""")
            actual_provider = [tuple(row) for row in cur.fetchall()]
            if actual_provider != expected_provider:
                fail("Claude scheduler provider contract is empty, stale, or not derived from checked-in definitions")
            cur.execute("""select surface_id,workflow_key,workflow_version,locator,repo_plist_relpath,
                                  installed_plist_name,program_arguments,plist_sha256,schedule_sha256,timezone
                             from ops.legacy_schedule_launchd_contract
                             order by surface_id""")
            actual_launchd = [tuple(row) for row in cur.fetchall()]
            if actual_launchd != expected_launchd:
                fail("launchd scheduler contract is empty, stale, or not derived from checked-in plists")
            cur.execute("select has_function_privilege('carr_jobs', "
                        "'ops.record_claude_scheduler_observation(text,text,text,text,boolean,text,text,text,timestamptz,text)'::regprocedure, 'execute')")
            if fetchone_required(cur.fetchone(), "jobs scheduler observation privilege")[0]:
                fail("carr_jobs can mint native scheduler observations")
            cur.execute("select has_function_privilege('carr_jobs', "
                        "'ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamptz,text)'::regprocedure, 'execute')")
            if fetchone_required(cur.fetchone(), "jobs launchd observation privilege")[0]:
                fail("carr_jobs can mint native launchd observations")

            # Renewal source ingress is a separate capability: an externally
            # provisioned LOGIN attestor, paired with one NOLOGIN bundle. The
            # generic jobs identity must not be able to turn a mutable cache
            # into a signed source receipt.
            cur.execute("""
                select
                  (select not rolcanlogin from pg_roles where rolname='carr_renewal_source_attestors'),
                  has_function_privilege('carr_renewal_source_attestors',
                    'ops.ingest_renewal_signed_snapshot(uuid,uuid,uuid,text,text,timestamptz,text,text,jsonb)'::regprocedure,
                    'execute'),
                  has_function_privilege('carr_jobs',
                    'ops.ingest_renewal_signed_snapshot(uuid,uuid,uuid,text,text,timestamptz,text,text,jsonb)'::regprocedure,
                    'execute'),
                  has_function_privilege('carr_jobs',
                    'ops.seal_renewal_decision_source_run(uuid,uuid)'::regprocedure,'execute')
            """)
            renewal_acl = fetchone_required(cur.fetchone(), "renewal source attestor ACL")
            if tuple(renewal_acl) != (True, True, False, False):
                fail("renewal signed ingress is not confined to its exact attestor capability")
            cur.execute("""
                select pg_get_functiondef(
                  'ops.ingest_renewal_signed_snapshot(uuid,uuid,uuid,text,text,timestamptz,text,text,jsonb)'::regprocedure)
            """)
            renewal_function = str(fetchone_required(
                cur.fetchone(), "renewal source ingress definition")[0]).replace(" ", "").lower()
            if ("session_user<>'carr_renewal_source_attestor'" not in renewal_function
                    or "pg_has_role(session_user,'carr_renewal_source_attestors','member')" not in renewal_function):
                fail("renewal signed ingress does not require exact attestor session and bundle")

            # The reaper must lock a finite expired-job set before it changes
            # attempt evidence.  Otherwise a heartbeat can renew the lease
            # between a broad attempt update and the later job transition.
            cur.execute("select pg_get_functiondef('ops.reap_expired_jobs()'::regprocedure)")
            reaper = fetchone_required(cur.fetchone(), "expired-job reaper definition")[0]
            reaper_text = str(reaper).lower()
            lock_at = reaper_text.find("with expired as materialized")
            attempt_at = reaper_text.find("update ops.job_attempt")
            if lock_at < 0 or "for update skip locked" not in reaper_text or attempt_at < lock_at:
                fail("expired-job reaper can mark an attempt before locking its job")
            cur.execute("select has_function_privilege('carr_writer', "
                        "'ops.record_workflow_acceptance(text,text,text,text)'::regprocedure, 'execute')")
            if fetchone_required(cur.fetchone(), "writer workflow acceptance privilege")[0]:
                fail("carr_writer can forge workflow acceptance")
            cur.execute("select has_function_privilege('carr_writer', "
                        "'ops.disable_legacy_schedule(text,text,text,text,text,text,text,text,text,text,text)'::regprocedure, 'execute')")
            if fetchone_required(cur.fetchone(), "writer legacy-disable privilege")[0]:
                fail("carr_writer can retire a legacy schedule")
            cur.execute("select pg_get_functiondef('ops.record_workflow_acceptance(text,text,text,text)'::regprocedure)")
            acceptance = str(fetchone_required(cur.fetchone(), "authority acceptance definition")[0]).lower()
            if "authority_actor_slug()" not in acceptance or "p_actor" in acceptance:
                fail("workflow acceptance does not derive its actor from the authority session")
            if ("p_mode='canary'" not in acceptance.replace(" ", "")
                    or "authority_actor<>'joe'" not in acceptance.replace(" ", "")):
                fail("accepted canary workflow evidence is not Joe-only")

            cur.execute("""
                select 1 from pg_trigger
                 where tgrelid='public.rule'::regclass
                   and tgname='rule_activation_requires_admission'
                   and not tgisinternal
            """)
            if cur.fetchone() is None:
                fail("rule activation trigger is not installed")

            # The runtime role operates through functions and must not own or
            # erase the ledger tables.
            cur.execute("select pg_get_userbyid(relowner) from pg_class where oid='ops.job'::regclass")
            if fetchone_required(cur.fetchone(), "ops.job owner")[0] == "carr_jobs":
                fail("carr_jobs owns ops.job")
            cur.execute("select has_table_privilege('carr_jobs','ops.job','delete')")
            if fetchone_required(cur.fetchone(), "ops.job delete privilege")[0]:
                fail("carr_jobs can delete jobs")
            cur.execute("select has_table_privilege('carr_jobs','ops.job_receipt','update')")
            if fetchone_required(cur.fetchone(), "ops.job_receipt update privilege")[0]:
                fail("carr_jobs can rewrite receipts")
            cur.execute("""
                select has_table_privilege('carr_jobs','ops.device_evidence_receipt','select'),
                       has_table_privilege('carr_jobs','ops.device_evidence_receipt','insert'),
                       has_table_privilege('carr_jobs','ops.device_evidence_receipt','update'),
                       has_table_privilege('carr_jobs','ops.device_evidence_receipt','delete')
            """)
            device_acl = fetchone_required(cur.fetchone(), "device evidence jobs ACL")
            if tuple(device_acl) != (True, False, False, False):
                fail("carr_jobs device evidence access is not exactly read-only")
            cur.execute("""
                select has_function_privilege(
                         'carr_jobs',
                         'ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,
                         'execute'),
                       has_function_privilege(
                         'carr_writer',
                         'ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,
                         'execute'),
                       has_function_privilege(
                         'carr_device_evidence',
                         'ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,
                         'execute')
            """)
            device_exec = fetchone_required(cur.fetchone(), "device evidence execution ACL")
            if tuple(device_exec) != (False, False, True):
                fail("device evidence append authority leaks to a routine role")
            cur.execute("""
                select pg_get_functiondef(
                  'ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure)
            """)
            device_function = str(fetchone_required(
                cur.fetchone(), "device evidence function definition")[0]).lower()
            if "login_role=session_user" not in device_function.replace(" ", ""):
                fail("device evidence function does not derive its principal from session_user")
            cur.execute("""
                select 1 from pg_trigger
                 where tgrelid='ops.device_evidence_receipt'::regclass
                   and tgname='device_evidence_receipt_append_only'
                   and not tgisinternal
            """)
            if cur.fetchone() is None:
                fail("device evidence receipts are not append-only")
            for table in ("ops.guidance_intake", "ops.rule_admission",
                          "ops.rule_enforcement_point"):
                cur.execute("select has_table_privilege('carr_writer',%s,'update')", (table,))
                if not fetchone_required(cur.fetchone(), f"carr_writer update {table}")[0]:
                    fail(f"carr_writer cannot update {table} during admission")
            collector_views = (
                "public.v_control_plane_enrichment_queue",
                "public.v_control_plane_deal_history_queue",
                "public.v_control_plane_content_fuel_rotation",
                "public.v_control_plane_npi_delta",
                "public.v_control_plane_radar_candidates",
                "public.v_control_plane_idea_candidates",
                "public.v_control_plane_social_sources",
                "public.v_control_plane_social_coverage",
                "public.v_control_plane_social_metric_exports",
                "ops.v_control_plane_health_evidence",
                "ops.v_control_plane_capability_candidate",
                "ops.v_control_plane_actionable_loops",
                "ops.v_control_plane_doctrine_due",
                "ops.v_control_plane_doctrine_failures",
                "ops.v_control_plane_system_prune_candidates",
            )
            for relation in collector_views:
                cur.execute("select has_table_privilege('carr_jobs',%s,'select')",(relation,))
                if not fetchone_required(cur.fetchone(), f"carr_jobs select {relation}")[0]:
                    fail(f"carr_jobs cannot read typed workflow evidence from {relation}")
                cur.execute(sql.SQL("select 1 from {} limit 1").format(sql.Identifier(*relation.split("."))))
                cur.fetchall()
            for relation in ("public.v_expired_verification", "public.candidate_pool",
                             "public.content_piece", "public.placement", "public.v_loops"):
                cur.execute("select has_table_privilege('carr_jobs',%s,'select')", (relation,))
                if fetchone_required(cur.fetchone(), f"carr_jobs broad read {relation}")[0]:
                    fail(f"carr_jobs retains broad source-table access to {relation}")

            cur.execute("select has_table_privilege('carr_jobs','ops.npi_device_evidence_receipt','select'), "
                        "has_table_privilege('carr_jobs','ops.npi_device_evidence_receipt','insert'), "
                        "has_function_privilege('carr_jobs',"
                        "'ops.record_npi_device_evidence(uuid,timestamptz,text,text,jsonb,text)'::regprocedure,'execute')")
            if fetchone_required(cur.fetchone(), "NPI device evidence ACL") != (True, False, False):
                fail("jobs role NPI evidence boundary is not read-only")
            cur.execute("select has_function_privilege('carr_writer',"
                        "'ops.record_npi_device_evidence(uuid,timestamptz,text,text,jsonb,text)'::regprocedure,'execute')")
            if fetchone_required(cur.fetchone(), "writer NPI evidence mint privilege")[0]:
                fail("writer can mint NPI device evidence")

            # Collector projections are definer views with PII-minimized
            # columns.  Explicit grants to carr_jobs must not be widened by a
            # deployment's default ACLs.
            for relation in collector_views:
                cur.execute("""
                    select exists (
                      select 1 from pg_class c
                       cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl
                      where c.oid=%s::regclass and acl.grantee=0
                        and acl.privilege_type='SELECT'
                    )
                """, (relation,))
                if fetchone_required(cur.fetchone(), f"PUBLIC collector read {relation}")[0]:
                    fail(f"PUBLIC can read collector projection {relation}")

            # An expired, unstamped, or never-recorded re-verification queue
            # is not a current verified queue.  A model input must therefore
            # refuse it instead of relabeling its provenance as verification.
            # 0387 added bands 1-4 (active vendors/leads/clients with a
            # never-filled profile field -- no category/city/county/
            # verticals/title/org/email/phone on file): those rows carry no
            # record_flag at all, so 'not_recorded' is their honest,
            # DISTINCT reason -- never 'expired'/'unstamped_volatile' (both
            # of which claim a stale record_flag exists) and never silently
            # folded into either. Keep this allowlist identical to
            # lib.control_plane_collectors_records.REVERIFICATION_DUE_REASONS.
            cur.execute("""
                select count(*) from public.v_control_plane_enrichment_queue
                 where current_verification_status='verified'
                    or reverification_due not in ('expired','unstamped_volatile','not_recorded')
            """)
            if fetchone_required(cur.fetchone(), "enrichment truthfulness")[0] != 0:
                fail("expired verification evidence is represented as current verified evidence")

            # Candidate-pool state and vertical are source fields, not a
            # territory policy or provider taxonomy.  Until a reviewed policy
            # projection is installed the NPI path must expose unknowns.
            cur.execute("""
                select count(*) from public.v_control_plane_npi_delta
                 where territory_match is not null or entity_type is not null
            """)
            if fetchone_required(cur.fetchone(), "NPI unknown predicates")[0] != 0:
                fail("NPI projection asserts territory or provider facts without policy evidence")

            # The two rotation lanes are policy configuration only.  No source
            # record currently proves that either has a primary market source.
            cur.execute("""
                select count(*) from public.v_control_plane_content_fuel_rotation
                 where source_class is not null
            """)
            if fetchone_required(cur.fetchone(), "content-fuel source policy")[0] != 0:
                fail("content-fuel projection fabricates a primary-source class")

            # An absent receipt cannot make the backlog vanish, but it also
            # cannot manufacture an execution cap.  Only receipt_bound rows
            # may carry sizing metadata; receipt_missing is a visible,
            # deliberately non-executable queue state.
            cur.execute("""
                select count(*)
                  from public.v_control_plane_deal_history_queue q
                 where q.sizing_state not in ('receipt_bound','receipt_missing')
                    or (q.sizing_state='receipt_missing' and (
                         q.slice_limit is not null
                      or q.enrichment_subject_count is not null
                      or q.enrichment_scheduled_for is not null
                      or q.enrichment_mode is not null))
                    or (q.sizing_state='receipt_bound' and not exists (
                   select 1 from ops.job_receipt r join ops.job j on j.id=r.job_id
                    where j.definition_key='contact-enrichment-weekly'
                      and r.kind='completion'
                      and extract(isodow from j.scheduled_for at time zone 'America/Chicago')=4
                      and jsonb_typeof(r.evidence->'subjects_processed')='number'
                      and (r.evidence->>'subjects_processed')::integer=q.enrichment_subject_count
                      and j.scheduled_for=q.enrichment_scheduled_for
                      and j.mode=q.enrichment_mode
                 ))
            """)
            if fetchone_required(cur.fetchone(), "deal-history enrichment receipt binding")[0] != 0:
                fail("deal-history queue has fabricated sizing state or unbound receipt evidence")

            # Discriminative schema proof: with every receipt temporarily
            # absent, a Salesforce-linked unverified client stays visible but
            # has no execution metadata.  Adding one typed Thursday receipt
            # restores the exact legacy receipt-bound behavior.
            cur.execute("select id from actor where slug='joe'")
            deal_history_actor = fetchone_required(cur.fetchone(), "Joe actor for deal-history fixture")[0]
            cur.execute("savepoint deal_history_queue_fixture")
            try:
                cur.execute("delete from ops.job_receipt")
                cur.execute("""insert into party(kind,name,created_by,updated_by)
                               values ('person','Deal-history receipt fixture',%s,%s) returning id""",
                            (deal_history_actor, deal_history_actor))
                deal_history_party = fetchone_required(cur.fetchone(), "deal-history fixture party")[0]
                cur.execute("""insert into client(party_id,created_by,updated_by)
                               values (%s,%s,%s) returning id""",
                            (deal_history_party, deal_history_actor, deal_history_actor))
                deal_history_client = fetchone_required(cur.fetchone(), "deal-history fixture client")[0]
                cur.execute("""insert into deal(client_id,name,salesforce_id,deal_type,phase,created_by,updated_by)
                               values (%s,'Deal-history receipt fixture','fixture-salesforce-id','lease','closing',%s,%s)""",
                            (deal_history_client, deal_history_actor, deal_history_actor))
                cur.execute("""select count(*), bool_and(sizing_state='receipt_missing'),
                                      bool_and(slice_limit is null and enrichment_subject_count is null
                                               and enrichment_scheduled_for is null and enrichment_mode is null)
                                 from public.v_control_plane_deal_history_queue
                                where subject_type='client' and subject_id=%s""", (deal_history_client,))
                missing_receipt = fetchone_required(cur.fetchone(), "deal-history missing-receipt queue")
                if tuple(missing_receipt) != (1, True, True):
                    fail("missing Thursday receipt hid a deal-history queue row or fabricated a cap")
                fixture_job = uuid.uuid4()
                cur.execute("""insert into ops.job
                                  (id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,
                                   attempt,max_attempts,next_attempt_at,timeout_seconds)
                               values (%s,'contact-enrichment-weekly',1,%s,
                                       (date_trunc('week', now() at time zone 'America/Chicago')
                                        + interval '3 days 10 hours') at time zone 'America/Chicago',
                                       'shadow','queued',0,1,now(),60)""",
                            (fixture_job, str(fixture_job)))
                cur.execute("""insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
                               values (%s,0,'completion','fixture:deal-history-receipt',
                                       '{"subjects_processed":30}'::jsonb)""", (fixture_job,))
                cur.execute("""select count(*), bool_and(sizing_state='receipt_bound'),
                                      bool_and(slice_limit=15 and enrichment_subject_count=30
                                               and enrichment_mode='shadow'
                                               and enrichment_scheduled_for is not null)
                                 from public.v_control_plane_deal_history_queue
                                where subject_type='client' and subject_id=%s""", (deal_history_client,))
                receipt_bound = fetchone_required(cur.fetchone(), "deal-history receipt-bound queue")
                if tuple(receipt_bound) != (1, True, True):
                    fail("typed Thursday receipt did not restore the deal-history execution slice")
            finally:
                cur.execute("rollback to savepoint deal_history_queue_fixture")

            # A new rule cannot activate on prose alone.
            cur.execute("select id from actor where slug='joe'")
            actor = fetchone_required(cur.fetchone(), "Joe actor")[0]
            # A snapshot carries 0194 in its migration ledger but historically
            # omitted its mutable catalog seeds. 0228 must restore exactly the
            # two reviewed global controls before attempting semantic binding.
            cur.execute("""
                select count(*) from ops.enforcement_control_catalog
                 where (control_key='human_authority_runtime'
                        and implementation_ref='migrations/0161_control_plane_authority_boundary.sql; mcp-server/src/mcp.js'
                        and test_ref='mcp-server/test/control-plane-authority-boundary.test.mjs; ops/control-plane-authority-runtime-preflight-selftest.py'
                        and enforcement_class='transactional_schema'
                        and installed and verified_at is not null)
                    or (control_key='platform_metering_pre_dispatch'
                        and implementation_ref='lib/platform_metering.py; ops/platform-metering-gate.py; hooks/guard-unattended.py'
                        and test_ref='ops/platform-metering-gate-selftest.py; ops/platform-metering-policy-selftest.py; ops/guard-selftest.py'
                        and enforcement_class='deny_gate'
                        and installed and verified_at is not null)
            """)
            if fetchone_required(cur.fetchone(), "forward control catalog restoration")[0] != 2:
                fail("0228 did not restore the two exact reviewed control catalog rows")
            # The exact spending rule and decision were captured before the
            # atomic approval architecture existed. Deployment must bind their
            # pinned preimages to the cost gate; a familiar UUID with different
            # words or governance decision must never be blessed.
            cur.execute("""
                insert into rule(id,statement,human_quote,taught_by,status,scope)
                values ('a57d981a-8f6d-4c18-95ee-0e63a5a90b89',
                        'Every metered CARR execution must pass a machine-enforced pre-dispatch budget gate; prose or registry-only guidance does not count as enforcement, and Joe alone may approve exceeding a cap, buying usage credits, or enabling paid overage.',
                        'fixture',%s,'proposed',
                        '{"domain":"system","applies_to":["github","neon","cloudflare","anthropic","openai","google","healthchecks","blotato","make"]}'::jsonb)
            """, (actor,))
            cur.execute("""
                insert into event
                  (id,occurred_at,actor_id,verb,subject_type,subject_id,new_value,
                   cause,human_quote,agent_rationale,idempotency_key)
                values
                  ('f7ea060c-268b-47f1-8a17-7168841b77e0',now(),%s,
                   'log-decision','decision','8b31938a-e2f2-4b8f-9c29-187efa5c1650',
                   jsonb_build_object(
                     'title','Make cost discipline permanent; expire only the temporary emergency restriction',
                     'quote_absent',false,'provenance','rollback DB gate fixture'),
                   'human_stated',
                   'But also, we want a budget rule in affect going forward not just expiring in September. We need to operate the system with cost in mind. Not to the point where it limits the system but just to the point where excessive spending is avoided',
                   'exact pinned decision fixture','db-gate-cost-decision')
            """, (actor,))
            cur.execute("""
                insert into record_source(entity_type,entity_id,source_system,external_key)
                values ('event','f7ea060c-268b-47f1-8a17-7168841b77e0',
                        'decision-history','fixture#db-gate-cost-binding')
            """)
            cur.execute("select ops.sync_system_rule_control_bindings()")
            if fetchone_required(cur.fetchone(), "system-rule binding sync")[0] != 1:
                fail("existing spending rule did not receive its exact installed-control binding")
            cur.execute("""
                select count(*)
                  from ops.rule_control_binding b
                  join rule r on r.id=b.rule_id
                 where b.rule_id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'
                   and b.control_key='platform_metering_pre_dispatch'
                   and b.statement_hash=encode(digest(r.statement,'sha256'),'hex')
                   and b.binding_contract->>'durable_decision_ref'=
                       '8b31938a-e2f2-4b8f-9c29-187efa5c1650'
                   and b.binding_contract->>'decision_event_ref'=
                       'f7ea060c-268b-47f1-8a17-7168841b77e0'
            """)
            if fetchone_required(cur.fetchone(), "system-rule binding readback")[0] != 1:
                fail("spending rule binding does not match the exact statement and decision")
            for savepoint, mutation, params, message in (
                ("narrowed_system_rule_scope",
                 "update rule set scope='{\"workflows\":[\"one-workflow\"]}'::jsonb "
                 "where id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'",
                 (), "system-rule sync accepted narrowed applicability"),
                ("personal_system_rule_audience",
                 "update rule set personal_to=%s "
                 "where id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'",
                 (actor,), "system-rule sync accepted a personal audience"),
            ):
                cur.execute(f"savepoint {savepoint}")
                try:
                    cur.execute(mutation, params)
                    cur.execute("select ops.sync_system_rule_control_bindings()")
                    fail(message)
                except psycopg.Error:
                    cur.execute(f"rollback to savepoint {savepoint}")
            cur.execute("savepoint wrong_system_rule_preimage")
            try:
                cur.execute("""
                    insert into rule(id,statement,human_quote,taught_by,status)
                    values ('ae44e0c0-e773-456c-a85b-2dc4cf4dd49e',
                            'wrong governance statement','fixture',%s,'proposed')
                """, (actor,))
                cur.execute("select ops.sync_system_rule_control_bindings()")
                fail("system-rule sync accepted a known UUID with the wrong statement")
            except psycopg.Error:
                cur.execute("rollback to savepoint wrong_system_rule_preimage")
            grant_settable_runtime_roles(cur, "carr_writer")
            set_local_role(cur, "carr_writer")
            cur.execute("select has_function_privilege(current_user,%s,'execute')",
                        ("ops.sync_system_rule_control_bindings()",))
            if fetchone_required(cur.fetchone(), "system-rule binding ACL")[0] is not False:
                fail("routine writer can install semantic rule bindings")
            cur.execute("reset role")
            cur.execute("""
                insert into rule(statement,human_quote,taught_by,status)
                values ('control-plane fixture','fixture',%s,'proposed') returning id
            """, (actor,))
            rule_id = fetchone_required(cur.fetchone(), "fixture rule")[0]
            cur.execute("savepoint admission_refusal")
            try:
                cur.execute("update rule set status='active',activated_by=%s,activated_at=now() where id=%s",
                            (actor, rule_id))
                fail("rule activated without an admitted contract")
            except psycopg.Error:
                cur.execute("rollback to savepoint admission_refusal")
            cur.execute("savepoint admission_insert_refusal")
            try:
                cur.execute("""
                    insert into rule(statement,human_quote,taught_by,status,activated_by,activated_at)
                    values ('direct active fixture','fixture',%s,'active',%s,now())
                """, (actor,actor))
                fail("rule inserted active without an admitted contract")
            except psycopg.Error:
                cur.execute("rollback to savepoint admission_insert_refusal")

            cur.execute("""
                insert into ops.guidance_intake
                  (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
                values ('rule','human','db-gate','control-plane fixture','normalized',
                        '{"enforcement_class":"machine_enforceable"}',%s)
                returning id
            """, (actor,))
            intake_id = fetchone_required(cur.fetchone(), "fixture guidance intake")[0]
            cur.execute("""
                insert into ops.rule_admission
                  (rule_id,guidance_intake_id,enforcement_class,enforcement_status,binding_moment,
                   applicability,projection,reachability,input_contract,fixture_refs,
                   state,admitted_by,admitted_at)
                values (%s,%s,'machine_enforceable','hard_enforced','before fixture action',
                        '{"workflows":["db-gate"]}',
                        '{"targets":["db-gate"]}', '{"paths":["database"]}',
                        '{"type":"object"}',
                        array['ops/control-plane-db-gate.py'],'admitted',%s,now())
            """, (rule_id, intake_id, actor))
            cur.execute("""
                insert into ops.enforcement_control_catalog
                  (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
                values ('db-gate-fixture','migration:0194',
                        'ops/control-plane-db-gate.py','transactional_schema',true,now())
                on conflict (control_key) do update set installed=true,verified_at=now()
            """)
            cur.execute("""
                insert into ops.rule_control_binding
                  (rule_id,control_key,statement_hash,binding_contract)
                select id,'db-gate-fixture',encode(digest(statement,'sha256'),'hex'),
                       '{"fixture":"control-plane-db-gate"}'::jsonb
                  from rule where id=%s
            """, (rule_id,))
            cur.execute("""
                insert into ops.rule_enforcement_point
                  (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
                values (%s,'db-gate-fixture','migration:0148',
                        'ops/control-plane-db-gate.py','transactional_schema',true,now())
            """, (rule_id,))
            cur.execute("""
                with approved as (
                  select r.id,r.version,encode(digest(r.statement,'sha256'),'hex') statement_hash,
                         jsonb_build_object(
                           'fixture','control-plane-db-gate',
                           'binding_moment','before fixture action',
                           'applicability','{"workflows":["db-gate"]}'::jsonb,
                           'projection','{"targets":["db-gate"]}'::jsonb,
                           'reachability','{"paths":["database"]}'::jsonb,
                           'input_contract','{"type":"object"}'::jsonb) contract
                    from rule r where r.id=%s
                )
                insert into ops.rule_approval_receipt
                  (idempotency_key,rule_id,rule_version,statement_hash,actor_id,policy_kind,
                   enforcement_status,requested_control_keys,installed_control_keys,reason,
                   normalized_contract,contract_hash,evidence_refs)
                select 'db-gate-approval:'||id::text,id,version+1,statement_hash,%s,
                       'machine_enforceable','hard_enforced',array['db-gate-fixture'],
                       array['db-gate-fixture'],'rollback-only enforced activation fixture',
                       contract,encode(digest(contract::text,'sha256'),'hex'),
                       array['ops/control-plane-db-gate.py']
                  from approved
            """, (rule_id, actor))
            cur.execute("""
                insert into ops.authority_receipt
                  (idempotency_key,kind,subject_type,subject_id,actor_id,decision,
                   contract_hash,evidence_refs)
                select 'approval:'||ar.idempotency_key,'activation','rule',ar.rule_id,
                       ar.actor_id,'rollback-only exact approval fixture',
                       ar.contract_hash,ar.evidence_refs
                  from ops.rule_approval_receipt ar where ar.rule_id=%s
            """, (rule_id,))
            # Exercise the trigger as its real firing role, not as owner. A
            # grant that exists only for the migration actor is not a control.
            # The Neon owner credential is intentionally not standing SET-role
            # enabled for runtime bundles. Enable it inside this transaction
            # only, switch to the firing role, then the final rollback erases
            # the temporary membership option along with every fixture.
            grant_settable_runtime_roles(cur, "carr_writer", "carr_jobs")
            set_local_role(cur, "carr_writer")
            cur.execute("update rule set status='active',activated_by=%s,activated_at=now(),enforcement='gate' where id=%s",
                        (actor, rule_id))
            cur.execute("reset role")
            cur.execute("select status from rule where id=%s", (rule_id,))
            if fetchone_required(cur.fetchone(), "activated fixture rule")[0] != "active":
                fail("admitted rule did not activate")
            cur.execute("savepoint active_rule_drift_refusal")
            try:
                set_local_role(cur, "carr_writer")
                cur.execute("update rule set statement=statement||' drift' where id=%s", (rule_id,))
                fail("active rule statement changed under an old approval receipt")
            except psycopg.Error:
                cur.execute("rollback to savepoint active_rule_drift_refusal")
            finally:
                cur.execute("reset role")
            cur.execute("""
                select r.version=ar.rule_version
                  and encode(digest(r.statement,'sha256'),'hex')=ar.statement_hash
                  from rule r join ops.rule_approval_receipt ar on ar.rule_id=r.id
                 where r.id=%s
            """, (rule_id,))
            if fetchone_required(cur.fetchone(), "active rule immutable preimage")[0] is not True:
                fail("active rule version/hash no longer matches its approval receipt")
            cur.execute("select count(*) from ops.applicable_rules('db-gate',null,null) where rule_id=%s",
                        (rule_id,))
            if fetchone_required(cur.fetchone(), "receipt-bound applicable rule")[0] != 1:
                fail("exact active enforced rule is absent from the policy compiler")
            # 0228 may preserve an OLD 0194 pre-activation receipt only through
            # its exact migration-time anchor. A fresh post-0228 approval is
            # already post-version and must never be made to look legacy; nor
            # may a caller claim a matching receipt with a substituted hash.
            for savepoint, sql_text, message in (
                ("fresh_receipt_anchor_refusal", """
                    insert into ops.rule_approval_lifecycle_anchor
                      (approval_receipt_id,rule_id,rule_version_after,statement_hash)
                    select ar.id,ar.rule_id,ar.rule_version,ar.statement_hash
                      from ops.rule_approval_receipt ar where ar.rule_id=%s
                """, "fresh post-version approval was accepted as a legacy anchor"),
                ("mismatched_anchor_refusal", """
                    insert into ops.rule_approval_lifecycle_anchor
                      (approval_receipt_id,rule_id,rule_version_after,statement_hash)
                    select ar.id,ar.rule_id,ar.rule_version+1,repeat('0',64)
                      from ops.rule_approval_receipt ar where ar.rule_id=%s
                """, "mismatched legacy anchor was accepted"),
            ):
                cur.execute(f"savepoint {savepoint}")
                try:
                    cur.execute(sql_text, (rule_id,))
                    fail(message)
                except psycopg.Error:
                    cur.execute(f"rollback to savepoint {savepoint}")
            for savepoint, sql_text, message in (
                ("active_admission_drift_refusal",
                 "update ops.rule_admission set applicability='{}'::jsonb where rule_id=%s",
                 "active rule admission changed under an old approval receipt"),
                ("active_control_removal_refusal",
                 "update ops.rule_enforcement_point set installed=false where rule_id=%s",
                 "active rule enforcement point was removed under an old approval receipt"),
                ("retirement_preimage_drift_refusal",
                 "update rule set status='retired',statement=statement||' drift' where id=%s",
                 "approved rule substance changed during retirement"),
                ("unreceipted_retirement_refusal",
                 "update rule set status='retired' where id=%s",
                 "routine writer retired an approved rule without Joe authority"),
                ("unreceipted_deactivation_refusal",
                 "update rule set status='proposed' where id=%s",
                 "routine writer deactivated an approved rule without Joe authority"),
                ("approved_rule_noop_update_refusal",
                 "update rule set statement=statement where id=%s",
                 "routine writer invalidated an approved rule through a no-op version bump"),
            ):
                cur.execute(f"savepoint {savepoint}")
                try:
                    set_local_role(cur, "carr_writer")
                    cur.execute(sql_text, (rule_id,))
                    fail(message)
                except psycopg.Error:
                    cur.execute(f"rollback to savepoint {savepoint}")
                finally:
                    cur.execute("reset role")

            # A proposed rule has no approval receipt, but its retirement is
            # still a permanent tombstone. Build one through the same receipt
            # precondition then prove a routine writer cannot alter any
            # tombstone field or revive it.
            tombstone_rule = fetchone_required(cur.execute("""
                insert into rule(statement,human_quote,taught_by,status)
                values ('retired rule fixture','fixture',%s,'proposed') returning id
            """, (actor,)).fetchone(), "retired proposed fixture")[0]
            tombstone_at = fetchone_required(cur.execute("""
                insert into ops.rule_retirement_receipt
                  (idempotency_key,rule_id,rule_version_before,rule_version_after,
                   statement_hash,previous_status,actor_id,reason,contract_hash,retired_at)
                select 'db-gate-retirement:'||id::text,id,version,version+1,
                       encode(digest(statement,'sha256'),'hex'),'proposed',%s,
                       'rollback-only retired tombstone fixture',
                       encode(digest('{}'::text,'sha256'),'hex'),now()
                  from rule where id=%s returning retired_at
            """, (actor, tombstone_rule)).fetchone(), "retired proposed receipt")[0]
            cur.execute("""update rule set status='retired',retired_by=%s,retired_at=%s
                           where id=%s""", (actor, tombstone_at, tombstone_rule))
            for savepoint, sql_text, message in (
                ("retired_rule_mutation_refusal",
                 "update rule set statement=statement||' drift' where id=%s",
                 "routine writer changed retired rule statement"),
                ("retired_rule_scope_mutation_refusal",
                 "update rule set scope='{\"workflows\":[\"drift\"]}'::jsonb where id=%s",
                 "routine writer changed retired rule scope"),
                ("retired_rule_actor_mutation_refusal",
                 "update rule set retired_by=null where id=%s",
                 "routine writer changed retired rule actor"),
                ("retired_rule_timestamp_mutation_refusal",
                 "update rule set retired_at=now() where id=%s",
                 "routine writer changed retired rule timestamp"),
                ("retired_rule_revival_refusal",
                 "update rule set status='proposed' where id=%s",
                 "routine writer revived a retired rule"),
            ):
                cur.execute(f"savepoint {savepoint}")
                try:
                    set_local_role(cur, "carr_writer")
                    cur.execute(sql_text, (tombstone_rule,))
                    fail(message)
                except psycopg.Error:
                    cur.execute(f"rollback to savepoint {savepoint}")
                finally:
                    cur.execute("reset role")

            # Disabling a definition is a database-level dispatch fence.  It
            # cancels queued/retry work with immutable evidence, and both claim
            # functions must ignore the old version even when called by an old
            # worker checkout.
            fenced_definition = f"db-gate-fenced-{uuid.uuid4()}"
            cur.execute("""
                insert into ops.job_definition
                  (key,version,enabled,risk,execution_kind,execution_contract,
                   recurrence,retry_policy,deduplication,completion_contract,
                   legacy_schedule)
                values (%s,1,true,'green','deterministic','{"entrypoint":"fixture"}',
                        '{"cron":"* * * * *","timezone":"UTC"}',
                        '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}',
                        '{"key_template":"fixture-fenced"}',
                        '{"predicate":"fixture","receipt_kind":"fixture"}',
                        '{"status":"enabled"}')
            """, (fenced_definition,))
            # ops.enqueue_job (0332) gates canary behind an accepted shadow
            # acceptance row.  carr_jobs/carr_writer hold SELECT only on
            # ops.workflow_acceptance, so this evidence is seeded directly as
            # the owner, before the role switch below -- exactly what a real
            # cutover would have on file before this fixture's canary mode.
            cur.execute("""
                insert into ops.workflow_acceptance
                  (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
                values (%s,1,'shadow','accepted','fixture:db-gate-fenced-shadow','db-gate-fixture')
            """, (fenced_definition,))
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'canary')).id",
                        (fenced_definition, "2026-08-15T11:59:00Z",
                         '{"fixture":"must-not-run"}', f"fixture-fenced-{uuid.uuid4()}"))
            fenced_job = fetchone_required(cur.fetchone(), "fenced fixture enqueue")[0]
            cur.execute("reset role")
            cur.execute("update ops.job_definition set enabled=false where key=%s and version=1",
                        (fenced_definition,))
            cur.execute("select state,attempt,last_failure_class from ops.job where id=%s",
                        (fenced_job,))
            if fetchone_required(cur.fetchone(), "disabled definition job state") != (
                    "cancelled", 0, "definition_disabled"):
                fail("definition disable did not cancel queued canary work before dispatch")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and attempt=0 "
                        "and kind='override' and evidence->>'failure_class'='definition_disabled'",
                        (fenced_job,))
            if fetchone_required(cur.fetchone(), "definition fence receipt")[0] != 1:
                fail("definition disable did not persist one immutable fencing receipt")
            set_local_role(cur, "carr_jobs")
            cur.execute("select * from ops.claim_job_mode('db-gate-old-worker','canary',1,30)")
            if cur.fetchone() is not None:
                fail("old worker claimed a job for a disabled definition")
            cur.execute("reset role")

            # One enqueue identity, one row, even when two schedulers fire it.
            definition = f"db-gate-{uuid.uuid4()}"
            cur.execute("""
                insert into ops.job_definition
                  (key,version,enabled,risk,execution_kind,execution_contract,
                   recurrence,retry_policy,deduplication,completion_contract,
                   legacy_schedule)
                values (%s,1,true,'green','deterministic','{"entrypoint":"fixture"}',
                        '{"cron":"* * * * *","timezone":"UTC"}',
                        '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}',
                        '{"key_template":"fixture"}',
                        '{"predicate":"fixture","receipt_kind":"fixture"}',
                        '{"status":"enabled"}')
            """, (definition,))
            scheduled = "2026-08-15T12:00:00Z"
            args = (definition, 1, scheduled, '{"fixture":true}', "fixture-idem", "shadow")
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,%s,%s,%s,%s,%s)).id", args)
            first = fetchone_required(cur.fetchone(), "first enqueue")[0]
            cur.execute("select (ops.enqueue_job(%s,%s,%s,%s,%s,%s)).id", args)
            second = fetchone_required(cur.fetchone(), "idempotent enqueue")[0]
            if first != second:
                fail("idempotent enqueue produced two jobs")
            duplicate_delivery = (definition, 1, scheduled, '{"fixture":true}',
                                  "fixture-idem-from-second-scheduler", "shadow")
            cur.execute("select (ops.enqueue_job(%s,%s,%s,%s,%s,%s)).id", duplicate_delivery)
            if fetchone_required(cur.fetchone(), "duplicate scheduler enqueue")[0] != first:
                fail("independent scheduler delivery produced a second scheduled job")

            cur.execute("select (ops.claim_job('db-gate-worker',1,30)).*")
            claim = fetchone_required(cur.fetchone(), "fixture job claim")
            if claim[0] != first:
                fail("dispatcher did not claim the queued fixture")
            lease_token = claim[1]
            cur.execute("reset role")

            # Provider health is a finite routing predicate owned by code. An
            # unavailable primary is skipped and the eligible secondary remains.
            cur.execute("""insert into ops.provider_route
                         (route_key,priority,endpoint_ref,monthly_budget_usd)
                       values ('gate-primary',9001,'env:GATE_PRIMARY',0.05),
                              ('gate-secondary',9002,'env:GATE_SECONDARY',1.00),
                              ('gate-third',9003,'env:GATE_THIRD',1.00)""")
            set_local_role(cur, "carr_jobs")
            cur.execute("select ops.record_provider_observation('gate-primary','unavailable',null,'synthetic',300,'db-gate')")
            cur.execute("select ops.record_provider_observation('gate-secondary','healthy',10,null,300,'db-gate')")
            cur.execute("select route_key from ops.select_provider_routes(array['gate-primary','gate-secondary'])")
            if [r[0] for r in cur.fetchall()] != ["gate-secondary"]:
                fail("provider health did not route around unavailable primary")
            cur.execute("reset role")

            # Cache data is proposal-only, provider-neutral, and invalidatable
            # by exact canonical dependency before its TTL expires.
            cur.execute("""insert into ops.cognition_job
                         (key,version,input_schema_version,output_schema_version,input_schema,
                          output_schema,max_tokens,max_cost_usd,timeout_seconds,provider_routes)
                       values ('db-gate-cognition',1,1,1,'{"type":"object"}',
                               '{"type":"object"}',100,1.0,30,array['gate-secondary'])""")
            set_local_role(cur, "carr_jobs")
            cur.execute("select ops.put_cognition_cache('gate-cache','db-gate-cognition',1,1,%s,array['party:P-1'],300)",
                        ('{"route":"gate-secondary","proposal":{}}',))
            cur.execute("select ops.get_cognition_cache('gate-cache')")
            if fetchone_required(cur.fetchone(), "fresh cognition cache")[0] is None:
                fail("fresh cognition cache entry was not readable")
            cur.execute("select ops.invalidate_cognition_cache('party:P-1')")
            if fetchone_required(cur.fetchone(), "cache invalidation")[0] != 1:
                fail("cache dependency invalidation did not name one entry")
            cur.execute("select ops.get_cognition_cache('gate-cache')")
            if fetchone_required(cur.fetchone(), "invalidated cognition cache")[0] is not None:
                fail("invalidated cache entry remained readable")

            # Cache reads and writes from a running cognition job are durable,
            # immutable evidence.  A deterministic job or an arbitrary table
            # insert cannot forge the workflow/job/attempt/mode binding.
            cur.execute("reset role")
            cache_definition = f"db-gate-cache-{uuid.uuid4()}"
            cur.execute("""
                insert into ops.job_definition
                  (key,version,enabled,risk,execution_kind,execution_contract,
                   recurrence,retry_policy,deduplication,completion_contract,legacy_schedule)
                values (%s,1,true,'green','cognition','{"cognition_job":"db-gate-cognition"}',
                        '{"cron":"* * * * *","timezone":"UTC"}',
                        '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}',
                        '{"key_template":"cache-observation-fixture"}',
                        '{"predicate":"fixture","receipt_kind":"fixture"}',
                        '{"status":"enabled"}')
            """, (cache_definition,))
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (cache_definition, "2026-08-15T12:01:00Z", '{}',
                         f"cache-observation-{uuid.uuid4()}"))
            cache_job = fetchone_required(cur.fetchone(), "cache observation job enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-cache-worker','shadow',1,30)")
            cache_claim = fetchone_required(cur.fetchone(), "cache observation job claim")
            if cache_claim[0] != cache_job:
                fail("cache observation worker claimed the wrong job")
            cache_lease = cache_claim[1]
            cur.execute("select cache_state,proposal from ops.get_cognition_cache_for_job(%s,%s,'gate-cache-observed')",
                        (cache_job, cache_lease))
            if fetchone_required(cur.fetchone(), "cache miss observation") != ("miss", None):
                fail("cache miss was not measured before provider dispatch")
            cur.execute("select ops.put_cognition_cache_for_job(%s,%s,'gate-cache-observed',"
                        "'db-gate-cognition',1,1,%s,array['party:P-2'],300)",
                        (cache_job, cache_lease, '{"route":"gate-secondary","proposal":{}}'))
            if not fetchone_required(cur.fetchone(), "cache store observation")[0]:
                fail("cache store was refused for a live cognition lease")
            for bad_key, bad_version, bad_schema in (("db-gate-cognition", 2, 1),
                                                     ("db-gate-cognition", 1, 2),
                                                     ("wrong-cognition", 1, 1)):
                cur.execute("savepoint bad_cache_contract")
                try:
                    cur.execute("select ops.put_cognition_cache_for_job(%s,%s,'bad-cache',%s,%s,%s,'{}',array[]::text[],300)",
                                (cache_job, cache_lease, bad_key, bad_version, bad_schema))
                    fail("wrong cognition contract wrote a cache entry")
                except psycopg.Error:
                    cur.execute("rollback to savepoint bad_cache_contract")
            cur.execute("select cache_state,proposal from ops.get_cognition_cache_for_job(%s,%s,'gate-cache-observed')",
                        (cache_job, cache_lease))
            cache_hit = fetchone_required(cur.fetchone(), "cache hit observation")
            if cache_hit[0] != "hit" or cache_hit[1] is None:
                fail("cache hit did not return measured proposal evidence")
            cur.execute("reset role")
            cur.execute("update ops.cognition_result_cache set output_schema_version=2 where cache_key='gate-cache-observed'")
            set_local_role(cur, "carr_jobs")
            cur.execute("select cache_state,proposal from ops.get_cognition_cache_for_job(%s,%s,'gate-cache-observed')",
                        (cache_job, cache_lease))
            if fetchone_required(cur.fetchone(), "mismatched cache contract")[0] != "miss":
                fail("mismatched cache entry produced a hit")
            cur.execute("reset role")
            cur.execute("update ops.cognition_result_cache set output_schema_version=1 where cache_key='gate-cache-observed'")
            set_local_role(cur, "carr_jobs")
            cur.execute("select ops.invalidate_cognition_cache_for_job(%s,%s,'party:P-2')",
                        (cache_job, cache_lease))
            if fetchone_required(cur.fetchone(), "cache invalidation observation")[0] != 1:
                fail("cache invalidation did not persist one bound observation")
            cur.execute("select cache_state,proposal from ops.get_cognition_cache_for_job(%s,%s,'gate-cache-observed')",
                        (cache_job, cache_lease))
            if fetchone_required(cur.fetchone(), "cache invalidated observation")[0] != "invalidated":
                fail("invalidated cache state was not measured")
            cur.execute("""select observation_kind,workflow_key,workflow_version,mode
                             from ops.cognition_cache_observation
                            where job_id=%s and attempt=1 and cache_key='gate-cache-observed'
                            order by observed_at,observation_kind""", (cache_job,))
            cache_evidence = [tuple(row) for row in cur.fetchall()]
            if {row[0] for row in cache_evidence} != {"miss", "store", "hit", "invalidate", "invalidated"} \
                    or any(row[1:] != (cache_definition, 1, "shadow") for row in cache_evidence):
                fail("cache observations did not bind exact workflow and mode")
            cur.execute("reset role")
            cur.execute("select has_table_privilege('carr_jobs','ops.cognition_cache_observation','insert'),"
                        "has_table_privilege('carr_jobs','ops.cognition_cache_observation','update'),"
                        "has_table_privilege('carr_jobs','ops.cognition_cache_observation','delete')")
            if fetchone_required(cur.fetchone(), "cache observation jobs ACL") != (False, False, False):
                fail("jobs role can directly rewrite cache observations")
            set_local_role(cur, "carr_jobs")
            cur.execute("savepoint cache_wrong_job")
            try:
                cur.execute("select cache_state from ops.get_cognition_cache_for_job(%s,%s,'forbidden')",
                            (first, lease_token))
                fail("deterministic job could create a cache observation")
            except psycopg.Error:
                cur.execute("rollback to savepoint cache_wrong_job")

            # Cost is reserved before provider dispatch and settled against the
            # live lease. A configured monthly ceiling is a durable pre-
            # dispatch refusal, not a rollback-erased exception.
            cur.execute("select ops.reserve_job_cost(%s,%s,'gate-secondary',0.10)",
                        (first, lease_token))
            reservation = fetchone_required(cur.fetchone(), "cost reservation")[0]
            cur.execute("select ops.settle_job_cost(%s,%s,%s,10,5,0.08)",
                        (reservation, first, lease_token))
            if not fetchone_required(cur.fetchone(), "cost settlement")[0]:
                fail("admitted cost reservation did not settle")
            cur.execute("select ops.reserve_job_cost(%s,%s,'gate-third',0.10)",
                        (first, lease_token))
            released_reservation = fetchone_required(cur.fetchone(), "failover cost reservation")[0]
            cur.execute("select ops.release_job_cost(%s,%s,%s)",
                        (released_reservation, first, lease_token))
            if not fetchone_required(cur.fetchone(), "cost reservation release")[0]:
                fail("failed provider reservation was not released for failover")
            cur.execute("select admitted,reservation_id,refusal_id,reason "
                        "from ops.admit_job_cost(%s,%s,'gate-primary',0.10)",
                        (first, lease_token))
            admitted, refused_reservation, refusal_id, refusal_reason = fetchone_required(
                cur.fetchone(), "durable budget refusal")
            if admitted is not False or refused_reservation is not None or refusal_id is None \
                    or refusal_reason != "monthly_budget_exceeded":
                fail("monthly provider budget did not return a typed refusal")
            cur.execute("select count(*) from ops.cost_refusal where id=%s and job_id=%s and attempt=1 "
                        "and route_key='gate-primary' and reason='monthly_budget_exceeded'",
                        (refusal_id, first))
            if fetchone_required(cur.fetchone(), "budget refusal evidence")[0] != 1:
                fail("budget refusal did not persist immutable evidence")
            cur.execute("select refusal_count,refused_estimated_cost_usd from ops.v_cost_refusal_metric "
                        "where month=date_trunc('month',now()) and route_key='gate-primary' "
                        "and reason='monthly_budget_exceeded'")
            metric = fetchone_required(cur.fetchone(), "budget refusal metric")
            if metric[0] != 1 or float(metric[1]) != 0.10:
                fail("budget refusal metric did not report the durable event")

            cur.execute("select ops.heartbeat_job(%s,%s,30)", (first, lease_token))
            if not fetchone_required(cur.fetchone(), "lease heartbeat")[0]:
                fail("lease heartbeat was refused")
            cur.execute("savepoint wrong_lease")
            try:
                cur.execute("select ops.complete_job(%s,%s,'{}','fixture')", (first, uuid.uuid4()))
                fail("wrong lease token completed a job")
            except psycopg.Error:
                cur.execute("rollback to savepoint wrong_lease")
            cur.execute("select ops.complete_job(%s,%s,%s::jsonb,'fixture')",
                        (first, lease_token, '{"ok":true}'))
            cur.execute("select state from ops.job where id=%s", (first,))
            if fetchone_required(cur.fetchone(), "completed job state")[0] != "succeeded":
                fail("job did not reach succeeded")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s", (first,))
            if fetchone_required(cur.fetchone(), "successful job receipt count")[0] != 1:
                fail("successful job did not produce exactly one receipt")
            cur.execute("reset role")

            # This fixture definition is about to enqueue canary and live
            # (here, and again below for the canary-completion and mode-
            # isolation fixtures).  Seed both acceptance tiers as the owner,
            # exactly once, before any of that: carr_jobs/carr_writer hold
            # SELECT only on ops.workflow_acceptance.
            cur.execute("""
                insert into ops.workflow_acceptance
                  (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
                values (%s,1,'shadow','accepted','fixture:db-gate-definition-shadow','db-gate-fixture'),
                       (%s,1,'canary','accepted','fixture:db-gate-definition-canary','db-gate-fixture')
            """, (definition, definition))

            # Shadow, canary, and live replacements at one scheduled instant
            # are distinct ledger identities.  Per-mode scheduler idempotency
            # must not collapse the evidence required for cutover.
            set_local_role(cur, "carr_jobs")
            mode_jobs = []
            for mode in ("shadow", "canary", "live"):
                cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,%s)).id",
                            (definition, "2026-08-15T12:00:15Z", '{"fixture":"mode-identity"}',
                             f"fixture-mode-{mode}-{uuid.uuid4()}", mode))
                mode_jobs.append(fetchone_required(cur.fetchone(), f"{mode} mode enqueue")[0])
            if len(set(mode_jobs)) != 3:
                fail("mode-specific schedules collapsed to one ledger job")
            cur.execute("reset role")
            cur.execute("update ops.job set next_attempt_at='2099-01-01T00:00:00Z' where id=any(%s)",
                        (mode_jobs,))

            # A normal failure reaches retry_wait with a failure receipt, then
            # the same ledger job is reclaimed under a fresh lease and can
            # complete.  It must never become a second scheduler delivery.
            retry_key = f"fixture-retry-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (definition, "2026-08-15T12:00:20Z", '{"fixture":"retry"}', retry_key))
            retry_job = fetchone_required(cur.fetchone(), "retry fixture enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-retry-a','shadow',1,30)")
            retry_claim = fetchone_required(cur.fetchone(), "retry first claim")
            if retry_claim[0] != retry_job:
                fail("dispatcher did not claim retry fixture")
            cur.execute("select ops.fail_job(%s,%s,'fixture_failure','first attempt')",
                        (retry_job, retry_claim[1]))
            if fetchone_required(cur.fetchone(), "retry failure state")[0] != "retry_wait":
                fail("ordinary failure did not enter retry_wait")
            cur.execute("reset role")
            cur.execute("select state,attempt,lease_token from ops.job where id=%s", (retry_job,))
            if fetchone_required(cur.fetchone(), "retry release state") != ("retry_wait", 1, None):
                fail("retry_wait retained a live lease or wrong attempt number")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and attempt=1 and kind='failure'",
                        (retry_job,))
            if fetchone_required(cur.fetchone(), "retry failure receipt")[0] != 1:
                fail("ordinary failure did not create one immutable failure receipt")
            cur.execute("update ops.job set next_attempt_at=now()-interval '1 second' where id=%s", (retry_job,))
            set_local_role(cur, "carr_jobs")
            cur.execute("select * from ops.claim_job_mode('db-gate-retry-b','shadow',1,30)")
            retry_second = fetchone_required(cur.fetchone(), "retry reclaim")
            if retry_second[0] != retry_job or retry_second[1] == retry_claim[1]:
                fail("retry did not reclaim the same job with a new lease token")
            cur.execute("select ops.complete_job(%s,%s,%s::jsonb,'fixture:retry-complete')",
                        (retry_job, retry_second[1], '{"ok":true}'))
            cur.execute("reset role")
            cur.execute("select state,attempt from ops.job where id=%s", (retry_job,))
            if fetchone_required(cur.fetchone(), "retry completed state") != ("succeeded", 2):
                fail("retried job did not complete as attempt two")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and kind in ('failure','completion')",
                        (retry_job,))
            if fetchone_required(cur.fetchone(), "retry receipt chain")[0] != 2:
                fail("retry completion did not retain both failure and completion receipts")

            # Produce a real canary completion receipt.  Human acceptance may
            # name this evidence, but arbitrary receipt strings must not open
            # a legacy cutover.
            canary_key = f"fixture-canary-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'canary')).id",
                        (definition, "2026-08-15T12:00:30Z", '{"fixture":"canary"}', canary_key))
            canary_job = fetchone_required(cur.fetchone(), "canary fixture enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-canary','canary',1,30)")
            canary_claim = fetchone_required(cur.fetchone(), "canary fixture claim")
            if canary_claim[0] != canary_job:
                fail("dispatcher did not claim canary fixture")
            cur.execute("select ops.complete_job(%s,%s,%s::jsonb,'fixture:canary')",
                        (canary_job, canary_claim[1], '{"ok":true}'))

            # A recurring shadow adapter must not steal queued live or canary
            # work merely because it happens to wake first.
            isolated_jobs = []
            for mode, instant in (("live", "2026-08-15T12:00:31Z"),
                                  ("canary", "2026-08-15T12:00:32Z")):
                key = f"fixture-{mode}-isolation-{uuid.uuid4()}"
                cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,%s)).id",
                            (definition, instant, '{"fixture":"mode-isolation"}', key, mode))
                isolated_jobs.append(fetchone_required(cur.fetchone(), f"{mode} isolation enqueue")[0])
            cur.execute("select * from ops.claim_job_mode('db-gate-shadow','shadow',1,30)")
            if cur.fetchone() is not None:
                fail("shadow worker claimed queued live or canary work")
            cur.execute("reset role")
            cur.execute("select count(*) from ops.job where id=any(%s) and state='queued'", (isolated_jobs,))
            if fetchone_required(cur.fetchone(), "mode-isolated queued jobs")[0] != 2:
                fail("shadow mode claim changed queued live/canary work")

            # An abandoned final lease is terminal evidence, not a silent state
            # flip. Reaping it must produce an immutable dead-letter receipt.
            expired_key = f"fixture-expired-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (definition, "2026-08-15T12:01:00Z", '{"fixture":"expired"}', expired_key))
            expired_job = fetchone_required(cur.fetchone(), "expired fixture enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-expiry','shadow',1,30)")
            expired_claim = fetchone_required(cur.fetchone(), "expired fixture claim")
            if expired_claim[0] != expired_job:
                fail("dispatcher did not claim expiry fixture")
            cur.execute("reset role")
            cur.execute("update ops.job set attempt=max_attempts,leased_until=now()-interval '1 second' where id=%s",
                        (expired_job,))
            set_local_role(cur, "carr_jobs")
            cur.execute("savepoint expired_worker_refusal")
            try:
                cur.execute("select ops.fail_job(%s,%s,'late','late worker')",
                            (expired_job,expired_claim[1]))
                fail("expired worker lease was allowed to mutate job state")
            except psycopg.Error:
                cur.execute("rollback to savepoint expired_worker_refusal")
            cur.execute("select ops.reap_expired_jobs()")
            if fetchone_required(cur.fetchone(), "expired job reap")[0] != 1:
                fail("expired lease was not reaped")
            cur.execute("reset role")
            cur.execute("select state from ops.job where id=%s", (expired_job,))
            if fetchone_required(cur.fetchone(), "expired job state")[0] != "dead_lettered":
                fail("exhausted expired lease did not dead-letter")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and kind='dead_letter'",
                        (expired_job,))
            if fetchone_required(cur.fetchone(), "dead letter receipt count")[0] != 1:
                fail("expired final lease did not produce one dead-letter receipt")

            # A retryable expired lease is also durable evidence.  It must
            # retain a timeout receipt before it returns to retry_wait.
            reaper_retry_key = f"fixture-reaper-retry-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (definition, "2026-08-15T12:01:30Z", '{"fixture":"reaper-retry"}', reaper_retry_key))
            reaper_retry_job = fetchone_required(cur.fetchone(), "reaper retry enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-reaper-retry','shadow',1,30)")
            reaper_retry_claim = fetchone_required(cur.fetchone(), "reaper retry claim")
            if reaper_retry_claim[0] != reaper_retry_job:
                fail("dispatcher did not claim retryable-expiry fixture")
            cur.execute("reset role")
            cur.execute("update ops.job set leased_until=now()-interval '1 second' where id=%s", (reaper_retry_job,))
            set_local_role(cur, "carr_jobs")
            cur.execute("select ops.reap_expired_jobs()")
            if fetchone_required(cur.fetchone(), "retryable lease reap")[0] != 1:
                fail("retryable expired lease was not reaped")
            cur.execute("reset role")
            cur.execute("select state from ops.job where id=%s", (reaper_retry_job,))
            if fetchone_required(cur.fetchone(), "retryable lease state")[0] != "retry_wait":
                fail("retryable expired lease did not return to retry_wait")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and attempt=1 and kind='timeout'",
                        (reaper_retry_job,))
            if fetchone_required(cur.fetchone(), "retryable lease timeout receipt")[0] != 1:
                fail("retryable expired lease lacks immutable timeout receipt")

            # A subprocess/provider deadline is distinct from a generic
            # failure and remains visible on the immutable attempt.
            timeout_key = f"fixture-timeout-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (definition,"2026-08-15T12:02:00Z",'{"fixture":"timeout"}',timeout_key))
            timeout_job = fetchone_required(cur.fetchone(), "timeout fixture enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-timeout','shadow',1,30)")
            timeout_claim = fetchone_required(cur.fetchone(), "timeout fixture claim")
            if timeout_claim[0] != timeout_job:
                fail("dispatcher did not claim timeout fixture")
            cur.execute("select ops.timeout_job(%s,%s,'fixture deadline')",
                        (timeout_job,timeout_claim[1]))
            if fetchone_required(cur.fetchone(), "timed-out job state")[0] != "retry_wait":
                fail("timed-out attempt did not enter retry policy")
            cur.execute("reset role")
            cur.execute("select state,failure_class from ops.job_attempt where job_id=%s",
                        (timeout_job,))
            if cur.fetchone() != ("timed_out","execution_timeout"):
                fail("timeout attempt evidence was not preserved")

            # ops.enqueue_job's mode ladder (migration 0332): canary needs an
            # accepted shadow acceptance row and an enabled canary contract;
            # live needs accepted canary, except a workflow whose contract
            # explicitly disables canary, where accepted shadow alone
            # suffices.  Two fixture definitions cover both contract shapes.
            ladder_enabled_definition = f"db-gate-ladder-enabled-{uuid.uuid4()}"
            cur.execute("""
                insert into ops.job_definition
                  (key,version,enabled,risk,execution_kind,execution_contract,
                   recurrence,retry_policy,deduplication,completion_contract,
                   legacy_schedule)
                values (%s,1,true,'green','deterministic','{"entrypoint":"fixture"}',
                        '{"cron":"* * * * *","timezone":"UTC"}',
                        '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}',
                        '{"key_template":"ladder-enabled-fixture"}',
                        '{"predicate":"fixture","receipt_kind":"fixture"}',
                        '{"status":"enabled"}')
            """, (ladder_enabled_definition,))
            ladder_disabled_definition = f"db-gate-ladder-disabled-{uuid.uuid4()}"
            cur.execute("""
                insert into ops.job_definition
                  (key,version,enabled,risk,execution_kind,execution_contract,
                   recurrence,retry_policy,deduplication,completion_contract,
                   legacy_schedule)
                values (%s,1,true,'green','deterministic',
                        '{"entrypoint":"fixture","canary":{"enabled":false,"reason":"db-gate fixture: no isolated destination"}}',
                        '{"cron":"* * * * *","timezone":"UTC"}',
                        '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}',
                        '{"key_template":"ladder-disabled-fixture"}',
                        '{"predicate":"fixture","receipt_kind":"fixture"}',
                        '{"status":"enabled"}')
            """, (ladder_disabled_definition,))

            set_local_role(cur, "carr_jobs")
            cur.execute("savepoint ladder_canary_without_evidence_refusal")
            try:
                cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'canary')).id",
                            (ladder_enabled_definition, "2026-08-15T12:03:00Z",
                             '{"fixture":"ladder-canary-no-evidence"}',
                             f"fixture-ladder-canary-no-evidence-{uuid.uuid4()}"))
                fail("canary enqueue with no acceptance evidence was admitted")
            except psycopg.Error as exc:
                if "no accepted shadow acceptance evidence" not in str(exc):
                    fail(f"canary refusal without evidence raised the wrong exception: {exc}")
                cur.execute("rollback to savepoint ladder_canary_without_evidence_refusal")
            cur.execute("reset role")

            # As the owner: seed accepted shadow for the canary-enabled
            # fixture only.  It must not yet be enough for live.
            cur.execute("""
                insert into ops.workflow_acceptance
                  (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
                values (%s,1,'shadow','accepted','fixture:db-gate-ladder-enabled-shadow','db-gate-fixture')
            """, (ladder_enabled_definition,))

            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'canary')).id",
                        (ladder_enabled_definition, "2026-08-15T12:03:00Z",
                         '{"fixture":"ladder-canary-with-shadow"}',
                         f"fixture-ladder-canary-with-shadow-{uuid.uuid4()}"))
            if fetchone_required(cur.fetchone(), "canary enqueue with accepted shadow")[0] is None:
                fail("canary enqueue with accepted shadow evidence was refused")
            cur.execute("savepoint ladder_live_without_canary_evidence_refusal")
            try:
                cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
                            (ladder_enabled_definition, "2026-08-15T12:03:00Z",
                             '{"fixture":"ladder-live-shadow-only"}',
                             f"fixture-ladder-live-shadow-only-{uuid.uuid4()}"))
                fail("live enqueue with only accepted shadow, canary contractually enabled, was admitted")
            except psycopg.Error as exc:
                if "no accepted canary acceptance evidence" not in str(exc):
                    fail(f"live refusal without canary evidence raised the wrong exception: {exc}")
                cur.execute("rollback to savepoint ladder_live_without_canary_evidence_refusal")
            cur.execute("reset role")

            # As the owner: seed accepted shadow for the canary-disabled
            # fixture.  Its contract's explicit canary.enabled=false makes
            # this the highest evidence it can ever produce, so it alone
            # must satisfy live.
            cur.execute("""
                insert into ops.workflow_acceptance
                  (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
                values (%s,1,'shadow','accepted','fixture:db-gate-ladder-disabled-shadow','db-gate-fixture')
            """, (ladder_disabled_definition,))

            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'live')).id",
                        (ladder_disabled_definition, "2026-08-15T12:03:00Z",
                         '{"fixture":"ladder-live-canary-disabled"}',
                         f"fixture-ladder-live-canary-disabled-{uuid.uuid4()}"))
            if fetchone_required(cur.fetchone(),
                                 "live enqueue for a canary-disabled contract with accepted shadow")[0] is None:
                fail("live enqueue was refused for a contract whose canary is disabled with accepted shadow evidence")
            cur.execute("reset role")

            # Retirement remains shut until accepted shadow AND canary receipts.
            cur.execute("savepoint early_cutover")
            try:
                set_local_role(cur, "carr_writer")
                cur.execute("select ops.disable_legacy_schedule(%s,'surface','locator','too early',"
                            "'native:enabled','native:disabled',null,null,null,null,'db-gate')", (definition,))
                fail("routine writer disabled a legacy schedule")
            except psycopg.Error:
                cur.execute("rollback to savepoint early_cutover")
            set_local_role(cur, "carr_writer")
            cur.execute("savepoint machine_acceptance_refusal")
            try:
                cur.execute("select ops.record_workflow_acceptance(%s,'shadow','accepted','fixture:bad')",
                            (definition,))
                fail("machine actor accepted workflow evidence")
            except psycopg.Error:
                cur.execute("rollback to savepoint machine_acceptance_refusal")
            cur.execute("savepoint fabricated_evidence_refusal")
            try:
                cur.execute("select ops.record_workflow_acceptance(%s,'shadow','accepted','fixture:made-up')",
                            (definition,))
                fail("fabricated receipt reference accepted for cutover")
            except psycopg.Error:
                cur.execute("rollback to savepoint fabricated_evidence_refusal")
            # A real accepted-cutover success requires an externally provisioned
            # carr_authority_joe/dell login DSN.  This owner-session fixture
            # proves only the negative: an unmapped DB session is refused.
            cur.execute("savepoint authority_actor_mismatch")
            try:
                cur.execute("select ops.record_workflow_acceptance(%s,'shadow','accepted','fixture')", (definition,))
                fail("unmapped database session was accepted as human authority")
            except psycopg.Error:
                cur.execute("rollback to savepoint authority_actor_mismatch")
            cur.execute("reset role")

            # carr_authority_joe/dell are externally provisioned LOGIN roles.
            # SET SESSION AUTHORIZATION is superuser-only on managed Postgres,
            # so an owner-driven disposable rebuild must not pretend to be
            # either partner.  The owner mismatch above is this gate's only
            # live authority result.  Positive Joe/Dell identity acceptance
            # requires an externally provisioned real authority-DSN probe;
            # this disposable owner gate does not perform one.

            workflow_truth_gate(cur)

    workflow_truth_race_gate(dsn)
    print("control-plane-db-gate passed: admission, leases, idempotency, receipts and owner cutover refusal exercised")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
