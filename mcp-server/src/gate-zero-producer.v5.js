// DoctorCRE v5 slice V5-A02 — THE GATE ZERO PRODUCER, Step A.
//
// WHAT THIS FILE IS, in one sentence: the seam the three ruled evidence readers
// were built for. They answer truthfully about whatever row they are pointed at;
// pointing them is this module's job, and nothing else in this repository has
// ever been allowed to do it.
//
// WHAT IT DOES, in the order it does it:
//
//   1. AIMS the three bound readers at NAMED rows, from bindings that are
//      module-private constants or derived from the staffed seat.
//   2. APPLIES the three deterministic clauses V5-A02's checkable_done names —
//      in production form, over READINGS, with the conditional mood gone.
//   3. EARNS `negative_admission_result` rather than writing it, by mutating a
//      reading in each required way and requiring its own clause to refuse.
//   4. ASSEMBLES one `consumer-gate-receipt.v1` — all twenty-one fields — and
//      takes its digest.
//
// WHAT IT IS NOT, and every one of these is a door somebody could have built:
//
//   * IT TAKES NO ARGUMENT. `v5A02GateZeroEmitOutcome` has arity zero. There is
//     no evidence parameter, no hash parameter, no identity parameter, no head
//     revision parameter and no reader parameter. The PR 985 defect was a public
//     function that turned a CALLER'S DESCRIPTION of evidence into a verdict;
//     the standing rule of 2026-09-11 closes it under any name, and the cheapest
//     way to stay closed is to have nowhere to put one.
//   * IT READS NO ENVIRONMENT VARIABLE. Not for a binding, not for an identity,
//     not for a switch. The ONE thing the environment supplies is where a ruled
//     store lives, and that is `gate-zero-seam-stores.v5.js`'s business, reached
//     only through a reader whose ruling already said it may look.
//   * IT EXPORTS NO SETTER AND NO REGISTRY. Every binding below is either a
//     frozen module-private constant or DERIVED at call time from the candidate
//     tree, the ruled stores and the authenticated call. There is no line a
//     human pastes, so there is no human step inside this slice at all.
//   * IT WRITES NOTHING. No database, no file, no verb. Step A emits a VALUE.
//     Recording that value as a row is Step B, it is the heavy path, and it is
//     waiting on Joe's ruling about which authority may write it.
//
// ---------------------------------------------------------------------------
// THE RUN BINDING, AND WHY NOBODY PASTES IT.
//
// The addresses a Gate Zero run stands on are facts about THAT RUN: the
// candidate's head revision, the scheduler service and the canary the wrapper
// dispatched, the actor that built the candidate, and each Work-Request
// predecessor's acceptance receipt. None of them can be a caller argument, and
// none of them can be invented here — an invented hash is the exact "digest
// derived from a synthetic test fixture" that benchmark-acceptance-store.v5.js
// lists as explicitly refused.
//
// THE FIRST DRAFT SHIPPED THEM AS PASTED LINES, on the theory that a human
// editing a constant is how a ruling changes. The review was right to refuse it:
// the seam study says in its own words that there is NO human step in the middle
// of this slice, and a file whose comment says "somebody edits this and commits
// it" names no owner and never acquires one.
//
// SO EVERY ONE OF THEM IS DERIVED, and the table beside SCHEDULER_SERVICE_KEY
// below says from what.
//
// AND AN ABSENCE IS CLASSIFIED HONESTLY, which the second review round asked
// for. Two different things can go missing and they are two different answers:
// `gate_zero_candidate_metadata_absent` when this DEPLOY carries no usable build
// stamp — which var, which field — and `gate_zero_run_binding_unnamed` only when
// a RULED ROW is genuinely absent, which today is exactly one row: the
// release-candidate record naming who built the stamped revision. The first
// draft answered the second for everything, which described the wrong fault to
// whoever read it next.
//
// ---------------------------------------------------------------------------
// THE DIGEST RECIPE, AND THE GAP THAT HAS SINCE BEEN CLOSED.
//
// r7's canonicalization contract is JCS over UTF-8, SHA-256, no trailing
// newline. Its receipt-payload rule named domain tags for
// `benchmark-manifest.v1`, `attended-effect-capability.v1`,
// `attended-effect-consumption-receipt.v2` and
// `attended-effect-outcome-receipt.v1` — and, until 2026-09-13, NOT for
// `consumer-gate-receipt.v1`. Two readings were available and they produce
// different digests: a plain `digest(receipt)`, or a tagged
// `digest([schema_version, receipt])`.
//
// THIS MODULE TOOK THE PLAIN READING AND PUT THE CHOICE ON THE SURFACE, as
// `V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE`, so a reviewer could overturn it in
// one line — noting that doing so would be an r7 amendment resealing all
// sixty-two chunks.
//
// A REVIEWER DID, AND THE AMENDMENT LANDED. Act 5 of accepted plan
// PLAN-a5059eb52474-v3 amended r7's `receipt_payload_digest_rule` on 2026-09-13
// to declare this schema's payload digest as the TAGGED preimage — sha256 over
// the JCS serialization of `["consumer-gate-receipt.v1", <receipt>]` — stating
// that a plain digest over the receipt alone does not satisfy it. The packet was
// resealed across all sixty-two chunks and is pinned at `4379c60e…`. So this
// module computes the tagged digest, the recipe constant carries the tag as a
// VALUE, and `ops.gate_zero_outcome_digest()` computes the identical preimage in
// SQL from migration 0505 forward.
//
// And the rule's closing clause is obeyed either way: no artifact includes its
// own whole-byte digest as input to that digest. The receipt carries
// twenty-one fields and the digest is taken over all twenty-one; the digest is
// not one of them.
//
// ---------------------------------------------------------------------------
// WHO SIGNS, AND IT IS NOT A CONSTANT.
//
// r7's identity rule: all identities derive from authenticated execution
// context, `producer_role` binds to the registry entry, and the subject maker's
// actor and session must differ from the evaluator's. Caller-supplied identity,
// unauthorized role, or same-actor self-review denies.
//
// THE FIRST DRAFT RECONSTRUCTED THEM instead. It read card 9's holder ref, took
// the actor slug out of it, and built each session ref out of that slug plus a
// digest of the evidence — so any process that imported this module got a
// receipt signed `codex-reviewer` without authenticating anything, and two runs
// over the same rows shared a session. That is the defect the review named
// first, and it is closed here rather than patched.
//
// THE PRODUCER AND THE EVALUATOR ARE THE AUTHENTICATED CALLER, read through the
// module-private `authenticatedCaller()` below. There is no exported reader and
// no exported setter anywhere on this path: the value it answers with is the one
// identity.js's SINGLE REQUEST ENTRY established, derived there from a bearer
// that file matched against the credential map it read from the server's own
// environment. The second round found `runInAuthenticatedCall(actor, fn)` shipped
// as a public export taking an ordinary object; the fifth found the pair that
// replaced it — a public minter and a public dispatcher-factory — composed by a
// probe into the same thing. Neither name exists now, and nothing exported by
// that file returns a branded actor or a callable that enters a context.
//
// THE SUBJECT MAKER IS THE RELEASE-CANDIDATE RECORD FOR THE STAMPED REVISION
// (amendment 9(b), 2026-09-14) — the ops.release row `tools/ops-record.py
// release candidate` filed over an authenticated authority connection for this
// exact build, read back through the Control Plane store. Both halves come out
// of that row: the maker it names, resolved through identity.js's partner
// registry, and the correlation the recorder stamped on it as the session. It is
// NOT HEAD's committer any more, which the third round installed and the fifth
// round's finding 3 undid: a committer line is an attribution anybody with a git
// config can write, the deployed Worker cannot read one, and the session beside
// it was being manufactured by suffixing the EVALUATOR'S OWN session with
// `:candidate-build` — the reviewer's seat, relabelled as the maker's.
//
// CARD 9 IS STILL WHAT BINDS THE SEAM. An unstaffed seat still turns the whole
// slice dark. What the seat no longer does is sign: naming who may sign and
// being the signer are different acts.
//
// ---------------------------------------------------------------------------
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER IS RETURNED, with a stable `reason_id` out of the closed
//     registry below.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Handing this module an
//     argument is not a policy question.

import { artifactManifestDigest, canonicalJson, digest } from "./artifact-trust.js";
import {
  BUILD_STAMP_NAMES,
  CANDIDATE_MANIFEST_SCHEMA,
  serverBuildEnvironment,
  stampedCandidateManifestDigest,
  stampedCandidateManifestText,
  stampedGitSha,
} from "./build-stamp.js";
import { closedCallable } from "./closed-callable.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID, authenticatedIdentity } from "./identity.js";
import {
  CONSUMER_GATE_RECEIPT_FIELDS,
  CONSUMER_GATE_RECEIPT_SCHEMA,
  GATE_ZERO_STEP_REF,
} from "./benchmark-minimum.v5.js";
import {
  V5_A02_GATE_ZERO_GATE_ID,
  V5_A02_GATE_ZERO_ORACLE_REF,
  V5_A02_GATE_ZERO_ORACLE_VERSION,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
  V5_A02_GATE_ZERO_R7_PACKET_SHA256,
  V5_A02_GATE_ZERO_PRODUCER_ROLE,
  V5_A02_SCHEDULER_STEP_REF,
} from "./gate-zero-producer-registration.v5.js";
import {
  readGateConclusionEvidence,
  readPredecessorOutcomeEvidence,
  readSchedulerCanaryEvidence,
} from "./gate-zero-seam-readers.v5.js";
import { fetchCandidateBuildRecordRows, isSeamStoreUnreachable } from "./gate-zero-seam-stores.v5.js";

export const V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION =
  "doctorcre-v5-a02-gate-zero-producer.v1";

/**
 * THE DECLARED DIGEST RECIPE — no longer a stated assumption, because r7 now
 * rules it. The earlier revision of this constant carried `domain_tag: null` and
 * named the tagged reading as "the alternative if overturned". It was overturned:
 * the 2026-09-13 r7 amendment (act 5 of accepted plan PLAN-a5059eb52474-v3,
 * packet `4379c60e…`) declared `consumer-gate-receipt.v1`'s payload digest to be
 * the TAGGED preimage and said in its own words that a plain digest over the
 * receipt alone does not satisfy the rule. So the tag is a value here, and the
 * fields that described the gap describe how it closed.
 */
export const V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE = deepFreeze({
  schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA,
  canonicalization: "jcs_utf8_sha256_no_trailing_newline",
  domain_tag: CONSUMER_GATE_RECEIPT_SCHEMA,
  recipe: "sha256 over the JCS canonicalization of the two-element array " +
    "[\"consumer-gate-receipt.v1\", <the twenty-one-field receipt object>]",
  declared_by_r7: "receipt_payload_digest_rule, amended 2026-09-13 to name this schema's tagged preimage",
  r7_packet_sha256: V5_A02_GATE_ZERO_R7_PACKET_SHA256,
  refused_recipe: "a plain digest over the receipt object alone, which r7 states does not satisfy the rule",
  computed_identically_in_sql_by:
    "ops.gate_zero_outcome_digest(jsonb), replaced with this preimage by migration 0505",
  self_digest_excluded: true,
});

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

// ---------------------------------------------------------------------------
// THE CLOSED VOCABULARIES.
// ---------------------------------------------------------------------------

/**
 * How a clause came out. THREE VALUES AND NO BOOLEAN, the same three the
 * readers use and for the same reason: a clause with no row behind it has not
 * failed, it has not been answered, and a boolean cannot say so.
 */
const HELD = "held";
const FAILED = "failed";
// "unknown", and not the obvious word for a clause nothing answered: every
// string this module can hand the gate is swept by the standing rule's closed
// union AS A SUBSTRING, and the obvious word carries "read". This is the
// readers' own third value, which is the right one anyway.
const UNKNOWN = "unknown";

export const V5_A02_GATE_ZERO_CLAUSE_STATES = deepFreeze([FAILED, HELD, UNKNOWN].sort());

/**
 * Every refusal this producer can answer with. Closed, so a refusal is
 * checkable, and every member is also registered in the GATE's own closed reason
 * registry — the gate refuses to build an answer around an id it does not know,
 * so the two lists cannot drift apart in silence.
 */
export const V5_A02_GATE_ZERO_PRODUCER_REASON_IDS = deepFreeze([
  // THE TWO KINDS OF ABSENCE, AND THEY ARE NOT ONE REASON (2026-09-12, second
  // correction round; narrowed from three by amendment 9, 2026-09-14). The first
  // draft answered `gate_zero_run_binding_unnamed` for all of them, which told a
  // reader "a row is missing" when the truth was "this deploy cannot say what it
  // is". A refusal that misdescribes its own cause is worse than a refusal,
  // because the next session debugs the wrong thing.
  //
  //   gate_zero_candidate_metadata_absent  this DEPLOY carries no usable
  //       candidate stamp: GIT_SHA, CANDIDATE_MANIFEST or
  //       CANDIDATE_MANIFEST_DIGEST is missing, or the manifest does not parse,
  //       lacks a field, names another revision, or is not what the stamped
  //       digest covers. The answer names which var and which field. Nothing is
  //       absent from a ruled store; this run cannot say which candidate it IS.
  //   gate_zero_run_binding_unnamed  a RULED ROW this run's binding derives from
  //       is genuinely absent, and the answer names which. The one such row is
  //       the subject maker: the release-candidate record the deploy wrapper
  //       filed for the stamped revision is missing, its rows disagree about who
  //       made the candidate, or the maker slug is one identity.js does not
  //       register as a partner.
  //
  // `gate_zero_sealed_artifact_absent` IS GONE FROM THIS LIST, and its absence
  // is the shape of amendment 9. It answered "a sealed file one of these digests
  // stands on is not on disk" — a sentence only a process standing in a checkout
  // can say. The environment manifest and the sealed fixture set are digested by
  // the deploy wrapper's sealer now and arrive inside the stamped manifest, so
  // their absence is a missing stamp field and is reported as one. The GATE's
  // registry keeps the id; this producer can no longer reach it, and a
  // vocabulary that keeps unreachable words invites a reader to look for a path
  // that is not there.
  "gate_zero_candidate_metadata_absent",
  "gate_zero_evidence_unavailable",
  "gate_zero_gate_graph_clause_failed",
  "gate_zero_negative_admission_unproved",
  "gate_zero_predecessor_clause_failed",
  "gate_zero_producer_identity_refused",
  "gate_zero_run_binding_unnamed",
  "gate_zero_scheduler_clause_failed",
].sort());

function reason(id) {
  if (!V5_A02_GATE_ZERO_PRODUCER_REASON_IDS.includes(id))
    fail("unknown_producer_reason_id", `${id} is not a registered producer reason`, { reason_id: id });
  return id;
}

/** The one finding each reader gives when it has actually read what was asked. */
const PREDECESSOR_HELD_FINDING = "predecessor_outcome_accepted_with_matching_hash";
const SCHEDULER_HELD_FINDING = "scheduler_canary_and_observation_join";
const CONCLUSION_READ_FINDING = "gate_conclusion_observed";

/**
 * THE FINDINGS THAT MEAN "NO ROW", not "a row that did not hold" — and the
 * distinction is load-bearing rather than pedantic. A canary the ledger has no
 * row for has not failed its clause: nothing was read, so nothing failed, and a
 * run that recorded a failing outcome over an absent row would be inventing an
 * observation. These answer `unknown`, which refuses without emitting anything.
 */
const ABSENT_ROW_FINDINGS = Object.freeze([
  "predecessor_outcome_absent",
  "scheduler_dispatch_row_absent",
  "scheduler_observation_absent",
  "scheduler_service_row_absent",
]);

/** The one conclusion word that is green. Every other one propagates non-green. */
const GREEN_CONCLUSION = "success";

// ---------------------------------------------------------------------------
// THE BINDINGS. Module-private constants, every one of them.
// ---------------------------------------------------------------------------

/**
 * The three predecessors card 11 answers for. The fourth,
 * `step:scheduler-active-receipt`, is answered by card 12 instead: no Work
 * Request carries it, and `PREDECESSOR_WORK_REQUEST_REFS` in the reader maps it
 * to null on purpose. Derived from the frozen four rather than restated, so a
 * change to the plan's predecessor set moves this list with it.
 */
const OUTCOME_BACKED_PREDECESSORS = deepFreeze(
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.filter(step => step !== V5_A02_SCHEDULER_STEP_REF));

/**
 * THE GATE GRAPH A GATE ZERO RUN STANDS ON, and it is this module's own
 * constant rather than anything a caller could assemble. Each node names one
 * check this repository DECLARES — card 13's reader refuses any other name — and
 * the edges are what make non-green propagation a real clause rather than a
 * conjunction: `main-canary` reports over the two gates beneath it, so a green
 * canary above a failed `ops/ci.sh --strict` is refused as inherited non-green
 * rather than read as a pass.
 */
const V5_A02_GATE_ZERO_GATE_GRAPH = deepFreeze([
  { gate_id: "ops-ci-strict", check_name: "ops/ci.sh --strict", depends_on: [] },
  { gate_id: "local-db-ci-migration", check_name: "local-db-ci --class migration", depends_on: [] },
  {
    gate_id: "main-canary",
    check_name: "main canary",
    depends_on: ["local-db-ci-migration", "ops-ci-strict"],
  },
]);

/**
 * THE TTL THIS PRODUCER STAMPS, as a module constant. A Gate Zero outcome is the
 * zero of the v5 clock and everything downstream must be observed strictly after
 * it, so the window is the one this module chose and not one a caller asked for.
 */
const RECEIPT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * The comparator sentence, 5-300 characters, stating what this producer compared
 * rather than what it concluded.
 */
const COMPARATOR =
  "each of the three ruled evidence seams is compared against the clause it answers, and the three clauses are conjoined with inherited-refusal propagation across the declared gate graph";

// ---------------------------------------------------------------------------
// THE RUN BINDING, DERIVED — there is no line anybody pastes.
//
// Four addresses a Gate Zero run stands on, and each one is now READ from
// something that already exists rather than looked up by a human and typed here:
//
//   the four predecessor step refs  frozen literals already
//                                   (gate-zero-producer-registration.v5.js)
//   each predecessor's acceptance   read from the ruled record-layer store BY
//     receipt                       that step ref; card 11's reader takes the
//                                   step and no hash, and reads the receipt
//   the scheduler service           a module-private constant naming this
//                                   wrapper's own service, the way the reader's
//                                   own bin/run-scheduled.sh constant is
//   the canary run                  the LATEST scheduler-minted row for that
//                                   service, which the ledger answers itself
//   the head revision               the revision the running module was built
//                                   from, read from the repository this file
//                                   lives in — the same 40-hex value
//                                   bin/deploy-worker.sh stamps as GIT_SHA and
//                                   /release reports back
//   the subject maker               the committer of that revision, resolved to
//                                   an actor THROUGH identity.js, never typed
//
// So `gate_zero_run_binding_unnamed` now fires on ONE condition only: a row this
// derivation asked for is genuinely absent. Its reason text names which.
// ---------------------------------------------------------------------------

/**
 * THE SERVICE THIS PRODUCER'S CANARY RUNS UNDER. A module-private constant, and
 * the only one of the four addresses that is: the scheduler cannot be asked
 * which of its services is Gate Zero's, so this file names it, the same way
 * card 12's reader names `bin/run-scheduled.sh`. ops/config/services.json
 * carries the row; ops/launchd/com.carr.gate-zero-canary.plist runs it.
 */
const SCHEDULER_SERVICE_KEY = "gate-zero-canary";

const HEAD_REVISION = /^[0-9a-f]{40}$/;
const SERVICE_KEY = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const ACTOR_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const CANDIDATE_DIGEST = /^sha256:[0-9a-f]{64}$/;

// ---------------------------------------------------------------------------
// THE CANDIDATE IS BUILD-TIME METADATA, NOT A REPOSITORY READ (standing-rule
// amendment 9, 2026-09-14).
//
// WHAT WAS HERE, AND WHY IT COULD NOT WORK. Two hundred lines of this file
// walked up for a `.git` directory, read HEAD, resolved loose and packed refs,
// zlib-inflated the commit object, parsed the tree it named, and hashed the
// working-tree bytes at the paths that tree sealed. Every one of those is a read
// of a CHECKOUT, and the fifth review round's P0 is that THE DEPLOYED WORKER HAS
// NO CHECKOUT: Cloudflare bundles the modules wrangler was pointed at and serves
// them over a read-only virtual filesystem, and supplies no `.git` directory at
// all. In production that derivation had exactly one reachable answer —
// `gate_zero_candidate_metadata_absent` — and every suite that showed it working
// had staged a local git tree first, which is not the condition the code runs
// in. A test that can only pass where production cannot run is not evidence.
//
// WHAT IT READS NOW: THE STAMPS THE DEPLOY WRAPPER WROTE. bin/deploy-worker.sh
// runs mcp-server/bin/seal-candidate-manifest.mjs against the revision it is
// deploying — at build time, in a real checkout, where git is a fact — and
// stamps the result into the upload as Worker vars beside the GIT_SHA it has
// stamped since 2026-08-13:
//
//   GIT_SHA                    the revision, read here through the SAME function
//                              release.js reads it with (build-stamp.js), so the
//                              sha this receipt binds and the sha /release
//                              reports cannot become two different reads.
//   CANDIDATE_MANIFEST         the sealed manifest's own JCS text: the candidate
//                              tree id, the file count and byte length, the
//                              digest of the blob ids the revision sealed, the
//                              digest of those blobs' contents, and the
//                              environment-manifest and fixture-set digests.
//   CANDIDATE_MANIFEST_DIGEST  the digest of that manifest, computed by the same
//                              recipe and stamped separately.
//
// THE STAMPS CHECK EACH OTHER. The manifest is re-digested here and must equal
// the stamped digest, and its `git_sha` must equal the stamped sha — so a var
// edited by hand after the seal refuses rather than signs, and neither stamp can
// move without the other moving with it. `--var` is scoped to one wrangler
// invocation and `--keep-vars` defaults to false, so a deploy that bypasses the
// wrapper carries no stamps at all.
//
// AN ABSENT STAMP IS NAMED, NEVER FALLEN BACK FROM. There is no git path left in
// this module to fall back TO — no `node:fs`, no `node:zlib`, no repository walk
// — which is the property the no-git suite proves and the property its mutation
// control breaks by putting one back.
// ---------------------------------------------------------------------------

/**
 * The stamped build metadata, validated, or the names of the stamps that are
 * absent or do not agree. Reads the process environment and nothing else.
 */
function stampedCandidate() {
  const env = serverBuildEnvironment();
  const sha = stampedGitSha(env);
  const manifestText = stampedCandidateManifestText(env);
  const manifestDigest = stampedCandidateManifestDigest(env);

  const absent = [];
  if (typeof sha !== "string" || !HEAD_REVISION.test(sha))
    absent.push(BUILD_STAMP_NAMES.gitSha);
  if (typeof manifestText !== "string" || manifestText.length === 0)
    absent.push(BUILD_STAMP_NAMES.candidateManifest);
  if (typeof manifestDigest !== "string" || !CANDIDATE_DIGEST.test(manifestDigest))
    absent.push(BUILD_STAMP_NAMES.candidateManifestDigest);
  if (absent.length > 0) return { revision: null, manifest: null, absent };

  let manifest;
  try {
    manifest = JSON.parse(manifestText);
  } catch {
    return { revision: null, manifest: null,
      absent: [`${BUILD_STAMP_NAMES.candidateManifest}:not_json`] };
  }
  const wrong = [];
  if (!isPlainObject(manifest) || manifest.schema_version !== CANDIDATE_MANIFEST_SCHEMA) {
    wrong.push(`${BUILD_STAMP_NAMES.candidateManifest}:schema_version`);
  } else {
    for (const [field, held] of [
      ["git_sha", manifest.git_sha === sha],
      ["candidate_tree_id", HEAD_REVISION.test(String(manifest.candidate_tree_id))],
      ["file_count", Number.isInteger(manifest.file_count) && manifest.file_count > 0],
      ["byte_length", Number.isInteger(manifest.byte_length) && manifest.byte_length > 0],
      ["artifact_digest", CANDIDATE_DIGEST.test(String(manifest.artifact_digest))],
      ["source_digest", CANDIDATE_DIGEST.test(String(manifest.source_digest))],
      ["environment_manifest_digest",
        CANDIDATE_DIGEST.test(String(manifest.environment_manifest_digest))],
      ["fixture_set_digest", CANDIDATE_DIGEST.test(String(manifest.fixture_set_digest))],
    ]) if (!held) wrong.push(`${BUILD_STAMP_NAMES.candidateManifest}:${field}`);
    if (wrong.length === 0 && digest(manifest) !== manifestDigest)
      wrong.push(`${BUILD_STAMP_NAMES.candidateManifestDigest}:does_not_cover_the_stamped_manifest`);
  }
  if (wrong.length > 0) return { revision: null, manifest: null, absent: wrong };
  return { revision: sha, manifest: deepFreeze({ ...manifest }), absent: [] };
}

// ---------------------------------------------------------------------------
// THE IDENTITIES, DERIVED FROM THE AUTHENTICATED CALL.
//
// r7's identity rule: the gateway derives all identities from authenticated
// execution context, binds `producer_role` to the registry entry, and requires
// the subject maker's actor and session to differ from the evaluator's.
//
// THE FIRST REVIEW ROUND FOUND THIS FILE RECONSTRUCTING THEM. It read card 9's
// holder ref, took the actor slug out of it, and manufactured a session ref from
// that slug and a digest of what it had just read — so any process that imported
// this module received a receipt signed `codex-reviewer` without authenticating
// anything, and two runs over the same evidence shared a "session".
//
// WHAT IT DOES NOW. The module-private `authenticatedCaller()` below answers
// with the identity tools.js's verb dispatch bound to the async context for THIS
// call — actor, session and authority class, each derived in identity.js from an
// actor that file minted from a credential, with the session ref being
// correlation.js's per-request correlation id. Outside an authenticated call it
// answers null and this producer refuses; inside one established from a
// FABRICATED actor object it also answers null, because identity.js will not
// derive an identity for an object it did not mint. A test, a CLI probe and an
// unauthenticated import all take that path, which is the point: nothing that
// cannot authenticate can obtain a receipt.
//
// CARD 9 IS STILL LOAD-BEARING and is unchanged by this: the seat declaration is
// what BINDS the producer seam at all, so an unstaffed seat still turns the whole
// slice dark. What the seat no longer does is supply an identity. Naming who may
// sign and being the signer are different acts, which is the distinction the
// first round collapsed.
// ---------------------------------------------------------------------------

// AMENDMENT 2'S CLOSED SHAPE FOR AN EXPORTED CALLABLE comes from
// ./closed-callable.js, which is the ONE definition this module and identity.js
// now share. It used to be copied into both: the bodies still matched, but the
// clauses each copy documented had drifted, and a duplicated security primitive
// drifts in behaviour immediately after it drifts in prose. The Gate Zero seam
// modules keep their own local copies on purpose — each of those is held to
// being self-contained and one asserts by source that it holds exactly one
// definition — so the unification is exactly the two copies that were free to
// diverge.

/**
 * The authority classes r7's registry admits for an independent control-plane
 * oracle. DERIVED classes only — identity.js computes the class, this module
 * checks membership, and a class outside the set refuses rather than signs.
 */
const PRODUCER_AUTHORITY_CLASSES = Object.freeze(["review_agent"]);

const SESSION_REF = /^session:[a-z0-9][a-z0-9:._/-]{8,199}$/;

/**
 * WHO IS CALLING — the module-private accessor, and it is module-private on
 * purpose. It is not exported, so nothing outside this file can reach it; it
 * takes no argument, so nothing can hand it an identity; and what it reads is
 * the async context tools.js's verb dispatch entered, whose stored value
 * identity.js DERIVED from an actor identity.js itself minted from a credential.
 * An object assembled anywhere else enters that scope as null, so a fabricated
 * actor reaches this function as "no authenticated call" and obtains no receipt.
 *
 * Answers a validated `authenticated-receipt-identity.v1` or null. The shape is
 * re-checked here rather than trusted across the module boundary.
 */
function authenticatedCaller() {
  const identity = authenticatedIdentity.receiptIdentity();
  if (identity === null || typeof identity !== "object") return null;
  if (typeof identity.actor_id !== "string" || !ACTOR_ID.test(identity.actor_id)) return null;
  if (typeof identity.session_ref !== "string" || !SESSION_REF.test(identity.session_ref))
    return null;
  return identity;
}

/**
 * THE STORE THIS PRODUCER READS ITS SUBJECT MAKER OUT OF, and the ONE store ref
 * it will accept an answer from. A store that states it is another store is a
 * store this derivation did not ask, whatever rows came back with it.
 */
const CANDIDATE_BUILD_RECORD_STORE_REF = "control-plane:ops.candidate-build-record";

/** The uuid shape the ops recorder stamps as a release row's correlation. */
const CANDIDATE_BUILD_CORRELATION_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * THE SUBJECT MAKER IS THE RELEASE-CANDIDATE RECORD FOR THE STAMPED REVISION —
 * standing-rule amendment 9(b), 2026-09-14, and the third shape this field has
 * had.
 *
 * THE TWO THAT WERE WRONG, because the wrongness is the whole reason this one is
 * shaped as it is. The first draft took the maker from card 9's SEAT
 * DECLARATION: a constant in a source file, authenticating nobody. The third
 * round replaced it with HEAD's own committer address through a frozen table —
 * better, and still not authentication: a committer line is an attribution
 * anybody with a git config can write, and the session half of the identity was
 * then MANUFACTURED, first here out of the revision and then one file over by
 * suffixing the evaluator's own session with `:candidate-build`. The fifth round
 * put it exactly: "this relabels the reviewer's authenticated session as the
 * subject-maker's build session; it is not authenticated provenance for the
 * person who made the candidate."
 *
 * WHAT IT IS NOW. `tools/ops-record.py release candidate` files one row in
 * ops.release per build, over an authenticated authority connection, carrying
 * the revision, the maker and the correlation the recorder stamped. This reads
 * that row back for the revision the deploy wrapper stamped into this Worker —
 * so BOTH HALVES of the identity come out of a record somebody had to be
 * authenticated to write, and neither is assembled from the call that is doing
 * the judging.
 *
 * THE ROWS ARE READ, NOT TRUSTED. Exactly one maker may answer: rows for the
 * revision that disagree about who made it are an ambiguous record and refuse,
 * because a receipt that picked one of two makers would be picking. The slug
 * must then be one identity.js REGISTERS as a partner — the store already
 * refuses anything that is not slug-shaped, and this refuses anything the
 * partner registry does not know — so the widest value this path can put in a
 * receipt is a registered partner's name.
 *
 * NO ROW IS A REFUSAL, NOT A DEFAULT. A revision with no release candidate on
 * record is a build nobody filed for, and this oracle does not judge one:
 * `gate_zero_run_binding_unnamed` answers with `subject_maker` named, which is
 * the same derived, honest absence an unreadable store gets.
 */
async function subjectMakerIdentityFor(revision) {
  let answered;
  try {
    answered = await fetchCandidateBuildRecordRows({ gitSha: revision });
  } catch (cause) {
    // The store's own refusal and any other throw reach the same answer: this
    // run could not read the record, so it has no maker. isSeamStoreUnreachable
    // is asked so the two are distinguishable in a debugger and identical here.
    void isSeamStoreUnreachable(cause);
    return null;
  }
  if (answered === null || typeof answered !== "object") return null;
  if (answered.store_ref !== CANDIDATE_BUILD_RECORD_STORE_REF) return null;
  const rows = Array.isArray(answered.rows) ? answered.rows : [];

  const makers = new Set();
  const sessions = new Set();
  for (const row of rows) {
    if (!isPlainObject(row)) return null;
    if (typeof row.maker_actor !== "string" || !ACTOR_ID.test(row.maker_actor)) continue;
    if (typeof row.correlation_id !== "string" || !CANDIDATE_BUILD_CORRELATION_ID.test(row.correlation_id))
      continue;
    makers.add(row.maker_actor);
    sessions.add(row.correlation_id);
  }
  if (makers.size !== 1 || sessions.size !== 1) return null;

  const [makerSlug] = [...makers];
  const [correlationId] = [...sessions];
  const named = authenticatedIdentity.partnerIdentity(makerSlug);
  if (named === null) return null;
  if (typeof named.actor_id !== "string" || !ACTOR_ID.test(named.actor_id)) return null;
  const sessionRef = `session:${correlationId}`;
  if (!SESSION_REF.test(sessionRef)) return null;
  return deepFreeze({
    actor_id: named.actor_id,
    session_ref: sessionRef,
    authority_class: named.authority_class,
  });
}

/** Two seats collide when they share EITHER the actor or the session. */
function sameSeat(a, b) {
  return a.actor_id === b.actor_id || a.session_ref === b.session_ref;
}

// ---------------------------------------------------------------------------
// THE THREE CLAUSES, IN PRODUCTION FORM.
//
// These are V5-A02's checkable_done clauses, promoted out of the conditional.
// The test-side clause helper answers in the conditional about a CALLER'S SHAPE,
// and it has to, because a caller's shape is not evidence. These answer `held`,
// `failed` or `unknown` about a ROW read by a ruled reader from a ruled store —
// which is the whole difference, and it is why the conditional mood does not
// appear on this side of the line in any spelling.
//
// Every one of them is PURE over the readings handed to it: no store, no clock,
// no environment. The reading is fetched by `aim()` below and by nothing else.
// ---------------------------------------------------------------------------

function clause(state, reasonId, detail) {
  return deepFreeze({ state, reason_id: reasonId === null ? null : reason(reasonId), detail });
}

/**
 * CLAUSE 1 — the four bound predecessors join exactly.
 *
 * Three of them are Work-Request outcomes read through card 11 and must each
 * answer with the one finding that means "accepted, complete, and the acceptance
 * receipt's own hash is the hash this run was bound to". The fourth is the
 * scheduler receipt, which card 12 answers and clause 2 judges.
 *
 * EXACTLY means exactly: each predecessor present once, each read from the ruled
 * store, and NO TWO OF THEM ANSWERED BY THE SAME WORK REQUEST — one outcome
 * record cannot close two predecessors, and the reader hands back the Work
 * Request ref out of its own frozen table, so the check is on a value no caller
 * chose.
 */
function predecessorClause(readings) {
  const seenWorkRequests = new Map();
  const per = {};
  for (const step of OUTCOME_BACKED_PREDECESSORS) {
    const answered = readings[step];
    if (!isPlainObject(answered) || !Object.hasOwn(answered, "card_ref")) {
      per[step] = UNKNOWN;
      continue;
    }
    if (ABSENT_ROW_FINDINGS.includes(answered.finding) || answered.finding === null) {
      per[step] = UNKNOWN;
      continue;
    }
    if (answered.finding !== PREDECESSOR_HELD_FINDING || answered.hash_match !== HELD) {
      per[step] = FAILED;
      continue;
    }
    const workRequest = answered.work_request_ref;
    if (typeof workRequest !== "string" || seenWorkRequests.has(workRequest)) {
      per[step] = FAILED;
      continue;
    }
    seenWorkRequests.set(workRequest, step);
    per[step] = HELD;
  }
  const unknown = OUTCOME_BACKED_PREDECESSORS.filter(step => per[step] === UNKNOWN);
  const failed = OUTCOME_BACKED_PREDECESSORS.filter(step => per[step] === FAILED);
  if (unknown.length > 0)
    return clause(UNKNOWN, "gate_zero_evidence_unavailable", { per_predecessor: per, not_answered: unknown });
  if (failed.length > 0)
    return clause(FAILED, "gate_zero_predecessor_clause_failed", { per_predecessor: per, failed });
  return clause(HELD, null, { per_predecessor: per, failed: [] });
}

/**
 * CLAUSE 2 — the scheduler canary and its readback join exactly, and the canary
 * is bound to the accepted scheduler-active receipt.
 *
 * Card 12's reader reports the three sub-clauses separately, so this reads all
 * three rather than only the finding: a canary bound to no receipt, an
 * observation at or before its own dispatch, and an observation of another
 * canary each fail on their own name.
 */
function schedulerClause(answered) {
  if (!isPlainObject(answered) || !Object.hasOwn(answered, "card_ref"))
    return clause(UNKNOWN, "gate_zero_evidence_unavailable", { sub_clauses: null });
  const sub = deepFreeze({
    receipt_binding: answered.receipt_binding ?? UNKNOWN,
    observation_after_dispatch: answered.observation_after_dispatch ?? UNKNOWN,
    canary_match: answered.canary_match ?? UNKNOWN,
  });
  if (ABSENT_ROW_FINDINGS.includes(answered.finding) || answered.finding === null)
    return clause(UNKNOWN, "gate_zero_evidence_unavailable", { sub_clauses: sub });
  const allHeld = sub.receipt_binding === HELD
    && sub.observation_after_dispatch === HELD && sub.canary_match === HELD;
  if (answered.finding !== SCHEDULER_HELD_FINDING || !allHeld)
    return clause(FAILED, "gate_zero_scheduler_clause_failed", { sub_clauses: sub });
  return clause(HELD, null, { sub_clauses: sub });
}

/**
 * CLAUSE 3 — a failed injected gate cannot claim green.
 *
 * A gate is green when its OWN conclusion is `success` AND every gate it
 * descends from is green. The second half is what makes an injected failure
 * impossible to claim past, and it is transitive rather than one hop deep. An
 * empty graph is not green: a Gate Zero that read no gate read nothing.
 */
function gateGraphClause(readings) {
  const conclusions = new Map();
  for (const node of V5_A02_GATE_ZERO_GATE_GRAPH) {
    const answered = readings[node.gate_id];
    if (!isPlainObject(answered) || !Object.hasOwn(answered, "card_ref")) continue;
    if (answered.finding !== CONCLUSION_READ_FINDING) continue;
    if (typeof answered.conclusion !== "string") continue;
    conclusions.set(node.gate_id, answered.conclusion);
  }
  const unknown = V5_A02_GATE_ZERO_GATE_GRAPH
    .filter(node => !conclusions.has(node.gate_id)).map(node => node.gate_id);
  if (unknown.length > 0)
    return clause(UNKNOWN, "gate_zero_evidence_unavailable",
      { not_answered: unknown, conclusion_not_success: [], inherited_not_success: [] });

  const byId = new Map(V5_A02_GATE_ZERO_GATE_GRAPH.map(node => [node.gate_id, node]));
  const memo = new Map();
  const isGreen = (gateId) => {
    if (memo.has(gateId)) return memo.get(gateId);
    const node = byId.get(gateId);
    // A gate this graph does not contain is a gate nothing answered for, and
    // unanswered is never
    // green. Pessimistic while the walk is open, so a cycle cannot answer green.
    if (node === undefined) { memo.set(gateId, false); return false; }
    memo.set(gateId, false);
    const green = conclusions.get(gateId) === GREEN_CONCLUSION && node.depends_on.every(isGreen);
    memo.set(gateId, green);
    return green;
  };

  const notSucceeding = [...byId.keys()].filter(id => conclusions.get(id) !== GREEN_CONCLUSION).sort();
  const inherited = [...byId.keys()]
    .filter(id => conclusions.get(id) === GREEN_CONCLUSION && !isGreen(id)).sort();
  const allGreen = byId.size > 0 && [...byId.keys()].every(isGreen);
  const detail = {
    not_answered: [], conclusion_not_success: notSucceeding, inherited_not_success: inherited,
  };
  if (!allGreen) return clause(FAILED, "gate_zero_gate_graph_clause_failed", detail);
  return clause(HELD, null, detail);
}

// ---------------------------------------------------------------------------
// NEGATIVE ADMISSION, EARNED RATHER THAN WRITTEN.
//
// `consumer-gate-receipt.v1` requires `negative_admission_result`, whose one
// legal value claims that every required denial was OBSERVED. Q036.D1 is
// explicit that a Gate Zero run must watch its injected failures actually being
// denied, not merely watch its passes pass — "any missed finding, false-green
// result, unbounded loop, or unproven recovery fails".
//
// So this module does what benchmark-minimum.v5.js does for the same field: it
// takes the readings it actually holds, mutates each in a required way, and
// requires ITS OWN live clause to refuse with the exact expected reason. A
// module that simply wrote the string would be self-attesting the property the
// field exists to prevent. If any case is not denied, production refuses.
// ---------------------------------------------------------------------------

const NEGATIVE_ADMISSION_RESULT = "all_required_denials_observed";

/** One mutation, and the reason the live clause must answer with. */
const REQUIRED_DENIALS = deepFreeze([
  "predecessor_not_accepted",
  "predecessor_acceptance_receipt_hash_mismatch",
  "one_outcome_record_closing_two_predecessors",
  "scheduler_canary_not_bound_to_its_receipt",
  "scheduler_observation_not_after_dispatch",
  "gate_conclusion_not_success",
  "gate_reporting_success_over_a_failed_ancestor",
]);

function withoutKey(reading, key, value) {
  return { ...reading, [key]: value };
}

/**
 * Every required denial, re-derived from the live clauses over the readings this
 * run actually took. Returns the proof, or the first case that was NOT denied.
 */
function proveNegativeAdmission(predecessorReadings, schedulerReading, conclusionReadings) {
  const observed = [];
  const record = (name, answer, expected) => {
    if (answer.state === HELD || answer.reason_id !== expected) return name;
    observed.push(name);
    return null;
  };

  const firstStep = OUTCOME_BACKED_PREDECESSORS[0];
  const secondStep = OUTCOME_BACKED_PREDECESSORS[1];

  const notAccepted = {
    ...predecessorReadings,
    [firstStep]: withoutKey(predecessorReadings[firstStep], "finding", "predecessor_outcome_not_accepted"),
  };
  const hashMismatch = {
    ...predecessorReadings,
    [firstStep]: withoutKey(predecessorReadings[firstStep], "hash_match", FAILED),
  };
  const doubleDuty = {
    ...predecessorReadings,
    [secondStep]: withoutKey(predecessorReadings[secondStep], "work_request_ref",
      predecessorReadings[firstStep]?.work_request_ref),
  };
  const unbound = withoutKey(schedulerReading, "receipt_binding", FAILED);
  const notAfter = withoutKey(
    withoutKey(schedulerReading, "observation_after_dispatch", FAILED),
    "finding", "scheduler_observation_not_after_dispatch");
  const firstGate = V5_A02_GATE_ZERO_GATE_GRAPH[0].gate_id;
  const topGate = V5_A02_GATE_ZERO_GATE_GRAPH[V5_A02_GATE_ZERO_GATE_GRAPH.length - 1].gate_id;
  const notGreen = {
    ...conclusionReadings,
    [topGate]: withoutKey(conclusionReadings[topGate], "conclusion", "failure"),
  };
  const greenOverFailure = {
    ...conclusionReadings,
    [firstGate]: withoutKey(conclusionReadings[firstGate], "conclusion", "failure"),
  };

  const missed = [
    record("predecessor_not_accepted",
      predecessorClause(notAccepted), "gate_zero_predecessor_clause_failed"),
    record("predecessor_acceptance_receipt_hash_mismatch",
      predecessorClause(hashMismatch), "gate_zero_predecessor_clause_failed"),
    record("one_outcome_record_closing_two_predecessors",
      predecessorClause(doubleDuty), "gate_zero_predecessor_clause_failed"),
    record("scheduler_canary_not_bound_to_its_receipt",
      schedulerClause(unbound), "gate_zero_scheduler_clause_failed"),
    record("scheduler_observation_not_after_dispatch",
      schedulerClause(notAfter), "gate_zero_scheduler_clause_failed"),
    record("gate_conclusion_not_success",
      gateGraphClause(notGreen), "gate_zero_gate_graph_clause_failed"),
    // THE ONE A CONJUNCTION WOULD MISS. The top gate still reports success; its
    // ancestor does not. A clause that only conjoined self-conclusions would
    // call this green.
    record("gate_reporting_success_over_a_failed_ancestor",
      gateGraphClause(greenOverFailure), "gate_zero_gate_graph_clause_failed"),
  ].filter(one => one !== null);

  return deepFreeze({
    result: missed.length === 0 ? NEGATIVE_ADMISSION_RESULT : null,
    case_count: REQUIRED_DENIALS.length,
    observed_codes: observed.sort(),
    missed_cases: missed.sort(),
  });
}

// ---------------------------------------------------------------------------
// AIMING THE THREE READERS.
// ---------------------------------------------------------------------------

/**
 * Every reading this run stands on, taken from the bound readers, at the
 * addresses the module-private binding names and at no others.
 *
 * The readers take an ADDRESS and no authority. What is handed to each one here
 * is built entirely out of this module's own constants: a step ref out of the
 * frozen predecessor list, an acceptance-receipt hash off the pasted binding, a
 * service and run key off the pasted binding, the pasted head revision, and a
 * check name out of this module's own gate graph — which card 13 refuses unless
 * a workflow in this repository declares it.
 */
async function aim(binding) {
  const predecessors = {};
  for (const step of OUTCOME_BACKED_PREDECESSORS) {
    // THE STEP REF AND NOTHING ELSE. Card 11 reads the acceptance receipt the
    // ruled store holds for this step's Work Request; no hash goes in, so no
    // hash had to be looked up by a human first.
    predecessors[step] = await readPredecessorOutcomeEvidence({ stepRef: step });
  }
  // THE SERVICE AND NOTHING ELSE. Card 12 reads the latest run its own wrapper
  // minted a receipt for, which is the canary a Gate Zero run stands on.
  const scheduler = await readSchedulerCanaryEvidence({ serviceKey: binding.scheduler_service_key });
  const conclusions = {};
  for (const node of V5_A02_GATE_ZERO_GATE_GRAPH) {
    conclusions[node.gate_id] = await readGateConclusionEvidence({
      headSha: binding.head_revision,
      checkName: node.check_name,
    });
  }
  return { predecessors, scheduler, conclusions };
}

/**
 * The digest of WHAT WAS READ, and it carries no store text: every reader answer
 * already reduces the store's own words to digests, counts and constants out of
 * the reader's frozen tables, so what is hashed here is a reading rather than a
 * transcript. It becomes the evidence ref and the session refs, so an identity
 * and an address both move when the evidence does.
 */
function evidenceDigestOf(read) {
  return digest(canonicalJson({
    predecessors: Object.fromEntries(OUTCOME_BACKED_PREDECESSORS
      .map(step => [step, digest(read.predecessors[step] ?? null)])),
    scheduler: digest(read.scheduler ?? null),
    conclusions: Object.fromEntries(V5_A02_GATE_ZERO_GATE_GRAPH
      .map(node => [node.gate_id, digest(read.conclusions[node.gate_id] ?? null)])),
  }));
}

// ---------------------------------------------------------------------------
// THE FIVE DIGESTS THE RECEIPT STANDS ON — AND THREE OF THEM ARE OVER BYTES.
//
// THE FIRST REVIEW ROUND FOUND THREE OF THESE HASHING A DESCRIPTION. The
// candidate digest hashed a revision string beside a list of check names; the
// environment digest hashed registration metadata; the fixture digest hashed the
// addresses the readers were aimed at. Every one of them had the right shape and
// bound nothing: change the artifact, and the digest did not move.
//
// WHAT EACH IS NOW:
//
//   subject_digest      the GATE's identity — the one that is legitimately a
//                       constant, because the subject of a Gate Zero receipt is
//                       Gate Zero and does not move with the candidate.
//   candidate_digest    the SEALED ARTIFACT MANIFEST for the head revision, by
//                       artifact-trust.js's own artifactManifestDigest: JCS
//                       SHA-256 over `scac-artifact-manifest.v1`'s eleven
//                       fields, which is byte-for-byte the recipe
//                       ops.scac_artifact_manifest_digest recomputes in the
//                       database and the recipe the release manifest binds. Its
//                       artifact and source digests are taken over the CANDIDATE
//                       TREE'S ACTUAL BYTES, so one byte changed in one module
//                       moves this digest.
//   policy_digest       this module's own sealed policy — the clause states, the
//                       reason registry, the gate graph, the required denials,
//                       the TTL and the stated digest recipe.
//   environment_        JCS SHA-256 over ops/config/environments.json, READ AS
//     manifest_digest   BYTES and parsed. That file is the environment matrix
//                       ops/environment-matrix-selftest.py holds the repository
//                       to; one byte changed in it moves this digest.
//   fixture_set_digest  SHA-256 over the SEALED FIXTURE SET'S BYTES, file by
//                       file, under their repository-relative paths. The
//                       receipt's own `evidence_scope` is `candidate-and-test`,
//                       so the test material is inside its scope by r7's own
//                       registration rather than by this module's choice.
//
// AN ARTIFACT THIS DERIVATION CANNOT READ IS AN ABSENT ROW, not a zero and not a
// default: every one of these answers null upward and the producer refuses
// naming which artifact it could not reach.
// ---------------------------------------------------------------------------

function subjectDigestOf() {
  return digest({
    subject: "gate-zero",
    gate_id: V5_A02_GATE_ZERO_GATE_ID,
    step_ref: GATE_ZERO_STEP_REF,
    predecessor_step_refs: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
    oracle_ref: V5_A02_GATE_ZERO_ORACLE_REF,
    oracle_version: V5_A02_GATE_ZERO_ORACLE_VERSION,
  });
}

/**
 * THE SOURCE-BUNDLE MANIFEST FOR THE STAMPED REVISION, assembled from the sealed
 * manifest the deploy wrapper stamped and from nothing else.
 *
 * `source_digest` is what the revision SEALED: git's own blob id for every
 * candidate path in that revision's tree. `artifact_digest` is what those blobs
 * CONTAIN: sha256 per path, over the same paths. `source_ref` is the revision and
 * `provenance_digest` carries the candidate tree id and the file count beside it.
 *
 * BOTH HALVES ARE READ AT ONE REVISION NOW, and that is stronger than the shape
 * it replaces rather than weaker. The previous producer read the sealed ids from
 * HEAD and the bytes from the WORKING TREE precisely so a dirty checkout would
 * show as a mismatch — a check the deployed Worker could never run, because it
 * has neither. The wrapper's sealer reads ids and contents from the object store
 * at one revision, so there is no second reading to disagree, and the question
 * the old comparison asked — is this checkout what it claims to be — is answered
 * where it belongs: bin/deploy-worker.sh refuses a dirty tree before it uploads.
 *
 * ONE BYTE STILL MOVES THIS DIGEST. Change a candidate file, and its blob id and
 * its content hash both move, so the sealed manifest moves, so the stamp moves,
 * so this moves. mcp-server/test/gate-zero-candidate-seal.test.mjs proves that
 * over a real repository rather than asserting it here.
 */
function candidateManifestOf(manifest, policyDigest) {
  return {
    artifact_digest: manifest.artifact_digest,
    artifact_kind: "source_bundle",
    media_type: "application/vnd.carr.source-bundle+json",
    byte_length: manifest.byte_length,
    source_ref: manifest.git_sha,
    source_digest: manifest.source_digest,
    sbom_digest: null,
    provenance_digest: digest({
      head_revision: manifest.git_sha,
      head_tree_id: manifest.candidate_tree_id,
      file_count: manifest.file_count }),
    policy_epoch: 1,
    policy_epoch_digest: policyDigest,
  };
}

function policyDigestOf() {
  return digest({
    schema_version: V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION,
    clause_states: [...V5_A02_GATE_ZERO_CLAUSE_STATES],
    reason_ids: [...V5_A02_GATE_ZERO_PRODUCER_REASON_IDS],
    gate_graph: V5_A02_GATE_ZERO_GATE_GRAPH.map(node =>
      ({ gate_id: node.gate_id, depends_on: [...node.depends_on] })),
    required_denials: [...REQUIRED_DENIALS],
    receipt_ttl_ms: RECEIPT_TTL_MS,
    digest_recipe: V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE,
  });
}

// ---------------------------------------------------------------------------
// THE ANSWER SHAPES.
// ---------------------------------------------------------------------------

function refusal(reasonId, because, extra) {
  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    run_binding_status: "derived",
    status: "unavailable",
    decision: "refuse",
    reason_id: reason(reasonId),
    unavailable_because: because,
    outcome_digest: null,
    observed_at: null,
    receipt: null,
    request_read: false,
    caller_evidence_admitted: false,
    model_judgment_admitted: false,
    decided_by: "ruled_store_rows",
    effects: V5_NO_EFFECTS,
    ...extra,
  });
}

// ---------------------------------------------------------------------------
// THE PRODUCER.
// ---------------------------------------------------------------------------

/**
 * ONE MODULE-PRIVATE PRODUCER. It takes nothing, reads nothing a caller
 * controls, and either refuses with a derived reason or returns one
 * `consumer-gate-receipt.v1` with its digest and the instant it was observed.
 */
async function produce() {
  // (1) WHO IS CALLING, and it is the first question because an unauthenticated
  // invocation must not reach a store, let alone a receipt.
  const caller = authenticatedCaller();
  if (caller === null)
    return refusal("gate_zero_producer_identity_refused",
      "this module was not called inside an authenticated call, so there is no execution context to derive a producer identity from",
      { clauses: null, negative_admission: null });
  if (!PRODUCER_AUTHORITY_CLASSES.includes(caller.authority_class))
    return refusal("gate_zero_producer_identity_refused",
      "the authenticated call's derived authority class is not one r7's registry admits for this independent oracle",
      { clauses: null, negative_admission: null });

  // (2) WHAT THIS RUN STANDS ON, derived — and each KIND of absence answers with
  // its own reason rather than all of them saying "a row is missing".
  //
  // (2a) THE BUILD STAMPS. An absent or disagreeing stamp is not an absent row:
  // it is this deploy being unable to say what candidate it IS. It is named
  // exactly — which var, and which field of the manifest — and there is nothing
  // to fall back to, because the repository this module used to read at request
  // time does not exist where this module runs.
  const stamped = stampedCandidate();
  if (stamped.absent.length > 0)
    return refusal("gate_zero_candidate_metadata_absent",
      `this deploy carries no usable candidate stamp: ${stamped.absent.join(", ")}`,
      { clauses: null, negative_admission: null, absent_candidate_metadata: stamped.absent });

  const headRevision = stamped.revision;
  const manifest = stamped.manifest;
  const policyDigest = policyDigestOf();
  // (2b) THE SEALED ARTIFACTS the receipt's other two digests stand on. They are
  // sealed in the same manifest, by the same wrapper, at the same revision — so
  // "absent" here means the stamped manifest did not carry them, which
  // `stampedCandidate` has already refused above. They are read out rather than
  // recomputed: this process cannot see ops/config/environments.json or the
  // sealed fixture bytes, and a digest it cannot take is not one it may invent.
  const environmentDigest = manifest.environment_manifest_digest;
  const fixtureDigest = manifest.fixture_set_digest;

  // (2c) THE RULED ROW. The subject maker is the release-candidate record the
  // deploy wrapper filed for this exact revision, read back through the Control
  // Plane store; a revision with no such record — or one whose rows disagree
  // about who made it — is a genuinely absent row, and
  // `gate_zero_run_binding_unnamed` says which.
  const subjectMakerIdentity = await subjectMakerIdentityFor(headRevision);
  const unnamed = [
    ["subject_maker", subjectMakerIdentity !== null],
    ["scheduler_service_key", SERVICE_KEY.test(SCHEDULER_SERVICE_KEY)],
  ].filter(([, held]) => !held).map(([name]) => name);
  if (unnamed.length > 0)
    return refusal("gate_zero_run_binding_unnamed",
      `a ruled row this run's binding derives from is absent: ${unnamed.join(", ")}`,
      { clauses: null, negative_admission: null, unnamed_bindings: unnamed });

  const candidateManifest = candidateManifestOf(manifest, policyDigest);

  const binding = deepFreeze({
    head_revision: headRevision,
    scheduler_service_key: SCHEDULER_SERVICE_KEY,
    subject_maker_actor_id: subjectMakerIdentity.actor_id,
  });

  // (3) THE THREE IDENTITIES. Producer and evaluator are the authenticated
  // call; the subject maker is HEAD's own committer, resolved through the same
  // identity.js derivation. r7's rule is checked on BOTH, because a producer
  // that is the maker attests to its own work exactly as much as an evaluator
  // that is.
  const producerIdentity = deepFreeze({ ...caller });
  const evaluatorIdentity = producerIdentity;
  for (const other of [producerIdentity, evaluatorIdentity])
    if (sameSeat(subjectMakerIdentity, other))
      return refusal("gate_zero_producer_identity_refused",
        "the seat that built the candidate and the seat that reviews it are the same seat",
        { clauses: null, negative_admission: null });

  const read = await aim(binding);
  const evidenceDigest = evidenceDigestOf(read);

  const clauses = deepFreeze({
    predecessor_join: predecessorClause(read.predecessors),
    scheduler_canary: schedulerClause(read.scheduler),
    gate_graph: gateGraphClause(read.conclusions),
  });
  const ordered = [clauses.predecessor_join, clauses.scheduler_canary, clauses.gate_graph];

  // NOT ANSWERED IS NOT THE SAME AS NOT HELD, and the order here is the whole
  // difference. A clause with no row behind it has not failed — nothing was
  // read, so there is no outcome to emit and no failure to record either. That
  // refuses, before the negative admission is even attempted: a falsifier run
  // over readings that do not exist proves nothing about anything.
  const unknown = ordered.find(one => one.state === UNKNOWN) ?? null;
  if (unknown !== null)
    return refusal("gate_zero_evidence_unavailable",
      "a ruled evidence seam returned no row for the address this run derived",
      { clauses, negative_admission: null });

  // The negative admission is proved over the readings this run actually took,
  // and it is proved BEFORE any conclusion is drawn from them — a run whose own
  // falsifiers do not fire has not established that its passes mean anything.
  const negativeAdmission = proveNegativeAdmission(
    read.predecessors, read.scheduler, read.conclusions);
  if (negativeAdmission.result !== NEGATIVE_ADMISSION_RESULT)
    return refusal("gate_zero_negative_admission_unproved",
      "at least one required denial was not observed, so this run cannot claim its injected failures were denied",
      { clauses, negative_admission: negativeAdmission });

  // A CLAUSE THAT DID NOT HOLD IS AN OUTCOME, NOT A REFUSAL, and this is what
  // the retry policy already said in its own words: retryable, every run kept,
  // failed runs retained. A Gate Zero run that read all three seams and found a
  // failed gate has established something — it must be recorded, with the same
  // twenty-one fields and the same digest as a passing one, and `status: "fail"`
  // on it. Refusing instead would throw away the one observation Q036.D1 exists
  // to demand: that an injected failure propagates rather than disappearing.
  const failed = ordered.find(one => one.state === FAILED) ?? null;

  const observedAt = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const expiresAt = new Date(Date.parse(observedAt) + RECEIPT_TTL_MS)
    .toISOString().replace(/\.\d{3}Z$/, "Z");
  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;

  const receipt = deepFreeze({
    gate_id: V5_A02_GATE_ZERO_GATE_ID,
    receipt_producer_step_ref: GATE_ZERO_STEP_REF,
    subject_digest: subjectDigestOf(),
    candidate_digest: artifactManifestDigest(candidateManifest),
    policy_digest: policyDigest,
    environment_manifest_digest: environmentDigest,
    subject_environment: entry.subject_environment,
    evidence_scope: entry.evidence_scope,
    subject_maker_identity: subjectMakerIdentity,
    producer_identity: producerIdentity,
    evaluator_identity: evaluatorIdentity,
    producer_role: entry.producer_role,
    independent_oracle_ref: entry.oracle_ref,
    oracle_version: entry.oracle_version,
    evidence_ref: `safe:gate-zero/evidence/${evidenceDigest.slice("sha256:".length)}`,
    fixture_set_digest: fixtureDigest,
    observed_at: observedAt,
    ttl_expires_at: expiresAt,
    // THE VERDICT, AND IT IS THE RECEIPT'S RATHER THAN THE GATE'S. r7's closed
    // set is pass/fail/unknown/stale/quarantined, and a clause that did not hold
    // makes this "fail" — a real, kept, retryable outcome that no consumer gate
    // will accept as a member, rather than an absence.
    status: failed === null ? "pass" : "fail",
    comparator: COMPARATOR,
    negative_admission_result: negativeAdmission.result,
  });

  // The twenty-one fields, counted rather than trusted: a field added or dropped
  // by a later edit refuses here instead of reaching a consumer.
  const fields = Object.keys(receipt).sort();
  const required = [...CONSUMER_GATE_RECEIPT_FIELDS].sort();
  if (fields.length !== required.length || fields.some((name, at) => name !== required[at]))
    fail("consumer_gate_receipt_fields_drifted",
      "the assembled receipt is not consumer-gate-receipt.v1's closed field set",
      { assembled: fields.length, required: required.length });

  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    run_binding_status: "derived",
    status: "outcome_produced",
    decision: "report",
    reason_id: failed === null ? null : failed.reason_id,
    unavailable_because: failed === null ? null
      : "a clause this run stands on was not held by the rows the ruled evidence seams returned",
    // THE DIGEST, by the recipe r7 declares: JCS SHA-256 over the two-element
    // array ["consumer-gate-receipt.v1", <the twenty-one-field receipt>]. The
    // rule's closing clause still holds — the digest is not one of the fields it
    // covers. This is the value the record layer independently recomputes with
    // ops.gate_zero_outcome_digest(); the two preimages are byte-identical, and
    // the gateway refuses rather than reports if they ever diverge.
    outcome_digest: digest([CONSUMER_GATE_RECEIPT_SCHEMA, receipt]),
    outcome_digest_recipe: V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE,
    observed_at: observedAt,
    receipt,
    receipt_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA,
    clauses,
    negative_admission: negativeAdmission,
    request_read: false,
    caller_evidence_admitted: false,
    model_judgment_admitted: false,
    decided_by: "ruled_store_rows",
    // STEP A EMITS A VALUE. Recording it as a row is Step B, it is the heavy
    // path, and the authority that may write it is Joe's to rule.
    persisted: false,
    durable_outcome_record_required: true,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * THE ONE CALLABLE EXPORT, AND ITS ARITY IS THE BOUNDARY.
 *
 * Zero parameters. There is no evidence argument, no binding argument and no
 * reader argument, so there is nothing for a caller to smuggle authority
 * through. Handing it one is a CONTRACT VIOLATION and throws synchronously,
 * before any store is opened — a policy answer is for policy questions, and
 * "you passed me an argument" is not one.
 *
 * The gate binds this as its producer seam's `emitOutcome`. Nothing else in this
 * repository may call it, and nothing that calls it can change what it answers.
 */
export const v5A02GateZeroEmitOutcome = closedCallable((...received) => {
  if (received.length > 0)
    fail("gate_zero_producer_takes_no_argument",
      "the Gate Zero producer is bound by this module and reads no caller input",
      { arguments_received: received.length });
  return produce();
});
