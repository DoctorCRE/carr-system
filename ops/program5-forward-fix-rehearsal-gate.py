#!/usr/bin/env python3
"""Disposable-Postgres contract for Program 5's forward-fix rehearsal path."""

# ci: db-gate
# doctrine: runbook

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

try:
    import psycopg
except ImportError:
    sys.exit("program5-forward-fix-rehearsal-gate: psycopg not installed")


REPO = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("staging_gate", REPO / "ops" / "staging-release-readback-gate.py")
if spec is None or spec.loader is None:
    sys.exit("program5-forward-fix-rehearsal-gate: cannot load base Program 5 fixture")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

PASSES: list[str] = []
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def one(cur):
    row = cur.fetchone()
    if row is None:
        raise AssertionError("database returned no row")
    return row


def refuses(cur, statement: str, params: tuple = (), *, expected_primary: str | None = None) -> bool:
    cur.execute("savepoint forward_fix_refusal")
    try:
        cur.execute(statement, params)
    except psycopg.Error as error:
        cur.execute("rollback to savepoint forward_fix_refusal")
        return (expected_primary is None
                or (error.sqlstate == "P0001" and error.diag.message_primary == expected_primary))
    cur.execute("rollback to savepoint forward_fix_refusal")
    return False


def jobs(cur) -> None:
    cur.execute("set session authorization carr_jobs")


def owner(cur) -> None:
    cur.execute("reset session authorization")


def verifier(cur) -> None:
    cur.execute("set session authorization carr_program5_forward_fix_verifier")


def ensure_disposable_verifier(cur) -> None:
    """Fixture-only named identity; production provisioning remains out of band."""
    cur.execute("create role carr_program5_forward_fix_verifier nologin")
    cur.execute("grant carr_program5_forward_fix_verifiers to carr_program5_forward_fix_verifier")


def forward_fixture(cur, suffix: str) -> dict:
    fixture = base.seed_fixture(cur, f"forward-{suffix}")
    cur.execute("update ops.release set recovery_strategy='forward_fix' where id=%s", (fixture["current_id"],))
    return fixture


def prepare(cur, fixture: dict, idem: uuid.UUID, correlation: uuid.UUID):
    cur.execute("select ops.prepare_staging_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,%s)",
                (idem, correlation, fixture["current_key"], base.CURRENT_SHA))
    return one(cur)[0]


def record(cur, idem: uuid.UUID, provider_version: uuid.UUID, expected_tag: str, *,
           verb_count: int = 211, schema_highest: str | None = None,
           schema_count: int | None = None, schema_ledger: str | None = None):
    cur.execute("""select ops.record_staging_forward_fix_rehearsal(
      %s::uuid,%s::uuid,%s,%s,%s,%s,%s,170,false)""",
                (idem, provider_version, expected_tag, verb_count,
                 schema_highest or base.SCHEMA_HIGHEST_MIGRATION,
                 schema_count or base.SCHEMA_APPLIED_COUNT,
                 schema_ledger or base.SCHEMA_LEDGER_SHA256))
    return one(cur)[0]


def prepare_claim_record(cur, fixture: dict) -> tuple[uuid.UUID, dict]:
    idem = uuid.uuid4()
    prepared = prepare(cur, fixture, idem, uuid.uuid4())
    cur.execute("select ops.claim_staging_forward_fix_rehearsal(%s::uuid)", (idem,))
    claim = one(cur)[0]
    if claim["mutation_allowed"] is not True:
        raise AssertionError("fresh forward-fix rehearsal was not claimed")
    verifier(cur)
    result = record(cur, idem, uuid.uuid4(), prepared["expected_provider_tag"])
    owner(cur)
    return idem, result


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("program5-forward-fix-rehearsal-gate: DATABASE_URL is not set")
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        base.ensure_authority_roles(cur)
        ensure_disposable_verifier(cur)
        # Preserve this fixture-only external identity across the separate
        # concurrency transactions below. Production provisioning is separate.
        conn.commit()
        fixture = forward_fixture(cur, "main")
        idem, correlation = uuid.uuid4(), uuid.uuid4()
        jobs(cur)
        check("rollback candidate cannot enter the forward-fix writer", refuses(
            cur, "select ops.prepare_staging_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,%s)",
            (uuid.uuid4(), uuid.uuid4(), fixture["prior_key"], base.PRIOR_SHA)))
        prepared = prepare(cur, fixture, idem, correlation)
        replay = prepare(cur, fixture, idem, correlation)
        check("forward-fix preparation is idempotent and candidate-bound",
              prepared["replayed"] is False and replay["replayed"] is True
              and prepared["expected_provider_tag"] == replay["expected_provider_tag"])
        check("preparation key refuses a changed correlation", refuses(
            cur, "select ops.prepare_staging_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,%s)",
            (idem, uuid.uuid4(), fixture["current_key"], base.CURRENT_SHA)))
        cur.execute("select ops.claim_staging_forward_fix_rehearsal(%s::uuid)", (idem,))
        first_claim = one(cur)[0]
        cur.execute("select ops.claim_staging_forward_fix_rehearsal(%s::uuid)", (idem,))
        second_claim = one(cur)[0]
        check("claim is single-writer and replay-safe",
              first_claim["mutation_allowed"] is True and second_claim["mutation_allowed"] is False)
        check("wrong staging schema ledger is refused", refuses(
            cur, """select ops.record_staging_forward_fix_rehearsal(
              %s::uuid,%s::uuid,%s,211,%s,%s,%s,170,false)""",
            (idem, uuid.uuid4(), prepared["expected_provider_tag"], base.SCHEMA_HIGHEST_MIGRATION,
             base.SCHEMA_APPLIED_COUNT, "sha256:" + "9" * 64)))
        check("the Production provider version cannot masquerade as staging observation", refuses(
            cur, """select ops.record_staging_forward_fix_rehearsal(
              %s::uuid,%s::uuid,%s,211,%s,%s,%s,170,false)""",
            (idem, uuid.UUID(base.CURRENT_PROVIDER_VERSION), prepared["expected_provider_tag"],
             base.SCHEMA_HIGHEST_MIGRATION, base.SCHEMA_APPLIED_COUNT, base.SCHEMA_LEDGER_SHA256)))
        check("carr_jobs cannot read the verifier declaration projection", refuses(
            cur, "select * from ops.read_staging_forward_fix_rehearsal_declaration(%s::uuid)", (idem,)))
        verifier(cur)
        cur.execute("select * from ops.read_staging_forward_fix_rehearsal_declaration(%s::uuid)", (idem,))
        declaration = one(cur)
        check("exact verifier reads only the six-scalar declaration projection",
              len(declaration) == 6 and declaration[0] == prepared["expected_provider_tag"]
              and declaration[2:] == (1, base.SCHEMA_HIGHEST_MIGRATION,
                                      base.SCHEMA_APPLIED_COUNT, base.SCHEMA_LEDGER_SHA256)
              and isinstance(declaration[1], str) and declaration[1].startswith("sha256:"), repr(declaration))
        owner(cur)
        jobs(cur)
        check("carr_jobs cannot call the final forward-fix recorder", refuses(
            cur, """select ops.record_staging_forward_fix_rehearsal(
              %s::uuid,%s::uuid,%s,211,%s,%s,%s,170,false)""",
            (idem, uuid.uuid4(), prepared["expected_provider_tag"], base.SCHEMA_HIGHEST_MIGRATION,
             base.SCHEMA_APPLIED_COUNT, base.SCHEMA_LEDGER_SHA256)))
        verifier(cur)
        provider_version = uuid.uuid4()
        recorded = record(cur, idem, provider_version, prepared["expected_provider_tag"])
        check("changed result replay is refused", refuses(
            cur, """select ops.record_staging_forward_fix_rehearsal(
              %s::uuid,%s::uuid,%s,212,%s,%s,%s,170,false)""",
            (idem, provider_version, prepared["expected_provider_tag"],
             base.SCHEMA_HIGHEST_MIGRATION, base.SCHEMA_APPLIED_COUNT,
             base.SCHEMA_LEDGER_SHA256)))
        owner(cur)

        # The reusable rollback fixture must still produce the legacy three-step
        # bundle after 0315; its old run key and shape are a regression control.
        rollback_fixture = base.seed_fixture(cur, "rollback-regression")
        base.make_typed_bundle(cur, rollback_fixture)
        cur.execute("""select recovery_strategy,prior_release_id is not null,
                       current_before_receipt_id is not null,forward_fix_result_id is null,
                       declared_schema_highest_migration
                    from ops.staging_recovery_rehearsal_bundle where current_release_id=%s""",
                    (rollback_fixture["current_id"],))
        check("rollback rehearsal accepts the live optional-letter migration boundary",
              one(cur) == ("rollback", True, True, True, base.SCHEMA_HIGHEST_MIGRATION))

        # H5's 0494a correction has to cover both the initial exact-release
        # declaration and the subsequent readback.  The deliberately invalid
        # double suffix stays rejected by both doors.
        malformed_prepare = base.seed_fixture(cur, "optional-letter-prepare")
        cur.execute("set local session_replication_role=replica")
        cur.execute("""update ops.release
                       set schema_highest_migration='0494aa_not_a_migration.sql'
                       where id=%s""", (malformed_prepare["current_id"],))
        cur.execute("set local session_replication_role=origin")
        jobs(cur)
        check("rollback preparation rejects a malformed optional-letter filename", refuses(
            cur, base.prepare_sql(),
            base.prepare_params(malformed_prepare, uuid.uuid4(), "current_before", uuid.uuid4()),
            expected_primary="release does not declare an exact migration/schema set"))
        owner(cur)

        malformed_readback = base.seed_fixture(cur, "optional-letter-readback")
        malformed_attempt, malformed_idem = uuid.uuid4(), uuid.uuid4()
        jobs(cur)
        base.prepare_and_claim(cur, malformed_readback, malformed_attempt, "current_before", malformed_idem)
        cur.execute("""select expected_provider_tag from ops.staging_deployment_attempt
                       where idempotency_key=%s""", (malformed_idem,))
        malformed_tag = one(cur)[0]
        check("rollback readback rejects a malformed optional-letter filename", refuses(
            cur, base.record_sql(),
            (malformed_idem, uuid.uuid4(), malformed_tag, 211,
             "0494aa_not_a_migration.sql", base.SCHEMA_APPLIED_COUNT, 170, False),
            expected_primary="invalid typed staging readback input"))
        cur.execute(base.record_sql(),
                    (malformed_idem, uuid.uuid4(), malformed_tag, 211,
                     base.SCHEMA_HIGHEST_MIGRATION, base.SCHEMA_APPLIED_COUNT, 170, False))
        check("rollback readback accepts the same prepared tag with only a valid filename",
              one(cur)[0]["replayed"] is False)
        owner(cur)

        # LIKE copies the database CHECKs but not append-only triggers. It lets
        # this fixture prove both strategy/writer combinations and rejects
        # forged cross-strategy writers without mutating durable evidence.
        cur.execute("create temporary table forward_fix_bundle_contract "
                    "(like ops.staging_recovery_rehearsal_bundle including constraints) on commit drop")
        cur.execute("insert into forward_fix_bundle_contract select * from ops.staging_recovery_rehearsal_bundle where id=%s",
                    (recorded["bundle_id"],))
        cur.execute("select recovery_strategy,writer_session_user from forward_fix_bundle_contract")
        check("forward-fix bundle records the scoped verifier writer",
              one(cur) == ("forward_fix", "carr_program5_forward_fix_verifier"))
        check("forward-fix bundle refuses a carr_jobs writer", refuses(
            cur, "update forward_fix_bundle_contract set writer_session_user='carr_jobs'"))
        cur.execute("delete from forward_fix_bundle_contract")
        cur.execute("""insert into forward_fix_bundle_contract
                       select * from ops.staging_recovery_rehearsal_bundle
                        where current_release_id=%s""", (rollback_fixture["current_id"],))
        cur.execute("select recovery_strategy,writer_session_user from forward_fix_bundle_contract")
        check("rollback bundle retains carr_jobs writer", one(cur) == ("rollback", "carr_jobs"))
        check("rollback bundle refuses a verifier writer", refuses(
            cur, "update forward_fix_bundle_contract set writer_session_user='carr_program5_forward_fix_verifier'"))

        cur.execute("""select b.recovery_strategy,b.prior_release_id is null,
                       b.forward_fix_result_id is not null,b.candidate_git_sha,b.candidate_provider_version_id::text,
                       b.writer_session_user,
                       r.run_key,r.environment,r.evidence_ref=b.evidence_ref
                    from ops.staging_recovery_rehearsal_bundle b join ops.run r on r.recovery_rehearsal_bundle_id=b.id
                    where b.id=%s""", (recorded["bundle_id"],))
        shape = one(cur)
        check("forward-fix bundle is strategy-specific and exact-bound",
              shape == ("forward_fix", True, True, base.CURRENT_SHA, base.CURRENT_PROVIDER_VERSION,
                        "carr_program5_forward_fix_verifier",
                        "recovery.rehearsal.forward-fix", "staging", True), repr(shape))
        check("manual generic recovery run cannot satisfy a rehearsal", refuses(
            cur, """insert into ops.run(correlation_id,kind,service_id,release_id,environment,run_key,state,
              started_at,ended_at,source_kind,source_ref,evidence_ref,recovery_strategy,recovery_plan_ref)
              values(%s,'check',%s,%s,'staging','recovery.rehearsal.forward-fix','succeeded',
              now(),now(),'operator','forged','forged:any','forward_fix',%s)""",
            (uuid.uuid4(), fixture["service_id"], fixture["current_id"], base.ROLLBACK_PLAN)))
        for table in ("ops.staging_forward_fix_rehearsal_attempt", "ops.staging_forward_fix_rehearsal_claim",
                      "ops.staging_forward_fix_rehearsal_result"):
            cur.execute("select has_table_privilege('carr_jobs',%s,'insert,update,delete')", (table,))
            check(f"routine role has no direct {table} DML", one(cur)[0] is False)
        cur.execute("select has_table_privilege('carr_program5_forward_fix_verifier',"
                    "'ops.staging_forward_fix_rehearsal_attempt','select')")
        check("verifier has no raw attempt-table SELECT", one(cur)[0] is False)

        # A fresh exact bundle lets Joe approve once, then a plan revision clears
        # the pointer and makes the old forward-fix evidence ineligible.
        base.authority(cur, "carr_authority_joe")
        cur.execute("select ops.approve_program5_release(%s,%s,%s::uuid,12)",
                    (fixture["current_key"], base.PLAN_HASH, uuid.uuid4()))
        approval = one(cur)[0]
        owner(cur)
        cur.execute("select state,approval_receipt_id is not null from ops.release where id=%s", (fixture["current_id"],))
        check("Joe approval dispatches to the exact forward-fix bundle", one(cur) == ("approved", True))
        cur.execute("update ops.release set plan_hash=%s where id=%s", ("sha256:" + "8" * 64, fixture["current_id"]))
        cur.execute("select state,approval_receipt_id is null from ops.release where id=%s", (fixture["current_id"],))
        check("plan revision invalidates forward-fix approval eligibility", one(cur) == ("candidate", True))
        base.authority(cur, "carr_authority_joe")
        check("Joe cannot approve a revised plan with the stale bundle", refuses(
            cur, "select ops.approve_program5_release(%s,%s,%s::uuid,12)",
            (fixture["current_key"], "sha256:" + "8" * 64, uuid.uuid4())))
        owner(cur)

        # Completion uses the same strategy dispatcher.  A separate exact
        # candidate receives the staged boundary, approval, Production readback,
        # and measured performance fact before carr_jobs can close it.
        completion = forward_fixture(cur, "completion")
        jobs(cur)
        _, completion_result = prepare_claim_record(cur, completion)
        owner(cur)
        base.authority(cur, "carr_authority_joe")
        cur.execute("select ops.approve_program5_release(%s,%s,%s::uuid,12)",
                    (completion["current_key"], base.PLAN_HASH, uuid.uuid4()))
        one(cur)
        owner(cur)
        now = datetime.now(timezone.utc)
        correlation_id = uuid.uuid4()
        cur.execute("""insert into ops.deployment(correlation_id,service_id,environment,state,git_sha,provider,
          provider_version_id,release_id,deployed_by_actor,verb_count,schema_highest_migration,doctrine_generation,
          started_at,ended_at,read_back_at,verification_evidence_ref,source_kind,source_ref,observed_at)
          values(%s,%s,'production','complete',%s,'cloudflare-workers',%s,%s,'jobs',211,%s,170,
          %s,%s,%s,'production:forward-fix-readback','wrapper','gate',%s)""",
          (correlation_id, completion["service_id"], base.CURRENT_SHA, base.CURRENT_PROVIDER_VERSION,
           completion["current_id"], base.SCHEMA_HIGHEST_MIGRATION, now, now, now, now))
        cur.execute("""insert into ops.run(correlation_id,kind,service_id,environment,run_key,state,started_at,ended_at,
          source_kind,source_ref,observed_at,evidence_ref,release_id,budget_ms)
          values(%s,'check',%s,'production','performance.forward-fix','succeeded',%s,%s,
          'wrapper','gate',%s,'performance:forward-fix',%s,250)""",
          (correlation_id, completion["service_id"], now, now + timedelta(milliseconds=100), now, completion["current_id"]))
        jobs(cur)
        cur.execute("update ops.release set state='complete',ended_at=clock_timestamp() where id=%s", (completion["current_id"],))
        owner(cur)
        cur.execute("select state from ops.release where id=%s", (completion["current_id"],))
        check("completion accepts only the same exact forward-fix bundle", one(cur)[0] == "complete")
        conn.rollback()

    # Exercise actual concurrent same-key recording in separate transactions.
    # One wins; the other replays, and no second append-only result is possible.
    barrier = threading.Barrier(2)
    outcomes: list[bool] = []
    errors: list[str] = []
    concurrent_provider_version = uuid.uuid4()
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        base.ensure_authority_roles(cur)
        concurrent_fixture = forward_fixture(cur, "concurrent")
        jobs(cur)
        concurrent_idem = uuid.uuid4()
        concurrent_prepared = prepare(cur, concurrent_fixture, concurrent_idem, uuid.uuid4())
        cur.execute("select ops.claim_staging_forward_fix_rehearsal(%s::uuid)", (concurrent_idem,))
        one(cur)
        conn.commit()
    def writer() -> None:
        try:
            with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
                verifier(cur)
                barrier.wait(timeout=10)
                value = record(cur, concurrent_idem, concurrent_provider_version,
                               concurrent_prepared["expected_provider_tag"])
                outcomes.append(value["replayed"])
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=20)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from ops.staging_forward_fix_rehearsal_result where idempotency_key=%s", (concurrent_idem,))
        check("concurrent same-key result recording leaves one durable fact",
              not errors and sorted(outcomes) == [False, True] and one(cur)[0] == 1, "; ".join(errors))
        cur.execute("drop role carr_program5_forward_fix_verifier")

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
