-- rollback: drop public.email_proposal after removing list-email-proposals, record-email-proposal and dismiss-email-proposal; confirmed changes stay in deal history.
-- lock-review: new empty table and index only; no existing rows rewritten.

create table public.email_proposal (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('phase_move','next_step')),
  deal_id uuid not null references public.deal(id),
  activity_id uuid not null references public.activity(id),
  evidence_quote text not null check (length(evidence_quote) between 10 and 2000),
  task_text text check (length(btrim(task_text)) between 1 and 500),
  next_date date,
  owner text check (owner in ('joe','dell')),
  confidence numeric not null check (confidence between 0 and 1),
  status text not null default 'pending' check (status in ('pending','confirmed','dismissed')),
  version integer not null default 1,
  created_by uuid not null references public.actor(id),
  created_at timestamptz not null default now(),
  resolved_by uuid references public.actor(id),
  resolved_at timestamptz,
  resolution_reason text check (length(btrim(resolution_reason)) between 1 and 500),
  unique (kind, deal_id, activity_id),
  check ((kind = 'phase_move' and task_text is null and next_date is null and owner is null)
      or (kind = 'next_step' and task_text is not null and next_date is not null and owner is not null)),
  check ((status = 'pending' and resolved_by is null and resolved_at is null and resolution_reason is null)
      or (status = 'confirmed' and resolved_by is not null and resolved_at is not null)
      or (status = 'dismissed' and resolved_by is not null and resolved_at is not null and resolution_reason is not null))
);

create index email_proposal_pending on public.email_proposal(created_at, id) where status = 'pending';

grant select on public.email_proposal to carr_reader;
grant select,insert,update on public.email_proposal to carr_writer;

create trigger scac_reference_monitor_guard_row before insert or update or delete
  on public.email_proposal for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
  on public.email_proposal for each statement execute function ops.scac_reference_monitor_guard();

-- Register exactly the two new writer grants; preserve all existing grants and retirements.
do $contract$
declare contract jsonb := ops.scac_current_grant_contract();
begin
  contract := jsonb_set(contract, '{relation_dml}', (contract->'relation_dml') || (
    select jsonb_agg(jsonb_build_object('grantee','carr_writer','relation','public.email_proposal','grantable',false,
      'privilege',privilege,'ingress_key','db-relation-acl:public.email_proposal:carr_writer:'||privilege,
      'ingress_kind','db_relation_acl','relation_kind','r') order by privilege)
    from unnest(array['insert','update']) as privilege));
  contract := jsonb_set(contract, '{runtime_dml_grants}', jsonb_build_object('count',341,
    'digest','sha256:417178cb46966205a188bafec1ceb5d7490249d9dd924264c6f811fb31697ed1'));
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb');
end $contract$;
