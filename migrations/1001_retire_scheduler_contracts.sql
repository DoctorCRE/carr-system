-- rollback: forward-only; restore retired scheduler objects only with a new migration.
-- expand-contract: contract; registered product verbs and native schedulers no longer call these doors.
-- lock-review: ACCESS EXCLUSIVE on eight retired tables and brief release constraint validation;
-- lock acquisition is bounded to five seconds. Historical receipts and shared jobs remain.
SET LOCAL lock_timeout = '5s';
DO $retire_scheduler_grants$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  execute_keys text[] := ARRAY['db-function-acl:ops.approve_staging_release(p_release_key text, p_plan_hash text, p_idempotency_key uuid, p_expires_hours integer, p_verifier_actor text, p_verifier_evidence_ref text):carr_authority:execute',
    'db-function-acl:ops.disable_legacy_schedule(p_workflow_key text, p_surface_id text, p_locator text, p_reason text, p_pre_observation_ref text, p_post_observation_ref text, p_sibling_surface_id text, p_sibling_locator text, p_sibling_pre_observation_ref text, p_sibling_post_observation_ref text, p_idempotency_key text):carr_authority:execute',
    'db-function-acl:ops.get_cognition_cache(p_cache_key text):carr_jobs:execute',
    'db-function-acl:ops.invalidate_cognition_cache(p_dependency_ref text):carr_jobs:execute',
    'db-function-acl:ops.invalidate_cognition_cache(p_dependency_ref text):carr_writer:execute',
    'db-function-acl:ops.put_cognition_cache(p_cache_key text, p_cognition_key text, p_cognition_version integer, p_output_schema_version integer, p_proposal jsonb, p_dependency_refs text[], p_ttl_seconds integer):carr_jobs:execute',
    'db-function-acl:ops.record_claude_scheduler_observation(p_surface_id text, p_provider_task_id text, p_cron_expression text, p_timezone text, p_enabled boolean, p_definition_sha256 text, p_provider_revision text, p_source_fingerprint text, p_observed_at timestamp with time zone, p_idempotency_key text):carr_device_evidence:execute',
    'db-function-acl:ops.record_launchd_scheduler_observation(p_surface_id text, p_label text, p_timezone text, p_enabled boolean, p_plist_sha256 text, p_schedule_sha256 text, p_launchctl_revision text, p_source_fingerprint text, p_observed_at timestamp with time zone, p_idempotency_key text):carr_device_evidence:execute',
    'db-function-acl:ops.record_provider_observation(p_route_key text, p_status text, p_latency_ms integer, p_error_class text, p_ttl_seconds integer, p_source_ref text):carr_jobs:execute',
    'db-function-acl:ops.release_job_cost(p_reservation_id uuid, p_job_id uuid, p_lease_token uuid):carr_jobs:execute',
    'db-function-acl:ops.reserve_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric):carr_jobs:execute',
    'db-function-acl:ops.retire_workflow_cutover_plan(p_plan_id uuid, p_disable_receipt_id uuid, p_reason text, p_idempotency_key uuid):carr_authority:execute',
    'db-function-acl:ops.select_provider_routes(p_requested text[]):carr_jobs:execute',
    'db-function-acl:ops.settle_job_cost(p_reservation_id uuid, p_job_id uuid, p_lease_token uuid, p_input_tokens integer, p_output_tokens integer, p_actual_cost_usd numeric):carr_jobs:execute'];
  relation_keys text[] := ARRAY['db-relation-acl:ops.cognition_job:carr_writer:insert',
    'db-relation-acl:ops.cognition_job:carr_writer:update',
    'db-relation-acl:ops.cognition_result_cache:carr_writer:insert',
    'db-relation-acl:ops.cognition_result_cache:carr_writer:update',
    'db-relation-acl:ops.provider_route:carr_writer:insert',
    'db-relation-acl:ops.provider_route:carr_writer:update',
    'db-relation-acl:ops.provider_observation:carr_jobs:insert'];
  retired_execute jsonb;
  retired_relations jsonb;
  next_contract jsonb;
  relation_name text;
  row_count bigint;
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT jsonb_agg(contract - 'effect_class' - 'owner_package' - 'implementation_state'
                           - 'classification_authorizing' - 'source_locator' ORDER BY ingress_key)
    INTO retired_execute FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version' AND ingress_key = ANY(execute_keys);
  SELECT jsonb_agg(contract - 'effect_class' - 'owner_package' - 'implementation_state'
                           - 'classification_authorizing' - 'source_locator' ORDER BY ingress_key)
    INTO retired_relations FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version' AND ingress_key = ANY(relation_keys);
  IF coalesce(jsonb_array_length(retired_execute),0) <> cardinality(execute_keys)
     OR coalesce(jsonb_array_length(retired_relations),0) <> cardinality(relation_keys) THEN
    RAISE EXCEPTION 'Expected scheduler capabilities absent from the sealed registry';
  END IF;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = ANY(execute_keys))
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(original->'retired_relation_dml','[]'::jsonb)) item
      WHERE item->>'ingress_key' = ANY(relation_keys)) THEN
    RAISE EXCEPTION 'A scheduler capability is already retired';
  END IF;
  FOREACH relation_name IN ARRAY ARRAY['ops.cost_reservation','ops.provider_observation',
      'ops.cognition_result_cache','ops.provider_route','ops.cognition_job'] LOOP
    EXECUTE format('SELECT count(*) FROM %s', relation_name::regclass) INTO row_count;
    RAISE NOTICE 'scheduler retirement % rows=%', relation_name, row_count;
  END LOOP;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb) || retired_execute);
  next_contract := jsonb_set(next_contract, '{retired_relation_dml}',
    coalesce(original->'retired_relation_dml','[]'::jsonb) || retired_relations);
  EXECUTE format('CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_scheduler_grants$;

CREATE OR REPLACE FUNCTION ops.scac_mutation_catalog_v118_current() RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'ops', 'pg_temp'
    AS $$
declare observed_count integer; observed_digest text; registered_count integer;
  current_grants jsonb := ops.scac_current_grant_contract();
begin
  if not exists (
    select 1 from ops.scac_mutation_registry_version
     where registry_version=current_grants->>'registry_version'
       and registry_digest=current_grants->>'registry_digest'
  ) then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  live_observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles))),
  observed as (select * from live_observed union all
    select value->>'ingress_key',value from jsonb_array_elements(coalesce(current_grants->'retired_secdef_execute','[]'::jsonb)))
  select count(*) filter (where row not in (select value from jsonb_array_elements(current_grants->'secdef_execute'))),
    'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(
      jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C")
        filter (where row not in (select value from jsonb_array_elements(current_grants->'secdef_execute'))),
      '[]'::jsonb)),'UTF8'),'sha256'),'hex'),
    count(*) filter (where row in (select value from jsonb_array_elements(current_grants->'secdef_execute')))
    into observed_count,observed_digest,registered_count from observed;
  if registered_count<>jsonb_array_length(current_grants->'secdef_execute') then return false; end if;
  if observed_count<>1267 or observed_digest<>'sha256:587e762e80c6691d4a07c5b35e48a259c7b8bdf885d8595ffd0c14928c657efa' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles))),
  accounted as (select * from observed union all
    select value->>'ingress_key',value from jsonb_array_elements(coalesce(current_grants->'retired_relation_dml','[]'::jsonb)))
  select count(*) filter (where row not in (select value from jsonb_array_elements(current_grants->'relation_dml'))),
    'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(
      jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C")
        filter (where row not in (select value from jsonb_array_elements(current_grants->'relation_dml'))),
      '[]'::jsonb)),'UTF8'),'sha256'),'hex'),
    count(*) filter (where row in (select value from jsonb_array_elements(current_grants->'relation_dml')))
    into observed_count,observed_digest,registered_count from accounted;
  if registered_count<>jsonb_array_length(current_grants->'relation_dml') then return false; end if;
  if observed_count<>321 or observed_digest<>'sha256:2ece3984f1389d78749dd3055ecf72516f02f97e33d260192e66daff062292e0' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(a.attacl) acl where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0 and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>15 or observed_digest<>'sha256:f7a33d14ce431ba588cc07dff07fc9fd91a0dcdb41112de6fecd390fb42dc098' then return false; end if;
  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper union
    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname~'^carr_' and other.rolname<>'carr_ci' and not other.rolcanlogin and not other.rolsuper
  ), role_rows as (
    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)
  ), membership_rows as (
    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)
  ), ownership_rows as (
    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all
    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if exists (select 1 from pg_auth_members m join pg_roles g on g.oid=m.roleid join pg_roles mem on mem.oid=m.member where mem.rolname~'^carr_' and (g.rolsuper or g.rolname~'^(neon_|pg_)')) then return false; end if;
  return observed_count=13 and observed_digest='sha256:93724fe71ed216afac9b4bf48eee500e693de057cae0b59f59ff8e68cc6a2bc0';
end $$;

DROP FUNCTION ops.reserve_job_cost(uuid,uuid,text,numeric) RESTRICT;
DROP FUNCTION ops.settle_job_cost(uuid,uuid,uuid,integer,integer,numeric) RESTRICT;
DROP FUNCTION ops.release_job_cost(uuid,uuid,uuid) RESTRICT;
DROP FUNCTION ops.get_cognition_cache(text) RESTRICT;
DROP FUNCTION ops.put_cognition_cache(text,text,integer,integer,jsonb,text[],integer) RESTRICT;
DROP FUNCTION ops.invalidate_cognition_cache(text) RESTRICT;
DROP FUNCTION ops.select_provider_routes(text[]) RESTRICT;
DROP FUNCTION ops.record_provider_observation(text,text,integer,text,integer,text) RESTRICT;
DROP VIEW ops.v_provider_route RESTRICT;
DROP TABLE ops.cost_reservation RESTRICT;
DROP TABLE ops.provider_observation RESTRICT;
DROP TABLE ops.cognition_result_cache RESTRICT;
DROP TABLE ops.provider_route RESTRICT;
DROP TABLE ops.cognition_job RESTRICT;

create or replace function ops.enqueue_job(
  p_definition_key text,
  p_definition_version integer,
  p_scheduled_for timestamptz,
  p_payload jsonb,
  p_idempotency_key text,
  p_mode text default 'live'
) returns ops.job
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  d ops.job_definition%rowtype;
  j ops.job%rowtype;
  canary_disabled boolean;

begin
  select * into d from ops.job_definition
   where key=p_definition_key and version=p_definition_version and enabled;
  if not found then
    raise exception 'job definition % v% is not enabled',p_definition_key,p_definition_version;
  end if;
  if p_mode not in ('shadow','canary','live','replay') then
    raise exception 'invalid job mode %',p_mode;
  end if;
  -- A missing canary key (every cognition contract, and any deterministic
  -- contract that never named one) is not the same claim as an explicit
  -- canary.enabled=false; only the explicit false is a contractual refusal.
  canary_disabled := (d.execution_contract #>> '{canary,enabled}') = 'false';
  if p_mode='canary' then
    if canary_disabled then
      raise exception 'workflow % cannot enqueue canary mode: canary is contractually disabled for definition v%',
        p_definition_key,p_definition_version;
    end if;
    if not exists (
      select 1 from ops.workflow_acceptance
       where workflow_key=p_definition_key and workflow_version=p_definition_version
         and mode='shadow' and status='accepted'
    ) then
      raise exception 'workflow % cannot enqueue canary mode: no accepted shadow acceptance evidence for definition v%',
        p_definition_key,p_definition_version;
    end if;
  elsif p_mode='live' then
    if canary_disabled then
      if not exists (
        select 1 from ops.workflow_acceptance
         where workflow_key=p_definition_key and workflow_version=p_definition_version
           and mode='shadow' and status='accepted'
      ) then
        raise exception 'workflow % cannot enqueue live mode: canary is contractually disabled and no accepted shadow acceptance evidence exists for definition v%',
          p_definition_key,p_definition_version;
      end if;
    elsif not exists (
      select 1 from ops.workflow_acceptance
       where workflow_key=p_definition_key and workflow_version=p_definition_version
         and mode='canary' and status='accepted'
    ) then
      raise exception 'workflow % cannot enqueue live mode: no accepted canary acceptance evidence for definition v%',
        p_definition_key,p_definition_version;
    end if;
  end if;

  insert into ops.job
    (definition_key,definition_version,idempotency_key,scheduled_for,mode,payload,
     max_attempts,timeout_seconds)
  values
    (d.key,d.version,p_idempotency_key,p_scheduled_for,p_mode,coalesce(p_payload,'{}'::jsonb),
     (d.retry_policy->>'max_attempts')::integer,
     (d.retry_policy->>'timeout_seconds')::integer)
  on conflict do nothing
  returning * into j;
  if j.id is null then
    select * into j from ops.job
     where idempotency_key=p_idempotency_key
        or (definition_key=p_definition_key
            and definition_version=p_definition_version
            and scheduled_for=p_scheduled_for)
     order by (idempotency_key=p_idempotency_key) desc
     limit 1;
    if j.id is null
       or j.definition_key <> p_definition_key
       or j.definition_version <> p_definition_version
       or j.scheduled_for <> p_scheduled_for
       or j.payload <> coalesce(p_payload,'{}'::jsonb)
       or j.mode <> p_mode then
      raise exception 'duplicate delivery conflicts with the canonical scheduled job';
    end if;
  end if;
  return j;
end $$;

alter table ops.release drop constraint an_approved_release_names_its_approval;
alter table ops.release add constraint an_approved_release_names_its_approval check
  (state in ('draft','candidate','abandoned')
   or (plan_hash is not null and
       (environment='staging'
        or (readiness_receipt_id is not null and ready_at is not null
            and approved_by_actor is null and approved_at is null and approval_expires_at is null)
        or (approved_by_actor is not null and approved_at is not null
            and approval_expires_at is not null))));

drop function ops.disable_legacy_schedule(text,text,text,text,text,text,text,text,text,text,text) restrict;
drop function ops.record_claude_scheduler_observation(text,text,text,text,boolean,text,text,text,timestamptz,text) restrict;
drop function ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamptz,text) restrict;
drop function ops.retire_workflow_cutover_plan(uuid,uuid,text,uuid) restrict;
drop trigger job_definition_cutover_requires_evidence on ops.job_definition;
drop function ops.require_cutover_evidence() restrict;
drop table ops.legacy_schedule_provider_contract restrict;
drop table ops.legacy_schedule_launchd_contract restrict;
drop table ops.legacy_schedule_surface_registry restrict;

drop function ops.approve_staging_release(text,text,uuid,integer,text,text) restrict;
drop trigger staging_release_approval_is_typed on ops.release;
drop trigger a_staging_release_plan_revision_invalidates_approval on ops.release;
drop function ops.staging_release_approval_is_typed() restrict;
drop function ops.staging_release_plan_revision_invalidates_approval() restrict;


comment on function ops.enqueue_job(text,integer,timestamptz,jsonb,text,text) is
  'Admit jobs for enabled definitions with mode acceptance and canonical delivery idempotency.';

create or replace function ops.release_assurance_is_immutable()
returns trigger language plpgsql as $$
begin
  if old.environment='staging'
     and old.state in ('approved','deploying','verifying','complete')
     and new.environment is distinct from old.environment then
    raise exception 'promoted staging release environment is immutable';
  end if;
  if old.state in ('ready','approved','deploying','verifying','complete')
     and new.state<>'candidate'
     and new.plan_hash is not distinct from old.plan_hash
     and (new.performance_budget_ref,new.performance_budget_ms,
          new.recovery_strategy,new.rollback_ready,new.rollback_plan_ref,
          new.service_id,new.environment,new.git_sha,new.artifact_digest,
          new.dependency_lock_digest,new.config_fingerprint,
          new.schema_highest_migration,new.schema_applied_count,
          new.schema_ledger_sha256,new.migration_set) is distinct from
         (old.performance_budget_ref,old.performance_budget_ms,
          old.recovery_strategy,old.rollback_ready,old.rollback_plan_ref,
          old.service_id,old.environment,old.git_sha,old.artifact_digest,
          old.dependency_lock_digest,old.config_fingerprint,
          old.schema_highest_migration,old.schema_applied_count,
          old.schema_ledger_sha256,old.migration_set) then
    raise exception 'promoted release material is immutable';
  end if;
  return new;
end $$;

DO $verify_scheduler_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Scheduler retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_scheduler_retirement$;
