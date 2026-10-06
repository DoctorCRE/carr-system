-- V1 W15 producer: additive invoice/close fields for existing deal reads.
-- Preserve invoice-eligible board membership and the existing column order.
-- Client benefit (deal.won_value) and commission (commission.gross_amount)
-- remain separate; neither is projected or derived here.
create or replace view v_deal_board as
select d.id, d.name, c.roster_ref as client_ref, pc.name as client_name,
       d.deal_type, d.phase, ph.sort as phase_sort, d.segment, d.outcome,
       lead_actor.slug as lead_owner, lt.last_touch,
       d.notes_path, d.invoiced_on, d.closed_on, d.lane
from deal d
join client c on c.id = d.client_id
join party pc on pc.id = c.party_id
join deal_phase ph on ph.slug = d.phase
left join deal_participant dp on dp.deal_id = d.id and dp.role = 'lead' and dp.to_at is null
left join actor lead_actor on lead_actor.id = dp.actor_id
left join v_last_touch lt on lt.subject_type = 'deal' and lt.subject_id = d.id;

create or replace view v_deal_room_board as
select d.id, d.name, d.deal_type as type, d.phase, d.owner, d.attention,
       d.next_date,
       coalesce(
         (select n.description from next_action n
           where n.subject_type = 'deal' and n.subject_id = d.id and n.status = 'open'
           order by n.updated_at desc, n.id desc limit 1),
         (select n.text from deal_note n
           where n.deal_id = d.id and n.kind = 'next_step'
           order by n.created_at desc, n.id desc limit 1)
       ) as next_step,
       d.city as market,
       d.segment,
       c.id as client_id,
       c.roster_ref as client_ref,
       cp.name as client_name,
       vca.account_client_id,
       vca.account_client_ref,
       vca.account_name,
       ao.slug as account_owner,
       dma.agent_name as market_agent,
       dma.agent_party_id as market_agent_party_id,
       lt.last_touch,
       (select max(i.reviewed_at) from deal_review_item i
         join deal_review_session s on s.id = i.session_id
        where i.deal_id = d.id and i.disposition = 'reviewed' and s.status = 'completed') as last_review_at,
       case when vca.account_client_id is null then 'team' else 'national_account' end as workspace_kind,
       d.operating_state,
       d.parking_reason,
       d.parking_note,
       d.parked_at,
       pa.slug as parked_by,
       d.invoiced_on,
       d.closed_on,
       d.lane,
       d.outcome
  from deal d
  join client c on c.id = d.client_id
  join party cp on cp.id = c.party_id
  left join v_client_account vca on vca.client_id = c.id and vca.is_sub_client
  left join national_account_owner nao on nao.account_client_id = vca.account_client_id
  left join actor ao on ao.id = nao.owner_actor_id
  left join deal_market_assignment dma on dma.deal_id = d.id
  left join v_last_touch lt on lt.subject_type = 'deal' and lt.subject_id = d.id
  left join actor pa on pa.id = d.parked_by
 where d.invoiced_on is null;


-- Keep the all-deal reconciliation read available after invoicing.
create or replace view v_deal_reconciliation_read as
select id, name, salesforce_id, version as base_version, phase, outcome,
       closed_on, invoiced_on, lane
  from deal;

-- CREATE OR REPLACE preserves these views' existing grants and ownership.
