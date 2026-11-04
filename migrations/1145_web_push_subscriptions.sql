-- WR617.S3.J2. Forward-only lifecycle store; no transport or preference changes.
-- lock-review: new tables only; bounded actor-scoped advisory lock.
create table ops.web_push_subscription (
  subscription_id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  actor_id uuid not null references public.actor(id),
  installation_id uuid not null,
  generation integer not null check (generation > 0),
  endpoint text not null check (length(endpoint) <= 2048),
  endpoint_hash text not null,
  p256dh text not null,
  auth text not null,
  key_generation text not null,
  expires_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  revoked_at timestamptz,
  unique (organization_tenant_id,actor_id,installation_id,generation)
);
create index web_push_owner on ops.web_push_subscription(organization_tenant_id,actor_id,installation_id,generation desc);
create index web_push_endpoint on ops.web_push_subscription(endpoint_hash);
create table ops.web_push_action (
  organization_tenant_id text not null,
  actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  operation text not null,
  request_hash text not null,
  response jsonb not null,
  created_at timestamptz not null default clock_timestamp(),
  primary key (organization_tenant_id,actor_id,idempotency_key)
);
create index web_push_action_budget on ops.web_push_action(organization_tenant_id,actor_id,created_at);
revoke all on ops.web_push_subscription,ops.web_push_action from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.web_push_lifecycle(p_request jsonb,p_key uuid,p_operation text) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,ops,public,pg_temp
set lock_timeout='2s'
set statement_timeout='5s'
as $$
declare
  who uuid:=ops.portfolio_writer_actor_id();
  tenant text:=nullif(current_setting('carr.organization_tenant_id',true),'');
  installation uuid;
  prior ops.web_push_subscription%rowtype;
  receipt ops.web_push_action%rowtype;
  fingerprint text:=encode(sha256(convert_to(p_request::text,'UTF8')),'hex');
  answer jsonb;
  version integer;
  subscription uuid;
  expiry timestamptz;
  instant timestamptz:=clock_timestamp();
begin
  if tenant is null or who is null then return jsonb_build_object('error','web_push_unavailable'); end if;
  if p_key is null or p_operation is null or p_operation not in ('register','revoke') or jsonb_typeof(p_request) is distinct from 'object'
    or coalesce(p_request->>'installation_id','') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    or exists(select 1 from jsonb_object_keys(p_request) k where k not in ('installation_id','endpoint','p256dh','auth','expirationTime','key_generation'))
    then return jsonb_build_object('error','web_push_invalid_input'); end if;
  installation:=(p_request->>'installation_id')::uuid;
  perform pg_advisory_xact_lock(hashtextextended('web-push:'||tenant||':'||who::text,0));
  select * into receipt from ops.web_push_action where organization_tenant_id=tenant and actor_id=who and idempotency_key=p_key;
  if found then
    if receipt.operation<>p_operation or receipt.request_hash<>fingerprint then return jsonb_build_object('error','key_reuse'); end if;
    return receipt.response;
  end if;
  if (select count(*) from ops.web_push_action where organization_tenant_id=tenant and actor_id=who and created_at>instant-interval '1 minute')>=10
    then return jsonb_build_object('error','web_push_rate_limit'); end if;
  select * into prior from ops.web_push_subscription where organization_tenant_id=tenant and actor_id=who and installation_id=installation order by generation desc limit 1;
  if p_operation='revoke' then
    update ops.web_push_subscription set revoked_at=coalesce(revoked_at,instant) where subscription_id=prior.subscription_id;
    answer:=jsonb_build_object('ok',true,'subscription_id',prior.subscription_id,'state',case when prior.subscription_id is null then 'absent' else 'revoked' end,'generation',coalesce(prior.generation,0));
  else
    if coalesce(p_request->>'endpoint','') !~ '^https://[^/@?#[:space:]]+([/?][^#[:space:]]*)?$'
      or length(p_request->>'endpoint')>2048 or position('#' in p_request->>'endpoint')>0
      or coalesce(p_request->>'p256dh','') !~ '^B[A-P][A-Za-z0-9_-]{84}[AEIMQUYcgkosw048]$'
      or coalesce(p_request->>'auth','') !~ '^[A-Za-z0-9_-]{21}[AQgw]$'
      or coalesce(p_request->>'key_generation','') !~ '^[A-Za-z0-9._-]{1,80}$'
      then return jsonb_build_object('error','web_push_invalid_input'); end if;
    expiry:=case when p_request->>'expirationTime' is null then null else to_timestamp((p_request->>'expirationTime')::numeric/1000) end;
    if expiry<=instant then return jsonb_build_object('error','web_push_invalid_input'); end if;
    if prior.revoked_at is null and (prior.expires_at is null or prior.expires_at>instant)
      and prior.endpoint=p_request->>'endpoint' and prior.p256dh=p_request->>'p256dh'
      and prior.auth=p_request->>'auth' and prior.key_generation=p_request->>'key_generation' and prior.expires_at is not distinct from expiry then
      subscription:=prior.subscription_id; version:=prior.generation;
    else
      if (select count(*) from ops.web_push_subscription where organization_tenant_id=tenant and actor_id=who and installation_id<>installation and revoked_at is null and (expires_at is null or expires_at>instant))>=3
        then return jsonb_build_object('error','web_push_limit'); end if;
      update ops.web_push_subscription set revoked_at=coalesce(revoked_at,instant) where subscription_id=prior.subscription_id;
      version:=coalesce(prior.generation,0)+1;
      insert into ops.web_push_subscription(organization_tenant_id,actor_id,installation_id,generation,endpoint,endpoint_hash,p256dh,auth,key_generation,expires_at)
      values(tenant,who,installation,version,p_request->>'endpoint',encode(sha256(convert_to(p_request->>'endpoint','UTF8')),'hex'),p_request->>'p256dh',p_request->>'auth',p_request->>'key_generation',expiry)
      returning subscription_id into subscription;
    end if;
    answer:=jsonb_build_object('ok',true,'subscription_id',subscription,'state','active','generation',version);
  end if;
  insert into ops.web_push_action(organization_tenant_id,actor_id,idempotency_key,operation,request_hash,response)
  values(tenant,who,p_key,p_operation,fingerprint,answer);
  return answer;
end $$;
revoke all on function ops.web_push_lifecycle(jsonb,uuid,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.web_push_lifecycle(jsonb,uuid,text) to carr_writer;
