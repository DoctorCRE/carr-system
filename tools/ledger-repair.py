#!/usr/bin/env python3
"""One-time repair: record migrations that are PHYSICALLY applied but missing
from schema_migrations.

WHY THIS EXISTS (found 2026-07-31 by the ORDER 3 session, on production).
`schema_migrations` stopped at 0012. Migrations 0013-0016 were applied to
production outside `tools/migrate.py` — their effects are all present and
verified, but the runner has no record of them, so it treats them as pending.
The next `run.sh migrate --apply` would therefore try to RE-run them; 0014's
`alter table client_status add column note` errors on a column that already
exists, and the run dies before it ever reaches the new migration. The ledger
has to match reality before anything else can be applied.

WHY IT IS NOT A GENERIC "MARK APPLIED" SWITCH. A flag that lets anyone stamp a
migration applied is a foot-gun that skips real migrations silently. This tool
can only ever record THESE FOUR files, and only after querying the database for
each one's own effect. A marker that does not pass leaves the file pending and
says so. When the four rows are in, this tool does nothing at all, forever.

Usage:
    DATABASE_URL=... .venv/bin/python tools/ledger-repair.py            # dry run
    DATABASE_URL=... .venv/bin/python tools/ledger-repair.py --apply
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration

import hashlib
import os

import psycopg

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

# (filename, marker sql, expected result) — each marker is that migration's own
# effect, read out of the live database. Nothing is recorded on trust.
MARKERS = [
    ("0013_active_book_derived.sql",
     "select count(*) from information_schema.columns "
     "where table_name='v_export_clients_active'", 10),
    ("0014_active_flags_and_semantics.sql",
     "select count(*) from client_status where is_active_pipeline", 14),
    ("0015_compiled_rules_view.sql",
     "select count(*) from information_schema.views "
     "where table_name='v_compiled_rules'", 1),
    ("0016_ref_index_resolver_view.sql",
     "select count(*) from information_schema.views "
     "where table_name='v_ref_index'", 1),
]


def main():
    apply = "--apply" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    with psycopg.connect(url) as conn:
        cur = conn.cursor()
        print("host:", conn.info.host)
        cur.execute("select filename from schema_migrations")
        already = {r[0] for r in cur.fetchall()}
        recorded = 0
        for name, sql, expected in MARKERS:
            if name in already:
                print(f"  {name}: already recorded, nothing to do")
                continue
            cur.execute(sql)
            got = cur.fetchone()[0]
            if got != expected:
                print(f"  {name}: MARKER FAILED (got {got}, expected {expected}) — "
                      "left PENDING, this one really does need applying")
                continue
            digest = hashlib.sha256(read_migration(MIGRATIONS.parent, name).encode()).hexdigest()
            print(f"  {name}: marker ok ({got}) -> recording as applied")
            if apply:
                cur.execute(
                    "insert into schema_migrations (filename, sha256) values (%s, %s)",
                    (name, digest))
                recorded += 1
        if apply:
            conn.commit()
            print(f"committed — {recorded} row(s) recorded")
        else:
            print("dry run — pass --apply")


main()
