-- rollback: forward-only; restore the prior function bodies in a new migration.
-- expand-contract: expand; preserve the Home row shape, command interface and canonical proposal records.
-- lock-review: replace two functions; no table rewrite or row mutation.
set local lock_timeout='5s';
set local statement_timeout='30s';

create or replace function ops.home_inbox_sources_v2(p_actor uuid)
returns table(item_id text,source_type text,source_id text,source_version text,material_version text,
  source_state text,title text,activity_at timestamptz,hold_until date,unseen boolean,
  context_label text,due_on date,open_url text,kind_label text,merged_source_ids jsonb,action_id uuid,action_version integer,deal_id uuid)
language sql stable set search_path=pg_catalog,pg_temp as $$
  with task_groups as (
    select t.*,
      first_value(id) over w representative,
      max(updated_at) over w group_activity_at,
      jsonb_agg(id::text) over w merged_ids,
      version::text revision
    from public.next_action t
    where owner_id=p_actor and
      (status='open' or (status='done' and updated_at>=now()-interval '7 days'))
    window w as (partition by owner_id,subject_type,subject_id,
      case when status='open' then lower(regexp_replace(btrim(description),'\s+',' ','g')) else id::text end
      order by created_at,id rows between unbounded preceding and unbounded following)
  ), tasks as materialized (
    select * from task_groups where id=representative
    order by (status='open') desc,group_activity_at desc,id limit 1001
  ), actions as materialized (
    select * from public.capture_post_call_action where owner_id=p_actor
      and (status='open' or (status='done' and completed_at>=now()-interval '7 days'))
    order by (status='open') desc,updated_at desc,id limit 1001
  ), sessions as materialized (
    select * from public.capture_session where actor_id=p_actor
      and (state<>'done' or state_at>=now()-interval '7 days')
    order by (state<>'done') desc,state_at desc,id limit 1001
  ), draft_groups as (
    select p.*,
      coalesce(nullif(btrim(e.email_context#>>'{sender,name}'),''),
        nullif(btrim(e.email_context#>>'{sender,email}'),''),'Unknown sender') sender_label,
      row_number() over w draft_rank,
      jsonb_agg(p.id::text) over w draft_merged_ids
    from public.email_proposal p
      join public.activity e on e.id=p.activity_id
      join public.actor a on a.id=p_actor and a.slug=p.owner
      where coalesce(e.email_context#>>'{lifecycle,state}','live')='live'
        and p.kind='reply_draft' and e.email_source->>'mailbox_owner'=a.slug
        and p.status<>'dismissed' and (p.status<>'sent' or p.result_recorded_at>=now()-interval '7 days')
      window w as (partition by p.owner,p.status,
        case when p.status='pending' then
          coalesce('conversation:'||nullif(btrim(e.email_context->>'conversation_id'),''),'proposal:'||p.id)
        else 'proposal:'||p.id end
        order by p.created_at desc,p.id desc rows between unbounded preceding and unbounded following)
  ), drafts as materialized (
    select * from draft_groups where draft_rank=1
    order by (status<>'sent') desc,coalesce(result_recorded_at,submitted_at,created_at) desc,id limit 1001
  ), notifications as materialized (
    select n.* from ops.notification n where recipient_actor=p_actor
      and not exists (select 1 from public.activity mail
        where n.subject_type='email' and mail.id::text=n.subject_ref
          and coalesce(mail.email_context#>>'{lifecycle,state}','live')<>'live')
      and ops.notification_visible(n.id)
      order by created_at desc,id desc limit 10001
  ), groups as (
    select subject_type,subject_ref,
      jsonb_agg(to_jsonb(n) order by created_at desc,id desc)->0 latest,
      md5(coalesce(string_agg(id::text,',' order by created_at,id) filter(where severity<>'completion'),'')) material,
      md5(string_agg(id::text,',' order by created_at,id)) revision,
      jsonb_agg(id::text order by created_at desc,id desc) merged_ids,
      bool_or(r.read_at is null) unseen
    from notifications n left join ops.notification_read r on r.notification_id=n.id and r.recipient_actor=p_actor
    group by subject_type,subject_ref
  ), raw_sources as (
    select 'next_action:'||t.id item_id,'next_action'::text source_type,t.id::text source_id,t.revision source_version,
      md5(jsonb_build_array(t.description,t.hold_until)::text) material_version,
      case when t.status='done' then 'done' else 'needs_attention' end source_state,
      t.description title,t.group_activity_at activity_at,t.hold_until,false unseen,
      d.name context_label,t.due_on,'/deals?deal='||d.id||'&focus=next-step' open_url,
      'Task'::text kind_label,t.merged_ids merged_source_ids,t.id action_id,t.version action_version,
      case when t.subject_type='deal' then t.subject_id end deal_id from tasks t left join public.deal d on t.subject_type='deal' and d.id=t.subject_id
    union all
    select 'capture_post_call_action:'||a.id,'capture_post_call_action',a.id::text,
      md5(jsonb_build_array(a.status,a.description,a.updated_at,a.completed_at)::text),md5(a.description),
      case when a.status='done' then 'done' else 'needs_attention' end,
      a.description,a.updated_at,null,false,d.name,a.due_on,'/deals?deal='||d.id||'&focus=next-step',
      'Call follow-up',jsonb_build_array(a.id::text),a.id,a.version,a.deal_id from actions a left join public.deal d on d.id=a.deal_id
    union all
    select 'capture_session:'||id,'capture_session',id::text,md5(jsonb_build_array(state,state_at)::text),
      md5(jsonb_build_array(started_at)::text),
      case when state='failed' then 'failed' when state='done' then 'done' else 'working' end,
      'Call processing '||to_char(started_at at time zone 'America/Chicago','Mon DD HH24:MI'),state_at,null,false,
      'Call '||to_char(started_at at time zone 'America/Chicago','Mon DD HH24:MI'),null,
      null,'Call',jsonb_build_array(id::text),null,null,null from sessions
    union all
    select 'email_proposal:'||id,'email_proposal',id::text,version::text||':'||md5(draft_merged_ids::text),
      md5(jsonb_build_array(draft_subject,sender_label,draft_merged_ids)::text),
      case when status='failed' then 'failed' when status='sent' then 'done'
        when status='approved_to_send' then 'working' else 'needs_attention' end,
      sender_label||' · '||case status when 'pending' then 'Reply needed'
        when 'failed' then 'Reply failed' when 'sent' then 'Reply sent'
        when 'approved_to_send' then 'Reply sending' else 'Reply draft' end||
        case when jsonb_array_length(draft_merged_ids)>1 then
          ' ('||(jsonb_array_length(draft_merged_ids)-1)||' earlier email'||
          case when jsonb_array_length(draft_merged_ids)>2 then 's' else '' end||')' else '' end,
      coalesce(result_recorded_at,submitted_at,created_at),null,false,
      coalesce(draft_subject,'Reply draft'),null,null,'Reply draft',draft_merged_ids,null,null,null from drafts
  )
  select s.item_id,s.source_type,s.source_id,
    s.source_version||':'||coalesce(g.material,md5('')),
    s.material_version||':'||coalesce(g.material,md5('')),
    case when (g.latest->>'created_at')::timestamptz>=s.activity_at
      and g.latest->>'severity' in ('failure','action_required')
      then case when g.latest->>'severity'='failure' then 'failed' else 'needs_attention' end
      else s.source_state end,
    s.title,greatest(s.activity_at,(g.latest->>'created_at')::timestamptz),s.hold_until,
    coalesce(g.unseen,s.unseen),s.context_label,s.due_on,s.open_url,s.kind_label,s.merged_source_ids,
    s.action_id,s.action_version,s.deal_id
    from raw_sources s left join groups g on g.subject_type=s.source_type and g.subject_ref=s.source_id
  union all
  select 'notification:'||jsonb_build_array(g.subject_type,g.subject_ref)::text,
    'notification',jsonb_build_array(g.subject_type,g.subject_ref)::text,revision,material,
    case latest->>'severity' when 'completion' then 'done' when 'failure' then 'failed' else 'needs_attention' end,
    coalesce(latest->>'reason','Update')||' · '||coalesce(d.name,lease_context.label,l.title,g.subject_type||' '||g.subject_ref),
    (latest->>'created_at')::timestamptz,null,unseen,
    coalesce(d.name,lease_context.label,l.title,g.subject_type||' '||g.subject_ref),null,latest->>'deep_link','Update',
    g.merged_ids,null,null,d.id
    from groups g left join public.deal d on g.subject_type='deal' and d.id::text=g.subject_ref
    left join public.loop_item l on g.subject_type='loop' and l.id=case
      when g.subject_ref ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      then g.subject_ref::uuid end
    left join public.lease lease on g.subject_type='lease' and lease.id::text=g.subject_ref
    left join public.client client on client.id=lease.client_id
    left join public.party party on party.id=client.party_id
    left join lateral (select coalesce(party.name,'Lease')||' · Lease expires '||
      coalesce(lease.expiration_on::text,'unknown') label where lease.id is not null) lease_context on true
    where g.subject_type in ('deal','lease','loop','call','email','doc_conversation_request')
      and (latest->>'severity'<>'completion' or (latest->>'created_at')::timestamptz>=now()-interval '7 days')
      and not exists(select 1 from raw_sources s where s.source_type=g.subject_type and s.source_id=g.subject_ref)
$$;

create or replace function ops.change_home_inbox(p_key uuid,p_command text,p_items jsonb,p_until timestamptz)
returns jsonb language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); request jsonb; prior ops.home_inbox_write%rowtype;
  entry jsonb; current_item jsonb; result jsonb; results jsonb:='[]'; source_material text;
begin
  if a is null then raise insufficient_privilege; end if;
  if p_command in ('settle_reversible','snooze_reversible','settle_undo','snooze_undo') then
    return ops.reversible_home_inbox(p_key,p_command,p_items,p_until);
  end if;
  if p_key is null or p_command not in ('snooze','settle','unsettle') or p_command is null
    or jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items) not between 1 and 100
    or (p_command='snooze' and p_until is null) or (p_command<>'snooze' and p_until is not null) then
    return jsonb_build_object('ok',false,'error','invalid_inbox_command');
  end if;
  request:=jsonb_build_array(p_command,p_items,p_until);
  perform pg_advisory_xact_lock(hashtextextended('home-inbox:'||a::text,0));
  select * into prior from ops.home_inbox_write where actor_id=a and idempotency_key=p_key;
  if found then
    if prior.request<>request then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return prior.result;
  end if;
  if p_command='snooze' and (p_until<=now() or not isfinite(p_until)) then
    return jsonb_build_object('ok',false,'error','invalid_snooze_time');
  end if;
  if exists(select 1 from jsonb_array_elements(p_items) i group by i->>'item_id' having count(*)>1) then
    return jsonb_build_object('ok',false,'error','duplicate_inbox_item');
  end if;
  for entry in select value from jsonb_array_elements(p_items) loop
    select i,s.material_version into current_item,source_material
      from ops.home_inbox_items_v3(a) i
      join ops.home_inbox_sources_v3(a) s on s.item_id=i->>'item_id'
      where i->>'item_id'=entry->>'item_id';
    if current_item is null or current_item->>'source_type'='overflow' then
      result:=jsonb_build_object('ok',false,'error','inbox_item_not_found');
    elsif entry->>'base_version' is distinct from current_item->>'base_version' then
      result:=jsonb_build_object('ok',false,'error','version_conflict','current_version',current_item->>'base_version');
    else
      perform ops.write_home_inbox_disposition(a,current_item,source_material,p_command,p_until);
      select jsonb_build_object('ok',true,'item',i) into result from ops.home_inbox_items_v3(a) i where i->>'item_id'=entry->>'item_id';
    end if;
    results:=results||jsonb_build_array(result||jsonb_build_object('item_id',entry->>'item_id'));
  end loop;
  result:=jsonb_build_object('ok',true,'schema_version','home-inbox-command.v1','results',results);
  insert into ops.home_inbox_write(actor_id,idempotency_key,request,result) values(a,p_key,request,result);
  return result;
end $$;
