-- rollback: restore the prior context and account reads, preserving hardened search_path, then drop the open view.
-- expand-contract: expand; preserve both read interfaces and their grants.
-- lock-review: replace read definitions only; no business rows are changed.
create view public.v_open_deal_room_board as
select * from public.v_deal_room_board
 where outcome is null and closed_on is null and invoiced_on is null
   and phase not in ('closed', 'invoiced');
grant select on public.v_open_deal_room_board to carr_reader, carr_writer;

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
  left join v_open_deal_room_board b on b.account_client_id = c.id
 where c.client_type = 'national_account' and c.merged_into is null
 group by c.id, c.roster_ref, p.name, a.slug;

create or replace function capture_call_context(requested_deal_ids uuid[])
returns table (
  deal_id uuid,
  deal_name text,
  owner text,
  operating_state text,
  participant_party_id uuid,
  participant_party_ref text,
  participant_name text,
  participant_email text,
  participant_role text
)
language sql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select d.id, d.name, d.owner, d.operating_state, dp.party_id, p.ref, p.name, p.email, dp.role
    from public.v_open_deal_room_board d
    left join public.deal_participant dp on dp.deal_id=d.id and dp.to_at is null
    left join public.party p on p.id=dp.party_id
   where d.id = any(requested_deal_ids)
     and d.operating_state = 'active'
$$;
