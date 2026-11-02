-- Rollback: disable formation-fl imports; preserve admitted six-character identities.
-- Expand-contract: admit the documented six- and twelve-character Sunbiz document numbers.
-- Lock-review: access-exclusive lock and CHECK validation scan; no table rewrite.
-- https://dos.fl.gov/sunbiz/other-services/data-downloads/data-usage-guide/
alter table practice_origination_event drop constraint practice_origination_event_identity_check;
alter table practice_origination_event add constraint practice_origination_event_identity_check check (
  (source in ('nppes','pecos') and npi is not null and document_number is null and filing_on is null
    and date_basis<>'filing' and kind<>'formation_candidate') or
  (source='formation-fl' and npi is null and document_number is not null
    and document_number ~ '^[A-Z0-9]{6}([A-Z0-9]{6})?$' and record_key=document_number and state='FL'
    and filing_on is not null and evidence_date=filing_on and filing_on<=published_on
    and date_basis='filing' and kind='formation_candidate')
);
