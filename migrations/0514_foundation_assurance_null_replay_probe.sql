-- WR-000095: the producer first asks this function whether an idempotency key
-- already has a durable result. That read-only probe intentionally supplies a
-- SQL NULL artifact. In 0511, jsonb_typeof(NULL) <> 'object' evaluated to SQL
-- NULL, so the probe fell through into artifact validation instead of returning
-- no result. Keep the existing authority boundary and make the NULL branch
-- explicit.

create or replace function ops.foundation_assurance_record_production(
  p_verb text, p_idempotency_key uuid, p_identity jsonb, p_produced jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_existing ops.foundation_assurance_production%rowtype;
        v_kind text; v_evidence text; v_digest text; v_result jsonb; v_id uuid:=gen_random_uuid();
        v_receipt jsonb; v_receipt_digest text; v_inventory uuid; v_scope jsonb; v_scope_key text;
        v_at text; v_admission uuid; v_event uuid; v_link text; v_evidence_row ops.foundation_assurance_evidence%rowtype;
begin
  v_actor:=ops.foundation_assurance_require_seat(p_verb,p_identity);
  if p_idempotency_key is null then
    raise exception 'foundation assurance production requires an idempotency key';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,0));
  select * into v_existing from ops.foundation_assurance_production where idempotency_key=p_idempotency_key;
  if found then
    if v_existing.verb is distinct from p_verb or v_existing.actor_id is distinct from v_actor
       or (p_produced is not null and v_existing.artifact_digest is distinct from
         'sha256:'||encode(public.digest(convert_to(ops.portfolio_canonical_json(p_produced),'UTF8'),'sha256'),'hex')) then
      raise exception 'foundation assurance idempotency key was reused for different production';
    end if;
    return v_existing.result||jsonb_build_object('replayed',true);
  end if;
  if p_produced is null or jsonb_typeof(p_produced)<>'object' then return null; end if;
  v_digest:='sha256:'||encode(public.digest(convert_to(ops.portfolio_canonical_json(p_produced),'UTF8'),'sha256'),'hex');
  if p_verb='produce-foundation-assurance-benchmark-coverage' then
    v_kind:='benchmark_coverage'; v_evidence:=p_produced->>'evidence_digest';
    if p_produced#>>'{fact,status}'<>'pass' or p_produced#>>'{fact,evaluator_identity,actor_id}'<>'codex-fa-coverage' then
      raise exception 'foundation assurance benchmark coverage is not a passing staffed fact';
    end if;
  elsif ops.foundation_assurance_expected_step(p_verb) is not null then
    v_kind:='member_receipt'; v_evidence:=p_produced->>'environment_manifest_digest';
    if p_produced->>'status'<>'pass'
       or p_produced->>'receipt_producer_step_ref' is distinct from ops.foundation_assurance_expected_step(p_verb)
       or p_produced#>>'{producer_identity,actor_id}' is distinct from ops.foundation_assurance_expected_actor(p_verb) then
      raise exception 'foundation assurance member receipt does not match its staffed producer';
    end if;
  elsif p_verb='record-foundation-assurance-minimum-outcome' then
    v_kind:='minimum_outcome'; v_receipt:=p_produced->'proposed_receipt';
    v_evidence:=v_receipt->>'environment_manifest_digest';
    if p_produced->>'admissible'<>'true' or p_produced->>'issued'<>'false'
       or v_receipt->>'status'<>'pass'
       or v_receipt#>>'{producer_identity,actor_id}'<>'codex-fa-minimum' then
      raise exception 'foundation assurance minimum was not an admissible unissued proposal';
    end if;
  else raise exception 'unknown foundation assurance producer verb %',p_verb;
  end if;
  select * into v_evidence_row from ops.foundation_assurance_evidence where evidence_digest=v_evidence;
  if not found then raise exception 'foundation assurance production names unknown sealed evidence'; end if;

  if v_kind='minimum_outcome' then
    if exists(select 1 from ops.foundation_assurance_production where evidence_digest=v_evidence and kind='minimum_outcome') then
      raise exception 'foundation assurance minimum already exists for this evidence';
    end if;
    if (select count(*) from ops.foundation_assurance_production
         where evidence_digest=v_evidence and kind in ('benchmark_coverage','member_receipt'))<>8 then
      raise exception 'foundation assurance minimum requires exactly eight stored predecessors';
    end if;
    v_scope:=jsonb_build_object('tenant','carr-internal','scope_ref',
      'safe:wr95-foundation-assurance/'||substr(v_evidence,8),
      'clock_origin_gate_id','foundation-assurance-minimum-accepted',
      'clock_terminus_gate_id','journey-one-kernel-production-accepted',
      'benchmark_subject_digest',v_receipt->>'subject_digest',
      'benchmark_candidate_digest',v_receipt->>'candidate_digest',
      'benchmark_policy_digest',v_receipt->>'policy_digest');
    v_scope_key:=ops.j1_clock_scope_digest(v_scope);
    insert into ops.j1_minimum_inventory(clock_scope_key,clock_scope,clock_scope_ref,tenant,
      minimum_receipt_ttl_policy_ms,minimum_environment_manifest_digest,opened_by_actor_id)
    values(v_scope_key,v_scope,v_scope->>'scope_ref','carr-internal',
      (v_evidence_row.config->>'minimum_receipt_ttl_ms')::bigint,v_evidence,v_actor)
    on conflict(clock_scope_key) do nothing;
    select id into v_inventory from ops.j1_minimum_inventory where clock_scope_key=v_scope_key;
    if exists(select 1 from ops.j1_minimum_admission where inventory_id=v_inventory) then
      raise exception 'foundation assurance minimum inventory already has an admission';
    end if;
    v_at:=ops.j1_minimum_admission_instant();
    v_receipt_digest:=ops.j1_minimum_receipt_digest(v_receipt);
    v_link:=ops.j1_minimum_admission_digest(v_at,v_scope_key,v_evidence,
      (v_evidence_row.config->>'minimum_receipt_ttl_ms')::bigint,null,v_receipt_digest,'carr-internal');
    insert into ops.j1_minimum_admission(inventory_id,admission_ordinal,idempotency_key,
      prior_admission_digest,admission_digest,receipt,receipt_digest,admitted_at,gate_id,
      receipt_producer_step_ref,observed_at,ttl_expires_at,status,
      minimum_receipt_ttl_policy_ms,minimum_environment_manifest_digest,receipt_schema_ref,
      source_ref,input_authority,written_by_actor_id)
    values(v_inventory,0,p_idempotency_key,null,v_link,v_receipt,v_receipt_digest,v_at,
      v_receipt->>'gate_id',v_receipt->>'receipt_producer_step_ref',v_receipt->>'observed_at',
      v_receipt->>'ttl_expires_at',v_receipt->>'status',
      (v_evidence_row.config->>'minimum_receipt_ttl_ms')::bigint,v_evidence,
      'consumer-gate-receipt.v1',v_evidence_row.evidence_ref,
      'trusted_admission_not_independently_verified_by_this_record_layer',v_actor)
    returning id into v_admission;
    insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,field,new_value,cause,
      agent_rationale,idempotency_key)
    values(now(),v_actor,p_verb,'foundation_assurance_minimum',v_id,'minimum_outcome',
      jsonb_build_object('receipt_digest',v_receipt_digest,'evidence_digest',v_evidence,
        'admission_id',v_admission),'automation_job',
      'WR-000095 exact minimum join admitted after all nine independent inputs passed',p_idempotency_key::text)
    returning id into v_event;
    v_result:=jsonb_build_object('ok',true,'outcome_id',v_id,'receipt_digest',v_receipt_digest,
      'evidence_digest',v_evidence,'admission_id',v_admission,'event_id',v_event,'replayed',false);
  else
    v_result:=jsonb_build_object('ok',true,'production_id',v_id,'verb',p_verb,
      'artifact_digest',v_digest,'evidence_digest',v_evidence,'replayed',false);
  end if;
  insert into ops.foundation_assurance_production(id,idempotency_key,verb,kind,evidence_digest,
    actor_id,session_ref,artifact,artifact_digest,admission_id,event_id,result)
  values(v_id,p_idempotency_key,p_verb,v_kind,v_evidence,v_actor,p_identity->>'session_ref',
    p_produced,v_digest,v_admission,v_event,v_result);
  return v_result;
end $$;

revoke all on function ops.foundation_assurance_record_production(text,uuid,jsonb,jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_foundation_assurance_oracle;
grant execute on function ops.foundation_assurance_record_production(text,uuid,jsonb,jsonb)
  to carr_foundation_assurance_oracle;
