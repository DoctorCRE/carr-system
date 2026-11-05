-- rollback: forward-only; retain generation and review evidence.
-- expand-contract: expand; existing router callers retain their contract.
-- lock-review: new table and short trigger installation; bounded proposal backfill.
create function public.email_filing_source_hash(a public.activity) returns text
language sql immutable set search_path=pg_catalog as $$
  select 'sha256:'||encode(sha256(convert_to(jsonb_build_array(a.summary,a.detail,a.email_context,a.email_source,a.email_identity)::text,'UTF8')),'hex')
$$;
create table public.email_filing_application (
  generation uuid primary key default gen_random_uuid(),
  activity_id uuid not null references public.activity(id),
  source_hash text not null,
  deal_id uuid references public.deal(id),
  selection_key text not null,
  authority text not null check(authority in ('review','confirmed','inherited')),
  status text not null check(status in ('review','pending','claimed','applied','suppressed','needs_review','exhausted')),
  reason text,
  attempts integer not null default 0 check(attempts between 0 and 3),
  claim_id uuid,
  claim_until timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  unique(activity_id,source_hash,selection_key),
  check((claim_id is null)=(claim_until is null))
);
create index email_filing_application_pending on public.email_filing_application(generation)
  where status in ('pending','claimed');
create index email_filing_application_source on public.email_filing_application(activity_id,created_at desc,generation desc);
create function public.enqueue_email_filing(source_id uuid,target_id uuid,selection text,basis text) returns void
language plpgsql set search_path=pg_catalog,public as $$
declare source public.activity; existing public.email_filing_application;
begin
  select * into source from public.activity where id=source_id for update;
  select * into existing from public.email_filing_application
    where activity_id=source_id and source_hash=public.email_filing_source_hash(source) and selection_key=selection;
  if found then return; end if;
  if basis<>'review' then
  update public.email_filing_application set status='needs_review',reason='superseded',claim_id=null,claim_until=null
    where activity_id=source_id and status in ('pending','claimed');
  end if;
  insert into public.email_filing_application(activity_id,source_hash,deal_id,selection_key,authority,status)
    values(source_id,public.email_filing_source_hash(source),target_id,selection,basis,
      case when basis='review' then 'review' else 'pending' end) on conflict do nothing;
end $$;
create function public.email_filing_proposal_changed() returns trigger
language plpgsql set search_path=pg_catalog,public as $$
begin
  if new.kind not in ('file_to_deal','possible_new_deal') then return new; end if;
  if new.status='pending' then
    perform public.enqueue_email_filing(new.activity_id,new.deal_id,new.id::text,'review');
  elsif new.status='confirmed' then
    perform public.enqueue_email_filing(new.activity_id,coalesce((new.correction_provenance->>'target_deal')::uuid,new.deal_id),
      new.id::text||':'||new.version::text,'confirmed');
  elsif new.status='dismissed' then
    update public.email_filing_application set status='suppressed',reason='dismissed',claim_id=null,claim_until=null
      where activity_id=new.activity_id and (selection_key=new.id::text or deal_id is not distinct from new.deal_id);
  end if;
  return new;
end $$;
create trigger email_filing_proposal_changed after insert or update of status on public.email_proposal
  for each row execute function public.email_filing_proposal_changed();
create function public.email_filing_source_changed() returns trigger
language plpgsql set search_path=pg_catalog,public as $$
declare previous public.email_filing_application;
begin
  if public.email_filing_source_hash(old)=public.email_filing_source_hash(new) then return new; end if;
  select * into previous from public.email_filing_application where activity_id=new.id and authority<>'review'
    order by created_at desc,generation desc limit 1;
  if found and previous.status not in ('suppressed','needs_review') and new.deal_id=previous.deal_id
    and coalesce(new.email_context#>>'{lifecycle,state}','live')='live' then
    perform public.enqueue_email_filing(new.id,new.deal_id,previous.selection_key,previous.authority);
  end if;
  return new;
end $$;
create trigger email_filing_source_changed after update of summary,detail,email_context,email_source,email_identity on public.activity
  for each row execute function public.email_filing_source_changed();
insert into public.email_filing_application(activity_id,source_hash,deal_id,selection_key,authority,status)
  select p.activity_id,public.email_filing_source_hash(a),p.deal_id,p.id::text,'review','review'
  from public.email_proposal p join public.activity a on a.id=p.activity_id
  where p.kind in ('file_to_deal','possible_new_deal') and p.status='pending';
insert into public.email_filing_application(activity_id,source_hash,deal_id,selection_key,authority,status)
  select p.activity_id,public.email_filing_source_hash(a),a.deal_id,p.id::text||':'||p.version::text,'confirmed','pending'
  from public.email_proposal p join public.activity a on a.id=p.activity_id
  join public.deal d on d.id=a.deal_id
  where p.kind in ('file_to_deal','possible_new_deal') and p.status='confirmed'
    and a.deal_id=coalesce((p.correction_provenance->>'target_deal')::uuid,p.deal_id)
    and d.phase<>'closed' and d.invoiced_on is null
    and coalesce(a.email_context#>>'{lifecycle,state}','live')='live';
insert into public.email_filing_application(activity_id,source_hash,deal_id,selection_key,authority,status)
  select distinct on (a.id) a.id,public.email_filing_source_hash(a),a.deal_id,e.id::text,'inherited','pending'
  from public.v_email_thread_filing_event e join public.activity a on a.id::text=e.new_value->>'activity_id'
  join public.deal d on d.id=a.deal_id
  where e.verb='inherit-email-thread' and e.subject_id=a.deal_id
    and d.phase<>'closed' and d.invoiced_on is null
    and coalesce(a.email_context#>>'{lifecycle,state}','live')='live'
    and not exists(select 1 from public.email_filing_application g where g.activity_id=a.id and g.authority='confirmed')
    and not exists(select 1 from public.v_email_thread_filing_event u where u.verb='undo-email-thread-filing'
      and u.new_value->>'activity_id'=a.id::text)
  order by a.id,e.mutation_order desc;
grant select on public.email_filing_application to carr_reader;
grant select,insert,update on public.email_filing_application to carr_writer;
grant execute on function public.email_filing_source_hash(public.activity) to carr_reader,carr_writer;
grant execute on function public.enqueue_email_filing(uuid,uuid,text,text) to carr_writer;
do $summary_grants$
declare
  contract jsonb := ops.scac_current_grant_contract();
  privilege text;
begin
  foreach privilege in array array['insert','update'] loop
    contract := jsonb_set(contract,'{relation_dml}',contract->'relation_dml' || jsonb_build_array(jsonb_build_object(
      'ingress_key','db-relation-acl:public.email_filing_application:carr_writer:' || privilege,
      'ingress_kind','db_relation_acl','relation','public.email_filing_application',
      'relation_kind','r','grantee','carr_writer','privilege',privilege,'grantable',false)));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception 'Email filing application grant catalog is invalid';
  end if;
end $summary_grants$;
