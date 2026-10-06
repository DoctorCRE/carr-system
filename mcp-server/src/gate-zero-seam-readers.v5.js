// DoctorCRE v5 slice V5-A02, the seam half: THE THREE READERS GATE ZERO IS
// OWED — built, tested, and ON.
//
// WHAT THIS IS FOR. Gate Zero's own path refuses with
// `predecessor_outcome_reader_unavailable`, and it refuses BEFORE it looks at
// any Work Request, because nothing on that path can read the evidence it would
// join. Four seams are owed. Cards 11, 12 and 13 of JOE-GATE-ZERO-CARDS.md
// named a store for three of them, and JOE HAS RULED all three. The decision
// ids are live on the three `decision_id:` lines of
// gate-zero-seam-rulings.v5.js:
//
//   card 11  16c7cdfb-b675-4b6a-bbff-4bbdab46baf8 — which store an accepted
//            predecessor outcome comes from (record layer Work Request outcome
//            feedback)
//   card 12  f7c486d6-5bee-4c4c-a76f-c0f162f66db8 — which scheduler surface
//            supplies a canary and the observation after it (ops.service and
//            ops.run)
//   card 13  87e9e11e-64b2-49b3-a6aa-4901c24eaa91 — which surface reports a
//            gate's own conclusion (the GitHub checks API)
//
// So all three readers read, and not one line of this file was written the day
// those rulings landed: the whole switch is those three lines in the ruling
// table. It turns both ways — put `null` back on a card's `decision_id:` line
// and that card's reader goes back to handing over the gate's own refusal,
// without opening its store.
//
// THE RULING GATE, and it is the first thing every reader does.
//
//   1. Ask the ruling table whether THIS CARD is ruled, by card token.
//   2. If it is not — which for these three cards means a `null` or a
//      malformed id put back on its ruling line — RETURN THE GATE'S OWN
//      REFUSAL, by calling
//      `readGateZeroPredecessorJoin()` or `readGateGraphAssurance()` and
//      returning what they return, unmodified. Not a copy, not a lookalike: the
//      gate's own function's own answer, so the two cannot drift.
//   3. If the ruling names a store this card's reader does not serve, that is
//      also not a ruling for this reader, and step 2 stands.
//   4. Only then look at the query. Only then open the store.
//
// The order is the point. A reader that validated the query first would leak
// which queries are well-formed; a reader that opened its store first would
// have read an unruled store even if it threw the answer away. Half an answer
// from an unruled store is still an unruled read.
//
// THE PUBLIC SURFACE IS FOUR NAMES. Three readers and a schema version string.
// There is no exported classifier, no exported predicate, no exported ruling
// table, no exported findings vocabulary and no exported status object. The
// derivation — the part that turns rows into a finding — is module-private,
// below, and there is no route to it that does not go through a reader that has
// already checked a ruling and fetched the rows itself. The ruling predicate the
// GATE also asks is shared rather than copied, and it is not a fifth name here
// either: it lives in internal/gate-zero-seam-binding.v5.js, which this module
// and gate-zero-assurance.v5.js import by that path and neither re-exports.
//
// WHAT A CALLER MAY SAY, AND WHAT IT MAY NEVER SAY. A query ADDRESSES a row:
// which predecessor step, which service and canary, which commit and check.
// Addressing is not asserting. There is no argument for a decision id, a store
// handle, a connection, a reader object, a row, a receipt or an outcome — and
// there is no environment variable that opens a seam either. The only way to
// change what these functions answer is to change what is in the store, or to
// paste a ruling into a file and commit it.
//
// NO CALLER TEXT COMES BACK OUT, AND NO STORE TEXT EITHER. Every query field is
// taken through a try/catch (a Proxy trap or a throwing getter is caught, not
// propagated) and then matched against a strict pattern. A field that passes is
// USED and never echoed. What a result carries instead is `query_digest` — a
// digest over the NORMALIZED query — plus counts, closed-vocabulary enums, and
// identifiers taken from this module's own frozen tables. Free text out of a
// store does not reach an answer either: not a feedback ref, not a stored
// outcome, not a timestamp. Hand these functions a hostile object and the answer
// contains none of its bytes; point them at a store full of hostile rows and the
// answer contains none of those either.
//
// NO PRIVILEGED WORD COMES OUT, AND THERE IS NO EXEMPTION LIST. The union the
// standing rule closes — allow, commit, prompt, suppress, release, read,
// covered, drafted, proposed, queued, healthy, passing, ok, pass, satisfied,
// complete, admitted, resumed, attended, verified, present, equivalent,
// operational, active, green, joins_exactly, coverage_complete, favorable,
// anything `would_*`, anything `*_if_authoritative` — appears in NO value any
// export of this module returns, as word, as token or as raw substring, and the
// test sweeps for it with no carve-out at all.
//
// The earlier draft of this module needed a carve-out, because it reused the
// gate's own reason ids and seam names verbatim and those contain "read"
// ("read-only", "readback") and "active" ("scheduler-active-receipt"). That
// carve-out is deleted. Two things replaced it, and neither is an exemption:
//
//   * The seams are addressed by OPAQUE CARD TOKENS — "card:11", "card:12",
//     "card:13" — everywhere a value could carry one. A gate seam name never
//     enters an answer this module builds.
//   * This module's own finding vocabulary is its own. `scheduler_readback_*`
//     became `scheduler_observation_*`. The clause is the same clause; the word
//     is one this module is free to choose, and it chose one with no privileged
//     substring in it.
//
// What remains is the UNRULED answer, which is not this module's output at all:
// it is the gate's object, returned by delegation. The test does not sweep it
// for words — it pins it, whole, by digest, to what `gate-zero-assurance.v5.js`
// returns on main. That is strictly stronger than a word sweep, because
// identity admits no new string of any kind, privileged or not.
//
// THE FOURTH SEAM IS NOT BUILT, AND IS NOT COPIED HERE.
// `seam:gate-zero-read-only-outcome-producer` is cards 9 and 10 — which
// independent seat holds the oracle, and whether r7 itself carries the
// registration. Those are not questions about where to read from; they are
// questions about who signs. Its refusal is `emitGateZeroOutcome()` in
// gate-zero-assurance.v5.js, which already says all of it; an earlier draft
// carried a written-out copy of that refusal as an exported constant, and the
// copy has been deleted rather than kept in sync. One place says it.
//
// WHAT IS STILL OWED AFTER A RULING, said now so nobody reads a ruling as more
// than it is: WHICH canary the scheduler reader should be pointed at, and WHICH
// commit and check the conclusion reader should be pointed at, are the
// consumer's bindings, and the consumer is the Gate Zero producer — the seam
// that is deliberately not built. These readers will answer truthfully about
// whatever row they are pointed at. Pointing them is the producer's job.

import { canonicalJson, digest } from "./artifact-trust.js";
import { closedCallable } from "./closed-callable.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_SCHEDULER_STEP_REF,
  readGateGraphAssurance,
  readGateZeroPredecessorJoin,
} from "./gate-zero-assurance.v5.js";
import { ruledCardBinding } from "./internal/gate-zero-seam-binding.v5.js";
import {
  fetchCheckConclusionRows,
  fetchPredecessorOutcomeRows,
  fetchSchedulerLedgerRows,
  isSeamStoreUnreachable,
} from "./gate-zero-seam-stores.v5.js";

/**
 * The schema id, and it deliberately says "evidence" rather than "readers": no
 * value this module returns may contain a privileged word even as a raw
 * substring, and "readers" carries one. The sweep is the reason, and keeping the
 * reason visible here is cheaper than rediscovering it.
 */
export const GATE_ZERO_SEAM_READERS_SCHEMA_VERSION = "doctorcre-v5-a02-gate-zero-seam-evidence.v1";

// ---------------------------------------------------------------------------
// The closed vocabularies. All module-private: a consumer that wants to know
// what a reader can say reads this file.
// ---------------------------------------------------------------------------

/** The opaque tokens the three cards are addressed by. Nothing else names them. */
const CARD_11 = "card:11";
const CARD_12 = "card:12";
const CARD_13 = "card:13";

/**
 * The ONE fetcher each card's reader will call. The other half of the binding —
 * WHICH store ref that card's ruling must name — is the predicate's table, in
 * internal/gate-zero-seam-binding.v5.js, so the two are not written twice: unless
 * the ruling names that store, `ruledCardBinding` answers null and no row is
 * fetched at all. A ruling that named `github:checks` for card 11 is not a ruling
 * this reader can act on, and the gate's refusal stands — rather than the old
 * behaviour, which reported the ruled ref while querying its own hard-coded
 * store. The pairing here is checked a second time at fetch: a store states which
 * store it is, and a disagreement with the ruling refuses.
 */
const CARD_FETCH = Object.freeze({
  [CARD_11]: fetchPredecessorOutcomeRows,
  [CARD_12]: fetchSchedulerLedgerRows,
  [CARD_13]: fetchCheckConclusionRows,
});

/**
 * Every finding a derivation can reach. Closed, so a finding is checkable, and
 * worded in this module's own vocabulary so that no value it emits carries a
 * privileged substring.
 */
const FINDINGS = Object.freeze([
  "gate_conclusion_check_absent",
  "gate_conclusion_observed",
  "gate_conclusion_unrecognized",
  "predecessor_outcome_absent",
  "predecessor_outcome_acceptance_receipt_hash_mismatch",
  "predecessor_outcome_accepted_with_matching_hash",
  "predecessor_outcome_detail_absent",
  "predecessor_outcome_not_accepted",
  "scheduler_canary_and_observation_join",
  "scheduler_canary_not_bound_to_receipt",
  "scheduler_dispatch_row_absent",
  "scheduler_observation_absent",
  "scheduler_observation_canary_mismatch",
  "scheduler_observation_not_after_dispatch",
  "scheduler_service_row_absent",
].sort());

/** The three findings that are a reading rather than a refusal. */
const REPORTING_FINDINGS = Object.freeze([
  "gate_conclusion_observed",
  "predecessor_outcome_accepted_with_matching_hash",
  "scheduler_canary_and_observation_join",
]);

/** Every refusal id a reader can answer with that is not a finding. Closed. */
const REASON_IDS = Object.freeze([
  ...FINDINGS,
  "gate_conclusion_query_invalid",
  "gate_conclusion_source_unreachable",
  "predecessor_outcome_store_unreachable",
  "predecessor_query_invalid",
  "scheduler_ledger_unreachable",
  "scheduler_predecessor_not_outcome_backed",
  "scheduler_query_invalid",
  "store_ref_not_the_ruled_one",
  "unknown_predecessor_step",
].sort());

/**
 * How a clause came out. THREE VALUES AND NO BOOLEAN, on purpose: a `true` on
 * this surface is the shape the standing rule closes over, whatever it is
 * called, and "unknown" is a state a boolean cannot express — a clause with no
 * row to read it from has not failed, it has not been answered.
 */
const HELD = "held";
const FAILED = "failed";
const UNKNOWN = "unknown";

/**
 * GitHub's own conclusion vocabulary, closed HERE rather than trusted from the
 * wire. The conclusion is the one store value that reaches an answer, and it
 * reaches it only by being equal to one of these strings — so what a consumer
 * receives is a constant from this file, not text the checks API chose. An
 * answer GitHub gives that is not on this list is reported as unrecognized
 * rather than passed through.
 */
const GITHUB_CONCLUSIONS = Object.freeze([
  "action_required", "cancelled", "failure", "neutral",
  "skipped", "stale", "success", "timed_out",
]);

/**
 * Which predecessor step is carried by which Work Request. Module-private and
 * frozen: the caller names a STEP, and the Work Request it resolves to is this
 * module's, so no caller can point the predecessor reader at a Work Request of
 * its own choosing. The step ref itself never reaches an answer — the Work
 * Request ref does, and it comes from here. `step:scheduler-active-receipt`
 * maps to null on purpose: no Work Request carries it, and the scheduler reader
 * answers it instead.
 */
const PREDECESSOR_WORK_REQUEST_REFS = Object.freeze({
  [V5_A02_SCHEDULER_STEP_REF]: null,
  "step:wr40-repository-outcome": "WR-000040",
  "step:wr46-dissolution-outcome": "WR-000046",
  "step:wr54-backup-recovery-outcome": "WR-000054",
});

// `headSha`, not `commitSha`: the field name reaches a result as
// `invalid_field`, and "commitSha" contains a word the privileged-word sweep
// closes over. GitHub's own API calls the field head_sha anyway.
const HEAD_SHA = /^[0-9a-f]{40}$/;
const SERVICE_KEY = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const RUN_KEY = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/;
const OUTCOME_HASH = /^sha256:[0-9a-f]{64}$/;

/**
 * THE CHECK NAMES THIS REPOSITORY ACTUALLY DECLARES, and it is a closed set
 * rather than a shape.
 *
 * DEFECT 0c7bc84a. The shape that stood here was
 * `/^[A-Za-z0-9][A-Za-z0-9 ._/()-]{0,99}$/` — a character class with no comma in
 * it. The only check that guards main is named
 * `main canary (gates, migration, types, freshness)`, so the one name card 13
 * exists to read was the one name it refused, with `gate_conclusion_query_invalid`
 * and an `invalid_field` of `checkName`. `pg_dump -> age-encrypt -> artifact`
 * was refused too, for the `>`. A reader whose validator was invented rather
 * than read off the workflows could not have admitted either.
 *
 * SO THE VALIDATOR IS AN ENUMERATION, READ OFF THE DECLARING FILES. Every entry
 * below is the name GitHub gives a check run for one declared job, or the name
 * this repository's own code posts a check run under. A job's check-run name is
 * its `name:` when it has one and its job id when it does not, which is why
 * `merge` is in the list spelled the way source-merge-controller.yml spells the
 * job.
 *
 *   .github/workflows/automerge-pilot.yml:25   Plan against current GitHub evidence
 *   .github/workflows/automerge-pilot.yml:91   Read-only exact merge-ref verification
 *   .github/workflows/automerge-pilot.yml:178  Conditional squash merge from protected main
 *   .github/workflows/backup-nightly.yml:116   pg_dump -> age-encrypt -> artifact
 *   .github/workflows/ci.yml:89                ops/ci.sh --strict
 *   .github/workflows/db-acceptance.yml:71     local-db-ci --class migration
 *   .github/workflows/edge-liveness.yml:70     is anything that should be reporting not reporting
 *   .github/workflows/main-canary.yml:77       main canary (gates, migration, types, freshness)
 *   .github/workflows/source-merge-controller.yml:20  merge   (job id; the job declares no name:)
 *   ops/backup-workflow-status.py:45           Backup artifact   (CHECK_NAME, posted by create_check)
 *
 * WHY A SET AND NOT A WIDER PATTERN, and the two clauses it reconciles. A
 * pattern that admitted every name above would have to admit `,` `>` and `/`,
 * because declared names contain all three — at which point "reject path
 * separators" is no longer something the validator does. An enumeration rejects
 * a newline, rejects a separator, and rejects three hundred characters for the
 * same reason it rejects everything else: the string is not one of ten literal
 * names this module wrote down. The only `/` and `>` that reach the store are
 * this module's own two, out of this module's own constant, and they reach it as
 * an `encodeURIComponent`-ed query parameter rather than as a path segment.
 *
 * THIS IS AN ADDRESS, NOT AN AUTHORITY. A caller still chooses WHICH declared
 * check to ask about, exactly as it chooses which commit; what it cannot do is
 * name a check that no workflow declares, and it never chose the answer. The set
 * is module-private and frozen, and nothing exported hands it back.
 *
 * WHEN A WORKFLOW ADDS A JOB this list is wrong, and it is wrong quietly —
 * a reader would refuse the new check instead of reading it. So the test file
 * derives the same set a second time by parsing `.github/workflows/*.yml` and
 * `ops/backup-workflow-status.py`, and fails when the two disagree. That test is
 * the maintenance contract for this constant.
 */
const DECLARED_CHECK_NAMES = Object.freeze([
  "Plan against current GitHub evidence",
  "Read-only exact merge-ref verification",
  "Conditional squash merge from protected main",
  "pg_dump -> age-encrypt -> artifact",
  "ops/ci.sh --strict",
  "ops/ci.sh --strict --only pushfloor unit types contract secret dependency binding artifact freshness",
  "ops/ci.sh --strict --only gates",
  "ops/ci.sh --strict --only replay",
  "ops/ci.sh --strict --only migration",
  "local-db-ci --class migration",
  "is anything that should be reporting not reporting",
  "main canary",
  "Resolve exact-tree PR evidence",
  "merge",
  "measure",
  "CI flake proposals",
  "Backup artifact",
]);

/**
 * GitHub's own ceiling on a check run's name. Nothing caller-supplied is ever
 * measured against it — the enumeration above already refuses every length but
 * the ten it holds. It is here so the enumeration is measured against it: the
 * self-check below refuses to let this module load carrying a name with a
 * newline, a control character, or more bytes than GitHub would store, which is
 * the way a future edit to the list gets caught at import rather than at a
 * reader's first live call.
 */
const GITHUB_CHECK_NAME_LIMIT = 255;
const DECLARED_CHECK_NAME_SHAPE = /^[^\p{Cc}\p{Cf}]+$/u;
for (const declared of DECLARED_CHECK_NAMES) {
  if (typeof declared !== "string" || declared.length === 0
    || declared.length > GITHUB_CHECK_NAME_LIMIT
    || !DECLARED_CHECK_NAME_SHAPE.test(declared))
    throw new TypeError("a declared check name is not a storable check name");
}

/**
 * WHAT THE SCHEDULER WRAPPER ACTUALLY WRITES, read off the two places that
 * define it rather than imagined:
 *
 *   db/schema.sql, `run_source_kind_check` — ops.run.source_kind is one of
 *     collector, registry, wrapper, operator. There is no "scheduler". A reader
 *     that required one could never match a real row, and an earlier draft of
 *     this file required exactly that.
 *   bin/run-scheduled.sh — the scheduler wrapper writes
 *     `--source-kind wrapper --source-ref bin/run-scheduled.sh`.
 *
 * So "the scheduler wrote it, not a hand-run" is: source_kind is `wrapper` AND
 * source_ref is that script. An `operator` row is a hand-run and a `collector`
 * row is a probe; neither is a dispatch this clause will speak for.
 *
 * AND A RECEIPT IS NOT A STRING THE LEDGER HAPPENED TO HOLD. Until 2026-09-11
 * bin/run-scheduled.sh passed no `--evidence-ref` at all, so every row it wrote
 * carried evidence_ref null and this clause could not be met by any scheduled
 * run in the ledger's history. The wrapper now MINTS its own receipt after its
 * child exits — no flag, no path, nothing a caller or the child can hand it —
 * and that receipt carries, in its own bytes, which run it is for and when it
 * was minted. So "bound to a receipt" is three questions and not one: the ref
 * is there, it is FOR THIS RUN (the run-key hash the receipt carries equals the
 * hash of the row's own run key), and it was minted AFTER the row's dispatch. A
 * receipt file left on disk by an earlier run, or minted for a different job,
 * answers the second or the third with a mismatch. Mere presence would have
 * accepted both.
 */
const SCHEDULER_SOURCE_KIND = "wrapper";
const SCHEDULER_SOURCE_REF = "bin/run-scheduled.sh";

/**
 * THE SAME TWO FACTS, IN THE FORM THE STORE HANDS THEM OVER. A ledger row's
 * source kind and source ref are free-form control-plane text, so the store
 * digests them rather than carrying a word it did not write; the clause is still
 * "written by the wrapper", asked as an equality against the digest of this
 * module's own constant. The constants above stay spelled out because they are
 * what a human checks against bin/run-scheduled.sh, and because a digest nobody
 * can read is not a specification.
 */
const SCHEDULER_SOURCE_KIND_DIGEST = digest(SCHEDULER_SOURCE_KIND);
const SCHEDULER_SOURCE_REF_DIGEST = digest(SCHEDULER_SOURCE_REF);

/**
 * A check run that GitHub says has finished. Same reasoning: the store digests
 * the wire's status word — "completed" carries a privileged substring and is
 * text GitHub chose besides — and the comparison happens here, against this
 * module's own copy of the word.
 */
const COMPLETED_STATUS = "completed";
const COMPLETED_STATUS_DIGEST = digest(COMPLETED_STATUS);

/**
 * The digest of each documented conclusion, in the same order. A conclusion
 * reaches a consumer by its digest MATCHING one of these and the consumer being
 * handed GITHUB_CONCLUSIONS[index] — this module's own constant — so a word
 * GitHub has not documented cannot appear in an answer even as a substring.
 */
const GITHUB_CONCLUSION_DIGESTS = Object.freeze(GITHUB_CONCLUSIONS.map(one => digest(one)));

// ---------------------------------------------------------------------------
// Small, total helpers.
// ---------------------------------------------------------------------------

/** Instants, never strings. An unparseable timestamp is a missing timestamp. */
function instant(value) {
  if (typeof value === "string" || value instanceof Date) {
    const parsed = Date.parse(value instanceof Date ? value.toISOString() : value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function text(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * One field out of a caller's object, safely. A Proxy whose `get` trap throws,
 * a throwing getter, a null prototype, a revoked Proxy — all of them come back
 * as null rather than as an exception that would carry the caller's own message
 * up the stack. The value is returned only to be MATCHED against; it is never
 * placed in a result.
 */
function field(query, key) {
  try {
    if (query === null || query === undefined) return null;
    const value = Reflect.get(Object(query), key);
    return typeof value === "string" ? value : null;
  } catch {
    return null;
  }
}

/**
 * Whether a caller NAMED a field at all, as opposed to naming it badly.
 *
 * The distinction exists for exactly one reason (2026-09-12, PR 1013 correction
 * round): two of this module's addresses are now DERIVABLE, and an omitted
 * address asks for the derivation while a malformed one is still refused. A
 * throwing trap counts as named and then fails validation, so a hostile query
 * cannot reach the derivation by making a getter explode.
 */
function named(query, key) {
  try {
    if (query === null || query === undefined) return false;
    return Reflect.get(Object(query), key) !== undefined;
  } catch {
    return true;
  }
}

/** A validated field, or null. Validation is total: no partial credit. */
function matched(query, key, pattern) {
  const value = field(query, key);
  return value !== null && pattern.test(value) ? value : null;
}

/**
 * A DECLARED check name, or null. Membership in this module's own frozen list,
 * by `Array.prototype.includes` over its elements — not a property lookup on a
 * table, because `TABLE["toString"]` answers with a function and a caller that
 * names `constructor` would have addressed something no workflow declares. A
 * value that is not a string never gets this far: `field()` returns null for
 * everything else.
 */
function declaredCheckName(query, key) {
  const value = field(query, key);
  return value !== null && DECLARED_CHECK_NAMES.includes(value) ? value : null;
}

function reason(id) {
  if (!REASON_IDS.includes(id)) throw new TypeError(`${id} is not a registered seam reader reason`);
  return id;
}

function finding(id) {
  if (!FINDINGS.includes(id)) throw new TypeError(`${id} is not a registered seam finding`);
  return id;
}

/**
 * The answer shape. `decision` is "refuse" or "report" — never "allow", and
 * there is no third value. A report is a statement about rows in a ruled store;
 * it is not a permission and no consumer may treat it as one.
 *
 * The card is addressed by its OPAQUE TOKEN. No gate seam name, no step ref and
 * no caller string is in this object anywhere.
 */
function answer(cardRef, ruling, queryDigest, decisionValue, reasonId, body) {
  return Object.freeze({
    schema_version: GATE_ZERO_SEAM_READERS_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    card_ref: cardRef,
    store_ref: ruling.store_ref,
    ruling_decision_ref: ruling.decision_ref,
    query_digest: queryDigest,
    status: decisionValue === "refuse" ? "unavailable" : "evidence_returned",
    decision: decisionValue,
    reason_id: reason(reasonId),
    caller_evidence_admitted: false,
    model_judgment_admitted: false,
    decided_by: "ruled_store_rows",
    effects: V5_NO_EFFECTS,
    ...body,
  });
}

/** A refusal that carries no finding, because no rows were derived from. */
function refuse(cardRef, ruling, queryDigest, reasonId, body) {
  return answer(cardRef, ruling, queryDigest, "refuse", reasonId, { finding: null, ...body });
}

/** A derived finding, with the provenance the derivation itself does not have. */
function report(cardRef, ruling, queryDigest, derived, extra) {
  const decisionValue = REPORTING_FINDINGS.includes(derived.finding) ? "report" : "refuse";
  return answer(cardRef, ruling, queryDigest, decisionValue, derived.finding,
    { finding: derived.finding, ...derived.facts, ...extra });
}

/**
 * The digest a result carries in place of the caller's own strings. Built from
 * the NORMALIZED query — the values that already passed their patterns — so a
 * hostile object contributes nothing but the absence of a field.
 */
function queryDigestOf(normalized) {
  return digest(canonicalJson(normalized));
}

// THE RULING GATE IS ONE PREDICATE AND IT IS NOT A NAME ON THIS SURFACE.
// `ruledCardBinding` asks both halves — the table carries a well-formed decision
// id for the card, AND the ruling names the one store that card's reader opens —
// and it lives in internal/gate-zero-seam-binding.v5.js because the GATE asks it
// too. gate-zero-assurance.v5.js used to ask the ruling table itself and read any
// non-null ruling as a bound seam, which is half of this test, so a ruling naming
// another registered store made the gate say bound while this reader refused. One
// predicate, imported by both, is the only shape in which they cannot drift; a
// public export of it would have been the fix at the price of this module's
// four-name promise, so the predicate sits behind an internal path that no public
// namespace re-exports.

/**
 * Fetch, or hand back the finished refusal. ONE function, not one per reader,
 * so both ways a fetch can fail are written once and every reader gets the same
 * answer shape for them.
 *
 *   * The fetcher is the ONE this card serves, and `ruledCardBinding` has already
 *     refused unless the ruling names exactly that store — so the ruling, not
 *     the reader, is what decided a query would happen at all.
 *   * `store_ref` coming back is the store's own statement of which store
 *     answered, and a disagreement with the ruling refuses instead of being
 *     reported over. The earlier reader ignored it and reported the ruled ref
 *     whatever it had actually queried.
 *   * A store that could not be reached refuses with its own closed phrase.
 */
async function fetchOrRefuse(cardRef, ruling, queryDigest, query, unreachableReason, because, body) {
  let fetched;
  try {
    fetched = await CARD_FETCH[cardRef](query);
  } catch (cause) {
    return { refusal: refuse(cardRef, ruling, queryDigest, unreachableReason,
      { ...body, unavailable_because: isSeamStoreUnreachable(cause) ? cause.because : because }) };
  }
  if (fetched?.store_ref !== ruling.store_ref)
    return { refusal: refuse(cardRef, ruling, queryDigest, "store_ref_not_the_ruled_one", body) };
  return { rows: Array.isArray(fetched.rows) ? fetched.rows : [] };
}

// ---------------------------------------------------------------------------
// THE DERIVATIONS. Module-private, every one of them: they turn rows into a
// finding, and a function that does that is exactly what the standing rule of
// 2026-09-11 forbids on a public surface, under any name and with any tag. They
// are reachable only from the three readers below, each of which has already
// checked a ruling and fetched the rows itself. There is no test-only export,
// no injectable variant and no "unwired" twin; the tests reach these the same
// way any consumer would, through a reader, over fixture rows.
// ---------------------------------------------------------------------------

function derived(id, facts) {
  return { finding: finding(id), facts };
}

// Card 11 — an accepted predecessor outcome, and the hash it was asked about.
//
// THE THREE CONDITIONS, AND ALL THREE MUST HOLD.
//
//   1. A row's `status` is exactly "accepted". "pending_human_acceptance" is not
//      a near miss; a proposal nobody signed is not an outcome.
//   2. The row is COMPLETE: an acceptance receipt whose matching card detail is
//      absent is a missing row, and a missing row refuses. The store COUNTS the
//      card rows it found for the receipt rather than synthesizing a
//      null-outcome row that looks accepted — and it is a count rather than the
//      boolean it shipped as, because a bare `true` out of an exported function
//      is the shape the standing rule closes over.
//   3. That row's ACCEPTANCE RECEIPT hash equals the outcome hash this reader
//      was asked about. Not the proposal's `feedback_hash` — the receipt's,
//      because the receipt is the row a human's acceptance wrote and the
//      proposal is the row a machine wrote. A forged or stale hash therefore
//      does not merely fail to match a proposal; it fails to match a signature.
//
// None of the three can be satisfied by the `outcomeHash` argument: it is
// compared against a stored value and never returned, so a caller who supplies
// a hash learns only whether the store agrees with it.
/**
 * The acceptance-receipt hash this reader compares against when the caller named
 * none: the PROPOSAL hash the card carries for the CURRENT accepted row. The
 * store preserves work_request_card's canonical newest-first order
 * (accepted_at DESC, receipt id DESC), so append-only historical acceptances do
 * not make the current receipt ambiguous. Null when that current row lacks its
 * detail, which fails the comparison below rather than inventing a value that
 * would pass it.
 */
function derivedAcceptanceHash(rows) {
  const current = (Array.isArray(rows) ? rows : [])
    .find(row => text(row?.status) === "accepted");
  if (current?.detail_row_count !== 1) return null;
  const proposal = text(current?.feedback_hash);
  return proposal !== null && OUTCOME_HASH.test(proposal) ? proposal : null;
}

function derivePredecessorOutcome(rows, outcomeHash) {
  const all = Array.isArray(rows) ? rows : [];
  const counts = { outcome_rows_seen: all.length, accepted_rows_seen: 0, hash_match: UNKNOWN };
  if (all.length === 0) return derived("predecessor_outcome_absent", { ...counts, hash_match: FAILED });
  const accepted = all.filter(row => text(row?.status) === "accepted");
  counts.accepted_rows_seen = accepted.length;
  if (accepted.length === 0)
    return derived("predecessor_outcome_not_accepted", { ...counts, hash_match: FAILED });
  const incomplete = accepted.filter(row => row?.detail_row_count !== 1);
  if (incomplete.length > 0)
    return derived("predecessor_outcome_detail_absent", { ...counts, hash_match: FAILED });
  const asked = typeof outcomeHash === "string" && OUTCOME_HASH.test(outcomeHash) ? outcomeHash : null;
  const matching = accepted.filter(row => {
    const receiptHash = text(row?.accepted_feedback_hash);
    return receiptHash !== null && asked !== null && receiptHash === asked;
  });
  if (matching.length === 0)
    return derived("predecessor_outcome_acceptance_receipt_hash_mismatch", { ...counts, hash_match: FAILED });
  return derived("predecessor_outcome_accepted_with_matching_hash", { ...counts, hash_match: HELD });
}

// Card 12 — the canary, its receipt binding, and an observation strictly after
// dispatch.
//
// THE THREE CLAUSES GATE ZERO NAMES, each answered from a real ops.run row and
// its timestamps, and a missing row refusing rather than defaulting.
//
//   receipt_binding             the dispatch row names A RECEIPT OF ITS OWN, and
//                               five facts have to agree for it to hold: the
//                               evidence ref is there at all; the source kind
//                               and source ref are the ones bin/run-scheduled.sh
//                               writes — `wrapper` and the script's own path —
//                               not a hand-run's `operator` row and not a
//                               probe's `collector` row; the run-key hash the
//                               receipt CARRIES equals the hash of this row's
//                               own run key; and the receipt was MINTED AFTER
//                               this row's `started_at`. The last two are what
//                               separate a receipt this run produced from one
//                               that was merely lying around: a stale file the
//                               child never refreshed predates the dispatch, and
//                               a receipt minted for another job names another
//                               run. Every one of those fields arrives as a
//                               digest or an instant, and every question asked
//                               of them is an equality or a comparison, so no
//                               ledger text is in the answer.
//   observation_after_dispatch  the observation's `observed_at` is STRICTLY after
//                               the dispatch row's `started_at`. Equal instants
//                               fail: rows written in one transaction share now().
//   canary_match                the observation is an observation OF THIS canary —
//                               same run key, same evidence ref.
//
// The dispatch row is the one with the latest `started_at`; the observation is
// the one with the latest `observed_at` among rows that also ENDED, because an
// observation of a run still in flight is not an observation of its result. In
// production these are usually THE SAME ROW: the wrapper writes one row per run
// carrying started_at, ended_at and an observed_at stamped when the row lands.
// All three clauses are reported every time rows allow them to be computed, so a
// caller sees which clause failed rather than only that one did.
function deriveSchedulerCanary(rows) {
  const all = Array.isArray(rows) ? rows : [];
  const serviceRows = all.filter(row => text(row?.service_key_digest) !== null);
  const blank = {
    service_rows_seen: serviceRows.length, run_rows_seen: 0,
    receipt_binding: UNKNOWN, observation_after_dispatch: UNKNOWN, canary_match: UNKNOWN,
  };
  if (serviceRows.length === 0) return derived("scheduler_service_row_absent", blank);
  const runRows = serviceRows.filter(row => text(row?.run_key_digest) !== null);
  blank.run_rows_seen = runRows.length;
  const dispatched = runRows.filter(row => instant(row?.started_at) !== null);
  if (dispatched.length === 0) return derived("scheduler_dispatch_row_absent", { ...blank });
  const dispatch = dispatched.reduce((a, b) => (instant(b.started_at) > instant(a.started_at) ? b : a));
  const observed = runRows.filter(row => instant(row?.observed_at) !== null && instant(row?.ended_at) !== null);
  if (observed.length === 0) return derived("scheduler_observation_absent", { ...blank });
  const observation = observed.reduce((a, b) => (instant(b.observed_at) > instant(a.observed_at) ? b : a));

  // Two of these read fields the STORE derived from the receipt's own bytes:
  // what run the receipt says it is for, and when the wrapper minted it. A ref
  // the store could not parse as this wrapper's mint arrives as null in both,
  // which fails the clause rather than defaulting it.
  const receiptNamesThisRun = text(dispatch.receipt_run_key_digest) !== null
    && text(dispatch.receipt_run_key_digest) === text(dispatch.run_key_receipt_digest);
  // STRICTLY after, for the reason the observation clause is strict: an instant
  // equal to the dispatch is an instant that proves no ordering at all.
  const mintedAfterDispatch = instant(dispatch.receipt_minted_at) !== null
    && instant(dispatch.started_at) !== null
    && instant(dispatch.receipt_minted_at) > instant(dispatch.started_at);
  const boundToReceipt = text(dispatch.evidence_ref_digest) !== null
    && text(dispatch.source_kind_digest) === SCHEDULER_SOURCE_KIND_DIGEST
    && text(dispatch.source_ref_digest) === SCHEDULER_SOURCE_REF_DIGEST
    && receiptNamesThisRun && mintedAfterDispatch;
  const afterDispatch = instant(observation.observed_at) > instant(dispatch.started_at);
  const canaryMatch = text(observation.run_key_digest) !== null
    && text(observation.run_key_digest) === text(dispatch.run_key_digest)
    && text(observation.evidence_ref_digest) !== null
    && text(observation.evidence_ref_digest) === text(dispatch.evidence_ref_digest);

  const facts = {
    service_rows_seen: serviceRows.length,
    run_rows_seen: runRows.length,
    receipt_binding: boundToReceipt ? HELD : FAILED,
    observation_after_dispatch: afterDispatch ? HELD : FAILED,
    canary_match: canaryMatch ? HELD : FAILED,
  };
  if (!boundToReceipt) return derived("scheduler_canary_not_bound_to_receipt", facts);
  if (!canaryMatch) return derived("scheduler_observation_canary_mismatch", facts);
  if (!afterDispatch) return derived("scheduler_observation_not_after_dispatch", facts);
  return derived("scheduler_canary_and_observation_join", facts);
}

// Card 13 — what hosted CI concluded about one commit, under one check name.
//
// A check run that has not COMPLETED has no conclusion, so it is not a check run
// this derivation will speak for: `status` must be "completed" and `conclusion`
// must be one of GitHub's own documented words. Several completed runs under one
// name on one commit (a re-run) resolve to the LATEST by `completed_at`, which is
// what the merge gate itself acts on, and the count is reported so a reader can
// see there was more than one.
//
// The conclusion is returned as GitHub's own word and is NOT mapped to green,
// passing or ok — but it is returned by EQUALITY against the closed list above,
// so the string a consumer receives is a constant out of this file rather than
// text off the wire. Whatever consumes a gate conclusion decides what a
// conclusion means; a reader that translated one would be that consumer wearing
// a reader's name.
//
// THE EQUALITY IS ON DIGESTS, because the store no longer carries the wire's own
// words at all: it hands over the digest of the status and the digest of the
// conclusion, and the word a consumer receives is GITHUB_CONCLUSIONS[index] —
// this module's constant, reached by its digest having matched.
function deriveGateConclusion(rows, headSha) {
  const all = Array.isArray(rows) ? rows : [];
  const completed = all.filter(row =>
    text(row?.status_digest) === COMPLETED_STATUS_DIGEST
    && text(row?.conclusion_digest) !== null
    && text(row?.head_sha) === headSha);
  const counts = { check_runs_seen: all.length, completed_runs_seen: completed.length };
  if (completed.length === 0)
    return derived("gate_conclusion_check_absent", { ...counts, conclusion: null });
  const latest = completed.reduce((a, b) =>
    ((instant(b.ended_at) ?? 0) > (instant(a.ended_at) ?? 0) ? b : a));
  const index = GITHUB_CONCLUSION_DIGESTS.indexOf(text(latest.conclusion_digest));
  if (index < 0) return derived("gate_conclusion_unrecognized", { ...counts, conclusion: null });
  return derived("gate_conclusion_observed", { ...counts, conclusion: GITHUB_CONCLUSIONS[index] });
}

// ---------------------------------------------------------------------------
// Card 11 — the predecessor outcome reader.
// ---------------------------------------------------------------------------

/**
 * Whether the ruled store holds an ACCEPTED outcome for one predecessor step
 * whose acceptance-receipt hash is the one this reader was asked about.
 *
 * Both halves are required and neither can come from the caller: the acceptance
 * is a receipt row a human's act wrote, and the hash the caller supplies is
 * compared against that row's hash and never echoed. A forged hash therefore
 * fails to match a signature, not merely a proposal — and it fails closed.
 */
async function predecessorOutcomeEvidence(query) {
  const ruling = ruledCardBinding(CARD_11);
  if (ruling === null) return readGateZeroPredecessorJoin();

  const stepRef = field(query, "stepRef");
  const known = typeof stepRef === "string"
    && V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.includes(stepRef) ? stepRef : null;
  // THE ACCEPTANCE-RECEIPT HASH IS NOW OPTIONAL (2026-09-12, PR 1013 correction
  // round), and omitting it is the honest address rather than a shortcut. A
  // caller that names one still has it COMPARED against the receipt row and
  // never echoed, exactly as before. A caller that names none is asking this
  // reader to read the acceptance receipt the ruled store holds for this step's
  // Work Request — which is what "read the receipt by its step ref" means, and
  // it is what removes the human who used to look a hash up and paste it.
  //
  // AND THE DERIVED READING IS WEAKER THAN THE NAMED ONE, said here rather than
  // discovered later: when the hash is derived, `hash_match` reports the
  // receipt-to-card join the STORE performed, not a comparison against a value
  // that arrived from somewhere else. What still stands on its own is the fact
  // Gate Zero actually needs — an accepted acceptance receipt exists for this
  // predecessor and its card detail is present — and an absent or unaccepted
  // row still refuses by its own name.
  const askedNamed = named(query, "outcomeHash");
  const outcomeHash = askedNamed ? matched(query, "outcomeHash", OUTCOME_HASH) : null;
  const queryDigest = queryDigestOf({
    step_ref: known, outcome_hash: outcomeHash, hash_derived: !askedNamed });

  if (known === null)
    return refuse(CARD_11, ruling, queryDigest, "unknown_predecessor_step", {});
  // The Work Request ref comes from this module's frozen table, never from the
  // caller's string — so what an answer carries is this module's identifier.
  const workRequestRef = PREDECESSOR_WORK_REQUEST_REFS[known];
  if (workRequestRef === null)
    return refuse(CARD_11, ruling, queryDigest, "scheduler_predecessor_not_outcome_backed", {});
  if (askedNamed && outcomeHash === null)
    return refuse(CARD_11, ruling, queryDigest, "predecessor_query_invalid",
      { invalid_field: "outcomeHash" });

  const got = await fetchOrRefuse(CARD_11, ruling, queryDigest, { workRequestRef },
    "predecessor_outcome_store_unreachable", "the store did not answer",
    { work_request_ref: workRequestRef });
  if (got.refusal !== undefined) return got.refusal;
  // The derived value comes off the CARD side of the store's own join — the
  // proposal hash — and is compared against the RECEIPT side below. It is this
  // module's read of a ruled row, never a value that entered from a caller.
  const asked = outcomeHash ?? derivedAcceptanceHash(got.rows);
  return report(CARD_11, ruling, queryDigest, derivePredecessorOutcome(got.rows, asked),
    { work_request_ref: workRequestRef, acceptance_hash_derived: !askedNamed });
}

// ---------------------------------------------------------------------------
// Card 12 — the scheduler canary reader.
// ---------------------------------------------------------------------------

/**
 * The three clauses `step:scheduler-active-receipt` needs — receipt_binding,
 * observation_after_dispatch, canary_match — taken strictly from Control Plane
 * ledger rows and their timestamps, with a missing row refusing rather than
 * defaulting.
 *
 * The caller says WHICH service and WHICH run key. It cannot say what the rows
 * contain, and it cannot make a row exist: no row, no answer.
 */
async function schedulerCanaryEvidence(query) {
  const ruling = ruledCardBinding(CARD_12);
  if (ruling === null) return readGateZeroPredecessorJoin();

  const serviceKey = matched(query, "serviceKey", SERVICE_KEY);
  // OPTIONAL for the same reason the acceptance hash is (2026-09-12): a run key
  // nobody has run yet cannot be pasted, and the ledger already knows which run
  // this service's wrapper last minted a receipt for. Omitting it addresses THAT
  // row; naming a malformed one is still refused.
  const canaryNamed = named(query, "canaryRunKey");
  const canaryRunKey = canaryNamed ? matched(query, "canaryRunKey", RUN_KEY) : null;
  const queryDigest = queryDigestOf({
    service_key: serviceKey, canary_run_key: canaryRunKey, run_key_derived: !canaryNamed });

  if (serviceKey === null || (canaryNamed && canaryRunKey === null))
    return refuse(CARD_12, ruling, queryDigest, "scheduler_query_invalid",
      { invalid_field: serviceKey === null ? "serviceKey" : "canaryRunKey" });

  // The key is OMITTED from the store query rather than passed as null, because
  // absent is what asks the ledger for its own latest minted run; a null would
  // be a named address the store cannot serve.
  const got = await fetchOrRefuse(CARD_12, ruling, queryDigest,
    canaryRunKey === null ? { serviceKey } : { serviceKey, canaryRunKey },
    "scheduler_ledger_unreachable", "the ledger did not answer", {});
  if (got.refusal !== undefined) return got.refusal;
  return report(CARD_12, ruling, queryDigest, deriveSchedulerCanary(got.rows),
    { run_key_derived: !canaryNamed });
}

// ---------------------------------------------------------------------------
// Card 13 — the gate conclusion reader.
// ---------------------------------------------------------------------------

/**
 * What hosted CI concluded for one commit (`headSha`) under one check name, in
 * GitHub's own word, or unavailable.
 *
 * The conclusion is not translated. "success" is returned as "success" and is
 * not turned into green, passing or ok — deciding what a conclusion MEANS is
 * the consuming gate's job, and a reader that did it would be that gate.
 */
async function gateConclusionEvidence(query) {
  const ruling = ruledCardBinding(CARD_13);
  if (ruling === null) return readGateGraphAssurance();

  const headSha = matched(query, "headSha", HEAD_SHA);
  const checkName = declaredCheckName(query, "checkName");
  const queryDigest = queryDigestOf({ head_sha: headSha, check_name: checkName });

  if (headSha === null || checkName === null)
    return refuse(CARD_13, ruling, queryDigest, "gate_conclusion_query_invalid",
      { invalid_field: headSha === null ? "headSha" : "checkName" });

  const got = await fetchOrRefuse(CARD_13, ruling, queryDigest, { headSha, checkName },
    "gate_conclusion_source_unreachable", "the checks source did not answer", {});
  if (got.refusal !== undefined) return got.refusal;
  return report(CARD_13, ruling, queryDigest, deriveGateConclusion(got.rows, headSha), {});
}

// ---------------------------------------------------------------------------
// THE PUBLIC SURFACE, AND THE ONE GUARDED BOUNDARY IT PASSES THROUGH.
//
// A reader's contract is that it ANSWERS. It never throws, and a caller reading
// a refusal never has to catch one — which is only true if there is a boundary
// that makes it true, rather than three implementations each remembering not to
// let anything escape. `reason()` and `finding()` raise on an unregistered id;
// the derivations index into rows; `fetchOrRefuse` reads a property off whatever
// a store handed back. Any of those can throw, and a native throw carries an
// ENGINE-BUILT STACK — a list of the caller's own frame names and file paths,
// bytes this module did not write. A caller function named `green` calling a
// reader that threw got `at green` handed back to it.
//
// So every export is the same wrapper over a module-private implementation, and
// what it answers when anything at all is thrown is the GATE'S OWN ANSWER for
// that card — the identical object the reader returns while the card is unruled.
// That is fail-closed by construction: the worst a thrown value can do is make a
// ruled reader as unavailable as an unruled one. Nothing is re-thrown, so no
// stack, no message and no caller byte leaves this surface by the throwing door
// at all.
// ---------------------------------------------------------------------------

// AMENDMENT 2'S CLOSED SHAPE COMES FROM ./closed-callable.js (amendment 9, fifth
// correction round, 2026-09-14). This file used to define its own copy, on the
// argument that a self-contained module is worth a duplicated primitive. The
// review measured that argument against the copies and it failed: the local
// copies had already DIVERGED from the shared one — they never froze the
// callable, which is clause (c), the clause the first shape enumeration added
// after finding it missing — so the file whose whole job is to close a probe was
// running the unhardened version of the shape. A security primitive that exists
// five times is hardened in one of five places. There is one definition now, and
// the enumeration control walks every export against it.

// AND THE BOUNDARY HAS TO COVER `new`, WHICH IT DOES BY LEAVING NOTHING TO
// CONSTRUCT. The fourth review round found that an async function has no
// [[Construct]] at all, so `Reflect.construct` on one is refused by the ENGINE
// before the function starts; the third correction answered that with a Proxy
// whose construct trap returned the gate's own answer, and the fifth round found
// what that proxy still forwarded — `get`, and with it the raw target under
// `prototype.constructor`, constructable with a `new.target` of the caller's
// choosing.
//
// So the wrapper is gone and every export is an ARROW FUNCTION. An arrow is not
// a constructor and has no `prototype`, so there is no target to reach and no
// `newTarget.prototype` read on the way in: construction is refused by the
// engine, in the caller's own frame, having run no line of this module. That is
// clause (a) of amendment 2, and the refusal is out of scope as a finding for
// exactly the reason it is safe — nothing here executed, so nothing here can
// have read the caller's object or written the sentence that comes back.
//
// The arrow returns the async work as a promise, so every caller sees what it
// saw before, and the CALLING door still answers with the gate's own object.
function guarded(gateAnswer, read) {
  const guardedRead = query => (async () => {
    try {
      return await read(query);
    } catch {
      return gateAnswer();
    }
  })();
  return closedCallable(guardedRead);
}

// THE GATE'S ANSWER ARRIVES AS A THUNK, and that is load order rather than
// style. gate-zero-assurance.v5.js imports this module for its seam bindings and
// this module imports its answers back, so the two form a cycle; since its
// exports wear amendment 2's closed shape they are `const` bindings, which are in
// their temporal dead zone until that module's body has run. Reading one HERE, at
// this module's own evaluation time, throws on the import order that starts at
// the gate. A thunk reads it when a reader actually falls back, by which time
// both modules are evaluated.
export const readPredecessorOutcomeEvidence =
  guarded(() => readGateZeroPredecessorJoin(), predecessorOutcomeEvidence);
export const readSchedulerCanaryEvidence =
  guarded(() => readGateZeroPredecessorJoin(), schedulerCanaryEvidence);
export const readGateConclusionEvidence =
  guarded(() => readGateGraphAssurance(), gateConclusionEvidence);
