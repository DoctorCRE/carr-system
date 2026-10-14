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
  v_can_modify boolean; v_can_truncate boolean;

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

  if left(v_canonical, 34) <> '["doctorcre:benchmark-payload:v1",' then
    raise exception 'the hashed preimage is not the r7 [domain_tag, payload] array: %', left(v_canonical, 60);
  end if;
  if right(v_canonical, 1) <> ']' then
    raise exception 'the hashed preimage is not a closed array';
  end if;
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
  if v_payload -> 'slo_thresholds' <> ops.benchmark_slo_thresholds()
     or v_payload -> 'cost_variance_thresholds' <> ops.benchmark_cost_variance_thresholds()
     or v_payload -> 'deadline_contract' <> ops.benchmark_deadline_contract() then
    raise exception 'the payload preimage does not carry the fixed r7 constants';
  end if;
  if (v_payload -> 'deadline_contract' ->> 'maximum_external_blocker_pause_days')::integer <> 5 then
    raise exception 'the manifest deadline contract must carry the pause budget in DAYS';
  end if;

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

  if ops.benchmark_utf16_length('abc') <> 3
     or ops.benchmark_utf16_length(v_astral) <> 2
     or char_length(v_astral) <> 1 then
    raise exception 'ops.benchmark_utf16_length does not measure the way JavaScript String#length measures';
  end if;
  begin
    v_draft2 := ops.benchmark_propose_manifest_draft(v_ref, 9, gen_random_uuid(), v_placeholder,
      jsonb_set(v_scalars, '{outlier_rule}', to_jsonb(repeat(v_astral, 200))),
      v_dimensions, v_workloads, v_sizes, v_concurrency, v_browsers, v_evaluators);
    raise exception 'an outlier rule of 400 UTF-16 code units was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'UTF-16 code units' then raise; end if;
  end;

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

  if (select count(*) from ops.job) <> v_jobs
     or (select count(*) from ops.engineering_execution_envelope) <> v_envelopes
     or (select count(*) from ops.capability_agent_session) <> v_sessions then
    raise exception 'a benchmark draft created an executable effect';
  end if;

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

  perform set_config('carr.acting_actor_slug', v_reviewer, true);

  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', null::text, 'no measurements', null::jsonb);
    raise exception 'a passing review naming no measurement set was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'unattested pass is refused' then raise; end if;
  end;

  begin
    perform ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
      'pass', v_measurements, 'a pass with evidence named and nothing attesting it', null::jsonb);
    raise exception 'a passing review with no coverage attestation was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'unattested pass is refused' then raise; end if;
  end;

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

  if not exists (select 1 from pg_constraint where conname = 'benchmark_review_pass_binds_measurements') then
    raise exception 'the benchmark_review_pass_binds_measurements constraint is missing; the write function alone does not bind a direct insert';
  end if;

  v_review := ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
    'pass', v_measurements, 'synthetic independent review: matrix covered',
    jsonb_build_object('coverage_proved_by', v_evaluator,
      'benchmark_payload_digest', v_digest,
      'measurement_set_digest', v_measurements,
      'evaluation_digest', 'sha256:' || repeat('a', 64)));
  if v_review is null then
    raise exception 'an independent passing review was not recorded';
  end if;

  set constraints all immediate;
  set constraints all deferred;

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

  v_review2 := ops.benchmark_review_manifest_draft(v_draft, gen_random_uuid(), v_digest,
    'fail', null::text, 'synthetic second review: recorded for the ordering fixture', null::jsonb);

  -- created_at defaults to now(), which is TRANSACTION START TIME: both reviews
  -- above carry the same instant, so ordering by created_at alone leaves the
  -- readback list at the mercy of the plan. This asserts the tie is real, and
  -- the readback group below asserts (created_at, id) resolves it.
  select count(distinct created_at) into v_instants
    from ops.benchmark_manifest_review where draft_id = v_draft;
  if v_instants <> 1 then
    raise exception 'the two reviews do not share an instant; this fixture no longer exercises the tiebreak';
  end if;

  -- THE FAIL-OPEN THIS REPLACES, SHOWN RATHER THAN DESCRIBED. `x <> NULL` is
  -- NULL, `NULL or NULL` is NULL, and `if NULL then raise ... end if` does not
  -- fire. A guard written that way admits an acceptance that bound NOTHING --
  -- which is exactly the state an unimplemented or half-implemented reader
  -- produces, so the failure mode arrived precisely when it mattered most.
  if (('sha256:' || repeat('7', 64)) <> null::text) is not null then
    raise exception 'a comparison against NULL is no longer NULL; this fixture no longer describes the fail-open it guards';
  end if;

  begin
    perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'outcome digest',
      'sha256:' || repeat('7', 64), null::text);
    raise exception 'an underived Gate Zero outcome digest was accepted as a match';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'was not derived' then raise; end if;
  end;
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
  perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'outcome digest',
    'sha256:' || repeat('7', 64), 'sha256:' || repeat('7', 64));

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
    if v_binding ? 'evaluation_digest' then
      raise exception 'the coverage binding reader returns an evaluation digest nothing binds';
    end if;
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'permission denied' then raise; end if;
    raise notice 'the coverage binding reader is not executable from this session; its return is checked only where it is reachable.';
  end;

  begin
    perform ops.benchmark_measurement_coverage_binding(v_review2);
    raise exception 'the coverage binding reader returned a proof for a review that attested nothing';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'none is recorded' and v_err !~ 'permission denied' then
      raise exception 'the coverage binding reader refused for an unexpected reason: %', v_err;
    end if;
  end;

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
    if ops.benchmark_payload_digest(v_draft) <> v_digest then
      raise exception 'the staged dimension row did not roll back; the rest of this proof would be about different bytes';
    end if;
  else
    raise notice 'SKIPPED: this session (%) cannot insert a benchmark content row, so the stale-attestation case is not staged here; it is exercised under the table owner.', current_user;
  end if;

  if not exists (
    select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'benchmark_manifest_review'
       and t.tgname = 'benchmark_pass_requires_attestation' and t.tgdeferrable) then
    raise exception 'the deferred benchmark_pass_requires_attestation constraint trigger is missing; the write function alone does not bind the table owner';
  end if;

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
    if has_table_privilege(v_role, 'ops.benchmark_manifest_draft', 'insert')
       or has_table_privilege(v_role, 'ops.benchmark_manifest_review', 'insert')
       or has_table_privilege(v_role, 'ops.benchmark_measurement_coverage_attestation', 'insert')
       or has_table_privilege(v_role, 'ops.benchmark_manifest_acceptance_receipt', 'insert') then
      raise exception 'role % holds direct INSERT on a benchmark table', v_role;
    end if;
    if has_table_privilege(v_role, 'ops.benchmark_manifest_review', 'truncate')
       or has_table_privilege(v_role, 'ops.benchmark_measurement_coverage_attestation', 'truncate') then
      raise exception 'role % can TRUNCATE a benchmark table', v_role;
    end if;
  end loop;

  if exists (select 1 from pg_roles where rolname = 'carr_writer')
     and has_function_privilege('carr_writer', 'ops.benchmark_accept_manifest_draft(uuid,uuid,text,uuid,text)', 'execute') then
    raise exception 'the writer bundle can execute benchmark acceptance';
  end if;

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

  foreach v_field in array array[
    'benchmark_acceptance_after_gate_zero', 'benchmark_acceptance_after_portfolio'
  ] loop
    if not exists (select 1 from pg_constraint where conname = v_field) then
      raise exception 'the % ordering constraint is missing; a trigger alone can be bypassed by a future direct insert', v_field;
    end if;
  end loop;

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
  if v_review < v_review2 then v_first_verdict := 'pass'; else v_first_verdict := 'fail'; end if;
  if v_readback -> 'reviews' -> 0 ->> 'verdict' <> v_first_verdict then
    raise exception 'the readback reviews are not ordered by (created_at, id); two reviews sharing an instant came back in an unstable order';
  end if;
  if (v_readback -> 'measurement_coverage_binding' ->> 'resolved')::boolean is distinct from true then
    raise exception 'the readback does not report the measurement coverage proof binding as resolved';
  end if;
  if v_readback -> 'measurement_coverage_binding' ->> 'note' !~* 'evaluates no coverage itself'
     or v_readback -> 'measurement_coverage_binding' ->> 'note' !~* 'not an independent verification' then
    raise exception 'the readback does not state the limit of the coverage attestation: %',
      v_readback -> 'measurement_coverage_binding' ->> 'note';
  end if;
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
