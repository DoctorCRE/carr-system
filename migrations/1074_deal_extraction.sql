-- rollback: remove extraction verbs; preserve append-only job receipts and review evidence.
-- lock-review: new empty job table; scalar lineage column defaults false; replace fact review functions.

create table ops.deal_extraction_job (
  id uuid primary key,
  organization_tenant_id text not null,
  actor_id uuid not null references public.actor(id),
  entity_ref text not null,
  document_ref uuid not null references public.attachment(id),
  contract jsonb not null,
  content_hash text not null check(content_hash ~ '^[0-9a-f]{64}$'),
  receipt jsonb not null,
  batch_id uuid references ops.fact_observation_batch(id),
  recorded_at timestamptz not null default now(),
  unique(organization_tenant_id,id)
);
create index deal_extraction_job_field on ops.deal_extraction_job(organization_tenant_id,entity_ref);
create trigger deal_extraction_job_append_only before update or delete on ops.deal_extraction_job
  for each row execute function ops.fact_store_append_only();
revoke all on ops.deal_extraction_job from public,carr_reader,carr_writer,carr_authority,dot_reader;

alter table ops.fact_observation add column lineage_verified boolean not null default false;
alter table ops.fact_review_decision add column extraction_job_ids uuid[] not null default '{}';
insert into public.system_config(key,value) values('facts.automatic_promotion_enabled','false'::jsonb)
  on conflict(key) do nothing;

create function ops.read_deal_extraction_source(p_tenant text,p_actor_id uuid,p_contract jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare doc public.attachment%rowtype; prior ops.deal_extraction_job%rowtype;
begin
  if p_tenant is distinct from 'carr-internal' or not exists(select 1 from public.actor
    where id=p_actor_id and kind='automation' and active) then
    return jsonb_build_object('ok',false,'error','not_in_profile');
  end if;
  select * into prior from ops.deal_extraction_job where organization_tenant_id=p_tenant
    and id=(p_contract->>'idempotency_key')::uuid;
  if found then
    if prior.contract<>p_contract or prior.actor_id<>p_actor_id then
      return jsonb_build_object('ok',false,'error','key_reuse');
    end if;
    return jsonb_build_object('ok',true,'receipt',prior.receipt||jsonb_build_object('replayed',true));
  end if;
  select * into doc from public.attachment where id=(p_contract->>'document_ref')::uuid
    and subject_type='deal' and subject_id::text=p_contract->>'entity_ref' and deleted_at is null;
  if not found then return jsonb_build_object('ok',false,'error','document_not_found'); end if;
  if doc.mime<>'application/pdf' or doc.bytes not between 1 and 26214400 then
    return jsonb_build_object('ok',false,'error','unsupported_document');
  end if;
  return jsonb_build_object('ok',true,'receipt',null,'document',jsonb_build_object(
    'document_ref',doc.id,'entity_ref',doc.subject_id,'organization_tenant_id',p_tenant,
    'content_hash',doc.sha256,'document_family_id','sha256:'||doc.sha256,
    'lineage_verified',false,'r2_key',doc.r2_key,'mime',doc.mime,'bytes',doc.bytes));
end $$;
revoke all on function ops.read_deal_extraction_source(text,uuid,jsonb) from public;
grant execute on function ops.read_deal_extraction_source(text,uuid,jsonb) to carr_reader;

create function ops.record_deal_extraction(p_tenant text,p_actor_id uuid,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare contract jsonb := p_args-'content_hash'-'receipt'-'observations';
  source jsonb; result jsonb; bound jsonb; receipt jsonb; item jsonb; field_key text;
  idem uuid := (p_args->>'idempotency_key')::uuid;
begin
  -- All writers take field locks before batch/job locks, including human review.
  for field_key in select fact_key from ops.fact_key_contract order by fact_key loop
    perform pg_advisory_xact_lock(hashtextextended('canonical-fact:'||p_tenant||':'||(p_args->>'entity_ref')||':'||field_key,0));
  end loop;
  perform pg_advisory_xact_lock(hashtextextended('deal-extraction:'||p_tenant||':'||idem,0));
  source := ops.read_deal_extraction_source(p_tenant,p_actor_id,contract);
  if not (source->>'ok')::boolean then return source; end if;
  if source->'receipt'<>'null'::jsonb then return source->'receipt'; end if;
  perform 1 from public.attachment where id=(p_args->>'document_ref')::uuid and deleted_at is null
    and sha256=source#>>'{document,content_hash}' and subject_type='deal'
    and subject_id::text=p_args->>'entity_ref' and mime='application/pdf'
    and bytes=(source#>>'{document,bytes}')::bigint for share;
  if not found then return jsonb_build_object('ok',false,'error','source_changed'); end if;
  if p_args->>'content_hash' is distinct from source#>>'{document,content_hash}'
    or p_args#>'{receipt,contract}' is distinct from contract
    or p_args#>'{receipt,source}' is distinct from ((source->'document')-'r2_key'-'mime'-'bytes')
    or p_args#>'{receipt,canonical}' is distinct from 'false'::jsonb then
    return jsonb_build_object('ok',false,'error','source_mismatch');
  end if;
  if jsonb_array_length(p_args->'observations')+jsonb_array_length(p_args#>'{receipt,abstentions}')<>4
    or (select count(distinct k) from (
      select e->>'fact_key' k from jsonb_array_elements(p_args->'observations') e
      union all select e->>'fact_key' from jsonb_array_elements(p_args#>'{receipt,abstentions}') e
    ) f join ops.fact_key_contract c on c.fact_key=f.k)<>4 then
    return jsonb_build_object('ok',false,'error','validation_error');
  end if;
  bound := '[]'::jsonb;
  for item in select e from jsonb_array_elements(p_args->'observations') e loop
    if item->>'method' is distinct from 'text_layer' or item->>'confidence_tier' is distinct from 'human_review'
      or item#>>'{source,document_ref}' is distinct from p_args->>'document_ref'
      or item#>>'{source,content_hash}' is distinct from p_args->>'content_hash' then
      return jsonb_build_object('ok',false,'error','observation_not_review_eligible');
    end if;
    item := jsonb_set(item,'{source}',((item->'source')-'derivation_chain')||jsonb_build_object(
      'document_family_id',source#>>'{document,document_family_id}'));
    bound := bound||jsonb_build_array(item);
  end loop;
  if jsonb_array_length(bound)>0 then
    result := ops.record_fact_observations(p_tenant,p_actor_id,idem,p_args->>'entity_ref',bound);
    if not (result->>'ok')::boolean then return result; end if;
  else result := jsonb_build_object('ok',true,'batch_id',null,'observations','[]'::jsonb);
  end if;
  receipt := (p_args->'receipt')||result||jsonb_build_object('job_id',idem,'replayed',false,'canonical',false);
  insert into ops.deal_extraction_job(id,organization_tenant_id,actor_id,entity_ref,document_ref,
    contract,content_hash,receipt,batch_id)
    values(idem,p_tenant,p_actor_id,p_args->>'entity_ref',(p_args->>'document_ref')::uuid,
      contract,p_args->>'content_hash',receipt,(result->>'batch_id')::uuid);
  return receipt;
end $$;
revoke all on function ops.record_deal_extraction(text,uuid,jsonb) from public;
grant execute on function ops.record_deal_extraction(text,uuid,jsonb) to carr_writer;


create or replace function ops.fact_review_field(p_tenant text,p_entity_ref text,p_fact_key text) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
  with evidence as (
    select coalesce(array_agg(o.id order by o.id),'{}'::uuid[]) ids,
      max(o.observed_at) newest,
      count(distinct (o.normalized_value,coalesce(o.expense_basis,'')))>1 conflict,
      bool_or(o.confidence_tier='verify_band') verify_band,
      bool_or(o.confidence_tier='human_review' or o.method in ('ocr','human')) human_review,
      coalesce(jsonb_agg(ops.fact_observation_json(o)||jsonb_build_object('recorded_by_actor_id',b.recorded_by_actor_id)
        order by o.observed_at,o.batch_id,o.ordinal),'[]') observations
    from ops.fact_observation o join ops.fact_observation_batch b on b.id=o.batch_id
    where o.organization_tenant_id=p_tenant and o.entity_ref=p_entity_ref and o.fact_key=p_fact_key
  ), extraction as (
    select coalesce(array_agg(j.id order by j.id),'{}'::uuid[]) ids,
      max(j.recorded_at) newest,
      coalesce(jsonb_agg(a||jsonb_build_object('job_id',j.id) order by j.id),'[]'::jsonb) abstentions
    from ops.deal_extraction_job j cross join lateral jsonb_array_elements(j.receipt->'abstentions') a
    where j.organization_tenant_id=p_tenant and j.entity_ref=p_entity_ref and a->>'fact_key'=p_fact_key
  ), canonical as (
    select * from ops.canonical_fact where organization_tenant_id=p_tenant
      and entity_ref=p_entity_ref and fact_key=p_fact_key order by version desc limit 1
  ), decision as (
    select * from ops.fact_review_decision where organization_tenant_id=p_tenant
      and entity_ref=p_entity_ref and fact_key=p_fact_key order by id desc limit 1
  )
  select jsonb_build_object('entity_ref',p_entity_ref,'fact_key',p_fact_key,
    'canonical_version',coalesce(c.version,0),
    'review_version',encode(sha256(convert_to(jsonb_build_array(e.ids,x.ids,coalesce(c.version,0),d.id)::text,'UTF8')),'hex'),
    'pending',((cardinality(e.ids)>0 and (e.conflict or e.verify_band or e.human_review)) or cardinality(x.ids)>0)
      and not (coalesce(d.observation_ids=e.ids and d.extraction_job_ids=x.ids and d.canonical_version=coalesce(c.version,0),false)
        or coalesce(d.id is null and c.basis='human_review' and c.promoted_at>=e.newest and (x.newest is null or c.promoted_at>=x.newest),false)),
    'reasons',to_jsonb(array_remove(array[case when e.conflict then 'conflict' end,
      case when e.verify_band then 'verify_band' end,case when e.human_review then 'human_review' end],null))||coalesce((select jsonb_agg(distinct a->>'reason') from jsonb_array_elements(x.abstentions) a),'[]'::jsonb),
    'extraction_abstentions',x.abstentions,'extraction_job_ids',to_jsonb(x.ids),
    'observations',e.observations,
    'canonical_fact',case when c.id is not null then (to_jsonb(c)-'request_sha256')||
      jsonb_build_object('normalized_value',trim_scale(c.normalized_value)::text) end,
    'canonical_history',coalesce((select jsonb_agg((to_jsonb(h)-'request_sha256')||
      jsonb_build_object('normalized_value',trim_scale(h.normalized_value)::text) order by h.version)
      from ops.canonical_fact h where h.organization_tenant_id=p_tenant and h.entity_ref=p_entity_ref and h.fact_key=p_fact_key),'[]'),
    'review_history',coalesce((select jsonb_agg(to_jsonb(h)-'request_sha256'-'result' order by h.id)
      from ops.fact_review_decision h where h.organization_tenant_id=p_tenant and h.entity_ref=p_entity_ref and h.fact_key=p_fact_key),'[]'))
  from evidence e cross join extraction x left join canonical c on true left join decision d on true
$$;

create or replace function ops.list_fact_review(p_tenant text,p_entity_ref text,p_limit integer,p_cursor jsonb,p_include_resolved boolean) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare items jsonb; more boolean; last_item jsonb;
begin
  if p_limit is null or p_limit not between 1 and 100 or p_include_resolved is null then
    return jsonb_build_object('ok',false,'error','validation_error');
  end if;
  if p_cursor is not null and (jsonb_typeof(p_cursor)<>'object'
    or p_cursor->>'tenant' is distinct from p_tenant
    or p_cursor->>'entity_filter' is distinct from p_entity_ref
    or p_cursor->'include_resolved' is distinct from to_jsonb(p_include_resolved)
    or jsonb_typeof(p_cursor->'entity_ref') is distinct from 'string'
    or jsonb_typeof(p_cursor->'fact_key') is distinct from 'string') then
    return jsonb_build_object('ok',false,'error','invalid_cursor');
  end if;
  with fields as (
    select distinct o.entity_ref,o.fact_key from (
      select entity_ref,fact_key,organization_tenant_id from ops.fact_observation
      union all
      select j.entity_ref,a->>'fact_key',j.organization_tenant_id from ops.deal_extraction_job j
        cross join lateral jsonb_array_elements(j.receipt->'abstentions') a
    ) o
    where o.organization_tenant_id=p_tenant and (p_entity_ref is null or o.entity_ref=p_entity_ref)
      and (p_cursor is null or (o.entity_ref collate "C",o.fact_key collate "C")>
        (p_cursor->>'entity_ref' collate "C",p_cursor->>'fact_key' collate "C"))
  ), page as (
    select f.*,ops.fact_review_field(p_tenant,f.entity_ref,f.fact_key) item from fields f
  ), eligible as (
    select * from page where p_include_resolved or (item->>'pending')::boolean
    order by entity_ref collate "C",fact_key collate "C" limit p_limit+1
  )
  select coalesce(jsonb_agg(item order by entity_ref collate "C",fact_key collate "C"),'[]') into items from eligible;
  more := jsonb_array_length(items)>p_limit;
  if more then items := items-p_limit; end if;
  last_item := items->(jsonb_array_length(items)-1);
  return jsonb_build_object('ok',true,'items',items,'has_more',more,'next_cursor',case when more then
    jsonb_build_object('tenant',p_tenant,'entity_filter',p_entity_ref,'include_resolved',p_include_resolved,
      'entity_ref',last_item->>'entity_ref','fact_key',last_item->>'fact_key')::text end);
end $$;

create or replace function ops.resolve_fact_review(p_tenant text,p_actor_id uuid,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare
  prior ops.fact_review_decision%rowtype;
  field jsonb;
  out jsonb;
  chosen uuid;
  evidence uuid[];
  digest text := encode(sha256(convert_to(p_args::text,'UTF8')),'hex');
  idem uuid := (p_args->>'idempotency_key')::uuid;
  entity text := p_args->>'entity_ref';
  fact text := p_args->>'fact_key';
  action text := p_args->>'decision';
  base integer := (p_args->>'base_version')::integer;
begin
  if not exists(select 1 from public.actor where id=p_actor_id and kind='human' and active) then
    return jsonb_build_object('ok',false,'error','human_only_verb_requires_verified_partner');
  end if;
  if action is null or action not in ('approve','correct','reject') or base is null or base<0
    or length(btrim(p_args->>'reason')) not between 1 and 2000
    or p_args->>'reason' is null
    or (action='approve') is distinct from (p_args ? 'observation_id')
    or (action='correct') is distinct from (p_args ? 'correction')
    or (action='correct' and p_args#>>'{correction,fact_key}' is distinct from fact) then
    return jsonb_build_object('ok',false,'error','validation_error');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('fact-review-key:'||p_tenant||':'||idem,0));
  select * into prior from ops.fact_review_decision where organization_tenant_id=p_tenant and idempotency_key=idem;
  if found then
    if prior.request_sha256<>digest or prior.actor_id<>p_actor_id then
      return jsonb_build_object('ok',false,'error','key_reuse');
    end if;
    return prior.result||jsonb_build_object('replayed',true,'review_receipt',to_jsonb(prior)-'request_sha256'-'result');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('canonical-fact:'||p_tenant||':'||entity||':'||fact,0));
  field := ops.fact_review_field(p_tenant,entity,fact);
  if (field->>'canonical_version')::integer<>base or field->>'review_version' is distinct from p_args->>'review_version' then
    return jsonb_build_object('ok',false,'error','version_conflict','current_version',field->'canonical_version');
  end if;
  if jsonb_array_length(field->'observations')=0 and jsonb_array_length(field->'extraction_abstentions')=0 then return jsonb_build_object('ok',false,'error','not_found'); end if;
  if action='correct' then
    out := ops.record_human_fact(p_tenant,p_actor_id,idem,entity,p_args->'correction');
    if not (out->>'ok')::boolean then return out; end if;
    chosen := (out#>>'{observation,id}')::uuid;
  elsif action='approve' then
    chosen := (p_args->>'observation_id')::uuid;
    if not exists(select 1 from ops.fact_observation where id=chosen and organization_tenant_id=p_tenant
      and entity_ref=entity and fact_key=fact) then return jsonb_build_object('ok',false,'error','not_found'); end if;
  end if;
  if action<>'reject' then
    out := ops.promote_fact(p_tenant,p_actor_id,idem,entity,fact,'human_review',array[chosen],base);
    if not (out->>'ok')::boolean then
      -- A failed promotion must also roll back any correction observation in this statement.
      raise exception 'fact review promotion refused' using errcode='22023';
    end if;
  else out := jsonb_build_object('ok',true,'replayed',false,'canonical_fact',field->'canonical_fact');
  end if;
  select array_agg(id order by id) into evidence from ops.fact_observation where organization_tenant_id=p_tenant
    and entity_ref=entity and fact_key=fact;
  insert into ops.fact_review_decision(organization_tenant_id,entity_ref,fact_key,decision,reason,observation_ids,extraction_job_ids,
    chosen_observation_id,canonical_version,actor_id,idempotency_key,request_sha256,result)
  values(p_tenant,entity,fact,action,p_args->>'reason',coalesce(evidence,'{}'::uuid[]),
    array(select jsonb_array_elements_text(field->'extraction_job_ids'))::uuid[],chosen,
    coalesce((out#>>'{canonical_fact,version}')::integer,0),p_actor_id,idem,digest,out) returning * into prior;
  return out||jsonb_build_object('review_receipt',to_jsonb(prior)-'request_sha256'-'result');
end $$;

create or replace function ops.promote_fact(p_tenant text,p_actor_id uuid,p_idempotency_key uuid,p_entity_ref text,
  p_fact_key text,p_basis text,p_observation_ids uuid[],p_base_version integer) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare
  digest text;
  prior ops.canonical_fact%rowtype;
  current_fact ops.canonical_fact%rowtype;
  inputs ops.fact_observation[];
  chosen ops.fact_observation;
  promoted ops.canonical_fact%rowtype;
begin
  if p_tenant is null or p_actor_id is null or p_idempotency_key is null or p_entity_ref is null
     or p_fact_key is null or p_basis not in ('independent_agreement','human_review')
     or p_observation_ids is null or cardinality(p_observation_ids) not between 1 and 100
     or array_position(p_observation_ids,null) is not null
     or (select count(distinct id) from unnest(p_observation_ids) id)<>cardinality(p_observation_ids)
     or p_base_version is null or p_base_version<0 then
    raise exception 'invalid fact promotion' using errcode='22023';
  end if;
  digest := encode(sha256(convert_to(jsonb_build_object('entity_ref',p_entity_ref,'fact_key',p_fact_key,
    'basis',p_basis,'observation_ids',to_jsonb(p_observation_ids),'base_version',p_base_version)::text,'UTF8')),'hex');
  perform pg_advisory_xact_lock(hashtextextended('canonical-fact-key:'||p_tenant||':'||p_idempotency_key,0));
  select * into prior from ops.canonical_fact f
   where f.organization_tenant_id=p_tenant and f.idempotency_key=p_idempotency_key;
  if found then
    if prior.request_sha256<>digest then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return jsonb_build_object('ok',true,'replayed',true,'canonical_fact',(to_jsonb(prior)-'request_sha256')
      ||jsonb_build_object('normalized_value',trim_scale(prior.normalized_value)::text));
  end if;
  perform pg_advisory_xact_lock(hashtextextended('canonical-fact:'||p_tenant||':'||p_entity_ref||':'||p_fact_key,0));
  select * into current_fact from ops.canonical_fact f
   where f.organization_tenant_id=p_tenant and f.entity_ref=p_entity_ref and f.fact_key=p_fact_key
   order by f.version desc limit 1;
  if coalesce(current_fact.version,0)<>p_base_version then
    return jsonb_build_object('ok',false,'error','version_conflict','current_version',coalesce(current_fact.version,0));
  end if;
  select array_agg(o order by o.observed_at,o.id) into inputs from ops.fact_observation o
   where o.id=any(p_observation_ids) and o.organization_tenant_id=p_tenant
     and o.entity_ref=p_entity_ref and o.fact_key=p_fact_key;
  if coalesce(cardinality(inputs),0)<>cardinality(p_observation_ids) then
    return jsonb_build_object('ok',false,'error','not_found');
  end if;
  chosen := inputs[1];
  if exists(select 1 from unnest(inputs) o where (o.normalized_value,o.expense_basis)
            is distinct from (chosen.normalized_value,chosen.expense_basis)) then
    return jsonb_build_object('ok',false,'error','observations_disagree');
  end if;
  if p_basis='human_review' then
    if cardinality(inputs)<>1 or not exists(select 1 from public.actor a where a.id=p_actor_id and a.kind='human' and a.active) then
      return jsonb_build_object('ok',false,'error','human_review_requires_one_observation_and_a_human');
    end if;
  else
    if exists(select 1 from unnest(inputs) o where o.method<>'text_layer' or o.confidence_tier<>'auto_accept') then
      return jsonb_build_object('ok',false,'error','observation_not_rule_eligible');
    end if;
    if exists(select 1 from ops.fact_observation o where o.organization_tenant_id=p_tenant
               and o.entity_ref=p_entity_ref and o.fact_key=p_fact_key
               and (o.normalized_value,o.expense_basis) is distinct from (chosen.normalized_value,chosen.expense_basis)) then
      return jsonb_build_object('ok',false,'error','unresolved_conflict');
    end if;
    if not exists(select 1 from public.system_config where key='facts.automatic_promotion_enabled' and value='true'::jsonb) then
      return jsonb_build_object('ok',false,'error','automatic_promotion_disabled');
    end if;
    if not exists(select 1 from unnest(inputs) a cross join unnest(inputs) b
       where a.id<b.id and a.lineage_verified and b.lineage_verified
         and a.content_hash is not null and b.content_hash is not null and a.content_hash<>b.content_hash
         and a.document_ref<>b.document_ref
         and a.document_family_id is not null and b.document_family_id is not null
         and not ((a.document_family_id||a.derivation_chain) && (b.document_family_id||b.derivation_chain))
         and a.extraction_pass is not null and b.extraction_pass is not null
         and (a.extraction_pass,a.extraction_model)<>(b.extraction_pass,b.extraction_model)) then
      return jsonb_build_object('ok',false,'error','independence_not_proven');
    end if;
  end if;
  insert into ops.canonical_fact(organization_tenant_id,entity_ref,fact_key,version,supersedes_id,normalized_value,
    canonical_unit,expense_basis,basis,observation_ids,promoted_by_actor_id,idempotency_key,request_sha256)
  select p_tenant,p_entity_ref,p_fact_key,p_base_version+1,current_fact.id,chosen.normalized_value,c.canonical_unit,
    chosen.expense_basis,p_basis,array(select o.id from unnest(inputs) o),p_actor_id,p_idempotency_key,digest
    from ops.fact_key_contract c where c.fact_key=p_fact_key
  returning * into promoted;
  return jsonb_build_object('ok',true,'replayed',false,'canonical_fact',(to_jsonb(promoted)-'request_sha256')
    ||jsonb_build_object('normalized_value',trim_scale(promoted.normalized_value)::text));
end $$;

do $contract$
declare contract jsonb;
begin
  contract := ops.scac_current_grant_contract();
  contract := jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||(
    select jsonb_agg(jsonb_build_object('config',jsonb_build_array('search_path=pg_catalog, pg_temp'),
      'grantee',grantee,'parallel','u','grantable',false,'privilege','execute','signature',signature,
      'volatility','v','ingress_key','db-function-acl:'||signature||':'||grantee||':execute',
      'ingress_kind','db_function_acl','function_kind','f','security_definer',true) order by ordinal)
    from (values
      (1,'ops.read_deal_extraction_source(p_tenant text, p_actor_id uuid, p_contract jsonb)','carr_reader'),
      (2,'ops.record_deal_extraction(p_tenant text, p_actor_id uuid, p_args jsonb)','carr_writer')
    ) as grants(ordinal,signature,grantee)));
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb');
end $contract$;
