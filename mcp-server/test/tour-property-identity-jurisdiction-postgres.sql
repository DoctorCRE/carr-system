-- Disposable proof for migration 0428. Run after the Tour foundation and Slice
-- 2 migrations. It makes no durable record because the final rollback is part
-- of the proof contract.
begin;
set local carr.verified_human_actor_slug='joe';

do $proof$
declare
  v_tenant constant text := 'tour-slice3-proof';
  v_property_one constant uuid := '30000000-0000-4000-8000-000000000001';
  v_property_two constant uuid := '30000000-0000-4000-8000-000000000002';
  v_rights constant uuid := '30000000-0000-4000-8000-000000000010';
  v_evidence constant uuid := '30000000-0000-4000-8000-000000000011';
  v_other_tenant constant text := 'tour-slice3-proof-other';
  v_other_rights constant uuid := '30000000-0000-4000-8000-000000000012';
  v_other_evidence constant uuid := '30000000-0000-4000-8000-000000000013';
  v_identifier uuid;
  v_conflicting_identifier uuid;
  v_coordinate uuid;
  v_dataset uuid;
begin
  insert into ops.tour_rights_receipt (
    id,organization_tenant_id,provider,sku,policy_key,receipt_version,
    receipt_digest,terms_url,reviewed_at,reviewer,intended_use,
    allowed_field_classes,allowed_use_classes,effective_at,status
  ) values (
    v_rights,v_tenant,'proof-authority','proof-sku','tour-slice3-v1',1,
    'sha256:' || repeat('a',64),'https://example.invalid/terms',
    '2026-08-27T08:00:00Z','actor:proof','Slice 3 proof',
    '["*"]'::jsonb,'["source_intake","canonical_fact","provider_projection"]'::jsonb,
    '2026-08-27T08:00:00Z','active'
  );
  insert into ops.tour_property (id,organization_tenant_id,property_status) values
    (v_property_one,v_tenant,'active'),(v_property_two,v_tenant,'active');
  insert into ops.tour_source_evidence (
    id,organization_tenant_id,stable_locator,evidence_class,retrieved_at,
    retrieval_status,content_digest,rights_receipt_id,rights_provider,
    rights_policy_key,data_classification
  ) values (
    v_evidence,v_tenant,'https://example.invalid/authoritative-source','direct_source',
    '2026-08-27T09:00:00Z','read','sha256:' || repeat('b',64),v_rights,
    'proof-authority','tour-slice3-v1','public'
  );

  v_identifier := ops.append_tour_property_identifier_assertion(jsonb_build_object(
    'organization_tenant_id',v_tenant,'property_id',v_property_one,
    'identifier_scheme','carr_property','identifier_value','CARR-ONE',
    'normalized_identifier','carr-one','source_evidence_id',v_evidence,
    'rights_receipt_id',v_rights,'observed_at','2026-08-27T10:00:00Z',
    'confidence','high','review_state','reviewed','assertion_digest','sha256:' || repeat('c',64)
  ));
  v_conflicting_identifier := ops.append_tour_property_identifier_assertion(jsonb_build_object(
    'organization_tenant_id',v_tenant,'property_id',v_property_two,
    'identifier_scheme','carr_property','identifier_value','CARR-ONE conflicting source',
    'normalized_identifier','carr-one','source_evidence_id',v_evidence,
    'rights_receipt_id',v_rights,'observed_at','2026-08-27T10:00:00Z',
    'confidence','high','review_state','reviewed','assertion_digest','sha256:' || repeat('9',64)
  ));
  if (select review_state from ops.tour_property_identifier_assertion
        where organization_tenant_id=v_tenant and id=v_conflicting_identifier) <> 'conflicted' then
    raise exception 'cross-property identifier collision was not preserved as conflicted evidence';
  end if;
  insert into ops.tour_property_identifier_alias (
    organization_tenant_id,property_id,identifier_assertion_id,alias_kind,
    alias_value,normalized_alias,source_evidence_id,rights_receipt_id,
    observed_at,alias_digest
  ) values (
    v_tenant,v_property_one,v_identifier,'legacy','Old CARR One','old-carr-one',
    v_evidence,v_rights,'2026-08-27T10:00:00Z','sha256:' || repeat('d',64)
  );
  insert into ops.tour_property_identity_lineage (
    organization_tenant_id,predecessor_property_id,successor_property_id,
    relationship,source_evidence_id,rights_receipt_id,effective_at,
    recorded_by_actor_id,rationale,lineage_digest
  ) values (
    v_tenant,v_property_one,v_property_two,'merged_into',v_evidence,v_rights,
    '2026-08-27T10:00:00Z','actor:proof','synthetic merge lineage',
    'sha256:' || repeat('e',64)
  );
  begin
    update ops.tour_property_identifier_assertion set review_state='withdrawn'
     where organization_tenant_id=v_tenant and id=v_identifier;
    raise exception 'proof expected immutable identifier assertion denial';
  exception when raise_exception then
    if sqlerrm <> 'tour_property_identifier_assertion is append-only' then raise; end if;
  end;

  -- Composite tenant FKs reject cross-tenant associations after a valid local
  -- evidence/rights guard has passed, rather than merely rejecting missing proof.
  insert into ops.tour_rights_receipt (
    id,organization_tenant_id,provider,sku,policy_key,receipt_version,
    receipt_digest,terms_url,reviewed_at,reviewer,intended_use,
    allowed_field_classes,allowed_use_classes,effective_at,status
  ) values (
    v_other_rights,v_other_tenant,'proof-authority-other','proof-sku','tour-slice3-v1',1,
    'sha256:' || repeat('2',64),'https://example.invalid/terms',
    '2026-08-27T08:00:00Z','actor:proof','Slice 3 cross-tenant proof',
    '["*"]'::jsonb,'["source_intake","canonical_fact","provider_projection"]'::jsonb,
    '2026-08-27T08:00:00Z','active'
  );
  insert into ops.tour_source_evidence (
    id,organization_tenant_id,stable_locator,evidence_class,retrieved_at,
    retrieval_status,content_digest,rights_receipt_id,rights_provider,
    rights_policy_key,data_classification
  ) values (
    v_other_evidence,v_other_tenant,'https://example.invalid/other-authority','direct_source',
    '2026-08-27T09:00:00Z','read','sha256:' || repeat('3',64),v_other_rights,
    'proof-authority-other','tour-slice3-v1','public'
  );
  begin
    insert into ops.tour_property_address_assertion (
      organization_tenant_id,property_id,address_value,address_role,source_evidence_id,
      rights_receipt_id,observed_at,effective_from,confidence,review_state
    ) values (
      v_other_tenant,v_property_one,'{"street":"cross tenant"}'::jsonb,'site',
      v_other_evidence,v_other_rights,'2026-08-27T10:00:00Z',
      '2026-08-27T10:00:00Z','high','reviewed'
    );
    raise exception 'proof expected composite property FK denial';
  exception when foreign_key_violation then null;
  end;
  insert into ops.tour_jurisdiction_dataset (
    organization_tenant_id,jurisdiction_type,state_code,county_name,
    authoritative_source_locator,dataset_version,source_crs,dataset_digest,
    source_evidence_id,rights_receipt_id,as_of,review_state
  ) values (
    v_tenant,'county','FL','Escambia','https://example.invalid/escambia',
    '2026-08','EPSG:4326','sha256:' || repeat('f',64),v_evidence,v_rights,
    '2026-08-27T10:00:00Z','reviewed'
  ) returning id into v_dataset;
  insert into ops.tour_property_jurisdiction_assertion (
    organization_tenant_id,property_id,jurisdiction_dataset_id,jurisdiction_name,
    assertion_method,source_evidence_id,rights_receipt_id,as_of,review_state
  ) values (
    v_tenant,v_property_one,v_dataset,'Escambia','authoritative_identifier',
    v_evidence,v_rights,'2026-08-27T10:00:00Z','reviewed'
  );

  -- The property read preserves the source-backed county at a selected time,
  -- then shows a later reviewed change without inferring a spatial ruling.
  if exists (select 1 from jsonb_array_elements(ops.read_tour_property_evidence(
      v_tenant,v_property_one,'2026-08-27T08:30:00Z')) row) then
    raise exception 'property evidence appeared before its source was retrieved';
  end if;
  if not exists (select 1 from jsonb_array_elements(ops.read_tour_property_evidence(
      v_tenant,v_property_one,'2026-08-28T00:00:00Z')) row
      where row->>'field'='county' and row->>'value'='Escambia'
        and row->>'source_crs'='EPSG:4326'
        and row->>'geometry_precision'='unknown'
        and row->>'geometry_method'='authoritative_identifier'
        and row->'source'->>'evidence_class'='direct_source') then
    raise exception 'reviewed Escambia county evidence was absent';
  end if;
  insert into ops.tour_jurisdiction_dataset (
    organization_tenant_id,jurisdiction_type,state_code,county_name,
    authoritative_source_locator,dataset_version,source_crs,dataset_digest,
    source_evidence_id,rights_receipt_id,as_of,review_state
  ) values (
    v_tenant,'county','FL','Walton','https://example.invalid/walton',
    '2026-09','EPSG:4326','sha256:' || repeat('8',64),v_evidence,v_rights,
    '2026-09-01T00:00:00Z','reviewed'
  ) returning id into v_dataset;
  insert into ops.tour_property_jurisdiction_assertion (
    organization_tenant_id,property_id,jurisdiction_dataset_id,jurisdiction_name,
    assertion_method,source_evidence_id,rights_receipt_id,as_of,review_state
  ) values (
    v_tenant,v_property_one,v_dataset,'Walton','manual_review',
    v_evidence,v_rights,'2026-09-01T00:00:00Z','reviewed'
  );
  if not exists (select 1 from jsonb_array_elements(ops.read_tour_property_evidence(
      v_tenant,v_property_one,'2026-09-02T00:00:00Z')) row
      where row->>'field'='county' and row->>'value'='Walton') then
    raise exception 'later reviewed Walton county evidence was absent';
  end if;
  begin
    insert into ops.tour_jurisdiction_dataset (
      organization_tenant_id,jurisdiction_type,state_code,county_name,
      authoritative_source_locator,dataset_version,dataset_digest,
      source_evidence_id,rights_receipt_id,as_of,review_state
    ) values (
      v_tenant,'county','AL','Baldwin','https://example.invalid/outside',
      '2026-09','sha256:' || repeat('7',64),v_evidence,v_rights,
      '2026-09-01T00:00:00Z','reviewed'
    );
    raise exception 'out-of-area jurisdiction was accepted';
  exception when check_violation then null;
  end;

  v_coordinate := ops.append_tour_coordinate_candidate(jsonb_build_object(
    'organization_tenant_id',v_tenant,'property_id',v_property_one,
    'coordinate_role','entrance','latitude',30.4156,'longitude',-87.2169,
    'precision_class','entrance','source_evidence_id',v_evidence,
    'rights_receipt_id',v_rights,'provider',null,'observed_at','2026-08-27T10:00:00Z',
    'review_state','reviewed','access_notes','north drive'
  ));
  perform set_config('carr.verified_human_actor_slug','',true);
  begin
    perform ops.append_tour_entrance_verification_receipt(jsonb_build_object(
      'organization_tenant_id',v_tenant,'property_id',v_property_one,
      'coordinate_candidate_id',v_coordinate,'verifier_actor_id',(select id::text from actor where slug='joe'),
      'verified_at','2026-08-27T10:05:00Z','evidence_reference','imagery:proof',
      'native_navigation_proof',jsonb_build_object('platform','apple_maps','device_tested',true),
      'receipt_digest','sha256:' || repeat('0',64)
    ));
    raise exception 'proof expected sponsored entrance verification refusal';
  exception when raise_exception then
    if sqlerrm <> 'entrance verification requires a verified human authority session' then raise; end if;
  end;
  perform set_config('carr.verified_human_actor_slug','joe',true);
  perform ops.append_tour_entrance_verification_receipt(jsonb_build_object(
    'organization_tenant_id',v_tenant,'property_id',v_property_one,
    'coordinate_candidate_id',v_coordinate,'verifier_actor_id',(select id::text from actor where slug='joe'),
    'verified_at','2026-08-27T10:05:00Z','evidence_reference','imagery:proof',
    'native_navigation_proof',jsonb_build_object('platform','apple_maps','device_tested',true),
    'receipt_digest','sha256:' || repeat('1',64)
  ));
  begin
    insert into ops.tour_property_coordinate_candidate (
      organization_tenant_id,property_id,coordinate_role,latitude,longitude,
      precision_class,source_evidence_id,rights_receipt_id,provider,observed_at,review_state
    ) values (
      v_tenant,v_property_two,'entrance',30.42,-87.22,'entrance',v_evidence,
      v_rights,'provider-only','2026-08-27T10:00:00Z','reviewed'
    );
    raise exception 'proof expected provider entrance candidate denial';
  exception when raise_exception then
    if sqlerrm <> 'provider coordinate requires non-canonical candidate role' then raise; end if;
  end;

  begin
    update ops.tour_property_coordinate_candidate set review_state='superseded'
     where organization_tenant_id=v_tenant and id=v_coordinate;
    raise exception 'proof expected immutable coordinate candidate denial';
  exception when raise_exception then
    if sqlerrm <> 'tour_property_coordinate_candidate is append-only' then raise; end if;
  end;
  begin
    perform ops.append_tour_property_identifier_assertion('{}'::jsonb);
    raise exception 'proof expected identifier exact-key denial';
  exception when raise_exception then
    if sqlerrm <> 'property identifier assertion payload is invalid' then raise; end if;
  end;
  begin
    perform ops.append_tour_coordinate_candidate('{}'::jsonb);
    raise exception 'proof expected coordinate exact-key denial';
  exception when raise_exception then
    if sqlerrm <> 'coordinate candidate payload is invalid' then raise; end if;
  end;
  begin
    perform ops.append_tour_entrance_verification_receipt('{}'::jsonb);
    raise exception 'proof expected entrance receipt exact-key denial';
  exception when raise_exception then
    if sqlerrm <> 'entrance verification receipt payload is invalid' then raise; end if;
  end;
  if not exists (select 1 from ops.tour_coordinate_entrance_verification_receipt
                  where organization_tenant_id=v_tenant and coordinate_candidate_id=v_coordinate) then
    raise exception 'proof did not retain human entrance verification';
  end if;
  if has_table_privilege('carr_authority','ops.tour_property_identifier_assertion','INSERT')
     or has_table_privilege('carr_writer','ops.tour_property_coordinate_candidate','INSERT')
     or has_table_privilege('carr_authority','ops.tour_coordinate_entrance_verification_receipt','INSERT')
     or not has_function_privilege('carr_writer','ops.append_tour_coordinate_candidate(jsonb)','EXECUTE')
     or not has_function_privilege('carr_authority','ops.append_tour_property_identifier_assertion(jsonb)','EXECUTE')
     or not has_function_privilege('carr_authority','ops.append_tour_entrance_verification_receipt(jsonb)','EXECUTE') then
    raise exception 'Slice 3 application authority boundary is wrong';
  end if;
end
$proof$;

rollback;
