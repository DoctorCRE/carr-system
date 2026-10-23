-- PR #2073 measured lifecycle/name scans repeated for every workspace lead.
-- Aggregate live client keys once per statement; retain the lifecycle view as
-- the shared policy used by the workspace, Claim and Link.
-- rollback: restore the prior definitions from 0845 and 0847; no stored data changes.
-- lock-review: CREATE OR REPLACE VIEW locks the two views, not business rows.
create or replace view v_lead_workspace_lifecycle as
with live_clients as materialized (
  select cl.id,cl.party_id,cp.org_id
  from client cl join party cp on cp.id=cl.party_id
  where cl.merged_into is null and cp.merged_into is null and cp.deleted_at is null
), client_parties as materialized (
  select distinct party_id from live_clients
), client_orgs as materialized (
  select distinct org_id from live_clients where org_id is not null
), deal_clients as materialized (
  select distinct client_id from deal
)
select l.id,l.party_id,l.client_id,l.stage,l.suppressed,l.owner_id,
       p.merged_into is null and p.deleted_at is null as live_party,
       p.contact_state,
       (l.suppressed or l.stage='do_not_contact' or p.contact_state='do_not_contact') as do_not_contact,
       not(l.suppressed or l.stage='do_not_contact' or p.contact_state='do_not_contact') as contact_eligible,
       same_party.party_id is not null as is_client,
       -- These are the exact conversion/same_party/same_org bridge bases.
       -- Once tombstones are excluded, merge-survivor equality is ID equality.
       -- A self-only shared org already qualifies through same_party.
       (p.merged_into is null and (
         converted.id is not null or (p.deleted_at is null and (
           same_party.party_id is not null or org_party.party_id is not null or siblings.org_id is not null
         ))
       )) as linked_client,
       deals.client_id is not null as is_deal
from lead l join party p on p.id=l.party_id
left join live_clients converted on converted.id=l.client_id
left join client_parties same_party on same_party.party_id=p.id
left join client_parties org_party on org_party.party_id=p.org_id
left join client_orgs siblings on siblings.org_id=p.org_id
left join deal_clients deals on deals.client_id=l.client_id;

create or replace view v_lead_workspace as
with name_matches as materialized (
  select p.id as party_id,
         jsonb_agg(jsonb_build_object('client_id',cl.id,'name',cp.name) order by cl.id) as items
  from (select distinct party_id from lead where not suppressed) leads
  join party p on p.id=leads.party_id
  join party cp on lower(trim(cp.name))=lower(trim(p.name)) and cp.id<>p.id
  join client cl on cl.party_id=cp.id
  where p.merged_into is null and p.deleted_at is null
    and cl.merged_into is null and cp.merged_into is null and cp.deleted_at is null
  group by p.id
), latest_moves as materialized (
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
left join name_matches matches on matches.party_id=p.id
left join latest_moves last_move on last_move.lead_id=b.id
where not life.suppressed and life.live_party;
