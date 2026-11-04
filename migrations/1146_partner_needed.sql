-- rollback: forward-only; revoke entry points before retiring durable event history.
-- expand-contract: expand; add review sources without changing legacy feedback settlement.
-- lock-review: new tables and function metadata only; tenant advisory lock bounds producers.
set local lock_timeout = '5s';
set local statement_timeout = '30s';

create table ops.partner_needed_event (
  event_id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  recipient_actor uuid not null references public.actor(id),
  topic text not null check(topic in ('reply_draft_ready','post_call_review_waiting','tour_feedback_received')),
  source_id text not null,
  source_revision text not null,
  card_item_id text not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now()+interval '24 hours',
  dedupe_key text not null unique
);
create table ops.partner_needed_activation (
  subscription_id uuid primary key references ops.web_push_subscription(subscription_id),
  activated_at timestamptz not null default now()
);
create table ops.partner_needed_seen (
  subscription_id uuid not null references ops.web_push_subscription(subscription_id),
  identity text not null,
  primary key(subscription_id,identity)
);
create table ops.partner_needed_delivery (
  event_id uuid not null references ops.partner_needed_event(event_id),
  subscription_id uuid not null references ops.web_push_subscription(subscription_id),
  state text not null default 'queued' check(state in ('queued','claimed','accepted_by_provider','retry_due','suppressed','expired','failed')),
  attempt_count integer not null default 0 check(attempt_count between 0 and 3),
  next_attempt_at timestamptz not null default now(),
  claim_token uuid,
  claim_until timestamptz,
  last_status integer,
  terminal_reason text,
  primary key(event_id,subscription_id)
);
create index partner_needed_due on ops.partner_needed_delivery(next_attempt_at) where state in ('queued','retry_due','claimed');
create table ops.partner_review_receipt (
  actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  event_id uuid not null references ops.partner_needed_event(event_id),
  acknowledged_at timestamptz not null default now(),
  primary key(actor_id,idempotency_key),
  unique(actor_id,event_id)
);
revoke all on ops.partner_needed_event,ops.partner_needed_activation,ops.partner_needed_seen,ops.partner_needed_delivery,ops.partner_review_receipt from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.partner_needed_sources(p_tenant text,p_actor uuid)
returns table(topic text,source_id text,source_revision text,item_id text,source_version text,activity_at timestamptz,open_url text,deal_id uuid)
language sql stable set search_path=pg_catalog,pg_temp as $$
  select 'reply_draft_ready',p.id::text,'ready', 'email_proposal:'||p.id,
    p.version::text||':'||coalesce((select md5(string_agg(n.id::text,',' order by n.created_at,n.id))
      from ops.notification n where n.recipient_actor=p_actor and n.subject_type='email' and n.subject_ref=p.id::text
      and n.severity<>'completion' and ops.notification_visible(n.id)),md5('')),
    p.created_at,null::text,null::uuid
  from public.email_proposal p join public.activity e on e.id=p.activity_id
  join public.actor a on a.id=p_actor and a.active and a.slug in ('joe','dell') and a.slug=p.owner
  where p_tenant='carr-internal' and p.kind='reply_draft' and p.status='pending'
    and e.email_source->>'mailbox_owner'=a.slug
  union all
  select 'post_call_review_waiting',s.id::text,'review', 'post_call_review:'||s.id,'review',s.started_at,
    '/inbox/card/post_call_review%3A'||s.id,null::uuid
  from public.capture_session s join public.actor a on a.id=p_actor and a.active and a.slug in ('joe','dell')
  where p_tenant='carr-internal' and s.post_call and s.outcome_reviewed_at is null and (
    (s.actor_id=p_actor and exists(select 1 from public.capture_candidate c where c.session_id=s.id and (c.applied_automatically or c.status='pending')))
    or exists(select 1 from public.capture_post_call_candidate c where c.session_id=s.id and c.status='pending'
      and coalesce(c.assignee_slug,(select slug from public.actor where id=s.actor_id))=a.slug))
  union all
  select 'tour_feedback_received',g.id::text,f.revision,'tour_feedback_review:'||g.id||':'||f.revision,
    f.revision,f.latest,'/deals?deal='||t.deal_id||'&focus=tour-feedback&projection='||g.projection_id,t.deal_id
  from ops.tour_share_grant g
  join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
  join ops.tour t on t.organization_tenant_id=p.organization_tenant_id and t.id=p.tour_id
  join public.deal d on d.id=t.deal_id
  join public.client cl on cl.id=d.client_id and cl.merged_into is null
  join public.party party on party.id=cl.party_id and party.merged_into is null and party.deleted_at is null
  join public.actor a on a.id=p_actor and a.active and a.slug in ('joe','dell')
  cross join lateral (select max(event_order)::text revision,max(created_at) latest from ops.tour_share_feedback_event e
    where e.organization_tenant_id=g.organization_tenant_id and e.projection_id=g.projection_id and e.share_grant_id=g.id) f
  where g.organization_tenant_id=p_tenant and g.created_by_actor_id=p_actor::text and f.revision is not null
    and p_tenant='carr-internal' and d.owner=a.slug
    and p.status='published' and t.tour_status not in ('archived','cancelled')
    and (g.permission_scopes ? 'shortlist' or g.permission_scopes ? 'comment')
    and g.status='active' and g.expires_at>now()
    and not exists(select 1 from ops.tour_share_grant newer where newer.organization_tenant_id=p_tenant and newer.rotated_from_grant_id=g.id)
    and not exists(select 1 from ops.tour_share_grant_revocation_receipt r where r.organization_tenant_id=p_tenant and r.share_grant_id=g.id)
    and not exists(select 1 from ops.partner_review_receipt r join ops.partner_needed_event e on e.event_id=r.event_id
      where r.actor_id=p_actor and e.organization_tenant_id=p_tenant and e.source_id=g.id::text and e.source_revision=f.revision and e.topic='tour_feedback_received')
$$;
revoke all on function ops.partner_needed_sources(text,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.partner_needed_events(p_tenant text,p_actor uuid)
returns table(topic text,source_id text,source_revision text,item_id text,source_version text,activity_at timestamptz,open_url text,deal_id uuid)
language sql stable set search_path=pg_catalog,pg_temp as $$
  select s.* from ops.partner_needed_sources(p_tenant,p_actor) s
  where not exists(select 1 from ops.home_inbox_disposition d where d.actor_id=p_actor and d.item_id=s.item_id
    and (d.disposition='settled' or (d.disposition='snoozed' and d.snoozed_until>now())))
    and (s.topic<>'reply_draft_ready' or exists(select 1 from public.email_proposal p
      where p.id::text=s.source_id and (p.deferred_until is null or p.deferred_until<=now())))
$$;
revoke all on function ops.partner_needed_events(text,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.activate_partner_needed_subscription() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  if new.revoked_at is null and (new.expires_at is null or new.expires_at>now()) then
    insert into ops.partner_needed_activation(subscription_id) values(new.subscription_id);
    insert into ops.partner_needed_seen(subscription_id,identity)
      select new.subscription_id,concat_ws(':',e.topic,e.source_id,e.source_revision)
      from ops.partner_needed_sources(new.organization_tenant_id,new.actor_id) e on conflict do nothing;
  end if;
  return new;
end $$;
revoke all on function ops.activate_partner_needed_subscription() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
create trigger partner_needed_subscription_activation after insert on ops.web_push_subscription
  for each row execute function ops.activate_partner_needed_subscription();

create function ops.enqueue_partner_needed(p_tenant text) returns jsonb
language plpgsql set search_path=pg_catalog,pg_temp set statement_timeout='5s' as $$
declare inserted integer;
begin
  if p_tenant<>'carr-internal' or p_tenant is null then raise insufficient_privilege; end if;
  perform pg_advisory_xact_lock(hashtextextended('partner-needed:'||p_tenant,0));
  with activated as (
    insert into ops.partner_needed_activation(subscription_id)
      select s.subscription_id from ops.web_push_subscription s
      where s.organization_tenant_id=p_tenant and s.revoked_at is null and (s.expires_at is null or s.expires_at>now())
      on conflict do nothing returning subscription_id
  )
  insert into ops.partner_needed_seen(subscription_id,identity)
    select a.subscription_id,concat_ws(':',e.topic,e.source_id,e.source_revision)
    from activated a join ops.web_push_subscription s using(subscription_id)
    cross join lateral ops.partner_needed_sources(p_tenant,s.actor_id) e on conflict do nothing;
  with candidates as materialized (
    select s.subscription_id,s.actor_id,e.*,concat_ws(':',e.topic,e.source_id,e.source_revision) identity
    from ops.web_push_subscription s join ops.partner_needed_activation a using(subscription_id)
    cross join lateral ops.partner_needed_events(p_tenant,s.actor_id) e
    where s.organization_tenant_id=p_tenant and s.revoked_at is null and (s.expires_at is null or s.expires_at>now())
      and not exists(select 1 from ops.partner_needed_seen seen where seen.subscription_id=s.subscription_id
        and seen.identity=concat_ws(':',e.topic,e.source_id,e.source_revision))
    order by e.activity_at,e.item_id,s.subscription_id limit 20
  ), events as (
    insert into ops.partner_needed_event(organization_tenant_id,recipient_actor,topic,source_id,source_revision,card_item_id,dedupe_key)
      select distinct p_tenant,c.actor_id,c.topic,c.source_id,c.source_revision,c.item_id,
        concat_ws(':',p_tenant,c.actor_id::text,c.identity) from candidates c
      on conflict(dedupe_key) do update set dedupe_key=excluded.dedupe_key returning *
  ), deliveries as (
    insert into ops.partner_needed_delivery(event_id,subscription_id)
      select e.event_id,c.subscription_id from candidates c join events e
        on e.dedupe_key=concat_ws(':',p_tenant,c.actor_id::text,c.identity)
      where e.expires_at>now() on conflict do nothing returning *
  )
  insert into ops.partner_needed_seen(subscription_id,identity)
    select subscription_id,identity from candidates on conflict do nothing;
  get diagnostics inserted=row_count;
  return jsonb_build_object('ok',true,'processed',inserted);
end $$;
revoke all on function ops.enqueue_partner_needed(text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.read_partner_needed_card(p_event uuid) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare who uuid:=ops.portfolio_writer_actor_id(); tenant text:=nullif(current_setting('carr.organization_tenant_id',true),'');
  e ops.partner_needed_event%rowtype; s record;
begin
  select * into e from ops.partner_needed_event where event_id=p_event and recipient_actor=who and organization_tenant_id=tenant;
  if not found then return jsonb_build_object('ok',true,'state','unavailable'); end if;
  select * into s from ops.partner_needed_events(tenant,who) where item_id=e.card_item_id and source_revision=e.source_revision;
  if not found then return jsonb_build_object('ok',true,'state','resolved'); end if;
  return jsonb_build_object('ok',true,'state','actionable','event_id',e.event_id,'expired',e.expires_at<=now(),
    'item',jsonb_build_object('item_id',s.item_id,'source_id',s.source_id,'source_version',s.source_version,
      'source_type',case s.topic when 'reply_draft_ready' then 'email_proposal' when 'post_call_review_waiting' then 'post_call_review' else 'tour_feedback_review' end,
      'source_state','needs_attention','open_url',s.open_url,'deal_id',s.deal_id,
      'review_target',case s.topic when 'post_call_review_waiting' then jsonb_build_object('kind','call_outcome','session_id',s.source_id)
        when 'tour_feedback_received' then jsonb_build_object('kind','tour_feedback','deal_id',s.deal_id,'grant_id',s.source_id,'revision',s.source_revision)
        else null end));
end $$;
revoke all on function ops.read_partner_needed_card(uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.read_partner_needed_card(uuid) to carr_writer;

create function ops.acknowledge_partner_review(p_event uuid,p_key uuid) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare who uuid:=ops.portfolio_writer_actor_id(); current_card jsonb; prior ops.partner_review_receipt%rowtype;
begin
  if who is null then raise insufficient_privilege; end if;
  if not exists(select 1 from ops.partner_needed_event e where e.event_id=p_event and e.recipient_actor=who
    and e.organization_tenant_id=nullif(current_setting('carr.organization_tenant_id',true),'')) then
    return jsonb_build_object('ok',false,'error','invalid_input'); end if;
  if p_key is null then return jsonb_build_object('ok',false,'error','invalid_input'); end if;
  perform pg_advisory_xact_lock(hashtextextended('partner-review:'||who::text,0));
  select * into prior from ops.partner_review_receipt where actor_id=who and idempotency_key=p_key;
  if found then return jsonb_build_object('ok',prior.event_id=p_event,'error',case when prior.event_id<>p_event then 'key_reuse' end); end if;
  current_card:=ops.read_partner_needed_card(p_event);
  if current_card->>'state'<>'actionable' or current_card#>>'{item,source_type}'<>'tour_feedback_review' then
    return jsonb_build_object('ok',false,'error','invalid_input'); end if;
  insert into ops.partner_review_receipt(actor_id,idempotency_key,event_id) values(who,p_key,p_event) on conflict(actor_id,event_id) do nothing;
  return jsonb_build_object('ok',true,'event_id',p_event,'state','resolved');
end $$;
revoke all on function ops.acknowledge_partner_review(uuid,uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.acknowledge_partner_review(uuid,uuid) to carr_writer;

do $append_grant$
declare contract jsonb; signature text;
begin
  contract:=ops.scac_current_grant_contract();
  foreach signature in array array['ops.read_partner_needed_card(p_event uuid)','ops.acknowledge_partner_review(p_event uuid, p_key uuid)'] loop
    contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||jsonb_build_array(jsonb_build_object(
      'grantee','carr_writer','signature',signature,'grantable',false,'privilege','execute',
      'ingress_key','db-function-acl:'||signature||':carr_writer:execute','ingress_kind','db_function_acl',
      'function_kind','f','security_definer',true,'volatility',case when signature like 'ops.read_%' then 's' else 'v' end,
      'parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp'))));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;

create function ops.partner_needed_exact_item(p_item text) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
  select jsonb_build_object('item_id',s.item_id,'source_id',s.source_id,'source_version',s.source_version,
    'source_type',case s.topic when 'reply_draft_ready' then 'email_proposal' when 'post_call_review_waiting' then 'post_call_review' else 'tour_feedback_review' end,
    'source_state','needs_attention','open_url',s.open_url,'deal_id',s.deal_id)
  from ops.partner_needed_events(nullif(current_setting('carr.organization_tenant_id',true),''),ops.portfolio_writer_actor_id()) s
  where s.item_id=p_item
$$;
revoke all on function ops.partner_needed_exact_item(text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.partner_needed_exact_item(text) to carr_writer;

create function ops.home_inbox_sources_v4(p_actor uuid)
returns table(item_id text,source_type text,source_id text,source_version text,material_version text,
  source_state text,title text,activity_at timestamptz,hold_until date,unseen boolean,
  context_label text,due_on date,open_url text,kind_label text,merged_source_ids jsonb,action_id uuid,action_version integer,deal_id uuid)
language sql stable set search_path=pg_catalog,pg_temp as $$
  select * from ops.home_inbox_sources_v3(p_actor)
  union all
  select s.item_id,case s.topic when 'post_call_review_waiting' then 'post_call_review' else 'tour_feedback_review' end,
    s.source_id,s.source_version,s.source_revision,'needs_attention',
    case s.topic when 'post_call_review_waiting' then 'Review call outcome' else 'Review tour feedback' end,
    s.activity_at,null,false,null,null,s.open_url,
    case s.topic when 'post_call_review_waiting' then 'Call review' else 'Tour feedback review' end,
    jsonb_build_array(s.source_id),null,null,s.deal_id
  from ops.partner_needed_sources('carr-internal',p_actor) s where s.topic<>'reply_draft_ready'
$$;
revoke all on function ops.home_inbox_sources_v4(uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;

do $extend_projector$
declare definition text;
begin
  select pg_get_functiondef('ops.home_inbox_project_items(uuid,integer)'::regprocedure) into definition;
  execute replace(definition,'union all select * from ops.home_inbox_sources_v3(p_actor) where p_version=3',
    'union all select * from ops.home_inbox_sources_v3(p_actor) where p_version=3 union all select * from ops.home_inbox_sources_v4(p_actor) where p_version=4');
end $extend_projector$;
create function ops.home_inbox_items_v4(p_actor uuid) returns setof jsonb
language sql stable set search_path=pg_catalog,pg_temp as $$ select * from ops.home_inbox_project_items(p_actor,4) $$;
revoke all on function ops.home_inbox_items_v4(uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
do $extend_reader$
declare definition text;
begin
  select pg_get_functiondef('ops.read_home_inbox_v3()'::regprocedure) into definition;
  execute replace(replace(replace(definition,'FUNCTION ops.read_home_inbox_v3()', 'FUNCTION ops.read_home_inbox_v4()'),
    'ops.home_inbox_items_v3(a)','ops.home_inbox_items_v4(a)'),'''home-inbox.v3''','''home-inbox.v4''');
end $extend_reader$;
revoke all on function ops.read_home_inbox_v4() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.read_home_inbox_v4() to carr_writer;

do $extend_disposition$
declare definition text;
begin
  select pg_get_functiondef('ops.change_home_inbox(uuid,text,jsonb,timestamptz)'::regprocedure) into definition;
  definition:=replace(definition,'ops.home_inbox_items_v3(a)','ops.home_inbox_items_v4(a)');
  execute replace(definition, 'entry->>''item_id'' like ''fact_review:%'' or entry->>''item_id'' like ''tour_feedback:%''',
    'entry->>''item_id'' like ''fact_review:%'' or entry->>''item_id'' like ''tour_feedback:%'' or entry->>''item_id'' like ''post_call_review:%'' or entry->>''item_id'' like ''tour_feedback_review:%''');
end $extend_disposition$;

do $append_exact_grant$
declare contract jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||
    '[{"grantee":"carr_writer","signature":"ops.partner_needed_exact_item(p_item text)","grantable":false,"privilege":"execute","ingress_key":"db-function-acl:ops.partner_needed_exact_item(p_item text):carr_writer:execute","ingress_kind":"db_function_acl","function_kind":"f","security_definer":true,"volatility":"s","parallel":"u","config":["search_path=pg_catalog, pg_temp"]}]'::jsonb);
  contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||
    '[{"grantee":"carr_writer","signature":"ops.read_home_inbox_v4()","grantable":false,"privilege":"execute","ingress_key":"db-function-acl:ops.read_home_inbox_v4():carr_writer:execute","ingress_kind":"db_function_acl","function_kind":"f","security_definer":true,"volatility":"s","parallel":"u","config":["search_path=pg_catalog, pg_temp"]}]'::jsonb);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_exact_grant$;
