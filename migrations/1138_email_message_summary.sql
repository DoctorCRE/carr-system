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
do $summary_grants$
declare
  contract jsonb := ops.scac_current_grant_contract();
  privilege text;
begin
  foreach privilege in array array['insert','update'] loop
    contract := jsonb_set(contract,'{relation_dml}',contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
      'ingress_key','db-relation-acl:public.email_message_summary:carr_writer:' || privilege,
      'ingress_kind','db_relation_acl','relation','public.email_message_summary',
      'relation_kind','r','grantee','carr_writer','privilege',privilege,'grantable',false)));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception 'Email summary grant catalog is invalid';
  end if;
end $summary_grants$;
