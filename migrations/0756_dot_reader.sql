-- Dedicated data-review login. Password remains NULL until the sanctioned
-- out-of-band credential step runs after release. No service membership.
-- public+ops are the business schemas (0119); neon_auth is provider-owned.
-- Read-all policies follow 0475, including partner personal-scope rows.
-- The migration runner owns the transaction.

do $dot_reader$
declare relation record; schema_name text;
begin
  if exists (select 1 from pg_roles where rolname = 'dot_reader') then
    raise exception 'dot_reader already exists; refusing to adopt an unknown login';
  end if;

  -- PostgreSQL has no per-role DENY. PUBLIC TEMP would let the new login
  -- create temporary tables. Remove that ambient DDL grant rather than add
  -- undeclared direct privileges to the repo's exact service-role contracts.
  -- Existing explicit TEMP grants and database-owner privileges are retained.
  execute format('revoke temporary on database %I from public', current_database());

  -- Remove legacy ambient schema DDL too; explicit creator grants remain.
  foreach schema_name in array array['public','ops'] loop
    if exists(select 1 from pg_namespace n,
      lateral aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
      where n.nspname=schema_name and a.grantee=0 and a.privilege_type='CREATE') then
      execute format('revoke create on schema %I from public',schema_name);
    end if;
  end loop;

  create role dot_reader login noinherit nosuperuser nocreatedb nocreaterole
    noreplication nobypassrls connection limit 2;
  -- PostgreSQL 16+ requires ADMIN OPTION to set another role's password.
  -- Retain only the migration administrator's administrative membership;
  -- it cannot inherit or SET ROLE into this login. No runtime service joins.
  if current_user <> 'neondb_owner' then
    grant dot_reader to neondb_owner with admin true, inherit false, set false;
    execute format('revoke dot_reader from %I',current_user);
  end if;
  alter role dot_reader set statement_timeout = '30s';
  alter role dot_reader set lock_timeout = '5s';
  execute format('grant connect on database %I to dot_reader', current_database());
  grant usage on schema public, ops to dot_reader;
  grant select on all tables in schema public, ops to dot_reader;
  grant select on all sequences in schema public, ops to dot_reader;
  -- As in 0119, this covers objects created by the migration owner. A new
  -- schema or a different creator requires its own corresponding grants.
  alter default privileges in schema public, ops grant select on tables to dot_reader;
  alter default privileges in schema public, ops grant select on sequences to dot_reader;

  for relation in
    select n.nspname,c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname in ('public','ops') and c.relkind in ('r','p') and c.relrowsecurity
  loop
    -- Table and sequence ACLs enforce read-only today. ALL lets a later
    -- table-DML plus sequence-mutation upgrade retain sponsor visibility.
    execute format('create policy dot_reader_full_read on %I.%I for all to dot_reader using (true) with check (true)',
                   relation.nspname,relation.relname);
  end loop;
end $dot_reader$;

-- These legacy SECURITY DEFINER write doors inherit PUBLIC EXECUTE. Refuse
-- this login before either executes any code. Retain their signatures, owner,
-- ACLs and existing service behavior, including the pinned SCAC catalog.
do $dot_function_boundary$
declare signature text; definition text;
begin
  foreach signature in array array['ops.engineering_register_slice_plan(text,jsonb,text,uuid)',
                                  'ops.issue_execution_envelope_v1(text,text,uuid)'] loop
    if to_regprocedure(signature) is null then continue; end if;
    definition := pg_get_functiondef(to_regprocedure(signature));
    if definition !~ E'\n[ \t]*[Bb][Ee][Gg][Ii][Nn][ \t]*\n' then
      raise exception 'Dot function boundary cannot locate outer BEGIN';
    end if;
    definition := regexp_replace(definition, E'\n[ \t]*[Bb][Ee][Gg][Ii][Nn][ \t]*\n',
      E'\nbegin\n  if session_user = ''dot_reader'' then\n'
      '    raise exception ''Dot login is read-only'' using errcode=''42501'';\n'
      '  end if;\n');
    execute definition;
  end loop;
  -- Future functions follow the repo's explicit EXECUTE-grant convention.
  -- The built-in PUBLIC EXECUTE default is global. A schema-scoped REVOKE
  -- cannot subtract it; future functions must explicitly grant their callers.
  alter default privileges revoke execute on functions from public;
end $dot_function_boundary$;

-- Completion's human/runtime callers remain tenant scoped. Dot reviews all
-- tenants, even with no tenant setting, just as it reads the underlying tables.
-- CASE prevents evaluating the required-tenant helper for this exact login.
do $dot_completion_views$
declare view_name text; definition text; adjusted text;
begin
  if to_regprocedure('ops.completion_runtime_tenant()') is null then
    raise exception 'Dot completion tenant helper is missing';
  end if;
  grant execute on function ops.completion_runtime_tenant() to dot_reader;
  foreach view_name in array array['completion_current_observation','completion_dimension_matrix'] loop
    definition := pg_get_viewdef(format('ops.%I',view_name)::regclass, true);
    adjusted := regexp_replace(definition,
      '([a-z_][a-z0-9_]*\.)?organization_tenant_id = ops\.completion_runtime_tenant\(\)',
      'CASE WHEN session_user = ''dot_reader'' THEN true ELSE \& END', 'g');
    if adjusted = definition then
      raise exception 'Dot completion view tenant predicate not found: %',view_name;
    end if;
    execute format('create or replace view ops.%I as %s',view_name,adjusted);
  end loop;
end $dot_completion_views$;

comment on role dot_reader is
  'Dedicated external data-quality review login. SELECT all public+ops tables/views, '
  'including every sponsor and personal-scope row. No service membership, ownership, '
  'write or DDL grants. Password set out-of-band after release.';

-- Effective privileges, including PUBLIC, must agree with the promised boundary.
do $dot_reader_proof$
begin
  if exists(select 1 from pg_auth_members where member='dot_reader'::regrole
    or (roleid='dot_reader'::regrole and (member<>'neondb_owner'::regrole
      or not admin_option or inherit_option or set_option)))
    or has_database_privilege('dot_reader',current_database(),'CREATE,TEMPORARY')
    or exists(select 1 from pg_namespace where has_schema_privilege('dot_reader',oid,'CREATE'))
    or exists(select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
      where n.nspname in ('public','ops') and c.relkind in ('r','p','v','m','f')
        and (not has_table_privilege('dot_reader',c.oid,'SELECT')
             or has_table_privilege('dot_reader',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN,SELECT WITH GRANT OPTION')
             or has_any_column_privilege('dot_reader',c.oid,'INSERT,UPDATE,REFERENCES,SELECT WITH GRANT OPTION')))
    or exists(select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
      where n.nspname in ('public','ops') and c.relkind='S'
        and case when c.relkind='S' then has_sequence_privilege('dot_reader',c.oid,'USAGE,UPDATE,SELECT WITH GRANT OPTION') else false end)
    or has_database_privilege('dot_reader',current_database(),'CONNECT WITH GRANT OPTION')
    or exists(select 1 from pg_namespace where has_schema_privilege('dot_reader',oid,'USAGE WITH GRANT OPTION'))
    or exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where n.nspname in ('public','ops') and has_function_privilege('dot_reader',p.oid,'EXECUTE WITH GRANT OPTION'))
  then
    raise exception 'dot_reader effective privilege boundary failed';
  end if;
end $dot_reader_proof$;
