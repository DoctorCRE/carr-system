#!/usr/bin/env python3
# ci: db-gate
# doctrine: snapshot-rebuild-fidelity
"""A database rebuilt from db/schema.sql must carry 0273's role membership.

WHY THIS GATE EXISTS (open loop #506 finding 3, 2026-08-22). The snapshot's
claim is that db/schema.sql plus the pending migrations fully describe the
applied database. Migration 0273 grants the carr_authority privilege bundle to
the externally provisioned human login roles. The day a snapshot refresh carried
the ledger past 0273, that grant stopped replaying anywhere — and nothing in the
snapshot carried it, because the CARR GRANTS section admits only app roles and
neondb_owner as members and neither login role is either. So the ledger said
0273 applied while the file it vouches for described a database where it never
had. A rebuild where carr_authority_joe exists came up with the bundle holding
its grants and NOBODY holding the bundle, which fails the authority privilege
contract — and the migration runner will never re-apply 0273, because its ledger
row is already there.

It was found by an independent review seat, called the sharpest open finding by
a second session, and walked past by two more. It was never reproduced against a
database until this gate, which is the actual reason it survived: every check
that could have caught it was reading a file.

WHAT THIS PROVES, against real Postgres, both ways round — because a guard is
only correct if BOTH of its branches are:

  1. ABSENT. With the login role absent, loading the preamble succeeds, grants
     nothing, and says so. This is the ordinary case: CI, a disposable local
     cluster, and any rebuild on a machine that has no authority credential.
     If this branch raised, the guard would abort every rebuild everywhere.
  2. PRESENT. With the login role present, loading the preamble grants it the
     bundle. This is the branch that carries 0273, and the one that was missing.
  3. THE GUARD IS LOAD-BEARING, not decoration: the unguarded statement the
     snapshot would otherwise have to emit raises undefined_object against an
     absent role, which is why this is rendered as a guarded block at all.
  4. JOINED, NEVER CREATED. Loading the preamble must not bring the login role
     into existence. These are human authority credentials provisioned outside
     this repository; a snapshot that minted a local carr_authority_joe would
     manufacture a principal that authenticates as Joe's authority on every
     machine that rebuilds.

SAFETY OF THE TEST ITSELF. The role this gate creates for branch 2 is made
NOLOGIN and with no password — the guard tests existence in pg_roles and nothing
else, so proving the branch never requires minting something that can
authenticate. Every statement runs inside one transaction that is always rolled
back, so the gate leaves no role, no membership, and no row behind.

Run: DATABASE_URL=... python3 ops/snapshot-authority-membership-gate.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from migration_source import migration_names, read_migration
SNAPSHOT = REPO / "db" / "schema.sql"
BUNDLE = "carr_authority"
# The externally provisioned authority logins, DERIVED FROM THE MIGRATIONS
# rather than written down here. Hard-coding them would pin today's answer and
# miss the next one: the day a migration introduces a third authority login, a
# literal list in this file would still say two, and the gate would go on
# passing while the snapshot dropped a membership — which is precisely the
# shape of the defect it exists to catch. Deriving it means the gate starts
# failing on the commit that adds the role, not on the rebuild months later.
AUTHORITY_LOGIN_PATTERN = re.compile(r"\bcarr_authority_[a-z][a-z0-9_]*\b")


def authority_logins(repo: Path = REPO) -> tuple[str, ...]:
    """Every authority login role the migrations name, in stable order."""
    found: set[str] = set()
    for name in sorted(migration_names(repo)):
        found.update(AUTHORITY_LOGIN_PATTERN.findall(read_migration(repo, name)))
    if not found:
        # Never silently proceed with an empty expectation: an empty set would
        # make every coverage assertion below vacuously true.
        raise SystemExit("snapshot-authority-membership-gate: no authority login "
                         "role found in migrations/ — the derivation is broken, "
                         "and an empty set would pass every check vacuously")
    return tuple(sorted(found))


AUTHORITY_LOGINS = authority_logins()

failures: list[str] = []
checked = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checked
    checked += 1
    if ok:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def preamble_block(text: str) -> str:
    """The role preamble's DO block, taken from the shipped snapshot itself.

    Read from db/schema.sql rather than from the generator, because the file is
    what a rebuild actually loads. A generator that emits the right thing into a
    file nobody ships would pass a test written against the generator.
    """
    marker = "-- CARR ROLE PREAMBLE (bin/schema-snapshot.sh) — not produced by pg_dump."
    start = text.find(marker)
    if start < 0:
        raise SystemExit("snapshot-authority-membership-gate: no role preamble marker")
    open_at = text.find("\ndo $$", start)
    close_at = text.find("\nend $$;", open_at)
    if open_at < 0 or close_at < 0:
        raise SystemExit("snapshot-authority-membership-gate: preamble block not delimited")
    return text[open_at + 1:close_at + len("\nend $$;")]


def members_of(cur, bundle: str) -> set[str]:
    cur.execute(
        """select mem.rolname
             from pg_auth_members m
             join pg_roles gr  on gr.oid  = m.roleid
             join pg_roles mem on mem.oid = m.member
            where gr.rolname = %s""",
        (bundle,),
    )
    return {r[0] for r in cur.fetchall()}


def role_exists(cur, role: str) -> bool:
    cur.execute("select 1 from pg_roles where rolname = %s", (role,))
    return cur.fetchone() is not None


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("snapshot-authority-membership-gate: FAIL — DATABASE_URL is required",
              file=sys.stderr)
        return 1

    text = SNAPSHOT.read_text(encoding="utf-8")
    block = preamble_block(text)

    # Scoped to the block it is about: the membership loop, not the preamble at
    # large. A preamble that still creates every role but has lost the join
    # would pass any looser assertion.
    membership_loop = re.search(
        r"foreach r in array array\[(?P<roles>[^\]]*)\] loop\s*"
        r"if exists \(select 1 from pg_roles where rolname = r\) then\s*"
        r"execute format\('grant carr_authority to %I', r\);",
        block,
    )
    check("the snapshot's preamble carries a guarded carr_authority membership loop",
          membership_loop is not None,
          "db/schema.sql no longer joins the authority logins to the bundle; "
          "0273's effect is absent from the rebuild path again")
    named = set(re.findall(r"'([a-z_][a-z0-9_]*)'",
                           membership_loop.group("roles") if membership_loop else ""))
    check("both authority logins 0273 names are covered",
          named == set(AUTHORITY_LOGINS),
          f"loop names {sorted(named)}, 0273 names {sorted(AUTHORITY_LOGINS)}")

    # The security property, asserted against the creation loop specifically:
    # the login roles are joined, never created.
    creation_loop = re.search(r"foreach r in array array\[(?P<roles>[^\]]*)\] loop\s*"
                              r"if not exists", block)
    created = set(re.findall(r"'([a-z_][a-z0-9_]*)'",
                             creation_loop.group("roles") if creation_loop else ""))
    check("no authority login role is created by the snapshot",
          not (created & set(AUTHORITY_LOGINS)),
          f"the preamble would mint {sorted(created & set(AUTHORITY_LOGINS))}, "
          "manufacturing a principal that authenticates as a human's authority")

    # THE DESTRUCTIVE SETUP BELOW IS FENCED TO LOOPBACK. Proving the absent
    # branch means guaranteeing absence, which means dropping the role if the
    # substrate already carries it. ci.sh already refuses a non-loopback
    # CARR_CI_DATABASE_URL, but a gate that drops roles should not rely on
    # somebody else's check for that — it states its own precondition and stops.
    host = re.search(r"@([^/:?]+)", dsn)
    hostname = host.group(1) if host else ""
    if hostname not in ("localhost", "127.0.0.1", "::1", "[::1]"):
        print("snapshot-authority-membership-gate: FAIL — refusing to run against "
              f"non-loopback host {hostname!r}; this gate drops and recreates roles "
              "inside a rolled-back transaction and is for throwaway databases only",
              file=sys.stderr)
        return 1

    # ONE FIXED PROBE, not whichever role happens to be absent. An earlier
    # revision chose the probe from the substrate's current state, and on this
    # Mac that made the gate report differently depending on which OTHER gate
    # had run first: several Program 6 gates mint both authority logins, and a
    # staging gate mints them on an autocommit connection. A gate whose coverage
    # depends on its neighbours proves nothing repeatable, so absence is
    # MANUFACTURED here and restored by the rollback.
    probe = AUTHORITY_LOGINS[0]

    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            started_present = role_exists(cur, probe)
            started_canlogin = None
            if started_present:
                cur.execute("select rolcanlogin from pg_roles where rolname = %s", (probe,))
                row = cur.fetchone()
                started_canlogin = row[0] if row else None

            def make_absent() -> None:
                if role_exists(cur, probe):
                    # DROP OWNED BY clears ACL entries and ownership in this
                    # database; memberships go with the role itself.
                    cur.execute(f"drop owned by {probe}")
                    cur.execute(f"drop role {probe}")

            # 1. ABSENT BRANCH — the ordinary case: CI, a disposable cluster,
            #    any rebuild on a machine with no authority credential.
            cur.execute("savepoint absent_branch")
            make_absent()
            check("absence can be manufactured for the absent branch",
                  not role_exists(cur, probe))
            cur.execute(block)
            check("loading the preamble with the login role absent does not raise",
                  True)
            check("nothing is granted the bundle on behalf of the absent role",
                  probe not in members_of(cur, BUNDLE))
            check("the absent login role is not brought into existence",
                  not role_exists(cur, probe))
            cur.execute("rollback to savepoint absent_branch")

            # 2. THE GUARD IS LOAD-BEARING. Same absent state, unguarded
            #    statement — what the snapshot would have to emit without the
            #    guard. It must abort, which is the whole reason for the block.
            cur.execute("savepoint unguarded")
            make_absent()
            try:
                cur.execute(f"grant {BUNDLE} to {probe}")
            except psycopg.errors.UndefinedObject:
                raised = True
            else:
                raised = False
            cur.execute("rollback to savepoint unguarded")
            check("an unguarded grant against the absent role aborts the load",
                  raised,
                  "if this stops raising, the guard is no longer the thing "
                  "keeping a rebuild from dying on an absent credential")

            # 3. PRESENT BRANCH — the one that carries 0273, and the one that
            #    was missing. NOLOGIN and no password when this gate mints the
            #    role itself: the guard reads pg_roles only, so proving the
            #    branch never requires something that can authenticate.
            cur.execute("savepoint present_branch")
            if not role_exists(cur, probe):
                cur.execute(f"create role {probe} nologin")
            cur.execute(f"revoke {BUNDLE} from {probe}")
            check("the probe holds no bundle before the preamble runs",
                  probe not in members_of(cur, BUNDLE))
            cur.execute(block)
            check("loading the preamble with the login role present grants the bundle",
                  probe in members_of(cur, BUNDLE),
                  "0273's effect is still absent from a database rebuilt from "
                  "the snapshot — loop #506 finding 3, unfixed")
            cur.execute(block)
            check("re-running the preamble is idempotent for the membership",
                  probe in members_of(cur, BUNDLE))
            cur.execute("select rolcanlogin from pg_roles where rolname = %s", (probe,))
            after = cur.fetchone()
            check("the preamble never changes the joined role's login-ness",
                  after is not None
                  and (after[0] is False if not started_present else after[0] == started_canlogin),
                  "joining a bundle must not turn an authority credential into "
                  "a login, nor take one away")
            cur.execute("rollback to savepoint present_branch")

            check("the gate restores the substrate it found",
                  role_exists(cur, probe) == started_present)
        conn.rollback()

    print(f"\npassed {checked - len(failures)} · failed {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
