-- rollback: revoke execute on function ops.market_terms_snapshot(text) from carr_reader; roll back the read verb deployment. Retain classification and fact receipts; restore the grant to resume.
-- lock-review: new tables and functions; brief unique-constraint lock on canonical_fact. No evidence is rewritten.

alter table ops.canonical_fact add constraint canonical_fact_tenant_identity unique(id,organization_tenant_id);

-- Context classifies a promoted fact. Extraction/backfill must supply reviewed context before comparison.
create table ops.market_term_context (
  canonical_fact_id uuid primary key,
  organization_tenant_id text not null,
  submarket text not null check(length(btrim(submarket)) between 1 and 200),
  use_type text not null check(length(btrim(use_type)) between 1 and 200),
  term_kind text not null check(term_kind in ('counter','executed')),
  term_date date not null,
  effective_from date not null,
  effective_to date check(effective_to>effective_from),
  document_ref text not null,
  reviewed_by_actor_id uuid not null references public.actor(id),
  reviewed_at timestamptz not null default now(),
  foreign key(canonical_fact_id,organization_tenant_id) references ops.canonical_fact(id,organization_tenant_id)
);
create function ops.validate_market_term_context() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  if not exists(select 1 from public.actor where id=new.reviewed_by_actor_id and kind='human' and active)
    or not exists(select 1 from ops.canonical_fact f join ops.fact_observation o
      on o.id=any(f.observation_ids) and o.organization_tenant_id=f.organization_tenant_id
      where f.id=new.canonical_fact_id and f.organization_tenant_id=new.organization_tenant_id
        and o.document_ref=new.document_ref) then
    raise exception 'context requires human classification and canonical source' using errcode='23514';
  end if;
  return new;
end $$;
create trigger market_term_context_validate before insert on ops.market_term_context
  for each row execute function ops.validate_market_term_context();
create trigger market_term_context_append_only before update or delete on ops.market_term_context
  for each row execute function ops.fact_store_append_only();

-- Owner-managed quarantine is reversible and tenant-local. Evidence receipts survive it.
create table ops.market_term_quarantine (
  organization_tenant_id text not null,
  extractor_version text not null check(length(btrim(extractor_version)) between 1 and 100),
  reason text not null check(length(btrim(reason)) between 1 and 2000),
  primary key(organization_tenant_id,extractor_version)
);
revoke all on ops.market_term_context,ops.market_term_quarantine from public,carr_reader,carr_writer,carr_authority,dot_reader;
revoke all on function ops.validate_market_term_context() from public;

create function ops.market_terms_snapshot(p_tenant text) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
  with facts as (
    select f.*,x.submarket,x.use_type,x.term_kind,x.term_date,x.effective_from,x.effective_to,
      x.document_ref context_document_ref,x.reviewed_by_actor_id,x.reviewed_at
    from ops.canonical_fact f join ops.market_term_context x on x.canonical_fact_id=f.id
      and x.organization_tenant_id=f.organization_tenant_id
    where f.organization_tenant_id=p_tenant
  ), supported as (
    select f.*,s.sources,
      f.basis='human_review' or exists(
        -- Reapply promote_fact's proven-independence rule after quarantine.
        select 1 from ops.fact_observation a cross join ops.fact_observation b
        where a.id=any(f.observation_ids) and b.id=any(f.observation_ids) and a.id<b.id
          and a.organization_tenant_id=p_tenant and b.organization_tenant_id=p_tenant
          and a.entity_ref=f.entity_ref and b.entity_ref=f.entity_ref
          and a.fact_key=f.fact_key and b.fact_key=f.fact_key
          and a.normalized_value=f.normalized_value and b.normalized_value=f.normalized_value
          and a.expense_basis is not distinct from f.expense_basis and b.expense_basis is not distinct from f.expense_basis
          and a.method='text_layer' and b.method='text_layer'
          and a.confidence_tier='auto_accept' and b.confidence_tier='auto_accept'
          and a.content_hash<>b.content_hash and a.document_ref<>b.document_ref
          and not ((a.document_family_id||a.derivation_chain) && (b.document_family_id||b.derivation_chain))
          and a.document_family_id is not null and b.document_family_id is not null
          and a.extraction_pass is not null and b.extraction_pass is not null
          and (a.extraction_pass,a.extraction_model)<>(b.extraction_pass,b.extraction_model)
          and not exists(select 1 from ops.market_term_quarantine q where q.organization_tenant_id=p_tenant
            and q.extractor_version in (a.extractor_version,b.extractor_version))
      ) eligible
    from facts f cross join lateral (
      select coalesce(jsonb_agg(jsonb_build_object('observation_id',o.id,'document_ref',o.document_ref,
        'content_hash',o.content_hash,'page_start',o.page_start,'page_end',o.page_end,
        'extractor_version',o.extractor_version,'confidence_tier',o.confidence_tier,
        'quarantined',exists(select 1 from ops.market_term_quarantine q
          where q.organization_tenant_id=p_tenant and q.extractor_version=o.extractor_version)) order by o.id),'[]') sources
      from ops.fact_observation o where o.organization_tenant_id=p_tenant and o.id=any(f.observation_ids)
    ) s
  )
  select jsonb_build_object('receipts',coalesce((select jsonb_agg(jsonb_build_object(
    'canonical_fact_id',id,'entity_ref',entity_ref,'fact_key',fact_key,'version',version,
    'value',trim_scale(normalized_value)::text,'unit',canonical_unit,'expense_basis',expense_basis,
    'confidence',basis,'submarket',submarket,'use_type',use_type,'term_kind',term_kind,'term_date',term_date,
    'effective_from',effective_from,'effective_to',effective_to,'eligible',eligible,
    'context_source',jsonb_build_object('document_ref',context_document_ref,'reviewed_by_actor_id',reviewed_by_actor_id,
      'reviewed_at',reviewed_at),'sources',sources) order by entity_ref,fact_key,version) from supported),'[]'),
    'coverage',jsonb_build_object(
      'scope','recorded_firm_history_only','history_coverage','unknown','archive_documents',null,
      'recorded_documents',(select count(distinct document_ref) from ops.fact_observation where organization_tenant_id=p_tenant),
      'observations',(select count(*) from ops.fact_observation where organization_tenant_id=p_tenant),
      'canonical_receipts',(select count(*) from ops.canonical_fact where organization_tenant_id=p_tenant),
      'classified_receipts',(select count(*) from facts),
      'quarantined_extractor_versions',(select coalesce(jsonb_agg(extractor_version order by extractor_version),'[]')
        from ops.market_term_quarantine where organization_tenant_id=p_tenant)))
$$;
revoke all on function ops.market_terms_snapshot(text) from public;
grant execute on function ops.market_terms_snapshot(text) to carr_reader;

do $contract$
declare contract jsonb;
begin
  contract := ops.scac_current_grant_contract();
  contract := jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||jsonb_build_array(jsonb_build_object(
    'config',jsonb_build_array('search_path=pg_catalog, pg_temp'),'grantee','carr_reader','parallel','u',
    'grantable',false,'privilege','execute','signature','ops.market_terms_snapshot(p_tenant text)',
    'volatility','s','ingress_key','db-function-acl:ops.market_terms_snapshot(p_tenant text):carr_reader:execute',
    'ingress_kind','db_function_acl','function_kind','f','security_definer',true)));
  execute format('CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT '||quote_literal(contract::text)||'::jsonb');
end $contract$;
