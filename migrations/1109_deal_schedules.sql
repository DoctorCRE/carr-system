-- rollback: remove v2 extraction ingress; retain immutable schedules and review history.
-- lock-review: short metadata locks add nullable structured values; existing scalar rows are unchanged.
-- expand-contract: expand the fact value union for structured rent schedules alongside existing scalars.

insert into ops.fact_key_contract values ('rent_schedule','schedule','{"schedule":1}',false);
alter table ops.fact_observation alter column normalized_value drop not null;
alter table ops.fact_observation drop constraint fact_observation_value_check;
alter table ops.fact_observation add column normalized_schedule jsonb;
alter table ops.fact_observation add column region_id text check (length(region_id) between 1 and 100);
alter table ops.canonical_fact alter column normalized_value drop not null;
alter table ops.canonical_fact add column normalized_schedule jsonb;

create function ops.schedule_terms(p_schedule jsonb) returns jsonb
language sql immutable set search_path=pg_catalog,pg_temp as $$
  select jsonb_agg((select jsonb_object_agg(k,v->'value') from jsonb_each(period) f(k,v)) order by ordinal)
    from jsonb_array_elements(p_schedule) with ordinality t(period,ordinal)
$$;
revoke all on function ops.schedule_terms(jsonb) from public;

create function ops.valid_rent_schedule(p_schedule jsonb) returns boolean
language plpgsql immutable set search_path=pg_catalog,pg_temp as $$
declare period jsonb; field jsonb; name text; ending integer := 0; starting integer; stopping integer;
begin
  if jsonb_typeof(p_schedule) is distinct from 'array' then return false; end if;
  if jsonb_array_length(p_schedule) not between 1 and 100 then return false; end if;
  for period in select jsonb_array_elements(p_schedule) loop
    if jsonb_typeof(period) is distinct from 'object' then return false; end if;
    if (select array_agg(k order by k) from jsonb_object_keys(period) k) <>
      array['end_month','escalation_percent','expense_basis','rate','start_month','unit'] then return false; end if;
    for name,field in select * from jsonb_each(period) loop
      if jsonb_typeof(field) is distinct from 'object' or not field ?& array['value','source']
        or (select count(*) from jsonb_object_keys(field))<>2 then return false; end if;
      if jsonb_typeof(field->'source') is distinct from 'object' then return false; end if;
      if (select array_agg(k order by k) from jsonb_object_keys(field->'source') k) <>
        array['method','page','region_id'] then return false; end if;
      if jsonb_typeof(field#>'{source,method}') is distinct from 'string'
        or field#>>'{source,method}' not in ('text_layer','ocr')
        or jsonb_typeof(field#>'{source,page}') is distinct from 'number'
        or (field#>>'{source,page}') !~ '^[1-9][0-9]{0,2}$|^1000$'
        or jsonb_typeof(field#>'{source,region_id}') is distinct from 'string'
        or length(field#>>'{source,region_id}') not between 1 and 100 then return false; end if;
      if name in ('start_month','end_month') then
        if jsonb_typeof(field->'value') is distinct from 'number' or (field->>'value') !~ '^[1-9][0-9]{0,4}$'
          or (field->>'value')::integer>12000 then return false; end if;
      elsif name in ('rate','escalation_percent') then
        if not (name='escalation_percent' and field->'value'='null'::jsonb) and
          (jsonb_typeof(field->'value') is distinct from 'string' or (field->>'value') !~ '^(0|[1-9][0-9]{0,29})(\.[0-9]{1,18})?$') then return false; end if;
      elsif name='unit' then
        if jsonb_typeof(field->'value') is distinct from 'string' or field->>'value' not in ('USD/SF/year','USD/SF/month') then return false; end if;
      elsif jsonb_typeof(field->'value') is distinct from 'string' or field->>'value' not in ('nnn','modified_gross','full_service_gross','gross') then return false;
      end if;
    end loop;
    starting := (period#>>'{start_month,value}')::integer;
    stopping := (period#>>'{end_month,value}')::integer;
    if starting<=ending or stopping<starting then return false; end if;
    ending := stopping;
  end loop;
  return true;
end $$;
revoke all on function ops.valid_rent_schedule(jsonb) from public;

alter table ops.fact_observation add constraint fact_observation_value_shape check (
  case when fact_key='rent_schedule' then normalized_value is null and normalized_schedule is not null and ops.valid_rent_schedule(value::jsonb)
    and normalized_schedule=ops.schedule_terms(value::jsonb)
    and (method<>'text_layer' or not jsonb_path_exists(value::jsonb,'$[*].*.source ? (@.method == "ocr")'))
  else normalized_schedule is null and normalized_value is not null
    and value ~ '^(0|[1-9][0-9]{0,29})(\.[0-9]{1,18})?$' end) not valid;
alter table ops.fact_observation validate constraint fact_observation_value_shape;
alter table ops.canonical_fact add constraint canonical_fact_value_shape check (
  (fact_key='rent_schedule' and normalized_value is null and normalized_schedule is not null)
  or (fact_key<>'rent_schedule' and normalized_value is not null and normalized_schedule is null)) not valid;
alter table ops.canonical_fact validate constraint canonical_fact_value_shape;


create or replace function ops.fact_observation_json(o ops.fact_observation) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
  select jsonb_build_object('id',o.id,'observation_ref',o.observation_ref,'entity_ref',o.entity_ref,
    'fact_key',o.fact_key,'value',case when o.fact_key='rent_schedule' then o.value::jsonb else to_jsonb(o.value) end,'unit',o.unit,'expense_basis',o.expense_basis,
    'normalized_value',coalesce(to_jsonb(trim_scale(o.normalized_value)::text),o.normalized_schedule),
    'source',jsonb_build_object('document_ref',o.document_ref,'content_hash',o.content_hash,
      'document_family_id',o.document_family_id,'derivation_chain',to_jsonb(o.derivation_chain),
      'page_start',o.page_start,'page_end',o.page_end,'region_id',o.region_id,
      'cited_text',case when o.cited_text_expires_at>now() then o.cited_text end,
      'cited_text_sha256',o.cited_text_sha256,'cited_text_expires_at',o.cited_text_expires_at),
    'method',o.method,'extractor_version',o.extractor_version,
    'extraction',case when o.extraction_pass is not null then
      jsonb_build_object('pass',o.extraction_pass,'model',o.extraction_model) end,
    'confidence_tier',o.confidence_tier,'observed_at',o.observed_at)
$$;

create or replace function ops.record_fact_observations(p_tenant text,p_actor_id uuid,p_idempotency_key uuid,
  p_entity_ref text,p_observations jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare
  digest text;
  batch ops.fact_observation_batch%rowtype;
  item jsonb;
  ord bigint;
  contract ops.fact_key_contract%rowtype;
  multiplier numeric;
  cited text;
  replayed boolean := false;
  field_key text;
begin
  if p_tenant is null or p_actor_id is null or p_idempotency_key is null or p_entity_ref is null
     or jsonb_typeof(p_observations) is distinct from 'array'
     or jsonb_array_length(p_observations) not between 1 and 100 then
    raise exception 'invalid fact observation batch' using errcode='22023';
  end if;
  for field_key in select distinct e->>'fact_key' from jsonb_array_elements(p_observations) e order by 1 loop
    perform pg_advisory_xact_lock(hashtextextended('canonical-fact:'||p_tenant||':'||p_entity_ref||':'||field_key,0));
  end loop;
  digest := encode(sha256(convert_to(jsonb_build_object('entity_ref',p_entity_ref,
    'observations',p_observations)::text,'UTF8')),'hex');
  perform pg_advisory_xact_lock(hashtextextended('fact-observation-batch:'||p_tenant||':'||p_idempotency_key,0));
  select * into batch from ops.fact_observation_batch b
   where b.organization_tenant_id=p_tenant and b.idempotency_key=p_idempotency_key;
  if found then
    if batch.request_sha256<>digest then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    replayed := true;
  else
    update ops.fact_observation o set cited_text=null
     where o.organization_tenant_id=p_tenant and o.cited_text is not null and o.cited_text_expires_at<=now();
    insert into ops.fact_observation_batch(organization_tenant_id,idempotency_key,request_sha256,recorded_by_actor_id)
      values(p_tenant,p_idempotency_key,digest,p_actor_id) returning * into batch;
    for item,ord in select e,n from jsonb_array_elements(p_observations) with ordinality as x(e,n) loop
      select * into contract from ops.fact_key_contract c where c.fact_key=item->>'fact_key';
      multiplier := (contract.unit_multipliers->>(item->>'unit'))::numeric;
      cited := item#>>'{source,cited_text}';
      if multiplier is null or cited is null
         or contract.expense_basis_required is distinct from (item ? 'expense_basis') then
        raise exception 'invalid fact observation' using errcode='22023';
      end if;
      if contract.fact_key='rent_schedule' and not ops.valid_rent_schedule(item->'value') then
        raise exception 'invalid schedule' using errcode='22023';
      end if;
      insert into ops.fact_observation(organization_tenant_id,batch_id,ordinal,observation_ref,entity_ref,fact_key,
        value,unit,expense_basis,normalized_value,normalized_schedule,document_ref,content_hash,document_family_id,derivation_chain,
        page_start,page_end,cited_text,cited_text_sha256,cited_text_expires_at,method,extractor_version,
        extraction_pass,extraction_model,confidence_tier,region_id)
      values(p_tenant,batch.id,ord,item->>'id',p_entity_ref,contract.fact_key,item->>'value',item->>'unit',
        item->>'expense_basis',case when contract.fact_key<>'rent_schedule' then (item->>'value')::numeric*multiplier end,
        case when contract.fact_key='rent_schedule' then ops.schedule_terms(item->'value') end,item#>>'{source,document_ref}',
        lower(item#>>'{source,content_hash}'),item#>>'{source,document_family_id}',
        coalesce(array(select jsonb_array_elements_text(item#>'{source,derivation_chain}')),'{}'),
        (item#>>'{source,page_start}')::integer,(item#>>'{source,page_end}')::integer,cited,
        encode(sha256(convert_to(cited,'UTF8')),'hex'),now()+interval '365 days',
        item->>'method',item->>'extractor_version',item#>>'{extraction,pass}',item#>>'{extraction,model}',
        item->>'confidence_tier',item#>>'{source,region_id}');
    end loop;
  end if;
  return jsonb_build_object('ok',true,'replayed',replayed,
    'batch_id',batch.id,'observations',(select jsonb_agg(jsonb_build_object('id',o.id,'observation_ref',o.observation_ref)
      order by o.ordinal) from ops.fact_observation o where o.batch_id=batch.id));
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
  if exists(select 1 from unnest(inputs) o where (o.normalized_value,o.normalized_schedule,o.expense_basis)
            is distinct from (chosen.normalized_value,chosen.normalized_schedule,chosen.expense_basis)) then
    return jsonb_build_object('ok',false,'error','observations_disagree');
  end if;
  if p_basis='human_review' then
    if cardinality(inputs)<>1 or not exists(select 1 from public.actor a where a.id=p_actor_id and a.kind='human' and a.active) then
      return jsonb_build_object('ok',false,'error','human_review_requires_one_observation_and_a_human');
    end if;
  else
    if exists(select 1 from unnest(inputs) o where o.fact_key='rent_schedule' or o.method<>'text_layer' or o.confidence_tier<>'auto_accept') then
      return jsonb_build_object('ok',false,'error','observation_not_rule_eligible');
    end if;
    if exists(select 1 from ops.fact_observation o where o.organization_tenant_id=p_tenant
               and o.entity_ref=p_entity_ref and o.fact_key=p_fact_key
               and (o.normalized_value,o.normalized_schedule,o.expense_basis) is distinct from (chosen.normalized_value,chosen.normalized_schedule,chosen.expense_basis)) then
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
  insert into ops.canonical_fact(organization_tenant_id,entity_ref,fact_key,version,supersedes_id,normalized_value,normalized_schedule,
    canonical_unit,expense_basis,basis,observation_ids,promoted_by_actor_id,idempotency_key,request_sha256)
  select p_tenant,p_entity_ref,p_fact_key,p_base_version+1,current_fact.id,chosen.normalized_value,chosen.normalized_schedule,c.canonical_unit,
    chosen.expense_basis,p_basis,array(select o.id from unnest(inputs) o),p_actor_id,p_idempotency_key,digest
    from ops.fact_key_contract c where c.fact_key=p_fact_key
  returning * into promoted;
  return jsonb_build_object('ok',true,'replayed',false,'canonical_fact',(to_jsonb(promoted)-'request_sha256')
    ||jsonb_build_object('normalized_value',trim_scale(promoted.normalized_value)::text));
end $$;

create or replace function ops.fact_review_field(p_tenant text,p_entity_ref text,p_fact_key text) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
  with evidence as (
    select coalesce(array_agg(o.id order by o.id),'{}'::uuid[]) ids,
      max(o.observed_at) newest,
      count(distinct (o.normalized_value,o.normalized_schedule,coalesce(o.expense_basis,'')))>1 conflict,
      bool_or(o.confidence_tier='verify_band') verify_band,
      bool_or(o.fact_key='rent_schedule' or o.confidence_tier='human_review' or o.method in ('ocr','human')) human_review,
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
      and not (coalesce(d.observation_ids=e.ids and d.extraction_job_ids=x.ids and (d.canonical_version=coalesce(c.version,0)
          or exists(select 1 from ops.canonical_fact reviewed
            where reviewed.organization_tenant_id=p_tenant and reviewed.entity_ref=p_entity_ref
              and reviewed.fact_key=p_fact_key and reviewed.version=d.canonical_version
              and (reviewed.normalized_value,reviewed.normalized_schedule,reviewed.canonical_unit,reviewed.expense_basis,reviewed.observation_ids)
                is not distinct from (c.normalized_value,c.normalized_schedule,c.canonical_unit,c.expense_basis,c.observation_ids))),false)
        or coalesce(d.id is null and c.basis='human_review' and c.promoted_at>=e.newest and (x.newest is null or c.promoted_at>=x.newest),false)),
    'priority',case when e.conflict or e.verify_band or exists(select 1 from jsonb_array_elements(x.abstentions) a where a->>'reason'='ocr_disagreement') then 'high' else 'normal' end,
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

create or replace function ops.record_deal_extraction(p_tenant text,p_actor_id uuid,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare contract jsonb := p_args-'content_hash'-'receipt'-'observations';
  source jsonb; result jsonb; bound jsonb; receipt jsonb; item jsonb; field_key text;
  expected integer := case when p_args->>'extractor_version'='deal-regions.v2' then 5 else 4 end;
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
  if jsonb_array_length(p_args->'observations')+jsonb_array_length(p_args#>'{receipt,abstentions}')<>expected
    or (select count(distinct k) from (
      select e->>'fact_key' k from jsonb_array_elements(p_args->'observations') e
      union all select e->>'fact_key' from jsonb_array_elements(p_args#>'{receipt,abstentions}') e
    ) f join ops.fact_key_contract c on c.fact_key=f.k)<>expected then
    return jsonb_build_object('ok',false,'error','validation_error');
  end if;
  bound := '[]'::jsonb;
  for item in select e from jsonb_array_elements(p_args->'observations') e loop
    if (item->>'method' is null or item->>'method' not in ('text_layer','ocr')) or item->>'confidence_tier' is distinct from 'human_review'
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
