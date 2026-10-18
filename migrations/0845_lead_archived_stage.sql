insert into lead_stage (slug,label,sort) values ('archived','Archived',110)
on conflict (slug) do update set label=excluded.label,sort=excluded.sort;

-- Stage events are inserted after the lead mutation holds its row lock. A
-- sequence, unlike transaction timestamps or UUIDs, preserves that order.
-- Existing history remains immutable; the current-stage comparison is mandatory
-- before reversing any historical transition.
alter table event add column mutation_order bigint generated always as identity;

create view v_lead_stage_transition as
select e.id as event_id,e.subject_id as lead_id,e.mutation_order,e.occurred_at,
       e.old_value->>'stage' as prior_stage,e.new_value->>'stage' as stage,
       e.cause,e.cause in ('automation_job','ingest_email','ingest_calendar','system') as automatic,
       e.agent_rationale as reason,e.idempotency_key,e.new_value->'stage_review' as stage_review
from event e
where e.subject_type='lead' and e.new_value->>'stage' is not null;

-- One exact lifecycle policy for workspace, Claim and Link. Nullable display
-- refs never participate in identity. Follow all exact bridge bases, rejecting
-- tombstones on both sides, rather than a possibly stale best-link candidate.
create view v_lead_workspace_lifecycle as
select l.id,l.party_id,l.client_id,l.stage,l.suppressed,l.owner_id,
       p.merged_into is null and p.deleted_at is null as live_party,
       p.contact_state,
       (l.suppressed or l.stage='do_not_contact' or p.contact_state='do_not_contact') as do_not_contact,
       not(l.suppressed or l.stage='do_not_contact' or p.contact_state='do_not_contact') as contact_eligible,
       exists(select 1 from client cl join party cp on cp.id=cl.party_id
              where cl.party_id=p.id and cl.merged_into is null and cp.merged_into is null and cp.deleted_at is null) as is_client,
       exists(select 1 from v_lead_client_link bridge
              join client cl on cl.id=bridge.client_id join party cp on cp.id=cl.party_id
              where bridge.lead_id=l.id and not bridge.either_merged and cl.merged_into is null
                and cp.merged_into is null and cp.deleted_at is null) as linked_client,
       exists(select 1 from deal d where d.client_id=l.client_id) as is_deal
from lead l join party p on p.id=l.party_id;

create view v_lead_workspace as
select b.*,life.party_id,life.client_id,life.is_client,life.linked_client,life.is_deal,
       life.contact_state,life.do_not_contact,life.contact_eligible,
       case when p.kind='person' then p.name end as doctor_name,
       org.name as practice_name,p.name as entity_name,
       coalesce(matches.items,'[]'::jsonb) as possible_clients,
       last_move.item as last_stage_move
from v_lead_board b join v_lead_workspace_lifecycle life on life.id=b.id
join party p on p.id=life.party_id left join party org on org.id=p.org_id
left join lateral (
 select jsonb_agg(jsonb_build_object('client_id',cl.id,'name',cp.name) order by cl.id) as items
 from client cl join party cp on cp.id=cl.party_id
 where lower(trim(cp.name))=lower(trim(p.name)) and cp.id<>p.id
   and cl.merged_into is null and cp.merged_into is null and cp.deleted_at is null
) matches on true
left join lateral (
 select jsonb_build_object('event_id',e.event_id,'idempotency_key',e.idempotency_key,
   'from',e.prior_stage,'to',e.stage,'occurred_at',e.occurred_at,
   'automatic',e.automatic,'reason',e.reason,'evidence_date',e.stage_review->>'evidence_date',
   'undone',e.stage_review->>'undo_event_id') as item
 from v_lead_stage_transition e where e.lead_id=b.id order by e.mutation_order desc limit 1
) last_move on true
where not life.suppressed and life.live_party;

create view v_lead_workspace_detail as
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

-- These views are the authenticated reader's deliberate read door. No base
-- table or party.org_id grant is widened. Public cannot use the projections.
revoke all on v_lead_stage_transition,v_lead_workspace_lifecycle,v_lead_workspace,v_lead_workspace_detail from public;
grant select on v_lead_workspace,v_lead_workspace_detail to carr_reader,carr_writer,carr_authority;
grant select on v_lead_workspace_lifecycle,v_lead_stage_transition to carr_writer,carr_authority;
