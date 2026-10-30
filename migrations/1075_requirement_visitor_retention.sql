-- rollback: revoke delete from carr_writer and drop the retention index.
-- expand-contract: permit bounded retention of upload visitor hashes only.
-- lock-review: the private visitor table is new and the index supports one-row pruning.
create index requirement_upload_visitor_retention on ops.requirement_upload_visitor(window_start,visitor_hash);
grant delete on ops.requirement_upload_visitor to carr_writer;
do $contract$
declare contract jsonb := ops.scac_current_grant_contract();
begin
  contract := jsonb_set(contract,'{relation_dml}',contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
    'ingress_key','db-relation-acl:ops.requirement_upload_visitor:carr_writer:delete',
    'ingress_kind','db_relation_acl','relation','ops.requirement_upload_visitor','relation_kind','r',
    'grantee','carr_writer','privilege','delete','grantable',false)));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $contract$;
