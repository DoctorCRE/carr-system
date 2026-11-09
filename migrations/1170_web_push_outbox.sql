-- rollback: remove scheduled sender before dropping these additive transport tables and functions.
-- expand-contract: no admission producers enabled by this migration.
-- lock-review: new indexed tables; registry advisory lock serializes preflight with revocation.
set local lock_timeout='5s';
set local statement_timeout='30s';
create table ops.web_push_policy (
 singleton boolean primary key default true check(singleton),
 batch_size integer not null default 4 check(batch_size between 1 and 4),
 max_attempts integer not null default 3 check(max_attempts between 1 and 3),
 hourly_cap integer not null default 12 check(hourly_cap between 1 and 12),
 daily_cap integer not null default 40 check(daily_cap between 1 and 40),
 lifetime_seconds integer not null default 86400 check(lifetime_seconds between 60 and 86400),
 request_ms integer not null default 1500 check(request_ms between 100 and 1500),
 run_ms integer not null default 15000 check(run_ms between 2000 and 15000),
 lease_seconds integer not null default 60 check(lease_seconds between 30 and 300),
 backoff_seconds integer not null default 60 check(backoff_seconds between 60 and 3600),
 circuit_seconds integer not null default 900 check(circuit_seconds between 60 and 3600),
 auth_threshold integer not null default 2 check(auth_threshold between 1 and 2),
 transient_threshold integer not null default 5 check(transient_threshold between 1 and 5)
);
insert into ops.web_push_policy default values;
create table ops.web_push_outbox (
 id uuid primary key default gen_random_uuid(),
 notification_id uuid not null references ops.notification(id),
 installation_id uuid not null references ops.web_push_subscription(installation_id),
 generation integer not null check(generation>0),
 target_kind text not null check(target_kind in ('reply_draft','post_call_review','feedback_event')),
 target_id uuid not null,
 target_ref text not null check(target_ref ~ '^al1_[A-Za-z0-9_-]{1,120}$'),
 state text not null default 'pending' check(state in ('pending','held','submitting','provider_accepted','failed','unknown','cancelled','expired')),
 reason text,
 due_at timestamptz not null default clock_timestamp(),
 created_at timestamptz not null default clock_timestamp(),
 expires_at timestamptz not null,
 attempt_id uuid,
 attempt_count integer not null default 0 check(attempt_count between 0 and 3),
 lease_until timestamptz,
 unique(notification_id,installation_id,generation)
);
create index web_push_due on ops.web_push_outbox(due_at,id) where state in ('pending','held','submitting');
create table ops.web_push_attempt (
 id uuid primary key,
 outbox_id uuid not null references ops.web_push_outbox(id),
 number integer not null,
 started_at timestamptz not null default clock_timestamp(),
 outcome text not null default 'submitting' check(outcome in ('submitting','provider_accepted','failed','unknown','cancelled','auth','gone','retry')),
 unique(outbox_id,number)
);
create index web_push_attempt_rate on ops.web_push_attempt(started_at);
create table ops.web_push_circuit (
 provider text primary key,
 auth_failures integer not null default 0,
 transient_failures integer not null default 0,
 open_until timestamptz
);
revoke all on ops.web_push_policy,ops.web_push_outbox,ops.web_push_attempt,ops.web_push_circuit
 from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.web_push_transport(p_op text,p_args jsonb default '{}') returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare cfg ops.web_push_policy; d ops.web_push_outbox; s ops.web_push_subscription;
 pref ops.notification_preference; circuit ops.web_push_circuit; n ops.notification;
 local_time time; quiet boolean; v_provider text; result jsonb; v_outcome text; retry_seconds integer; v_now timestamptz:=clock_timestamp();
begin
 select * into strict cfg from ops.web_push_policy;
 if p_op='policy' then return to_jsonb(cfg); end if;
 if p_op='status' then
  return jsonb_build_object('schema_version','web-push-delivery.v1','deliveries',coalesce((
   select jsonb_agg(x) from (select dd.id,dd.notification_id,dd.installation_id,dd.generation,dd.state,dd.reason,
    dd.attempt_id,dd.attempt_count,dd.created_at,dd.due_at,dd.expires_at from ops.web_push_outbox dd
    join ops.notification nn on nn.id=dd.notification_id where nn.recipient_actor=ops.portfolio_writer_actor_id()
    order by dd.created_at desc,dd.id limit 100) x),'[]'::jsonb));
 end if;
 -- This is the registry's lock. Hold it through submission and settlement, so revoke cannot win preflight then race HTTP.
 perform pg_advisory_xact_lock(hashtextextended('web-push-registry.v1',0));
 if p_op='claim' then
  select * into d from ops.web_push_outbox where
   (state in ('pending','held') and due_at<=v_now) or (state='submitting' and lease_until<=v_now)
   order by due_at,id for update skip locked limit 1;
  if not found then return null; end if;
  if d.state='submitting' then
   update ops.web_push_outbox set state='unknown',reason='interrupted_submission' where id=d.id;
   update ops.web_push_attempt set outcome='unknown' where id=d.attempt_id;
   return jsonb_build_object('state','unknown');
  end if;
 elsif p_op in ('preflight','settle') then
  select * into d from ops.web_push_outbox where id=(p_args->>'id')::uuid for update;
  if not found or d.state<>'submitting' or p_args->>'attempt_id' is null
    or d.attempt_id is distinct from (p_args->>'attempt_id')::uuid
    or d.lease_until<=v_now then return null; end if;
 else raise exception 'push_invalid_operation';
 end if;
 select * into strict s from ops.web_push_subscription where installation_id=d.installation_id for update;
 select * into strict n from ops.notification where id=d.notification_id;
 v_provider:=split_part(s.endpoint,'/',3)||':'||s.vapid_key_version;
 if p_op='settle' then
  v_outcome:=p_args->>'outcome';
  if v_outcome not in ('provider_accepted','failed','unknown','auth','gone','retry','cancelled') or v_outcome is null then
   raise exception 'push_invalid_outcome';
  end if;
  insert into ops.web_push_circuit(provider) values(v_provider) on conflict do nothing;
  select * into strict circuit from ops.web_push_circuit c where c.provider=v_provider for update;
  retry_seconds:=greatest(cfg.backoff_seconds * power(5,d.attempt_count-1)::integer,
    least(86400,greatest(0,coalesce((p_args->>'retry_after')::integer,0))));
  update ops.web_push_attempt set outcome=v_outcome where id=d.attempt_id;
  update ops.web_push_outbox set
   state=case when v_outcome in ('auth','retry') and d.attempt_count<cfg.max_attempts then 'held'
     when v_outcome in ('auth','retry','gone') then 'failed' else v_outcome end,
   reason=v_outcome,due_at=v_now+make_interval(secs=>retry_seconds+floor(random()*cfg.backoff_seconds)::integer)
   where id=d.id;
  if v_outcome='gone' then
   update ops.web_push_subscription set state='expired',endpoint=null,p256dh=null,auth=null,vapid_key_version=null,
    updated_at=v_now where installation_id=d.installation_id and generation=d.generation;
  end if;
  update ops.web_push_circuit c set
   auth_failures=case when v_outcome='auth' then c.auth_failures+1 else 0 end,
   transient_failures=case when v_outcome in ('retry','unknown') then c.transient_failures+1 else 0 end,
   open_until=case when v_outcome='auth' and c.auth_failures+1>=cfg.auth_threshold
     or v_outcome in ('retry','unknown') and c.transient_failures+1>=cfg.transient_threshold
     or circuit.open_until is not null and v_outcome<>'provider_accepted'
     then v_now+make_interval(secs=>cfg.circuit_seconds)
     when v_outcome='provider_accepted' then null else c.open_until end where c.provider=v_provider;
  return jsonb_build_object('state',(select state from ops.web_push_outbox where id=d.id));
 end if;
 result:=null;
 if s.state<>'active' or s.generation<>d.generation or s.actor_id<>n.recipient_actor
   or exists(select 1 from ops.web_push_session where session_hash=s.session_hash and closed) then result:='"subscription_unavailable"';
 elsif d.expires_at<=v_now or d.created_at+make_interval(secs=>cfg.lifetime_seconds)<=v_now then result:='"expired"';
 elsif d.attempt_count>=cfg.max_attempts and p_op='claim' then result:='"attempt_cap"';
 elsif d.target_kind='reply_draft' then
  if not exists(select 1 from public.email_proposal p join public.activity a on a.id=p.activity_id
    join public.actor owner on owner.slug=p.owner where p.id=d.target_id and owner.id=n.recipient_actor and owner.active and owner.kind='human'
    and p.kind='reply_draft' and p.status='pending' and coalesce(a.email_context#>>'{lifecycle,state}','live')='live')
    then result:='"source_unavailable"'; end if;
 else
  result:=ops.resolve_action_card(n.recipient_actor,d.target_kind,d.target_id);
  if result is null or (d.target_kind='post_call_review' and result->>'state'<>'pending_review')
    or (d.target_kind='feedback_event' and result->>'state'<>'resolved') then result:='"source_unavailable"'; else result:=null; end if;
 end if;
 if result is not null then
  update ops.web_push_outbox set state=case when result='"expired"'::jsonb then 'expired' else 'cancelled' end,
   reason=result#>>'{}' where id=d.id;
  if p_op='preflight' then update ops.web_push_attempt set outcome='cancelled' where id=d.attempt_id; end if;
  return jsonb_build_object('state','cancelled');
 end if;
 select * into pref from ops.notification_preference where actor=n.recipient_actor;
 local_time:=(v_now at time zone coalesce(pref.timezone,'UTC'))::time;
 quiet:=pref.quiet_hours_start is not null and case when pref.quiet_hours_start<=pref.quiet_hours_end
  then local_time>=pref.quiet_hours_start and local_time<pref.quiet_hours_end
  else local_time>=pref.quiet_hours_start or local_time<pref.quiet_hours_end end;
 select * into circuit from ops.web_push_circuit c where c.provider=v_provider;
 if not coalesce(pref.device_opt_in,false) or quiet then result:=to_jsonb(case when quiet then 'quiet_hours' else 'preference_off' end::text);
 elsif circuit.open_until>v_now then result:='"circuit_open"';
 elsif p_op='claim' and (select count(*) from ops.web_push_attempt where started_at>=date_trunc('minute',v_now))>=cfg.batch_size then result:='"run_cap"';
 elsif (select count(distinct o.notification_id) from ops.web_push_attempt a join ops.web_push_outbox o on o.id=a.outbox_id
   join ops.notification nn on nn.id=o.notification_id where nn.recipient_actor=n.recipient_actor
   and a.started_at>v_now-interval '1 hour' and o.notification_id<>d.notification_id)>=cfg.hourly_cap then result:='"hourly_cap"';
 elsif (select count(distinct o.notification_id) from ops.web_push_attempt a join ops.web_push_outbox o on o.id=a.outbox_id
   join ops.notification nn on nn.id=o.notification_id where nn.recipient_actor=n.recipient_actor
   and a.started_at>v_now-interval '24 hours' and o.notification_id<>d.notification_id)>=cfg.daily_cap then result:='"daily_cap"';
 end if;
 if result is not null then
  update ops.web_push_outbox set state='held',reason=result#>>'{}',due_at=v_now+make_interval(secs=>cfg.backoff_seconds) where id=d.id;
  if p_op='preflight' then update ops.web_push_attempt set outcome='cancelled' where id=d.attempt_id; end if;
  return jsonb_build_object('state','held');
 end if;
 if p_op='claim' then
  update ops.web_push_outbox set state='submitting',reason=null,attempt_id=gen_random_uuid(),attempt_count=attempt_count+1,
   lease_until=v_now+make_interval(secs=>cfg.lease_seconds) where id=d.id returning * into d;
  insert into ops.web_push_attempt(id,outbox_id,number) values(d.attempt_id,d.id,d.attempt_count);
  return jsonb_build_object('id',d.id,'attempt_id',d.attempt_id,'state','submitting');
 end if;
 return jsonb_build_object('endpoint',s.endpoint,'p256dh',s.p256dh,'auth',s.auth,'notification_id',d.notification_id,
   'target_ref',d.target_ref,'ttl',greatest(1,extract(epoch from least(d.expires_at,d.created_at+make_interval(secs=>cfg.lifetime_seconds))-v_now)::integer));
end $$;
revoke all on function ops.web_push_transport(text,jsonb) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.web_push_transport(text,jsonb) to carr_writer,carr_authority;
do $grant_contract$
declare contract jsonb; entries jsonb;
begin
 contract:=ops.scac_current_grant_contract();
 select jsonb_agg(jsonb_build_object('grantee',grantee,'signature','ops.web_push_transport(p_op text, p_args jsonb)',
  'grantable',false,'privilege','execute','ingress_key','db-function-acl:ops.web_push_transport(p_op text, p_args jsonb):'||grantee||':execute',
  'ingress_kind','db_function_acl','function_kind','f','security_definer',true,'volatility','v','parallel','u',
  'config',jsonb_build_array('search_path=pg_catalog, pg_temp'))) into entries from (values('carr_writer'),('carr_authority')) roles(grantee);
 contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||entries);
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
  'select '||quote_literal(contract::text)||'::jsonb');
end $grant_contract$;
