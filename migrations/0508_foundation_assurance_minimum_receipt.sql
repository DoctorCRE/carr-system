-- DoctorCRE v5 slice V5-A00: durable benchmark-manifest persistence and the
-- exact-hash human acceptance rail (requirement Q012, decisions Q008.D1,
-- Q128.D1, Q143.D1; gate benchmark-contract-accepted).
--
-- Migration 0508. tools/migrate.py owns the transaction and records the file
-- digest only after every statement succeeds. Production application remains a
-- separate Joe-gated act under accepted WR-000095 plan v7.
--
-- WHAT THIS IS. benchmark-minimum.v5.js can compute the one payload digest a
-- verified partner would have to accept, and can authenticate an acceptance
-- envelope that already exists. It cannot store a manifest and it cannot accept
-- one. This file is the missing durable half: the benchmark SUBJECT as typed
-- rows, an independent review bound to exact bytes, and a verified-partner
-- acceptance receipt whose acceptor is derived from the authenticated session.
--
-- IT IS NOT A SECOND PROJECTION LAYER. The payload digest is RECOMPUTED from
-- the persisted rows on every read, review and acceptance. A caller may supply
-- a hash; it is only ever compared against the one the rows produce. The
-- manifest is therefore stored as typed rows rather than as one jsonb blob whose
-- meaning nothing in the database can check. TEN relations carry the whole rail:
-- the draft, its six ordered content tables (dimensions, workloads, request-size
-- points, concurrency levels, browsers and evaluator seats), the independent
-- review, the measurement coverage attestation recorded beside a passing review,
-- and the acceptance receipt.
--
-- THREE THINGS ARE DELIBERATELY NOT STORED, because storing them would move an
-- authority this slice does not hold:
--
--   1. The FIXED CONSTANTS. slo_thresholds, cost_variance_thresholds and
--      deadline_contract are identity, not configuration (r7 declares each
--      field `const`). They are EMITTED by the immutable functions below and
--      hashed into every preimage, so no caller-chosen threshold can ever
--      reach a digest. A manifest that disagrees with them is refused in
--      benchmark-minimum.v5.js before it reaches this file, and could not be
--      expressed here in any case.
--   2. The MEASUREMENT SET. r7's pass_rule requires every required matrix cell
--      to be exercised, and the required matrix is combinatorial (the kernel
--      caps it at 200000 cells). The samples are far too large to live here, so
--      a passing review NAMES the exact `sha256:` digest of the set its writer
--      read; the samples themselves stay outside this record layer.
--      READ THAT NARROWLY. The digest NAMES bytes. It does not prove anything
--      about them, and nothing in this database evaluates coverage. Who proved
--      what is a property of the caller, and there are two kinds:
--        * a review written through the MCP verb review-benchmark-manifest-draft
--          has had its coverage proved by the kernel's
--          evaluateBenchmarkWorkloadCoverage against the payload REBUILT FROM
--          THESE ROWS, and the digest was computed there rather than supplied;
--        * a review written by any other holder of the writer bundle calling
--          ops.benchmark_review_manifest_draft directly carries a digest this
--          database took on that writer's word.
--      Both are TRUSTED writers -- direct INSERT is granted to nobody and the
--      write functions reach only the writer and authority bundles -- and that
--      trusted-writer authority is deliberately preserved rather than replaced
--      by a second authority. The RECORD that tells the two apart is now written:
--      ops.benchmark_measurement_coverage_attestation, recorded in the same
--      definer call that records the passing review, names the kernel evaluator,
--      the payload digest it proved against and the measurement digest it proved
--      over. Read that narrowly too -- see THE REMAINING TRUST BOUNDARY below.
--   3. A REVIEW TTL. r7 attaches no currentness window to benchmark-manifest.v1
--      at all -- no maximum review age, and no relation to the consumer-gate
--      receipt TTLs, which govern a different set of receipts. Inventing one
--      here would be a second currentness authority, so currentness is enforced
--      the one way r7 does settle: a review and an acceptance must each name the
--      digest the rows produce RIGHT NOW, and a passing review that no longer
--      names the live digest cannot be accepted.
--
-- NO ACTOR ARRIVES IN A PAYLOAD. Proposal and review derive the writer from
-- ops.portfolio_writer_actor_id(), the existing server-established writer
-- context (its name is historical; its contract is "the active actor the server
-- established for this transaction", and reusing it is what keeps one writer
-- derivation rather than two). Acceptance derives the partner from
-- ops.authority_actor_slug(), which reads session_user. Direct INSERT is
-- granted to nobody, so neither derivation can be stepped around with raw SQL.
-- No column named accepted_by_identity, verified_human or partner_confirmed
-- exists anywhere below: a boolean a caller can set is not a verification.
--
-- WHAT AN ACCEPTANCE MUST BIND, AND WHAT IS STILL UNBOUND HERE.
-- r7's receipt_producer_step_registry makes
-- step:benchmark-contract-human-exact-hash-acceptance-receipt depend on exactly
-- two steps: step:portfolio-constitution-human-exact-hash-acceptance-receipt
-- and step:gate-zero-read-only-outcome. Acceptance additionally rests on the
-- passing review's measurement evidence, which is where the third gap sits.
--
--   PORTFOLIO -- BOUND, TO EXACTLY WHAT IT PROVES AND NO MORE. The existing rail
--     from migration 0496 answers it. ops.portfolio_accepted_revision() returns
--     the current accepted revision, raises on an integrity failure, and returns
--     null when nothing is accepted. Acceptance binds that revision id and its
--     recomputed accepted digest, and refuses when there is no accepted
--     portfolio. No accepted portfolio is invented here and none is created by
--     this file.
--     STATED EXACTLY, because the previous wording overclaimed: portfolio_ref is
--     a reference the ACCEPTOR names. This binding proves that the named
--     portfolio constitution is accepted, is intact at this instant, and was
--     accepted strictly before this acceptance. It does NOT prove that this
--     benchmark manifest descends from that portfolio. Nothing in this database
--     records a benchmark-to-portfolio lineage edge, so none is asserted, and
--     none is invented: r7 makes the portfolio ACCEPTANCE STEP a prerequisite,
--     not a lineage relation, so adding a lineage rule here would be a policy
--     this slice does not hold. The claim is simply not made.
--   GATE ZERO -- BOUND AS OF MIGRATION 0502, AND THE OLD READING IS RETIRED BY
--     THE PACKET RATHER THAN BY PREFERENCE. This block used to say r7 registers
--     no v5 producer for step:gate-zero-read-only-outcome and that the outcome
--     is produced outside this system. The card-10 amendment (decision
--     311a9af5-3685-4c47-a158-f8dd70870ca1) added the producer row, and
--     tools/doctorcre-v5-review.cjs's own comment now says the external boundary
--     moved down to the four predecessors. Gate Zero is a v5 producer with the
--     role independent_control_plane_oracle, and the seat that holds it -- the
--     independent Codex reviewer lane -- records the outcome itself under Joe's
--     2026-09-13 ruling d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70, with no partner
--     countersign. ops.gate_zero_read_only_outcome holds those rows.
--     ops.benchmark_gate_zero_outcome() below is STILL a PRIVATE reader granted
--     to no role and reachable only from the definer functions here, and it
--     STILL raises when no current passing outcome exists -- so an acceptance
--     attempted before one is recorded refuses exactly as it always did. What
--     changed is that the refusal is now about the rows, not about the absence
--     of a place to put them. A caller-selected work-request reference, a
--     synthetic fixture digest and a Gate Zero policy invented in this file are
--     all still refused.
--   MEASUREMENT COVERAGE -- BOUND, TO EXACTLY WHAT IT PROVES AND NO MORE. A
--     review row carries the digest of the measurement set its writer read (see
--     2 above). Beside it, ops.benchmark_measurement_coverage_attestation now
--     records WHAT PROVED IT: the kernel evaluator by name from a closed set,
--     the payload digest that evaluator returned, the measurement digest it
--     proved over, and the digest of the evaluation result so the judgement is
--     replayable. It is written in the SAME definer call that writes the review,
--     and a passing review cannot be written without it, so the pass and its
--     attestation cannot come apart.
--     ops.benchmark_measurement_coverage_binding(uuid) reads that record and is
--     still PRIVATE, still fail-closed, and still deliberately INDEPENDENT of
--     Gate Zero. It RECOMPUTES the payload digest from the draft's own rows with
--     ops.benchmark_payload_digest() and refuses an attestation naming a
--     different one, refuses an attestation whose measurement digest is not the
--     review row's, and refuses an evaluator name outside the closed set. Two of
--     the three attested values are therefore checkable here rather than merely
--     stored.
--
-- THE REMAINING TRUST BOUNDARY, STATED EXACTLY, because the attestation moves it
-- less far than it may look. The samples live outside this record layer and this
-- database cannot read them. The attestation records that the MCP path ran
-- evaluateBenchmarkWorkloadCoverage over the payload rebuilt from these rows and
-- over bytes that hash to that digest -- and the database is still believing a
-- TRUSTED WRITER about an evaluation it did not perform and cannot repeat. What
-- the attestation buys is that the assertion is explicit, attributed and
-- auditable instead of implicit and anonymous, and that an unattested pass is
-- unwritable. This database does not become an independent verifier of benchmark
-- coverage, and nothing below should be read as saying that it does.
--
-- AND ACCEPTANCE STILL REFUSES UNTIL BOTH ANSWER. The attestation cleared one
-- binding on its own evidence; migration 0502 cleared the other by landing the
-- Gate Zero outcome record and implementing its reader.
-- ops.benchmark_gate_zero_outcome() is still read FIRST and still raises when no
-- current passing outcome exists, so no acceptance receipt can exist until an
-- independent oracle seat has recorded one.
--
-- Acceptance is strictly after both r7 prerequisites and the review it rests on:
-- an acceptance recorded AT the Gate Zero instant, at the portfolio acceptance
-- instant, or at the review instant is refused exactly as one recorded before it
-- is. That exclusive reading is the same one benchmark-minimum.v5.js applies to
-- member observation, so the two paths cannot differ on the boundary.

-- ---------------------------------------------------------------------------
-- Shared derivations, reused rather than restated.
-- ---------------------------------------------------------------------------

-- CANONICALIZATION IS NOT REDECIDED HERE. ops.portfolio_canonical_json() from
-- migration 0496 already matches artifact-trust.js's canonicalJson exactly,
-- including JavaScript number rendering, and every function below calls it. A
-- benchmark-local copy would be a second canonicalization authority, and two
-- canonicalizers that agree today are two that can disagree after one edit.
--
-- One property that copy DOES have to hold, stated so a reviewer can check it:
-- artifact-trust.js sorts object keys by UTF-16 code unit and
-- ops.portfolio_canonical_json sorts by `collate "C"` (UTF-8 byte order). Those
-- two orders coincide for every key in benchmark-manifest.v1, all of which are
-- ASCII lower-case and underscore. A future non-ASCII key would have to
-- reconcile them; there is none, and adding one is a deliberate edit.
--
-- THE SAME DIVERGENCE HAS A SECOND FACE, IN VALUES RATHER THAN KEYS, and it is
-- the one that can actually admit a row the kernel would refuse. The kernel
-- bounds free text with JavaScript's String#length, which counts UTF-16 CODE
-- UNITS; SQL's char_length counts CODEPOINTS. They differ by exactly one per
-- codepoint above U+FFFF, which JavaScript stores as a surrogate pair -- so a
-- 300-codepoint emoji outlier_rule is 600 units to the kernel, which refuses it,
-- and 300 characters to char_length, which would not. Wherever a bound exists on
-- both sides the guards below apply the UTF-16 count through
-- ops.benchmark_utf16_length(), so nothing can be stored here that fails kernel
-- validation on the way back out. Where r7 declares no maximum at all -- workload
-- ids, browser triples, dimension values -- neither side invents one, so the two
-- cannot disagree there.

-- r7 canonicalization_contract, receipt_payload_digest_rule: the domain tag for
-- benchmark-manifest.v1.
create or replace function ops.benchmark_payload_domain_tag()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'doctorcre:benchmark-payload:v1'::text $$;

comment on function ops.benchmark_payload_domain_tag() is
  'The r7 domain tag for the benchmark-manifest.v1 payload digest.';

-- The length JavaScript would report for the same string. See the divergence
-- note above: char_length counts codepoints, String#length counts UTF-16 code
-- units, and every codepoint above U+FFFF costs two of the latter. This is a
-- PARITY function, not a policy: it decides no bound, it only measures the way
-- the kernel measures so that a bound stated once means the same thing twice.
create or replace function ops.benchmark_utf16_length(p_text text)
returns integer language sql immutable strict
set search_path = pg_catalog
as $$
  select char_length(p_text) + coalesce((
    select count(*)::integer
      from unnest(regexp_split_to_array(p_text, '')) as ch
     where ascii(ch) > 65535), 0)
$$;

comment on function ops.benchmark_utf16_length(text) is
  'The length JavaScript String#length would report for this text: codepoints plus one per codepoint above U+FFFF. Used so a bound the kernel states in UTF-16 code units means the same thing in SQL.';

-- NULL IS NOT A MATCH, AND A MISSING DERIVATION IS NOT A PASS.
--
-- Every acceptance binding is checked through here for one reason: `a <> b` is
-- NULL when either side is null, `if NULL then ... end if` does not fire, and a
-- comparison written that way FAILS OPEN exactly when the thing being bound was
-- never derived. This raises when the derived side is null (the reader produced
-- no binding, which is not permission to proceed), raises when the supplied side
-- is null, and otherwise compares with IS DISTINCT FROM so no null can make the
-- comparison itself disappear.
-- Left VOLATILE deliberately. Its whole job is to RAISE; a purity marker invites
-- the planner to fold or inline it away, and a check that can be optimized out
-- is not a check.
create or replace function ops.benchmark_assert_bound(
  p_binding text, p_field text, p_supplied anyelement, p_derived anyelement)
returns void language plpgsql
set search_path = pg_catalog
as $$
begin
  if p_derived is null then
    raise exception 'benchmark acceptance cannot bind the %: its % was not derived, and an underived binding is a refusal rather than a match',
      p_binding, p_field;
  end if;
  if p_supplied is null then
    raise exception 'benchmark acceptance names no % for the %', p_field, p_binding;
  end if;
  if p_supplied is distinct from p_derived then
    raise exception 'benchmark acceptance does not bind the current %: its % differs from the derived one',
      p_binding, p_field;
  end if;
end;
$$;

comment on function ops.benchmark_assert_bound(text,text,anyelement,anyelement) is
  'Fail-closed equality for one acceptance binding field: refuses a null derived value, refuses a null supplied value, and otherwise compares with IS DISTINCT FROM so a null can never make the comparison itself evaluate to NULL and fall through.';

-- The three fixed constant groups, verbatim from r7. They are emitted into
-- every preimage rather than stored, so a caller-chosen threshold, cost band or
-- clock contract can never be hashed as if a partner had chosen it.
create or replace function ops.benchmark_slo_thresholds()
returns jsonb language sql immutable
set search_path = pg_catalog
as $$
  select jsonb_build_object(
    'warm_core_navigation_p95_ms', 2000,
    'cold_lcp_p95_ms', 4000,
    'command_acknowledgement_p95_ms', 300)
$$;

create or replace function ops.benchmark_cost_variance_thresholds()
returns jsonb language sql immutable
set search_path = pg_catalog
as $$
  select jsonb_build_object(
    'warn_basis_points', 15000,
    'mandatory_replan_basis_points', 20000)
$$;

create or replace function ops.benchmark_deadline_contract()
returns jsonb language sql immutable
set search_path = pg_catalog
as $$
  select jsonb_build_object(
    'timezone', 'America/Chicago',
    'calendar_days', 30,
    'clock_origin_gate_id', 'foundation-assurance-minimum-accepted',
    'clock_origin_rule', 'observed_at of the first current passing foundation-assurance-minimum receipt that makes Journey 1 admissible',
    'maximum_external_blocker_pause_days', 5,
    'reset_policy', 'never_reset_or_rebase_elapsed_history',
    'amendment_policy', 'verified_partner_exact_hash_amendment_preserves_original_origin_and_elapsed_history',
    'clock_terminus_gate_id', 'journey-one-kernel-production-accepted',
    'kernel_obligation_decision_ids', jsonb_build_array('Q002.D1', 'Q014.D1', 'Q123.D1'),
    'miss_consequence', 'mark_deadline_missed_require_replan_preserve_origin_and_elapsed_continue_safe_construction_without_claiming_deadline_success')
$$;

-- THE CLOSED SET OF COVERAGE EVALUATORS. One member, and it is a NAME rather
-- than an implementation: this database evaluates no coverage and this function
-- is not a second coverage authority. It exists so that "which evaluator proved
-- this" is a constrained value the reader can check rather than free text a
-- writer can invent, in exactly the way gate_id and producer_role are constrained
-- on the draft. Adding a member is a deliberate edit to a closed set, which is
-- the point; benchmark-minimum.v5.js's BENCHMARK_COVERAGE_EVALUATORS carries the
-- same list on the other side.
create or replace function ops.benchmark_coverage_evaluators()
returns text[] language sql immutable
set search_path = pg_catalog
as $$ select array['benchmark-minimum.v5.js#evaluateBenchmarkWorkloadCoverage']::text[] $$;

comment on function ops.benchmark_coverage_evaluators() is
  'The closed set of names a measurement coverage attestation may cite as the evaluator that proved coverage. A name, not an implementation: nothing in this database evaluates coverage.';

comment on function ops.benchmark_slo_thresholds() is
  'The three fixed r7 SLO thresholds. Identity, not configuration: emitted into every payload preimage and never stored per draft.';
comment on function ops.benchmark_cost_variance_thresholds() is
  'The two fixed r7 cost variance thresholds in basis points. Emitted into every payload preimage and never stored per draft.';
comment on function ops.benchmark_deadline_contract() is
  'The fixed r7 benchmark deadline contract, pause budget in DAYS. Emitted into every payload preimage and never stored per draft.';

-- ---------------------------------------------------------------------------
-- The draft: one immutable benchmark manifest payload, stored as typed rows.
-- ---------------------------------------------------------------------------
create table if not exists ops.benchmark_manifest_draft (
  id                      uuid primary key default gen_random_uuid(),
  benchmark_ref           text not null check (benchmark_ref ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$'),
  draft_version           integer not null check (draft_version > 0),
  idempotency_key         uuid not null unique,
  schema_version          text not null check (schema_version = 'benchmark-manifest.v1'),
  payload_domain_tag      text not null check (payload_domain_tag = 'doctorcre:benchmark-payload:v1'),
  gate_id                 text not null check (gate_id = 'benchmark-contract-accepted'),
  producer_step_ref       text not null
                            check (producer_step_ref = 'step:benchmark-contract-human-exact-hash-acceptance-receipt'),
  producer_role           text not null check (producer_role = 'verified_partner_benchmark_authority'),
  combiner                text not null check (combiner = 'exact_verified_partner_hash_acceptance'),
  subject_digest          text not null check (subject_digest ~ '^sha256:[0-9a-f]{64}$'),
  candidate_digest        text not null check (candidate_digest ~ '^sha256:[0-9a-f]{64}$'),
  policy_digest           text not null check (policy_digest ~ '^sha256:[0-9a-f]{64}$'),
  cost_expectation_matrix_digest text not null
                            check (cost_expectation_matrix_digest ~ '^sha256:[0-9a-f]{64}$'),
  samples_per_cell        integer not null check (samples_per_cell >= 20),
  warmup_runs             integer not null check (warmup_runs >= 1),
  p95_aggregation_method  text not null
                            check (p95_aggregation_method = 'nearest-rank-per-required-cell-all-cells-must-pass'),
  outlier_rule            text not null
                            check (char_length(outlier_rule) between 5 and 300),
  payload_digest          text not null check (payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  proposed_by_actor_id    uuid not null references public.actor(id),
  created_at              timestamptz not null default now(),
  unique (benchmark_ref, draft_version),
  unique (benchmark_ref, payload_digest)
);

comment on table ops.benchmark_manifest_draft is
  'One inert DoctorCRE v5 benchmark manifest draft. payload_digest is the r7 exact hash a verified partner would have to accept, and is recomputed from this draft''s own rows at every read, review and acceptance. A draft creates no job, envelope, capability, schedule, deployment or clock, and starts no J1 clock.';

-- The ten ordered, unique string dimensions r7 declares as arrays. One relation
-- rather than ten: they share a shape (an ordered set of non-empty unique
-- strings) and a single closed dimension enum is easier to review than ten
-- near-identical tables.
create table if not exists ops.benchmark_manifest_dimension (
  id                      uuid primary key default gen_random_uuid(),
  draft_id                uuid not null references ops.benchmark_manifest_draft(id),
  dimension               text not null check (dimension in (
                            'capacity_profiles', 'arrival_patterns', 'routes', 'runtime_versions',
                            'device_profiles', 'hardware_profiles', 'network_profiles',
                            'acknowledgement_endpoints', 'comparator_versions', 'cache_states')),
  ordinal                 integer not null check (ordinal >= 0),
  value                   text not null check (char_length(value) >= 1),
  created_at              timestamptz not null default now(),
  -- cache_states is a closed two-value enum in r7; a third state is not a new
  -- profile, it is a different contract.
  constraint benchmark_dimension_cache_state_closed
    check (dimension <> 'cache_states' or value in ('cold', 'warm')),
  unique (draft_id, dimension, ordinal),
  unique (draft_id, dimension, value)
);

comment on table ops.benchmark_manifest_dimension is
  'The ten ordered unique string dimensions of one benchmark manifest draft. Order is part of the hash: a reorder is a different manifest.';

create table if not exists ops.benchmark_manifest_workload (
  id                      uuid primary key default gen_random_uuid(),
  draft_id                uuid not null references ops.benchmark_manifest_draft(id),
  ordinal                 integer not null check (ordinal >= 0),
  workload_id             text not null check (char_length(workload_id) >= 1),
  weight_basis_points     integer not null check (weight_basis_points between 1 and 10000),
  operation_mix_digest    text not null check (operation_mix_digest ~ '^sha256:[0-9a-f]{64}$'),
  created_at              timestamptz not null default now(),
  unique (draft_id, ordinal),
  unique (draft_id, workload_id)
);

comment on table ops.benchmark_manifest_workload is
  'The workload mix of one benchmark manifest draft. Weights are basis points and must total exactly 10000 before the draft may commit.';

create table if not exists ops.benchmark_manifest_request_size (
  id                      uuid primary key default gen_random_uuid(),
  draft_id                uuid not null references ops.benchmark_manifest_draft(id),
  ordinal                 integer not null check (ordinal >= 0),
  percentile              integer not null check (percentile between 1 and 100),
  -- bigint is wider than the kernel's domain. validateBenchmarkPayload admits
  -- bytes only as a SAFE integer, so 9007199254740991 (2^53 - 1) is the largest
  -- value both sides can hold: above it a JavaScript number stops being exact and
  -- a rebuilt payload would round to a different manifest than the stored rows.
  bytes                   bigint not null check (bytes >= 0 and bytes <= 9007199254740991),
  created_at              timestamptz not null default now(),
  unique (draft_id, ordinal),
  -- r7 declares no uniqueItems here. The uniqueness is DERIVED, for the reason
  -- benchmark-minimum.v5.js states in full: this list is a distribution within a
  -- cell, and two entries for one percentile make "how many bytes at p50"
  -- unanswerable rather than merely redundant. The two sides agree deliberately.
  unique (draft_id, percentile)
);

comment on table ops.benchmark_manifest_request_size is
  'The request-size distribution of one benchmark manifest draft: a mapping from percentile to bytes within a cell. The per-percentile uniqueness is derived, matching benchmark-minimum.v5.js.';

create table if not exists ops.benchmark_manifest_concurrency (
  id                      uuid primary key default gen_random_uuid(),
  draft_id                uuid not null references ops.benchmark_manifest_draft(id),
  ordinal                 integer not null check (ordinal >= 0),
  concurrency_level       integer not null check (concurrency_level >= 1),
  created_at              timestamptz not null default now(),
  unique (draft_id, ordinal),
  unique (draft_id, concurrency_level)
);

comment on table ops.benchmark_manifest_concurrency is
  'The concurrency levels of one benchmark manifest draft; an ordered set of distinct positive integers.';

create table if not exists ops.benchmark_manifest_browser (
  id                      uuid primary key default gen_random_uuid(),
  draft_id                uuid not null references ops.benchmark_manifest_draft(id),
  ordinal                 integer not null check (ordinal >= 0),
  name                    text not null check (char_length(name) >= 1),
  version                 text not null check (char_length(version) >= 1),
  build                   text not null check (char_length(build) >= 1),
  created_at              timestamptz not null default now(),
  unique (draft_id, ordinal),
  -- Derived, as above: a browser triple indexes a matrix cell, so a repeat makes
  -- "which cell is this" unanswerable.
  unique (draft_id, name, version, build)
);

comment on table ops.benchmark_manifest_browser is
  'The browser triples of one benchmark manifest draft. Each triple indexes a matrix cell, so a repeated triple is refused.';

create table if not exists ops.benchmark_manifest_evaluator (
  id                      uuid primary key default gen_random_uuid(),
  draft_id                uuid not null references ops.benchmark_manifest_draft(id),
  ordinal                 integer not null check (ordinal >= 0),
  -- authenticated-receipt-identity.v1. These are the evaluator seats the
  -- manifest DECLARES; they are content of the hashed payload, not an
  -- authorization. Nothing in this file reads authority_class back out of a
  -- stored row to decide anything: the live classification is derived by
  -- identity.js at the moment of an act, exactly as global-boundaries.v5.js
  -- does, and a projection that copied a stored class string would turn the
  -- partner test into the caller boolean this rail exists to prevent.
  actor_id                text not null check (char_length(actor_id) >= 1),
  session_ref             text not null check (session_ref ~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$'),
  authority_class         text not null check (char_length(authority_class) >= 1),
  created_at              timestamptz not null default now(),
  unique (draft_id, ordinal),
  -- Derived: a repeated evaluator seat is one evaluator counted twice, which
  -- inflates apparent independent review.
  unique (draft_id, actor_id, session_ref)
);

comment on table ops.benchmark_manifest_evaluator is
  'The declared evaluator seats of one benchmark manifest draft. Hashed payload content only: no row here grants authority, and authority_class is never read back out to decide anything.';

-- ---------------------------------------------------------------------------
-- Review and acceptance.
-- ---------------------------------------------------------------------------
create table if not exists ops.benchmark_manifest_review (
  id                      uuid primary key default gen_random_uuid(),
  draft_id                uuid not null references ops.benchmark_manifest_draft(id),
  idempotency_key         uuid not null unique,
  reviewed_payload_digest text not null check (reviewed_payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  verdict                 text not null check (verdict in ('pass', 'fail')),
  -- The measurement evidence the REVIEW'S WRITER says it read. r7's pass_rule
  -- requires every required matrix cell to be exercised and to meet its fixed
  -- SLO; the samples are far too large to store here, so a PASS names their
  -- exact digest and a FAIL may leave it null.
  --
  -- WHAT THIS COLUMN IS NOT. It is not proof that the coverage rule was checked,
  -- and this database never checks it. When the row is written through the MCP
  -- verb review-benchmark-manifest-draft, benchmark-minimum.v5.js's
  -- evaluateBenchmarkWorkloadCoverage has already proved coverage against the
  -- payload rebuilt from these rows and the digest was computed there rather
  -- than supplied. When ops.benchmark_review_manifest_draft is called directly by
  -- a holder of the writer bundle, this column is that trusted writer's
  -- assertion and nothing more. Both writers are trusted -- no role holds direct
  -- INSERT -- and that authority is preserved deliberately. WHAT TELLS THE TWO
  -- APART IS RECORDED: a passing review carries a row in
  -- ops.benchmark_measurement_coverage_attestation written in the same definer
  -- call, naming the kernel evaluator that proved coverage, the payload digest
  -- that evaluator returned and the measurement digest it proved over. A passing
  -- review with no attestation cannot be written at all.
  -- ops.benchmark_measurement_coverage_binding(uuid) reads THAT record -- it
  -- recomputes the payload digest from the draft's own rows and compares the
  -- attested measurement digest against this column -- rather than reading this
  -- column as evidence. The attestation makes the writer's assertion attributed
  -- and checkable; it does not make it a verification, and this column on its own
  -- still proves nothing.
  measurement_set_digest  text check (measurement_set_digest ~ '^sha256:[0-9a-f]{64}$'),
  review_summary          text not null check (btrim(review_summary) <> '' and char_length(review_summary) <= 1000),
  reviewer_actor_id       uuid not null references public.actor(id),
  created_at              timestamptz not null default now(),
  constraint benchmark_review_pass_binds_measurements
    check (verdict <> 'pass' or measurement_set_digest is not null)
);

comment on table ops.benchmark_manifest_review is
  'Append-only independent review of one exact benchmark payload digest. A review naming a digest the draft no longer produces is refused at write time, so a passing review can never be carried onto different bytes. A passing review must additionally NAME the exact measurement set its writer read AND carry a coverage attestation in ops.benchmark_measurement_coverage_attestation recorded in the same call: naming is still not proving, this database still evaluates no coverage, and the attestation is what makes the assertion attributed and checkable rather than anonymous.';

-- ---------------------------------------------------------------------------
-- THE MEASUREMENT COVERAGE ATTESTATION.
--
-- WHAT THIS ROW IS. A statement, by the trusted writer that recorded a passing
-- review, of WHICH evaluator proved coverage, WHICH payload digest it proved
-- against, WHICH measurement digest it proved over, and WHAT the evaluation
-- returned. All four values are computed on the write path before the review is
-- written: the payload digest and the evaluation are the kernel evaluator's own
-- RETURN VALUE, not a recomputation, and not a caller argument.
--
-- WHAT THIS ROW IS NOT, and the distinction is the whole reason the wording of
-- the reader below is so careful. It is not a verification. The samples are not
-- here and cannot be read here, so the database cannot repeat the evaluation and
-- does not claim to. What changes is that a passing review's measurement digest
-- stops being anonymous: it now carries a named evaluator, an attributed writer,
-- a payload digest this database CAN recompute, and an evaluation digest that
-- makes the judgement replayable by anyone who holds the samples.
--
-- ONE ATTESTATION PER REVIEW, enforced by the unique constraint rather than by
-- convention: two attestations for one review would be two answers to a question
-- that has one.
create table if not exists ops.benchmark_measurement_coverage_attestation (
  id                      uuid primary key default gen_random_uuid(),
  review_id               uuid not null unique references ops.benchmark_manifest_review(id),
  -- Carried so the reader can recompute the payload digest without a second
  -- join, and so a reader that reaches this row directly still knows which
  -- draft it is about.
  draft_id                uuid not null references ops.benchmark_manifest_draft(id),
  -- Constrained to the closed set literally here, the way gate_id and
  -- producer_role are constrained on the draft, AND checked against
  -- ops.benchmark_coverage_evaluators() on both the write and the read path. A
  -- column check cannot be stepped around by a future write path; a function
  -- check cannot be stepped around by a future column edit.
  coverage_proved_by      text not null
                            check (coverage_proved_by = 'benchmark-minimum.v5.js#evaluateBenchmarkWorkloadCoverage'),
  -- The digest the evaluator RETURNED, not one recomputed beside it. The reader
  -- recomputes ops.benchmark_payload_digest(draft_id) and refuses a mismatch, so
  -- an attestation naming a payload the draft no longer produces is refused.
  benchmark_payload_digest text not null check (benchmark_payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- The same bytes the review row names. Stored again rather than joined so the
  -- reader can compare the two and refuse a divergence instead of assuming one
  -- cannot happen.
  measurement_set_digest  text not null check (measurement_set_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- digest(coverage) over the evaluator's frozen return value. It makes the
  -- judgement replayable: a holder of the samples can re-run the named evaluator
  -- and compare. It is not read as evidence by anything here.
  evaluation_digest       text not null check (evaluation_digest ~ '^sha256:[0-9a-f]{64}$'),
  attested_by_actor_id    uuid not null references public.actor(id),
  created_at              timestamptz not null default now()
);

comment on table ops.benchmark_measurement_coverage_attestation is
  'The trusted writer''s explicit, attributed statement that benchmark-minimum.v5.js proved coverage for one passing review: the evaluator by name from a closed set, the payload digest that evaluator returned, the measurement digest it proved over, and the digest of its result. Written in the same definer call as the review it attests, so an unattested pass cannot be written. It is not a verification: the samples stay outside this record layer and the database cannot repeat the evaluation.';

create table if not exists ops.benchmark_manifest_acceptance_receipt (
  id                      uuid primary key default gen_random_uuid(),
  draft_id                uuid not null unique references ops.benchmark_manifest_draft(id),
  benchmark_ref           text not null,
  idempotency_key         uuid not null unique,
  gate_id                 text not null check (gate_id = 'benchmark-contract-accepted'),
  producer_step_ref       text not null
                            check (producer_step_ref = 'step:benchmark-contract-human-exact-hash-acceptance-receipt'),
  -- r7's benchmark-manifest.v1 status enum has exactly one member.
  status                  text not null check (status = 'accepted'),
  accepted_payload_digest text not null check (accepted_payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  review_id               uuid not null unique references ops.benchmark_manifest_review(id),
  -- PREREQUISITE ONE: the accepted portfolio constitution, bound to the exact
  -- revision and the digest recomputed from its rows.
  --
  -- WHAT THESE FOUR COLUMNS PROVE, EXACTLY: that the portfolio the acceptor
  -- NAMED was accepted, was intact when this receipt was written, and was
  -- accepted strictly before it. They do not prove that this benchmark descends
  -- from that portfolio -- no lineage edge from a benchmark draft to a portfolio
  -- revision exists anywhere in this database, so none is recorded here and none
  -- is invented. r7 requires the portfolio acceptance STEP, not a lineage
  -- relation.
  portfolio_ref           text not null,
  portfolio_revision_id   uuid not null references ops.portfolio_revision(id),
  portfolio_accepted_digest text not null check (portfolio_accepted_digest ~ '^sha256:[0-9a-f]{64}$'),
  portfolio_accepted_at   timestamptz not null,
  -- PREREQUISITE TWO: the Gate Zero read-only outcome, as authenticated HERE,
  -- AND IT IS NOW BOUND. Both columns are filled only from
  -- ops.benchmark_gate_zero_outcome(), which since migration 0502 reads the
  -- current passing, unexpired row of ops.gate_zero_read_only_outcome and still
  -- raises when there is none. So the receipt binds the exact outcome an
  -- independent oracle seat recorded, and an acceptance attempted before any
  -- outcome exists still fails closed exactly as it did while this was a stub.
  gate_zero_step_ref      text not null check (gate_zero_step_ref = 'step:gate-zero-read-only-outcome'),
  gate_zero_outcome_digest text not null check (gate_zero_outcome_digest ~ '^sha256:[0-9a-f]{64}$'),
  gate_zero_observed_at   timestamptz not null,
  accepted_by_actor_id    uuid not null references public.actor(id),
  accepted_at             timestamptz not null default now(),
  constraint benchmark_acceptance_after_gate_zero
    check (accepted_at > gate_zero_observed_at),
  constraint benchmark_acceptance_after_portfolio
    check (accepted_at > portfolio_accepted_at)
);

comment on table ops.benchmark_manifest_acceptance_receipt is
  'Private verified-partner receipt accepting one exact benchmark payload digest, strictly after both r7 prerequisites: the portfolio constitution the acceptor named (accepted and intact, not a lineage claim) and the Gate Zero read-only outcome as authenticated in this record layer. It grants no dispatch, activation or execution authority and starts no clock; it only makes that draft the accepted benchmark contract. Both bindings are implemented as of migration 0502: the coverage attestation and the Gate Zero outcome record. A row can exist only when both answer, and each still refuses on its own grounds.';

-- ---------------------------------------------------------------------------
-- Append-only, and the freeze that follows acceptance.
-- ---------------------------------------------------------------------------
-- TWO TRIGGERS PER RELATION, because UPDATE/DELETE and TRUNCATE are different
-- events. A ROW-LEVEL TRIGGER NEVER SEES TRUNCATE -- it is a statement event --
-- so a row-level-only posture leaves "a benchmark row cannot be erased" true of
-- every runtime bundle and FALSE of the table owner, from whom TRUNCATE cannot
-- be revoked. The `revoke ... truncate` in the grants section below is the grant
-- half and does not bind the owner; this is the half that does. Same shape as
-- ops/journey-one-clock-input-store.candidate.sql, ops/model-role-store.candidate.sql
-- and ops/cre-lifecycle.candidate.sql, which is the house pattern rather than a
-- new one invented here.
create or replace function ops.benchmark_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'DoctorCRE v5 benchmark rows are append-only: % is refused on ops.%',
    tg_op, tg_table_name using errcode = '42501';
end;
$$;

comment on function ops.benchmark_rows_immutable() is
  'Refuses every update, delete and truncate on the DoctorCRE v5 benchmark manifest tables. Installed twice per relation because a row-level trigger never sees TRUNCATE, and TRUNCATE cannot be revoked from the table owner.';

do $$
declare t text;
begin
  foreach t in array array[
    'benchmark_manifest_draft', 'benchmark_manifest_dimension', 'benchmark_manifest_workload',
    'benchmark_manifest_request_size', 'benchmark_manifest_concurrency',
    'benchmark_manifest_browser', 'benchmark_manifest_evaluator',
    'benchmark_manifest_review', 'benchmark_measurement_coverage_attestation',
    'benchmark_manifest_acceptance_receipt'
  ] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_append_only', t);
    execute format(
      'create trigger %I before update or delete on ops.%I for each row execute function ops.benchmark_rows_immutable()',
      t || '_append_only', t);
    -- TRUNCATE is statement-level and BEFORE-only; there is no row to see.
    execute format('drop trigger if exists %I on ops.%I', t || '_no_truncate', t);
    execute format(
      'create trigger %I before truncate on ops.%I for each statement execute function ops.benchmark_rows_immutable()',
      t || '_no_truncate', t);
  end loop;
end $$;

-- Append-only blocks rewriting an accepted draft. It does NOT block ADDING to
-- one, and a dimension row appended after acceptance would change what the
-- accepted hash covers while the receipt still read as valid. So once a draft is
-- accepted its content is closed to inserts too.
--
-- THE FREEZE AND THE ACCEPTANCE MUST SHARE A LOCK, OR NEITHER SEES THE OTHER.
-- Read-committed gives each of these a snapshot that excludes the other's
-- uncommitted row: a content insert running beside an acceptance sees no receipt
-- and is admitted, while the acceptance recomputes a digest over rows that do
-- not yet include it, and both commit. The result is a receipt naming a hash its
-- own draft no longer produces.
--
-- So both sides take a lock ON THE DRAFT ROW, and it only works because they
-- take the SAME one. A lock serializes nothing on its own -- it serializes
-- exactly the transactions that request a conflicting mode on the same row.
-- Here: this trigger takes FOR SHARE, ops.benchmark_accept_manifest_draft takes
-- FOR UPDATE, and those two modes conflict. The trigger is what makes the
-- protocol total rather than a convention: it fires on EVERY insert into all six
-- content tables regardless of which function or future code path issued it, so
-- a writer added later cannot join without taking the lock. Direct INSERT is
-- granted to no role, so there is no path that skips the trigger either. Nothing
-- outside those two modes on this row is claimed to be serialized.
create or replace function ops.benchmark_content_frozen_after_acceptance()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  -- The shared half of the protocol above. Taken BEFORE the receipt lookup, so
  -- an acceptance in flight on this draft must commit or abort first and this
  -- statement then reads a settled answer rather than a stale one.
  perform 1 from ops.benchmark_manifest_draft where id = new.draft_id for share;
  if exists (select 1 from ops.benchmark_manifest_acceptance_receipt
              where draft_id = new.draft_id) then
    raise exception 'benchmark draft % is accepted; its content is closed to further % rows',
      new.draft_id, tg_table_name;
  end if;
  return new;
end;
$$;

comment on function ops.benchmark_content_frozen_after_acceptance() is
  'Refuses a dimension, workload, request-size, concurrency, browser or evaluator insert against a draft that already carries an acceptance receipt, so no payload row can appear outside the hash the partner accepted. Takes FOR SHARE on the draft row first; ops.benchmark_accept_manifest_draft takes FOR UPDATE on the same row, and it is that shared protocol -- not the lock alone -- that keeps a concurrent content insert and an acceptance from committing past each other.';

do $$
declare t text;
begin
  foreach t in array array[
    'benchmark_manifest_dimension', 'benchmark_manifest_workload',
    'benchmark_manifest_request_size', 'benchmark_manifest_concurrency',
    'benchmark_manifest_browser', 'benchmark_manifest_evaluator'
  ] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_frozen_after_acceptance', t);
    execute format(
      'create trigger %I before insert on ops.%I for each row execute function ops.benchmark_content_frozen_after_acceptance()',
      t || '_frozen_after_acceptance', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- The canonical payload preimage, rebuilt FROM THE STORED ROWS.
--
-- This is what makes the digest a statement about the persisted manifest rather
-- than about a blob a caller once supplied. Ordering is declared: every list is
-- emitted in its stored ordinal order, because r7 array order participates in
-- the hash and a reorder is a different manifest.
-- ---------------------------------------------------------------------------
create or replace function ops.benchmark_dimension_array(p_draft_id uuid, p_dimension text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select coalesce(jsonb_agg(to_jsonb(d.value) order by d.ordinal), '[]'::jsonb)
    from ops.benchmark_manifest_dimension d
   where d.draft_id = p_draft_id and d.dimension = p_dimension
$$;

comment on function ops.benchmark_dimension_array(uuid, text) is
  'One stored benchmark dimension as a JSON array in its declared ordinal order.';

create or replace function ops.benchmark_payload_preimage(p_draft_id uuid)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_draft ops.benchmark_manifest_draft%rowtype;
  v_workloads jsonb; v_sizes jsonb; v_levels jsonb; v_browsers jsonb; v_evaluators jsonb;
begin
  select * into v_draft from ops.benchmark_manifest_draft where id = p_draft_id;
  if not found then
    raise exception 'benchmark draft % does not exist', p_draft_id;
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
           'workload_id', w.workload_id,
           'weight_basis_points', w.weight_basis_points,
           'operation_mix_digest', w.operation_mix_digest) order by w.ordinal), '[]'::jsonb)
    into v_workloads from ops.benchmark_manifest_workload w where w.draft_id = p_draft_id;

  select coalesce(jsonb_agg(jsonb_build_object(
           'percentile', r.percentile,
           'bytes', r.bytes) order by r.ordinal), '[]'::jsonb)
    into v_sizes from ops.benchmark_manifest_request_size r where r.draft_id = p_draft_id;

  select coalesce(jsonb_agg(to_jsonb(c.concurrency_level) order by c.ordinal), '[]'::jsonb)
    into v_levels from ops.benchmark_manifest_concurrency c where c.draft_id = p_draft_id;

  select coalesce(jsonb_agg(jsonb_build_object(
           'name', b.name, 'version', b.version, 'build', b.build) order by b.ordinal), '[]'::jsonb)
    into v_browsers from ops.benchmark_manifest_browser b where b.draft_id = p_draft_id;

  select coalesce(jsonb_agg(jsonb_build_object(
           'actor_id', e.actor_id, 'session_ref', e.session_ref,
           'authority_class', e.authority_class) order by e.ordinal), '[]'::jsonb)
    into v_evaluators from ops.benchmark_manifest_evaluator e where e.draft_id = p_draft_id;

  -- Exactly the twenty-six r7 payload fields: the thirty manifest fields minus
  -- the four acceptance-envelope fields the canonicalization contract excludes,
  -- so no artifact hashes its own digest.
  return jsonb_build_object(
    'subject_digest', v_draft.subject_digest,
    'candidate_digest', v_draft.candidate_digest,
    'policy_digest', v_draft.policy_digest,
    'capacity_profiles', ops.benchmark_dimension_array(p_draft_id, 'capacity_profiles'),
    'workload_mix', v_workloads,
    'request_size_distribution', v_sizes,
    'concurrency_levels', v_levels,
    'arrival_patterns', ops.benchmark_dimension_array(p_draft_id, 'arrival_patterns'),
    'routes', ops.benchmark_dimension_array(p_draft_id, 'routes'),
    'browsers', v_browsers,
    'runtime_versions', ops.benchmark_dimension_array(p_draft_id, 'runtime_versions'),
    'device_profiles', ops.benchmark_dimension_array(p_draft_id, 'device_profiles'),
    'hardware_profiles', ops.benchmark_dimension_array(p_draft_id, 'hardware_profiles'),
    'network_profiles', ops.benchmark_dimension_array(p_draft_id, 'network_profiles'),
    'cache_states', ops.benchmark_dimension_array(p_draft_id, 'cache_states'),
    'samples_per_cell', v_draft.samples_per_cell,
    'warmup_runs', v_draft.warmup_runs,
    'p95_aggregation_method', v_draft.p95_aggregation_method,
    'outlier_rule', v_draft.outlier_rule,
    'acknowledgement_endpoints', ops.benchmark_dimension_array(p_draft_id, 'acknowledgement_endpoints'),
    'evaluator_identities', v_evaluators,
    'comparator_versions', ops.benchmark_dimension_array(p_draft_id, 'comparator_versions'),
    'slo_thresholds', ops.benchmark_slo_thresholds(),
    'cost_expectation_matrix_digest', v_draft.cost_expectation_matrix_digest,
    'cost_variance_thresholds', ops.benchmark_cost_variance_thresholds(),
    'deadline_contract', ops.benchmark_deadline_contract());
end;
$$;

comment on function ops.benchmark_payload_preimage(uuid) is
  'The twenty-six r7 payload fields of one benchmark draft, rebuilt from its persisted rows with the fixed constants emitted rather than stored.';

-- r7 receipt_payload_digest_rule: SHA-256 over the canonical serialization of
-- [domain_tag, payload]. The array form is what artifact-trust.js's
-- digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]) hashes, so the two sides
-- produce the same bytes for the same manifest.
create or replace function ops.benchmark_payload_digest(p_draft_id uuid)
returns text language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(jsonb_build_array(
      ops.benchmark_payload_domain_tag(),
      ops.benchmark_payload_preimage(p_draft_id))),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.benchmark_payload_digest(uuid) is
  'The r7 exact hash a verified partner accepts: sha256 over the canonical [domain_tag, payload] of one benchmark draft, recomputed from its rows.';

-- ---------------------------------------------------------------------------
-- Structural validation. Every clause is a recomputation from the persisted
-- rows, so a tampered row cannot answer for itself.
-- ---------------------------------------------------------------------------
create or replace function ops.benchmark_draft_structure_error(p_draft_id uuid)
returns text language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_draft ops.benchmark_manifest_draft%rowtype;
  v_dimension text; v_count integer; v_total integer;
begin
  select * into v_draft from ops.benchmark_manifest_draft where id = p_draft_id;
  if not found then return format('draft %s does not exist', p_draft_id); end if;

  -- Every ordered list must be present, non-empty, and contiguously ordinaled
  -- from zero. A gap in the ordinals means a row was expected and is missing,
  -- which is the shape a partial insert leaves behind.
  foreach v_dimension in array array[
    'capacity_profiles', 'arrival_patterns', 'routes', 'runtime_versions',
    'device_profiles', 'hardware_profiles', 'network_profiles',
    'acknowledgement_endpoints', 'comparator_versions', 'cache_states'
  ] loop
    select count(*) into v_count from ops.benchmark_manifest_dimension d
     where d.draft_id = p_draft_id and d.dimension = v_dimension;
    if v_count < 1 then
      return format('draft %s declares no %s', p_draft_id, v_dimension);
    end if;
    if v_count <> (select coalesce(max(d.ordinal), -1) + 1 from ops.benchmark_manifest_dimension d
                    where d.draft_id = p_draft_id and d.dimension = v_dimension) then
      return format('draft %s has a gap in the %s ordinals', p_draft_id, v_dimension);
    end if;
  end loop;

  -- cache_states carries minItems 2 over a closed two-value enum, so both
  -- states are required and neither is optional.
  select count(*) into v_count from ops.benchmark_manifest_dimension d
   where d.draft_id = p_draft_id and d.dimension = 'cache_states';
  if v_count <> 2 then
    return format('draft %s must declare both cache states, it declares %s', p_draft_id, v_count);
  end if;

  for v_dimension, v_count in
    select 'workload_mix', count(*) from ops.benchmark_manifest_workload where draft_id = p_draft_id
    union all
    select 'request_size_distribution', count(*) from ops.benchmark_manifest_request_size where draft_id = p_draft_id
    union all
    select 'concurrency_levels', count(*) from ops.benchmark_manifest_concurrency where draft_id = p_draft_id
    union all
    select 'browsers', count(*) from ops.benchmark_manifest_browser where draft_id = p_draft_id
    union all
    select 'evaluator_identities', count(*) from ops.benchmark_manifest_evaluator where draft_id = p_draft_id
  loop
    if v_count < 1 then
      return format('draft %s declares no %s', p_draft_id, v_dimension);
    end if;
  end loop;

  if (select count(*) from ops.benchmark_manifest_workload where draft_id = p_draft_id)
     <> (select coalesce(max(ordinal), -1) + 1 from ops.benchmark_manifest_workload where draft_id = p_draft_id)
   or (select count(*) from ops.benchmark_manifest_request_size where draft_id = p_draft_id)
     <> (select coalesce(max(ordinal), -1) + 1 from ops.benchmark_manifest_request_size where draft_id = p_draft_id)
   or (select count(*) from ops.benchmark_manifest_concurrency where draft_id = p_draft_id)
     <> (select coalesce(max(ordinal), -1) + 1 from ops.benchmark_manifest_concurrency where draft_id = p_draft_id)
   or (select count(*) from ops.benchmark_manifest_browser where draft_id = p_draft_id)
     <> (select coalesce(max(ordinal), -1) + 1 from ops.benchmark_manifest_browser where draft_id = p_draft_id)
   or (select count(*) from ops.benchmark_manifest_evaluator where draft_id = p_draft_id)
     <> (select coalesce(max(ordinal), -1) + 1 from ops.benchmark_manifest_evaluator where draft_id = p_draft_id) then
    return format('draft %s has a gap in a payload list''s ordinals', p_draft_id);
  end if;

  select coalesce(sum(weight_basis_points), 0) into v_total
    from ops.benchmark_manifest_workload where draft_id = p_draft_id;
  if v_total <> 10000 then
    return format('draft %s workload weights total %s basis points, not 10000', p_draft_id, v_total);
  end if;

  return null;
end;
$$;

comment on function ops.benchmark_draft_structure_error(uuid) is
  'Why one benchmark draft is not a complete r7 payload, recomputed from its rows, or null when it is.';

create or replace function ops.benchmark_draft_structure_valid(p_draft_id uuid)
returns boolean language sql stable security definer
set search_path = pg_catalog, ops, public
as $$ select ops.benchmark_draft_structure_error(p_draft_id) is null $$;

comment on function ops.benchmark_draft_structure_valid(uuid) is
  'True when one benchmark draft is a complete r7 payload.';

-- A draft is only complete once its dimensions, workloads and identities are
-- in, so its structure and its digest are checked at COMMIT.
create or replace function ops.benchmark_draft_complete()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_error text; v_live text;
begin
  v_error := ops.benchmark_draft_structure_error(new.id);
  if v_error is not null then
    raise exception 'benchmark draft is not a complete r7 payload: %', v_error;
  end if;
  v_live := ops.benchmark_payload_digest(new.id);
  if new.payload_digest <> v_live then
    raise exception 'benchmark payload digest does not match its rows: stored %, computed %',
      new.payload_digest, v_live;
  end if;
  return null;
end;
$$;

comment on function ops.benchmark_draft_complete() is
  'Deferred completeness check: at commit a benchmark draft must be a complete r7 payload and its stored payload digest must equal the digest recomputed from its own rows.';

drop trigger if exists benchmark_draft_complete on ops.benchmark_manifest_draft;
create constraint trigger benchmark_draft_complete
  after insert on ops.benchmark_manifest_draft
  deferrable initially deferred
  for each row execute function ops.benchmark_draft_complete();

-- ---------------------------------------------------------------------------
-- THE TWO ACCEPTANCE PREREQUISITES.
-- ---------------------------------------------------------------------------

-- PREREQUISITE ONE, BOUND. The accepted portfolio constitution, read through
-- the existing 0496 rail. ops.portfolio_accepted_revision() already recomputes
-- every digest from the persisted rows and raises on an integrity failure, so
-- this function adds no second integrity policy: it resolves, re-verifies the
-- stored accepted digest against the recomputed one, and refuses when no
-- portfolio has been accepted at all. It never falls back and never invents an
-- accepted portfolio.
--
-- READ THE RESULT EXACTLY. p_portfolio_ref is a reference the ACCEPTOR chooses,
-- so what comes back says: this named portfolio constitution is accepted, is
-- intact right now, and carries this acceptance instant. It does NOT say the
-- benchmark being accepted descends from it. There is no benchmark-to-portfolio
-- lineage anywhere in this database to consult, so this function invents no
-- lineage and asserts none, and the acceptance receipt records only the binding
-- above. Anyone who needs the descent relation has to record it first; adding a
-- lineage rule here would be a policy this slice does not hold.
create or replace function ops.benchmark_portfolio_prerequisite(p_portfolio_ref text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_revision_id uuid; v_live text; v_stored text; v_accepted_at timestamptz;
begin
  if p_portfolio_ref is null or btrim(p_portfolio_ref) = '' then
    raise exception 'benchmark acceptance requires the accepted portfolio constitution it descends from; no portfolio reference was supplied';
  end if;
  -- Raises on integrity failure; null means nothing is accepted.
  v_revision_id := ops.portfolio_accepted_revision(p_portfolio_ref);
  if v_revision_id is null then
    raise exception 'benchmark acceptance requires an accepted portfolio constitution; portfolio % has no acceptance receipt. r7 makes step:benchmark-contract-human-exact-hash-acceptance-receipt depend on step:portfolio-constitution-human-exact-hash-acceptance-receipt, and no accepted portfolio is created or assumed here.',
      p_portfolio_ref;
  end if;
  v_live := ops.portfolio_accepted_digest(v_revision_id);
  -- An absent recomputed digest is a refusal, not a match. Without this, a null
  -- on both sides would satisfy the IS DISTINCT FROM below and the prerequisite
  -- would return a binding with no digest in it.
  if v_live is null then
    raise exception 'accepted portfolio % produced no accepted digest', p_portfolio_ref
      using errcode = 'integrity_constraint_violation';
  end if;
  select r.accepted_digest into v_stored from ops.portfolio_revision r where r.id = v_revision_id;
  if v_stored is distinct from v_live then
    raise exception 'accepted portfolio % no longer produces its accepted digest', p_portfolio_ref
      using errcode = 'integrity_constraint_violation';
  end if;
  select a.accepted_at into v_accepted_at
    from ops.portfolio_revision_acceptance_receipt a
   where a.portfolio_revision_id = v_revision_id;
  if v_accepted_at is null then
    raise exception 'accepted portfolio % carries no acceptance instant', p_portfolio_ref
      using errcode = 'integrity_constraint_violation';
  end if;
  return jsonb_build_object(
    'portfolio_ref', p_portfolio_ref,
    'portfolio_revision_id', v_revision_id,
    'portfolio_accepted_digest', v_live,
    'portfolio_accepted_at', v_accepted_at);
end;
$$;

comment on function ops.benchmark_portfolio_prerequisite(text) is
  'The accepted portfolio constitution NAMED BY THE ACCEPTOR, resolved through the existing 0496 rail and verified against digests recomputed from its rows. Proves that the named portfolio is accepted and intact; it does not prove that the benchmark descends from it, because no benchmark-to-portfolio lineage is recorded anywhere. Refuses when no portfolio is accepted; invents none.';

-- PREREQUISITE TWO, NOW BOUND. THE PRIVATE GATE ZERO READER.
--
-- THIS BLOCK WAS A STUB FROM V5-A00 UNTIL migration 0502. What it said then was
-- true then: no table in this database held an authenticated Gate Zero outcome,
-- so an acceptance had nothing here to bind to. Migration
-- 0502_gate_zero_read_only_outcome.sql lands that table, and this reader is
-- implemented against it in the same change -- which the stub's own integration
-- requirement demanded, because either half alone opens the gate without a
-- record on the other side. The module-side twin, readGateZeroOutcome in
-- mcp-server/src/benchmark-acceptance-store.v5.js, landed with it.
--
-- ONE CLAUSE OF THE OLD BLOCK WAS ALSO WRONG BY THE TIME IT WAS DELETED, and it
-- is worth naming rather than quietly dropping. It said r7 registers no v5
-- producer for step:gate-zero-read-only-outcome and that this is intentional.
-- The card-10 amendment (decision 311a9af5-3685-4c47-a158-f8dd70870ca1, applied
-- under Joe's ruling on open loop #589) added the producer row, and
-- tools/doctorcre-v5-review.cjs's own comment now says the external boundary
-- moved down to the four predecessors. Gate Zero is a v5 producer with a
-- registered role, oracle, output schema, evidence scope and gate.
--
-- WHO PRODUCES ONE. The independent oracle seat -- the Codex reviewer lane,
-- staffed under charter ruling 8a1dad08-8707-4bb0-a159-c2831a00cea2 and Joe's
-- blanket approval 5e2b8c1a-9f47-4d63-b0e5-7a3d1c9f2e84 -- writing through
-- ops.gate_zero_record_read_only_outcome under Joe's 2026-09-13 ruling
-- d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70. No partner countersign. The human act
-- in this chain stays where the provisional ruling put it: downstream, on the
-- benchmark manifest acceptance, which is unchanged by all of this.
--
-- WHAT THIS FUNCTION STILL REFUSES TO DO. It does not accept an outcome digest
-- from the caller, read one out of configuration, derive one from a synthetic
-- fixture, select a work-request reference, or treat a recorded non-passing run
-- as a binding. Each of those would manufacture the exact authority the record
-- is supposed to carry. It reads one row and returns three fields.
--
-- WHICH ROW IS CURRENT, as an ordered procedure rather than a description:
-- status must be 'pass'; the expiry must not have passed; of what remains the
-- latest observed_at wins, tie-broken on outcome_digest descending so the order
-- is total; and none left raises, which is the same fail-closed posture the stub
-- had. The two refusals are distinguished -- nothing ever recorded, versus
-- everything recorded being expired or non-passing -- because those are
-- different problems for whoever hits them.
--
-- IT IS DEFINED IN TWO PLACES ON PURPOSE, AND THEY MUST NOT DRIFT. Migration
-- 0505 carries the current definition that binds in a database; this file is candidate
-- source and is still not in public.schema_migrations. The bodies are
-- deliberately identical, and mcp-server/test/benchmark-acceptance-store.v5.test.mjs
-- asserts so by reading both files.
--
-- PRIVATE means private: this function is granted to no role below. It is
-- reachable only from the security-definer write path in this file, which runs
-- as the owner. It is not a public claim about Gate Zero and it is not a
-- configuration surface.
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
  -- THE CLOSED THREE-FIELD OBJECT the whole foundation join hangs on
  -- (benchmark-minimum.v5.js:435, :1449-1453). Nothing else is returned: a
  -- fourth field here is a value some future reader consumes as something it
  -- is not.
  return jsonb_build_object(
    'step_ref', v_row.step_ref,
    'outcome_digest', v_row.outcome_digest,
    'observed_at', to_char(v_row.observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
$$;

comment on function ops.benchmark_gate_zero_outcome() is
  'PRIVATE reader for the current Gate Zero read-only outcome: the latest passing, unexpired row in ops.gate_zero_read_only_outcome, as the closed { step_ref, outcome_digest, observed_at }. Recomputes the tagged consumer-gate-receipt.v1 digest from the stored receipt and refuses a mismatch before answering. Raises when there is no current row, which is the same fail-closed posture this reader had while it was a stub. Granted to no role; reachable only from the definer write path in this file.';

-- THE THIRD BINDING, NOW BOUND, AND DELIBERATELY STILL INDEPENDENT OF THE
-- SECOND. THE PRIVATE MEASUREMENT COVERAGE PROOF READER.
--
-- WHAT IT READS. ops.benchmark_manifest_review.measurement_set_digest names the
-- bytes a reviewer read. ops.benchmark_measurement_coverage_attestation, written
-- in the same definer call, says HOW that digest came to be there: which
-- evaluator proved coverage, which payload digest it proved against, which
-- measurement digest it proved over. This function reads that attestation and
-- returns the three fields acceptance binds.
--
-- IT DOES NOT TRUST WHAT IT READS. Two of the three attested values are checked
-- against something the database derives for itself:
--   * the payload digest is RECOMPUTED with ops.benchmark_payload_digest() from
--     the draft's own rows. An attestation naming a payload digest the draft no
--     longer produces is refused, so appending content to a draft after a review
--     invalidates the attestation instead of silently outliving it;
--   * the measurement digest is compared against the review row's own. An
--     attestation that does not attest THIS review's bytes is refused;
--   * the evaluator name is constrained to ops.benchmark_coverage_evaluators().
-- A missing attestation refuses, exactly as it did when none could exist.
--
-- THIS IS NOT A SECOND COVERAGE AUTHORITY. It evaluates no matrix, reads no
-- samples and re-decides no r7 pass_rule; benchmark-minimum.v5.js remains the
-- only place coverage is judged. It answers "is there a checkable record binding
-- this review's digest to that judgement", and no more than that.
--
-- WHAT IS STILL TRUE AFTER THIS LANDS, said here rather than left to be
-- discovered: the samples are outside this record layer, the evaluation cannot
-- be repeated here, and the database is believing a trusted writer about work it
-- did not do. The attestation makes that belief explicit, attributed and
-- auditable, and makes an unattested pass unwritable. It does not make this
-- database a verifier of coverage.
--
-- IT IS SEPARATE FROM GATE ZERO ON PURPOSE, in both directions, and it stayed
-- separate when Gate Zero landed. This binding resolving did not resolve Gate
-- Zero, and Gate Zero landing in migration 0502 does not upgrade this one:
-- ops.benchmark_gate_zero_outcome() is still read first and still raises when
-- there is no current passing outcome, and this reader still refuses on its own
-- grounds afterwards. That is the fourth thing the coverage requirement
-- explicitly refuses -- silently upgrading the assertion when Gate Zero lands.
create or replace function ops.benchmark_measurement_coverage_binding(p_review_id uuid)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_review ops.benchmark_manifest_review%rowtype;
  v_attestation ops.benchmark_measurement_coverage_attestation%rowtype;
  v_live text;
begin
  select * into v_review from ops.benchmark_manifest_review where id = p_review_id;
  if not found then
    raise exception 'the coverage proof binding names an unknown benchmark review %', p_review_id;
  end if;

  select * into v_attestation from ops.benchmark_measurement_coverage_attestation
   where review_id = p_review_id;
  if not found then
    raise exception 'benchmark acceptance requires a recorded coverage proof binding for the measurement set named by review %, and none is recorded: measurement_set_digest names the bytes a trusted writer read, and without an attestation beside it nothing binds those bytes to a coverage evaluation by benchmark-minimum.v5.js. Accepting on the digest alone would claim an independent verification that does not exist. This function evaluates no coverage and is not a second coverage authority.', p_review_id;
  end if;

  -- RECOMPUTED, not read back. This is the half of the attestation the database
  -- can check for itself, and checking it is what makes the record more than a
  -- note the writer left.
  v_live := ops.benchmark_payload_digest(v_attestation.draft_id);
  if v_attestation.benchmark_payload_digest is distinct from v_live then
    raise exception 'the coverage proof binding for review % attests payload digest %, which this draft no longer produces: it now produces %',
      p_review_id, v_attestation.benchmark_payload_digest, v_live;
  end if;
  if v_attestation.draft_id is distinct from v_review.draft_id then
    raise exception 'the coverage proof binding for review % attests a different draft', p_review_id;
  end if;
  -- The attestation must attest THIS review's bytes. The write path already
  -- refuses a divergence, so this is the fail-closed half: a future write path
  -- that lost that check must not become an acceptance that bound nothing.
  if v_attestation.measurement_set_digest is distinct from v_review.measurement_set_digest then
    raise exception 'the coverage proof binding for review % attests measurement set %, which is not the set the review names',
      p_review_id, v_attestation.measurement_set_digest;
  end if;
  if not (v_attestation.coverage_proved_by = any (ops.benchmark_coverage_evaluators())) then
    raise exception 'the coverage proof binding for review % names %, which is not a benchmark coverage evaluator this rail admits',
      p_review_id, v_attestation.coverage_proved_by;
  end if;

  -- The documented return: exactly what acceptance binds. The evaluation digest
  -- is deliberately NOT returned -- acceptance binds nothing to it, and a value
  -- on this result that nothing consumes invites a future reader to consume it.
  return jsonb_build_object(
    'review_id', v_review.id,
    'measurement_set_digest', v_attestation.measurement_set_digest,
    'coverage_proved_by', v_attestation.coverage_proved_by);
end;
$$;

comment on function ops.benchmark_measurement_coverage_binding(uuid) is
  'PRIVATE reader for the proof binding between a review''s measurement_set_digest and a kernel coverage evaluation. Returns { review_id, measurement_set_digest, coverage_proved_by } for an attested review and raises for one with no attestation, one whose attested payload digest the draft no longer produces, one whose measurement digest is not the review''s, and one naming an evaluator outside ops.benchmark_coverage_evaluators(). Evaluates no coverage and is not a second coverage authority: the samples are outside this record layer and the evaluation cannot be repeated here. Independent of the Gate Zero binding on purpose. Granted to no role.';

-- ---------------------------------------------------------------------------
-- Proposal, review and acceptance guards. Everything each act depends on is
-- checked HERE, so a handler bug cannot admit an unreviewed, stale or
-- misattributed hash.
-- ---------------------------------------------------------------------------
create or replace function ops.benchmark_proposal_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_units integer;
begin
  if new.proposed_by_actor_id <> ops.portfolio_writer_actor_id() then
    raise exception 'benchmark proposal actor does not match the authenticated writer context';
  end if;
  -- THE ONE BOUND THE COLUMN CHECK CANNOT STATE. r7 bounds outlier_rule at
  -- 5..300 and the kernel enforces that with String#length, in UTF-16 code
  -- units; the column's char_length check counts codepoints, so it would admit a
  -- 300-character astral string the kernel refuses at 600 units. Both bounds are
  -- kept: the column decides the shape, this decides the same number the kernel
  -- means by it.
  v_units := ops.benchmark_utf16_length(new.outlier_rule);
  if v_units < 5 or v_units > 300 then
    raise exception 'benchmark outlier rule is % UTF-16 code units; r7 bounds it to 5..300 as benchmark-minimum.v5.js measures it', v_units;
  end if;
  return new;
end;
$$;

comment on function ops.benchmark_proposal_guard() is
  'Binds a benchmark draft to the server-established writer, so a draft cannot be attributed to another actor, and bounds outlier_rule by the UTF-16 code-unit length the kernel measures rather than by codepoints alone.';

drop trigger if exists benchmark_proposal_guard on ops.benchmark_manifest_draft;
create trigger benchmark_proposal_guard
  before insert on ops.benchmark_manifest_draft
  for each row execute function ops.benchmark_proposal_guard();

create or replace function ops.benchmark_review_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_live text; v_proposer uuid; v_error text; v_units integer;
begin
  select proposed_by_actor_id into v_proposer from ops.benchmark_manifest_draft
   where id = new.draft_id;
  if not found then
    raise exception 'benchmark review names an unknown draft';
  end if;
  -- The record layer's own bound on review_summary, counted the way the module
  -- that supplies it counts. See the proposal guard: the column check counts
  -- codepoints, this counts UTF-16 code units, and the two differ on astral
  -- text. r7 states no bound here at all -- this one belongs to this rail.
  v_units := ops.benchmark_utf16_length(new.review_summary);
  if v_units > 1000 then
    raise exception 'benchmark review summary is % UTF-16 code units; this rail bounds it to 1000', v_units;
  end if;
  -- The reviewer is the server-established writer, never a payload field.
  if new.reviewer_actor_id <> ops.portfolio_writer_actor_id() then
    raise exception 'benchmark review actor does not match the authenticated writer context';
  end if;
  -- CURRENTNESS. The digest is recomputed from the rows; a review naming
  -- anything else was written against different bytes.
  v_live := ops.benchmark_payload_digest(new.draft_id);
  if new.reviewed_payload_digest <> v_live then
    raise exception 'benchmark review digest is stale: expected %', v_live;
  end if;
  if new.verdict = 'pass' then
    v_error := ops.benchmark_draft_structure_error(new.draft_id);
    if v_error is not null then
      raise exception 'a benchmark draft that is not a complete r7 payload cannot pass review: %', v_error;
    end if;
    if new.reviewer_actor_id = v_proposer then
      raise exception 'a proposer may not pass their own benchmark draft';
    end if;
  end if;
  return new;
end;
$$;

comment on function ops.benchmark_review_guard() is
  'Refuses a benchmark review that is misattributed, over-long as the module measures length, written against a digest the draft no longer produces, a pass over an incomplete payload, or a self-review pass. It does not check coverage: nothing in this database does.';

drop trigger if exists benchmark_review_guard on ops.benchmark_manifest_review;
create trigger benchmark_review_guard
  before insert on ops.benchmark_manifest_review
  for each row execute function ops.benchmark_review_guard();

create or replace function ops.benchmark_acceptance_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  v_draft ops.benchmark_manifest_draft%rowtype;
  v_review ops.benchmark_manifest_review%rowtype;
  v_live text; v_error text; v_partner text; v_partner_actor_id uuid;
  v_portfolio jsonb; v_gate_zero jsonb; v_coverage jsonb;
begin
  select * into v_draft from ops.benchmark_manifest_draft where id = new.draft_id;
  if not found then raise exception 'benchmark acceptance names an unknown draft'; end if;
  if new.benchmark_ref <> v_draft.benchmark_ref then
    raise exception 'benchmark acceptance names the wrong benchmark';
  end if;

  -- EXACT HASH. The digest is RECOMPUTED from the persisted rows; a supplied
  -- hash is only ever compared against it, never trusted as the value.
  v_live := ops.benchmark_payload_digest(new.draft_id);
  if new.accepted_payload_digest <> v_live or v_draft.payload_digest <> v_live then
    raise exception 'benchmark acceptance digest is stale: expected %', v_live;
  end if;
  v_error := ops.benchmark_draft_structure_error(new.draft_id);
  if v_error is not null then
    raise exception 'benchmark acceptance requires a complete r7 payload: %', v_error;
  end if;

  select * into v_review from ops.benchmark_manifest_review where id = new.review_id;
  if not found then raise exception 'benchmark acceptance names an unknown review'; end if;
  if v_review.draft_id <> new.draft_id then
    raise exception 'benchmark acceptance names a review of a different draft';
  end if;
  if v_review.verdict <> 'pass' then
    raise exception 'benchmark acceptance requires a passing independent review';
  end if;
  if v_review.reviewed_payload_digest <> v_live then
    raise exception 'the passing benchmark review was written against different bytes';
  end if;
  -- NAMING, NOT PROVING. A passing review must name the measurement set its
  -- writer read; this clause checks that it did. It is not a coverage check, and
  -- the coverage proof binding below is what refuses to treat it as one.
  if v_review.measurement_set_digest is null then
    raise exception 'the passing benchmark review names no measurement set';
  end if;
  if v_review.reviewer_actor_id = v_draft.proposed_by_actor_id then
    raise exception 'the reviewer may not be the proposer of the same benchmark draft';
  end if;

  -- THE TRUST BOUNDARY. An actor id in the payload proves nothing: anyone able
  -- to insert could name Joe. What authenticates the acceptor is the database
  -- session -- the per-partner authority credential the server selects from
  -- verified session state. ops.authority_actor_slug() reads session_user and
  -- raises for any other principal, so a supplied id may only agree with it.
  v_partner := ops.authority_actor_slug();
  select id into v_partner_actor_id from public.actor
   where slug = v_partner and active and kind = 'human';
  if not found then
    raise exception 'partner authority session % has no active human actor', v_partner;
  end if;
  if new.accepted_by_actor_id <> v_partner_actor_id then
    raise exception 'benchmark acceptance actor does not match the authenticated partner session';
  end if;

  -- Three distinct roles, not two: a reviewer who can accept their own pass is
  -- not an independent reviewer, and a proposer who can accept their own draft
  -- is not being reviewed at all.
  if new.accepted_by_actor_id = v_draft.proposed_by_actor_id then
    raise exception 'the acceptor may not be the proposer of the same benchmark draft';
  end if;
  if new.accepted_by_actor_id = v_review.reviewer_actor_id then
    raise exception 'the acceptor may not also be the independent reviewer';
  end if;

  -- EVERY BINDING BELOW GOES THROUGH ops.benchmark_assert_bound(), AND THE
  -- REASON IS A FAIL-OPEN THAT USED TO BE HERE. These comparisons were written
  -- as `new.x <> (derived ->> 'x')`. When the derived side is null -- which is
  -- exactly what a reader that has not been implemented, or has been implemented
  -- badly, produces -- `<>` evaluates to NULL, `or` of NULLs is NULL, the IF does
  -- not fire, and an acceptance binding NOTHING is admitted. The helper refuses a
  -- null derived value, refuses a null supplied value, and compares with IS
  -- DISTINCT FROM.
  --
  -- PREREQUISITE ONE, re-derived here rather than trusted from the write
  -- function: the portfolio binding on this row must be the one the rail
  -- currently produces. What that proves is stated at the receipt's columns: the
  -- named portfolio is accepted and intact, not that this benchmark descends
  -- from it.
  v_portfolio := ops.benchmark_portfolio_prerequisite(new.portfolio_ref);
  perform ops.benchmark_assert_bound('accepted portfolio constitution', 'revision id',
    new.portfolio_revision_id, (v_portfolio ->> 'portfolio_revision_id')::uuid);
  perform ops.benchmark_assert_bound('accepted portfolio constitution', 'accepted digest',
    new.portfolio_accepted_digest, v_portfolio ->> 'portfolio_accepted_digest');
  perform ops.benchmark_assert_bound('accepted portfolio constitution', 'acceptance instant',
    new.portfolio_accepted_at, (v_portfolio ->> 'portfolio_accepted_at')::timestamptz);

  -- PREREQUISITE TWO, likewise re-derived. This raises today, which is why no
  -- row reaches the table.
  v_gate_zero := ops.benchmark_gate_zero_outcome();
  perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'step reference',
    new.gate_zero_step_ref, v_gate_zero ->> 'step_ref');
  perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'outcome digest',
    new.gate_zero_outcome_digest, v_gate_zero ->> 'outcome_digest');
  perform ops.benchmark_assert_bound('Gate Zero read-only outcome', 'observed instant',
    new.gate_zero_observed_at, (v_gate_zero ->> 'observed_at')::timestamptz);

  -- THE THIRD BINDING, WHICH NOW READS A RECORD RATHER THAN REFUSING FOR WANT OF
  -- ONE. The passing review named a measurement set above; the attestation
  -- recorded in the same call says which kernel evaluator proved it, against
  -- which payload digest, over which bytes. The reader recomputes the payload
  -- digest and refuses a stale attestation, so this binds a checked record.
  --
  -- IT STILL BINDS ONLY WHAT THE RECORD SAYS. The samples are outside this
  -- database, the evaluation cannot be repeated here, and this clause does not
  -- turn a trusted writer's attributed statement into an independent
  -- verification. It is also still ordered AFTER the Gate Zero binding, which
  -- still raises: acceptance refuses at Gate Zero, and reaching this line at all
  -- requires a fact this record layer does not hold.
  v_coverage := ops.benchmark_measurement_coverage_binding(new.review_id);
  perform ops.benchmark_assert_bound('measurement coverage proof', 'measurement set digest',
    v_review.measurement_set_digest, v_coverage ->> 'measurement_set_digest');
  -- The evaluator is bound too, against the closed set rather than against a
  -- value the receipt carries: there is no evaluator column on the receipt, and
  -- adding one would record a coverage claim the receipt does not make.
  perform ops.benchmark_assert_bound('measurement coverage proof', 'coverage evaluator',
    v_coverage ->> 'coverage_proved_by',
    (select e from unnest(ops.benchmark_coverage_evaluators()) e
      where e = v_coverage ->> 'coverage_proved_by'));

  -- STRICTLY AFTER, on the exclusive reading benchmark-minimum.v5.js uses for
  -- every member: an acceptance recorded AT a prerequisite instant did not
  -- follow it. The table constraints assert the same two inequalities; both are
  -- kept so a future direct insert cannot lose either.
  if new.accepted_at <= new.gate_zero_observed_at then
    raise exception 'benchmark acceptance must be strictly after the Gate Zero outcome';
  end if;
  if new.accepted_at <= new.portfolio_accepted_at then
    raise exception 'benchmark acceptance must be strictly after the portfolio constitution acceptance';
  end if;
  if new.accepted_at <= v_review.created_at then
    raise exception 'benchmark acceptance must be strictly after the review it rests on';
  end if;

  return new;
end;
$$;

comment on function ops.benchmark_acceptance_guard() is
  'Authoritative benchmark acceptance precondition: recomputed exact payload digest, complete payload, a fresh passing independent review on the same bytes naming its measurement set, three distinct identities, an acceptor derived from the authenticated partner session, the accepted portfolio constitution the acceptor named, the Gate Zero outcome as authenticated here, the recorded coverage proof binding for the review''s measurement set and its named evaluator, and an acceptance strictly after the portfolio, the Gate Zero instant and the review. Every binding comparison runs through ops.benchmark_assert_bound(), so an underived binding refuses instead of comparing to NULL and falling through. It still cannot succeed: the Gate Zero reader is read first and always raises.';

drop trigger if exists benchmark_acceptance_guard on ops.benchmark_manifest_acceptance_receipt;
create trigger benchmark_acceptance_guard
  before insert on ops.benchmark_manifest_acceptance_receipt
  for each row execute function ops.benchmark_acceptance_guard();

-- ---------------------------------------------------------------------------
-- The accepted benchmark, and the zero-effect readback.
-- ---------------------------------------------------------------------------
create or replace function ops.benchmark_current_accepted_draft(p_benchmark_ref text)
returns uuid language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select d.id from ops.benchmark_manifest_draft d
    join ops.benchmark_manifest_acceptance_receipt a on a.draft_id = d.id
   where d.benchmark_ref = p_benchmark_ref
   order by d.draft_version desc limit 1
$$;

comment on function ops.benchmark_current_accepted_draft(text) is
  'The highest-version benchmark draft carrying an acceptance receipt, regardless of integrity.';

create or replace function ops.benchmark_draft_integrity_error(p_draft_id uuid)
returns text language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_draft ops.benchmark_manifest_draft%rowtype;
        v_receipt ops.benchmark_manifest_acceptance_receipt%rowtype;
        v_live text; v_error text;
begin
  select * into v_draft from ops.benchmark_manifest_draft where id = p_draft_id;
  if not found then return format('draft %s does not exist', p_draft_id); end if;
  v_error := ops.benchmark_draft_structure_error(p_draft_id);
  if v_error is not null then return v_error; end if;
  v_live := ops.benchmark_payload_digest(p_draft_id);
  if v_draft.payload_digest <> v_live then
    return format('draft %s payload digest no longer matches its rows', p_draft_id);
  end if;
  select * into v_receipt from ops.benchmark_manifest_acceptance_receipt where draft_id = p_draft_id;
  if found and v_receipt.accepted_payload_digest <> v_live then
    return format('draft %s acceptance receipt names a digest the rows no longer produce', p_draft_id);
  end if;
  return null;
end;
$$;

comment on function ops.benchmark_draft_integrity_error(uuid) is
  'Why one benchmark draft is not trustworthy, recomputed from its rows, or null when it is intact.';

-- The accepted benchmark contract. It selects the current accepted identity
-- first and then REFUSES an integrity failure, rather than falling back to an
-- older healthy draft or reporting a tampered benchmark as no benchmark: both
-- would turn corruption into ordinary source.
create or replace function ops.benchmark_accepted_draft(p_benchmark_ref text)
returns uuid language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_id uuid; v_error text;
begin
  v_id := ops.benchmark_current_accepted_draft(p_benchmark_ref);
  if v_id is null then return null; end if;
  v_error := ops.benchmark_draft_integrity_error(v_id);
  if v_error is not null then
    raise exception 'accepted benchmark % failed integrity: %', p_benchmark_ref, v_error
      using errcode = 'integrity_constraint_violation';
  end if;
  return v_id;
end;
$$;

comment on function ops.benchmark_accepted_draft(text) is
  'The current accepted benchmark draft. Null when nothing is accepted; an explicit refusal when the current accepted draft fails integrity.';

create or replace function ops.benchmark_readback(p_benchmark_ref text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_draft ops.benchmark_manifest_draft%rowtype; v_accepted_id uuid;
begin
  select * into v_draft from ops.benchmark_manifest_draft
   where benchmark_ref = p_benchmark_ref
   order by draft_version desc limit 1;
  if not found then
    return jsonb_build_object('benchmark_ref', p_benchmark_ref, 'exists', false);
  end if;
  v_accepted_id := ops.benchmark_accepted_draft(p_benchmark_ref);

  return jsonb_build_object(
    'benchmark_ref', v_draft.benchmark_ref,
    'exists', true,
    'schema_version', v_draft.schema_version,
    'draft_version', v_draft.draft_version,
    'gate_id', v_draft.gate_id,
    'producer_step_ref', v_draft.producer_step_ref,
    'producer_role', v_draft.producer_role,
    'combiner', v_draft.combiner,
    -- Both digests are exposed on purpose: the STORED one is what the proposal
    -- claimed, the RECOMPUTED one is what the rows say now, and a reader that
    -- only ever saw one of them could not tell a tampered draft from a healthy
    -- one.
    'payload_digest', ops.benchmark_payload_digest(v_draft.id),
    'stored_payload_digest', v_draft.payload_digest,
    'payload', ops.benchmark_payload_preimage(v_draft.id),
    'structure_valid', ops.benchmark_draft_structure_valid(v_draft.id),
    'structure_error', ops.benchmark_draft_structure_error(v_draft.id),
    -- ORDERED BY (created_at, id), NOT created_at ALONE. created_at defaults to
    -- now(), which is transaction start time: every review written in one
    -- transaction carries the SAME instant, and an order that ties there returns
    -- rows in whatever order the plan happens to produce. The primary key breaks
    -- the tie, so two readers of the same draft see the same list.
    'reviews', (select coalesce(jsonb_agg(jsonb_build_object(
                         'verdict', r.verdict,
                         'reviewed_payload_digest', r.reviewed_payload_digest,
                         -- The bytes the review's writer NAMED. See
                         -- measurement_coverage_binding below before reading this
                         -- as verified coverage.
                         'measurement_set_digest', r.measurement_set_digest,
                         -- Per review, so a reader can see WHICH reviews carry an
                         -- attestation rather than being told about the rail in
                         -- general. A fail verdict carries none and needs none.
                         'coverage_proved_by', (
                           select a.coverage_proved_by
                             from ops.benchmark_measurement_coverage_attestation a
                            where a.review_id = r.id),
                         'reviewer_actor_id', r.reviewer_actor_id)
                       order by r.created_at, r.id), '[]'::jsonb)
                  from ops.benchmark_manifest_review r where r.draft_id = v_draft.id),
    -- Said out loud in the readback, because a caller reading a measurement
    -- digest off a passing review is entitled to know exactly how much this
    -- database checked -- which is more than nothing and much less than a
    -- verification.
    'measurement_coverage_binding', jsonb_build_object(
      'resolved', true,
      'note', 'measurement_set_digest names the bytes the review''s writer read. Beside each passing review this database records an attestation naming the kernel evaluator that proved coverage, the payload digest it proved against and the measurement digest it proved over, and it refuses an attestation whose payload digest the draft no longer produces. It still evaluates no coverage itself: the samples are outside this record layer and the evaluation cannot be repeated here, so this is an attributed, checkable assertion by a trusted writer and not an independent verification.'),
    'accepted', v_accepted_id is not null and v_accepted_id = v_draft.id,
    'accepted_draft_id', v_accepted_id,
    -- Inert by construction. Acceptance remains a separate human exact-hash act
    -- and starts no clock: the Journey 1 clock origin is the first current
    -- passing foundation-assurance-minimum receipt, which this rail neither
    -- issues nor reaches.
    'clock_started', false,
    'effects', jsonb_build_object(
      'creates_effect', false, 'jobs', 0, 'capabilities', 0, 'execution_envelopes', 0,
      'admissions', 0, 'schedules', 0, 'deployments', 0));
end;
$$;

comment on function ops.benchmark_readback(text) is
  'Deterministic zero-effect readback of one benchmark: its payload rebuilt from the stored rows, both the stored and the recomputed digest, its reviews, and whether a partner has accepted it.';

-- ---------------------------------------------------------------------------
-- The only write path. Each function derives its own actor and accepts none.
--
-- IDEMPOTENCY IS A REPLAY, NOT A SECOND WRITE. Each function looks its
-- idempotency key up first: an exact replay returns the row that already
-- exists, and the same key presented with different content is refused rather
-- than quietly writing a second row or silently returning the first.
-- ---------------------------------------------------------------------------
create or replace function ops.benchmark_propose_manifest_draft(
  p_benchmark_ref text, p_draft_version integer, p_idempotency_key uuid,
  p_payload_digest text, p_scalars jsonb, p_dimensions jsonb,
  p_workloads jsonb, p_request_sizes jsonb, p_concurrency jsonb,
  p_browsers jsonb, p_evaluators jsonb)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_draft uuid; v_actor uuid; v_existing ops.benchmark_manifest_draft%rowtype;
        d jsonb; w jsonb; r jsonb; c jsonb; b jsonb; e jsonb;
begin
  select * into v_existing from ops.benchmark_manifest_draft where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.benchmark_ref <> p_benchmark_ref
       or v_existing.draft_version <> p_draft_version
       or v_existing.payload_digest <> p_payload_digest then
      raise exception 'benchmark idempotency key % was already used for a different draft', p_idempotency_key;
    end if;
    return v_existing.id;
  end if;

  v_actor := ops.portfolio_writer_actor_id();
  insert into ops.benchmark_manifest_draft(
    benchmark_ref, draft_version, idempotency_key, schema_version, payload_domain_tag,
    gate_id, producer_step_ref, producer_role, combiner,
    subject_digest, candidate_digest, policy_digest, cost_expectation_matrix_digest,
    samples_per_cell, warmup_runs, p95_aggregation_method, outlier_rule,
    payload_digest, proposed_by_actor_id)
  values (p_benchmark_ref, p_draft_version, p_idempotency_key,
    'benchmark-manifest.v1', ops.benchmark_payload_domain_tag(),
    'benchmark-contract-accepted',
    'step:benchmark-contract-human-exact-hash-acceptance-receipt',
    'verified_partner_benchmark_authority', 'exact_verified_partner_hash_acceptance',
    p_scalars ->> 'subject_digest', p_scalars ->> 'candidate_digest',
    p_scalars ->> 'policy_digest', p_scalars ->> 'cost_expectation_matrix_digest',
    (p_scalars ->> 'samples_per_cell')::integer, (p_scalars ->> 'warmup_runs')::integer,
    p_scalars ->> 'p95_aggregation_method', p_scalars ->> 'outlier_rule',
    p_payload_digest, v_actor)
  returning id into v_draft;

  for d in select value from jsonb_array_elements(p_dimensions) loop
    insert into ops.benchmark_manifest_dimension(draft_id, dimension, ordinal, value)
    values (v_draft, d ->> 'dimension', (d ->> 'ordinal')::integer, d ->> 'value');
  end loop;

  for w in select value from jsonb_array_elements(p_workloads) loop
    insert into ops.benchmark_manifest_workload(
      draft_id, ordinal, workload_id, weight_basis_points, operation_mix_digest)
    values (v_draft, (w ->> 'ordinal')::integer, w ->> 'workload_id',
      (w ->> 'weight_basis_points')::integer, w ->> 'operation_mix_digest');
  end loop;

  for r in select value from jsonb_array_elements(p_request_sizes) loop
    insert into ops.benchmark_manifest_request_size(draft_id, ordinal, percentile, bytes)
    values (v_draft, (r ->> 'ordinal')::integer, (r ->> 'percentile')::integer,
      (r ->> 'bytes')::bigint);
  end loop;

  for c in select value from jsonb_array_elements(p_concurrency) loop
    insert into ops.benchmark_manifest_concurrency(draft_id, ordinal, concurrency_level)
    values (v_draft, (c ->> 'ordinal')::integer, (c ->> 'concurrency_level')::integer);
  end loop;

  for b in select value from jsonb_array_elements(p_browsers) loop
    insert into ops.benchmark_manifest_browser(draft_id, ordinal, name, version, build)
    values (v_draft, (b ->> 'ordinal')::integer, b ->> 'name', b ->> 'version', b ->> 'build');
  end loop;

  for e in select value from jsonb_array_elements(p_evaluators) loop
    insert into ops.benchmark_manifest_evaluator(
      draft_id, ordinal, actor_id, session_ref, authority_class)
    values (v_draft, (e ->> 'ordinal')::integer, e ->> 'actor_id',
      e ->> 'session_ref', e ->> 'authority_class');
  end loop;

  return v_draft;
end;
$$;

comment on function ops.benchmark_propose_manifest_draft(text,integer,uuid,text,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb) is
  'The only way to create a benchmark manifest draft. The draft is inert; the proposer is derived from the server-established writer context and is not a parameter. The supplied payload digest is compared at commit against one recomputed from the stored rows.';

-- THE REVIEW AND ITS ATTESTATION ARE ONE ACT. p_coverage_attestation is a fourth
-- kind of parameter and it is worth naming what it is not. It is not a coverage
-- VERDICT: the verdict is p_verdict, and on the MCP path it comes from the kernel
-- evaluator throwing or returning, never from an argument. It is not a caller
-- input either -- the MCP verb's inputSchema has no attestation field, and the
-- values below are the evaluator's own return value, computed on the write path
-- microseconds earlier. What it IS: the TRUSTED WRITER's statement of what
-- proved this pass, which is precisely the trusted-writer authority this rail
-- has always run on, made explicit instead of left anonymous.
--
-- A PASS WITHOUT ONE IS REFUSED HERE, which is what stops a pass and its
-- attestation coming apart. A direct caller holding the writer bundle can still
-- record a passing review -- that authority is preserved -- but it can no longer
-- record one without saying, on the record and under its own actor id, what
-- proved it.
--
-- The old six-argument signature is dropped rather than left beside this one: two
-- overloads would mean the unattested write path still existed under a shorter
-- name, which is exactly the thing being closed.
drop function if exists ops.benchmark_review_manifest_draft(uuid,uuid,text,text,text,text);

create or replace function ops.benchmark_review_manifest_draft(
  p_draft_id uuid, p_idempotency_key uuid, p_reviewed_payload_digest text,
  p_verdict text, p_measurement_set_digest text, p_review_summary text,
  p_coverage_attestation jsonb)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_id uuid;
  v_existing ops.benchmark_manifest_review%rowtype;
  v_existing_attestation ops.benchmark_measurement_coverage_attestation%rowtype;
  v_proved_by text; v_payload_digest text; v_measurements text; v_evaluation text;
  v_key text;
begin
  select * into v_existing from ops.benchmark_manifest_review where idempotency_key = p_idempotency_key;
  if found then
    -- EVERY STORED PARAMETER IS COMPARED, review_summary included. A replay that
    -- matched on the digests but carried different prose would return the first
    -- row while the caller believed the second had been recorded, which is the
    -- same silent divergence the digest comparison exists to prevent. The
    -- comparisons use IS DISTINCT FROM so a null measurement digest on both
    -- sides is a match rather than an unknown.
    if v_existing.draft_id is distinct from p_draft_id
       or v_existing.reviewed_payload_digest is distinct from p_reviewed_payload_digest
       or v_existing.verdict is distinct from p_verdict
       or v_existing.measurement_set_digest is distinct from p_measurement_set_digest
       or v_existing.review_summary is distinct from p_review_summary then
      raise exception 'benchmark idempotency key % was already used for a different review', p_idempotency_key;
    end if;
    -- THE ATTESTATION IS A STORED PARAMETER TOO, so it is compared on a replay
    -- for the same reason review_summary is: a replay carrying a different
    -- attestation is a different request wearing the same key, and returning the
    -- first row would leave the caller believing the second had been recorded.
    select * into v_existing_attestation from ops.benchmark_measurement_coverage_attestation
     where review_id = v_existing.id;
    if found <> (p_coverage_attestation is not null)
       or (p_coverage_attestation is not null and (
            v_existing_attestation.coverage_proved_by is distinct from (p_coverage_attestation ->> 'coverage_proved_by')
         or v_existing_attestation.benchmark_payload_digest is distinct from (p_coverage_attestation ->> 'benchmark_payload_digest')
         or v_existing_attestation.measurement_set_digest is distinct from (p_coverage_attestation ->> 'measurement_set_digest')
         or v_existing_attestation.evaluation_digest is distinct from (p_coverage_attestation ->> 'evaluation_digest'))) then
      raise exception 'benchmark idempotency key % was already used for a different review', p_idempotency_key;
    end if;
    return v_existing.id;
  end if;

  -- THE PASS/ATTESTATION PAIR, CHECKED BEFORE EITHER ROW EXISTS.
  if p_verdict = 'pass' and p_coverage_attestation is null then
    raise exception 'a passing benchmark review must record a measurement coverage attestation naming the kernel evaluator that proved coverage, the payload digest it proved against and the measurement digest it proved over; an unattested pass is refused';
  end if;
  if p_verdict <> 'pass' and p_coverage_attestation is not null then
    raise exception 'a benchmark review that does not pass proves no coverage and must record no coverage attestation';
  end if;

  if p_coverage_attestation is not null then
    if jsonb_typeof(p_coverage_attestation) <> 'object' then
      raise exception 'the benchmark coverage attestation must be an object';
    end if;
    -- CLOSED, the way every other shape in this rail is closed: an unknown key is
    -- a caller believing it recorded something that was silently dropped.
    for v_key in select key from jsonb_object_keys(p_coverage_attestation) as t(key) loop
      if v_key not in ('coverage_proved_by', 'benchmark_payload_digest',
                       'measurement_set_digest', 'evaluation_digest') then
        raise exception 'the benchmark coverage attestation carries an unknown field %', v_key;
      end if;
    end loop;
    v_proved_by      := p_coverage_attestation ->> 'coverage_proved_by';
    v_payload_digest := p_coverage_attestation ->> 'benchmark_payload_digest';
    v_measurements   := p_coverage_attestation ->> 'measurement_set_digest';
    v_evaluation     := p_coverage_attestation ->> 'evaluation_digest';
    if v_proved_by is null or v_payload_digest is null
       or v_measurements is null or v_evaluation is null then
      raise exception 'the benchmark coverage attestation must name the evaluator, the payload digest, the measurement digest and the evaluation digest';
    end if;
    if not (v_proved_by = any (ops.benchmark_coverage_evaluators())) then
      raise exception 'the benchmark coverage attestation names %, which is not a benchmark coverage evaluator this rail admits', v_proved_by;
    end if;
    -- THE ATTESTATION MUST BE ABOUT THIS REVIEW'S BYTES. Refused here as well as
    -- in the reader, because a row that never diverges is better than a
    -- divergence a later reader has to catch.
    if v_measurements is distinct from p_measurement_set_digest then
      raise exception 'the benchmark coverage attestation proves coverage over measurement set %, which is not the set this review names', v_measurements;
    end if;
    -- AND ABOUT THIS REVIEW'S PAYLOAD, pinned to the digest the review itself
    -- names rather than to a second recomputation here.
    --
    -- THAT IS NOT A WEAKER CHECK, AND THE REASON IS WORTH STATING. The review
    -- guard already refuses this insert unless p_reviewed_payload_digest is the
    -- digest ops.benchmark_payload_digest() produces from the draft's rows right
    -- now; it is the rail's ONE currentness authority and the same insert runs
    -- it. Pinning the attestation to that value therefore binds it to the live
    -- digest transitively, through the check that already exists, instead of
    -- adding a second currentness authority that could disagree with it. It also
    -- keeps an unknown draft arriving as the guard's named refusal rather than as
    -- a raw digest error, and it does not rebuild the whole preimage twice per
    -- review. The reader recomputes independently at acceptance time, which is
    -- where an attestation that has been outgrown actually matters.
    if v_payload_digest is distinct from p_reviewed_payload_digest then
      raise exception 'the benchmark coverage attestation proves coverage against payload digest %, which this draft does not produce', v_payload_digest;
    end if;
  end if;

  insert into ops.benchmark_manifest_review(
    draft_id, idempotency_key, reviewed_payload_digest, verdict,
    measurement_set_digest, review_summary, reviewer_actor_id)
  values (p_draft_id, p_idempotency_key, p_reviewed_payload_digest, p_verdict,
    p_measurement_set_digest, p_review_summary, ops.portfolio_writer_actor_id())
  returning id into v_id;

  if p_coverage_attestation is not null then
    -- SAME CALL, SAME TRANSACTION, SAME DERIVED ACTOR. The attesting writer is
    -- ops.portfolio_writer_actor_id() exactly as the reviewer is: the attestation
    -- is attributed to whoever recorded the review, and is not a parameter.
    insert into ops.benchmark_measurement_coverage_attestation(
      review_id, draft_id, coverage_proved_by, benchmark_payload_digest,
      measurement_set_digest, evaluation_digest, attested_by_actor_id)
    values (v_id, p_draft_id, v_proved_by, v_payload_digest,
      v_measurements, v_evaluation, ops.portfolio_writer_actor_id());
  end if;

  return v_id;
end;
$$;

comment on function ops.benchmark_review_manifest_draft(uuid,uuid,text,text,text,text,jsonb) is
  'The only way to record an independent benchmark review, and the only way to record the coverage attestation that must accompany a passing one. The reviewer and the attesting writer are both derived from the server-established writer context and neither is a parameter. A pass with no attestation is refused and a non-pass with one is refused, so the pass and its attestation cannot come apart. The attestation is still a TRUSTED WRITER''s statement -- through the MCP verb its values are benchmark-minimum.v5.js''s own return value, through a direct call they are the writer''s assertion -- but it is now explicit, attributed, closed to a known evaluator set, and checked against the digest this draft actually produces. Every stored parameter, review_summary and the attestation included, is compared on an idempotent replay.';

-- THE PASS/ATTESTATION PAIR, ENFORCED AT COMMIT AS WELL AS AT THE WRITE PATH.
-- The function above refuses an unattested pass, and the function above is the
-- only write path any role can reach -- direct INSERT is granted to nobody. What
-- this deferred constraint trigger adds is the TABLE OWNER, from whom INSERT
-- cannot be revoked, and any future write path that forgets the rule. It has to
-- be DEFERRED because the attestation row references the review row and
-- therefore cannot exist until after it.
create or replace function ops.benchmark_pass_requires_attestation()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  if new.verdict = 'pass' and not exists (
       select 1 from ops.benchmark_measurement_coverage_attestation where review_id = new.id) then
    raise exception 'benchmark review % passes and records no measurement coverage attestation; an unattested pass is refused', new.id;
  end if;
  return null;
end;
$$;

comment on function ops.benchmark_pass_requires_attestation() is
  'Commit-time refusal of a passing benchmark review with no coverage attestation. The write function refuses one first; this is the half that binds the table owner, from whom INSERT cannot be revoked.';

drop trigger if exists benchmark_pass_requires_attestation on ops.benchmark_manifest_review;
create constraint trigger benchmark_pass_requires_attestation
  after insert on ops.benchmark_manifest_review
  deferrable initially deferred
  for each row execute function ops.benchmark_pass_requires_attestation();

-- ACCEPTANCE. Every authoritative fact is DERIVED inside this function: the
-- partner from the authenticated session, the portfolio binding from the 0496
-- rail, and the Gate Zero outcome from the private reader. The caller supplies
-- only the draft, the key, the hash it believes is current, the review it rests
-- on, and which portfolio it descends from. There is no parameter through which
-- an accepted_by, a verified boolean or a Gate Zero digest could arrive.
--
-- THIS FUNCTION CANNOT SUCCEED TODAY, FOR ONE REMAINING REASON:
-- ops.benchmark_gate_zero_outcome() raises, and it is called before any row is
-- written. ops.benchmark_measurement_coverage_binding() NO LONGER RAISES. It
-- reads the attestation recorded beside the review and RETURNS for an attested
-- review, refusing only a review whose record does not hold up: no attestation,
-- an attested payload digest the draft no longer produces, an evaluator outside
-- the closed set, or a measurement digest the review does not name. What it
-- returns is still a trusted writer's attributed assertion and not an
-- independent verification -- the samples stay outside this record layer and
-- this database evaluates no coverage itself.
create or replace function ops.benchmark_accept_manifest_draft(
  p_draft_id uuid, p_idempotency_key uuid, p_accepted_payload_digest text,
  p_review_id uuid, p_portfolio_ref text)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_id uuid; v_partner text; v_actor uuid; v_benchmark_ref text;
  v_portfolio jsonb; v_gate_zero jsonb; v_coverage jsonb;
  v_existing ops.benchmark_manifest_acceptance_receipt%rowtype;
begin
  select * into v_existing from ops.benchmark_manifest_acceptance_receipt
   where idempotency_key = p_idempotency_key;
  if found then
    -- p_portfolio_ref is compared too: it is a stored column and it selects
    -- WHICH portfolio the receipt binds, so a replay naming a different one is a
    -- different request wearing the same key.
    if v_existing.draft_id is distinct from p_draft_id
       or v_existing.accepted_payload_digest is distinct from p_accepted_payload_digest
       or v_existing.review_id is distinct from p_review_id
       or v_existing.portfolio_ref is distinct from p_portfolio_ref then
      raise exception 'benchmark idempotency key % was already used for a different acceptance', p_idempotency_key;
    end if;
    return v_existing.id;
  end if;

  -- Raises unless session_user is an admitted partner authority principal.
  v_partner := ops.authority_actor_slug();
  select id into v_actor from public.actor where slug = v_partner and active and kind = 'human';
  if not found then
    raise exception 'partner authority session % has no active human actor', v_partner;
  end if;

  -- THE WRITER'S HALF OF THE LOCK PROTOCOL described at
  -- ops.benchmark_content_frozen_after_acceptance(). FOR UPDATE on the draft row
  -- conflicts with the FOR SHARE that trigger takes on the same row, so a content
  -- insert racing this acceptance must settle before either proceeds and the
  -- digest recomputed below cannot be recomputed over rows a neighbour is still
  -- adding to. It serializes those two paths and nothing else: any future writer
  -- gets the same guarantee only by taking a conflicting mode on this row, and
  -- inserts into the six content tables do so through the trigger, which fires
  -- whatever code path issued them.
  select benchmark_ref into v_benchmark_ref from ops.benchmark_manifest_draft
   where id = p_draft_id for update;
  if not found then raise exception 'benchmark acceptance names an unknown draft'; end if;

  -- All three bindings, derived. Order is deliberate: a caller missing an
  -- accepted portfolio learns about that first rather than being told only about
  -- the unimplemented Gate Zero reader, and the coverage binding is read LAST so
  -- that deleting the Gate Zero throw cannot SKIP it -- the coverage record is
  -- checked on the way to every acceptance, not only on the ones a bound Gate
  -- Zero would have let through anyway. Once the Gate Zero record lands, this
  -- clause PASSES for an attested review and refuses an unattested or outgrown
  -- one; it is not a second gate held shut behind the first. All three refusals
  -- are terminal.
  v_portfolio := ops.benchmark_portfolio_prerequisite(p_portfolio_ref);
  v_gate_zero := ops.benchmark_gate_zero_outcome();
  v_coverage := ops.benchmark_measurement_coverage_binding(p_review_id);

  insert into ops.benchmark_manifest_acceptance_receipt(
    draft_id, benchmark_ref, idempotency_key, gate_id, producer_step_ref, status,
    accepted_payload_digest, review_id,
    portfolio_ref, portfolio_revision_id, portfolio_accepted_digest, portfolio_accepted_at,
    gate_zero_step_ref, gate_zero_outcome_digest, gate_zero_observed_at,
    accepted_by_actor_id)
  values (p_draft_id, v_benchmark_ref, p_idempotency_key,
    'benchmark-contract-accepted',
    'step:benchmark-contract-human-exact-hash-acceptance-receipt', 'accepted',
    p_accepted_payload_digest, p_review_id,
    p_portfolio_ref,
    (v_portfolio ->> 'portfolio_revision_id')::uuid,
    v_portfolio ->> 'portfolio_accepted_digest',
    (v_portfolio ->> 'portfolio_accepted_at')::timestamptz,
    v_gate_zero ->> 'step_ref',
    v_gate_zero ->> 'outcome_digest',
    (v_gate_zero ->> 'observed_at')::timestamptz,
    v_actor)
  returning id into v_id;
  return v_id;
end;
$$;

comment on function ops.benchmark_accept_manifest_draft(uuid,uuid,text,uuid,text) is
  'The only way to accept a benchmark manifest draft. The acceptor, the portfolio binding, the Gate Zero outcome and the measurement coverage proof are all derived, never parameters. It takes FOR UPDATE on the draft row, which the content freeze trigger''s FOR SHARE conflicts with. It cannot succeed unless BOTH a current passing Gate Zero outcome record and a measurement coverage attestation exist here; either one missing still leaves it refusing.';

-- ---------------------------------------------------------------------------
-- Grants. Reads reach the ordinary bundles. DIRECT INSERT IS GRANTED TO NOBODY:
-- every write goes through a definer function that derives its own actor, so a
-- writer holding a raw connection cannot attribute a row to someone else.
--
-- No role is created by this file. Every role named below already exists.
-- ---------------------------------------------------------------------------
grant select on ops.benchmark_manifest_draft, ops.benchmark_manifest_dimension,
  ops.benchmark_manifest_workload, ops.benchmark_manifest_request_size,
  ops.benchmark_manifest_concurrency, ops.benchmark_manifest_browser,
  ops.benchmark_manifest_evaluator, ops.benchmark_manifest_review,
  ops.benchmark_measurement_coverage_attestation,
  ops.benchmark_manifest_acceptance_receipt to carr_reader, carr_writer, carr_authority;

-- THE GRANT HALF OF APPEND-ONLY, AND IT DOES NOT BIND THE OWNER. TRUNCATE cannot
-- be revoked from the table owner, and the row-level append-only trigger never
-- sees a TRUNCATE at all; the statement-level trigger installed beside it above
-- is the half that closes that. Both halves are kept.
revoke insert, update, delete, truncate on ops.benchmark_manifest_draft,
  ops.benchmark_manifest_dimension, ops.benchmark_manifest_workload,
  ops.benchmark_manifest_request_size, ops.benchmark_manifest_concurrency,
  ops.benchmark_manifest_browser, ops.benchmark_manifest_evaluator,
  ops.benchmark_manifest_review, ops.benchmark_measurement_coverage_attestation,
  ops.benchmark_manifest_acceptance_receipt
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function ops.benchmark_payload_domain_tag(),
  ops.benchmark_utf16_length(text),
  ops.benchmark_assert_bound(text,text,anyelement,anyelement),
  ops.benchmark_coverage_evaluators(),
  ops.benchmark_slo_thresholds(), ops.benchmark_cost_variance_thresholds(),
  ops.benchmark_deadline_contract(), ops.benchmark_dimension_array(uuid,text),
  ops.benchmark_payload_preimage(uuid), ops.benchmark_payload_digest(uuid),
  ops.benchmark_draft_structure_error(uuid), ops.benchmark_draft_structure_valid(uuid),
  ops.benchmark_draft_integrity_error(uuid), ops.benchmark_current_accepted_draft(text),
  ops.benchmark_accepted_draft(text), ops.benchmark_portfolio_prerequisite(text),
  ops.benchmark_readback(text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
-- The two helpers are pure and expose nothing: one measures a string the way
-- JavaScript measures it, the other is a fail-closed equality. They are granted
-- with the rest of the readers so a reviewer can exercise them directly.
grant execute on function ops.benchmark_payload_domain_tag(),
  ops.benchmark_utf16_length(text),
  ops.benchmark_assert_bound(text,text,anyelement,anyelement),
  ops.benchmark_coverage_evaluators(),
  ops.benchmark_slo_thresholds(), ops.benchmark_cost_variance_thresholds(),
  ops.benchmark_deadline_contract(), ops.benchmark_dimension_array(uuid,text),
  ops.benchmark_payload_preimage(uuid), ops.benchmark_payload_digest(uuid),
  ops.benchmark_draft_structure_error(uuid), ops.benchmark_draft_structure_valid(uuid),
  ops.benchmark_draft_integrity_error(uuid), ops.benchmark_current_accepted_draft(text),
  ops.benchmark_accepted_draft(text), ops.benchmark_portfolio_prerequisite(text),
  ops.benchmark_readback(text)
  to carr_reader, carr_writer, carr_jobs, carr_authority;

-- THE TWO PRIVATE READERS ARE GRANTED TO NOBODY. Not to carr_authority, and not
-- to carr_reader: exposing the Gate Zero stub would turn "this record layer
-- cannot authenticate a Gate Zero outcome" into a callable public claim about
-- Gate Zero, and a callable stub is the first step toward a configurable one.
-- The coverage binding reader is private for the same reason. The definer write
-- path above reaches both as the function owner, which is the only access they
-- need.
revoke all on function ops.benchmark_gate_zero_outcome(),
  ops.benchmark_measurement_coverage_binding(uuid)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function
  ops.benchmark_propose_manifest_draft(text,integer,uuid,text,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb),
  ops.benchmark_review_manifest_draft(uuid,uuid,text,text,text,text,jsonb),
  ops.benchmark_accept_manifest_draft(uuid,uuid,text,uuid,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function
  ops.benchmark_propose_manifest_draft(text,integer,uuid,text,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb),
  ops.benchmark_review_manifest_draft(uuid,uuid,text,text,text,text,jsonb)
  to carr_writer, carr_authority;
-- Acceptance reaches the authority bundle only.
grant execute on function ops.benchmark_accept_manifest_draft(uuid,uuid,text,uuid,text)
  to carr_authority;
