-- rollback: forward-only; preserve accepted confirmation evidence and touch outcomes.
-- expand-contract: new table leaves existing activity and schedule readers unchanged.
-- lock-review: creates a new empty table; no activity rewrite or scan.
create table public.calendar_completion (
  organization_tenant_id text not null,
  sponsoring_human_slug text not null,
  event_identity text not null check(event_identity ~ '^[a-f0-9]{64}$'),
  event_key text not null check(event_key = left(event_identity,16)),
  source_revision text not null check(source_revision ~ '^[a-f0-9]{64}$'),
  disposition text not null check(disposition = 'confirmed-complete'),
  resolved_attendees jsonb not null,
  unresolved_attendees jsonb not null,
  outcomes jsonb not null,
  confirmed_by uuid not null references public.actor(id),
  primary key(organization_tenant_id,sponsoring_human_slug,event_identity)
);
revoke all on public.calendar_completion from public;
grant select on public.calendar_completion to carr_reader,carr_writer;
grant insert,update on public.calendar_completion to carr_writer;
do $completion_grants$
declare
  contract jsonb := ops.scac_current_grant_contract();
  privilege text;
begin
  foreach privilege in array array['insert','update'] loop
    contract := jsonb_set(contract,'{relation_dml}',contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
      'ingress_key','db-relation-acl:public.calendar_completion:carr_writer:' || privilege,
      'ingress_kind','db_relation_acl','relation','public.calendar_completion',
      'relation_kind','r','grantee','carr_writer','privilege',privilege,'grantable',false)));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception 'Calendar completion grant catalog is invalid';
  end if;
end $completion_grants$;
