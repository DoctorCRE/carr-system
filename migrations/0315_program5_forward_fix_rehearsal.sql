-- Program 5 forward-fix recovery rehearsal.
--
-- A migration-bearing release cannot truthfully rehearse a rollback.  This
-- creates a separate, staging-only forward-fix path: it proves the exact
-- candidate was applied through its declared forward migration boundary and
-- read back, but never claims that Production was rolled back or that a
-- defect was manufactured.  Rollback evidence remains structurally unchanged.

begin;

-- The public /release reader already uses this view rather than the base
-- ledger.  Expose the stored migration digest through that same least-privilege
-- surface so staging can prove the full forward boundary, not merely its max.
create or replace view public.v_schema_ledger as
  select filename, applied_at, sha256 from public.schema_migrations;

-- Capability bundle only. The named LOGIN verifier is deliberately provisioned
-- out of band after this migration; a rebuilt schema must never mint a secret.
do $$ begin
  if not exists (select 1 from pg_roles where rolname='carr_program5_forward_fix_verifiers') then
    create role carr_program5_forward_fix_verifiers nologin;
  elsif exists (select 1 from pg_roles where rolname='carr_program5_forward_fix_verifiers' and rolcanlogin) then
    raise exception '0315 FAILED: forward-fix verifier capability must be NOLOGIN';
  end if;
end $$;
grant usage on schema ops,public to carr_program5_forward_fix_verifiers;

create table ops.staging_forward_fix_rehearsal_attempt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  correlation_id uuid not null unique,
  release_id uuid not null references ops.release(id) on delete restrict,
  service_id uuid not null references ops.service(id) on delete restrict,
  environment text not null check (environment='staging'),
  git_sha text not null check (git_sha ~ '^[0-9a-f]{40}$'),
  provider text not null check (provider='cloudflare-workers'),
  candidate_provider_version_id uuid not null,
  recovery_strategy text not null check (recovery_strategy='forward_fix'),
  recovery_plan_ref text not null check (btrim(recovery_plan_ref)<>''),
  plan_hash text not null check (btrim(plan_hash)<>''),
  expected_provider_tag text not null unique
    check (expected_provider_tag ~ '^carr-staging-forward-fix-[0-9a-f]{32}$'),
  declared_migration_set_sha256 text not null
    check (declared_migration_set_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  declared_migration_count integer not null check (declared_migration_count>0),
  declared_schema_highest_migration text not null
    check (declared_schema_highest_migration ~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$'),
  declared_schema_applied_count integer not null check (declared_schema_applied_count>0),
  declared_schema_ledger_sha256 text not null
    check (declared_schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  writer_session_user text not null default session_user
    check (writer_session_user='carr_jobs'),
  created_at timestamptz not null default clock_timestamp()
);

create table ops.staging_forward_fix_rehearsal_claim (
  rehearsal_attempt_id uuid primary key
    references ops.staging_forward_fix_rehearsal_attempt(id) on delete restrict,
  writer_session_user text not null default session_user
    check (writer_session_user='carr_jobs'),
  claimed_at timestamptz not null default clock_timestamp()
);

create table ops.staging_forward_fix_rehearsal_result (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  rehearsal_attempt_id uuid not null unique
    references ops.staging_forward_fix_rehearsal_attempt(id) on delete restrict,
  provider_version_id uuid not null,
  provider_tag text not null,
  verb_count integer not null check (verb_count>0),
  schema_highest_migration text not null,
  schema_applied_count integer not null check (schema_applied_count>0),
  schema_ledger_sha256 text not null check (schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  doctrine_generation bigint not null check (doctrine_generation>=0),
  program6_actions_enabled boolean not null,
  projection_sha256 text not null unique check (projection_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref text not null unique
    check (evidence_ref ~ '^ops\.staging-forward-fix-readback:sha256:[0-9a-f]{64}$'),
  observed_at timestamptz not null default clock_timestamp(),
  writer_session_user text not null default session_user
    check (writer_session_user='carr_program5_forward_fix_verifier')
);

create trigger staging_forward_fix_rehearsal_attempt_append_only
before update or delete on ops.staging_forward_fix_rehearsal_attempt
for each row execute function ops.refuse_program5_evidence_mutation();
create trigger staging_forward_fix_rehearsal_claim_append_only
before update or delete on ops.staging_forward_fix_rehearsal_claim
for each row execute function ops.refuse_program5_evidence_mutation();
create trigger staging_forward_fix_rehearsal_result_append_only
before update or delete on ops.staging_forward_fix_rehearsal_result
for each row execute function ops.refuse_program5_evidence_mutation();

-- One existing bundle ledger remains the approval/completion pointer, but its
-- rows now have mutually-exclusive rollback and forward-fix shapes.
alter table ops.staging_recovery_rehearsal_bundle
  add column forward_fix_result_id uuid
    references ops.staging_forward_fix_rehearsal_result(id) on delete restrict,
  add column candidate_git_sha text,
  add column candidate_provider_version_id uuid;
alter table ops.staging_recovery_rehearsal_bundle
  alter column prior_release_id drop not null,
  alter column current_before_receipt_id drop not null,
  alter column prior_after_rollback_receipt_id drop not null,
  alter column current_after_restore_receipt_id drop not null;
alter table ops.staging_recovery_rehearsal_bundle
  drop constraint if exists staging_recovery_rehearsal_bundle_recovery_strategy_check,
  drop constraint if exists recovery_bundle_distinct_releases,
  drop constraint if exists recovery_bundle_distinct_receipts,
  drop constraint if exists staging_recovery_rehearsal_bundle_writer_session_user_check;
alter table ops.staging_recovery_rehearsal_bundle
  drop constraint if exists recovery_bundle_strategy_shape;
alter table ops.staging_recovery_rehearsal_bundle
  add constraint recovery_bundle_strategy_shape check (
    (recovery_strategy='rollback'
      and prior_release_id is not null
      and current_before_receipt_id is not null
      and prior_after_rollback_receipt_id is not null
      and current_after_restore_receipt_id is not null
      and forward_fix_result_id is null
      and candidate_git_sha is null
      and candidate_provider_version_id is null
      and current_release_id<>prior_release_id
      and current_before_receipt_id<>prior_after_rollback_receipt_id
      and current_before_receipt_id<>current_after_restore_receipt_id
      and prior_after_rollback_receipt_id<>current_after_restore_receipt_id)
    or
    (recovery_strategy='forward_fix'
      and prior_release_id is null
      and current_before_receipt_id is null
      and prior_after_rollback_receipt_id is null
      and current_after_restore_receipt_id is null
      and forward_fix_result_id is not null
      and candidate_git_sha is not null
      and candidate_git_sha ~ '^[0-9a-f]{40}$'
      and candidate_provider_version_id is not null)
  ),
  add constraint recovery_bundle_writer_strategy check (
    (recovery_strategy='rollback' and writer_session_user='carr_jobs')
    or
    (recovery_strategy='forward_fix' and writer_session_user='carr_program5_forward_fix_verifier')
  );
create unique index staging_recovery_bundle_forward_fix_result_once
  on ops.staging_recovery_rehearsal_bundle(forward_fix_result_id)
  where forward_fix_result_id is not null;

alter table ops.run drop constraint if exists recovery_rehearsal_assurance;
alter table ops.run add constraint recovery_rehearsal_assurance check (
  run_key not like 'recovery.rehearsal.%'
  or (release_id is not null and environment='staging' and evidence_ref is not null
      and recovery_strategy in ('rollback','forward_fix') and recovery_plan_ref is not null
      and (state<>'succeeded' or recovery_rehearsal_bundle_id is not null))
) not valid;

create or replace function ops.prepare_staging_forward_fix_rehearsal(
  p_idempotency_key uuid, p_correlation_id uuid, p_release_key text, p_git_sha text
) returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare rel ops.release%rowtype; existing ops.staging_forward_fix_rehearsal_attempt%rowtype;
  result_row ops.staging_forward_fix_rehearsal_result%rowtype; migration_hash text;
  migration_count integer; provider_uuid uuid; attempt_uuid uuid; expected_tag text;
begin
  if session_user<>'carr_jobs' then raise exception 'forward-fix rehearsal writer requires the carr_jobs session'; end if;
  if p_idempotency_key is null or p_correlation_id is null or coalesce(p_release_key,'')=''
     or coalesce(p_git_sha,'') !~ '^[0-9a-f]{40}$' then
    raise exception 'invalid typed forward-fix rehearsal input';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,315));
  perform pg_advisory_xact_lock(hashtextextended(p_correlation_id::text,315));
  select * into rel from ops.release where release_key=p_release_key;
  if not found or rel.environment<>'production' or rel.state<>'candidate'
     or rel.recovery_strategy<>'forward_fix' or rel.rollback_ready is not true
     or coalesce(btrim(rel.rollback_plan_ref),'')='' or coalesce(btrim(rel.plan_hash),'')=''
     or rel.service_id is null or rel.provider<>'cloudflare-workers'
     or coalesce(btrim(rel.provider_version_id),'')='' then
    raise exception 'forward-fix rehearsal target is not an exact forward-fix Production candidate';
  end if;
  provider_uuid:=rel.provider_version_id::uuid;
  if p_git_sha<>rel.git_sha or coalesce(cardinality(rel.migration_set),0)<=0
     or coalesce(rel.schema_highest_migration,'') !~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$'
     or coalesce(rel.schema_applied_count,0)<=0
     or coalesce(rel.schema_ledger_sha256,'') !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'forward-fix rehearsal target lacks the exact declared candidate identity';
  end if;
  migration_hash:=ops.program5_migration_set_sha256(rel.migration_set);
  migration_count:=cardinality(rel.migration_set);
  expected_tag:='carr-staging-forward-fix-'||replace(p_idempotency_key::text,'-','');
  select * into existing from ops.staging_forward_fix_rehearsal_attempt where idempotency_key=p_idempotency_key;
  if found then
    if (existing.correlation_id,existing.release_id,existing.service_id,existing.git_sha,
        existing.candidate_provider_version_id,existing.recovery_strategy,existing.recovery_plan_ref,
        existing.plan_hash,existing.expected_provider_tag,existing.declared_migration_set_sha256,
        existing.declared_migration_count,existing.declared_schema_highest_migration,
        existing.declared_schema_applied_count,existing.declared_schema_ledger_sha256) is distinct from
       (p_correlation_id,rel.id,rel.service_id,p_git_sha,provider_uuid,'forward_fix',rel.rollback_plan_ref,
        rel.plan_hash,expected_tag,migration_hash,migration_count,rel.schema_highest_migration,
        rel.schema_applied_count,rel.schema_ledger_sha256) then
      raise exception 'forward-fix rehearsal idempotency key was reused with changed input';
    end if;
    select * into result_row from ops.staging_forward_fix_rehearsal_result where rehearsal_attempt_id=existing.id;
    return jsonb_build_object('forward_fix_rehearsal_attempt_id',existing.id,
      'expected_provider_tag',existing.expected_provider_tag,
      'state',case when result_row.id is null then 'prepared' else 'observed' end,
      'mutation_claimed',exists(select 1 from ops.staging_forward_fix_rehearsal_claim where rehearsal_attempt_id=existing.id),
      'result_ref',result_row.evidence_ref,'replayed',true);
  end if;
  insert into ops.staging_forward_fix_rehearsal_attempt(
    idempotency_key,correlation_id,release_id,service_id,environment,git_sha,provider,
    candidate_provider_version_id,recovery_strategy,recovery_plan_ref,plan_hash,expected_provider_tag,
    declared_migration_set_sha256,declared_migration_count,declared_schema_highest_migration,
    declared_schema_applied_count,declared_schema_ledger_sha256,writer_session_user)
  values(p_idempotency_key,p_correlation_id,rel.id,rel.service_id,'staging',p_git_sha,'cloudflare-workers',
    provider_uuid,'forward_fix',rel.rollback_plan_ref,rel.plan_hash,expected_tag,migration_hash,migration_count,
    rel.schema_highest_migration,rel.schema_applied_count,rel.schema_ledger_sha256,session_user)
  returning id into attempt_uuid;
  return jsonb_build_object('forward_fix_rehearsal_attempt_id',attempt_uuid,
    'expected_provider_tag',expected_tag,'state','prepared','mutation_claimed',false,
    'result_ref',null,'replayed',false);
end $$;

create or replace function ops.claim_staging_forward_fix_rehearsal(p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare attempt ops.staging_forward_fix_rehearsal_attempt%rowtype; result_row ops.staging_forward_fix_rehearsal_result%rowtype; inserted_count integer;
begin
  if session_user<>'carr_jobs' then raise exception 'forward-fix rehearsal claim requires the carr_jobs session'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,315));
  select * into attempt from ops.staging_forward_fix_rehearsal_attempt where idempotency_key=p_idempotency_key;
  if not found then raise exception 'forward-fix rehearsal must be prepared before claim'; end if;
  select * into result_row from ops.staging_forward_fix_rehearsal_result where rehearsal_attempt_id=attempt.id;
  if found then return jsonb_build_object('forward_fix_rehearsal_attempt_id',attempt.id,'mutation_allowed',false,'state','observed','replayed',true); end if;
  insert into ops.staging_forward_fix_rehearsal_claim(rehearsal_attempt_id,writer_session_user)
  values(attempt.id,session_user) on conflict do nothing;
  get diagnostics inserted_count=row_count;
  return jsonb_build_object('forward_fix_rehearsal_attempt_id',attempt.id,'mutation_allowed',inserted_count=1,
    'state',case when inserted_count=1 then 'claimed' else 'claimed_pending_result' end,'replayed',inserted_count=0);
end $$;

create or replace function ops.record_staging_forward_fix_rehearsal(
  p_idempotency_key uuid, p_provider_version_id uuid, p_provider_tag text, p_verb_count integer,
  p_schema_highest_migration text, p_schema_applied_count integer, p_schema_ledger_sha256 text,
  p_doctrine_generation bigint, p_program6_actions_enabled boolean
) returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare attempt ops.staging_forward_fix_rehearsal_attempt%rowtype; existing ops.staging_forward_fix_rehearsal_result%rowtype;
  result_uuid uuid; bundle_uuid uuid; run_uuid uuid; observed_time timestamptz:=clock_timestamp();
  projection jsonb; projection_hash text; result_ref text; bundle_projection jsonb; bundle_hash text; bundle_ref text;
begin
  if session_user<>'carr_program5_forward_fix_verifier'
     or not pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member') then
    raise exception using errcode='42501',message='forward-fix rehearsal result requires the exact scoped verifier capability';
  end if;
  if p_idempotency_key is null or p_provider_version_id is null or coalesce(p_provider_tag,'') !~ '^carr-staging-forward-fix-[0-9a-f]{32}$'
     or coalesce(p_verb_count,0)<=0 or coalesce(p_schema_highest_migration,'') !~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$'
     or coalesce(p_schema_applied_count,0)<=0 or coalesce(p_schema_ledger_sha256,'') !~ '^sha256:[0-9a-f]{64}$'
     or coalesce(p_doctrine_generation,-1)<0 or p_program6_actions_enabled is null then
    raise exception 'invalid typed forward-fix staging readback input';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,315));
  select * into attempt from ops.staging_forward_fix_rehearsal_attempt where idempotency_key=p_idempotency_key;
  if not found then raise exception 'forward-fix rehearsal result has no prepared attempt'; end if;
  if not exists(select 1 from ops.staging_forward_fix_rehearsal_claim where rehearsal_attempt_id=attempt.id) then
    raise exception 'forward-fix rehearsal result was never claimed';
  end if;
  select * into existing from ops.staging_forward_fix_rehearsal_result where rehearsal_attempt_id=attempt.id;
  if found then
    if (existing.provider_version_id,existing.provider_tag,existing.verb_count,existing.schema_highest_migration,
        existing.schema_applied_count,existing.schema_ledger_sha256,existing.doctrine_generation,existing.program6_actions_enabled) is distinct from
       (p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,p_schema_applied_count,
        p_schema_ledger_sha256,p_doctrine_generation,p_program6_actions_enabled) then
      raise exception 'forward-fix rehearsal result idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('forward_fix_rehearsal_result_id',existing.id,'result_ref',existing.evidence_ref,
      'bundle_id',(select id from ops.staging_recovery_rehearsal_bundle where forward_fix_result_id=existing.id),
      'recovery_run_id',(select id from ops.run where recovery_rehearsal_bundle_id=(select id from ops.staging_recovery_rehearsal_bundle where forward_fix_result_id=existing.id)),
      'replayed',true);
  end if;
  if p_provider_version_id=attempt.candidate_provider_version_id or p_provider_tag<>attempt.expected_provider_tag
     or p_schema_highest_migration<>attempt.declared_schema_highest_migration
     or p_schema_applied_count<>attempt.declared_schema_applied_count
     or p_schema_ledger_sha256<>attempt.declared_schema_ledger_sha256 then
    raise exception 'forward-fix rehearsal readback does not match the exact staging candidate boundary';
  end if;
  projection:=jsonb_build_object('forward_fix_rehearsal_attempt_id',attempt.id,'release_id',attempt.release_id,
    'service_id',attempt.service_id,'environment','staging','git_sha',attempt.git_sha,
    'candidate_provider_version_id',attempt.candidate_provider_version_id,'provider_version_id',p_provider_version_id,
    'provider_tag',p_provider_tag,'verb_count',p_verb_count,'schema_highest_migration',p_schema_highest_migration,
    'schema_applied_count',p_schema_applied_count,'schema_ledger_sha256',p_schema_ledger_sha256,
    'doctrine_generation',p_doctrine_generation,'program6_actions_enabled',p_program6_actions_enabled);
  projection_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex');
  result_ref:='ops.staging-forward-fix-readback:'||projection_hash;
  insert into ops.staging_forward_fix_rehearsal_result(
    idempotency_key,rehearsal_attempt_id,provider_version_id,provider_tag,verb_count,schema_highest_migration,
    schema_applied_count,schema_ledger_sha256,doctrine_generation,program6_actions_enabled,
    projection_sha256,evidence_ref,observed_at,writer_session_user)
  values(p_idempotency_key,attempt.id,p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,
    p_schema_applied_count,p_schema_ledger_sha256,p_doctrine_generation,p_program6_actions_enabled,
    projection_hash,result_ref,observed_time,session_user) returning id into result_uuid;
  bundle_projection:=jsonb_build_object('forward_fix_rehearsal_attempt_id',attempt.id,'forward_fix_result_id',result_uuid,
    'current_release_id',attempt.release_id,'service_id',attempt.service_id,'environment','staging','git_sha',attempt.git_sha,
    'candidate_provider_version_id',attempt.candidate_provider_version_id,'recovery_strategy','forward_fix',
    'recovery_plan_ref',attempt.recovery_plan_ref,'plan_hash',attempt.plan_hash,
    'declared_migration_set_sha256',attempt.declared_migration_set_sha256,
    'declared_migration_count',attempt.declared_migration_count,
    'declared_schema_highest_migration',attempt.declared_schema_highest_migration,
    'declared_schema_applied_count',attempt.declared_schema_applied_count,
    'declared_schema_ledger_sha256',attempt.declared_schema_ledger_sha256);
  bundle_hash:='sha256:'||encode(public.digest(bundle_projection::text,'sha256'),'hex');
  bundle_ref:='ops.staging-recovery-bundle:'||bundle_hash;
  insert into ops.staging_recovery_rehearsal_bundle(
    recovery_attempt_id,correlation_id,current_release_id,prior_release_id,service_id,environment,
    current_before_receipt_id,prior_after_rollback_receipt_id,current_after_restore_receipt_id,
    forward_fix_result_id,candidate_git_sha,candidate_provider_version_id,recovery_strategy,recovery_plan_ref,
    plan_hash,declared_migration_set_sha256,declared_migration_count,declared_schema_highest_migration,
    declared_schema_applied_count,declared_schema_ledger_sha256,bundle_sha256,evidence_ref,completed_at,writer_session_user)
  values(attempt.id,attempt.correlation_id,attempt.release_id,null,attempt.service_id,'staging',null,null,null,
    result_uuid,attempt.git_sha,attempt.candidate_provider_version_id,'forward_fix',attempt.recovery_plan_ref,
    attempt.plan_hash,attempt.declared_migration_set_sha256,attempt.declared_migration_count,
    attempt.declared_schema_highest_migration,attempt.declared_schema_applied_count,
    attempt.declared_schema_ledger_sha256,bundle_hash,bundle_ref,observed_time,session_user)
  returning id into bundle_uuid;
  insert into ops.run(correlation_id,kind,service_id,environment,run_key,state,started_at,ended_at,source_kind,
    source_ref,observed_at,evidence_ref,release_id,recovery_strategy,recovery_plan_ref,recovery_rehearsal_bundle_id)
  values(attempt.correlation_id,'check',attempt.service_id,'staging','recovery.rehearsal.forward-fix','succeeded',
    observed_time,observed_time,'wrapper','ops.record_staging_forward_fix_rehearsal',observed_time,bundle_ref,
    attempt.release_id,'forward_fix',attempt.recovery_plan_ref,bundle_uuid) returning id into run_uuid;
  return jsonb_build_object('forward_fix_rehearsal_result_id',result_uuid,'result_ref',result_ref,
    'bundle_id',bundle_uuid,'recovery_run_id',run_uuid,'replayed',false);
end $$;

-- The final recorder's login gets exactly this six-scalar declaration
-- projection, not SELECT on the append-only attempt ledger.
create or replace function ops.read_staging_forward_fix_rehearsal_declaration(p_idempotency_key uuid)
returns table(expected_provider_tag text, declared_migration_set_sha256 text,
  declared_migration_count integer, declared_schema_highest_migration text,
  declared_schema_applied_count integer, declared_schema_ledger_sha256 text)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if session_user<>'carr_program5_forward_fix_verifier'
     or not pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member') then
    raise exception using errcode='42501',message='forward-fix declaration projection requires the exact scoped verifier capability';
  end if;
  return query select a.expected_provider_tag,a.declared_migration_set_sha256,
    a.declared_migration_count,a.declared_schema_highest_migration,
    a.declared_schema_applied_count,a.declared_schema_ledger_sha256
  from ops.staging_forward_fix_rehearsal_attempt a where a.idempotency_key=p_idempotency_key;
end $$;

create or replace function ops.validate_recovery_rehearsal_run()
returns trigger language plpgsql as $$
declare b ops.staging_recovery_rehearsal_bundle%rowtype; expected_started timestamptz;
begin
  if new.run_key like 'recovery.rehearsal.%' and new.state='succeeded'
     and new.recovery_rehearsal_bundle_id is null then
    raise exception 'successful recovery rehearsal run requires a typed bundle';
  end if;
  if new.recovery_rehearsal_bundle_id is null then return new; end if;
  select * into strict b from ops.staging_recovery_rehearsal_bundle where id=new.recovery_rehearsal_bundle_id;
  expected_started:=case when b.recovery_strategy='rollback' then
    (select observed_at from ops.staging_release_readback_receipt where id=b.current_before_receipt_id)
    else (select observed_at from ops.staging_forward_fix_rehearsal_result where id=b.forward_fix_result_id) end;
  if new.kind<>'check' or new.run_key<>(case when b.recovery_strategy='rollback' then 'recovery.rehearsal.worker' else 'recovery.rehearsal.forward-fix' end)
     or new.state<>'succeeded' or new.environment<>'staging' or new.release_id<>b.current_release_id
     or new.service_id<>b.service_id or new.correlation_id<>b.correlation_id
     or new.recovery_strategy<>b.recovery_strategy or new.recovery_plan_ref<>b.recovery_plan_ref
     or new.evidence_ref<>b.evidence_ref or new.started_at is distinct from expected_started
     or new.ended_at is distinct from b.completed_at then
    raise exception 'recovery rehearsal run does not exactly match its typed bundle';
  end if;
  return new;
end $$;

create or replace function ops.program5_exact_recovery_rehearsal(p_release_id uuid, p_not_before timestamptz default null)
returns uuid language sql stable security definer set search_path=ops,public,pg_temp as $$
  select r.id
    from ops.release rel
    join ops.run r on r.release_id=rel.id and r.service_id=rel.service_id
    join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id
   where rel.id=$1 and r.environment='staging' and r.state='succeeded' and r.evidence_ref=b.evidence_ref
     and r.run_key=case when rel.recovery_strategy='rollback' then 'recovery.rehearsal.worker' else 'recovery.rehearsal.forward-fix' end
     and b.current_release_id=rel.id and b.service_id=rel.service_id and b.recovery_strategy=rel.recovery_strategy
     and b.recovery_plan_ref=rel.rollback_plan_ref and b.plan_hash=rel.plan_hash
     and b.declared_migration_set_sha256=ops.program5_migration_set_sha256(rel.migration_set)
     and b.declared_migration_count=cardinality(rel.migration_set)
     and b.declared_schema_highest_migration=rel.schema_highest_migration
     and b.declared_schema_applied_count=rel.schema_applied_count
     and b.declared_schema_ledger_sha256=rel.schema_ledger_sha256
     and (rel.recovery_strategy='rollback' or (b.candidate_git_sha=rel.git_sha
          and b.candidate_provider_version_id::text=rel.provider_version_id
          and exists(select 1 from ops.staging_forward_fix_rehearsal_result x where x.id=b.forward_fix_result_id)))
     and (p_not_before is null or b.completed_at>=p_not_before)
   order by b.completed_at desc limit 1
$$;

create or replace function ops.release_approval_requires_recovery_rehearsal()
returns trigger language plpgsql as $$
begin
  if new.environment='production' and new.state='approved'
     and (tg_op='INSERT' or old.environment is distinct from 'production' or old.state is distinct from 'approved')
     and ops.program5_exact_recovery_rehearsal(new.id) is null then
    raise exception 'Production release % cannot be approved: no exact typed recovery bundle',new.release_key;
  end if;
  return new;
end $$;

create or replace function ops.release_completion_requires_a_read_back()
returns trigger language plpgsql as $$
begin
  if new.state='complete' and (tg_op='INSERT' or old.state is distinct from 'complete') then
    if not exists(select 1 from ops.deployment d where d.release_id=new.id and d.service_id=new.service_id
      and d.environment='production' and d.state='complete' and d.read_back_at is not null and d.git_sha=new.git_sha
      and d.provider=new.provider and d.provider_version_id=new.provider_version_id) then
      raise exception 'release % cannot be complete: no exact Production read-back',new.release_key;
    end if;
    if not exists(select 1 from ops.run r join ops.deployment d on d.release_id=r.release_id and d.service_id=r.service_id and d.correlation_id=r.correlation_id
      where r.release_id=new.id and r.service_id=new.service_id and r.environment='production' and r.run_key like 'performance.%'
        and r.state='succeeded' and r.evidence_ref is not null and r.budget_ms=new.performance_budget_ms
        and r.duration_ms>0 and r.duration_ms<=r.budget_ms and d.environment='production' and d.state='complete'
        and d.read_back_at is not null and d.git_sha=new.git_sha and d.provider=new.provider and d.provider_version_id=new.provider_version_id) then
      raise exception 'release % cannot be complete: no within-budget Production performance receipt',new.release_key;
    end if;
    if ops.program5_exact_recovery_rehearsal(new.id,new.approved_at-interval '24 hours') is null then
      raise exception 'release % cannot be complete: no exact typed recovery bundle',new.release_key;
    end if;
  end if;
  return new;
end $$;

create or replace function ops.approve_program5_release(
  p_release_key text,p_plan_hash text,p_idempotency_key uuid,p_expires_hours integer,
  p_verifier_actor text,p_verifier_evidence_ref text
) returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare actor_slug text; rel ops.release%rowtype; existing ops.release_approval_receipt%rowtype;
  rehearsal_run ops.run%rowtype; approval_uuid uuid; approved_time timestamptz; expiry_time timestamptz;
  projection jsonb; approval_hash text; approval_ref text; supplied_verifier_actor text; supplied_verifier_evidence text;
  candidate_verifier_actor text; candidate_verifier_evidence text; verifier_actor_value text; verifier_evidence_value text;
begin
  actor_slug:=ops.authority_actor_slug();
  if actor_slug<>'joe' then raise exception 'Program 5 Production approval requires Joe authority'; end if;
  if p_idempotency_key is null or coalesce(p_release_key,'')='' or coalesce(p_plan_hash,'')='' or coalesce(p_expires_hours,0) not between 1 and 24 then raise exception 'invalid Program 5 approval input'; end if;
  if (p_verifier_actor is null)<>(p_verifier_evidence_ref is null) or (p_verifier_actor is not null and (btrim(p_verifier_actor)='' or btrim(p_verifier_evidence_ref)='')) then raise exception 'supplied verifier actor and evidence must be an atomic nonblank pair'; end if;
  supplied_verifier_actor:=case when p_verifier_actor is null then null else lower(btrim(p_verifier_actor)) end;
  supplied_verifier_evidence:=case when p_verifier_evidence_ref is null then null else btrim(p_verifier_evidence_ref) end;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into existing from ops.release_approval_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.plan_hash<>p_plan_hash or existing.approval_expires_at-existing.approved_at<>make_interval(hours=>p_expires_hours)
       or not exists(select 1 from ops.release where id=existing.release_id and release_key=p_release_key and plan_hash=existing.plan_hash and approval_receipt_id=existing.id and state in ('approved','deploying','verifying','complete') and approved_at=existing.approved_at and approval_expires_at=existing.approval_expires_at and (supplied_verifier_actor is null or existing.verifier_actor=supplied_verifier_actor) and (supplied_verifier_evidence is null or existing.verifier_evidence_ref=supplied_verifier_evidence)) then raise exception 'Program 5 approval idempotency key was reused with changed input'; end if;
    return jsonb_build_object('approval_receipt_id',existing.id,'approval_ref',existing.evidence_ref,'approval_expires_at',existing.approval_expires_at,'replayed',true);
  end if;
  select * into rel from ops.release where release_key=p_release_key for update;
  if not found or rel.environment<>'production' or rel.state<>'candidate' or rel.plan_hash is distinct from p_plan_hash then raise exception 'release is not the exact Production candidate plan requested'; end if;
  if (rel.verifier_actor is null)<>(rel.verifier_evidence_ref is null) or (rel.verifier_actor is not null and (btrim(rel.verifier_actor)='' or btrim(rel.verifier_evidence_ref)='')) then raise exception 'candidate verifier actor and evidence must be an atomic nonblank pair'; end if;
  candidate_verifier_actor:=case when rel.verifier_actor is null then null else lower(btrim(rel.verifier_actor)) end;
  candidate_verifier_evidence:=case when rel.verifier_evidence_ref is null then null else btrim(rel.verifier_evidence_ref) end;
  verifier_actor_value:=coalesce(supplied_verifier_actor,candidate_verifier_actor);
  verifier_evidence_value:=coalesce(supplied_verifier_evidence,candidate_verifier_evidence);
  if verifier_actor_value is null or verifier_evidence_value is null then raise exception 'release cannot be approved without an INDEPENDENT VERIFIER and verification evidence'; end if;
  if rel.maker_actor is not null and verifier_actor_value=lower(btrim(rel.maker_actor)) then raise exception 'maker cannot independently verify their own release'; end if;
  select * into rehearsal_run from ops.run where id=ops.program5_exact_recovery_rehearsal(rel.id,clock_timestamp()-interval '24 hours');
  if not found then raise exception 'release has no exact typed recovery rehearsal'; end if;
  approved_time:=clock_timestamp(); expiry_time:=approved_time+make_interval(hours=>p_expires_hours);
  projection:=jsonb_build_object('release_id',rel.id,'plan_hash',rel.plan_hash,'recovery_run_id',rehearsal_run.id,'recovery_bundle_id',rehearsal_run.recovery_rehearsal_bundle_id,'approved_by_actor',actor_slug,'approved_at',approved_time,'approval_expires_at',expiry_time,'verifier_actor',verifier_actor_value,'verifier_evidence_ref',verifier_evidence_value);
  approval_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex'); approval_ref:='ops.program5-release-approval:'||approval_hash;
  insert into ops.release_approval_receipt(idempotency_key,release_id,recovery_run_id,recovery_bundle_id,plan_hash,approved_by_actor,approved_at,approval_expires_at,verifier_actor,verifier_evidence_ref,approval_sha256,evidence_ref)
  values(p_idempotency_key,rel.id,rehearsal_run.id,rehearsal_run.recovery_rehearsal_bundle_id,rel.plan_hash,actor_slug,approved_time,expiry_time,verifier_actor_value,verifier_evidence_value,approval_hash,approval_ref) returning id into approval_uuid;
  update ops.release set verifier_actor=verifier_actor_value,verifier_evidence_ref=verifier_evidence_value,state='approved',approved_by_actor=actor_slug,approved_at=approved_time,approval_expires_at=expiry_time,approval_receipt_id=approval_uuid where id=rel.id;
  return jsonb_build_object('approval_receipt_id',approval_uuid,'approval_ref',approval_ref,'approval_expires_at',expiry_time,'replayed',false);
end $$;

create or replace function ops.approve_program5_release(
  p_release_key text,p_plan_hash text,p_idempotency_key uuid,p_expires_hours integer
) returns jsonb language sql security definer set search_path=ops,public,pg_temp as $$
  select ops.approve_program5_release($1,$2,$3,$4,null,null)
$$;

revoke all on ops.staging_forward_fix_rehearsal_attempt,ops.staging_forward_fix_rehearsal_claim,
  ops.staging_forward_fix_rehearsal_result from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_program5_forward_fix_verifiers;
grant select on ops.staging_forward_fix_rehearsal_attempt,ops.staging_forward_fix_rehearsal_claim,
  ops.staging_forward_fix_rehearsal_result to carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.prepare_staging_forward_fix_rehearsal(uuid,uuid,text,text) from public,carr_reader,carr_writer,carr_authority;
revoke all on function ops.claim_staging_forward_fix_rehearsal(uuid) from public,carr_reader,carr_writer,carr_authority;
revoke all on function ops.record_staging_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.read_staging_forward_fix_rehearsal_declaration(uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.prepare_staging_forward_fix_rehearsal(uuid,uuid,text,text) to carr_jobs;
grant execute on function ops.claim_staging_forward_fix_rehearsal(uuid) to carr_jobs;
grant execute on function ops.record_staging_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean) to carr_program5_forward_fix_verifiers;
grant execute on function ops.read_staging_forward_fix_rehearsal_declaration(uuid) to carr_program5_forward_fix_verifiers;
revoke all on function ops.program5_exact_recovery_rehearsal(uuid,timestamptz) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.program5_exact_recovery_rehearsal(uuid,timestamptz) to carr_jobs,carr_authority;

commit;

do $$
begin
  if not exists(select 1 from pg_roles where rolname='carr_program5_forward_fix_verifiers' and not rolcanlogin)
     or has_table_privilege('carr_jobs','ops.staging_forward_fix_rehearsal_attempt','insert')
     or has_table_privilege('carr_writer','ops.staging_forward_fix_rehearsal_result','insert')
     or has_function_privilege('carr_jobs','ops.record_staging_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean)'::regprocedure,'execute')
     or has_function_privilege('carr_writer','ops.record_staging_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean)'::regprocedure,'execute')
     or not has_function_privilege('carr_program5_forward_fix_verifiers','ops.record_staging_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean)'::regprocedure,'execute')
     or has_function_privilege('carr_jobs','ops.read_staging_forward_fix_rehearsal_declaration(uuid)'::regprocedure,'execute')
     or has_function_privilege('carr_writer','ops.read_staging_forward_fix_rehearsal_declaration(uuid)'::regprocedure,'execute')
     or not has_function_privilege('carr_program5_forward_fix_verifiers','ops.read_staging_forward_fix_rehearsal_declaration(uuid)'::regprocedure,'execute')
     or has_function_privilege('carr_jobs','ops.approve_program5_release(text,text,uuid,integer)'::regprocedure,'execute') then
    raise exception '0315 FAILED: forward-fix rehearsal authority boundary is wrong';
  end if;
end $$;
