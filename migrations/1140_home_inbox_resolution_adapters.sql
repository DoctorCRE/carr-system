-- rollback: forward-only; restore the previous read handler before retiring v3 functions.
-- expand-contract: expand; v1 and v2 projections remain unchanged.
-- lock-review: function metadata only; no domain writes or table locks.
set local lock_timeout = '5s';
set local statement_timeout = '30s';

create function ops.home_inbox_sources_v3(p_actor uuid)
returns table(item_id text,source_type text,source_id text,source_version text,material_version text,
  source_state text,title text,activity_at timestamptz,hold_until date,unseen boolean,
  context_label text,due_on date,open_url text,kind_label text,merged_source_ids jsonb,action_id uuid,action_version integer,deal_id uuid)
language sql stable set search_path=pg_catalog,pg_temp as $$
  with fields as (
    select o.entity_ref,o.fact_key,max(o.observed_at) newest
    from (
      select organization_tenant_id,entity_ref,fact_key,observed_at from ops.fact_observation
      union all
      select j.organization_tenant_id,j.entity_ref,a->>'fact_key',j.recorded_at
        from ops.deal_extraction_job j cross join lateral jsonb_array_elements(j.receipt->'abstentions') a
    ) o join public.deal d on d.id::text=o.entity_ref
    join public.actor a on a.id=p_actor and a.slug=d.owner
    where o.organization_tenant_id='carr-internal'
    group by o.entity_ref,o.fact_key
  ), reviews as (
    select f.*,ops.fact_review_field('carr-internal',f.entity_ref,f.fact_key) field,
      (select max(decided_at) from ops.fact_review_decision r where r.organization_tenant_id='carr-internal'
        and r.entity_ref=f.entity_ref and r.fact_key=f.fact_key) decided_at
    from fields f
  ), grants as (
    select g.*,e.latest,e.revision from ops.tour_share_grant g
    left join lateral (
      select max(created_at) latest, max(event_order)::text revision
      from ops.tour_share_feedback_event e where e.organization_tenant_id=g.organization_tenant_id
        and e.projection_id=g.projection_id and e.share_grant_id=g.id
    ) e on true
    where g.organization_tenant_id='carr-internal' and g.created_by_actor_id=p_actor::text
      and (g.permission_scopes ? 'shortlist' or g.permission_scopes ? 'comment')
      and g.status='active' and g.expires_at>now()
      and not exists(select 1 from ops.tour_share_grant newer
        where newer.organization_tenant_id=g.organization_tenant_id and newer.rotated_from_grant_id=g.id)
      and not exists(select 1 from ops.tour_share_grant_revocation_receipt r
        where r.organization_tenant_id=g.organization_tenant_id and r.share_grant_id=g.id)
  )
  select * from ops.home_inbox_sources_v2(p_actor)
  union all
  select 'fact_review:'||jsonb_build_array(r.entity_ref,r.fact_key)::text,'fact_review',
    jsonb_build_array(r.entity_ref,r.fact_key)::text,r.field->>'review_version',
    md5(jsonb_build_array(r.entity_ref,r.fact_key,jsonb_path_query_array(r.field,'$.observations[*].id'),r.field->'extraction_job_ids')::text),
    case when (r.field->>'pending')::boolean then 'needs_attention' else 'done' end,
    'Review '||r.fact_key,greatest(r.newest,r.decided_at,(r.field#>>'{canonical_fact,promoted_at}')::timestamptz),null,false,d.name,null,
    '/deals?deal='||d.id||'&focus=fact-review','Fact review','[]'::jsonb,null,null,d.id
    from reviews r join public.deal d on d.id::text=r.entity_ref
    where ((r.field->>'pending')::boolean or r.decided_at>=now()-interval '7 days'
      or (r.field#>>'{canonical_fact,basis}'='human_review'
        and (r.field#>>'{canonical_fact,promoted_at}')::timestamptz>=now()-interval '7 days'))
  union all
  select 'tour_feedback:'||g.id,'tour_feedback',g.id::text,
    md5(jsonb_build_array(g.grant_version,g.revision)::text),md5(g.id::text),
    case when g.latest is null then 'needs_attention' else 'done' end,
    'Tour feedback',coalesce(g.latest,g.created_at),null,false,'Tour feedback',null,
    '/tours?projection='||g.projection_id,'Tour feedback',jsonb_build_array(g.id::text),null,null,null
    from grants g where g.latest is null or g.latest>=now()-interval '7 days'
$$;
revoke all on function ops.home_inbox_sources_v3(uuid) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.home_inbox_project_items(p_actor uuid,p_version integer)
returns setof jsonb language sql stable set search_path=pg_catalog,pg_temp as $$
  with observed as (
    select s.*,d.version disposition_version,d.explicit_unsettle,d.snoozed_until,
      coalesce(d.disposition,'inbox') stored_disposition,
      coalesce(d.material_version<>s.material_version or
        (s.source_state in ('needs_attention','failed') and d.source_version<>s.source_version),false) new_activity
    from (select * from ops.home_inbox_sources_v2(p_actor) where p_version=2
      union all select * from ops.home_inbox_sources_v3(p_actor) where p_version=3) s
    left join lateral (
      select d.disposition,d.snoozed_until,d.version,d.explicit_unsettle,
        case when d.item_id=s.item_id then d.source_version else d.v2_source_version end source_version,
        case when d.item_id=s.item_id then d.material_version else d.v2_material_version end material_version
      from ops.home_inbox_disposition d where d.actor_id=p_actor
        and (d.item_id=s.item_id or d.v2_item_id=s.item_id)
      order by (d.item_id=s.item_id) desc,d.updated_at desc,d.item_id limit 1
    ) d on true
  ), placed as (
    select *,case when stored_disposition='snoozed' and (snoozed_until<=now() or new_activity)
      then 'inbox' else stored_disposition end disposition from observed
  )
  select jsonb_build_object('item_id',item_id,'source_type',source_type,'source_id',source_id,
    'source_version',source_version,'source_state',source_state,'title',title,'activity_at',activity_at,
    'context_label',context_label,'due_on',due_on,'open_url',open_url,'kind_label',kind_label,
    'action_id',action_id,'action_version',action_version,'deal_id',deal_id,'merged_source_ids',merged_source_ids,'hold_until',hold_until,'unseen',unseen,'disposition',disposition,
    'snoozed_until',case when disposition='snoozed' then snoozed_until end,
    'explicit_unsettle',coalesce(explicit_unsettle,false) and not new_activity,
    'section',case when source_state='done' then 'done' when hold_until>(now() at time zone 'America/Chicago')::date then 'held' when disposition<>'inbox' then disposition when source_state='working' then 'working' else 'inbox' end,
    'base_version',md5(jsonb_build_array(source_version,coalesce(disposition_version,0),disposition)::text))
    from placed order by (source_state='done'),activity_at desc,item_id limit 1001
$$;
revoke all on function ops.home_inbox_project_items(uuid,integer) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create or replace function ops.home_inbox_items_v2(p_actor uuid)
returns setof jsonb language sql stable set search_path=pg_catalog,pg_temp as $$
  select * from ops.home_inbox_project_items(p_actor,2)
$$;
create function ops.home_inbox_items_v3(p_actor uuid)
returns setof jsonb language sql stable set search_path=pg_catalog,pg_temp as $$
  select * from ops.home_inbox_project_items(p_actor,3)
$$;
revoke all on function ops.home_inbox_items_v3(uuid) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create or replace function ops.read_home_inbox_v3() returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); items jsonb;
begin
  if a is null then raise insufficient_privilege; end if;
  select coalesce(jsonb_agg(i order by (i->>'source_state'='done'),(i->>'activity_at')::timestamptz desc,i->>'item_id'),'[]') into items from ops.home_inbox_items_v3(a) i;
  return jsonb_build_object('ok',true,'schema_version','home-inbox.v3','items',(select coalesce(jsonb_agg(value order by ord),'[]') from jsonb_array_elements(items) with ordinality e(value,ord) where ord<=1000),
    'truncated',jsonb_array_length(items)>1000 or exists (
      select 1 from ops.notification n where n.recipient_actor=a
        and ops.notification_visible(n.id)
        offset 10000 limit 1),'limit',1000,
    'working_collapsed',true,'auto_settle_enabled',false);
end $$;
revoke all on function ops.read_home_inbox_v3() from public,carr_reader;
grant execute on function ops.read_home_inbox_v3() to carr_writer;

create or replace function ops.change_home_inbox(p_key uuid,p_command text,p_items jsonb,p_until timestamptz)
returns jsonb language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); request jsonb; prior ops.home_inbox_write%rowtype;
  entry jsonb; current_item jsonb; result jsonb; results jsonb:='[]'; source_material text;
begin
  if a is null then raise insufficient_privilege; end if;
  if p_key is null or p_command not in ('snooze','settle','unsettle') or p_command is null
    or jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items) not between 1 and 100
    or (p_command='snooze' and p_until is null) or (p_command<>'snooze' and p_until is not null) then
    return jsonb_build_object('ok',false,'error','invalid_inbox_command');
  end if;
  request:=jsonb_build_array(p_command,p_items,p_until);
  perform pg_advisory_xact_lock(hashtextextended('home-inbox:'||a::text,0));
  select * into prior from ops.home_inbox_write where actor_id=a and idempotency_key=p_key;
  if found then
    if prior.request<>request then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return prior.result;
  end if;
  if p_command='snooze' and (p_until<=now() or not isfinite(p_until)) then
    return jsonb_build_object('ok',false,'error','invalid_snooze_time');
  end if;
  if exists(select 1 from jsonb_array_elements(p_items) i group by i->>'item_id' having count(*)>1) then
    return jsonb_build_object('ok',false,'error','duplicate_inbox_item');
  end if;
  for entry in select value from jsonb_array_elements(p_items) loop
    select i,s.material_version into current_item,source_material
      from ops.home_inbox_items(a) i
      join ops.home_inbox_sources(a) s on s.item_id=i->>'item_id'
      where i->>'item_id'=entry->>'item_id';
    if entry->>'item_id' like 'fact_review:%' or entry->>'item_id' like 'tour_feedback:%' then
      select i,s.material_version into current_item,source_material
        from ops.home_inbox_items_v3(a) i join ops.home_inbox_sources_v3(a) s on s.item_id=i->>'item_id'
        where i->>'item_id'=entry->>'item_id';
    end if;
    if current_item is null or current_item->>'source_type'='overflow' then
      result:=jsonb_build_object('ok',false,'error','inbox_item_not_found');
    elsif entry->>'base_version' is distinct from current_item->>'base_version' then
      result:=jsonb_build_object('ok',false,'error','version_conflict','current_version',current_item->>'base_version');
    else
      insert into ops.home_inbox_disposition(actor_id,item_id,disposition,snoozed_until,source_version,material_version,explicit_unsettle)
        values(a,entry->>'item_id',case p_command when 'snooze' then 'snoozed' when 'settle' then 'settled' else 'inbox' end,
          p_until,current_item->>'source_version',source_material,p_command='unsettle')
      on conflict(actor_id,item_id) do update set disposition=excluded.disposition,snoozed_until=excluded.snoozed_until,
        source_version=excluded.source_version,material_version=excluded.material_version,
        explicit_unsettle=excluded.explicit_unsettle,version=ops.home_inbox_disposition.version+1,updated_at=now();
      select jsonb_build_object('ok',true,'item',i) into result from ops.home_inbox_items(a) i where i->>'item_id'=entry->>'item_id';
      if entry->>'item_id' like 'fact_review:%' or entry->>'item_id' like 'tour_feedback:%' then
        select jsonb_build_object('ok',true,'item',i) into result from ops.home_inbox_items_v3(a) i where i->>'item_id'=entry->>'item_id';
      end if;
    end if;
    results:=results||jsonb_build_array(result||jsonb_build_object('item_id',entry->>'item_id'));
  end loop;
  result:=jsonb_build_object('ok',true,'schema_version','home-inbox-command.v1','results',results);
  insert into ops.home_inbox_write(actor_id,idempotency_key,request,result) values(a,p_key,request,result);
  return result;
end $$;
revoke all on function ops.change_home_inbox(uuid,text,jsonb,timestamptz) from public,carr_reader;
grant execute on function ops.change_home_inbox(uuid,text,jsonb,timestamptz) to carr_writer;

do $append_grant$
declare contract jsonb;
begin
  contract:=ops.scac_current_grant_contract();
  contract:=jsonb_set(contract,'{secdef_execute}',coalesce(contract->'secdef_execute','[]'::jsonb)||
    '[{"grantee":"carr_writer","signature":"ops.read_home_inbox_v3()","grantable":false,"privilege":"execute","ingress_key":"db-function-acl:ops.read_home_inbox_v3():carr_writer:execute","ingress_kind":"db_function_acl","function_kind":"f","security_definer":true,"volatility":"s","parallel":"u","config":["search_path=pg_catalog, pg_temp"]}]'::jsonb);
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
