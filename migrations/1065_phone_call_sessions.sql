-- rollback: forward-only; retain admitted phone sessions and their review history.
-- expand-contract: expand; metadata beside the existing capture workflow.
-- lock-review: new empty table and indexes only; no existing table rewrite.
create table phone_call_session (
  id uuid primary key references capture_session(id),
  owner_id uuid not null references actor(id),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  byte_count bigint not null check (byte_count > 0),
  duration_seconds numeric not null check (duration_seconds > 0),
  mime_type text not null check (mime_type in
    ('audio/wav','audio/x-wav','audio/mp4','audio/m4a','audio/x-m4a','audio/mpeg','audio/webm')),
  started_at timestamptz not null,
  consent_announced_at timestamptz not null,
  admitted_on date not null default ((now() at time zone 'America/Chicago')::date),
  created_at timestamptz not null default now(),
  transferred_at timestamptz,
  projection jsonb,
  unique (owner_id,sha256)
);
create index phone_call_session_daily on phone_call_session (owner_id,admitted_on);
grant select,insert,update on phone_call_session to carr_writer;
