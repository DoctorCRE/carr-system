#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-PostgreSQL acceptance for the DoctorCRE V5-UX-C02/C06
resource-observation write and read doors.

WHY THIS EXISTS. mcp-server/test/resource-observation.test.mjs exercises the
MCP verb handlers against a hand-rolled JS fake of ops.record_resource_observation
and ops.read_resource_dashboard. That fake cannot catch a bug that only a real
PostgreSQL catalog exposes -- and one shipped in the first cut of migration
0580: the write door called the unqualified digest(...) under
`set search_path = pg_catalog, ops`, while pgcrypto's digest() lives in
`public`. Every real write then failed at runtime with "function digest(text,
unknown) does not exist", invisible to the JS fake and to every other db-gate
that never happens to call this door. This gate calls the real write door and
the real read door, end to end, against disposable PostgreSQL, so that class
of bug fails CI instead of production.

It also proves the three fixes an independent review asked for on this
migration:
  1. public.digest(...) — a write actually succeeds (this file's own reason
     for existing).
  2. Only local_compute and model_route are collectible today: neon, github
     and cloudflare are refused until C03-C05 land, so no agent can fabricate
     a healthy external-provider reading the read door then serves as fact.
  3. Staleness is re-checked at read time against the wall clock, not trusted
     from the stored state: an observation older than the 15-minute
     RESOURCE_OBSERVATION_STALE_AFTER threshold reads back state='stale' with
     an age-bearing reason, regardless of what state the collector originally
     reported. And observed_at carries a small future-skew guard on write, so
     a caller cannot buy permanent front-of-queue ordering with a fabricated
     future timestamp.
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"resource-observation-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def record(cur, **kwargs):
    defaults = dict(
        provider="local_compute",
        account=None,
        project=None,
        product=None,
        period=None,
        as_of=None,
        quantity=None,
        quantity_unit=None,
        allowance=None,
        policy=None,
        estimate=None,
        charge=None,
        measured_capacity=None,
        configured_capacity=None,
        model_route=None,
        state="ok",
        reason=None,
        source="resource-observation-local-pg-gate",
        observed_at="now()",
        idempotency_key=None,
        actor_slug="resource-observation-local-pg-gate",
    )
    defaults.update(kwargs)
    idempotency_key = defaults["idempotency_key"] or uuid.uuid4()
    observed_at_sql = "now()" if defaults["observed_at"] == "now()" else "%(observed_at)s"
    row = cur.execute(
        f"""select * from ops.record_resource_observation(
              %(provider)s, %(account)s, %(project)s, %(product)s, %(period)s, %(as_of)s,
              %(quantity)s, %(quantity_unit)s, %(allowance)s, %(policy)s, %(estimate)s, %(charge)s,
              %(measured_capacity)s, %(configured_capacity)s, %(model_route)s,
              %(state)s, %(reason)s, %(source)s, {observed_at_sql},
              %(idempotency_key)s, %(actor_slug)s)""",
        {
            **defaults,
            "policy": Jsonb(defaults["policy"]) if defaults["policy"] is not None else None,
            "measured_capacity": Jsonb(defaults["measured_capacity"])
            if defaults["measured_capacity"] is not None
            else None,
            "configured_capacity": Jsonb(defaults["configured_capacity"])
            if defaults["configured_capacity"] is not None
            else None,
            "model_route": Jsonb(defaults["model_route"]) if defaults["model_route"] is not None else None,
            "idempotency_key": idempotency_key,
        },
    ).fetchone()
    if row is None:
        raise RuntimeError("record_resource_observation returned no row")
    return row


def read(cur):
    row = cur.execute("select ops.read_resource_dashboard()").fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("read_resource_dashboard returned no row")
    return row[0]


def provider_row(payload, provider):
    for row in payload["providers"]:
        if row["provider"] == provider:
            return row
    raise RuntimeError(f"provider {provider!r} missing from dashboard payload")


def expect_refusal(cur, fn, expected_substring: str, label: str) -> None:
    """Call `fn()` expecting a RaiseException containing `expected_substring`.

    A failed statement poisons the REST of a real PostgreSQL transaction
    ("current transaction is aborted") until a ROLLBACK or ROLLBACK TO
    SAVEPOINT runs -- so an expected refusal has to be wrapped in its own
    savepoint, or every statement after it in this rollback-only gate fails
    with that unrelated error instead of the assertion actually being tested.
    """
    cur.execute(f"savepoint {label}")
    try:
        fn()
    except psycopg.errors.RaiseException as exc:
        cur.execute(f"rollback to savepoint {label}")
        if expected_substring not in str(exc):
            raise RuntimeError(f"{label} refused with the wrong reason: {exc}") from exc
        return
    cur.execute(f"rollback to savepoint {label}")
    raise RuntimeError(f"{label} was accepted; expected a refusal")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, "carr_writer", "carr_reader")

            # 1. public.digest(...) fix: a real write against real PostgreSQL
            # must succeed. Before the fix, this raised "function digest(text,
            # unknown) does not exist" under search_path = pg_catalog, ops.
            set_local_role(cur, "carr_writer")
            written = record(
                cur,
                provider="local_compute",
                measured_capacity={"cpu_cores": 24, "memory_gb": 192},
                configured_capacity={"cpu_cores": 24, "memory_gb": 192},
            )
            if written[2] != "ok" or written[5] is not False:
                raise RuntimeError(f"fresh local_compute write did not land as a non-replayed ok row: {written}")
            cur.execute("reset role")

            set_local_role(cur, "carr_reader")
            payload = read(cur)
            cur.execute("reset role")
            if payload.get("schema") != "doctorcre-resource-dashboard.v1":
                raise RuntimeError(f"unexpected dashboard schema: {payload.get('schema')}")
            local = provider_row(payload, "local_compute")
            if local["state"] != "ok" or local["measured_capacity"] != {"cpu_cores": 24, "memory_gb": 192}:
                raise RuntimeError(f"fresh write did not read back as ok with its measured capacity: {local}")

            # 2. Provider-collectibility refusal: neon/github/cloudflare are
            # not yet collectible (C03-C05 not built). A write door that
            # accepted {provider:'neon', state:'ok'} would let any carr_writer
            # agent fabricate a healthy external-provider reading.
            set_local_role(cur, "carr_writer")
            for blocked in ("neon", "github", "cloudflare"):
                expect_refusal(
                    cur,
                    lambda blocked=blocked: record(cur, provider=blocked, state="ok"),
                    "resource_observation_provider_not_yet_collectible",
                    f"{blocked}_not_yet_collectible",
                )
            cur.execute("reset role")
            set_local_role(cur, "carr_reader")
            payload = read(cur)
            cur.execute("reset role")
            for still_unconfigured in ("neon", "github", "cloudflare"):
                row = provider_row(payload, still_unconfigured)
                if row["state"] != "unconfigured":
                    raise RuntimeError(
                        f"{still_unconfigured} shows {row['state']!r} after a refused write; "
                        "fabricated health leaked through"
                    )

            # 3a. Staleness: an old observation reads back state='stale' with
            # an age-bearing reason, regardless of what state the collector
            # originally reported, because a dead collector must not read
            # back 'ok' forever.
            set_local_role(cur, "carr_writer")
            cur.execute(
                """select * from ops.record_resource_observation(
                     'model_route', null, null, null, null, null,
                     null, null, null, null, null, null,
                     null, null, %(model_route)s,
                     'ok', null, 'resource-observation-local-pg-gate',
                     now() - interval '20 minutes', %(idempotency_key)s,
                     'resource-observation-local-pg-gate')""",
                {"model_route": Jsonb({"label": "local.ds4-flash-next"}), "idempotency_key": uuid.uuid4()},
            )
            cur.execute("reset role")
            set_local_role(cur, "carr_reader")
            payload = read(cur)
            cur.execute("reset role")
            model_route = provider_row(payload, "model_route")
            if model_route["state"] != "stale":
                raise RuntimeError(f"20-minute-old observation did not read back stale: {model_route}")
            if "minute" not in (model_route["reason"] or ""):
                raise RuntimeError(f"stale reason does not carry an age: {model_route}")

            # 3b. Future-skew guard: observed_at more than a few minutes in
            # the future is refused outright, so a fabricated future
            # timestamp cannot buy permanent front-of-queue ordering.
            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur,
                lambda: cur.execute(
                    """select * from ops.record_resource_observation(
                         'local_compute', null, null, null, null, null,
                         null, null, null, null, null, null,
                         null, null, null,
                         'ok', null, 'resource-observation-local-pg-gate',
                         now() + interval '1 hour', %(idempotency_key)s,
                         'resource-observation-local-pg-gate')""",
                    {"idempotency_key": uuid.uuid4()},
                ),
                "resource_observation_observed_at_in_future",
                "future_observed_at",
            )
            cur.execute("reset role")

        print("PASS: resource-observation real-PostgreSQL write/read, provider refusal, and staleness proof")
        return 0
    except Exception as exc:  # noqa: BLE001 - gate contract is a printed failure, not a traceback
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
