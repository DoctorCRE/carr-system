-- rollback: forward-only; restore prior read definitions in a new migration if needed.
-- lock-review: replace two views without rewriting business rows or changing grants.

create or replace view v_lead_workspace_lifecycle as
with live_clients as materialized (
  select cl.id,cl.party_id from client cl join party cp on cp.id=cl.party_id
  where cl.merged_into is null and cp.merged_into is null and cp.deleted_at is null
), client_parties as (
  select distinct party_id from live_clients
), linked_leads as materialized (
  select distinct bridge.lead_id from v_lead_client_link bridge
  join live_clients cl on cl.id=bridge.client_id where not bridge.either_merged
), deal_clients as (
  select distinct client_id from deal where client_id is not null
)
select l.id,l.party_id,l.client_id,l.stage,l.suppressed,l.owner_id,
       p.merged_into is null and p.deleted_at is null as live_party,
       p.contact_state,
       (l.suppressed or l.stage='do_not_contact' or p.contact_state='do_not_contact') as do_not_contact,
       not(l.suppressed or l.stage='do_not_contact' or p.contact_state='do_not_contact') as contact_eligible,
       cp.party_id is not null as is_client,
       bridge.lead_id is not null as linked_client,
       d.client_id is not null as is_deal
from lead l join party p on p.id=l.party_id
left join client_parties cp on cp.party_id=p.id
left join linked_leads bridge on bridge.lead_id=l.id
left join deal_clients d on d.client_id=l.client_id;

create or replace view v_lead_workspace as
with live_clients as materialized (
  select cl.id,cp.id as party_id,cp.name,lower(trim(cp.name)) as normalized_name
  from client cl join party cp on cp.id=cl.party_id
  where cl.merged_into is null and cp.merged_into is null and cp.deleted_at is null
), possible_clients as materialized (
  select p.id as party_id,
    jsonb_agg(jsonb_build_object('client_id',cl.id,'name',cl.name) order by cl.id) as items
  from party p join live_clients cl on cl.normalized_name=lower(trim(p.name)) and cl.party_id<>p.id
  group by p.id
), last_moves as materialized (
  select distinct on (e.lead_id) e.lead_id,
    jsonb_build_object('event_id',e.event_id,'idempotency_key',e.idempotency_key,
      'from',e.prior_stage,'to',e.stage,'occurred_at',e.occurred_at,
      'automatic',e.automatic,'reason',e.reason,'evidence_date',e.stage_review->>'evidence_date',
      'undone',e.stage_review->>'undo_event_id') as item
  from v_lead_stage_transition e order by e.lead_id,e.mutation_order desc
)
select b.id,b.registry_ref,b.name,b.specialty,b.city,b.county,b.state,b.lane,b.stage,
       b.stage_label,b.stage_sort,b.score,b.segment,b.suppressed,b.est_lease_event,
       b.event_confidence,b.last_touch,b.next_action_date,b.owner,b.owner_label,
       b.base_version,b.created_at,b.updated_at,
       life.party_id,life.client_id,life.is_client,life.linked_client,life.is_deal,
       life.contact_state,life.do_not_contact,life.contact_eligible,
       case when p.kind='person' then p.name end as doctor_name,
       org.name as practice_name,p.name as entity_name,
       coalesce(matches.items,'[]'::jsonb) as possible_clients,
       last_move.item as last_stage_move,l.score_reason
from v_lead_board b join v_lead_workspace_lifecycle life on life.id=b.id
join lead l on l.id=b.id
join party p on p.id=life.party_id left join party org on org.id=p.org_id
left join possible_clients matches on matches.party_id=p.id
left join last_moves last_move on last_move.lead_id=b.id
where not life.suppressed and life.live_party;
