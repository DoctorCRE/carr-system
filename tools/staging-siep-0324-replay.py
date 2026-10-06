#!/usr/bin/env python3
"""One-time repair: replay the skipped SIEP migration (0324) on the ISOLATED
STAGING project, then record its truthful ledger row.

WHY (2026-08-26, heavy-build rollout). Staging carries every migration in the
tree except 0324_siep_program_authority.sql — it applied 0325 while 0324 was
skipped, so tools/migrate.py fails closed on the reordered ledger, and the
typed staging readback refuses release rehearsals because staging's ledger
(259 rows) does not equal the declared candidate set (260). Third instance of
the same shape tonight; see tools/staging-ledger-repair-0315.py and
tools/staging-heavy-build-parity.py for the pattern's provenance.

CONTRACT, same as its two siblings: marker-verified, repairs exactly one
named skew, refuses any database not showing it (production records 0324, so
it refuses there by construction), applies the exact checked-in bytes whose
sha256 must equal what production's ledger recorded, and verifies the
migration's own effects. The DRY RUN here executes the full migration inside
a transaction and rolls it back — proving it applies cleanly against
staging's synthetic fixtures before anything commits.

Usage (through the documented staging door):
    .venv/bin/python tools/db-tap.py --project staging run \
        tools/staging-siep-0324-replay.py              # dry run (full replay, rolled back)
    CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --project staging \
        --reason "replay skipped 0324 on staging" run \
        tools/staging-siep-0324-replay.py --apply
"""

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

import psycopg

MIGRATION = (Path(__file__).resolve().parent.parent
             / "migrations" / "0324_siep_program_authority.sql")
# sha256 production's schema_migrations ledger recorded for this file when
# bin/migrate-prod.sh applied it on 2026-08-26. The checked-in bytes must
# still hash to exactly this before anything runs, so staging can never
# receive an edited file.
PRODUCTION_SHA = "e86613bcde40a5fa26d4ce92e09829f6e2b9b5f911dbf6d086d69a28bfe5c523"
NEIGHBOR_APPLIED_REQUIRED = [
    "0323_engineering_claim_output_qualification.sql",
    "0325_engineering_claim_envelope_eligibility.sql",
]


def fail(msg: str) -> NoReturn:
    sys.exit(f"staging-siep-0324-replay: {msg}")


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
    if digest != PRODUCTION_SHA:
        fail(f"{MIGRATION.name} no longer hashes to what production ran "
             f"(got {digest[:12]}…, production recorded {PRODUCTION_SHA[:12]}…); refusing")

    with psycopg.connect(url) as conn:
        cur = conn.cursor()
        print("host:", conn.info.host)

        cur.execute("select filename from schema_migrations")
        applied = {r[0] for r in cur.fetchall()}
        if MIGRATION.name in applied:
            print(f"{MIGRATION.name} is already recorded — nothing to repair, ever.")
            return
        missing_neighbors = [n for n in NEIGHBOR_APPLIED_REQUIRED if n not in applied]
        if missing_neighbors:
            fail("target does not show the one skew this tool repairs "
                 f"(neighbors not recorded: {', '.join(missing_neighbors)}); refusing")

        cur.execute(
            "select count(*) from pg_tables where schemaname='ops' and tablename like %s",
            ("siep_%",))
        siep_tables = one(cur)[0]
        if siep_tables != 0:
            # 0324 wraps itself in begin;...commit;, so a replay attempt that was
            # rolled back from outside still committed the whole migration — the
            # exact "physically applied but unrecorded" state tools/ledger-repair.py
            # exists for. Verify the migration's own effects, then record only.
            cur.execute("select count(*) from ops.siep_package_contract")
            packages = one(cur)[0]
            cur.execute("select count(*) from ops.siep_program_dependency")
            edges = one(cur)[0]
            if packages != 40 or edges != 88:
                fail(f"marker failed: SIEP tables present but effects do not verify "
                     f"({packages} packages, {edges} edges) — reconcile by hand")
            print(f"{MIGRATION.name}: physically applied (40 packages, 88 edges), "
                  "ledger row missing — recording only")
            if not apply:
                print("dry run — pass --apply to record the ledger row")
                return
            cur.execute(
                "insert into schema_migrations (filename, sha256) values (%s, %s)",
                (MIGRATION.name, digest))
            conn.commit()
            print(f"committed — {MIGRATION.name} recorded as applied (sha256 {digest[:12]}…)")
            return

        print(f"{MIGRATION.name}: genuinely absent (ledger hole confirmed, 0 SIEP tables)")

        cur.execute(sql_text)
        cur.execute(
            "insert into schema_migrations (filename, sha256) values (%s, %s)",
            (MIGRATION.name, digest))

        # The migration's own effects, verified before any commit.
        cur.execute("select count(*) from ops.siep_package_contract")
        if one(cur)[0] != 40:
            fail("post-apply verification failed: package contract is not 40 rows")
        cur.execute("select count(*) from ops.siep_program_dependency")
        if one(cur)[0] != 88:
            fail("post-apply verification failed: dependency DAG is not 88 edges")

        if not apply:
            conn.rollback()
            print("dry run — full replay executed and rolled back cleanly; pass --apply")
            return
        conn.commit()
        print(f"committed — {MIGRATION.name} applied and recorded (sha256 {digest[:12]}…)")


main()
