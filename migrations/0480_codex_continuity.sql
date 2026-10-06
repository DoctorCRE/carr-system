-- Bounded record-backed continuity for native Codex tasks.
-- Transcript bodies remain in the native client; this migration stores only
-- semantic state, local cursors/references, and append-only lifecycle receipts.

create table codex_continuity_checkpoint (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  owner_actor_id uuid not null references actor(id),
  native_task_id text not null check (native_task_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  project_id text not null check (length(btrim(project_id)) between 1 and 500),
  cwd text not null check (length(btrim(cwd)) between 1 and 1000),
  state jsonb not null check (jsonb_typeof(state)='object' and octet_length(state::text)<=24000),
  cursor jsonb check (cursor is null or (jsonb_typeof(cursor)='object' and octet_length(cursor::text)<=2000)),
  checkpoint_version bigint not null default 1 check (checkpoint_version > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_tenant_id, owner_actor_id, native_task_id)
);
create index codex_continuity_checkpoint_scope_idx
  on codex_continuity_checkpoint(organization_tenant_id, owner_actor_id, native_task_id, project_id, cwd);

create table codex_continuity_revision (
  id uuid primary key default gen_random_uuid(),
  checkpoint_id uuid not null references codex_continuity_checkpoint(id),
  checkpoint_version bigint not null check (checkpoint_version > 0),
  state jsonb not null check (jsonb_typeof(state)='object' and octet_length(state::text)<=24000),
  cursor jsonb check (cursor is null or (jsonb_typeof(cursor)='object' and octet_length(cursor::text)<=2000)),
  created_by_actor_id uuid not null references actor(id),
  created_at timestamptz not null default now(),
  unique (checkpoint_id, checkpoint_version)
);
create index codex_continuity_revision_checkpoint_idx
  on codex_continuity_revision(checkpoint_id, checkpoint_version desc);

create table codex_continuity_event (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  owner_actor_id uuid not null references actor(id),
  native_task_id text not null check (native_task_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  project_id text not null check (length(btrim(project_id)) between 1 and 500),
  cwd text not null check (length(btrim(cwd)) between 1 and 1000),
  event_type text not null check (length(btrim(event_type)) between 1 and 100),
  cursor jsonb check (cursor is null or (jsonb_typeof(cursor)='object' and octet_length(cursor::text)<=2000)),
  transcript_ref text check (transcript_ref is null or length(transcript_ref)<=1000),
  idempotency_key text not null check (length(btrim(idempotency_key)) between 1 and 500),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, owner_actor_id, native_task_id, idempotency_key)
);
create index codex_continuity_event_scope_idx
  on codex_continuity_event(organization_tenant_id, owner_actor_id, native_task_id, created_at desc);

create or replace function codex_continuity_revision_append_only()
returns trigger language plpgsql as $$
begin
  raise exception 'codex continuity revisions are append-only';
end $$;
drop trigger if exists codex_continuity_revision_append_only on codex_continuity_revision;
create trigger codex_continuity_revision_append_only
before update or delete on codex_continuity_revision
for each row execute function codex_continuity_revision_append_only();

create or replace function codex_continuity_checkpoint_timestamps()
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
drop trigger if exists codex_continuity_checkpoint_timestamps on codex_continuity_checkpoint;
create trigger codex_continuity_checkpoint_timestamps
before insert or update on codex_continuity_checkpoint
for each row execute function codex_continuity_checkpoint_timestamps();

create or replace function codex_continuity_checkpoint_identity_immutable()
returns trigger language plpgsql as $$
begin
  if new.organization_tenant_id is distinct from old.organization_tenant_id
     or new.owner_actor_id is distinct from old.owner_actor_id
     or new.native_task_id is distinct from old.native_task_id
     or new.project_id is distinct from old.project_id
     or new.cwd is distinct from old.cwd
     or new.created_at is distinct from old.created_at then
    raise exception 'codex continuity task binding is immutable';
  end if;
  return new;
end $$;
drop trigger if exists codex_continuity_checkpoint_identity_immutable on codex_continuity_checkpoint;
create trigger codex_continuity_checkpoint_identity_immutable
before update on codex_continuity_checkpoint
for each row execute function codex_continuity_checkpoint_identity_immutable();

revoke all on codex_continuity_checkpoint, codex_continuity_revision, codex_continuity_event from public;
grant select on codex_continuity_checkpoint, codex_continuity_revision, codex_continuity_event to carr_reader, carr_writer;
grant insert, update on codex_continuity_checkpoint to carr_writer;
grant insert on codex_continuity_revision, codex_continuity_event to carr_writer;
