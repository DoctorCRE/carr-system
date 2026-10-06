-- DoctorCRE v5 V5-A01: durable, exact-scope assurance-health evidence.
-- Paired atomically with 0718, the SCAC v83 successor.
-- This migration changes no production state by itself; release applies it.

do $preconditions$
declare missing text[] := array[]::text[]; name text;
begin
  foreach name in array array[
    'ops.f01_digest_jsonb(jsonb)', 'public.digest(bytea,text)'
  ] loop
    if to_regprocedure(name) is null then missing := missing || name; end if;
  end loop;
  if to_regclass('public.actor') is null then missing := missing || 'public.actor'; end if;
  foreach name in array array['carr_writer','carr_authority'] loop
    if not exists (select 1 from pg_roles where rolname=name) then missing := missing || ('role '||name); end if;
  end loop;
  if cardinality(missing)>0 then
    raise exception 'assurance_health_store_blocked: missing %', array_to_string(missing,', ')
      using errcode='42704';
  end if;
end $preconditions$;

do $fresh_install$
begin
  if to_regclass('ops.assurance_health_evidence') is not null
     or to_regprocedure('ops.record_assurance_health_evidence(jsonb,jsonb,uuid)') is not null
     or to_regprocedure('ops.read_assurance_health(text,integer,text)') is not null then
    raise exception 'assurance_health_store_blocked: V5-A01 objects already exist; this migration will not adopt an unknown shape'
      using errcode='42P07';
  end if;
end $fresh_install$;

create function ops.assurance_health_layers()
returns text[] language sql immutable set search_path=pg_catalog
as $$ select array['artifact_assessment','execution_assessment','controller_assessment',
  'candidate_outcome_oracle','activation_readback','actual_business_outcome']::text[] $$;

create function ops.assurance_health_basis(p_layer text)
returns text language sql immutable set search_path=pg_catalog
as $$ select case p_layer
  when 'artifact_assessment' then 'independent_artifact_review'
  when 'execution_assessment' then 'attempt_receipt_execution_evidence'
  when 'controller_assessment' then 'controller_readback'
  when 'candidate_outcome_oracle' then 'candidate_outcome_oracle_receipt'
  when 'activation_readback' then 'activation_readback'
  when 'actual_business_outcome' then 'accepted_sourced_outcome_feedback_receipt'
end $$;

create function ops.assurance_health_exact_keys(p_value jsonb,p_keys text[])
returns boolean language sql immutable set search_path=pg_catalog
as $$ select jsonb_typeof(p_value)='object'
  and (select coalesce(array_agg(k order by k),array[]::text[]) from jsonb_object_keys(p_value) k)
      = (select array_agg(k order by k) from unnest(p_keys) k) $$;

create function ops.assurance_health_refs_valid(p_value jsonb)
returns boolean language sql immutable set search_path=pg_catalog
as $$ select jsonb_typeof(p_value)='array'
  and jsonb_array_length(p_value)<=64
  and not exists(select 1 from jsonb_array_elements(p_value) x
                 where jsonb_typeof(x)<>'string' or length(x#>>'{}') not between 1 and 255)
  and jsonb_array_length(p_value)=(select count(distinct x#>>'{}') from jsonb_array_elements(p_value) x) $$;

-- An ISO-8601 instant with an explicit offset, or NULL. The offset is required
-- so the parsed instant never depends on the session TimeZone.
create function ops.assurance_health_instant(p_value text)
returns timestamptz language plpgsql stable set search_path=pg_catalog
as $$
begin
  if p_value is null
     or p_value!~'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' then
    return null;
  end if;
  return p_value::timestamptz;
exception when others then
  return null;
end $$;

create table ops.assurance_health_evidence (
  id uuid not null default gen_random_uuid(),
  record_sequence bigint generated always as identity,
  tenant text not null,
  workflow_key text not null,
  workflow_version integer not null,
  work_request_id text,
  layer text not null,
  basis text not null,
  status text not null,
  subject_ref text not null,
  evaluator_actor_id uuid not null,
  evaluator_slug text not null,
  evidence_ref text not null,
  evidence_digest text not null,
  observed_at timestamptz not null,
  expires_at timestamptz not null,
  detail jsonb not null,
  incident_refs text[] not null,
  recovery_refs text[] not null,
  receipt_digest text not null,
  idempotency_key uuid not null,
  recorded_at timestamptz not null default clock_timestamp(),
  constraint assurance_health_evidence_pk primary key(id),
  constraint assurance_health_evidence_idem unique(idempotency_key),
  constraint assurance_health_evidence_ref unique(tenant,evidence_ref),
  constraint assurance_health_evidence_actor_fk foreign key(evaluator_actor_id) references public.actor(id),
  constraint assurance_health_evidence_tenant check(tenant='carr-internal'),
  constraint assurance_health_evidence_workflow check(length(workflow_key) between 1 and 255 and workflow_version>0),
  constraint assurance_health_evidence_work_request check(work_request_id is null or work_request_id~'^WR-[0-9]{1,12}$'),
  constraint assurance_health_evidence_layer check(layer=any(ops.assurance_health_layers())),
  constraint assurance_health_evidence_basis check(basis=ops.assurance_health_basis(layer)),
  constraint assurance_health_evidence_status check(status in('pass','fail','skipped','untested','error','conflicting')),
  constraint assurance_health_evidence_subject check(length(subject_ref) between 1 and 255 and subject_ref<>evaluator_slug),
  constraint assurance_health_evidence_evaluator check(length(evaluator_slug) between 1 and 255),
  constraint assurance_health_evidence_ref_shape check(length(evidence_ref) between 1 and 255),
  constraint assurance_health_evidence_digest_shape check(evidence_digest~'^sha256:[0-9a-f]{64}$'),
  constraint assurance_health_evidence_time check(observed_at<expires_at),
  constraint assurance_health_evidence_detail check(jsonb_typeof(detail)='object'),
  constraint assurance_health_evidence_incident_refs check(cardinality(incident_refs)<=64),
  constraint assurance_health_evidence_recovery_refs check(cardinality(recovery_refs)<=64),
  constraint assurance_health_evidence_receipt_digest check(receipt_digest~'^sha256:[0-9a-f]{64}$')
);

create index assurance_health_evidence_scope_latest
  on ops.assurance_health_evidence(tenant,workflow_key,workflow_version,work_request_id,layer,observed_at desc,record_sequence desc);

comment on table ops.assurance_health_evidence is
  'V5-A01 append-only independent evidence, one exact workflow/work-request scope and one of six layers per receipt. Labels are derived only by ops.read_assurance_health.';

create function ops.assurance_health_evidence_immutable()
returns trigger language plpgsql set search_path=pg_catalog as $$
begin raise exception 'assurance health evidence is append-only' using errcode='55000'; end $$;
create trigger assurance_health_evidence_immutable
  before update or delete on ops.assurance_health_evidence
  for each row execute function ops.assurance_health_evidence_immutable();
-- TRUNCATE fires no row trigger, so the owner could otherwise erase the whole
-- evidence history in one statement (the 0700/0704/0706 no_truncate idiom).
create trigger assurance_health_evidence_no_truncate
  before truncate on ops.assurance_health_evidence
  for each statement execute function ops.assurance_health_evidence_immutable();

create function ops.record_assurance_health_evidence(p_scope jsonb,p_evidence jsonb,p_idempotency_key uuid)
returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,public,ops as $fn$
declare
  v_slug text:=nullif(btrim(current_setting('carr.acting_actor_slug',true)),'');
  v_actor uuid; v_layer text; v_detail jsonb; v_observed timestamptz; v_expires timestamptz;
  v_incident text[]; v_recovery text[]; v_digest text; v_existing ops.assurance_health_evidence%rowtype;
  v_row ops.assurance_health_evidence%rowtype; v_required text[];
begin
  if v_slug is null then raise exception 'assurance_health_actor_required' using errcode='42501'; end if;
  select id into v_actor from public.actor where slug=v_slug;
  if v_actor is null then raise exception 'assurance_health_actor_unregistered' using errcode='42501'; end if;
  if p_idempotency_key is null then raise exception 'assurance_health_idempotency_required' using errcode='22023'; end if;
  if not ops.assurance_health_exact_keys(p_scope,
       case when p_scope?'work_request_id' then array['workflow_key','workflow_version','work_request_id'] else array['workflow_key','workflow_version'] end)
     or nullif(btrim(p_scope->>'workflow_key'),'') is null
     or (p_scope->>'workflow_version')!~'^[1-9][0-9]*$'
     or (p_scope?'work_request_id' and (p_scope->>'work_request_id')!~'^WR-[0-9]{1,12}$') then
    raise exception 'assurance_health_scope_invalid' using errcode='22023';
  end if;
  if not ops.assurance_health_exact_keys(p_evidence,array['layer','basis','status','subject_ref','evidence_ref','evidence_digest','observed_at','expires_at','detail','incident_refs','recovery_refs']) then
    raise exception 'assurance_health_evidence_shape_invalid' using errcode='22023';
  end if;
  v_layer:=p_evidence->>'layer'; v_detail:=p_evidence->'detail';
  if not(v_layer=any(ops.assurance_health_layers())) or p_evidence->>'basis' is distinct from ops.assurance_health_basis(v_layer)
     or p_evidence->>'status' not in('pass','fail','skipped','untested','error','conflicting')
     or length(p_evidence->>'subject_ref') not between 1 and 255 or p_evidence->>'subject_ref'=v_slug
     or length(p_evidence->>'evidence_ref') not between 1 and 255
     or (p_evidence->>'evidence_digest')!~'^sha256:[0-9a-f]{64}$'
     or not ops.assurance_health_refs_valid(p_evidence->'incident_refs')
     or not ops.assurance_health_refs_valid(p_evidence->'recovery_refs') then
    raise exception 'assurance_health_evidence_invalid' using errcode='22023';
  end if;
  v_required:=case v_layer
    when 'artifact_assessment' then array['repository_commit_sha','repository_tree_sha','reviewer_fact_id']
    when 'execution_assessment' then array['attempt_id','envelope_digest','plan_hash']
    when 'controller_assessment' then array['controller_state','readback_source','readback_at']
    when 'candidate_outcome_oracle' then array['governed_data_ref','environment','expected_result_ref','equivalence_comparator','component_versions']
    when 'activation_readback' then array['activation_id','readback_source','readback_at']
    when 'actual_business_outcome' then array['outcome_feedback_ref','outcome_feedback_hash','acceptance_receipt_id'] end;
  if not ops.assurance_health_exact_keys(v_detail,v_required)
     or exists(select 1 from unnest(v_required) k where v_detail->>k is null or v_detail->>k='') then
    raise exception 'assurance_health_layer_detail_invalid' using errcode='22023';
  end if;
  if v_layer='candidate_outcome_oracle' and jsonb_typeof(v_detail->'component_versions')<>'object' then
    raise exception 'assurance_health_component_versions_invalid' using errcode='22023';
  end if;
  if v_layer='actual_business_outcome' and not(p_scope?'work_request_id') then
    raise exception 'assurance_health_outcome_requires_work_request' using errcode='22023';
  end if;
  v_observed:=ops.assurance_health_instant(p_evidence->>'observed_at');
  v_expires:=ops.assurance_health_instant(p_evidence->>'expires_at');
  if v_observed is null or v_expires is null
     or (v_detail?'readback_at' and ops.assurance_health_instant(v_detail->>'readback_at') is null) then
    raise exception 'assurance_health_instant_invalid' using errcode='22023';
  end if;
  -- Five minutes of clock skew is admitted at ingress; the read still reports
  -- any instant after its own clock as conflicting, never as passing.
  if v_observed>=v_expires or v_observed>now()+interval '5 minutes'
     or (v_detail?'readback_at' and ops.assurance_health_instant(v_detail->>'readback_at')>now()+interval '5 minutes') then
    raise exception 'assurance_health_evidence_time_invalid' using errcode='22023';
  end if;
  select coalesce(array_agg(x#>>'{}' order by o),array[]::text[]) into v_incident
    from jsonb_array_elements(p_evidence->'incident_refs') with ordinality e(x,o);
  select coalesce(array_agg(x#>>'{}' order by o),array[]::text[]) into v_recovery
    from jsonb_array_elements(p_evidence->'recovery_refs') with ordinality e(x,o);
  v_digest:=ops.f01_digest_jsonb(jsonb_build_object('scope',p_scope,'evidence',p_evidence,'evaluator',v_slug));
  select * into v_existing from ops.assurance_health_evidence where idempotency_key=p_idempotency_key;
  if found then
    if v_existing.receipt_digest<>v_digest then raise exception 'assurance_health_idempotency_conflict' using errcode='23505'; end if;
    return jsonb_build_object('id',v_existing.id,'evidence_ref',v_existing.evidence_ref,'evidence_digest',v_existing.evidence_digest,
      'evaluator',v_existing.evaluator_slug,'recorded_at',to_char(v_existing.recorded_at at time zone 'utc','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'replayed',true);
  end if;
  insert into ops.assurance_health_evidence(tenant,workflow_key,workflow_version,work_request_id,layer,basis,status,subject_ref,
    evaluator_actor_id,evaluator_slug,evidence_ref,evidence_digest,observed_at,expires_at,detail,incident_refs,recovery_refs,receipt_digest,idempotency_key)
  values('carr-internal',p_scope->>'workflow_key',(p_scope->>'workflow_version')::integer,p_scope->>'work_request_id',v_layer,p_evidence->>'basis',
    p_evidence->>'status',p_evidence->>'subject_ref',v_actor,v_slug,p_evidence->>'evidence_ref',p_evidence->>'evidence_digest',
    v_observed,v_expires,v_detail,v_incident,v_recovery,v_digest,p_idempotency_key) returning * into v_row;
  return jsonb_build_object('id',v_row.id,'evidence_ref',v_row.evidence_ref,'evidence_digest',v_row.evidence_digest,
    'evaluator',v_row.evaluator_slug,'recorded_at',to_char(v_row.recorded_at at time zone 'utc','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'replayed',false);
end $fn$;

-- THE STAGE LADDER, as the fixed table lib/assurance_health.py:306-312 names it.
-- p_granted is every requirement that is proven; the first stage whose every
-- requirement is present is the stage the scope holds. Workflow truth is part
-- of every rung: without it no stage above unavailable is reachable.
create function ops.assurance_health_stage(p_granted text[])
returns text language sql immutable set search_path=pg_catalog
as $$ select case
  when p_granted @> array['workflow_readable','workflow_coherent','workflow_live_admissible',
    'artifact_assessment','execution_assessment','controller_assessment',
    'candidate_outcome_oracle','activation_readback','actual_business_outcome']::text[] then 'act'
  when p_granted @> array['workflow_readable','workflow_coherent','workflow_enabled',
    'artifact_assessment','execution_assessment','controller_assessment']::text[] then 'draft'
  when p_granted @> array['workflow_readable','workflow_coherent','artifact_assessment']::text[] then 'read'
  else 'unavailable' end $$;

-- THE LABEL PREDICATE. Not granted to any runtime role and not reachable from a
-- verb: it takes workflow truth as an argument, and a caller that could supply
-- that argument would be authoring a label rather than reading one (the same
-- reason lib/assurance_health.py keeps its predicate module-private). The only
-- runtime caller is ops.read_assurance_health, which passes NULL -- workflow
-- truth unreadable -- until the V5-F09 census store (PR #1244) is on main and
-- its verified read can be passed here instead.
--
-- p_workflow_truth, when present, is one F09-projected row:
--   {"workflow_key","workflow_version","state","enabled","admissible_modes"}.
create function ops.assurance_health_label(p_workflow_key text,p_workflow_version integer,
  p_work_request_id text,p_workflow_truth jsonb)
returns jsonb language plpgsql stable
set search_path=pg_catalog,public,ops as $fn$
declare
  v_now timestamptz:=now();
  v_scope jsonb; v_evidence jsonb:='{}'; v_layer text; r ops.assurance_health_evidence%rowtype;
  v_state text; v_row jsonb; v_duplicate integer; v_readback timestamptz; v_reasons text[]:=array[]::text[];
  v_truth_state text; v_readable boolean:=false; v_coherent boolean:=false; v_enabled boolean:=false;
  v_live boolean:=false; v_truth_indeterminate boolean:=true; v_not_enabled boolean:=false;
  v_granted text[]:=array[]::text[]; v_nonpassing text[]:=array[]::text[]; v_indeterminate text[]:=array[]::text[];
  v_determinate text[]:=array[]::text[]; v_missing text[]:=array[]::text[]; v_unbindable text[]:=array[]::text[];
  v_passing_slots integer:=0; v_stage text; v_attributable text; v_health text; v_reason text; v_truth_out jsonb;
begin
  if nullif(btrim(p_workflow_key),'') is null or p_workflow_version is null or p_workflow_version<1
     or (p_work_request_id is not null and p_work_request_id!~'^WR-[0-9]{1,12}$') then
    raise exception 'assurance_health_scope_invalid' using errcode='22023';
  end if;
  v_scope:=jsonb_strip_nulls(jsonb_build_object('workflow_key',p_workflow_key,'workflow_version',p_workflow_version,'work_request_id',p_work_request_id));

  -- Workflow truth (V5-F09). Absent means UNREADABLE, never "fine".
  if p_workflow_truth is null then
    v_truth_out:=jsonb_build_object('available',false,'source','V5-F09 workflow census',
      'reason','workflow truth is not readable by this store; no stage above unavailable and no green state can be claimed');
  else
    v_truth_state:=p_workflow_truth->>'state';
    if jsonb_typeof(p_workflow_truth)<>'object'
       or p_workflow_truth->>'workflow_key' is distinct from p_workflow_key
       or jsonb_typeof(p_workflow_truth->'workflow_version')<>'number'
       or (p_workflow_truth->>'workflow_version') is distinct from p_workflow_version::text
       or v_truth_state is null or not(v_truth_state=any(array['unknown','conflict','undeclared','unregistered',
         'declared_disabled','enabled_shadow_only','enabled_canary_eligible','enabled_live_eligible','operational']))
       or jsonb_typeof(p_workflow_truth->'enabled') is distinct from 'boolean'
       or jsonb_typeof(p_workflow_truth->'admissible_modes') is distinct from 'array' then
      raise exception 'assurance_health_workflow_truth_invalid' using errcode='22023';
    end if;
    v_truth_indeterminate:=v_truth_state in('unknown','conflict','undeclared');
    v_not_enabled:=v_truth_state='declared_disabled';
    v_readable:=v_truth_state<>'unknown';
    v_coherent:=v_truth_state not in('conflict','undeclared');
    v_enabled:=(p_workflow_truth->>'enabled')::boolean;
    v_live:=p_workflow_truth->'admissible_modes' ? 'live';
    v_truth_out:=jsonb_build_object('available',true,'source','V5-F09 workflow census','state',v_truth_state,
      'enabled',v_enabled,'admissible_modes',p_workflow_truth->'admissible_modes');
  end if;
  if v_readable then v_granted:=v_granted||'workflow_readable'::text; end if;
  if v_coherent then v_granted:=v_granted||'workflow_coherent'::text; end if;
  if v_enabled then v_granted:=v_granted||'workflow_enabled'::text; end if;
  if v_live then v_granted:=v_granted||'workflow_live_admissible'::text; end if;

  foreach v_layer in array ops.assurance_health_layers() loop
    r:=null;
    select * into r from ops.assurance_health_evidence
     where tenant='carr-internal' and workflow_key=p_workflow_key and workflow_version=p_workflow_version
       and work_request_id is not distinct from p_work_request_id and layer=v_layer
     order by observed_at desc,record_sequence desc limit 1;
    if not found then
      v_state:=case when v_layer='actual_business_outcome' and p_work_request_id is null then 'unbindable' else 'missing' end;
      v_row:=jsonb_build_object('layer',v_layer,'state',v_state,'present',false,'scope',v_scope);
    else
      with latest as (
        select distinct on(layer) layer,evidence_ref,evidence_digest from ops.assurance_health_evidence
         where tenant='carr-internal' and workflow_key=p_workflow_key and workflow_version=p_workflow_version
           and work_request_id is not distinct from p_work_request_id
         order by layer,observed_at desc,record_sequence desc)
      select count(*) into v_duplicate from latest
       where evidence_ref=r.evidence_ref or evidence_digest=r.evidence_digest;
      v_readback:=case when r.detail?'readback_at' then ops.assurance_health_instant(r.detail->>'readback_at') end;
      -- Most conservative first, in lib/assurance_health.py _EVIDENCE_STATES order.
      v_state:=case
        when v_layer='actual_business_outcome' and p_work_request_id is null then 'unbindable'
        when r.observed_at>v_now or r.status='conflicting'
          or (r.detail?'readback_at' and (v_readback is null or v_readback>v_now)) then 'conflicting'
        when r.basis is distinct from ops.assurance_health_basis(v_layer) then 'refused_substitute'
        when r.status='fail' then 'failed'
        when r.status in('error','skipped','untested') then r.status
        when r.subject_ref=r.evaluator_slug then 'self_attested'
        when v_duplicate>1 then 'indistinct'
        when v_now>r.expires_at then 'stale'
        when r.status='pass' then 'passing'
        else 'conflicting' end;
      v_row:=jsonb_build_object('layer',v_layer,'state',v_state,'present',true,'status',r.status,'basis',r.basis,
        'scope',v_scope,'subject_ref',r.subject_ref,'evaluator_ref',r.evaluator_slug,'evidence_ref',r.evidence_ref,
        'evidence_digest',r.evidence_digest,'observed_at',to_char(r.observed_at at time zone 'utc','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
        'expires_at',to_char(r.expires_at at time zone 'utc','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
        'incident_refs',to_jsonb(r.incident_refs),'recovery_refs',to_jsonb(r.recovery_refs))||r.detail;
    end if;
    v_evidence:=v_evidence||jsonb_build_object(v_layer,v_row);
  end loop;

  -- ORDERING COHERENCE (lib/assurance_health.py): an accepted business outcome for
  -- a scope with no current passing activation readback cannot have happened the
  -- way it claims, so it is conflicting -- never passing, never merely unbindable.
  if v_evidence#>>'{actual_business_outcome,state}'='passing'
     and v_evidence#>>'{activation_readback,state}'<>'passing' then
    v_evidence:=jsonb_set(v_evidence,'{actual_business_outcome,state}','"conflicting"');
    v_reasons:=v_reasons||'actual_business_outcome: an accepted business outcome is claimed for a scope with no current passing activation readback'::text;
  end if;

  foreach v_layer in array ops.assurance_health_layers() loop
    v_state:=v_evidence#>>array[v_layer,'state'];
    if v_state='passing' then
      v_granted:=v_granted||v_layer; v_passing_slots:=v_passing_slots+1;
    else
      v_nonpassing:=v_nonpassing||v_layer;
      if v_state in('unreadable','mismatched','conflicting','indistinct') then v_indeterminate:=v_indeterminate||v_layer; end if;
      if v_state in('refused_substitute','failed','error','skipped','untested','self_attested','stale') then v_determinate:=v_determinate||v_layer; end if;
      if v_state='missing' then v_missing:=v_missing||v_layer; end if;
      if v_state='unbindable' then v_unbindable:=v_unbindable||v_layer; end if;
    end if;
  end loop;

  v_stage:=ops.assurance_health_stage(v_granted);
  -- Capability lost only to an indeterminate layer is unproven, not withdrawn:
  -- the failure is attributed only what survives granting those layers.
  v_attributable:=ops.assurance_health_stage(v_granted||v_indeterminate);

  if v_truth_indeterminate then
    v_health:='unknown';
    v_reason:=case when p_workflow_truth is null
      then 'authoritative workflow truth (V5-F09) is unreadable; nothing is claimed about this scope'
      else format('authoritative workflow truth is indeterminate (F09 state %s); nothing is claimed about this scope',v_truth_state) end;
  elsif v_not_enabled then
    v_health:='disabled'; v_reason:='F09 authoritative workflow truth reports the definition is not enabled';
  elsif cardinality(v_determinate)>0 and v_attributable='unavailable' then
    v_health:='failed'; v_reason:=format('a current exactly bound non-pass in %s withdrew every capability of this scope',v_determinate);
  elsif cardinality(v_determinate)>0 then
    v_health:='degraded'; v_reason:=format('a current exactly bound non-pass in %s withdrew capability; %s is what the failure itself leaves standing',v_determinate,v_attributable);
  elsif cardinality(v_indeterminate)=0 and (not v_live
      or v_evidence#>>'{activation_readback,state}' in('missing','unbindable')
      or v_evidence#>>'{actual_business_outcome,state}' in('missing','unbindable')) then
    v_health:='not-yet-operational'; v_reason:='nothing failed and nothing is operational yet';
  elsif v_stage='act' then
    v_health:='healthy'; v_reason:='six distinct, current, independently evaluated, exactly bound passing facts and F09 live admission';
  else
    v_health:='unknown'; v_reason:=format('evidence is incomplete or indeterminate (indeterminate=%s, missing=%s); no state is claimed',v_indeterminate,v_missing);
  end if;
  if (v_health='healthy') is distinct from (v_stage='act') or (v_health='healthy' and (v_passing_slots<>6 or not v_live)) then
    raise exception 'assurance_health_label_invariant: % at stage %',v_health,v_stage using errcode='XX000';
  end if;

  return jsonb_build_object('schema_version','assurance-health.v1','scope',v_scope,'state',v_health,'green',v_health='healthy',
    'state_reason',v_reason,'capability_stage',v_stage,'capability_stage_attributable_to_findings',v_attributable,
    'workflow_truth',v_truth_out,
    'owner',jsonb_build_object('kind','record_layer','ref','ops.assurance_health_evidence'),
    'evidence',v_evidence,'failing_layers',to_jsonb(v_determinate),'indeterminate_layers',to_jsonb(v_indeterminate),
    'missing_layers',to_jsonb(v_missing),'unbindable_layers',to_jsonb(v_unbindable),'reasons',to_jsonb(v_reasons),
    'impact',jsonb_build_object('scope_limited_to',v_scope,'withdrawn_stages',case v_stage
      when 'act' then '[]'::jsonb when 'draft' then '["act"]'::jsonb when 'read' then '["act","draft"]'::jsonb
      else '["act","draft","read"]'::jsonb end),
    'recovery',jsonb_build_object('required_evidence',to_jsonb(v_nonpassing)));
end $fn$;

-- THE READ DOOR. Workflow truth is passed as NULL (unreadable): the V5-F09
-- census store is not on main, and this store will not grow a second workflow
-- registry to stand in for it. Every scope therefore reads `unknown`, stage
-- `unavailable`, green false -- an unregistered workflow can never read green --
-- while the six evidence dispositions stay fully visible.
create function ops.read_assurance_health(p_workflow_key text,p_workflow_version integer,p_work_request_id text default null)
returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,public,ops as $fn$
begin
  return ops.assurance_health_label(p_workflow_key,p_workflow_version,p_work_request_id,null);
end $fn$;

revoke all on ops.assurance_health_evidence from public,carr_writer,carr_authority;
revoke all on function ops.assurance_health_layers(),ops.assurance_health_basis(text),ops.assurance_health_exact_keys(jsonb,text[]),
  ops.assurance_health_refs_valid(jsonb),ops.assurance_health_instant(text),ops.assurance_health_stage(text[]),
  ops.assurance_health_evidence_immutable(),ops.assurance_health_label(text,integer,text,jsonb),
  ops.record_assurance_health_evidence(jsonb,jsonb,uuid),ops.read_assurance_health(text,integer,text) from public;
grant execute on function ops.record_assurance_health_evidence(jsonb,jsonb,uuid),ops.read_assurance_health(text,integer,text)
  to carr_writer,carr_authority;
