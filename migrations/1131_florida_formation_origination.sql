-- Rollback: disable formation-fl imports and retain existing evidence.
-- Expand-contract: CMS identities retain their existing NPI; formations use document numbers.
-- Lock-review: access-exclusive locks on two evidence tables; identity validation scans existing rows, without rewriting them.
alter table practice_origination_feed drop constraint practice_origination_feed_source_check;
alter table practice_origination_feed add constraint practice_origination_feed_source_check
  check (source in ('nppes','pecos','formation-fl'));
alter table practice_origination_event alter column npi drop not null;
alter table practice_origination_event add column document_number text;
alter table practice_origination_event add column filing_on date;
alter table practice_origination_event drop constraint practice_origination_event_kind_check;
alter table practice_origination_event add constraint practice_origination_event_kind_check
  check (kind in ('enumeration','registry_update','pecos_observation','pecos_correction','formation_candidate'));
alter table practice_origination_event drop constraint practice_origination_event_date_basis_check;
alter table practice_origination_event add constraint practice_origination_event_date_basis_check
  check (date_basis in ('enumeration','registry_update','publication','filing'));
alter table practice_origination_event add constraint practice_origination_event_identity_check check (
  (source in ('nppes','pecos') and npi is not null and document_number is null and filing_on is null
    and date_basis<>'filing' and kind<>'formation_candidate') or
  (source='formation-fl' and npi is null and document_number is not null
    and document_number ~ '^[A-Z0-9]{12}$' and record_key=document_number and state='FL'
    and filing_on is not null and evidence_date=filing_on and filing_on<=published_on
    and date_basis='filing' and kind='formation_candidate')
);
