-- WR586.S2.J2: canonical Deal ownership without rewriting legacy Tour subjects.
-- rollback: forward-only; remove this capability with a successor after migrating consumers.
-- lock-review: nullable column and constraints lock ops.tour briefly; no data rewrite or backfill.
alter table ops.tour add column deal_id uuid references public.deal(id);
alter table ops.tour drop constraint tour_subject_binding_valid;
alter table ops.tour add constraint tour_subject_binding_valid check (
 (subject_type is null and subject_id is null and subject_bound_at is null) or
 (subject_type in ('client','work','deal') and subject_id ~ '^[A-Za-z0-9._:-]{1,200}$' and subject_bound_at is not null)
);
alter table ops.tour add constraint tour_deal_binding_valid check (
 (subject_type is not distinct from 'deal') = (deal_id is not null) and
 (deal_id is null or (subject_id is not distinct from deal_id::text and subject_bound_at is not null))
);
create function ops.tour_deal_binding_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_actor text;
begin
 if TG_OP='UPDATE' and old.deal_id is not null and
    (new.organization_tenant_id is distinct from old.organization_tenant_id or new.deal_id is distinct from old.deal_id or new.subject_type is distinct from old.subject_type or new.subject_id is distinct from old.subject_id) then
   raise exception 'tour deal binding is immutable';
 end if;
 if new.deal_id is not null and (TG_OP='INSERT' or new.deal_id is distinct from old.deal_id) then
   v_actor:=ops.tour_server_actor_id();
   if new.organization_tenant_id is distinct from 'carr-internal' or
      not exists(select 1 from public.deal where id=new.deal_id) or
      not exists(select 1 from public.actor where slug=v_actor and active) then
     raise exception 'tour deal is unavailable';
   end if;
 end if;
 return new;
end $$;
revoke all on function ops.tour_deal_binding_guard() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
create trigger tour_deal_binding_guard before insert or update on ops.tour
for each row execute function ops.tour_deal_binding_guard();

create or replace function ops.create_tour_domain(p_tenant text,p_tour_name text,p_subject_type text,p_subject_id text,p_canonical_dataset_version text,p_start_point jsonb,p_end_point jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_actor text;
begin
  v_actor:=ops.tour_server_actor_id();
  if p_tenant is null or p_tour_name is null or p_tour_name !~ '^.{1,240}$' or p_subject_type not in ('client','work','deal') or p_subject_id is null or p_subject_id !~ '^[A-Za-z0-9._:-]{1,200}$' or p_canonical_dataset_version is null or p_canonical_dataset_version !~ '^.{1,240}$' or jsonb_typeof(p_start_point)<>'object' or jsonb_typeof(p_end_point)<>'object' then raise exception 'tour subject binding is invalid'; end if;
  if p_subject_type='deal' then p_subject_id:=lower(p_subject_id); end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || p_subject_type || ':' || p_subject_id,386));
  insert into ops.tour(organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version,subject_type,subject_id,subject_bound_at,deal_id) values(p_tenant,p_tour_name,'draft',1,p_canonical_dataset_version,p_subject_type,p_subject_id,now(),case when p_subject_type='deal' then p_subject_id::uuid end) returning id into v_id;
  insert into ops.tour_route_version(organization_tenant_id,tour_id,route_version,start_point,end_point,routing_source,routing_request,created_by_actor_id) values(p_tenant,v_id,1,p_start_point,p_end_point,'manual','{}',v_actor);
  return v_id;
end $$;

CREATE OR REPLACE FUNCTION ops.list_tour_library(p_tenant text, p_actor_id text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
    AS $$
  select jsonb_build_object('tours',coalesce(jsonb_agg(jsonb_build_object(
    'subject_type',t.subject_type,'subject_id',t.subject_id,'deal_id',t.deal_id,'deal_name',(select d.name from public.deal d where d.id=t.deal_id),'id',t.id,'tour_name',t.tour_name,'tour_status',t.tour_status,'route_version',t.route_version,
    'updated_at',t.updated_at,'property_count',(select count(*) from ops.tour_property_membership m where m.organization_tenant_id=t.organization_tenant_id and m.tour_id=t.id and m.route_version=t.route_version),
    'projection_count',(select count(*) from ops.tour_public_projection p where p.organization_tenant_id=t.organization_tenant_id and p.tour_id=t.id),
    'share_count',(select count(*) from ops.tour_share_grant g join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id where p.organization_tenant_id=t.organization_tenant_id and p.tour_id=t.id)
  ) order by t.updated_at desc,t.id),'[]'::jsonb))
  from ops.tour t where t.organization_tenant_id=p_tenant and (t.deal_id is null or (p_tenant='carr-internal' and exists(select 1 from public.actor a where (a.id::text=p_actor_id or a.slug=p_actor_id) and a.active))) and nullif(btrim(p_actor_id),'') is not null;
$$;

CREATE OR REPLACE FUNCTION ops.read_tour_internal_detail(p_tenant text, p_tour_id uuid, p_actor_id text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
    AS $$
  select jsonb_build_object(
    'subject_type',t.subject_type,'subject_id',t.subject_id,
    'deal_id',t.deal_id,'deal_name',(select d.name from public.deal d where d.id=t.deal_id),'id',t.id,'tour_name',t.tour_name,'tour_status',t.tour_status,'route_version',t.route_version,'updated_at',t.updated_at,
    'routes',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'route_version',v.route_version,'routing_source',v.routing_source,'acceptance_digest',ops.tour_route_review_digest(v.organization_tenant_id,v.id),'created_at',v.created_at,'accepted',a.id is not null,'stops',coalesce((select jsonb_agg(jsonb_build_object(
      'id',s.id,'property_id',s.property_id,'route_sequence',s.route_sequence,'route_label',s.route_label,'stop_state',s.stop_state,
      'appointment_start',s.appointment_start,'appointment_end',s.appointment_end,'locked_appointment',s.locked_appointment,
      'dwell_minutes',s.dwell_minutes,'buffer_minutes',s.buffer_minutes,'access_coordinate_status',s.access_coordinate_status,
      'property_name',(select fa.value#>>'{}' from ops.tour_field_assertion fa where fa.organization_tenant_id=s.organization_tenant_id and fa.property_id=s.property_id and fa.field_key='display.name' and fa.review_state='reviewed' order by fa.effective_from desc,fa.id desc limit 1),
      'property_address',(select fa.value#>>'{}' from ops.tour_field_assertion fa where fa.organization_tenant_id=s.organization_tenant_id and fa.property_id=s.property_id and fa.field_key='display.address' and fa.review_state='reviewed' order by fa.effective_from desc,fa.id desc limit 1)
    ) order by s.route_sequence nulls last,s.id) from ops.tour_route_stop s where s.organization_tenant_id=v.organization_tenant_id and s.route_version_id=v.id),'[]'::jsonb)) order by v.route_version desc) from ops.tour_route_version v left join ops.tour_route_version_acceptance a on a.organization_tenant_id=v.organization_tenant_id and a.route_version_id=v.id where v.organization_tenant_id=t.organization_tenant_id and v.tour_id=t.id),'[]'::jsonb),
    'cheat_sheet',coalesce((select jsonb_build_object(
      'revision_id',c.id,'revision_number',c.revision_number,'content',c.content,'revision_kind',c.revision_kind,'created_at',c.created_at,
      'restore_revision_id',(select prior.id from ops.tour_cheat_sheet_revision prior where prior.organization_tenant_id=c.organization_tenant_id and prior.tour_id=c.tour_id and prior.revision_number<c.revision_number order by prior.revision_number desc,prior.id desc limit 1)
    ) from ops.tour_cheat_sheet_revision c where c.organization_tenant_id=t.organization_tenant_id and c.tour_id=t.id order by c.revision_number desc,c.id desc limit 1),'{}'::jsonb),
    'projections',coalesce((select jsonb_agg(jsonb_build_object('id',p.id,'projection_version',p.projection_version,'route_version',p.route_version,'status',p.status,'as_of',p.as_of,'projection_digest',p.projection_digest) order by p.projection_version desc) from ops.tour_public_projection p where p.organization_tenant_id=t.organization_tenant_id and p.tour_id=t.id),'[]'::jsonb),
    'shares',coalesce((select jsonb_agg(jsonb_build_object(
      'share_grant_id',g.id,'projection_id',g.projection_id,'grant_version',g.grant_version,'permission_scopes',g.permission_scopes,
      'expires_at',g.expires_at,'status',case when r.id is not null then 'revoked' when newer.id is not null then 'rotated' when g.expires_at<=now() then 'expired' else g.status end
    ) order by g.created_at desc,g.id desc)
      from ops.tour_share_grant g
      join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
      left join ops.tour_share_grant_revocation_receipt r on r.organization_tenant_id=g.organization_tenant_id and r.share_grant_id=g.id
      left join ops.tour_share_grant newer on newer.organization_tenant_id=g.organization_tenant_id and newer.rotated_from_grant_id=g.id
      where p.organization_tenant_id=t.organization_tenant_id and p.tour_id=t.id),'[]'::jsonb),
    'pdf_render',coalesce((select jsonb_build_object(
      'render_job_id',j.id,'projection_id',j.projection_id,'status',case when h.decision='accept' then 'available' when h.decision='reject' then 'rejected' else coalesce(r.status,'queued') end,
      'qc_run_digest',r.qc_run_digest,'human_review_state',case when h.decision='accept' then 'accepted' when h.decision='reject' then 'rejected' else 'pending' end
    ) from ops.tour_pdf_render_job j join ops.tour_public_projection p on p.organization_tenant_id=j.organization_tenant_id and p.id=j.projection_id
      left join lateral (select rr.* from ops.tour_pdf_render_result rr where rr.organization_tenant_id=j.organization_tenant_id and rr.render_job_id=j.id order by rr.attempt_count desc,rr.id desc limit 1) r on true
      left join ops.tour_pdf_human_review h on h.organization_tenant_id=j.organization_tenant_id and h.render_job_id=j.id
      where p.organization_tenant_id=t.organization_tenant_id and p.tour_id=t.id
        and p.id=(select current_projection.id from ops.tour_public_projection current_projection
          where current_projection.organization_tenant_id=t.organization_tenant_id and current_projection.tour_id=t.id
            and current_projection.status in ('approved','published')
          order by current_projection.projection_version desc,current_projection.id desc limit 1)
      order by j.created_at desc,j.id desc limit 1),'{}'::jsonb)
  ) from ops.tour t where t.organization_tenant_id=p_tenant and (t.deal_id is null or (p_tenant='carr-internal' and exists(select 1 from public.actor a where (a.id::text=p_actor_id or a.slug=p_actor_id) and a.active))) and t.id=p_tour_id and nullif(btrim(p_actor_id),'') is not null;
$$;
