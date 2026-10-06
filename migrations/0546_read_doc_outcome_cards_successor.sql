-- WR-000133 / B09: bounded, actor- and tenant-scoped outcome-card read.
-- This is a projection only. It creates no writer, dispatcher, retry, task, or
-- native-session authority.  Missing canonical joins remain explicitly unavailable.
create or replace function ops.read_doc_outcome_cards_successor(p_cursor text, p_limit integer)
returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,ops,public
as $b09$
declare actor_id uuid; actor_slug text; tenant text; page_limit integer;
  as_of timestamptz:=statement_timestamp(); scope_digest text; cursor jsonb;
  after_updated timestamptz; after_id uuid; result jsonb;
begin
  actor_id:=ops.portfolio_writer_actor_id();
  select slug into actor_slug from public.actor where id=actor_id and active;
  tenant:=nullif(current_setting('carr.organization_tenant_id',true),'');
  if actor_slug is null or tenant is null then
    return jsonb_build_object('ok',false,'reason_id','doc_outcome_actor_context_unavailable');
  end if;
  page_limit:=least(greatest(coalesce(p_limit,25),1),50);
  scope_digest:='sha256:'||encode(public.digest(convert_to(actor_id::text||E'\n'||tenant,'UTF8'),'sha256'),'hex');
  if p_cursor is not null then
    begin
      cursor:=convert_from(decode(p_cursor,'base64'),'UTF8')::jsonb;
      if jsonb_typeof(cursor)<>'object' or cursor->>'v'<>'2'
         or cursor->>'s'<>scope_digest then raise exception 'invalid cursor'; end if;
      as_of:=(cursor->>'a')::timestamptz; after_updated:=(cursor->>'u')::timestamptz;
      after_id:=(cursor->>'i')::uuid;
      if as_of>statement_timestamp() or as_of<statement_timestamp()-interval '24 hours' then raise exception 'stale cursor'; end if;
    exception when others then return jsonb_build_object('ok',false,'reason_id','doc_outcome_cursor_invalid');
    end;
  end if;
  with visible as (
    select w.*, greatest(w.updated_at,coalesce(s.updated_at,w.updated_at),coalesce(j.updated_at,w.updated_at),
      coalesce(e.issued_at,w.updated_at),coalesce(e.created_at,w.updated_at),coalesce(a.started_at,w.updated_at)) observed_at,
      s.id session_id,s.state session_state,s.updated_at session_updated_at,
      j.id job_id,j.state job_state,a.id job_attempt_id,e.id envelope_id,e.envelope,
      e.envelope#>>'{server_binding,adapter,surface}' native_surface
    from ops.work_request w
    left join lateral (select x.* from ops.engineering_execution_envelope x
      where x.work_request_id=w.id and x.issued_at<=as_of and x.created_at<=as_of
        and not exists(select 1 from ops.engineering_execution_envelope y
          where y.supersedes_envelope_id=x.id and y.issued_at<=as_of and y.created_at<=as_of)
      order by x.issued_at desc,x.id desc limit 1) e on true
    left join ops.job j on j.id=e.job_id and j.created_at<=as_of and j.updated_at<=as_of
    left join ops.capability_agent_session s on s.id=e.agent_session_id and s.work_request_id=w.id
      and s.created_at<=as_of and s.updated_at<=as_of
    left join lateral (select x.id,x.started_at from ops.job_attempt x
      where x.job_id=j.id and x.attempt=j.attempt and x.started_at<=as_of order by x.id limit 1) a on true
    where w.organization_tenant_id=tenant and (w.requester_actor=actor_slug or w.owner_actor=actor_slug or w.executor_actor=actor_slug)
      and w.desired_outcome is not null and w.updated_at<=as_of
      and (after_updated is null or (w.updated_at,w.id)<(after_updated,after_id))
  ), rows as (
    select *, case when observed_at<as_of-interval '24 hours' then 'unknown'
      when job_state in ('failed','timed_out','cancelled','dead_lettered') then 'failed'
      when job_state in ('retry_wait','waiting_approval') then 'waiting'
      when job_state='running' then 'active'
      when job_state='queued' then 'queued'
      when job_state='succeeded' and state in ('confirmed_closed','released') then 'verified'
      when job_id is not null then 'unknown'
      when state='failed' then 'failed' when state in ('blocked','needs_joe','awaiting_release') then 'waiting'
      when session_state in ('claimed','in_progress','verification') then 'active'
      when state in ('confirmed_closed','released') then 'verified'
      when state in ('captured','triaged','ready','claimed') then 'queued' else 'unknown' end routing_state
    from visible order by updated_at desc,id desc limit page_limit+1
  ), page as (select *,row_number() over(order by updated_at desc,id desc) rn from rows)
  select jsonb_build_object('ok',true,'schema_version','doc-outcome-cards.v2','as_of',as_of,
    'correlation_version','sha256:'||encode(public.digest(convert_to(scope_digest||E'\n'||as_of::text,'UTF8'),'sha256'),'hex'),
    'more',exists(select 1 from page where rn>page_limit),
    'next_cursor',case when exists(select 1 from page where rn>page_limit) then
      (select replace(encode(convert_to(jsonb_build_object('v',2,'s',scope_digest,'a',as_of,
        'u',p.updated_at,'i',p.id)::text,'UTF8'),'base64'),E'\n','')
       from page p where p.rn=page_limit) end,
    'cards',coalesce(jsonb_agg(jsonb_build_object(
      'card_id','card:doc-outcome:'||id::text,'requested_outcome',desired_outcome,
      'intent_kind',case when job_id is null then 'recommendation' else 'submission' end,
      'work_request_ref',ref,'owner',coalesce(owner_actor,executor_actor,requester_actor),'controlled_phase',state,
      'source_freshness',jsonb_build_object('state',case when observed_at>=as_of-interval '24 hours' then 'fresh' else 'stale' end,'observed_at',observed_at,'source_ref','work-request:'||ref),
      'routing_state',routing_state,'state_evidence',jsonb_build_object('observed_at',observed_at,'routing_source','canonical_work_request'),
      'job_id',jsonb_build_object('value',case when job_id is null then null else 'job:'||job_id::text end,'unavailable_reason',case when job_id is null then 'no_authoritative_job_join' end),
      'attempt_id',jsonb_build_object('value',case when job_attempt_id is null then null else 'job-attempt:'||job_attempt_id::text end,'unavailable_reason',case when job_attempt_id is null then 'no_authoritative_attempt_join' end),
      'canonical_session_id',jsonb_build_object('value',session_id,'unavailable_reason',case when session_id is null then 'no_authoritative_capability_session_join' end),
      'native_task_id',jsonb_build_object('value',null,'unavailable_reason','no_authoritative_native_task_join'),
      'next_check',jsonb_build_object('available',false,'value',null,'unavailable_reason','no_proved_next_check'),
      'result',jsonb_build_object('available',false,'value',null,'unavailable_reason','no_accepted_outcome'),
      'session_entry',jsonb_build_object('available',false,'target',null,'capability',null,
        'unavailable_reason',case when session_id is null then 'session_relation_unavailable'
          when native_surface not in ('codex_desktop','claude_desktop') then 'native_open_unsupported'
          when session_state not in ('cancelled','completed') and session_updated_at>=as_of-interval '2 hours' then 'host_available_but_native_task_unbound'
          else 'host_unavailable' end,
        'fallback',case when session_id is not null and native_surface in ('codex_desktop','claude_desktop')
          and (session_state in ('cancelled','completed') or session_updated_at<as_of-interval '2 hours')
          then jsonb_build_object('kind','copy_session_id','value',session_id::text) else null end,
        'auto_launch',false)
    ) order by updated_at desc,id desc) filter(where rn<=page_limit),'[]'::jsonb)) into result from page;
  return result;
end $b09$;
revoke all on function ops.read_doc_outcome_cards_successor(text,integer) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.read_doc_outcome_cards_successor(text,integer) to carr_writer,carr_authority;
