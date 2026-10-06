#!/usr/bin/env python3
# ci: db-gate
# doctrine: doctorcre-v5-astra-integration-review
"""Exercise V5-RW02's durable evidence SQL on the disposable CI database."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "mcp-server/test/salesforce-reconciliation-rw02-postgres.sql"


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    parts = psycopg.conninfo.conninfo_to_dict(dsn)
    host = str(parts.get("hostaddr") or parts.get("host") or "")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not dsn or not loopback:
        print("salesforce-reconciliation-rw02-local-pg-gate: refusing non-loopback DATABASE_URL",
              file=sys.stderr)
        return 1
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute("select exists (select 1 from schema_migrations where filename=%s)",
                    ("0726_salesforce_reconciliation_rw02_store.sql",))
        migration = cur.fetchone()
        if migration is None or not migration[0]:
            print("salesforce-reconciliation-rw02-local-pg-gate: migration 0726 missing",
                  file=sys.stderr)
            return 1
    proc = subprocess.run(["psql", "-X", dsn, "-v", "ON_ERROR_STOP=1", "-f", str(FIXTURE)],
                          cwd=REPO, stdin=subprocess.DEVNULL, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if proc.returncode:
        print(proc.stdout[-4000:], file=sys.stderr)
        return proc.returncode
    print("db-gate-proof: rw02 page-stop, exact replay, action isolation, and append-only grants exercised")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
