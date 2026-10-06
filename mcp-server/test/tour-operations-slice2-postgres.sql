\set ON_ERROR_STOP on

-- Run as the database owner against a database where schema.sql, 0318, and
-- 0427 are applied:
--   psql "$CARR_CI_DATABASE_URL" -v ON_ERROR_STOP=1 \
--     -f mcp-server/test/tour-operations-slice2-postgres.sql
--
-- Every fixture is inside this transaction and is rolled back at the end.
-- The proof intentionally uses the database-owner session for fixture setup;
-- ACL assertions below verify that application roles cannot bypass the typed
-- seams.

begin;

do $proof$
declare
  v_tenant constant text := 'tenant-α';
  v_tour_id constant uuid := '10000000-0000-4000-8000-000000000040';
  v_vector_projection_id constant uuid := '10000000-0000-4000-8000-000000000060';
  v_success_projection_id constant uuid := '10000000-0000-4000-8000-000000000061';
  v_incomplete_projection_id constant uuid := '10000000-0000-4000-8000-000000000062';
  v_conflict_projection_id constant uuid := '10000000-0000-4000-8000-000000000066';
  v_resolved_projection_id constant uuid := '10000000-0000-4000-8000-000000000067';
  v_property_one constant uuid := '10000000-0000-4000-8000-000000000010';
  v_property_two constant uuid := '10000000-0000-4000-8000-000000000011';
  v_rights_id constant uuid := '10000000-0000-4000-8000-000000000021';
  v_evidence_id constant uuid := '10000000-0000-4000-8000-000000000020';
  v_partial_evidence_id constant uuid := '10000000-0000-4000-8000-000000000022';
  v_name_one constant uuid := '10000000-0000-4000-8000-000000000030';
  v_address_two constant uuid := '10000000-0000-4000-8000-000000000031';
  v_address_one constant uuid := '10000000-0000-4000-8000-000000000032';
  v_name_two constant uuid := '10000000-0000-4000-8000-000000000033';
  v_partial_name constant uuid := '10000000-0000-4000-8000-000000000034';
  v_unknown_name constant uuid := '10000000-0000-4000-8000-000000000035';
  v_future_name constant uuid := '10000000-0000-4000-8000-000000000036';
  v_coordinate constant uuid := '10000000-0000-4000-8000-000000000070';
  v_coordinate_receipt constant uuid := '10000000-0000-4000-8000-000000000071';
  v_as_of constant timestamptz := '2026-08-25T12:00:00Z';
  v_digest text;
  v_read jsonb;
  v_fact_count integer;
  v_seal_count integer;
  v_case record;
  v_expected_vector constant text :=
    'sha256:73c90187e235a2e7262bf8de28ea4b61f69721cb8e60e8876092d3337d134bb7';
begin
  if ops.tour_public_value_safe('display.name','"   "'::jsonb)
     or ops.tour_public_value_safe('display.address',to_jsonb(repeat('A',361))) then
    raise exception 'required public display identity accepted blank or oversized text';
  end if;
  -- Direct approved projection DML is refused by the database-owned
  -- draft-only creation guard, even for the owner session.
  begin
    insert into ops.tour_public_projection (
      id, organization_tenant_id, tour_id, projection_version, route_version,
      as_of, facts_only, projection_digest, status
    ) values (
      v_vector_projection_id, v_tenant, v_tour_id, 1, 2, v_as_of, true,
      'sha256:' || repeat('f', 64), 'approved'
    );
    raise exception 'proof expected direct approved projection denial';
  exception when raise_exception then
    if sqlerrm <> 'projection creation requires draft status' then
      raise;
    end if;
  end;

  insert into ops.tour_rights_receipt (
    id, organization_tenant_id, provider, sku, policy_key, receipt_version,
    receipt_digest, terms_url, reviewed_at, reviewer, intended_use,
    allowed_field_classes, allowed_use_classes, effective_at, status
  ) values (
    v_rights_id, v_tenant, 'proof-provider', 'proof-sku', 'tour-public-v1', 1,
    'sha256:' || repeat('a', 64), 'https://example.invalid/terms',
    '2026-08-25T09:00:00Z', 'actor:proof', 'tour acceptance proof',
    '["display.name","display.address"]'::jsonb,
    '["source_intake","canonical_fact","client_public_display"]'::jsonb,
    '2026-08-25T09:00:00Z', 'active'
  );

  insert into ops.tour_property (id, organization_tenant_id, property_status)
  values
    (v_property_one, v_tenant, 'active'),
    (v_property_two, v_tenant, 'active');

  insert into ops.tour_source_evidence (
    id, organization_tenant_id, stable_locator, evidence_class, retrieved_at,
    retrieval_status, content_digest, rights_receipt_id, rights_provider,
    rights_policy_key, data_classification
  ) values (
    v_evidence_id, v_tenant, 'https://example.invalid/source',
    'direct_source', '2026-08-25T10:00:00Z', 'read',
    'sha256:' || repeat('b', 64), v_rights_id, 'proof-provider',
    'tour-public-v1', 'public'
  );
  insert into ops.tour_source_evidence (
    id, organization_tenant_id, stable_locator, evidence_class, retrieved_at,
    retrieval_status, content_digest, rights_receipt_id, rights_provider,
    rights_policy_key, data_classification
  ) values (
    v_partial_evidence_id, v_tenant, 'https://example.invalid/partial',
    'direct_source', '2026-08-25T10:00:00Z', 'partial',
    'sha256:' || repeat('8', 64), v_rights_id, 'proof-provider',
    'tour-public-v1', 'public'
  );

  -- The first two IDs/fields are the exact JS digest vector.  The additional
  -- two assertions provide the required name/address pair for both selected
  -- properties in the successful seal.
  insert into ops.tour_field_assertion (
    id, organization_tenant_id, property_id, field_key, value,
    source_evidence_id, rights_receipt_id, observed_at, effective_from,
    confidence, data_classification, review_state
  ) values
    (
      v_name_one, v_tenant, v_property_one, 'display.name',
      '"Proof Clinic"'::jsonb, v_evidence_id, v_rights_id,
      '2026-08-25T11:00:00Z', '2026-08-25T11:00:00Z',
      'high', 'public', 'reviewed'
    ),
    (
      v_address_two, v_tenant, v_property_two, 'display.address',
      '"2 Synthetic Way"'::jsonb, v_evidence_id, v_rights_id,
      '2026-08-25T11:00:00Z', '2026-08-25T11:00:00Z',
      'high', 'public', 'reviewed'
    ),
    (
      v_address_one, v_tenant, v_property_one, 'display.address',
      '"1 Synthetic Way"'::jsonb, v_evidence_id, v_rights_id,
      '2026-08-25T11:00:00Z', '2026-08-25T11:00:00Z',
      'high', 'public', 'reviewed'
    ),
    (
      v_name_two, v_tenant, v_property_two, 'display.name',
      '"Other Clinic"'::jsonb, v_evidence_id, v_rights_id,
      '2026-08-25T11:00:00Z', '2026-08-25T11:00:00Z',
      'high', 'public', 'reviewed'
    ),
    (
      v_partial_name, v_tenant, v_property_one, 'display.name',
      '"Partial source"'::jsonb, v_partial_evidence_id, v_rights_id,
      '2026-08-25T11:00:00Z', '2026-08-25T11:00:00Z',
      'high', 'public', 'reviewed'
    ),
    (
      v_unknown_name, v_tenant, v_property_one, 'display.name',
      '"Unknown confidence"'::jsonb, v_evidence_id, v_rights_id,
      '2026-08-25T11:00:00Z', '2026-08-25T11:00:00Z',
      'unknown', 'public', 'reviewed'
    ),
    (
      v_future_name, v_tenant, v_property_one, 'display.name',
      '"Observed later"'::jsonb, v_evidence_id, v_rights_id,
      '2026-08-25T13:00:00Z', '2026-08-25T11:00:00Z',
      'high', 'public', 'reviewed'
    );

  insert into ops.tour (
    id, organization_tenant_id, tour_name, tour_status, route_version,
    canonical_dataset_version
  ) values (
    v_tour_id, v_tenant, 'Slice 2 proof tour', 'draft', 2, 'proof-v1'
  );

  insert into ops.tour_property_membership (
    id, organization_tenant_id, tour_id, property_id, route_version,
    route_sequence, route_label, assertion_set_digest, selected_at
  ) values
    (
      '10000000-0000-4000-8000-000000000050', v_tenant, v_tour_id,
      v_property_one, 2, 1, 'A', 'sha256:' || repeat('c', 64),
      '2026-08-25T11:00:00Z'
    ),
    (
      '10000000-0000-4000-8000-000000000051', v_tenant, v_tour_id,
      v_property_two, 2, 2, 'B', 'sha256:' || repeat('d', 64),
      '2026-08-25T11:00:00Z'
    );

  -- A human-verified entrance candidate still cannot become client-visible
  -- when the governing receipt lacks the explicit coordinates field class.
  insert into ops.tour_property_coordinate_candidate (
    id,organization_tenant_id,property_id,coordinate_role,latitude,longitude,
    precision_class,source_evidence_id,rights_receipt_id,observed_at,review_state
  ) values (
    v_coordinate,v_tenant,v_property_one,'entrance',30.4156,-87.2169,
    'entrance',v_evidence_id,v_rights_id,'2026-08-25T11:00:00Z','reviewed'
  );
  insert into ops.tour_coordinate_entrance_verification_receipt (
    id,organization_tenant_id,property_id,coordinate_candidate_id,verifier_actor_id,
    verified_at,evidence_reference,native_navigation_proof,receipt_digest
  ) values (
    v_coordinate_receipt,v_tenant,v_property_one,v_coordinate,'actor:proof',
    '2026-08-25T11:05:00Z','native-nav:proof',
    '{"platform":"apple_maps","device_tested":true}'::jsonb,'sha256:'||repeat('9',64)
  );

  -- Build the exact cross-language vector in normalized rows.  This draft is
  -- deliberately not sealed: the successful projection below proves the
  -- tightened complete name/address requirement separately.
  insert into ops.tour_public_projection (
    id, organization_tenant_id, tour_id, projection_version, route_version,
    as_of, facts_only, projection_digest, status
  ) values (
    v_vector_projection_id, v_tenant, v_tour_id, 1, 2, v_as_of, true,
    'sha256:' || repeat('e', 64), 'draft'
  );

  insert into ops.tour_public_projection_fact (
    organization_tenant_id, projection_id, property_id, field_assertion_id,
    route_version, display_field_key
  ) values
    (v_tenant, v_vector_projection_id, v_property_one, v_name_one, 2, 'display.name'),
    (v_tenant, v_vector_projection_id, v_property_two, v_address_two, 2, 'display.address');

  v_digest := ops.tour_canonical_projection_digest(v_tenant, v_vector_projection_id);
  if v_digest <> v_expected_vector then
    raise exception 'cross-language digest vector mismatch: got %, expected %',
      v_digest, v_expected_vector;
  end if;

  -- The typed seal is the only authority path that may create approved
  -- derived rows.  It inserts four facts and the seal receipt atomically.
  insert into ops.tour_public_projection (
    id, organization_tenant_id, tour_id, projection_version, route_version,
    as_of, facts_only, projection_digest, status
  ) values (
    v_success_projection_id, v_tenant, v_tour_id, 2, 2, v_as_of, true,
    'sha256:' || repeat('e', 64), 'draft'
  );

  v_digest := ops.seal_tour_public_projection(
    v_tenant,
    v_success_projection_id,
    jsonb_build_array(
      jsonb_build_object(
        'property_id', v_property_one,
        'field_assertion_id', v_name_one,
        'display_field_key', 'display.name'
      ),
      jsonb_build_object(
        'property_id', v_property_one,
        'field_assertion_id', v_address_one,
        'display_field_key', 'display.address'
      ),
      jsonb_build_object(
        'property_id', v_property_two,
        'field_assertion_id', v_name_two,
        'display_field_key', 'display.name'
      ),
      jsonb_build_object(
        'property_id', v_property_two,
        'field_assertion_id', v_address_two,
        'display_field_key', 'display.address'
      )
    ),
    'actor:proof',
    'sha256:' || repeat('1', 64)
  );

  select count(*) into v_fact_count
    from ops.tour_public_projection_fact
   where organization_tenant_id = v_tenant
     and projection_id = v_success_projection_id;
  select count(*) into v_seal_count
    from ops.tour_public_projection_seal_receipt
   where organization_tenant_id = v_tenant
     and projection_id = v_success_projection_id;
  if v_fact_count <> 4 or v_seal_count <> 1 then
    raise exception 'complete seal row counts are wrong: facts %, seals %',
      v_fact_count, v_seal_count;
  end if;
  if exists (
    select 1 from ops.tour_public_projection_map_point
    where organization_tenant_id=v_tenant and projection_id=v_success_projection_id
  ) then
    raise exception 'coordinate without explicit field rights entered the public projection';
  end if;
  if (select status from ops.tour_public_projection
       where organization_tenant_id = v_tenant and id = v_success_projection_id)
       <> 'approved' then
    raise exception 'complete seal did not approve the projection';
  end if;
  if v_digest <> (
    select projection_digest from ops.tour_public_projection
     where organization_tenant_id = v_tenant and id = v_success_projection_id
  ) then
    raise exception 'seal return digest does not match projection digest';
  end if;
  if v_digest <> ops.tour_canonical_projection_digest(v_tenant, v_success_projection_id) then
    raise exception 'approved projection digest does not match database recomputation';
  end if;

  -- Safe read includes public values and provenance IDs/timestamps, but never
  -- source locator, provider, terms, or internal assertion keys.
  select ops.read_tour_public_projection(v_tenant, v_success_projection_id)
    into v_read;
  if v_read is null
     or jsonb_array_length(v_read->'facts') <> 4
     or not (v_read->'facts' @> '[{"display_field_key":"display.name","value":"Proof Clinic"}]'::jsonb)
     or not (v_read->'facts' @> '[{"display_field_key":"display.address","value":"1 Synthetic Way"}]'::jsonb)
     or not (v_read->'facts' @> '[{"source_evidence_id":"10000000-0000-4000-8000-000000000020"}]'::jsonb)
     or not (v_read->'facts' @> '[{"rights_receipt_id":"10000000-0000-4000-8000-000000000021"}]'::jsonb) then
    raise exception 'safe public read omitted public value or provenance';
  end if;
  if v_read::text ~ '(stable_locator|rights_provider|rights_policy_key|terms_url|internal_note)' then
    raise exception 'safe public read leaked internal or provider metadata';
  end if;

  -- Each unresolved factual input is refused by the database-owned fact
  -- trigger, and the typed seal remains atomic with no partial rows.
  for v_case in
    select * from (values
      ('10000000-0000-4000-8000-000000000063'::uuid, 4, v_partial_name),
      ('10000000-0000-4000-8000-000000000064'::uuid, 5, v_unknown_name),
      ('10000000-0000-4000-8000-000000000065'::uuid, 6, v_future_name)
    ) cases(projection_id, projection_version, assertion_id)
  loop
    insert into ops.tour_public_projection (
      id, organization_tenant_id, tour_id, projection_version, route_version,
      as_of, facts_only, projection_digest, status
    ) values (
      v_case.projection_id, v_tenant, v_tour_id, v_case.projection_version, 2,
      v_as_of, true, 'sha256:' || repeat('e', 64), 'draft'
    );
    begin
      perform ops.seal_tour_public_projection(
        v_tenant, v_case.projection_id,
        jsonb_build_array(
          jsonb_build_object('property_id',v_property_one,'field_assertion_id',v_case.assertion_id,'display_field_key','display.name'),
          jsonb_build_object('property_id',v_property_one,'field_assertion_id',v_address_one,'display_field_key','display.address'),
          jsonb_build_object('property_id',v_property_two,'field_assertion_id',v_name_two,'display_field_key','display.name'),
          jsonb_build_object('property_id',v_property_two,'field_assertion_id',v_address_two,'display_field_key','display.address')
        ), 'actor:proof', 'sha256:' || repeat('4',64));
      raise exception 'proof expected unresolved projection denial';
    exception when raise_exception then
      if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if;
    end;
    if exists (select 1 from ops.tour_public_projection_fact where organization_tenant_id=v_tenant and projection_id=v_case.projection_id)
       or exists (select 1 from ops.tour_public_projection_seal_receipt where organization_tenant_id=v_tenant and projection_id=v_case.projection_id) then
      raise exception 'unresolved projection denial left partial rows';
    end if;
  end loop;

  -- An immutable conflict quarantines both new seals and an already-approved
  -- read until a receipt selects the exact assertion being projected.
  insert into ops.tour_fact_conflict (
    id,organization_tenant_id,property_id,field_key,state,opened_at
  ) values (
    '10000000-0000-4000-8000-000000000072',v_tenant,v_property_one,
    'display.name','open','2026-08-25T11:30:00Z'
  );
  insert into ops.tour_fact_conflict_participant (
    organization_tenant_id,conflict_id,field_assertion_id,participant_role
  ) values (
    v_tenant,'10000000-0000-4000-8000-000000000072',v_name_one,'candidate'
  );
  if ops.read_tour_public_projection(v_tenant,v_success_projection_id) is not null then
    raise exception 'unresolved fact conflict left approved projection readable';
  end if;
  insert into ops.tour_public_projection (
    id,organization_tenant_id,tour_id,projection_version,route_version,
    as_of,facts_only,projection_digest,status
  ) values (
    v_conflict_projection_id,v_tenant,v_tour_id,7,2,v_as_of,true,
    'sha256:'||repeat('e',64),'draft'
  );
  begin
    perform ops.seal_tour_public_projection(
      v_tenant,v_conflict_projection_id,
      jsonb_build_array(
        jsonb_build_object('property_id',v_property_one,'field_assertion_id',v_name_one,'display_field_key','display.name'),
        jsonb_build_object('property_id',v_property_one,'field_assertion_id',v_address_one,'display_field_key','display.address'),
        jsonb_build_object('property_id',v_property_two,'field_assertion_id',v_name_two,'display_field_key','display.name'),
        jsonb_build_object('property_id',v_property_two,'field_assertion_id',v_address_two,'display_field_key','display.address')
      ),'actor:proof','sha256:'||repeat('5',64));
    raise exception 'proof expected unresolved conflict denial';
  exception when raise_exception then
    if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if;
  end;
  insert into ops.tour_conflict_resolution_receipt (
    organization_tenant_id,conflict_id,selected_field_assertion_id,rationale,
    evidence,resolver_actor_id,resolved_at,receipt_digest
  ) values (
    v_tenant,'10000000-0000-4000-8000-000000000072',v_name_one,
    'verified source selected','{}'::jsonb,'actor:proof',
    '2026-08-25T11:45:00Z','sha256:'||repeat('6',64)
  );
  if ops.read_tour_public_projection(v_tenant,v_success_projection_id) is null then
    raise exception 'exact conflict resolution did not restore approved projection read';
  end if;
  insert into ops.tour_public_projection (
    id,organization_tenant_id,tour_id,projection_version,route_version,
    as_of,facts_only,projection_digest,status
  ) values (
    v_resolved_projection_id,v_tenant,v_tour_id,8,2,v_as_of,true,
    'sha256:'||repeat('e',64),'draft'
  );
  perform ops.seal_tour_public_projection(
    v_tenant,v_resolved_projection_id,
    jsonb_build_array(
      jsonb_build_object('property_id',v_property_one,'field_assertion_id',v_name_one,'display_field_key','display.name'),
      jsonb_build_object('property_id',v_property_one,'field_assertion_id',v_address_one,'display_field_key','display.address'),
      jsonb_build_object('property_id',v_property_two,'field_assertion_id',v_name_two,'display_field_key','display.name'),
      jsonb_build_object('property_id',v_property_two,'field_assertion_id',v_address_two,'display_field_key','display.address')
    ),'actor:proof','sha256:'||repeat('7',64));

  -- A seal missing one required field is rejected before fact insertion.  The
  -- caught subtransaction proves no partial fact or seal rows survive.
  insert into ops.tour_public_projection (
    id, organization_tenant_id, tour_id, projection_version, route_version,
    as_of, facts_only, projection_digest, status
  ) values (
    v_incomplete_projection_id, v_tenant, v_tour_id, 3, 2, v_as_of, true,
    'sha256:' || repeat('e', 64), 'draft'
  );

  begin
    perform ops.seal_tour_public_projection(
      v_tenant,
      v_incomplete_projection_id,
      jsonb_build_array(
        jsonb_build_object(
          'property_id', v_property_one,
          'field_assertion_id', v_name_one,
          'display_field_key', 'display.name'
        ),
        jsonb_build_object(
          'property_id', v_property_one,
          'field_assertion_id', v_address_one,
          'display_field_key', 'display.address'
        ),
        jsonb_build_object(
          'property_id', v_property_two,
          'field_assertion_id', v_name_two,
          'display_field_key', 'display.name'
        )
      ),
      'actor:proof',
      'sha256:' || repeat('2', 64)
    );
    raise exception 'proof expected incomplete seal denial';
  exception when raise_exception then
    if sqlerrm <> 'projection seal requires one complete selected-property fact set' then
      raise;
    end if;
  end;

  select count(*) into v_fact_count
    from ops.tour_public_projection_fact
   where organization_tenant_id = v_tenant
     and projection_id = v_incomplete_projection_id;
  select count(*) into v_seal_count
    from ops.tour_public_projection_seal_receipt
   where organization_tenant_id = v_tenant
     and projection_id = v_incomplete_projection_id;
  if v_fact_count <> 0 or v_seal_count <> 0 then
    raise exception 'incomplete seal left partial rows: facts %, seals %',
      v_fact_count, v_seal_count;
  end if;
  if (select status from ops.tour_public_projection
       where organization_tenant_id = v_tenant and id = v_incomplete_projection_id)
       <> 'draft' then
    raise exception 'incomplete seal changed projection state';
  end if;

  -- A later immutable rights revocation makes the already-approved projection
  -- unreadable immediately; the read seam never relies only on seal-time rights.
  perform ops.revoke_tour_rights_receipt(
    v_tenant,
    v_rights_id,
    now(),
    'actor:proof',
    'sha256:' || repeat('3', 64)
  );
  if ops.read_tour_public_projection(v_tenant, v_success_projection_id) is not null then
    raise exception 'revoked rights left an approved public projection readable';
  end if;

  -- Application roles retain no direct table DML on the normalized Tour
  -- records.  The following exact function ACL assertions are the intended
  -- application seams installed by 0427.
  if has_table_privilege('carr_reader', 'ops.tour_public_projection', 'INSERT')
     or has_table_privilege('carr_writer', 'ops.tour_public_projection', 'INSERT')
     or has_table_privilege('carr_jobs', 'ops.tour_public_projection', 'INSERT')
     or has_table_privilege('carr_authority', 'ops.tour_public_projection', 'INSERT')
     or has_table_privilege('carr_authority', 'ops.tour_public_projection_fact', 'INSERT')
     or has_table_privilege('carr_authority', 'ops.tour_public_projection_seal_receipt', 'INSERT') then
    raise exception 'application role retains direct Tour projection DML';
  end if;

  if not has_function_privilege(
       'carr_authority',
       'ops.append_tour_rights_receipt(jsonb)', 'EXECUTE')
     or not has_function_privilege(
       'carr_authority',
       'ops.revoke_tour_rights_receipt(text,uuid,timestamptz,text,text)', 'EXECUTE')
     or not has_function_privilege(
       'carr_writer',
       'ops.append_tour_source_evidence(jsonb)', 'EXECUTE')
     or not has_function_privilege(
       'carr_authority',
       'ops.append_tour_field_assertion(jsonb)', 'EXECUTE')
     or not has_function_privilege(
       'carr_writer',
       'ops.create_tour_public_projection_draft(text,uuid,integer,integer,timestamptz)', 'EXECUTE')
     or not has_function_privilege(
       'carr_authority',
       'ops.seal_tour_public_projection(text,uuid,jsonb,text,text)', 'EXECUTE')
     or not has_function_privilege(
       'carr_reader',
       'ops.read_tour_public_projection(text,uuid)', 'EXECUTE')
     or not has_function_privilege(
       'carr_writer',
       'ops.read_tour_public_projection(text,uuid)', 'EXECUTE')
     or not has_function_privilege(
       'carr_jobs',
       'ops.read_tour_public_projection(text,uuid)', 'EXECUTE')
     or not has_function_privilege(
       'carr_authority',
       'ops.read_tour_public_projection(text,uuid)', 'EXECUTE') then
    raise exception 'intended Tour function grant is missing';
  end if;

  if has_function_privilege(
       'carr_reader',
       'ops.seal_tour_public_projection(text,uuid,jsonb,text,text)', 'EXECUTE')
     or has_function_privilege(
       'carr_writer',
       'ops.seal_tour_public_projection(text,uuid,jsonb,text,text)', 'EXECUTE')
     or has_function_privilege(
       'carr_jobs',
       'ops.seal_tour_public_projection(text,uuid,jsonb,text,text)', 'EXECUTE')
     or has_function_privilege(
       'carr_reader',
       'ops.append_tour_source_evidence(jsonb)', 'EXECUTE')
     or has_function_privilege(
       'carr_jobs',
       'ops.append_tour_field_assertion(jsonb)', 'EXECUTE')
     or has_function_privilege(
       'carr_writer',
       'ops.append_tour_field_assertion(jsonb)', 'EXECUTE') then
    raise exception 'Tour function ACL is wider than intended';
  end if;
end
$proof$;

rollback;

-- A public read evaluates revocations and conflicts at the start of each read
-- statement, not at the start of a long-running transaction.  The conflict is
-- deliberately opened with clock_timestamp() after BEGIN; transaction-scoped
-- now() would predate it and incorrectly leave the packet readable.
begin;

insert into ops.tour_rights_receipt (
  id,organization_tenant_id,provider,sku,policy_key,receipt_version,
  receipt_digest,terms_url,reviewed_at,reviewer,intended_use,
  allowed_field_classes,allowed_use_classes,effective_at,status
) values (
  '20000000-0000-4000-8000-000000000021','tour-long-transaction',
  'proof-provider','proof-sku','tour-public-v1',1,
  'sha256:'||repeat('a',64),'https://example.invalid/terms',
  transaction_timestamp()-interval '2 hours','actor:proof','tour acceptance proof',
  '["display.name","display.address"]'::jsonb,
  '["source_intake","canonical_fact","client_public_display"]'::jsonb,
  transaction_timestamp()-interval '2 hours','active'
);
insert into ops.tour_property (id,organization_tenant_id,property_status)
values ('20000000-0000-4000-8000-000000000010','tour-long-transaction','active');
insert into ops.tour_source_evidence (
  id,organization_tenant_id,stable_locator,evidence_class,retrieved_at,
  retrieval_status,content_digest,rights_receipt_id,rights_provider,
  rights_policy_key,data_classification
) values (
  '20000000-0000-4000-8000-000000000020','tour-long-transaction',
  'https://example.invalid/source','direct_source',
  transaction_timestamp()-interval '1 hour','read','sha256:'||repeat('b',64),
  '20000000-0000-4000-8000-000000000021','proof-provider',
  'tour-public-v1','public'
);
insert into ops.tour_field_assertion (
  id,organization_tenant_id,property_id,field_key,value,source_evidence_id,
  rights_receipt_id,observed_at,effective_from,confidence,
  data_classification,review_state
) values
  ('20000000-0000-4000-8000-000000000030','tour-long-transaction',
   '20000000-0000-4000-8000-000000000010','display.name','"Long Transaction Clinic"'::jsonb,
   '20000000-0000-4000-8000-000000000020','20000000-0000-4000-8000-000000000021',
   transaction_timestamp()-interval '30 minutes',transaction_timestamp()-interval '30 minutes',
   'high','public','reviewed'),
  ('20000000-0000-4000-8000-000000000031','tour-long-transaction',
   '20000000-0000-4000-8000-000000000010','display.address','"2 Synthetic Way"'::jsonb,
   '20000000-0000-4000-8000-000000000020','20000000-0000-4000-8000-000000000021',
   transaction_timestamp()-interval '30 minutes',transaction_timestamp()-interval '30 minutes',
   'high','public','reviewed');
insert into ops.tour (
  id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version
) values (
  '20000000-0000-4000-8000-000000000040','tour-long-transaction',
  'Long transaction proof','draft',1,'proof'
);
insert into ops.tour_property_membership (
  id,organization_tenant_id,tour_id,property_id,route_version,route_sequence,
  route_label,assertion_set_digest,selected_at
) values (
  '20000000-0000-4000-8000-000000000050','tour-long-transaction',
  '20000000-0000-4000-8000-000000000040','20000000-0000-4000-8000-000000000010',
  1,1,'A','sha256:'||repeat('c',64),transaction_timestamp()-interval '10 minutes'
);
insert into ops.tour_public_projection (
  id,organization_tenant_id,tour_id,projection_version,route_version,as_of,
  facts_only,projection_digest,status
) values (
  '20000000-0000-4000-8000-000000000060','tour-long-transaction',
  '20000000-0000-4000-8000-000000000040',1,1,transaction_timestamp(),true,
  'sha256:'||repeat('d',64),'draft'
);
select ops.seal_tour_public_projection(
  'tour-long-transaction','20000000-0000-4000-8000-000000000060',
  jsonb_build_array(
    jsonb_build_object(
      'property_id','20000000-0000-4000-8000-000000000010',
      'field_assertion_id','20000000-0000-4000-8000-000000000030',
      'display_field_key','display.name'),
    jsonb_build_object(
      'property_id','20000000-0000-4000-8000-000000000010',
      'field_assertion_id','20000000-0000-4000-8000-000000000031',
      'display_field_key','display.address')
  ),'actor:proof','sha256:'||repeat('e',64)
);

select pg_sleep(0.02);
insert into ops.tour_fact_conflict (
  id,organization_tenant_id,property_id,field_key,state,opened_at
) values (
  '20000000-0000-4000-8000-000000000070','tour-long-transaction',
  '20000000-0000-4000-8000-000000000010','display.name','open',clock_timestamp()
);
insert into ops.tour_fact_conflict_participant (
  organization_tenant_id,conflict_id,field_assertion_id,participant_role
) values (
  'tour-long-transaction','20000000-0000-4000-8000-000000000070',
  '20000000-0000-4000-8000-000000000030','candidate'
);
do $long_transaction_conflict$
begin
  if ops.read_tour_public_projection(
       'tour-long-transaction','20000000-0000-4000-8000-000000000060') is not null then
    raise exception 'statement-time conflict left long-transaction projection readable';
  end if;
end
$long_transaction_conflict$;

select pg_sleep(0.02);
insert into ops.tour_conflict_resolution_receipt (
  id,organization_tenant_id,conflict_id,selected_field_assertion_id,rationale,
  evidence,resolver_actor_id,resolved_at,receipt_digest
) values (
  '20000000-0000-4000-8000-000000000071','tour-long-transaction',
  '20000000-0000-4000-8000-000000000070','20000000-0000-4000-8000-000000000030',
  'verified source selected','{}'::jsonb,'actor:proof',clock_timestamp(),
  'sha256:'||repeat('f',64)
);
do $long_transaction_resolution$
begin
  if ops.read_tour_public_projection(
       'tour-long-transaction','20000000-0000-4000-8000-000000000060') is null then
    raise exception 'statement-time resolution did not restore long-transaction projection read';
  end if;
end
$long_transaction_resolution$;

rollback;
