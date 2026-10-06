-- Forward repair: preserve creation evidence independently of mutable lead tuples.
create table ops.doc_whats_new_lead_creation (
  lead_id uuid primary key,
  created_at timestamptz not null,
  creation_xid xid8 not null
);
-- Existing rows predate this feature's first acknowledgement. Future inserts
-- are captured in their own transaction and never overwritten by updates.
insert into ops.doc_whats_new_lead_creation
  select id,created_at,xmin::text::xid8 from public.lead;
create function ops.capture_whats_new_lead_creation() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops as $$
begin
  insert into ops.doc_whats_new_lead_creation(lead_id,created_at,creation_xid)
    values(new.id,new.created_at,pg_current_xact_id());
  return new;
end $$;
revoke all on table ops.doc_whats_new_lead_creation from public,carr_reader,carr_writer,carr_authority;
revoke all on function ops.capture_whats_new_lead_creation() from public,carr_reader,carr_writer,carr_authority;
create trigger doc_whats_new_lead_creation after insert on public.lead
  for each row execute function ops.capture_whats_new_lead_creation();

-- Replace alias spellings in the executable provision contract with PostgreSQL's
-- canonical function identities. These address the same functions in the store.
revoke all on function ops.mark_whats_new_seen(timestamptz,pg_snapshot),
  ops.whats_new_section(text,timestamptz,timestamptz,pg_snapshot) from carr_writer,carr_authority;
grant execute on function ops.mark_whats_new_seen(timestamp with time zone,pg_snapshot),
  ops.whats_new_section(text,timestamp with time zone,timestamp with time zone,pg_snapshot) to carr_writer,carr_authority;

create or replace function ops.whats_new_section(p_section text,p_since timestamptz,p_until timestamptz,
  p_previous pg_snapshot) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,ops,public as $$
declare v_partner uuid; v_items jsonb; v_unknown boolean:=false;
begin
  v_partner:=ops.whats_new_partner_id();
  if p_section='deal_changes' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','event:'||e.id,'at',e.recorded_at,
      'group_ref','deal:'||d.id,'group_name',d.name,
      'text',d.name||' changed '||replace(coalesce(e.field,e.verb),'_',' ')||
        case when e.field in ('phase','outcome','operating_state') and jsonb_typeof(e.new_value)='string'
          then ' to '||(e.new_value#>>'{}') else '' end)
      order by e.recorded_at desc,e.id),'[]'::jsonb) into v_items
    from public.event e join public.deal d on e.subject_type='deal' and e.subject_id=d.id
    where ops.whats_new_changed(e.recorded_at,e.xmin::text::xid8,p_since,p_until,p_previous);
  elsif p_section='lead_changes' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','event:'||e.id,'at',e.recorded_at,
      'group_ref','lead:'||l.id,'group_name',p.name,
      'text',p.name||' changed '||replace(coalesce(e.field,e.verb),'_',' '))
      order by e.recorded_at desc,e.id),'[]'::jsonb) into v_items
    from public.event e join public.lead l on e.subject_type='lead' and e.subject_id=l.id
      join public.party p on p.id=l.party_id
    where (l.owner_id is null or l.owner_id=v_partner) and p.merged_into is null and p.deleted_at is null
      and ops.whats_new_changed(e.recorded_at,e.xmin::text::xid8,p_since,p_until,p_previous);
  elsif p_section='new_leads' then
    select coalesce(jsonb_agg(jsonb_build_object('ref',coalesce(l.registry_ref,'lead:'||l.id),'at',lc.created_at,
      'group_ref','lead:'||l.id,'group_name',p.name,'text',p.name||' is a new lead')
      order by lc.created_at desc,l.id),'[]'::jsonb) into v_items
    from public.lead l join public.party p on p.id=l.party_id
      join ops.doc_whats_new_lead_creation lc on lc.lead_id=l.id
    where (l.owner_id is null or l.owner_id=v_partner) and p.merged_into is null and p.deleted_at is null
      and ops.whats_new_changed(lc.created_at,lc.creation_xid,p_since,p_until,p_previous);
  elsif p_section='partner_activity' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','activity:'||a.id,'at',a.recorded_at,
      'group_ref',case when a.deal_id is not null then 'deal:'||a.deal_id
        when a.lead_id is not null then 'lead:'||a.lead_id
        when a.client_id is not null then 'client:'||a.client_id
        when a.vendor_id is not null then 'vendor:'||a.vendor_id else 'other' end,
      'group_name',coalesce(d.name,p.name,'Other activity'),'text',act.display_name||': '||a.summary)
      order by a.recorded_at desc,a.id),'[]'::jsonb) into v_items
    from public.activity a join public.actor act on act.id=a.actor_id
      left join public.deal d on d.id=a.deal_id
      left join public.lead l on l.id=a.lead_id
      left join public.client cl on cl.id=a.client_id
      left join public.vendor v on v.id=a.vendor_id
      left join public.party p on p.id=coalesce(l.party_id,cl.party_id,v.party_id)
    where act.kind='human' and act.slug in ('joe','dell') and act.id<>v_partner
      and ops.whats_new_changed(a.recorded_at,a.xmin::text::xid8,p_since,p_until,p_previous);
  elsif p_section='next_actions' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','next-action:'||n.id,'at',
      greatest(n.updated_at,case when n.status='open' and greatest(n.due_on,coalesce(n.hold_until,n.due_on))<=(p_until at time zone 'America/Chicago')::date
        then greatest(n.due_on,coalesce(n.hold_until,n.due_on))::timestamp at time zone 'America/Chicago' end),
      'group_ref',n.subject_type||':'||n.subject_id,'group_name',coalesce(d.name,p.name,'Other work'),
      'text',n.description||case when n.status='done' then ' was completed'
        when n.status='dropped' then ' was dropped'
        else case when n.due_on is null then '' else ' is due '||n.due_on::text end||
          case when n.hold_until>(p_until at time zone 'America/Chicago')::date then ' and held until '||n.hold_until::text else '' end end)
      order by n.updated_at desc,n.id),'[]'::jsonb) into v_items
    from public.next_action n left join public.deal d on n.subject_type='deal' and d.id=n.subject_id
      left join public.lead l on n.subject_type='lead' and l.id=n.subject_id
      left join public.client cl on n.subject_type='client' and cl.id=n.subject_id
      left join public.party p on p.id=coalesce(l.party_id,cl.party_id)
    where n.owner_id=v_partner and (
      ops.whats_new_changed(n.updated_at,n.xmin::text::xid8,p_since,p_until,p_previous) or
      (n.status='open' and greatest(n.due_on,coalesce(n.hold_until,n.due_on))::timestamp at time zone 'America/Chicago'>p_since and
       greatest(n.due_on,coalesce(n.hold_until,n.due_on))::timestamp at time zone 'America/Chicago'<=p_until));
  elsif p_section='critical_dates' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','critical-date:'||cd.id,
      'at',greatest(cd.updated_at,case when cd.status='open' and cd.due_on-14<=(p_until at time zone 'America/Chicago')::date
        then (cd.due_on-14)::timestamp at time zone 'America/Chicago' end),
      'group_ref','deal:'||d.id,'group_name',d.name,
      'text',d.name||' has '||replace(cd.kind,'_',' ')||case when cd.status='open' then ' due '||cd.due_on::text
        else ' marked '||cd.status end)
      order by cd.updated_at desc,cd.id),'[]'::jsonb) into v_items
    from public.critical_date cd join public.deal d on d.id=cd.deal_id
    where (ops.whats_new_changed(cd.updated_at,cd.xmin::text::xid8,p_since,p_until,p_previous) or
      (cd.status='open' and (cd.due_on-14)::timestamp at time zone 'America/Chicago'>p_since and
       (cd.due_on-14)::timestamp at time zone 'America/Chicago'<=p_until));
  elsif p_section='shipped_releases' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','release:'||r.release_key,
      'at',coalesce(r.ended_at,r.updated_at),'group_ref','system','group_name','System work',
      'text','System work shipped') order by coalesce(r.ended_at,r.updated_at) desc,r.release_key),'[]'::jsonb) into v_items
    from ops.release r where r.environment='production' and r.state='complete'
      and ops.whats_new_changed(coalesce(r.ended_at,r.updated_at),r.xmin::text::xid8,p_since,p_until,p_previous);
  elsif p_section='doc_suggestions' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','doc-suggestion:'||s.id,'at',
      greatest(s.suggested_at,case when s.disposition='snoozed' and s.snoozed_until<=(p_until at time zone 'America/Chicago')::date
        then s.snoozed_until::timestamp at time zone 'America/Chicago' end),
      'group_ref','doc-conversation:'||c.id,'group_name',c.title,'text',s.polished_text)
      order by s.suggested_at desc,s.id),'[]'::jsonb) into v_items
    from ops.doc_suggestion s join ops.doc_conversation c on c.id=s.conversation_id
    where ops.doc_suggestion_visible(c.id,v_partner) and (s.disposition in ('open','discuss')
      or (s.disposition='dismissed' and s.dismissed_material_version is distinct from s.material_version)
      or (s.disposition='snoozed' and (s.snoozed_material_version is distinct from s.material_version
        or s.snoozed_until<=(p_until at time zone 'America/Chicago')::date)))
      and (ops.whats_new_changed(s.suggested_at,s.xmin::text::xid8,p_since,p_until,p_previous)
        or (s.disposition='snoozed' and s.snoozed_until::timestamp at time zone 'America/Chicago'>p_since
          and s.snoozed_until::timestamp at time zone 'America/Chicago'<=p_until));
    select exists(select 1 from ops.doc_conversation c where ops.doc_suggestion_visible(c.id,v_partner)
      and coalesce((select max(t.sequence) from ops.doc_conversation_turn t where t.conversation_id=c.id),-1)>
          coalesce((select max(s.through_sequence) from ops.doc_suggestion_scan s where s.conversation_id=c.id),-2)) into v_unknown;
  else raise exception 'whats_new_section_invalid';
  end if;
  return jsonb_build_object('state',case when v_unknown then 'unavailable'
    when jsonb_array_length(v_items)>0 then 'ready' else 'empty' end,'items',v_items)||
    case when v_unknown then jsonb_build_object('reason','suggestion_coverage_unknown') else '{}'::jsonb end;
end $$;
