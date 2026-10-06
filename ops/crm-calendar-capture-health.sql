-- Read-only, counts-only standing check for the calendar-to-activity ingress.
-- Owner: CRM capture. Remediation: inspect the calendar-eventkit failure class,
-- complete unknown-attendee intake evidence, then rerun the scheduled capture.
-- Verification/autoclear: a newer succeeded run clears the incomplete state;
-- a subsequent fresh run keeps it clear. Exact activity calls are shown
-- separately because they can succeed while unknown intake still refuses.
with runs as (
  select r.state, r.exit_code, r.observed_at,
         row_number() over (order by r.observed_at desc, r.id desc) as recency
    from ops.run r
    join ops.service s on s.id = r.service_id
   where s.key = 'calendar-eventkit' and r.environment = 'production'
), run_summary as (
  select max(observed_at) filter (where state = 'succeeded') as last_success_at,
         max(observed_at) filter (where state = 'skipped') as last_skip_at,
         max(observed_at) filter (where recency = 1) as last_run_at,
         max(state) filter (where recency = 1) as last_run_state,
         max(exit_code) filter (where recency = 1) as last_exit_code
    from runs
), calls as (
  select count(*) filter (where created_at >= now() - interval '7 days') as exact_calls_7d,
         max(created_at) as last_exact_call_at
    from public.tool_call
   where verb = 'log-activity' and idempotency_key like 'calcap-%'
)
select case
         when last_run_at is null then 'missing_run'
         when last_run_state <> 'succeeded' then 'incomplete'
         when last_run_at < now() - interval '4 days' then 'stale'
         else 'healthy'
       end as calendar_capture_state,
       last_run_at, last_run_state, last_exit_code,
       last_success_at, last_skip_at,
       exact_calls_7d, last_exact_call_at,
       'CRM capture'::text as owner,
       'Resolve unknown-attendee intake evidence and rerun calendar-eventkit'::text
         as remediation,
       'Confirm a newer succeeded run; exact log-activity calls are partial progress only'::text
         as verification
  from run_summary cross join calls;
