-- Tour client feedback. Grant scopes stay narrow; every write is an append-only,
-- projection-bound event, never a mutation of a canonical property or fact.
-- This migration is paired with the next SCAC registry successor.

alter table ops.tour_share_grant drop constraint if exists tour_share_grant_permission_scopes_check;
alter table ops.tour_share_grant add constraint tour_share_grant_permission_scopes_check
  check (jsonb_typeof(permission_scopes)='array' and jsonb_array_length(permission_scopes)>0
    and permission_scopes <@ '["view_packet","view_map","shortlist","comment"]'::jsonb
    and (not (permission_scopes ? 'shortlist' or permission_scopes ? 'comment') or permission_scopes ? 'view_packet'));
alter table ops.tour_share_session drop constraint if exists tour_share_session_permission_scopes_check;
alter table ops.tour_share_session add constraint tour_share_session_permission_scopes_check
  check (jsonb_typeof(permission_scopes)='array' and jsonb_array_length(permission_scopes)>0
    and permission_scopes <@ '["view_packet","view_map","shortlist","comment"]'::jsonb
    and (not (permission_scopes ? 'shortlist' or permission_scopes ? 'comment') or permission_scopes ? 'view_packet'));

create or replace function ops.issue_tour_share_grant(p_tenant text,p_projection_id uuid,p_token_digest text,p_permission_scopes jsonb,p_expires_at timestamptz,p_receipt_digest text,p_actor_id text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_projection ops.tour_public_projection%rowtype;
begin
  if p_token_digest !~ '^sha256:[a-f0-9]{64}$' or p_receipt_digest !~ '^sha256:[a-f0-9]{64}$' or p_expires_at<=now() or jsonb_typeof(p_permission_scopes)<>'array' or not (p_permission_scopes <@ '["view_packet","view_map","shortlist","comment"]'::jsonb) or jsonb_array_length(p_permission_scopes)=0 or ((p_permission_scopes ? 'shortlist' or p_permission_scopes ? 'comment') and not p_permission_scopes ? 'view_packet') or nullif(btrim(p_actor_id),'') is null then raise exception 'tour share payload is invalid'; end if;
  select * into v_projection from ops.tour_public_projection p where p.organization_tenant_id=p_tenant and p.id=p_projection_id and p.status='approved' and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest) and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null and ops.tour_public_projection_client_safe(p.organization_tenant_id,p.id) for update;
  if not found then raise exception 'tour share requires a sealed projection'; end if;
  if p_permission_scopes ? 'view_map' and not ops.tour_public_map_projection_ready(p_tenant,p_projection_id)
  then raise exception 'tour map share requires current rights, sealed entrance coordinates, and an approved promotion receipt'; end if;
  if exists(select 1 from ops.tour_share_grant where organization_tenant_id=p_tenant and projection_id=p_projection_id) then raise exception 'tour share issue requires rotation'; end if;
  insert into ops.tour_share_grant(organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,expires_at,status,receipt_digest,created_by_actor_id)
  values(p_tenant,p_projection_id,1,p_token_digest,'client',p_permission_scopes,p_expires_at,'active',p_receipt_digest,p_actor_id) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.rotate_tour_share_grant(p_tenant text,p_share_grant_id uuid,p_projection_id uuid,p_token_digest text,p_permission_scopes jsonb,p_expires_at timestamptz,p_receipt_digest text,p_actor_id text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_prior ops.tour_share_grant%rowtype; v_projection ops.tour_public_projection%rowtype; v_id uuid;
begin
  select * into v_prior from ops.tour_share_grant where organization_tenant_id=p_tenant and id=p_share_grant_id for update;
  if not found or v_prior.projection_id<>p_projection_id or exists(select 1 from ops.tour_share_grant_revocation_receipt r where r.organization_tenant_id=p_tenant and r.share_grant_id=v_prior.id) or exists(select 1 from ops.tour_share_grant g where g.organization_tenant_id=p_tenant and g.rotated_from_grant_id=v_prior.id) then raise exception 'tour share rotation target is inactive'; end if;
  if p_token_digest !~ '^sha256:[a-f0-9]{64}$' or p_receipt_digest !~ '^sha256:[a-f0-9]{64}$' or p_expires_at<=now() or jsonb_typeof(p_permission_scopes)<>'array' or not (p_permission_scopes <@ '["view_packet","view_map","shortlist","comment"]'::jsonb) or jsonb_array_length(p_permission_scopes)=0 or ((p_permission_scopes ? 'shortlist' or p_permission_scopes ? 'comment') and not p_permission_scopes ? 'view_packet') or nullif(btrim(p_actor_id),'') is null then raise exception 'tour share payload is invalid'; end if;
  select * into v_projection from ops.tour_public_projection where organization_tenant_id=p_tenant and id=p_projection_id;
  if not found or not ops.tour_public_projection_client_safe(p_tenant,p_projection_id) or ops.read_tour_public_projection(p_tenant,p_projection_id) is null then raise exception 'tour share requires a current sealed projection'; end if;
  if p_permission_scopes ? 'view_map' and not ops.tour_public_map_projection_ready(p_tenant,p_projection_id)
  then raise exception 'tour map share requires current rights, sealed entrance coordinates, and an approved promotion receipt'; end if;
  insert into ops.tour_share_grant(organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,rotated_from_grant_id,expires_at,status,receipt_digest,created_by_actor_id)
  values(p_tenant,p_projection_id,v_prior.grant_version+1,p_token_digest,'client',p_permission_scopes,v_prior.id,p_expires_at,'active',p_receipt_digest,p_actor_id) returning id into v_id;
  return v_id;
end $$;


create table ops.tour_share_feedback_event (
  id uuid primary key default gen_random_uuid(),
  event_order bigint generated always as identity unique,
  organization_tenant_id text not null,
  projection_id uuid not null,
  share_grant_id uuid not null,
  property_id uuid not null,
  idempotency_key uuid not null,
  action text not null check (action in ('shortlist','comment')),
  shortlisted boolean,
  comment text,
  payload_digest text not null check (payload_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,share_grant_id,idempotency_key),
  foreign key (organization_tenant_id,projection_id) references ops.tour_public_projection(organization_tenant_id,id),
  foreign key (organization_tenant_id,share_grant_id) references ops.tour_share_grant(organization_tenant_id,id),
  foreign key (organization_tenant_id,property_id) references ops.tour_property(organization_tenant_id,id),
  check ((action='shortlist' and shortlisted is not null and comment is null)
      or (action='comment' and shortlisted is null and comment is not null and char_length(comment) between 1 and 1000))
);
create index tour_share_feedback_projection_idx on ops.tour_share_feedback_event(organization_tenant_id,projection_id,property_id,created_at,id);
create index tour_share_feedback_shortlist_order_idx on ops.tour_share_feedback_event(organization_tenant_id,projection_id,property_id,event_order desc) where action='shortlist';
create trigger tour_share_feedback_append_only before update or delete on ops.tour_share_feedback_event
  for each row execute function ops.tour_reject_mutation();
revoke all on table ops.tour_share_feedback_event from public,carr_reader,carr_writer,carr_jobs,carr_authority;

-- Every client call rechecks the session, grant lineage, projection and member
-- property at statement time. Opaque refs are derived from the sealed projection,
-- so a property ref from another Tour or projection cannot name a valid target.
create or replace function ops.tour_share_feedback_target(p_session_digest text,p_scope text,p_projection_ref text,p_property_ref text)
returns table(organization_tenant_id text,projection_id uuid,share_grant_id uuid,property_id uuid)
language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select g.organization_tenant_id,p.id,g.id,m.property_id
  from (select (ops.tour_share_session_grant(p_session_digest,p_scope)).*) g
  join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
  join ops.tour t on t.organization_tenant_id=p.organization_tenant_id and t.id=p.tour_id
  join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id
    and m.tour_id=p.tour_id and m.route_version=p.route_version
  where p_scope in ('shortlist','comment') and p.status='approved' and p.route_version=t.route_version
    and p_projection_ref='projection:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||p.projection_digest,'sha256'),'hex'),1,32)
    and p_property_ref='property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32)
    and exists(select 1 from ops.tour_public_projection_seal_receipt seal
      where seal.organization_tenant_id=p.organization_tenant_id and seal.projection_id=p.id and seal.canonical_projection_digest=p.projection_digest)
    and not exists(select 1 from ops.tour_public_projection newer where newer.organization_tenant_id=p.organization_tenant_id
      and newer.tour_id=p.tour_id and newer.projection_version>p.projection_version and newer.status in ('approved','published'))
    and ops.tour_public_projection_client_safe(p.organization_tenant_id,p.id)
    and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null;
$$;

create or replace function ops.write_tour_share_feedback(p_session_digest text,p_projection_ref text,p_property_ref text,p_action text,p_shortlisted boolean,p_comment text,p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_target record; v_event ops.tour_share_feedback_event%rowtype; v_payload_digest text;
begin
  if p_session_digest !~ '^sha256:[a-f0-9]{64}$' or p_projection_ref !~ '^projection:public:[a-f0-9]{32}$'
     or p_property_ref !~ '^property:public:[a-f0-9]{32}$' or p_idempotency_key is null
     or p_action not in ('shortlist','comment')
     or (p_action='shortlist' and (p_shortlisted is null or p_comment is not null))
     or (p_action='comment' and (p_shortlisted is not null or p_comment is null or char_length(btrim(p_comment)) not between 1 and 1000 or p_comment ~ '[[:cntrl:]]'))
  then return null; end if;
  select * into v_target from ops.tour_share_feedback_target(p_session_digest,p_action,p_projection_ref,p_property_ref);
  if not found then return null; end if;
  v_payload_digest:='sha256:'||encode(public.digest(convert_to(p_action||':'||p_property_ref||':'||coalesce(p_shortlisted::text,p_comment),'UTF8'),'sha256'),'hex');
  insert into ops.tour_share_feedback_event(organization_tenant_id,projection_id,share_grant_id,property_id,idempotency_key,action,shortlisted,comment,payload_digest)
  values(v_target.organization_tenant_id,v_target.projection_id,v_target.share_grant_id,v_target.property_id,p_idempotency_key,p_action,p_shortlisted,p_comment,v_payload_digest)
  on conflict (organization_tenant_id,share_grant_id,idempotency_key) do nothing
  returning * into v_event;
  if not found then
    select * into v_event from ops.tour_share_feedback_event where organization_tenant_id=v_target.organization_tenant_id
      and share_grant_id=v_target.share_grant_id and idempotency_key=p_idempotency_key;
    if not found or v_event.payload_digest is distinct from v_payload_digest then return null; end if;
  end if;
  return jsonb_build_object('saved',true);
end $$;

create or replace function ops.write_tour_share_shortlist(p_session_digest text,p_projection_ref text,p_property_ref text,p_shortlisted boolean,p_idempotency_key uuid)
returns jsonb language sql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select ops.write_tour_share_feedback(p_session_digest,p_projection_ref,p_property_ref,'shortlist',p_shortlisted,null,p_idempotency_key);
$$;
create or replace function ops.write_tour_share_comment(p_session_digest text,p_projection_ref text,p_property_ref text,p_comment text,p_idempotency_key uuid)
returns jsonb language sql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select ops.write_tour_share_feedback(p_session_digest,p_projection_ref,p_property_ref,'comment',null,p_comment,p_idempotency_key);
$$;

create or replace function ops.read_tour_share_feedback(p_session_digest text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with g as (select grant_row.* from ops.tour_share_session_grant(p_session_digest,'shortlist') grant_row where grant_row.id is not null
            union all select grant_row.* from ops.tour_share_session_grant(p_session_digest,'comment') grant_row where grant_row.id is not null limit 1),
  p as (select p.* ,g.id grant_id,g.permission_scopes from g join ops.tour_public_projection p
       on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
       join ops.tour t on t.organization_tenant_id=p.organization_tenant_id and t.id=p.tour_id
       where p.status='approved' and p.route_version=t.route_version
         and exists(select 1 from ops.tour_public_projection_seal_receipt seal
           where seal.organization_tenant_id=p.organization_tenant_id and seal.projection_id=p.id and seal.canonical_projection_digest=p.projection_digest)
         and not exists(select 1 from ops.tour_public_projection newer where newer.organization_tenant_id=p.organization_tenant_id
           and newer.tour_id=p.tour_id and newer.projection_version>p.projection_version and newer.status in ('approved','published'))
         and ops.tour_public_projection_client_safe(p.organization_tenant_id,p.id)
         and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null),
  items as (select p.organization_tenant_id,p.id projection_id,m.property_id,
    'property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32) property_ref
    from p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version)
  select jsonb_build_object('projection_ref','projection:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||p.projection_digest,'sha256'),'hex'),1,32),
    'permission_scopes',p.permission_scopes,
    'items',coalesce((select jsonb_agg(jsonb_build_object('property_ref',i.property_ref) order by i.property_ref) from items i),'[]'::jsonb)) from p;
$$;

create or replace function ops.read_tour_feedback(p_tenant text,p_projection_id uuid,p_actor_id text,p_cursor text,p_limit integer)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with authorized as (select p.* from ops.tour_public_projection p where p.organization_tenant_id=p_tenant and p.id=p_projection_id
    and nullif(btrim(p_actor_id),'') is not null and (p_cursor is null or p_cursor ~ '^[0-9]{1,9}$') and p_limit between 1 and 100),
  members as (select m.property_id,m.route_sequence,m.route_label,p.organization_tenant_id,p.id projection_id
    from authorized p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
    order by m.route_sequence limit p_limit offset coalesce(p_cursor::integer,0))
  select jsonb_build_object('projection_id',p.id,'items',coalesce((select jsonb_agg(jsonb_build_object(
    'property_ref','property:public:'||substr(encode(public.digest(m.organization_tenant_id||':'||m.projection_id::text||':'||m.property_id::text,'sha256'),'hex'),1,32),
    'route_label',m.route_label,
    'shortlisted',(select e.shortlisted from ops.tour_share_feedback_event e where e.organization_tenant_id=m.organization_tenant_id and e.projection_id=m.projection_id and e.property_id=m.property_id and e.action='shortlist' order by e.event_order desc limit 1),
    'comments',coalesce((select jsonb_agg(jsonb_build_object('comment_ref','comment:public:'||substr(encode(public.digest(e.id::text,'sha256'),'hex'),1,32),'comment',e.comment,'created_at',e.created_at) order by e.event_order)
      from ops.tour_share_feedback_event e where e.organization_tenant_id=m.organization_tenant_id and e.projection_id=m.projection_id and e.property_id=m.property_id and e.action='comment'),'[]'::jsonb)) order by m.route_sequence) from members m),'[]'::jsonb)) from authorized p;
$$;

revoke all on function ops.tour_share_feedback_target(text,text,text,text),
  ops.write_tour_share_feedback(text,text,text,text,boolean,text,uuid),
  ops.write_tour_share_shortlist(text,text,text,boolean,uuid),
  ops.write_tour_share_comment(text,text,text,text,uuid),
  ops.read_tour_share_feedback(text),ops.read_tour_feedback(text,uuid,text,text,integer)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.write_tour_share_shortlist(text,text,text,boolean,uuid),
  ops.write_tour_share_comment(text,text,text,text,uuid),ops.read_tour_share_feedback(text) to carr_writer;
grant execute on function ops.read_tour_feedback(text,uuid,text,text,integer) to carr_writer;
-- Nested SECURITY DEFINER calls need EXECUTE for the caller role as well.
grant execute on function ops.tour_share_feedback_target(text,text,text,text),
  ops.write_tour_share_feedback(text,text,text,text,boolean,text,uuid) to carr_writer;
