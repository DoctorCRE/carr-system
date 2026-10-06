-- DoctorCRE v5 slice V5-J102: durable persistence for the healthcare CRE
-- lifecycle -- Prospect through Client/Engagement/Assignment/Deal (requirements
-- Q069, Q072, Q077-Q083, Q094-Q096, Q103; gate journey-one-production-accepted).
--
-- NUMBERED AS migrations/0704_cre_lifecycle.sql, byte for byte. This file is
-- the reviewed source; the migration is an exact copy of it (the store parity
-- suite asserts the two are identical), applied in one transaction with its SCAC
-- seal, 0705. It creates no role and no user.
--
-- WHAT THIS IS. cre-lifecycle.v5.js can judge a transition against loaded state
-- and loaded evidence, and cre-lifecycle-store.v5.js can build the exact
-- envelopes for it. Neither can store anything. This file is the missing durable
-- half: authoritative current state per subject, append-only events beside it,
-- the first-party business records evidence is read from, the external
-- Salesforce references, the correction receipts, and the one writer that
-- applies a whole coupled transition or none of it.
--
-- IT IS NOT A SECOND DECISION LAYER. The transition table, the refusal matrix
-- and the coupled-fact sets live in the kernel and are not restated here. What
-- this file enforces is the half a kernel cannot: that the state a decision was
-- taken against has not moved, that the EXACT evidence is still what it was when
-- read, that the whole coupled set lands together, and that nobody writes any of
-- it except through a registered writer that derives its own actor.
--
-- THE ONE THING IT DOES TRANSCRIBE, and why. ops.j102_admission_policy() below
-- carries the kernel's OWN exported transition and evidence contracts -- which
-- actor class may perform each transition, which operation performs it, which
-- subjects and fields it may write, which evidence kinds bound to which subject
-- it requires. Not because a second opinion is wanted, but because
-- ops.j102_apply_transition is granted to carr_writer as well as carr_authority,
-- and a JavaScript assertion is not a control on a caller holding that grant. The
-- transcription is asserted EQUAL to the kernel's exports, contract by contract,
-- by the Node parity suite; two copies with a comparison between them are one
-- contract with two readers, and two without one are a future contradiction. This
-- file invents no state value, no actor class, no evidence kind and no business
-- rule of its own, and the parity test is what keeps that true.
--
-- WHAT IT TRANSCRIBES BEYOND THE DECLARATION TABLE, and why that was not
-- optional. The admission map also carries, per transition, the EXACT resulting
-- value of every field the kernel's evaluator writes, the exact set of subjects
-- it writes them on, and the exact set of events it appends. Declaring only
-- WHICH FIELDS a transition may move is not a control on the result: a routine
-- `open-assignment` call may move `assignment_phase`, and `committed` is a
-- commitment performed with none of a commitment's evidence. The same gap
-- admitted an arbitrary `open_negotiation_count`, a `pending_deal_id` pointing at
-- a deal that was never created, a cleared reference kept, a deleted key, a
-- coupled write sent as a subset, and an event of any kind on a correctly bound
-- subject. Every one of those is now compared against the committed row, the
-- other subjects in the same call and the evidence re-read under the lock.
--
-- AND ops.j102_apply_transition STILL CREATES NO PRIMARY SUBJECT. A transition
-- advances a subject that ALREADY EXISTS. An earlier revision of this file
-- admitted a proposed primary subject with a null compare-and-swap operand and
-- called it a bootstrap: it was not one, because a created primary has no
-- committed row for the transition's own prerequisites, instrument kind and
-- prior-state conditions to be checked against, and a direct caller could
-- therefore seed a deal that was born executed or an assignment that was born
-- committed. That path is refused by name and is NOT reopened below. Inside the
-- transition writer, creation remains admitted only for the two COUPLED subjects
-- the kernel itself creates -- the engagement of establish-client-and-engagement
-- and the pending deal of commit-winning-property -- in their exact declared
-- shape, and both require a primary that is already committed.
--
-- THE FIRST ROW OF A CHAIN NOW HAS ITS OWN DOOR, AND IT IS A DIFFERENT FUNCTION
-- WITH A DIFFERENT MAP. ops.j102_initialize_subject below creates exactly one
-- subject of exactly one of three kinds -- a prospect relationship, an assignment
-- under an already ACTIVE engagement held by a CLIENT, a property negotiation
-- under a still-open assignment -- each in the EARLIEST DECLARED STATE of its
-- kind and in a shape the admission map fixes field by field. It performs no
-- transition, checks no transition prerequisite and skips none: every one of them
-- remains mandatory for the transition that follows, which is why the two writers
-- are separate rather than one writer with a flag.
--
-- WHY THAT DOOR CARRIES NO EVIDENCE, since it is the one place a reader should
-- expect some. Every evidence kind in this rail binds to a SUBJECT, a first-party
-- record carries that binding in its own typed columns, and the record writer
-- takes the binding at write time -- so an assignment mandate about assignment A
-- cannot exist before assignment A does, and cannot be the evidence for creating
-- it. What the initialization writer checks instead is the PARENT CHAIN, re-read
-- and compare-and-swapped under its own locks, plus a created shape that carries
-- no lifecycle claim: a prospect is not a client, a created assignment is not an
-- opened one, and an LOI draft is neither a submission nor a Deal.
--
-- WHICH DATABASE IT MAY BE APPLIED TO. A FRESH one, or one whose J102 relations
-- already carry EXACTLY the shape below. Every DDL here is idempotent, which is
-- the right idiom for re-applying the same shape and the wrong one for a database
-- holding an EARLIER candidate's shape -- the create would be a silent no-op and
-- the first write would fail deep inside a writer on a missing column. The
-- preflight block below therefore REFUSES to apply the file in that case, naming
-- the relation and the columns. It is not a migration: it alters nothing,
-- backfills nothing, renumbers nothing and drops no relation that holds data.
--
-- NOTHING IS RE-DERIVED THAT ALREADY HAS A HOME. The tenant, the canonical JSON,
-- the digest, the instant parser, the server clock and the authenticated
-- principal all come from ops.f01_* in domain.sql, and this file CALLS them
-- rather than restating them. Two canonicalizers that agree today are two that
-- can disagree after one edit, and a second principal derivation would be a
-- second answer to "who is writing". The prerequisite block below refuses to
-- apply this file at all against a database that does not carry them.
--
-- THE ONE APPROVAL READER IS PRIVATE AND ALWAYS RAISES, deliberately, on the
-- same terms as ops.benchmark_gate_zero_outcome(): see
-- ops.j102_typed_approval() below. Q077's "approved representation equivalent"
-- and Q095's "explicit approved exception" both rest on a typed authenticated
-- approval, and nothing in this record layer PRODUCES one. Refusing is the
-- honest state; the alternative would be inventing the business policy the
-- approval is supposed to carry.
--
-- FIVE THINGS ARE DELIBERATELY NOT DONE HERE, because each would move an
-- authority this slice does not hold:
--
--   1. NO SALESFORCE MAPPING. ops.j102_salesforce_reference stores the
--      opportunity's own name and phase and a link to a DoctorCRE subject. There
--      is no column, function or view anywhere below that turns a Salesforce
--      phase into a lifecycle state, and ops.j102_read exposes none.
--   2. NO LEGACY REWRITE. The compatibility view PROJECTS the new records into
--      the old shape for reading. It is not updatable, nothing writes back
--      through it, and no legacy table is renamed, dropped or altered.
--   3. NO CALLER RETIREMENT. ops.j102_migration_readiness() reports that the old
--      interface may not be retired, for every input, because no verified caller
--      census exists. It is a reader, and it says no.
--   4. NO TOUR. Journey 3 owns active Tour behaviour. No relation, function or
--      view here creates, activates or reads one.
--   5. NO F01 SCHEMA PATCH. Evidence has to name the subject it advances, and an
--      F01 document carries no lifecycle binding. Rather than adding a column to
--      a schema this slice does not own, the association lives in the J102-scoped
--      ops.j102_evidence_subject_link below, written by its own registered
--      partner-only producer. Nothing here alters, extends or writes to any
--      ops.f01_* relation; F01 remains the sole authority for document identity
--      and document state, and an association asserts neither.

-- ---------------------------------------------------------------------------
-- Prerequisites. This file REFUSES TO APPLY rather than creating a second copy
-- of anything domain.sql already owns.
-- ---------------------------------------------------------------------------
do $$
declare
  v_missing text[] := array[]::text[];
  v_name text;
begin
  foreach v_name in array array[
    'ops.f01_tenant()', 'ops.f01_canonical_json(jsonb)', 'ops.f01_digest_jsonb(jsonb)',
    'ops.f01_is_digest_ref(text)', 'ops.f01_instant(text)', 'ops.f01_instant_text(timestamptz)',
    'ops.f01_now_text()', 'ops.f01_context_actor_slug()', 'ops.f01_principal()',
    'ops.f01_read(text,jsonb)', 'ops.f01_stored_artifact(text)'
  ] loop
    if to_regprocedure(v_name) is null then
      v_missing := v_missing || v_name;
    end if;
  end loop;
  if array_length(v_missing, 1) > 0 then
    raise exception 'j102_prerequisites_missing: domain.sql must be applied first; absent: %',
      array_to_string(v_missing, ', ');
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- HIGH-2 -- THE SHAPE PREFLIGHT, and what it deliberately is NOT.
--
-- Every DDL below is `create ... if not exists`, which is the right idiom for a
-- file that may be applied twice to the SAME shape and the wrong one for a file
-- whose relations have CHANGED shape since an earlier candidate. Applied to a
-- database where a PREVIOUS candidate already created ops.j102_first_party_record
-- with three fewer columns, the create is a silent no-op, the old shape survives,
-- and the first record-lifecycle-fact write fails deep inside a writer with
-- `column "bound_subject_kind" does not exist`. That is a confusing failure in
-- the wrong place, and it is the most likely way running the SQL fixture against
-- a previously seeded database goes wrong.
--
-- SO THE FILE REFUSES TO APPLY instead, naming the relation and the missing
-- columns. IT IS NOT A MIGRATION: it carries no ordinal, it ALTERs nothing, it
-- backfills nothing, it assigns no version to any existing row, and it drops
-- nothing that holds data. The only remedy it names is the honest one -- a fresh
-- database, or a database whose J102 relations already carry exactly this shape.
--
-- THIS CANDIDATE HAS NEVER BEEN EXECUTED, so no deployed old shape exists from
-- this slice. The preflight exists for the case where somebody applied an EARLIER
-- draft of this same candidate to a scratch database and then applies this one.
-- ---------------------------------------------------------------------------
do $$
declare
  v_relation text;
  v_columns text[];
  v_column text;
  v_absent text[];
  v_complaints text[] := array[]::text[];
begin
  for v_relation, v_columns in
    select * from (values
      ('j102_subject_current', array['tenant', 'subject_kind', 'subject_id', 'envelope',
        'envelope_digest', 'state_digest', 'parent_id', 'deal_state', 'updated_by',
        'sponsoring_partner', 'updated_at']),
      ('j102_subject_event', array['tenant', 'event_seq', 'subject_kind', 'subject_id',
        'event_kind', 'transition_id', 'envelope', 'envelope_digest', 'event_digest',
        'recorded_by', 'sponsoring_partner', 'recorded_at', 'idempotency_key']),
      -- The three columns and the author class the corrections added. A database
      -- holding the older four-column-short shape is exactly the case this block
      -- exists to name.
      ('j102_first_party_record', array['tenant', 'record_kind', 'record_id', 'envelope',
        'envelope_digest', 'record_digest', 'bound_subject_kind', 'bound_subject_id',
        'closing_date', 'recorded_by', 'recorded_by_class', 'recorded_at', 'idempotency_key']),
      ('j102_evidence_subject_link', array['tenant', 'link_seq', 'evidence_source', 'evidence_ref',
        'version_no', 'content_digest', 'subject_kind', 'subject_id', 'envelope',
        'envelope_digest', 'link_digest', 'associated_by', 'associated_by_class',
        'associated_at', 'idempotency_key']),
      ('j102_salesforce_reference', array['tenant', 'opportunity_id', 'reference_seq',
        'opportunity_name', 'opportunity_phase', 'linked_subject_kind', 'linked_subject_id',
        'observed_at', 'envelope', 'envelope_digest', 'reference_digest', 'recorded_by',
        'recorded_at', 'idempotency_key']),
      ('j102_correction_receipt', array['tenant', 'receipt_seq', 'subject_kind', 'subject_id',
        'correction_record_id', 'reason', 'prior_state_digest', 'envelope', 'envelope_digest',
        'receipt_digest', 'corrected_by', 'corrected_at', 'idempotency_key']),
      ('j102_reconciliation_item', array['tenant', 'item_seq', 'subject_kind', 'subject_id',
        'conflict_kind', 'base_version_digest', 'current_version_digest', 'envelope',
        'envelope_digest', 'item_digest', 'proposed_by', 'recorded_at']),
      ('j102_idempotency', array['tenant', 'operation', 'idempotency_key', 'request_digest',
        'actor_slug', 'result', 'result_digest', 'claimed_at', 'settled_at'])
    ) as t(name, columns)
  loop
    -- A relation that does not exist yet is the ordinary fresh case and is fine.
    if to_regclass('ops.' || v_relation) is null then
      continue;
    end if;
    v_absent := array[]::text[];
    foreach v_column in array v_columns loop
      if not exists (
        select 1 from information_schema.columns
         where table_schema = 'ops' and table_name = v_relation and column_name = v_column) then
        v_absent := v_absent || v_column;
      end if;
    end loop;
    if array_length(v_absent, 1) > 0 then
      v_complaints := v_complaints ||
        (v_relation || ' is missing ' || array_to_string(v_absent, ', '));
    end if;
  end loop;
  if array_length(v_complaints, 1) > 0 then
    raise exception 'j102_incompatible_existing_schema: this database already holds J102 relations of an EARLIER shape, and this file alters nothing: %. Apply it to a fresh database, or drop the earlier J102 candidate objects deliberately first. Nothing here migrates, backfills or renumbers an existing row.',
      array_to_string(v_complaints, '; ');
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- Guards.
--
-- WHY A CALL-STACK CHECK RATHER THAN A FLAG, restated because it is the same
-- reasoning F01 records and the same trap: a transaction-local flag is something
-- a caller with DML rights could set for itself, so it would prove nothing.
-- PG_CONTEXT names the actual PL/pgSQL frames beneath the trigger, so the guard
-- can require that a write genuinely arrived through a registered ops.j102_*
-- writer.
--
-- THE SCHEMA HALF, and how far it can honestly be taken. PG_CONTEXT renders each
-- frame through format_procedure, which OMITS the schema when the function is
-- visible on the current search_path -- and this guard's own search_path puts
-- `ops` ahead of `public`, so a genuine ops.j102_apply_transition frame prints
-- unqualified. Requiring a literal `ops.` prefix would therefore refuse every
-- legitimate write, which is why the prefix stays optional. What makes the
-- optional prefix safe is that an unqualified `j102_apply_transition(` frame can
-- only be the function that search_path resolves that name to, and the
-- application-time check below asserts that every writer name resolves to the
-- ops one under exactly this search_path. A same-named function in `public` is
-- shadowed by ops and prints QUALIFIED, so `public.j102_apply_transition(` does
-- not satisfy the pattern.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_guard_direct_dml()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_context text;
begin
  get diagnostics v_context = pg_context;
  if regexp_replace(v_context, 'PL/pgSQL function (ops\.)?j102_guard_direct_dml\(\)[^\n]*', '', 'g')
       !~ 'PL/pgSQL function (ops\.)?j102_(apply_transition|initialize_subject|record_first_party_fact|record_evidence_subject_link|record_salesforce_reference|record_correction|record_reconciliation_item|run_migration_shadow|claim_idempotency|settle_idempotency)\('
  then
    raise exception 'j102_direct_dml_refused: %.% is written only through the registered ops.j102_* writers',
      tg_table_schema, tg_table_name using errcode = '42501';
  end if;
  return new;
end;
$$;

create or replace function ops.j102_guard_append_only()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  raise exception 'j102_append_only_violation: %.% admits INSERT only; % refused',
    tg_table_schema, tg_table_name, tg_op using errcode = '42501';
  return null;
end;
$$;

create or replace function ops.j102_guard_no_truncate()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  raise exception 'j102_truncate_refused: %.% is append-only history',
    tg_table_schema, tg_table_name using errcode = '42501';
  return null;
end;
$$;

-- ---------------------------------------------------------------------------
-- The relations.
--
-- EVERY RECORD IS AN ENVELOPE, exactly as F01 stores one. `envelope` is the
-- canonical preimage the record hashes to, `envelope -> 'record'` is the domain
-- record the kernel produced, and both digests are CHECK-bound to a
-- recomputation inside PostgreSQL -- so a readback that agrees with the stored
-- claim has genuinely verified it, and a tampered row cannot read back healthy.
--
-- CURRENT STATE IS ONE TABLE, NOT FIVE. Q079 keeps the four entities distinct in
-- MEANING; it does not require four physical homes, and one keyed relation makes
-- the compare-and-swap, the lock ordering and the append-only history uniform
-- across every subject kind rather than five near-identical copies that can
-- drift. The kind is a closed enum and every kind-specific invariant that can be
-- expressed structurally is expressed as a constraint or a partial index below.
-- ---------------------------------------------------------------------------

create table if not exists ops.j102_subject_current (
  tenant            text not null,
  subject_kind      text not null check (subject_kind in
                      ('relationship', 'engagement', 'assignment', 'property_negotiation', 'deal')),
  subject_id        text not null check (subject_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  envelope          jsonb not null,
  envelope_digest   text not null,
  state_digest      text not null,
  -- The one structural parent, extracted so the partial indexes below can reach
  -- it, and CHECK-bound to the envelope so it can never drift from the hashed
  -- bytes. Relationship rows have none.
  parent_id         text,
  -- Extracted for the pending-deal index and for the compatibility projection,
  -- likewise CHECK-bound to the envelope.
  deal_state        text,
  updated_by        text not null,
  -- OWNER RULING (c), 2026-09-25: every write is recorded UNDER THE SPONSORING
  -- PARTNER. Derived by ops.j102_sponsoring_partner() from the authenticated
  -- login -- the partner's own slug on a partner's session, the server-verified
  -- sponsor on an agent's -- and never from a caller field.
  sponsoring_partner text not null check (sponsoring_partner in ('joe', 'dell')),
  updated_at        timestamptz not null,
  primary key (tenant, subject_kind, subject_id),
  constraint j102_subject_tenant check (tenant = ops.f01_tenant()),
  constraint j102_subject_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_subject_state_digest
    check (state_digest = ops.f01_digest_jsonb(envelope -> 'record' -> 'state')),
  constraint j102_subject_kind_matches_envelope
    check (subject_kind = envelope -> 'record' ->> 'subject_kind'),
  constraint j102_subject_id_matches_envelope
    check (subject_id = envelope -> 'record' ->> 'subject_id'),
  constraint j102_subject_parent_matches_envelope
    check (parent_id is not distinct from coalesce(
      envelope -> 'record' -> 'state' ->> 'relationship_id',
      envelope -> 'record' -> 'state' ->> 'engagement_id',
      envelope -> 'record' -> 'state' ->> 'assignment_id')),
  constraint j102_subject_deal_state_matches_envelope
    check (deal_state is not distinct from envelope -> 'record' -> 'state' ->> 'deal_state'),
  constraint j102_subject_updated_by_matches_envelope
    check (updated_by = envelope -> 'record' ->> 'updated_by'),
  constraint j102_subject_sponsor_matches_envelope
    check (sponsoring_partner = envelope -> 'record' ->> 'sponsoring_partner'),
  -- HIGH-5, STRUCTURALLY. WHAT ESTABLISHED THIS SUBJECT'S STATE is a field
  -- ops.j102_subject returns as the row's provenance and the transition receipt
  -- echoes back in its readback, and it lived inside the hashed bytes bound to
  -- nothing. The BINDING is the writer's -- ops.j102_apply_transition refuses any
  -- envelope whose established_by_transition is not the transition actually being
  -- applied -- and these are the structural floor beneath it: a row whose
  -- provenance is absent, blank, of the wrong type, written under a foreign
  -- schema or attributed to another tenant cannot exist to be read back as
  -- authoritative. They decide nothing; WHICH transition established a subject
  -- is the writer's answer and is never re-derived here.
  constraint j102_subject_provenance_present
    check (jsonb_typeof(envelope -> 'record' -> 'established_by_transition') = 'string'
       and btrim(envelope -> 'record' ->> 'established_by_transition') <> ''),
  constraint j102_subject_record_schema_version
    check (envelope -> 'record' ->> 'schema_version'
             = 'doctorcre-v5-j102-stored-lifecycle-subject.v1'),
  constraint j102_subject_record_tenant
    check (envelope -> 'record' ->> 'tenant' = ops.f01_tenant()),
  -- THE ENVELOPE'S OWN DURABLE DIGEST CLAIM, which the store writes INSIDE the
  -- hashed bytes and which ops.j102_subject used to "verify" by recomputing the
  -- same bytes and comparing them to themselves. Bound here, so that arm of
  -- j102_verify_envelope compares a stored claim against a recomputation on this
  -- relation exactly as it already does on every other one.
  constraint j102_subject_record_digest_claim
    check (envelope ->> 'record_digest' = ops.f01_digest_jsonb(envelope -> 'record'))
);

comment on table ops.j102_subject_current is
  'Authoritative current state for one DoctorCRE lifecycle subject. state_digest is recomputed from the hashed bytes on every read and is the compare-and-swap operand for every transition. Updated only by ops.j102_apply_transition.';

-- Q078 and Q095, structurally. An assignment holds at most ONE pending Deal, so
-- a second commitment racing the first cannot both succeed: the index refuses
-- the loser rather than leaving two pending deals for a human to discover.
create unique index if not exists j102_one_pending_deal_per_assignment
  on ops.j102_subject_current (tenant, parent_id)
  where subject_kind = 'deal' and deal_state = 'pending';

-- Owner ruling 2026-09-25: at most ONE ACTIVE property negotiation per
-- (tenant, assignment, property). Dead negotiations and a selected winner are
-- outside the predicate, so they never block a new one. The state list is the
-- admission map's `active_negotiation_states`, restated here because an index
-- predicate must be immutable; the parity suite compares the two.
create unique index if not exists j102_one_active_negotiation_per_property
  on ops.j102_subject_current (tenant, parent_id, (envelope -> 'record' -> 'state' ->> 'property_id'))
  where subject_kind = 'property_negotiation'
    and (envelope -> 'record' -> 'state' ->> 'negotiation_state')
        in ('loi_drafted', 'loi_submitted', 'loi_countered', 'loi_accepted');

comment on index ops.j102_one_pending_deal_per_assignment is
  'Q078/Q095 structurally: one pending Deal per Assignment. Cancelled and closed deals are excluded, so an assignment may hold many historical deals and only one live one.';

create table if not exists ops.j102_subject_event (
  tenant            text not null,
  event_seq         bigserial primary key,
  subject_kind      text not null,
  subject_id        text not null,
  event_kind        text not null,
  transition_id     text not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  event_digest      text not null,
  recorded_by       text not null,
  -- Owner ruling (c): the partner this history row is recorded under, derived
  -- exactly as on the subject row.
  sponsoring_partner text not null check (sponsoring_partner in ('joe', 'dell')),
  recorded_at       timestamptz not null,
  idempotency_key   text not null,
  constraint j102_event_tenant check (tenant = ops.f01_tenant()),
  constraint j102_event_sponsor_matches_envelope
    check (sponsoring_partner = envelope -> 'record' ->> 'sponsoring_partner'),
  constraint j102_event_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_event_digest_bound
    check (event_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_event_kind_matches_envelope
    check (event_kind = envelope -> 'record' -> 'event' ->> 'event_kind'),
  constraint j102_event_subject_matches_envelope
    check (subject_kind = envelope -> 'record' -> 'event' ->> 'subject_kind'
       and subject_id = envelope -> 'record' -> 'event' ->> 'subject_id'),
  constraint j102_event_transition_matches_envelope
    check (transition_id = envelope -> 'record' ->> 'transition_id'),
  -- HIGH-6, STRUCTURALLY. The comment below promises that every event names the
  -- exact evidence references it rested on, and nothing made that true: the
  -- array could be empty, absent or a scalar and the row stored perfectly well.
  -- WHICH references are the right ones is the writer's answer -- it compares
  -- them, one for one, against what ops.j102_recheck_evidence actually re-read
  -- under the lock -- and this is the floor beneath it: a history row that cites
  -- no evidence at all cannot exist.
  -- AND THE ONE CLASS OF HISTORY ROW THAT CITES NOTHING, which is narrow and is
  -- named rather than admitted by loosening the rule. An INITIALIZATION creates
  -- the first row of a chain and rests on no evidence, because no evidence in
  -- this rail can bind to a subject that does not exist yet; its event therefore
  -- carries an empty array, and that empty array is a positive statement. The
  -- three ids are written out so a TRANSITION still cannot append a history row
  -- citing nothing -- which is the property the constraint existed for. The Node
  -- parity suite asserts this list is exactly the kernel's
  -- V5_J102_INITIALIZATION_IDS.
  constraint j102_event_cites_evidence
    check (jsonb_typeof(envelope -> 'record' -> 'evidence_references') = 'array'
       and (jsonb_array_length(envelope -> 'record' -> 'evidence_references') > 0
            or transition_id in ('initialize-assignment', 'initialize-property-negotiation',
                                 'initialize-prospect-relationship'))),
  constraint j102_event_record_schema_version
    check (envelope -> 'record' ->> 'schema_version'
             = 'doctorcre-v5-j102-stored-lifecycle-event.v1'),
  constraint j102_event_record_tenant
    check (envelope -> 'record' ->> 'tenant' = ops.f01_tenant()),
  -- The KERNEL's own event schema version, on the nested event the kernel built.
  constraint j102_event_payload_schema_version
    check (envelope -> 'record' -> 'event' ->> 'schema_version'
             = 'doctorcre-v5-j102-lifecycle-event.v1'),
  constraint j102_event_record_digest_claim
    check (envelope ->> 'record_digest' = ops.f01_digest_jsonb(envelope -> 'record'))
);

comment on table ops.j102_subject_event is
  'Append-only lifecycle history. Every event names the transition that produced it and the exact evidence references it rested on -- the references being what the writer RE-READ under its own lock, compared one for one against the event, never the caller''s account of them -- so the history says what a change was judged against and not only what changed. Q096: a cancelled deal keeps every event it ever had.';

create index if not exists j102_subject_event_by_subject
  on ops.j102_subject_event (tenant, subject_kind, subject_id, event_seq);

-- The first-party business records evidence is read from: a mandate, a winning
-- property commitment, a diligence outcome, a closing settlement and its actual
-- date, an invoice, a payment, a completion, a deal failure and its reason, a
-- correction.
--
-- WHY THIS IS NOT A CALLER BOOLEAN WITH EXTRA STEPS. A row here is written by an
-- authenticated actor through a registered writer, carries the server's own
-- instant, is append-only, and is addressable afterwards. A transition names one
-- by id and the record layer loads it; the fact never travels inside the
-- transition request. That is the difference between "the broker recorded that
-- the closing happened on the 14th" and "the caller said closed: true".
-- THE TYPED SUBJECT BINDING IS PART OF THE ROW, and it is the correction this
-- table most needed. A record used to say WHAT happened and WHO recorded it and
-- never WHICH deal, assignment or client it happened to, so one closing
-- settlement with one date could close any number of unrelated deals and a
-- commitment recorded for assignment A could commit assignment B. The columns
-- below are CHECK-bound to the hashed envelope, so the binding cannot be edited
-- beside the bytes it is supposed to describe.
create table if not exists ops.j102_first_party_record (
  tenant            text not null,
  record_kind       text not null check (record_kind in
                      ('assignment_mandate', 'winning_property_commitment', 'diligence_outcome',
                       'closing_settlement', 'deal_failure', 'invoice', 'payment', 'completion',
                       'lifecycle_correction')),
  record_id         text not null check (record_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  envelope          jsonb not null,
  envelope_digest   text not null,
  record_digest     text not null,
  bound_subject_kind text not null check (bound_subject_kind in
                      ('relationship', 'engagement', 'assignment', 'property_negotiation', 'deal')),
  bound_subject_id  text not null check (bound_subject_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  closing_date      timestamptz,
  recorded_by       text not null,
  -- H5. The author's own authorization class, stamped from the derived principal
  -- at write time. It is what makes "a partner stated this" a checkable property
  -- of the row rather than a property of whoever presents it later.
  recorded_by_class text not null check (recorded_by_class in
                      ('verified_partner', 'sponsored_agent')),
  recorded_at       timestamptz not null,
  idempotency_key   text not null,
  primary key (tenant, record_kind, record_id),
  constraint j102_fact_tenant check (tenant = ops.f01_tenant()),
  constraint j102_fact_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_fact_record_digest
    check (record_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_fact_kind_matches_envelope
    check (record_kind = envelope -> 'record' ->> 'record_kind'),
  constraint j102_fact_id_matches_envelope
    check (record_id = envelope -> 'record' ->> 'record_id'),
  constraint j102_fact_binding_matches_envelope
    check (bound_subject_kind = envelope -> 'record' ->> 'subject_kind'
       and bound_subject_id = envelope -> 'record' ->> 'subject_id'),
  constraint j102_fact_recorded_by_matches_envelope
    check (recorded_by = envelope -> 'record' ->> 'recorded_by'),
  constraint j102_fact_recorded_by_class_matches_envelope
    check (recorded_by_class = envelope -> 'record' ->> 'recorded_by_authorization_class'),
  -- H5 structurally: the four facts only a partner may state cannot be stored
  -- with any other author class, so an agent-authored closing date, winning
  -- property commitment, failure reason or correction proof does not exist to be
  -- laundered through a partner-performed transition later.
  constraint j102_fact_partner_authored_kinds
    check (record_kind not in ('winning_property_commitment', 'closing_settlement',
                               'deal_failure', 'lifecycle_correction')
        or recorded_by_class = 'verified_partner'),
  -- M1 structurally. A reason or detail that is not a JSON string stores
  -- perfectly well and then makes the record UNREADABLE as evidence, which turns
  -- a policy question into a thrown contract violation at read time.
  constraint j102_fact_reason_is_text
    check (jsonb_typeof(envelope -> 'record' -> 'reason') in ('string', 'null')),
  constraint j102_fact_detail_is_text
    check (jsonb_typeof(envelope -> 'record' -> 'detail') in ('string', 'null')),
  constraint j102_fact_supporting_document_is_ident
    check (envelope -> 'record' ->> 'supporting_document_id' is null
        or envelope -> 'record' ->> 'supporting_document_id'
             ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  -- Q094 structurally: a closing_settlement record without an actual closing
  -- date cannot exist, so no closing transition can ever find one to read.
  constraint j102_fact_closing_requires_date
    check (record_kind <> 'closing_settlement' or closing_date is not null),
  constraint j102_fact_closing_date_matches_envelope
    check (closing_date is not distinct from
      case when envelope -> 'record' ->> 'closing_date' is null then null
           else ops.f01_instant(envelope -> 'record' ->> 'closing_date') end),
  -- A reason is mandatory where the kernel's evidence contract requires one.
  -- Stated twice on purpose: once where the decision is made, once where the row
  -- lives, so a row that could never satisfy a transition cannot be stored.
  constraint j102_fact_reason_required
    check (record_kind not in ('deal_failure', 'lifecycle_correction')
        or btrim(coalesce(envelope -> 'record' ->> 'reason', '')) <> '')
);

comment on table ops.j102_first_party_record is
  'Append-only authenticated first-party business records, each bound to the exact subject it is about and carrying its author''s authorization class. A lifecycle transition names one by id and the record layer loads it; the fact never travels inside a transition request, which is what keeps a caller from asserting the outcome it is asking for.';

create index if not exists j102_fact_by_bound_subject
  on ops.j102_first_party_record (tenant, bound_subject_kind, bound_subject_id, record_kind);

-- ---------------------------------------------------------------------------
-- THE EVIDENCE -> SUBJECT ASSOCIATION, and why it lives here rather than in F01.
--
-- A first-party record can carry its own binding because this rail writes it. An
-- F01 DOCUMENT cannot: F01 owns document identity, versions, signature, validity
-- and version state, and it holds no lifecycle concept at all -- there is no
-- column on an F01 document that says which DoctorCRE deal it belongs to, and
-- adding one would be this slice editing a schema it does not own to make its
-- own problem easier. The same is true of a corporate artifact.
--
-- So the association is held HERE, scoped to J102, and it is a RECORD rather
-- than a claim on a request: one partner-authored, append-only, digest-bound row
-- saying that one exact document version, or one exact artifact, belongs to one
-- lifecycle subject. A transition reads it; nobody asserts it in passing.
--
-- IT IS PINNED, NOT NAMED. The association binds document_id + version_no +
-- content_digest, so a document that gains a version is not the document that
-- was associated and needs its own association. That is deliberately the same
-- rule the evidence pin itself follows, for the same reason: "the document still
-- exists" is not the question.
--
-- WHAT AN ASSOCIATION IS NOT. It is not a document, it does not create one, and
-- it asserts nothing whatever about signature, validity or version state -- every
-- one of those is read from F01 at judgement time and none of them is copied
-- here. A partner saying "this lease is that deal's lease" is not a partner
-- saying the lease is signed.
-- ---------------------------------------------------------------------------
create table if not exists ops.j102_evidence_subject_link (
  tenant            text not null,
  link_seq          bigserial primary key,
  evidence_source   text not null check (evidence_source in
                      ('f01_document', 'f01_corporate_artifact')),
  -- The document id, or the artifact digest. The pin's identity half.
  evidence_ref      text not null check (btrim(evidence_ref) <> ''),
  -- The document version. An artifact has none, and 0 says so rather than a null
  -- the unique index would have to work around.
  version_no        integer not null,
  -- The document version's content digest, or -- for an artifact, whose pin IS
  -- its digest -- the artifact digest again.
  content_digest    text not null,
  subject_kind      text not null check (subject_kind in
                      ('relationship', 'engagement', 'assignment', 'property_negotiation', 'deal')),
  subject_id        text not null check (subject_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  envelope          jsonb not null,
  envelope_digest   text not null,
  link_digest       text not null,
  associated_by     text not null,
  associated_by_class text not null check (associated_by_class = 'verified_partner'),
  associated_at     timestamptz not null,
  idempotency_key   text not null,
  constraint j102_link_tenant check (tenant = ops.f01_tenant()),
  constraint j102_link_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_link_digest_bound
    check (link_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_link_content_digest_shape check (ops.f01_is_digest_ref(content_digest)),
  constraint j102_link_version_matches_source
    check ((evidence_source = 'f01_document') = (version_no > 0)),
  constraint j102_link_matches_envelope
    check (evidence_source = envelope -> 'record' ->> 'evidence_source'
       and evidence_ref = envelope -> 'record' ->> 'evidence_ref'
       and content_digest = envelope -> 'record' ->> 'content_digest'
       and subject_kind = envelope -> 'record' ->> 'subject_kind'
       and subject_id = envelope -> 'record' ->> 'subject_id'
       and associated_by = envelope -> 'record' ->> 'associated_by'),
  -- M-c. THE ONE BINDING COLUMN THAT WAS NOT CHECK-BOUND TO ITS ENVELOPE.
  -- version_no is half of the pin -- "IT IS PINNED, NOT NAMED" above is a claim
  -- about (ref, version, digest) together -- and it was inserted from the record
  -- while every other binding column was verified against it. The CASE is not
  -- decoration: it guarantees the cast is evaluated only on the branch where the
  -- value really is a JSON number, so a string or an object refuses as a check
  -- violation rather than as a cast error from an unspecified evaluation order.
  constraint j102_link_version_matches_envelope
    check (case
             when jsonb_typeof(envelope -> 'record' -> 'version_no') = 'number'
               then version_no = (envelope -> 'record' ->> 'version_no')::integer
             else false
           end),
  -- An association says nothing about the document's own states, and a record
  -- claiming otherwise is refused rather than stored and ignored.
  constraint j102_link_asserts_no_document_state
    check ((envelope -> 'record' ->> 'asserts_document_state') = 'false'
       and (envelope -> 'record' ->> 'creates_document') = 'false')
);

comment on table ops.j102_evidence_subject_link is
  'The J102-scoped association between one EXACT evidence pin (an F01 document id + version + content digest, or a corporate artifact digest) and one lifecycle subject. Written only by ops.j102_record_evidence_subject_link, only by a verified partner, append-only. It creates no document and asserts no document state; F01 remains the sole authority for both.';

-- One association per (pin, subject). A repeat is the same fact, not a second one.
create unique index if not exists j102_evidence_subject_link_uq
  on ops.j102_evidence_subject_link
     (tenant, evidence_source, evidence_ref, version_no, content_digest, subject_kind, subject_id);

create table if not exists ops.j102_salesforce_reference (
  tenant            text not null,
  opportunity_id    text not null,
  reference_seq     bigserial primary key,
  -- SALESFORCE'S OWN LABELS, preserved verbatim and never interpreted. There is
  -- deliberately no mapping column, no derived lifecycle state, and no
  -- constraint relating either of these to any DoctorCRE vocabulary.
  opportunity_name  text not null check (btrim(opportunity_name) <> ''),
  opportunity_phase text not null check (btrim(opportunity_phase) <> ''),
  linked_subject_kind text check (linked_subject_kind in
                      ('relationship', 'engagement', 'assignment', 'deal')),
  linked_subject_id text,
  observed_at       timestamptz not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  reference_digest  text not null,
  recorded_by       text not null,
  recorded_at       timestamptz not null,
  idempotency_key   text not null,
  constraint j102_reference_tenant check (tenant = ops.f01_tenant()),
  constraint j102_reference_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_reference_digest_bound
    check (reference_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_reference_link_complete
    check ((linked_subject_kind is null) = (linked_subject_id is null)),
  constraint j102_reference_matches_envelope
    check (opportunity_id = envelope -> 'record' ->> 'opportunity_id'
       and opportunity_name = envelope -> 'record' ->> 'opportunity_name'
       and opportunity_phase = envelope -> 'record' ->> 'opportunity_phase')
);

comment on table ops.j102_salesforce_reference is
  'Q083: external corporate references, progressively linked. The Salesforce name and phase are stored as SALESFORCE facts about its own record. No column here is a DoctorCRE lifecycle state and nothing in this file maps one onto the other.';

create index if not exists j102_reference_by_opportunity
  on ops.j102_salesforce_reference (tenant, opportunity_id, reference_seq);

create table if not exists ops.j102_correction_receipt (
  tenant            text not null,
  receipt_seq       bigserial primary key,
  subject_kind      text not null,
  subject_id        text not null,
  correction_record_id text not null,
  reason            text not null check (btrim(reason) <> ''),
  prior_state_digest text not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  receipt_digest    text not null,
  corrected_by      text not null,
  corrected_at      timestamptz not null,
  idempotency_key   text not null,
  constraint j102_receipt_tenant check (tenant = ops.f01_tenant()),
  constraint j102_receipt_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_receipt_digest_bound
    check (receipt_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_receipt_prior_state_digest
    check (ops.f01_is_digest_ref(prior_state_digest)),
  constraint j102_receipt_matches_envelope
    check (subject_kind = envelope -> 'record' ->> 'subject_kind'
       and subject_id = envelope -> 'record' ->> 'subject_id'
       and corrected_by = envelope -> 'record' ->> 'corrected_by')
);

comment on table ops.j102_correction_receipt is
  'Q082/Q072: every manual correction leaves an append-only receipt naming the human who made it, the reason, the durable correction record it rests on, and the exact prior state digest. Nothing here overwrites history.';

-- Q103's visible reconciliation. A conflict that cannot be auto-merged lands
-- here with BOTH versions preserved, and it is resolved by a person.
create table if not exists ops.j102_reconciliation_item (
  tenant            text not null,
  item_seq          bigserial primary key,
  -- The same structural floor ops.j102_subject_current carries, for the same
  -- reason: a conflict about a kind this slice does not have is not a conflict
  -- anyone can resolve.
  subject_kind      text not null constraint j102_item_subject_kind check (subject_kind in
                      ('relationship', 'engagement', 'assignment', 'property_negotiation', 'deal')),
  subject_id        text not null,
  -- AND THE SAME FLOOR UNDER THE LABEL. These four are the kinds
  -- evaluateConcurrentEdit can file (V5_J102_CONFLICT_KINDS); the writer checks
  -- the admission map, and this restates it structurally so a row that reached
  -- the table another way still cannot carry a label nothing authored.
  conflict_kind     text not null constraint j102_item_conflict_kind check (conflict_kind in
                      ('uncharacterized_concurrent_change', 'overlapping_field_edit',
                       'material_class_edit', 'unclassified_field_edit')),
  base_version_digest text not null,
  current_version_digest text not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  item_digest       text not null,
  proposed_by       text not null,
  recorded_at       timestamptz not null,
  constraint j102_item_tenant check (tenant = ops.f01_tenant()),
  constraint j102_item_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_item_digest_bound
    check (item_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  -- Both sides, always. An item recording only the losing edit would make the
  -- resolution unreviewable, which is the same defect as resolving it silently.
  constraint j102_item_preserves_both_sides
    check (jsonb_typeof(envelope -> 'record' -> 'incoming_edits') = 'array'
       and jsonb_typeof(envelope -> 'record' -> 'concurrent_edits') = 'array'),
  -- VISIBLE, UNAPPLIED AND UNRESOLVED, AS ACTUAL JSON BOOLEANS. `->>` renders
  -- the string "false" and the boolean false identically, so the text comparison
  -- alone was satisfied by a caller that wrote `"resolved_by_machine": "false"`
  -- -- a value that reads as the right answer and is a string. The type is
  -- checked beside the value so the three properties a reader acts on are the
  -- three the row actually holds.
  constraint j102_item_resolution_flags_are_booleans
    check (jsonb_typeof(envelope -> 'record' -> 'resolved_by_machine') = 'boolean'
       and jsonb_typeof(envelope -> 'record' -> 'visible') = 'boolean'
       and jsonb_typeof(envelope -> 'record' -> 'applied') = 'boolean'),
  constraint j102_item_not_machine_resolved
    check ((envelope -> 'record' -> 'resolved_by_machine') = 'false'::jsonb
       and (envelope -> 'record' -> 'visible') = 'true'::jsonb
       and (envelope -> 'record' -> 'applied') = 'false'::jsonb)
);

comment on table ops.j102_reconciliation_item is
  'Q103: a lifecycle, financial, recipient or document conflict, visible and unresolved, with both versions preserved. Nothing in this file resolves one; a person does. The label is closed to the four kinds evaluateConcurrentEdit can file, the subject kind to the five this slice defines, and visible/applied/resolved_by_machine are pinned as JSON BOOLEANS rather than as text -- so a row here cannot read as already handled, and cannot carry a conflict kind nothing authored.';

create table if not exists ops.j102_idempotency (
  tenant            text not null,
  operation         text not null,
  idempotency_key   text not null,
  request_digest    text not null,
  actor_slug        text not null,
  result            jsonb,
  result_digest     text,
  claimed_at        timestamptz not null,
  settled_at        timestamptz,
  primary key (tenant, operation, idempotency_key),
  constraint j102_idempotency_tenant check (tenant = ops.f01_tenant()),
  constraint j102_idempotency_request_digest check (ops.f01_is_digest_ref(request_digest)),
  constraint j102_idempotency_result_digest
    check (result_digest is null or result_digest = ops.f01_digest_jsonb(result))
);

-- An idempotency key is bound to ONE operation and ONE payload, so a key can
-- never substitute one write for another.
create unique index if not exists j102_idempotency_key_operation_uq
  on ops.j102_idempotency (tenant, idempotency_key, operation);

-- ---------------------------------------------------------------------------
-- Trigger wiring. Every relation refuses direct DML; the history relations
-- additionally refuse UPDATE, DELETE and TRUNCATE.
-- ---------------------------------------------------------------------------
do $$
declare v_table text; v_append_only boolean;
begin
  for v_table, v_append_only in
    select * from (values
      ('j102_subject_current', false),
      ('j102_subject_event', true),
      ('j102_first_party_record', true),
      ('j102_evidence_subject_link', true),
      ('j102_salesforce_reference', true),
      ('j102_correction_receipt', true),
      ('j102_reconciliation_item', true),
      ('j102_idempotency', false)
    ) as t(name, append_only)
  loop
    execute format('drop trigger if exists %I on ops.%I', v_table || '_dml_guard', v_table);
    execute format(
      'create trigger %I before insert or update on ops.%I for each row execute function ops.j102_guard_direct_dml()',
      v_table || '_dml_guard', v_table);
    execute format('drop trigger if exists %I on ops.%I', v_table || '_no_truncate', v_table);
    execute format(
      'create trigger %I before truncate on ops.%I execute function ops.j102_guard_no_truncate()',
      v_table || '_no_truncate', v_table);
    if v_append_only then
      execute format('drop trigger if exists %I on ops.%I', v_table || '_append_only', v_table);
      execute format(
        'create trigger %I before update or delete on ops.%I for each row execute function ops.j102_guard_append_only()',
        v_table || '_append_only', v_table);
    end if;
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Envelope verification, and the readers.
-- ---------------------------------------------------------------------------

-- Recompute, never trust. A row whose stored digests no longer describe its own
-- bytes RAISES rather than reading back as healthy: a lifecycle record that
-- cannot prove its own integrity is not a record to make a decision from.
create or replace function ops.j102_verify_envelope(
  p_envelope jsonb, p_envelope_digest text, p_record_digest text, p_record_kind text)
returns jsonb language plpgsql immutable
set search_path = pg_catalog, ops, public
as $$
begin
  if p_envelope is null then
    raise exception 'j102_envelope_missing' using errcode = 'integrity_constraint_violation';
  end if;
  if (p_envelope ->> 'record_kind') is distinct from p_record_kind then
    raise exception 'j102_record_kind_mismatch: stored % expected %',
      p_envelope ->> 'record_kind', p_record_kind using errcode = 'integrity_constraint_violation';
  end if;
  if ops.f01_digest_jsonb(p_envelope) is distinct from p_envelope_digest then
    raise exception 'j102_envelope_digest_mismatch' using errcode = 'integrity_constraint_violation';
  end if;
  if ops.f01_digest_jsonb(p_envelope -> 'record') is distinct from p_record_digest then
    raise exception 'j102_record_digest_mismatch' using errcode = 'integrity_constraint_violation';
  end if;
  return jsonb_build_object(
    'record', p_envelope -> 'record',
    'record_digest', p_record_digest,
    'integrity', 'recomputed_from_committed_row');
end;
$$;

comment on function ops.j102_verify_envelope(jsonb,text,text,text) is
  'Recompute both digests of one stored J102 envelope from its committed bytes and raise on any mismatch. A readback that returns is a readback that verified.';

create or replace function ops.j102_subject(p_subject_kind text, p_subject_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.j102_subject_current%rowtype; v_verified jsonb;
begin
  select * into v_row from ops.j102_subject_current
   where tenant = ops.f01_tenant() and subject_kind = p_subject_kind and subject_id = p_subject_id;
  if not found then return null; end if;
  -- THE STORED CLAIM, not a recomputation of the same bytes compared to itself.
  -- This reader used to pass ops.f01_digest_jsonb(envelope -> 'record') as the
  -- EXPECTED record digest, which made that arm of j102_verify_envelope a
  -- tautology here while it was a real check on every other relation. The
  -- envelope's own durable record_digest -- written by the store, hashed inside
  -- the envelope, and CHECK-bound by j102_subject_record_digest_claim above -- is
  -- the claim, and comparing the recomputation to it is the check.
  v_verified := ops.j102_verify_envelope(v_row.envelope, v_row.envelope_digest,
    v_row.envelope ->> 'record_digest', 'stored_lifecycle_subject');
  -- The state digest is RECOMPUTED here too, not read off the column, so the
  -- compare-and-swap operand a caller receives is derived from the bytes rather
  -- than from a value that could have been written beside them.
  return jsonb_build_object(
    'subject_kind', v_row.subject_kind,
    'subject_id', v_row.subject_id,
    'state', v_verified -> 'record' -> 'state',
    'state_digest', ops.f01_digest_jsonb(v_verified -> 'record' -> 'state'),
    'established_by_transition', v_verified -> 'record' ->> 'established_by_transition',
    -- Q103's characterization anchor. It is inside the hashed envelope and
    -- CHECK-bound, so it is the stored row's own statement of the digest it
    -- replaced: when it equals a caller's base, exactly one committed write --
    -- the one named above -- lies between the two.
    'prior_state_digest', v_verified -> 'record' ->> 'prior_state_digest',
    'updated_by', v_row.updated_by,
    'sponsoring_partner', v_row.sponsoring_partner,
    'updated_at', ops.f01_instant_text(v_row.updated_at),
    'integrity', 'recomputed_from_committed_row');
end;
$$;

comment on function ops.j102_subject(text,text) is
  'One lifecycle subject''s verified current state and its recomputed compare-and-swap digest, or null when no such subject exists.';

create or replace function ops.j102_first_party_record(p_record_kind text, p_record_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.j102_first_party_record%rowtype; v_verified jsonb;
begin
  select * into v_row from ops.j102_first_party_record
   where tenant = ops.f01_tenant() and record_kind = p_record_kind and record_id = p_record_id;
  if not found then return null; end if;
  v_verified := ops.j102_verify_envelope(v_row.envelope, v_row.envelope_digest,
    v_row.record_digest, 'stored_first_party_record');
  return jsonb_build_object(
    'record', v_verified -> 'record',
    'record_digest', v_row.record_digest,
    'integrity', 'recomputed_from_committed_row');
end;
$$;

comment on function ops.j102_first_party_record(text,text) is
  'One verified first-party business record, or null. This is the ONLY door a lifecycle transition reads a business fact through, and every record it returns names the subject it is about.';

-- THE ASSOCIATION READER ASKS A YES/NO QUESTION, and the question is the point.
--
-- It takes the subject as a PARAMETER rather than returning whichever subject
-- the association happens to name, so the caller asks "is this exact document
-- version bound to THIS deal" and gets null when it is not. A reader that
-- returned the association's own subject would read identically on every happy
-- path and differ on exactly the case BLOCK-2 describes -- a perfectly authentic
-- lease, bound to somebody else's deal, presented against this one.
create or replace function ops.j102_evidence_subject_link(
  p_evidence_source text, p_evidence_ref text, p_version_no integer,
  p_content_digest text, p_subject_kind text, p_subject_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.j102_evidence_subject_link%rowtype; v_verified jsonb;
begin
  select * into v_row from ops.j102_evidence_subject_link
   where tenant = ops.f01_tenant()
     and evidence_source = p_evidence_source
     and evidence_ref = p_evidence_ref
     and version_no = coalesce(p_version_no, 0)
     and content_digest = p_content_digest
     and subject_kind = p_subject_kind
     and subject_id = p_subject_id;
  if not found then return null; end if;
  v_verified := ops.j102_verify_envelope(v_row.envelope, v_row.envelope_digest,
    v_row.link_digest, 'stored_evidence_subject_link');
  return jsonb_build_object(
    'record', v_verified -> 'record',
    'link_digest', v_row.link_digest,
    'integrity', 'recomputed_from_committed_row');
end;
$$;

comment on function ops.j102_evidence_subject_link(text,text,integer,text,text,text) is
  'Whether one EXACT evidence pin is associated with one named lifecycle subject, verified from committed bytes, or null. The subject is asked about rather than reported, so an authentic document bound to a different subject answers null.';

-- ---------------------------------------------------------------------------
-- THE PRIVATE FAIL-CLOSED APPROVAL READER.
--
-- This is the whole of the typed-approval binding, and it is a stub on purpose.
--
-- WHAT IS MISSING, PRECISELY. Any AUTHENTICATED RECORD IN THIS DATABASE of an
-- approval that a named class of agreement is a representation equivalent
-- (Q077), or that a named assignment may hold a second selected property or
-- lease-draft target (Q095). No table holds one because no workflow writes one,
-- so there is nothing here for a transition to bind to.
--
-- WHAT THIS FUNCTION REFUSES TO DO INSTEAD, because each would manufacture the
-- authority the absent record is supposed to carry: accept an approval from its
-- caller, read one out of configuration, derive one from a synthetic fixture,
-- treat an approval REFERENCE stored on an assignment row as the approval
-- itself, or treat "the reader is not built" as "the approval is not required".
--
-- IT IS GRANTED TO NOBODY. Exposing it would turn "this record layer cannot
-- authenticate a lifecycle approval" into a callable claim about approvals in
-- general, and a callable stub is the first step toward a configurable one. The
-- writers below reach it as the function owner, which is the only access it
-- needs. In practice no shipped path reaches it: the store's own
-- V5_J102_ABSENT_EVIDENCE_READERS registry turns those two evidence kinds into a
-- recordable policy refusal before it ever issues a read, so a caller learns
-- which fact is missing instead of receiving a database error. This function is
-- the same refusal one layer down, for any future writer that reaches for an
-- approval without going through that registry.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_typed_approval(p_approval_kind text, p_approval_ref text)
returns jsonb language plpgsql stable
set search_path = pg_catalog, ops, public
as $$
begin
  raise exception 'j102_typed_approval_unavailable: this record layer holds no authenticated % approval (ref %); no producer writes one, and no approval is inferred, defaulted or accepted from a caller',
    coalesce(p_approval_kind, 'lifecycle'), coalesce(p_approval_ref, 'unnamed')
    using errcode = '42501';
end;
$$;

comment on function ops.j102_typed_approval(text,text) is
  'PRIVATE fail-closed reader for Q077 representation-equivalence and Q095 multi-target-exception approvals. It always raises, because no producer writes such an approval and this database can authenticate none. Granted to no role. Landing a producer for one approval kind does not open the other.';

-- ---------------------------------------------------------------------------
-- Idempotency.
--
-- REPLAY BEFORE STATE, and the ordering is load-bearing rather than incidental:
-- a settled key must return its stored result even though the world has moved
-- since, and a replay that ran after the compare-and-swap would refuse a request
-- that had already succeeded. No writer below reads a subject, takes a lock or
-- evaluates a CAS above its claim.
-- ---------------------------------------------------------------------------

create or replace function ops.j102_replay_outcome(
  p_operation text, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.j102_idempotency%rowtype; v_actor text := ops.f01_context_actor_slug();
begin
  -- The closed write-operation vocabulary. A new writer MUST be added here or it
  -- cannot claim, replay or settle a key at all; nothing here is a wildcard.
  if p_operation not in (
    'record-lifecycle-fact', 'record-evidence-subject-link',
    'initialize-prospect-relationship', 'initialize-assignment',
    'initialize-property-negotiation',
    'record-representation-agreement', 'open-cre-assignment',
    'record-loi-submission', 'record-loi-acceptance', 'commit-winning-property',
    'record-deal-execution', 'record-diligence-outcome', 'record-deal-closing',
    'cancel-pending-deal', 'record-deal-axis', 'link-salesforce-reference',
    'record-lifecycle-correction', 'record-lifecycle-reconciliation',
    'run-migration-shadow') then
    raise exception 'j102_unknown_write_operation: %', p_operation using errcode = '22023';
  end if;
  if p_idempotency_key is null or length(p_idempotency_key) not between 1 and 200 then
    raise exception 'j102_idempotency_key_required' using errcode = '22023';
  end if;
  if p_request_digest is null or not ops.f01_is_digest_ref(p_request_digest) then
    raise exception 'j102_idempotency_request_digest_malformed' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'j102:request:' || ops.f01_tenant() || ':' || p_idempotency_key, 0));
  select * into v_row from ops.j102_idempotency
   where tenant = ops.f01_tenant() and operation = p_operation
     and idempotency_key = p_idempotency_key;
  if not found then return null; end if;
  if v_row.request_digest is distinct from p_request_digest then
    raise exception 'j102_idempotency_payload_mismatch: key % already binds a different payload',
      p_idempotency_key using errcode = '23505';
  end if;
  if v_row.actor_slug is distinct from v_actor then
    raise exception 'j102_idempotency_actor_mismatch: key % belongs to another actor',
      p_idempotency_key using errcode = '42501';
  end if;
  return v_row.result;
end;
$$;

create or replace function ops.j102_claim_idempotency(
  p_operation text, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  v_row ops.j102_idempotency%rowtype;
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
begin
  v_replay := ops.j102_replay_outcome(p_operation, p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  -- ON CONFLICT DO UPDATE takes the row lock, so a concurrent claim of the same
  -- key BLOCKS here and then reads the committed outcome, rather than racing
  -- past it and writing a second time.
  insert into ops.j102_idempotency as i
    (tenant, operation, idempotency_key, request_digest, actor_slug, claimed_at)
  values (ops.f01_tenant(), p_operation, p_idempotency_key, p_request_digest, v_actor, now())
  on conflict (tenant, operation, idempotency_key)
  do update set claimed_at = i.claimed_at
  returning * into v_row;
  if v_row.request_digest is distinct from p_request_digest then
    raise exception 'j102_idempotency_payload_mismatch: key % already binds a different payload',
      p_idempotency_key using errcode = '23505';
  end if;
  if v_row.actor_slug is distinct from v_actor then
    raise exception 'j102_idempotency_actor_mismatch: key % belongs to another actor',
      p_idempotency_key using errcode = '42501';
  end if;
  -- A key already used for a DIFFERENT operation is a substitution attempt.
  if exists (select 1 from ops.j102_idempotency
              where tenant = ops.f01_tenant() and idempotency_key = p_idempotency_key
                and operation <> p_operation) then
    raise exception 'j102_idempotency_operation_mismatch: key % already binds another operation',
      p_idempotency_key using errcode = '23505';
  end if;
  return v_row.result;
end;
$$;

create or replace function ops.j102_settle_idempotency(
  p_operation text, p_idempotency_key text, p_result jsonb)
returns jsonb language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  update ops.j102_idempotency
     set result = p_result, result_digest = ops.f01_digest_jsonb(p_result), settled_at = now()
   where tenant = ops.f01_tenant() and operation = p_operation
     and idempotency_key = p_idempotency_key;
  return p_result;
end;
$$;

-- ---------------------------------------------------------------------------
-- BLOCK-1 / BLOCK-2 -- THE CLOSED SQL ADMISSION MAP.
--
-- WHAT THIS IS, AND WHAT IT IS NOT. It is NOT a second decision layer and it
-- decides nothing the kernel decides: there is no business rule here, no new
-- state value, no new actor class, no new evidence kind and no policy that is not
-- already written in cre-lifecycle.v5.js. It is a TRANSCRIPTION of the kernel's
-- own exported contracts into the one place a caller holding the writer's EXECUTE
-- grant cannot go around -- and the Node suite asserts, contract by contract,
-- that the transcription is exact against `v5J102TransitionContract` and
-- `v5J102EvidenceContract`. Two copies that nothing compares are a future
-- contradiction; two copies with a parity check are one contract with two
-- readers, which is the only shape that closes a direct-writer gap at all.
--
-- WHY IT HAD TO EXIST. `ops.j102_apply_transition` is granted to carr_writer as
-- well as carr_authority, and carr_writer resolves to `sponsored_agent`. Before
-- this map the writer asked WHO the actor was and never WHAT CLASS it held, never
-- validated `p_transition_id` against any vocabulary at all, and never compared
-- the transition to the operation the diagnostics named. A sponsored agent on a
-- direct call could therefore perform `record-deal-closing`, `cancel-pending-deal`
-- or `commit-winning-property` -- the three the kernel and the store both hold to
-- a verified partner -- and could name any transition beside any operation. The
-- record layer already accepts exactly this restatement one table over
-- (j102_record_first_party_fact restates the four partner-AUTHORED record kinds,
-- and the relation restates them a third time as a CHECK) precisely so that H5 is
-- not a JavaScript assertion. This is the same move for the transition table.
--
-- THE FIVE THINGS IT LETS THE WRITER ASK, none of which it could ask before:
--   1. is `p_transition_id` a transition at all
--   2. may the operation the diagnostics name perform THAT transition
--   3. may this actor's DERIVED class perform it
--   4. which subject does it advance, which others may it write, and which FIELDS
--      of each may it move -- so an allowed operation is not an arbitrary rewrite
--   5. which evidence kinds does it require, from which source, bound to WHICH
--      subject and authored by which class
--
-- `writes` IS DERIVED, NOT INVENTED. For every transition the keys of `writes`
-- are exactly {subject_kind} union {the prefixes of coupled_facts}, and the
-- fields under each key are exactly the coupled facts for that kind plus the
-- three DERIVED COUNTERS AND MIRRORS the kernel also moves and does not list as
-- coupled facts: relationship.active_engagement_count,
-- assignment.open_negotiation_count and assignment.active_lease_draft_target_id.
-- Those three are named in `derived_fields` below so the parity test can subtract
-- them and prove the remainder is the coupled-fact set exactly.
--
-- ===========================================================================
-- WHAT THE SECOND CORRECTION ADDS, AND WHY `writes` WAS NOT ENOUGH.
--
-- `writes` answers "may this transition move that field". It does not answer
-- "to WHAT". A routine `open-assignment` call could therefore supply
-- `assignment_phase: "committed"`, or delete the key outright; `record-payment`
-- could write any payment_state it liked; `cancel-pending-deal` could clear the
-- deal reference and leave the counter at an arbitrary number. Field MOVABILITY
-- is not target validation, and every one of those is a permitted field with a
-- forbidden value.
--
-- So each transition now carries `subjects` and `events`, transcribed from the
-- kernel's own evaluator (applyTransition and axisResult in cre-lifecycle.v5.js)
-- rather than from its declaration table:
--
--   subjects[kind].role     primary or coupled -- and the FULL set is REQUIRED.
--                           A call that proposes the deal and omits the
--                           assignment `cancel-pending-deal` also returns is a
--                           subset of a coupled write, which Q082 refuses.
--   subjects[kind].mode     update, or create for the TWO subjects the kernel
--                           actually creates: the engagement of
--                           establish-client-and-engagement and the deal of
--                           commit-winning-property. Everything else must
--                           already exist. THE PRIMARY SUBJECT IS NEVER
--                           CREATABLE -- see the writer's own note.
--   .prior_conditions       the evaluator's per-subject refusals, checked
--                           against the COMMITTED row: an assignment holding a
--                           pending deal, a negotiation that is not the winner,
--                           a closing against unresolved diligence.
--   .effects                the EXACT resulting value of every field the
--                           transition moves, as an expression over the stored
--                           prior state, the other subjects in the same call and
--                           the evidence that was re-read under the lock. Every
--                           other field must be byte-identical to the committed
--                           row.
--   .creation_shape         for a created subject: the exact key set and the
--                           exact value of each key, so a creation cannot carry
--                           an extra field, omit one, or point at another
--                           assignment.
--   required_context        subjects the kernel requires to be LOADED and true
--                           without writing them -- the active engagement and
--                           the client relationship an assignment opens under.
--   events                  the exact event set: which kinds, on which subjects,
--                           AND the exact nested detail each one carries. Not
--                           "at least one event": a call cannot erase the history
--                           by sending an empty array, cannot append an event the
--                           transition does not produce, and cannot omit one it
--                           does. EVERY kind here is a literal read off a
--                           lifecycleEvent() call site; none is derived. The
--                           kernel's `payment_${level}` is a REASON_ID and has
--                           never been an event kind -- record-payment appends
--                           the constant `payment_recorded` -- and this map
--                           carries no derived-kind rule of any sort.
--   events[].detail         the EXACT value of every non-identity key the
--                           kernel's own lifecycleEvent() detail carries: the
--                           closing date on deal_closed, the cancellation reason
--                           on pending_deal_cancelled, the axis value on every
--                           axis event, the property and assignment a negotiation
--                           event names, and the evidence reference each event
--                           rested on. Written in the SAME effect vocabulary as
--                           the state targets, so a lying nested fact is refused
--                           by the machinery a lying state field already was.
--                           Three coupled events carry no evidence_reference at
--                           all, which is the kernel's shape and is transcribed
--                           rather than filled in.
--
-- NONE OF THIS IS A SECOND BUSINESS POLICY. Every value below is read off the
-- kernel's evaluator, and the Node suite proves it by running that evaluator on
-- real transitions and asserting the map PREDICTS its proposed_state and its
-- events exactly -- so a divergence is a test failure rather than a silent
-- second opinion.
-- ===========================================================================
-- ---------------------------------------------------------------------------
create or replace function ops.j102_admission_policy()
returns jsonb language sql immutable
set search_path = pg_catalog
as $fn$
select $policy$
{
  "policy_id": "j102-sql-admission.v5",
  "derived_from": "cre-lifecycle.v5.js v5J102TransitionContract + v5J102EvidenceContract + v5J102InitializationContract + V5_J102_CONFLICT_KINDS + V5_J102_FIELD_CLASS_REGISTRY + applyTransition/axisResult/lifecycleEvent/evaluateLifecycleInitialization/evaluateConcurrentEdit",
  "invented_business_policy": false,
  "subject_creation": "coupled_only_never_the_primary_subject",
  "initialization_writer": "ops.j102_initialize_subject",
  "initialization_requires_evidence": false,
  "initialization_performs_transition": false,
  "derived_event_kinds": false,
  "stored_subject_schema_version": "doctorcre-v5-j102-stored-lifecycle-subject.v1",
  "stored_event_schema_version": "doctorcre-v5-j102-stored-lifecycle-event.v1",
  "event_schema_version": "doctorcre-v5-j102-lifecycle-event.v1",
  "stored_subject_record_keys": ["schema_version", "tenant", "subject_kind", "subject_id",
    "state", "established_by_transition", "prior_state_digest", "updated_by",
    "sponsoring_partner", "updated_at"],
  "stored_event_record_keys": ["schema_version", "tenant", "event", "transition_id",
    "evidence_references", "recorded_by", "sponsoring_partner", "recorded_at"],
  "event_identity_keys": ["schema_version", "event_kind", "subject_kind", "subject_id"],
  "evidence_reference_keys": ["evidence_kind", "source", "reference"],
  "parent_reference_fields": {
    "relationship": "relationship_id",
    "engagement": "engagement_id",
    "assignment": "assignment_id",
    "deal": "pending_deal_id",
    "property_negotiation": null
  },
  "derived_fields": [
    "relationship.active_engagement_count",
    "assignment.open_negotiation_count",
    "assignment.active_lease_draft_target_id"
  ],
  "reconciliation": {
    "writer": "ops.j102_record_reconciliation_item",
    "conflict_kinds": ["uncharacterized_concurrent_change", "overlapping_field_edit",
      "material_class_edit", "unclassified_field_edit"],
    "field_classes": ["lifecycle", "financial", "recipient", "document", "routine"],
    "material_field_classes": ["lifecycle", "financial", "recipient", "document"],
    "caller_supplied_field_class_admitted": false,
    "unregistered_field_class": null,
    "routine_fields_registered": 0,
    "field_class_registry": {
      "active_engagement_count": "lifecycle",
      "party_id": "lifecycle",
      "active_lease_draft_target_id": "lifecycle",
      "assignment_id": "lifecycle",
      "assignment_phase": "lifecycle",
      "cancellation_reason": "lifecycle",
      "closing_date": "lifecycle",
      "closing_state": "lifecycle",
      "commission_agreement_state": "financial",
      "completion_state": "lifecycle",
      "content_digest": "document",
      "deal_state": "lifecycle",
      "diligence_state": "lifecycle",
      "document_id": "document",
      "effective_from": "lifecycle",
      "effective_to": "lifecycle",
      "engagement_id": "lifecycle",
      "engagement_state": "lifecycle",
      "execution_state": "lifecycle",
      "instrument_kind": "lifecycle",
      "invoice_state": "financial",
      "multi_target_exception_ref": "lifecycle",
      "negotiation_state": "lifecycle",
      "open_negotiation_count": "lifecycle",
      "payment_state": "financial",
      "pending_deal_id": "lifecycle",
      "property_id": "lifecycle",
      "relationship_id": "lifecycle",
      "relationship_state": "lifecycle",
      "representation_basis": "lifecycle",
      "selected_property_id": "lifecycle",
      "subject_id": "lifecycle",
      "subject_kind": "lifecycle",
      "supporting_document_id": "document",
      "version_no": "document"
    }
  },
  "active_negotiation_states": ["loi_drafted", "loi_submitted", "loi_countered", "loi_accepted"],
  "initializations": {
    "initialize-prospect-relationship": {
      "subject_kind": "relationship",
      "operations": ["initialize-prospect-relationship"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q069.D1", "Q077.D1", "Q079.D1"],
      "requires_evidence": false,
      "parent_subject_kind": null,
      "declared_identifiers": [],
      "optional_declared_identifiers": ["party_id"],
      "creation_shape": {
        "subject_kind": { "op": "const", "value": "relationship" },
        "subject_id": { "op": "proposed_subject_id", "subject": "relationship" },
        "relationship_state": { "op": "const", "value": "prospect" },
        "active_engagement_count": { "op": "const", "value": 0 },
        "party_id": { "op": "optional_declared_identifier", "field": "party_id" }
      },
      "required_context": [],
      "event": {
        "event_kind": "relationship_initialized", "subject": "relationship",
        "detail": {
          "relationship_state": { "op": "subject_field", "subject": "relationship",
            "source": "proposed", "field": "relationship_state" }
        }
      }
    },
    "initialize-assignment": {
      "subject_kind": "assignment",
      "operations": ["initialize-assignment"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q072.D1", "Q077.D1", "Q079.D1", "Q080.D1"],
      "requires_evidence": false,
      "parent_subject_kind": "engagement",
      "parent_reference_field": "engagement_id",
      "declared_identifiers": [],
      "optional_declared_identifiers": [],
      "creation_shape": {
        "subject_kind": { "op": "const", "value": "assignment" },
        "subject_id": { "op": "proposed_subject_id", "subject": "assignment" },
        "engagement_id": { "op": "subject_field", "subject": "engagement",
          "source": "context", "field": "subject_id" },
        "assignment_phase": { "op": "const", "value": "research" },
        "open_negotiation_count": { "op": "const", "value": 0 },
        "selected_property_id": { "op": "const", "value": null },
        "active_lease_draft_target_id": { "op": "const", "value": null },
        "pending_deal_id": { "op": "const", "value": null },
        "multi_target_exception_ref": { "op": "const", "value": null }
      },
      "required_context": [
        { "subject": "engagement",
          "identified_by": { "source": "created", "field": "engagement_id" },
          "conditions": [ { "field": "engagement_state", "equals": "active" } ] },
        { "subject": "relationship",
          "identified_by": { "source": "context", "subject": "engagement",
            "field": "relationship_id" },
          "conditions": [ { "field": "relationship_state", "equals": "client" } ] }
      ],
      "event": {
        "event_kind": "assignment_initialized", "subject": "assignment",
        "detail": {
          "engagement_id": { "op": "subject_field", "subject": "engagement",
            "source": "context", "field": "subject_id" },
          "assignment_phase": { "op": "subject_field", "subject": "assignment",
            "source": "proposed", "field": "assignment_phase" }
        }
      }
    },
    "initialize-property-negotiation": {
      "subject_kind": "property_negotiation",
      "operations": ["initialize-property-negotiation"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q080.D1", "Q095.D1"],
      "requires_evidence": false,
      "parent_subject_kind": "assignment",
      "parent_reference_field": "assignment_id",
      "declared_identifiers": ["property_id"],
      "optional_declared_identifiers": [],
      "unique_active_per_property": true,
      "creation_shape": {
        "subject_kind": { "op": "const", "value": "property_negotiation" },
        "subject_id": { "op": "proposed_subject_id", "subject": "property_negotiation" },
        "assignment_id": { "op": "subject_field", "subject": "assignment",
          "source": "context", "field": "subject_id" },
        "property_id": { "op": "declared_identifier", "field": "property_id" },
        "negotiation_state": { "op": "const", "value": "loi_drafted" }
      },
      "required_context": [
        { "subject": "assignment",
          "identified_by": { "source": "created", "field": "assignment_id" },
          "conditions": [ { "field": "assignment_phase",
            "in": ["research", "search", "negotiation"] } ] }
      ],
      "event": {
        "event_kind": "property_negotiation_initialized", "subject": "property_negotiation",
        "detail": {
          "assignment_id": { "op": "subject_field", "subject": "assignment",
            "source": "context", "field": "subject_id" },
          "property_id": { "op": "subject_field", "subject": "property_negotiation",
            "source": "proposed", "field": "property_id" },
          "negotiation_state": { "op": "subject_field", "subject": "property_negotiation",
            "source": "proposed", "field": "negotiation_state" }
        }
      }
    }
  },
  "evidence": {
    "signed_engagement_letter": {
      "source": "f01_document", "binds_subject_kind": "relationship",
      "record_kind": null, "requires_author_class": null,
      "requires_closing_date": false,
      "document_states": { "signature_state": "fully_executed",
        "validity_state": "effective", "version_state": "current" },
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "approved_representation_equivalent": {
      "source": "typed_approval", "binds_subject_kind": "relationship",
      "record_kind": null, "requires_author_class": null,
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner"]
    },
    "search_initiation": {
      "source": "first_party_record", "binds_subject_kind": "assignment",
      "record_kind": "assignment_mandate", "requires_author_class": null,
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "submitted_loi": {
      "source": "f01_document", "binds_subject_kind": "property_negotiation",
      "record_kind": null, "requires_author_class": null,
      "requires_closing_date": false,
      "document_states": { "delivery_state": "delivered", "version_state": "current" },
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "counterparty_loi_acceptance": {
      "source": "f01_corporate_artifact", "binds_subject_kind": "property_negotiation",
      "record_kind": null, "requires_author_class": null,
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "winner_selection_commitment": {
      "source": "first_party_record", "binds_subject_kind": "assignment",
      "record_kind": "winning_property_commitment",
      "requires_author_class": "verified_partner",
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner"]
    },
    "executed_lease": {
      "source": "f01_document", "binds_subject_kind": "deal",
      "record_kind": null, "requires_author_class": null,
      "requires_closing_date": false,
      "document_states": { "signature_state": "fully_executed",
        "validity_state": "effective", "version_state": "current" },
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "signed_purchase_contract": {
      "source": "f01_document", "binds_subject_kind": "deal",
      "record_kind": null, "requires_author_class": null,
      "requires_closing_date": false,
      "document_states": { "signature_state": "fully_executed",
        "version_state": "current" },
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "diligence_outcome": {
      "source": "first_party_record", "binds_subject_kind": "deal",
      "record_kind": "diligence_outcome", "requires_author_class": null,
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "final_closing_settlement": {
      "source": "first_party_record", "binds_subject_kind": "deal",
      "record_kind": "closing_settlement",
      "requires_author_class": "verified_partner",
      "requires_closing_date": true,
      "permitted_actor_classes": ["verified_partner"]
    },
    "deal_failure_record": {
      "source": "first_party_record", "binds_subject_kind": "deal",
      "record_kind": "deal_failure", "requires_author_class": "verified_partner",
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner"]
    },
    "commission_agreement": {
      "source": "f01_document", "binds_subject_kind": "deal",
      "record_kind": null, "requires_author_class": null,
      "requires_closing_date": false,
      "document_states": { "signature_state": "fully_executed",
        "version_state": "current" },
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "invoice_issued": {
      "source": "first_party_record", "binds_subject_kind": "deal",
      "record_kind": "invoice", "requires_author_class": null,
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "payment_received": {
      "source": "first_party_record", "binds_subject_kind": "deal",
      "record_kind": "payment", "requires_author_class": null,
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "completion_recorded": {
      "source": "first_party_record", "binds_subject_kind": "deal",
      "record_kind": "completion", "requires_author_class": null,
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"]
    },
    "manual_correction": {
      "source": "first_party_record", "binds_subject_kind": null,
      "record_kind": "lifecycle_correction",
      "requires_author_class": "verified_partner",
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner"]
    },
    "multi_target_exception_approval": {
      "source": "typed_approval", "binds_subject_kind": "assignment",
      "record_kind": null, "requires_author_class": null,
      "requires_closing_date": false,
      "permitted_actor_classes": ["verified_partner"]
    }
  },
  "transitions": {
    "establish-client-and-engagement": {
      "subject_kind": "relationship",
      "operations": ["record-representation-agreement"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q069.D1", "Q072.D1", "Q077.D1", "Q079.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "relationship_state": ["prospect"] },
      "instrument_kinds": null,
      "required_evidence_alternatives":
        [["signed_engagement_letter"], ["approved_representation_equivalent"]],
      "coupled_facts": ["relationship.relationship_state", "engagement.engagement_state",
        "engagement.representation_basis"],
      "creates_deal": false,
      "writes": {
        "relationship": ["relationship_state", "active_engagement_count"],
        "engagement": ["engagement_state", "representation_basis"]
      },
      "subjects": {
        "relationship": {
          "role": "primary", "mode": "update",
          "effects": {
            "relationship_state": { "op": "const", "value": "client" },
            "active_engagement_count": { "op": "prior_plus", "subject": "relationship",
              "field": "active_engagement_count", "add": 1 }
          }
        },
        "engagement": {
          "role": "coupled", "mode": "create",
          "creation_shape": {
            "subject_kind": { "op": "const", "value": "engagement" },
            "subject_id": { "op": "proposed_subject_id", "subject": "engagement" },
            "relationship_id": { "op": "proposed_subject_id", "subject": "relationship" },
            "engagement_state": { "op": "const", "value": "active" },
            "representation_basis": { "op": "supplied_evidence_kind" },
            "effective_from": { "op": "case_on_evidence", "cases": {
              "signed_engagement_letter": { "op": "const", "value": null },
              "approved_representation_equivalent": { "op": "unbound",
                "why": "the kernel takes this from the typed approval's approved_at, and ops.j102_typed_approval is private and always raises, so no manifest can reach this branch here" } } },
            "effective_to": { "op": "const", "value": null }
          }
        }
      },
      "events": [
        { "event_kind": "client_status_established", "subject": "relationship",
          "detail": {
            "representation_basis": { "op": "subject_field", "subject": "engagement",
              "source": "proposed", "field": "representation_basis" },
            "evidence_reference": { "op": "case_on_evidence", "cases": {
              "signed_engagement_letter": { "op": "evidence_fact",
                "evidence_kind": "signed_engagement_letter", "fact": "reference" },
              "approved_representation_equivalent": { "op": "evidence_fact",
                "evidence_kind": "approved_representation_equivalent", "fact": "reference" } } }
          } },
        { "event_kind": "engagement_opened", "subject": "engagement",
          "detail": {
            "relationship_id": { "op": "proposed_subject_id", "subject": "relationship" },
            "representation_basis": { "op": "subject_field", "subject": "engagement",
              "source": "proposed", "field": "representation_basis" }
          } }
      ]
    },
    "open-assignment": {
      "subject_kind": "assignment",
      "operations": ["open-cre-assignment"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q072.D1", "Q079.D1", "Q080.D1"],
      "requires_active_engagement": true,
      "prerequisites": { "assignment_phase": ["research", "search"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["search_initiation"]],
      "coupled_facts": ["assignment.assignment_phase"],
      "creates_deal": false,
      "writes": { "assignment": ["assignment_phase"] },
      "subjects": {
        "assignment": {
          "role": "primary", "mode": "update",
          "prior_conditions": [
            { "field": "pending_deal_id", "must_be_null": true },
            { "field": "selected_property_id", "must_be_null": true },
            { "field": "active_lease_draft_target_id", "must_be_null": true }
          ],
          "effects": {
            "assignment_phase": { "op": "one_of", "values": ["research", "search"],
              "guards": [ { "value": "research", "requires": { "subject": "assignment",
                "source": "prior", "field": "open_negotiation_count", "equals": 0 } } ] }
          }
        }
      },
      "required_context": [
        { "subject": "engagement",
          "identified_by": { "subject": "assignment", "source": "prior", "field": "engagement_id" },
          "conditions": [ { "field": "engagement_state", "equals": "active" } ] },
        { "subject": "relationship",
          "identified_by": { "subject": "engagement", "source": "context", "field": "relationship_id" },
          "conditions": [ { "field": "relationship_state", "equals": "client" } ] }
      ],
      "events": [ { "event_kind": "assignment_opened", "subject": "assignment",
        "detail": {
          "engagement_id": { "op": "subject_field", "subject": "engagement",
            "source": "context", "field": "subject_id" },
          "assignment_phase": { "op": "subject_field", "subject": "assignment",
            "source": "proposed", "field": "assignment_phase" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "search_initiation", "fact": "reference" }
        } } ]
    },
    "record-loi-submission": {
      "subject_kind": "property_negotiation",
      "operations": ["record-loi-submission"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q072.D1", "Q080.D1", "Q095.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "negotiation_state": ["loi_drafted", "loi_countered"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["submitted_loi"]],
      "coupled_facts": ["property_negotiation.negotiation_state", "assignment.assignment_phase"],
      "creates_deal": false,
      "writes": {
        "property_negotiation": ["negotiation_state"],
        "assignment": ["assignment_phase", "open_negotiation_count"]
      },
      "subjects": {
        "property_negotiation": {
          "role": "primary", "mode": "update",
          "prior_conditions": [
            { "field": "assignment_id", "equals_subject_id": "assignment" }
          ],
          "effects": {
            "negotiation_state": { "op": "const", "value": "loi_submitted" }
          }
        },
        "assignment": {
          "role": "coupled", "mode": "update",
          "prior_conditions": [
            { "field": "assignment_phase", "in": ["research", "search", "negotiation"] }
          ],
          "effects": {
            "assignment_phase": { "op": "const", "value": "negotiation" },
            "open_negotiation_count": { "op": "prior_plus_conditional",
              "subject": "assignment", "field": "open_negotiation_count", "add": 1,
              "when": { "subject": "property_negotiation", "source": "prior",
                "field": "negotiation_state", "equals": "loi_drafted" } }
          }
        }
      },
      "events": [ { "event_kind": "loi_submitted", "subject": "property_negotiation",
        "detail": {
          "assignment_id": { "op": "proposed_subject_id", "subject": "assignment" },
          "property_id": { "op": "subject_field", "subject": "property_negotiation",
            "source": "prior", "field": "property_id" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "submitted_loi", "fact": "reference" }
        } } ]
    },
    "record-loi-acceptance": {
      "subject_kind": "property_negotiation",
      "operations": ["record-loi-acceptance"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q078.D1", "Q095.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "negotiation_state": ["loi_submitted", "loi_countered"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["counterparty_loi_acceptance"]],
      "coupled_facts": ["property_negotiation.negotiation_state"],
      "creates_deal": false,
      "writes": { "property_negotiation": ["negotiation_state"] },
      "subjects": {
        "property_negotiation": {
          "role": "primary", "mode": "update",
          "effects": {
            "negotiation_state": { "op": "const", "value": "loi_accepted" }
          }
        }
      },
      "events": [ { "event_kind": "loi_accepted", "subject": "property_negotiation",
        "detail": {
          "property_id": { "op": "subject_field", "subject": "property_negotiation",
            "source": "prior", "field": "property_id" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "counterparty_loi_acceptance", "fact": "reference" }
        } } ]
    },
    "commit-winning-property": {
      "subject_kind": "assignment",
      "operations": ["commit-winning-property"],
      "permitted_actor_classes": ["verified_partner"],
      "decision_refs": ["Q069.D1", "Q078.D1", "Q080.D1", "Q095.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "assignment_phase": ["search", "negotiation"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["winner_selection_commitment"]],
      "coupled_facts": ["property_negotiation.negotiation_state", "assignment.assignment_phase",
        "assignment.selected_property_id", "assignment.pending_deal_id", "deal.deal_state"],
      "creates_deal": true,
      "writes": {
        "property_negotiation": ["negotiation_state"],
        "assignment": ["assignment_phase", "selected_property_id",
          "active_lease_draft_target_id", "pending_deal_id"],
        "deal": ["deal_state"]
      },
      "subjects": {
        "assignment": {
          "role": "primary", "mode": "update",
          "prior_conditions": [
            { "field": "pending_deal_id", "must_be_null": true },
            { "field": "selected_property_id", "null_or_matches": { "op": "subject_field",
              "subject": "property_negotiation", "source": "prior", "field": "property_id" } },
            { "field": "active_lease_draft_target_id", "null_or_matches": { "op": "subject_field",
              "subject": "property_negotiation", "source": "prior", "field": "property_id" } }
          ],
          "effects": {
            "assignment_phase": { "op": "const", "value": "committed" },
            "selected_property_id": { "op": "subject_field", "subject": "property_negotiation",
              "source": "prior", "field": "property_id" },
            "active_lease_draft_target_id": { "op": "case_on_field", "subject": "deal",
              "source": "proposed", "field": "instrument_kind",
              "cases": { "purchase": { "op": "const", "value": null } },
              "default": { "op": "subject_field", "subject": "property_negotiation",
                "source": "prior", "field": "property_id" } },
            "pending_deal_id": { "op": "proposed_subject_id", "subject": "deal" }
          }
        },
        "property_negotiation": {
          "role": "coupled", "mode": "update",
          "prior_conditions": [
            { "field": "assignment_id", "equals_subject_id": "assignment" },
            { "field": "negotiation_state", "equals": "loi_accepted" }
          ],
          "effects": {
            "negotiation_state": { "op": "const", "value": "selected_winner" }
          }
        },
        "deal": {
          "role": "coupled", "mode": "create",
          "creation_shape": {
            "subject_kind": { "op": "const", "value": "deal" },
            "subject_id": { "op": "proposed_subject_id", "subject": "deal" },
            "assignment_id": { "op": "proposed_subject_id", "subject": "assignment" },
            "property_id": { "op": "subject_field", "subject": "property_negotiation",
              "source": "prior", "field": "property_id" },
            "instrument_kind": { "op": "one_of",
              "values": ["lease", "purchase", "renewal", "amendment"] },
            "deal_state": { "op": "const", "value": "pending" },
            "execution_state": { "op": "const", "value": "unexecuted" },
            "diligence_state": { "op": "const", "value": "not_applicable" },
            "closing_state": { "op": "const", "value": "not_reached" },
            "commission_agreement_state": { "op": "const", "value": "absent" },
            "invoice_state": { "op": "const", "value": "not_invoiced" },
            "payment_state": { "op": "const", "value": "unpaid" },
            "completion_state": { "op": "const", "value": "open" },
            "cancellation_reason": { "op": "const", "value": null },
            "closing_date": { "op": "const", "value": null }
          }
        }
      },
      "events": [
        { "event_kind": "winning_property_selected", "subject": "property_negotiation",
          "detail": {
            "property_id": { "op": "subject_field", "subject": "property_negotiation",
              "source": "prior", "field": "property_id" }
          } },
        { "event_kind": "assignment_committed", "subject": "assignment",
          "detail": {
            "selected_property_id": { "op": "subject_field", "subject": "property_negotiation",
              "source": "prior", "field": "property_id" },
            "pending_deal_id": { "op": "proposed_subject_id", "subject": "deal" }
          } },
        { "event_kind": "pending_deal_created", "subject": "deal",
          "detail": {
            "assignment_id": { "op": "proposed_subject_id", "subject": "assignment" },
            "property_id": { "op": "subject_field", "subject": "property_negotiation",
              "source": "prior", "field": "property_id" },
            "instrument_kind": { "op": "subject_field", "subject": "deal",
              "source": "proposed", "field": "instrument_kind" },
            "evidence_reference": { "op": "evidence_fact",
              "evidence_kind": "winner_selection_commitment", "fact": "reference" }
          } }
      ]
    },
    "record-lease-execution": {
      "subject_kind": "deal",
      "operations": ["record-deal-execution"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q072.D1", "Q078.D1", "Q080.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "deal_state": ["pending"], "execution_state": ["unexecuted"] },
      "instrument_kinds": ["lease", "renewal", "amendment"],
      "required_evidence_alternatives": [["executed_lease"]],
      "coupled_facts": ["deal.execution_state"],
      "creates_deal": false,
      "writes": { "deal": ["execution_state"] },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "effects": { "execution_state": { "op": "const", "value": "executed" } }
        }
      },
      "events": [ { "event_kind": "lease_executed", "subject": "deal",
        "detail": {
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "executed_lease", "fact": "reference" }
        } } ]
    },
    "record-purchase-contract-execution": {
      "subject_kind": "deal",
      "operations": ["record-deal-execution"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q072.D1", "Q094.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "deal_state": ["pending"], "execution_state": ["unexecuted"] },
      "instrument_kinds": ["purchase"],
      "required_evidence_alternatives": [["signed_purchase_contract"]],
      "coupled_facts": ["deal.execution_state", "deal.diligence_state"],
      "creates_deal": false,
      "writes": { "deal": ["execution_state", "diligence_state"] },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "effects": {
            "execution_state": { "op": "const", "value": "executed" },
            "diligence_state": { "op": "const", "value": "in_progress" }
          }
        }
      },
      "events": [ { "event_kind": "purchase_contract_executed", "subject": "deal",
        "detail": {
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "signed_purchase_contract", "fact": "reference" }
        } } ]
    },
    "record-diligence-outcome": {
      "subject_kind": "deal",
      "operations": ["record-diligence-outcome"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q080.D1", "Q094.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "deal_state": ["pending"], "diligence_state": ["in_progress"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["diligence_outcome"]],
      "coupled_facts": ["deal.diligence_state"],
      "creates_deal": false,
      "writes": { "deal": ["diligence_state"] },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "effects": { "diligence_state": { "op": "one_of",
            "values": ["waived", "satisfied", "failed"] } }
        }
      },
      "events": [ { "event_kind": "diligence_outcome_recorded", "subject": "deal",
        "detail": {
          "diligence_state": { "op": "subject_field", "subject": "deal",
            "source": "proposed", "field": "diligence_state" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "diligence_outcome", "fact": "reference" }
        } } ]
    },
    "record-deal-closing": {
      "subject_kind": "deal",
      "operations": ["record-deal-closing"],
      "permitted_actor_classes": ["verified_partner"],
      "decision_refs": ["Q080.D1", "Q082.D1", "Q094.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "deal_state": ["pending"], "execution_state": ["executed"],
        "closing_state": ["not_reached"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["final_closing_settlement"]],
      "coupled_facts": ["deal.deal_state", "deal.closing_state", "deal.closing_date"],
      "creates_deal": false,
      "writes": { "deal": ["deal_state", "closing_state", "closing_date"] },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "prior_conditions": [
            { "field": "diligence_state", "not_in": ["in_progress", "failed"] }
          ],
          "effects": {
            "deal_state": { "op": "const", "value": "closed" },
            "closing_state": { "op": "const", "value": "closed" },
            "closing_date": { "op": "evidence_fact",
              "evidence_kind": "final_closing_settlement", "fact": "closing_date" }
          }
        }
      },
      "events": [ { "event_kind": "deal_closed", "subject": "deal",
        "detail": {
          "closing_date": { "op": "evidence_fact",
            "evidence_kind": "final_closing_settlement", "fact": "closing_date" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "final_closing_settlement", "fact": "reference" }
        } } ]
    },
    "cancel-pending-deal": {
      "subject_kind": "deal",
      "operations": ["cancel-pending-deal"],
      "permitted_actor_classes": ["verified_partner"],
      "decision_refs": ["Q096.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "deal_state": ["pending"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["deal_failure_record"]],
      "coupled_facts": ["deal.deal_state", "deal.cancellation_reason",
        "assignment.assignment_phase", "assignment.selected_property_id",
        "assignment.pending_deal_id"],
      "creates_deal": false,
      "writes": {
        "deal": ["deal_state", "cancellation_reason"],
        "assignment": ["assignment_phase", "selected_property_id",
          "active_lease_draft_target_id", "pending_deal_id"]
      },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "prior_conditions": [
            { "field": "assignment_id", "equals_subject_id": "assignment" }
          ],
          "effects": {
            "deal_state": { "op": "const", "value": "cancelled" },
            "cancellation_reason": { "op": "evidence_fact",
              "evidence_kind": "deal_failure_record", "fact": "reason" }
          }
        },
        "assignment": {
          "role": "coupled", "mode": "update",
          "effects": {
            "assignment_phase": { "op": "one_of", "values": ["search", "negotiation"],
              "guards": [ { "value": "negotiation", "requires": { "subject": "assignment",
                "source": "prior", "field": "open_negotiation_count", "at_least": 1 } } ] },
            "selected_property_id": { "op": "const", "value": null },
            "active_lease_draft_target_id": { "op": "const", "value": null },
            "pending_deal_id": { "op": "const", "value": null }
          }
        }
      },
      "events": [
        { "event_kind": "pending_deal_cancelled", "subject": "deal",
          "detail": {
            "cancellation_reason": { "op": "evidence_fact",
              "evidence_kind": "deal_failure_record", "fact": "reason" },
            "evidence_reference": { "op": "evidence_fact",
              "evidence_kind": "deal_failure_record", "fact": "reference" }
          } },
        { "event_kind": "assignment_returned_to_market", "subject": "assignment",
          "detail": {
            "assignment_phase": { "op": "subject_field", "subject": "assignment",
              "source": "proposed", "field": "assignment_phase" }
          } }
      ]
    },
    "record-commission-agreement": {
      "subject_kind": "deal",
      "operations": ["record-deal-axis"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q080.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "commission_agreement_state": ["absent"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["commission_agreement"]],
      "coupled_facts": ["deal.commission_agreement_state"],
      "creates_deal": false,
      "writes": { "deal": ["commission_agreement_state"] },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "effects": { "commission_agreement_state": { "op": "const", "value": "agreed" } }
        }
      },
      "events": [ { "event_kind": "commission_agreement_recorded", "subject": "deal",
        "detail": {
          "commission_agreement_state": { "op": "subject_field", "subject": "deal",
            "source": "proposed", "field": "commission_agreement_state" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "commission_agreement", "fact": "reference" }
        } } ]
    },
    "record-invoice-issued": {
      "subject_kind": "deal",
      "operations": ["record-deal-axis"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q080.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "invoice_state": ["not_invoiced"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["invoice_issued"]],
      "coupled_facts": ["deal.invoice_state"],
      "creates_deal": false,
      "writes": { "deal": ["invoice_state"] },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "effects": { "invoice_state": { "op": "const", "value": "invoiced" } }
        }
      },
      "events": [ { "event_kind": "invoice_issued", "subject": "deal",
        "detail": {
          "invoice_state": { "op": "subject_field", "subject": "deal",
            "source": "proposed", "field": "invoice_state" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "invoice_issued", "fact": "reference" }
        } } ]
    },
    "record-payment": {
      "subject_kind": "deal",
      "operations": ["record-deal-axis"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q072.D1", "Q080.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "payment_state": ["unpaid", "partially_paid"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["payment_received"]],
      "coupled_facts": ["deal.payment_state"],
      "creates_deal": false,
      "writes": { "deal": ["payment_state"] },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "effects": { "payment_state": { "op": "one_of",
            "values": ["partially_paid", "paid"], "differs_from_prior": true,
            "prior_subject": "deal", "prior_field": "payment_state" } }
        }
      },
      "events": [ { "event_kind": "payment_recorded", "subject": "deal",
        "detail": {
          "payment_state": { "op": "subject_field", "subject": "deal",
            "source": "proposed", "field": "payment_state" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "payment_received", "fact": "reference" }
        } } ]
    },
    "record-completion": {
      "subject_kind": "deal",
      "operations": ["record-deal-axis"],
      "permitted_actor_classes": ["verified_partner", "sponsored_agent"],
      "decision_refs": ["Q072.D1", "Q080.D1"],
      "requires_active_engagement": false,
      "prerequisites": { "completion_state": ["open"] },
      "instrument_kinds": null,
      "required_evidence_alternatives": [["completion_recorded"]],
      "coupled_facts": ["deal.completion_state"],
      "creates_deal": false,
      "writes": { "deal": ["completion_state"] },
      "subjects": {
        "deal": {
          "role": "primary", "mode": "update",
          "effects": { "completion_state": { "op": "const", "value": "complete" } }
        }
      },
      "events": [ { "event_kind": "completion_recorded", "subject": "deal",
        "detail": {
          "completion_state": { "op": "subject_field", "subject": "deal",
            "source": "proposed", "field": "completion_state" },
          "evidence_reference": { "op": "evidence_fact",
            "evidence_kind": "completion_recorded", "fact": "reference" }
        } } ]
    }
  }
}
$policy$::jsonb
$fn$;

comment on function ops.j102_admission_policy() is
  'The closed SQL admission map: which operation may perform which transition, which actor class may perform it, which subjects it must write and which it may CREATE (never the primary one), the EXACT resulting value of every field it moves, the exact event set it appends AND the exact nested detail of every one of those events, and which evidence kinds bound to which subject it requires. It also carries Q103''s reconciliation vocabulary -- the four conflict kinds evaluateConcurrentEdit can file and the field-class registry that says what class each field is -- because ops.j102_record_reconciliation_item is granted to carr_writer and enforces both against this map rather than against a caller''s label. A transcription of the kernel''s exported contracts and of its evaluator, asserted equal to both by the Node parity suite -- which runs the evaluator and requires this map to predict its answer. It decides nothing the kernel does not already decide and adds no business policy.';

-- ---------------------------------------------------------------------------
-- THE EFFECT INTERPRETER.
--
-- One pure function, so "what should this field be" is answered in ONE place and
-- the writer below is a comparison rather than a second policy. It reads nothing:
-- every input it needs -- the committed prior states, the proposed states, the
-- subject ids of this call and the facts the evidence recheck read off stored
-- rows -- is passed in, which is also what makes it checkable by inspection.
--
-- IT ANSWERS IN ONE OF FOUR SHAPES.
--   {"kind":"exact","value":X}     the field must be exactly X.
--   {"kind":"any_of","values":[…]} the caller chooses, from the set the KERNEL
--                                  offers on that path and no wider: the mandate
--                                  scope, the diligence result, the return phase,
--                                  the payment level, the instrument kind.
--   {"kind":"declared_identifier","field":F}
--                                  the value is an IDENTIFIER the caller declares
--                                  and nothing can derive -- the property a new
--                                  negotiation concerns. Only the INITIALIZATION
--                                  writer accepts this kind, and it holds the
--                                  value to the identifier shape; the transition
--                                  writer refuses it, because a transition target
--                                  is always derivable from state or evidence.
--   {"kind":"unbound","why":…}     the value belongs to a layer this rail does
--                                  not re-derive. There is exactly ONE of these
--                                  in the whole map and it sits on an unreachable
--                                  branch; it is named rather than hidden.
--
-- AN EMPTY any_of IS A REFUSAL, not a wildcard: it is what a guard that removed
-- every candidate means, and the writer treats it as "no value is admissible
-- here".
-- ---------------------------------------------------------------------------
create or replace function ops.j102_expected_value(
  p_effect jsonb, p_prior jsonb, p_proposed jsonb, p_context jsonb,
  p_ids jsonb, p_facts jsonb)
returns jsonb language plpgsql immutable
set search_path = pg_catalog
as $$
declare
  v_op text := p_effect ->> 'op';
  v_source jsonb;
  v_values jsonb := '[]'::jsonb;
  v_candidate jsonb;
  v_guard jsonb;
  v_requires jsonb;
  v_observed jsonb;
  v_keep boolean;
  v_prior_value jsonb;
  v_case jsonb;
  v_key text;
begin
  if v_op is null then
    raise exception 'j102_malformed_effect: an admission effect names no op: %', p_effect
      using errcode = '22023';
  end if;
  if v_op = 'const' then
    return jsonb_build_object('kind', 'exact', 'value', p_effect -> 'value');
  elsif v_op = 'declared_identifier' then
    -- THE ONE VALUE NOTHING CAN DERIVE, and it is an IDENTIFIER rather than a
    -- fact: which property a new negotiation concerns. It cannot be enumerated
    -- like a declared enum and it cannot be read off another row, because before
    -- this call there is no row that names it. The answer says so, and the writer
    -- that receives it holds the value to the identifier shape rather than to a
    -- value set. No TRANSITION uses this op -- a transition's every target is
    -- derivable from the committed row, the coupled subjects or the re-read
    -- evidence -- and the transition writer refuses a kind it does not expect.
    return jsonb_build_object('kind', 'declared_identifier', 'field', p_effect -> 'field');
  elsif v_op = 'optional_declared_identifier' then
    -- The party link (owner ruling 2026-09-25): a declared identifier that MAY be
    -- null. When present, the writer binds it to an existing CARR party row.
    return jsonb_build_object('kind', 'optional_declared_identifier', 'field', p_effect -> 'field');
  elsif v_op = 'unbound' then
    return jsonb_build_object('kind', 'unbound', 'why', p_effect -> 'why');
  elsif v_op = 'proposed_subject_id' then
    return jsonb_build_object('kind', 'exact',
      'value', to_jsonb(p_ids ->> (p_effect ->> 'subject')));
  elsif v_op = 'subject_field' then
    v_source := case p_effect ->> 'source'
                  when 'prior' then p_prior when 'proposed' then p_proposed
                  when 'context' then p_context else null end;
    if v_source is null then
      raise exception 'j102_malformed_effect: unknown effect source %', p_effect ->> 'source'
        using errcode = '22023';
    end if;
    return jsonb_build_object('kind', 'exact',
      'value', (v_source -> (p_effect ->> 'subject')) -> (p_effect ->> 'field'));
  elsif v_op = 'evidence_fact' then
    -- The value the DATABASE read off the stored evidence row under this
    -- transaction's lock, not the value the caller proposed. A closing date is
    -- the settlement's closing date or the transition refuses.
    return jsonb_build_object('kind', 'exact',
      'value', (p_facts -> (p_effect ->> 'evidence_kind')) -> (p_effect ->> 'fact'));
  elsif v_op = 'supplied_evidence_kind' then
    select jsonb_build_object('kind', 'exact', 'value', to_jsonb(k))
      into v_candidate from jsonb_object_keys(p_facts) as k limit 1;
    return coalesce(v_candidate, jsonb_build_object('kind', 'any_of', 'values', '[]'::jsonb));
  elsif v_op = 'prior_plus' then
    v_observed := (p_prior -> (p_effect ->> 'subject')) -> (p_effect ->> 'field');
    if jsonb_typeof(v_observed) is distinct from 'number' then
      -- A counter that is not a number has no successor, and guessing one would
      -- be the invention this map exists to avoid.
      return jsonb_build_object('kind', 'any_of', 'values', '[]'::jsonb);
    end if;
    return jsonb_build_object('kind', 'exact',
      'value', to_jsonb((v_observed #>> '{}')::numeric + (p_effect ->> 'add')::numeric));
  elsif v_op = 'prior_plus_conditional' then
    v_observed := (p_prior -> (p_effect ->> 'subject')) -> (p_effect ->> 'field');
    if jsonb_typeof(v_observed) is distinct from 'number' then
      return jsonb_build_object('kind', 'any_of', 'values', '[]'::jsonb);
    end if;
    v_requires := p_effect -> 'when';
    v_source := case v_requires ->> 'source'
                  when 'prior' then p_prior when 'proposed' then p_proposed
                  when 'context' then p_context else null end;
    if ((v_source -> (v_requires ->> 'subject')) -> (v_requires ->> 'field'))
         is not distinct from (v_requires -> 'equals') then
      return jsonb_build_object('kind', 'exact',
        'value', to_jsonb((v_observed #>> '{}')::numeric + (p_effect ->> 'add')::numeric));
    end if;
    return jsonb_build_object('kind', 'exact', 'value', v_observed);
  elsif v_op = 'case_on_field' then
    v_observed := (case p_effect ->> 'source'
                     when 'prior' then p_prior when 'proposed' then p_proposed
                     when 'context' then p_context else null end
                  -> (p_effect ->> 'subject')) -> (p_effect ->> 'field');
    for v_key in select * from jsonb_object_keys(p_effect -> 'cases') loop
      if v_observed = to_jsonb(v_key) then
        return ops.j102_expected_value(p_effect -> 'cases' -> v_key,
          p_prior, p_proposed, p_context, p_ids, p_facts);
      end if;
    end loop;
    return ops.j102_expected_value(p_effect -> 'default',
      p_prior, p_proposed, p_context, p_ids, p_facts);
  elsif v_op = 'case_on_evidence' then
    for v_key in select * from jsonb_object_keys(p_facts) loop
      v_case := p_effect -> 'cases' -> v_key;
      if v_case is not null then
        return ops.j102_expected_value(v_case, p_prior, p_proposed, p_context, p_ids, p_facts);
      end if;
    end loop;
    return jsonb_build_object('kind', 'any_of', 'values', '[]'::jsonb);
  elsif v_op = 'one_of' then
    for v_candidate in select * from jsonb_array_elements(p_effect -> 'values') loop
      v_keep := true;
      -- A GUARD IS THE KERNEL'S OWN NARROWING, not a new rule: research is not a
      -- scope for an assignment with open negotiations, and `negotiation` is not
      -- a phase to return to when nothing is being negotiated.
      for v_guard in
        select * from jsonb_array_elements(coalesce(p_effect -> 'guards', '[]'::jsonb))
      loop
        if (v_guard -> 'value') = v_candidate then
          v_requires := v_guard -> 'requires';
          v_source := case v_requires ->> 'source'
                        when 'prior' then p_prior when 'proposed' then p_proposed
                        when 'context' then p_context else null end;
          v_observed := (v_source -> (v_requires ->> 'subject')) -> (v_requires ->> 'field');
          if v_requires ? 'equals' and v_observed is distinct from (v_requires -> 'equals') then
            v_keep := false;
          end if;
          if v_requires ? 'at_least' and
             (jsonb_typeof(v_observed) is distinct from 'number'
              or (v_observed #>> '{}')::numeric < (v_requires ->> 'at_least')::numeric) then
            v_keep := false;
          end if;
        end if;
      end loop;
      -- Q072's payment axis: `partially_paid` over an already partially paid deal
      -- is a level that would not change the state, which the kernel refuses by
      -- name. The candidate that equals the committed value is dropped here for
      -- exactly that reason and for no other.
      if v_keep and coalesce((p_effect ->> 'differs_from_prior')::boolean, false) then
        v_prior_value := (p_prior -> (p_effect ->> 'prior_subject')) -> (p_effect ->> 'prior_field');
        if v_prior_value is not null and v_prior_value = v_candidate then
          v_keep := false;
        end if;
      end if;
      if v_keep then
        v_values := v_values || jsonb_build_array(v_candidate);
      end if;
    end loop;
    return jsonb_build_object('kind', 'any_of', 'values', v_values);
  end if;
  raise exception 'j102_malformed_effect: unknown admission effect op %', v_op
    using errcode = '22023';
end;
$$;

comment on function ops.j102_expected_value(jsonb,jsonb,jsonb,jsonb,jsonb,jsonb) is
  'Compute the EXACT value the admission map says a field must hold after one transition, from the committed prior state, the other subjects in the same call, the ids this call proposes and the evidence facts the recheck read under the lock. Pure: it reads no relation and derives nothing from the caller. An empty any_of means no value is admissible on that path.';

-- ---------------------------------------------------------------------------
-- THE EVIDENCE RECHECK.
--
-- This is the half a kernel cannot enforce, and it is the reason this file
-- exists rather than a set of plain INSERTs.
--
-- The store read the evidence, the kernel judged it, and time passed. A document
-- can gain a version, an artifact can be superseded, a first-party record can be
-- rewritten -- all between the read and the write. Applying a decision taken
-- against evidence that has since moved is the same defect as last-writer-wins,
-- one layer down, so the EXACT pin is re-read HERE, inside the transaction that
-- already holds the subject locks, and any movement refuses the whole transition.
--
-- IT COMPARES PINS, NOT SHAPES. A document reference names the version and the
-- content digest it meant; a first-party record names the record digest it meant.
-- "The document still exists" is not the check. "The document is still the one
-- the decision was taken against" is.
--
-- AND IT RE-ASSERTS THE SUBJECT BINDING AGAINST THE SUBJECT THE TRANSITION IS
-- ACTUALLY ADVANCING, which is the half that was still missing after the first
-- correction. The recheck could compare the manifest's binding against the
-- EVIDENCE -- it re-read the record's own typed columns and asked the association
-- reader a yes/no question -- but the manifest's binding was itself caller-
-- supplied and was never compared to the subject envelopes, which arrive in a
-- different parameter. So a direct caller could present a genuine, unmoved,
-- correctly pinned closing settlement bound to deal B, propose a state change for
-- deal A, and satisfy every check: the record really was bound to deal B and the
-- manifest really said deal B. Deal A closed on deal B's settlement date.
--
-- MEMBERSHIP OF THE LOCK SET IS NOT ENOUGH EITHER, and that is worth saying
-- because it is the obvious fix and it does not hold: an attacker who has to make
-- the binding a MEMBER of the compare-and-swap key set simply adds deal B to the
-- key set with its true digest and goes on proposing deal A. What closes it is
-- identity, not membership: the caller passes the ONE PRIMARY SUBJECT the
-- validated transition advances -- derived from the transition contract's own
-- subject_kind and read off the proposed envelopes -- and every pin must bind to
-- exactly that subject, with the source, record kind and author class the
-- kernel's evidence contract names.
-- ---------------------------------------------------------------------------

-- The single-argument form is replaced, not shadowed: leaving it in place would
-- leave a callable recheck that establishes "the evidence was still exact"
-- without ever being told which subject it was exact FOR. It holds no data.
drop function if exists ops.j102_recheck_evidence(jsonb);

create or replace function ops.j102_recheck_evidence(
  p_recheck jsonb, p_transition_id text, p_subject_kind text, p_subject_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_policy jsonb := ops.j102_admission_policy();
  v_contract jsonb;
  v_class text := ops.f01_principal() ->> 'authorization_class';
  v_item jsonb;
  v_kind text;
  v_axis text;
  v_contract_evidence jsonb;
  v_body jsonb;
  v_record jsonb;
  v_link jsonb;
  v_checked jsonb := '[]'::jsonb;
  -- THE FACTS THE STORED EVIDENCE ACTUALLY CARRIES, read off the row under this
  -- transaction's lock and returned to the writer so a coupled fact derived from
  -- evidence -- a closing date, a cancellation reason -- is compared against the
  -- record rather than against the caller's account of it.
  v_facts jsonb;
  -- HIGH-6. THE PIN'S CANONICAL REFERENCE, taken from what the READER returned
  -- rather than from the selector the caller wrote: F01's own document_id off the
  -- document record, the stored-artifact reader's own artifact_digest, and the
  -- record_id on the committed first-party row. This is the value the kernel puts
  -- on its events as `evidence_reference` and the value the store puts in
  -- `evidence_references`, so deriving it HERE is what lets the writer compare
  -- the history it is about to append against evidence that was actually re-read.
  v_reference jsonb;
  v_supplied text[] := array[]::text[];
  v_supplied_sorted text[];
  v_alternative jsonb;
  v_alternative_sorted text[];
  v_matched boolean := false;
begin
  v_contract := v_policy -> 'transitions' -> p_transition_id;
  if v_contract is null then
    raise exception 'j102_unknown_transition: % is in no admission contract',
      coalesce(p_transition_id, 'unnamed') using errcode = '22023';
  end if;
  -- THE PRIMARY SUBJECT IS NOT OPTIONAL. A recheck that cannot name the subject
  -- it is re-reading evidence FOR is the exact gap this correction closes, so it
  -- refuses rather than falling back to checking the evidence against itself.
  if p_subject_kind is null or p_subject_id is null then
    raise exception 'j102_primary_subject_unresolved: % names no primary subject for its evidence to bind to',
      p_transition_id using errcode = '22023';
  end if;
  if jsonb_typeof(p_recheck) is distinct from 'array' or jsonb_array_length(p_recheck) < 1 then
    raise exception 'j102_evidence_recheck_required: a transition never applies without re-reading its evidence'
      using errcode = '22023';
  end if;
  for v_item in select * from jsonb_array_elements(p_recheck) loop
    v_kind := v_item ->> 'evidence_kind';
    v_contract_evidence := v_policy -> 'evidence' -> v_kind;
    if v_contract_evidence is null then
      raise exception 'j102_unknown_evidence_kind: % is not an evidence kind this rail admits',
        coalesce(v_kind, 'unnamed') using errcode = '22023';
    end if;
    -- Two records for one kind is ambiguous evidence, exactly as it is in the
    -- kernel. It also stops a manifest padding one required kind to look like a
    -- satisfied alternative.
    if v_kind = any(v_supplied) then
      raise exception 'j102_duplicate_evidence_kind: % appears twice in one manifest', v_kind
        using errcode = '22023';
    end if;
    v_supplied := v_supplied || v_kind;
    v_facts := '{}'::jsonb;
    v_reference := null;
    -- THE SOURCE IS THE CONTRACT'S, not the manifest's. A first-party record
    -- presented as a document would otherwise take the document branch and skip
    -- the record's own typed binding entirely.
    if (v_item ->> 'source') is distinct from (v_contract_evidence ->> 'source') then
      raise exception 'j102_evidence_source_mismatch: % is established from %, and this manifest names %',
        v_kind, v_contract_evidence ->> 'source', coalesce(v_item ->> 'source', 'nothing')
        using errcode = '22023';
    end if;
    if not (v_contract_evidence -> 'permitted_actor_classes' ? v_class) then
      raise exception 'j102_actor_class_not_permitted_for_evidence: % is not presented by a %',
        v_kind, v_class using errcode = '42501';
    end if;
    -- EVERY item must name the subject it binds to. A manifest entry without one
    -- is a decision nobody can re-check, and it refuses rather than being
    -- re-checked on the half of itself that is present.
    if v_item -> 'binding' is null
       or (v_item -> 'binding' ->> 'subject_kind') is null
       or (v_item -> 'binding' ->> 'subject_id') is null then
      raise exception 'j102_evidence_binding_required: % evidence names no subject binding to re-assert',
        coalesce(v_kind, 'unnamed') using errcode = '22023';
    end if;
    if (v_contract_evidence ->> 'binds_subject_kind') is not null
       and (v_item -> 'binding' ->> 'subject_kind')
             is distinct from (v_contract_evidence ->> 'binds_subject_kind') then
      raise exception 'j102_evidence_bound_to_wrong_subject_kind: % binds a %, and this manifest names a %',
        v_kind, v_contract_evidence ->> 'binds_subject_kind',
        v_item -> 'binding' ->> 'subject_kind' using errcode = '22023';
    end if;
    -- BLOCK-2, AND THIS IS THE LINE THE WHOLE CORRECTION TURNS ON. Not "is the
    -- binding somewhere in the lock set" -- an attacker supplies the lock set --
    -- but "is the binding the EXACT subject this transition advances".
    if (v_item -> 'binding' ->> 'subject_kind') is distinct from p_subject_kind
       or (v_item -> 'binding' ->> 'subject_id') is distinct from p_subject_id then
      raise exception 'j102_evidence_not_bound_to_primary_subject: % is presented against % %, and this transition advances % %',
        v_kind, v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id',
        p_subject_kind, p_subject_id using errcode = '42501';
    end if;
    if (v_item ->> 'source') = 'f01_document' then
      v_body := ops.f01_read('document',
        jsonb_build_object('document_id', v_item -> 'selector' ->> 'document_id')) -> 'body';
      if v_body is null or v_body -> 'record' is null then
        raise exception 'j102_evidence_moved: document % is no longer readable',
          v_item -> 'selector' ->> 'document_id' using errcode = '40001';
      end if;
      v_record := v_body -> 'record';
      if (v_record -> 'neon_identity' ->> 'version_no')
           is distinct from (v_item ->> 'expected_version_no')
         or (v_record -> 'neon_identity' ->> 'content_digest')
           is distinct from (v_item ->> 'expected_content_digest') then
        raise exception 'j102_evidence_moved: document % is not the version this transition was decided against',
          v_item -> 'selector' ->> 'document_id' using errcode = '40001';
      end if;
      v_link := ops.j102_evidence_subject_link(
        'f01_document', v_item -> 'selector' ->> 'document_id',
        (v_item ->> 'expected_version_no')::integer, v_item ->> 'expected_content_digest',
        v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id');
      if v_link is null or (v_link ->> 'link_digest') is distinct from (v_item ->> 'expected_link_digest') then
        raise exception 'j102_evidence_unbound: document % is not associated with % % under this lock',
          v_item -> 'selector' ->> 'document_id',
          v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id'
          using errcode = '40001';
      end if;
      -- THE DOCUMENT'S OWN STATE AXES, read off F01's record and compared to the
      -- kernel's evidence contract. The pin proves the document has not MOVED; it
      -- says nothing about whether it is signed, delivered, effective or current.
      -- Q077's "ACTIVE signed ETL" and Q078's "lease signing" are statements about
      -- those axes, and without this a direct caller could execute a deal on an
      -- unsigned draft that is authentically pinned and correctly associated.
      -- F01 remains the sole authority for what the axes say; this only refuses to
      -- read past them.
      for v_axis in select * from jsonb_object_keys(
        coalesce(v_contract_evidence -> 'document_states', '{}'::jsonb)) loop
        if (v_record ->> v_axis)
             is distinct from (v_contract_evidence -> 'document_states' ->> v_axis) then
          raise exception 'j102_document_state_not_met: % requires % to be %, and document % is %',
            v_kind, v_axis, v_contract_evidence -> 'document_states' ->> v_axis,
            v_item -> 'selector' ->> 'document_id', coalesce(v_record ->> v_axis, 'unstated')
            using errcode = '22023';
        end if;
      end loop;
      -- F01's OWN identity for this document, not the selector the caller wrote.
      -- The two agree on every honest call because f01_read looks the document up
      -- by that id; taking the reader's answer is what makes the reference a
      -- re-read fact rather than an echo.
      v_reference := v_record -> 'neon_identity' -> 'document_id';
      v_facts := jsonb_build_object(
        'reference', v_reference,
        'document_id', v_record -> 'neon_identity' -> 'document_id',
        'version_no', v_record -> 'neon_identity' -> 'version_no',
        'content_digest', v_record -> 'neon_identity' -> 'content_digest',
        'signature_state', v_record -> 'signature_state',
        'validity_state', v_record -> 'validity_state',
        'version_state', v_record -> 'version_state',
        'delivery_state', v_record -> 'delivery_state');
    elsif (v_item ->> 'source') = 'f01_corporate_artifact' then
      v_body := ops.f01_stored_artifact(v_item -> 'selector' ->> 'artifact_digest');
      if v_body is null then
        raise exception 'j102_evidence_moved: artifact % is no longer readable',
          v_item -> 'selector' ->> 'artifact_digest' using errcode = '40001';
      end if;
      v_link := ops.j102_evidence_subject_link(
        'f01_corporate_artifact', v_item -> 'selector' ->> 'artifact_digest', 0,
        v_item -> 'selector' ->> 'artifact_digest',
        v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id');
      if v_link is null or (v_link ->> 'link_digest') is distinct from (v_item ->> 'expected_link_digest') then
        raise exception 'j102_evidence_unbound: artifact % is not associated with % % under this lock',
          v_item -> 'selector' ->> 'artifact_digest',
          v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id'
          using errcode = '40001';
      end if;
      -- THE STORED ARTIFACT'S OWN DIGEST, off ops.f01_stored_artifact's answer.
      -- This used to be copied from the caller's selector, which made the one
      -- fact this branch reports an echo of the request rather than a reading.
      v_reference := v_body -> 'artifact_digest';
      v_facts := jsonb_build_object(
        'reference', v_reference,
        'artifact_digest', v_body -> 'artifact_digest');
    elsif (v_item ->> 'source') = 'first_party_record' then
      v_body := ops.j102_first_party_record(
        v_item -> 'selector' ->> 'record_kind', v_item -> 'selector' ->> 'record_id');
      if v_body is null then
        raise exception 'j102_evidence_moved: % record % is no longer readable',
          v_item -> 'selector' ->> 'record_kind', v_item -> 'selector' ->> 'record_id'
          using errcode = '40001';
      end if;
      if (v_body ->> 'record_digest') is distinct from (v_item ->> 'expected_record_digest') then
        raise exception 'j102_evidence_moved: % record % is not the record this transition was decided against',
          v_item -> 'selector' ->> 'record_kind', v_item -> 'selector' ->> 'record_id'
          using errcode = '40001';
      end if;
      -- The record's OWN typed binding, re-read under the lock from the row
      -- rather than taken from the manifest that travelled here. The manifest's
      -- binding has already been proved identical to the primary subject above,
      -- so this closes the chain: stored row -> manifest -> subject being moved.
      if (v_body -> 'record' ->> 'subject_kind') is distinct from (v_item -> 'binding' ->> 'subject_kind')
         or (v_body -> 'record' ->> 'subject_id') is distinct from (v_item -> 'binding' ->> 'subject_id') then
        raise exception 'j102_evidence_unbound: % record % is bound to % %, not to the % % this transition moves',
          v_item -> 'selector' ->> 'record_kind', v_item -> 'selector' ->> 'record_id',
          v_body -> 'record' ->> 'subject_kind', v_body -> 'record' ->> 'subject_id',
          v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id'
          using errcode = '40001';
      end if;
      -- The stored row must be the record KIND the evidence contract names, read
      -- off the row rather than off the selector the caller wrote.
      if (v_body -> 'record' ->> 'record_kind')
           is distinct from (v_contract_evidence ->> 'record_kind') then
        raise exception 'j102_evidence_record_kind_mismatch: % is established from a % record, and this one is a %',
          v_kind, v_contract_evidence ->> 'record_kind', v_body -> 'record' ->> 'record_kind'
          using errcode = '22023';
      end if;
      -- H5 under the lock. WHO AUTHORED the fact, re-read from the row's own
      -- derived class column, so a partner performing the transition cannot
      -- launder an agent-authored closing date, commitment or failure reason.
      if (v_contract_evidence ->> 'requires_author_class') is not null
         and (v_body -> 'record' ->> 'recorded_by_authorization_class')
               is distinct from (v_contract_evidence ->> 'requires_author_class') then
        raise exception 'j102_evidence_author_class_not_permitted: % is authored by a %, and % holds %',
          v_kind, v_contract_evidence ->> 'requires_author_class',
          coalesce(v_body -> 'record' ->> 'recorded_by', 'an unnamed author'),
          coalesce(v_body -> 'record' ->> 'recorded_by_authorization_class', 'no class')
          using errcode = '42501';
      end if;
      -- Q094's date has to have ARRIVED. The relation already refuses a closing
      -- settlement with no date at all; this is the other half, and it is the
      -- same bound the kernel applies rather than a new one.
      if (v_contract_evidence ->> 'requires_closing_date') = 'true'
         and ops.f01_instant(v_body -> 'record' ->> 'closing_date') > now() then
        raise exception 'j102_closing_date_in_the_future: % names a closing date of %, which has not arrived',
          v_item -> 'selector' ->> 'record_id', v_body -> 'record' ->> 'closing_date'
          using errcode = '22023';
      end if;
      -- The two fields a coupled fact is DERIVED from -- Q094's closing date and
      -- Q096's cancellation reason -- taken from the stored row, so the writer
      -- compares the proposed state against the record instead of against the
      -- caller's copy of it.
      v_reference := v_body -> 'record' -> 'record_id';
      v_facts := jsonb_build_object(
        'reference', v_reference,
        'record_kind', v_body -> 'record' -> 'record_kind',
        'record_id', v_body -> 'record' -> 'record_id',
        'closing_date', v_body -> 'record' -> 'closing_date',
        'reason', v_body -> 'record' -> 'reason',
        'subject_kind', v_body -> 'record' -> 'subject_kind',
        'subject_id', v_body -> 'record' -> 'subject_id');
    elsif (v_item ->> 'source') = 'typed_approval' then
      -- Unreachable through the shipped store, which refuses these paths before
      -- opening a transaction. Routed to the private reader anyway, so a future
      -- caller that reaches here gets the same refusal rather than a gap.
      perform ops.j102_typed_approval(
        v_item -> 'selector' ->> 'approval_kind', v_item -> 'selector' ->> 'approval_ref');
    else
      raise exception 'j102_unknown_evidence_source: %', v_item ->> 'source' using errcode = '22023';
    end if;
    -- A pin that reached here without a readable reference is a pin whose reader
    -- returned no identity for it, and the history could not name it. It refuses
    -- rather than being recorded as an unnamed piece of evidence.
    if jsonb_typeof(v_reference) is distinct from 'string' then
      raise exception 'j102_evidence_reference_unresolved: % was re-read and its reader named no reference for the history to cite',
        v_kind using errcode = '40001';
    end if;
    v_checked := v_checked || jsonb_build_array(jsonb_build_object(
      'evidence_kind', v_item ->> 'evidence_kind',
      'source', v_item ->> 'source',
      -- The reference the READER returned. Everything the writer stamps into
      -- history about which evidence a transition rested on comes from here.
      'reference', v_reference,
      'reader', v_item ->> 'reader',
      'bound_subject_kind', v_item -> 'binding' ->> 'subject_kind',
      'bound_subject_id', v_item -> 'binding' ->> 'subject_id',
      'still_exact', true,
      'still_bound', true,
      'bound_to_primary_subject', true,
      'facts', v_facts));
  end loop;

  -- THE MANIFEST MUST BE EXACTLY ONE DECLARED ALTERNATIVE -- not a superset, not
  -- a subset, not two alternatives at once. Missing evidence is the obvious
  -- failure; EXTRA evidence is the subtle one, because a manifest carrying both
  -- an ETL and an approved equivalence cannot say which basis the engagement
  -- rests on, and one carrying an unrelated extra pin is a caller establishing
  -- something the transition never asked for.
  select array_agg(s order by s) into v_supplied_sorted from unnest(v_supplied) as s;
  for v_alternative in
    select * from jsonb_array_elements(v_contract -> 'required_evidence_alternatives')
  loop
    select array_agg(a order by a) into v_alternative_sorted
      from jsonb_array_elements_text(v_alternative) as a;
    if v_alternative_sorted = v_supplied_sorted then
      v_matched := true;
      exit;
    end if;
  end loop;
  if not v_matched then
    raise exception 'j102_evidence_alternative_not_satisfied: % is established from % and this manifest carries %',
      p_transition_id, v_contract -> 'required_evidence_alternatives',
      to_jsonb(v_supplied_sorted) using errcode = '22023';
  end if;
  return v_checked;
end;
$$;

comment on function ops.j102_recheck_evidence(jsonb,text,text,text) is
  'Re-read the EXACT evidence pins a transition was decided against, inside the transaction that holds its subject locks, and bind every one of them to the ONE primary subject that transition advances. Any movement, any wrong source or record kind, any wrong author class, any binding naming a different subject, and any manifest that is not exactly one declared alternative refuses the whole coupled transition. Existence is not the check; sameness and identity are. Returns, per pin, the CANONICAL REFERENCE the reader itself named -- F01''s document_id, the stored artifact''s digest, the committed record''s record_id -- and the FACTS read off the stored row (the closing date, the reason), so the writer can compare both the state it is about to write and the history it is about to append against the evidence rather than against the caller.';

-- ---------------------------------------------------------------------------
-- THE ONE TRANSITION WRITER.
--
-- Q082's coupled facts, made structural: every proposed subject and every event
-- lands in ONE transaction or none of them does. There is no partial apply, no
-- per-subject writer a caller could call twice, and no free-form stage update
-- anywhere in this file.
--
-- THE LOCK ORDER IS DECLARED AND ACYCLIC. Every writer here takes locks in
-- exactly this order, and a future writer MUST slot into it:
--
--   tier 1  j102:request:<tenant>:<key>          (j102_replay_outcome; shared
--           with the private claim and re-entrant within one transaction, so a
--           caller that probes for a replay and then writes does not self-block)
--   tier 2  j102:subject:<tenant>:<kind>:<id>    (every subject this transition
--           touches, acquired in ASCENDING kind:id order)
--
-- ASCENDING ORDER IS WHAT MAKES TIER 2 SAFE. Two concurrent transitions touching
-- the same pair of subjects acquire them in the same sequence, so one waits and
-- the other proceeds instead of each holding what the other needs. The ordering
-- is applied to the union of the compare-and-swap operands and the proposed
-- subjects, so a subject that is only READ is locked exactly as one that is
-- written -- a decision taken against an unlocked read is a decision taken
-- against a value that can move underneath it.
-- ---------------------------------------------------------------------------
-- ---------------------------------------------------------------------------
-- OWNER RULING (c), 2026-09-25: THE SPONSORING PARTNER, DERIVED.
--
-- A verified partner's own session is its own sponsor. Anyone else -- a
-- sponsored agent, or a partner whose call ran on the writer credential -- is
-- recorded under the sponsor the SERVER established for this transaction
-- (mcp.js setWriterActorContext sets carr.sponsoring_human_slug from the
-- authenticated grant, transaction-local, never from a caller argument). No
-- verified sponsor, no write: an unattributable row is refused, not defaulted.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_sponsoring_partner()
returns text language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_principal jsonb := ops.f01_principal();
  v_setting text := nullif(current_setting('carr.sponsoring_human_slug', true), '');
begin
  if v_principal ->> 'authorization_class' = 'verified_partner' then
    if v_setting is not null and v_setting is distinct from v_principal ->> 'actor_slug' then
      raise exception 'j102_sponsor_context_conflict: % is a verified partner, and this transaction names % as its sponsor',
        v_principal ->> 'actor_slug', v_setting using errcode = '42501';
    end if;
    return v_principal ->> 'actor_slug';
  end if;
  if v_setting is null or v_setting not in ('joe', 'dell') then
    raise exception 'j102_sponsor_unavailable: % acts as % and this transaction carries no verified sponsoring partner',
      coalesce(v_principal ->> 'actor_slug', 'an unnamed actor'),
      coalesce(v_principal ->> 'authorization_class', 'no class') using errcode = '42501';
  end if;
  return v_setting;
end;
$$;

comment on function ops.j102_sponsoring_partner() is
  'Owner ruling (c): the partner a lifecycle write is recorded under -- the partner themselves, or the server-established sponsor of an agent or writer-credential session. Refuses when none is established.';

create or replace function ops.j102_apply_transition(
  p_transition_id text,
  p_expected_state_digests jsonb,
  p_subject_envelopes jsonb,
  p_event_envelopes jsonb,
  p_evidence_recheck jsonb,
  p_idempotency_key text,
  p_request_digest text,
  p_diagnostics jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_sponsor text := ops.j102_sponsoring_partner();
  -- BLOCK-1. THE ACTOR'S CLASS, DERIVED FROM THE SAME PRINCIPAL THE ACTOR IS.
  -- The writer used to ask only WHO was writing. Whether that who was ENTITLED to
  -- perform this particular transition lived in JavaScript -- `authorityOnly` in
  -- the store and `permitted_actor_classes` in the kernel -- and the EXECUTE grant
  -- on this function reaches carr_writer, which always resolves to a sponsored
  -- agent. f01_principal() keys on session_user, so it cannot be forged from a
  -- GUC the caller controls.
  v_class text := ops.f01_principal() ->> 'authorization_class';
  v_policy jsonb := ops.j102_admission_policy();
  v_contract jsonb;
  v_writes jsonb;
  v_subjects jsonb;
  v_subject_rule jsonb;
  v_primary_kind text;
  v_primary_id text;
  v_proposed_states jsonb := '{}'::jsonb;
  v_proposed_ids jsonb := '{}'::jsonb;
  v_stored_states jsonb := '{}'::jsonb;
  v_prior_by_kind jsonb := '{}'::jsonb;
  v_context_by_kind jsonb := '{}'::jsonb;
  v_facts_by_kind jsonb := '{}'::jsonb;
  v_stored_state jsonb;
  v_prior jsonb;
  v_child jsonb;
  v_parent_field text;
  v_axis text;
  v_permitted jsonb;
  v_field text;
  v_chained boolean;
  v_condition jsonb;
  v_expected_value jsonb;
  v_actual_value jsonb;
  v_effect jsonb;
  v_context_rule jsonb;
  v_context_id text;
  v_required_events jsonb := '[]'::jsonb;
  v_supplied_events jsonb := '[]'::jsonb;
  v_event_spec jsonb;
  v_event_key jsonb;
  v_matched_index integer;
  v_index integer;
  v_created_kinds jsonb := '[]'::jsonb;
  v_item jsonb;
  -- H4. THE CLOCK IS THE DATABASE'S, and it is read ONCE here. Every writer used
  -- to take updated_at and recorded_at from the supplied envelope and store them
  -- unexamined, so anything holding the carr_writer execute grant could backdate
  -- lifecycle state and its history. now() is the transaction timestamp, so every
  -- row this call writes shares one instant and a receipt can never appear to
  -- precede the event it binds.
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_operation text := p_diagnostics ->> 'operation';
  v_replay jsonb;
  v_key text;
  v_envelope jsonb;
  v_record jsonb;
  v_state jsonb;
  v_kind text;
  v_id text;
  v_stored text;
  v_expected text;
  v_proposed jsonb := '{}'::jsonb;
  v_subject_digests jsonb := '{}'::jsonb;
  v_event_digests jsonb := '[]'::jsonb;
  v_checked jsonb;
  v_readback jsonb := '{}'::jsonb;
  v_result jsonb;
  -- HIGH-5/HIGH-6. THE CONSTANTS THE STORE AND THE KERNEL STAMP, read off the
  -- admission map so there is one written copy of each in this file and the Node
  -- parity suite can assert all three equal their exported originals.
  v_subject_schema text;
  v_event_record_schema text;
  v_event_schema text;
  v_subject_keys jsonb;
  v_event_keys jsonb;
  v_identity_keys jsonb;
  -- HIGH-6. The evidence references the recheck ACTUALLY re-read, canonicalised
  -- once and compared against every event this call appends.
  v_canonical_refs jsonb := '{}'::jsonb;
  v_canonical_count integer;
  v_seen jsonb;
  v_event jsonb;
begin
  if v_operation is null then
    raise exception 'j102_operation_required' using errcode = '22023';
  end if;
  if jsonb_typeof(p_subject_envelopes) is distinct from 'array'
     or jsonb_array_length(p_subject_envelopes) < 1 then
    raise exception 'j102_no_subject_envelopes' using errcode = '22023';
  end if;
  if jsonb_typeof(p_event_envelopes) is distinct from 'array'
     or jsonb_array_length(p_event_envelopes) < 1 then
    -- A state change with no event is a change nobody can audit afterwards.
    raise exception 'j102_no_event_envelopes: every transition appends its history'
      using errcode = '22023';
  end if;
  -- M-3. THE COMPARE-AND-SWAP OPERAND MAP, ASKED ABOUT BEFORE ANYTHING INDEXES
  -- INTO IT. A SQL NULL here propagated through every `?` and `->` below as NULL,
  -- which `if` reads as false, so THREE named refusals were skipped in silence --
  -- both creation gates and j102_expected_state_digest_missing -- and the call
  -- refused further down for a different reason, with `created_subject_kinds`
  -- reporting empty for a creation that had been attempted. A JSON scalar was
  -- worse: it reached jsonb_object_keys and raised an unnamed `cannot call
  -- jsonb_object_keys on a scalar` rather than any J102 refusal at all. Both now
  -- refuse HERE, by name, before a key is claimed.
  if jsonb_typeof(p_expected_state_digests) is distinct from 'object' then
    raise exception 'j102_expected_state_digests_not_an_object: every proposed subject carries a compare-and-swap operand and a creation carries an explicit null; this call supplies %',
      coalesce(jsonb_typeof(p_expected_state_digests), 'no operand map at all')
      using errcode = '22023';
  end if;

  -- ==========================================================================
  -- BLOCK-1 -- ADMISSION, BEFORE A KEY IS CLAIMED AND BEFORE ANY STATE IS READ.
  --
  -- These three questions read no lifecycle state at all, so asking them here
  -- costs the replay-before-state ordering nothing: a caller who may not perform
  -- this transition never reaches the idempotency table and cannot burn a key on
  -- an attempt that was never admissible.
  -- ==========================================================================

  -- 1. IS IT A TRANSITION AT ALL. p_transition_id used to be an unchecked string
  --    that only ever appeared in a receipt and in an event column, so history
  --    could name a transition that does not exist.
  v_contract := v_policy -> 'transitions' -> p_transition_id;
  if v_contract is null then
    raise exception 'j102_unknown_transition: % is not a transition this rail performs',
      coalesce(p_transition_id, 'unnamed') using errcode = '22023';
  end if;

  -- 2. MAY THIS OPERATION PERFORM IT. The operation and the transition were two
  --    independent unchecked strings: the operation was validated only against
  --    the idempotency vocabulary and the transition against nothing, so a caller
  --    could name the routine `record-deal-axis` beside the partner-only
  --    `record-deal-closing` and satisfy whichever gate keyed on which. Binding
  --    the pair is also what makes the record-deal-axis dispatch mean anything:
  --    that operation pairs with the four orthogonal axis transitions and with
  --    nothing else, so it cannot be used to reach a lifecycle axis.
  if not (v_contract -> 'operations' ? v_operation) then
    raise exception 'j102_operation_transition_mismatch: % is not performed by the % operation; it is performed by %',
      p_transition_id, v_operation, v_contract -> 'operations' using errcode = '22023';
  end if;

  -- 3. MAY THIS ACTOR'S CLASS PERFORM IT. The three partner-only transitions --
  --    commit-winning-property, record-deal-closing and cancel-pending-deal --
  --    were reachable by anything holding this function's EXECUTE grant, which
  --    includes carr_writer.
  if not (v_contract -> 'permitted_actor_classes' ? v_class) then
    raise exception 'j102_actor_class_not_permitted: % is performed by %, and % holds %',
      p_transition_id, v_contract -> 'permitted_actor_classes', v_actor, v_class
      using errcode = '42501';
  end if;
  v_writes := v_contract -> 'writes';
  v_subjects := v_contract -> 'subjects';
  v_subject_schema := v_policy ->> 'stored_subject_schema_version';
  v_event_record_schema := v_policy ->> 'stored_event_schema_version';
  v_event_schema := v_policy ->> 'event_schema_version';
  v_subject_keys := v_policy -> 'stored_subject_record_keys';
  v_event_keys := v_policy -> 'stored_event_record_keys';
  v_identity_keys := v_policy -> 'event_identity_keys';
  -- THE PRIMARY SUBJECT KIND IS THE CONTRACT'S, decided here and never read off
  -- the request. Everything below -- which row must already exist, which evidence
  -- must bind to what, which events may be appended -- hangs off this line.
  v_primary_kind := v_contract ->> 'subject_kind';

  v_replay := ops.j102_claim_idempotency(v_operation, p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;

  -- TIER 2, IN ASCENDING ORDER over the union of read and written subjects.
  for v_key in
    select k from (
      select jsonb_object_keys(p_expected_state_digests) as k
      union
      select (e -> 'record' ->> 'subject_kind') || ':' || (e -> 'record' ->> 'subject_id')
        from jsonb_array_elements(p_subject_envelopes) as e
    ) s order by k collate "C"
  loop
    perform pg_advisory_xact_lock(hashtextextended(
      'j102:subject:' || ops.f01_tenant() || ':' || v_key, 0));
  end loop;

  -- EVERY PROPOSED SUBJECT MUST CARRY AN OPERAND, and a caller that omits one is
  -- refused rather than defaulted.
  --
  -- BLOCK-1. The compare-and-swap loop used to iterate the SUPPLIED MAP, so a
  -- subject that appeared only in the envelopes -- a creation -- was never
  -- checked at all, and the upsert below silently overwrote whatever already
  -- held that key. A caller naming an existing deal id as its new deal replaced
  -- that deal's authoritative current state, closing date and all, under a
  -- different assignment, while its events stayed behind: history and current
  -- state disagreeing about what that id is. Iterating the UNION, and demanding
  -- a key for every proposed subject, is what closes it. A creation's operand is
  -- an explicit JSON null, which the `is distinct from` below reads as "this
  -- subject must be ABSENT" -- so a collision refuses instead of upserting, and
  -- omitting the key is not a way to ask for the old behaviour.
  for v_envelope in select * from jsonb_array_elements(p_subject_envelopes) loop
    v_record := v_envelope -> 'record';
    -- Named rather than left to a cast error deeper in, on the same terms as the
    -- operand-map guard above: an envelope whose `record` is a scalar, an array
    -- or absent reaches jsonb_object_keys below.
    if jsonb_typeof(v_record) is distinct from 'object' then
      raise exception 'j102_subject_envelope_not_an_object: a proposed subject envelope carries % where its record should be',
        coalesce(jsonb_typeof(v_record), 'nothing') using errcode = '22023';
    end if;
    v_kind := v_record ->> 'subject_kind';
    v_id := v_record ->> 'subject_id';
    v_key := v_kind || ':' || v_id;
    -- BLOCK-2. WHICH SUBJECTS THIS TRANSITION MAY WRITE AT ALL. The writer used
    -- to write whatever it was handed, so an allowed operation could carry an
    -- unrelated subject alongside its real one and rewrite that row -- a new
    -- updated_by and updated_at on a client who had nothing to do with it, at
    -- best, and an arbitrary state change at worst.
    if v_kind is null or not (v_writes ? v_kind) then
      raise exception 'j102_subject_kind_not_written_by_transition: % writes the subjects %, and this request proposes a %',
        p_transition_id, v_writes, coalesce(v_kind, 'subject of no kind')
        using errcode = '22023';
    end if;
    -- THE ACTOR AND THE INSTANT, CHECKED HERE rather than in the write loop at
    -- the bottom. They are properties of the REQUEST and depend on no stored row,
    -- so asking them before anything is read costs nothing and means a backdated
    -- or actor-injected envelope is refused by name instead of surviving as far
    -- as the insert.
    if (v_record ->> 'updated_by') is distinct from v_actor then
      raise exception 'j102_actor_injection_refused: updated_by is derived, never supplied'
        using errcode = '42501';
    end if;
    if (v_record ->> 'sponsoring_partner') is distinct from v_sponsor then
      raise exception 'j102_sponsor_mismatch: the write is recorded under %, derived from the authenticated login, and the request names %',
        v_sponsor, coalesce(v_record ->> 'sponsoring_partner', 'none') using errcode = '42501';
    end if;
    -- H4. THE INSTANT IS THE DATABASE'S, verified against this transaction's own
    -- clock and then stamped from it. A caller-chosen updated_at is refused here
    -- rather than stored, so no grant holder can backdate lifecycle state.
    if (v_record ->> 'updated_at') is distinct from v_txn_now_text then
      raise exception 'j102_clock_injection_refused: updated_at is the database transaction time %, not %',
        v_txn_now_text, coalesce(v_record ->> 'updated_at', 'null') using errcode = '42501';
    end if;
    if ops.f01_digest_jsonb(v_record) is distinct from (v_envelope ->> 'record_digest') then
      raise exception 'j102_subject_digest_mismatch: the supplied subject does not hash to its claim'
        using errcode = '22000';
    end if;
    -- ======================================================================
    -- HIGH-5. THE SUBJECT'S OWN PROVENANCE, BOUND TO THE TRANSITION THAT RAN.
    --
    -- `established_by_transition` is what ops.j102_subject returns as a subject's
    -- provenance, what ops.j102_read('subject') surfaces, and what this function
    -- echoes back in its own receipt's readback. It was compared to NOTHING. The
    -- digest check above is no help at all here: a forged value is hashed into
    -- its own claim, so a fully canonical `record-invoice-issued` -- real
    -- evidence, exact target, correct event -- could leave a deal's authoritative
    -- row reporting that its state was established by a partner-only
    -- `record-deal-closing` that never happened.
    --
    -- This is the exact binding the EVENT side already had (`transition_id`
    -- below, CHECK-bound to its column), applied to the subject's mirror of it.
    -- It derives nothing: the transition that ran is p_transition_id, already
    -- validated against the map, and the envelope must say so or refuse.
    -- ======================================================================
    if (v_record ->> 'established_by_transition') is distinct from p_transition_id then
      raise exception 'j102_subject_provenance_mismatch: this call applies %, and the % % claims to be established by %; a subject may not carry provenance for a transition that did not run',
        p_transition_id, v_kind, v_id,
        coalesce(v_record ->> 'established_by_transition', 'nothing') using errcode = '42501';
    end if;
    -- THE ENVELOPE IS THE STORE'S SHAPE, not something that resembles it. The
    -- schema version and the tenant are CONSTANTS on both sides -- the store
    -- stamps V5_J102_STORED_SUBJECT_SCHEMA_VERSION and the kernel owns the
    -- tenant -- so a record claiming another schema or another tenant is a record
    -- this rail did not produce, and it refuses here rather than being stored and
    -- read back later as authoritative. The relation restates both as CHECKs.
    if (v_record ->> 'schema_version') is distinct from v_subject_schema then
      raise exception 'j102_subject_schema_version_mismatch: a stored lifecycle subject is %, and this request carries %',
        v_subject_schema, coalesce(v_record ->> 'schema_version', 'nothing')
        using errcode = '22023';
    end if;
    if (v_record ->> 'tenant') is distinct from ops.f01_tenant()
       or (v_envelope ->> 'tenant') is distinct from ops.f01_tenant() then
      raise exception 'j102_subject_tenant_mismatch: this database is tenant %, and a proposed % names %',
        ops.f01_tenant(), coalesce(v_kind, 'subject'),
        coalesce(v_record ->> 'tenant', v_envelope ->> 'tenant', 'nothing')
        using errcode = '42501';
    end if;
    if (v_envelope ->> 'record_kind') is distinct from 'stored_lifecycle_subject' then
      raise exception 'j102_subject_record_kind_mismatch: this writer stores lifecycle subjects, and this envelope is a %',
        coalesce(v_envelope ->> 'record_kind', 'record of no kind') using errcode = '22023';
    end if;
    -- NO DANGLING CLAIMED PROVENANCE BESIDE THE BOUND ONE. Every key of the
    -- record is one the store writes, and every key the store writes is present.
    -- Without this, a caller could hash a second, unchecked `approved_by`,
    -- `established_at` or `source_of_truth` into the same bytes and have
    -- j102_read hand it to a reviewer as part of the authoritative row -- which
    -- is the same defect as the forged provenance above, one key over.
    if exists (select 1 from jsonb_object_keys(v_record) as k
                where not (v_subject_keys @> jsonb_build_array(k)))
       or exists (select 1 from jsonb_array_elements_text(v_subject_keys) as k
                   where not (v_record ? k)) then
      raise exception 'j102_subject_record_shape_unrecognised: a stored lifecycle subject carries exactly %, and this request carries %',
        v_subject_keys,
        coalesce((select jsonb_agg(k order by k) from jsonb_object_keys(v_record) as k),
                 '[]'::jsonb) using errcode = '22023';
    end if;
    -- And the record's own header must agree with the state it wraps: the header
    -- is what the primary key columns are CHECK-bound to, and a header naming one
    -- subject over a state describing another is a row whose identity depends on
    -- which half of it a reader looks at.
    if (v_record ->> 'subject_kind') is distinct from (v_record -> 'state' ->> 'subject_kind')
       or (v_record ->> 'subject_id') is distinct from (v_record -> 'state' ->> 'subject_id') then
      raise exception 'j102_subject_header_state_mismatch: the envelope names % %, and the state it carries describes % %',
        v_kind, v_id, coalesce(v_record -> 'state' ->> 'subject_kind', 'nothing'),
        coalesce(v_record -> 'state' ->> 'subject_id', 'nothing') using errcode = '22023';
    end if;
    -- ======================================================================
    -- ROOT BLOCKER 1. WHO MAY BE CREATED, AND WHO MAY NOT.
    --
    -- The previous correction let ANY proposed subject be created on an explicit
    -- null operand, including the PRIMARY subject of the transition -- the one
    -- the kernel always loads and never creates. That was not a bootstrap, it was
    -- a bypass with a bootstrap's name on it: a created primary has no committed
    -- row, so its `from` prerequisites, its instrument kind and every prior-state
    -- condition below have nothing to be checked against. A caller could invent a
    -- deal already `executed`, an assignment already `committed`, a relationship
    -- already a `client`, and the receipt's `prerequisites_checked: false` was a
    -- true statement about a check that never ran rather than a refusal.
    --
    -- SO THE PRIMARY SUBJECT MUST ALREADY EXIST. Creation is admitted only where
    -- the KERNEL itself creates a subject, which is exactly twice: the engagement
    -- of establish-client-and-engagement and the pending deal of
    -- commit-winning-property, both of them COUPLED subjects of a transition
    -- whose primary is loaded. Every other proposed subject must be an update of
    -- a row that is already there.
    --
    -- THIS WRITER THEREFORE HAS NO BOOTSTRAP, AND NEVER WILL. That is a statement
    -- about ops.j102_apply_transition and about nothing else, and it is worth
    -- being exact because it used to be a statement about the whole rail and no
    -- longer is: the first relationship, assignment and property negotiation are
    -- created by ops.j102_initialize_subject, a SEPARATE writer reading a separate
    -- half of the admission map, which performs no transition and therefore has no
    -- prerequisite to make vacuous. The two are kept apart precisely so that
    -- folding creation back in behind a flag is not one boolean away.
    --
    -- NOTHING ABOUT THE REFUSAL BELOW IS RELAXED BY THAT. A transition still
    -- advances a row that is already committed, and a caller that proposes to
    -- create the subject it claims to advance is refused here by name whatever
    -- else can now create one.
    -- ======================================================================
    v_subject_rule := v_subjects -> v_kind;
    if v_subject_rule is null then
      raise exception 'j102_subject_kind_not_written_by_transition: % declares no rule for a %',
        p_transition_id, v_kind using errcode = '22023';
    end if;
    if (p_expected_state_digests ? v_key) and (p_expected_state_digests -> v_key) = 'null'::jsonb then
      if v_kind = v_primary_kind then
        raise exception 'j102_primary_subject_creation_refused: % advances an EXISTING % and creates none; % is proposed with a null compare-and-swap operand, which is a creation',
          p_transition_id, v_primary_kind, v_key using errcode = '42501';
      end if;
      if (v_subject_rule ->> 'mode') is distinct from 'create' then
        raise exception 'j102_subject_creation_not_permitted: % updates the % it is given and creates only %; % is proposed as a creation',
          p_transition_id, v_kind,
          coalesce((select string_agg(k, ', ' order by k) from jsonb_object_keys(v_subjects) as k
                     where (v_subjects -> k ->> 'mode') = 'create'), 'nothing'),
          v_key using errcode = '42501';
      end if;
      v_created_kinds := v_created_kinds || jsonb_build_array(v_kind);
    end if;
    -- ONE SUBJECT PER KIND. Every shipped transition proposes at most one subject
    -- of each kind, and requiring it makes the primary subject unique by
    -- construction -- which is what the evidence binding is compared against. It
    -- also removes M-e: two proposed subjects of one kind can no longer collide
    -- in the receipt's readback, because they can no longer both be proposed.
    if v_proposed_ids ? v_kind then
      raise exception 'j102_duplicate_proposed_subject_kind: % proposes two % subjects (% and %); one transition advances one subject of each kind',
        p_transition_id, v_kind, v_proposed_ids ->> v_kind, v_id using errcode = '22023';
    end if;
    v_proposed_ids := v_proposed_ids || jsonb_build_object(v_kind, v_id);
    v_proposed_states := v_proposed_states
      || jsonb_build_object(v_kind, v_envelope -> 'record' -> 'state');
    if not (p_expected_state_digests ? v_key) then
      raise exception 'j102_expected_state_digest_missing: % is proposed with no compare-and-swap operand; a creation must supply an explicit null',
        v_key using errcode = '22023';
    end if;
    -- The envelope's own prior_state_digest and the operand are the same claim
    -- written twice, and they must agree: a pair that disagrees is a request
    -- whose history would describe a version its own check did not enforce.
    if (v_envelope -> 'record' ->> 'prior_state_digest')
         is distinct from (p_expected_state_digests ->> v_key) then
      raise exception 'j102_prior_state_digest_mismatch: % declares prior state % and its operand is %',
        v_key, coalesce(v_envelope -> 'record' ->> 'prior_state_digest', 'null'),
        coalesce(p_expected_state_digests ->> v_key, 'null') using errcode = '22023';
    end if;
    v_proposed := v_proposed || jsonb_build_object(v_key, true);
  end loop;

  -- ==========================================================================
  -- ROOT BLOCKER 2, FIRST HALF -- THE REQUIRED SUBJECT SET AND THE REQUIRED
  -- EVENT SET, both asked BEFORE any state is compared because both are
  -- properties of the request alone.
  --
  -- A SUBSET OF A COUPLED WRITE IS NOT A COUPLED WRITE. `writes` said which
  -- subjects a transition MAY touch, so a call could send the deal and omit the
  -- assignment `cancel-pending-deal` also returns to the market -- leaving an
  -- assignment still pointing at a cancelled deal, still holding a selected
  -- property, still `committed`. Q082's "coupled facts commit atomically or
  -- refuse" is a statement about the WHOLE set, and this is where the whole set
  -- is required.
  -- ==========================================================================
  for v_kind in select * from jsonb_object_keys(v_subjects) loop
    if not (v_proposed_ids ? v_kind) then
      raise exception 'j102_required_subject_not_proposed: % lands % together or not at all, and this request proposes only %',
        p_transition_id,
        (select string_agg(k, ', ' order by k) from jsonb_object_keys(v_subjects) as k),
        coalesce((select string_agg(k, ', ' order by k) from jsonb_object_keys(v_proposed_ids) as k),
                 'nothing')
        using errcode = '22023';
    end if;
  end loop;

  -- THE ONE PRIMARY SUBJECT, unique by construction because the loop above
  -- refused a second subject of any kind. Every evidence pin binds to exactly
  -- this identity, and every event names a subject this call advances.
  if not (v_proposed_ids ? v_primary_kind) then
    raise exception 'j102_primary_subject_not_proposed: % advances a %, and this request proposes none',
      p_transition_id, v_primary_kind using errcode = '22023';
  end if;
  v_primary_id := v_proposed_ids ->> v_primary_kind;

  -- ==========================================================================
  -- THE EVENT SET IS EXACT: not "at least one event", which is what the writer
  -- required before.
  --
  -- Three separate failures live in the gap between "at least one" and "exactly
  -- these". A call could append ONE event for a transition the kernel gives two,
  -- so the history records the deal cancellation and not the assignment's return.
  -- It could append an EXTRA event that never happened. And because the kernel's
  -- own event kind is what a reader searches history by, it could append a
  -- correctly-bound event under a plausible WRONG KIND -- `lease_executed` on a
  -- purchase, an invented `payment_paid` event -- which is the residual the
  -- previous correction acknowledged and left open.
  --
  -- The required set comes from the kernel's evaluator: the event kinds
  -- lifecycleEvent() is called with, on the subjects it is called with. EVERY ONE
  -- OF THEM IS A LITERAL and none is derived. An earlier revision of this file
  -- carried a derived-kind rule for record-payment, on the belief that the kernel
  -- spelled that event `payment_${level}`; it does not, and never did. That
  -- string is the transition's REASON_ID, the event kind beside it is the
  -- constant `payment_recorded` (cre-lifecycle.v5.js axisResult call site), and
  -- the derivation, the branch that implemented it and the prose that described
  -- it as intentional are all gone rather than left to be read as the contract.
  -- ==========================================================================
  for v_event_spec in select * from jsonb_array_elements(v_contract -> 'events') loop
    v_kind := v_event_spec ->> 'subject';
    v_required_events := v_required_events || jsonb_build_array(jsonb_build_object(
      'event_kind', v_event_spec ->> 'event_kind',
      'subject_kind', v_kind,
      'subject_id', v_proposed_ids ->> v_kind));
  end loop;

  for v_envelope in select * from jsonb_array_elements(p_event_envelopes) loop
    v_record := v_envelope -> 'record';
    if jsonb_typeof(v_record) is distinct from 'object'
       or jsonb_typeof(v_record -> 'event') is distinct from 'object' then
      raise exception 'j102_event_envelope_not_an_object: an event envelope carries % where its record and nested event should be',
        coalesce(jsonb_typeof(v_record), 'nothing') using errcode = '22023';
    end if;
    if (v_record ->> 'recorded_by') is distinct from v_actor then
      raise exception 'j102_actor_injection_refused: recorded_by is derived, never supplied'
        using errcode = '42501';
    end if;
    if (v_record ->> 'sponsoring_partner') is distinct from v_sponsor then
      raise exception 'j102_sponsor_mismatch: the event is recorded under %, derived from the authenticated login, and the request names %',
        v_sponsor, coalesce(v_record ->> 'sponsoring_partner', 'none') using errcode = '42501';
    end if;
    if (v_record ->> 'recorded_at') is distinct from v_txn_now_text then
      raise exception 'j102_clock_injection_refused: recorded_at is the database transaction time %, not %',
        v_txn_now_text, coalesce(v_record ->> 'recorded_at', 'null') using errcode = '42501';
    end if;
    -- HIGH-1, FIRST HALF. THE EVENT MUST HASH TO ITS OWN CLAIM, exactly as a
    -- subject envelope must. The insert RECOMPUTES event_digest and
    -- envelope_digest, so the table's CHECK constraints were trivially satisfied
    -- and j102_verify_envelope read the row back as healthy -- while the
    -- envelope's own `record_digest`, which the store populates and which is
    -- stored durably INSIDE the hashed bytes, could say something else entirely.
    -- F01 binds both on its own documents; so does this now.
    if ops.f01_digest_jsonb(v_record) is distinct from (v_envelope ->> 'record_digest') then
      raise exception 'j102_event_digest_mismatch: the supplied event does not hash to its claim'
        using errcode = '22000';
    end if;
    -- The history must name the transition that actually ran.
    if (v_record ->> 'transition_id') is distinct from p_transition_id then
      raise exception 'j102_event_transition_mismatch: this call applies %, and an event claims %',
        p_transition_id, coalesce(v_record ->> 'transition_id', 'nothing') using errcode = '22023';
    end if;
    -- HIGH-5's other half, on the history side. Schema version, tenant and the
    -- record's key set are the store's constants and the store's shape, checked
    -- here because they are properties of the REQUEST alone; the nested event's
    -- own schema version is the KERNEL's constant and is checked with it.
    if (v_record ->> 'schema_version') is distinct from v_event_record_schema then
      raise exception 'j102_event_schema_version_mismatch: a stored lifecycle event is %, and this request carries %',
        v_event_record_schema, coalesce(v_record ->> 'schema_version', 'nothing')
        using errcode = '22023';
    end if;
    if (v_record -> 'event' ->> 'schema_version') is distinct from v_event_schema then
      raise exception 'j102_event_payload_schema_version_mismatch: the kernel builds every lifecycle event as %, and this request carries %',
        v_event_schema, coalesce(v_record -> 'event' ->> 'schema_version', 'nothing')
        using errcode = '22023';
    end if;
    if (v_record ->> 'tenant') is distinct from ops.f01_tenant()
       or (v_envelope ->> 'tenant') is distinct from ops.f01_tenant() then
      raise exception 'j102_event_tenant_mismatch: this database is tenant %, and an event names %',
        ops.f01_tenant(),
        coalesce(v_record ->> 'tenant', v_envelope ->> 'tenant', 'nothing') using errcode = '42501';
    end if;
    if (v_envelope ->> 'record_kind') is distinct from 'stored_lifecycle_event' then
      raise exception 'j102_event_record_kind_mismatch: this writer appends lifecycle events, and this envelope is a %',
        coalesce(v_envelope ->> 'record_kind', 'record of no kind') using errcode = '22023';
    end if;
    if exists (select 1 from jsonb_object_keys(v_record) as k
                where not (v_event_keys @> jsonb_build_array(k)))
       or exists (select 1 from jsonb_array_elements_text(v_event_keys) as k
                   where not (v_record ? k)) then
      raise exception 'j102_event_record_shape_unrecognised: a stored lifecycle event carries exactly %, and this request carries %',
        v_event_keys,
        coalesce((select jsonb_agg(k order by k) from jsonb_object_keys(v_record) as k),
                 '[]'::jsonb) using errcode = '22023';
    end if;
    v_supplied_events := v_supplied_events || jsonb_build_array(jsonb_build_object(
      'event_kind', v_record -> 'event' ->> 'event_kind',
      'subject_kind', v_record -> 'event' ->> 'subject_kind',
      'subject_id', v_record -> 'event' ->> 'subject_id'));
  end loop;

  if jsonb_array_length(v_supplied_events) <> jsonb_array_length(v_required_events) then
    raise exception 'j102_event_set_mismatch: % appends exactly % and this request carries %',
      p_transition_id, v_required_events, v_supplied_events using errcode = '22023';
  end if;
  -- Matched pairwise and consumed, so a request cannot satisfy a two-event
  -- contract by sending the same event twice.
  for v_event_key in select * from jsonb_array_elements(v_required_events) loop
    v_matched_index := null;
    v_index := 0;
    for v_item in select * from jsonb_array_elements(v_supplied_events) loop
      if v_matched_index is null and v_item = v_event_key then
        v_matched_index := v_index;
      end if;
      v_index := v_index + 1;
    end loop;
    if v_matched_index is null then
      raise exception 'j102_event_missing_or_wrong: % appends a % event on % %, and this request carries %',
        p_transition_id, v_event_key ->> 'event_kind', v_event_key ->> 'subject_kind',
        v_event_key ->> 'subject_id', v_supplied_events using errcode = '22023';
    end if;
    v_supplied_events := v_supplied_events - v_matched_index;
  end loop;
  if jsonb_array_length(v_supplied_events) <> 0 then
    raise exception 'j102_event_not_produced_by_transition: % appends %, and this request carries the extra %',
      p_transition_id, v_contract -> 'events', v_supplied_events using errcode = '42501';
  end if;

  -- THE EVIDENCE RECHECK, under the locks already taken and before any state is
  -- compared or written, bound to the ONE primary subject this transition
  -- advances. It returns the facts it read off the stored rows, which is what the
  -- coupled-fact comparison below is decided against.
  v_checked := ops.j102_recheck_evidence(
    p_evidence_recheck, p_transition_id, v_primary_kind, v_primary_id);
  for v_item in select * from jsonb_array_elements(v_checked) loop
    v_facts_by_kind := v_facts_by_kind ||
      jsonb_build_object(v_item ->> 'evidence_kind', coalesce(v_item -> 'facts', '{}'::jsonb));
  end loop;

  -- THE COMPARE-AND-SWAP, decided against the STORED row under the lock, over the
  -- UNION of the supplied operands and the proposed subjects. Every operand is
  -- checked, including the ones for subjects this transition only read: a
  -- prerequisite that moved invalidates the decision exactly as a target that
  -- moved does. And presence AND ABSENCE are both enforced -- a null operand for
  -- a subject that now exists refuses, which is the creation collision.
  for v_key in
    select k from (
      select jsonb_object_keys(p_expected_state_digests) as k
      union
      select jsonb_object_keys(v_proposed)
    ) s order by k collate "C"
  loop
    v_kind := split_part(v_key, ':', 1);
    v_id := substr(v_key, length(v_kind) + 2);
    v_expected := p_expected_state_digests ->> v_key;
    v_stored := null;
    v_stored_state := null;
    select c.envelope -> 'record' -> 'state' into v_stored_state
      from ops.j102_subject_current c
     where c.tenant = ops.f01_tenant() and c.subject_kind = v_kind and c.subject_id = v_id;
    if found then
      -- The STORED state itself is kept, not only its digest. The compare-and-swap
      -- proves the row has not moved; the checks below need to know what the row
      -- actually SAYS, so that "which fields did this request change" is answered
      -- from committed bytes rather than from the request's own account of them.
      v_stored := ops.f01_digest_jsonb(v_stored_state);
      v_stored_states := v_stored_states || jsonb_build_object(v_key, v_stored_state);
    end if;
    if v_stored is distinct from v_expected then
      raise exception 'j102_stale_subject_digest: the current state of % is %, and the caller decided against %',
        v_key, coalesce(v_stored, 'absent'), coalesce(v_expected, 'absent')
        using errcode = '40001';
    end if;
  end loop;

  -- ==========================================================================
  -- ROOT BLOCKER 2, SECOND HALF -- THE PRIOR ROW MUST EXIST, THE COUPLED CHAIN
  -- MUST HOLD, THE PREREQUISITES MUST BE TRUE OF THE COMMITTED ROW, AND EVERY
  -- FIELD MUST LAND ON ITS EXACT CANONICAL TARGET.
  --
  -- Everything above proves the request is internally consistent, that the rows
  -- it names have not moved, that the whole coupled set is present, that the
  -- event set is exactly the transition's own, and that the evidence still binds
  -- to the subject being advanced. None of it asks whether the RESULT is the
  -- result this transition produces, which is what turns an allowed operation
  -- into an arbitrary row rewrite.
  -- ==========================================================================

  -- ROOT BLOCKER 1, ENFORCED AGAINST THE COMMITTED ROW. The creation gate in the
  -- envelope loop reads the caller's own operand; this reads the DATABASE. A
  -- subject the map says is updated must have a committed row -- and because its
  -- operand was a digest rather than a null, the compare-and-swap above has
  -- already refused if that row is absent. Both are kept: one names the attempt,
  -- the other is the fact.
  for v_kind in select * from jsonb_object_keys(v_proposed_ids) loop
    v_id := v_proposed_ids ->> v_kind;
    if (v_subjects -> v_kind ->> 'mode') = 'update'
       and not (v_stored_states ? (v_kind || ':' || v_id)) then
      if v_kind = v_primary_kind then
        raise exception 'j102_primary_subject_not_found: % advances an existing % and % holds no committed row; nothing in this rail creates one and this writer will not invent it',
          p_transition_id, v_primary_kind, v_kind || ':' || v_id using errcode = '22023';
      end if;
      raise exception 'j102_coupled_subject_not_found: % also writes the % it is coupled to, and % holds no committed row',
        p_transition_id, v_kind, v_kind || ':' || v_id using errcode = '22023';
    end if;
    v_prior_by_kind := v_prior_by_kind ||
      jsonb_build_object(v_kind, coalesce(v_stored_states -> (v_kind || ':' || v_id), 'null'::jsonb));
  end loop;

  -- THE COUPLED SUBJECT IDENTITY CHAIN. A transition that writes more than one
  -- subject writes subjects that are RELATED, and the relation is a field on one
  -- of the two rows: an engagement names its relationship, a negotiation and a
  -- deal name their assignment, an assignment names its pending deal. A coupled
  -- subject that is in no such chain with the primary is an unrelated row
  -- travelling beside a legitimate request.
  for v_kind in select * from jsonb_object_keys(v_proposed_ids) loop
    continue when v_kind = v_primary_kind;
    v_id := v_proposed_ids ->> v_kind;
    v_child := v_proposed_states -> v_kind;
    v_chained := false;
    v_parent_field := v_policy -> 'parent_reference_fields' ->> v_primary_kind;
    if v_parent_field is not null and (v_child ->> v_parent_field) = v_primary_id then
      v_chained := true;
    end if;
    v_parent_field := v_policy -> 'parent_reference_fields' ->> v_kind;
    if v_parent_field is not null
       and ((v_proposed_states -> v_primary_kind) ->> v_parent_field) = v_id then
      v_chained := true;
    end if;
    if not v_chained then
      raise exception 'j102_coupled_subject_not_in_chain: % % is proposed beside % %, and neither names the other',
        v_kind, v_id, v_primary_kind, v_primary_id using errcode = '22023';
    end if;
  end loop;

  -- THE SUBJECTS THE TRANSITION REQUIRES TO BE TRUE WITHOUT WRITING THEM.
  -- `open-assignment` opens an assignment under an ACTIVE engagement held by a
  -- relationship that is already a CLIENT (Q077), and the kernel refuses without
  -- both. They are read here from the compare-and-swap operand set -- which means
  -- they were locked and their digests were checked like any other subject -- so a
  -- direct caller cannot open an assignment under a lapsed engagement or a
  -- prospect by simply not mentioning them.
  for v_context_rule in
    select * from jsonb_array_elements(coalesce(v_contract -> 'required_context', '[]'::jsonb))
  loop
    v_kind := v_context_rule ->> 'subject';
    v_context_id := (case v_context_rule -> 'identified_by' ->> 'source'
                       when 'prior' then v_prior_by_kind
                       when 'proposed' then v_proposed_states
                       when 'context' then v_context_by_kind else null end
                     -> (v_context_rule -> 'identified_by' ->> 'subject'))
                    ->> (v_context_rule -> 'identified_by' ->> 'field');
    if v_context_id is null then
      raise exception 'j102_required_context_unidentified: % requires the % this transition runs under, and nothing names it',
        p_transition_id, v_kind using errcode = '22023';
    end if;
    v_prior := v_stored_states -> (v_kind || ':' || v_context_id);
    if v_prior is null then
      raise exception 'j102_required_context_not_locked: % requires % % to be loaded and unmoved, and it is in neither the compare-and-swap operands nor the database',
        p_transition_id, v_kind, v_context_id using errcode = '22023';
    end if;
    for v_condition in select * from jsonb_array_elements(v_context_rule -> 'conditions') loop
      if (v_prior -> (v_condition ->> 'field')) is distinct from (v_condition -> 'equals') then
        raise exception 'j102_required_context_not_met: % requires % % to have % of %, and it is %',
          p_transition_id, v_kind, v_context_id, v_condition ->> 'field',
          v_condition -> 'equals', coalesce(v_prior -> (v_condition ->> 'field'), 'null'::jsonb)
          using errcode = '22023';
      end if;
    end loop;
    v_context_by_kind := v_context_by_kind || jsonb_build_object(v_kind, v_prior);
  end loop;

  -- ==========================================================================
  -- PREREQUISITES, PRIOR CONDITIONS AND EXACT TARGETS, decided against the
  -- STORED row, the other subjects in this call and the evidence read under the
  -- lock.
  --
  -- THE PREVIOUS CHECK ASKED THE WRONG QUESTION. "Is this a field the transition
  -- may move" admits `assignment_phase: "committed"` from `open-assignment`,
  -- `payment_state: "paid"` from a partial payment, a `pending_deal_id` pointing
  -- at a deal that was never created, an `open_negotiation_count` of 40, and a
  -- deleted key. Every one of those is a permitted field carrying a value the
  -- kernel would never produce. What is checked now is the RESULT: each moved
  -- field must equal the exact value the kernel's evaluator computes, and each
  -- field that is not moved must be byte-identical to the committed row.
  -- ==========================================================================
  for v_kind in select * from jsonb_object_keys(v_proposed_ids) loop
    v_id := v_proposed_ids ->> v_kind;
    v_subject_rule := v_subjects -> v_kind;
    v_prior := v_stored_states -> (v_kind || ':' || v_id);
    v_state := v_proposed_states -> v_kind;
    -- Named rather than left to a cast error deeper in. A subject envelope whose
    -- `state` is not an object is a request nothing can compare.
    if jsonb_typeof(v_state) is distinct from 'object' then
      raise exception 'j102_proposed_state_not_an_object: the proposed % % carries no state object',
        v_kind, v_id using errcode = '22023';
    end if;

    if (v_subject_rule ->> 'mode') = 'create' then
      -- A CREATED SUBJECT HAS AN EXACT SHAPE. The kernel writes a new engagement
      -- and a new pending deal with a fixed key set and fixed values -- a deal is
      -- born pending, unexecuted, uninvoiced, unpaid and open, under THIS
      -- assignment and on THIS property. A creation carrying an extra key, a
      -- missing key, another assignment's id or a deal that is born closed is not
      -- the thing this transition creates.
      for v_field in
        select k from (
          select jsonb_object_keys(v_subject_rule -> 'creation_shape') as k
          union
          select jsonb_object_keys(v_state)
        ) f order by k collate "C"
      loop
        v_effect := v_subject_rule -> 'creation_shape' -> v_field;
        if v_effect is null then
          raise exception 'j102_created_subject_shape_mismatch: the % % created by % carries no %, and this request supplies one',
            v_kind, v_id, p_transition_id, v_field using errcode = '22023';
        end if;
        if not (v_state ? v_field) then
          raise exception 'j102_created_subject_shape_mismatch: the % % created by % must carry %, and this request omits it',
            v_kind, v_id, p_transition_id, v_field using errcode = '22023';
        end if;
        v_expected_value := ops.j102_expected_value(v_effect, v_prior_by_kind,
          v_proposed_states, v_context_by_kind, v_proposed_ids, v_facts_by_kind);
        v_actual_value := v_state -> v_field;
        -- FAIL CLOSED ON A KIND THIS WRITER DOES NOT COMPARE. The interpreter is
        -- shared with the initialization writer, which understands one more
        -- answer shape; without this, a future map entry using that shape inside a
        -- TRANSITION would fall past both branches below and be accepted
        -- unchecked. A transition target is always derivable, so there is nothing
        -- here to admit.
        if (v_expected_value ->> 'kind') not in ('exact', 'any_of', 'unbound') then
          raise exception 'j102_expected_value_kind_unsupported: % computes % for %.%, which this writer does not compare',
            p_transition_id, v_expected_value ->> 'kind', v_kind, v_field
            using errcode = '22023';
        end if;
        if (v_expected_value ->> 'kind') = 'exact'
           and v_actual_value is distinct from (v_expected_value -> 'value') then
          raise exception 'j102_created_subject_field_not_canonical: % creates % % with % of %, and this request supplies %',
            p_transition_id, v_kind, v_id, v_field,
            coalesce(v_expected_value -> 'value', 'null'::jsonb),
            coalesce(v_actual_value, 'null'::jsonb) using errcode = '42501';
        elsif (v_expected_value ->> 'kind') = 'any_of'
              and not (v_expected_value -> 'values' @> jsonb_build_array(v_actual_value)) then
          raise exception 'j102_created_subject_field_not_canonical: % creates % % with % from %, and this request supplies %',
            p_transition_id, v_kind, v_id, v_field, v_expected_value -> 'values',
            coalesce(v_actual_value, 'null'::jsonb) using errcode = '42501';
        end if;
      end loop;
      continue;
    end if;

    if v_kind = v_primary_kind then
      -- The transition's declared `from` axes, checked against the row as it is
      -- committed rather than against the state the request would like it to have
      -- had. This is what stops `record-deal-closing` from closing a deal that was
      -- never executed, or `open-assignment` from rewinding a committed one,
      -- through a direct call that never reached the kernel.
      for v_axis, v_permitted in select key, value from jsonb_each(v_contract -> 'prerequisites') loop
        if not (v_permitted ? coalesce(v_prior ->> v_axis, '')) then
          raise exception 'j102_prerequisite_not_met: % requires % % to be one of %, and % % is %',
            p_transition_id, v_primary_kind, v_axis, v_permitted, v_kind, v_id,
            coalesce(v_prior ->> v_axis, 'absent') using errcode = '22023';
        end if;
      end loop;
      -- Q094's split, enforced where the row is. record-lease-execution and
      -- record-purchase-contract-execution have different consequences, so the
      -- instrument kind comes off the STORED deal exactly as the store's
      -- dispatcher takes it, and a direct caller cannot obtain the lease
      -- semantics -- which open no diligence -- on a purchase.
      if jsonb_typeof(v_contract -> 'instrument_kinds') = 'array'
         and not (v_contract -> 'instrument_kinds' ? coalesce(v_prior ->> 'instrument_kind', '')) then
        raise exception 'j102_instrument_kind_not_permitted: % applies to %, and % % is a %',
          p_transition_id, v_contract -> 'instrument_kinds', v_kind, v_id,
          coalesce(v_prior ->> 'instrument_kind', 'deal of no instrument kind')
          using errcode = '22023';
      end if;
    end if;

    -- THE EVALUATOR'S OWN PER-SUBJECT REFUSALS, against the committed row: an
    -- assignment that still holds a pending deal or a committed target cannot be
    -- reopened, a negotiation that is not the accepted one cannot be selected as
    -- the winner, a closing cannot land while diligence is unresolved, and a
    -- coupled subject must be the one the primary actually names.
    for v_condition in
      select * from jsonb_array_elements(coalesce(v_subject_rule -> 'prior_conditions', '[]'::jsonb))
    loop
      v_field := v_condition ->> 'field';
      v_actual_value := v_prior -> v_field;
      if coalesce((v_condition ->> 'must_be_null')::boolean, false)
         and v_actual_value is distinct from 'null'::jsonb then
        raise exception 'j102_prior_condition_not_met: % requires % % to hold no %, and it holds %',
          p_transition_id, v_kind, v_id, v_field, coalesce(v_actual_value, '"absent"'::jsonb)
          using errcode = '22023';
      end if;
      if v_condition ? 'equals' and v_actual_value is distinct from (v_condition -> 'equals') then
        raise exception 'j102_prior_condition_not_met: % requires the % % it moves to have % of %, and it is %',
          p_transition_id, v_kind, v_id, v_field, v_condition -> 'equals',
          coalesce(v_actual_value, '"absent"'::jsonb) using errcode = '22023';
      end if;
      if v_condition ? 'in' and not (v_condition -> 'in' @> jsonb_build_array(v_actual_value)) then
        raise exception 'j102_prior_condition_not_met: % requires % % to have % in %, and it is %',
          p_transition_id, v_kind, v_id, v_field, v_condition -> 'in',
          coalesce(v_actual_value, '"absent"'::jsonb) using errcode = '22023';
      end if;
      if v_condition ? 'not_in' and (v_condition -> 'not_in' @> jsonb_build_array(v_actual_value)) then
        raise exception 'j102_prior_condition_not_met: % refuses % % while its % is %',
          p_transition_id, v_kind, v_id, v_field, v_actual_value using errcode = '22023';
      end if;
      -- The identity half of a coupled write: the negotiation this assignment
      -- commits must be one of ITS negotiations, and the assignment a cancelled
      -- deal returns must be the deal's own assignment.
      if v_condition ? 'equals_subject_id'
         and v_actual_value is distinct from
             to_jsonb(v_proposed_ids ->> (v_condition ->> 'equals_subject_id')) then
        raise exception 'j102_prior_condition_not_met: the % % names % as its %, and this call advances % %',
          v_kind, v_id, coalesce(v_actual_value #>> '{}', 'nothing'), v_field,
          v_condition ->> 'equals_subject_id',
          v_proposed_ids ->> (v_condition ->> 'equals_subject_id') using errcode = '22023';
      end if;
      -- Q095's single target: a selected property or lease-draft target that is
      -- already set must already BE the candidate, or the commitment is a second
      -- target and only an approved exception moves it -- and no producer writes
      -- one, so it refuses.
      if v_condition ? 'null_or_matches' then
        v_expected_value := ops.j102_expected_value(v_condition -> 'null_or_matches',
          v_prior_by_kind, v_proposed_states, v_context_by_kind, v_proposed_ids, v_facts_by_kind);
        if v_actual_value is distinct from 'null'::jsonb
           and v_actual_value is distinct from (v_expected_value -> 'value') then
          raise exception 'j102_prior_condition_not_met: % % already holds % of %, and this call names %; a second target needs an approved exception, which nothing produces',
            v_kind, v_id, v_field, v_actual_value,
            coalesce(v_expected_value -> 'value', 'null'::jsonb) using errcode = '22023';
        end if;
      end if;
    end loop;

    -- THE EXACT RESULT. Every key of the committed row and of the proposal is
    -- visited, so a DELETED key is caught exactly as a changed one: `->` answers
    -- SQL NULL for a key that is not there and `is distinct from` reads that as a
    -- difference.
    for v_field in
      select k from (
        select jsonb_object_keys(v_prior) as k
        union
        select jsonb_object_keys(v_state)
      ) f order by k collate "C"
    loop
      v_effect := (v_subject_rule -> 'effects') -> v_field;
      v_actual_value := v_state -> v_field;
      if v_effect is null then
        -- NOT A FIELD THIS TRANSITION MOVES. It must survive the write exactly as
        -- it was committed -- not merely "not be one of the coupled facts".
        if v_actual_value is distinct from (v_prior -> v_field) then
          raise exception 'j102_field_not_movable_by_transition: % may not change %.%; on a % it moves only %',
            p_transition_id, v_kind, v_field, v_kind,
            coalesce((select jsonb_agg(k order by k)
                        from jsonb_object_keys(v_subject_rule -> 'effects') as k), '[]'::jsonb)
            using errcode = '42501';
        end if;
        continue;
      end if;
      v_expected_value := ops.j102_expected_value(v_effect, v_prior_by_kind,
        v_proposed_states, v_context_by_kind, v_proposed_ids, v_facts_by_kind);
      if (v_expected_value ->> 'kind') not in ('exact', 'any_of', 'unbound') then
        raise exception 'j102_expected_value_kind_unsupported: % computes % for %.%, which this writer does not compare',
          p_transition_id, v_expected_value ->> 'kind', v_kind, v_field using errcode = '22023';
      end if;
      if (v_expected_value ->> 'kind') = 'unbound' then
        -- Declared, not hidden. There is one of these in the whole map and it sits
        -- on a branch no manifest can reach.
        continue;
      elsif (v_expected_value ->> 'kind') = 'exact' then
        if v_actual_value is distinct from (v_expected_value -> 'value') then
          raise exception 'j102_transition_effect_not_canonical: % moves %.% to %, and this request supplies %',
            p_transition_id, v_kind, v_field,
            coalesce(v_expected_value -> 'value', 'null'::jsonb),
            coalesce(v_actual_value, '"absent"'::jsonb) using errcode = '42501';
        end if;
      else
        if not (v_expected_value -> 'values' @> jsonb_build_array(v_actual_value)) then
          raise exception 'j102_transition_effect_not_canonical: % moves %.% to one of %, and this request supplies %',
            p_transition_id, v_kind, v_field, v_expected_value -> 'values',
            coalesce(v_actual_value, '"absent"'::jsonb) using errcode = '42501';
        end if;
      end if;
    end loop;
    -- A field the transition MOVES but the proposal does not carry at all: the
    -- loop above visits it only if the committed row has it. A subject whose
    -- shape gained a coupled fact since it was written would otherwise pass.
    for v_field in select * from jsonb_object_keys(v_subject_rule -> 'effects') loop
      if not (v_state ? v_field) then
        raise exception 'j102_transition_effect_missing: % moves %.%, and this request carries no such field',
          p_transition_id, v_kind, v_field using errcode = '22023';
      end if;
    end loop;
  end loop;

  -- ==========================================================================
  -- THE WHOLE EVENT PAYLOAD, AND THE EVIDENCE THE HISTORY CITES.
  --
  -- The set check above proves WHICH events are appended and on which subjects.
  -- It says nothing about what is INSIDE them, and the kernel puts the facts a
  -- reviewer actually reads history for inside: the closing date on
  -- `deal_closed`, the cancellation reason on `pending_deal_cancelled`, the axis
  -- value on every axis event, the property and the assignment a negotiation
  -- event names, and the evidence reference each one rested on. Every one of
  -- those could be a lie on a call whose event KIND and SUBJECT were both
  -- correct, and the state row beside it correct too -- so the history and the
  -- row it was written with would disagree about the same fact.
  --
  -- HIGH-6, the other half: `record.evidence_references`. The table comment
  -- promises "the exact evidence references it rested on"; nothing anywhere in
  -- this file compared them to anything. An event could cite a document the
  -- transition never rested on, another deal's settlement, a duplicate, an extra
  -- pin, or an empty array, and Q072's "the correction is receipted" and Q082's
  -- "transitions declare their evidence" became uncheckable from exactly the
  -- surface a reviewer reads.
  --
  -- BOTH ARE DECIDED HERE, AGAINST THE SAME FACTS THE STATE WAS. The canonical
  -- references come from ops.j102_recheck_evidence's own answer -- the document
  -- id F01 returned, the digest the stored-artifact reader returned, the record
  -- id on the committed row -- and never from the manifest's selector, from a
  -- caller-supplied array or from the diagnostics. The nested detail is computed
  -- by the SAME interpreter the state targets use, over the same committed prior
  -- rows, the same coupled subjects and the same re-read evidence. An event this
  -- transition would not have produced cannot be appended beside a state it would
  -- have.
  --
  -- THE WHOLE ARRAY IS REQUIRED ON EVERY EVENT, INCLUDING A COUPLED ONE THAT
  -- NAMES NO SINGLE PIN. Three of the fourteen transitions append an event with
  -- no `evidence_reference` detail field at all -- winning_property_selected,
  -- assignment_committed, assignment_returned_to_market -- and that absence is
  -- the kernel's own shape, transcribed rather than filled in. It is a different
  -- question from the array: "which pin does THIS event name" is the detail
  -- field, and "what did the transition that produced it rest on" is the array,
  -- which the store stamps identically on every event of one call and which is
  -- therefore required identically on every one of them.
  -- ==========================================================================
  select coalesce(jsonb_object_agg(item ->> 'evidence_kind', jsonb_build_object(
           'evidence_kind', item -> 'evidence_kind',
           'source', item -> 'source',
           'reference', item -> 'reference')), '{}'::jsonb)
    into v_canonical_refs
    from jsonb_array_elements(v_checked) as item;
  v_canonical_count := jsonb_array_length(v_checked);

  for v_envelope in select * from jsonb_array_elements(p_event_envelopes) loop
    v_record := v_envelope -> 'record';
    v_event := v_record -> 'event';
    v_kind := v_event ->> 'subject_kind';
    -- The spec for THIS event. The pairwise set check above already proved the
    -- supplied events are exactly the contract's, so exactly one spec matches;
    -- the refusal below is the structural belt for a future edit that widens the
    -- set check without widening this one.
    v_event_spec := null;
    select spec into v_event_spec
      from jsonb_array_elements(v_contract -> 'events') as spec
     where (spec ->> 'event_kind') = (v_event ->> 'event_kind')
       and (spec ->> 'subject') = v_kind
     limit 1;
    if v_event_spec is null then
      raise exception 'j102_event_not_produced_by_transition: % appends %, and this request carries a % event on a %',
        p_transition_id, v_contract -> 'events',
        coalesce(v_event ->> 'event_kind', 'nameless'), coalesce(v_kind, 'subject of no kind')
        using errcode = '42501';
    end if;

    -- THE EVENT'S CLOSED KEY SET. The kernel builds every event as
    -- lifecycleEvent(kind, subject_kind, subject_id, detail): four identity keys
    -- and the transition's own detail, and nothing else. A key outside that set
    -- is a fact the kernel never wrote, hashed into the history's own bytes.
    for v_field in
      select k from (
        select jsonb_object_keys(coalesce(v_event_spec -> 'detail', '{}'::jsonb)) as k
        union
        select jsonb_object_keys(v_event)
      ) f order by k collate "C"
    loop
      continue when v_identity_keys @> jsonb_build_array(v_field);
      v_effect := (v_event_spec -> 'detail') -> v_field;
      if v_effect is null then
        raise exception 'j102_event_detail_not_produced_by_transition: the % event % appends carries %, and this request supplies an extra %',
          v_event ->> 'event_kind', p_transition_id,
          coalesce((select jsonb_agg(k order by k)
                      from jsonb_object_keys(coalesce(v_event_spec -> 'detail', '{}'::jsonb)) as k),
                   '[]'::jsonb),
          v_field using errcode = '42501';
      end if;
      if not (v_event ? v_field) then
        raise exception 'j102_event_detail_missing: the % event % appends names %, and this request omits it',
          v_event ->> 'event_kind', p_transition_id, v_field using errcode = '22023';
      end if;
      v_expected_value := ops.j102_expected_value(v_effect, v_prior_by_kind,
        v_proposed_states, v_context_by_kind, v_proposed_ids, v_facts_by_kind);
      v_actual_value := v_event -> v_field;
      if (v_expected_value ->> 'kind') not in ('exact', 'any_of', 'unbound') then
        raise exception 'j102_expected_value_kind_unsupported: the % event computes % for %, which this writer does not compare',
          v_event ->> 'event_kind', v_expected_value ->> 'kind', v_field using errcode = '22023';
      end if;
      if (v_expected_value ->> 'kind') = 'unbound' then
        continue;
      elsif (v_expected_value ->> 'kind') = 'exact' then
        if v_actual_value is distinct from (v_expected_value -> 'value') then
          raise exception 'j102_event_detail_not_canonical: the % event names % of %, and this request supplies %',
            v_event ->> 'event_kind', v_field,
            coalesce(v_expected_value -> 'value', 'null'::jsonb),
            coalesce(v_actual_value, '"absent"'::jsonb) using errcode = '42501';
        end if;
      else
        if not (v_expected_value -> 'values' @> jsonb_build_array(v_actual_value)) then
          raise exception 'j102_event_detail_not_canonical: the % event names % from %, and this request supplies %',
            v_event ->> 'event_kind', v_field, v_expected_value -> 'values',
            coalesce(v_actual_value, '"absent"'::jsonb) using errcode = '42501';
        end if;
      end if;
    end loop;

    -- THE EVIDENCE REFERENCES: exactly the set that was re-read, one for one.
    v_actual_value := v_record -> 'evidence_references';
    if jsonb_typeof(v_actual_value) is distinct from 'array' then
      raise exception 'j102_event_evidence_references_missing: the % event cites % where it must cite the evidence this transition re-read',
        v_event ->> 'event_kind', coalesce(jsonb_typeof(v_actual_value), 'nothing')
        using errcode = '22023';
    end if;
    if jsonb_array_length(v_actual_value) <> v_canonical_count then
      raise exception 'j102_event_evidence_references_not_rechecked: % re-read % evidence records under this lock, and the % event cites %',
        p_transition_id, v_canonical_count, v_event ->> 'event_kind',
        jsonb_array_length(v_actual_value) using errcode = '42501';
    end if;
    v_seen := '{}'::jsonb;
    for v_item in select * from jsonb_array_elements(v_actual_value) loop
      v_key := v_item ->> 'evidence_kind';
      if v_key is null or not (v_canonical_refs ? v_key) then
        raise exception 'j102_event_evidence_reference_not_rechecked: the % event cites % evidence, and this transition re-read %',
          v_event ->> 'event_kind', coalesce(v_key, 'unnamed'),
          coalesce((select jsonb_agg(k order by k) from jsonb_object_keys(v_canonical_refs) as k),
                   '[]'::jsonb) using errcode = '42501';
      end if;
      if v_seen ? v_key then
        raise exception 'j102_event_evidence_reference_duplicated: the % event cites % twice; two citations of one kind is one record counted as two',
          v_event ->> 'event_kind', v_key using errcode = '42501';
      end if;
      -- Whole-object equality, which is also what closes the key set: an element
      -- carrying an extra key, a missing key, another document's reference or a
      -- source the contract does not name is not the element the recheck built.
      if v_item is distinct from (v_canonical_refs -> v_key) then
        raise exception 'j102_event_evidence_reference_not_rechecked: % was re-read as %, and the % event cites %',
          v_key, v_canonical_refs -> v_key, v_event ->> 'event_kind', v_item
          using errcode = '42501';
      end if;
      v_seen := v_seen || jsonb_build_object(v_key, true);
    end loop;
  end loop;

  -- Every proposed subject, written. A subject whose operand is null is a
  -- CREATION and the loop above has already proved, under this transaction's
  -- locks, that no row holds that key; a subject whose operand is a digest is an
  -- update of the exact version that digest names.
  --
  -- WHAT THE SHARED ADVISORY LOCK BUYS, STATED AT THE ISOLATION LEVEL IT DEPENDS
  -- ON. Both writers take the same tier-2 key, so two concurrent creations of one
  -- id serialize on it. AT READ COMMITTED the loser then re-reads and its null
  -- operand meets the winner's committed row, so it gets the NAMED refusal. At
  -- REPEATABLE READ or SERIALIZABLE the loser's snapshot predates that commit, the
  -- compare-and-swap passes, and the primary key on ops.j102_subject_current
  -- refuses the insert with 23505 instead. EITHER WAY NO DUPLICATE ROW EXISTS AND
  -- NEITHER WRITE OVERWRITES THE OTHER -- what changes is which refusal the loser
  -- sees, and this rail does not set the isolation level, so the message is a
  -- property of the deployment rather than a promise made here.
  for v_envelope in select * from jsonb_array_elements(p_subject_envelopes) loop
    v_record := v_envelope -> 'record';
    v_state := v_record -> 'state';
    v_kind := v_record ->> 'subject_kind';
    v_id := v_record ->> 'subject_id';
    -- The actor, the instant and the envelope's own digest were checked in the
    -- admission loop above, before anything was read or compared. Nothing between
    -- there and here can change them: both loops read the same immutable
    -- parameter.
    insert into ops.j102_subject_current as c
      (tenant, subject_kind, subject_id, envelope, envelope_digest, state_digest,
       parent_id, deal_state, updated_by, sponsoring_partner, updated_at)
    values (
      ops.f01_tenant(), v_kind, v_id, v_envelope, ops.f01_digest_jsonb(v_envelope),
      ops.f01_digest_jsonb(v_state),
      coalesce(v_state ->> 'relationship_id', v_state ->> 'engagement_id',
               v_state ->> 'assignment_id'),
      v_state ->> 'deal_state',
      v_actor, v_sponsor, v_txn_now)
    on conflict (tenant, subject_kind, subject_id) do update
      set envelope = excluded.envelope,
          envelope_digest = excluded.envelope_digest,
          state_digest = excluded.state_digest,
          parent_id = excluded.parent_id,
          deal_state = excluded.deal_state,
          updated_by = excluded.updated_by,
          sponsoring_partner = excluded.sponsoring_partner,
          updated_at = excluded.updated_at;
    v_subject_digests := v_subject_digests ||
      jsonb_build_object(v_kind || ':' || v_id, ops.f01_digest_jsonb(v_state));
    v_readback := v_readback ||
      jsonb_build_object(v_kind, ops.j102_subject(v_kind, v_id));
  end loop;

  -- Every event, appended. Same transaction as the state above, so history and
  -- current state can never disagree about whether something happened.
  for v_envelope in select * from jsonb_array_elements(p_event_envelopes) loop
    v_record := v_envelope -> 'record';
    -- HIGH-1, SECOND HALF, kept as a structural belt beside the exact event-set
    -- check above. The event's subject came straight off the event record and was
    -- checked against NOTHING -- not the proposed subjects, not the
    -- compare-and-swap operands, not the lock set, and there is no foreign key. A
    -- direct caller could append an extra event naming an unrelated deal in the
    -- same call: it landed in the history, j102_read returned it, and that deal's
    -- current state never moved. The set check refuses that as an extra event;
    -- this refuses it as an unadvanced subject, and the insert is reached only
    -- when both are satisfied.
    v_kind := v_record -> 'event' ->> 'subject_kind';
    v_id := v_record -> 'event' ->> 'subject_id';
    if v_kind is null or v_id is null or (v_proposed_ids ->> v_kind) is distinct from v_id then
      raise exception 'j102_event_subject_not_advanced: an event names % %, and this transition advances %',
        coalesce(v_kind, 'a subject of no kind'), coalesce(v_id, 'no id'), v_proposed_ids
        using errcode = '42501';
    end if;
    insert into ops.j102_subject_event
      (tenant, subject_kind, subject_id, event_kind, transition_id, envelope, envelope_digest,
       event_digest, recorded_by, sponsoring_partner, recorded_at, idempotency_key)
    values (
      ops.f01_tenant(),
      v_record -> 'event' ->> 'subject_kind',
      v_record -> 'event' ->> 'subject_id',
      v_record -> 'event' ->> 'event_kind',
      v_record ->> 'transition_id',
      v_envelope, ops.f01_digest_jsonb(v_envelope), ops.f01_digest_jsonb(v_record),
      v_actor, v_sponsor, v_txn_now, p_idempotency_key);
    v_event_digests := v_event_digests || jsonb_build_array(ops.f01_digest_jsonb(v_record));
  end loop;

  v_result := jsonb_build_object(
    'operation', v_operation,
    'decision', 'allow',
    'outcome', 'applied',
    'actor_slug', v_actor,
    'transition_id', p_transition_id,
    -- ======================================================================
    -- M-2. WHAT THE RECEIPT MAY STATE AS FACT, AND WHAT IT MAY ONLY REPORT.
    --
    -- These three used to be taken straight off p_diagnostics -- the caller's
    -- own account of the decision it says it took -- and reported beside
    -- properties this function had actually enforced, in one flat object with
    -- nothing distinguishing them. A direct caller could therefore write a
    -- durable receipt that misdescribed what it committed.
    --
    -- TWO OF THEM ARE NOW DERIVED and no longer read the diagnostics at all.
    -- `coupled_facts_committed` and `decision_refs` are properties of the
    -- TRANSITION, and the transition is p_transition_id, already validated
    -- against the admission map; the map's copies of both are asserted equal to
    -- the kernel's exported contract, contract by contract, by the Node parity
    -- suite. So the receipt states what this transition commits, and states it
    -- from the same place the writer decided everything else from.
    --
    -- THE THIRD IS NOT DERIVABLE HERE and is labelled instead of laundered.
    -- `reason_id` is the KERNEL's diagnostic for the branch its evaluator took --
    -- `diligence_satisfied`, `payment_paid`, `search_initiation_opens_assignment`
    -- -- and this file transcribes the kernel's contracts and its targets, not
    -- its reason vocabulary. Deriving it would mean inventing a second copy of a
    -- fourth thing; echoing it as `reason_id` beside two derived fields would
    -- make a caller-asserted string read as an authoritative one. It is carried
    -- under its own name, with its own scope, so history says who said it.
    -- ======================================================================
    'coupled_facts_committed', coalesce(v_contract -> 'coupled_facts', '[]'::jsonb),
    'coupled_facts_committed_source', 'derived_from_the_admission_contract',
    'decision_refs', coalesce(v_contract -> 'decision_refs', '[]'::jsonb),
    'decision_refs_source', 'derived_from_the_admission_contract',
    'caller_reported_reason_id', p_diagnostics ->> 'reason_id',
    'caller_reported_reason_id_scope',
      'kernel_result_diagnostic_asserted_by_the_caller_and_not_recomputed_here',
    -- Q103's audit trail, STORED with the outcome so a replay and history both
    -- show a machine merge. The caller's account of what moved; what this writer
    -- enforced is that every compare-and-swap operand was the CURRENT digest.
    'concurrent_merges', coalesce(p_diagnostics -> 'concurrent_merges', '[]'::jsonb),
    'concurrent_merges_scope', 'caller_reported_merge_account_cas_enforced_on_current_row',
    'subject_digests', v_subject_digests,
    'event_digests', v_event_digests,
    'evidence_rechecked_under_lock', true,
    -- Now a statement about what was actually enforced: every pin was re-read
    -- under the lock AND bound to the one primary subject this call advances.
    -- Before, this said `true` while nothing compared the binding to the subject.
    'evidence_bound_under_lock', true,
    'evidence_bound_to_primary_subject', true,
    'evidence_checked', v_checked,
    -- BLOCK-1's receipt half: which policy admitted this, which class the derived
    -- principal held, and which subject the evidence was bound to.
    'admission_policy_id', v_policy ->> 'policy_id',
    'actor_authorization_class', v_class,
    'primary_subject_kind', v_primary_kind,
    'primary_subject_id', v_primary_id,
    -- FACTS ABOUT THIS CALL, not hedges. The primary subject was LOADED -- a
    -- creation of it is refused by name, so there is no path on which these are
    -- anything but true -- and the prerequisites were therefore checked against a
    -- committed row. The two fields stay in the receipt precisely because their
    -- earlier values (`primary_subject_created: true`,
    -- `prerequisites_checked: false`) were an honest report of a bypass, and a
    -- reader comparing two receipts should see that the bypass is gone rather
    -- than that the report disappeared.
    'primary_subject_loaded', true,
    'primary_subject_created', false,
    'prerequisites_checked', true,
    -- Which subjects this call CREATED, which is only ever the coupled ones the
    -- kernel creates: an engagement, or a pending deal.
    'created_subject_kinds', v_created_kinds,
    -- The two properties the second correction adds, reported so a caller can
    -- record what was enforced rather than infer it: every moved field landed on
    -- the value the transition contract computes, and the appended history is
    -- exactly the event set that transition produces.
    'transition_effects_enforced', true,
    'required_event_set_enforced', true,
    'required_subject_set_enforced', true,
    -- HIGH-5 and HIGH-6, reported as what they are: every subject envelope named
    -- THIS transition as its own provenance, every event's whole payload equalled
    -- the one this transition produces from the same facts the state came from,
    -- and every event cites exactly the evidence that was re-read under the lock.
    'subject_provenance_bound_to_transition', true,
    'event_payloads_enforced', true,
    'event_evidence_references_enforced', true,
    -- THE COMMITTED RECEIPT. The instant every row in this transaction carries,
    -- taken from the database rather than echoed back from the request, and the
    -- operands the swap was actually decided against.
    'committed_at', v_txn_now_text,
    'committed_state_digests', v_subject_digests,
    'expected_state_digests', p_expected_state_digests,
    -- M-b. TWO DIGESTS, AND THEY ARE NOT THE SAME CLAIM.
    --
    -- `request_digest` is the CALLER'S OWN digest of its own intent. This function
    -- never receives the payload it was computed over, so it cannot recompute it
    -- and does not pretend to: it is shape-checked, bound to the idempotency key,
    -- and that is the whole of it. Naming its scope is the honest close; claiming
    -- the database verified the caller's payload would be the dishonest one, and
    -- verifying it would need an authority -- the raw request bytes -- that this
    -- slice has no reason to hold.
    --
    -- `committed_content_digest` is OURS, and it is recomputed from what actually
    -- landed: the recomputed state digest of every subject written and the
    -- recomputed digest of every event appended, under this transaction's instant
    -- and transition. A replay returns the stored receipt unchanged, so
    -- idempotency is untouched by either.
    'request_digest', p_request_digest,
    'request_digest_scope', 'caller_supplied_intent_digest_not_recomputed_here',
    'committed_content_digest', ops.f01_digest_jsonb(jsonb_build_object(
      'transition_id', p_transition_id,
      'committed_at', v_txn_now_text,
      'subject_digests', v_subject_digests,
      'event_digests', v_event_digests)),
    'committed_content_digest_source', 'recomputed_from_committed_rows',
    'readback', v_readback,
    'external_effects', false);
  return ops.j102_settle_idempotency(v_operation, p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb) is
  'The ONLY writer of lifecycle state. Admits the transition against the closed SQL admission map first -- the transition must exist, the operation must be one that performs it, and the DERIVED principal class must be permitted -- then claims its idempotency key before reading any state and locks every subject it reads or writes in ascending order. It REFUSES TO CREATE THE PRIMARY SUBJECT: a transition advances a row that is already there, and creation is admitted only for the two coupled subjects the kernel itself creates (an engagement, a pending deal) in their exact shape. It requires a compare-and-swap operand for every proposed subject, the WHOLE coupled subject set rather than any subset, and EXACTLY the event set that transition appends -- so a call can neither erase history with an empty array nor fabricate an extra or wrong-kind event. It re-reads the exact evidence pins under those locks, binds every one of them to the single primary subject, and then checks the RESULT: the prerequisites and instrument kind against the committed row, the evaluator''s own prior conditions, and every field either equal to the exact value the transition computes -- from the stored prior state, the coupled subjects and the evidence facts read under the lock -- or byte-identical to what was committed. THE HISTORY IS CHECKED THE SAME WAY: every subject envelope must name THIS transition as its own established_by_transition, every event''s whole payload -- not only its kind and subject -- must equal what the transition produces from those same facts, and every event must cite EXACTLY the evidence references the recheck actually re-read, derived from the readers'' own answers rather than from the manifest or from any supplied array. Actor, instant, schema versions and tenant are derived or fixed. Then it writes every proposed subject and every event in one transaction or none.';

-- ---------------------------------------------------------------------------
-- THE INITIALIZATION WRITER -- the first row of a chain, and nothing else.
--
-- IT IS A DIFFERENT FUNCTION FROM ops.j102_apply_transition ON PURPOSE, and the
-- separation is the whole safety argument rather than a tidiness preference. The
-- transition writer refuses to create its own primary subject because a created
-- primary makes its `from` axes, its instrument kind and its prior conditions
-- vacuous. Folding creation back in behind a flag would put that bypass one
-- boolean away from returning. So creation lives here, where there is NO
-- transition to have prerequisites: this function performs none, reads none of
-- the transition contracts, and cannot advance a committed row -- it writes one
-- row that did not exist, in a shape the admission map fixes field by field, and
-- refuses if that row is already there.
--
-- WHAT IT CHECKS INSTEAD OF EVIDENCE, since it takes none:
--
--   1. IS IT AN INITIALIZATION AT ALL, may this operation perform it, and may
--      this actor's DERIVED class -- the same three admission questions the
--      transition writer asks, against the same map, before a key is claimed.
--   2. THE PARENT CHAIN, under this transaction's own locks. Every subject the
--      contract requires must be in the compare-and-swap operand map, must still
--      hash to the digest the caller decided against, and must satisfy its
--      declared conditions on the COMMITTED row: an assignment only under an
--      ACTIVE engagement held by a relationship that is already a CLIENT (Q077),
--      a negotiation only under an assignment that is still open (Q095). A chain
--      link the caller simply omits is refused rather than skipped.
--   3. THE CREATED SHAPE, field for field, through the same interpreter the
--      transition targets use. A caller cannot create a relationship that is born
--      a `client`, an assignment born `committed`, a negotiation born
--      `loi_accepted`, or a row carrying an extra key or missing one.
--   4. THAT IT IS A CREATION. The operand for the created key must be an EXPLICIT
--      JSON null -- "this subject must be ABSENT" -- and the compare-and-swap
--      refuses if any row holds that key. Two concurrent initializations of one id
--      take the same advisory lock and serialize on it; AT READ COMMITTED the
--      loser then meets the winner's committed row and gets the named
--      `j102_subject_already_exists`, and at a stronger isolation level its
--      snapshot predates that commit and the PRIMARY KEY refuses the insert with
--      23505 instead. NO DUPLICATE ROW AND NO OVERWRITE EITHER WAY -- only the
--      refusal a caller sees differs, and this rail sets no isolation level.
--   4b. AND THAT ONLY THE SUBJECTS IT ACTUALLY CONSULTS ARE LOCKED. Every operand
--      key must be the created subject or one of the parents this contract
--      RESOLVED and read -- by identity, not merely by kind -- so a caller cannot
--      attach an unrelated row to the call, have it locked and compare-and-swapped
--      and never consulted, and then read `required_context_enforced: true` beside
--      it on the receipt.
--   5. THE HISTORY, in the same transaction. Exactly one event, of the kind and
--      on the subject the contract names, with the exact nested detail computed
--      from the row that was created -- and an EMPTY evidence citation, which is
--      a positive statement that this act rested on none.
--
-- WHAT IT CANNOT DO, structurally: create an engagement or a deal (neither kind
-- appears in the initialization map, because both are COUPLED creations of a
-- transition and creating one here would be a client status or a Deal with no
-- commitment behind it), write a second subject (it takes one envelope), append a
-- second event, touch a committed row, or reach ops.j102_apply_transition.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_initialize_subject(
  p_initialization_id text,
  p_expected_state_digests jsonb,
  p_subject_envelope jsonb,
  p_event_envelope jsonb,
  p_idempotency_key text,
  p_request_digest text,
  p_diagnostics jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_sponsor text := ops.j102_sponsoring_partner();
  v_class text := ops.f01_principal() ->> 'authorization_class';
  v_policy jsonb := ops.j102_admission_policy();
  v_contract jsonb;
  v_operation text := p_diagnostics ->> 'operation';
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb;
  v_record jsonb;
  v_state jsonb;
  v_event_record jsonb;
  v_event jsonb;
  v_event_spec jsonb;
  v_kind text;
  v_id text;
  v_key text;
  v_created_key text;
  v_context_rule jsonb;
  v_context_kind text;
  v_context_id text;
  v_condition jsonb;
  v_prior jsonb;
  v_context_by_kind jsonb := '{}'::jsonb;
  -- The `kind:id` of every parent a required-context rule actually RESOLVED and
  -- read. Empty for a parentless creation, which is what makes the receipt able
  -- to say "no parent" rather than reporting an unconditional true.
  v_consulted_keys jsonb := '[]'::jsonb;
  v_stored_states jsonb := '{}'::jsonb;
  v_proposed_states jsonb;
  v_ids jsonb;
  v_declared jsonb := '{}'::jsonb;
  v_field text;
  v_effect jsonb;
  v_expected_value jsonb;
  v_actual_value jsonb;
  v_stored text;
  v_expected text;
  v_stored_state jsonb;
  v_permitted_kinds text[];
  v_subject_schema text;
  v_event_record_schema text;
  v_event_schema text;
  v_subject_keys jsonb;
  v_event_keys jsonb;
  v_identity_keys jsonb;
  v_state_digest text;
  v_event_digest text;
  v_result jsonb;
begin
  if v_operation is null then
    raise exception 'j102_operation_required' using errcode = '22023';
  end if;
  -- Asked before anything indexes into it, on the same terms as the transition
  -- writer: a SQL NULL would propagate through every `?` and `->` below as NULL,
  -- which `if` reads as false, and a JSON scalar would reach jsonb_object_keys
  -- and raise something that is not a J102 refusal at all.
  if jsonb_typeof(p_expected_state_digests) is distinct from 'object' then
    raise exception 'j102_expected_state_digests_not_an_object: the created subject carries an explicit null operand and every parent carries its digest; this call supplies %',
      coalesce(jsonb_typeof(p_expected_state_digests), 'no operand map at all')
      using errcode = '22023';
  end if;

  -- === ADMISSION, before a key is claimed and before any state is read ========
  -- A TRANSITION ID PRESENTED TO THE INITIALIZATION WRITER is a caller trying the
  -- other door's vocabulary on this one, and it is named FIRST so the refusal says
  -- what was attempted rather than reporting an unknown initialization. There is
  -- nothing here that could perform it: this function reads only the
  -- `initializations` half of the map and never dispatches into `transitions`.
  if (v_policy -> 'transitions') ? p_initialization_id then
    raise exception 'j102_transition_is_not_an_initialization: % is a transition and is performed by ops.j102_apply_transition, which creates no primary subject',
      p_initialization_id using errcode = '22023';
  end if;
  v_contract := v_policy -> 'initializations' -> p_initialization_id;
  if v_contract is null then
    raise exception 'j102_unknown_initialization: % is not an initialization this rail performs',
      coalesce(p_initialization_id, 'unnamed') using errcode = '22023';
  end if;
  if not (v_contract -> 'operations' ? v_operation) then
    raise exception 'j102_operation_initialization_mismatch: % is not performed by the % operation; it is performed by %',
      p_initialization_id, v_operation, v_contract -> 'operations' using errcode = '22023';
  end if;
  if not (v_contract -> 'permitted_actor_classes' ? v_class) then
    raise exception 'j102_actor_class_not_permitted: % is performed by %, and % holds %',
      p_initialization_id, v_contract -> 'permitted_actor_classes', v_actor, v_class
      using errcode = '42501';
  end if;
  v_subject_schema := v_policy ->> 'stored_subject_schema_version';
  v_event_record_schema := v_policy ->> 'stored_event_schema_version';
  v_event_schema := v_policy ->> 'event_schema_version';
  v_subject_keys := v_policy -> 'stored_subject_record_keys';
  v_event_keys := v_policy -> 'stored_event_record_keys';
  v_identity_keys := v_policy -> 'event_identity_keys';
  v_event_spec := v_contract -> 'event';

  v_replay := ops.j102_claim_idempotency(v_operation, p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;

  -- === THE PROPOSED SUBJECT, as a property of the request alone ==============
  v_record := p_subject_envelope -> 'record';
  if jsonb_typeof(v_record) is distinct from 'object' then
    raise exception 'j102_subject_envelope_not_an_object: the proposed subject envelope carries % where its record should be',
      coalesce(jsonb_typeof(v_record), 'nothing') using errcode = '22023';
  end if;
  v_kind := v_record ->> 'subject_kind';
  v_id := v_record ->> 'subject_id';
  if v_kind is distinct from (v_contract ->> 'subject_kind') then
    raise exception 'j102_initialized_subject_kind_mismatch: % creates a %, and this request proposes a %',
      p_initialization_id, v_contract ->> 'subject_kind',
      coalesce(v_kind, 'subject of no kind') using errcode = '22023';
  end if;
  if v_id is null then
    raise exception 'j102_initialized_subject_id_missing: a created subject names itself'
      using errcode = '22023';
  end if;
  v_created_key := v_kind || ':' || v_id;
  v_state := v_record -> 'state';
  if jsonb_typeof(v_state) is distinct from 'object' then
    raise exception 'j102_proposed_state_not_an_object: the proposed % % carries no state object',
      v_kind, v_id using errcode = '22023';
  end if;
  if (v_record ->> 'updated_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: updated_by is derived, never supplied'
      using errcode = '42501';
  end if;
  if (v_record ->> 'sponsoring_partner') is distinct from v_sponsor then
    raise exception 'j102_sponsor_mismatch: the subject is recorded under %, derived from the authenticated login, and the request names %',
      v_sponsor, coalesce(v_record ->> 'sponsoring_partner', 'none') using errcode = '42501';
  end if;
  if (v_record ->> 'updated_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: updated_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'updated_at', 'null') using errcode = '42501';
  end if;
  if ops.f01_digest_jsonb(v_record) is distinct from (p_subject_envelope ->> 'record_digest') then
    raise exception 'j102_subject_digest_mismatch: the supplied subject does not hash to its claim'
      using errcode = '22000';
  end if;
  -- The row's own provenance is the INITIALIZATION that created it, and it may not
  -- claim a transition. A created row reporting `established_by_transition:
  -- record-deal-closing` would be a row that says it was advanced by something
  -- that never ran, on exactly the surface ops.j102_subject hands a reviewer.
  if (v_record ->> 'established_by_transition') is distinct from p_initialization_id then
    raise exception 'j102_subject_provenance_mismatch: this call applies %, and the % % claims to be established by %',
      p_initialization_id, v_kind, v_id,
      coalesce(v_record ->> 'established_by_transition', 'nothing') using errcode = '42501';
  end if;
  if (v_record ->> 'schema_version') is distinct from v_subject_schema then
    raise exception 'j102_subject_schema_version_mismatch: a stored lifecycle subject is %, and this request carries %',
      v_subject_schema, coalesce(v_record ->> 'schema_version', 'nothing') using errcode = '22023';
  end if;
  if (v_record ->> 'tenant') is distinct from ops.f01_tenant()
     or (p_subject_envelope ->> 'tenant') is distinct from ops.f01_tenant() then
    raise exception 'j102_subject_tenant_mismatch: this database is tenant %, and a proposed % names %',
      ops.f01_tenant(), coalesce(v_kind, 'subject'),
      coalesce(v_record ->> 'tenant', p_subject_envelope ->> 'tenant', 'nothing')
      using errcode = '42501';
  end if;
  if (p_subject_envelope ->> 'record_kind') is distinct from 'stored_lifecycle_subject' then
    raise exception 'j102_subject_record_kind_mismatch: this writer stores lifecycle subjects, and this envelope is a %',
      coalesce(p_subject_envelope ->> 'record_kind', 'record of no kind') using errcode = '22023';
  end if;
  if exists (select 1 from jsonb_object_keys(v_record) as k
              where not (v_subject_keys @> jsonb_build_array(k)))
     or exists (select 1 from jsonb_array_elements_text(v_subject_keys) as k
                 where not (v_record ? k)) then
    raise exception 'j102_subject_record_shape_unrecognised: a stored lifecycle subject carries exactly %, and this request carries %',
      v_subject_keys,
      coalesce((select jsonb_agg(k order by k) from jsonb_object_keys(v_record) as k), '[]'::jsonb)
      using errcode = '22023';
  end if;
  if (v_record ->> 'subject_kind') is distinct from (v_state ->> 'subject_kind')
     or (v_record ->> 'subject_id') is distinct from (v_state ->> 'subject_id') then
    raise exception 'j102_subject_header_state_mismatch: the envelope names % %, and the state it carries describes % %',
      v_kind, v_id, coalesce(v_state ->> 'subject_kind', 'nothing'),
      coalesce(v_state ->> 'subject_id', 'nothing') using errcode = '22023';
  end if;
  -- A CREATION HAS NO PRIOR STATE, and both places that say so must agree: the
  -- envelope's own prior_state_digest and the operand are the same claim written
  -- twice, and a pair that disagrees is a request whose history would describe a
  -- version its own check did not enforce.
  if not (p_expected_state_digests ? v_created_key) then
    raise exception 'j102_expected_state_digest_missing: % is created with no compare-and-swap operand; a creation must supply an explicit null',
      v_created_key using errcode = '22023';
  end if;
  if (p_expected_state_digests -> v_created_key) is distinct from 'null'::jsonb then
    raise exception 'j102_initialization_is_not_an_update: % creates % and its operand is %; a creation carries an explicit null, and a subject that already exists is advanced by a transition, not initialized',
      p_initialization_id, v_created_key,
      coalesce(p_expected_state_digests ->> v_created_key, 'a non-null digest')
      using errcode = '42501';
  end if;
  if jsonb_typeof(v_record -> 'prior_state_digest') is distinct from 'null' then
    raise exception 'j102_prior_state_digest_mismatch: % declares prior state % and a creation has none',
      v_created_key, coalesce(v_record ->> 'prior_state_digest', 'null') using errcode = '22023';
  end if;

  -- A FIRST, CHEAP PASS BY KIND, so a wholly unrelated kind is refused before any
  -- lock is taken. It is NOT the whole check: `assignment:somebody-elses-id` is
  -- the right kind and the wrong row, and it would pass here. The exact check is
  -- by IDENTITY and cannot run yet, because which parent id this call actually
  -- reads is not known until the created state has been read and each context
  -- rule resolved. It runs below, once they have been.
  v_permitted_kinds := array[v_kind];
  for v_context_rule in
    select * from jsonb_array_elements(coalesce(v_contract -> 'required_context', '[]'::jsonb))
  loop
    v_permitted_kinds := v_permitted_kinds || (v_context_rule ->> 'subject');
  end loop;
  for v_key in select * from jsonb_object_keys(p_expected_state_digests) loop
    if split_part(v_key, ':', 1) <> all(v_permitted_kinds) then
      raise exception 'j102_operand_subject_not_read_by_initialization: % creates a % under %, and this request also names %',
        p_initialization_id, v_kind, to_jsonb(v_permitted_kinds), v_key using errcode = '22023';
    end if;
  end loop;

  -- TIER 2, IN ASCENDING ORDER over the union of the created key and the parents,
  -- exactly as the transition writer takes them, so the two writers cannot
  -- deadlock against each other on the same pair of subjects.
  for v_key in
    select k from (
      select jsonb_object_keys(p_expected_state_digests) as k
      union
      select v_created_key
    ) s order by k collate "C"
  loop
    perform pg_advisory_xact_lock(hashtextextended(
      'j102:subject:' || ops.f01_tenant() || ':' || v_key, 0));
  end loop;

  -- === THE COMPARE-AND-SWAP, decided against the STORED rows under the lock ===
  -- Presence AND ABSENCE are both enforced: the created key's null operand meets
  -- any committed row and refuses, which is the creation collision, and a parent
  -- that moved between the decision and the write refuses exactly as a
  -- transition's would.
  for v_key in
    select k from (
      select jsonb_object_keys(p_expected_state_digests) as k
      union
      select v_created_key
    ) s order by k collate "C"
  loop
    v_context_kind := split_part(v_key, ':', 1);
    v_context_id := substr(v_key, length(v_context_kind) + 2);
    v_stored := null;
    v_stored_state := null;
    select c.envelope -> 'record' -> 'state' into v_stored_state
      from ops.j102_subject_current c
     where c.tenant = ops.f01_tenant() and c.subject_kind = v_context_kind
       and c.subject_id = v_context_id;
    if found then
      v_stored := ops.f01_digest_jsonb(v_stored_state);
      v_stored_states := v_stored_states || jsonb_build_object(v_key, v_stored_state);
    end if;
    v_expected := p_expected_state_digests ->> v_key;
    if v_stored is distinct from v_expected then
      if v_key = v_created_key and v_stored is not null then
        raise exception 'j102_subject_already_exists: % already holds a committed row; a subject that exists is advanced by a transition, never initialized again',
          v_key using errcode = '40001';
      end if;
      raise exception 'j102_stale_subject_digest: the current state of % is %, and the caller decided against %',
        v_key, coalesce(v_stored, 'absent'), coalesce(v_expected, 'absent')
        using errcode = '40001';
    end if;
  end loop;

  -- === THE PARENT CHAIN, on the committed rows ===============================
  -- The id of each context subject comes from the CREATED state's own parent
  -- reference, or from a field on the context subject before it -- so an
  -- assignment names its engagement and the engagement names its relationship,
  -- and neither link is taken on trust.
  for v_context_rule in
    select * from jsonb_array_elements(coalesce(v_contract -> 'required_context', '[]'::jsonb))
  loop
    v_context_kind := v_context_rule ->> 'subject';
    v_context_id := case v_context_rule -> 'identified_by' ->> 'source'
                      when 'created' then v_state ->> (v_context_rule -> 'identified_by' ->> 'field')
                      when 'context' then
                        (v_context_by_kind -> (v_context_rule -> 'identified_by' ->> 'subject'))
                          ->> (v_context_rule -> 'identified_by' ->> 'field')
                      else null end;
    if v_context_id is null then
      raise exception 'j102_required_context_unidentified: % runs under a % and nothing names it',
        p_initialization_id, v_context_kind using errcode = '22023';
    end if;
    v_prior := v_stored_states -> (v_context_kind || ':' || v_context_id);
    if v_prior is null then
      raise exception 'j102_required_context_not_locked: % requires % % to be loaded and unmoved, and it is in neither the compare-and-swap operands nor the database',
        p_initialization_id, v_context_kind, v_context_id using errcode = '22023';
    end if;
    for v_condition in select * from jsonb_array_elements(v_context_rule -> 'conditions') loop
      v_field := v_condition ->> 'field';
      v_actual_value := v_prior -> v_field;
      if v_condition ? 'equals' and v_actual_value is distinct from (v_condition -> 'equals') then
        raise exception 'j102_required_context_not_met: % requires % % to have % of %, and it is %',
          p_initialization_id, v_context_kind, v_context_id, v_field, v_condition -> 'equals',
          coalesce(v_actual_value, 'null'::jsonb) using errcode = '22023';
      end if;
      if v_condition ? 'in'
         and not (v_condition -> 'in' @> jsonb_build_array(v_actual_value)) then
        raise exception 'j102_required_context_not_met: % requires % % to have % in %, and it is %',
          p_initialization_id, v_context_kind, v_context_id, v_field, v_condition -> 'in',
          coalesce(v_actual_value, 'null'::jsonb) using errcode = '22023';
      end if;
      if v_condition ? 'not_in'
         and (v_condition -> 'not_in' @> jsonb_build_array(v_actual_value)) then
        raise exception 'j102_required_context_not_met: % refuses % % while its % is %',
          p_initialization_id, v_context_kind, v_context_id, v_field, v_actual_value
          using errcode = '22023';
      end if;
    end loop;
    v_context_by_kind := v_context_by_kind || jsonb_build_object(v_context_kind, v_prior);
    -- THE KEY THIS RULE ACTUALLY CONSULTED, kept so the operand set can be closed
    -- by identity below and so the receipt can report WHICH rows were read rather
    -- than only that reading happened.
    v_consulted_keys := v_consulted_keys
      || jsonb_build_array(to_jsonb(v_context_kind || ':' || v_context_id));
  end loop;

  -- === EVERY OPERAND IS A ROW THIS CALL ACTUALLY READ, BY IDENTITY ============
  --
  -- The pass above closed the operand set by KIND, before any lock, which stops a
  -- wholly unrelated kind. It does not stop the right kind with the wrong id:
  -- `relationship:SOMEBODY-ELSE` on a prospect creation, or a second
  -- `engagement:` beside the real one. Either would be locked, compare-and-swapped
  -- and then never consulted -- and would appear in this call's own receipt under
  -- `expected_state_digests` beside `required_context_enforced: true`, which reads
  -- as a check that ran on a row nothing looked at. It is receipt integrity and
  -- lock scope rather than a state bypass (nothing unconsulted is written), and it
  -- is reachable only by a direct holder of this function's EXECUTE grant, because
  -- the store keys `related_refs` by kind and sends exactly the contract's parents.
  --
  -- IT CANNOT BE ASKED ANY EARLIER. Which parent id this call reads is the created
  -- state's own reference for the first hop and a field on the hop before it for
  -- the second, so the answer exists only once the loop above has resolved both.
  for v_key in select * from jsonb_object_keys(p_expected_state_digests) loop
    if v_key <> v_created_key
       and not (v_consulted_keys @> jsonb_build_array(to_jsonb(v_key))) then
      raise exception 'j102_operand_subject_not_read_by_initialization: % created % and consulted %, and this request also locks % -- a row nothing here reads may not ride along on a creation',
        p_initialization_id, v_created_key,
        case when jsonb_array_length(v_consulted_keys) = 0 then '"no parent"'::jsonb
             else v_consulted_keys end,
        v_key using errcode = '22023';
    end if;
  end loop;

  -- === THE CREATED SHAPE, field for field ====================================
  -- Through the SAME interpreter the transition targets use, over the created
  -- state, the verified parents and the ids this call proposes. A key the
  -- contract does not declare, a key it declares and this request omits, or a
  -- declared key carrying anything other than its exact value refuses.
  v_proposed_states := jsonb_build_object(v_kind, v_state);
  v_ids := jsonb_build_object(v_kind, v_id);
  for v_field in
    select k from (
      select jsonb_object_keys(v_contract -> 'creation_shape') as k
      union
      select jsonb_object_keys(v_state)
    ) f order by k collate "C"
  loop
    v_effect := v_contract -> 'creation_shape' -> v_field;
    if v_effect is null then
      raise exception 'j102_created_subject_shape_mismatch: the % % created by % carries no %, and this request supplies one',
        v_kind, v_id, p_initialization_id, v_field using errcode = '22023';
    end if;
    if not (v_state ? v_field) then
      raise exception 'j102_created_subject_shape_mismatch: the % % created by % must carry %, and this request omits it',
        v_kind, v_id, p_initialization_id, v_field using errcode = '22023';
    end if;
    v_expected_value := ops.j102_expected_value(v_effect, '{}'::jsonb, v_proposed_states,
      v_context_by_kind, v_ids, '{}'::jsonb);
    v_actual_value := v_state -> v_field;
    if (v_expected_value ->> 'kind') = 'exact' then
      if v_actual_value is distinct from (v_expected_value -> 'value') then
        raise exception 'j102_created_subject_field_not_canonical: % creates % % with % of %, and this request supplies %',
          p_initialization_id, v_kind, v_id, v_field,
          coalesce(v_expected_value -> 'value', 'null'::jsonb),
          coalesce(v_actual_value, 'null'::jsonb) using errcode = '42501';
      end if;
    elsif (v_expected_value ->> 'kind') = 'declared_identifier' then
      -- THE ONE FIELD THE CALLER CHOOSES, and it is held to being an IDENTIFIER
      -- and nothing else: it may not be null, an object, a number or a state
      -- word smuggled into an identifier slot. The contract lists which fields
      -- may take this shape, so a caller cannot reach it for any other field.
      if not (v_contract -> 'declared_identifiers' ? v_field) then
        raise exception 'j102_declared_identifier_not_permitted: % declares the identifiers %, and this map computes a declared identifier for %',
          p_initialization_id, v_contract -> 'declared_identifiers', v_field
          using errcode = '22023';
      end if;
      if jsonb_typeof(v_actual_value) is distinct from 'string'
         or (v_actual_value #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$' then
        raise exception 'j102_created_subject_field_not_canonical: % creates % % with % as a declared identifier, and this request supplies %',
          p_initialization_id, v_kind, v_id, v_field,
          coalesce(v_actual_value, 'null'::jsonb) using errcode = '42501';
      end if;
      v_declared := v_declared || jsonb_build_object(v_field, v_actual_value);
    elsif (v_expected_value ->> 'kind') = 'any_of' then
      if not (v_expected_value -> 'values' @> jsonb_build_array(v_actual_value)) then
        raise exception 'j102_created_subject_field_not_canonical: % creates % % with % from %, and this request supplies %',
          p_initialization_id, v_kind, v_id, v_field, v_expected_value -> 'values',
          coalesce(v_actual_value, 'null'::jsonb) using errcode = '42501';
      end if;
    elsif (v_expected_value ->> 'kind') = 'optional_declared_identifier' then
      if not (coalesce(v_contract -> 'optional_declared_identifiers', '[]'::jsonb) ? v_field) then
        raise exception 'j102_declared_identifier_not_permitted: % declares the optional identifiers %, and this map computes one for %',
          p_initialization_id, v_contract -> 'optional_declared_identifiers', v_field
          using errcode = '22023';
      end if;
      if jsonb_typeof(v_actual_value) is distinct from 'null' then
        if jsonb_typeof(v_actual_value) is distinct from 'string'
           or (v_actual_value #>> '{}') !~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$' then
          raise exception 'j102_created_subject_field_not_canonical: % creates % % with % as an optional declared identifier, and this request supplies %',
            p_initialization_id, v_kind, v_id, v_field, v_actual_value using errcode = '42501';
        end if;
        -- THE PARTY LINK POINTS AT A REAL PARTY, checked here where the row
        -- lands: a reference to an id nobody holds is a dangling reference
        -- wearing the shape of identity.
        if v_field = 'party_id' and not exists (
             select 1 from public.party p where p.id::text = (v_actual_value #>> '{}')) then
          raise exception 'j102_party_not_found: % names party %, and no CARR party record has that id',
            p_initialization_id, v_actual_value #>> '{}' using errcode = '23503';
        end if;
        v_declared := v_declared || jsonb_build_object(v_field, v_actual_value);
      end if;
    else
      raise exception 'j102_expected_value_kind_unsupported: % computes % for %.%, which this writer does not compare',
        p_initialization_id, v_expected_value ->> 'kind', v_kind, v_field using errcode = '22023';
    end if;
  end loop;

  -- ONE ACTIVE NEGOTIATION PER PROPERTY (owner ruling 2026-09-25), re-checked
  -- here in the writer's own transaction; the partial unique index
  -- j102_one_active_negotiation_per_property is the structural backstop for a
  -- concurrent pair both passing this check.
  if coalesce((v_contract ->> 'unique_active_per_property')::boolean, false)
     and exists (
       select 1 from ops.j102_subject_current s
        where s.tenant = ops.f01_tenant()
          and s.subject_kind = 'property_negotiation'
          and s.parent_id = v_state ->> 'assignment_id'
          and s.envelope -> 'record' -> 'state' ->> 'property_id' = v_state ->> 'property_id'
          and (ops.j102_admission_policy() -> 'active_negotiation_states')
                ? (s.envelope -> 'record' -> 'state' ->> 'negotiation_state')) then
    raise exception 'j102_active_negotiation_exists: assignment % already holds an active negotiation on property %',
      v_state ->> 'assignment_id', v_state ->> 'property_id' using errcode = '23505';
  end if;

  -- === THE HISTORY, in the same transaction ==================================
  v_event_record := p_event_envelope -> 'record';
  if jsonb_typeof(v_event_record) is distinct from 'object'
     or jsonb_typeof(v_event_record -> 'event') is distinct from 'object' then
    raise exception 'j102_event_envelope_not_an_object: an event envelope carries % where its record and nested event should be',
      coalesce(jsonb_typeof(v_event_record), 'nothing') using errcode = '22023';
  end if;
  v_event := v_event_record -> 'event';
  if (v_event_record ->> 'recorded_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: recorded_by is derived, never supplied'
      using errcode = '42501';
  end if;
  if (v_event_record ->> 'sponsoring_partner') is distinct from v_sponsor then
    raise exception 'j102_sponsor_mismatch: the event is recorded under %, derived from the authenticated login, and the request names %',
      v_sponsor, coalesce(v_event_record ->> 'sponsoring_partner', 'none') using errcode = '42501';
  end if;
  if (v_event_record ->> 'recorded_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: recorded_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_event_record ->> 'recorded_at', 'null') using errcode = '42501';
  end if;
  if ops.f01_digest_jsonb(v_event_record) is distinct from (p_event_envelope ->> 'record_digest') then
    raise exception 'j102_event_digest_mismatch: the supplied event does not hash to its claim'
      using errcode = '22000';
  end if;
  if (v_event_record ->> 'transition_id') is distinct from p_initialization_id then
    raise exception 'j102_event_transition_mismatch: this call applies %, and an event claims %',
      p_initialization_id, coalesce(v_event_record ->> 'transition_id', 'nothing')
      using errcode = '22023';
  end if;
  if (v_event_record ->> 'schema_version') is distinct from v_event_record_schema then
    raise exception 'j102_event_schema_version_mismatch: a stored lifecycle event is %, and this request carries %',
      v_event_record_schema, coalesce(v_event_record ->> 'schema_version', 'nothing')
      using errcode = '22023';
  end if;
  if (v_event ->> 'schema_version') is distinct from v_event_schema then
    raise exception 'j102_event_payload_schema_version_mismatch: the kernel builds every lifecycle event as %, and this request carries %',
      v_event_schema, coalesce(v_event ->> 'schema_version', 'nothing') using errcode = '22023';
  end if;
  if (v_event_record ->> 'tenant') is distinct from ops.f01_tenant()
     or (p_event_envelope ->> 'tenant') is distinct from ops.f01_tenant() then
    raise exception 'j102_event_tenant_mismatch: this database is tenant %, and an event names %',
      ops.f01_tenant(),
      coalesce(v_event_record ->> 'tenant', p_event_envelope ->> 'tenant', 'nothing')
      using errcode = '42501';
  end if;
  if (p_event_envelope ->> 'record_kind') is distinct from 'stored_lifecycle_event' then
    raise exception 'j102_event_record_kind_mismatch: this writer appends lifecycle events, and this envelope is a %',
      coalesce(p_event_envelope ->> 'record_kind', 'record of no kind') using errcode = '22023';
  end if;
  if exists (select 1 from jsonb_object_keys(v_event_record) as k
              where not (v_event_keys @> jsonb_build_array(k)))
     or exists (select 1 from jsonb_array_elements_text(v_event_keys) as k
                 where not (v_event_record ? k)) then
    raise exception 'j102_event_record_shape_unrecognised: a stored lifecycle event carries exactly %, and this request carries %',
      v_event_keys,
      coalesce((select jsonb_agg(k order by k) from jsonb_object_keys(v_event_record) as k),
               '[]'::jsonb) using errcode = '22023';
  end if;
  -- THE EVENT IS THE ONE THIS INITIALIZATION APPENDS, on the subject it created.
  if (v_event ->> 'event_kind') is distinct from (v_event_spec ->> 'event_kind')
     or (v_event ->> 'subject_kind') is distinct from (v_event_spec ->> 'subject')
     or (v_event ->> 'subject_id') is distinct from v_id then
    raise exception 'j102_event_missing_or_wrong: % appends a % event on % %, and this request carries a % event on % %',
      p_initialization_id, v_event_spec ->> 'event_kind', v_event_spec ->> 'subject', v_id,
      coalesce(v_event ->> 'event_kind', 'nameless'),
      coalesce(v_event ->> 'subject_kind', 'no kind'), coalesce(v_event ->> 'subject_id', 'no id')
      using errcode = '22023';
  end if;
  -- AND ITS WHOLE NESTED PAYLOAD, computed from the row that was created rather
  -- than from the caller's account of it.
  for v_field in
    select k from (
      select jsonb_object_keys(coalesce(v_event_spec -> 'detail', '{}'::jsonb)) as k
      union
      select jsonb_object_keys(v_event)
    ) f order by k collate "C"
  loop
    continue when v_identity_keys @> jsonb_build_array(v_field);
    v_effect := (v_event_spec -> 'detail') -> v_field;
    if v_effect is null then
      raise exception 'j102_event_detail_not_produced_by_transition: the % event % appends carries %, and this request supplies an extra %',
        v_event ->> 'event_kind', p_initialization_id,
        coalesce((select jsonb_agg(k order by k)
                    from jsonb_object_keys(coalesce(v_event_spec -> 'detail', '{}'::jsonb)) as k),
                 '[]'::jsonb),
        v_field using errcode = '42501';
    end if;
    if not (v_event ? v_field) then
      raise exception 'j102_event_detail_missing: the % event % appends names %, and this request omits it',
        v_event ->> 'event_kind', p_initialization_id, v_field using errcode = '22023';
    end if;
    v_expected_value := ops.j102_expected_value(v_effect, '{}'::jsonb, v_proposed_states,
      v_context_by_kind, v_ids, '{}'::jsonb);
    v_actual_value := v_event -> v_field;
    if (v_expected_value ->> 'kind') is distinct from 'exact' then
      raise exception 'j102_expected_value_kind_unsupported: the % event computes % for %, which this writer does not compare',
        v_event ->> 'event_kind', v_expected_value ->> 'kind', v_field using errcode = '22023';
    end if;
    if v_actual_value is distinct from (v_expected_value -> 'value') then
      raise exception 'j102_event_detail_not_canonical: the % event names % of %, and this request supplies %',
        v_event ->> 'event_kind', v_field,
        coalesce(v_expected_value -> 'value', 'null'::jsonb),
        coalesce(v_actual_value, '"absent"'::jsonb) using errcode = '42501';
    end if;
  end loop;
  -- THE EMPTY CITATION IS EXACT, not merely permitted. An initialization rests on
  -- no evidence, and a history row claiming it rested on some would be a citation
  -- nothing re-read under this lock -- which is the same defect the transition
  -- writer refuses in the other direction.
  if (v_event_record -> 'evidence_references') is distinct from '[]'::jsonb then
    raise exception 'j102_initialization_cites_evidence: % rests on no evidence -- no evidence in this rail can bind to a subject that does not exist yet -- and this event cites %',
      p_initialization_id,
      coalesce(v_event_record -> 'evidence_references', '"nothing"'::jsonb) using errcode = '42501';
  end if;

  -- === THE WRITE, one subject and its history or neither =====================
  v_state_digest := ops.f01_digest_jsonb(v_state);
  insert into ops.j102_subject_current
    (tenant, subject_kind, subject_id, envelope, envelope_digest, state_digest,
     parent_id, deal_state, updated_by, sponsoring_partner, updated_at)
  values (
    ops.f01_tenant(), v_kind, v_id, p_subject_envelope,
    ops.f01_digest_jsonb(p_subject_envelope), v_state_digest,
    coalesce(v_state ->> 'relationship_id', v_state ->> 'engagement_id',
             v_state ->> 'assignment_id'),
    v_state ->> 'deal_state',
    v_actor, v_sponsor, v_txn_now);
  v_event_digest := ops.f01_digest_jsonb(v_event_record);
  insert into ops.j102_subject_event
    (tenant, subject_kind, subject_id, event_kind, transition_id, envelope, envelope_digest,
     event_digest, recorded_by, sponsoring_partner, recorded_at, idempotency_key)
  values (
    ops.f01_tenant(), v_event ->> 'subject_kind', v_event ->> 'subject_id',
    v_event ->> 'event_kind', v_event_record ->> 'transition_id',
    p_event_envelope, ops.f01_digest_jsonb(p_event_envelope), v_event_digest,
    v_actor, v_sponsor, v_txn_now, p_idempotency_key);

  v_result := jsonb_build_object(
    'operation', v_operation,
    'decision', 'allow',
    'outcome', 'initialized',
    'actor_slug', v_actor,
    'initialization_id', p_initialization_id,
    'created_subject_kind', v_kind,
    'created_subject_id', v_id,
    'subject_digests', jsonb_build_object(v_created_key, v_state_digest),
    'event_digests', jsonb_build_array(v_event_digest),
    -- DERIVED from the admission contract, on the same terms as the transition
    -- receipt: the decision refs are a property of the initialization and the
    -- kernel's diagnostic reason is carried under its own name as the caller's.
    'decision_refs', coalesce(v_contract -> 'decision_refs', '[]'::jsonb),
    'decision_refs_source', 'derived_from_the_admission_contract',
    'caller_reported_reason_id', p_diagnostics ->> 'reason_id',
    'caller_reported_reason_id_scope',
      'kernel_result_diagnostic_asserted_by_the_caller_and_not_recomputed_here',
    'admission_policy_id', v_policy ->> 'policy_id',
    'actor_authorization_class', v_class,
    -- WHAT WAS ENFORCED, as facts rather than hedges -- AND, FOR THE CONTEXT, AS
    -- THE SET THAT WAS ACTUALLY READ.
    --
    -- `required_context_enforced` and `parent_subjects_locked_and_unmoved` are
    -- unconditional trues, and on a PARENTLESS creation they describe the empty
    -- set: nothing was verified because there was nothing to verify, and a bare
    -- boolean reads on a prospect receipt as though a chain had been walked. The
    -- two booleans stay, because a caller comparing receipts across kinds needs a
    -- stable field, and `context_subjects_consulted` beside them is what makes
    -- them checkable: `[]` on a prospect, the engagement and relationship keys on
    -- an assignment, the assignment key on a negotiation -- every one of them a
    -- row this call resolved, locked, compare-and-swapped and read a condition
    -- off, and no row that merely travelled with the request.
    'creation_shape_enforced', true,
    'required_context_enforced', true,
    'parent_subjects_locked_and_unmoved', true,
    'context_subjects_consulted', v_consulted_keys,
    'context_subjects_consulted_count', jsonb_array_length(v_consulted_keys),
    'subject_created', true,
    -- AND THE ANTI-BYPASS STATEMENT. This writer performs no transition, so it
    -- checked no transition prerequisite and skipped none. The row it created is
    -- the earliest declared state of its kind and every transition that follows
    -- still has to satisfy its own `from` axes, its instrument kind and its
    -- evidence.
    'transition_applied', false,
    'transition_prerequisites_bypassed', false,
    'advances_lifecycle_state', false,
    'evidence_required', false,
    'evidence_supplied', 0,
    -- The identifiers the caller genuinely chose, reported so a reader can see
    -- exactly how much of this row came from the request: an id, and — for a
    -- negotiation — the property it concerns. Everything else is fixed or derived.
    'declared_identifiers', v_declared,
    'committed_at', v_txn_now_text,
    'expected_state_digests', p_expected_state_digests,
    'request_digest', p_request_digest,
    'request_digest_scope', 'caller_supplied_intent_digest_not_recomputed_here',
    'committed_content_digest', ops.f01_digest_jsonb(jsonb_build_object(
      'initialization_id', p_initialization_id,
      'committed_at', v_txn_now_text,
      'subject_digests', jsonb_build_object(v_created_key, v_state_digest),
      'event_digests', jsonb_build_array(v_event_digest))),
    'committed_content_digest_source', 'recomputed_from_committed_rows',
    'readback', jsonb_build_object(v_kind, ops.j102_subject(v_kind, v_id)),
    'external_effects', false);
  return ops.j102_settle_idempotency(v_operation, p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_initialize_subject(text,jsonb,jsonb,jsonb,text,text,jsonb) is
  'The ONLY writer that creates the first lifecycle subject of a chain, and it performs no transition. It admits the call against the closed SQL admission map''s initialization half -- the initialization must exist, the operation must be one that performs it, and the DERIVED principal class must be permitted -- then claims its idempotency key before reading any state and locks the created key and every parent in ascending order. The created key''s operand must be an EXPLICIT NULL and the compare-and-swap refuses if any row holds it, so two concurrent creations of one id serialize on the shared advisory lock and neither overwrites the other: at READ COMMITTED the loser gets the named j102_subject_already_exists, and at a stronger isolation level its snapshot predates the winner''s commit and the primary key refuses the insert with 23505 instead. Every operand key must be the created subject or a parent this call actually RESOLVED and read, by identity rather than by kind, so no unconsulted row can be locked and then reported as checked; the receipt names the consulted keys, which is empty for a parentless creation. Every parent the contract requires must be in the operand map, must still hash to the digest the caller decided against, and must satisfy its declared conditions on the COMMITTED row -- an ACTIVE engagement held by a CLIENT for an assignment, a still-open assignment for a negotiation. The created row is then held to the contract''s exact shape field by field, so a relationship cannot be born a client, an assignment born committed or a negotiation born accepted; the single event is held to its declared kind, subject and whole nested payload; and its evidence citation must be EXACTLY empty, because no evidence in this rail can bind to a subject that does not exist yet. Actor, instant, schema versions and tenant are derived or fixed. It never touches ops.j102_apply_transition, and that function''s refusal to create its own primary subject is unaffected: every transition prerequisite remains mandatory afterwards.';

-- ---------------------------------------------------------------------------
-- The three non-transition writers.
-- ---------------------------------------------------------------------------

create or replace function ops.j102_record_first_party_fact(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_class text := ops.f01_principal() ->> 'authorization_class';
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_record jsonb; v_digest text; v_result jsonb;
begin
  v_replay := ops.j102_claim_idempotency('record-lifecycle-fact', p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_fact_digest_mismatch: the supplied record does not hash to its claim'
      using errcode = '22000';
  end if;
  if (v_record ->> 'recorded_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: recorded_by is derived, never supplied'
      using errcode = '42501';
  end if;
  -- H5. The author's class is DERIVED from the same principal the actor comes
  -- from; a record claiming a different one is refused rather than believed.
  if (v_record ->> 'recorded_by_authorization_class') is distinct from v_class then
    raise exception 'j102_author_class_injection_refused: the author class is derived (% here), never supplied',
      v_class using errcode = '42501';
  end if;
  if v_record ->> 'record_kind' in ('winning_property_commitment', 'closing_settlement',
                                    'deal_failure', 'lifecycle_correction')
     and v_class <> 'verified_partner' then
    raise exception 'j102_partner_authored_record_refused: a % record is authored by a verified partner, and % holds %',
      v_record ->> 'record_kind', v_actor, v_class using errcode = '42501';
  end if;
  -- H4. The instant is the transaction's, verified and then stamped from it.
  if (v_record ->> 'recorded_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: recorded_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'recorded_at', 'null') using errcode = '42501';
  end if;
  -- BLOCK-2, and WHY THE EXISTENCE OF THE BOUND SUBJECT IS NOT RAISED HERE.
  --
  -- The store refuses `bound_subject_not_found` before it reaches this function,
  -- so a record bound to an id nobody holds does not get written through the
  -- shipped path. This layer leaves subject existence to the store and the
  -- transition recheck; ops.j102_initialize_subject now creates first subjects
  -- separately. What actually gives the binding its force is
  -- one layer along: ops.j102_recheck_evidence re-reads this row's own typed
  -- binding under the transition's lock and refuses when it is not the subject
  -- being moved. A record bound to an id that does not exist can therefore never
  -- advance anything, which is the property that matters.
  insert into ops.j102_first_party_record
    (tenant, record_kind, record_id, envelope, envelope_digest, record_digest,
     bound_subject_kind, bound_subject_id,
     closing_date, recorded_by, recorded_by_class, recorded_at, idempotency_key)
  values (
    ops.f01_tenant(), v_record ->> 'record_kind', v_record ->> 'record_id',
    p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
    v_record ->> 'subject_kind', v_record ->> 'subject_id',
    -- THE ONE DATE THAT IS NOT THE SERVER'S, and it is the business fact this
    -- record exists to carry: Q094 closes a deal on the ACTUAL final closing
    -- date, which is a statement about the world and not about when the row was
    -- written. It stays distinct from recorded_at above on purpose.
    case when v_record ->> 'closing_date' is null then null
         else ops.f01_instant(v_record ->> 'closing_date') end,
    v_actor, v_class, v_txn_now, p_idempotency_key);
  v_result := jsonb_build_object(
    'operation', 'record-lifecycle-fact', 'decision', 'allow',
    'reason_id', 'first_party_record_appended', 'actor_slug', v_actor,
    'record_kind', v_record ->> 'record_kind', 'record_id', v_record ->> 'record_id',
    'record_digest', v_digest,
    'bound_subject_kind', v_record ->> 'subject_kind',
    'bound_subject_id', v_record ->> 'subject_id',
    'recorded_by_authorization_class', v_class,
    'committed_at', v_txn_now_text,
    'readback', ops.j102_first_party_record(v_record ->> 'record_kind', v_record ->> 'record_id'),
    'advances_lifecycle_state', false, 'external_effects', false);
  return ops.j102_settle_idempotency('record-lifecycle-fact', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_first_party_fact(jsonb,text,text) is
  'Append one authenticated first-party business record, bound to an existing subject and stamped with the derived author, the derived author class and the database transaction time. The record''s closing_date remains the business fact it carries and is deliberately distinct from the server instant. It advances no lifecycle state; a transition still has to accept it as evidence.';

create or replace function ops.j102_record_evidence_subject_link(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_class text := ops.f01_principal() ->> 'authorization_class';
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_record jsonb; v_digest text; v_result jsonb; v_seq bigint;
  v_identity jsonb;
begin
  -- A partner's statement about which transaction a document belongs to. An
  -- agent that could make it could bind any authentic lease to any deal.
  if v_class <> 'verified_partner' then
    raise exception 'j102_evidence_association_requires_partner_authority: % holds %',
      v_actor, v_class using errcode = '42501';
  end if;
  v_replay := ops.j102_claim_idempotency('record-evidence-subject-link',
    p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_link_digest_mismatch' using errcode = '22000';
  end if;
  if (v_record ->> 'associated_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: associated_by is derived, never supplied'
      using errcode = '42501';
  end if;
  if (v_record ->> 'associated_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: associated_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'associated_at', 'null') using errcode = '42501';
  end if;
  -- BOTH ENDS ARE REAL, checked here as well as in the store. The subject has to
  -- exist in this rail; the pin has to exist in F01, at exactly the version and
  -- content digest named, so an association cannot be made to a document version
  -- the record layer does not hold.
  if ops.j102_subject(v_record ->> 'subject_kind', v_record ->> 'subject_id') is null then
    raise exception 'j102_link_subject_missing: no % % exists to associate evidence with',
      v_record ->> 'subject_kind', v_record ->> 'subject_id' using errcode = '23503';
  end if;
  if (v_record ->> 'evidence_source') = 'f01_document' then
    v_identity := ops.f01_read('document', jsonb_build_object(
      'document_id', v_record ->> 'evidence_ref')) -> 'body' -> 'record' -> 'neon_identity';
    if (v_identity ->> 'content_digest') is distinct from (v_record ->> 'content_digest') then
      raise exception 'j102_link_pin_not_held: F01 does not hold document % at the content digest this association names',
        v_record ->> 'evidence_ref' using errcode = '23503';
    end if;
    -- M-c's other half. The content digest was checked against F01 and the
    -- VERSION NUMBER was not, so an association could name version 4 of a
    -- document F01 holds at version 7 and read back as a healthy pin. Both halves
    -- of the pin are F01's fact, so both are compared to F01.
    if (v_identity ->> 'version_no') is distinct from (v_record ->> 'version_no') then
      raise exception 'j102_link_pin_not_held: F01 holds document % at version %, not the version % this association names',
        v_record ->> 'evidence_ref', coalesce(v_identity ->> 'version_no', 'unknown'),
        coalesce(v_record ->> 'version_no', 'unstated') using errcode = '23503';
    end if;
  elsif ops.f01_stored_artifact(v_record ->> 'evidence_ref') is null then
    raise exception 'j102_link_pin_not_held: no stored corporate artifact exists for %',
      v_record ->> 'evidence_ref' using errcode = '23503';
  end if;
  insert into ops.j102_evidence_subject_link
    (tenant, evidence_source, evidence_ref, version_no, content_digest,
     subject_kind, subject_id, envelope, envelope_digest, link_digest,
     associated_by, associated_by_class, associated_at, idempotency_key)
  values (
    ops.f01_tenant(), v_record ->> 'evidence_source', v_record ->> 'evidence_ref',
    coalesce((v_record ->> 'version_no')::integer, 0), v_record ->> 'content_digest',
    v_record ->> 'subject_kind', v_record ->> 'subject_id',
    p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
    v_actor, v_class, v_txn_now, p_idempotency_key)
  returning link_seq into v_seq;
  v_result := jsonb_build_object(
    'operation', 'record-evidence-subject-link', 'decision', 'allow',
    'reason_id', 'evidence_subject_association_appended', 'actor_slug', v_actor,
    'link_digest', v_digest,
    'evidence_source', v_record ->> 'evidence_source',
    'bound_subject_kind', v_record ->> 'subject_kind',
    'bound_subject_id', v_record ->> 'subject_id',
    'committed_at', v_txn_now_text,
    'readback', jsonb_build_object('link_seq', v_seq, 'record', v_record),
    'advances_lifecycle_state', false, 'creates_document', false,
    'asserts_document_state', false, 'external_effects', false);
  return ops.j102_settle_idempotency('record-evidence-subject-link', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_evidence_subject_link(jsonb,text,text) is
  'BLOCK-2''s producer: append one partner-authored association between an exact F01 document version or corporate artifact and one existing lifecycle subject. It creates no document, asserts no document state, and advances no lifecycle state.';

create or replace function ops.j102_record_salesforce_reference(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_record jsonb; v_digest text; v_result jsonb; v_seq bigint;
begin
  v_replay := ops.j102_claim_idempotency('link-salesforce-reference', p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_reference_digest_mismatch' using errcode = '22000';
  end if;
  if (v_record ->> 'recorded_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: recorded_by is derived, never supplied'
      using errcode = '42501';
  end if;
  -- Q083, enforced rather than merely intended: a reference whose stored record
  -- claims to set lifecycle state is refused outright.
  if (v_record ->> 'phase_label_is_doctorcre_state') is distinct from 'false' then
    raise exception 'j102_salesforce_label_mapping_refused: a Salesforce phase is never a DoctorCRE state'
      using errcode = '42501';
  end if;
  -- H4. `recorded_at` is OURS and is verified; `observed_at` is SALESFORCE'S own
  -- statement about when it saw its own record, and stays the caller's to supply.
  -- The two are different facts and this is the one place they meet.
  if (v_record ->> 'recorded_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: recorded_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'recorded_at', 'null') using errcode = '42501';
  end if;
  -- M-f. observed_at stays SALESFORCE'S fact and is not stamped from our clock --
  -- but a fact about the past cannot have been observed in the future, and an
  -- unbounded one lets a reference claim an observation that has not happened. The
  -- bound is the same one the kernel already applies to every other observed
  -- instant it judges (`evidence_observed_after_server_time`), so this restates no
  -- policy: it is the stored-fact contract, enforced where the row lands.
  if ops.f01_instant(v_record ->> 'observed_at') > v_txn_now then
    raise exception 'j102_observed_at_in_the_future: Salesforce cannot have observed its own record at %, which is after this transaction''s %',
      v_record ->> 'observed_at', v_txn_now_text using errcode = '22023';
  end if;
  insert into ops.j102_salesforce_reference
    (tenant, opportunity_id, opportunity_name, opportunity_phase, linked_subject_kind,
     linked_subject_id, observed_at, envelope, envelope_digest, reference_digest,
     recorded_by, recorded_at, idempotency_key)
  values (
    ops.f01_tenant(), v_record ->> 'opportunity_id', v_record ->> 'opportunity_name',
    v_record ->> 'opportunity_phase', v_record ->> 'linked_subject_kind',
    v_record ->> 'linked_subject_id', ops.f01_instant(v_record ->> 'observed_at'),
    p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
    v_actor, v_txn_now, p_idempotency_key)
  returning reference_seq into v_seq;
  v_result := jsonb_build_object(
    'operation', 'link-salesforce-reference', 'decision', 'allow',
    'reason_id', case when v_record ->> 'linked_subject_kind' is null
                   then 'external_reference_recorded_unlinked'
                   else 'external_reference_progressively_linked' end,
    'actor_slug', v_actor,
    'opportunity_id', v_record ->> 'opportunity_id',
    'linked_subject_kind', v_record ->> 'linked_subject_kind',
    'reference_digest', v_digest,
    'committed_at', v_txn_now_text,
    'readback', jsonb_build_object('reference_seq', v_seq, 'record', v_record),
    'sets_lifecycle_state', false, 'external_effects', false);
  return ops.j102_settle_idempotency('link-salesforce-reference', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_salesforce_reference(jsonb,text,text) is
  'Q083: append one external Salesforce opportunity reference with its own name and phase. It sets no lifecycle state, and a record claiming otherwise is refused.';

create or replace function ops.j102_record_correction(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_principal jsonb := ops.f01_principal();
  v_actor text := ops.f01_context_actor_slug();
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_record jsonb; v_digest text; v_result jsonb; v_seq bigint;
begin
  -- humanOnly plus authorityOnly, checked HERE and not only in the handler, so a
  -- writer that somehow reached this function still refuses.
  --
  -- M3. FROM THE DERIVED PRINCIPAL, NOT FROM A ROLE-NAME LITERAL. This used to
  -- compare session_user against the two authority login names spelled out
  -- inline (deliberately not repeated here: the fixture's M3 check reads this
  -- function's whole text, comments included, for exactly that literal), which
  -- was a SECOND identity source sitting beside ops.f01_principal() — one
  -- that would have to be edited in two places to stay true, and that answered a
  -- different question from the one every other writer here asks. The principal
  -- is derived from session_user in exactly one place, and this reads that.
  if (v_principal ->> 'human') is distinct from 'true'
     or (v_principal ->> 'authorization_class') is distinct from 'verified_partner' then
    raise exception 'j102_correction_requires_partner_authority: % may not correct the lifecycle record',
      v_actor using errcode = '42501';
  end if;
  v_replay := ops.j102_claim_idempotency('record-lifecycle-correction', p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_receipt_digest_mismatch' using errcode = '22000';
  end if;
  if (v_record ->> 'corrected_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: corrected_by is derived, never supplied'
      using errcode = '42501';
  end if;
  if (v_record ->> 'corrected_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: corrected_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'corrected_at', 'null') using errcode = '42501';
  end if;
  -- The correction must rest on a durable authored record, not on a summary of
  -- what somebody meant. An absent one refuses here as well as in the store.
  if ops.j102_first_party_record('lifecycle_correction',
       v_record ->> 'correction_record_id') is null then
    raise exception 'j102_correction_record_missing: no lifecycle_correction record % exists',
      v_record ->> 'correction_record_id' using errcode = '23503';
  end if;
  insert into ops.j102_correction_receipt
    (tenant, subject_kind, subject_id, correction_record_id, reason, prior_state_digest,
     envelope, envelope_digest, receipt_digest, corrected_by, corrected_at, idempotency_key)
  values (
    ops.f01_tenant(), v_record ->> 'subject_kind', v_record ->> 'subject_id',
    v_record ->> 'correction_record_id', v_record ->> 'reason',
    v_record ->> 'prior_state_digest',
    p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
    v_actor, v_txn_now, p_idempotency_key)
  returning receipt_seq into v_seq;
  v_result := jsonb_build_object(
    'operation', 'record-lifecycle-correction', 'decision', 'allow',
    'reason_id', 'correction_receipt_appended', 'actor_slug', v_actor,
    'receipt_digest', v_digest,
    'committed_at', v_txn_now_text,
    'corrected_fields', coalesce(v_record -> 'corrected_fields', '[]'::jsonb),
    'readback', jsonb_build_object('receipt_seq', v_seq, 'record', v_record),
    'external_effects', false);
  return ops.j102_settle_idempotency('record-lifecycle-correction', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_correction(jsonb,text,text) is
  'Q082/Q072: append one partner-authority correction receipt binding the reason, the durable correction record and the exact prior state digest. Append-only; it overwrites nothing and no assistant text is ever its basis.';

-- ---------------------------------------------------------------------------
-- THE RECONCILIATION WRITER -- Q103's visible conflict, written under the same
-- governance every other writer in this file carries.
--
-- THE ONE-ARGUMENT FORM IS REPLACED, NOT SHADOWED. It took an envelope and
-- nothing else: no idempotency key, so a retried request wrote a second visible
-- item; no compare-and-swap operand, so the `current_version_digest` it stored
-- was whatever the caller had read whenever it read it; and no re-read of the
-- subject, so a conflict item could be filed claiming a "current" version that
-- had stopped being current before the insert. An application-level readback
-- cannot close any of that -- it is not atomic, two callers pass it
-- simultaneously, and it cannot tell a stale claim from a fresh one at all. The
-- old signature is DROPPED so no ungoverned overload survives beside this one;
-- `create or replace` alone would have left it callable, and its grants revoked
-- and re-granted below name the new signature.
--
-- WHAT IT NOW BINDS, and each is a fact about the row at COMMIT time rather than
-- at read time:
--
--   1. THE REQUEST. It claims its idempotency key through the same
--      ops.j102_claim_idempotency every sibling uses, so a replay returns the
--      stored outcome and writes nothing, and the same key over DIFFERENT bytes
--      refuses rather than substituting one conflict for another. The claim is
--      tier 1 and is taken before any state is read.
--   2. THE SUBJECT, under the tier-2 advisory lock in the established order, with
--      an explicit compare-and-swap operand. The operand must be the digest the
--      caller decided against AND the digest the row actually holds.
--   3. THE "CURRENT" VERSION THE ITEM CLAIMS. It must equal that same committed
--      digest. A conflict item whose `current_version_digest` is not current is a
--      stale reading filed as a fact, and it is the shape a person resolving the
--      conflict is least able to detect.
--   4. THE STATE SNAPSHOT INSIDE THE ITEM. The evidence the item carries so a
--      human can see what the record says now must HASH to the version it names.
--   5. THE HISTORY EVIDENCE. The newest row of the tail the item carries must
--      still be the newest committed event for that subject.
--   6. THAT THERE IS A CONFLICT AT ALL. A base equal to the current version is
--      not a conflict, and an item recording one would be noise in the one place
--      a person is supposed to look.
--
-- WHAT IT DELIBERATELY DOES NOT DO: collapse distinct proposals. Two callers, or
-- one caller twice, proposing DIFFERENT edits against the same two versions have
-- raised two real conflicts, and both are visible. Idempotency is keyed on the
-- REQUEST -- which covers the edits -- and on nothing else; there is no unique
-- index over the version pair, deliberately, because one would silently discard
-- the second proposal.
-- ---------------------------------------------------------------------------

-- The ungoverned single-argument form holds no data and is removed rather than
-- left callable beside its replacement.
drop function if exists ops.j102_record_reconciliation_item(jsonb);

create or replace function ops.j102_record_reconciliation_item(
  p_envelope jsonb, p_expected_state_digests jsonb,
  p_idempotency_key text, p_request_digest text, p_diagnostics jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_class text := ops.f01_principal() ->> 'authorization_class';
  v_operation text := p_diagnostics ->> 'operation';
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  -- The same map every other governed writer here reads, so the vocabulary a
  -- direct caller is held to is the kernel's transcribed vocabulary and not a
  -- second list maintained beside it.
  v_recon jsonb := ops.j102_admission_policy() -> 'reconciliation';
  v_edit jsonb;
  v_side text;
  v_field text;
  v_registered jsonb;
  v_replay jsonb;
  v_record jsonb;
  v_evidence jsonb;
  v_tail jsonb;
  v_digest text;
  v_kind text;
  v_id text;
  v_key text;
  v_operand_keys integer;
  v_stored_state jsonb;
  v_stored text;
  v_expected text;
  v_newest text;
  v_seq bigint;
  v_result jsonb;
begin
  if v_operation is distinct from 'record-lifecycle-reconciliation' then
    raise exception 'j102_operation_reconciliation_mismatch: this writer records reconciliation items for the record-lifecycle-reconciliation operation, and this call names %',
      coalesce(v_operation, 'no operation') using errcode = '22023';
  end if;
  if jsonb_typeof(p_expected_state_digests) is distinct from 'object' then
    raise exception 'j102_expected_state_digests_not_an_object: the subject this conflict is about carries a compare-and-swap operand; this call supplies %',
      coalesce(jsonb_typeof(p_expected_state_digests), 'no operand map at all')
      using errcode = '22023';
  end if;

  -- TIER 1, and before any state is read, exactly as every sibling writer does.
  v_replay := ops.j102_claim_idempotency(v_operation, p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;

  v_record := p_envelope -> 'record';
  if jsonb_typeof(v_record) is distinct from 'object' then
    raise exception 'j102_item_envelope_not_an_object: a reconciliation envelope carries % where its record should be',
      coalesce(jsonb_typeof(v_record), 'nothing') using errcode = '22023';
  end if;
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_item_digest_mismatch: the supplied item does not hash to its claim'
      using errcode = '22000';
  end if;
  if (p_envelope ->> 'record_kind') is distinct from 'stored_reconciliation_item' then
    raise exception 'j102_item_record_kind_mismatch: this writer stores reconciliation items, and this envelope is a %',
      coalesce(p_envelope ->> 'record_kind', 'record of no kind') using errcode = '22023';
  end if;
  if (v_record ->> 'tenant') is distinct from ops.f01_tenant()
     or (p_envelope ->> 'tenant') is distinct from ops.f01_tenant() then
    raise exception 'j102_item_tenant_mismatch: this database is tenant %, and this item names %',
      ops.f01_tenant(),
      coalesce(v_record ->> 'tenant', p_envelope ->> 'tenant', 'nothing') using errcode = '42501';
  end if;
  if (v_record ->> 'proposed_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: proposed_by is derived, never supplied'
      using errcode = '42501';
  end if;
  -- AN ITEM MAY NOT RESOLVE ITSELF. The relation restates these three as CHECKs;
  -- they are what a caller would otherwise use to file a conflict that reads as
  -- already handled.
  --
  -- COMPARED AS JSON BOOLEANS, NOT AS TEXT. `->>` renders the string "false" and
  -- the boolean false the same way, so a text comparison accepts an item whose
  -- flags are strings -- a row that looks right to this writer and is a different
  -- type to everything that reads it afterwards.
  if (v_record -> 'resolved_by_machine') is distinct from 'false'::jsonb
     or (v_record -> 'visible') is distinct from 'true'::jsonb
     or (v_record -> 'applied') is distinct from 'false'::jsonb then
    raise exception 'j102_reconciliation_resolves_itself: a conflict lands visible, unapplied and unresolved by any machine, and each is a JSON boolean; this one carries resolved_by_machine=%, visible=%, applied=%',
      coalesce(v_record -> 'resolved_by_machine', 'null'::jsonb),
      coalesce(v_record -> 'visible', 'null'::jsonb),
      coalesce(v_record -> 'applied', 'null'::jsonb) using errcode = '42501';
  end if;

  -- THE LABEL IS NOT THE CALLER'S TO INVENT EITHER.
  --
  -- On the shipped path `conflict_kind` and every edit's `field_class` are
  -- DERIVED -- the store refuses both as caller-supplied fields and spreads the
  -- kernel's own item. This function, though, is granted to carr_writer, and that
  -- is the caller the admission map exists for. Without these two checks a direct
  -- caller could file a visible item labelled `material_class_edit` whose edits
  -- are labelled `routine`, or labelled with a kind no evaluator emits, and the
  -- receipt derives `material_incoming_fields` and `unclassified_fields` from
  -- exactly those stored labels. It cannot merge, apply or resolve anything --
  -- but it mislabels the queue a person is supposed to read, and the map already
  -- holds the vocabulary needed to refuse it.
  -- The null arm is spelled out: `jsonb ? null` is NULL rather than false, and an
  -- `if` on NULL does not raise -- which is the shape a fail-open guard has.
  if v_record ->> 'conflict_kind' is null
     or not (v_recon -> 'conflict_kinds' ? (v_record ->> 'conflict_kind')) then
    raise exception 'j102_item_conflict_kind_unregistered: % is not a conflict this slice files; the registered kinds are %',
      coalesce(v_record ->> 'conflict_kind', 'no kind at all'),
      v_recon -> 'conflict_kinds' using errcode = '22023';
  end if;

  -- AND THE CLASS OF A FIELD IS THE REGISTRY'S ANSWER ABOUT THAT FIELD. Both
  -- sides are checked: a lying `concurrent_edits` entry is as misleading to the
  -- person resolving the conflict as a lying incoming one. An unregistered field
  -- must carry JSON null -- policy has said nothing about it, and `routine` is
  -- the one label that would matter, so silence is recorded as silence.
  foreach v_side in array array['incoming_edits', 'concurrent_edits'] loop
    if jsonb_typeof(v_record -> v_side) is distinct from 'array' then
      raise exception 'j102_item_edits_not_an_array: % carries % where both sides of the conflict should be lists',
        v_side, coalesce(jsonb_typeof(v_record -> v_side), 'nothing') using errcode = '22023';
    end if;
    for v_edit in select value from jsonb_array_elements(v_record -> v_side) loop
      v_field := v_edit ->> 'field';
      if v_field is null then
        raise exception 'j102_item_edit_names_no_field: an edit in % names no field'
          , v_side using errcode = '22023';
      end if;
      v_registered := coalesce(v_recon -> 'field_class_registry' -> v_field, 'null'::jsonb);
      if coalesce(v_edit -> 'field_class', 'null'::jsonb) is distinct from v_registered then
        raise exception 'j102_item_field_class_mismatch: %.% is classified % by policy and this item labels it %',
          v_side, v_field, v_registered,
          coalesce(v_edit -> 'field_class', 'null'::jsonb) using errcode = '42501';
      end if;
    end loop;
  end loop;

  v_kind := v_record ->> 'subject_kind';
  v_id := v_record ->> 'subject_id';
  if v_kind is null or v_id is null then
    raise exception 'j102_item_subject_missing: a conflict item names the subject it is about'
      using errcode = '22023';
  end if;
  v_key := v_kind || ':' || v_id;
  -- ONE SUBJECT, AND ONLY THAT SUBJECT. An extra operand would be a row locked
  -- and compared and never consulted, reported on the receipt as though it had
  -- been part of the answer.
  select count(*) into v_operand_keys from jsonb_object_keys(p_expected_state_digests);
  if not (p_expected_state_digests ? v_key) or v_operand_keys <> 1 then
    raise exception 'j102_item_operand_set_mismatch: this conflict is about % and its operand map must name exactly that subject; it names %',
      v_key,
      coalesce((select jsonb_agg(k order by k)
                  from jsonb_object_keys(p_expected_state_digests) as k), '[]'::jsonb)
      using errcode = '22023';
  end if;

  -- TIER 2, in the same order the transition and initialization writers take it.
  perform pg_advisory_xact_lock(hashtextextended(
    'j102:subject:' || ops.f01_tenant() || ':' || v_key, 0));

  select c.envelope -> 'record' -> 'state' into v_stored_state
    from ops.j102_subject_current c
   where c.tenant = ops.f01_tenant() and c.subject_kind = v_kind and c.subject_id = v_id;
  if not found then
    raise exception 'j102_reconciliation_subject_not_found: % holds no committed row, so there is no version for a conflict to be about',
      v_key using errcode = '22023';
  end if;
  v_stored := ops.f01_digest_jsonb(v_stored_state);
  v_expected := p_expected_state_digests ->> v_key;
  if v_stored is distinct from v_expected then
    raise exception 'j102_stale_subject_digest: the current state of % is %, and the caller decided against %',
      v_key, v_stored, coalesce(v_expected, 'absent') using errcode = '40001';
  end if;

  -- THE THREE STALENESS BINDINGS. Everything above proves the request is
  -- internally consistent and that the row has not moved since the caller read
  -- it. These prove that what the ITEM SAYS about the current version is true of
  -- the row at the instant it is filed.
  if (v_record ->> 'current_version_digest') is distinct from v_stored then
    raise exception 'j102_reconciliation_current_version_not_current: the item files % as the current version of %, and the committed row is %',
      coalesce(v_record ->> 'current_version_digest', 'nothing'), v_key, v_stored
      using errcode = '40001';
  end if;
  v_evidence := v_record -> 'concurrent_change_evidence';
  if jsonb_typeof(v_evidence) is distinct from 'object' then
    raise exception 'j102_reconciliation_evidence_missing: a visible conflict carries the evidence a person resolves it from'
      using errcode = '22023';
  end if;
  if ops.f01_digest_jsonb(v_evidence -> 'current_state') is distinct from v_stored then
    raise exception 'j102_reconciliation_state_evidence_stale: the state this item shows as current does not hash to the version it names'
      using errcode = '40001';
  end if;
  select e.event_digest into v_newest from ops.j102_subject_event e
   where e.tenant = ops.f01_tenant() and e.subject_kind = v_kind and e.subject_id = v_id
   order by e.event_seq desc limit 1;
  v_tail := coalesce(v_evidence -> 'history_tail', '[]'::jsonb);
  if jsonb_typeof(v_tail) is distinct from 'array' then
    raise exception 'j102_reconciliation_evidence_missing: the history evidence is % where a list should be',
      jsonb_typeof(v_tail) using errcode = '22023';
  end if;
  if v_newest is null then
    if jsonb_array_length(v_tail) <> 0 then
      raise exception 'j102_reconciliation_history_evidence_stale: % has no committed history and this item shows % rows of it',
        v_key, jsonb_array_length(v_tail) using errcode = '40001';
    end if;
  elsif jsonb_array_length(v_tail) = 0
        or (v_tail -> (jsonb_array_length(v_tail) - 1) ->> 'record_digest')
             is distinct from v_newest then
    raise exception 'j102_reconciliation_history_evidence_stale: the newest committed event for % is %, and this item''s history evidence ends at %',
      v_key, v_newest,
      coalesce(v_tail -> (jsonb_array_length(v_tail) - 1) ->> 'record_digest', 'nothing')
      using errcode = '40001';
  end if;

  -- AND THERE MUST BE A CONFLICT. A base equal to the current version is a caller
  -- that has not been overtaken by anybody.
  if (v_record ->> 'base_version_digest') is not distinct from v_stored then
    raise exception 'j102_reconciliation_without_conflict: % is at % and the caller decided against the same version; there is nothing to reconcile',
      v_key, v_stored using errcode = '22023';
  end if;

  insert into ops.j102_reconciliation_item
    (tenant, subject_kind, subject_id, conflict_kind, base_version_digest,
     current_version_digest, envelope, envelope_digest, item_digest, proposed_by, recorded_at)
  values (
    ops.f01_tenant(), v_kind, v_id,
    v_record ->> 'conflict_kind', v_record ->> 'base_version_digest',
    v_record ->> 'current_version_digest', p_envelope, ops.f01_digest_jsonb(p_envelope),
    v_digest, v_actor, v_txn_now)
  returning item_seq into v_seq;

  v_result := jsonb_build_object(
    'operation', v_operation,
    'decision', 'reconcile',
    'outcome', 'recorded',
    'actor_slug', v_actor,
    'actor_authorization_class', v_class,
    'subject_kind', v_kind,
    'subject_id', v_id,
    'item_seq', v_seq,
    'item_digest', v_digest,
    -- STORED FACTS, read back off what was just inserted rather than echoed from
    -- the diagnostics.
    'conflict_kind', v_record ->> 'conflict_kind',
    'base_version_digest', v_record ->> 'base_version_digest',
    'current_version_digest', v_record ->> 'current_version_digest',
    'expected_state_digests', p_expected_state_digests,
    -- WHAT THIS WRITER ENFORCED, as facts rather than hedges.
    'current_version_bound_to_committed_row', true,
    'state_evidence_bound_to_committed_row', true,
    'history_evidence_bound_to_committed_history', true,
    'conflict_present', true,
    -- THE TWO LABELS, CHECKED RATHER THAN CARRIED. Both are derived on the
    -- shipped path and re-derived here against the admission map, so a direct
    -- caller's label is not what this receipt is reporting.
    'conflict_kind_registered', true,
    'field_classes_bound_to_policy_registry', true,
    'visible', true,
    'applied', false,
    'resolved_by_machine', false,
    'advances_lifecycle_state', false,
    -- AND WHAT IT DOES NOT DO: it collapses no distinct proposal. Two different
    -- edit sets against the same two versions carry different request digests and
    -- both land, because both are real conflicts.
    'distinct_proposals_collapsed', false,
    'committed_at', v_txn_now_text,
    'caller_reported_reason_id', p_diagnostics ->> 'reason_id',
    'caller_reported_reason_id_scope',
      'kernel_result_diagnostic_asserted_by_the_caller_and_not_recomputed_here',
    'request_digest', p_request_digest,
    'request_digest_scope', 'caller_supplied_intent_digest_not_recomputed_here',
    'committed_content_digest', ops.f01_digest_jsonb(jsonb_build_object(
      'item_digest', v_digest,
      'item_seq', v_seq,
      'committed_at', v_txn_now_text)),
    'committed_content_digest_source', 'recomputed_from_committed_rows',
    'readback', jsonb_build_object('item_seq', v_seq, 'record', v_record),
    'external_effects', false);
  return ops.j102_settle_idempotency(v_operation, p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_reconciliation_item(jsonb,jsonb,text,text,jsonb) is
  'Q103: append one VISIBLE, unresolved conflict item, under the same governance every other writer here carries. It claims its idempotency key before reading any state, so a replay returns the stored outcome and the same key over different bytes refuses; it locks the subject in the established tier-2 order and compare-and-swaps it; and it then binds what the item SAYS to what the row IS at commit time -- the current version digest, the state snapshot the item shows and the newest row of its history evidence must all match the committed subject and its committed history, so a reading that went stale between the caller''s read and this insert cannot be filed as a current fact. A base equal to the current version is refused as no conflict at all. It collapses no distinct proposal: idempotency is keyed on the request, which covers the edits, and there is deliberately no unique index over the version pair. IT ALSO REFUSES THE TWO LABELS A DIRECT carr_writer CALLER COULD OTHERWISE INVENT: conflict_kind must be one of the four kinds evaluateConcurrentEdit files, and every edit on BOTH sides must carry the class the policy registry gives that field -- JSON null for a field policy has not classified -- both read out of ops.j102_admission_policy() rather than restated here. visible, applied and resolved_by_machine are compared as JSON BOOLEANS, so a string "false" is not accepted where the boolean is meant. The ungoverned single-argument form is DROPPED rather than shadowed.';

-- ---------------------------------------------------------------------------
-- Q081 -- the compatibility projection and the migration shadow.
--
-- READ-ONLY, AND DERIVED ON EVERY READ. The legacy shape is reconstructed from
-- the current records; it stores nothing of its own, so there is no second home
-- to drift. It is a function rather than a view precisely so that nobody can
-- write through it.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_compatibility_view(p_assignment_id text, p_deal_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_assignment jsonb; v_deal jsonb; v_phase text; v_source text; v_deal_state text;
begin
  v_assignment := case when p_assignment_id is null then null
                    else ops.j102_subject('assignment', p_assignment_id) end;
  v_deal := case when p_deal_id is null then null else ops.j102_subject('deal', p_deal_id) end;
  if v_assignment is null and v_deal is null then
    raise exception 'j102_compatibility_subject_required: name an assignment, a deal, or both'
      using errcode = '22023';
  end if;
  v_deal_state := v_deal -> 'state' ->> 'deal_state';
  if v_deal is not null and v_deal_state <> 'cancelled' then
    v_source := 'deal';
    v_phase := case
      when (v_deal -> 'state' ->> 'closing_state') = 'closed' then 'closing'
      when (v_deal -> 'state' ->> 'diligence_state') = 'in_progress' then 'due_diligence'
      when (v_deal -> 'state' ->> 'execution_state') = 'executed' then 'legal'
      else 'negotiation' end;
  elsif v_assignment is not null then
    v_source := 'assignment';
    v_phase := case (v_assignment -> 'state' ->> 'assignment_phase')
      when 'research' then 'research'
      when 'search' then 'site_selection'
      when 'concluded' then 'closing'
      else 'negotiation' end;
  end if;
  return jsonb_build_object(
    'legacy_phase', v_phase,
    'legacy_phase_source', v_source,
    'legacy_closed', coalesce(v_deal_state = 'closed', false),
    'legacy_outcome', case v_deal_state
      when 'closed' then 'won' when 'cancelled' then 'lost'
      when 'pending' then 'open' else null end,
    'assignment_id', p_assignment_id,
    'deal_id', p_deal_id,
    -- The four properties that keep this a view rather than a second home.
    'authoritative', false,
    'writable', false,
    'derived_from_current_records', true,
    'retires_any_caller', false);
end;
$$;

comment on function ops.j102_compatibility_view(text,text) is
  'Q081: the old single-phase shape, PROJECTED from the current records on every read for callers that have not migrated. Not authoritative, not writable, and it retires nobody.';

-- ---------------------------------------------------------------------------
-- Q081 -- THE MIGRATION SHADOW, RUN FOR REAL.
--
-- OLD AND NEW SIDE BY SIDE, AND NEITHER IS TOUCHED. ops.j102_run_migration_shadow
-- reads every legacy public.deal row, resolves it to the J102 assignment and deal
-- the EXISTING Salesforce reference links name (public.deal.salesforce_id =
-- j102_salesforce_reference.opportunity_id, newest link per subject kind), projects
-- the old single-phase shape through ops.j102_compatibility_view, and compares
-- phase, closed and outcome field by field -- the same three fields, the same
-- rule, as the kernel's compareMigrationShadow. It then appends ONE immutable,
-- digest-bound run record. It repairs nothing: a difference is reported for a
-- person, never written back to either side, because a shadow that could correct
-- what it compares could only ever prove itself.
--
-- WHAT MAKES A RUN CLEAN, and why an unlinked row spoils it. A run is clean only
-- when it compared at least one row, found no difference, and left NO legacy row
-- unlinked. An unlinked row is a row nobody has migrated; excluding it would
-- report "clean" over exactly the rows that were never tested.
--
-- OWNER RULING (f), 2026-09-25: THE RUN IS BOUND TO A SNAPSHOT AND CHECKS BOTH
-- DIRECTIONS. ops.j102_migration_shadow_snapshot() digests the legacy input
-- (every public.deal row the comparison reads) and the projection input (every
-- Salesforce reference to an assignment or deal, with that subject's current
-- state digest). The run records both, and refuses (40001) if either moved while
-- it ran. Readiness RECOMPUTES both: a clean run over a snapshot that has since
-- moved is stale and no longer proof. Two further faults spoil a run:
--   * MANY-TO-ONE: two or more legacy rows resolve to the same J102 subject pair,
--     so the old world counts one deal twice where the new world has one.
--   * A SUBJECT WITH NO LEGACY ROW: a J102 assignment or deal carries Salesforce
--     references and not one of them names a legacy row. A subject with no
--     Salesforce reference at all was born in J102 and has no legacy counterpart
--     to compare; the caller census, not the shadow, answers for callers blind to it.
--
-- TWO NORMALIZATIONS, both stated. The legacy `closed` fact is `closed_on is not
-- null` (the legacy table has no flag). A legacy NULL outcome and a projected NULL
-- outcome are both read as 'open' -- "no outcome recorded yet" -- so a pending
-- deal is not reported as differing merely because the old column was empty.
-- The legacy 'paused' outcome has no J102 counterpart and is reported as a
-- difference whenever it appears, which is correct: it is a real disagreement a
-- person has to resolve.
-- ---------------------------------------------------------------------------
create table if not exists ops.j102_migration_shadow_run (
  tenant            text not null,
  run_seq           bigserial primary key,
  compared_rows     integer not null check (compared_rows >= 0),
  matching_rows     integer not null check (matching_rows >= 0),
  differing_rows    integer not null check (differing_rows >= 0),
  unlinked_rows     integer not null check (unlinked_rows >= 0),
  many_to_one_subjects        integer not null check (many_to_one_subjects >= 0),
  subjects_without_legacy_row integer not null check (subjects_without_legacy_row >= 0),
  legacy_snapshot_digest      text not null,
  projection_snapshot_digest  text not null,
  clean             boolean not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  run_digest        text not null,
  recorded_by       text not null,
  recorded_at       timestamptz not null,
  idempotency_key   text not null,
  constraint j102_shadow_run_tenant check (tenant = ops.f01_tenant()),
  constraint j102_shadow_run_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_shadow_run_digest_bound
    check (run_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_shadow_run_counts_add_up
    check (compared_rows = matching_rows + differing_rows),
  constraint j102_shadow_run_clean_is_derived
    check (clean = (compared_rows > 0 and differing_rows = 0 and unlinked_rows = 0
                    and many_to_one_subjects = 0 and subjects_without_legacy_row = 0)),
  constraint j102_shadow_run_matches_envelope
    check ((envelope -> 'record' ->> 'compared_rows')::integer = compared_rows
       and (envelope -> 'record' ->> 'differing_rows')::integer = differing_rows
       and (envelope -> 'record' ->> 'unlinked_rows')::integer = unlinked_rows
       and (envelope -> 'record' ->> 'many_to_one_subjects')::integer = many_to_one_subjects
       and (envelope -> 'record' ->> 'subjects_without_legacy_row')::integer = subjects_without_legacy_row
       and envelope -> 'record' ->> 'legacy_snapshot_digest' = legacy_snapshot_digest
       and envelope -> 'record' ->> 'projection_snapshot_digest' = projection_snapshot_digest
       and (envelope -> 'record' ->> 'clean')::boolean = clean
       and envelope -> 'record' ->> 'recorded_by' = recorded_by)
);

drop trigger if exists j102_migration_shadow_run_dml_guard on ops.j102_migration_shadow_run;
create trigger j102_migration_shadow_run_dml_guard before insert or update
  on ops.j102_migration_shadow_run for each row execute function ops.j102_guard_direct_dml();
drop trigger if exists j102_migration_shadow_run_no_truncate on ops.j102_migration_shadow_run;
create trigger j102_migration_shadow_run_no_truncate before truncate
  on ops.j102_migration_shadow_run execute function ops.j102_guard_no_truncate();
drop trigger if exists j102_migration_shadow_run_append_only on ops.j102_migration_shadow_run;
create trigger j102_migration_shadow_run_append_only before update or delete
  on ops.j102_migration_shadow_run for each row execute function ops.j102_guard_append_only();

create or replace function ops.j102_migration_shadow_snapshot()
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
    'legacy_snapshot_digest', ops.f01_digest_jsonb(coalesce((
      select jsonb_agg(jsonb_build_array(d.id::text, d.salesforce_id, d.phase, d.outcome,
                                         d.closed_on::text) order by d.id)
        from public.deal d), '[]'::jsonb)),
    'projection_snapshot_digest', ops.f01_digest_jsonb(coalesce((
      select jsonb_agg(jsonb_build_array(r.reference_seq, r.opportunity_id,
                                         r.linked_subject_kind, r.linked_subject_id,
                                         s.state_digest) order by r.reference_seq)
        from ops.j102_salesforce_reference r
        left join ops.j102_subject_current s
          on s.tenant = r.tenant and s.subject_kind = r.linked_subject_kind
         and s.subject_id = r.linked_subject_id
       where r.tenant = ops.f01_tenant()
         and r.linked_subject_kind in ('assignment', 'deal')), '[]'::jsonb)));
$$;

comment on function ops.j102_migration_shadow_snapshot() is
  'Q081 / owner ruling (f): the digests of both inputs the migration shadow reads. A run records them; readiness recomputes them, so a clean run over a moved snapshot is stale.';

create or replace function ops.j102_run_migration_shadow(
  p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_rows jsonb := '[]'::jsonb; v_row jsonb; v_view jsonb;
  v_legacy record; v_assignment text; v_deal text; v_differences jsonb;
  v_legacy_closed boolean; v_legacy_outcome text; v_projected_outcome text;
  v_compared integer := 0; v_matching integer := 0; v_differing integer := 0;
  v_unlinked integer := 0; v_record jsonb; v_envelope jsonb; v_digest text;
  v_seq bigint; v_result jsonb; v_clean boolean;
  v_snapshot jsonb; v_many jsonb; v_orphans jsonb;
begin
  v_replay := ops.j102_claim_idempotency('run-migration-shadow', p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_snapshot := ops.j102_migration_shadow_snapshot();
  for v_legacy in
    select d.id::text as legacy_row_id, d.salesforce_id, d.phase, d.outcome, d.closed_on
      from public.deal d
     order by d.id
  loop
    v_assignment := null; v_deal := null;
    if v_legacy.salesforce_id is not null then
      select r.linked_subject_id into v_assignment
        from ops.j102_salesforce_reference r
       where r.tenant = ops.f01_tenant() and r.opportunity_id = v_legacy.salesforce_id
         and r.linked_subject_kind = 'assignment'
         and exists (select 1 from ops.j102_subject_current s
                      where s.tenant = r.tenant and s.subject_kind = 'assignment'
                        and s.subject_id = r.linked_subject_id)
       order by r.reference_seq desc limit 1;
      select r.linked_subject_id into v_deal
        from ops.j102_salesforce_reference r
       where r.tenant = ops.f01_tenant() and r.opportunity_id = v_legacy.salesforce_id
         and r.linked_subject_kind = 'deal'
         and exists (select 1 from ops.j102_subject_current s
                      where s.tenant = r.tenant and s.subject_kind = 'deal'
                        and s.subject_id = r.linked_subject_id)
       order by r.reference_seq desc limit 1;
    end if;
    if v_assignment is null and v_deal is null then
      v_unlinked := v_unlinked + 1;
      v_rows := v_rows || jsonb_build_array(jsonb_build_object(
        'legacy_row_id', v_legacy.legacy_row_id, 'status', 'unlinked',
        'why', case when v_legacy.salesforce_id is null
                 then 'the legacy row carries no Salesforce id, so no reference can link it'
                 else 'no Salesforce reference links this opportunity to an existing assignment or deal' end));
      continue;
    end if;
    v_view := ops.j102_compatibility_view(v_assignment, v_deal);
    v_legacy_closed := v_legacy.closed_on is not null;
    v_legacy_outcome := coalesce(v_legacy.outcome, 'open');
    v_projected_outcome := coalesce(v_view ->> 'legacy_outcome', 'open');
    v_differences := '[]'::jsonb;
    if v_legacy.phase is distinct from (v_view ->> 'legacy_phase') then
      v_differences := v_differences || jsonb_build_array(jsonb_build_object(
        'field', 'phase', 'legacy', v_legacy.phase, 'projected', v_view -> 'legacy_phase'));
    end if;
    if v_legacy_closed is distinct from (v_view ->> 'legacy_closed')::boolean then
      v_differences := v_differences || jsonb_build_array(jsonb_build_object(
        'field', 'closed', 'legacy', v_legacy_closed, 'projected', v_view -> 'legacy_closed'));
    end if;
    if v_legacy_outcome is distinct from v_projected_outcome then
      v_differences := v_differences || jsonb_build_array(jsonb_build_object(
        'field', 'outcome', 'legacy', v_legacy_outcome, 'projected', v_projected_outcome));
    end if;
    v_compared := v_compared + 1;
    if jsonb_array_length(v_differences) = 0 then
      v_matching := v_matching + 1;
    else
      v_differing := v_differing + 1;
    end if;
    v_rows := v_rows || jsonb_build_array(jsonb_build_object(
      'legacy_row_id', v_legacy.legacy_row_id,
      'status', case when jsonb_array_length(v_differences) = 0 then 'matching' else 'differing' end,
      'assignment_id', v_assignment, 'deal_id', v_deal,
      'differences', v_differences));
  end loop;
  -- MANY-TO-ONE: legacy rows that resolve to the same (assignment, deal) pair.
  select coalesce(jsonb_agg(jsonb_build_object(
           'assignment_id', g.assignment_id, 'deal_id', g.deal_id,
           'legacy_row_ids', g.ids) order by g.assignment_id, g.deal_id), '[]'::jsonb)
    into v_many
    from (select r ->> 'assignment_id' as assignment_id, r ->> 'deal_id' as deal_id,
                 jsonb_agg(r ->> 'legacy_row_id' order by r ->> 'legacy_row_id') as ids
            from jsonb_array_elements(v_rows) r
           where r ->> 'status' <> 'unlinked'
           group by 1, 2 having count(*) > 1) g;
  -- A SUBJECT WITH NO LEGACY ROW: it carries Salesforce references, and none
  -- of them names an opportunity any legacy row holds.
  select coalesce(jsonb_agg(jsonb_build_object(
           'subject_kind', x.linked_subject_kind, 'subject_id', x.linked_subject_id,
           'opportunity_ids', x.opps) order by x.linked_subject_kind, x.linked_subject_id), '[]'::jsonb)
    into v_orphans
    from (select r.linked_subject_kind, r.linked_subject_id,
                 jsonb_agg(distinct r.opportunity_id) as opps
            from ops.j102_salesforce_reference r
           where r.tenant = ops.f01_tenant() and r.linked_subject_kind in ('assignment', 'deal')
             and exists (select 1 from ops.j102_subject_current s
                          where s.tenant = r.tenant and s.subject_kind = r.linked_subject_kind
                            and s.subject_id = r.linked_subject_id)
           group by 1, 2
          having not bool_or(exists (select 1 from public.deal d
                                      where d.salesforce_id = r.opportunity_id))) x;
  -- THE SNAPSHOT HELD: nothing either side reads moved while this ran.
  if ops.j102_migration_shadow_snapshot() is distinct from v_snapshot then
    raise exception 'j102_shadow_snapshot_moved: a legacy row or a linked lifecycle subject changed while the shadow ran; run it again'
      using errcode = '40001';
  end if;
  v_clean := v_compared > 0 and v_differing = 0 and v_unlinked = 0
             and jsonb_array_length(v_many) = 0 and jsonb_array_length(v_orphans) = 0;
  v_record := jsonb_build_object(
    'record_kind', 'stored_migration_shadow_run',
    'tenant', ops.f01_tenant(),
    'compared_rows', v_compared, 'matching_rows', v_matching,
    'differing_rows', v_differing, 'unlinked_rows', v_unlinked,
    'many_to_one_subjects', jsonb_array_length(v_many),
    'subjects_without_legacy_row', jsonb_array_length(v_orphans),
    'legacy_snapshot_digest', v_snapshot ->> 'legacy_snapshot_digest',
    'projection_snapshot_digest', v_snapshot ->> 'projection_snapshot_digest',
    'clean', v_clean,
    'rows', v_rows,
    'many_to_one', v_many,
    'subjects_without_legacy_rows', v_orphans,
    'normalizations', jsonb_build_array(
      'legacy closed = closed_on is not null',
      'a null outcome on either side reads as open'),
    'legacy_rows_modified', 0, 'projection_modified', false,
    'retires_any_caller', false,
    'recorded_by', v_actor, 'recorded_at', v_txn_now_text);
  v_digest := ops.f01_digest_jsonb(v_record);
  v_envelope := jsonb_build_object(
    'record_kind', 'stored_migration_shadow_run', 'record', v_record, 'record_digest', v_digest);
  insert into ops.j102_migration_shadow_run
    (tenant, compared_rows, matching_rows, differing_rows, unlinked_rows,
     many_to_one_subjects, subjects_without_legacy_row,
     legacy_snapshot_digest, projection_snapshot_digest, clean,
     envelope, envelope_digest, run_digest, recorded_by, recorded_at, idempotency_key)
  values (ops.f01_tenant(), v_compared, v_matching, v_differing, v_unlinked,
     jsonb_array_length(v_many), jsonb_array_length(v_orphans),
     v_snapshot ->> 'legacy_snapshot_digest', v_snapshot ->> 'projection_snapshot_digest', v_clean,
     v_envelope, ops.f01_digest_jsonb(v_envelope), v_digest, v_actor, v_txn_now, p_idempotency_key)
  returning run_seq into v_seq;
  v_result := jsonb_build_object(
    'operation', 'run-migration-shadow', 'decision', 'allow',
    'reason_id', case when v_clean then 'shadow_run_clean'
                      when v_compared = 0 and v_unlinked = 0 and jsonb_array_length(v_orphans) = 0
                        then 'shadow_run_found_no_legacy_rows'
                      else 'shadow_run_found_differences_or_unlinked_rows' end,
    'actor_slug', v_actor, 'run_seq', v_seq, 'run_digest', v_digest,
    'compared_rows', v_compared, 'matching_rows', v_matching,
    'differing_rows', v_differing, 'unlinked_rows', v_unlinked,
    'many_to_one_subjects', jsonb_array_length(v_many),
    'subjects_without_legacy_row', jsonb_array_length(v_orphans),
    'legacy_snapshot_digest', v_snapshot ->> 'legacy_snapshot_digest',
    'projection_snapshot_digest', v_snapshot ->> 'projection_snapshot_digest',
    'clean', v_clean,
    'committed_at', v_txn_now_text,
    'legacy_rows_modified', 0, 'retires_any_caller', false, 'external_effects', false);
  return ops.j102_settle_idempotency('run-migration-shadow', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_run_migration_shadow(text,text) is
  'Q081: compare every legacy public.deal row with the J102 projection its Salesforce reference links, and append one immutable digest-bound run record bound to the snapshot of both inputs. Modifies neither side and retires nobody; an unlinked row, a many-to-one mapping or a referenced subject with no legacy row keeps the run from being clean.';

create or replace function ops.j102_migration_readiness()
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_run ops.j102_migration_shadow_run%rowtype; v_latest jsonb; v_missing jsonb;
  v_now_snapshot jsonb; v_current boolean := false; v_proof boolean := false;
begin
  select * into v_run from ops.j102_migration_shadow_run
   where tenant = ops.f01_tenant() order by run_seq desc limit 1;
  if found then
    perform ops.j102_verify_envelope(v_run.envelope, v_run.envelope_digest, v_run.run_digest,
                                     'stored_migration_shadow_run');
    -- A RUN IS PROOF ONLY OF THE SNAPSHOT IT READ. Recomputed here, now.
    v_now_snapshot := ops.j102_migration_shadow_snapshot();
    v_current := (v_now_snapshot ->> 'legacy_snapshot_digest') = v_run.legacy_snapshot_digest
             and (v_now_snapshot ->> 'projection_snapshot_digest') = v_run.projection_snapshot_digest;
    v_proof := v_run.clean and v_current;
    v_latest := jsonb_build_object(
      'run_seq', v_run.run_seq, 'run_digest', v_run.run_digest,
      'recorded_at', ops.f01_instant_text(v_run.recorded_at), 'recorded_by', v_run.recorded_by,
      'compared_rows', v_run.compared_rows, 'matching_rows', v_run.matching_rows,
      'differing_rows', v_run.differing_rows, 'unlinked_rows', v_run.unlinked_rows,
      'many_to_one_subjects', v_run.many_to_one_subjects,
      'subjects_without_legacy_row', v_run.subjects_without_legacy_row,
      'legacy_snapshot_digest', v_run.legacy_snapshot_digest,
      'projection_snapshot_digest', v_run.projection_snapshot_digest,
      'snapshot_current', v_current,
      'clean', v_run.clean);
  end if;
  v_missing := jsonb_build_array(jsonb_build_object(
    'fact', 'exact_caller_census',
    'why', 'Q081 permits retiring the old interface only after migration proof, and no verified enumeration of the callers and projections still reading the legacy shape exists in this record layer.',
    'produced_by', 'not_produced_by_this_slice'));
  if not v_proof then
    v_missing := v_missing || jsonb_build_array(jsonb_build_object(
      'fact', 'shadow_comparison_clean_run',
      'why', case when v_latest is null
               then 'No migration shadow has been run; ops.j102_run_migration_shadow produces one.'
               when not v_run.clean
               then 'The newest migration shadow run is not clean: it found differences, unlinked legacy rows, many-to-one mappings or referenced subjects with no legacy row, which a person must resolve before it counts as proof.'
               else 'The newest migration shadow run was clean over a snapshot that has since moved; run it again.' end,
      'produced_by', 'ops.j102_run_migration_shadow'));
  end if;
  return jsonb_build_object(
    'decision', 'refuse',
    'reason_id', 'caller_census_absent',
    'may_retire_callers', false,
    'migration_complete', false,
    'caller_census_verified', false,
    'shadow_comparison_clean_run', v_proof,
    'latest_shadow_run', v_latest,
    'compatibility_view_available', true,
    'big_bang_rename', false,
    'missing_facts', v_missing);
end;
$$;

comment on function ops.j102_migration_readiness() is
  'Q081: whether the old interface may be retired. It answers no while no verified caller census exists, whatever the shadow says; it reports the newest verified shadow run and names each fact still missing.';

-- ---------------------------------------------------------------------------
-- The read door.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_read(p_kind text, p_selector jsonb default '{}'::jsonb)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_actor text := ops.f01_context_actor_slug(); v_body jsonb;
begin
  if p_kind = 'subject' then
    v_body := ops.j102_subject(p_selector ->> 'subject_kind', p_selector ->> 'subject_id');
  elsif p_kind = 'subject_events' then
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select e.event_seq as seq,
                   ops.j102_verify_envelope(e.envelope, e.envelope_digest, e.event_digest,
                                            'stored_lifecycle_event') as verified
              from ops.j102_subject_event e
             where e.tenant = ops.f01_tenant()
               and e.subject_kind = p_selector ->> 'subject_kind'
               and e.subject_id = p_selector ->> 'subject_id') s;
  elsif p_kind = 'first_party_record' then
    v_body := ops.j102_first_party_record(p_selector ->> 'record_kind', p_selector ->> 'record_id');
  elsif p_kind = 'evidence_subject_links' then
    -- The associations one subject holds, so a reviewer can see WHICH documents
    -- a deal's transitions were entitled to rest on without having to ask the
    -- writer.
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select l.link_seq as seq,
                   ops.j102_verify_envelope(l.envelope, l.envelope_digest, l.link_digest,
                                            'stored_evidence_subject_link') as verified
              from ops.j102_evidence_subject_link l
             where l.tenant = ops.f01_tenant()
               and l.subject_kind = p_selector ->> 'subject_kind'
               and l.subject_id = p_selector ->> 'subject_id') s;
  elsif p_kind = 'salesforce_references' then
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select r.reference_seq as seq,
                   ops.j102_verify_envelope(r.envelope, r.envelope_digest, r.reference_digest,
                                            'stored_salesforce_reference') as verified
              from ops.j102_salesforce_reference r
             where r.tenant = ops.f01_tenant()
               and (p_selector ->> 'opportunity_id' is null
                    or r.opportunity_id = p_selector ->> 'opportunity_id')) s;
  elsif p_kind = 'correction_receipts' then
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select c.receipt_seq as seq,
                   ops.j102_verify_envelope(c.envelope, c.envelope_digest, c.receipt_digest,
                                            'stored_correction_receipt') as verified
              from ops.j102_correction_receipt c
             where c.tenant = ops.f01_tenant()
               and c.subject_kind = p_selector ->> 'subject_kind'
               and c.subject_id = p_selector ->> 'subject_id') s;
  elsif p_kind = 'reconciliation_items' then
    -- VERIFIED LIKE EVERY OTHER LIST HERE. This arm used to return the raw
    -- envelope record, the one read kind that recomputed nothing -- found on the
    -- first live read of a Q103 item (2026-09-25).
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select i.item_seq as seq,
                   ops.j102_verify_envelope(i.envelope, i.envelope_digest, i.item_digest,
                                            'stored_reconciliation_item') as verified
              from ops.j102_reconciliation_item i
             where i.tenant = ops.f01_tenant()
               and i.subject_kind = p_selector ->> 'subject_kind'
               and i.subject_id = p_selector ->> 'subject_id') s;
  elsif p_kind = 'compatibility_view' then
    v_body := ops.j102_compatibility_view(
      p_selector ->> 'assignment_id', p_selector ->> 'deal_id');
  elsif p_kind = 'migration_shadow' then
    v_body := ops.j102_migration_readiness();
  else
    raise exception 'j102_unknown_read_kind: %', p_kind using errcode = '22023';
  end if;
  return jsonb_build_object(
    'operation', 'read-cre-lifecycle',
    'kind', p_kind,
    'tenant', ops.f01_tenant(),
    'actor_slug', v_actor,
    'server_time', ops.f01_now_text(),
    'body', v_body,
    'integrity', 'recomputed_not_trusted');
end;
$$;

comment on function ops.j102_read(text,jsonb) is
  'The one read door for J102 records. Every envelope it returns has had both digests recomputed from committed bytes; it exposes no Salesforce-to-lifecycle mapping and no write path.';

-- ---------------------------------------------------------------------------
-- Grants. Reads reach the ordinary bundles. DIRECT INSERT IS GRANTED TO NOBODY:
-- every write goes through a definer function that derives its own actor, so a
-- writer holding a raw connection cannot attribute a row to someone else.
--
-- No role is created by this file. Every role named below already exists.
-- ---------------------------------------------------------------------------
grant select on ops.j102_subject_current, ops.j102_subject_event,
  ops.j102_first_party_record, ops.j102_evidence_subject_link,
  ops.j102_salesforce_reference,
  ops.j102_correction_receipt, ops.j102_reconciliation_item, ops.j102_migration_shadow_run
  to carr_reader, carr_writer, carr_authority;

revoke insert, update, delete, truncate on ops.j102_subject_current,
  ops.j102_subject_event, ops.j102_first_party_record, ops.j102_evidence_subject_link,
  ops.j102_salesforce_reference,
  ops.j102_correction_receipt, ops.j102_reconciliation_item, ops.j102_idempotency,
  ops.j102_migration_shadow_run
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function ops.j102_verify_envelope(jsonb,text,text,text),
  ops.j102_subject(text,text), ops.j102_first_party_record(text,text),
  ops.j102_evidence_subject_link(text,text,integer,text,text,text),
  ops.j102_compatibility_view(text,text), ops.j102_migration_readiness(),
  ops.j102_migration_shadow_snapshot(), ops.j102_sponsoring_partner(),
  ops.j102_read(text,jsonb), ops.j102_admission_policy(),
  ops.j102_expected_value(jsonb,jsonb,jsonb,jsonb,jsonb,jsonb),
  ops.j102_recheck_evidence(jsonb,text,text,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.j102_verify_envelope(jsonb,text,text,text),
  ops.j102_subject(text,text), ops.j102_first_party_record(text,text),
  ops.j102_evidence_subject_link(text,text,integer,text,text,text),
  ops.j102_compatibility_view(text,text), ops.j102_migration_readiness(),
  ops.j102_migration_shadow_snapshot(), ops.j102_sponsoring_partner(),
  ops.j102_read(text,jsonb),
  -- THE ADMISSION MAP IS READABLE, deliberately. It confers nothing: it decides
  -- no request, it is IMMUTABLE, it takes no argument, and everything in it is
  -- already exported from the kernel to anyone holding the source. A caller that
  -- can read which transition it is entitled to perform gets a better refusal;
  -- one that cannot still gets refused.
  ops.j102_admission_policy()
  to carr_reader, carr_writer, carr_jobs, carr_authority;
-- The evidence recheck is reachable only from the transition writer that owns
-- it. Exposing it would let a caller establish "the evidence was still exact"
-- outside the transaction that holds the locks making that statement true --
-- and, now that the recheck takes the primary subject as a parameter, would let
-- a caller name whichever subject made its own manifest verify.
revoke all on function ops.j102_recheck_evidence(jsonb,text,text,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

-- The effect interpreter is likewise the writer's, and for a plainer reason: it
-- is a pure calculator over values a caller would have to already hold, so
-- exposing it would confer nothing and would still add a callable surface with
-- no purpose. It is granted to nobody and called only from the writer that owns
-- it.
revoke all on function ops.j102_expected_value(jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

-- THE PRIVATE APPROVAL READER IS GRANTED TO NOBODY, for the reason its own
-- comment gives: a callable stub is the first step toward a configurable one.
revoke all on function ops.j102_typed_approval(text,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function
  ops.j102_replay_outcome(text,text,text), ops.j102_claim_idempotency(text,text,text),
  ops.j102_settle_idempotency(text,text,jsonb),
  ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb),
  ops.j102_initialize_subject(text,jsonb,jsonb,jsonb,text,text,jsonb),
  ops.j102_record_first_party_fact(jsonb,text,text),
  ops.j102_record_evidence_subject_link(jsonb,text,text),
  ops.j102_record_salesforce_reference(jsonb,text,text),
  ops.j102_record_correction(jsonb,text,text),
  ops.j102_record_reconciliation_item(jsonb,jsonb,text,text,jsonb),
  ops.j102_run_migration_shadow(text,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function
  ops.j102_replay_outcome(text,text,text),
  ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb),
  -- The initialization writer reaches the same two bundles as the transition
  -- writer: the three initializations currently admit both actor classes, and
  -- the function checks the DERIVED class itself. Sponsored-agent parity is the
  -- owner's ruling (c) of 2026-09-25; see the store's V5_J102_OPEN_OWNER_QUESTIONS.
  ops.j102_initialize_subject(text,jsonb,jsonb,jsonb,text,text,jsonb),
  ops.j102_record_first_party_fact(jsonb,text,text),
  ops.j102_record_salesforce_reference(jsonb,text,text),
  ops.j102_record_reconciliation_item(jsonb,jsonb,text,text,jsonb),
  -- The shadow runner reads legacy rows and appends a comparison; it changes no
  -- business record, so both write bundles may run it.
  ops.j102_run_migration_shadow(text,text)
  to carr_writer, carr_authority;
-- Correction and the evidence association reach the authority bundle only, and
-- each additionally checks the DERIVED PRINCIPAL inside the function: the grant
-- says who may call it, the check says who may succeed.
grant execute on function ops.j102_record_correction(jsonb,text,text),
  ops.j102_record_evidence_subject_link(jsonb,text,text)
  to carr_authority;

-- ---------------------------------------------------------------------------
-- The direct-DML guard's schema assumption, asserted rather than assumed.
--
-- The guard matches an OPTIONALLY ops-qualified frame, because a genuine ops
-- writer prints unqualified under the guard's own search_path. That is only safe
-- while each unqualified writer NAME resolves to the ops function under exactly
-- that search_path; if something else shadowed one, an unqualified frame from
-- the impostor would satisfy the pattern. This block refuses to leave the file
-- in that state.
-- ---------------------------------------------------------------------------
do $$
declare v_name text; v_signature text;
begin
  perform set_config('search_path', 'pg_catalog, ops, public', true);
  foreach v_signature in array array[
    'j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb)',
    'j102_initialize_subject(text,jsonb,jsonb,jsonb,text,text,jsonb)',
    'j102_record_first_party_fact(jsonb,text,text)',
    'j102_record_evidence_subject_link(jsonb,text,text)',
    'j102_record_salesforce_reference(jsonb,text,text)',
    'j102_record_correction(jsonb,text,text)',
    'j102_record_reconciliation_item(jsonb,jsonb,text,text,jsonb)',
    'j102_run_migration_shadow(text,text)',
    'j102_claim_idempotency(text,text,text)',
    'j102_settle_idempotency(text,text,jsonb)'
  ] loop
    v_name := to_regprocedure(v_signature)::text;
    if to_regprocedure(v_signature) is distinct from to_regprocedure('ops.' || v_signature) then
      raise exception 'j102_writer_name_shadowed: the unqualified name % resolves to %, not to the ops writer; the direct-DML guard cannot rely on an unqualified frame',
        v_signature, coalesce(v_name, 'nothing');
    end if;
  end loop;
end $$;
