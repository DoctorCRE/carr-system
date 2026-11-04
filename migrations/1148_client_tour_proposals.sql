-- rollback: forward-only; retain proposal decisions and acceptance receipts.
-- lock-review: new empty tables; acceptance locks the existing Tour row.
create table ops.client_tour_proposal (
 id uuid primary key default gen_random_uuid(),
 portal_slug text not null references ops.client_portal(slug),
 deal_id uuid not null references public.deal(id),
 tour_id uuid not null references ops.tour(id),
 portal_revision bigint not null check(portal_revision>0),
 property_ids jsonb not null check(jsonb_typeof(property_ids)='array' and jsonb_array_length(property_ids)<=100),
 base_property_ids jsonb not null,
 choices jsonb not null default '{}',
 state text not null default 'pending' check(state in ('pending','partially_accepted','accepted','superseded','dismissed')),
 pulled_at timestamptz not null default now(),
 unique(portal_slug,portal_revision)
);
create index client_tour_proposal_deal on ops.client_tour_proposal(deal_id,pulled_at desc,id);
create table ops.client_tour_proposal_receipt (
 actor text not null, idempotency_key text not null,
 request jsonb not null, receipt jsonb not null,
 primary key(actor,idempotency_key)
);
revoke all on ops.client_tour_proposal,ops.client_tour_proposal_receipt from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.client_tour_proposal_source(p_deal uuid,p_tour uuid,p_actor text) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
 select jsonb_build_object('portal_slug',p.slug)
 from ops.client_portal p join public.deal d on d.id=p.deal_id and d.client_id=p.client_id
 join ops.tour t on t.deal_id=d.id and t.id=p_tour and t.organization_tenant_id='carr-internal'
 where p.deal_id=p_deal and p.disabled_at is null
 and p_actor in ('joe','dell') and ops.tour_deal_visible('carr-internal',p_deal,p_actor)
$$;

create function ops.pull_client_tour_proposal(p_deal uuid,p_tour uuid,p_slug text,p_revision bigint,p_ids jsonb,p_actor text) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare existing ops.client_tour_proposal%rowtype; latest bigint; result jsonb; base jsonb;
begin
 perform 1 from ops.client_portal where slug=p_slug for update;
 if ops.client_tour_proposal_source(p_deal,p_tour,p_actor)->>'portal_slug' is distinct from p_slug
 then raise exception 'proposal_denied'; end if;
 if p_revision is null or p_revision<=0 or jsonb_typeof(p_ids) is distinct from 'array' or jsonb_array_length(p_ids)>100
 then raise exception 'proposal_invalid'; end if;
 if exists(select 1 from jsonb_array_elements_text(p_ids) x(id)
   left join ops.tour_property p on p.id=x.id::uuid and p.organization_tenant_id='carr-internal' and p.property_status='active'
   where p.id is null) then raise exception 'proposal_unresolved_property'; end if;
 if (select count(distinct value) from jsonb_array_elements_text(p_ids))<>jsonb_array_length(p_ids)
 then raise exception 'proposal_invalid'; end if;
 select * into existing from ops.client_tour_proposal where portal_slug=p_slug and portal_revision=p_revision;
 if existing.id is not null then
   if existing.tour_id<>p_tour or existing.property_ids<>p_ids then raise exception 'proposal_revision_conflict'; end if;
   return jsonb_build_object('id',existing.id,'state',existing.state,'portal_revision',existing.portal_revision);
 end if;
 select max(portal_revision) into latest from ops.client_tour_proposal where portal_slug=p_slug;
 if p_revision<=latest then raise exception 'proposal_stale_revision'; end if;
 if (select count(*) from ops.client_tour_proposal where portal_slug=p_slug)>=1000 then raise exception 'proposal_capacity'; end if;
 select property_ids into base from ops.tour_selection_cart_version where organization_tenant_id='carr-internal' and tour_id=p_tour order by selection_version desc limit 1;
 update ops.client_tour_proposal set state='superseded' where portal_slug=p_slug and state in ('pending','partially_accepted');
 insert into ops.client_tour_proposal(portal_slug,deal_id,tour_id,portal_revision,property_ids,base_property_ids)
 values(p_slug,p_deal,p_tour,p_revision,p_ids,coalesce(base,'[]'))
 returning jsonb_build_object('id',id,'state',state,'portal_revision',portal_revision) into result;
 return result;
end $$;

create function ops.list_client_tour_proposals(p_deal uuid,p_actor text) returns jsonb
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
 select jsonb_build_object('pending_count',(select count(*) from ops.client_tour_proposal where deal_id=p_deal and state in ('pending','partially_accepted')),
 'proposals',coalesce((select jsonb_agg(item order by pulled_at desc,id) from (
 select p.id,p.pulled_at,jsonb_build_object('id',p.id,'deal_id',p.deal_id,'tour_id',p.tour_id,'portal_revision',p.portal_revision,
 'state',p.state,'items',(select coalesce(jsonb_agg(jsonb_build_object('carr_property_id',value)),'[]') from jsonb_array_elements(p.property_ids)),
 'choices',p.choices,'expected_selection_version_id',cart.id,
 'difference',jsonb_build_object('add',(select coalesce(jsonb_agg(value),'[]') from jsonb_array_elements(p.property_ids) where not coalesce(cart.property_ids,'[]') @> jsonb_build_array(value)),
 'remove',(select coalesce(jsonb_agg(value),'[]') from jsonb_array_elements(coalesce(cart.property_ids,'[]')) where not p.property_ids @> jsonb_build_array(value)))) item
 from ops.client_tour_proposal p left join lateral (select id,property_ids from ops.tour_selection_cart_version where organization_tenant_id='carr-internal' and tour_id=p.tour_id order by selection_version desc limit 1) cart on true
 where p.deal_id=p_deal order by p.pulled_at desc,p.id limit 50) rows),'[]'))
 where p_actor in ('joe','dell') and ops.tour_deal_visible('carr-internal',p_deal,p_actor)
$$;

create function ops.accept_client_tour_proposal(p_deal uuid,p_id uuid,p_args jsonb,p_actor text) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,pg_temp as $$
declare p ops.client_tour_proposal%rowtype; cart ops.tour_selection_cart_version%rowtype; saved ops.client_tour_proposal_receipt%rowtype;
 request jsonb; result jsonb; ids jsonb; version_id uuid; decisions jsonb; next_state text; key text; mode text;
begin
 if p_actor is null or p_actor not in ('joe','dell') or not ops.tour_deal_visible('carr-internal',p_deal,p_actor) then raise exception 'proposal_denied'; end if;
 key:=p_args->>'idempotency_key'; mode:=p_args->>'mode';
 if key is null or length(key) not between 1 and 200 or mode is null or mode not in ('use_theirs','per_property','keep_mine') then raise exception 'proposal_invalid'; end if;
 request:=jsonb_build_object('deal_id',p_deal,'proposal_id',p_id,'args',p_args);
 perform pg_advisory_xact_lock(hashtextextended('proposal-key:'||p_actor||':'||key,0));
 select * into saved from ops.client_tour_proposal_receipt where actor=p_actor and idempotency_key=key;
 if saved.actor is not null then
   if saved.request<>request then raise exception 'proposal_key_reuse'; end if;
   return saved.receipt;
 end if;
 select * into p from ops.client_tour_proposal where id=p_id and deal_id=p_deal;
 if p.id is null then raise exception 'proposal_not_found'; end if;
 perform 1 from ops.client_portal where slug=p.portal_slug for update;
 perform 1 from ops.tour where id=p.tour_id for update;
 select * into p from ops.client_tour_proposal where id=p_id for update;
 if ops.client_tour_proposal_source(p_deal,p.tour_id,p_actor) is null then raise exception 'proposal_denied'; end if;
 if p.state not in ('pending','partially_accepted') then raise exception 'proposal_not_pending'; end if;
 select * into cart from ops.tour_selection_cart_version where organization_tenant_id='carr-internal' and tour_id=p.tour_id order by selection_version desc limit 1;
 version_id:=cart.id;
 if mode='keep_mine' then next_state:='dismissed';
 else
   if (p_args->>'expected_selection_version_id')::uuid is distinct from cart.id then raise exception 'proposal_shortlist_conflict'; end if;
   if mode='use_theirs' then ids:=p.property_ids; next_state:='accepted';
   else
     decisions:=p_args->'choices';
     if jsonb_typeof(decisions) is distinct from 'object' or decisions='{}' or (select count(*) from jsonb_object_keys(decisions))>100 then raise exception 'proposal_invalid_choices'; end if;
     if exists(select 1 from jsonb_each(decisions) x where jsonb_typeof(x.value)<>'boolean' or not (p.property_ids||p.base_property_ids) @> jsonb_build_array(x.key)) then raise exception 'proposal_invalid_choices'; end if;
     decisions:=p.choices||decisions;
     select coalesce(jsonb_agg(to_jsonb(id) order by id),'[]') into ids from (
       select distinct value id from jsonb_array_elements_text(coalesce(cart.property_ids,'[]')||p.property_ids||p.base_property_ids)
       where case when (p_args->'choices') ? value then (p_args->'choices'->>value)::boolean else coalesce(cart.property_ids,'[]') @> jsonb_build_array(value) end) chosen;
     next_state:=case when exists(select 1 from jsonb_array_elements_text(p.property_ids||p.base_property_ids) x(id)
       where (p.property_ids @> jsonb_build_array(id))<>(p.base_property_ids @> jsonb_build_array(id)) and not decisions ? id)
       then 'partially_accepted' else 'accepted' end;
     update ops.client_tour_proposal set choices=decisions where id=p.id;
   end if;
   if exists(select 1 from jsonb_array_elements_text(ids) x(id) left join ops.tour_property t on t.id=x.id::uuid and t.organization_tenant_id='carr-internal' and t.property_status='active' where t.id is null) then raise exception 'proposal_unresolved_property'; end if;
   if ids is distinct from coalesce(cart.property_ids,'[]') then
     version_id:=ops.append_tour_selection_cart_version('carr-internal',p.tour_id,cart.id,ids,coalesce(cart.selection_version,0),
       'sha256:'||encode(public.digest(ids::text,'sha256'),'hex'));
     if version_id is null then raise exception 'proposal_denied'; end if;
   end if;
 end if;
 update ops.client_tour_proposal set state=next_state where id=p.id;
 result:=jsonb_build_object('proposal_id',p.id,'state',next_state,'actor',p_actor,'selection_version_id',version_id,'portal_revision',p.portal_revision);
 insert into ops.client_tour_proposal_receipt values(p_actor,key,request,result);
 return result;
end $$;

create function ops.pending_client_tour_proposals(p_actor text)
returns table(deal_id uuid,deal_name text,properties_changed integer)
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
 with bounded_deals as materialized (
   select d.id,d.name from public.deal d
   join public.client client on client.id=d.client_id and client.merged_into is null
   where p_actor in ('joe','dell') and d.owner=p_actor and exists (
     select 1 from ops.client_tour_proposal p
     join ops.client_portal portal on portal.slug=p.portal_slug and portal.deal_id=d.id
       and portal.client_id=d.client_id and portal.disabled_at is null
     join ops.tour t on t.id=p.tour_id and t.deal_id=d.id and t.organization_tenant_id='carr-internal'
     where p.deal_id=d.id and p.state in ('pending','partially_accepted'))
   order by d.name,d.id limit 21
 )
 select d.id,d.name,count(distinct changed.id)::integer
 from bounded_deals d
 join public.deal current_deal on current_deal.id=d.id
 join ops.client_tour_proposal p on p.deal_id=d.id and p.state in ('pending','partially_accepted')
 join ops.client_portal portal on portal.slug=p.portal_slug and portal.deal_id=d.id
   and portal.client_id=current_deal.client_id and portal.disabled_at is null
 join ops.tour t on t.id=p.tour_id and t.deal_id=d.id and t.organization_tenant_id='carr-internal'
 left join lateral (
   select property_ids from ops.tour_selection_cart_version
   where organization_tenant_id='carr-internal' and tour_id=p.tour_id order by selection_version desc limit 1
 ) cart on true
 left join lateral (
   select value id from jsonb_array_elements_text(p.property_ids)
   where not coalesce(cart.property_ids,'[]') @> jsonb_build_array(value)
   union
   select value id from jsonb_array_elements_text(coalesce(cart.property_ids,'[]'))
   where not p.property_ids @> jsonb_build_array(value)
 ) changed on true
 group by d.id,d.name order by d.name,d.id
$$;

do $acl$
declare f record; contract jsonb:=ops.scac_current_grant_contract(); signature text; grantee text;
begin
 for f in select p.oid,p.provolatile from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where n.nspname='ops' and p.proname in ('client_tour_proposal_source','pull_client_tour_proposal','list_client_tour_proposals','accept_client_tour_proposal','pending_client_tour_proposals') loop
   signature:='ops.'||(select proname from pg_proc where oid=f.oid)||'('||pg_get_function_identity_arguments(f.oid)||')';
   execute format('revoke all on function %s from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader',signature);
   grantee:=case when (select proname from pg_proc where oid=f.oid)='pending_client_tour_proposals' then 'carr_reader' else 'carr_writer' end;
   execute format('grant execute on function %s to %I',signature,grantee);
   contract:=jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(jsonb_build_object(
    'config',jsonb_build_array('search_path=pg_catalog, pg_temp'),'grantee',grantee,'parallel','u','grantable',false,'privilege','execute',
    'signature',signature,'volatility',f.provolatile,'ingress_key','db-function-acl:'||signature||':'||grantee||':execute',
    'ingress_kind','db_function_acl','function_kind','f','security_definer',true)));
 end loop;
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $acl$;
