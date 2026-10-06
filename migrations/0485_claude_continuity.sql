-- Surface-isolated, record-backed continuity for native Claude session leaves.
-- Native transcripts stay local.  The record layer stores only bounded semantic
-- checkpoints, immutable lifecycle receipts, and server-bound leaf metadata.

create table claude_continuity_leaf (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  surface_principal_actor_id uuid not null references actor(id),
  owner_actor_id uuid not null references actor(id),
  session_id text not null check (session_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  transcript_path_digest text not null check (transcript_path_digest ~ '^[0-9a-f]{64}$'),
  project_affinity text not null check (length(btrim(project_affinity)) between 1 and 500),
  parent_session_id text check (parent_session_id is null or length(btrim(parent_session_id)) between 1 and 200),
  native_agent_id text check (native_agent_id is null or length(btrim(native_agent_id)) between 1 and 200),
  latest_cwd text check (latest_cwd is null or length(btrim(latest_cwd)) between 1 and 1000),
  latest_model_id text check (latest_model_id is null or length(btrim(latest_model_id)) between 1 and 200),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_tenant_id, surface_principal_actor_id, owner_actor_id, session_id, transcript_path_digest),
  unique (id, organization_tenant_id, surface_principal_actor_id)
);
create index claude_continuity_leaf_scope_idx on claude_continuity_leaf
  (organization_tenant_id, surface_principal_actor_id, owner_actor_id, session_id, transcript_path_digest);

create table claude_continuity_checkpoint (
  id uuid primary key default gen_random_uuid(),
  leaf_id uuid not null unique references claude_continuity_leaf(id),
  state jsonb not null check (jsonb_typeof(state)='object' and octet_length(state::text)<=24000),
  cursor jsonb not null check (jsonb_typeof(cursor)='object' and octet_length(cursor::text)<=2000),
  transcript_digest text check (transcript_digest is null or transcript_digest ~ '^[0-9a-f]{64}$'),
  source_observed_at timestamptz not null,
  checkpoint_version bigint not null default 1 check (checkpoint_version between 1 and 9007199254740991),
  compaction_generation bigint not null default 0 check (compaction_generation between 0 and 9007199254740991),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table claude_continuity_revision (
  id uuid primary key default gen_random_uuid(),
  checkpoint_id uuid not null references claude_continuity_checkpoint(id),
  checkpoint_version bigint not null check (checkpoint_version between 1 and 9007199254740991),
  state jsonb not null check (jsonb_typeof(state)='object' and octet_length(state::text)<=24000),
  cursor jsonb not null check (jsonb_typeof(cursor)='object' and octet_length(cursor::text)<=2000),
  transcript_digest text check (transcript_digest is null or transcript_digest ~ '^[0-9a-f]{64}$'),
  source_observed_at timestamptz not null,
  compaction_generation bigint not null check (compaction_generation between 0 and 9007199254740991),
  created_by_actor_id uuid not null references actor(id),
  created_at timestamptz not null default now(),
  unique (checkpoint_id, checkpoint_version)
);
create index claude_continuity_revision_checkpoint_idx on claude_continuity_revision
  (checkpoint_id, checkpoint_version desc);

create table claude_continuity_event (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  surface_principal_actor_id uuid not null references actor(id),
  leaf_id uuid not null,
  event_type text not null check (event_type in ('user_prompt_submit','post_tool_use','pre_compact','stop')),
  cursor jsonb not null check (jsonb_typeof(cursor)='object' and octet_length(cursor::text)<=2000),
  transcript_digest text check (transcript_digest is null or transcript_digest ~ '^[0-9a-f]{64}$'),
  observed_at timestamptz not null,
  telemetry jsonb check (telemetry is null or (jsonb_typeof(telemetry)='object' and octet_length(telemetry::text)<=4000)),
  checkpoint_version bigint not null default 0 check (checkpoint_version between 0 and 9007199254740991),
  idempotency_key text not null check (length(btrim(idempotency_key)) between 1 and 500),
  created_at timestamptz not null default now(),
  foreign key (leaf_id, organization_tenant_id, surface_principal_actor_id)
    references claude_continuity_leaf(id, organization_tenant_id, surface_principal_actor_id),
  unique (organization_tenant_id, surface_principal_actor_id, idempotency_key)
);
create index claude_continuity_event_leaf_idx on claude_continuity_event
  (leaf_id, created_at desc, id desc);

create or replace function claude_continuity_leaf_timestamps()
returns trigger language plpgsql as $$
begin
  if tg_op='INSERT' then
    new.created_at := now();
    new.updated_at := new.created_at;
  else
    new.created_at := old.created_at;
    new.updated_at := now();
  end if;
  return new;
end $$;
create trigger claude_continuity_leaf_timestamps before insert or update on claude_continuity_leaf
for each row execute function claude_continuity_leaf_timestamps();

create or replace function claude_continuity_leaf_identity_immutable()
returns trigger language plpgsql as $$
begin
  if new.organization_tenant_id is distinct from old.organization_tenant_id
     or new.surface_principal_actor_id is distinct from old.surface_principal_actor_id
     or new.owner_actor_id is distinct from old.owner_actor_id
     or new.session_id is distinct from old.session_id
     or new.transcript_path_digest is distinct from old.transcript_path_digest
     or new.project_affinity is distinct from old.project_affinity
     or new.parent_session_id is distinct from old.parent_session_id
     or new.native_agent_id is distinct from old.native_agent_id
     or new.created_at is distinct from old.created_at then
    raise exception 'claude continuity leaf binding is immutable';
  end if;
  return new;
end $$;
create trigger claude_continuity_leaf_identity_immutable before update on claude_continuity_leaf
for each row execute function claude_continuity_leaf_identity_immutable();

create or replace function claude_continuity_checkpoint_guard()
returns trigger language plpgsql as $$
begin
  if tg_op='INSERT' then
    new.created_at := now();
    new.updated_at := new.created_at;
  else
    if new.leaf_id is distinct from old.leaf_id or new.created_at is distinct from old.created_at then
      raise exception 'claude continuity checkpoint leaf binding is immutable';
    end if;
    if new.compaction_generation < old.compaction_generation then
      raise exception 'claude continuity compaction generation cannot regress';
    end if;
    new.updated_at := now();
  end if;
  return new;
end $$;
create trigger claude_continuity_checkpoint_guard before insert or update on claude_continuity_checkpoint
for each row execute function claude_continuity_checkpoint_guard();

create or replace function claude_continuity_revision_append_only()
returns trigger language plpgsql as $$
begin
  raise exception 'claude continuity revisions are append-only';
end $$;
create trigger claude_continuity_revision_append_only before update or delete on claude_continuity_revision
for each row execute function claude_continuity_revision_append_only();

revoke all on claude_continuity_leaf, claude_continuity_checkpoint,
  claude_continuity_revision, claude_continuity_event from public;
grant select on claude_continuity_leaf, claude_continuity_checkpoint,
  claude_continuity_revision, claude_continuity_event to carr_reader, carr_writer;
grant insert, update on claude_continuity_leaf, claude_continuity_checkpoint to carr_writer;
grant insert on claude_continuity_revision, claude_continuity_event to carr_writer;
