\set ON_ERROR_STOP on
begin;
create temporary table deal_tour_cases(kind text,deal_id uuid,tour_id uuid,client_party uuid,projection_id uuid,render_id uuid,route_id uuid);
grant all on deal_tour_cases to carr_writer;
create function pg_temp.authorize_deal_tours(who text) returns void language sql security definer as $$
 select set_config('carr.tour_deal_access',jsonb_build_object('slug',who,'id',(select id::text from public.actor where slug=who),'deals',coalesce((select jsonb_object_agg(id::text,jsonb_build_object('owner',owner,'version',version)) from public.deal where owner=who),'{}'))::text,true)::text::void
$$;
grant execute on function pg_temp.authorize_deal_tours(text) to carr_writer;
do $fixtures$
declare a uuid; p uuid; c uuid; d uuid; kind text;
begin
 insert into public.actor(slug,kind,display_name) values('joe','human','Synthetic Owner'),('dell','human','Synthetic Other') on conflict(slug) do update set active=true;
 select id into a from public.actor where slug='joe';
 foreach kind in array array['accessible','inaccessible','deleted','unknown'] loop
  insert into public.party(kind,name,created_by,updated_by) values('person','Synthetic '||kind,a,a) returning id into p;
  insert into public.client(party_id,created_by,updated_by) values(p,a,a) returning id into c;
  insert into public.deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values(c,'Synthetic '||kind,'other','legal','joe',a,a) returning id into d;
  insert into deal_tour_cases(kind,deal_id,client_party) values(kind,d,p);
 end loop;
end $fixtures$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='joe';
select pg_temp.authorize_deal_tours('joe');
do $create_accessible$
declare fixture record; t uuid;
begin
 for fixture in select * from deal_tour_cases where kind<>'unknown' loop
  t:=ops.create_tour_domain('carr-internal','Matrix '||fixture.kind,'deal',fixture.deal_id::text,'proof','{}','{}');
  if t is null then raise exception 'create_accessible failed'; end if;
  update deal_tour_cases set tour_id=t where kind=fixture.kind;
  perform ops.append_tour_selection_cart_version('carr-internal',t,null,'[]',0,'sha256:'||repeat('a',64));
 end loop;
end $create_accessible$;
reset session authorization;
do $internal_fixtures$
declare fixture record; prop uuid; projection uuid; job uuid; route uuid; dig text:='sha256:'||repeat('a',64);
begin
 insert into ops.tour_property(organization_tenant_id,property_status) values('carr-internal','active') returning id into prop;
 for fixture in select * from deal_tour_cases where tour_id is not null loop
  select id into route from ops.tour_route_version where tour_id=fixture.tour_id;
  insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest)
   values('carr-internal',fixture.tour_id,route,0,'joe',dig);
  insert into ops.tour_property_membership(organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest)
   values('carr-internal',fixture.tour_id,prop,1,1,'A',dig);
  insert into ops.tour_public_projection(organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
   values('carr-internal',fixture.tour_id,1,1,now(),true,dig,'draft') returning id into projection;
  insert into ops.tour_pdf_render_job(organization_tenant_id,projection_id,requested_by_actor_id,request,projection_digest,packet_digest,template_digest,renderer_digest,qc_ruleset_digest,expected_property_count)
   values('carr-internal',projection,'joe','{}',dig,dig,dig,dig,dig,1) returning id into job;
  update deal_tour_cases set projection_id=projection,render_id=job,route_id=route where kind=fixture.kind;
 end loop;
end $internal_fixtures$;
update public.deal set owner='dell' where id=(select deal_id from deal_tour_cases where kind='inaccessible');
update public.party set deleted_at=now() where id=(select client_party from deal_tour_cases where kind='deleted');
delete from public.deal where id=(select deal_id from deal_tour_cases where kind='unknown');
set local session authorization carr_writer;
set local carr.acting_actor_slug='joe';
select pg_temp.authorize_deal_tours('joe');
do $create_unavailable$
declare fixture record;
begin
 for fixture in select * from deal_tour_cases where kind<>'accessible' loop
  begin
   perform ops.create_tour_domain('carr-internal','Denied','deal',fixture.deal_id::text,'proof','{}','{}');
   raise exception 'create_unavailable failed for %',fixture.kind;
  exception when raise_exception then if sqlerrm<>'tour deal is unavailable' then raise; end if; end;
 end loop;
end $create_unavailable$;
do $read_accessible$
declare t uuid:=(select tour_id from deal_tour_cases where kind='accessible');
begin
 if ops.read_tour_internal_detail('carr-internal',t,'joe') is null then raise exception 'read_accessible detail failed'; end if;
 if ops.read_tour_selection_cart('carr-internal',t,'joe') is null then raise exception 'read_accessible cart failed'; end if;
end $read_accessible$;
do $internal_reads_accessible$
declare f record;
begin
 select * into f from deal_tour_cases where kind='accessible';
 if ops.read_tour_projection_creation_metadata('carr-internal',f.tour_id,f.route_id,'joe') is null then raise exception 'accessible projection metadata missing'; end if;
 if ops.read_tour_projection_seal_candidates('carr-internal',f.projection_id,'joe') is null then raise exception 'accessible candidates missing'; end if;
 if ops.read_tour_feedback('carr-internal',f.projection_id,'joe',null,100) is null then raise exception 'accessible feedback missing'; end if;
 if ops.read_tour_sharing_library('carr-internal',f.projection_id,'joe',null,100) is null then raise exception 'accessible shares missing'; end if;
 if ops.read_tour_pdf_render('carr-internal',f.render_id,'joe') is null then raise exception 'accessible PDF status missing'; end if;
end $internal_reads_accessible$;
do $read_unavailable$
declare fixture record; t uuid;
begin
 for fixture in select * from deal_tour_cases where kind<>'accessible' loop
  t:=coalesce(fixture.tour_id,fixture.deal_id);
  if ops.read_tour_internal_detail('carr-internal',t,'joe') is not null then raise exception 'read_unavailable detail %',fixture.kind; end if;
  if ops.read_tour_selection_cart('carr-internal',t,'joe') is not null then raise exception 'read_unavailable cart %',fixture.kind; end if;
  if ops.read_tour_projection_creation_metadata('carr-internal',t,coalesce(fixture.route_id,t),'joe') is not null then raise exception 'unavailable projection metadata %',fixture.kind; end if;
  if ops.read_tour_projection_seal_candidates('carr-internal',coalesce(fixture.projection_id,t),'joe') is not null then raise exception 'unavailable candidates %',fixture.kind; end if;
  if ops.read_tour_feedback('carr-internal',coalesce(fixture.projection_id,t),'joe',null,100) is not null then raise exception 'unavailable feedback %',fixture.kind; end if;
  if ops.read_tour_sharing_library('carr-internal',coalesce(fixture.projection_id,t),'joe',null,100) is not null then raise exception 'unavailable shares %',fixture.kind; end if;
  if ops.read_tour_pdf_render('carr-internal',coalesce(fixture.render_id,t),'joe') is not null then raise exception 'unavailable PDF status %',fixture.kind; end if;
  if ops.read_tour_pdf_artifact_for_review('carr-internal',coalesce(fixture.render_id,t),'joe') is not null then raise exception 'unavailable preview %',fixture.kind; end if;
  if ops.read_tour_pdf_artifact_for_download('carr-internal',coalesce(fixture.render_id,t),'joe') is not null then raise exception 'unavailable download %',fixture.kind; end if;
  if ops.read_tour_packet_for_render('carr-internal',coalesce(fixture.projection_id,t),'joe') is not null then raise exception 'unavailable render packet %',fixture.kind; end if;
  if exists(select 1 from jsonb_array_elements(ops.list_tour_library('carr-internal','joe')->'tours') x where x->>'deal_id'=fixture.deal_id::text) then raise exception 'read_unavailable library %',fixture.kind; end if;
 end loop;
 -- The actor argument alone cannot impersonate another partner.
 if ops.read_tour_internal_detail('carr-internal',(select tour_id from deal_tour_cases where kind='inaccessible'),'dell') is not null then raise exception 'actor substitution leaked'; end if;
end $read_unavailable$;
do $mixed_library$
declare ids jsonb;
begin
 select jsonb_agg(x->>'deal_id') into ids from jsonb_array_elements(ops.list_tour_library('carr-internal','joe')->'tours') x where x->>'tour_name' like 'Matrix %';
 if ids is distinct from jsonb_build_array((select deal_id::text from deal_tour_cases where kind='accessible')) then raise exception 'mixed_library failed'; end if;
end $mixed_library$;
reset session authorization;
do $withdraw_restore$
declare t uuid:=(select tour_id from deal_tour_cases where kind='accessible'); d uuid:=(select deal_id from deal_tour_cases where kind='accessible');
begin
 update public.deal set owner='dell' where id=d;
 if ops.read_tour_internal_detail('carr-internal',t,'joe') is not null then raise exception 'withdraw_restore stale decision leaked'; end if;
 update public.deal set owner='joe' where id=d;
 perform pg_temp.authorize_deal_tours('joe');
 if ops.read_tour_internal_detail('carr-internal',t,'joe') is null then raise exception 'withdraw_restore failed'; end if;
end $withdraw_restore$;
do $immutable_binding$
begin
 begin
  delete from public.deal where id=(select deal_id from deal_tour_cases where kind='accessible');
  raise exception 'immutable_binding deletion failed';
 exception when foreign_key_violation then null; end;
 begin
  update ops.tour set deal_id=(select deal_id from deal_tour_cases where kind='inaccessible') where id=(select tour_id from deal_tour_cases where kind='accessible');
  raise exception 'immutable_binding rebind failed';
 exception when raise_exception then if sqlerrm<>'tour deal binding is immutable' then raise; end if; end;
end $immutable_binding$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='joe';
do $legacy_unchanged$
declare kind text; t uuid;
begin
 foreach kind in array array['client','work'] loop
  t:=ops.create_tour_domain('legacy-fixture','Legacy Matrix',kind,'opaque','proof','{}','{}');
  if ops.read_tour_internal_detail('legacy-fixture',t,'joe')->>'subject_type' is distinct from kind then raise exception 'legacy_unchanged failed'; end if;
 end loop;
end $legacy_unchanged$;
reset session authorization;
rollback;
