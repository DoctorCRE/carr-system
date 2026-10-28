-- rollback: forward-only — the hash pages are a rebuildable cache. To undo, copy public.system_work_census_cache back into public.board_snapshot as board_id 'carr-v5-<kind>-<digest>' (version 1, updated_by_actor_id = any partner actor) and drop the table; the next publish-board run also rewrites every page the live manifest names.
-- expand-contract: migrate — creates the new cache table beside board_snapshot, copies the content-addressed census pages into it in this transaction, and removes only those copied rows; no real board (carr-v5, orchestrator, needs-joe-local, …) is touched, and no other table, view, function or trigger reads the removed rows (checked against pg_depend and pg_proc.prosrc on production).
-- lock-review: new empty table, so its index builds instantly; board_snapshot holds ~3,450 rows (~85 MB of JSON) and the delete takes row locks on the ~3,110 hash-board rows only, none of which any board_question or board_answer row references.
-- WR573 database audit F6 / P6: board_snapshot was holding immutable hash-named cache pages
-- written by tools/progress_board.py. They get their own small table, trimmed on write.

create table public.system_work_census_cache (
  organization_tenant_id text not null check (length(btrim(organization_tenant_id)) > 0),
  sponsoring_human_slug text not null check (sponsoring_human_slug in ('joe', 'dell')),
  kind text not null check (kind in ('external', 'snapshot')),
  digest text not null check (digest ~ '^[0-9a-f]{64}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  touched_at timestamptz not null default now(),
  primary key (organization_tenant_id, sponsoring_human_slug, kind, digest)
);
create index system_work_census_cache_touched_at on public.system_work_census_cache (touched_at);
comment on table public.system_work_census_cache is
  'Immutable content-addressed pages of the system-work census (external GitHub inventory, oversized board snapshot). Rewritten by publish-census-cache-page, which trims pages untouched for 14 days; rebuildable from the publisher.';

grant select on table public.system_work_census_cache to carr_reader;
grant insert, select, update, delete on table public.system_work_census_cache to carr_writer;

-- touched_at keeps the page's own age, except a page the live carr-v5 manifest still names starts at now():
-- it may be older than 14 days, and the next publish must re-touch it before any trim can take it.
insert into public.system_work_census_cache (organization_tenant_id, sponsoring_human_slug, kind, digest, payload, touched_at)
select b.organization_tenant_id, b.sponsoring_human_slug, m[1], m[2], b.snapshot_json,
       case when exists (
         select 1 from public.board_snapshot live,
           lateral jsonb_array_elements(case when jsonb_typeof(live.snapshot_json->'external_inventory'->'pages') = 'array'
                                             then live.snapshot_json->'external_inventory'->'pages' else '[]'::jsonb end) p
          where live.board_id = 'carr-v5' and live.organization_tenant_id = b.organization_tenant_id
            and live.sponsoring_human_slug = b.sponsoring_human_slug and p->>'board_id' = b.board_id)
       then now() else b.updated_at end
  from public.board_snapshot b, lateral regexp_match(b.board_id, '^carr-v5-(external|snapshot)-([0-9a-f]{64})$') as m
 where m is not null and b.version = 1
on conflict do nothing;

delete from public.board_snapshot b
 where b.board_id ~ '^carr-v5-(external|snapshot)-[0-9a-f]{64}$'
   and b.version = 1
   and exists (select 1 from public.system_work_census_cache k
                where k.organization_tenant_id = b.organization_tenant_id
                  and k.sponsoring_human_slug = b.sponsoring_human_slug
                  and k.kind = (regexp_match(b.board_id, '^carr-v5-(external|snapshot)-'))[1]
                  and k.digest = right(b.board_id, 64));
