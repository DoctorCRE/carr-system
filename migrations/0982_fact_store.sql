-- rollback: forward-only; observations and promotion receipts are append-only evidence. Remove the three verbs, then drop the empty fact-store objects.
-- lock-review: creates new empty tables and definer functions; existing records are untouched.

-- WR-000039 E09a: the observation -> canonical fact store (plan PLAN-28c876e33fd0-v2, rulings 3.1 and 3.11).
-- Every write passes through the three definer functions below. No runtime role can touch the tables.

create table ops.fact_key_contract (
  fact_key text primary key check (fact_key ~ '^[a-z][a-z0-9_]{0,62}$'),
  canonical_unit text not null,
  unit_multipliers jsonb not null check (jsonb_typeof(unit_multipliers)='object'),
  expense_basis_required boolean not null
);
insert into ops.fact_key_contract values
  ('base_rent_per_sf','USD/SF/year','{"USD/SF/year":1,"USD/SF/month":12}',true),
  ('landlord_ti_per_sf','USD/SF','{"USD/SF":1}',false),
  ('term_months','months','{"months":1,"years":12}',false),
  ('free_rent_months','months','{"months":1}',false);

create table ops.fact_observation_batch (
  id uuid not null default gen_random_uuid() unique,
  organization_tenant_id text not null check (length(organization_tenant_id) between 1 and 100),
  idempotency_key uuid not null,
  request_sha256 text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_by_actor_id uuid not null references public.actor(id),
  recorded_at timestamptz not null default now(),
  primary key (organization_tenant_id, idempotency_key)
);

-- Ruling 3.11: cited_text is the only sensitive excerpt. It is retained for 365 days for review, then
-- purged to null; its SHA-256 stays so the receipt remains verifiable. Purge is the only permitted update.
-- Encryption at rest is the database platform's storage encryption. Definers return refusal codes, never
-- cited text, so error logs cannot carry an excerpt.
create table ops.fact_observation (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  batch_id uuid not null references ops.fact_observation_batch(id),
  ordinal integer not null check (ordinal between 1 and 100),
  observation_ref text not null check (length(btrim(observation_ref)) between 1 and 100),
  entity_ref text not null check (length(btrim(entity_ref)) between 1 and 200),
  fact_key text not null references ops.fact_key_contract(fact_key),
  value text not null check (value ~ '^(0|[1-9][0-9]{0,29})(\.[0-9]{1,18})?$'),
  unit text not null,
  expense_basis text check (expense_basis in ('nnn','modified_gross','full_service_gross','gross')),
  normalized_value numeric not null,
  document_ref text not null check (length(btrim(document_ref)) between 1 and 200),
  content_hash text check (content_hash ~ '^[0-9a-f]{64}$'),
  document_family_id text check (length(btrim(document_family_id)) between 1 and 200),
  derivation_chain text[] not null default '{}' check (cardinality(derivation_chain)<=20),
  page_start integer not null check (page_start between 1 and 100000),
  page_end integer not null check (page_end between page_start and 100000),
  cited_text text check (length(btrim(cited_text)) between 1 and 4000),
  cited_text_sha256 text not null check (cited_text_sha256 ~ '^[0-9a-f]{64}$'),
  cited_text_expires_at timestamptz not null,
  method text not null check (method in ('text_layer','ocr')),
  extractor_version text not null check (length(btrim(extractor_version)) between 1 and 100),
  extraction_pass text check (length(btrim(extraction_pass)) between 1 and 100),
  extraction_model text check (length(btrim(extraction_model)) between 1 and 100),
  confidence_tier text not null check (confidence_tier in ('auto_accept','verify_band','human_review')),
  observed_at timestamptz not null default now(),
  check ((extraction_pass is null)=(extraction_model is null)),
  unique (batch_id, observation_ref),
  unique (batch_id, ordinal)
);
create index fact_observation_field on ops.fact_observation(organization_tenant_id,entity_ref,fact_key);
create index fact_observation_citation_expiry on ops.fact_observation(organization_tenant_id,cited_text_expires_at)
  where cited_text is not null;

-- Canonical facts are a versioned projection: each promotion appends the next version of one
-- entity-field and is its own receipt (rule or human, exact observation inputs, idempotency key).
create table ops.canonical_fact (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  entity_ref text not null,
  fact_key text not null references ops.fact_key_contract(fact_key),
  version integer not null check (version>=1),
  supersedes_id uuid unique references ops.canonical_fact(id),
  normalized_value numeric not null,
  canonical_unit text not null,
  expense_basis text,
  basis text not null check (basis in ('independent_agreement','human_review')),
  observation_ids uuid[] not null check (cardinality(observation_ids)>=1),
  promoted_by_actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  request_sha256 text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  promoted_at timestamptz not null default now(),
  check ((version=1)=(supersedes_id is null)),
  unique (organization_tenant_id, entity_ref, fact_key, version),
  unique (organization_tenant_id, idempotency_key)
);

create function ops.fact_store_append_only() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  if tg_op='UPDATE' and tg_table_name='fact_observation' then
    if to_jsonb(old)->>'cited_text' is not null and to_jsonb(new)->>'cited_text' is null
       and (to_jsonb(new)-'cited_text')=(to_jsonb(old)-'cited_text') then
      return new;
    end if;
  end if;
  raise exception '% is append-only', tg_table_name using errcode='23514';
end $$;
create trigger fact_observation_append_only before update or delete on ops.fact_observation
  for each row execute function ops.fact_store_append_only();
create trigger canonical_fact_append_only before update or delete on ops.canonical_fact
  for each row execute function ops.fact_store_append_only();
create trigger fact_observation_batch_append_only before update or delete on ops.fact_observation_batch
  for each row execute function ops.fact_store_append_only();

revoke all on ops.fact_key_contract,ops.fact_observation_batch,ops.fact_observation,ops.canonical_fact
  from public,carr_reader,carr_writer,carr_authority,dot_reader;

create function ops.fact_observation_json(o ops.fact_observation) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
  select jsonb_build_object('id',o.id,'observation_ref',o.observation_ref,'entity_ref',o.entity_ref,
    'fact_key',o.fact_key,'value',o.value,'unit',o.unit,'expense_basis',o.expense_basis,
    'normalized_value',trim_scale(o.normalized_value)::text,
    'source',jsonb_build_object('document_ref',o.document_ref,'content_hash',o.content_hash,
      'document_family_id',o.document_family_id,'derivation_chain',to_jsonb(o.derivation_chain),
      'page_start',o.page_start,'page_end',o.page_end,
      'cited_text',case when o.cited_text_expires_at>now() then o.cited_text end,
      'cited_text_sha256',o.cited_text_sha256,'cited_text_expires_at',o.cited_text_expires_at),
    'method',o.method,'extractor_version',o.extractor_version,
    'extraction',case when o.extraction_pass is not null then
      jsonb_build_object('pass',o.extraction_pass,'model',o.extraction_model) end,
    'confidence_tier',o.confidence_tier,'observed_at',o.observed_at)
$$;
revoke all on function ops.fact_observation_json(ops.fact_observation) from public;

create function ops.record_fact_observations(p_tenant text,p_actor_id uuid,p_idempotency_key uuid,
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
begin
  if p_tenant is null or p_actor_id is null or p_idempotency_key is null or p_entity_ref is null
     or jsonb_typeof(p_observations) is distinct from 'array'
     or jsonb_array_length(p_observations) not between 1 and 100 then
    raise exception 'invalid fact observation batch' using errcode='22023';
  end if;
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

-- Ruling 3.1 in the kernel. Rule promotion needs at least two agreeing, auto-accept, text-layer
-- observations with no disagreeing observation on the entity-field, and at least one pair that is
-- PROVEN independent: different document, different content hash, disjoint document lineage
-- (family plus derivation chain), and a different extraction pass or model. Anything else is a human decision.
create function ops.promote_fact(p_tenant text,p_actor_id uuid,p_idempotency_key uuid,p_entity_ref text,
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
    if cardinality(inputs)<>1 or not exists(select 1 from public.actor a where a.id=p_actor_id and a.kind='human') then
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
    if not exists(select 1 from unnest(inputs) a cross join unnest(inputs) b
       where a.id<b.id
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

-- A conflict is an entity-field whose observations normalize to more than one value, unless a human
-- promotion recorded after its newest observation decided it.
create function ops.list_fact_conflicts(p_tenant text,p_entity_ref text,p_limit integer) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
  with fields as (
    select o.entity_ref,o.fact_key,max(o.observed_at) newest
      from ops.fact_observation o
     where o.organization_tenant_id=p_tenant and (p_entity_ref is null or o.entity_ref=p_entity_ref)
     group by o.entity_ref,o.fact_key
    having count(distinct (o.normalized_value,coalesce(o.expense_basis,'')))>1
  ), open_fields as (
    select f.*,c.version,c.basis,c.promoted_at
      from fields f
      left join lateral (select * from ops.canonical_fact c where c.organization_tenant_id=p_tenant
        and c.entity_ref=f.entity_ref and c.fact_key=f.fact_key order by c.version desc limit 1) c on true
     where not (c.basis is not distinct from 'human_review' and c.promoted_at>=f.newest)
     order by f.newest desc,f.entity_ref,f.fact_key
     limit least(greatest(coalesce(p_limit,50),1),100)
  )
  select jsonb_build_object('ok',true,'conflicts',coalesce(jsonb_agg(jsonb_build_object(
      'entity_ref',f.entity_ref,'fact_key',f.fact_key,'canonical_version',coalesce(f.version,0),
      'observations',(select jsonb_agg(ops.fact_observation_json(o) order by o.observed_at,o.batch_id,o.ordinal)
        from ops.fact_observation o where o.organization_tenant_id=p_tenant
         and o.entity_ref=f.entity_ref and o.fact_key=f.fact_key))
    order by f.newest desc,f.entity_ref,f.fact_key),'[]'::jsonb))
    from open_fields f
$$;

revoke all on function ops.fact_store_append_only() from public;
revoke all on function ops.record_fact_observations(text,uuid,uuid,text,jsonb) from public;
revoke all on function ops.promote_fact(text,uuid,uuid,text,text,text,uuid[],integer) from public;
revoke all on function ops.list_fact_conflicts(text,text,integer) from public;
grant usage on schema ops to carr_writer,carr_reader;
grant execute on function ops.record_fact_observations(text,uuid,uuid,text,jsonb) to carr_writer;
grant execute on function ops.promote_fact(text,uuid,uuid,text,text,text,uuid[],integer) to carr_writer;
grant execute on function ops.list_fact_conflicts(text,text,integer) to carr_reader;

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
      (1,'ops.record_fact_observations(p_tenant text, p_actor_id uuid, p_idempotency_key uuid, p_entity_ref text, p_observations jsonb)','carr_writer','v'),
      (2,'ops.promote_fact(p_tenant text, p_actor_id uuid, p_idempotency_key uuid, p_entity_ref text, p_fact_key text, p_basis text, p_observation_ids uuid[], p_base_version integer)','carr_writer','v'),
      (3,'ops.list_fact_conflicts(p_tenant text, p_entity_ref text, p_limit integer)','carr_reader','s')
    ) as grants(ordinal,signature,grantee,volatility)));
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb');
end $contract$;
