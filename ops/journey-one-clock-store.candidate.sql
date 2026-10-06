-- DoctorCRE v5 slice V5-M01: durable append-only storage for the Journey 1
-- clock history, with exact prior-history compare-and-swap (requirement Q008,
-- decision Q008.D1; gates journey-one-preactivation-contract-bound and
-- journey-one-production-accepted).
--
-- CANDIDATE SQL. This file is source, not a migration. It carries no migration
-- ledger preflight, is not listed in public.schema_migrations, creates no role,
-- creates no schema, and is not applied to Production by anything in this slice.
-- Landing it as a numbered migration is a separate, Joe-gated act. IT HAS NEVER
-- BEEN EXECUTED: the local initdb is blocked (shmget), and no remote database
-- was used as a workaround.
--
-- PREREQUISITES, exact rather than implied. This file is FRESH-OR-EXACTLY-
-- COMPATIBLE: every relation is `create table if not exists` with no ALTER and
-- no backfill anywhere, so applying it to a database that already carries these
-- relations changes no row. It reuses, read-only and without restating:
--   * ops.portfolio_canonical_json(jsonb) from migration 0496 -- the ONE
--     canonicalizer, already reconciled against artifact-trust.js's
--     canonicalJson. A clock-local copy would be a second canonicalization
--     authority, and two canonicalizers that agree today are two that can
--     disagree after one edit.
--   * ops.portfolio_writer_actor_id() -- the ONE server-established writer
--     derivation. Its name is historical; its contract is "the active actor the
--     server established for this transaction".
--   * public.digest(bytea, text) from pgcrypto, and public.actor.
-- Every object created below is namespaced `ops.j1_clock_*`. Nothing outside
-- that prefix is created, altered or dropped.
--
-- ---------------------------------------------------------------------------
-- WHAT THIS IS. mcp-server/src/journey-one-clock.v5.js is a pure kernel and says
-- so: "Returning a history does not claim it was persisted; the caller owns the
-- durable append", and "ANTI-ROLLBACK LIVES IN THAT ADAPTER, not here ... an
-- OLDER GENUINE history replays unless the durable store compare-and-swaps on
-- the exact prior history_digest and refuses a write whose prior does not match
-- the stored one." This file is the durable half of that adapter: the clock
-- state decomposed into typed rows, an append-only revision chain, and a
-- compare-and-swap that makes an older genuine history unwritable.
--
-- IT IS NOT A SECOND DEADLINE POLICY. No function here computes a deadline,
-- selects an origin, judges a receipt, counts a pause hour or decides a status.
-- The Chicago calendar rule, the 120-hour pre-approved blocker-pause union, the
-- sticky miss and the "late kernel receipt stays usable, deadline success is
-- never claimed after a recorded miss" reading all live in the kernel. This file
-- transports them and refuses to store a record that contradicts the one of
-- them Q008.D1 states outright.
--
-- ---------------------------------------------------------------------------
-- THE ROWS ARE THE RECORD, AND THE DIGEST IS RECOMPUTED FROM THEM. The
-- twenty-one fields of doctorcre-v5-journey-one-clock.v2 are stored as
-- eighteen scalar columns plus two ordered child relations (the pause intervals
-- and the event chain). ops.j1_clock_history_preimage() rebuilds the state from
-- those rows and ops.j1_clock_history_digest() hashes it, so the stored
-- history_digest is a statement about the PERSISTED history rather than about a
-- blob a caller once supplied. A caller may name a digest; it is only ever
-- compared against the one the rows produce.
--
-- EVERY HISTORY INSTANT IS STORED AS EXACT TEXT, NOT AS timestamptz, AND THAT IS
-- NOT AN OVERSIGHT. The kernel copies each pause interval's `ends_at` off the
-- projection WITHOUT normalizing it, so `...T00:00:00Z` and `...T00:00:00+00:00`
-- are two different stored values naming one instant, and only the exact one
-- reproduces the digest a partner's ledger carries. A round trip through
-- timestamptz would return a different STRING and therefore a different
-- history_digest. The one timestamptz on this rail is `recorded_at`, which is
-- SERVER TIME and belongs to the record layer, not to the history.
--
-- SERVER TIME AND KERNEL TIME ARE TWO COLUMNS AND NEVER SUBSTITUTE FOR EACH
-- OTHER. `evaluated_at` is the instant the KERNEL evaluated at -- it comes from
-- the verified projection's `as_of` and a caller therefore influences it.
-- `recorded_at` is `now()` in this database. A caller-chosen as_of is never this
-- record layer's clock, and no ordering rule below is decided by as_of alone.
--
-- ---------------------------------------------------------------------------
-- WHAT A DIRECT SQL WRITER CAN AND CANNOT DO, SAID PLAINLY, BECAUSE THE
-- TEMPTATION TO OVERSTATE IT IS THE WHOLE RISK ON THIS RAIL.
--
-- Direct INSERT is granted to nobody. Every write goes through
-- ops.j1_clock_append_revision(), which derives its own actor and enforces every
-- invariant from the persisted rows. But that function is reachable by any
-- holder of the writer bundle, and a holder who calls it directly can STORE AN
-- ASSERTION: a state object it composed itself rather than one the kernel
-- produced. This database cannot tell the two apart, and it does not pretend to.
--
-- WHAT THE DATABASE THEREFORE DOES *NOT* CLAIM about any stored revision:
--   * that the origin receipt was a genuine current passing
--     foundation-assurance-minimum receipt (there is no authenticated admission
--     ledger here, and admitted_at is a projection fact);
--   * that the recorded pauses were approved by a verified partner strictly
--     before they started, or were not backdated;
--   * that the terminus receipt was admitted under the accepted per-receipt TTL
--     policy against the accepted kernel scope;
--   * that the projection the kernel read was authentic;
--   * ANYTHING WHATEVER ABOUT DEADLINE SUCCESS. A stored `completed_on_time` is
--     a recorded computation. IT IS NOT AN ACCEPTANCE OF A DEADLINE BY THIS
--     RECORD LAYER, and ops.j1_clock_record_layer_cannot_prove() says so on
--     every readback. No column here is named verified, accepted, admitted or
--     deadline_success, and `input_authority` is pinned by a CHECK to the single
--     value `trusted_projection_not_independently_verified_by_this_record_layer`
--     so no writer can widen it.
--
-- WHAT THE DATABASE *DOES* PROVE, independently of any writer:
--   * the stored history rebuilds from its own rows to the digest it is filed
--     under, at commit and on every read;
--   * the clock a revision was appended to is the one its OWN PRESENTED ORIGIN
--     derives -- ops.j1_clock_identity_digest() over the tenant, the origin
--     receipt digest, the origin instant and the origin benchmark manifest
--     digest. A caller cannot pick a name; `clock_ref` is a legibility label
--     with no authority, is set once, and selects nothing. A caller inventing a
--     fresh alias for a clock that already has history gets a refusal, not a
--     fresh clock. THAT IS A STATEMENT ABOUT ALIASES AND ABOUT NOTHING ELSE: a
--     caller who presents a DIFFERENT origin derives a different key, and a
--     different key has no head, so its creation meets no compare-and-swap at
--     all. The scope binding below is what refuses that, and this file does not
--     claim the derived key ever did;
--   * that one AUTHORITATIVE CLOCK SCOPE holds at most one clock --
--     ops.j1_clock_scope_binding, keyed by a scope digest derived from the
--     accepted scope's own SIX IDENTITY FIELDS, unique on both sides. A second
--     origin presented for a scope that already names a clock is refused by name
--     and by constraint, and RENAMING THE SCOPE DOES NOT EVADE IT: the human
--     scope_ref label is stored and sealed but is not in the preimage, so one
--     accepted scope under two names is one key and meets the same constraint.
--     WHAT THE DATABASE CANNOT SAY ABOUT IT: that the supplied scope is the
--     accepted scope of the projection the kernel read. The stored state carries
--     no subject, candidate or policy digest, so there is nothing here to derive
--     it from; the scope is compared, never verified, and
--     ops.j1_clock_record_layer_cannot_prove() says so on every readback;
--   * the append named the EXACT current head, or an explicit NULL that succeeds
--     only against a clock with no revisions;
--   * the origin, the base deadline it produced and the completion seals are
--     identical in every revision; a recorded miss is never removed; the prior
--     event chain is an exact prefix of the next;
--   * one idempotency key carries one payload;
--   * update, delete and truncate are refused everywhere on this rail.
--
-- ---------------------------------------------------------------------------
-- THE INVARIANT IDS BELOW ARE SHARED WITH THE MODULE ON PURPOSE.
-- mcp-server/src/journey-one-clock-store.v5.js exports
-- JOURNEY_ONE_CLOCK_APPEND_INVARIANTS, one entry per rule, and every id in it
-- appears verbatim in a refusal message here. The unit suite reads this file and
-- asserts each id is present. That is a real, mechanical comparison between two
-- homes of one rule set -- it proves neither home dropped an entry. It does NOT
-- prove this SQL is correct: nothing that has never run can prove that, and this
-- file has never run. Both homes exist because neither can replace the other --
-- a direct SQL writer never executes the JavaScript, and the module's reference
-- journal has no database.
--
-- ---------------------------------------------------------------------------
-- WHAT REMAINS INTEGRATION WORK, named rather than implied:
--   * Applying this file as a numbered migration.
--   * The authenticated projection reader, the admitted-minimum ledger and the
--     terminus producer. Until they exist, the only writer of a KERNEL-PRODUCED
--     revision is trusted server code holding a real verifier; see
--     JOURNEY_ONE_CLOCK_INPUT_AUTHORITY_REQUIREMENT in the module.
--   * Running mcp-server/test/journey-one-clock-store-postgres.sql. It has never
--     been executed.

-- ---------------------------------------------------------------------------
-- Shared derivations. Nothing here re-decides a rule the kernel owns.
-- ---------------------------------------------------------------------------

-- The domain tag a clock's IDENTITY is derived under. It matches
-- JOURNEY_ONE_CLOCK_IDENTITY_DOMAIN_TAG in the module exactly.
create or replace function ops.j1_clock_identity_domain_tag()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'doctorcre:j1-clock-identity:v1'::text $$;

comment on function ops.j1_clock_identity_domain_tag() is
  'The domain tag under which a Journey 1 clock identity is derived. Matches JOURNEY_ONE_CLOCK_IDENTITY_DOMAIN_TAG in mcp-server/src/journey-one-clock-store.v5.js.';

-- The state schema this rail stores, and the one it refuses BY NAME.
create or replace function ops.j1_clock_state_schema()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'doctorcre-v5-journey-one-clock.v2'::text $$;

create or replace function ops.j1_clock_legacy_state_schemas()
returns text[] language sql immutable
set search_path = pg_catalog
as $$ select array['doctorcre-v5-journey-one-clock.v1']::text[] $$;

comment on function ops.j1_clock_legacy_state_schemas() is
  'State schemas this rail refuses by name rather than reinterpreting. v1 sealed its origin digest over a different minimum and carries neither the TTL policy that selected its origin nor the deadline resolution that produced its base deadline; re-deriving any of those would rebase a sealed origin. Migration is explicit and external, exactly as the kernel says.';

-- THE CLOCK IDENTITY. Derived from the PRESENTED origin, never from a name a
-- caller chose. It is the defence against a self-chosen alias restarting a
-- running clock -- two callers presenting one origin address one clock and
-- collide on the creation CAS -- and it is not a defence against a caller who
-- presents a different origin, which derives a different clock with no head to
-- collide with. ops.j1_clock_scope_binding is what refuses that one.
--
-- The key order below is irrelevant to the hash -- ops.portfolio_canonical_json
-- sorts object keys -- and is written C-sorted anyway so a reviewer can check it
-- against JOURNEY_ONE_CLOCK_IDENTITY_FIELDS by eye.
create or replace function ops.j1_clock_identity_digest(
  p_tenant text, p_origin_receipt_digest text, p_origin_at text,
  p_origin_benchmark_manifest_digest text)
returns text language sql stable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(jsonb_build_array(
      ops.j1_clock_identity_domain_tag(),
      jsonb_build_object(
        'origin_at', p_origin_at,
        'origin_benchmark_manifest_digest', p_origin_benchmark_manifest_digest,
        'origin_receipt_digest', p_origin_receipt_digest,
        'tenant', p_tenant))),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.j1_clock_identity_digest(text,text,text,text) is
  'The identity of one Journey 1 clock: sha256 over the canonical [domain_tag, {origin_at, origin_benchmark_manifest_digest, origin_receipt_digest, tenant}]. Derived from the kernel origin, never chosen by a caller.';

-- THE AUTHORITATIVE CLOCK SCOPE. The clock identity above answers "which
-- admitted receipt started this clock"; this answers "which program and subject
-- is it the clock FOR". They are separate because the origin is exactly the
-- thing a reset presents a new one of: an address derived only from the origin
-- cannot refuse a second clock, because the second clock has a different
-- address. The scope is stable across origins, so it can.
--
-- IT IS A TRUSTED BINDING AND NOT A DERIVATION FROM STORED FACTS. The two gate
-- ids are checked against the kernel's own contract, the tenant is checked, and
-- the three benchmark digests are checked for shape -- and that is the whole of
-- what this database can check, because the stored state carries none of them.
-- The scope OBJECT travels and the key is derived here; a key accepted on trust
-- would be a caller-chosen address wearing a hash.
--
-- THE HUMAN LABEL IS NOT IN THE PREIMAGE, and the tag is v2 because it once was.
-- Hashing scope_ref made one accepted scope -- same tenant, same three benchmark
-- digests, same gate ids -- addressable under as many keys as it had names, so
-- the uniqueness below held over LABELS rather than over scopes and a relabelled
-- caller could bind a second clock for one program. The preimage is now the six
-- identity fields; scope_ref is stored beside the key as provenance and is
-- sealed at binding. This file has never been applied, so the version bump
-- rewrites nothing and no backfill is proposed by it.
create or replace function ops.j1_clock_scope_domain_tag()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'doctorcre:j1-clock-scope:v2'::text $$;

comment on function ops.j1_clock_scope_domain_tag() is
  'The domain tag under which a Journey 1 authoritative clock scope is derived. Matches JOURNEY_ONE_CLOCK_SCOPE_DOMAIN_TAG in mcp-server/src/journey-one-clock-store.v5.js. v2 excludes the human scope_ref label from the preimage; v1 hashed it and made one accepted scope addressable under two keys.';

-- The two gate ids JOURNEY_ONE_DEADLINE_CONTRACT names, restated here for the
-- same reason the status and deadline-resolution lists are: so a scope naming
-- some other gate cannot be stored at all. Neither decides anything.
create or replace function ops.j1_clock_origin_gate_id()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'foundation-assurance-minimum-accepted'::text $$;

create or replace function ops.j1_clock_terminus_gate_id()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'journey-one-kernel-production-accepted'::text $$;

create or replace function ops.j1_clock_scope_digest(p_scope jsonb)
returns text language plpgsql stable
set search_path = pg_catalog, ops, public
as $$
declare v_field text; v_fields jsonb;
begin
  if p_scope is null or jsonb_typeof(p_scope) <> 'object' then
    raise exception '[j1_clock_scope_binds_one_clock] a Journey 1 clock scope binding must be a json object';
  end if;
  -- EXACTLY THE SEVEN DECLARED FIELDS. An extra one would hash to an object the
  -- module never computes, and a missing one would hash to a shorter object
  -- rather than refusing.
  if (select count(*) from jsonb_object_keys(p_scope)) <> 7 then
    raise exception '[j1_clock_scope_binds_one_clock] a Journey 1 clock scope binding carries exactly its seven declared fields';
  end if;
  if coalesce(p_scope ->> 'tenant', '') <> 'carr-internal' then
    raise exception '[j1_clock_tenant_bound] a Journey 1 clock scope binding names another tenant';
  end if;
  if coalesce(p_scope ->> 'clock_origin_gate_id', '') <> ops.j1_clock_origin_gate_id()
     or coalesce(p_scope ->> 'clock_terminus_gate_id', '') <> ops.j1_clock_terminus_gate_id() then
    raise exception '[j1_clock_scope_binds_one_clock] a Journey 1 clock scope binding names gates other than % and %, so it is not a Journey 1 clock scope',
      ops.j1_clock_origin_gate_id(), ops.j1_clock_terminus_gate_id();
  end if;
  if coalesce(p_scope ->> 'scope_ref', '') !~ '^safe:[A-Za-z0-9:._/-]{3,290}$' then
    raise exception '[j1_clock_scope_binds_one_clock] a Journey 1 clock scope binding needs a safe: scope_ref';
  end if;
  foreach v_field in array array['benchmark_candidate_digest', 'benchmark_policy_digest',
                                 'benchmark_subject_digest'] loop
    if coalesce(p_scope ->> v_field, '') !~ '^sha256:[0-9a-f]{64}$' then
      raise exception '[j1_clock_scope_binds_one_clock] a Journey 1 clock scope binding needs a sha256 %', v_field;
    end if;
  end loop;
  -- Rebuilt by name and C-sorted, so a reviewer can check it against
  -- JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS by eye. Key order is irrelevant to
  -- the hash. `scope_ref` is validated above and DELIBERATELY ABSENT here: a
  -- label a caller chose is not a fact about which program a clock belongs to,
  -- and hashing it would let one accepted scope hold one clock PER NAME.
  -- [j1_clock_scope_label_is_not_identity]
  v_fields := jsonb_build_object(
    'benchmark_candidate_digest', p_scope ->> 'benchmark_candidate_digest',
    'benchmark_policy_digest', p_scope ->> 'benchmark_policy_digest',
    'benchmark_subject_digest', p_scope ->> 'benchmark_subject_digest',
    'clock_origin_gate_id', p_scope ->> 'clock_origin_gate_id',
    'clock_terminus_gate_id', p_scope ->> 'clock_terminus_gate_id',
    'tenant', p_scope ->> 'tenant');
  return 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(jsonb_build_array(
      ops.j1_clock_scope_domain_tag(), v_fields)),
    'UTF8'), 'sha256'), 'hex');
end;
$$;

comment on function ops.j1_clock_scope_digest(jsonb) is
  'The key of one authoritative Journey 1 clock scope: sha256 over the canonical [domain_tag, the SIX identity fields]. All seven declared fields are required and validated, but the human scope_ref label is excluded from the preimage -- it is provenance, not identity, and hashing it made one accepted scope addressable under two keys. The gate ids and the tenant are checked rather than believed; the three benchmark digests are checked for shape only, because the stored clock state carries none of them and there is nothing here to compare them against.';

-- NULL IS NOT A MATCH, AND A MISSING DERIVATION IS NOT A PASS. `a <> b` is NULL
-- when either side is null and `if NULL then ... end if` does not fire, so a
-- comparison written that way FAILS OPEN exactly when the value being bound was
-- never derived. Left VOLATILE deliberately: its whole job is to RAISE, and a
-- check the planner may fold away is not a check.
create or replace function ops.j1_clock_assert_same(
  p_invariant text, p_field text, p_recorded anyelement, p_supplied anyelement)
returns void language plpgsql
set search_path = pg_catalog
as $$
begin
  if p_recorded is null and p_supplied is null then return; end if;
  if p_recorded is distinct from p_supplied then
    raise exception '[%] Journey 1 clock revision changes %: recorded %, supplied %',
      p_invariant, p_field, coalesce(p_recorded::text, 'null'), coalesce(p_supplied::text, 'null');
  end if;
end;
$$;

comment on function ops.j1_clock_assert_same(text,text,anyelement,anyelement) is
  'Fail-closed equality for one append-only field, comparing with IS DISTINCT FROM so a null can never make the comparison itself evaluate to NULL and fall through. Raises with the shared invariant id.';

-- What this record layer cannot prove about a stored revision. Carried on every
-- readback so a consumer reading a status off one cannot mistake storage for
-- acceptance. It mirrors JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE in the module.
create or replace function ops.j1_clock_record_layer_cannot_prove()
returns jsonb language sql immutable
set search_path = pg_catalog
as $$
  select jsonb_build_array(
    'that the origin receipt was a genuine, current, passing foundation-assurance-minimum receipt: no authenticated admission ledger exists here, and admitted_at is a projection fact',
    'that the recorded pauses were approved by a real verified partner strictly before they started, or that an approval was not backdated',
    'that the terminus receipt was admitted under the accepted per-receipt TTL policy against the accepted kernel scope',
    'that the projection the kernel read was authentic: the verifier is trusted server code and this record layer never sees it',
    'that a revision written by a direct holder of the writer bundle is a kernel computation rather than that writer''s assertion; both are trusted writers and nothing recorded here tells them apart',
    'that the authoritative scope a stored revision is bound to is the accepted scope of the projection the kernel actually read. The module''s trusted recorder compares the kernel''s own verified_binding for that computation against its store''s scope before anything is written; a direct caller of this function is checked against neither, because doctorcre-v5-journey-one-clock.v2 carries no subject, candidate or policy digest and this rail never derives one from a stored history',
    'anything about deadline SUCCESS. A stored status is a recorded computation, never an acceptance of a deadline by this record layer')
$$;

comment on function ops.j1_clock_record_layer_cannot_prove() is
  'The explicit list of things a stored Journey 1 clock revision does NOT prove. Returned on every readback so a stored status is never read as a verified deadline acceptance.';

-- ---------------------------------------------------------------------------
-- The clock: one row per identity, created once, never rewritten.
-- ---------------------------------------------------------------------------
create table if not exists ops.j1_clock (
  id                       uuid primary key default gen_random_uuid(),
  -- The DERIVED identity. It is the primary address of a clock; nothing else is.
  clock_key                text not null unique
                             check (clock_key ~ '^sha256:[0-9a-f]{64}$'),
  tenant                   text not null check (tenant = 'carr-internal'),
  -- A LEGIBILITY LABEL WITH NO AUTHORITY. It is nullable, it is set once at
  -- creation, it is never part of clock_key, and no function below selects a
  -- clock by it. A caller cannot restart a clock by inventing a new one.
  clock_ref                text check (clock_ref ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$'),
  state_schema_version     text not null
                             check (state_schema_version = 'doctorcre-v5-journey-one-clock.v2'),
  -- The origin, sealed here at creation. The revision rows carry it too, and the
  -- guard asserts the two agree on every append: a clock row that disagreed with
  -- its own revisions is a rebase wearing two hats.
  origin_receipt_digest    text not null check (origin_receipt_digest ~ '^sha256:[0-9a-f]{64}$'),
  origin_at                text not null
                             check (origin_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  origin_benchmark_manifest_digest text not null
                             check (origin_benchmark_manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  created_by_actor_id      uuid not null references public.actor(id),
  created_at               timestamptz not null default now()
);

comment on table ops.j1_clock is
  'One Journey 1 deadline clock, addressed by an identity DERIVED from its kernel origin (tenant, origin receipt digest, origin instant, origin benchmark manifest digest). clock_ref is a legibility label with no authority and selects nothing. The row is created once and never rewritten: this rail has no reset, no rebase and no replacement.';

-- ---------------------------------------------------------------------------
-- The scope binding: one authoritative scope, at most one clock, both ways.
--
-- A SEPARATE RELATION AND NOT A COLUMN ON ops.j1_clock, on purpose. This file
-- is fresh-or-exactly-compatible -- every relation is `create table if not
-- exists` -- so adding a NOT NULL column to an existing table would be silently
-- skipped on any database that already carries ops.j1_clock, and the guarantee
-- would read as present while being absent. A new relation cannot be
-- half-present: either it exists with these constraints or it is created with
-- them, and the append guard refuses a revision whose clock has no row here.
-- ---------------------------------------------------------------------------
create table if not exists ops.j1_clock_scope_binding (
  id                       uuid primary key default gen_random_uuid(),
  -- ONE SCOPE, ONE CLOCK, ENFORCED ON BOTH SIDES. The scope key is unique so a
  -- second origin cannot open a second clock for one program; the clock key is
  -- unique so a clock cannot be rebound to a second scope.
  clock_scope_key          text not null unique
                             check (clock_scope_key ~ '^sha256:[0-9a-f]{64}$'),
  -- DELIBERATELY NOT A FOREIGN KEY to ops.j1_clock. The binding is written
  -- BEFORE the clock row exists, because a clock row is created by the append
  -- itself and the append guard refuses an unbound clock: a reference would
  -- invert that order and make the guard unreachable. A binding naming a clock
  -- that never gets created is an inert row inside a transaction that either
  -- commits the clock too or rolls both back.
  clock_key                text not null unique
                             check (clock_key ~ '^sha256:[0-9a-f]{64}$'),
  -- The scope as it was supplied, kept whole so a reader can see WHICH accepted
  -- scope was named rather than only that some scope hashed to this key. It is
  -- evidence of what was claimed; it is not evidence that the claim was true.
  clock_scope              jsonb not null,
  clock_scope_ref          text not null check (clock_scope_ref ~ '^safe:[A-Za-z0-9:._/-]{3,290}$'),
  tenant                   text not null check (tenant = 'carr-internal'),
  bound_by_actor_id        uuid not null references public.actor(id),
  bound_at                 timestamptz not null default now()
);

comment on table ops.j1_clock_scope_binding is
  'Which authoritative scope holds which Journey 1 clock. Unique on both columns: one scope never holds two clocks and one clock is never rebound. This is the refusal a caller presenting a NEW ORIGIN meets -- their origin derives a fresh clock key whose creation would otherwise meet no compare-and-swap at all. It does not prove the bound scope is the accepted scope of the projection the kernel read; the stored state carries nothing to check that against.';

-- ---------------------------------------------------------------------------
-- The revision: one append-only entry per kernel evaluation that was persisted.
-- ---------------------------------------------------------------------------
create table if not exists ops.j1_clock_revision (
  id                       uuid primary key default gen_random_uuid(),
  clock_id                 uuid not null references ops.j1_clock(id),
  revision_ordinal         integer not null check (revision_ordinal >= 0),
  idempotency_key          uuid not null unique,
  -- THE COMPARE-AND-SWAP TOKEN. NULL means "create this clock" and is admissible
  -- exactly once per clock, which the partial unique index below enforces
  -- structurally rather than by convention.
  prior_history_digest     text check (prior_history_digest ~ '^sha256:[0-9a-f]{64}$'),
  history_digest           text not null check (history_digest ~ '^sha256:[0-9a-f]{64}$'),

  -- The eighteen scalar fields of doctorcre-v5-journey-one-clock.v2. Instants
  -- are TEXT and stored verbatim; see the header for why.
  state_schema_version     text not null
                             check (state_schema_version = 'doctorcre-v5-journey-one-clock.v2'),
  origin_receipt_digest    text not null check (origin_receipt_digest ~ '^sha256:[0-9a-f]{64}$'),
  origin_at                text not null
                             check (origin_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  origin_benchmark_manifest_digest text not null
                             check (origin_benchmark_manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  current_benchmark_manifest_digest text not null
                             check (current_benchmark_manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  origin_receipt_ttl_policy_ms bigint not null
                             check (origin_receipt_ttl_policy_ms > 0
                                    and origin_receipt_ttl_policy_ms <= 9007199254740991),
  base_deadline_at         text
                             check (base_deadline_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  -- The kernel's four decided resolutions, verbatim. This is not a second DST
  -- policy: it is the closed set of answers the kernel can produce, restated so
  -- a row that names something else cannot be stored at all.
  base_deadline_resolution text not null check (base_deadline_resolution in (
                             'same_chicago_wall_time_after_30_dates',
                             'chicago_wall_time_gap_shifted_forward_by_gap_length',
                             'chicago_wall_time_overlap_resolved_to_origin_utc_offset',
                             'unsupported_chicago_calendar_case')),
  due_at                   text
                             check (due_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  -- The 120-hour pre-approved blocker-pause union, in milliseconds. The kernel
  -- computes it; this ceiling only refuses a row that could not have come from
  -- the kernel at all. IT IS NOT A SECOND PAUSE BUDGET and nothing here counts
  -- an hour.
  paused_ms                bigint not null check (paused_ms >= 0 and paused_ms <= 432000000),
  status                   text not null check (status in (
                             'running', 'missed', 'completed_on_time', 'completed_late',
                             'completed_after_recorded_miss', 'unresolved_deadline',
                             'completed_unresolved_deadline')),
  miss_at                  text
                             check (miss_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  completion_receipt_digest text check (completion_receipt_digest ~ '^sha256:[0-9a-f]{64}$'),
  completion_observed_at   text
                             check (completion_observed_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  completion_receipt_ttl_policy_ms bigint
                             check (completion_receipt_ttl_policy_ms > 0
                                    and completion_receipt_ttl_policy_ms <= 9007199254740991),
  completion_artifact_digest text check (completion_artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
  completion_fixture_set_digest text check (completion_fixture_set_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- KERNEL TIME. The instant the kernel evaluated at, from the verified
  -- projection's as_of. A caller influences it; it is never this record layer's
  -- clock.
  evaluated_at             text not null
                             check (evaluated_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),

  -- PROVENANCE, NARROWLY SCOPED. It records WHICH code computed the state and
  -- from WHICH projection schema, and then says in its own column that this
  -- record layer did not verify the inputs. It is not a receipt. verifier_ref
  -- NAMES the installed verifier; naming is not proving.
  computed_by              text not null
                             check (computed_by = 'mcp-server/src/journey-one-clock.v5.js'),
  projection_schema_version text not null
                             check (projection_schema_version = 'doctorcre-v5-journey-one-clock-projection.v2'),
  verifier_ref             text not null check (verifier_ref ~ '^safe:[A-Za-z0-9:._/-]{3,290}$'),
  input_authority          text not null
                             check (input_authority = 'trusted_projection_not_independently_verified_by_this_record_layer'),

  written_by_actor_id      uuid not null references public.actor(id),
  -- SERVER TIME. This is the record layer's own clock and the only timestamptz
  -- on the rail.
  recorded_at              timestamptz not null default now(),

  unique (clock_id, revision_ordinal),
  -- THE CAS, ENFORCED STRUCTURALLY AS WELL AS IN THE GUARD. Two appends naming
  -- the same prior cannot both land, whatever a guard did or did not see.
  unique (clock_id, prior_history_digest),
  unique (clock_id, history_digest),
  -- A revision may not name itself as its own prior.
  constraint j1_clock_revision_prior_is_not_self
    check (prior_history_digest is null or prior_history_digest <> history_digest),
  -- Ordinal zero is the creation and carries the explicit null prior; every
  -- later ordinal must name one.
  constraint j1_clock_revision_creation_prior_is_null
    check ((revision_ordinal = 0) = (prior_history_digest is null)),
  -- THE COMPLETION AND ITS SEALS ARE ONE FACT. All five are null before a
  -- completion is recorded and all five are present after it, exactly as the
  -- kernel writes them.
  constraint j1_clock_revision_completion_seals_are_one_fact
    check (num_nulls(completion_receipt_digest, completion_observed_at,
                     completion_receipt_ttl_policy_ms, completion_artifact_digest,
                     completion_fixture_set_digest) in (0, 5)),
  -- An unresolved deadline has no base deadline and therefore no due date.
  constraint j1_clock_revision_unresolved_deadline_has_no_dates
    check ((base_deadline_at is null)
           = (base_deadline_resolution = 'unsupported_chicago_calendar_case')
           and (base_deadline_at is null) = (due_at is null)),
  -- Q008.D1 FORBIDS CLAIMING DEADLINE SUCCESS ONCE A MISS IS DURABLY RECORDED.
  -- The kernel refuses to READ a history carrying both; this rail refuses to
  -- STORE one, because a store that will hold the contradiction is the place a
  -- forbidden claim goes unnoticed.
  constraint j1_clock_no_deadline_success_after_recorded_miss
    check (miss_at is null or status <> 'completed_on_time'),
  -- completed_after_recorded_miss is exactly a completion standing beside a
  -- recorded miss; it cannot mean anything else.
  constraint j1_clock_revision_after_miss_status_is_coherent
    check (status <> 'completed_after_recorded_miss'
           or (miss_at is not null and completion_receipt_digest is not null))
);

comment on table ops.j1_clock_revision is
  'One append-only revision of a Journey 1 clock history. prior_history_digest is the exact compare-and-swap token: NULL creates the clock and is admissible once, anything else must name the current head. history_digest is recomputed from this revision''s own rows at commit and on every read. Storing a status is not accepting a deadline: see ops.j1_clock_record_layer_cannot_prove().';

-- AT MOST ONE CREATION PER CLOCK. A plain unique (clock_id, prior_history_digest)
-- does not cover this: PostgreSQL treats NULLs as distinct in a unique index, so
-- two concurrent creations would both be admitted. This partial index is what
-- makes the null-prior CAS a real compare-and-swap.
create unique index if not exists j1_clock_revision_one_creation
  on ops.j1_clock_revision (clock_id)
  where prior_history_digest is null;

-- The head lookup, and the ordered read.
create index if not exists j1_clock_revision_by_clock
  on ops.j1_clock_revision (clock_id, revision_ordinal desc);

-- ---------------------------------------------------------------------------
-- The two ordered child relations. ORDER IS PART OF THE HASH.
-- ---------------------------------------------------------------------------
create table if not exists ops.j1_clock_revision_pause_interval (
  id                       uuid primary key default gen_random_uuid(),
  revision_id              uuid not null references ops.j1_clock_revision(id),
  ordinal                  integer not null check (ordinal >= 0),
  pause_id                 text not null check (char_length(pause_id) >= 1),
  -- VERBATIM, AND NULLABLE. A pause whose blocker has not ended carries null;
  -- an end reported later is stored as the exact string the projection carried,
  -- because a normalized one would hash differently.
  ends_at                  text
                             check (ends_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  created_at               timestamptz not null default now(),
  unique (revision_id, ordinal),
  -- The kernel refuses a history with a repeated pause id; so does this.
  unique (revision_id, pause_id)
);

comment on table ops.j1_clock_revision_pause_interval is
  'The pause intervals of one Journey 1 clock revision, in the projection''s own array order. ends_at is stored verbatim as text because the kernel does not normalize it and only the exact string reproduces the history digest.';

create table if not exists ops.j1_clock_revision_event (
  id                       uuid primary key default gen_random_uuid(),
  revision_id              uuid not null references ops.j1_clock_revision(id),
  ordinal                  integer not null check (ordinal >= 0),
  -- The kernel's five event types, verbatim. A row naming anything else could
  -- not have come from the kernel.
  event_type               text not null check (event_type in (
                             'clock_started', 'pause_approved', 'amendment_recorded',
                             'deadline_missed', 'completion_observed')),
  -- When the fact happened, and when the kernel first saw it. Both verbatim.
  happened_at              text not null
                             check (happened_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  kernel_recorded_at       text not null
                             check (kernel_recorded_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$'),
  evidence_digest          text not null check (evidence_digest ~ '^sha256:[0-9a-f]{64}$'),
  previous_event_digest    text check (previous_event_digest ~ '^sha256:[0-9a-f]{64}$'),
  event_digest             text not null check (event_digest ~ '^sha256:[0-9a-f]{64}$'),
  created_at               timestamptz not null default now(),
  unique (revision_id, ordinal),
  unique (revision_id, event_digest),
  -- Only the first link has no predecessor.
  constraint j1_clock_event_first_link_has_no_previous
    check ((ordinal = 0) = (previous_event_digest is null)),
  constraint j1_clock_event_does_not_precede_itself
    check (previous_event_digest is null or previous_event_digest <> event_digest)
);

comment on table ops.j1_clock_revision_event is
  'The event chain of one Journey 1 clock revision: a linked list whose links are hashes of the link before. A prior revision''s chain must be an exact prefix of the next revision''s, which is what makes a lost event, a removed miss and an erased approval all unwritable.';

-- ---------------------------------------------------------------------------
-- APPEND-ONLY. There is no reset, no delete, no backdating and no replacement on
-- this rail, and that is enforced rather than asserted.
--
-- TWO TRIGGERS PER RELATION, because UPDATE/DELETE and TRUNCATE are different
-- events. A ROW-LEVEL TRIGGER NEVER SEES TRUNCATE -- it is a statement event --
-- so a row-level-only posture would leave the header's claim that "update,
-- delete and truncate are refused everywhere on this rail" true of every runtime
-- bundle and FALSE of the table owner, from whom TRUNCATE cannot be revoked. The
-- `revoke ... truncate` at the foot of this file is the grant half and does not
-- bind the owner; this is the half that does. Same shape as
-- ops/journey-one-clock-input-store.candidate.sql, ops/model-role-store.candidate.sql,
-- ops/cre-lifecycle.candidate.sql and ops/document-derivative-registration.candidate.sql,
-- which is the house pattern rather than a new one invented here.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_clock_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception '[j1_clock_rows_are_append_only] Journey 1 clock rows are append-only: % is refused on ops.%',
    tg_op, tg_table_name using errcode = '42501';
end;
$$;

comment on function ops.j1_clock_rows_immutable() is
  'Refuses every update, delete and truncate on the Journey 1 clock storage tables. Carries the shared invariant id j1_clock_rows_are_append_only. It is installed twice per relation because a row-level trigger never sees TRUNCATE, and TRUNCATE cannot be revoked from the table owner.';

do $$
declare t text;
begin
  foreach t in array array[
    'j1_clock', 'j1_clock_scope_binding', 'j1_clock_revision',
    'j1_clock_revision_pause_interval', 'j1_clock_revision_event'
  ] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_append_only', t);
    execute format(
      'create trigger %I before update or delete on ops.%I for each row execute function ops.j1_clock_rows_immutable()',
      t || '_append_only', t);
    -- TRUNCATE is statement-level and BEFORE-only; there is no row to see.
    execute format('drop trigger if exists %I on ops.%I', t || '_no_truncate', t);
    execute format(
      'create trigger %I before truncate on ops.%I for each statement execute function ops.j1_clock_rows_immutable()',
      t || '_no_truncate', t);
  end loop;
end $$;

-- Append-only blocks REWRITING a stored revision. It does NOT block ADDING to
-- one, and a pause-interval or event row appended to a revision that is already
-- sealed would change what its digest covers while the row still read as valid.
-- So a revision's content is closed to inserts the moment a LATER revision
-- exists on the same clock, and the digest check at commit closes the rest.
--
-- THE FREEZE AND THE APPEND SHARE A LOCK, OR NEITHER SEES THE OTHER.
-- Read-committed gives each of these a snapshot that excludes the other's
-- uncommitted rows: a child insert running beside a fresh append sees no later
-- revision and is admitted, while the append recomputes a digest over rows that
-- do not yet include it, and both commit. The result is a revision naming a
-- digest its own rows no longer produce.
--
-- Both sides therefore take a lock ON THE CLOCK ROW, and it works only because
-- they take the SAME one. A lock serializes nothing on its own; it serializes
-- exactly the transactions requesting a conflicting mode on the same row. Here
-- this trigger takes FOR SHARE and ops.j1_clock_append_revision() takes FOR
-- UPDATE, and those two modes conflict. The trigger is what makes the protocol
-- total rather than a convention: it fires on EVERY insert into both child
-- tables whatever code path issued it, and direct INSERT is granted to nobody,
-- so there is no path that skips it.
create or replace function ops.j1_clock_child_frozen_after_next_revision()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_clock uuid; v_ordinal integer;
begin
  select r.clock_id, r.revision_ordinal into v_clock, v_ordinal
    from ops.j1_clock_revision r where r.id = new.revision_id;
  if not found then
    raise exception '[j1_clock_content_rebuilds_to_its_digest] Journey 1 clock % row names an unknown revision', tg_table_name;
  end if;
  perform 1 from ops.j1_clock where id = v_clock for share;
  if exists (select 1 from ops.j1_clock_revision r
              where r.clock_id = v_clock and r.revision_ordinal > v_ordinal) then
    raise exception '[j1_clock_content_rebuilds_to_its_digest] revision % of this clock is sealed behind a later revision; its content is closed to further % rows',
      v_ordinal, tg_table_name;
  end if;
  return new;
end;
$$;

comment on function ops.j1_clock_child_frozen_after_next_revision() is
  'Refuses a pause-interval or event insert against a revision that a later revision already follows, so no hashed row can appear outside the digest the revision was sealed under. Takes FOR SHARE on the clock row; ops.j1_clock_append_revision takes FOR UPDATE on the same row, and it is that shared protocol -- not the lock alone -- that keeps a concurrent child insert and an append from committing past each other.';

do $$
declare t text;
begin
  foreach t in array array['j1_clock_revision_pause_interval', 'j1_clock_revision_event'] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_frozen', t);
    execute format(
      'create trigger %I before insert on ops.%I for each row execute function ops.j1_clock_child_frozen_after_next_revision()',
      t || '_frozen', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- WHOLE-CONTENT RECONSTRUCTION. This is what makes the digest a statement about
-- the persisted history rather than about a blob a caller once supplied.
-- ---------------------------------------------------------------------------
-- ONE PREIMAGE BUILDER, TWO CALLERS. `_of` takes the decomposed content as
-- parameters; the uuid form reads a stored revision's rows and hands them to it.
-- They are not two implementations: the uuid form is one line of delegation, so
-- "what the caller offered" and "what the rows say" cannot be hashed by two
-- different rules that drift apart after one edit. The parameter form also gives
-- a fixture a way to LEARN the digest a set of rows will produce before it
-- writes them, without an insert.
--
-- The ordinals are the ORDER and are not themselves hashed: r7 array order
-- participates in the kernel's serialization, so the arrays are emitted in
-- ordinal order and the ordinal column is dropped from the hashed object.
create or replace function ops.j1_clock_history_preimage_of(
  p_scalars jsonb, p_pause_intervals jsonb, p_events jsonb)
returns jsonb language sql immutable
set search_path = pg_catalog
as $$
  select p_scalars || jsonb_build_object(
    'pause_intervals', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'pause_id', x.value ->> 'pause_id',
               -- `->` and not `->>`, so a null stays JSON null instead of
               -- vanishing from the object and changing the hash.
               'ends_at', x.value -> 'ends_at')
             order by (x.value ->> 'ordinal')::integer), '[]'::jsonb)
        from jsonb_array_elements(coalesce(p_pause_intervals, '[]'::jsonb)) x),
    'events', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'type', x.value ->> 'type',
               'at', x.value ->> 'at',
               'recorded_at', x.value ->> 'recorded_at',
               'evidence_digest', x.value ->> 'evidence_digest',
               'previous_event_digest', x.value -> 'previous_event_digest',
               'event_digest', x.value ->> 'event_digest')
             order by (x.value ->> 'ordinal')::integer), '[]'::jsonb)
        from jsonb_array_elements(coalesce(p_events, '[]'::jsonb)) x))
$$;

comment on function ops.j1_clock_history_preimage_of(jsonb,jsonb,jsonb) is
  'The twenty hashed fields of a Journey 1 clock history, assembled from decomposed content. The ordinals order the arrays and are dropped from the hashed object. The uuid form below delegates to this, so stored rows and offered content are hashed by one rule.';

create or replace function ops.j1_clock_history_digest_of(
  p_scalars jsonb, p_pause_intervals jsonb, p_events jsonb)
returns text language sql stable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(
      ops.j1_clock_history_preimage_of(p_scalars, p_pause_intervals, p_events)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.j1_clock_history_digest_of(jsonb,jsonb,jsonb) is
  'The history digest a set of decomposed content produces. A fixture can learn a digest with this without writing a row; ops.j1_clock_append_revision still compares against the digest the STORED rows produce, so the two can never diverge silently.';

-- ONE EVENT LINK, HASHED THE KERNEL'S OWN WAY. journey-one-clock.v5.js builds
-- `data = { type, at, recorded_at, evidence_digest, previous_event_digest }` and
-- sets `event_digest = digest(data)`. This is that statement in SQL, and it is
-- what lets the integrity reader below check an event chain rather than merely
-- believe it: without it, a writer could hand this rail any sha256-shaped string
-- as an event digest and the chain would still "link".
--
-- It re-decides nothing. It does not say WHICH events may exist, in what order,
-- or what evidence they must name -- all of that is the kernel's. It only
-- recomputes a hash the kernel already defined.
create or replace function ops.j1_clock_event_digest(
  p_type text, p_at text, p_recorded_at text, p_evidence_digest text,
  p_previous_event_digest text)
returns text language sql stable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(jsonb_build_object(
      'at', p_at,
      'evidence_digest', p_evidence_digest,
      'previous_event_digest', to_jsonb(p_previous_event_digest),
      'recorded_at', p_recorded_at,
      'type', p_type)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.j1_clock_event_digest(text,text,text,text,text) is
  'The digest journey-one-clock.v5.js gives one history event: sha256 over the canonical {at, evidence_digest, previous_event_digest, recorded_at, type}. Recomputing a hash the kernel defined; it decides nothing about which events may exist.';

create or replace function ops.j1_clock_revision_scalars(p_revision_id uuid)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v ops.j1_clock_revision%rowtype;
begin
  select * into v from ops.j1_clock_revision where id = p_revision_id;
  if not found then
    raise exception '[j1_clock_content_rebuilds_to_its_digest] Journey 1 clock revision % does not exist', p_revision_id;
  end if;
  -- to_jsonb on every nullable field, so a null is stored in the object as JSON
  -- null rather than being dropped: a dropped key is a different hash.
  return jsonb_build_object(
    'schema_version', v.state_schema_version,
    'origin_receipt_digest', v.origin_receipt_digest,
    'origin_at', v.origin_at,
    'origin_benchmark_manifest_digest', v.origin_benchmark_manifest_digest,
    'current_benchmark_manifest_digest', v.current_benchmark_manifest_digest,
    'origin_receipt_ttl_policy_ms', v.origin_receipt_ttl_policy_ms,
    'base_deadline_at', to_jsonb(v.base_deadline_at),
    'base_deadline_resolution', v.base_deadline_resolution,
    'due_at', to_jsonb(v.due_at),
    'paused_ms', v.paused_ms,
    'status', v.status,
    'miss_at', to_jsonb(v.miss_at),
    'completion_receipt_digest', to_jsonb(v.completion_receipt_digest),
    'completion_observed_at', to_jsonb(v.completion_observed_at),
    'completion_receipt_ttl_policy_ms', to_jsonb(v.completion_receipt_ttl_policy_ms),
    'completion_artifact_digest', to_jsonb(v.completion_artifact_digest),
    'completion_fixture_set_digest', to_jsonb(v.completion_fixture_set_digest),
    'evaluated_at', v.evaluated_at);
end;
$$;

comment on function ops.j1_clock_revision_scalars(uuid) is
  'The eighteen scalar state fields of one stored revision, as the kernel spells them.';

create or replace function ops.j1_clock_revision_pause_interval_rows(p_revision_id uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select coalesce(jsonb_agg(jsonb_build_object(
           'ordinal', p.ordinal, 'pause_id', p.pause_id,
           'ends_at', to_jsonb(p.ends_at)) order by p.ordinal), '[]'::jsonb)
    from ops.j1_clock_revision_pause_interval p where p.revision_id = p_revision_id
$$;

create or replace function ops.j1_clock_revision_event_rows(p_revision_id uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select coalesce(jsonb_agg(jsonb_build_object(
           'ordinal', e.ordinal, 'type', e.event_type, 'at', e.happened_at,
           'recorded_at', e.kernel_recorded_at, 'evidence_digest', e.evidence_digest,
           'previous_event_digest', to_jsonb(e.previous_event_digest),
           'event_digest', e.event_digest) order by e.ordinal), '[]'::jsonb)
    from ops.j1_clock_revision_event e where e.revision_id = p_revision_id
$$;

comment on function ops.j1_clock_revision_pause_interval_rows(uuid) is
  'The stored pause intervals of one revision, with their ordinals, in ordinal order.';
comment on function ops.j1_clock_revision_event_rows(uuid) is
  'The stored event chain of one revision, with its ordinals, in ordinal order.';

-- The twenty state fields WITHOUT history_digest. The kernel computes its digest
-- as sha256 over the canonical serialization of exactly this object -- it writes
-- `delete state.history_digest; state.history_digest = digest(state)` and reads
-- it back the same way -- so this is the preimage and nothing else.
create or replace function ops.j1_clock_history_preimage(p_revision_id uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select ops.j1_clock_history_preimage_of(
    ops.j1_clock_revision_scalars(p_revision_id),
    ops.j1_clock_revision_pause_interval_rows(p_revision_id),
    ops.j1_clock_revision_event_rows(p_revision_id))
$$;

comment on function ops.j1_clock_history_preimage(uuid) is
  'The twenty hashed fields of one stored Journey 1 clock revision, rebuilt from its own rows. The twenty-first, history_digest, is the hash OF this object and is therefore not in it.';

create or replace function ops.j1_clock_history_digest(p_revision_id uuid)
returns text language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(ops.j1_clock_history_preimage(p_revision_id)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.j1_clock_history_digest(uuid) is
  'The history digest one stored revision''s rows produce right now. A caller may name a digest; it is only ever compared against this.';

-- The full state, WITH its digest: what a reader gets back.
create or replace function ops.j1_clock_history_state(p_revision_id uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select ops.j1_clock_history_preimage(p_revision_id)
         || jsonb_build_object('history_digest',
              (select r.history_digest from ops.j1_clock_revision r where r.id = p_revision_id))
$$;

comment on function ops.j1_clock_history_state(uuid) is
  'One stored revision rebuilt into the kernel''s twenty-one-field state, ready to be handed back to journey-one-clock.v5.js as its `history`.';

-- ---------------------------------------------------------------------------
-- INTEGRITY. Every clause recomputes from the persisted rows, so a tampered row
-- cannot answer for itself.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_clock_revision_integrity_error(p_revision_id uuid)
returns text language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v ops.j1_clock_revision%rowtype; v_clock ops.j1_clock%rowtype;
  v_live text; v_derived text; v_count integer; v_max integer;
  v_previous text; v_row record;
begin
  select * into v from ops.j1_clock_revision where id = p_revision_id;
  if not found then return format('revision %s does not exist', p_revision_id); end if;
  select * into v_clock from ops.j1_clock where id = v.clock_id;
  if not found then return format('revision %s names an unknown clock', p_revision_id); end if;

  if v.state_schema_version <> ops.j1_clock_state_schema() then
    return format('[j1_clock_state_schema_current] revision %s stores %s, not %s',
      p_revision_id, v.state_schema_version, ops.j1_clock_state_schema());
  end if;

  -- THE CONTENT REBUILDS TO ITS DIGEST, or it is not the history it claims.
  v_live := ops.j1_clock_history_digest(p_revision_id);
  if v.history_digest is distinct from v_live then
    return format('[j1_clock_content_rebuilds_to_its_digest] revision %s no longer rebuilds to its stored digest: stored %s, computed %s',
      p_revision_id, v.history_digest, v_live);
  end if;

  -- THE CLOCK IS THE ONE THIS REVISION''S OWN ORIGIN DERIVES.
  v_derived := ops.j1_clock_identity_digest(
    v_clock.tenant, v.origin_receipt_digest, v.origin_at, v.origin_benchmark_manifest_digest);
  if v_derived is distinct from v_clock.clock_key then
    return format('[j1_clock_identity_derived_from_origin] revision %s is filed under %s but its own origin derives %s',
      p_revision_id, v_clock.clock_key, v_derived);
  end if;
  if v_clock.origin_receipt_digest is distinct from v.origin_receipt_digest
     or v_clock.origin_at is distinct from v.origin_at
     or v_clock.origin_benchmark_manifest_digest is distinct from v.origin_benchmark_manifest_digest then
    return format('[j1_clock_origin_never_rewritten] revision %s disagrees with its clock row about the origin', p_revision_id);
  end if;

  -- Contiguous ordinals in both child relations. A gap is a row that was
  -- expected and is missing, which is the shape a partial insert leaves behind.
  select count(*), coalesce(max(ordinal), -1) into v_count, v_max
    from ops.j1_clock_revision_event where revision_id = p_revision_id;
  if v_count = 0 then
    return format('[j1_clock_events_are_append_only] revision %s carries no events', p_revision_id);
  end if;
  if v_count <> v_max + 1 then
    return format('[j1_clock_events_are_append_only] revision %s has a gap in its event ordinals', p_revision_id);
  end if;
  select count(*), coalesce(max(ordinal), -1) into v_count, v_max
    from ops.j1_clock_revision_pause_interval where revision_id = p_revision_id;
  if v_count <> v_max + 1 then
    return format('[j1_clock_content_rebuilds_to_its_digest] revision %s has a gap in its pause-interval ordinals', p_revision_id);
  end if;

  -- The chain is a chain: each link names the link before it, AND each link
  -- hashes to its own content. Linkage alone would let a writer hand this rail
  -- any sha256-shaped strings that happened to point at each other.
  v_previous := null;
  for v_row in select ordinal, event_type, happened_at, kernel_recorded_at,
                      evidence_digest, previous_event_digest, event_digest
                 from ops.j1_clock_revision_event
                where revision_id = p_revision_id order by ordinal loop
    if v_row.previous_event_digest is distinct from v_previous then
      return format('[j1_clock_events_are_append_only] revision %s event %s does not link to the event before it',
        p_revision_id, v_row.ordinal);
    end if;
    if v_row.event_digest is distinct from ops.j1_clock_event_digest(
         v_row.event_type, v_row.happened_at, v_row.kernel_recorded_at,
         v_row.evidence_digest, v_row.previous_event_digest) then
      return format('[j1_clock_events_are_append_only] revision %s event %s does not hash to its own event_digest',
        p_revision_id, v_row.ordinal);
    end if;
    v_previous := v_row.event_digest;
  end loop;

  -- The one contradiction Q008.D1 states outright. The table constraint refuses
  -- it at insert; this refuses it on read too, so a record that reached the
  -- table by any other route is still reported rather than served.
  if v.miss_at is not null and v.status = 'completed_on_time' then
    return format('[j1_clock_no_deadline_success_after_recorded_miss] revision %s claims deadline success beside a recorded miss',
      p_revision_id);
  end if;
  return null;
end;
$$;

comment on function ops.j1_clock_revision_integrity_error(uuid) is
  'Why one stored Journey 1 clock revision is not trustworthy, recomputed from its rows, or null when it is intact. Every message carries the shared invariant id.';

-- ---------------------------------------------------------------------------
-- Head, row and idempotency readers. Each returns jsonb in the exact shape the
-- module''s journal port expects, so the two sides cannot drift on field names.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_clock_row(p_clock_key text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
           'clock_key', c.clock_key,
           'tenant', c.tenant,
           'clock_ref', to_jsonb(c.clock_ref),
           'state_schema_version', c.state_schema_version,
           'origin_receipt_digest', c.origin_receipt_digest,
           'origin_at', c.origin_at,
           'origin_benchmark_manifest_digest', c.origin_benchmark_manifest_digest,
           -- The authoritative scope this clock is bound to, or null. Null is a
           -- real answer and is reported rather than hidden: an unbound clock is
           -- the shape a second origin leaves behind.
           'clock_scope_key', (select b.clock_scope_key from ops.j1_clock_scope_binding b
                                where b.clock_key = c.clock_key),
           'clock_scope_ref', (select b.clock_scope_ref from ops.j1_clock_scope_binding b
                                where b.clock_key = c.clock_key),
           'created_at', c.created_at)
    from ops.j1_clock c where c.clock_key = p_clock_key
$$;

comment on function ops.j1_clock_row(text) is
  'One Journey 1 clock addressed by its DERIVED key, or null. There is deliberately no lookup by clock_ref: a label selects nothing on this rail.';

create or replace function ops.j1_clock_revision_json(p_revision_id uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
           'revision_id', r.id,
           'clock_key', c.clock_key,
           'tenant', c.tenant,
           'clock_ref', to_jsonb(c.clock_ref),
           'revision_ordinal', r.revision_ordinal,
           'state_schema_version', r.state_schema_version,
           'history_digest', r.history_digest,
           'expected_prior_history_digest', to_jsonb(r.prior_history_digest),
           'recorded_at', r.recorded_at,
                    'scalars', ops.j1_clock_revision_scalars(r.id),
           'pause_intervals', ops.j1_clock_revision_pause_interval_rows(r.id),
           'events', ops.j1_clock_revision_event_rows(r.id),
           'provenance', jsonb_build_object(
             'computed_by', r.computed_by,
             'kernel_state_schema_version', r.state_schema_version,
             'projection_schema_version', r.projection_schema_version,
             'verifier_ref', r.verifier_ref,
             'input_authority', r.input_authority,
             'written_by_actor_id', r.written_by_actor_id),
           'integrity_error', to_jsonb(ops.j1_clock_revision_integrity_error(r.id)))
    from ops.j1_clock_revision r join ops.j1_clock c on c.id = r.clock_id
   where r.id = p_revision_id
$$;

comment on function ops.j1_clock_revision_json(uuid) is
  'One stored revision in the exact shape createPostgresJourneyOneClockJournal expects: the decomposed rows, the stored digest, the CAS token it was written under, the server instant it was recorded at, and its scoped provenance. `scalars` is the hashed preimage minus its two relations, so the reader and the hasher cannot disagree about which fields are scalar.';

create or replace function ops.j1_clock_head(p_clock_key text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select ops.j1_clock_revision_json(r.id)
    from ops.j1_clock_revision r join ops.j1_clock c on c.id = r.clock_id
   where c.clock_key = p_clock_key
   order by r.revision_ordinal desc limit 1
$$;

comment on function ops.j1_clock_head(text) is
  'The current head revision of one clock, or null when the clock has none. The compare-and-swap token an append must name is this row''s history_digest.';

create or replace function ops.j1_clock_revision_by_idempotency_key(p_idempotency_key uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select ops.j1_clock_revision_json(r.id)
    from ops.j1_clock_revision r where r.idempotency_key = p_idempotency_key
$$;

comment on function ops.j1_clock_revision_by_idempotency_key(uuid) is
  'The revision one idempotency key already wrote, or null. An exact replay returns it; a key presented with different content is refused by ops.j1_clock_append_revision.';

create or replace function ops.j1_clock_revisions(p_clock_key text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select coalesce(jsonb_agg(ops.j1_clock_revision_json(r.id) order by r.revision_ordinal), '[]'::jsonb)
    from ops.j1_clock_revision r join ops.j1_clock c on c.id = r.clock_id
   where c.clock_key = p_clock_key
$$;

comment on function ops.j1_clock_revisions(text) is
  'Every revision of one clock, ascending by revision_ordinal. Ordered by the ordinal and not by recorded_at: now() is transaction start time, so two revisions written in one transaction share an instant and an order that tied there would return rows in whatever order the plan produced.';

-- ---------------------------------------------------------------------------
-- THE SERIALIZED SECTION. Two halves, taken by one function so a future writer
-- cannot join the protocol while taking only one of them.
--
-- The advisory half is not decoration: a CREATION has no clock row to lock, so
-- FOR UPDATE alone would let two concurrent creations both proceed to the point
-- of insert. (The partial unique index would still refuse the second, so the
-- outcome is correct either way -- but a refusal that arrives as a named
-- exception is better than one that arrives as a constraint violation.)
-- ---------------------------------------------------------------------------
create or replace function ops.j1_clock_lock(p_clock_key text)
returns void language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
begin
  if p_clock_key is null then
    raise exception '[j1_clock_exact_prior_history_digest] a Journey 1 clock append must name the clock it is appending to';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_clock_key, 0));
  perform 1 from ops.j1_clock where clock_key = p_clock_key for update;
end;
$$;

comment on function ops.j1_clock_lock(text) is
  'Opens the serialized same-clock append section for the calling transaction: a transaction-scoped advisory lock on the clock key (which serializes concurrent CREATIONS, which have no row yet) plus FOR UPDATE on the clock row when it exists. The child-freeze trigger takes FOR SHARE on the same row, and the two modes conflict.';

-- THE SCOPE HALF OF THE SAME SECTION, and it is not covered by the clock half:
-- two creations for one scope under two DIFFERENT origins hold no clock lock in
-- common, because their clock keys differ. This is what serializes them. The
-- advisory seed is 1 rather than 0 so a scope key and a clock key never share a
-- slot, and the unique constraints underneath remain the structural backstop --
-- this only makes the refusal a named one rather than a constraint violation.
create or replace function ops.j1_clock_scope_lock(p_clock_scope_key text)
returns void language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
begin
  if p_clock_scope_key is null then
    raise exception '[j1_clock_scope_binds_one_clock] a Journey 1 clock append must name the authoritative scope it is writing for';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_clock_scope_key, 1));
  perform 1 from ops.j1_clock_scope_binding where clock_scope_key = p_clock_scope_key for update;
end;
$$;

comment on function ops.j1_clock_scope_lock(text) is
  'Opens the serialized same-scope section for the calling transaction, so two creations for one authoritative scope under two different origins cannot both read an unbound scope and both bind it. The clock-key lock does not cover this: their clock keys differ.';

create or replace function ops.j1_clock_scope_binding_json(p_binding_id uuid)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
           'clock_scope_key', b.clock_scope_key,
           'clock_key', b.clock_key,
           'clock_scope_ref', b.clock_scope_ref,
           'clock_scope', b.clock_scope,
           'tenant', b.tenant,
           'bound_at', b.bound_at)
    from ops.j1_clock_scope_binding b where b.id = p_binding_id
$$;

comment on function ops.j1_clock_scope_binding_json(uuid) is
  'One scope binding in the shape createPostgresJourneyOneClockJournal expects. The stored scope object travels with it: a reader is entitled to see WHICH accepted scope was named, not merely that something hashed to this key.';

create or replace function ops.j1_clock_scope_bindings(p_clock_scope_key text, p_clock_key text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
    'by_scope', (select ops.j1_clock_scope_binding_json(b.id) from ops.j1_clock_scope_binding b
                  where p_clock_scope_key is not null and b.clock_scope_key = p_clock_scope_key),
    'by_clock', (select ops.j1_clock_scope_binding_json(b.id) from ops.j1_clock_scope_binding b
                  where p_clock_key is not null and b.clock_key = p_clock_key))
$$;

comment on function ops.j1_clock_scope_bindings(text,text) is
  'Which clock this authoritative scope already holds, and which scope this clock is already bound to. Either side may be null. This is the READ a creation presenting a new origin has to meet before it can become a second clock for one program.';

-- BINDING IS ITS OWN ACT, WRITTEN BEFORE THE APPEND IT BELONGS TO, and it is
-- idempotent for the exact pair so an ordinary append rebinds nothing. The key
-- is DERIVED from the supplied scope here; a caller-supplied key would be a
-- self-chosen address wearing a hash.
create or replace function ops.j1_clock_bind_scope(p_clock_key text, p_clock_scope jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_key text; v_existing ops.j1_clock_scope_binding%rowtype; v_id uuid;
begin
  if p_clock_key is null or p_clock_key !~ '^sha256:[0-9a-f]{64}$' then
    raise exception '[j1_clock_identity_derived_from_origin] a Journey 1 scope binding names a derived clock key';
  end if;
  v_key := ops.j1_clock_scope_digest(p_clock_scope);
  perform ops.j1_clock_scope_lock(v_key);

  select * into v_existing from ops.j1_clock_scope_binding where clock_scope_key = v_key;
  if found then
    if v_existing.clock_key is distinct from p_clock_key then
      raise exception '[j1_clock_scope_binds_one_clock] authoritative clock scope % already holds clock %, and this revision''s origin derives %. A new origin for a scope that already has a clock is a reset wearing a new address: append to the clock that exists, or refuse',
        v_key, v_existing.clock_key, p_clock_key;
    end if;
    -- THE LABEL IS SEALED AT BINDING. It is not identity -- v_key ignores it,
    -- which is exactly what makes a relabelled scope the SAME scope -- and for
    -- that reason the record must not end up holding two names for one scope or
    -- quietly replacing the one it was bound under. Renaming is external.
    if v_existing.clock_scope_ref is distinct from (p_clock_scope ->> 'scope_ref') then
      raise exception '[j1_clock_scope_label_is_not_identity] authoritative clock scope % was bound under label %, and % was supplied. The label is provenance, is recorded once, and is never rewritten by a later write',
        v_key, v_existing.clock_scope_ref, coalesce(p_clock_scope ->> 'scope_ref', 'null');
    end if;
    return ops.j1_clock_scope_binding_json(v_existing.id);
  end if;

  select * into v_existing from ops.j1_clock_scope_binding where clock_key = p_clock_key;
  if found then
    raise exception '[j1_clock_scope_sealed_at_creation] Journey 1 clock % is bound to authoritative scope % and is never rebound; scope % was supplied',
      p_clock_key, v_existing.clock_scope_key, v_key;
  end if;

  insert into ops.j1_clock_scope_binding(
    clock_scope_key, clock_key, clock_scope, clock_scope_ref, tenant, bound_by_actor_id)
  values (v_key, p_clock_key, p_clock_scope,
    p_clock_scope ->> 'scope_ref', p_clock_scope ->> 'tenant',
    ops.portfolio_writer_actor_id())
  returning id into v_id;
  return ops.j1_clock_scope_binding_json(v_id);
end;
$$;

comment on function ops.j1_clock_bind_scope(text,jsonb) is
  'Bind one Journey 1 clock to the authoritative scope it is the clock for, deriving the scope key from the scope''s six identity fields. Idempotent for the exact pair; refuses a second clock for one scope, a second scope for one clock, and a second label for one scope. It proves nothing about whether the supplied scope is the accepted scope of the projection the kernel read -- the stored state carries nothing to check that against, and ops.j1_clock_record_layer_cannot_prove() says so.';

-- ---------------------------------------------------------------------------
-- THE APPEND GUARD. Everything an append depends on is checked HERE, from the
-- persisted rows, so a handler bug -- or a direct caller who never runs the
-- JavaScript at all -- cannot land a revision that breaks an invariant.
--
-- It is a DEFERRED CONSTRAINT TRIGGER because the diff needs the new revision's
-- child rows, which do not exist yet at BEFORE INSERT time. Deferring past the
-- inserting statement is what lets it compare two complete event chains.
--
-- WHICH MEANS THE DEFERRAL MODE IS PART OF THIS GUARD, NOT A DETAIL OF ITS
-- CALLER. SET CONSTRAINTS is transaction-scoped, so a mode set by one call
-- outlives it: a guard left IMMEDIATE fires at the end of the next revision's
-- own INSERT and reads a revision with no children, refusing a valid append.
-- ops.j1_clock_append_revision therefore sets DEFERRED before it inserts the
-- revision row, forces IMMEDIATE once the children are written, and restores
-- DEFERRED before returning. Every path that writes a revision goes through
-- that function -- direct INSERT is granted to nobody -- so the discipline is
-- total rather than conventional.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_clock_append_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  v_clock ops.j1_clock%rowtype;
  v_prior ops.j1_clock_revision%rowtype;
  v_binding ops.j1_clock_scope_binding%rowtype;
  v_error text; v_head_ordinal integer; v_prior_events integer; v_new_events integer;
  v_row record; v_field text;
begin
  select * into v_clock from ops.j1_clock where id = new.clock_id;
  if not found then
    raise exception '[j1_clock_identity_derived_from_origin] Journey 1 clock revision names an unknown clock';
  end if;

  -- VERSION CONFUSION, REFUSED BY NAME. A v1 record names an origin digest the
  -- current kernel cannot recompute; silently re-deriving it would rebase a
  -- sealed origin, so migration is explicit and external.
  if new.state_schema_version = any (ops.j1_clock_legacy_state_schemas()) then
    raise exception '[j1_clock_state_schema_current] Journey 1 clock history % is a legacy state schema and requires an explicit migration; this rail stores % only and never re-derives a sealed origin',
      new.state_schema_version, ops.j1_clock_state_schema();
  end if;
  if new.state_schema_version <> ops.j1_clock_state_schema() then
    raise exception '[j1_clock_state_schema_current] Journey 1 clock history % is not a state schema this rail stores',
      new.state_schema_version;
  end if;
  if v_clock.state_schema_version <> new.state_schema_version then
    raise exception '[j1_clock_state_schema_current] Journey 1 clock revision changes the state schema of its clock';
  end if;

  -- TENANT. A revision is stored under the tenant its identity was derived with.
  if v_clock.tenant <> 'carr-internal' then
    raise exception '[j1_clock_tenant_bound] Journey 1 clock % is not this tenant''s', v_clock.clock_key;
  end if;

  -- ATTRIBUTION. The writer is the server-established one, never a payload
  -- field. This is not one of the shared append invariants -- it is the record
  -- layer's own attribution rule and carries no invariant id, because it does
  -- not exist on the JavaScript side: there, the writer is derived from the live
  -- actor before a store can be constructed at all.
  if new.written_by_actor_id <> ops.portfolio_writer_actor_id() then
    raise exception 'Journey 1 clock revision actor does not match the authenticated writer context';
  end if;

  -- IDENTITY. The clock is the one this revision's OWN ORIGIN derives.
  if ops.j1_clock_identity_digest(v_clock.tenant, new.origin_receipt_digest,
       new.origin_at, new.origin_benchmark_manifest_digest) is distinct from v_clock.clock_key then
    raise exception '[j1_clock_identity_derived_from_origin] Journey 1 clock revision is being filed under %, but its own origin derives a different clock. A clock is addressed by its kernel origin and never by a name a caller chose',
      v_clock.clock_key;
  end if;

  -- THE AUTHORITATIVE SCOPE. A clock with NO scope binding is exactly the clock
  -- a second origin could have opened beside a running one, so a revision for an
  -- unbound clock is refused here rather than stored and explained afterwards.
  -- ops.j1_clock_bind_scope writes the binding earlier in the same transaction;
  -- this clause is what makes that mandatory for a direct caller of
  -- ops.j1_clock_append_revision who never runs the JavaScript at all.
  select * into v_binding from ops.j1_clock_scope_binding where clock_key = v_clock.clock_key;
  if not found then
    raise exception '[j1_clock_scope_binds_one_clock] Journey 1 clock % has no authoritative scope binding; bind the accepted scope with ops.j1_clock_bind_scope before appending. An unbound clock is one a second origin could have created beside another, and this rail does not store one',
      v_clock.clock_key;
  end if;
  if v_binding.tenant is distinct from v_clock.tenant then
    raise exception '[j1_clock_scope_sealed_at_creation] Journey 1 clock % is bound to a scope of another tenant', v_clock.clock_key;
  end if;

  -- CONTENT. It rebuilds to the digest it is filed under, or it is not the
  -- history it claims to be. Everything below this line diffs two histories, and
  -- diffing content that does not hash to its own digest would be meaningless.
  v_error := ops.j1_clock_revision_integrity_error(new.id);
  if v_error is not null then
    raise exception 'Journey 1 clock revision is not a storable history: %', v_error;
  end if;

  -- THE COMPARE-AND-SWAP.
  select coalesce(max(revision_ordinal), -1) into v_head_ordinal
    from ops.j1_clock_revision where clock_id = new.clock_id and id <> new.id;
  if new.prior_history_digest is null then
    if v_head_ordinal >= 0 then
      raise exception '[j1_clock_exact_prior_history_digest] Journey 1 clock % already has history; a null prior is a creation and a clock is created once. A caller-chosen label cannot restart it, because the identity is derived from the kernel origin',
        v_clock.clock_key;
    end if;
    if new.revision_ordinal <> 0 then
      raise exception '[j1_clock_exact_prior_history_digest] a creating revision is ordinal 0';
    end if;
    return null;
  end if;

  if v_head_ordinal < 0 then
    raise exception '[j1_clock_exact_prior_history_digest] Journey 1 clock % has no history, so there is no prior digest to match; create it with an explicit null prior',
      v_clock.clock_key;
  end if;
  select * into v_prior from ops.j1_clock_revision
   where clock_id = new.clock_id and revision_ordinal = v_head_ordinal;
  if new.revision_ordinal <> v_head_ordinal + 1 then
    raise exception '[j1_clock_exact_prior_history_digest] Journey 1 clock revision ordinal % does not follow the head at %',
      new.revision_ordinal, v_head_ordinal;
  end if;
  if new.prior_history_digest is distinct from v_prior.history_digest then
    raise exception '[j1_clock_exact_prior_history_digest] stale prior history digest for clock %: the head is %, this revision names %. Another writer appended first and this revision was computed against a history that is no longer the head',
      v_clock.clock_key, v_prior.history_digest, new.prior_history_digest;
  end if;
  -- The head must still be intact, or an already-corrupted head could authorize
  -- an illegal append.
  v_error := ops.j1_clock_revision_integrity_error(v_prior.id);
  if v_error is not null then
    raise exception 'Journey 1 clock head is not a readable history: %', v_error;
  end if;

  -- THE ORIGIN AND THE BASE DEADLINE IT PRODUCED ARE NEVER REWRITTEN. The base
  -- deadline rides along because the kernel derives it from the origin alone: a
  -- base deadline that moved means the origin moved, whatever the origin digest
  -- still says.
  perform ops.j1_clock_assert_same('j1_clock_origin_never_rewritten', 'origin_receipt_digest',
    v_prior.origin_receipt_digest, new.origin_receipt_digest);
  perform ops.j1_clock_assert_same('j1_clock_origin_never_rewritten', 'origin_at',
    v_prior.origin_at, new.origin_at);
  perform ops.j1_clock_assert_same('j1_clock_origin_never_rewritten', 'origin_benchmark_manifest_digest',
    v_prior.origin_benchmark_manifest_digest, new.origin_benchmark_manifest_digest);
  perform ops.j1_clock_assert_same('j1_clock_origin_never_rewritten', 'origin_receipt_ttl_policy_ms',
    v_prior.origin_receipt_ttl_policy_ms, new.origin_receipt_ttl_policy_ms);
  perform ops.j1_clock_assert_same('j1_clock_origin_never_rewritten', 'base_deadline_at',
    v_prior.base_deadline_at, new.base_deadline_at);
  perform ops.j1_clock_assert_same('j1_clock_origin_never_rewritten', 'base_deadline_resolution',
    v_prior.base_deadline_resolution, new.base_deadline_resolution);

  -- THE COMPLETION SEALS. Once a completion is recorded, all five are identical
  -- in every later revision. The kernel refuses a PROJECTION that changes them;
  -- this refuses a WRITE that does, which the kernel cannot see.
  if v_prior.completion_receipt_digest is not null then
    perform ops.j1_clock_assert_same('j1_clock_completion_seals_never_changed', 'completion_receipt_digest',
      v_prior.completion_receipt_digest, new.completion_receipt_digest);
    perform ops.j1_clock_assert_same('j1_clock_completion_seals_never_changed', 'completion_observed_at',
      v_prior.completion_observed_at, new.completion_observed_at);
    perform ops.j1_clock_assert_same('j1_clock_completion_seals_never_changed', 'completion_receipt_ttl_policy_ms',
      v_prior.completion_receipt_ttl_policy_ms, new.completion_receipt_ttl_policy_ms);
    perform ops.j1_clock_assert_same('j1_clock_completion_seals_never_changed', 'completion_artifact_digest',
      v_prior.completion_artifact_digest, new.completion_artifact_digest);
    perform ops.j1_clock_assert_same('j1_clock_completion_seals_never_changed', 'completion_fixture_set_digest',
      v_prior.completion_fixture_set_digest, new.completion_fixture_set_digest);
  end if;

  -- A DURABLY RECORDED MISS NEVER UN-STICKS.
  if v_prior.miss_at is not null then
    perform ops.j1_clock_assert_same('j1_clock_recorded_miss_never_removed', 'miss_at',
      v_prior.miss_at, new.miss_at);
  end if;

  -- THE PRIOR EVENT CHAIN IS AN EXACT PREFIX OF THE NEXT. This is what makes a
  -- lost event, a removed miss event and an erased pause or amendment approval
  -- all unwritable, whichever field a writer tried to change.
  select count(*) into v_prior_events from ops.j1_clock_revision_event where revision_id = v_prior.id;
  select count(*) into v_new_events from ops.j1_clock_revision_event where revision_id = new.id;
  if v_new_events < v_prior_events then
    raise exception '[j1_clock_events_are_append_only] this revision carries % events where the head carries %; events are appended, never dropped',
      v_new_events, v_prior_events;
  end if;
  for v_row in
    select p.ordinal, p.event_digest as prior_digest, n.event_digest as next_digest
      from ops.j1_clock_revision_event p
      left join ops.j1_clock_revision_event n
        on n.revision_id = new.id and n.ordinal = p.ordinal
     where p.revision_id = v_prior.id
     order by p.ordinal
  loop
    if v_row.next_digest is distinct from v_row.prior_digest then
      raise exception '[j1_clock_events_are_append_only] event % is not the event the head recorded at that position: head %, supplied %',
        v_row.ordinal, v_row.prior_digest, coalesce(v_row.next_digest, 'nothing');
    end if;
  end loop;

  -- NO BACKDATING. `evaluated_at` is kernel time and compared as an instant, so
  -- the same instant spelled `Z` and `+00:00` is not a move. `recorded_at` is
  -- server time; the comparison is non-strict because two revisions written in
  -- one transaction share now().
  if new.evaluated_at::timestamptz < v_prior.evaluated_at::timestamptz then
    raise exception '[j1_clock_no_backdated_evaluation] this revision was evaluated at %, before the head at %',
      new.evaluated_at, v_prior.evaluated_at;
  end if;
  if new.recorded_at < v_prior.recorded_at then
    raise exception '[j1_clock_no_backdated_evaluation] the server clock moved backwards between two revisions of clock %',
      v_clock.clock_key;
  end if;

  -- DELIBERATELY NOT CONSTRAINED, because a plausible-looking guard here would
  -- be WRONG and would make the only writable answer the false one:
  --   * paused_ms is NOT monotone. A pause whose end was unknown is counted to
  --     the evaluation instant; when the blocker's actual end is honestly
  --     reported later, the credited hours GO DOWN.
  --   * due_at may move forward PAST an existing miss_at, when a pause approved
  --     before the deadline is reported after the miss. That never erases the
  --     miss -- which is why the miss rule is stated on miss_at and never on
  --     due_at.
  --   * current_benchmark_manifest_digest may change: that is what a
  --     partner-signed amendment does. The ORIGIN manifest is the immutable one.
  --   * status may move between the kernel's own values. This rail does not
  --     decide status and does not police its transitions; the one shape it
  --     refuses is the contradiction Q008.D1 names, at the table constraint and
  --     again in the integrity reader.
  return null;
end;
$$;

comment on function ops.j1_clock_append_guard() is
  'The authoritative Journey 1 clock append precondition, checked from the persisted rows at commit: current state schema, bound tenant, authenticated writer, an identity derived from the revision''s own origin, content that rebuilds to its digest, the exact prior-history compare-and-swap, an unchanged origin and base deadline, unchanged completion seals, a preserved recorded miss, an event chain the head''s is an exact prefix of, and no backdating. It deliberately does not constrain paused_ms, due_at, the current benchmark manifest or the status transition; see the body for why each would be wrong.';

drop trigger if exists j1_clock_append_guard on ops.j1_clock_revision;
create constraint trigger j1_clock_append_guard
  after insert on ops.j1_clock_revision
  deferrable initially deferred
  for each row execute function ops.j1_clock_append_guard();

-- ---------------------------------------------------------------------------
-- THE READBACK. Deterministic, zero-write, and honest about what it is not.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_clock_history(p_clock_key text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_clock ops.j1_clock%rowtype; v_head ops.j1_clock_revision%rowtype; v_error text;
begin
  select * into v_clock from ops.j1_clock where clock_key = p_clock_key;
  if not found then
    return jsonb_build_object(
      'clock_key', p_clock_key, 'exists', false,
      'record_layer_cannot_prove', ops.j1_clock_record_layer_cannot_prove(),
      'deadline_accepted_by_record_layer', false);
  end if;
  select * into v_head from ops.j1_clock_revision
   where clock_id = v_clock.id order by revision_ordinal desc limit 1;
  if not found then
    raise exception '[j1_clock_content_rebuilds_to_its_digest] Journey 1 clock % exists with no revisions; this record layer cannot produce the history it holds',
      p_clock_key using errcode = 'integrity_constraint_violation';
  end if;
  -- A TAMPERED READBACK REFUSES. It does not fall back to an earlier healthy
  -- revision and does not report a corrupted clock as no clock: both would turn
  -- corruption into ordinary source.
  v_error := ops.j1_clock_revision_integrity_error(v_head.id);
  if v_error is not null then
    raise exception 'Journey 1 clock % failed integrity: %', p_clock_key, v_error
      using errcode = 'integrity_constraint_violation';
  end if;
  return jsonb_build_object(
    'clock_key', v_clock.clock_key,
    'tenant', v_clock.tenant,
    'exists', true,
    'clock_ref', to_jsonb(v_clock.clock_ref),
    -- WHICH AUTHORITATIVE SCOPE HOLDS THIS CLOCK. `false` is a real answer: an
    -- unbound clock is exactly the shape a second origin could have created
    -- beside another, and a reader sees that rather than an absent field.
    'clock_scope_bound', exists (select 1 from ops.j1_clock_scope_binding b
                                  where b.clock_key = v_clock.clock_key),
    'clock_scope_key', (select b.clock_scope_key from ops.j1_clock_scope_binding b
                         where b.clock_key = v_clock.clock_key),
    'clock_scope_ref', (select b.clock_scope_ref from ops.j1_clock_scope_binding b
                         where b.clock_key = v_clock.clock_key),
    'state_schema_version', v_head.state_schema_version,
    'revision_count', (select count(*) from ops.j1_clock_revision where clock_id = v_clock.id),
    'head_revision_ordinal', v_head.revision_ordinal,
    'history_digest', v_head.history_digest,
    -- BOTH DIGESTS ARE EXPOSED ON PURPOSE: the STORED one is what the append
    -- claimed, the RECOMPUTED one is what the rows say now, and a reader that
    -- only ever saw one of them could not tell a tampered revision from a
    -- healthy one.
    'recomputed_history_digest', ops.j1_clock_history_digest(v_head.id),
    -- SERVER TIME and KERNEL TIME, both reported, neither standing in for the
    -- other.
    'recorded_at', v_head.recorded_at,
    'evaluated_at', v_head.evaluated_at,
    'history', ops.j1_clock_history_state(v_head.id),
    'revisions', ops.j1_clock_revisions(p_clock_key),
    -- Said out loud on every read. A consumer that saw only `status` would
    -- otherwise be entitled to assume this database had checked something.
    'record_layer_cannot_prove', ops.j1_clock_record_layer_cannot_prove(),
    'deadline_accepted_by_record_layer', false,
    'clock_started_by_this_record_layer', false,
    'effects', jsonb_build_object(
      'creates_effect', false, 'database_writes', 0, 'network_calls', 0,
      'provider_actions', 0, 'notifications', 0, 'schedules', 0,
      'deployments', 0, 'activations', 0, 'acceptances', 0));
end;
$$;

comment on function ops.j1_clock_history(text) is
  'Deterministic zero-write readback of one Journey 1 clock: the head rebuilt from its stored rows, both the stored and the recomputed digest, the full revision chain, and an explicit statement of what this record layer cannot prove. A tampered head refuses rather than serving an older revision.';

-- ---------------------------------------------------------------------------
-- THE ONLY WRITE PATH. It derives its own actor and accepts none.
--
-- IDEMPOTENCY IS A REPLAY, NOT A SECOND WRITE. The key is looked up first: an
-- exact replay returns the row that already exists, and the same key presented
-- with different content is refused rather than quietly writing a second row or
-- silently returning the first.
-- ---------------------------------------------------------------------------
create or replace function ops.j1_clock_append_revision(
  p_clock_key text, p_tenant text, p_clock_ref text,
  p_expected_prior_history_digest text, p_idempotency_key uuid,
  p_history_digest text, p_scalars jsonb, p_pause_intervals jsonb,
  p_events jsonb, p_provenance jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_existing ops.j1_clock_revision%rowtype;
  v_clock ops.j1_clock%rowtype; v_actor uuid; v_revision uuid;
  v_ordinal integer; v_derived text; v_live text; v_row jsonb;
begin
  if p_idempotency_key is null then
    raise exception '[j1_clock_idempotency_key_binds_its_payload] a Journey 1 clock append must carry an idempotency key';
  end if;
  select * into v_existing from ops.j1_clock_revision where idempotency_key = p_idempotency_key;
  if found then
    -- EVERY BINDING PARAMETER IS COMPARED. A replay that matched on the content
    -- digest but named a different clock or a different prior is a different
    -- request wearing the same key.
    if (select c.clock_key from ops.j1_clock c where c.id = v_existing.clock_id) is distinct from p_clock_key
       or v_existing.history_digest is distinct from p_history_digest
       or v_existing.prior_history_digest is distinct from p_expected_prior_history_digest then
      raise exception '[j1_clock_idempotency_key_binds_its_payload] idempotency key % was already used for a different Journey 1 clock revision',
        p_idempotency_key;
    end if;
    return ops.j1_clock_revision_json(v_existing.id) || jsonb_build_object('replayed', true);
  end if;

  v_actor := ops.portfolio_writer_actor_id();

  -- IDENTITY IS DERIVED, NEVER ACCEPTED. The caller supplies a key; it is only
  -- ever compared against the one this revision's own origin produces.
  v_derived := ops.j1_clock_identity_digest(p_tenant,
    p_scalars ->> 'origin_receipt_digest', p_scalars ->> 'origin_at',
    p_scalars ->> 'origin_benchmark_manifest_digest');
  if v_derived is distinct from p_clock_key then
    raise exception '[j1_clock_identity_derived_from_origin] the supplied clock key is not the one this origin derives: derived %, supplied %',
      v_derived, p_clock_key;
  end if;

  -- The writer's half of the lock protocol described at
  -- ops.j1_clock_child_frozen_after_next_revision(). Taken before the head is
  -- read, so a neighbour's append settles first and the head read below is a
  -- settled answer rather than a stale one.
  perform ops.j1_clock_lock(p_clock_key);

  select * into v_clock from ops.j1_clock where clock_key = p_clock_key;
  if not found then
    if p_expected_prior_history_digest is not null then
      raise exception '[j1_clock_exact_prior_history_digest] Journey 1 clock % does not exist, so there is no prior digest to match; create it with an explicit null prior',
        p_clock_key;
    end if;
    insert into ops.j1_clock(clock_key, tenant, clock_ref, state_schema_version,
      origin_receipt_digest, origin_at, origin_benchmark_manifest_digest, created_by_actor_id)
    values (p_clock_key, p_tenant, p_clock_ref, p_scalars ->> 'schema_version',
      p_scalars ->> 'origin_receipt_digest', p_scalars ->> 'origin_at',
      p_scalars ->> 'origin_benchmark_manifest_digest', v_actor)
    returning * into v_clock;
  elsif v_clock.tenant is distinct from p_tenant then
    raise exception '[j1_clock_tenant_bound] Journey 1 clock % belongs to another tenant', p_clock_key;
  end if;

  select coalesce(max(revision_ordinal), -1) + 1 into v_ordinal
    from ops.j1_clock_revision where clock_id = v_clock.id;

  -- THE GUARD'S DEFERRAL MODE IS OWNED BY THIS FUNCTION, FOR THE LENGTH OF THIS
  -- CALL, AND IS SET BEFORE THE PARENT ROW EXISTS.
  --
  -- SET CONSTRAINTS is TRANSACTION-SCOPED, not statement-scoped: the IMMEDIATE
  -- below outlives the call that issued it. Without this line the second append
  -- in one transaction would insert its revision row while the guard was still
  -- IMMEDIATE from the FIRST call, so the trigger would fire at the end of that
  -- INSERT -- before this function had inserted a single event or pause-interval
  -- child. The guard reads the persisted children (an empty event chain, a
  -- digest over content that is not there yet) and would refuse a PERFECTLY
  -- VALID append, by a name that describes nothing the caller did.
  --
  -- It is unconditional because the mode cannot be read back: a caller may have
  -- entered the transaction under SET CONSTRAINTS ALL IMMEDIATE, and a previous
  -- call of this function may have left it IMMEDIATE for the same reason. Both
  -- arrive here identically and both are corrected the same way.
  --
  -- IT DEFERS NOTHING PAST THIS CALL. The forced IMMEDIATE below still validates
  -- this revision before the function returns; deferral is narrowed to the
  -- window between the parent insert and its own complete children, which is the
  -- only window in which the guard could read an incomplete revision.
  execute 'set constraints ops.j1_clock_append_guard deferred';

  insert into ops.j1_clock_revision(
    clock_id, revision_ordinal, idempotency_key, prior_history_digest, history_digest,
    state_schema_version, origin_receipt_digest, origin_at, origin_benchmark_manifest_digest,
    current_benchmark_manifest_digest, origin_receipt_ttl_policy_ms,
    base_deadline_at, base_deadline_resolution, due_at, paused_ms, status, miss_at,
    completion_receipt_digest, completion_observed_at, completion_receipt_ttl_policy_ms,
    completion_artifact_digest, completion_fixture_set_digest, evaluated_at,
    computed_by, projection_schema_version, verifier_ref, input_authority, written_by_actor_id)
  values (v_clock.id, v_ordinal, p_idempotency_key,
    p_expected_prior_history_digest, p_history_digest,
    p_scalars ->> 'schema_version', p_scalars ->> 'origin_receipt_digest',
    p_scalars ->> 'origin_at', p_scalars ->> 'origin_benchmark_manifest_digest',
    p_scalars ->> 'current_benchmark_manifest_digest',
    (p_scalars ->> 'origin_receipt_ttl_policy_ms')::bigint,
    p_scalars ->> 'base_deadline_at', p_scalars ->> 'base_deadline_resolution',
    p_scalars ->> 'due_at', (p_scalars ->> 'paused_ms')::bigint,
    p_scalars ->> 'status', p_scalars ->> 'miss_at',
    p_scalars ->> 'completion_receipt_digest', p_scalars ->> 'completion_observed_at',
    (p_scalars ->> 'completion_receipt_ttl_policy_ms')::bigint,
    p_scalars ->> 'completion_artifact_digest', p_scalars ->> 'completion_fixture_set_digest',
    p_scalars ->> 'evaluated_at',
    -- PROVENANCE IS PINNED, NOT COPIED. computed_by, the projection schema and
    -- input_authority are CHECK-constrained to single values, so a direct writer
    -- cannot widen them into a claim of verification. verifier_ref NAMES the
    -- installed verifier and is the only free field; naming is not proving.
    'mcp-server/src/journey-one-clock.v5.js',
    'doctorcre-v5-journey-one-clock-projection.v2',
    p_provenance ->> 'verifier_ref',
    'trusted_projection_not_independently_verified_by_this_record_layer',
    v_actor)
  returning id into v_revision;

  insert into ops.j1_clock_revision_pause_interval(revision_id, ordinal, pause_id, ends_at)
  select v_revision, (value ->> 'ordinal')::integer, value ->> 'pause_id', value ->> 'ends_at'
    from jsonb_array_elements(coalesce(p_pause_intervals, '[]'::jsonb));

  insert into ops.j1_clock_revision_event(
    revision_id, ordinal, event_type, happened_at, kernel_recorded_at,
    evidence_digest, previous_event_digest, event_digest)
  select v_revision, (value ->> 'ordinal')::integer, value ->> 'type',
         value ->> 'at', value ->> 'recorded_at', value ->> 'evidence_digest',
         value ->> 'previous_event_digest', value ->> 'event_digest'
    from jsonb_array_elements(coalesce(p_events, '[]'::jsonb));

  -- THE CALLER'S HASH IS NEVER THE ANSWER. It is compared here against the
  -- digest the rows just written produce, and the guard compares it again over
  -- the settled rows a few lines below, where it is forced to run.
  v_live := ops.j1_clock_history_digest(v_revision);
  if p_history_digest is distinct from v_live then
    raise exception '[j1_clock_claimed_history_digest_is_never_trusted] the named history digest is not the one these rows produce: named %, computed %',
      p_history_digest, v_live;
  end if;

  -- FORCE THE DEFERRED APPEND GUARD TO RUN HERE, over the COMPLETE rows -- the
  -- revision, its pause intervals and its whole event chain -- rather than at
  -- COMMIT. Two reasons, and the second one matters more than it looks: a caller
  -- gets the named refusal at the point of the call instead of a surprise at
  -- commit, and a ROLLBACK-ONLY PROOF FIXTURE can exercise the guard at all -- a
  -- deferred constraint that only ever fires at commit is one no
  -- transaction-scoped test can reach.
  --
  -- Issued through EXECUTE rather than as a bare statement so it is handed
  -- straight to the SQL engine and does not depend on PL/pgSQL's own statement
  -- parser accepting SET CONSTRAINTS.
  execute 'set constraints ops.j1_clock_append_guard immediate';

  -- AND RESTORED BEFORE RETURNING, because the line above would otherwise stay
  -- in force for the whole transaction and break the NEXT append in it: that
  -- one's revision row would be validated at the end of its own INSERT, against
  -- children this function had not written yet. The guard is DEFERRABLE
  -- INITIALLY DEFERRED by declaration, so restoring DEFERRED returns the
  -- transaction to the trigger's declared state rather than to some state this
  -- function invented.
  --
  -- IT WEAKENS NOTHING. Every revision this function writes has already been
  -- validated, one line above, over its own settled rows; nothing is left
  -- pending for commit to catch, and no caller can batch past the check by
  -- deferring the constraint, because the check does not happen at commit.
  -- A caller who deliberately entered under SET CONSTRAINTS ALL IMMEDIATE gets
  -- that same guarantee -- validation at the point of the call -- and this
  -- function does not restore their ad-hoc mode, which it has no way to read.
  --
  -- THE REFUSING PATH NEEDS NO RESTORE AND DOES NOT GET ONE. If the forced check
  -- above raises, this line is never reached -- and it does not have to be: the
  -- raise aborts the transaction, or the subtransaction of a caller that caught
  -- it, and a constraint-mode change is rolled back with everything else that
  -- subtransaction did. A refusal therefore cannot leave IMMEDIATE behind for
  -- the next append either.
  execute 'set constraints ops.j1_clock_append_guard deferred';

  v_row := ops.j1_clock_revision_json(v_revision);
  return v_row || jsonb_build_object('replayed', false);
end;
$$;

comment on function ops.j1_clock_append_revision(text,text,text,text,uuid,text,jsonb,jsonb,jsonb,jsonb) is
  'The only way to append a Journey 1 clock revision. The writer, the clock identity and the history digest are all DERIVED; the caller supplies content, an idempotency key and the exact prior-history digest it believes is the head. It creates the clock row when the prior is an explicit null, refuses a stale prior, replays an exact idempotent repeat and refuses a key whose payload changed. It records a computation with scoped provenance: it accepts no deadline, admits no receipt, verifies no input and starts nothing. It owns the append guard''s deferral mode for the length of the call -- DEFERRED before the revision row is inserted, IMMEDIATE once its children are complete, DEFERRED again before returning -- so repeated appends in one transaction, and callers who entered under SET CONSTRAINTS ALL IMMEDIATE, are each validated over complete rows rather than over a revision whose children do not exist yet.';

-- ---------------------------------------------------------------------------
-- Grants. Reads reach the ordinary bundles. DIRECT INSERT IS GRANTED TO NOBODY:
-- every write goes through the definer function above, which derives its own
-- actor, so a writer holding a raw connection cannot attribute a revision to
-- someone else or step around the guard.
--
-- No role is created by this file. Every role named below already exists.
-- ---------------------------------------------------------------------------
grant select on ops.j1_clock, ops.j1_clock_scope_binding, ops.j1_clock_revision,
  ops.j1_clock_revision_pause_interval, ops.j1_clock_revision_event
  to carr_reader, carr_writer, carr_authority;

revoke insert, update, delete, truncate on ops.j1_clock, ops.j1_clock_scope_binding,
  ops.j1_clock_revision,
  ops.j1_clock_revision_pause_interval, ops.j1_clock_revision_event
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function
  ops.j1_clock_identity_domain_tag(), ops.j1_clock_state_schema(),
  ops.j1_clock_legacy_state_schemas(),
  ops.j1_clock_scope_domain_tag(), ops.j1_clock_origin_gate_id(),
  ops.j1_clock_terminus_gate_id(), ops.j1_clock_scope_digest(jsonb),
  ops.j1_clock_scope_binding_json(uuid), ops.j1_clock_scope_bindings(text,text),
  ops.j1_clock_identity_digest(text,text,text,text),
  ops.j1_clock_assert_same(text,text,anyelement,anyelement),
  ops.j1_clock_record_layer_cannot_prove(),
  ops.j1_clock_history_preimage_of(jsonb,jsonb,jsonb),
  ops.j1_clock_history_digest_of(jsonb,jsonb,jsonb),
  ops.j1_clock_event_digest(text,text,text,text,text),
  ops.j1_clock_revision_scalars(uuid),
  ops.j1_clock_revision_pause_interval_rows(uuid), ops.j1_clock_revision_event_rows(uuid),
  ops.j1_clock_history_preimage(uuid), ops.j1_clock_history_digest(uuid),
  ops.j1_clock_history_state(uuid), ops.j1_clock_revision_integrity_error(uuid),
  ops.j1_clock_row(text), ops.j1_clock_revision_json(uuid), ops.j1_clock_head(text),
  ops.j1_clock_revision_by_idempotency_key(uuid), ops.j1_clock_revisions(text),
  ops.j1_clock_history(text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

grant execute on function
  ops.j1_clock_identity_domain_tag(), ops.j1_clock_state_schema(),
  ops.j1_clock_legacy_state_schemas(),
  ops.j1_clock_scope_domain_tag(), ops.j1_clock_origin_gate_id(),
  ops.j1_clock_terminus_gate_id(), ops.j1_clock_scope_digest(jsonb),
  ops.j1_clock_scope_binding_json(uuid), ops.j1_clock_scope_bindings(text,text),
  ops.j1_clock_identity_digest(text,text,text,text),
  ops.j1_clock_assert_same(text,text,anyelement,anyelement),
  ops.j1_clock_record_layer_cannot_prove(),
  ops.j1_clock_history_preimage_of(jsonb,jsonb,jsonb),
  ops.j1_clock_history_digest_of(jsonb,jsonb,jsonb),
  ops.j1_clock_event_digest(text,text,text,text,text),
  ops.j1_clock_revision_scalars(uuid),
  ops.j1_clock_revision_pause_interval_rows(uuid), ops.j1_clock_revision_event_rows(uuid),
  ops.j1_clock_history_preimage(uuid), ops.j1_clock_history_digest(uuid),
  ops.j1_clock_history_state(uuid), ops.j1_clock_revision_integrity_error(uuid),
  ops.j1_clock_row(text), ops.j1_clock_revision_json(uuid), ops.j1_clock_head(text),
  ops.j1_clock_revision_by_idempotency_key(uuid), ops.j1_clock_revisions(text),
  ops.j1_clock_history(text)
  to carr_reader, carr_writer, carr_jobs, carr_authority;

-- The lock opener and the append reach the writer bundle only. There is NO
-- authority-only verb on this rail and no humanOnly gate: recording a
-- computation is ordinary trusted-writer work, and pretending it were a partner
-- act would be inventing an authority this slice does not hold.
revoke all on function ops.j1_clock_lock(text), ops.j1_clock_scope_lock(text),
  ops.j1_clock_bind_scope(text,jsonb),
  ops.j1_clock_append_revision(text,text,text,text,uuid,text,jsonb,jsonb,jsonb,jsonb)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.j1_clock_lock(text), ops.j1_clock_scope_lock(text),
  ops.j1_clock_bind_scope(text,jsonb),
  ops.j1_clock_append_revision(text,text,text,text,uuid,text,jsonb,jsonb,jsonb,jsonb)
  to carr_writer, carr_authority;
