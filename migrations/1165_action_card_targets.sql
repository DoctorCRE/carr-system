-- rollback: revoke and drop ops.resolve_action_card(uuid,text,uuid), then remove its grant contract entry.
-- expand-contract: additive exact-card read; existing v1 and source authority remain unchanged.
-- lock-review: function and grant metadata only; no business rows rewritten.
set local lock_timeout='5s';
set local statement_timeout='30s';

create function ops.resolve_action_card(p_actor uuid,p_kind text,p_id uuid)
returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,pg_temp as $$
declare target jsonb; source record;
begin
  if not exists(select 1 from public.actor where id=p_actor and active and slug in ('joe','dell')) then return null; end if;
  if p_kind='post_call_review' then
    select s.id,s.post_call,s.outcome_reviewed_at,s.started_at into source
      from public.capture_session s where s.id=p_id and s.actor_id=p_actor;
    if not found then return null; end if;
    target:=jsonb_build_object('session_id',source.id,'started_at',source.started_at,
      'reviewed',source.outcome_reviewed_at is not null,
      'candidates',coalesce((select jsonb_agg(jsonb_build_object('id',c.id,'kind',c.kind,'status',c.status,
        'deal_id',c.deal_id,'assignee',c.assignee_slug,'description',c.action_description,
        'due_on',c.due_on,'subject',c.email_subject,'evidence_quote',c.evidence_quote) order by c.id)
        from public.capture_post_call_candidate c where c.session_id=source.id),'[]'::jsonb),
      'changes',coalesce((select jsonb_agg(jsonb_build_object('id',c.id,'kind',c.kind,'status',c.status,
        'payload',c.payload,'evidence_quote',c.evidence_quote,'applied_automatically',c.applied_automatically,
        'undone_at',c.undone_at) order by c.id) from public.capture_candidate c
        where c.session_id=source.id),'[]'::jsonb));
    return jsonb_build_object('detail',target,'source_version',md5(target::text),
      'state',case when not source.post_call then 'retired'
        when source.outcome_reviewed_at is not null or not (
          exists(select 1 from public.capture_candidate c where c.session_id=source.id and (c.status='pending' or c.applied_automatically))
          or exists(select 1 from public.capture_post_call_candidate c where c.session_id=source.id and c.status='pending'))
        then 'resolved' else 'pending_review' end);
  elsif p_kind='feedback_event' then
    select e.*,g.status as grant_status,g.expires_at,
      exists(select 1 from ops.tour_share_grant_revocation_receipt r where r.share_grant_id=g.id
        and r.organization_tenant_id=g.organization_tenant_id) as revoked
      into source from ops.tour_share_feedback_event e join ops.tour_share_grant g
        on g.id=e.share_grant_id and g.organization_tenant_id=e.organization_tenant_id and g.projection_id=e.projection_id
      where e.id=p_id and g.created_by_actor_id=p_actor::text and e.organization_tenant_id='carr-internal';
    if not found then return null; end if;
    target:=jsonb_build_object('event_id',source.id,'projection_id',source.projection_id,
      'share_grant_id',source.share_grant_id,'property_id',source.property_id,'action',source.action,
      'shortlisted',source.shortlisted,'comment',source.comment,'created_at',source.created_at);
    return jsonb_build_object('detail',target,'source_version',source.event_order::text,
      'state',case when source.grant_status<>'active' or source.revoked or source.expires_at<=now() then 'retired' else 'resolved' end);
  end if;
  return null;
end $$;
revoke all on function ops.resolve_action_card(uuid,text,uuid) from public;
grant execute on function ops.resolve_action_card(uuid,text,uuid) to carr_writer;

do $grant_contract$
declare contract jsonb; signature text:='ops.resolve_action_card(p_actor uuid, p_kind text, p_id uuid)';
begin
  contract:=ops.scac_current_grant_contract();
  contract:=jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(jsonb_build_object(
    'grantee','carr_writer','signature',signature,'grantable',false,'privilege','execute',
    'ingress_key','db-function-acl:'||signature||':carr_writer:execute',
    'ingress_kind','db_function_acl','function_kind','f','security_definer',true,
    'volatility','s','parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp'))));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
    'select '||quote_literal(contract::text)||'::jsonb');
end $grant_contract$;

