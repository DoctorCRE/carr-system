-- rollback: drop view v_email_deal_contact; end email_contact participants and remove that role; move confirmed outlook-intake rows back to pool, then restore the four-status check.
-- expand-contract: expand — widens candidate_pool status with 'confirmed' and adds one reader view; nothing is removed.
-- lock-review: candidate_pool is a few thousand rows; swapping one check constraint is a brief lock and a single validation scan.
alter table public.candidate_pool drop constraint candidate_pool_status_check;
alter table public.candidate_pool add constraint candidate_pool_status_check
  check (status = any (array['pool','promoted','suppressed_dup','declined','confirmed']));
-- A confirmed email sender on a deal: the partner knows the mail belongs there, not the sender's side.
insert into public.participant_role(slug,label,side) values ('email_contact','Email contact','party')
  on conflict (slug) do nothing;
-- The deal contacts the Outlook intake matches beside client, lead and vendor records: current
-- participants of open deals whose party holds no such role (a role holder already matches by its ref).
create view public.v_email_deal_contact as
select dp.deal_id as "Deal ID", d.name as "Name", p.email as "Email"
from public.deal_participant dp
join public.deal d on d.id=dp.deal_id
join public.party p on p.id=dp.party_id
where dp.to_at is null and d.phase<>'closed' and d.closed_on is null and d.invoiced_on is null
  and p.merged_into is null and p.deleted_at is null and p.email is not null
  and not exists (select 1 from public.v_ref_index r where r.party_id=p.id
    and r.subject_type in ('client','lead','vendor') and not r.merged);
grant select on public.v_email_deal_contact to carr_reader, carr_writer;
