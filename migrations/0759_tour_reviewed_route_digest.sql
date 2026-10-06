-- Bind Tour acceptance to the draft the operator reviewed.
-- A reviewed route is identified by its immutable routing inputs, all stops
-- (including held/excluded stops), and the exact transition mapping. This
-- digest excludes display enrichment and uses epochs for timezone-independent
-- appointment equality. Read it with the draft; echo it after human review.
create or replace function ops.tour_route_review_digest(p_tenant text,p_route_version_id uuid)
returns text language sql stable security invoker
set search_path=pg_catalog,ops,public,pg_temp as $$
 select 'sha256:' || encode(public.digest(convert_to(jsonb_build_object(
   'schema','tour-route-review.v1', 'tenant',v.organization_tenant_id,
   'route',jsonb_build_object('id',v.id,'tour_id',v.tour_id,'route_version',v.route_version,
     'base_route_version_id',v.base_route_version_id,'start_point',v.start_point,'end_point',v.end_point,
     'routing_source',v.routing_source,'routing_provider',v.routing_provider,
     'routing_policy_key',v.routing_policy_key,'routing_rights_receipt_id',v.routing_rights_receipt_id,
     'routing_request',v.routing_request,'routing_response_digest',v.routing_response_digest),
   'stops',coalesce((select jsonb_agg(jsonb_build_object(
     'id',s.id,'property_id',s.property_id,'route_sequence',s.route_sequence,'route_label',s.route_label,
     'stop_state',s.stop_state,'appointment_start',extract(epoch from s.appointment_start),
     'appointment_end',extract(epoch from s.appointment_end),'locked_appointment',s.locked_appointment,
     'dwell_minutes',s.dwell_minutes,'buffer_minutes',s.buffer_minutes,
     'access_coordinate_status',s.access_coordinate_status,'assertion_set_digest',s.assertion_set_digest
   ) order by s.route_sequence nulls last,s.id) from ops.tour_route_stop s
     where s.organization_tenant_id=v.organization_tenant_id and s.route_version_id=v.id),'[]'::jsonb),
   'transitions',coalesce((select jsonb_agg(jsonb_build_object(
     'id',x.id,'old_route_version_id',x.old_route_version_id,'old_route_stop_id',x.old_route_stop_id,
     'new_route_stop_id',x.new_route_stop_id,'disposition',x.disposition
   ) order by x.id) from ops.tour_route_stop_transition x
     where x.organization_tenant_id=v.organization_tenant_id and x.new_route_version_id=v.id),'[]'::jsonb)
 )::text,'UTF8'),'sha256'),'hex')
 from ops.tour_route_version v where v.organization_tenant_id=p_tenant and v.id=p_route_version_id;
$$;
revoke all on function ops.tour_route_review_digest(text,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create or replace function ops.accept_tour_route_version(p_tenant text, p_route_version_id uuid, p_expected_prior_route_version integer, p_acceptance_digest text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
    AS $_$
declare v_route ops.tour_route_version%rowtype; v_prior ops.tour_route_version_acceptance%rowtype; v_id uuid; v_actor text;
begin
  v_actor:=ops.tour_server_actor_id(); select * into v_route from ops.tour_route_version where id=p_route_version_id and organization_tenant_id=p_tenant for update;
  if not found or p_expected_prior_route_version is null or p_acceptance_digest is null or p_acceptance_digest !~ '^sha256:[a-f0-9]{64}$' then raise exception 'route acceptance payload is invalid'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || v_route.tour_id::text,386));
  select a.* into v_prior from ops.tour_route_version_acceptance a join ops.tour_route_version v on v.id=a.route_version_id and v.organization_tenant_id=a.organization_tenant_id where a.organization_tenant_id=p_tenant and a.tour_id=v_route.tour_id and not exists(select 1 from ops.tour_route_version_acceptance newer where newer.organization_tenant_id=a.organization_tenant_id and newer.supersedes_acceptance_id=a.id) order by a.accepted_at desc,a.id desc limit 1 for update of a;
  if p_expected_prior_route_version<>coalesce((select route_version from ops.tour_route_version where id=v_prior.route_version_id and organization_tenant_id=p_tenant),0) or (v_prior.id is null and v_route.base_route_version_id is not null) or (v_prior.id is not null and v_route.base_route_version_id<>v_prior.route_version_id) then raise exception 'route acceptance refuses concurrent or stale route state'; end if;
  if exists(select 1 from ops.tour_route_version_acceptance where organization_tenant_id=p_tenant and route_version_id=p_route_version_id) then raise exception 'route version is already accepted'; end if;
  if not exists(select 1 from ops.tour_route_stop s where s.organization_tenant_id=p_tenant and s.route_version_id=v_route.id and s.stop_state='active') then raise exception 'route acceptance requires at least one active stop'; end if;
  if exists(select 1 from ops.tour_route_stop s where s.organization_tenant_id=p_tenant and s.route_version_id=v_route.id and not exists(select 1 from ops.tour_route_stop_transition x where x.organization_tenant_id=p_tenant and x.new_route_version_id=v_route.id and x.new_route_stop_id=s.id)) then raise exception 'route acceptance requires an explicit transition for every new route stop'; end if;
  if v_route.routing_source='provider' then
    perform ops.tour_rights_provider_policy_lock(p_tenant,v_route.routing_provider,v_route.routing_policy_key);
    if not exists(select 1 from ops.tour_rights_receipt r where r.id=v_route.routing_rights_receipt_id and r.organization_tenant_id=p_tenant and r.provider=v_route.routing_provider and r.policy_key=v_route.routing_policy_key and r.status='active' and r.revoked_at is null and r.effective_at<=now() and (r.expires_at is null or r.expires_at>now()) and r.allowed_use_classes ? 'route_planning' and not exists(select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.provider=r.provider and newer.policy_key=r.policy_key and newer.receipt_version>r.receipt_version and newer.effective_at<=now())) then raise exception 'provider route cannot become canonical without an exact current provider-policy route-planning rights receipt'; end if;
  end if;
  if v_prior.id is not null and exists(select 1 from ops.tour_route_stop old_stop where old_stop.organization_tenant_id=p_tenant and old_stop.route_version_id=v_prior.route_version_id and not exists(select 1 from ops.tour_route_stop_transition x where x.organization_tenant_id=p_tenant and x.new_route_version_id=v_route.id and x.old_route_stop_id=old_stop.id)) then raise exception 'route acceptance requires an explicit disposition for every prior route stop'; end if;
  if v_prior.id is not null and exists(
    select 1 from ops.tour_route_stop old_stop
    where old_stop.organization_tenant_id=p_tenant and old_stop.route_version_id=v_prior.route_version_id
      and old_stop.locked_appointment
      and not exists(
        select 1 from ops.tour_route_stop_transition x
        join ops.tour_route_stop new_stop on new_stop.organization_tenant_id=x.organization_tenant_id and new_stop.id=x.new_route_stop_id
        where x.organization_tenant_id=p_tenant and x.new_route_version_id=v_route.id and x.old_route_stop_id=old_stop.id
          and new_stop.stop_state='active' and new_stop.property_id=old_stop.property_id and new_stop.locked_appointment
          and new_stop.appointment_start=old_stop.appointment_start and new_stop.appointment_end=old_stop.appointment_end
          and new_stop.dwell_minutes=old_stop.dwell_minutes and new_stop.buffer_minutes=old_stop.buffer_minutes
      )
  ) then raise exception 'route acceptance must preserve every locked appointment window, dwell, and buffer'; end if;
  if exists(select 1 from ops.tour_route_stop s where s.organization_tenant_id=p_tenant and s.route_version_id=v_route.id and s.stop_state='active' and s.assertion_set_digest is null) then raise exception 'route acceptance requires an assertion-set digest for every active stop'; end if;
  if p_acceptance_digest is distinct from ops.tour_route_review_digest(p_tenant,p_route_version_id) then raise exception 'route acceptance refuses changed draft contents'; end if;
  insert into ops.tour_property_membership(organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest)
    select p_tenant,v_route.tour_id,s.property_id,v_route.route_version,s.route_sequence,s.route_label,s.assertion_set_digest from ops.tour_route_stop s where s.organization_tenant_id=p_tenant and s.route_version_id=v_route.id and s.stop_state='active';
  update ops.tour set route_version=v_route.route_version,updated_at=now() where organization_tenant_id=p_tenant and id=v_route.tour_id;
  insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,supersedes_acceptance_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest) values(p_tenant,v_route.tour_id,v_route.id,v_prior.id,p_expected_prior_route_version,v_actor,p_acceptance_digest) returning id into v_id;
  return v_id;
end $_$;


create or replace function ops.read_tour_internal_detail(p_tenant text, p_tour_id uuid, p_actor_id text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
    AS $$
  select jsonb_build_object(
    'id',t.id,'tour_name',t.tour_name,'tour_status',t.tour_status,'route_version',t.route_version,'updated_at',t.updated_at,
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
  ) from ops.tour t where t.organization_tenant_id=p_tenant and t.id=p_tour_id and nullif(btrim(p_actor_id),'') is not null;
$$;


