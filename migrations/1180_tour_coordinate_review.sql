-- Internal review evidence only. No new executable runtime capability.
-- rollback: forward function replacement; lock-review: no table changes.
create function ops.tour_property_coordinate_review(p_tenant text,p_property uuid,p_as_of timestamptz)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
with display_facts as (
  select distinct on (a.field_key) a.field_key,a.value
  from ops.tour_field_assertion a
  join ops.tour_property p on p.organization_tenant_id=a.organization_tenant_id and p.id=a.property_id and p.property_status='active'
  join ops.tour_source_evidence e on e.organization_tenant_id=a.organization_tenant_id and e.id=a.source_evidence_id
    and e.rights_receipt_id=a.rights_receipt_id and e.retrieval_status='read' and e.retrieved_at<=p_as_of
  join ops.tour_rights_receipt r on r.organization_tenant_id=a.organization_tenant_id and r.id=a.rights_receipt_id
    and r.status='active' and r.revoked_at is null and r.effective_at<=p_as_of
    and (r.expires_at is null or r.expires_at>p_as_of) and r.allowed_use_classes ? 'canonical_fact'
    and (r.allowed_field_classes ? a.field_key or r.allowed_field_classes ? '*')
    and not exists(select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id
      and newer.provider=r.provider and newer.policy_key=r.policy_key
      and newer.receipt_version>r.receipt_version and newer.effective_at<=p_as_of)
  where a.organization_tenant_id=p_tenant and a.property_id=p_property and a.field_key in ('display.name','display.address')
    and a.review_state='reviewed' and a.observed_at<=p_as_of and a.effective_from<=p_as_of
    and (a.effective_to is null or a.effective_to>p_as_of) and jsonb_typeof(a.value)='string'
  order by a.field_key,a.effective_from desc,a.observed_at desc,a.id desc
), candidates as (
  select c.*, e.stable_locator, e.retrieval_status, e.retrieved_at,
    r.provider rights_provider,r.policy_key,r.receipt_version,r.terms_url,r.allowed_use_classes,r.allowed_field_classes,
    r.effective_at,r.expires_at,r.revoked_at,r.status rights_status,
    coalesce(r.status='active' and r.revoked_at is null and r.effective_at<=p_as_of
      and (r.expires_at is null or r.expires_at>p_as_of)
      and e.rights_receipt_id=r.id and e.retrieval_status='read' and e.retrieved_at<=p_as_of
      and r.allowed_use_classes ? case when c.provider is null then 'canonical_fact' else 'provider_projection' end
      and (r.allowed_field_classes ? 'coordinates' or r.allowed_field_classes ? '*')
      and not exists(select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id
        and newer.provider=r.provider and newer.policy_key=r.policy_key
        and newer.receipt_version>r.receipt_version and newer.effective_at<=p_as_of),false) usable
  from ops.tour_property_coordinate_candidate c
  join ops.tour_property p on p.organization_tenant_id=c.organization_tenant_id and p.id=c.property_id and p.property_status='active'
  left join ops.tour_source_evidence e on e.organization_tenant_id=c.organization_tenant_id and e.id=c.source_evidence_id
  left join ops.tour_rights_receipt r on r.organization_tenant_id=c.organization_tenant_id and r.id=c.rights_receipt_id
  where c.organization_tenant_id=p_tenant and c.property_id=p_property and c.observed_at<=p_as_of and c.created_at<=p_as_of
), projected as (
  select c.id,c.observed_at,jsonb_build_object(
    'coordinate_candidate_id',c.id,'property_id',c.property_id,'coordinate_role',c.coordinate_role,
    'precision_class',c.precision_class,'observed_at',c.observed_at,'review_state',c.review_state,
    'position',case when c.usable then jsonb_build_object('latitude',c.latitude,'longitude',c.longitude) else null end,
    'access_notes',case when c.usable then c.access_notes else null end,
    'source',jsonb_build_object('source_evidence_id',c.source_evidence_id,'provider',c.provider,
      'stable_locator',case when c.usable then c.stable_locator else null end,'retrieved_at',c.retrieved_at,'retrieval_status',c.retrieval_status),
    'rights',jsonb_build_object('rights_receipt_id',c.rights_receipt_id,'status',case when c.usable then 'usable' else 'unavailable' end,
      'provider',c.rights_provider,'policy_key',c.policy_key,'receipt_version',c.receipt_version,'terms_url',c.terms_url,
      'allowed_use_classes',c.allowed_use_classes,'allowed_field_classes',c.allowed_field_classes,
      'effective_at',c.effective_at,'expires_at',c.expires_at,'revoked_at',c.revoked_at,'receipt_status',c.rights_status),
    'approval',jsonb_build_object('status',case when not c.usable then 'rights_unavailable'
      when c.coordinate_role not in ('entrance','driveway','parking_access') then 'ineligible_role'
      when c.review_state<>'reviewed' then 'candidate_not_reviewed'
      when er.id is null then 'unverified' else 'verified_entrance' end,
      'receipt',case when c.usable and c.coordinate_role in ('entrance','driveway','parking_access') and c.review_state='reviewed' and er.id is not null
        then jsonb_build_object('verification_receipt_id',er.id,'verifier_actor_id',er.verifier_actor_id,
          'verified_at',er.verified_at,'evidence_reference',er.evidence_reference,
          'native_navigation_proof',er.native_navigation_proof,'receipt_digest',er.receipt_digest) else null end)) data
  from candidates c
  left join ops.tour_coordinate_entrance_verification_receipt er on er.organization_tenant_id=c.organization_tenant_id
    and er.property_id=c.property_id and er.coordinate_candidate_id=c.id and er.verified_at<=p_as_of and er.created_at<=p_as_of
)
select jsonb_build_object('schema','tour-coordinate-review.v1','property_id',p_property,'as_of',p_as_of,
  'property_name',(select value#>>'{}' from display_facts where field_key='display.name'),
  'property_address',(select value#>>'{}' from display_facts where field_key='display.address'),
  'status',case when count(*)=0 then 'unknown' else 'candidates' end,
  'reason',case when count(*)=0 then 'no_observed_candidates' else null end,
  'property_evidence',ops.read_tour_property_evidence(p_tenant,p_property,p_as_of),
  'candidates',coalesce(jsonb_agg(data order by observed_at desc,id),'[]'::jsonb)) from projected
$$;
revoke all on function ops.tour_property_coordinate_review(text,uuid,timestamptz) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

do $extend_detail$
declare definition text; extended text;
begin
  select pg_get_functiondef('ops.read_tour_internal_detail(text,uuid,text)'::regprocedure) into definition;
  extended := replace(definition,'''access_coordinate_status'',s.access_coordinate_status,',
    '''coordinate_review'',ops.tour_property_coordinate_review(s.organization_tenant_id,s.property_id,now()),''access_coordinate_status'',s.access_coordinate_status,');
  if extended=definition then raise exception 'Tour coordinate review extension anchor missing'; end if;
  execute extended;
end $extend_detail$;
