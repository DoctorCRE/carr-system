#!/usr/bin/env python3
"""Bootstrap shared workflow contracts. This command never schedules or runs jobs.

On-demand engineering claims still reference these contracts.
Recurrence is owned by the native schedulers.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MANIFEST_PATH = REPO / 'ops/config/control-plane-workflows.v1.json'


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def connect():
    import psycopg
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        raise SystemExit('DATABASE_URL is required')
    return psycopg.connect(dsn)


def sync_registry(manifest: dict[str, Any]) -> dict[str, int]:
    with connect() as conn, conn.cursor() as cur:
        for workflow in manifest["workflows"]:
            execution = workflow["execution"]
            # This one workflow has a deliberately authority-managed runtime
            # bit.  The checked-in manifest remains disabled by default so a
            # fresh environment cannot be enabled by configuration sync.  Once
            # the migration has recorded a *latest* Joe activation receipt for
            # the current allowlist, a routine generic sync must preserve that
            # narrow live state instead of silently turning it back off.
            enabled = workflow["enabled"]
            if (workflow["key"], workflow["version"]) == ("calendar-prebrief-projection-joe-daily", 1):
                cur.execute("select to_regclass('ops.calendar_prebrief_runtime_activation_receipt')")
                if cur.fetchone()[0] is not None:
                    cur.execute("""select exists(
                    select 1 from ops.calendar_prebrief_allowed_calendar a
                    join lateral (
                      select r.allowlist_revision_id
                        from ops.calendar_prebrief_runtime_activation_receipt r
                       where r.sponsor='joe'
                       order by r.activated_at desc,r.id desc limit 1
                    ) latest on latest.allowlist_revision_id=a.active_revision_id
                    join ops.calendar_prebrief_allowlist_receipt l
                      on l.id=a.active_revision_id and l.sponsor='joe'
                     and l.configuration_digest=a.configuration_digest
                   where a.sponsor='joe')""")
                    enabled = bool(cur.fetchone()[0])
            # The partial unique index permits one enabled version per key.
            # Lock superseded definitions before checking for running work.
            # Claim functions lock the same row, so either a claim wins and
            # sync refuses until that worker drains, or sync wins and no old
            # worker can obtain a new lease.  A database cannot undo an
            # external command after launch, so silently disabling a running
            # version would be false fencing.
            cur.execute(
                "select version from ops.job_definition "
                "where key=%s and version<>%s and enabled for update",
                (workflow["key"], workflow["version"]),
            )
            superseded = [int(row[0]) for row in cur.fetchall()]
            if superseded:
                cur.execute(
                    "select count(*) from ops.job where definition_key=%s "
                    "and definition_version=any(%s) and state='running'",
                    (workflow["key"], superseded),
                )
                running = int(cur.fetchone()[0])
                if running:
                    raise RuntimeError(
                        f"drain running jobs before superseding {workflow['key']}: {running} active")
            # Disable superseded versions in this same transaction before
            # installing the declared version; a failed insert rolls the
            # disable and its queued-job fencing receipts back with it.
            cur.execute(
                "update ops.job_definition set enabled=false,updated_at=now() "
                "where key=%s and version<>%s and enabled",
                (workflow["key"],workflow["version"]),
            )
            cur.execute(
                """insert into ops.job_definition
                     (key,version,enabled,risk,owner_actor,execution_kind,execution_contract,
                      inventory_contract,recurrence,state_contract,routing_contract,
                      filtering_contract,validation_contract,retry_policy,deduplication,
                      completion_contract,legacy_schedule)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   on conflict (key,version) do update set
                     enabled=excluded.enabled,risk=excluded.risk,owner_actor=excluded.owner_actor,
                     execution_kind=excluded.execution_kind,
                     execution_contract=excluded.execution_contract,
                     inventory_contract=excluded.inventory_contract,recurrence=excluded.recurrence,
                     state_contract=excluded.state_contract,routing_contract=excluded.routing_contract,
                     filtering_contract=excluded.filtering_contract,
                     validation_contract=excluded.validation_contract,
                     retry_policy=excluded.retry_policy,deduplication=excluded.deduplication,
                     completion_contract=excluded.completion_contract,
                     legacy_schedule=excluded.legacy_schedule,updated_at=now()""",
                (workflow["key"],workflow["version"],enabled,workflow["risk"],
                 workflow.get("inventory",{}).get("owner","system"),execution["kind"],
                 json.dumps({k:v for k,v in execution.items() if k != "kind"}),
                 json.dumps(workflow.get("inventory",{})),json.dumps(workflow["recurrence"]),
                 json.dumps(workflow["state"]),json.dumps(workflow["routing"]),
                 json.dumps(workflow["filtering"]),json.dumps(workflow["validation"]),
                 json.dumps(workflow["retry"]),json.dumps(workflow["deduplication"]),
                 json.dumps(workflow["completion"]),json.dumps(workflow["legacy_schedule"])))
        conn.commit()
    return {"workflows": len(manifest["workflows"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['sync'])
    parser.parse_args()
    print(json.dumps(sync_registry(load_manifest()), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
