-- rollback: restore the prior fact_review_field definition; evidence and receipts remain unchanged.
-- lock-review: CREATE OR REPLACE takes only the function metadata lock; no table writes.

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
      and not (coalesce(d.observation_ids=e.ids and (d.canonical_version=coalesce(c.version,0)
          or exists(select 1 from ops.canonical_fact reviewed
            where reviewed.organization_tenant_id=p_tenant and reviewed.entity_ref=p_entity_ref
              and reviewed.fact_key=p_fact_key and reviewed.version=d.canonical_version
              and (reviewed.normalized_value,reviewed.canonical_unit,reviewed.expense_basis,reviewed.observation_ids)
                is not distinct from (c.normalized_value,c.canonical_unit,c.expense_basis,c.observation_ids))),false)
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
