-- WR-000622: retained doctrine history must not become one JSONB array.
-- rollback: forward-only; restore digest semantics with a new migration, never rewrite epochs.
-- expand-contract: expand; same function signature, privileges and source coverage.
-- lock-review: replaces one function; no table rewrite or explicit relation lock.
-- The next schema-ledger epoch records the new digest; historical epochs stay immutable.
create or replace function ops.scac_doctrine_projection_digest()
returns text language plpgsql stable security definer
set search_path=pg_catalog,public,ops,pg_temp as $fn$
declare
  state bytea := public.digest(convert_to('carr:doctrine-projection:row-chain:v2','UTF8'),'sha256');
  fact record;
begin
  -- Hash complete rows before sorting. Only fixed-width digests enter the fold.
  for fact in
    with facts(table_name,row_value) as (
      select 'doctrine_document',to_jsonb(x) from public.doctrine_document x union all
      select 'doctrine_slug_alias',to_jsonb(x) from public.doctrine_slug_alias x union all
      select 'doctrine_section',to_jsonb(x) from public.doctrine_section x union all
      select 'doctrine_revision',to_jsonb(x) from public.doctrine_revision x union all
      select 'doctrine_edge_type',to_jsonb(x) from public.doctrine_edge_type x union all
      select 'doctrine_edge',to_jsonb(x) from public.doctrine_edge x union all
      select 'doctrine_link',to_jsonb(x) from public.doctrine_link x union all
      select 'doctrine_review_policy',to_jsonb(x) from public.doctrine_review_policy x union all
      select 'doctrine_snapshot',to_jsonb(x) from public.doctrine_snapshot x union all
      select 'doctrine_gate_check',to_jsonb(x) from public.doctrine_gate_check x union all
      select 'doctrine_concept_mapping',to_jsonb(x) from public.doctrine_concept_mapping x
    )
    select public.digest(convert_to(ops.scac_canonical_json(
      jsonb_build_object('table',table_name,'row',row_value)),'UTF8'),'sha256') as row_digest
      from facts order by row_digest
  loop
    state := public.digest(state || fact.row_digest,'sha256');
  end loop;
  return 'sha256:' || encode(state,'hex');
end $fn$;
