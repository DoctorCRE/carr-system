-- rollback: forward-only; retain lifecycle observations and prior-action notes.
-- expand-contract: expand; guard new proposal and summary writes without changing stored history.
-- lock-review: trigger metadata only on email_proposal and email_message_summary; no row rewrite.
create function public.guard_email_lifecycle_action() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
declare context jsonb;
begin
  select a.email_context into context from public.activity a where a.id=new.activity_id for update;
  if coalesce(context#>>'{lifecycle,state}','live') <> 'live' then
    raise exception 'email_not_live: source mail is terminal' using errcode='23514';
  end if;
  return new;
end $$;
revoke all on function public.guard_email_lifecycle_action() from public;
create trigger email_proposal_live_source before insert or update on public.email_proposal
  for each row execute function public.guard_email_lifecycle_action();
create trigger email_summary_live_source before insert or update on public.email_message_summary
  for each row execute function public.guard_email_lifecycle_action();

do $home_mail$
declare
  name text;
  definition text;
  updated text;
begin
  foreach name in array array['ops.home_inbox_sources(uuid)','ops.home_inbox_sources_v2(uuid)'] loop
    definition := pg_get_functiondef(name::regprocedure);
    updated := replace(definition, 'where p.kind=''reply_draft''',
      'where coalesce(e.email_context#>>''{lifecycle,state}'',''live'')=''live'' and p.kind=''reply_draft''');
    if updated = definition then raise exception 'email lifecycle Home predicate missing in %',name; end if;
    definition := updated;
    updated := replace(definition, 'where recipient_actor=p_actor',
      'where recipient_actor=p_actor and not exists (select 1 from public.activity mail where n.subject_type=''email'' and mail.id::text=n.subject_ref and coalesce(mail.email_context#>>''{lifecycle,state}'',''live'')<>''live'')');
    if updated = definition then raise exception 'email lifecycle notification predicate missing in %',name; end if;
    execute updated;
  end loop;
end $home_mail$;
