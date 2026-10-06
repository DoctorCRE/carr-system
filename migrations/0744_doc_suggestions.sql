-- DoctorCRE B08: one suggestion per explicitly identified obligation.
-- 0745 seals this migration in the same atomic migration group.
create table ops.doc_suggestion (
  id uuid primary key,
  conversation_id uuid not null references ops.doc_conversation(id),
  obligation_key text not null check (length(btrim(obligation_key)) between 1 and 240),
  material_facts jsonb not null check (jsonb_typeof(material_facts)='object' and material_facts<>'{}'::jsonb),
  material_version integer not null default 1 check (material_version>0),
  version integer not null default 1 check (version>0),
  source_sequence integer not null,
  original_text text not null,
  polished_text text not null check (length(btrim(polished_text)) between 1 and 4000),
  uncertainty text,
  contributor text not null,
  source_at timestamptz not null,
  suggested_at timestamptz not null default now(),
  disposition text not null default 'open' check (disposition in ('open','act','discuss','snoozed','dismissed')),
  dismissed_material_version integer,
  snoozed_material_version integer,
  snoozed_until date,
  work_ref uuid references public.loop_item(id),
  unique(conversation_id,obligation_key)
);
create table ops.doc_suggestion_contribution (
  idempotency_key uuid primary key,
  suggestion_id uuid not null references ops.doc_suggestion(id),
  request jsonb not null,
  source_sequence integer not null,
  original_text text not null,
  contributor text not null,
  at timestamptz not null,
  unique(suggestion_id,source_sequence)
);
create table ops.doc_suggestion_scan (
  idempotency_key uuid primary key,
  conversation_id uuid not null references ops.doc_conversation(id),
  through_sequence integer not null check (through_sequence>=-1),
  completed_at timestamptz not null default now()
);
create table ops.doc_suggestion_decision (
  idempotency_key uuid primary key,
  suggestion_id uuid not null references ops.doc_suggestion(id),
  request jsonb not null,
  result jsonb not null,
  decided_by uuid not null references public.actor(id),
  at timestamptz not null default now()
);
create table ops.doc_correction_proposal (
  id uuid primary key,
  suggestion_id uuid not null references ops.doc_suggestion(id),
  base_version integer not null,
  proposed_text text not null check (length(btrim(proposed_text)) between 1 and 4000),
  source_conversation_id uuid not null references ops.doc_conversation(id),
  source_sequence integer not null,
  proposed_by uuid not null references public.actor(id),
  proposed_at timestamptz not null default now(),
  status text not null default 'pending' check (status in ('pending','accepted','declined'))
);

create or replace function ops.doc_suggestion_visible(p_conversation uuid,p_actor uuid)
returns boolean language sql stable security definer set search_path=pg_catalog,ops,public as $$
  select exists(select 1 from ops.doc_conversation c where c.id=p_conversation and
    (c.created_by_actor=p_actor or exists(select 1 from ops.doc_conversation_grant g
      where g.conversation_id=c.id and g.grantee_actor=p_actor and g.revoked_at is null)))
$$;

create or replace function ops.suggest_doc_work(p_conversation uuid,p_sequence integer,
  p_obligation_key text,p_polished text,p_uncertainty text,p_material jsonb,p_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare v_turn ops.doc_conversation_turn%rowtype; v_row ops.doc_suggestion%rowtype;
        v_existing ops.doc_suggestion_contribution%rowtype; v_changed boolean;
        v_request jsonb;
begin
  v_request:=jsonb_build_object('conversation_id',p_conversation,'source_sequence',p_sequence,
    'obligation_key',p_obligation_key,'polished_text',p_polished,
    'uncertainty',p_uncertainty,'material_facts',p_material);
  select * into v_existing from ops.doc_suggestion_contribution where idempotency_key=p_key;
  if v_existing.idempotency_key is not null then
    if v_existing.request is distinct from v_request then
      return jsonb_build_object('ok',false,'reason_id','idempotency_key_reuse'); end if;
    select * into v_row from ops.doc_suggestion where id=v_existing.suggestion_id;
    return jsonb_build_object('ok',true,'deduplicated',true,'suggestion_id',v_row.id,
      'version',v_row.version,'material_version',v_row.material_version);
  end if;
  if p_material is null or jsonb_typeof(p_material)<>'object' or p_material='{}'::jsonb
     or length(btrim(p_obligation_key)) not between 1 and 240
     or length(btrim(p_polished)) not between 1 and 4000 then
    return jsonb_build_object('ok',false,'reason_id','doc_suggestion_input_invalid'); end if;
  select * into v_turn from ops.doc_conversation_turn
    where conversation_id=p_conversation and sequence=p_sequence;
  if v_turn.id is null then return jsonb_build_object('ok',false,'reason_id','source_turn_not_found'); end if;
  select * into v_row from ops.doc_suggestion
    where conversation_id=p_conversation and obligation_key=p_obligation_key for update;
  if v_row.id is null then
    insert into ops.doc_suggestion(id,conversation_id,obligation_key,material_facts,source_sequence,
      original_text,polished_text,uncertainty,contributor,source_at)
    values(p_key,p_conversation,p_obligation_key,p_material,p_sequence,v_turn.body,p_polished,
      p_uncertainty,v_turn.origin_actor,v_turn.at) returning * into v_row;
  else
    -- One source turn contributes once to one obligation. The producer must
    -- name a distinct obligation key for a second obligation in the same turn.
    if exists(select 1 from ops.doc_suggestion_contribution
        where suggestion_id=v_row.id and source_sequence=p_sequence) then
      return jsonb_build_object('ok',false,'reason_id','source_turn_already_contributed'); end if;
    v_changed := v_row.material_facts is distinct from p_material;
    update ops.doc_suggestion set material_facts=p_material,
      material_version=material_version+case when v_changed then 1 else 0 end,
      version=version+1, source_sequence=p_sequence, original_text=v_turn.body,
      polished_text=p_polished, uncertainty=p_uncertainty,contributor=v_turn.origin_actor,
      source_at=v_turn.at,suggested_at=now(),
      disposition=case when v_changed then 'open' else disposition end,
      work_ref=case when v_changed then null else work_ref end
      where id=v_row.id returning * into v_row;
  end if;
  insert into ops.doc_suggestion_contribution(idempotency_key,suggestion_id,request,source_sequence,
    original_text,contributor,at)
  values(p_key,v_row.id,v_request,p_sequence,v_turn.body,v_turn.origin_actor,v_turn.at);
  return jsonb_build_object('ok',true,'deduplicated',false,'suggestion_id',v_row.id,
    'version',v_row.version,'material_version',v_row.material_version);
end $$;

create or replace function ops.complete_doc_suggestion_scan(p_conversation uuid,
  p_through_sequence integer,p_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare v_prior ops.doc_suggestion_scan%rowtype; v_head integer;
begin
  select * into v_prior from ops.doc_suggestion_scan where idempotency_key=p_key;
  if v_prior.idempotency_key is not null then
    if v_prior.conversation_id is distinct from p_conversation or
       v_prior.through_sequence is distinct from p_through_sequence then
      return jsonb_build_object('ok',false,'reason_id','idempotency_key_reuse'); end if;
    return jsonb_build_object('ok',true,'deduplicated',true,'through_sequence',v_prior.through_sequence);
  end if;
  perform 1 from ops.doc_conversation where id=p_conversation for update;
  if not found then return jsonb_build_object('ok',false,'reason_id','doc_conversation_not_found'); end if;
  select coalesce(max(sequence),-1) into v_head from ops.doc_conversation_turn
    where conversation_id=p_conversation;
  if p_through_sequence is distinct from v_head then
    return jsonb_build_object('ok',false,'reason_id','scan_head_conflict','current_sequence',v_head); end if;
  insert into ops.doc_suggestion_scan(idempotency_key,conversation_id,through_sequence)
    values(p_key,p_conversation,p_through_sequence);
  return jsonb_build_object('ok',true,'deduplicated',false,'through_sequence',v_head);
end $$;

create or replace function ops.list_doc_suggestions(p_conversation uuid,p_include_parked boolean)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public as $$
declare v_actor uuid; v_rows jsonb; v_head integer; v_scanned integer;
        v_total integer; v_coverage jsonb;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  if p_conversation is not null and not ops.doc_suggestion_visible(p_conversation,v_actor) then
    return jsonb_build_object('ok',false,'reason_id','doc_conversation_not_found'); end if;
  select coalesce(jsonb_agg(to_jsonb(s)
    order by s.suggested_at desc,s.id),'[]'::jsonb) into v_rows
  from (select d.*,
      (select coalesce(jsonb_agg(jsonb_build_object('original_text',c.original_text,
        'contributor',c.contributor,'at',c.at) order by c.at,c.source_sequence),'[]'::jsonb)
        from ops.doc_suggestion_contribution c where c.suggestion_id=d.id) as contributions,
      (select coalesce(jsonb_agg(jsonb_build_object('id',p.id,
        'proposed_text',p.proposed_text,'base_version',p.base_version,
        'proposed_at',p.proposed_at,'status',p.status) order by p.proposed_at,p.id),'[]'::jsonb)
        from ops.doc_correction_proposal p where p.suggestion_id=d.id) as corrections
    from ops.doc_suggestion d where (p_conversation is null or d.conversation_id=p_conversation)
      and ops.doc_suggestion_visible(d.conversation_id,v_actor)
      and (p_include_parked or d.disposition not in ('dismissed','snoozed')
        or (d.disposition='dismissed' and d.dismissed_material_version is distinct from d.material_version)
        or (d.disposition='snoozed' and (d.snoozed_material_version is distinct from d.material_version
          or d.snoozed_until<=current_date)))
    order by d.suggested_at desc,d.id limit 100) s;
  if p_conversation is null then
    v_coverage:=jsonb_build_object('state','unknown','reason_id','conversation_scope_required');
  else
    select coalesce(max(sequence),-1) into v_head from ops.doc_conversation_turn
      where conversation_id=p_conversation;
    select max(through_sequence) into v_scanned from ops.doc_suggestion_scan
      where conversation_id=p_conversation;
    select count(*) into v_total from ops.doc_suggestion where conversation_id=p_conversation;
    v_coverage:=jsonb_build_object('state',case when v_scanned=v_head then 'complete' else 'unknown' end,
      'latest_sequence',v_head,'scanned_through',v_scanned,
      'empty_state',case when jsonb_array_length(v_rows)>0 then 'not_empty'
        when v_scanned is distinct from v_head then 'unknown'
        when v_total>0 then 'filtered'
        else 'verified_empty' end);
  end if;
  return jsonb_build_object('ok',true,'suggestions',v_rows,'coverage',v_coverage,'as_of',now());
end $$;

create or replace function ops.decide_doc_suggestion(p_id uuid,p_base integer,p_choice text,
  p_until date,p_work_ref text,p_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare v_actor uuid; v_row ops.doc_suggestion%rowtype; v_prior ops.doc_suggestion_decision%rowtype;
        v_request jsonb; v_result jsonb; v_work uuid; v_count integer;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  v_request:=jsonb_build_object('suggestion_id',p_id,'base_version',p_base,'choice',p_choice,
    'snoozed_until',p_until,'work_ref',p_work_ref);
  select * into v_prior from ops.doc_suggestion_decision where idempotency_key=p_key;
  if v_prior.idempotency_key is not null then
    if v_prior.request<>v_request or v_prior.decided_by<>v_actor then
      return jsonb_build_object('ok',false,'reason_id','idempotency_key_reuse'); end if;
    return v_prior.result || jsonb_build_object('deduplicated',true);
  end if;
  select * into v_row from ops.doc_suggestion where id=p_id for update;
  if v_row.id is null or not ops.doc_suggestion_visible(v_row.conversation_id,v_actor) then
    return jsonb_build_object('ok',false,'reason_id','doc_suggestion_not_found'); end if;
  if v_row.version<>p_base then
    return jsonb_build_object('ok',false,'reason_id','version_conflict','current',to_jsonb(v_row)); end if;
  if p_choice not in ('act','discuss','snooze','dismiss') then
    return jsonb_build_object('ok',false,'reason_id','choice_invalid'); end if;
  if p_choice='act' then
    select count(*),min(id::text)::uuid into v_count,v_work from public.loop_item
      where number=ltrim(btrim(p_work_ref),'#') and status='open'
        and (tier='shared' or personal_to=v_actor);
    if v_count>1 then return jsonb_build_object('ok',false,'reason_id','work_ref_ambiguous'); end if;
    if v_work is null then
      return jsonb_build_object('ok',false,'reason_id','work_ref_required'); end if;
  elsif p_work_ref is not null then
    return jsonb_build_object('ok',false,'reason_id','work_ref_not_applicable');
  end if;
  if p_choice='snooze' and (p_until is null or p_until<=current_date) then
    return jsonb_build_object('ok',false,'reason_id','future_snooze_date_required'); end if;
  update ops.doc_suggestion set disposition=case when p_choice='snooze' then 'snoozed'
      when p_choice='dismiss' then 'dismissed' else p_choice end,
    version=version+1, dismissed_material_version=case when p_choice='dismiss' then material_version else dismissed_material_version end,
    snoozed_material_version=case when p_choice='snooze' then material_version else snoozed_material_version end,
    snoozed_until=case when p_choice='snooze' then p_until else null end,
    work_ref=case when p_choice='act' then v_work else work_ref end
    where id=p_id returning * into v_row;
  v_result:=jsonb_build_object('ok',true,'suggestion_id',p_id,'version',v_row.version,
    'choice',p_choice,'work_ref',v_row.work_ref,'deduplicated',false);
  insert into ops.doc_suggestion_decision(idempotency_key,suggestion_id,request,result,decided_by)
    values(p_key,p_id,v_request,v_result,v_actor);
  return v_result;
end $$;

create or replace function ops.propose_doc_correction(p_id uuid,p_base integer,p_text text,
  p_conversation uuid,p_sequence integer,p_key uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public as $$
declare v_actor uuid; v_row ops.doc_suggestion%rowtype; v_prior ops.doc_correction_proposal%rowtype;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  select * into v_prior from ops.doc_correction_proposal where id=p_key;
  if v_prior.id is not null then
    if v_prior.suggestion_id<>p_id or v_prior.base_version<>p_base or
      v_prior.proposed_text<>p_text or v_prior.proposed_by<>v_actor or
      v_prior.source_conversation_id<>p_conversation or v_prior.source_sequence<>p_sequence then
      return jsonb_build_object('ok',false,'reason_id','idempotency_key_reuse'); end if;
    return jsonb_build_object('ok',true,'deduplicated',true,'proposal_id',p_key,'status',v_prior.status);
  end if;
  select * into v_row from ops.doc_suggestion where id=p_id for update;
  if v_row.id is null or not ops.doc_suggestion_visible(v_row.conversation_id,v_actor) then
    return jsonb_build_object('ok',false,'reason_id','doc_suggestion_not_found'); end if;
  if v_row.version<>p_base then
    return jsonb_build_object('ok',false,'reason_id','version_conflict','current',to_jsonb(v_row)); end if;
  if v_row.conversation_id<>p_conversation or v_row.source_sequence<>p_sequence then
    return jsonb_build_object('ok',false,'reason_id','source_scope_mismatch'); end if;
  if length(btrim(p_text)) not between 1 and 4000 then
    return jsonb_build_object('ok',false,'reason_id','correction_text_invalid'); end if;
  insert into ops.doc_correction_proposal(id,suggestion_id,base_version,proposed_text,
    source_conversation_id,source_sequence,proposed_by)
    values(p_key,p_id,p_base,p_text,p_conversation,p_sequence,v_actor);
  return jsonb_build_object('ok',true,'deduplicated',false,'proposal_id',p_key,'status','pending');
end $$;

-- The source and decision histories are append-only. The suggestion header is
-- the current projection and advances only through its versioned functions.
create or replace function ops.doc_suggestion_history_immutable() returns trigger
language plpgsql as $$ begin raise exception 'Doc suggestion history is immutable'; end $$;
create trigger doc_suggestion_contribution_immutable before update or delete on ops.doc_suggestion_contribution
  for each row execute function ops.doc_suggestion_history_immutable();
create trigger doc_suggestion_scan_immutable before update or delete on ops.doc_suggestion_scan
  for each row execute function ops.doc_suggestion_history_immutable();
create trigger doc_suggestion_decision_immutable before update or delete on ops.doc_suggestion_decision
  for each row execute function ops.doc_suggestion_history_immutable();
create trigger doc_correction_proposal_immutable before update or delete on ops.doc_correction_proposal
  for each row execute function ops.doc_suggestion_history_immutable();

do $$ declare t text; begin
  foreach t in array array['doc_suggestion','doc_suggestion_contribution','doc_suggestion_scan','doc_suggestion_decision','doc_correction_proposal'] loop
    execute format('create trigger scac_reference_monitor_guard_row before insert or update or delete on ops.%I for each row execute function ops.scac_reference_monitor_guard()',t);
    execute format('create trigger scac_reference_monitor_guard_truncate before truncate on ops.%I for each statement execute function ops.scac_reference_monitor_guard()',t);
  end loop;
end $$;
revoke all on ops.doc_suggestion,ops.doc_suggestion_contribution,ops.doc_suggestion_scan,ops.doc_suggestion_decision,
  ops.doc_correction_proposal from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.doc_suggestion_visible(uuid,uuid),
  ops.suggest_doc_work(uuid,integer,text,text,text,jsonb,uuid),
  ops.complete_doc_suggestion_scan(uuid,integer,uuid),
  ops.list_doc_suggestions(uuid,boolean),
  ops.decide_doc_suggestion(uuid,integer,text,date,text,uuid),
  ops.propose_doc_correction(uuid,integer,text,uuid,integer,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.suggest_doc_work(uuid,integer,text,text,text,jsonb,uuid) to carr_authority;
grant execute on function ops.complete_doc_suggestion_scan(uuid,integer,uuid) to carr_authority;
grant execute on function ops.list_doc_suggestions(uuid,boolean) to carr_writer,carr_authority;
grant execute on function ops.decide_doc_suggestion(uuid,integer,text,date,text,uuid),
  ops.propose_doc_correction(uuid,integer,text,uuid,integer,uuid) to carr_writer,carr_authority;
