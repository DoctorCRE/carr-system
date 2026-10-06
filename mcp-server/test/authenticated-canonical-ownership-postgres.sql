-- Executed only by the fenced disposable-PG acceptance gate. SET SESSION
-- AUTHORIZATION changes session_user, unlike SET ROLE, and therefore exercises
-- the production predicate without any test-only branch in deployed SQL.
do $catalog$
declare role_name text; signature text;
begin
  foreach role_name in array array['carr_writer','carr_jobs','carr_reader','carr_authority'] loop
    foreach signature in array array[
      'ops.mint_canonical_ownership_runtime_session(uuid,uuid,uuid,integer,text,text,timestamptz,uuid)',
      'ops.canonical_ownership_lifecycle(jsonb)',
      'ops.read_canonical_ownership_operation(uuid)',
      'ops.authenticated_canonical_ownership_controller_binding(uuid,uuid,text,text)',
      'ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer)',
      'ops.canonical_ownership_trusted_context()'] loop
      if has_function_privilege(role_name,signature,'execute') then
        raise exception 'ordinary role has ownership execution privilege: % %',role_name,signature;
      end if;
    end loop;
    if pg_has_role(role_name,'carr_ownership_issuer','member') then
      raise exception 'ordinary role inherits issuer membership: %',role_name;
    end if;
  end loop;
  if exists(select 1 from pg_roles where rolname in ('carr_ownership_issuer_g1','carr_ownership_issuer_g2')
       and (not rolcanlogin or rolinherit or rolbypassrls or rolsuper or rolcreaterole or rolcreatedb))
     or exists(select 1 from pg_roles where rolname='carr_ownership_issuer' and rolcanlogin) then
    raise exception 'issuer attribute boundary invalid';
  end if;
  if position('carr_ci' in pg_get_functiondef('ops.canonical_ownership_context()'::regprocedure))>0 then
    raise exception 'test-role bypass present in deployed ownership context';
  end if;
end $catalog$;

set session authorization carr_writer;
select set_config('carr.organization_tenant_id','carr-internal',true),
       set_config('carr.acting_actor_slug','codex',true),
       set_config('carr.receipt_session_ref','session:forged-writer',true),
       set_config('carr.ownership_session_id','ownership:00000000-0000-4000-8000-000000000001',true),
       set_config('carr.execution_host_id','cloudflare-workers:forged',true),
       set_config('carr.engineering_job_lease_token','00000000-0000-4000-8000-000000000001',true);
do $raw_writer$
begin
  begin
    perform ops.canonical_ownership_lifecycle('{}'::jsonb);
    raise exception 'forged writer unexpectedly executed lifecycle';
  exception when insufficient_privilege then null; end;
  begin
    execute 'set role carr_ownership_issuer';
    raise exception 'writer unexpectedly became issuer';
  exception when insufficient_privilege then null; end;
end $raw_writer$;
reset session authorization;

set session authorization carr_jobs;
do $raw_jobs$
begin
  begin
    perform ops.mint_canonical_ownership_runtime_session(null,null,null,1,
      'session:forged-jobs','cloudflare-workers:forged',clock_timestamp()+interval '1 minute',gen_random_uuid());
    raise exception 'forged jobs unexpectedly minted';
  exception when insufficient_privilege then null; end;
  begin
    execute 'set role carr_ownership_issuer_g1';
    raise exception 'jobs unexpectedly became issuer login';
  exception when insufficient_privilege then null; end;
end $raw_jobs$;
reset session authorization;

-- Even the test administrator's SET ROLE does not forge authenticated login.
set role carr_ownership_issuer;
do $set_role_is_not_login$
begin
  if ops.canonical_ownership_trusted_context()->>'reason_id' is distinct from
      'ownership_runtime_principal_untrusted' then
    raise exception 'SET ROLE forged session_user';
  end if;
end $set_role_is_not_login$;
reset role;

set session authorization carr_ownership_issuer_g2;
set role carr_ownership_issuer;
select set_config('carr.receipt_session_ref','session:draining-test',true),
       set_config('carr.execution_host_id','cloudflare-workers:fixture',true);
do $draining$
begin
  if ops.mint_canonical_ownership_runtime_session(null,null,null,1,
      'session:draining-test','cloudflare-workers:fixture',clock_timestamp()+interval '1 minute',gen_random_uuid())
      ->>'reason_id' is distinct from 'ownership_issuer_draining' then
    raise exception 'draining issuer minted new authority';
  end if;
end $draining$;
reset role;
reset session authorization;

set session authorization carr_ownership_issuer_g1;
set role carr_ownership_issuer;
do $raw_kernel$
begin
  begin
    perform ops.release_canonical_ownership_lease(gen_random_uuid(),gen_random_uuid(),1);
    raise exception 'issuer bypassed the closed lifecycle';
  exception when insufficient_privilege then null; end;
end $raw_kernel$;
reset role;
reset session authorization;
