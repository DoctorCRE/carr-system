-- rollback: forward-only; preserve candidate evidence and invalidation history.
-- expand-contract: expand with candidate-scoped evidence and promotion guard.
-- lock-review: new tables and candidate trigger; no existing row rewrite.
alter table public.candidate_pool add column evidence_version integer not null default 0;
alter table public.candidate_pool add column source_invalid boolean not null default false;
create table public.email_candidate_signature (
  id uuid primary key default gen_random_uuid(),
  pool_id uuid not null references public.candidate_pool(id),
  candidate_version integer not null,
  mailbox_owner text not null check (mailbox_owner in ('joe','dell')),
  mailbox_account text not null,
  source_id text not null check (source_id ~ '^[a-f0-9]{64}$'),
  item_ids text[] not null,
  raw_digest text not null check (raw_digest ~ '^[a-f0-9]{64}$'),
  extraction_version text not null,
  extraction jsonb not null,
  observed_at timestamptz not null,
  invalidated_at timestamptz,
  invalid_reason text,
  lifecycle jsonb,
  unique(pool_id,mailbox_account,source_id,raw_digest,extraction_version)
);
create index email_candidate_signature_page on public.email_candidate_signature(pool_id,id);
create index email_candidate_signature_identity on public.email_candidate_signature(mailbox_owner,mailbox_account);
create index email_candidate_signature_items on public.email_candidate_signature using gin(item_ids);
grant select on public.email_candidate_signature to carr_reader,carr_writer;
grant insert,update on public.email_candidate_signature to carr_writer;

create function public.guard_candidate_signature_source() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  if old.status='pool' and new.status in ('promoted','confirmed') and old.source_invalid then
    raise exception 'email_candidate_source_invalid' using errcode='23514';
  end if;
  return new;
end $$;
revoke all on function public.guard_candidate_signature_source() from public;
create trigger candidate_signature_source_guard before update on public.candidate_pool
for each row execute function public.guard_candidate_signature_source();
