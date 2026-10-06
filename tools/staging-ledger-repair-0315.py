#!/usr/bin/env python3
"""One-time repair: replay the skipped 0315 migration on the ISOLATED STAGING
project, then record its truthful ledger row.

WHAT HAPPENED (found 2026-08-26, heavy-build rollout). The staging Neon
project's schema_migrations ledger has a hole: 0315_program5_forward_fix_
rehearsal.sql was never applied there, while 0316-0319 were. tools/migrate.py
correctly fails closed on the reorder, so staging can accept no further
migrations at all — and the Worker's /release schema-identity query fails
against staging because 0315 is what gave v_schema_ledger its sha256 column.
That in turn fails every typed staging readback, which blocks the
approval-eligible rehearsal evidence production releases require.

WHY THIS TOOL AND NOT A HAND EDIT. The house pattern is
tools/ledger-repair.py: markers read the database's own state, nothing is
recorded on trust, and the tool can repair exactly one named situation, then
does nothing at all, forever. This tool follows it, with one difference
forced by the situation: 0315 here is NOT physically applied, so the repair
applies the exact checked-in migration bytes and the ledger row in one
transaction, then verifies the migration's own effects before committing.

WHY IT CANNOT TOUCH PRODUCTION. The guard is marker-based, not host-based:
it refuses unless the connected database shows the exact skew it repairs —
0315 absent from the ledger while 0316 through 0319 are present, and zero
ops.*forward_fix_rehearsal* functions. Production has 0315 applied, so this
tool refuses there by construction. It prints the host it connected to
either way.

Usage (through the documented staging door):
    .venv/bin/python tools/db-tap.py --project staging run \
        tools/staging-ledger-repair-0315.py            # dry run
    CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --project staging \
        --reason "replay skipped 0315 on staging" run \
        tools/staging-ledger-repair-0315.py --apply
"""

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

import psycopg

MIGRATION = (Path(__file__).resolve().parent.parent
             / "migrations" / "0315_program5_forward_fix_rehearsal.sql")
LATER_APPLIED_REQUIRED = [
    "0316_rule_delivery_audit_counts.sql",
    "0317_atomic_rule_delivery_cutover.sql",
    "0318_tour_operations_foundation.sql",
    "0319_engineering_envelope_writer_successor.sql",
]


def fail(msg: str) -> NoReturn:
    sys.exit(f"staging-ledger-repair-0315: {msg}")


def one(cur: Any) -> tuple[Any, ...]:
    row = cur.fetchone()
    if row is None:
        fail("query returned no row")
    return row


def main() -> None:
    apply = "--apply" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        fail("DATABASE_URL is not set")
    sql_text = MIGRATION.read_text()
    digest = hashlib.sha256(sql_text.encode()).hexdigest()
    with psycopg.connect(url) as conn:
        cur = conn.cursor()
        print("host:", conn.info.host)

        cur.execute("select filename from schema_migrations")
        applied = {r[0] for r in cur.fetchall()}
        if MIGRATION.name in applied:
            print(f"{MIGRATION.name} is already recorded — nothing to repair, ever.")
            return
        missing_later = [n for n in LATER_APPLIED_REQUIRED if n not in applied]
        if missing_later:
            fail("this database does not show the one skew this tool repairs "
                 f"(later migrations not applied: {', '.join(missing_later)}); refusing")

        cur.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='ops' and p.proname like %s",
            ("%forward_fix_rehearsal%",))
        fn_count = one(cur)[0]
        if fn_count != 0:
            fail(f"marker failed: {fn_count} forward_fix_rehearsal function(s) already "
                 "exist while 0315 is unrecorded — reconcile by hand, not with this tool")

        print(f"{MIGRATION.name}: genuinely absent (ledger hole confirmed, 0 objects)")
        if not apply:
            print("dry run — pass --apply")
            return

        cur.execute(sql_text)
        cur.execute(
            "insert into schema_migrations (filename, sha256) values (%s, %s)",
            (MIGRATION.name, digest))

        # Verify the migration's own effects before committing anything.
        cur.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='ops' and p.proname like %s",
            ("%forward_fix_rehearsal%",))
        if one(cur)[0] == 0:
            fail("post-apply verification failed: rehearsal functions still absent; rolling back")
        cur.execute(
            "select count(*) from information_schema.columns "
            "where table_name='v_schema_ledger' and column_name='sha256'")
        if one(cur)[0] != 1:
            fail("post-apply verification failed: v_schema_ledger has no sha256 column; rolling back")

        conn.commit()
        print(f"committed — {MIGRATION.name} applied and recorded (sha256 {digest[:12]}…)")


main()
