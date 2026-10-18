-- W4: closed assignments remain visible until invoiced; payments stay in commission.
-- This granted projection owns board membership for both board reads, account
-- counters and the Command Center. Active work means an eligible, active row;
-- outcome does not remove uninvoiced work, and parked rows stay restorable.
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
       d.invoiced_on
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

-- Count the same assignment population as the account's drill-down, preserving
-- the existing column order and zero-count accounts.
create or replace view v_deal_room_account as
select c.id as account_client_id,
       c.roster_ref as account_client_ref,
       p.name as account_name,
       a.slug as account_owner,
       count(b.id) filter (where b.operating_state = 'active') as open_deals,
       count(b.id) filter (where b.operating_state = 'active' and b.attention) as attention_deals,
       count(b.id) filter (where b.operating_state = 'active' and b.next_date < current_date) as overdue_deals,
       count(b.id) filter (where b.operating_state = 'active' and
         (b.last_touch is null or b.last_touch < current_date - 14)) as stale_deals,
       (select max(rs.ended_at) from deal_review_session rs
         where rs.account_client_id = c.id and rs.status = 'completed') as last_review_at,
       count(b.id) filter (where b.operating_state = 'parked') as parked_deals
  from client c
  join party p on p.id = c.party_id
  left join national_account_owner nao on nao.account_client_id = c.id
  left join actor a on a.id = nao.owner_actor_id
  left join v_deal_room_board b on b.account_client_id = c.id
 where c.client_type = 'national_account' and c.merged_into is null
 group by c.id, c.roster_ref, p.name, a.slug;

grant select on v_deal_room_board, v_deal_room_account to carr_reader, carr_writer;

-- Latest phase event only: a manual correction removes an older automatic badge.
-- occurred_at is the event evidence date, distinct from the database recording time.
create or replace view v_deal_room_phase_change as
select distinct on (e.subject_id) e.subject_id as deal_id, e.id as event_id,
       e.old_value->>'phase' as prior_phase, e.new_value->>'phase' as phase,
       e.cause in ('ingest_email','ingest_calendar','ingest_webhook','automation_job','system')
         and e.verb <> 'revert-deal-field'
         and not (e.verb in ('patch-deal-field','resolve-conflict') and coalesce(e.via,'')='dealroom-cookie'
           and coalesce(e.client_id,'')='dealroom-pwa') as automatic,
       coalesce(nullif(e.agent_rationale,''),'phase changed') as reason,
       to_jsonb(e.occurred_at)#>>'{}' as evidence_date,
       to_jsonb(e.recorded_at)#>>'{}' as recorded_at
  from event e
 where e.subject_type='deal' and e.field='phase'
 order by e.subject_id, e.recorded_at desc, e.id desc;
grant select on v_deal_room_phase_change to carr_reader, carr_writer;
