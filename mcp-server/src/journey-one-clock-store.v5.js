// DoctorCRE v5 slice V5-M01, product source tail: DURABLE APPEND-ONLY STORAGE
// FOR THE JOURNEY 1 CLOCK HISTORY, with exact prior-history compare-and-swap.
//
// journey-one-clock.v5.js is a pure kernel. It says so itself, at length, under
// WHAT REMAINS INTEGRATION WORK: "Returning a history does not claim it was
// persisted; the caller owns the durable append, which is why every result
// carries durable_history_write_required", and "ANTI-ROLLBACK LIVES IN THAT
// ADAPTER, not here ... an OLDER GENUINE history replays unless the durable
// store compare-and-swaps on the exact prior history_digest and refuses a write
// whose prior does not match the stored one."
//
// THIS FILE IS THAT ADAPTER, AND NOTHING ELSE. It computes no deadline, selects
// no origin, judges no receipt, decides no status and holds no pause budget.
// Every one of those belongs to the kernel and is reached by CALLING it. If this
// file and the kernel could disagree about what a clock says, one of them would
// be wrong; the only facts this file produces on its own are facts ABOUT THE
// STORE — which revision is the head, whether a supplied prior matches it,
// whether the content rebuilt from the stored rows still hashes to the digest it
// was written under, and whether one revision is a legal APPEND onto another.
//
// ---------------------------------------------------------------------------
// THE AUTHORITY BOUNDARY, STATED FIRST BECAUSE IT BOUNDS EVERYTHING BELOW.
//
// PERSISTENCE DOES NOT AUTHENTICATE INPUT PROOFS, AND THIS FILE NEVER PRETENDS
// OTHERWISE. createJourneyOneClock refuses to exist without an authenticated
// verifySnapshot callback and a current TRUSTED PROJECTION. In this repository
// today there is:
//   * no live producer of an admitted foundation-assurance-minimum receipt,
//   * no live producer of a journey-one-kernel-production terminus receipt,
//   * no authenticated reader that can build the trusted projection FROM THE
//     RECORD LAYER (a verified DB projection reader).
// So there is no safe PUBLIC "start the clock" verb to expose, and none is
// exposed. What is exposed instead:
//   * journeyOneClockStoreIntegrationRequirements() — an honest, zero-effect
//     descriptor naming each missing seam.
//   * readAuthenticatedClockProjectionInputs() — a PRIVATE, parameterless,
//     configuration-free fail-closed reader that always throws. The write verb
//     calls it BEFORE it issues any query, so the absence is observable rather
//     than merely asserted.
//   * createJourneyOneClockRecorder({ clock, store }) — the TRUSTED INTEGRATION
//     CONTRACT. It takes an ALREADY-CONSTRUCTED kernel instance, which can only
//     be built by trusted server code that installed a real verifier. It is not
//     reachable from ordinary request JSON, it invents no verifier, and it is
//     the seat a real integration will use on the day the readers above land.
//
// THE STORAGE MECHANICS UNDERNEATH ARE FULLY IMPLEMENTED AND FULLY EXERCISED.
// This is deliberately NOT a wrapper whose useful work is permanently disabled:
// the identity derivation, the row decomposition and whole-content
// reconstruction, the digest recomputation, the exact-prior CAS, the
// idempotency binding, the append-only diff and the deterministic readback all
// run for real, and the unit suite drives them end to end through the
// non-durable reference journal. What is narrowly missing is the AUTHORITY to
// admit an input proof without a trusted caller, and that is the only thing
// named as missing.
//
// WHAT THIS FILE REFUSES TO INVENT, each because inventing it would manufacture
// the exact authority the missing record is supposed to carry:
//   * a caller `{ verified: true }` envelope, or any self-hashed envelope, read
//     as authority. The kernel's verification binding is a trusted-code seam and
//     stays one.
//   * a fabricated consumer-gate or rollout-component receipt, or a minted
//     session_ref standing in for an authenticated identity.
//   * a caller-chosen `as_of` used as the record layer's clock. `as_of` is the
//     instant the KERNEL evaluated at; `recorded_at` is server time and is
//     produced by the store (Date.now for the reference journal, now() in the
//     database for the durable one). The two are stored separately and never
//     substituted.
//   * a live start verb that steps around the missing proof seam.
//
// ---------------------------------------------------------------------------
// WHAT THE RECORD LAYER CAN AND CANNOT PROVE, SAID PLAINLY.
//
// CAN: that a stored history is the one that was written (the digest recomputes
// from the rebuilt content), that the head is the head (exact-prior CAS under a
// serialized same-clock section), that a clock's identity was derived from the
// kernel's own origin rather than chosen by its caller, that the origin and the
// completion seals never changed, that a recorded miss was never removed, that
// no event ever left the chain, that one idempotency key never carried two
// payloads, that no revision was backdated, and that one AUTHORITATIVE CLOCK
// SCOPE holds at most one clock — the last of those only as exactly as the
// trusted integration's scope binding is itself stable (see THE TWO IDENTITIES
// below).
//
// ---------------------------------------------------------------------------
// THE TWO IDENTITIES, AND WHY ONE OF THEM IS NOT ENOUGH.
//
// `clock_key` is DERIVED FROM THE PRESENTED ORIGIN: the origin receipt digest,
// the origin instant and the origin benchmark manifest digest, under this
// tenant. That defeats exactly one attack — a caller renaming a clock — and it
// is stated here rather than oversold, because it does NOT defeat the other one:
//
//   A CALLER WHO PRESENTS A DIFFERENT ORIGIN DERIVES A DIFFERENT KEY, AND A
//   DIFFERENT KEY HAS NO HEAD, SO ITS CREATION MEETS NO COMPARE-AND-SWAP AT ALL.
//
// A second admitted minimum receipt, an amended benchmark manifest read as the
// origin manifest, a re-observed origin instant, or a copied history with the
// old state left out: each of them addresses a fresh clock and the running one
// is never touched. The kernel's `origin_reset_or_rebase` cannot see it either —
// that refusal fires only when a HISTORY is presented, and this attack presents
// none. A same-origin negative ("an alias cannot restart this clock") is a true
// statement about aliases and PROVES NOTHING ABOUT RESETS. Nothing in this file
// claims the derived key enforces one clock per actual program or subject.
//
// `clock_scope_key` is what does. It is an EXACT TRUSTED BINDING supplied by the
// integration seat — the accepted scope the verifier already reads: this
// tenant, the accepted benchmark subject, candidate and policy digests, and the
// two J1 gate ids from JOURNEY_ONE_DEADLINE_CONTRACT — and it is bound to the
// STORE OBJECT at construction, never to a request. One scope holds at most one
// clock: a creation whose origin derives a new key while the scope already
// names a clock READS that binding and refuses, so a new origin for one
// authoritative scope meets a refusal instead of a fresh clock.
//
// THE LABEL IS NOT PART OF IT. `scope_ref` is a name for humans: it is stored,
// read back and sealed once, and it is NOT in the key's preimage. It used to be,
// while every comment said it carried no authority — so one accepted scope
// spelled two ways produced two keys, and "one scope, one clock" silently meant
// "one label, one clock". A relabelled store could then start a second clock for
// one program with a fresh origin. The preimage is now the six identity fields
// (JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS) and the domain tag is versioned to
// v2 because the published key changed.
//
// ITS LIMITS, STATED WITH IT. Through createJourneyOneClockRecorder the scope is
// now checked against the kernel's own `verified_binding` for the computation
// being stored, so a store whose scope is not the one the kernel judged refuses
// before any journal call. Through a DIRECT store.record() call — and through a
// direct SQL writer — the binding is still COMPARED, not verified: the stored
// v2 state carries no subject, candidate or policy digest, so there is nothing
// on that path to check it against. That remaining boundary is deliberate and is
// carried on every readback in JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE.
//
// CANNOT: that the origin receipt was a genuine current passing
// foundation-assurance-minimum receipt; that the pauses were approved by a real
// partner before they started; that the terminus receipt was admitted by the
// accepted per-receipt TTL policy against the accepted kernel scope; or that the
// projection the kernel read was authentic. A DIRECT-SQL WRITER HOLDING THE
// WRITER BUNDLE CAN STORE AN ASSERTION, and the record layer cannot tell that
// assertion from a kernel computation. That is why every revision carries
// `input_authority` fixed to
// `trusted_projection_not_independently_verified_by_this_record_layer`, why no
// column is named `verified`, `accepted` or `deadline_success`, and why the
// readback says the same sentence out loud to anyone who reads a status off it.
// STORING `completed_on_time` IS NOT ACCEPTING A DEADLINE.
//
// ---------------------------------------------------------------------------
// EXACT SEMANTICS PRESERVED, NOT RE-DECIDED. The Chicago/DST resolution, the
// pre-approved 120-hour blocker-pause union, the sticky miss, "a late passing
// kernel receipt stays usable", and "never claim deadline success after a
// recorded miss" all live in the kernel. This file transports them: the
// pause-interval `ends_at` strings are stored VERBATIM as text rather than as
// timestamptz, because the kernel copies them off the projection unnormalized
// and a round trip through a timestamp type would return a different STRING and
// therefore a different history_digest. Every other instant in the state is
// already `new Date(ms).toISOString()` and is likewise stored as its exact text.
//
// ---------------------------------------------------------------------------
// REUSED READ-ONLY, NEVER RESTATED:
//   * journey-one-clock.v5.js — the schema constants, the legacy-schema list,
//     the deadline contract, the error type, and the kernel itself.
//   * benchmark-acceptance-store.v5.js — assertNoSelfAssertedAuthority. The
//     A00 rail already owns that vocabulary ("verified", "authority_granted",
//     "clock_started", "issued", "accepted_by"). A clock-local copy would be a
//     second list that drifts, so this file imports it instead of writing one.
//   * identity.js — ORGANIZATION_TENANT_ID and authorizationClassForActor, over
//     the LIVE actor the server built. No authority class is ever read back out
//     of a stored row to decide anything.
//   * ops.portfolio_canonical_json and ops.portfolio_writer_actor_id, on the SQL
//     side. No second canonicalizer and no second writer derivation.
//
// THE HISTORY VALIDATOR IS THE KERNEL'S, NOT A SECOND ONE. An earlier revision
// of this file owned a deliberately minimal self-consistency gate, because
// `readHistory` was module-private in journey-one-clock.v5.js and the only way
// to ask the kernel anything was to build a whole authenticated projection. It
// is now exported as `readJourneyOneClockHistory(history, now)` and THIS FILE
// CALLS IT. The store's own gate is gone; what a single history has to be —
// closed shape, current state schema, recomputed history_digest, instant
// grammar, the paired completion seals, the sticky miss bound to its own event,
// the linked and self-hashing event chain, the one Q008.D1 contradiction — is
// answered in one place, by the code that also reads a history inside evaluate().
// A malformed history that used to survive "closed shape plus a recomputed
// digest" — a sealed field dropped, an event set that contradicts miss_at or the
// completion, a status outside the kernel's own list, an origin that postdates
// its own evaluation — now refuses at the storage boundary under the KERNEL'S
// name, unchanged.
//
// THE DIVISION IS EXACT, and each half owns what the other structurally cannot:
//   * THE KERNEL owns one history's whole shape and semantics. It sees one
//     history and can say everything about it alone.
//   * THIS FILE owns the PAIRWISE facts — this revision against the head on
//     disk — and the facts about the store itself: the exact-prior CAS, the
//     origin/base-deadline/seal/miss diff, the event-prefix diff, the ordinal
//     chain, idempotency binding, and identity. The kernel cannot check any of
//     them, because it never sees the history already stored.
// The RESTART PATH is still exercised end to end and still proves more than the
// validator does: a stored history handed back through evaluate() is read as
// the clock's actual history, against a live projection.
//
// WHAT REMAINS INTEGRATION WORK, named rather than implied:
//   * The authenticated projection reader (see the private stub below) and the
//     live minimum/terminus producers. Until they exist the public write verb
//     fails closed and no clock can be started through it.
//   * Applying ops/journey-one-clock-store.candidate.sql as a numbered
//     migration. It is candidate source and is not in public.schema_migrations.
//   * Registering these verbs. `journeyOneClockStoreTools` is exported and is
//     deliberately NOT added to any tool index by this slice.
//   * Running mcp-server/test/journey-one-clock-store-postgres.sql against a
//     database. It has never been executed here.
//   * Carrying the scope check onto the DIRECT record path. The kernel's
//     read-only projection-binding extension has landed — evaluate() returns
//     `verified_binding` beside the state — and createJourneyOneClockRecorder
//     compares it against the store's authoritative scope before any journal
//     call. A caller of store.record() supplies only a STATE, which carries none
//     of those three digests, so that path is unchanged and is still a trusted
//     seat. Closing it needs the stored state to carry the binding, which would
//     change every history_digest, and is not done here.
//   * Re-deriving any ALREADY PUBLISHED scope key under the v2 domain tag. There
//     is nothing to re-derive: the candidate SQL has never been applied and no
//     clock has been started. If that ever stops being true, moving a stored key
//     is an explicit migration owned by whoever owns the durable store, exactly
//     as a v1 history is.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, authorizationClassForActor, isKnownActor } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { assertNoSelfAssertedAuthority } from "./benchmark-acceptance-store.v5.js";
import {
  JOURNEY_ONE_CLOCK_LEGACY_SCHEMAS, JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_CLOCK_SCHEMA,
  JOURNEY_ONE_CLOCK_VERIFIED_BINDING, JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS,
  JOURNEY_ONE_DEADLINE_CONTRACT, readJourneyOneClockHistory,
} from "./journey-one-clock.v5.js";

/** Module-local adapter schemas. NOT r7 schemas; r7 declares no storage shape. */
export const JOURNEY_ONE_CLOCK_STORE_SCHEMA = "doctorcre-v5-journey-one-clock-store.v1";
export const JOURNEY_ONE_CLOCK_HISTORY_ROWS_SCHEMA =
  "doctorcre-v5-journey-one-clock-history-rows.v1";
export const JOURNEY_ONE_CLOCK_READBACK_SCHEMA =
  "doctorcre-v5-journey-one-clock-readback.v1";
/**
 * The readback of ONE REQUEST that already landed, addressed by its idempotency
 * key. It is a read of rows this rail already holds — it opens no append, takes
 * no lock, and grants nothing.
 */
export const JOURNEY_ONE_CLOCK_REPLAY_READBACK_SCHEMA =
  "doctorcre-v5-journey-one-clock-request-readback.v1";
export const JOURNEY_ONE_CLOCK_INTEGRATION_SCHEMA =
  "doctorcre-v5-journey-one-clock-store-integration.v1";

/**
 * The domain tag under which a clock's IDENTITY is derived.
 *
 * THE IDENTITY IS A FUNCTION OF THE PRESENTED ORIGIN, NEVER OF A NAME THE
 * CALLER PICKED. Two callers presenting the same origin address the SAME clock
 * and therefore collide on the creation CAS; a caller who invents a fresh label
 * for a clock that already has history does not get a fresh clock, it gets a
 * refusal. That is why `clock_ref` below is a legibility label with no authority
 * at all.
 *
 * IT IS A DEFENCE AGAINST ALIASES AND AGAINST NOTHING ELSE. A caller who
 * presents a DIFFERENT origin — a second admitted minimum, an amended manifest
 * read as the origin manifest, a re-observed instant — derives a different key,
 * and a different key has no head to compare against. The scope binding below
 * is what refuses that; this tag never did and is not claimed to. See THE TWO
 * IDENTITIES in the header.
 */
export const JOURNEY_ONE_CLOCK_IDENTITY_DOMAIN_TAG = "doctorcre:j1-clock-identity:v1";

/** The exact fields the presented origin identity is derived from, C-sorted. */
export const JOURNEY_ONE_CLOCK_IDENTITY_FIELDS = Object.freeze([
  "origin_at", "origin_benchmark_manifest_digest", "origin_receipt_digest", "tenant",
]);

/**
 * The domain tag under which a clock's STABLE AUTHORITATIVE SCOPE is derived.
 *
 * The scope answers "which program and subject is this the 30-day clock for",
 * and the origin answers "which admitted receipt started it". They are separate
 * on purpose: the origin is immutable per clock and a NEW one is exactly the
 * thing a reset would present, so an identity built only from the origin cannot
 * refuse a reset. The scope is stable ACROSS origins, which is what makes "this
 * program already has a running clock" a question the record layer can answer.
 *
 * v2, AND THE VERSION IS THE HONEST PART OF THE FIX. v1 hashed all seven
 * declared fields INCLUDING `scope_ref`, while every comment beside it said that
 * field is a human label with no authority. Those two statements cannot both be
 * true: with the label inside the preimage, one accepted scope relabelled
 * `safe:clock-scope:a` and `safe:clock-scope:b` produced TWO keys, and a caller
 * holding a relabelled store could create a second clock for one program with a
 * fresh origin — the exact evasion the scope exists to refuse. The preimage is
 * now the six IDENTITY fields below and the label is carried as provenance
 * beside the key. That changes the published key for every scope, so the tag is
 * versioned rather than silently redefined. Nothing is rewritten by it: the
 * candidate SQL has never been applied, no clock has been started, and no
 * migration or backfill is proposed here.
 */
export const JOURNEY_ONE_CLOCK_SCOPE_DOMAIN_TAG = "doctorcre:j1-clock-scope:v2";

/**
 * The declared shape of an accepted scope binding as it is SUPPLIED, C-sorted:
 * the six identity fields plus the human label. All seven are required — a
 * binding with no label would leave the record unable to say WHICH accepted
 * scope a reader is looking at — and exactly seven are accepted.
 */
export const JOURNEY_ONE_CLOCK_SCOPE_FIELDS = Object.freeze([
  "benchmark_candidate_digest", "benchmark_policy_digest", "benchmark_subject_digest",
  "clock_origin_gate_id", "clock_terminus_gate_id", "scope_ref", "tenant",
]);

/**
 * The exact fields one authoritative clock scope's KEY is derived from, C-sorted.
 * This is JOURNEY_ONE_CLOCK_SCOPE_FIELDS MINUS `scope_ref`.
 *
 * NOT NEW DATA, AND NOT SELF-ASSERTED DATA. Every field is one the accepted
 * contracts already carry: the three digests are the trusted projection's own
 * `binding` — the ones journey-one-clock.v5.js forces the accepted benchmark
 * AND every receipt it reads to match, and now returns beside the state as
 * `verified_binding` — and the two gate ids are JOURNEY_ONE_DEADLINE_CONTRACT's
 * own, checked below against that constant rather than believed.
 *
 * THE LABEL IS NOT IDENTITY, and its absence here is the whole point. A name a
 * human chose is not a fact about which program a clock belongs to, and hashing
 * it made "one scope, one clock" hold over labels rather than over scopes. It is
 * still stored, still read back, and still refused when it changes for a scope
 * already bound (`j1_clock_scope_label_is_not_identity`) — provenance that
 * cannot become authority.
 *
 * WHY THE THREE DIGESTS ARE THE STABLE PART. For one clock the kernel already
 * pins them: a later evaluation must present the SAME origin receipt, and that
 * receipt's subject, candidate and policy digests must equal the binding's, so
 * a clock cannot change them. A second origin for the same accepted scope
 * therefore lands on the SAME scope key and meets the existing binding.
 *
 * WHAT THIS RAIL STILL CANNOT DO WITH THEM: check them against a stored HISTORY.
 * The v2 state carries none of the three. Through the trusted recorder they are
 * now compared against the kernel's own `verified_binding` for the computation
 * being stored; through a direct store.record() call, or a direct SQL writer,
 * the binding is compared to the store's construction-time scope and no further.
 */
export const JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS = Object.freeze(
  JOURNEY_ONE_CLOCK_SCOPE_FIELDS.filter(field => field !== "scope_ref"));

/**
 * The twenty-one fields of doctorcre-v5-journey-one-clock.v2, in the kernel's
 * own declared order. Stated here so the round trip can assert it stored every
 * one of them: a field that is hashed but not stored is a record layer that
 * cannot reproduce the history it claims to hold.
 */
export const JOURNEY_ONE_CLOCK_STATE_FIELDS = Object.freeze([
  "schema_version", "origin_receipt_digest", "origin_at", "origin_benchmark_manifest_digest",
  "current_benchmark_manifest_digest", "origin_receipt_ttl_policy_ms", "base_deadline_at",
  "base_deadline_resolution", "due_at", "paused_ms", "status", "miss_at",
  "completion_receipt_digest", "completion_observed_at", "completion_receipt_ttl_policy_ms",
  "completion_artifact_digest", "completion_fixture_set_digest", "evaluated_at",
  "pause_intervals", "events", "history_digest",
]);

/** The three state fields that are relations rather than scalars. */
export const JOURNEY_ONE_CLOCK_RELATION_FIELDS = Object.freeze([
  "events", "history_digest", "pause_intervals",
]);

/** The scalar state fields, stored as columns on the revision row. */
export const JOURNEY_ONE_CLOCK_SCALAR_FIELDS = Object.freeze(
  JOURNEY_ONE_CLOCK_STATE_FIELDS.filter(f => !JOURNEY_ONE_CLOCK_RELATION_FIELDS.includes(f)));

export const JOURNEY_ONE_CLOCK_EVENT_FIELDS = Object.freeze([
  "type", "at", "recorded_at", "evidence_digest", "previous_event_digest", "event_digest",
]);

export const JOURNEY_ONE_CLOCK_PAUSE_INTERVAL_FIELDS = Object.freeze(["pause_id", "ends_at"]);

/**
 * The origin, sealed at creation and never rewritten. `origin_at` and
 * `base_deadline_at`/`base_deadline_resolution` ride along because the kernel
 * derives the base deadline from the origin alone: a base deadline that moved
 * means the origin moved, whatever the origin digest still says.
 */
export const JOURNEY_ONE_CLOCK_ORIGIN_FIELDS = Object.freeze([
  "base_deadline_at", "base_deadline_resolution", "origin_at",
  "origin_benchmark_manifest_digest", "origin_receipt_digest", "origin_receipt_ttl_policy_ms",
  "schema_version",
]);

/**
 * The completion seals. The kernel writes all five as ONE fact on the first
 * completion, and a later projection that changes any of them is refused BY NAME
 * there. Here they are refused again at the storage boundary, because the kernel
 * only ever sees the history it was handed and cannot know it differs from the
 * one on disk.
 */
export const JOURNEY_ONE_CLOCK_COMPLETION_SEAL_FIELDS = Object.freeze([
  "completion_artifact_digest", "completion_fixture_set_digest", "completion_observed_at",
  "completion_receipt_digest", "completion_receipt_ttl_policy_ms",
]);

/**
 * THE APPEND INVARIANTS, STATED ONCE.
 *
 * There are two enforcement homes for these — assertJourneyOneClockAppendOnly()
 * below, and ops.j1_clock_append_guard() in the candidate SQL — and rule
 * a8c55a47 says a duplicated operation needs something that COMPARES the two.
 * This table is that comparison's anchor: every id below appears verbatim in the
 * SQL guard's refusal messages, and the unit suite reads the .sql file and
 * asserts each one is present. That proves the ids are stated on both sides. It
 * does not prove the SQL is correct — nothing that has never run can prove that,
 * and the SQL has never run. The honest reading is: one list, two homes, a
 * mechanical check that neither home dropped an entry.
 *
 * WHY BOTH HOMES EXIST AT ALL. The store cannot enforce anything against a
 * writer holding the writer bundle and calling the SQL function directly; the
 * database cannot be the only home because the reference journal has no
 * database. Neither is redundant with the other.
 */
const BOTH = Object.freeze(["module", "record_layer"]);
export const JOURNEY_ONE_CLOCK_APPEND_INVARIANTS = Object.freeze([
  Object.freeze({ id: "j1_clock_state_schema_current", enforced_in: BOTH,
    statement: "A stored revision is doctorcre-v5-journey-one-clock.v2. A v1 record is refused by name and its migration is an explicit external act, exactly as the kernel refuses it." }),
  Object.freeze({ id: "j1_clock_identity_derived_from_origin", enforced_in: BOTH,
    statement: "The clock a revision is appended to is the one its own origin derives, never one its caller named." }),
  Object.freeze({ id: "j1_clock_scope_binds_one_clock", enforced_in: BOTH,
    statement: "One authoritative clock scope holds at most one clock. A creation whose presented origin derives a new key, for a scope that already names a clock, reads that binding and is refused; a new origin is never a way around the compare-and-swap of the clock that scope already has." }),
  Object.freeze({ id: "j1_clock_scope_sealed_at_creation", enforced_in: BOTH,
    statement: "The authoritative scope a clock was bound to at creation is the scope every later revision of it is written under. A revision presenting another scope is refused rather than rebinding the clock to it." }),
  Object.freeze({ id: "j1_clock_scope_label_is_not_identity", enforced_in: BOTH,
    statement: "A scope's human label is provenance and never identity: it is excluded from the scope key, so one accepted scope under two names is one scope and cannot hold two clocks; and it is recorded once, so a second label presented for a bound scope is refused rather than silently kept or silently replaced." }),
  Object.freeze({ id: "j1_clock_tenant_bound", enforced_in: BOTH,
    statement: "A revision is stored under the tenant its identity was derived with, and a read for another tenant refuses rather than serving another tenant's clock." }),
  Object.freeze({ id: "j1_clock_content_rebuilds_to_its_digest", enforced_in: BOTH,
    statement: "The history rebuilt from the stored rows hashes to the history_digest the revision was written under." }),
  Object.freeze({ id: "j1_clock_exact_prior_history_digest", enforced_in: BOTH,
    statement: "An append names the exact history_digest of the current head; creation names an explicit null and succeeds only when the clock has no revisions." }),
  Object.freeze({ id: "j1_clock_origin_never_rewritten", enforced_in: BOTH,
    statement: "The origin fields and the base deadline they produce are identical in every revision of one clock." }),
  Object.freeze({ id: "j1_clock_completion_seals_never_changed", enforced_in: BOTH,
    statement: "Once a completion is recorded, all five completion seals are identical in every later revision." }),
  Object.freeze({ id: "j1_clock_recorded_miss_never_removed", enforced_in: BOTH,
    statement: "Once miss_at is recorded it is present and identical in every later revision." }),
  Object.freeze({ id: "j1_clock_events_are_append_only", enforced_in: BOTH,
    statement: "The prior revision's event chain is an exact prefix of the next revision's, by ordinal and by event_digest, and every link hashes to its own content." }),
  Object.freeze({ id: "j1_clock_no_deadline_success_after_recorded_miss", enforced_in: BOTH,
    statement: "A revision carrying miss_at may not carry status completed_on_time. Q008.D1 forbids the claim and the kernel refuses the same shape under the same name." }),
  Object.freeze({ id: "j1_clock_no_backdated_evaluation", enforced_in: BOTH,
    statement: "A revision's evaluated_at is at or after the head's, and its server recorded_at is at or after the head's." }),
  Object.freeze({ id: "j1_clock_idempotency_key_binds_its_payload", enforced_in: BOTH,
    statement: "One idempotency key replays exactly one revision; the same key presented with different content is refused rather than returning the first." }),
  Object.freeze({ id: "j1_clock_claimed_history_digest_is_never_trusted", enforced_in: BOTH,
    statement: "A caller-claimed history digest is only ever compared against the one the rebuilt content produces." }),
  // THE ONE INVARIANT WITH A SINGLE HOME, and it is honest about why: this
  // module has no update or delete path to refuse. The reference journal only
  // ever pushes, and the durable journal issues no DML at all — every write goes
  // through one definer function. "Refusing an UPDATE" is a thing only the
  // database can do, so only the database claims it.
  Object.freeze({ id: "j1_clock_rows_are_append_only", enforced_in: Object.freeze(["record_layer"]),
    statement: "Update, delete and truncate are refused on every relation of this rail, by TWO triggers per relation: a row-level one for update and delete, and a statement-level one for truncate, which a row-level trigger never sees and which cannot be revoked from the table owner. There is no reset, no rebase and no replacement. Enforced only at the database: this module has no mutation path to refuse." }),
]);

export const JOURNEY_ONE_CLOCK_APPEND_INVARIANT_IDS = Object.freeze(
  JOURNEY_ONE_CLOCK_APPEND_INVARIANTS.map(i => i.id));

/**
 * What this record layer cannot prove about a stored revision, carried on every
 * readback so a consumer reading a status off it cannot mistake storage for
 * acceptance.
 */
export const JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE = Object.freeze([
  "that the origin receipt was a genuine, current, passing foundation-assurance-minimum receipt: no authenticated admission ledger exists here, and admitted_at is a projection fact",
  "that the recorded pauses were approved by a real verified partner strictly before they started, or that an approval was not backdated",
  "that the terminus receipt was admitted under the accepted per-receipt TTL policy against the accepted kernel scope",
  "that the projection the kernel read was authentic: the verifier is trusted server code and this record layer never sees it",
  "that a revision written by a direct holder of the writer bundle is a kernel computation rather than that writer's assertion; both are trusted writers and nothing recorded here tells them apart",
  "that the authoritative scope a STORED revision is bound to is the accepted scope of the projection the kernel actually read. Through createJourneyOneClockRecorder the kernel's own verified_binding for that computation is compared against the store's scope before anything is written; through a direct store.record() call or a direct SQL writer it is not, because doctorcre-v5-journey-one-clock.v2 carries no subject, candidate or policy digest and this rail never derives one from a stored history",
  "anything about deadline SUCCESS. A stored status is a recorded computation, never an acceptance of a deadline by this record layer",
  "that a revision written through a recorder built WITHOUT an assert_before_write was bound to any composed projection. That seam is optional by construction, so a recorder without one checks the scope and nothing else; it is a third path, beside a direct record() call and a direct SQL writer, on which nothing ties the computation to an inventory this record layer read",
]);

/**
 * A RECORD LAYER'S OWN EFFECTS, which are not a pure evaluator's. V5_NO_EFFECTS
 * asserts `database_writes: 0`, which is true of every read and descriptor on
 * this rail and FALSE of a recorded revision. Claiming it on the write path
 * would be a convenient lie about the one thing that path does.
 *
 * `acceptances: 0` and `clock_started: false` are on it and are not decoration:
 * appending a revision records a computation. It accepts no deadline, admits no
 * receipt and starts nothing.
 */
export const JOURNEY_ONE_CLOCK_RECORD_EFFECTS = deepFreeze({
  creates_effect: false,
  database_writes: 1,
  network_calls: 0, provider_actions: 0, notifications: 0,
  schedules: 0, deployments: 0, activations: 0, acceptances: 0,
  clock_started: false,
  history_appended: true,
  grants_dispatch_activation_or_execution: false,
});

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CLOCK_REF = /^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$/;
/** The repository's ordinary `safe:` reference grammar, reused rather than widened. */
const SAFE_REF = /^safe:[A-Za-z0-9:._/-]{3,290}$/;
/**
 * The kernel's own timestamp grammar, restated as a STORAGE ACCEPTANCE bound
 * rather than as a second policy: this rail stores instants as exact text and
 * has to know which strings it is willing to hold. It decides nothing about
 * time — no ordering, no arithmetic, no zone — and the kernel remains the only
 * place an instant means anything.
 */
const TIMESTAMP =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/;

export class JourneyOneClockStoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "JourneyOneClockStoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new JourneyOneClockStoreError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

const copy = value => JSON.parse(JSON.stringify(value));

function closed(value, fields, path) {
  if (!isPlainObject(value)) refuse("invalid_shape", `${path} must be an object`, { path });
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some(f => !Object.hasOwn(value, f))) {
    refuse("closed_shape", `${path} must carry exactly its declared fields`,
      { path, expected: [...fields].sort(), actual: [...keys].sort() });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    refuse("invalid_digest", `${path} must be a sha256: reference`, { path, actual: value });
  }
  return value;
}

function assertNullableDigestRef(value, path) {
  return value === null ? null : assertDigestRef(value, path);
}

function assertTimestampText(value, path) {
  if (typeof value !== "string" || !TIMESTAMP.test(value)) {
    refuse("invalid_timestamp", `${path} must be an instant this rail can store verbatim`,
      { path, actual: value });
  }
  return value;
}

function assertNullableTimestampText(value, path) {
  return value === null ? null : assertTimestampText(value, path);
}

function assertUuid(value, path) {
  if (typeof value !== "string" || !UUID.test(value)) {
    refuse("invalid_uuid", `${path} must be a uuid`, { path });
  }
  return value;
}

// ---------------------------------------------------------------------------
// IDENTITY. Derived from the kernel's origin, never chosen by a caller.
// ---------------------------------------------------------------------------

/**
 * The identity preimage of one clock: the exact four facts that make it THAT
 * clock rather than another one.
 *
 * origin_receipt_digest is the hash of the whole r7-exact minimum receipt, so
 * the benchmark subject, candidate and policy digests ride inside it
 * transitively; origin_benchmark_manifest_digest names the accepted manifest the
 * clock started under; origin_at is the receipt's own observed_at. A caller
 * cannot produce a different clock for the same origin.
 *
 * IT CAN PRODUCE A DIFFERENT CLOCK FOR A DIFFERENT ORIGIN, and that is not a
 * flaw in this derivation but the reason the authoritative scope exists beside
 * it: moving any of these three fields addresses another clock, which is
 * precisely why the address alone cannot refuse a reset.
 */
export function journeyOneClockIdentityPreimage({
  tenant, origin_receipt_digest, origin_at, origin_benchmark_manifest_digest,
}) {
  assertDigestRef(origin_receipt_digest, "origin_receipt_digest");
  assertDigestRef(origin_benchmark_manifest_digest, "origin_benchmark_manifest_digest");
  assertTimestampText(origin_at, "origin_at");
  if (tenant !== ORGANIZATION_TENANT_ID) {
    refuse("wrong_tenant", `this rail stores clocks for ${ORGANIZATION_TENANT_ID} only`,
      { supplied: tenant, expected: ORGANIZATION_TENANT_ID });
  }
  return deepFreeze({
    origin_at, origin_benchmark_manifest_digest, origin_receipt_digest, tenant,
  });
}

/** The clock key: sha256 over the canonical [domain_tag, identity preimage]. */
export function journeyOneClockKey(preimage) {
  if (!isPlainObject(preimage)) {
    refuse("invalid_shape", "a clock identity needs its four origin facts", { path: "preimage" });
  }
  const fields = journeyOneClockIdentityPreimage(preimage);
  return digest([JOURNEY_ONE_CLOCK_IDENTITY_DOMAIN_TAG, fields]);
}

/** The clock key a kernel STATE belongs to, read off the state's own origin. */
export function journeyOneClockKeyForState(state, tenant = ORGANIZATION_TENANT_ID) {
  if (!isPlainObject(state)) refuse("invalid_shape", "state must be an object", { path: "state" });
  return journeyOneClockKey({
    tenant,
    origin_receipt_digest: state.origin_receipt_digest,
    origin_at: state.origin_at,
    origin_benchmark_manifest_digest: state.origin_benchmark_manifest_digest,
  });
}

/**
 * Read one authoritative clock scope binding, exactly.
 *
 * WHAT IS CHECKED HERE, and it is not everything: the shape is closed, the
 * tenant is this rail's, the three benchmark digests are sha256 references, the
 * scope reference is an ordinary `safe:` name, and THE TWO GATE IDS ARE
 * COMPARED AGAINST JOURNEY_ONE_DEADLINE_CONTRACT rather than believed — a scope
 * naming some other gate is not a Journey 1 clock scope and is refused by name.
 *
 * THE KEY IS DERIVED FROM THE SIX IDENTITY FIELDS AND NOT FROM THE LABEL, so
 * two spellings of one accepted scope are one scope. The label travels beside
 * the key as `clock_scope_ref`, and the whole supplied object travels as
 * `scope`, so a reader still sees which accepted scope was named.
 *
 * WHAT IS NOT CHECKED HERE: that these three digests are the ones the verifier
 * accepted for the projection the kernel read. This function is handed a
 * binding; it is the trusted RECORDER that compares it against the kernel's own
 * verified_binding for the computation being stored. Through a direct
 * store.record() call the binding is trusted because the seat that constructed
 * it is trusted, and that limit is carried on every readback in
 * JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE instead of being left for a reader to
 * discover.
 */
export function journeyOneClockScopeBinding(scope) {
  if (!isPlainObject(scope)) {
    refuse("invalid_shape", "a clock scope binding must be an object", { path: "clock_scope" });
  }
  closed(scope, JOURNEY_ONE_CLOCK_SCOPE_FIELDS, "clock_scope");
  if (scope.tenant !== ORGANIZATION_TENANT_ID) {
    refuse("wrong_tenant", `this rail stores clocks for ${ORGANIZATION_TENANT_ID} only`,
      { invariant: "j1_clock_tenant_bound", supplied: scope.tenant, expected: ORGANIZATION_TENANT_ID });
  }
  for (const field of ["benchmark_candidate_digest", "benchmark_policy_digest",
    "benchmark_subject_digest"]) {
    assertDigestRef(scope[field], `clock_scope.${field}`);
  }
  if (typeof scope.scope_ref !== "string" || !SAFE_REF.test(scope.scope_ref)) {
    refuse("invalid_reference",
      "clock_scope.scope_ref must be a safe: reference NAMING the accepted scope; it is a name and selects nothing",
      { path: "clock_scope.scope_ref", actual: scope.scope_ref });
  }
  for (const [field, expected] of [
    ["clock_origin_gate_id", JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id],
    ["clock_terminus_gate_id", JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id],
  ]) {
    if (scope[field] !== expected) {
      refuse("wrong_clock_scope_gate",
        `clock_scope.${field} must be the gate JOURNEY_ONE_DEADLINE_CONTRACT names; a scope for another gate is not a Journey 1 clock scope`,
        { invariant: "j1_clock_scope_binds_one_clock", field, expected, supplied: scope[field] });
    }
  }
  const fields = deepFreeze(Object.fromEntries(
    JOURNEY_ONE_CLOCK_SCOPE_FIELDS.map(field => [field, scope[field]])));
  // THE PREIMAGE IS THE SIX IDENTITY FIELDS. `scope_ref` is deliberately absent:
  // a label inside the hash makes one accepted scope addressable under as many
  // keys as a caller has names for it, which is a one-clock-per-LABEL rule
  // wearing the name of a one-clock-per-scope rule.
  const identity = deepFreeze(Object.fromEntries(
    JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS.map(field => [field, scope[field]])));
  return deepFreeze({
    clock_scope_key: digest([JOURNEY_ONE_CLOCK_SCOPE_DOMAIN_TAG, identity]),
    clock_scope_ref: scope.scope_ref,
    tenant: scope.tenant,
    scope: fields,
    scope_identity: identity,
  });
}

/** The scope key alone: sha256 over the canonical [domain_tag, identity fields]. */
export function journeyOneClockScopeKey(scope) {
  return journeyOneClockScopeBinding(scope).clock_scope_key;
}

// ---------------------------------------------------------------------------
// THE ROWS ARE THE RECORD. Decomposition and whole-content reconstruction.
// ---------------------------------------------------------------------------

/**
 * The digest of one history, computed the kernel's own way: sha256 over the
 * canonical serialization of the state WITHOUT its history_digest field.
 *
 * The kernel writes `delete state.history_digest; state.history_digest =
 * digest(state)` and reads it back as `const { history_digest, ...body } =
 * history; digest(body) !== history_digest`. This is that same statement, and it
 * is the ONE place this file restates a kernel rule — because the rule is what
 * the store has to recompute in order to be a store at all.
 */
export function journeyOneClockHistoryDigest(state) {
  if (!isPlainObject(state)) refuse("invalid_shape", "state must be an object", { path: "state" });
  const { history_digest, ...body } = state;
  return digest(body);
}

/**
 * Decompose one kernel state into the typed rows the record layer stores.
 *
 * ORDER IS PART OF THE HASH, twice over: the event chain is a linked list whose
 * links are hashes of the previous link, and pause_intervals is the projection's
 * own array order. Both carry an explicit zero-based ordinal so a reader
 * enumerates them the way the hash did.
 *
 * The assertion below is what keeps the decomposition total: if the kernel ever
 * gained a twenty-second state field, this function refuses rather than silently
 * dropping it from storage while it stays in the digest.
 */
export function journeyOneClockHistoryRows(state, tenant = ORGANIZATION_TENANT_ID) {
  closed(state, JOURNEY_ONE_CLOCK_STATE_FIELDS, "state");

  if (JOURNEY_ONE_CLOCK_LEGACY_SCHEMAS.includes(state.schema_version)) {
    refuse("legacy_history_migration_required",
      `this rail stores ${JOURNEY_ONE_CLOCK_SCHEMA} only; ${state.schema_version} names an origin digest the current kernel cannot recompute, and moving it forward is an explicit migration owned by whoever owns the durable store`,
      { invariant: "j1_clock_state_schema_current",
        history_schema_version: state.schema_version,
        current_schema_version: JOURNEY_ONE_CLOCK_SCHEMA });
  }
  if (state.schema_version !== JOURNEY_ONE_CLOCK_SCHEMA) {
    refuse("unsupported_history_schema",
      `this rail stores ${JOURNEY_ONE_CLOCK_SCHEMA} only`,
      { invariant: "j1_clock_state_schema_current",
        history_schema_version: state.schema_version });
  }

  const clock_key = journeyOneClockKeyForState(state, tenant);

  assertDigestRef(state.current_benchmark_manifest_digest, "state.current_benchmark_manifest_digest");
  assertDigestRef(state.history_digest, "state.history_digest");
  assertNullableDigestRef(state.completion_receipt_digest, "state.completion_receipt_digest");
  assertNullableDigestRef(state.completion_artifact_digest, "state.completion_artifact_digest");
  assertNullableDigestRef(state.completion_fixture_set_digest, "state.completion_fixture_set_digest");
  for (const field of ["base_deadline_at", "due_at", "miss_at", "completion_observed_at"]) {
    assertNullableTimestampText(state[field], `state.${field}`);
  }
  assertTimestampText(state.evaluated_at, "state.evaluated_at");
  for (const field of ["origin_receipt_ttl_policy_ms", "paused_ms"]) {
    if (!Number.isSafeInteger(state[field]) || state[field] < 0) {
      refuse("invalid_duration", `state.${field} must be a non-negative safe integer`,
        { path: `state.${field}`, actual: state[field] });
    }
  }
  if (state.completion_receipt_ttl_policy_ms !== null &&
      (!Number.isSafeInteger(state.completion_receipt_ttl_policy_ms) ||
       state.completion_receipt_ttl_policy_ms <= 0)) {
    refuse("invalid_duration", "state.completion_receipt_ttl_policy_ms must be a positive safe integer or null",
      { path: "state.completion_receipt_ttl_policy_ms" });
  }
  for (const field of ["base_deadline_resolution", "status"]) {
    if (typeof state[field] !== "string" || state[field] === "") {
      refuse("invalid_shape", `state.${field} must be a non-empty string`, { path: `state.${field}` });
    }
  }

  if (!Array.isArray(state.pause_intervals)) {
    refuse("invalid_shape", "state.pause_intervals must be an array", { path: "state.pause_intervals" });
  }
  if (!Array.isArray(state.events) || state.events.length === 0) {
    refuse("invalid_shape", "state.events must be a non-empty array", { path: "state.events" });
  }

  const scalars = {};
  for (const field of JOURNEY_ONE_CLOCK_SCALAR_FIELDS) scalars[field] = state[field];

  const pause_intervals = state.pause_intervals.map((interval, ordinal) => {
    closed(interval, JOURNEY_ONE_CLOCK_PAUSE_INTERVAL_FIELDS, `state.pause_intervals[${ordinal}]`);
    if (typeof interval.pause_id !== "string" || interval.pause_id === "") {
      refuse("invalid_shape", `state.pause_intervals[${ordinal}].pause_id must be a non-empty string`,
        { path: `state.pause_intervals[${ordinal}].pause_id` });
    }
    // VERBATIM. The kernel copies this string off the projection without
    // normalizing it, so `Z` and `+00:00` are two different stored values for
    // one instant and only the exact one reproduces the digest.
    assertNullableTimestampText(interval.ends_at, `state.pause_intervals[${ordinal}].ends_at`);
    return { ordinal, pause_id: interval.pause_id, ends_at: interval.ends_at };
  });

  const events = state.events.map((event, ordinal) => {
    closed(event, JOURNEY_ONE_CLOCK_EVENT_FIELDS, `state.events[${ordinal}]`);
    assertDigestRef(event.evidence_digest, `state.events[${ordinal}].evidence_digest`);
    assertDigestRef(event.event_digest, `state.events[${ordinal}].event_digest`);
    assertNullableDigestRef(event.previous_event_digest, `state.events[${ordinal}].previous_event_digest`);
    assertTimestampText(event.at, `state.events[${ordinal}].at`);
    assertTimestampText(event.recorded_at, `state.events[${ordinal}].recorded_at`);
    if (typeof event.type !== "string" || event.type === "") {
      refuse("invalid_shape", `state.events[${ordinal}].type must be a non-empty string`,
        { path: `state.events[${ordinal}].type` });
    }
    return { ordinal, ...Object.fromEntries(JOURNEY_ONE_CLOCK_EVENT_FIELDS.map(f => [f, event[f]])) };
  });

  return deepFreeze({
    schema_version: JOURNEY_ONE_CLOCK_HISTORY_ROWS_SCHEMA,
    tenant, clock_key,
    state_schema_version: state.schema_version,
    history_digest: state.history_digest,
    scalars, pause_intervals, events,
  });
}

/**
 * Rebuild one kernel state from its stored rows, in the kernel's declared field
 * order. Canonicalization sorts keys anyway, so the order is legibility rather
 * than correctness — but a state that reads like the kernel's STATE table is one
 * a reviewer can check against it.
 *
 * A GAP IN THE ORDINALS IS A MISSING ROW, which is exactly the shape a partial
 * insert leaves behind. Rebuilding from gapped rows would produce a shorter list
 * that hashes to something no kernel ever computed, so it refuses.
 */
export function journeyOneClockHistoryFromRows(rows) {
  if (!isPlainObject(rows)) refuse("invalid_shape", "rows must be an object", { path: "rows" });
  if (rows.schema_version !== JOURNEY_ONE_CLOCK_HISTORY_ROWS_SCHEMA) {
    refuse("wrong_rows_schema", `rows.schema_version must be "${JOURNEY_ONE_CLOCK_HISTORY_ROWS_SCHEMA}"`,
      { actual: rows.schema_version });
  }
  const ordered = (list, path) => {
    if (!Array.isArray(list)) refuse("invalid_shape", `${path} must be an array`, { path });
    const sorted = [...list].sort((a, b) => a.ordinal - b.ordinal);
    sorted.forEach((row, index) => {
      if (row?.ordinal !== index) {
        refuse("clock_row_ordinal_gap", `${path} is not contiguously ordinaled from zero`,
          { path, expected: index, actual: row?.ordinal });
      }
    });
    return sorted;
  };

  const scalars = rows.scalars;
  if (!isPlainObject(scalars)) refuse("invalid_shape", "rows.scalars must be an object", { path: "rows.scalars" });
  const state = {};
  for (const field of JOURNEY_ONE_CLOCK_STATE_FIELDS) {
    if (field === "pause_intervals") {
      state.pause_intervals = ordered(rows.pause_intervals, "rows.pause_intervals")
        .map(row => ({ pause_id: row.pause_id, ends_at: row.ends_at ?? null }));
    } else if (field === "events") {
      state.events = ordered(rows.events, "rows.events").map(row =>
        Object.fromEntries(JOURNEY_ONE_CLOCK_EVENT_FIELDS.map(f => [f, row[f] ?? null])));
    } else if (field === "history_digest") {
      state.history_digest = rows.history_digest;
    } else {
      if (!Object.hasOwn(scalars, field)) {
        refuse("clock_scalar_field_missing", `rows.scalars is missing ${field}; a state field that is hashed but not stored cannot be rebuilt`,
          { field });
      }
      state[field] = scalars[field];
    }
  }
  return state;
}

/**
 * IS THIS A HISTORY THIS RAIL MAY HOLD? Two questions, asked in one place and
 * answered by two owners.
 *
 * FIRST, THE STORE'S OWN QUESTION, which the kernel structurally cannot answer:
 * does the content rebuilt FROM THE ROWS THIS RAIL WOULD WRITE hash to the
 * digest it is filed under? The kernel reads whatever object it is handed; only
 * the decomposition and reconstruction here can say the persisted form
 * reproduces it. That is asked first, because a history that does not hash to
 * its own digest cannot meaningfully be read as anything.
 *
 * SECOND, THE KERNEL'S QUESTION, asked of the kernel:
 * readJourneyOneClockHistory applies the whole of the kernel's structural read —
 * the v1 refusal by name, the closed shape, the instant grammar, the paired
 * completion seals, the sticky miss bound to its own event, the linked and
 * self-hashing event chain, the status list, the Q008.D1 contradiction. This
 * file owns NO copy of any of that. A malformed history that used to survive
 * "closed shape plus a recomputed digest" refuses here now, under the kernel's
 * own error code, which travels unchanged for the same reason the recorder lets
 * an evaluate() refusal travel: a caller who would learn `erased_miss_history`
 * from the kernel should not meet a second vocabulary for the one fact.
 *
 * THE INSTANT IS THE HISTORY'S OWN evaluated_at, and that is deliberate. The
 * kernel's read is relative to an instant, and asking "was this readable when it
 * was computed" is the only question with a stable answer: reading a stored
 * history against the server's wall clock would make storability depend on when
 * the question was asked, and reading it against a caller-supplied instant would
 * put a caller in charge of the answer. It also introduces no new time policy —
 * every kernel-produced history satisfies it by construction, because the
 * kernel stamps evaluated_at and every event it records at that same instant.
 */
export function assertJourneyOneClockHistoryIntact(state, tenant = ORGANIZATION_TENANT_ID) {
  const rows = journeyOneClockHistoryRows(state, tenant);
  const rebuilt = journeyOneClockHistoryFromRows(rows);
  const recomputed = journeyOneClockHistoryDigest(rebuilt);
  if (recomputed !== state.history_digest) {
    refuse("clock_history_digest_mismatch",
      "the history rebuilt from its own rows does not hash to the digest it carries",
      { invariant: "j1_clock_content_rebuilds_to_its_digest",
        recomputed, carried: state.history_digest });
  }
  // Q008.D1 forbids CLAIMING DEADLINE SUCCESS once a miss is durably recorded.
  // The kernel refuses this shape under this very name a few lines below, and
  // the record layer states it in its own voice as well: this is one of the
  // invariants JOURNEY_ONE_CLOCK_APPEND_INVARIANTS marks `enforced_in: BOTH`, it
  // has a home in the SQL guard, and an id that appeared only in a declaration
  // would be a rule with no enforcement to point at. The two cannot disagree —
  // the same condition, the same name. The table is the count; the unit suite
  // reads `enforced_in` rather than this sentence.
  if (rebuilt.miss_at !== null && rebuilt.status === "completed_on_time") {
    refuse("deadline_success_claimed_after_recorded_miss",
      "a revision carrying a recorded miss may not carry status completed_on_time",
      { invariant: "j1_clock_no_deadline_success_after_recorded_miss",
        miss_at: rebuilt.miss_at, status: rebuilt.status });
  }
  readJourneyOneClockHistory(rebuilt, rebuilt.evaluated_at);
  return rows;
}

// ---------------------------------------------------------------------------
// THE APPEND DIFF. One revision against the head it claims to follow.
//
// This is the store's own job and could not be the kernel's: the kernel sees ONE
// history and cannot know it differs from the one already on disk. Every clause
// carries the invariant id from JOURNEY_ONE_CLOCK_APPEND_INVARIANTS.
// ---------------------------------------------------------------------------

/**
 * NOTE ON WHAT IS DELIBERATELY *NOT* CONSTRAINED, because a plausible-looking
 * guard here would be wrong:
 *
 *   * paused_ms IS NOT MONOTONE, and must not be. A pause whose end was unknown
 *     is counted to the evaluation instant; when the blocker's actual end is
 *     honestly reported later, the credited hours GO DOWN. The kernel documents
 *     that as the admissible report and refusing it here would leave the false
 *     one as the only writable answer.
 *   * due_at may move FORWARD PAST an existing miss_at. A pause legitimately
 *     approved before the deadline but reported after the miss credits its
 *     actual elapsed hours. That never erases the miss — which is why the miss
 *     invariant is stated on miss_at and never on due_at.
 *   * current_benchmark_manifest_digest may change. That is what a
 *     partner-signed amendment does, and the ORIGIN manifest digest is the one
 *     that may not move.
 *   * status may move between the kernel's own values. The store does not decide
 *     status and does not police its transitions; the one shape it refuses is
 *     the contradiction Q008.D1 names.
 */
export function assertJourneyOneClockAppendOnly(prior, next) {
  if (prior === null) return;
  if (!isPlainObject(prior) || !isPlainObject(next)) {
    refuse("invalid_shape", "an append diff needs two states", { path: "append" });
  }
  for (const field of JOURNEY_ONE_CLOCK_ORIGIN_FIELDS) {
    if (prior[field] !== next[field]) {
      refuse("clock_origin_reset_or_rebase",
        `${field} differs from the origin this clock was created with; this rail never resets, rebases or replaces an origin`,
        { invariant: "j1_clock_origin_never_rewritten", field,
          recorded: prior[field], supplied: next[field] });
    }
  }
  if (prior.completion_receipt_digest !== null) {
    for (const field of JOURNEY_ONE_CLOCK_COMPLETION_SEAL_FIELDS) {
      if (prior[field] !== next[field]) {
        refuse("clock_completion_seal_changed",
          `${field} was sealed when this clock recorded its completion and may not change`,
          { invariant: "j1_clock_completion_seals_never_changed", field,
            recorded: prior[field], supplied: next[field] });
      }
    }
  }
  if (prior.miss_at !== null && next.miss_at !== prior.miss_at) {
    refuse("clock_recorded_miss_removed",
      "a durably recorded miss is preserved in every later revision; it never un-sticks",
      { invariant: "j1_clock_recorded_miss_never_removed",
        recorded: prior.miss_at, supplied: next.miss_at });
  }
  if (next.events.length < prior.events.length) {
    refuse("clock_event_history_truncated",
      `this revision carries ${next.events.length} events where the head carries ${prior.events.length}`,
      { invariant: "j1_clock_events_are_append_only",
        recorded: prior.events.length, supplied: next.events.length });
  }
  prior.events.forEach((event, ordinal) => {
    if (next.events[ordinal].event_digest !== event.event_digest) {
      refuse("clock_event_history_rewritten",
        `event ${ordinal} is not the event the head recorded at that position`,
        { invariant: "j1_clock_events_are_append_only", ordinal,
          recorded: event.event_digest, supplied: next.events[ordinal].event_digest });
    }
  });
  if (Date.parse(next.evaluated_at) < Date.parse(prior.evaluated_at)) {
    refuse("clock_backdated_evaluation",
      "a revision may not be evaluated before the head it follows",
      { invariant: "j1_clock_no_backdated_evaluation",
        recorded: prior.evaluated_at, supplied: next.evaluated_at });
  }
}

// ---------------------------------------------------------------------------
// THE JOURNAL PORT.
//
// Two implementations, one contract. A journal must, for one clock key,
// SERIALIZE its appends: the whole read-head / diff / insert section runs with
// no other append to the same clock interleaved. Everything else the store does
// is pure and needs no help.
//
//   runAppend(clockKey, { idempotencyKey, build }) -> { ...revision, replayed }
//     Serialized per clock key. Looks the idempotency key up FIRST; on a hit it
//     returns the stored revision and refuses a changed payload. Otherwise it
//     calls build({ clock, head }) — where `head` is the current head revision
//     or null — and persists exactly what build returns.
//   readClock(clockKey) -> clock row or null
//   readRevisions(clockKey) -> revisions ascending by revision_ordinal
//   readScopeBindings({ clockScopeKey, clockKey }) -> { by_scope, by_clock }
//     Either side may be null. This is the READ a creation for a new origin has
//     to meet: it is what turns "one authoritative scope holds one clock" into
//     an answerable question rather than an assertion.
//   bindScope({ clock_key, clock_scope_key, clock_scope_ref, tenant, scope })
//     Idempotent for an exact repeat, and the point at which a second clock for
//     one scope, or a clock rebound to a second scope, is refused. It is called
//     inside the same serialized section as the append it belongs to.
//   readRevisionByIdempotencyKey(idempotencyKey) -> revision row or null
//     OPTIONAL, AND READ-ONLY. The same lookup runAppend already performs inside
//     its serialized section, exposed on its own so a caller can ask "did this
//     request already land?" WITHOUT opening a write. Both implementations here
//     answer it from the row they already store — the reference journal from its
//     idempotency map, the durable one from ops.j1_clock_revision_by_idempotency_key,
//     which exists, is already granted to the reader bundle and is unchanged by
//     this seam. A journal that does not implement it makes
//     readRecordedRevisionForKey refuse BY NAME rather than answer "no".
// ---------------------------------------------------------------------------

/**
 * THE NON-DURABLE REFERENCE JOURNAL. Its name says what it is: this journal
 * keeps revisions in a Map and loses everything when the process exits. It
 * exists so the store's CAS, idempotency, diff and readback can be exercised for
 * real in-process, and so a reviewer can read one complete implementation of the
 * port. IT IS NOT A MODEL OF POSTGRESQL and proves nothing about the SQL: it has
 * one writer, no transactions and no lock manager. `durable` is false and is
 * carried on the object so nothing can quietly treat it as a store.
 */
export function createEphemeralJourneyOneClockJournal({ now = Date.now } = {}) {
  if (typeof now !== "function") refuse("invalid_shape", "now must be a function", { path: "now" });
  const clocks = new Map();       // clock_key -> clock row
  const revisions = new Map();    // clock_key -> revision rows, ascending
  const byIdempotency = new Map();// idempotency key -> revision row
  const queues = new Map();       // clock_key -> tail promise
  const byScopeKey = new Map();   // clock_scope_key -> scope binding row
  const byClockKey = new Map();   // clock_key -> scope binding row

  const serialize = (clockKey, fn) => {
    const previous = queues.get(clockKey) ?? Promise.resolve();
    const settled = previous.then(fn, fn);
    // The queue must survive a rejection, or one refusal would wedge the clock.
    queues.set(clockKey, settled.then(() => undefined, () => undefined));
    return settled;
  };

  return Object.freeze({
    durable: false,
    kind: "ephemeral-reference-journal",
    async runAppend(clockKey, { idempotencyKey, build }) {
      return serialize(clockKey, async () => {
        const replay = byIdempotency.get(idempotencyKey);
        const list = revisions.get(clockKey) ?? [];
        const head = list.length ? list[list.length - 1] : null;
        const clock = clocks.get(clockKey) ?? null;
        const candidate = await build({ clock, head, replay: replay ?? null });
        if (candidate === null) return { ...replay, replayed: true };
        const recorded_at = new Date(now()).toISOString();
        if (head && Date.parse(recorded_at) < Date.parse(head.recorded_at)) {
          refuse("clock_backdated_revision",
            "the server clock moved backwards between two revisions of one clock",
            { invariant: "j1_clock_no_backdated_evaluation",
              recorded: head.recorded_at, supplied: recorded_at });
        }
        if (!clock) {
          clocks.set(clockKey, deepFreeze({
            clock_key: clockKey, tenant: candidate.tenant, clock_ref: candidate.clock_ref,
            clock_scope_key: candidate.clock_scope_key,
            clock_scope_ref: candidate.clock_scope_ref,
            state_schema_version: candidate.state_schema_version,
            origin_receipt_digest: candidate.scalars.origin_receipt_digest,
            origin_at: candidate.scalars.origin_at,
            origin_benchmark_manifest_digest: candidate.scalars.origin_benchmark_manifest_digest,
            created_at: recorded_at, created_by_actor_id: candidate.written_by_actor_id,
          }));
          revisions.set(clockKey, []);
        }
        const stored = deepFreeze({ ...candidate, recorded_at,
          revision_ordinal: (revisions.get(clockKey) ?? []).length });
        revisions.get(clockKey).push(stored);
        byIdempotency.set(idempotencyKey, stored);
        return { ...stored, replayed: false };
      });
    },
    async readClock(clockKey) { return clocks.get(clockKey) ?? null; },
    async readRevisions(clockKey) { return [...(revisions.get(clockKey) ?? [])]; },
    /** The same map runAppend consults, read without opening an append. */
    async readRevisionByIdempotencyKey(idempotencyKey) {
      return byIdempotency.get(idempotencyKey) ?? null;
    },
    async readScopeBindings({ clockScopeKey = null, clockKey = null } = {}) {
      return {
        by_scope: clockScopeKey === null ? null : byScopeKey.get(clockScopeKey) ?? null,
        by_clock: clockKey === null ? null : byClockKey.get(clockKey) ?? null,
      };
    },
    /**
     * The two refusals are HERE and not only in the caller, because a journal
     * that will hold a second clock for one scope is a journal that has the
     * hole in it. The check and the write are one synchronous step, so two
     * creations racing under one scope cannot both pass the check.
     */
    async bindScope({ clock_key, clock_scope_key, clock_scope_ref, tenant } = {}) {
      const bound = byScopeKey.get(clock_scope_key) ?? null;
      const existing = byClockKey.get(clock_key) ?? null;
      if (bound && bound.clock_key !== clock_key) {
        refuse("clock_scope_already_bound",
          "this authoritative clock scope already holds a clock; a different origin does not start a second one",
          { invariant: "j1_clock_scope_binds_one_clock", clock_scope_key,
            bound_clock_key: bound.clock_key, supplied_clock_key: clock_key });
      }
      if (existing && existing.clock_scope_key !== clock_scope_key) {
        refuse("clock_scope_changed",
          "this clock was created under another authoritative scope, and a clock is never rebound",
          { invariant: "j1_clock_scope_sealed_at_creation", clock_key,
            bound_clock_scope_key: existing.clock_scope_key, supplied_clock_scope_key: clock_scope_key });
      }
      // THE LABEL IS SEALED AT BINDING. It is not identity — the key above
      // ignores it, which is what makes a relabelled scope the SAME scope — and
      // precisely because it is not identity, the record must not quietly hold
      // two names for one scope or silently overwrite the one it was bound
      // under. Renaming an accepted scope is an explicit external act.
      if (bound && bound.clock_scope_ref !== clock_scope_ref) {
        refuse("clock_scope_label_changed",
          "this authoritative scope was bound under another label; the label is provenance, is recorded once, and is never rewritten by a later write",
          { invariant: "j1_clock_scope_label_is_not_identity", clock_scope_key,
            bound_clock_scope_ref: bound.clock_scope_ref, supplied_clock_scope_ref: clock_scope_ref });
      }
      const row = bound ?? deepFreeze({ clock_key, clock_scope_key, clock_scope_ref, tenant,
        bound_at: new Date(now()).toISOString() });
      byScopeKey.set(clock_scope_key, row);
      byClockKey.set(clock_key, row);
      return row;
    },
  });
}

/**
 * THE DURABLE JOURNAL: the binding to ops/journey-one-clock-store.candidate.sql.
 *
 * It is thin on purpose. Serialization is `ops.j1_clock_lock(text)`, which takes
 * a transaction-scoped advisory lock on the clock key AND, when the clock row
 * exists, FOR UPDATE on it — the advisory half is what serializes two concurrent
 * CREATIONS, which have no row to lock yet. The append itself is one call to the
 * definer function `ops.j1_clock_append_revision(...)`, because direct INSERT on
 * this rail is granted to nobody: a writer holding a raw connection cannot
 * attribute a revision to another actor or step around the guard.
 *
 * THE GUARD IS NOT REDUNDANT WITH THE STORE. Everything the store checks in
 * JavaScript, ops.j1_clock_append_guard() checks again from the persisted rows,
 * because a direct caller of the SQL function never runs the JavaScript at all.
 *
 * `query` is the repository's ordinary connection handle contract:
 * `query(text, params) -> { rows }`, inside the caller's transaction.
 *
 * NEVER EXECUTED HERE. The candidate SQL has not been applied and this journal
 * has not been run against a database in this slice; see the report.
 */
export function createPostgresJourneyOneClockJournal({ query } = {}) {
  if (typeof query !== "function") {
    refuse("invalid_shape", "a postgres journal needs a query function", { path: "query" });
  }
  const one = async (sql, params) => (await query(sql, params)).rows[0] ?? null;

  return Object.freeze({
    durable: true,
    kind: "postgres-journal",
    async runAppend(clockKey, { idempotencyKey, clockScopeKey = null, build }) {
      // The serialized section opens here and closes when the caller's
      // transaction commits. Both halves are taken by one function so a future
      // writer cannot join the protocol while taking only one of them.
      await query("select ops.j1_clock_lock($1::text)", [clockKey]);
      // AND THE SCOPE, taken second and always in that order, so two creations
      // for one scope under two different origins — which hold no lock in
      // common, because their clock keys differ — cannot both read an unbound
      // scope and both bind it. The unique constraint underneath is the
      // structural backstop; this is what makes the refusal a named one.
      if (clockScopeKey !== null) {
        await query("select ops.j1_clock_scope_lock($1::text)", [clockScopeKey]);
      }
      const head = (await one("select ops.j1_clock_head($1::text) as head", [clockKey]))?.head ?? null;
      const clock = (await one("select ops.j1_clock_row($1::text) as clock", [clockKey]))?.clock ?? null;
      const replay = (await one("select ops.j1_clock_revision_by_idempotency_key($1::uuid) as revision",
        [idempotencyKey]))?.revision ?? null;
      const candidate = await build({ clock, head, replay });
      // A REPLAY IS NOT A SECOND WRITE. build() has already refused a key whose
      // payload changed; a null candidate means it matched, so the stored row is
      // returned and no INSERT is attempted.
      if (candidate === null) return { ...replay, replayed: true };
      const result = (await one(
        `select ops.j1_clock_append_revision(
           $1::text,$2::text,$3::text,$4::text,$5::uuid,$6::text,$7::jsonb,$8::jsonb,$9::jsonb,$10::jsonb) as revision`,
        [clockKey, candidate.tenant, candidate.clock_ref,
          candidate.expected_prior_history_digest, idempotencyKey, candidate.history_digest,
          JSON.stringify(candidate.scalars), JSON.stringify(candidate.pause_intervals),
          JSON.stringify(candidate.events), JSON.stringify(candidate.provenance)]))?.revision;
      if (!result) {
        refuse("clock_append_returned_nothing",
          "ops.j1_clock_append_revision returned no revision", { clock_key: clockKey });
      }
      return result;
    },
    async readClock(clockKey) {
      return (await one("select ops.j1_clock_row($1::text) as clock", [clockKey]))?.clock ?? null;
    },
    async readRevisions(clockKey) {
      return (await one("select ops.j1_clock_revisions($1::text) as revisions", [clockKey]))?.revisions ?? [];
    },
    /**
     * THE SAME FUNCTION runAppend CALLS, and no new one. It is `stable security
     * definer`, is already granted to the reader bundle, and takes no lock: this
     * is a read, and calling it outside the serialized section is exactly what
     * makes it useful — it answers "did this request already land?" without
     * opening a write. No schema, grant or SQL text changed for it.
     */
    async readRevisionByIdempotencyKey(idempotencyKey) {
      return (await one("select ops.j1_clock_revision_by_idempotency_key($1::uuid) as revision",
        [idempotencyKey]))?.revision ?? null;
    },
    async readScopeBindings({ clockScopeKey = null, clockKey = null } = {}) {
      return (await one("select ops.j1_clock_scope_bindings($1::text,$2::text) as bindings",
        [clockScopeKey, clockKey]))?.bindings ?? { by_scope: null, by_clock: null };
    },
    /**
     * The scope binding is written by its own definer function, which derives
     * the scope key from the scope's own fields exactly as this module does and
     * refuses a second clock for one scope from the persisted rows. The whole
     * scope object travels, not the derived key: a key the database accepted on
     * trust would be a caller-chosen address wearing a hash.
     */
    async bindScope({ clock_key, scope } = {}) {
      return (await one("select ops.j1_clock_bind_scope($1::text,$2::jsonb) as binding",
        [clock_key, JSON.stringify(scope)]))?.binding ?? null;
    },
  });
}

// ---------------------------------------------------------------------------
// THE STORE. Identity, CAS, idempotency, the append diff and the readback.
// ---------------------------------------------------------------------------

/**
 * Derive the writing seat from the LIVE authenticated actor.
 *
 * NOT A HUMAN GATE, AND NOT A NEW AUTHORITY. Recording a computation is
 * ordinary trusted-writer work: it is not a partner acceptance, no humanOnly
 * flag is introduced here, and holding an authority login is not read as being a
 * human. What this does is refuse an UNAUTHENTICATED writer and record the class
 * identity.js derives for the live actor, so a revision is attributable.
 *
 * The class is computed here and now. It is never read from arguments and never
 * from a stored row: a projection that copied a stored class string would turn
 * this into the caller boolean the whole rail exists to prevent.
 */
export function deriveJourneyOneClockWriter(actor) {
  if (!isPlainObject(actor) || !isKnownActor(actor.slug)) {
    refuse("clock_writer_identity_unavailable",
      "recording a Journey 1 clock revision requires an authenticated runtime actor; none was supplied",
      { path: "actor" });
  }
  return deepFreeze({
    actor_id: actor.slug,
    authority_class: authorizationClassForActor(actor),
    authority_class_source: "identity.authorizationClassForActor",
    // Said on the object so nothing downstream can read attribution as approval.
    grants_authority: false,
  });
}

/**
 * The provenance every revision carries. NARROWLY SCOPED ON PURPOSE: it records
 * WHICH code computed the state and WHICH projection schema it was computed
 * from, and then says in its own field that this record layer did not verify the
 * inputs. It is not a receipt and it is not a verification.
 */
function provenanceFor(writer, verifier_ref) {
  if (typeof verifier_ref !== "string" || !verifier_ref.startsWith("safe:")) {
    refuse("invalid_reference",
      "provenance.verifier_ref must be a safe: reference NAMING the installed verifier; it is a name, never a proof",
      { path: "verifier_ref", actual: verifier_ref });
  }
  return deepFreeze({
    computed_by: "mcp-server/src/journey-one-clock.v5.js",
    kernel_state_schema_version: JOURNEY_ONE_CLOCK_SCHEMA,
    projection_schema_version: JOURNEY_ONE_CLOCK_PROJECTION,
    verifier_ref,
    input_authority: "trusted_projection_not_independently_verified_by_this_record_layer",
    written_by_actor_id: writer.actor_id,
    written_by_authority_class: writer.authority_class,
  });
}

/**
 * The durable Journey 1 clock history store.
 *
 * `journal` is either journal implementation above. `actor` is the live
 * authenticated runtime actor the server established. `now` is the SERVER clock
 * and defaults to Date.now; a caller-supplied `as_of` never reaches it.
 *
 * `clock_scope` IS A CONSTRUCTION-TIME BINDING AND IS DELIBERATELY NOT A
 * REQUEST FIELD. It names the authoritative scope this store writes clocks for,
 * and it is exactly the seam a per-request scope would destroy: a caller who
 * could choose the scope beside the origin could choose a fresh pair of both and
 * be back where the derived key alone left us. Only trusted server code
 * constructs a store, so only trusted server code names a scope.
 *
 * READS DO NOT NEED IT and are available without one, because a readback
 * addresses a clock that already exists by its own key. WRITES DO: record()
 * refuses by name when no scope was bound, rather than storing a clock nothing
 * can later prove was the only one for its program.
 */
export function createJourneyOneClockStore({
  journal, actor, tenant = ORGANIZATION_TENANT_ID, now = Date.now, clock_scope = null,
} = {}) {
  if (!journal || typeof journal.runAppend !== "function" ||
      typeof journal.readClock !== "function" || typeof journal.readRevisions !== "function" ||
      typeof journal.readScopeBindings !== "function" || typeof journal.bindScope !== "function") {
    refuse("invalid_shape", "a clock store needs a journal implementing the port", { path: "journal" });
  }
  if (typeof now !== "function") refuse("invalid_shape", "now must be a function", { path: "now" });
  const writer = deriveJourneyOneClockWriter(actor);
  if (tenant !== ORGANIZATION_TENANT_ID) {
    refuse("wrong_tenant", `this rail stores clocks for ${ORGANIZATION_TENANT_ID} only`,
      { invariant: "j1_clock_tenant_bound", supplied: tenant });
  }
  // Read once, here, so a malformed scope is a refusal at construction rather
  // than a surprise on the first write.
  const scope = clock_scope === null ? null : journeyOneClockScopeBinding(clock_scope);
  if (scope !== null && scope.tenant !== tenant) {
    refuse("wrong_tenant", "the clock scope names another tenant than this store",
      { invariant: "j1_clock_tenant_bound", supplied: scope.tenant, expected: tenant });
  }

  /** Rebuild and verify one stored revision, refusing a tampered readback. */
  const rebuild = (clockKey, revision) => {
    // The tenant first, so a foreign row meets the refusal that names the
    // problem rather than one about an identity it could never have derived.
    if (revision.tenant !== tenant) {
      refuse("cross_tenant_clock_history",
        "the stored revision belongs to another tenant",
        { invariant: "j1_clock_tenant_bound", stored: revision.tenant, reading_as: tenant });
    }
    const rows = {
      schema_version: JOURNEY_ONE_CLOCK_HISTORY_ROWS_SCHEMA,
      tenant: revision.tenant, clock_key: revision.clock_key ?? clockKey,
      state_schema_version: revision.state_schema_version,
      history_digest: revision.history_digest,
      scalars: revision.scalars, pause_intervals: revision.pause_intervals,
      events: revision.events,
    };
    const state = journeyOneClockHistoryFromRows(rows);
    const recomputed = journeyOneClockHistoryDigest(state);
    if (recomputed !== revision.history_digest) {
      refuse("clock_readback_tampered",
        "the stored revision no longer rebuilds to the digest it was written under",
        { invariant: "j1_clock_content_rebuilds_to_its_digest",
          clock_key: clockKey, revision_ordinal: revision.revision_ordinal,
          recomputed, stored: revision.history_digest });
    }
    const derived = journeyOneClockKeyForState(state, revision.tenant ?? tenant);
    if (derived !== clockKey) {
      refuse("clock_identity_mismatch",
        "the stored revision's own origin derives a different clock than the one it is filed under",
        { invariant: "j1_clock_identity_derived_from_origin",
          clock_key: clockKey, derived_clock_key: derived });
    }
    // AND IT IS STILL A HISTORY THE KERNEL WILL READ. A revision that reached
    // the tables by some other route — a direct SQL writer, a restored dump —
    // is not made readable by hashing to its own digest, and serving it as
    // ordinary source is how a malformed record becomes an input. The kernel's
    // own refusal travels unchanged.
    readJourneyOneClockHistory(state, state.evaluated_at);
    return state;
  };

  return Object.freeze({
    writer,
    journal_is_durable: journal.durable === true,
    /**
     * The authoritative scope this store writes for, or null. Zero effect.
     * `scope_identity` is the six-field preimage the key is derived from, and
     * `scope` is the whole supplied binding including its human label: the two
     * are separate here for the same reason they are separate in the hash.
     */
    clock_scope: scope === null ? null : deepFreeze({
      clock_scope_key: scope.clock_scope_key, clock_scope_ref: scope.clock_scope_ref,
      tenant: scope.tenant, scope: copy(scope.scope),
      scope_identity: copy(scope.scope_identity) }),

    /**
     * Which clock this store's authoritative scope already holds, or null.
     *
     * A trusted integration about to start a clock is entitled to ASK before it
     * presents an origin, rather than discovering the answer as a refusal. It
     * reads and returns nothing else: no clock is created, no scope is bound,
     * and a null here is "this scope holds no clock in this record layer" and
     * never "no clock exists".
     */
    async readClockKeyForScope() {
      if (scope === null) {
        refuse("clock_scope_binding_required",
          "this store was constructed without an authoritative clock scope, so there is no scope to ask about",
          { invariant: "j1_clock_scope_binds_one_clock" });
      }
      const bindings = await journal.readScopeBindings({ clockScopeKey: scope.clock_scope_key });
      return deepFreeze({
        clock_scope_key: scope.clock_scope_key,
        clock_scope_ref: scope.clock_scope_ref,
        clock_key: bindings?.by_scope?.clock_key ?? null,
        record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE],
        effects: V5_NO_EFFECTS,
      });
    },

    /**
     * WHAT ONE IDEMPOTENCY KEY ALREADY WROTE, if anything. READ ONLY.
     *
     * WHY THIS EXISTS. `record()` learns a key was used only from INSIDE its
     * serialized section, and by then it has already been handed a state that was
     * computed against whatever the head is NOW. A caller retrying after a lost
     * response therefore arrives with a legitimately different prior and meets
     * `clock_idempotency_key_reused` — a true statement about the payload and a
     * misleading one about the situation. This is the question asked BEFORE any
     * of that: did this exact request already land, and what did it land against?
     *
     * IT ANSWERS AND DECIDES NOTHING. It does not compare a caller's intent, does
     * not judge whether a retry is legitimate, and cannot be used to skip a write:
     * the seat that retries has to RE-COMPUTE against the prior returned here and
     * match the recorded history digest itself. Everything below is a validated
     * read of rows that already exist — the revision is rebuilt from its own rows
     * and re-hashed by the same `rebuild` every readback uses, so a tampered row
     * refuses here exactly as it does there, and the prior it names is rebuilt the
     * same way rather than believed.
     *
     * THE PRIOR IS THE POINT. `expected_prior_history_digest` is the CAS token the
     * recorded revision was written under; the history it names is the ONLY
     * history a faithful re-computation of that request can be made against. A
     * recorded prior this rail cannot produce refuses BY NAME rather than being
     * quietly replaced with the current head, which would be the rebase this whole
     * rail exists to refuse.
     */
    async readRecordedRevisionForKey(idempotency_key) {
      assertUuid(idempotency_key, "idempotency_key");
      if (typeof journal.readRevisionByIdempotencyKey !== "function") {
        refuse("clock_idempotency_read_unavailable",
          "this journal cannot answer what one idempotency key already wrote without opening an append, so a request's outcome cannot be read back. The port method is optional and both journals in this module implement it; a journal without it refuses here rather than reporting 'no revision', which a caller would read as 'this request never landed'",
          { invariant: "j1_clock_idempotency_key_binds_its_payload",
            path: "journal.readRevisionByIdempotencyKey" });
      }
      const absent = () => deepFreeze({
        schema_version: JOURNEY_ONE_CLOCK_REPLAY_READBACK_SCHEMA,
        idempotency_key, tenant, exists: false,
        record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE],
        effects: V5_NO_EFFECTS,
      });
      const row = await journal.readRevisionByIdempotencyKey(idempotency_key);
      if (!row) return absent();
      if (!isPlainObject(row) || typeof row.clock_key !== "string") {
        refuse("clock_readback_tampered",
          "the revision this idempotency key names is not a revision row",
          { invariant: "j1_clock_content_rebuilds_to_its_digest", idempotency_key });
      }
      if (row.tenant !== tenant) {
        refuse("cross_tenant_clock_history",
          "that request belongs to another tenant",
          { invariant: "j1_clock_tenant_bound", stored: row.tenant, reading_as: tenant });
      }
      // REBUILT AND RE-HASHED, not believed: the same read a clock readback does.
      const history = rebuild(row.clock_key, row);
      const priorDigest = assertNullableDigestRef(
        row.expected_prior_history_digest ?? null, "expected_prior_history_digest");
      let prior = null;
      let priorOrdinal = null;
      if (priorDigest !== null) {
        // WHICH REVISION THE PRIOR IS, DERIVED FROM THE COMPARE-AND-SWAP RATHER
        // THAN SEARCHED FOR. A revision filed at ordinal N was refused unless its
        // prior digest was the head's, and a clock holding N revisions has its
        // head at ordinal N-1 — ordinals are zero-based and contiguous, which
        // read() asserts on every readback. So the prior is exactly N-1, and
        // taking it by ordinal is what makes this exact: locating it by digest
        // alone would pick the FIRST row carrying that digest, and two revisions
        // of one clock can legitimately carry the same history digest.
        // The digest is then checked against that row, so the ordinal rule is
        // verified against the record rather than assumed over it.
        const expectedOrdinal = Number.isSafeInteger(row.revision_ordinal)
          ? row.revision_ordinal - 1 : -1;
        const revisions = await journal.readRevisions(row.clock_key);
        const found = expectedOrdinal < 0 ? null
          : (Array.isArray(revisions) ? revisions : [])
            .find(revision => revision?.revision_ordinal === expectedOrdinal) ?? null;
        if (found === null || found.history_digest !== priorDigest) {
          refuse("clock_replay_prior_history_unavailable",
            "the recorded revision names a prior history this rail cannot produce at the ordinal its compare-and-swap wrote it against, so the request it recorded cannot be re-computed against the history it was actually written against. The current head is not a substitute: computing against it would be the rebase this rail refuses",
            { invariant: "j1_clock_exact_prior_history_digest",
              clock_key: row.clock_key, expected_prior_history_digest: priorDigest,
              revision_ordinal: row.revision_ordinal,
              expected_prior_revision_ordinal: expectedOrdinal < 0 ? null : expectedOrdinal,
              found_prior_history_digest: found?.history_digest ?? null });
        }
        prior = rebuild(row.clock_key, found);
        priorOrdinal = found.revision_ordinal;
      }
      const bound = (await journal.readScopeBindings({ clockKey: row.clock_key }))?.by_clock ?? null;
      return deepFreeze({
        schema_version: JOURNEY_ONE_CLOCK_REPLAY_READBACK_SCHEMA,
        idempotency_key, tenant, exists: true,
        clock_key: row.clock_key,
        clock_ref: row.clock_ref ?? null,
        clock_scope_bound: bound !== null,
        clock_scope_key: bound?.clock_scope_key ?? null,
        clock_scope_ref: bound?.clock_scope_ref ?? null,
        revision_ordinal: row.revision_ordinal,
        history_digest: row.history_digest,
        expected_prior_history_digest: priorDigest,
        // The ordinal of the revision that prior digest names, read off the row
        // this rail actually rebuilt rather than computed beside it. Null when
        // the recorded revision was a creation, which is the same thing null
        // means everywhere else on this rail: there was no prior.
        prior_revision_ordinal: priorOrdinal,
        recorded_at: row.recorded_at,
        // The seat comparing an actor against this one is comparing two derived
        // classes, not a claim: `written_by_actor_id` was derived from the LIVE
        // actor at write time by deriveJourneyOneClockWriter and is never read
        // back to decide anything here.
        written_by_actor_id: row.provenance?.written_by_actor_id ?? row.written_by_actor_id ?? null,
        provenance: copy(row.provenance ?? null),
        history: copy(history),
        prior_history: prior === null ? null : copy(prior),
        record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE],
        deadline_accepted_by_record_layer: false,
        effects: V5_NO_EFFECTS,
      });
    },

    /**
     * Persist one kernel-produced state as the next revision of its own clock.
     *
     * `expected_prior_history_digest` IS REQUIRED AND MAY BE EXPLICITLY NULL.
     * There is no default: an omitted CAS token is a caller who does not know
     * what they are appending to, and a default of null would silently turn
     * every such call into a creation attempt.
     */
    async record(args = {}) {
      // THE WHOLE ARGUMENT OBJECT, not the destructured subset. An extra key
      // that reads like a caller asserting the authority this rail derives is
      // refused BY NAME before anything else happens, so `{ verified: true }`
      // riding beside a legitimate write meets a refusal rather than being
      // silently ignored — silently ignored is how a caller comes to believe it
      // was honoured.
      assertNoSelfAssertedAuthority(args, "record");
      const {
        state, expected_prior_history_digest, idempotency_key,
        claimed_history_digest = null, clock_ref = null, verifier_ref,
      } = args;
      assertUuid(idempotency_key, "idempotency_key");
      // NO SCOPE, NO WRITE. An unbound clock is precisely the one a second
      // origin could create beside a running one, and this rail will not store
      // one and call the omission a default.
      if (scope === null) {
        refuse("clock_scope_binding_required",
          "recording a Journey 1 clock revision requires the authoritative clock scope this store writes for; it is bound at construction by trusted server code and is never a request field, because a caller who could choose the scope beside the origin could create a second clock for one program",
          { invariant: "j1_clock_scope_binds_one_clock" });
      }
      if (expected_prior_history_digest === undefined) {
        refuse("missing_prior_history_digest",
          "every write names the exact history_digest it is appending to, or an explicit null to create the clock",
          { invariant: "j1_clock_exact_prior_history_digest" });
      }
      assertNullableDigestRef(expected_prior_history_digest, "expected_prior_history_digest");
      if (clock_ref !== null && (typeof clock_ref !== "string" || !CLOCK_REF.test(clock_ref))) {
        refuse("invalid_clock_ref",
          "clock_ref is an optional legibility label; it carries no authority and never selects a clock",
          { path: "clock_ref" });
      }

      // The content is rebuilt and re-hashed HERE, from the rows this call is
      // about to store, before anything is compared. A caller's claimed hash is
      // only ever the loser of that comparison.
      const rows = assertJourneyOneClockHistoryIntact(state, tenant);
      if (claimed_history_digest !== null) {
        assertDigestRef(claimed_history_digest, "claimed_history_digest");
        if (claimed_history_digest !== rows.history_digest) {
          refuse("claimed_history_digest_mismatch",
            "the claimed history digest is not the one this content produces",
            { invariant: "j1_clock_claimed_history_digest_is_never_trusted",
              recomputed: rows.history_digest, claimed: claimed_history_digest });
        }
      }
      const clockKey = rows.clock_key;
      const provenance = provenanceFor(writer, verifier_ref);

      return journal.runAppend(clockKey, {
        idempotencyKey: idempotency_key,
        clockScopeKey: scope.clock_scope_key,
        build: async ({ clock, head, replay }) => {
          // IDEMPOTENCY IS A REPLAY, NOT A SECOND WRITE, and a key that carried
          // a different payload is a different request wearing the same key.
          if (replay) {
            if (replay.clock_key !== clockKey ||
                replay.history_digest !== rows.history_digest ||
                (replay.expected_prior_history_digest ?? null) !== expected_prior_history_digest) {
              refuse("clock_idempotency_key_reused",
                `idempotency key ${idempotency_key} was already used for a different Journey 1 clock revision`,
                { invariant: "j1_clock_idempotency_key_binds_its_payload",
                  recorded: { clock_key: replay.clock_key, history_digest: replay.history_digest,
                    expected_prior_history_digest: replay.expected_prior_history_digest ?? null },
                  supplied: { clock_key: clockKey, history_digest: rows.history_digest,
                    expected_prior_history_digest } });
            }
            return null;
          }
          if (clock && clock.tenant !== tenant) {
            refuse("cross_tenant_clock_history", "that clock belongs to another tenant",
              { invariant: "j1_clock_tenant_bound" });
          }
          // THE SCOPE READ, BEFORE THE COMPARE-AND-SWAP AND INSIDE THE SAME
          // SERIALIZED SECTION. This is the clause a caller presenting a NEW
          // ORIGIN meets: their origin derives a key with no head, so the CAS
          // below would have nothing to refuse them with, and the running clock
          // this scope already holds is what refuses instead. A changed origin
          // receipt, an amended benchmark manifest read as the origin manifest,
          // and a copied history with the old state left out all arrive here.
          const bindings = await journal.readScopeBindings({
            clockScopeKey: scope.clock_scope_key, clockKey });
          const boundClock = bindings?.by_scope ?? null;
          const boundScope = bindings?.by_clock ?? null;
          if (boundClock && boundClock.clock_key !== clockKey) {
            refuse("clock_scope_already_bound",
              "this authoritative clock scope already holds a clock, and this revision's origin derives a different one. A new origin for a scope that already has a clock is a reset wearing a new address: append to the clock that exists, or refuse",
              { invariant: "j1_clock_scope_binds_one_clock",
                clock_scope_key: scope.clock_scope_key,
                clock_scope_ref: scope.clock_scope_ref,
                bound_clock_key: boundClock.clock_key, supplied_clock_key: clockKey });
          }
          if (boundScope && boundScope.clock_scope_key !== scope.clock_scope_key) {
            refuse("clock_scope_changed",
              "this clock was created under another authoritative scope; a clock is bound once and is never rebound",
              { invariant: "j1_clock_scope_sealed_at_creation", clock_key: clockKey,
                bound_clock_scope_key: boundScope.clock_scope_key,
                supplied_clock_scope_key: scope.clock_scope_key });
          }
          // THE LABEL SEAL, HERE AS WELL AS IN THE JOURNAL. Both bindings were
          // just read, and the label is on each of them. The reference journal
          // refuses a relabelled scope inside bindScope, but on the DURABLE path
          // that refusal lives in ops.j1_clock_bind_scope — SQL that has never
          // run — so without this clause the store would pass its whole read,
          // its compare-and-swap and its append-only diff before meeting the
          // seal, and would meet it in the one home this rail cannot execute.
          // The label is not identity — the key ignores it, which is what makes
          // a relabelled scope the SAME scope — and precisely because it is not
          // identity the record must not end up holding two names for one scope.
          // `!== undefined` so a journal that does not carry the label back
          // cannot false-refuse a legitimate append; both journals here do.
          for (const row of [boundClock, boundScope]) {
            if (row && row.clock_scope_key === scope.clock_scope_key &&
                row.clock_scope_ref !== undefined &&
                row.clock_scope_ref !== scope.clock_scope_ref) {
              refuse("clock_scope_label_changed",
                "this authoritative scope was bound under another label; the label is provenance, is recorded once, and is never rewritten by a later write",
                { invariant: "j1_clock_scope_label_is_not_identity",
                  clock_scope_key: scope.clock_scope_key,
                  bound_clock_scope_ref: row.clock_scope_ref,
                  supplied_clock_scope_ref: scope.clock_scope_ref });
            }
          }
          // THE COMPARE-AND-SWAP. An explicit null creates and succeeds only
          // against an empty clock; anything else must be the exact head.
          if (expected_prior_history_digest === null) {
            if (head) {
              refuse("clock_already_exists",
                "this clock already has history; a null prior is a creation and a clock is created once. A caller-chosen label cannot restart a clock, because the identity is derived from the kernel's own origin",
                { invariant: "j1_clock_exact_prior_history_digest",
                  clock_key: clockKey, head_history_digest: head.history_digest,
                  head_revision_ordinal: head.revision_ordinal });
            }
          } else if (!head) {
            refuse("clock_prior_history_unknown",
              "this clock has no history, so there is no prior digest to match; create it with an explicit null prior",
              { invariant: "j1_clock_exact_prior_history_digest",
                supplied: expected_prior_history_digest });
          } else if (head.history_digest !== expected_prior_history_digest) {
            refuse("clock_stale_prior_history_digest",
              "the prior history digest is not the current head; another writer appended first and this revision was computed against a history that is no longer the head",
              { invariant: "j1_clock_exact_prior_history_digest",
                head: head.history_digest, supplied: expected_prior_history_digest,
                head_revision_ordinal: head.revision_ordinal });
          }
          if (head) {
            // The head is REBUILT from its own rows before it is diffed against,
            // so a tampered head cannot silently authorize an illegal append.
            assertJourneyOneClockAppendOnly(rebuild(clockKey, head), journeyOneClockHistoryFromRows(rows));
          }
          // The binding is WRITTEN only once everything above has passed, and
          // it is idempotent for the exact pair, so an append to an already
          // bound clock writes nothing new. The journal refuses the same two
          // shapes again from its own rows, because a direct writer never runs
          // the clauses above.
          await journal.bindScope({
            clock_key: clockKey, clock_scope_key: scope.clock_scope_key,
            clock_scope_ref: scope.clock_scope_ref, tenant, scope: copy(scope.scope),
          });
          return {
            clock_key: clockKey, tenant, clock_ref: clock?.clock_ref ?? clock_ref,
            clock_scope_key: scope.clock_scope_key, clock_scope_ref: scope.clock_scope_ref,
            state_schema_version: rows.state_schema_version,
            history_digest: rows.history_digest,
            expected_prior_history_digest,
            scalars: rows.scalars, pause_intervals: rows.pause_intervals, events: rows.events,
            provenance, written_by_actor_id: writer.actor_id,
          };
        },
      });
    },

    /**
     * Read one clock's history deterministically.
     *
     * The head is REBUILT from the stored rows and re-hashed, and its identity is
     * re-derived from its own origin. A tampered readback REFUSES rather than
     * falling back to an earlier healthy revision or reporting a corrupted clock
     * as no clock: both would turn corruption into ordinary source.
     */
    async read(clockKey) {
      assertDigestRef(clockKey, "clock_key");
      const clock = await journal.readClock(clockKey);
      if (!clock) {
        return deepFreeze({ schema_version: JOURNEY_ONE_CLOCK_READBACK_SCHEMA,
          clock_key: clockKey, tenant, exists: false,
          record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE],
          effects: V5_NO_EFFECTS });
      }
      if (clock.tenant !== tenant) {
        refuse("cross_tenant_clock_history", "that clock belongs to another tenant",
          { invariant: "j1_clock_tenant_bound", stored: clock.tenant, reading_as: tenant });
      }
      const revisions = await journal.readRevisions(clockKey);
      if (!Array.isArray(revisions) || revisions.length === 0) {
        refuse("clock_history_empty",
          "a clock exists with no revisions; the record layer cannot produce the history it claims to hold",
          { invariant: "j1_clock_content_rebuilds_to_its_digest", clock_key: clockKey });
      }
      // DETERMINISTIC ORDER, asserted rather than assumed: the ordinals must be
      // contiguous from zero and each revision's prior must be the one before
      // it. A gap is a lost revision, which is the shape a partial write leaves.
      const ordered = [...revisions].sort((a, b) => a.revision_ordinal - b.revision_ordinal);
      let previousDigest = null;
      ordered.forEach((revision, index) => {
        if (revision.revision_ordinal !== index) {
          refuse("clock_revision_ordinal_gap",
            `revision ordinals are not contiguous from zero at position ${index}`,
            { invariant: "j1_clock_events_are_append_only", index,
              actual: revision.revision_ordinal });
        }
        if ((revision.expected_prior_history_digest ?? null) !== previousDigest) {
          refuse("clock_revision_chain_broken",
            `revision ${index} does not name the revision before it as its prior`,
            { invariant: "j1_clock_exact_prior_history_digest", index,
              expected_prior: previousDigest,
              stored_prior: revision.expected_prior_history_digest ?? null });
        }
        previousDigest = revision.history_digest;
      });
      const head = ordered[ordered.length - 1];
      const history = rebuild(clockKey, head);
      // WHICH AUTHORITATIVE SCOPE HOLDS THIS CLOCK, reported rather than
      // assumed. `false` is a real answer and is not smoothed over: a clock
      // with no scope binding is exactly the shape a second origin could have
      // created beside another clock, and a reader is entitled to see that
      // rather than a field that is quietly absent.
      const bound = (await journal.readScopeBindings({ clockKey }))?.by_clock ?? null;
      return deepFreeze({
        schema_version: JOURNEY_ONE_CLOCK_READBACK_SCHEMA,
        clock_key: clockKey, tenant, exists: true,
        clock_ref: clock.clock_ref ?? null,
        clock_scope_bound: bound !== null,
        clock_scope_key: bound?.clock_scope_key ?? null,
        clock_scope_ref: bound?.clock_scope_ref ?? null,
        state_schema_version: head.state_schema_version,
        revision_count: ordered.length,
        head_revision_ordinal: head.revision_ordinal,
        history_digest: head.history_digest,
        // The instant the RECORD LAYER wrote it, which is server time and is not
        // the kernel's evaluated_at. Both are reported; neither stands in for
        // the other.
        recorded_at: head.recorded_at,
        evaluated_at: history.evaluated_at,
        history: copy(history),
        provenance: copy(head.provenance ?? null),
        revisions: ordered.map(r => ({ revision_ordinal: r.revision_ordinal,
          history_digest: r.history_digest,
          expected_prior_history_digest: r.expected_prior_history_digest ?? null,
          recorded_at: r.recorded_at })),
        // Said out loud on every read. A consumer that saw only `status` would
        // otherwise be entitled to assume this record layer had checked
        // something. It has not, and it does not.
        record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE],
        deadline_accepted_by_record_layer: false,
        effects: V5_NO_EFFECTS,
      });
    },
  });
}

// ---------------------------------------------------------------------------
// THE TRUSTED INTEGRATION CONTRACT, and the missing public authority.
// ---------------------------------------------------------------------------

/**
 * THE KERNEL'S OWN ANSWER ABOUT WHICH ACCEPTED SCOPE A COMPUTATION WAS JUDGED
 * UNDER, read off the result and compared against the scope the store was
 * constructed for. Returns the scope key derived from the kernel's answer.
 *
 * WHY THIS IS NOT A SECOND AUTHORITY. It derives nothing new: it hands the
 * kernel's `verified_binding` to the SAME journeyOneClockScopeBinding every
 * other caller uses, so there is one derivation, one domain tag and one
 * preimage. The label comes from the store's own binding because a label is not
 * identity and never enters the key — feeding it in keeps the shape complete
 * without letting it affect the comparison, which is decided entirely by the six
 * facts the kernel enforced.
 *
 * MISSING OR MALFORMED REFUSES, WITH NO BYPASS. A kernel that returned no
 * binding, a binding of another schema, or a binding whose fields are not the
 * declared closed set is not a computation this seat can file: the alternative
 * is writing a revision under a scope nothing checked, which is the state this
 * requirement existed to end. Every field is read exactly ONCE into a local
 * snapshot before it is validated, so an exotic accessor cannot answer the
 * validation and the derivation differently.
 *
 * WHAT IT STILL DOES NOT PROVE: that the projection the kernel read was
 * authentic. The verifier remains trusted server code; this compares two
 * trusted-seat statements about the same computation and catches the case where
 * they disagree.
 */
function assertVerifiedBindingMatchesScope(result, installed) {
  const raw = result?.verified_binding;
  if (raw === undefined || raw === null) {
    refuse("clock_verified_binding_unavailable",
      "this kernel returned no verified_binding beside its state, so the scope this revision would be written under could not be checked against the computation being stored; recording is refused rather than filed under an unchecked scope",
      { invariant: "j1_clock_scope_binds_one_clock",
        expected_schema_version: JOURNEY_ONE_CLOCK_VERIFIED_BINDING });
  }
  if (!isPlainObject(raw)) {
    refuse("clock_verified_binding_malformed", "verified_binding must be an object",
      { invariant: "j1_clock_scope_binds_one_clock", path: "verified_binding" });
  }
  const keys = Object.keys(raw);
  if (keys.length !== JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS.length ||
      JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS.some(field => !Object.hasOwn(raw, field))) {
    refuse("clock_verified_binding_malformed",
      "verified_binding must carry exactly the kernel's declared fields",
      { invariant: "j1_clock_scope_binds_one_clock",
        expected: [...JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS], actual: [...keys].sort() });
  }
  // Read once, then validate the snapshot rather than the source.
  const seen = Object.fromEntries(
    JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS.map(field => [field, raw[field]]));
  if (seen.schema_version !== JOURNEY_ONE_CLOCK_VERIFIED_BINDING) {
    refuse("clock_verified_binding_malformed",
      `verified_binding.schema_version must be ${JOURNEY_ONE_CLOCK_VERIFIED_BINDING}`,
      { invariant: "j1_clock_scope_binds_one_clock", actual: seen.schema_version });
  }
  // `authenticated_projection_digest` is checked for SHAPE here and used for
  // nothing on this path: it names WHICH snapshot the kernel judged, and this
  // rail holds no projection to compare it against. A seat that does — the
  // composition loop in journey-one-clock-runtime.v5.js — is where it is read.
  // It is validated all the same, because a binding this rail cannot read whole
  // is one it should not be deriving a scope from either.
  for (const field of ["subject_digest", "candidate_digest", "policy_digest",
    "authenticated_projection_digest"]) {
    if (typeof seen[field] !== "string" || !SHA256_REF.test(seen[field])) {
      refuse("clock_verified_binding_malformed",
        `verified_binding.${field} must be a sha256: reference`,
        { invariant: "j1_clock_scope_binds_one_clock", path: `verified_binding.${field}` });
    }
  }
  // Derived through the ONE scope derivation, which is also what checks the two
  // gate ids against JOURNEY_ONE_DEADLINE_CONTRACT and the tenant against this
  // rail's, and refuses each by its own name.
  const derived = journeyOneClockScopeBinding({
    benchmark_candidate_digest: seen.candidate_digest,
    benchmark_policy_digest: seen.policy_digest,
    benchmark_subject_digest: seen.subject_digest,
    clock_origin_gate_id: seen.clock_origin_gate_id,
    clock_terminus_gate_id: seen.clock_terminus_gate_id,
    scope_ref: installed.clock_scope_ref,
    tenant: seen.tenant,
  });
  if (derived.clock_scope_key !== installed.clock_scope_key) {
    refuse("clock_scope_not_the_verified_binding",
      "this store writes for one authoritative clock scope, and the kernel verified this computation under a different one. A revision is never filed under a scope the computation was not judged against",
      { invariant: "j1_clock_scope_binds_one_clock",
        store_clock_scope_key: installed.clock_scope_key,
        verified_clock_scope_key: derived.clock_scope_key,
        differing_fields: JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS.filter(field =>
          installed.scope_identity?.[field] !== derived.scope_identity[field]),
        store_scope_identity: copy(installed.scope_identity ?? null),
        verified_scope_identity: copy(derived.scope_identity) });
  }
  return derived.clock_scope_key;
}

/**
 * Evaluate one envelope with an ALREADY-CONSTRUCTED kernel and persist the
 * result as the next revision of its own clock.
 *
 * `clock` must be the object createJourneyOneClock({ verifySnapshot }) returns.
 * That constructor refuses to exist without an authenticated verifier, so the
 * only way to reach this function is to be trusted server code that installed
 * one. No verifier is created here, no envelope is trusted here, and nothing in
 * an ordinary request can construct this recorder.
 *
 * WHAT IT DOES NOT DO: it does not decide whether the clock may start, it does
 * not admit a receipt, and it does not turn a kernel result into an acceptance.
 * It records a computation with accurately scoped provenance and independently
 * rebuilt content, which is exactly what the header says a trusted writer may
 * do and no more.
 *
 * THE SCOPE IS CHECKED HERE, AND THIS IS THE ONLY SEAT THAT CAN. The kernel
 * returns `verified_binding` beside its state; the store carries the
 * authoritative scope it was constructed for. This function is the one place
 * both are in hand, so it compares them BEFORE the store is called at all — no
 * journal read, no compare-and-swap, no row. A store with no scope cannot be
 * recorded through at all, and that refuses at CONSTRUCTION rather than on the
 * first write.
 *
 * `assert_before_write` IS AN OPTIONAL TRUSTED PRE-WRITE ASSERTION THAT CAN ONLY
 * REFUSE. It exists because there are facts about a computation that neither the
 * kernel nor this rail can see: journey-one-clock-runtime.v5.js holds the
 * INVENTORY the projection was composed from, and "the origin this kernel
 * selected is one this record layer actually admitted" is a pairwise fact only
 * that seat has both halves of. Rather than a second evaluate-check-record
 * sequence living there — two homes for one order, which is the drift rule
 * a8c55a47 forbids — the sequence stays here and the extra refusal is injected.
 *
 * ITS LIMITS ARE STRUCTURAL, NOT PROMISED. It is a CONSTRUCTION-TIME callback
 * from trusted server code, never a request field. It runs AFTER the scope check
 * and BEFORE any journal read, compare-and-swap or row. It is handed the kernel's
 * own deep-frozen result and nothing else, so it cannot edit the computation it
 * is inspecting. ITS RETURN VALUE IS IGNORED — there is no value it can return
 * that admits anything, and no path by which it can pass a computation this
 * recorder would otherwise refuse. A recorder built without one behaves exactly
 * as it did before; the only thing it can add is a throw.
 */
export function createJourneyOneClockRecorder({
  clock, store, verifier_ref, assert_before_write = null } = {}) {
  if (!clock || typeof clock.evaluate !== "function") {
    refuse("authenticated_kernel_required",
      "recording requires a kernel built by createJourneyOneClock({ verifySnapshot }); this rail never constructs one and never accepts a verifier as data",
      { path: "clock" });
  }
  if (!store || typeof store.record !== "function" || typeof store.read !== "function") {
    refuse("invalid_shape", "a recorder needs a clock store", { path: "store" });
  }
  if (assert_before_write !== null && typeof assert_before_write !== "function") {
    refuse("invalid_shape",
      "assert_before_write is an optional trusted pre-write assertion supplied at construction; it must be a function, and it may only refuse",
      { path: "assert_before_write" });
  }
  const installed = store.clock_scope ?? null;
  if (!isPlainObject(installed) || typeof installed.clock_scope_key !== "string" ||
      typeof installed.clock_scope_ref !== "string") {
    refuse("clock_scope_binding_required",
      "a recorder writes, and every write is filed under the authoritative clock scope its store was constructed for; a store without one cannot be recorded through, because the kernel's verified binding would have nothing to be checked against",
      { invariant: "j1_clock_scope_binds_one_clock", path: "store.clock_scope" });
  }
  /**
   * EVALUATE AND CHECK, WITHOUT WRITING — and it is the same code the write path
   * runs, not a copy of it. The kernel runs on its own terms, the scope is
   * derived from its verified binding and compared, and any pre-write assertion
   * fires; nothing is stored and no journal method is called.
   *
   * It exists because a seat that has established a request ALREADY LANDED still
   * has to check that the re-computation is the same computation, and it must do
   * that without appending. Splitting the order into a second implementation
   * there is what rule a8c55a47 forbids, so the order stays here and the write is
   * what is optional.
   */
  const evaluateAndBind = (envelope) => {
    // The kernel runs FIRST and on its own terms. If it refuses, nothing is
    // stored and its own error code travels unchanged: a caller who learns
    // `origin_reset_or_rebase` from the kernel should see that code, not a
    // second vocabulary for the same fact.
    const result = clock.evaluate(envelope);
    // AND THE SCOPE IT WAS JUDGED UNDER IS THE SCOPE IT IS FILED UNDER. This
    // runs before store.record(), so a mismatch, a missing binding or a
    // malformed one refuses with no journal call and no row written.
    const clock_scope_key = assertVerifiedBindingMatchesScope(result, installed);
    // AND ANY TRUSTED PRE-WRITE ASSERTION, in the same window: after the scope
    // check, before the store is called. Its refusal travels unchanged, for the
    // same reason the kernel's does — a caller who would learn
    // `clock_computation_origin_not_in_composed_inventory` from the seat that
    // holds the inventory should meet that code and not a second vocabulary.
    // Nothing is read from its return value.
    if (assert_before_write !== null) assert_before_write(result);
    return Object.freeze({ result, clock_scope_key });
  };

  return Object.freeze({
    evaluateAndBind,
    async evaluateAndRecord({ envelope, expected_prior_history_digest, idempotency_key, clock_ref = null } = {}) {
      const { result, clock_scope_key } = evaluateAndBind(envelope);
      const recorded = await store.record({
        state: copy(result.state), expected_prior_history_digest, idempotency_key,
        claimed_history_digest: result.state.history_digest, clock_ref, verifier_ref,
      });
      return deepFreeze({
        ok: true,
        schema_version: JOURNEY_ONE_CLOCK_STORE_SCHEMA,
        clock_key: recorded.clock_key,
        revision_ordinal: recorded.revision_ordinal,
        history_digest: recorded.history_digest,
        // The scope this revision was filed under, and the fact that it is the
        // one the kernel verified this computation against — reported rather
        // than assumed, because the check is what makes it true.
        clock_scope_key,
        clock_scope_matches_verified_binding: true,
        expected_prior_history_digest: recorded.expected_prior_history_digest ?? null,
        recorded_at: recorded.recorded_at,
        replayed: recorded.replayed === true,
        // The kernel's own verdict, passed through unchanged and unre-decided.
        // durable_history_write_required is now FALSE for this revision and only
        // for this revision: the write it names has happened.
        kernel_verdict: {
          status: result.state.status,
          deadline_success: result.deadline_success,
          replan_required: result.replan_required,
          completion_currently_usable: result.completion_currently_usable,
          completion_observed_within_deadline: result.completion_observed_within_deadline,
          missing_evidence_miss_recorded: result.missing_evidence_miss_recorded,
          benchmark_amended: result.benchmark_amended,
          deadline_resolution: result.deadline_resolution,
          unresolved_reason: result.unresolved_reason,
        },
        durable_history_write_required: false,
        deadline_accepted_by_record_layer: false,
        record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE],
        effects: JOURNEY_ONE_CLOCK_RECORD_EFFECTS,
      });
    },
  });
}

/**
 * The honest, zero-effect statement of what a PUBLIC evaluate-and-record still
 * needs. It describes MISSING RECORDS AND MISSING PRODUCERS; it grants nothing,
 * reads nothing and configures nothing.
 */
export const JOURNEY_ONE_CLOCK_INPUT_AUTHORITY_REQUIREMENT = deepFreeze({
  binding_ref: "binding:journey-one-clock-authenticated-projection",
  resolved: false,
  scope: "the authority available in this repository, not a claim about what exists outside it",
  why_unresolved: [
    "createJourneyOneClock requires an authenticated verifySnapshot callback and a current trusted projection. Nothing in this repository builds either from the record layer, so there is no authenticated reader a public verb could call.",
    "No live producer of an admitted foundation-assurance-minimum receipt exists here, so there is no origin to read. NO CLOCK HAS BEEN STARTED; that is an absence of evidence in this repository, not a proof of absence about the record as a whole.",
    "No live producer of a journey-one-kernel-production terminus receipt exists here either, so no completion can be presented.",
    "The trusted projection's own facts -- completion_expectation and the two per-receipt TTL maxima -- must be derived from the accepted kernel scope and the accepted policy. Nothing here derives them, and reading them back off the receipt they judge would make the binding the tautology it exists to replace.",
  ],
  required_to_resolve: [
    "Land an authenticated projection reader that builds doctorcre-v5-journey-one-clock-projection.v2 from the record layer, with the verifySnapshot binding the kernel demands, and implement readAuthenticatedClockProjectionInputs() against it.",
    "Land the admitted-minimum ledger the projection's minimum_history is read from, stamping admitted_at from the same trusted clock and asserting it at write time. A single instant of skew between admitted_at and a receipt's observed_at is fatal to the whole projection and an append-only inventory cannot shed it.",
    "Derive completion_expectation and both TTL maxima from the accepted scope and policy, never from the receipt being judged.",
    "Have that same reader return the authoritative clock scope it read the projection under, and hand it to createJourneyOneClockStore({ clock_scope }). It is a construction-time trusted binding on purpose; a scope taken from a request would let one caller present a new origin AND a new scope and create a second clock for one program.",
  ],
  /**
   * THE ONE THING THIS RECORD LAYER ASKED ANOTHER FILE FOR, AND WHAT LANDED.
   * The scope is now derived from the computation being stored on the trusted
   * recorder path. Nothing else about the missing public authority changed, and
   * the entries above are unaltered.
   */
  read_only_kernel_projection_extension_required: {
    resolved: true,
    exact_requirement: "A read-only projection-binding projection from journey-one-clock.v5.js: the trusted binding {subject_digest, candidate_digest, policy_digest} the kernel already read and enforced, returned beside the state so a store can derive the scope key from the same facts the kernel judged. Read-only, decides nothing, and adds no field to the hashed state -- adding one would change every history_digest and rebase every stored clock.",
    what_landed: "createJourneyOneClock().evaluate() returns verified_binding beside the state: {schema_version, tenant, subject_digest, candidate_digest, policy_digest, clock_origin_gate_id, clock_terminus_gate_id, authenticated_projection_digest}, deep-frozen with the rest of the result. The last of those is v2 and is not read by this rail, which holds no projection to compare it against; it is checked for shape and used by the composition loop. Every value was already enforced by that evaluation -- the three digests against the accepted benchmark and against every receipt read, the gate ids by the exact deadline-contract comparison. createJourneyOneClockRecorder derives the authoritative scope key from it through the same journeyOneClockScopeBinding every other caller uses and refuses when it is not the scope the store was constructed for, BEFORE any journal read or write. A missing or malformed binding refuses too; there is no bypass and no option.",
    hashed_state_unchanged: "doctorcre-v5-journey-one-clock.v2 gained no field. The state, its schema version and every history_digest are exactly what they were, which is why no stored clock is rebased and no migration is proposed.",
    still_not_proved: [
      "that the projection the kernel read was authentic: verifySnapshot remains trusted server code, and this compares two trusted-seat statements about one computation rather than authenticating either",
      "anything at all about a direct store.record() call or a direct SQL writer: those are handed a STATE, which carries none of the three digests, so on those paths the store's scope is still compared and never verified",
      "that the scope key this module derives equals the one ops.j1_clock_scope_digest derives: both hash the canonical [domain tag, the same six identity fields], and the SQL has never been executed",
    ],
    explicitly_not_done_instead: [
      "adding the binding to the hashed state, which would change every history_digest and rebase every stored clock",
      "deriving a scope from the stored history's origin fields, which would make the scope move with the origin and defend nothing",
      "accepting a scope, or a verified binding, as a request field",
      "inventing a fixed one-clock-per-tenant rule, which is a policy nobody accepted",
    ],
  },
  /**
   * THE JOIN BETWEEN THIS RAIL AND THE ADMITTED-MINIMUM INVENTORY, which used to
   * exist only inside a test. It closes NONE of `why_unresolved` above: the
   * producers and the authenticated reader are still missing, and this seat
   * cannot run at all until an inventory exists to compose from.
   */
  record_layer_composition_loop: {
    resolved: true,
    exact_requirement: "A production seat where the composed projection, the kernel and this store meet: read the authoritative scope's clock and its head history from this rail, compose the projection from the admitted-minimum inventory against that exact head, evaluate, and compare-and-swap on that same head. Both halves were published as trusted_integration_contracts and nothing joined them, so every caller had to re-derive the order — and the two facts that make the loop coherent, the history evaluated and the prior it appends onto, were two independent caller arguments on two rails.",
    what_landed: "journey-one-clock-runtime.v5.js. createJourneyOneClockRuntime({ composer, clock, clock_store, present_projection, verifier_ref }).advance() reads store.readClockKeyForScope() and store.read() ONCE and derives both the composed `history` and the `expected_prior_history_digest` from that one read — neither is an argument, and supplying either is refused by name. It then asserts the computation IS the composed projection: the kernel's v2 authenticated_projection_digest must equal the digest of the projection that seat composed, and the selected origin must additionally be a receipt the composed inventory admitted. Seven named field checks run first as diagnostics only -- the accepted scope is shared by every projection for one program, so they cannot establish identity on their own. That assertion runs through this recorder's assert_before_write seam, so the evaluate-check-record order still has exactly one home.",
    still_not_resolved: "Everything in why_unresolved. The loop cannot run in this repository: composing refuses with minimum_inventory_unavailable because no receipt can be admitted, and the runtime installs no verifier, admits no receipt and starts no clock.",
    explicitly_not_done_instead: [
      "creating or accepting a verifySnapshot callback anywhere in the runtime seat: it takes an ALREADY-CONSTRUCTED kernel, exactly as this recorder does",
      "a second evaluate-check-record sequence in the runtime, which would be two homes for one order",
      "a public verb, a tool registration, or any relaxation of the fail-closed readers on either rail",
    ],
  },
  /**
   * A SEPARATE, ALREADY-EXPLOITABLE DEFECT IN THE SCOPE KEY, fixed beside the
   * extension above because they are one seam: a scope identity that a caller
   * could move by renaming is not an identity at all.
   */
  scope_label_excluded_from_identity: {
    resolved: true,
    defect: "journeyOneClockScopeBinding hashed all seven declared fields, including scope_ref, while its own contract said scope_ref is a human label carrying no authority. One accepted scope -- identical tenant, benchmark subject, candidate and policy digests and gate ids -- produced DIFFERENT keys under safe:clock-scope:a and safe:clock-scope:b, so a relabelled store could present a fresh origin and create a second clock for one program: exactly the evasion the scope exists to refuse.",
    fix: "The key's preimage is JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS -- the six identity fields, scope_ref excluded -- in both this module and ops/journey-one-clock-store.candidate.sql, under a domain tag versioned to doctorcre:j1-clock-scope:v2 because the published key changed. The label is still supplied, still required, still stored and still read back; it is sealed at binding, so a second label for a bound scope is refused by name (j1_clock_scope_label_is_not_identity) rather than kept or overwritten.",
    nothing_was_rewritten: "The candidate SQL has never been applied and no clock has been started, so no stored key changed, no data was migrated and no backfill is proposed. If a key had ever been published durably, moving it would be an explicit migration owned by whoever owns the store.",
  },
  explicitly_refused: [
    "a caller envelope carrying { verified: true } or any self-hashed attestation",
    "a fabricated consumer-gate or rollout-component receipt, or a minted session_ref",
    "a caller-chosen as_of used as this record layer's clock",
    "a public start verb that steps around the missing proof seam",
    "a deadline policy implemented a second time in this file",
    "a clock scope taken from a request, derived from a stored history's own origin, or invented here as a fixed one-clock-per-tenant rule",
    "a history validator implemented a second time in this file: readJourneyOneClockHistory is the kernel's own read and this rail calls it",
  ],
  // Said plainly so the seam is not oversold before it is built.
  remaining_trust_boundary:
    "Even with the reader landed, this record layer stores a computation performed elsewhere. It can recompute the digest, enforce the append invariants and derive the clock identity from the origin; it cannot repeat the verification, and nothing here should be read as saying it can.",
});

/**
 * THE PRIVATE FAIL-CLOSED AUTHENTICATED-INPUT READER.
 *
 * Deliberately not exported, deliberately parameterless, deliberately without a
 * configuration path: an exported stub is a callable claim about the missing
 * authority, and a stub that takes an argument is one edit away from being a
 * configuration surface. The only way to reach it is to attempt the public
 * evaluate-and-record verb, which is where the gap actually matters.
 *
 * It always throws. When the reader exists, this body builds the authenticated
 * envelope and returns { envelope, verifier_ref, clock_scope,
 * expected_prior_history_digest } — and the trusted recorder above is what it
 * hands them to, unchanged. `clock_scope` belongs in that list and not in a
 * request: the reader that authenticates the projection is the only thing that
 * knows the accepted scope it was read under.
 */
function readAuthenticatedClockProjectionInputs() {
  refuse("clock_input_authority_unbound",
    "starting or advancing a Journey 1 clock requires an authenticated trusted projection, and this repository holds no reader that can build one: there is no verified projection reader, no live admitted-minimum producer and no live terminus producer. A caller-supplied envelope is not a substitute -- the kernel's verification binding is trusted server code by construction. This verb therefore fails closed before it issues any query. The storage, compare-and-swap and readback underneath are implemented and exercised; what is missing is the authority to admit an input proof.",
    JOURNEY_ONE_CLOCK_INPUT_AUTHORITY_REQUIREMENT);
}

/** A reader is entitled to know the gate is shut and why. Zero effect. */
export function journeyOneClockStoreIntegrationRequirements() {
  return deepFreeze({
    schema_version: JOURNEY_ONE_CLOCK_INTEGRATION_SCHEMA,
    state_schema_version: JOURNEY_ONE_CLOCK_SCHEMA,
    projection_schema_version: JOURNEY_ONE_CLOCK_PROJECTION,
    legacy_state_schemas_refused: [...JOURNEY_ONE_CLOCK_LEGACY_SCHEMAS],
    storage_implemented: true,
    storage_notes: [
      "Identity derivation, row decomposition, whole-content reconstruction, digest recomputation, exact-prior compare-and-swap, idempotency binding, the append-only diff and the deterministic readback are implemented and exercised in-process.",
      "One history's whole shape and semantics are read by the kernel's own readJourneyOneClockHistory, called on every write and on every readback. This rail owns no second history validator; it owns the pairwise diff against the head on disk, which the kernel structurally cannot see.",
      "A clock's derived key defends against a caller RENAMING a clock and against nothing else: a caller presenting a different origin derives a different key, whose creation meets no compare-and-swap. The authoritative clock scope is what refuses that. It is a construction-time trusted binding, checked against the kernel's own verified_binding on the recorder path and compared without being verified on a direct record() call.",
      "The scope key is derived from six identity fields and never from scope_ref: a scope's human label is provenance, so one accepted scope under two names is one scope and cannot hold two clocks. The label is sealed at binding and a changed one is refused by name.",
      "The durable journal binds ops/journey-one-clock-store.candidate.sql, which is candidate source: it has not been applied as a numbered migration and has never been executed.",
    ],
    clock_scope_binding_required_for_writes: true,
    input_authority: JOURNEY_ONE_CLOCK_INPUT_AUTHORITY_REQUIREMENT,
    public_evaluate_and_record_available: false,
    public_evaluate_and_record_blocked_by: [
      JOURNEY_ONE_CLOCK_INPUT_AUTHORITY_REQUIREMENT.binding_ref,
    ],
    trusted_integration_contract: {
      entry_point: "createJourneyOneClockRecorder({ clock, store, verifier_ref })",
      requires: "a kernel built by createJourneyOneClock({ verifySnapshot }), which only trusted server code can construct, and a store constructed with the authoritative clock scope it writes for",
      checks_before_any_write: "the kernel's verified_binding for this computation derives the authoritative scope key, and a scope that is not the store's refuses -- as does a missing or malformed binding -- before any journal read, compare-and-swap or row",
      optional_pre_write_assertion: "assert_before_write is a construction-time trusted callback that runs after the scope check and before any journal call. It is handed the kernel's deep-frozen result, its return value is ignored, and it can only add a refusal; a recorder built without one is unchanged",
      records: "a kernel computation with accurately scoped provenance and independently rebuilt content",
      does_not_record: "an acceptance, a receipt, a verification, or any claim of deadline success",
      composition_loop: "createJourneyOneClockRuntime({ composer, clock, clock_store, present_projection, verifier_ref }) in journey-one-clock-runtime.v5.js is the seat that reads this store's head once, composes the projection from the admitted-minimum inventory against it, and appends onto that same head through this recorder. It cannot run here: no receipt can be admitted, so there is no inventory to compose from",
    },
    append_invariants: JOURNEY_ONE_CLOCK_APPEND_INVARIANTS.map(i => ({ ...i })),
    record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE],
    clock_started: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * The verbs.
 *
 * DELIBERATELY NOT ADDED TO ANY TOOL INDEX BY THIS SLICE. Registering a verb is
 * a separate reviewed act, and the write verb below cannot succeed in any case.
 */
export function journeyOneClockStoreTools({ withEnvelope, ToolError }) {
  const toolRefuse = (error, detail) => { throw new ToolError({ error, ...detail }); };
  const asToolError = (error) => {
    // The A00 rail's refusal type travels too: assertNoSelfAssertedAuthority is
    // imported from it, so a smuggled authority claim raises
    // BenchmarkAcceptanceStoreError and a caller should meet its code rather
    // than a raw exception in a different shape from every other refusal here.
    if (error instanceof JourneyOneClockStoreError ||
        error?.name === "JourneyOneClockError" ||
        error?.name === "BenchmarkAcceptanceStoreError") {
      toolRefuse(error.code, {
        message: error.message,
        ...(error.detail !== undefined ? { detail: error.detail } : {}),
      });
    }
    throw error;
  };
  const check = (fn) => { try { return fn(); } catch (error) { return asToolError(error); } };

  return {
    "read-journey-one-clock-history": {
      write: false,
      description: "Read one Journey 1 clock's stored history: the head revision rebuilt from its typed rows, the digest recomputed from that rebuilt content, the revision chain, the server instant each revision was recorded at, and the provenance of the computation that produced it. A clock is addressed by the key DERIVED FROM ITS OWN KERNEL ORIGIN, never by a caller-chosen name. A tampered readback refuses rather than falling back to an earlier revision. It reports in its own fields what this record layer cannot prove -- that the origin receipt was genuine, that the pauses were partner-approved, that the terminus was admitted, or anything at all about deadline success -- and produces no effect.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { clock_key: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" } },
        required: ["clock_key"],
      },
      handler: async (c, actor, args) => {
        const store = check(() => createJourneyOneClockStore({
          journal: createPostgresJourneyOneClockJournal({ query: (sql, params) => c.query(sql, params) }),
          actor,
        }));
        try {
          return { ok: true, ...(await store.read(args.clock_key)),
            integration: journeyOneClockStoreIntegrationRequirements() };
        } catch (error) { return asToolError(error); }
      },
    },

    "evaluate-and-record-journey-one-clock": {
      write: true,
      description: "REFUSES TODAY, BY DESIGN. Evaluating and recording a Journey 1 clock revision requires an authenticated trusted projection, and this repository holds no reader that can build one: there is no verified projection reader, no live admitted-minimum producer and no live terminus producer. A caller-supplied envelope is not a substitute, because the kernel's verification binding is trusted server code by construction. This verb calls the private fail-closed reader BEFORE it issues any query, so no clock can be half-started, logged as pending or mistaken for one that nearly worked. The storage, compare-and-swap, idempotency and readback underneath are implemented and exercised; the trusted-integration entry point is createJourneyOneClockRecorder, which only server code holding a real verifier can construct.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          clock_ref: { type: "string" },
          // Present so the shape a live caller will use is stated in code rather
          // than only in prose. Nothing reads it: the refusal happens first.
          expected_prior_history_digest: { type: ["string", "null"], pattern: "^sha256:[0-9a-f]{64}$" },
        },
        required: ["idempotency_key", "expected_prior_history_digest"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "evaluate-and-record-journey-one-clock", args, async () => {
        // ORDER IS DELIBERATE AND LOAD-BEARING. The writer is derived from the
        // live actor first, so the rail does not depend on a flag being read
        // correctly somewhere else; then the input authority refuses, ahead of
        // every query, which is what makes "this verb cannot start a clock
        // today" observable rather than merely asserted.
        check(() => deriveJourneyOneClockWriter(actor));
        const inputs = check(() => readAuthenticatedClockProjectionInputs());

        // UNREACHABLE UNTIL THE READER EXISTS. Written out rather than stubbed
        // so that landing it is a change to one function and not a fresh set of
        // decisions made by whoever happens to land it.
        const store = createJourneyOneClockStore({
          journal: createPostgresJourneyOneClockJournal({ query: (sql, params) => c.query(sql, params) }),
          actor,
          // The authoritative scope comes from the same trusted reader that
          // produces the envelope, and from nowhere else. It is not read off
          // `args`: a caller who could name the scope could name a new one
          // beside a new origin and create a second clock for one program.
          clock_scope: inputs.clock_scope,
        });
        const recorder = createJourneyOneClockRecorder({
          clock: inputs.clock, store, verifier_ref: inputs.verifier_ref });
        return { ...(await recorder.evaluateAndRecord({
          envelope: inputs.envelope, idempotency_key: args.idempotency_key,
          expected_prior_history_digest: args.expected_prior_history_digest,
          clock_ref: args.clock_ref ?? null,
        })), effects: JOURNEY_ONE_CLOCK_RECORD_EFFECTS };
      }),
    },
  };
}
