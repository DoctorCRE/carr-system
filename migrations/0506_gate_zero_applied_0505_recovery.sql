-- 0506 — forward recovery after migration 0505 was applied before its source
-- was rewritten on main.
--
-- Production records 0505 with sha256
-- d9494cbdd700c61ca2d273eba802997e3367c4f39b558eb9a80c48eee99962ea.
-- That applied file is immutable and has been restored byte-for-byte. This new
-- migration carries only the catalog/function delta that the reviewed 0505 on
-- merged PR #1019 represented. Intentionally no BEGIN/COMMIT: tools/migrate.py
-- owns the transaction and records this file only after every assertion passes.

comment on column ops.gate_zero_read_only_outcome.candidate_scoped_digest is
  'Informational comparison value: ops.gate_zero_outcome_candidate_digest(receipt), the canonical-JSON sha256 over the receipt minus observed_at, ttl_expires_at and each identity''s per-call session_ref. It is not evidence and not an admission or idempotency key. The unique candidate_digest column arbitrates one immutable row per candidate; a later call returns that recorded row unchanged and reports whether its offered receipt differed.';

comment on function ops.gate_zero_outcome_candidate_projection(jsonb) is
  'One consumer-gate-receipt.v1 reduced by removing observed_at, ttl_expires_at and each identity''s per-call session_ref. It is an informational view used to explain differences between offered and recorded observations. It is not evidence and does not admit or refuse a retry; candidate_digest arbitrates the immutable row.';

comment on function ops.gate_zero_outcome_candidate_digest(jsonb) is
  'Canonical-JSON sha256 over ops.gate_zero_outcome_candidate_projection(receipt). This informational digest can show whether non-time, non-session evidence differs between an offered and recorded observation. It is not evidence or an idempotency key and does not admit or refuse a retry; candidate_digest arbitrates the immutable row.';

-- The SQL consumer independently recomputes the tagged receipt digest before
-- returning the stored row. The return shape and currentness rule are unchanged.
create or replace function ops.benchmark_gate_zero_outcome()
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_row ops.gate_zero_read_only_outcome%rowtype;
  v_any boolean;
  v_recomputed_digest text;
begin
  select * into v_row from ops.gate_zero_read_only_outcome
   where status = 'pass' and ttl_expires_at > now()
   order by observed_at desc, outcome_digest collate "C" desc
   limit 1;
  if not found then
    select exists (select 1 from ops.gate_zero_read_only_outcome) into v_any;
    if v_any then
      raise exception 'benchmark acceptance requires a current passing Gate Zero read-only outcome; every outcome recorded here is non-passing or past its expiry. No caller-supplied, configured or synthetic Gate Zero outcome is accepted.';
    end if;
    raise exception 'benchmark acceptance requires an authenticated Gate Zero read-only outcome binding, and none has been recorded here yet. The record exists (ops.gate_zero_read_only_outcome) and the independent oracle seat writes it; until it does, acceptance fails closed. No caller-supplied, configured or synthetic Gate Zero outcome is accepted.';
  end if;

  v_recomputed_digest := ops.gate_zero_outcome_digest(v_row.receipt);
  if v_row.outcome_digest <> v_recomputed_digest then
    raise exception 'Gate Zero outcome digest divergence: stored %, recomputed % from the tagged consumer-gate-receipt.v1 receipt',
      v_row.outcome_digest, v_recomputed_digest;
  end if;

  return jsonb_build_object(
    'step_ref', v_row.step_ref,
    'outcome_digest', v_row.outcome_digest,
    'observed_at', to_char(v_row.observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
$$;

comment on function ops.benchmark_gate_zero_outcome() is
  'PRIVATE reader for the latest passing, unexpired Gate Zero outcome. Before returning the closed { step_ref, outcome_digest, observed_at } object it recomputes sha256 over canonical JSON ["consumer-gate-receipt.v1", receipt] and refuses if that tagged digest differs from the stored value.';

-- Preserve the candidate-key insert arbiter and the seat-only write boundary.
-- The fallback returns the immutable first row and exposes changed offered bytes
-- through a notice so the gateway can report convergence and heal one event.
create or replace function ops.gate_zero_record_read_only_outcome(
  p_idempotency_key uuid,
  p_receipt jsonb)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor uuid; v_slug text; v_seat text; v_id uuid;
  v_existing ops.gate_zero_read_only_outcome%rowtype;
  v_digest text; v_candidate_scoped_digest text;
  v_field text; v_keys integer; v_identity jsonb; v_identity_keys integer;
begin
  if p_idempotency_key is null then
    raise exception 'recording a Gate Zero read-only outcome requires an idempotency key';
  end if;
  if p_receipt is null or jsonb_typeof(p_receipt) <> 'object' then
    raise exception 'recording a Gate Zero read-only outcome requires one consumer-gate-receipt.v1 object';
  end if;

  v_actor := ops.gate_zero_producer_actor_id();
  v_seat := ops.gate_zero_producer_seat_holder_ref();
  v_slug := split_part(v_seat, ':', 2);

  foreach v_field in array array[
    'gate_id', 'receipt_producer_step_ref', 'subject_digest', 'candidate_digest',
    'policy_digest', 'environment_manifest_digest', 'subject_environment', 'evidence_scope',
    'subject_maker_identity', 'producer_identity', 'evaluator_identity', 'producer_role',
    'independent_oracle_ref', 'oracle_version', 'evidence_ref', 'fixture_set_digest',
    'observed_at', 'ttl_expires_at', 'status', 'comparator', 'negative_admission_result'
  ] loop
    if not (p_receipt ? v_field) then
      raise exception 'the Gate Zero receipt is missing the required consumer-gate-receipt.v1 field %', v_field;
    end if;
  end loop;
  select count(*) into v_keys from jsonb_object_keys(p_receipt);
  if v_keys <> 21 then
    raise exception 'consumer-gate-receipt.v1 is a closed schema: the Gate Zero receipt carries % fields rather than 21', v_keys;
  end if;

  foreach v_field in array array['subject_maker_identity', 'producer_identity', 'evaluator_identity'] loop
    v_identity := p_receipt -> v_field;
    if jsonb_typeof(v_identity) <> 'object' then
      raise exception 'the Gate Zero receipt field % is not an authenticated-receipt-identity.v1 object', v_field;
    end if;
    select count(*) into v_identity_keys from jsonb_object_keys(v_identity);
    if v_identity_keys <> 3
       or not (v_identity ? 'actor_id')
       or not (v_identity ? 'session_ref')
       or not (v_identity ? 'authority_class') then
      raise exception 'authenticated-receipt-identity.v1 is a closed schema: the Gate Zero receipt field % carries % fields rather than exactly actor_id, session_ref and authority_class',
        v_field, v_identity_keys;
    end if;
    if coalesce(v_identity ->> 'actor_id', '') = ''
       or coalesce(v_identity ->> 'authority_class', '') = '' then
      raise exception 'the Gate Zero receipt field % has an empty actor_id or authority_class', v_field;
    end if;
    if coalesce(v_identity ->> 'session_ref', '') !~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$' then
      raise exception 'the Gate Zero receipt field % has a session_ref that is not r7''s authenticated-receipt-identity.v1 pattern: %',
        v_field, coalesce(v_identity ->> 'session_ref', '');
    end if;
  end loop;
  if p_receipt -> 'producer_identity' ->> 'actor_id' <> v_slug
     or p_receipt -> 'evaluator_identity' ->> 'actor_id' <> v_slug then
    raise exception 'the Gate Zero receipt names a producer or evaluator other than the staffed seat lane %', v_slug;
  end if;
  if p_receipt -> 'producer_identity' ->> 'authority_class' <> 'review_agent'
     or p_receipt -> 'evaluator_identity' ->> 'authority_class' <> 'review_agent' then
    raise exception 'the Gate Zero receipt names a producer or evaluator authority class other than review_agent';
  end if;
  if p_receipt -> 'subject_maker_identity' ->> 'actor_id' = v_slug then
    raise exception 'the Gate Zero receipt names the oracle seat as the maker of its own subject; r7 requires the subject maker to differ from the evaluator';
  end if;
  if p_receipt -> 'subject_maker_identity' ->> 'session_ref'
     = p_receipt -> 'evaluator_identity' ->> 'session_ref' then
    raise exception 'the Gate Zero receipt names one session as both subject maker and evaluator';
  end if;

  insert into ops.gate_zero_read_only_outcome (
    idempotency_key, step_ref, receipt_producer_step_ref, gate_id, receipt_schema,
    producer_role, independent_oracle_ref, oracle_version, evidence_scope,
    subject_environment, negative_admission_result, producing_seat_ref, producing_actor_id,
    candidate_digest, subject_digest, policy_digest, environment_manifest_digest,
    fixture_set_digest, evidence_ref, receipt, outcome_digest, candidate_scoped_digest,
    status, comparator, observed_at, ttl_expires_at)
  values (
    p_idempotency_key,
    'step:gate-zero-read-only-outcome',
    p_receipt ->> 'receipt_producer_step_ref',
    p_receipt ->> 'gate_id',
    'consumer-gate-receipt.v1',
    p_receipt ->> 'producer_role',
    p_receipt ->> 'independent_oracle_ref',
    p_receipt ->> 'oracle_version',
    p_receipt ->> 'evidence_scope',
    p_receipt ->> 'subject_environment',
    p_receipt ->> 'negative_admission_result',
    v_seat,
    v_actor,
    p_receipt ->> 'candidate_digest',
    p_receipt ->> 'subject_digest',
    p_receipt ->> 'policy_digest',
    p_receipt ->> 'environment_manifest_digest',
    p_receipt ->> 'fixture_set_digest',
    p_receipt ->> 'evidence_ref',
    p_receipt,
    ops.gate_zero_outcome_digest(p_receipt),
    ops.gate_zero_outcome_candidate_digest(p_receipt),
    p_receipt ->> 'status',
    p_receipt ->> 'comparator',
    (p_receipt ->> 'observed_at')::timestamptz,
    (p_receipt ->> 'ttl_expires_at')::timestamptz)
  on conflict (candidate_digest) do nothing
  returning id into v_id;
  if v_id is not null then
    return v_id;
  end if;

  select * into v_existing from ops.gate_zero_read_only_outcome
   where candidate_digest = p_receipt ->> 'candidate_digest';
  if not found then
    raise exception 'the Gate Zero outcome for candidate % was neither inserted nor found; the record layer is in a state this writer cannot account for',
      p_receipt ->> 'candidate_digest';
  end if;
  -- RETURN THE IMMUTABLE FIRST ROW UNCONDITIONALLY. The outcome and its audit
  -- event use different authenticated connections and transactions; the seat
  -- commits first, so a later outer failure can leave this row eventless. Every
  -- normal retry has new per-call identity and time bytes, and its evidence may
  -- also have moved. Refusing those bytes prevents the retry from reaching the
  -- event write. Returning the existing row changes no state here: the gateway
  -- recomputes and labels recorded versus offered digests before healing exactly
  -- one event under an advisory lock.
  v_digest := ops.gate_zero_outcome_digest(p_receipt);
  v_candidate_scoped_digest := ops.gate_zero_outcome_candidate_digest(p_receipt);
  if v_existing.outcome_digest <> v_digest then
    raise notice 'candidate % already has an immutable outcome; returning it unchanged (recorded full %, offered full %, recorded projection %, offered projection %)',
      v_existing.candidate_digest, v_existing.outcome_digest, v_digest,
      v_existing.candidate_scoped_digest, v_candidate_scoped_digest;
  end if;
  return v_existing.id;
end;
$$;

comment on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) is
  'The only way to record a Gate Zero read-only outcome. The producing seat, actor and outcome digest are derived; the receipt and idempotency key are the only parameters. It admits only the staffed non-human oracle seat and validates the closed receipt and identity schemas. One insert is arbitrated by candidate_digest; its fallback always returns the immutable first row, including when later evidence, identity or time bytes differ, so the caller can report convergence and heal a missing outer audit event. No receipt is replaced. outcome_digest is the tagged digest r7 declares; candidate_scoped_digest is informational only.';

do $verify$
declare
  v_applied_0505 text;
  v_writer text;
  v_reader text;
begin
  select sha256 into v_applied_0505
    from public.schema_migrations
   where filename = '0505_gate_zero_tagged_digest_and_candidate_reads.sql';
  if v_applied_0505 is distinct from
     'd9494cbdd700c61ca2d273eba802997e3367c4f39b558eb9a80c48eee99962ea' then
    raise exception '0506 FAILED: predecessor 0505 ledger digest is %, expected the production-applied d9494c digest',
      coalesce(v_applied_0505, '<missing>');
  end if;

  select pg_get_functiondef('ops.gate_zero_record_read_only_outcome(uuid,jsonb)'::regprocedure)
    into v_writer;
  if position('returning it unchanged (recorded full %, offered full %, recorded projection %, offered projection %)' in v_writer) = 0 then
    raise exception '0506 FAILED: the convergent Gate Zero writer replacement is absent';
  end if;

  select pg_get_functiondef('ops.benchmark_gate_zero_outcome()'::regprocedure)
    into v_reader;
  if position('v_recomputed_digest := ops.gate_zero_outcome_digest(v_row.receipt)' in v_reader) = 0 then
    raise exception '0506 FAILED: the SQL consumer does not recompute the tagged digest';
  end if;

  if not has_function_privilege('carr_gate_zero_producer',
       'ops.gate_zero_record_read_only_outcome(uuid,jsonb)', 'EXECUTE')
     or has_function_privilege('carr_writer',
       'ops.gate_zero_record_read_only_outcome(uuid,jsonb)', 'EXECUTE') then
    raise exception '0506 FAILED: the seat-only Gate Zero writer boundary moved';
  end if;
end
$verify$;
