-- rollback: restore the view from 0916_deal_phase_start_dates.sql.
create or replace view v_deal_room_phase_start as
with ordered as (
  select e.subject_id, e.id, e.recorded_at, e.occurred_at, e.verb, e.old_value, e.new_value,
         sum(case when e.verb = 'revert-deal-field' then 0 else 1 end)
           over (partition by e.subject_id order by e.recorded_at, e.id) as change_group
    from event e
   where e.subject_type = 'deal' and e.field = 'phase'
), effective as (
  select subject_id, change_group,
         (array_agg(new_value order by recorded_at, id))[1] as new_value,
         (array_agg(old_value order by recorded_at, id))[1] as old_value,
         (array_agg(occurred_at order by recorded_at, id))[1] as occurred_at,
         count(*) filter (where verb = 'revert-deal-field') as undos
    from ordered
   group by subject_id, change_group
)
select subject_id as deal_id, new_value->>'phase' as phase,
       (min(occurred_at) at time zone 'America/Chicago')::date as started_on
  from effective
 where change_group > 0 and undos % 2 = 0
   and nullif(new_value->>'phase', '') is not null
   and old_value->>'phase' is distinct from new_value->>'phase'
 group by subject_id, new_value->>'phase';
