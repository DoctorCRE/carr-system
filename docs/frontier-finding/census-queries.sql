-- census-queries.sql — the five Section-0 census row-sets, adapted to plain SQL.
-- WR-000046, F02 (gated-register-plan.md rev 12, Section 0 and Section 2 Artifacts B/C).
--
-- RECONCILED, 2026-09-02: two sibling build seats independently authored this
-- file (Artifact B, for gen-census-matrix.py; Artifact C, for
-- breakglass-run.py). This is the single reconciled version both consumers
-- parse. Full equivalence proof, per-category row counts, and the one
-- textual divergence found (and how it was resolved) live in
-- out/frontier-finding/build-receipts/reconcile-census/equivalence-report.md.
--
-- WHY PLAIN SQL: the frontier's own census logic lives inside `ops.*`
-- functions (ops.scac_mutation_catalog_v10_current(),
-- ops.scac_runtime_dml_grant_snapshot(), ops.scac_canonical_json for the
-- pinned digest algorithm) that do not exist on production pre-activation
-- (SIEP-11..18 are source-only; see the plan Section 0 and F02). Each query
-- below is the SAME pg_catalog logic, adapted to run standalone: no ops.*
-- calls, no reference-monitor digest wrapper.
--
-- Pinned source commit: origin/main 0985dcc70764d888d70004641e210f3730ef9d2a.
-- Provenance for categories 1-4: migrations/0471_source_merge_catalog_registry_successor.sql,
--   function ops.scac_mutation_catalog_v10_current(), the four `observed` CTEs
--   (lines 257-282 at the pinned commit).
-- Provenance for category 5: migrations/0467_siep18_atomic_db_monitor_grants.sql,
--   function ops.scac_runtime_dml_grant_snapshot() (lines 162-207 at the pinned
--   commit; spec range ~162-232 also spans the next function,
--   ops.scac_runtime_privilege_bundle, which is not part of the census). The
--   source function returns ONE aggregate row (entry_count, grant_digest);
--   this query is that same body's `rows` CTE (relation_rows UNION ALL
--   column_rows) UNWRAPPED to row form — one row per grant, not the aggregate.
--
-- The WHERE-clause / CTE logic of every block below is a verbatim, line-level
-- match to the cited migration text (verified by `git show` against the
-- pinned commit and cross-checked by executing both prior drafts against the
-- same disposable database — see the equivalence report). One prior draft
-- (Artifact C) had added `and grantee <> proowner` / `and grantee <>
-- relowner` filters to categories 1-3 that are NOT present in the pinned
-- migration text; that filter is dropped here to match the pinned source
-- exactly (it was empirically a no-op on the pinned db/schema.sql, which
-- issues no explicit self-grant to any object's owner, but it is not what
-- the pinned functions compute, so it does not belong in a query whose whole
-- purpose is to reproduce that pinned logic standalone).
--
-- OUTPUT SHAPE. Each block is one SELECT returning exactly two columns —
-- identity_key (text, unique within the block) and row (jsonb) — the same
-- contract breakglass-snapshot.sql documents and that breakglass-run.py's
-- load_snapshot_blocks()/capture_family_snapshot() require. This is Artifact
-- C's shape; Artifact B originally emitted a single `row::text` column for
-- gen-census-matrix.py's db-tap-based tap, and that generator required a
-- small patch (kept alongside this file's receipts as
-- gen-census-matrix.py.patch) to read the two-column, jsonb-typed shape
-- instead: db-tap.py's printer does not re-serialize a jsonb column back to
-- JSON text (psycopg decodes jsonb to a Python object by default, and
-- str(dict) is Python repr, not JSON), so the patched reader accepts either
-- valid JSON or that Python-repr text for the trailing `row` field.
--
-- FORMAT — deliberately dual-marked so BOTH consumers parse this same file
-- unmodified in its query text:
--   (1) gen-census-matrix.py splits on "-- CATEGORY: <name>" lines, each
--       header bounded above and below by a lone divider line of "-- " plus
--       sixty "=" characters (BLOCK_DIVIDER in that script).
--   (2) breakglass-run.py (and breakglass-snapshot.sql, which this file
--       matches block-for-block in format) scans for "-- @snapshot <name>"
--       / SELECT / "-- @end" triples anywhere in the file. The @snapshot/@end
--       pair sits INSIDE the divider-bounded region for each category, so
--       both parsers resolve to byte-identical executable SQL — proven by
--       running both extractions against the same disposable database (see
--       the equivalence report).
--
-- SHARED SCOPING PREDICATE (repeated per block, not factored into a view,
-- because the pinned source functions repeat it inline and this file stays a
-- direct, auditable transcription of that logic): the runtime role closure is
-- every role reachable by membership from a role matching '^carr_' excluding
-- carr_ci, stopping at superusers (categories 1, 2, 3, 5) or not stopping at
-- superusers (category 4's role/membership enumeration, matching the pinned
-- source's own connected CTE for that category exactly).
--
-- CLAIM DISCIPLINE (gated-register-plan.md Section T): this file and its two
-- consumers make no claim of mechanical completeness over any open universe —
-- not the five categories, not the objects any one category enumerates. It
-- reports what the queries below actually return against whatever database
-- they are pointed at.


-- ============================================================
-- CATEGORY: secdef_execute
-- PROVENANCE: migrations/0471_source_merge_catalog_registry_successor.sql
--   (pinned commit 0985dcc70764d888d70004641e210f3730ef9d2a), lines 257-263,
--   the first `observed` CTE of ops.scac_mutation_catalog_v10_current()
--   (db-function-acl / EXECUTE grants on SECURITY DEFINER functions to
--   connected runtime roles or PUBLIC).
-- ============================================================
-- @snapshot census_function_acl
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname <> 'carr_ci'
  union
  select other.oid from connected c
    join pg_auth_members m on m.roleid = c.oid or m.member = c.oid
    join pg_roles other on other.oid = case when m.roleid = c.oid then m.member else m.roleid end
  where other.rolname <> 'carr_ci' and not other.rolsuper
),
runtime_roles as (
  select r.oid, r.rolname
  from pg_roles r
  where r.oid in (select oid from connected) and not r.rolsuper
),
functions as (
  select p.oid, n.nspname, p.proname,
         pg_get_function_identity_arguments(p.oid) as args,
         p.prosecdef, p.prokind, p.provolatile, p.proparallel, p.proconfig, p.proacl, p.proowner
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname not in ('pg_catalog', 'information_schema') and p.prokind in ('f', 'p')
),
capabilities as (
  select f.*, acl.grantee, acl.privilege_type, acl.is_grantable
  from functions f
  cross join lateral aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) acl
),
observed as (
  select
    'db-function-acl:' || nspname || '.' || proname || '(' || args || '):' ||
      coalesce(r.rolname, 'public') || ':execute' as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-function-acl:' || nspname || '.' || proname || '(' || args || '):' ||
        coalesce(r.rolname, 'public') || ':execute',
      'ingress_kind', 'db_function_acl',
      'signature', nspname || '.' || proname || '(' || args || ')',
      'security_definer', prosecdef,
      'function_kind', prokind,
      'volatility', provolatile,
      'parallel', proparallel,
      'config', coalesce(to_jsonb(proconfig), '[]'::jsonb),
      'grantee', coalesce(r.rolname, 'public'),
      'privilege', 'execute',
      'grantable', is_grantable
    ) as row
  from capabilities c
  left join pg_roles r on r.oid = c.grantee
  where prosecdef and privilege_type = 'EXECUTE'
    and (grantee = 0 or r.oid in (select oid from runtime_roles))
)
select ingress_key as identity_key, row
from observed
order by ingress_key collate "C";
-- @end

-- ============================================================
-- CATEGORY: relation_dml
-- PROVENANCE: migrations/0471_source_merge_catalog_registry_successor.sql
--   (pinned commit 0985dcc70764d888d70004641e210f3730ef9d2a), lines 264-266,
--   the second `observed` CTE of ops.scac_mutation_catalog_v10_current()
--   (db-relation-acl / INSERT-UPDATE-DELETE-TRUNCATE grants on relations to
--   connected runtime roles or PUBLIC).
-- ============================================================
-- @snapshot census_relation_acl
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname <> 'carr_ci'
  union
  select other.oid from connected c
    join pg_auth_members m on m.roleid = c.oid or m.member = c.oid
    join pg_roles other on other.oid = case when m.roleid = c.oid then m.member else m.roleid end
  where other.rolname <> 'carr_ci' and not other.rolsuper
),
runtime_roles as (
  select r.oid, r.rolname
  from pg_roles r
  where r.oid in (select oid from connected) and not r.rolsuper
),
capabilities as (
  select n.nspname, c.relname, c.relkind, acl.grantee, acl.privilege_type, acl.is_grantable
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl
  where n.nspname not in ('pg_catalog', 'information_schema')
    and c.relkind in ('r', 'p', 'v', 'm', 'f')
),
observed as (
  select
    'db-relation-acl:' || nspname || '.' || relname || ':' ||
      coalesce(r.rolname, 'public') || ':' || lower(privilege_type) as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-relation-acl:' || nspname || '.' || relname || ':' ||
        coalesce(r.rolname, 'public') || ':' || lower(privilege_type),
      'ingress_kind', 'db_relation_acl',
      'relation', nspname || '.' || relname,
      'relation_kind', relkind,
      'grantee', coalesce(r.rolname, 'public'),
      'privilege', lower(privilege_type),
      'grantable', is_grantable
    ) as row
  from capabilities c
  left join pg_roles r on r.oid = c.grantee
  where privilege_type in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
    and (grantee = 0 or r.oid in (select oid from runtime_roles))
)
select ingress_key as identity_key, row
from observed
order by ingress_key collate "C";
-- @end

-- ============================================================
-- CATEGORY: column_dml
-- PROVENANCE: migrations/0471_source_merge_catalog_registry_successor.sql
--   (pinned commit 0985dcc70764d888d70004641e210f3730ef9d2a), lines 267-269,
--   the third `observed` CTE of ops.scac_mutation_catalog_v10_current()
--   (db-column-acl / INSERT-UPDATE grants on individual columns to connected
--   runtime roles or PUBLIC).
-- ============================================================
-- @snapshot census_column_acl
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname <> 'carr_ci'
  union
  select other.oid from connected c
    join pg_auth_members m on m.roleid = c.oid or m.member = c.oid
    join pg_roles other on other.oid = case when m.roleid = c.oid then m.member else m.roleid end
  where other.rolname <> 'carr_ci' and not other.rolsuper
),
runtime_roles as (
  select r.oid, r.rolname
  from pg_roles r
  where r.oid in (select oid from connected) and not r.rolsuper
),
capabilities as (
  select n.nspname, c.relname, c.relkind, a.attname, acl.grantee, acl.privilege_type, acl.is_grantable
  from pg_attribute a
  join pg_class c on c.oid = a.attrelid
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral aclexplode(a.attacl) acl
  where a.attnum > 0 and not a.attisdropped and a.attacl is not null
    and cardinality(a.attacl) > 0
    and n.nspname not in ('pg_catalog', 'information_schema')
    and c.relkind in ('r', 'p', 'v', 'm', 'f')
),
observed as (
  select
    'db-column-acl:' || nspname || '.' || relname || '.' || attname || ':' ||
      coalesce(r.rolname, 'public') || ':' || lower(privilege_type) as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-column-acl:' || nspname || '.' || relname || '.' || attname || ':' ||
        coalesce(r.rolname, 'public') || ':' || lower(privilege_type),
      'ingress_kind', 'db_column_acl',
      'relation', nspname || '.' || relname,
      'relation_kind', relkind,
      'column', attname,
      'grantee', coalesce(r.rolname, 'public'),
      'privilege', lower(privilege_type),
      'grantable', is_grantable
    ) as row
  from capabilities c
  left join pg_roles r on r.oid = c.grantee
  where privilege_type in ('INSERT', 'UPDATE')
    and (grantee = 0 or r.oid in (select oid from runtime_roles))
)
select ingress_key as identity_key, row
from observed
order by ingress_key collate "C";
-- @end

-- ============================================================
-- CATEGORY: role_authority
-- PROVENANCE: migrations/0471_source_merge_catalog_registry_successor.sql
--   (pinned commit 0985dcc70764d888d70004641e210f3730ef9d2a), lines 270-282,
--   the fourth `observed` CTE of ops.scac_mutation_catalog_v10_current()
--   (union of role rows, membership rows, and function/relation ownership
--   rows for connected roles, excluding neondb_owner and superusers).
-- ============================================================
-- @snapshot census_roles_membership_ownership
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname <> 'carr_ci'
  union
  select other.oid from connected c
    join pg_auth_members m on m.roleid = c.oid or m.member = c.oid
    join pg_roles other on other.oid = case when m.roleid = c.oid then m.member else m.roleid end
  where other.rolname <> 'carr_ci'
),
role_rows as (
  select
    'db-role:' || r.rolname as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-role:' || r.rolname,
      'row_kind', 'role',
      'role', r.rolname,
      'login', r.rolcanlogin,
      'inherit', r.rolinherit,
      'superuser', r.rolsuper,
      'create_role', r.rolcreaterole,
      'create_db', r.rolcreatedb,
      'replication', r.rolreplication,
      'bypass_rls', r.rolbypassrls
    ) as row
  from pg_roles r
  where r.oid in (select oid from connected)
),
membership_rows as (
  select
    'db-role-membership:' || role.rolname || ':' || member.rolname as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-role-membership:' || role.rolname || ':' || member.rolname,
      'row_kind', 'membership',
      'role', role.rolname,
      'member', member.rolname,
      'admin_option', m.admin_option,
      'inherit_option', m.inherit_option,
      'set_option', m.set_option
    ) as row
  from pg_auth_members m
  join pg_roles role on role.oid = m.roleid
  join pg_roles member on member.oid = m.member
  where m.roleid in (select oid from connected) and m.member in (select oid from connected)
),
ownership_rows as (
  select
    'db-function-owner:' || n.nspname || '.' || p.proname || '(' ||
      pg_get_function_identity_arguments(p.oid) || '):' || owner.rolname as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-function-owner:' || n.nspname || '.' || p.proname || '(' ||
        pg_get_function_identity_arguments(p.oid) || '):' || owner.rolname,
      'row_kind', 'function_owner',
      'signature', n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
      'owner', owner.rolname
    ) as row
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  join pg_roles owner on owner.oid = p.proowner
  where n.nspname not in ('pg_catalog', 'information_schema') and p.prokind in ('f', 'p')
    and owner.oid in (select oid from connected) and not owner.rolsuper and owner.rolname <> 'neondb_owner'
  union all
  select
    'db-relation-owner:' || n.nspname || '.' || c.relname || ':' || owner.rolname as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-relation-owner:' || n.nspname || '.' || c.relname || ':' || owner.rolname,
      'row_kind', 'relation_owner',
      'relation', n.nspname || '.' || c.relname,
      'relation_kind', c.relkind,
      'owner', owner.rolname
    ) as row
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  join pg_roles owner on owner.oid = c.relowner
  where n.nspname not in ('pg_catalog', 'information_schema') and c.relkind in ('r', 'p', 'v', 'm', 'f')
    and owner.oid in (select oid from connected) and not owner.rolsuper and owner.rolname <> 'neondb_owner'
),
observed as (
  select * from role_rows
  union all
  select * from membership_rows
  union all
  select * from ownership_rows
)
select ingress_key as identity_key, row
from observed
order by ingress_key collate "C";
-- @end

-- ============================================================
-- CATEGORY: runtime_dml_grants
-- PROVENANCE: migrations/0467_siep18_atomic_db_monitor_grants.sql
--   (pinned commit 0985dcc70764d888d70004641e210f3730ef9d2a), lines 162-207,
--   function ops.scac_runtime_dml_grant_snapshot() (spec range ~162-232 also
--   spans the next function, ops.scac_runtime_privilege_bundle, which is not
--   part of the census). The source function returns ONE aggregate row
--   (jsonb_build_object('schema_version',...,'entry_count',count(*),
--   'grant_digest',...)); this query is that same body's `rows` CTE
--   (relation_rows UNION ALL column_rows) UNWRAPPED to row form — one row per
--   grant, not the aggregate summary. Enters the versioned catalog shape at
--   v9 (Section 0 of the plan).
-- ============================================================
-- @snapshot census_runtime_dml_grants
with recursive connected(oid) as (
  select oid from pg_roles where rolname ~ '^carr_' and rolname <> 'carr_ci'
  union
  select other.oid from connected c
    join pg_auth_members m on m.roleid = c.oid or m.member = c.oid
    join pg_roles other on other.oid = case when m.roleid = c.oid then m.member else m.roleid end
  where other.rolname <> 'carr_ci' and not other.rolsuper
),
runtime_roles as (
  select r.oid, r.rolname
  from pg_roles r
  where r.oid in (select oid from connected) and not r.rolsuper
),
relation_rows as (
  select
    'db-relation-acl:' || n.nspname || '.' || c.relname || ':' ||
      coalesce(r.rolname, 'public') || ':' || lower(a.privilege_type) as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-relation-acl:' || n.nspname || '.' || c.relname || ':' ||
        coalesce(r.rolname, 'public') || ':' || lower(a.privilege_type),
      'relation', n.nspname || '.' || c.relname,
      'relation_kind', c.relkind,
      'grantee', coalesce(r.rolname, 'public'),
      'privilege', lower(a.privilege_type),
      'grantable', a.is_grantable
    ) as row
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
  left join pg_roles r on r.oid = a.grantee
  where n.nspname not in ('pg_catalog', 'information_schema')
    and a.privilege_type in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
    and (a.grantee = 0 or r.oid in (select oid from runtime_roles))
),
column_rows as (
  select
    'db-column-acl:' || n.nspname || '.' || c.relname || '.' || att.attname || ':' ||
      coalesce(r.rolname, 'public') || ':' || lower(a.privilege_type) as ingress_key,
    jsonb_build_object(
      'ingress_key', 'db-column-acl:' || n.nspname || '.' || c.relname || '.' || att.attname || ':' ||
        coalesce(r.rolname, 'public') || ':' || lower(a.privilege_type),
      'relation', n.nspname || '.' || c.relname,
      'relation_kind', c.relkind,
      'column', att.attname,
      'grantee', coalesce(r.rolname, 'public'),
      'privilege', lower(a.privilege_type),
      'grantable', a.is_grantable
    ) as row
  from pg_attribute att
  join pg_class c on c.oid = att.attrelid
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral aclexplode(att.attacl) a
  left join pg_roles r on r.oid = a.grantee
  where att.attnum > 0 and not att.attisdropped and att.attacl is not null
    and n.nspname not in ('pg_catalog', 'information_schema')
    and a.privilege_type in ('INSERT', 'UPDATE')
    and (a.grantee = 0 or r.oid in (select oid from runtime_roles))
),
observed as (
  select * from relation_rows
  union all
  select * from column_rows
)
select ingress_key as identity_key, row
from observed
order by ingress_key collate "C";
-- @end
