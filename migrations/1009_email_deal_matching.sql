-- rollback: remove matching proposals before restoring the original kind and shape constraints; retain captured email context.
-- lock-review: nullable column additions and constraint replacements on small activity/proposal tables; no table rewrite.
alter table public.activity add column email_context jsonb;
alter table public.activity add constraint activity_email_context_object check (email_context is null or jsonb_typeof(email_context)='object');
alter table public.email_proposal add column sender_role text check (sender_role in ('client','landlord_side','lender','attorney','other_vendor'));
alter table public.email_proposal alter column deal_id drop not null;
alter table public.email_proposal drop constraint email_proposal_kind_check;
alter table public.email_proposal add constraint email_proposal_kind_check check (kind in ('phase_move','next_step','file_to_deal','possible_new_deal'));
alter table public.email_proposal drop constraint email_proposal_check;
alter table public.email_proposal add constraint email_proposal_check check (
  (kind='phase_move' and deal_id is not null and task_text is null and next_date is null and owner is null and sender_role is null)
  or (kind='next_step' and deal_id is not null and task_text is not null and next_date is not null and owner is not null and sender_role is null)
  or (kind='file_to_deal' and deal_id is not null and task_text is null and next_date is null and owner is null and sender_role is not null)
  or (kind='possible_new_deal' and deal_id is null and next_date is null and owner is null and sender_role is not null));
alter table public.email_proposal drop constraint email_proposal_kind_deal_id_activity_id_key;
alter table public.email_proposal add constraint email_proposal_kind_deal_id_activity_id_key unique nulls not distinct (kind,deal_id,activity_id);
insert into public.participant_role(slug,label,side) values
  ('client','Client','party'),('landlord_side','Landlord side','party'),('lender','Lender','party'),
  ('attorney','Attorney','party'),('other_vendor','Other vendor','party') on conflict(slug) do nothing;

grant select (deal_id,party_id,role,to_at) on public.deal_participant to carr_reader;

grant select (org_id) on public.party to carr_reader;
alter table public.email_proposal drop constraint email_proposal_evidence_quote_check;
alter table public.email_proposal add constraint email_proposal_evidence_quote_check check(length(evidence_quote) between 1 and 2000);
