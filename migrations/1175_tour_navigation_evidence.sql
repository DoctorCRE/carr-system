-- Read-only extension. Preserve the installed authorization wrapper and grants.
-- rollback: forward function replacement. lock-review: no table changes.
create function ops.tour_route_navigation_evidence(p_tenant text,p_route_id uuid)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare
 v ops.tour_route_version%rowtype; t ops.tour%rowtype;
 p ops.tour_public_projection%rowtype; r ops.tour_map_promotion_receipt%rowtype;
 reason text; points jsonb;
begin
 select * into v from ops.tour_route_version where organization_tenant_id=p_tenant and id=p_route_id;
 select * into t from ops.tour where organization_tenant_id=p_tenant and id=v.tour_id;
 select * into p from ops.tour_public_projection where organization_tenant_id=p_tenant and tour_id=v.tour_id
   and status in ('approved','published') order by projection_version desc,id desc limit 1;
 select * into r from ops.tour_map_promotion_receipt where organization_tenant_id=p_tenant and projection_id=p.id
   order by receipt_sequence desc limit 1;
 reason := case
   when v.id is null then 'route_unknown'
   when not exists(select 1 from ops.tour_route_version_acceptance a where a.organization_tenant_id=p_tenant and a.route_version_id=v.id) then 'route_not_accepted'
   when v.route_version is distinct from t.route_version then 'route_replaced'
   when p.id is null then 'projection_missing'
   when p.route_version is distinct from v.route_version then 'projection_route_mismatch'
   when not coalesce(ops.tour_public_map_projection_evidence_ready(p_tenant,p.id),false) then 'entrance_or_rights_evidence_unavailable'
   when r.id is null then 'promotion_missing'
   when r.decision <> 'approved' then 'promotion_rejected'
   when r.route_version is distinct from v.route_version or r.canonical_dataset_version is distinct from t.canonical_dataset_version then 'promotion_stale'
   else null end;
 if reason is null then
   select jsonb_agg(jsonb_build_object(
     'route_stop_id',s.id,'property_id',s.property_id,'route_sequence',s.route_sequence,'route_label',s.route_label,
     'position',jsonb_build_object('latitude',c.latitude,'longitude',c.longitude),
     'source',jsonb_build_object('coordinate_candidate_id',c.id,'coordinate_role',c.coordinate_role,
       'precision_class',c.precision_class,'observed_at',c.observed_at,'review_state',c.review_state,
       'source_evidence_id',e.id,'stable_locator',e.stable_locator,'content_digest',e.content_digest,
       'retrieved_at',e.retrieved_at,'retrieval_status',e.retrieval_status,'rights_receipt_id',c.rights_receipt_id,
       'entrance_verification_receipt_id',er.id,'receipt_digest',er.receipt_digest,
       'verifier_actor_id',er.verifier_actor_id,'verified_at',er.verified_at,
       'evidence_reference',er.evidence_reference,'native_navigation_proof',er.native_navigation_proof)
   ) order by s.route_sequence,s.id) into points
   from ops.tour_route_stop s
   join ops.tour_public_projection_map_point mp on mp.organization_tenant_id=s.organization_tenant_id and mp.projection_id=p.id and mp.property_id=s.property_id and mp.route_version=v.route_version
   join ops.tour_property_coordinate_candidate c on c.organization_tenant_id=mp.organization_tenant_id and c.id=mp.coordinate_candidate_id and c.property_id=s.property_id
   join ops.tour_coordinate_entrance_verification_receipt er on er.organization_tenant_id=mp.organization_tenant_id and er.id=mp.entrance_verification_receipt_id and er.coordinate_candidate_id=c.id and er.property_id=s.property_id
   join ops.tour_source_evidence e on e.organization_tenant_id=c.organization_tenant_id and e.id=c.source_evidence_id
   where s.organization_tenant_id=p_tenant and s.route_version_id=v.id and s.stop_state='active'
     and s.access_coordinate_status='approved' and e.retrieval_status='read' and er.verified_at<=now()
     and er.native_navigation_proof->>'status'='passed';
   if points is null or jsonb_array_length(points) <> (select count(*) from ops.tour_route_stop s where s.organization_tenant_id=p_tenant and s.route_version_id=v.id and s.stop_state='active') then
     reason := 'stop_entrance_evidence_unavailable';
   end if;
 end if;
 return jsonb_build_object('contract_version','tour-navigation-evidence.v1','status',case when reason is null then 'ready' else 'unavailable' end,
   'reason',reason,'evidence',case when reason is not null then null else jsonb_build_object(
     'route_version_id',v.id,'route_version',v.route_version,'projection_id',p.id,'projection_version',p.projection_version,
     'projection_digest',p.projection_digest,'as_of',p.as_of,
     'promotion',jsonb_build_object('receipt_id',r.id,'receipt_digest',r.receipt_digest,'reviewed_at',r.reviewed_at,
       'reviewer_actor_id',r.reviewer_actor_id,'route_version',r.route_version,'canonical_dataset_version',r.canonical_dataset_version,
       'component_registry_version',r.component_registry_version,'selected_prototype_id',r.selected_prototype_id,
       'brief_version',r.brief_version,'provider_rights_receipt_ids',r.provider_rights_receipt_ids,
       'mobile_test_evidence',r.mobile_test_evidence,'native_navigation_test_evidence',r.native_navigation_test_evidence,
       'offline_test_evidence',r.offline_test_evidence,'required_checks',r.required_checks),
     'stops',points) end);
end $$;
revoke all on function ops.tour_route_navigation_evidence(text,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

do $extend_detail$
declare definition text; extended text;
begin
 select pg_get_functiondef('ops.read_tour_internal_detail(text,uuid,text)'::regprocedure) into definition;
 extended := replace(definition,'''routing_source'',v.routing_source,',
   '''navigation'',ops.tour_route_navigation_evidence(v.organization_tenant_id,v.id),''routing_source'',v.routing_source,');
 if extended=definition then raise exception 'Tour detail extension anchor missing'; end if;
 execute extended;
end $extend_detail$;
