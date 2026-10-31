-- rollback: forward fix only; retain captured email identities and correspondence.
-- expand-contract: add optional immutable identity to existing email activity rows.
-- lock-review: nullable activity column, brief catalog locks and partial index on new empty column; no backfill.
alter table public.activity add column email_identity jsonb;
alter table public.activity add constraint activity_email_identity_check check ((email_identity is null or
  (kind in ('email_in','email_out') and jsonb_typeof(email_identity)='object' and
   email_identity ?& array['mailbox_owner','mailbox_account','item_id','subject'] and
   email_identity->>'mailbox_owner' in ('joe','dell') and
   length(email_identity->>'mailbox_account') between 3 and 254 and
   length(email_identity->>'item_id') between 1 and 500 and
   length(email_identity->>'subject') > 0)) is true);
create unique index activity_email_identity on public.activity
  ((email_identity->>'mailbox_owner'),(email_identity->>'mailbox_account'),(email_identity->>'item_id'),(email_identity->>'subject'))
  where email_identity is not null;
create function ops.guard_email_identity() returns trigger language plpgsql set search_path=pg_catalog as $$
begin
  if new.email_identity is distinct from old.email_identity then
    raise exception 'email_identity_immutable' using errcode='23514';
  end if;
  return new;
end $$;
create trigger email_identity_immutable before update of email_identity on public.activity
  for each row execute function ops.guard_email_identity();
revoke all on function ops.guard_email_identity() from public;
