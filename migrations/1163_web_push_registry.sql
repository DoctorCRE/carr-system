-- rollback: remove subscription verbs before dropping the new registry and functions.
-- expand-contract: additive registry and replay receipts only.
-- lock-review: new tables only; bounded registry mutations use one transaction advisory lock.
set local lock_timeout='5s';
set local statement_timeout='30s';

create table ops.web_push_session (
  session_hash text primary key,
  actor_id uuid not null references public.actor(id),
  closed boolean not null default false
);
create table ops.web_push_endpoint_owner (
  endpoint_hash text primary key,
  actor_id uuid not null references public.actor(id)
);
create table ops.web_push_subscription (
  installation_id uuid primary key,
  actor_id uuid not null references public.actor(id),
  session_hash text not null references ops.web_push_session(session_hash),
  generation integer not null check(generation>0),
  state text not null check(state in ('active','revoked','expired')),
  endpoint text,
  p256dh text,
  auth text,
  vapid_key_version text,
  updated_at timestamptz not null default clock_timestamp(),
  check((state='active') = (endpoint is not null and p256dh is not null and auth is not null and vapid_key_version is not null))
);
create unique index web_push_endpoint_active on ops.web_push_subscription(endpoint) where state='active';
create index web_push_partner on ops.web_push_subscription(actor_id,state);
create table ops.web_push_receipt (
  operation_key uuid primary key,
  actor_id uuid not null references public.actor(id),
  request_hash text not null,
  response jsonb not null,
  created_at timestamptz not null default clock_timestamp()
);
revoke all on ops.web_push_session,ops.web_push_endpoint_owner,ops.web_push_subscription,ops.web_push_receipt
 from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create index web_push_receipt_rate on ops.web_push_receipt(actor_id,created_at);

create function ops.web_push_registry(p_operation text,p_args jsonb,p_session text) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid; old ops.web_push_subscription; result jsonb; fingerprint text; prior ops.web_push_receipt;
  installation uuid; expected integer; key uuid; v_endpoint_hash text;
begin
  a:=ops.portfolio_writer_actor_id();
  if not exists(select 1 from public.actor where id=a and active and kind='human' and slug in ('joe','dell'))
    or coalesce(nullif(current_setting('carr.organization_tenant_id',true),''),'carr-internal')<>'carr-internal'
    or p_session is null or p_session !~ '^[a-f0-9]{64}$' then
    raise exception using errcode='42501',message='push_session_required';
  end if;
  if p_operation='list' then
    return jsonb_build_object('ok',true,'schema_version','web-push-registry.v1','subscriptions',
      coalesce((select jsonb_agg(jsonb_build_object('installation_id',installation_id,'generation',generation,
        'state',state,'vapid_key_version',vapid_key_version) order by installation_id)
        from ops.web_push_subscription where actor_id=a),'[]'::jsonb));
  end if;
  if p_operation not in ('register','revoke','logout') then
    raise exception using errcode='22023',message='push_invalid_request';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('web-push-registry.v1',0));
  insert into ops.web_push_session(session_hash,actor_id) values(p_session,a) on conflict do nothing;
  if not exists(select 1 from ops.web_push_session where session_hash=p_session and actor_id=a) then
    raise exception using errcode='42501',message='push_session_required';
  end if;
  if p_operation='logout' then
    update ops.web_push_session set closed=true where session_hash=p_session;
    update ops.web_push_subscription set state='revoked',generation=generation+1,endpoint=null,p256dh=null,auth=null,
      vapid_key_version=null,updated_at=clock_timestamp() where session_hash=p_session and actor_id=a and state='active';
    return jsonb_build_object('ok',true,'schema_version','web-push-registry.v1');
  end if;
  if exists(select 1 from ops.web_push_session where session_hash=p_session and closed) then
    raise exception using errcode='42501',message='push_session_closed';
  end if;
  key:=(p_args->>'idempotency_key')::uuid;
  installation:=(p_args->>'installation_id')::uuid;
  expected:=(p_args->>'base_generation')::integer;
  if key is null or installation is null or expected is null or expected<0 then
    raise exception using errcode='22023',message='push_invalid_request';
  end if;
  fingerprint:=encode(sha256(convert_to(jsonb_build_array(a,p_session,p_operation,p_args)::text,'UTF8')),'hex');
  select * into prior from ops.web_push_receipt where operation_key=key;
  if found then
    if prior.request_hash<>fingerprint then
      raise exception using errcode='22023',message='push_key_reuse';
    end if;
    return prior.response||jsonb_build_object('replayed',true);
  end if;
  if (select count(*) from ops.web_push_receipt where actor_id=a and created_at>clock_timestamp()-interval '1 minute')>=20 then
    raise exception using errcode='54000',message='push_rate_limited';
  end if;
  select * into old from ops.web_push_subscription where installation_id=installation;
  if found and old.actor_id<>a then
    raise exception using errcode='42501',message='push_subscription_unavailable';
  end if;
  if coalesce(old.generation,0)<>expected then
    raise exception using errcode='40001',message='push_generation_conflict';
  end if;
  if p_operation='register' then
    if length(p_args->>'endpoint')>2048 or (p_args->>'endpoint') !~ '^https://(fcm[.]googleapis[.]com|updates[.]push[.]services[.]mozilla[.]com|[a-z0-9-]+[.]notify[.]windows[.]com|web[.]push[.]apple[.]com)/[^[:space:]#]+$'
      or (p_args->>'p256dh') !~ '^B[A-Za-z0-9_-]{86}$' or (p_args->>'auth') !~ '^[A-Za-z0-9_-]{22}$'
      or p_args->>'vapid_key_version'<>'v1'
      or not(p_args ?& array['endpoint','p256dh','auth','vapid_key_version']) then
      raise exception using errcode='22023',message='push_invalid_subscription';
    end if;
    v_endpoint_hash:=encode(sha256(convert_to(p_args->>'endpoint','UTF8')),'hex');
    insert into ops.web_push_endpoint_owner(endpoint_hash,actor_id) values(v_endpoint_hash,a) on conflict do nothing;
    if not exists(select 1 from ops.web_push_endpoint_owner e where e.endpoint_hash=v_endpoint_hash and actor_id=a)
      or exists(select 1 from ops.web_push_subscription where endpoint=p_args->>'endpoint' and installation_id<>installation and state='active') then
      raise exception using errcode='42501',message='push_subscription_unavailable';
    end if;
    if coalesce(old.state,'off')<>'active' and (select count(*) from ops.web_push_subscription where actor_id=a and state='active')>=3 then
      raise exception using errcode='54000',message='push_installation_limit';
    end if;
    insert into ops.web_push_subscription(installation_id,actor_id,session_hash,generation,state,endpoint,p256dh,auth,vapid_key_version)
      values(installation,a,p_session,expected+1,'active',p_args->>'endpoint',p_args->>'p256dh',p_args->>'auth',p_args->>'vapid_key_version')
      on conflict(installation_id) do update set session_hash=excluded.session_hash,generation=excluded.generation,state='active',endpoint=excluded.endpoint,
        p256dh=excluded.p256dh,auth=excluded.auth,vapid_key_version=excluded.vapid_key_version,updated_at=clock_timestamp();
  else
    if old.installation_id is null then
      raise exception using errcode='42501',message='push_subscription_unavailable';
    end if;
    update ops.web_push_subscription set generation=generation+1,state='revoked',endpoint=null,p256dh=null,auth=null,
      vapid_key_version=null,updated_at=clock_timestamp() where installation_id=installation;
  end if;
  result:=jsonb_build_object('ok',true,'schema_version','web-push-registry.v1','installation_id',installation,
    'generation',expected+1,'state',case when p_operation='register' then 'active' else 'revoked' end);
  insert into ops.web_push_receipt(operation_key,actor_id,request_hash,response) values(key,a,fingerprint,result);
  return result;
end $$;
revoke all on function ops.web_push_registry(text,jsonb,text) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.web_push_registry(text,jsonb,text) to carr_writer,carr_authority;

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
      ('ops.web_push_registry(p_operation text, p_args jsonb, p_session text)','v')
    ) functions(signature,volatility) cross join (values('carr_writer'),('carr_authority')) roles(grantee);
  -- 1164 may already be installed. Keep its grants after 1163 in both upgrade orders.
  select jsonb_agg(entry order by
    case when entry->>'signature' in (
      'ops.record_deal_document_text(p_actor uuid, p_args jsonb)',
      'ops.list_deal_document_text_sources(p_actor uuid, p_after uuid, p_limit integer)'
    ) then 1 else 0 end, ordinal)
    into entries from jsonb_array_elements((contract->'secdef_execute')||entries)
      with ordinality as grants(entry,ordinal);
  contract:=jsonb_set(contract,'{secdef_execute}',entries);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
