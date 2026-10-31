-- rollback: revoke the writer execute grant after removing comparison generation.
-- lock-review: function ACL and immutable contract replacement only; no table scan.

grant execute on function ops.list_fact_review(text,text,integer,jsonb,boolean) to carr_writer;

do $contract$
declare contract jsonb; entry jsonb;
begin
  contract := ops.scac_current_grant_contract();
  select value into strict entry from jsonb_array_elements(contract->'secdef_execute')
    where value->>'signature'='ops.list_fact_review(p_tenant text, p_entity_ref text, p_limit integer, p_cursor jsonb, p_include_resolved boolean)'
    and value->>'grantee'='carr_reader';
  entry := entry||jsonb_build_object('grantee','carr_writer',
    'ingress_key','db-function-acl:ops.list_fact_review(p_tenant text, p_entity_ref text, p_limit integer, p_cursor jsonb, p_include_resolved boolean):carr_writer:execute');
  contract := jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||jsonb_build_array(entry));
  execute format('CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT '||quote_literal(contract::text)||'::jsonb');
end $contract$;
