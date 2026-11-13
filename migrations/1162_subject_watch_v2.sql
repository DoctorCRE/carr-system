-- rollback: remove v2 verbs then drop the v2 function and table; v1 is untouched.
-- expand-contract: additive v2 policy subscriptions beside v1 producer subscriptions.
-- lock-review: new table only; bounded actor-subject locks serialize lifecycle changes.
set local lock_timeout='5s';
set local statement_timeout='30s';
create table ops.subject_watch_v2_subscription (
  id uuid not null unique default gen_random_uuid(),
  actor_id uuid not null references public.actor(id),
  subject_type text not null check(subject_type in ('deal','lease','listing')),
  subject_id uuid not null,
  baseline jsonb not null,
  policy jsonb not null,
  state text not null check(state in ('watching','paused','removed')),
  lifecycle_version integer not null default 1 check(lifecycle_version>0),
  subscribed_at timestamptz not null default clock_timestamp(),
  primary key(actor_id,subject_type,subject_id)
);
create index subject_watch_v2_actor_cursor on ops.subject_watch_v2_subscription(actor_id,id);
revoke all on ops.subject_watch_v2_subscription from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.subject_watch_v2(p_command text,p_args jsonb) returns jsonb
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
    return jsonb_build_object('ok',true,'schema_version','subject-watch.v2','watch',case when w.state<>'removed' then to_jsonb(w) end);
  end if;
  if p_command in ('watch','replace-policy') then
    pol:=p_args->'policy';
    if jsonb_typeof(pol)<>'object' or jsonb_typeof(pol->'change_kinds')<>'array'
      or coalesce(jsonb_array_length(pol->'change_kinds'),0) not between 1 and 3
      or exists(select 1 from jsonb_array_elements_text(pol->'change_kinds') v where v not in ('reply_arrived','lease_threshold_crossed','listing_status_changed')) then
      return jsonb_build_object('ok',false,'error','watch_policy_invalid');
    end if;
    if exists(select 1 from jsonb_object_keys(pol) key where key not in ('change_kinds','include_self_changes','lease_threshold_days'))
      or (pol ? 'include_self_changes' and jsonb_typeof(pol->'include_self_changes')<>'boolean')
      or (select count(*)<>count(distinct value) from jsonb_array_elements(pol->'change_kinds'))
      or (pol->'change_kinds' ? 'lease_threshold_crossed' and not pol ? 'lease_threshold_days') then
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
  return jsonb_build_object('ok',true,'schema_version','subject-watch.v2','watch',to_jsonb(w),'rows_changed',changed);
end $$;
revoke all on function ops.subject_watch_v2(text,jsonb) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.subject_watch_v2(text,jsonb) to carr_writer,carr_authority;
do $append_grant$
declare contract jsonb; entries jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  select jsonb_agg(jsonb_build_object(
    'grantee',grantee,'signature','ops.subject_watch_v2(p_command text, p_args jsonb)','grantable',false,'privilege','execute',
    'ingress_key','db-function-acl:ops.subject_watch_v2(p_command text, p_args jsonb):'||grantee||':execute',
    'ingress_kind','db_function_acl','function_kind','f','security_definer',true,
    'volatility','v','parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp')))
    into entries from (values('carr_writer'),('carr_authority')) roles(grantee);
  contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||entries);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
