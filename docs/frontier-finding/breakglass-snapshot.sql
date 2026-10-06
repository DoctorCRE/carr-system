-- breakglass-snapshot.sql — Artifact C snapshot queries (WR-000046, gated-register-plan.md rev 12,
-- Section 2 "Artifact C"). Read by docs/frontier-finding/breakglass-run.py.
--
-- CLAIM DISCIPLINE (Section T of the plan): this list is a REVIEWED, EXTENDABLE
-- surface, not a claim of mechanical completeness over every PostgreSQL catalog
-- family. An effect on THIS surface that was not declared aborts the run; an
-- effect outside it is the residual the plan's human/procedural controls carry.
-- Extend this file freely as gaps are found; do not read its presence as proof
-- nothing is missing.
--
-- FORMAT. Each catalog family is one named block:
--   -- @snapshot <name>
--   <one SELECT statement, terminated by a lone ";" at end-of-block>
--   -- @end
-- The SELECT must return exactly two columns: identity_key (text, unique
-- within the block) and row (jsonb, the family's row content for that key).
-- docs/frontier-finding/breakglass-run.py parses this file by the markers,
-- not by generic SQL splitting, so a family's SELECT may itself contain
-- semicolons only inside string/dollar-quoted literals (none do below).
--
-- SCOPE. Every family below is restricted to non-system namespaces — schemas
-- other than pg_catalog, information_schema, pg_toast, and pg_temp* — because
-- the built-in catalog itself does not change under a candidate migration and
-- including it would make every diff dominated by noise. pg_roles and
-- pg_auth_members are global (no namespace) and are captured in full; pg_roles
-- is the VIEW, which already masks rolpassword, so no credential material is
-- ever captured here.
--
-- VOLATILE-STATISTICS EXCLUSION (named, per the plan; extendable — Section T).
-- pg_class carries columns that change on their own schedule, or as a side
-- effect of an otherwise-benign operation, and carry no schema meaning:
-- reltuples, relpages, relallvisible (autovacuum/ANALYZE), and relfilenode
-- (the physical storage pointer, which PostgreSQL rewrites — silently
-- changing this column — on a sequence value/increment change via
-- nextval/setval/ALTER SEQUENCE, not just on VACUUM FULL/CLUSTER/TRUNCATE;
-- observed directly building this suite's alter_sequence_config_change_reported
-- test, whose ALTER SEQUENCE ... INCREMENT BY changed nothing else in this
-- row). Sequence VALUE state is deliberately reported, never gated (see
-- pg_sequence below); a bare storage-pointer bump on the sequence's own
-- pg_class row must not silently gate what pg_sequence deliberately does not.
-- All four are stripped from the captured row.
--
-- pg_sequence's last_value/is_called are NOT columns of the pg_sequence
-- catalog (that catalog holds only the fixed configuration: start/increment/
-- min/max/cache/cycle/type). The current value lives in the sequence object
-- itself and must be read per-sequence with SELECT last_value, is_called FROM
-- <sequence>. docs/frontier-finding/breakglass-run.py runs the @snapshot
-- pg_sequence block below for the configuration row, then supplements each
-- row in Python with a per-sequence last_value/is_called read, because no
-- single catalog-wide SELECT exposes that pair for every sequence at once.
--
-- Similarly, "per-table row digests over all user-schema tables" is not a
-- fixed-shape SELECT (the table list is itself catalog state) — the driver
-- enumerates pg_class relkind='r' rows under the same namespace filter used
-- here and computes one digest per table dynamically. This file does not
-- special-case it.

-- ── pg_proc ──────────────────────────────────────────────────────────────
-- to_jsonb(p) includes prosrc (the function/procedure body for SQL/PL
-- languages), so a body-only edit that leaves the signature untouched is
-- still visible here, not just in pg_rewrite/view bodies.
-- @snapshot pg_proc
select n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' as identity_key,
       to_jsonb(p) as row
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_class (incl. relrowsecurity, relforcerowsecurity, reloptions) ──────
-- @snapshot pg_class
select n.nspname || '.' || c.relname as identity_key,
       (to_jsonb(c) - 'reltuples' - 'relpages' - 'relallvisible' - 'relfilenode') as row
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_attribute ─────────────────────────────────────────────────────────
-- @snapshot pg_attribute
select n.nspname || '.' || c.relname || '.' || a.attname as identity_key,
       to_jsonb(a) as row
from pg_attribute a
join pg_class c on c.oid = a.attrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\'
  and a.attnum > 0;
-- @end

-- ── pg_attrdef ───────────────────────────────────────────────────────────
-- @snapshot pg_attrdef
select n.nspname || '.' || c.relname || '.' || a.attname as identity_key,
       (to_jsonb(d) || jsonb_build_object('def_expr', pg_get_expr(d.adbin, d.adrelid))) as row
from pg_attrdef d
join pg_class c on c.oid = d.adrelid
join pg_namespace n on n.oid = c.relnamespace
join pg_attribute a on a.attrelid = d.adrelid and a.attnum = d.adnum
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_constraint ────────────────────────────────────────────────────────
-- @snapshot pg_constraint
select n.nspname || '.' || coalesce(rel.relname, dom.typname) || '.' || con.conname as identity_key,
       (to_jsonb(con) || jsonb_build_object('def', pg_get_constraintdef(con.oid))) as row
from pg_constraint con
join pg_namespace n on n.oid = con.connamespace
left join pg_class rel on rel.oid = con.conrelid
left join pg_type dom on dom.oid = con.contypid
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_trigger (incl. tgenabled) ────────────────────────────────────────
-- Internal (constraint-backed, e.g. FK RI) triggers are excluded: they are
-- 1:1 with a pg_constraint row already captured above, and including them
-- doubles every foreign key as trigger noise. An ordinary user trigger's
-- disable is fully visible here via tgenabled.
-- @snapshot pg_trigger
select n.nspname || '.' || c.relname || '.' || t.tgname as identity_key,
       (to_jsonb(t) || jsonb_build_object('def', pg_get_triggerdef(t.oid))) as row
from pg_trigger t
join pg_class c on c.oid = t.tgrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\'
  and not t.tgisinternal;
-- @end

-- ── pg_policy ────────────────────────────────────────────────────────────
-- @snapshot pg_policy
select n.nspname || '.' || c.relname || '.' || p.polname as identity_key,
       to_jsonb(p) as row
from pg_policy p
join pg_class c on c.oid = p.polrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_rewrite ───────────────────────────────────────────────────────────
-- A view's body lives here (the "_RETURN" rule); pg_get_ruledef(oid) yields
-- the exact executable CREATE [OR REPLACE] RULE text, which is also what
-- restoration replays for a view-body target.
-- @snapshot pg_rewrite
select n.nspname || '.' || c.relname || '.' || r.rulename as identity_key,
       (to_jsonb(r) || jsonb_build_object('def', pg_get_ruledef(r.oid))) as row
from pg_rewrite r
join pg_class c on c.oid = r.ev_class
join pg_namespace n on n.oid = c.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_default_acl ───────────────────────────────────────────────────────
-- @snapshot pg_default_acl
select coalesce(n.nspname, '(database-wide)') || '.' || da.defaclrole::regrole::text || '.' || da.defaclobjtype::text as identity_key,
       to_jsonb(da) as row
from pg_default_acl da
left join pg_namespace n on n.oid = da.defaclnamespace;
-- @end

-- ── pg_namespace ─────────────────────────────────────────────────────────
-- @snapshot pg_namespace
select n.nspname as identity_key, to_jsonb(n) as row
from pg_namespace n
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_roles ─────────────────────────────────────────────────────────────
-- The VIEW, not pg_authid — rolpassword is masked to '********' by Postgres
-- itself, so this can never carry a credential.
-- @snapshot pg_roles
select r.rolname as identity_key, to_jsonb(r) as row
from pg_roles r;
-- @end

-- ── pg_auth_members ──────────────────────────────────────────────────────
-- @snapshot pg_auth_members
select role.rolname || ':' || member.rolname || ':' || coalesce(grantor.rolname, '') as identity_key,
       to_jsonb(m) as row
from pg_auth_members m
join pg_roles role on role.oid = m.roleid
join pg_roles member on member.oid = m.member
left join pg_roles grantor on grantor.oid = m.grantor;
-- @end

-- ── pg_extension ─────────────────────────────────────────────────────────
-- @snapshot pg_extension
select e.extname as identity_key, to_jsonb(e) as row
from pg_extension e;
-- @end

-- ── pg_index (with pg_get_indexdef) ─────────────────────────────────────
-- @snapshot pg_index
select n.nspname || '.' || ic.relname as identity_key,
       (to_jsonb(i) || jsonb_build_object('indexdef', pg_get_indexdef(i.indexrelid))) as row
from pg_index i
join pg_class ic on ic.oid = i.indexrelid
join pg_namespace n on n.oid = ic.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_sequence (full configuration row; last_value/is_called added by the driver) ──
-- @snapshot pg_sequence
select n.nspname || '.' || c.relname as identity_key, to_jsonb(s) as row
from pg_sequence s
join pg_class c on c.oid = s.seqrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_type ──────────────────────────────────────────────────────────────
-- @snapshot pg_type
select n.nspname || '.' || t.typname as identity_key, to_jsonb(t) as row
from pg_type t
join pg_namespace n on n.oid = t.typnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_enum ──────────────────────────────────────────────────────────────
-- An added enum label is a NEW row (enum labels never share an oid across
-- values), so an undeclared addition surfaces as an undeclared new key in
-- this block's diff — no special-case detection needed.
-- @snapshot pg_enum
select n.nspname || '.' || t.typname || '.' || e.enumlabel as identity_key,
       to_jsonb(e) as row
from pg_enum e
join pg_type t on t.oid = e.enumtypid
join pg_namespace n on n.oid = t.typnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end

-- ── pg_partitioned_table ─────────────────────────────────────────────────
-- @snapshot pg_partitioned_table
select n.nspname || '.' || c.relname as identity_key, to_jsonb(pt) as row
from pg_partitioned_table pt
join pg_class c on c.oid = pt.partrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname not in ('pg_catalog', 'information_schema')
  and n.nspname not like 'pg\_temp%' escape '\'
  and n.nspname not like 'pg\_toast%' escape '\';
-- @end
