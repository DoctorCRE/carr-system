-- DoctorCRE v5 slice V5-M01: the durable append-only ADMITTED-MINIMUM INPUT
-- HISTORY the Journey 1 clock projection's `minimum_history` is read from
-- (requirement Q008, decision Q008.D1; gate foundation-assurance-minimum-accepted).
--
-- CANDIDATE SQL. This file is source, not a migration. It carries no migration
-- ledger preflight, is not listed in public.schema_migrations, creates no role,
-- creates no schema, and is not applied to Production by anything in this slice.
-- Landing it as a numbered migration is a separate, Joe-gated act. IT HAS NEVER
-- BEEN EXECUTED.
--
-- PREREQUISITES, exact rather than implied. This file is FRESH-OR-EXACTLY-
-- COMPATIBLE: every relation is `create table if not exists` with no ALTER and
-- no backfill anywhere. It reuses, read-only and without restating:
--   * ops.j1_clock_scope_digest(jsonb), ops.j1_clock_origin_gate_id() and
--     ops.j1_clock_assert_same(...) from ops/journey-one-clock-store.candidate.sql
--     -- THE ONE scope derivation. A second scope key here would file one
--     program's inputs and one program's clock under two different addresses,
--     and the uniqueness both rails rely on would silently hold over two sets.
--     That file is also candidate source and has never been applied, so this
--     one cannot be applied before it.
--   * ops.portfolio_canonical_json(jsonb) from migration 0496 -- the ONE
--     canonicalizer, already reconciled against artifact-trust.js's canonicalJson.
--   * ops.portfolio_writer_actor_id() -- the ONE server-established writer.
--   * ops.benchmark_utf16_length(text) from ops/benchmark-acceptance.candidate.sql
--     -- the ONE home of "the length JavaScript String#length would report".
--     Its name is historical, like ops.portfolio_writer_actor_id()'s; its
--     contract is general. The comparator bound this rail enforces is stated by
--     the kernel in UTF-16 code units, and char_length() counts CODEPOINTS,
--     which differs on text above U+FFFF in both directions -- so a rail-local
--     copy would be a second counter that agrees today and drifts after one
--     edit. That file is also candidate source and has never been applied, so
--     this one cannot be applied before it either.
--   * public.digest(bytea, text) from pgcrypto, and public.actor.
-- Every object created below is namespaced `ops.j1_minimum_*`. Nothing outside
-- that prefix is created, altered or dropped.
--
-- ---------------------------------------------------------------------------
-- WHAT THIS IS. journey-one-clock-store.candidate.sql stores what the kernel
-- COMPUTES. This file stores what the kernel READS: the inventory of admitted
-- foundation-assurance-minimum receipts whose FIRST admission the kernel selects
-- an origin from.
--
-- THE ADMISSION INSTANT IS THE WHOLE POINT, AND IT IS DERIVED HERE.
-- journey-one-clock.v5.js selects the origin by ADMISSION ORDER because "an
-- append-only inventory can only ever gain LATER admissions", and warns that
-- selecting on observed_at instead "would let that ordinary admission rebase a
-- running clock, which is not a repairable state". That guarantee is this
-- file's to provide:
--   * `admitted_at` comes from ops.j1_minimum_admission_instant(), which is
--     now() -- the TRANSACTION timestamp -- rendered in the kernel's instant
--     grammar. A caller may name it; ops.j1_minimum_append_admission only ever
--     compares the named value against the one it derives, and because now() is
--     stable within a transaction the two are one reading rather than two.
--   * The ledger is stored IN THE KERNEL'S OWN SELECTION ORDER -- admitted_at
--     non-decreasing, receipt digest strictly increasing within one admitted_at
--     group -- and an append only ever extends it. The kernel orders candidates
--     by (admitted_at, receipt_digest) and takes the first ELIGIBLE one, so a
--     row that sorts after every stored row cannot be preferred to any of them
--     and cannot take the origin from the row that already has it. Comparing
--     against the FIRST ROW would not do it: that row may be an inadmissible
--     attempt the kernel skips. This file computes no eligibility.
--
-- IT IS NOT A SECOND RECEIPT VALIDATOR AND NOT A SECOND BENCHMARK. It checks
-- the FATAL-in-kernel bindings it can check from the accepted source bindings
-- this inventory was sealed under -- producer step, gate, role, oracle, scope,
-- environment manifest, window against the accepted policy, and the admission
-- instant against the receipt's own observation. It does not judge a receipt's
-- internal seat independence: that is enforced where the receipt is PROPOSED,
-- in benchmark-minimum.v5.js's join, and refused by name by the kernel.
--
-- WHAT IS REFUSED AND WHAT IS STORED. The kernel calls a non-passing receipt,
-- and one whose window has lapsed, "an ordinary fact of the ledger". Both are
-- STORED here. This rail has no filter and no discard path; the kernel decides
-- what may become an origin. Everything the kernel treats as FATAL is refused
-- at admission, because one such row makes every later evaluation of the
-- inventory refuse and an append-only inventory cannot shed it.
--
-- ---------------------------------------------------------------------------
-- WHAT A DIRECT SQL WRITER CAN AND CANNOT DO, SAID PLAINLY.
--
-- Direct INSERT is granted to nobody. Every write goes through
-- ops.j1_minimum_append_admission(), which derives its own actor, its own
-- admission instant and every digest. But that function is reachable by any
-- holder of the writer bundle, and a holder who calls it directly can STORE AN
-- ASSERTION: a receipt object it composed itself rather than one an authenticated
-- producer issued. This database cannot tell the two apart and does not pretend
-- to. No column here is named verified, accepted, admissible or issued;
-- `input_authority` is pinned by a CHECK to the single value
-- `trusted_admission_not_independently_verified_by_this_record_layer`, and
-- ops.j1_minimum_record_layer_cannot_prove() says so on every readback.
--
-- WHAT THE DATABASE *DOES* PROVE, independently of any writer: that a stored
-- receipt rebuilds to the digest it was admitted under; that the admission
-- instant was this database's own and is at or after the receipt's observation;
-- that one authoritative scope holds one inventory whose accepted policy and
-- environment manifest were sealed at its first admission; that the chain names
-- the exact head; that no receipt was admitted twice; that one idempotency key
-- carries one payload; that the stored sequence is in the kernel's own
-- (admitted_at, receipt_digest) selection order -- checked on write against the
-- head and again over the whole sequence on read -- so no append can displace
-- the row the kernel already selected as the origin; and that update, delete and
-- truncate are refused everywhere.
--
-- ---------------------------------------------------------------------------
-- THE INVARIANT IDS BELOW ARE SHARED WITH THE MODULE ON PURPOSE.
-- mcp-server/src/journey-one-clock-input-store.v5.js exports
-- JOURNEY_ONE_MINIMUM_ADMISSION_INVARIANTS, one entry per rule, and every id in
-- it appears verbatim in a refusal message here. The unit suite reads this file
-- and asserts each id is present. That is a real, mechanical comparison between
-- two homes of one rule set. It does NOT prove this SQL is correct: nothing that
-- has never run can prove that. Both homes exist because neither can replace the
-- other -- a direct SQL writer never executes the JavaScript, and the module's
-- reference journal has no database.
--
-- ---------------------------------------------------------------------------
-- WHAT REMAINS INTEGRATION WORK, named rather than implied:
--   * Applying this file, and ops/journey-one-clock-store.candidate.sql and
--     ops/benchmark-acceptance.candidate.sql before it, as numbered migrations.
--     This file reuses one scope derivation and one UTF-16 counter from those
--     two rather than defining second copies, so their order is load-bearing.
--   * The ISSUANCE ADAPTER. benchmark-minimum.v5.js proposes a minimum receipt
--     and marks it proposed_not_issued; nothing issues one, so no genuine
--     artifact can reach this rail and no clock has been started.
--   * The GATE ZERO and BENCHMARK COVERAGE bindings. consumer-gate-receipt.v1
--     carries neither and M01's projection has no slot for either, so this file
--     deliberately stores neither: a column filled from a caller would mint the
--     binding rather than carry it. It stays an explicit blocker.
--   * Running mcp-server/test/journey-one-clock-input-store-postgres.sql. It has
--     never been executed.

-- ---------------------------------------------------------------------------
-- Shared derivations. Nothing here re-decides a rule another home owns.
-- ---------------------------------------------------------------------------

-- The domain tag one admission's chain link is derived under. It matches
-- JOURNEY_ONE_MINIMUM_ADMISSION_DOMAIN_TAG in the module exactly.
create or replace function ops.j1_minimum_admission_domain_tag()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'doctorcre:j1-minimum-admission:v1'::text $$;

comment on function ops.j1_minimum_admission_domain_tag() is
  'The domain tag under which one admitted-minimum chain link is derived. Matches JOURNEY_ONE_MINIMUM_ADMISSION_DOMAIN_TAG in mcp-server/src/journey-one-clock-input-store.v5.js.';

-- The producer contract this inventory admits, restated here for the same reason
-- the clock rail restates its gate ids: so a receipt from another producer
-- cannot be stored at all. Each value is benchmark-minimum.v5.js's own constant.
create or replace function ops.j1_minimum_receipt_schema()
returns text language sql immutable set search_path = pg_catalog
as $$ select 'consumer-gate-receipt.v1'::text $$;

create or replace function ops.j1_minimum_producer_step_ref()
returns text language sql immutable set search_path = pg_catalog
as $$ select 'step:foundation-assurance-minimum-receipt'::text $$;

create or replace function ops.j1_minimum_producer_role()
returns text language sql immutable set search_path = pg_catalog
as $$ select 'independent_foundation_assurance_minimum_oracle'::text $$;

create or replace function ops.j1_minimum_oracle_ref()
returns text language sql immutable set search_path = pg_catalog
as $$ select 'oracle:gate-producer:foundation-assurance-minimum'::text $$;

create or replace function ops.j1_minimum_oracle_version()
returns text language sql immutable set search_path = pg_catalog
as $$ select '1.0.0'::text $$;

create or replace function ops.j1_minimum_evidence_scope()
returns text language sql immutable set search_path = pg_catalog
as $$ select 'candidate-and-test'::text $$;

create or replace function ops.j1_minimum_subject_environment()
returns text language sql immutable set search_path = pg_catalog
as $$ select 'candidate'::text $$;

-- The twenty-one r7 fields of consumer-gate-receipt.v1, C-sorted so a reviewer
-- can check them against CONSUMER_GATE_RECEIPT_FIELDS by eye. The set is closed:
-- an extra field would hash to an object the kernel never reads, and a missing
-- one would hash to a shorter object rather than refusing.
create or replace function ops.j1_minimum_receipt_fields()
returns text[] language sql immutable set search_path = pg_catalog
as $$ select array[
  'candidate_digest','comparator','environment_manifest_digest','evaluator_identity',
  'evidence_ref','evidence_scope','fixture_set_digest','gate_id','independent_oracle_ref',
  'negative_admission_result','observed_at','oracle_version','policy_digest',
  'producer_identity','producer_role','receipt_producer_step_ref','status',
  'subject_digest','subject_environment','subject_maker_identity','ttl_expires_at']::text[] $$;

comment on function ops.j1_minimum_receipt_fields() is
  'The closed twenty-one field set of consumer-gate-receipt.v1, matching CONSUMER_GATE_RECEIPT_FIELDS in mcp-server/src/benchmark-minimum.v5.js. Neither home declares a schema_version: the r7 schema does not have one, and the producer step ref is the discriminator.';

-- THE RECEIPT IDENTITY. sha256 over the canonical serialization of the receipt
-- itself, with NO domain tag -- because that is exactly what the kernel computes
-- (`digest(admission.receipt)`) and what it seals as origin_receipt_digest. A
-- domain tag here would give one artifact two identities and leave a durable
-- store unable to find the origin the clock names.
create or replace function ops.j1_minimum_receipt_digest(p_receipt jsonb)
returns text language sql stable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(p_receipt), 'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.j1_minimum_receipt_digest(jsonb) is
  'The identity of one admitted minimum receipt: sha256 over the canonical serialization of the artifact, untagged, exactly as journey-one-clock.v5.js computes digest(admission.receipt) and seals it as origin_receipt_digest.';

-- ONE ADMISSION'S CHAIN LINK. The sealed accepted policy and environment are in
-- the preimage on purpose: they are the accepted source bindings the receipt was
-- admitted under, and hashing them into every link means neither can be revised
-- retroactively without the whole chain refusing to rebuild.
-- The key order is irrelevant to the hash -- ops.portfolio_canonical_json sorts
-- object keys -- and is written C-sorted anyway so a reviewer can check it
-- against JOURNEY_ONE_MINIMUM_ADMISSION_FIELDS by eye.
create or replace function ops.j1_minimum_admission_digest(
  p_admitted_at text, p_clock_scope_key text, p_minimum_environment_manifest_digest text,
  p_minimum_receipt_ttl_policy_ms bigint, p_previous_admission_digest text,
  p_receipt_digest text, p_tenant text)
returns text language sql stable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(jsonb_build_array(
      ops.j1_minimum_admission_domain_tag(),
      jsonb_build_object(
        'admitted_at', p_admitted_at,
        'clock_scope_key', p_clock_scope_key,
        'minimum_environment_manifest_digest', p_minimum_environment_manifest_digest,
        'minimum_receipt_ttl_policy_ms', p_minimum_receipt_ttl_policy_ms,
        'previous_admission_digest', to_jsonb(p_previous_admission_digest),
        'receipt_digest', p_receipt_digest,
        'tenant', p_tenant))),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.j1_minimum_admission_digest(text,text,text,bigint,text,text,text) is
  'One admitted-minimum chain link: sha256 over the canonical [domain tag, the seven declared fields]. Each link hashes the link before it, so a row cannot be removed, reordered or re-dated without every later link failing to rebuild.';

-- THE SERVER ADMISSION INSTANT, AND THE ONLY PLACE IT COMES FROM.
-- now() is the TRANSACTION timestamp and is therefore stable for the whole
-- transaction: the journal reads this value, hands it to the module's checks,
-- and the append function below re-derives the SAME value and refuses a supplied
-- one that differs. That is one reading compared against itself, not two
-- readings hoped to be close. clock_timestamp() would be two.
create or replace function ops.j1_minimum_admission_instant()
returns text language sql stable
set search_path = pg_catalog
as $$ select to_char(now() at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') $$;

comment on function ops.j1_minimum_admission_instant() is
  'The record layer''s own admission instant, in the kernel''s instant grammar, derived from now() -- the transaction timestamp -- so every reading of it inside one transaction is the same reading. A caller never supplies this value; ops.j1_minimum_append_admission only ever compares one against it.';

-- What this record layer cannot prove about a stored admission. Carried on every
-- readback. It mirrors JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE in the module.
create or replace function ops.j1_minimum_record_layer_cannot_prove()
returns jsonb language sql immutable
set search_path = pg_catalog
as $$
  select jsonb_build_array(
    'that an admitted artifact is a receipt a real independent foundation-assurance-minimum oracle issued: no issuance adapter exists, benchmark-minimum.v5.js proposes a receipt and marks it proposed_not_issued, and a trusted writer''s composed object is indistinguishable here from a genuine one',
    'that the three seats inside an admitted receipt are independent of one another and of the subject''s maker: that is enforced where the receipt is proposed and refused by name by the kernel, and this rail owns no second copy',
    'that the identities inside a receipt are live authenticated seats rather than strings',
    'that the Gate Zero outcome and the benchmark coverage fact the join consumed were bound to this receipt: consumer-gate-receipt.v1 has no field for either and this rail stores neither',
    'that the accepted scope and accepted minimum policy an inventory was opened under are the ones a verifier accepted for any projection: both are trusted bindings, compared and sealed, never verified',
    'that a row written by a direct holder of the writer bundle is a genuine admission rather than that writer''s assertion',
    'anything about a deadline. An admitted row is an INPUT the kernel may read: it starts no clock, accepts no benchmark and grants no gate')
$$;

comment on function ops.j1_minimum_record_layer_cannot_prove() is
  'The explicit list of things a stored admitted-minimum row does NOT prove. Returned on every readback so a stored receipt is never read as an admitted gate.';

-- ---------------------------------------------------------------------------
-- The inventory: one per authoritative clock scope, opened once, never rewritten.
--
-- The scope key is DERIVED by ops.j1_clock_scope_digest from the scope object
-- itself -- the clock rail's function, not a copy -- so the inputs and the clock
-- of one program are addressed by one key. A key accepted on trust would be a
-- caller-chosen address wearing a hash.
-- ---------------------------------------------------------------------------
create table if not exists ops.j1_minimum_inventory (
  id                       uuid primary key default gen_random_uuid(),
  clock_scope_key          text not null unique
                             check (clock_scope_key ~ '^sha256:[0-9a-f]{64}$'),
  -- The scope as it was supplied, kept whole so a reader can see WHICH accepted
  -- scope was named. It is evidence of what was claimed, never that it was true.
  clock_scope              jsonb not null,
  -- PROVENANCE, NOT IDENTITY. The label is excluded from the scope key, so one
  -- accepted scope under two names is one inventory; it is recorded once here
  -- and a later write may not replace it.
  clock_scope_ref          text not null check (clock_scope_ref ~ '^safe:[A-Za-z0-9:._/-]{3,290}$'),
  tenant                   text not null check (tenant = 'carr-internal'),
  -- THE ACCEPTED SOURCE BINDINGS, SEALED AT THE FIRST ADMISSION. The kernel
  -- refuses a later projection that changes the minimum TTL policy
  -- (origin_ttl_policy_changed) because a different policy would admit or skip a
  -- different set of attempts and could name a different first pass. Sealing it
  -- here refuses that at the storage boundary instead.
  minimum_receipt_ttl_policy_ms bigint not null
                             check (minimum_receipt_ttl_policy_ms > 0
                                    and minimum_receipt_ttl_policy_ms <= 9007199254740991),
  minimum_environment_manifest_digest text not null
                             check (minimum_environment_manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  opened_by_actor_id       uuid not null references public.actor(id),
  opened_at                timestamptz not null default now()
);

comment on table ops.j1_minimum_inventory is
  'The admitted-minimum input inventory of one authoritative Journey 1 clock scope, addressed by the scope key ops.j1_clock_scope_digest derives from the accepted scope''s six identity fields. Opened once and never rewritten; the accepted TTL policy and environment manifest are sealed at the first admission and every later admission is written under them.';

-- ---------------------------------------------------------------------------
-- The admission: one append-only row per receipt this record layer admitted.
-- ---------------------------------------------------------------------------
create table if not exists ops.j1_minimum_admission (
  id                       uuid primary key default gen_random_uuid(),
  inventory_id             uuid not null references ops.j1_minimum_inventory(id),
  admission_ordinal        integer not null check (admission_ordinal >= 0),
  idempotency_key          uuid not null unique,
  -- THE COMPARE-AND-SWAP TOKEN. NULL opens the inventory and is admissible
  -- exactly once, which the partial unique index below enforces structurally.
  prior_admission_digest   text check (prior_admission_digest ~ '^sha256:[0-9a-f]{64}$'),
  admission_digest         text not null unique
                             check (admission_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- THE ARTIFACT AND ITS IDENTITY. The receipt is stored whole; the digest is
  -- recomputed from it on every read, so a caller may name one and it is only
  -- ever the loser of that comparison.
  receipt                  jsonb not null,
  receipt_digest           text not null check (receipt_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- THE RECORD LAYER'S OWN ADMISSION INSTANT, stored as exact text in the
  -- kernel's grammar because the projection carries it verbatim.
  admitted_at              text not null
                             check (admitted_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  -- Extracted columns. A PROJECTION OF THE ARTIFACT AND NEVER A SECOND SOURCE OF
  -- TRUTH: the guard asserts each equals the receipt's own value, so a row whose
  -- columns disagree with its receipt is a row somebody edited.
  gate_id                  text not null,
  receipt_producer_step_ref text not null,
  observed_at              text not null
                             check (observed_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  ttl_expires_at           text not null
                             check (ttl_expires_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  -- STORED VERBATIM AND NEVER FILTERED ON, and deliberately WITHOUT an enum
  -- CHECK: the legal statuses are consumer-gate-receipt.v1's and are enforced
  -- where the receipt is validated, so a list here would be a second home for
  -- one closed set. The kernel calls a non-passing receipt an ordinary fact of
  -- the ledger, and this rail has no discard path.
  status                   text not null,
  -- The sealed accepted bindings this row was admitted under, denormalized so
  -- the chain link can be recomputed from the row alone.
  minimum_receipt_ttl_policy_ms bigint not null check (minimum_receipt_ttl_policy_ms > 0),
  minimum_environment_manifest_digest text not null
                             check (minimum_environment_manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- PROVENANCE IS PINNED, NOT COPIED. source_ref NAMES where the artifact came
  -- from and is the only free field; naming is not proving.
  receipt_schema_ref       text not null check (receipt_schema_ref = 'consumer-gate-receipt.v1'),
  source_ref               text not null check (source_ref ~ '^safe:[A-Za-z0-9:._/-]{3,290}$'),
  input_authority          text not null check (input_authority =
                             'trusted_admission_not_independently_verified_by_this_record_layer'),
  written_by_actor_id      uuid not null references public.actor(id),
  recorded_at              timestamptz not null default now(),
  -- ONE ARTIFACT IS ADMITTED ONCE PER INVENTORY. Re-presenting it under a fresh
  -- instant is a replay of evidence, not a second admission.
  unique (inventory_id, receipt_digest),
  unique (inventory_id, admission_ordinal)
);

comment on table ops.j1_minimum_admission is
  'One admitted foundation-assurance-minimum receipt: the artifact, the record layer''s own admission instant, the accepted bindings it was admitted under and a hash-chained link to the admission before it. Append-only. It records an INPUT the kernel may read; it admits nothing to a gate, accepts no benchmark and starts no clock.';

-- AT MOST ONE OPENING PER INVENTORY. A plain unique (inventory_id,
-- prior_admission_digest) would not do it: NULL is distinct from NULL in a
-- unique index, so two openings would both be admitted.
create unique index if not exists j1_minimum_admission_one_opening
  on ops.j1_minimum_admission (inventory_id)
  where prior_admission_digest is null;

create index if not exists j1_minimum_admission_by_inventory
  on ops.j1_minimum_admission (inventory_id, admission_ordinal desc);

-- ---------------------------------------------------------------------------
-- APPEND-ONLY. There is no reset, no re-dating and no replacement on this rail.
--
-- TWO TRIGGERS PER RELATION, because UPDATE/DELETE and TRUNCATE are different
-- events. A ROW-LEVEL TRIGGER NEVER SEES TRUNCATE -- it is a statement event --
-- so a row-level-only posture would leave "an admitted attempt cannot be erased"
-- true of every runtime bundle and FALSE of the table owner, from whom TRUNCATE
-- cannot be revoked. `revoke ... truncate` below is the grant half and does not
-- bind the owner; this is the half that does. Same shape as
-- ops/model-role-store.candidate.sql, ops/cre-lifecycle.candidate.sql and
-- ops/document-derivative-registration.candidate.sql, which is the house
-- pattern rather than a new one invented here.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_minimum_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception '[j1_minimum_rows_are_append_only] Journey 1 minimum-input rows are append-only: % is refused on ops.%',
    tg_op, tg_table_name using errcode = '42501';
end;
$$;

comment on function ops.j1_minimum_rows_immutable() is
  'Refuses every update, delete and truncate on the admitted-minimum storage tables. Carries the shared invariant id j1_minimum_rows_are_append_only. It is installed twice per relation because a row-level trigger never sees TRUNCATE, and TRUNCATE cannot be revoked from the table owner.';

do $$
declare t text;
begin
  foreach t in array array['j1_minimum_inventory', 'j1_minimum_admission'] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_append_only', t);
    execute format(
      'create trigger %I before update or delete on ops.%I for each row execute function ops.j1_minimum_rows_immutable()',
      t || '_append_only', t);
    -- TRUNCATE is statement-level and BEFORE-only; there is no row to see.
    execute format('drop trigger if exists %I on ops.%I', t || '_no_truncate', t);
    execute format(
      'create trigger %I before truncate on ops.%I for each statement execute function ops.j1_minimum_rows_immutable()',
      t || '_no_truncate', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Serialization. One inventory's admissions are appended one at a time.
--
-- The advisory seed is 2 so a minimum-inventory key never shares a slot with the
-- clock rail's clock keys (seed 0) or scope keys (seed 1), even though both
-- rails address a scope by the same digest.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_minimum_lock(p_clock_scope_key text)
returns void language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
begin
  if p_clock_scope_key is null then
    raise exception '[j1_minimum_inventory_scope_bound] an admitted-minimum append must name the authoritative scope it is writing for';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_clock_scope_key, 2));
  perform 1 from ops.j1_minimum_inventory where clock_scope_key = p_clock_scope_key for update;
end;
$$;

comment on function ops.j1_minimum_lock(text) is
  'Opens the serialized same-inventory section for the calling transaction: a transaction-scoped advisory lock on the scope key (which serializes concurrent OPENINGS, which have no row yet) plus FOR UPDATE on the inventory row when it exists.';

-- ---------------------------------------------------------------------------
-- Readers. Each returns jsonb in the exact shape the module's postgres journal
-- expects, and each recomputes rather than reporting a stored claim.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_minimum_inventory_row(p_clock_scope_key text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
           'clock_scope_key', i.clock_scope_key,
           'clock_scope_ref', i.clock_scope_ref,
           'scope', i.clock_scope,
           'tenant', i.tenant,
           'minimum_receipt_ttl_policy_ms', i.minimum_receipt_ttl_policy_ms,
           'minimum_environment_manifest_digest', i.minimum_environment_manifest_digest,
           'opened_at', i.opened_at)
    from ops.j1_minimum_inventory i where i.clock_scope_key = p_clock_scope_key
$$;

create or replace function ops.j1_minimum_admission_json(p_admission_id uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
           'clock_scope_key', i.clock_scope_key,
           'clock_scope_ref', i.clock_scope_ref,
           'tenant', i.tenant,
           'admission_ordinal', a.admission_ordinal,
           'admitted_at', a.admitted_at,
           'receipt', a.receipt,
           'receipt_digest', a.receipt_digest,
           -- RECOMPUTED, not echoed: a reader is told what the stored artifact
           -- hashes to now, beside the digest it was admitted under.
           'recomputed_receipt_digest', ops.j1_minimum_receipt_digest(a.receipt),
           'gate_id', a.gate_id,
           'receipt_producer_step_ref', a.receipt_producer_step_ref,
           'observed_at', a.observed_at,
           'ttl_expires_at', a.ttl_expires_at,
           'status', a.status,
           'minimum_receipt_ttl_policy_ms', a.minimum_receipt_ttl_policy_ms,
           'minimum_environment_manifest_digest', a.minimum_environment_manifest_digest,
           'previous_admission_digest', a.prior_admission_digest,
           'admission_digest', a.admission_digest,
           'recorded_at', a.recorded_at,
           'written_by_actor_id', (select ac.slug from public.actor ac where ac.id = a.written_by_actor_id),
           -- FIVE KEYS WHERE THE MODULE'S PROVENANCE HAS SIX, and the missing one
           -- is deliberate. admitted_by_authority_class is derived by identity.js
           -- from the LIVE actor; this database has no such derivation, and a
           -- column filled from a caller -- or back-derived from a stored row --
           -- would be exactly the caller boolean the whole rail exists to
           -- prevent. The actor slug agrees on both sides.
           'provenance', jsonb_build_object(
             'receipt_schema_ref', a.receipt_schema_ref,
             'receipt_producer_step_ref', a.receipt_producer_step_ref,
             'source_ref', a.source_ref,
             'input_authority', a.input_authority,
             'admitted_by_actor_id', (select ac.slug from public.actor ac where ac.id = a.written_by_actor_id)))
    from ops.j1_minimum_admission a
    join ops.j1_minimum_inventory i on i.id = a.inventory_id
   where a.id = p_admission_id
$$;

comment on function ops.j1_minimum_admission_json(uuid) is
  'One admitted-minimum row in the shape createPostgresJourneyOneMinimumAdmissionJournal consumes, with the receipt digest RECOMPUTED beside the one it was admitted under. Its provenance carries five of the module''s six keys: admitted_by_authority_class is derived from the live actor by identity.js and this database derives no authority class, so it is absent rather than invented.';

create or replace function ops.j1_minimum_head(p_clock_scope_key text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select ops.j1_minimum_admission_json(a.id)
    from ops.j1_minimum_admission a
    join ops.j1_minimum_inventory i on i.id = a.inventory_id
   where i.clock_scope_key = p_clock_scope_key
   order by a.admission_ordinal desc
   limit 1
$$;

create or replace function ops.j1_minimum_admissions(p_clock_scope_key text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select coalesce(jsonb_agg(ops.j1_minimum_admission_json(a.id) order by a.admission_ordinal), '[]'::jsonb)
    from ops.j1_minimum_admission a
    join ops.j1_minimum_inventory i on i.id = a.inventory_id
   where i.clock_scope_key = p_clock_scope_key
$$;

create or replace function ops.j1_minimum_admission_by_idempotency_key(p_key uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select ops.j1_minimum_admission_json(a.id)
    from ops.j1_minimum_admission a where a.idempotency_key = p_key
$$;

comment on function ops.j1_minimum_admissions(text) is
  'One inventory''s admissions in admission order, each with its receipt, the digest it was admitted under and the digest that receipt hashes to now. The kernel''s minimum_history is [{admitted_at, receipt}] read straight off this list.';

-- THE MINIMUM_HISTORY THE KERNEL READS, assembled from the stored rows and
-- nothing else. It is the exact closed {admitted_at, receipt} shape
-- journey-one-clock.v5.js checks, and it is emitted in admission order.
--
-- IT REFUSES RATHER THAN SERVING A SHORTER INVENTORY. A row whose stored receipt
-- no longer hashes to the digest it was admitted under is not skipped: a
-- silently dropped attempt is exactly the discard this rail must not make, and
-- dropping the FIRST one would move the origin.
--
-- IT ANSWERS IN THE MODULE'S OWN READBACK SHAPE. The two homes are read side by
-- side by anyone comparing them, so the schema version, the top-level tenant and
-- the two sealed policy fields are here under the names the module's read()
-- uses, and the exists:false branch carries the module's exists:false key set
-- exactly -- six keys, `effects` among them and `gate_admitted_by_record_layer`
-- deliberately not, because an inventory that does not exist has admitted
-- nothing to say `false` about. The scope and its label stay nested under
-- `inventory` on the exists:true branch, because that is the row this function
-- actually read.
create or replace function ops.j1_minimum_history(p_clock_scope_key text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_row record; v_previous text := null; v_ordinal integer := 0; v_history jsonb := '[]'::jsonb;
  v_last_at timestamptz := null; v_last_digest text := null;
  v_inventory ops.j1_minimum_inventory%rowtype;
begin
  select * into v_inventory from ops.j1_minimum_inventory where clock_scope_key = p_clock_scope_key;
  if not found then
    return jsonb_build_object(
      'schema_version', 'doctorcre-v5-journey-one-minimum-inventory-readback.v1',
      'clock_scope_key', p_clock_scope_key,
      'tenant', 'carr-internal',
      'exists', false,
      'record_layer_cannot_prove', ops.j1_minimum_record_layer_cannot_prove(),
      -- THE MODULE'S OWN exists:false KEY SET, EXACTLY. read() returns
      -- {schema_version, clock_scope_key, tenant, exists, record_layer_cannot_
      -- prove, effects} and nothing else -- no gate_admitted_by_record_layer,
      -- because there is no inventory to say anything about. An earlier revision
      -- carried that key here and no `effects`, so five of the six agreed while
      -- the header claimed the branch matched; a claimed parity that is off by
      -- one field is worse than no claim, because it is the reason nobody checks.
      'effects', jsonb_build_object(
        'creates_effect', false, 'database_writes', 0, 'network_calls', 0,
        'provider_actions', 0, 'notifications', 0, 'schedules', 0,
        'deployments', 0, 'activations', 0, 'acceptances', 0));
  end if;
  for v_row in
    select a.* from ops.j1_minimum_admission a
      join ops.j1_minimum_inventory i on i.id = a.inventory_id
     where i.clock_scope_key = p_clock_scope_key
     order by a.admission_ordinal
  loop
    if v_row.admission_ordinal <> v_ordinal then
      raise exception '[j1_minimum_content_rebuilds_to_its_digest] admitted-minimum ordinals are not contiguous from zero at position %', v_ordinal;
    end if;
    if ops.j1_minimum_receipt_digest(v_row.receipt) is distinct from v_row.receipt_digest then
      raise exception '[j1_minimum_content_rebuilds_to_its_digest] admission % no longer hashes to the digest it was admitted under', v_ordinal;
    end if;
    if v_row.prior_admission_digest is distinct from v_previous
       or ops.j1_minimum_admission_digest(v_row.admitted_at, p_clock_scope_key,
            v_row.minimum_environment_manifest_digest, v_row.minimum_receipt_ttl_policy_ms,
            v_previous, v_row.receipt_digest, 'carr-internal')
          is distinct from v_row.admission_digest then
      raise exception '[j1_minimum_exact_prior_admission_digest] admission % does not link to the admission before it', v_ordinal;
    end if;
    -- THE SELECTION ORDER, RE-CHECKED ON READ RATHER THAN ASSUMED. The guard
    -- compares a newcomer against the head only, which is sound by induction;
    -- this validates the whole sequence, because the induction is exactly what
    -- a row inserted by some other route would break. Serving an out-of-order
    -- inventory would hand the kernel an origin this rail never admitted first.
    -- COLLATE "C" IS THE PARITY, NOT A PREFERENCE. The JavaScript home compares
    -- these digests with UTF-16 code units; a database default collation is
    -- locale-dependent, and two homes that agree by coincidence of locale are
    -- two homes that can disagree after one initdb. Byte order is the rule on
    -- both sides, so it is pinned here and in the guard rather than inherited.
    if v_last_at is not null
       and (v_row.admitted_at::timestamptz < v_last_at
            or (v_row.admitted_at::timestamptz = v_last_at
                and v_row.receipt_digest collate "C" <= v_last_digest)) then
      raise exception '[j1_minimum_first_origin_never_replaced] admission % does not sort after the one it follows; a stored inventory is in the kernel''s own (admitted_at, receipt_digest) selection order',
        v_ordinal;
    end if;
    v_last_at := v_row.admitted_at::timestamptz;
    v_last_digest := v_row.receipt_digest;
    v_history := v_history || jsonb_build_array(jsonb_build_object(
      'admitted_at', v_row.admitted_at, 'receipt', v_row.receipt));
    v_previous := v_row.admission_digest;
    v_ordinal := v_ordinal + 1;
  end loop;
  if v_ordinal = 0 then
    raise exception '[j1_minimum_content_rebuilds_to_its_digest] inventory % exists with no admissions', p_clock_scope_key;
  end if;
  return jsonb_build_object(
    'schema_version', 'doctorcre-v5-journey-one-minimum-inventory-readback.v1',
    'clock_scope_key', p_clock_scope_key,
    'tenant', v_inventory.tenant,
    'exists', true,
    'clock_scope_ref', v_inventory.clock_scope_ref,
    'clock_scope', v_inventory.clock_scope,
    'minimum_receipt_ttl_policy_ms', v_inventory.minimum_receipt_ttl_policy_ms,
    'minimum_environment_manifest_digest', v_inventory.minimum_environment_manifest_digest,
    'inventory', ops.j1_minimum_inventory_row(p_clock_scope_key),
    'admission_count', v_ordinal,
    'head_admission_digest', v_previous,
    'minimum_history', v_history,
    'admissions', ops.j1_minimum_admissions(p_clock_scope_key),
    'record_layer_cannot_prove', ops.j1_minimum_record_layer_cannot_prove(),
    'gate_admitted_by_record_layer', false,
    'effects', jsonb_build_object(
      'creates_effect', false, 'database_writes', 0, 'network_calls', 0,
      'provider_actions', 0, 'notifications', 0, 'schedules', 0,
      'deployments', 0, 'activations', 0, 'acceptances', 0));
end;
$$;

comment on function ops.j1_minimum_history(text) is
  'Deterministic zero-write readback of one authoritative scope''s admitted-minimum inventory, with the kernel''s own minimum_history assembled from the stored rows. Every receipt is re-hashed, every chain link recomputed, and the whole sequence re-validated against the kernel''s (admitted_at, receipt_digest) selection order; a tampered or out-of-order row refuses rather than being skipped, because a dropped attempt is a discard and a row out of that order could hand the kernel an origin this rail never admitted first.';

-- ---------------------------------------------------------------------------
-- OPENING AN INVENTORY. Its own act, idempotent for the exact binding, and the
-- point at which a second policy or a second label for one scope is refused.
-- The scope key is DERIVED here by the clock rail's own function.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_minimum_open_inventory(
  p_clock_scope jsonb, p_minimum_receipt_ttl_policy_ms bigint,
  p_minimum_environment_manifest_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_key text; v_existing ops.j1_minimum_inventory%rowtype;
begin
  -- ONE SCOPE DERIVATION, AND IT IS THE CLOCK RAIL'S. A second one here would
  -- address one program's inputs and one program's clock under two keys.
  v_key := ops.j1_clock_scope_digest(p_clock_scope);
  if p_minimum_receipt_ttl_policy_ms is null or p_minimum_receipt_ttl_policy_ms <= 0 then
    raise exception '[j1_minimum_inventory_policy_sealed] an admitted-minimum inventory needs the accepted maximum minimum-receipt TTL it admits under';
  end if;
  if coalesce(p_minimum_environment_manifest_digest, '') !~ '^sha256:[0-9a-f]{64}$' then
    raise exception '[j1_minimum_inventory_policy_sealed] an admitted-minimum inventory needs the accepted minimum environment manifest digest it admits under';
  end if;
  perform ops.j1_minimum_lock(v_key);

  select * into v_existing from ops.j1_minimum_inventory where clock_scope_key = v_key;
  if found then
    if v_existing.minimum_receipt_ttl_policy_ms is distinct from p_minimum_receipt_ttl_policy_ms
       or v_existing.minimum_environment_manifest_digest
          is distinct from p_minimum_environment_manifest_digest then
      raise exception '[j1_minimum_inventory_policy_sealed] admitted-minimum inventory % was opened under TTL policy % and environment %, and % / % was supplied. The accepted policy is sealed at the first admission and is never applied to rows it did not judge',
        v_key, v_existing.minimum_receipt_ttl_policy_ms,
        v_existing.minimum_environment_manifest_digest,
        p_minimum_receipt_ttl_policy_ms, p_minimum_environment_manifest_digest;
    end if;
    -- THE LABEL IS SEALED AT OPENING. It is not identity -- v_key ignores it,
    -- which is what makes a relabelled scope the SAME scope -- and precisely
    -- because it is not identity the record must not hold two names for one
    -- scope or silently replace the one it was opened under.
    if v_existing.clock_scope_ref is distinct from (p_clock_scope ->> 'scope_ref') then
      raise exception '[j1_minimum_scope_label_is_not_identity] admitted-minimum inventory % was opened under label %, and % was supplied. The label is provenance, is recorded once, and is never rewritten by a later write',
        v_key, v_existing.clock_scope_ref, coalesce(p_clock_scope ->> 'scope_ref', 'null');
    end if;
    return ops.j1_minimum_inventory_row(v_key);
  end if;

  insert into ops.j1_minimum_inventory(
    clock_scope_key, clock_scope, clock_scope_ref, tenant,
    minimum_receipt_ttl_policy_ms, minimum_environment_manifest_digest, opened_by_actor_id)
  values (v_key, p_clock_scope, p_clock_scope ->> 'scope_ref', p_clock_scope ->> 'tenant',
    p_minimum_receipt_ttl_policy_ms, p_minimum_environment_manifest_digest,
    ops.portfolio_writer_actor_id());
  return ops.j1_minimum_inventory_row(v_key);
end;
$$;

comment on function ops.j1_minimum_open_inventory(jsonb,bigint,text) is
  'Open the admitted-minimum inventory of one authoritative clock scope, deriving the scope key with the clock rail''s own ops.j1_clock_scope_digest. Idempotent for the exact binding; refuses a changed accepted policy, a changed environment manifest and a second label. It proves nothing about whether the supplied scope or policy is any projection''s accepted one.';

-- ---------------------------------------------------------------------------
-- THE APPEND GUARD. Everything an admission depends on is checked HERE, from the
-- persisted rows and the artifact itself, so a handler bug -- or a direct caller
-- who never runs the JavaScript at all -- cannot land a row that breaks an
-- invariant. A BEFORE INSERT trigger suffices: an admission has no child rows,
-- so there is nothing to defer past.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_minimum_append_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  v_inventory ops.j1_minimum_inventory%rowtype;
  v_head ops.j1_minimum_admission%rowtype;
  v_field text; v_seat_field text; v_keys integer;
  v_observed timestamptz; v_expires timestamptz; v_admitted timestamptz;
begin
  select * into v_inventory from ops.j1_minimum_inventory where id = new.inventory_id;
  if not found then
    raise exception '[j1_minimum_inventory_scope_bound] an admitted-minimum row names an unknown inventory';
  end if;
  -- TENANT. An admission is stored under the tenant its inventory was opened with.
  if v_inventory.tenant <> 'carr-internal' then
    raise exception '[j1_minimum_tenant_bound] admitted-minimum inventory % is not this tenant''s', v_inventory.clock_scope_key;
  end if;

  -- ATTRIBUTION. The writer is the server-established one, never a payload
  -- field. This is the record layer's own rule and carries no shared invariant
  -- id: on the JavaScript side the writer is derived from the live actor before
  -- a store can be constructed at all.
  if new.written_by_actor_id <> ops.portfolio_writer_actor_id() then
    raise exception 'admitted-minimum row actor does not match the authenticated writer context';
  end if;

  -- THE ARTIFACT IS A CLOSED consumer-gate-receipt.v1 FROM THIS PRODUCER.
  if jsonb_typeof(new.receipt) <> 'object' then
    raise exception '[j1_minimum_receipt_producer_bound] an admitted minimum receipt is a json object';
  end if;
  select count(*) into v_keys from jsonb_object_keys(new.receipt);
  if v_keys <> 21 then
    raise exception '[j1_minimum_receipt_producer_bound] an admitted minimum receipt carries exactly the twenty-one declared fields of %', ops.j1_minimum_receipt_schema();
  end if;
  foreach v_field in array ops.j1_minimum_receipt_fields() loop
    if not (new.receipt ? v_field) then
      raise exception '[j1_minimum_receipt_producer_bound] an admitted minimum receipt is missing %', v_field;
    end if;
  end loop;
  if (new.receipt ->> 'gate_id') is distinct from ops.j1_clock_origin_gate_id()
     or (new.receipt ->> 'receipt_producer_step_ref') is distinct from ops.j1_minimum_producer_step_ref()
     or (new.receipt ->> 'producer_role') is distinct from ops.j1_minimum_producer_role()
     or (new.receipt ->> 'independent_oracle_ref') is distinct from ops.j1_minimum_oracle_ref()
     or (new.receipt ->> 'oracle_version') is distinct from ops.j1_minimum_oracle_version()
     or (new.receipt ->> 'evidence_scope') is distinct from ops.j1_minimum_evidence_scope()
     or (new.receipt ->> 'subject_environment') is distinct from ops.j1_minimum_subject_environment() then
    raise exception '[j1_minimum_receipt_producer_bound] this inventory admits the % receipt its own producer step issues, and nothing else; the kernel refuses another producer fatally and an append-only inventory could never shed the row',
      ops.j1_clock_origin_gate_id();
  end if;

  -- THE REMAINING FATAL-IN-KERNEL SHAPE FACTS, mirroring the module's clauses in
  -- journeyOneMinimumAdmissionView. Each is fatal in the kernel rather than
  -- skipped -- invalid_reference, invalid_identity, invalid_digest,
  -- invalid_comparator -- so one admitted row would make every later evaluation
  -- of this inventory throw, and an append-only inventory cannot shed it.
  --
  -- IT IS SHAPE, NOT ELIGIBILITY AND NOT SEAT AUTHORITY. No independence between
  -- the seats is judged here and no authority class is derived: that belongs to
  -- the join that PROPOSES a receipt, which is the only seat holding the live
  -- identities, and it is disclosed in ops.j1_minimum_record_layer_cannot_prove().
  -- TYPE FIRST, THEN VALUE, AND THE TYPE TEST IS NULL-AWARE.
  --
  -- `->>` RENDERS a jsonb number, boolean or null as text, so a regex or a
  -- length applied to it silently judges a value the JavaScript home refuses
  -- outright: the numeric comparator 12345 becomes '12345' and passes a bare
  -- length test, and a numeric actor_id becomes a non-empty string. That is a
  -- BOTH-homes invariant admitting on one side what it refuses on the other, so
  -- every field below is type-checked against jsonb_typeof before its value is
  -- read. IS DISTINCT FROM is the null-aware form: an absent key yields NULL,
  -- and `if NULL then` does not fire, which would fail open on exactly the
  -- missing field.
  --
  -- The bounds are the KERNEL'S, counted the kernel's way: its ref() tests the
  -- prefix and then the WHOLE string against [A-Za-z0-9:._/-]{3,300}, so the
  -- five-character prefix leaves 295 and the eight-character one leaves 292.
  if jsonb_typeof(new.receipt -> 'evidence_ref') is distinct from 'string'
     or coalesce(new.receipt ->> 'evidence_ref', '') !~ '^safe:[A-Za-z0-9:._/-]{0,295}$' then
    raise exception '[j1_minimum_receipt_readable_by_kernel] receipt.evidence_ref is a json string holding a safe: reference the kernel can read';
  end if;
  if jsonb_typeof(new.receipt -> 'fixture_set_digest') is distinct from 'string'
     or coalesce(new.receipt ->> 'fixture_set_digest', '') !~ '^sha256:[0-9a-f]{64}$' then
    raise exception '[j1_minimum_receipt_readable_by_kernel] receipt.fixture_set_digest is a json string holding a sha256 reference';
  end if;
  -- UTF-16 CODE UNITS, WHICH IS WHAT THE KERNEL COUNTS. char_length() counts
  -- CODEPOINTS, and the two differ on text above U+FFFF in BOTH directions: 151
  -- astral characters are 151 codepoints and 302 UTF-16 units, so a codepoint
  -- test admits a comparator the kernel refuses; three astral characters are 3
  -- codepoints and 6 UTF-16 units, so a codepoint test refuses one the kernel
  -- ACCEPTS. Narrowing the accepted domain is as wrong as widening it, so the
  -- count is exact rather than approximated in either direction.
  -- ops.benchmark_utf16_length() is the ONE home of that count -- its name is
  -- historical, its contract is "the length JavaScript String#length would
  -- report" -- and a rail-local copy would be a second counter that agrees today.
  -- The type test in front of it also keeps its STRICT null from failing open.
  if jsonb_typeof(new.receipt -> 'comparator') is distinct from 'string'
     or ops.benchmark_utf16_length(new.receipt ->> 'comparator') < 5
     or ops.benchmark_utf16_length(new.receipt ->> 'comparator') > 300 then
    raise exception '[j1_minimum_receipt_readable_by_kernel] receipt.comparator is a json string of between 5 and 300 UTF-16 code units';
  end if;
  -- THE SEATS. Written as nested statements rather than one OR chain because
  -- jsonb_object_keys ERRORS on a non-object and SQL does not promise to
  -- short-circuit an OR: the type test has to have already run.
  foreach v_field in array array[
    'evaluator_identity', 'producer_identity', 'subject_maker_identity'
  ] loop
    if jsonb_typeof(new.receipt -> v_field) is distinct from 'object' then
      raise exception '[j1_minimum_receipt_readable_by_kernel] receipt.% is a json object seat', v_field;
    end if;
    if (select count(*) from jsonb_object_keys(new.receipt -> v_field)) <> 3 then
      raise exception '[j1_minimum_receipt_readable_by_kernel] receipt.% carries exactly the three declared fields of an authenticated-receipt-identity.v1 seat',
        v_field;
    end if;
    -- Three names AND three string types: a seat with three keys under other
    -- names fails here rather than on the count, because a missing name yields
    -- NULL from jsonb_typeof and IS DISTINCT FROM refuses it.
    foreach v_seat_field in array array['actor_id', 'authority_class', 'session_ref'] loop
      if jsonb_typeof(new.receipt -> v_field -> v_seat_field) is distinct from 'string' then
        raise exception '[j1_minimum_receipt_readable_by_kernel] receipt.%.% is a json string', v_field, v_seat_field;
      end if;
      if (new.receipt -> v_field ->> v_seat_field) = '' then
        raise exception '[j1_minimum_receipt_readable_by_kernel] receipt.%.% is a non-empty string', v_field, v_seat_field;
      end if;
    end loop;
    if (new.receipt -> v_field ->> 'session_ref') !~ '^session:[A-Za-z0-9:._/-]{0,292}$' then
      raise exception '[j1_minimum_receipt_readable_by_kernel] receipt.%.session_ref is a session: reference the kernel can read',
        v_field;
    end if;
  end loop;

  -- THE ACCEPTED SCOPE DECIDES, NOT THE RECEIPT. The three digests come from the
  -- scope this inventory was opened under and the environment from its sealed
  -- policy, so a receipt for another subject is refused rather than quietly
  -- widening the inventory's own binding.
  --
  -- ops.j1_clock_assert_same is the clock rail's fail-closed comparison and is
  -- reused rather than copied: `a <> b` is NULL when either side is null and
  -- would fall through exactly when the value was never derived. Its message
  -- text says "clock revision" because that rail wrote it; the INVARIANT ID it
  -- raises is this rail's, and the id is what the two homes are compared on.
  perform ops.j1_clock_assert_same('j1_minimum_receipt_binds_accepted_scope', 'subject_digest',
    v_inventory.clock_scope ->> 'benchmark_subject_digest', new.receipt ->> 'subject_digest');
  perform ops.j1_clock_assert_same('j1_minimum_receipt_binds_accepted_scope', 'candidate_digest',
    v_inventory.clock_scope ->> 'benchmark_candidate_digest', new.receipt ->> 'candidate_digest');
  perform ops.j1_clock_assert_same('j1_minimum_receipt_binds_accepted_scope', 'policy_digest',
    v_inventory.clock_scope ->> 'benchmark_policy_digest', new.receipt ->> 'policy_digest');
  perform ops.j1_clock_assert_same('j1_minimum_receipt_binds_accepted_scope',
    'environment_manifest_digest', v_inventory.minimum_environment_manifest_digest,
    new.receipt ->> 'environment_manifest_digest');

  -- THE EXTRACTED COLUMNS ARE A PROJECTION OF THE ARTIFACT.
  perform ops.j1_clock_assert_same('j1_minimum_content_rebuilds_to_its_digest', 'gate_id',
    new.receipt ->> 'gate_id', new.gate_id);
  perform ops.j1_clock_assert_same('j1_minimum_content_rebuilds_to_its_digest',
    'receipt_producer_step_ref', new.receipt ->> 'receipt_producer_step_ref',
    new.receipt_producer_step_ref);
  perform ops.j1_clock_assert_same('j1_minimum_content_rebuilds_to_its_digest', 'observed_at',
    new.receipt ->> 'observed_at', new.observed_at);
  perform ops.j1_clock_assert_same('j1_minimum_content_rebuilds_to_its_digest', 'ttl_expires_at',
    new.receipt ->> 'ttl_expires_at', new.ttl_expires_at);
  perform ops.j1_clock_assert_same('j1_minimum_content_rebuilds_to_its_digest', 'status',
    new.receipt ->> 'status', new.status);

  -- CONTENT. The stored artifact hashes to the digest it is filed under.
  if ops.j1_minimum_receipt_digest(new.receipt) is distinct from new.receipt_digest then
    raise exception '[j1_minimum_content_rebuilds_to_its_digest] the stored receipt does not hash to the digest it is admitted under: computed %, filed %',
      ops.j1_minimum_receipt_digest(new.receipt), new.receipt_digest;
  end if;

  -- THE SEALED ACCEPTED POLICY travels on the row and must be the inventory's.
  perform ops.j1_clock_assert_same('j1_minimum_inventory_policy_sealed',
    'minimum_receipt_ttl_policy_ms', v_inventory.minimum_receipt_ttl_policy_ms,
    new.minimum_receipt_ttl_policy_ms);
  perform ops.j1_clock_assert_same('j1_minimum_inventory_policy_sealed',
    'minimum_environment_manifest_digest', v_inventory.minimum_environment_manifest_digest,
    new.minimum_environment_manifest_digest);

  -- THE WINDOW, against the accepted policy. An overlong window is a misissued
  -- receipt or a misbound policy, which the kernel refuses FATALLY rather than
  -- skipping, so it can never be stored here.
  v_observed := new.observed_at::timestamptz;
  v_expires := new.ttl_expires_at::timestamptz;
  v_admitted := new.admitted_at::timestamptz;
  if v_expires <= v_observed then
    raise exception '[j1_minimum_receipt_window_within_accepted_policy] a receipt''s ttl_expires_at is after its observed_at';
  end if;
  if extract(epoch from (v_expires - v_observed)) * 1000
     > new.minimum_receipt_ttl_policy_ms then
    raise exception '[j1_minimum_receipt_window_within_accepted_policy] the receipt''s window exceeds the accepted maximum of % ms this inventory was opened under',
      new.minimum_receipt_ttl_policy_ms;
  end if;

  -- THE ADMISSION INSTANT IS THIS DATABASE'S OWN, AND IT IS BOUND TO THE
  -- ARTIFACT'S OBSERVATION WITHOUT SKEW.
  if new.admitted_at is distinct from ops.j1_minimum_admission_instant() then
    raise exception '[j1_minimum_admission_instant_is_server_time] admitted_at is stamped by this record layer and is never a caller field: derived %, supplied %',
      ops.j1_minimum_admission_instant(), new.admitted_at;
  end if;
  if v_observed > v_admitted then
    raise exception '[j1_minimum_admission_not_before_observation] this receipt reports being observed at %, after the instant % the record layer admitted it. A single instant of skew is fatal to every later evaluation of the inventory and an append-only inventory cannot shed the row',
      new.observed_at, new.admitted_at;
  end if;

  -- ORDER AND THE ORIGIN. THE LEDGER IS STORED IN THE KERNEL'S OWN SELECTION
  -- ORDER AND AN APPEND ONLY EVER EXTENDS IT: admitted_at non-decreasing, and
  -- STRICTLY INCREASING receipt digest within one admitted_at group.
  --
  -- The kernel orders candidates by (admitted_at, receipt_digest) and takes the
  -- FIRST ELIGIBLE one, skipping every inadmissible attempt, so the row it
  -- selected is not necessarily the first row in this ledger. Comparing a
  -- newcomer against the FIRST ROW is therefore the wrong comparison: with a
  -- failed attempt at T0 and a passing one at T1, the origin is the T1 row, and
  -- a second T1 row with a lower digest ties nothing at T0 while taking the
  -- origin away from the row that had it -- which surfaces on the next
  -- evaluation as origin_reset_or_rebase against a retained history and leaves
  -- the clock unreadable rather than wrong.
  --
  -- Keeping the stored sequence in that same total order closes it without this
  -- database computing eligibility, which is the kernel's and stays the
  -- kernel's: a new row sorts after every stored row, so it cannot be preferred
  -- to any of them, whichever ones were eligible. The head alone is enough to
  -- compare against because it is the maximum of its own group under this
  -- invariant, and ops.j1_minimum_history re-checks the whole sequence rather
  -- than trusting the induction.
  select * into v_head from ops.j1_minimum_admission
    where inventory_id = new.inventory_id and id <> new.id
    order by admission_ordinal desc limit 1;
  if found then
    if new.prior_admission_digest is distinct from v_head.admission_digest then
      raise exception '[j1_minimum_exact_prior_admission_digest] the prior admission digest is not the current head: head %, supplied %',
        v_head.admission_digest, coalesce(new.prior_admission_digest, 'null');
    end if;
    if new.admission_ordinal <> v_head.admission_ordinal + 1 then
      raise exception '[j1_minimum_content_rebuilds_to_its_digest] admitted-minimum ordinals are not contiguous: head %, supplied %',
        v_head.admission_ordinal, new.admission_ordinal;
    end if;
    if v_admitted < v_head.admitted_at::timestamptz then
      raise exception '[j1_minimum_first_origin_never_replaced] an append-only admission inventory only ever gains LATER admissions; this one is dated % and the head is %',
        new.admitted_at, v_head.admitted_at;
    end if;
    if new.recorded_at < v_head.recorded_at then
      raise exception '[j1_minimum_admission_instant_is_server_time] the server clock moved backwards between two admissions of one inventory';
    end if;
    -- COLLATE "C": byte order, matching the JavaScript home's UTF-16 code-unit
    -- comparison, rather than whatever collation this database happens to default
    -- to. See the same pin in ops.j1_minimum_history.
    if v_admitted = v_head.admitted_at::timestamptz
       and new.receipt_digest collate "C" <= v_head.receipt_digest then
      raise exception '[j1_minimum_first_origin_never_replaced] this admission shares the head''s admission instant % and does not sort after it (head digest %, supplied %), so the kernel could prefer it to a row already stored in that group -- including the eligible row it has already selected as the origin. Within one admission instant the receipt digest strictly increases, because that is the order the kernel selects in',
        v_head.admitted_at, v_head.receipt_digest, new.receipt_digest;
    end if;
  else
    if new.prior_admission_digest is not null then
      raise exception '[j1_minimum_exact_prior_admission_digest] this inventory holds no admissions, so there is no prior digest to match; open it with an explicit null prior';
    end if;
    if new.admission_ordinal <> 0 then
      raise exception '[j1_minimum_content_rebuilds_to_its_digest] the opening admission of an inventory is ordinal 0';
    end if;
  end if;

  -- THE CHAIN LINK, recomputed rather than accepted.
  if ops.j1_minimum_admission_digest(new.admitted_at, v_inventory.clock_scope_key,
       new.minimum_environment_manifest_digest, new.minimum_receipt_ttl_policy_ms,
       new.prior_admission_digest, new.receipt_digest, v_inventory.tenant)
     is distinct from new.admission_digest then
    raise exception '[j1_minimum_claimed_digest_is_never_trusted] the named admission digest is not the one this row produces';
  end if;

  return new;
end;
$$;

comment on function ops.j1_minimum_append_guard() is
  'Everything an admitted-minimum row depends on, checked from the persisted rows and the artifact itself: the closed receipt shape and its producer, the accepted scope and sealed policy, the recomputed receipt digest and chain link, the server admission instant and its binding to the receipt''s observation, and the append-only ordering that keeps the stored sequence in the kernel''s own (admitted_at, receipt_digest) selection order, so no append can displace the eligible row the kernel already selected as the origin. It judges no receipt''s internal seat independence, computes no eligibility, and filters nothing: a non-passing or lapsed receipt is an ordinary fact of the ledger and is stored.';

drop trigger if exists j1_minimum_admission_guard on ops.j1_minimum_admission;
create trigger j1_minimum_admission_guard
  before insert on ops.j1_minimum_admission
  for each row execute function ops.j1_minimum_append_guard();

-- ---------------------------------------------------------------------------
-- THE ONLY WRITE PATH. It derives its own actor, its own admission instant and
-- every digest, and accepts none of them.
--
-- IDEMPOTENCY IS A REPLAY, NOT A SECOND WRITE. The key is looked up first: an
-- exact replay returns the row that already exists, and the same key presented
-- with different content is refused rather than quietly writing a second row.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_minimum_append_admission(
  p_clock_scope_key text, p_tenant text, p_idempotency_key uuid,
  p_prior_admission_digest text, p_admitted_at text, p_receipt_digest text,
  p_receipt jsonb, p_provenance jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_existing ops.j1_minimum_admission%rowtype;
  v_inventory ops.j1_minimum_inventory%rowtype;
  v_actor uuid; v_admission uuid; v_ordinal integer;
  v_derived text; v_at text; v_link text;
begin
  if p_idempotency_key is null then
    raise exception '[j1_minimum_idempotency_key_binds_its_payload] an admitted-minimum append must carry an idempotency key';
  end if;
  select * into v_existing from ops.j1_minimum_admission where idempotency_key = p_idempotency_key;
  if found then
    -- EVERY BINDING PARAMETER IS COMPARED. A replay matching on the artifact but
    -- naming a different inventory or prior is a different request in one key.
    if (select i.clock_scope_key from ops.j1_minimum_inventory i where i.id = v_existing.inventory_id)
         is distinct from p_clock_scope_key
       or v_existing.receipt_digest is distinct from p_receipt_digest
       or v_existing.prior_admission_digest is distinct from p_prior_admission_digest then
      raise exception '[j1_minimum_idempotency_key_binds_its_payload] idempotency key % was already used for a different minimum admission',
        p_idempotency_key;
    end if;
    return ops.j1_minimum_admission_json(v_existing.id) || jsonb_build_object('replayed', true);
  end if;

  v_actor := ops.portfolio_writer_actor_id();
  perform ops.j1_minimum_lock(p_clock_scope_key);

  select * into v_inventory from ops.j1_minimum_inventory where clock_scope_key = p_clock_scope_key;
  if not found then
    raise exception '[j1_minimum_inventory_scope_bound] authoritative clock scope % holds no admitted-minimum inventory; open it with ops.j1_minimum_open_inventory before admitting',
      p_clock_scope_key;
  end if;
  if v_inventory.tenant is distinct from p_tenant then
    raise exception '[j1_minimum_tenant_bound] admitted-minimum inventory % belongs to another tenant', p_clock_scope_key;
  end if;

  -- THE ARTIFACT'S IDENTITY IS DERIVED, NEVER ACCEPTED. A caller may name one;
  -- it is only ever compared against the one the artifact produces.
  v_derived := ops.j1_minimum_receipt_digest(p_receipt);
  if p_receipt_digest is distinct from v_derived then
    raise exception '[j1_minimum_claimed_digest_is_never_trusted] the named receipt digest is not the one this artifact produces: named %, computed %',
      coalesce(p_receipt_digest, 'null'), v_derived;
  end if;
  if exists (select 1 from ops.j1_minimum_admission
              where inventory_id = v_inventory.id and receipt_digest = v_derived) then
    raise exception '[j1_minimum_receipt_never_readmitted] receipt % is already in this inventory; re-presenting one artifact under a new admission instant is a replay, not a second admission',
      v_derived;
  end if;

  -- AND SO IS THE ADMISSION INSTANT. now() is the transaction timestamp, so the
  -- value the caller read from ops.j1_minimum_admission_instant() in this same
  -- transaction is bit-for-bit this one; a differing value is a caller trying to
  -- date its own admission.
  v_at := ops.j1_minimum_admission_instant();
  if p_admitted_at is not null and p_admitted_at is distinct from v_at then
    raise exception '[j1_minimum_admission_instant_is_server_time] admitted_at is stamped by this record layer and is never a caller field: derived %, supplied %',
      v_at, p_admitted_at;
  end if;

  select coalesce(max(admission_ordinal), -1) + 1 into v_ordinal
    from ops.j1_minimum_admission where inventory_id = v_inventory.id;

  v_link := ops.j1_minimum_admission_digest(v_at, v_inventory.clock_scope_key,
    v_inventory.minimum_environment_manifest_digest, v_inventory.minimum_receipt_ttl_policy_ms,
    p_prior_admission_digest, v_derived, v_inventory.tenant);

  insert into ops.j1_minimum_admission(
    inventory_id, admission_ordinal, idempotency_key, prior_admission_digest, admission_digest,
    receipt, receipt_digest, admitted_at, gate_id, receipt_producer_step_ref,
    observed_at, ttl_expires_at, status,
    minimum_receipt_ttl_policy_ms, minimum_environment_manifest_digest,
    receipt_schema_ref, source_ref, input_authority, written_by_actor_id)
  values (v_inventory.id, v_ordinal, p_idempotency_key, p_prior_admission_digest, v_link,
    p_receipt, v_derived, v_at,
    p_receipt ->> 'gate_id', p_receipt ->> 'receipt_producer_step_ref',
    p_receipt ->> 'observed_at', p_receipt ->> 'ttl_expires_at', p_receipt ->> 'status',
    v_inventory.minimum_receipt_ttl_policy_ms, v_inventory.minimum_environment_manifest_digest,
    -- PROVENANCE IS PINNED, NOT COPIED. The schema ref and input_authority are
    -- CHECK-constrained to single values so a direct writer cannot widen them
    -- into a claim of verification; source_ref NAMES the origin of the artifact.
    ops.j1_minimum_receipt_schema(), p_provenance ->> 'source_ref',
    'trusted_admission_not_independently_verified_by_this_record_layer', v_actor)
  returning id into v_admission;

  return ops.j1_minimum_admission_json(v_admission) || jsonb_build_object('replayed', false);
end;
$$;

comment on function ops.j1_minimum_append_admission(text,text,uuid,text,text,text,jsonb,jsonb) is
  'The only way to admit a foundation-assurance-minimum receipt to an inventory. The writer, the admission instant, the receipt digest and the chain link are all DERIVED; the caller supplies the artifact, an idempotency key, a source reference and the exact prior chain digest it believes is the head. It replays an exact idempotent repeat and refuses a key whose payload changed. It records an INPUT with scoped provenance: it admits nothing to a gate, verifies no artifact, accepts no benchmark and starts no clock.';

-- ---------------------------------------------------------------------------
-- Grants. Reads reach the ordinary bundles. DIRECT INSERT IS GRANTED TO NOBODY:
-- every write goes through the definer functions above, which derive their own
-- actor, so a writer holding a raw connection cannot attribute an admission to
-- someone else, date it itself, or step around the guard.
--
-- No role is created by this file. Every role named below already exists.
-- ---------------------------------------------------------------------------
grant select on ops.j1_minimum_inventory, ops.j1_minimum_admission
  to carr_reader, carr_writer, carr_authority;

revoke insert, update, delete, truncate on ops.j1_minimum_inventory, ops.j1_minimum_admission
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function
  ops.j1_minimum_admission_domain_tag(), ops.j1_minimum_receipt_schema(),
  ops.j1_minimum_producer_step_ref(), ops.j1_minimum_producer_role(),
  ops.j1_minimum_oracle_ref(), ops.j1_minimum_oracle_version(),
  ops.j1_minimum_evidence_scope(), ops.j1_minimum_subject_environment(),
  ops.j1_minimum_receipt_fields(), ops.j1_minimum_receipt_digest(jsonb),
  ops.j1_minimum_admission_digest(text,text,text,bigint,text,text,text),
  ops.j1_minimum_admission_instant(), ops.j1_minimum_record_layer_cannot_prove(),
  ops.j1_minimum_inventory_row(text), ops.j1_minimum_admission_json(uuid),
  ops.j1_minimum_head(text), ops.j1_minimum_admissions(text),
  ops.j1_minimum_admission_by_idempotency_key(uuid), ops.j1_minimum_history(text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

grant execute on function
  ops.j1_minimum_admission_domain_tag(), ops.j1_minimum_receipt_schema(),
  ops.j1_minimum_producer_step_ref(), ops.j1_minimum_producer_role(),
  ops.j1_minimum_oracle_ref(), ops.j1_minimum_oracle_version(),
  ops.j1_minimum_evidence_scope(), ops.j1_minimum_subject_environment(),
  ops.j1_minimum_receipt_fields(), ops.j1_minimum_receipt_digest(jsonb),
  ops.j1_minimum_admission_digest(text,text,text,bigint,text,text,text),
  ops.j1_minimum_admission_instant(), ops.j1_minimum_record_layer_cannot_prove(),
  ops.j1_minimum_inventory_row(text), ops.j1_minimum_admission_json(uuid),
  ops.j1_minimum_head(text), ops.j1_minimum_admissions(text),
  ops.j1_minimum_admission_by_idempotency_key(uuid), ops.j1_minimum_history(text)
  to carr_reader, carr_writer, carr_authority;

-- The lock opener, the inventory opener and the append reach the writer bundle
-- only. There is NO authority-only verb on this rail and no humanOnly gate:
-- recording an input is ordinary trusted-writer work, and pretending it were a
-- partner act would invent an authority this slice does not hold.
revoke all on function ops.j1_minimum_lock(text),
  ops.j1_minimum_open_inventory(jsonb,bigint,text),
  ops.j1_minimum_append_admission(text,text,uuid,text,text,text,jsonb,jsonb)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

grant execute on function ops.j1_minimum_lock(text),
  ops.j1_minimum_open_inventory(jsonb,bigint,text),
  ops.j1_minimum_append_admission(text,text,uuid,text,text,text,jsonb,jsonb)
  to carr_writer, carr_authority;
