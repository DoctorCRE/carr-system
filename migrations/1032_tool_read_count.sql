-- rollback: create or replace v_integrity_digest from migrations/0879_verb_usage_digest.sql, restore mcp.js's tool_read_call insert, then drop table public.tool_read_count; tool_read_call is untouched here and still holds every row written before the new Worker deployed
-- expand-contract: expand — adds tool_read_count beside tool_read_call and moves its one view reader; the drop of tool_read_call is a separate contract PR after this release
-- lock-review: lock table tool_read_call in share mode holds the old Worker's fire-and-forget read-call inserts for the backfill (one grouped scan of about 1M rows, 310 MB; seconds, measured in the PR) so the post-check can demand an exact count; reads of the table are not blocked; create or replace view takes a brief lock on v_integrity_digest
-- 1032_tool_read_count.sql — read verbs are counted per day, not logged per call (WR573.S1.J2, database audit F2/P2).
--
-- WHY. 0108 made tool_read_call one row per read-verb call so Dell's 2026-08-21
-- cutover could be confirmed passively. On 2026-10-28 it held 1,014,993 rows
-- (310 MB, about 32.6k new rows a day), 26.5k a day of them standing-context
-- fired by the rule hook on every tool call. Every question asked of it since is
-- a count or a latest time per verb and sponsor: did this verb get called, by
-- whose session, through which door, and did it succeed. The same rows grouped
-- by UTC day, verb, sponsor, via and ok are 4,756 counters.
--
-- WHAT IT ADDS.
--   tool_read_count   one row per (day, verb, sponsoring_human_slug, via, ok)
--                     with n calls and last_at, the latest call's time.
--                     The writer (mcp.js readCallInsertSQL) upserts it: n + 1,
--                     greatest(last_at). No per-call actor lookup.
--   backfill          every tool_read_call row, grouped the same way.
--   v_integrity_digest  verb_usage_30d reads last_at from tool_read_count; the
--                     other five rows are restated unchanged from 0879.
-- The key columns sponsoring_human_slug and via are nullable (a call with no
-- sponsor or door label is still counted), so the key is a unique constraint
-- with NULLS NOT DISTINCT rather than a primary key.
--
-- WHAT IT DOES NOT DO. tool_read_call keeps its rows and grants; the new Worker
-- stops writing it. Rows the old Worker writes between this migration and the
-- Worker promotion are not in the backfill; the contract PR counts the rows
-- created after this file's schema_migrations.applied_at before it drops the
-- table.

create table public.tool_read_count (
  day                    date        not null,
  verb                   text        not null,
  sponsoring_human_slug  text,
  via                    text,
  ok                     boolean     not null,
  n                      bigint      not null check (n > 0),
  last_at                timestamptz not null,
  constraint tool_read_count_key unique nulls not distinct (day, verb, sponsoring_human_slug, via, ok),
  check (sponsoring_human_slug is null or sponsoring_human_slug in ('joe', 'dell'))
);

comment on table public.tool_read_count is
  'WR573.S1.J2 (1032). Read-verb calls through the MCP Worker counted per UTC day, verb, '
  'sponsoring human, door (via) and outcome; replaces the per-call tool_read_call log (0108). '
  'n is the call count, last_at the latest call. Never an argument value or a response body.';

-- Holds concurrent inserts (not reads) until commit, so the count below is exact.
lock table public.tool_read_call in share mode;

insert into public.tool_read_count (day, verb, sponsoring_human_slug, via, ok, n, last_at)
select (created_at at time zone 'UTC')::date, verb, sponsoring_human_slug, via, ok, count(*), max(created_at)
  from public.tool_read_call
 group by 1, 2, 3, 4, 5;

-- ON CONFLICT DO UPDATE reads n and last_at, so the writer needs SELECT too.
grant select, insert, update on public.tool_read_count to carr_writer;
grant select on public.tool_read_count to carr_reader, carr_exporter;

create or replace view public.v_integrity_digest as
  select 'row_counts'::text as line,
    jsonb_build_object(
      'deals',         (select count(*) from deal),
      'clients',       (select count(*) from client),
      'leads',         (select count(*) from lead),
      'vendors',       (select count(*) from vendor),
      'activities_7d', (select count(*) from activity where recorded_at > now() - interval '7 days'),
      'events_24h',    (select count(*) from event    where recorded_at > now() - interval '24 hours')
    ) as value
union all
  select 'writes_by_dell_24h'::text,
    to_jsonb((select count(*) from event e join actor a on a.id = e.actor_id
               where a.slug = 'dell' and e.recorded_at > now() - interval '24 hours'))
union all
  select 'export_freshness'::text,
    coalesce((
      select jsonb_object_agg(t.target, jsonb_build_object(
               'last_ok', t.last_ok,
               'stale',   case when t.last_ok is null then null
                               else t.last_ok < now() - interval '26 hours' end,
               'state',   case when t.last_ok is null then 'never_succeeded'
                               when t.last_ok < now() - interval '26 hours' then 'stale'
                               else 'fresh' end,
               'last_attempt',        t.last_any,
               'last_attempt_status', t.last_status))
        from (select target,
                     max(ran_at) filter (where status = 'ok') as last_ok,
                     max(ran_at)                              as last_any,
                     (array_agg(status order by ran_at desc))[1] as last_status
                from export_run group by target) t), '{}'::jsonb)
union all
  select 'norm_owed_open'::text,
    to_jsonb((select count(*) from availability where norm_owed))
union all
  select 'merge_queue'::text,
    to_jsonb((select count(*) from ingest_inbox where status = 'new'))

union all
  select 'verb_usage_30d'::text,
    (with registered as (
       select substr(e.ingress_key, length('mcp-tool:') + 1) as verb,
              (e.contract ->> 'write')::boolean          as write,
              (e.contract ->> 'human_only')::boolean     as human_only,
              (e.contract ->> 'authority_only')::boolean as authority_only
         from ops.scac_mutation_registry_entry e
        where e.ingress_key like 'mcp-tool:%'
          and e.registry_version = (
                select v.registry_version from ops.scac_mutation_registry_version v
                 order by split_part(v.registry_version, '.v', 2)::integer desc
                 limit 1)),
     last_call as (
       select c.verb, max(c.created_at) as last_called_at
         from (select verb, created_at from tool_call
               union all
               select verb, last_at from tool_read_count) c
        group by c.verb)
     select jsonb_build_object(
              'window_days', 30,
              'registered',  count(*),
              'called',      count(*) filter (where l.last_called_at > now() - interval '30 days'),
              'uncalled',    coalesce(jsonb_agg(jsonb_build_object(
                                 'verb',           r.verb,
                                 'write',          r.write,
                                 'human_only',     r.human_only,
                                 'authority_only', r.authority_only,
                                 'last_called_at', l.last_called_at)
                               order by r.write desc, r.verb)
                               filter (where l.last_called_at is null
                                          or l.last_called_at <= now() - interval '30 days'),
                             '[]'::jsonb),
              'action',      'each uncalled verb is retired through a SCAC successor or gets a named consumer; record the call on WR-000284')
       from registered r
       left join last_call l on l.verb = r.verb);

-- post-conditions
do $$
declare
  v_calls bigint;
  v_counted bigint;
  v_latest_log timestamptz;
  v_latest_count timestamptz;
begin
  select count(*), max(created_at) into v_calls, v_latest_log from public.tool_read_call;
  select coalesce(sum(n), 0), max(last_at) into v_counted, v_latest_count from public.tool_read_count;
  if v_counted <> v_calls then
    raise exception '1032 FAILED: tool_read_count sums to % calls, tool_read_call holds %', v_counted, v_calls;
  end if;
  if v_latest_count is distinct from v_latest_log then
    raise exception '1032 FAILED: latest counted call % differs from latest logged call %', v_latest_count, v_latest_log;
  end if;
  if not has_table_privilege('carr_writer', 'public.tool_read_count', 'insert')
     or not has_table_privilege('carr_writer', 'public.tool_read_count', 'update')
     or not has_table_privilege('carr_writer', 'public.tool_read_count', 'select') then
    raise exception '1032 FAILED: carr_writer cannot upsert tool_read_count';
  end if;
  if has_table_privilege('carr_writer', 'public.tool_read_count', 'delete') then
    raise exception '1032 FAILED: carr_writer holds DELETE on tool_read_count';
  end if;
  if not has_table_privilege('carr_reader', 'public.tool_read_count', 'select') then
    raise exception '1032 FAILED: carr_reader cannot select tool_read_count';
  end if;
  if (select count(*) from public.v_integrity_digest) <> 6
     or not exists (select 1 from public.v_integrity_digest where line = 'verb_usage_30d') then
    raise exception '1032 FAILED: v_integrity_digest lost a row';
  end if;
  if exists (select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
              where d.refobjid = 'public.tool_read_call'::regclass
                and r.ev_class = 'public.v_integrity_digest'::regclass) then
    raise exception '1032 FAILED: v_integrity_digest still reads tool_read_call';
  end if;
end $$;
