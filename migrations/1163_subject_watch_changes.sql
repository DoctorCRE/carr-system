-- rollback: restore the prior consumer before dropping change capture functions/tables.
-- expand-contract: pending semantic receipts; emission remains a separate delivery slice.
-- lock-review: additive tables, short trigger install; no source table rewrite.
set local lock_timeout='5s';
set local statement_timeout='30s';

create function ops.subject_watch_material(p_kind text,p_id uuid) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
  select jsonb_build_object('phase',btrim(d.phase),'outcome',nullif(btrim(d.outcome),''),
    'owner',nullif(btrim(d.owner),''),'next_date',d.next_date,'closed_on',d.closed_on,
    'operating_state',d.operating_state,
    'critical_dates',(select coalesce(jsonb_agg(v order by v::text),'[]') from
      (select distinct jsonb_build_object('kind',btrim(kind),'due_on',due_on) v
       from public.critical_date where deal_id=d.id and status='open') dates),
    'action_owners',(select coalesce(jsonb_agg(v order by v::text),'[]') from
      (select distinct owner_id v from public.next_action where subject_type='deal' and subject_id=d.id and status='open') owners),
    'leases',(select coalesce(jsonb_agg(v order by v::text),'[]') from
      (select distinct jsonb_build_object('expiration_on',expiration_on,'executed_on',executed_on,'status',status) v
       from public.lease where deal_id=d.id and status='current') leases))
  from public.deal d where p_kind='deal' and d.id=p_id
  union all
  select jsonb_build_object('expiration_on',l.expiration_on,'status',l.status,
    'replacement',(select jsonb_build_object('expiration_on',n.expiration_on,'executed_on',n.executed_on,'status',n.status)
      from public.lease n where n.supersedes_lease_id=l.id and n.status='current' order by n.id limit 1),
    'notice_on',(select min(due_on) from public.critical_date where deal_id=l.deal_id and kind='option_window' and status='open'))
  from public.lease l where p_kind='lease' and l.id=p_id
$$;

create function ops.classify_subject_watch_change(p_before jsonb,p_after jsonb)
returns jsonb language sql immutable set search_path=pg_catalog,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('field',key,'before',p_before->key,'after',p_after->key,
    'reason_code',case when p_before->key is null or p_before->key='null'::jsonb then 'unknown_to_known'
      when p_after->key is null or p_after->key='null'::jsonb then 'known_to_unknown' else 'value_changed' end) order by key),'[]')
  from jsonb_object_keys(p_after) key where p_before->key is distinct from p_after->key
$$;

create table ops.subject_watch_material_baseline (
  watch_id uuid primary key, material jsonb not null
);
create table ops.subject_watch_change (
  id uuid primary key default gen_random_uuid(),
  watch_generation_id uuid not null,
  actor_id uuid not null references public.actor(id),
  subject_type text not null check(subject_type in ('deal','lease')),
  subject_id uuid not null,
  semantic_key text not null,
  source_event_id uuid not null references public.event(id),
  source_revision integer not null,
  policy_version integer not null,
  category text not null check(category in ('material_changed','reply_arrived')),
  changes jsonb not null,
  evidence_refs jsonb not null,
  occurred_at timestamptz not null,
  recorded_at timestamptz not null default clock_timestamp(),
  eligible_at timestamptz not null default clock_timestamp(),
  disposition text not null default 'pending' check(disposition in ('pending','emitted','suppressed')),
  notification_id uuid references ops.notification(id),
  failure_count integer not null default 0 check(failure_count between 0 and 2),
  failure_code text,
  unique(watch_generation_id,semantic_key)
);
create index subject_watch_change_read on ops.subject_watch_change(actor_id,watch_generation_id,id);
create index subject_watch_change_pending on ops.subject_watch_change(eligible_at,id) where disposition='pending';
revoke all on ops.subject_watch_material_baseline,ops.subject_watch_change from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.subject_watch_material_start() returns trigger
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
begin
  if tg_op='DELETE' then
    delete from ops.subject_watch_material_baseline where watch_id=old.id; return old;
  end if;
  if tg_op='UPDATE' and old.id is distinct from new.id then
    delete from ops.subject_watch_material_baseline where watch_id=old.id;
  end if;
  if to_jsonb(new)->>'state'='removed' then
    delete from ops.subject_watch_material_baseline where watch_id=new.id; return new;
  end if;
  if tg_op='INSERT' or new.id is distinct from old.id or (to_jsonb(new)->>'state'='watching' and to_jsonb(old)->>'state'='paused') then
    insert into ops.subject_watch_material_baseline(watch_id,material)
    values(new.id,ops.subject_watch_material(new.subject_type,new.subject_id))
    on conflict(watch_id) do update set material=excluded.material;
  end if;
  return new;
end $$;
create trigger subject_watch_material_start after insert or delete on ops.subject_watch
for each row execute function ops.subject_watch_material_start();
create trigger subject_watch_v2_material_start after insert or update on ops.subject_watch_v2_subscription
for each row execute function ops.subject_watch_material_start();
insert into ops.subject_watch_material_baseline
select id,ops.subject_watch_material(subject_type,subject_id) from ops.subject_watch
union all select id,ops.subject_watch_material(subject_type,subject_id) from ops.subject_watch_v2_subscription where subject_type in ('deal','lease');

create function ops.capture_subject_watch_changes(p_event uuid) returns void
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare e public.event; w record; before_value jsonb; after_value jsonb; delta jsonb;
  reply public.activity; version_value integer; key_value text; category_value text;
begin
  select * into strict e from public.event where id=p_event;
  if coalesce(e.organization_tenant_id,'carr-internal')<>'carr-internal'
    or coalesce(e.personal_scope,'none') not in ('none','joe-personal','dell-personal') then return; end if;
  if e.verb in ('confirm-email-filing','inherit-email-thread') and e.new_value->>'activity_id' is not null then
    select * into reply from public.activity where id=(e.new_value->>'activity_id')::uuid
      and deal_id=e.subject_id and kind='email_in'
      and coalesce(email_context#>>'{lifecycle,state}','live')='live';
  end if;
  for w in
    select s.*,a.slug from (
      select id,actor_id,subject_type,subject_id,subscribed_at,1 policy_version,true include_self,true replies
      from ops.subject_watch
      union all
      select id,actor_id,subject_type,subject_id,subscribed_at,lifecycle_version,
        coalesce((policy->>'include_self_changes')::boolean,true),policy->'change_kinds' ? 'reply_arrived'
      from ops.subject_watch_v2_subscription where state='watching'
    ) s join public.actor a on a.id=s.actor_id
    where (
      (s.subject_type=e.subject_type and s.subject_id=e.subject_id)
      or (e.subject_type='deal' and s.subject_type='lease' and exists(select 1 from public.lease where id=s.subject_id and deal_id=e.subject_id)))
    and ops.subject_watch_target(s.subject_type,s.subject_id) is not null
    order by s.id
  loop
    perform pg_advisory_xact_lock(hashtextextended(w.actor_id::text||':'||w.subject_type||':'||w.subject_id::text,0));
    if not exists(select 1 from ops.subject_watch where id=w.id)
      and not exists(select 1 from ops.subject_watch_v2_subscription where id=w.id and state='watching') then continue; end if;
    if exists(select 1 from ops.subject_watch_v2_subscription where id=w.id) then
    select lifecycle_version,coalesce((policy->>'include_self_changes')::boolean,true),policy->'change_kinds' ? 'reply_arrived'
      into w.policy_version,w.include_self,w.replies from ops.subject_watch_v2_subscription where id=w.id and state='watching';
    if not found then continue; end if;
    end if;
    select material into before_value from ops.subject_watch_material_baseline where watch_id=w.id for update;
    if not found then continue; end if;
    after_value:=ops.subject_watch_material(w.subject_type,w.subject_id);
    if after_value is null then continue; end if;
    delta:=ops.classify_subject_watch_change(before_value,after_value);
    update ops.subject_watch_material_baseline set material=after_value where watch_id=w.id;
    select (ops.subject_watch_target(w.subject_type,w.subject_id)->>'version')::integer into version_value;
    key_value:=e.id||':material'; category_value:='material_changed';
    if reply.id is not null then
      if w.subject_type<>'deal' or not w.replies or reply.email_source->>'mailbox_owner' is distinct from w.slug then continue; end if;
      key_value:='reply:'||reply.id; category_value:='reply_arrived'; delta:='[]';
    elsif delta='[]' then continue;
    end if;
    if not w.include_self and (e.actor_id=w.actor_id or e.sponsoring_human_slug=w.slug) then continue; end if;
    insert into ops.subject_watch_change(watch_generation_id,actor_id,subject_type,subject_id,semantic_key,
      source_event_id,source_revision,policy_version,category,changes,evidence_refs,occurred_at)
    values(w.id,w.actor_id,w.subject_type,w.subject_id,key_value,e.id,version_value,w.policy_version,
      category_value,delta,jsonb_build_array(jsonb_build_object('event_id',e.id,'activity_id',reply.id)),e.occurred_at)
    on conflict(watch_generation_id,semantic_key) do nothing;
  end loop;
end $$;
create or replace function ops.consume_subject_watch_event(p_event uuid) returns void
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare e public.event%rowtype; s record; d record; reply public.activity%rowtype;
  source_key text; reason text;
begin
  select * into e from public.event where id=p_event;
  if not found then raise exception 'watch_event_required'; end if;
  if coalesce(e.organization_tenant_id,'carr-internal')<>'carr-internal'
    or coalesce(e.personal_scope,'none') not in ('none','joe-personal','dell-personal') then return; end if;
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
      end if;
      if source_key is not null then
        for s in select sw.*,a.slug from ops.subject_watch sw join public.actor a on a.id=sw.actor_id
          where sw.subject_type='deal' and sw.subject_id=d.id and sw.subscribed_at<=e.recorded_at
          and reply.email_source->>'mailbox_owner'=a.slug
          order by sw.id limit 2 for update of sw
        loop
          perform ops.mint_notification('event',e.id,'deal',d.id::text,reason,'action_required',
            '/deals?deal='||d.id::text,'subject-watch:'||s.id||':'||source_key,s.slug,false,false);
        end loop;
      end if;
    end if;
  end if;
  perform ops.capture_subject_watch_changes(e.id);
  perform ops.scan_subject_watch_leases(e.id,50);
end $$;


create function ops.read_subject_watch_changes(p_kind text,p_id uuid,p_watch uuid,p_cursor uuid,p_limit integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.subject_watch_actor(); result jsonb;
begin
  if p_limit is null or p_limit not between 1 and 100 then raise exception 'watch_limit_invalid'; end if;
  if ops.subject_watch_target(p_kind,p_id) is null then
    return jsonb_build_object('ok',false,'error','watch_target_not_found'); end if;
  if not exists(select 1 from ops.subject_watch where id=p_watch and actor_id=a and subject_type=p_kind and subject_id=p_id)
    and not exists(select 1 from ops.subject_watch_v2_subscription where id=p_watch and actor_id=a and subject_type=p_kind and subject_id=p_id and state<>'removed') then
    return jsonb_build_object('ok',false,'error','watch_target_not_found'); end if;
  select coalesce(jsonb_agg(to_jsonb(page) order by page.id),'[]') into result from (
    select id,watch_generation_id,subject_type,subject_id,source_event_id,source_revision,policy_version,
      category,changes,evidence_refs,occurred_at,recorded_at,eligible_at,disposition,notification_id
    from ops.subject_watch_change where actor_id=a and watch_generation_id=p_watch
      and (category<>'reply_arrived' or exists(select 1 from public.activity mail join public.actor owner on owner.id=a
        where mail.id=(evidence_refs->0->>'activity_id')::uuid and mail.email_source->>'mailbox_owner'=owner.slug
        and coalesce(mail.email_context#>>'{lifecycle,state}','live')='live'))
      and (p_cursor is null or id>p_cursor) order by id limit p_limit+1
  ) page;
  return jsonb_build_object('ok',true,'schema_version','subject-watch-change.v1','changes',
    (select coalesce(jsonb_agg(value order by ordinality),'[]') from jsonb_array_elements(result) with ordinality where ordinality<=p_limit),
    'has_more',jsonb_array_length(result)>p_limit,'next_cursor',case when jsonb_array_length(result)>p_limit then result->(p_limit-1)->>'id' end);
end $$;
revoke all on function ops.subject_watch_material(text,uuid),ops.classify_subject_watch_change(jsonb,jsonb),
  ops.subject_watch_material_start(),ops.capture_subject_watch_changes(uuid),ops.read_subject_watch_changes(text,uuid,uuid,uuid,integer)
  from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.read_subject_watch_changes(text,uuid,uuid,uuid,integer) to carr_writer,carr_authority;
do $append_grant$
declare contract jsonb; entries jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  select jsonb_agg(jsonb_build_object(
    'grantee',grantee,'signature','ops.read_subject_watch_changes(p_kind text, p_id uuid, p_watch uuid, p_cursor uuid, p_limit integer)','grantable',false,'privilege','execute',
    'ingress_key','db-function-acl:ops.read_subject_watch_changes(p_kind text, p_id uuid, p_watch uuid, p_cursor uuid, p_limit integer):'||grantee||':execute',
    'ingress_kind','db_function_acl','function_kind','f','security_definer',true,
    'volatility','s','parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp')))
    into entries from (values('carr_writer'),('carr_authority')) roles(grantee);
  contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||entries);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
