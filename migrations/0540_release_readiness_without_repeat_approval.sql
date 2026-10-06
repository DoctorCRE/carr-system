-- A technical release readiness fact for attended product delivery. Historical
-- Joe approvals remain immutable evidence; new readiness never impersonates one.

alter table ops.release add column readiness_receipt_id uuid;
alter table ops.release add column ready_at timestamptz;
alter table ops.release drop constraint release_state_check;
alter table ops.release add constraint release_state_check check
  (state in ('draft','candidate','ready','approved','deploying','verifying',
             'complete','failed','superseded','abandoned'));
alter table ops.release drop constraint an_approved_release_names_its_approval;
alter table ops.release add constraint an_approved_release_names_its_approval check
  (state in ('draft','candidate','abandoned')
   or (plan_hash is not null and
       ((readiness_receipt_id is not null and ready_at is not null
         and approved_by_actor is null and approved_at is null and approval_expires_at is null)
        or (approved_by_actor is not null and approved_at is not null
            and approval_expires_at is not null))));
alter table ops.release drop constraint production_promotion_requires_assurance;
alter table ops.release add constraint production_promotion_requires_assurance check
  (environment<>'production' or state not in
    ('ready','approved','deploying','verifying','complete') or
   (performance_budget_ref is not null and performance_budget_ms>0
    and recovery_strategy in ('rollback','forward_fix'))) not valid;

create table ops.release_readiness_receipt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  release_id uuid not null unique references ops.release(id) on delete restrict,
  git_sha text not null check (git_sha ~ '^[0-9a-f]{40}$'),
  provider text not null,
  provider_version_id text not null,
  plan_hash text not null check (plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  recovery_run_id uuid not null references ops.run(id) on delete restrict,
  verifier_actor text not null,
  verifier_evidence_ref text not null,
  session_login text not null check (session_login='carr_jobs'),
  ready_at timestamptz not null,
  receipt_sha256 text not null unique check (receipt_sha256 ~ '^sha256:[0-9a-f]{64}$')
);
alter table ops.release add constraint release_readiness_receipt_id_fkey
  foreign key (readiness_receipt_id) references ops.release_readiness_receipt(id) on delete restrict;
create function ops.release_readiness_append_only() returns trigger language plpgsql as $$
begin raise exception 'release readiness evidence is append-only'; end $$;
create trigger release_readiness_append_only before update or delete
  on ops.release_readiness_receipt for each row execute function ops.release_readiness_append_only();
revoke all on ops.release_readiness_receipt from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant select on ops.release_readiness_receipt to carr_reader,carr_writer,carr_jobs;

create or replace function ops.release_plan_revision_invalidates_approval()
returns trigger language plpgsql as $$
begin
  if new.plan_hash is distinct from old.plan_hash
     and old.state in ('ready','approved','deploying','verifying') then
    new.state:='candidate';
    new.approved_by_actor:=null; new.approved_at:=null;
    new.approval_expires_at:=null; new.approval_receipt_id:=null;
    new.staging_approval_receipt_id:=null;
    new.readiness_receipt_id:=null; new.ready_at:=null;
  end if;
  new.updated_at:=now(); return new;
end $$;

create or replace function ops.release_assurance_is_immutable()
returns trigger language plpgsql as $$
begin
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

create or replace function ops.release_provider_identity_is_immutable()
returns trigger language plpgsql as $$
begin
  if old.environment='production'
     and old.state in ('ready','approved','deploying','verifying','complete')
     and new.state<>'candidate'
     and (new.provider,new.provider_version_id) is distinct from
         (old.provider,old.provider_version_id) then
    raise exception 'Production provider identity is immutable after readiness';
  end if;
  return new;
end $$;

create or replace function ops.release_readiness_is_typed()
returns trigger language plpgsql security definer
  set search_path=pg_catalog,ops,public as $$
declare receipt ops.release_readiness_receipt%rowtype;
begin
  if tg_op='UPDATE' and old.readiness_receipt_id is not null
     and new.state<>'candidate'
     and (new.readiness_receipt_id,new.ready_at) is distinct from
         (old.readiness_receipt_id,old.ready_at) then
    raise exception 'promoted release readiness is immutable';
  end if;
  if new.environment<>'production' or new.state not in
      ('ready','deploying','verifying','complete') or
      new.readiness_receipt_id is null then return new; end if;
  select * into receipt from ops.release_readiness_receipt
    where id=new.readiness_receipt_id;
  if not found or receipt.release_id is distinct from new.id
     or receipt.git_sha is distinct from new.git_sha
     or receipt.provider is distinct from new.provider
     or receipt.provider_version_id is distinct from new.provider_version_id
     or receipt.plan_hash is distinct from new.plan_hash
     or receipt.verifier_actor is distinct from new.verifier_actor
     or receipt.verifier_evidence_ref is distinct from new.verifier_evidence_ref
     or receipt.ready_at is distinct from new.ready_at
     or not exists (select 1 from ops.run r
       where r.id=receipt.recovery_run_id and r.release_id=new.id
         and r.state='succeeded' and r.evidence_ref is not null)
     or new.approved_by_actor is not null or new.approved_at is not null
     or new.approval_expires_at is not null then
    raise exception 'Production release readiness does not match exact typed evidence';
  end if;
  return new;
end $$;
revoke all on function ops.release_readiness_is_typed()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
create trigger z_release_readiness_is_typed before insert or update of
  state,environment,git_sha,provider,provider_version_id,plan_hash,
  verifier_actor,verifier_evidence_ref,readiness_receipt_id,ready_at
  on ops.release for each row execute function ops.release_readiness_is_typed();

create function ops.release_technical_readiness_current(p_release_id uuid)
returns boolean language sql stable security definer
  set search_path=pg_catalog,ops,public as $$
  select exists(
    select 1 from ops.release r
    join ops.release_readiness_receipt q on q.id=r.readiness_receipt_id
    where r.id=p_release_id and r.environment='production'
      and r.state in ('ready','deploying','verifying')
      and q.release_id=r.id and q.git_sha=r.git_sha
      and q.provider=r.provider and q.provider_version_id=r.provider_version_id
      and q.plan_hash=r.plan_hash
      and ops.program5_exact_recovery_rehearsal(r.id,now()-interval '24 hours') is not null)
$$;
revoke all on function ops.release_technical_readiness_current(uuid)
  from public,carr_reader,carr_authority;
grant execute on function ops.release_technical_readiness_current(uuid)
  to carr_writer,carr_jobs;

-- A stale ready release may rehearse again through the existing candidate-only
-- staging route. Its original append-only readiness receipt remains the fact
-- of first qualification; a fresh exact run independently restores recency.
create function ops.reopen_program5_release_rehearsal(
  p_release_key text,p_plan_hash text
) returns jsonb language plpgsql security definer
  set search_path=pg_catalog,ops,public as $$
declare rel ops.release%rowtype;
begin
  if session_user<>'carr_jobs' then
    raise exception 'recovery rehearsal reopen requires the authenticated jobs service';
  end if;
  select * into rel from ops.release where release_key=p_release_key for update;
  if not found or rel.environment is distinct from 'production'
     or rel.plan_hash is distinct from p_plan_hash
     or rel.readiness_receipt_id is null
     or not exists (select 1 from ops.release_readiness_receipt q
       where q.id=rel.readiness_receipt_id and q.release_id=rel.id
         and q.git_sha=rel.git_sha and q.provider=rel.provider
         and q.provider_version_id=rel.provider_version_id
         and q.plan_hash=rel.plan_hash) then
    raise exception 'release has no exact service readiness to rehearse';
  end if;
  if rel.state='candidate' then
    return jsonb_build_object('release_id',rel.id,'replayed',true);
  end if;
  if rel.state<>'ready' or ops.release_technical_readiness_current(rel.id) then
    raise exception 'only a stale ready release may reopen rehearsal';
  end if;
  update ops.release set state='candidate' where id=rel.id;
  return jsonb_build_object('release_id',rel.id,'replayed',false);
end $$;
revoke all on function ops.reopen_program5_release_rehearsal(text,text)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.reopen_program5_release_rehearsal(text,text)
  to carr_jobs;

-- A service login records its own readiness after independent technical facts
-- exist. No caller may supply a Joe actor or approval timestamp.
create function ops.qualify_program5_release(
  p_release_key text,p_plan_hash text,p_idempotency_key uuid
) returns jsonb language plpgsql security definer
  set search_path=pg_catalog,ops,public as $$
declare rel ops.release%rowtype; prior ops.release_readiness_receipt%rowtype;
  rehearsal uuid; stamp timestamptz; digest_value text; receipt_id uuid;
begin
  if session_user<>'carr_jobs' then
    raise exception 'release readiness requires the authenticated jobs service';
  end if;
  if p_idempotency_key is null or p_plan_hash is null
     or p_plan_hash !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'invalid release readiness input';
  end if;
  select * into prior from ops.release_readiness_receipt
    where idempotency_key=p_idempotency_key;
  if found then
    select * into rel from ops.release where id=prior.release_id for update;
    if not found or prior.plan_hash is distinct from p_plan_hash
       or rel.release_key is distinct from p_release_key
       or rel.readiness_receipt_id is distinct from prior.id
       or rel.git_sha is distinct from prior.git_sha
       or rel.provider is distinct from prior.provider
       or rel.provider_version_id is distinct from prior.provider_version_id
       or rel.plan_hash is distinct from prior.plan_hash then
      raise exception 'readiness idempotency key was reused with changed input';
    end if;
    if rel.state='candidate' then
      rehearsal:=ops.program5_exact_recovery_rehearsal(
        rel.id,clock_timestamp()-interval '24 hours');
      if rehearsal is null or rehearsal=prior.recovery_run_id then
        raise exception 'reopened release has no new fresh exact typed recovery rehearsal';
      end if;
      update ops.release set state='ready' where id=rel.id;
      return jsonb_build_object('readiness_receipt_id',prior.id,
        'ready_at',prior.ready_at,'replayed',true,'refreshed',true,
        'current_recovery_run_id',rehearsal);
    end if;
    return jsonb_build_object('readiness_receipt_id',prior.id,
      'ready_at',prior.ready_at,'replayed',true,'refreshed',false);
  end if;
  select * into rel from ops.release where release_key=p_release_key for update;
  if not found or rel.environment is distinct from 'production'
     or rel.state is distinct from 'candidate'
     or rel.plan_hash is distinct from p_plan_hash
     or rel.provider is distinct from 'cloudflare-workers'
     or coalesce(rel.provider_version_id,'') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     or rel.maker_session_user is distinct from 'carr_jobs'
     or rel.maker_actor is distinct from 'carr_jobs'
     or rel.maker_authority_verified is distinct from false
     or rel.approved_by_actor is not null or rel.approval_receipt_id is not null
     or rel.artifact_digest is null or rel.dependency_lock_digest is null
     or rel.test_evidence_ref is null or rel.security_evidence_ref is null
     or rel.verifier_actor is null or rel.verifier_evidence_ref is null
     or rel.verifier_actor=rel.maker_actor or rel.rollback_ready is not true
     or rel.performance_budget_ref is null or coalesce(rel.performance_budget_ms,0)<=0
     or rel.recovery_strategy is null
     or rel.recovery_strategy not in ('rollback','forward_fix')
     or rel.rollback_plan_ref is null then
    raise exception 'release is not an independently verified exact Production candidate';
  end if;
  stamp:=clock_timestamp();
  rehearsal:=ops.program5_exact_recovery_rehearsal(rel.id,stamp-interval '24 hours');
  if rehearsal is null then raise exception 'release has no fresh exact typed recovery rehearsal'; end if;
  digest_value:='sha256:'||encode(public.digest(
    jsonb_build_object('release_id',rel.id,'git_sha',rel.git_sha,
      'provider',rel.provider,'provider_version_id',rel.provider_version_id,
      'plan_hash',rel.plan_hash,'recovery_run_id',rehearsal,
      'verifier_actor',rel.verifier_actor,
      'verifier_evidence_ref',rel.verifier_evidence_ref,
      'session_login',session_user,'ready_at',stamp)::text,'sha256'),'hex');
  insert into ops.release_readiness_receipt
    (idempotency_key,release_id,git_sha,provider,provider_version_id,plan_hash,
     recovery_run_id,verifier_actor,verifier_evidence_ref,session_login,ready_at,receipt_sha256)
  values(p_idempotency_key,rel.id,rel.git_sha,rel.provider,rel.provider_version_id,
    rel.plan_hash,rehearsal,rel.verifier_actor,rel.verifier_evidence_ref,
    session_user,stamp,digest_value) returning id into receipt_id;
  update ops.release set state='ready',ready_at=stamp,
    readiness_receipt_id=receipt_id where id=rel.id;
  return jsonb_build_object('readiness_receipt_id',receipt_id,
    'ready_at',stamp,'replayed',false);
end $$;
revoke all on function ops.qualify_program5_release(text,text,uuid)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.qualify_program5_release(text,text,uuid) to carr_jobs;

-- Legacy approval facts are still checked on the old route. The service
-- readiness route carries its own typed projection and cannot create approval.
create or replace function ops.program5_release_approval_is_joe_owned()
returns trigger language plpgsql as $$
begin
  if new.environment='production' and new.readiness_receipt_id is not null then
    if new.approved_by_actor is not null or new.approval_receipt_id is not null
       or new.approved_at is not null or new.approval_expires_at is not null then
      raise exception 'service readiness cannot impersonate Joe approval';
    end if;
    return new;
  end if;
  if new.environment='production'
     and new.state in ('approved','deploying','verifying','complete')
     and (tg_op='INSERT' or old.state not in ('approved','deploying','verifying','complete')) then
    if new.state<>'approved' or session_user<>'carr_authority_joe'
       or new.approved_by_actor<>'joe'
       or new.approval_receipt_id is null or not exists (
         select 1 from ops.release_approval_receipt a
          where a.id=new.approval_receipt_id and a.release_id=new.id
            and a.plan_hash=new.plan_hash and a.approved_by_actor='joe'
            and a.approved_at=new.approved_at
            and a.approval_expires_at=new.approval_expires_at) then
      raise exception 'legacy Production approval requires Joe authority and typed receipt';
    end if;
  end if;
  if tg_op='UPDATE' and old.environment='production'
     and old.state in ('approved','deploying','verifying','complete')
     and new.state in ('approved','deploying','verifying','complete')
     and (new.approved_by_actor,new.approved_at,new.approval_expires_at,new.approval_receipt_id)
       is distinct from
       (old.approved_by_actor,old.approved_at,old.approval_expires_at,old.approval_receipt_id) then
    raise exception 'legacy approval projection is immutable';
  end if;
  return new;
end $$;

create or replace function ops.release_approval_requires_recovery_rehearsal()
returns trigger language plpgsql as $$
begin
  if new.environment='production' and new.state in ('ready','approved')
     and (tg_op='INSERT' or old.state is distinct from new.state)
     and ops.program5_exact_recovery_rehearsal(new.id) is null then
    raise exception 'Production release % has no exact typed recovery bundle',new.release_key;
  end if;
  return new;
end $$;

create or replace function ops.release_schema_declaration_matches_live()
returns trigger language plpgsql security definer
  set search_path=pg_catalog,ops,public as $$
declare live_count integer; live_highest text; live_digest text;
begin
  if new.environment='production' and new.state in ('ready','approved') then
    select count(*)::integer,max(filename collate "C"),
      'sha256:'||encode(public.digest(coalesce(string_agg(
        convert_to(filename,'UTF8')||decode('00','hex')||
        convert_to(sha256,'UTF8')||decode('0a','hex'),
        ''::bytea order by filename collate "C"),''::bytea),'sha256'),'hex')
      into live_count,live_highest,live_digest from public.schema_migrations;
    if new.schema_applied_count is null
       or new.schema_highest_migration is distinct from live_highest
       or new.schema_applied_count<>live_count
       or new.schema_ledger_sha256 is distinct from live_digest then
      raise exception
        'Production release % schema declaration does not match live ops schema truth',
        new.release_key
        using detail = jsonb_build_object(
          'release_key',new.release_key,
          'declared_schema_highest_migration',new.schema_highest_migration,
          'declared_schema_applied_count',new.schema_applied_count,
          'declared_schema_ledger_sha256',new.schema_ledger_sha256,
          'live_schema_highest_migration',live_highest,
          'live_schema_applied_count',live_count,
          'live_schema_ledger_sha256',live_digest,
          'evidence_source','public.schema_migrations')::text;
    end if;
  end if;
  return new;
end $$;

create or replace function ops.deployment_requires_a_live_approval()
returns trigger language plpgsql as $$
declare rel ops.release%rowtype; receipt ops.release_readiness_receipt%rowtype;
begin
  if new.environment<>'production' then return new; end if;
  if new.release_id is null then raise exception 'Production deployment requires a release'; end if;
  select * into rel from ops.release where id=new.release_id;
  if not found or rel.environment is distinct from new.environment
     or rel.git_sha is distinct from new.git_sha
     or rel.provider is distinct from new.provider
     or rel.provider_version_id is distinct from new.provider_version_id then
    raise exception 'Production deployment differs from exact release identity';
  end if;
  if rel.readiness_receipt_id is not null then
    select * into receipt from ops.release_readiness_receipt
      where id=rel.readiness_receipt_id;
    if rel.state not in ('ready','deploying','verifying','complete')
       or not found or receipt.release_id<>rel.id
       or receipt.git_sha<>rel.git_sha or receipt.provider<>rel.provider
       or receipt.provider_version_id<>rel.provider_version_id
       or receipt.plan_hash<>rel.plan_hash
       or (rel.state<>'complete'
           and not ops.release_technical_readiness_current(rel.id)) then
      raise exception 'Production deployment lacks exact technical readiness';
    end if;
  elsif rel.state not in ('approved','deploying','verifying','complete')
     or rel.approval_expires_at is null or rel.approval_expires_at<=now() then
    raise exception 'Production deployment lacks live historical approval';
  end if;
  return new;
end $$;

create or replace function ops.release_completion_requires_a_read_back()
returns trigger language plpgsql as $$
begin
  if new.state='complete' and (tg_op='INSERT' or old.state is distinct from 'complete') then
    if not exists(select 1 from ops.deployment d where d.release_id=new.id
      and d.service_id=new.service_id and d.environment='production'
      and d.state='complete' and d.read_back_at is not null
      and d.git_sha=new.git_sha and d.provider=new.provider
      and d.provider_version_id=new.provider_version_id) then
      raise exception 'release % has no exact Production read-back',new.release_key;
    end if;
    if not exists(select 1 from ops.run r join ops.deployment d
      on d.release_id=r.release_id and d.service_id=r.service_id
      and d.correlation_id=r.correlation_id where r.release_id=new.id
      and r.service_id=new.service_id and r.environment='production'
      and r.run_key like 'performance.%' and r.state='succeeded'
      and r.evidence_ref is not null and r.budget_ms=new.performance_budget_ms
      and r.duration_ms>0 and r.duration_ms<=r.budget_ms
      and d.environment='production' and d.state='complete'
      and d.read_back_at is not null and d.git_sha=new.git_sha
      and d.provider=new.provider and d.provider_version_id=new.provider_version_id) then
      raise exception 'release % has no within-budget Production performance receipt',new.release_key;
    end if;
    if ops.program5_exact_recovery_rehearsal(new.id,
      coalesce(new.ready_at,new.approved_at)-interval '24 hours') is null then
      raise exception 'release % has no fresh exact typed recovery bundle',new.release_key;
    end if;
  end if;
  return new;
end $$;
