#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only proof for migration 0852 (correct-party-identity, rule 8cddc6ad).

party_reference_counts decides whether an org row is shared before the verb
renames it, so a wrong count renames a row other records ride. This proves, as
the role the verb runs under (carr_writer), that the count:

1. sees other people on the org (party.org_id) and skips the corrected party;
2. ignores merged tombstones and soft-deleted people;
3. counts a non-person reference (a client row on the org) as sharing;
4. is not executable by carr_reader.

It also proves carr_writer can call the shared org helpers the verb uses
(org_identity_key, org_party_id) and carr_reader can read the undo view.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import urlparse

import psycopg

from gate_runtime_role import rollback_only_connection


def one(cur: psycopg.Cursor[Any], query: str, args: tuple[object, ...] = ()) -> tuple[Any, ...]:
    row = cur.execute(query, args).fetchone()
    if row is None:
        raise RuntimeError(f"party identity gate expected one row: {query[:120]}")
    return tuple(row)


def counts(cur: psycopg.Cursor[Any], party: uuid.UUID, exclude: uuid.UUID | None) -> dict[str, int]:
    cur.execute("set local role carr_writer")
    rows = cur.execute("select source, n from party_reference_counts(%s,%s)", (party, exclude)).fetchall()
    cur.execute("reset role")
    return {str(source): int(n) for source, n in rows}


def party(cur: psycopg.Cursor[Any], actor: Any, kind: str, name: str, org: uuid.UUID | None = None) -> uuid.UUID:
    return one(cur, """insert into party(kind,name,org_id,created_by,updated_by)
                        values(%s,%s,%s,%s,%s) returning id""", (kind, name, org, actor, actor))[0]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    host = urlparse(dsn).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("party identity gate requires a loopback DATABASE_URL or CARR_LOCAL_PG_DSN")

    assertions = 0
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        actor = one(cur, "select id from actor where active order by slug limit 1")[0]
        tag = uuid.uuid4().hex[:10]
        org = party(cur, actor, "org", f"Gate Harbor Legal {tag}")
        target = party(cur, actor, "person", "Gate Target", org)
        other = party(cur, actor, "person", "Gate Other", org)

        if counts(cur, org, target) != {"party.org_id": 1}:
            raise RuntimeError(f"expected one other person on the org: {counts(cur, org, target)}")
        if counts(cur, org, None) != {"party.org_id": 2}:
            raise RuntimeError("p_exclude null must count every live person")
        assertions += 2

        tomb = party(cur, actor, "org", f"Gate Harbor Legal twin {tag}")
        cur.execute("update party set merged_into=%s where id=%s", (org, tomb))
        gone = party(cur, actor, "person", "Gate Departed", org)
        cur.execute("update party set deleted_at=now() where id=%s", (gone,))
        if counts(cur, org, target) != {"party.org_id": 1}:
            raise RuntimeError(f"tombstones or deleted people were counted: {counts(cur, org, target)}")
        assertions += 1

        cur.execute("update party set org_id=null where id=%s", (other,))
        if counts(cur, org, target) != {}:
            raise RuntimeError("an org with only the target on it must read unshared")
        status = one(cur, "select slug from client_status order by sort limit 1")[0]
        cur.execute("""insert into client(roster_ref,party_id,status,created_by,updated_by)
                       values(%s,%s,%s,%s,%s)""", (f"C-GATE-PID-{tag}", org, status, actor, actor))
        if counts(cur, org, target) != {"client.party_id": 1}:
            raise RuntimeError(f"a client row on the org must count as sharing: {counts(cur, org, target)}")
        assertions += 2

        cur.execute("savepoint reader_refused")
        try:
            cur.execute("set local role carr_reader")
            cur.execute("select * from party_reference_counts(%s,%s)", (org, target))
        except psycopg.errors.InsufficientPrivilege:
            cur.execute("rollback to savepoint reader_refused")
        else:
            raise RuntimeError("carr_reader must not execute party_reference_counts")
        assertions += 1

        cur.execute("set local role carr_writer")
        cur.execute("lock table party in exclusive mode")
        assertions += 1
        key = one(cur, "select org_identity_key(%s)", (f"  Gate  Harbor Legal {tag} ",))[0]
        if key != f"gate harbor legal {tag}":
            raise RuntimeError(f"org_identity_key changed shape: {key}")
        if one(cur, "select org_party_id(%s,%s)", (f"gate harbor legal {tag}", actor))[0] != org:
            raise RuntimeError("org_party_id must find the existing org by identity as carr_writer")
        cur.execute("reset role")
        cur.execute("set local role carr_reader")
        one(cur, "select count(*) from v_party_identity_correction")
        cur.execute("reset role")
        assertions += 3

    print(f"party-identity-local-pg-gate: PASS — {assertions} assertions on reference counting, "
          "tombstones, non-person sharing, and role grants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
