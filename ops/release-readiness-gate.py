#!/usr/bin/env python3
"""Disposable-DB acceptance for service-owned production release readiness."""

# ci: db-gate
# doctrine: engineering-workflow-sop

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import uuid

import psycopg

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "typed_recovery_gate", ROOT / "ops/staging-release-readback-gate.py")
assert SPEC is not None and SPEC.loader is not None
typed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(typed)
# The production manifest's provider-bound plan hash uses the CARR release
# contract, which differs from Engineering Work Request sha256 plan hashes.
# Seed this disposable release fixture with the value the wrapper really emits.
setattr(typed, "PLAN_HASH", "plan:" + "c" * 32)


def expect_refusal(cur, statement: str, params: tuple = ()) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(statement, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint expected_refusal")
        return
    cur.execute("rollback to savepoint expected_refusal")
    raise AssertionError(f"unexpected success: {statement[:90]}")


def one(cur):
    row = cur.fetchone()
    assert row is not None
    return row


def main() -> int:
    dsn = os.environ.get("CARR_CI_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("release-readiness-gate: explicit disposable DATABASE_URL required", file=sys.stderr)
        return 78
    typed.require_loopback(dsn)
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cur:
            typed.ensure_authority_roles(cur)
            fixture = typed.seed_fixture(cur, "service-readiness")
            typed.make_typed_bundle(cur, fixture)

            typed.authority(cur, "carr_jobs")
            cur.execute("""insert into ops.release
                (release_key,service_id,environment,state,git_sha,
                 source_kind,source_ref)
                values(%s,%s,'production','candidate',%s,'wrapper',
                       'ops/release-readiness-gate.py')
                returning maker_actor,maker_session_user,maker_authority_verified""",
                (f"service-candidate-{uuid.uuid4()}", fixture["service_id"],
                 "c" * 40))
            assert one(cur) == ("carr_jobs", "carr_jobs", False)
            typed.owner(cur)

            # The new trigger also fires for the historical Joe authority
            # candidate insert. Its internal evidence reads must not require
            # widening that login's table SELECT grants.
            typed.authority(cur, "carr_authority_joe")
            cur.execute("""insert into ops.release
                (release_key,service_id,environment,state,git_sha,
                 source_kind,source_ref)
                values(%s,%s,'production','candidate',%s,'wrapper',
                       'ops/release-readiness-gate.py#historical')
                returning maker_actor,maker_session_user,maker_authority_verified""",
                (f"historical-candidate-{uuid.uuid4()}", fixture["service_id"],
                 "d" * 40))
            assert one(cur) == ("joe", "carr_authority_joe", True)
            typed.owner(cur)

            # A legacy row without an authenticated filing login must not pass
            # the service qualifier through SQL's NULL three-valued logic.
            cur.execute("set local session_replication_role=replica")
            cur.execute("update ops.release set maker_session_user=null where id=%s",
                        (fixture["current_id"],))
            cur.execute("set local session_replication_role=origin")
            typed.authority(cur, "carr_jobs")
            expect_refusal(cur, "select ops.qualify_program5_release(%s,%s,%s)",
                           (fixture["current_key"], typed.PLAN_HASH, uuid.uuid4()))
            typed.owner(cur)

            # Historical gate fixtures predate the service-filed candidate
            # path. Rewrite only this disposable fixture's provenance to the
            # values the live 0504 insert trigger derives from carr_jobs.
            cur.execute("set local session_replication_role=replica")
            cur.execute("""update ops.release set maker_session_user='carr_jobs',
                maker_actor='carr_jobs',
                maker_verification_ref='ops.session-login:carr_jobs'
                where id=%s""", (fixture["current_id"],))
            cur.execute("set local session_replication_role=origin")

            expect_refusal(cur, """update ops.release set state='ready'
                where id=%s""", (fixture["current_id"],))
            typed.authority(cur, "carr_authority_joe")
            expect_refusal(cur, "select ops.qualify_program5_release(%s,%s,%s)",
                           (fixture["current_key"], typed.PLAN_HASH, uuid.uuid4()))
            typed.owner(cur)
            typed.authority(cur, "carr_writer")
            expect_refusal(cur, "select ops.qualify_program5_release(%s,%s,%s)",
                           (fixture["current_key"], typed.PLAN_HASH, uuid.uuid4()))
            typed.owner(cur)
            typed.authority(cur, "carr_jobs")
            key = uuid.uuid4()
            cur.execute("select ops.qualify_program5_release(%s,%s,%s)",
                        (fixture["current_key"], typed.PLAN_HASH, key))
            first = one(cur)[0]
            cur.execute("select ops.qualify_program5_release(%s,%s,%s)",
                        (fixture["current_key"], typed.PLAN_HASH, key))
            replay = one(cur)[0]
            assert first["replayed"] is False and replay["replayed"] is True
            expect_refusal(cur, "select ops.qualify_program5_release(%s,%s,%s)",
                           (fixture["current_key"], "sha256:" + "d" * 64, key))
            typed.owner(cur)
            cur.execute("""select r.state,r.maker_actor,r.approved_by_actor,
                       r.approved_at,r.approval_receipt_id,q.session_login,
                       q.provider_version_id,q.git_sha,q.plan_hash
                       from ops.release r join ops.release_readiness_receipt q
                         on q.id=r.readiness_receipt_id where r.id=%s""",
                        (fixture["current_id"],))
            state, maker, approver, approved_at, approval_id, login, version, sha, plan = one(cur)
            assert (state, maker, approver, approved_at, approval_id, login,
                    version, sha, plan) == (
                    "ready", "carr_jobs", None, None, None, "carr_jobs",
                    typed.CURRENT_PROVIDER_VERSION, typed.CURRENT_SHA, typed.PLAN_HASH)
            expect_refusal(cur, """update ops.release set approved_by_actor='joe'
                where id=%s""", (fixture["current_id"],))
            expect_refusal(cur, """update ops.release set verifier_actor='other'
                where id=%s""", (fixture["current_id"],))
            expect_refusal(cur, """insert into ops.deployment
               (correlation_id,service_id,environment,state,git_sha,provider,
                provider_version_id,release_id,started_at,source_kind,source_ref)
               values(%s,%s,'production','verifying',%s,'cloudflare-workers',%s,
                      %s,now(),'wrapper','readiness-gate')""",
               (uuid.uuid4(), fixture["service_id"], typed.CURRENT_SHA,
                typed.PRIOR_PROVIDER_VERSION, fixture["current_id"]))
            cur.execute("""insert into ops.deployment
               (correlation_id,service_id,environment,state,git_sha,provider,
                provider_version_id,release_id,started_at,source_kind,source_ref)
               values(%s,%s,'production','verifying',%s,'cloudflare-workers',%s,
                      %s,now(),'wrapper','readiness-gate')""",
               (uuid.uuid4(), fixture["service_id"], typed.CURRENT_SHA,
                typed.CURRENT_PROVIDER_VERSION, fixture["current_id"]))
            cur.execute("select ops.release_technical_readiness_current(%s)",
                        (fixture["current_id"],))
            assert one(cur)[0] is True
            cur.execute("set local session_replication_role=replica")
            cur.execute("""update ops.staging_recovery_rehearsal_bundle
                set completed_at=now()-interval '25 hours'
                where current_release_id=%s""", (fixture["current_id"],))
            cur.execute("set local session_replication_role=origin")
            cur.execute("select ops.release_technical_readiness_current(%s)",
                        (fixture["current_id"],))
            assert one(cur)[0] is False
            expect_refusal(cur, """insert into ops.deployment
               (correlation_id,service_id,environment,state,git_sha,provider,
                provider_version_id,release_id,started_at,source_kind,source_ref)
               values(%s,%s,'production','verifying',%s,'cloudflare-workers',%s,
                      %s,now(),'wrapper','readiness-gate')""",
               (uuid.uuid4(), fixture["service_id"], typed.CURRENT_SHA,
                typed.CURRENT_PROVIDER_VERSION, fixture["current_id"]))
            typed.authority(cur, "carr_authority_joe")
            expect_refusal(cur, "select ops.reopen_program5_release_rehearsal(%s,%s)",
                           (fixture["current_key"], typed.PLAN_HASH))
            typed.owner(cur)
            typed.authority(cur, "carr_jobs")
            expect_refusal(cur, "select ops.reopen_program5_release_rehearsal(%s,%s)",
                           (fixture["current_key"], "sha256:" + "d" * 64))
            cur.execute("select ops.reopen_program5_release_rehearsal(%s,%s)",
                        (fixture["current_key"], typed.PLAN_HASH))
            assert one(cur)[0]["replayed"] is False
            expect_refusal(cur, "select ops.qualify_program5_release(%s,%s,%s)",
                           (fixture["current_key"], typed.PLAN_HASH, key))
            typed.owner(cur)
            typed.make_typed_bundle(cur, fixture)
            typed.authority(cur, "carr_jobs")
            cur.execute("select ops.qualify_program5_release(%s,%s,%s)",
                        (fixture["current_key"], typed.PLAN_HASH, key))
            refreshed = one(cur)[0]
            assert refreshed["replayed"] is True and refreshed["refreshed"] is True
            assert refreshed["readiness_receipt_id"] == first["readiness_receipt_id"]
            typed.owner(cur)
            cur.execute("select ops.release_technical_readiness_current(%s)",
                        (fixture["current_id"],))
            assert one(cur)[0] is True
        connection.rollback()
    print("release-readiness-gate: service readiness, replay, identity and audit pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
