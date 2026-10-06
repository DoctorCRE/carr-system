-- W5: sourced vendor/deal associations and an audited, partner-authored trust override.
-- No historical attribution is guessed or backfilled by this migration.
alter table public.vendor add column if not exists loan_programs text[];
alter table public.vendor add column if not exists deal_history_verified_at timestamptz;
alter table public.vendor add column if not exists trust_override jsonb;
alter table public.vendor add column if not exists deal_evidence jsonb not null default '[]'::jsonb;
alter table public.vendor add constraint vendor_deal_evidence_array check (jsonb_typeof(deal_evidence)='array');
alter table public.vendor add constraint vendor_trust_override_shape check (
  trust_override is null or (
    jsonb_typeof(trust_override)='object' and
    trust_override ?& array['tier','recorded_by','reason','recorded_at'] and
    jsonb_typeof(trust_override->'reason')='string' and
    trust_override->>'tier' in ('Proven','Established','Trial') and
    trust_override->>'recorded_by' in ('joe','dell') and
    length(trim(trust_override->>'reason')) between 1 and 500 and
    trust_override->>'recorded_at' is not null
  )
);
grant select on table public.activity to carr_reader;
grant select on table public.party_link to carr_reader;
grant select on table public.deal to carr_reader;
grant select (loan_programs,deal_history_verified_at,trust_override,deal_evidence) on table public.vendor to carr_reader;
create index if not exists activity_vendor_contact_order on public.activity (vendor_id,occurred_at desc,id desc) where vendor_id is not null;
