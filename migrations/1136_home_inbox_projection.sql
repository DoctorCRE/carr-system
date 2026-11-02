-- rollback: forward-only; restore prior projection bodies in a new migration if needed.
-- expand-contract: expand; install v2 beside unchanged v1 reads and disposition commands.
-- lock-review: additive functions and a new duplicate-disposition table; existing rows stay unchanged.
set local lock_timeout = '5s';
set local statement_timeout = '30s';

create function ops.home_inbox_sources_v2(p_actor uuid)
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
      where p.kind='reply_draft' and e.email_source->>'mailbox_owner'=a.slug
        and p.status<>'dismissed' and (p.status<>'sent' or p.result_recorded_at>=now()-interval '7 days')
      order by (p.status<>'sent') desc,coalesce(p.result_recorded_at,p.submitted_at,p.created_at) desc,p.id limit 1001
  ), notifications as materialized (
    select n.* from ops.notification n where recipient_actor=p_actor
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
    coalesce(latest->>'reason','Update')||' · '||coalesce(d.name,l.title,g.subject_type||' '||g.subject_ref),
    (latest->>'created_at')::timestamptz,null,unseen,
    coalesce(d.name,l.title,g.subject_type||' '||g.subject_ref),null,latest->>'deep_link','Update',
    g.merged_ids,null,null,d.id
    from groups g left join public.deal d on g.subject_type='deal' and d.id::text=g.subject_ref
    left join public.loop_item l on g.subject_type='loop' and l.id=case
      when g.subject_ref ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      then g.subject_ref::uuid end
    where g.subject_type in ('deal','loop','call','email','doc_conversation_request')
      and (latest->>'severity'<>'completion' or (latest->>'created_at')::timestamptz>=now()-interval '7 days')
      and not exists(select 1 from raw_sources s where s.source_type=g.subject_type and s.source_id=g.subject_ref)
$$;
revoke all on function ops.home_inbox_sources_v2(uuid) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create table ops.home_inbox_disposition_carry_v2 (like ops.home_inbox_disposition including all);
revoke all on ops.home_inbox_disposition_carry_v2 from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create or replace function ops.home_inbox_items_v2(p_actor uuid)
returns setof jsonb language sql stable set search_path=pg_catalog,pg_temp as $$
  with observed as (
    select s.*,d.version disposition_version,d.explicit_unsettle,d.snoozed_until,
      coalesce(d.disposition,'inbox') stored_disposition,
      coalesce(d.material_version<>s.material_version or
        (s.source_state in ('needs_attention','failed') and d.source_version<>s.source_version),false) new_activity
    from ops.home_inbox_sources_v2(p_actor) s
    left join lateral (
      select candidates.* from (
        select d.*,0 priority from ops.home_inbox_disposition d where d.actor_id=p_actor and d.item_id=s.item_id
        union all
        select d.*,1 priority from ops.home_inbox_disposition_carry_v2 d where d.actor_id=p_actor and d.item_id=s.item_id
      ) candidates order by priority limit 1
    ) d on true
  ), placed as (
    select *,case when stored_disposition='snoozed' and (snoozed_until<=now() or new_activity)
      then 'inbox' else stored_disposition end disposition from observed
  )
  select jsonb_build_object('item_id',item_id,'source_type',source_type,'source_id',source_id,
    'source_version',source_version,'source_state',source_state,'title',title,'activity_at',activity_at,
    'context_label',context_label,'due_on',due_on,'open_url',open_url,'kind_label',kind_label,
    'action_id',action_id,'action_version',action_version,'deal_id',deal_id,'merged_source_ids',merged_source_ids,'hold_until',hold_until,'unseen',unseen,'disposition',disposition,
    'snoozed_until',case when disposition='snoozed' then snoozed_until end,
    'explicit_unsettle',coalesce(explicit_unsettle,false) and not new_activity,
    'section',case when source_state='done' then 'done' when hold_until>(now() at time zone 'America/Chicago')::date then 'held' when disposition<>'inbox' then disposition when source_state='working' then 'working' else 'inbox' end,
    'base_version',md5(jsonb_build_array(source_version,coalesce(disposition_version,0),disposition)::text))
    from placed order by (source_state='done'),activity_at desc,item_id limit 1001
$$;
revoke all on function ops.home_inbox_items_v2(uuid) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create or replace function ops.read_home_inbox_v2() returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); items jsonb;
begin
  if a is null then raise insufficient_privilege; end if;
  select coalesce(jsonb_agg(i order by (i->>'source_state'='done'),(i->>'activity_at')::timestamptz desc,i->>'item_id'),'[]') into items from ops.home_inbox_items_v2(a) i;
  return jsonb_build_object('ok',true,'schema_version','home-inbox.v2','items',(select coalesce(jsonb_agg(value order by ord),'[]') from jsonb_array_elements(items) with ordinality e(value,ord) where ord<=1000),
    'truncated',jsonb_array_length(items)>1000 or exists (
      select 1 from ops.notification n where n.recipient_actor=a
        and ops.notification_visible(n.id)
        offset 10000 limit 1),'limit',1000,
    'working_collapsed',true,'auto_settle_enabled',false);
end $$;
revoke all on function ops.read_home_inbox_v2() from public,carr_reader;
grant execute on function ops.read_home_inbox_v2() to carr_writer;

-- Keep inherited dispositions private to v2 so the deployed v1 cards retain their identities.
with actors as (select distinct actor_id from ops.home_inbox_disposition),
carry as (
  select a.actor_id,s.item_id,s.source_version,s.material_version,d.old_item_id
  from actors a cross join lateral ops.home_inbox_sources_v2(a.actor_id) s
  cross join lateral (
    select d.item_id old_item_id from ops.home_inbox_disposition d
    join ops.home_inbox_sources(a.actor_id) old on old.item_id=d.item_id
    where d.actor_id=a.actor_id and s.source_type='next_action'
      and s.merged_source_ids ? old.source_id and d.item_id<>s.item_id
      and d.source_version=old.source_version and d.material_version=old.material_version
    order by d.updated_at desc,d.item_id limit 1
  ) d
  where not exists(select 1 from ops.home_inbox_disposition existing
    where existing.actor_id=a.actor_id and existing.item_id=s.item_id)
)
insert into ops.home_inbox_disposition_carry_v2
  (actor_id,item_id,disposition,snoozed_until,source_version,material_version,version,explicit_unsettle,updated_at)
select d.actor_id,c.item_id,d.disposition,d.snoozed_until,c.source_version,c.material_version,d.version,d.explicit_unsettle,d.updated_at
from carry c join ops.home_inbox_disposition d on d.actor_id=c.actor_id and d.item_id=c.old_item_id;

-- Append the new definer without replacing concurrent grant declarations.
do $append_grant$
declare contract jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||
    '[{"grantee":"carr_writer","signature":"ops.read_home_inbox_v2()","grantable":false,"privilege":"execute","ingress_key":"db-function-acl:ops.read_home_inbox_v2():carr_writer:execute","ingress_kind":"db_function_acl","function_kind":"f","security_definer":true,"volatility":"s","parallel":"u","config":["search_path=pg_catalog, pg_temp"]}]'::jsonb);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
