-- rollback: restore the next_step deal constraint after resolving non-deal proposals, then drop v_email_non_deal_step.
-- expand-contract: expand — existing proposal rows and review verbs also carry non-deal next steps.
-- lock-review: short constraint replacement; no row rewrite or destructive change.

alter table public.email_proposal drop constraint email_proposal_shape;
alter table public.email_proposal add constraint email_proposal_shape check (
  (kind='phase_move' and deal_id is not null and task_text is null and next_date is null and owner is null and sender_role is null)
  or (kind='next_step' and task_text is not null and next_date is not null and owner is not null and sender_role is null)
  or (kind='reply_draft' and task_text is null and next_date is null and owner is not null and
    length(draft_subject) between 1 and 998 and length(btrim(draft_body)) between 1 and 20000 and sender_role is null)
  or (kind='file_to_deal' and deal_id is not null and task_text is null and next_date is null and owner is null and sender_role is not null and draft_subject is null and draft_body is null)
  or (kind='possible_new_deal' and deal_id is null and next_date is null and owner is null and sender_role is not null and draft_subject is null and draft_body is null));

create view public.v_email_non_deal_step as
select a.id as activity_id,a.version as activity_version,a.summary,a.detail,a.occurred_at,
  s.subject_type,s.subject_id,s.subject_version,s.party_version,s.name,
  coalesce((select jsonb_agg(jsonb_build_object('id',n.id,'version',n.version,'owner',o.slug,
    'description',n.description,'due_date',n.due_on,'hold_until',n.hold_until) order by n.id)
    from public.next_action n join public.actor o on o.id=n.owner_id
    where n.subject_type=s.subject_type and n.subject_id=s.subject_id
      and n.status='open' and n.item_title is null),'[]'::jsonb) as actions
from public.activity a join (
  select 'client'::text as subject_type,cl.id as subject_id,cl.version as subject_version,
    p.version as party_version,p.name from public.client cl join public.party p on p.id=cl.party_id
    where cl.merged_into is null and p.merged_into is null and p.deleted_at is null
  union all
  select 'lead',l.id,l.version,p.version,p.name from public.lead l join public.party p on p.id=l.party_id
    where not l.suppressed and l.stage not in ('lost','do_not_contact')
      and p.merged_into is null and p.deleted_at is null
  union all
  select 'vendor',v.id,v.version,p.version,p.name from public.vendor v join public.party p on p.id=v.party_id
    where v.merged_into is null and p.merged_into is null and p.deleted_at is null
) s on (s.subject_type='client' and s.subject_id=a.client_id)
  or (s.subject_type='lead' and s.subject_id=a.lead_id)
  or (s.subject_type='vendor' and s.subject_id=a.vendor_id)
where a.deal_id is null and num_nonnulls(a.client_id,a.lead_id,a.vendor_id)=1
  and a.kind in ('email_in','email_out') and a.occurred_at<=now();

grant select on public.v_email_non_deal_step to carr_reader,carr_writer;
comment on view public.v_email_non_deal_step is
  'Email next-step review for one explicit activity subject. Sender participation never selects a deal. Pending proposals still blocked 30 days after creation are dismissed with their preview error by the router maintenance pass; evidence and resolution remain in email_proposal.';
