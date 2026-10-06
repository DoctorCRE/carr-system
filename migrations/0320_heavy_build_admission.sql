-- 0320_heavy_build_admission.sql
-- A heavy build is a server-derived lifecycle state, not prose a model may or
-- may not notice. Bind research and the complete product plan before proposal,
-- then require a fresh-context review receipt before human plan acceptance.

begin;

create table ops.heavy_build_admission_revision (
  id                    uuid primary key default gen_random_uuid(),
  admission_ref         text not null unique,
  work_request_id       uuid not null references ops.work_request(id),
  plan_id               uuid not null references ops.sourced_work_request_plan(id),
  version               integer not null check (version > 0),
  idempotency_key       uuid not null unique,
  tier                  text not null check (tier = 'heavy'),
  classifier_reasons    jsonb not null check (jsonb_typeof(classifier_reasons) = 'array' and jsonb_array_length(classifier_reasons) > 0),
  contract              jsonb not null check (jsonb_typeof(contract) = 'object'),
  builder_session_ref   text not null check (builder_session_ref ~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$'),
  admission_hash        text not null check (admission_hash ~ '^sha256:[0-9a-f]{64}$'),
  proposed_by_actor_id  uuid not null references public.actor(id),
  proposed_at           timestamptz not null default now(),
  unique (plan_id, version),
  unique (plan_id, admission_hash)
);

create table ops.heavy_build_plan_review (
  id                    uuid primary key default gen_random_uuid(),
  review_ref            text not null unique,
  admission_id          uuid not null references ops.heavy_build_admission_revision(id),
  version               integer not null check (version > 0),
  idempotency_key       uuid not null unique,
  verdict               text not null check (verdict in ('pass','fail')),
  reviewer_actor_id     uuid not null references public.actor(id),
  reviewer_session_ref  text not null check (reviewer_session_ref ~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$'),
  review_summary        text not null check (char_length(btrim(review_summary)) between 20 and 1000),
  evidence_refs         jsonb not null check (jsonb_typeof(evidence_refs) = 'array' and jsonb_array_length(evidence_refs) between 1 and 12),
  gaps                  jsonb not null check (jsonb_typeof(gaps) = 'array' and jsonb_array_length(gaps) <= 12),
  review_hash           text not null check (review_hash ~ '^sha256:[0-9a-f]{64}$'),
  reviewed_at           timestamptz not null default now(),
  unique (admission_id, version),
  unique (admission_id, review_hash),
  check ((verdict = 'pass' and jsonb_array_length(gaps) = 0)
      or (verdict = 'fail' and jsonb_array_length(gaps) > 0))
);

comment on table ops.heavy_build_admission_revision is
  'Append-only typed research manifest and complete target-product plan bound to one exact sourced ready-plan proposal. It grants no acceptance or execution authority.';
comment on table ops.heavy_build_plan_review is
  'Append-only fresh-context review of one exact heavy-build admission revision. Only the latest passing review can admit human ready-plan acceptance.';

create or replace function ops.heavy_build_admission_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'heavy-build admission and review receipts are append-only';
end;
$$;

create trigger heavy_build_admission_revision_immutable
before update or delete on ops.heavy_build_admission_revision
for each row execute function ops.heavy_build_admission_rows_immutable();

create trigger heavy_build_plan_review_immutable
before update or delete on ops.heavy_build_plan_review
for each row execute function ops.heavy_build_admission_rows_immutable();

create or replace function ops.heavy_build_jsonb_has_exact_keys(p_document jsonb, p_keys text[])
returns boolean language sql immutable security definer
set search_path = pg_catalog, ops
as $$
  select jsonb_typeof(p_document) = 'object'
     and (select array_agg(k order by k) from jsonb_object_keys(p_document) k)
         is not distinct from
         (select array_agg(k order by k) from unnest(p_keys) k);
$$;

create or replace function ops.heavy_build_digest(p_preimage jsonb)
returns text language sql immutable security definer
set search_path = pg_catalog, ops
as $$
  select 'sha256:' || encode(public.digest(p_preimage::text, 'sha256'), 'hex');
$$;

-- This is the single classifier. The MCP preflight, receipt writer, and final
-- acceptance trigger all call it. A caller may escalate standard work by
-- supplying a heavy contract, but cannot downgrade any server-derived signal.
create or replace function ops.heavy_build_classification(
  p_work_request_id uuid,
  p_scope_summary text,
  p_dependency_refs jsonb,
  p_caps jsonb
)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
  signal_text text;
  reasons jsonb := '[]'::jsonb;
  criteria_count integer;
  dependency_count integer;
  step_count integer;
  shape_ready boolean;
begin
  select x.* into w from ops.work_request x where x.id = p_work_request_id;
  if not found then return null; end if;
  if jsonb_typeof(coalesce(p_dependency_refs, '[]'::jsonb)) is distinct from 'array'
     or jsonb_typeof(coalesce(p_caps, '{}'::jsonb)) is distinct from 'object' then
    raise exception 'heavy-build classification requires typed dependency refs and caps';
  end if;

  signal_text := lower(concat_ws(' ', w.title, w.desired_outcome,
    coalesce(w.acceptance_criteria::text, ''), coalesce(p_scope_summary, '')));
  criteria_count := case when jsonb_typeof(w.acceptance_criteria) = 'array' then jsonb_array_length(w.acceptance_criteria) else 0 end;
  dependency_count := jsonb_array_length(coalesce(p_dependency_refs, '[]'::jsonb));
  step_count := case when coalesce(p_caps->>'max_steps','') ~ '^[0-9]+$' then (p_caps->>'max_steps')::integer else 0 end;

  if signal_text ~ '\m(heavy[ -]build|first[ -]of[ -](its|the)[ -]kind|first[ -]of[ -]kind)\M' then
    reasons := reasons || jsonb_build_array('signal:first_of_kind_or_explicit_heavy');
  end if;
  if signal_text ~ '\m(build|create|extend|implement|ship|design)\M.{0,80}\m(new |complete |governed )?(capability|system|platform|engine|service|workflow|kernel)\M' then
    reasons := reasons || jsonb_build_array('signal:new_capability');
  end if;
  if signal_text ~ '\m(architecture|architectural|system design|multi[ -]surface|end[ -]to[ -]end)\M' then
    reasons := reasons || jsonb_build_array('signal:architecture_or_multi_surface');
  end if;
  if signal_text ~ '\m(migration|schema|deploy|deployment|rebuild|refactor|integration)\M' then
    reasons := reasons || jsonb_build_array('signal:structural_change');
  end if;
  if signal_text ~ '\m(agent learning|memory kernel|learning system|memory engine)\M' then
    reasons := reasons || jsonb_build_array('signal:agent_learning');
  end if;
  if criteria_count >= 5 then reasons := reasons || jsonb_build_array('scale:acceptance_criteria'); end if;
  if dependency_count >= 3 then reasons := reasons || jsonb_build_array('scale:dependency_refs'); end if;
  if step_count >= 5 then reasons := reasons || jsonb_build_array('scale:max_steps'); end if;

  shape_ready := w.shape_disposition = 'required'
    and exists (
      select 1 from ops.work_shape_revision sr
       where sr.work_request_id = w.id and sr.work_request_version = w.version
         and sr.version = (select max(current_sr.version) from ops.work_shape_revision current_sr where current_sr.work_request_id = w.id)
    );
  return jsonb_build_object(
    'tier', case when jsonb_array_length(reasons) > 0 then 'heavy' else 'standard' end,
    'reasons', reasons,
    'shape_disposition', w.shape_disposition,
    'shape_ready', shape_ready,
    'acceptance_criteria_count', criteria_count,
    'dependency_ref_count', dependency_count,
    'max_steps', step_count
  );
end;
$$;

create or replace function ops.classify_sourced_work_request_build(
  p_work_request text,
  p_base_version integer,
  p_scope_summary text,
  p_dependency_refs jsonb,
  p_caps jsonb
)
returns table (
  work_request_id uuid, ref text, tier text, reasons jsonb,
  shape_disposition text, shape_ready boolean
)
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
  classification jsonb;
begin
  select x.* into w from ops.work_request x
   where x.ref = p_work_request and x.state = 'triaged' and x.version = p_base_version
     and x.capture_idempotency_key is not null and x.organization_tenant_id = 'carr-internal';
  if not found then return; end if;
  classification := ops.heavy_build_classification(w.id, p_scope_summary, p_dependency_refs, p_caps);
  return query select w.id, w.ref, classification->>'tier', classification->'reasons',
    classification->>'shape_disposition', (classification->>'shape_ready')::boolean;
end;
$$;

create or replace function ops.record_sourced_heavy_build_admission(
  p_plan_id uuid,
  p_work_request text,
  p_base_version integer,
  p_classifier_reasons jsonb,
  p_contract jsonb,
  p_proposed_by_actor_id uuid,
  p_idempotency_key uuid
)
returns table (
  work_request_id uuid, ref text, plan_id uuid, plan_ref text,
  admission_ref text, admission_hash text, tier text,
  classifier_reasons jsonb, builder_session_ref text, replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  a ops.heavy_build_admission_revision%rowtype;
  actor public.actor%rowtype;
  classification jsonb;
  expected_reasons jsonb;
  research jsonb;
  master_plan jsonb;
  field text;
  minimum integer;
  next_version integer;
  preimage jsonb;
  digest text;
begin
  if p_plan_id is null or coalesce(p_work_request,'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1 or p_idempotency_key is null
     or p_proposed_by_actor_id is null or jsonb_typeof(p_classifier_reasons) is distinct from 'array'
     or jsonb_array_length(p_classifier_reasons) = 0
     or not ops.heavy_build_jsonb_has_exact_keys(p_contract, array['builder_session_ref','master_plan','research_manifest'])
     or coalesce(p_contract->>'builder_session_ref','') !~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$' then
    raise exception 'heavy build admission requires exact plan, Work Request version, classifier reasons, typed contract, actor, and idempotency key';
  end if;

  research := p_contract->'research_manifest';
  master_plan := p_contract->'master_plan';
  if not ops.heavy_build_jsonb_has_exact_keys(research,
       array['conclusion','current_baseline','failure_modes','maintained_repositories','practitioner_evidence','primary_sources','unresolved_contradictions'])
     or char_length(btrim(coalesce(research->>'conclusion',''))) not between 20 and 1000
     or jsonb_typeof(research->'unresolved_contradictions') is distinct from 'array'
     or jsonb_array_length(research->'unresolved_contradictions') > 12
     or exists (select 1 from jsonb_array_elements(research->'unresolved_contradictions') item
                 where jsonb_typeof(item) <> 'string' or char_length(btrim(item #>> '{}')) not between 10 and 500) then
    raise exception 'heavy build research manifest is incomplete';
  end if;

  foreach field in array array['primary_sources','maintained_repositories','practitioner_evidence','current_baseline','failure_modes'] loop
    minimum := case when field = 'maintained_repositories' then 2 else 1 end;
    if jsonb_typeof(research->field) is distinct from 'array'
       or jsonb_array_length(research->field) not between minimum and 12 then
      raise exception 'heavy build research class % requires % to 12 evidence items', field, minimum;
    end if;
  end loop;

  if exists (
    select 1
      from (values
        ('primary_sources','primary_source'),
        ('maintained_repositories','maintained_repository'),
        ('practitioner_evidence','practitioner_evidence'),
        ('current_baseline','current_baseline'),
        ('failure_modes','failure_mode')
      ) expected(field_name, class_name)
      cross join lateral jsonb_array_elements(research->expected.field_name) item
     where not ops.heavy_build_jsonb_has_exact_keys(item, array['content_digest','finding','locator','observed_at','source_class','source_ref'])
        or coalesce(item->>'source_ref','') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
        or item->>'source_class' is distinct from expected.class_name
        or coalesce(item->>'locator','') !~ '^https://'
        or coalesce(item->>'observed_at','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
        or case when coalesce(item->>'observed_at','') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
             then (item->>'observed_at')::timestamptz > now() + interval '5 minutes' else true end
        or coalesce(item->>'content_digest','') !~ '^sha256:[0-9a-f]{64}$'
        or char_length(btrim(coalesce(item->>'finding',''))) not between 20 and 1000
  ) then raise exception 'heavy build research evidence item is invalid or in the wrong source class'; end if;

  if exists (
    select source_ref from (
      select item->>'source_ref' source_ref
        from (values ('primary_sources'),('maintained_repositories'),('practitioner_evidence'),('current_baseline'),('failure_modes')) fields(field_name)
        cross join lateral jsonb_array_elements(research->fields.field_name) item
    ) refs group by source_ref having count(*) > 1
  ) then raise exception 'heavy build research source refs must be unique across classes'; end if;

  if not ops.heavy_build_jsonb_has_exact_keys(master_plan,
       array['architecture','authority_boundaries','baseline_comparison','dependency_dag','fully_shipped_definition','non_goals','observability_strategy','planned_checks','prerequisite_policy','product_goal','release_strategy','rollback_strategy']) then
    raise exception 'heavy build master plan must name the complete target-product contract';
  end if;
  foreach field in array array['product_goal','baseline_comparison','release_strategy','rollback_strategy','observability_strategy','fully_shipped_definition','prerequisite_policy'] loop
    if char_length(btrim(coalesce(master_plan->>field,''))) not between 20 and 2000 then
      raise exception 'heavy build master plan field % is incomplete', field;
    end if;
  end loop;
  foreach field in array array['non_goals','architecture','authority_boundaries'] loop
    minimum := case when field = 'architecture' then 2 else 1 end;
    if jsonb_typeof(master_plan->field) is distinct from 'array'
       or jsonb_array_length(master_plan->field) not between minimum and (case when field='architecture' then 20 else 12 end)
       or exists (select 1 from jsonb_array_elements(master_plan->field) item
                   where jsonb_typeof(item) <> 'string' or char_length(btrim(item #>> '{}')) not between 10 and 1000) then
      raise exception 'heavy build master plan field % requires substantive entries', field;
    end if;
  end loop;
  if jsonb_typeof(master_plan->'dependency_dag') is distinct from 'array'
     or jsonb_array_length(master_plan->'dependency_dag') not between 1 and 20
     or exists (select 1 from jsonb_array_elements(master_plan->'dependency_dag') step
                 where not ops.heavy_build_jsonb_has_exact_keys(step,array['depends_on','step_ref'])
                    or coalesce(step->>'step_ref','') !~ '^step:[a-z0-9][a-z0-9:._/-]*$'
                    or jsonb_typeof(step->'depends_on') <> 'array'
                    or exists (select 1 from jsonb_array_elements(step->'depends_on') dep
                                where jsonb_typeof(dep) <> 'string' or dep #>> '{}' !~ '^step:[a-z0-9][a-z0-9:._/-]*$' or dep #>> '{}' = step->>'step_ref'))
     or exists (select step->>'step_ref' from jsonb_array_elements(master_plan->'dependency_dag') step group by step->>'step_ref' having count(*) > 1)
     or exists (select 1 from jsonb_array_elements(master_plan->'dependency_dag') step
                 cross join lateral jsonb_array_elements_text(step->'depends_on') dep
                where not exists (select 1 from jsonb_array_elements(master_plan->'dependency_dag') declared where declared->>'step_ref'=dep)) then
    raise exception 'heavy build dependency DAG is invalid';
  end if;
  if exists (
    with recursive edges(src,dst) as (
      select step->>'step_ref', dep
        from jsonb_array_elements(master_plan->'dependency_dag') step
        cross join lateral jsonb_array_elements_text(step->'depends_on') dep
    ), walk(start_node,node,path,cycle) as (
      select src,dst,array[src,dst],dst=src from edges
      union all
      select walk.start_node,edges.dst,walk.path||edges.dst,edges.dst=any(walk.path)
        from walk join edges on edges.src=walk.node where not walk.cycle
    ) select 1 from walk where cycle limit 1
  ) then raise exception 'heavy build dependency graph contains a cycle'; end if;
  if jsonb_typeof(master_plan->'planned_checks') is distinct from 'array'
     or jsonb_array_length(master_plan->'planned_checks') not between 1 and 20
     or exists (select 1 from jsonb_array_elements(master_plan->'planned_checks') check_item
                 where not ops.heavy_build_jsonb_has_exact_keys(check_item,array['artifact','comparator','failure_condition'])
                    or char_length(btrim(coalesce(check_item->>'artifact',''))) not between 5 and 500
                    or char_length(btrim(coalesce(check_item->>'comparator',''))) not between 5 and 500
                    or char_length(btrim(coalesce(check_item->>'failure_condition',''))) not between 5 and 500) then
    raise exception 'heavy build planned checks must name artifact, comparator, and failure condition';
  end if;

  select x.* into actor from public.actor x where x.id=p_proposed_by_actor_id and x.active for share;
  if not found then raise exception 'heavy build admission actor is not active'; end if;
  perform pg_advisory_xact_lock(hashtextextended('heavy-build-admission:' || p_idempotency_key,0));
  select x.* into a from ops.heavy_build_admission_revision x where x.idempotency_key=p_idempotency_key for share;
  if found then
    select x.* into w from ops.work_request x where x.id=a.work_request_id;
    select x.* into p from ops.sourced_work_request_plan x where x.id=a.plan_id;
    if not found or w.ref is distinct from p_work_request or p.id is distinct from p_plan_id
       or p.work_request_version is distinct from p_base_version or a.classifier_reasons is distinct from p_classifier_reasons
       or a.contract is distinct from p_contract or a.proposed_by_actor_id is distinct from p_proposed_by_actor_id then
      raise exception 'idempotency key already names a different heavy build admission';
    end if;
    return query select w.id,w.ref,p.id,p.plan_ref,a.admission_ref,a.admission_hash,a.tier,a.classifier_reasons,a.builder_session_ref,true;
    return;
  end if;

  select w0.* into w from ops.work_request w0 where w0.ref=p_work_request for update;
  if not found or w.state is distinct from 'triaged' or w.version is distinct from p_base_version then
    raise exception 'exact current triaged Work Request and immutable plan required for heavy build admission';
  end if;
  select p0.* into p from ops.sourced_work_request_plan p0 where p0.id=p_plan_id and p0.work_request_id=w.id for share;
  if not found or p.work_request_version is distinct from w.version then
    raise exception 'exact current triaged Work Request and immutable plan required for heavy build admission';
  end if;
  classification := ops.heavy_build_classification(w.id,p.scope_summary,p.dependency_refs,p.caps);
  if coalesce((classification->>'shape_ready')::boolean,false) is not true then
    raise exception 'heavy build requires a current evidence-backed Work Shape before plan proposal';
  end if;
  expected_reasons := case when classification->>'tier'='heavy' then classification->'reasons' else jsonb_build_array('caller:explicit-heavy-contract') end;
  if p_classifier_reasons is distinct from expected_reasons then raise exception 'heavy build classifier reasons must be server-derived'; end if;

  select coalesce(max(x.version),0)+1 into next_version from ops.heavy_build_admission_revision x where x.plan_id=p.id;
  preimage := jsonb_build_object('contract','carr-heavy-build-admission/v1','work_request_id',w.id,'work_request_version',w.version,
    'plan_id',p.id,'plan_hash',p.plan_hash,'classifier_reasons',expected_reasons,'heavy_build',p_contract,
    'proposed_by_actor_id',p_proposed_by_actor_id,'version',next_version);
  digest := ops.heavy_build_digest(preimage);
  if exists (select 1 from ops.heavy_build_admission_revision x where x.plan_id=p.id and x.admission_hash=digest) then
    raise exception 'the exact heavy build admission already exists under a different idempotency key';
  end if;
  insert into ops.heavy_build_admission_revision
    (admission_ref,work_request_id,plan_id,version,idempotency_key,tier,classifier_reasons,contract,builder_session_ref,admission_hash,proposed_by_actor_id)
  values ('HBA-'||substr(digest,8,12)||'-v'||next_version,w.id,p.id,next_version,p_idempotency_key,'heavy',expected_reasons,p_contract,p_contract->>'builder_session_ref',digest,p_proposed_by_actor_id)
  returning * into a;
  return query select w.id,w.ref,p.id,p.plan_ref,a.admission_ref,a.admission_hash,a.tier,a.classifier_reasons,a.builder_session_ref,false;
end;
$$;

create or replace function ops.sourced_heavy_build_review_target(
  p_work_request text, p_plan_hash text, p_admission_hash text
)
returns table (work_request_id uuid, ref text, plan_id uuid, admission_id uuid, admission_ref text, builder_session_ref text)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select w.id,w.ref,p.id,a.id,a.admission_ref,a.builder_session_ref
    from ops.work_request w
    join ops.sourced_work_request_plan p on p.work_request_id=w.id
    join ops.heavy_build_admission_revision a on a.plan_id=p.id
   where w.ref=p_work_request and w.state='triaged' and p.plan_hash=p_plan_hash and a.admission_hash=p_admission_hash
     and a.version=(select max(latest.version) from ops.heavy_build_admission_revision latest where latest.plan_id=p.id)
     and w.organization_tenant_id='carr-internal';
$$;

create or replace function ops.review_sourced_heavy_build_plan(
  p_work_request text,
  p_plan_hash text,
  p_admission_hash text,
  p_reviewer_actor_id uuid,
  p_verdict text,
  p_reviewer_session_ref text,
  p_review_summary text,
  p_evidence_refs jsonb,
  p_gaps jsonb,
  p_idempotency_key uuid
)
returns table (
  work_request_id uuid, ref text, plan_id uuid, admission_ref text,
  admission_hash text, review_ref text, review_hash text, verdict text,
  reviewer_session_ref text, replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  a ops.heavy_build_admission_revision%rowtype;
  r ops.heavy_build_plan_review%rowtype;
  actor public.actor%rowtype;
  next_version integer;
  preimage jsonb;
  digest text;
begin
  if coalesce(p_work_request,'') !~ '^WR-[0-9]{1,12}$' or coalesce(p_plan_hash,'') !~ '^sha256:[0-9a-f]{64}$'
     or coalesce(p_admission_hash,'') !~ '^sha256:[0-9a-f]{64}$' or p_reviewer_actor_id is null or p_idempotency_key is null
     or p_verdict not in ('pass','fail') or coalesce(p_reviewer_session_ref,'') !~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$'
     or char_length(btrim(coalesce(p_review_summary,''))) not between 20 and 1000
     or jsonb_typeof(p_evidence_refs) is distinct from 'array' or jsonb_array_length(p_evidence_refs) not between 1 and 12
     or exists (select 1 from jsonb_array_elements(p_evidence_refs) item where jsonb_typeof(item)<>'string' or item #>> '{}' !~ '^safe:[a-z0-9][a-z0-9:_./-]*$')
     or exists (select item #>> '{}' from jsonb_array_elements(p_evidence_refs) item group by item #>> '{}' having count(*)>1)
     or jsonb_typeof(p_gaps) is distinct from 'array' or jsonb_array_length(p_gaps)>12
     or exists (select 1 from jsonb_array_elements(p_gaps) item where jsonb_typeof(item)<>'string' or char_length(btrim(item #>> '{}')) not between 10 and 500)
     or (p_verdict='pass' and jsonb_array_length(p_gaps)<>0) or (p_verdict='fail' and jsonb_array_length(p_gaps)=0) then
    raise exception 'heavy build review requires exact hashes, fresh session, verdict-consistent gaps, evidence, actor, and idempotency key';
  end if;
  select x.* into actor from public.actor x where x.id=p_reviewer_actor_id and x.active for share;
  if not found then raise exception 'heavy build reviewer actor is not active'; end if;
  perform pg_advisory_xact_lock(hashtextextended('heavy-build-review:'||p_idempotency_key,0));
  select x.* into r from ops.heavy_build_plan_review x where x.idempotency_key=p_idempotency_key for share;
  if found then
    select x.* into a from ops.heavy_build_admission_revision x where x.id=r.admission_id;
    select x.* into p from ops.sourced_work_request_plan x where x.id=a.plan_id;
    select x.* into w from ops.work_request x where x.id=a.work_request_id;
    if not found or w.ref is distinct from p_work_request or p.plan_hash is distinct from p_plan_hash
       or a.admission_hash is distinct from p_admission_hash or r.reviewer_actor_id is distinct from p_reviewer_actor_id
       or r.verdict is distinct from p_verdict or r.reviewer_session_ref is distinct from p_reviewer_session_ref
       or r.review_summary is distinct from btrim(p_review_summary) or r.evidence_refs is distinct from p_evidence_refs or r.gaps is distinct from p_gaps then
      raise exception 'idempotency key already names a different heavy build review';
    end if;
    return query select w.id,w.ref,p.id,a.admission_ref,a.admission_hash,r.review_ref,r.review_hash,r.verdict,r.reviewer_session_ref,true;
    return;
  end if;
  select w0.* into w from ops.work_request w0 where w0.ref=p_work_request and w0.state='triaged' for share;
  if not found then raise exception 'exact current heavy build review target not found'; end if;
  select p0.* into p from ops.sourced_work_request_plan p0 where p0.work_request_id=w.id and p0.plan_hash=p_plan_hash for share;
  if not found then raise exception 'exact current heavy build review target not found'; end if;
  select a0.* into a from ops.heavy_build_admission_revision a0
   where a0.plan_id=p.id and a0.admission_hash=p_admission_hash
     and a0.version=(select max(latest.version) from ops.heavy_build_admission_revision latest where latest.plan_id=p.id)
   for share;
  if not found then raise exception 'exact current heavy build review target not found'; end if;
  if p_reviewer_session_ref=a.builder_session_ref then raise exception 'heavy build review requires a fresh session distinct from the builder context'; end if;
  select coalesce(max(x.version),0)+1 into next_version from ops.heavy_build_plan_review x where x.admission_id=a.id;
  preimage := jsonb_build_object('contract','carr-heavy-build-review/v1','admission_id',a.id,'admission_hash',a.admission_hash,
    'plan_hash',p.plan_hash,'verdict',p_verdict,'reviewer_actor_id',p_reviewer_actor_id,'reviewer_session_ref',p_reviewer_session_ref,
    'review_summary',btrim(p_review_summary),'evidence_refs',p_evidence_refs,'gaps',p_gaps,'version',next_version);
  digest := ops.heavy_build_digest(preimage);
  insert into ops.heavy_build_plan_review
    (review_ref,admission_id,version,idempotency_key,verdict,reviewer_actor_id,reviewer_session_ref,review_summary,evidence_refs,gaps,review_hash)
  values ('HBR-'||substr(digest,8,12)||'-v'||next_version,a.id,next_version,p_idempotency_key,p_verdict,p_reviewer_actor_id,p_reviewer_session_ref,btrim(p_review_summary),p_evidence_refs,p_gaps,digest)
  returning * into r;
  return query select w.id,w.ref,p.id,a.admission_ref,a.admission_hash,r.review_ref,r.review_hash,r.verdict,r.reviewer_session_ref,false;
end;
$$;

-- The final, non-bypassable rail. The existing acceptance function inserts its
-- human receipt before updating the Work Request, so this trigger can resolve
-- the exact plan and refuse the transition atomically. Existing ready rows are
-- untouched; only a new triaged-to-ready transition is judged.
create or replace function ops.heavy_build_ready_plan_gate()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  p ops.sourced_work_request_plan%rowtype;
  a ops.heavy_build_admission_revision%rowtype;
  r ops.heavy_build_plan_review%rowtype;
  classification jsonb;
  heavy boolean;
  admission_found boolean;
begin
  if old.state = 'triaged' and new.state = 'ready' and old.capture_idempotency_key is not null then
    select plan.* into p
      from ops.sourced_work_request_plan_acceptance_receipt acceptance
      join ops.sourced_work_request_plan plan on plan.id=acceptance.plan_id
     where acceptance.work_request_id=old.id and acceptance.base_version=old.version and acceptance.result_version=new.version
     order by acceptance.accepted_at desc limit 1;
    if not found then return new; end if;
    classification := ops.heavy_build_classification(old.id,p.scope_summary,p.dependency_refs,p.caps);
    select x.* into a from ops.heavy_build_admission_revision x where x.plan_id=p.id order by x.version desc limit 1;
    admission_found := found;
    heavy := classification->>'tier'='heavy' or admission_found;
    if heavy then
      if new.shape_disposition is distinct from 'required'
         or not exists (select 1 from ops.work_shape_revision sr where sr.work_request_id=old.id and sr.work_request_version=old.version
                         and sr.version=(select max(current_sr.version) from ops.work_shape_revision current_sr where current_sr.work_request_id=old.id)) then
        raise exception 'heavy build plan requires a current evidence-backed Work Shape';
      end if;
      if a.id is null then
        raise exception 'heavy build plan requires a typed research and master-plan admission receipt';
      end if;
      select x.* into r from ops.heavy_build_plan_review x where x.admission_id=a.id order by x.version desc limit 1;
      if r.id is null or r.verdict <> 'pass' then
        raise exception 'heavy build plan requires a fresh passing independent review';
      end if;
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists work_request_heavy_build_ready_plan_gate on ops.work_request;
create trigger work_request_heavy_build_ready_plan_gate
before update on ops.work_request
for each row execute function ops.heavy_build_ready_plan_gate();

revoke all on table ops.heavy_build_admission_revision,
  ops.heavy_build_plan_review
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.heavy_build_jsonb_has_exact_keys(jsonb,text[]),
  ops.heavy_build_digest(jsonb),
  ops.heavy_build_classification(uuid,text,jsonb,jsonb),
  ops.classify_sourced_work_request_build(text,integer,text,jsonb,jsonb),
  ops.record_sourced_heavy_build_admission(uuid,text,integer,jsonb,jsonb,uuid,uuid),
  ops.sourced_heavy_build_review_target(text,text,text),
  ops.review_sourced_heavy_build_plan(text,text,text,uuid,text,text,text,jsonb,jsonb,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

grant execute on function ops.classify_sourced_work_request_build(text,integer,text,jsonb,jsonb)
  to carr_writer;
grant execute on function ops.record_sourced_heavy_build_admission(uuid,text,integer,jsonb,jsonb,uuid,uuid)
  to carr_writer;
grant execute on function ops.sourced_heavy_build_review_target(text,text,text)
  to carr_writer;
grant execute on function ops.review_sourced_heavy_build_plan(text,text,text,uuid,text,text,text,jsonb,jsonb,uuid)
  to carr_writer;

commit;
