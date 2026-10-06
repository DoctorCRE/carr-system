-- 0430: least-privilege data plane for authenticated Tour search/cart,
-- confidential digest-only shares, and immutable PDF render/review records.
begin;

alter table ops.tour_share_grant add column if not exists receipt_digest text;
alter table ops.tour_share_grant add column if not exists created_by_actor_id text;
alter table ops.tour_share_grant drop constraint if exists tour_share_grant_receipt_digest_valid;
alter table ops.tour_share_grant add constraint tour_share_grant_receipt_digest_valid
  check (receipt_digest is null or receipt_digest ~ '^sha256:[a-f0-9]{64}$');

create table if not exists ops.tour_selection_cart_version (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  tour_id uuid not null, selection_version integer not null check (selection_version>0),
  base_selection_version_id uuid, property_ids jsonb not null,
  selection_digest text not null check (selection_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_by_actor_id text not null, created_at timestamptz not null default now(),
  unique (organization_tenant_id,id), unique (organization_tenant_id,tour_id,selection_version),
  unique (organization_tenant_id,base_selection_version_id),
  foreign key (organization_tenant_id,tour_id) references ops.tour(organization_tenant_id,id),
  foreign key (organization_tenant_id,base_selection_version_id) references ops.tour_selection_cart_version(organization_tenant_id,id),
  check (jsonb_typeof(property_ids)='array' and jsonb_array_length(property_ids)<=100)
);

create table if not exists ops.tour_share_session (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  share_grant_id uuid not null, session_digest text not null,
  permission_scopes jsonb not null, expires_at timestamptz not null,
  audit_digest text not null check (audit_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id), unique (session_digest), unique (audit_digest),
  foreign key (organization_tenant_id,share_grant_id) references ops.tour_share_grant(organization_tenant_id,id),
  check (session_digest ~ '^sha256:[a-f0-9]{64}$'),
  check (jsonb_typeof(permission_scopes)='array' and permission_scopes <@ '["view_packet","view_map"]'::jsonb),
  check (expires_at>created_at)
);

create table if not exists ops.tour_public_asset (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  projection_id uuid not null, property_id uuid not null, asset_ref text not null,
  storage_ref text not null, artifact_digest text not null check (artifact_digest ~ '^sha256:[a-f0-9]{64}$'),
  media_type text not null, content_length integer not null check (content_length>=0),
  alt text, caption text, created_at timestamptz not null default now(),
  unique (organization_tenant_id,id), unique (asset_ref),
  foreign key (organization_tenant_id,projection_id) references ops.tour_public_projection(organization_tenant_id,id),
  foreign key (organization_tenant_id,property_id) references ops.tour_property(organization_tenant_id,id),
  check (asset_ref ~ '^asset:public:[A-Za-z0-9_-]{16,256}$'),
  check (length(storage_ref) between 1 and 500)
);

-- A public map is a projection, never a live view over coordinate candidates.
-- These rows are selected exactly once while the parent projection is sealed
-- and are transitively bound into that projection's canonical digest below.
create table if not exists ops.tour_public_projection_map_point (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  projection_id uuid not null, property_id uuid not null,
  coordinate_candidate_id uuid not null, entrance_verification_receipt_id uuid not null,
  route_version integer not null check (route_version>0), created_at timestamptz not null default now(),
  unique (organization_tenant_id,id), unique (organization_tenant_id,projection_id,property_id),
  foreign key (organization_tenant_id,projection_id) references ops.tour_public_projection(organization_tenant_id,id),
  foreign key (organization_tenant_id,property_id) references ops.tour_property(organization_tenant_id,id),
  foreign key (organization_tenant_id,coordinate_candidate_id) references ops.tour_property_coordinate_candidate(organization_tenant_id,id),
  foreign key (organization_tenant_id,entrance_verification_receipt_id) references ops.tour_coordinate_entrance_verification_receipt(organization_tenant_id,id)
);

-- Client-map promotion is a separate human decision from projection sealing.
-- Each receipt binds the exact projection/dataset/route/component inputs and
-- the complete doctrine-required mobile, native-navigation, offline, rights,
-- and parity evidence. The newest receipt is authoritative; a later rejection
-- therefore closes a previously approved map without mutating history.
create table if not exists ops.tour_map_promotion_receipt (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  receipt_sequence bigint generated always as identity,
  projection_id uuid not null, reviewer_actor_id text not null,
  reviewed_at timestamptz not null, decision text not null check (decision in ('approved','rejected')),
  decision_reason text not null check (char_length(btrim(decision_reason)) between 1 and 500),
  brief_version text not null, canonical_dataset_version text not null,
  selected_prototype_id text not null, component_registry_version text not null,
  route_version integer not null check (route_version>0),
  provider_rights_receipt_ids jsonb not null,
  mobile_test_evidence jsonb not null, native_navigation_test_evidence jsonb not null,
  offline_test_evidence jsonb not null, required_checks jsonb not null,
  receipt_digest text not null check (receipt_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id), unique (receipt_digest),
  foreign key (organization_tenant_id,projection_id) references ops.tour_public_projection(organization_tenant_id,id),
  check (jsonb_typeof(provider_rights_receipt_ids)='array' and jsonb_array_length(provider_rights_receipt_ids)>0),
  check (jsonb_typeof(mobile_test_evidence)='object' and jsonb_typeof(native_navigation_test_evidence)='object'
    and jsonb_typeof(offline_test_evidence)='object' and jsonb_typeof(required_checks)='object')
);

create table if not exists ops.tour_pdf_render_job (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  projection_id uuid not null, requested_by_actor_id text not null, request jsonb not null,
  projection_digest text not null, packet_digest text not null,
  template_digest text not null, renderer_digest text not null, qc_ruleset_digest text not null,
  expected_property_count integer not null check (expected_property_count between 1 and 50),
  created_at timestamptz not null default now(), unique (organization_tenant_id,id),
  foreign key (organization_tenant_id,projection_id) references ops.tour_public_projection(organization_tenant_id,id),
  check (jsonb_typeof(request)='object'),
  check (projection_digest ~ '^sha256:[a-f0-9]{64}$' and packet_digest ~ '^sha256:[a-f0-9]{64}$'
    and template_digest ~ '^sha256:[a-f0-9]{64}$' and renderer_digest ~ '^sha256:[a-f0-9]{64}$'
    and qc_ruleset_digest ~ '^sha256:[a-f0-9]{64}$')
);

create table if not exists ops.tour_pdf_render_result (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  render_job_id uuid not null, status text not null check (status in ('rendering','qc_blocked','review_ready','available','failed')),
  artifact_ref text, artifact_digest text, storage_ref text, content_length integer check (content_length>=0), page_count integer check (page_count>=0),
  blocking_finding_count integer not null default 0 check (blocking_finding_count>=0),
  qc_run_digest text, attempt_count integer not null check (attempt_count>0),
  completed_at timestamptz, created_at timestamptz not null default now(),
  unique (organization_tenant_id,id), unique (organization_tenant_id,render_job_id,attempt_count),
  foreign key (organization_tenant_id,render_job_id) references ops.tour_pdf_render_job(organization_tenant_id,id),
  check (artifact_ref is null or artifact_ref ~ '^artifact:tour-pdf:[A-Za-z0-9_-]{16,128}$'),
  check (artifact_digest is null or artifact_digest ~ '^sha256:[a-f0-9]{64}$'),
  check (storage_ref is null or storage_ref ~ '^tour-pdf/[A-Za-z0-9._/-]{16,400}\.pdf$'),
  check (qc_run_digest is null or qc_run_digest ~ '^sha256:[a-f0-9]{64}$')
);

create table if not exists ops.tour_pdf_human_review (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  render_job_id uuid not null, render_result_id uuid not null, qc_run_digest text not null,
  decision text not null check (decision in ('accept','reject')), reviewed_at timestamptz not null,
  review_receipt_digest text not null check (review_receipt_digest ~ '^sha256:[a-f0-9]{64}$'),
  reason text not null, reviewer_actor_id text not null, created_at timestamptz not null default now(),
  unique (organization_tenant_id,id), unique (organization_tenant_id,render_job_id), unique (review_receipt_digest),
  foreign key (organization_tenant_id,render_job_id) references ops.tour_pdf_render_job(organization_tenant_id,id),
  foreign key (organization_tenant_id,render_result_id) references ops.tour_pdf_render_result(organization_tenant_id,id)
);

create or replace function ops.tour_delivery_append_only_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin raise exception '% is append-only',tg_table_name; end $$;

do $triggers$
declare v_table text;
begin
  foreach v_table in array array['tour_selection_cart_version','tour_share_session','tour_public_asset','tour_public_projection_map_point','tour_map_promotion_receipt','tour_pdf_render_job','tour_pdf_render_result','tour_pdf_human_review'] loop
    execute format('drop trigger if exists %I_append_only on ops.%I',v_table,v_table);
    execute format('create trigger %I_append_only before update or delete on ops.%I for each row execute function ops.tour_delivery_append_only_guard()',v_table,v_table);
  end loop;
end $triggers$;

-- Version 2 binds the immutable, human-verified public-map coordinate choice
-- alongside the selected public facts. Adding a later coordinate receipt can
-- therefore never change an already-sealed client URL.
create or replace function ops.tour_canonical_projection_digest(p_tenant text,p_projection_id uuid)
returns text language plpgsql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_projection ops.tour_public_projection%rowtype; v_fact_lines text; v_map_lines text; v_bytes text;
begin
  select * into v_projection from ops.tour_public_projection where id=p_projection_id and organization_tenant_id=p_tenant;
  if not found then raise exception 'canonical projection digest target is unavailable'; end if;
  select string_agg(
    f.property_id::text || '|' || f.field_assertion_id::text || '|' || f.route_version::text || '|' ||
    replace(encode(convert_to(f.display_field_key,'UTF8'),'base64'),E'\n',''),
    E'\n' order by f.property_id::text,convert_to(f.display_field_key,'UTF8'),f.field_assertion_id::text
  ) into v_fact_lines from ops.tour_public_projection_fact f
   where f.organization_tenant_id=p_tenant and f.projection_id=p_projection_id;
  select string_agg(
    mp.property_id::text || '|' || mp.coordinate_candidate_id::text || '|' ||
    mp.entrance_verification_receipt_id::text || '|' || mp.route_version::text,
    E'\n' order by mp.property_id::text,mp.coordinate_candidate_id::text,mp.entrance_verification_receipt_id::text
  ) into v_map_lines from ops.tour_public_projection_map_point mp
   where mp.organization_tenant_id=p_tenant and mp.projection_id=p_projection_id;
  v_bytes := array_to_string(array[
    'public-tour-projection-digest.v2',
    replace(encode(convert_to(p_tenant,'UTF8'),'base64'),E'\n',''),
    v_projection.tour_id::text,p_projection_id::text,v_projection.projection_version::text,
    v_projection.route_version::text,
    to_char(date_trunc('milliseconds',v_projection.as_of at time zone 'UTC'),'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
  ],E'\n') || case when v_fact_lines is null then '' else E'\n' || v_fact_lines end
    || case when v_map_lines is null then '' else E'\nmap' || E'\n' || v_map_lines end;
  return 'sha256:' || encode(public.digest(convert_to(v_bytes,'UTF8'),'sha256'),'hex');
end $$;

-- Replace the Slice-2 seal now that coordinate tables exist. The selected
-- fact API remains stable; the database deterministically snapshots at most
-- one eligible, human-verified entrance point per route property before the
-- single canonical projection digest and approval receipt are written.
create or replace function ops.seal_tour_public_projection(
  p_tenant text,p_projection_id uuid,p_selected_facts jsonb,p_actor_id text,p_receipt_digest text
) returns text language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_projection ops.tour_public_projection%rowtype; v_digest text;
begin
  if jsonb_typeof(p_selected_facts) <> 'array' or jsonb_array_length(p_selected_facts)=0
     or exists (select 1 from jsonb_array_elements(p_selected_facts) item where jsonb_typeof(item)<>'object' or (select array_agg(k order by k) from jsonb_object_keys(item) k) is distinct from array['display_field_key','field_assertion_id','property_id'])
     or exists (select 1 from jsonb_array_elements(p_selected_facts) item where coalesce(item->>'property_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' or coalesce(item->>'field_assertion_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' or coalesce(item->>'display_field_key','')='')
  then raise exception 'projection seal selected facts are invalid'; end if;
  select * into v_projection from ops.tour_public_projection where id=p_projection_id and organization_tenant_id=p_tenant for update;
  if not found then raise exception 'projection seal target is unavailable'; end if;
  if v_projection.status <> 'draft' or exists (select 1 from ops.tour_public_projection_seal_receipt where organization_tenant_id=p_tenant and projection_id=p_projection_id) then raise exception 'projection is not an unsealed draft'; end if;
  if exists (select 1 from ops.tour_public_projection_fact where organization_tenant_id=p_tenant and projection_id=p_projection_id) then raise exception 'projection seal facts already exist'; end if;
  if exists (select 1 from jsonb_to_recordset(p_selected_facts) x(property_id uuid,field_assertion_id uuid,display_field_key text) group by property_id,display_field_key having count(*)<>1) then raise exception 'projection seal selected facts are duplicated'; end if;
  if exists (
    select 1 from ops.tour_property_membership m cross join (values ('display.name'::text),('display.address'::text)) required(field_key)
    where m.organization_tenant_id=p_tenant and m.tour_id=v_projection.tour_id and m.route_version=v_projection.route_version
      and not exists (select 1 from jsonb_to_recordset(p_selected_facts) x(property_id uuid,field_assertion_id uuid,display_field_key text) where x.property_id=m.property_id and x.display_field_key=required.field_key)
  ) then raise exception 'projection seal requires one complete selected-property fact set'; end if;
  insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key)
  select p_tenant,p_projection_id,x.property_id,x.field_assertion_id,v_projection.route_version,x.display_field_key
    from jsonb_to_recordset(p_selected_facts) x(property_id uuid,field_assertion_id uuid,display_field_key text);
  insert into ops.tour_public_projection_map_point(
    organization_tenant_id,projection_id,property_id,coordinate_candidate_id,entrance_verification_receipt_id,route_version
  )
  select p_tenant,p_projection_id,m.property_id,chosen.coordinate_candidate_id,chosen.verification_receipt_id,v_projection.route_version
  from ops.tour_property_membership m
  join lateral (
    select c.id coordinate_candidate_id,er.id verification_receipt_id
    from ops.tour_coordinate_entrance_verification_receipt er
    join ops.tour_property_coordinate_candidate c on c.organization_tenant_id=er.organization_tenant_id and c.id=er.coordinate_candidate_id and c.property_id=er.property_id
    join ops.tour_source_evidence e on e.organization_tenant_id=c.organization_tenant_id and e.id=c.source_evidence_id and e.rights_receipt_id=c.rights_receipt_id
    join ops.tour_rights_receipt r on r.organization_tenant_id=e.organization_tenant_id and r.id=e.rights_receipt_id and r.provider=e.rights_provider and r.policy_key=e.rights_policy_key
    where er.organization_tenant_id=p_tenant and er.property_id=m.property_id
      and er.verified_at<=v_projection.as_of and c.observed_at<=v_projection.as_of
      and c.coordinate_role in ('entrance','driveway','parking_access') and c.review_state='reviewed'
      and r.status='active' and r.revoked_at is null and r.effective_at<=now() and (r.expires_at is null or r.expires_at>now())
      and r.allowed_use_classes ? 'client_public_display'
      -- Coordinates are a separately governed public field class, not an
      -- implicit consequence of broad display permission.
      and (r.allowed_field_classes ? 'coordinates' or r.allowed_field_classes ? '*')
      and not exists(select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.provider=r.provider and newer.policy_key=r.policy_key and newer.receipt_version>r.receipt_version and newer.effective_at<=now())
    order by er.verified_at desc,er.id desc,c.id desc limit 1
  ) chosen on true
  where m.organization_tenant_id=p_tenant and m.tour_id=v_projection.tour_id and m.route_version=v_projection.route_version;
  v_digest:=ops.tour_canonical_projection_digest(p_tenant,p_projection_id);
  insert into ops.tour_public_projection_seal_receipt (organization_tenant_id,projection_id,sealed_at,sealed_state,actor_id,receipt_digest,canonical_projection_digest)
  values (p_tenant,p_projection_id,now(),'approved',p_actor_id,p_receipt_digest,v_digest);
  return v_digest;
end $$;

create or replace function ops.search_tour_properties(p_tenant text,p_actor_id text,p_filters jsonb)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_limit integer; v_offset integer; v_count integer; v_items jsonb; v_has_more boolean; v_cursor text;
begin
  if p_tenant is null or p_actor_id is null or jsonb_typeof(p_filters)<>'object' then raise exception 'tour search context is invalid'; end if;
  v_limit:=least(greatest(coalesce((p_filters->>'limit')::integer,25),1),100);
  if p_filters->>'cursor' is not null and p_filters->>'cursor' !~ '^[0-9]{1,9}$' then raise exception 'tour search cursor is invalid'; end if;
  v_offset:=coalesce((p_filters->>'cursor')::integer,0);
  with candidates as (
    select p.id,
      encode(public.digest(p_tenant||':'||p.id::text,'sha256'),'hex') as public_key,
      j.county_name,
      name.value as name_value,address.value as address_value,type.value as type_value,
      size_fact.value as size_value,econ.value as econ_value,avail.value as availability_value,
      exists(select 1 from ops.tour_coordinate_entrance_verification_receipt er where er.organization_tenant_id=p_tenant and er.property_id=p.id) as entrance_verified,
      exists(select 1 from ops.tour_public_projection_fact pf join ops.tour_public_projection pp on pp.id=pf.projection_id and pp.organization_tenant_id=pf.organization_tenant_id where pf.organization_tenant_id=p_tenant and pf.property_id=p.id and pp.status='approved' and exists(select 1 from ops.tour_public_projection_seal_receipt ps where ps.organization_tenant_id=pp.organization_tenant_id and ps.projection_id=pp.id and ps.canonical_projection_digest=pp.projection_digest) and ops.read_tour_public_projection(pp.organization_tenant_id,pp.id) is not null) as public_projection_ready,
      coalesce(jsonb_array_length(case when jsonb_typeof(photos.value)='array' then photos.value else '[]'::jsonb end),0) as photo_count,
      greatest(p.created_at,j.created_at,name.created_at,address.created_at,type.created_at,
        size_fact.created_at,econ.created_at,avail.created_at,photos.created_at) as updated_at,
      greatest(j.as_of,name.observed_at,name.effective_from,address.observed_at,address.effective_from,
        type.observed_at,type.effective_from,size_fact.observed_at,size_fact.effective_from,
        econ.observed_at,econ.effective_from,avail.observed_at,avail.effective_from,
        photos.observed_at,photos.effective_from) as fact_as_of
    from ops.tour_property p
    join lateral (
      select d.county_name,a.created_at,a.as_of from ops.tour_property_jurisdiction_assertion a join ops.tour_jurisdiction_dataset d on d.id=a.jurisdiction_dataset_id and d.organization_tenant_id=a.organization_tenant_id
       where a.organization_tenant_id=p.organization_tenant_id and a.property_id=p.id and a.review_state='reviewed' and d.review_state='reviewed' and d.state_code='FL'
       order by a.as_of desc,a.id desc limit 1
    ) j on true
    left join lateral (select value,created_at,observed_at,effective_from from ops.tour_field_assertion a where a.organization_tenant_id=p.organization_tenant_id and a.property_id=p.id and a.field_key='display.name' and a.review_state='reviewed' and a.effective_from<=now() and (a.effective_to is null or a.effective_to>now()) order by a.effective_from desc,a.id desc limit 1) name on true
    left join lateral (select value,created_at,observed_at,effective_from from ops.tour_field_assertion a where a.organization_tenant_id=p.organization_tenant_id and a.property_id=p.id and a.field_key='display.address' and a.review_state='reviewed' and a.effective_from<=now() and (a.effective_to is null or a.effective_to>now()) order by a.effective_from desc,a.id desc limit 1) address on true
    left join lateral (select value,created_at,observed_at,effective_from from ops.tour_field_assertion a where a.organization_tenant_id=p.organization_tenant_id and a.property_id=p.id and a.field_key='property_type' and a.review_state='reviewed' and a.effective_from<=now() and (a.effective_to is null or a.effective_to>now()) order by a.effective_from desc,a.id desc limit 1) type on true
    left join lateral (select value,created_at,observed_at,effective_from from ops.tour_field_assertion a where a.organization_tenant_id=p.organization_tenant_id and a.property_id=p.id and a.field_key='size' and a.review_state='reviewed' and a.effective_from<=now() and (a.effective_to is null or a.effective_to>now()) order by a.effective_from desc,a.id desc limit 1) size_fact on true
    left join lateral (select value,created_at,observed_at,effective_from from ops.tour_field_assertion a where a.organization_tenant_id=p.organization_tenant_id and a.property_id=p.id and a.field_key='asking_economics' and a.review_state='reviewed' and a.effective_from<=now() and (a.effective_to is null or a.effective_to>now()) order by a.effective_from desc,a.id desc limit 1) econ on true
    left join lateral (select value,created_at,observed_at,effective_from from ops.tour_field_assertion a where a.organization_tenant_id=p.organization_tenant_id and a.property_id=p.id and a.field_key='availability' and a.review_state='reviewed' and a.effective_from<=now() and (a.effective_to is null or a.effective_to>now()) order by a.effective_from desc,a.id desc limit 1) avail on true
    left join lateral (select value,created_at,observed_at,effective_from from ops.tour_field_assertion a where a.organization_tenant_id=p.organization_tenant_id and a.property_id=p.id and a.field_key='photos' and a.review_state='reviewed' and a.effective_from<=now() and (a.effective_to is null or a.effective_to>now()) order by a.effective_from desc,a.id desc limit 1) photos on true
    where p.organization_tenant_id=p_tenant and p.property_status='active'
  ), filtered as (
    select * from candidates c where
      (coalesce(jsonb_array_length(p_filters->'counties'),0)=0 or p_filters->'counties' ? c.county_name)
      and (nullif(p_filters->>'query','') is null or coalesce(c.name_value#>>'{}','') ilike '%'||(p_filters->>'query')||'%' or coalesce(c.address_value#>>'{}','') ilike '%'||(p_filters->>'query')||'%')
      and (coalesce(jsonb_array_length(p_filters->'property_types'),0)=0 or p_filters->'property_types' ? coalesce(c.type_value#>>'{}',''))
      and (coalesce(jsonb_array_length(p_filters->'availability'),0)=0 or p_filters->'availability' ? coalesce(c.availability_value#>>'{}','unknown'))
      and ((p_filters->>'entrance_verified') is null or c.entrance_verified=(p_filters->>'entrance_verified')::boolean)
      and ((p_filters->>'public_projection_ready') is null or c.public_projection_ready=(p_filters->>'public_projection_ready')::boolean)
      and ((p_filters->>'photos_available') is null or (c.photo_count>0)=(p_filters->>'photos_available')::boolean)
      and ((p_filters->>'min_square_feet') is null or (case when c.size_value->>'value' ~ '^-?[0-9]+(?:\.[0-9]+)?$' then (c.size_value->>'value')::numeric end) >= (p_filters->>'min_square_feet')::numeric)
      and ((p_filters->>'max_square_feet') is null or (case when c.size_value->>'value' ~ '^-?[0-9]+(?:\.[0-9]+)?$' then (c.size_value->>'value')::numeric end) <= (p_filters->>'max_square_feet')::numeric)
  ), ordered as (
    select filtered.*,row_number() over (order by
      case when p_filters->>'sort'='address_asc' then address_value#>>'{}' end asc,
      case when p_filters->>'sort'='size_asc' and size_value->>'value' ~ '^-?[0-9]+(?:\.[0-9]+)?$' then (size_value->>'value')::numeric end asc nulls last,
      case when p_filters->>'sort'='size_desc' and size_value->>'value' ~ '^-?[0-9]+(?:\.[0-9]+)?$' then (size_value->>'value')::numeric end desc nulls last,
      updated_at desc,id
    ) result_position from filtered
  ), page_plus_one as (
    select * from ordered where result_position>v_offset order by result_position limit v_limit+1
  ), page as (
    select * from page_plus_one order by result_position limit v_limit
  )
  select count(*)::integer,coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
    'property_id',id,'property_ref','property:public:'||substr(public_key,1,32),
    'name',name_value#>>'{}','address',address_value#>>'{}','county',county_name,'state','FL',
    'property_type',type_value#>>'{}','size',size_value,'asking_economics',econ_value,
    'availability',coalesce(availability_value#>>'{}','unknown'),'entrance_verified',entrance_verified,
    'public_projection_ready',public_projection_ready,'photos_available',photo_count>0,'photo_count',photo_count,
    'updated_at',updated_at,'fact_as_of',fact_as_of,'caveat','Facts shown are source-reviewed as of the displayed timestamp.'
  )) order by result_position),'[]'::jsonb),
  exists(select 1 from page_plus_one where result_position>v_offset+v_limit)
  into v_count,v_items,v_has_more from page;
  v_cursor:=case when v_has_more then (v_offset+v_count)::text else null end;
  return jsonb_strip_nulls(jsonb_build_object('count',v_count,'has_more',v_has_more,'cursor',v_cursor,'items',v_items));
end $$;

create or replace function ops.append_tour_selection_cart_version(p_tenant text,p_tour_id uuid,p_base_selection_version_id uuid,p_property_ids jsonb,p_expected_selection_version integer,p_selection_digest text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_latest ops.tour_selection_cart_version%rowtype; v_id uuid; v_actor text;
begin
  v_actor:=ops.tour_server_actor_id();
  if p_expected_selection_version is null or jsonb_typeof(p_property_ids)<>'array' or jsonb_array_length(p_property_ids)>100 or p_selection_digest !~ '^sha256:[a-f0-9]{64}$' then raise exception 'tour selection payload is invalid'; end if;
  perform 1 from ops.tour where organization_tenant_id=p_tenant and id=p_tour_id for update;
  if not found then raise exception 'tour selection tour is unavailable'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_tenant||':'||p_tour_id::text||':selection',403));
  select * into v_latest from ops.tour_selection_cart_version where organization_tenant_id=p_tenant and tour_id=p_tour_id order by selection_version desc,id desc limit 1;
  if p_expected_selection_version<>coalesce(v_latest.selection_version,0) or p_base_selection_version_id is distinct from v_latest.id then raise exception 'tour selection refuses stale version'; end if;
  if exists(select 1 from jsonb_array_elements_text(p_property_ids) x(value) left join ops.tour_property p on p.organization_tenant_id=p_tenant and p.id=x.value::uuid where p.id is null) then raise exception 'tour selection property is unavailable'; end if;
  insert into ops.tour_selection_cart_version(organization_tenant_id,tour_id,selection_version,base_selection_version_id,property_ids,selection_digest,created_by_actor_id)
  values(p_tenant,p_tour_id,coalesce(v_latest.selection_version,0)+1,v_latest.id,p_property_ids,p_selection_digest,v_actor) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.read_tour_selection_cart(p_tenant text,p_tour_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select jsonb_build_object('tour_id',tour_id,'selection_version_id',id,'selection_version',selection_version,'property_ids',property_ids,'updated_at',created_at)
  from ops.tour_selection_cart_version where organization_tenant_id=p_tenant and tour_id=p_tour_id and nullif(btrim(p_actor_id),'') is not null order by selection_version desc,id desc limit 1;
$$;

create or replace function ops.list_tour_library(p_tenant text,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select jsonb_build_object('tours',coalesce(jsonb_agg(jsonb_build_object(
    'id',t.id,'tour_name',t.tour_name,'tour_status',t.tour_status,'route_version',t.route_version,
    'updated_at',t.updated_at,'property_count',(select count(*) from ops.tour_property_membership m where m.organization_tenant_id=t.organization_tenant_id and m.tour_id=t.id and m.route_version=t.route_version),
    'projection_count',(select count(*) from ops.tour_public_projection p where p.organization_tenant_id=t.organization_tenant_id and p.tour_id=t.id),
    'share_count',(select count(*) from ops.tour_share_grant g join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id where p.organization_tenant_id=t.organization_tenant_id and p.tour_id=t.id)
  ) order by t.updated_at desc,t.id),'[]'::jsonb))
  from ops.tour t where t.organization_tenant_id=p_tenant and nullif(btrim(p_actor_id),'') is not null;
$$;

create or replace function ops.read_tour_internal_detail(p_tenant text,p_tour_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select jsonb_build_object(
    'id',t.id,'tour_name',t.tour_name,'tour_status',t.tour_status,'route_version',t.route_version,'updated_at',t.updated_at,
    'routes',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'route_version',v.route_version,'routing_source',v.routing_source,'created_at',v.created_at,'accepted',a.id is not null,'stops',coalesce((select jsonb_agg(jsonb_build_object(
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

create or replace function ops.prepare_tour_route_version(p_tenant text,p_tour_id uuid,p_base_route_version_id uuid,p_expected_route_version integer,p_stop_ids jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_tour ops.tour%rowtype; v_base ops.tour_route_version%rowtype; v_source ops.tour_route_version%rowtype; v_new uuid; v_new_stop uuid; v_next integer; v_stop record; v_old_stop ops.tour_route_stop%rowtype; v_label text;
begin
  if jsonb_typeof(p_stop_ids)<>'array' or jsonb_array_length(p_stop_ids)=0 or jsonb_array_length(p_stop_ids)>100
     or exists(select 1 from jsonb_array_elements_text(p_stop_ids) x(value) where value !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
     or (select count(*) from jsonb_array_elements_text(p_stop_ids))<>(select count(distinct value) from jsonb_array_elements_text(p_stop_ids) x(value)) then raise exception 'tour route preparation payload is invalid'; end if;
  select * into v_tour from ops.tour where organization_tenant_id=p_tenant and id=p_tour_id for update;
  if not found or v_tour.route_version<>p_expected_route_version then raise exception 'tour route preparation refuses stale state'; end if;
  select v.* into v_base from ops.tour_route_version v join ops.tour_route_version_acceptance a on a.organization_tenant_id=v.organization_tenant_id and a.route_version_id=v.id where v.organization_tenant_id=p_tenant and v.tour_id=p_tour_id and v.route_version=p_expected_route_version;
  if not found then raise exception 'tour route preparation base is unavailable'; end if;
  if p_base_route_version_id is null then v_source:=v_base;
  else
    select * into v_source from ops.tour_route_version where organization_tenant_id=p_tenant and tour_id=p_tour_id and id=p_base_route_version_id;
    if not found then raise exception 'tour route preparation base is unavailable'; end if;
  end if;
  if exists(select 1 from jsonb_array_elements_text(p_stop_ids) x(value) left join ops.tour_route_stop s on s.organization_tenant_id=p_tenant and s.route_version_id=v_source.id and s.id=x.value::uuid and s.stop_state='active' where s.id is null) then raise exception 'tour route preparation stop is unavailable'; end if;
  if exists(
    select 1 from (
      select s.appointment_start,
        lag(s.appointment_start) over (order by x.ordinality) as prior_appointment_start
      from jsonb_array_elements_text(p_stop_ids) with ordinality x(value,ordinality)
      join ops.tour_route_stop s on s.organization_tenant_id=p_tenant
        and s.route_version_id=v_source.id and s.id=x.value::uuid
      where s.stop_state='active' and s.locked_appointment
    ) locked
    where locked.prior_appointment_start is not null
      and locked.appointment_start < locked.prior_appointment_start
  ) then raise exception 'tour route preparation violates locked appointment order'; end if;
  select coalesce(max(route_version),0)+1 into v_next from ops.tour_route_version where organization_tenant_id=p_tenant and tour_id=p_tour_id;
  v_new:=ops.append_tour_route_version(p_tenant,p_tour_id,v_next,v_base.id,v_base.start_point,v_base.end_point,'manual',null,null,'{}',null,p_expected_route_version,null);
  for v_stop in select s.*,x.ordinality::integer new_sequence from jsonb_array_elements_text(p_stop_ids) with ordinality x(value,ordinality) join ops.tour_route_stop s on s.organization_tenant_id=p_tenant and s.route_version_id=v_source.id and s.id=x.value::uuid order by x.ordinality loop
    v_label:=case when v_stop.new_sequence<=26 then chr(64+v_stop.new_sequence) else chr(64+((v_stop.new_sequence-1)/26))||chr(65+((v_stop.new_sequence-1)%26)) end;
    v_new_stop:=ops.append_tour_route_stop(p_tenant,v_new,v_stop.property_id,v_stop.new_sequence,v_label,'active',v_stop.appointment_start,v_stop.appointment_end,v_stop.locked_appointment,v_stop.dwell_minutes,v_stop.buffer_minutes,v_stop.access_coordinate_status,v_stop.assertion_set_digest);
    select * into v_old_stop from ops.tour_route_stop where organization_tenant_id=p_tenant and route_version_id=v_base.id and property_id=v_stop.property_id;
    if found then
      perform ops.append_tour_route_stop_transition(p_tenant,v_base.id,v_new,v_old_stop.id,v_new_stop,case when v_old_stop.route_sequence=v_stop.new_sequence then 'unchanged' else 'reordered' end);
    else
      perform ops.append_tour_route_stop_transition(p_tenant,null,v_new,null,v_new_stop,'added');
    end if;
  end loop;
  for v_stop in select s.* from ops.tour_route_stop s where s.organization_tenant_id=p_tenant and s.route_version_id=v_base.id and not exists(
    select 1 from jsonb_array_elements_text(p_stop_ids) x(value) join ops.tour_route_stop selected on selected.organization_tenant_id=p_tenant and selected.route_version_id=v_source.id and selected.id=x.value::uuid where selected.property_id=s.property_id
  ) loop
    perform ops.append_tour_route_stop_transition(p_tenant,v_base.id,v_new,v_stop.id,null,'removed');
  end loop;
  return v_new;
end $$;

create or replace function ops.read_tour_projection_creation_metadata(p_tenant text,p_tour_id uuid,p_route_version_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select jsonb_build_object(
    'route_version',v.route_version,
    'projection_version',coalesce((select max(p.projection_version) from ops.tour_public_projection p where p.organization_tenant_id=v.organization_tenant_id and p.tour_id=v.tour_id),0)+1
  )
  from ops.tour_route_version v
  join ops.tour_route_version_acceptance a on a.organization_tenant_id=v.organization_tenant_id and a.route_version_id=v.id
  where v.organization_tenant_id=p_tenant and v.tour_id=p_tour_id and v.id=p_route_version_id
    and nullif(btrim(p_actor_id),'') is not null;
$$;

create or replace function ops.read_tour_projection_seal_candidates(p_tenant text,p_projection_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with projection as (
    select p.* from ops.tour_public_projection p where p.organization_tenant_id=p_tenant and p.id=p_projection_id and p.status='draft' and nullif(btrim(p_actor_id),'') is not null
  ), candidates as (
    select m.property_id,m.route_sequence,m.route_label,a.id field_assertion_id,a.field_key display_field_key,a.value,a.effective_from
    from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
    join lateral (
      select assertion.* from ops.tour_field_assertion assertion
      join ops.tour_source_evidence e on e.organization_tenant_id=assertion.organization_tenant_id and e.id=assertion.source_evidence_id and e.rights_receipt_id=assertion.rights_receipt_id
      join ops.tour_rights_receipt r on r.organization_tenant_id=assertion.organization_tenant_id and r.id=assertion.rights_receipt_id and r.provider=e.rights_provider and r.policy_key=e.rights_policy_key
      where assertion.organization_tenant_id=m.organization_tenant_id and assertion.property_id=m.property_id and assertion.review_state='reviewed' and assertion.data_classification='public'
        and assertion.effective_from<=p.as_of and (assertion.effective_to is null or assertion.effective_to>p.as_of)
        and r.status='active' and r.revoked_at is null and r.effective_at<=p.as_of and (r.expires_at is null or r.expires_at>p.as_of)
        and r.allowed_use_classes ? 'client_public_display' and (r.allowed_field_classes ? assertion.field_key or r.allowed_field_classes ? '*')
        and not exists(select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.provider=r.provider and newer.policy_key=r.policy_key and newer.receipt_version>r.receipt_version and newer.effective_at<=p.as_of)
        and ops.tour_public_value_safe(assertion.field_key,assertion.value)
      order by assertion.field_key,assertion.effective_from desc,assertion.id desc
    ) a on true
  ), selected as (
    select distinct on (property_id,display_field_key) property_id,route_sequence,route_label,field_assertion_id,display_field_key,value
    from candidates order by property_id,display_field_key,effective_from desc,field_assertion_id desc
  ), selected_json as (
    select coalesce(jsonb_agg(jsonb_build_object('property_id',property_id,'field_assertion_id',field_assertion_id,'display_field_key',display_field_key) order by property_id::text,display_field_key,field_assertion_id::text),'[]'::jsonb) value from selected
  ), preview as (
    select coalesce(jsonb_agg(jsonb_build_object('route_sequence',route_sequence,'route_label',route_label,'property_id',property_id,'facts',facts) order by route_sequence),'[]'::jsonb) value
    from (select property_id,route_sequence,route_label,jsonb_object_agg(display_field_key,value order by display_field_key) facts from selected group by property_id,route_sequence,route_label) grouped
  ) select jsonb_build_object('projection_id',p.id,'candidate_digest','sha256:'||encode(public.digest(s.value::text,'sha256'),'hex'),'selected_facts',s.value,'preview',v.value)
  from projection p cross join selected_json s cross join preview v;
$$;

create or replace function ops.tour_public_map_projection_evidence_ready(p_tenant text,p_projection_id uuid)
returns boolean language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select exists (
    select 1 from ops.tour_public_projection p
    where p.organization_tenant_id=p_tenant and p.id=p_projection_id and p.status='approved'
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
      and exists(select 1 from ops.tour_property_membership m where m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version)
      and (select count(*) from ops.tour_public_projection_map_point mp where mp.organization_tenant_id=p.organization_tenant_id and mp.projection_id=p.id)
        = (select count(*) from ops.tour_property_membership m where m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version)
      and not exists (
        select 1 from ops.tour_public_projection_map_point mp
        where mp.organization_tenant_id=p.organization_tenant_id and mp.projection_id=p.id
          and not exists (
            select 1 from ops.tour_property_coordinate_candidate c
            join ops.tour_coordinate_entrance_verification_receipt er on er.organization_tenant_id=c.organization_tenant_id and er.coordinate_candidate_id=c.id and er.property_id=c.property_id
            join ops.tour_source_evidence e on e.organization_tenant_id=c.organization_tenant_id and e.id=c.source_evidence_id and e.rights_receipt_id=c.rights_receipt_id
            join ops.tour_rights_receipt r on r.organization_tenant_id=e.organization_tenant_id and r.id=e.rights_receipt_id and r.provider=e.rights_provider and r.policy_key=e.rights_policy_key
            where c.organization_tenant_id=mp.organization_tenant_id and c.id=mp.coordinate_candidate_id and c.property_id=mp.property_id
              and er.id=mp.entrance_verification_receipt_id and c.coordinate_role in ('entrance','driveway','parking_access') and c.review_state='reviewed'
              and r.status='active' and r.effective_at<=now() and (r.expires_at is null or r.expires_at>now()) and (r.revoked_at is null or r.revoked_at>now())
              and r.allowed_use_classes ? 'client_public_display' and (r.allowed_field_classes ? 'coordinates' or r.allowed_field_classes ? '*')
              and not exists(select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.provider=r.provider and newer.policy_key=r.policy_key and newer.receipt_version>r.receipt_version and newer.effective_at<=now())
          )
      )
  );
$$;

create or replace function ops.record_tour_map_promotion_receipt(p_tenant text,p_projection_id uuid,p_payload jsonb,p_actor_id text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare
  v_id uuid; v_projection ops.tour_public_projection%rowtype; v_tour ops.tour%rowtype;
  v_verified_slug text; v_verified_actor_id text;
  v_required_check_keys constant text[]:=array[
    'canonical_address_and_coordinate_review','claims_and_layers_have_source_as_of_rights_and_review_state',
    'deterministic_rebuild_from_canonical_record','exact_native_navigation_handoff',
    'locked_appointments_dwell_and_buffers_preserved','map_list_route_offline_order_parity',
    'no_unresolved_route_critical_unknown_or_conflict','optional_context_layers_progressively_disclosed',
    'ordered_offline_itinerary_verified','phone_and_ipad_interaction_test',
    'provider_terms_attribution_expiry_and_cost_gate_passed'];
begin
  if jsonb_typeof(p_payload)<>'object'
     or (select array_agg(k order by k) from jsonb_object_keys(p_payload) k) is distinct from array[
       'brief_version','canonical_dataset_version','component_registry_version','decision','decision_reason',
       'mobile_test_evidence','native_navigation_test_evidence','offline_test_evidence',
       'provider_rights_receipt_ids','receipt_digest','required_checks','reviewed_at',
       'route_version','selected_prototype_id']
  then raise exception 'tour map promotion receipt payload is invalid'; end if;
  v_verified_slug:=nullif(btrim(current_setting('carr.verified_human_actor_slug',true)),'');
  select id::text into v_verified_actor_id from public.actor where slug=v_verified_slug;
  if v_verified_actor_id is null or v_verified_actor_id is distinct from p_actor_id then
    raise exception 'tour map promotion requires a verified human authority session';
  end if;
  select * into v_projection from ops.tour_public_projection
    where organization_tenant_id=p_tenant and id=p_projection_id and status='approved';
  select * into v_tour from ops.tour
    where organization_tenant_id=p_tenant and id=v_projection.tour_id;
  if not found or v_projection.id is null
     or not ops.tour_public_map_projection_evidence_ready(p_tenant,p_projection_id)
     or p_payload->>'decision' not in ('approved','rejected')
     or nullif(btrim(p_payload->>'decision_reason'),'') is null
     or char_length(btrim(p_payload->>'decision_reason'))>500
     or p_payload->>'decision_reason' ~ '[[:cntrl:]]'
     or (p_payload->>'reviewed_at')::timestamptz>now()
     or nullif(btrim(p_payload->>'brief_version'),'') is null
     or nullif(btrim(p_payload->>'selected_prototype_id'),'') is null
     or nullif(btrim(p_payload->>'component_registry_version'),'') is null
     or p_payload->>'canonical_dataset_version' is distinct from v_tour.canonical_dataset_version
     or (p_payload->>'route_version')::integer is distinct from v_projection.route_version
     or p_payload->>'receipt_digest' !~ '^sha256:[a-f0-9]{64}$'
     or jsonb_typeof(p_payload->'provider_rights_receipt_ids')<>'array'
     or jsonb_array_length(p_payload->'provider_rights_receipt_ids')=0
     or exists(select 1 from jsonb_array_elements_text(p_payload->'provider_rights_receipt_ids') x where x !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
     or (select count(*) from jsonb_array_elements_text(p_payload->'provider_rights_receipt_ids'))
        <> (select count(distinct x) from jsonb_array_elements_text(p_payload->'provider_rights_receipt_ids') x)
     or jsonb_typeof(p_payload->'mobile_test_evidence')<>'object' or p_payload->'mobile_test_evidence'='{}'::jsonb
     or jsonb_typeof(p_payload->'native_navigation_test_evidence')<>'object' or p_payload->'native_navigation_test_evidence'='{}'::jsonb
     or jsonb_typeof(p_payload->'offline_test_evidence')<>'object' or p_payload->'offline_test_evidence'='{}'::jsonb
     or jsonb_typeof(p_payload->'required_checks')<>'object'
     or (select array_agg(k order by k) from jsonb_object_keys(p_payload->'required_checks') k) is distinct from v_required_check_keys
     or exists(select 1 from jsonb_each(p_payload->'required_checks') check_row where check_row.value not in ('true'::jsonb,'false'::jsonb))
     or (p_payload->>'decision'='approved' and (
       exists(select 1 from jsonb_each(p_payload->'required_checks') check_row where check_row.value<>'true'::jsonb)
       or p_payload#>>'{mobile_test_evidence,status}' is distinct from 'passed'
       or p_payload#>>'{native_navigation_test_evidence,status}' is distinct from 'passed'
       or p_payload#>>'{offline_test_evidence,status}' is distinct from 'passed'))
     or (p_payload->>'decision'='rejected' and not (
       exists(select 1 from jsonb_each(p_payload->'required_checks') check_row where check_row.value='false'::jsonb)
       or p_payload#>>'{mobile_test_evidence,status}'='failed'
       or p_payload#>>'{native_navigation_test_evidence,status}'='failed'
       or p_payload#>>'{offline_test_evidence,status}'='failed'))
  then raise exception 'tour map promotion receipt is invalid or incomplete'; end if;
  if exists (
    (select x::uuid from jsonb_array_elements_text(p_payload->'provider_rights_receipt_ids') x
     except
     select distinct r.id from ops.tour_public_projection_map_point mp
       join ops.tour_property_coordinate_candidate c on c.organization_tenant_id=mp.organization_tenant_id and c.id=mp.coordinate_candidate_id
       join ops.tour_rights_receipt r on r.organization_tenant_id=c.organization_tenant_id and r.id=c.rights_receipt_id
      where mp.organization_tenant_id=p_tenant and mp.projection_id=p_projection_id)
    union all
    (select distinct r.id from ops.tour_public_projection_map_point mp
       join ops.tour_property_coordinate_candidate c on c.organization_tenant_id=mp.organization_tenant_id and c.id=mp.coordinate_candidate_id
       join ops.tour_rights_receipt r on r.organization_tenant_id=c.organization_tenant_id and r.id=c.rights_receipt_id
      where mp.organization_tenant_id=p_tenant and mp.projection_id=p_projection_id
     except
     select x::uuid from jsonb_array_elements_text(p_payload->'provider_rights_receipt_ids') x)
  ) then raise exception 'tour map promotion provider-rights evidence mismatch'; end if;
  insert into ops.tour_map_promotion_receipt(
    organization_tenant_id,projection_id,reviewer_actor_id,reviewed_at,decision,decision_reason,
    brief_version,canonical_dataset_version,selected_prototype_id,component_registry_version,
    route_version,provider_rights_receipt_ids,mobile_test_evidence,native_navigation_test_evidence,
    offline_test_evidence,required_checks,receipt_digest)
  values(p_tenant,p_projection_id,p_actor_id,(p_payload->>'reviewed_at')::timestamptz,p_payload->>'decision',btrim(p_payload->>'decision_reason'),
    p_payload->>'brief_version',p_payload->>'canonical_dataset_version',p_payload->>'selected_prototype_id',
    p_payload->>'component_registry_version',(p_payload->>'route_version')::integer,
    p_payload->'provider_rights_receipt_ids',p_payload->'mobile_test_evidence',
    p_payload->'native_navigation_test_evidence',p_payload->'offline_test_evidence',
    p_payload->'required_checks',p_payload->>'receipt_digest') returning id into v_id;
  return v_id;
end $$;

create or replace function ops.tour_public_map_projection_ready(p_tenant text,p_projection_id uuid)
returns boolean language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select ops.tour_public_map_projection_evidence_ready(p_tenant,p_projection_id)
    and coalesce((
      select r.decision='approved'
      from ops.tour_map_promotion_receipt r
      join ops.tour_public_projection p on p.organization_tenant_id=r.organization_tenant_id and p.id=r.projection_id
      join ops.tour t on t.organization_tenant_id=p.organization_tenant_id and t.id=p.tour_id
      where r.organization_tenant_id=p_tenant and r.projection_id=p_projection_id
        and r.route_version=p.route_version and r.canonical_dataset_version=t.canonical_dataset_version
      order by r.receipt_sequence desc limit 1
    ),false);
$$;

create or replace function ops.issue_tour_share_grant(p_tenant text,p_projection_id uuid,p_token_digest text,p_permission_scopes jsonb,p_expires_at timestamptz,p_receipt_digest text,p_actor_id text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_projection ops.tour_public_projection%rowtype;
begin
  if p_token_digest !~ '^sha256:[a-f0-9]{64}$' or p_receipt_digest !~ '^sha256:[a-f0-9]{64}$' or p_expires_at<=now() or jsonb_typeof(p_permission_scopes)<>'array' or not (p_permission_scopes <@ '["view_packet","view_map"]'::jsonb) or jsonb_array_length(p_permission_scopes)=0 or nullif(btrim(p_actor_id),'') is null then raise exception 'tour share payload is invalid'; end if;
  select * into v_projection from ops.tour_public_projection p where p.organization_tenant_id=p_tenant and p.id=p_projection_id and p.status='approved' and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest) and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null for update;
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
  if p_token_digest !~ '^sha256:[a-f0-9]{64}$' or p_receipt_digest !~ '^sha256:[a-f0-9]{64}$' or p_expires_at<=now() or jsonb_typeof(p_permission_scopes)<>'array' or not (p_permission_scopes <@ '["view_packet","view_map"]'::jsonb) or jsonb_array_length(p_permission_scopes)=0 or nullif(btrim(p_actor_id),'') is null then raise exception 'tour share payload is invalid'; end if;
  select * into v_projection from ops.tour_public_projection where organization_tenant_id=p_tenant and id=p_projection_id;
  if not found or ops.read_tour_public_projection(p_tenant,p_projection_id) is null then raise exception 'tour share requires a current sealed projection'; end if;
  if p_permission_scopes ? 'view_map' and not ops.tour_public_map_projection_ready(p_tenant,p_projection_id)
  then raise exception 'tour map share requires current rights, sealed entrance coordinates, and an approved promotion receipt'; end if;
  insert into ops.tour_share_grant(organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,rotated_from_grant_id,expires_at,status,receipt_digest,created_by_actor_id)
  values(p_tenant,p_projection_id,v_prior.grant_version+1,p_token_digest,'client',p_permission_scopes,v_prior.id,p_expires_at,'active',p_receipt_digest,p_actor_id) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.revoke_tour_share_grant(p_tenant text,p_share_grant_id uuid,p_reason text,p_receipt_digest text,p_revoked_at timestamptz,p_actor_id text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform 1 from ops.tour_share_grant where organization_tenant_id=p_tenant and id=p_share_grant_id for update;
  if not found or exists(select 1 from ops.tour_share_grant_revocation_receipt where organization_tenant_id=p_tenant and share_grant_id=p_share_grant_id) or p_receipt_digest !~ '^sha256:[a-f0-9]{64}$' or nullif(btrim(p_reason),'') is null or nullif(btrim(p_actor_id),'') is null then raise exception 'tour share revocation is invalid'; end if;
  insert into ops.tour_share_grant_revocation_receipt(organization_tenant_id,share_grant_id,revoked_at,actor_id,reason,receipt_digest)
  values(p_tenant,p_share_grant_id,p_revoked_at,p_actor_id,p_reason,p_receipt_digest);
  return p_share_grant_id;
end $$;

create or replace function ops.read_tour_sharing_library(p_tenant text,p_projection_id uuid,p_actor_id text,p_cursor text,p_limit integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_limit integer; v_offset integer; v_count integer; v_items jsonb;
begin
  if nullif(btrim(p_actor_id),'') is null or p_limit is null or p_limit<1 or p_limit>100
     or (p_cursor is not null and p_cursor !~ '^[0-9]{1,9}$') then return null; end if;
  v_limit:=p_limit; v_offset:=coalesce(p_cursor::integer,0);
  with selected as (
    select g.*,r.id revocation_id,r.revoked_at,r.reason,successor.id successor_id
    from ops.tour_share_grant g
    left join ops.tour_share_grant_revocation_receipt r on r.organization_tenant_id=g.organization_tenant_id and r.share_grant_id=g.id
    left join ops.tour_share_grant successor on successor.organization_tenant_id=g.organization_tenant_id and successor.rotated_from_grant_id=g.id
    where g.organization_tenant_id=p_tenant and g.projection_id=p_projection_id
    order by g.grant_version desc,g.id desc limit v_limit+1 offset v_offset
  ), page as (
    select selected.*,row_number() over (order by grant_version desc,id desc) page_row from selected
  )
  select count(*),coalesce(jsonb_agg(jsonb_build_object(
    'share_grant_id',id,'status',case when revocation_id is not null then 'revoked' when successor_id is not null then 'rotated' when expires_at<=now() then 'expired' else 'active' end,
    'permission_scopes',permission_scopes,'expires_at',expires_at,'revoked_at',revoked_at,'reason',reason,'created_at',created_at
  ) order by grant_version desc,id desc) filter(where page_row<=v_limit),'[]'::jsonb)
  into v_count,v_items from page;
  return jsonb_strip_nulls(jsonb_build_object(
    'items',v_items,'has_more',v_count>v_limit,
    'cursor',case when v_count>v_limit then (v_offset+v_limit)::text end));
end;
$$;

create or replace function ops.exchange_tour_share_token(p_token_digest text,p_session_digest text,p_session_expires_at timestamptz,p_audit_digest text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_grant ops.tour_share_grant%rowtype; v_expiry timestamptz;
begin
  if p_token_digest !~ '^sha256:[a-f0-9]{64}$' or p_session_digest !~ '^sha256:[a-f0-9]{64}$' or p_audit_digest !~ '^sha256:[a-f0-9]{64}$' then return null; end if;
  select * into v_grant from ops.tour_share_grant g where g.token_digest=p_token_digest and g.status='active' and g.expires_at>now()
    and not exists(select 1 from ops.tour_share_grant_revocation_receipt r where r.organization_tenant_id=g.organization_tenant_id and r.share_grant_id=g.id)
    and not exists(select 1 from ops.tour_share_grant successor where successor.organization_tenant_id=g.organization_tenant_id and successor.rotated_from_grant_id=g.id) for share;
  if not found then return null; end if;
  v_expiry:=least(p_session_expires_at,v_grant.expires_at);
  if v_expiry<=now() then return null; end if;
  insert into ops.tour_share_session(organization_tenant_id,share_grant_id,session_digest,permission_scopes,expires_at,audit_digest)
  values(v_grant.organization_tenant_id,v_grant.id,p_session_digest,v_grant.permission_scopes,v_expiry,p_audit_digest);
  return jsonb_build_object('expires_at',v_expiry,'permission_scopes',v_grant.permission_scopes);
end $$;

create or replace function ops.tour_share_session_grant(p_session_digest text,p_scope text)
returns ops.tour_share_grant language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select g from ops.tour_share_session s join ops.tour_share_grant g on g.id=s.share_grant_id and g.organization_tenant_id=s.organization_tenant_id
  where s.session_digest=p_session_digest and s.expires_at>now() and s.permission_scopes ? p_scope and g.expires_at>now()
    and not exists(select 1 from ops.tour_share_grant_revocation_receipt r where r.organization_tenant_id=g.organization_tenant_id and r.share_grant_id=g.id)
    and not exists(select 1 from ops.tour_share_grant successor where successor.organization_tenant_id=g.organization_tenant_id and successor.rotated_from_grant_id=g.id)
  limit 1;
$$;

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
  ) select jsonb_build_object('as_of',p.as_of,'caveat','Facts only; verify current availability and economics.','stops',coalesce((select jsonb_agg(to_jsonb(stops) order by route_sequence) from stops),'[]'::jsonb)) from projection p;
$$;

create or replace function ops.read_tour_share_map(p_session_digest text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with grant_row as (select (ops.tour_share_session_grant(p_session_digest,'view_map')).*), projection as (
    select p.* from grant_row g join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
    where p.status='approved' and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
      and ops.tour_public_map_projection_ready(p.organization_tenant_id,p.id)
  ) select jsonb_build_object('as_of',p.as_of,'points',coalesce(jsonb_agg(jsonb_build_object(
    'property_ref','property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32),
    'route_sequence',m.route_sequence,'route_label',m.route_label,'latitude',c.latitude::double precision,'longitude',c.longitude::double precision
  ) order by m.route_sequence),'[]'::jsonb))
  from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
  join ops.tour_public_projection_map_point mp on mp.organization_tenant_id=p.organization_tenant_id and mp.projection_id=p.id and mp.property_id=m.property_id and mp.route_version=p.route_version
  join ops.tour_coordinate_entrance_verification_receipt er on er.organization_tenant_id=mp.organization_tenant_id and er.id=mp.entrance_verification_receipt_id and er.property_id=mp.property_id and er.coordinate_candidate_id=mp.coordinate_candidate_id
  join ops.tour_property_coordinate_candidate c on c.organization_tenant_id=mp.organization_tenant_id and c.id=mp.coordinate_candidate_id and c.coordinate_role in ('entrance','driveway','parking_access') and c.review_state='reviewed'
  group by p.as_of;
$$;

create or replace function ops.resolve_tour_public_asset(p_session_digest text,p_asset_ref text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with g as (select (ops.tour_share_session_grant(p_session_digest,'view_packet')).*)
  select jsonb_strip_nulls(jsonb_build_object('media_type',a.media_type,'content_length',a.content_length,'alt',a.alt,'caption',a.caption))
  from g join ops.tour_public_asset a on a.organization_tenant_id=g.organization_tenant_id and a.projection_id=g.projection_id and a.asset_ref=p_asset_ref;
$$;

create or replace function ops.request_tour_pdf_render(p_tenant text,p_actor_id text,p_request jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_projection ops.tour_public_projection%rowtype;
begin
  if jsonb_typeof(p_request)<>'object' or nullif(btrim(p_actor_id),'') is null then raise exception 'tour PDF request is invalid'; end if;
  select * into v_projection from ops.tour_public_projection where organization_tenant_id=p_tenant and id=(p_request->>'projection_id')::uuid and status='approved' for share;
  if not found or v_projection.projection_digest<>(p_request->>'projection_digest') or not exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p_tenant and s.projection_id=v_projection.id and s.canonical_projection_digest=v_projection.projection_digest) then raise exception 'tour PDF requires a sealed projection'; end if;
  if (p_request->>'expected_property_count')::integer<>(select count(*) from ops.tour_property_membership m where m.organization_tenant_id=p_tenant and m.tour_id=v_projection.tour_id and m.route_version=v_projection.route_version) then raise exception 'tour PDF property count mismatch'; end if;
  insert into ops.tour_pdf_render_job(organization_tenant_id,projection_id,requested_by_actor_id,request,projection_digest,packet_digest,template_digest,renderer_digest,qc_ruleset_digest,expected_property_count)
  values(p_tenant,v_projection.id,p_actor_id,p_request,p_request->>'projection_digest',p_request->>'packet_digest',p_request->>'template_digest',p_request->>'renderer_digest',p_request->>'qc_ruleset_digest',(p_request->>'expected_property_count')::integer) returning id into v_id;
  return v_id;
end $$;

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
    'packet',jsonb_build_object('as_of',p.as_of,'caveat','Facts only; verify current availability and economics.','properties',coalesce((select jsonb_agg(to_jsonb(properties) order by route_sequence) from properties),'[]'::jsonb))
  ) from projection p;
$$;

create or replace function ops.record_tour_pdf_render_result(p_tenant text,p_render_job_id uuid,p_status text,p_artifact_ref text,p_artifact_digest text,p_storage_ref text,p_content_length integer,p_page_count integer,p_blocking_finding_count integer,p_qc_run_digest text,p_actor_id text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_job ops.tour_pdf_render_job%rowtype; v_attempt integer; v_id uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_tenant||':'||p_render_job_id::text||':pdf-result',403));
  select * into v_job from ops.tour_pdf_render_job where organization_tenant_id=p_tenant and id=p_render_job_id for share;
  if not found or p_status not in ('review_ready','qc_blocked','failed') or nullif(btrim(p_actor_id),'') is null
     or p_qc_run_digest !~ '^sha256:[a-f0-9]{64}$' or p_blocking_finding_count<0
     or exists(select 1 from ops.tour_pdf_human_review h where h.organization_tenant_id=p_tenant and h.render_job_id=p_render_job_id)
     or (p_status='failed' and (p_artifact_ref is not null or p_artifact_digest is not null or p_storage_ref is not null
       or p_content_length is not null or p_page_count is not null or p_blocking_finding_count<>0))
     or (p_status<>'failed' and (p_artifact_digest !~ '^sha256:[a-f0-9]{64}$'
       or p_page_count<>v_job.expected_property_count or p_content_length<=0
       or (p_status='review_ready' and p_blocking_finding_count<>0)
       or p_artifact_ref !~ '^artifact:tour-pdf:[A-Za-z0-9_-]{16,128}$'
       or p_storage_ref !~ '^tour-pdf/[A-Za-z0-9._/-]{16,400}\.pdf$')) then
    raise exception 'tour PDF render result is invalid';
  end if;
  select coalesce(max(attempt_count),0)+1 into v_attempt from ops.tour_pdf_render_result where organization_tenant_id=p_tenant and render_job_id=p_render_job_id;
  insert into ops.tour_pdf_render_result(organization_tenant_id,render_job_id,status,artifact_ref,artifact_digest,storage_ref,content_length,page_count,blocking_finding_count,qc_run_digest,attempt_count,completed_at)
  values(p_tenant,p_render_job_id,p_status,p_artifact_ref,p_artifact_digest,p_storage_ref,p_content_length,p_page_count,p_blocking_finding_count,p_qc_run_digest,v_attempt,now()) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.read_tour_pdf_artifact_for_download(p_tenant text,p_render_job_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select jsonb_build_object('storage_ref',r.storage_ref,'artifact_digest',r.artifact_digest,'content_length',r.content_length,'media_type','application/pdf')
  from ops.tour_pdf_render_job j join ops.tour_pdf_render_result r on r.organization_tenant_id=j.organization_tenant_id and r.render_job_id=j.id
  join ops.tour_pdf_human_review h on h.organization_tenant_id=j.organization_tenant_id and h.render_job_id=j.id and h.render_result_id=r.id and h.decision='accept'
  where j.organization_tenant_id=p_tenant and j.id=p_render_job_id and nullif(btrim(p_actor_id),'') is not null
  order by r.attempt_count desc,r.id desc limit 1;
$$;

create or replace function ops.read_tour_pdf_artifact_for_review(p_tenant text,p_render_job_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select jsonb_build_object('storage_ref',r.storage_ref,'artifact_digest',r.artifact_digest,'content_length',r.content_length,'media_type','application/pdf')
  from ops.tour_pdf_render_job j join ops.tour_pdf_render_result r on r.organization_tenant_id=j.organization_tenant_id and r.render_job_id=j.id and r.status='review_ready'
  where j.organization_tenant_id=p_tenant and j.id=p_render_job_id and nullif(btrim(p_actor_id),'') is not null
  order by r.attempt_count desc,r.id desc limit 1;
$$;

create or replace function ops.read_tour_pdf_render(p_tenant text,p_render_job_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  select jsonb_strip_nulls(jsonb_build_object('render_job_id',j.id,'status',case when h.decision='accept' then 'available' when h.decision='reject' then 'rejected' else coalesce(r.status,'queued') end,'artifact_ref',r.artifact_ref,'artifact_digest',r.artifact_digest,'projection_digest',j.projection_digest,'template_digest',j.template_digest,'renderer_digest',j.renderer_digest,'qc_ruleset_digest',j.qc_ruleset_digest,'qc_run_digest',r.qc_run_digest,'expected_property_count',j.expected_property_count,'page_count',r.page_count,'blocking_finding_count',r.blocking_finding_count,'attempt_count',r.attempt_count,'created_at',j.created_at,'updated_at',coalesce(r.created_at,j.created_at),'completed_at',r.completed_at,'human_review_state',case when h.decision='accept' then 'accepted' when h.decision='reject' then 'rejected' else 'pending' end,'reviewed_at',h.reviewed_at))
  from ops.tour_pdf_render_job j left join lateral (select * from ops.tour_pdf_render_result where organization_tenant_id=j.organization_tenant_id and render_job_id=j.id order by attempt_count desc,id desc limit 1) r on true
  left join ops.tour_pdf_human_review h on h.organization_tenant_id=j.organization_tenant_id and h.render_job_id=j.id
  where j.organization_tenant_id=p_tenant and j.id=p_render_job_id and nullif(btrim(p_actor_id),'') is not null;
$$;

create or replace function ops.record_tour_pdf_human_review(p_tenant text,p_render_job_id uuid,p_qc_run_digest text,p_decision text,p_reviewed_at timestamptz,p_review_receipt_digest text,p_reason text,p_actor_id text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_result ops.tour_pdf_render_result%rowtype; v_id uuid; v_verified_slug text; v_verified_actor_id text;
begin
  v_verified_slug:=nullif(btrim(current_setting('carr.verified_human_actor_slug',true)),'');
  select id::text into v_verified_actor_id from public.actor where slug=v_verified_slug;
  if v_verified_actor_id is null or v_verified_actor_id is distinct from p_actor_id then
    raise exception 'tour PDF review requires a verified human authority session';
  end if;
  select * into v_result from ops.tour_pdf_render_result where organization_tenant_id=p_tenant and render_job_id=p_render_job_id and status='review_ready' and qc_run_digest=p_qc_run_digest order by attempt_count desc,id desc limit 1 for update;
  if not found or p_decision not in ('accept','reject') or p_review_receipt_digest !~ '^sha256:[a-f0-9]{64}$' or nullif(btrim(p_reason),'') is null or nullif(btrim(p_actor_id),'') is null then raise exception 'tour PDF human review is invalid'; end if;
  insert into ops.tour_pdf_human_review(organization_tenant_id,render_job_id,render_result_id,qc_run_digest,decision,reviewed_at,review_receipt_digest,reason,reviewer_actor_id)
  values(p_tenant,p_render_job_id,v_result.id,p_qc_run_digest,p_decision,p_reviewed_at,p_review_receipt_digest,p_reason,p_actor_id) returning id into v_id;
  return v_id;
end $$;

revoke all on table ops.tour_selection_cart_version,ops.tour_share_session,ops.tour_public_asset,ops.tour_public_projection_map_point,ops.tour_map_promotion_receipt,ops.tour_pdf_render_job,ops.tour_pdf_render_result,ops.tour_pdf_human_review from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.tour_delivery_append_only_guard(),ops.search_tour_properties(text,text,jsonb),ops.append_tour_selection_cart_version(text,uuid,uuid,jsonb,integer,text),ops.read_tour_selection_cart(text,uuid,text),ops.list_tour_library(text,text),ops.read_tour_internal_detail(text,uuid,text),ops.prepare_tour_route_version(text,uuid,uuid,integer,jsonb),ops.read_tour_projection_creation_metadata(text,uuid,uuid,text),ops.read_tour_projection_seal_candidates(text,uuid,text),ops.tour_public_map_projection_evidence_ready(text,uuid),ops.record_tour_map_promotion_receipt(text,uuid,jsonb,text),ops.tour_public_map_projection_ready(text,uuid),ops.issue_tour_share_grant(text,uuid,text,jsonb,timestamp with time zone,text,text),ops.rotate_tour_share_grant(text,uuid,uuid,text,jsonb,timestamp with time zone,text,text),ops.revoke_tour_share_grant(text,uuid,text,text,timestamp with time zone,text),ops.read_tour_sharing_library(text,uuid,text,text,integer),ops.exchange_tour_share_token(text,text,timestamp with time zone,text),ops.tour_share_session_grant(text,text),ops.read_tour_share_packet(text),ops.read_tour_share_map(text),ops.resolve_tour_public_asset(text,text),ops.request_tour_pdf_render(text,text,jsonb),ops.read_tour_packet_for_render(text,uuid,text),ops.record_tour_pdf_render_result(text,uuid,text,text,text,text,integer,integer,integer,text,text),ops.read_tour_pdf_artifact_for_review(text,uuid,text),ops.read_tour_pdf_artifact_for_download(text,uuid,text),ops.read_tour_pdf_render(text,uuid,text),ops.record_tour_pdf_human_review(text,uuid,text,text,timestamp with time zone,text,text,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.search_tour_properties(text,text,jsonb),ops.append_tour_selection_cart_version(text,uuid,uuid,jsonb,integer,text),ops.read_tour_selection_cart(text,uuid,text),ops.list_tour_library(text,text),ops.read_tour_internal_detail(text,uuid,text),ops.prepare_tour_route_version(text,uuid,uuid,integer,jsonb),ops.read_tour_projection_creation_metadata(text,uuid,uuid,text),ops.read_tour_projection_seal_candidates(text,uuid,text),ops.read_tour_sharing_library(text,uuid,text,text,integer),ops.request_tour_pdf_render(text,text,jsonb),ops.read_tour_packet_for_render(text,uuid,text),ops.read_tour_pdf_artifact_for_review(text,uuid,text),ops.read_tour_pdf_artifact_for_download(text,uuid,text),ops.read_tour_pdf_render(text,uuid,text) to carr_writer,carr_authority;
grant execute on function ops.record_tour_map_promotion_receipt(text,uuid,jsonb,text),ops.issue_tour_share_grant(text,uuid,text,jsonb,timestamp with time zone,text,text),ops.rotate_tour_share_grant(text,uuid,uuid,text,jsonb,timestamp with time zone,text,text),ops.revoke_tour_share_grant(text,uuid,text,text,timestamp with time zone,text),ops.record_tour_pdf_render_result(text,uuid,text,text,text,text,integer,integer,integer,text,text),ops.record_tour_pdf_human_review(text,uuid,text,text,timestamp with time zone,text,text,text) to carr_authority;
grant execute on function ops.exchange_tour_share_token(text,text,timestamp with time zone,text),ops.read_tour_share_packet(text),ops.read_tour_share_map(text),ops.resolve_tour_public_asset(text,text) to carr_writer;

commit;
