-- 0428_tour_property_identity_jurisdiction.sql
-- Additive Tour Slice 3 foundation.  A canonical CARR property UUID remains
-- distinct from mutable address, parcel, building, provider, map and route
-- projections.  This migration deliberately has no PostGIS dependency and
-- geometry/context is never a legal or regulatory determination.

begin;

create table if not exists ops.tour_property_identifier_assertion (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  identifier_scheme text not null check (identifier_scheme in ('carr_property','county_parcel','building','listing','provider','legacy')),
  identifier_value text not null check (length(btrim(identifier_value)) > 0),
  normalized_identifier text not null check (length(btrim(normalized_identifier)) > 0),
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  observed_at timestamptz not null,
  confidence text not null check (confidence in ('low','medium','high','unknown')),
  review_state text not null check (review_state in ('unreviewed','reviewed','conflicted','superseded','withdrawn')),
  assertion_digest text not null check (assertion_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  -- Cross-property collisions are preserved as review_state=conflicted instead of failing evidence intake globally.
  -- A property itself cannot repeat the same normalized identifier/scheme assertion.
  unique (organization_tenant_id,property_id,identifier_scheme,normalized_identifier),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

create table if not exists ops.tour_property_identifier_alias (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  identifier_assertion_id uuid not null,
  alias_kind text not null check (alias_kind in ('historical','provider','legacy','display','merged')),
  alias_value text not null check (length(btrim(alias_value)) > 0),
  normalized_alias text not null check (length(btrim(normalized_alias)) > 0),
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  observed_at timestamptz not null,
  alias_digest text not null check (alias_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,identifier_assertion_id,normalized_alias),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,identifier_assertion_id) references ops.tour_property_identifier_assertion (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

create table if not exists ops.tour_property_identity_lineage (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  predecessor_property_id uuid not null,
  successor_property_id uuid not null,
  relationship text not null check (relationship in ('merged_into','split_from','duplicate_of','successor_of')),
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  effective_at timestamptz not null,
  recorded_by_actor_id text not null check (length(btrim(recorded_by_actor_id)) > 0),
  rationale text not null check (length(btrim(rationale)) > 0),
  lineage_digest text not null check (lineage_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,predecessor_property_id,successor_property_id,relationship),
  check (predecessor_property_id <> successor_property_id),
  foreign key (organization_tenant_id,predecessor_property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,successor_property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

create table if not exists ops.tour_property_address_assertion (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  address_value jsonb not null check (jsonb_typeof(address_value)='object'),
  address_role text not null check (address_role in ('site','mailing','former','suite','provider_projection')),
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  observed_at timestamptz not null,
  effective_from timestamptz not null,
  effective_to timestamptz,
  confidence text not null check (confidence in ('low','medium','high','unknown')),
  review_state text not null check (review_state in ('unreviewed','reviewed','conflicted','superseded','withdrawn')),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  check (effective_to is null or effective_to >= effective_from),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

create table if not exists ops.tour_property_parcel_assertion (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  parcel_identifier text not null check (length(btrim(parcel_identifier)) > 0),
  parcel_source_locator text not null check (length(btrim(parcel_source_locator)) > 0),
  source_crs text,
  geometry_digest text check (geometry_digest is null or geometry_digest ~ '^sha256:[a-f0-9]{64}$'),
  geometry_method text not null check (geometry_method in ('authoritative_reference','provider_projection','manual_reference','unknown')),
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  as_of timestamptz not null,
  review_state text not null check (review_state in ('unreviewed','reviewed','conflicted','superseded','withdrawn')),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,property_id,parcel_identifier,parcel_source_locator,as_of),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

create table if not exists ops.tour_property_building_assertion (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  building_identifier text not null check (length(btrim(building_identifier)) > 0),
  building_name text,
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  observed_at timestamptz not null,
  confidence text not null check (confidence in ('low','medium','high','unknown')),
  review_state text not null check (review_state in ('unreviewed','reviewed','conflicted','superseded','withdrawn')),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

-- Provider results are retained only as rights-bound external projections. A
-- canonical property identity is not provider-owned and a geocode never
-- becomes canonical by virtue of arriving from a provider.
create table if not exists ops.tour_property_provider_projection (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  provider text not null check (length(btrim(provider)) > 0),
  projection_kind text not null check (projection_kind in ('geocode','listing','route','map','building','parcel')),
  provider_record_id text not null check (length(btrim(provider_record_id)) > 0),
  payload_digest text not null check (payload_digest ~ '^sha256:[a-f0-9]{64}$'),
  rights_receipt_id uuid not null,
  source_evidence_id uuid not null,
  observed_at timestamptz not null,
  retention_expires_at timestamptz,
  review_state text not null check (review_state in ('unreviewed','reviewed','rejected','superseded')),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,provider,projection_kind,provider_record_id,payload_digest),
  check (retention_expires_at is null or retention_expires_at > observed_at),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id)
);

-- The first service area is deliberately exact: Escambia through Bay County,
-- Florida. Future coverage is a forward migration, not an unconstrained claim.
create table if not exists ops.tour_jurisdiction_dataset (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  jurisdiction_type text not null check (jurisdiction_type='county'),
  state_code text not null check (state_code='FL'),
  county_name text not null check (county_name in ('Escambia','Santa Rosa','Okaloosa','Walton','Bay')),
  authoritative_source_locator text not null check (length(btrim(authoritative_source_locator)) > 0),
  dataset_version text not null check (length(btrim(dataset_version)) > 0),
  source_crs text,
  dataset_digest text not null check (dataset_digest ~ '^sha256:[a-f0-9]{64}$'),
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  as_of timestamptz not null,
  review_state text not null check (review_state in ('unreviewed','reviewed','rejected','superseded')),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,state_code,county_name,dataset_version,dataset_digest),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

create table if not exists ops.tour_property_jurisdiction_assertion (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  jurisdiction_dataset_id uuid not null,
  jurisdiction_name text not null check (length(btrim(jurisdiction_name)) > 0),
  assertion_method text not null check (assertion_method in ('authoritative_identifier','reviewed_spatial_context','manual_review')),
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  as_of timestamptz not null,
  review_state text not null check (review_state in ('unreviewed','reviewed','conflicted','superseded','withdrawn')),
  determination_status text not null default 'context_only' check (determination_status='context_only'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,property_id,jurisdiction_dataset_id,as_of),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,jurisdiction_dataset_id) references ops.tour_jurisdiction_dataset (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

create table if not exists ops.tour_property_coordinate_candidate (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  coordinate_role text not null check (coordinate_role in ('parcel_centroid','building_centroid','address_point','geocoder_candidate','entrance','driveway','parking_access','other')),
  latitude numeric(9,6) not null check (latitude between -90 and 90),
  longitude numeric(9,6) not null check (longitude between -180 and 180),
  precision_class text not null check (precision_class in ('unknown','approximate','parcel','building','address','entrance','surveyed')),
  source_evidence_id uuid not null,
  rights_receipt_id uuid not null,
  provider text,
  observed_at timestamptz not null,
  review_state text not null check (review_state in ('unreviewed','reviewed','rejected','superseded')),
  access_notes text,
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,property_id,coordinate_role,latitude,longitude,source_evidence_id),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,source_evidence_id) references ops.tour_source_evidence (organization_tenant_id,id),
  foreign key (organization_tenant_id,rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id,id)
);

-- A coordinate candidate is not canonical. It remains distinct from identity
-- until a reviewed human entrance verification receipt names it.
create table if not exists ops.tour_coordinate_entrance_verification_receipt (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null,
  coordinate_candidate_id uuid not null,
  verifier_actor_id text not null check (length(btrim(verifier_actor_id)) > 0),
  verified_at timestamptz not null,
  evidence_reference text not null check (length(btrim(evidence_reference)) > 0),
  native_navigation_proof jsonb not null check (jsonb_typeof(native_navigation_proof)='object'),
  receipt_digest text not null check (receipt_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id,id),
  unique (organization_tenant_id,coordinate_candidate_id),
  foreign key (organization_tenant_id,property_id) references ops.tour_property (organization_tenant_id,id),
  foreign key (organization_tenant_id,coordinate_candidate_id) references ops.tour_property_coordinate_candidate (organization_tenant_id,id)
);

create index if not exists tour_property_identifier_assertion_property_idx on ops.tour_property_identifier_assertion (organization_tenant_id,property_id,observed_at desc);
create index if not exists tour_property_coordinate_candidate_property_idx on ops.tour_property_coordinate_candidate (organization_tenant_id,property_id,review_state,observed_at desc);
create index if not exists tour_property_jurisdiction_assertion_property_idx on ops.tour_property_jurisdiction_assertion (organization_tenant_id,property_id,as_of desc);

create or replace function ops.tour_slice3_rights_guard(
  p_tenant text,p_evidence_id uuid,p_rights_id uuid,p_at timestamptz,p_use_class text
) returns void language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_provider text; v_policy_key text;
begin
  select rights_provider,rights_policy_key into v_provider,v_policy_key
    from ops.tour_source_evidence where id=p_evidence_id and organization_tenant_id=p_tenant;
  if not found or v_provider is null or v_policy_key is null then
    raise exception 'Tour Slice 3 requires exact evidence rights lineage';
  end if;
  perform ops.tour_rights_provider_policy_lock(p_tenant,v_provider,v_policy_key);
  if not exists (
    select 1 from ops.tour_rights_receipt r
     where r.id=p_rights_id and r.organization_tenant_id=p_tenant
       and r.id=(select e.rights_receipt_id from ops.tour_source_evidence e where e.id=p_evidence_id and e.organization_tenant_id=p_tenant)
       and r.provider=v_provider and r.policy_key=v_policy_key and r.status='active'
       and r.effective_at<=p_at and (r.expires_at is null or r.expires_at>p_at) and r.revoked_at is null
       and r.allowed_use_classes ? p_use_class
       and not exists (select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.provider=r.provider and newer.policy_key=r.policy_key and newer.receipt_version>r.receipt_version and newer.effective_at<=p_at)
  ) then raise exception 'Tour Slice 3 rights receipt refuses evidence use'; end if;
end $$;

create or replace function ops.tour_slice3_identifier_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.observed_at,'canonical_fact');
  if new.normalized_identifier <> lower(btrim(new.normalized_identifier)) then raise exception 'identifier normalization must be lowercase and trimmed'; end if;
  if exists (select 1 from ops.tour_property_identifier_assertion prior where prior.organization_tenant_id=new.organization_tenant_id and prior.identifier_scheme=new.identifier_scheme and prior.normalized_identifier=new.normalized_identifier and prior.property_id<>new.property_id) then new.review_state:='conflicted'; end if;
  return new;
end $$;

create or replace function ops.tour_slice3_alias_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if not exists (select 1 from ops.tour_property_identifier_assertion i where i.id=new.identifier_assertion_id and i.organization_tenant_id=new.organization_tenant_id and i.property_id=new.property_id) then raise exception 'identifier alias does not match canonical property identity'; end if;
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.observed_at,'canonical_fact');
  return new;
end $$;

create or replace function ops.tour_slice3_lineage_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if new.predecessor_property_id=new.successor_property_id then raise exception 'identity lineage cannot self-reference'; end if;
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.effective_at,'canonical_fact');
  return new;
end $$;

create or replace function ops.tour_slice3_assertion_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.observed_at,'canonical_fact');
  return new;
end $$;

create or replace function ops.tour_slice3_parcel_assertion_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.as_of,'canonical_fact');
  return new;
end $$;

create or replace function ops.tour_slice3_provider_projection_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.observed_at,'provider_projection');
  return new;
end $$;

create or replace function ops.tour_slice3_jurisdiction_dataset_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.as_of,'canonical_fact');
  return new;
end $$;

create or replace function ops.tour_slice3_jurisdiction_assertion_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_dataset ops.tour_jurisdiction_dataset%rowtype;
begin
  select * into v_dataset from ops.tour_jurisdiction_dataset where id=new.jurisdiction_dataset_id and organization_tenant_id=new.organization_tenant_id;
  if not found or v_dataset.review_state<>'reviewed' or v_dataset.as_of>new.as_of or v_dataset.rights_receipt_id<>new.rights_receipt_id or v_dataset.source_evidence_id<>new.source_evidence_id then raise exception 'jurisdiction assertion requires reviewed authoritative dataset, evidence, rights, and as_of'; end if;
  if new.jurisdiction_name<>v_dataset.county_name then raise exception 'jurisdiction assertion name does not match dataset'; end if;
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.as_of,'canonical_fact');
  if new.determination_status<>'context_only' then raise exception 'jurisdiction assertion does not make a legal determination'; end if;
  return new;
end $$;

create or replace function ops.tour_slice3_coordinate_candidate_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform ops.tour_slice3_rights_guard(new.organization_tenant_id,new.source_evidence_id,new.rights_receipt_id,new.observed_at,case when new.provider is null then 'canonical_fact' else 'provider_projection' end);
  if new.provider is not null and new.coordinate_role not in ('geocoder_candidate','address_point','building_centroid','parcel_centroid') then raise exception 'provider coordinate requires non-canonical candidate role'; end if;
  return new;
end $$;

create or replace function ops.tour_slice3_entrance_receipt_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if not exists (select 1 from ops.tour_property_coordinate_candidate c where c.id=new.coordinate_candidate_id and c.organization_tenant_id=new.organization_tenant_id and c.property_id=new.property_id and c.coordinate_role in ('entrance','driveway','parking_access') and c.review_state='reviewed') then raise exception 'entrance verification requires an entrance-compatible coordinate role'; end if;
  return new;
end $$;

drop trigger if exists tour_property_identifier_assertion_rights_guard on ops.tour_property_identifier_assertion;
create trigger tour_property_identifier_assertion_rights_guard before insert on ops.tour_property_identifier_assertion for each row execute function ops.tour_slice3_identifier_guard();
drop trigger if exists tour_property_identifier_alias_rights_guard on ops.tour_property_identifier_alias;
create trigger tour_property_identifier_alias_rights_guard before insert on ops.tour_property_identifier_alias for each row execute function ops.tour_slice3_alias_guard();
drop trigger if exists tour_property_identity_lineage_rights_guard on ops.tour_property_identity_lineage;
create trigger tour_property_identity_lineage_rights_guard before insert on ops.tour_property_identity_lineage for each row execute function ops.tour_slice3_lineage_guard();
drop trigger if exists tour_property_address_assertion_rights_guard on ops.tour_property_address_assertion;
create trigger tour_property_address_assertion_rights_guard before insert on ops.tour_property_address_assertion for each row execute function ops.tour_slice3_assertion_guard();
drop trigger if exists tour_property_parcel_assertion_rights_guard on ops.tour_property_parcel_assertion;
create trigger tour_property_parcel_assertion_rights_guard before insert on ops.tour_property_parcel_assertion for each row execute function ops.tour_slice3_parcel_assertion_guard();
drop trigger if exists tour_property_building_assertion_rights_guard on ops.tour_property_building_assertion;
create trigger tour_property_building_assertion_rights_guard before insert on ops.tour_property_building_assertion for each row execute function ops.tour_slice3_assertion_guard();
drop trigger if exists tour_property_provider_projection_rights_guard on ops.tour_property_provider_projection;
create trigger tour_property_provider_projection_rights_guard before insert on ops.tour_property_provider_projection for each row execute function ops.tour_slice3_provider_projection_guard();
drop trigger if exists tour_jurisdiction_dataset_rights_guard on ops.tour_jurisdiction_dataset;
create trigger tour_jurisdiction_dataset_rights_guard before insert on ops.tour_jurisdiction_dataset for each row execute function ops.tour_slice3_jurisdiction_dataset_guard();
drop trigger if exists tour_property_jurisdiction_assertion_rights_guard on ops.tour_property_jurisdiction_assertion;
create trigger tour_property_jurisdiction_assertion_rights_guard before insert on ops.tour_property_jurisdiction_assertion for each row execute function ops.tour_slice3_jurisdiction_assertion_guard();
drop trigger if exists tour_property_coordinate_candidate_rights_guard on ops.tour_property_coordinate_candidate;
create trigger tour_property_coordinate_candidate_rights_guard before insert on ops.tour_property_coordinate_candidate for each row execute function ops.tour_slice3_coordinate_candidate_guard();
drop trigger if exists tour_coordinate_entrance_verification_receipt_guard on ops.tour_coordinate_entrance_verification_receipt;
create trigger tour_coordinate_entrance_verification_receipt_guard before insert on ops.tour_coordinate_entrance_verification_receipt for each row execute function ops.tour_slice3_entrance_receipt_guard();

create or replace function ops.tour_slice3_reject_mutation() returns trigger language plpgsql as $$ begin raise exception '% is append-only',tg_table_name; end $$;
drop trigger if exists tour_property_identifier_assertion_append_only on ops.tour_property_identifier_assertion;
create trigger tour_property_identifier_assertion_append_only before update or delete on ops.tour_property_identifier_assertion for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_property_identifier_alias_append_only on ops.tour_property_identifier_alias;
create trigger tour_property_identifier_alias_append_only before update or delete on ops.tour_property_identifier_alias for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_property_identity_lineage_append_only on ops.tour_property_identity_lineage;
create trigger tour_property_identity_lineage_append_only before update or delete on ops.tour_property_identity_lineage for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_property_address_assertion_append_only on ops.tour_property_address_assertion;
create trigger tour_property_address_assertion_append_only before update or delete on ops.tour_property_address_assertion for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_property_parcel_assertion_append_only on ops.tour_property_parcel_assertion;
create trigger tour_property_parcel_assertion_append_only before update or delete on ops.tour_property_parcel_assertion for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_property_building_assertion_append_only on ops.tour_property_building_assertion;
create trigger tour_property_building_assertion_append_only before update or delete on ops.tour_property_building_assertion for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_property_provider_projection_append_only on ops.tour_property_provider_projection;
create trigger tour_property_provider_projection_append_only before update or delete on ops.tour_property_provider_projection for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_jurisdiction_dataset_append_only on ops.tour_jurisdiction_dataset;
create trigger tour_jurisdiction_dataset_append_only before update or delete on ops.tour_jurisdiction_dataset for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_property_jurisdiction_assertion_append_only on ops.tour_property_jurisdiction_assertion;
create trigger tour_property_jurisdiction_assertion_append_only before update or delete on ops.tour_property_jurisdiction_assertion for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_property_coordinate_candidate_append_only on ops.tour_property_coordinate_candidate;
create trigger tour_property_coordinate_candidate_append_only before update or delete on ops.tour_property_coordinate_candidate for each row execute function ops.tour_slice3_reject_mutation();
drop trigger if exists tour_coordinate_entrance_verification_receipt_append_only on ops.tour_coordinate_entrance_verification_receipt;
create trigger tour_coordinate_entrance_verification_receipt_append_only before update or delete on ops.tour_coordinate_entrance_verification_receipt for each row execute function ops.tour_slice3_reject_mutation();

-- Typed app seams deliberately expose only the first two persistence paths.
-- Additional relations remain database-governed until their API contracts are
-- independently reviewed; no read or write seam promotes a map or publication.
create or replace function ops.append_tour_property_identifier_assertion(p_payload jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid;
begin
  if jsonb_typeof(p_payload)<>'object' or (select array_agg(k order by k) from jsonb_object_keys(p_payload) k) is distinct from array['assertion_digest','confidence','identifier_scheme','identifier_value','normalized_identifier','observed_at','organization_tenant_id','property_id','review_state','rights_receipt_id','source_evidence_id'] then raise exception 'property identifier assertion payload is invalid'; end if;
  insert into ops.tour_property_identifier_assertion (organization_tenant_id,property_id,identifier_scheme,identifier_value,normalized_identifier,source_evidence_id,rights_receipt_id,observed_at,confidence,review_state,assertion_digest)
  values (p_payload->>'organization_tenant_id',(p_payload->>'property_id')::uuid,p_payload->>'identifier_scheme',p_payload->>'identifier_value',p_payload->>'normalized_identifier',(p_payload->>'source_evidence_id')::uuid,(p_payload->>'rights_receipt_id')::uuid,(p_payload->>'observed_at')::timestamptz,p_payload->>'confidence',p_payload->>'review_state',p_payload->>'assertion_digest') returning id into v_id;
  return v_id;
end $$;

create or replace function ops.append_tour_coordinate_candidate(p_payload jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid;
begin
  if jsonb_typeof(p_payload)<>'object' or (select array_agg(k order by k) from jsonb_object_keys(p_payload) k) is distinct from array['access_notes','coordinate_role','latitude','longitude','observed_at','organization_tenant_id','precision_class','property_id','provider','review_state','rights_receipt_id','source_evidence_id'] then raise exception 'coordinate candidate payload is invalid'; end if;
  insert into ops.tour_property_coordinate_candidate (organization_tenant_id,property_id,coordinate_role,latitude,longitude,precision_class,source_evidence_id,rights_receipt_id,provider,observed_at,review_state,access_notes)
  values (p_payload->>'organization_tenant_id',(p_payload->>'property_id')::uuid,p_payload->>'coordinate_role',(p_payload->>'latitude')::numeric,(p_payload->>'longitude')::numeric,p_payload->>'precision_class',(p_payload->>'source_evidence_id')::uuid,(p_payload->>'rights_receipt_id')::uuid,nullif(p_payload->>'provider',''),(p_payload->>'observed_at')::timestamptz,p_payload->>'review_state',nullif(p_payload->>'access_notes','')) returning id into v_id;
  return v_id;
end $$;

create or replace function ops.append_tour_entrance_verification_receipt(p_payload jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_id uuid; v_verified_slug text; v_verified_actor_id text;
begin
  if jsonb_typeof(p_payload)<>'object' or (select array_agg(k order by k) from jsonb_object_keys(p_payload) k) is distinct from array['coordinate_candidate_id','evidence_reference','native_navigation_proof','organization_tenant_id','property_id','receipt_digest','verified_at','verifier_actor_id'] then raise exception 'entrance verification receipt payload is invalid'; end if;
  v_verified_slug:=nullif(btrim(current_setting('carr.verified_human_actor_slug',true)),'');
  select id::text into v_verified_actor_id from public.actor where slug=v_verified_slug;
  if v_verified_actor_id is null or v_verified_actor_id is distinct from p_payload->>'verifier_actor_id' then
    raise exception 'entrance verification requires a verified human authority session';
  end if;
  insert into ops.tour_coordinate_entrance_verification_receipt (organization_tenant_id,property_id,coordinate_candidate_id,verifier_actor_id,verified_at,evidence_reference,native_navigation_proof,receipt_digest)
  values (p_payload->>'organization_tenant_id',(p_payload->>'property_id')::uuid,(p_payload->>'coordinate_candidate_id')::uuid,p_payload->>'verifier_actor_id',(p_payload->>'verified_at')::timestamptz,p_payload->>'evidence_reference',p_payload->'native_navigation_proof',p_payload->>'receipt_digest') returning id into v_id;
  return v_id;
end $$;

revoke all on function ops.tour_slice3_rights_guard(text,uuid,uuid,timestamptz,text),ops.tour_slice3_identifier_guard(),ops.tour_slice3_alias_guard(),ops.tour_slice3_lineage_guard(),ops.tour_slice3_assertion_guard(),ops.tour_slice3_parcel_assertion_guard(),ops.tour_slice3_provider_projection_guard(),ops.tour_slice3_jurisdiction_dataset_guard(),ops.tour_slice3_jurisdiction_assertion_guard(),ops.tour_slice3_coordinate_candidate_guard(),ops.tour_slice3_entrance_receipt_guard(),ops.tour_slice3_reject_mutation() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on table ops.tour_property_identifier_assertion,ops.tour_property_identifier_alias,ops.tour_property_identity_lineage,ops.tour_property_address_assertion,ops.tour_property_parcel_assertion,ops.tour_property_building_assertion,ops.tour_property_provider_projection,ops.tour_jurisdiction_dataset,ops.tour_property_jurisdiction_assertion,ops.tour_property_coordinate_candidate,ops.tour_coordinate_entrance_verification_receipt from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.append_tour_property_identifier_assertion(jsonb),ops.append_tour_coordinate_candidate(jsonb),ops.append_tour_entrance_verification_receipt(jsonb) from public;
grant execute on function ops.append_tour_property_identifier_assertion(jsonb) to carr_authority;
grant execute on function ops.append_tour_coordinate_candidate(jsonb) to carr_writer;
grant execute on function ops.append_tour_entrance_verification_receipt(jsonb) to carr_authority;

commit;
