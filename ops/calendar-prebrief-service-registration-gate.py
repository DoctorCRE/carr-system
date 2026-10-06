#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Prove the Joe calendar-prebrief LaunchAgent is representable in operations.

The service catalog is the source of truth; ``tools/ops-record.py sync-registry``
is its one database projection.  This gate deliberately exercises that door on
the disposable migration database rather than adding seed SQL that could drift
from ``ops/config/services.json``.

The declaration must create a production health-view row, but it must not turn
on either prebrief workflow.  Joe live activation remains the existing typed,
receipt-backed authority operation; the isolated canary remains disabled.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg


REPO = Path(__file__).resolve().parents[1]
SERVICE = "calendar-prebrief-joe"
ENVIRONMENT = "production"


def require(row: tuple | None, message: str) -> tuple:
    if row is None:
        raise RuntimeError(message)
    return row


def insert_job(cur, *, job_id: uuid.UUID, observed_at: datetime, state: str,
               definition_key: str = "calendar-prebrief-projection-joe-daily",
               mode: str = "live", failure_class: str | None = None) -> None:
    cur.execute(
        """insert into ops.job
               (id,definition_key,definition_version,idempotency_key,scheduled_for,
                mode,state,attempt,max_attempts,next_attempt_at,timeout_seconds,
                last_failure_class,created_at,ended_at,updated_at)
             values (%s,%s,1,%s,%s,%s,%s,1,2,%s,300,%s,%s,%s,%s)""",
        (job_id, definition_key, f"health-gate:{job_id}", observed_at,
         mode, state, observed_at, failure_class, observed_at, observed_at,
         observed_at),
    )


def insert_projection_evidence(cur, *, job_id: uuid.UUID,
                               observed_at: datetime, sponsor: str = "joe",
                               mode: str = "live") -> uuid.UUID:
    """Seed one internally consistent receipt chain for a DB-gate fixture."""
    allowlist_id, challenge_id = uuid.uuid4(), uuid.uuid4()
    attestation_id, projection_id = uuid.uuid4(), uuid.uuid4()
    lease = uuid.uuid4()
    digest, calendar_key = "a" * 64, "b" * 64
    destination = "live" if mode == "live" else f"calendar-prebrief-canary-{sponsor}"
    cur.execute(
        """insert into ops.calendar_prebrief_allowlist_receipt
               (id,sponsor,calendar_keys,configuration_digest,configured_at,configured_by)
             values (%s,%s,%s,%s,%s,%s)""",
        (allowlist_id, sponsor, [calendar_key], digest, observed_at, sponsor),
    )
    cur.execute(
        """insert into ops.calendar_prebrief_capture_challenge
               (id,job_id,attempt,lease_token,sponsor,resolver_identity,mode,
                destination,scheduled_for,window_starts_at,window_ends_at,
                allowlist_revision_id,allowlist_digest,calendar_keys,issued_at)
             values (%s,%s,1,%s,%s,%s,%s,%s,
                     %s,
                     %s::timestamptz - interval '7 days',
                     %s::timestamptz + interval '45 days',
                     %s,%s,%s,%s)""",
        (challenge_id, job_id, lease, sponsor,
         f"carr_calendar_prebrief_resolver_{sponsor}", mode, destination,
         observed_at, observed_at, observed_at, allowlist_id, digest,
         [calendar_key], observed_at),
    )
    cur.execute(
        """insert into ops.calendar_prebrief_source_attestation_receipt
               (id,job_id,attempt,lease_token,sponsor,attestor_identity,mode,
                destination,snapshot_at,allowlist_revision_id,capture_challenge_id,
                allowlist_digest,observed_calendar_keys,event_count,
                canonical_event_digest,collector_key_fingerprint,signature_sha256,
                collector_version,attested_at)
             values (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s)""",
        (attestation_id, job_id, lease, sponsor,
         f"carr_calendar_prebrief_attestor_{sponsor}", mode, destination,
         observed_at, allowlist_id, challenge_id, digest, [calendar_key],
         "c" * 64, "d" * 64, uuid.uuid4().hex * 2,
         "health-gate", observed_at),
    )
    cur.execute(
        """insert into ops.calendar_prebrief_projection_receipt
               (id,job_id,attempt,sponsor,snapshot_at,allowlist_revision_id,
                allowlist_digest,snapshot_digest,event_count,participant_count,
                captured_at,source_attestation_id)
             values (%s,%s,1,%s,%s,%s,%s,%s,0,0,%s,%s)""",
        (projection_id, job_id, sponsor, observed_at, allowlist_id, digest,
         "e" * 64, observed_at, attestation_id),
    )
    cur.execute(
        """insert into ops.job_receipt
               (job_id,attempt,kind,receipt_ref,evidence,created_at)
             values (%s,1,'completion',%s,%s::jsonb,%s)""",
        (job_id, f"calendar-prebrief:joe:{job_id}:1",
         '{"sponsor":"joe","mode":"live","attestation_id":"'
         + str(attestation_id) + '","receipt_id":"' + str(projection_id)
         + '","allowlist_revision_id":"' + str(allowlist_id)
         + '","allowlist_digest":"' + digest + '"}', observed_at),
    )
    return projection_id


def read_health(cur) -> tuple:
    cur.execute(
        """select health,freshness_state,last_run_state,last_failure_class,
                  source_kind,source_ref
             from ops.v_service_environment_health
            where service_key=%s and environment=%s""",
        (SERVICE, ENVIRONMENT),
    )
    return require(cur.fetchone(), "calendar prebrief has no health-view row")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("calendar-prebrief service registration gate: DATABASE_URL is not set")

    synced = subprocess.run(
        [sys.executable, str(REPO / "tools" / "ops-record.py"), "sync-registry"],
        cwd=REPO, env={**os.environ, "DATABASE_URL": dsn}, text=True,
        capture_output=True, timeout=120,
    )
    if synced.returncode:
        raise RuntimeError(
            "calendar-prebrief service registration gate: sync-registry failed: "
            + (synced.stderr.strip() or synced.stdout.strip())
        )

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(
            """select s.key, s.name, s.family, s.criticality, s.owner_actor,
                      s.repo_path, s.runtime, se.environment,
                      se.deploy_mechanism, se.expected_cadence_seconds,
                      se.cadence_grace_seconds
                 from ops.service s
                 join ops.service_environment se on se.service_id=s.id
                where s.key=%s and s.retired_at is null and se.environment=%s""",
            (SERVICE, ENVIRONMENT),
        )
        service = require(cur.fetchone(), "calendar prebrief service projection is absent")
        expected = (
            SERVICE, "Joe calendar prebrief projection", "Local Mac edge", "medium", "joe",
            "tools/calendar-prebrief-joe-runtime.py", "launchd", ENVIRONMENT,
            "ops/launchd/com.carr.calendar-prebrief-joe.plist", 86400, 172800,
        )
        if service != expected:
            raise RuntimeError(f"calendar prebrief service projection drifted: {service!r}")

        cur.execute(
            """select service_key, environment, health, freshness_state,
                      observed_at
                 from ops.v_service_environment_health
                where service_key=%s and environment=%s""",
            (SERVICE, ENVIRONMENT),
        )
        initial_health = require(cur.fetchone(), "calendar prebrief has no health-view row")
        if initial_health[:4] != (SERVICE, ENVIRONMENT, "unknown", "missing") or initial_health[4] is not None:
            raise RuntimeError(f"calendar prebrief initial health is not visible-and-honest: {initial_health!r}")

        if read_health(cur)[:2] != ("unknown", "missing"):
            raise RuntimeError("calendar prebrief initial observation is not unknown/missing")

        unrelated = uuid.uuid4()
        unrelated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        insert_job(cur, job_id=unrelated, observed_at=unrelated_at, state="succeeded",
                   definition_key="calendar-prebrief-canary-joe-daily", mode="canary")
        insert_projection_evidence(cur, job_id=unrelated, observed_at=unrelated_at,
                                   mode="canary")
        if read_health(cur)[:2] != ("unknown", "missing"):
            raise RuntimeError("an unrelated canary job/receipt changed Joe live health")

        stale_job = uuid.uuid4()
        stale_at = datetime.now(timezone.utc) - timedelta(days=4)
        insert_job(cur, job_id=stale_job, observed_at=stale_at, state="succeeded")
        stale_receipt = insert_projection_evidence(
            cur, job_id=stale_job, observed_at=stale_at)
        stale = read_health(cur)
        if stale[:3] != ("unknown", "stale", "succeeded") or str(stale_receipt) not in stale[5]:
            raise RuntimeError(f"stale exact receipt did not project stale health: {stale!r}")

        fresh_job = uuid.uuid4()
        fresh_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        insert_job(cur, job_id=fresh_job, observed_at=fresh_at, state="succeeded")
        fresh_receipt = insert_projection_evidence(
            cur, job_id=fresh_job, observed_at=fresh_at)
        fresh = read_health(cur)
        if fresh[:3] != ("healthy", "fresh", "succeeded") or str(fresh_receipt) not in fresh[5]:
            raise RuntimeError(f"fresh exact receipt did not project healthy: {fresh!r}")

        failed_job = uuid.uuid4()
        failed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        insert_job(cur, job_id=failed_job, observed_at=failed_at,
                   state="dead_lettered", failure_class="lease_expired")
        cur.execute(
            """insert into ops.job_receipt
                   (job_id,attempt,kind,receipt_ref,evidence,created_at)
                 values (%s,1,'dead_letter',%s,
                         '{"failure_class":"lease_expired","next_state":"dead_lettered"}'::jsonb,%s)""",
            (failed_job, f"lease-expired:{failed_job}:1", failed_at),
        )
        failed = read_health(cur)
        if failed[:4] != ("unavailable", "fresh", "dead_lettered", "lease_expired"):
            raise RuntimeError(f"latest dead-letter job did not project failure: {failed!r}")

        cur.execute(
            """select key, enabled
                 from ops.job_definition
                where (key, version) in
                    (('calendar-prebrief-projection-joe-daily', 1),
                     ('calendar-prebrief-canary-joe-daily', 1))
                order by key"""
        )
        definitions = cur.fetchall()
        if definitions != [
            ("calendar-prebrief-canary-joe-daily", False),
            ("calendar-prebrief-projection-joe-daily", False),
        ]:
            raise RuntimeError(
                "service registration changed calendar-prebrief activation authority: "
                f"{definitions!r}"
            )

        conn.rollback()

    print("calendar prebrief service registration gate: PASS — projected, health-visible, authority unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
