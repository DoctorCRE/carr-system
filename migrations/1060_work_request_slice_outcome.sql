-- rollback: forward fix only; preserve recorded slice outcomes.
-- expand-contract: expand with a default open outcome; existing readers ignore it.
-- lock-review: the small queue slice table takes an ALTER lock; no table scan outside constraint validation.
alter table ops.work_request_slice add column version integer not null default 1 check (version > 0);
alter table ops.work_request_slice add column outcome jsonb not null default '{"outcome":"open"}'::jsonb
  check (coalesce(jsonb_typeof(outcome) = 'object' and outcome->>'outcome' in ('open','done','retired')
    and (outcome->>'outcome' = 'open' or
      (length(btrim(outcome->>'outcome_by')) > 0 and outcome->>'outcome_at' is not null))
    and (outcome->>'outcome' <> 'retired' or length(btrim(outcome->>'reason')) > 0)
    and (outcome->>'outcome' <> 'done' or
      (jsonb_typeof(outcome->'change_refs') = 'array' and jsonb_array_length(outcome->'change_refs') > 0)),false));
grant update on ops.work_request_slice to carr_writer;
do $slice_grant$
declare contract jsonb := ops.scac_current_grant_contract();
begin
  contract := jsonb_set(contract, '{relation_dml}', contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
    'ingress_key','db-relation-acl:ops.work_request_slice:carr_writer:update',
    'ingress_kind','db_relation_acl','relation','ops.work_request_slice','relation_kind','r',
    'grantee','carr_writer','privilege','update','grantable',false)));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L', 'select '||quote_literal(contract::text)||'::jsonb');
end $slice_grant$;
