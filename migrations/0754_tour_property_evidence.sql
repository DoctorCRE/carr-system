-- Authenticated, internal property evidence read for the first five Florida
-- counties. Source rows remain authoritative; this read draws no legal or
-- regulatory conclusion from jurisdiction or parcel context.
create or replace function ops.read_tour_property_evidence(p_tenant text,p_property uuid,p_as_of timestamptz)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
with raw as (
  select 'county'::text field, to_jsonb(d.county_name) value, a.as_of,
    a.as_of effective_from, null::timestamptz effective_to, a.review_state,
    'unknown'::text geometry_precision, a.assertion_method geometry_method,
    d.source_crs, a.source_evidence_id, a.rights_receipt_id
  from ops.tour_property_jurisdiction_assertion a
  join ops.tour_jurisdiction_dataset d on d.organization_tenant_id=a.organization_tenant_id
    and d.id=a.jurisdiction_dataset_id and d.review_state='reviewed'
    and d.state_code='FL' and d.county_name in ('Escambia','Santa Rosa','Okaloosa','Walton','Bay')
    and d.as_of<=a.as_of
  where a.organization_tenant_id=p_tenant and a.property_id=p_property
  union all
  select 'parcel', to_jsonb(a.parcel_identifier), a.as_of, a.as_of, null::timestamptz,
    a.review_state, 'unknown', a.geometry_method, a.source_crs,
    a.source_evidence_id, a.rights_receipt_id
  from ops.tour_property_parcel_assertion a
  where a.organization_tenant_id=p_tenant and a.property_id=p_property and a.geometry_method='authoritative_reference'
  union all
  select 'site_address', a.address_value, a.observed_at, a.effective_from, a.effective_to,
    a.review_state, 'unknown', null::text, null::text,
    a.source_evidence_id, a.rights_receipt_id
  from ops.tour_property_address_assertion a
  where a.organization_tenant_id=p_tenant and a.property_id=p_property and a.address_role='site'
  union all
  select 'building', to_jsonb(coalesce(a.building_name,a.building_identifier)), a.observed_at,
    a.observed_at, null::timestamptz, a.review_state, 'unknown', null::text, null::text,
    a.source_evidence_id, a.rights_receipt_id
  from ops.tour_property_building_assertion a
  where a.organization_tenant_id=p_tenant and a.property_id=p_property
), allowed as (
  select raw.field, raw.value, raw.as_of, raw.effective_from, raw.effective_to,
    raw.review_state, raw.geometry_precision, raw.geometry_method, raw.source_crs,
    jsonb_build_object('locator',e.stable_locator,'evidence_class',e.evidence_class,'retrieved_at',e.retrieved_at) source
  from raw
  join ops.tour_property p on p.organization_tenant_id=p_tenant and p.id=p_property and p.property_status='active'
  join ops.tour_source_evidence e on e.organization_tenant_id=p_tenant and e.id=raw.source_evidence_id
    and e.rights_receipt_id=raw.rights_receipt_id and e.retrieval_status='read'
    and e.evidence_class='direct_source' and e.retrieved_at<=p_as_of
  join ops.tour_rights_receipt r on r.organization_tenant_id=p_tenant and r.id=raw.rights_receipt_id
    and r.status='active' and r.revoked_at is null
    and r.effective_at<=p_as_of and (r.expires_at is null or r.expires_at>p_as_of)
    and r.allowed_use_classes ? 'canonical_fact'
    and (r.allowed_field_classes ? raw.field or r.allowed_field_classes ? '*')
    and not exists (select 1 from ops.tour_rights_receipt newer
      where newer.organization_tenant_id=r.organization_tenant_id
        and newer.provider=r.provider and newer.policy_key=r.policy_key
        and newer.receipt_version>r.receipt_version and newer.effective_at<=p_as_of)
  where raw.as_of<=p_as_of and raw.effective_from<=p_as_of
    and (raw.effective_to is null or raw.effective_to>p_as_of)
)
select coalesce(jsonb_agg(to_jsonb(allowed) order by field,as_of desc,effective_from desc),'[]'::jsonb)
from allowed
$$;

revoke all on function ops.read_tour_property_evidence(text,uuid,timestamp with time zone) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.read_tour_property_evidence(text,uuid,timestamp with time zone) to carr_writer,carr_authority;
