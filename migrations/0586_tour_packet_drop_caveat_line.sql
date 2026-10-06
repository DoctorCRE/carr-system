-- 0586: stop printing the hard-coded "Facts only; verify current availability
-- and economics." line on client-facing Tour packets.
--
-- Joe's proposed no-caveats rule for client material (rule cbb267fb) rejects
-- this boilerplate. It was injected as the packet-level `caveat` key by two
-- functions in migrations/0430_tour_delivery_data_plane.sql: ops.read_tour_
-- share_packet (line 720) and ops.read_tour_packet_for_render (line 785),
-- both via a literal string inside jsonb_build_object.
--
-- WHAT STAYS. `caveat` remains a legitimate PER-PROPERTY field key backed by
-- a real, reviewed, public tour_field_assertion (display_field_key='caveat')
-- -- an editorial fact about one specific property, no different in kind from
-- availability or parking. Neither function's per-property CTE (`stops` /
-- `properties`) touched the literal; that per-property caveat column is left
-- exactly as it was and continues to surface a genuine assertion when one
-- exists. Only the packet-level DEFAULT -- the literal that was injected
-- whether or not any real caveat fact existed -- is removed, in favor of
-- null. Application code (mcp-server/src/tour-packet-render.js and
-- mcp-server/src/tour-pdf-renderer.js in the same PR) treats a null/absent
-- caveat as "print nothing" instead of falling back to boilerplate.
--
-- Signatures and grants are unchanged; only the function bodies change.

create or replace function ops.read_tour_share_packet(p_session_digest text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with grant_row as (select (ops.tour_share_session_grant(p_session_digest,'view_packet')).*), projection as (
    select p.* from grant_row g join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
    where p.status='approved' and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
  ), stops as (
    select m.route_sequence,m.route_label,'property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32) property_ref,
      max(a.value#>>'{}') filter(where f.display_field_key='display.name') name,
      max(a.value#>>'{}') filter(where f.display_field_key='display.address') address,
      max(a.value#>>'{}') filter(where f.display_field_key='suite') suite,
      max(a.value#>>'{}') filter(where f.display_field_key='property_type') property_type,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='size'))->0 size,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='asking_economics'))->0 asking_economics,
      max(a.value#>>'{}') filter(where f.display_field_key='availability') availability,
      max(a.value#>>'{}') filter(where f.display_field_key='parking') parking,
      max(a.value#>>'{}') filter(where f.display_field_key='caveat') caveat
    from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
    join ops.tour_public_projection_fact f on f.organization_tenant_id=p.organization_tenant_id and f.projection_id=p.id and f.property_id=m.property_id
    join ops.tour_field_assertion a on a.organization_tenant_id=f.organization_tenant_id and a.id=f.field_assertion_id and ops.tour_public_value_safe(a.field_key,a.value)
    group by p.organization_tenant_id,p.id,m.property_id,m.route_sequence,m.route_label
  ) select jsonb_build_object('as_of',p.as_of,'caveat',null,'stops',coalesce((select jsonb_agg(to_jsonb(stops) order by route_sequence) from stops),'[]'::jsonb)) from projection p;
$$;

create or replace function ops.read_tour_packet_for_render(p_tenant text,p_projection_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with projection as (
    select p.*,t.tour_name from ops.tour_public_projection p join ops.tour t on t.organization_tenant_id=p.organization_tenant_id and t.id=p.tour_id
    where p.organization_tenant_id=p_tenant and p.id=p_projection_id and nullif(btrim(p_actor_id),'') is not null and p.status='approved'
      and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
  ), properties as (
    select m.route_sequence,m.route_label,'property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32) property_ref,
      max(a.value#>>'{}') filter(where f.display_field_key='display.name') name,
      max(a.value#>>'{}') filter(where f.display_field_key='display.address') address,
      max(a.value#>>'{}') filter(where f.display_field_key='suite') suite,
      max(a.value#>>'{}') filter(where f.display_field_key='property_type') property_type,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='size'))->0 size,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='asking_economics'))->0 asking_economics,
      max(a.value#>>'{}') filter(where f.display_field_key='availability') availability,
      max(a.value#>>'{}') filter(where f.display_field_key='parking') parking,
      max(a.value#>>'{}') filter(where f.display_field_key='caveat') caveat
    from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
    join ops.tour_public_projection_fact f on f.organization_tenant_id=p.organization_tenant_id and f.projection_id=p.id and f.property_id=m.property_id
    join ops.tour_field_assertion a on a.organization_tenant_id=f.organization_tenant_id and a.id=f.field_assertion_id and ops.tour_public_value_safe(a.field_key,a.value)
    group by p.organization_tenant_id,p.id,m.property_id,m.route_sequence,m.route_label
  ) select jsonb_build_object(
    'projection_digest',p.projection_digest,
    'packet',jsonb_build_object('as_of',p.as_of,'caveat',null,'properties',coalesce((select jsonb_agg(to_jsonb(properties) order by route_sequence) from properties),'[]'::jsonb))
  ) from projection p;
$$;
