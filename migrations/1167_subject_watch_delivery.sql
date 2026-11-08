-- rollback: remove cron before removing sweep functions and delivery state.
-- expand-contract: depends on 1166 pending receipts; no new MCP write verb.
-- lock-review: small runtime tables and indexed bounded queue reads; no business rewrite.
set local lock_timeout='5s';
set local statement_timeout='30s';

alter table ops.subject_watch_change drop constraint subject_watch_change_category_check;
alter table ops.subject_watch_change add constraint subject_watch_change_category_check
 check(category in ('material_changed','reply_arrived','lease_threshold_crossed'));

-- Adopt notifications already emitted by the pre-scheduler v1 reply path.
update ops.subject_watch_change c set disposition='emitted',notification_id=n.id
from ops.notification n where c.disposition='pending' and n.recipient_actor=c.actor_id
 and n.dedupe_key='subject-watch:'||c.watch_generation_id||':'||c.semantic_key;

create table ops.subject_watch_delivery (
 watch_id uuid primary key,
 next_evaluation_at timestamptz not null,
 clock_seen jsonb not null default '[]',
 last_emitted_at timestamptz,
 last_success_at timestamptz,
 failure_count integer not null default 0 check(failure_count between 0 and 2),
 failure_code text
);
create index subject_watch_delivery_due on ops.subject_watch_delivery(next_evaluation_at,watch_id) where failure_count<2;
create table ops.subject_watch_sweep (
 singleton boolean primary key default true check(singleton),
 scheduled_at timestamptz not null, completed_at timestamptz not null,
 event_id uuid not null, scanned integer not null, emitted integer not null
);
revoke all on ops.subject_watch_delivery,ops.subject_watch_sweep from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.subject_watch_clock_keys(p_kind text,p_id uuid,p_policy jsonb,p_now timestamptz)
returns jsonb language sql stable set search_path=pg_catalog,pg_temp as $$
 select coalesce(jsonb_agg(key order by urgency,key),'[]') from (
 select l.id||':'||d.field||':'||d.on_date||':'||n.unit||':'||n.offset_value key,
   case n.unit when 'day' then d.on_date-n.offset_value else (d.on_date-make_interval(months=>n.offset_value))::date end urgency
 from public.lease l
 cross join lateral (values ('expiration_on',l.expiration_on),('notice_on',
   (select min(due_on) from public.critical_date where deal_id=l.deal_id and kind='option_window' and status='open'))) d(field,on_date)
 cross join lateral (
   select 'day' unit,value::integer offset_value from jsonb_array_elements_text(p_policy->'lease_threshold_days')
   union all select 'month',value::integer from jsonb_array_elements_text(p_policy->'lease_threshold_months')
 ) n
 where ((p_kind='lease' and l.id=p_id) or (p_kind='deal' and l.deal_id=p_id))
 and l.status<>'superseded' and d.on_date is not null
 and p_policy->'change_kinds' ? 'lease_threshold_crossed'
 and d.on_date <= case n.unit when 'day' then (p_now at time zone 'America/Chicago')::date+n.offset_value
   else ((p_now at time zone 'America/Chicago')::date+make_interval(months=>n.offset_value))::date end
 ) thresholds
$$;

create function ops.subject_watch_delivery_start() returns trigger
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare pol jsonb:=coalesce(to_jsonb(new)->'policy','{"change_kinds":["lease_threshold_crossed"],"lease_threshold_days":[90,30,7,0],"lease_threshold_months":[24,12,6]}');
begin
 if tg_op='DELETE' then
   update ops.subject_watch_change set disposition='suppressed' where watch_generation_id=old.id and disposition='pending';
   delete from ops.subject_watch_delivery where watch_id=old.id; return old;
 end if;
 if tg_op='UPDATE' then
   if old.id<>new.id or new.state<>'watching' or old.state='paused' then
     update ops.subject_watch_change set disposition='suppressed' where watch_generation_id=old.id and disposition='pending';
   end if;
   if old.id<>new.id then delete from ops.subject_watch_delivery where watch_id=old.id; end if;
 end if;
 insert into ops.subject_watch_delivery(watch_id,next_evaluation_at,clock_seen)
 values(new.id,new.subscribed_at,ops.subject_watch_clock_keys(new.subject_type,new.subject_id,pol,new.subscribed_at))
 on conflict(watch_id) do nothing;
 if tg_op='UPDATE' then
 if old.state='paused' and new.state='watching' or old.policy<>new.policy then
   update ops.subject_watch_delivery set clock_seen=ops.subject_watch_clock_keys(new.subject_type,new.subject_id,pol,clock_timestamp()),
     next_evaluation_at=clock_timestamp(),failure_count=0,failure_code=null where watch_id=new.id;
 end if;
 end if;
 return new;
end $$;
create trigger subject_watch_delivery_start after insert or delete on ops.subject_watch
 for each row execute function ops.subject_watch_delivery_start();
create trigger subject_watch_v2_delivery_start after insert or update on ops.subject_watch_v2_subscription
 for each row execute function ops.subject_watch_delivery_start();
insert into ops.subject_watch_delivery(watch_id,next_evaluation_at,clock_seen)
 select id,clock_timestamp(),ops.subject_watch_clock_keys(subject_type,subject_id,
 '{"change_kinds":["lease_threshold_crossed"],"lease_threshold_days":[90,30,7,0],"lease_threshold_months":[24,12,6]}',clock_timestamp()) from ops.subject_watch
 union all select id,clock_timestamp(),ops.subject_watch_clock_keys(subject_type,subject_id,policy,clock_timestamp())
 from ops.subject_watch_v2_subscription where state<>'removed';

create function ops.sweep_subject_watches(p_now timestamptz,p_limit integer default 50)
returns jsonb language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare w record; st ops.subject_watch_delivery; pol jsonb; keys jsonb; fresh jsonb; ids uuid[];
 event_id uuid; minted jsonb; nid uuid; n integer:=0; emits integer:=0; slug_value text; target jsonb; link text;
begin
 if p_now is null or p_limit is null or p_limit not between 1 and 50 then raise exception 'watch_batch_limit'; end if;
 insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,cause,new_value)
 select p_now,id,'subject-watch-scheduled-sweep','system',gen_random_uuid(),'automation_job','{}'
 from public.actor where slug='joe' returning id into event_id;
 for w in
   select s.* from (
     select id,actor_id,subject_type,subject_id,1 policy_version,'watching' state,
       '{"change_kinds":["reply_arrived","lease_threshold_crossed"],"lease_threshold_days":[90,30,7,0],"lease_threshold_months":[24,12,6],"include_self_changes":true}'::jsonb policy from ops.subject_watch
     union all select id,actor_id,subject_type,subject_id,lifecycle_version,state,policy from ops.subject_watch_v2_subscription
   ) s join ops.subject_watch_delivery q on q.watch_id=s.id
   where q.failure_count<2 and (q.next_evaluation_at<=p_now or exists(select 1 from ops.subject_watch_change c
     where c.watch_generation_id=s.id and c.disposition='pending' and c.eligible_at<=p_now and c.failure_count<2))
   order by q.next_evaluation_at,s.id limit p_limit
 loop
   if not pg_try_advisory_xact_lock(hashtextextended(w.actor_id::text||':'||w.subject_type||':'||w.subject_id::text,0)) then continue; end if;
   select * into st from ops.subject_watch_delivery where watch_id=w.id for update skip locked;
   if not found or st.failure_count>=2 then continue; end if;
   n:=n+1;
   -- Refresh lifecycle and policy after acquiring the same lock as unwatch.
   if exists(select 1 from ops.subject_watch_v2_subscription where id=w.id) then
     select state,policy,lifecycle_version into w.state,w.policy,w.policy_version from ops.subject_watch_v2_subscription where id=w.id;
   elsif not exists(select 1 from ops.subject_watch where id=w.id) then w.state:='removed'; end if;
   begin
     select slug into slug_value from public.actor where id=w.actor_id and active and kind='human' and slug in ('joe','dell');
     target:=ops.subject_watch_target(w.subject_type,w.subject_id);
     if w.state<>'watching' or slug_value is null or target is null or
       (w.subject_type='lease' and exists(select 1 from public.lease where id=w.subject_id and status='superseded')) then
       update ops.subject_watch_change set disposition='suppressed' where watch_generation_id=w.id and disposition='pending';
       if w.state='watching' and (slug_value is null or target is null) then
         update ops.subject_watch_v2_subscription set state='paused',lifecycle_version=lifecycle_version+1 where id=w.id and state='watching';
       end if;
     else
       pol:=w.policy;
       keys:=ops.subject_watch_clock_keys(w.subject_type,w.subject_id,pol,p_now);
       select coalesce(jsonb_agg(value order by ordinality),'[]') into fresh from jsonb_array_elements(keys) with ordinality where not st.clock_seen @> jsonb_build_array(value);
       if jsonb_array_length(fresh)>0 then
         -- The last crossing is the most urgent; cover skipped windows quietly.
         insert into ops.subject_watch_change(watch_generation_id,actor_id,subject_type,subject_id,semantic_key,source_event_id,
           source_revision,policy_version,category,changes,evidence_refs,occurred_at,eligible_at)
         values(w.id,w.actor_id,w.subject_type,w.subject_id,'clock:'||(fresh->>-1),event_id,(target->>'version')::integer,
           w.policy_version,'lease_threshold_crossed',jsonb_build_array(jsonb_build_object('field','lease_threshold','before',false,'after',jsonb_build_object('threshold',fresh->>-1,'crossed_count',jsonb_array_length(fresh)),'reason_code','value_changed')),
           jsonb_build_array(jsonb_build_object('event_id',event_id,'activity_id',null)),p_now,p_now)
         on conflict(watch_generation_id,semantic_key) do nothing;
       end if;
       update ops.subject_watch_delivery set clock_seen=keys where watch_id=w.id;
       update ops.subject_watch_change c set disposition='suppressed'
       where c.watch_generation_id=w.id and c.disposition='pending' and (
         c.category='lease_threshold_crossed' and (not pol->'change_kinds' ? 'lease_threshold_crossed'
           or not keys ? substring(c.semantic_key from 7))
         or c.category='reply_arrived' and (not pol->'change_kinds' ? 'reply_arrived' or not exists(
           select 1 from public.activity a where a.id=(c.evidence_refs->0->>'activity_id')::uuid
             and a.email_source->>'mailbox_owner'=slug_value and coalesce(a.email_context#>>'{lifecycle,state}','live')='live'))
         or coalesce((pol->>'include_self_changes')::boolean,true)=false and c.category<>'lease_threshold_crossed' and exists(
           select 1 from public.event e where e.id=c.source_event_id and (e.actor_id=w.actor_id or e.sponsoring_human_slug=slug_value)));
       begin
       if st.last_emitted_at is null or st.last_emitted_at<=p_now-interval '5 minutes' then
         select array_agg(id order by id) into ids from (
           select id from ops.subject_watch_change where watch_generation_id=w.id and disposition='pending'
             and eligible_at<=p_now and failure_count<2 order by id limit 100 for update skip locked) pending;
         if cardinality(ids)>0 then
           if w.subject_type='deal' then link:='/deals?deal='||w.subject_id;
           else select '/clients?record='||client_id into link from public.lease where id=w.subject_id; end if;
           minted:=ops.mint_notification('event',event_id,w.subject_type,w.subject_id::text,
             case when cardinality(ids)=1 and exists(select 1 from ops.subject_watch_change where id=ids[1] and category='lease_threshold_crossed')
               then 'A watched lease reached a reminder date'
               when cardinality(ids)=1 and exists(select 1 from ops.subject_watch_change where id=ids[1] and category='reply_arrived')
               then 'Reply received on a watched deal'
               else 'Watched '||w.subject_type||' has '||cardinality(ids)||' updates to review' end,'action_required',link,
             'subject-watch:'||w.id||':pending:'||ids[1],slug_value,false,false);
           nid:=(minted->>'notification_id')::uuid;
           if nid is null then raise exception 'watch_notification_not_minted'; end if;
           update ops.subject_watch_change set disposition='emitted',notification_id=nid where id=any(ids);
           update ops.subject_watch_delivery set last_emitted_at=p_now where watch_id=w.id;
           emits:=emits+1;
         end if;
       end if;
       exception when others then
         update ops.subject_watch_delivery set failure_count=least(2,failure_count+1),failure_code=sqlstate,
           next_evaluation_at=p_now+interval '5 minutes' where watch_id=w.id;
         continue;
       end;
     end if;
     update ops.subject_watch_delivery set last_success_at=p_now,failure_count=0,failure_code=null,
       next_evaluation_at=((p_now at time zone 'America/Chicago')::date+1)::timestamp at time zone 'America/Chicago' where watch_id=w.id;
   exception when others then
     update ops.subject_watch_delivery set failure_count=least(2,failure_count+1),failure_code=sqlstate,
       next_evaluation_at=p_now+interval '5 minutes' where watch_id=w.id;
   end;
 end loop;
 insert into ops.subject_watch_sweep(singleton,scheduled_at,completed_at,event_id,scanned,emitted)
 values(true,p_now,clock_timestamp(),event_id,n,emits) on conflict(singleton) do update set
 scheduled_at=excluded.scheduled_at,completed_at=excluded.completed_at,event_id=excluded.event_id,scanned=excluded.scanned,emitted=excluded.emitted;
 return jsonb_build_object('scanned',n,'emitted',emits,'event_id',event_id);
end $$;
revoke all on function ops.subject_watch_clock_keys(text,uuid,jsonb,timestamptz),ops.subject_watch_delivery_start(),ops.sweep_subject_watches(timestamptz,integer)
 from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.sweep_subject_watches(timestamptz,integer) to carr_writer;

create or replace function ops.consume_subject_watch_event(p_event uuid) returns void
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
begin
 insert into ops.subject_watch_event_receipt(event_id) values(p_event) on conflict do nothing;
 if found then perform ops.capture_subject_watch_changes(p_event); end if;
end $$;
create or replace function ops.set_subject_watch(p_kind text,p_id uuid,p_watching boolean) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid; baseline jsonb; w jsonb; changed integer;
begin
  a:=ops.subject_watch_actor();
  perform pg_advisory_xact_lock(hashtextextended(a::text||':'||p_kind||':'||p_id::text,0));
  if p_watching then
    baseline:=ops.subject_watch_target(p_kind,p_id);
    if baseline is null then return jsonb_build_object('ok',false,'error','watch_target_not_found'); end if;
    insert into ops.subject_watch(actor_id,subject_type,subject_id,baseline)
      values(a,p_kind,p_id,baseline) on conflict(actor_id,subject_type,subject_id) do nothing;
    get diagnostics changed=row_count;
    select to_jsonb(s) into w from ops.subject_watch s where actor_id=a and subject_type=p_kind and subject_id=p_id;
  else
    delete from ops.subject_watch where actor_id=a and subject_type=p_kind and subject_id=p_id;
    get diagnostics changed=row_count;
  end if;
  return jsonb_build_object('ok',true,'schema_version','subject-watch.v1','watch',w,'rows_changed',changed);
end $$;

do $append_grant$
declare contract jsonb; entries jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  select jsonb_agg(jsonb_build_object(
    'grantee',grantee,'signature','ops.sweep_subject_watches(p_now timestamp with time zone, p_limit integer)','grantable',false,'privilege','execute',
    'ingress_key','db-function-acl:ops.sweep_subject_watches(p_now timestamp with time zone, p_limit integer):'||grantee||':execute',
    'ingress_kind','db_function_acl','function_kind','f','security_definer',true,
    'volatility','v','parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp')))
    into entries from (values('carr_writer')) roles(grantee);
  contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||entries);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;

create index subject_watch_change_delivery_group on ops.subject_watch_change(watch_generation_id,eligible_at,id) where disposition='pending';
create function ops.subject_watch_health(p_watch uuid) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
 select jsonb_build_object('last_success_at',d.last_success_at,'pending_count',
   (select count(*) from (select 1 from ops.subject_watch_change where watch_generation_id=p_watch and disposition='pending' limit 1001) bounded),
   'oldest_pending_at',(select min(eligible_at) from ops.subject_watch_change where watch_generation_id=p_watch and disposition='pending'),
   'status',case when d.failure_count>=2 then 'needs_repair'
     when d.failure_count>0 or not exists(select 1 from ops.subject_watch_sweep where scheduled_at>=clock_timestamp()-interval '15 minutes') then 'delayed' else 'healthy' end)
 from ops.subject_watch_delivery d where watch_id=p_watch
$$;
revoke all on function ops.subject_watch_health(uuid) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
create or replace function ops.subject_watch_v2(p_command text,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare
  a uuid:=ops.subject_watch_actor();
  k text:=p_args->>'subject_type';
  sid uuid:=(p_args->>'subject_id')::uuid;
  w ops.subject_watch_v2_subscription;
  b jsonb; pol jsonb; result jsonb; lim integer; changed integer:=0;
begin
  if p_command='list' then
    lim:=coalesce((p_args->>'limit')::integer,50);
    if lim<1 or lim>100 then raise exception 'watch_limit_invalid'; end if;
    select coalesce(jsonb_agg(to_jsonb(page) order by page.id),'[]'::jsonb) into result from (
      select s.* from ops.subject_watch_v2_subscription s
      where s.actor_id=a and s.state<>'removed'
        and (p_args->>'cursor' is null or s.id>(p_args->>'cursor')::uuid)
        and ops.subject_watch_target(s.subject_type,s.subject_id) is not null
      order by s.id limit lim+1
    ) page;
    return jsonb_build_object('ok',true,'schema_version','subject-watch.v2','watches',
      (select coalesce(jsonb_agg(value order by ordinality),'[]') from jsonb_array_elements(result) with ordinality where ordinality<=lim),
      'has_more',jsonb_array_length(result)>lim,'next_cursor',case when jsonb_array_length(result)>lim then result->(lim-1)->>'id' end);
  end if;
  if p_command not in ('watch','read','pause','resume','replace-policy','unwatch') then
    return jsonb_build_object('ok',false,'error','watch_command_invalid');
  end if;
  if p_command<>'read' then
    perform pg_advisory_xact_lock(hashtextextended(a::text||':'||k||':'||sid::text,0));
  end if;
  if p_command<>'unwatch' then
    b:=ops.subject_watch_target(k,sid);
    if b is null then return jsonb_build_object('ok',false,'error','watch_target_not_found'); end if;
  end if;
  select * into w from ops.subject_watch_v2_subscription where actor_id=a and subject_type=k and subject_id=sid;
  if p_command='read' then
    return jsonb_build_object('ok',true,'schema_version','subject-watch.v2','watch',case when w.state<>'removed' then to_jsonb(w)||jsonb_build_object('health',ops.subject_watch_health(w.id)) end);
  end if;
  if p_command in ('watch','replace-policy') then
    pol:=p_args->'policy';
    if jsonb_typeof(pol)<>'object' or jsonb_typeof(pol->'change_kinds')<>'array'
      or coalesce(jsonb_array_length(pol->'change_kinds'),0) not between 1 and 3
      or exists(select 1 from jsonb_array_elements_text(pol->'change_kinds') v where v not in ('reply_arrived','lease_threshold_crossed','listing_status_changed')) then
      return jsonb_build_object('ok',false,'error','watch_policy_invalid');
    end if;
    if exists(select 1 from jsonb_object_keys(pol) key where key not in ('change_kinds','include_self_changes','lease_threshold_days','lease_threshold_months'))
      or (pol ? 'include_self_changes' and jsonb_typeof(pol->'include_self_changes')<>'boolean')
      or (select count(*)<>count(distinct value) from jsonb_array_elements(pol->'change_kinds'))
      or (pol->'change_kinds' ? 'lease_threshold_crossed' and not pol ? 'lease_threshold_days' and not pol ? 'lease_threshold_months') then
      return jsonb_build_object('ok',false,'error','watch_policy_invalid');
    end if;
    if pol ? 'lease_threshold_days' then
      if jsonb_typeof(pol->'lease_threshold_days')<>'array' then
        return jsonb_build_object('ok',false,'error','watch_policy_invalid');
      end if;
      if jsonb_array_length(pol->'lease_threshold_days') not between 1 and 20
        or exists(select 1 from jsonb_array_elements(pol->'lease_threshold_days') v
          where case when jsonb_typeof(v)='number' and v::text ~ '^[0-9]+$' then v::text::numeric not between 0 and 3650 else true end)
        or (select count(*)<>count(distinct value) from jsonb_array_elements(pol->'lease_threshold_days')) then
        return jsonb_build_object('ok',false,'error','watch_policy_invalid');
      end if;
    end if;
    if pol ? 'lease_threshold_months' then
      if jsonb_typeof(pol->'lease_threshold_months')<>'array' then
        return jsonb_build_object('ok',false,'error','watch_policy_invalid');
      end if;
      if jsonb_array_length(pol->'lease_threshold_months') not between 1 and 20
        or exists(select 1 from jsonb_array_elements(pol->'lease_threshold_months') v
          where case when jsonb_typeof(v)='number' and v::text ~ '^[0-9]+$' then v::text::numeric not between 0 and 120 else true end)
        or (select count(*)<>count(distinct value) from jsonb_array_elements(pol->'lease_threshold_months')) then
        return jsonb_build_object('ok',false,'error','watch_policy_invalid');
      end if;
    end if;
    pol:=jsonb_build_object('include_self_changes',true)||pol;
  end if;
  if p_command='watch' then
    if w.id is null or w.state='removed' then
      insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state)
        values(a,k,sid,b,pol,'watching')
        on conflict(actor_id,subject_type,subject_id) do update set
          id=excluded.id,baseline=excluded.baseline,policy=excluded.policy,state='watching',
          lifecycle_version=1,subscribed_at=excluded.subscribed_at
        returning * into w;
      changed:=1;
    end if;
  else
    if w.id is null or w.state='removed' or w.id is distinct from (p_args->>'subscription_id')::uuid
      or w.lifecycle_version is distinct from (p_args->>'base_version')::integer then
      return jsonb_build_object('ok',false,'error','watch_stale_version');
    end if;
    update ops.subject_watch_v2_subscription set
      state=case p_command when 'pause' then 'paused' when 'resume' then 'watching' when 'unwatch' then 'removed' else state end,
      policy=case when p_command='replace-policy' then pol else policy end,
      lifecycle_version=lifecycle_version+1
      where actor_id=a and subject_type=k and subject_id=sid returning * into w;
    changed:=1;
  end if;
  return jsonb_build_object('ok',true,'schema_version','subject-watch.v2','watch',to_jsonb(w)||jsonb_build_object('health',ops.subject_watch_health(w.id)),'rows_changed',changed);
end $$;
