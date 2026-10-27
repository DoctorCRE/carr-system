-- rollback: forward fix only; submitted text and delivery receipts must be retained.
-- expand-contract: widen proposal kind/status checks while retaining prior phase/next-step states.
-- lock-review: brief catalog locks on activity and email_proposal; no data rewrite.
alter table public.activity add column email_source jsonb;
alter table public.activity add constraint activity_email_source_check check ((email_source is null or
  (kind='email_in' and jsonb_typeof(email_source)='object' and
   email_source ?& array['mailbox_owner','sender_email'] and
   email_source->>'mailbox_owner' in ('joe','dell') and email_source->>'sender_email' ~ '^[^[:space:]<>@]+@[^[:space:]<>@]+[.][^[:space:]<>@]+$')) is true);

alter table public.email_proposal
  add column draft_subject text,
  add column draft_body text,
  add column submitted_email jsonb,
  add column submitted_at timestamptz,
  add column send_result jsonb,
  drop constraint email_proposal_kind_check,
  drop constraint email_proposal_status_check,
  drop constraint email_proposal_check,
  drop constraint email_proposal_check1;
alter table public.email_proposal
  add constraint email_proposal_kind_check check (kind in ('phase_move','next_step','reply_draft')),
  add constraint email_proposal_status_check check (status in ('pending','confirmed','dismissed','approved_to_send','sent','failed')),
  add constraint email_proposal_shape check ((
    (kind='phase_move' and task_text is null and next_date is null and owner is null and draft_subject is null and draft_body is null) or
    (kind='next_step' and task_text is not null and next_date is not null and owner is not null and draft_subject is null and draft_body is null) or
    (kind='reply_draft' and task_text is null and next_date is null and owner is not null and
      length(draft_subject) between 1 and 998 and length(btrim(draft_body)) between 1 and 20000)) is true),
  add constraint email_proposal_resolution check (
    (status='pending' and resolved_by is null and resolved_at is null and resolution_reason is null) or
    (status='confirmed' and kind<>'reply_draft' and resolved_by is not null and resolved_at is not null) or
    (status='dismissed' and resolved_by is not null and resolved_at is not null and resolution_reason is not null) or
    (status in ('approved_to_send','sent','failed') and kind='reply_draft' and resolved_by is not null and resolved_at is not null)),
  add constraint email_proposal_submission check ((
    (submitted_email is null and submitted_at is null and status not in ('approved_to_send','sent','failed')) or
    (kind='reply_draft' and submitted_at is not null and status in ('approved_to_send','sent','failed') and
      jsonb_typeof(submitted_email)='object' and submitted_email ?& array['subject','body','sha256','to','cc'])) is true),
  add constraint email_proposal_send_result check ((
    (status not in ('sent','failed') and send_result is null) or
    (status in ('sent','failed') and send_result->>'status'=status)) is true);
create index email_proposal_outbox on public.email_proposal(owner,submitted_at,id) where status='approved_to_send';

create function ops.guard_captured_email_source() returns trigger language plpgsql set search_path=pg_catalog as $$
begin
  if new.email_source is distinct from old.email_source then
    raise exception 'email_source_immutable' using errcode='23514';
  end if;
  return new;
end $$;
create trigger captured_email_source_immutable before update of email_source on public.activity
  for each row execute function ops.guard_captured_email_source();

revoke all on function ops.guard_captured_email_source() from public;
