-- 0565_tour_property_registration.sql
--
-- The one door that creates a canonical ops.tour_property row.
--
-- WHY THIS EXISTS (2026-09-23, Joe: "turn the hosted tour surface on and feed
-- it sapala"). Every Tour write since 0318 is foreign-key bound to an existing
-- ops.tour_property row, and 0428 deliberately exposed identifier, coordinate
-- and entrance seams without a property-creation seam, so a real client search
-- could not enter the Tour data plane at all: the only rows ever inserted into
-- ops.tour_property were the proof seeds inside migrations. This closes that
-- gap with the narrowest possible door.
--
-- WHAT REGISTRATION IS. A property comes into existence only together with its
-- first rights-bound identifier assertion, under the same 0428 rights lineage
-- guard (evidence -> receipt -> provider/policy lock -> canonical_fact use
-- class). There is no bare "create property" path: an identity with no reviewed
-- source is exactly the thing 0428's design refuses. The identifier assertion
-- is written through the existing ops.append_tour_property_identifier_assertion
-- seam, so its guard, its conflict marking and its append-only trigger all
-- apply unchanged.
--
-- WHAT REGISTRATION REFUSES. A second registration of the same normalized
-- identifier under the same scheme in the same tenant, while an active
-- property already carries it in a live assertion state, is refused by name
-- rather than silently minting a duplicate identity. Cross-property collisions
-- that arrive through the assertion seam (not this one) keep 0428's
-- review_state='conflicted' behaviour; this door is the intake path and a
-- duplicate intake is an operator error, not evidence to preserve.
--
-- This does not choose a canonical identity among aliases, merge lineage,
-- assert an address, jurisdiction or coordinate, put the property on a Tour,
-- or publish anything. Those remain the existing reviewed seams.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs each
-- migration inside its own single transaction.

create or replace function ops.register_tour_property(p_payload jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_tenant text; v_property_id uuid; v_assertion_id uuid; v_existing uuid;
begin
  if jsonb_typeof(p_payload)<>'object' or (select array_agg(k order by k) from jsonb_object_keys(p_payload) k) is distinct from array['assertion_digest','confidence','identifier_scheme','identifier_value','normalized_identifier','observed_at','organization_tenant_id','review_state','rights_receipt_id','source_evidence_id'] then raise exception 'property registration payload is invalid'; end if;
  v_tenant:=p_payload->>'organization_tenant_id';
  if v_tenant is null or length(btrim(v_tenant))=0 then raise exception 'property registration payload is invalid'; end if;
  if p_payload->>'normalized_identifier' is distinct from lower(btrim(p_payload->>'normalized_identifier')) then raise exception 'identifier normalization must be lowercase and trimmed'; end if;
  select a.property_id into v_existing
    from ops.tour_property_identifier_assertion a
    join ops.tour_property p on p.organization_tenant_id=a.organization_tenant_id and p.id=a.property_id
   where a.organization_tenant_id=v_tenant
     and a.identifier_scheme=p_payload->>'identifier_scheme'
     and a.normalized_identifier=p_payload->>'normalized_identifier'
     and a.review_state in ('unreviewed','reviewed','conflicted')
     and p.property_status='active'
   order by a.created_at,a.id limit 1;
  if v_existing is not null then raise exception 'tour property is already registered under this identifier'; end if;
  insert into ops.tour_property (organization_tenant_id,property_status) values (v_tenant,'active') returning id into v_property_id;
  v_assertion_id:=ops.append_tour_property_identifier_assertion(jsonb_build_object(
    'organization_tenant_id',v_tenant,
    'property_id',v_property_id,
    'identifier_scheme',p_payload->>'identifier_scheme',
    'identifier_value',p_payload->>'identifier_value',
    'normalized_identifier',p_payload->>'normalized_identifier',
    'source_evidence_id',p_payload->>'source_evidence_id',
    'rights_receipt_id',p_payload->>'rights_receipt_id',
    'observed_at',p_payload->>'observed_at',
    'confidence',p_payload->>'confidence',
    'review_state',p_payload->>'review_state',
    'assertion_digest',p_payload->>'assertion_digest'));
  if v_assertion_id is null then raise exception 'property registration did not record its identifier assertion'; end if;
  return jsonb_build_object('property_id',v_property_id,'property_identifier_assertion_id',v_assertion_id);
end $$;

revoke all on function ops.register_tour_property(jsonb) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.register_tour_property(jsonb) to carr_authority;
comment on function ops.register_tour_property(jsonb) is 'Authority-only Tour property registration: creates one ops.tour_property row together with its first rights-bound identifier assertion through the 0428 seam; refuses a duplicate live identifier in the tenant; never selects identity, merges lineage, routes, maps or publishes.';

-- Disposable proof: registration creates the property and its assertion under
-- the rights guard, refuses a duplicate identifier, and rolls back.
do $proof$
declare
  v_tenant constant text := 'tour-registration-proof';
  v_rights constant uuid := '56400000-0000-4000-8000-000000000010';
  v_evidence constant uuid := '56400000-0000-4000-8000-000000000011';
  v_first jsonb; v_second jsonb; v_payload jsonb;
begin
  insert into ops.tour_rights_receipt (id,organization_tenant_id,provider,sku,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status)
  values (v_rights,v_tenant,'proof-authority','proof-sku','tour-registration-v1',1,'sha256:'||repeat('a',64),'https://example.invalid/terms','2026-09-23T08:00:00Z','actor:proof','registration proof','["*"]'::jsonb,'["source_intake","canonical_fact"]'::jsonb,'2026-09-23T08:00:00Z','active');
  insert into ops.tour_source_evidence (id,organization_tenant_id,stable_locator,evidence_class,retrieved_at,retrieval_status,content_digest,rights_receipt_id,rights_provider,rights_policy_key,data_classification)
  values (v_evidence,v_tenant,'https://example.invalid/listing','direct_source','2026-09-23T09:00:00Z','read','sha256:'||repeat('b',64),v_rights,'proof-authority','tour-registration-v1','public');
  v_payload:=jsonb_build_object('organization_tenant_id',v_tenant,'identifier_scheme','listing','identifier_value','CoStar 123','normalized_identifier','costar:123','source_evidence_id',v_evidence,'rights_receipt_id',v_rights,'observed_at','2026-09-23T09:05:00Z','confidence','high','review_state','reviewed','assertion_digest','sha256:'||repeat('c',64));
  v_first:=ops.register_tour_property(v_payload);
  if (v_first->>'property_id') is null or (v_first->>'property_identifier_assertion_id') is null then raise exception '0565 FAILED: registration returned no ids'; end if;
  if not exists (select 1 from ops.tour_property where organization_tenant_id=v_tenant and id=(v_first->>'property_id')::uuid and property_status='active') then raise exception '0565 FAILED: property row missing'; end if;
  if not exists (select 1 from ops.tour_property_identifier_assertion where organization_tenant_id=v_tenant and id=(v_first->>'property_identifier_assertion_id')::uuid and property_id=(v_first->>'property_id')::uuid and review_state='reviewed') then raise exception '0565 FAILED: identifier assertion missing'; end if;
  begin
    v_second:=ops.register_tour_property(v_payload);
    raise exception '0565 FAILED: duplicate registration was accepted';
  exception when raise_exception then
    if sqlerrm <> 'tour property is already registered under this identifier' then raise; end if;
  end;
  begin
    perform ops.register_tour_property(v_payload || jsonb_build_object('property_id','56400000-0000-4000-8000-000000000099'));
    raise exception '0565 FAILED: caller-supplied property_id was accepted';
  exception when raise_exception then
    if sqlerrm <> 'property registration payload is invalid' then raise; end if;
  end;
  begin
    perform ops.register_tour_property(v_payload || jsonb_build_object('normalized_identifier','Costar:456'));
    raise exception '0565 FAILED: unnormalized identifier was accepted';
  exception when raise_exception then
    if sqlerrm <> 'identifier normalization must be lowercase and trimmed' then raise; end if;
  end;
  if (select count(*) from ops.tour_property where organization_tenant_id=v_tenant) <> 1 then raise exception '0565 FAILED: refused registrations left property rows behind'; end if;
  raise exception 'ROLLBACK_0565_PROOF';
exception when raise_exception then
  if sqlerrm <> 'ROLLBACK_0565_PROOF' then raise; end if;
end $proof$;
