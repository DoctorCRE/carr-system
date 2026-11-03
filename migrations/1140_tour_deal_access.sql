-- WR586.S2.J4: consume the existing business.read_deal decision on every internal path.
-- rollback: forward-only; restore function bodies in a successor if needed.
-- lock-review: function replacements only; no table rewrite or data mutation.
create function ops.tour_deal_visible(p_tenant text,p_deal uuid,p_actor text) returns boolean
language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
 select p_deal is null or (p_tenant='carr-internal' and exists(
   select 1 from public.deal d join public.client c on c.id=d.client_id
   join public.party p on p.id=c.party_id
   cross join lateral (select nullif(current_setting('carr.tour_deal_access',true),'')::jsonb as decision) ctx
   join public.actor a on a.slug=ctx.decision->>'slug' and a.active
   where d.id=p_deal and c.merged_into is null and p.merged_into is null and p.deleted_at is null
     and p_actor in (a.slug,a.id::text)
     and ctx.decision->'deals'->d.id::text = jsonb_build_object('owner',d.owner,'version',d.version)
 ));
$$;
revoke all on function ops.tour_deal_visible(text,uuid,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.tour_target_visible(p_tenant text,p_kind text,p_id uuid,p_actor text) returns boolean
language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
 select coalesce((select ops.tour_deal_visible(p_tenant,t.deal_id,p_actor)
 from ops.tour t where t.organization_tenant_id=p_tenant and t.id=case p_kind
 when 'tour' then p_id
 when 'route' then (select tour_id from ops.tour_route_version where id=p_id and organization_tenant_id=p_tenant)
 when 'projection' then (select tour_id from ops.tour_public_projection where id=p_id and organization_tenant_id=p_tenant)
 when 'render' then (select p.tour_id from ops.tour_pdf_render_job j join ops.tour_public_projection p on p.id=j.projection_id and p.organization_tenant_id=j.organization_tenant_id where j.id=p_id and j.organization_tenant_id=p_tenant)
 when 'share' then (select p.tour_id from ops.tour_share_grant g join ops.tour_public_projection p on p.id=g.projection_id and p.organization_tenant_id=g.organization_tenant_id where g.id=p_id and g.organization_tenant_id=p_tenant)
 end),false);
$$;
revoke all on function ops.tour_target_visible(text,text,uuid,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create or replace function ops.tour_deal_binding_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
 if TG_OP='UPDATE' and old.deal_id is not null and
    (new.organization_tenant_id is distinct from old.organization_tenant_id or new.deal_id is distinct from old.deal_id or new.subject_type is distinct from old.subject_type or new.subject_id is distinct from old.subject_id) then
   raise exception 'tour deal binding is immutable';
 end if;
 if new.deal_id is not null and (TG_OP='INSERT' or new.deal_id is distinct from old.deal_id) and
    not ops.tour_deal_visible(new.organization_tenant_id,new.deal_id,ops.tour_server_actor_id()) then
   raise exception 'tour deal is unavailable';
 end if;
 return new;
end $$;

-- Preserve installed bodies, signatures, owners and grants. The explicit set
-- excludes digest-only public share paths and property-level canonical reads.
do $guard_internal$
declare f record; body text; guard text; target text; actor text; definition text;
begin
 for f in select p.*,l.lanname from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_language l on l.oid=p.prolang
 where n.nspname='ops' and p.proname=any(array[
 'accept_tour_route_version','append_tour_cheat_sheet_revision','append_tour_route_stop',
 'append_tour_route_stop_transition','append_tour_route_version','append_tour_selection_cart_version',
 'create_tour_public_projection_draft','issue_tour_share_grant','prepare_tour_route_version',
 'read_tour_feedback','read_tour_internal_detail','read_tour_packet_for_render',
 'read_tour_pdf_artifact_for_download','read_tour_pdf_artifact_for_review','read_tour_pdf_render',
 'read_tour_projection_creation_metadata','read_tour_projection_seal_candidates',
 'read_tour_selection_cart','read_tour_sharing_library','record_tour_map_promotion_receipt',
 'record_tour_pdf_human_review','record_tour_pdf_render_result','request_tour_pdf_render',
 'restore_tour_cheat_sheet_revision','revoke_tour_share_grant','rotate_tour_share_grant','seal_tour_public_projection'])
 loop
   actor:=case when 'p_actor_id'=any(f.proargnames) then 'p_actor_id' else 'ops.tour_server_actor_id()' end;
   target:=case
    when 'p_tour_id'=any(f.proargnames) then '''tour'',p_tour_id'
    when 'p_route_version_id'=any(f.proargnames) then '''route'',p_route_version_id'
    when 'p_old_route_version_id'=any(f.proargnames) then '''route'',p_old_route_version_id'
    when 'p_projection_id'=any(f.proargnames) then '''projection'',p_projection_id'
    when 'p_render_job_id'=any(f.proargnames) then '''render'',p_render_job_id'
    when 'p_share_grant_id'=any(f.proargnames) then '''share'',p_share_grant_id'
    when f.proname='request_tour_pdf_render' then '''projection'',(p_request->>''projection_id'')::uuid'
   end;
   if target is null then raise exception 'Tour access target missing for %',f.proname; end if;
   guard:='ops.tour_target_visible(p_tenant,'||target||','||actor||')';
   if f.proname='append_tour_route_stop_transition' then
     guard:='(p_old_route_version_id is null or '||guard||') and ops.tour_target_visible(p_tenant,''route'',p_new_route_version_id,'||actor||')';
   end if;
   if f.proname='rotate_tour_share_grant' then
     guard:=guard||' and ops.tour_target_visible(p_tenant,''share'',p_share_grant_id,'||actor||')';
   end if;
   if f.lanname='sql' then
     body:='select case when '||guard||' then ('||regexp_replace(btrim(f.prosrc),';\s*$','')||') else null end';
   elsif f.lanname='plpgsql' then
     body:=regexp_replace(f.prosrc,'\mbegin\M','begin if not ('||guard||') then return null; end if;','i');
   else raise exception 'Unsupported Tour function language'; end if;
   if f.proname='read_tour_sharing_library' then
     body:=replace(body,'r.revoked_at,r.reason','r.revoked_at receipt_revoked_at,r.reason');
     body:=replace(body,'''revoked_at'',revoked_at','''revoked_at'',receipt_revoked_at');
   end if;
   definition:=pg_get_functiondef(f.oid);
   execute replace(definition,f.prosrc,body);
 end loop;
 select pg_get_functiondef('ops.list_tour_library(text,text)'::regprocedure) into definition;
 definition:=replace(definition,
   '(t.deal_id is null or (p_tenant=''carr-internal'' and exists(select 1 from public.actor a where (a.id::text=p_actor_id or a.slug=p_actor_id) and a.active)))',
   'ops.tour_deal_visible(p_tenant,t.deal_id,p_actor_id)');
 execute definition;
end $guard_internal$;

-- The registered read-tour-public-projection tool is an internal UUID door.
-- Share-session doors validate their grant independently and use private content.
do $projection_doors$
declare definition text; body text; f record;
begin
 select pg_get_functiondef('ops.read_tour_public_projection(text,uuid)'::regprocedure) into definition;
 execute replace(definition,'FUNCTION ops.read_tour_public_projection(', 'FUNCTION ops.tour_projection_content(');
 revoke all on function ops.tour_projection_content(text,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
 select prosrc into body from pg_proc where oid='ops.read_tour_public_projection(text,uuid)'::regprocedure;
 execute replace(definition,body,
   'select case when ops.tour_target_visible(p_tenant,''projection'',p_projection_id,'||
   '(nullif(current_setting(''carr.tour_deal_access'',true),'''')::jsonb->>''slug'')) '||
   'then ops.tour_projection_content(p_tenant,p_projection_id) else null end');
 for f in select p.oid,p.prosrc from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where n.nspname='ops' and p.proname=any(array[
   'read_tour_share_packet','read_tour_share_map','read_tour_share_feedback',
   'tour_share_feedback_target','tour_public_map_projection_evidence_ready']) loop
  definition:=pg_get_functiondef(f.oid);
  execute replace(definition,f.prosrc,replace(f.prosrc,'ops.read_tour_public_projection(', 'ops.tour_projection_content('));
 end loop;
end $projection_doors$;
