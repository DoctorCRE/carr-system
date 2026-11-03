-- rollback: forward-only; retain generated summary provenance.
-- expand-contract: expand; separate metadata does not change captured email or proposals.
-- lock-review: new table only; no source backfill or existing row rewrite.
create table public.email_message_summary (
  activity_id uuid not null references public.activity(id),
  source_hash text not null check (source_hash ~ '^sha256:[a-f0-9]{64}$'),
  generator_version text not null,
  text text,
  status text not null default 'unavailable' check (status in ('available','unavailable')),
  provenance jsonb not null default '{"reason":"not_generated"}'::jsonb,
  attempts integer not null default 0 check (attempts between 0 and 3),
  claim_id uuid,
  claim_until timestamptz,
  updated_at timestamptz not null default now(),
  primary key(activity_id,source_hash,generator_version),
  check ((status='available' and length(text) between 1 and 300) or (status='unavailable' and text is null)),
  check ((claim_id is null) = (claim_until is null))
);
grant select on public.email_message_summary to carr_reader;
grant select,insert,update on public.email_message_summary to carr_writer;
