-- DoctorCRE v5 Journey 1 ADMITTED-MINIMUM INPUT STORE: transaction-scoped
-- PostgreSQL proof of ops/journey-one-clock-input-store.candidate.sql.
--
-- THIS FILE HAS NOT BEEN RUN. The local initdb is blocked (shmget), no remote
-- database was used as a workaround, and nothing here is wired into CI. Every
-- claim below is what this fixture WOULD assert; none of it is an observation.
--
-- EVERY FIXTURE ROW IS ROLLED BACK, AND NO RECEIPT IS ADMITTED TO ANY GATE BY
-- RUNNING THIS FILE. What it admits is a SYNTHETIC artifact composed by this
-- file, not one an authenticated producer issued, and that is the exact point of
-- the direct-writer group below: this record layer cannot tell a trusted
-- writer's assertion from a genuine admission, so the rows it stores prove
-- storage mechanics and nothing about a gate, a benchmark or a deadline. A run
-- of this file starts no clock and rolls back regardless.
--
-- ONE TRANSACTION IS ONE ADMISSION INSTANT, AND THAT SHAPES THE WHOLE FIXTURE.
-- ops.j1_minimum_admission_instant() is derived from now(), the TRANSACTION
-- timestamp, so every admission below shares one admitted_at and they are all in
-- ONE ordering group. The stored order is the kernel's own (admitted_at,
-- receipt_digest) selection order, so this file admits its receipts IN DIGEST
-- ORDER -- learned with ops.j1_minimum_receipt_digest before anything is
-- written, never guessed. That is not a workaround: it is the invariant under
-- test, exercised at its hardest boundary, where every row ties on instant.
--
-- EXPLICIT PREREQUISITES, checked before anything is attempted. Each one SKIPS
-- with a notice rather than failing:
--   * ops.j1_minimum_append_admission must exist. It does not until
--     ops/journey-one-clock-input-store.candidate.sql has been applied, which is
--     a separate reviewed act.
--   * ops.j1_clock_scope_digest must exist. This rail reuses THE ONE scope
--     derivation from ops/journey-one-clock-store.candidate.sql rather than
--     defining a second one, so that file must be applied first.
--   * ops.portfolio_canonical_json and ops.portfolio_writer_actor_id must exist
--     (migration 0496).
--   * ops.benchmark_utf16_length must exist. The comparator bound is stated by
--     the kernel in UTF-16 code units and this rail reuses that ONE counter
--     rather than defining a second, so ops/benchmark-acceptance.candidate.sql
--     must be applied first. It is also candidate source and unapplied.
--   * One active actor must already exist for the writer context. THIS FILE
--     CREATES NO ROLE AND NO ACTOR: minting either would manufacture the
--     identity the rail exists to derive.
--   * The append-only NEGATIVES additionally need the invoking role to hold
--     UPDATE, DELETE and TRUNCATE on the two relations -- which in practice
--     means running as the TABLE OWNER, since all three are revoked from every
--     runtime bundle. Run as anyone else those statements fail with
--     insufficient privilege before the trigger fires, so they are SKIPPED with
--     a notice rather than re-raised as a proof. The structural half -- that
--     both triggers exist -- needs no privilege and always runs.
--
-- WHAT IT PROVES, none of which can be shown by reading SQL text:
--   * THE ADMISSION INSTANT IS THE DATABASE'S. ops.j1_minimum_admission_instant()
--     is derived from now(), so the value read before the call and the value the
--     append function derives inside it are one reading; a caller naming a
--     different one is refused by name.
--   * A receipt reporting an observation AFTER that instant is refused, because
--     the kernel refuses that skew fatally and an append-only inventory could
--     never shed the row.
--   * POSITIVE PATH. An opening admission with an explicit NULL prior lands as
--     ordinal 0; each later one naming the head's exact chain digest extends the
--     ledger; ops.j1_minimum_history rebuilds them all, re-hashes every receipt,
--     recomputes every chain link, re-validates the selection order, and emits
--     the kernel's own [{admitted_at, receipt}] minimum_history in that order.
--   * The receipt digest is DERIVED from the artifact and a caller-named one is
--     only ever compared, so an artifact cannot be filed under a borrowed identity.
--   * ONE AUTHORITATIVE SCOPE HOLDS ONE INVENTORY, whose accepted TTL policy and
--     environment manifest are SEALED at opening: a changed policy, a changed
--     environment and a second label are each refused by name. One accepted scope
--     under two names hashes to ONE key, and a different accepted subject is a
--     different scope.
--   * A stale prior, a second opening, a reused idempotency key carrying a
--     changed payload, a re-presented artifact, a receipt from another producer
--     step, gate, role, oracle or scope, a receipt for another subject or
--     environment, and an overlong window are each refused BY NAME, with the
--     shared invariant id in the message.
--   * THE ORIGIN REGRESSION, IN ITS DISCRIMINATING FORM. The guard compares a
--     newcomer against the HEAD and not against the first row, because the
--     kernel skips inadmissible attempts and the row it selected need not be the
--     first one in the ledger. The fixture builds three candidates whose digests
--     are d1 < d2 < d3, admits d1 and then d3, and then offers d2: it is
--     STRICTLY GREATER than the first stored row, so a first-row comparison
--     would have admitted it, and strictly less than the head, so the kernel
--     could prefer it to a row already stored in that group. It is refused.
--   * THE FATAL SHAPE FACTS A00's seam validator leaves open are refused: a
--     wrong `safe:`/`session:` prefix, a malformed fixture-set digest, an
--     out-of-bounds comparator, and an identity seat with a fourth key, a wrong
--     name or an empty field. Each is a well-formed twenty-one-field receipt
--     from the right producer that the kernel would refuse fatally.
--   * TYPE PARITY, WHICH `->>` QUIETLY BREAKS. A numeric, null or object
--     comparator, and a numeric or null actor_id, authority_class or
--     session_ref, are each refused -- `->>` renders them as text, so a bare
--     regex or length would judge a value the JavaScript home refuses outright.
--   * THE COMPARATOR BOUND IS COUNTED IN UTF-16 CODE UNITS, BOTH WAYS. 151
--     astral characters are 151 codepoints and 302 units and are REFUSED; 150
--     are exactly 300 units and are ADMITTED; 3 are 3 codepoints -- below a
--     codepoint floor of 5 -- and 6 units, and are ADMITTED. The accepted half
--     is admitted for real, because narrowing the accepted domain would be as
--     wrong as widening it.
--   * NOTHING IS DISCARDED. A non-passing receipt is STORED and read back: the
--     kernel calls it an ordinary fact of the ledger and this rail has no filter.
--   * A readback whose rows are out of the selection order refuses rather than
--     being served, so a row that reached the tables by another route cannot
--     hand the kernel an origin this rail never admitted first.
--   * UPDATE, DELETE and TRUNCATE are refused everywhere. The truncate half is
--     the only thing that can show the statement-level trigger exists: a
--     row-level trigger never sees TRUNCATE, and the `revoke` does not bind the
--     table owner. The updated column is asserted to exist on both relations
--     first, so the negative cannot fail at parse and prove nothing.
--   * Direct INSERT is executable by none of the runtime role bundles, and the
--     append function reaches the writer and authority bundles only.
--   * Every function the module's postgres journal calls exists with the exact
--     arity it calls it at.
--   * The readback says, in its own fields, that this record layer admitted
--     nothing to a gate and lists what it cannot prove.
--
-- WHAT IT DOES NOT PROVE, named rather than implied:
--   * That ops.j1_minimum_receipt_digest and journeyOneMinimumReceiptDigest
--     produce the SAME hash for one receipt, or that ops.j1_minimum_admission_digest
--     and journeyOneMinimumAdmissionDigest agree. Both sides hash the canonical
--     serialization of the same preimage and this side reuses
--     ops.portfolio_canonical_json, which migration 0496 already reconciles
--     against artifact-trust.js's canonicalJson -- but a SQL fixture cannot
--     execute JavaScript. THIS IS THE SINGLE MOST IMPORTANT THING TO CHECK FIRST
--     when the rail is exercised end to end: a disagreement would give one
--     artifact two origin digests, one per language, and the clock would name an
--     origin the other side could not find.
--   * Anything the KERNEL does with these rows. No JavaScript runs here, so the
--     "the kernel would have refused origin_reset_or_rebase" half of the origin
--     regression lives in mcp-server/test/journey-one-clock-input-store.v5.test.mjs,
--     which drives the real kernel over the real store. This side proves only
--     that the row is refused.
--   * Anything about the concurrency of two SESSIONS. One psql session cannot
--     contend with itself, so the opening/append lock protocol is asserted
--     structurally and remains live-integration verification.
--   * The STRICTLY-LATER admitted_at case. One transaction is one instant, so
--     every admission here ties; the cross-instant half of the ordering rule is
--     a multi-transaction fact and is left to live integration.
--   * That any admitted artifact is a receipt an authenticated producer issued.
--     It is not: this file composes its own.
--   * That the scope or the accepted policy this inventory was opened under is
--     any projection's accepted one. Both are synthetic bindings this file
--     composed, exactly as a trusted integration would compose real ones.
--
-- Digests are LEARNED with ops.j1_minimum_receipt_digest, which hashes the
-- artifact without writing a row, so the fixture never guesses a hash or reads
-- one out of an error message.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_actor           text;
  v_scope_key       text;
  v_second_key      text;
  v_result          jsonb;
  v_readback        jsonb;
  v_count           integer;
  v_signature       text;
  v_role            text;
  v_relation        text;
  v_field           text;
  v_at              text;
  v_head            text;
  v_receipt         jsonb;
  v_variant         jsonb;
  v_candidates      jsonb;
  v_sorted          jsonb;
  v_d1              jsonb;
  v_d2              jsonb;
  v_d3              jsonb;

  v_third_key       text;
  v_accepted_long   jsonb;
  v_accepted_short  jsonb;

  v_tenant          constant text := 'carr-internal';
  -- U+1D11E MUSICAL SYMBOL G CLEF: one codepoint, TWO UTF-16 code units. It is
  -- what makes the comparator bound's two possible rulers give different
  -- answers, in both directions.
  v_astral          constant text := chr(119070);
  v_environment     constant text := 'sha256:' || repeat('44', 32);
  v_fixtures        constant text := 'sha256:' || repeat('55', 32);
  v_ttl_policy      constant bigint := 172800000;  -- 48 hours
  v_provenance      constant jsonb := jsonb_build_object(
                      'source_ref', 'safe:a00:postgres-proof-synthetic-minimum');
  v_key0            constant uuid := '00000000-0000-4000-8000-00000000d001';
  v_key1            constant uuid := '00000000-0000-4000-8000-00000000d002';
  v_key2            constant uuid := '00000000-0000-4000-8000-00000000d003';
  v_key3            constant uuid := '00000000-0000-4000-8000-00000000d004';
  v_key4            constant uuid := '00000000-0000-4000-8000-00000000d005';
  v_key5            constant uuid := '00000000-0000-4000-8000-00000000d006';
  v_key6            constant uuid := '00000000-0000-4000-8000-00000000d007';
  v_key_x           constant uuid := '00000000-0000-4000-8000-00000000d008';

  -- THE AUTHORITATIVE SCOPE, composed by this file exactly as a trusted
  -- integration would compose a real one. It is synthetic: nothing here proves
  -- it is any projection's accepted scope.
  v_scope           constant jsonb := jsonb_build_object(
                      'benchmark_candidate_digest', 'sha256:' || repeat('92', 32),
                      'benchmark_policy_digest', 'sha256:' || repeat('93', 32),
                      'benchmark_subject_digest', 'sha256:' || repeat('91', 32),
                      'clock_origin_gate_id', 'foundation-assurance-minimum-accepted',
                      'clock_terminus_gate_id', 'journey-one-kernel-production-accepted',
                      'scope_ref', 'safe:clock-scope:postgres-proof-minimum-inputs',
                      'tenant', 'carr-internal');
  -- THE SAME ACCEPTED SCOPE UNDER ANOTHER NAME. Identical identity fields; only
  -- the human label differs. It must hash to the SAME key, or "one scope, one
  -- inventory" would really be "one label, one inventory".
  v_scope_relabelled constant jsonb := v_scope || jsonb_build_object(
                      'scope_ref', 'safe:clock-scope:postgres-proof-minimum-inputs-renamed');
  -- A DIFFERENT accepted scope, for the origin-ordering regression's own inventory.
  v_scope_second    constant jsonb := v_scope || jsonb_build_object(
                      'benchmark_subject_digest', 'sha256:' || repeat('97', 32),
                      'scope_ref', 'safe:clock-scope:postgres-proof-minimum-inputs-second');
  -- A THIRD accepted scope, so the ACCEPTED comparator boundary can be admitted
  -- for real without disturbing the ordering of the inventories above.
  v_scope_third     constant jsonb := v_scope || jsonb_build_object(
                      'benchmark_subject_digest', 'sha256:' || repeat('98', 32),
                      'scope_ref', 'safe:clock-scope:postgres-proof-minimum-inputs-third');
begin
  -- --- prerequisites -------------------------------------------------------
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'j1_minimum_append_admission') then
    raise notice 'SKIPPED: ops.j1_minimum_append_admission is absent; ops/journey-one-clock-input-store.candidate.sql has not been applied here yet.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'j1_clock_scope_digest') then
    raise notice 'SKIPPED: ops.j1_clock_scope_digest is absent; this rail reuses the clock rail''s ONE scope derivation and ops/journey-one-clock-store.candidate.sql has not been applied here yet.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'portfolio_canonical_json')
     or not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'ops' and p.proname = 'portfolio_writer_actor_id') then
    raise notice 'SKIPPED: migration 0496 is absent; this rail reuses its canonicalizer and writer context rather than restating them.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'benchmark_utf16_length') then
    raise notice 'SKIPPED: ops.benchmark_utf16_length is absent; the comparator bound is stated in UTF-16 code units and this rail reuses that ONE counter rather than defining a second. ops/benchmark-acceptance.candidate.sql has not been applied here yet.';
    return;
  end if;
  select slug into v_actor from public.actor where active order by slug collate "C" limit 1;
  if v_actor is null then
    raise notice 'SKIPPED: no active actor exists; this proof creates none.';
    return;
  end if;
  perform set_config('carr.acting_actor_slug', v_actor, true);

  -- --- ARITY. Every function the module's postgres journal calls, with the
  -- --- exact signature it calls it at. A journal naming a function this file
  -- --- does not define would fail at the first live call.
  foreach v_signature in array array[
    'ops.j1_minimum_lock(text)',
    'ops.j1_minimum_admission_instant()',
    'ops.j1_minimum_inventory_row(text)',
    'ops.j1_minimum_head(text)',
    'ops.j1_minimum_admissions(text)',
    'ops.j1_minimum_admission_by_idempotency_key(uuid)',
    'ops.j1_minimum_open_inventory(jsonb,bigint,text)',
    'ops.j1_minimum_append_admission(text,text,uuid,text,text,text,jsonb,jsonb)',
    'ops.j1_minimum_receipt_digest(jsonb)',
    'ops.j1_minimum_admission_digest(text,text,text,bigint,text,text,text)',
    'ops.j1_minimum_receipt_fields()',
    'ops.j1_minimum_record_layer_cannot_prove()',
    'ops.j1_minimum_history(text)',
    'ops.j1_clock_scope_digest(jsonb)'
  ] loop
    if to_regprocedure(v_signature) is null then
      raise exception 'ARITY: % is not defined with that signature', v_signature;
    end if;
  end loop;

  -- --- the synthetic artifacts -----------------------------------------------
  -- Observed an hour ago with a 24-hour window, so each is current at the
  -- admission instant and well inside the accepted 48-hour policy.
  v_at := ops.j1_minimum_admission_instant();
  v_receipt := jsonb_build_object(
    'gate_id', 'foundation-assurance-minimum-accepted',
    'receipt_producer_step_ref', 'step:foundation-assurance-minimum-receipt',
    'subject_digest', v_scope ->> 'benchmark_subject_digest',
    'candidate_digest', v_scope ->> 'benchmark_candidate_digest',
    'policy_digest', v_scope ->> 'benchmark_policy_digest',
    'environment_manifest_digest', v_environment,
    'subject_environment', 'candidate',
    'evidence_scope', 'candidate-and-test',
    'subject_maker_identity', jsonb_build_object(
      'actor_id', 'proof-maker', 'session_ref', 'session:postgres-proof-maker',
      'authority_class', 'synthetic_oracle'),
    'producer_identity', jsonb_build_object(
      'actor_id', 'proof-producer', 'session_ref', 'session:postgres-proof-producer',
      'authority_class', 'synthetic_oracle'),
    'evaluator_identity', jsonb_build_object(
      'actor_id', 'proof-evaluator', 'session_ref', 'session:postgres-proof-evaluator',
      'authority_class', 'synthetic_oracle'),
    'producer_role', 'independent_foundation_assurance_minimum_oracle',
    'independent_oracle_ref', 'oracle:gate-producer:foundation-assurance-minimum',
    'oracle_version', '1.0.0',
    'evidence_ref', 'safe:postgres-proof:minimum-evidence-one',
    'fixture_set_digest', v_fixtures,
    'observed_at', to_char(now() at time zone 'UTC' - interval '1 hour', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'ttl_expires_at', to_char(now() at time zone 'UTC' + interval '23 hours', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'status', 'pass',
    'comparator', 'postgres-proof exact comparator',
    'negative_admission_result', 'all_required_denials_observed');

  -- THREE ARTIFACTS FOR THE FIRST INVENTORY, one of them a NON-PASSING attempt.
  -- They are sorted by digest before anything is written, because one
  -- transaction is one admission instant and the stored order within an instant
  -- is the kernel's own digest order. The status plays no part in the ordering,
  -- which is exactly the point: this rail computes no eligibility.
  v_candidates := jsonb_build_array(
    v_receipt,
    v_receipt || jsonb_build_object('evidence_ref', 'safe:postgres-proof:minimum-evidence-two'),
    v_receipt || jsonb_build_object(
      'status', 'fail', 'evidence_ref', 'safe:postgres-proof:failed-attempt'));
  -- COLLATE "C": byte order, the same rule the guard and the module compare
  -- with. A locale-dependent sort here would build a fixture whose expectations
  -- disagree with the invariant it is testing.
  select jsonb_agg(t.r order by ops.j1_minimum_receipt_digest(t.r) collate "C")
    into v_sorted from jsonb_array_elements(v_candidates) as t(r);
  v_d1 := v_sorted -> 0; v_d2 := v_sorted -> 1; v_d3 := v_sorted -> 2;

  -- --- THE INVENTORY IS OPENED BEFORE ANY ADMISSION -------------------------
  -- The key is DERIVED from the scope's own fields by the CLOCK rail's function;
  -- a caller-supplied one would be a self-chosen address wearing a hash.
  v_result := ops.j1_minimum_open_inventory(v_scope, v_ttl_policy, v_environment);
  v_scope_key := v_result ->> 'clock_scope_key';
  if v_scope_key is distinct from ops.j1_clock_scope_digest(v_scope) then
    raise exception 'SCOPE: the inventory did not derive its key from the scope it was handed';
  end if;
  -- ONE ACCEPTED SCOPE UNDER TWO NAMES IS ONE KEY, and a different accepted
  -- subject is a different scope.
  if ops.j1_clock_scope_digest(v_scope_relabelled) is distinct from v_scope_key then
    raise exception 'SCOPE: relabelling an accepted scope produced a second key';
  end if;
  if ops.j1_clock_scope_digest(v_scope_second) = v_scope_key then
    raise exception 'SCOPE: two different accepted scopes produced one key';
  end if;
  -- OPENING IS IDEMPOTENT for the exact binding.
  perform ops.j1_minimum_open_inventory(v_scope, v_ttl_policy, v_environment);
  select count(*) into v_count from ops.j1_minimum_inventory where clock_scope_key = v_scope_key;
  if v_count <> 1 then
    raise exception 'SCOPE: an exact reopen wrote a second inventory row';
  end if;
  -- THE ACCEPTED POLICY AND THE LABEL ARE SEALED.
  begin
    perform ops.j1_minimum_open_inventory(v_scope, v_ttl_policy * 2, v_environment);
    raise exception 'NEGATIVE FAILED: a widened accepted TTL policy was applied to a sealed inventory';
  exception when others then
    if sqlerrm not like '%j1_minimum_inventory_policy_sealed%' then raise; end if;
  end;
  begin
    perform ops.j1_minimum_open_inventory(v_scope, v_ttl_policy, 'sha256:' || repeat('4f', 32));
    raise exception 'NEGATIVE FAILED: a changed accepted environment manifest was applied to a sealed inventory';
  exception when others then
    if sqlerrm not like '%j1_minimum_inventory_policy_sealed%' then raise; end if;
  end;
  begin
    perform ops.j1_minimum_open_inventory(v_scope_relabelled, v_ttl_policy, v_environment);
    raise exception 'NEGATIVE FAILED: a relabelled scope replaced the label its inventory was opened under';
  exception when others then
    if sqlerrm not like '%j1_minimum_scope_label_is_not_identity%' then raise; end if;
  end;

  -- --- THE OPENING ADMISSION -------------------------------------------------
  v_result := ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key0,
    null, v_at, ops.j1_minimum_receipt_digest(v_d1), v_d1, v_provenance);
  if (v_result ->> 'admission_ordinal')::integer <> 0
     or (v_result ->> 'replayed')::boolean is distinct from false
     or (v_result ->> 'previous_admission_digest') is not null then
    raise exception 'POSITIVE: the opening admission did not land as ordinal 0 with a null prior';
  end if;
  -- THE ADMISSION INSTANT IS THE DATABASE'S OWN, and it is the same reading the
  -- caller took from ops.j1_minimum_admission_instant() before the call.
  if (v_result ->> 'admitted_at') is distinct from v_at then
    raise exception 'INSTANT: the stored admission instant is not the one this transaction derives';
  end if;
  if (v_result ->> 'recomputed_receipt_digest')
     is distinct from (v_result ->> 'receipt_digest') then
    raise exception 'CONTENT: the stored receipt does not hash to the digest it was admitted under';
  end if;
  v_head := v_result ->> 'admission_digest';

  -- A CALLER-NAMED ADMISSION INSTANT IS ONLY EVER COMPARED.
  begin
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, '2020-01-01T00:00:00.000Z', ops.j1_minimum_receipt_digest(v_d2),
      v_d2, v_provenance);
    raise exception 'NEGATIVE FAILED: a caller dated its own admission';
  exception when others then
    if sqlerrm not like '%j1_minimum_admission_instant_is_server_time%' then raise; end if;
  end;
  -- AND SO IS A CALLER-NAMED RECEIPT DIGEST.
  begin
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, v_at, 'sha256:' || repeat('ab', 32), v_d2, v_provenance);
    raise exception 'NEGATIVE FAILED: an artifact was filed under a borrowed identity';
  exception when others then
    if sqlerrm not like '%j1_minimum_claimed_digest_is_never_trusted%' then raise; end if;
  end;

  -- --- THE COMPARE-AND-SWAP --------------------------------------------------
  begin
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      'sha256:' || repeat('cd', 32), v_at, ops.j1_minimum_receipt_digest(v_d2),
      v_d2, v_provenance);
    raise exception 'NEGATIVE FAILED: a stale prior chain digest was accepted';
  exception when others then
    if sqlerrm not like '%j1_minimum_exact_prior_admission_digest%' then raise; end if;
  end;
  begin
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      null, v_at, ops.j1_minimum_receipt_digest(v_d2), v_d2, v_provenance);
    raise exception 'NEGATIVE FAILED: an inventory was opened twice';
  exception when others then
    if sqlerrm not like '%j1_minimum_exact_prior_admission_digest%' then raise; end if;
  end;
  -- AN ARTIFACT IS ADMITTED ONCE. Re-presenting it is a replay of evidence.
  begin
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, v_at, ops.j1_minimum_receipt_digest(v_d1), v_d1, v_provenance);
    raise exception 'NEGATIVE FAILED: one artifact was admitted twice';
  exception when others then
    if sqlerrm not like '%j1_minimum_receipt_never_readmitted%' then raise; end if;
  end;

  v_result := ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key1,
    v_head, v_at, ops.j1_minimum_receipt_digest(v_d2), v_d2, v_provenance);
  if (v_result ->> 'admission_ordinal')::integer <> 1
     or (v_result ->> 'previous_admission_digest') is distinct from v_head then
    raise exception 'POSITIVE: the second admission did not chain onto the head';
  end if;

  -- --- IDEMPOTENCY -----------------------------------------------------------
  v_result := ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key1,
    v_head, v_at, ops.j1_minimum_receipt_digest(v_d2), v_d2, v_provenance);
  if (v_result ->> 'replayed')::boolean is distinct from true then
    raise exception 'IDEMPOTENCY: an exact repeat was not replayed';
  end if;
  select count(*) into v_count from ops.j1_minimum_admission a
    join ops.j1_minimum_inventory i on i.id = a.inventory_id
   where i.clock_scope_key = v_scope_key;
  if v_count <> 2 then
    raise exception 'IDEMPOTENCY: a replay wrote a second row';
  end if;
  v_head := v_result ->> 'admission_digest';
  begin
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key1,
      v_head, v_at, ops.j1_minimum_receipt_digest(v_d3), v_d3, v_provenance);
    raise exception 'NEGATIVE FAILED: one idempotency key carried two payloads';
  exception when others then
    if sqlerrm not like '%j1_minimum_idempotency_key_binds_its_payload%' then raise; end if;
  end;

  -- --- THE FATAL-IN-KERNEL REFUSALS -----------------------------------------
  foreach v_field in array array[
    'receipt_producer_step_ref', 'producer_role', 'independent_oracle_ref',
    'oracle_version', 'evidence_scope', 'subject_environment', 'gate_id'
  ] loop
    v_variant := v_receipt || jsonb_build_object(
      v_field, 'a-value-no-minimum-producer-issues',
      'evidence_ref', 'safe:postgres-proof:producer-' || v_field);
    begin
      perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
        v_head, v_at, ops.j1_minimum_receipt_digest(v_variant), v_variant, v_provenance);
      raise exception 'NEGATIVE FAILED: a receipt with a wrong % was admitted', v_field;
    exception when others then
      if sqlerrm not like '%j1_minimum_receipt_producer_bound%' then raise; end if;
    end;
  end loop;
  -- A field added or removed is not a consumer-gate-receipt.v1 at all.
  begin
    v_variant := v_receipt || jsonb_build_object('schema_version', 'consumer-gate-receipt.v1');
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, v_at, ops.j1_minimum_receipt_digest(v_variant), v_variant, v_provenance);
    raise exception 'NEGATIVE FAILED: a receipt carrying an extra field was admitted';
  exception when others then
    if sqlerrm not like '%j1_minimum_receipt_producer_bound%' then raise; end if;
  end;
  begin
    v_variant := v_receipt - 'comparator';
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, v_at, ops.j1_minimum_receipt_digest(v_variant), v_variant, v_provenance);
    raise exception 'NEGATIVE FAILED: a receipt missing a required field was admitted';
  exception when others then
    if sqlerrm not like '%j1_minimum_receipt_producer_bound%' then raise; end if;
  end;
  -- THE FATAL SHAPE FACTS A00'S SEAM VALIDATOR LEAVES OPEN. Each of these is a
  -- valid-shaped twenty-one-field receipt from the right producer that the
  -- KERNEL refuses fatally, so one stored row would make every later evaluation
  -- of this inventory throw and the inventory could not shed it.
  -- THE TWO COUNTERS DIFFER, AND THE FIXTURE SHOWS IT RATHER THAN ASSUMING IT.
  -- One astral character is one codepoint and two UTF-16 code units, which is
  -- why char_length() is the wrong ruler for a bound the kernel states in units.
  if char_length(repeat(v_astral, 150)) <> 150
     or ops.benchmark_utf16_length(repeat(v_astral, 150)) <> 300 then
    raise exception 'FIXTURE: the astral character is not astral here, so the counting cases discriminate nothing';
  end if;

  foreach v_field in array array[
    'evidence_ref', 'evidence_ref_numeric', 'fixture_set_digest', 'fixture_set_digest_numeric',
    'comparator_short', 'comparator_numeric', 'comparator_null', 'comparator_object',
    'comparator_astral_over', 'comparator_ascii_over',
    'identity_extra_key', 'identity_empty_actor', 'identity_bad_session_prefix',
    'identity_actor_numeric', 'identity_authority_class_numeric',
    'identity_session_ref_numeric', 'identity_actor_null', 'identity_wrong_names'
  ] loop
    v_variant := case v_field
      when 'evidence_ref' then v_receipt || jsonb_build_object(
        'evidence_ref', 'notsafe:postgres-proof:wrong-prefix')
      -- `->>` renders 12345 as '12345', so a bare regex or length judges a value
      -- the JavaScript home refuses outright. These are the type parity cases.
      when 'evidence_ref_numeric' then v_receipt || jsonb_build_object('evidence_ref', 12345)
      when 'fixture_set_digest' then v_receipt || jsonb_build_object(
        'fixture_set_digest', 'not-a-digest', 'evidence_ref', 'safe:postgres-proof:bad-fixture')
      when 'fixture_set_digest_numeric' then v_receipt || jsonb_build_object(
        'fixture_set_digest', 12345, 'evidence_ref', 'safe:postgres-proof:numeric-fixture')
      when 'comparator_short' then v_receipt || jsonb_build_object(
        'comparator', 'x', 'evidence_ref', 'safe:postgres-proof:short-comparator')
      when 'comparator_numeric' then v_receipt || jsonb_build_object(
        'comparator', 12345, 'evidence_ref', 'safe:postgres-proof:numeric-comparator')
      when 'comparator_null' then v_receipt || jsonb_build_object(
        'comparator', null::text, 'evidence_ref', 'safe:postgres-proof:null-comparator')
      when 'comparator_object' then v_receipt || jsonb_build_object(
        'comparator', jsonb_build_object('text', 'an exact comparator'),
        'evidence_ref', 'safe:postgres-proof:object-comparator')
      -- 151 astral characters: 151 CODEPOINTS and 302 UTF-16 units. A codepoint
      -- bound admits it; the kernel refuses it.
      when 'comparator_astral_over' then v_receipt || jsonb_build_object(
        'comparator', repeat(v_astral, 151),
        'evidence_ref', 'safe:postgres-proof:astral-over-comparator')
      when 'comparator_ascii_over' then v_receipt || jsonb_build_object(
        'comparator', repeat('c', 301),
        'evidence_ref', 'safe:postgres-proof:ascii-over-comparator')
      when 'identity_extra_key' then v_receipt || jsonb_build_object(
        'producer_identity', (v_receipt -> 'producer_identity') || jsonb_build_object('display_name', 'x'),
        'evidence_ref', 'safe:postgres-proof:seat-extra-key')
      when 'identity_empty_actor' then v_receipt || jsonb_build_object(
        'producer_identity', (v_receipt -> 'producer_identity') || jsonb_build_object('actor_id', ''),
        'evidence_ref', 'safe:postgres-proof:seat-empty-actor')
      when 'identity_bad_session_prefix' then v_receipt || jsonb_build_object(
        'producer_identity', (v_receipt -> 'producer_identity')
          || jsonb_build_object('session_ref', 'notsession:postgres-proof'),
        'evidence_ref', 'safe:postgres-proof:seat-bad-session')
      when 'identity_actor_numeric' then v_receipt || jsonb_build_object(
        'producer_identity', (v_receipt -> 'producer_identity') || jsonb_build_object('actor_id', 7),
        'evidence_ref', 'safe:postgres-proof:seat-numeric-actor')
      when 'identity_authority_class_numeric' then v_receipt || jsonb_build_object(
        'producer_identity', (v_receipt -> 'producer_identity') || jsonb_build_object('authority_class', 7),
        'evidence_ref', 'safe:postgres-proof:seat-numeric-class')
      when 'identity_session_ref_numeric' then v_receipt || jsonb_build_object(
        'producer_identity', (v_receipt -> 'producer_identity') || jsonb_build_object('session_ref', 7),
        'evidence_ref', 'safe:postgres-proof:seat-numeric-session')
      when 'identity_actor_null' then v_receipt || jsonb_build_object(
        'producer_identity', (v_receipt -> 'producer_identity') || jsonb_build_object('actor_id', null::text),
        'evidence_ref', 'safe:postgres-proof:seat-null-actor')
      -- THREE KEYS, WRONG NAMES. The count passes; the required-name type tests
      -- are what refuse it, which is why both checks exist.
      else v_receipt || jsonb_build_object(
        'producer_identity', jsonb_build_object(
          'actor_id', 'proof-producer', 'authority_class', 'synthetic_oracle',
          'session_reference', 'session:postgres-proof-producer'),
        'evidence_ref', 'safe:postgres-proof:seat-wrong-names')
    end;
    begin
      perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
        v_head, v_at, ops.j1_minimum_receipt_digest(v_variant), v_variant, v_provenance);
      raise exception 'NEGATIVE FAILED: a receipt the kernel could not read was admitted (%)', v_field;
    exception when others then
      if sqlerrm not like '%j1_minimum_receipt_readable_by_kernel%' then raise; end if;
    end;
  end loop;

  -- THE ACCEPTED SCOPE DECIDES, NOT THE RECEIPT.
  begin
    v_variant := v_receipt || jsonb_build_object(
      'subject_digest', 'sha256:' || repeat('9f', 32),
      'evidence_ref', 'safe:postgres-proof:other-subject');
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, v_at, ops.j1_minimum_receipt_digest(v_variant), v_variant, v_provenance);
    raise exception 'NEGATIVE FAILED: a receipt for another accepted subject was admitted';
  exception when others then
    if sqlerrm not like '%j1_minimum_receipt_binds_accepted_scope%' then raise; end if;
  end;
  begin
    v_variant := v_receipt || jsonb_build_object(
      'environment_manifest_digest', 'sha256:' || repeat('9e', 32),
      'evidence_ref', 'safe:postgres-proof:other-environment');
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, v_at, ops.j1_minimum_receipt_digest(v_variant), v_variant, v_provenance);
    raise exception 'NEGATIVE FAILED: a receipt for another environment manifest was admitted';
  exception when others then
    if sqlerrm not like '%j1_minimum_receipt_binds_accepted_scope%' then raise; end if;
  end;
  -- AN OVERLONG WINDOW IS A MISISSUED RECEIPT, refused fatally by the kernel.
  begin
    v_variant := v_receipt || jsonb_build_object(
      'ttl_expires_at', to_char(now() at time zone 'UTC' + interval '400 days', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'evidence_ref', 'safe:postgres-proof:overlong-window');
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, v_at, ops.j1_minimum_receipt_digest(v_variant), v_variant, v_provenance);
    raise exception 'NEGATIVE FAILED: a window longer than the accepted policy was admitted';
  exception when others then
    if sqlerrm not like '%j1_minimum_receipt_window_within_accepted_policy%' then raise; end if;
  end;
  -- ONE INSTANT OF SKEW. The receipt claims an observation the record layer
  -- cannot yet have seen, which the kernel refuses fatally.
  begin
    v_variant := v_receipt || jsonb_build_object(
      'observed_at', to_char(now() at time zone 'UTC' + interval '1 second', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'evidence_ref', 'safe:postgres-proof:skewed-observation');
    perform ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key_x,
      v_head, v_at, ops.j1_minimum_receipt_digest(v_variant), v_variant, v_provenance);
    raise exception 'NEGATIVE FAILED: a receipt observed after its own admission was stored';
  exception when others then
    if sqlerrm not like '%j1_minimum_admission_not_before_observation%' then raise; end if;
  end;
  -- AN APPEND FOR AN UNOPENED INVENTORY.
  begin
    perform ops.j1_minimum_append_admission(ops.j1_clock_scope_digest(v_scope_second),
      v_tenant, v_key_x, null, v_at, ops.j1_minimum_receipt_digest(v_d3),
      v_d3, v_provenance);
    raise exception 'NEGATIVE FAILED: an admission landed in an inventory nobody opened';
  exception when others then
    if sqlerrm not like '%j1_minimum_inventory_scope_bound%' then raise; end if;
  end;

  -- --- NOTHING IS DISCARDED --------------------------------------------------
  -- The third artifact completes the group. One of these three is a non-passing
  -- attempt -- real history the kernel skips when it selects an origin -- and it
  -- is stored in its digest position like any other, because this rail filters
  -- nothing and its status plays no part in the order.
  v_result := ops.j1_minimum_append_admission(v_scope_key, v_tenant, v_key2,
    v_head, v_at, ops.j1_minimum_receipt_digest(v_d3), v_d3, v_provenance);
  v_head := v_result ->> 'admission_digest';
  select count(*) into v_count from ops.j1_minimum_admission a
    join ops.j1_minimum_inventory i on i.id = a.inventory_id
   where i.clock_scope_key = v_scope_key and a.status = 'fail';
  if v_count <> 1 then
    raise exception 'DISCARD: the non-passing attempt was not stored verbatim';
  end if;

  -- --- THE READBACK ----------------------------------------------------------
  -- Read BEFORE the second inventory is built, while v_sorted still holds this
  -- inventory's own three artifacts to compare against.
  v_readback := ops.j1_minimum_history(v_scope_key);
  if (v_readback ->> 'exists')::boolean is distinct from true
     or (v_readback ->> 'admission_count')::integer <> 3 then
    raise exception 'READBACK: the inventory did not read back with its three admissions';
  end if;
  if (v_readback ->> 'head_admission_digest') is distinct from v_head then
    raise exception 'READBACK: the head chain digest is not the one the last append produced';
  end if;
  -- THE KERNEL'S OWN SHAPE: [{admitted_at, receipt}], and nothing else.
  if jsonb_array_length(v_readback -> 'minimum_history') <> 3
     or (select count(*) from jsonb_array_elements(v_readback -> 'minimum_history') e
          where (select count(*) from jsonb_object_keys(e.value)) <> 2
             or not (e.value ? 'admitted_at') or not (e.value ? 'receipt')) <> 0 then
    raise exception 'READBACK: minimum_history is not the kernel''s closed {admitted_at, receipt} shape';
  end if;
  -- IN THE KERNEL'S SELECTION ORDER, artifacts unchanged. The three admitted
  -- artifacts come back in digest order, whatever their statuses.
  if (v_readback -> 'minimum_history' -> 0 -> 'receipt') is distinct from v_d1
     or (v_readback -> 'minimum_history' -> 1 -> 'receipt') is distinct from v_d2
     or (v_readback -> 'minimum_history' -> 2 -> 'receipt') is distinct from v_d3 then
    raise exception 'READBACK: the admitted artifacts did not come back unchanged in the kernel''s selection order';
  end if;
  if (select bool_or(a.receipt_digest collate "C" <= b.receipt_digest)
        from ops.j1_minimum_admission a
        join ops.j1_minimum_admission b
          on b.inventory_id = a.inventory_id
         and b.admission_ordinal = a.admission_ordinal - 1
        join ops.j1_minimum_inventory i on i.id = a.inventory_id
       where i.clock_scope_key = v_scope_key) then
    raise exception 'READBACK: the stored inventory is not in the kernel''s digest order within its admission instant';
  end if;
  if (v_readback ->> 'gate_admitted_by_record_layer')::boolean is distinct from false
     or jsonb_array_length(v_readback -> 'record_layer_cannot_prove') < 5 then
    raise exception 'READBACK: the readback does not say what it cannot prove';
  end if;
  v_variant := ops.j1_minimum_history(ops.j1_clock_scope_digest(
    v_scope || jsonb_build_object('benchmark_policy_digest', 'sha256:' || repeat('7a', 32))));
  if v_variant ->> 'exists' <> 'false' then
    raise exception 'READBACK: an unopened inventory reported as existing';
  end if;
  -- AND ITS KEY SET IS THE MODULE'S OWN exists:false SHAPE, not five of its six
  -- keys. read() returns schema_version, clock_scope_key, tenant, exists,
  -- record_layer_cannot_prove and effects -- and no gate_admitted_by_record_layer,
  -- because an inventory that does not exist has admitted nothing to say false
  -- about. A parity claim that is off by one field is the reason nobody rechecks.
  if (select array_agg(k order by k) from jsonb_object_keys(v_variant) as t(k))
     is distinct from array['clock_scope_key', 'effects', 'exists',
                            'record_layer_cannot_prove', 'schema_version', 'tenant']::text[] then
    raise exception 'READBACK: the exists:false branch is not the module''s exists:false key set: %',
      (select string_agg(k, ',' order by k) from jsonb_object_keys(v_variant) as t(k));
  end if;
  if (v_variant -> 'effects' ->> 'database_writes') <> '0' then
    raise exception 'READBACK: a zero-write readback must report zero database writes';
  end if;

  -- --- THE ORIGIN REGRESSION, IN ITS DISCRIMINATING FORM ---------------------
  -- The guard compares a newcomer against the HEAD, not against the first row.
  -- That distinction is the whole defect: the kernel skips inadmissible attempts
  -- and selects the first ELIGIBLE row, so the row it chose need not be the
  -- first one in the ledger, and a comparison anchored on the first row leaves
  -- the chosen one unprotected.
  --
  -- Three candidates with digests d1 < d2 < d3. Admit d1, then d3. Now offer d2:
  -- it is STRICTLY GREATER than the first stored row -- so a first-row
  -- comparison admits it -- and strictly less than the head, so the kernel could
  -- prefer it to a row already stored in this group.
  v_second_key := ops.j1_minimum_open_inventory(
    v_scope_second, v_ttl_policy, v_environment) ->> 'clock_scope_key';
  v_candidates := jsonb_build_array(
    v_receipt || jsonb_build_object(
      'subject_digest', v_scope_second ->> 'benchmark_subject_digest',
      'evidence_ref', 'safe:postgres-proof:order-a'),
    v_receipt || jsonb_build_object(
      'subject_digest', v_scope_second ->> 'benchmark_subject_digest',
      'evidence_ref', 'safe:postgres-proof:order-b'),
    v_receipt || jsonb_build_object(
      'subject_digest', v_scope_second ->> 'benchmark_subject_digest',
      'evidence_ref', 'safe:postgres-proof:order-c'));
  -- COLLATE "C": byte order, the same rule the guard and the module compare
  -- with. A locale-dependent sort here would build a fixture whose expectations
  -- disagree with the invariant it is testing.
  select jsonb_agg(t.r order by ops.j1_minimum_receipt_digest(t.r) collate "C")
    into v_sorted from jsonb_array_elements(v_candidates) as t(r);
  v_d1 := v_sorted -> 0; v_d2 := v_sorted -> 1; v_d3 := v_sorted -> 2;

  v_result := ops.j1_minimum_append_admission(v_second_key, v_tenant, v_key3,
    null, v_at, ops.j1_minimum_receipt_digest(v_d1), v_d1, v_provenance);
  v_result := ops.j1_minimum_append_admission(v_second_key, v_tenant, v_key4,
    v_result ->> 'admission_digest', v_at, ops.j1_minimum_receipt_digest(v_d3),
    v_d3, v_provenance);
  -- The discrimination, asserted rather than assumed: the challenger really is
  -- above the first stored row, so the retired comparison would have taken it.
  if ops.j1_minimum_receipt_digest(v_d2) collate "C" <= ops.j1_minimum_receipt_digest(v_d1)
     or ops.j1_minimum_receipt_digest(v_d2) collate "C" >= ops.j1_minimum_receipt_digest(v_d3) then
    raise exception 'FIXTURE: the ordering candidates were not sorted, so this case discriminates nothing';
  end if;
  begin
    perform ops.j1_minimum_append_admission(v_second_key, v_tenant, v_key_x,
      v_result ->> 'admission_digest', v_at, ops.j1_minimum_receipt_digest(v_d2),
      v_d2, v_provenance);
    raise exception 'NEGATIVE FAILED: an admission below the head was accepted because it was above the first row';
  exception when others then
    if sqlerrm not like '%j1_minimum_first_origin_never_replaced%' then raise; end if;
  end;

  -- --- THE ACCEPTED COMPARATOR DOMAIN, ADMITTED FOR REAL ---------------------
  -- A bound is only right if it accepts what the kernel accepts. Both of these
  -- are refused by a CODEPOINT ruler and accepted by the kernel's UTF-16 one, so
  -- they are the half of the parity that a negative-only fixture would miss:
  --   * 150 astral characters -- 150 codepoints, exactly 300 UTF-16 units, the
  --     upper boundary itself;
  --   * 3 astral characters -- 3 codepoints, which a codepoint floor of 5 would
  --     REFUSE, and 6 UTF-16 units, which the kernel accepts.
  -- Narrowing the accepted domain would be as wrong as widening it.
  v_third_key := ops.j1_minimum_open_inventory(
    v_scope_third, v_ttl_policy, v_environment) ->> 'clock_scope_key';
  v_accepted_long := v_receipt || jsonb_build_object(
    'subject_digest', v_scope_third ->> 'benchmark_subject_digest',
    'comparator', repeat(v_astral, 150),
    'evidence_ref', 'safe:postgres-proof:astral-boundary-300');
  v_accepted_short := v_receipt || jsonb_build_object(
    'subject_digest', v_scope_third ->> 'benchmark_subject_digest',
    'comparator', repeat(v_astral, 3),
    'evidence_ref', 'safe:postgres-proof:astral-boundary-6');
  if ops.benchmark_utf16_length(v_accepted_long ->> 'comparator') <> 300
     or ops.benchmark_utf16_length(v_accepted_short ->> 'comparator') <> 6
     or char_length(v_accepted_short ->> 'comparator') >= 5 then
    raise exception 'FIXTURE: the accepted comparator boundary cases do not straddle the two rulers, so they prove nothing';
  end if;
  -- Admitted in digest order, because one transaction is one admission instant.
  v_candidates := jsonb_build_array(v_accepted_long, v_accepted_short);
  select jsonb_agg(t.r order by ops.j1_minimum_receipt_digest(t.r) collate "C")
    into v_sorted from jsonb_array_elements(v_candidates) as t(r);
  v_result := ops.j1_minimum_append_admission(v_third_key, v_tenant, v_key5,
    null, v_at, ops.j1_minimum_receipt_digest(v_sorted -> 0), v_sorted -> 0, v_provenance);
  v_result := ops.j1_minimum_append_admission(v_third_key, v_tenant, v_key6,
    v_result ->> 'admission_digest', v_at, ops.j1_minimum_receipt_digest(v_sorted -> 1),
    v_sorted -> 1, v_provenance);
  if (v_result ->> 'admission_ordinal')::integer <> 1 then
    raise exception 'ACCEPTED DOMAIN: an astral comparator inside the kernel''s bound was not admitted';
  end if;
  if (ops.j1_minimum_history(v_third_key) ->> 'admission_count')::integer <> 2 then
    raise exception 'ACCEPTED DOMAIN: the admitted astral-comparator receipts did not read back';
  end if;

  -- --- APPEND-ONLY -----------------------------------------------------------
  -- THE UPDATED COLUMN MUST EXIST ON BOTH RELATIONS, AND THAT IS ASSERTED FIRST.
  -- An earlier revision of this fixture updated `tenant`, which ops.j1_minimum_
  -- inventory has and ops.j1_minimum_admission does not -- its tenant is reached
  -- through the inventory join. That statement fails at PARSE with 42703 before
  -- the BEFORE UPDATE trigger can fire, the handler below sees a message with no
  -- invariant id in it, re-raises, and the whole proof aborts having proved
  -- nothing about append-only. A negative that cannot reach the thing it is
  -- testing is worse than no negative, so the column is checked rather than
  -- assumed. minimum_receipt_ttl_policy_ms is on both relations, and updating it
  -- is also the exact retroactive policy revision the seal forbids.
  --
  -- AND THE STATEMENT MUST BE ABLE TO REACH THE TRIGGER AT ALL. update, delete
  -- and truncate are revoked from every runtime bundle at the foot of the
  -- candidate SQL, so a run as `carr_writer` fails each attempt below with
  -- insufficient-privilege (42501) BEFORE the trigger fires. The handlers match
  -- on the invariant id in the message, so they would re-raise and abort the
  -- whole proof having proved nothing about append-only -- the same defect as
  -- the missing column above, gated on ROLE rather than on schema. The owner is
  -- the intended and natural mode (has_table_privilege reports true for an owner
  -- without an explicit grant), so this is a SKIP with a notice and not a
  -- failure: the fixture says what it could not test instead of claiming it.
  --
  -- THE COLUMN PRECHECK IS STRUCTURAL AND IS NOT GATED ON THE ROLE. Reading
  -- information_schema.columns needs no privilege on the relation, so a run that
  -- has to SKIP the attempts below still asserts that the column those attempts
  -- name exists on both relations.
  foreach v_relation in array array['j1_minimum_inventory', 'j1_minimum_admission'] loop
    if not exists (select 1 from information_schema.columns
                    where table_schema = 'ops' and table_name = v_relation
                      and column_name = 'minimum_receipt_ttl_policy_ms') then
      raise exception 'FIXTURE: ops.% has no minimum_receipt_ttl_policy_ms column, so this negative would fail at parse instead of reaching the append-only trigger',
        v_relation;
    end if;
  end loop;
  if not (has_table_privilege(current_user, 'ops.j1_minimum_inventory', 'UPDATE')
      and has_table_privilege(current_user, 'ops.j1_minimum_inventory', 'DELETE')
      and has_table_privilege(current_user, 'ops.j1_minimum_inventory', 'TRUNCATE')
      and has_table_privilege(current_user, 'ops.j1_minimum_admission', 'UPDATE')
      and has_table_privilege(current_user, 'ops.j1_minimum_admission', 'DELETE')
      and has_table_privilege(current_user, 'ops.j1_minimum_admission', 'TRUNCATE')) then
    raise notice 'SKIPPED: % holds no UPDATE/DELETE/TRUNCATE on the admitted-minimum relations, so the append-only negatives would fail with insufficient privilege before reaching the trigger and would prove nothing. Run this fixture as the table owner to exercise them.',
      current_user;
  else
  foreach v_relation in array array['j1_minimum_inventory', 'j1_minimum_admission'] loop
    begin
      execute format(
        'update ops.%I set minimum_receipt_ttl_policy_ms = minimum_receipt_ttl_policy_ms + 1 where true',
        v_relation);
      raise exception 'NEGATIVE FAILED: update was accepted on ops.%', v_relation;
    exception when others then
      if sqlerrm not like '%j1_minimum_rows_are_append_only%' then raise; end if;
    end;
    begin
      execute format('delete from ops.%I where true', v_relation);
      raise exception 'NEGATIVE FAILED: delete was accepted on ops.%', v_relation;
    exception when others then
      if sqlerrm not like '%j1_minimum_rows_are_append_only%' then raise; end if;
    end;
    -- TRUNCATE IS A STATEMENT EVENT AND A ROW-LEVEL TRIGGER NEVER SEES IT. The
    -- claim "truncate is refused" was carried by `revoke` alone, which does not
    -- bind the table owner; the second, statement-level trigger is what makes it
    -- true, and this is the only thing that can show it.
    begin
      execute format('truncate ops.%I cascade', v_relation);
      raise exception 'NEGATIVE FAILED: truncate was accepted on ops.%', v_relation;
    exception when others then
      if sqlerrm not like '%j1_minimum_rows_are_append_only%' then raise; end if;
    end;
  end loop;
  end if;
  -- THE STRUCTURAL HALF IS NOT GATED ON THE ROLE. Reading pg_trigger needs no
  -- privilege on the relation, so whether the statement-level trigger EXISTS is
  -- asserted for every run, including one that had to skip the attempts above.
  foreach v_relation in array array['j1_minimum_inventory', 'j1_minimum_admission'] loop
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

  -- --- GRANTS ----------------------------------------------------------------
  -- DIRECT INSERT IS GRANTED TO NOBODY, so a writer holding a raw connection
  -- cannot attribute an admission to someone else or date it itself.
  foreach v_role in array array['carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority'] loop
    if not exists (select 1 from pg_roles where rolname = v_role) then continue; end if;
    foreach v_relation in array array['ops.j1_minimum_inventory', 'ops.j1_minimum_admission'] loop
      -- TRUNCATE IS ASSERTED HERE TOO, because it is now a first-class claim of
      -- this rail with its own trigger. The revoke is the half that binds a
      -- runtime bundle; the trigger above is the half that binds the owner.
      if has_table_privilege(v_role, v_relation, 'INSERT')
         or has_table_privilege(v_role, v_relation, 'UPDATE')
         or has_table_privilege(v_role, v_relation, 'DELETE')
         or has_table_privilege(v_role, v_relation, 'TRUNCATE') then
        raise exception 'GRANTS: % holds a direct write on %', v_role, v_relation;
      end if;
    end loop;
  end loop;
  foreach v_role in array array['carr_reader', 'carr_jobs'] loop
    if not exists (select 1 from pg_roles where rolname = v_role) then continue; end if;
    if has_function_privilege(v_role,
         'ops.j1_minimum_append_admission(text,text,uuid,text,text,text,jsonb,jsonb)', 'EXECUTE')
       or has_function_privilege(v_role,
         'ops.j1_minimum_open_inventory(jsonb,bigint,text)', 'EXECUTE') then
      raise exception 'GRANTS: % can write on this rail', v_role;
    end if;
  end loop;
  foreach v_role in array array['carr_writer', 'carr_authority'] loop
    if not exists (select 1 from pg_roles where rolname = v_role) then continue; end if;
    if not has_function_privilege(v_role,
         'ops.j1_minimum_append_admission(text,text,uuid,text,text,text,jsonb,jsonb)', 'EXECUTE') then
      raise exception 'GRANTS: % cannot reach the append function', v_role;
    end if;
  end loop;

  raise notice 'ADMITTED-MINIMUM INPUT STORE PROOF PASSED for actor %, scope %', v_actor, v_scope_key;
end;
$proof$;

-- EVERY ROW ROLLS BACK. Nothing above is durable, no receipt was admitted to any
-- gate, no benchmark was accepted and no clock was started.
rollback;
