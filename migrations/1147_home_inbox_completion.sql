-- rollback: forward-only; retire the v4 read in a successor migration.
-- expand-contract: expand; v1, v2, v3 and disposition receipts remain unchanged.
-- lock-review: function metadata only; no domain writes or table locks.
set local lock_timeout = '5s';
set local statement_timeout = '30s';

create function ops.read_home_inbox_v4() returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare snapshot jsonb; items jsonb;
begin
  snapshot:=ops.read_home_inbox_v3();
  select coalesce(jsonb_agg(i || jsonb_build_object('completed_at',
    case when isfinite(evidence.completed_at) then
      to_char(evidence.completed_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') end) order by ord),'[]')
    into items
    from jsonb_array_elements(snapshot->'items') with ordinality entries(i,ord)
    left join lateral (
      select completion.occurred_at completed_at
      from public.next_action t
      cross join lateral (
        select e.occurred_at from public.event e
        where e.organization_tenant_id=t.organization_tenant_id
          and e.subject_type=t.subject_type and e.subject_id=t.subject_id
          and e.new_value->>'status'='done'
          and ((e.verb in ('complete-one-action','complete-action') and e.field='status'
              and e.old_value->>'status'='open' and e.new_value->>'next_action_id'=t.id::text)
            or (e.verb='complete-deal-item' and e.field='deal_item' and e.new_value->>'item_id'=t.id::text))
          and not exists(select 1 from public.event reopened
            where reopened.organization_tenant_id=t.organization_tenant_id
              and reopened.subject_type=t.subject_type and reopened.subject_id=t.subject_id
              and reopened.verb='reopen-one-action' and reopened.field='status'
              and reopened.new_value->>'action_id'=t.id::text and reopened.new_value->>'status'='open'
              and reopened.mutation_order>e.mutation_order)
        order by e.mutation_order desc limit 1
      ) completion
      where i->>'source_type'='next_action' and t.id=case when i->>'source_type'='next_action' then (i->>'source_id')::uuid end and t.status='done'
      union all
      select a.completed_at from public.capture_post_call_action a
      where i->>'source_type'='capture_post_call_action' and a.id=case when i->>'source_type'='capture_post_call_action' then (i->>'source_id')::uuid end and a.status='done'
      union all
      select case when pg_input_is_valid(p.send_result->>'sent_at','timestamptz')
        then (p.send_result->>'sent_at')::timestamptz end from public.email_proposal p
      where i->>'source_type'='email_proposal' and p.id=case when i->>'source_type'='email_proposal' then (i->>'source_id')::uuid end and p.status='sent'
    ) evidence on true;
  return snapshot || jsonb_build_object('schema_version','home-inbox.v4','items',items);
end $$;
revoke all on function ops.read_home_inbox_v4() from public,carr_reader;
grant execute on function ops.read_home_inbox_v4() to carr_writer;

do $append_grant$
declare contract jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||
    '[{"grantee":"carr_writer","signature":"ops.read_home_inbox_v4()","grantable":false,"privilege":"execute","ingress_key":"db-function-acl:ops.read_home_inbox_v4():carr_writer:execute","ingress_kind":"db_function_acl","function_kind":"f","security_definer":true,"volatility":"s","parallel":"u","config":["search_path=pg_catalog, pg_temp"]}]'::jsonb);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
