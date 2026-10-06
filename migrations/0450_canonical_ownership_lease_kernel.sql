-- 0450_canonical_ownership_lease_kernel.sql
-- doctrine: carr-production-maturity-baseline
--
-- Dark, tenant-scoped ownership coordination for assurance slices.  This is
-- deliberately not ops.job leasing, a SIEP lane lock, a capability session,
-- an Engineering envelope, or the business-domain public.lease table.  No
-- runtime role receives EXECUTE here: A3 must install trusted session/host
-- context producers and grants before this kernel can authorize an action.

begin;

create sequence ops.canonical_ownership_fencing_generation
  as bigint minvalue 1 start with 1 increment by 1 no cycle;

create table ops.canonical_ownership_lease (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null check (btrim(organization_tenant_id) <> ''),
  holder_actor_id uuid not null references public.actor(id) on delete restrict,
  holder_actor_slug text not null check (btrim(holder_actor_slug) <> ''),
  holder_session_ref text not null check (holder_session_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  holder_host_ref text not null check (holder_host_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  lease_token uuid not null unique default gen_random_uuid(),
  fencing_generation bigint not null unique
    default nextval('ops.canonical_ownership_fencing_generation'),
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  work_request_version integer not null check (work_request_version > 0),
  work_request_digest text not null check (work_request_digest ~ '^sha256:[0-9a-f]{64}$'),
  accepted_plan_id uuid not null references ops.sourced_work_request_plan(id) on delete restrict,
  accepted_plan_digest text not null check (accepted_plan_digest ~ '^sha256:[0-9a-f]{64}$'),
  slice_plan_id uuid not null references ops.engineering_slice_plan(id) on delete restrict,
  slice_plan_digest text not null check (slice_plan_digest ~ '^sha256:[0-9a-f]{64}$'),
  slice_ref text not null check (slice_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  subject_envelope_id uuid not null references ops.engineering_execution_envelope(id) on delete restrict,
  contract_digest text not null check (contract_digest ~ '^sha256:[0-9a-f]{64}$'),
  state text not null default 'active'
    check (state in ('active','released','expired','replaced')),
  acquired_at timestamptz not null,
  renewed_at timestamptz,
  expires_at timestamptz not null,
  released_at timestamptz,
  replaced_at timestamptz,
  superseded_by_lease_id uuid references ops.canonical_ownership_lease(id) on delete restrict,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  check (expires_at > acquired_at),
  check ((state='released') = (released_at is not null)),
  check ((state='replaced') = (replaced_at is not null)),
  check ((superseded_by_lease_id is null) = (state<>'replaced'))
);

create index canonical_ownership_lease_active_tenant_idx
  on ops.canonical_ownership_lease(organization_tenant_id,expires_at,id)
  where state='active';

create table ops.canonical_ownership_claim (
  lease_id uuid not null references ops.canonical_ownership_lease(id) on delete restrict,
  organization_tenant_id text not null,
  claim_kind text not null check (claim_kind in ('path','resource')),
  claim_value text not null check (btrim(claim_value)<>''),
  claim_mode text not null check (claim_mode in ('file','tree','resource')),
  operation text not null check (operation in ('write','rename_source','rename_destination','claim')),
  created_at timestamptz not null default now(),
  primary key (lease_id,claim_kind,claim_value,claim_mode,operation),
  foreign key (organization_tenant_id,lease_id)
    references ops.canonical_ownership_lease(organization_tenant_id,id) on delete restrict,
  check ((claim_kind='path' and claim_mode in ('file','tree') and operation<>'claim')
      or (claim_kind='resource' and claim_mode='resource' and operation='claim'))
);

create index canonical_ownership_claim_collision_idx
  on ops.canonical_ownership_claim(organization_tenant_id,claim_kind,lower(claim_value));

create table ops.canonical_ownership_dependency (
  lease_id uuid not null references ops.canonical_ownership_lease(id) on delete restrict,
  organization_tenant_id text not null,
  dependency_slice_ref text not null check (dependency_slice_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  required_state text not null check (required_state in ('completed','independently_verified')),
  observed_envelope_id uuid not null references ops.engineering_execution_envelope(id) on delete restrict,
  observed_receipt_id uuid not null references ops.engineering_slice_receipt(id) on delete restrict,
  observed_reviewer_fact_id uuid references ops.engineering_reviewer_fact(id) on delete restrict,
  evaluated_at timestamptz not null,
  created_at timestamptz not null default now(),
  primary key (lease_id,dependency_slice_ref),
  foreign key (organization_tenant_id,lease_id)
    references ops.canonical_ownership_lease(organization_tenant_id,id) on delete restrict,
  check ((required_state='completed' and observed_reviewer_fact_id is null)
      or (required_state='independently_verified' and observed_reviewer_fact_id is not null))
);

create table ops.canonical_ownership_lease_event (
  id bigint generated always as identity primary key,
  organization_tenant_id text not null,
  lease_id uuid not null references ops.canonical_ownership_lease(id) on delete restrict,
  event_kind text not null check (event_kind in ('acquired','renewed','released','expired','replaced')),
  fencing_generation bigint not null check (fencing_generation > 0),
  actor_id uuid not null references public.actor(id) on delete restrict,
  session_ref text not null,
  host_ref text not null,
  cause jsonb not null check (jsonb_typeof(cause)='object' and not (cause ? 'lease_token')),
  occurred_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (organization_tenant_id,lease_id)
    references ops.canonical_ownership_lease(organization_tenant_id,id) on delete restrict
);

comment on table ops.canonical_ownership_lease is
  'Dark canonical session/work ownership lease. Runtime use remains revoked until A3 installs trusted session and execution-host context.';
comment on table ops.canonical_ownership_claim is
  'Exact path/resource claims. Path collision is computed case-insensitively by slash-segment ancestry; rename endpoints are ordinary claims.';
comment on table ops.canonical_ownership_dependency is
  'Acquisition-time evidence pointers only; every protected boundary rereads current Engineering lineage.';
comment on table ops.canonical_ownership_lease_event is
  'Append-only token-redacted lifecycle evidence. Expiry cleanup never deletes lease history.';

create or replace function ops.canonical_ownership_refusal(
  p_code text,p_causal_object text,p_expected jsonb,p_actual jsonb
) returns jsonb language plpgsql immutable set search_path=pg_catalog,ops
as $a2_refusal$
begin
  if p_code is null or p_code<>all(array[
    'IDENTITY_CONTEXT_MISSING','IDENTITY_CONTEXT_INVALID','INPUT_INVALID',
    'PATH_INVALID','PATH_CASE_ALIAS','RESOURCE_INVALID','DUPLICATE_CLAIM',
    'WORK_REQUEST_NOT_FOUND','WORK_REQUEST_BINDING_STALE','SLICE_PLAN_NOT_FOUND',
    'SLICE_PLAN_BINDING_STALE','DEPENDENCY_MISSING','DEPENDENCY_UNSATISFIED',
    'FOREIGN_LEASE_COLLISION','LEASE_NOT_FOUND','LEASE_HOLDER_MISMATCH',
    'LEASE_TOKEN_STALE','FENCING_GENERATION_STALE','LEASE_RELEASED',
    'LEASE_REPLACED','LEASE_EXPIRED','LEASE_CLAIMS_MISMATCH'
  ]::text[]) then
    raise exception 'canonical ownership refusal code is not registered';
  end if;
  return jsonb_build_object('ok',false,'refusal',jsonb_build_object(
    'code',p_code,'causal_object',p_causal_object,'expected',p_expected,'actual',p_actual));
end $a2_refusal$;

create or replace function ops.canonical_ownership_path_valid(p_path text)
returns boolean language sql immutable strict set search_path=pg_catalog
as $$
  select p_path<>''
     and length(p_path)=octet_length(p_path)
     and p_path ~ '^[!-~]+$'
     and left(p_path,1)<>'/' and right(p_path,1)<>'/'
     and strpos(p_path,chr(92))=0
     and strpos(p_path,'*')=0 and strpos(p_path,'?')=0
     and strpos(p_path,'[')=0 and strpos(p_path,']')=0
     and strpos(p_path,'{')=0 and strpos(p_path,'}')=0 and strpos(p_path,'!')=0
     and not exists (
       select 1 from unnest(string_to_array(p_path,'/')) component
        where component in ('','.','..')
     );
$$;

create or replace function ops.canonical_ownership_resource_valid(p_resource text)
returns boolean language sql immutable strict set search_path=pg_catalog
as $$
  select length(p_resource)=octet_length(p_resource)
     and p_resource ~ '^[A-Za-z][A-Za-z0-9._:/-]{2,239}$';
$$;

create or replace function ops.canonical_ownership_path_case_alias(p_left text,p_right text)
returns boolean language sql immutable strict set search_path=pg_catalog
as $$
  with parts as (
    select string_to_array(p_left,'/') l,string_to_array(p_right,'/') r
  )
  select exists (
    select 1 from parts,generate_series(1,least(cardinality(l),cardinality(r))) n
     where lower(l[n])=lower(r[n]) and l[n]<>r[n]
  );
$$;

create or replace function ops.canonical_ownership_paths_overlap(
  p_left text,p_left_mode text,p_right text,p_right_mode text
) returns boolean language sql immutable strict set search_path=pg_catalog
as $$
  select lower(p_left)=lower(p_right)
      or (p_left_mode='tree' and left(lower(p_right),length(p_left)+1)=lower(p_left)||'/')
      or (p_right_mode='tree' and left(lower(p_left),length(p_right)+1)=lower(p_right)||'/');
$$;

create or replace function ops.canonical_ownership_context()
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public
as $$
declare tenant text:=nullif(btrim(current_setting('carr.organization_tenant_id',true)),'');
        actor_slug text:=nullif(btrim(current_setting('carr.acting_actor_slug',true)),'');
        session_ref text:=nullif(btrim(current_setting('carr.ownership_session_id',true)),'');
        host_ref text:=nullif(btrim(current_setting('carr.execution_host_id',true)),'');
        actor_id uuid;
begin
  if tenant is null or actor_slug is null or session_ref is null or host_ref is null then
    return ops.canonical_ownership_refusal('IDENTITY_CONTEXT_MISSING','identity_context',
      '["organization_tenant_id","acting_actor_slug","ownership_session_id","execution_host_id"]'::jsonb,
      jsonb_build_object('organization_tenant_id',tenant is not null,'acting_actor_slug',actor_slug is not null,
        'ownership_session_id',session_ref is not null,'execution_host_id',host_ref is not null));
  end if;
  if session_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or host_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' then
    return ops.canonical_ownership_refusal('IDENTITY_CONTEXT_INVALID','identity_context',
      '"canonical server identity refs"'::jsonb,'"invalid"'::jsonb);
  end if;
  select id into actor_id from public.actor where slug=actor_slug and active;
  if not found then
    return ops.canonical_ownership_refusal('IDENTITY_CONTEXT_INVALID','identity_context.actor',
      '"active canonical actor"'::jsonb,
      jsonb_build_object('reason','unknown_or_inactive','value_redacted',true));
  end if;
  return jsonb_build_object('ok',true,'tenant',tenant,'actor_id',actor_id,'actor_slug',actor_slug,
    'session_ref',session_ref,'host_ref',host_ref);
end $$;

-- Lock the same mutable authority in the same order as the established
-- Engineering receipt/review/successor writers. Tenant/lease locks are A2-only
-- and are taken by the caller before this helper.
create or replace function ops.canonical_ownership_lock_lineage(
  p_holder_actor_id uuid,p_slice_plan_id uuid,p_slice_refs text[]
) returns void language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare lock_key text;
begin
  perform 1 from ops.capability_agent_session s
   where s.id in (select e.agent_session_id from ops.engineering_execution_envelope e
                   where e.slice_plan_id=p_slice_plan_id and e.slice_ref=any(p_slice_refs))
   order by s.id for share;
  perform 1 from public.actor a
   where a.id in (
     select s.executor_actor_id from ops.capability_agent_session s
      join ops.engineering_execution_envelope e on e.agent_session_id=s.id
     where e.slice_plan_id=p_slice_plan_id and e.slice_ref=any(p_slice_refs)
     union
     select r.reviewer_actor_id from ops.engineering_reviewer_fact r
      join ops.engineering_slice_receipt receipt on receipt.id=r.receipt_id
      join ops.engineering_execution_envelope e on e.id=receipt.envelope_id
     where e.slice_plan_id=p_slice_plan_id and e.slice_ref=any(p_slice_refs)
     union
     select p_holder_actor_id
   ) order by a.id for share;
  for lock_key in
    select 'engineering-envelope:'||p_slice_plan_id::text||':'||slice_ref
      from unnest(p_slice_refs) slice_ref order by 1
  loop
    perform pg_advisory_xact_lock(hashtextextended(lock_key,0));
  end loop;
  perform 1 from ops.engineering_execution_envelope e
   where e.slice_plan_id=p_slice_plan_id and e.slice_ref=any(p_slice_refs)
   order by e.id for key share;
  perform 1 from ops.engineering_slice_plan sp where sp.id=p_slice_plan_id order by sp.id for key share;
  perform 1 from ops.work_request w
   where w.id=(select sp.work_request_id from ops.engineering_slice_plan sp where sp.id=p_slice_plan_id)
   order by w.id for share;
  perform 1 from ops.engineering_slice_receipt r
   where r.envelope_id in (select e.id from ops.engineering_execution_envelope e
                            where e.slice_plan_id=p_slice_plan_id and e.slice_ref=any(p_slice_refs))
   order by r.created_at,r.id for key share;
  perform 1 from ops.engineering_reviewer_fact f
   where f.receipt_id in (
     select r.id from ops.engineering_slice_receipt r
      join ops.engineering_execution_envelope e on e.id=r.envelope_id
     where e.slice_plan_id=p_slice_plan_id and e.slice_ref=any(p_slice_refs))
   order by f.created_at,f.id for key share;
end $$;

create or replace function ops.canonical_ownership_currentness(
  p_work_request_id uuid,p_work_request_version integer,p_work_request_digest text,
  p_accepted_plan_id uuid,p_accepted_plan_digest text,p_slice_plan_id uuid,
  p_slice_plan_digest text,p_slice_ref text
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare work_row ops.work_request%rowtype; plan_row ops.engineering_slice_plan%rowtype;
        source jsonb; canonical_deps jsonb;
        tenant text:=current_setting('carr.organization_tenant_id',true);
begin
  select * into work_row from ops.work_request where id=p_work_request_id;
  if not found or (work_row.organization_tenant_id is not null
     and work_row.organization_tenant_id is distinct from tenant) then
    return ops.canonical_ownership_refusal('WORK_REQUEST_NOT_FOUND','work_request',
      '"tenant-visible canonical work request"'::jsonb,'"absent"'::jsonb);
  end if;
  source:=ops.engineering_admission_source(work_row.ref);
  if source is null or work_row.version is distinct from p_work_request_version
     or source->'work_request'->>'id' is distinct from 'wr:'||p_work_request_id::text
     or (source->'work_request'->>'version')::integer is distinct from p_work_request_version
     or source->'work_request'->>'canonical_record_digest' is distinct from p_work_request_digest
     or (source->'accepted_plan'->>'record_id')::uuid is distinct from p_accepted_plan_id
     or source->'accepted_plan'->>'digest' is distinct from p_accepted_plan_digest then
    return ops.canonical_ownership_refusal('WORK_REQUEST_BINDING_STALE','work_request.binding',
      '"submitted binding matches canonical source"'::jsonb,
      jsonb_build_object('reason','binding_stale','value_redacted',true));
  end if;
  select * into plan_row from ops.engineering_slice_plan where id=p_slice_plan_id;
  if not found then
    return ops.canonical_ownership_refusal('SLICE_PLAN_NOT_FOUND','slice_plan',
      '"canonical slice plan"'::jsonb,'"absent"'::jsonb);
  end if;
  if plan_row.work_request_id is distinct from p_work_request_id
     or plan_row.accepted_plan_id is distinct from p_accepted_plan_id
     or plan_row.work_request_version is distinct from p_work_request_version
     or plan_row.accepted_plan_hash is distinct from p_accepted_plan_digest
     or plan_row.plan_digest is distinct from p_slice_plan_digest then
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.binding',
      '"submitted binding matches canonical slice plan"'::jsonb,
      jsonb_build_object('reason','binding_stale','value_redacted',true));
  end if;
  canonical_deps:=ops.canonical_ownership_plan_dependencies(p_slice_plan_id,p_slice_ref);
  if not coalesce((canonical_deps->>'ok')::boolean,false) then
    return canonical_deps;
  end if;
  return jsonb_build_object('ok',true);
end $$;

create or replace function ops.canonical_ownership_plan_dependencies(
  p_slice_plan_id uuid,p_slice_ref text
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare plan_row ops.engineering_slice_plan%rowtype; slice jsonb; slice_count integer;
        dependencies jsonb;
begin
  select * into plan_row from ops.engineering_slice_plan where id=p_slice_plan_id;
  if not found then
    return ops.canonical_ownership_refusal('SLICE_PLAN_NOT_FOUND','slice_plan',
      '"canonical slice plan"'::jsonb,'"absent"'::jsonb);
  end if;
  if jsonb_typeof(plan_row.plan->'slices') is distinct from 'array' then
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.dependencies',
      '"one typed canonical dependency set"'::jsonb,
      jsonb_build_object('reason','malformed_plan','value_redacted',true));
  end if;
  select count(*),min(value::text)::jsonb into slice_count,slice
    from jsonb_array_elements(plan_row.plan->'slices')
   where value->>'slice_ref'=p_slice_ref;
  if slice_count<>1 or not coalesce(ops.engineering_receipt_exact_object(slice,array[
       'baseline_evidence_refs','concurrency_posture','declared_component_refs','declared_plan_step_refs',
       'declared_resource_refs','definition_of_done','dependency_refs','forbidden_change_refs','manual_qa_required',
       'objective','ordinal','planned_checks','release_requirement','risk_class','scope_boundary','slice_ref'
     ]),false)
     or not coalesce(ops.engineering_receipt_identifier_array(slice->'dependency_refs'),false) then
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.dependencies',
      '"one typed canonical dependency set"'::jsonb,
      jsonb_build_object('reason','malformed_dependencies','value_redacted',true));
  end if;
  select coalesce(jsonb_agg(jsonb_build_object(
           'slice_ref',dependency_ref,'required_state','independently_verified')
           order by dependency_ref),'[]'::jsonb)
    into dependencies
    from jsonb_array_elements_text(slice->'dependency_refs') dependency_ref;
  return jsonb_build_object('ok',true,'dependencies',dependencies);
end $$;

create or replace function ops.canonical_ownership_dependency_state(
  p_work_request_id uuid,p_slice_plan_id uuid,p_slice_ref text,p_required_state text
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare leaf_count integer; leaf ops.engineering_execution_envelope%rowtype;
        receipt ops.engineering_slice_receipt%rowtype; review ops.engineering_reviewer_fact%rowtype;
        plan_row ops.engineering_slice_plan%rowtype; receipt_slice jsonb;
        receipt_slice_count integer; receipt_attempt integer;
        session_executor uuid; session_slug text;
        reviewer_slug text; executor_session text; deviation_refs text[];
begin
  select count(*) into leaf_count from ops.engineering_execution_envelope e
   where e.work_request_id=p_work_request_id and e.slice_plan_id=p_slice_plan_id and e.slice_ref=p_slice_ref
     and not exists (select 1 from ops.engineering_execution_envelope successor
                      where successor.supersedes_envelope_id=e.id);
  if leaf_count<>1 then
    return ops.canonical_ownership_refusal('DEPENDENCY_MISSING','dependency',
      '"exactly one unsuperseded envelope leaf"'::jsonb,
      jsonb_build_object('slice_ref',p_slice_ref,'leaf_count',leaf_count));
  end if;
  select e.* into leaf from ops.engineering_execution_envelope e
   where e.work_request_id=p_work_request_id and e.slice_plan_id=p_slice_plan_id and e.slice_ref=p_slice_ref
     and not exists (select 1 from ops.engineering_execution_envelope successor
                      where successor.supersedes_envelope_id=e.id);
  select r.* into receipt from ops.engineering_slice_receipt r where r.envelope_id=leaf.id
   order by r.created_at desc,r.id desc limit 1;
  select sp.* into plan_row from ops.engineering_slice_plan sp
   where sp.id=p_slice_plan_id;
  select count(*),min(value::text)::jsonb
    into receipt_slice_count,receipt_slice
    from jsonb_array_elements(case
      when jsonb_typeof(plan_row.plan->'slices')='array'
      then plan_row.plan->'slices' else '[]'::jsonb end)
   where value->>'slice_ref'=p_slice_ref;
  select ja.attempt into receipt_attempt from ops.job_attempt ja
   where ja.id=receipt.job_attempt_id and ja.job_id=leaf.job_id;
  select s.executor_actor_id,a.slug into session_executor,session_slug
    from ops.capability_agent_session s
    join public.actor a on a.id=s.executor_actor_id and a.active
   where s.id=leaf.agent_session_id and s.work_request_id=p_work_request_id;
  if receipt.id is null or receipt.outcome is distinct from 'claimed_complete'
     or receipt.work_request_id is distinct from p_work_request_id
     or receipt.envelope_id is distinct from leaf.id
     or receipt.slice_ref is distinct from p_slice_ref
     or plan_row.id is null
     or plan_row.work_request_id is distinct from p_work_request_id
     or leaf.slice_plan_id is distinct from plan_row.id
     or leaf.envelope->>'envelope_id'
          is distinct from 'env:'||leaf.id::text
     or leaf.envelope#>>'{request,job_ref}'
          is distinct from 'job:'||leaf.job_id::text
     or leaf.envelope#>>'{agent_session,id}'
          is distinct from 'session:'||leaf.agent_session_id::text
     or receipt.executor_actor_id is distinct from session_executor
     or session_slug is null
     or receipt_attempt is null
     or receipt.attempt_id is distinct from ('attempt:'||receipt_attempt)
     or receipt_slice_count<>1
     or not coalesce(ops.engineering_receipt_exact_object(receipt.receipt,array[
          'actual_component_refs','actual_resource_refs','artifact_refs','attribution','attempt_id','checks',
          'deviations','envelope_digest','evidence_refs','executor_claim','independent_verification_required',
          'outcome','plan_digest','planned_component_refs','planned_resource_refs','reset_reconstruction',
          'schema_version','slice_ref','source_evidence'
        ]),false)
     or receipt.receipt->>'schema_version' is distinct from 'engineering-slice-receipt.v1'
     or receipt.receipt_digest is distinct from
        ('sha256:'||encode(public.digest(
          ops.guidance_import_canonical_json(receipt.receipt),'sha256'),'hex'))
     or receipt.receipt->>'outcome' is distinct from 'claimed_complete'
     or receipt.receipt->>'slice_ref' is distinct from p_slice_ref
     or receipt.receipt->>'attempt_id' is distinct from receipt.attempt_id
     or receipt.receipt->>'envelope_digest' is distinct from leaf.envelope_digest
     or receipt.receipt->>'plan_digest' is distinct from plan_row.plan_digest
     or receipt.receipt->'independent_verification_required' is distinct from 'true'::jsonb
     or not coalesce(ops.engineering_receipt_exact_object(
          receipt.receipt->'attribution',array['actor_ref','adapter_ref','session_ref']),false)
     or leaf.envelope#>>'{server_binding,identity,agent_principal_id}' is null
     or leaf.envelope#>>'{agent_session,id}' is null
     or leaf.envelope#>>'{server_binding,adapter,adapter_id}' is null
     or receipt.receipt#>>'{attribution,actor_ref}' is distinct from
        leaf.envelope#>>'{server_binding,identity,agent_principal_id}'
     or receipt.receipt#>>'{attribution,session_ref}' is distinct from
        leaf.envelope#>>'{agent_session,id}'
     or receipt.receipt#>>'{attribution,adapter_ref}' is distinct from
        leaf.envelope#>>'{server_binding,adapter,adapter_id}'
     or not coalesce(ops.engineering_receipt_exact_object(receipt_slice,array[
          'baseline_evidence_refs','concurrency_posture','declared_component_refs',
          'declared_plan_step_refs','declared_resource_refs','definition_of_done',
          'dependency_refs','forbidden_change_refs','manual_qa_required','objective',
          'ordinal','planned_checks','release_requirement','risk_class',
          'scope_boundary','slice_ref'
        ]),false)
     or receipt_slice->>'slice_ref' is distinct from p_slice_ref
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt_slice->'declared_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt_slice->'declared_component_refs'),false)
     or jsonb_typeof(receipt_slice->'planned_checks') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(receipt_slice->'planned_checks')='array'
                          then jsonb_array_length(receipt_slice->'planned_checks')>0
                          else false end,false)
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt_slice->'planned_checks')='array'
         then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
        where not coalesce(ops.engineering_receipt_exact_object(
                planned_check,array['check_ref','evidence_requirement',
                                    'failure_condition']),false)
           or not coalesce((planned_check->>'check_ref') ~
                '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or not coalesce(planned_check->>'evidence_requirement'=any(array[
                'redacted_evidence_required','metadata_only_sufficient']),false)
           or jsonb_typeof(planned_check->'failure_condition') is distinct from 'string'
           or not coalesce(btrim(planned_check->>'failure_condition')<>'',false)
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt_slice->'planned_checks')='array'
         then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
       group by planned_check->>'check_ref' having count(*)>1
     )
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'planned_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'actual_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'planned_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'actual_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'artifact_refs'),false)
     or not coalesce(ops.engineering_receipt_evidence_array(
          receipt.receipt->'evidence_refs'),false)
     or not coalesce(case
          when jsonb_typeof(receipt.receipt->'artifact_refs')='array'
          then jsonb_array_length(receipt.receipt->'artifact_refs')>0
          else false end,false)
     or not coalesce(case
          when jsonb_typeof(receipt.receipt->'evidence_refs')='array'
          then jsonb_array_length(receipt.receipt->'evidence_refs')>0
          else false end,false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          receipt.receipt->'planned_resource_refs',
          receipt_slice->'declared_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          receipt.receipt->'planned_component_refs',
          receipt_slice->'declared_component_refs'),false)
     or jsonb_typeof(receipt.receipt->'checks') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(receipt.receipt->'checks')='array'
                          then jsonb_array_length(receipt.receipt->'checks')>0
                          else false end,false)
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'checks')='array'
         then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
        where not coalesce(ops.engineering_receipt_exact_object(
                receipt_check,array['check_ref','evidence_refs','state']),false)
           or not coalesce((receipt_check->>'check_ref') ~
                '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or receipt_check->>'state' is distinct from 'passed'
           or not coalesce(ops.engineering_receipt_evidence_array(
                receipt_check->'evidence_refs'),false)
           or not coalesce(case
                when jsonb_typeof(receipt_check->'evidence_refs')='array'
                then jsonb_array_length(receipt_check->'evidence_refs')>0
                else false end,false)
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'checks')='array'
         then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
       group by receipt_check->>'check_ref' having count(*)>1
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'checks')='array'
         then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt_slice->'planned_checks')='array'
            then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
           where planned_check->>'check_ref'=receipt_check->>'check_ref')
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt_slice->'planned_checks')='array'
         then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt.receipt->'checks')='array'
            then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
           where receipt_check->>'check_ref'=planned_check->>'check_ref')
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'checks')='array'
         then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
       join jsonb_array_elements(case
         when jsonb_typeof(receipt_slice->'planned_checks')='array'
         then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
         on planned_check->>'check_ref'=receipt_check->>'check_ref'
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt_check->'evidence_refs')='array'
            then receipt_check->'evidence_refs' else '[]'::jsonb end) evidence
           where evidence->>'redaction_class'=case
             when planned_check->>'evidence_requirement'='redacted_evidence_required'
             then 'redacted_evidence' else 'metadata_only' end)
     )
     or jsonb_typeof(receipt.receipt->'deviations') is distinct from 'array'
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'deviations')='array'
         then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
        where not coalesce(ops.engineering_receipt_exact_object(deviation,array[
                'category','deviation_ref','evidence_refs','impact','out_of_scope_component_refs',
                'out_of_scope_resource_refs','plan_revision_required','reason','review_state'
              ]),false)
           or not coalesce((deviation->>'deviation_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or exists (select 1 from unnest(array['category','reason','impact']) field
                       where jsonb_typeof(deviation->field) is distinct from 'string'
                          or not coalesce(btrim(deviation->>field)<>'',false))
           or jsonb_typeof(deviation->'plan_revision_required') is distinct from 'boolean'
           or not coalesce(ops.engineering_receipt_evidence_array(deviation->'evidence_refs'),false)
           or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_resource_refs'),false)
           or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_component_refs'),false)
           or deviation->>'review_state' is distinct from 'resolved'
           or deviation->'plan_revision_required' is distinct from 'false'::jsonb
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'deviations')='array'
         then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
       group by deviation->>'deviation_ref' having count(*)>1
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'actual_resource_refs')='array'
         then receipt.receipt->'actual_resource_refs' else '[]'::jsonb end) actual_ref
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt_slice->'declared_resource_refs')='array'
            then receipt_slice->'declared_resource_refs' else '[]'::jsonb end) declared_ref
           where declared_ref=actual_ref)
          and not exists (
            select 1 from jsonb_array_elements(case
              when jsonb_typeof(receipt.receipt->'deviations')='array'
              then receipt.receipt->'deviations' else '[]'::jsonb end) deviation,
              jsonb_array_elements(case
                when jsonb_typeof(deviation->'out_of_scope_resource_refs')='array'
                then deviation->'out_of_scope_resource_refs' else '[]'::jsonb end) approved_ref
             where deviation->>'review_state'='resolved' and approved_ref=actual_ref)
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'actual_component_refs')='array'
         then receipt.receipt->'actual_component_refs' else '[]'::jsonb end) actual_ref
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt_slice->'declared_component_refs')='array'
            then receipt_slice->'declared_component_refs' else '[]'::jsonb end) declared_ref
           where declared_ref=actual_ref)
          and not exists (
            select 1 from jsonb_array_elements(case
              when jsonb_typeof(receipt.receipt->'deviations')='array'
              then receipt.receipt->'deviations' else '[]'::jsonb end) deviation,
              jsonb_array_elements(case
                when jsonb_typeof(deviation->'out_of_scope_component_refs')='array'
                then deviation->'out_of_scope_component_refs' else '[]'::jsonb end) approved_ref
             where deviation->>'review_state'='resolved' and approved_ref=actual_ref)
     )
     or not coalesce(ops.engineering_receipt_exact_object(
          receipt.receipt->'source_evidence',
          array['branch_ref','evidence_refs','source_sha','worktree_ref']),false)
     or exists (
       select 1 from unnest(array['worktree_ref','branch_ref','source_sha']) field
        where jsonb_typeof(receipt.receipt->'source_evidence'->field)
                is distinct from 'string'
           or not coalesce(btrim(
                receipt.receipt->'source_evidence'->>field)<>'',false)
           or (field<>'source_sha' and not coalesce(
                (receipt.receipt->'source_evidence'->>field) ~
                '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false))
     )
     or not coalesce(ops.engineering_receipt_evidence_array(
          receipt.receipt->'source_evidence'->'evidence_refs'),false)
     or not coalesce(ops.engineering_receipt_exact_object(
          receipt.receipt->'reset_reconstruction',array[
            'fresh_session','inherited_transcript_used',
            'reconstruction_free','remediation_action']),false)
     or receipt.receipt->'reset_reconstruction'->'fresh_session'
          is distinct from 'true'::jsonb
     or receipt.receipt->'reset_reconstruction'->'inherited_transcript_used'
          is distinct from 'false'::jsonb
     or jsonb_typeof(receipt.receipt->'reset_reconstruction'
          ->'reconstruction_free') is distinct from 'boolean'
     or (receipt.receipt->'reset_reconstruction'->'reconstruction_free'='false'::jsonb
         and (jsonb_typeof(receipt.receipt->'reset_reconstruction'
                ->'remediation_action') is distinct from 'string'
              or not coalesce(btrim(receipt.receipt->'reset_reconstruction'
                ->>'remediation_action')<>'',false)))
     or (receipt.receipt->'reset_reconstruction'->'reconstruction_free'='true'::jsonb
         and receipt.receipt->'reset_reconstruction'->'remediation_action'
                is distinct from 'null'::jsonb
         and (jsonb_typeof(receipt.receipt->'reset_reconstruction'
                ->'remediation_action') is distinct from 'string'
              or not coalesce(btrim(receipt.receipt->'reset_reconstruction'
                ->>'remediation_action')<>'',false)))
     or not coalesce(ops.engineering_receipt_exact_object(
          receipt.receipt->'executor_claim',
          array['claim_state','claimed_at','claimed_by']),false)
     or receipt.receipt->'executor_claim'->>'claim_state'
          is distinct from 'executor_claim'
     or receipt.receipt->'executor_claim'->>'claimed_by'
          is distinct from session_slug
     or jsonb_typeof(receipt.receipt->'executor_claim'->'claimed_at')
          is distinct from 'string'
     or not coalesce(btrim(receipt.receipt->'executor_claim'
          ->>'claimed_at')<>'',false) then
    return ops.canonical_ownership_refusal('DEPENDENCY_UNSATISFIED','dependency',
      to_jsonb(p_required_state),jsonb_build_object(
        'slice_ref',p_slice_ref,'receipt_id',receipt.id,'outcome',receipt.outcome));
  end if;
  if p_required_state='completed' then
    return jsonb_build_object('ok',true,'envelope_id',leaf.id,'receipt_id',receipt.id,'reviewer_fact_id',null);
  end if;
  select f.* into review from ops.engineering_reviewer_fact f where f.receipt_id=receipt.id
   order by f.created_at desc,f.id desc limit 1;
  select a.slug into reviewer_slug from public.actor a
   where a.id=review.reviewer_actor_id and a.active;
  executor_session:=receipt.receipt#>>'{attribution,session_ref}';
  select coalesce(array_agg(d->>'deviation_ref' order by d->>'deviation_ref'),'{}'::text[])
    into deviation_refs from jsonb_array_elements(receipt.receipt->'deviations') d;
  if review.id is null or review.state is distinct from 'passed'
     or review.contract_version is distinct from 'engineering-review.v1'
     or review.work_request_id is distinct from receipt.work_request_id
     or review.slice_ref is distinct from receipt.slice_ref
     or reviewer_slug is null
     or not coalesce(ops.engineering_receipt_exact_object(review.fact,array[
          'attempt_id','evidence_refs','is_independent','resolved_deviation_refs',
          'reviewed_deviation_refs','reviewer_ref','session_ref','slice_ref','state'
        ]),false)
     or review.reviewer_actor_id=receipt.executor_actor_id
     or review.fact->>'state' is distinct from 'passed' or review.fact->'is_independent' is distinct from 'true'::jsonb
     or review.fact->>'attempt_id' is distinct from receipt.attempt_id or review.fact->>'slice_ref' is distinct from p_slice_ref
     or not coalesce(review.fact->>'reviewer_ref'=any(array[
          reviewer_slug,'actor:'||reviewer_slug,'reviewer:'||reviewer_slug
        ]),false)
     or review.reviewer_session_ref is distinct from review.fact->>'session_ref'
     or review.reviewer_session_ref=executor_session
     or not coalesce(ops.engineering_receipt_evidence_array(review.fact->'evidence_refs'),false)
     or not coalesce(case when jsonb_typeof(review.fact->'evidence_refs')='array'
                          then jsonb_array_length(review.fact->'evidence_refs')>0
                          else false end,false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          review.fact->'reviewed_deviation_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          review.fact->'resolved_deviation_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          review.fact->'reviewed_deviation_refs',to_jsonb(deviation_refs)),false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          review.fact->'resolved_deviation_refs',to_jsonb(deviation_refs)),false) then
    return ops.canonical_ownership_refusal('DEPENDENCY_UNSATISFIED','dependency',
      '"independently_verified"'::jsonb,jsonb_build_object(
        'slice_ref',p_slice_ref,'receipt_id',receipt.id,
        'reviewer_fact_id',review.id,'review_state',review.state));
  end if;
  return jsonb_build_object('ok',true,'envelope_id',leaf.id,'receipt_id',receipt.id,'reviewer_fact_id',review.id);
end $$;

create or replace function ops.acquire_canonical_ownership_lease(
  p_work_request_id uuid,p_work_request_version integer,p_work_request_digest text,
  p_accepted_plan_id uuid,p_accepted_plan_digest text,p_slice_plan_id uuid,
  p_slice_plan_digest text,p_slice_ref text,p_contract_digest text,
  p_path_claims jsonb,p_resource_claims jsonb,p_dependencies jsonb,p_ttl_seconds integer default 900
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare context jsonb:=ops.canonical_ownership_context(); claim jsonb; other jsonb;
        dep jsonb; currentness jsonb; dep_state jsonb; canonical_deps jsonb;
        submitted_deps jsonb; collision record; replaced record;
        lease_row ops.canonical_ownership_lease%rowtype; now_at timestamptz;
        refs text[]; subject_id uuid; subject_count integer;
        claim_ordinal bigint; other_ordinal bigint; dep_ordinal bigint;
        duplicate_kind text; mismatch_ordinal integer;
begin
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  if p_work_request_id is null or p_work_request_version is null or p_work_request_version<1
     or p_work_request_digest is null or p_work_request_digest !~ '^sha256:[0-9a-f]{64}$'
     or p_accepted_plan_id is null or p_accepted_plan_digest is null
     or p_accepted_plan_digest !~ '^sha256:[0-9a-f]{64}$'
     or p_slice_plan_id is null or p_slice_plan_digest is null
     or p_slice_plan_digest !~ '^sha256:[0-9a-f]{64}$'
     or p_slice_ref is null or p_slice_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_ttl_seconds is null or p_ttl_seconds<30 or p_ttl_seconds>1800
     or jsonb_typeof(p_path_claims) is distinct from 'array'
     or jsonb_typeof(p_resource_claims) is distinct from 'array'
     or jsonb_typeof(p_dependencies) is distinct from 'array'
     or p_contract_digest is null or p_contract_digest !~ '^sha256:[0-9a-f]{64}$' then
    return ops.canonical_ownership_refusal('INPUT_INVALID','lease.input','"bounded exact A2 input"'::jsonb,'"invalid"'::jsonb);
  end if;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_path_claims) with ordinality
  loop
    if not coalesce(ops.engineering_receipt_exact_object(claim,array['path','mode','operation']),false)
       or claim->>'mode' not in ('file','tree') or claim->>'operation' not in ('write','rename_source','rename_destination')
       or not coalesce(ops.canonical_ownership_path_valid(claim->>'path'),false) then
      return ops.canonical_ownership_refusal('PATH_INVALID','path_claim',
        '"exact A1a path claim"'::jsonb,
        jsonb_build_object('ordinal',claim_ordinal,'field','path_claim',
          'reason','invalid','value_redacted',true));
    end if;
  end loop;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_path_claims) with ordinality
  loop
    for other,other_ordinal in
      select value,ordinality from jsonb_array_elements(p_path_claims) with ordinality
    loop
      if claim_ordinal<other_ordinal
         and ops.canonical_ownership_path_case_alias(claim->>'path',other->>'path') then
        return ops.canonical_ownership_refusal('PATH_CASE_ALIAS','path_claims',
          '"one canonical path case"'::jsonb,
          jsonb_build_object('left_ordinal',claim_ordinal,
            'right_ordinal',other_ordinal,'reason','case_alias','value_redacted',true));
      end if;
    end loop;
  end loop;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_resource_claims) with ordinality
  loop
    if not coalesce(ops.engineering_receipt_exact_object(claim,array['resource']),false)
       or not coalesce(ops.canonical_ownership_resource_valid(claim->>'resource'),false) then
      return ops.canonical_ownership_refusal('RESOURCE_INVALID','resource_claim',
        '"exact ASCII resource identifier"'::jsonb,
        jsonb_build_object('ordinal',claim_ordinal,'field','resource_claim',
          'reason','invalid','value_redacted',true));
    end if;
  end loop;
  select min(second.ordinality) into claim_ordinal
    from jsonb_array_elements(p_path_claims) with ordinality first(value,ordinality)
    join jsonb_array_elements(p_path_claims) with ordinality second(value,ordinality)
      on first.value=second.value and first.ordinality<second.ordinality;
  duplicate_kind:='path';
  if claim_ordinal is null then
    select min(second.ordinality) into claim_ordinal
      from jsonb_array_elements(p_resource_claims) with ordinality first(value,ordinality)
      join jsonb_array_elements(p_resource_claims) with ordinality second(value,ordinality)
        on first.value=second.value and first.ordinality<second.ordinality;
    duplicate_kind:='resource';
  end if;
  if claim_ordinal is not null then
    return ops.canonical_ownership_refusal('DUPLICATE_CLAIM','claims',
      '"unique claims"'::jsonb,jsonb_build_object(
        'claim_kind',duplicate_kind,'duplicate_ordinal',claim_ordinal));
  end if;
  for dep,dep_ordinal in
    select value,ordinality from jsonb_array_elements(p_dependencies) with ordinality
  loop
    if not coalesce(ops.engineering_receipt_exact_object(dep,array['slice_ref','required_state']),false)
       or dep->>'required_state' not in ('completed','independently_verified')
       or (dep->>'slice_ref') !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' then
      return ops.canonical_ownership_refusal('INPUT_INVALID','dependency',
        '"exact dependency object"'::jsonb,
        jsonb_build_object('ordinal',dep_ordinal,'field','dependency',
          'reason','invalid','value_redacted',true));
    end if;
  end loop;
  select min(second.ordinality) into dep_ordinal
    from jsonb_array_elements(p_dependencies) with ordinality first(value,ordinality)
    join jsonb_array_elements(p_dependencies) with ordinality second(value,ordinality)
      on first.value->>'slice_ref'=second.value->>'slice_ref'
     and first.ordinality<second.ordinality;
  if dep_ordinal is not null then
    return ops.canonical_ownership_refusal('INPUT_INVALID','dependencies',
      '"unique slice_ref values"'::jsonb,
      jsonb_build_object('duplicate_ordinal',dep_ordinal));
  end if;

  perform pg_advisory_xact_lock(hashtextextended('canonical-ownership:'||(context->>'tenant'),0));
  perform 1 from ops.canonical_ownership_lease l where l.organization_tenant_id=context->>'tenant' order by l.id for update;
  canonical_deps:=ops.canonical_ownership_plan_dependencies(p_slice_plan_id,p_slice_ref);
  select array_agg(ref order by ref) into refs from (
    select p_slice_ref ref union select value->>'slice_ref'
      from jsonb_array_elements(case when coalesce((canonical_deps->>'ok')::boolean,false)
        then canonical_deps->'dependencies' else '[]'::jsonb end)
  ) q;
  perform ops.canonical_ownership_lock_lineage(
    (context->>'actor_id')::uuid,p_slice_plan_id,refs);
  perform 1 from public.actor a
   where a.id=(context->>'actor_id')::uuid
     and a.slug=context->>'actor_slug' and a.active;
  if not found then
    return ops.canonical_ownership_refusal('IDENTITY_CONTEXT_INVALID','identity_context.actor',
      '"active canonical actor"'::jsonb,
      jsonb_build_object('reason','unknown_or_inactive','value_redacted',true));
  end if;
  now_at:=clock_timestamp();
  currentness:=ops.canonical_ownership_currentness(p_work_request_id,p_work_request_version,p_work_request_digest,
    p_accepted_plan_id,p_accepted_plan_digest,p_slice_plan_id,p_slice_plan_digest,p_slice_ref);
  if not coalesce((currentness->>'ok')::boolean,false) then return currentness; end if;
  canonical_deps:=ops.canonical_ownership_plan_dependencies(p_slice_plan_id,p_slice_ref);
  if not coalesce((canonical_deps->>'ok')::boolean,false) then return canonical_deps; end if;
  select coalesce(jsonb_agg(value order by value->>'slice_ref'),'[]'::jsonb)
    into submitted_deps from jsonb_array_elements(p_dependencies);
  if submitted_deps is distinct from canonical_deps->'dependencies' then
    select min(ordinal) into mismatch_ordinal
      from generate_series(1,greatest(jsonb_array_length(submitted_deps),
           jsonb_array_length(canonical_deps->'dependencies'))) ordinal
     where submitted_deps->(ordinal-1)
           is distinct from canonical_deps->'dependencies'->(ordinal-1);
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.dependencies',
      '"exact canonical dependency snapshot"'::jsonb,
      jsonb_build_object('reason','snapshot_mismatch',
        'submitted_count',jsonb_array_length(submitted_deps),
        'canonical_count',jsonb_array_length(canonical_deps->'dependencies'),
        'first_mismatch_ordinal',mismatch_ordinal,'value_redacted',true));
  end if;
  select count(*),(array_agg(id order by id))[1] into subject_count,subject_id
    from ops.engineering_execution_envelope e
   where e.work_request_id=p_work_request_id and e.slice_plan_id=p_slice_plan_id
     and e.slice_ref=p_slice_ref
     and not exists (select 1 from ops.engineering_execution_envelope successor
                      where successor.supersedes_envelope_id=e.id);
  if subject_count<>1 then
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.subject',
      '"one current subject envelope"'::jsonb,
      jsonb_build_object('reason','subject_lineage_stale','value_redacted',true));
  end if;
  for dep in select value from jsonb_array_elements(canonical_deps->'dependencies') loop
    dep_state:=ops.canonical_ownership_dependency_state(p_work_request_id,p_slice_plan_id,dep->>'slice_ref',dep->>'required_state');
    if not coalesce((dep_state->>'ok')::boolean,false) then return dep_state; end if;
  end loop;

  select l.id lease_id,c.claim_kind,c.claim_value,c.claim_mode,c.operation,
         submitted.submitted_ordinal,
         encode(digest(c.claim_value,'sha256'),'hex') claim_digest into collision
    from ops.canonical_ownership_lease l
    join ops.canonical_ownership_claim c on c.lease_id=l.id
    join lateral (
      select r.ordinality submitted_ordinal
        from jsonb_array_elements(p_resource_claims) with ordinality r(value,ordinality)
       where c.claim_kind='resource' and r.value->>'resource'=c.claim_value
      union all
      select p.ordinality submitted_ordinal
        from jsonb_array_elements(p_path_claims) with ordinality p(value,ordinality)
       where c.claim_kind='path' and ops.canonical_ownership_paths_overlap(
         c.claim_value,c.claim_mode,p.value->>'path',p.value->>'mode')
    ) submitted on true
   where l.organization_tenant_id=context->>'tenant' and l.state='active' and l.expires_at>now_at
   order by l.id,c.claim_kind,c.claim_value,submitted.submitted_ordinal limit 1;
  if found then
    return ops.canonical_ownership_refusal('FOREIGN_LEASE_COLLISION','lease.collision',
      '"unclaimed scope"'::jsonb,
      jsonb_build_object('conflicting_lease_id',collision.lease_id,
        'claim_kind',collision.claim_kind,
        'submitted_ordinal',collision.submitted_ordinal,
        'claim_digest',collision.claim_digest,
        'reason','already_claimed','value_redacted',true));
  end if;

  insert into ops.canonical_ownership_lease(
    organization_tenant_id,holder_actor_id,holder_actor_slug,holder_session_ref,holder_host_ref,
    work_request_id,work_request_version,work_request_digest,accepted_plan_id,accepted_plan_digest,
    slice_plan_id,slice_plan_digest,slice_ref,subject_envelope_id,
    contract_digest,acquired_at,expires_at,created_at,updated_at)
  values(context->>'tenant',(context->>'actor_id')::uuid,context->>'actor_slug',context->>'session_ref',context->>'host_ref',
    p_work_request_id,p_work_request_version,p_work_request_digest,p_accepted_plan_id,p_accepted_plan_digest,
    p_slice_plan_id,p_slice_plan_digest,p_slice_ref,subject_id,
    p_contract_digest,now_at,now_at+make_interval(secs=>p_ttl_seconds),now_at,now_at)
  returning * into lease_row;

  for claim in select value from jsonb_array_elements(p_path_claims) loop
    insert into ops.canonical_ownership_claim values(lease_row.id,context->>'tenant','path',claim->>'path',claim->>'mode',claim->>'operation',now_at);
  end loop;
  for claim in select value from jsonb_array_elements(p_resource_claims) loop
    insert into ops.canonical_ownership_claim values(lease_row.id,context->>'tenant','resource',claim->>'resource','resource','claim',now_at);
  end loop;
  for dep in select value from jsonb_array_elements(canonical_deps->'dependencies') loop
    dep_state:=ops.canonical_ownership_dependency_state(p_work_request_id,p_slice_plan_id,dep->>'slice_ref',dep->>'required_state');
    insert into ops.canonical_ownership_dependency values(
      lease_row.id,context->>'tenant',dep->>'slice_ref',dep->>'required_state',
      (dep_state->>'envelope_id')::uuid,(dep_state->>'receipt_id')::uuid,
      nullif(dep_state->>'reviewer_fact_id','')::uuid,now_at,now_at);
  end loop;

  for replaced in
    select distinct l.organization_tenant_id,l.id,l.lease_token,l.fencing_generation,
      l.expires_at,l.state
      from ops.canonical_ownership_lease l join ops.canonical_ownership_claim c on c.lease_id=l.id
     where l.organization_tenant_id=context->>'tenant' and l.id<>lease_row.id and l.state in ('active','expired') and l.expires_at<=now_at
       and ((c.claim_kind='resource' and exists(select 1 from jsonb_array_elements(p_resource_claims) r where r->>'resource'=c.claim_value))
         or (c.claim_kind='path' and exists(select 1 from jsonb_array_elements(p_path_claims) p
               where ops.canonical_ownership_paths_overlap(c.claim_value,c.claim_mode,p->>'path',p->>'mode'))))
  loop
    update ops.canonical_ownership_lease set state='replaced',replaced_at=now_at,
      superseded_by_lease_id=lease_row.id,updated_at=now_at
     where organization_tenant_id=replaced.organization_tenant_id and id=replaced.id
       and lease_token=replaced.lease_token and fencing_generation=replaced.fencing_generation
       and expires_at=replaced.expires_at and state=replaced.state;
    if not found then raise exception 'canonical ownership predecessor fence changed under tenant lock'; end if;
    if replaced.state='active' then
      insert into ops.canonical_ownership_lease_event
        (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
         session_ref,host_ref,cause,occurred_at,created_at)
      values(replaced.organization_tenant_id,replaced.id,'expired',replaced.fencing_generation,
        (context->>'actor_id')::uuid,context->>'session_ref',context->>'host_ref',
        '{"reason":"observed_expired_during_reacquire"}'::jsonb,now_at,now_at);
    end if;
    insert into ops.canonical_ownership_lease_event
      (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
       session_ref,host_ref,cause,occurred_at,created_at)
    values(replaced.organization_tenant_id,replaced.id,'replaced',replaced.fencing_generation,
      (context->>'actor_id')::uuid,context->>'session_ref',context->>'host_ref',
      jsonb_build_object('superseded_by_lease_id',lease_row.id,
        'reason','expired_scope_reacquired'),now_at,now_at);
  end loop;
  insert into ops.canonical_ownership_lease_event
    (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
     session_ref,host_ref,cause,occurred_at,created_at)
  values(context->>'tenant',lease_row.id,'acquired',lease_row.fencing_generation,(context->>'actor_id')::uuid,
    context->>'session_ref',context->>'host_ref',
    jsonb_build_object('contract_digest',p_contract_digest),now_at,now_at);
  return jsonb_build_object('ok',true,'lease_id',lease_row.id,'lease_token',lease_row.lease_token,
    'fencing_generation',lease_row.fencing_generation,'expires_at',lease_row.expires_at);
end $$;

create or replace function ops.canonical_ownership_validate_live(
  p_lease_id uuid,p_lease_token uuid,p_fencing_generation bigint,p_check_currentness boolean default true
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare context jsonb:=ops.canonical_ownership_context(); lease_row ops.canonical_ownership_lease%rowtype;
        currentness jsonb; canonical_deps jsonb; stored_deps jsonb;
        dep record; dep_state jsonb; refs text[]; now_at timestamptz;
        current_subject uuid; subject_count integer;
begin
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  if p_lease_id is null or p_lease_token is null or p_fencing_generation is null or p_fencing_generation<1 then
    return ops.canonical_ownership_refusal('INPUT_INVALID','lease.binding','"lease id, token, positive generation"'::jsonb,'"invalid"'::jsonb);
  end if;
  perform pg_advisory_xact_lock(hashtextextended('canonical-ownership:'||(context->>'tenant'),0));
  select * into lease_row from ops.canonical_ownership_lease
   where id=p_lease_id and organization_tenant_id=context->>'tenant' for update;
  if not found then return ops.canonical_ownership_refusal('LEASE_NOT_FOUND','lease',
    '"tenant lease"'::jsonb,'"absent"'::jsonb); end if;
  if lease_row.holder_actor_id is distinct from (context->>'actor_id')::uuid
     or lease_row.holder_actor_slug is distinct from context->>'actor_slug'
     or lease_row.holder_session_ref is distinct from context->>'session_ref'
     or lease_row.holder_host_ref is distinct from context->>'host_ref' then
    return ops.canonical_ownership_refusal('LEASE_HOLDER_MISMATCH','lease.holder',
      '"acquiring actor, session, and host"'::jsonb,
      jsonb_build_object('actor_matches',lease_row.holder_actor_id=(context->>'actor_id')::uuid,
        'session_matches',lease_row.holder_session_ref=context->>'session_ref',
        'host_matches',lease_row.holder_host_ref=context->>'host_ref'));
  end if;
  if lease_row.lease_token<>p_lease_token then
    return ops.canonical_ownership_refusal('LEASE_TOKEN_STALE','lease.token',
      '"current lease token"'::jsonb,'"redacted"'::jsonb);
  end if;
  if lease_row.fencing_generation<>p_fencing_generation then
    return ops.canonical_ownership_refusal('FENCING_GENERATION_STALE','lease.fencing_generation',
      to_jsonb(lease_row.fencing_generation),to_jsonb(p_fencing_generation));
  end if;
  if lease_row.state='released' then return ops.canonical_ownership_refusal('LEASE_RELEASED','lease.lifecycle','"active"'::jsonb,'"released"'::jsonb); end if;
  if lease_row.state='replaced' then return ops.canonical_ownership_refusal('LEASE_REPLACED','lease.lifecycle','"active"'::jsonb,'"replaced"'::jsonb); end if;
  if lease_row.state='expired' then return ops.canonical_ownership_refusal('LEASE_EXPIRED','lease.lifecycle','"active and unexpired"'::jsonb,'"expired"'::jsonb); end if;
  select array_agg(ref order by ref) into refs from (
    select lease_row.slice_ref ref union
    select dependency_slice_ref from ops.canonical_ownership_dependency where lease_id=p_lease_id
  ) q;
  perform ops.canonical_ownership_lock_lineage(
    (context->>'actor_id')::uuid,lease_row.slice_plan_id,refs);
  perform 1 from public.actor a
   where a.id=(context->>'actor_id')::uuid
     and a.slug=context->>'actor_slug' and a.active;
  if not found then
    return ops.canonical_ownership_refusal('IDENTITY_CONTEXT_INVALID','identity_context.actor',
      '"active canonical actor"'::jsonb,
      jsonb_build_object('reason','unknown_or_inactive','value_redacted',true));
  end if;
  now_at:=clock_timestamp();
  if lease_row.expires_at<=now_at then
    return ops.canonical_ownership_refusal('LEASE_EXPIRED','lease.expiry',
      '"future expiry"'::jsonb,'"elapsed"'::jsonb);
  end if;
  if p_check_currentness then
    currentness:=ops.canonical_ownership_currentness(lease_row.work_request_id,lease_row.work_request_version,
      lease_row.work_request_digest,lease_row.accepted_plan_id,lease_row.accepted_plan_digest,
      lease_row.slice_plan_id,lease_row.slice_plan_digest,lease_row.slice_ref);
    if not coalesce((currentness->>'ok')::boolean,false) then return currentness; end if;
    select count(*),(array_agg(id order by id))[1] into subject_count,current_subject
      from ops.engineering_execution_envelope e
     where e.work_request_id=lease_row.work_request_id
       and e.slice_plan_id=lease_row.slice_plan_id and e.slice_ref=lease_row.slice_ref
       and not exists (select 1 from ops.engineering_execution_envelope successor
                        where successor.supersedes_envelope_id=e.id);
    if subject_count<>1 or current_subject is distinct from lease_row.subject_envelope_id then
      return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.subject',
        '"acquired subject envelope remains current"'::jsonb,
        jsonb_build_object('reason','subject_lineage_stale','value_redacted',true));
    end if;
    canonical_deps:=ops.canonical_ownership_plan_dependencies(lease_row.slice_plan_id,lease_row.slice_ref);
    if not coalesce((canonical_deps->>'ok')::boolean,false) then return canonical_deps; end if;
    select coalesce(jsonb_agg(jsonb_build_object('slice_ref',dependency_slice_ref,
             'required_state',required_state) order by dependency_slice_ref),'[]'::jsonb)
      into stored_deps from ops.canonical_ownership_dependency where lease_id=p_lease_id;
    if stored_deps is distinct from canonical_deps->'dependencies' then
      return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.dependencies',
        '"persisted dependencies match canonical plan"'::jsonb,
        jsonb_build_object('reason','canonical_drift','value_redacted',true));
    end if;
    for dep in select * from ops.canonical_ownership_dependency where lease_id=p_lease_id order by dependency_slice_ref loop
      dep_state:=ops.canonical_ownership_dependency_state(lease_row.work_request_id,lease_row.slice_plan_id,
        dep.dependency_slice_ref,dep.required_state);
      if not coalesce((dep_state->>'ok')::boolean,false) then return dep_state; end if;
    end loop;
  end if;
  return jsonb_build_object('ok',true,'lease_id',lease_row.id,'fencing_generation',lease_row.fencing_generation,
    'expires_at',lease_row.expires_at,'evaluated_at',now_at);
end $$;

create or replace function ops.check_canonical_ownership_lease(
  p_lease_id uuid,p_lease_token uuid,p_fencing_generation bigint,
  p_path_claims jsonb default '[]'::jsonb,p_resource_claims jsonb default '[]'::jsonb
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $a2_check$
declare context jsonb:=ops.canonical_ownership_context(); live jsonb; claim jsonb;
        claim_ordinal bigint; mismatch_kind text; mismatch_ordinal bigint;
begin
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  if jsonb_typeof(p_path_claims) is distinct from 'array'
     or jsonb_typeof(p_resource_claims) is distinct from 'array' then
    return ops.canonical_ownership_refusal('INPUT_INVALID','required_claims','"arrays"'::jsonb,'"invalid"'::jsonb);
  end if;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_path_claims) with ordinality
  loop
    if not coalesce(ops.engineering_receipt_exact_object(claim,array['path','mode','operation']),false)
       or claim->>'mode' not in ('file','tree') or claim->>'operation' not in ('write','rename_source','rename_destination')
       or not coalesce(ops.canonical_ownership_path_valid(claim->>'path'),false) then
      return ops.canonical_ownership_refusal('INPUT_INVALID','required_claims.path',
        '"exact path claim"'::jsonb,
        jsonb_build_object('ordinal',claim_ordinal,'field','path_claim',
          'reason','invalid','value_redacted',true));
    end if;
  end loop;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_resource_claims) with ordinality
  loop
    if not coalesce(ops.engineering_receipt_exact_object(claim,array['resource']),false)
       or not coalesce(ops.canonical_ownership_resource_valid(claim->>'resource'),false) then
      return ops.canonical_ownership_refusal('INPUT_INVALID','required_claims.resource',
        '"exact resource claim"'::jsonb,
        jsonb_build_object('ordinal',claim_ordinal,'field','resource_claim',
          'reason','invalid','value_redacted',true));
    end if;
  end loop;
  live:=ops.canonical_ownership_validate_live(p_lease_id,p_lease_token,p_fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_path_claims) with ordinality
  loop
    if not exists(select 1 from ops.canonical_ownership_claim c where c.lease_id=p_lease_id and c.claim_kind='path'
      and c.claim_value=claim->>'path' and c.claim_mode=claim->>'mode' and c.operation=claim->>'operation') then
      mismatch_kind:='path'; mismatch_ordinal:=claim_ordinal; exit;
    end if;
  end loop;
  if mismatch_ordinal is null then
    for claim,claim_ordinal in
      select value,ordinality from jsonb_array_elements(p_resource_claims) with ordinality
    loop
      if not exists(select 1 from ops.canonical_ownership_claim c where c.lease_id=p_lease_id and c.claim_kind='resource'
        and c.claim_value=claim->>'resource') then
        mismatch_kind:='resource'; mismatch_ordinal:=claim_ordinal; exit;
      end if;
    end loop;
  end if;
  if mismatch_ordinal is not null then return ops.canonical_ownership_refusal(
    'LEASE_CLAIMS_MISMATCH','lease.claims','"claimed scope"'::jsonb,
    jsonb_build_object('claim_kind',mismatch_kind,'submitted_ordinal',mismatch_ordinal,
      'reason','unowned_claim','value_redacted',true)); end if;
  return live;
end $a2_check$;

create or replace function ops.renew_canonical_ownership_lease(
  p_lease_id uuid,p_lease_token uuid,p_fencing_generation bigint,p_ttl_seconds integer default 900
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $a2_renew$
declare live jsonb; context jsonb:=ops.canonical_ownership_context(); now_at timestamptz; new_expiry timestamptz;
begin
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  if p_ttl_seconds is null or p_ttl_seconds<30 or p_ttl_seconds>1800 then return ops.canonical_ownership_refusal('INPUT_INVALID','lease.ttl','"30..1800 seconds"'::jsonb,to_jsonb(p_ttl_seconds)); end if;
  live:=ops.canonical_ownership_validate_live(p_lease_id,p_lease_token,p_fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  now_at:=(live->>'evaluated_at')::timestamptz; new_expiry:=now_at+make_interval(secs=>p_ttl_seconds);
  update ops.canonical_ownership_lease set renewed_at=now_at,expires_at=new_expiry,updated_at=now_at
   where id=p_lease_id and state='active' and lease_token=p_lease_token and fencing_generation=p_fencing_generation and expires_at>now_at;
  if not found then return ops.canonical_ownership_refusal('LEASE_EXPIRED','lease.expiry','"renewable live lease"'::jsonb,'"changed while renewing"'::jsonb); end if;
  insert into ops.canonical_ownership_lease_event
    (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
     session_ref,host_ref,cause,occurred_at,created_at)
  values(context->>'tenant',p_lease_id,'renewed',p_fencing_generation,(context->>'actor_id')::uuid,
    context->>'session_ref',context->>'host_ref',
    jsonb_build_object('expires_at',new_expiry),now_at,now_at);
  return jsonb_build_object('ok',true,'lease_id',p_lease_id,'fencing_generation',p_fencing_generation,'expires_at',new_expiry);
end $a2_renew$;

create or replace function ops.release_canonical_ownership_lease(
  p_lease_id uuid,p_lease_token uuid,p_fencing_generation bigint
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare live jsonb; context jsonb; now_at timestamptz;
begin
  live:=ops.canonical_ownership_validate_live(p_lease_id,p_lease_token,p_fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  context:=ops.canonical_ownership_context(); now_at:=(live->>'evaluated_at')::timestamptz;
  update ops.canonical_ownership_lease set state='released',released_at=now_at,updated_at=now_at
   where id=p_lease_id and state='active' and lease_token=p_lease_token and fencing_generation=p_fencing_generation and expires_at>now_at;
  if not found then return ops.canonical_ownership_refusal('LEASE_EXPIRED','lease.expiry','"releasable live lease"'::jsonb,'"changed while releasing"'::jsonb); end if;
  insert into ops.canonical_ownership_lease_event
    (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
     session_ref,host_ref,cause,occurred_at,created_at)
  values(context->>'tenant',p_lease_id,'released',p_fencing_generation,(context->>'actor_id')::uuid,
    context->>'session_ref',context->>'host_ref',
    '{"reason":"holder_release"}',now_at,now_at);
  return jsonb_build_object('ok',true,'lease_id',p_lease_id,'fencing_generation',p_fencing_generation,'state','released');
end $$;

create or replace function ops.expire_canonical_ownership_leases()
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare context jsonb:=ops.canonical_ownership_context(); now_at timestamptz; changed integer;
begin
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  perform pg_advisory_xact_lock(hashtextextended('canonical-ownership:'||(context->>'tenant'),0));
  perform 1 from ops.canonical_ownership_lease l where l.organization_tenant_id=context->>'tenant' order by l.id for update;
  now_at:=clock_timestamp();
  with expired as (
    update ops.canonical_ownership_lease set state='expired',updated_at=now_at
     where organization_tenant_id=context->>'tenant' and state='active' and expires_at<=now_at
     returning *
  ), events as (
    insert into ops.canonical_ownership_lease_event
      (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
       session_ref,host_ref,cause,occurred_at,created_at)
    select organization_tenant_id,id,'expired',fencing_generation,(context->>'actor_id')::uuid,
      context->>'session_ref',context->>'host_ref',
      '{"reason":"lease_expiry"}'::jsonb,now_at,now_at from expired
    returning 1
  ) select count(*) into changed from events;
  return jsonb_build_object('ok',true,'expired_count',changed,'evaluated_at',now_at);
end $$;

create or replace function ops.canonical_ownership_append_only()
returns trigger language plpgsql set search_path=pg_catalog,ops as $$
begin raise exception '% is append-only',tg_table_name; end $$;

create trigger canonical_ownership_claim_append_only before update or delete on ops.canonical_ownership_claim
for each row execute function ops.canonical_ownership_append_only();
create trigger canonical_ownership_dependency_append_only before update or delete on ops.canonical_ownership_dependency
for each row execute function ops.canonical_ownership_append_only();
create trigger canonical_ownership_event_append_only before update or delete on ops.canonical_ownership_lease_event
for each row execute function ops.canonical_ownership_append_only();

revoke all on sequence ops.canonical_ownership_fencing_generation from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on ops.canonical_ownership_lease,ops.canonical_ownership_claim,
  ops.canonical_ownership_dependency,ops.canonical_ownership_lease_event
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function
  ops.canonical_ownership_refusal(text,text,jsonb,jsonb),
  ops.canonical_ownership_path_valid(text),
  ops.canonical_ownership_resource_valid(text),
  ops.canonical_ownership_path_case_alias(text,text),
  ops.canonical_ownership_paths_overlap(text,text,text,text),
  ops.canonical_ownership_context(),
  ops.canonical_ownership_lock_lineage(uuid,uuid,text[]),
  ops.canonical_ownership_currentness(uuid,integer,text,uuid,text,uuid,text,text),
  ops.canonical_ownership_plan_dependencies(uuid,text),
  ops.canonical_ownership_dependency_state(uuid,uuid,text,text),
  ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer),
  ops.canonical_ownership_validate_live(uuid,uuid,bigint,boolean),
  ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb),
  ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer),
  ops.release_canonical_ownership_lease(uuid,uuid,bigint),
  ops.expire_canonical_ownership_leases(),
  ops.canonical_ownership_append_only()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

commit;
