-- rollback: forward-only evidence; remove the review verbs before retiring these objects.
-- lock-review: short metadata locks on fact_observation; new tables and functions only.

alter table ops.fact_observation drop constraint fact_observation_method_check;
alter table ops.fact_observation add constraint fact_observation_method_check check (method in ('text_layer','ocr','human'));

create function ops.record_human_fact(p_tenant text,p_actor_id uuid,p_idempotency_key uuid,
  p_entity_ref text,p_observation jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare
  doc public.attachment%rowtype;
  out jsonb;
  item jsonb;
begin
  if not exists(select 1 from public.actor where id=p_actor_id and kind='human' and active) then
    return jsonb_build_object('ok',false,'error','human_only_verb_requires_verified_partner');
  end if;
  -- Attachments belong to the single internal CARR tenant; never infer another tenant from a caller.
  select * into doc from public.attachment a where p_tenant='carr-internal'
    and a.id::text=p_observation#>>'{source,document_ref}' and a.subject_type='deal'
    and a.subject_id::text=p_entity_ref and a.deleted_at is null for share;
  if not found then return jsonb_build_object('ok',false,'error','document_not_found'); end if;
  item := (p_observation-'source')||jsonb_build_object('id','human','method','human',
    'extractor_version','human-entry.v1','confidence_tier','human_review',
    'source',(p_observation->'source')||jsonb_build_object('content_hash',doc.sha256,'document_family_id',doc.id::text));
  out := ops.record_fact_observations(p_tenant,p_actor_id,p_idempotency_key,p_entity_ref,jsonb_build_array(item));
  if not (out->>'ok')::boolean then return out; end if;
  if exists(select 1 from ops.fact_observation_batch where id=(out->>'batch_id')::uuid and recorded_by_actor_id<>p_actor_id) then
    return jsonb_build_object('ok',false,'error','key_reuse');
  end if;
  return out||jsonb_build_object('observation',(select ops.fact_observation_json(o)||
    jsonb_build_object('recorded_by_actor_id',b.recorded_by_actor_id) from ops.fact_observation o join ops.fact_observation_batch b on b.id=o.batch_id where o.id=(out#>>'{observations,0,id}')::uuid));
end $$;
revoke all on function ops.record_human_fact(text,uuid,uuid,text,jsonb) from public;
grant execute on function ops.record_human_fact(text,uuid,uuid,text,jsonb) to carr_writer;

create table ops.fact_review_decision (
  id bigint generated always as identity primary key,
  organization_tenant_id text not null,
  entity_ref text not null,
  fact_key text not null references ops.fact_key_contract(fact_key),
  decision text not null check (decision in ('approve','correct','reject')),
  reason text not null check (length(btrim(reason)) between 1 and 2000),
  observation_ids uuid[] not null,
  chosen_observation_id uuid references ops.fact_observation(id),
  canonical_version integer not null,
  actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  request_sha256 text not null,
  result jsonb not null,
  decided_at timestamptz not null default now(),
  unique(organization_tenant_id,idempotency_key)
);
create index fact_review_decision_field on ops.fact_review_decision(organization_tenant_id,entity_ref,fact_key,id desc);
create trigger fact_review_decision_append_only before update or delete on ops.fact_review_decision
  for each row execute function ops.fact_store_append_only();
revoke all on ops.fact_review_decision from public,carr_reader,carr_writer,carr_authority,dot_reader;
revoke all on sequence ops.fact_review_decision_id_seq from public,carr_reader,carr_writer,carr_authority,dot_reader;

create function ops.fact_review_field(p_tenant text,p_entity_ref text,p_fact_key text) returns jsonb
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
  ), canonical as (
    select * from ops.canonical_fact where organization_tenant_id=p_tenant
      and entity_ref=p_entity_ref and fact_key=p_fact_key order by version desc limit 1
  ), decision as (
    select * from ops.fact_review_decision where organization_tenant_id=p_tenant
      and entity_ref=p_entity_ref and fact_key=p_fact_key order by id desc limit 1
  )
  select jsonb_build_object('entity_ref',p_entity_ref,'fact_key',p_fact_key,
    'canonical_version',coalesce(c.version,0),
    'review_version',encode(sha256(convert_to(jsonb_build_array(e.ids,coalesce(c.version,0),d.id)::text,'UTF8')),'hex'),
    'pending',cardinality(e.ids)>0 and (e.conflict or e.verify_band or e.human_review)
      and not (coalesce(d.observation_ids=e.ids and d.canonical_version=coalesce(c.version,0),false)
        or coalesce(d.id is null and c.basis='human_review' and c.promoted_at>=e.newest,false)),
    'reasons',to_jsonb(array_remove(array[case when e.conflict then 'conflict' end,
      case when e.verify_band then 'verify_band' end,case when e.human_review then 'human_review' end],null)),
    'observations',e.observations,
    'canonical_fact',case when c.id is not null then (to_jsonb(c)-'request_sha256')||
      jsonb_build_object('normalized_value',trim_scale(c.normalized_value)::text) end,
    'canonical_history',coalesce((select jsonb_agg((to_jsonb(h)-'request_sha256')||
      jsonb_build_object('normalized_value',trim_scale(h.normalized_value)::text) order by h.version)
      from ops.canonical_fact h where h.organization_tenant_id=p_tenant and h.entity_ref=p_entity_ref and h.fact_key=p_fact_key),'[]'),
    'review_history',coalesce((select jsonb_agg(to_jsonb(h)-'request_sha256'-'result' order by h.id)
      from ops.fact_review_decision h where h.organization_tenant_id=p_tenant and h.entity_ref=p_entity_ref and h.fact_key=p_fact_key),'[]'))
  from evidence e left join canonical c on true left join decision d on true
$$;
revoke all on function ops.fact_review_field(text,text,text) from public;

create function ops.list_fact_review(p_tenant text,p_entity_ref text,p_limit integer,p_cursor jsonb,p_include_resolved boolean) returns jsonb
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
    select distinct o.entity_ref,o.fact_key from ops.fact_observation o
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
revoke all on function ops.list_fact_review(text,text,integer,jsonb,boolean) from public;
grant execute on function ops.list_fact_review(text,text,integer,jsonb,boolean) to carr_reader;

create function ops.resolve_fact_review(p_tenant text,p_actor_id uuid,p_args jsonb) returns jsonb
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
  if jsonb_array_length(field->'observations')=0 then return jsonb_build_object('ok',false,'error','not_found'); end if;
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
  insert into ops.fact_review_decision(organization_tenant_id,entity_ref,fact_key,decision,reason,observation_ids,
    chosen_observation_id,canonical_version,actor_id,idempotency_key,request_sha256,result)
  values(p_tenant,entity,fact,action,p_args->>'reason',evidence,chosen,
    coalesce((out#>>'{canonical_fact,version}')::integer,0),p_actor_id,idem,digest,out) returning * into prior;
  return out||jsonb_build_object('review_receipt',to_jsonb(prior)-'request_sha256'-'result');
end $$;
revoke all on function ops.resolve_fact_review(text,uuid,jsonb) from public;
grant execute on function ops.resolve_fact_review(text,uuid,jsonb) to carr_writer;

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
      insert into ops.fact_observation(organization_tenant_id,batch_id,ordinal,observation_ref,entity_ref,fact_key,
        value,unit,expense_basis,normalized_value,document_ref,content_hash,document_family_id,derivation_chain,
        page_start,page_end,cited_text,cited_text_sha256,cited_text_expires_at,method,extractor_version,
        extraction_pass,extraction_model,confidence_tier)
      values(p_tenant,batch.id,ord,item->>'id',p_entity_ref,contract.fact_key,item->>'value',item->>'unit',
        item->>'expense_basis',(item->>'value')::numeric*multiplier,item#>>'{source,document_ref}',
        lower(item#>>'{source,content_hash}'),item#>>'{source,document_family_id}',
        coalesce(array(select jsonb_array_elements_text(item#>'{source,derivation_chain}')),'{}'),
        (item#>>'{source,page_start}')::integer,(item#>>'{source,page_end}')::integer,cited,
        encode(sha256(convert_to(cited,'UTF8')),'hex'),now()+interval '365 days',
        item->>'method',item->>'extractor_version',item#>>'{extraction,pass}',item#>>'{extraction,model}',
        item->>'confidence_tier');
    end loop;
  end if;
  return jsonb_build_object('ok',true,'replayed',replayed,
    'batch_id',batch.id,'observations',(select jsonb_agg(jsonb_build_object('id',o.id,'observation_ref',o.observation_ref)
      order by o.ordinal) from ops.fact_observation o where o.batch_id=batch.id));
end $$;


do $contract$
declare contract jsonb;
begin
  contract := ops.scac_current_grant_contract();
  contract := jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||(
    select jsonb_agg(jsonb_build_object('config',jsonb_build_array('search_path=pg_catalog, pg_temp'),
      'grantee',grantee,'parallel','u','grantable',false,'privilege','execute','signature',signature,
      'volatility',volatility,'ingress_key','db-function-acl:'||signature||':'||grantee||':execute',
      'ingress_kind','db_function_acl','function_kind','f','security_definer',true) order by ordinal)
    from (values
      (1,'ops.record_human_fact(p_tenant text, p_actor_id uuid, p_idempotency_key uuid, p_entity_ref text, p_observation jsonb)','carr_writer','v'),
      (2,'ops.list_fact_review(p_tenant text, p_entity_ref text, p_limit integer, p_cursor jsonb, p_include_resolved boolean)','carr_reader','s'),
      (3,'ops.resolve_fact_review(p_tenant text, p_actor_id uuid, p_args jsonb)','carr_writer','v')
    ) as grants(ordinal,signature,grantee,volatility)));
  execute format('CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT '||quote_literal(contract::text)||'::jsonb');
end $contract$;

create or replace function ops.list_fact_conflicts(p_tenant text,p_entity_ref text,p_limit integer) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
  with fields as (
    select o.entity_ref,o.fact_key,max(o.observed_at) newest from ops.fact_observation o
    where o.organization_tenant_id=p_tenant and (p_entity_ref is null or o.entity_ref=p_entity_ref)
    group by o.entity_ref,o.fact_key
  ), page as (
    select f.*,ops.fact_review_field(p_tenant,f.entity_ref,f.fact_key) item from fields f
  ), conflicts as (
    select * from page where (item->>'pending')::boolean and item->'reasons' ? 'conflict'
    order by newest desc,entity_ref,fact_key limit least(greatest(coalesce(p_limit,50),1),100)
  )
  select jsonb_build_object('ok',true,'conflicts',coalesce(jsonb_agg(jsonb_build_object(
    'entity_ref',entity_ref,'fact_key',fact_key,'canonical_version',item->'canonical_version',
    'observations',item->'observations') order by newest desc,entity_ref,fact_key),'[]')) from conflicts
$$;
