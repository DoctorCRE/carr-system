-- rollback: remove watch verbs before dropping ops.subject_watch and its functions.
-- expand-contract: additive partner subscriptions, without notification effects.
-- lock-review: new table and functions only; no rewrite of business records.
set local lock_timeout='5s';
set local statement_timeout='30s';

create table ops.subject_watch (
  id uuid not null default gen_random_uuid() unique,
  actor_id uuid not null references public.actor(id),
  subject_type text not null check(subject_type in ('deal','lease')),
  subject_id uuid not null,
  baseline jsonb not null check(jsonb_typeof(baseline)='object'),
  subscribed_at timestamptz not null default clock_timestamp(),
  primary key(actor_id,subject_type,subject_id)
);
create index subject_watch_actor_cursor on ops.subject_watch(actor_id,id);
revoke all on ops.subject_watch from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.subject_watch_actor() returns uuid
language plpgsql stable set search_path=pg_catalog,pg_temp as $$
declare a uuid;
begin
  a:=ops.portfolio_writer_actor_id();
  if coalesce(nullif(current_setting('carr.organization_tenant_id',true),''),'carr-internal')<>'carr-internal'
    or not exists(select 1 from public.actor where id=a and active and kind='human' and slug in ('joe','dell')) then
    raise exception using errcode='42501',message='watch_partner_required';
  end if;
  return a;
end $$;

create function ops.subject_watch_target(p_kind text,p_id uuid) returns jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$
  select jsonb_build_object('schema_version','subject-watch-baseline.v1','subject_type',p_kind,
    'subject_id',d.id,'version',d.version)
    from public.v_deal_room_deal visible join public.deal d on d.id=visible.id
    join public.client c on c.id=d.client_id and c.merged_into is null
    join public.party p on p.id=c.party_id and p.deleted_at is null and p.merged_into is null
    where p_kind='deal' and d.id=p_id
  union all
  select jsonb_build_object('schema_version','subject-watch-baseline.v1','subject_type',p_kind,
    'subject_id',l.id,'version',l.version)
    from public.lease l join public.client c on c.id=l.client_id and c.merged_into is null
    join public.party p on p.id=c.party_id and p.deleted_at is null and p.merged_into is null
    where p_kind='lease' and l.id=p_id
$$;

create function ops.read_subject_watch(p_kind text,p_id uuid) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid; target jsonb; w jsonb;
begin
  a:=ops.subject_watch_actor();
  target:=ops.subject_watch_target(p_kind,p_id);
  if target is null then return jsonb_build_object('ok',false,'error','watch_target_not_found'); end if;
  select to_jsonb(s) into w from ops.subject_watch s where actor_id=a and subject_type=p_kind and subject_id=p_id;
  return jsonb_build_object('ok',true,'schema_version','subject-watch.v1','watch',w);
end $$;

create function ops.set_subject_watch(p_kind text,p_id uuid,p_watching boolean) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid; baseline jsonb; w jsonb; changed integer;
begin
  a:=ops.subject_watch_actor();
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

create function ops.list_subject_watches(p_cursor uuid,p_limit integer) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid; result jsonb;
begin
  a:=ops.subject_watch_actor();
  if p_limit is null or p_limit<1 or p_limit>100 then raise exception 'watch_limit_invalid'; end if;
  select coalesce(jsonb_agg(to_jsonb(page) order by page.id),'[]'::jsonb) into result from (
    select s.* from ops.subject_watch s
    where s.actor_id=a and (p_cursor is null or s.id>p_cursor)
      and ops.subject_watch_target(s.subject_type,s.subject_id) is not null
    order by s.id limit p_limit+1
  ) page;
  return jsonb_build_object('ok',true,'schema_version','subject-watch.v1','watches',result);
end $$;

revoke all on function ops.subject_watch_actor(),ops.subject_watch_target(text,uuid),
  ops.read_subject_watch(text,uuid),ops.set_subject_watch(text,uuid,boolean),ops.list_subject_watches(uuid,integer)
  from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.read_subject_watch(text,uuid),ops.set_subject_watch(text,uuid,boolean),
  ops.list_subject_watches(uuid,integer) to carr_writer,carr_authority;

do $append_grant$
declare contract jsonb; entries jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  select jsonb_agg(jsonb_build_object(
    'grantee',grantee,'signature',signature,'grantable',false,'privilege','execute',
    'ingress_key','db-function-acl:'||signature||':'||grantee||':execute',
    'ingress_kind','db_function_acl','function_kind','f','security_definer',true,
    'volatility',volatility,'parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp')))
    into entries from (values
      ('ops.read_subject_watch(p_kind text, p_id uuid)','s'),
      ('ops.set_subject_watch(p_kind text, p_id uuid, p_watching boolean)','v'),
      ('ops.list_subject_watches(p_cursor uuid, p_limit integer)','s')
    ) functions(signature,volatility) cross join (values('carr_writer'),('carr_authority')) roles(grantee);
  contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||entries);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
