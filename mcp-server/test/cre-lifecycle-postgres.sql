-- DoctorCRE v5 slice V5-J102: transaction-scoped PostgreSQL proof for the CRE
-- lifecycle rail.
--
-- THIS FILE HAS NOT BEEN EXECUTED. It is delivered as source alongside
-- ops/cre-lifecycle.candidate.sql, which is itself an unnumbered candidate that
-- has not been applied to any database. Nothing in this slice runs either file,
-- there is no new PostgreSQL gate executable and no inventory entry point, and
-- running this file is a separate act on a disposable database. Read every
-- assertion below as "what this fixture WOULD prove when run", never as a result.
--
-- EVERY FIXTURE ROW IS ROLLED BACK. No lifecycle subject, event, business
-- record, Salesforce reference or correction receipt survives running it, and
-- the last group asserts the tables are empty again before the rollback.
--
-- EVERY RECORD IS SYNTHETIC. Every client, assignment, property, deal, document
-- and reason below is unmistakably test data. Nothing here is a claim about a
-- real CARR client or a real transaction, and nothing here is a proposal of one.
--
-- ===========================================================================
-- THE TWO DOORS, STATED FIRST BECAUSE THEY DETERMINE WHAT EVERY GROUP BELOW
-- PROVES.
--
-- ops.j102_apply_transition STILL REFUSES TO CREATE ITS OWN PRIMARY SUBJECT
-- (`j102_primary_subject_creation_refused`), because a created primary has no
-- committed row for the transition's own prerequisites, instrument kind and
-- prior-state conditions to be checked against. An earlier revision of this
-- fixture opened an assignment that way and called it a positive walk; it was a
-- bypass with a bootstrap's name on it, and the receipt's
-- `prerequisites_checked: false` was an honest description of a check that never
-- ran. Group P0 below proves that refusal is still in force, and it is the group
-- to read first: the arrival of a creation door must not have reopened it.
--
-- ops.j102_initialize_subject IS THE CREATION DOOR, and it is a different
-- function with a different half of the admission map. It creates exactly one
-- subject of one of three kinds -- a prospect relationship, an assignment under
-- an already ACTIVE engagement held by a CLIENT, a property negotiation under a
-- still-open assignment -- each in the earliest declared state of its kind, in a
-- shape the map fixes field by field, with its parents re-read and
-- compare-and-swapped under its own locks. It performs no transition and checks
-- no transition prerequisite, so it skips none.
--
-- WHAT THIS FIXTURE IS THEREFORE WRITTEN TO PROVE, AND THE PREVIOUS REVISION
-- COULD NOT: a POSITIVE WALK from a legitimate first row through the existing
-- evidence-bound transitions (group J), beside the refusals that were always the
-- reason the file exists — and, because that walk yields a committed assignment,
-- the target and event-detail checks (U1, U2) and the context-resolution checks
-- (J4) that were previously unreachable. Group I is the creation door's own
-- refusal matrix, and it is the one to read beside P0, because a creation path is
-- the obvious place to smuggle a later state past the evidence meant to establish
-- it.
--
-- "WRITTEN TO PROVE" IS THE EXACT CLAIM. NOTHING HERE HAS BEEN RUN. Every
-- sentence in this file describes what an execution WOULD establish, and until
-- somebody executes it against a disposable database none of it is evidence.
--
-- WHAT THE POSITIVE WALK STILL NEEDS, and what happens when it is absent. Q077
-- admits Client status on a signed effective ETL or an approved equivalent, and
-- the approval reader is private and always raises -- so the walk needs one real
-- F01 DOCUMENT. This file creates a synthetic engagement letter through F01's own
-- writer when it can, and SKIPS the rest of the walk with a notice naming the
-- exact reason when it cannot: a session that is not a verified partner (the
-- evidence association is partner-only), or a database without the four-argument
-- ops.f01_record_document. It invents no document, no approval and no seed verb,
-- and it creates no role.
--
-- THIS FILE CANNOT SEED A ROW DIRECTLY EITHER, and does not try: every J102
-- relation carries ops.j102_guard_direct_dml, which requires the write to arrive
-- through a registered ops.j102_* writer and refuses a raw INSERT from this DO
-- block regardless of who owns the table. B7 proves that refusal rather than
-- working around it.
-- ===========================================================================
--
-- WHAT THIS FIXTURE PROVES THAT THE NODE SUITE CANNOT. The Node tests run
-- against a scripted fake handle, which can prove which statements the store
-- issues, in what order, and with which parameters -- and nothing at all about
-- what PostgreSQL does with them. These are the properties that only a real
-- engine can answer:
--
--   S1  the append-only triggers, the direct-DML guard and the no-truncate
--       guard exist on exactly the intended relations
--   S2  direct INSERT is granted to nobody, and the private approval reader is
--       executable by none of the runtime role bundles
--   S3  the one-pending-Deal-per-Assignment partial index exists with the
--       intended predicate (Q078/Q095 structurally)
--   S4  BLOCK-2 and H5 structurally: a first-party record cannot be stored
--       without a typed subject binding and an author class, the four
--       partner-only record kinds cannot be stored with any other author class,
--       and the evidence->subject association relation exists with its pin-exact
--       unique index
--   S5  BLOCK-1 and both root corrections structurally: the three partner-only
--       transitions are partner-only in the database's own admission map, the
--       map carries an exact target and an exact event set for every transition,
--       and the writer really does consult all of it. Runs in every session,
--       including one with no principal.
--   S6  the admission map declares NO transition that creates its own primary
--       subject, read off the installed policy rather than off this file's prose.
--       (That property used to be called `j102_fixture_bootstrap_absent`, a name
--       that also meant "this fixture cannot obtain a committed assignment". The
--       second half is now false, so the label is retired rather than reused: one
--       name for two propositions, one of them stale, is how a stale reason
--       survives an edit.)
--   S7  HIGH-5 and HIGH-6 structurally: every event in the map declares its whole
--       nested payload and no derived kind, the writer carries every provenance
--       and citation refusal, the citations are compared against what the RECHECK
--       read, the receipt derives its coupled facts and decision refs instead of
--       echoing them, and both history relations carry the CHECK constraints that
--       are the floor beneath all of it. Runs in every session.
--   S8  the creation door is a SEPARATE function with its own half of the map,
--       and it does not call the transition writer. Runs in every session.
--   S9  Q103's conflict writer is governed and the ungoverned single-argument
--       overload is GONE: the right signature, the right grants, the idempotency
--       and lock calls, every refusal by name, the reconciliation vocabulary in
--       the admission map, the relation's own conflict-kind, subject-kind and
--       boolean-flag constraints, and NO unique index over the version pair.
--       Runs in every session -- and it is STRUCTURAL ONLY. It reads signatures,
--       grants, constraints and function text; it drives nothing. Every claim
--       about how that writer BEHAVES belongs to group R below, which runs only
--       inside the walk.
--   B5  an idempotent replay returns the committed outcome, and the same key
--       over a different payload refuses (proved on the first-party record
--       writer, which is the one writer this fixture can drive to a success)
--   B6  the append-only relations refuse UPDATE and DELETE for real
--   B7  direct DML outside a registered writer refuses for real
--   B9  a closing_settlement record with no actual closing date cannot be
--       stored at all (Q094 structurally)
--   B10 the private approval reader raises, so no approval can be manufactured
--       one layer down either
--   B16 H5: whichever half this session can prove -- a sponsored agent cannot
--       author a closing_settlement, or a partner-authored one carries its
--       author class on the row
--
-- AND THE TWELVE ADVERSARIAL GROUPS, which are the reason this revision exists.
-- Every one of them is a DIRECT call on ops.j102_apply_transition, holding
-- nothing more than the carr_writer EXECUTE grant. Every one of them SUCCEEDED
-- against some earlier revision of the writer:
--
--   P0  the primary-subject creation refusal, still in force in the transition
--       writer now that a creation door exists beside it.
--   A1  BLOCKER-2's reproducer INCLUDING the obvious fix's defeat -- the wrong
--       subject's evidence, with that subject added to the lock set so that a
--       membership check would pass.
--   A2  the primary-creation masquerade: a transition proposing to CREATE the
--       subject it claims to advance, which is how the previous revision was
--       seeded and which is now refused by name.
--   A3  Diagnostics naming a weaker operation to obtain a stronger transition,
--       and a transition that does not exist at all.
--   A4  The direct carr_writer bypass of a partner-only transition, behavioural
--       wherever this session is a sponsored agent.
--   A5  HIGH-1 and the event set in full: an event lying about its own bytes, an
--       event claiming a different transition, an event for an unrelated
--       subject, an EMPTY event array, an EXTRA event, and a plausible WRONG
--       KIND on a correctly bound subject.
--   A6  An empty manifest, an unregistered evidence kind, and real evidence
--       presented from the wrong source.
--   A7  An unrelated proposed subject, and two subjects of one kind.
--   A8  A coupled subject proposed as a CREATION by a transition that only ever
--       updates it.
--   A9  A coupled write sent as a SUBSET: the primary subject alone, with the
--       coupled subject the same transition must move left out.
--   A10 The whole coupled set present, and the EVENT set short by one.
--   A11 HIGH-5: a FULLY CANONICAL call -- real evidence, correct binding, correct
--       event, correct target -- whose subject envelope claims to have been
--       established by a DIFFERENT transition, or by no transition at all; a
--       second unchecked claim hashed into the same authoritative bytes; a
--       foreign stored-subject schema; a header naming a subject its own state
--       does not describe; and the same two shape defects on the history side.
--       PROVED behaviourally: all of them refuse before any state is read.
--   A12 M-3: a compare-and-swap operand map that is SQL NULL, and one that is a
--       JSON scalar. Both used to slip three named refusals in silence or raise
--       an unnamed cast error; both now name the attempt. PROVED behaviourally.
--   U1  The target-value rewrites -- a permitted field carrying an unpermitted
--       value, and a permitted field DELETED rather than moved. Both are decided
--       against the COMMITTED row, so until a creation door existed they refused
--       earlier, at the compare-and-swap, for want of an assignment, and reported
--       themselves unproven. THEY NOW RUN INSIDE GROUP J against the assignment
--       the walk legitimately creates and opens, and prove the target check.
--   U2  HIGH-6 and the nested-payload half, moved for the same reason: an event
--       of the right kind, on the right subject, in the right number, naming an
--       evidence reference the transition never rested on; a record citing
--       another assignment's authentic mandate; and a record citing nothing at
--       all. All three are decided against the evidence re-read under the lock
--       AND against the committed prior row, and all three now fire.
--
-- TWO PREREQUISITES, checked before anything is attempted, each SKIPPING with a
-- notice rather than failing -- the same shape work-portfolio-postgres.sql and
-- benchmark-acceptance-postgres.sql use:
--   * ops/cre-lifecycle.candidate.sql must have been applied here.
--   * domain.sql must have been applied here (the J102 rail calls its tenant,
--     canonicaliser, digest, instant parser, clock and principal rather than
--     restating any of them).
--   * The BEHAVIOURAL groups additionally need a session running as an admitted
--     principal, because every writer derives its actor through
--     ops.f01_context_actor_slug() and refuses any other session_user. This file
--     CREATES NO ROLE and grants nothing: minting one would manufacture the
--     identity the whole rail exists to derive. The STRUCTURAL groups need no
--     principal and run either way.
--
-- AND THE THREE GROUPS THIS REVISION ADDS:
--
--   S8  the admission map's INITIALIZATION half, structurally: three creations,
--       each with a fixed creation shape and a single event, none of them
--       requiring evidence, none of them creating an engagement or a deal, and
--       the writer that carries every one of its refusals. Runs in every session.
--   I1  the creation door's own refusal matrix, behaviourally: an unknown
--       initialization, a TRANSITION id offered to it, an operation that does not
--       perform it, a non-null operand (which is an update wearing a creation's
--       clothes), a relationship born a CLIENT, an assignment born under no
--       engagement, a forged provenance, and an event citing evidence a creation
--       cannot have rested on.
--   J   THE POSITIVE WALK, from a legitimate first row through the existing
--       evidence-bound transitions: a prospect is created, a synthetic F01
--       engagement letter is recorded and associated with it, the ETL transition
--       makes it a Client with its Engagement, an assignment is created under
--       that engagement and OPENED on its own mandate record, and a property
--       negotiation is created under the open assignment. Every prerequisite is
--       genuinely satisfied rather than skipped, and the group ends by proving
--       that the transitions afterwards still refuse without their evidence.
--   J4  The negative context cases the walk makes reachable: the CHAINED hop
--       resolving the relationship from the engagement's own field rather than
--       from whichever relationship a caller supplies, and the operand set closed
--       by IDENTITY rather than by kind. Both are direct calls on the creation
--       writer against real, locked, unmoved rows of the right kind. J4 also
--       NAMES the one context arm that is still unreachable here and why.
--   R   Q103's CONFLICT WRITER, BEHAVIOURALLY (R1-R15), and it lives inside the
--       walk for the reason U1 and U2 do: every property it asserts is a property
--       of what happens under the lock against a COMMITTED subject with a real
--       history. The conflict lands visible and unresolved; a retry replays and
--       writes nothing; the same key over different bytes refuses; a SECOND,
--       DIFFERENT proposal against the same two versions lands beside the first
--       rather than collapsing into it; a stale operand, a stale "current"
--       version inside the item, a stale state snapshot and stale history
--       evidence are each refused at the write boundary; an item may not arrive
--       already resolved or attributed to somebody else; an extra operand is
--       refused; and the two LABELS are refused as well -- a conflict kind no
--       evaluator emits, a lifecycle field labelled `routine`, an unclassified
--       field carrying a class, and a resolution flag that is a STRING where the
--       boolean is meant. WHEN THE WALK SKIPS, NONE OF THIS RUNS, and the closing
--       notice says so in those words rather than leaving R out of the list.
--
-- WHERE GROUP J RUNS IN FULL, since that is not the configuration every database
-- has: it needs a session running as a VERIFIED PARTNER (the evidence -> subject
-- association is partner-only) and either overload of ops.f01_record_document --
-- the six-argument writer from ops/document-derivative-registration.candidate.sql
-- is used when present, and domain.sql's four-argument form otherwise. Each
-- absent prerequisite is named in its own skip notice; nothing is invented to get
-- past one, and an F01 call that FAILS is a failure rather than a skip.
--
-- WHAT THIS FILE DOES NOT PROVE, named rather than implied:
--   * The walk beyond the LOI draft. Submitting an LOI, recording a counterparty
--     acceptance, executing a lease and recording a commission all rest on
--     further F01 documents or corporate artifacts, and this fixture creates one
--     document rather than a library of them. Those transitions are proved by the
--     Node kernel suite against loaded evidence, and by the refusals here.
--   * ANYTHING AT ALL FROM GROUP J WHEN IT SKIPS. A green run is not evidence
--     that the walk ran: J3 onward sits behind a prerequisite check, and the
--     row-count assertions at the foot compare against counters the walk itself
--     increments, so they pass identically when the walk stops at the prospect.
--     Read the 'THE POSITIVE WALK RAN IN FULL' notice, not the exit status. When
--     it is absent, U1, U2 and J4 did not run either.
--   * `j102_required_context_not_met` -- the arm enforcing engagement_state and
--     relationship_state on a LOCKED row. No shipped door writes a non-active
--     engagement or a non-client relationship, so no case here can drive it; J4
--     names that rather than leaving it to be inferred, and the Node parity
--     suite covers the arm against the kernel instead.
--   * That ops.f01_digest_jsonb and artifact-trust.js's digest() agree on the
--     same record. Both sides are asserted to hash the canonical JSON, and the
--     SQL side reuses F01's canonicaliser, which domain.sql already reconciles
--     against the module -- but a rollback-only fixture cannot execute
--     JavaScript, so the cross-language equality remains live-integration
--     verification. It is the first thing to check when this rail is exercised
--     end to end.
--   * That the lock ordering in ops.j102_apply_transition prevents deadlock
--     under real concurrency. A single-session fixture cannot contend with
--     itself; the ordering is argued in that function's own comment and would
--     need a two-session harness to demonstrate.
--   * That two CONCURRENT creations of the same subject id resolve to one winner
--     and one refusal. Both sessions take the same tier-2 advisory lock on that
--     key before either reads, so the loser's null operand meets the winner's
--     committed row -- but one session cannot contend with itself.
--   * Anything about a real client, a real deal or a real Salesforce record.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_actor            text;
  v_sponsor          text;
  v_class            text;
  v_now              text;
  v_behavioural      boolean := false;
  v_partner          boolean := false;
  v_role             text;
  v_predicate        text;
  v_count            bigint;
  v_result           jsonb;
  v_replay           jsonb;
  v_fact             jsonb;
  v_fact_digest      text;
  v_fact2_digest     text;
  v_policy           jsonb;
  v_transition       text;
  v_rec              jsonb;
  v_rec2             jsonb;
  v_evt              jsonb;
  v_evt2             jsonb;
  v_subjects         jsonb;
  v_events           jsonb;
  v_manifest         jsonb;
  v_manifest_wrong   jsonb;
  v_refusal          text;

  v_tenant           constant text := 'carr-internal';
  v_placeholder      constant text := 'sha256:' || repeat('0', 64);
  v_lie              constant text := 'sha256:' || repeat('3', 64);
  v_env_schema       constant text := 'doctorcre-v5-j102-stored-record-envelope.v1';
  v_subject_schema   constant text := 'doctorcre-v5-j102-stored-lifecycle-subject.v1';
  v_event_schema     constant text := 'doctorcre-v5-j102-stored-lifecycle-event.v1';
  v_fact_schema      constant text := 'doctorcre-v5-j102-stored-first-party-record.v1';
  v_ev_schema        constant text := 'doctorcre-v5-j102-lifecycle-event.v1';
  v_assignment_id    constant text := 'j102-fixture-assignment-1';
  v_assignment_id_2  constant text := 'j102-fixture-assignment-2';
  v_engagement_id    constant text := 'j102-fixture-engagement-1';
  v_negotiation_id   constant text := 'j102-fixture-negotiation-1';
  v_property_id      constant text := 'j102-fixture-property-1';
  v_deal_id          constant text := 'j102-fixture-deal-1';
  -- The unrelated deal an adversarial event names. Nothing ever creates it, and
  -- the whole point of A5 is that naming it must not create it either.
  v_deal_id_c        constant text := 'j102-fixture-deal-c';
  v_fact_id          constant text := 'j102-fixture-fact-1';
  v_fact_id_2        constant text := 'j102-fixture-fact-2';

  -- The walk's own identifiers, kept apart from the adversarial ones above so
  -- that a row the walk creates can never satisfy a group that is supposed to
  -- refuse for want of one.
  v_walk_rel_id      constant text := 'j102-fixture-walk-relationship-1';
  -- Two more prospects, created legitimately, so the negative context and operand
  -- cases have REAL rows of the right kind to point at rather than placeholder
  -- digests that would refuse at the compare-and-swap for the wrong reason.
  v_walk_rel_id_2    constant text := 'j102-fixture-walk-relationship-2';
  v_walk_rel_id_3    constant text := 'j102-fixture-walk-relationship-3';
  v_walk_eng_id      constant text := 'j102-fixture-walk-engagement-1';
  v_walk_asg_id      constant text := 'j102-fixture-walk-assignment-1';
  v_walk_asg_id_2    constant text := 'j102-fixture-walk-assignment-2';
  v_walk_neg_id      constant text := 'j102-fixture-walk-negotiation-1';
  v_walk_prop_id     constant text := 'j102-fixture-walk-property-1';
  v_walk_doc_id      constant text := 'j102-fixture-walk-etl-1';
  v_walk_fact_id     constant text := 'j102-fixture-walk-mandate-1';
  v_walk_doc_digest  constant text := 'sha256:' || repeat('7a', 32);
  v_walk_subjects    integer := 0;
  v_walk_events      integer := 0;
  v_walk_ok          boolean := false;
  v_subject_env      jsonb;
  v_event_env        jsonb;
  v_doc_record       jsonb;
  v_doc_envelope     jsonb;
  v_prov_record      jsonb;
  v_link_digest      text;
  v_rel_digest       text;
  v_eng_digest       text;
  v_asg_digest       text;
  v_fact3_digest     text;
  v_state            jsonb;
  v_init_def         text;
  v_recon_def        text;
  v_item             jsonb;
  v_item_env         jsonb;
  v_newest_event     text;
  v_base_digest      text;

  -- One synthetic Assignment in the shape `open-assignment` would leave it. It is
  -- never committed to the database -- nothing can commit it -- and exists here so
  -- the adversarial payloads are otherwise well formed.
  v_assignment_state constant jsonb := jsonb_build_object(
    'subject_kind', 'assignment',
    'subject_id', v_assignment_id,
    'engagement_id', v_engagement_id,
    'assignment_phase', 'search',
    'open_negotiation_count', 2,
    'selected_property_id', null,
    'active_lease_draft_target_id', null,
    'pending_deal_id', null,
    'multi_target_exception_ref', null);

  -- The committed-assignment masquerade U1 proposes is built INSIDE group J now,
  -- against the walk's own assignment id and its real compare-and-swap digest,
  -- because that is the only shape in which the target check can actually answer.
  -- A constant here would have to name the adversarial assignment, which has no
  -- committed row on purpose.

  v_negotiation_state constant jsonb := jsonb_build_object(
    'subject_kind', 'property_negotiation',
    'subject_id', v_negotiation_id,
    'assignment_id', v_assignment_id,
    'property_id', v_property_id,
    'negotiation_state', 'loi_submitted');
begin
  -- === prerequisites =======================================================
  if to_regprocedure('ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb)') is null then
    raise notice 'SKIPPED: ops.j102_apply_transition is absent; ops/cre-lifecycle.candidate.sql has not been applied here yet.';
    return;
  end if;
  if to_regprocedure('ops.f01_digest_jsonb(jsonb)') is null
     or to_regprocedure('ops.f01_context_actor_slug()') is null then
    raise notice 'SKIPPED: domain.sql is absent; the J102 rail reuses its digest, tenant, clock and principal.';
    return;
  end if;

  -- Is this session an admitted principal? A definer function cannot be talked
  -- into one, and this file creates none, so the behavioural groups are simply
  -- skipped where the answer is no.
  begin
    v_actor := ops.f01_context_actor_slug();
    v_sponsor := ops.j102_sponsoring_partner();
    v_class := ops.f01_principal() ->> 'authorization_class';
    v_now := ops.f01_now_text();
    v_behavioural := true;
    v_partner := v_class = 'verified_partner';
  exception when others then
    raise notice 'PARTIAL: session_user % is not an admitted J102 principal; the structural groups run and the behavioural groups are skipped. No role is created here.',
      session_user;
  end;

  -- === S1: the guards exist on exactly the intended relations ==============
  foreach v_role in array array[
    'j102_subject_current', 'j102_subject_event', 'j102_first_party_record',
    'j102_salesforce_reference', 'j102_correction_receipt', 'j102_reconciliation_item',
    'j102_idempotency'
  ] loop
    if not exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_role
         and t.tgname = v_role || '_dml_guard' and not t.tgisinternal) then
      raise exception 'S1: ops.% carries no direct-DML guard', v_role;
    end if;
    if not exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_role
         and t.tgname = v_role || '_no_truncate' and not t.tgisinternal) then
      raise exception 'S1: ops.% carries no truncate guard', v_role;
    end if;
  end loop;

  -- The five history relations refuse UPDATE and DELETE; the two mutable ones
  -- deliberately do not, and the fixture asserts BOTH halves so a future edit
  -- that made current state append-only, or history mutable, is visible.
  foreach v_role in array array[
    'j102_subject_event', 'j102_first_party_record', 'j102_salesforce_reference',
    'j102_correction_receipt', 'j102_reconciliation_item'
  ] loop
    if not exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_role
         and t.tgname = v_role || '_append_only' and not t.tgisinternal) then
      raise exception 'S1: ops.% is history and carries no append-only guard', v_role;
    end if;
  end loop;
  foreach v_role in array array['j102_subject_current', 'j102_idempotency'] loop
    if exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_role
         and t.tgname = v_role || '_append_only' and not t.tgisinternal) then
      raise exception 'S1: ops.% is current state and must remain updatable through its writer', v_role;
    end if;
  end loop;

  -- === S2: nobody holds direct DML, and the approval reader is private ======
  for v_role in select unnest(array['carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority']) loop
    if not exists (select 1 from pg_roles where rolname = v_role) then
      continue;  -- a database without the full bundle set still proves the rest
    end if;
    if has_table_privilege(v_role, 'ops.j102_subject_current', 'INSERT')
       or has_table_privilege(v_role, 'ops.j102_subject_event', 'INSERT')
       or has_table_privilege(v_role, 'ops.j102_first_party_record', 'INSERT')
       or has_table_privilege(v_role, 'ops.j102_correction_receipt', 'INSERT') then
      raise exception 'S2: % holds direct INSERT; every write must go through a definer writer that derives its own actor',
        v_role;
    end if;
    if has_function_privilege(v_role, 'ops.j102_typed_approval(text,text)', 'EXECUTE') then
      raise exception 'S2: % can execute the private approval reader; a callable stub is the first step toward a configurable one',
        v_role;
    end if;
    -- The effect interpreter is the writer's own; a callable one confers nothing
    -- and is still a surface.
    if to_regprocedure('ops.j102_expected_value(jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)') is not null
       and has_function_privilege(v_role,
             'ops.j102_expected_value(jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)', 'EXECUTE') then
      raise exception 'S2: % can execute the transition-effect interpreter, which is private to the writer',
        v_role;
    end if;
    -- The correction writer reaches the AUTHORITY bundle only.
    if v_role in ('carr_reader', 'carr_writer', 'carr_jobs')
       and has_function_privilege(v_role, 'ops.j102_record_correction(jsonb,text,text)', 'EXECUTE') then
      raise exception 'S2: % can execute the correction writer, which is partner authority only', v_role;
    end if;
  end loop;

  -- === S3: the one-pending-Deal-per-Assignment index =======================
  select pg_get_expr(i.indpred, i.indrelid) into v_predicate
    from pg_index i join pg_class c on c.oid = i.indexrelid
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'ops' and c.relname = 'j102_one_pending_deal_per_assignment';
  if v_predicate is null then
    raise exception 'S3: the one-pending-deal-per-assignment index is absent';
  end if;
  if v_predicate !~ 'deal_state' or v_predicate !~ 'pending' then
    raise exception 'S3: the pending-deal index predicate does not name the pending deal state: %', v_predicate;
  end if;
  -- CANCELLED AND CLOSED DEALS MUST BE OUTSIDE THE INDEX, or Q096 breaks: an
  -- assignment that lost one deal could never hold another.
  if exists (select 1 from pg_index i join pg_class c on c.oid = i.indexrelid
               join pg_namespace n on n.oid = c.relnamespace
              where n.nspname = 'ops' and c.relname = 'j102_one_pending_deal_per_assignment'
                and i.indpred is null) then
    raise exception 'S3: the pending-deal index is not partial, so a cancelled deal would block a new one';
  end if;

  -- === S4: the binding and the author class are structural =================
  -- BLOCK-2. A business record that cannot say WHICH subject it is about is the
  -- defect; NOT NULL on both columns is what makes it unstorable rather than
  -- merely discouraged.
  foreach v_role in array array['bound_subject_kind', 'bound_subject_id', 'recorded_by_class'] loop
    if not exists (
      select 1 from information_schema.columns
       where table_schema = 'ops' and table_name = 'j102_first_party_record'
         and column_name = v_role and is_nullable = 'NO') then
      raise exception 'S4: ops.j102_first_party_record has no non-null % column; an unbound or unattributed business record could be stored',
        v_role;
    end if;
  end loop;
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_first_party_record'
       and co.conname = 'j102_fact_binding_matches_envelope') then
    raise exception 'S4: the subject binding is not CHECK-bound to the hashed envelope, so it could drift from the bytes it describes';
  end if;
  -- H5. The four partner-only facts cannot be stored with an agent author, so an
  -- agent-authored closing date does not exist to be laundered later.
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_first_party_record'
       and co.conname = 'j102_fact_partner_authored_kinds') then
    raise exception 'S4: a sponsored agent could author a closing_settlement, winning_property_commitment, deal_failure or lifecycle_correction record';
  end if;
  -- The association relation, and its PIN-EXACT unique index. A unique index over
  -- the document id alone would let one association follow a document across
  -- versions, which is exactly what a pin exists to prevent.
  if to_regclass('ops.j102_evidence_subject_link') is null then
    raise exception 'S4: the evidence->subject association relation is absent, so document evidence could not be bound to a subject at all';
  end if;
  select pg_get_indexdef(c.oid) into v_predicate
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'ops' and c.relname = 'j102_evidence_subject_link_uq';
  if v_predicate is null then
    raise exception 'S4: the association relation has no unique index';
  end if;
  foreach v_role in array array['evidence_ref', 'version_no', 'content_digest',
                                'subject_kind', 'subject_id'] loop
    if v_predicate !~ v_role then
      raise exception 'S4: the association unique index does not bind %; an association that is not pin-exact would follow a document across versions',
        v_role;
    end if;
  end loop;
  -- An association is not a document and asserts nothing about document state.
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_evidence_subject_link'
       and co.conname = 'j102_link_asserts_no_document_state') then
    raise exception 'S4: an association could claim to create a document or assert its state, which is F01''s authority and not this rail''s';
  end if;
  -- M3: the correction writer decides authority from the DERIVED principal, not
  -- from a role-name literal sitting beside it as a second identity source.
  if pg_get_functiondef(to_regprocedure('ops.j102_record_correction(jsonb,text,text)')) ~ 'carr_authority_joe' then
    raise exception 'M3: the correction writer still names a role literal; authority has one derivation, ops.f01_principal()';
  end if;

  -- === S5: the admission map exists, is complete, and the writer consults it ==
  --
  -- This group needs no principal and runs in every session, which matters: a
  -- session running AS a verified partner cannot demonstrate the agent refusal
  -- behaviourally, and this is what covers that direction there.
  if to_regprocedure('ops.j102_admission_policy()') is null then
    raise exception 'S5: the SQL admission map is absent, so the transition writer has nothing to admit against and every transition is performable by any grant holder';
  end if;
  v_policy := ops.j102_admission_policy();
  foreach v_role in array array[
    'commit-winning-property', 'record-deal-closing', 'cancel-pending-deal'
  ] loop
    if (v_policy -> 'transitions' -> v_role -> 'permitted_actor_classes')
         is distinct from '["verified_partner"]'::jsonb then
      raise exception 'S5: % is not partner-only in the SQL admission map, so a sponsored agent holding the carr_writer EXECUTE grant could perform it on a direct call',
        v_role;
    end if;
  end loop;
  -- ROOT BLOCKER 2, structurally: every transition declares a subject rule set
  -- with an EXACT target for every field it moves, and an EXACT event set. A map
  -- that named the fields without naming the values would admit
  -- `assignment_phase: "committed"` from a routine open-assignment call.
  for v_transition in select * from jsonb_object_keys(v_policy -> 'transitions') loop
    if jsonb_typeof(v_policy -> 'transitions' -> v_transition -> 'subjects') is distinct from 'object' then
      raise exception 'S5: % declares which fields it may move and not what it must move them TO', v_transition;
    end if;
    if jsonb_typeof(v_policy -> 'transitions' -> v_transition -> 'events') is distinct from 'array'
       or jsonb_array_length(v_policy -> 'transitions' -> v_transition -> 'events') < 1 then
      raise exception 'S5: % declares no event set, so a call could append any history it liked', v_transition;
    end if;
    for v_role in select * from jsonb_object_keys(
      v_policy -> 'transitions' -> v_transition -> 'subjects') loop
      if (v_policy -> 'transitions' -> v_transition -> 'subjects' -> v_role ->> 'mode') = 'update'
         and jsonb_typeof(v_policy -> 'transitions' -> v_transition -> 'subjects' -> v_role -> 'effects')
               is distinct from 'object' then
        raise exception 'S5: % may write a % and declares no resulting value for any of its fields',
          v_transition, v_role;
      end if;
    end loop;
  end loop;
  -- The map is worth nothing if the writer does not consult it. These are the
  -- exact call sites, asserted from the installed definition rather than from the
  -- file on disk.
  v_predicate := pg_get_functiondef(
    to_regprocedure('ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb)'));
  foreach v_role in array array[
    'ops.j102_admission_policy()', 'ops.f01_principal()', 'ops.j102_expected_value(',
    'j102_actor_class_not_permitted', 'j102_operation_transition_mismatch',
    'j102_unknown_transition', 'j102_field_not_movable_by_transition',
    'j102_subject_kind_not_written_by_transition', 'j102_coupled_subject_not_in_chain',
    'j102_prerequisite_not_met', 'j102_event_subject_not_advanced',
    'j102_event_digest_mismatch',
    -- ROOT BLOCKER 1
    'j102_primary_subject_creation_refused', 'j102_subject_creation_not_permitted',
    'j102_primary_subject_not_found',
    -- ROOT BLOCKER 2
    'j102_required_subject_not_proposed', 'j102_prior_condition_not_met',
    'j102_transition_effect_not_canonical', 'j102_transition_effect_missing',
    'j102_created_subject_shape_mismatch', 'j102_created_subject_field_not_canonical',
    'j102_event_set_mismatch', 'j102_event_missing_or_wrong',
    'j102_event_not_produced_by_transition'
  ] loop
    if position(v_role in v_predicate) = 0 then
      raise exception 'S5: the transition writer does not carry %; the admission map exists and is not consulted', v_role;
    end if;
  end loop;
  -- BLOCK-2: the recheck is handed the primary subject rather than left to check
  -- a caller-supplied manifest against itself.
  if to_regprocedure('ops.j102_recheck_evidence(jsonb,text,text,text)') is null then
    raise exception 'S5: the evidence recheck does not take the primary subject, so a manifest bound to another subject cannot be caught';
  end if;
  if to_regprocedure('ops.j102_recheck_evidence(jsonb)') is not null then
    raise exception 'S5: the single-argument recheck still exists; it establishes "the evidence was exact" without being told which subject it was exact FOR';
  end if;

  -- === S6: NO TRANSITION CREATES ITS OWN PRIMARY SUBJECT ====================
  --
  -- Read off the installed policy rather than asserted in prose, and it matters
  -- MORE now that a creation door exists beside the transition writer: if any
  -- transition ever declares its PRIMARY subject creatable, the bypass this
  -- correction removed is back, and the walk group below would start passing for
  -- the wrong reason.
  for v_transition in select * from jsonb_object_keys(v_policy -> 'transitions') loop
    v_role := v_policy -> 'transitions' -> v_transition ->> 'subject_kind';
    if (v_policy -> 'transitions' -> v_transition -> 'subjects' -> v_role ->> 'mode')
         is distinct from 'update' then
      raise exception 'S6: % declares its primary % creatable; a created primary has no committed row for its own prerequisites to be checked against',
        v_transition, v_role;
    end if;
  end loop;
  if (v_policy ->> 'subject_creation') is distinct from 'coupled_only_never_the_primary_subject' then
    raise exception 'S6: the admission map does not declare that creation is coupled-only';
  end if;

  -- === S8: the INITIALIZATION half of the map, and its own writer ============
  --
  -- Needs no principal and runs in every session. It is the structural half of
  -- the creation door: the map declares three creations and nothing wider, each
  -- with an exact shape and a single event, and the writer that consults it
  -- carries every refusal that keeps a creation from being a state jump.
  if jsonb_typeof(v_policy -> 'initializations') is distinct from 'object' then
    raise exception 'S8: the admission map declares no initializations, so ops.j102_initialize_subject has nothing to admit against';
  end if;
  if to_regprocedure('ops.j102_initialize_subject(text,jsonb,jsonb,jsonb,text,text,jsonb)') is null then
    raise exception 'S8: the initialization writer is absent, so nothing creates the first row of a chain';
  end if;
  if (v_policy ->> 'initialization_requires_evidence') is distinct from 'false'
     or (v_policy ->> 'initialization_performs_transition') is distinct from 'false' then
    raise exception 'S8: the map does not declare that an initialization carries no evidence and performs no transition';
  end if;
  for v_transition in select * from jsonb_object_keys(v_policy -> 'initializations') loop
    v_rec := v_policy -> 'initializations' -> v_transition;
    v_role := v_rec ->> 'subject_kind';
    -- THE TWO KINDS A CREATION DOOR MAY NEVER MAKE. An engagement created outside
    -- establish-client-and-engagement is a Client with no coupled status change;
    -- a deal created outside commit-winning-property is Q078's overruled rule
    -- back again, a Deal with no commitment behind it.
    if v_role in ('engagement', 'deal') then
      raise exception 'S8: % creates a %, which is a COUPLED creation of a transition and may not have its own door',
        v_transition, v_role;
    end if;
    if jsonb_typeof(v_rec -> 'creation_shape') is distinct from 'object' then
      raise exception 'S8: % declares no creation shape, so a created row could carry any state it liked',
        v_transition;
    end if;
    if jsonb_typeof(v_rec -> 'event') is distinct from 'object'
       or jsonb_typeof(v_rec -> 'event' -> 'detail') is distinct from 'object' then
      raise exception 'S8: % declares no event or no nested detail, so a creation could append any history',
        v_transition;
    end if;
    if (v_rec ->> 'requires_evidence') is distinct from 'false' then
      raise exception 'S8: % claims to require evidence; no evidence in this rail can bind to a subject that does not exist yet',
        v_transition;
    end if;
    if jsonb_typeof(v_rec -> 'operations') is distinct from 'array'
       or jsonb_array_length(v_rec -> 'operations') < 1 then
      raise exception 'S8: % is reachable from no operation', v_transition;
    end if;
    -- A PARENT THAT IS NAMED MUST BE CHECKED. A reference with no prerequisite
    -- behind it is how an assignment ends up under a lapsed engagement.
    if (v_rec ->> 'parent_subject_kind') is not null
       and not exists (
         select 1 from jsonb_array_elements(coalesce(v_rec -> 'required_context', '[]'::jsonb)) as r
          where (r ->> 'subject') = (v_rec ->> 'parent_subject_kind')) then
      raise exception 'S8: % creates a row under a % it never checks',
        v_transition, v_rec ->> 'parent_subject_kind';
    end if;
    -- AND THE CREATED STATE IS THE EARLIEST ONE OF ITS KIND, which is the whole
    -- anti-bypass property: a created row may not arrive already advanced.
    if v_role = 'relationship'
       and (v_rec -> 'creation_shape' -> 'relationship_state' ->> 'value')
             is distinct from 'prospect' then
      raise exception 'S8: % creates a relationship that is not a prospect', v_transition;
    end if;
    if v_role = 'assignment'
       and (v_rec -> 'creation_shape' -> 'assignment_phase' ->> 'value')
             is distinct from 'research' then
      raise exception 'S8: % creates an assignment past the earliest phase, so open-assignment would no longer declare the scope',
        v_transition;
    end if;
    if v_role = 'property_negotiation'
       and (v_rec -> 'creation_shape' -> 'negotiation_state' ->> 'value')
             is distinct from 'loi_drafted' then
      raise exception 'S8: % creates a negotiation past the draft, so an LOI could exist without its document',
        v_transition;
    end if;
  end loop;
  -- The writer consults all of it, read off the installed definition. It is held
  -- in its OWN variable rather than in v_predicate, which S5 loaded with the
  -- transition writer's definition and S7 still reads.
  v_init_def := pg_get_functiondef(
    to_regprocedure('ops.j102_initialize_subject(text,jsonb,jsonb,jsonb,text,text,jsonb)'));
  foreach v_role in array array[
    'ops.j102_admission_policy()', 'ops.f01_principal()', 'ops.j102_expected_value(',
    'j102_unknown_initialization', 'j102_transition_is_not_an_initialization',
    'j102_operation_initialization_mismatch', 'j102_actor_class_not_permitted',
    'j102_initialization_is_not_an_update', 'j102_subject_already_exists',
    'j102_created_subject_shape_mismatch', 'j102_created_subject_field_not_canonical',
    'j102_required_context_not_locked', 'j102_required_context_not_met',
    'j102_operand_subject_not_read_by_initialization',
    'j102_subject_provenance_mismatch', 'j102_event_missing_or_wrong',
    'j102_event_detail_not_canonical', 'j102_initialization_cites_evidence',
    'j102_stale_subject_digest', 'pg_advisory_xact_lock'
  ] loop
    if position(v_role in v_init_def) = 0 then
      raise exception 'S8: the initialization writer does not carry %; a creation could be a state jump with a creation''s name on it',
        v_role;
    end if;
  end loop;
  -- AND IT IS NOT A SECOND TRANSITION WRITER. It never dispatches into the
  -- transition half of the map, so nothing it does can advance a committed row.
  if position('''initializations''' in v_init_def) = 0 then
    raise exception 'S8: the initialization writer does not read the initialization half of the admission map';
  end if;
  -- AND IT CALLS THE TRANSITION WRITER NOWHERE, which is a claim about CODE and
  -- not about text. The initialization writer NAMES ops.j102_apply_transition
  -- inside the refusal it raises when a caller offers a transition id to the
  -- creation door, so a bare search of pg_get_functiondef would fire on correct
  -- code -- and the two ways to quiet that are deleting the guard or rewording
  -- the refusal, each of which weakens a real control. The INVOCATION shape is
  -- what separates them: a call is the name followed by its argument list, and
  -- the message is the name followed by a comma.
  --
  -- The two controls below keep the pattern honest in both directions, because a
  -- pattern that matched nothing would make this check vacuous. They are pure
  -- string comparisons against literals written here; they read no relation.
  if not ('perform ops.j102_apply_transition(''open-assignment'', p_x)'
            ~ 'ops\.j102_apply_transition\s*\(') then
    raise exception 'S8: the invocation pattern would not catch a real call, so this check proves nothing';
  end if;
  if 'is performed by ops.j102_apply_transition, which creates no primary subject'
       ~ 'ops\.j102_apply_transition\s*\(' then
    raise exception 'S8: the invocation pattern is really a text search, and would fire on the refusal message';
  end if;
  if v_init_def ~ 'ops\.j102_apply_transition\s*\(' then
    raise exception 'S8: the initialization writer CALLS the transition writer; the two doors must stay separate';
  end if;

  -- === S9: the reconciliation writer is GOVERNED, and the old one is gone =====
  --
  -- Q103's conflict writer used to take an envelope and nothing else: no
  -- idempotency key, so a retry wrote a second visible item; no compare-and-swap
  -- operand; and no re-read of the subject, so an item could be filed claiming a
  -- "current" version that had stopped being current before the insert. This
  -- group is the structural half of the replacement, and the FIRST assertion is
  -- the one that matters: the ungoverned overload must not still be callable
  -- beside its successor.
  if to_regprocedure('ops.j102_record_reconciliation_item(jsonb)') is not null then
    raise exception 'S9: the ungoverned single-argument reconciliation writer still exists; create-or-replace left it callable beside the governed one, and anything holding its grant can still file an unkeyed, unbound conflict';
  end if;
  if to_regprocedure('ops.j102_record_reconciliation_item(jsonb,jsonb,text,text,jsonb)') is null then
    raise exception 'S9: the governed reconciliation writer is absent, so Q103 has no atomic conflict door';
  end if;
  -- It must be reachable by the same bundles as the other ordinary writers, and
  -- by nobody else.
  for v_role in select unnest(array['carr_reader', 'carr_jobs']) loop
    if exists (select 1 from pg_roles where rolname = v_role)
       and has_function_privilege(v_role,
             'ops.j102_record_reconciliation_item(jsonb,jsonb,text,text,jsonb)', 'EXECUTE') then
      raise exception 'S9: % can file a reconciliation item; the conflict writer reaches carr_writer and carr_authority only',
        v_role;
    end if;
  end loop;
  -- In its OWN variable: v_predicate carries the transition writer's definition
  -- from S5 and S7 still reads it.
  v_recon_def := pg_get_functiondef(
    to_regprocedure('ops.j102_record_reconciliation_item(jsonb,jsonb,text,text,jsonb)'));
  foreach v_role in array array[
    -- The governance every sibling writer carries.
    'ops.j102_claim_idempotency(', 'ops.j102_settle_idempotency(',
    'pg_advisory_xact_lock', 'ops.f01_principal()',
    'j102_stale_subject_digest', 'j102_item_operand_set_mismatch',
    'j102_actor_injection_refused', 'j102_item_digest_mismatch',
    -- And the three staleness bindings, which are what stop a reading that went
    -- stale between the caller's read and this insert being filed as current.
    'j102_reconciliation_current_version_not_current',
    'j102_reconciliation_state_evidence_stale',
    'j102_reconciliation_history_evidence_stale',
    'j102_reconciliation_without_conflict',
    'j102_reconciliation_resolves_itself',
    'j102_reconciliation_subject_not_found',
    -- AND THE TWO LABELS. conflict_kind and every edit's field_class are derived
    -- on the shipped path; this writer is granted to carr_writer, so a direct
    -- caller is the one who could otherwise file a visible item labelled with a
    -- kind nothing emits, or a lifecycle field labelled routine.
    'j102_item_conflict_kind_unregistered',
    'j102_item_field_class_mismatch',
    'ops.j102_admission_policy()'
  ] loop
    if position(v_role in v_recon_def) = 0 then
      raise exception 'S9: the reconciliation writer does not carry %; a conflict could be filed unkeyed, unbound, already stale or mislabelled',
        v_role;
    end if;
  end loop;
  -- THE VOCABULARY IT CHECKS AGAINST IS THE MAP'S, and the map must actually hold
  -- one. A writer that reads an absent key refuses nothing.
  if jsonb_typeof(ops.j102_admission_policy() -> 'reconciliation' -> 'conflict_kinds')
       is distinct from 'array'
     or jsonb_array_length(ops.j102_admission_policy() -> 'reconciliation' -> 'conflict_kinds') <> 4
     or jsonb_typeof(ops.j102_admission_policy() -> 'reconciliation' -> 'field_class_registry')
       is distinct from 'object' then
    raise exception 'S9: the admission map carries no reconciliation vocabulary, so the writer''s label checks compare against nothing';
  end if;
  -- The registry the writer re-derives every edit's class from must classify the
  -- fields this fixture and the kernel both use, and must classify NO field
  -- routine -- `routine` is the one class that would buy the auto-merge branch.
  if (ops.j102_admission_policy() -> 'reconciliation' -> 'field_class_registry'
        ->> 'assignment_phase') is distinct from 'lifecycle'
     or (ops.j102_admission_policy() -> 'reconciliation' -> 'field_class_registry'
        ->> 'payment_state') is distinct from 'financial'
     or exists (select 1 from jsonb_each_text(
          ops.j102_admission_policy() -> 'reconciliation' -> 'field_class_registry')
          where value = 'routine') then
    raise exception 'S9: the SQL field-class registry does not match the kernel''s, or has registered a routine field without a policy owner saying so';
  end if;
  -- AND THE RELATION RESTATES BOTH FLOORS STRUCTURALLY, so a row that reached the
  -- table another way still cannot carry an unregistered label or a resolution
  -- flag that is a STRING where a boolean is meant.
  foreach v_role in array array[
    'j102_item_conflict_kind', 'j102_item_subject_kind',
    'j102_item_resolution_flags_are_booleans', 'j102_item_not_machine_resolved'
  ] loop
    if not exists (select 1 from pg_constraint c
                     join pg_class t on t.oid = c.conrelid
                     join pg_namespace n on n.oid = t.relnamespace
                    where n.nspname = 'ops' and t.relname = 'j102_reconciliation_item'
                      and c.conname = v_role) then
      raise exception 'S9: ops.j102_reconciliation_item carries no % constraint', v_role;
    end if;
  end loop;
  -- THE REPLAY DOOR MUST KNOW THE OPERATION, or the key could never be claimed at
  -- all and the writer would be governed in name only.
  if position('record-lifecycle-reconciliation' in
       pg_get_functiondef(to_regprocedure('ops.j102_replay_outcome(text,text,text)'))) = 0 then
    raise exception 'S9: ops.j102_replay_outcome does not admit record-lifecycle-reconciliation, so its idempotency key cannot be claimed';
  end if;
  -- AND NO UNIQUE INDEX COLLAPSES DISTINCT PROPOSALS. Two different edit sets
  -- against the same two versions are two real conflicts; an index over the
  -- version pair would silently discard the second.
  if exists (
    select 1 from pg_index i join pg_class c on c.oid = i.indexrelid
      join pg_class t on t.oid = i.indrelid
      join pg_namespace n on n.oid = t.relnamespace
     where n.nspname = 'ops' and t.relname = 'j102_reconciliation_item' and i.indisunique
       and c.relname <> 'j102_reconciliation_item_pkey') then
    raise exception 'S9: a unique index on the reconciliation relation would collapse distinct proposals sharing two version digests';
  end if;

  -- === S7: the history is bound as tightly as the state ======================
  --
  -- HIGH-5 and HIGH-6, structurally, and this group runs in every session because
  -- none of it needs a principal or a committed row. The behavioural halves are
  -- A11/A12 (which refuse before any state is read, so they really fire) and U2
  -- (which cannot, and says so).

  -- Every event in the map declares its WHOLE payload, not only its kind and its
  -- subject. A map that named the kinds without naming the nested facts would
  -- admit a `deal_closed` event carrying somebody else's closing date.
  for v_transition in select * from jsonb_object_keys(v_policy -> 'transitions') loop
    for v_result in select * from jsonb_array_elements(
      v_policy -> 'transitions' -> v_transition -> 'events') loop
      if jsonb_typeof(v_result -> 'detail') is distinct from 'object' then
        raise exception 'S7: %''s % event declares no nested detail, so an event of the right kind on the right subject could say anything inside',
          v_transition, v_result ->> 'event_kind';
      end if;
      if v_result ? 'event_kind_from' then
        raise exception 'S7: %''s event kind is DERIVED; the kernel spells every lifecycle event kind literally and payment_${level} is a reason_id, not an event',
          v_transition;
      end if;
    end loop;
  end loop;
  if (v_policy ->> 'derived_event_kinds') is distinct from 'false' then
    raise exception 'S7: the admission map claims to derive an event kind';
  end if;
  -- The constants the writer holds every envelope to are the ones this fixture
  -- builds its own payloads from. If they ever diverge, every group above would
  -- refuse for a shape reason and prove nothing about what it is aimed at.
  if (v_policy ->> 'stored_subject_schema_version') is distinct from v_subject_schema
     or (v_policy ->> 'stored_event_schema_version') is distinct from v_event_schema
     or (v_policy ->> 'event_schema_version') is distinct from v_ev_schema then
    raise exception 'S7: the admission map''s stored-record constants are not the ones this rail writes: %',
      jsonb_build_object(
        'subject', v_policy ->> 'stored_subject_schema_version',
        'event_record', v_policy ->> 'stored_event_schema_version',
        'event', v_policy ->> 'event_schema_version');
  end if;

  -- The writer's own call sites for the two residual classes, read off the
  -- installed definition rather than from the file on disk.
  foreach v_role in array array[
    -- HIGH-5: the subject's provenance, and the envelope's identity.
    'j102_subject_provenance_mismatch', 'j102_subject_schema_version_mismatch',
    'j102_subject_tenant_mismatch', 'j102_subject_record_shape_unrecognised',
    'j102_subject_header_state_mismatch',
    -- HIGH-6: the whole event payload, and the evidence the history cites.
    'j102_event_detail_not_canonical', 'j102_event_detail_missing',
    'j102_event_detail_not_produced_by_transition',
    'j102_event_evidence_references_not_rechecked',
    'j102_event_evidence_reference_not_rechecked',
    'j102_event_evidence_reference_duplicated',
    'j102_event_payload_schema_version_mismatch',
    -- M-3: the operand map's own type.
    'j102_expected_state_digests_not_an_object',
    -- HIGH-6 again: the citations are compared against what the RECHECK read.
    'jsonb_array_elements(v_checked)'
  ] loop
    if position(v_role in v_predicate) = 0 then
      raise exception 'S7: the transition writer does not carry %; the history can still say something the transition did not do', v_role;
    end if;
  end loop;
  -- M-2: the two derived receipt fields come off the contract, and the kernel's
  -- diagnostic reason is carried under its own name rather than as a fact.
  if position('''coupled_facts_committed'', coalesce(v_contract -> ''coupled_facts''' in v_predicate) = 0
     or position('''decision_refs'', coalesce(v_contract -> ''decision_refs''' in v_predicate) = 0 then
    raise exception 'M-2: the receipt still echoes the caller''s account of what it committed';
  end if;
  if position('''caller_reported_reason_id''' in v_predicate) = 0 then
    raise exception 'M-2: the kernel''s diagnostic reason is not labelled as the caller''s assertion';
  end if;
  -- The recheck names the canonical reference it derives from its own readers.
  if position('j102_evidence_reference_unresolved' in
       pg_get_functiondef(to_regprocedure('ops.j102_recheck_evidence(jsonb,text,text,text)'))) = 0 then
    raise exception 'S7: the recheck does not derive a reference for the history to cite';
  end if;

  -- And the relations restate the floor beneath all of it. The preflight above
  -- compares COLUMNS; these are the constraints, named, so a database carrying an
  -- earlier candidate's relation without them is visible here.
  foreach v_role in array array[
    'j102_subject_provenance_present', 'j102_subject_record_schema_version',
    'j102_subject_record_tenant', 'j102_subject_record_digest_claim'
  ] loop
    if not exists (
      select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = 'j102_subject_current' and co.conname = v_role) then
      raise exception 'S7: ops.j102_subject_current carries no %; a lifecycle row could exist with no provenance, a foreign schema or a foreign tenant',
        v_role;
    end if;
  end loop;
  foreach v_role in array array[
    'j102_event_cites_evidence', 'j102_event_record_schema_version',
    'j102_event_record_tenant', 'j102_event_payload_schema_version',
    'j102_event_record_digest_claim'
  ] loop
    if not exists (
      select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = 'j102_subject_event' and co.conname = v_role) then
      raise exception 'S7: ops.j102_subject_event carries no %; a history row could cite no evidence at all', v_role;
    end if;
  end loop;
  -- The subject readback compares a STORED claim rather than recomputing the same
  -- bytes and comparing them to themselves.
  if position('v_row.envelope ->> ''record_digest''' in
       pg_get_functiondef(to_regprocedure('ops.j102_subject(text,text)'))) = 0 then
    raise exception 'S7: ops.j102_subject still passes a recomputation as its own expected digest, which verifies nothing';
  end if;

  -- === B9 and B10 need no principal ========================================
  -- The closing-date constraint is structural: a closing_settlement row with no
  -- actual date cannot exist, so no closing transition can ever find one.
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_first_party_record'
       and co.conname = 'j102_fact_closing_requires_date') then
    raise exception 'B9: a closing_settlement record could be stored without an actual closing date';
  end if;
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_first_party_record'
       and co.conname = 'j102_fact_reason_required') then
    raise exception 'B9: a deal_failure or correction record could be stored without a reason';
  end if;

  -- B10: the private approval reader raises rather than returning anything. It
  -- is reachable here only where this session owns it; where it is not, the
  -- absence of the grant was already proved in S2.
  begin
    perform ops.j102_typed_approval('representation_equivalence_approval', 'j102-fixture-approval-1');
    raise exception 'B10: the private approval reader returned instead of refusing';
  exception when insufficient_privilege then
    -- A missing EXECUTE grant is ALSO SQLSTATE 42501, so it lands in this arm
    -- and not in `others` below. First execution (2026-09-25, PostgreSQL 17, as
    -- carr_authority_joe) hit exactly that: the ungranted reader was reported as
    -- "refused for the wrong reason". Told apart by the engine's own text.
    if sqlerrm ~ '^permission denied for function' then
      raise notice 'B10: the approval reader is not executable from this session; its refusal text is checked only where it is reachable.';
    elsif sqlerrm !~ 'j102_typed_approval_unavailable' then
      raise exception 'B10: the approval reader refused for the wrong reason: %', sqlerrm;
    end if;
  when others then
    if sqlerrm ~ 'permission denied' then
      raise notice 'B10: the approval reader is not executable from this session; its refusal text is checked only where it is reachable.';
    else
      raise;
    end if;
  end;

  -- The migration reader answers no, for every input, and names what is missing.
  v_result := ops.j102_migration_readiness();
  if (v_result ->> 'may_retire_callers') <> 'false'
     or (v_result ->> 'migration_complete') <> 'false'
     or (v_result ->> 'big_bang_rename') <> 'false' then
    raise exception 'Q081: the migration reader claimed something it cannot know: %', v_result;
  end if;
  if jsonb_array_length(v_result -> 'missing_facts') < 2 then
    raise exception 'Q081: the migration reader does not name the missing facts';
  end if;

  if not v_behavioural then
    raise notice 'STRUCTURAL GROUPS PASSED (S1, S2, S3, S4, S5, S6, B9, B10, M3, Q081). Behavioural groups skipped: no admitted principal.';
    return;
  end if;

  -- ========================================================================
  -- BEHAVIOURAL GROUPS. Everything below runs as the derived actor and rolls
  -- back with the transaction.
  --
  -- NONE OF THE GROUPS BETWEEN HERE AND GROUP J IS A POSITIVE TRANSITION WALK.
  -- No lifecycle subject exists yet at this point in the file -- the creation
  -- door is not opened until J -- so every ops.j102_apply_transition call below
  -- is an adversarial one and every one of them must REFUSE. The adversarial ids
  -- are deliberately NOT the walk's, so nothing group J creates can later satisfy
  -- a group that is supposed to refuse for want of a row.
  -- ========================================================================

  -- Two synthetic first-party records. These CAN be written -- the record layer
  -- is seedable even though the lifecycle rail is not, deliberately, because a
  -- record binds itself to a subject id and advances nothing on its own -- and
  -- they give the adversarial groups an AUTHENTIC record about the WRONG subject
  -- to present, which is the only shape that tests a binding at all.
  --
  -- `assignment_mandate` is used rather than one of the four partner-only kinds
  -- precisely so these seeds run whichever admitted class the session holds.
  v_rec := jsonb_build_object(
    'schema_version', v_fact_schema, 'tenant', v_tenant,
    'record_kind', 'assignment_mandate', 'record_id', v_fact_id,
    'subject_kind', 'assignment', 'subject_id', v_assignment_id,
    'reason', null, 'detail', 'synthetic fixture mandate',
    'closing_date', null, 'supporting_document_id', null,
    'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
    'recorded_at', v_now, 'advances_lifecycle_state', false);
  v_result := ops.j102_record_first_party_fact(
    jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_first_party_record',
      'tenant', v_tenant, 'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
    'j102-fixture-key-fact', v_placeholder);
  if (v_result ->> 'advances_lifecycle_state') <> 'false' then
    raise exception 'a first-party record must advance no lifecycle state';
  end if;
  if (v_result ->> 'bound_subject_id') <> v_assignment_id
     or (v_result ->> 'recorded_by_authorization_class') <> v_class then
    raise exception 'the stored record did not report its binding and its derived author class: %', v_result;
  end if;
  v_fact := ops.j102_first_party_record('assignment_mandate', v_fact_id);
  if v_fact is null then
    raise exception 'the first-party record did not read back';
  end if;
  if (v_fact -> 'record' ->> 'subject_id') <> v_assignment_id then
    raise exception 'the readback lost the record''s subject binding';
  end if;
  v_fact_digest := v_fact ->> 'record_digest';

  -- The SECOND mandate: identical in every way except the assignment it is
  -- about. Authentic, unmoved, correctly pinned, and about somebody else.
  v_rec := jsonb_build_object(
    'schema_version', v_fact_schema, 'tenant', v_tenant,
    'record_kind', 'assignment_mandate', 'record_id', v_fact_id_2,
    'subject_kind', 'assignment', 'subject_id', v_assignment_id_2,
    'reason', null, 'detail', 'synthetic fixture mandate for the other assignment',
    'closing_date', null, 'supporting_document_id', null,
    'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
    'recorded_at', v_now, 'advances_lifecycle_state', false);
  perform ops.j102_record_first_party_fact(
    jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_first_party_record',
      'tenant', v_tenant, 'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
    'j102-fixture-key-fact-2', v_placeholder);
  v_fact2_digest := ops.j102_first_party_record('assignment_mandate', v_fact_id_2)
                      ->> 'record_digest';

  -- The manifest the adversarial groups reuse: the RIGHT mandate for assignment
  -- 1, correctly pinned and correctly bound.
  v_manifest := jsonb_build_array(jsonb_build_object(
    'evidence_kind', 'search_initiation', 'source', 'first_party_record',
    'reader', 'ops.j102_first_party_record',
    'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                   'record_id', v_fact_id),
    'expected_record_digest', v_fact_digest,
    'binding', jsonb_build_object('subject_kind', 'assignment',
                                  'subject_id', v_assignment_id)));
  -- And the same manifest for the OTHER assignment's mandate: equally authentic,
  -- equally unmoved, about somebody else.
  v_manifest_wrong := jsonb_build_array(jsonb_build_object(
    'evidence_kind', 'search_initiation', 'source', 'first_party_record',
    'reader', 'ops.j102_first_party_record',
    'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                   'record_id', v_fact_id_2),
    'expected_record_digest', v_fact2_digest,
    'binding', jsonb_build_object('subject_kind', 'assignment',
                                  'subject_id', v_assignment_id_2)));

  -- The subject and event envelopes an `open-assignment` call would carry. The
  -- operand below is a PLACEHOLDER digest rather than a real one, because the
  -- ADVERSARIAL assignment has no row and deliberately never gets one: these
  -- groups are about what the writer refuses on a request, and group J is where a
  -- real committed row is obtained and the checks that need one are driven.
  v_rec := jsonb_build_object(
    'schema_version', v_subject_schema, 'tenant', v_tenant,
    'subject_kind', 'assignment', 'subject_id', v_assignment_id,
    'state', v_assignment_state,
    'established_by_transition', 'open-assignment',
    'prior_state_digest', v_placeholder,
    'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
  -- THE EVENT IN ITS WHOLE CANONICAL SHAPE, nested detail and all. The writer now
  -- compares every non-identity key of the nested event against the admission
  -- map's template for `assignment_opened` and the cited references against what
  -- the recheck re-read, so an event carrying only its kind and its subject is no
  -- longer "otherwise well formed" and the adversarial groups below would refuse
  -- for the wrong reason if this were left short.
  v_evt := jsonb_build_object(
    'schema_version', v_event_schema, 'tenant', v_tenant,
    'event', jsonb_build_object('schema_version', v_ev_schema,
      'event_kind', 'assignment_opened',
      'subject_kind', 'assignment', 'subject_id', v_assignment_id,
      'engagement_id', v_engagement_id,
      'assignment_phase', 'search',
      'evidence_reference', v_fact_id),
    'transition_id', 'open-assignment',
    'evidence_references', jsonb_build_array(jsonb_build_object(
      'evidence_kind', 'search_initiation', 'source', 'first_party_record',
      'reference', v_fact_id)),
    'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
  v_subjects := jsonb_build_array(jsonb_build_object(
    'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
    'tenant', v_tenant, 'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
    'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder));
  v_events := jsonb_build_array(jsonb_build_object(
    'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
    'tenant', v_tenant, 'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
    'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder));

  -- === P0: the transition writer STILL creates no primary subject ============
  --
  -- The one call that would seed a lifecycle subject THROUGH A TRANSITION, made
  -- exactly as an earlier revision of this fixture made it: a well-formed
  -- `open-assignment` with correct evidence, a correct event, and an explicit
  -- NULL compare-and-swap operand meaning "this subject must be absent". That is
  -- a creation of the transition's own primary subject, and it is refused by
  -- name.
  --
  -- THIS IS THE GROUP TO READ FIRST NOW THAT A CREATION DOOR EXISTS. The right
  -- way to obtain an assignment is group J's `initialize-assignment`, which
  -- creates it at `research` under a verified parent chain and leaves
  -- `open-assignment` to do the whole of its own job. The wrong way is this call,
  -- and it must keep refusing.
  begin
    v_rec2 := jsonb_set(v_rec, '{prior_state_digest}', 'null'::jsonb);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, null),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-p0', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment',
        'reason_id', 'search_initiation_opens_assignment',
        'coupled_facts', jsonb_build_array('assignment.assignment_phase'),
        'decision_refs', jsonb_build_array('Q080.D1')));
    raise exception 'P0: a transition CREATED the primary subject it claims to advance; the prerequisites it declares had no committed row to be checked against';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_primary_subject_creation_refused' then raise; end if;
  end;
  raise notice 'P0: the transition writer refused to create its own primary subject, which is the property the initialization door must not have relaxed. Creation happens in group J through ops.j102_initialize_subject, at the earliest declared state and under a verified parent chain.';

  -- ========================================================================
  -- ADVERSARIAL GROUPS A1-A10. Every one of them is a DIRECT call, which is the
  -- threat model: everything below would have been refused by the store and by
  -- the kernel, and every one of them reached a durable row against some earlier
  -- revision of this writer. All ten run whichever admitted class this session
  -- holds, except A4, which is behavioural only for a sponsored agent.
  -- ========================================================================

  -- === A1: the wrong subject, WITH the extra compare-and-swap key included ====
  --
  -- THE REVIEW'S BLOCKER-2 REPRODUCER, in the form that defeats the obvious fix.
  -- Requiring the manifest's binding to be a MEMBER of the lock set is not enough,
  -- because the caller supplies the lock set: it simply adds the other subject to
  -- the map, with a true operand, and goes on proposing this one. The record is
  -- authentic, unmoved and correctly pinned; what is wrong is that it is about
  -- assignment 2 and this call advances assignment 1.
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      -- The extra key is HERE, and a membership check would find the binding in it.
      jsonb_build_object(
        'assignment:' || v_assignment_id, v_placeholder,
        'assignment:' || v_assignment_id_2, null),
      v_subjects, v_events, v_manifest_wrong,
      'j102-fixture-key-a1', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A1: evidence about another subject advanced this one, with the lock set covering it';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_evidence_not_bound_to_primary_subject' then raise; end if;
  end;

  -- === A2: the primary-creation masquerade, one transition over ==============
  --
  -- P0 proved it for `open-assignment`. This proves the refusal is a property of
  -- the MAP rather than of one transition: `record-loi-submission` advances a
  -- property negotiation, and a caller proposing to create that negotiation is
  -- proposing a lifecycle subject nothing in this rail creates.
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
      'state', v_negotiation_state,
      'established_by_transition', 'record-loi-submission',
      'prior_state_digest', null,
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'record-loi-submission',
      jsonb_build_object(
        'property_negotiation:' || v_negotiation_id, null,
        'assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a2', v_placeholder,
      jsonb_build_object('operation', 'record-loi-submission', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A2: a transition created the property negotiation it claims to advance';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_primary_subject_creation_refused' then raise; end if;
  end;

  -- === A3: the diagnostics cannot choose a weaker operation ==================
  --
  -- `record-deal-axis` is the routine, agent-permitted operation. Naming it beside
  -- a partner-only transition is how a gate keyed on the OPERATION would be
  -- satisfied while the transition performed was something else entirely. The
  -- pairing is checked before the idempotency key is claimed, so this attempt does
  -- not even burn a key.
  begin
    perform ops.j102_apply_transition(
      'record-deal-closing',
      jsonb_build_object('deal:' || v_deal_id, v_placeholder),
      v_subjects, v_events, v_manifest,
      'j102-fixture-key-a3', v_placeholder,
      jsonb_build_object('operation', 'record-deal-axis', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A3: a routine operation performed a partner-only transition';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_operation_transition_mismatch' then raise; end if;
  end;

  -- And a transition that is in no contract at all: p_transition_id used to be an
  -- unchecked string that reached the event table and the receipt.
  begin
    perform ops.j102_apply_transition(
      'set-the-phase',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, v_events, v_manifest,
      'j102-fixture-key-a3b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A3: a transition that does not exist was performed and recorded';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_unknown_transition' then raise; end if;
  end;

  -- === A4: the direct carr_writer bypass of a partner-only transition =========
  --
  -- BLOCK-1's whole point. The EXECUTE grant on the transition writer reaches
  -- carr_writer, which always resolves to a sponsored agent, and the three
  -- partner-only transitions used to be reachable from there because the writer
  -- never asked the actor's CLASS. Behavioural only in an agent session; S5 covers
  -- the same fact structurally in a partner session.
  if not v_partner then
    begin
      perform ops.j102_apply_transition(
        'cancel-pending-deal',
        jsonb_build_object('deal:' || v_deal_id, v_placeholder),
        v_subjects, v_events, v_manifest,
        'j102-fixture-key-a4', v_placeholder,
        jsonb_build_object('operation', 'cancel-pending-deal', 'reason_id', 'x',
          'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
      raise exception 'A4: a sponsored agent performed a partner-only transition on a direct call';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_actor_class_not_permitted' then raise; end if;
    end;
  else
    raise notice 'A4 behavioural half skipped: this session holds verified_partner, so the agent refusal cannot be demonstrated from here. S5 asserts the same admission map structurally.';
  end if;

  -- === A5: the event lies, and the event SET ==================================
  --
  -- Five separate attempts, because "at least one event" -- which is all the
  -- writer used to require -- catches none of them.

  -- (a) an event that does not hash to its own claim. The insert RECOMPUTES both
  -- digests, so the table's CHECK constraints were trivially satisfied while the
  -- envelope's own record_digest said something else inside the hashed bytes.
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt, 'record_digest', v_lie,
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a5a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: an event whose bytes contradict its own digest claim was appended';
  exception when data_exception then
    if sqlerrm !~ 'j102_event_digest_mismatch' then raise; end if;
  end;

  -- (b) an event for an UNRELATED deal, riding along with a legitimate call. It
  -- used to land in history, be returned by j102_read, and describe a subject
  -- whose current state never moved.
  begin
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'deal_closed',
        'subject_kind', 'deal', 'subject_id', v_deal_id_c),
      'transition_id', 'open-assignment', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      v_events || jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a5b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: history was written for a deal this transition never advanced';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_set_mismatch' then raise; end if;
  end;

  -- (c) an event claiming a DIFFERENT transition than the one being applied.
  begin
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_opened',
        'subject_kind', 'assignment', 'subject_id', v_assignment_id),
      'transition_id', 'commit-winning-property', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a5c', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: history named a transition that did not run';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_transition_mismatch' then raise; end if;
  end;

  -- (d) NO EVENTS AT ALL: a state change nobody can audit afterwards.
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, '[]'::jsonb, v_manifest,
      'j102-fixture-key-a5d', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: a transition applied with no history at all';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_no_event_envelopes' and sqlerrm !~ 'j102_event_set_mismatch' then raise; end if;
  end;

  -- (e) A PLAUSIBLE WRONG KIND on a correctly bound subject -- the residual the
  -- previous correction acknowledged and left open. The subject is one this call
  -- advances, the transition is the one being applied, the bytes hash to their
  -- claim; only the KIND is a lie, and a reader searching history by kind is
  -- exactly who it lies to.
  begin
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_committed',
        'subject_kind', 'assignment', 'subject_id', v_assignment_id),
      'transition_id', 'open-assignment', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a5e', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: an assignment_committed event was appended by an open-assignment call';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_missing_or_wrong' then raise; end if;
  end;

  -- === A6: empty, unknown, and wrong-source evidence ==========================
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, v_events, '[]'::jsonb,
      'j102-fixture-key-a6a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A6: a transition applied with no evidence to re-read';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_evidence_recheck_required' then raise; end if;
  end;

  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, v_events,
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'a_convincing_email', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-a6b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A6: an evidence kind no contract admits was accepted';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_unknown_evidence_kind' then raise; end if;
  end;

  -- Real evidence, presented from the wrong SOURCE. A first-party record
  -- announced as a document takes the document branch, which never re-reads the
  -- record's own typed binding.
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, v_events,
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'f01_document',
        'reader', 'ops.f01_read.document',
        'selector', jsonb_build_object('document_id', v_fact_id),
        'expected_version_no', '1', 'expected_content_digest', v_fact_digest,
        'expected_link_digest', v_placeholder,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-a6c', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A6: a first-party record was re-read as a document, skipping its own binding';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_evidence_source_mismatch' then raise; end if;
  end;

  -- === A7: the unrelated proposed subject, and the duplicate kind =============
  --
  -- An allowed operation carrying a second subject beside its real one: a new
  -- updated_by and updated_at on a client who had nothing to do with it at best,
  -- an arbitrary state change at worst.
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'deal', 'subject_id', v_deal_id_c,
      'state', jsonb_build_object(
        'subject_kind', 'deal', 'subject_id', v_deal_id_c,
        'assignment_id', v_assignment_id, 'property_id', v_property_id,
        'instrument_kind', 'lease', 'deal_state', 'closed',
        'execution_state', 'executed', 'diligence_state', 'not_applicable',
        'closing_state', 'closed', 'commission_agreement_state', 'absent',
        'invoice_state', 'not_invoiced', 'payment_state', 'unpaid',
        'completion_state', 'open', 'cancellation_reason', null,
        'closing_date', v_now),
      'established_by_transition', 'open-assignment',
      'prior_state_digest', null,
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object(
        'assignment:' || v_assignment_id, v_placeholder,
        'deal:' || v_deal_id_c, null),
      v_subjects || jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a7a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A7: an open-assignment call wrote a closed deal beside its assignment';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_subject_kind_not_written_by_transition' then raise; end if;
  end;

  -- Two subjects of one kind: which one is the primary the evidence must bind to?
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_assignment_id_2,
      'state', v_assignment_state || jsonb_build_object('subject_id', v_assignment_id_2),
      'established_by_transition', 'open-assignment',
      'prior_state_digest', v_placeholder,
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object(
        'assignment:' || v_assignment_id, v_placeholder,
        'assignment:' || v_assignment_id_2, v_placeholder),
      v_subjects || jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a7b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A7: one transition advanced two assignments at once';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_duplicate_proposed_subject_kind' then raise; end if;
  end;

  -- === A8: a coupled subject proposed as a CREATION ===========================
  --
  -- `record-loi-submission` writes the negotiation AND the assignment it belongs
  -- to, and it UPDATES both. A caller proposing to create the assignment beside a
  -- genuine negotiation is asking for a row nothing in this rail creates, on the
  -- strength of a transition that never creates one.
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_assignment_id,
      'state', v_assignment_state,
      'established_by_transition', 'record-loi-submission',
      'prior_state_digest', null,
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'record-loi-submission',
      jsonb_build_object(
        'property_negotiation:' || v_negotiation_id, v_placeholder,
        'assignment:' || v_assignment_id, null),
      jsonb_build_array(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant,
          'record', jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
            'state', v_negotiation_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now),
          'record_digest', ops.f01_digest_jsonb(jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
            'state', v_negotiation_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now)),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a8', v_placeholder,
      jsonb_build_object('operation', 'record-loi-submission', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A8: a transition that only updates an assignment created one';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_subject_creation_not_permitted' then raise; end if;
  end;

  -- === A9: the coupled write, sent as a subset ================================
  --
  -- Q082: coupled facts land together or refuse together. `record-loi-submission`
  -- moves the negotiation to loi_submitted AND moves its assignment to
  -- negotiation; a call carrying only the negotiation leaves an assignment that
  -- does not know one of its negotiations was submitted. `writes` admitted this,
  -- because a subset of a permitted set is still a subset.
  begin
    perform ops.j102_apply_transition(
      'record-loi-submission',
      jsonb_build_object('property_negotiation:' || v_negotiation_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant,
        'record', jsonb_build_object(
          'schema_version', v_subject_schema, 'tenant', v_tenant,
          'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
          'state', v_negotiation_state,
          'established_by_transition', 'record-loi-submission',
          'prior_state_digest', v_placeholder,
          'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now),
        'record_digest', ops.f01_digest_jsonb(jsonb_build_object(
          'schema_version', v_subject_schema, 'tenant', v_tenant,
          'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
          'state', v_negotiation_state,
          'established_by_transition', 'record-loi-submission',
          'prior_state_digest', v_placeholder,
          'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now)),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a9', v_placeholder,
      jsonb_build_object('operation', 'record-loi-submission', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A9: half of a coupled write landed without the other half';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_required_subject_not_proposed' then raise; end if;
  end;

  -- === A10: the whole subject set, and an event set short by one ==============
  --
  -- The mirror of A9 on the history side, using the same transition: both
  -- subjects are present and the single event `record-loi-submission` appends is
  -- replaced by an event for the OTHER subject it writes. The subject check
  -- passes, the "event names a subject this call advances" check passes, and only
  -- the exact event set refuses it.
  begin
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_opened',
        'subject_kind', 'assignment', 'subject_id', v_assignment_id),
      'transition_id', 'record-loi-submission', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    perform ops.j102_apply_transition(
      'record-loi-submission',
      jsonb_build_object(
        'property_negotiation:' || v_negotiation_id, v_placeholder,
        'assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant,
          'record', jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
            'state', v_negotiation_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now),
          'record_digest', ops.f01_digest_jsonb(jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
            'state', v_negotiation_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now)),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        -- The coupled assignment, provenanced to the transition THIS call
        -- applies. Reusing the open-assignment envelope here would now refuse at
        -- j102_subject_provenance_mismatch and A10 would stop proving anything
        -- about the event set, which is what it is for.
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant,
          'record', jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'assignment', 'subject_id', v_assignment_id,
            'state', v_assignment_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now),
          'record_digest', ops.f01_digest_jsonb(jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'assignment', 'subject_id', v_assignment_id,
            'state', v_assignment_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now)),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a10', v_placeholder,
      jsonb_build_object('operation', 'record-loi-submission', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A10: a transition appended an event it does not produce, in place of the one it does';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_missing_or_wrong' then raise; end if;
  end;

  -- === A11: the subject's own provenance, and its envelope's identity =========
  --
  -- HIGH-5's reproducer, and it needs nothing exotic: a FULLY CANONICAL call --
  -- real evidence, correctly bound, correct event, correct target -- whose
  -- subject envelope claims to have been established by a DIFFERENT transition.
  -- The forged string is hashed into the envelope's own record_digest, so the
  -- digest check agrees with it; ops.j102_subject returns it as the row's
  -- provenance and this writer's own readback echoes it back. Nothing compared it
  -- to the transition that ran. These four all refuse BEFORE any state is read,
  -- so unlike U1 below they are proved here rather than reported as unproven.
  begin
    v_rec2 := jsonb_set(v_rec, '{established_by_transition}', '"record-deal-closing"'::jsonb);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: an assignment recorded its state as established by a partner-only closing that never ran';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_subject_provenance_mismatch' then raise; end if;
  end;

  -- The same field naming no transition at all. j102_unknown_transition guards
  -- only p_transition_id, so this string reached the durable row unexamined.
  begin
    v_rec2 := jsonb_set(v_rec, '{established_by_transition}', '"set-the-phase"'::jsonb);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: a subject was established by a transition that does not exist';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_subject_provenance_mismatch' then raise; end if;
  end;

  -- A SECOND, UNCHECKED CLAIM hashed into the same authoritative bytes. The
  -- envelope is otherwise perfect; it simply carries one more key, which
  -- ops.j102_read would hand a reviewer as part of the row.
  begin
    v_rec2 := v_rec || jsonb_build_object('approved_by', 'joe');
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11c', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: an unchecked approval claim landed inside an authoritative lifecycle row';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_subject_record_shape_unrecognised' then raise; end if;
  end;

  -- A FOREIGN SCHEMA VERSION on the stored subject, and a header naming a subject
  -- its own state does not describe.
  begin
    v_rec2 := jsonb_set(v_rec, '{schema_version}',
      to_jsonb('doctorcre-v5-j102-stored-lifecycle-subject.v0'::text));
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11d', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: a lifecycle subject was stored under a schema this rail does not write';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_subject_schema_version_mismatch' then raise; end if;
  end;

  begin
    v_rec2 := jsonb_set(v_rec, '{subject_id}', to_jsonb(v_assignment_id_2));
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id_2, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11e', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: a row whose primary key and whose state describe different subjects was written';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_subject_header_state_mismatch' then raise; end if;
  end;

  -- The same class on the history side: an event record carrying a key the store
  -- never writes, and a nested event stamped with a foreign kernel schema.
  begin
    v_evt2 := v_evt || jsonb_build_object('authorised_by', 'joe');
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a11f', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: an unchecked authorisation claim landed inside a history row';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_record_shape_unrecognised' then raise; end if;
  end;

  begin
    v_evt2 := jsonb_set(v_evt, '{event,schema_version}',
      to_jsonb('doctorcre-v5-j102-lifecycle-event.v0'::text));
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a11g', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: a lifecycle event was appended under a kernel schema the kernel does not stamp';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_payload_schema_version_mismatch' then raise; end if;
  end;

  -- === A12: the compare-and-swap operand map that is not a map ================
  --
  -- M-3. A SQL NULL here used to propagate through every `?` and `->` in the
  -- admission loop as NULL, which `if` reads as false: BOTH creation refusals and
  -- j102_expected_state_digest_missing were skipped in silence, the call refused
  -- further down for a different reason, and a creation attempted this way was
  -- reported as `created_subject_kinds: []`. A JSON scalar was worse -- it reached
  -- jsonb_object_keys and raised an unnamed cast error rather than any J102
  -- refusal. Both now name the attempt.
  begin
    perform ops.j102_apply_transition(
      'open-assignment', null,
      v_subjects, v_events, v_manifest,
      'j102-fixture-key-a12a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A12: a transition ran with no compare-and-swap operands at all';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_expected_state_digests_not_an_object' then raise; end if;
  end;

  begin
    perform ops.j102_apply_transition(
      'open-assignment', 'null'::jsonb,
      v_subjects, v_events, v_manifest,
      'j102-fixture-key-a12b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A12: a JSON scalar was accepted as a compare-and-swap operand map';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_expected_state_digests_not_an_object' then raise; end if;
  end;

  -- U1 AND U2 USED TO SIT HERE, AND THEY HAVE MOVED. Both groups are decided
  -- against a COMMITTED row -- that is the whole point of them -- and at this
  -- point in the file no lifecycle subject exists, so both refused at the
  -- compare-and-swap for want of an assignment and reported themselves UNPROVEN.
  -- The creation door makes a committed assignment obtainable legitimately, so
  -- they now run inside group J against the assignment the walk actually creates
  -- and opens, where they prove the target and event-detail checks they are aimed
  -- at rather than naming a reason they can no longer give.
  --
  -- THEY ARE NOT DUPLICATED HERE. Running them twice would mean reporting a
  -- compare-and-swap refusal beside a real one and calling both coverage.

  -- === B5: an idempotent replay, on the one writer this file can drive ========
  --
  -- The transition writer cannot reach a success here, so the replay property is
  -- proved where it CAN be: the first-party record writer settled a key above,
  -- and the same key must return the same stored outcome rather than writing a
  -- second row.
  -- The SECOND mandate's own record, rebuilt here rather than reused from a
  -- variable the adversarial groups have since overwritten.
  v_rec2 := jsonb_build_object(
    'schema_version', v_fact_schema, 'tenant', v_tenant,
    'record_kind', 'assignment_mandate', 'record_id', v_fact_id_2,
    'subject_kind', 'assignment', 'subject_id', v_assignment_id_2,
    'reason', null, 'detail', 'synthetic fixture mandate for the other assignment',
    'closing_date', null, 'supporting_document_id', null,
    'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
    'recorded_at', v_now, 'advances_lifecycle_state', false);
  v_replay := ops.j102_record_first_party_fact(
    jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_first_party_record',
      'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
    'j102-fixture-key-fact-2', v_placeholder);
  if v_replay is null or (v_replay ->> 'record_id') <> v_fact_id_2 then
    raise exception 'B5: the settled key did not replay its stored outcome: %', v_replay;
  end if;
  select count(*) into v_count from ops.j102_first_party_record where tenant = v_tenant;
  if v_count <> 2 then
    raise exception 'B5: a replay appended a second row (% rows)', v_count;
  end if;
  -- The same key over a DIFFERENT payload is a substitution attempt.
  begin
    perform ops.j102_record_first_party_fact(
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_first_party_record',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-fact-2', 'sha256:' || repeat('9', 64));
    raise exception 'B5: a different payload replayed under the same key';
  exception when unique_violation then
    if sqlerrm !~ 'j102_idempotency_payload_mismatch' then raise; end if;
  end;

  -- === B6: the append-only relations refuse UPDATE and DELETE for real ======
  begin
    update ops.j102_first_party_record set record_id = 'tampered' where tenant = v_tenant;
    raise exception 'B6: a first-party business record accepted an UPDATE';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_append_only_violation' and sqlerrm !~ 'permission denied' then raise; end if;
  end;
  begin
    delete from ops.j102_first_party_record where tenant = v_tenant;
    raise exception 'B6: a first-party business record accepted a DELETE';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_append_only_violation' and sqlerrm !~ 'permission denied' then raise; end if;
  end;

  -- === B7: direct DML outside a registered writer refuses ==================
  --
  -- AND THIS IS ALSO WHY THIS FIXTURE CANNOT SEED A SUBJECT. The guard reads the
  -- PL/pgSQL call stack and requires a registered ops.j102_* writer frame, so a
  -- raw INSERT from this DO block is refused whoever owns the table. Seeding
  -- around it would need either a new writer -- which is the missing bootstrap,
  -- and inventing one here would be inventing the capability the slice does not
  -- have -- or disabling the guard, which is the thing being tested.
  begin
    insert into ops.j102_subject_current
      (tenant, subject_kind, subject_id, envelope, envelope_digest, state_digest,
       parent_id, deal_state, updated_by, updated_at)
    values (v_tenant, 'assignment', v_assignment_id, '{}'::jsonb, v_placeholder,
            v_placeholder, null, null, v_actor, now());
    raise exception 'B7: a raw INSERT reached current state';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_direct_dml_refused' and sqlerrm !~ 'permission denied' then raise; end if;
  when others then
    -- A CHECK constraint firing first is also a refusal; what must never happen
    -- is the row landing.
    null;
  end;
  if exists (select 1 from ops.j102_subject_current where tenant = v_tenant) then
    raise exception 'B7: the smuggled row landed';
  end if;

  -- === B9 / B16 behaviourally: the closing settlement, both of its guards =====
  --
  -- WHICH HALF THIS SESSION PROVES DEPENDS ON WHICH CLASS IT HOLDS, and both
  -- halves are worth having. As a SPONSORED AGENT, the interesting fact is H5:
  -- the record is refused outright, so an agent-authored closing date never
  -- exists for a partner to launder through a later transition. As a VERIFIED
  -- PARTNER, the interesting fact is B9: even the right author cannot store a
  -- closing_settlement with no actual date.
  if v_class = 'sponsored_agent' then
    begin
      perform ops.j102_record_first_party_fact(
        (select jsonb_build_object(
           'schema_version', v_env_schema,
           'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
           'record', r, 'record_digest', ops.f01_digest_jsonb(r),
           'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
           from (select jsonb_build_object(
             'schema_version', v_fact_schema,
             'tenant', v_tenant, 'record_kind', 'closing_settlement',
             'record_id', 'j102-fixture-agent-closing',
             'subject_kind', 'deal', 'subject_id', v_deal_id,
             'reason', null, 'detail', null, 'closing_date', v_now,
             'supporting_document_id', null,
             'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
             'recorded_at', v_now,
             'advances_lifecycle_state', false) as r) s),
        'j102-fixture-key-agent-closing', v_placeholder);
      raise exception 'B16: a sponsored agent authored a closing settlement';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_partner_authored_record_refused' then raise; end if;
    end;
    raise notice 'B9 behavioural skipped and B16 proved in the agent direction: this session is a sponsored agent, so no closing_settlement of any shape can be authored here.';
  else
    begin
      perform ops.j102_record_first_party_fact(
        (select jsonb_build_object(
           'schema_version', v_env_schema,
           'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
           'record', r, 'record_digest', ops.f01_digest_jsonb(r),
           'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
           from (select jsonb_build_object(
             'schema_version', v_fact_schema,
             'tenant', v_tenant, 'record_kind', 'closing_settlement',
             'record_id', 'j102-fixture-dateless-closing',
             'subject_kind', 'deal', 'subject_id', v_deal_id,
             'reason', null, 'detail', null, 'closing_date', null,
             'supporting_document_id', null,
             'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
             'recorded_at', v_now,
             'advances_lifecycle_state', false) as r) s),
        'j102-fixture-key-dateless', v_placeholder);
      raise exception 'B9: a closing_settlement record without an actual closing date was stored';
    exception when check_violation then
      null;  -- Q094 structurally: signing is not closing, and neither is a null date
    end;
    -- And the partner-authored half of B16: the author class lands ON the row,
    -- derived from the principal rather than supplied.
    perform ops.j102_record_first_party_fact(
      (select jsonb_build_object(
         'schema_version', v_env_schema,
         'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', v_fact_schema,
           'tenant', v_tenant, 'record_kind', 'closing_settlement',
           'record_id', 'j102-fixture-partner-closing',
           'subject_kind', 'deal', 'subject_id', v_deal_id,
           'reason', null, 'detail', null, 'closing_date', v_now,
           'supporting_document_id', null,
           'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
           'recorded_at', v_now,
           'advances_lifecycle_state', false) as r) s),
      'j102-fixture-key-partner-closing', v_placeholder);
    select count(*) into v_count from ops.j102_first_party_record
     where tenant = v_tenant and record_id = 'j102-fixture-partner-closing'
       and recorded_by_class = 'verified_partner' and bound_subject_id = v_deal_id;
    if v_count <> 1 then
      raise exception 'B16: the partner-authored closing settlement did not land with its author class and its deal binding';
    end if;
  end if;

  -- ========================================================================
  -- === I1: the creation door's own refusal matrix ==========================
  --
  -- Every call below is a DIRECT call on ops.j102_initialize_subject holding
  -- nothing more than the writer's EXECUTE grant, and every one must refuse. A
  -- creation path is the obvious place to smuggle a later state past the evidence
  -- that is supposed to establish it, so these are the cases that matter most in
  -- this file after P0.
  --
  -- The relationship payload each case starts from is CANONICAL: the right shape,
  -- the right provenance, the right event, an explicit null operand. Each case
  -- then breaks exactly one thing, so the refusal names that thing rather than an
  -- unrelated defect further up.
  -- ========================================================================
  v_state := jsonb_build_object(
    'subject_kind', 'relationship', 'subject_id', v_walk_rel_id,
    'relationship_state', 'prospect', 'active_engagement_count', 0, 'party_id', null);
  v_rec := jsonb_build_object(
    'schema_version', v_subject_schema, 'tenant', v_tenant,
    'subject_kind', 'relationship', 'subject_id', v_walk_rel_id,
    'state', v_state,
    'established_by_transition', 'initialize-prospect-relationship',
    'prior_state_digest', null,
    'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
  v_subject_env := jsonb_build_object(
    'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
    'tenant', v_tenant, 'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
    'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder);
  v_evt := jsonb_build_object(
    'schema_version', v_event_schema, 'tenant', v_tenant,
    'event', jsonb_build_object('schema_version', v_ev_schema,
      'event_kind', 'relationship_initialized',
      'subject_kind', 'relationship', 'subject_id', v_walk_rel_id,
      'relationship_state', 'prospect'),
    'transition_id', 'initialize-prospect-relationship',
    'evidence_references', '[]'::jsonb,
    'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
  v_event_env := jsonb_build_object(
    'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
    'tenant', v_tenant, 'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
    'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder);

  -- I1.a An initialization nobody registered, and a TRANSITION id offered to the
  --      creation door. The second is the one that matters: it must be refused
  --      because it is a transition, not because the map happens not to list it.
  begin
    perform ops.j102_initialize_subject('initialize-deal',
      jsonb_build_object('relationship:' || v_walk_rel_id, null),
      v_subject_env, v_event_env, 'j102-fixture-key-i1a', v_placeholder,
      jsonb_build_object('operation', 'initialize-prospect-relationship',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    raise exception 'I1.a: an unregistered initialization created a subject';
  exception when others then
    if sqlerrm !~ 'j102_unknown_initialization' then raise; end if;
  end;
  begin
    perform ops.j102_initialize_subject('commit-winning-property',
      jsonb_build_object('relationship:' || v_walk_rel_id, null),
      v_subject_env, v_event_env, 'j102-fixture-key-i1a2', v_placeholder,
      jsonb_build_object('operation', 'initialize-prospect-relationship',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    raise exception 'I1.a: a TRANSITION was performed through the creation door';
  exception when others then
    if sqlerrm !~ 'j102_transition_is_not_an_initialization' then raise; end if;
  end;

  -- I1.b An operation that does not perform this initialization. Binding the pair
  --      is what stops a caller naming a routine operation beside a creation the
  --      store would never route to it.
  begin
    perform ops.j102_initialize_subject('initialize-prospect-relationship',
      jsonb_build_object('relationship:' || v_walk_rel_id, null),
      v_subject_env, v_event_env, 'j102-fixture-key-i1b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    raise exception 'I1.b: an unrelated operation performed a creation';
  exception when others then
    if sqlerrm !~ 'j102_operation_initialization_mismatch' then raise; end if;
  end;

  -- I1.c AN UPDATE WEARING A CREATION'S CLOTHES. A non-null operand says "this
  --      subject exists and is at that version", which is a transition's request
  --      and not a creation's; a caller that could send one here would be
  --      advancing a committed row through a writer that checks no prerequisite.
  begin
    v_rec2 := jsonb_set(v_rec, '{prior_state_digest}', to_jsonb(v_placeholder));
    perform ops.j102_initialize_subject('initialize-prospect-relationship',
      jsonb_build_object('relationship:' || v_walk_rel_id, v_placeholder),
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      v_event_env, 'j102-fixture-key-i1c', v_placeholder,
      jsonb_build_object('operation', 'initialize-prospect-relationship',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    raise exception 'I1.c: a creation carried a non-null compare-and-swap operand and was admitted';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_initialization_is_not_an_update' then raise; end if;
  end;

  -- I1.d A RELATIONSHIP BORN A CLIENT. This is the whole reason the creation
  --      shape is fixed field by field: Q077's client status rests on an active
  --      signed representation agreement, and a creation carries no evidence at
  --      all, so a created row that arrived as a client would be client status
  --      with nothing behind it.
  begin
    v_rec2 := jsonb_set(v_rec, '{state}', jsonb_build_object(
      'subject_kind', 'relationship', 'subject_id', v_walk_rel_id,
      'relationship_state', 'client', 'active_engagement_count', 1, 'party_id', null));
    perform ops.j102_initialize_subject('initialize-prospect-relationship',
      jsonb_build_object('relationship:' || v_walk_rel_id, null),
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      v_event_env, 'j102-fixture-key-i1d', v_placeholder,
      jsonb_build_object('operation', 'initialize-prospect-relationship',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    raise exception 'I1.d: a relationship was CREATED as a client, with no representation agreement anywhere';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_created_subject_field_not_canonical' then raise; end if;
  end;

  -- I1.e A FORGED PROVENANCE on an otherwise canonical creation: the row claims a
  --      partner-only transition established it. ops.j102_subject hands that
  --      field to a reviewer as the row's own account of itself.
  begin
    v_rec2 := jsonb_set(v_rec, '{established_by_transition}', '"record-deal-closing"'::jsonb);
    perform ops.j102_initialize_subject('initialize-prospect-relationship',
      jsonb_build_object('relationship:' || v_walk_rel_id, null),
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      v_event_env, 'j102-fixture-key-i1e', v_placeholder,
      jsonb_build_object('operation', 'initialize-prospect-relationship',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    raise exception 'I1.e: a created row claimed to be established by a transition that never ran';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_subject_provenance_mismatch' then raise; end if;
  end;

  -- I1.f AN EVENT CITING EVIDENCE. A creation rests on none, so a history row
  --      claiming otherwise cites something nothing re-read under this lock.
  begin
    v_evt2 := jsonb_set(v_evt, '{evidence_references}', jsonb_build_array(
      jsonb_build_object('evidence_kind', 'search_initiation',
        'source', 'first_party_record', 'reference', v_fact_id)));
    perform ops.j102_initialize_subject('initialize-prospect-relationship',
      jsonb_build_object('relationship:' || v_walk_rel_id, null),
      v_subject_env,
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-i1f', v_placeholder,
      jsonb_build_object('operation', 'initialize-prospect-relationship',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    raise exception 'I1.f: a creation appended history citing evidence it could not have rested on';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_initialization_cites_evidence' then raise; end if;
  end;

  -- I1.g AN ASSIGNMENT UNDER NO ENGAGEMENT. Q077's client gate is the whole of
  --      this creation's admission, and a caller that simply omits the parent
  --      must not thereby skip it.
  begin
    v_state := jsonb_build_object(
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'engagement_id', v_walk_eng_id, 'assignment_phase', 'research',
      'open_negotiation_count', 0, 'selected_property_id', null,
      'active_lease_draft_target_id', null, 'pending_deal_id', null,
      'multi_target_exception_ref', null);
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'state', v_state, 'established_by_transition', 'initialize-assignment',
      'prior_state_digest', null, 'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_initialized',
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
        'engagement_id', v_walk_eng_id, 'assignment_phase', 'research'),
      'transition_id', 'initialize-assignment', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    perform ops.j102_initialize_subject('initialize-assignment',
      jsonb_build_object('assignment:' || v_walk_asg_id, null),
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-i1g', v_placeholder,
      jsonb_build_object('operation', 'initialize-assignment',
        'reason_id', 'assignment_initialized_under_active_engagement',
        'decision_refs', jsonb_build_array('Q077.D1')));
    raise exception 'I1.g: an assignment was created with no engagement to run under';
  exception when others then
    if sqlerrm !~ 'j102_required_context_not_locked' then raise; end if;
  end;

  if exists (select 1 from ops.j102_subject_current
              where tenant = v_tenant and subject_id in (v_walk_rel_id, v_walk_asg_id)) then
    raise exception 'I1: a refused creation left a row behind';
  end if;

  -- ========================================================================
  -- === J: THE POSITIVE WALK, from a legitimate first row ===================
  --
  -- Everything from here writes real rows through real writers, and every
  -- prerequisite is genuinely satisfied rather than skipped. It is the one part
  -- of this file that is not a refusal.
  -- ========================================================================

  -- J1. THE FIRST ROW. A prospect, created with no evidence because none can bind
  --     to a subject that does not exist yet, and holding no engagements. The
  --     envelopes are the CANONICAL ones I1 broke one field of at a time, so a
  --     reader can see that the only difference between the refusals above and
  --     this success is the field each case changed.
  v_result := ops.j102_initialize_subject('initialize-prospect-relationship',
    jsonb_build_object('relationship:' || v_walk_rel_id, null),
    v_subject_env, v_event_env, 'j102-fixture-key-j1', v_placeholder,
    jsonb_build_object('operation', 'initialize-prospect-relationship',
      'reason_id', 'prospect_relationship_initialized',
      'decision_refs', jsonb_build_array('Q069.D1', 'Q077.D1', 'Q079.D1')));
  v_walk_subjects := v_walk_subjects + 1;
  v_walk_events := v_walk_events + 1;
  if (v_result ->> 'outcome') <> 'initialized'
     or (v_result ->> 'subject_created') <> 'true'
     or (v_result ->> 'creation_shape_enforced') <> 'true'
     or (v_result ->> 'required_context_enforced') <> 'true' then
    raise exception 'J1: the creation receipt does not report what it enforced: %', v_result;
  end if;
  -- THE ANTI-BYPASS HALF OF THE RECEIPT. A creation performs no transition, so it
  -- checked no transition prerequisite and skipped none.
  if (v_result ->> 'transition_applied') <> 'false'
     or (v_result ->> 'transition_prerequisites_bypassed') <> 'false'
     or (v_result ->> 'advances_lifecycle_state') <> 'false'
     or (v_result ->> 'evidence_required') <> 'false' then
    raise exception 'J1: the creation receipt claims something about a transition: %', v_result;
  end if;
  if ops.j102_subject('relationship', v_walk_rel_id) -> 'state' ->> 'relationship_state'
       <> 'prospect' then
    raise exception 'J1: the created relationship is not a prospect';
  end if;
  if (ops.j102_subject('relationship', v_walk_rel_id) ->> 'established_by_transition')
       <> 'initialize-prospect-relationship' then
    raise exception 'J1: the created row does not name the initialization that created it';
  end if;
  select count(*) into v_count from ops.j102_subject_event
   where tenant = v_tenant and subject_id = v_walk_rel_id
     and event_kind = 'relationship_initialized'
     and jsonb_array_length(envelope -> 'record' -> 'evidence_references') = 0;
  if v_count <> 1 then
    raise exception 'J1: the creation appended no history, or its history cites evidence';
  end if;

  -- J1a. A REPLAY RETURNS WHAT LANDED, and writes nothing further.
  v_replay := ops.j102_initialize_subject('initialize-prospect-relationship',
    jsonb_build_object('relationship:' || v_walk_rel_id, null),
    v_subject_env, v_event_env, 'j102-fixture-key-j1', v_placeholder,
    jsonb_build_object('operation', 'initialize-prospect-relationship',
      'reason_id', 'prospect_relationship_initialized',
      'decision_refs', jsonb_build_array('Q069.D1', 'Q077.D1', 'Q079.D1')));
  if (v_replay ->> 'committed_content_digest') is distinct from
     (v_result ->> 'committed_content_digest') then
    raise exception 'J1a: the replay did not return the committed outcome';
  end if;
  select count(*) into v_count from ops.j102_subject_event
   where tenant = v_tenant and subject_id = v_walk_rel_id;
  if v_count <> 1 then
    raise exception 'J1a: the replay appended a second history row';
  end if;

  -- J1b. AND THE SAME ID CANNOT BE CREATED TWICE. The operand says the subject
  --      must be absent; it is not, so the second call refuses rather than
  --      overwriting the first. This is the single-session half of the
  --      concurrency property -- two sessions racing take the same advisory lock,
  --      which one session cannot demonstrate.
  begin
    perform ops.j102_initialize_subject('initialize-prospect-relationship',
      jsonb_build_object('relationship:' || v_walk_rel_id, null),
      v_subject_env, v_event_env, 'j102-fixture-key-j1b', v_placeholder,
      jsonb_build_object('operation', 'initialize-prospect-relationship',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    raise exception 'J1b: the same subject id was created twice';
  exception when serialization_failure then
    if sqlerrm !~ 'j102_subject_already_exists' then raise; end if;
  end;

  -- J2. THE CLIENT GATE IS REAL. The prospect has no engagement, so an assignment
  --     cannot be created under one -- which is Q077's "work before signature
  --     remains prospect work", enforced at the creation door.
  begin
    v_state := jsonb_build_object(
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'engagement_id', v_walk_eng_id, 'assignment_phase', 'research',
      'open_negotiation_count', 0, 'selected_property_id', null,
      'active_lease_draft_target_id', null, 'pending_deal_id', null,
      'multi_target_exception_ref', null);
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'state', v_state, 'established_by_transition', 'initialize-assignment',
      'prior_state_digest', null, 'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_initialized',
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
        'engagement_id', v_walk_eng_id, 'assignment_phase', 'research'),
      'transition_id', 'initialize-assignment', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    perform ops.j102_initialize_subject('initialize-assignment',
      jsonb_build_object('assignment:' || v_walk_asg_id, null,
                         'engagement:' || v_walk_eng_id, null),
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-j2', v_placeholder,
      jsonb_build_object('operation', 'initialize-assignment',
        'reason_id', 'assignment_initialized_under_active_engagement',
        'decision_refs', jsonb_build_array('Q077.D1')));
    raise exception 'J2: an assignment was created under an engagement that does not exist';
  exception when others then
    if sqlerrm !~ 'j102_required_context_not_locked' then raise; end if;
  end;

  -- === J3: THE ETL LEG, against WHICHEVER F01 DOCUMENT WRITER THIS DATABASE HAS
  --
  -- ONE SYNTHETIC ENGAGEMENT LETTER, through F01's own writer. Q077's "active
  -- signed ETL" is these axes and no others: fully executed, effective and
  -- current. F01 remains the sole authority for all three; this fixture only
  -- records a document that carries them, and invents no approval and no role.
  --
  -- WHICH OVERLOAD, AND WHY BOTH ARE HANDLED. domain.sql ships a four-argument
  -- ops.f01_record_document and DROPS it as soon as the six-argument successor
  -- from ops/document-derivative-registration.candidate.sql is present, so on any
  -- database carrying the current document-source hunk the four-argument form
  -- does not exist. An earlier revision of this fixture gated J3 on that dropped
  -- overload alone, which meant the whole walk silently skipped on exactly the
  -- configuration the document slice targets. Both are probed by exact signature,
  -- the six-argument one first because it is the current writer.
  --
  -- THE PROVENANCE STATEMENT IS REAL, NOT A PLACEHOLDER. A synthetic ETL signed
  -- by a counterparty is an ORIGINAL first-party document: it is derived from no
  -- stored artifact, so it names no source, registers no derivative link and
  -- claims no producer workflow, and it carries the basis statement that answer
  -- requires. That is the honest shape for what this fixture actually has, and it
  -- is why this leg fabricates no artifact and no derivative coverage -- the
  -- writer's own answer says `establishes_coverage: false`, and nothing here
  -- reads it as anything else.
  --
  -- AND THE CALL IS NOT WRAPPED IN A CATCH-ALL. An earlier revision degraded any
  -- F01 error into "J3 SKIPPED", so a wrong envelope shape reported as a missing
  -- prerequisite. A prerequisite that is genuinely absent is named and skipped
  -- BELOW, before anything is attempted; once the writer is present, whatever it
  -- says is the result, and a refusal fails this fixture rather than muting it.
  if not v_partner then
    raise notice 'J3 SKIPPED -- UNMET PREREQUISITE: this session is a %, and the evidence -> subject association (ops.j102_record_evidence_subject_link) is partner-only. The walk stops at the prospect; nothing here mints an identity to get past that. J3 onward, and therefore the client gate, are UNPROVEN in this run.',
      v_class;
  elsif to_regprocedure('ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)') is null
        and to_regprocedure('ops.f01_record_document(jsonb,text,text,text)') is null then
    raise notice 'J3 SKIPPED -- UNMET PREREQUISITE: this database carries NEITHER ops.f01_record_document overload, so no F01 document exists for Q077 to rest on and none is invented here. Apply domain.sql (and, for the six-argument writer, ops/document-derivative-registration.candidate.sql). The walk stops at the prospect and J3 onward is UNPROVEN in this run.';
  else
    v_doc_record := jsonb_build_object(
      'schema_version', 'doctorcre-v5-f01-stored-document-version.v1',
      'tenant', v_tenant,
      'document_class', 'engagement_letter',
      'neon_identity', jsonb_build_object(
        'document_id', v_walk_doc_id, 'version_no', 1,
        'content_digest', v_walk_doc_digest),
      'object_storage_identity', jsonb_build_object(
        'object_key', 'j102/fixture/synthetic-etl-1',
        'content_digest', v_walk_doc_digest,
        'byte_length', 2048, 'sealed', true),
      'onedrive_identity', jsonb_build_object(
        'drive_id', 'j102-fixture-drive', 'item_id', 'j102-fixture-item',
        'content_digest', v_walk_doc_digest,
        'filing_state', 'filed'),
      'preparation_state', 'approved_for_delivery',
      'delivery_state', 'delivered',
      'signature_state', 'fully_executed',
      'validity_state', 'effective',
      'version_state', 'current',
      'official_filing_state', 'filed',
      'prior_document_digest', null,
      'recorded_by', v_actor,
      'recorded_at', v_now);
    v_doc_envelope := jsonb_build_object(
      'schema_version', 'doctorcre-v5-f01-stored-record-envelope.v1',
      'record_kind', 'stored_document_version', 'tenant', v_tenant,
      'record', v_doc_record, 'record_digest', ops.f01_digest_jsonb(v_doc_record));
    if to_regprocedure('ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)') is not null then
      v_prov_record := jsonb_build_object(
        'schema_version', 'doctorcre-v5-f01-stored-document-source-provenance.v1',
        'tenant', v_tenant,
        'document_id', v_walk_doc_id,
        'version_no', 1,
        'document_digest', ops.f01_digest_jsonb(v_doc_record),
        -- THE HONEST ANSWER FOR WHAT THIS FIXTURE HAS. Not derived, so no source
        -- artifact, no derivative link, no producer workflow -- and a stated
        -- basis, which the non-derived half is required to carry precisely so a
        -- claim with no basis cannot be read as a fact by whoever finds it next.
        'provenance_state', 'original_first_party',
        'source_artifact_digest', null,
        'derivative_link_digest', null,
        'derivative_kind', null,
        'derivative_id', null,
        'producer_workflow', null,
        'producer_run_ref', null,
        'basis_statement', 'synthetic J102 fixture engagement letter, authored in this transaction and rolled back with it',
        'registration_is_provenance', true,
        'source_artifact_inferred_from_document_bytes', false,
        'source_artifact_inferred_from_onedrive_identity', false,
        'is_exhaustive_inventory', false,
        'establishes_coverage', false,
        'permits_deletion', false,
        'recorded_by', v_actor,
        'recorded_at', v_now);
      v_result := ops.f01_record_document(
        v_doc_envelope,
        -- The envelope repeats the three "claims nothing" flags at ITS level too:
        -- F01's f01_docsource_claims_nothing constraint reads both, so an
        -- envelope that states them only inside the record is refused. Found on
        -- the first run against F01's numbered migration rather than domain.sql.
        jsonb_build_object(
          'schema_version', 'doctorcre-v5-f01-stored-record-envelope.v1',
          'record_kind', 'stored_document_source_provenance', 'tenant', v_tenant,
          'record', v_prov_record, 'record_digest', ops.f01_digest_jsonb(v_prov_record),
          'is_exhaustive_inventory', false, 'establishes_coverage', false,
          'permits_deletion', false),
        -- NO DERIVATIVE LINK. An original names no source, and the writer refuses
        -- a link on a non-derived document by name, so passing one would be
        -- inventing exactly the derivative coverage this fixture must not claim.
        null,
        null, 'j102-fixture-key-j3-doc', v_placeholder);
      if (v_result ->> 'provenance_state') <> 'original_first_party'
         or (v_result ->> 'derivative_registration_bound') <> 'false'
         or (v_result ->> 'establishes_coverage') <> 'false' then
        raise exception 'J3: the F01 writer recorded the ETL as something other than an unregistered original: %',
          v_result;
      end if;
      raise notice 'J3: the ETL was recorded through the SIX-ARGUMENT ops.f01_record_document, as an original_first_party document with a stated basis and no derivative registration.';
    else
      perform ops.f01_record_document(
        v_doc_envelope, null, 'j102-fixture-key-j3-doc', v_placeholder);
      raise notice 'J3: the ETL was recorded through the FOUR-ARGUMENT ops.f01_record_document. This database does not carry the document-source hunk, so no provenance statement was required or written.';
    end if;
    v_walk_ok := true;
  end if;

  if v_walk_ok then
    -- J3a. THE ASSOCIATION. F01 owns document identity and carries no lifecycle
    --      binding, so a partner states which subject this exact document VERSION
    --      belongs to. It asserts nothing about the document's own states.
    v_rec := jsonb_build_object(
      'schema_version', 'doctorcre-v5-j102-stored-evidence-subject-link.v1',
      'tenant', v_tenant,
      'evidence_source', 'f01_document', 'evidence_ref', v_walk_doc_id,
      'version_no', 1, 'content_digest', v_walk_doc_digest,
      'subject_kind', 'relationship', 'subject_id', v_walk_rel_id,
      'associated_by', v_actor, 'associated_by_authorization_class', v_class,
      'associated_at', v_now,
      'advances_lifecycle_state', false, 'creates_document', false,
      'asserts_document_state', false);
    v_result := ops.j102_record_evidence_subject_link(
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_evidence_subject_link',
        'tenant', v_tenant, 'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-j3-link', v_placeholder);
    v_link_digest := v_result ->> 'link_digest';

    -- J3b. THE ETL TRANSITION. Client status and the Engagement land together or
    --      neither does (Q069/Q082), and the engagement is a COUPLED creation of
    --      this transition rather than anything the creation door may make.
    v_rel_digest := ops.j102_subject('relationship', v_walk_rel_id) ->> 'state_digest';
    v_state := jsonb_build_object(
      'subject_kind', 'relationship', 'subject_id', v_walk_rel_id,
      'relationship_state', 'client', 'active_engagement_count', 1, 'party_id', null);
    v_rec := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'relationship', 'subject_id', v_walk_rel_id,
      'state', v_state,
      'established_by_transition', 'establish-client-and-engagement',
      'prior_state_digest', v_rel_digest,
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'engagement', 'subject_id', v_walk_eng_id,
      'state', jsonb_build_object(
        'subject_kind', 'engagement', 'subject_id', v_walk_eng_id,
        'relationship_id', v_walk_rel_id, 'engagement_state', 'active',
        'representation_basis', 'signed_engagement_letter',
        'effective_from', null, 'effective_to', null),
      'established_by_transition', 'establish-client-and-engagement',
      'prior_state_digest', null,
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_manifest := jsonb_build_array(jsonb_build_object(
      'evidence_kind', 'signed_engagement_letter', 'source', 'f01_document',
      'reader', 'ops.f01_read.document',
      'selector', jsonb_build_object('document_id', v_walk_doc_id),
      'expected_version_no', 1,
      'expected_content_digest', v_walk_doc_digest,
      'binding', jsonb_build_object('subject_kind', 'relationship',
                                    'subject_id', v_walk_rel_id),
      'expected_link_digest', v_link_digest));
    v_evt := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'client_status_established',
        'subject_kind', 'relationship', 'subject_id', v_walk_rel_id,
        'representation_basis', 'signed_engagement_letter',
        'evidence_reference', v_walk_doc_id),
      'transition_id', 'establish-client-and-engagement',
      'evidence_references', jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'signed_engagement_letter', 'source', 'f01_document',
        'reference', v_walk_doc_id)),
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'engagement_opened',
        'subject_kind', 'engagement', 'subject_id', v_walk_eng_id,
        'relationship_id', v_walk_rel_id,
        'representation_basis', 'signed_engagement_letter'),
      'transition_id', 'establish-client-and-engagement',
      'evidence_references', jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'signed_engagement_letter', 'source', 'f01_document',
        'reference', v_walk_doc_id)),
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    v_result := ops.j102_apply_transition(
      'establish-client-and-engagement',
      jsonb_build_object('relationship:' || v_walk_rel_id, v_rel_digest,
                         'engagement:' || v_walk_eng_id, null),
      jsonb_build_array(
        jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
          'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
          'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      jsonb_build_array(
        jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
          'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
          'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest, 'j102-fixture-key-j3-etl', v_placeholder,
      jsonb_build_object('operation', 'record-representation-agreement',
        'reason_id', 'active_representation_creates_client_and_engagement',
        'coupled_facts', jsonb_build_array('relationship.relationship_state'),
        'decision_refs', jsonb_build_array('Q069.D1')));
    v_walk_events := v_walk_events + 2;
    v_walk_subjects := v_walk_subjects + 1;
    if (v_result ->> 'primary_subject_created') <> 'false'
       or (v_result ->> 'prerequisites_checked') <> 'true' then
      raise exception 'J3b: the ETL receipt does not report a loaded primary and a checked prerequisite: %',
        v_result;
    end if;
    if ops.j102_subject('relationship', v_walk_rel_id) -> 'state' ->> 'relationship_state'
         <> 'client' then
      raise exception 'J3b: the signed effective ETL did not create client status';
    end if;
    if ops.j102_subject('engagement', v_walk_eng_id) -> 'state' ->> 'engagement_state'
         <> 'active' then
      raise exception 'J3b: the coupled engagement did not land beside the client status';
    end if;

    -- J3c. THE ASSIGNMENT IS CREATED under that engagement, at `research`, with
    --      the client relationship verified through the engagement's own field.
    v_rel_digest := ops.j102_subject('relationship', v_walk_rel_id) ->> 'state_digest';
    v_eng_digest := ops.j102_subject('engagement', v_walk_eng_id) ->> 'state_digest';
    v_state := jsonb_build_object(
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'engagement_id', v_walk_eng_id, 'assignment_phase', 'research',
      'open_negotiation_count', 0, 'selected_property_id', null,
      'active_lease_draft_target_id', null, 'pending_deal_id', null,
      'multi_target_exception_ref', null);
    v_rec := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'state', v_state, 'established_by_transition', 'initialize-assignment',
      'prior_state_digest', null, 'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_evt := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_initialized',
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
        'engagement_id', v_walk_eng_id, 'assignment_phase', 'research'),
      'transition_id', 'initialize-assignment', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    v_result := ops.j102_initialize_subject('initialize-assignment',
      jsonb_build_object('assignment:' || v_walk_asg_id, null,
                         'engagement:' || v_walk_eng_id, v_eng_digest,
                         'relationship:' || v_walk_rel_id, v_rel_digest),
      jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
        'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
        'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-j3-asg', v_placeholder,
      jsonb_build_object('operation', 'initialize-assignment',
        'reason_id', 'assignment_initialized_under_active_engagement',
        'decision_refs', jsonb_build_array('Q077.D1')));
    v_walk_subjects := v_walk_subjects + 1;
    v_walk_events := v_walk_events + 1;
    if (v_result ->> 'parent_subjects_locked_and_unmoved') <> 'true' then
      raise exception 'J3c: the assignment creation does not report its verified parents';
    end if;

    -- J3d. THE MANDATE RECORD, and then the OPEN. The created assignment bought
    --      none of this: `open-assignment` still reads a first-party mandate bound
    --      to that assignment, still requires the active engagement and the client
    --      relationship, and still declares the scope.
    v_rec := jsonb_build_object(
      'schema_version', v_fact_schema, 'tenant', v_tenant,
      'record_kind', 'assignment_mandate', 'record_id', v_walk_fact_id,
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'reason', null, 'detail', 'synthetic fixture mandate for the walk',
      'closing_date', null, 'supporting_document_id', null,
      'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
      'recorded_at', v_now, 'advances_lifecycle_state', false);
    perform ops.j102_record_first_party_fact(
      jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
        'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-j3-mandate', v_placeholder);
    v_fact3_digest := ops.j102_first_party_record('assignment_mandate', v_walk_fact_id)
                        ->> 'record_digest';
    v_asg_digest := ops.j102_subject('assignment', v_walk_asg_id) ->> 'state_digest';
    v_state := jsonb_build_object(
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'engagement_id', v_walk_eng_id, 'assignment_phase', 'search',
      'open_negotiation_count', 0, 'selected_property_id', null,
      'active_lease_draft_target_id', null, 'pending_deal_id', null,
      'multi_target_exception_ref', null);
    v_rec := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'state', v_state, 'established_by_transition', 'open-assignment',
      'prior_state_digest', v_asg_digest, 'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_evt := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_opened',
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
        'engagement_id', v_walk_eng_id, 'assignment_phase', 'search',
        'evidence_reference', v_walk_fact_id),
      'transition_id', 'open-assignment',
      'evidence_references', jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reference', v_walk_fact_id)),
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    v_result := ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest,
                         'engagement:' || v_walk_eng_id, v_eng_digest,
                         'relationship:' || v_walk_rel_id, v_rel_digest),
      jsonb_build_array(jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
        'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      jsonb_build_array(jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
        'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_walk_fact_id),
        'expected_record_digest', v_fact3_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_walk_asg_id))),
      'j102-fixture-key-j3-open', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment',
        'reason_id', 'search_initiation_opens_assignment',
        'coupled_facts', jsonb_build_array('assignment.assignment_phase'),
        'decision_refs', jsonb_build_array('Q080.D1')));
    v_walk_events := v_walk_events + 1;
    if ops.j102_subject('assignment', v_walk_asg_id) -> 'state' ->> 'assignment_phase'
         <> 'search' then
      raise exception 'J3d: the mandate did not open the assignment into search';
    end if;
    if (v_result ->> 'evidence_bound_to_primary_subject') <> 'true' then
      raise exception 'J3d: the open receipt does not report the evidence binding it enforced';
    end if;

    -- J3e. A NEGOTIATION IS DRAFTED under the open assignment. Q095's concurrent
    --      LOIs start here, and a draft is not a submission.
    v_asg_digest := ops.j102_subject('assignment', v_walk_asg_id) ->> 'state_digest';
    v_state := jsonb_build_object(
      'subject_kind', 'property_negotiation', 'subject_id', v_walk_neg_id,
      'assignment_id', v_walk_asg_id, 'property_id', v_walk_prop_id,
      'negotiation_state', 'loi_drafted');
    v_rec := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'property_negotiation', 'subject_id', v_walk_neg_id,
      'state', v_state, 'established_by_transition', 'initialize-property-negotiation',
      'prior_state_digest', null, 'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_evt := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'property_negotiation_initialized',
        'subject_kind', 'property_negotiation', 'subject_id', v_walk_neg_id,
        'assignment_id', v_walk_asg_id, 'property_id', v_walk_prop_id,
        'negotiation_state', 'loi_drafted'),
      'transition_id', 'initialize-property-negotiation',
      'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    perform ops.j102_initialize_subject('initialize-property-negotiation',
      jsonb_build_object('property_negotiation:' || v_walk_neg_id, null,
                         'assignment:' || v_walk_asg_id, v_asg_digest),
      jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
        'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
        'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-j3-neg', v_placeholder,
      jsonb_build_object('operation', 'initialize-property-negotiation',
        'reason_id', 'property_negotiation_initialized_under_assignment',
        'decision_refs', jsonb_build_array('Q095.D1')));
    v_walk_subjects := v_walk_subjects + 1;
    v_walk_events := v_walk_events + 1;
    if ops.j102_subject('property_negotiation', v_walk_neg_id) -> 'state' ->> 'negotiation_state'
         <> 'loi_drafted' then
      raise exception 'J3e: the created negotiation is not an LOI draft';
    end if;

    -- J3f. AND THE EVIDENCE GATE AFTERWARDS IS UNTOUCHED. This payload is correct
    --      in EVERY other respect -- the right transition, the whole coupled
    --      subject set, the right event, the right provenance, live
    --      compare-and-swap operands read back a line ago -- and carries no
    --      evidence manifest. It must refuse for exactly that reason, which is
    --      what makes "the draft the creation door made is a draft and not an
    --      LOI" a property of the writer rather than of this file's prose.
    v_asg_digest := ops.j102_subject('assignment', v_walk_asg_id) ->> 'state_digest';
    v_rec := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'property_negotiation', 'subject_id', v_walk_neg_id,
      'state', jsonb_build_object(
        'subject_kind', 'property_negotiation', 'subject_id', v_walk_neg_id,
        'assignment_id', v_walk_asg_id, 'property_id', v_walk_prop_id,
        'negotiation_state', 'loi_submitted'),
      'established_by_transition', 'record-loi-submission',
      'prior_state_digest',
        ops.j102_subject('property_negotiation', v_walk_neg_id) ->> 'state_digest',
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'state', jsonb_build_object(
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
        'engagement_id', v_walk_eng_id, 'assignment_phase', 'negotiation',
        'open_negotiation_count', 1, 'selected_property_id', null,
        'active_lease_draft_target_id', null, 'pending_deal_id', null,
        'multi_target_exception_ref', null),
      'established_by_transition', 'record-loi-submission',
      'prior_state_digest', v_asg_digest,
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_evt := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'loi_submitted',
        'subject_kind', 'property_negotiation', 'subject_id', v_walk_neg_id,
        'assignment_id', v_walk_asg_id, 'property_id', v_walk_prop_id,
        'evidence_reference', v_walk_doc_id),
      'transition_id', 'record-loi-submission',
      'evidence_references', jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'submitted_loi', 'source', 'f01_document',
        'reference', v_walk_doc_id)),
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    begin
      perform ops.j102_apply_transition(
        'record-loi-submission',
        jsonb_build_object(
          'property_negotiation:' || v_walk_neg_id,
            ops.j102_subject('property_negotiation', v_walk_neg_id) ->> 'state_digest',
          'assignment:' || v_walk_asg_id, v_asg_digest),
        jsonb_build_array(
          jsonb_build_object('schema_version', v_env_schema,
            'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
            'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
            'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
          jsonb_build_object('schema_version', v_env_schema,
            'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
            'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
            'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
        jsonb_build_array(jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
          'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
        '[]'::jsonb, 'j102-fixture-key-j3-loi', v_placeholder,
        jsonb_build_object('operation', 'record-loi-submission',
          'reason_id', 'loi_submission_moves_assignment_to_negotiation',
          'coupled_facts', jsonb_build_array('property_negotiation.negotiation_state'),
          'decision_refs', jsonb_build_array('Q095.D1')));
      raise exception 'J3f: an LOI submission was applied with no evidence at all';
    exception when others then
      if sqlerrm !~ 'j102_evidence_recheck_required' then raise; end if;
    end;
    if ops.j102_subject('property_negotiation', v_walk_neg_id) -> 'state' ->> 'negotiation_state'
         <> 'loi_drafted' then
      raise exception 'J3f: the refused submission moved the negotiation anyway';
    end if;

    -- ======================================================================
    -- === J4: THE NEGATIVE CONTEXT CASES, on rows that now exist ============
    --
    -- The chained hop -- `identified_by: {source: "context"}` -- resolves the
    -- relationship from the ENGAGEMENT'S OWN FIELD rather than from whichever
    -- relationship a caller supplies, and until the walk existed there was no way
    -- to drive that negatively: I1.g and J2 both refuse at the FIRST hop, for
    -- want of an engagement, and never reach the second.
    -- ======================================================================

    -- J4a. A SECOND PROSPECT, created legitimately, so the case below has a real
    --      relationship row to point at rather than a placeholder digest.
    v_state := jsonb_build_object(
      'subject_kind', 'relationship', 'subject_id', v_walk_rel_id_2,
      'relationship_state', 'prospect', 'active_engagement_count', 0, 'party_id', null);
    v_rec := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'relationship', 'subject_id', v_walk_rel_id_2,
      'state', v_state, 'established_by_transition', 'initialize-prospect-relationship',
      'prior_state_digest', null, 'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_evt := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'relationship_initialized',
        'subject_kind', 'relationship', 'subject_id', v_walk_rel_id_2,
        'relationship_state', 'prospect'),
      'transition_id', 'initialize-prospect-relationship',
      'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    perform ops.j102_initialize_subject('initialize-prospect-relationship',
      jsonb_build_object('relationship:' || v_walk_rel_id_2, null),
      jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
        'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      jsonb_build_object('schema_version', v_env_schema,
        'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
        'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-j4a', v_placeholder,
      jsonb_build_object('operation', 'initialize-prospect-relationship',
        'reason_id', 'prospect_relationship_initialized',
        'decision_refs', jsonb_build_array('Q069.D1')));
    v_walk_subjects := v_walk_subjects + 1;
    v_walk_events := v_walk_events + 1;

    -- J4b. THE CHAINED HOP, NEGATIVELY. The engagement is the walk's, and it is
    --      active, so the FIRST hop passes. The relationship supplied is the
    --      second prospect -- a real, locked, unmoved row of the right kind. The
    --      map resolves the relationship from `engagement.relationship_id`, which
    --      names the walk's CLIENT, and that row is not in this operand map: the
    --      second hop refuses. A writer that read "whichever relationship the
    --      caller sent" would have found a perfectly good row and gone on.
    v_rel_digest := ops.j102_subject('relationship', v_walk_rel_id) ->> 'state_digest';
    v_eng_digest := ops.j102_subject('engagement', v_walk_eng_id) ->> 'state_digest';
    begin
      v_state := jsonb_build_object(
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id_2,
        'engagement_id', v_walk_eng_id, 'assignment_phase', 'research',
        'open_negotiation_count', 0, 'selected_property_id', null,
        'active_lease_draft_target_id', null, 'pending_deal_id', null,
        'multi_target_exception_ref', null);
      v_rec2 := jsonb_build_object(
        'schema_version', v_subject_schema, 'tenant', v_tenant,
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id_2,
        'state', v_state, 'established_by_transition', 'initialize-assignment',
        'prior_state_digest', null, 'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
      v_evt2 := jsonb_build_object(
        'schema_version', v_event_schema, 'tenant', v_tenant,
        'event', jsonb_build_object('schema_version', v_ev_schema,
          'event_kind', 'assignment_initialized',
          'subject_kind', 'assignment', 'subject_id', v_walk_asg_id_2,
          'engagement_id', v_walk_eng_id, 'assignment_phase', 'research'),
        'transition_id', 'initialize-assignment', 'evidence_references', '[]'::jsonb,
        'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
      perform ops.j102_initialize_subject('initialize-assignment',
        jsonb_build_object('assignment:' || v_walk_asg_id_2, null,
                           'engagement:' || v_walk_eng_id, v_eng_digest,
                           'relationship:' || v_walk_rel_id_2,
                             ops.j102_subject('relationship', v_walk_rel_id_2) ->> 'state_digest'),
        jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
          'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
          'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        'j102-fixture-key-j4b', v_placeholder,
        jsonb_build_object('operation', 'initialize-assignment',
          'reason_id', 'assignment_initialized_under_active_engagement',
          'decision_refs', jsonb_build_array('Q077.D1')));
      raise exception 'J4b: the second context hop read the relationship the CALLER supplied rather than the one the engagement names';
    exception when others then
      if sqlerrm !~ 'j102_required_context_not_locked' then raise; end if;
    end;

    -- J4c. AND THE OPERAND SET IS CLOSED BY IDENTITY. This is a prospect
    --      creation, which consults NO parent at all, and it carries an extra
    --      `relationship:` operand -- the right KIND, a real row, and a row
    --      nothing in this call reads. It would be locked, compare-and-swapped
    --      and then reported in the receipt's own expected_state_digests beside
    --      `required_context_enforced: true`. The kind-only check that shipped
    --      first admitted exactly this.
    begin
      v_state := jsonb_build_object(
        'subject_kind', 'relationship', 'subject_id', v_walk_rel_id_3,
        'relationship_state', 'prospect', 'active_engagement_count', 0, 'party_id', null);
      v_rec2 := jsonb_build_object(
        'schema_version', v_subject_schema, 'tenant', v_tenant,
        'subject_kind', 'relationship', 'subject_id', v_walk_rel_id_3,
        'state', v_state, 'established_by_transition', 'initialize-prospect-relationship',
        'prior_state_digest', null, 'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
      v_evt2 := jsonb_build_object(
        'schema_version', v_event_schema, 'tenant', v_tenant,
        'event', jsonb_build_object('schema_version', v_ev_schema,
          'event_kind', 'relationship_initialized',
          'subject_kind', 'relationship', 'subject_id', v_walk_rel_id_3,
          'relationship_state', 'prospect'),
        'transition_id', 'initialize-prospect-relationship',
        'evidence_references', '[]'::jsonb,
        'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
      perform ops.j102_initialize_subject('initialize-prospect-relationship',
        jsonb_build_object('relationship:' || v_walk_rel_id_3, null,
                           'relationship:' || v_walk_rel_id_2,
                             ops.j102_subject('relationship', v_walk_rel_id_2) ->> 'state_digest'),
        jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
          'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('schema_version', v_env_schema,
          'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
          'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        'j102-fixture-key-j4c', v_placeholder,
        jsonb_build_object('operation', 'initialize-prospect-relationship',
          'reason_id', 'prospect_relationship_initialized',
          'decision_refs', jsonb_build_array('Q069.D1')));
      raise exception 'J4c: a row nothing in this creation reads was locked and compare-and-swapped beside it';
    exception when others then
      if sqlerrm !~ 'j102_operand_subject_not_read_by_initialization' then raise; end if;
    end;

    -- J4d. WHAT IS STILL UNPROVEN HERE, NAMED. `j102_required_context_not_met` --
    --      the arm that enforces `engagement_state: active` and
    --      `relationship_state: client` on a LOCKED row -- is driven by no case in
    --      this file, and it cannot be: no shipped door writes `expired`,
    --      `terminated`, `client_paused` or `client_ended` at all, and the
    --      assignment phases outside the negotiation's admitted set (`committed`,
    --      `concluded`) are reached only through a commitment whose evidence is a
    --      counterparty acceptance artifact this fixture does not fabricate. The
    --      cases above prove the RESOLUTION (which row is read); the condition
    --      arm is proved against the kernel in the Node parity suite's refusal
    --      walks instead, and is stated here rather than left to be inferred from
    --      the cases that are present.
    raise notice 'J4: UNPROVEN IN THIS FILE -- j102_required_context_not_met. No shipped transition produces a non-active engagement or a non-client relationship, and committed/concluded assignments need a counterparty acceptance artifact this fixture does not fabricate. The context RESOLUTION is proved by J4b and the operand identity check by J4c; the condition arm is covered against the kernel by the Node parity suite, not here.';

    -- ======================================================================
    -- === U1: the target-value rewrites, NOW DECIDED AGAINST A COMMITTED ROW
    --
    -- These are the payloads the second root correction exists for: a permitted
    -- field carrying a value the kernel would never produce. They are decided
    -- against the COMMITTED row, and until the creation door existed no committed
    -- row could exist here, so they refused earlier -- at the compare-and-swap,
    -- for want of an assignment -- and said so. The walk's assignment is real,
    -- open at `search`, and its digest was read back a line ago, so the
    -- compare-and-swap PASSES and the target check is what answers.
    -- ======================================================================
    v_asg_digest := ops.j102_subject('assignment', v_walk_asg_id) ->> 'state_digest';
    -- The canonical open-assignment payload for the walk's assignment, re-opened
    -- into `search`. Every case below breaks exactly one thing in it.
    v_state := jsonb_build_object(
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'engagement_id', v_walk_eng_id, 'assignment_phase', 'search',
      'open_negotiation_count', 0, 'selected_property_id', null,
      'active_lease_draft_target_id', null, 'pending_deal_id', null,
      'multi_target_exception_ref', null);
    v_evt := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_opened',
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
        'engagement_id', v_walk_eng_id, 'assignment_phase', 'search',
        'evidence_reference', v_walk_fact_id),
      'transition_id', 'open-assignment',
      'evidence_references', jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reference', v_walk_fact_id)),
      'recorded_by', v_actor, 'sponsoring_partner', v_sponsor, 'recorded_at', v_now);
    v_events := jsonb_build_array(jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
      'tenant', v_tenant, 'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder));
    v_manifest := jsonb_build_array(jsonb_build_object(
      'evidence_kind', 'search_initiation', 'source', 'first_party_record',
      'reader', 'ops.j102_first_party_record',
      'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                     'record_id', v_walk_fact_id),
      'expected_record_digest', v_fact3_digest,
      'binding', jsonb_build_object('subject_kind', 'assignment',
                                    'subject_id', v_walk_asg_id)));

    -- U1a. A ROUTINE OPEN-ASSIGNMENT CALL, PROPOSING A COMMITMENT: the phase, the
    --      selected property, the lease-draft target and a pending deal id, none
    --      of which any evidence in this manifest establishes.
    v_refusal := null;
    begin
      v_rec2 := jsonb_build_object(
        'schema_version', v_subject_schema, 'tenant', v_tenant,
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
        'state', jsonb_build_object(
          'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
          'engagement_id', v_walk_eng_id, 'assignment_phase', 'committed',
          'open_negotiation_count', 0, 'selected_property_id', v_walk_prop_id,
          'active_lease_draft_target_id', v_walk_prop_id,
          'pending_deal_id', v_deal_id, 'multi_target_exception_ref', null),
        'established_by_transition', 'open-assignment',
        'prior_state_digest', v_asg_digest,
        'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
      perform ops.j102_apply_transition(
        'open-assignment',
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest,
                           'engagement:' || v_walk_eng_id, v_eng_digest,
                           'relationship:' || v_walk_rel_id, v_rel_digest),
        jsonb_build_array(jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
        v_events, v_manifest,
        'j102-fixture-key-u1a', v_placeholder,
        jsonb_build_object('operation', 'open-cre-assignment',
          'reason_id', 'search_initiation_opens_assignment',
          'coupled_facts', jsonb_build_array('assignment.assignment_phase'),
          'decision_refs', jsonb_build_array('Q080.D1')));
      raise exception 'U1a: a routine open-assignment call committed an assignment to a property and a pending deal';
    exception when others then
      v_refusal := sqlerrm;
      if sqlerrm !~ 'j102_transition_effect_not_canonical'
         and sqlerrm !~ 'j102_field_not_movable_by_transition' then
        raise;
      end if;
    end;
    raise notice 'U1a PROVED against a committed row: %', v_refusal;
    if ops.j102_subject('assignment', v_walk_asg_id) -> 'state' ->> 'assignment_phase'
         <> 'search' then
      raise exception 'U1a: the refused masquerade moved the assignment anyway';
    end if;

    -- U1b. The same shape, one field wide: a permitted field DELETED rather than
    --      moved, which is how a state axis stops existing.
    v_refusal := null;
    begin
      v_rec2 := jsonb_build_object(
        'schema_version', v_subject_schema, 'tenant', v_tenant,
        'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
        'state', v_state - 'assignment_phase',
        'established_by_transition', 'open-assignment',
        'prior_state_digest', v_asg_digest,
        'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
      perform ops.j102_apply_transition(
        'open-assignment',
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest,
                           'engagement:' || v_walk_eng_id, v_eng_digest,
                           'relationship:' || v_walk_rel_id, v_rel_digest),
        jsonb_build_array(jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
        v_events, v_manifest,
        'j102-fixture-key-u1b', v_placeholder,
        jsonb_build_object('operation', 'open-cre-assignment',
          'reason_id', 'search_initiation_opens_assignment',
          'coupled_facts', jsonb_build_array('assignment.assignment_phase'),
          'decision_refs', jsonb_build_array('Q080.D1')));
      raise exception 'U1b: an assignment was written with no phase at all';
    exception when others then
      v_refusal := sqlerrm;
      if sqlerrm !~ 'j102_transition_effect_missing'
         and sqlerrm !~ 'j102_transition_effect_not_canonical'
         and sqlerrm !~ 'j102_field_not_movable_by_transition' then
        raise;
      end if;
    end;
    raise notice 'U1b PROVED against a committed row: %', v_refusal;

    -- ======================================================================
    -- === U2: the history that lies inside a correct event, ALSO DECIDED NOW
    --
    -- HIGH-6 and the nested-payload half of the same class. Each payload carries
    -- the RIGHT event kind on the RIGHT subject in the RIGHT number -- every
    -- check A5 and A10 cover passes -- and lies about what is INSIDE: the
    -- evidence the event names, and the evidence the record cites as what the
    -- transition rested on. Both are decided against the evidence RE-READ under
    -- the lock, and the mandate for the OTHER assignment (seeded above, authentic
    -- and unmoved and about somebody else) is what makes the lie plausible.
    -- ======================================================================
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'state', v_state, 'established_by_transition', 'open-assignment',
      'prior_state_digest', v_asg_digest,
      'updated_by', v_actor, 'sponsoring_partner', v_sponsor, 'updated_at', v_now);
    v_subjects := jsonb_build_array(jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
      'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder));

    -- U2a. The event NAMES a mandate the transition never rested on.
    v_refusal := null;
    begin
      v_evt2 := jsonb_set(v_evt, '{event,evidence_reference}', to_jsonb(v_fact_id_2));
      perform ops.j102_apply_transition(
        'open-assignment',
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest,
                           'engagement:' || v_walk_eng_id, v_eng_digest,
                           'relationship:' || v_walk_rel_id, v_rel_digest),
        v_subjects,
        jsonb_build_array(jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
          'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
        v_manifest,
        'j102-fixture-key-u2a', v_placeholder,
        jsonb_build_object('operation', 'open-cre-assignment',
          'reason_id', 'search_initiation_opens_assignment',
          'coupled_facts', jsonb_build_array('assignment.assignment_phase'),
          'decision_refs', jsonb_build_array('Q080.D1')));
      raise exception 'U2a: an event named a mandate the transition never rested on';
    exception when others then
      v_refusal := sqlerrm;
      if sqlerrm !~ 'j102_event_detail_not_canonical' then raise; end if;
    end;
    raise notice 'U2a PROVED at the event-detail check: %', v_refusal;

    -- U2b. The history CITES a mandate that IS authentic, IS unmoved and belongs
    --      to somebody else -- the A1 payload, moved from the manifest into the
    --      record, where nothing used to compare it.
    v_refusal := null;
    begin
      v_evt2 := jsonb_set(v_evt, '{evidence_references}', jsonb_build_array(
        jsonb_build_object('evidence_kind', 'search_initiation',
          'source', 'first_party_record', 'reference', v_fact_id_2)));
      perform ops.j102_apply_transition(
        'open-assignment',
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest,
                           'engagement:' || v_walk_eng_id, v_eng_digest,
                           'relationship:' || v_walk_rel_id, v_rel_digest),
        v_subjects,
        jsonb_build_array(jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
          'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
        v_manifest,
        'j102-fixture-key-u2b', v_placeholder,
        jsonb_build_object('operation', 'open-cre-assignment',
          'reason_id', 'search_initiation_opens_assignment',
          'coupled_facts', jsonb_build_array('assignment.assignment_phase'),
          'decision_refs', jsonb_build_array('Q080.D1')));
      raise exception 'U2b: history cited evidence this transition never re-read';
    exception when others then
      v_refusal := sqlerrm;
      if sqlerrm !~ 'j102_event_evidence_reference_not_rechecked' then raise; end if;
    end;
    raise notice 'U2b PROVED at the citation check: %', v_refusal;

    -- U2c. And the empty array, which is how history stops saying anything at
    --      all. The relation's own j102_event_cites_evidence CHECK is the floor
    --      beneath this and is asserted structurally in S7; this is the writer's
    --      half, which refuses before the row is ever offered to the relation.
    v_refusal := null;
    begin
      v_evt2 := jsonb_set(v_evt, '{evidence_references}', '[]'::jsonb);
      perform ops.j102_apply_transition(
        'open-assignment',
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest,
                           'engagement:' || v_walk_eng_id, v_eng_digest,
                           'relationship:' || v_walk_rel_id, v_rel_digest),
        v_subjects,
        jsonb_build_array(jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
          'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
        v_manifest,
        'j102-fixture-key-u2c', v_placeholder,
        jsonb_build_object('operation', 'open-cre-assignment',
          'reason_id', 'search_initiation_opens_assignment',
          'coupled_facts', jsonb_build_array('assignment.assignment_phase'),
          'decision_refs', jsonb_build_array('Q080.D1')));
      raise exception 'U2c: an event cited no evidence at all and was appended anyway';
    exception when others then
      v_refusal := sqlerrm;
      if sqlerrm !~ 'j102_event_evidence_references_not_rechecked'
         and sqlerrm !~ 'j102_event_cites_evidence' then
        raise;
      end if;
    end;
    raise notice 'U2c PROVED at the citation check: %', v_refusal;

    -- ======================================================================
    -- === R: Q103's CONFLICT WRITER, against the committed assignment ========
    --
    -- Every case here is a DIRECT call on ops.j102_record_reconciliation_item
    -- holding nothing more than its EXECUTE grant, against a real subject with a
    -- real history. They exist because the properties that matter — a retry does
    -- not write twice, a substituted payload refuses, two DIFFERENT proposals
    -- both land, and a reading that went stale cannot be filed as current — are
    -- all properties of what happens under the lock at commit time, and an
    -- application-level readback can establish none of them.
    -- ======================================================================
    v_asg_digest := ops.j102_subject('assignment', v_walk_asg_id) ->> 'state_digest';
    v_base_digest := 'sha256:' || repeat('5c', 32);
    select e.event_digest into v_newest_event from ops.j102_subject_event e
     where e.tenant = v_tenant and e.subject_kind = 'assignment'
       and e.subject_id = v_walk_asg_id
     order by e.event_seq desc limit 1;

    -- The canonical item: the kernel's own reconciliation shape, plus the subject
    -- identity the relation requires and the evidence a person resolves it from.
    v_item := jsonb_build_object(
      'schema_version', 'doctorcre-v5-j102-lifecycle-reconciliation-item.v1',
      'tenant', v_tenant,
      'conflict_kind', 'uncharacterized_concurrent_change',
      'base_version_digest', v_base_digest,
      'current_version_digest', v_asg_digest,
      'incoming_edits', jsonb_build_array(jsonb_build_object(
        'field', 'assignment_phase', 'value_digest', v_lie,
        'field_class', 'lifecycle', 'edited_by', v_actor, 'edited_at', v_now)),
      'concurrent_edits', '[]'::jsonb,
      'proposed_by', v_actor,
      'visible', true, 'applied', false, 'resolved_by_machine', false,
      'subject_kind', 'assignment', 'subject_id', v_walk_asg_id,
      'concurrent_change_evidence', jsonb_build_object(
        'characterized', false,
        'why', 'synthetic fixture conflict',
        'current_state', ops.j102_subject('assignment', v_walk_asg_id) -> 'state',
        'current_state_source', 'ops.j102_read.subject',
        'history_tail', jsonb_build_array(jsonb_build_object(
          'transition_id', 'open-assignment', 'event_kind', 'assignment_opened',
          'recorded_by', v_actor, 'recorded_at', v_now,
          'record_digest', v_newest_event)),
        'history_tail_source', 'ops.j102_read.subject_events',
        'history_tail_is_complete', false));
    v_item_env := jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
      'tenant', v_tenant, 'record', v_item, 'record_digest', ops.f01_digest_jsonb(v_item),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder);

    -- R1. THE CONFLICT LANDS, visible and unresolved.
    v_result := ops.j102_record_reconciliation_item(
      v_item_env,
      jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
      'j102-fixture-key-r1', v_placeholder,
      jsonb_build_object('operation', 'record-lifecycle-reconciliation',
        'reason_id', 'concurrent_change_not_characterized'));
    if (v_result ->> 'outcome') <> 'recorded'
       or (v_result ->> 'visible') <> 'true'
       or (v_result ->> 'resolved_by_machine') <> 'false'
       or (v_result ->> 'current_version_bound_to_committed_row') <> 'true'
       or (v_result ->> 'state_evidence_bound_to_committed_row') <> 'true'
       or (v_result ->> 'history_evidence_bound_to_committed_history') <> 'true' then
      raise exception 'R1: the conflict receipt does not report what it bound: %', v_result;
    end if;
    select count(*) into v_count from ops.j102_reconciliation_item
     where tenant = v_tenant and subject_id = v_walk_asg_id;
    if v_count <> 1 then
      raise exception 'R1: the conflict did not land exactly once (% rows)', v_count;
    end if;

    -- R2. A RETRY REPLAYS. The same key and the same bytes return the stored
    --     outcome and write nothing — which is the property the read-before-write
    --     check could never give, because it was not atomic.
    v_replay := ops.j102_record_reconciliation_item(
      v_item_env,
      jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
      'j102-fixture-key-r1', v_placeholder,
      jsonb_build_object('operation', 'record-lifecycle-reconciliation',
        'reason_id', 'concurrent_change_not_characterized'));
    if (v_replay ->> 'item_digest') is distinct from (v_result ->> 'item_digest')
       or (v_replay ->> 'item_seq') is distinct from (v_result ->> 'item_seq') then
      raise exception 'R2: the replay did not return the committed outcome: %', v_replay;
    end if;
    select count(*) into v_count from ops.j102_reconciliation_item
     where tenant = v_tenant and subject_id = v_walk_asg_id;
    if v_count <> 1 then
      raise exception 'R2: the replay filed a second visible conflict';
    end if;

    -- R3. THE SAME KEY OVER DIFFERENT BYTES REFUSES rather than substituting one
    --     conflict for another.
    begin
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_item,
          'record_digest', ops.f01_digest_jsonb(v_item),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r1', v_lie,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R3: one idempotency key bound two different requests';
    exception when unique_violation then
      if sqlerrm !~ 'j102_idempotency_payload_mismatch' then raise; end if;
    end;

    -- R4. A DISTINCT PROPOSAL LANDS BESIDE IT. Different edits against the SAME
    --     two versions are a second real conflict, and collapsing them on the
    --     version pair would discard somebody's proposal silently.
    v_rec2 := jsonb_set(v_item, '{incoming_edits}', jsonb_build_array(
      jsonb_build_object('field', 'open_negotiation_count', 'value_digest', v_placeholder,
        'field_class', 'lifecycle', 'edited_by', v_actor, 'edited_at', v_now)));
    perform ops.j102_record_reconciliation_item(
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
      'j102-fixture-key-r4', v_placeholder,
      jsonb_build_object('operation', 'record-lifecycle-reconciliation',
        'reason_id', 'concurrent_change_not_characterized'));
    select count(*) into v_count from ops.j102_reconciliation_item
     where tenant = v_tenant and subject_id = v_walk_asg_id
       and base_version_digest = v_base_digest and current_version_digest = v_asg_digest;
    if v_count <> 2 then
      raise exception 'R4: two distinct proposals against one version pair collapsed to % row(s)',
        v_count;
    end if;

    -- R5. A STALE OPERAND refuses at the compare-and-swap, like every other
    --     writer here.
    begin
      perform ops.j102_record_reconciliation_item(
        v_item_env, jsonb_build_object('assignment:' || v_walk_asg_id, v_lie),
        'j102-fixture-key-r5', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R5: a conflict was filed against a version the row does not hold';
    exception when serialization_failure then
      if sqlerrm !~ 'j102_stale_subject_digest' then raise; end if;
    end;

    -- R6. A STALE "CURRENT" VERSION INSIDE THE ITEM. This is the one the earlier
    --     readback could not catch at all: the operand is right, the row has not
    --     moved, and the ITEM claims a current version that is not the committed
    --     one. A person resolving it would be comparing against a version that
    --     never was current.
    begin
      v_rec2 := jsonb_set(v_item, '{current_version_digest}', to_jsonb(v_lie));
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r6', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R6: an item filed a version as current that the committed row is not at';
    exception when serialization_failure then
      if sqlerrm !~ 'j102_reconciliation_current_version_not_current' then raise; end if;
    end;

    -- R7. A STALE STATE SNAPSHOT. The item names the right version and SHOWS a
    --     different one, which is the evidence half of the same lie.
    begin
      v_rec2 := jsonb_set(v_item, '{concurrent_change_evidence,current_state}',
        v_assignment_state);
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r7', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R7: the state an item shows as current did not have to hash to the version it names';
    exception when serialization_failure then
      if sqlerrm !~ 'j102_reconciliation_state_evidence_stale' then raise; end if;
    end;

    -- R8. STALE HISTORY EVIDENCE. The tail no longer ends at the newest committed
    --     event, so the changes a person is meant to review are not the changes
    --     that happened.
    begin
      v_rec2 := jsonb_set(v_item, '{concurrent_change_evidence,history_tail}',
        jsonb_build_array(jsonb_build_object(
          'transition_id', 'open-assignment', 'event_kind', 'assignment_opened',
          'recorded_by', v_actor, 'recorded_at', v_now, 'record_digest', v_lie)));
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r8', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R8: an item filed history evidence that is not the committed history';
    exception when serialization_failure then
      if sqlerrm !~ 'j102_reconciliation_history_evidence_stale' then raise; end if;
    end;

    -- R9. NO CONFLICT, NO ITEM. A base equal to the current version is a caller
    --     nobody overtook, and an item for it is noise where a person looks.
    begin
      v_rec2 := jsonb_set(v_item, '{base_version_digest}', to_jsonb(v_asg_digest));
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r9', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'no_concurrent_movement'));
      raise exception 'R9: a conflict item was filed for a subject nobody had moved';
    exception when others then
      if sqlerrm !~ 'j102_reconciliation_without_conflict' then raise; end if;
    end;

    -- R10. AND AN ITEM MAY NOT ARRIVE ALREADY RESOLVED, nor attributed to
    --      somebody else.
    begin
      v_rec2 := jsonb_set(v_item, '{resolved_by_machine}', 'true'::jsonb);
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r10', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R10: a conflict was filed already resolved by a machine';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_reconciliation_resolves_itself' then raise; end if;
    end;
    begin
      v_rec2 := jsonb_set(v_item, '{proposed_by}', '"somebody-else"'::jsonb);
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r11', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R10: a conflict was attributed to an actor who did not raise it';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_actor_injection_refused' then raise; end if;
    end;

    -- R11. AN EXTRA OPERAND is a row locked, compared and never consulted.
    begin
      perform ops.j102_record_reconciliation_item(
        v_item_env,
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest,
                           'relationship:' || v_walk_rel_id,
                             ops.j102_subject('relationship', v_walk_rel_id) ->> 'state_digest'),
        'j102-fixture-key-r12', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R11: a conflict locked a subject it is not about';
    exception when others then
      if sqlerrm !~ 'j102_item_operand_set_mismatch' then raise; end if;
    end;

    -- ----------------------------------------------------------------------
    -- R12-R15: THE LABELS. Everything above is about whether the item is BOUND
    -- to the row. These four are about whether it is DESCRIBED honestly, which
    -- is the other half of "visible reconciliation": a person reads this queue
    -- and acts on what it says. On the shipped path conflict_kind and every
    -- field_class are derived by the kernel and refused as caller input by the
    -- store -- but this function is granted to carr_writer, and that caller is
    -- exactly who these refusals are for.
    -- ----------------------------------------------------------------------

    -- R12. A CONFLICT KIND NOTHING FILES. Not a state bypass; a row in the queue
    --      labelled with a category no evaluator in this slice produces.
    begin
      v_rec2 := jsonb_set(v_item, '{conflict_kind}', '"totally_fine_edit"'::jsonb);
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r13', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R12: a conflict was filed under a kind this slice never emits';
    exception when invalid_parameter_value then
      if sqlerrm !~ 'j102_item_conflict_kind_unregistered' then raise; end if;
    end;

    -- R13. A LIFECYCLE FIELD LABELLED ROUTINE. This is the original Q103 bypass
    --      arriving one layer lower: `routine` is the class that buys automatic
    --      merge, and a stored item carrying it would tell the next reader that
    --      an assignment phase change was nothing much.
    begin
      v_rec2 := jsonb_set(v_item, '{incoming_edits}', jsonb_build_array(
        jsonb_build_object('field', 'assignment_phase', 'value_digest', v_lie,
          'field_class', 'routine', 'edited_by', v_actor, 'edited_at', v_now)));
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r14', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R13: a lifecycle field was filed labelled routine';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_item_field_class_mismatch' then raise; end if;
    end;

    -- R14. AND THE OTHER SIDE IS CHECKED TOO, on a field policy has NOT
    --      classified: silence is recorded as JSON null, and a caller cannot
    --      upgrade it to a class by writing one down.
    begin
      v_rec2 := jsonb_set(v_item, '{concurrent_edits}', jsonb_build_array(
        jsonb_build_object('field', 'internal_note', 'value_digest', v_lie,
          'field_class', 'lifecycle', 'edited_by', v_actor, 'edited_at', v_now)));
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r15', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R14: an unclassified field was filed carrying a class';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_item_field_class_mismatch' then raise; end if;
    end;

    -- R15. THE RESOLUTION FLAGS AS STRINGS. `->>` renders the string "false" and
    --      the boolean false identically, so a text comparison alone accepted an
    --      item whose flags are the wrong TYPE -- one that reads as correct here
    --      and as something else to everything downstream.
    begin
      v_rec2 := jsonb_set(v_item, '{resolved_by_machine}', '"false"'::jsonb);
      perform ops.j102_record_reconciliation_item(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_reconciliation_item',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object('assignment:' || v_walk_asg_id, v_asg_digest),
        'j102-fixture-key-r16', v_placeholder,
        jsonb_build_object('operation', 'record-lifecycle-reconciliation',
          'reason_id', 'concurrent_change_not_characterized'));
      raise exception 'R15: a resolution flag was accepted as a string';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_reconciliation_resolves_itself' then raise; end if;
    end;

    -- AND EXACTLY TWO CONFLICTS EXIST: the one R1 filed and the distinct proposal
    -- R4 filed. Every refusal above left nothing behind. The two rows that DID
    -- land are checked for the properties a reader depends on, as stored values
    -- rather than as receipt claims.
    select count(*) into v_count from ops.j102_reconciliation_item where tenant = v_tenant;
    if v_count <> 2 then
      raise exception 'R: the reconciliation relation holds % rows and exactly 2 were filed',
        v_count;
    end if;
    if exists (select 1 from ops.j102_reconciliation_item
                where tenant = v_tenant
                  and ((envelope -> 'record' -> 'visible') is distinct from 'true'::jsonb
                    or (envelope -> 'record' -> 'applied') is distinct from 'false'::jsonb
                    or (envelope -> 'record' -> 'resolved_by_machine')
                         is distinct from 'false'::jsonb
                    or not (ops.j102_admission_policy() -> 'reconciliation' -> 'conflict_kinds'
                              ? conflict_kind))) then
      raise exception 'R: a filed conflict is not visible, unapplied and unresolved, or carries an unregistered kind';
    end if;
    raise notice 'R1-R15 PROVED: the conflict writer is GOVERNED -- a retry replayed, a substituted payload refused, two distinct proposals both landed, a stale operand, a stale current version, a stale state snapshot and stale history evidence were each refused at the write boundary, and an unregistered conflict kind, a lifecycle field labelled routine, an unclassified field carrying a class and a string-typed resolution flag were each refused too. The two rows that landed are visible, unapplied and unresolved as JSON booleans.';

    -- AND NOTHING U1 OR U2 ATTEMPTED LANDED. Every one of them was a refusal, so
    -- the walk's own history is exactly what the walk appended.
    select count(*) into v_count from ops.j102_subject_event
     where tenant = v_tenant and subject_id = v_walk_asg_id;
    if v_count <> 2 then
      raise exception 'U1/U2: the assignment carries % history rows and the walk appended 2 (initialized, opened)',
        v_count;
    end if;

    raise notice 'J: THE POSITIVE WALK RAN IN FULL -- prospect created, synthetic ETL recorded and associated, client status and engagement landed together, assignment created under the active engagement and opened on its own mandate, negotiation drafted. Every prerequisite was satisfied rather than skipped. U1 and U2 then ran against that committed assignment and PROVED the target and event-detail checks they are aimed at, which they could not do before a creation door existed. Every row rolls back.';
  end if;

  -- === the fixture leaves nothing behind ===================================
  -- Asserted BEFORE the rollback, so a writer that somehow escaped the
  -- transaction would be visible here rather than assumed away by the rollback.
  --
  -- EXACTLY WHAT THE WALK CREATED, AND NOT ONE ROW MORE. Every adversarial and
  -- initialization-refusal group above must have refused WHOLE, so the only rows
  -- that may exist are the ones group J wrote and counted as it went. A count
  -- that is too high means a refused call half-landed; too low means a step of
  -- the walk did not do what it reported.
  select count(*) into v_count from ops.j102_subject_current where tenant = v_tenant;
  if v_count <> v_walk_subjects then
    raise exception 'the fixture holds % lifecycle subjects and the walk created %; every other group above is a refusal and none of them may write a row',
      v_count, v_walk_subjects;
  end if;
  select count(*) into v_count from ops.j102_subject_event where tenant = v_tenant;
  if v_count <> v_walk_events then
    raise exception 'the fixture holds % lifecycle events and the walk appended %; a refused transition wrote history',
      v_count, v_walk_events;
  end if;

  -- THE ONE LINE A READER SHOULD TAKE THE RUN'S MEANING FROM, and it says which
  -- groups actually ran rather than only that nothing raised. U1, U2, J4 and the
  -- WHOLE R GROUP live inside the walk, so a run that skipped J proved none of
  -- them. S9 is structural and runs in every session, so it is enumerated on both
  -- branches -- and it is worth being exact about what it does and does not
  -- establish: S9 reads signatures, grants, constraints and function text. The
  -- BEHAVIOUR of the conflict writer -- that a retry replays, that a stale
  -- reading cannot be filed as current, that a mislabelled item is refused -- is
  -- R's to prove, and R does not run without J.
  if v_walk_ok then
    raise notice 'ALL RUNNABLE GROUPS PASSED (S1-S9, P0, A1-A12, I1, J, J4, U1, U2, R1-R15, B5, B6, B7, B9, B10, B16, M3, Q081). The transition writer still refuses to create its own primary subject (P0); the creation door refuses every state jump (I1) and every unread operand (J4c); the positive walk ran IN FULL and created % subjects and % events; U1 and U2 were then decided against that committed assignment and PROVED the target and event-detail checks; and R1-R15 exercised the governed conflict writer against that committed assignment -- replay, substituted payload, two distinct proposals, three staleness lies, an unregistered conflict kind, two mislabelled field classes and a string-typed resolution flag. One arm is still unproven here and J4 names it: j102_required_context_not_met. Every row is about to roll back.',
      v_walk_subjects, v_walk_events;
  else
    raise notice 'PARTIAL RUN (S1-S9, P0, A1-A12, I1, B5, B6, B7, B9, B10, B16, M3, Q081). GROUP J DID NOT RUN -- see its skip notice for the exact unmet prerequisite -- so U1, U2, J4 and R1-R15 did not run either, and NOTHING here proves the target check, the event-detail check, the context resolution, any positive walk, or ANY BEHAVIOUR OF THE Q103 CONFLICT WRITER: not its idempotent replay, not its staleness bindings, not that two distinct proposals both land, and not that an unregistered conflict kind or a mislabelled field class is refused. S9 ran and is structural only -- it read the writer''s signature, its grants, its constraints and its text, which is not the same claim. The walk created % subjects and % events, which is what a run that stopped at the prospect leaves. Do not read this as a green J or a green R.',
      v_walk_subjects, v_walk_events;
  end if;
end
$proof$;

-- EVERYTHING ROLLS BACK. Nothing above is a real client, a real deal, an applied
-- migration, or a claim that this file has been run.
rollback;
