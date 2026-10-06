-- 0429: additive immutable Tour domain, route, and internal notebook slice.
begin;

alter table ops.tour add column if not exists subject_type text;
alter table ops.tour add column if not exists subject_id text;
alter table ops.tour add column if not exists subject_bound_at timestamptz;
alter table ops.tour drop constraint if exists tour_subject_binding_valid;
alter table ops.tour add constraint tour_subject_binding_valid check (
  (subject_type is null and subject_id is null and subject_bound_at is null) or
  (subject_type in ('client','work') and subject_id ~ '^[A-Za-z0-9._:-]{1,200}$' and subject_bound_at is not null)
);

create table if not exists ops.tour_route_version (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_id uuid not null,
  route_version integer not null check (route_version>0), base_route_version_id uuid,
  start_point jsonb not null, end_point jsonb not null,
  routing_source text not null check (routing_source in ('manual','provider')), routing_provider text,
  routing_rights_receipt_id uuid, routing_request jsonb not null default '{}'::jsonb,
  routing_response_digest text, created_by_actor_id text not null, created_at timestamptz not null default now(),
  unique (organization_tenant_id,id), unique (organization_tenant_id,tour_id,route_version),
  foreign key (organization_tenant_id,tour_id) references ops.tour(organization_tenant_id,id),
  foreign key (organization_tenant_id,base_route_version_id) references ops.tour_route_version(organization_tenant_id,id),
  foreign key (organization_tenant_id,routing_rights_receipt_id) references ops.tour_rights_receipt(organization_tenant_id,id),
  check (jsonb_typeof(start_point)='object' and jsonb_typeof(end_point)='object'),
  check ((routing_source='manual' and routing_provider is null and routing_rights_receipt_id is null) or
         (routing_source='provider' and routing_provider ~ '^[A-Za-z0-9._:-]{1,120}$' and routing_rights_receipt_id is not null)),
  check (routing_response_digest is null or routing_response_digest ~ '^sha256:[a-f0-9]{64}$')
);

create table if not exists ops.tour_route_stop (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, route_version_id uuid not null, property_id uuid not null,
  route_sequence integer, route_label text, stop_state text not null check (stop_state in ('active','held','excluded')),
  appointment_start timestamptz, appointment_end timestamptz, locked_appointment boolean not null default false,
  dwell_minutes integer not null default 0 check (dwell_minutes between 0 and 1440), buffer_minutes integer not null default 0 check (buffer_minutes between 0 and 1440),
  access_coordinate_status text not null default 'unknown' check (access_coordinate_status in ('unknown','candidate','approved','excluded')),
  created_by_actor_id text not null, created_at timestamptz not null default now(), unique (organization_tenant_id,id),
  foreign key (organization_tenant_id,route_version_id) references ops.tour_route_version(organization_tenant_id,id),
  foreign key (organization_tenant_id,property_id) references ops.tour_property(organization_tenant_id,id),
  check ((stop_state='active' and route_sequence>0 and route_label ~ '^[A-Za-z0-9._ -]{1,80}$') or
         (stop_state in ('held','excluded') and route_sequence is null and route_label is null)),
  check ((appointment_start is null and appointment_end is null and not locked_appointment) or
         (appointment_start is not null and appointment_end is not null and appointment_end>=appointment_start))
);
create unique index if not exists tour_route_stop_active_sequence_unique on ops.tour_route_stop(organization_tenant_id,route_version_id,route_sequence) where stop_state='active';
create unique index if not exists tour_route_stop_active_label_unique on ops.tour_route_stop(organization_tenant_id,route_version_id,route_label) where stop_state='active';

create table if not exists ops.tour_route_stop_transition (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  old_route_version_id uuid, new_route_version_id uuid not null, old_route_stop_id uuid, new_route_stop_id uuid,
  old_route_sequence integer, new_route_sequence integer,
  disposition text not null check (disposition in ('unchanged','reordered','removed','held','excluded','merged','added')),
  created_by_actor_id text not null, created_at timestamptz not null default now(), unique (organization_tenant_id,id),
  unique (organization_tenant_id,new_route_version_id,old_route_stop_id),
  foreign key (organization_tenant_id,old_route_version_id) references ops.tour_route_version(organization_tenant_id,id),
  foreign key (organization_tenant_id,new_route_version_id) references ops.tour_route_version(organization_tenant_id,id),
  foreign key (organization_tenant_id,old_route_stop_id) references ops.tour_route_stop(organization_tenant_id,id),
  foreign key (organization_tenant_id,new_route_stop_id) references ops.tour_route_stop(organization_tenant_id,id),
  check ((disposition='added' and old_route_version_id is null and old_route_stop_id is null and new_route_stop_id is not null) or
         (disposition in ('removed','held','excluded') and old_route_version_id is not null and old_route_stop_id is not null) or
         (disposition in ('unchanged','reordered','merged') and old_route_version_id is not null and old_route_stop_id is not null and new_route_stop_id is not null))
);

create table if not exists ops.tour_route_version_acceptance (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_id uuid not null, route_version_id uuid not null,
  supersedes_acceptance_id uuid, expected_prior_route_version integer not null check (expected_prior_route_version>=0),
  accepted_by_actor_id text not null, accepted_at timestamptz not null default now(), acceptance_digest text not null check (acceptance_digest ~ '^sha256:[a-f0-9]{64}$'),
  unique (organization_tenant_id,id), unique (organization_tenant_id,route_version_id), unique (organization_tenant_id,supersedes_acceptance_id),
  foreign key (organization_tenant_id,tour_id) references ops.tour(organization_tenant_id,id),
  foreign key (organization_tenant_id,route_version_id) references ops.tour_route_version(organization_tenant_id,id),
  foreign key (organization_tenant_id,supersedes_acceptance_id) references ops.tour_route_version_acceptance(organization_tenant_id,id)
);

alter table ops.tour_cheat_sheet_revision add column if not exists revision_kind text not null default 'autosave';
alter table ops.tour_cheat_sheet_revision add column if not exists parent_revision_id uuid;
alter table ops.tour_cheat_sheet_revision add column if not exists restored_from_revision_id uuid;
alter table ops.tour_cheat_sheet_revision add column if not exists content_digest text;
alter table ops.tour_cheat_sheet_revision drop constraint if exists tour_cheat_sheet_revision_kind_valid;
alter table ops.tour_cheat_sheet_revision add constraint tour_cheat_sheet_revision_kind_valid check (revision_kind in ('autosave','restore'));
alter table ops.tour_cheat_sheet_revision drop constraint if exists tour_cheat_sheet_content_digest_valid;
alter table ops.tour_cheat_sheet_revision add constraint tour_cheat_sheet_content_digest_valid check (content_digest is null or content_digest ~ '^sha256:[a-f0-9]{64}$');
alter table ops.tour_cheat_sheet_revision drop constraint if exists tour_cheat_sheet_parent_tenant_fk;
alter table ops.tour_cheat_sheet_revision add constraint tour_cheat_sheet_parent_tenant_fk foreign key (organization_tenant_id,parent_revision_id) references ops.tour_cheat_sheet_revision(organization_tenant_id,id);
alter table ops.tour_cheat_sheet_revision drop constraint if exists tour_cheat_sheet_restore_tenant_fk;
alter table ops.tour_cheat_sheet_revision add constraint tour_cheat_sheet_restore_tenant_fk foreign key (organization_tenant_id,restored_from_revision_id) references ops.tour_cheat_sheet_revision(organization_tenant_id,id);

create or replace function ops.tour_server_actor_id() returns text language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_actor text;
begin
  if session_user ~ '^carr_authority_' then v_actor:=regexp_replace(session_user,'^carr_authority_','');
  elsif session_user='carr_writer' then v_actor:=nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  else raise exception 'tour mutation requires an authority connection or sponsored writer session'; end if;
  if v_actor is null or v_actor !~ '^[A-Za-z0-9._:-]{1,160}$' then raise exception 'tour mutation has no server-derived actor'; end if;
  return v_actor;
end $$;

create or replace function ops.create_tour_domain(p_tenant text,p_tour_name text,p_subject_type text,p_subject_id text,p_canonical_dataset_version text,p_start_point jsonb,p_end_point jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_actor text;
begin
  v_actor:=ops.tour_server_actor_id();
  if p_tenant is null or p_tour_name is null or p_tour_name !~ '^.{1,240}$' or p_subject_type not in ('client','work') or p_subject_id is null or p_subject_id !~ '^[A-Za-z0-9._:-]{1,200}$' or p_canonical_dataset_version is null or p_canonical_dataset_version !~ '^.{1,240}$' or jsonb_typeof(p_start_point)<>'object' or jsonb_typeof(p_end_point)<>'object' then raise exception 'tour subject binding is invalid'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || p_subject_type || ':' || p_subject_id,386));
  insert into ops.tour(organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version,subject_type,subject_id,subject_bound_at) values(p_tenant,p_tour_name,'draft',1,p_canonical_dataset_version,p_subject_type,p_subject_id,now()) returning id into v_id;
  insert into ops.tour_route_version(organization_tenant_id,tour_id,route_version,start_point,end_point,routing_source,routing_request,created_by_actor_id) values(p_tenant,v_id,1,p_start_point,p_end_point,'manual','{}',v_actor);
  return v_id;
end $$;

create or replace function ops.append_tour_route_version(p_tenant text,p_tour_id uuid,p_route_version integer,p_base_route_version_id uuid,p_start_point jsonb,p_end_point jsonb,p_routing_source text,p_routing_provider text,p_routing_rights_receipt_id uuid,p_routing_request jsonb,p_routing_response_digest text,p_expected_route_version integer)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_actor text; v_latest integer; v_accepted integer;
begin
  v_actor:=ops.tour_server_actor_id();
  if p_tenant is null or p_tour_id is null or p_route_version is null or p_expected_route_version is null or jsonb_typeof(p_start_point)<>'object' or jsonb_typeof(p_end_point)<>'object' or jsonb_typeof(p_routing_request)<>'object' or p_routing_source not in ('manual','provider') then raise exception 'route version payload is invalid'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || p_tour_id::text,386));
  select route_version into v_accepted from ops.tour where organization_tenant_id=p_tenant and id=p_tour_id for update;
  if not found then raise exception 'route version tour is unavailable'; end if;
  select coalesce(max(route_version),0) into v_latest from ops.tour_route_version where organization_tenant_id=p_tenant and tour_id=p_tour_id;
  if p_expected_route_version<>v_accepted or p_route_version<>v_latest+1 then raise exception 'route version refuses concurrent or stale route state'; end if;
  if p_base_route_version_id is null or not exists(
    select 1 from ops.tour_route_version v
     where v.organization_tenant_id=p_tenant and v.id=p_base_route_version_id and v.tour_id=p_tour_id
       and v.route_version=v_accepted
       and exists(select 1 from ops.tour_route_version_acceptance a where a.organization_tenant_id=p_tenant and a.route_version_id=v.id)
  ) then raise exception 'route version base is invalid'; end if;
  if (p_routing_source='manual' and (p_routing_provider is not null or p_routing_rights_receipt_id is not null)) or (p_routing_source='provider' and (p_routing_provider is null or p_routing_provider !~ '^[A-Za-z0-9._:-]{1,120}$' or p_routing_rights_receipt_id is null)) then raise exception 'route provider projection is invalid'; end if;
  insert into ops.tour_route_version(organization_tenant_id,tour_id,route_version,base_route_version_id,start_point,end_point,routing_source,routing_provider,routing_rights_receipt_id,routing_request,routing_response_digest,created_by_actor_id) values(p_tenant,p_tour_id,p_route_version,p_base_route_version_id,p_start_point,p_end_point,p_routing_source,p_routing_provider,p_routing_rights_receipt_id,p_routing_request,p_routing_response_digest,v_actor) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.append_tour_route_stop(p_tenant text,p_route_version_id uuid,p_property_id uuid,p_route_sequence integer,p_route_label text,p_stop_state text,p_appointment_start timestamptz,p_appointment_end timestamptz,p_locked_appointment boolean,p_dwell_minutes integer,p_buffer_minutes integer,p_access_coordinate_status text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_actor text; v_tour uuid;
begin
  v_actor:=ops.tour_server_actor_id(); select tour_id into v_tour from ops.tour_route_version where id=p_route_version_id and organization_tenant_id=p_tenant for update;
  if not found then raise exception 'route stop route version is unavailable'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || v_tour::text,386));
  if exists(select 1 from ops.tour_route_version_acceptance where organization_tenant_id=p_tenant and route_version_id=p_route_version_id) then raise exception 'route stop cannot alter an accepted route version'; end if;
  insert into ops.tour_route_stop(organization_tenant_id,route_version_id,property_id,route_sequence,route_label,stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,buffer_minutes,access_coordinate_status,created_by_actor_id) values(p_tenant,p_route_version_id,p_property_id,p_route_sequence,p_route_label,p_stop_state,p_appointment_start,p_appointment_end,coalesce(p_locked_appointment,false),coalesce(p_dwell_minutes,0),coalesce(p_buffer_minutes,0),coalesce(p_access_coordinate_status,'unknown'),v_actor) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.append_tour_route_stop_transition(p_tenant text,p_old_route_version_id uuid,p_new_route_version_id uuid,p_old_route_stop_id uuid,p_new_route_stop_id uuid,p_disposition text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_actor text; v_old_tour uuid; v_new_tour uuid; v_old_seq integer; v_new_seq integer; v_old_property uuid; v_new_property uuid;
begin
  v_actor:=ops.tour_server_actor_id(); select tour_id into v_new_tour from ops.tour_route_version where id=p_new_route_version_id and organization_tenant_id=p_tenant for update;
  if not found then raise exception 'route transition new version is unavailable'; end if;
  if p_old_route_version_id is not null then select tour_id into v_old_tour from ops.tour_route_version where id=p_old_route_version_id and organization_tenant_id=p_tenant; end if;
  if p_disposition='added' then if p_old_route_version_id is not null or p_old_route_stop_id is not null or p_new_route_stop_id is null then raise exception 'added route transition is invalid'; end if;
  elsif v_old_tour is distinct from v_new_tour or p_old_route_stop_id is null then raise exception 'route transition versions must belong to one tour'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || v_new_tour::text,386));
  if exists(select 1 from ops.tour_route_version_acceptance where organization_tenant_id=p_tenant and route_version_id=p_new_route_version_id) then raise exception 'route transition cannot alter an accepted route version'; end if;
  select property_id,route_sequence into v_old_property,v_old_seq from ops.tour_route_stop where id=p_old_route_stop_id and organization_tenant_id=p_tenant and route_version_id=p_old_route_version_id; if p_old_route_stop_id is not null and not found then raise exception 'route transition old stop is unavailable'; end if;
  select property_id,route_sequence into v_new_property,v_new_seq from ops.tour_route_stop where id=p_new_route_stop_id and organization_tenant_id=p_tenant and route_version_id=p_new_route_version_id; if p_new_route_stop_id is not null and not found then raise exception 'route transition new stop is unavailable'; end if;
  if (p_disposition in ('unchanged','reordered') or (p_disposition in ('held','excluded') and p_new_route_stop_id is not null)) and v_old_property is distinct from v_new_property then raise exception 'route transition property identity mismatch'; end if;
  if p_disposition='unchanged' and v_old_seq is distinct from v_new_seq then raise exception 'unchanged route transition requires the same sequence'; end if;
  if p_disposition='reordered' and v_old_seq is not distinct from v_new_seq then raise exception 'reordered route transition requires a sequence change'; end if;
  if p_disposition='merged' and (v_old_property is not distinct from v_new_property or not exists(
    select 1 from ops.tour_property_identity_lineage l where l.organization_tenant_id=p_tenant
      and l.predecessor_property_id=v_old_property and l.successor_property_id=v_new_property
      and l.relationship in ('merged_into','duplicate_of','successor_of')
  )) then raise exception 'merged route transition requires explicit property identity lineage'; end if;
  insert into ops.tour_route_stop_transition(organization_tenant_id,old_route_version_id,new_route_version_id,old_route_stop_id,new_route_stop_id,old_route_sequence,new_route_sequence,disposition,created_by_actor_id) values(p_tenant,p_old_route_version_id,p_new_route_version_id,p_old_route_stop_id,p_new_route_stop_id,v_old_seq,v_new_seq,p_disposition,v_actor) returning id into v_id; return v_id;
end $$;

create or replace function ops.accept_tour_route_version(p_tenant text,p_route_version_id uuid,p_expected_prior_route_version integer,p_acceptance_digest text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
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
  if v_route.routing_source='provider' and not exists(select 1 from ops.tour_rights_receipt r where r.id=v_route.routing_rights_receipt_id and r.organization_tenant_id=p_tenant and r.status='active' and r.revoked_at is null and r.effective_at<=now() and (r.expires_at is null or r.expires_at>now()) and r.allowed_use_classes ? 'route_planning') then raise exception 'provider route cannot become canonical without an active route-planning rights receipt'; end if;
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
  insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,supersedes_acceptance_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest) values(p_tenant,v_route.tour_id,v_route.id,v_prior.id,p_expected_prior_route_version,v_actor,p_acceptance_digest) returning id into v_id; return v_id;
end $$;

create or replace function ops.append_tour_cheat_sheet_revision(p_tenant text,p_tour_id uuid,p_content jsonb,p_expected_revision_number integer)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_prior ops.tour_cheat_sheet_revision%rowtype; v_id uuid; v_actor text;
begin
  v_actor:=ops.tour_server_actor_id(); if p_tenant is null or p_tour_id is null or jsonb_typeof(p_content)<>'object' or p_expected_revision_number is null then raise exception 'tour cheat sheet content must be an object'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || p_tour_id::text || ':cheat-sheet',386)); select * into v_prior from ops.tour_cheat_sheet_revision where organization_tenant_id=p_tenant and tour_id=p_tour_id order by revision_number desc,id desc limit 1 for update;
  if p_expected_revision_number<>coalesce(v_prior.revision_number,0) then raise exception 'cheat sheet revision refuses concurrent or stale version'; end if;
  insert into ops.tour_cheat_sheet_revision(organization_tenant_id,tour_id,revision_number,content,editor_actor_id,status,revision_kind,parent_revision_id,content_digest) values(p_tenant,p_tour_id,coalesce(v_prior.revision_number,0)+1,p_content,v_actor,'draft','autosave',v_prior.id,'sha256:'||encode(public.digest(p_content::text,'sha256'),'hex')) returning id into v_id; return v_id;
end $$;

create or replace function ops.restore_tour_cheat_sheet_revision(p_tenant text,p_tour_id uuid,p_restore_revision_id uuid,p_expected_revision_number integer)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_source ops.tour_cheat_sheet_revision%rowtype; v_prior ops.tour_cheat_sheet_revision%rowtype; v_id uuid; v_actor text;
begin
  v_actor:=ops.tour_server_actor_id(); perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || p_tour_id::text || ':cheat-sheet',386));
  select * into v_source from ops.tour_cheat_sheet_revision where id=p_restore_revision_id and organization_tenant_id=p_tenant and tour_id=p_tour_id;
  select * into v_prior from ops.tour_cheat_sheet_revision where organization_tenant_id=p_tenant and tour_id=p_tour_id order by revision_number desc,id desc limit 1 for update;
  if v_source.id is null or p_expected_revision_number<>coalesce(v_prior.revision_number,0) then raise exception 'cheat sheet restore refuses unavailable or stale revision'; end if;
  -- Restore creates a new cheat sheet revision; it never rewrites the source.
  insert into ops.tour_cheat_sheet_revision(organization_tenant_id,tour_id,revision_number,content,editor_actor_id,status,revision_kind,parent_revision_id,restored_from_revision_id,content_digest) values(p_tenant,p_tour_id,v_prior.revision_number+1,v_source.content,v_actor,'draft','restore',v_prior.id,v_source.id,'sha256:'||encode(public.digest(v_source.content::text,'sha256'),'hex')) returning id into v_id; return v_id;
end $$;

create or replace function ops.tour_route_append_only_guard() returns trigger language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$ begin raise exception '% is append-only',tg_table_name; end $$;
-- ops.tour_route_version is append-only.
drop trigger if exists tour_route_version_append_only on ops.tour_route_version;
create trigger tour_route_version_append_only before update or delete on ops.tour_route_version for each row execute function ops.tour_route_append_only_guard();

-- ops.tour_route_stop is append-only.
drop trigger if exists tour_route_stop_append_only on ops.tour_route_stop;
create trigger tour_route_stop_append_only before update or delete on ops.tour_route_stop for each row execute function ops.tour_route_append_only_guard();

-- ops.tour_route_stop_transition is append-only.
drop trigger if exists tour_route_stop_transition_append_only on ops.tour_route_stop_transition;
create trigger tour_route_stop_transition_append_only before update or delete on ops.tour_route_stop_transition for each row execute function ops.tour_route_append_only_guard();

-- ops.tour_route_version_acceptance is append-only.
drop trigger if exists tour_route_version_acceptance_append_only on ops.tour_route_version_acceptance;
create trigger tour_route_version_acceptance_append_only before update or delete on ops.tour_route_version_acceptance for each row execute function ops.tour_route_append_only_guard();


-- Cheat sheets are internal-only; read_tour_public_projection does not join cheat-sheet content.
comment on table ops.tour_cheat_sheet_revision is 'Internal-only append-only notebook revisions; no public projection joins this content.';
revoke all on table ops.tour_route_version,ops.tour_route_stop,ops.tour_route_stop_transition,ops.tour_route_version_acceptance,ops.tour_cheat_sheet_revision from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.tour_server_actor_id(),ops.tour_route_append_only_guard(),ops.create_tour_domain(text,text,text,text,text,jsonb,jsonb),ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer),ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamp with time zone,timestamp with time zone,boolean,integer,integer,text),ops.append_tour_route_stop_transition(text,uuid,uuid,uuid,uuid,text),ops.accept_tour_route_version(text,uuid,integer,text),ops.append_tour_cheat_sheet_revision(text,uuid,jsonb,integer),ops.restore_tour_cheat_sheet_revision(text,uuid,uuid,integer) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.create_tour_domain(text,text,text,text,text,jsonb,jsonb),ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer),ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamp with time zone,timestamp with time zone,boolean,integer,integer,text),ops.append_tour_route_stop_transition(text,uuid,uuid,uuid,uuid,text),ops.append_tour_cheat_sheet_revision(text,uuid,jsonb,integer),ops.restore_tour_cheat_sheet_revision(text,uuid,uuid,integer) to carr_writer,carr_authority;
grant execute on function ops.accept_tour_route_version(text,uuid,integer,text) to carr_authority;
grant execute on function ops.append_tour_cheat_sheet_revision(text,uuid,jsonb,integer) to carr_writer;

-- Second-pass canonical bridge.  Existing tour_property_membership is the
-- normalized source for 0427 projections, so only accepted active stops enter it.
alter table ops.tour_route_version add column if not exists routing_policy_key text;
alter table ops.tour_route_stop add column if not exists assertion_set_digest text;
alter table ops.tour_route_stop alter column assertion_set_digest set not null;
alter table ops.tour_route_stop drop constraint if exists tour_route_stop_assertion_digest_valid;
alter table ops.tour_route_stop add constraint tour_route_stop_assertion_digest_valid check (assertion_set_digest ~ '^sha256:[a-f0-9]{64}$');
alter table ops.tour_route_stop drop constraint if exists tour_route_stop_property_once;
alter table ops.tour_route_stop add constraint tour_route_stop_property_once unique (organization_tenant_id,route_version_id,property_id);

create or replace function ops.tour_server_actor_id() returns text language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_actor text;
begin
  if session_user ~ '^carr_authority_' then v_actor:=regexp_replace(session_user,'^carr_authority_','');
  elsif session_user in ('carr_writer','carr_authority') then v_actor:=nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  else raise exception 'tour mutation requires an authority connection or sponsored writer session'; end if;
  if v_actor is null or v_actor !~ '^[A-Za-z0-9._:-]{1,160}$' then raise exception 'tour mutation has no server-derived actor'; end if;
  return v_actor;
end $$;

create or replace function ops.append_tour_route_version(
  p_tenant text,p_tour_id uuid,p_route_version integer,p_base_route_version_id uuid,p_start_point jsonb,p_end_point jsonb,
  p_routing_source text,p_routing_provider text,p_routing_rights_receipt_id uuid,p_routing_request jsonb,p_routing_response_digest text,
  p_expected_route_version integer,p_routing_policy_key text
) returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_actor text; v_latest integer; v_accepted integer;
begin
  v_actor:=ops.tour_server_actor_id();
  if p_tenant is null or p_tour_id is null or p_route_version is null or p_expected_route_version is null or jsonb_typeof(p_start_point)<>'object' or jsonb_typeof(p_end_point)<>'object' or jsonb_typeof(p_routing_request)<>'object' or p_routing_source not in ('manual','provider') then raise exception 'route version payload is invalid'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || p_tour_id::text,386));
  select route_version into v_accepted from ops.tour where organization_tenant_id=p_tenant and id=p_tour_id for update;
  if not found then raise exception 'route version tour is unavailable'; end if;
  select coalesce(max(route_version),0) into v_latest from ops.tour_route_version where organization_tenant_id=p_tenant and tour_id=p_tour_id;
  if p_expected_route_version<>v_accepted or p_route_version<>v_latest+1 then raise exception 'route version refuses concurrent or stale route state'; end if;
  if p_base_route_version_id is null or not exists(
    select 1 from ops.tour_route_version v
     where v.organization_tenant_id=p_tenant and v.id=p_base_route_version_id and v.tour_id=p_tour_id
       and v.route_version=v_accepted
       and exists(select 1 from ops.tour_route_version_acceptance a where a.organization_tenant_id=p_tenant and a.route_version_id=v.id)
  ) then raise exception 'route version base is invalid'; end if;
  if (p_routing_source='manual' and (p_routing_provider is not null or p_routing_policy_key is not null or p_routing_rights_receipt_id is not null)) or (p_routing_source='provider' and (p_routing_provider is null or p_routing_provider !~ '^[A-Za-z0-9._:-]{1,120}$' or p_routing_policy_key is null or p_routing_policy_key !~ '^[A-Za-z0-9._:-]{1,160}$' or p_routing_rights_receipt_id is null)) then raise exception 'route provider projection is invalid'; end if;
  insert into ops.tour_route_version(organization_tenant_id,tour_id,route_version,base_route_version_id,start_point,end_point,routing_source,routing_provider,routing_policy_key,routing_rights_receipt_id,routing_request,routing_response_digest,created_by_actor_id) values(p_tenant,p_tour_id,p_route_version,p_base_route_version_id,p_start_point,p_end_point,p_routing_source,p_routing_provider,p_routing_policy_key,p_routing_rights_receipt_id,p_routing_request,p_routing_response_digest,v_actor) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.append_tour_route_stop(
  p_tenant text,p_route_version_id uuid,p_property_id uuid,p_route_sequence integer,p_route_label text,p_stop_state text,
  p_appointment_start timestamptz,p_appointment_end timestamptz,p_locked_appointment boolean,p_dwell_minutes integer,p_buffer_minutes integer,
  p_access_coordinate_status text,p_assertion_set_digest text
) returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_actor text; v_tour uuid;
begin
  v_actor:=ops.tour_server_actor_id(); select tour_id into v_tour from ops.tour_route_version where id=p_route_version_id and organization_tenant_id=p_tenant for update;
  if not found or p_assertion_set_digest is null or p_assertion_set_digest !~ '^sha256:[a-f0-9]{64}$' then raise exception 'route stop payload is invalid'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant || ':' || v_tour::text,386));
  if exists(select 1 from ops.tour_route_version_acceptance where organization_tenant_id=p_tenant and route_version_id=p_route_version_id) then raise exception 'route stop cannot alter an accepted route version'; end if;
  insert into ops.tour_route_stop(organization_tenant_id,route_version_id,property_id,route_sequence,route_label,stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,buffer_minutes,access_coordinate_status,assertion_set_digest,created_by_actor_id) values(p_tenant,p_route_version_id,p_property_id,p_route_sequence,p_route_label,p_stop_state,p_appointment_start,p_appointment_end,coalesce(p_locked_appointment,false),coalesce(p_dwell_minutes,0),coalesce(p_buffer_minutes,0),coalesce(p_access_coordinate_status,'unknown'),p_assertion_set_digest,v_actor) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.accept_tour_route_version(p_tenant text,p_route_version_id uuid,p_expected_prior_route_version integer,p_acceptance_digest text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
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
  insert into ops.tour_property_membership(organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest)
    select p_tenant,v_route.tour_id,s.property_id,v_route.route_version,s.route_sequence,s.route_label,s.assertion_set_digest from ops.tour_route_stop s where s.organization_tenant_id=p_tenant and s.route_version_id=v_route.id and s.stop_state='active';
  update ops.tour set route_version=v_route.route_version,updated_at=now() where organization_tenant_id=p_tenant and id=v_route.tour_id;
  insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,supersedes_acceptance_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest) values(p_tenant,v_route.tour_id,v_route.id,v_prior.id,p_expected_prior_route_version,v_actor,p_acceptance_digest) returning id into v_id;
  return v_id;
end $$;

revoke all on function ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer),ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamp with time zone,timestamp with time zone,boolean,integer,integer,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text),ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamp with time zone,timestamp with time zone,boolean,integer,integer,text,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text),ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamp with time zone,timestamp with time zone,boolean,integer,integer,text,text) to carr_writer,carr_authority;
grant select on ops.tour,ops.tour_property_membership,ops.tour_route_version,ops.tour_route_stop,ops.tour_route_stop_transition,ops.tour_route_version_acceptance,ops.tour_cheat_sheet_revision to carr_writer,carr_authority;
commit;
-- Rollback is forward-only: revoke functions and quarantine consumers; never rewrite history.
