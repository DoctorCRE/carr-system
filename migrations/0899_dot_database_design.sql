-- Dot database design review: forward-only integrity and concurrency repairs.
-- New writes refuse invalid parents; existing invalid allocations refuse validation.
-- rollback: forward-only — preserve recorded decisions and completed jobs; restore future behavior with a successor migration rather than undoing accepted state.
-- lock-review: commission_allocation constraints and the activity index scan without rewriting business rows. Production row counts are not assumed; the migrator bounds lock acquisition at 5s and each statement at 5min, with rollback on timeout. The timeline index is exercised on 1,000,000 synthetic activity rows.

CREATE OR REPLACE FUNCTION ops.decide_doc_suggestion(p_id uuid, p_base integer, p_choice text, p_until date, p_work_ref text, p_key uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public'
AS $function$
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
  if v_row.version is distinct from p_base then
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
end $function$;

CREATE OR REPLACE FUNCTION ops.suggest_doc_work(p_conversation uuid, p_sequence integer, p_obligation_key text, p_polished text, p_uncertainty text, p_material jsonb, p_key uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public'
AS $function$
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
    if p_sequence < v_row.source_sequence then
      return jsonb_build_object('ok',false,'reason_id','stale_source_sequence','current_sequence',v_row.source_sequence);
    end if;
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
end $function$;

CREATE OR REPLACE FUNCTION ops.complete_job(p_job_id uuid, p_lease_token uuid, p_evidence jsonb, p_receipt_ref text)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare j ops.job%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if found and j.definition_key='engineering-slice' then
    raise exception 'engineering jobs require scoped controller functions';
  end if;
  if not found or j.state <> 'running' or j.lease_token is distinct from p_lease_token
     or p_lease_token is null or j.leased_until is null or j.leased_until < now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  update ops.job_attempt set state='succeeded',ended_at=now()
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  update ops.job set state='succeeded',ended_at=now(),lease_owner=null,
         lease_token=null,leased_until=null,updated_at=now() where id=j.id;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,'completion',p_receipt_ref,coalesce(p_evidence,'{}'::jsonb));
  return true;
end $function$;

CREATE OR REPLACE FUNCTION ops.enforce_work_in_progress_limit()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
declare
  system_wide     int;
  per_executor    int;
  who             text;
  who_before      text;
  already_in_flight boolean;
  limit_system    constant int := 2;
  limit_each      constant int := 1;
begin
  if new.state not in ('claimed','in_progress') then
    return new;
  end if;

  -- A fixed snapshot can omit a claim committed by the prior lock holder.
  if current_setting('transaction_isolation') not in ('read committed','read uncommitted') then
    raise exception using errcode='25001',
      message='work-in-progress admission requires READ COMMITTED isolation';
  end if;

  -- All competing claims and reassignments share one admission lock.
  perform pg_advisory_xact_lock(757, 1);
  already_in_flight := (tg_op = 'UPDATE' and old.state in ('claimed','in_progress'));

  -- SYSTEM-WIDE: entry only. A row already in flight is already counted, and
  -- re-checking here would make a full queue uneditable.
  if not already_in_flight then
    select count(*) into system_wide
      from ops.work_request
     where state in ('claimed','in_progress')
       and id <> new.id;

    if system_wide + 1 > limit_system then
      raise exception
        'work-in-progress limit: % already in flight system-wide and the limit is %. '
        'Move something to blocked, verification or confirmed_closed before claiming %.',
        system_wide, limit_system, new.ref
        using errcode = 'check_violation';
    end if;
  end if;

  who := coalesce(new.executor_actor, new.owner_actor);

  -- PER-EXECUTOR: on entry, and on any reassignment of a row already in flight.
  -- Reassignment does not move the system-wide total but it does move this one.
  if already_in_flight then
    who_before := coalesce(old.executor_actor, old.owner_actor);
    if who_before is not distinct from who then
      return new;
    end if;
  end if;

  if who is not null then
    select count(*) into per_executor
      from ops.work_request
     where state in ('claimed','in_progress')
       and coalesce(executor_actor, owner_actor) = who
       and id <> new.id;

    if per_executor + 1 > limit_each then
      raise exception
        'work-in-progress limit: % already has % in flight and the limit per executor is %. '
        'One thing at a time is the point; finish or park it before claiming %.',
        who, per_executor, limit_each, new.ref
        using errcode = 'check_violation';
    end if;
  end if;

  return new;
end $function$;

CREATE OR REPLACE FUNCTION ops.reserve_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare j ops.job%rowtype; route ops.provider_route%rowtype; spent numeric; reserved numeric; rid uuid;
begin
  -- The route lock serializes admission, but a fixed snapshot can still
  -- omit reservations committed by the prior lock holder.
  if current_setting('transaction_isolation') not in ('read committed','read uncommitted') then
    raise exception using errcode='25001',
      message='provider route budget admission requires READ COMMITTED isolation';
  end if;
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select * into route from ops.provider_route where route_key=p_route_key and enabled for update;
  if not found then raise exception 'provider route % is not enabled',p_route_key; end if;
  if p_estimated_cost_usd<0 then raise exception 'estimated cost must be non-negative'; end if;
  -- Settlement time owns spend, not the attempt's start month. Preserve
  -- legacy unreserved attempt costs without counting reserved costs twice.
  select coalesce(sum(r.actual_cost_usd),0) into spent from ops.cost_reservation r
   where r.route_key=p_route_key and r.state='settled'
     and r.settled_at>=date_trunc('month',now());
  select spent+coalesce(sum(a.cost_usd),0) into spent from ops.job_attempt a
   where a.provider_route=p_route_key and a.started_at>=date_trunc('month',now())
     and not exists(select 1 from ops.cost_reservation r where r.job_id=a.job_id
       and r.attempt=a.attempt and r.state='settled');
  select coalesce(sum(r.estimated_cost_usd),0) into reserved from ops.cost_reservation r
   where r.route_key=p_route_key and r.state='reserved'
;
  if route.monthly_budget_usd is not null
     and spent+reserved+p_estimated_cost_usd>route.monthly_budget_usd then
    raise exception 'provider route % monthly budget would be exceeded',p_route_key;
  end if;
  insert into ops.cost_reservation(job_id,attempt,route_key,estimated_cost_usd)
    values(j.id,j.attempt,p_route_key,p_estimated_cost_usd)
  returning id into rid;
  return rid;
end $function$;

CREATE OR REPLACE FUNCTION ops.settle_job_cost(p_reservation_id uuid, p_job_id uuid, p_lease_token uuid, p_input_tokens integer, p_output_tokens integer, p_actual_cost_usd numeric)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare j ops.job%rowtype; r ops.cost_reservation%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select * into r from ops.cost_reservation where id=p_reservation_id for update;
  if not found or r.job_id<>j.id or r.attempt<>j.attempt or r.state<>'reserved' then
    raise exception 'cost reservation does not belong to this live attempt';
  end if;
  -- Serialize conversion from reserved to spent with admission on this route.
  perform 1 from ops.provider_route where route_key=r.route_key for update;
  if p_actual_cost_usd<0 or p_actual_cost_usd>r.estimated_cost_usd then
    raise exception 'actual cost exceeds admitted reservation';
  end if;
  update ops.cost_reservation set state='settled',actual_cost_usd=p_actual_cost_usd,
         settled_at=now() where id=r.id;
  update ops.job_attempt set provider_route=r.route_key,input_tokens=p_input_tokens,
         output_tokens=p_output_tokens,cost_usd=p_actual_cost_usd
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  return true;
end $function$;

CREATE OR REPLACE FUNCTION ops.put_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text, p_cognition_key text, p_cognition_version integer, p_output_schema_version integer, p_proposal jsonb, p_dependency_refs text[], p_ttl_seconds integer)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare j ops.job%rowtype; execution_kind text; declared_cognition_key text; active_contract_count integer; contract ops.cognition_job%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select d.execution_kind,d.execution_contract->>'cognition_job'
    into execution_kind,declared_cognition_key from ops.job_definition d
   where d.key=j.definition_key and d.version=j.definition_version;
  if execution_kind is distinct from 'cognition'
     or declared_cognition_key is distinct from p_cognition_key then
    raise exception 'job % is not bound to cognition contract %',p_job_id,p_cognition_key;
  end if;
  select count(*) into active_contract_count from ops.cognition_job where key=declared_cognition_key and active;
  if active_contract_count <> 1 then raise exception 'job % lacks one active cognition contract',p_job_id; end if;
  select * into contract from ops.cognition_job where key=declared_cognition_key and active;
  if p_cognition_key<>contract.key or p_cognition_version<>contract.version
     or p_output_schema_version<>contract.output_schema_version then
    raise exception 'cache write does not match active cognition contract';
  end if;
  if btrim(coalesce(p_cache_key,''))='' or p_ttl_seconds<1 then
    raise exception 'cache key and positive cache TTL are required';
  end if;
  insert into ops.cognition_result_cache
    (cache_key,cognition_key,cognition_version,output_schema_version,proposal,
     dependency_refs,validated_at,expires_at)
  values (p_cache_key,p_cognition_key,p_cognition_version,p_output_schema_version,p_proposal,
          coalesce(p_dependency_refs,'{}'),now(),now()+make_interval(secs=>p_ttl_seconds))
  on conflict(cache_key) do update set cognition_key=excluded.cognition_key,
    cognition_version=excluded.cognition_version,output_schema_version=excluded.output_schema_version,
    proposal=excluded.proposal,
    dependency_refs=excluded.dependency_refs,validated_at=excluded.validated_at,
    expires_at=excluded.expires_at,invalidated_at=null;
  insert into ops.cognition_cache_observation
    (job_id,attempt,workflow_key,workflow_version,mode,cache_key,observation_kind)
  values (j.id,j.attempt,j.definition_key,j.definition_version,j.mode,p_cache_key,'store')
  on conflict (job_id,attempt,cache_key,observation_kind) do nothing;
  return true;
end $function$;

CREATE OR REPLACE FUNCTION ops.invalidate_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_dependency_ref text)
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare j ops.job%rowtype; n integer; execution_kind text;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select d.execution_kind into execution_kind from ops.job_definition d
   where d.key=j.definition_key and d.version=j.definition_version;
  if execution_kind is distinct from 'cognition' then
    raise exception 'job % is not a cognition workflow',p_job_id;
  end if;
  if btrim(coalesce(p_dependency_ref,''))='' then raise exception 'dependency ref is required'; end if;
  with changed as (
    update ops.cognition_result_cache set invalidated_at=now()
     where invalidated_at is null and p_dependency_ref=any(dependency_refs)
     returning cache_key
  ), evidence as (
    insert into ops.cognition_cache_observation
      (job_id,attempt,workflow_key,workflow_version,mode,cache_key,observation_kind,dependency_ref)
    select j.id,j.attempt,j.definition_key,j.definition_version,j.mode,cache_key,'invalidate',p_dependency_ref
      from changed
    on conflict (job_id,attempt,cache_key,observation_kind) do nothing
    returning 1
  ) select count(*) into n from changed;
  return n;
end $function$;

CREATE OR REPLACE FUNCTION ops.v5_a05_assurance_cadence_batch(p_recipient_slug text)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public'
AS $function$
declare v_result jsonb; v_recipient uuid;
begin
  if p_recipient_slug is null or p_recipient_slug not in ('joe', 'dell') then
    raise exception using errcode = '42501',
      message = 'the V5-A05 morning batch is readable only for a partner recipient';
  end if;
  select id into v_recipient from public.actor
   where slug = p_recipient_slug and kind = 'human' and active;
  if v_recipient is null then
    raise exception using errcode = '42501',
      message = 'the V5-A05 morning batch is readable only for an active partner recipient';
  end if;

  select coalesce(jsonb_agg(row_to_json(batch) order by batch.created_at desc), '[]'::jsonb)
    into v_result
  from (
    select n.id as notification_id, n.reason, n.severity, n.subject_type, n.subject_ref,
           n.deep_link, n.created_at, s.signal_kind as reason_id
      from ops.notification n
      join public.signal_event s on s.id = n.event_ref and n.event_source = 'signal_event'
      left join ops.notification_read r
        on r.notification_id = n.id and r.recipient_actor = n.recipient_actor
     where n.recipient_actor = v_recipient
       and s.producer = 'v5-a05-delivery-cadence'
       and r.notification_id is null
       and not exists (select 1 from ops.notification_delivery h
                        where h.notification_id = n.id and h.state = 'held_until_morning'
                          and h.held_until > now())
     order by n.created_at desc
     limit 50
  ) batch;
  return v_result;
end;
$function$;

CREATE OR REPLACE FUNCTION public.trg_deal_participant_side()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
declare s text;
begin
  select side into s from public.participant_role where slug = new.role;

  if s = 'actor' then
    if new.actor_id is null then
      raise exception 'deal_participant.role=% is an ACTOR-side role (a CARR employee) and '
                      'needs actor_id. See participant_role.side.', new.role;
    end if;
    if new.party_id is not null then
      raise exception 'deal_participant.role=% is an ACTOR-side role and must NOT carry '
                      'party_id. role=''lead'' means the deal''s OWNING AGENT (joe or dell), '
                      'not the client — v_deal_board reads lead_owner off actor_id and never '
                      'looks at party_id. Writing the client''s party here makes one row '
                      'assert two different owners. If you want the client''s person on the '
                      'deal, that is role=''client_contact'', or just follow '
                      'deal -> client -> client.party_id, which is already exact and 1:1.',
                      new.role;
    end if;

  elsif s = 'party' then
    if new.party_id is null then
      raise exception 'deal_participant.role=% is a PARTY-side role (someone outside CARR) '
                      'and needs party_id. See participant_role.side.', new.role;
    end if;
    if new.actor_id is not null then
      raise exception 'deal_participant.role=% is a PARTY-side role and must NOT carry '
                      'actor_id — an actor is a CARR employee and this role is a '
                      'counterparty.', new.role;
    end if;
  end if;

  return new;
end
$function$;

CREATE OR REPLACE FUNCTION ops.add_meeting_note(p_meeting uuid, p_body text, p_revises_note_number integer, p_base_revision integer, p_client_instance text, p_idempotency_key uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public'
AS $function$
declare v_who record; v_row ops.meeting%rowtype; v_prior ops.meeting_note%rowtype;
        v_number integer; v_revision integer; v_current integer;
begin
  select * into v_who from ops.meeting_mode_actor();
  select * into v_prior from ops.meeting_note where id = p_idempotency_key;
  if v_prior.id is not null then
    if v_prior.meeting_id is distinct from p_meeting or v_prior.body is distinct from p_body
       or v_prior.author_actor is distinct from v_who.actor_id
       or (case when v_prior.revision=1 then null else v_prior.note_number end) is distinct from p_revises_note_number
       or (case when v_prior.revision=1 then null else v_prior.revision-1 end) is distinct from p_base_revision then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_note_idempotency_key_reuse');
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'meeting_id', p_meeting,
      'note_number', v_prior.note_number, 'revision', v_prior.revision);
  end if;
  select * into v_row from ops.meeting
   where id = p_meeting and organization_tenant_id = v_who.tenant for update;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  if v_row.mode_state = 'ended' then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_ended', 'meeting_id', p_meeting);
  end if;
  if p_revises_note_number is null then
    if p_base_revision is not null then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_note_base_revision_without_note');
    end if;
    v_number := v_row.last_note_number + 1;
    v_revision := 1;
    update ops.meeting set last_note_number = v_number where id = p_meeting;
  else
    select max(revision) into v_current from ops.meeting_note
     where meeting_id = p_meeting and note_number = p_revises_note_number;
    if v_current is null then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_note_not_found',
        'note_number', p_revises_note_number);
    end if;
    if p_base_revision is distinct from v_current then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_note_revision_conflict',
        'note_number', p_revises_note_number, 'current_revision', v_current);
    end if;
    v_number := p_revises_note_number;
    v_revision := v_current + 1;
  end if;
  insert into ops.meeting_note(id, meeting_id, note_number, revision, body, author_actor, author_instance)
  values (p_idempotency_key, p_meeting, v_number, v_revision, p_body, v_who.actor_id, p_client_instance);
  perform ops.meeting_mode_append(p_meeting,
    case when v_revision = 1 then 'note_added' else 'note_revised' end,
    v_who.actor_id, p_client_instance, v_number, null, v_revision, null, p_idempotency_key);
  return jsonb_build_object('ok', true, 'deduplicated', false, 'meeting_id', p_meeting,
    'note_number', v_number, 'revision', v_revision,
    'seq', (select last_seq from ops.meeting where id = p_meeting));
end $function$;

CREATE OR REPLACE FUNCTION ops.rename_doc_conversation(p_conversation uuid, p_base_version integer, p_title text, p_pinned boolean, p_archived boolean, p_idempotency_key uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public'
AS $function$
declare v_actor uuid; v_row ops.doc_conversation%rowtype; v_version integer;
begin
  v_actor := ops.portfolio_writer_actor_id();

  select * into v_row from ops.doc_conversation where id = p_conversation for update;
  if v_row.id is null
     or not (v_row.created_by_actor = v_actor
             or exists (select 1 from ops.doc_conversation_grant g
                         where g.conversation_id = v_row.id and g.grantee_actor = v_actor
                           and g.revoked_at is null)) then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end if;
  -- BEFORE the version comparison and BEFORE any write.
  if v_row.created_by_actor <> v_actor then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_creator_only');
  end if;

  if p_title is null and p_pinned is null and p_archived is null then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_no_change_requested');
  end if;

  -- The PRIOR title, appended only when the title actually moves, and only
  -- under the same version predicate the update below carries.
  -- clock_timestamp() for the same primary-key reason as the grant (0520:43).
  insert into ops.doc_conversation_title_revision(conversation_id, title, at, by_actor)
  select id, title, clock_timestamp(), v_actor from ops.doc_conversation
   where id = p_conversation and version = p_base_version
     and p_title is not null and p_title is distinct from title;

  -- The compare-and-swap is a predicated update and NOT FOUND *is* the refusal.
  update ops.doc_conversation
     set title       = coalesce(p_title, title),
         pinned_at   = case when p_pinned   is null then pinned_at
                            when p_pinned   then coalesce(pinned_at, now()) else null end,
         archived_at = case when p_archived is null then archived_at
                            when p_archived then coalesce(archived_at, now()) else null end,
         version     = version + 1,
         updated_at  = now()
   where id = p_conversation and version = p_base_version
  returning version into v_version;
  if not found then
    return jsonb_build_object('ok', false, 'reason_id', 'version_conflict',
      'current_version', (select version from ops.doc_conversation where id = p_conversation));
  end if;

  select * into v_row from ops.doc_conversation where id = p_conversation;
  return jsonb_build_object('ok', true, 'id', p_conversation, 'version', v_version,
    'title', v_row.title, 'pinned', v_row.pinned_at is not null,
    'archived', v_row.archived_at is not null);
end $function$;


-- Direct SELECT bypassed the access-checked Doc facts/read doors.
revoke select on ops.doc_conversation,ops.doc_conversation_turn,
  ops.doc_conversation_title_revision,ops.doc_conversation_grant from public,carr_reader,carr_writer;
revoke execute on function ops.engineering_register_slice_plan(text,jsonb,text,uuid) from public,carr_reader;
revoke execute on function ops.issue_execution_envelope_v1(text,text,uuid) from public,carr_reader;

-- Full operation identity for newly accepted meeting decisions. No direct
-- runtime grants: only the existing SECURITY DEFINER decision door writes it.
create table ops.meeting_action_decision_request (
  idempotency_key uuid primary key,
  request jsonb not null check(jsonb_typeof(request)='object')
);

CREATE OR REPLACE FUNCTION ops.decide_meeting_action(p_meeting uuid, p_action_number integer, p_decision text, p_base_revision integer, p_disposition text, p_assignee_slug text, p_client_instance text, p_idempotency_key uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public'
AS $function$
declare v_who record; v_row ops.meeting%rowtype; v_head ops.meeting_action%rowtype;
        v_latest ops.meeting_action_revision%rowtype; v_assignee uuid; v_disposition text;
        v_request jsonb; v_prior_request jsonb;
begin
  select * into v_who from ops.meeting_mode_actor();
  if not coalesce(v_who.is_partner, false) then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_decision_requires_verified_partner');
  end if;
  if p_decision not in ('accept', 'decline') then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_decision_invalid');
  end if;
  select * into v_row from ops.meeting
   where id = p_meeting and organization_tenant_id = v_who.tenant for update;
  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_not_found');
  end if;
  select * into v_head from ops.meeting_action
   where meeting_id = p_meeting and action_number = p_action_number for update;
  if v_head.meeting_id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_not_found',
      'action_number', p_action_number);
  end if;
  v_request:=jsonb_build_object('meeting_id',p_meeting,'action_number',p_action_number,
    'decision',p_decision,'base_revision',p_base_revision,'disposition',p_disposition,
    'assignee_slug',p_assignee_slug,'actor_id',v_who.actor_id);
  select request into v_prior_request from ops.meeting_action_decision_request
    where idempotency_key=p_idempotency_key;
  if v_prior_request is not null and v_prior_request is distinct from v_request then
    return jsonb_build_object('ok',false,'reason_id','meeting_action_idempotency_key_reuse');
  end if;
  if exists (select 1 from ops.meeting_stream where idempotency_key = p_idempotency_key
               and kind in ('action_accepted', 'action_declined')) then
    if v_prior_request is null then
      return jsonb_build_object('ok',false,'reason_id','meeting_action_legacy_replay_unverifiable');
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'already', true,
      'meeting_id', p_meeting, 'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;

  if v_head.state <> 'proposed' then
    if p_decision = 'accept' and v_head.state in ('accepted', 'executed', 'delegated') then
      return jsonb_build_object('ok', true, 'deduplicated', false, 'already', true,
        'resolved_once', true, 'meeting_id', p_meeting,
        'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
    end if;
    if p_decision = 'decline' and v_head.state = 'declined' then
      return jsonb_build_object('ok', true, 'deduplicated', false, 'already', true,
        'meeting_id', p_meeting, 'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
    end if;
    return jsonb_build_object('ok', false,
      'reason_id', case when v_head.state = 'declined' then 'meeting_action_already_declined'
                        else 'meeting_action_already_accepted_requires_correction' end,
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;
  if p_base_revision is distinct from v_head.current_revision then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_revised_since_read',
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;

  if p_decision = 'decline' then
    update ops.meeting_action
       set state = 'declined', decided_revision = v_head.current_revision,
           decided_by_actor = v_who.actor_id, decided_at = clock_timestamp()
     where meeting_id = p_meeting and action_number = p_action_number;
    perform ops.meeting_mode_append(p_meeting, 'action_declined', v_who.actor_id,
      p_client_instance, null, p_action_number, v_head.current_revision, null, p_idempotency_key);
    insert into ops.meeting_action_decision_request values(p_idempotency_key,v_request);
    return jsonb_build_object('ok', true, 'deduplicated', false, 'already', false,
      'meeting_id', p_meeting, 'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;

  select * into v_latest from ops.meeting_action_revision
   where meeting_id = p_meeting and action_number = p_action_number
     and revision = v_head.current_revision;
  if v_latest.command is null then
    -- A tentative item with nothing to execute stays a proposal; accepting it
    -- would make this table the task store.
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_has_no_canonical_command',
      'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
  end if;
  v_disposition := coalesce(p_disposition, 'execute');
  if v_disposition not in ('execute', 'delegate') then
    return jsonb_build_object('ok', false, 'reason_id', 'meeting_action_disposition_invalid');
  end if;
  if p_assignee_slug is null then
    if v_disposition = 'delegate' then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_delegation_requires_assignee');
    end if;
    v_assignee := v_who.actor_id;
  else
    select id into v_assignee from public.actor where slug = p_assignee_slug and active;
    if v_assignee is null then
      return jsonb_build_object('ok', false, 'reason_id', 'meeting_assignee_not_found');
    end if;
  end if;
  perform ops.meeting_mode_accept(p_meeting, p_action_number, v_head.current_revision,
    v_who.actor_id, v_disposition, v_assignee, p_client_instance, p_idempotency_key);
  insert into ops.meeting_action_decision_request values(p_idempotency_key,v_request);
  return jsonb_build_object('ok', true, 'deduplicated', false, 'already', false,
    'resolved_once', true, 'meeting_id', p_meeting,
    'action', ops.meeting_mode_action_view(p_meeting, p_action_number));
end $function$;


-- Ownership is enforced by a composite foreign key, including parent changes.
alter table public.commission_allocation add constraint allocation_id_commission_unique unique(id,commission_id);
alter table public.commission_allocation add constraint allocation_parent_same_commission
  foreign key(parent_id,commission_id) references public.commission_allocation(id,commission_id) not valid;
alter table public.commission_allocation add constraint allocation_not_own_parent check(parent_id is distinct from id) not valid;
alter table public.commission_allocation validate constraint allocation_parent_same_commission;
alter table public.commission_allocation validate constraint allocation_not_own_parent;
create function public.enforce_allocation_tree() returns trigger
language plpgsql set search_path=pg_catalog,public,pg_temp as $$
begin
  -- The ancestor census must see commits made by the prior lock holder.
  -- Fixed snapshots do not refresh after an advisory-lock wait.
  if current_setting('transaction_isolation') not in ('read committed','read uncommitted') then
    raise exception using errcode='25001',
      message='commission allocation tree writes require READ COMMITTED isolation';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('commission-allocation:'||new.commission_id::text,757));
  if new.parent_id is not null and exists (
    with recursive ancestors as (
      select a.id,a.parent_id from public.commission_allocation a where a.id=new.parent_id
      union
      select a.id,a.parent_id from public.commission_allocation a join ancestors p on a.id=p.parent_id
    ) select 1 from ancestors where id=new.id
  ) then raise exception using errcode='23514',message='commission allocation parent would create a cycle'; end if;
  return new;
end $$;
create trigger allocation_tree before insert or update of parent_id,commission_id
  on public.commission_allocation for each row execute function public.enforce_allocation_tree();
-- Refuse pre-existing longer cycles; do not silently reparent business records.
do $$
begin
  if exists(with recursive walk as (
    select id as root,id,parent_id,array[id] as path,false as cycle from public.commission_allocation
    union all
    select w.root,a.id,a.parent_id,w.path||a.id,a.id=any(w.path)
      from walk w join public.commission_allocation a on a.id=w.parent_id where not w.cycle
  ) select 1 from walk where cycle) then raise exception 'existing commission allocation cycle requires repair'; end if;
end $$;

create index activity_subject_timeline on public.activity (
  (case when deal_id is not null then 'deal' when client_id is not null then 'client'
    when lead_id is not null then 'lead' else 'vendor' end),
  (coalesce(deal_id,client_id,lead_id,vendor_id)),occurred_at desc
);
