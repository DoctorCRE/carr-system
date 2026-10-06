-- Count a pending pre-call receipt as unknown spend until the final receipt's
-- transaction marks it settled. Both rows use the existing append-only door;
-- this migration adds no database privilege or new write function.
create or replace function ops.jev_call_receipt_integrity()
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, ops
as $$
declare
  v_total bigint;
  v_orphans bigint;
  v_orphan_ids jsonb;
  v_triggers jsonb;
  v_enabled boolean;
  v_day date;
  v_calls bigint;
  v_tokens bigint;
  v_unknown bigint;
  v_pending bigint;
begin
  select count(*) into v_total from ops.jev_call_receipt;
  with orphan as (
    select r.receipt_id, r.recorded_at
      from ops.jev_call_receipt r
     where not exists (
       select 1 from public.tool_call t
        where t.idempotency_key = r.idempotency_key
          and t.verb = case when r.model_answered = 'jev-attempt-pending'
                            then 'ask-jev-attempt' else 'ask-jev' end
          and t.actor_id = r.actor_id
          and t.response->>'receipt_id' = r.receipt_id::text)
  )
  select count(*),
         coalesce((select jsonb_agg(o2.receipt_id order by o2.recorded_at desc, o2.receipt_id)
                     from (select * from orphan order by recorded_at desc, receipt_id limit 20) o2), '[]'::jsonb)
    into v_orphans, v_orphan_ids
    from orphan;
  with expected(name) as (
    values ('jev_call_receipt_append_only'), ('jev_call_receipt_no_truncate')
  )
  select jsonb_agg(jsonb_build_object(
           'name', e.name,
           'tgenabled', t.tgenabled::text,
           'enabled', coalesce(t.tgenabled in ('O','A'), false)) order by e.name),
         bool_and(coalesce(t.tgenabled in ('O','A'), false))
    into v_triggers, v_enabled
    from expected e
    left join pg_catalog.pg_trigger t
      on t.tgname = e.name
     and t.tgrelid = 'ops.jev_call_receipt'::regclass
     and not t.tgisinternal;

  v_day := (clock_timestamp() at time zone 'UTC')::date;
  select count(*) filter (where r.usage is not null),
         coalesce(sum(case when r.usage->>'input_tokens' ~ '^[0-9]{1,15}$'
                           then (r.usage->>'input_tokens')::bigint else 0 end), 0),
         count(*) filter (where (r.usage is null and r.model_answered <> 'jev-attempt-pending'
                                   and coalesce(t.response->>'cache_hit', 'false') <> 'true')
                            or (r.usage is not null and
                                coalesce(r.usage->>'input_tokens', '') !~ '^[0-9]{1,15}$'))
    into v_calls, v_tokens, v_unknown
    from ops.jev_call_receipt r
    left join public.tool_call t
      on t.idempotency_key = r.idempotency_key
     and t.verb = case when r.model_answered = 'jev-attempt-pending'
                       then 'ask-jev-attempt' else 'ask-jev' end
     and t.actor_id = r.actor_id
     and t.response->>'receipt_id' = r.receipt_id::text
   where r.recorded_at >= v_day::timestamp at time zone 'UTC'
     and r.recorded_at < (v_day + 1)::timestamp at time zone 'UTC';

  -- An unfinished attempt remains uncertain after UTC midnight. Count it
  -- once until the matching settlement row commits.
  select count(*) into v_pending from ops.jev_call_receipt a
   where a.model_answered = 'jev-attempt-pending'
     and not exists (
       select 1 from public.tool_call t
        where t.idempotency_key = a.idempotency_key
          and t.verb = 'ask-jev-attempt' and t.actor_id = a.actor_id
          and t.response->>'receipt_id' = a.receipt_id::text
          and t.response->>'cache_hit' = 'true');

  return jsonb_build_object(
    'receipts_total', v_total,
    'receipts_without_tool_call', jsonb_build_object('count', v_orphans, 'receipt_ids', v_orphan_ids),
    'trigger_enabled', v_enabled,
    'triggers', v_triggers,
    'daily_usage', jsonb_build_object('utc_day', v_day, 'calls', v_calls,
                                     'input_tokens', v_tokens, 'unknown', v_unknown + v_pending,
                                     'pending_attempts', v_pending),
    'checked_at', to_jsonb(clock_timestamp()));
end;
$$;

comment on function ops.jev_call_receipt_integrity() is
  'Integrity and daily usage audit for append-only Worker Jev receipts, including pending billable attempts, cache-hit exclusion and missing-usage count.';
