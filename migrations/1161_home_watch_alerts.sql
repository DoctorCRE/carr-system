-- rollback: forward-only; restore the prior projection body in a new migration.
-- expand-contract: expand; lease notifications join the existing bounded Home read.
-- lock-review: replace one SQL function; no table rewrite or row mutation.
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
  ), drafts as materialized (
    select p.* from public.email_proposal p
      join public.activity e on e.id=p.activity_id
      join public.actor a on a.id=p_actor and a.slug=p.owner
      where coalesce(e.email_context#>>'{lifecycle,state}','live')='live'
        and p.kind='reply_draft' and e.email_source->>'mailbox_owner'=a.slug
        and p.status<>'dismissed' and (p.status<>'sent' or p.result_recorded_at>=now()-interval '7 days')
      order by (p.status<>'sent') desc,coalesce(p.result_recorded_at,p.submitted_at,p.created_at) desc,p.id limit 1001
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
    select 'email_proposal:'||id,'email_proposal',id::text,version::text,md5(coalesce(draft_subject,'')),
      case when status='failed' then 'failed' when status='sent' then 'done'
        when status='approved_to_send' then 'working' else 'needs_attention' end,
      coalesce(draft_subject,'Reply draft'),coalesce(result_recorded_at,submitted_at,created_at),null,false,
      coalesce(draft_subject,'Reply draft'),null,null,'Reply draft',jsonb_build_array(id::text),null,null,null from drafts
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
