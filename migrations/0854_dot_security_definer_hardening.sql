-- Dot's database review exposed legacy PUBLIC-executable definers and
-- search paths that let PostgreSQL implicitly search temporary relations
-- first. Preserve each trusted schema's order and every function body.
-- The migration runner owns the transaction.
-- rollback: forward-only — preserve the security boundary; repair trusted paths with a later migration.
do $pin_definer_paths$
declare routine record; path text; pinned text; changed integer := 0;
begin
  for routine in
    select p.oid,p.oid::regprocedure signature,p.proconfig
      from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where p.prosecdef and n.nspname in ('public','ops')
     order by n.nspname,p.proname,p.oid
  loop
    select substring(setting from 13) into path
      from unnest(routine.proconfig) setting where setting like 'search_path=%';
    -- Do not invent a path for an unreviewed routine or retain an untrusted
    -- schema. All current definers use only these repository-owned schemas.
    if path is null or path !~ '^(pg_catalog|public|ops|pg_temp)(,\s*(pg_catalog|public|ops|pg_temp))*$' then
      raise exception 'unreviewed SECURITY DEFINER search path for %: %',routine.signature,path;
    end if;
    select string_agg(quote_ident(schema_name), ', ' order by ordinal)
      into pinned
      from unnest(regexp_split_to_array(path,'\s*,\s*')) with ordinality schemas(schema_name,ordinal)
     where schema_name<>'pg_temp';
    pinned := concat_ws(', ',pinned,'pg_temp');
    if pinned<>path then
      execute format('alter function %s set search_path = %s',routine.signature,pinned);
      changed := changed+1;
    end if;
  end loop;
  raise notice 'pinned pg_temp last on % SECURITY DEFINER functions',changed;
end $pin_definer_paths$;

-- The two callable writers already reject session_user=dot_reader. Remove
-- their unnecessary ambient entry point as well; explicit carr_writer grants
-- remain. Trigger invocation does not require the invoker's EXECUTE grant,
-- so revoking direct calls on the two writing triggers preserves their use.
revoke execute on function ops.engineering_register_slice_plan(text,jsonb,text,uuid) from public,dot_reader;
revoke execute on function ops.issue_execution_envelope_v1(text,text,uuid) from public,dot_reader;
revoke execute on function ops.bind_execution_environment_to_assignment() from public,dot_reader;
revoke execute on function ops.fence_jobs_when_definition_disabled() from public,dot_reader;

do $dot_writer_boundary$
declare signature text;
begin
  foreach signature in array array[
    'ops.engineering_register_slice_plan(text,jsonb,text,uuid)',
    'ops.issue_execution_envelope_v1(text,text,uuid)',
    'ops.bind_execution_environment_to_assignment()',
    'ops.fence_jobs_when_definition_disabled()'
  ] loop
    if has_function_privilege('dot_reader',signature,'EXECUTE') then
      raise exception 'Dot still inherits EXECUTE on writing definer %',signature;
    end if;
  end loop;
  if not has_function_privilege('carr_writer','ops.engineering_register_slice_plan(text,jsonb,text,uuid)','EXECUTE')
     or not has_function_privilege('carr_writer','ops.issue_execution_envelope_v1(text,text,uuid)','EXECUTE') then
    raise exception 'definer hardening removed required writer access';
  end if;
end $dot_writer_boundary$;
