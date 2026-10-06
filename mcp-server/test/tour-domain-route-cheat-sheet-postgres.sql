\set ON_ERROR_STOP on
-- Disposable typed lifecycle proof for 0429; every fixture is rolled back.
begin;
do $owner$
begin
  insert into ops.tour_rights_receipt(id,organization_tenant_id,provider,sku,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status)
  values('40000000-0000-4000-8000-000000000010','tour-slice4-proof','route-proof','sku','route-policy',1,'sha256:'||repeat('a',64),'https://example.invalid','2026-08-27','owner','proof','["*"]','["route_planning"]','2026-08-27','active');
  insert into ops.tour_property(id,organization_tenant_id,property_status) values
  ('40000000-0000-4000-8000-000000000001','tour-slice4-proof','active'),
  ('40000000-0000-4000-8000-000000000002','tour-slice4-proof','active'),
  ('40000000-0000-4000-8000-000000000003','tour-slice4-proof','active');
end $owner$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
do $writer$
declare t uuid; r1 uuid; a uuid; h uuid;
begin
 t:=ops.create_tour_domain('tour-slice4-proof','typed','work','opaque','proof','{}','{}');
 select id into r1 from ops.tour_route_version where tour_id=t and route_version=1;
 a:=ops.append_tour_route_stop('tour-slice4-proof',r1,'40000000-0000-4000-8000-000000000001',1,'A','active','2026-08-28T14:00:00Z','2026-08-28T14:30:00Z',true,20,5,'approved','sha256:'||repeat('1',64));
 h:=ops.append_tour_route_stop('tour-slice4-proof',r1,'40000000-0000-4000-8000-000000000002',null,null,'held',null,null,false,0,0,'unknown','sha256:'||repeat('2',64));
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r1,null,a,'added');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r1,null,h,'added');
end $writer$;
set local session authorization carr_authority;
set local carr.acting_actor_slug='tour-proof';
do $authority$
declare r1 uuid; begin
 select id into r1 from ops.tour_route_version where organization_tenant_id='tour-slice4-proof' and route_version=1;
 perform ops.accept_tour_route_version('tour-slice4-proof',r1,0,(ops.read_tour_internal_detail('tour-slice4-proof',(select tour_id from ops.tour_route_version where id=r1),'tour-proof')#>>'{routes,0,acceptance_digest}'));
end $authority$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
do $writer$
declare t uuid; r1 uuid; abandoned_r2 uuid; oa uuid; oh uuid; replacement uuid;
begin
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof';
 select id into r1 from ops.tour_route_version where tour_id=t and route_version=1;
 abandoned_r2:=ops.append_tour_route_version('tour-slice4-proof',t,2,r1,'{}','{}','manual',null,null,'{}',null,1,null);
 if abandoned_r2 is null then raise exception 'abandoned route draft was not retained'; end if;
 replacement:=ops.append_tour_route_stop('tour-slice4-proof',abandoned_r2,'40000000-0000-4000-8000-000000000003',1,'A','active',null,null,false,10,5,'approved','sha256:'||repeat('4',64));
 select id into oa from ops.tour_route_stop where route_version_id=r1 and property_id='40000000-0000-4000-8000-000000000001';
 select id into oh from ops.tour_route_stop where route_version_id=r1 and property_id='40000000-0000-4000-8000-000000000002';
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,abandoned_r2,oa,null,'removed');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,abandoned_r2,oh,null,'held');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,abandoned_r2,null,replacement,'added');
end $writer$;
set local session authorization carr_authority;
set local carr.acting_actor_slug='tour-proof';
do $authority$
declare r2 uuid;
begin
 select id into r2 from ops.tour_route_version where organization_tenant_id='tour-slice4-proof' and route_version=2;
 begin
   perform ops.accept_tour_route_version('tour-slice4-proof',r2,1,(ops.read_tour_internal_detail('tour-slice4-proof',(select tour_id from ops.tour_route_version where id=r2),'tour-proof')#>>'{routes,0,acceptance_digest}'));
   raise exception 'expected locked appointment preservation refusal';
 exception when raise_exception then
   if sqlerrm<>'route acceptance must preserve every locked appointment window, dwell, and buffer' then raise; end if;
 end;
end $authority$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
do $writer$
declare t uuid; r1 uuid; r3 uuid; oa uuid; oh uuid; na uuid; nh uuid; n uuid;
begin
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof';
 select id into r1 from ops.tour_route_version where tour_id=t and route_version=1;
 r3:=ops.append_tour_route_version('tour-slice4-proof',t,3,r1,'{}','{}','provider','route-proof','40000000-0000-4000-8000-000000000010','{}','sha256:'||repeat('4',64),1,'route-policy');
 na:=ops.append_tour_route_stop('tour-slice4-proof',r3,'40000000-0000-4000-8000-000000000001',2,'B','active','2026-08-28T14:00:00Z','2026-08-28T14:30:00Z',true,20,5,'approved','sha256:'||repeat('5',64));
 nh:=ops.append_tour_route_stop('tour-slice4-proof',r3,'40000000-0000-4000-8000-000000000002',null,null,'held',null,null,false,0,0,'unknown','sha256:'||repeat('6',64));
 n:=ops.append_tour_route_stop('tour-slice4-proof',r3,'40000000-0000-4000-8000-000000000003',1,'A','active',null,null,false,10,5,'approved','sha256:'||repeat('7',64));
 select id into oa from ops.tour_route_stop where route_version_id=r1 and property_id='40000000-0000-4000-8000-000000000001';
 select id into oh from ops.tour_route_stop where route_version_id=r1 and property_id='40000000-0000-4000-8000-000000000002';
 begin
   perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,r3,oa,n,'reordered');
   raise exception 'expected cross-property reorder refusal';
 exception when raise_exception then
   if sqlerrm<>'route transition property identity mismatch' then raise; end if;
 end;
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,r3,oa,na,'reordered');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,r3,oh,nh,'held');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r3,null,n,'added');
 perform ops.append_tour_cheat_sheet_revision('tour-slice4-proof',t,'{"internal":"contact"}',0);
end $writer$;
set local session authorization carr_authority;
set local carr.acting_actor_slug='tour-proof';
do $authority$
declare r3 uuid; t uuid; begin
 select id into r3 from ops.tour_route_version where organization_tenant_id='tour-slice4-proof' and route_version=3;
 perform ops.accept_tour_route_version('tour-slice4-proof',r3,1,(ops.read_tour_internal_detail('tour-slice4-proof',(select tour_id from ops.tour_route_version where id=r3),'tour-proof')#>>'{routes,0,acceptance_digest}'));
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof';
 if (select count(*) from ops.tour_property_membership where tour_id=t and route_version=3)<>2 then raise exception 'held/excluded stop entered canonical membership'; end if;
end $authority$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
do $writer$
declare t uuid; rev uuid; begin
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof';
 select id into rev from ops.tour_cheat_sheet_revision where tour_id=t and revision_number=1;
 perform ops.restore_tour_cheat_sheet_revision('tour-slice4-proof',t,rev,1);
 begin update ops.tour_route_stop set route_label='X' where organization_tenant_id='tour-slice4-proof'; raise exception 'expected raw DML refusal'; exception when insufficient_privilege then null; end;
end $writer$;
reset session authorization;
do $owner$
begin
 begin update ops.tour_route_stop set route_label='X' where organization_tenant_id='tour-slice4-proof'; raise exception 'expected append-only refusal'; exception when raise_exception then if sqlerrm<>'tour_route_stop is append-only' then raise; end if; end;
 if has_table_privilege('carr_writer','ops.tour_route_stop','insert') then raise exception 'raw DML granted'; end if;
 if has_function_privilege('public','ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text)','execute') or has_function_privilege('carr_reader','ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text)','execute') or has_function_privilege('carr_jobs','ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text)','execute') then raise exception 'revised version seam leaked'; end if;
 if has_function_privilege('public','ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamptz,timestamptz,boolean,integer,integer,text,text)','execute') or has_function_privilege('carr_reader','ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamptz,timestamptz,boolean,integer,integer,text,text)','execute') or has_function_privilege('carr_jobs','ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamptz,timestamptz,boolean,integer,integer,text,text)','execute') then raise exception 'revised stop seam leaked'; end if;
 if exists(select 1 from ops.tour_public_projection_fact where organization_tenant_id='tour-slice4-proof') then raise exception 'public noninterference failed'; end if;
end $owner$;
-- Review A/B, then a second operator appends C to the same draft.
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-reviewer';
do $race_setup$
declare t uuid; r uuid; a uuid; b uuid;
begin
 t:=ops.create_tour_domain('tour-slice4-proof','review race','work','synthetic-race','proof','{}','{}');
 select id into r from ops.tour_route_version where tour_id=t and route_version=1;
 a:=ops.append_tour_route_stop('tour-slice4-proof',r,'40000000-0000-4000-8000-000000000001',1,'A','active',null,null,false,20,5,'approved','sha256:'||repeat('1',64));
 b:=ops.append_tour_route_stop('tour-slice4-proof',r,'40000000-0000-4000-8000-000000000002',2,'B','active',null,null,false,20,5,'approved','sha256:'||repeat('2',64));
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r,null,a,'added');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r,null,b,'added');
 perform set_config('tour_test.review_digest',ops.read_tour_internal_detail('tour-slice4-proof',t,'tour-reviewer')#>>'{routes,0,acceptance_digest}',true);
 if current_setting('tour_test.review_digest') !~ '^sha256:[a-f0-9]{64}$' then raise exception 'draft review digest missing'; end if;
end $race_setup$;
set local carr.acting_actor_slug='tour-second-operator';
do $race_append$
declare r uuid; c uuid;
begin
 select v.id into r from ops.tour_route_version v join ops.tour t on t.id=v.tour_id where t.tour_name='review race' and t.organization_tenant_id='tour-slice4-proof';
 c:=ops.append_tour_route_stop('tour-slice4-proof',r,'40000000-0000-4000-8000-000000000003',3,'C','active',null,null,false,20,5,'approved','sha256:'||repeat('3',64));
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r,null,c,'added');
end $race_append$;
set local session authorization carr_authority;
set local carr.acting_actor_slug='tour-reviewer';
do $race_accept$
declare r uuid; t uuid;
begin
 select v.id,v.tour_id into r,t from ops.tour_route_version v join ops.tour t on t.id=v.tour_id where t.tour_name='review race' and t.organization_tenant_id='tour-slice4-proof';
 begin
   perform ops.accept_tour_route_version('tour-slice4-proof',r,0,current_setting('tour_test.review_digest'));
   raise exception 'CONFIRMED: unreviewed appended stop was accepted';
 exception when raise_exception then
   if sqlerrm<>'route acceptance refuses changed draft contents' then raise; end if;
 end;
 if exists(select 1 from ops.tour_route_version_acceptance where route_version_id=r) or exists(select 1 from ops.tour_property_membership where tour_id=t) then raise exception 'stale acceptance left canonical effects'; end if;
 -- An explicit reload/review of A/B/C now succeeds and records that digest.
 perform set_config('tour_test.current_digest',ops.read_tour_internal_detail('tour-slice4-proof',t,'tour-reviewer')#>>'{routes,0,acceptance_digest}',true);
 if current_setting('tour_test.current_digest')=current_setting('tour_test.review_digest') then raise exception 'appended stop did not change review digest'; end if;
 set local timezone='Pacific/Auckland';
 if (ops.read_tour_internal_detail('tour-slice4-proof',(select tour_id from ops.tour_route_version where id=r),'tour-proof')#>>'{routes,0,acceptance_digest}')<>current_setting('tour_test.current_digest') then raise exception 'review digest depends on session timezone'; end if;
 perform ops.accept_tour_route_version('tour-slice4-proof',r,0,current_setting('tour_test.current_digest'));
 if (select count(*) from ops.tour_property_membership where tour_id=t)<>3 then raise exception 'fresh reviewed route did not accept all stops'; end if;
 if (select acceptance_digest from ops.tour_route_version_acceptance where route_version_id=r)<>current_setting('tour_test.current_digest') then raise exception 'accepted digest differs from reviewed digest'; end if;
 if ops.read_tour_internal_detail('other-synthetic-tenant',t,'tour-reviewer') is not null then raise exception 'digest crossed tenant boundary'; end if;
end $race_accept$;
-- The app default is an invalid client marker. Preserve the strict database
-- boundary; the UI must use compact labels such as 1/A instead of Stop 1.
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-label-proof';
do $label_setup$
declare t uuid; r uuid; s uuid;
begin
 t:=ops.create_tour_domain('tour-slice4-proof','label contract','work','synthetic-label','proof','{}','{}');
 select id into r from ops.tour_route_version where tour_id=t;
 s:=ops.append_tour_route_stop('tour-slice4-proof',r,'40000000-0000-4000-8000-000000000001',1,'Stop 1','active',null,null,false,20,5,'approved','sha256:'||repeat('1',64));
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r,null,s,'added');
end $label_setup$;
set local session authorization carr_authority;
set local carr.acting_actor_slug='tour-label-proof';
do $label_accept$
declare r uuid;
begin
 select v.id into r from ops.tour_route_version v join ops.tour t on t.id=v.tour_id where t.tour_name='label contract' and t.organization_tenant_id='tour-slice4-proof';
 begin
   perform ops.accept_tour_route_version('tour-slice4-proof',r,0,(ops.read_tour_internal_detail('tour-slice4-proof',(select tour_id from ops.tour_route_version where id=r),'tour-proof')#>>'{routes,0,acceptance_digest}'));
   raise exception 'Stop 1 unexpectedly accepted as client marker';
 exception when raise_exception then
   if sqlerrm<>'route stop label must be a 1-3 letter or digit client marker' then raise; end if;
   raise notice 'CONFIRMED: app default Stop 1 fails the client-marker membership guard';
 end;
end $label_accept$;
reset session authorization;
do $digest_acl$
begin
 if has_function_privilege('public','ops.tour_route_review_digest(text,uuid)','execute')
   or has_function_privilege('carr_writer','ops.tour_route_review_digest(text,uuid)','execute')
   or has_function_privilege('carr_authority','ops.tour_route_review_digest(text,uuid)','execute') then
   raise exception 'review digest leaked an unnecessary direct execution door';
 end if;
end $digest_acl$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
select ops.create_tour_domain('tour-slice4-proof','subject-client','client','11111111-1111-4111-8111-111111111111','proof','{}','{}');
do $subject_read$
declare t uuid; detail jsonb;
begin
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof' and tour_name='typed';
 detail:=ops.read_tour_internal_detail('tour-slice4-proof',t,'tour-proof');
 if detail->>'subject_type' is distinct from 'work' or detail->>'subject_id' is distinct from 'opaque' then
   raise exception 'internal detail lost the stored opaque subject binding';
 end if;
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof' and tour_name='subject-client';
 detail:=ops.read_tour_internal_detail('tour-slice4-proof',t,'tour-proof');
 if detail->>'subject_type' is distinct from 'client' or detail->>'subject_id' is distinct from '11111111-1111-4111-8111-111111111111' then raise exception 'client subject binding changed'; end if;
 if ops.read_tour_internal_detail('other-synthetic-tenant',t,'tour-proof') is not null then raise exception 'subject binding crossed tenant boundary'; end if;
 if ops.read_tour_internal_detail('tour-slice4-proof',t,' ') is not null then raise exception 'subject binding allowed a blank actor'; end if;
end $subject_read$;
reset session authorization;
do $subject_acl$
begin
 if has_function_privilege('carr_reader','ops.read_tour_internal_detail(text,uuid,text)','execute') then raise exception 'internal subject read widened reader access'; end if;
end $subject_acl$;
rollback;
