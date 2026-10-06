#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Personal rows are write-fenced by the database, per partner (0573, 0579).

Decisions 04101316 and 443fe82a (audit ruling 5, 2026-09-24). Two proofs, one
rolled-back transaction on the disposable migration-lane database:

  Joe, 2026-09-24: partners need not hide personal doctrine or memories from
  each other. 'personal' scopes which partner a row APPLIES to and who may
  CHANGE it, not who may read it (migration 0579 opened memory reads).

  ENUMERATION. Every public/ops table whose CHECK constraints mention the
  'personal' scope is a table that can hold a partner's own rows. Each one
  must either have row security enabled with a write policy that reads the
  server-set carr.sponsoring_human_slug, be named in ACCEPTED_SHARED with the
  reason its "personal" marker is not privacy, or be named in PENDING with the
  follow-up that will fence it. A new table that grows a personal scope without
  one of the three fails here, before it can leak.

  BEHAVIOUR on public.memory_item, as the real runtime role carr_writer: every
  session (Joe, Dell, a machine) reads every row; a session cannot insert, or
  update, a personal row owned by another partner; a login that is a member of
  carr_writer (the deployed app_writer shape) is fenced the same way;
  carr_reader reads every row rather than failing; carr_backup, where the role
  exists, reads every row so the nightly dump stays complete. Applying only a
  partner's own memories is recall-memory's query filter, tested in
  mcp-server/test/memory-kernel.test.mjs.

  WHAT THIS DOES NOT PROVE. The sponsor is a transaction-local setting the
  trusted server writes. A session holding a writer or reader login directly
  can set it itself; the fence is against every query path the runtime owns,
  not against a person holding a database credential.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role

GUC = "carr.sponsoring_human_slug"

# A table here carries a 'personal' marker that is NOT privacy. Each entry
# names why, and changes only with a logged decision.
ACCEPTED_SHARED = {
    "public.loop_item": (
        "decision 443fe82a: add-loop stamps every open loop and idea 'personal' "
        "to whoever filed it, automation included; the loop board is the shared "
        "work queue both partners and the system drain"),
    "public.doctrine_document": (
        "decision 9c06bf1e (Joe 2026-09-24): partners need not hide personal doctrine from each "
        "other; 'personal' scopes which partner a document APPLIES to "
        "(search-doctrine and resolve-doctrine-rules filter by owner), while "
        "read-doctrine, doctrine-sections and doctrine-index show it to both "
        "with owner_slug; writes stay owner-only"),
}

# A table here IS private and is NOT yet fenced by the database; the server's
# own filter is its only guard until the named follow-up lands. Listing it is an
# admission, not an exemption: the entry must be removed when its policy ships.
PENDING: dict[str, str] = {}


def fail(message: str) -> int:
    print(f"private-row sponsor RLS gate: {message}", file=sys.stderr)
    return 1


def main() -> int:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("private-row sponsor RLS gate requires DATABASE_URL (the disposable migration-lane database)",
              file=sys.stderr)
        return 78
    with rollback_only_connection(dsn) as conn:
        with conn.cursor() as cur:
            personal_tables = sorted({row[0] for row in cur.execute("""
                select c.oid::regclass::text
                  from pg_constraint k
                  join pg_class c on c.oid = k.conrelid
                  join pg_namespace n on n.oid = c.relnamespace
                 where k.contype = 'c'
                   and n.nspname in ('public', 'ops')
                   and pg_get_constraintdef(k.oid) ilike '%''personal''%'""").fetchall()})
            personal_tables = [t if "." in t else f"public.{t}" for t in personal_tables]
            if "public.memory_item" not in personal_tables:
                return fail(f"enumeration is vacuous: memory_item not found among {personal_tables}")
            uncovered = []
            for table in personal_tables:
                if table in ACCEPTED_SHARED or table in PENDING:
                    continue
                rls, insert_fenced, update_fenced = cur.execute("""
                    select c.relrowsecurity,
                           exists (select 1 from pg_policy p
                                    where p.polrelid = c.oid and p.polcmd in ('a', '*')
                                      and coalesce(pg_get_expr(p.polwithcheck, p.polrelid), '') like %s),
                           exists (select 1 from pg_policy p
                                    where p.polrelid = c.oid and p.polcmd in ('w', '*')
                                      and coalesce(pg_get_expr(p.polqual, p.polrelid), '') like %s)
                      from pg_class c where c.oid = %s::regclass""",
                    (f"%{GUC}%", f"%{GUC}%", table)).fetchone()
                if not (rls and insert_fenced and update_fenced):
                    uncovered.append(table)
            stale = sorted((set(ACCEPTED_SHARED) | set(PENDING)) - set(personal_tables))
            if stale:
                return fail(f"ACCEPTED_SHARED or PENDING names tables with no personal scope any more: {stale}")
            for table in PENDING:
                fenced = cur.execute("select relrowsecurity from pg_class where oid=%s::regclass",
                                     (table,)).fetchone()[0]
                if fenced:
                    return fail(f"{table} now has row security; move it out of PENDING")
            if uncovered:
                return fail("tables that can hold a partner's personal rows have no sponsor policy "
                            f"and no accepted-shared reason: {uncovered}")

            # Behaviour. Fixtures are written as the migration owner, which
            # bypasses row security, then read back as carr_writer.
            actors = {}
            for slug in ("joe", "dell"):
                row = cur.execute("select id from public.actor where slug=%s and kind='human'", (slug,)).fetchone()
                if row is None:
                    row = cur.execute(
                        "insert into public.actor (slug, kind, display_name) values (%s,'human',%s) returning id",
                        (slug, slug.title())).fetchone()
                actors[slug] = row[0]
            observer = actors["joe"]
            tag = f"rls-gate-{uuid.uuid4()}"
            for scope, owner in (("shared", None), ("personal", actors["joe"]), ("personal", actors["dell"])):
                cur.execute(
                    "insert into public.memory_item (organization_tenant_id, kind, statement, scope, "
                    "owner_actor_id, observed_by_actor_id) values ('carr-internal','fact',%s,%s,%s,%s)",
                    (f"{tag} {scope} {owner}", scope, owner, observer))

            def visible() -> dict[str, int]:
                rows = cur.execute(
                    "select scope, owner_actor_id from public.memory_item where statement like %s",
                    (f"{tag}%",)).fetchall()
                seen = {"shared": 0, "joe": 0, "dell": 0}
                for scope, owner in rows:
                    key = "shared" if scope == "shared" else ("joe" if owner == actors["joe"] else "dell")
                    seen[key] += 1
                return seen

            grant_settable_runtime_roles(cur, "carr_writer")
            set_local_role(cur, "carr_writer")
            everything = {"shared": 1, "joe": 1, "dell": 1}
            expected = {"joe": everything, "dell": everything, "": everything}
            observed = {}
            for sponsor, want in expected.items():
                cur.execute("select set_config(%s, %s, true)", (GUC, sponsor))
                observed[sponsor or "machine"] = got = visible()
                if got != want:
                    return fail(f"sponsor {sponsor or '<none>'!r} saw {got}, expected {want}")

            cur.execute("select set_config(%s, 'dell', true)", (GUC,))
            cur.execute("savepoint cross_owner")
            try:
                cur.execute(
                    "insert into public.memory_item (organization_tenant_id, kind, statement, scope, "
                    "owner_actor_id, observed_by_actor_id) values ('carr-internal','fact',%s,'personal',%s,%s)",
                    (f"{tag} forged", actors["joe"], actors["dell"]))
            except psycopg.errors.InsufficientPrivilege:
                cur.execute("rollback to savepoint cross_owner")
            else:
                return fail("Dell's session inserted a personal memory owned by Joe")

            # A cross-partner UPDATE (review of PR 1183): Dell's session can
            # read Joe's personal row (0579) but the update policy still
            # excludes it, so an update aimed at it touches nothing.
            cur.execute("update public.memory_item set confidence = 0.9 "
                        "where statement like %s and owner_actor_id = %s",
                        (f"{tag}%", actors["joe"]))
            if cur.rowcount != 0:
                return fail(f"Dell's session updated {cur.rowcount} of Joe's personal memories")

            cur.execute("reset role")

            # The deployed Worker logs in as app_writer, a MEMBER of
            # carr_writer, not as carr_writer itself (review of PR 1183). Prove
            # the fence through that shape: the real app_writer where it exists,
            # else a login created inside this rolled-back transaction.
            login = "app_writer"
            if not cur.execute("select 1 from pg_roles where rolname='app_writer'").fetchone():
                login = f"rls_gate_login_{uuid.uuid4().hex[:8]}"
                cur.execute(f"create role {login} login in role carr_writer")
            grant_settable_runtime_roles(cur, login)
            set_local_role(cur, login)
            cur.execute("select set_config(%s, 'joe', true)", (GUC,))
            observed[f"{login} as joe"] = got = visible()
            if got != everything:
                return fail(f"{login} (member of carr_writer) as Joe saw {got}")
            cur.execute("update public.memory_item set confidence = 0.9 "
                        "where statement like %s and owner_actor_id = %s",
                        (f"{tag}%", actors["dell"]))
            if cur.rowcount != 0:
                return fail(f"{login} as Joe updated {cur.rowcount} of Dell's personal memories")
            cur.execute("reset role")

            # carr_reader holds actor (id, slug) only. A reader query must read
            # every row, never fail on a policy lookup (0574, 0579).
            grant_settable_runtime_roles(cur, "carr_reader")
            set_local_role(cur, "carr_reader")
            cur.execute("select set_config(%s, '', true)", (GUC,))
            try:
                observed["carr_reader"] = got = visible()
            except psycopg.errors.InsufficientPrivilege as exc:
                return fail(f"carr_reader cannot read memory_item at all: {exc}")
            if got != everything:
                return fail(f"carr_reader with no sponsor saw {got}")
            cur.execute("reset role")
            backup = None
            if cur.execute("select 1 from pg_roles where rolname='carr_backup'").fetchone():
                grant_settable_runtime_roles(cur, "carr_backup")
                set_local_role(cur, "carr_backup")
                cur.execute("select set_config(%s, '', true)", (GUC,))
                backup = visible()
                if backup != {"shared": 1, "joe": 1, "dell": 1}:
                    return fail(f"carr_backup saw {backup}; the nightly dump would be short")
                cur.execute("reset role")

    print(json.dumps({
        "contract": "private-row-sponsor-rls.v1",
        "personal_scope_tables": personal_tables,
        "accepted_shared": sorted(ACCEPTED_SHARED),
        "pending_not_yet_fenced": sorted(PENDING),
        "visible_by_session": observed,
        "carr_backup": backup if backup is not None else "role absent on this database",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
