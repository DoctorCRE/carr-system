-- rollback: forward-only; disable the worker before retiring delivery functions.
-- expand-contract: expand; preserve the P4a event and delivery identities.
-- lock-review: bounded delivery locks, then actor quota and provider locks.
set local lock_timeout='5s';
set local statement_timeout='30s';

alter table ops.partner_needed_delivery add column attempted_token uuid;
create index web_push_active_claims on ops.partner_needed_delivery(claim_until)
  where state='claimed' and attempted_token=claim_token;
create table ops.web_push_budget (
  actor_id uuid not null references public.actor(id),
  day date not null,
  attempts integer not null check(attempts between 0 and 100),
  primary key(actor_id,day)
);
create table ops.web_push_breaker (
  origin text primary key,
  failures integer not null default 0,
  open_until timestamptz,
  probe_token uuid,
  probe_until timestamptz
);
revoke all on ops.web_push_budget,ops.web_push_breaker from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.claim_web_push() returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare result jsonb;
begin
  with due as (
    select d.event_id,d.subscription_id from ops.partner_needed_delivery d
    where (d.state in ('queued','retry_due') and d.next_attempt_at<=now())
       or (d.state='claimed' and d.claim_until<=now())
    order by d.next_attempt_at,d.event_id,d.subscription_id limit 20 for update skip locked
  ), claimed as (
    update ops.partner_needed_delivery d set state='claimed',claim_token=gen_random_uuid(),claim_until=now()+interval '5 minutes'
    from due where d.event_id=due.event_id and d.subscription_id=due.subscription_id returning d.*
  )
  select coalesce(jsonb_agg(jsonb_build_object('event_id',d.event_id,'subscription_id',d.subscription_id,
    'claim_token',d.claim_token,'card_item_id',e.card_item_id,'endpoint',s.endpoint,'p256dh',s.p256dh,'auth',s.auth,
    'key_generation',s.key_generation,'ttl',greatest(0,least(86400,extract(epoch from least(e.expires_at,e.created_at+interval '24 hours')-now())::integer)))),'[]'::jsonb)
  into result from claimed d join ops.partner_needed_event e using(event_id) join ops.web_push_subscription s using(subscription_id);
  return result;
end $$;

create function ops.authorize_web_push(p_event uuid,p_subscription uuid,p_token uuid,p_generation text) returns boolean
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare d ops.partner_needed_delivery%rowtype; e ops.partner_needed_event%rowtype;
  s ops.web_push_subscription%rowtype; pref ops.notification_preference%rowtype; b ops.web_push_breaker%rowtype;
  instant timestamptz:=clock_timestamp(); local_now timestamp; defer_until timestamptz; v_origin text; used integer;
begin
  select * into d from ops.partner_needed_delivery where event_id=p_event and subscription_id=p_subscription for update;
  if not found or d.state<>'claimed' or d.claim_token is distinct from p_token or d.claim_until<=instant or d.attempted_token=p_token then return false; end if;
  select * into e from ops.partner_needed_event where event_id=p_event;
  select * into s from ops.web_push_subscription where subscription_id=p_subscription;
  if least(e.expires_at,e.created_at+interval '24 hours')<=instant then
    update ops.partner_needed_delivery set state='expired',terminal_reason='event_expired' where event_id=p_event and subscription_id=p_subscription;
    return false;
  end if;
  if d.attempt_count>=3 then
    update ops.partner_needed_delivery set state='failed',terminal_reason='attempt_budget' where event_id=p_event and subscription_id=p_subscription;
    return false;
  end if;
  select * into pref from ops.notification_preference where actor=e.recipient_actor;
  if s.actor_id<>e.recipient_actor or s.organization_tenant_id<>e.organization_tenant_id or s.revoked_at is not null
    or s.expires_at<=instant or s.key_generation<>p_generation or not coalesce(pref.device_opt_in,false)
    or not exists(select 1 from ops.partner_needed_events(e.organization_tenant_id,e.recipient_actor) x
      where x.item_id=e.card_item_id and x.source_revision=e.source_revision and x.topic=e.topic)
    or p_subscription not in (select subscription_id from ops.web_push_subscription
      where actor_id=e.recipient_actor and organization_tenant_id=e.organization_tenant_id and revoked_at is null
      and (expires_at is null or expires_at>instant) order by created_at,subscription_id limit 3)
  then
    update ops.partner_needed_delivery set state='suppressed',terminal_reason='no_longer_eligible' where event_id=p_event and subscription_id=p_subscription;
    return false;
  end if;
  local_now:=instant at time zone pref.timezone;
  if pref.quiet_hours_start is not null and (case when pref.quiet_hours_start<=pref.quiet_hours_end
    then local_now::time>=pref.quiet_hours_start and local_now::time<pref.quiet_hours_end
    else local_now::time>=pref.quiet_hours_start or local_now::time<pref.quiet_hours_end end)
  then
    defer_until:=((local_now::date+pref.quiet_hours_end)+case when local_now::time>=pref.quiet_hours_end then interval '1 day' else interval '0' end) at time zone pref.timezone;
  end if;
  perform pg_advisory_xact_lock(hashtextextended('web-push-provider-slots',0));
  if (select count(*) from ops.partner_needed_delivery where state='claimed' and claim_until>instant
    and attempted_token=claim_token)>=2 then defer_until:=greatest(defer_until,instant+interval '1 minute'); end if;
  insert into ops.web_push_budget(actor_id,day,attempts) values(e.recipient_actor,(instant at time zone 'UTC')::date,0) on conflict do nothing;
  select attempts into used from ops.web_push_budget where actor_id=e.recipient_actor and day=(instant at time zone 'UTC')::date for update;
  if used>=100 then defer_until:=greatest(defer_until,((instant at time zone 'UTC')::date+1)::timestamp at time zone 'UTC'); end if;
  v_origin:=substring(s.endpoint from '^https://([^/?#]+)');
  if v_origin is null then
    update ops.partner_needed_delivery set state='failed',terminal_reason='invalid_endpoint' where event_id=p_event and subscription_id=p_subscription;
    return false;
  end if;
  insert into ops.web_push_breaker(origin) values(v_origin) on conflict do nothing;
  select * into b from ops.web_push_breaker where web_push_breaker.origin=v_origin for update;
  if b.open_until>instant then defer_until:=greatest(defer_until,b.open_until);
  elsif b.open_until is not null and b.probe_until>instant then defer_until:=greatest(defer_until,b.probe_until); end if;
  if defer_until is not null then
    update ops.partner_needed_delivery set state=case when defer_until>=least(e.expires_at,e.created_at+interval '24 hours') then 'expired' else 'retry_due' end,
      next_attempt_at=defer_until,terminal_reason='deferred' where event_id=p_event and subscription_id=p_subscription;
    return false;
  end if;
  if b.open_until is not null then
    update ops.web_push_breaker set probe_token=p_token,probe_until=instant+interval '5 minutes' where web_push_breaker.origin=v_origin;
  end if;
  update ops.web_push_budget set attempts=attempts+1 where actor_id=e.recipient_actor and day=(instant at time zone 'UTC')::date;
  update ops.partner_needed_delivery set attempt_count=attempt_count+1,attempted_token=p_token where event_id=p_event and subscription_id=p_subscription;
  return true;
end $$;

create function ops.finish_web_push(p_event uuid,p_subscription uuid,p_token uuid,p_status integer,p_retry_seconds integer) returns boolean
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare d ops.partner_needed_delivery%rowtype; e ops.partner_needed_event%rowtype;
  v_origin text; instant timestamptz:=clock_timestamp(); retry_at timestamptz; transient boolean; b ops.web_push_breaker%rowtype;
begin
  select * into d from ops.partner_needed_delivery where event_id=p_event and subscription_id=p_subscription for update;
  if not found or d.state<>'claimed' or d.claim_token is distinct from p_token or d.attempted_token is distinct from p_token or d.claim_until<=instant then return false; end if;
  select * into e from ops.partner_needed_event where event_id=p_event;
  select substring(endpoint from '^https://([^/?#]+)') into v_origin from ops.web_push_subscription where subscription_id=p_subscription;
  select * into b from ops.web_push_breaker where web_push_breaker.origin=v_origin for update;
  transient:=p_status=0 or p_status=429 or p_status between 500 and 599;
  if transient then
    update ops.web_push_breaker set failures=failures+1,
      open_until=case when failures+1>=5 or probe_token=p_token then instant+interval '5 minutes' else open_until end,
      probe_token=null,probe_until=null where web_push_breaker.origin=v_origin;
  elsif b.probe_token is null or b.probe_token=p_token then
    update ops.web_push_breaker set failures=0,open_until=null,probe_token=null,probe_until=null where web_push_breaker.origin=v_origin;
  end if;
  if p_status in (404,410) then
    update ops.web_push_subscription set revoked_at=coalesce(revoked_at,instant) where subscription_id=p_subscription;
  end if;
  retry_at:=instant+make_interval(secs=>greatest(case when d.attempt_count=1 then 60 else 300 end,
    case when p_status=429 then coalesce(p_retry_seconds,0) else 0 end));
  update ops.partner_needed_delivery set
    state=case when p_status between 200 and 299 then 'accepted_by_provider'
      when not transient or d.attempt_count>=3 then 'failed'
      when retry_at>=least(e.expires_at,e.created_at+interval '24 hours') then 'expired' else 'retry_due' end,
    last_status=p_status,terminal_reason=case when p_status between 200 and 299 then 'provider_accepted' when transient then 'transient_provider_failure' else 'permanent_provider_failure' end,
    next_attempt_at=retry_at,claim_until=null
    where event_id=p_event and subscription_id=p_subscription;
  return true;
end $$;

create function ops.enqueue_web_push() returns jsonb
language sql security definer set search_path=pg_catalog,pg_temp as $$
  select ops.enqueue_partner_needed('carr-internal')
$$;

revoke all on function ops.claim_web_push(),ops.authorize_web_push(uuid,uuid,uuid,text),ops.finish_web_push(uuid,uuid,uuid,integer,integer),ops.enqueue_web_push() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.claim_web_push(),ops.authorize_web_push(uuid,uuid,uuid,text),ops.finish_web_push(uuid,uuid,uuid,integer,integer),ops.enqueue_web_push() to carr_jobs;

do $append_grant$
declare contract jsonb; signature text;
begin
  contract:=ops.scac_current_grant_contract();
  foreach signature in array array['ops.claim_web_push()','ops.authorize_web_push(p_event uuid, p_subscription uuid, p_token uuid, p_generation text)','ops.finish_web_push(p_event uuid, p_subscription uuid, p_token uuid, p_status integer, p_retry_seconds integer)','ops.enqueue_web_push()'] loop
    contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||jsonb_build_array(jsonb_build_object(
      'grantee','carr_jobs','signature',signature,'grantable',false,'privilege','execute',
      'ingress_key','db-function-acl:'||signature||':carr_jobs:execute','ingress_kind','db_function_acl',
      'function_kind','f','security_definer',true,'volatility','v','parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp'))));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
