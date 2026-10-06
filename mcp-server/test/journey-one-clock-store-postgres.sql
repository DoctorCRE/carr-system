-- DoctorCRE v5 Journey 1 clock-history store: transaction-scoped PostgreSQL
-- proof of ops/journey-one-clock-store.candidate.sql.
--
-- THIS FILE HAS NOT BEEN RUN. The local initdb is blocked (shmget), no remote
-- database was used as a workaround, and nothing here is wired into CI. Every
-- claim below is what this fixture WOULD assert; none of it is an observation.
--
-- EVERY FIXTURE ROW IS ROLLED BACK, AND NO JOURNEY 1 CLOCK IS STARTED BY RUNNING
-- THIS FILE. What it appends is a SYNTHETIC history composed by this file, not a
-- kernel computation, and that is the exact point of the direct-writer group
-- below: this record layer cannot tell a trusted writer's assertion from a
-- kernel computation, so the rows it stores prove storage mechanics and nothing
-- about a deadline. A run of this file records no acceptance, no receipt, no
-- admission and no clock start, and it rolls back regardless.
--
-- EXPLICIT PREREQUISITES, checked before anything is attempted. Each one SKIPS
-- with a notice rather than failing, exactly as benchmark-acceptance-postgres.sql
-- does:
--   * ops.j1_clock_append_revision must exist. It does not until
--     ops/journey-one-clock-store.candidate.sql has been applied, which is a
--     separate reviewed act.
--   * ops.portfolio_canonical_json and ops.portfolio_writer_actor_id must exist
--     (migration 0496). This rail reuses both rather than restating them.
--   * One active actor must already exist for the writer context. THIS FILE
--     CREATES NO ROLE AND NO ACTOR: minting either would manufacture the
--     identity the rail exists to derive.
--
-- WHAT IT PROVES, none of which can be shown by reading SQL text:
--   * POSITIVE CAS. A creation with an explicit NULL prior lands as ordinal 0; a
--     second revision naming the head's exact digest lands as ordinal 1; the
--     readback rebuilds both and recomputes the same digest it stored.
--   * The digest is recomputed FROM THE STORED ROWS, and the parameter-form and
--     row-form builders agree for the same content.
--   * TRANSACTION SEQUENCING. Four appends land in ONE transaction, each with
--     its complete children, including one issued after the caller set
--     CONSTRAINTS ALL IMMEDIATE and one issued after a refusal was caught. The
--     append guard is deferred while a revision row is inserted, forced over the
--     settled children, and restored before the call returns; a mode left in
--     force would validate the next revision row against children that do not
--     exist yet and refuse a valid append.
--   * A stale prior, a second creation, a reused idempotency key carrying a
--     changed payload, a rebased origin, a removed miss, a changed seal, a lost
--     event, a forged event digest and a lied-about history digest are each
--     refused BY NAME, with the shared invariant id in the message.
--   * The clock identity is derived: a caller-supplied key that its own origin
--     does not produce is refused, so a self-chosen alias cannot address, and
--     cannot restart, a clock.
--   * ONE AUTHORITATIVE SCOPE HOLDS ONE CLOCK, which is the refusal a NEW ORIGIN
--     meets and the derived identity never could: a second origin for a bound
--     scope, a clock rebound to a second scope, and an append for a clock with
--     no scope binding at all are each refused by name. The scope key is derived
--     from the scope's own SIX IDENTITY FIELDS here, and a scope naming other
--     gates is refused rather than hashed.
--   * THE SCOPE LABEL IS NOT THE IDENTITY: one accepted scope under two names
--     hashes to ONE key, so a relabelled scope presenting a fresh origin meets
--     the clock that scope already holds -- refused before any revision is
--     written -- while a different accepted subject is still a different scope.
--     The label a scope was bound under is recorded once and a later write may
--     not replace it.
--   * A legacy v1 state schema is refused by name rather than migrated.
--   * UPDATE, DELETE and TRUNCATE are refused everywhere (append-only), and a
--     child row cannot be added to a revision a later revision already seals.
--     The truncate half is the only thing that can show the statement-level
--     trigger exists: a row-level trigger never sees TRUNCATE, and the `revoke`
--     does not bind the table owner. The attempts need the privilege they test,
--     so they run as the table owner and SKIP with a notice otherwise; that both
--     triggers exist is asserted from pg_trigger on every run.
--   * Direct INSERT is executable by none of the runtime role bundles, and the
--     append function reaches the writer and authority bundles only.
--   * Every function the module's postgres journal calls exists with the exact
--     arity it calls it at.
--   * The readback says, in its own fields, that this record layer accepted no
--     deadline and lists what it cannot prove.
--   * A tampered readback is detectable: the recomputation is content-sensitive,
--     so the same rows with one field changed produce a different digest, and
--     the integrity reader reports that rather than serving the row.
--
-- WHAT IT DOES NOT PROVE, named rather than implied:
--   * That ops.j1_clock_history_digest and journey-one-clock-store.v5.js's
--     journeyOneClockHistoryDigest produce the SAME hash for the same history.
--     Both are asserted to hash the canonical serialization of the same
--     twenty-field preimage, and this side reuses ops.portfolio_canonical_json,
--     which migration 0496 already reconciles against artifact-trust.js's
--     canonicalJson -- but a SQL fixture cannot execute JavaScript. THIS IS THE
--     SINGLE MOST IMPORTANT THING TO CHECK FIRST when the rail is exercised end
--     to end: run the module's own round trip over a real history, then compare
--     its digest against ops.j1_clock_history_digest_of over the same
--     decomposed content. THE SAME IS TRUE OF THE SCOPE KEY: ops.j1_clock_scope_digest
--     and journeyOneClockScopeKey are asserted to hash the same
--     [doctorcre:j1-clock-scope:v2, the six identity fields] preimage -- the
--     human scope_ref label is excluded on both sides -- and neither can execute the
--     other, so a disagreement there would file one program's clock under two
--     scope keys -- one for each language -- and the uniqueness both rely on
--     would silently hold over two different sets.
--   * Anything about the concurrency of two SESSIONS. One psql session cannot
--     contend with itself, so the freeze/append lock protocol is asserted
--     structurally (both modes are taken on the same row) and remains
--     live-integration verification.
--   * That any stored revision is a kernel computation. It is not: this file
--     composes its own.
--   * That a bound scope is the ACCEPTED scope of the projection some kernel
--     read. The stored state carries no subject, candidate or policy digest, so
--     the scope here is a synthetic binding this file composed, exactly as a
--     trusted integration would compose a real one. One scope holding one clock
--     is enforced; the truth of the scope is not, and is not claimed.
--
-- Digests are LEARNED with ops.j1_clock_history_digest_of, which hashes the
-- decomposed content WITHOUT writing a row, so the fixture never has to guess a
-- hash or read one out of an error message.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_actor           text;
  v_clock_key       text;
  v_wrong_key       text;
  v_digest0         text;
  v_digest1         text;
  v_digest2         text;
  v_digest3         text;
  v_result          jsonb;
  v_readback        jsonb;
  v_revision0       uuid;
  v_revision1       uuid;
  v_revision2       uuid;
  v_err             text;
  v_count           integer;
  v_jobs            bigint := 0;
  v_jobs_after      bigint := 0;
  v_scalars0        jsonb;
  v_scalars1        jsonb;
  v_scalars2        jsonb;
  v_scalars3        jsonb;
  v_scalars_tamper  jsonb;
  v_events0         jsonb;
  v_events1         jsonb;
  v_pauses1         jsonb;
  v_event0          jsonb;
  v_event1          jsonb;
  v_signature       text;
  v_role            text;
  v_relation        text;
  -- Whether THIS role can even reach the append-only triggers. See the group
  -- itself: update, delete and truncate are revoked from every runtime bundle,
  -- so a non-owner meets insufficient-privilege before any trigger fires.
  v_may_attempt_dml boolean := true;
  v_relations       constant text[] := array[
                      'j1_clock', 'j1_clock_scope_binding', 'j1_clock_revision',
                      'j1_clock_revision_pause_interval', 'j1_clock_revision_event'];

  -- The synthetic clock. Every digest is an obvious fixture value; none of them
  -- names a real receipt, and no real receipt exists to name.
  v_tenant          constant text := 'carr-internal';
  v_origin_receipt  constant text := 'sha256:' || repeat('11', 32);
  v_origin_at       constant text := '2026-09-09T15:00:00.000Z';
  v_benchmark       constant text := 'sha256:' || repeat('22', 32);
  v_due             constant text := '2026-10-09T15:00:00.000Z';
  v_other_receipt   constant text := 'sha256:' || repeat('33', 32);
  v_pause_evidence  constant text := 'sha256:' || repeat('44', 32);
  v_completion      constant text := 'sha256:' || repeat('55', 32);
  v_artifact        constant text := 'sha256:' || repeat('66', 32);
  v_fixtures        constant text := 'sha256:' || repeat('77', 32);
  v_verifier        constant text := 'safe:verifier:postgres-proof-synthetic';
  v_provenance      constant jsonb := jsonb_build_object('verifier_ref', 'safe:verifier:postgres-proof-synthetic');
  v_key0            constant uuid := '00000000-0000-4000-8000-00000000c001';
  v_key1            constant uuid := '00000000-0000-4000-8000-00000000c002';
  v_key_dup         constant uuid := '00000000-0000-4000-8000-00000000c003';
  v_key_x           constant uuid := '00000000-0000-4000-8000-00000000c004';
  v_key2            constant uuid := '00000000-0000-4000-8000-00000000c005';
  v_key3            constant uuid := '00000000-0000-4000-8000-00000000c006';

  -- THE AUTHORITATIVE SCOPE, composed by this file exactly as a trusted
  -- integration would compose a real one. It is a synthetic binding: nothing
  -- here proves it is any projection's accepted scope, and the fixture asserts
  -- only that one scope holds one clock.
  v_scope_key       text;
  v_scope           constant jsonb := jsonb_build_object(
                      'benchmark_candidate_digest', 'sha256:' || repeat('92', 32),
                      'benchmark_policy_digest', 'sha256:' || repeat('93', 32),
                      'benchmark_subject_digest', 'sha256:' || repeat('91', 32),
                      'clock_origin_gate_id', 'foundation-assurance-minimum-accepted',
                      'clock_terminus_gate_id', 'journey-one-kernel-production-accepted',
                      'scope_ref', 'safe:clock-scope:postgres-proof-journey-one',
                      'tenant', 'carr-internal');
  -- THE SAME ACCEPTED SCOPE UNDER ANOTHER NAME. Identical tenant, benchmark
  -- subject, candidate and policy digests and gate ids; only the human label
  -- differs. It must hash to the SAME key, because a label is provenance and not
  -- identity -- when it was in the preimage, relabelling produced a second scope
  -- key and a caller could bind a second clock for one program under it.
  v_scope_relabelled constant jsonb := jsonb_build_object(
                      'benchmark_candidate_digest', 'sha256:' || repeat('92', 32),
                      'benchmark_policy_digest', 'sha256:' || repeat('93', 32),
                      'benchmark_subject_digest', 'sha256:' || repeat('91', 32),
                      'clock_origin_gate_id', 'foundation-assurance-minimum-accepted',
                      'clock_terminus_gate_id', 'journey-one-kernel-production-accepted',
                      'scope_ref', 'safe:clock-scope:postgres-proof-journey-one-relabelled',
                      'tenant', 'carr-internal');
  -- A DIFFERENT accepted scope, for the second synthetic clock below. Two
  -- clocks are two scopes; one scope is never two clocks.
  v_scope_second    constant jsonb := jsonb_build_object(
                      'benchmark_candidate_digest', 'sha256:' || repeat('94', 32),
                      'benchmark_policy_digest', 'sha256:' || repeat('95', 32),
                      'benchmark_subject_digest', 'sha256:' || repeat('96', 32),
                      'clock_origin_gate_id', 'foundation-assurance-minimum-accepted',
                      'clock_terminus_gate_id', 'journey-one-kernel-production-accepted',
                      'scope_ref', 'safe:clock-scope:postgres-proof-journey-one-second',
                      'tenant', 'carr-internal');
begin
  -- --- prerequisites -------------------------------------------------------
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'j1_clock_append_revision') then
    raise notice 'SKIPPED: ops.j1_clock_append_revision is absent; ops/journey-one-clock-store.candidate.sql has not been applied here yet.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'portfolio_canonical_json')
     or not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'ops' and p.proname = 'portfolio_writer_actor_id') then
    raise notice 'SKIPPED: migration 0496 is absent; this rail reuses its canonicalizer and writer context rather than restating them.';
    return;
  end if;
  select slug into v_actor from public.actor where active order by slug collate "C" limit 1;
  if v_actor is null then
    raise notice 'SKIPPED: no active actor exists; this proof creates none.';
    return;
  end if;
  perform set_config('carr.acting_actor_slug', v_actor, true);

  if to_regclass('ops.job') is not null then
    select count(*) into v_jobs from ops.job;
  end if;

  -- --- ARITY. Every function the module's postgres journal calls, with the
  -- --- exact signature it calls it at. A journal that names a function this
  -- --- file does not define would fail at the first live call.
  foreach v_signature in array array[
    'ops.j1_clock_lock(text)',
    'ops.j1_clock_scope_lock(text)',
    'ops.j1_clock_scope_digest(jsonb)',
    'ops.j1_clock_bind_scope(text,jsonb)',
    'ops.j1_clock_scope_bindings(text,text)',
    'ops.j1_clock_head(text)',
    'ops.j1_clock_row(text)',
    'ops.j1_clock_revisions(text)',
    'ops.j1_clock_history(text)',
    'ops.j1_clock_revision_by_idempotency_key(uuid)',
    'ops.j1_clock_identity_digest(text,text,text,text)',
    'ops.j1_clock_history_digest_of(jsonb,jsonb,jsonb)',
    'ops.j1_clock_history_preimage_of(jsonb,jsonb,jsonb)',
    'ops.j1_clock_event_digest(text,text,text,text,text)',
    'ops.j1_clock_history_digest(uuid)',
    'ops.j1_clock_revision_integrity_error(uuid)',
    'ops.j1_clock_append_revision(text,text,text,text,uuid,text,jsonb,jsonb,jsonb,jsonb)'
  ] loop
    if to_regprocedure(v_signature) is null then
      raise exception 'ARITY: % is not defined with that signature', v_signature;
    end if;
  end loop;

  -- --- the synthetic history ------------------------------------------------
  -- Ordinal 0: the clock as it stands the moment it started. One event, no
  -- pauses, no completion.
  v_event0 := jsonb_build_object(
    'ordinal', 0, 'type', 'clock_started', 'at', v_origin_at,
    'recorded_at', v_origin_at, 'evidence_digest', v_origin_receipt,
    'previous_event_digest', null);
  v_event0 := v_event0 || jsonb_build_object('event_digest',
    ops.j1_clock_event_digest('clock_started', v_origin_at, v_origin_at, v_origin_receipt, null));
  v_events0 := jsonb_build_array(v_event0);

  v_scalars0 := jsonb_build_object(
    'schema_version', 'doctorcre-v5-journey-one-clock.v2',
    'origin_receipt_digest', v_origin_receipt,
    'origin_at', v_origin_at,
    'origin_benchmark_manifest_digest', v_benchmark,
    'current_benchmark_manifest_digest', v_benchmark,
    'origin_receipt_ttl_policy_ms', 172800000,
    'base_deadline_at', v_due,
    'base_deadline_resolution', 'same_chicago_wall_time_after_30_dates',
    'due_at', v_due,
    'paused_ms', 0,
    'status', 'running',
    'miss_at', null,
    'completion_receipt_digest', null,
    'completion_observed_at', null,
    'completion_receipt_ttl_policy_ms', null,
    'completion_artifact_digest', null,
    'completion_fixture_set_digest', null,
    'evaluated_at', '2026-09-10T00:00:00.000Z');

  v_clock_key := ops.j1_clock_identity_digest(v_tenant, v_origin_receipt, v_origin_at, v_benchmark);
  v_digest0 := ops.j1_clock_history_digest_of(v_scalars0, '[]'::jsonb, v_events0);

  -- --- THE SCOPE IS BOUND BEFORE THE CLOCK EXISTS --------------------------
  -- The key is DERIVED from the scope's own fields here; a caller-supplied one
  -- would be a self-chosen address wearing a hash.
  v_result := ops.j1_clock_bind_scope(v_clock_key, v_scope);
  v_scope_key := v_result ->> 'clock_scope_key';
  if v_scope_key is distinct from ops.j1_clock_scope_digest(v_scope) then
    raise exception 'SCOPE: the binding did not derive its key from the scope it was handed';
  end if;
  if (v_result ->> 'clock_key') is distinct from v_clock_key then
    raise exception 'SCOPE: the binding names another clock than the one it was asked for';
  end if;
  if (ops.j1_clock_scope_bindings(v_scope_key, null) -> 'by_scope' ->> 'clock_key')
     is distinct from v_clock_key then
    raise exception 'SCOPE: the binding does not read back by its scope key';
  end if;
  -- BINDING IS IDEMPOTENT for the exact pair, so an ordinary append rebinds
  -- nothing and writes no second row.
  perform ops.j1_clock_bind_scope(v_clock_key, v_scope);
  select count(*) into v_count from ops.j1_clock_scope_binding where clock_scope_key = v_scope_key;
  if v_count <> 1 then
    raise exception 'SCOPE: an exact rebind wrote a second binding row';
  end if;
  -- A SCOPE NAMING OTHER GATES IS NOT A JOURNEY 1 CLOCK SCOPE.
  begin
    perform ops.j1_clock_scope_digest(
      v_scope || jsonb_build_object('clock_origin_gate_id', 'some-other-gate-accepted'));
    raise exception 'NEGATIVE FAILED: a scope naming another gate was hashed';
  exception when others then
    if sqlerrm not like '%j1_clock_scope_binds_one_clock%' then raise; end if;
  end;
  begin
    perform ops.j1_clock_scope_digest(v_scope || jsonb_build_object('extra_field', 'x'));
    raise exception 'NEGATIVE FAILED: a scope carrying an extra field was hashed';
  exception when others then
    if sqlerrm not like '%j1_clock_scope_binds_one_clock%' then raise; end if;
  end;

  -- --- THE LABEL IS NOT THE IDENTITY --------------------------------------
  -- ONE ACCEPTED SCOPE UNDER TWO NAMES IS ONE KEY. This is the whole of the
  -- fix: with scope_ref in the preimage these two hashed differently, so "one
  -- scope, one clock" was really "one label, one clock".
  if ops.j1_clock_scope_digest(v_scope_relabelled) is distinct from v_scope_key then
    raise exception 'SCOPE: relabelling an accepted scope produced a second scope key';
  end if;
  -- AND A DIFFERENT LEGITIMATE SUBJECT IS STILL A DIFFERENT SCOPE. The label is
  -- excluded; the six identity fields are not, and moving one still separates
  -- two programs.
  if ops.j1_clock_scope_digest(
       v_scope || jsonb_build_object('benchmark_subject_digest', 'sha256:' || repeat('97', 32)))
     is not distinct from v_scope_key then
    raise exception 'SCOPE: two different accepted subjects collapsed onto one scope key';
  end if;
  -- THE ALIAS ATTEMPT ITSELF, REFUSED BEFORE ANY REVISION EXISTS. A fresh origin
  -- presented under a relabelled scope derives a clock key this rail has never
  -- seen -- so no compare-and-swap could refuse it -- and meets the scope that
  -- already holds a clock instead.
  begin
    perform ops.j1_clock_bind_scope(
      ops.j1_clock_identity_digest(v_tenant, v_other_receipt, v_origin_at, v_benchmark),
      v_scope_relabelled);
    raise exception 'NEGATIVE FAILED: a relabelled scope opened a second clock for one program';
  exception when others then
    if sqlerrm not like '%j1_clock_scope_binds_one_clock%' then raise; end if;
  end;
  -- AND THE LABEL A SCOPE WAS BOUND UNDER IS RECORDED ONCE. Not identity, and
  -- therefore not silently replaceable either: the same clock rebinding its own
  -- scope under a new name is refused rather than overwriting the provenance.
  begin
    perform ops.j1_clock_bind_scope(v_clock_key, v_scope_relabelled);
    raise exception 'NEGATIVE FAILED: a bound scope was relabelled by a later write';
  exception when others then
    if sqlerrm not like '%j1_clock_scope_label_is_not_identity%' then raise; end if;
  end;
  select count(*) into v_count from ops.j1_clock_scope_binding where clock_scope_key = v_scope_key;
  if v_count <> 1
     or (select clock_scope_ref from ops.j1_clock_scope_binding where clock_scope_key = v_scope_key)
        is distinct from (v_scope ->> 'scope_ref') then
    raise exception 'SCOPE: the refused relabelling changed the stored binding';
  end if;

  -- --- POSITIVE CAS FIXTURE, part one: the creation ------------------------
  -- An explicit NULL prior. This is the only shape that may create a clock, and
  -- it is admissible exactly once.
  v_result := ops.j1_clock_append_revision(
    v_clock_key, v_tenant, 'J1-POSTGRES-PROOF', null, v_key0, v_digest0,
    v_scalars0, '[]'::jsonb, v_events0, v_provenance);
  if (v_result ->> 'revision_ordinal')::integer <> 0 then
    raise exception 'POSITIVE CAS: a creating revision must be ordinal 0, got %', v_result ->> 'revision_ordinal';
  end if;
  if (v_result ->> 'replayed')::boolean then
    raise exception 'POSITIVE CAS: a first write is not a replay';
  end if;
  if v_result -> 'expected_prior_history_digest' <> 'null'::jsonb then
    raise exception 'POSITIVE CAS: the creation must record its explicit null prior';
  end if;
  v_revision0 := (v_result ->> 'revision_id')::uuid;

  -- THE DIGEST IS RECOMPUTED FROM THE STORED ROWS, and the two builders agree.
  if ops.j1_clock_history_digest(v_revision0) is distinct from v_digest0 then
    raise exception 'the stored rows do not reproduce the digest they were written under';
  end if;
  if ops.j1_clock_history_preimage(v_revision0)
     is distinct from ops.j1_clock_history_preimage_of(v_scalars0, '[]'::jsonb, v_events0) then
    raise exception 'the row-form and parameter-form preimages disagree for identical content';
  end if;
  v_err := ops.j1_clock_revision_integrity_error(v_revision0);
  if v_err is not null then raise exception 'a freshly written revision is not intact: %', v_err; end if;

  -- --- POSITIVE CAS FIXTURE, part two: a valid append ----------------------
  -- A pause approval arrives. One new event, one pause interval, credited hours.
  v_event1 := jsonb_build_object(
    'ordinal', 1, 'type', 'pause_approved', 'at', '2026-09-11T15:00:00.000Z',
    'recorded_at', '2026-09-14T00:00:00.000Z', 'evidence_digest', v_pause_evidence,
    'previous_event_digest', v_event0 ->> 'event_digest');
  v_event1 := v_event1 || jsonb_build_object('event_digest',
    ops.j1_clock_event_digest('pause_approved', '2026-09-11T15:00:00.000Z',
      '2026-09-14T00:00:00.000Z', v_pause_evidence, v_event0 ->> 'event_digest'));
  v_events1 := v_events0 || jsonb_build_array(v_event1);
  v_pauses1 := jsonb_build_array(jsonb_build_object(
    'ordinal', 0, 'pause_id', 'safe:synthetic:pause-one',
    'ends_at', '2026-09-12T03:00:00.000Z'));
  v_scalars1 := v_scalars0
    || jsonb_build_object('paused_ms', 43200000,
                          'due_at', '2026-10-10T03:00:00.000Z',
                          'evaluated_at', '2026-09-14T00:00:00.000Z');
  v_digest1 := ops.j1_clock_history_digest_of(v_scalars1, v_pauses1, v_events1);

  v_result := ops.j1_clock_append_revision(
    v_clock_key, v_tenant, 'J1-POSTGRES-PROOF', v_digest0, v_key1, v_digest1,
    v_scalars1, v_pauses1, v_events1, v_provenance);
  if (v_result ->> 'revision_ordinal')::integer <> 1 then
    raise exception 'POSITIVE CAS: an append onto the head must be ordinal 1';
  end if;
  v_revision1 := (v_result ->> 'revision_id')::uuid;
  if ops.j1_clock_history_digest(v_revision1) is distinct from v_digest1 then
    raise exception 'the appended rows do not reproduce their digest';
  end if;
  if (ops.j1_clock_head(v_clock_key) ->> 'history_digest') is distinct from v_digest1 then
    raise exception 'the head is not the revision just appended';
  end if;

  -- --- the readback, and its honesty ---------------------------------------
  v_readback := ops.j1_clock_history(v_clock_key);
  if not (v_readback ->> 'exists')::boolean then
    raise exception 'READBACK: the clock just written does not read back';
  end if;
  if (v_readback ->> 'revision_count')::integer <> 2
     or (v_readback ->> 'head_revision_ordinal')::integer <> 1 then
    raise exception 'READBACK: the revision chain is not two revisions ending at ordinal 1';
  end if;
  if v_readback ->> 'history_digest' is distinct from v_readback ->> 'recomputed_history_digest' then
    raise exception 'READBACK: the stored and recomputed digests disagree';
  end if;
  if (v_readback -> 'history' ->> 'history_digest') is distinct from v_digest1 then
    raise exception 'READBACK: the rebuilt state does not carry the digest it was filed under';
  end if;
  if jsonb_array_length(v_readback -> 'history' -> 'events') <> 2 then
    raise exception 'READBACK: the event chain lost a link';
  end if;
  -- ORDER IS PART OF THE HASH: the first event must still be the first.
  if (v_readback -> 'history' -> 'events' -> 0 ->> 'event_digest')
     is distinct from (v_event0 ->> 'event_digest') then
    raise exception 'READBACK: the event chain came back reordered';
  end if;
  -- The verbatim pause end, unnormalized.
  if (v_readback -> 'history' -> 'pause_intervals' -> 0 ->> 'ends_at')
     is distinct from '2026-09-12T03:00:00.000Z' then
    raise exception 'READBACK: a pause end was normalized on the way out';
  end if;
  -- STORING A STATUS IS NOT ACCEPTING A DEADLINE, and the readback says so.
  if (v_readback ->> 'deadline_accepted_by_record_layer')::boolean then
    raise exception 'READBACK: this record layer must never report a deadline as accepted';
  end if;
  if jsonb_array_length(v_readback -> 'record_layer_cannot_prove') <> 7 then
    raise exception 'READBACK: the record layer must state what it cannot prove';
  end if;
  -- WHICH SCOPE HOLDS THIS CLOCK, reported on the readback rather than assumed.
  if not (v_readback ->> 'clock_scope_bound')::boolean
     or (v_readback ->> 'clock_scope_key') is distinct from v_scope_key then
    raise exception 'READBACK: the readback does not report the scope this clock is bound to';
  end if;
  if (v_readback ->> 'clock_started_by_this_record_layer')::boolean then
    raise exception 'READBACK: storing a history does not start a clock';
  end if;
  -- Provenance is scoped and pinned, and names no verification.
  if (v_readback -> 'revisions' -> 1 -> 'provenance' ->> 'input_authority')
     is distinct from 'trusted_projection_not_independently_verified_by_this_record_layer' then
    raise exception 'PROVENANCE: input_authority must state that the inputs were not verified here';
  end if;
  if (v_readback -> 'revisions' -> 1 -> 'provenance' ->> 'verifier_ref') is distinct from v_verifier then
    raise exception 'PROVENANCE: verifier_ref must NAME the installed verifier';
  end if;

  -- =========================================================================
  -- DIRECT-WRITER NEGATIVES. Each one is what a holder of the writer bundle
  -- could try, and each is refused by name with the shared invariant id.
  -- =========================================================================

  -- A STALE PRIOR. Another writer appended first; this revision was computed
  -- against a history that is no longer the head.
  begin
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, null, v_digest0, v_key_x, v_digest0,
      v_scalars0, '[]'::jsonb, v_events0, v_provenance);
    raise exception 'NEGATIVE FAILED: a stale prior history digest was admitted';
  exception when others then
    if sqlerrm not like '%j1_clock_exact_prior_history_digest%' then raise; end if;
  end;

  -- A SECOND CREATION. An explicit null prior against a clock that already has
  -- history: the shape a caller-chosen alias would take if a label could
  -- address a clock. It cannot; the identity is derived from the origin.
  begin
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, 'J1-A-DIFFERENT-LABEL', null, v_key_x, v_digest0,
      v_scalars0, '[]'::jsonb, v_events0, v_provenance);
    raise exception 'NEGATIVE FAILED: a second creation restarted a running clock';
  exception when others then
    if sqlerrm not like '%j1_clock_exact_prior_history_digest%' then raise; end if;
  end;

  -- A SECOND ORIGIN FOR ONE AUTHORITATIVE SCOPE. THIS IS THE ATTACK THE DERIVED
  -- IDENTITY CANNOT SEE: a different origin receipt derives a different clock
  -- key, and a different key has no head, so its creation would meet no
  -- compare-and-swap at all. The scope binding is what refuses it, and the
  -- same-origin negative above proves nothing about this one.
  begin
    perform ops.j1_clock_bind_scope(
      ops.j1_clock_identity_digest(v_tenant, v_other_receipt, v_origin_at, v_benchmark), v_scope);
    raise exception 'NEGATIVE FAILED: a second origin opened a second clock for one authoritative scope';
  exception when others then
    if sqlerrm not like '%j1_clock_scope_binds_one_clock%' then raise; end if;
  end;

  -- AN AMENDED ORIGIN MANIFEST is the same attack wearing another field: it also
  -- derives a fresh clock key, and it also meets the scope that already has one.
  begin
    perform ops.j1_clock_bind_scope(
      ops.j1_clock_identity_digest(v_tenant, v_origin_receipt, v_origin_at,
        'sha256:' || repeat('ee', 32)), v_scope);
    raise exception 'NEGATIVE FAILED: an amended origin manifest opened a second clock for one scope';
  exception when others then
    if sqlerrm not like '%j1_clock_scope_binds_one_clock%' then raise; end if;
  end;

  -- A CLOCK REBOUND TO A SECOND SCOPE. A clock is bound once and never rebound.
  begin
    perform ops.j1_clock_bind_scope(v_clock_key, v_scope_second);
    raise exception 'NEGATIVE FAILED: a clock was rebound to a second authoritative scope';
  exception when others then
    if sqlerrm not like '%j1_clock_scope_sealed_at_creation%' then raise; end if;
  end;

  -- AN APPEND FOR A CLOCK WITH NO SCOPE BINDING AT ALL. A direct caller of the
  -- append function never runs the JavaScript, so the guard is what makes
  -- binding mandatory rather than conventional: an unbound clock is exactly the
  -- clock a second origin could have opened beside a running one.
  declare
    v_unbound_origin  constant text := 'sha256:' || repeat('99', 32);
    v_unbound_key     text;
    v_unbound_scalars jsonb;
    v_unbound_events  jsonb;
    v_unbound_event   jsonb;
  begin
    v_unbound_key := ops.j1_clock_identity_digest(v_tenant, v_unbound_origin, v_origin_at, v_benchmark);
    v_unbound_event := jsonb_build_object(
      'ordinal', 0, 'type', 'clock_started', 'at', v_origin_at,
      'recorded_at', v_origin_at, 'evidence_digest', v_unbound_origin,
      'previous_event_digest', null);
    v_unbound_event := v_unbound_event || jsonb_build_object('event_digest',
      ops.j1_clock_event_digest('clock_started', v_origin_at, v_origin_at, v_unbound_origin, null));
    v_unbound_events := jsonb_build_array(v_unbound_event);
    v_unbound_scalars := v_scalars0 || jsonb_build_object('origin_receipt_digest', v_unbound_origin);
    begin
      perform ops.j1_clock_append_revision(
        v_unbound_key, v_tenant, null, null, v_key_x,
        ops.j1_clock_history_digest_of(v_unbound_scalars, '[]'::jsonb, v_unbound_events),
        v_unbound_scalars, '[]'::jsonb, v_unbound_events, v_provenance);
      raise exception 'NEGATIVE FAILED: a revision was stored for a clock with no authoritative scope binding';
    exception when others then
      if sqlerrm not like '%j1_clock_scope_binds_one_clock%' then raise; end if;
    end;
  end;

  -- AN IDEMPOTENCY KEY CARRYING A CHANGED PAYLOAD. The exact replay is a replay;
  -- the same key with different content is a different request wearing it.
  v_result := ops.j1_clock_append_revision(
    v_clock_key, v_tenant, 'J1-POSTGRES-PROOF', v_digest0, v_key1, v_digest1,
    v_scalars1, v_pauses1, v_events1, v_provenance);
  if not (v_result ->> 'replayed')::boolean then
    raise exception 'IDEMPOTENCY: an exact repeat must replay, not write again';
  end if;
  select count(*) into v_count from ops.j1_clock_revision r
    join ops.j1_clock c on c.id = r.clock_id where c.clock_key = v_clock_key;
  if v_count <> 2 then
    raise exception 'IDEMPOTENCY: a replay wrote a second row';
  end if;
  begin
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, 'J1-POSTGRES-PROOF', v_digest1, v_key1, v_digest0,
      v_scalars0, '[]'::jsonb, v_events0, v_provenance);
    raise exception 'NEGATIVE FAILED: one idempotency key carried two payloads';
  exception when others then
    if sqlerrm not like '%j1_clock_idempotency_key_binds_its_payload%' then raise; end if;
  end;

  -- A REBASED ORIGIN. The origin digest and instant are untouched, so this still
  -- addresses the same clock -- and the base deadline it claims has moved. The
  -- origin columns alone would not have caught it.
  begin
    v_scalars_tamper := v_scalars1
      || jsonb_build_object('base_deadline_at', '2026-10-10T15:00:00.000Z');
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, null, v_digest1, v_key_x,
      ops.j1_clock_history_digest_of(v_scalars_tamper, v_pauses1, v_events1),
      v_scalars_tamper, v_pauses1, v_events1, v_provenance);
    raise exception 'NEGATIVE FAILED: a rebased origin was admitted';
  exception when others then
    if sqlerrm not like '%j1_clock_origin_never_rewritten%' then raise; end if;
  end;

  -- A DIFFERENT ORIGIN RECEIPT does not reach the diff at all: it derives a
  -- DIFFERENT clock, so a caller naming this clock's key is refused on identity.
  begin
    v_scalars_tamper := v_scalars1 || jsonb_build_object('origin_receipt_digest', v_other_receipt);
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, null, v_digest1, v_key_x,
      ops.j1_clock_history_digest_of(v_scalars_tamper, v_pauses1, v_events1),
      v_scalars_tamper, v_pauses1, v_events1, v_provenance);
    raise exception 'NEGATIVE FAILED: a revision was filed under a clock its own origin does not derive';
  exception when others then
    if sqlerrm not like '%j1_clock_identity_derived_from_origin%' then raise; end if;
  end;

  -- A LOST EVENT. The chain still links and the digest still recomputes: this
  -- record is internally flawless and is exactly what an erased approval looks
  -- like from the inside.
  begin
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, null, v_digest1, v_key_x,
      ops.j1_clock_history_digest_of(v_scalars1, '[]'::jsonb, v_events0),
      v_scalars1, '[]'::jsonb, v_events0, v_provenance);
    raise exception 'NEGATIVE FAILED: a lost event was admitted';
  exception when others then
    if sqlerrm not like '%j1_clock_events_are_append_only%' then raise; end if;
  end;

  -- A FORGED EVENT DIGEST. Linkage alone is not enough: each link must hash to
  -- its own content, or a writer could hand this rail any sha256-shaped strings
  -- that happen to point at each other.
  begin
    v_event1 := jsonb_build_object(
      'ordinal', 1, 'type', 'pause_approved', 'at', '2026-09-11T15:00:00.000Z',
      'recorded_at', '2026-09-14T00:00:00.000Z', 'evidence_digest', v_pause_evidence,
      'previous_event_digest', v_event0 ->> 'event_digest',
      'event_digest', 'sha256:' || repeat('ab', 32));
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, null, v_digest1, v_key_x,
      ops.j1_clock_history_digest_of(v_scalars1, v_pauses1,
        v_events0 || jsonb_build_array(v_event1)),
      v_scalars1, v_pauses1, v_events0 || jsonb_build_array(v_event1), v_provenance);
    raise exception 'NEGATIVE FAILED: a forged event digest was admitted';
  exception when others then
    if sqlerrm not like '%j1_clock_events_are_append_only%' then raise; end if;
  end;

  -- A LIED-ABOUT HISTORY DIGEST. The caller's hash is only ever the loser of a
  -- comparison against the one the stored rows produce.
  begin
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, null, v_digest1, v_key_x, 'sha256:' || repeat('cd', 32),
      v_scalars1, v_pauses1, v_events1, v_provenance);
    raise exception 'NEGATIVE FAILED: a claimed history digest was taken on trust';
  exception when others then
    if sqlerrm not like '%j1_clock_claimed_history_digest_is_never_trusted%' then raise; end if;
  end;

  -- A LEGACY v1 HISTORY. Refused by name; never silently re-derived, because
  -- re-deriving a v1 origin digest would rebase a sealed origin.
  begin
    v_scalars_tamper := v_scalars1
      || jsonb_build_object('schema_version', 'doctorcre-v5-journey-one-clock.v1');
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, null, v_digest1, v_key_x,
      ops.j1_clock_history_digest_of(v_scalars_tamper, v_pauses1, v_events1),
      v_scalars_tamper, v_pauses1, v_events1, v_provenance);
    raise exception 'NEGATIVE FAILED: a legacy v1 history was stored';
  exception when others then
    if sqlerrm not like '%j1_clock_state_schema_current%' and sqlerrm not like '%state_schema_version%' then
      raise;
    end if;
  end;

  -- =========================================================================
  -- REPEATED APPENDS IN ONE TRANSACTION, AND A CALLER WHO ENTERED IMMEDIATE.
  --
  -- SET CONSTRAINTS IS TRANSACTION-SCOPED, NOT CALL-SCOPED. The append function
  -- forces its deferred guard to IMMEDIATE so a caller gets a named refusal at
  -- the point of the call; left in force, that mode would then validate the NEXT
  -- revision row at the end of its own INSERT -- before that revision's events
  -- and pause intervals had been written -- and refuse a perfectly valid append
  -- for carrying no events. The function therefore sets DEFERRED before it
  -- inserts a revision, forces IMMEDIATE over the complete children, and
  -- restores DEFERRED before returning.
  --
  -- Revisions 0 and 1 above were ALREADY two appends inside this one
  -- transaction. This group says so explicitly, checks that each landed with its
  -- children intact, and then repeats it from the harder starting point: a
  -- caller that entered under SET CONSTRAINTS ALL IMMEDIATE.
  -- =========================================================================
  select count(*) into v_count from ops.j1_clock_revision_event where revision_id = v_revision0;
  if v_count <> 1 then
    raise exception 'SEQUENCING: the creating revision did not keep its complete event chain';
  end if;
  select count(*) into v_count from ops.j1_clock_revision_event where revision_id = v_revision1;
  if v_count <> 2 then
    raise exception 'SEQUENCING: the second append in this transaction lost its event chain';
  end if;
  select count(*) into v_count from ops.j1_clock_revision_pause_interval where revision_id = v_revision1;
  if v_count <> 1 then
    raise exception 'SEQUENCING: the second append in this transaction lost its pause interval';
  end if;

  -- A CALLER-ENTERED IMMEDIATE MODE, set here exactly as a batch loader or an
  -- outer fixture might set it, and never restored by this file: what follows
  -- has to work with it in force.
  execute 'set constraints all immediate';
  v_scalars2 := v_scalars1 || jsonb_build_object('evaluated_at', '2026-09-15T00:00:00.000Z');
  v_digest2 := ops.j1_clock_history_digest_of(v_scalars2, v_pauses1, v_events1);
  v_result := ops.j1_clock_append_revision(
    v_clock_key, v_tenant, 'J1-POSTGRES-PROOF', v_digest1, v_key2, v_digest2,
    v_scalars2, v_pauses1, v_events1, v_provenance);
  if (v_result ->> 'revision_ordinal')::integer <> 2 then
    raise exception 'SEQUENCING: a third append in one transaction must be ordinal 2, got %',
      v_result ->> 'revision_ordinal';
  end if;
  v_revision2 := (v_result ->> 'revision_id')::uuid;
  select count(*) into v_count from ops.j1_clock_revision_event where revision_id = v_revision2;
  if v_count <> 2 then
    raise exception 'SEQUENCING: the append under a caller-entered IMMEDIATE lost its event chain';
  end if;
  select count(*) into v_count from ops.j1_clock_revision_pause_interval where revision_id = v_revision2;
  if v_count <> 1 then
    raise exception 'SEQUENCING: the append under a caller-entered IMMEDIATE lost its pause interval';
  end if;
  v_err := ops.j1_clock_revision_integrity_error(v_revision2);
  if v_err is not null then
    raise exception 'SEQUENCING: the append under a caller-entered IMMEDIATE is not intact: %', v_err;
  end if;

  -- THE GUARD IS STILL IN FORCE AFTERWARDS. Restoring the deferral mode is not
  -- a way of switching the check off: this append is refused by name over the
  -- new head, and a caught refusal rolls back its own subtransaction -- the
  -- constraint mode it set along with everything else it wrote.
  begin
    perform ops.j1_clock_append_revision(
      v_clock_key, v_tenant, null, v_digest2, v_key_x,
      ops.j1_clock_history_digest_of(v_scalars2, '[]'::jsonb, v_events0),
      v_scalars2, '[]'::jsonb, v_events0, v_provenance);
    raise exception 'NEGATIVE FAILED: a lost event was admitted after the constraint mode was restored';
  exception when others then
    if sqlerrm not like '%j1_clock_events_are_append_only%' then raise; end if;
  end;

  -- AND THE TRANSACTION IS NOT WEDGED BY EITHER OF THEM: a fourth valid append
  -- still lands, with its own children, and it is the head.
  v_scalars3 := v_scalars2 || jsonb_build_object('evaluated_at', '2026-09-16T00:00:00.000Z');
  v_digest3 := ops.j1_clock_history_digest_of(v_scalars3, v_pauses1, v_events1);
  v_result := ops.j1_clock_append_revision(
    v_clock_key, v_tenant, 'J1-POSTGRES-PROOF', v_digest2, v_key3, v_digest3,
    v_scalars3, v_pauses1, v_events1, v_provenance);
  if (v_result ->> 'revision_ordinal')::integer <> 3 then
    raise exception 'SEQUENCING: a fourth append in one transaction must be ordinal 3, got %',
      v_result ->> 'revision_ordinal';
  end if;
  select count(*) into v_count
    from ops.j1_clock_revision_event where revision_id = (v_result ->> 'revision_id')::uuid;
  if v_count <> 2 then
    raise exception 'SEQUENCING: the fourth append in this transaction lost its event chain';
  end if;
  if (ops.j1_clock_head(v_clock_key) ->> 'history_digest') is distinct from v_digest3 then
    raise exception 'SEQUENCING: the head is not the last revision appended in this transaction';
  end if;
  if (ops.j1_clock_history(v_clock_key) ->> 'revision_count')::integer <> 4 then
    raise exception 'SEQUENCING: four appends in one transaction did not produce four revisions';
  end if;

  -- =========================================================================
  -- THE MISS AND THE SEALS. A second clock, started already missed and already
  -- completed, so the removed-miss and changed-seal negatives have something
  -- real to be refused against.
  -- =========================================================================
  declare
    v_key_m         constant uuid := '00000000-0000-4000-8000-00000000c010';
    v_key_m2        constant uuid := '00000000-0000-4000-8000-00000000c011';
    v_missed_key    text;
    v_missed_digest text;
    v_missed_scalars jsonb;
    v_missed_events jsonb;
    v_missed_origin constant text := 'sha256:' || repeat('88', 32);
    v_miss_event    jsonb;
    v_done_event    jsonb;
  begin
    v_missed_key := ops.j1_clock_identity_digest(v_tenant, v_missed_origin, v_origin_at, v_benchmark);
    v_event0 := jsonb_build_object(
      'ordinal', 0, 'type', 'clock_started', 'at', v_origin_at,
      'recorded_at', v_origin_at, 'evidence_digest', v_missed_origin,
      'previous_event_digest', null);
    v_event0 := v_event0 || jsonb_build_object('event_digest',
      ops.j1_clock_event_digest('clock_started', v_origin_at, v_origin_at, v_missed_origin, null));
    v_miss_event := jsonb_build_object(
      'ordinal', 1, 'type', 'deadline_missed', 'at', v_due,
      'recorded_at', '2026-10-09T16:00:00.000Z', 'evidence_digest', v_origin_receipt,
      'previous_event_digest', v_event0 ->> 'event_digest');
    v_miss_event := v_miss_event || jsonb_build_object('event_digest',
      ops.j1_clock_event_digest('deadline_missed', v_due, '2026-10-09T16:00:00.000Z',
        v_origin_receipt, v_event0 ->> 'event_digest'));
    v_done_event := jsonb_build_object(
      'ordinal', 2, 'type', 'completion_observed', 'at', '2026-10-11T15:00:00.000Z',
      'recorded_at', '2026-10-11T16:00:00.000Z', 'evidence_digest', v_completion,
      'previous_event_digest', v_miss_event ->> 'event_digest');
    v_done_event := v_done_event || jsonb_build_object('event_digest',
      ops.j1_clock_event_digest('completion_observed', '2026-10-11T15:00:00.000Z',
        '2026-10-11T16:00:00.000Z', v_completion, v_miss_event ->> 'event_digest'));
    v_missed_events := jsonb_build_array(v_event0, v_miss_event, v_done_event);

    -- A LATE COMPLETION BESIDE A RECORDED MISS. Q008.D1 as accepted: the receipt
    -- stays usable, the miss stands, and deadline success is never claimed. This
    -- rail stores that combination and refuses the forbidden one below.
    v_missed_scalars := v_scalars0 || jsonb_build_object(
      'origin_receipt_digest', v_missed_origin,
      'status', 'completed_late',
      'miss_at', v_due,
      'completion_receipt_digest', v_completion,
      'completion_observed_at', '2026-10-11T15:00:00.000Z',
      'completion_receipt_ttl_policy_ms', 259200000,
      'completion_artifact_digest', v_artifact,
      'completion_fixture_set_digest', v_fixtures,
      'evaluated_at', '2026-10-11T16:00:00.000Z');
    v_missed_digest := ops.j1_clock_history_digest_of(v_missed_scalars, '[]'::jsonb, v_missed_events);
    -- A SECOND CLOCK IS A SECOND SCOPE. Two clocks may coexist on this rail;
    -- what may not is two clocks for ONE authoritative scope.
    perform ops.j1_clock_bind_scope(v_missed_key, v_scope_second);
    v_result := ops.j1_clock_append_revision(
      v_missed_key, v_tenant, 'J1-POSTGRES-PROOF-MISSED', null, v_key_m, v_missed_digest,
      v_missed_scalars, '[]'::jsonb, v_missed_events, v_provenance);
    if (v_result ->> 'revision_ordinal')::integer <> 0 then
      raise exception 'a late completion beside a recorded miss must be storable';
    end if;

    -- A REMOVED MISS. It never un-sticks.
    begin
      perform ops.j1_clock_append_revision(
        v_missed_key, v_tenant, null, v_missed_digest, v_key_x,
        ops.j1_clock_history_digest_of(
          v_missed_scalars || jsonb_build_object('miss_at', null), '[]'::jsonb, v_missed_events),
        v_missed_scalars || jsonb_build_object('miss_at', null), '[]'::jsonb,
        v_missed_events, v_provenance);
      raise exception 'NEGATIVE FAILED: a recorded miss was removed';
    exception when others then
      if sqlerrm not like '%j1_clock_recorded_miss_never_removed%' then raise; end if;
    end;

    -- A CHANGED SEAL. The completion TTL policy and the exact accepted kernel
    -- scope are sealed by the first completion; a later revision may not move
    -- either.
    begin
      perform ops.j1_clock_append_revision(
        v_missed_key, v_tenant, null, v_missed_digest, v_key_x,
        ops.j1_clock_history_digest_of(
          v_missed_scalars || jsonb_build_object('completion_artifact_digest', v_other_receipt),
          '[]'::jsonb, v_missed_events),
        v_missed_scalars || jsonb_build_object('completion_artifact_digest', v_other_receipt),
        '[]'::jsonb, v_missed_events, v_provenance);
      raise exception 'NEGATIVE FAILED: a completion seal was changed';
    exception when others then
      if sqlerrm not like '%j1_clock_completion_seals_never_changed%' then raise; end if;
    end;

    -- THE FORBIDDEN CLAIM. A recorded miss beside completed_on_time is refused
    -- at the table constraint, so it cannot be stored at all.
    begin
      perform ops.j1_clock_append_revision(
        v_missed_key, v_tenant, null, v_missed_digest, v_key_x,
        ops.j1_clock_history_digest_of(
          v_missed_scalars || jsonb_build_object('status', 'completed_on_time'),
          '[]'::jsonb, v_missed_events),
        v_missed_scalars || jsonb_build_object('status', 'completed_on_time'),
        '[]'::jsonb, v_missed_events, v_provenance);
      raise exception 'NEGATIVE FAILED: deadline success was claimed beside a recorded miss';
    exception when others then
      if sqlerrm not like '%j1_clock_no_deadline_success_after_recorded_miss%' then raise; end if;
    end;

    -- A PARTIAL COMPLETION. The five seals are ONE fact; four of them is not a
    -- completion and is refused by the table.
    begin
      perform ops.j1_clock_append_revision(
        v_missed_key, v_tenant, null, v_missed_digest, v_key_m2,
        ops.j1_clock_history_digest_of(
          v_missed_scalars || jsonb_build_object('completion_fixture_set_digest', null),
          '[]'::jsonb, v_missed_events),
        v_missed_scalars || jsonb_build_object('completion_fixture_set_digest', null),
        '[]'::jsonb, v_missed_events, v_provenance);
      raise exception 'NEGATIVE FAILED: a partial completion seal was stored';
    exception when others then
      if sqlerrm not like '%completion_seals_are_one_fact%' then raise; end if;
    end;
  end;

  -- =========================================================================
  -- APPEND-ONLY, THE FREEZE, AND TAMPER DETECTION.
  -- =========================================================================

  -- APPEND-ONLY: update, delete and truncate are refused on every relation of
  -- this rail.
  --
  -- THE ATTEMPTS BELOW NEED THE PRIVILEGE THEY ARE TESTING. update, delete and
  -- truncate are revoked from every runtime bundle at the foot of the candidate
  -- SQL, so a run as `carr_writer` fails each one with insufficient privilege
  -- (42501) BEFORE the trigger fires; the handlers match on the message text,
  -- would re-raise, and the whole proof would abort having proved nothing about
  -- append-only. The table owner is the intended mode -- has_table_privilege
  -- reports true for an owner with no explicit grant -- so a role that cannot
  -- reach the triggers SKIPS the attempts with a notice instead of turning an
  -- environment fact into a false negative. The structural half, that both
  -- triggers exist, needs no privilege and runs either way.
  foreach v_relation in array v_relations loop
    if not (has_table_privilege(current_user, 'ops.' || v_relation, 'UPDATE')
        and has_table_privilege(current_user, 'ops.' || v_relation, 'DELETE')
        and has_table_privilege(current_user, 'ops.' || v_relation, 'TRUNCATE')) then
      v_may_attempt_dml := false;
    end if;
  end loop;
  if not v_may_attempt_dml then
    raise notice 'SKIPPED: % holds no UPDATE/DELETE/TRUNCATE on the Journey 1 clock relations, so the append-only negatives would fail with insufficient privilege before reaching the trigger and would prove nothing. Run this fixture as the table owner to exercise them.',
      current_user;
  else
  begin
    update ops.j1_clock_revision set status = 'completed_on_time' where id = v_revision1;
    raise exception 'NEGATIVE FAILED: a stored revision was updated';
  exception when others then
    if sqlerrm not like '%append-only%' then raise; end if;
  end;
  begin
    delete from ops.j1_clock_revision_event where revision_id = v_revision1;
    raise exception 'NEGATIVE FAILED: a stored event was deleted';
  exception when others then
    if sqlerrm not like '%append-only%' then raise; end if;
  end;
  begin
    delete from ops.j1_clock where clock_key = v_clock_key;
    raise exception 'NEGATIVE FAILED: a clock was deleted';
  exception when others then
    if sqlerrm not like '%append-only%' then raise; end if;
  end;
  -- The scope binding is on the same rail: unbinding a clock by deleting its
  -- binding would be a reset with an extra step.
  begin
    delete from ops.j1_clock_scope_binding where clock_key = v_clock_key;
    raise exception 'NEGATIVE FAILED: an authoritative scope binding was deleted';
  exception when others then
    if sqlerrm not like '%append-only%' then raise; end if;
  end;
  begin
    update ops.j1_clock_scope_binding set clock_key = v_clock_key where clock_scope_key = v_scope_key;
    raise exception 'NEGATIVE FAILED: an authoritative scope binding was rewritten';
  exception when others then
    if sqlerrm not like '%append-only%' then raise; end if;
  end;
  -- TRUNCATE IS A STATEMENT EVENT AND A ROW-LEVEL TRIGGER NEVER SEES IT. The
  -- header's claim that truncate is refused everywhere on this rail was carried
  -- by `revoke` alone, which does not bind the table owner; the second,
  -- statement-level trigger is what makes it true, and an attempted truncate is
  -- the only thing that can show it. CASCADE because the child tables reference
  -- the revision, and the first refusal is the point either way.
  foreach v_relation in array v_relations loop
    begin
      execute format('truncate ops.%I cascade', v_relation);
      raise exception 'NEGATIVE FAILED: truncate was accepted on ops.%', v_relation;
    exception when others then
      if sqlerrm not like '%append-only%' then raise; end if;
    end;
  end loop;
  end if;
  -- THE STRUCTURAL HALF, NOT GATED ON THE ROLE: reading pg_trigger needs no
  -- privilege on the relation, so whether each trigger EXISTS is asserted for
  -- every run, including one that had to skip the attempts above.
  foreach v_relation in array v_relations loop
    if not exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
                    join pg_namespace n on n.oid = c.relnamespace
                   where n.nspname = 'ops' and c.relname = v_relation
                     and t.tgname = v_relation || '_no_truncate' and not t.tgisinternal) then
      raise exception 'APPEND-ONLY: ops.% has no statement-level truncate trigger, so the claim rests on a revoke the owner is not bound by',
        v_relation;
    end if;
    if not exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
                    join pg_namespace n on n.oid = c.relnamespace
                   where n.nspname = 'ops' and c.relname = v_relation
                     and t.tgname = v_relation || '_append_only' and not t.tgisinternal) then
      raise exception 'APPEND-ONLY: ops.% has no row-level update/delete trigger', v_relation;
    end if;
  end loop;

  -- THE FREEZE: a hashed child row cannot be added to a revision that a later
  -- revision already seals, or the digest would stop covering the rows.
  begin
    insert into ops.j1_clock_revision_pause_interval(revision_id, ordinal, pause_id, ends_at)
    values (v_revision0, 0, 'safe:synthetic:pause-smuggled', null);
    raise exception 'NEGATIVE FAILED: a hashed row was added to a sealed revision';
  exception when others then
    if sqlerrm not like '%sealed behind a later revision%' then raise; end if;
  end;

  -- TAMPER DETECTION IS CONTENT-SENSITIVE. A stored revision cannot be edited
  -- here (update is refused above), so what is demonstrated is the property that
  -- makes an edit detectable: the same rows with ONE field changed produce a
  -- different digest, so a tampered row can never still match the digest it is
  -- filed under, and ops.j1_clock_revision_integrity_error reports it rather
  -- than serving the row.
  if ops.j1_clock_history_digest_of(
       v_scalars1 || jsonb_build_object('status', 'completed_on_time'), v_pauses1, v_events1)
     = v_digest1 then
    raise exception 'TAMPER: a changed status must change the digest';
  end if;
  if ops.j1_clock_history_digest_of(v_scalars1, v_pauses1,
       jsonb_build_array(v_events1 -> 1, v_events1 -> 0)) = v_digest1 then
    raise exception 'TAMPER: order is part of the hash, so a reordered chain must hash differently';
  end if;
  if ops.j1_clock_history_digest_of(v_scalars1,
       jsonb_build_array(jsonb_build_object('ordinal', 0,
         'pause_id', 'safe:synthetic:pause-one', 'ends_at', '2026-09-12T03:00:00+00:00')),
       v_events1) = v_digest1 then
    raise exception 'TAMPER: a re-spelled instant is a different stored history';
  end if;

  -- =========================================================================
  -- GRANTS. Direct INSERT reaches nobody; the write path reaches the writer and
  -- authority bundles only; the readers reach the ordinary bundles.
  -- =========================================================================
  foreach v_role in array array['carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority'] loop
    if not exists (select 1 from pg_roles where rolname = v_role) then continue; end if;
    foreach v_relation in array array['ops.j1_clock', 'ops.j1_clock_scope_binding',
      'ops.j1_clock_revision',
      'ops.j1_clock_revision_pause_interval', 'ops.j1_clock_revision_event'] loop
      -- TRUNCATE IS ASSERTED HERE TOO, because it is a first-class claim of this
      -- rail with its own statement-level trigger. The revoke is the half that
      -- binds a runtime bundle; the trigger is the half that binds the owner.
      if has_table_privilege(v_role, v_relation, 'INSERT')
         or has_table_privilege(v_role, v_relation, 'UPDATE')
         or has_table_privilege(v_role, v_relation, 'DELETE')
         or has_table_privilege(v_role, v_relation, 'TRUNCATE') then
        raise exception 'GRANTS: % holds direct DML on %', v_role, v_relation;
      end if;
    end loop;
    if has_function_privilege(v_role,
         'ops.j1_clock_append_revision(text,text,text,text,uuid,text,jsonb,jsonb,jsonb,jsonb)', 'EXECUTE')
       <> (v_role in ('carr_writer', 'carr_authority')) then
      raise exception 'GRANTS: % has the wrong access to the append function', v_role;
    end if;
    if has_function_privilege(v_role, 'ops.j1_clock_bind_scope(text,jsonb)', 'EXECUTE')
       <> (v_role in ('carr_writer', 'carr_authority')) then
      raise exception 'GRANTS: % has the wrong access to the scope binding function', v_role;
    end if;
    if not has_function_privilege(v_role, 'ops.j1_clock_history(text)', 'EXECUTE') then
      raise exception 'GRANTS: % cannot read a clock history', v_role;
    end if;
    if not has_function_privilege(v_role, 'ops.j1_clock_scope_bindings(text,text)', 'EXECUTE') then
      raise exception 'GRANTS: % cannot read which clock an authoritative scope holds', v_role;
    end if;
  end loop;

  -- INERT. No job, envelope or capability session is created by any of this.
  if to_regclass('ops.job') is not null then
    select count(*) into v_jobs_after from ops.job;
    if v_jobs_after <> v_jobs then
      raise exception 'EFFECTS: storing a clock history created a job';
    end if;
  end if;

  raise notice 'journey-one-clock-store: positive CAS, scope binding, readback and every direct-writer negative asserted; all rows roll back.';
end;
$proof$;

rollback;
