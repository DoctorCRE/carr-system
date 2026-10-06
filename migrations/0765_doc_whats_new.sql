-- Doc catchup state belongs to the authenticated partner, never a tool argument.
-- The response envelope and acknowledgement commit in the same writer transaction.
-- A new-verb SCAC successor seal is owed before this ingress can be activated.
create table ops.doc_whats_new_watermark (
  partner_id uuid primary key references public.actor(id),
  high_water timestamptz not null,
  source_snapshot pg_snapshot not null
);

create function ops.whats_new_partner_id() returns uuid
language plpgsql stable security definer set search_path=pg_catalog,ops,public as $$
declare v_actor uuid; v_partner uuid; v_slug text;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  v_slug:=nullif(current_setting('carr.sponsoring_human_slug',true),'');
  select id into v_partner from public.actor
    where slug=v_slug and slug in ('joe','dell') and kind='human' and active;
  if v_partner is null then raise exception 'whats_new_requires_partner_scope'; end if;
  return v_partner;
end $$;

create function ops.whats_new_context(p_lock boolean) returns jsonb
language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare v_partner uuid; v_seen ops.doc_whats_new_watermark%rowtype;
        v_until timestamptz; v_snapshot pg_snapshot;
begin
  v_partner:=ops.whats_new_partner_id();
  if p_lock then perform pg_advisory_xact_lock(hashtextextended('doc-whats-new:'||v_partner::text,0)); end if;
  v_until:=statement_timestamp(); v_snapshot:=pg_current_snapshot();
  select * into v_seen from ops.doc_whats_new_watermark where partner_id=v_partner;
  return jsonb_build_object('ok',true,'since',coalesce(v_seen.high_water,v_until-interval '24 hours'),
    'high_water',v_until,'snapshot',v_snapshot::text,'previous_snapshot',v_seen.source_snapshot::text,
    'first_call',v_seen.partner_id is null);
end $$;

create function ops.mark_whats_new_seen(p_until timestamptz,p_snapshot pg_snapshot) returns jsonb
language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare v_partner uuid;
begin
  v_partner:=ops.whats_new_partner_id();
  if p_until is null or p_until>statement_timestamp() or p_snapshot is null then
    raise exception 'whats_new_acknowledgement_invalid'; end if;
  perform pg_advisory_xact_lock(hashtextextended('doc-whats-new:'||v_partner::text,0));
  insert into ops.doc_whats_new_watermark(partner_id,high_water,source_snapshot)
    values(v_partner,p_until,p_snapshot)
    on conflict(partner_id) do update set high_water=excluded.high_water,source_snapshot=excluded.source_snapshot
      where excluded.high_water>ops.doc_whats_new_watermark.high_water;
  return jsonb_build_object('ok',true);
end $$;

-- The timestamp bounds the answer. The previous snapshot additionally keeps
-- rows from transactions that were still in flight when that answer was made.
create function ops.whats_new_changed(p_at timestamptz,p_xid xid8,p_since timestamptz,
  p_until timestamptz,p_previous pg_snapshot) returns boolean
language sql immutable set search_path=pg_catalog as $$
  select p_at<=p_until and (p_at>p_since or
    (p_previous is not null and not pg_visible_in_snapshot(p_xid,p_previous)))
$$;

create function ops.whats_new_section(p_section text,p_since timestamptz,p_until timestamptz,
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
    select coalesce(jsonb_agg(jsonb_build_object('ref',coalesce(l.registry_ref,'lead:'||l.id),'at',l.created_at,
      'group_ref','lead:'||l.id,'group_name',p.name,'text',p.name||' is a new lead')
      order by l.created_at desc,l.id),'[]'::jsonb) into v_items
    from public.lead l join public.party p on p.id=l.party_id
    where (l.owner_id is null or l.owner_id=v_partner) and p.merged_into is null and p.deleted_at is null
      and ops.whats_new_changed(l.created_at,l.xmin::text::xid8,p_since,p_until,p_previous)
      and (l.created_at>p_since or l.updated_at=l.created_at);
  elsif p_section='partner_activity' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','activity:'||a.id,'at',a.recorded_at,
      'group_ref','deal:'||d.id,'group_name',d.name,'text',act.display_name||': '||a.summary)
      order by a.recorded_at desc,a.id),'[]'::jsonb) into v_items
    from public.activity a join public.deal d on d.id=a.deal_id join public.actor act on act.id=a.actor_id
    where act.kind='human' and act.slug in ('joe','dell') and act.id<>v_partner
      and ops.whats_new_changed(a.recorded_at,a.xmin::text::xid8,p_since,p_until,p_previous);
  elsif p_section='next_actions' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','next-action:'||n.id,'at',
      greatest(n.updated_at,case when greatest(n.due_on,coalesce(n.hold_until,n.due_on))<=(p_until at time zone 'America/Chicago')::date
        then greatest(n.due_on,coalesce(n.hold_until,n.due_on))::timestamp at time zone 'America/Chicago' end),
      'group_ref',n.subject_type||':'||n.subject_id,'group_name',coalesce(d.name,p.name,'Other work'),
      'text',n.description||case when n.due_on is null then '' else ' is due '||n.due_on::text end||
        case when n.hold_until>(p_until at time zone 'America/Chicago')::date then ' and held until '||n.hold_until::text else '' end)
      order by n.updated_at desc,n.id),'[]'::jsonb) into v_items
    from public.next_action n left join public.deal d on n.subject_type='deal' and d.id=n.subject_id
      left join public.lead l on n.subject_type='lead' and l.id=n.subject_id
      left join public.client cl on n.subject_type='client' and cl.id=n.subject_id
      left join public.party p on p.id=coalesce(l.party_id,cl.party_id)
    where n.status='open' and n.owner_id=v_partner and (
      ops.whats_new_changed(n.updated_at,n.xmin::text::xid8,p_since,p_until,p_previous) or
      (greatest(n.due_on,coalesce(n.hold_until,n.due_on))::timestamp at time zone 'America/Chicago'>p_since and
       greatest(n.due_on,coalesce(n.hold_until,n.due_on))::timestamp at time zone 'America/Chicago'<=p_until));
  elsif p_section='critical_dates' then
    select coalesce(jsonb_agg(jsonb_build_object('ref','critical-date:'||cd.id,
      'at',greatest(cd.updated_at,case when cd.due_on-14<=(p_until at time zone 'America/Chicago')::date
        then (cd.due_on-14)::timestamp at time zone 'America/Chicago' end),
      'group_ref','deal:'||d.id,'group_name',d.name,
      'text',d.name||' has '||replace(cd.kind,'_',' ')||' due '||cd.due_on::text)
      order by cd.updated_at desc,cd.id),'[]'::jsonb) into v_items
    from public.critical_date cd join public.deal d on d.id=cd.deal_id
    where cd.status='open' and (ops.whats_new_changed(cd.updated_at,cd.xmin::text::xid8,p_since,p_until,p_previous) or
      ((cd.due_on-14)::timestamp at time zone 'America/Chicago'>p_since and
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

revoke all on table ops.doc_whats_new_watermark from public,carr_reader,carr_writer,carr_authority;
revoke all on function ops.whats_new_partner_id(),ops.whats_new_context(boolean),
  ops.mark_whats_new_seen(timestamptz,pg_snapshot),
  ops.whats_new_changed(timestamptz,xid8,timestamptz,timestamptz,pg_snapshot),
  ops.whats_new_section(text,timestamptz,timestamptz,pg_snapshot) from public;
grant execute on function ops.whats_new_context(boolean),ops.mark_whats_new_seen(timestamptz,pg_snapshot),
  ops.whats_new_section(text,timestamptz,timestamptz,pg_snapshot) to carr_writer,carr_authority;
