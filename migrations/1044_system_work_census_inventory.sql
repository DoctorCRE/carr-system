-- rollback: forward-only — the rows removed are a rebuildable cache of GitHub inventory pages. To undo, drop public.system_work_census_inventory; the publisher rewrites the inventory on its next run.
-- expand-contract: contract — deletes the content-addressed census pages from board_snapshot. Their only reader, system-work-census.v5.js, moves to the new table in the same release; nothing else reads them (checked on production against pg_depend, pg_proc.prosrc, board_question and board_answer).
-- lock-review: the new table is empty; the delete takes row locks on about 3,100 board_snapshot rows (about 85 MB of JSON) that no other statement touches.
-- WR573 database audit F6 / P6: board_snapshot was holding immutable hash-named cache pages written by
-- tools/progress_board.py. The latest external inventory now lives in one row per partner, replaced whole.
-- Nothing is copied: the carr-v5 manifest still names the old pages, so the census reports the external
-- inventory unavailable (never complete) until the publisher's next run writes the row and a v3 manifest.

create table public.system_work_census_inventory (
  organization_tenant_id text not null check (length(btrim(organization_tenant_id)) > 0),
  sponsoring_human_slug text not null check (sponsoring_human_slug in ('joe', 'dell')),
  payload jsonb not null check (jsonb_typeof(payload) = 'object' and jsonb_typeof(payload->'items') = 'array'),
  updated_at timestamptz not null default now(),
  primary key (organization_tenant_id, sponsoring_human_slug)
);
comment on table public.system_work_census_inventory is
  'Latest external (GitHub) work inventory for the system-work census, one row per partner, replaced whole by publish-census-inventory. Rebuildable by the board publisher.';
grant select on table public.system_work_census_inventory to carr_reader;
grant insert, select, update on table public.system_work_census_inventory to carr_writer;

delete from public.board_snapshot
 where board_id ~ '^carr-v5-(external|snapshot)-[0-9a-f]{64}$';

do $inventory_grants$
declare contract jsonb := ops.scac_current_grant_contract(); privilege text;
begin
  foreach privilege in array array['insert','update'] loop
    contract := jsonb_set(contract, '{relation_dml}', contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
      'ingress_key','db-relation-acl:public.system_work_census_inventory:carr_writer:' || privilege,
      'ingress_kind','db_relation_acl','relation','public.system_work_census_inventory','relation_kind','r',
      'grantee','carr_writer','privilege',privilege,'grantable',false)));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L', 'select '||quote_literal(contract::text)||'::jsonb');
end $inventory_grants$;
