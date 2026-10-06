-- DoctorCRE v5 benchmark acceptance rail: transaction-scoped PostgreSQL proof.
--
-- EVERY FIXTURE ROW IS ROLLED BACK, AND NO BENCHMARK IS ACCEPTED BY RUNNING
-- THIS FILE. It cannot be: acceptance fails closed on the unresolved Gate Zero
-- binding, which the last group below asserts directly, along with the fact that
-- the acceptance receipt table is still empty afterwards. The measurement
-- coverage binding is now bound and this file proves it; that changed the number
-- of reasons acceptance refuses from two to one and changed nothing about
-- whether it refuses.
--
-- THE MANIFEST IS SYNTHETIC ON PURPOSE. The real benchmark subject -- Joe's
-- routes, devices, hardware and cost matrix -- does not exist in any
-- authenticated source yet, and this proof is about the mechanism, not about the
-- real benchmark. Nothing here is a proposal of a real manifest.
--
-- EXPLICIT PREREQUISITES, checked before anything is attempted:
--   * ops.benchmark_propose_manifest_draft must exist. It does not until
--     ops/benchmark-acceptance.candidate.sql has been applied, which is a
--     separate reviewed act; this file SKIPS with a notice rather than failing
--     when it is absent, exactly as work-portfolio-postgres.sql does.
--   * ops.portfolio_writer_actor_id and ops.portfolio_accepted_revision must
--     exist (migration 0496). The benchmark rail reuses both rather than
--     restating them.
--   * Two distinct active NON-HUMAN actors must already exist, for the proposer
--     and the independent reviewer. This file CREATES NO ROLE AND NO ACTOR:
--     writing as a human additionally requires the verified-partner context the
--     server sets, and minting either here would be manufacturing the identity
--     the rail exists to derive.
--
-- What it proves, none of which can be shown by reading SQL text:
--   * the payload digest is recomputed from the persisted rows and is stable
--   * a wrong stored digest, an incomplete payload and a wrong weight total are
--     each refused at the commit check
--   * list ORDER participates in the digest: two drafts with the same set of
--     cache states in a different order hash differently
--   * a draft is inert -- no job, execution envelope or capability session
--   * update, delete and TRUNCATE are refused
--   * review refuses a stale digest, a proposer's self-pass, a pass that names
--     no measurement set, and a pass that attests no coverage
--   * the coverage proof binding RETURNS for an attested review and REFUSES for
--     an unattested one and for one whose draft has outgrown its attestation
--   * ACCEPTANCE FAILS CLOSED, the receipt table stays empty, and the private
--     Gate Zero reader is executable by none of the runtime role bundles
--
-- TWO GROUPS ARE GUARDED AND ANNOUNCE SKIPPED, and it is worth knowing why
-- before reading a green run as a full pass. The append-only and TRUNCATE cases
-- need privileges the runtime bundles do not hold: under carr_writer they abort
-- with insufficient-privilege BEFORE the trigger they exist to exercise is
-- reached, which proves the grant half and says nothing about the trigger half.
-- The trigger half is the one that binds the TABLE OWNER, from whom TRUNCATE
-- cannot be revoked at all. Those groups therefore check the privilege first and
-- say SKIPPED rather than pretending; the triggers' presence is asserted
-- structurally either way. Run this file as the table owner to exercise them.
--
-- The regression fixtures added after the first review are grouped and labelled
-- B1/B2/B3 below so each one can be traced to the finding it exists for:
--   * B1 -- a binding comparison written as `new.x <> derived` evaluates to NULL
--     when the derived side is null and the IF never fires. The fixture calls
--     ops.benchmark_assert_bound() with the exact null shapes that used to fall
--     through, for the Gate Zero binding and the portfolio binding alike, and
--     demonstrates the fail-open beside it so the two are visible together.
--   * B2 -- the review function records the measurement digest a TRUSTED WRITER
--     supplies, and cannot tell a kernel-proved digest from an asserted one. The
--     fixture writes a passing review with an arbitrary digest (which is still
--     admitted: trusted-writer authority is preserved on purpose) and shows what
--     the coverage attestation did and did not change. It did not make this
--     database able to check those bytes -- they are not here. What it changed is
--     that the writer can no longer record that pass ANONYMOUSLY: the attestation
--     is required, attributed to the writer's own actor id, closed to a named
--     evaluator, and bound to a payload digest this database recomputes.
--   * B3 -- the Gate Zero refusal must be about the binding available HERE. The
--     fixture asserts the message scopes itself to this record layer and does not
--     assert that Gate Zero produced no outcome; the external pre-v5 producer is
--     intentional and nothing here asks for a registry entry.
-- Plus: the 2^53-1 bytes ceiling, UTF-16/codepoint length parity, review
-- idempotency over review_summary, the (created_at, id) review order, and the
-- two halves of the freeze/acceptance lock protocol.
--
-- WHAT THIS FILE DOES NOT PROVE, named rather than implied: that
-- ops.benchmark_payload_digest and benchmark-minimum.v5.js's
-- benchmarkPayloadDigest produce the SAME hash for the same manifest. Both
-- sides are asserted to hash the canonical [domain_tag, payload] array of the
-- twenty-six r7 fields, and the SQL side reuses ops.portfolio_canonical_json,
-- which migration 0496 already reconciles against the module canonicalJson --
-- but a rollback-only fixture cannot execute JavaScript, so the cross-language
-- equality is asserted structurally here and remains live-integration
-- verification. It is the single most important thing to check first when this
-- rail is exercised end to end.
--
-- Digests are LEARNED in throwaway subtransactions. A PL/pgSQL exception block
-- is a subtransaction: its database writes roll back while the variables it
-- assigned survive, so the learned digest describes exactly the rows the real
-- proposal then carries.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_draft uuid; v_draft2 uuid;
  v_digest text; v_digest_reordered text;
  v_review uuid; v_review2 uuid; v_err text; v_canonical text; v_payload jsonb; v_readback jsonb;
  v_jobs bigint; v_envelopes bigint; v_sessions bigint;
  v_proposer text; v_reviewer text; v_role text; v_field text; v_actor_count integer;
  v_gate_zero_message text; v_first_verdict text; v_instants integer; v_definition text;
  v_binding jsonb;
  -- A6. The negative cases below need privileges the runtime bundles do not
  -- hold. Under carr_writer an UPDATE aborts with insufficient-privilege before
  -- the append-only trigger is ever reached, which proves nothing about
  -- append-only and looks exactly like the trigger working. These are read once
  -- and the guarded groups announce SKIPPED rather than pretending.
  v_can_modify boolean; v_can_truncate boolean;

  -- The measurement digest the synthetic independent review names. Arbitrary on
  -- purpose -- see B2 -- and the attestation below attests to it, which is what
  -- makes B2 sharper rather than weaker: a trusted writer can still name bytes
  -- nobody here checked, and now has to say so under its own actor id.
  v_measurements   constant text := 'sha256:' || repeat('6', 64);
  v_evaluator      constant text := 'benchmark-minimum.v5.js#evaluateBenchmarkWorkloadCoverage';

  -- One astral codepoint. char_length counts it once; JavaScript's String#length
  -- counts the surrogate pair as two, and that gap is the whole finding.
  v_astral         constant text := U&'\+01F600';

  v_ref            constant text := 'BENCH-SYNTHETIC-A00';
  v_learn          constant text := 'benchmark-acceptance-proof-learn-rollback';
  v_placeholder    constant text := 'sha256:' || repeat('0', 64);
  v_scalars constant jsonb := jsonb_build_object(
    'subject_digest', 'sha256:' || repeat('1', 64),
    'candidate_digest', 'sha256:' || repeat('2', 64),
    'policy_digest', 'sha256:' || repeat('3', 64),
    'cost_expectation_matrix_digest', 'sha256:' || repeat('4', 64),
    'samples_per_cell', 20,
    'warmup_runs', 1,
    'p95_aggregation_method', 'nearest-rank-per-required-cell-all-cells-must-pass',
    'outlier_rule', 'synthetic fixture rule: discard no sample');
  v_dimensions constant jsonb := '[
    {"dimension":"acknowledgement_endpoints","ordinal":0,"value":"/synthetic/ack"},
    {"dimension":"arrival_patterns","ordinal":0,"value":"steady"},
    {"dimension":"cache_states","ordinal":0,"value":"cold"},
    {"dimension":"cache_states","ordinal":1,"value":"warm"},
    {"dimension":"capacity_profiles","ordinal":0,"value":"baseline"},
    {"dimension":"comparator_versions","ordinal":0,"value":"synthetic-comparator-1"},
    {"dimension":"device_profiles","ordinal":0,"value":"synthetic-device"},
    {"dimension":"hardware_profiles","ordinal":0,"value":"synthetic-hardware"},
    {"dimension":"network_profiles","ordinal":0,"value":"synthetic-network"},
    {"dimension":"routes","ordinal":0,"value":"/synthetic"},
    {"dimension":"runtime_versions","ordinal":0,"value":"synthetic-runtime-1"}
  ]'::jsonb;
  -- The same two cache states, in the other order. Everything else is identical.
  v_dimensions_reordered constant jsonb := '[
    {"dimension":"acknowledgement_endpoints","ordinal":0,"value":"/synthetic/ack"},
    {"dimension":"arrival_patterns","ordinal":0,"value":"steady"},
    {"dimension":"cache_states","ordinal":0,"value":"warm"},
    {"dimension":"cache_states","ordinal":1,"value":"cold"},
    {"dimension":"capacity_profiles","ordinal":0,"value":"baseline"},
    {"dimension":"comparator_versions","ordinal":0,"value":"synthetic-comparator-1"},
    {"dimension":"device_profiles","ordinal":0,"value":"synthetic-device"},
    {"dimension":"hardware_profiles","ordinal":0,"value":"synthetic-hardware"},
    {"dimension":"network_profiles","ordinal":0,"value":"synthetic-network"},
    {"dimension":"routes","ordinal":0,"value":"/synthetic"},
    {"dimension":"runtime_versions","ordinal":0,"value":"synthetic-runtime-1"}
  ]'::jsonb;
  v_workloads constant jsonb := jsonb_build_array(jsonb_build_object(
    'ordinal', 0, 'workload_id', 'synthetic-core', 'weight_basis_points', 10000,
    'operation_mix_digest', 'sha256:' || repeat('5', 64)));
  v_workloads_underweight constant jsonb := jsonb_build_array(jsonb_build_object(
    'ordinal', 0, 'workload_id', 'synthetic-core', 'weight_basis_points', 9999,
    'operation_mix_digest', 'sha256:' || repeat('5', 64)));
  v_sizes constant jsonb := '[
    {"ordinal":0,"percentile":50,"bytes":1024},
    {"ordinal":1,"percentile":95,"bytes":8192}
  ]'::jsonb;
  v_concurrency constant jsonb := '[{"ordinal":0,"concurrency_level":1}]'::jsonb;
  v_browsers constant jsonb := '[{"ordinal":0,"name":"synthetic","version":"1","build":"1"}]'::jsonb;
  v_evaluators constant jsonb := '[{"ordinal":0,"actor_id":"synthetic-evaluator",
    "session_ref":"session:a00-postgres-evaluator","authority_class":"synthetic_oracle"}]'::jsonb;
begin
  -- --- prerequisites -------------------------------------------------------
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'benchmark_propose_manifest_draft') then
    raise notice 'SKIPPED: ops.benchmark_propose_manifest_draft is absent; ops/benchmark-acceptance.candidate.sql has not been applied here yet.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'portfolio_writer_actor_id')
     or not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'ops' and p.proname = 'portfolio_accepted_revision') then
    raise notice 'SKIPPED: migration 0496 is absent; the benchmark rail reuses its writer context and accepted-portfolio reader.';
    return;
  end if;

  -- Two distinct existing non-human actors. Nothing is created: a human writer
  -- would additionally need the verified-partner context the server sets, and
  -- minting an actor here would manufacture the identity this rail derives.
  select count(*) into v_actor_count from public.actor where active and kind <> 'human';
  if v_actor_count < 2 then
    raise notice 'SKIPPED: fewer than two active non-human actors exist; this proof creates none.';
    return;
  end if;
  select slug into v_proposer from public.actor where active and kind <> 'human'
   order by slug collate "C" limit 1;
  select slug into v_reviewer from public.actor where active and kind <> 'human'
   order by slug collate "C" offset 1 limit 1;

  select count(*) into v_jobs from ops.job;
  select count(*) into v_envelopes from ops.engineering_execution_envelope;
  select count(*) into v_sessions from ops.capability_agent_session;

  perform set_config('carr.acting_actor_slug', v_proposer, true);

  -- --- learn the digest the completed rows produce --------------------------
  begin
    v_draft := ops.benchmark_propose_manifest_draft(v_ref, 1, gen_random_uuid(), v_placeholder,
      v_scalars, v_dimensions, v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
    v_digest := ops.benchmark_payload_digest(v_draft);
    v_canonical := ops.portfolio_canonical_json(jsonb_build_array(
      ops.benchmark_payload_domain_tag(), ops.benchmark_payload_preimage(v_draft)));
    v_payload := ops.benchmark_payload_preimage(v_draft);
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then raise; end if;
  end;

  -- --- the digest preimage is the r7 shape ----------------------------------
  -- Structural, and deliberately not a hard-coded hash: a literal digest in a
  -- fixture proves only that somebody once ran the code, and it would have to be
  -- edited every time the contract legitimately moved. These clauses say what
  -- the bytes MUST be, which is what a reviewer can check against r7.
  if left(v_canonical, 34) <> '["doctorcre:benchmark-payload:v1",' then
    raise exception 'the hashed preimage is not the r7 [domain_tag, payload] array: %', left(v_canonical, 60);
  end if;
  if right(v_canonical, 1) <> ']' then
    raise exception 'the hashed preimage is not a closed array';
  end if;
  -- Exactly the twenty-six payload fields: the thirty manifest fields minus the
  -- four the canonicalization contract excludes, so no artifact hashes its own
  -- digest and no acceptance fact rides inside the accepted bytes.
  if (select count(*) from jsonb_object_keys(v_payload)) <> 26 then
    raise exception 'the payload preimage has % fields, not the 26 r7 payload fields',
      (select count(*) from jsonb_object_keys(v_payload));
  end if;
  foreach v_field in array array[
    'benchmark_manifest_digest', 'accepted_by_identity', 'accepted_at', 'status'
  ] loop
    if jsonb_exists(v_payload, v_field) then
      raise exception 'the acceptance-envelope field % is inside the hashed payload', v_field;
    end if;
  end loop;
  -- The three fixed constant groups are emitted from the immutable functions,
  -- never from a stored row, so a caller-chosen threshold cannot reach a digest.
  if v_payload -> 'slo_thresholds' <> ops.benchmark_slo_thresholds()
     or v_payload -> 'cost_variance_thresholds' <> ops.benchmark_cost_variance_thresholds()
     or v_payload -> 'deadline_contract' <> ops.benchmark_deadline_contract() then
    raise exception 'the payload preimage does not carry the fixed r7 constants';
  end if;
  if (v_payload -> 'deadline_contract' ->> 'maximum_external_blocker_pause_days')::integer <> 5 then
    raise exception 'the manifest deadline contract must carry the pause budget in DAYS';
  end if;

  -- --- a wrong stored digest is refused at the commit check ------------------
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 2, gen_random_uuid(), v_placeholder,
      v_scalars, v_dimensions, v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
    set constraints all immediate;
    raise exception 'a draft storing the wrong payload digest was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'payload digest does not match its rows' then raise; end if;
  end;
  set constraints all deferred;

  -- --- an incomplete payload is refused at the commit check ------------------
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 3, gen_random_uuid(), v_digest,
      v_scalars,
      (select jsonb_agg(d) from jsonb_array_elements(v_dimensions) d where d ->> 'dimension' <> 'routes'),
      v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
    set constraints all immediate;
    raise exception 'a draft declaring no routes was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'declares no routes' then raise; end if;
  end;
  set constraints all deferred;

  -- Only one cache state, so the closed two-value enum is under-declared even
  -- though the dimension is present. minItems 2 over a two-value enum means
  -- both states are required, not that at least one is.
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 4, gen_random_uuid(), v_digest,
      v_scalars,
      (select jsonb_agg(d) from jsonb_array_elements(v_dimensions) d
        where not (d ->> 'dimension' = 'cache_states' and (d ->> 'ordinal')::integer = 1)),
      v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
    set constraints all immediate;
    raise exception 'a draft declaring one cache state was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'must declare both cache states' then raise; end if;
  end;
  set constraints all deferred;

  -- --- the workload weights must total exactly 10000 basis points -----------
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 5, gen_random_uuid(), v_digest,
      v_scalars, v_dimensions, v_workloads_underweight, v_sizes, v_concurrency,
      v_browsers, v_evaluators);
    set constraints all immediate;
    raise exception 'a draft whose workload weights total 9999 was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'basis points, not 10000' then raise; end if;
  end;
  set constraints all deferred;

  -- --- the request-size bytes ceiling ---------------------------------------
  -- bigint is wider than the kernel's domain. validateBenchmarkPayload admits
  -- bytes only as a JavaScript SAFE integer, so 2^53 is the first value a
  -- rebuilt payload could not represent exactly -- it would round to a different
  -- manifest than the rows hold.
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 7, gen_random_uuid(), v_digest,
      v_scalars, v_dimensions, v_workloads,
      '[{"ordinal":0,"percentile":50,"bytes":9007199254740992}]'::jsonb,
      v_concurrency, v_browsers, v_evaluators);
    raise exception 'a request size of 2^53 bytes was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'bytes' then raise; end if;
  end;
  -- The ceiling is a ceiling, not an accidental narrowing: 2^53 - 1 is admitted.
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 8, gen_random_uuid(), v_placeholder,
      v_scalars, v_dimensions, v_workloads,
      '[{"ordinal":0,"percentile":50,"bytes":9007199254740991}]'::jsonb,
      v_concurrency, v_browsers, v_evaluators);
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then
      raise exception 'the largest safe integer was refused as a request size: %', sqlerrm;
    end if;
  end;

  -- --- UTF-16 code units versus codepoints ----------------------------------
  if ops.benchmark_utf16_length('abc') <> 3
     or ops.benchmark_utf16_length(v_astral) <> 2
     or char_length(v_astral) <> 1 then
    raise exception 'ops.benchmark_utf16_length does not measure the way JavaScript String#length measures';
  end if;
  -- 200 astral codepoints: 200 CHARACTERS, which the column's char_length check
  -- admits, and 400 CODE UNITS, which the kernel refuses at 300. Without the
  -- parity bound in the proposal guard this draft would be storable here and
  -- would then fail validateBenchmarkPayload on the way back out -- a row the
  -- record layer holds and the contract does not admit.
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 9, gen_random_uuid(), v_placeholder,
      jsonb_set(v_scalars, '{outlier_rule}', to_jsonb(repeat(v_astral, 200))),
      v_dimensions, v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
    raise exception 'an outlier rule of 400 UTF-16 code units was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'UTF-16 code units' then raise; end if;
  end;

  -- --- list order participates in the digest --------------------------------
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 6, gen_random_uuid(), v_placeholder,
      v_scalars, v_dimensions_reordered, v_workloads, v_sizes, v_concurrency,
      v_browsers, v_evaluators);
    v_digest_reordered := ops.benchmark_payload_digest(v_draft2);
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then raise; end if;
  end;
  if v_digest_reordered = v_digest then
    raise exception 'reordering the cache states must move the payload digest; r7 array order is part of the hash';
  end if;

  -- --- the real proposal ----------------------------------------------------
  v_draft := ops.benchmark_propose_manifest_draft(v_ref, 1, gen_random_uuid(), v_digest,
    v_scalars, v_dimensions, v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
  set constraints all immediate;
  set constraints all deferred;

  if ops.benchmark_payload_digest(v_draft) <> v_digest then
    raise exception 'the payload digest is not stable across identical rows';
  end if;
  if not ops.benchmark_draft_structure_valid(v_draft) then
    raise exception 'the synthetic manifest did not validate';
  end if;
  if ops.benchmark_draft_integrity_error(v_draft) is not null then
    raise exception 'a freshly proposed draft reports an integrity error: %',
      ops.benchmark_draft_integrity_error(v_draft);
  end if;

  -- --- an exact idempotent replay returns the same draft --------------------
  -- A SEPARATE benchmark reference, because (benchmark_ref, payload_digest) is
  -- unique: the real proposal above already holds this payload under v_ref, and
  -- reusing it here would prove a uniqueness collision rather than idempotency.
  declare
    v_key constant uuid := gen_random_uuid();
    v_replay_ref constant text := v_ref || '-REPLAY';
    v_replay uuid;
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_replay_ref, 1, v_key, v_digest,
      v_scalars, v_dimensions, v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
    v_replay := ops.benchmark_propose_manifest_draft(v_replay_ref, 1, v_key, v_digest,
      v_scalars, v_dimensions, v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
    if v_replay <> v_draft2 then
      raise exception 'an exact idempotent replay created a second draft';
    end if;
    -- The same key with different content is a different request wearing the
    -- same name, and is refused rather than silently returning the first row.
    begin
      v_replay := ops.benchmark_propose_manifest_draft(v_replay_ref, 2, v_key, v_digest,
        v_scalars, v_dimensions, v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
      raise exception 'an idempotency key reused for a different draft was not refused';
    exception when others then
      get stacked diagnostics v_err = message_text;
      if v_err !~ 'already used for a different draft' then raise; end if;
    end;
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then raise; end if;
  end;

  -- --- a draft is inert -----------------------------------------------------
  if (select count(*) from ops.job) <> v_jobs
     or (select count(*) from ops.engineering_execution_envelope) <> v_envelopes
     or (select count(*) from ops.capability_agent_session) <> v_sessions then
    raise exception 'a benchmark draft created an executable effect';
  end if;

  -- --- append-only ----------------------------------------------------------
  -- A GREEN RUN UNDER THE WRONG ROLE IS NOT A PASS, WHICH IS WHY THIS IS GUARDED.
  -- The grants in the candidate SQL revoke UPDATE, DELETE and TRUNCATE from
  -- every runtime bundle, so under carr_writer these statements abort with
  -- insufficient-privilege BEFORE the append-only trigger is reached. That
  -- refusal is real but it is a different refusal: it proves the grant half and
  -- says nothing about the trigger half, which is precisely the half that binds
  -- the table owner. Running them anyway would either fail the whole proof for
  -- an expected reason or -- worse, if the message check were ever loosened --
  -- report the grant refusal as evidence of append-only. So the privilege is
  -- read first and the group announces SKIPPED when it is absent.
  v_can_modify := has_table_privilege(current_user, 'ops.benchmark_manifest_draft', 'update')
              and has_table_privilege(current_user, 'ops.benchmark_manifest_dimension', 'delete');
  v_can_truncate := has_table_privilege(current_user, 'ops.benchmark_manifest_draft', 'truncate');

  if not v_can_modify then
    raise notice 'SKIPPED: this session (%) holds no UPDATE/DELETE on the benchmark tables, so the append-only TRIGGER cannot be exercised here; what a run under this role shows is the grant half only. Re-run as the table owner to exercise the trigger.', current_user;
  else
    begin
      update ops.benchmark_manifest_draft set draft_version = 99 where id = v_draft;
      raise exception 'update of a benchmark draft was not refused';
    exception when others then
      get stacked diagnostics v_err = message_text;
      if v_err !~ 'append-only' then raise; end if;
    end;
    begin
      delete from ops.benchmark_manifest_dimension where draft_id = v_draft;
      raise exception 'delete of a benchmark dimension was not refused';
    exception when others then
      get stacked diagnostics v_err = message_text;
      if v_err !~ 'append-only' then raise; end if;
    end;
  end if;

  -- TRUNCATE, WHICH IS THE ONE THE REVOKE CANNOT COVER. It cannot be revoked
  -- from the table owner and a row-level trigger never sees it, so before the
  -- statement-level trigger was installed beside the row-level one this was the
  -- gap: every runtime bundle refused, and the owner could empty the table. The
  -- guard is the same as above and matters more here, because the ONLY session
  -- that can prove this is the one that could otherwise do the damage.
  if not v_can_truncate then
    raise notice 'SKIPPED: this session (%) cannot TRUNCATE the benchmark tables, so the statement-level append-only trigger cannot be exercised here. It is asserted structurally below and proved only under the table owner.', current_user;
  else
    begin
      truncate ops.benchmark_manifest_dimension;
      raise exception 'TRUNCATE of a benchmark content table was not refused; the revoke does not bind the table owner and a row-level trigger never sees TRUNCATE';
    exception when others then
      get stacked diagnostics v_err = message_text;
      if v_err !~ 'append-only' then raise; end if;
    end;
  end if;

  -- STRUCTURAL, so a run under any role still catches the trigger being dropped.
  -- Both triggers, on all ten relations: the row-level one for UPDATE/DELETE and
  -- the statement-level one for TRUNCATE. Losing either silently reopens exactly
  -- one of the two holes.
  foreach v_field in array array[
    'benchmark_manifest_draft', 'benchmark_manifest_dimension', 'benchmark_manifest_workload',
    'benchmark_manifest_request_size', 'benchmark_manifest_concurrency',
    'benchmark_manifest_browser', 'benchmark_manifest_evaluator',
    'benchmark_manifest_review', 'benchmark_measurement_coverage_attestation',
    'benchmark_manifest_acceptance_receipt'
  ] loop
    if not exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_field
         and t.tgname = v_field || '_append_only' and not t.tgisinternal) then
      raise exception 'ops.% carries no row-level append-only trigger', v_field;
    end if;
    if not exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_field
         and t.tgname = v_field || '_no_truncate' and not t.tgisinternal) then
      raise exception 'ops.% carries no statement-level TRUNCATE trigger; the revoke does not bind the table owner and a row-level trigger never sees TRUNCATE', v_field;
    end if;
  end loop;

  -- --- review guards --------------------------------------------------------
  -- A 'fail' verdict is used for the staleness case: the self-pass and
  -- measurement rules only govern a 'pass', so staleness is the only reason
  -- this can be refused.
  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_placeholder,
      'fail', null::text, 'stale', null::jsonb);
    raise exception 'a review against a stale digest was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'review digest is stale' then raise; end if;
  end;

  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', v_measurements, 'self',
      jsonb_build_object('coverage_proved_by', v_evaluator,
        'benchmark_payload_digest', v_digest,
        'measurement_set_digest', v_measurements,
        'evaluation_digest', 'sha256:' || repeat('a', 64)));
    raise exception 'a proposer self-review pass was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'own benchmark draft' then raise; end if;
  end;

  -- The independent reviewer. Same server-established writer context, a
  -- different actor: the reviewer is derived from it and is never a parameter.
  perform set_config('carr.acting_actor_slug', v_reviewer, true);

  -- A PASS THAT NAMES NO MEASUREMENT SET AND ATTESTS NOTHING IS REFUSED. r7's
  -- pass rule requires every required matrix cell to be exercised and to meet
  -- its fixed SLO, so a passing review has to name the exact evidence it read
  -- AND say what proved it. The reviewer is independent here, so the self-pass
  -- rule cannot be what refuses this.
  --
  -- NOTE WHICH LAYER REFUSES, because it moved. The write function now checks the
  -- pass/attestation pair before it inserts anything, so this arrives at the
  -- attestation refusal rather than at the benchmark_review_pass_binds_measurements
  -- column constraint. That constraint has not been weakened -- it still binds a
  -- direct insert and its presence is asserted structurally below -- it is simply
  -- no longer the first thing this call meets.
  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', null::text, 'no measurements', null::jsonb);
    raise exception 'a passing review naming no measurement set was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'unattested pass is refused' then raise; end if;
  end;

  -- CLAUSE 4, AND THE ONE THAT RETIRES THE AMBIGUITY. A passing review written
  -- by a DIRECT call to this function -- the exact path that used to produce a
  -- digest nobody could distinguish from a proved one -- refuses at write time
  -- unless it says what proved it. Trusted-writer authority is preserved: this
  -- writer may still record a pass. It may no longer record one anonymously.
  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', v_measurements, 'a pass with evidence named and nothing attesting it', null::jsonb);
    raise exception 'a passing review with no coverage attestation was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'unattested pass is refused' then raise; end if;
  end;

  -- AND THE CONVERSE. A fail verdict proves no coverage, so it must attest none:
  -- an attestation beside a failing review would be a proof of something that
  -- did not happen.
  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'fail', null::text, 'a failure carrying a proof',
      jsonb_build_object('coverage_proved_by', v_evaluator,
        'benchmark_payload_digest', v_digest,
        'measurement_set_digest', v_measurements,
        'evaluation_digest', 'sha256:' || repeat('a', 64)));
    raise exception 'a failing review carrying a coverage attestation was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'must record no coverage attestation' then raise; end if;
  end;

  -- THE THREE ATTESTATION NEGATIVES, EACH ITS OWN CASE so a single refusal
  -- cannot stand in for three.
  --
  -- (a) an attestation over bytes the review does not name.
  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', v_measurements, 'attesting to other bytes',
      jsonb_build_object('coverage_proved_by', v_evaluator,
        'benchmark_payload_digest', v_digest,
        'measurement_set_digest', 'sha256:' || repeat('7', 64),
        'evaluation_digest', 'sha256:' || repeat('a', 64)));
    raise exception 'an attestation naming a different measurement set was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'not the set this review names' then raise; end if;
  end;
  -- (b) an attestation against a payload digest this draft does not produce.
  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', v_measurements, 'attesting against other bytes',
      jsonb_build_object('coverage_proved_by', v_evaluator,
        'benchmark_payload_digest', v_placeholder,
        'measurement_set_digest', v_measurements,
        'evaluation_digest', 'sha256:' || repeat('a', 64)));
    raise exception 'an attestation naming a payload digest this draft does not produce was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'which this draft does not produce' then raise; end if;
  end;
  -- (c) an evaluator outside the closed set. This is the clause that keeps
  -- "which evaluator proved this" a constrained fact rather than free text, and
  -- it is checked in three places: here, in the reader, and by the column.
  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', v_measurements, 'attested by something else entirely',
      jsonb_build_object('coverage_proved_by', 'some-other-module.js#proveCoverage',
        'benchmark_payload_digest', v_digest,
        'measurement_set_digest', v_measurements,
        'evaluation_digest', 'sha256:' || repeat('a', 64)));
    raise exception 'an attestation naming an unadmitted evaluator was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'not a benchmark coverage evaluator this rail admits' then raise; end if;
  end;
  -- (d) the attestation shape is CLOSED, like every other shape in this rail: an
  -- unknown key is a caller believing it recorded something that was dropped.
  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', v_measurements, 'an attestation with an extra field',
      jsonb_build_object('coverage_proved_by', v_evaluator,
        'benchmark_payload_digest', v_digest,
        'measurement_set_digest', v_measurements,
        'evaluation_digest', 'sha256:' || repeat('a', 64),
        'verified', true));
    raise exception 'an attestation carrying an unknown field was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'unknown field' then raise; end if;
  end;

  -- The column constraint the first case above used to meet is still there, and
  -- still binds a direct insert that never reaches the write function.
  if not exists (select 1 from pg_constraint where conname = 'benchmark_review_pass_binds_measurements') then
    raise exception 'the benchmark_review_pass_binds_measurements constraint is missing; the write function alone does not bind a direct insert';
  end if;

  -- B2. THIS DIGEST IS ARBITRARY, AND IT IS ADMITTED ON PURPOSE. Nothing proved
  -- coverage over sha256:6666...; the writer said it read those bytes and this
  -- function recorded that. Trusted-writer authority is preserved deliberately
  -- -- direct INSERT is granted to nobody, so only server-side bundles get here
  -- -- and the record layer evaluates no coverage and never has. What must NOT
  -- follow is an acceptance receipt that reads this column as independently
  -- verified coverage, which the acceptance group below proves it cannot.
  v_review := ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
    'pass', v_measurements, 'synthetic independent review: matrix covered',
    jsonb_build_object('coverage_proved_by', v_evaluator,
      'benchmark_payload_digest', v_digest,
      'measurement_set_digest', v_measurements,
      'evaluation_digest', 'sha256:' || repeat('a', 64)));
  if v_review is null then
    raise exception 'an independent passing review was not recorded';
  end if;

  -- AND THE COMMIT-TIME CHECK ADMITS IT, which is what keeps the refusals above
  -- from being a blanket refusal that would make every one of them vacuous. The
  -- pass/attestation trigger is DEFERRED -- the attestation row references the
  -- review row and cannot exist before it -- so in a rollback-only fixture it
  -- would otherwise never fire at all. Forcing it immediate here fires it
  -- against the one review that should survive it.
  set constraints all immediate;
  set constraints all deferred;

  -- --- review idempotency binds every stored parameter ----------------------
  -- review_summary included. A replay that matched on the digests but carried
  -- different prose used to return the first row while the caller believed the
  -- second had been recorded.
  declare
    v_key constant uuid := gen_random_uuid();
    v_replayed uuid;
  begin
    v_replayed := ops.benchmark_review_manifest_draft(v_draft, v_key, v_digest,
      'fail', null::text, 'first summary', null::jsonb);
    if ops.benchmark_review_manifest_draft(v_draft, v_key, v_digest,
         'fail', null::text, 'first summary', null::jsonb) <> v_replayed then
      raise exception 'an exact idempotent review replay created a second review';
    end if;
    begin
      v_replayed := ops.benchmark_review_manifest_draft(v_draft, v_key, v_digest,
        'fail', null::text, 'a different summary entirely', null::jsonb);
      raise exception 'a review idempotency key reused with a different summary was not refused';
    exception when others then
      get stacked diagnostics v_err = message_text;
      if v_err !~ 'already used for a different review' then raise; end if;
    end;
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then raise; end if;
  end;

  -- A second recorded review, so the readback's ordering has something to order.
  v_review2 := ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
    'fail', null::text, 'synthetic second review: recorded for the ordering fixture', null::jsonb);

  -- --- the review order needs the primary-key tiebreak ----------------------
  -- created_at defaults to now(), which is TRANSACTION START TIME: both reviews
  -- above carry the same instant, so ordering by created_at alone leaves the
  -- readback list at the mercy of the plan. This asserts the tie is real, and
  -- the readback group below asserts (created_at, id) resolves it.
  select count(distinct created_at) into v_instants
    from ops.benchmark_manifest_review where draft_id = v_draft;
  if v_instants <> 1 then
    raise exception 'the two reviews do not share an instant; this fixture no longer exercises the tiebreak';
  end if;

  -- --- B1: an underived binding refuses instead of comparing to NULL --------
  -- THE FAIL-OPEN THIS REPLACES, SHOWN RATHER THAN DESCRIBED. `x <> NULL` is
  -- NULL, `NULL or NULL` is NULL, and `if NULL then raise ... end if` does not
  -- fire. A guard written that way admits an acceptance that bound NOTHING --
  -- which is exactly the state an unimplemented or half-implemented reader
  -- produces, so the failure mode arrived precisely when it mattered most.
  if (('sha256:' || repeat('7', 64)) <> null::text) is not null then
    raise exception 'a comparison against NULL is no longer NULL; this fixture no longer describes the fail-open it guards';
  end if;

  -- The Gate Zero shape: the reader produced no outcome digest at all.
  begin
    perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'outcome digest',
      'sha256:' || repeat('7', 64), null::text);
    raise exception 'an underived Gate Zero outcome digest was accepted as a match';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'was not derived' then raise; end if;
  end;
  -- The portfolio binding had the same defect, over three columns. Both null
  -- shapes are covered: a null uuid and a null instant, neither of which a
  -- `<>` comparison would have caught.
  begin
    perform ops.benchmark_assert_bound('accepted portfolio constitution', 'revision id',
      gen_random_uuid(), null::uuid);
    raise exception 'an underived portfolio revision id was accepted as a match';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'was not derived' then raise; end if;
  end;
  begin
    perform ops.benchmark_assert_bound('accepted portfolio constitution', 'acceptance instant',
      now(), null::timestamptz);
    raise exception 'an underived portfolio acceptance instant was accepted as a match';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'was not derived' then raise; end if;
  end;
  -- A null on the SUPPLIED side refuses too, and a genuine mismatch still does.
  begin
    perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'outcome digest',
      null::text, 'sha256:' || repeat('7', 64));
    raise exception 'an acceptance naming no outcome digest was accepted';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'names no' then raise; end if;
  end;
  begin
    perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'outcome digest',
      'sha256:' || repeat('7', 64), 'sha256:' || repeat('8', 64));
    raise exception 'a mismatched outcome digest was accepted';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'does not bind the current' then raise; end if;
  end;
  -- And equal values PASS, so the clauses above are a comparison rather than a
  -- blanket refusal that would make every one of them vacuous.
  perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'outcome digest',
    'sha256:' || repeat('7', 64), 'sha256:' || repeat('7', 64));

  -- --- acceptance fails closed ---------------------------------------------
  -- The receipt table must still be empty afterwards. Which refusal arrives
  -- first depends on the session: an ordinary test session is not a partner
  -- authority principal, so ops.authority_actor_slug() may refuse before the
  -- prerequisites are read. Every one of these refusals is terminal and none of
  -- them writes a row, which is the property being proved.
  begin
    perform ops.benchmark_accept_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      v_review, 'WR-NO-SUCH-ACCEPTED-PORTFOLIO');
    raise exception 'a benchmark acceptance succeeded; the Gate Zero and coverage bindings are unresolved and it must fail closed';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'Gate Zero'
       and v_err !~ 'coverage proof binding'
       and v_err !~ 'accepted portfolio constitution'
       and v_err !~ 'authority'
       and v_err !~ 'permission denied' then
      raise exception 'benchmark acceptance refused for an unexpected reason: %', v_err;
    end if;
  end;

  if (select count(*) from ops.benchmark_manifest_acceptance_receipt) <> 0 then
    raise exception 'a benchmark acceptance receipt exists; no benchmark has been accepted';
  end if;
  if ops.benchmark_accepted_draft(v_ref) is not null then
    raise exception 'a benchmark reports as accepted';
  end if;

  -- The Gate Zero reader itself refuses, whether it is reachable from this
  -- session or not. Both outcomes are correct: raising names the missing
  -- binding, and a permission denial is the privacy of the stub working.
  v_gate_zero_message := null;
  begin
    perform ops.benchmark_gate_zero_outcome();
    raise exception 'the Gate Zero reader returned an outcome; this record layer holds no authenticated Gate Zero binding';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err ~ 'permission denied' then
      raise notice 'the Gate Zero reader is not executable from this session; its message is checked only where it is reachable.';
    elsif v_err !~ 'Gate Zero' then
      raise exception 'the Gate Zero reader refused for an unexpected reason: %', v_err;
    else
      v_gate_zero_message := v_err;
    end if;
  end;

  -- B3. THE REFUSAL MUST BE ABOUT THE BINDING AVAILABLE HERE. Gate Zero is an
  -- external pre-v5 step; it runs outside this system, it is intentionally not a
  -- registered v5 producer, and nothing in this rail asks for a producer
  -- registry entry or is entitled to say what Gate Zero did or did not produce.
  -- The refusal is scoped to this record layer, and these clauses fail if a
  -- future edit reintroduces a global claim.
  if v_gate_zero_message is not null then
    if v_gate_zero_message !~* 'record layer'
       or v_gate_zero_message !~* 'authenticated' then
      raise exception 'the Gate Zero refusal does not scope itself to the binding available in this record layer: %',
        v_gate_zero_message;
    end if;
    if v_gate_zero_message ~* 'no (canonical )?gate zero outcome (record )?exists'
       or v_gate_zero_message ~* 'gate zero (has )?(not run|produced no)' then
      raise exception 'the Gate Zero refusal asserts a global absence of a Gate Zero outcome, which this rail cannot observe: %',
        v_gate_zero_message;
    end if;
  end if;

  -- B2, REVISITED. The coverage proof binding reader now READS A RECORD, and it
  -- is still a SEPARATE reader from Gate Zero on purpose: this binding resolving
  -- did not resolve that one, and acceptance above still failed closed.
  --
  -- WHAT B2 STILL SHOWS, and it shows it more sharply than before. The digest
  -- attested for v_review is sha256:6666... -- arbitrary bytes nothing in this
  -- transaction evaluated. The attestation is admitted, because trusted-writer
  -- authority is preserved deliberately and this database cannot read samples it
  -- does not hold. What changed is that the assertion is now attributed, closed
  -- to a named evaluator, and impossible to omit. The database still does not
  -- know that coverage was proved; it knows exactly who said so and about what.
  begin
    v_binding := ops.benchmark_measurement_coverage_binding(v_review);
    if v_binding ->> 'review_id' <> v_review::text then
      raise exception 'the coverage binding reader returned a different review';
    end if;
    if v_binding ->> 'measurement_set_digest' <> v_measurements then
      raise exception 'the coverage binding reader did not return the measurement set the review names';
    end if;
    if v_binding ->> 'coverage_proved_by' <> v_evaluator then
      raise exception 'the coverage binding reader did not return the attested evaluator';
    end if;
    -- The evaluation digest is recorded and deliberately NOT returned: nothing
    -- binds to it, and a field nothing consumes invites a future reader to
    -- consume it as something it is not.
    if v_binding ? 'evaluation_digest' then
      raise exception 'the coverage binding reader returns an evaluation digest nothing binds';
    end if;
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'permission denied' then raise; end if;
    raise notice 'the coverage binding reader is not executable from this session; its return is checked only where it is reachable.';
  end;

  -- AND IT STILL REFUSES FOR A REVIEW WITH NO ATTESTATION. v_review2 is a fail
  -- verdict: it proved no coverage, it recorded none, and asking for its binding
  -- is asking for something that does not exist. This is the refusal the
  -- requirement was quoted under, and it survives the requirement resolving.
  begin
    perform ops.benchmark_measurement_coverage_binding(v_review2);
    raise exception 'the coverage binding reader returned a proof for a review that attested nothing';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'none is recorded' and v_err !~ 'permission denied' then
      raise exception 'the coverage binding reader refused for an unexpected reason: %', v_err;
    end if;
  end;

  -- AND IT REFUSES AN ATTESTATION THE DRAFT HAS OUTGROWN. This is the one check
  -- the database can make entirely on its own, and it is the reason the payload
  -- digest is RECOMPUTED rather than read back: a draft's content can still be
  -- appended to before acceptance, and appending moves the digest its rows
  -- produce. An attestation about the old bytes must stop binding at that moment
  -- rather than silently outliving them.
  --
  -- Guarded and rolled back: direct INSERT is granted to nobody, so only the
  -- table owner can stage this, and the subtransaction puts the draft back so
  -- every later assertion still sees the digest it was written against.
  if has_table_privilege(current_user, 'ops.benchmark_manifest_dimension', 'insert') then
    begin
      insert into ops.benchmark_manifest_dimension(draft_id, dimension, ordinal, value)
      values (v_draft, 'routes', 1, '/synthetic/second');
      if ops.benchmark_payload_digest(v_draft) = v_digest then
        raise exception 'appending a dimension row did not move the payload digest; this fixture no longer stages the case it describes';
      end if;
      begin
        perform ops.benchmark_measurement_coverage_binding(v_review);
        raise exception 'a coverage attestation naming a payload digest the draft no longer produces was still returned as a binding';
      exception when others then
        get stacked diagnostics v_err = message_text;
        if v_err !~ 'no longer produces' and v_err !~ 'permission denied' then
          raise exception 'the coverage binding reader refused a stale attestation for an unexpected reason: %', v_err;
        end if;
      end;
      raise exception '%', v_learn;
    exception when others then
      if sqlerrm <> v_learn then raise; end if;
    end;
    -- The rollback landed: the draft produces the digest it was reviewed under.
    if ops.benchmark_payload_digest(v_draft) <> v_digest then
      raise exception 'the staged dimension row did not roll back; the rest of this proof would be about different bytes';
    end if;
  else
    raise notice 'SKIPPED: this session (%) cannot insert a benchmark content row, so the stale-attestation case is not staged here; it is exercised under the table owner.', current_user;
  end if;

  -- THE PASS/ATTESTATION PAIR IS ALSO ENFORCED AT COMMIT, for the table owner
  -- and for any future write path that forgets. Structural, because a fixture
  -- that rolls back never reaches commit.
  if not exists (
    select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'benchmark_manifest_review'
       and t.tgname = 'benchmark_pass_requires_attestation' and t.tgdeferrable) then
    raise exception 'the deferred benchmark_pass_requires_attestation constraint trigger is missing; the write function alone does not bind the table owner';
  end if;

  -- --- the private reader is granted to nobody ------------------------------
  -- PUBLIC is checked against the catalog rather than through
  -- has_function_privilege, because a function's DEFAULT acl grants EXECUTE to
  -- PUBLIC: the question is whether the revoke in the candidate SQL actually
  -- landed, and only the stored acl answers that.
  foreach v_field in array array[
    'benchmark_gate_zero_outcome', 'benchmark_measurement_coverage_binding'
  ] loop
    if exists (
      select 1 from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
       where n.nspname = 'ops' and p.proname = v_field
         and acl.grantee = 0 and acl.privilege_type = 'EXECUTE') then
      raise exception 'PUBLIC can execute the private reader ops.%', v_field;
    end if;
  end loop;

  foreach v_role in array array['carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority'] loop
    if not exists (select 1 from pg_roles where rolname = v_role) then continue; end if;
    if has_function_privilege(v_role, 'ops.benchmark_gate_zero_outcome()', 'execute') then
      raise exception 'role % can execute the private Gate Zero reader', v_role;
    end if;
    if has_function_privilege(v_role, 'ops.benchmark_measurement_coverage_binding(uuid)', 'execute') then
      raise exception 'role % can execute the private coverage proof binding reader', v_role;
    end if;
    -- Direct INSERT is granted to nobody either, so no derivation can be
    -- stepped around with raw SQL.
    if has_table_privilege(v_role, 'ops.benchmark_manifest_draft', 'insert')
       or has_table_privilege(v_role, 'ops.benchmark_manifest_review', 'insert')
       or has_table_privilege(v_role, 'ops.benchmark_measurement_coverage_attestation', 'insert')
       or has_table_privilege(v_role, 'ops.benchmark_manifest_acceptance_receipt', 'insert') then
      raise exception 'role % holds direct INSERT on a benchmark table', v_role;
    end if;
    -- Nor UPDATE, DELETE or TRUNCATE. The TRUNCATE half is the one the trigger
    -- above has to cover for the owner; for every runtime bundle it is revoked.
    if has_table_privilege(v_role, 'ops.benchmark_manifest_review', 'truncate')
       or has_table_privilege(v_role, 'ops.benchmark_measurement_coverage_attestation', 'truncate') then
      raise exception 'role % can TRUNCATE a benchmark table', v_role;
    end if;
  end loop;

  -- Acceptance reaches the authority bundle only.
  if exists (select 1 from pg_roles where rolname = 'carr_writer')
     and has_function_privilege('carr_writer', 'ops.benchmark_accept_manifest_draft(uuid,uuid,text,uuid,text)', 'execute') then
    raise exception 'the writer bundle can execute benchmark acceptance';
  end if;

  -- --- the freeze and the acceptance share one lock -------------------------
  -- Under read committed a content insert and an acceptance each hold a snapshot
  -- that excludes the other's uncommitted row, so both can commit and leave a
  -- receipt naming a hash its own draft no longer produces. Both sides therefore
  -- lock the DRAFT ROW, in conflicting modes.
  --
  -- A single-transaction fixture cannot demonstrate a race, so what is asserted
  -- here is that BOTH HALVES OF THE PROTOCOL ARE STILL PRESENT: the trigger that
  -- makes every content insert take the lock, on all six content tables, and the
  -- two conflicting lock modes. A lock on one side serializes nothing; removing
  -- either half silently restores the race, and that is what these clauses
  -- guard. Genuine concurrent verification remains integration work.
  foreach v_field in array array[
    'benchmark_manifest_dimension', 'benchmark_manifest_workload',
    'benchmark_manifest_request_size', 'benchmark_manifest_concurrency',
    'benchmark_manifest_browser', 'benchmark_manifest_evaluator'
  ] loop
    if not exists (
      select 1 from pg_trigger t
        join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_field
         and t.tgname = v_field || '_frozen_after_acceptance' and not t.tgisinternal) then
      raise exception 'ops.% carries no freeze trigger, so an insert there would neither see an acceptance nor take the lock', v_field;
    end if;
  end loop;

  select pg_get_functiondef(p.oid) into v_definition
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops' and p.proname = 'benchmark_content_frozen_after_acceptance';
  if v_definition !~* 'benchmark_manifest_draft[^;]*for share' then
    raise exception 'the content freeze no longer takes FOR SHARE on the draft row; its half of the lock protocol is gone';
  end if;
  select pg_get_functiondef(p.oid) into v_definition
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops' and p.proname = 'benchmark_accept_manifest_draft';
  if v_definition !~* 'benchmark_manifest_draft[^;]*for update' then
    raise exception 'benchmark acceptance no longer takes FOR UPDATE on the draft row; the freeze trigger would be locking against nobody';
  end if;

  -- --- the strictly-after ordering is structural ----------------------------
  foreach v_field in array array[
    'benchmark_acceptance_after_gate_zero', 'benchmark_acceptance_after_portfolio'
  ] loop
    if not exists (select 1 from pg_constraint where conname = v_field) then
      raise exception 'the % ordering constraint is missing; a trigger alone can be bypassed by a future direct insert', v_field;
    end if;
  end loop;

  -- --- the readback is honest ----------------------------------------------
  v_readback := ops.benchmark_readback(v_ref);
  if (v_readback ->> 'accepted')::boolean then
    raise exception 'the readback reports an accepted benchmark';
  end if;
  if (v_readback ->> 'clock_started')::boolean then
    raise exception 'the readback claims a started clock; this rail starts none';
  end if;
  if v_readback ->> 'payload_digest' <> v_digest
     or v_readback ->> 'stored_payload_digest' <> v_digest then
    raise exception 'the readback does not report the digest the rows produce';
  end if;
  if (v_readback -> 'effects' ->> 'creates_effect')::boolean then
    raise exception 'the readback claims an executable effect';
  end if;
  if jsonb_array_length(v_readback -> 'reviews') <> 2 then
    raise exception 'the readback does not report both recorded reviews';
  end if;
  -- ORDER BY (created_at, id). Both reviews share created_at -- asserted above --
  -- so the primary key is the only thing deciding this list, and the smaller uuid
  -- must come first. Ordering by created_at alone would leave it plan-dependent.
  if v_review < v_review2 then v_first_verdict := 'pass'; else v_first_verdict := 'fail'; end if;
  if v_readback -> 'reviews' -> 0 ->> 'verdict' <> v_first_verdict then
    raise exception 'the readback reviews are not ordered by (created_at, id); two reviews sharing an instant came back in an unstable order';
  end if;
  -- The readback says out loud that a measurement digest is not verified
  -- coverage, so a caller reading one off a passing review is not entitled to
  -- assume this database checked anything. It did not.
  if (v_readback -> 'measurement_coverage_binding' ->> 'resolved')::boolean is distinct from true then
    raise exception 'the readback does not report the measurement coverage proof binding as resolved';
  end if;
  -- AND IT STILL SAYS WHAT THE BINDING IS NOT. A readback that reported
  -- resolved: true and nothing else would let a caller read an attributed
  -- assertion as a verification this database performed.
  if v_readback -> 'measurement_coverage_binding' ->> 'note' !~* 'evaluates no coverage itself'
     or v_readback -> 'measurement_coverage_binding' ->> 'note' !~* 'not an independent verification' then
    raise exception 'the readback does not state the limit of the coverage attestation: %',
      v_readback -> 'measurement_coverage_binding' ->> 'note';
  end if;
  -- Per review: the passing one names its evaluator, the failing one names none.
  if (select r ->> 'coverage_proved_by' from jsonb_array_elements(v_readback -> 'reviews') r
       where r ->> 'verdict' = 'pass' limit 1) <> v_evaluator then
    raise exception 'the readback does not report the evaluator attested for the passing review';
  end if;
  if (select r ->> 'coverage_proved_by' from jsonb_array_elements(v_readback -> 'reviews') r
       where r ->> 'verdict' = 'fail' limit 1) is not null then
    raise exception 'the readback reports a coverage evaluator for a failing review';
  end if;

  raise notice 'benchmark acceptance PostgreSQL proof passed: digest recomputed from rows and stable, order-sensitive, incomplete and mis-weighted payloads refused, request sizes bounded at 2^53-1, UTF-16 length parity enforced, append-only enforced on update, delete and truncate, review guards and review idempotency refuse, an unattested pass and a mis-attested pass are both refused at write time, the coverage proof binding returns for an attested review and refuses for an unattested or outgrown one, reviews ordered by (created_at, id), an underived binding refuses instead of comparing to NULL, the Gate Zero refusal is scoped to this record layer, both private readers are unreachable from every role bundle, acceptance FAILS CLOSED on the Gate Zero binding, and no acceptance receipt exists.';
end $proof$;

rollback;
