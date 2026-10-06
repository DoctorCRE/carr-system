-- 0318_tour_operations_foundation.sql
-- Additive, tenant-safe foundation. Canonical facts are append-only and
-- client render data is always derived from normalized public assertions.

begin;

create table if not exists ops.tour_rights_receipt (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  provider text not null, sku text, policy_key text not null, receipt_version integer not null check (receipt_version > 0),
  receipt_digest text not null check (receipt_digest ~ '^sha256:[a-f0-9]{64}$'), terms_url text not null,
  reviewed_at timestamptz not null, reviewer text not null, intended_use text not null,
  allowed_field_classes jsonb not null, allowed_use_classes jsonb not null,
  effective_at timestamptz not null, expires_at timestamptz, revoked_at timestamptz, supersedes_receipt_id uuid,
  status text not null check (status in ('active','expired','revoked','unknown')),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  unique (organization_tenant_id, provider, policy_key, receipt_version),
  constraint tour_rights_receipt_one_successor unique (organization_tenant_id, supersedes_receipt_id),
  foreign key (organization_tenant_id, supersedes_receipt_id) references ops.tour_rights_receipt (organization_tenant_id, id),
  check (jsonb_typeof(allowed_field_classes) = 'array' and jsonb_typeof(allowed_use_classes) = 'array'),
  check (expires_at is null or expires_at > effective_at), check (status <> 'revoked' or revoked_at is not null)
);

create table if not exists ops.tour_property (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  property_status text not null check (property_status in ('active','inactive','withdrawn','unknown')),
  created_at timestamptz not null default now(), retired_at timestamptz,
  unique (organization_tenant_id, id)
);

create table if not exists ops.tour_source_evidence (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  stable_locator text not null, evidence_class text not null check (evidence_class in ('direct_source','linked_artifact','public_mirror','inference')),
  retrieved_at timestamptz not null, retrieval_status text not null check (retrieval_status in ('read','partial','inaccessible','failed')),
  content_digest text not null check (content_digest ~ '^sha256:[a-f0-9]{64}$'), rights_receipt_id uuid not null,
  data_classification text not null check (data_classification in ('public','client_authorized','internal','restricted')),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id, id)
);

create table if not exists ops.tour_field_assertion (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, property_id uuid not null,
  field_key text not null, value jsonb not null, source_evidence_id uuid not null, rights_receipt_id uuid not null,
  observed_at timestamptz not null, effective_from timestamptz not null, effective_to timestamptz,
  confidence text not null check (confidence in ('low','medium','high','unknown')),
  data_classification text not null check (data_classification in ('public','client_authorized','internal','restricted')),
  review_state text not null check (review_state in ('unreviewed','reviewed','conflicted','superseded','withdrawn')),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, property_id) references ops.tour_property (organization_tenant_id, id),
  foreign key (organization_tenant_id, source_evidence_id) references ops.tour_source_evidence (organization_tenant_id, id),
  foreign key (organization_tenant_id, rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id, id),
  check (effective_to is null or effective_to >= effective_from),
  check (jsonb_typeof(value) in ('object','array','string','number','boolean','null'))
);
create index if not exists tour_field_assertion_property_field_active_idx on ops.tour_field_assertion (organization_tenant_id, property_id, field_key, effective_from desc) where review_state in ('unreviewed','reviewed','conflicted');

create table if not exists ops.tour_fact_conflict (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, property_id uuid not null,
  field_key text not null, state text not null check (state in ('open','resolved','superseded')),
  opened_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, property_id) references ops.tour_property (organization_tenant_id, id)
);
create table if not exists ops.tour_fact_conflict_participant (
  conflict_id uuid not null, organization_tenant_id text not null, field_assertion_id uuid not null,
  participant_role text not null check (participant_role in ('candidate','selected','rejected')),
  created_at timestamptz not null default now(), primary key (organization_tenant_id, conflict_id, field_assertion_id),
  foreign key (organization_tenant_id, conflict_id) references ops.tour_fact_conflict (organization_tenant_id, id),
  foreign key (organization_tenant_id, field_assertion_id) references ops.tour_field_assertion (organization_tenant_id, id)
);
create table if not exists ops.tour_conflict_resolution_receipt (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, conflict_id uuid not null,
  selected_field_assertion_id uuid not null, rationale text not null, evidence jsonb not null, resolver_actor_id text not null,
  resolved_at timestamptz not null, receipt_digest text not null check (receipt_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id), unique (organization_tenant_id, conflict_id),
  foreign key (organization_tenant_id, conflict_id) references ops.tour_fact_conflict (organization_tenant_id, id),
  foreign key (organization_tenant_id, selected_field_assertion_id) references ops.tour_field_assertion (organization_tenant_id, id),
  check (jsonb_typeof(evidence) = 'object')
);

create table if not exists ops.tour (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_name text not null,
  tour_status text not null check (tour_status in ('draft','active','completed','cancelled','archived')),
  route_version integer not null check (route_version > 0), canonical_dataset_version text not null,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique (organization_tenant_id, id)
);
create table if not exists ops.tour_property_membership (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_id uuid not null, property_id uuid not null,
  route_version integer not null check (route_version > 0), route_sequence integer not null check (route_sequence > 0), route_label text not null,
  assertion_set_digest text not null check (assertion_set_digest ~ '^sha256:[a-f0-9]{64}$'), selected_at timestamptz not null default now(),
  unique (organization_tenant_id, id), unique (organization_tenant_id, tour_id, route_version, property_id),
  unique (organization_tenant_id, tour_id, route_version, route_sequence), unique (organization_tenant_id, tour_id, route_version, route_label),
  foreign key (organization_tenant_id, tour_id) references ops.tour (organization_tenant_id, id),
  foreign key (organization_tenant_id, property_id) references ops.tour_property (organization_tenant_id, id)
);

create table if not exists ops.tour_public_projection (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_id uuid not null,
  projection_version integer not null check (projection_version > 0), route_version integer not null check (route_version > 0), as_of timestamptz not null,
  facts_only boolean not null check (facts_only), projection_digest text not null check (projection_digest ~ '^sha256:[a-f0-9]{64}$'),
  derived_render_digest text, status text not null check (status in ('draft','qc_blocked','approved','published','superseded','quarantined','rolled_back')),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id), unique (organization_tenant_id, tour_id, projection_version),
  foreign key (organization_tenant_id, tour_id) references ops.tour (organization_tenant_id, id)
);
create table if not exists ops.tour_public_projection_fact (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, projection_id uuid not null,
  property_id uuid not null, field_assertion_id uuid not null, route_version integer not null, display_field_key text not null,
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  unique (organization_tenant_id, projection_id, property_id, display_field_key),
  foreign key (organization_tenant_id, projection_id) references ops.tour_public_projection (organization_tenant_id, id),
  foreign key (organization_tenant_id, property_id) references ops.tour_property (organization_tenant_id, id),
  foreign key (organization_tenant_id, field_assertion_id) references ops.tour_field_assertion (organization_tenant_id, id),
  check (display_field_key in ('display.name','display.address','suite','property_type','size','asking_economics','availability','parking','access','photos','floor_plan','source_attribution','as_of','caveat'))
);
create table if not exists ops.tour_public_projection_seal_receipt (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, projection_id uuid not null,
  sealed_at timestamptz not null, sealed_state text not null check (sealed_state in ('approved','published','quarantined','rolled_back')),
  actor_id text not null, receipt_digest text not null check (receipt_digest ~ '^sha256:[a-f0-9]{64}$'), created_at timestamptz not null default now(),
  unique (organization_tenant_id, id), unique (organization_tenant_id, projection_id),
  foreign key (organization_tenant_id, projection_id) references ops.tour_public_projection (organization_tenant_id, id)
);

create table if not exists ops.tour_cheat_sheet_revision (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_id uuid not null,
  revision_number integer not null check (revision_number > 0), content jsonb not null, editor_actor_id text not null,
  status text not null check (status in ('draft','saved','superseded')), created_at timestamptz not null default now(),
  unique (organization_tenant_id, id), unique (organization_tenant_id, tour_id, revision_number),
  foreign key (organization_tenant_id, tour_id) references ops.tour (organization_tenant_id, id), check (jsonb_typeof(content) = 'object')
);
create table if not exists ops.tour_share_grant (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, projection_id uuid not null,
  grant_version integer not null check (grant_version > 0), token_digest text not null check (token_digest ~ '^sha256:[a-f0-9]{64}$'),
  audience text not null check (audience in ('client','internal')), permission_scopes jsonb not null, rotated_from_grant_id uuid, created_at timestamptz not null default now(), expires_at timestamptz,
  revoked_at timestamptz, status text not null check (status in ('active','revoked','expired','rotated')),
  unique (organization_tenant_id, id), unique (organization_tenant_id, projection_id, grant_version), constraint tour_share_grant_one_successor unique (organization_tenant_id, rotated_from_grant_id), unique (token_digest),
  foreign key (organization_tenant_id, projection_id) references ops.tour_public_projection (organization_tenant_id, id),
  foreign key (organization_tenant_id, rotated_from_grant_id) references ops.tour_share_grant (organization_tenant_id, id),
  check (jsonb_typeof(permission_scopes) = 'array' and jsonb_array_length(permission_scopes) > 0 and permission_scopes <@ '["view_packet","view_map"]'::jsonb), check (expires_at is null or expires_at > created_at), check (status <> 'revoked' or revoked_at is not null)
);
create table if not exists ops.tour_share_grant_revocation_receipt (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, share_grant_id uuid not null,
  revoked_at timestamptz not null, actor_id text not null, reason text not null,
  receipt_digest text not null check (receipt_digest ~ '^sha256:[a-f0-9]{64}$'), created_at timestamptz not null default now(),
  unique (organization_tenant_id, id), unique (organization_tenant_id, share_grant_id),
  foreign key (organization_tenant_id, share_grant_id) references ops.tour_share_grant (organization_tenant_id, id)
);

create table if not exists ops.tour_qc_finding (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  artifact_type text not null check (artifact_type in ('public_projection','pdf','map','cheat_sheet','share_grant')),
  artifact_id uuid not null, check_id text not null, severity text not null check (severity in ('blocker','error','warning','info')),
  state text not null check (state in ('open','accepted_risk','resolved','superseded')), evidence jsonb not null,
  created_at timestamptz not null default now(), resolved_at timestamptz, unique (organization_tenant_id, id), check (jsonb_typeof(evidence) = 'object')
);
create table if not exists ops.tour_publication (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, projection_id uuid not null,
  publication_state text not null check (publication_state in ('draft','pending_qc','approved','published','quarantined','rolled_back')),
  projection_digest text not null check (projection_digest ~ '^sha256:[a-f0-9]{64}$'), actor_id text,
  created_at timestamptz not null default now(), state_changed_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, projection_id) references ops.tour_public_projection (organization_tenant_id, id)
);
create table if not exists ops.tour_audit_event (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, event_type text not null,
  entity_type text not null, entity_id uuid not null, actor_id text, occurred_at timestamptz not null,
  event_digest text not null check (event_digest ~ '^sha256:[a-f0-9]{64}$'), payload jsonb not null,
  created_at timestamptz not null default now(), unique (organization_tenant_id, id), unique (organization_tenant_id, event_digest), check (jsonb_typeof(payload) = 'object')
);

create or replace function ops.tour_reject_mutation() returns trigger language plpgsql as $$ begin raise exception '% is append-only', tg_table_name; end $$;
create or replace function ops.tour_assertion_rights_guard() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from ops.tour_rights_receipt r where r.id=new.rights_receipt_id and r.organization_tenant_id=new.organization_tenant_id and r.status='active' and r.effective_at <= new.observed_at and (r.expires_at is null or r.expires_at > new.observed_at) and r.revoked_at is null and r.allowed_use_classes ? 'canonical_fact' and (r.allowed_field_classes ? new.field_key or r.allowed_field_classes ? '*') and not exists (select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.policy_key=r.policy_key and newer.receipt_version > r.receipt_version and newer.effective_at <= new.observed_at)) then raise exception 'rights receipt refuses asserted field/use'; end if;
  return new;
end $$;
create or replace function ops.tour_source_rights_guard() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from ops.tour_rights_receipt r where r.id=new.rights_receipt_id and r.organization_tenant_id=new.organization_tenant_id and r.status='active' and r.effective_at <= new.retrieved_at and (r.expires_at is null or r.expires_at > new.retrieved_at) and r.revoked_at is null and r.allowed_use_classes ? 'source_intake' and not exists (select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.policy_key=r.policy_key and newer.receipt_version > r.receipt_version and newer.effective_at <= new.retrieved_at)) then raise exception 'rights receipt refuses source intake'; end if;
  return new;
end $$;
create or replace function ops.tour_public_value_safe(p_field_key text, p_value jsonb) returns boolean language sql immutable as $$
  select case
    when p_field_key in ('display.name','display.address','suite','property_type','availability','parking','access','source_attribution','as_of','caveat') then jsonb_typeof(p_value) = 'string'
    when p_field_key in ('size','asking_economics') then jsonb_typeof(p_value) = 'object' and not exists (select 1 from jsonb_each(p_value) e where e.key not in ('value','unit','min','max','currency','period','label') or jsonb_typeof(e.value) not in ('string','number','boolean','null'))
    when p_field_key in ('photos','floor_plan') then jsonb_typeof(p_value) = 'array' and not exists (select 1 from jsonb_array_elements(p_value) item where jsonb_typeof(item) <> 'object' or exists (select 1 from jsonb_each(item) e where e.key not in ('url','alt','caption','source') or jsonb_typeof(e.value) <> 'string'))
    else false end;
$$;
create or replace function ops.tour_rights_lineage_guard() returns trigger language plpgsql as $$
declare prior ops.tour_rights_receipt%rowtype;
begin
  if new.supersedes_receipt_id is null and new.receipt_version <> 1 then raise exception 'rights receipt version requires supersession lineage'; end if;
  if new.supersedes_receipt_id is not null then
    select * into prior from ops.tour_rights_receipt where id=new.supersedes_receipt_id and organization_tenant_id=new.organization_tenant_id for update;
    if not found or prior.policy_key <> new.policy_key or prior.provider <> new.provider or new.receipt_version <> prior.receipt_version + 1 then raise exception 'rights receipt supersession lineage is invalid'; end if;
  end if;
  return new;
end $$;
create or replace function ops.tour_conflict_participant_guard() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from ops.tour_fact_conflict c join ops.tour_field_assertion a on a.id=new.field_assertion_id and a.organization_tenant_id=new.organization_tenant_id where c.id=new.conflict_id and c.organization_tenant_id=new.organization_tenant_id and c.property_id=a.property_id and c.field_key=a.field_key) then raise exception 'conflict participant does not match conflict property and field'; end if;
  return new;
end $$;
create or replace function ops.tour_conflict_resolution_guard() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from ops.tour_fact_conflict_participant p join ops.tour_fact_conflict c on c.id=p.conflict_id and c.organization_tenant_id=p.organization_tenant_id join ops.tour_field_assertion a on a.id=p.field_assertion_id and a.organization_tenant_id=p.organization_tenant_id where p.conflict_id=new.conflict_id and p.organization_tenant_id=new.organization_tenant_id and p.field_assertion_id=new.selected_field_assertion_id and a.property_id=c.property_id and a.field_key=c.field_key) then raise exception 'conflict resolution selection is not a matching participant'; end if;
  return new;
end $$;
create or replace function ops.tour_share_rotation_guard() returns trigger language plpgsql as $$
declare prior ops.tour_share_grant%rowtype;
begin
  if new.rotated_from_grant_id is null and new.grant_version <> 1 then raise exception 'share grant version requires rotation lineage'; end if;
  if new.rotated_from_grant_id is not null then
    if new.rotated_from_grant_id = new.id then raise exception 'share grant cannot rotate itself'; end if;
    select * into prior from ops.tour_share_grant where id=new.rotated_from_grant_id and organization_tenant_id=new.organization_tenant_id for update;
    if not found or prior.projection_id <> new.projection_id or new.grant_version <> prior.grant_version + 1 then raise exception 'share grant rotation lineage is invalid'; end if;
  end if;
  return new;
end $$;
create or replace function ops.tour_projection_fact_guard() returns trigger language plpgsql as $$
declare p_tour_id uuid; p ops.tour_public_projection%rowtype;
begin
  if tg_op <> 'INSERT' then raise exception 'tour_public_projection_fact is append-only'; end if;
  select tour_id into p_tour_id from ops.tour_public_projection where id=new.projection_id and organization_tenant_id=new.organization_tenant_id;
  if not found then raise exception 'projection fact projection is unavailable'; end if;
  perform 1 from ops.tour where id=p_tour_id and organization_tenant_id=new.organization_tenant_id for update;
  select * into p from ops.tour_public_projection where id=new.projection_id and organization_tenant_id=new.organization_tenant_id for update;
  if not exists (select 1 from ops.tour_field_assertion a join ops.tour_rights_receipt r on r.id=a.rights_receipt_id and r.organization_tenant_id=a.organization_tenant_id join ops.tour_property_membership m on m.tour_id=p.tour_id and m.property_id=new.property_id and m.organization_tenant_id=new.organization_tenant_id and m.route_version=p.route_version where p.status='draft' and not exists (select 1 from ops.tour_public_projection_seal_receipt seal where seal.organization_tenant_id=p.organization_tenant_id and seal.projection_id=p.id) and new.route_version=p.route_version and a.id=new.field_assertion_id and a.organization_tenant_id=new.organization_tenant_id and a.property_id=new.property_id and a.field_key=new.display_field_key and a.review_state='reviewed' and a.data_classification='public' and m.selected_at <= p.as_of and a.effective_from <= p.as_of and (a.effective_to is null or a.effective_to > p.as_of) and r.status='active' and r.effective_at <= p.as_of and (r.expires_at is null or r.expires_at > p.as_of) and r.revoked_at is null and r.allowed_use_classes ? 'client_public_display' and (r.allowed_field_classes ? a.field_key or r.allowed_field_classes ? '*') and not exists (select 1 from ops.tour_rights_receipt newer where newer.organization_tenant_id=r.organization_tenant_id and newer.policy_key=r.policy_key and newer.receipt_version > r.receipt_version and newer.effective_at <= p.as_of) and ops.tour_public_value_safe(a.field_key,a.value)) then raise exception 'projection fact lacks current public assertion, rights, or safe value'; end if;
  return new;
end $$;
create or replace function ops.tour_membership_seal_guard() returns trigger language plpgsql as $$
begin
  perform 1 from ops.tour where id=new.tour_id and organization_tenant_id=new.organization_tenant_id for update;
  if exists (select 1 from ops.tour_public_projection p where p.organization_tenant_id=new.organization_tenant_id and p.tour_id=new.tour_id and p.route_version=new.route_version) then raise exception 'tour route version is sealed by an existing projection'; end if;
  return new;
end $$;
create or replace function ops.tour_projection_creation_guard() returns trigger language plpgsql as $$
begin
  perform 1 from ops.tour where id=new.tour_id and organization_tenant_id=new.organization_tenant_id for update;
  if not exists (select 1 from ops.tour_property_membership m where m.organization_tenant_id=new.organization_tenant_id and m.tour_id=new.tour_id and m.route_version=new.route_version) or exists (select 1 from ops.tour_property_membership m where m.organization_tenant_id=new.organization_tenant_id and m.tour_id=new.tour_id and m.route_version=new.route_version and m.selected_at > new.as_of) then raise exception 'projection requires a complete membership set selected by as_of'; end if;
  return new;
end $$;
create or replace function ops.tour_projection_seal_guard() returns trigger language plpgsql as $$
declare p_tour_id uuid; p ops.tour_public_projection%rowtype;
begin
  select tour_id into p_tour_id from ops.tour_public_projection where id=new.projection_id and organization_tenant_id=new.organization_tenant_id;
  if not found then raise exception 'projection seal target is unavailable'; end if;
  perform 1 from ops.tour where id=p_tour_id and organization_tenant_id=new.organization_tenant_id for update;
  select * into p from ops.tour_public_projection where id=new.projection_id and organization_tenant_id=new.organization_tenant_id for update;
  if p.status <> 'draft' or exists (select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id) then raise exception 'projection is not an unsealed draft'; end if;
  return new;
end $$;
create trigger tour_source_evidence_append_only before update or delete on ops.tour_source_evidence for each row execute function ops.tour_reject_mutation();
create trigger tour_field_assertion_append_only before update or delete on ops.tour_field_assertion for each row execute function ops.tour_reject_mutation();
create trigger tour_cheat_sheet_revision_append_only before update or delete on ops.tour_cheat_sheet_revision for each row execute function ops.tour_reject_mutation();
create trigger tour_audit_event_append_only before update or delete on ops.tour_audit_event for each row execute function ops.tour_reject_mutation();
create trigger tour_rights_receipt_append_only before update or delete on ops.tour_rights_receipt for each row execute function ops.tour_reject_mutation();
create trigger tour_property_membership_append_only before update or delete on ops.tour_property_membership for each row execute function ops.tour_reject_mutation();
create trigger tour_public_projection_append_only before update or delete on ops.tour_public_projection for each row execute function ops.tour_reject_mutation();
create trigger tour_public_projection_fact_append_only before update or delete on ops.tour_public_projection_fact for each row execute function ops.tour_reject_mutation();
create trigger tour_share_grant_append_only before update or delete on ops.tour_share_grant for each row execute function ops.tour_reject_mutation();
create trigger tour_public_projection_seal_receipt_append_only before update or delete on ops.tour_public_projection_seal_receipt for each row execute function ops.tour_reject_mutation();
create trigger tour_share_grant_revocation_receipt_append_only before update or delete on ops.tour_share_grant_revocation_receipt for each row execute function ops.tour_reject_mutation();
create trigger tour_fact_conflict_append_only before update or delete on ops.tour_fact_conflict for each row execute function ops.tour_reject_mutation();
create trigger tour_fact_conflict_participant_append_only before update or delete on ops.tour_fact_conflict_participant for each row execute function ops.tour_reject_mutation();
create trigger tour_conflict_resolution_receipt_append_only before update or delete on ops.tour_conflict_resolution_receipt for each row execute function ops.tour_reject_mutation();
create trigger tour_source_rights_guard before insert on ops.tour_source_evidence for each row execute function ops.tour_source_rights_guard();
create trigger tour_assertion_rights_guard before insert on ops.tour_field_assertion for each row execute function ops.tour_assertion_rights_guard();
create trigger tour_membership_seal_guard before insert on ops.tour_property_membership for each row execute function ops.tour_membership_seal_guard();
create trigger tour_projection_creation_guard before insert on ops.tour_public_projection for each row execute function ops.tour_projection_creation_guard();
create trigger tour_projection_fact_guard before insert or update on ops.tour_public_projection_fact for each row execute function ops.tour_projection_fact_guard();
create trigger tour_projection_seal_guard before insert on ops.tour_public_projection_seal_receipt for each row execute function ops.tour_projection_seal_guard();
create trigger tour_rights_lineage_guard before insert on ops.tour_rights_receipt for each row execute function ops.tour_rights_lineage_guard();
create trigger tour_conflict_participant_guard before insert on ops.tour_fact_conflict_participant for each row execute function ops.tour_conflict_participant_guard();
create trigger tour_conflict_resolution_guard before insert on ops.tour_conflict_resolution_receipt for each row execute function ops.tour_conflict_resolution_guard();
create trigger tour_share_rotation_guard before insert on ops.tour_share_grant for each row execute function ops.tour_share_rotation_guard();

comment on table ops.tour_public_projection is 'Client renderer cache metadata only. Facts exist only in normalized tour_public_projection_fact rows.';
comment on table ops.tour_share_grant is 'Stores opaque-token digests and scope/version/rotation lineage; never plaintext tokens.';

-- Behavioral acceptance proof. The nested exception rolls every synthetic row
-- back; this proves constraints without leaving fixture data in any environment.
do $$
begin
  begin
    insert into ops.tour_rights_receipt (id,organization_tenant_id,provider,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status)
    values ('10000000-0000-4000-8000-000000000001','tour-proof','proof','county',1,'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','https://example.invalid/terms',now(),'proof','tour', '["display.name"]','["source_intake","canonical_fact"]',now()-interval '2 hours','active');
    insert into ops.tour_rights_receipt (id,organization_tenant_id,provider,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status,supersedes_receipt_id)
    values ('10000000-0000-4000-8000-000000000002','tour-proof','proof','county',2,'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','https://example.invalid/terms',now(),'proof','tour', '["display.name"]','["source_intake","canonical_fact","client_public_display"]',now()-interval '1 hour','active','10000000-0000-4000-8000-000000000001');
    begin insert into ops.tour_rights_receipt (id,organization_tenant_id,provider,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status,supersedes_receipt_id) values ('10000000-0000-4000-8000-000000000003','tour-proof','proof','county',3,'sha256:abababababababababababababababababababababababababababababababab','https://example.invalid/terms',now(),'proof','tour','["display.name"]','["source_intake","canonical_fact"]',now(),'active','10000000-0000-4000-8000-000000000001'); raise exception 'proof expected skipped rights version denial'; exception when raise_exception then if sqlerrm <> 'rights receipt supersession lineage is invalid' then raise; end if; end;
    begin insert into ops.tour_rights_receipt (id,organization_tenant_id,provider,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status,supersedes_receipt_id) values ('10000000-0000-4000-8000-000000000004','tour-proof','proof','county',2,'sha256:acacacacacacacacacacacacacacacacacacacacacacacacacacacacacacacac','https://example.invalid/terms',now(),'proof','tour','["display.name"]','["source_intake","canonical_fact"]',now(),'active','10000000-0000-4000-8000-000000000001'); raise exception 'proof expected forked rights successor denial'; exception when unique_violation then null; end;
    begin
      insert into ops.tour_source_evidence (organization_tenant_id,stable_locator,evidence_class,retrieved_at,retrieval_status,content_digest,rights_receipt_id,data_classification) values ('tour-proof','https://example.invalid/old','direct_source',now(),'read','sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc','10000000-0000-4000-8000-000000000001','public');
      raise exception 'proof expected superseded rights denial';
    exception when raise_exception then if sqlerrm <> 'rights receipt refuses source intake' then raise; end if; end;
    insert into ops.tour_property (id,organization_tenant_id,property_status) values ('10000000-0000-4000-8000-000000000010','tour-proof','active'),('10000000-0000-4000-8000-000000000011','tour-proof','active');
    insert into ops.tour_source_evidence (id,organization_tenant_id,stable_locator,evidence_class,retrieved_at,retrieval_status,content_digest,rights_receipt_id,data_classification) values ('10000000-0000-4000-8000-000000000020','tour-proof','https://example.invalid/current','direct_source',now(),'read','sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd','10000000-0000-4000-8000-000000000002','public');
    insert into ops.tour_field_assertion (id,organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,confidence,data_classification,review_state) values ('10000000-0000-4000-8000-000000000030','tour-proof','10000000-0000-4000-8000-000000000010','display.name','"Proof"','10000000-0000-4000-8000-000000000020','10000000-0000-4000-8000-000000000002',now(),now(),'high','public','reviewed'),('10000000-0000-4000-8000-000000000031','tour-proof','10000000-0000-4000-8000-000000000011','display.name','"Other"','10000000-0000-4000-8000-000000000020','10000000-0000-4000-8000-000000000002',now(),now(),'high','public','reviewed');
    insert into ops.tour (id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version) values ('10000000-0000-4000-8000-000000000040','tour-proof','Proof','draft',1,'proof');
    begin
      insert into ops.tour_property_membership (organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest) values ('other-proof','10000000-0000-4000-8000-000000000040','10000000-0000-4000-8000-000000000010',1,1,'A','sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee');
      raise exception 'proof expected cross tenant denial';
    exception when foreign_key_violation then null; end;
    insert into ops.tour_property_membership (id,organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest) values ('10000000-0000-4000-8000-000000000050','tour-proof','10000000-0000-4000-8000-000000000040','10000000-0000-4000-8000-000000000010',1,1,'A','sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee');
    insert into ops.tour_public_projection (id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status) values ('10000000-0000-4000-8000-000000000060','tour-proof','10000000-0000-4000-8000-000000000040',1,1,now(),true,'sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff','draft');
    insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000060','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000030',1,'display.name');
    begin insert into ops.tour_property_membership (organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest) values ('tour-proof','10000000-0000-4000-8000-000000000040','10000000-0000-4000-8000-000000000011',1,2,'B','sha256:1515151515151515151515151515151515151515151515151515151515151515'); raise exception 'proof expected late membership denial'; exception when raise_exception then if sqlerrm <> 'tour route version is sealed by an existing projection' then raise; end if; end;
    begin
      insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000060','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000030',1,'display.address');
      raise exception 'proof expected projection relabel denial';
    exception when raise_exception then if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if; end;
    insert into ops.tour_field_assertion (id,organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,confidence,data_classification,review_state) values ('10000000-0000-4000-8000-000000000032','tour-proof','10000000-0000-4000-8000-000000000010','display.name','"Draft"','10000000-0000-4000-8000-000000000020','10000000-0000-4000-8000-000000000002',now(),now(),'high','public','unreviewed'),('10000000-0000-4000-8000-000000000033','tour-proof','10000000-0000-4000-8000-000000000010','display.name','"Internal"','10000000-0000-4000-8000-000000000020','10000000-0000-4000-8000-000000000002',now(),now(),'high','internal','reviewed');
    begin insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000060','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000032',1,'display.name'); raise exception 'proof expected unreviewed projection denial'; exception when raise_exception then if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if; end;
    begin insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000060','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000033',1,'display.name'); raise exception 'proof expected internal projection denial'; exception when raise_exception then if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if; end;
    insert into ops.tour_field_assertion (id,organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,effective_to,confidence,data_classification,review_state) values ('10000000-0000-4000-8000-000000000034','tour-proof','10000000-0000-4000-8000-000000000010','display.name','"Future"','10000000-0000-4000-8000-000000000020','10000000-0000-4000-8000-000000000002',now(),now()+interval '1 hour',null,'high','public','reviewed'),('10000000-0000-4000-8000-000000000035','tour-proof','10000000-0000-4000-8000-000000000010','display.name','"Expired"','10000000-0000-4000-8000-000000000020','10000000-0000-4000-8000-000000000002',now(),now()-interval '2 hour',now()-interval '1 hour','high','public','reviewed'),('10000000-0000-4000-8000-000000000036','tour-proof','10000000-0000-4000-8000-000000000010','display.name','{"text":"Leak","internal_note":"secret"}','10000000-0000-4000-8000-000000000020','10000000-0000-4000-8000-000000000002',now(),now(),null,'high','public','reviewed');
    begin insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000060','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000034',1,'display.name'); raise exception 'proof expected future assertion denial'; exception when raise_exception then if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if; end;
    begin insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000060','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000035',1,'display.name'); raise exception 'proof expected expired assertion denial'; exception when raise_exception then if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if; end;
    begin insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000060','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000036',1,'display.name'); raise exception 'proof expected nested secret denial'; exception when raise_exception then if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if; end;
    begin update ops.tour_source_evidence set stable_locator='https://example.invalid/changed' where id='10000000-0000-4000-8000-000000000020'; raise exception 'proof expected append only denial'; exception when raise_exception then if sqlerrm <> 'tour_source_evidence is append-only' then raise; end if; end;
    begin update ops.tour_property_membership set route_sequence=2 where id='10000000-0000-4000-8000-000000000050'; raise exception 'proof expected membership rewrite denial'; exception when raise_exception then if sqlerrm <> 'tour_property_membership is append-only' then raise; end if; end;
    begin update ops.tour_public_projection set projection_digest='sha256:abababababababababababababababababababababababababababababababab' where id='10000000-0000-4000-8000-000000000060'; raise exception 'proof expected projection rewrite denial'; exception when raise_exception then if sqlerrm <> 'tour_public_projection is append-only' then raise; end if; end;
    begin update ops.tour_public_projection_fact set display_field_key='display.address' where projection_id='10000000-0000-4000-8000-000000000060'; raise exception 'proof expected projection fact rewrite denial'; exception when raise_exception then if sqlerrm <> 'tour_public_projection_fact is append-only' then raise; end if; end;
    insert into ops.tour_public_projection_seal_receipt (organization_tenant_id,projection_id,sealed_at,sealed_state,actor_id,receipt_digest) values ('tour-proof','10000000-0000-4000-8000-000000000060',now(),'approved','proof','sha256:1616161616161616161616161616161616161616161616161616161616161616');
    begin insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000060','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000030',1,'display.name'); raise exception 'proof expected sealed projection fact denial'; exception when raise_exception then if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if; end;
    insert into ops.tour_public_projection (id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status) values ('10000000-0000-4000-8000-000000000061','tour-proof','10000000-0000-4000-8000-000000000040',2,1,now(),true,'sha256:1717171717171717171717171717171717171717171717171717171717171717','approved');
    begin insert into ops.tour_public_projection_fact (organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-proof','10000000-0000-4000-8000-000000000061','10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000030',1,'display.name'); raise exception 'proof expected non-draft projection fact denial'; exception when raise_exception then if sqlerrm <> 'projection fact lacks current public assertion, rights, or safe value' then raise; end if; end;
    insert into ops.tour_fact_conflict (id,organization_tenant_id,property_id,field_key,state) values ('10000000-0000-4000-8000-000000000070','tour-proof','10000000-0000-4000-8000-000000000010','display.name','open');
    begin insert into ops.tour_fact_conflict_participant (organization_tenant_id,conflict_id,field_assertion_id,participant_role) values ('tour-proof','10000000-0000-4000-8000-000000000070','10000000-0000-4000-8000-000000000031','candidate'); raise exception 'proof expected conflict mismatch denial'; exception when raise_exception then if sqlerrm <> 'conflict participant does not match conflict property and field' then raise; end if; end;
    insert into ops.tour_fact_conflict_participant (organization_tenant_id,conflict_id,field_assertion_id,participant_role) values ('tour-proof','10000000-0000-4000-8000-000000000070','10000000-0000-4000-8000-000000000030','candidate');
    insert into ops.tour_conflict_resolution_receipt (organization_tenant_id,conflict_id,selected_field_assertion_id,rationale,evidence,resolver_actor_id,resolved_at,receipt_digest) values ('tour-proof','10000000-0000-4000-8000-000000000070','10000000-0000-4000-8000-000000000030','proof','{}','proof',now(),'sha256:1212121212121212121212121212121212121212121212121212121212121212');
    begin insert into ops.tour_conflict_resolution_receipt (organization_tenant_id,conflict_id,selected_field_assertion_id,rationale,evidence,resolver_actor_id,resolved_at,receipt_digest) values ('tour-proof','10000000-0000-4000-8000-000000000070','10000000-0000-4000-8000-000000000031','proof mismatch','{}','proof',now(),'sha256:1313131313131313131313131313131313131313131313131313131313131313'); raise exception 'proof expected conflict resolution denial'; exception when raise_exception then if sqlerrm <> 'conflict resolution selection is not a matching participant' then raise; end if; end;
    begin insert into ops.tour_conflict_resolution_receipt (organization_tenant_id,conflict_id,selected_field_assertion_id,rationale,evidence,resolver_actor_id,resolved_at,receipt_digest) values ('tour-proof','10000000-0000-4000-8000-000000000070','10000000-0000-4000-8000-000000000030','proof duplicate','{}','proof',now(),'sha256:1414141414141414141414141414141414141414141414141414141414141414'); raise exception 'proof expected second conflict resolution denial'; exception when unique_violation then null; end;
    insert into ops.tour_share_grant (id,organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,status) values ('10000000-0000-4000-8000-000000000080','tour-proof','10000000-0000-4000-8000-000000000060',1,'sha256:1111111111111111111111111111111111111111111111111111111111111111','client','["view_packet"]','active');
    begin insert into ops.tour_share_grant (organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,status,rotated_from_grant_id) values ('tour-proof','10000000-0000-4000-8000-000000000060',3,'sha256:1818181818181818181818181818181818181818181818181818181818181818','client','["view_packet"]','active','10000000-0000-4000-8000-000000000080'); raise exception 'proof expected skipped share version denial'; exception when raise_exception then if sqlerrm <> 'share grant rotation lineage is invalid' then raise; end if; end;
    insert into ops.tour_share_grant (id,organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,status,rotated_from_grant_id) values ('10000000-0000-4000-8000-000000000081','tour-proof','10000000-0000-4000-8000-000000000060',2,'sha256:1919191919191919191919191919191919191919191919191919191919191919','client','["view_packet"]','active','10000000-0000-4000-8000-000000000080');
    begin insert into ops.tour_share_grant (organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,status,rotated_from_grant_id) values ('tour-proof','10000000-0000-4000-8000-000000000060',2,'sha256:1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a','client','["view_packet"]','active','10000000-0000-4000-8000-000000000080'); raise exception 'proof expected forked share successor denial'; exception when unique_violation then null; end;
    begin update ops.tour_share_grant set status='revoked' where id='10000000-0000-4000-8000-000000000080'; raise exception 'proof expected share rewrite denial'; exception when raise_exception then if sqlerrm <> 'tour_share_grant is append-only' then raise; end if; end;
    begin delete from ops.tour_share_grant where id='10000000-0000-4000-8000-000000000080'; raise exception 'proof expected share delete denial'; exception when raise_exception then if sqlerrm <> 'tour_share_grant is append-only' then raise; end if; end;
    begin insert into ops.tour_share_grant (organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,status,rotated_from_grant_id) values ('tour-proof','10000000-0000-4000-8000-000000000060',2,'sha256:2222222222222222222222222222222222222222222222222222222222222222','client','["edit_cheat_sheet"]','active','10000000-0000-4000-8000-000000000080'); raise exception 'proof expected unsafe scope denial'; exception when check_violation then null; end;
    begin insert into ops.tour_share_grant (id,organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,status,rotated_from_grant_id) values ('10000000-0000-4000-8000-000000000082','tour-proof','10000000-0000-4000-8000-000000000060',2,'sha256:2323232323232323232323232323232323232323232323232323232323232323','client','["view_packet"]','active','10000000-0000-4000-8000-000000000082'); raise exception 'proof expected rotation self denial'; exception when raise_exception then if sqlerrm <> 'share grant cannot rotate itself' then raise; end if; end;
    raise exception 'tour foundation acceptance proof rollback';
  exception when raise_exception then
    if sqlerrm <> 'tour foundation acceptance proof rollback' then raise; end if;
  end;
end $$;

commit;
-- Rollback is forward-only: revoke grants and quarantine projections. Do not drop evidence-bearing tables.
