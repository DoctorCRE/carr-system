-- rollback: forward-only; retain source evidence and guarded correction history.
-- expand-contract: expand; no existing date or proposal rows are rewritten.
-- lock-review: new table; deal/kind index supports bounded obligation reads.
set local lock_timeout='5s';
set local statement_timeout='30s';
create table public.email_critical_date_effect (
  id uuid primary key default gen_random_uuid(),
  generation uuid not null references public.email_filing_application(generation),
  activity_id uuid not null references public.activity(id),
  deal_id uuid not null references public.deal(id),
  obligation_kind text not null,
  source_hash text not null,
  evidence_quote text not null check(length(evidence_quote) between 1 and 2000),
  due_on date,
  status text not null check(status in ('review','applied','duplicate','undone','dismissed')),
  reason text,
  critical_date_id uuid references public.critical_date(id),
  before_value jsonb,
  after_value jsonb,
  unique(generation,obligation_kind)
);
create index email_critical_date_effect_source on public.email_critical_date_effect(activity_id,id);
create index critical_date_deal_kind on public.critical_date(deal_id,kind);
grant select on public.email_critical_date_effect to carr_reader;
grant select,insert,update on public.email_critical_date_effect to carr_writer;
do $contract$
declare contract jsonb := ops.scac_current_grant_contract(); privilege text;
begin
  foreach privilege in array array['insert','update'] loop
    contract := jsonb_set(contract,'{relation_dml}',contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
      'ingress_key','db-relation-acl:public.email_critical_date_effect:carr_writer:'||privilege,
      'ingress_kind','db_relation_acl','relation','public.email_critical_date_effect',
      'relation_kind','r','grantee','carr_writer','privilege',privilege,'grantable',false)));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
  if ops.scac_mutation_catalog_v118_current() is not true then raise exception 'Invalid email date grant catalog'; end if;
end $contract$;
