// V5-A02, the seam half — the three evidence readers Gate Zero is owed, proved
// in four parts that must not be confused with each other.
//
// PART A, THE RULING GATE, WHICH JOE OPENED ON 2026-09-11. Cards 11, 12 and 13
// are ruled: three decision ids are pasted onto the three `decision_id:` lines,
// and this part asks both halves of what that means.
//
//   RULED, of the SHIPPED modules. Each reader names its OWN card's decision id
//   and its own card's store — three distinct ids, so a reader that read another
//   card's line fails on the id rather than on the row it went on to read — and
//   each one STILL REFUSES, because a ruling says where to read and nothing more.
//   No store is reachable in this suite: no credential is set, so the real store
//   module refuses, and the refusal it gives is the observable proof that the
//   only thing the paste changed is that the reader now goes and looks.
//
//   UNRULED, on a staged copy whose three lines are back to null. That copy is
//   the test-only override described on `stageTree` — an edit to a file under
//   node_modules/.cache, imported by path. Nothing in src reaches it, no argument
//   selects it and no environment variable points at it. Against it, the clause
//   this part used to make of production still stands: each reader returns the
//   gate's OWN answer, pinned by the repository's canonical digest rather than by
//   a field-by-field comparison against a shape that happens to match today, for
//   every query shape including hostile ones. That is the behaviour Gate Zero
//   falls back to if a ruling is ever withdrawn.
//
// PART B, THE PUBLIC SURFACE, AND THE SWEEP HAS NO EXEMPTIONS. The export list
// of all three modules is exactly enumerated. EVERY export is swept — constants
// by value, callables by CALLING them, and CONSTRUCTORS BY CONSTRUCTING THEM,
// with no argument and with each hostile argument in each position, and sweeping
// what comes back or what they throw. The sweep looks for the closed union of
// privileged words as exact value, as token, AND as raw substring; for any
// `would_*` or `*_if_authoritative` key; and for the boolean `true` anywhere at
// all, under any key.
//
// THE NAMED EXEMPTION THAT USED TO SIT HERE IS DELETED. An earlier draft swept
// `SeamStoreUnreachable` by constructing it with its OWN closed reasons and a
// well-formed store ref, which is the one input shape that could never leak, and
// skipped it otherwise. Under that exemption the constructor interpolated and
// retained whatever a caller handed it — a store ref, a reason, and the whole
// `cause` object — and the sweep never saw it. It is now constructed with the
// same hostile arguments as everything else, and its own properties, message,
// stack and cause are swept in turn.
//
// THE STRING ALLOWLIST THE EARLIER DRAFT CARRIED IS DELETED. It existed because
// the reader module reused the gate's own seam names and reason ids verbatim,
// and those contain "read" ("read-only", "readback") and "active"
// ("scheduler-active-receipt"). The module no longer emits any of them: seams
// are addressed by opaque card tokens, and its findings are its own words. So
// there is nothing left to exempt and nothing is exempted.
//
// WHAT REPLACES IT IS NOT AN ALLOWLIST BUT AN IDENTITY. While no seam is ruled,
// a reader's answer IS the gate's answer — the object `readGateZeroPredecessorJoin`
// or `readGateGraphAssurance` built, returned unmodified. That object is main's,
// not this slice's, and its strings are Gate Zero's live refusal: renaming them
// would change what Gate Zero says to every caller, which this PR must not do.
// So a returned value that is digest-identical to a gate answer is asserted to
// BE that answer rather than swept for words. Identity is the stronger check —
// a word sweep permits any new string that dodges the union, and identity
// permits no new string at all.
//
// PART C, THE RULED PATH OVER ROWS, ON A STAGED TREE. mcp-server/src is copied
// into a scratch directory, a DISTINCT FIXTURE decision id is put on each of the
// three `decision_id:` lines in place of the id Joe pasted there, and the copied
// reader is imported. The fixture ids stay because they are what makes the
// per-card claim falsifiable: the three ids differ, so a reader that looked up
// another card's line is caught by the id before it reads a row. Four stagings:
//
//   * with the store module REPLACED by ./gate-zero-seam-stores.v5.fixture.mjs,
//     whose rows are shaped exactly as production writes them, which proves
//     every clause including one negative row per field of card 12's
//     receipt-binding clause;
//   * with ./gate-zero-seam-stores.v5.receipt-fixture.mjs, which pulls the
//     acceptance receipt's hash apart from the proposal's — the one thing no
//     production row can do, and the clause mutation testing found unproved;
//   * with ./gate-zero-seam-fault-injection.testhelper.mjs, a DISTINCT store
//     instance whose three fetchers misbehave — one throws a value that is not
//     an Error, one returns an answer whose getters throw, one answers about
//     another store — with every fault wired in by closure at construction and
//     none of them reachable through a label, an argument or an exported
//     constant. It is what makes the reader's own boundary reachable, and it is
//     a SEPARATE module precisely so no fixture surface answers a caller's label;
//   * with card 11's `store_ref:` line changed to a store its reader does not
//     serve, which must refuse with the gate's own answer and must NOT fetch;
//   * with the REAL store module kept and no connection configured, which proves
//     the unreachable refusals are the real code's, not a fixture's.
//
// What runs in all four is the real reader, the real ruling gate and the real
// derivation. The staging is how a store is stood in for without standing in for
// the reader, and it doubles as the check on the ruling table's own shape: each
// staging asserts it found Joe's three lines exactly once apiece before it
// touches them, so a line that drifted fails Part C on its anchor rather than
// silently proving nothing.
//
// PART D, THE PRODUCER SEAM IS NOT BUILT, AND IS NOT COPIED. Cards 9 and 10 have
// no ruling line, no reader and no restatement of their refusal anywhere in this
// slice — `emitGateZeroOutcome()` still says it, once.
//
//   node --test mcp-server/test/gate-zero-seam-readers.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import esbuild from "esbuild";

import { WHOLE_WALK, pathToValue, topLevelIdentityOnly }
  from "./gate-zero-reachability-walk.testhelper.mjs";

import { digest } from "../src/artifact-trust.js";
import { V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  V5_A02_GATE_ZERO_OWED_SEAMS,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_SEAM,
  V5_A02_POLICY_VERSION,
  V5_A02_SCHEDULER_STEP_REF,
  emitGateZeroOutcome,
  readGateGraphAssurance,
  readGateZeroPredecessorJoin,
} from "../src/gate-zero-assurance.v5.js";

import * as readers from "../src/gate-zero-seam-readers.v5.js";
import * as binding from "../src/internal/gate-zero-seam-binding.v5.js";
import * as rulings from "../src/gate-zero-seam-rulings.v5.js";
import * as stores from "../src/gate-zero-seam-stores.v5.js";
import * as fixtureStores from "./gate-zero-seam-stores.v5.fixture.mjs";
import * as receiptStores from "./gate-zero-seam-stores.v5.receipt-fixture.mjs";
import * as faultedStores from "./gate-zero-seam-fault-injection.testhelper.mjs";
import {
  PRE_PR_BASELINE, PRE_PR_BASELINE_DIGEST, PRE_PR_COMMIT, commitReachable,
  prePrCommitReachable, releasePrePrTrees, stagePrePrTree,
} from "./gate-zero-pre-pr-baseline.v5.testhelper.mjs";

const SRC = fileURLToPath(new URL("../src", import.meta.url));
const FIXTURE_STORE_FILE = fileURLToPath(new URL("./gate-zero-seam-stores.v5.fixture.mjs", import.meta.url));
const RECEIPT_STORE_FILE = fileURLToPath(new URL("./gate-zero-seam-stores.v5.receipt-fixture.mjs", import.meta.url));
const FAULT_STORE_FILE =
  fileURLToPath(new URL("./gate-zero-seam-fault-injection.testhelper.mjs", import.meta.url));
const RULINGS_FILE = "gate-zero-seam-rulings.v5.js";
const READERS_FILE = "gate-zero-seam-readers.v5.js";
/** The internal path the shared ruling predicate moved to, relative to src. */
const BINDING_FILE = "internal/gate-zero-seam-binding.v5.js";
const STORES_FILE = "gate-zero-seam-stores.v5.js";
/** The one module outside the readers that opens a store — see ISOLATION below. */
const PRODUCER_FILE = "gate-zero-producer.v5.js";
const GATE_FILE = "gate-zero-assurance.v5.js";
const REGISTRATION_FILE = "gate-zero-producer-registration.v5.js";

/**
 * CARD 9'S ONE LINE, and the null it goes back to — the same anchor
 * gate-zero-assurance.v5.test.mjs stages against, because it is the same switch.
 * It matters here because the producer seam's binding condition IS that
 * declaration: an unruled staging that left the seat staffed would leave a bound
 * producer behind a gate whose three readers are all withdrawn.
 */
const STAFFED_SEAT_LINE = '  holder_ref: "seat:codex-reviewer:gpt-5.6-sol",\n';
const UNSTAFFED_SEAT_LINE = "  holder_ref: null,\n";
const FAKE_PG_FILE = fileURLToPath(new URL("./gate-zero-seam-pg.v5.fake.cjs", import.meta.url));

/** The repository the checks store serves, and the only one it will serve. */
const AUTHORITATIVE_REPOSITORY = "jbookout/carr-system";

/**
 * THE STORE'S CLOSED REASON SET, restated here because it is a CONTRACT rather
 * than an implementation detail: a reader puts one of these straight into an
 * answer, and the guarded boundary may re-throw nothing else. The test below
 * reads the module's own registry back out of its source and asserts the two
 * agree, so adding a reason in src without adding it here is red.
 */
const STORE_UNREACHABLE_REASONS = Object.freeze([
  "the checks source answer did not parse",
  "the checks source credentials are not configured in this process",
  "the checks source refused the request",
  "the checks source was not reachable",
  "the database client is not available in this process",
  "the connection target for this store is not configured in this process",
  "the query did not finish",
  "the query did not address a row",
  "the configured checks repository is not the one this file serves",
  "the call did not finish",
  "the addressed head sha is not the shape this file serves",
  "more than one authenticated candidate build row answers this head sha",
  "the reason this store was unreachable is not a registered one",
]);

const SWEPT_NAMESPACES = () =>
  [["readers", readers], ["rulings", rulings], ["stores", stores], ["binding", binding]];

const STORE_CREDENTIALS = ["DATABASE_URL_READER", "GITHUB_TOKEN", "GITHUB_REPOSITORY"];

function saveEnv(names) {
  const saved = {};
  for (const name of names) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  return saved;
}

function restoreEnv(saved) {
  for (const [name, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
}

/**
 * THE THREE RULINGS, ON THE RECORD. Joe ruled cards 11, 12 and 13 on
 * 2026-09-11, and these are the ids `log-decision` returned, in the order the
 * `decision_id:` lines appear in the ruling table — card 11, card 12, card 13.
 * They are restated here rather than read out of src, so a paste that changed a
 * character is red rather than self-confirming.
 */
const PASTED_DECISION_IDS = Object.freeze([
  "16c7cdfb-b675-4b6a-bbff-4bbdab46baf8",
  "f7c486d6-5bee-4c4c-a76f-c0f162f66db8",
  "87e9e11e-64b2-49b3-a6aa-4901c24eaa91",
]);

/** The card tokens, in the same order as the ids and the lines above. */
const CARD_REFS = Object.freeze(["card:11", "card:12", "card:13"]);

/** The store each card's ruling names, in the same order. */
const CARD_STORE_REFS = Object.freeze([
  "record-layer:work-request-outcome-feedback",
  "control-plane:ops.service+ops.run",
  "github:checks",
]);

/** A `decision_id:` line, ruled or not, in the exact shape the table holds. */
const decisionLine = id => id === null
  ? "    decision_id: null,\n"
  : `    decision_id: "${id}",\n`;

/**
 * The three lines as they stand in src today. They are the staging anchors now:
 * before the ruling the staging pasted OVER a null, and it now pastes over a
 * ruling — either a fixture id, or the null that proves the unruled path.
 * If any of these stops matching, nothing below is proved.
 */
const RULED_DECISION_LINES = Object.freeze(PASTED_DECISION_IDS.map(decisionLine));

/** The unruled line. Reachable only in a staged copy; never in src. */
const NULL_DECISION_LINE = decisionLine(null);

/** Card 11's store line, the anchor for the wrong-store staging. */
const CARD_11_STORE_LINE = `    store_ref: "record-layer:work-request-outcome-feedback",\n`;

/**
 * Three distinct well-formed ids, in the order the `decision_id:` lines appear
 * in the ruling table — card 11, card 12, card 13. Distinct on purpose: each
 * reader is asserted to come back with ITS OWN card's id, so a reader that
 * looked up the wrong card fails here instead of reading the wrong store later.
 */
const FIXTURE_DECISION_IDS = Object.freeze([
  "11111111-1111-4111-8111-111111111111",
  "22222222-2222-4222-8222-222222222222",
  "33333333-3333-4333-8333-333333333333",
]);

// ---------------------------------------------------------------------------
// PART A — the ruling gate is shut, and the refusal is the gate's own answer.
// ---------------------------------------------------------------------------

const READERS_UNDER_TEST = [
  ["readPredecessorOutcomeEvidence", readers.readPredecessorOutcomeEvidence,
    readGateZeroPredecessorJoin, "readGateZeroPredecessorJoin"],
  ["readSchedulerCanaryEvidence", readers.readSchedulerCanaryEvidence,
    readGateZeroPredecessorJoin, "readGateZeroPredecessorJoin"],
  ["readGateConclusionEvidence", readers.readGateConclusionEvidence,
    readGateGraphAssurance, "readGateGraphAssurance"],
];

/** The repository this suite lives in, for the workflow files it reads below. */
const REPO_ROOT = fileURLToPath(new URL("../..", import.meta.url));

/**
 * TWO SETS, AND THE DIFFERENCE BETWEEN THEM IS THE WHOLE OF AMENDMENT 3.
 *
 * `PRE_PR_GATE_ANSWER_DIGESTS` is the amendment's exemption and is NOT ONE
 * DIGEST WIDER than the amendment allows: the two answers Gate Zero gave BEFORE
 * this PR. Amendment 5 of 2026-09-12 is why they are not the literals an earlier
 * round wrote down — a test may exempt only values it PROVES predate the PR, and
 * an author's literal proves only what the author believed.
 *
 * THE FIFTH CORRECTION IS WHY THEY ARE NO LONGER READ OUT OF `origin/main` AT
 * LOAD TIME. That ref moves: the moment this PR merges, origin/main IS this
 * branch, the exemption quietly widens to cover everything this branch writes,
 * and the "did the staging restore anything" assertion goes empty and red. The
 * values now come from commit 229980a5 — the merge-base, which cannot move —
 * committed as explicit values, digest-pinned in the baseline helper, and
 * REGENERATED from `git show 229980a5:<path>` by the test below wherever that
 * commit is reachable.
 *
 * `GATE_DELEGATION_DIGESTS` is a different question with a different answer and
 * exempts nothing. It is the set of answers each tree's OWN gate gives, used only
 * to assert delegation — that an unruled reader hands back its gate's object
 * whole, and that a ruled one does not. It seeds with the shipped gate's two
 * answers and takes on each staged tree's as that tree is built, because since
 * 2026-09-12 the gate binds these readers behind the same ruling table and a tree
 * with three null lines has a gate that answers differently from the shipped one.
 * The vocabulary of a branch-authored gate answer is swept where it belongs, in
 * gate-zero-assurance.v5.test.mjs, against the same pre-PR baseline read the same
 * way, with this branch's additions enumerated.
 */
assert.equal(digest(PRE_PR_BASELINE), PRE_PR_BASELINE_DIGEST,
  "the committed pre-PR baseline is not the snapshot this suite pins");

const PRE_PR_GATE_ANSWER_DIGESTS = new Set(PRE_PR_BASELINE.gate_answer_digests);

/**
 * The pre-PR policy version, put back before a pinned comparison. See the note
 * at the pin itself: this slice moved `V5_A02_POLICY_VERSION` 2 -> 3 because the
 * surface stopped being one that can only refuse, and that number rides on every
 * object the gate builds. Everything else is still compared byte for byte, and
 * the two assertions inside make the normalization itself falsifiable — it
 * refuses to run against an answer that does not carry this branch's version,
 * and it refuses to exist at all if the version ever stops having moved.
 */
const PRE_PR_POLICY_VERSION = 2;
function atPrePrPolicyVersion(answer) {
  assert.equal(answer.policy_version, V5_A02_POLICY_VERSION,
    "the answer does not carry this branch's policy version, so the normalization is wrong");
  assert.notEqual(V5_A02_POLICY_VERSION, PRE_PR_POLICY_VERSION,
    "the policy version did not move, so this normalization is hiding nothing and must go");
  return { ...answer, policy_version: PRE_PR_POLICY_VERSION };
}

// TWO DISTINCT ANSWERS, AND NEITHER OF THEM IS THIS BRANCH'S. The shipped gate
// is ruled and answers differently; if the baseline ever held this branch's own
// answers, the exemption would silently widen to cover everything this branch
// writes, which is the hole the fourth review named and the ref the fifth one
// named.
assert.equal(PRE_PR_GATE_ANSWER_DIGESTS.size, 2,
  "the pre-PR gate no longer gives two distinct answers");
for (const answer of [readGateZeroPredecessorJoin(), readGateGraphAssurance()])
  assert.equal(PRE_PR_GATE_ANSWER_DIGESTS.has(digest(answer)), false,
    "the pre-PR baseline holds what the shipped gate answers, so it is not the pre-PR gate's");

test("BASELINE: the pre-PR gate answers are that commit's own, regenerated from git", async () => {
  // THE DIGEST HALF RUNS EVERYWHERE, shallow checkouts included, and these are
  // its mutation controls.
  assert.equal(digest(PRE_PR_BASELINE), PRE_PR_BASELINE_DIGEST);
  assert.notEqual(
    digest({ ...PRE_PR_BASELINE,
      gate_answer_digests: [...PRE_PR_BASELINE.gate_answer_digests, digest(readGateGraphAssurance())] }),
    PRE_PR_BASELINE_DIGEST,
    "a baseline with this branch's answer added to it digests the same, so the pin proves nothing");
  assert.notEqual(digest({ ...PRE_PR_BASELINE, commit: "0".repeat(40) }), PRE_PR_BASELINE_DIGEST,
    "a baseline naming a different commit digests the same");

  // AND THE REGENERATION HALF IS SKIPPABLE ONLY WHERE THE COMMIT IS GENUINELY
  // GONE — a checkout too shallow to hold the merge-base. The probe is proved to
  // answer false for an absent commit rather than to answer false always.
  assert.equal(commitReachable("0".repeat(40)), false,
    "the reachability probe calls a commit that cannot exist reachable");
  if (!prePrCommitReachable()) {
    console.log(`REGENERATION SKIPPED: ${PRE_PR_COMMIT} is not in this checkout`);
    return;
  }

  const prePrGate = await import(pathToFileURL(join(stagePrePrTree(), GATE_FILE)).href);
  const regenerated = [
    digest(prePrGate.readGateZeroPredecessorJoin()),
    digest(prePrGate.readGateGraphAssurance()),
  ].sort();
  assert.deepEqual(regenerated, [...PRE_PR_BASELINE.gate_answer_digests],
    "the committed pre-PR answers are not what that commit's gate answers with");
  assert.equal(new Set(regenerated).size, 2,
    "that commit's gate gives one answer twice, so the pair proves nothing");
  // NON-VACUOUS: the tree that was walked is the commit's, not this branch's
  // wearing its name.
  for (const answer of [readGateZeroPredecessorJoin(), readGateGraphAssurance()])
    assert.equal(regenerated.includes(digest(answer)), false,
      "the staged pre-PR tree answers what this branch answers, so it is not that commit's");
});

const GATE_DELEGATION_DIGESTS = new Set([
  digest(readGateZeroPredecessorJoin()),
  digest(readGateGraphAssurance()),
]);

/** Each staged readers namespace, mapped to the gate module of the same tree. */
const STAGED_GATES = new Map();

/** The gate that belongs to a staged readers module. */
function gateOf(stagedReadersModule) {
  const gate = STAGED_GATES.get(stagedReadersModule);
  assert.ok(gate !== undefined, "a staged readers module has no gate recorded for its tree");
  return gate;
}

test("RULING: all three cards are ruled, and the lookup is still the only way to ask", () => {
  // THE PASTE OF 2026-09-11. The table is not exported: `seamRulingRef` is the
  // whole surface, and it now answers each card with the pair Joe's ruling put
  // on that card's two lines — the decision id he logged, and the store he ruled
  // authoritative for it. Both halves are asserted, because both halves are the
  // ruling: an id pasted beside the wrong store is not the decision he made.
  assert.deepEqual(Object.keys(rulings).sort(), ["seamRulingRef"]);
  CARD_REFS.forEach((card, index) => {
    const ref = rulings.seamRulingRef(card);
    assert.deepEqual(ref,
      { decision_ref: PASTED_DECISION_IDS[index], store_ref: CARD_STORE_REFS[index] }, card);
    assert.ok(Object.isFrozen(ref), card);
    // And the lookup builds a fresh pair per call, so no caller can hold the
    // table's own object and no two callers share one.
    assert.notEqual(ref, rulings.seamRulingRef(card), card);
  });
  // The three ids are distinct, so a reader that read another card's line is
  // caught by the id rather than by whichever store it went on to open.
  assert.equal(new Set(PASTED_DECISION_IDS).size, 3);
  // Nothing else is ruled. The producer seam, and anything else a caller might
  // name, still answers null.
  for (const card of ["card:9", "card:10", "card:14", V5_A02_GATE_ZERO_PRODUCER_SEAM])
    assert.equal(rulings.seamRulingRef(card), null, String(card));
});

test("RULING: the lookup takes a card token and never a decision id", () => {
  // Arity is the boundary, and so is the vocabulary: there is no argument that
  // could carry a ruling, and a well-formed decision id handed in as the card
  // token is not a card token.
  assert.equal(rulings.seamRulingRef.length, 1);
  const hostiles = [undefined, null, {}, [], 0, true, Symbol("x"),
    FIXTURE_DECISION_IDS[0], "card:14", "__proto__", "constructor", "toString",
    { decision_id: FIXTURE_DECISION_IDS[0] },
    // And the ids that ARE on the record: a real ruling handed in as the card
    // token is still not a card token, which is what makes a ruling a commit.
    ...PASTED_DECISION_IDS, ...PASTED_DECISION_IDS.map(id => ({ decision_id: id })),
    ...hostileQueries()];
  // Indexed, not stringified: one of these throws from its own toString.
  hostiles.forEach((hostile, index) =>
    assert.equal(rulings.seamRulingRef(hostile), null, `hostile argument ${index}`));
});

test("RULING: the three decision_id lines carry the three ids Joe ruled, and no null", () => {
  // The seams report told Joe to paste onto these three lines, and the paste has
  // happened. What is checked now is that the file holds exactly those three
  // rulings, in card order, in the shape the lookup's pattern admits — and that
  // no line went back to null. The ids are restated in this file rather than
  // read out of src, so a paste that dropped or transposed a character is red
  // here instead of quietly ruling something Joe did not rule.
  const source = readFileSync(join(SRC, RULINGS_FILE), "utf8");
  const found = [...source.matchAll(/^ {4}decision_id: (.+),$/gm)].map(one => one[1]);
  assert.deepEqual(found, PASTED_DECISION_IDS.map(id => `"${id}"`),
    "the ruling table no longer holds Joe's three ids, in card order, and nothing else");
  assert.equal(source.includes(NULL_DECISION_LINE), false,
    "a decision_id line went back to null, which would shut a ruled seam");
  RULED_DECISION_LINES.forEach((line, card) =>
    assert.equal(source.split(line).length - 1, 1,
      `card ${11 + card}'s ruling is not the single line the staging edits`));
  assert.equal(source.split(CARD_11_STORE_LINE).length - 1, 1,
    "card 11's store_ref line is no longer the single line the staging edits");
  // Each ruling sits BESIDE its own store line, in that order, because the two
  // lines are one ruling and the lookup reads them together.
  CARD_STORE_REFS.forEach((storeRef, card) =>
    assert.ok(source.includes(`    store_ref: "${storeRef}",\n${RULED_DECISION_LINES[card]}`),
      `card ${11 + card}'s ruling is not on the line below the store it names`));
});

test("RULING SHUT: an unruled reader returns the gate's own answer, byte for byte", async () => {
  // WHAT MOVED WITH THE PASTE, AND WHAT DID NOT. Before 2026-09-11 this clause
  // was asked of the production readers, because src carried three nulls. It is
  // now asked of a STAGED TREE whose three lines are back to null — the test-only
  // override described on `stageTree`, an edit to a copy under
  // node_modules/.cache that production has no route to. The clause itself is
  // unchanged and it still matters: it is the behaviour Gate Zero falls back to
  // if a ruling is ever withdrawn, and the proof that the readers own no refusal
  // text of their own.
  //
  // The gate builds a fresh frozen object per call, so identity is not available
  // to assert. What IS available is stronger than deep-equality on its own: the
  // answer is byte-identical under the repository's own canonical digest, AND
  // the reader's source returns the gate function's result directly rather than
  // assembling a shape that happens to match today. The source half is asserted
  // against REAL src, not the copy.
  const source = readFileSync(join(SRC, READERS_FILE), "utf8");
  assert.equal((source.match(/return readGateZeroPredecessorJoin\(\);/g) ?? []).length, 2,
    "a reader stopped delegating its refusal to the gate");
  assert.equal((source.match(/return readGateGraphAssurance\(\);/g) ?? []).length, 1,
    "the conclusion reader stopped delegating its refusal to the gate");

  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    const unruled = await stagedReaders({ unruled: true });
    for (const [name, , , gateName] of READERS_UNDER_TEST) {
      const expected = gateOf(unruled)[gateName]();
      const got = await unruled[name]({ stepRef: "step:wr46-dissolution-outcome" });
      assert.deepEqual(got, expected, `${name} returned a different refusal than the gate's`);
      assert.ok(Object.isFrozen(got), name);
      assert.equal(digest(got), digest(expected), name);
      assert.equal(got.status, "unavailable", name);
      assert.equal(got.decision, "refuse", name);
      assert.equal(got.caller_evidence_admitted, false, name);
      // And it says nothing about a ruling, because there is none to say.
      assert.equal(Object.hasOwn(got, "ruling_decision_ref"), false, name);
    }
    // And the reason ids are the ones an UNRULED gate refuses with, not new
    // words. Asked of the staged tree's gate: since 2026-09-12 the shipped gate
    // binds these readers behind the same three ruling lines, so with the lines
    // live it refuses one step further on — `gate_zero_producer_seam_unavailable`
    // — and these two ids are exactly what it goes back to when they are null.
    const gate = gateOf(unruled);
    assert.equal(gate.readGateZeroPredecessorJoin().reason_id,
      "predecessor_outcome_reader_unavailable");
    assert.equal(gate.readGateGraphAssurance().reason_id, "gate_conclusion_reader_unavailable");
  } finally {
    restoreEnv(saved);
  }
});

test("RULED: each production reader now names its own card's ruling, and refuses anyway", async () => {
  // THE OTHER SIDE OF THE PASTE, asked of the SHIPPED modules with nothing
  // configured — which is what every caller in this repository gets today, and
  // will get until a connection target and a checks credential exist in the
  // process that runs Gate Zero.
  //
  // Three things are asserted, and the third is the one that matters most:
  //
  //   * the answer carries THIS card's decision ref and THIS card's store ref,
  //     so a reader that read the wrong line is caught here;
  //   * it is decided by the ruled store's rows and by nothing else;
  //   * IT STILL REFUSES. A ruling says where to read, not what the answer is.
  //     No caller's evidence and no model's judgment is admitted, no finding is
  //     reported, and no effect is created — the ruled path's refusal is as
  //     closed as the unruled one, and the seam is open only in the sense that
  //     the reader would now go and look.
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    for (const [index, [name, reader]] of READERS_UNDER_TEST.entries()) {
      const got = await reader({ stepRef: "step:wr46-dissolution-outcome" });
      assert.ok(Object.isFrozen(got), name);
      assert.equal(got.card_ref, CARD_REFS[index], name);
      assert.equal(got.ruling_decision_ref, PASTED_DECISION_IDS[index], name);
      assert.equal(got.store_ref, CARD_STORE_REFS[index], name);
      assert.equal(got.decided_by, "ruled_store_rows", name);
      assert.equal(got.status, "unavailable", name);
      assert.equal(got.decision, "refuse", name);
      assert.equal(got.finding, null, name);
      assert.equal(got.caller_evidence_admitted, false, name);
      assert.equal(got.model_judgment_admitted, false, name);
      assert.deepEqual(got.effects, V5_NO_EFFECTS, name);
      // And it is NOT the gate's answer any more, which is the whole observable
      // difference the paste made.
      assert.equal(GATE_DELEGATION_DIGESTS.has(digest(got)), false,
        `${name} still answers as though it were unruled`);
    }
  } finally {
    restoreEnv(saved);
  }
});

/**
 * REQUIREMENT (4), WHICH WAS THE WHOLE POINT OF SHIPPING THIS UNRULED: with the
 * three decision ids null, every caller of every reader gets, byte for byte, the
 * JSON main's Gate Zero already returns. There is no input — well formed,
 * malformed, hostile or absent — for which that is not true.
 *
 * Joe's paste of 2026-09-11 is exactly what that requirement was waiting for, so
 * this is no longer a claim about the shipped modules. It is asked of the staged
 * unruled tree, where it remains the falsifiable form of "a reader with no ruling
 * reads nothing and invents nothing": one input shape that moved the answer would
 * mean the query was looked at before the ruling was.
 */
test("BYTE-IDENTICAL TO MAIN: an unruled answer does not move for any query, valid or not", async () => {
  const queries = [
    undefined, null, {}, [], "step:wr46-dissolution-outcome", 1, true,
    { stepRef: "step:wr40-repository-outcome", outcomeHash: `sha256:${"a".repeat(64)}` },
    { serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" },
    { headSha: "a".repeat(40), checkName: "main canary" },
    { decision_id: FIXTURE_DECISION_IDS[0], stepRef: "step:wr46-dissolution-outcome" },
    { ruling_decision_ref: FIXTURE_DECISION_IDS[0] },
    { card_ref: "card:11", store_ref: "github:checks", finding: "gate_conclusion_observed" },
    ...hostileQueries(),
  ];
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    const unruled = await stagedReaders({ unruled: true });
    for (const [name, , , gateName] of READERS_UNDER_TEST) {
      const baseline = digest(gateOf(unruled)[gateName]());
      for (const query of queries) {
        const got = await unruled[name](query);
        assert.equal(digest(got), baseline,
          `${name} answered differently for ${safeLabel(query)}`);
        assert.ok(GATE_DELEGATION_DIGESTS.has(digest(got)), name);
      }
    }
  } finally {
    restoreEnv(saved);
  }
});

/**
 * THE RULED COUNTERPART, and it is the clause that replaces the one above for
 * the modules this repository actually ships. A ruling says WHERE to read. It
 * does not say what was found, and it does not make a query that addresses no
 * row into one that does. So the shipped readers' answer may now differ between
 * queries — a malformed field is named, an unreachable store is reported — and
 * across every one of those shapes the closed half must not move: refuse,
 * nothing admitted, no finding, no effect, and none of the caller's bytes.
 */
test("RULED: no query, valid or not, moves the closed half of a ruled answer", async () => {
  const queries = [
    undefined, null, {}, [], "step:wr46-dissolution-outcome", 1, true,
    { stepRef: "step:wr40-repository-outcome", outcomeHash: `sha256:${"a".repeat(64)}` },
    { serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" },
    { headSha: "a".repeat(40), checkName: "main canary" },
    { decision_id: FIXTURE_DECISION_IDS[0], stepRef: "step:wr46-dissolution-outcome" },
    { ruling_decision_ref: FIXTURE_DECISION_IDS[0] },
    // The real ruling handed back in as a query field, which is the one shape a
    // caller might hope a ruled reader would accept as evidence of a ruling.
    ...PASTED_DECISION_IDS.map(id => ({ ruling_decision_ref: id, decision_id: id })),
    { card_ref: "card:11", store_ref: "github:checks", finding: "gate_conclusion_observed" },
    ...hostileQueries(),
  ];
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    for (const [index, [name, reader]] of READERS_UNDER_TEST.entries())
      for (const query of queries) {
        const got = await reader(query);
        const label = `${name} for ${safeLabel(query)}`;
        assert.equal(got.decision, "refuse", label);
        assert.equal(got.status, "unavailable", label);
        assert.equal(got.finding, null, label);
        assert.equal(got.caller_evidence_admitted, false, label);
        assert.equal(got.model_judgment_admitted, false, label);
        assert.deepEqual(got.effects, V5_NO_EFFECTS, label);
        // The ruling it names is its own card's, whatever the caller said about
        // one, and the store is the one that card ruled.
        assert.equal(got.ruling_decision_ref, PASTED_DECISION_IDS[index], label);
        assert.equal(got.card_ref, CARD_REFS[index], label);
        assert.equal(got.store_ref, CARD_STORE_REFS[index], label);
        assert.ok(!JSON.stringify(got).includes(HOSTILE_MARKER), label);
      }
  } finally {
    restoreEnv(saved);
  }
});

test("RULING SHUT: nothing in the reader or the ruling table reaches the environment", () => {
  // A seam an env var could open is a seam any shell could open. Only the store
  // layer reads the environment, and only for WHERE a ruled store lives.
  for (const file of [RULINGS_FILE, READERS_FILE, BINDING_FILE]) {
    const source = readFileSync(join(SRC, file), "utf8");
    assert.equal(/process\s*\.\s*env/.test(source), false, `${file} names the process environment`);
  }
  // And each reader takes one query. A second argument cannot carry a ruling
  // because there is no second argument to carry it.
  for (const [name, reader] of READERS_UNDER_TEST)
    assert.equal(reader.length, 1, `${name} takes more than the query`);
});

// ---------------------------------------------------------------------------
// PART B — the public surface, and the privileged-word sweep.
// ---------------------------------------------------------------------------

const EXPECTED_EXPORTS = Object.freeze({
  readers: [
    "GATE_ZERO_SEAM_READERS_SCHEMA_VERSION",
    "readGateConclusionEvidence",
    "readPredecessorOutcomeEvidence",
    "readSchedulerCanaryEvidence",
    // AND NOTHING ELSE. The second correction put the shared ruling predicate
    // here, which bought the gate the reader's own test at the price of this
    // module's four-name promise; the third moved it to an internal path both
    // files import. The absence is asserted below, by name.
  ],
  rulings: ["seamRulingRef"],
  // The internal surface: one predicate, reachable only by importing the path.
  binding: ["ruledCardBinding"],
  stores: [
    // The three CARD stores, and one more that is not a card: the
    // candidate-build record the producer reads its subject maker out of
    // (amendment 9, 2026-09-14). Its token is spelled without the table's name
    // on purpose — see the store module's own note — because the sweep below
    // closes over `release` as a substring.
    "fetchCandidateBuildRecordRows",
    "fetchCheckConclusionRows",
    "fetchPredecessorOutcomeRows",
    "fetchSchedulerLedgerRows",
    "isSeamStoreUnreachable",
    "seamStoreUnreachable",
  ],
});

/** The closed union, verbatim from the standing rule of 2026-09-11. */
const PRIVILEGED_WORDS = Object.freeze([
  "allow", "commit", "prompt", "suppress", "release", "read", "covered", "drafted",
  "proposed", "queued", "healthy", "passing", "ok", "pass", "satisfied", "complete",
  "admitted", "resumed", "attended", "verified", "present", "equivalent",
  "operational", "active", "green", "joins_exactly", "coverage_complete", "favorable",
]);

/**
 * Four sweeps over one value, in order of strictness, AND NO CARVE-OUT OF ANY
 * KIND. There is no set of blessed strings in this file.
 *
 *   exact      the whole string IS a privileged word.
 *   token      a word of the string, split on every non-alphanumeric run, IS one.
 *   substring  the privileged word appears anywhere in the string.
 *   shape      any `would_*` or `*_if_authoritative` key, and the boolean `true`
 *              anywhere at all, under any key, at any depth.
 *
 * The boolean rule is wider than the standing rule's "a privileged key that is
 * literally true", on purpose: a clause result is reported as "held", "failed" or
 * "unknown", so a `true` on this surface means someone reintroduced a yes/no
 * answer under a name the union does not happen to list.
 */
function privilegedFindings(value, path = "$", found = []) {
  if (value === true) { found.push(`${path} is the boolean true`); return found; }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedFindings(entry, `${path}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      const at = `${path}.${key}`;
      if (/^would_/.test(key) || key.includes("_if_authoritative"))
        found.push(`${at} is a conditional key on the public surface`);
      privilegedFindings(entry, at, found);
    }
    return found;
  }
  if (typeof value !== "string") return found;
  const tokens = value.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  for (const word of PRIVILEGED_WORDS) {
    if (value === word) found.push(`${path} is the privileged word ${word}`);
    else if (tokens.includes(word)) found.push(`${path} carries the privileged token ${word}`);
    else if (value.includes(word)) found.push(`${path} carries ${word} as a substring`);
  }
  return found;
}

/**
 * The sweep, applied to one value.
 *
 * THE ONE STRUCTURAL FACT, and it is an identity rather than an allowlist: while
 * no seam is ruled, a reader returns the GATE'S answer, unmodified. That object
 * belongs to gate-zero-assurance.v5.js on main; its strings are Gate Zero's live
 * refusal, and renaming them would change what the gate says to every caller,
 * which this change must not do. So a value that is digest-identical to a gate
 * answer is asserted to BE one — which admits no new string at all, privileged or
 * otherwise — and everything else is swept with no exemption whatsoever.
 */
function assertSwept(label, value) {
  if (value !== null && typeof value === "object" && PRE_PR_GATE_ANSWER_DIGESTS.has(digest(value)))
    return;
  assert.deepEqual(privilegedFindings(value, label), [], `${label} carries a privileged outcome`);
}

function safeLabel(value) {
  try { return JSON.stringify(value) ?? String(value); } catch { return "<unserializable>"; }
}

// ---------------------------------------------------------------------------
// THE THROWN VALUE IS SWEPT AS A WHOLE, not as a `.message` and a `.because`.
//
// The third review round found the hole with a probe worth restating: it called
// an export from a caller function named `green`, the export threw a native
// TypeError, and the error's STACK — a list of the caller's own frame names —
// came back carrying `at green`. The sweep at the time read `.message` and
// `.because` and nothing else, so it saw nothing. It also could not have seen
// `throw "allow"`, `throw true`, or an object built to read as a verdict,
// because none of those has either field.
//
// So everything about the thrown value is inspected: what it IS, every own
// property including the non-enumerable ones Error keeps, and the five names a
// consumer would reach for. And the SHAPE is asserted as well as the words: a
// value that leaves by the throwing door has to be one of the module's own
// refusals, which means a fixed `stack` of exactly `${name}: ${message}`, no
// retained cause, non-writable non-configurable data properties, frozen, and a
// code out of the module's closed registry. A stack with frames in it fails on
// the equality before the word sweep ever has to catch the frame name.
// ---------------------------------------------------------------------------

function safeRead(holder, key) {
  try { return Reflect.get(Object(holder), key); } catch { return "<threw on read>"; }
}

function safeOwnKeys(value) {
  try { return Reflect.ownKeys(Object(value)); } catch { return []; }
}

function thrownFacets(thrown) {
  const facets = [["value", thrown]];
  if (thrown === null || (typeof thrown !== "object" && typeof thrown !== "function"))
    return facets;
  for (const key of safeOwnKeys(thrown))
    facets.push([`own.${String(key)}`,
      typeof key === "symbol" ? String(key) : safeRead(thrown, key)]);
  for (const key of ["name", "message", "stack", "cause", "code", "because", "store_ref"])
    facets.push([key, safeRead(thrown, key)]);
  return facets;
}

/** One thrown value, against every clause the surface owes for one. */
function assertNothingRaw(at, thrown) {
  for (const [facet, value] of thrownFacets(thrown)) assertSwept(`${at}.${facet}`, value);
  assert.ok(!safeLabel(thrownFacets(thrown).map(([, value]) => value)).includes(HOSTILE_MARKER),
    `${at} threw the caller's own text back`);

  assert.ok(thrown !== null && typeof thrown === "object",
    `${at} threw a bare ${typeof thrown}: ${safeLabel(thrown)}`);
  const name = safeRead(thrown, "name");
  const message = safeRead(thrown, "message");
  assert.equal(typeof name, "string", `${at} threw a value with no name`);
  assert.equal(typeof message, "string", `${at} threw a value with no message`);
  assert.equal(safeRead(thrown, "stack"), `${name}: ${message}`,
    `${at} carries an engine-built stack, which is the caller's own frames`);
  assert.equal(safeRead(thrown, "cause"), undefined, `${at} retained a cause`);
  assert.ok(Object.isFrozen(thrown), `${at} is not frozen`);
  for (const key of safeOwnKeys(thrown)) {
    const where = `${at}.${String(key)}`;
    const descriptor = Object.getOwnPropertyDescriptor(thrown, key);
    assert.ok(Object.hasOwn(descriptor, "value"), `${where} is an accessor, not a data property`);
    assert.equal(descriptor.writable, false, `${where} is writable`);
    assert.equal(descriptor.configurable, false, `${where} is configurable`);
  }
  assert.ok(STORE_UNREACHABLE_REASONS.includes(safeRead(thrown, "because")),
    `${at} carries a code the module never registered: ${safeLabel(safeRead(thrown, "because"))}`);
}

/**
 * Callers whose FRAME NAME is a privileged word. A named function expression is
 * the only way to put a chosen name in an engine stack, and that is exactly the
 * probe the re-review used.
 */
function privilegedCallers() {
  return [
    function green(call, argument) { return call(argument); },
    function allow(call, argument) { return call(argument); },
    function ok(call, argument) { return call(argument); },
    function verified(call, argument) { return call(argument); },
  ];
}

/** One invocation: whatever it returns is swept, whatever it throws is swept harder. */
async function assertNothingRawEscapes(at, invoke) {
  let outcome;
  try {
    outcome = await invoke();
  } catch (thrown) {
    assertNothingRaw(at, thrown);
    return;
  }
  assertSwept(`${at}.returned`, outcome);
  assert.ok(!safeLabel(outcome).includes(HOSTILE_MARKER), `${at} returned the caller's own text`);
}

/**
 * Every export, invoked every way it can be invoked, from every privileged
 * caller, with every hostile argument. ONE function, so the credential-less run
 * and the run over real rows ask exactly the same question.
 */
async function sweepEveryInvocation(label, namespace) {
  for (const [name, value] of Object.entries(namespace)) {
    const at = `${label}.${name}`;
    if (typeof value !== "function") { assertSwept(at, value); continue; }
    // CONSTRUCTION IS NO LONGER A CALL INTO THE MODULE, so it is no longer swept
    // as one. Amendment 2 of 2026-09-12: every export is a bound arrow, the
    // engine refuses `Reflect.construct` on one before any line of the module
    // runs, and that refusal is the engine's own — not a finding, and not a
    // value this surface produced. What IS asked, once per export and in full,
    // is that the shape holds: non-constructable, no prototype, no chain walked
    // on a caller's operand, and no module code reached by any newTarget a
    // caller supplies.
    assertClosedCallable(at, value);
    for (const caller of privilegedCallers())
      for (const argument of [undefined, ...hostileArguments()])
        await assertNothingRawEscapes(`${at}<-${caller.name}`, () => caller(value, argument));
  }
}

test("SWEEP: the sweep itself catches a privileged outcome when one is planted", () => {
  // A sweep nobody has seen fail is a sweep nobody has tested. One plant per
  // mechanism, so a broken mechanism cannot hide behind a working one.
  assert.ok(privilegedFindings({ ok: true }).length > 0);
  assert.ok(privilegedFindings({ receipt_binding: true }).length > 0,
    "the boolean rule missed a true under a name the union does not list");
  assert.ok(privilegedFindings({ status: "green" }).length > 0);
  assert.ok(privilegedFindings({ status: "the gate is green now" }).length > 0);
  assert.ok(privilegedFindings({ would_allow_if_authoritative: false }).length > 0);
  assert.ok(privilegedFindings({ note: "committed" }).length > 0,
    "the substring sweep missed a privileged word inside a longer one");
  // And the gate's own strings are NOT blessed as strings: only a whole object
  // that IS a gate answer is. A gate word lifted into a new sentence is caught.
  assert.ok(privilegedFindings({ note: V5_A02_GATE_ZERO_PRODUCER_SEAM }).length > 0,
    "a gate seam name pasted into a new value must still be caught");
  assert.ok(privilegedFindings({ note: V5_A02_SCHEDULER_STEP_REF }).length > 0);
  assert.throws(() => assertSwept("planted", { conclusion: "green" }));
});

test("SURFACE: every exported callable of all four modules is a guarded one", () => {
  // THE STRUCTURAL HALF OF INVARIANT (2), and it is here because the behavioural
  // half cannot reach all of it. The ruling lookup has no throwing path today —
  // `Object.hasOwn` on a frozen literal and a pattern over a string — so removing
  // its boundary changes nothing any input can observe, and a guard that no test
  // can see removed is a guard that quietly goes away. The invariant is that
  // EVERY export passes through one, whether today's implementation needs it or
  // not, so the invariant is what is checked.
  const stores_ = readFileSync(join(SRC, STORES_FILE), "utf8");
  const readers_ = readFileSync(join(SRC, READERS_FILE), "utf8");
  const rulings_ = readFileSync(join(SRC, RULINGS_FILE), "utf8");
  const binding_ = readFileSync(join(SRC, BINDING_FILE), "utf8");

  // Each module declares exactly one boundary, and it catches everything.
  for (const [name, source] of [["stores", stores_], ["readers", readers_]])
    assert.equal((source.match(/^function guarded\(/gm) ?? []).length, 1,
      `${name} has no single guarded boundary, or has more than one`);

  // Every fetcher and every reader is that boundary applied to a private
  // implementation — never the implementation exported directly.
  const guardedExports = source => [...source.matchAll(/^export const (\w+) =\s*\n?\s*([\s\S]{0,40})/gm)]
    .map(([, name, tail]) => [name, tail.trimStart().startsWith("guarded(")]);
  const storeExports = guardedExports(stores_);
  assert.deepEqual(storeExports.filter(([name]) => name.startsWith("fetch")).map(([, ok]) => ok),
    [true, true, true, true], "a store fetcher is exported without its boundary");
  assert.deepEqual(guardedExports(readers_).filter(([name]) => name.startsWith("read"))
    .map(([, ok]) => ok), [true, true, true], "a reader is exported without its boundary");

  // And the ruling lookup, whose boundary no input can reach, is asserted whole.
  assert.ok(/try \{\s*return seamRulingRefOf\(cardRef\);\s*\} catch \{\s*return null;\s*\}\s*\}/
    .test(rulings_), "the ruling lookup is exported without its boundary");

  // AND THE CLOSED SHAPE IS STRUCTURAL TOO, because the behavioural half cannot
  // see a shape that is not there. Amendment 2 of 2026-09-12, asked of the
  // source: one `closedCallable` per module, EVERY exported const passing
  // through it or through `guarded` (which returns one) or a plain string
  // constant, and NO Proxy anywhere — the wrapper the fourth correction shipped
  // is what forwarded `get` to a raw target, and it is deleted rather than
  // tightened.
  const sources = [["stores", stores_], ["readers", readers_], ["rulings", rulings_],
    ["binding", binding_]];
  for (const [name, source] of sources) {
    // ONE DEFINITION IN THE TREE, AND IT IS NOT IN THESE FILES (amendment 9,
    // 2026-09-14). This line used to require exactly one LOCAL definition per
    // module, on the argument that a self-contained module is worth a duplicated
    // primitive. The fifth review round measured that against the copies: all
    // five had drifted from src/closed-callable.js in the same way — none of
    // them froze the callable, which is clause (c) — so the by-source assertion
    // was proving that a copy existed, not that it was the hardened shape. Each
    // module now IMPORTS the one definition, and this asserts the local copies
    // are gone rather than that they are present.
    assert.equal((source.match(/^function closedCallable\(/gm) ?? []).length, 0,
      `${name} defines closedCallable locally again instead of importing the one definition`);
    assert.ok(/^import \{ closedCallable \} from "\.\.?\/?[\w.-]*closed-callable\.js";$/m
      .test(source), `${name} does not import the one closed-callable definition`);
    assert.equal(/new Proxy\(/.test(source), false, `${name} wraps a value in a Proxy again`);
    assert.equal(/^export (function|class) /m.test(source), false,
      `${name} exports a function declaration or a class, which are both constructors`);
  }
  const exportedConsts = source => [...source.matchAll(/^export const (\w+) =\s*\n?\s*([\s\S]{0,20})/gm)]
    .map(([, name, tail]) => [name, tail.trimStart()]);
  for (const [name, source] of sources)
    for (const [exported, tail] of exportedConsts(source))
      assert.ok(tail.startsWith("closedCallable(") || tail.startsWith("guarded(")
        || tail.startsWith('"'), `${name}.${exported} is exported without the closed shape`);
  // The ruling lookup by name, because it is the one export with no boundary
  // wrapper of its own in the line that exports it.
  assert.ok(/^export const seamRulingRef = closedCallable\(seamRulingRefLookup\);$/m.test(rulings_),
    "the ruling lookup is exported as something other than a closed callable");
  // And the shared predicate, for the same reason: its boundary is not reachable
  // by any input either, so the line that exports it is asserted whole.
  assert.ok(/^export const ruledCardBinding = closedCallable\(ruledCardBindingOf\);$/m.test(binding_),
    "the shared ruling predicate is exported as something other than a closed callable");
  // And the store's type is not on the surface at all: a factory and a predicate
  // are, and the class they build is private.
  assert.ok(/^class SeamStoreUnreachableType extends Error \{$/m.test(stores_),
    "the store's error type is no longer the module-private class it must be");
  assert.equal(/^export (const|class) SeamStoreUnreachable\b/m.test(stores_), false,
    "the store's error type is exported again");
});

test("SURFACE: the shared ruling predicate answers for the three cards and nothing else", () => {
  // The gate's bound-ness is this function's answer, so what it says for a card
  // token is asserted here once rather than inferred from a gate answer. It is
  // asked of the INTERNAL module, because that is where it lives: the third
  // correction's review refused it as a public name on the reader surface.
  for (const card of ["card:11", "card:12", "card:13"]) {
    const bound = binding.ruledCardBinding(card);
    assert.notEqual(bound, null, `${card} is ruled in src but the predicate refuses it`);
    // It is the ruling table's own pair, narrowed — never a value of its own.
    assert.deepEqual(bound, rulings.seamRulingRef(card), card);
  }
  // FAIL-CLOSED, and these are the tokens a caller could reach for: the producer
  // seam, a card with no store behind it, and anything that is not a card token.
  for (const other of ["card:9", "card:10", "card:14", "seam:gate-zero-read-only-outcome-producer",
    "", "16c7cdfb-b675-4b6a-bbff-4bbdab46baf8", 11, null, undefined, {}, Symbol("card:11")])
    assert.equal(binding.ruledCardBinding(other), null, `${String(other)} answered as a ruled card`);
});

test("SURFACE: the shared predicate is on no public namespace of this slice", () => {
  // THE THIRD CORRECTION'S FINDING, asserted as a property rather than as a
  // count. The predicate is shared — the gate asks the same function — and
  // sharing it through the reader's public surface is what the review refused:
  // the reader module promises four names, and a fifth is a wider surface
  // whether or not the fifth is narrow.
  for (const [label, namespace] of [["readers", readers], ["rulings", rulings], ["stores", stores]])
    assert.equal(Object.hasOwn(namespace, "ruledCardBinding"), false,
      `the shared predicate is a public name of ${label} again`);
  assert.deepEqual(Object.keys(readers).sort(), [...EXPECTED_EXPORTS.readers].sort(),
    "the reader module no longer promises exactly four public names");
  // And it IS reachable where it lives, or the clause above would hold vacuously
  // over a predicate nobody can call.
  assert.equal(typeof binding.ruledCardBinding, "function");
  assert.notEqual(binding.ruledCardBinding("card:11"), null);
});

test("SURFACE: the export list of all four modules is exactly enumerated", () => {
  for (const [label, namespace] of SWEPT_NAMESPACES())
    assert.deepEqual(Object.keys(namespace).sort(), [...EXPECTED_EXPORTS[label]].sort(), label);
  // No classifier, no binder, no conditional name anywhere on the surface — the
  // internal one included, since a name that would be refused in public is not
  // made acceptable by the path it sits behind.
  for (const [label, namespace] of SWEPT_NAMESPACES())
    for (const name of Object.keys(namespace)) {
      assert.ok(!/^(classify|evaluate|derive|bind|create|set|would)/.test(name),
        `${label}.${name} is a binder or classifier name on the public surface`);
      assert.ok(!/would_|_if_authoritative/.test(name), `${label}.${name} is a conditional name`);
    }
  // The ruling table, the findings vocabulary, the reason ids, the seam list and
  // the producer restatement are all gone from the surface. Naming them here
  // means a future edit that re-exports one fails on this line.
  for (const gone of ["ruledCardBinding",
    "GATE_ZERO_SEAM_RULINGS", "GATE_ZERO_SEAM_STORE_REFS", "seamRulingDecisionRef",
    "DECISION_ID", "GATE_ZERO_SEAM_FINDINGS", "GATE_ZERO_SEAM_READER_REASON_IDS",
    "GATE_ZERO_SEAM_READER_SEAMS", "GATE_ZERO_PRODUCER_SEAM_NOT_BUILT", "gateZeroSeamRulingStatus",
    "wouldAdmitPredecessorOutcome", "wouldReportSchedulerCanary", "wouldReportGateConclusion"]) {
    assert.ok(!Object.hasOwn(readers, gone), `${gone} is back on the reader surface`);
    assert.ok(!Object.hasOwn(rulings, gone), `${gone} is back on the ruling surface`);
  }
  // And the module that held the exported classifiers is gone, not hidden.
  assert.ok(!readdirSync(SRC).includes("gate-zero-seam-evidence.v5.js"),
    "the exported-classifier module is still in src");
});

/** Objects built to break a reader that reaches into them naively. */
const HOSTILE_MARKER = "HOSTILEMARKERTEXT";
function hostileQueries() {
  const throwingProxy = new Proxy({}, {
    get() { throw new Error(`${HOSTILE_MARKER}-proxy-get`); },
    has() { throw new Error(`${HOSTILE_MARKER}-proxy-has`); },
    ownKeys() { throw new Error(`${HOSTILE_MARKER}-proxy-keys`); },
  });
  const revocable = Proxy.revocable({ stepRef: "step:wr46-dissolution-outcome" }, {});
  revocable.revoke();
  const nullProto = Object.create(null);
  nullProto.stepRef = `${HOSTILE_MARKER}-null-proto`;
  return [
    throwingProxy,
    revocable.proxy,
    nullProto,
    { get stepRef() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get outcomeHash() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get serviceKey() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get canaryRunKey() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get headSha() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get checkName() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get workRequestRef() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get commitSha() { throw new Error(`${HOSTILE_MARKER}-getter`); } },
    { stepRef: `${HOSTILE_MARKER}-step`, outcomeHash: `${HOSTILE_MARKER}-hash`,
      serviceKey: `${HOSTILE_MARKER}-service`, canaryRunKey: `${HOSTILE_MARKER}-run`,
      headSha: `${HOSTILE_MARKER}-sha`, checkName: `${HOSTILE_MARKER}-check`,
      workRequestRef: `${HOSTILE_MARKER}-wr`, commitSha: `${HOSTILE_MARKER}-commit` },
    { stepRef: { toString() { throw new Error(`${HOSTILE_MARKER}-tostring`); } } },
    { stepRef: Symbol(`${HOSTILE_MARKER}-symbol`) },
    { stepRef: ["step:wr46-dissolution-outcome"], outcomeHash: [`sha256:${"a".repeat(64)}`] },
    { stepRef: "green", outcomeHash: "ok", serviceKey: "allow", canaryRunKey: "passing",
      headSha: "complete", checkName: "verified" },
  ];
}

/**
 * Values handed to an exported callable in EVERY argument position, including
 * the positions a constructor reads. hostileQueries() builds query OBJECTS,
 * which is the shape the three fetchers take; a constructor takes bare strings,
 * so the privileged words themselves are arguments here, alongside the object
 * shapes the amendment of 2026-09-11 puts in scope: a Proxy, a revoked Proxy, a
 * throwing getter, a throwing toString, a Symbol and an Error.
 */
function hostileArguments() {
  const throwingCoercion = {
    toString() { throw new Error(`${HOSTILE_MARKER}-tostring`); },
    valueOf() { throw new Error(`${HOSTILE_MARKER}-valueof`); },
  };
  const revocable = Proxy.revocable({ because: HOSTILE_MARKER }, {});
  revocable.revoke();
  return [
    undefined, null, 0, -1, true, false,
    HOSTILE_MARKER,
    `${HOSTILE_MARKER}: the checks source answer did not parse`,
    "the query did not finish ",
    ...PRIVILEGED_WORDS,
    "would_allow_if_authoritative",
    "the gate is green and the run is ok",
    Symbol(`${HOSTILE_MARKER}-symbol`),
    throwingCoercion,
    revocable.proxy,
    new Error(`${HOSTILE_MARKER}-cause`),
    { because: "green", store_ref: "allow", message: HOSTILE_MARKER, stack: HOSTILE_MARKER },
    ["allow", HOSTILE_MARKER],
    ...hostileQueries(),
  ];
}

/**
 * Argument lists for a constructor: each hostile value alone, in every position
 * at once, and behind a well-formed prefix — because a leak that needs the first
 * argument to be valid is still a leak, and the earlier special case only ever
 * tried the all-valid list.
 */
function constructorArgumentLists() {
  const lists = [[]];
  for (const hostile of hostileArguments()) {
    lists.push([hostile], [hostile, hostile], [hostile, hostile, hostile]);
    lists.push(["github:checks", hostile, hostile]);
    lists.push(["github:checks", "the query did not finish", hostile]);
  }
  return lists;
}

/**
 * Two structural questions, asked of a value without invoking it.
 *
 * EVERY ORDINARY FUNCTION IS A CONSTRUCTOR — that is the trap the first draft of
 * this sweep fell into. `Reflect.construct` accepts `seamRulingRef` as happily
 * as it accepts a class, so "is it constructible" cannot be the branch on its
 * own. What separates a class is its `prototype`: the engine installs it
 * non-writable on a class and writable on a function, and nothing in this file
 * has to trust a name or a `toString` to see it.
 */
function isConstructor(value) {
  try {
    Reflect.construct(String, [], value);
    return true;
  } catch {
    return false;
  }
}

function isClassConstructor(value) {
  const descriptor = Object.getOwnPropertyDescriptor(value, "prototype");
  return isConstructor(value) && descriptor !== undefined && descriptor.writable === false;
}

/**
 * new.targets A CALLER CHOOSES, which is the half of construction the earlier
 * sweep never supplied. `Reflect.construct(x, args, newTarget)` makes the ENGINE
 * read `newTarget.prototype` — so a caller's object is read on the way in, and
 * whatever the constructed value inherits comes from the caller.
 *
 *   throws-on-prototype  the read itself throws the caller's own text. A plain
 *                        function's `prototype` is non-configurable, so the
 *                        accessor cannot be planted directly; a Proxy over a
 *                        function IS a constructor, and its `get` trap is
 *                        exactly where the engine's read lands.
 *   marker-prototype     the read succeeds and hands back an object carrying the
 *                        caller's marker, so anything built against it carries
 *                        the marker on its chain.
 *   foreign              `Object`, the probe the fourth round used.
 */
function hostileNewTargets() {
  const throwsOnPrototype = new Proxy(function () {}, {
    get(target, key, receiver) {
      if (key === "prototype") throw new Error(`${HOSTILE_MARKER}-new-target-prototype`);
      return Reflect.get(target, key, receiver);
    },
  });
  function markerTarget() {}
  markerTarget.prototype = Object.freeze({ marker: `${HOSTILE_MARKER}-new-target-prototype` });
  return [["throws-on-prototype", throwsOnPrototype], ["marker-prototype", markerTarget],
    ["foreign", Object]];
}

/** Left operands built to make `instanceof` run the caller's own code. */
function hostileInstanceOperands() {
  const selfReferencing = new Proxy({}, { getPrototypeOf() { return selfProxy; } });
  const selfProxy = selfReferencing;
  const revocable = Proxy.revocable({}, {});
  revocable.revoke();
  return [
    ["throws-a-string", new Proxy({}, { getPrototypeOf() { throw "allow"; } })],
    ["throws-the-caller-text", new Proxy({}, {
      getPrototypeOf() { throw new Error(`${HOSTILE_MARKER}-get-prototype-of`); } })],
    ["throws-a-true", new Proxy({}, { getPrototypeOf() { throw true; } })],
    ["revoked", revocable.proxy],
    // A chain that never ends: an unbounded walk over it does not return at all,
    // and a hang is a refusal the caller chose rather than one a module wrote.
    ["never-ends", selfReferencing],
    ["a-plain-object", {}],
    ["a-real-error", new Error("plain")],
    ["a-null-prototype", Object.create(null)],
  ];
}

/**
 * The marker is the whole question amendment 2 of 2026-09-12 leaves open for
 * construction. The ENGINE'S refusal of a non-constructor — its TypeError, its
 * message and its stack of the caller's own frames — is the engine's and is NOT
 * a finding, because no line of the module ran to produce it. What must still be
 * true is that no line of the module ran AT ALL: nothing read the caller's
 * newTarget, nothing built against its prototype, and nothing carries its text.
 */
function markerInChain(value) {
  try {
    let walked = value;
    for (let step = 0; step < 8 && walked !== null && walked !== undefined; step += 1) {
      for (const key of safeOwnKeys(walked))
        if (safeLabel(safeRead(walked, key)).includes(HOSTILE_MARKER)) return true;
      walked = Reflect.getPrototypeOf(Object(walked));
    }
    return false;
  } catch {
    return true;
  }
}

function assertNoModuleCodeRan(at, value) {
  assert.ok(!String(safeRead(value, "message") ?? "").includes(HOSTILE_MARKER),
    `${at}: module code ran and carried the caller's own text back`);
  assert.equal(markerInChain(value), false,
    `${at}: the caller's newTarget was read, and what came back is built on it`);
}

/**
 * THE CLOSED SHAPE, asked of one exported callable, clause by clause. Amendment
 * 2 of 2026-09-12 after the fifth review round on PR 1001:
 *
 *   (a) an arrow or a bound function — non-constructable, no `.prototype` — so
 *       no `newTarget.prototype` is ever read and no raw target is reachable
 *       through `prototype.constructor`;
 *   (b) `Symbol.hasInstance` as a non-writable, non-configurable DATA property
 *       whose function returns false WITHOUT READING its argument, so the
 *       intrinsic OrdinaryHasInstance never walks a caller's chain.
 *
 * The guard function itself is asked the same question the fifth round asked of
 * the one on the error type: a retrievable guard that is constructable is
 * another callable on the surface, reachable by a route nobody enumerated.
 */
function assertClosedCallable(at, exported) {
  assert.equal(typeof exported, "function", `${at} is not callable`);

  assert.equal(Object.hasOwn(exported, "prototype"), false,
    `${at} carries an own prototype, so a foreign new.target has something to be read against`);
  assert.equal(exported.prototype, undefined, `${at} reaches a prototype through its chain`);
  assert.equal(isConstructor(exported), false, `${at} is still a constructor`);
  for (const [label, newTarget] of hostileNewTargets()) {
    let built;
    try {
      built = Reflect.construct(exported, [HOSTILE_MARKER, "green", { ok: true }], newTarget);
    } catch (thrown) {
      assertNoModuleCodeRan(`${at}.${label}.threw`, thrown);
      // AND THE ENGINE'S OWN REFUSAL CARRIES NO LINE OF THE MODULE. The
      // amendment puts the engine's TypeError out of scope, and it is still
      // worth choosing which one the engine builds: its refusal of a BARE arrow
      // quotes that arrow's SOURCE TEXT back — the module's own body, privileged
      // substrings and all — while its refusal of a BOUND one names
      // `function () { [native code] }`. That is why every export is bound.
      assert.ok(!String(safeRead(thrown, "message") ?? "").includes("=>"),
        `${at}.${label}: the engine's refusal quotes the module's own source back`);
      continue;
    }
    assertNoModuleCodeRan(`${at}.${label}.returned`, built);
    assert.fail(`${at}.${label} constructed a value out of something that is not a constructor`);
  }

  const guard = Object.getOwnPropertyDescriptor(exported, Symbol.hasInstance);
  assert.ok(guard !== undefined,
    `${at} answers instanceof with the intrinsic, which walks the operand's chain`);
  assert.ok(Object.hasOwn(guard, "value"), `${at}'s hasInstance is an accessor, not a data property`);
  assert.equal(guard.writable, false, `${at}'s hasInstance is writable`);
  assert.equal(guard.configurable, false, `${at}'s hasInstance is configurable`);
  assert.equal(isConstructor(guard.value), false, `${at}'s hasInstance guard is itself constructable`);
  assert.equal(Object.hasOwn(guard.value, "prototype"), false,
    `${at}'s hasInstance guard carries a prototype of its own`);

  for (const [label, hostile] of hostileInstanceOperands()) {
    let outcome = "threw";
    try { outcome = hostile instanceof exported; } catch (thrown) { outcome = thrown; }
    assert.equal(outcome, false, `${at}.${label}: instanceof threw, or answered true`);
  }
  // AND IT DOES NOT LOOK AT THE OPERAND AT ALL, which is stronger than answering
  // false about it: a guard that walks a chain to decide can be steered by what
  // it walks.
  let looked = false;
  const watched = new Proxy({}, { getPrototypeOf() { looked = true; return null; } });
  assert.equal(watched instanceof exported, false, `${at} answered a membership question true`);
  assert.equal(looked, false, `${at}'s hasInstance read the left operand`);
}

/**
 * The error factory, swept the way the class used to be: every hostile value in
 * every argument position, and every produced error checked for a route back to
 * a constructor the caller could reach.
 */
function sweepErrorFactory(at, factory) {
  assertClosedCallable(at, factory);
  for (const argumentList of constructorArgumentLists()) {
    let built;
    try {
      built = factory(...argumentList);
    } catch (error) {
      assertSwept(`${at}.throw.message`, error?.message ?? null);
      assertSwept(`${at}.throw.because`, error?.because ?? null);
      assert.ok(!String(error?.message ?? "").includes(HOSTILE_MARKER),
        `${at} quoted the caller back in its own refusal`);
      continue;
    }
    assertSweptConstructed(`${at}.made[${argumentList.length}]`, built);
    assertNoRawRouteFrom(`${at}.made`, built, factory);
  }
}

/**
 * A PRODUCED ERROR IS NOT A ROUTE BACK TO ITS CLASS. `prototype.constructor`
 * carries the class every engine installs there, and the fourth round reached it
 * from an instance — so the chain is walked and every `constructor` on it has to
 * be the factory or an intrinsic. An intrinsic is not a route to THIS module's
 * type; anything else is.
 */
function assertNoRawRouteFrom(at, built, factory) {
  let walked = built;
  for (let step = 0; step < 8 && walked !== null && walked !== undefined; step += 1) {
    const descriptor = Object.getOwnPropertyDescriptor(Object(walked), "constructor");
    if (descriptor !== undefined) {
      assert.ok(Object.hasOwn(descriptor, "value"), `${at} reaches an accessor at .constructor`);
      const reached = descriptor.value;
      assert.ok(reached === factory || reached === Error || reached === Object,
        `${at} reaches a constructor this module owns through .constructor`);
      if (reached === factory) {
        assert.equal(descriptor.writable, false, `${at}.constructor is writable`);
        assert.equal(descriptor.configurable, false, `${at}.constructor is configurable`);
      }
    }
    walked = Reflect.getPrototypeOf(Object(walked));
  }
}

/**
 * One constructed object, against the whole contract this surface owes, in the
 * order a failure is most informative:
 *
 *   1. THE CAUSE IS NOT KEPT. A retained `cause` is the caller's whole object,
 *      handed back under a standard name.
 *   2. NOTHING THE CALLER WROTE, AND NO WORD OF THE CLOSED UNION, in the name,
 *      the message, the stack, the cause or any own property.
 *   3. THE SHAPE. Every own property is a non-writable, non-configurable DATA
 *      property — an accessor under one of these names runs caller code on
 *      every read, and a configurable property can become one afterwards — and
 *      an object carrying any own property at all is frozen, so nothing can be
 *      written over what it says.
 */
function assertSweptConstructed(at, built) {
  assert.ok(built !== null && typeof built === "object", `${at} did not construct an object`);
  // THE SAME IDENTITY THE VALUE SWEEP USES, and it is here because construction
  // now reaches the readers: `new readGateConclusionEvidence()` answers with the
  // GATE'S OWN object, which is upstream's and carries upstream's strings. Identity
  // admits no new string at all, which is stronger than sweeping its words.
  if (PRE_PR_GATE_ANSWER_DIGESTS.has(digest(built))) return;
  const keys = Reflect.ownKeys(built);

  assert.ok(!Object.hasOwn(built, "cause"), `${at} carries an own cause`);
  assert.equal(built.cause, undefined, `${at} retained the caller's cause`);

  for (const key of ["name", "message", "stack", "cause"])
    assertSwept(`${at}.${key}`, built[key] ?? null);
  for (const key of keys) assertSwept(`${at}.own.${String(key)}`, built[key]);
  const readable = `${built.name}|${built.message}|${built.stack}|${safeLabel({ ...built })}`;
  assert.ok(!readable.includes(HOSTILE_MARKER), `${at} leaked caller text: ${readable.slice(0, 200)}`);

  for (const key of keys) {
    const where = `${at}.${String(key)}`;
    assert.ok(typeof key !== "symbol", `${where} is a symbol-keyed own property`);
    const descriptor = Object.getOwnPropertyDescriptor(built, key);
    assert.ok(Object.hasOwn(descriptor, "value"), `${where} is an accessor, not a data property`);
    assert.equal(descriptor.writable, false, `${where} is writable`);
    assert.equal(descriptor.configurable, false, `${where} is configurable`);
  }
  if (keys.length > 0) assert.ok(Object.isFrozen(built), `${at} is not frozen`);
}

test("SWEEP: every export of every seam module, constants and callables alike", async () => {
  const saved = {};
  for (const name of ["DATABASE_URL_READER", "GITHUB_TOKEN", "GITHUB_REPOSITORY"]) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  try {
    for (const [label, namespace] of SWEPT_NAMESPACES()) {
      for (const [name, value] of Object.entries(namespace)) {
        const at = `${label}.${name}`;
        if (typeof value !== "function") { assertSwept(at, value); continue; }
        // EVERY WAY AN EXPORT CAN BE INVOKED IS SWEPT, and after the fifth round
        // there is exactly one way: a CALL. No export is a constructor — the
        // class is module-private, and every reader, fetcher, factory and lookup
        // is a bound arrow — so the branch that used to route a class one way
        // and a function another is replaced by the assertion that neither route
        // exists. The error factory is swept through its own door below, with
        // every hostile value in every argument position.
        for (const argument of [undefined, ...hostileQueries()]) {
          let outcome;
          try {
            outcome = await value(argument);
          } catch (error) {
            assertSwept(`${at}.throw.message`, error?.message ?? null);
            assertSwept(`${at}.throw.because`, error?.because ?? null);
            continue;
          }
          assertSwept(`${at}(${safeLabel(argument)})`, outcome);
          assert.ok(!JSON.stringify(outcome ?? null).includes(HOSTILE_MARKER),
            `${at} leaked caller text`);
        }
      }
    }
    sweepErrorFactory("stores.seamStoreUnreachable", stores.seamStoreUnreachable);
  } finally {
    for (const [name, value] of Object.entries(saved))
      if (value !== undefined) process.env[name] = value;
  }
});

test("STORE ERROR: the error carries registered codes only, and nothing the caller wrote", () => {
  const registered = "the query did not finish";
  const carried = stores.seamStoreUnreachable("github:checks", registered,
    new Error(`${HOSTILE_MARKER}-underlying`));
  assert.equal(carried.store_ref, "github:checks");
  assert.equal(carried.because, registered);
  assert.equal(carried.message, `github:checks: ${registered}`);
  assert.equal(carried.stack, `SeamStoreUnreachable: github:checks: ${registered}`,
    "the stack is a caller's frames again");
  assert.equal(carried.cause, undefined, "the underlying cause is retained again");
  assert.equal(carried.cause_kind, "an-error");

  // AN UNREGISTERED ARGUMENT IS REPLACED, NOT QUOTED. The old constructor threw
  // a TypeError reciting the word it had just refused, which is how a privileged
  // string left an exported callable.
  const refused = stores.seamStoreUnreachable("allow", "green", { ok: true });
  assert.equal(refused.store_ref, "a-store-this-file-does-not-serve");
  assert.equal(refused.because, "the reason this store was unreachable is not a registered one");
  assert.equal(refused.message,
    "a-store-this-file-does-not-serve: the reason this store was unreachable is not a registered one");
  assert.equal(refused.cause, undefined);
  assert.equal(refused.cause_kind, "not-an-error");
  assertSwept("refused", refused);
  assertSwept("refused.message", refused.message);
  assertSwept("refused.stack", refused.stack);

  // A revoked Proxy as the cause: the prototype walk throws, and the answer to
  // that is this file's own code rather than the engine's sentence.
  const revocable = Proxy.revocable({}, {});
  revocable.revoke();
  assert.equal(stores.seamStoreUnreachable("github:checks", registered, revocable.proxy).cause_kind,
    "undetermined");

  // And no field can be replaced after the fact.
  assert.throws(() => { refused.because = registered; }, TypeError);
  assert.throws(() => Object.defineProperty(refused, "because", { value: registered }), TypeError);
  assert.throws(() => Object.defineProperty(refused, "stack", { get: () => HOSTILE_MARKER }), TypeError);
});

test("STORE ERROR: a setter on the prototype chain cannot stand in for a field", () => {
  // The fields are installed with defineProperty rather than assigned, and the
  // difference is only visible when something is listening: a plain
  // `this.store_ref = store` would call a setter Error.prototype happened to
  // carry, the own property would never be installed, and every read of the
  // field would return whatever that setter's partner getter wanted to say.
  //
  // Planting one here is a MEASUREMENT, not a threat model — the amendment of
  // 2026-09-11 puts mutation of an intrinsic out of scope as an attack — and it
  // is what makes "installed as data properties" an observable requirement
  // rather than one that assigning-and-freezing satisfies by accident.
  const assigned = [];
  const fields = ["store_ref", "because", "cause_kind"];
  for (const key of fields)
    Object.defineProperty(Error.prototype, key, {
      configurable: true,
      set(value) { assigned.push(value); },
      get() { return `${HOSTILE_MARKER}-from-the-prototype`; },
    });
  try {
    const error = stores.seamStoreUnreachable("github:checks", "the query did not finish");
    assert.deepEqual(assigned, [], "a field was assigned through a prototype setter");
    for (const key of fields) assert.ok(Object.hasOwn(error, key), `${key} is not an own property`);
    assert.equal(error.store_ref, "github:checks");
    assert.equal(error.because, "the query did not finish");
    assert.equal(error.cause_kind, "none");
    assertSweptConstructed("prototype-setter", error);
  } finally {
    for (const key of fields) delete Error.prototype[key];
  }
  assert.equal(Object.hasOwn(Error.prototype, "store_ref"), false, "the plant was not removed");
});

test("SWEEP CONTROL: each assertion the constructor sweep makes has been seen to fail", () => {
  // A sweep nobody has seen fail is a sweep nobody has tested, and this one is
  // new. EIGHT PLANTS — counted as the loop below executes them, which is the
  // only count that proves anything — each differing from the shipped class in
  // exactly ONE way, so no working assertion can cover for a broken one. Plant 1
  // is the code this correction replaced, verbatim; the other seven isolate one
  // clause each. Seven CLASSES are declared for the eight, because the last two
  // plants differ in how the same class is exported rather than in its body.
  const REASONS = Object.freeze(["the query did not finish", "the checks source was not reachable",
    "the reason this store was unreachable is not a registered one"]);
  const TOKENS = Object.freeze(["github:checks", "a-store-this-file-does-not-serve"]);
  const fixed = (storeRef, because) => [
    TOKENS.includes(storeRef) ? storeRef : "a-store-this-file-does-not-serve",
    REASONS.includes(because) ? because : "the reason this store was unreachable is not a registered one",
  ];
  const install = (target, key, value, enumerable) =>
    Object.defineProperty(target, key, { value, writable: false, enumerable, configurable: false });
  const text = value => (typeof value === "string" ? value : "an argument of another type");

  // 1 — the constructor that shipped, exactly as the re-review found it.
  class Interpolating extends Error {
    constructor(storeRef, because, cause) {
      super(`${storeRef}: ${because}`, cause === undefined ? undefined : { cause });
      this.name = "SeamStoreUnreachable";
      this.store_ref = storeRef;
      this.because = because;
    }
  }

  // 2 — right shape, right filter, and it still echoes the caller's strings.
  class EchoesCaller extends Error {
    constructor(storeRef, because) {
      if (new.target !== EchoesCaller) throw new TypeError("final");
      const message = `${text(storeRef)}: ${text(because)}`;
      super(message);
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", message, false);
      install(this, "stack", `SeamStoreUnreachable: ${message}`, false);
      install(this, "store_ref", text(storeRef), true);
      install(this, "because", text(because), true);
      Object.freeze(this);
    }
  }

  // 3 — refuses an unregistered reason by quoting it back, which is what the
  // old TypeError did and what made a privileged word leave the module.
  class QuotesTheRefusal extends Error {
    constructor(storeRef, because) {
      if (new.target !== QuotesTheRefusal) throw new TypeError("final");
      if (!REASONS.includes(because))
        throw new TypeError(`${String(because)} is not a registered store-unreachable reason`);
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`);
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", `SeamStoreUnreachable: ${store}: ${reason}`, false);
      install(this, "store_ref", store, true);
      install(this, "because", reason, true);
      Object.freeze(this);
    }
  }

  // 4 — filters both arguments, and hands the third one back untouched.
  class KeepsCause extends Error {
    constructor(storeRef, because, cause) {
      if (new.target !== KeepsCause) throw new TypeError("final");
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`, cause === undefined ? undefined : { cause });
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", `SeamStoreUnreachable: ${store}: ${reason}`, false);
      install(this, "store_ref", store, true);
      install(this, "because", reason, true);
      Object.freeze(this);
    }
  }

  // 5 — a REAL engine stack: a list of the caller's file paths and frame names,
  // bytes this module did not write, swept like any other unfixed text.
  class EngineStack extends Error {
    constructor(storeRef, because) {
      if (new.target !== EngineStack) throw new TypeError("final");
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`);
      const engineStack = this.stack;
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", engineStack, false);
      install(this, "store_ref", store, true);
      install(this, "because", reason, true);
      Object.freeze(this);
    }
  }

  // 6 — every value it yields is registered; the SHAPE is the defect, because an
  // accessor runs code that is not this module's on every read of the field.
  class AccessorField extends Error {
    constructor(storeRef, because) {
      if (new.target !== AccessorField) throw new TypeError("final");
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`);
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", `SeamStoreUnreachable: ${store}: ${reason}`, false);
      install(this, "store_ref", store, true);
      Object.defineProperty(this, "because", { get: () => reason, enumerable: true });
      Object.freeze(this);
    }
  }

  // The body behind plants 7 and 8: everything above it, minus the `new.target`
  // check. The two plants differ in how it is EXPORTED, not in what it does.
  class Subclassable extends Error {
    constructor(storeRef, because) {
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`);
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", `SeamStoreUnreachable: ${store}: ${reason}`, false);
      install(this, "store_ref", store, true);
      install(this, "because", reason, true);
      Object.freeze(this);
    }
  }

  // EACH PLANT IS REACHED THE WAY THE MODULE IS NOW REACHED: through a bound
  // arrow factory, because a class is no longer something a caller can hold.
  // `closed` is this file's own restatement of the module's `closedCallable` —
  // bind, then deny `instanceof` — and it is here so that a plant differs from
  // the shipped shape in exactly ONE way rather than also failing the clauses
  // every unclosed value would fail.
  const closed = callable => {
    const bound = callable.bind(null);
    Object.defineProperty(bound, Symbol.hasInstance,
      { value: () => false, writable: false, enumerable: false, configurable: false });
    return bound;
  };
  const factoryFor = plant => {
    const exported = closed((...args) => new plant(...args));
    Object.defineProperty(plant.prototype, "constructor",
      { value: exported, writable: false, enumerable: false, configurable: false });
    return exported;
  };

  for (const [label, factory] of [
    ["interpolating", factoryFor(Interpolating)],
    ["echoes-caller", factoryFor(EchoesCaller)],
    ["quotes-the-refusal", factoryFor(QuotesTheRefusal)],
    ["keeps-cause", factoryFor(KeepsCause)],
    ["engine-stack", factoryFor(EngineStack)],
    ["accessor-field", factoryFor(AccessorField)],
    // 7 — every value it yields is registered and every shape clause holds. The
    //     ROUTE is the defect: `prototype.constructor` is left as the class the
    //     engine installed, so any refusal it produces hands a caller back a
    //     constructor to aim a new.target at. That is the fourth round's finding
    //     (2), and nothing about the VALUES it returns is wrong.
    ["leaks-the-class", closed((...args) => new Subclassable(...args))],
    // 8 — the class itself, exported the way the first draft exported it:
    //     constructable, carrying a prototype, callable by anybody.
    ["the-class-itself", Subclassable],
  ])
    assert.throws(() => sweepErrorFactory(`control.${label}`, factory), undefined,
      `the sweep passed the ${label} plant, so the assertion it is planted against proves nothing`);

  // The calibration: the shipped factory goes through the same function untouched.
  assert.doesNotThrow(() => sweepErrorFactory("control.shipped", stores.seamStoreUnreachable));

  // And the structural fact the whole sweep now rests on: NOTHING on these three
  // surfaces is a constructor at all, so there is no second route to sweep.
  for (const callable of [stores.seamStoreUnreachable, stores.isSeamStoreUnreachable,
    stores.fetchPredecessorOutcomeRows, stores.fetchSchedulerLedgerRows,
    stores.fetchCheckConclusionRows, readers.readGateConclusionEvidence,
    readers.readPredecessorOutcomeEvidence, readers.readSchedulerCanaryEvidence,
    rulings.seamRulingRef]) {
    assert.equal(isClassConstructor(callable), false);
    assert.equal(isConstructor(callable), false, "an exported callable is constructible again");
  }
});

test("SWEEP: the module's registered reason set is the one this file restates", () => {
  // The reason set is module-private on purpose — an importer that could
  // enumerate it could assemble a message out of it and hand it back in. So the
  // contract is restated here and checked against the source, which is the only
  // honest way to have both.
  const source = readFileSync(join(SRC, STORES_FILE), "utf8");
  const registry = /const UNREACHABLE_REASONS = Object\.freeze\(\{([\s\S]*?)\}\);/.exec(source);
  assert.ok(registry, "the store's reason registry is no longer where this test reads it");
  const declared = [...registry[1].matchAll(/"([^"]+)"/g)].map(one => one[1]);
  assert.deepEqual([...declared].sort(), [...STORE_UNREACHABLE_REASONS].sort(),
    "a store-unreachable reason was added or removed without the contract following it");
});

test("SWEEP: a native error or a raw thrown value never leaves an export", async () => {
  // FINDING 1 OF THE THIRD RE-REVIEW. Nothing here is new about the arguments —
  // it is the same hostile set — and everything is new about what is looked at:
  // the call goes through a caller whose frame name is a privileged word, and
  // whatever comes back out of the throwing door is inspected whole.
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    for (const [label, namespace] of SWEPT_NAMESPACES())
      await sweepEveryInvocation(label, namespace);
  } finally {
    restoreEnv(saved);
  }
});

test("SURFACE: no export can be constructed, and none reads a caller's newTarget", async () => {
  // FINDING 1 OF THE FIFTH RE-REVIEW, and the shape amendment 2 of 2026-09-12
  // rules for it. The fourth correction answered construction from INSIDE the
  // module — a proxy construct trap, a `new.target` branch in the ruling lookup —
  // and that is already too late: the engine reads `newTarget.prototype` BEFORE
  // any body runs, so a caller's object was read on the way in, and a proxy
  // forwards `get`, so `exported.prototype.constructor` handed the raw target
  // back to be constructed with a new.target of the caller's choosing.
  //
  // The closed shape is the answer: a bound arrow has no [[Construct]] and no
  // `prototype`, so the ENGINE refuses in the caller's own frame having run no
  // line of the module. That refusal is the engine's and is not a finding. What
  // is asserted is what the amendment leaves the author owing — the shape holds,
  // and no module code ran against the caller's newTarget.
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    let callables = 0;
    for (const [label, namespace] of SWEPT_NAMESPACES())
      for (const [name, value] of Object.entries(namespace)) {
        if (typeof value !== "function") continue;
        callables += 1;
        assertClosedCallable(`${label}.${name}`, value);
      }
    // The SEVEN readers and fetchers — three readers, three card fetchers and the
    // candidate-build record fetcher amendment 9 added — the factory, the store
    // predicate, the lookup and, since the PR 1004 re-review, the ruling
    // predicate the gate binds its seams on: an export that stopped being one of
    // them fails here rather than quietly skipping the loop above.
    assert.equal(callables, 11, "the callable surface moved without this count following it");

    // AND THE CALLING DOOR STILL ANSWERS, which is the thing the construction
    // door must not have cost. Before Joe's paste this was pinned to the gate's
    // own digest; the readers are ruled now, so what is pinned is the closed
    // half — a refusal that admits nothing, names its own card's ruling, and
    // carries none of the caller's bytes.
    for (const [index, [name, reader]] of READERS_UNDER_TEST.entries()) {
      const answered = await reader({ headSha: HOSTILE_MARKER });
      assert.equal(answered.decision, "refuse", name);
      assert.equal(answered.finding, null, name);
      assert.equal(answered.caller_evidence_admitted, false, name);
      assert.equal(answered.ruling_decision_ref, PASTED_DECISION_IDS[index], name);
      assert.ok(!JSON.stringify(answered).includes(HOSTILE_MARKER), name);
    }
  } finally {
    restoreEnv(saved);
  }
});

test("SURFACE CONTROL: each clause of the closed-callable check has been seen to fail", () => {
  // A check nobody has seen fail is a check nobody has tested, and every clause
  // here is new. ELEVEN PLANTS, one per clause, each differing from the shipped
  // shape in exactly one way.
  const deny = (callable, value) => {
    Object.defineProperty(callable, Symbol.hasInstance, value);
    return callable;
  };
  const flat = { value: () => false, writable: false, enumerable: false, configurable: false };
  const closed = callable => deny(callable.bind(null), flat);

  const plants = [
    // 1 — an ordinary function: constructable, and carrying the `prototype` a
    //     foreign new.target is read against.
    ["a-plain-function", deny(function plain() {}, flat)],
    // 2 — a class: the shape the first draft exported.
    ["a-class", deny(class Plain {}, flat)],
    // 3 — the proxy wrapper the fourth correction shipped: a proxy over a plain
    //     function IS a constructor, and it forwards `get`.
    ["a-proxy-over-a-function", deny(new Proxy(function () {}, {}), flat)],
    // 4 — bound and non-constructable, and it answers instanceof with the
    //     intrinsic, which walks the caller's own prototype chain.
    ["no-hasInstance", (() => {}).bind(null)],
    // 5 — closed in every other way, and BARE rather than bound: the engine's
    //     refusal of it quotes the function's own source text back.
    ["a-bare-arrow", deny(query => query, flat)],
    // 6 — a guard that is an accessor: it runs code on every read of the slot.
    ["hasInstance-accessor", deny((() => {}).bind(null), { get: () => () => false, configurable: true })],
    // 7 — a guard that can be replaced.
    ["hasInstance-writable", deny((() => {}).bind(null), { ...flat, writable: true })],
    // 8 — a guard that can be redefined as an accessor afterwards.
    ["hasInstance-configurable", deny((() => {}).bind(null), { ...flat, configurable: true })],
    // 9 — a guard that is itself a constructable callable, which is the fifth
    //     round's second standards finding: the retrieved function was another
    //     door nobody had enumerated.
    ["hasInstance-constructable", deny((() => {}).bind(null), { ...flat, value: function () { return false; } })],
    // 10 — a guard that answers the membership question yes.
    ["hasInstance-answers-true", deny((() => {}).bind(null), { ...flat, value: () => true })],
    // 11 — a guard that LOOKS at the operand: it answers correctly for innocent
    //      values and runs the caller's getPrototypeOf trap for hostile ones.
    ["hasInstance-reads-the-operand",
      deny((() => {}).bind(null), { ...flat, value: x => x instanceof Error })],
  ];
  for (const [label, plant] of plants)
    assert.throws(() => assertClosedCallable(`control.${label}`, plant), undefined,
      `the check passed the ${label} plant, so the clause it is planted against proves nothing`);

  // AND THE MARKER CLAUSE, asked directly, because it is the one clause no
  // non-constructor can reach: a value that DID run module code against the
  // caller's newTarget carries the caller's text, either in what it says or on
  // the chain it was built against.
  assert.throws(() => assertNoModuleCodeRan("control.said-it",
    new Error(`${HOSTILE_MARKER}-from-a-getter`)));
  assert.throws(() => assertNoModuleCodeRan("control.built-on-it",
    Object.create(Object.freeze({ marker: `${HOSTILE_MARKER}-new-target-prototype` }))));
  assert.doesNotThrow(() => assertNoModuleCodeRan("control.engine-refusal",
    new TypeError("function () { [native code] } is not a constructor")));

  // The calibration: a shipped export goes through the same check untouched.
  for (const [label, exported] of [["stores.seamStoreUnreachable", stores.seamStoreUnreachable],
    ["stores.isSeamStoreUnreachable", stores.isSeamStoreUnreachable],
    ["stores.fetchCheckConclusionRows", stores.fetchCheckConclusionRows],
    ["readers.readGateConclusionEvidence", readers.readGateConclusionEvidence],
    ["rulings.seamRulingRef", rulings.seamRulingRef]])
    assert.doesNotThrow(() => assertClosedCallable(label, exported));

  // And the probes themselves are real: a target that DOES read its newTarget
  // gets the caller's object, which is what the plants above stand in for.
  function readsTheNewTarget() { return Object.create(new.target.prototype); }
  const [, markerTarget] = hostileNewTargets()[1];
  assert.equal(markerInChain(Reflect.construct(readsTheNewTarget, [], markerTarget)), true,
    "the marker newTarget no longer reaches what is built against it, so the probe proves nothing");
  const [, throwsOnPrototype] = hostileNewTargets()[0];
  assert.throws(() => Reflect.construct(readsTheNewTarget, [], throwsOnPrototype),
    error => String(error.message).includes(HOSTILE_MARKER),
    "the throwing-prototype newTarget no longer throws the caller's text");
});

test("STORE ERROR: the class is unreachable, and membership is a predicate rather than instanceof",
  () => {
    // FINDING 2 OF THE FIFTH RE-REVIEW, and the two clauses of amendment 2 that
    // answer it: the class is module-private and what is exported is a factory,
    // so there is nothing to construct, nothing to subclass and no raw class to
    // reach — and `instanceof` against an exported callable answers false without
    // walking anything, so the honest membership question needs its own name.
    const registered = "the query did not finish";
    const built = stores.seamStoreUnreachable("github:checks", registered);
    assert.equal(built.because, registered);
    assert.equal(built.store_ref, "github:checks");

    // (a) NO ROUTE BACK TO A CONSTRUCTOR, from the factory or from an instance.
    assertClosedCallable("stores.seamStoreUnreachable", stores.seamStoreUnreachable);
    assertNoRawRouteFrom("stores.seamStoreUnreachable.made", built, stores.seamStoreUnreachable);
    assert.equal(built.constructor, stores.seamStoreUnreachable,
      "an instance reaches something other than the factory at .constructor");
    const prototype = Object.getPrototypeOf(built);
    assert.equal(Object.isFrozen(prototype), true, "the type's prototype can be written again");
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "constructor");
    assert.ok(Object.hasOwn(descriptor, "value"), "constructor is an accessor on the prototype");
    assert.equal(descriptor.writable, false, "prototype.constructor is writable");
    assert.equal(descriptor.configurable, false, "prototype.constructor is configurable");
    assert.equal(isConstructor(descriptor.value), false,
      "prototype.constructor is a constructor again, which is the raw route");

    // (b) THE PREDICATE IS THE MEMBERSHIP QUESTION, and `instanceof` is not.
    assert.equal(stores.isSeamStoreUnreachable(built), true,
      "a real refusal stopped answering the predicate");
    assert.equal(built instanceof stores.seamStoreUnreachable, false,
      "the factory answered a membership question about a value");
    for (const innocent of [{}, null, undefined, 0, "a string", new Error("plain"),
      Object.create(null), stores.seamStoreUnreachable])
      assert.equal(stores.isSeamStoreUnreachable(innocent), false,
        `${safeLabel(innocent)} answered the predicate`);
    // A LOOKALIKE IS NOT ONE, which is what makes the predicate a type question
    // rather than a name check a caller could satisfy by writing the name.
    assert.equal(stores.isSeamStoreUnreachable(Object.freeze({
      name: "SeamStoreUnreachable", because: "green", store_ref: "github:checks",
      stack: "SeamStoreUnreachable: github:checks: green",
    })), false, "the predicate reads a name a caller wrote");

    // And nothing a caller can shape makes it throw, or makes it hang.
    for (const [label, hostile] of hostileInstanceOperands()) {
      let outcome = "threw";
      try { outcome = stores.isSeamStoreUnreachable(hostile); } catch (error) { outcome = error; }
      assert.equal(outcome, false, `${label}: the predicate threw, or answered true`);
    }
  });

test("STORES: a head sha that is not one never reaches the URL, and the path stays here", async () => {
  // FINDING 3 OF THE FOURTH RE-REVIEW. `addressed` asked only for a nonempty
  // string, and the value went into a URL PATH — so a caller that named no
  // repository at all still chose one, by traversing out of this one.
  const sha = "a".repeat(40);
  const traversal = `../../../foreign-owner/foreign-repo/commits/${sha}`;

  // THE PROBE IS REAL, and this line is what keeps it real: the template the
  // store used to build normalizes to a foreign repository's check runs. If a
  // future URL library stopped normalizing, this test would prove nothing, and
  // it would say so here rather than passing quietly.
  assert.equal(
    new URL(`https://api.github.com/repos/${AUTHORITATIVE_REPOSITORY}/commits/${traversal}/check-runs`)
      .pathname,
    `/repos/foreign-owner/foreign-repo/commits/${sha}/check-runs`,
    "the traversal probe no longer escapes, so this test proves nothing");

  const refused = "the addressed head sha is not the shape this file serves";
  const savedEnv = saveEnv(STORE_CREDENTIALS);
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = fakeChecksSource(HOSTILE_CHECK_RUNS, calls);
  process.env.GITHUB_TOKEN = "a-token-this-test-wrote";
  try {
    for (const [label, headSha] of [
      ["traversal", traversal],
      ["traversal-with-encoded-slashes", `..%2F..%2Fforeign-owner/foreign-repo/commits/${sha}`],
      ["an-absolute-url", `https://api.github.com/repos/foreign-owner/foreign-repo/commits/${sha}`],
      ["an-appended-query", `${sha}/check-runs?check_name=x&per_page=1&`],
      ["a-fragment", `${sha}#/../../foreign-owner/foreign-repo`],
      ["upper-case-hex", "A".repeat(40)],
      ["one-character-too-many", `${sha}a`],
      ["one-character-short", sha.slice(1)],
      ["not-hexadecimal", "g".repeat(40)],
      ["forty-spaces", " ".repeat(40)],
      ["a-privileged-word", "green"],
    ])
      await assert.rejects(
        () => stores.fetchCheckConclusionRows({ headSha, checkName: "main canary" }),
        error => stores.isSeamStoreUnreachable(error)
          && error.store_ref === "github:checks" && error.because === refused, label);
    assert.deepEqual(calls, [], "a sha this file will not serve reached the source anyway");

    // AND THE ONE SHAPE IT DOES SERVE LANDS INSIDE THE MODULE-PRIVATE REPOSITORY,
    // asserted on the NORMALIZED path rather than on a substring of the string
    // that was built — a `includes("/repos/jbookout/carr-system/")` passes just
    // as happily on a URL that then traverses back out of it.
    await stores.fetchCheckConclusionRows({ headSha: sha, checkName: "main canary" });
    assert.equal(calls.length, 1, "the checks store never called its source");
    const url = new URL(calls[0].url);
    assert.equal(url.origin, "https://api.github.com");
    assert.equal(url.pathname, `/repos/${AUTHORITATIVE_REPOSITORY}/commits/${sha}/check-runs`);
    assert.equal(url.searchParams.get("check_name"), "main canary");

    // A check name is not a path segment, and it may not become one.
    calls.length = 0;
    await stores.fetchCheckConclusionRows({ headSha: sha, checkName: "../../foreign/name" });
    assert.equal(new URL(calls[0].url).pathname,
      `/repos/${AUTHORITATIVE_REPOSITORY}/commits/${sha}/check-runs`,
      "a check name traversed out of the path");
  } finally {
    globalThis.fetch = realFetch;
    restoreEnv(savedEnv);
  }

  // AND THE ENCODING IS ASSERTED STRUCTURALLY, because no input can make it
  // observable: the sha has already matched forty hex characters and the
  // repository is the module's own constant, so both encodings are no-ops on
  // every value that can reach them. That is the point of them — the next
  // segment added to this path inherits an encoded template rather than a
  // trusting one — and a requirement no test can see removed is one that goes
  // away quietly. What a test can hold is that they are there.
  const source = readFileSync(join(SRC, STORES_FILE), "utf8");
  assert.ok(source.includes("/commits/${encodeURIComponent(headSha)}/check-runs"),
    "the head sha goes into the URL path unencoded");
  assert.ok(source.includes('AUTHORITATIVE_REPOSITORY.split("/").map(segment => encodeURIComponent(segment)).join("/")'),
    "the repository goes into the URL path unencoded");
  assert.equal(/repos\/\$\{repository\}/.test(source), false,
    "the repository is interpolated into the path again");
  assert.ok(/const headSha = addressedShape\(storeRef, query, "headSha", HEAD_SHA,/.test(source),
    "the head sha is no longer validated before the URL is built");
});

test("STORES: card 11's statements are read from ONE snapshot, not one each", async () => {
  // FINDING 4 OF THE FOURTH RE-REVIEW. `begin read only` keeps PostgreSQL's
  // DEFAULT isolation — read committed — and read committed takes a fresh
  // snapshot for every statement. The transaction was one transaction and three
  // moments, so an acceptance another session committed between the receipt
  // statement and the card statement was invisible to the first and visible to
  // the second, and the join of the two described a state that never existed.
  //
  // The fake `pg` models the database rather than the answer: it reads the
  // isolation level off the BEGIN the store actually sends, copies the world at
  // the first statement when that level is a snapshot one, and lets a concurrent
  // writer commit right after the first statement returns. What is asserted here
  // is which of the two documented behaviours the store's own statement gets.
  const target = stageWithFakePg({});
  const staged = await import(pathToFileURL(join(target, STORES_FILE)).href);
  const fakePg = await import(
    pathToFileURL(join(dirname(target), "node_modules", "pg", "index.js")).href);
  const savedEnv = saveEnv(STORE_CREDENTIALS);
  process.env.DATABASE_URL_READER = "postgres://fake/rows";
  try {
    const begins = fakePg.default.FAKE_BEGINS;
    begins.length = 0;
    const answer = await staged.fetchPredecessorOutcomeRows(
      { workRequestRef: fakePg.default.FAKE_CONCURRENT });

    assert.equal(begins.length, 1, "the fake saw no begin at all, so nothing here was measured");
    assert.match(begins[0], /isolation level\s+(repeatable read|serializable)/i,
      `the transaction runs at the default isolation: ${begins[0]}`);
    assert.match(begins[0], /read only/i, `the transaction is not read only: ${begins[0]}`);

    assert.equal(answer.rows.length, 1, "the concurrent scenario returned no row to judge");
    assert.equal(answer.rows[0].detail_row_count, 0,
      "a row committed between the two statements was observed, so they are not one snapshot");
    assertSwept("snapshot.rows", answer);
  } finally {
    restoreEnv(savedEnv);
  }
});

test("STORES: card 11 orders accepted receipts by the card's two-key current selector", async () => {
  const target = stageWithFakePg({});
  const staged = await import(pathToFileURL(join(target, STORES_FILE)).href);
  const fakePg = await import(
    pathToFileURL(join(dirname(target), "node_modules", "pg", "index.js")).href);
  const savedEnv = saveEnv(STORE_CREDENTIALS);
  process.env.DATABASE_URL_READER = "postgres://fake/rows";
  try {
    const queries = fakePg.default.FAKE_QUERIES;
    queries.length = 0;
    const answer = await staged.fetchPredecessorOutcomeRows({
      workRequestRef: fakePg.default.FAKE_ORDERED_ACCEPTANCES,
    });
    assert.deepEqual(
      answer.rows.map(row => row.accepted_feedback_hash),
      fakePg.default.FAKE_ORDERED_HASHES,
      "the real store did not return newest acceptance first and descending UUID first on a tie",
    );
    const receiptSql = queries.find(sql => sql.includes(
      "sourced_work_request_outcome_feedback_acceptance_receipt"));
    assert.equal(typeof receiptSql, "string",
      "the real store never issued its acceptance-receipt query");

    const hasCanonicalOrder = sql =>
      /order by r\.accepted_at desc\s*,\s*r\.id desc\s*$/i.test(sql);
    assert.equal(hasCanonicalOrder(receiptSql), true,
      "the receipt query does not preserve work_request_card's accepted_at/id currentness rule");
    assert.equal(hasCanonicalOrder(receiptSql.replace("r.accepted_at desc", "r.accepted_at asc")), false,
      "the assertion does not catch a reversed accepted_at ordering");
    assert.equal(hasCanonicalOrder(receiptSql.replace(/\s*,\s*r\.id desc/i, "")), false,
      "the assertion does not catch a missing UUID tie-breaker");
    assert.equal(hasCanonicalOrder(receiptSql.replace("r.id desc", "r.id asc")), false,
      "the assertion does not catch a reversed UUID tie-breaker");
  } finally {
    restoreEnv(savedEnv);
  }
});

test("STORES: a revision with two authenticated candidate rows is refused, not picked",
  async () => {
    // THE SEVENTH REVIEW ROUND'S CARDINALITY FINDING, asked of the real store.
    // The producer reads its SUBJECT MAKER out of this store by git_sha alone, and
    // the previous shape accepted any number of rows so long as their makers and
    // correlations each happened to collapse to one value. Two rows that agree are
    // still two rows: nothing in the reader was deciding that a revision has one
    // authenticated candidate, so nothing was enforcing it either.
    //
    // Migration 0504 enforces it in the database, with a partial unique index over
    // `maker_authority_verified`. This is the OTHER half — what the reader does in
    // a database where that index is absent — because a control that exists only
    // in the schema is a control this file cannot claim.
    const target = stageWithFakePg({});
    const staged = await import(pathToFileURL(join(target, STORES_FILE)).href);
    const fakePg = await import(
      pathToFileURL(join(dirname(target), "node_modules", "pg", "index.js")).href);
    const savedEnv = saveEnv(STORE_CREDENTIALS);
    process.env.DATABASE_URL_READER = "postgres://fake/rows";
    try {
      // The control first: one row answers, so the refusal below is about the
      // COUNT and not about the store being unable to read this world at all.
      const one = await staged.fetchCandidateBuildRecordRows(
        { gitSha: fakePg.default.FAKE_CANDIDATE_ONE_ROW });
      assert.equal(one.rows.length, 1, "the one-row control returned no row to judge");
      assert.equal(one.rows[0].maker_actor, "joe");
      assertSwept("candidate.one", one);

      await assert.rejects(
        () => staged.fetchCandidateBuildRecordRows(
          { gitSha: fakePg.default.FAKE_CANDIDATE_TWO_ROWS }),
        error => {
          assert.equal(staged.isSeamStoreUnreachable(error), true,
            "the ambiguity came back as something other than this store's refusal");
          assert.equal(error.because,
            "more than one authenticated candidate build row answers this head sha",
            "the refusal does not name the ambiguity it is about");
          assert.equal(error.store_ref, "control-plane:ops.candidate-build-record");
          return true;
        });

      // AND THE PREDICATE THAT MAKES A ROW AUTHENTICATED IS IN THE STATEMENT, not
      // in the consumer: the fake answers this query only because the column is
      // named in it, so a store that stopped asking for it would fail the control
      // above rather than quietly widening what it trusts.
      const source = readFileSync(join(SRC, STORES_FILE), "utf8");
      const candidateQuery = source.slice(source.indexOf("from ops.release r"));
      assert.match(candidateQuery, /and r\.maker_authority_verified/,
        "the candidate store reads rows the database did not mark authenticated");
    } finally {
      restoreEnv(savedEnv);
    }
  });

test("GUARD CONTROL: each clause of the thrown-value sweep has been seen to fail", async () => {
  // A sweep nobody has seen fail is a sweep nobody has tested. SEVEN THROWS,
  // each of which the shipped boundary converts and each of which must trip
  // exactly the clause it is planted against.
  const registered = "the query did not finish";
  const conforming = () => stores.seamStoreUnreachable("github:checks", registered);

  const unconforming = value => {
    const built = new Error("a plant");
    for (const [key, entry] of Object.entries(value))
      Object.defineProperty(built, key, { value: entry, writable: false, configurable: false });
    return Object.freeze(built);
  };

  const plants = [
    // 1 — a bare string, and it is a privileged word.
    ["raw-string", () => { throw "allow"; }],
    // 2 — a bare boolean, the shape the union does not have to list.
    ["raw-true", () => { throw true; }],
    // 3 — a non-privileged bare string: swept clean, and still not a refusal.
    ["raw-innocent-string", () => { throw "something went wrong"; }],
    // 4 — an object built to read as a verdict from a consumer's side.
    ["hostile-object", () => {
      throw Object.freeze({ name: "green", message: "the gate is green",
        stack: "green: the gate is green", because: "green" });
    }],
    // 5 — a native TypeError, raised from a caller named with a privileged word,
    //     so its stack carries `at green` and its message carries `read`.
    ["native-error", () => { const absent = null; return absent.green; }],
    // 6 — conforming in every way except that its code is not registered.
    ["unregistered-code", () => {
      throw unconforming({ name: "SeamStoreUnreachable", message: "github:checks: invented",
        stack: "SeamStoreUnreachable: github:checks: invented", because: "invented" });
    }],
    // 7 — registered code, fixed stack, and it kept the caller's cause.
    ["kept-cause", () => {
      throw unconforming({
        name: "SeamStoreUnreachable",
        message: `github:checks: ${registered}`,
        stack: `SeamStoreUnreachable: github:checks: ${registered}`,
        because: registered,
        cause: { retained: 1 },
      });
    }],
  ];

  for (const [name, plant] of plants) {
    let failed = false;
    try {
      await assertNothingRawEscapes(`control.${name}`, plant);
    } catch {
      failed = true;
    }
    assert.ok(failed, `the sweep passed the ${name} plant, so its assertion proves nothing`);
  }

  // The calibration: a real refusal, raised from a privileged caller, passes.
  await assertNothingRawEscapes("control.shipped",
    () => privilegedCallers()[0](() => { throw conforming(); }, undefined));
});

test("HOSTILE: no hostile query throws out of a reader, and none of its bytes come back", async () => {
  // Asked of the SHIPPED readers, which are ruled, and of a staged UNRULED copy,
  // because a reader's boundary has to hold on both sides of the ruling gate: an
  // unruled reader must give back the gate's answer and nothing else, and a ruled
  // one must refuse without echoing a byte of what it was handed.
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    const unruled = await stagedReaders({ unruled: true });
    for (const [name, reader] of READERS_UNDER_TEST)
      for (const query of hostileQueries()) {
        for (const [side, answered] of [["ruled", await reader(query)],
          ["unruled", await unruled[name](query)]]) {
          const serialized = JSON.stringify(answered);
          assert.ok(!serialized.includes(HOSTILE_MARKER),
            `${name} (${side}) leaked caller text: ${serialized.slice(0, 200)}`);
          assert.equal(answered.decision, "refuse", `${name} (${side})`);
          assert.equal(answered.caller_evidence_admitted, false, `${name} (${side})`);
          // A ruled refusal carries `finding: null`; the gate's own answer has no
          // `finding` key at all. Absent and null are the same claim — nothing
          // was found — and neither side may report anything else.
          assert.equal(answered.finding ?? null, null, `${name} (${side})`);
        }
        // And the unruled half is still the gate's own answer, whole.
        assert.ok(GATE_DELEGATION_DIGESTS.has(digest(await unruled[name](query))), name);
      }
  } finally {
    restoreEnv(saved);
  }
});

// ---------------------------------------------------------------------------
// PART C — the ruled path, on a staged copy of src with a ruling pasted in.
// ---------------------------------------------------------------------------

const staged = [];

/**
 * A copy of mcp-server/src with a DIFFERENT ruling on each of the three
 * `decision_id:` lines than the one src carries. Lives under node_modules/.cache
 * so `pg` and every other dependency still resolve by walking up, and so nothing
 * untracked lands in the working tree.
 *
 * TWO STAGINGS, AND THE SECOND ONE IS WHY THIS FUNCTION STILL EXISTS NOW THAT
 * THE RULINGS ARE REAL.
 *
 *   default          each of Joe's three ids is replaced by a DISTINCT fixture
 *                    id, so every ruled clause below still proves that each
 *                    reader looked up ITS OWN card. A reader that read card 12's
 *                    line for card 11 fails on the id, before it reads a row.
 *   `unruled: true`  each line goes back to `decision_id: null,` — the shape src
 *                    carried before 2026-09-11 — which is how the null path is
 *                    still proved after the paste. It is a TEST-ONLY OVERRIDE
 *                    THAT PRODUCTION CANNOT REACH: it is an edit to a copy of
 *                    the file under node_modules/.cache, reached by importing
 *                    that copy's own reader. Nothing in src imports it, no
 *                    argument selects it, and no environment variable points at
 *                    it — the same property the ruling gate itself rests on.
 */
function stageTree({ storeFile = null, card11StoreRef = null, unruled = false } = {}) {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const base = mkdtempSync(join(cache, "gate-zero-seam-"));
  staged.push(base);
  const target = join(base, "src");
  cpSync(SRC, target, { recursive: true });

  const path = join(target, RULINGS_FILE);
  const source = readFileSync(path, "utf8");
  let ruled = source;
  RULED_DECISION_LINES.forEach((anchor, card) => {
    assert.equal(ruled.split(anchor).length - 1, 1,
      `the staging anchor no longer matches card ${11 + card}'s ruling line`);
    ruled = ruled.replace(anchor,
      unruled ? NULL_DECISION_LINE : decisionLine(FIXTURE_DECISION_IDS[card]));
  });
  for (const anchor of RULED_DECISION_LINES)
    assert.ok(!ruled.includes(anchor), "a production ruling survived the staging");
  assert.equal(ruled.split(NULL_DECISION_LINE).length - 1, unruled ? 3 : 0,
    "the staging left the wrong number of unruled lines");
  if (card11StoreRef !== null) {
    assert.ok(ruled.includes(CARD_11_STORE_LINE), "card 11's store line moved");
    ruled = ruled.replace(CARD_11_STORE_LINE, `    store_ref: "${card11StoreRef}",\n`);
  }
  writeFileSync(path, ruled);

  // AND CARD 9'S SEAT GOES BACK TO UNSTAFFED IN THE UNRULED TREE, for the same
  // reason the three ruling lines do. The producer seam's binding condition is
  // that declaration, so a staged tree that kept the staffed seat would have a
  // BOUND producer behind a gate whose readers are all withdrawn — which is not
  // the tree main shipped, and the pin below is about the tree main shipped.
  if (unruled) {
    const registrationPath = join(target, REGISTRATION_FILE);
    const registration = readFileSync(registrationPath, "utf8");
    assert.equal(registration.split(STAFFED_SEAT_LINE).length - 1, 1,
      "the staging anchor no longer matches the seat declaration");
    writeFileSync(registrationPath, registration.replace(STAFFED_SEAT_LINE, UNSTAFFED_SEAT_LINE));
  }

  if (storeFile !== null) cpSync(storeFile, join(target, STORES_FILE));
  return target;
}

async function stagedReaders(options) {
  const target = stageTree(options);
  const staged = await import(pathToFileURL(join(target, READERS_FILE)).href);
  // The SAME TREE's gate, so a clause about "the gate's own answer" is asked of
  // the gate that reader actually delegates to rather than of the shipped one.
  const gate = await import(pathToFileURL(join(target, GATE_FILE)).href);
  STAGED_GATES.set(staged, gate);
  const answers = [gate.readGateZeroPredecessorJoin(), gate.readGateGraphAssurance()];
  for (const answer of answers) GATE_DELEGATION_DIGESTS.add(digest(answer));
  // AND THE AMENDMENT 3 PROOF, taken here because this is where an unruled tree
  // exists: with all three ruling lines null the gate's two answers ARE the
  // pre-PR baseline's pinned bytes, which is what makes the exemption above an
  // exemption for unchanged upstream output rather than for whatever this branch
  // happens to answer. A ruled tree's answers are this branch's and are never
  // added.
  //
  // ONE FIELD IS NORMALIZED AND IT IS DECLARED: `V5_A02_POLICY_VERSION` moved
  // 2 -> 3 in the producer slice, on purpose — version 2's claim was that this
  // surface answers `unavailable` for every caller on every input, and it can
  // now answer over rows. The number is in every object the gate builds, so no
  // tree can be byte-identical to main while it stands. Everything else is
  // compared exactly, which is what this pin is for.
  if (options.unruled === true)
    for (const answer of answers)
      assert.ok(PRE_PR_GATE_ANSWER_DIGESTS.has(digest(atPrePrPolicyVersion(answer))),
        "an unruled staged gate no longer answers the pre-PR baseline's bytes");
  return staged;
}

/**
 * A staged tree with a `pg` OF ITS OWN, written into `<base>/node_modules/pg`.
 *
 * The store module opens its own connection — no handle parameter, no injectable
 * opener, no env var that points it elsewhere — which is the property the whole
 * slice rests on and the reason its successful row-shaping path had never been
 * run by a test. Node resolves a bare `import("pg")` by walking up from the
 * importing file, so a package placed one directory above the staged src is
 * found before mcp-server/node_modules. The REAL store module runs, over rows a
 * hostile database would hand it. Nothing in src is edited and nothing is
 * monkeypatched.
 */
function stageWithFakePg(options = {}) {
  const target = stageTree(options);
  const module = join(dirname(target), "node_modules", "pg");
  mkdirSync(module, { recursive: true });
  writeFileSync(join(module, "package.json"),
    JSON.stringify({ name: "pg", version: "0.0.0", main: "index.js" }));
  cpSync(FAKE_PG_FILE, join(module, "index.js"));
  return target;
}

/**
 * A `fetch` that answers the checks call with wire text nobody would want in an
 * answer. Replacing a global in a TEST is a measurement, not a threat model —
 * the amendment of 2026-09-11 puts interpreter-level substitution out of scope
 * as an attack — and it is the only seam the checks store has, for the same
 * reason the database one had none.
 */
function fakeChecksSource(runs, calls = []) {
  return async (url, init) => {
    calls.push({ url: String(url), init });
    return {
      ok: true,
      status: 200,
      async json() { return { check_runs: runs, total_count: runs.length, ok: true }; },
    };
  };
}

const HOSTILE_CHECK_RUNS = Object.freeze([
  Object.freeze({
    name: "allow-the-commit",
    head_sha: "a".repeat(40),
    status: "completed",
    conclusion: "success",
    started_at: "2026-09-11T17:00:00.000Z",
    completed_at: "2026-09-11T17:00:30.000Z",
    html_url: `a-link-this-store-drops-green-passing-${HOSTILE_MARKER}`,
    check_suite: { conclusion: "green", ok: true },
  }),
]);

test.after(() => {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
  releasePrePrTrees();
});

test("STAGING: both fixture store modules cover every export the real one has", () => {
  const real = Object.keys(stores).sort();
  for (const [label, fixture] of [["fixture", fixtureStores], ["receipt", receiptStores]]) {
    const names = new Set(Object.keys(fixture));
    for (const name of real)
      assert.ok(names.has(name), `the ${label} store is missing ${name}, so a path would go unproved`);
    for (const name of [...names].sort())
      if (!real.includes(name))
        assert.ok(name.startsWith("FIXTURE_"), `${label}.${name} is a fixture-only export not named as one`);
  }
});

// ---------------------------------------------------------------------------
// AND EXPORT NAMES ARE NOT THE WHOLE CONTRACT — the shape is the other half.
//
// FINDINGS 1 AND 2 OF THE SIXTH RE-REVIEW. The parity check above asks that the
// fixtures carry the real module's export NAMES, and it stops there; the
// exhaustive sweep asked the shape question of the three production namespaces
// and never of a fixture. So both fixtures shipped the pre-amendment shape —
// plain `export async function` fetchers with no `Symbol.hasInstance`, and error
// instances whose `.constructor` was the constructable private class — under
// names the parity check was happy with.
//
// That gap is not cosmetic, because of what these two files ARE: each is copied
// OVER src/gate-zero-seam-stores.v5.js, and the staged reader imports whichever
// is in place. Every RULED clause below this line is therefore proved against a
// store surface that, until this test existed, was weaker than the one
// production runs. The sweep now asks both fixtures the same questions in the
// same words, through the same two functions the production sweep uses.
// ---------------------------------------------------------------------------

const SWEPT_FIXTURE_NAMESPACES = () => [
  ["fixtureStores", fixtureStores], ["receiptStores", receiptStores]];

/**
 * One fixture namespace, against the whole closed shape:
 *
 *   sweepEveryInvocation  every export — `assertClosedCallable` on each callable
 *                         (no `.prototype`, non-constructable, a non-writable
 *                         non-configurable `Symbol.hasInstance` DATA property
 *                         that never reads the operand) and then every hostile
 *                         argument from every privileged-named caller, with
 *                         whatever returns or throws swept whole.
 *   sweepErrorFactory     the error factory, with every hostile value in every
 *                         argument position, and every produced error walked for
 *                         a `.constructor` that reaches a constructable class.
 */
async function assertFixtureSurfaceClosed(label, namespace) {
  await sweepEveryInvocation(label, namespace);
  sweepErrorFactory(`${label}.seamStoreUnreachable`, namespace.seamStoreUnreachable);
  await assertNoExportAnswersALabel(label, namespace);
}

// ---------------------------------------------------------------------------
// AND THE SWEEP NO LONGER HAS AN ARGUMENT IT DECLINES TO PASS — the finding of
// the seventh re-review, and the one clause `hostileArguments()` structurally
// could not carry.
//
// A hostile argument list is built out of values a sweep's AUTHOR chose. The
// store fixture exported four trigger strings and misbehaved on an exact match
// against one, and no value in that list was ever equal to one — so the sweep
// asked its question honestly and got a clean answer, while the surface it swept
// answered a caller's label two functions further down. The file argued the door
// was addressed rather than open. The standing rule of 2026-09-11 does not admit
// that distinction: an exported constant used as an exact caller-supplied label
// is the exception it forbids.
//
// So the sweep now derives its arguments from THE NAMESPACE ITSELF. Every string
// a fixture exports, and every name it exports one under, is handed to every
// fetcher under every query key the readers use — plus the four retired trigger
// strings by name, so those exact doors cannot be reopened quietly. The claim
// this makes is not "no hostile value got through"; it is the stronger one the
// PR body now states: NO EXPORT ANSWERS A LABEL AT ALL. Every one of them
// answers with the same empty reading it gives any other string.
// ---------------------------------------------------------------------------

/**
 * The four addresses the fixtures used to answer, kept by their exact text. A
 * correction that re-introduced any of them — under any export name, or under
 * none — is red here rather than red in a review round.
 */
const RETIRED_FIXTURE_TRIGGERS = Object.freeze([
  "unreachable",
  "canary-from-another-store",
  "canary-that-throws-a-raw-value",
  "canary-whose-answer-is-hostile",
]);

/** What each fetcher must say it is, whatever it was asked. */
const FETCHER_STORE_REFS = Object.freeze({
  fetchPredecessorOutcomeRows: "record-layer:work-request-outcome-feedback",
  fetchSchedulerLedgerRows: "control-plane:ops.service+ops.run",
  fetchCheckConclusionRows: "github:checks",
});

/** Every query field the three readers put in front of a store. */
const READER_QUERY_KEYS = Object.freeze([
  "stepRef", "outcomeHash", "serviceKey", "canaryRunKey",
  "headSha", "checkName", "workRequestRef", "commitSha",
]);

// ---------------------------------------------------------------------------
// AND THE LABEL SWEEP REACHES EVERY EXPORT, NOT THREE OF THEM — the finding of
// the eighth re-review, and a gap in the sweep's own reach rather than in its
// idea.
//
// The sweep this replaces derived its words from the namespace, which was the
// seventh round's correction, and then handed them to the entries of
// `FETCHER_STORE_REFS` and nothing else. Two exported callables of each fixture
// — the error factory and the membership predicate — were never asked a label
// at all, and neither were the production readers or the ruling lookup. Both
// label-door mutation controls planted their door in a fetcher, so the clause
// was proved exactly where it was already enforced and nowhere else. A label
// branch in `seamStoreUnreachable` or in `isSeamStoreUnreachable` would have
// survived a green run of this file.
//
// WHAT IS ASKED NOW, of every exported callable of all five namespaces — the two
// fixtures, and the three production modules — is the claim in its general form:
//
//   THE SURFACE'S BEHAVIOUR DOES NOT VARY WITH THE WORD IT IS HANDED.
//
// Each word is handed to each export in every argument position and, because a
// door does not have to be at the top of an object, as a nested KEY and as a
// nested LEAF. The same call is then made again with the word's TWIN — the same
// string with one character moved within its own class, so a 40-hex sha stays a
// 40-hex sha and a `sha256:` hash stays one, and no refusal moves because a
// pattern stopped matching. The two outcomes must be the same outcome.
//
// That comparison is what makes the claim falsifiable without an allow-list of
// answers: a module constant that appears in the answer appears in the twin's
// answer too and says nothing, while a word that came back BECAUSE THE CALLER
// SENT IT comes back as the twin instead, and the two disagree.
// ---------------------------------------------------------------------------

/** The one phrase every label clause fails with, so a control can attribute it. */
const LABEL_CLAUSE = "on the label";

/**
 * The words a namespace is asked about: every name it exports, every string it
 * exports under one, and the four retired triggers by their exact text.
 */
function labelsOf(namespace) {
  const words = new Set(RETIRED_FIXTURE_TRIGGERS);
  for (const [name, value] of Object.entries(namespace)) {
    words.add(name);
    if (typeof value === "string") words.add(value);
  }
  return [...words].filter(word => word.length > 0);
}

/**
 * The alphabet a character must stay inside for its twin to be the same KIND of
 * string. Lower-case hex is its own class because three of this slice's patterns
 * are built out of it, and a twin that left it would move a refusal honestly and
 * read here as a door.
 */
function characterClass(character) {
  if (/[0-9]/.test(character)) return "0123456789";
  if (/[a-f]/.test(character)) return "abcdef";
  if (/[g-z]/.test(character)) return "ghijklmnopqrstuvwxyz";
  if (/[A-Z]/.test(character)) return "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  return null;
}

/**
 * The same word with its last alphanumeric character moved one place inside its
 * own class. Null when the word has no such character, in which case there is no
 * twin to compare against and the word is swept for its other clauses only.
 */
function twinOf(word) {
  for (let at = word.length - 1; at >= 0; at -= 1) {
    const alphabet = characterClass(word[at]);
    if (alphabet === null) continue;
    const moved = alphabet[(alphabet.indexOf(word[at]) + 1) % alphabet.length];
    return `${word.slice(0, at)}${moved}${word.slice(at + 1)}`;
  }
  return null;
}

/** The same argument structure with every occurrence of the word — key or leaf — replaced. */
function substituted(value, word, twin) {
  if (value === word) return twin;
  if (Array.isArray(value)) return value.map(one => substituted(one, word, twin));
  if (value !== null && typeof value === "object") {
    const rebuilt = {};
    for (const [key, one] of Object.entries(value))
      rebuilt[key === word ? twin : key] = substituted(one, word, twin);
    return rebuilt;
  }
  return value;
}

/**
 * One word, in every position an argument can take: alone first, second and
 * third; in all three at once; under each query key the readers use, alone and
 * beside a service key the fixtures do serve, because a door behind two
 * conditions is still a door; and as a key and as a leaf, one and two levels
 * down.
 */
function labelArgumentLists(word) {
  const lists = [
    [word], [undefined, word], [undefined, undefined, word], [word, word, word],
  ];
  for (const key of READER_QUERY_KEYS)
    lists.push([{ [key]: word }], [{ serviceKey: "carr-fleet-sync", [key]: word }]);
  lists.push([{ [word]: word }]);
  lists.push([{ wrapped: { [word]: [word] } }]);
  lists.push([{ serviceKey: "carr-fleet-sync", nested: [{ [word]: { leaf: word } }] }]);
  return lists;
}

async function outcomeOf(callable, argumentList) {
  try {
    return { kind: "returned", value: await callable(...argumentList) };
  } catch (thrown) {
    return { kind: "threw", value: thrown };
  }
}

/** A digest is content-derived, so a twin moves it honestly; nothing else may move. */
const DIGEST_TEXT = /sha256:[0-9a-f]{64}/g;
const SHAPE_DEPTH_LIMIT = 6;

/**
 * What a caller can observe of a value: its own properties including the
 * non-enumerable ones an Error keeps, read through a `try` so a refusing getter
 * is a fact rather than a crash, with digests masked.
 */
function observableShape(value, depth = 0, seen = new Set()) {
  if (value === undefined) return "<undefined>";
  if (value === null) return null;
  const kind = typeof value;
  if (kind === "string") return value.replace(DIGEST_TEXT, "sha256:<digest>");
  if (kind === "number" || kind === "boolean") return value;
  if (kind === "bigint") return `<bigint:${value}>`;
  if (kind === "symbol") return `<symbol:${String(value.description)}>`;
  if (kind === "function") return "<function>";
  if (depth >= SHAPE_DEPTH_LIMIT || seen.has(value)) return "<not-walked-further>";
  seen.add(value);
  const shaped = Array.isArray(value) ? { "<array>": value.length } : {};
  for (const key of safeOwnKeys(value).filter(one => typeof one === "string").sort())
    shaped[key] = observableShape(safeRead(value, key), depth + 1, seen);
  return shaped;
}

/** Every string a caller could read off a value, keys included. */
function surfaceText(value, depth = 0, seen = new Set(), parts = []) {
  if (typeof value === "string") { parts.push(value); return parts; }
  if (typeof value === "symbol") { parts.push(String(value.description)); return parts; }
  if (value === null || (typeof value !== "object" && typeof value !== "function")) return parts;
  if (depth >= SHAPE_DEPTH_LIMIT || seen.has(value)) return parts;
  seen.add(value);
  for (const key of safeOwnKeys(value)) {
    parts.push(String(key));
    surfaceText(safeRead(value, key), depth + 1, seen, parts);
  }
  return parts;
}

function saysTheWord(value, word) {
  return surfaceText(value).some(part => part.includes(word));
}

/**
 * EVERY exported callable of a namespace, against every word derived from that
 * namespace, in every argument position and nested as a key and as a leaf.
 *
 * Three clauses per call, and the twin is what makes the first two decidable:
 *   behaviour  the outcome for the word is the outcome for its twin — the same
 *              kind, and the same observable shape once digests are masked.
 *   echo       the word appears in the outcome only where the twin's outcome
 *              carries it too, which is to say only because the module already
 *              said it and not because the caller did.
 *   raw        whatever is thrown is one of the module's own registered
 *              refusals, and whatever is returned carries no privileged outcome.
 */
async function assertNoExportAnswersALabel(label, namespace) {
  for (const [name, exported] of Object.entries(namespace)) {
    if (typeof exported !== "function") continue;
    const at = `${label}.${name}`;
    for (const word of labelsOf(namespace)) {
      const twin = twinOf(word);
      for (const argumentList of labelArgumentLists(word)) {
        const where = `${at} ${LABEL_CLAUSE} ${safeLabel(word)} at ${safeLabel(argumentList)}`;
        const answered = await outcomeOf(exported, argumentList);
        if (answered.kind === "threw") assertNothingRaw(at, answered.value);
        else assertSwept(`${at}.returned`, answered.value);
        if (twin === null) continue;
        const twinAnswered = await outcomeOf(
          exported, argumentList.map(one => substituted(one, word, twin)));
        assert.equal(answered.kind, twinAnswered.kind, `${where}: it answered a different way`);
        assert.deepEqual(observableShape(answered.value), observableShape(twinAnswered.value),
          `${where}: its answer moved`);
        assert.ok(!saysTheWord(answered.value, word) || saysTheWord(twinAnswered.value, word),
          `${where}: it said the caller's own word back`);
      }
    }
  }
}

/** The five namespaces the label sweep owes: both fixtures and all three modules. */
const LABEL_SWEPT_NAMESPACES = () => [...SWEPT_FIXTURE_NAMESPACES(), ...SWEPT_NAMESPACES()];

test("STAGING: both fixture namespaces hold the closed shape the real module does", async () => {
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    let callables = 0;
    for (const [label, namespace] of SWEPT_FIXTURE_NAMESPACES()) {
      for (const value of Object.values(namespace))
        if (typeof value === "function") callables += 1;
      await assertFixtureSurfaceClosed(label, namespace);
    }
    // Two factories, two predicates and eight fetchers — four per fixture since
    // amendment 9 added the candidate-build record store. A fixture that grows a
    // callable fails here rather than quietly skipping the loop above.
    assert.equal(callables, 12,
      "a fixture's callable surface moved without this count following it");
  } finally {
    restoreEnv(saved);
  }
});

test("STAGING: no export of any of the five namespaces answers a label, and the four retired doors stay shut", async () => {
  // WHAT THIS REPLACES, said plainly, because the claim has changed rather than
  // been tightened. The test that stood here asserted that the store fixture's
  // two deliberate misbehaviours — `throw "allow"` and an answer whose getters
  // throw — were reachable ONLY by a caller holding the exact exported constant
  // for each, and it asserted the doors were still live. That is an exported
  // constant used as an exact caller-supplied label, which the standing rule of
  // 2026-09-11 forbids outright; "only by its own address" is a description of
  // the exception, not a defence against it.
  //
  // Both faults still exist, because the reader's own boundary is unproved
  // without them. They live in ./gate-zero-seam-fault-injection.testhelper.mjs,
  // in a DISTINCT store instance whose fetchers take no query at all and whose
  // faults were closed over at construction. Nothing a caller passes selects
  // one, because there is nothing to select.
  //
  // So what is asserted here is the opposite of what used to be: that no export
  // of either fixture answers a label of any kind — and, since the eighth
  // re-review, that no export of the three PRODUCTION namespaces does either.
  // The credentials are cleared for the production half so that every refusal is
  // the configured one and no call in this file reaches a network.
  const savedEnv = saveEnv(STORE_CREDENTIALS);
  try {
    for (const [label, namespace] of LABEL_SWEPT_NAMESPACES())
      await assertNoExportAnswersALabel(label, namespace);
  } finally {
    restoreEnv(savedEnv);
  }

  // AND THE THREE FETCHERS OF EACH FIXTURE SAY THE SAME EMPTY READING BY NAME,
  // which is the concrete form of the general claim above: the twin comparison
  // proves the answer did not MOVE, and this proves what the answer IS.
  for (const [label, namespace] of SWEPT_FIXTURE_NAMESPACES())
    for (const [name, storeRef] of Object.entries(FETCHER_STORE_REFS))
      for (const word of labelsOf(namespace))
        for (const key of READER_QUERY_KEYS)
          for (const query of [{ [key]: word }, { serviceKey: "carr-fleet-sync", [key]: word }])
            assert.deepEqual(await namespace[name](query), { store_ref: storeRef, rows: [] },
              `${label}.${name} answered the label ${safeLabel(word)} under ${key}`);

  // AND THE FOUR RETIRED ADDRESSES BY NAME, against the fetcher each used to
  // open a door on, with the service key that used to be its other half.
  for (const word of RETIRED_FIXTURE_TRIGGERS) {
    const ledger = await fixtureStores.fetchSchedulerLedgerRows({
      serviceKey: "carr-fleet-sync", canaryRunKey: word });
    assert.deepEqual(ledger, { store_ref: "control-plane:ops.service+ops.run", rows: [] },
      `${word} still opens a door on the store fixture`);
    const receipt = await receiptStores.fetchPredecessorOutcomeRows({ workRequestRef: word });
    assert.deepEqual(receipt,
      { store_ref: "record-layer:work-request-outcome-feedback", rows: [] },
      `${word} still opens a door on the receipt fixture`);
  }

  // AND NEITHER FIXTURE EXPORTS ONE OF THOSE ADDRESSES ANY MORE, under any name.
  // A constant nothing answers is harmless, but it is also a loaded gun left on
  // the table, and the finding was about the export as much as the branch.
  for (const [label, namespace] of SWEPT_FIXTURE_NAMESPACES())
    for (const [name, value] of Object.entries(namespace))
      assert.ok(!RETIRED_FIXTURE_TRIGGERS.includes(value),
        `${label}.${name} is a retired trigger string, exported again`);

  // AND A CASE IS ADDRESSED BY AN OWN KEY, not by anything on Object.prototype:
  // `TABLE["constructor"]` used to answer with a function, which is a row no
  // fixture holds and a value no reader could read.
  for (const inherited of ["constructor", "__proto__", "toString", "hasOwnProperty"]) {
    const ledger = await fixtureStores.fetchSchedulerLedgerRows({
      serviceKey: "carr-fleet-sync", canaryRunKey: inherited });
    assert.deepEqual(ledger.rows, [], `${inherited} reached Object.prototype through the row table`);
    const checks = await fixtureStores.fetchCheckConclusionRows({ checkName: inherited });
    assert.deepEqual(checks.rows, [], `${inherited} reached Object.prototype through the row table`);
    const receipt = await receiptStores.fetchPredecessorOutcomeRows({ workRequestRef: inherited });
    assert.deepEqual(receipt.rows, [], `${inherited} reached Object.prototype through the row table`);
  }
});

test("STAGING: the faulted store is one instance, and no caller value steers it", async () => {
  // THE OTHER HALF OF THE CORRECTION. Moving the faults out of the fixture is
  // worth nothing if the helper merely re-implements the same label lookup one
  // file over, so the claim is asserted rather than asserted about: each faulted
  // fetcher does the SAME thing for every argument the sweep can build,
  // including the four retired trigger strings and the fixtures' own constants.
  //
  // A fault that varied with its argument would be a label again, whatever it
  // was called, and it would fail here on the first shape that differed.
  const everyArgument = [
    undefined, ...hostileArguments(),
    ...RETIRED_FIXTURE_TRIGGERS,
    ...RETIRED_FIXTURE_TRIGGERS.map(word => ({ canaryRunKey: word, workRequestRef: word,
      checkName: word, serviceKey: "carr-fleet-sync", headSha: "a".repeat(40) })),
    ...Object.values(fixtureStores).filter(value => typeof value === "string")
      .map(word => ({ canaryRunKey: word, workRequestRef: word, checkName: word })),
  ];

  for (const argument of everyArgument) {
    // Card 11's fault RETURNS, and its answer's getters throw on read.
    const answer = await faultedStores.fetchPredecessorOutcomeRows(argument);
    assert.throws(() => answer.store_ref, `a predecessor fault varied with ${safeLabel(argument)}`);
    assert.throws(() => answer.rows, `a predecessor fault varied with ${safeLabel(argument)}`);

    // Card 12's fault THROWS a bare string, which is not an Error at all.
    const thrown = await faultedStores.fetchSchedulerLedgerRows(argument)
      .then(() => null, one => one);
    assert.equal(thrown, "allow", `a scheduler fault varied with ${safeLabel(argument)}`);

    // Card 13's fault answers about a store this card was not ruled for.
    const foreign = await faultedStores.fetchCheckConclusionRows(argument);
    assert.equal(foreign.store_ref, "control-plane:ops.service+ops.run",
      `a checks fault varied with ${safeLabel(argument)}`);
    assert.equal(foreign.rows.length, 1);
  }

  // And the helper carries the real module's export names, so staging it proves
  // the same surface the fixtures do rather than a narrower one.
  for (const name of Object.keys(stores))
    assert.ok(Object.hasOwn(faultedStores, name),
      `the fault helper is missing ${name}, so a staged reader would import nothing for it`);
  // It exports no constant at all: there is no address to hold.
  for (const [name, value] of Object.entries(faultedStores))
    assert.equal(typeof value, "function", `the fault helper exports a constant, ${name}`);
});

test("STAGING CONTROL: each door the fixture-surface sweep closes has been seen to fail", async () => {
  // A check nobody has seen fail is a check nobody has tested, and this whole
  // test is new. SIX PLANTS, one per clause, each a namespace identical to the
  // shipped fixture in every way but one, so a failure is attributable to that one.
  const closed = callable => {
    const bound = callable.bind(null);
    Object.defineProperty(bound, Symbol.hasInstance,
      { value: () => false, writable: false, enumerable: false, configurable: false });
    return bound;
  };
  const answer = { store_ref: "github:checks", rows: [] };

  const plants = [
    // 1 — the shape BEFORE this correction on the fetchers' side, spelled as an
    //     ordinary function: constructable, and carrying the `prototype` a
    //     foreign new.target is read against.
    ["a-plain-function-fetcher", { ...fixtureStores,
      fetchCheckConclusionRows: function fetchCheckConclusionRows() { return answer; } }],
    // 2 — bound and non-constructable, and answering `instanceof` with the
    //     intrinsic: the exact miss finding 1 of the sixth round names, which
    //     walks the LEFT operand's chain and runs a caller's own trap.
    ["a-fetcher-without-hasInstance", { ...fixtureStores,
      fetchCheckConclusionRows: (async () => answer).bind(null) }],
    // 3 — closed in shape and naive in its reads: `query?.checkName` on a
    //     revoked Proxy or a throwing getter answers with the caller's own text.
    ["a-fetcher-that-reads-the-query-naively", { ...fixtureStores,
      fetchCheckConclusionRows: closed(async query => ({ ...answer, asked: query?.checkName ?? null })) }],
    // 4 — a constant that is not a callable at all: the non-function branch of
    //     the sweep is live too.
    ["a-constant-carrying-a-privileged-word", { ...fixtureStores, FIXTURE_PLANTED_WORD: "green" }],
    // 5 — THE SEVENTH RE-REVIEW'S FINDING, PLANTED BACK. A fetcher that answers
    //     one exact address with a bare throw, exactly as the store fixture did
    //     until this correction. Every clause above passes it: the callable is
    //     bound, guarded and non-constructable, it reads its query through a
    //     try, and no value in `hostileArguments()` is equal to the address. Only
    //     the label sweep — which builds its arguments from the namespace and
    //     from the retired trigger list — can see it.
    ["a-door-behind-a-retired-address", { ...fixtureStores,
      fetchSchedulerLedgerRows: closed(async query => {
        let asked;
        try { asked = query === null || query === undefined ? undefined : query.canaryRunKey; }
        catch { asked = undefined; }
        if (asked === "canary-that-throws-a-raw-value") throw "allow";
        return { store_ref: "control-plane:ops.service+ops.run", rows: [] };
      }) }],
    // 6 — the same shape addressed by the fixture's OWN exported constant rather
    //     than by a retired one, which is how the door would come back if it came
    //     back under a new name.
    ["a-door-behind-an-exported-constant", { ...fixtureStores,
      fetchCheckConclusionRows: closed(async query => {
        let asked;
        try { asked = query === null || query === undefined ? undefined : query.checkName; }
        catch { asked = undefined; }
        return asked === fixtureStores.FIXTURE_COMMIT_SHA
          ? { store_ref: "github:checks", rows: [{ planted: 1 }] }
          : { store_ref: "github:checks", rows: [] };
      }) }],
  ];
  for (const [label, plant] of plants) {
    let failed = false;
    try {
      await assertFixtureSurfaceClosed(`control.${label}`, plant);
    } catch {
      failed = true;
    }
    assert.ok(failed,
      `the sweep passed the ${label} plant, so the clause it is planted against proves nothing`);
  }

  // 5 — THE RAW CLASS REACHED THROUGH `.constructor`, which is the other half of
  //     finding 1 and the one clause no shape check above can see. The refusal
  //     below conforms in every way the sweep inspects — fixed stack, registered
  //     reason, no retained cause, frozen, non-writable non-configurable data
  //     properties — and differs from the shipped one in exactly one way: its
  //     prototype still carries the constructable class the engine installed.
  const REGISTERED = "the query did not finish";
  class ReachableType extends Error {
    constructor() {
      const message = `github:checks: ${REGISTERED}`;
      super(message);
      for (const [key, value, enumerable] of [
        ["name", "SeamStoreUnreachable", false], ["message", message, false],
        ["stack", `SeamStoreUnreachable: ${message}`, false],
        ["store_ref", "github:checks", true], ["because", REGISTERED, true],
        ["cause_kind", "none", true]])
        Object.defineProperty(this, key, { value, writable: false, enumerable, configurable: false });
      Object.freeze(this);
    }
  }
  const reachableFactory = closed(() => new ReachableType());
  assert.equal(Reflect.get(reachableFactory(), "constructor"), ReachableType,
    "the plant does not reach a class, so it stands in for nothing");
  assert.equal(isConstructor(ReachableType), true);
  assert.throws(() => sweepErrorFactory("control.reachable-class", reachableFactory), undefined,
    "an instance whose .constructor is a constructable class passed the sweep");

  // AND THE CALIBRATION, which is what makes the failure attributable: redefine
  // that ONE property the way both fixtures now do, change nothing else, and the
  // same value goes through the same sweep untouched.
  Object.defineProperty(ReachableType.prototype, "constructor",
    { value: reachableFactory, writable: false, enumerable: false, configurable: false });
  Object.freeze(ReachableType.prototype);
  assert.doesNotThrow(() => sweepErrorFactory("control.redefined-constructor", reachableFactory));

  // And the shipped fixtures go through the whole check untouched.
  for (const [label, namespace] of SWEPT_FIXTURE_NAMESPACES())
    await assertFixtureSurfaceClosed(`calibration.${label}`, namespace);
});

test("STAGING CONTROL: a planted label door in every export class has been seen to fail", async () => {
  // THE FINDING OF THE EIGHTH RE-REVIEW WAS NOT THAT THE SWEEP WAS WEAK — it was
  // that it was AIMED. Both label-door controls planted their door in a fetcher,
  // which is the one export class the label sweep already reached, so the clause
  // was proved exactly where it was already enforced and nowhere else. A control
  // that can only fail where the check already runs proves the check runs, not
  // that it covers anything.
  //
  // So there is a plant per EXPORT CLASS now: the error factory, the membership
  // predicate, a fetcher, a reader, and the ruling lookup — EIGHT PLANTS across
  // those five classes, because two of them have two doors. Each namespace is
  // identical to the shipped one in every way but the single planted branch, so
  // a failure is attributable to that branch — and the clause that catches it is
  // named in the plant and asserted, so a plant that trips some other check on
  // its way past is a red control rather than a green one.
  const planted = callable => {
    const bound = callable.bind(null);
    Object.defineProperty(bound, Symbol.hasInstance,
      { value: () => false, writable: false, enumerable: false, configurable: false });
    return bound;
  };
  // One door text for all five namespaces: a retired trigger is in every derived
  // label set, because `labelsOf` seeds every set with the four of them.
  const DOOR = RETIRED_FIXTURE_TRIGGERS[0];
  const askedFor = (query, key) => {
    try { return query === null || query === undefined ? undefined : query[key]; }
    catch { return undefined; }
  };
  const SWEPT_CLAUSE = "carries a privileged outcome";

  const plants = [
    // THE ERROR FACTORY — the class the sweep never handed a word to. The door is
    // the quietest one a factory has: the same refusal, built with a cause it was
    // not given, so only `cause_kind` moves.
    ["the error factory", "fixtureStores", LABEL_CLAUSE, { ...fixtureStores,
      seamStoreUnreachable: planted((storeRef, because, cause) =>
        storeRef === DOOR || because === DOOR
          ? fixtureStores.seamStoreUnreachable(storeRef, because, new Error("a door"))
          : fixtureStores.seamStoreUnreachable(storeRef, because, cause)) }],
    // THE MEMBERSHIP PREDICATE, twice, because its whole answer space is two
    // booleans and the two doors it has are caught by different clauses.
    //
    // (a) It REFUSES on the label instead of answering. The refusal is one of the
    //     fixture's own registered ones, so the raw clause is satisfied and it is
    //     the label clause that sees the kind change.
    ["the membership predicate, refusing on a label", "fixtureStores", LABEL_CLAUSE,
      { ...fixtureStores,
        isSeamStoreUnreachable: planted(value => {
          if (value !== DOOR) return fixtureStores.isSeamStoreUnreachable(value);
          throw fixtureStores.seamStoreUnreachable(
            "control-plane:ops.service+ops.run", "the call did not finish");
        }) }],
    // (b) It ANSWERS TRUE on the label, which is the door a predicate actually
    //     has. The twin comparison would see it too, but the sweep's own raw
    //     clause gets there first: a bare `true` is a privileged outcome
    //     wherever it appears, so this plant is asserted against THAT clause
    //     rather than against the label one. Both are clauses of this sweep.
    ["the membership predicate, answering true on a label", "fixtureStores", SWEPT_CLAUSE,
      { ...fixtureStores,
        isSeamStoreUnreachable: planted(value =>
          value === DOOR ? true : fixtureStores.isSeamStoreUnreachable(value)) }],
    // A FETCHER — the class both earlier controls covered, kept so the change is
    // additive and the class is not left uncovered by the rewrite.
    ["a fetcher", "fixtureStores", LABEL_CLAUSE, { ...fixtureStores,
      fetchSchedulerLedgerRows: planted(async query =>
        askedFor(query, "canaryRunKey") === DOOR
          ? { store_ref: "control-plane:ops.service+ops.run", rows: [{ planted: 1 }] }
          : fixtureStores.fetchSchedulerLedgerRows(query)) }],
    // A READER — a door that answers with another card's answer, which is a
    // well-formed gate answer and so invisible to every clause but the twin.
    ["a reader", "readers", LABEL_CLAUSE, { ...readers,
      readGateConclusionEvidence: planted(async query =>
        askedFor(query, "checkName") === DOOR
          ? readers.readPredecessorOutcomeEvidence({})
          : readers.readGateConclusionEvidence(query)) }],
    // A DOOR THAT IS NOT AT THE TOP OF THE OBJECT, twice, because "every
    // argument position" is a claim about depth as well as arity and a template
    // no plant can reach is an untested template. Neither of these is addressed
    // by any query key the readers use: the first is reachable only by the word
    // as a KEY one level down, the second only by the word as a key AND as a
    // LEAF three levels down.
    ["a door under the word as a nested key", "fixtureStores", LABEL_CLAUSE, {
      ...fixtureStores,
      fetchCheckConclusionRows: planted(async query => {
        let opened = false;
        try { opened = Object.hasOwn(Object(askedFor(query, "wrapped")), DOOR); }
        catch { opened = false; }
        return opened
          ? { store_ref: "github:checks", rows: [{ planted: 1 }] }
          : fixtureStores.fetchCheckConclusionRows(query);
      }) }],
    ["a door on the word as a nested leaf", "fixtureStores", LABEL_CLAUSE, {
      ...fixtureStores,
      fetchPredecessorOutcomeRows: planted(async query => {
        let opened = false;
        try { opened = askedFor(askedFor(askedFor(query, "nested")?.[0], DOOR), "leaf") === DOOR; }
        catch { opened = false; }
        return opened
          ? { store_ref: "record-layer:work-request-outcome-feedback", rows: [{ planted: 1 }] }
          : fixtureStores.fetchPredecessorOutcomeRows(query);
      }) }],
    // THE RULING LOOKUP — the one export of its module, and the one whose door
    // would open the seam outright: a ruling where the record has none.
    ["the ruling lookup", "rulings", LABEL_CLAUSE, { ...rulings,
      seamRulingRef: planted(cardRef =>
        cardRef === DOOR
          ? Object.freeze({ decision_ref: FIXTURE_DECISION_IDS[0], store_ref: "github:checks" })
          : rulings.seamRulingRef(cardRef)) }],
  ];

  const savedEnv = saveEnv(STORE_CREDENTIALS);
  try {
    for (const [what, label, clause, plant] of plants) {
      let failure = null;
      try {
        await assertNoExportAnswersALabel(`control.${label}`, plant);
      } catch (thrown) {
        failure = thrown;
      }
      assert.ok(failure !== null, `the sweep passed a label door planted in ${what}`);
      assert.ok(String(failure?.message ?? "").includes(clause),
        `the door in ${what} was caught by a clause other than ${clause}: ${failure?.message}`);
    }

    // AND THE CALIBRATION, which is what makes each red above attributable: the
    // same five namespaces, unplanted, go through the same sweep untouched.
    for (const [label, namespace] of LABEL_SWEPT_NAMESPACES())
      await assertNoExportAnswersALabel(`calibration.${label}`, namespace);
  } finally {
    restoreEnv(savedEnv);
  }
});

test("STAGING CONTROL: a word's twin is the same kind of string, and differs from it", () => {
  // THE SWEEP'S COMPARISON RESTS ENTIRELY ON THIS, so it is asserted rather than
  // assumed: a twin that stopped matching a pattern its word matched would make
  // every honest refusal look like a door, and a twin equal to its word would
  // make every door look honest.
  const patterns = [/^[0-9a-f]{40}$/, /^sha256:[0-9a-f]{64}$/, /^[a-z0-9][a-z0-9._-]{0,63}$/,
    /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/, /^[A-Za-z0-9][A-Za-z0-9 ._/()-]{0,99}$/];
  const words = new Set([...RETIRED_FIXTURE_TRIGGERS]);
  for (const [, namespace] of LABEL_SWEPT_NAMESPACES())
    for (const word of labelsOf(namespace)) words.add(word);
  for (const word of words) {
    const twin = twinOf(word);
    assert.equal(typeof twin, "string", `no twin was built for ${safeLabel(word)}`);
    assert.notEqual(twin, word, `the twin of ${safeLabel(word)} is the word itself`);
    assert.equal(twin.length, word.length, `the twin of ${safeLabel(word)} changed length`);
    for (const pattern of patterns)
      assert.equal(pattern.test(twin), pattern.test(word),
        `the twin of ${safeLabel(word)} changed which shapes it matches`);
  }
  // And a word with nothing to move has no twin, which the sweep reads as "no
  // comparison to make" rather than as a passing comparison.
  assert.equal(twinOf("---"), null);
  assert.equal(twinOf(""), null);
  // And the substitution reaches keys and leaves at every depth the sweep builds.
  assert.deepEqual(substituted({ a: ["a", { a: "a" }] }, "a", "b"),
    { b: ["b", { b: "b" }] });
});

test("RULED: a pasted decision id is what opens the seam, and nothing else", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const opened = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.ok(!GATE_DELEGATION_DIGESTS.has(digest(opened)),
    "the staged ruling did not open the seam");
  assert.equal(opened.ruling_decision_ref, FIXTURE_DECISION_IDS[0]);

  // AND THE SHIPPED MODULE IN THIS SAME PROCESS IS UNTOUCHED BY THE STAGING,
  // which is the clause that makes every ruled result above attributable to the
  // staged copy rather than to something the staging did to the real one. Before
  // Joe's paste that was shown by the shipped reader still giving the gate's
  // answer; it is ruled now, so what is shown is that it still names HIS
  // decision id — not the fixture one — and still has no store to read, while
  // the staged copy beside it just read a row.
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    const shipped = await readers.readPredecessorOutcomeEvidence({
      stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
    assert.equal(shipped.ruling_decision_ref, PASTED_DECISION_IDS[0],
      "the staging reached the shipped ruling table");
    assert.equal(shipped.decision, "refuse");
    assert.equal(shipped.finding, null);
    assert.equal(shipped.unavailable_because,
      "the connection target for this store is not configured in this process");
    // And the two answers came from the same real reader code over different
    // ruling tables and different stores, so neither is the other's.
    assert.notEqual(digest(shipped), digest(opened));
  } finally {
    restoreEnv(saved);
  }

  // And a staged copy whose lines went back to null is shut again, which is the
  // only direction this test can prove the gate closes in.
  const unruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE, unruled: true });
  const shut = await unruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(digest(shut), digest(gateOf(unruled).readGateZeroPredecessorJoin()),
    "an unruled reader read the fixture store anyway");
});

test("RULED: the predecessor reader admits only an accepted outcome whose receipt hash matches", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const accepted = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(accepted.status, "evidence_returned");
  assert.equal(accepted.decision, "report");
  assert.equal(accepted.finding, "predecessor_outcome_accepted_with_matching_hash");
  assert.equal(accepted.hash_match, "held");
  assert.equal(accepted.work_request_ref, "WR-000046");
  assert.equal(accepted.card_ref, "card:11");
  assert.equal(accepted.ruling_decision_ref, FIXTURE_DECISION_IDS[0],
    "card 11 took the wrong card's ruling");
  assert.equal(accepted.store_ref, "record-layer:work-request-outcome-feedback");
  assertSwept("card11.accepted", accepted);
  // No caller byte and no store byte: the step ref the caller named is not in
  // the answer, and neither is a feedback ref, an outcome or a timestamp.
  assert.ok(!Object.hasOwn(accepted, "step_ref"));
  for (const gone of ["feedback_ref", "stored_outcome", "accepted_at", "outcome"])
    assert.ok(!Object.hasOwn(accepted, gone), `${gone} is echoed into the answer`);

  // A forged hash — well-formed, and no row carries it.
  const forged = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_FORGED_HASH });
  assert.equal(forged.decision, "refuse");
  assert.equal(forged.finding, "predecessor_outcome_acceptance_receipt_hash_mismatch");
  assert.equal(forged.hash_match, "failed");

  // A proposal nobody signed. The near miss, and it refuses before the hash.
  const pending = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr40-repository-outcome", outcomeHash: `sha256:${"5".repeat(64)}` });
  assert.equal(pending.decision, "refuse");
  assert.equal(pending.finding, "predecessor_outcome_not_accepted");

  // No outcome row at all.
  const absent = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr54-backup-recovery-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(absent.decision, "refuse");
  assert.equal(absent.finding, "predecessor_outcome_absent");

  // The scheduler predecessor is not outcome-backed, and says so by name.
  const scheduler = await ruled.readPredecessorOutcomeEvidence({
    stepRef: V5_A02_SCHEDULER_STEP_REF, outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(scheduler.decision, "refuse");
  assert.equal(scheduler.reason_id, "scheduler_predecessor_not_outcome_backed");
  assertSwept("card11.scheduler", scheduler);

  // A step outside the frozen four, and a malformed hash.
  const unknown = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:something-else", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(unknown.reason_id, "unknown_predecessor_step");
  const badHash = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: "sha256:nope" });
  assert.equal(badHash.reason_id, "predecessor_query_invalid");
  assert.equal(badHash.invalid_field, "outcomeHash");
});

test("RULED: the receipt's hash is the one consulted, not the proposal's", async () => {
  // Found by mutation: in production ops.accept_sourced_work_request_outcome_feedback
  // writes the proposal hash and the receipt hash equal, so swapping the two
  // fields changed nothing any test could see, and the most load-bearing clause
  // in the slice was unproved. This staging's rows separate them, which no
  // production row does.
  const ruled = await stagedReaders({ storeFile: RECEIPT_STORE_FILE });
  const asked = receiptStores.FIXTURE_ASKED_HASH;

  // The receipt carries the asked-about hash; the proposal carries another.
  const receiptMatches = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: asked });
  assert.equal(receiptMatches.finding, "predecessor_outcome_accepted_with_matching_hash",
    "the reader stopped consulting the acceptance receipt's hash");
  assert.equal(receiptMatches.hash_match, "held");

  // The mirror: the PROPOSAL carries the asked-about hash and no receipt does.
  const proposalOnly = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr40-repository-outcome",
    outcomeHash: receiptStores.FIXTURE_PROPOSAL_ONLY_HASH,
  });
  assert.equal(proposalOnly.finding, "predecessor_outcome_acceptance_receipt_hash_mismatch",
    "a row matched on its proposal hash instead of its receipt hash");
  assert.equal(proposalOnly.decision, "refuse");

  // A receipt whose work_request_card detail is absent is a MISSING ROW. The
  // earlier store synthesized it as accepted with a null outcome and it could
  // still be admitted.
  const detailAbsent = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr54-backup-recovery-outcome", outcomeHash: asked });
  assert.equal(detailAbsent.finding, "predecessor_outcome_detail_absent");
  assert.equal(detailAbsent.decision, "refuse");
  for (const result of [receiptMatches, proposalOnly, detailAbsent]) assertSwept("receipt", result);
});

test("RULED: an omitted hash resolves the current accepted predecessor revision", async () => {
  const ruled = await stagedReaders({ storeFile: RECEIPT_STORE_FILE });
  const current = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr40-repository-outcome",
  });
  assert.equal(current.finding, "predecessor_outcome_accepted_with_matching_hash");
  assert.equal(current.decision, "report");
  assert.equal(current.accepted_rows_seen, 3);
  assert.equal(current.hash_match, "held");
});

test("RULED: card 12 reads the rows bin/run-scheduled.sh actually writes", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const read = (canaryRunKey, serviceKey = "carr-fleet-sync") =>
    ruled.readSchedulerCanaryEvidence({ serviceKey, canaryRunKey });

  const joined = await read("canary-join");
  assert.equal(joined.decision, "report");
  assert.equal(joined.finding, "scheduler_canary_and_observation_join");
  assert.equal(joined.receipt_binding, "held");
  assert.equal(joined.observation_after_dispatch, "held");
  assert.equal(joined.canary_match, "held");
  assert.equal(joined.card_ref, "card:12");
  assert.equal(joined.ruling_decision_ref, FIXTURE_DECISION_IDS[1]);
  assert.equal(joined.store_ref, "control-plane:ops.service+ops.run");
  assertSwept("card12.joined", joined);
  for (const gone of ["dispatched_at", "observed_at", "scheduler_step_ref"])
    assert.ok(!Object.hasOwn(joined, gone), `${gone} is echoed into the answer`);

  // ONE NEGATIVE ROW PER FIELD the receipt-binding clause reads. Each changes a
  // single field of the row the wrapper writes, so a clause that stopped reading
  // one field fails on exactly one case.
  //
  // THE LAST FOUR ARE THE MUTATION CONTROL for the receipt itself, and they are
  // the reason this clause is no longer "evidence_ref is not null". Every one of
  // them carries a non-null evidence ref and would have bound under the old
  // clause: a well-formed receipt minted YESTERDAY (a file an earlier run left
  // behind that this run's child never refreshed), one minted at the very
  // instant of dispatch, one minted for a DIFFERENT job, and the free-form
  // `ops.run:...` text 121 production rows actually carry. A receipt that a
  // previous run could have left on disk must not bind this run's row, and
  // these are the cases that prove it.
  for (const key of ["canary-today", "canary-hand-run", "canary-probe",
                     "canary-foreign-wrapper", "canary-stale-receipt",
                     "canary-instant-receipt", "canary-foreign-receipt",
                     "canary-freeform-receipt"]) {
    const result = await read(key);
    assert.equal(result.decision, "refuse", key);
    assert.equal(result.finding, "scheduler_canary_not_bound_to_receipt", key);
    assert.equal(result.receipt_binding, "failed", key);
    assertSwept(`card12.${key}`, result);
  }

  // Dispatch and observation share one instant: strictly-after is strict.
  const sameInstant = await read("canary-same-instant");
  assert.equal(sameInstant.decision, "refuse");
  assert.equal(sameInstant.finding, "scheduler_observation_not_after_dispatch");
  assert.equal(sameInstant.observation_after_dispatch, "failed");

  const mismatch = await read("canary-mismatch");
  assert.equal(mismatch.decision, "refuse");
  assert.equal(mismatch.finding, "scheduler_observation_canary_mismatch");
  assert.equal(mismatch.canary_match, "failed");

  for (const [key, expected] of [
    ["canary-inflight", "scheduler_observation_absent"],
    ["canary-never-ran", "scheduler_dispatch_row_absent"],
  ]) {
    const result = await read(key);
    assert.equal(result.decision, "refuse", key);
    assert.equal(result.finding, expected, key);
    assert.equal(result.receipt_binding, "unknown",
      `${key} guessed a clause with no row to read it from`);
  }

  // The store-identity check that used to be proved here, by a canary key this
  // fixture answered about another store, now runs against card 13 in the
  // faulted-store test below — because an exported key that changes what a
  // fetcher answers is the label this correction removed.

  // A LEDGER WHOSE EVERY IDENTIFIER IS A PRIVILEGED WORD, and all three clauses
  // hold anyway. Before the store reduced identifiers to digests, this row's
  // service key, run key and evidence ref went into the answer verbatim.
  const greenNames = await read("canary-green-names", "release-canary");
  assert.equal(greenNames.decision, "report");
  assert.equal(greenNames.finding, "scheduler_canary_and_observation_join");
  assertSwept("card12.greenNames", greenNames);

  const noService = await read("canary-join", "carr-not-in-the-ledger");
  assert.equal(noService.finding, "scheduler_service_row_absent");

  const badQuery = await ruled.readSchedulerCanaryEvidence({ serviceKey: "Not A Key", canaryRunKey: "x" });
  assert.equal(badQuery.reason_id, "scheduler_query_invalid");
  assert.equal(badQuery.invalid_field, "serviceKey");
});

test("SCHEMA: the source_kind the clause requires is one db/schema.sql permits", () => {
  // The defect this replaces: the clause required `source_kind === "scheduler"`,
  // which ops.run's own check constraint forbids, so it could never have matched
  // a real row. Both halves are read from the files that define them.
  const repo = fileURLToPath(new URL("../..", import.meta.url));
  const schema = readFileSync(join(repo, "db/schema.sql"), "utf8");
  const constraint = /run_source_kind_check CHECK \(\(source_kind = ANY \(ARRAY\[([^\]]*)\]\)\)\)/.exec(schema);
  assert.ok(constraint, "ops.run's source_kind constraint is no longer where this test reads it");
  const permitted = [...constraint[1].matchAll(/'([a-z_]+)'::text/g)].map(one => one[1]);
  assert.deepEqual(permitted.sort(), ["collector", "operator", "registry", "wrapper"]);
  assert.ok(!permitted.includes("scheduler"), "ops.run permits a scheduler source_kind after all");

  const wrapper = readFileSync(join(repo, "bin/run-scheduled.sh"), "utf8");
  assert.ok(/--source-kind wrapper --source-ref bin\/run-scheduled\.sh/.test(wrapper),
    "bin/run-scheduled.sh no longer writes the source kind and ref the clause binds to");

  // THE RECEIPT IS MINTED BY THE WRAPPER AND BY NOTHING ELSE, read off the
  // wrapper itself rather than taken on trust. The token this store parses is
  // built in that file out of its own clock, its own entropy and a hash of the
  // run key; there is no flag, no path and no environment variable by which a
  // caller, a plist or the child could hand one in. The first draft took
  // `--evidence-ref-file PATH` and promoted whatever that file held, which is
  // the shape these two assertions exist to keep from coming back.
  assert.ok(/candidate="carr-run-receipt:v1:\$minted_at:\$nonce:\$run_key_hash"/.test(wrapper),
    "bin/run-scheduled.sh no longer mints the receipt this clause parses");
  assert.equal(/^\s*--evidence-ref-file\)/m.test(wrapper), false,
    "bin/run-scheduled.sh takes a caller-supplied receipt path again");
  const storeSource = readFileSync(join(SRC, "gate-zero-seam-stores.v5.js"), "utf8");
  assert.ok(/carr-run-receipt:v1:/.test(storeSource),
    "the store no longer parses the wrapper's minted receipt shape");
  // And the clause binds to those exact two strings, not to an invented one.
  const source = readFileSync(join(SRC, READERS_FILE), "utf8");
  assert.ok(/const SCHEDULER_SOURCE_KIND = "wrapper";/.test(source));
  assert.ok(/const SCHEDULER_SOURCE_REF = "bin\/run-scheduled\.sh";/.test(source));
  assert.equal(/source_kind[^\n]*===\s*"scheduler"/.test(source), false,
    "the reader still compares source_kind against an impossible value");
});

test("RULED: the conclusion reader returns GitHub's own word, and translates nothing", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const sha = fixtureStores.FIXTURE_COMMIT_SHA;

  const success = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "main canary" });
  assert.equal(success.decision, "report");
  assert.equal(success.finding, "gate_conclusion_observed");
  assert.equal(success.conclusion, "success");
  assert.equal(success.store_ref, "github:checks");
  assert.equal(success.card_ref, "card:13");
  assert.equal(success.ruling_decision_ref, FIXTURE_DECISION_IDS[2]);
  assertSwept("card13.success", success);

  // A re-run: the later conclusion is the one reported, and the count is visible.
  const rerun = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "ops/ci.sh --strict" });
  assert.equal(rerun.conclusion, "failure");
  assert.equal(rerun.completed_runs_seen, 2);
  assert.equal(rerun.decision, "report",
    "a reported conclusion is a reading, not a verdict — failure is still a reading");

  // Queued: no conclusion exists, so none is invented.
  const queued = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "local-db-ci --class migration" });
  assert.equal(queued.decision, "refuse");
  assert.equal(queued.finding, "gate_conclusion_check_absent");
  assert.equal(queued.conclusion, null);

  // A completed run that belongs to a different commit does not answer for this one.
  const wrongCommit = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "Backup artifact" });
  assert.equal(wrongCommit.finding, "gate_conclusion_check_absent");
  assert.equal(wrongCommit.check_runs_seen, 1,
    "the row was seen and still did not answer, which is the point");

  // A word GitHub does not document is not passed through: the only conclusion
  // strings a consumer sees are constants out of the reader module.
  const invented = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "pg_dump -> age-encrypt -> artifact" });
  assert.equal(invented.finding, "gate_conclusion_unrecognized");
  assert.equal(invented.conclusion, null);
  assert.equal(invented.decision, "refuse");
  assertSwept("card13.invented", invented);

  const badQuery = await ruled.readGateConclusionEvidence({ headSha: "nope", checkName: "main canary" });
  assert.equal(badQuery.reason_id, "gate_conclusion_query_invalid");
  assert.equal(badQuery.invalid_field, "headSha");
});

test("RULED: a ruling naming a store this reader does not serve opens nothing", async () => {
  // Criterion (c). The earlier reader checked only that the ruled ref was a
  // member of a global union, then called its own hard-coded fetcher and
  // reported the ruled ref regardless — so a ruling naming `github:checks` for
  // card 11 still queried the database and said github:checks.
  //
  // The REAL store module is staged here with no DSN configured: if a row were
  // fetched the answer would be `predecessor_outcome_store_unreachable`. It is
  // the gate's own refusal instead, so nothing was opened.
  const ruled = await stagedReaders({ card11StoreRef: "github:checks" });
  const saved = process.env.DATABASE_URL_READER;
  delete process.env.DATABASE_URL_READER;
  try {
    const result = await ruled.readPredecessorOutcomeEvidence({
      stepRef: "step:wr46-dissolution-outcome", outcomeHash: `sha256:${"4".repeat(64)}` });
    // THE SAME TREE'S GATE, and that is the PR 1004 re-review's finding rather
    // than a nicety. This clause used to compare against the SHIPPED gate's
    // answer, and it passed for the wrong reason: the gate read any non-null
    // ruling as a bound seam, so a staged tree whose card 11 named another store
    // produced a gate answer identical to the shipped one — the gate calling the
    // seam bound while this reader refused it. The gate now asks the reader's own
    // predicate, so the staged gate reports card 11 UNBOUND and its answer is no
    // longer the shipped bytes. Both halves are asserted.
    const stagedGate = gateOf(ruled);
    assert.equal(digest(result), digest(stagedGate.readGateZeroPredecessorJoin()),
      "a ruling naming the wrong store opened the seam anyway");
    assert.equal(stagedGate.readGateZeroPredecessorJoin().predecessor_outcome_reader_bound, false,
      "the gate reports card 11 bound while this reader refuses its ruling");
    assert.equal((await stagedGate.emitGateZeroOutcome()).predecessor_outcome_reader_bound, false,
      "the emitted answer reports card 11 bound while this reader refuses its ruling");
    assert.notEqual(digest(result), digest(readGateZeroPredecessorJoin()),
      "the staged answer is the shipped one, so the gate still reads a mismatched ruling as bound");
    // The other two cards are ruled normally in this same staging and do open.
    const scheduler = await ruled.readSchedulerCanaryEvidence({
      serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" });
    assert.equal(scheduler.reason_id, "scheduler_ledger_unreachable",
      "card 12 did not open, so the staging proved nothing about card 11");
  } finally {
    if (saved !== undefined) process.env.DATABASE_URL_READER = saved;
  }
});

test("RULED: with the real stores and nothing configured, every seam reports unreachable", async () => {
  const ruled = await stagedReaders({});
  const saved = {};
  for (const name of ["DATABASE_URL_READER", "GITHUB_TOKEN", "GITHUB_REPOSITORY"]) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  try {
    const predecessor = await ruled.readPredecessorOutcomeEvidence({
      stepRef: "step:wr46-dissolution-outcome", outcomeHash: `sha256:${"4".repeat(64)}` });
    assert.equal(predecessor.reason_id, "predecessor_outcome_store_unreachable");
    assert.equal(predecessor.unavailable_because,
      "the connection target for this store is not configured in this process");
    assert.equal(predecessor.finding, null, "an unreachable store produced a finding anyway");

    const scheduler = await ruled.readSchedulerCanaryEvidence({
      serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" });
    assert.equal(scheduler.reason_id, "scheduler_ledger_unreachable");

    const conclusion = await ruled.readGateConclusionEvidence({
      headSha: "a".repeat(40), checkName: "main canary" });
    assert.equal(conclusion.reason_id, "gate_conclusion_source_unreachable");
    assert.equal(conclusion.unavailable_because,
      "the checks source credentials are not configured in this process");

    for (const result of [predecessor, scheduler, conclusion]) assertSwept("unreachable", result);
  } finally {
    for (const [name, value] of Object.entries(saved))
      if (value !== undefined) process.env[name] = value;
  }
});

test("STORES: a query that addresses no row says so, and does not say it broke", async () => {
  // The guarded boundary answers anything it cannot recognize with the generic
  // `the call did not finish`, which is right — and it is also why the specific
  // refusals underneath it have to be asserted by name. Without this, the inner
  // refusal could go back to a native TypeError and nothing would notice: the
  // boundary would catch it and the sweep would pass.
  const saved = saveEnv(STORE_CREDENTIALS);
  process.env.DATABASE_URL_READER = "postgres://nothing/at-all";
  process.env.GITHUB_TOKEN = "a-token-this-test-wrote";
  const notAddressed = "the query did not address a row";
  try {
    for (const [name, fetcher, query] of [
      ["predecessor", stores.fetchPredecessorOutcomeRows, {}],
      // THE SERVICE IS THE LEDGER'S REQUIRED ADDRESS, and since 2026-09-12 it
      // is the only one: an omitted `canaryRunKey` asks for the latest run this
      // service's wrapper minted a receipt for, which is a row the ledger can
      // find on its own. A NAMED-BUT-EMPTY one is still no address at all, and
      // the last shape below proves it.
      ["ledger", stores.fetchSchedulerLedgerRows, { canaryRunKey: "gate-zero-run-1" }],
      ["checks", stores.fetchCheckConclusionRows, { headSha: "a".repeat(40) }],
    ])
      for (const shape of [undefined, null, query, { ...query, extra: 1 }])
        await assert.rejects(() => fetcher(shape),
          error => stores.isSeamStoreUnreachable(error) && error.because === notAddressed,
          `${name} answered a query that addressed no row with something else`);
    // The ledger's optional address is optional, not unchecked: present and
    // unusable is refused exactly the way an absent service key is.
    await assert.rejects(
      () => stores.fetchSchedulerLedgerRows({ serviceKey: "carr-fleet-sync", canaryRunKey: "" }),
      error => stores.isSeamStoreUnreachable(error) && error.because === notAddressed,
      "a named-but-empty canary run key was treated as an omitted one");
  } finally {
    restoreEnv(saved);
  }
});

test("STORES: the real store module refuses rather than guessing when nothing is configured", async () => {
  const saved = process.env.DATABASE_URL_READER;
  delete process.env.DATABASE_URL_READER;
  try {
    await assert.rejects(() => stores.fetchPredecessorOutcomeRows({ workRequestRef: "WR-000046" }),
      error => stores.isSeamStoreUnreachable(error)
        && error.because === "the connection target for this store is not configured in this process");
  } finally {
    if (saved !== undefined) process.env.DATABASE_URL_READER = saved;
  }
});

test("RULED: nothing a faulted store does to a reader gets past the reader's boundary", async () => {
  // THE READER'S OWN BOUNDARY, reached the three ways a store can reach it, over
  // the faulted store instance — NOT over a fixture answering an address.
  //
  // One fault per card, so one staging proves all three and no fetcher needs a
  // query value to decide which fault to perform. What is asserted of every
  // answer is the same thing: it is the reader's own, it carries no byte of the
  // store's, and no engine-built frame comes back inside it.
  const ruled = await stagedReaders({ storeFile: FAULT_STORE_FILE });

  // CARD 12 — the store throws a BARE STRING, `throw "allow"`, the value the
  // third re-review named. It is not an Error, so nothing about it is readable
  // as a reason; `fetchOrRefuse` answers with the reader's own closed phrase.
  const rawThrow = await ruled.readSchedulerCanaryEvidence({
    serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" });
  assert.equal(rawThrow.decision, "refuse");
  assert.equal(rawThrow.reason_id, "scheduler_ledger_unreachable");
  assert.equal(rawThrow.unavailable_because, "the ledger did not answer");
  assert.equal(rawThrow.finding, null, "an unreachable store produced a finding anyway");
  assertSwept("faulted.raw-throw", rawThrow);

  // CARD 11 — the store RETURNS, and its answer throws from the getter the
  // reader reads OUTSIDE its own try. That throw lands in the reader itself,
  // which is the only thing the outer boundary is there for, and the answer is
  // the gate's own object rather than anything the store produced.
  const hostileAnswer = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: `sha256:${"4".repeat(64)}` });
  assert.equal(digest(hostileAnswer), digest(readGateZeroPredecessorJoin()),
    "a throw inside the reader escaped instead of closing the seam");
  // AND THE IDENTITY IS WHAT IS ASSERTED HERE, not the word sweep, which is the
  // 2026-09-12 review's amendment 3 finding applied to this line. Byte-for-byte
  // equality with the gate's own answer admits NO new string at all, which is
  // stronger than sweeping its words; and the answer is the SHIPPED gate's, which
  // this branch authored, so it is not upstream output and may not take the
  // amendment 3 exemption. Its own vocabulary is swept in
  // gate-zero-assurance.v5.test.mjs — against the pre-PR commit's, with every
  // string this branch adds enumerated and the baseline regenerated from that
  // commit.
  assert.ok(!PRE_PR_GATE_ANSWER_DIGESTS.has(digest(hostileAnswer)),
    "the shipped gate answers the pre-PR baseline's bytes, so this clause proves nothing");

  // CARD 13 — the store answers about a DIFFERENT store than the ruling named,
  // over rows that would otherwise report a conclusion of "success". The reader
  // refuses on the identity check, and the store it names in the refusal is the
  // RULED one, not the one that replied.
  const foreignStore = await ruled.readGateConclusionEvidence({
    headSha: "a".repeat(40), checkName: "main canary" });
  assert.equal(foreignStore.decision, "refuse");
  assert.equal(foreignStore.reason_id, "store_ref_not_the_ruled_one");
  assert.equal(foreignStore.finding, null);
  assert.notEqual(foreignStore.conclusion, "success",
    "the reader reported over rows it did not ask the ruled store for");
  assert.equal(foreignStore.store_ref, "github:checks",
    "the answer reported the store that replied instead of the store that was ruled");
  assertSwept("faulted.foreign-store", foreignStore);

  // AND NOT ONE BYTE OF ANY OF IT IS THE STORE'S. The hostile answer's getters
  // throw with the marker in their message; the raw throw is the word `allow`
  // itself; the foreign answer names a store. None of the three may appear.
  for (const [at, answer] of [["raw-throw", rawThrow], ["hostile-answer", hostileAnswer],
    ["foreign-store", foreignStore]]) {
    const text = JSON.stringify(answer) ?? "";
    assert.ok(!text.includes(HOSTILE_MARKER), `${at} returned the store's own text`);
    assert.ok(!text.includes("allow"), `${at} returned the value the store threw`);
    // A frame is the shape an engine-built stack has: `at <name> (<file>:<n>:<n>)`.
    assert.equal(/\bat [\w.<>]+ \(/.test(text), false,
      `${at} carries an engine-built frame, which is the reader's own file and line`);
  }
});

test("SWEEP: the same sweep again, over real rows, with every credential configured",
  async () => {
  // FINDING 2 OF THE THIRD RE-REVIEW. The credential-less sweep never reaches a
  // successful return, so `detail_present: entry !== undefined` — a bare `true`
  // out of an exported function — sat on the public surface unswept. The store
  // opens its own connection, so the only way to run its successful path is to
  // put a `pg` where its own dynamic import finds one.
  const target = stageWithFakePg({});
  const staged = {
    readers: await import(pathToFileURL(join(target, READERS_FILE)).href),
    rulings: await import(pathToFileURL(join(target, RULINGS_FILE)).href),
    stores: await import(pathToFileURL(join(target, STORES_FILE)).href),
  };

  const savedEnv = saveEnv(STORE_CREDENTIALS);
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = fakeChecksSource(HOSTILE_CHECK_RUNS, calls);
  process.env.DATABASE_URL_READER = "postgres://fake/rows";
  process.env.GITHUB_TOKEN = "a-token-this-test-wrote";
  process.env.GITHUB_REPOSITORY = AUTHORITATIVE_REPOSITORY;
  const sha = "a".repeat(40);
  const askedHash = `sha256:${"4".repeat(64)}`;
  try {
    // 1. THE STORES ANSWER WITH ROWS, and the rows are what is swept.
    const predecessor = await staged.stores.fetchPredecessorOutcomeRows(
      { workRequestRef: "WR-000046" });
    assert.ok(predecessor.rows.length > 0, "the predecessor store returned no rows to sweep");
    assertSwept("rows.predecessor", predecessor);
    for (const row of predecessor.rows) {
      assert.equal(typeof row.detail_row_count, "number",
        "the detail field is a boolean again");
      assert.ok(!Object.hasOwn(row, "detail_present"),
        "detail_present is back, and its name carries a privileged word besides");
    }

    const ledger = await staged.stores.fetchSchedulerLedgerRows(
      { serviceKey: "release-canary", canaryRunKey: "allow-commit-green" });
    assert.ok(ledger.rows.length > 0, "the ledger store returned no rows to sweep");
    assertSwept("rows.ledger", ledger);

    const checks = await staged.stores.fetchCheckConclusionRows(
      { headSha: sha, checkName: "main canary" });
    assert.ok(checks.rows.length > 0, "the checks store returned no rows to sweep");
    assert.ok(calls.length > 0, "the checks store never called its source");
    assertSwept("rows.checks", checks);

    for (const answer of [predecessor, ledger, checks])
      assert.ok(!JSON.stringify(answer).includes(HOSTILE_MARKER),
        `a store carried the database's own text out: ${JSON.stringify(answer).slice(0, 200)}`);

    // 2. THE READERS REPORT OVER THOSE ROWS. Each of the three reaches its
    //    REPORTING finding — the successful return, the one the sweep had never
    //    seen — over rows whose every free-form column is a privileged word.
    const report11 = await staged.readers.readPredecessorOutcomeEvidence(
      { stepRef: "step:wr46-dissolution-outcome", outcomeHash: askedHash });
    assert.equal(report11.finding, "predecessor_outcome_accepted_with_matching_hash");
    assert.equal(report11.decision, "report");

    const report12 = await staged.readers.readSchedulerCanaryEvidence(
      { serviceKey: "release-canary", canaryRunKey: "allow-commit-green" });
    assert.equal(report12.finding, "scheduler_canary_and_observation_join");
    assert.equal(report12.decision, "report");

    // THE REAL STORE'S RECEIPT PARSE, pinned over real rows rather than over the
    // fixture's copy of it. Same row, same clocks, same wrapper — one field
    // moved: the receipt is minted for a DIFFERENT run key. A store that
    // answered "is this the run's own receipt" from the row instead of from the
    // receipt's own bytes would join here, and nothing else in this file would
    // notice.
    const foreign = await staged.readers.readSchedulerCanaryEvidence(
      { serviceKey: "release-canary-foreign-receipt", canaryRunKey: "allow-commit-green" });
    assert.equal(foreign.finding, "scheduler_canary_not_bound_to_receipt");
    assert.equal(foreign.receipt_binding, "failed");
    assertSwept("rows.foreign-receipt", foreign);

    // And the stale one, for the same reason: the receipt is for the right run,
    // minted the day BEFORE this dispatch. The row's own observation is still
    // the latest instant it carries, so a store that read the mint stamp off
    // `observed_at` would join here.
    const stale = await staged.readers.readSchedulerCanaryEvidence(
      { serviceKey: "release-canary-stale-receipt", canaryRunKey: "allow-commit-green" });
    assert.equal(stale.finding, "scheduler_canary_not_bound_to_receipt");
    assert.equal(stale.receipt_binding, "failed");
    assertSwept("rows.stale-receipt", stale);

    // And a ref that is a near miss for the token rather than the token.
    const nearMiss = await staged.readers.readSchedulerCanaryEvidence(
      { serviceKey: "release-canary-near-miss-receipt", canaryRunKey: "allow-commit-green" });
    assert.equal(nearMiss.finding, "scheduler_canary_not_bound_to_receipt");
    assert.equal(nearMiss.receipt_binding, "failed");
    assertSwept("rows.near-miss-receipt", nearMiss);

    const report13 = await staged.readers.readGateConclusionEvidence(
      { headSha: sha, checkName: "main canary" });
    assert.equal(report13.finding, "gate_conclusion_observed");
    assert.equal(report13.conclusion, "success");

    for (const [name, report] of [["11", report11], ["12", report12], ["13", report13]]) {
      assert.ok(!GATE_DELEGATION_DIGESTS.has(digest(report)), `card ${name} did not open`);
      assertSwept(`rows.report.${name}`, report);
      assert.ok(!JSON.stringify(report).includes(HOSTILE_MARKER), `card ${name} leaked store text`);
    }

    // 3. AND THE WHOLE EXPORT SWEEP AGAIN, with the credentials in place, so
    //    every hostile argument reaches the successful path rather than the
    //    unconfigured refusal it used to stop at.
    for (const label of ["readers", "rulings", "stores"])
      await sweepEveryInvocation(`rows.${label}`, staged[label]);

    // 4. THE FOUR WAYS A DEPENDENCY CAN THROW SOMETHING UNSPEAKABLE, each one
    //    addressed by its own query value, each answered by a registered code.
    for (const scenario of ["throw-a-string", "throw-a-true", "throw-an-object", "throw-a-native"]) {
      await assert.rejects(
        () => staged.stores.fetchPredecessorOutcomeRows({ workRequestRef: scenario }),
        error => error.because === "the query did not finish", scenario);
      await assertNothingRawEscapes(`rows.throws.${scenario}`, () =>
        privilegedCallers()[0](query => staged.stores.fetchPredecessorOutcomeRows(query),
          { workRequestRef: scenario }));
    }

    // 5. AND THE TWO THAT LAND OUTSIDE THE QUERY'S OWN TRY — the pool
    //    constructor and the close in the finally — which is what the outermost
    //    boundary exists for.
    for (const [target_, expected] of [
      ["postgres://fake/pool-throws-a-raw-value", "the call did not finish"],
      ["postgres://fake/end-throws-a-raw-value", "the call did not finish"],
    ]) {
      process.env.DATABASE_URL_READER = target_;
      await assert.rejects(
        () => staged.stores.fetchPredecessorOutcomeRows({ workRequestRef: "WR-000046" }),
        error => error.because === expected, target_);
      await assertNothingRawEscapes(`rows.outer.${target_}`, () =>
        privilegedCallers()[0](query => staged.stores.fetchPredecessorOutcomeRows(query),
          { workRequestRef: "WR-000046" }));
    }
  } finally {
    globalThis.fetch = realFetch;
    restoreEnv(savedEnv);
  }
});

test("STORES: the checks store serves one repository, and refuses every other", async () => {
  // FINDING 3 OF THE THIRD RE-REVIEW. The store read GITHUB_REPOSITORY and built
  // its URL out of it, so whoever set that variable chose whose check runs would
  // be reported under the label `github:checks` — and a repository the caller
  // controls answers as readily as this one.
  const foreign = "someone-else/carr-system";
  const refused = "the configured checks repository is not the one this file serves";
  const savedEnv = saveEnv(STORE_CREDENTIALS);
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = fakeChecksSource(HOSTILE_CHECK_RUNS, calls);
  process.env.GITHUB_TOKEN = "a-token-this-test-wrote";
  const query = { headSha: "a".repeat(40), checkName: "main canary" };
  try {
    // (a) A FOREIGN REPOSITORY IN THE ENVIRONMENT IS REFUSED, and the refusal
    //     happens before anything is called — a refusal that first fetched would
    //     still have told the foreign source which commit we are asking about.
    process.env.GITHUB_REPOSITORY = foreign;
    await assert.rejects(() => stores.fetchCheckConclusionRows(query),
      error => stores.isSeamStoreUnreachable(error)
        && error.store_ref === "github:checks" && error.because === refused);
    assert.deepEqual(calls, [], "the store called the foreign repository before refusing it");

    // (b) AND SO IS A CALLER THAT NAMES ONE, under any of the three names an
    //     API client would reach for. A caller may address a commit and a check;
    //     it may not address a repository at all.
    delete process.env.GITHUB_REPOSITORY;
    for (const field of ["repository", "repo", "owner"]) {
      await assert.rejects(() => stores.fetchCheckConclusionRows({ ...query, [field]: foreign }),
        error => error.because === refused, field);
      await assert.rejects(
        () => stores.fetchCheckConclusionRows({ ...query, [field]: "jbookout/some-other-repo" }),
        error => error.because === refused, `${field} (same owner)`);
    }
    assert.deepEqual(calls, [], "a caller-named repository was fetched");

    // (c) UNSET IS THE SUPPORTED STATE: the URL comes from the constant.
    await stores.fetchCheckConclusionRows(query);
    assert.equal(calls.length, 1);
    assert.ok(calls[0].url.includes(`/repos/${AUTHORITATIVE_REPOSITORY}/commits/`),
      `the checks call went somewhere else: ${calls[0].url}`);

    // (d) AND A MATCHING VALUE IS HARMLESS — this is a binding, not a ban.
    process.env.GITHUB_REPOSITORY = AUTHORITATIVE_REPOSITORY;
    await stores.fetchCheckConclusionRows(query);
    assert.equal(calls.length, 2);
    assert.ok(calls[1].url.includes(`/repos/${AUTHORITATIVE_REPOSITORY}/commits/`));
  } finally {
    globalThis.fetch = realFetch;
    restoreEnv(savedEnv);
  }

  // The constant is module-private, and the environment is no longer a source
  // for it: `configured("GITHUB_REPOSITORY"` is what this replaced.
  const source = readFileSync(join(SRC, STORES_FILE), "utf8");
  assert.ok(source.includes(`const AUTHORITATIVE_REPOSITORY = "${AUTHORITATIVE_REPOSITORY}";`),
    "the authoritative repository is no longer a constant of the store module");
  assert.equal(/export[^\n]*AUTHORITATIVE_REPOSITORY/.test(source), false,
    "the authoritative repository is exported, so a consumer could compare against it");
  assert.equal(source.includes(`configured("GITHUB_REPOSITORY"`), false,
    "the checks URL is built from the environment again");
  assert.ok(!Object.hasOwn(stores, "AUTHORITATIVE_REPOSITORY"));
});

// ---------------------------------------------------------------------------
// PART D — the producer seam, not built, and said in exactly one place.
// ---------------------------------------------------------------------------

test("PRODUCER: the producer seam is built, and still has no ruling line and no reader", async () => {
  const live = await emitGateZeroOutcome();
  assert.equal(live.passable, false);
  // BOUND AS OF THE PRODUCER SLICE — by card 9's seat declaration, which is a
  // committed line and not a ruling line. The distinction is the subject of the
  // rest of this test: the seam opened, and it did NOT open through this
  // module's table.
  assert.equal(live.producer_bound, true);
  assert.deepEqual(live.owed_seams, []);

  // No ruling line: the lookup has no entry for it, so no paste could open it.
  assert.equal(rulings.seamRulingRef(V5_A02_GATE_ZERO_PRODUCER_SEAM), null);
  const rulingSource = readFileSync(join(SRC, RULINGS_FILE), "utf8");
  assert.equal(/^\s*"seam:/m.test(rulingSource), false,
    "the ruling table keys on seam names again, so the producer seam could take a line");

  // No restatement: the earlier draft exported a written-out copy of this
  // refusal, which had to be kept in sync. The gate says it once.
  const readerSource = readFileSync(join(SRC, READERS_FILE), "utf8");
  for (const phrase of [live.unavailable_because, live.not_passable_because])
    assert.ok(!readerSource.includes(phrase), "the producer refusal is copied into the reader module");
  for (const name of Object.keys(readers))
    assert.ok(!/producer/i.test(name), `${name} names the producer seam on the reader surface`);
});

test("PRODUCER: three bound readers and a built seam still do not make the gate pass", async () => {
  // The whole point of building three readers, and then the producer behind
  // them: neither is a signature. Every seam has something behind it and the
  // gate still refuses, because the rows a run would stand on are not named.
  const emitted = await emitGateZeroOutcome();
  assert.equal(emitted.passable, false);
  assert.equal(emitted.join, null);
  assert.equal(emitted.producer_bound, true);
  assert.deepEqual([...emitted.owed_seams], []);
  // And the producer seam is still the one thing no ruling line can open: the
  // ruling table has no entry for it, so the lookup answers null for its name.
  assert.equal(rulings.seamRulingRef(V5_A02_GATE_ZERO_PRODUCER_SEAM), null);
  assert.deepEqual([...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS].length > 0, true);
});

// ---------------------------------------------------------------------------
// The import graph, parsed rather than grepped.
// ---------------------------------------------------------------------------

/**
 * V8's own ESM parser, via vm.SourceTextModule in a child process.
 *
 * IT WALKS SUBDIRECTORIES, and that is the third correction's doing rather than
 * tidiness: the shared ruling predicate moved to src/internal/, and a scan that
 * stopped at the top level would have reported an import graph with the predicate
 * missing from it — every clause below would have passed over a file the graph
 * could not see. Keys are paths relative to src, so `internal/...` is a key like
 * any other; specifiers are left EXACTLY as the module wrote them, and the
 * resolution to a key happens in `resolveFrom` where it can be read.
 */
function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync } = require("node:fs");
    const { join, relative, sep } = require("node:path");
    const vm = require("node:vm");
    const root = process.argv[1];
    const out = {};
    const walk = (dir) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })
        .sort((a, b) => a.name.localeCompare(b.name))) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) { walk(full); continue; }
        if (!entry.name.endsWith(".js")) continue;
        const key = relative(root, full).split(sep).join("/");
        const source = readFileSync(full, "utf8");
        out[key] = new vm.SourceTextModule(source, { identifier: key }).dependencySpecifiers;
      }
    };
    walk(root);
    process.stdout.write(JSON.stringify(out));
  `;
  const run = spawnSync(process.execPath, ["--experimental-vm-modules", "-e", script, directory],
    { encoding: "utf8" });
  assert.equal(run.status, 0, `the module parser failed: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

/**
 * Where a specifier written IN `from` lands, as a key of the import graph.
 *
 * Relative specifiers only: a bare one ("pg") is not a module in src and is
 * returned unchanged, so a clause that asks "who imports this file" never
 * matches one. This is the whole resolution step, written once, so that a module
 * in a subdirectory naming `../gate-zero-seam-rulings.v5.js` and one at the top
 * naming `./gate-zero-seam-rulings.v5.js` are the same answer to the same
 * question — the alternative is a clause that silently stops counting importers
 * the moment one of them moves a directory.
 */
function resolveFrom(from, specifier) {
  if (!specifier.startsWith(".")) return specifier;
  const parts = from.split("/").slice(0, -1);
  for (const step of specifier.split("/")) {
    if (step === ".") continue;
    if (step === "..") { parts.pop(); continue; }
    parts.push(step);
  }
  return parts.join("/");
}

// ---------------------------------------------------------------------------
// THE EXPORT SURFACE, LINKED RATHER THAN GREPPED — the fourth correction's
// standards finding.
//
// The guard that stood here was `/export\s+\{[^}]*\bruledCardBinding\b/` over
// each file's text, and the standing rule of 2026-09-11 says in as many words
// that a static module guard uses a real parser. The regex had the two holes a
// parser does not: `export * from "./internal/gate-zero-seam-binding.v5.js"`
// forwards the name without writing it anywhere, and
// `export { ruledCardBinding as somethingElse }` writes a different name on the
// surface. The self-test below runs both forms past the old pattern and the new
// guard so the difference is measured rather than asserted.
//
// SO THE QUESTION IS ASKED THREE TIMES, in the three ways it can be wrong.
//
//   BY NAME, over EVERY module in src, out of esbuild's linker: `export *` is
//   resolved to the concrete names it forwards, so a chain of re-exports through
//   three files is one answer.
//   BY FORM, over every export statement of every module, read off esbuild's own
//   printer — because a name check answers `seam` for
//   `export * as seam from "<the binding>"` and calls that clean. The fifth
//   correction adds this one; it is built directly below.
//   BY REACHABILITY AT RUNTIME, over the namespaces the modules actually export,
//   walked recursively — because neither of the above can see a predicate held
//   inside an exported object.
// ---------------------------------------------------------------------------

/** esbuild refuses to run at all if it is missing, rather than degrading quietly. */
assert.equal(typeof esbuild.buildSync, "function",
  "the re-export guard needs a real parser; esbuild is not loadable");

/** The internal predicate's own name, the one thing src may not put on a surface. */
const PREDICATE_NAME = "ruledCardBinding";

/** Every `.js` module under a directory, as the tree-relative keys used below. */
function modulesUnder(directory) {
  const found = [];
  const walk = at => {
    for (const entry of readdirSync(at, { withFileTypes: true })
      .sort((a, b) => a.name.localeCompare(b.name))) {
      const full = join(at, entry.name);
      if (entry.isDirectory()) { walk(full); continue; }
      if (entry.name.endsWith(".js")) found.push(relative(directory, full).split(sep).join("/"));
    }
  };
  walk(directory);
  return found;
}

/**
 * WHAT EVERY MODULE UNDER `directory` EXPORTS, keyed by its path relative to
 * that directory. Read out of esbuild's metafile after a real bundle, so the
 * names are the ones a consumer could import, `export *` included.
 *
 * The `.ttf` loader is not decoration: one module in src imports a font as a
 * binary asset, and without a loader for it esbuild refuses the whole batch and
 * this guard would never run.
 */
function exportedNames(directory) {
  const entryPoints = modulesUnder(directory);
  const built = esbuild.buildSync({
    absWorkingDir: directory, entryPoints, bundle: true, write: false, format: "esm",
    platform: "node", packages: "external", metafile: true, treeShaking: false,
    outdir: "__exported_names__", outbase: ".", loader: { ".ttf": "binary" },
    logLevel: "silent", logLimit: 0,
  });
  const names = {};
  for (const output of Object.values(built.metafile.outputs)) {
    if (output.entryPoint === undefined) continue;
    names[output.entryPoint] = [...output.exports].sort();
  }
  assert.equal(Object.keys(names).length, entryPoints.length,
    "the linker did not report an export list for every module in the tree");
  return names;
}

/** The pattern this guard replaced, kept only so the self-test can measure it. */
const DELETED_REEXPORT_REGEX = /export\s+\{[^}]*\bruledCardBinding\b/;

// ---------------------------------------------------------------------------
// AND THE NAME IS NOT THE WHOLE QUESTION — the fifth correction's second
// finding.
//
// The check above asks the linker for export NAMES and the check in ISOLATION
// asked the two importers for TOP-LEVEL value identity. Both are blind to the
// same shape, and the review demonstrated it:
//
//     export * as seam from "./internal/gate-zero-seam-binding.v5.js";
//
// exports the single name `seam`, which is not the predicate's name; the value
// under it is a module namespace, which is not the predicate; and
// `seam.ruledCardBinding` is public anyway. `export default { ruledCardBinding }`
// and `export const api = { seam: ruledCardBinding }` hide it the same way, one
// property deeper.
//
// SO THE QUESTION BECOMES REACHABILITY, ASKED TWICE OVER.
//
//   STATICALLY, OVER EVERY EXPORT FORM OF EVERY MODULE IN SRC. Each module is
//   run through esbuild's parser and PRINTED BACK in a normal form, in which
//   every re-export has become an import plus one export clause, comments are
//   gone, and a string that reads like an export statement is printed as the
//   string it is. The forms are read off that normal form: named, default,
//   `export * from`, `export { x as y } from`, and `export * as ns from` — the
//   last being the one that costs a name check its answer. Namespace and star
//   edges are followed TRANSITIVELY, so a chain through three files is one
//   answer.
//
//   AT RUNTIME, BY WALKING WHAT THE MODULE ACTUALLY EXPORTS. Every own property
//   of every public namespace of this slice, recursively, compared against the
//   predicate itself — which is the only check that can see a value re-exposed
//   inside a container object, under any name, at any depth.
//
// THE PARSE IS PROVED COMPLETE RATHER THAN ASSUMED. The names these forms yield
// are reconciled, module by module across all of src, against the names the
// LINKER reports for the same module. A form this reader cannot see costs it a
// name; a phantom read out of a string gains it one; either way the two lists
// stop matching and the test is red before it asks anything about the predicate.
// ---------------------------------------------------------------------------

const NAMESPACE_IMPORT = /^import\s*\*\s*as\s+([A-Za-z_$][\w$]*)\s+from\s*"((?:[^"\\]|\\.)*)";$/gm;
const NAMED_IMPORT = /^import\s*\{([^}]*)\}\s*from\s*"((?:[^"\\]|\\.)*)";$/gm;
const STAR_REEXPORT = /^export\s*\*\s*from\s*"((?:[^"\\]|\\.)*)";$/gm;
const EXPORT_CLAUSE = /^export\s*\{([^}]*)\}\s*;$/gm;
const EXPORT_DECL =
  /^export\s+(?:async\s+)?(?:const|let|var|function\s*\*?|class)\s+([A-Za-z_$][\w$]*)/gm;
const EXPORT_DEFAULT = /^export\s+default\b/gm;

/** `a, b as c` → the pairs, an unaliased entry standing for itself. */
function clauseEntries(text) {
  return text.split(",").map(one => one.trim()).filter(Boolean).map(entry => {
    const [local, exported] = entry.split(/\s+as\s+/).map(one => one.trim());
    return { local, exported: exported ?? local };
  });
}

function matchAll(pattern, code) {
  pattern.lastIndex = 0;
  return [...code.matchAll(pattern)];
}

/**
 * ONE MODULE'S EXPORT FORMS AND THE BINDINGS THEY STAND ON, read off esbuild's
 * printer: every exported name with the local it came from, every
 * `export * from`, and for each local the import that bound it — a single name,
 * or a whole namespace.
 */
function exportForms(directory, moduleName) {
  const code = esbuild.transformSync(readFileSync(join(directory, moduleName), "utf8"),
    { loader: "js", format: "esm", logLevel: "silent" }).code;
  const locals = new Map();
  for (const found of matchAll(NAMESPACE_IMPORT, code))
    locals.set(found[1], { kind: "namespace", from: found[2] });
  for (const found of matchAll(NAMED_IMPORT, code))
    for (const { local, exported } of clauseEntries(found[1]))
      locals.set(exported, { kind: "named", name: local, from: found[2] });

  const forms = [];
  for (const found of matchAll(STAR_REEXPORT, code)) forms.push({ kind: "star", from: found[1] });
  for (const found of matchAll(EXPORT_CLAUSE, code))
    for (const { local, exported } of clauseEntries(found[1]))
      forms.push({ kind: "name", exported, local });
  for (const found of matchAll(EXPORT_DECL, code))
    forms.push({ kind: "name", exported: found[1], local: found[1] });
  for (const _ of matchAll(EXPORT_DEFAULT, code))
    forms.push({ kind: "name", exported: "default", local: null });
  return { forms, locals };
}

/**
 * THE WHOLE TREE'S EXPORT FORMS, with the three questions this guard asks of
 * them.
 *
 * `namesOf(m)` is the names m's forms put on its surface, `export * from`
 * expanded through the file it names — the list reconciled against the linker.
 * `namespacesExposedBy(m)` is every module whose NAMESPACE OBJECT a consumer of
 * m can hold, both edges followed transitively. `namedReexportsOf(m)` is every
 * binding m forwards by name, under whatever name it forwards it as.
 */
function exportGraph(directory) {
  const modules = modulesUnder(directory);
  const analyzed = new Map(modules.map(name => [name, exportForms(directory, name)]));

  const namesOf = (name, seen = new Set()) => {
    if (seen.has(name)) return [];
    seen.add(name);
    const module = analyzed.get(name);
    if (module === undefined) return null;
    const names = [];
    for (const form of module.forms) {
      if (form.kind === "name") { names.push(form.exported); continue; }
      const forwarded = namesOf(resolveFrom(name, form.from), seen);
      // A star re-export of a package outside this tree forwards names the
      // linker knows and this reader does not; it is marked rather than guessed
      // at, so the reconciliation below would fail rather than pass vacuously.
      if (forwarded === null) { names.push("<external star re-export>"); continue; }
      names.push(...forwarded.filter(one => one !== "default"));
    }
    return names;
  };

  const namespacesExposedBy = (name, seen = new Set()) => {
    const exposed = new Set();
    if (seen.has(name)) return exposed;
    seen.add(name);
    const module = analyzed.get(name);
    if (module === undefined) return exposed;
    for (const form of module.forms) {
      // `export * from t` does not put t's namespace on the surface, but it does
      // forward every namespace t itself exposes.
      if (form.kind === "star") {
        for (const onward of namespacesExposedBy(resolveFrom(name, form.from), seen))
          exposed.add(onward);
        continue;
      }
      const bound = form.local === null ? undefined : module.locals.get(form.local);
      if (bound === undefined || bound.kind !== "namespace") continue;
      const target = resolveFrom(name, bound.from);
      exposed.add(target);
      for (const onward of namespacesExposedBy(target, seen)) exposed.add(onward);
    }
    return exposed;
  };

  const namedReexportsOf = name => {
    const module = analyzed.get(name);
    const forwarded = [];
    for (const form of module.forms) {
      const bound = form.local === null ? undefined : module.locals.get(form.local);
      if (bound === undefined || bound.kind !== "named") continue;
      forwarded.push({ exportedAs: form.exported, imported: bound.name,
        from: resolveFrom(name, bound.from) });
    }
    return forwarded;
  };

  return { modules, namesOf, namespacesExposedBy, namedReexportsOf };
}

// THE RUNTIME REACHABILITY WALK LIVES IN ITS OWN FILE NOW
// (./gate-zero-reachability-walk.testhelper.mjs, 2026-09-12). The producer suite
// needs the same walk applied to its own callable, and the one thing that must
// not happen is a second copy written from memory: `parts` still lets the
// mutation controls below revert ONE part of the walk at a time against THAT
// code, which is the same code the producer suite runs.

/**
 * ONE MODULE PER RE-EXPOSING FORM, plus the private one that must stay legal.
 *
 * `caught` names the checks that MUST see each case and is asserted exactly:
 * a case the name check alone would catch is not evidence for the two checks
 * this correction adds, so each case records which of the three answers it.
 */
const REEXPORT_CASES = Object.freeze([
  // A re-export under the same name is both: the linker reports the name, and
  // the form is a named forward of the binding.
  { file: "plain.js", caught: ["name", "alias", "runtime"],
    source: `export { ${PREDICATE_NAME} } from "./binding.js";\n` },
  { file: "star.js", caught: ["name", "runtime"],
    source: `export * from "./binding.js";\n` },
  // THE REVIEW'S OWN PROBE: one name on the surface, and it is not the
  // predicate's; one value, and it is not the predicate.
  { file: "namespace.js", caught: ["namespace", "runtime"],
    source: `export * as seam from "./binding.js";\n` },
  { file: "chain.js", caught: ["namespace", "runtime"],
    source: `export * as onward from "./namespace.js";\n` },
  { file: "star-chain.js", caught: ["namespace", "runtime"],
    source: `export * from "./namespace.js";\n` },
  { file: "alias.js", caught: ["alias", "runtime"],
    source: `import { ${PREDICATE_NAME} as ruledSeam } from "./binding.js";\nexport { ruledSeam };\n` },
  { file: "from-alias.js", caught: ["alias", "runtime"],
    source: `export { ${PREDICATE_NAME} as ruledSeam } from "./binding.js";\n` },
  { file: "defaulted.js", caught: ["runtime"],
    source: `import { ${PREDICATE_NAME} } from "./binding.js";\nexport default { ${PREDICATE_NAME} };\n` },
  { file: "container.js", caught: ["runtime"],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\nexport const api = { seam: ${PREDICATE_NAME} };\n` },
  // THE SIXTH REVIEW'S THREE PROBES, one per part of the walk it defeated: a
  // chain longer than the old six-edge budget, a symbol key, and a property held
  // on a prototype rather than on the object. Each is `runtime` only — no name,
  // no namespace and no alias is on the surface — so each measures the walk and
  // nothing else.
  { file: "deep.js", caught: ["runtime"],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\n`
      + `export const api = { a: { b: { c: { d: { e: { f: { seam: ${PREDICATE_NAME} } } } } } } };\n` },
  { file: "symbol.js", caught: ["runtime"],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\n`
      + `export const api = { [Symbol.for("gate-zero.seam")]: ${PREDICATE_NAME} };\n` },
  { file: "inherited.js", caught: ["runtime"],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\n`
      + `export const api = Object.create({ seam: ${PREDICATE_NAME} });\n` },
  // THE SEVENTH REVIEW'S PROBE: a public getter, whose value the sixth
  // correction declined to read. `api.seam === ruledCardBinding` for any
  // consumer, so the walk must reach it.
  { file: "accessor.js", caught: ["runtime"],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\n`
      + `export const api = { get seam() { return ${PREDICATE_NAME}; } };\n` },
  // THE EIGHTH REVIEW'S TWO PROBES, which are the last two members of the
  // closed reachability set. Each is `runtime` only, and each is a route the
  // seventh correction's accessor branch stepped over: the getter is inherited
  // and answers only the exported child, and the descriptor's own `get`/`set`
  // ARE the predicate while calling the getter hands back nothing.
  { file: "receiver-getter.js", caught: ["runtime"],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\n`
      + `const shape = { get seam() { return this === shape ? null : ${PREDICATE_NAME}; } };\n`
      + `export const api = Object.create(shape);\n` },
  { file: "descriptor-accessor.js", caught: ["runtime"],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\n`
      + `export const api = {};\n`
      + `Object.defineProperty(api, "seam", { get: ${PREDICATE_NAME}, set: ${PREDICATE_NAME},\n`
      + `  enumerable: true, configurable: true });\n` },
  // AND THE TWO THE ACCESSOR BRANCH MUST SURVIVE RATHER THAN CATCH: a getter
  // that throws is an opaque leaf, and one that hands back something else is
  // walked and found to hold nothing.
  { file: "throwing-accessor.js", caught: [],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\n`
      + `export const asked = card => ${PREDICATE_NAME}(card) !== null;\n`
      + `export const api = { get seam() { throw new Error("no seam here"); } };\n` },
  // AND THE ONE THAT MUST PASS: imported, used, never handed on.
  { file: "private.js", caught: [],
    source:
      `import { ${PREDICATE_NAME} } from "./binding.js";\n`
      + `export const asked = card => ${PREDICATE_NAME}(card) !== null;\n` },
]);

/** A directory holding the cases above, plus the binding they all reach for. */
function stageReexportCases() {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const directory = mkdtempSync(join(cache, "gate-zero-reexport-"));
  staged.push(directory);
  writeFileSync(join(directory, "binding.js"),
    `export const ${PREDICATE_NAME} = () => null;\n`
    // A comment and a string that read like the forms above, so the reader is
    // measured against the two things a text scan cannot tell apart from code.
    + `// export * as fromAComment from "./binding.js";\n`
    + `export const decoy = 'export * as fromAString from "./binding.js";';\n`);
  for (const { file, source } of REEXPORT_CASES) writeFileSync(join(directory, file), source);
  return directory;
}

test("PARSER: every export form is read, and the three checks catch what each is for", async () => {
  const directory = stageReexportCases();
  const graph = exportGraph(directory);
  const linked = exportedNames(directory);
  const predicate =
    (await import(pathToFileURL(join(directory, "binding.js")).href))[PREDICATE_NAME];

  // THE COMPLETENESS PROOF FIRST: what the forms say a module exports is what
  // the linker says it exports, for every module in the fixture.
  for (const name of graph.modules)
    assert.deepEqual([...new Set(graph.namesOf(name))].sort(), linked[name],
      `the export forms read off ${name} are not the names the linker resolves`);
  // AND THE COMMENT AND THE STRING IN binding.js PRODUCED NO FORM: the reader
  // sees code, not text that looks like code.
  assert.deepEqual(linked["binding.js"], ["decoy", PREDICATE_NAME]);
  assert.deepEqual([...graph.namespacesExposedBy("binding.js")], []);

  for (const { file, caught } of REEXPORT_CASES) {
    const byName = linked[file].includes(PREDICATE_NAME);
    const byNamespace = graph.namespacesExposedBy(file).has("binding.js");
    const byAlias = graph.namedReexportsOf(file)
      .some(one => one.from === "binding.js" && one.imported === PREDICATE_NAME);
    const namespace = await import(pathToFileURL(join(directory, file)).href);
    const atRuntime = pathToValue(namespace, predicate) !== null;
    assert.deepEqual(
      ["name", "namespace", "alias", "runtime"]
        .filter((_, index) => [byName, byNamespace, byAlias, atRuntime][index]),
      caught,
      `${file} is caught by the wrong checks`);
  }

  // THE MEASUREMENT, AND IT IS WHAT MAKES THIS CORRECTION A DIFFERENCE RATHER
  // THAN A RESTATEMENT: for the three forms the review named, BOTH checks the
  // fourth correction shipped come back clean.
  for (const file of ["namespace.js", "defaulted.js", "container.js"]) {
    const namespace = await import(pathToFileURL(join(directory, file)).href);
    assert.equal(linked[file].includes(PREDICATE_NAME), false,
      `${file}: the name check would have caught this, so it proves nothing new`);
    assert.equal(topLevelIdentityOnly(namespace, predicate), null,
      `${file}: the top-level identity check would have caught this`);
    assert.equal(DELETED_REEXPORT_REGEX.test(REEXPORT_CASES.find(one => one.file === file).source),
      false, `${file}: the deleted pattern would have caught this`);
    assert.ok(pathToValue(namespace, predicate) !== null,
      `${file}: the check this correction adds does NOT catch it`);
  }
  // And the route is named, not merely found.
  assert.equal(pathToValue(await import(pathToFileURL(join(directory, "namespace.js")).href),
    predicate), ".seam.ruledCardBinding");
  assert.equal(pathToValue(await import(pathToFileURL(join(directory, "container.js")).href),
    predicate), ".api.seam");
  assert.equal(pathToValue(await import(pathToFileURL(join(directory, "chain.js")).href),
    predicate), ".onward.seam.ruledCardBinding");

  // THE MUTATION CONTROLS THE SIXTH AND EIGHTH CORRECTIONS OWE, as a matrix
  // rather than a list. Each shape the reviews probed is found by the whole walk
  // at a named route; then ONE part of the walk is reverted and the matrix is
  // asserted BOTH ways — every probe that NEEDS that part goes null, and every
  // probe that does not stays found, so no control is measuring a walk broken in
  // general.
  //
  // `needs` is the honest dependency list, and it is why this replaced the
  // one-probe-per-part list: Sol's receiver probe rides THREE parts at once (it
  // sits behind a prototype, behind a getter, and answers only the exported
  // child), so a control that demanded it survive every other reversion would be
  // asserting something false. What proves the receiver part is load-bearing is
  // the row where only that part is reverted.
  const PROBES = Object.freeze([
    { probe: "deep.js", route: ".api.a.b.c.d.e.f.seam", needs: ["unbounded depth"] },
    { probe: "symbol.js", route: ".api[Symbol(gate-zero.seam)]", needs: ["symbol keys"] },
    { probe: "inherited.js", route: ".api.[[Prototype]].seam", needs: ["the prototype chain"] },
    { probe: "accessor.js", route: ".api.seam", needs: ["accessor reads"] },
    { probe: "receiver-getter.js", route: ".api.[[Prototype]].seam",
      needs: ["the prototype chain", "accessor reads", "the exported child as receiver"] },
    { probe: "descriptor-accessor.js", route: ".api.seam<get>",
      needs: ["the get and set objects themselves"] },
  ]);
  const REVERTED = Object.freeze([
    { part: "unbounded depth", walk: { ...WHOLE_WALK, bounded: 6 } },
    { part: "symbol keys", walk: { ...WHOLE_WALK, symbols: false } },
    { part: "the prototype chain", walk: { ...WHOLE_WALK, prototypes: false } },
    { part: "accessor reads", walk: { ...WHOLE_WALK, accessors: false } },
    { part: "the get and set objects themselves",
      walk: { ...WHOLE_WALK, accessorFunctions: false } },
    { part: "the exported child as receiver", walk: { ...WHOLE_WALK, inheritedReceiver: false } },
  ]);
  const probes = new Map();
  for (const { probe } of PROBES)
    probes.set(probe, await import(pathToFileURL(join(directory, probe)).href));
  for (const { probe, route } of PROBES)
    assert.equal(pathToValue(probes.get(probe), predicate), route,
      `${probe}: the walk does not reach the predicate, or names the wrong route`);

  // AND THE TWO EIGHTH-REVIEW PROBES ARE ASSERTED AS A CONSUMER SEES THEM, so
  // what the walk is being measured against is Sol's shape and not something
  // easier: the inherited getter answers the exported child (and answers the
  // prototype nothing), and the descriptor's own get and set ARE the predicate
  // while reading the property hands back null.
  const receiverApi = probes.get("receiver-getter.js").api;
  assert.equal(receiverApi.seam, predicate,
    "the receiver probe does not hand a consumer the predicate, so it probes nothing");
  assert.equal(Object.getPrototypeOf(receiverApi).seam, null,
    "the receiver probe answers the prototype too, so it is not receiver-sensitive");
  const descriptorApi = probes.get("descriptor-accessor.js").api;
  const exposed = Object.getOwnPropertyDescriptor(descriptorApi, "seam");
  assert.equal(exposed.get, predicate, "the descriptor probe's get is not the predicate");
  assert.equal(exposed.set, predicate, "the descriptor probe's set is not the predicate");
  assert.equal(descriptorApi.seam, null,
    "the descriptor probe returns the predicate from the getter, so it is the seventh case again");

  for (const { part, walk } of REVERTED) {
    assert.ok(PROBES.some(one => one.needs.includes(part)),
      `${part} is reverted by a control no probe depends on`);
    for (const { probe, needs } of PROBES) {
      const found = pathToValue(probes.get(probe), predicate, walk);
      if (needs.includes(part))
        assert.equal(found, null,
          `${probe} is still found with ${part} reverted, so that part is not what catches it`);
      else
        assert.notEqual(found, null,
          `${probe} is lost with ${part} reverted, so that control is not isolated to its probes`);
    }
  }
});


test("PARSER: the re-export guard reads the forms the deleted regex missed", () => {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const directory = mkdtempSync(join(cache, "gate-zero-reexport-"));
  staged.push(directory);
  const cases = {
    "binding.js": `export const ${PREDICATE_NAME} = () => null;\n`,
    // The one the regex did catch.
    "plain.js": `import { ${PREDICATE_NAME} } from "./binding.js";\nexport { ${PREDICATE_NAME} };\n`,
    // The two it did not: a star re-export never spells the name at all, and a
    // LOCAL alias spells it only on the import side, which the pattern did not
    // read.
    "star.js": `export * from "./binding.js";\n`,
    "alias.js": `import { ${PREDICATE_NAME} as ruledSeam } from "./binding.js";\nexport { ruledSeam };\n`,
    // And one that imports it and keeps it private, which must stay legal.
    "private.js":
      `import { ${PREDICATE_NAME} } from "./binding.js";\nexport const asked = card => ${PREDICATE_NAME}(card) !== null;\n`,
  };
  for (const [name, source] of Object.entries(cases)) writeFileSync(join(directory, name), source);

  const names = exportedNames(directory);
  // BY NAME: the star re-export is reported, which is the hole that mattered
  // most — a file can forward the predicate without ever spelling it.
  assert.ok(names["star.js"].includes(PREDICATE_NAME), "the linker did not resolve export *");
  assert.ok(names["plain.js"].includes(PREDICATE_NAME));
  assert.deepEqual(names["alias.js"], ["ruledSeam"]);
  assert.deepEqual(names["private.js"], ["asked"]);
  // AND THE MEASUREMENT: the deleted pattern saw neither of the two forms above,
  // so this is a difference rather than a restatement.
  assert.equal(DELETED_REEXPORT_REGEX.test(cases["star.js"]), false);
  assert.equal(DELETED_REEXPORT_REGEX.test(cases["alias.js"]), false);
  assert.equal(DELETED_REEXPORT_REGEX.test(cases["plain.js"]), true);
});

test("SURFACE: the shared predicate is unreachable from every public namespace in src, by any path",
  async () => {
    const graph = exportGraph(SRC);
    const linked = exportedNames(SRC);
    assert.ok(graph.modules.length > 100, "every module in src must have been parsed");
    assert.ok(graph.modules.includes(BINDING_FILE),
      "the module walk does not include the predicate, so nothing below is proved of it");

    // THE COMPLETENESS PROOF, OVER THE WHOLE TREE. Every module's forms yield
    // exactly the names the linker resolves for it. A form this reader cannot
    // see, or one it imagines out of a string, is red here — before a single
    // question is asked about the predicate.
    for (const name of graph.modules)
      assert.deepEqual([...new Set(graph.namesOf(name))].sort(), linked[name],
        `the export forms read off ${name} are not the names the linker resolves`);

    // BY NAME, BY NAMESPACE, AND BY ALIAS, for every module in src but the one
    // the predicate lives in.
    assert.deepEqual(
      Object.keys(linked).filter(name => linked[name].includes(PREDICATE_NAME)), [BINDING_FILE],
      `a module other than ${BINDING_FILE} exports ${PREDICATE_NAME}, directly or through an export *`);
    for (const name of graph.modules) {
      if (name === BINDING_FILE) continue;
      assert.equal(graph.namespacesExposedBy(name).has(BINDING_FILE), false,
        `${name} puts the predicate's own module namespace on a public surface`);
      assert.deepEqual(graph.namedReexportsOf(name).filter(one => one.from === BINDING_FILE), [],
        `${name} forwards a binding of ${BINDING_FILE} under another name`);
    }

    // AND AT RUNTIME, over every public namespace of this slice, recursively.
    const gate = await import(pathToFileURL(join(SRC, GATE_FILE)).href);
    for (const [label, namespace] of
      [["gate", gate], ["readers", readers], ["rulings", rulings], ["stores", stores]]) {
      const route = pathToValue(namespace, binding.ruledCardBinding);
      assert.equal(route, null, `${label} exposes the shared predicate at ${label}${route}`);
    }
    // NON-VACUOUS: the same walk finds it in the ONE namespace it is meant to be
    // in, so a walk that could never find anything is not what passed above.
    assert.equal(pathToValue(binding, binding.ruledCardBinding), `.${PREDICATE_NAME}`);

    // THE MUTATION CONTROL IS THE REVIEW'S OWN PROBE, RUN ON A COPY OF SRC. One
    // line is added to the reader — the line a name check and a top-level
    // identity check both let through — and the two checks this correction adds
    // are asserted to catch it while the two it replaces are asserted not to.
    // OUTSIDE the package rather than under its cache, because what is copied is
    // the package: src reaches a font and a manifest that live beside it, and a
    // tree without them cannot be linked at all. This suite and node_modules are
    // left behind.
    const base = mkdtempSync(join(tmpdir(), "gate-zero-reexport-src-"));
    staged.push(base);
    const control = join(base, "src");
    cpSync(fileURLToPath(new URL("..", import.meta.url)), base,
      { recursive: true, filter: source => !/\/(?:node_modules|test)$/.test(source) });
    const readersPath = join(control, READERS_FILE);
    writeFileSync(readersPath,
      `${readFileSync(readersPath, "utf8")}\nexport * as seam from "./${BINDING_FILE}";\n`);

    const controlGraph = exportGraph(control);
    const controlLinked = exportedNames(control);
    const controlReaders = await import(pathToFileURL(readersPath).href);
    const controlBinding = await import(pathToFileURL(join(control, BINDING_FILE)).href);

    assert.equal(controlGraph.namespacesExposedBy(READERS_FILE).has(BINDING_FILE), true,
      "the namespace check does not catch the probe the review demonstrated");
    assert.equal(pathToValue(controlReaders, controlBinding[PREDICATE_NAME]),
      `.seam.${PREDICATE_NAME}`,
      "the runtime walk does not reach the predicate through the re-exported namespace");
    assert.equal(controlLinked[READERS_FILE].includes(PREDICATE_NAME), false,
      "the name check catches the probe, so the namespace check proves nothing new");
    assert.equal(topLevelIdentityOnly(controlReaders, controlBinding[PREDICATE_NAME]), null,
      "the top-level identity check catches the probe, so the runtime walk proves nothing new");
    // AND THE CONTROL TREE IS OTHERWISE SRC: nothing else about it moved, so the
    // difference measured above is the one line.
    assert.deepEqual(controlGraph.modules, graph.modules);
    for (const name of controlGraph.modules) {
      if (name === READERS_FILE) continue;
      assert.deepEqual(controlLinked[name], linked[name], `${name} changed in the control tree`);
    }
  });

test("ISOLATION: the store module is reached from one place, and nothing in src reaches a fixture", async () => {
  const imports = moduleImports(SRC);
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");
  assert.ok(Object.hasOwn(imports, READERS_FILE));
  // The subdirectory walk is load-bearing for every clause below that names it.
  assert.ok(Object.hasOwn(imports, BINDING_FILE),
    "the import graph does not include the internal predicate, so nothing below is proved of it");
  // Non-vacuous: resolution really does fold a parent-relative specifier.
  assert.equal(resolveFrom(BINDING_FILE, "../gate-zero-seam-rulings.v5.js"), RULINGS_FILE);
  assert.equal(resolveFrom(READERS_FILE, `./${BINDING_FILE}`), BINDING_FILE);

  const offenders = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one =>
      one.includes("/test/") || one.startsWith("../test")
      || one.includes(".fixture.") || one.includes(".testhelper.")))
    .map(([name]) => name);
  assert.deepEqual(offenders, [],
    "a production module reached into the test directory, a fixture or a test helper");

  // The reader is the only module that opens the STORES, so a second consumer
  // of a store is red on sight rather than red after an incident.
  const importersOf = module => Object.entries(imports)
    .filter(([name, specifiers]) => specifiers.some(one => resolveFrom(name, one) === module))
    .map(([name]) => name).sort();
  // TWO IMPORTERS, NAMED, AND THE SECOND IS A DELIBERATE ADDITION (standing-rule
  // amendment 9, 2026-09-14). The readers open the three CARD stores, and that
  // is still the whole of the card evidence path. The producer opens ONE store
  // and one only — the candidate-build record it takes its subject maker from —
  // and it is not card evidence: no clause reads it, no ruling names it, and it
  // decides no verdict. It is the run binding, which this slice has always
  // derived rather than ruled.
  //
  // WHY IT IS NOT A FOURTH CARD, stated plainly because it is the judgment call
  // in this diff. A card is a RULING, and a ruling in this system is a decision
  // id Joe pasted onto a line in gate-zero-seam-rulings.v5.js. A session cannot
  // write one — inventing a decision id would be inventing a human ruling, which
  // is the exact class of thing this slice refuses everywhere else — and a card
  // added with `decision_id: null` would leave the producer permanently dark. So
  // the subject maker is read through the same read-only store surface, with the
  // store's own `store_ref` statement checked by the consumer, and the closed
  // set below is what keeps a THIRD importer from appearing quietly.
  assert.deepEqual(importersOf(STORES_FILE), [PRODUCER_FILE, READERS_FILE].sort(),
    "the stores module has an importer that is neither the reader nor the producer");

  // THE RULING TABLE HAS EXACTLY ONE IMPORTER, and that is the PR 1004
  // re-review's finding turned into a structural invariant. The gate used to
  // import the table too and formed its own opinion about what a ruling means —
  // any non-null ruling was a bound seam — which is half of the test the reader
  // applies, so a ruling naming another registered store made the gate say bound
  // while the reader refused. The table is read in ONE place, by the ONE
  // predicate that also holds the store half. A SECOND importer of this table is
  // the drift itself.
  assert.deepEqual(importersOf(RULINGS_FILE), [BINDING_FILE],
    "the ruling table has an importer other than the shared predicate, which is how the two drifted");
  assert.equal(imports[GATE_FILE].includes(`./${RULINGS_FILE}`), false,
    "the gate reads the ruling table directly again instead of the shared predicate");
  assert.equal(imports[READERS_FILE].includes(`./${RULINGS_FILE}`), false,
    "the reader reads the ruling table directly again, beside the predicate that answers for it");

  // AND THE PREDICATE IS SHARED THROUGH AN INTERNAL PATH, WHICH IS THE THIRD
  // CORRECTION'S FINDING. Exactly two modules import it — the readers and the
  // gate, the two that must not disagree — it imports nothing but the ruling
  // table, and no public namespace of this slice carries its name (asserted on
  // the surface test above). A third importer, or a re-export, is how a
  // deliberately internal surface becomes a public one by accident.
  assert.deepEqual(importersOf(BINDING_FILE), [GATE_FILE, READERS_FILE].sort(),
    "the shared predicate has an importer other than the two modules that ask it");
  assert.deepEqual(imports[BINDING_FILE].sort(),
    ["../closed-callable.js", `../${RULINGS_FILE}`].sort(),
    "the shared predicate imports something other than the ruling table it narrows and the one closed shape");
  // NO MODULE PUTS IT BACK ON A PUBLIC SURFACE, asked of the linker and then of
  // the values themselves. BY NAME first, over every module in src: the binding
  // file is the one place the name may be exported from.
  const exported = exportedNames(SRC);
  assert.deepEqual(Object.keys(exported).filter(name => exported[name].includes(PREDICATE_NAME)),
    [BINDING_FILE],
    `a module other than ${BINDING_FILE} exports ${PREDICATE_NAME}, directly or through an export *`);
  // THEN BY REACHABILITY, over the two modules that import it, because a name
  // check cannot see `export { ruledCardBinding as somethingElse }` and a
  // top-level identity check cannot see `export * as seam` or a container object
  // holding it. Each namespace is WALKED, recursively, against the predicate
  // itself, so every alias and every route is the same answer. The whole tree is
  // asked the same question in the SURFACE test above; this is the two modules
  // that actually hold it, asked where the import graph is already in hand.
  for (const name of importersOf(BINDING_FILE)) {
    const namespace = await import(pathToFileURL(join(SRC, name)).href);
    const route = pathToValue(namespace, binding.ruledCardBinding);
    assert.equal(route, null,
      `${name} puts the shared predicate back on a public surface at ${name}${route}`);
  }
  // And the gate reaches the readers directly, which is what "no caller-supplied
  // reader" costs: a module-private import and nothing else.
  assert.ok(imports[GATE_FILE].includes(`./${READERS_FILE}`),
    "the gate no longer imports the readers it binds");
  assert.equal(imports[GATE_FILE].includes(`./${STORES_FILE}`), false,
    "the gate opens a store directly instead of going through a ruled reader");
  // The stores module statically imports the tenant constant, the digest recipe
  // and — since amendment 9 — the one closed-callable definition. `pg` is
  // dynamic on purpose, so the Worker bundle never pulls it in through here.
  assert.deepEqual(imports[STORES_FILE],
    ["./artifact-trust.js", "./closed-callable.js", "./identity.js"]);

  const strays = readdirSync(SRC).filter(name => /\.(testonly|testhelper|fixture)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");
});

// ---------------------------------------------------------------------------
// PART E — THE CHECK NAME IS AN ENUMERATION READ OFF THE WORKFLOWS, NOT A SHAPE
// SOMEBODY IMAGINED.
//
// DEFECT 0c7bc84a. Card 13's validator was
// `/^[A-Za-z0-9][A-Za-z0-9 ._/()-]{0,99}$/`, a character class with no comma in
// it, and the only check that guards main is named
// `main canary (gates, migration, types, freshness)`. The one name the reader
// exists to read was the one name it refused. `pg_dump -> age-encrypt ->
// artifact` was refused too, for the `>`. Nothing was broken about the reader's
// machinery; the validator had simply never been compared against a real name.
//
// SO THIS PART DERIVES THE SET A SECOND TIME, FROM THE DECLARING FILES, and
// asserts three things against that derivation rather than against the module's
// own list: every declared name is admitted, nothing else is, and the two lists
// are equal. The third is what makes the first two stay true — add a job to a
// workflow and this part goes red in the same commit, instead of card 13 quietly
// refusing the new check the first time anyone asks it about one.
// ---------------------------------------------------------------------------

const WORKFLOWS = join(REPO_ROOT, ".github/workflows");
const BACKUP_STATUS = join(REPO_ROOT, "ops/backup-workflow-status.py");

/**
 * THE CHECK-RUN NAMES THE WORKFLOWS DECLARE, parsed here rather than imported
 * from the module under test — a test that read the module's own list would
 * assert the module agrees with itself.
 *
 * GitHub names a workflow job's check run after the job's `name:`, and after the
 * job's ID when the job declares no name. Both cases are covered: a two-space
 * key under `jobs:` is a job id, a four-space `name:` beneath it renames it, and
 * a job that never gets one keeps its id.
 */
function declaredWorkflowCheckNames() {
  const names = [];
  for (const file of readdirSync(WORKFLOWS).sort()) {
    if (!/\.ya?ml$/.test(file)) continue;
    let inJobs = false;
    let current = -1;
    // A matrix job declares ONE templated name and GitHub reports one check per
    // matrix value, so the template is expanded here into the names a reader
    // will actually be asked about (ci.yml's class groups since 2026-09-23).
    // Only the single-axis `${{ matrix.<axis> }}` shape is expanded; anything
    // else stays literal and the reader test refuses it, which is the point.
    const matrices = new Map();
    let matrixJob = -1;
    let matrixAxis = null;
    for (const line of readFileSync(join(WORKFLOWS, file), "utf8").split("\n")) {
      if (/^jobs:\s*$/.test(line)) { inJobs = true; current = -1; continue; }
      if (!inJobs) continue;
      if (/^\S/.test(line)) { inJobs = false; continue; }
      const job = /^ {2}([A-Za-z0-9_-]+):\s*$/.exec(line);
      if (job !== null) { current = names.push(job[1]) - 1; matrixJob = -1; continue; }
      const named = /^ {4}name:\s*(\S.*?)\s*$/.exec(line);
      if (named !== null && current >= 0) names[current] = named[1];
      if (/^ {6}matrix:\s*$/.test(line)) { matrixJob = current; matrixAxis = null; continue; }
      if (matrixJob !== current || current < 0) continue;
      const inlineAxis = /^ {8}([A-Za-z0-9_-]+):\s*(\[.*\])\s*$/.exec(line);
      if (inlineAxis !== null) { matrices.set(`${current}:${inlineAxis[1]}`, JSON.parse(inlineAxis[2])); matrixAxis = null; continue; }
      const blockAxis = /^ {8}([A-Za-z0-9_-]+):\s*$/.exec(line);
      if (blockAxis !== null) { matrixAxis = `${current}:${blockAxis[1]}`; matrices.set(matrixAxis, []); continue; }
      const item = /^ {10}- (?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S.*?))\s*$/.exec(line);
      if (item !== null && matrixAxis !== null) matrices.get(matrixAxis).push(item[1] ?? item[2] ?? item[3]);
    }
    names.forEach((name, index) => {
      const template = /^(.*)\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}(.*)$/.exec(name);
      if (template === null) return;
      const values = matrices.get(`${index}:${template[2]}`);
      if (!Array.isArray(values)) return;
      names.splice(index, 1, ...values.map(value => `${template[1]}${value}${template[3]}`));
    });
  }
  // And the one check run this repository posts for itself, out of the constant
  // that posts it.
  const posted = /^CHECK_NAME = "([^"\n]+)"$/m.exec(readFileSync(BACKUP_STATUS, "utf8"));
  assert.ok(posted, "ops/backup-workflow-status.py no longer declares CHECK_NAME where this test reads it");
  names.push(posted[1]);
  return [...new Set(names)].sort();
}

/** The set the reader module actually carries, read out of its source. */
function moduleCheckNames() {
  const source = readFileSync(join(SRC, READERS_FILE), "utf8");
  const block = /const DECLARED_CHECK_NAMES = Object\.freeze\(\[([\s\S]*?)\]\);/.exec(source);
  assert.ok(block, "the reader no longer declares its check names where this test reads them");
  return [...block[1].matchAll(/"((?:[^"\\\n]|\\.)*)"/g)].map(one => one[1]).sort();
}

/**
 * The reader's own registered reason ids, read out of its source. The registry
 * is module-private on purpose — an importer able to enumerate it could
 * assemble a message out of it and hand it back in — so a test that needs to
 * assert "registered" reads the declaration rather than importing it.
 */
function registeredReaderReasonIds() {
  const source = readFileSync(join(SRC, READERS_FILE), "utf8");
  const registry = /const REASON_IDS = Object\.freeze\(\[([\s\S]*?)\]\);/.exec(source);
  assert.ok(registry, "the reader's reason registry is no longer where this test reads it");
  return [...registry[1].matchAll(/"([^"\n]+)"/g)].map(one => one[1]);
}

/** The validator this part retired, kept only so the control below can run it. */
const RETIRED_CHECK_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 ._/()-]{0,99}$/;

/** The one name main is actually guarded by, spelled once. */
const MAIN_CANARY_CHECK = "main canary";

test("CHECK NAME: the reader's set is exactly what the repository's own files declare", () => {
  const declared = declaredWorkflowCheckNames();
  // The canary is the defect's own case, asserted by name so that renaming the
  // job in main-canary.yml cannot quietly satisfy this test by removing it.
  assert.ok(declared.includes(MAIN_CANARY_CHECK),
    "main-canary.yml no longer declares the check this reader was fixed to admit");
  assert.deepEqual(moduleCheckNames(), declared,
    "a workflow declares a check name the reader would refuse, or the reader carries one nothing declares");
});

test("CHECK NAME: every declared check name is admitted, the canary's included", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const sha = fixtureStores.FIXTURE_COMMIT_SHA;

  for (const name of declaredWorkflowCheckNames()) {
    const answer = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: name });
    // ADMITTED means the query got past validation and the store was opened. A
    // name the fixture holds no row for comes back `gate_conclusion_check_absent`,
    // which is an answer ABOUT the store; `gate_conclusion_query_invalid` is the
    // refusal to look at all, and that is the one the defect produced.
    assert.notEqual(answer.reason_id, "gate_conclusion_query_invalid",
      `the reader refused ${JSON.stringify(name)}, which this repository declares`);
    assert.equal(answer.invalid_field, undefined);
    assert.equal(answer.card_ref, "card:13");
  }

  // And the defect's own name reaches a row and reads its conclusion.
  const canary = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: MAIN_CANARY_CHECK });
  assert.equal(canary.decision, "report");
  assert.equal(canary.finding, "gate_conclusion_observed");
  assert.equal(canary.conclusion, "success");
  assertSwept("card13.canary-name", canary);
});

test("CHECK NAME: a newline, a path separator and three hundred characters are each refused", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const sha = fixtureStores.FIXTURE_COMMIT_SHA;

  const NEWLINE = String.fromCharCode(10);
  const RETURN = String.fromCharCode(13);
  const refused = [
    ["a newline", `main canary${NEWLINE}(gates, migration, types, freshness)`],
    ["a carriage return", `main canary${RETURN}(gates, migration, types, freshness)`],
    ["a header split", `main canary${RETURN}${NEWLINE}x-injected: 1`],
    ["a trailing newline on a declared name", `${MAIN_CANARY_CHECK}${NEWLINE}`],
    ["a path separator", "../../foreign/name"],
    ["a path separator inside a declared name", "ops/../ci.sh --strict"],
    ["a bare separator", "/"],
    ["three hundred characters", "m".repeat(300)],
    ["the GitHub limit exactly", "m".repeat(255)],
    ["an empty name", ""],
    ["a declared name with a trailing space", "ops/ci.sh --strict "],
    ["a prototype key toString", "toString"],
    ["a prototype key constructor", "constructor"],
    ["a smuggled query parameter", "main canary&check_name=other"],
  ];

  for (const [why, name] of refused) {
    const answer = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: name });
    assert.equal(answer.reason_id, "gate_conclusion_query_invalid", `${why} was admitted`);
    assert.equal(answer.invalid_field, "checkName", `${why} was refused for the wrong field`);
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.status, "unavailable");
    assert.equal(answer.finding, null);
    // The registered-code contract: the id is one the module's own registry
    // holds, so a refusal cannot carry an unregistered word. The registry is
    // module-private by design, so it is read out of the source the same way
    // the store's reason registry is.
    assert.ok(registeredReaderReasonIds().includes(answer.reason_id),
      `${why} was refused with an unregistered reason id`);
    assertSwept(`card13.refused.${why}`, answer);
    // And the caller's own bytes are not in the answer. The two prototype keys
    // are ordinary English substrings of this module's prose, so they are read
    // for membership only; every other shape is checked byte-wise.
    if (name.length >= 6 && name !== "toString" && name !== "constructor")
      assert.equal(JSON.stringify(answer).includes(name.slice(0, 6)), false,
        `${why} put the caller's own bytes in the answer`);
  }
});

test("CHECK NAME CONTROL: the retired pattern has been seen to refuse the names this admits", () => {
  const declared = declaredWorkflowCheckNames();
  const wouldHaveBeenRefused = declared.filter(name => !RETIRED_CHECK_NAME_PATTERN.test(name)).sort();

  // THE MUTATION THIS TEST IS THE CONTROL FOR: put the retired pattern back and
  // the acceptance test above goes red on these two names. If this list is ever
  // empty the acceptance test proves nothing, because the old validator would
  // have passed it too.
  assert.deepEqual(wouldHaveBeenRefused, [
    "pg_dump -> age-encrypt -> artifact",
  ], "the retired pattern no longer refuses anything, so the acceptance test is not load-bearing");

  // And the retired pattern is not still in the module under some other name.
  // The class is asserted against as a DECLARATION rather than as a substring:
  // the module quotes it once, in the comment that records why it was retired,
  // and a test that banned the bytes would have banned the explanation too.
  const source = readFileSync(join(SRC, READERS_FILE), "utf8");
  assert.equal(/=\s*\/\^\[A-Za-z0-9\]\[A-Za-z0-9 \._\/\(\)-\]/.test(source), false,
    "the reader still assigns the character class that produced defect 0c7bc84a");
  assert.equal(/const CHECK_NAME\s*=/.test(source), false,
    "the retired CHECK_NAME pattern is still declared");
  assert.equal(source.split("[A-Za-z0-9 ._/()-]").length - 1, 1,
    "the retired character class appears somewhere other than the comment that retires it");
  assert.equal(/checkName", CHECK_NAME/.test(source), false,
    "the conclusion reader still validates its check name against a shape");

  // The module's own import-time self-check is real: every name it carries is
  // one GitHub would store, and none of them could split a header.
  for (const name of moduleCheckNames()) {
    assert.ok(name.length > 0 && name.length <= 255, `${name} is not a storable check name`);
    // Character codes, not an escape inside a pattern: the bytes this asserts
    // the absence of must not be writable into the assertion that looks for
    // them, or the test file itself carries what it forbids.
    const control = [...name].some(one => {
      const code = one.codePointAt(0);
      return code < 32 || code === 127;
    });
    assert.equal(control, false, `${name} carries a control character`);
  }
});
