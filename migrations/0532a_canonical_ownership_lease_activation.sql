-- WR-000126 / PLAN-660624b07e83-v1
-- Activate the byte-identical 0450 ownership kernel through the existing
-- authenticated Worker principal, add immutable same-request accepted-plan
-- successors, and permanently fence the abandoned WR122 contract.

-- The abandoned WR122 proposal remains readable history.  These rows are a
-- deny-list, not a deletion list, and the generation is never reusable.
create table ops.engineering_stale_contract_fence (
  generation bigint primary key check (generation > 0),
  work_request_ref text not null,
  plan_ref text not null unique,
  plan_hash text not null unique check (plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  migration_filenames text[] not null,
  successor_allowed boolean not null default false,
  reason text not null check (btrim(reason) <> ''),
  created_at timestamptz not null default now(),
  check (cardinality(migration_filenames) > 0)
);

insert into ops.engineering_stale_contract_fence(
  generation,work_request_ref,plan_ref,plan_hash,migration_filenames,successor_allowed,reason)
values (1,'WR-000122','PLAN-954b03dd464d-v1',
  'sha256:954b03dd464dab9986bc705d01fdb197edd29b23ad10ca673da53ad0ac03c27d',
  array['0535_ready_plan_amendment.sql','0536_ready_plan_amendment_scac_successor.sql'],false,
  'Superseded through WR-000125 and its accepted WR-000126 replacement authority; historical rows remain exact but are permanently non-executable.');

insert into ops.engineering_stale_contract_fence(
  generation,work_request_ref,plan_ref,plan_hash,migration_filenames,successor_allowed,reason)
values (2,'WR-000120','PLAN-339b2976b387-v1',
  'sha256:339b2976b3876fe56291f0e7ba6732d8448b7539779f4d71a2e1891f2a2ad5ca',
  array['0533_read_doc_outcome_cards.sql','0534_read_doc_outcome_cards_scac_successor.sql'],true,
  'Frozen predecessor plan must return only through a separately proposed, reviewed, and accepted same-request successor; its burned filenames are permanently non-executable.');

create or replace function ops.engineering_stale_contract_fence_immutable()
returns trigger language plpgsql set search_path=pg_catalog,ops
as $$
begin
  raise exception 'engineering stale-contract fences are append-only';
end $$;

create trigger engineering_stale_contract_fence_immutable
before update or delete on ops.engineering_stale_contract_fence
for each row execute function ops.engineering_stale_contract_fence_immutable();

create or replace function ops.engineering_contract_stale(
  p_plan_ref text,p_plan_hash text,p_migration_filename text default null
) returns boolean language sql stable security definer
set search_path=pg_catalog,ops
as $$
  select exists (
    select 1 from ops.engineering_stale_contract_fence f
     where (p_plan_ref is not null and f.plan_ref=p_plan_ref)
        or (p_plan_hash is not null and f.plan_hash=p_plan_hash)
        or (p_migration_filename is not null and p_migration_filename=any(f.migration_filenames))
  );
$$;

create or replace function ops.reject_stale_engineering_migration()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops
as $$
begin
  if ops.engineering_contract_stale(null,null,new.filename) then
    raise exception using errcode='55000',
      message='stale engineering migration filename is permanently non-executable',
      detail=new.filename;
  end if;
  return new;
end $$;

do $stale_migration_preflight$
begin
  if exists(select 1 from public.schema_migrations m
    where ops.engineering_contract_stale(null,null,m.filename)) then
    raise exception 'a permanently fenced engineering migration filename is already present in the ledger';
  end if;
end $stale_migration_preflight$;

drop trigger if exists reject_stale_engineering_migration on public.schema_migrations;
create trigger reject_stale_engineering_migration
before insert on public.schema_migrations
for each row execute function ops.reject_stale_engineering_migration();

-- Put the permanent plan fence underneath every execution family.  The
-- predecessor implementations remain intact behind private names; callers see
-- the same result shapes plus a closed retired-plan refusal.
do $clone_engineering_currentness$
declare v_definition text; v_marker text:='FUNCTION ops.engineering_envelope_currentness(';
begin
  select pg_get_functiondef('ops.engineering_envelope_currentness(uuid,uuid)'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
    raise exception 'engineering currentness predecessor definition drifted';
  end if;
  execute replace(v_definition,v_marker,'FUNCTION ops.engineering_envelope_currentness_v1(');
end $clone_engineering_currentness$;

create or replace function ops.engineering_envelope_currentness(
  p_envelope_id uuid,p_job_id uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public
as $$
declare v_plan ops.sourced_work_request_plan%rowtype; v_result jsonb;
begin
  select p.* into v_plan from ops.engineering_execution_envelope e
    join ops.sourced_work_request_plan p on p.id=e.accepted_plan_id
   where e.id=p_envelope_id and e.job_id=p_job_id;
  if v_plan.id is not null then
    perform 1 from ops.work_request w where w.id=v_plan.work_request_id for share;
  end if;
  if v_plan.id is not null and ops.engineering_contract_stale(v_plan.plan_ref,v_plan.plan_hash,null) then
    return jsonb_build_object('eligible',false,'dispatch_runway_sufficient',false,
      'execution_authorized',false,'reason','accepted_plan_retired');
  end if;
  v_result:=ops.engineering_envelope_currentness_v1(p_envelope_id,p_job_id);
  return coalesce(v_result,'{}'::jsonb)||jsonb_build_object('execution_authorized',
    coalesce((v_result->>'eligible')::boolean,false));
end $$;

do $clone_ownership_currentness$
declare v_definition text; v_marker text:='FUNCTION ops.canonical_ownership_currentness(';
begin
  select pg_get_functiondef(
    'ops.canonical_ownership_currentness(uuid,integer,text,uuid,text,uuid,text,text)'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
    raise exception 'canonical ownership currentness predecessor definition drifted';
  end if;
  execute replace(v_definition,v_marker,'FUNCTION ops.canonical_ownership_currentness_v1(');
end $clone_ownership_currentness$;

create or replace function ops.canonical_ownership_currentness(
  p_work_request_id uuid,p_work_request_version integer,p_work_request_digest text,
  p_accepted_plan_id uuid,p_accepted_plan_digest text,p_slice_plan_id uuid,
  p_slice_plan_digest text,p_slice_ref text
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public
as $$
declare v_plan_ref text; v_plan_hash text;
begin
  perform 1 from ops.work_request w where w.id=p_work_request_id for share;
  select plan_ref,plan_hash into v_plan_ref,v_plan_hash
    from ops.sourced_work_request_plan where id=p_accepted_plan_id;
  if ops.engineering_contract_stale(v_plan_ref,coalesce(v_plan_hash,p_accepted_plan_digest),null) then
    return ops.canonical_ownership_refusal('WORK_REQUEST_BINDING_STALE','accepted_plan',
      '"current executable accepted plan"'::jsonb,
      jsonb_build_object('reason','accepted_plan_retired','value_redacted',true));
  end if;
  return ops.canonical_ownership_currentness_v1(p_work_request_id,p_work_request_version,
    p_work_request_digest,p_accepted_plan_id,p_accepted_plan_digest,p_slice_plan_id,
    p_slice_plan_digest,p_slice_ref);
end $$;

-- The predecessor projected the only acceptance row and therefore had no
-- version predicate.  With append-only successors it must resolve by the Work
-- Request's exact state version.  Rebuild that otherwise-byte-identical body
-- from PostgreSQL's own parsed definition and refuse if the one reviewed seam
-- is not found exactly once.
do $source_merge_successor$
declare v_definition text; v_marker text:='where x.work_request_id=w.id;';
        v_name_marker text:='FUNCTION ops.source_merge_authority_projection('; v_old text; v_new text;
begin
  select pg_get_functiondef('ops.source_merge_authority_projection(uuid,text,text,integer)'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,v_name_marker,'')))/length(v_name_marker)<>1
     or (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
    raise exception 'source-merge predecessor acceptance lookup shape drifted';
  end if;
  v_definition:=replace(v_definition,v_name_marker,
    'FUNCTION ops.source_merge_authority_projection_v1(');
  v_definition:=replace(v_definition,v_marker,
    'where x.work_request_id=w.id and x.result_version=w.version;');
  for v_old,v_new in select * from (values
    ($old$decision.title is distinct from 'Routine authorized green PRs merge without asking Joe for ceremonial approval'$old$,
     $new$decision.title is distinct from 'Gate A2 source-merge authority: '||work_ref$new$),
    ($old$and e.subject_id=p_decision_id$old$,
     $new$and e.subject_id=p_decision_id
     and e.authorization_class='human' and e.new_value->'quote_absent'='false'::jsonb
     and e.human_quote='I approve Gate A2 for '||work_ref||' PR #'||p_pr_number::text||' at '||p_head_sha||' for 120 minutes'
     and e.occurred_at<=statement_timestamp() and e.occurred_at+interval '120 minutes'>statement_timestamp()$new$),
    ($old$and lease.state='active' and lease.expires_at>evaluated_at
     and lease.id=review_manifest.lease_id$old$,
     $new$and lease.state in ('active','released')
     and lease.id=review_manifest.lease_id$new$),
    ($old$and exists (select 1 from ops.assurance_execution_manifest bound_manifest
                      join ops.assurance_evidence_extension bound_evidence
                        on bound_evidence.manifest_id=bound_manifest.id
                     where bound_manifest.lease_id=lease.id
                       and bound_manifest.repository_commit_sha=p_head_sha
                       and bound_evidence.receipt_id in (
                         select bound_receipt.id from ops.engineering_slice_receipt bound_receipt
                          where bound_receipt.work_request_id=w.id
                            and bound_receipt.outcome='claimed_complete'))$old$,
     $new$and exists (select 1 from ops.canonical_ownership_runtime_session merge_session
                      join ops.canonical_ownership_merge_binding merge_binding
                        on merge_binding.runtime_binding_id=merge_session.id
                     where merge_session.ownership_session_ref=lease.holder_session_ref
                       and merge_session.phase='merge'
                       and merge_binding.decision_id=p_decision_id
                       and merge_binding.head_sha=p_head_sha and merge_binding.pr_number=p_pr_number
                       and ops.canonical_ownership_merge_binding_valid(merge_session.id))$new$)
  ) changes(old_text,new_text) loop
    if (length(v_definition)-length(replace(v_definition,v_old,'')))/length(v_old)<>1 then
      raise exception 'source-merge phase seam drifted';
    end if;
    v_definition:=replace(v_definition,v_old,v_new);
  end loop;
  execute v_definition;
end $source_merge_successor$;

create or replace function ops.source_merge_authority_projection(
  p_decision_id uuid,p_work_request text,p_head_sha text,p_pr_number integer
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public
as $$
declare v_stale boolean;
begin
  perform 1 from ops.work_request w where w.ref=p_work_request for share;
  select ops.engineering_contract_stale(p.plan_ref,p.plan_hash,null) into v_stale
    from ops.work_request w
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.work_request_id=w.id and ar.result_version=w.version
    join ops.sourced_work_request_plan p on p.id=ar.plan_id
   where w.ref=p_work_request;
  if coalesce(v_stale,false) then
    return jsonb_build_object('ok',false,'error','source_merge_accepted_plan_retired',
      'execution_authorized',false);
  end if;
  return ops.source_merge_authority_projection_v1(
    p_decision_id,p_work_request,p_head_sha,p_pr_number);
end $$;

-- -------------------------------------------------------------------------
-- Trusted transaction-local 0450 adapter binding.
-- -------------------------------------------------------------------------

-- Credentials are provisioned separately. Membership permits SET ROLE for
-- EXECUTE only; session_user remains the authenticated generation throughout.
do $issuer_roles$
declare r text;
begin
  if not exists(select 1 from pg_roles where rolname='carr_ownership_issuer') then
    create role carr_ownership_issuer nologin noinherit nobypassrls;
  end if;
  foreach r in array array['carr_ownership_issuer_g1','carr_ownership_issuer_g2'] loop
    if not exists(select 1 from pg_roles where rolname=r) then
      execute format('create role %I login noinherit nobypassrls',r);
    end if;
    if exists(select 1 from pg_roles where rolname=r and
      (not rolcanlogin or rolinherit or rolbypassrls or rolsuper or rolcreaterole or rolcreatedb)) then
      raise exception 'ownership issuer login has unsafe attributes: %',r;
    end if;
    execute format('grant carr_ownership_issuer to %I',r);
  end loop;
  if exists(select 1 from pg_roles where rolname='carr_ownership_issuer' and
    (rolcanlogin or rolbypassrls or rolsuper or rolcreaterole or rolcreatedb)) then
    raise exception 'ownership issuer capability role has unsafe attributes';
  end if;
end $issuer_roles$;

create table ops.canonical_ownership_issuer_generation (
  principal name primary key check (principal in ('carr_ownership_issuer_g1','carr_ownership_issuer_g2')),
  generation integer not null unique check (generation>0),
  state text not null check (state in ('active','draining')),
  changed_at timestamptz not null default clock_timestamp()
);
create unique index canonical_ownership_one_active_issuer
  on ops.canonical_ownership_issuer_generation(state) where state='active';
insert into ops.canonical_ownership_issuer_generation(principal,generation,state)
values ('carr_ownership_issuer_g1',1,'active'),('carr_ownership_issuer_g2',2,'draining');
revoke all on ops.canonical_ownership_issuer_generation from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_ownership_issuer;
grant usage on schema ops to carr_ownership_issuer;

create table ops.canonical_ownership_runtime_session (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  organization_tenant_id text not null check (btrim(organization_tenant_id)<>''),
  actor_id uuid not null references public.actor(id) on delete restrict,
  actor_slug text not null check (btrim(actor_slug)<>''),
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  accepted_plan_id uuid not null references ops.sourced_work_request_plan(id) on delete restrict,
  subject_envelope_id uuid not null references ops.engineering_execution_envelope(id) on delete restrict,
  attempt integer not null check (attempt > 0),
  runtime_session_ref text not null check (runtime_session_ref ~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$'),
  ownership_session_ref text not null unique check (ownership_session_ref ~ '^ownership:[0-9a-f-]{36}$'),
  execution_host_ref text not null check (execution_host_ref ~ '^cloudflare-workers:[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$'),
  issuer_principal name not null references ops.canonical_ownership_issuer_generation(principal),
  capability_token_hash text not null check (capability_token_hash ~ '^[0-9a-f]{64}$'),
  phase text not null default 'build' check (phase in ('build','merge')),
  state text not null default 'active' check (state in ('active','expired','replaced')),
  issued_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null,
  replaced_by_id uuid references ops.canonical_ownership_runtime_session(id) on delete restrict,
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  check (expires_at > issued_at),
  check ((state='replaced')=(replaced_by_id is not null))
);

create index canonical_ownership_runtime_session_active_idx
  on ops.canonical_ownership_runtime_session(organization_tenant_id,ownership_session_ref,expires_at)
  where state='active';
create unique index canonical_ownership_runtime_one_build_attempt
  on ops.canonical_ownership_runtime_session(subject_envelope_id,attempt)
  where state='active' and phase='build';

create table ops.canonical_ownership_merge_binding (
  runtime_binding_id uuid primary key references ops.canonical_ownership_runtime_session(id),
  decision_id uuid not null,
  decision_event_id uuid not null references public.event(id),
  receipt_id uuid not null references ops.engineering_slice_receipt(id),
  scope_id uuid not null references ops.source_merge_plan_scope(id),
  head_sha text not null check (head_sha ~ '^[0-9a-f]{40}$'),
  pr_number integer not null check (pr_number>0),
  capability_token uuid not null unique,
  decision_expires_at timestamptz not null,
  unique(decision_id,head_sha,pr_number)
);
revoke all on ops.canonical_ownership_merge_binding from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_ownership_issuer;

create or replace function ops.canonical_ownership_merge_binding_valid(p_binding_id uuid)
returns boolean language sql stable security definer set search_path=pg_catalog,ops,public
as $$
  select exists(select 1 from ops.canonical_ownership_runtime_session rs
    join ops.canonical_ownership_merge_binding m on m.runtime_binding_id=rs.id
    join ops.work_request w on w.id=rs.work_request_id and w.organization_tenant_id=rs.organization_tenant_id
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.work_request_id=w.id and ar.result_version=w.version and ar.plan_id=rs.accepted_plan_id
    join ops.sourced_work_request_plan p on p.id=ar.plan_id and p.plan_hash=ar.plan_hash
    join ops.source_merge_plan_scope scope on scope.id=m.scope_id and scope.accepted_plan_id=p.id
      and scope.work_request_id=w.id and scope.acceptance_receipt_id=ar.id
    join ops.engineering_execution_envelope env on env.id=rs.subject_envelope_id
      and env.accepted_plan_id=p.id and env.work_request_id=w.id and env.state_version=w.version
    join ops.engineering_slice_receipt receipt on receipt.id=m.receipt_id and receipt.envelope_id=env.id
      and receipt.outcome='claimed_complete' and receipt.receipt#>>'{source_evidence,source_sha}'=m.head_sha
    join public.event ev on ev.id=m.decision_event_id and ev.subject_id=m.decision_id
    join public.actor a on a.id=rs.actor_id and a.slug=rs.actor_slug and a.active
   where rs.id=p_binding_id and rs.phase='merge' and rs.state='active'
     and rs.expires_at>statement_timestamp() and m.decision_expires_at>=rs.expires_at
     and w.state='ready' and w.blocker_code is null
     and not ops.engineering_contract_stale(p.plan_ref,p.plan_hash,null)
     and not exists(select 1 from ops.engineering_execution_envelope successor where successor.supersedes_envelope_id=env.id)
     and ev.verb='log-decision' and ev.subject_type='decision'
     and ev.organization_tenant_id=rs.organization_tenant_id
     and ev.sponsoring_human_slug='joe' and ev.authorization_class='human'
     and ev.new_value->>'title'='Gate A2 source-merge authority: '||w.ref
     and ev.new_value->'quote_absent'='false'::jsonb
     and ev.human_quote='I approve Gate A2 for '||w.ref||' PR #'||m.pr_number::text||' at '||m.head_sha||' for 120 minutes'
     and ev.occurred_at<=statement_timestamp()
     and m.decision_expires_at=ev.occurred_at+interval '120 minutes');
$$;

create or replace function ops.canonical_ownership_runtime_principal_valid()
returns boolean language sql stable security definer set search_path=pg_catalog,ops
as $$
  select exists(select 1 from ops.canonical_ownership_issuer_generation g
       join pg_roles r on r.rolname=g.principal
      where g.principal=session_user and g.state in ('active','draining')
        and r.rolcanlogin and not r.rolinherit and not r.rolbypassrls and not r.rolsuper
        and not r.rolcreaterole and not r.rolcreatedb)
     and pg_has_role(session_user,'carr_ownership_issuer','member')
     and not pg_has_role(session_user,'carr_writer','member')
     and not pg_has_role(session_user,'carr_jobs','member')
     and not pg_has_role(session_user,'carr_authority','member');
$$;

create or replace function ops.mint_canonical_ownership_runtime_session(
  p_work_request_id uuid,p_accepted_plan_id uuid,p_subject_envelope_id uuid,
  p_attempt integer,p_runtime_session_ref text,p_execution_host_ref text,
  p_expires_at timestamptz,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public
as $$
declare v_tenant text:=nullif(btrim(current_setting('carr.organization_tenant_id',true)),'');
        v_actor_slug text:=nullif(btrim(current_setting('carr.acting_actor_slug',true)),'');
        v_runtime_context text:=nullif(btrim(current_setting('carr.receipt_session_ref',true)),'');
        v_host_context text:=nullif(btrim(current_setting('carr.execution_host_id',true)),'');
        v_capability text:=nullif(current_setting('carr.engineering_job_lease_token',true),'');
        v_actor public.actor%rowtype; v_work ops.work_request%rowtype;
        v_plan ops.sourced_work_request_plan%rowtype;
        v_env ops.engineering_execution_envelope%rowtype;
        v_job ops.job%rowtype; v_agent_session ops.capability_agent_session%rowtype;
        v_row ops.canonical_ownership_runtime_session%rowtype; v_id uuid;
begin
  if not ops.canonical_ownership_runtime_principal_valid() then
    return jsonb_build_object('ok',false,'reason_id','ownership_runtime_principal_untrusted');
  end if;
  if v_tenant is null or v_actor_slug is null or v_runtime_context is null
     or v_host_context is null or v_capability is null then
    return jsonb_build_object('ok',false,'reason_id','ownership_runtime_identity_missing');
  end if;
  if p_idempotency_key is null or p_attempt is null or p_attempt<1
     or coalesce(p_runtime_session_ref,'') !~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$'
     or coalesce(p_execution_host_ref,'') !~ '^cloudflare-workers:[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$'
     or p_runtime_session_ref is distinct from v_runtime_context
     or p_execution_host_ref is distinct from v_host_context
     or p_expires_at is null or p_expires_at<=clock_timestamp()
     or p_expires_at>clock_timestamp()+interval '2 hours' then
    return jsonb_build_object('ok',false,'reason_id','ownership_runtime_binding_invalid');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('canonical-ownership-runtime:'||p_idempotency_key::text,0));
  select * into v_row from ops.canonical_ownership_runtime_session
   where idempotency_key=p_idempotency_key for share;
  if found then
    if (v_row.work_request_id,v_row.accepted_plan_id,v_row.subject_envelope_id,v_row.attempt,
        v_row.runtime_session_ref,v_row.execution_host_ref,v_row.expires_at)
       is distinct from
       (p_work_request_id,p_accepted_plan_id,p_subject_envelope_id,p_attempt,
        p_runtime_session_ref,p_execution_host_ref,p_expires_at) then
      return jsonb_build_object('ok',false,'reason_id','ownership_runtime_idempotency_conflict');
    end if;
    if v_row.phase<>'build' or v_row.organization_tenant_id is distinct from v_tenant
       or v_row.actor_slug is distinct from v_actor_slug
       or v_row.issuer_principal is distinct from session_user
       or v_row.capability_token_hash is distinct from encode(public.digest(v_capability,'sha256'),'hex')
       or v_row.state<>'active' or v_row.expires_at<=clock_timestamp()
       or not exists(select 1 from ops.engineering_execution_envelope e
         join ops.job j on j.id=e.job_id
         join ops.capability_agent_session s on s.id=e.agent_session_id
         where e.id=v_row.subject_envelope_id and j.attempt=v_row.attempt and j.state='running'
           and j.lease_token::text=v_capability and j.leased_until>clock_timestamp()
           and s.executor_actor_id=v_row.actor_id and s.state in ('claimed','in_progress')
           and s.lease_expires_at>clock_timestamp())
       or not exists(
         select 1 from ops.work_request w
         join ops.sourced_work_request_plan_acceptance_receipt ar
           on ar.work_request_id=w.id and ar.result_version=w.version
          where w.id=v_row.work_request_id and w.state='ready'
            and ar.plan_id=v_row.accepted_plan_id)
       or coalesce((select (ops.engineering_envelope_currentness(
            e.id,e.job_id)->>'eligible')::boolean
          from ops.engineering_execution_envelope e
         where e.id=v_row.subject_envelope_id),false) is not true then
      return jsonb_build_object('ok',false,'reason_id','ownership_runtime_binding_stale');
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'binding_id',v_row.id,
      'ownership_session_ref',v_row.ownership_session_ref,
      'organization_tenant_id',v_row.organization_tenant_id,
      'acting_actor_slug',v_row.actor_slug,'execution_host_ref',v_row.execution_host_ref,
      'expires_at',v_row.expires_at);
  end if;
  if not exists(select 1 from ops.canonical_ownership_issuer_generation
      where principal=session_user and state='active') then
    return jsonb_build_object('ok',false,'reason_id','ownership_issuer_draining');
  end if;
  select * into v_actor from public.actor where slug=v_actor_slug and active for share;
  select * into v_work from ops.work_request where id=p_work_request_id for update;
  select * into v_plan from ops.sourced_work_request_plan where id=p_accepted_plan_id for share;
  select * into v_env from ops.engineering_execution_envelope where id=p_subject_envelope_id for share;
  select * into v_job from ops.job where id=v_env.job_id for share;
  select * into v_agent_session from ops.capability_agent_session
    where id=v_env.agent_session_id for share;
  if v_actor.id is null or v_work.id is null or v_plan.id is null or v_env.id is null
     or v_job.id is null or v_agent_session.id is null
     or v_work.organization_tenant_id is distinct from v_tenant
     or v_work.state<>'ready' or v_plan.work_request_id<>v_work.id
     or v_env.work_request_id<>v_work.id or v_env.accepted_plan_id<>v_plan.id
     or v_env.state_version<>v_work.version or v_env.expires_at<=clock_timestamp()
     or p_expires_at>v_env.expires_at
     or v_agent_session.work_request_id<>v_work.id
     or v_actor.id is distinct from v_agent_session.executor_actor_id
     or p_runtime_session_ref is distinct from 'session:'||v_agent_session.id::text
     or v_agent_session.state not in ('claimed','in_progress')
     or v_agent_session.lease_expires_at is null
     or v_agent_session.lease_expires_at<=clock_timestamp()
     or p_expires_at>v_agent_session.lease_expires_at
     or ops.engineering_contract_stale(v_plan.plan_ref,v_plan.plan_hash,null)
     or not exists (
       select 1 from ops.sourced_work_request_plan_acceptance_receipt ar
        where ar.work_request_id=v_work.id and ar.plan_id=v_plan.id
          and ar.plan_hash=v_plan.plan_hash and ar.result_version=v_work.version)
     or v_job.attempt<>p_attempt or v_job.state<>'running'
     or v_job.lease_token::text is distinct from v_capability
     or v_job.leased_until is null or v_job.leased_until<=clock_timestamp()
     or p_expires_at>v_job.leased_until
     or coalesce((ops.engineering_envelope_currentness(v_env.id,v_job.id)->>'eligible')::boolean,false)
        is not true then
    return jsonb_build_object('ok',false,'reason_id','ownership_runtime_binding_stale');
  end if;
  update ops.canonical_ownership_runtime_session set state='expired'
    where subject_envelope_id=v_env.id and attempt=p_attempt and phase='build'
      and state='active' and expires_at<=clock_timestamp();
  if exists(select 1 from ops.canonical_ownership_runtime_session
      where subject_envelope_id=v_env.id and attempt=p_attempt and phase='build' and state='active') then
    return jsonb_build_object('ok',false,'reason_id','ownership_runtime_binding_already_exists');
  end if;
  v_id:=gen_random_uuid();
  insert into ops.canonical_ownership_runtime_session(
    id,idempotency_key,organization_tenant_id,actor_id,actor_slug,work_request_id,
    accepted_plan_id,subject_envelope_id,attempt,runtime_session_ref,
    ownership_session_ref,execution_host_ref,expires_at,issuer_principal,capability_token_hash)
  values(v_id,p_idempotency_key,v_tenant,v_actor.id,v_actor.slug,v_work.id,v_plan.id,
    v_env.id,p_attempt,p_runtime_session_ref,'ownership:'||v_id::text,
    p_execution_host_ref,p_expires_at,session_user,encode(public.digest(v_capability,'sha256'),'hex'))
  returning * into v_row;
  return jsonb_build_object('ok',true,'replayed',false,'binding_id',v_row.id,
    'ownership_session_ref',v_row.ownership_session_ref,
    'organization_tenant_id',v_row.organization_tenant_id,
    'acting_actor_slug',v_row.actor_slug,'execution_host_ref',v_row.execution_host_ref,
    'expires_at',v_row.expires_at);
end $$;

create or replace function ops.authenticated_canonical_ownership_controller_binding(
  p_job_id uuid,p_lease_token uuid,p_worker text,p_operation text
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare e ops.engineering_execution_envelope%rowtype; j ops.job%rowtype;
  bound jsonb; candidates integer; s ops.capability_agent_session%rowtype;
  sp ops.engineering_slice_plan%rowtype; a public.actor%rowtype;
begin
  if not ops.canonical_ownership_runtime_principal_valid()
     or p_job_id is null or p_lease_token is null or nullif(btrim(p_worker),'') is null
     or p_operation is null or p_operation not in ('acquire','check','renew','release') then return null; end if;
  select count(*) into candidates from ops.engineering_execution_envelope env
    where env.job_id=p_job_id and not exists(select 1 from ops.engineering_execution_envelope successor
      where successor.supersedes_envelope_id=env.id);
  if candidates<>1 then return null; end if;
  select * into e from ops.engineering_execution_envelope env where env.job_id=p_job_id
    and not exists(select 1 from ops.engineering_execution_envelope successor where successor.supersedes_envelope_id=env.id);
  if p_operation='acquire' then
    bound:=ops.engineering_controller_binding(e.id,p_job_id,p_lease_token);
    if bound is null then return null; end if;
  else
    perform ops.canonical_ownership_lock_lineage(null,e.slice_plan_id,array[e.slice_ref]);
    select * into s from ops.capability_agent_session where id=e.agent_session_id for share;
    select * into a from public.actor where id=s.executor_actor_id and active and kind='automation' and slug='codex' for share;
    select * into sp from ops.engineering_slice_plan where id=e.slice_plan_id for share;
    if s.id is null or a.id is null or sp.id is null or s.state not in ('claimed','in_progress')
       or s.lease_expires_at is null or s.lease_expires_at<=clock_timestamp() or e.expires_at<=clock_timestamp()
       or not coalesce((ops.engineering_envelope_currentness(e.id,p_job_id)->>'eligible')::boolean,false) then return null; end if;
    bound:=jsonb_build_object('envelope_id',e.id,'envelope_digest',e.envelope_digest,
      'slice_ref',e.slice_ref,'plan_digest',sp.plan_digest,'slice_plan',sp.plan,
      'executor_actor',jsonb_build_object('id',a.id,'slug',a.slug),'agent_session_id',s.id,
      'agent_session_lease_expires_at',s.lease_expires_at);
  end if;
  select * into j from ops.job where id=p_job_id for share;
  if not found or j.definition_key<>'engineering-slice' or j.state<>'running'
     or j.lease_token is distinct from p_lease_token or j.lease_owner is distinct from p_worker
     or j.leased_until<=clock_timestamp() then return null; end if;
  return jsonb_build_object('envelope_id',e.id,'work_request_id',e.work_request_id,
    'accepted_plan_id',e.accepted_plan_id,'slice_plan_id',e.slice_plan_id,
    'state_version',e.state_version,'canonical_record_digest',e.canonical_record_digest,
    'expires_at',e.expires_at,
    'runtime_expires_at',least(e.expires_at,j.leased_until,(bound->>'agent_session_lease_expires_at')::timestamptz),
    'attempt',j.attempt,'agent_session_id',e.agent_session_id,'binding',bound);
end $$;

create or replace function ops.canonical_ownership_trusted_context()
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare v_tenant text:=nullif(btrim(current_setting('carr.organization_tenant_id',true)),'');
        v_actor text:=nullif(btrim(current_setting('carr.acting_actor_slug',true)),'');
        v_runtime text:=nullif(btrim(current_setting('carr.receipt_session_ref',true)),'');
        v_session text:=nullif(btrim(current_setting('carr.ownership_session_id',true)),'');
        v_host text:=nullif(btrim(current_setting('carr.execution_host_id',true)),'');
        v_capability text:=nullif(current_setting('carr.engineering_job_lease_token',true),'');
        v_row ops.canonical_ownership_runtime_session%rowtype; v_job_id uuid;
begin
  if not ops.canonical_ownership_runtime_principal_valid() then
    return jsonb_build_object('ok',false,'reason_id','ownership_runtime_principal_untrusted');
  end if;
  if v_tenant is null or v_actor is null or v_runtime is null
     or v_session is null or v_host is null or v_capability is null then
    return jsonb_build_object('ok',false,'reason_id','ownership_runtime_context_missing');
  end if;
  select * into v_row from ops.canonical_ownership_runtime_session
   where organization_tenant_id=v_tenant and ownership_session_ref=v_session;
  select job_id into v_job_id from ops.engineering_execution_envelope
   where id=v_row.subject_envelope_id;
  if not found or v_row.state<>'active' or v_row.expires_at<=clock_timestamp()
     or v_row.actor_slug<>v_actor or v_row.runtime_session_ref<>v_runtime
     or v_row.execution_host_ref<>v_host
     or v_row.issuer_principal is distinct from session_user
     or v_row.capability_token_hash is distinct from encode(public.digest(v_capability,'sha256'),'hex')
     or (v_row.phase='build' and not exists(select 1 from ops.engineering_execution_envelope e
       join ops.capability_agent_session s on s.id=e.agent_session_id
       join ops.job j on j.id=e.job_id
       join public.actor a on a.id=s.executor_actor_id and a.active
       where e.id=v_row.subject_envelope_id and s.executor_actor_id=v_row.actor_id
         and v_row.runtime_session_ref='session:'||s.id::text
         and s.state in ('claimed','in_progress') and s.lease_expires_at>clock_timestamp()
         and j.state='running' and j.attempt=v_row.attempt
         and j.lease_token::text=v_capability and j.leased_until>clock_timestamp()))
     or (v_row.phase='merge' and not ops.canonical_ownership_merge_binding_valid(v_row.id))
     or not exists(
       select 1 from ops.work_request w
       join ops.sourced_work_request_plan_acceptance_receipt ar
         on ar.work_request_id=w.id and ar.result_version=w.version
       join ops.sourced_work_request_plan p on p.id=ar.plan_id
        where w.id=v_row.work_request_id and w.state='ready'
          and ar.plan_id=v_row.accepted_plan_id and ar.plan_hash=p.plan_hash
          and not ops.engineering_contract_stale(p.plan_ref,p.plan_hash,null))
     or (v_row.phase='build' and coalesce((ops.engineering_envelope_currentness(
          v_row.subject_envelope_id,v_job_id)->>'eligible')::boolean,false) is not true) then
    return jsonb_build_object('ok',false,'reason_id','ownership_runtime_context_stale');
  end if;
  return jsonb_build_object('ok',true,'organization_tenant_id',v_tenant,
    'acting_actor_id',v_row.actor_id,'acting_actor_slug',v_actor,'ownership_session_ref',v_session,
    'execution_host_ref',v_host,'binding_id',v_row.id,
    'work_request_id',v_row.work_request_id,'accepted_plan_id',v_row.accepted_plan_id,
    'subject_envelope_id',v_row.subject_envelope_id,'attempt',v_row.attempt,
    'runtime_session_ref',v_row.runtime_session_ref,'phase',v_row.phase,'expires_at',v_row.expires_at);
end $$;

create or replace function ops.mint_canonical_ownership_merge_session(
  p_subject_envelope_id uuid,p_decision_id uuid,p_head_sha text,p_pr_number integer,
  p_execution_host_ref text,p_expires_at timestamptz,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare tenant text:=nullif(current_setting('carr.organization_tenant_id',true),'');
  actor_slug text:=nullif(current_setting('carr.acting_actor_slug',true),'');
  host text:=nullif(current_setting('carr.execution_host_id',true),'');
  e ops.engineering_execution_envelope%rowtype; w ops.work_request%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  scope ops.source_merge_plan_scope%rowtype; receipt ops.engineering_slice_receipt%rowtype;
  ev public.event%rowtype; a public.actor%rowtype;
  rs ops.canonical_ownership_runtime_session%rowtype; m ops.canonical_ownership_merge_binding%rowtype;
  v_id uuid; capability uuid;
begin
  if not ops.canonical_ownership_runtime_principal_valid() then
    return jsonb_build_object('ok',false,'reason_id','ownership_merge_issuer_untrusted');
  end if;
  if tenant is null or actor_slug is null or host is null or p_execution_host_ref is distinct from host
     or host !~ '^cloudflare-workers:[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$'
     or p_idempotency_key is null or coalesce(p_head_sha,'') !~ '^[0-9a-f]{40}$'
     or p_pr_number is null or p_pr_number<1 or p_expires_at is null or p_expires_at<=clock_timestamp() then
    return jsonb_build_object('ok',false,'reason_id','ownership_merge_input_invalid');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('canonical-ownership-runtime:'||p_idempotency_key::text,0));
  select * into rs from ops.canonical_ownership_runtime_session where idempotency_key=p_idempotency_key;
  if found then
    select * into m from ops.canonical_ownership_merge_binding where runtime_binding_id=rs.id;
    if rs.phase<>'merge' or rs.issuer_principal is distinct from session_user
       or (rs.organization_tenant_id,rs.actor_slug,rs.subject_envelope_id,rs.execution_host_ref,rs.expires_at,
           m.decision_id,m.head_sha,m.pr_number) is distinct from
          (tenant,actor_slug,p_subject_envelope_id,host,p_expires_at,p_decision_id,p_head_sha,p_pr_number) then
      return jsonb_build_object('ok',false,'reason_id','ownership_merge_idempotency_conflict');
    end if;
    if not ops.canonical_ownership_merge_binding_valid(rs.id) then
      return jsonb_build_object('ok',false,'reason_id','ownership_merge_binding_stale');
    end if;
  else
    if not exists(select 1 from ops.canonical_ownership_issuer_generation where principal=session_user and state='active') then
      return jsonb_build_object('ok',false,'reason_id','ownership_issuer_draining');
    end if;
    select * into e from ops.engineering_execution_envelope where id=p_subject_envelope_id for share;
    select * into w from ops.work_request where id=e.work_request_id and organization_tenant_id=tenant for update;
    select * into ar from ops.sourced_work_request_plan_acceptance_receipt
     where work_request_id=w.id and result_version=w.version and plan_id=e.accepted_plan_id;
    select * into scope from ops.source_merge_plan_scope where work_request_id=w.id
      and accepted_plan_id=e.accepted_plan_id and acceptance_receipt_id=ar.id;
    select * into receipt from ops.engineering_slice_receipt where envelope_id=e.id
      and outcome='claimed_complete' and receipt#>>'{source_evidence,source_sha}'=p_head_sha;
    select * into ev from public.event where subject_type='decision' and verb='log-decision'
      and subject_id=p_decision_id order by recorded_at desc,id desc limit 1;
    select * into a from public.actor where slug=actor_slug and active;
    if e.id is null or w.id is null or ar.id is null or scope.id is null or receipt.id is null or a.id is null
       or ev.id is null or ev.organization_tenant_id is distinct from tenant
       or ev.sponsoring_human_slug is distinct from 'joe'
       or ev.authorization_class is distinct from 'human'
       or ev.new_value->>'title' is distinct from 'Gate A2 source-merge authority: '||w.ref
       or ev.new_value->'quote_absent' is distinct from 'false'::jsonb
       or ev.human_quote is distinct from 'I approve Gate A2 for '||w.ref||' PR #'||p_pr_number::text||' at '||p_head_sha||' for 120 minutes'
       or ev.occurred_at>clock_timestamp() or p_expires_at>ev.occurred_at+interval '120 minutes'
       or w.state<>'ready' or w.blocker_code is not null or e.state_version<>w.version
       or ops.engineering_contract_stale(null,ar.plan_hash,null)
       or exists(select 1 from ops.engineering_execution_envelope successor where successor.supersedes_envelope_id=e.id) then
      return jsonb_build_object('ok',false,'reason_id','ownership_merge_authority_invalid');
    end if;
    v_id:=gen_random_uuid(); capability:=gen_random_uuid();
    insert into ops.canonical_ownership_runtime_session(id,idempotency_key,organization_tenant_id,actor_id,actor_slug,
      work_request_id,accepted_plan_id,subject_envelope_id,attempt,runtime_session_ref,ownership_session_ref,
      execution_host_ref,issuer_principal,capability_token_hash,phase,expires_at)
    values(v_id,p_idempotency_key,tenant,a.id,a.slug,w.id,e.accepted_plan_id,e.id,split_part(receipt.attempt_id,':',2)::integer,
      'session:merge:'||v_id::text,'ownership:'||v_id::text,host,session_user,
      encode(public.digest(capability::text,'sha256'),'hex'),'merge',p_expires_at) returning * into rs;
    insert into ops.canonical_ownership_merge_binding(runtime_binding_id,decision_id,decision_event_id,
      receipt_id,scope_id,head_sha,pr_number,capability_token,decision_expires_at)
    values(v_id,p_decision_id,ev.id,receipt.id,scope.id,p_head_sha,p_pr_number,capability,ev.occurred_at+interval '120 minutes')
    returning * into m;
  end if;
  return jsonb_build_object('ok',true,'binding_id',rs.id,'phase','merge',
    'ownership_session_ref',rs.ownership_session_ref,'runtime_session_ref',rs.runtime_session_ref,
    'organization_tenant_id',rs.organization_tenant_id,'acting_actor_slug',rs.actor_slug,
    'execution_host_ref',rs.execution_host_ref,'expires_at',rs.expires_at,
    'merge_capability_token',m.capability_token,'decision_id',m.decision_id,
    'head_sha',m.head_sha,'pr_number',m.pr_number);
end $$;

do $clone_ownership_context$
declare v_definition text; v_marker text:='FUNCTION ops.canonical_ownership_context(';
begin
  select pg_get_functiondef('ops.canonical_ownership_context()'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
    raise exception 'canonical ownership context predecessor definition drifted';
  end if;
  execute replace(v_definition,v_marker,'FUNCTION ops.canonical_ownership_context_v1(');
end $clone_ownership_context$;

create or replace function ops.canonical_ownership_context()
returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public
as $$
declare v_context jsonb:=ops.canonical_ownership_trusted_context();
begin
  if not coalesce((v_context->>'ok')::boolean,false) then
    return ops.canonical_ownership_refusal('IDENTITY_CONTEXT_INVALID','identity_context',
      '"active database-minted runtime binding"'::jsonb,
      jsonb_build_object('reason',coalesce(v_context->>'reason_id','binding_invalid'),
        'value_redacted',true));
  end if;
  return jsonb_build_object('ok',true,
    'tenant',v_context->>'organization_tenant_id',
    'actor_id',v_context->>'acting_actor_id',
    'actor_slug',v_context->>'acting_actor_slug',
    'session_ref',v_context->>'ownership_session_ref',
    'host_ref',v_context->>'execution_host_ref');
end $$;

-- -------------------------------------------------------------------------
-- Same-Work-Request ready-plan successor lifecycle.
-- -------------------------------------------------------------------------

-- A request receipt binds every operation to its durable runtime identity.
-- Tokens never enter logs or audit events; the protected response allows the
-- same issuer to resolve an ambiguous commit without acquiring a second lease.
create table ops.canonical_ownership_operation (
  idempotency_key uuid primary key,
  runtime_binding_id uuid not null references ops.canonical_ownership_runtime_session(id),
  request_digest text not null,
  operation text not null check (operation in ('acquire','check','renew','release')),
  response jsonb not null,
  created_at timestamptz not null default clock_timestamp()
);
revoke all on ops.canonical_ownership_operation from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_ownership_issuer;

create or replace function ops.canonical_ownership_lifecycle(p_request jsonb)
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare ctx jsonb:=ops.canonical_ownership_trusted_context();
  rs ops.canonical_ownership_runtime_session%rowtype;
  e ops.engineering_execution_envelope%rowtype;
  sp ops.engineering_slice_plan%rowtype;
  prior ops.canonical_ownership_operation%rowtype;
  op text:=p_request->>'operation'; key uuid; digest text; result jsonb; keys text[];
  ttl integer; lease ops.canonical_ownership_lease%rowtype;
  scope ops.source_merge_plan_scope%rowtype; paths jsonb; resources jsonb; deps jsonb; slice jsonb;
begin
  if not coalesce((ctx->>'ok')::boolean,false) then return ctx; end if;
  keys:=case op
    when 'acquire' then array['operation','idempotency_key','ttl_seconds']
    when 'check' then array['operation','idempotency_key','lease_id','lease_token','fencing_generation']
    when 'renew' then array['operation','idempotency_key','lease_id','lease_token','fencing_generation','ttl_seconds']
    when 'release' then array['operation','idempotency_key','lease_id','lease_token','fencing_generation'] end;
  if keys is null or not coalesce(ops.engineering_receipt_exact_object(p_request,keys),false)
     or coalesce(p_request->>'idempotency_key','') !~ '^[0-9a-f-]{36}$' then
    return jsonb_build_object('ok',false,'reason_id','ownership_operation_invalid');
  end if;
  key:=(p_request->>'idempotency_key')::uuid;
  digest:=encode(public.digest(p_request::text,'sha256'),'hex');
  perform pg_advisory_xact_lock(hashtextextended('ownership-operation:'||key::text,0));
  select * into prior from ops.canonical_ownership_operation where idempotency_key=key;
  if found then
    if prior.runtime_binding_id is distinct from (ctx->>'binding_id')::uuid
       or prior.request_digest is distinct from digest then
      return jsonb_build_object('ok',false,'reason_id','ownership_operation_idempotency_conflict');
    end if;
    return prior.response||jsonb_build_object('replayed',true);
  end if;
  select * into rs from ops.canonical_ownership_runtime_session where id=(ctx->>'binding_id')::uuid;
  select * into e from ops.engineering_execution_envelope where id=rs.subject_envelope_id;
  select * into sp from ops.engineering_slice_plan where id=e.slice_plan_id;
  if op in ('acquire','renew') then
    ttl:=(p_request->>'ttl_seconds')::integer;
    if ttl is null or ttl<30 or ttl>1800 or clock_timestamp()+make_interval(secs=>ttl)>rs.expires_at then
      return jsonb_build_object('ok',false,'reason_id','ownership_operation_ttl_exceeds_binding');
    end if;
  end if;
  if op='acquire' then
    if not exists(select 1 from ops.canonical_ownership_issuer_generation
        where principal=session_user and state='active') then
      return jsonb_build_object('ok',false,'reason_id','ownership_issuer_draining');
    end if;
    select s.* into scope from ops.source_merge_plan_scope s
      join ops.sourced_work_request_plan_acceptance_receipt ar on ar.id=s.acceptance_receipt_id
      where s.accepted_plan_id=rs.accepted_plan_id and s.work_request_id=rs.work_request_id
        and s.organization_tenant_id=rs.organization_tenant_id and ar.result_version=e.state_version;
    if not found then return jsonb_build_object('ok',false,'reason_id','ownership_accepted_scope_missing'); end if;
    select jsonb_agg(jsonb_build_object('path',p,'mode','file','operation','write') order by p collate "C")
      into paths from jsonb_array_elements_text(scope.authorized_paths) p;
    select item into slice from jsonb_array_elements(sp.plan->'slices') item where item->>'slice_ref'=e.slice_ref;
    if slice is null or jsonb_typeof(slice->'declared_resource_refs') is distinct from 'array'
       or jsonb_typeof(slice->'declared_component_refs') is distinct from 'array' then
      return jsonb_build_object('ok',false,'reason_id','ownership_registered_claims_invalid');
    end if;
    select coalesce(jsonb_agg(jsonb_build_object('resource',ref) order by ref collate "C"),'[]'::jsonb)
      into resources from (select distinct value ref from jsonb_array_elements_text(
        (slice->'declared_resource_refs')||(slice->'declared_component_refs'))) r;
    deps:=ops.canonical_ownership_plan_dependencies(e.slice_plan_id,e.slice_ref);
    if not coalesce((deps->>'ok')::boolean,false) then return deps; end if;
    result:=ops.acquire_canonical_ownership_lease(e.work_request_id,e.state_version,
      e.canonical_record_digest,e.accepted_plan_id,sp.accepted_plan_hash,e.slice_plan_id,
      sp.plan_digest,e.slice_ref,scope.scope_digest,paths,resources,deps->'dependencies',ttl);
  else
    select * into lease from ops.canonical_ownership_lease where id=(p_request->>'lease_id')::uuid;
    if not found or lease.subject_envelope_id is distinct from rs.subject_envelope_id
       or lease.holder_session_ref is distinct from rs.ownership_session_ref then
      return jsonb_build_object('ok',false,'reason_id','ownership_operation_subject_mismatch');
    end if;
    case op
      when 'check' then
        select coalesce(jsonb_agg(jsonb_build_object('path',claim_value,'mode',claim_mode,'operation',operation)
          order by claim_value collate "C") filter(where claim_kind='path'),'[]'::jsonb),
          coalesce(jsonb_agg(jsonb_build_object('resource',claim_value) order by claim_value collate "C")
          filter(where claim_kind='resource'),'[]'::jsonb) into paths,resources
          from ops.canonical_ownership_claim where lease_id=lease.id;
        result:=ops.check_canonical_ownership_lease(lease.id,
        (p_request->>'lease_token')::uuid,(p_request->>'fencing_generation')::bigint,
        paths,resources);
      when 'renew' then result:=ops.renew_canonical_ownership_lease(lease.id,
        (p_request->>'lease_token')::uuid,(p_request->>'fencing_generation')::bigint,ttl);
      when 'release' then result:=ops.release_canonical_ownership_lease(lease.id,
        (p_request->>'lease_token')::uuid,(p_request->>'fencing_generation')::bigint);
    end case;
  end if;
  insert into ops.canonical_ownership_operation(idempotency_key,runtime_binding_id,request_digest,operation,response)
  values(key,rs.id,digest,op,result);
  return result||jsonb_build_object('replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range then
  return jsonb_build_object('ok',false,'reason_id','ownership_operation_invalid');
end $$;

create or replace function ops.canonical_ownership_claim_projection(p_lease_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare ctx jsonb:=ops.canonical_ownership_trusted_context(); result jsonb;
begin
  if not coalesce((ctx->>'ok')::boolean,false) then return ctx; end if;
  if not exists(select 1 from ops.canonical_ownership_lease where id=p_lease_id
     and holder_session_ref=ctx->>'ownership_session_ref'
     and subject_envelope_id=(ctx->>'subject_envelope_id')::uuid) then
    return jsonb_build_object('ok',false,'reason_id','ownership_operation_subject_mismatch');
  end if;
  select jsonb_build_object('ok',true,'lease_id',p_lease_id,
    'path_claims',coalesce(jsonb_agg(jsonb_build_object('path',claim_value,'mode',claim_mode,'operation',operation)
      order by claim_value collate "C") filter(where claim_kind='path'),'[]'::jsonb),
    'resource_claims',coalesce(jsonb_agg(jsonb_build_object('resource',claim_value) order by claim_value collate "C")
      filter(where claim_kind='resource'),'[]'::jsonb)) into result
    from ops.canonical_ownership_claim where lease_id=p_lease_id;
  return result;
end $$;

create or replace function ops.read_canonical_ownership_operation(p_idempotency_key uuid)
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare ctx jsonb:=ops.canonical_ownership_trusted_context(); result jsonb;
  l ops.canonical_ownership_lease%rowtype;
begin
  if not coalesce((ctx->>'ok')::boolean,false) then return ctx; end if;
  select response into result from ops.canonical_ownership_operation
   where idempotency_key=p_idempotency_key and runtime_binding_id=(ctx->>'binding_id')::uuid;
  if coalesce((result->>'ok')::boolean,false) and result ? 'lease_id' then
    select * into l from ops.canonical_ownership_lease where id=(result->>'lease_id')::uuid
      and holder_session_ref=ctx->>'ownership_session_ref'
      and subject_envelope_id=(ctx->>'subject_envelope_id')::uuid;
    if not found then return jsonb_build_object('ok',false,'reason_id','ownership_operation_subject_mismatch'); end if;
    result:=result||jsonb_build_object('lease_id',l.id,'lease_token',l.lease_token,'fencing_generation',l.fencing_generation);
  end if;
  return coalesce(result||jsonb_build_object('replayed',true),
    jsonb_build_object('ok',false,'reason_id','ownership_operation_not_found'));
end $$;

-- The two scoped Engineering terminal doors call this helper before changing
-- ops.job.  Keeping cleanup inside those existing doors preserves the control-
-- plane invariant that ops.job has no user trigger while still making lease
-- cleanup and the terminal transition one transaction.  The try-lock refuses a
-- competing acquisition rather than introducing job/ownership lock inversion.
create or replace function ops.canonical_ownership_release_for_job(
  p_job_id uuid,p_attempt integer,p_lease_token uuid
)
returns void language plpgsql set search_path=pg_catalog,ops,public
as $$
declare rs ops.canonical_ownership_runtime_session%rowtype;
  l ops.canonical_ownership_lease%rowtype; stamp timestamptz; event_kind text;
begin
  for rs in select r.* from ops.canonical_ownership_runtime_session r
    join ops.engineering_execution_envelope e on e.id=r.subject_envelope_id
    where e.job_id=p_job_id and r.attempt=p_attempt and r.state='active' and r.phase='build' order by r.id loop
    if rs.capability_token_hash is distinct from encode(public.digest(p_lease_token::text,'sha256'),'hex') then
      raise exception 'ownership terminal capability binding mismatch';
    end if;
    if not pg_try_advisory_xact_lock(hashtextextended('canonical-ownership:'||rs.organization_tenant_id,0)) then
      raise exception using errcode='40001',message='ownership terminal cleanup contended; retry exact transition';
    end if;
    for l in select * from ops.canonical_ownership_lease where holder_session_ref=rs.ownership_session_ref
      and subject_envelope_id=rs.subject_envelope_id and state='active' order by id for update loop
      stamp:=clock_timestamp();
      event_kind:=case when l.expires_at<=stamp then 'expired' else 'released' end;
      update ops.canonical_ownership_lease set state=event_kind,
        released_at=case when event_kind='released' then stamp else null end,updated_at=stamp where id=l.id;
      insert into ops.canonical_ownership_lease_event(organization_tenant_id,lease_id,event_kind,
        fencing_generation,actor_id,session_ref,host_ref,cause,occurred_at,created_at)
      values(l.organization_tenant_id,l.id,event_kind,l.fencing_generation,l.holder_actor_id,
        l.holder_session_ref,l.holder_host_ref,'{"reason":"release_before_terminal"}',stamp,stamp);
    end loop;
    update ops.canonical_ownership_runtime_session set state='expired' where id=rs.id;
  end loop;
end $$;

-- Patch the already reviewed scoped terminal functions without cloning their
-- large typed-receipt contracts. Exact markers refuse if a predecessor changes.
do $terminal_cleanup_doors$
declare v_definition text; v_marker text; v_replacement text;
begin
  select pg_get_functiondef('ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid)'::regprocedure)
    into v_definition;
  v_marker:='  if row.outcome=''claimed_complete'' then';
  if length(v_definition)-length(replace(v_definition,v_marker,''))<>length(v_marker) then
    raise exception 'engineering finalize cleanup marker drifted';
  end if;
  v_replacement:='  perform ops.canonical_ownership_release_for_job(j.id,j.attempt,p_lease_token);'||chr(10)||v_marker;
  execute replace(v_definition,v_marker,v_replacement);

  select pg_get_functiondef('ops.engineering_fail_claim(uuid,uuid,text,text)'::regprocedure)
    into v_definition;
  v_marker:='  update ops.job_attempt set state=''failed'',ended_at=v_now,';
  if length(v_definition)-length(replace(v_definition,v_marker,''))<>length(v_marker) then
    raise exception 'engineering failure cleanup marker drifted';
  end if;
  v_replacement:='  perform ops.canonical_ownership_release_for_job(j.id,j.attempt,p_lease_token);'||chr(10)||v_marker;
  execute replace(v_definition,v_marker,v_replacement);
end $terminal_cleanup_doors$;
revoke all on function ops.canonical_ownership_release_for_job(uuid,integer,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_ownership_issuer;
revoke all on function ops.canonical_ownership_lifecycle(jsonb),
  ops.read_canonical_ownership_operation(uuid),
  ops.canonical_ownership_claim_projection(uuid),ops.canonical_ownership_merge_binding_valid(uuid),
  ops.authenticated_canonical_ownership_controller_binding(uuid,uuid,text,text),
  ops.mint_canonical_ownership_merge_session(uuid,uuid,text,integer,text,timestamptz,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.canonical_ownership_lifecycle(jsonb),
  ops.read_canonical_ownership_operation(uuid),ops.canonical_ownership_claim_projection(uuid),
  ops.authenticated_canonical_ownership_controller_binding(uuid,uuid,text,text),
  ops.mint_canonical_ownership_merge_session(uuid,uuid,text,integer,text,timestamptz,uuid)
  to carr_ownership_issuer;

-- Preserve the initial triaged-plan admission/review implementation byte for
-- behavior under private predecessor names.  The public names below dispatch
-- ready amendment targets to the new lane and every other target to v1.
do $clone_heavy_build_predecessors$
declare v_definition text; v_marker text; v_private text; v_signature text;
begin
  for v_signature,v_marker,v_private in
    select * from (values
      ('ops.record_sourced_heavy_build_admission(uuid,text,integer,jsonb,jsonb,uuid,uuid)',
       'FUNCTION ops.record_sourced_heavy_build_admission(',
       'FUNCTION ops.record_sourced_heavy_build_admission_v1('),
      ('ops.sourced_heavy_build_review_target(text,text,text)',
       'FUNCTION ops.sourced_heavy_build_review_target(',
       'FUNCTION ops.sourced_heavy_build_review_target_v1('),
      ('ops.review_sourced_heavy_build_plan(text,text,text,uuid,text,text,text,jsonb,jsonb,uuid)',
       'FUNCTION ops.review_sourced_heavy_build_plan(',
       'FUNCTION ops.review_sourced_heavy_build_plan_v1(')
    ) predecessor(signature,name_marker,private_marker)
  loop
    select pg_get_functiondef(v_signature::regprocedure) into v_definition;
    if (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
      raise exception 'heavy-build predecessor definition drifted: %',v_signature;
    end if;
    execute replace(v_definition,v_marker,v_private);
  end loop;
end $clone_heavy_build_predecessors$;

alter table ops.sourced_work_request_plan_acceptance_receipt
  drop constraint sourced_work_request_plan_acceptance_receip_work_request_id_key;
alter table ops.sourced_work_request_plan_acceptance_receipt
  add constraint sourced_work_request_plan_acceptance_work_version_key
  unique(work_request_id,result_version);

-- Source-merge scope was originally one-row-per-Work-Request because a ready
-- Work Request could have only one accepted plan.  Keep every historical scope
-- immutable, but let each accepted successor carry its own exact scope.
alter table ops.source_merge_plan_scope
  drop constraint source_merge_plan_scope_work_request_id_key;

-- Every existing Passport/registration reader enters through this function.
-- Resolve current authority by the receipt version, never max(timestamp) and
-- never the old one-row-per-request assumption.
create or replace function ops.engineering_admission_source(p_work_request text)
returns jsonb language sql stable security definer
set search_path=pg_catalog,ops,public
as $$
  select jsonb_build_object(
    'work_request',jsonb_build_object(
      'id','wr:'||w.id::text,'ref',w.ref,'state',w.state,'version',w.version,
      'title',w.title,'desired_outcome',w.desired_outcome,
      'acceptance_criteria',w.acceptance_criteria,
      'canonical_record_digest','sha256:'||encode(public.digest(
        jsonb_build_object('id',w.id,'ref',w.ref,'state',w.state,'version',w.version,
          'title',w.title,'desired_outcome',w.desired_outcome,
          'acceptance_criteria',w.acceptance_criteria)::text,'sha256'),'hex')),
    'accepted_plan',jsonb_build_object(
      'id',p.plan_ref,'record_id',p.id,'plan_ref',p.plan_ref,
      'revision',p.plan_version,'digest',p.plan_hash,
      'work_request_version',p.work_request_version,'preimage',p.preimage,
      'scope_summary',p.scope_summary,'dependency_refs',p.dependency_refs,
      'recovery_ref',p.recovery_ref,'observability_ref',p.observability_ref,
      'caps',p.caps,'accepted_at',ar.accepted_at,
      'accepted_by_actor_id',ar.accepted_by_actor_id),
    'execution_authorized',not ops.engineering_contract_stale(p.plan_ref,p.plan_hash,null),
    'execution_refusal_reason',case when ops.engineering_contract_stale(p.plan_ref,p.plan_hash,null)
      then (select f.reason from ops.engineering_stale_contract_fence f where f.plan_ref=p.plan_ref)
      else null end)
    from ops.work_request w
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.work_request_id=w.id and ar.result_version=w.version
    join ops.sourced_work_request_plan p
      on p.id=ar.plan_id and p.work_request_id=w.id and p.plan_hash=ar.plan_hash
   where w.ref=p_work_request and w.state='ready';
$$;

create table ops.ready_plan_amendment (
  plan_id uuid primary key references ops.sourced_work_request_plan(id) on delete restrict,
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  predecessor_plan_id uuid not null references ops.sourced_work_request_plan(id) on delete restrict,
  predecessor_plan_hash text not null check (predecessor_plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  base_version integer not null check (base_version>0),
  amendment_hash text not null unique check (amendment_hash ~ '^sha256:[0-9a-f]{64}$'),
  proposed_by_actor_id uuid not null references public.actor(id) on delete restrict,
  proposed_at timestamptz not null default now(),
  unique(work_request_id,predecessor_plan_id,plan_id),
  unique(work_request_id,predecessor_plan_id),
  check (plan_id<>predecessor_plan_id)
);

-- WR125 deliberately carries no completion across a changed accepted plan.
-- The compatibility map is nevertheless exhaustive: one immutable row for
-- every predecessor slice says that it must be rebuilt against the successor.
-- This is the conservative closed-union policy; no claimed-only, changed,
-- removed, or dependency-invalidated slice can inherit completion.
create table ops.ready_plan_amendment_slice_compatibility (
  successor_plan_id uuid not null references ops.ready_plan_amendment(plan_id) on delete restrict,
  predecessor_slice_plan_id uuid not null references ops.engineering_slice_plan(id) on delete restrict,
  predecessor_plan_digest text not null check (predecessor_plan_digest ~ '^sha256:[0-9a-f]{64}$'),
  slice_ref text not null check (btrim(slice_ref)<>''),
  predecessor_slice_digest text not null check (predecessor_slice_digest ~ '^sha256:[0-9a-f]{64}$'),
  disposition text not null default 'rebuild_required' check (disposition='rebuild_required'),
  reason text not null default 'accepted_plan_changed' check (reason='accepted_plan_changed'),
  created_at timestamptz not null default now(),
  primary key(successor_plan_id,predecessor_slice_plan_id,slice_ref)
);

create table ops.ready_plan_amendment_acceptance_receipt (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  predecessor_plan_id uuid not null references ops.sourced_work_request_plan(id) on delete restrict,
  successor_plan_id uuid not null unique references ops.sourced_work_request_plan(id) on delete restrict,
  plan_acceptance_receipt_id uuid not null unique references ops.sourced_work_request_plan_acceptance_receipt(id) on delete restrict,
  idempotency_key uuid not null unique,
  base_version integer not null,
  result_version integer not null,
  accepted_by_actor_id uuid not null references public.actor(id) on delete restrict,
  accepted_at timestamptz not null default now(),
  census jsonb not null check (jsonb_typeof(census)='object'),
  unique(work_request_id,result_version),
  check (result_version=base_version+1)
);

create table ops.ready_plan_amendment_notice (
  id bigint generated always as identity primary key,
  acceptance_receipt_id uuid not null references ops.ready_plan_amendment_acceptance_receipt(id) on delete restrict,
  actor_id uuid not null references public.actor(id) on delete restrict,
  created_at timestamptz not null default now(),
  unique(acceptance_receipt_id,actor_id)
);

create table ops.ready_plan_amendment_ack (
  notice_id bigint primary key references ops.ready_plan_amendment_notice(id) on delete restrict,
  actor_id uuid not null references public.actor(id) on delete restrict,
  idempotency_key uuid not null unique,
  acknowledged_at timestamptz not null default now()
);

create or replace function ops.ready_plan_amendment_rows_immutable()
returns trigger language plpgsql set search_path=pg_catalog,ops as $$
begin raise exception 'ready-plan amendment history is append-only'; end $$;

create trigger ready_plan_amendment_immutable before update or delete on ops.ready_plan_amendment
for each row execute function ops.ready_plan_amendment_rows_immutable();
create trigger ready_plan_amendment_slice_compatibility_immutable before update or delete on ops.ready_plan_amendment_slice_compatibility
for each row execute function ops.ready_plan_amendment_rows_immutable();
create trigger ready_plan_amendment_acceptance_immutable before update or delete on ops.ready_plan_amendment_acceptance_receipt
for each row execute function ops.ready_plan_amendment_rows_immutable();
create trigger ready_plan_amendment_notice_immutable before update or delete on ops.ready_plan_amendment_notice
for each row execute function ops.ready_plan_amendment_rows_immutable();
create trigger ready_plan_amendment_ack_immutable before update or delete on ops.ready_plan_amendment_ack
for each row execute function ops.ready_plan_amendment_rows_immutable();

-- Serialize slice-plan creation with ready-plan replacement on the canonical
-- Work Request row.  A registration that began before acceptance either lands
-- before the census/map or wakes afterward and refuses as stale.
create or replace function ops.ready_plan_amendment_slice_plan_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $$
begin
  perform 1 from ops.work_request w where w.id=new.work_request_id for share;
  if not found or not exists(
    select 1 from ops.work_request w
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.work_request_id=w.id and ar.result_version=w.version
   where w.id=new.work_request_id and w.state='ready'
     and ar.plan_id=new.accepted_plan_id and ar.plan_hash=new.accepted_plan_hash
     and new.work_request_version=w.version) then
    raise exception 'engineering slice plan is not bound to the exact current accepted plan';
  end if;
  return new;
end $$;

create trigger ready_plan_amendment_slice_plan_current
before insert on ops.engineering_slice_plan
for each row execute function ops.ready_plan_amendment_slice_plan_guard();

create or replace function ops.record_ready_plan_amendment_admission_internal(
  p_plan_id uuid,p_work_request text,p_base_version integer,
  p_classifier_reasons jsonb,p_contract jsonb,p_proposed_by_actor_id uuid,
  p_idempotency_key uuid
) returns table (
  work_request_id uuid,ref text,plan_id uuid,plan_ref text,
  admission_ref text,admission_hash text,tier text,
  classifier_reasons jsonb,builder_session_ref text,replayed boolean
) language plpgsql set search_path=pg_catalog,ops,public
as $$
declare v_work ops.work_request%rowtype; v_plan ops.sourced_work_request_plan%rowtype;
        v_amend ops.ready_plan_amendment%rowtype; v_admission ops.heavy_build_admission_revision%rowtype;
        v_actor public.actor%rowtype; v_classification jsonb; v_expected jsonb;
        v_research jsonb; v_master jsonb; v_field text; v_minimum integer;
        v_version integer; v_preimage jsonb; v_digest text;
begin
  if p_plan_id is null or coalesce(p_work_request,'')!~'^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version<1 or p_proposed_by_actor_id is null
     or p_idempotency_key is null or jsonb_typeof(p_classifier_reasons) is distinct from 'array'
     or jsonb_array_length(p_classifier_reasons)=0
     or not ops.heavy_build_jsonb_has_exact_keys(p_contract,
          array['builder_session_ref','master_plan','research_manifest'])
     or coalesce(p_contract->>'builder_session_ref','')!~'^session:[a-z0-9][a-z0-9:._/-]{8,199}$'
     or jsonb_typeof(p_contract->'master_plan') is distinct from 'object'
     or jsonb_typeof(p_contract->'research_manifest') is distinct from 'object'
     or p_contract->'master_plan'='{}'::jsonb or p_contract->'research_manifest'='{}'::jsonb then
    raise exception 'ready-plan amendment admission requires exact plan, version, classifier, closed heavy contract, actor, and idempotency key';
  end if;
  v_research:=p_contract->'research_manifest';
  v_master:=p_contract->'master_plan';
  if not ops.heavy_build_jsonb_has_exact_keys(v_research,
       array['conclusion','current_baseline','failure_modes','maintained_repositories',
             'practitioner_evidence','primary_sources','unresolved_contradictions'])
     or char_length(btrim(coalesce(v_research->>'conclusion',''))) not between 20 and 1000
     or jsonb_typeof(v_research->'unresolved_contradictions') is distinct from 'array'
     or jsonb_array_length(v_research->'unresolved_contradictions')>12
     or exists(select 1 from jsonb_array_elements(v_research->'unresolved_contradictions') item
       where jsonb_typeof(item)<>'string'
          or char_length(btrim(item#>>'{}')) not between 10 and 500) then
    raise exception 'ready-plan amendment research manifest is incomplete';
  end if;
  foreach v_field in array array['primary_sources','maintained_repositories',
      'practitioner_evidence','current_baseline','failure_modes'] loop
    v_minimum:=case when v_field='maintained_repositories' then 2 else 1 end;
    if jsonb_typeof(v_research->v_field) is distinct from 'array'
       or jsonb_array_length(v_research->v_field) not between v_minimum and 12 then
      raise exception 'ready-plan amendment research class % is incomplete',v_field;
    end if;
  end loop;
  if exists(
    select 1 from (values
      ('primary_sources','primary_source'),
      ('maintained_repositories','maintained_repository'),
      ('practitioner_evidence','practitioner_evidence'),
      ('current_baseline','current_baseline'),('failure_modes','failure_mode')
    ) expected(field_name,class_name)
    cross join lateral jsonb_array_elements(v_research->expected.field_name) item
    where not ops.heavy_build_jsonb_has_exact_keys(item,
            array['content_digest','finding','locator','observed_at','source_class','source_ref'])
       or coalesce(item->>'source_ref','')!~'^safe:[a-z0-9][a-z0-9:_./-]*$'
       or item->>'source_class' is distinct from expected.class_name
       or coalesce(item->>'locator','')!~'^https://'
       or coalesce(item->>'observed_at','')!~'^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
       or case when coalesce(item->>'observed_at','')~'^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
            then (item->>'observed_at')::timestamptz>now()+interval '5 minutes' else true end
       or coalesce(item->>'content_digest','')!~'^sha256:[0-9a-f]{64}$'
       or char_length(btrim(coalesce(item->>'finding',''))) not between 20 and 1000
  ) then raise exception 'ready-plan amendment research evidence is invalid'; end if;
  if exists(select source_ref from (
      select item->>'source_ref' source_ref
        from (values ('primary_sources'),('maintained_repositories'),
          ('practitioner_evidence'),('current_baseline'),('failure_modes')) fields(field_name)
        cross join lateral jsonb_array_elements(v_research->fields.field_name) item
    ) refs group by source_ref having count(*)>1) then
    raise exception 'ready-plan amendment research source refs must be unique';
  end if;
  if not ops.heavy_build_jsonb_has_exact_keys(v_master,
       array['architecture','authority_boundaries','baseline_comparison','dependency_dag',
             'fully_shipped_definition','non_goals','observability_strategy','planned_checks',
             'prerequisite_policy','product_goal','release_strategy','rollback_strategy']) then
    raise exception 'ready-plan amendment master plan is incomplete';
  end if;
  foreach v_field in array array['product_goal','baseline_comparison','release_strategy',
      'rollback_strategy','observability_strategy','fully_shipped_definition','prerequisite_policy'] loop
    if char_length(btrim(coalesce(v_master->>v_field,''))) not between 20 and 2000 then
      raise exception 'ready-plan amendment master plan field % is incomplete',v_field;
    end if;
  end loop;
  foreach v_field in array array['non_goals','architecture','authority_boundaries'] loop
    v_minimum:=case when v_field='architecture' then 2 else 1 end;
    if jsonb_typeof(v_master->v_field) is distinct from 'array'
       or jsonb_array_length(v_master->v_field) not between v_minimum
          and (case when v_field='architecture' then 20 else 12 end)
       or exists(select 1 from jsonb_array_elements(v_master->v_field) item
         where jsonb_typeof(item)<>'string'
           or char_length(btrim(item#>>'{}')) not between 10 and 1000) then
      raise exception 'ready-plan amendment master plan field % is incomplete',v_field;
    end if;
  end loop;
  if jsonb_typeof(v_master->'dependency_dag') is distinct from 'array'
     or jsonb_array_length(v_master->'dependency_dag') not between 1 and 20
     or exists(select 1 from jsonb_array_elements(v_master->'dependency_dag') step
       where not ops.heavy_build_jsonb_has_exact_keys(step,array['depends_on','step_ref'])
         or coalesce(step->>'step_ref','')!~'^step:[a-z0-9][a-z0-9:._/-]*$'
         or jsonb_typeof(step->'depends_on')<>'array'
         or exists(select 1 from jsonb_array_elements(step->'depends_on') dep
           where jsonb_typeof(dep)<>'string'
              or dep#>>'{}'!~'^step:[a-z0-9][a-z0-9:._/-]*$'
              or dep#>>'{}'=step->>'step_ref'))
     or exists(select step->>'step_ref' from jsonb_array_elements(v_master->'dependency_dag') step
       group by step->>'step_ref' having count(*)>1)
     or exists(select 1 from jsonb_array_elements(v_master->'dependency_dag') step
       cross join lateral jsonb_array_elements_text(step->'depends_on') dep
       where not exists(select 1 from jsonb_array_elements(v_master->'dependency_dag') declared
         where declared->>'step_ref'=dep)) then
    raise exception 'ready-plan amendment dependency DAG is invalid';
  end if;
  if exists(with recursive edges(src,dst) as (
      select step->>'step_ref',dep from jsonb_array_elements(v_master->'dependency_dag') step
      cross join lateral jsonb_array_elements_text(step->'depends_on') dep
    ), walk(start_node,node,path,cycle) as (
      select src,dst,array[src,dst],dst=src from edges
      union all
      select walk.start_node,edges.dst,walk.path||edges.dst,edges.dst=any(walk.path)
        from walk join edges on edges.src=walk.node where not walk.cycle
    ) select 1 from walk where cycle limit 1) then
    raise exception 'ready-plan amendment dependency DAG contains a cycle';
  end if;
  if jsonb_typeof(v_master->'planned_checks') is distinct from 'array'
     or jsonb_array_length(v_master->'planned_checks') not between 1 and 20
     or exists(select 1 from jsonb_array_elements(v_master->'planned_checks') item
       where not ops.heavy_build_jsonb_has_exact_keys(item,
              array['artifact','comparator','failure_condition'])
         or char_length(btrim(coalesce(item->>'artifact',''))) not between 5 and 500
         or char_length(btrim(coalesce(item->>'comparator',''))) not between 5 and 500
         or char_length(btrim(coalesce(item->>'failure_condition',''))) not between 5 and 500) then
    raise exception 'ready-plan amendment planned checks are invalid';
  end if;
  select * into v_actor from public.actor where id=p_proposed_by_actor_id and active for share;
  if not found then raise exception 'ready-plan amendment admission actor is not active'; end if;
  perform pg_advisory_xact_lock(hashtextextended('heavy-build-admission:'||p_idempotency_key,0));
  select * into v_admission from ops.heavy_build_admission_revision
   where idempotency_key=p_idempotency_key for share;
  if found then
    select * into v_work from ops.work_request where id=v_admission.work_request_id;
    select * into v_plan from ops.sourced_work_request_plan where id=v_admission.plan_id;
    if v_work.ref is distinct from p_work_request or v_plan.id is distinct from p_plan_id
       or v_plan.work_request_version is distinct from p_base_version
       or v_admission.classifier_reasons is distinct from p_classifier_reasons
       or v_admission.contract is distinct from p_contract
       or v_admission.proposed_by_actor_id is distinct from p_proposed_by_actor_id then
      raise exception 'idempotency key already names a different ready-plan amendment admission';
    end if;
    return query select v_work.id,v_work.ref,v_plan.id,v_plan.plan_ref,
      v_admission.admission_ref,v_admission.admission_hash,v_admission.tier,
      v_admission.classifier_reasons,v_admission.builder_session_ref,true;
    return;
  end if;
  select * into v_work from ops.work_request where ref=p_work_request for share;
  if not found or v_work.state<>'ready' or v_work.version<>p_base_version then
    raise exception 'exact current ready Work Request required for amendment admission';
  end if;
  select * into v_plan from ops.sourced_work_request_plan
   where id=p_plan_id and work_request_id=v_work.id for share;
  select * into v_amend from ops.ready_plan_amendment
   where plan_id=p_plan_id and work_request_id=v_work.id and base_version=v_work.version for share;
  if v_plan.id is null or v_amend.plan_id is null
     or v_plan.work_request_version<>v_work.version then
    raise exception 'exact current ready-plan amendment required for admission';
  end if;
  v_classification:=ops.heavy_build_classification(v_work.id,v_plan.scope_summary,
    v_plan.dependency_refs,v_plan.caps);
  if coalesce((v_classification->>'shape_ready')::boolean,false) is not true then
    raise exception 'ready-plan amendment requires the current evidence-backed Work Shape';
  end if;
  v_expected:=case when v_classification->>'tier'='heavy' then v_classification->'reasons'
    else jsonb_build_array('caller:explicit-heavy-contract') end;
  if p_classifier_reasons is distinct from v_expected then
    raise exception 'ready-plan amendment classifier reasons must be server-derived';
  end if;
  select coalesce(max(version),0)+1 into v_version
    from ops.heavy_build_admission_revision where plan_id=v_plan.id;
  v_preimage:=jsonb_build_object('contract','carr-heavy-build-admission/v1',
    'work_request_id',v_work.id,'work_request_version',v_work.version,
    'plan_id',v_plan.id,'plan_hash',v_plan.plan_hash,'classifier_reasons',v_expected,
    'heavy_build',p_contract,'proposed_by_actor_id',p_proposed_by_actor_id,'version',v_version);
  v_digest:=ops.heavy_build_digest(v_preimage);
  insert into ops.heavy_build_admission_revision(admission_ref,work_request_id,plan_id,
    version,idempotency_key,tier,classifier_reasons,contract,builder_session_ref,
    admission_hash,proposed_by_actor_id)
  values('HBA-'||substr(v_digest,8,12)||'-v'||v_version,v_work.id,v_plan.id,
    v_version,p_idempotency_key,'heavy',v_expected,p_contract,
    p_contract->>'builder_session_ref',v_digest,p_proposed_by_actor_id)
  returning * into v_admission;
  return query select v_work.id,v_work.ref,v_plan.id,v_plan.plan_ref,
    v_admission.admission_ref,v_admission.admission_hash,v_admission.tier,
    v_admission.classifier_reasons,v_admission.builder_session_ref,false;
end $$;

create or replace function ops.record_sourced_heavy_build_admission(
  p_plan_id uuid,p_work_request text,p_base_version integer,
  p_classifier_reasons jsonb,p_contract jsonb,p_proposed_by_actor_id uuid,
  p_idempotency_key uuid
) returns table (
  work_request_id uuid,ref text,plan_id uuid,plan_ref text,
  admission_ref text,admission_hash text,tier text,
  classifier_reasons jsonb,builder_session_ref text,replayed boolean
) language plpgsql security definer set search_path=pg_catalog,ops
as $$
begin
  if exists(select 1 from ops.ready_plan_amendment a
    where a.plan_id=p_plan_id and a.base_version=p_base_version) then
    return query select * from ops.record_ready_plan_amendment_admission_internal(
      p_plan_id,p_work_request,p_base_version,p_classifier_reasons,p_contract,
      p_proposed_by_actor_id,p_idempotency_key);
  else
    return query select * from ops.record_sourced_heavy_build_admission_v1(
      p_plan_id,p_work_request,p_base_version,p_classifier_reasons,p_contract,
      p_proposed_by_actor_id,p_idempotency_key);
  end if;
end $$;

create or replace function ops.effective_ready_plan(p_work_request text)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_work ops.work_request%rowtype; v_plan ops.sourced_work_request_plan%rowtype;
begin
  select * into v_work from ops.work_request where ref=p_work_request
    and organization_tenant_id=current_setting('carr.organization_tenant_id',true);
  if not found then return jsonb_build_object('ok',false,'reason_id','work_request_not_found'); end if;
  select p.* into v_plan from ops.sourced_work_request_plan_acceptance_receipt ar
    join ops.sourced_work_request_plan p on p.id=ar.plan_id
   where ar.work_request_id=v_work.id and ar.result_version=v_work.version;
  if not found or ops.engineering_contract_stale(v_plan.plan_ref,v_plan.plan_hash,null) then
    return jsonb_build_object('ok',false,'reason_id','current_plan_stale_or_missing');
  end if;
  return jsonb_build_object('ok',true,
    'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state',v_work.state,'version',v_work.version),
    'current_plan',jsonb_build_object('id',v_plan.id,'ref',v_plan.plan_ref,'hash',v_plan.plan_hash,'version',v_plan.plan_version),
    'lineage',(select coalesce(jsonb_agg(jsonb_build_object(
      'plan_ref',p.plan_ref,'plan_hash',p.plan_hash,'plan_version',p.plan_version,
      'predecessor_plan_ref',prior.plan_ref,'accepted',ar.id is not null)
      order by p.plan_version),'[]'::jsonb)
      from ops.sourced_work_request_plan p
      left join ops.ready_plan_amendment a on a.plan_id=p.id
      left join ops.sourced_work_request_plan prior on prior.id=a.predecessor_plan_id
      left join ops.sourced_work_request_plan_acceptance_receipt ar on ar.plan_id=p.id
      where p.work_request_id=v_work.id));
end $$;

create or replace function ops.propose_ready_plan_amendment(
  p_work_request text,p_base_version integer,p_predecessor_plan_hash text,
  p_scope_summary text,p_runbook_ref text,p_dependency_refs jsonb,
  p_recovery_ref text,p_observability_ref text,p_caps jsonb,p_heavy_build jsonb,
  p_proposed_by_actor_id uuid,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_work ops.work_request%rowtype; v_prior ops.sourced_work_request_plan%rowtype;
        v_plan ops.sourced_work_request_plan%rowtype; v_existing ops.sourced_work_request_plan%rowtype;
        v_section uuid; v_revision uuid; v_hash text; v_preimage jsonb;
        v_plan_hash text; v_version integer; v_amendment_hash text;
        v_classification jsonb; v_reasons jsonb; v_admission record;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  if coalesce(p_work_request,'')!~'^WR-[0-9]{1,12}$'
     or p_idempotency_key is null or p_base_version is null or p_base_version<1
     or coalesce(p_predecessor_plan_hash,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(btrim(p_scope_summary),'')='' or char_length(btrim(p_scope_summary))>1000
     or coalesce(btrim(p_runbook_ref),'')!~'^doctrine:runbook#[a-z0-9][a-z0-9-]*$'
     or coalesce(btrim(p_recovery_ref),'')!~'^safe:[a-z0-9][a-z0-9:_./-]*$'
     or char_length(btrim(p_recovery_ref))>300
     or coalesce(btrim(p_observability_ref),'')!~'^safe:[a-z0-9][a-z0-9:_./-]*$'
     or char_length(btrim(p_observability_ref))>300
     or jsonb_typeof(p_dependency_refs) is distinct from 'array'
     or jsonb_array_length(p_dependency_refs)>12
     or exists(select 1 from jsonb_array_elements(p_dependency_refs) dep
       where jsonb_typeof(dep)<>'string' or dep#>>'{}'!~'^safe:[a-z0-9][a-z0-9:_./-]*$'
         or char_length(dep#>>'{}')>300)
     or exists(select dep#>>'{}' from jsonb_array_elements(p_dependency_refs) dep
       group by dep#>>'{}' having count(*)>1)
     or jsonb_typeof(p_caps) is distinct from 'object'
     or ((select array_agg(key order by key) from jsonb_object_keys(p_caps) key)
          is distinct from array['max_duration_minutes','max_steps']::text[]
       and (select array_agg(key order by key) from jsonb_object_keys(p_caps) key)
          is distinct from array['max_duration_minutes','max_steps','source_merge']::text[])
     or coalesce(p_caps->>'max_steps','')!~'^[0-9]+$'
     or coalesce(p_caps->>'max_duration_minutes','')!~'^[0-9]+$'
     or (p_caps->>'max_steps')::integer not between 1 and 20
     or (p_caps->>'max_duration_minutes')::integer not between 1 and 120
     or (p_caps?'source_merge' and not coalesce(ops.source_merge_scope_valid(p_caps->'source_merge'),false))
     or p_proposed_by_actor_id is null or p_proposed_by_actor_id<>v_actor
     or not ops.heavy_build_jsonb_has_exact_keys(p_heavy_build,
          array['builder_session_ref','master_plan','research_manifest'])
     or coalesce(p_heavy_build->>'builder_session_ref','')!~'^session:[a-z0-9][a-z0-9:._/-]{8,199}$'
     or jsonb_typeof(p_heavy_build->'master_plan') is distinct from 'object'
     or jsonb_typeof(p_heavy_build->'research_manifest') is distinct from 'object'
     or p_heavy_build->'master_plan'='{}'::jsonb
     or p_heavy_build->'research_manifest'='{}'::jsonb then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_input_invalid');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('ready-plan-amendment:'||p_work_request,0));
  select * into v_existing from ops.sourced_work_request_plan where idempotency_key=p_idempotency_key;
  if found then
    select * into v_work from ops.work_request where id=v_existing.work_request_id;
    select * into v_prior from ops.sourced_work_request_plan where id=(select predecessor_plan_id from ops.ready_plan_amendment where plan_id=v_existing.id);
    select * into v_admission from ops.heavy_build_admission_revision
      where plan_id=v_existing.id order by version desc limit 1;
    if v_work.ref is distinct from p_work_request or v_prior.plan_hash is distinct from p_predecessor_plan_hash
       or v_existing.work_request_version is distinct from p_base_version
       or v_existing.scope_summary is distinct from btrim(p_scope_summary)
       or v_existing.runbook_ref is distinct from btrim(p_runbook_ref)
       or v_existing.dependency_refs is distinct from p_dependency_refs
       or v_existing.recovery_ref is distinct from btrim(p_recovery_ref)
       or v_existing.observability_ref is distinct from btrim(p_observability_ref)
       or v_existing.caps is distinct from p_caps or v_admission.contract is distinct from p_heavy_build
       or v_admission.proposed_by_actor_id is distinct from p_proposed_by_actor_id then
      return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_idempotency_conflict');
    end if;
    return jsonb_build_object('ok',true,'replayed',true,
      'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state','ready','version',v_existing.work_request_version),
      'plan',jsonb_build_object('id',v_existing.id,'ref',v_existing.plan_ref,'hash',v_existing.plan_hash,'version',v_existing.plan_version,
        'predecessor_ref',v_prior.plan_ref,'predecessor_hash',v_prior.plan_hash),
      'build_admission',jsonb_build_object('tier',v_admission.tier,'reasons',v_admission.classifier_reasons,
        'admission_ref',v_admission.admission_ref,'admission_hash',v_admission.admission_hash,
        'builder_session_ref',v_admission.builder_session_ref));
  end if;
  select * into v_work from ops.work_request where ref=p_work_request for update;
  if not found or v_work.state<>'ready' or v_work.version<>p_base_version then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_base_stale');
  end if;
  select p.* into v_prior from ops.sourced_work_request_plan_acceptance_receipt ar
    join ops.sourced_work_request_plan p on p.id=ar.plan_id
   where ar.work_request_id=v_work.id and ar.result_version=v_work.version for share of p,ar;
  if not found or v_prior.plan_hash<>p_predecessor_plan_hash
     or exists(select 1 from ops.engineering_stale_contract_fence f
       where (f.plan_ref=v_prior.plan_ref or f.plan_hash=v_prior.plan_hash)
         and not f.successor_allowed) then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_predecessor_stale');
  end if;
  if exists(select 1 from ops.ready_plan_amendment a
    where a.work_request_id=v_work.id and a.predecessor_plan_id=v_prior.id) then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_fork_exists');
  end if;
  select s.id,r.id,r.content_hash into v_section,v_revision,v_hash
    from public.doctrine_document d join public.doctrine_section s on s.document_id=d.id
    join public.doctrine_revision r on r.id=s.current_revision_id
   where d.slug='runbook' and s.status='active'
     and 'doctrine:'||d.slug||'#'||s.section_key=btrim(p_runbook_ref)
     and r.section_id=s.id and encode(public.digest(r.plain_text,'sha256'),'hex')=r.content_hash
   for share of d,s,r;
  if not found then return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_runbook_stale'); end if;
  select coalesce(max(plan_version),0)+1 into v_version from ops.sourced_work_request_plan where work_request_id=v_work.id;
  v_preimage:=ops.sourced_work_request_plan_preimage(v_work.id,btrim(p_scope_summary),btrim(p_runbook_ref),
    v_section,v_revision,v_hash,p_dependency_refs,btrim(p_recovery_ref),btrim(p_observability_ref),p_caps)
    ||jsonb_build_object('successor_of',jsonb_build_object('plan_id',v_prior.id,'plan_ref',v_prior.plan_ref,'plan_hash',v_prior.plan_hash));
  v_plan_hash:=ops.sourced_work_request_plan_digest(v_preimage);
  if ops.engineering_contract_stale(null,v_plan_hash,null) then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_plan_stale');
  end if;
  insert into ops.sourced_work_request_plan(work_request_id,plan_version,idempotency_key,work_request_version,
    preimage,scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,runbook_content_hash,
    dependency_refs,recovery_ref,observability_ref,caps,plan_hash,plan_ref)
  values(v_work.id,v_version,p_idempotency_key,v_work.version,v_preimage,btrim(p_scope_summary),btrim(p_runbook_ref),
    v_section,v_revision,v_hash,p_dependency_refs,btrim(p_recovery_ref),btrim(p_observability_ref),p_caps,
    v_plan_hash,'PLAN-'||substr(v_plan_hash,8,12)||'-v'||v_version)
  returning * into v_plan;
  v_amendment_hash:=ops.heavy_build_digest(jsonb_build_object('contract','carr-ready-plan-amendment/v1',
    'work_request_id',v_work.id,'base_version',v_work.version,'predecessor_plan_hash',v_prior.plan_hash,
    'successor_plan_hash',v_plan.plan_hash));
  insert into ops.ready_plan_amendment(plan_id,work_request_id,predecessor_plan_id,
    predecessor_plan_hash,base_version,amendment_hash,proposed_by_actor_id)
  values(v_plan.id,v_work.id,v_prior.id,v_prior.plan_hash,v_work.version,v_amendment_hash,v_actor);
  v_classification:=ops.heavy_build_classification(v_work.id,v_plan.scope_summary,
    v_plan.dependency_refs,v_plan.caps);
  v_reasons:=case when v_classification->>'tier'='heavy' then v_classification->'reasons'
    else jsonb_build_array('caller:explicit-heavy-contract') end;
  select * into v_admission from ops.record_sourced_heavy_build_admission(
    v_plan.id,p_work_request,p_base_version,v_reasons,p_heavy_build,
    p_proposed_by_actor_id,p_idempotency_key);
  return jsonb_build_object('ok',true,'replayed',false,
    'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state',v_work.state,'version',v_work.version),
    'plan',jsonb_build_object('id',v_plan.id,'ref',v_plan.plan_ref,'hash',v_plan.plan_hash,'version',v_plan.plan_version,
      'predecessor_ref',v_prior.plan_ref,'predecessor_hash',v_prior.plan_hash),
    'build_admission',jsonb_build_object('tier',v_admission.tier,'reasons',v_admission.classifier_reasons,
      'admission_ref',v_admission.admission_ref,'admission_hash',v_admission.admission_hash,
      'builder_session_ref',v_admission.builder_session_ref));
end $$;

create or replace function ops.ready_plan_amendment_census(p_work_request_id uuid,p_predecessor_plan_id uuid)
returns jsonb language sql volatile security definer set search_path=pg_catalog,ops
as $$
  with envelopes as (
    select e.* from ops.engineering_execution_envelope e
     where e.work_request_id=p_work_request_id and e.accepted_plan_id=p_predecessor_plan_id
       and not exists(select 1 from ops.engineering_execution_envelope n where n.supersedes_envelope_id=e.id)
  ), counts as (
    select
      (select count(*) from ops.canonical_ownership_lease l where l.work_request_id=p_work_request_id
        and l.accepted_plan_id=p_predecessor_plan_id and l.state='active' and l.expires_at>clock_timestamp()) active_leases,
      (select count(*) from envelopes e join ops.job j on j.id=e.job_id
        where j.state in ('queued','running','retry_wait','waiting_approval')) active_jobs,
      (select count(*) from envelopes e join ops.capability_agent_session s on s.id=e.agent_session_id
        where s.state in ('claimed','in_progress','verification')
          and coalesce(s.lease_expires_at,'infinity'::timestamptz)>clock_timestamp()) active_sessions,
      (select count(*) from ops.canonical_ownership_runtime_session rs
        where rs.work_request_id=p_work_request_id and rs.accepted_plan_id=p_predecessor_plan_id
          and rs.state='active' and rs.expires_at>clock_timestamp()) active_runtime_sessions,
      (select count(*) from envelopes e where not exists(select 1 from ops.engineering_slice_receipt r where r.envelope_id=e.id)) unreceipted_envelopes,
      (select coalesce(sum(jsonb_array_length(sp.plan->'slices')),0)
         from ops.engineering_slice_plan sp
        where sp.work_request_id=p_work_request_id and sp.accepted_plan_id=p_predecessor_plan_id
          and jsonb_typeof(sp.plan->'slices')='array') predecessor_slices,
      (select count(*) from ops.ready_plan_amendment_slice_compatibility m
         join ops.ready_plan_amendment a on a.plan_id=m.successor_plan_id
        where a.work_request_id=p_work_request_id and a.predecessor_plan_id=p_predecessor_plan_id)
        mapped_slices,
      (select count(*) from ops.engineering_slice_plan sp
        where sp.work_request_id=p_work_request_id and sp.accepted_plan_id=p_predecessor_plan_id
          and jsonb_typeof(sp.plan->'slices') is distinct from 'array') malformed_slice_plans
  ) select jsonb_build_object('active_leases',active_leases,'active_jobs',active_jobs,
      'active_sessions',active_sessions,'active_runtime_sessions',active_runtime_sessions,
      'unreceipted_envelopes',unreceipted_envelopes,
      'predecessor_slices',predecessor_slices,'mapped_slices',mapped_slices,
      'malformed_slice_plans',malformed_slice_plans,
      'compatibility_complete',predecessor_slices=mapped_slices and malformed_slice_plans=0,
      'safe',active_leases=0 and active_jobs=0 and active_sessions=0 and active_runtime_sessions=0
        and unreceipted_envelopes=0 and predecessor_slices=mapped_slices
        and malformed_slice_plans=0)
    from counts;
$$;

create or replace function ops.sourced_heavy_build_review_target(
  p_work_request text,p_plan_hash text,p_admission_hash text
) returns table(work_request_id uuid,ref text,plan_id uuid,admission_id uuid,
  admission_ref text,builder_session_ref text)
language sql stable security definer set search_path=pg_catalog,ops
as $$
  select w.id,w.ref,p.id,a.id,a.admission_ref,a.builder_session_ref
    from ops.work_request w
    join ops.sourced_work_request_plan p on p.work_request_id=w.id
    join ops.heavy_build_admission_revision a on a.plan_id=p.id
   where w.ref=p_work_request and p.plan_hash=p_plan_hash
     and a.admission_hash=p_admission_hash
     and a.version=(select max(latest.version) from ops.heavy_build_admission_revision latest
                     where latest.plan_id=p.id)
     and w.organization_tenant_id='carr-internal'
     and (w.state='triaged' or (w.state='ready' and exists(
       select 1 from ops.ready_plan_amendment amendment
        where amendment.plan_id=p.id and amendment.work_request_id=w.id
          and amendment.base_version=w.version)));
$$;

create or replace function ops.review_ready_plan_amendment_internal(
  p_work_request text,p_plan_hash text,p_admission_hash text,
  p_reviewer_actor_id uuid,p_verdict text,p_reviewer_session_ref text,
  p_review_summary text,p_evidence_refs jsonb,p_gaps jsonb,p_idempotency_key uuid
) returns table(work_request_id uuid,ref text,plan_id uuid,admission_ref text,
  admission_hash text,review_ref text,review_hash text,verdict text,
  reviewer_session_ref text,replayed boolean)
language plpgsql set search_path=pg_catalog,ops,public
as $$
declare v_work ops.work_request%rowtype; v_plan ops.sourced_work_request_plan%rowtype;
        v_admission ops.heavy_build_admission_revision%rowtype;
        v_review ops.heavy_build_plan_review%rowtype; v_actor public.actor%rowtype;
        v_version integer; v_preimage jsonb; v_digest text;
begin
  if coalesce(p_work_request,'')!~'^WR-[0-9]{1,12}$'
     or coalesce(p_plan_hash,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_admission_hash,'')!~'^sha256:[0-9a-f]{64}$'
     or p_reviewer_actor_id is null or p_idempotency_key is null
     or p_verdict not in ('pass','fail')
     or coalesce(p_reviewer_session_ref,'')!~'^session:[a-z0-9][a-z0-9:._/-]{8,199}$'
     or char_length(btrim(coalesce(p_review_summary,''))) not between 20 and 1000
     or jsonb_typeof(p_evidence_refs) is distinct from 'array'
     or jsonb_array_length(p_evidence_refs) not between 1 and 12
     or exists(select 1 from jsonb_array_elements(p_evidence_refs) item
       where jsonb_typeof(item)<>'string' or item#>>'{}'!~'^safe:[a-z0-9][a-z0-9:_./-]*$')
     or exists(select item#>>'{}' from jsonb_array_elements(p_evidence_refs) item
       group by item#>>'{}' having count(*)>1)
     or jsonb_typeof(p_gaps) is distinct from 'array' or jsonb_array_length(p_gaps)>12
     or exists(select 1 from jsonb_array_elements(p_gaps) item where jsonb_typeof(item)<>'string'
       or char_length(btrim(item#>>'{}')) not between 10 and 500)
     or (p_verdict='pass' and jsonb_array_length(p_gaps)<>0)
     or (p_verdict='fail' and jsonb_array_length(p_gaps)=0) then
    raise exception 'ready-plan amendment review requires exact hashes, fresh session, verdict-consistent gaps, evidence, actor, and idempotency key';
  end if;
  select * into v_actor from public.actor where id=p_reviewer_actor_id and active for share;
  if not found then raise exception 'ready-plan amendment reviewer actor is not active'; end if;
  perform pg_advisory_xact_lock(hashtextextended('heavy-build-review:'||p_idempotency_key,0));
  select * into v_review from ops.heavy_build_plan_review
   where idempotency_key=p_idempotency_key for share;
  if found then
    select * into v_admission from ops.heavy_build_admission_revision where id=v_review.admission_id;
    select * into v_plan from ops.sourced_work_request_plan where id=v_admission.plan_id;
    select * into v_work from ops.work_request where id=v_admission.work_request_id;
    if v_work.ref is distinct from p_work_request or v_plan.plan_hash is distinct from p_plan_hash
       or v_admission.admission_hash is distinct from p_admission_hash
       or v_review.reviewer_actor_id is distinct from p_reviewer_actor_id
       or v_review.verdict is distinct from p_verdict
       or v_review.reviewer_session_ref is distinct from p_reviewer_session_ref
       or v_review.review_summary is distinct from btrim(p_review_summary)
       or v_review.evidence_refs is distinct from p_evidence_refs
       or v_review.gaps is distinct from p_gaps then
      raise exception 'idempotency key already names a different ready-plan amendment review';
    end if;
    return query select v_work.id,v_work.ref,v_plan.id,v_admission.admission_ref,
      v_admission.admission_hash,v_review.review_ref,v_review.review_hash,
      v_review.verdict,v_review.reviewer_session_ref,true;
    return;
  end if;
  select * into v_work from ops.work_request
   where ref=p_work_request and state='ready' for share;
  select * into v_plan from ops.sourced_work_request_plan
   where work_request_id=v_work.id and plan_hash=p_plan_hash for share;
  if v_work.id is null or v_plan.id is null or not exists(
    select 1 from ops.ready_plan_amendment amendment where amendment.plan_id=v_plan.id
      and amendment.work_request_id=v_work.id and amendment.base_version=v_work.version) then
    raise exception 'exact current ready-plan amendment review target not found';
  end if;
  select * into v_admission from ops.heavy_build_admission_revision
   where plan_id=v_plan.id and admission_hash=p_admission_hash
     and version=(select max(latest.version) from ops.heavy_build_admission_revision latest
                   where latest.plan_id=v_plan.id) for share;
  if not found then raise exception 'exact current ready-plan amendment review target not found'; end if;
  if p_reviewer_session_ref=v_admission.builder_session_ref then
    raise exception 'ready-plan amendment review requires a fresh session distinct from the builder context';
  end if;
  select coalesce(max(version),0)+1 into v_version from ops.heavy_build_plan_review
   where admission_id=v_admission.id;
  v_preimage:=jsonb_build_object('contract','carr-heavy-build-review/v1',
    'admission_id',v_admission.id,'admission_hash',v_admission.admission_hash,
    'plan_hash',v_plan.plan_hash,'verdict',p_verdict,
    'reviewer_actor_id',p_reviewer_actor_id,'reviewer_session_ref',p_reviewer_session_ref,
    'review_summary',btrim(p_review_summary),'evidence_refs',p_evidence_refs,
    'gaps',p_gaps,'version',v_version);
  v_digest:=ops.heavy_build_digest(v_preimage);
  insert into ops.heavy_build_plan_review(review_ref,admission_id,version,idempotency_key,
    verdict,reviewer_actor_id,reviewer_session_ref,review_summary,evidence_refs,gaps,review_hash)
  values('HBR-'||substr(v_digest,8,12)||'-v'||v_version,v_admission.id,v_version,
    p_idempotency_key,p_verdict,p_reviewer_actor_id,p_reviewer_session_ref,
    btrim(p_review_summary),p_evidence_refs,p_gaps,v_digest)
  returning * into v_review;
  return query select v_work.id,v_work.ref,v_plan.id,v_admission.admission_ref,
    v_admission.admission_hash,v_review.review_ref,v_review.review_hash,
    v_review.verdict,v_review.reviewer_session_ref,false;
end $$;

create or replace function ops.review_sourced_heavy_build_plan(
  p_work_request text,p_plan_hash text,p_admission_hash text,
  p_reviewer_actor_id uuid,p_verdict text,p_reviewer_session_ref text,
  p_review_summary text,p_evidence_refs jsonb,p_gaps jsonb,p_idempotency_key uuid
) returns table(work_request_id uuid,ref text,plan_id uuid,admission_ref text,
  admission_hash text,review_ref text,review_hash text,verdict text,
  reviewer_session_ref text,replayed boolean)
language plpgsql security definer set search_path=pg_catalog,ops
as $$
begin
  if exists(select 1 from ops.work_request w
    join ops.sourced_work_request_plan p on p.work_request_id=w.id
    join ops.ready_plan_amendment a on a.plan_id=p.id
    where w.ref=p_work_request and p.plan_hash=p_plan_hash) then
    return query select * from ops.review_ready_plan_amendment_internal(
      p_work_request,p_plan_hash,p_admission_hash,p_reviewer_actor_id,p_verdict,
      p_reviewer_session_ref,p_review_summary,p_evidence_refs,p_gaps,p_idempotency_key);
  else
    return query select * from ops.review_sourced_heavy_build_plan_v1(
      p_work_request,p_plan_hash,p_admission_hash,p_reviewer_actor_id,p_verdict,
      p_reviewer_session_ref,p_review_summary,p_evidence_refs,p_gaps,p_idempotency_key);
  end if;
end $$;

create or replace function ops.accept_ready_plan_amendment(
  p_work_request text,p_base_version integer,p_plan_hash text,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare v_slug text; v_actor uuid; v_work ops.work_request%rowtype;
        v_plan ops.sourced_work_request_plan%rowtype; v_prior ops.sourced_work_request_plan%rowtype;
        v_amend ops.ready_plan_amendment%rowtype; v_accept ops.ready_plan_amendment_acceptance_receipt%rowtype;
        v_plan_accept ops.sourced_work_request_plan_acceptance_receipt%rowtype;
        v_admission ops.heavy_build_admission_revision%rowtype; v_review ops.heavy_build_plan_review%rowtype;
        v_census jsonb; v_notice_count integer; v_expected_slices integer; v_mapped_slices integer;
begin
  v_slug:=ops.authority_actor_slug();
  select id into v_actor from public.actor where slug=v_slug and active and kind='human' for share;
  if v_actor is null then return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_authority_required'); end if;
  if p_idempotency_key is null or p_base_version is null or p_base_version<1
     or coalesce(p_plan_hash,'')!~'^sha256:[0-9a-f]{64}$' then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_accept_input_invalid');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('ready-plan-amendment-accept:'||p_work_request,0));
  select * into v_accept from ops.ready_plan_amendment_acceptance_receipt where idempotency_key=p_idempotency_key;
  if found then
    select * into v_work from ops.work_request where id=v_accept.work_request_id;
    select * into v_plan from ops.sourced_work_request_plan where id=v_accept.successor_plan_id;
    select * into v_prior from ops.sourced_work_request_plan where id=v_accept.predecessor_plan_id;
    if v_work.ref is distinct from p_work_request or v_accept.base_version<>p_base_version or v_plan.plan_hash<>p_plan_hash then
      return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_accept_idempotency_conflict');
    end if;
    return jsonb_build_object('ok',true,'replayed',true,
      'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state','ready','version',v_accept.result_version),
      'prior_plan',jsonb_build_object('ref',v_prior.plan_ref,'hash',v_prior.plan_hash),
      'successor_plan',jsonb_build_object('ref',v_plan.plan_ref,'hash',v_plan.plan_hash));
  end if;
  select * into v_work from ops.work_request where ref=p_work_request for update;
  select p.* into v_plan from ops.sourced_work_request_plan p
   where p.work_request_id=v_work.id and p.plan_hash=p_plan_hash for share of p;
  select a.* into v_amend from ops.ready_plan_amendment a where a.plan_id=v_plan.id for share of a;
  select * into v_prior from ops.sourced_work_request_plan where id=v_amend.predecessor_plan_id for share;
  if v_work.id is null or v_work.state<>'ready' or v_work.version<>p_base_version
     or v_plan.id is null or v_amend.base_version<>v_work.version
     or v_prior.plan_hash<>v_amend.predecessor_plan_hash
     or not exists(select 1 from ops.sourced_work_request_plan_acceptance_receipt ar
       where ar.work_request_id=v_work.id and ar.plan_id=v_prior.id and ar.result_version=v_work.version)
     or ops.engineering_contract_stale(v_plan.plan_ref,v_plan.plan_hash,null) then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_accept_stale');
  end if;
  select * into v_admission from ops.heavy_build_admission_revision where plan_id=v_plan.id order by version desc limit 1;
  select * into v_review from ops.heavy_build_plan_review where admission_id=v_admission.id order by version desc limit 1;
  if v_admission.id is null or v_review.id is null or v_review.verdict<>'pass'
     or v_review.reviewer_actor_id=v_actor then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_fresh_review_required');
  end if;
  insert into ops.ready_plan_amendment_slice_compatibility(
    successor_plan_id,predecessor_slice_plan_id,predecessor_plan_digest,
    slice_ref,predecessor_slice_digest)
  select v_plan.id,sp.id,sp.plan_digest,slice->>'slice_ref',
    'sha256:'||encode(public.digest(
      ops.guidance_import_canonical_json(slice),'sha256'),'hex')
    from ops.engineering_slice_plan sp
    cross join lateral jsonb_array_elements(sp.plan->'slices') slice
   where sp.work_request_id=v_work.id and sp.accepted_plan_id=v_prior.id
     and jsonb_typeof(sp.plan->'slices')='array'
  on conflict do nothing;
  select coalesce(sum(jsonb_array_length(sp.plan->'slices')),0) into v_expected_slices
    from ops.engineering_slice_plan sp
   where sp.work_request_id=v_work.id and sp.accepted_plan_id=v_prior.id
     and jsonb_typeof(sp.plan->'slices')='array';
  select count(*) into v_mapped_slices
    from ops.ready_plan_amendment_slice_compatibility
   where successor_plan_id=v_plan.id;
  if v_mapped_slices<>v_expected_slices then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_compatibility_incomplete',
      'expected_slices',v_expected_slices,'mapped_slices',v_mapped_slices);
  end if;
  v_census:=ops.ready_plan_amendment_census(v_work.id,v_prior.id);
  if coalesce((v_census->>'safe')::boolean,false) is not true then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_active_work','census',v_census);
  end if;
  insert into ops.sourced_work_request_plan_acceptance_receipt(work_request_id,plan_id,idempotency_key,
    base_version,plan_hash,accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
  values(v_work.id,v_plan.id,p_idempotency_key,v_work.version,v_plan.plan_hash,v_actor,v_work.version+1,
    v_work.shape_fixed_surface_ref,v_work.shape_rationale) returning * into v_plan_accept;
  insert into ops.sourced_work_request_plan_shape_binding_receipt(plan_acceptance_receipt_id,work_request_id,
    disposition,fixed_surface_ref,rationale,decided_by_actor_id,decided_at)
  values(v_plan_accept.id,v_work.id,v_work.shape_disposition,v_work.shape_fixed_surface_ref,
    v_work.shape_rationale,v_work.shape_decided_by_actor_id,v_work.shape_decided_at);
  insert into ops.ready_plan_amendment_acceptance_receipt(work_request_id,predecessor_plan_id,
    successor_plan_id,plan_acceptance_receipt_id,idempotency_key,base_version,result_version,
    accepted_by_actor_id,accepted_at,census)
  values(v_work.id,v_prior.id,v_plan.id,v_plan_accept.id,p_idempotency_key,v_work.version,
    v_work.version+1,v_actor,v_plan_accept.accepted_at,v_census) returning * into v_accept;
  update ops.work_request set version=v_accept.result_version,updated_at=now() where id=v_work.id;
  insert into ops.ready_plan_amendment_notice(acceptance_receipt_id,actor_id)
    select v_accept.id,a.id from public.actor a where a.active on conflict do nothing;
  get diagnostics v_notice_count=row_count;
  return jsonb_build_object('ok',true,'replayed',false,
    'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state',v_work.state,'version',v_accept.result_version),
    'prior_plan',jsonb_build_object('ref',v_prior.plan_ref,'hash',v_prior.plan_hash),
    'successor_plan',jsonb_build_object('ref',v_plan.plan_ref,'hash',v_plan.plan_hash),
    'notice_count',v_notice_count,'census',v_census);
end $$;

-- Extend the sourced Work Request invariant with exactly one receipt-backed
-- ready->ready successor transition.  All original branches are retained.
do $work_request_successor$
declare
  v_definition text;
  v_marker text:='  -- CAPTURED -> DECLINED or SUPERSEDED, receipt-backed like every other arm.';
  v_branch text:=$ready_branch$
  -- A ready plan may move only to an independently accepted successor on the
  -- same Work Request.  No business field changes; the version bump makes all
  -- predecessor envelopes, slice plans, leases, and feedback bindings stale.
  if old.state = 'ready'
     and new.state = 'ready'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['version','updated_at']) is not distinct from
         (to_jsonb(old) - array['version','updated_at'])
     and exists (
       select 1 from ops.ready_plan_amendment_acceptance_receipt a
       join ops.sourced_work_request_plan_acceptance_receipt ar
         on ar.id = a.plan_acceptance_receipt_id
        where a.work_request_id = old.id and a.base_version = old.version
          and a.result_version = new.version and ar.plan_id = a.successor_plan_id
          and ar.base_version = old.version and ar.result_version = new.version
     ) then
    return new;
  end if;
$ready_branch$;
begin
  select pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
    raise exception 'sourced Work Request lifecycle predecessor shape drifted';
  end if;
  execute replace(v_definition,v_marker,v_branch||E'\n'||v_marker);
end $work_request_successor$;

create or replace function ops.discover_ready_plan_amendments(p_after_notice_id bigint,p_limit integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_slug text; v_limit integer:=least(greatest(coalesce(p_limit,50),1),100);
        v_items jsonb; v_next bigint; v_more boolean;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  if p_after_notice_id is not null and p_after_notice_id<0 then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_discovery_cursor_invalid');
  end if;
  select slug into v_slug from public.actor where id=v_actor;
  select coalesce(jsonb_agg(item order by notice_id),'[]'::jsonb),max(notice_id) into v_items,v_next
    from (select n.id notice_id,jsonb_build_object('notice_id',n.id,'work_request_ref',w.ref,
      'prior_plan_ref',prior.plan_ref,'prior_plan_hash',prior.plan_hash,
      'successor_plan_ref',next.plan_ref,'successor_plan_hash',next.plan_hash,
      'accepted_at',a.accepted_at,'acknowledged_at',ack.acknowledged_at) item
      from ops.ready_plan_amendment_notice n
      join ops.ready_plan_amendment_acceptance_receipt a on a.id=n.acceptance_receipt_id
      join ops.work_request w on w.id=a.work_request_id
      join ops.sourced_work_request_plan prior on prior.id=a.predecessor_plan_id
      join ops.sourced_work_request_plan next on next.id=a.successor_plan_id
      left join ops.ready_plan_amendment_ack ack on ack.notice_id=n.id
      where n.actor_id=v_actor and n.id>coalesce(p_after_notice_id,0)
      order by n.id limit v_limit) page;
  select exists(select 1 from ops.ready_plan_amendment_notice n
    where n.actor_id=v_actor and n.id>coalesce(v_next,p_after_notice_id,0)) into v_more;
  return jsonb_build_object('ok',true,'actor',v_slug,'items',v_items,
    'next_after_notice_id',v_next,'has_more',v_more);
end $$;

create or replace function ops.acknowledge_ready_plan_amendment(p_notice_id bigint,p_idempotency_key uuid)
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_row ops.ready_plan_amendment_ack%rowtype; v_notice ops.ready_plan_amendment_notice%rowtype;
        v_work text; v_plan text;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  if p_notice_id is null or p_notice_id<1 or p_idempotency_key is null then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_ack_input_invalid');
  end if;
  select * into v_row from ops.ready_plan_amendment_ack where idempotency_key=p_idempotency_key;
  if found then
    if v_row.notice_id<>p_notice_id or v_row.actor_id<>v_actor then
      return jsonb_build_object('ok',false,'reason_id','ready_plan_ack_idempotency_conflict'); end if;
    select w.ref,p.plan_ref into v_work,v_plan from ops.ready_plan_amendment_notice n
      join ops.ready_plan_amendment_acceptance_receipt a on a.id=n.acceptance_receipt_id
      join ops.work_request w on w.id=a.work_request_id
      join ops.sourced_work_request_plan p on p.id=a.successor_plan_id where n.id=p_notice_id;
    return jsonb_build_object('ok',true,'replayed',true,'notice_id',p_notice_id,
      'work_request_ref',v_work,'plan_ref',v_plan,'acknowledged_at',v_row.acknowledged_at);
  end if;
  select * into v_notice from ops.ready_plan_amendment_notice where id=p_notice_id and actor_id=v_actor for share;
  if not found then return jsonb_build_object('ok',false,'reason_id','ready_plan_notice_not_found'); end if;
  insert into ops.ready_plan_amendment_ack(notice_id,actor_id,idempotency_key)
  values(p_notice_id,v_actor,p_idempotency_key) returning * into v_row;
  select w.ref,p.plan_ref into v_work,v_plan from ops.ready_plan_amendment_acceptance_receipt a
    join ops.work_request w on w.id=a.work_request_id
    join ops.sourced_work_request_plan p on p.id=a.successor_plan_id
    where a.id=v_notice.acceptance_receipt_id;
  return jsonb_build_object('ok',true,'replayed',false,'notice_id',p_notice_id,
    'work_request_ref',v_work,'plan_ref',v_plan,'acknowledged_at',v_row.acknowledged_at);
end $$;

revoke all on table ops.engineering_stale_contract_fence,
  ops.canonical_ownership_runtime_session,ops.ready_plan_amendment,
  ops.ready_plan_amendment_slice_compatibility,
  ops.ready_plan_amendment_acceptance_receipt,ops.ready_plan_amendment_notice,
  ops.ready_plan_amendment_ack from public,carr_reader,carr_writer,carr_jobs,carr_authority;

revoke all on function ops.engineering_contract_stale(text,text,text),
  ops.engineering_stale_contract_fence_immutable(),
  ops.reject_stale_engineering_migration(),
  ops.engineering_envelope_currentness_v1(uuid,uuid),
  ops.engineering_envelope_currentness(uuid,uuid),
  ops.canonical_ownership_currentness_v1(uuid,integer,text,uuid,text,uuid,text,text),
  ops.canonical_ownership_currentness(uuid,integer,text,uuid,text,uuid,text,text),
  ops.source_merge_authority_projection_v1(uuid,text,text,integer),
  ops.source_merge_authority_projection(uuid,text,text,integer),
  ops.canonical_ownership_runtime_principal_valid(),
  ops.mint_canonical_ownership_runtime_session(uuid,uuid,uuid,integer,text,text,timestamptz,uuid),
  ops.canonical_ownership_trusted_context(),
  ops.canonical_ownership_context_v1(),ops.canonical_ownership_context(),
  ops.effective_ready_plan(text),
  ops.ready_plan_amendment_rows_immutable(),
  ops.ready_plan_amendment_slice_plan_guard(),
  ops.record_ready_plan_amendment_admission_internal(uuid,text,integer,jsonb,jsonb,uuid,uuid),
  ops.record_sourced_heavy_build_admission_v1(uuid,text,integer,jsonb,jsonb,uuid,uuid),
  ops.record_sourced_heavy_build_admission(uuid,text,integer,jsonb,jsonb,uuid,uuid),
  ops.sourced_heavy_build_review_target_v1(text,text,text),
  ops.sourced_heavy_build_review_target(text,text,text),
  ops.review_ready_plan_amendment_internal(text,text,text,uuid,text,text,text,jsonb,jsonb,uuid),
  ops.review_sourced_heavy_build_plan_v1(text,text,text,uuid,text,text,text,jsonb,jsonb,uuid),
  ops.review_sourced_heavy_build_plan(text,text,text,uuid,text,text,text,jsonb,jsonb,uuid),
  ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,jsonb,uuid,uuid),
  ops.ready_plan_amendment_census(uuid,uuid),
  ops.accept_ready_plan_amendment(text,integer,text,uuid),
  ops.discover_ready_plan_amendments(bigint,integer),
  ops.acknowledge_ready_plan_amendment(bigint,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

grant execute on function ops.engineering_contract_stale(text,text,text),
  ops.effective_ready_plan(text),ops.discover_ready_plan_amendments(bigint,integer)
  to carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.engineering_envelope_currentness(uuid,uuid)
  to carr_reader,carr_writer;
grant execute on function ops.source_merge_authority_projection(uuid,text,text,integer)
  to carr_reader;
grant execute on function
  ops.mint_canonical_ownership_runtime_session(uuid,uuid,uuid,integer,text,text,timestamptz,uuid),
  ops.canonical_ownership_trusted_context()
  to carr_ownership_issuer;
revoke all on function
  ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer),
  ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb),
  ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer),
  ops.release_canonical_ownership_lease(uuid,uuid,bigint)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_ownership_issuer;
grant execute on function
  ops.record_sourced_heavy_build_admission(uuid,text,integer,jsonb,jsonb,uuid,uuid),
  ops.sourced_heavy_build_review_target(text,text,text),
  ops.review_sourced_heavy_build_plan(text,text,text,uuid,text,text,text,jsonb,jsonb,uuid),
  ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,jsonb,uuid,uuid),
  ops.acknowledge_ready_plan_amendment(bigint,uuid)
  to carr_writer;
grant execute on function ops.accept_ready_plan_amendment(text,integer,text,uuid)
  to carr_authority;
