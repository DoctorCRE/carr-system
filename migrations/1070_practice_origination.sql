-- Rollback: stop imports and retain evidence; no existing business records change.
-- Expand-contract: additive evidence tables and read projection only.
-- Lock-review: new tables only; no existing table rewrite.
create table practice_origination_feed (
  source text primary key check (source in ('nppes','pecos')),
  checkpoint text,
  published_on date,
  status text not null default 'unavailable' check (status in ('available','blocked','unavailable')),
  detail text not null default 'No successful import',
  updated_at timestamptz not null default now()
);
create table practice_origination_event (
  id bigint generated always as identity primary key,
  source text not null references practice_origination_feed(source),
  record_key text not null,
  fingerprint text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
  npi text not null check (npi ~ '^[0-9]{10}$'),
  name text not null,
  city text not null,
  state text not null check (state in ('AL','FL')),
  kind text not null check (kind in ('enumeration','registry_update','pecos_observation','pecos_correction')),
  evidence_date date not null,
  date_basis text not null check (date_basis in ('enumeration','registry_update','publication')),
  source_url text not null,
  facts jsonb not null,
  published_on date not null,
  recorded_at timestamptz not null default now(),
  recorded_by uuid not null references actor(id),
  unique (source,record_key,fingerprint)
);
create index practice_origination_record on practice_origination_event(source,record_key,id desc);
create view v_practice_origination_party_address as
  select distinct on (subject_id) subject_id as party_id,value->>'street_address' as street_address
  from record_flag
  where subject_type='party' and kind='address' and value->>'found'='true'
    and value->>'epistemic_status'='source_backed'
    and (expires_on is null or expires_on>=current_date)
  order by subject_id,observed_at desc,id desc;
grant select on v_practice_origination_party_address to carr_reader,carr_writer;
grant select on practice_origination_feed,practice_origination_event to carr_reader,carr_writer;
grant insert,update on practice_origination_feed to carr_writer;
grant insert on practice_origination_event to carr_writer;
grant usage,select on sequence practice_origination_event_id_seq to carr_writer;
do $grants$
declare contract jsonb := ops.scac_current_grant_contract(); rel text; privilege text;
begin
  foreach rel in array array['practice_origination_feed','practice_origination_event'] loop
    foreach privilege in array case when rel='practice_origination_feed' then array['insert','update'] else array['insert'] end loop
      contract := jsonb_set(contract,'{relation_dml}',contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
        'ingress_key','db-relation-acl:public.'||rel||':carr_writer:'||privilege,
        'ingress_kind','db_relation_acl','relation','public.'||rel,'relation_kind','r',
        'grantee','carr_writer','privilege',privilege,'grantable',false)));
    end loop;
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $grants$;
