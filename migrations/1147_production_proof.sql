-- rollback: forward-only; retain signed deployment and execution history.
-- expand-contract: expand; new immutable receipts do not change completion gates.
-- lock-review: new tables only; no existing business rows are locked or rewritten.
create table public.production_proof_runner (
  id text primary key,
  actor_id uuid not null references public.actor(id),
  public_key text not null,
  runner_revision text not null check (runner_revision ~ '^[0-9a-f]{40}$'),
  repo text not null check (repo in ('DoctorCRE/carr-system','jbookout/doctorcre-app')),
  target text not null check (target in ('worker','app','studio')),
  operations text[] not null check (operations <@ array['deployment','reservation','receipt']::text[]),
  enabled boolean not null default true,
  unique (id,actor_id,public_key,runner_revision,repo,target)
);
create table public.production_proof_record (
  id uuid primary key default gen_random_uuid(),
  sequence bigint generated always as identity unique,
  kind text not null check (kind in ('deployment','reservation','receipt')),
  repo text not null,
  merge_sha text not null check (merge_sha ~ '^[0-9a-f]{40}$'),
  target text not null check (target in ('worker','app','studio')),
  requirement_digest text not null check (requirement_digest ~ '^sha256:[0-9a-f]{64}$'),
  deployment_id uuid references public.production_proof_record(id),
  attempt_id uuid unique references public.production_proof_record(id),
  assertion_id text,
  payload jsonb not null check (octet_length(payload::text) <= 262144),
  payload_digest text not null check (payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  runner_id text not null references public.production_proof_runner(id),
  actor_id uuid not null references public.actor(id),
  public_key text not null,
  runner_revision text not null check (runner_revision ~ '^[0-9a-f]{40}$'),
  signature text not null,
  idempotency_key uuid not null,
  recorded_at timestamptz not null default clock_timestamp(),
  unique (actor_id,idempotency_key),
  foreign key (runner_id,actor_id,public_key,runner_revision,repo,target)
    references public.production_proof_runner(id,actor_id,public_key,runner_revision,repo,target),
  check ((kind='deployment' and deployment_id is null and attempt_id is null and assertion_id is null)
    or (kind='reservation' and deployment_id is not null and attempt_id is null and assertion_id is not null)
    or (kind='receipt' and deployment_id is not null and attempt_id is not null and assertion_id is not null))
);
create index production_proof_binding on public.production_proof_record(repo,merge_sha,target,kind,sequence desc);
create index production_proof_release on public.production_proof_record(repo,target,sequence desc) where kind='deployment';
create index production_proof_attempt on public.production_proof_record(deployment_id,assertion_id,sequence desc) where kind='reservation';
create function public.production_proof_immutable() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  raise exception 'production_proof_immutable' using errcode='23514';
end $$;
revoke all on function public.production_proof_immutable() from public;
create trigger production_proof_no_changes before update or delete on public.production_proof_record
for each row execute function public.production_proof_immutable();
create trigger production_proof_no_truncate before truncate on public.production_proof_record
for each statement execute function public.production_proof_immutable();
revoke all on public.production_proof_runner,public.production_proof_record from public,carr_reader,carr_writer;
grant select on public.production_proof_runner,public.production_proof_record to carr_reader,carr_writer;
grant insert on public.production_proof_record to carr_writer;
grant usage on sequence public.production_proof_record_sequence_seq to carr_writer;
