#!/usr/bin/env python3
# ci: db-gate
"""Read back the canonical ledger registry and compare every tracked contract.

WIRED 2026-08-21. This gate was written, was correct, and had no caller: no
marker, no invocation anywhere in the repository. ops/ci.sh has been naming it
in the clear on every push since the db-gate loop was built — "db-gates without
the `# ci: db-gate` marker: control-plane-registry-gate.py" — and nobody read
the line. The control-plane roadmap's Phase 2 exit asks that every scheduled
run have an auditable owner independent of the AI provider's scheduler; this is
the check that asserts it, so Phase 2 could not exit while it sat unrun
(rule ab814a26 — a rule ships with its enforcement, and recitation is not
enforcement).

It needs nothing but the throwaway database the migration class already stands
up: verified passing there on 2026-08-21 against the committed schema, 25
workflows and 8 cognition contracts exact, zero legacy cutovers.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "ops" / "config" / "control-plane-workflows.v1.json"


def normalized(value):
    return value if isinstance(value, (dict, list)) else json.loads(value)


def fetchone_required(row: tuple[Any, ...] | None, context: str) -> tuple[Any, ...]:
    if row is None:
        raise RuntimeError(f"registry gate expected one row for {context}")
    return row


MANAGED_ENABLED_IDENTITY = ("calendar-prebrief-projection-joe-daily", 1)


def compare(cur: Any, manifest: dict[str, Any], *, resolve_managed_enabled: bool = True
            ) -> tuple[list[str], int, int, list[str]]:
    """Compare a live registry against the reviewed manifest, read-only.

    Split out of main() on 2026-08-23 so the nightly drift watch and this gate
    run the SAME comparison instead of two that agree until one of them is
    edited (rule a8c55a47). Every statement here is a SELECT, so a caller may
    hand in a cursor already inside a read-only transaction under a routine
    role rather than the owner credential this gate's own main() uses.

    RESOLVE_MANAGED_ENABLED exists because the one authority-managed runtime bit
    lives in tables only the prebrief roles may read. A caller running under a
    routine role passes False: the receipt is not consulted, that single
    workflow's enabled bit is NOT compared, and its identity comes back in the
    fourth return value so the caller can say which check it did not perform.
    Reporting an unreadable input as a pass would be the more comfortable choice
    and the dishonest one.
    """
    failures: list[str] = []
    not_checked: list[str] = []
    cur.execute("""select key,version,enabled,risk,execution_kind,execution_contract,
                          inventory_contract,recurrence,routing_contract,filtering_contract,
                          validation_contract,retry_policy,deduplication,completion_contract,
                          legacy_schedule from ops.job_definition""")
    live = {(r[0],r[1]):r for r in cur.fetchall()}
    expected = {(w["key"],w["version"]):w for w in manifest["workflows"]}
    # The manifest's false is the safe bootstrap default.  The Joe
    # prebrief is the sole authority-managed exception: it may be true
    # only when the latest immutable activation receipt still names the
    # current allowlist revision.  This prevents a later generic registry
    # reconciliation from treating the activated runtime as drift.
    managed_joe_enabled = False
    if not resolve_managed_enabled:
        not_checked.append(
            f"{MANAGED_ENABLED_IDENTITY[0]} enabled (authority-managed; this "
            "role may not read the activation receipt)")
    else:
        cur.execute("select to_regclass('ops.calendar_prebrief_runtime_activation_receipt')")
        if fetchone_required(
                cur.fetchone(), "calendar prebrief activation receipt relation")[0] is not None:
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
            managed_joe_enabled = bool(fetchone_required(
                cur.fetchone(), "Joe calendar prebrief activation receipt")[0])
    # Sync disables a superseded version and keeps its row for the jobs that
    # reference it; that history is not drift while its key is still declared.
    declared = {key for key, _ in expected}
    extra = sorted(i for i in set(live) - set(expected) if live[i][2] or i[0] not in declared)
    missing = sorted(set(expected) - set(live))
    if missing or extra:
        failures.append(f"workflow keys differ missing={missing} extra={extra}")
    for identity, workflow in expected.items():
        row = live.get(identity)
        if row is None:
            continue
        expected_enabled = workflow["enabled"]
        if identity == MANAGED_ENABLED_IDENTITY and managed_joe_enabled:
            expected_enabled = True
        comparisons = {
            "enabled": (row[2],expected_enabled), "risk": (row[3],workflow["risk"]),
            "execution_kind": (row[4],workflow["execution"]["kind"]),
            "execution": (normalized(row[5]),{k:v for k,v in workflow["execution"].items() if k!="kind"}),
            "inventory": (normalized(row[6]),workflow["inventory"]),
            "recurrence": (normalized(row[7]),workflow["recurrence"]),
            "routing": (normalized(row[8]),workflow["routing"]),
            "filtering": (normalized(row[9]),workflow["filtering"]),
            "validation": (normalized(row[10]),workflow["validation"]),
            "retry": (normalized(row[11]),workflow["retry"]),
            "deduplication": (normalized(row[12]),workflow["deduplication"]),
            "completion": (normalized(row[13]),workflow["completion"]),
            "legacy_schedule": (normalized(row[14]),workflow["legacy_schedule"]),
        }
        if not resolve_managed_enabled and identity == MANAGED_ENABLED_IDENTITY:
            comparisons.pop("enabled")
        failures.extend(f"{identity[0]} {name} differs" for name,(got,want) in comparisons.items() if got!=want)
    cur.execute("select key,version,canonical_write_authority from ops.cognition_job")
    live_cognition = {(r[0],r[1]):r[2] for r in cur.fetchall()}
    expected_cognition = {(c["key"],c["version"]) for c in manifest["cognition_jobs"]}
    if set(live_cognition) != expected_cognition:
        failures.append("cognition job keys differ")
    if any(live_cognition.values()):
        failures.append("a cognition job has canonical-write authority")
    cur.execute("select count(*) from ops.job_definition where legacy_disabled_at is not null")
    if fetchone_required(cur.fetchone(), "legacy schedule cutover count")[0]:
        failures.append("a legacy schedule was disabled before acceptance")
    return failures, len(expected), len(expected_cognition), not_checked


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("control-plane-registry-gate: DATABASE_URL is required", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        failures, workflows, cognition, _ = compare(cur, manifest)
    if failures:
        print("control-plane-registry-gate FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    print(f"control-plane-registry-gate passed: {workflows} workflows and {cognition} cognition contracts exact; zero legacy cutovers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
