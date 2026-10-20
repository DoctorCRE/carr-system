-- rollback: remove the phase_started_on projections, then drop v_deal_room_phase_start.
-- First observed entry into each phase, using the evidence clock in the team's timezone.
create or replace view v_deal_room_phase_start as
select e.subject_id as deal_id,
       e.new_value->>'phase' as phase,
       (min(e.occurred_at) at time zone 'America/Chicago')::date as started_on
  from event e
 where e.subject_type='deal' and e.field='phase'
   and nullif(e.new_value->>'phase','') is not null
   and e.old_value->>'phase' is distinct from e.new_value->>'phase'
 group by e.subject_id, e.new_value->>'phase';

grant select on v_deal_room_phase_start to carr_reader, carr_writer;
