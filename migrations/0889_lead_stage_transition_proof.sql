-- rollback: forward-only; retain immutable events and replace read projections in a new migration.
-- lock-review: CREATE OR REPLACE updates two existing views; no business-row rewrite or privilege change.

create or replace view v_lead_stage_transition as
select e.id as event_id,e.subject_id as lead_id,e.mutation_order,e.occurred_at,
       e.old_value->>'stage' as prior_stage,e.new_value->>'stage' as stage,
       e.cause,e.cause in ('automation_job','ingest_email','ingest_calendar','system') as automatic,
       e.agent_rationale as reason,e.idempotency_key,e.new_value->'stage_review' as stage_review,
       e.actor_id,
       e.new_value->'transition_proof'->>'actor_slug' as actor_slug,
       (e.new_value->'transition_proof'->>'before_version')::integer as before_version,
       (e.new_value->'transition_proof'->>'after_version')::integer as after_version
from event e
where e.subject_type='lead' and e.new_value->>'stage' is not null;

create or replace view v_lead_workspace_detail as
select b.id,jsonb_build_object('phone',p.phone,'email',p.email,'notes',l.notes,
 'est_lease_event',l.est_lease_event,'segment',l.segment,
 'correspondence',coalesce(c.items,'[]'::jsonb),
 'stage_history',coalesce(h.items,'[]'::jsonb),
 'correspondence_coverage','captured_entries_only','history_limit',100) as detail
from v_lead_workspace b join lead l on l.id=b.id join party p on p.id=l.party_id
left join lateral (
 select jsonb_agg(to_jsonb(a) - 'lead_id' order by a.occurred_at desc,a.id desc) as items from (
  select id,lead_id,kind,occurred_at,summary,detail,source,connected
  from activity where lead_id=b.id order by occurred_at desc,id desc limit 100
 ) a
) c on true
left join lateral (
 select jsonb_agg(to_jsonb(e) - 'lead_id' order by e.mutation_order desc) as items from (
  select * from v_lead_stage_transition where lead_id=b.id order by mutation_order desc limit 100
 ) e
) h on true;
