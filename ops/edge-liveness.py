#!/usr/bin/env python3
"""Read service health off the Mac and report failed or overdue critical jobs.

Observed freshness comes from the canonical health view. Never-observed jobs
have a first-report deadline only when their environment declares a cadence.
Missing credentials or an unreachable ledger produce an unknown verdict.
"""
import os
import sys
from datetime import datetime
from typing import NamedTuple

ALARM_CRITICALITY = {"critical", "high"}


class Observation(NamedTuple):
    service_key: str
    environment: str
    criticality: str
    runtime: str
    freshness_state: str
    health: str
    observed_at: datetime | None
    hours_since_observed: float | None
    expected_cadence_seconds: int | None
    first_report_overdue: bool


def classification(row: Observation) -> str | None:
    if row.observed_at is None:
        if row.expected_cadence_seconds and row.first_report_overdue:
            return "never started"
    elif row.freshness_state == "stale":
        return "stale"
    elif row.health == "unavailable":
        return "failed"
    return None


def dark_mac_jobs(rows: list[Observation]) -> list[Observation]:
    launchd = [r for r in rows if r.runtime == "launchd" and r.observed_at is not None]
    return launchd if launchd and all(r.freshness_state == "stale" for r in launchd) else []


def affected(rows: list[Observation]) -> list[Observation]:
    dark = dark_mac_jobs(rows)
    return [r for r in rows if classification(r) is not None
            and (r.criticality in ALARM_CRITICALITY or r in dark)]


def main() -> int:
    dsn = (os.environ.get("LIVENESS_DATABASE_URL")
           or os.environ.get("BACKUP_DATABASE_URL")
           or os.environ.get("DATABASE_URL", ""))
    if not dsn:
        print("edge-liveness: no database credential in this environment — "
              "nothing read, nothing claimed (EX_CONFIG).")
        return 78

    try:
        import psycopg
    except ImportError:
        print("edge-liveness: psycopg unavailable — nothing read, nothing claimed.")
        return 78

    try:
        conn_ctx = psycopg.connect(dsn, connect_timeout=20)
    except Exception as exc:  # noqa: BLE001 — every connect failure reads the same
        print(f"edge-liveness: the ledger is unreachable ({type(exc).__name__}) "
              f"— nothing read, nothing claimed. This is NOT a report that a "
              f"service has gone quiet, and must never be shown as one.")
        return 78

    with conn_ctx as conn, conn.cursor() as cur:
        cur.execute(
            """select v.service_key, v.environment, v.criticality, s.runtime,
                      v.freshness_state, v.health, v.observed_at,
                      extract(epoch from (now() - v.observed_at)) / 3600.0 as hours,
                      se.expected_cadence_seconds,
                      coalesce(now() > s.registered_at + make_interval(secs =>
                        se.expected_cadence_seconds + se.cadence_grace_seconds), false)
                        as first_report_overdue
                 from ops.v_service_environment_health v
                 join ops.service s on s.id = v.service_id
                 join ops.service_environment se
                   on se.service_id = v.service_id and se.environment = v.environment
                where v.environment = 'production'
                  and s.retired_at is null
             order by v.service_key""")
        rows = [Observation(*row) for row in cur.fetchall()]

    return assess(rows)


def assess(rows: list[Observation]) -> int:
    if not rows:
        print("edge-liveness: the health view returned no rows; service health is unknown.")
        return 1

    alarming = affected(rows)
    observed = [r for r in rows if r.observed_at is not None]
    never = [r for r in rows if r.observed_at is None]
    dark = dark_mac_jobs(rows)
    print("edge-liveness: read from GitHub, not from the machine it reports on")
    print(f"  {len(rows)} production row(s): {len(observed)} observed, {len(never)} never observed")
    for row in never:
        state = ("no recurring report required" if not row.expected_cadence_seconds
                 else "never started" if row.first_report_overdue else "awaiting first report")
        print(f"      {row.service_key:<28} {state}")

    if dark:
        ages = [float(r.hours_since_observed) for r in dark
                if r.hours_since_observed is not None]
        most_recent = min(ages)
        print(f"\n  THE MAC HAS GONE DARK. All {len(dark)} observed launchd service(s) "
              f"are stale together; the most recent word was {most_recent:.1f}h ago.")

    for row in alarming:
        age = (f"last heard {float(row.hours_since_observed):.1f}h ago"
               if row.hours_since_observed is not None else "no completion observed")
        print(f"      {row.service_key:<28} {classification(row)}; {age}")
    if alarming:
        print("\n  Inspect service evidence with tools/ops-record.py health.")
        return 1
    noted = [r for r in rows if classification(r) and r not in alarming]
    if noted:
        print("  Low/medium service findings noted without alarm: "
              + ", ".join(r.service_key for r in noted))
    print("\n  No critical service failures or overdue reports detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
