"""Canonical read-only SIEP-11 projection of DB mutation capability surfaces."""

from __future__ import annotations

import hashlib
import json
from typing import Any


SECDEF_EXECUTE_SQL = r"""
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'
  union
  select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid
  join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end
  where other.rolname<>'carr_ci' and not other.rolsuper
), runtime_roles as (
  select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper
), functions as (
  select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,
         p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace
   where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')
), capabilities as (
  select f.*,acl.grantee,acl.privilege_type,acl.is_grantable
    from functions f
    cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl
)
select jsonb_build_object(
  'ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute',
  'ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')',
  'security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,
  'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),
  'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable)
from capabilities c left join pg_roles r on r.oid=c.grantee
where prosecdef and privilege_type='EXECUTE'
  and (grantee=0 or r.oid in (select oid from runtime_roles))
order by nspname collate "C",proname collate "C",args collate "C",
         coalesce(r.rolname,'public') collate "C"
"""

RELATION_DML_SQL = r"""
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'
  union
  select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid
  join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end
  where other.rolname<>'carr_ci' and not other.rolsuper
), runtime_roles as (
  select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper
), capabilities as (
  select n.nspname,c.relname,c.relkind,acl.grantee,acl.privilege_type,acl.is_grantable
    from pg_class c join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl
   where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')
)
select jsonb_build_object(
  'ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),
  'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,
  'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable)
from capabilities c left join pg_roles r on r.oid=c.grantee
where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
  and (grantee=0 or r.oid in (select oid from runtime_roles))
order by nspname collate "C",relname collate "C",
         coalesce(r.rolname,'public') collate "C",lower(privilege_type) collate "C"
"""

COLUMN_DML_SQL = r"""
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'
  union
  select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid
  join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end
  where other.rolname<>'carr_ci' and not other.rolsuper
), runtime_roles as (
  select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper
), capabilities as (
  select n.nspname,c.relname,c.relkind,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable
    from pg_attribute a join pg_class c on c.oid=a.attrelid
    join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(a.attacl) acl
   where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0
     and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')
)
select jsonb_build_object(
  'ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),
  'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,
  'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable)
from capabilities c left join pg_roles r on r.oid=c.grantee
where privilege_type in ('INSERT','UPDATE')
  and (grantee=0 or r.oid in (select oid from runtime_roles))
order by nspname collate "C",relname collate "C",attname collate "C",
         coalesce(r.rolname,'public') collate "C",lower(privilege_type) collate "C"
"""

JOB_DEFINITIONS_SQL = r"""
select jsonb_build_object(
  'ingress_key','job-definition:'||key||':'||version,'ingress_kind','job_definition',
  'key',key,'version',version,'enabled',enabled,'risk',risk,'owner_actor',owner_actor,
  'execution_kind',execution_kind,'entrypoint',coalesce(execution_contract->>'entrypoint',execution_contract->>'cognition_job'),
  'execution_contract',execution_contract,'inventory_contract',inventory_contract,'recurrence',recurrence,
  'state_contract',state_contract,'routing_contract',routing_contract,'filtering_contract',filtering_contract,
  'validation_contract',validation_contract,'retry_policy',retry_policy,'deduplication',deduplication,
  'completion_contract',completion_contract,'legacy_schedule',legacy_schedule)
from ops.job_definition order by key collate "C",version
"""

# Role membership changes effective DB authority without changing relation or
# function ACL rows.  Project the ENVIRONMENT-INVARIANT connected component of
# CARR *bundle* (non-login) roles, so the sealed census is identical on a
# from-scratch migration build and on production.  The walk deliberately stays
# inside the CARR namespace and never crosses into a login role, a superuser,
# neondb_owner, or a neon_*/pg_* platform role: those are provisioned per
# deployment (login/per-human roles) or by the provider (the
# EXPECTED_PROVIDER_REACHABLE_ROLES bundle in tools/cleanup-staging-app-writer.py)
# and are not part of what the migrations define.  A CARR role reaching a
# superuser/write-all bundle is NOT dropped silently — ESCALATION_SQL below is a
# separate, environment-independent assertion that catches exactly that, so
# narrowing the census for portability does not blind the monitor.  (Root cause:
# defect ec742b5f — the old walk lacked `not rolsuper` and swept neondb_owner +
# the whole Neon/PG platform graph in, giving 95 rows on a clone and 52 on a
# virgin DB, so no single seal could pass both.)
ROLE_AUTHORITY_SQL = r"""
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'
    and not rolcanlogin and not rolsuper
  union
  select other.oid
    from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid
    join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end
   where other.rolname ~ '^carr_' and other.rolname<>'carr_ci'
     and not other.rolcanlogin and not other.rolsuper
), role_rows as (
  select 'db-role:'||r.rolname ingress_key,
    jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role',
      'role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,
      'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,
      'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row
    from pg_roles r where r.oid in(select oid from connected)
), membership_rows as (
  select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,
    jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,
      'row_kind','membership','role',role.rolname,'member',member.rolname,
      'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row
    from pg_auth_members m join pg_roles role on role.oid=m.roleid
    join pg_roles member on member.oid=m.member
   where m.roleid in(select oid from connected) and m.member in(select oid from connected)
), ownership_rows as (
  select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,
    jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,
      'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner
   where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')
     and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  union all
  select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,
    jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,
      'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname)
    from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner
   where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')
     and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
)
select row from (select * from role_rows union all select * from membership_rows union all select * from ownership_rows) facts
order by ingress_key collate "C"
"""

# Environment-independent escalation assertion.  The portable census above
# deliberately excludes platform/superuser/login roles for reproducibility; this
# is the compensating control so that exclusion never hides a real escalation.
# It flags any CARR role that is a member of a superuser role or a neon_*/pg_*
# platform bundle (which is how write-all leaks in — e.g. neon_superuser carries
# pg_write_all_data).  It MUST be empty on a correctly-provisioned database; a
# from-scratch build has none.
#
# UPDATED 2026-09-03 (WR-000048 repair cascade). Finding cf7b565e (production
# tripping this on carr_program5_forward_fix_verifier -> neon_superuser) is
# NO LONGER CURRENT: measured today, the grant is gone from production,
# ESCALATION_SQL returns zero rows, and bypass_rls on that role reads false.
# Separately, and regardless of that measurement: the SQL-side v10 catalog
# guard previously carried a named exception for exactly that one role, so
# this scan's job was only to SURFACE a finding the guard itself tolerated.
# That exception has been REMOVED from the guard with no replacement -- any
# non-empty result from this query now also fails the v10 catalog
# current-check and the policy epoch closed, for every carr_ role including
# carr_program5_forward_fix_verifier, not merely reported here.
ESCALATION_SQL = r"""
select jsonb_build_object('ingress_key','db-role-escalation:'||mem.rolname||':'||g.rolname,
  'row_kind','escalation','carr_role',mem.rolname,'dangerous_bundle',g.rolname,
  'bundle_superuser',g.rolsuper) row
  from pg_auth_members m
  join pg_roles g on g.oid=m.roleid
  join pg_roles mem on mem.oid=m.member
 where mem.rolname ~ '^carr_' and (g.rolsuper or g.rolname ~ '^(neon_|pg_)')
 order by mem.rolname collate "C", g.rolname collate "C"
"""

QUERIES = {
    "secdef_execute": SECDEF_EXECUTE_SQL,
    "relation_dml": RELATION_DML_SQL,
    "column_dml": COLUMN_DML_SQL,
    "job_definitions": JOB_DEFINITIONS_SQL,
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def project(cur: Any) -> dict[str, list[dict[str, Any]]]:
    return {name: [row[0] for row in cur.execute(query)] for name, query in QUERIES.items()}


def summarize(projection: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    categories = {name: {"count": len(rows), "digest": digest(rows)}
                  for name, rows in projection.items()}
    combined_rows = sorted(
        [row for rows in projection.values() for row in rows],
        key=lambda row: row["ingress_key"],
    )
    return {"categories": categories, "combined": {"count": len(combined_rows), "digest": digest(combined_rows)}}


def project_role_authority(cur: Any) -> dict[str, Any]:
    rows = [row[0] for row in cur.execute(ROLE_AUTHORITY_SQL)]
    return {"count": len(rows), "digest": digest(rows), "rows": rows}


def project_escalation(cur: Any) -> dict[str, Any]:
    """CARR roles reaching a superuser/write-all platform bundle. Empty on a
    correctly-provisioned database; a non-empty result is an authority
    escalation the portable census intentionally does not enumerate."""
    rows = [row[0] for row in cur.execute(ESCALATION_SQL)]
    return {"count": len(rows), "digest": digest(rows), "rows": rows}
