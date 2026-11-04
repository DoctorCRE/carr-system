-- Authority-authored answers reuse the recorded firm-history snapshot reader.
-- rollback: forward-only; revoke the authority execute grant and retire its catalog receipt in a successor.
-- expand-contract: expand; adds one read-only function grant, no table writes or schema changes.
-- lock-review: no table locks; function ACL and frozen grant receipt only.
grant execute on function ops.market_terms_snapshot(text) to carr_authority;

do $grants$
declare
  contract jsonb := ops.scac_current_grant_contract();
  entry jsonb;
begin
  select jsonb_build_object('config',to_jsonb(p.proconfig),'grantee','carr_authority',
    'parallel',p.proparallel::text,'grantable',false,'privilege','execute',
    'signature','ops.market_terms_snapshot('||pg_get_function_identity_arguments(p.oid)||')',
    'volatility',p.provolatile::text,'ingress_key','db-function-acl:ops.market_terms_snapshot('
      ||pg_get_function_identity_arguments(p.oid)||'):carr_authority:execute',
    'ingress_kind','db_function_acl','function_kind',p.prokind::text,'security_definer',p.prosecdef)
    into strict entry from pg_proc p where p.oid='ops.market_terms_snapshot(text)'::regprocedure;
  if not exists(select 1 from jsonb_array_elements(contract->'secdef_execute') e
    where e->>'ingress_key'=entry->>'ingress_key') then
    contract := jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(entry));
    execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
      'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
  end if;
end $grants$;
