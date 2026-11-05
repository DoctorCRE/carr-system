-- rollback: drop subject_watch event triggers and consumer functions before tables.
-- expand-contract: additive watch consumer; subscription contract 1152 must precede it.
-- lock-review: new tables and short trigger installation; no business table rewrite.
set local lock_timeout='5s';
set local statement_timeout='30s';

alter table ops.notification drop constraint notification_deep_link_check;
alter table ops.notification add constraint notification_deep_link_check check (
  length(deep_link)<=301 and (
    deep_link ~ '^/([A-Za-z0-9._~-][A-Za-z0-9._~/-]*)?$'
    or (subject_type='deal' and deep_link='/deals?deal='||subject_ref
      and subject_ref ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    or (subject_type='lease' and deep_link ~ '^/clients[?]record=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
  )
) not valid;

create function ops.lease_radar_window() returns table(starts_on date,ends_on date)
language sql stable set search_path=pg_catalog,pg_temp as $$
  select today,(today+interval '24 months')::date
  from (select (now() at time zone 'America/Chicago')::date today) clock
$$;
revoke all on function ops.lease_radar_window() from public;
grant execute on function ops.lease_radar_window() to carr_reader,dot_reader;

create table ops.subject_watch_scan_cursor (
  singleton boolean primary key default true check(singleton),watch_id uuid
);
insert into ops.subject_watch_scan_cursor(singleton) values(true);
create table ops.subject_watch_lease_baseline (
  watch_id uuid primary key references ops.subject_watch(id) on delete cascade,
  expiration_on date,
  version integer not null
);
create table ops.subject_watch_event_receipt (
  event_id uuid primary key,
  consumed_at timestamptz not null default clock_timestamp()
);
create index subject_watch_lease_scan on ops.subject_watch(id) where subject_type='lease';
create index subject_watch_source on ops.subject_watch(subject_type,subject_id);
revoke all on ops.subject_watch_scan_cursor,ops.subject_watch_lease_baseline,
  ops.subject_watch_event_receipt from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.subject_watch_lease_baseline_insert() returns trigger
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
begin
  if new.subject_type='lease' then
    insert into ops.subject_watch_lease_baseline(watch_id,expiration_on,version)
    select new.id,l.expiration_on,l.version from public.lease l cross join ops.lease_radar_window() w
    where l.id=new.subject_id and l.expiration_on between w.starts_on and w.ends_on;
  end if;
  return new;
end $$;
create trigger subject_watch_lease_baseline after insert on ops.subject_watch
for each row execute function ops.subject_watch_lease_baseline_insert();
insert into ops.subject_watch_lease_baseline(watch_id,expiration_on,version)
select s.id,l.expiration_on,l.version from ops.subject_watch s join public.lease l on l.id=s.subject_id
cross join ops.lease_radar_window() w where s.subject_type='lease'
and l.expiration_on between w.starts_on and w.ends_on;

create function ops.scan_subject_watch_leases(p_event uuid,p_limit integer default 50)
returns integer language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare cursor_id uuid; last_id uuid; s record; l record; scanned integer:=0;
begin
  if p_limit is null or p_limit<1 or p_limit>50 then raise exception 'watch_batch_limit'; end if;
  if not exists(select 1 from public.event where id=p_event) then raise exception 'watch_event_required'; end if;
  select watch_id into cursor_id from ops.subject_watch_scan_cursor where singleton for update;
  for s in select sw.*,a.slug from ops.subject_watch sw join public.actor a on a.id=sw.actor_id
    where sw.subject_type='lease' and (cursor_id is null or sw.id>cursor_id)
    order by sw.id limit p_limit for update of sw
  loop
    scanned:=scanned+1;last_id:=s.id;
    select lease.* into l from public.lease lease
    join public.client c on c.id=lease.client_id and c.merged_into is null
    join public.party p on p.id=c.party_id and p.deleted_at is null and p.merged_into is null
    cross join ops.lease_radar_window() w
    where lease.id=s.subject_id and lease.status<>'superseded'
      and lease.expiration_on between w.starts_on and w.ends_on;
    if not found then continue; end if;
    if exists(select 1 from ops.subject_watch_lease_baseline b where b.watch_id=s.id
      and b.expiration_on=l.expiration_on and b.version=l.version) then continue; end if;
    perform ops.mint_notification('event',p_event,'lease',l.id::text,
      'Watched lease needs renewal review','action_required','/clients?record='||l.client_id::text,
      'subject-watch:'||s.id||':lease-window:'||l.version||':'||l.expiration_on,s.slug,false,false);
  end loop;
  update ops.subject_watch_scan_cursor set watch_id=case when scanned<p_limit then null else last_id end where singleton;
  return scanned;
end $$;

create function ops.consume_subject_watch_event(p_event uuid) returns void
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare e public.event%rowtype; s record; d record; reply public.activity%rowtype;
  source_key text; reason text;
begin
  select * into e from public.event where id=p_event;
  if not found then raise exception 'watch_event_required'; end if;
  if coalesce(e.organization_tenant_id,'carr-internal')<>'carr-internal'
    or coalesce(e.personal_scope,'none')<>'none' then return; end if;
  insert into ops.subject_watch_event_receipt(event_id) values(e.id) on conflict do nothing;
  if not found then return; end if;
  if e.subject_type='deal' then
    select deal.* into d from public.deal deal
    join public.v_deal_room_deal visible on visible.id=deal.id
    join public.client c on c.id=deal.client_id and c.merged_into is null
    join public.party p on p.id=c.party_id and p.deleted_at is null and p.merged_into is null
    where deal.id=e.subject_id;
    if found then
      if e.verb in ('confirm-email-filing','inherit-email-thread') and e.new_value->>'activity_id' is not null then
        select * into reply from public.activity where id=(e.new_value->>'activity_id')::uuid
          and deal_id=e.subject_id and kind='email_in'
          and coalesce(email_context#>>'{lifecycle,state}','live')='live';
        if found then source_key:='reply:'||reply.id;reason:='Reply received on a watched deal'; end if;
      elsif d.version>1 then
        source_key:='deal-version:'||d.version;reason:='Watched deal changed';
      end if;
      if source_key is not null then
        for s in select sw.*,a.slug from ops.subject_watch sw join public.actor a on a.id=sw.actor_id
          where sw.subject_type='deal' and sw.subject_id=d.id and sw.subscribed_at<=e.recorded_at
          and (reply.id is not null or d.version>(sw.baseline->>'version')::integer)
          and (reply.id is null or reply.email_source->>'mailbox_owner'=a.slug)
          order by sw.id limit 2 for update of sw
        loop
          perform ops.mint_notification('event',e.id,'deal',d.id::text,reason,'action_required',
            '/deals?deal='||d.id::text,'subject-watch:'||s.id||':'||source_key,s.slug,false,false);
        end loop;
      end if;
    end if;
  end if;
  perform ops.scan_subject_watch_leases(e.id,50);
end $$;

create function ops.subject_watch_on_event() returns trigger
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
begin
  perform ops.consume_subject_watch_event(new.id);
  return new;
end $$;
create trigger subject_watch_on_event after insert on public.event
for each row execute function ops.subject_watch_on_event();
revoke all on function ops.subject_watch_lease_baseline_insert(),
  ops.scan_subject_watch_leases(uuid,integer),ops.consume_subject_watch_event(uuid),ops.subject_watch_on_event()
  from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create or replace view public.v_client_lease_radar as
with policy as (select starts_on as today,ends_on from ops.lease_radar_window()),
leases as (
  select l.id, l.client_id, l.deal_id, p.name as client_name,
         c.status as client_status, cs.label as client_status_label,
         p.city, p.state, c.vertical, owner.slug as owner,
         coalesce(c.owner_label, owner.display_name) as owner_label,
         p.contact_state, p.contact_state_until,
         l.expiration_on, l.commencement_on, l.executed_on,
         l.term_months, l.status as lease_status, l.evidence_kind,
         l.options_note, l.evidence_ref, l.version,
         action.id as touch_id, action.due_on as touch_due_on,
         action.description as touch_summary,
         action_owner.slug as touch_owner,
         (c.status='past_client' and p.contact_state in ('active','nurture')
          and action.due_on <= policy.today and action_owner.slug in ('joe','dell')) as touch_eligible,
         notice.due_on as notice_on, notice.note as notice_note
    from public.lease l
    join public.client c on c.id=l.client_id and c.merged_into is null
    join public.party p on p.id=c.party_id and p.merged_into is null and p.deleted_at is null
    left join public.client_status cs on cs.slug=c.status
    left join public.actor owner on owner.id=c.owner_id
    cross join policy
    left join lateral (
      select n.id,n.due_on,n.description,n.owner_id
        from public.next_action n
       where n.status='open'
         and (n.hold_until is null or n.hold_until <= policy.today) and
         ((n.subject_type='client' and n.subject_id=c.id) or
          (n.subject_type='deal' and n.subject_id=l.deal_id))
       order by n.due_on nulls last,n.created_at,n.id limit 1
    ) action on true
    left join public.actor action_owner on action_owner.id=action.owner_id
    left join lateral (
      select cd.due_on,cd.note from public.critical_date cd
       where cd.deal_id=l.deal_id and cd.kind='option_window' and cd.status='open'
       order by cd.due_on,cd.id limit 1
    ) notice on true
   where l.status <> 'superseded'
     and (l.expiration_on is null or l.expiration_on between policy.today and policy.ends_on)
)
select policy.today as starts_on, policy.ends_on,
       coalesce(jsonb_agg(to_jsonb(l) order by l.expiration_on nulls last,l.id)
         filter (where l.id is not null),'[]'::jsonb) as leases
  from policy left join leases l on true
 group by policy.today, policy.ends_on;
