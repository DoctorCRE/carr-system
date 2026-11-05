-- rollback: retain undated review evidence; forward-only.
-- expand-contract: nullable dates for review; application still requires a literal date.
-- lock-review: short constraint replacement, no table rewrite.
alter table public.email_proposal drop constraint email_proposal_shape;
alter table public.email_proposal add constraint email_proposal_shape check (
  (kind='phase_move' and deal_id is not null and task_text is null and next_date is null and owner is null and sender_role is null)
  or (kind='next_step' and task_text is not null and owner is not null and sender_role is null)
  or (kind='reply_draft' and task_text is null and next_date is null and owner is not null and
    length(draft_subject) between 1 and 998 and length(btrim(draft_body)) between 1 and 20000 and sender_role is null)
  or (kind='file_to_deal' and deal_id is not null and task_text is null and next_date is null and owner is null and sender_role is not null and draft_subject is null and draft_body is null)
  or (kind='possible_new_deal' and deal_id is null and next_date is null and owner is null and sender_role is not null and draft_subject is null and draft_body is null));

create view public.v_email_step_undo as
select p.id as proposal_id,
  (n.status='open' and n.version::text=p.correction_provenance#>>'{email_application,action_version}'
    and (p.deal_id is null or d.version::text=p.correction_provenance#>>'{email_application,subject_version}')) as eligible
from public.email_proposal p
left join public.next_action n on n.id=(p.correction_provenance#>>'{email_application,action_id}')::uuid
left join public.deal d on d.id=p.deal_id
where p.kind='next_step' and p.correction_provenance#>>'{email_application,state}'='applied';
grant select on public.v_email_step_undo to carr_reader,carr_writer;
