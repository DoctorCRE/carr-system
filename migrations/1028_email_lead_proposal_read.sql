-- rollback: drop the new reader views and email_lead_exclusion; candidate readings remain intact.
-- lock-review: creates an empty receipt table and views; candidate backfill locks proven-owner Outlook rows.
update public.candidate_pool cp
set source_row=cp.source_row || jsonb_build_object('mailbox_owner',replace(a.slug,'-local','')),
  source_key='email-sha256:' || replace(a.slug,'-local','') || ':' || substr(cp.source_key,14)
from public.actor a
where cp.created_by=a.id and a.slug in ('joe','dell','joe-local','dell-local')
  and cp.source='outlook-intake' and not cp.source_row ? 'mailbox_owner'
  and cp.source_key ~ '^email-sha256:[a-f0-9]{64}$';
create table public.email_lead_exclusion (
  mailbox_owner text not null check (mailbox_owner in ('joe','dell')),
  message_hash text not null check (message_hash ~ '^[a-f0-9]{64}$'),
  recorded_at timestamptz not null default now(),
  primary key (mailbox_owner, message_hash)
);
grant select, insert on public.email_lead_exclusion to carr_writer;
create view public.v_email_lead_exclusion_count as
select mailbox_owner, count(*)::integer as not_business_count
from public.email_lead_exclusion group by mailbox_owner;
grant select on public.v_email_lead_exclusion_count to carr_reader, carr_writer;
create view public.v_email_lead_proposal as
select cp.id as pool_id, cp.version as base_version, cp.status, cp.created_at,
  cp.source_row->>'mailbox_owner' as mailbox_owner,
  cp.name as display_name, cp.email as sender_email, split_part(cp.email,'@',2) as sender_domain,
  cp.source_row->'reading' as reading,
  d.id as deal_id, d.name as deal_name,
  r.ref as party_ref, p.id as party_id, p.name as party_name, p.version as party_version
from public.candidate_pool cp
left join public.deal d on d.id::text=cp.source_row->'reading'->>'deal_id'
left join public.v_ref_index r on r.ref=cp.source_row->'reading'->>'party_ref'
  and r.subject_type in ('client','lead','vendor') and not r.merged
left join public.party p on p.id=r.party_id and p.merged_into is null and p.deleted_at is null
where cp.source='outlook-intake' and cp.source_row->>'mailbox_owner' in ('joe','dell');
grant select on public.v_email_lead_proposal to carr_reader, carr_writer;
do $exclusion_grants$
declare contract jsonb := ops.scac_current_grant_contract();
begin
  contract := jsonb_set(contract, '{relation_dml}', contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
    'ingress_key','db-relation-acl:public.email_lead_exclusion:carr_writer:insert',
    'ingress_kind','db_relation_acl','relation','public.email_lead_exclusion','relation_kind','r',
    'grantee','carr_writer','privilege','insert','grantable',false)));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L', 'select '||quote_literal(contract::text)||'::jsonb');
end $exclusion_grants$;
