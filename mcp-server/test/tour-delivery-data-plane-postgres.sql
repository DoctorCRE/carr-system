\set ON_ERROR_STOP on
-- Disposable least-privilege proof for 0430. All rows are rolled back.
begin;

insert into ops.tour_property(id,organization_tenant_id,property_status,created_at) values
('a1000000-0000-4000-8000-000000000001','tour-delivery-proof','active',now()-interval '30 days'),
('a1000000-0000-4000-8000-000000000002','tour-delivery-proof','active',now()-interval '30 days');
insert into ops.tour_rights_receipt(id,organization_tenant_id,provider,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status)
values('a5000000-0000-4000-8000-000000000001','tour-delivery-proof','search-proof','search-policy',1,'sha256:'||repeat('1',64),'https://example.invalid/search',now()-interval '1 year','proof','search proof','["*"]','["source_intake","canonical_fact","client_public_display"]',now()-interval '1 year','active');
insert into ops.tour_source_evidence(id,organization_tenant_id,stable_locator,evidence_class,retrieved_at,retrieval_status,content_digest,rights_receipt_id,data_classification,rights_provider,rights_policy_key)
values('a6000000-0000-4000-8000-000000000001','tour-delivery-proof','proof:search','direct_source',now()-interval '31 days','read','sha256:'||repeat('2',64),'a5000000-0000-4000-8000-000000000001','public','search-proof','search-policy');
insert into ops.tour_jurisdiction_dataset(id,organization_tenant_id,jurisdiction_type,state_code,county_name,authoritative_source_locator,dataset_version,dataset_digest,source_evidence_id,rights_receipt_id,as_of,review_state,created_at)
values('a7000000-0000-4000-8000-000000000001','tour-delivery-proof','county','FL','Escambia','proof:county','v1','sha256:'||repeat('3',64),'a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '25 days','reviewed',now()-interval '25 days');
insert into ops.tour_property_jurisdiction_assertion(id,organization_tenant_id,property_id,jurisdiction_dataset_id,jurisdiction_name,assertion_method,source_evidence_id,rights_receipt_id,as_of,review_state,created_at)
values('a8000000-0000-4000-8000-000000000001','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','a7000000-0000-4000-8000-000000000001','Escambia','manual_review','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '25 days','reviewed',now()-interval '25 days');
insert into ops.tour_field_assertion(id,organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,effective_to,confidence,data_classification,review_state,created_at) values
('a9000000-0000-4000-8000-000000000001','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','display.name','"Current Medical Plaza"','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('a9000000-0000-4000-8000-000000000002','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','display.name','"Future Medical Plaza"','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now(),now()+interval '1 day',null,'high','public','reviewed',now()),
('a9000000-0000-4000-8000-000000000003','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','display.address','"100 Current Way"','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '19 days',now()-interval '19 days',null,'high','public','reviewed',now()-interval '19 days'),
('a9000000-0000-4000-8000-000000000004','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','property_type','"medical_office"','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '18 days',now()-interval '18 days',null,'high','public','reviewed',now()-interval '18 days'),
('a9000000-0000-4000-8000-000000000005','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','size','{"value":4200,"unit":"SF"}','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '17 days',now()-interval '17 days',null,'high','public','reviewed',now()-interval '17 days'),
('a9000000-0000-4000-8000-000000000006','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','asking_economics','{"value":24,"currency":"USD","period":"NNN"}','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '16 days',now()-interval '16 days',null,'high','public','reviewed',now()-interval '16 days'),
('a9000000-0000-4000-8000-000000000007','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','availability','"available"','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '1 day',now()-interval '10 days',null,'high','public','reviewed',now()-interval '1 day'),
('a9000000-0000-4000-8000-000000000008','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','availability','"withdrawn"','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '3 days',now()-interval '5 days',now()-interval '2 days','high','public','reviewed',now()-interval '2 days'),
('a9000000-0000-4000-8000-000000000009','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','photos','[{"asset_ref":"asset:public:abcdefghijklmnop"}]','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '15 days',now()-interval '15 days',null,'high','public','reviewed',now()-interval '15 days'),
('a9000000-0000-4000-8000-000000000010','tour-delivery-proof','a1000000-0000-4000-8000-000000000002','display.name','"Second Medical Plaza"','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '14 days',now()-interval '14 days',null,'high','public','reviewed',now()-interval '14 days'),
('a9000000-0000-4000-8000-000000000011','tour-delivery-proof','a1000000-0000-4000-8000-000000000002','display.address','"200 Current Way"','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',now()-interval '13 days',now()-interval '13 days',null,'high','public','reviewed',now()-interval '13 days');
insert into ops.tour(id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version,subject_type,subject_id,subject_bound_at)
values('a2000000-0000-4000-8000-000000000001','tour-delivery-proof','Delivery proof','draft',1,'proof-v1','work','proof',now());
insert into ops.tour_route_version(id,organization_tenant_id,tour_id,route_version,start_point,end_point,routing_source,routing_request,created_by_actor_id)
values('a3000000-0000-4000-8000-000000000001','tour-delivery-proof','a2000000-0000-4000-8000-000000000001',1,'{}','{}','manual','{}','tour-delivery-proof');
insert into ops.tour_route_stop(id,organization_tenant_id,route_version_id,property_id,route_sequence,route_label,stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,buffer_minutes,access_coordinate_status,assertion_set_digest,created_by_actor_id)
values
('a4000000-0000-4000-8000-000000000001','tour-delivery-proof','a3000000-0000-4000-8000-000000000001','a1000000-0000-4000-8000-000000000001',1,'A','active','2026-08-28 14:00:00+00','2026-08-28 14:30:00+00',true,30,10,'approved','sha256:'||repeat('d',64),'tour-delivery-proof'),
('a4000000-0000-4000-8000-000000000002','tour-delivery-proof','a3000000-0000-4000-8000-000000000001','a1000000-0000-4000-8000-000000000002',2,'B','active','2026-08-28 15:00:00+00','2026-08-28 15:30:00+00',true,30,10,'approved','sha256:'||repeat('e',64),'tour-delivery-proof');
insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest)
values('tour-delivery-proof','a2000000-0000-4000-8000-000000000001','a3000000-0000-4000-8000-000000000001',0,'tour-delivery-proof','sha256:'||repeat('c',64));
insert into ops.tour_property_membership(
  id,organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest,selected_at)
values
('ad000000-0000-4000-8000-000000000001','tour-delivery-proof','a2000000-0000-4000-8000-000000000001','a1000000-0000-4000-8000-000000000001',1,1,'A','sha256:'||repeat('d',64),now()-interval '1 hour'),
('ad000000-0000-4000-8000-000000000002','tour-delivery-proof','a2000000-0000-4000-8000-000000000001','a1000000-0000-4000-8000-000000000002',1,2,'B','sha256:'||repeat('e',64),now()-interval '1 hour');

insert into ops.tour_property_coordinate_candidate(
  id,organization_tenant_id,property_id,coordinate_role,latitude,longitude,precision_class,
  source_evidence_id,rights_receipt_id,provider,observed_at,review_state)
values
('aa000000-0000-4000-8000-000000000001','tour-delivery-proof','a1000000-0000-4000-8000-000000000001','entrance',30.421000,-87.216000,'entrance','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',null,now()-interval '2 days','reviewed'),
('aa000000-0000-4000-8000-000000000002','tour-delivery-proof','a1000000-0000-4000-8000-000000000002','parking_access',30.422000,-87.217000,'entrance','a6000000-0000-4000-8000-000000000001','a5000000-0000-4000-8000-000000000001',null,now()-interval '2 days','reviewed');

set local carr.verified_human_actor_slug='joe';
do $map_promotion$
declare v_actor_id text; v_digest text; v_share uuid; v_packet jsonb; v_render_packet jsonb;
begin
  select id::text into strict v_actor_id from public.actor where slug='joe' and active and kind='human';
  perform ops.append_tour_entrance_verification_receipt(jsonb_build_object(
    'organization_tenant_id','tour-delivery-proof','property_id','a1000000-0000-4000-8000-000000000001',
    'coordinate_candidate_id','aa000000-0000-4000-8000-000000000001','verifier_actor_id',v_actor_id,
    'verified_at',to_char(now()-interval '1 day','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'evidence_reference','proof:native-navigation:one','native_navigation_proof',jsonb_build_object('status','passed'),
    'receipt_digest','sha256:'||repeat('4',64)));
  perform ops.append_tour_entrance_verification_receipt(jsonb_build_object(
    'organization_tenant_id','tour-delivery-proof','property_id','a1000000-0000-4000-8000-000000000002',
    'coordinate_candidate_id','aa000000-0000-4000-8000-000000000002','verifier_actor_id',v_actor_id,
    'verified_at',to_char(now()-interval '1 day','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'evidence_reference','proof:native-navigation:two','native_navigation_proof',jsonb_build_object('status','passed'),
    'receipt_digest','sha256:'||repeat('5',64)));
  insert into ops.tour_public_projection(
    id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
  values('ac000000-0000-4000-8000-000000000001','tour-delivery-proof','a2000000-0000-4000-8000-000000000001',1,1,now(),true,'sha256:'||repeat('0',64),'draft');
  v_digest:=ops.seal_tour_public_projection(
    'tour-delivery-proof','ac000000-0000-4000-8000-000000000001',
    '[{"property_id":"a1000000-0000-4000-8000-000000000001","field_assertion_id":"a9000000-0000-4000-8000-000000000001","display_field_key":"display.name"},{"property_id":"a1000000-0000-4000-8000-000000000001","field_assertion_id":"a9000000-0000-4000-8000-000000000003","display_field_key":"display.address"},{"property_id":"a1000000-0000-4000-8000-000000000002","field_assertion_id":"a9000000-0000-4000-8000-000000000010","display_field_key":"display.name"},{"property_id":"a1000000-0000-4000-8000-000000000002","field_assertion_id":"a9000000-0000-4000-8000-000000000011","display_field_key":"display.address"}]',
    v_actor_id,'sha256:'||repeat('6',64));
  if v_digest !~ '^sha256:[a-f0-9]{64}$' then raise exception 'map projection did not seal'; end if;
  begin
    perform ops.issue_tour_share_grant('tour-delivery-proof','ac000000-0000-4000-8000-000000000001','sha256:'||repeat('7',64),'["view_map"]',now()+interval '1 day','sha256:'||repeat('8',64),v_actor_id);
    raise exception 'expected promotion receipt refusal';
  exception when raise_exception then
    if sqlerrm<>'tour map share requires current rights, sealed entrance coordinates, and an approved promotion receipt' then raise; end if;
  end;
  perform ops.record_tour_map_promotion_receipt('tour-delivery-proof','ac000000-0000-4000-8000-000000000001',jsonb_build_object(
    'decision','approved','reviewed_at',to_char(now(),'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'decision_reason','All required map promotion checks passed.',
    'brief_version','tour-map-brief.v1','canonical_dataset_version','proof-v1','selected_prototype_id','carr-map-tour-v1',
    'component_registry_version','maplibre-6.1.0','route_version',1,
    'provider_rights_receipt_ids',jsonb_build_array('a5000000-0000-4000-8000-000000000001'),
    'mobile_test_evidence',jsonb_build_object('status','passed'),'native_navigation_test_evidence',jsonb_build_object('status','passed'),
    'offline_test_evidence',jsonb_build_object('status','passed'),'required_checks',jsonb_build_object(
      'canonical_address_and_coordinate_review',true,'claims_and_layers_have_source_as_of_rights_and_review_state',true,
      'deterministic_rebuild_from_canonical_record',true,'exact_native_navigation_handoff',true,
      'locked_appointments_dwell_and_buffers_preserved',true,'map_list_route_offline_order_parity',true,
      'no_unresolved_route_critical_unknown_or_conflict',true,'optional_context_layers_progressively_disclosed',true,
      'ordered_offline_itinerary_verified',true,'phone_and_ipad_interaction_test',true,
      'provider_terms_attribution_expiry_and_cost_gate_passed',true),'receipt_digest','sha256:'||repeat('9',64)),v_actor_id);
  if not ops.tour_public_map_projection_ready('tour-delivery-proof','ac000000-0000-4000-8000-000000000001') then
    raise exception 'approved map promotion did not open readiness';
  end if;
  v_share:=ops.issue_tour_share_grant('tour-delivery-proof','ac000000-0000-4000-8000-000000000001','sha256:'||repeat('a',64),'["view_map","view_packet"]',now()+interval '1 day','sha256:'||repeat('b',64),v_actor_id);
  perform ops.exchange_tour_share_token('sha256:'||repeat('a',64),'sha256:'||repeat('2',64),now()+interval '1 day','sha256:'||repeat('3',64));
  v_packet:=ops.read_tour_share_packet('sha256:'||repeat('2',64));
  if v_packet is null or v_packet ? 'tour_name' or v_packet::text like '%Delivery proof%' then
    raise exception 'internal tour name crossed the sealed public share boundary';
  end if;
  -- migration 0586: the packet-level caveat is never the removed hard-coded
  -- "Facts only; verify current availability and economics." line -- it must
  -- be present as an explicit null (kept key, dropped default), not the
  -- literal, and not silently omitted either.
  if not (v_packet ? 'caveat') or v_packet->'caveat' is distinct from 'null'::jsonb then
    raise exception 'read_tour_share_packet must carry caveat as an explicit null, not a hard-coded default';
  end if;
  if v_packet::text like '%Facts only%' then
    raise exception 'read_tour_share_packet leaked the removed hard-coded caveat line';
  end if;
  v_render_packet:=ops.read_tour_packet_for_render('tour-delivery-proof','ac000000-0000-4000-8000-000000000001',v_actor_id);
  if v_render_packet is null or not (v_render_packet->'packet' ? 'caveat')
     or v_render_packet->'packet'->'caveat' is distinct from 'null'::jsonb then
    raise exception 'read_tour_packet_for_render must carry caveat as an explicit null, not a hard-coded default';
  end if;
  if v_render_packet::text like '%Facts only%' then
    raise exception 'read_tour_packet_for_render leaked the removed hard-coded caveat line';
  end if;
  perform ops.record_tour_map_promotion_receipt('tour-delivery-proof','ac000000-0000-4000-8000-000000000001',jsonb_build_object(
    'decision','rejected','reviewed_at',to_char(now()-interval '1 year','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'decision_reason','Mobile, native-navigation, and offline evidence failed.',
    'brief_version','tour-map-brief.v1','canonical_dataset_version','proof-v1','selected_prototype_id','carr-map-tour-v1',
    'component_registry_version','maplibre-6.1.0','route_version',1,
    'provider_rights_receipt_ids',jsonb_build_array('a5000000-0000-4000-8000-000000000001'),
    'mobile_test_evidence',jsonb_build_object('status','failed'),'native_navigation_test_evidence',jsonb_build_object('status','failed'),
    'offline_test_evidence',jsonb_build_object('status','failed'),'required_checks',jsonb_build_object(
      'canonical_address_and_coordinate_review',true,'claims_and_layers_have_source_as_of_rights_and_review_state',true,
      'deterministic_rebuild_from_canonical_record',true,'exact_native_navigation_handoff',false,
      'locked_appointments_dwell_and_buffers_preserved',true,'map_list_route_offline_order_parity',true,
      'no_unresolved_route_critical_unknown_or_conflict',true,'optional_context_layers_progressively_disclosed',true,
      'ordered_offline_itinerary_verified',true,'phone_and_ipad_interaction_test',true,
      'provider_terms_attribution_expiry_and_cost_gate_passed',true),'receipt_digest','sha256:'||repeat('c',64)),v_actor_id);
  if ops.tour_public_map_projection_ready('tour-delivery-proof','ac000000-0000-4000-8000-000000000001') then
    raise exception 'later appended backdated rejection did not close readiness';
  end if;
  begin
    perform ops.rotate_tour_share_grant('tour-delivery-proof',v_share,'ac000000-0000-4000-8000-000000000001','sha256:'||repeat('d',64),'["view_map"]',now()+interval '1 day','sha256:'||repeat('e',64),v_actor_id);
    raise exception 'expected rejected promotion rotation refusal';
  exception when raise_exception then
    if sqlerrm<>'tour map share requires current rights, sealed entrance coordinates, and an approved promotion receipt' then raise; end if;
  end;
end $map_promotion$;

set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-delivery-proof';
do $writer$
declare v_version uuid; v_cart jsonb; v_search jsonb; v_projection_meta jsonb;
begin
  begin
    perform ops.prepare_tour_route_version(
      'tour-delivery-proof','a2000000-0000-4000-8000-000000000001',
      'a3000000-0000-4000-8000-000000000001',1,
      '["a4000000-0000-4000-8000-000000000002","a4000000-0000-4000-8000-000000000001"]');
    raise exception 'expected locked appointment order refusal';
  exception when raise_exception then
    if sqlerrm<>'tour route preparation violates locked appointment order' then raise; end if;
  end;
  v_version:=ops.append_tour_selection_cart_version(
    'tour-delivery-proof','a2000000-0000-4000-8000-000000000001',null,
    '["a1000000-0000-4000-8000-000000000001","a1000000-0000-4000-8000-000000000002"]',0,
    'sha256:'||repeat('a',64));
  if v_version is null then raise exception 'selection cart version was not created'; end if;
  v_cart:=ops.read_tour_selection_cart('tour-delivery-proof','a2000000-0000-4000-8000-000000000001','tour-delivery-proof');
  if v_cart->>'selection_version'<>'1' or jsonb_array_length(v_cart->'property_ids')<>2 then raise exception 'selection cart projection is incomplete'; end if;
  begin
    perform ops.append_tour_selection_cart_version(
      'tour-delivery-proof','a2000000-0000-4000-8000-000000000001',null,'[]',0,'sha256:'||repeat('b',64));
    raise exception 'expected stale cart refusal';
  exception when raise_exception then
    if sqlerrm<>'tour selection refuses stale version' then raise; end if;
  end;
  v_search:=ops.search_tour_properties('tour-delivery-proof','tour-delivery-proof','{"query":null,"counties":[],"property_types":[],"min_square_feet":null,"max_square_feet":null,"availability":[],"entrance_verified":null,"public_projection_ready":null,"photos_available":null,"sort":"updated_desc","cursor":null,"limit":25}');
  if jsonb_typeof(v_search->'items')<>'array' then raise exception 'search did not return a bounded item array'; end if;
  if v_search#>>'{items,0,name}'<>'Current Medical Plaza' or v_search#>>'{items,0,availability}'<>'available' then raise exception 'search selected a future-effective or expired assertion'; end if;
  if (v_search#>>'{items,0,updated_at}')::timestamptz not between now()-interval '26 hours' and now()-interval '22 hours' then raise exception 'search update timestamp omitted a displayed fact'; end if;
  if (v_search#>>'{items,0,fact_as_of}')::timestamptz not between now()-interval '26 hours' and now()-interval '22 hours' then raise exception 'search factual as-of timestamp omitted a displayed fact'; end if;
  v_projection_meta:=ops.read_tour_projection_creation_metadata('tour-delivery-proof','a2000000-0000-4000-8000-000000000001','a3000000-0000-4000-8000-000000000001','tour-delivery-proof');
  if v_projection_meta->>'route_version'<>'1' or v_projection_meta->>'projection_version'<>'2' then raise exception 'projection creation metadata is incomplete'; end if;
end $writer$;

reset session authorization;
do $owner$
begin
  begin
    update ops.tour_selection_cart_version set selection_version=2 where organization_tenant_id='tour-delivery-proof';
    raise exception 'expected append-only refusal';
  exception when raise_exception then
    if sqlerrm<>'tour_selection_cart_version is append-only' then raise; end if;
  end;
  if has_table_privilege('carr_writer','ops.tour_selection_cart_version','INSERT')
     or has_table_privilege('carr_authority','ops.tour_share_session','INSERT')
     or has_table_privilege('carr_reader','ops.tour_pdf_render_job','SELECT') then
    raise exception 'raw Tour delivery table privileges leaked';
  end if;
  if not has_function_privilege('carr_writer','ops.append_tour_selection_cart_version(text,uuid,uuid,jsonb,integer,text)','EXECUTE')
     or not has_function_privilege('carr_authority','ops.issue_tour_share_grant(text,uuid,text,jsonb,timestamp with time zone,text,text)','EXECUTE')
     or not has_function_privilege('carr_writer','ops.read_tour_projection_creation_metadata(text,uuid,uuid,text)','EXECUTE')
     or not has_function_privilege('carr_writer','ops.read_tour_projection_seal_candidates(text,uuid,text)','EXECUTE')
     or not has_function_privilege('carr_authority','ops.record_tour_pdf_render_result(text,uuid,text,text,text,text,integer,integer,integer,text,text)','EXECUTE')
     or not has_function_privilege('carr_writer','ops.exchange_tour_share_token(text,text,timestamp with time zone,text)','EXECUTE')
     or has_function_privilege('carr_reader','ops.exchange_tour_share_token(text,text,timestamp with time zone,text)','EXECUTE') then
    raise exception 'Tour delivery function grants are incorrect';
  end if;
end $owner$;

rollback;
