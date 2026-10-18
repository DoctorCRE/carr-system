-- rollback: re-run the create or replace view from migrations/0109_integrity_digest_unregistered_targets.sql (the verb_usage_30d row disappears, the five older rows are unchanged); no data is written and no grant is added here
-- 0879_verb_usage_digest.sql
--
-- NAME EVERY REGISTERED VERB NOBODY HAS CALLED IN 30 DAYS (WR-000284 C5).
--
-- WHY THIS EXISTS. Joe, 2026-10-13: things get built and never used, because
-- the usage ledgers are consumed by nothing. tool_call logs every write verb
-- call and tool_read_call every read verb call, and no surface compared either
-- against the list of verbs that exist. Measured on production that day: 411
-- registered verbs, 258 called in 90 days, 153 never called (140 write, 13
-- read; 45 of them human- or authority-only by design).
--
-- WHAT IT ADDS. One row, verb_usage_30d, appended to v_integrity_digest. The
-- five existing rows are restated unchanged from 0109. The value is
--   window_days  30
--   registered   the current sealed registry's mcp-tool entries
--   called       how many of those have a tool_call or tool_read_call row
--                inside the window
--   uncalled     the rest, each with write / human_only / authority_only from
--                its sealed contract and its all-time last_called_at (null when
--                never called), write verbs first, then by name
--   action       what a reader does about each one
-- registered = called + the length of uncalled, by construction: a ledger row
-- for a verb the registry no longer lists counts toward nothing.
--
-- WHY THE SEALED REGISTRY IS WHAT "REGISTERED" MEANS. Every verb the MCP
-- server serves, read or write, is a row in ops.scac_mutation_registry_entry
-- with ingress_key 'mcp-tool:<verb>', sealed per registry version. The current
-- version is the highest .vN; the version name's CHECK pins that spelling. No
-- second list of verbs is kept anywhere, so this row cannot drift from it.
--
-- WHY IN THE VIEW, NOT THE HANDLER. integrity-digest's handler is
-- `select * from v_integrity_digest`, so the verb, the MCP read door and
-- tools/health-check.py all see the new row with no mcp-server/src edit and no
-- registry reseal.
--
-- NO GRANT IS ADDED. The view is not security_invoker, so it reads its base
-- tables with its owner's privileges; carr_reader already holds select on the
-- view (0004). Proved on PG18 before writing this: `set role carr_reader;
-- select * from v_integrity_digest` returns all six rows with carr_reader
-- holding no privilege on tool_call.verb, tool_call.created_at or the registry
-- tables.

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
               select verb, created_at from tool_read_call) c
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
  v jsonb;
  v_registry integer;
  v_ledger_rows bigint;
begin
  select value into v from public.v_integrity_digest where line = 'verb_usage_30d';
  if v is null then
    raise exception '0879 FAILED: v_integrity_digest has no verb_usage_30d row';
  end if;
  if (select array_agg(k order by k) from jsonb_object_keys(v) k)
     is distinct from array['action', 'called', 'registered', 'uncalled', 'window_days'] then
    raise exception '0879 FAILED: verb_usage_30d keys are %', (select array_agg(k order by k) from jsonb_object_keys(v) k);
  end if;
  if exists (select 1 from jsonb_array_elements(v -> 'uncalled') u
              where (select array_agg(k order by k) from jsonb_object_keys(u) k)
                    is distinct from array['authority_only', 'human_only', 'last_called_at', 'verb', 'write']) then
    raise exception '0879 FAILED: an uncalled entry does not carry exactly verb, write, human_only, authority_only, last_called_at';
  end if;
  if (select count(*) from public.v_integrity_digest) <> 6 then
    raise exception '0879 FAILED: v_integrity_digest has % rows, expected 6', (select count(*) from public.v_integrity_digest);
  end if;

  select count(*) into v_registry
    from ops.scac_mutation_registry_entry e
   where e.ingress_key like 'mcp-tool:%'
     and e.registry_version = (select v2.registry_version from ops.scac_mutation_registry_version v2
                                order by split_part(v2.registry_version, '.v', 2)::integer desc limit 1);
  if v_registry = 0 or (v ->> 'registered')::integer <> v_registry then
    raise exception '0879 FAILED: registered is %, the current sealed registry has % mcp-tool entries', v ->> 'registered', v_registry;
  end if;
  if (v ->> 'called')::integer + jsonb_array_length(v -> 'uncalled') <> v_registry then
    raise exception '0879 FAILED: called % + uncalled % does not equal registered %',
      v ->> 'called', jsonb_array_length(v -> 'uncalled'), v_registry;
  end if;

  select (select count(*) from public.tool_call) + (select count(*) from public.tool_read_call)
    into v_ledger_rows;
  if v_ledger_rows = 0 and jsonb_array_length(v -> 'uncalled') <> v_registry then
    raise exception '0879 FAILED: both ledgers are empty yet % of % registered verbs read as called',
      v_registry - jsonb_array_length(v -> 'uncalled'), v_registry;
  end if;
end $$;
