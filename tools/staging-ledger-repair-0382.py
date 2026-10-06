#!/usr/bin/env python3
"""One-time repair for the isolated staging project's genuine 0382 hole.

Staging records 0383 while 0382 is absent, and its ops.standing_guidance
function still has the exact pre-0382 invoker shape.  The ordinary forward-only
migrator correctly refuses that reordered history.  This tool repairs only that
marker-proven state: it verifies the immutable 0382 bytes against Production's
recorded digest, applies those bytes, verifies the narrow reader boundary, and
then records the truthful ledger row.

If execution is interrupted after the migration's own COMMIT but before the
ledger insert, an exact repaired boundary is the only accepted recovery state;
the next invocation replays the immutable, idempotent migration bytes before
recording the row.

Usage through the isolated staging door:
  .venv/bin/python tools/db-tap.py --project staging run \
      tools/staging-ledger-repair-0382.py
  CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --project staging \
      --reason "repair genuine staging 0382 hole" run \
      tools/staging-ledger-repair-0382.py --apply
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

import psycopg


REPO = Path(__file__).resolve().parent.parent
MIGRATION_NAME = "0382_standing_guidance_reader_boundary.sql"
MIGRATION = REPO / "migrations" / MIGRATION_NAME
EXPECTED_SHA256 = "a6ffe5f29e9224f263b0c6a90c414b4828915a5ed3265e52e8fadbe31ef8c2bc"
LATER_NAME = "0383_control_plane_not_configured_state.sql"
LATER_SHA256 = "f0cb86f97fcd87db8412be1f4c36544fe40f1ba9e524182bb3cb3b9ad3148bfa"
EXPECTED_RESULT = (
    "TABLE(source_rule_id uuid, statement text, human_quote text, taught_by text, "
    "personal_to text, scope jsonb, guidance_type text, is_constitution boolean)"
)
EXPECTED_CONFIG = ("search_path=pg_catalog, ops, public, pg_temp",)
EXPECTED_EXECUTE_GRANTEES = ("carr_reader", "carr_writer")
EXPECTED_LEGACY_BODY = """
  select r.id,r.statement,r.human_quote,teacher.display_name,owner.slug,g.scope,
         g.guidance_type,g.is_constitution
    from ops.v_guidance_current g
    join rule r on r.id=g.source_rule_id and r.status='active'
    join actor teacher on teacher.id=r.taught_by
    left join actor owner on owner.id=r.personal_to
   where exists (
           select 1
             from ops.v_guidance_registry_state s
             join ops.guidance_registry registry
               on registry.id=s.registry_id and registry.singleton
            where s.state='active'
         )
     and (r.personal_to is null or owner.slug=p_actor)
     and (
       g.is_constitution
       or (g.guidance_type='constraint' and exists (
         select 1 from ops.applicable_rules(p_workflow,p_surface,p_tier) ar
          where ar.rule_id=r.id))
     )
   order by g.is_constitution desc,r.personal_to nulls first,r.created_at,r.id
"""
EXPECTED_REPAIRED_BODY = """
  select r.id,r.statement,r.human_quote,teacher.display_name,owner.slug,g.scope,
         g.guidance_type,g.is_constitution
    from ops.v_guidance_current g
    join public.rule r on r.id=g.source_rule_id and r.status='active'
    join public.actor teacher on teacher.id=r.taught_by
    left join public.actor owner on owner.id=r.personal_to
   where exists (
           select 1
             from ops.v_guidance_registry_state s
             join ops.guidance_registry registry
               on registry.id=s.registry_id and registry.singleton
            where s.state='active'
         )
     and (r.personal_to is null or owner.slug=p_actor)
     and (
       g.is_constitution
       or (g.guidance_type='constraint' and exists (
         select 1 from ops.applicable_rules(p_workflow,p_surface,p_tier) ar
          where ar.rule_id=r.id))
     )
   order by g.is_constitution desc,r.personal_to nulls first,r.created_at,r.id
"""


def fail(message: str) -> NoReturn:
    sys.exit(f"staging-ledger-repair-0382: {message}")


def normalize_sql(value: str) -> str:
    # Whitespace is presentation-only in pg_proc.prosrc.  Case is not: SQL
    # string literals are case-sensitive, so folding the catalog body could
    # accept a semantically different predicate such as 'ACTIVE'.
    return re.sub(r"\s+", " ", value.strip())


def classify_state(applied: dict[str, str], boundary: str) -> str:
    """Return the sole safe action for marker-proven ledger/function state."""
    if MIGRATION_NAME in applied:
        if applied[MIGRATION_NAME] != EXPECTED_SHA256:
            raise ValueError("0382 ledger digest does not match immutable migration")
        if boundary != "repaired":
            raise ValueError("0382 is recorded but its exact repaired boundary is absent")
        return "already_recorded"
    if applied.get(LATER_NAME) != LATER_SHA256:
        raise ValueError("exact later 0383 ledger marker is absent or mismatched")
    if boundary in {"legacy", "repaired"}:
        # Replaying CREATE OR REPLACE plus the exact ACL statements is
        # idempotent.  Recovery must replay those immutable bytes rather than
        # infer that they ran from a similar-looking catalog postcondition.
        return "replay_and_record"
    raise ValueError("standing_guidance is neither the exact legacy nor repaired boundary")


def classify_boundary_row(row: tuple[Any, ...]) -> str:
    (
        security_definer,
        volatility,
        config,
        body,
        result,
        owner_is_current,
        execute_grantees,
        execute_grantable,
        reader_execute,
        writer_execute,
        reader_rule_statement,
        reader_actor_display_name,
    ) = row
    common = (
        volatility == "s"
        and result == EXPECTED_RESULT
        and owner_is_current
        and tuple(execute_grantees or ()) == EXPECTED_EXECUTE_GRANTEES
        and not execute_grantable
        and reader_execute
        and writer_execute
        and not reader_rule_statement
        and not reader_actor_display_name
    )
    normalized_body = normalize_sql(str(body))
    if (
        not security_definer
        and tuple(config or ()) == ()
        and common
        and normalized_body == normalize_sql(EXPECTED_LEGACY_BODY)
    ):
        return "legacy"
    if (
        security_definer
        and tuple(config or ()) == EXPECTED_CONFIG
        and common
        and normalized_body == normalize_sql(EXPECTED_REPAIRED_BODY)
    ):
        return "repaired"
    return "unknown"


def reader_projection_works(cur: Any) -> bool:
    """Exercise the repaired function as the real Worker reader role."""
    cur.execute("savepoint staging_repair_reader_probe")
    ok = False
    try:
        cur.execute("set local role carr_reader")
        cur.execute("select count(*) from ops.standing_guidance('joe',null,null,null)")
        ok = cur.fetchone() is not None
    except psycopg.Error:
        ok = False
    finally:
        # Rolling back to the savepoint also restores the role after either the
        # successful probe or an aborted permission check.
        cur.execute("rollback to savepoint staging_repair_reader_probe")
    return ok


def boundary_state(cur: Any) -> str:
    cur.execute(
        """select p.prosecdef,
                  p.provolatile,
                  coalesce(p.proconfig, '{}'::text[]),
                  p.prosrc,
                  pg_catalog.pg_get_function_result(p.oid),
                  pg_catalog.pg_get_userbyid(p.proowner) = current_user,
                  coalesce((
                    select array_agg(grantee_name order by grantee_name)
                      from (
                        select case when acl.grantee=0 then 'PUBLIC'
                                    else pg_catalog.pg_get_userbyid(acl.grantee) end
                                 as grantee_name
                          from pg_catalog.aclexplode(
                                 coalesce(p.proacl,
                                          pg_catalog.acldefault('f',p.proowner))) acl
                         where acl.privilege_type='EXECUTE'
                           and acl.grantee<>p.proowner
                      ) direct_execute
                  ), '{}'::text[]),
                  coalesce((
                    select pg_catalog.bool_or(acl.is_grantable)
                      from pg_catalog.aclexplode(
                             coalesce(p.proacl,
                                      pg_catalog.acldefault('f',p.proowner))) acl
                     where acl.privilege_type='EXECUTE'
                       and acl.grantee<>p.proowner
                  ), false),
                  has_function_privilege('carr_reader', p.oid, 'EXECUTE'),
                  has_function_privilege('carr_writer', p.oid, 'EXECUTE'),
                  has_column_privilege(
                    'carr_reader','public.rule','statement','SELECT'),
                  has_column_privilege(
                    'carr_reader','public.actor','display_name','SELECT')
             from pg_catalog.pg_proc p
             join pg_catalog.pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'ops'
              and p.proname = 'standing_guidance'
              and pg_catalog.pg_get_function_identity_arguments(p.oid) =
                  'p_actor text, p_workflow text, p_surface text, p_tier text'"""
    )
    rows = cur.fetchall()
    if len(rows) != 1:
        return "unknown"
    state = classify_boundary_row(rows[0])
    if state == "repaired" and not reader_projection_works(cur):
        return "unknown"
    return state


def main() -> None:
    apply = "--apply" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        fail("DATABASE_URL is not set")

    sql_text = MIGRATION.read_text()
    digest = hashlib.sha256(sql_text.encode()).hexdigest()
    if digest != EXPECTED_SHA256:
        fail(f"immutable 0382 digest mismatch: got {digest}, expected {EXPECTED_SHA256}")

    with psycopg.connect(url) as conn:
        cur = conn.cursor()
        print("host:", conn.info.host)
        cur.execute(
            "select filename, sha256 from public.schema_migrations "
            "where filename in (%s, %s)",
            (MIGRATION_NAME, LATER_NAME),
        )
        applied: dict[str, str] = dict(cur.fetchall())
        boundary = boundary_state(cur)
        try:
            action = classify_state(applied, boundary)
        except ValueError as exc:
            fail(f"marker refusal: {exc}")

        if action == "already_recorded":
            print("0382 is already recorded with the immutable digest — nothing to do, ever.")
            return
        print(f"exact staging skew confirmed: action={action}")
        if not apply:
            print("dry run — pass --apply")
            return

        if action != "replay_and_record":
            fail(f"unexpected repair action: {action}")
        conn.rollback()
        cur.execute(sql_text)
        if boundary_state(cur) != "repaired":
            fail("post-replay boundary verification failed; refusing ledger record")
        print("replayed exact 0382 bytes; repaired boundary verified")

        if boundary_state(cur) != "repaired":
            fail("repaired boundary changed before ledger record; refusing")
        cur.execute(
            "insert into public.schema_migrations (filename, sha256) values (%s, %s)",
            (MIGRATION_NAME, EXPECTED_SHA256),
        )
        conn.commit()

        cur.execute(
            "select sha256 from public.schema_migrations where filename=%s",
            (MIGRATION_NAME,),
        )
        row = cur.fetchone()
        if row != (EXPECTED_SHA256,) or boundary_state(cur) != "repaired":
            fail("post-commit ledger/boundary verification failed")
        print(f"committed and verified — {MIGRATION_NAME} ({EXPECTED_SHA256[:12]}…)")


if __name__ == "__main__":
    main()
