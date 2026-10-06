// V5-A02 STEP A — THE GATE ZERO PRODUCER, proved the only way a seam that takes
// no argument can be proved: by staging a whole candidate tree, editing the
// things a RUN differs by, and reading what the module then answers.
//
// THERE IS NO INJECTION POINT AND THAT IS THE SUBJECT. `v5A02GateZeroEmitOutcome`
// has arity zero. It cannot be handed evidence, a hash, an identity, a revision,
// a reader or a store; the standing rule of 2026-09-11 forbids a public function
// that turns a caller's description of evidence into a verdict, under any name,
// and a function with nowhere to put one cannot have the defect. Since the PR
// 1013 correction it cannot be handed an IDENTITY either, and that is the other
// half: the producer reads the authenticated call it is running inside, so a
// process that cannot authenticate cannot obtain a receipt at all.
//
// EVERY CASE RUNS UNDER THE PRODUCTION CONDITION, WHICH IS NO `.git` AT ALL
// (standing-rule amendment 9, 2026-09-14). The staged trees used to hold a real
// loose object store, and every case that showed the producer working had first
// built the one thing the deployed Worker does not have. The fifth review round
// named it a P0: Cloudflare bundles the modules wrangler was pointed at, serves
// them over a read-only virtual filesystem, and supplies no `.git` directory —
// so in production the old derivation had exactly one reachable answer, which
// was to refuse. NO STAGED TREE HERE WRITES A `.git`, and the suite asserts that
// no staged tree HAS one before it asserts a receipt was produced. The mutation
// control is `reintroduceGitDerivation`, which puts a repository read back into
// the staged producer and turns the produced cases red.
//
// SO EVERY CASE BELOW IS A STAGED TREE under node_modules/.cache, holding:
//
//   * mcp-server/src — a copy, with the store module replaced by
//     ./gate-zero-producer-stores.v5.fixture.mjs, and with card 9's seat
//     declaration or the three `decision_id:` lines edited when a case asks;
//   * mcp-server/test — the sealed fixture bytes the fixture-set digest covers;
//   * ops/config/environments.json — the environment manifest its digest covers;
//
// and a set of BUILD STAMPS computed over exactly those bytes and written into
// the process environment for the duration of the call, which is what
// bin/deploy-worker.sh writes into a real upload with `wrangler --var`. The
// bytes still decide the digests; what changed is WHO READS THEM — the deploy
// wrapper's sealer, at build time, instead of the Worker at request time. That
// the sealer's digests move with the bytes is proved over a real git repository
// in gate-zero-candidate-seal.test.mjs; that the receipt's digests move with the
// stamp is proved here.
//
// Nothing in src reaches those trees, no argument selects one, and no
// environment variable OTHER THAN THE THREE DECLARED BUILD STAMPS moves an
// answer — which is its own test near the end of this file, run in a fresh
// process against a hostile environment.
//
// HOW THE AUTHENTICATED CALL IS OBTAINED, and it is the sixth correction round's
// subject. Nothing here mints or fabricates an actor, because identity.js no
// longer exports anything that could — and since this round, nothing here hands
// identity.js CODE either. The suite writes the recorded REVIEW_TOKENS map into
// its own process environment — becoming the server, the only way a suite now
// can — and then calls `serveAuthenticatedCall` with an Authorization header and
// the NAME of one of the server's own entries, exactly as a route would. That one
// entry matches the bearer internally, derives the identity internally, resolves
// the name in a frozen map identity.js holds itself, enters the context
// internally, and runs what it resolved inside it. The previous shape passed a
// CONTINUATION, and a callback parameter on an exported door is the exported
// context entry amendment 8 forbids: a probe holding the configured bearer ran
// its own code as `review_agent` through it. A caller-chosen bearer authenticates
// nobody, a function in the name's place is refused rather than run, and there is
// no second export to compose with anything — which is control 11.
//
// THE CONTROLS, and each is named where it is asserted:
//   1. an authenticated request + every row present -> passable, digest, instant
//   2. an UNAUTHENTICATED invocation                -> refused, and no receipt
//  11. every export, walked recursively             -> none mints an identity and
//                                                      none enters a context
//  12. the real request entry                       -> is what establishes the
//                                                      identity the producer reads
//   3. the receipt's producer identity              -> equals the authenticated seat
//   4. each of the three clauses turned off         -> the gate refuses or fails
//   5. each digest against its own stamp            -> one byte moves it
//   6. the seat back to unstaffed                   -> the whole slice goes dark
//   7. an injected non-green conclusion             -> status "fail", denials observed
//   8. subject maker == the reviewing seat          -> denied
//   9. a caller-supplied anything                   -> unreachable, by parse and call
//  10. the producer callable                        -> unreachable from every src
//                                                      namespace, by the repository's
//                                                      own runtime walk
//  13. no `.git` anywhere                           -> and a git read put back is red
//
//   node --test mcp-server/test/gate-zero-producer.v5.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync,
  statSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, relative, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { types } from "node:util";

import { artifactManifestDigest, canonicalJson, digest } from "../src/artifact-trust.js";
import { BUILD_STAMP_NAMES, CANDIDATE_MANIFEST_SCHEMA } from "../src/build-stamp.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  WHOLE_WALK, pathToValue, reachableCallables,
} from "./gate-zero-reachability-walk.testhelper.mjs";
import {
  CONSUMER_GATE_RECEIPT_FIELDS,
  CONSUMER_GATE_RECEIPT_SCHEMA,
  GATE_ZERO_STEP_REF,
} from "../src/benchmark-minimum.v5.js";
import { V5_A02_GATE_ZERO_REASON_IDS } from "../src/gate-zero-assurance.v5.js";
import * as producer from "../src/gate-zero-producer.v5.js";
import {
  CANARY_ABSENT, CANARY_UNBOUND, FORGED_HASH,
  CANDIDATE_BUILD_CORRELATION_ID, CANDIDATE_MAKER_ACTOR,
  REVISION_ALL_SUCCEED, REVISION_FAILED_ANCESTOR, REVISION_UNFINISHED,
} from "./gate-zero-producer-stores.v5.fixture.mjs";

const TEST_DIR = fileURLToPath(new URL("./", import.meta.url));
const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const REPO = fileURLToPath(new URL("../../", import.meta.url));
const PRODUCER_FILE = "gate-zero-producer.v5.js";
const REGISTRATION_FILE = "gate-zero-producer-registration.v5.js";
const RULINGS_FILE = "gate-zero-seam-rulings.v5.js";
const STORES_FILE = "gate-zero-seam-stores.v5.js";
const GATE_FILE = "gate-zero-assurance.v5.js";
const IDENTITY_FILE = "identity.js";
/**
 * THE NAME EVERY CASE IS SERVED THROUGH — identity.js's one entry for the Gate
 * Zero outcome, spelled exactly as V5_A02_GATE_ZERO_PRODUCER_SEAM. It is a NAME
 * rather than a callable on purpose (sixth correction round): a caller may choose
 * which of the server's entries runs and can express nothing else, and an
 * unknown name runs nothing at all.
 */
const GATE_ZERO_ENTRY = "seam:gate-zero-read-only-outcome-producer";
const FIXTURE_FILE = "gate-zero-producer-stores.v5.fixture.mjs";

/** The sealed fixture set the receipt's fixture_set_digest covers, by path. */
const SEALED_FIXTURES = Object.freeze([
  "gate-zero-producer-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.receipt-fixture.mjs",
]);
const ENVIRONMENT_MANIFEST = ["ops", "config", "environments.json"];

/**
 * THE RECORDED AUTHENTICATED CONTEXT. A bearer and the one-entry map shape the
 * Worker's REVIEW_TOKENS secret holds, which is everything index.js's review
 * door reads — recorded here so the call under test is the call the server
 * makes rather than a literal written out. The token is a fixture string: it
 * authenticates against the map beside it and against nothing else.
 */
const REVIEWING_SEAT_ACTOR = "codex-reviewer";
const RECORDED_REVIEW_TOKEN = "gate-zero-recorded-review-bearer-2026-09-12";
const RECORDED_REVIEW_TOKENS = JSON.stringify({ [REVIEWING_SEAT_ACTOR]: RECORDED_REVIEW_TOKEN });
const CORRELATION_ID = "3f2a6c18-9b4d-4e7a-8c11-5d0e2f7a6b93";

/**
 * A LEGACY MAP-TAKING DOOR'S RECORDED SECRET, and the OAuth grant door's
 * recorded witness. Both are here because the enumeration control has to show
 * every door refusing a caller's bytes — and, non-vacuously, the same doors
 * still answering for the server's own.
 */
const RECORDED_AGENT_ACTOR = "codex";
const RECORDED_AGENT_TOKEN = "gate-zero-recorded-agent-bearer-2026-09-12";
const RECORDED_AGENT_TOKENS = JSON.stringify({ [RECORDED_AGENT_ACTOR]: RECORDED_AGENT_TOKEN });
const RECORDED_GRANT_WITNESS = "gate-zero-recorded-oauth-client-secret-2026-09-12";

/**
 * THE SERVER'S OWN SECRET SOURCE, AND THERE IS NO OTHER ONE ANY MORE.
 *
 * identity.js reads its credentials ONCE, at module initialisation, out of the
 * environment the SERVER PROCESS was started with — `process.env`, which is what
 * wrangler populates this Worker's secrets into (nodejs_compat, 2026-07-01
 * compatibility date). There is no function to install a map, so there is no
 * first-caller race to win.
 *
 * SO THE SUITE BECOMES THE SERVER, the only way a suite now can: it writes the
 * recorded secrets into its own process environment, HERE, before any staged
 * tree is imported. Every `moduleOfTree(target, IDENTITY_FILE)` below is a fresh
 * module instance whose initialisation reads exactly these values, the way a
 * deployed Worker's does.
 *
 * The statically imported `../src/identity.js` instance was initialised before
 * this line ran and therefore holds NO credentials at all. That is deliberate
 * and load-bearing: every authenticated case goes through a staged module, so a
 * case cannot pass because some other import booted the door first.
 */
process.env.REVIEW_TOKENS = RECORDED_REVIEW_TOKENS;
process.env.AGENT_TOKENS = RECORDED_AGENT_TOKENS;
process.env.GOOGLE_CLIENT_SECRET = RECORDED_GRANT_WITNESS;

/** The maker and session the release-candidate record names, from the fixture. */
const SUBJECT_MAKER_ACTOR = CANDIDATE_MAKER_ACTOR;
const SUBJECT_MAKER_SESSION = `session:${CANDIDATE_BUILD_CORRELATION_ID}`;

const staged = [];
after(() => {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// THE STAGING. It builds a candidate tree and the BUILD STAMPS that describe it,
// because that is the pair the producer now stands on: the modules that run, and
// the sealed manifest the deploy wrapper stamped for the revision they were
// built from. Every edit is asserted to have matched exactly once before it is
// made — a staging whose anchor silently stopped matching would leave the case
// proving nothing, which is the failure mode the seam suites already learned.
// ---------------------------------------------------------------------------

const STAFFED_SEAT_LINE = '  holder_ref: "seat:codex-reviewer:gpt-5.6-sol",\n';
const UNSTAFFED_SEAT_LINE = "  holder_ref: null,\n";
const RULED_DECISION_LINES = Object.freeze([
  '    decision_id: "16c7cdfb-b675-4b6a-bbff-4bbdab46baf8",\n',
  '    decision_id: "f7c486d6-5bee-4c4c-a76f-c0f162f66db8",\n',
  '    decision_id: "87e9e11e-64b2-49b3-a6aa-4901c24eaa91",\n',
]);
const NULL_DECISION_LINE = "    decision_id: null,\n";

/** The lines the fixture STORE carries so a broken world is a store edit. */
const LEDGER_CANARY_LINE = "const LEDGER_CANARY = CANARY_JOINING;\n";
const PREDECESSOR_WORLD_LINE = 'const PREDECESSOR_WORLD = "clean";\n';
const CANDIDATE_RECORD_WORLD_LINE = 'const CANDIDATE_BUILD_RECORD_WORLD = "filed";\n';
/** The producer's own admitted-class line, edited by exactly one control. */
const AUTHORITY_CLASSES_LINE =
  'const PRODUCER_AUTHORITY_CLASSES = Object.freeze(["review_agent"]);\n';
/**
 * THE MUTATION CONTROL FOR AMENDMENT 9's FIRST CLAUSE. The producer's one read
 * of its build stamps, replaced by a read of a REPOSITORY — the shape this file
 * used to ship. In a staged tree there is no `.git`, so the read throws, the
 * gate's guarded boundary answers its own refusal, and every produced case goes
 * red. A producer that quietly kept a git path would pass the no-git assertions
 * below and fail nothing; this is what makes them mean something.
 */
const STAMPED_CANDIDATE_CALL = "  const stamped = stampedCandidate();\n";
const GIT_DERIVED_CANDIDATE_CALL =
  "  const stamped = (await import(\"node:fs\"))\n"
  + "    .readFileSync(new URL(\"../../../.git/HEAD\", import.meta.url), \"utf8\")\n"
  + "    && stampedCandidate();\n";

function editOnce(path, anchor, replacement, what) {
  const source = readFileSync(path, "utf8");
  assert.equal(source.split(anchor).length - 1, 1,
    `the staging anchor no longer matches ${what}`);
  writeFileSync(path, source.replace(anchor, replacement));
}

/** Git's own blob address for some bytes: sha1 of `blob <len>\0<bytes>`. */
const blobId = bytes => createHash("sha1")
  .update(Buffer.concat([Buffer.from(`blob ${bytes.length}\0`), bytes])).digest("hex");

/**
 * THE SEALED MANIFEST FOR A STAGED TREE, computed the way the deploy wrapper's
 * sealer computes it for a real one: git's blob id per candidate path for the
 * sealed half, sha256 of each blob's content for the observed half, JCS over the
 * parsed environment manifest, and sha256 per sealed fixture under its own path.
 *
 * IT IS COMPUTED FROM THE STAGED BYTES, not written out as constants, so the
 * byte-level controls this suite has always run still move the stamp: append one
 * comment to one staged module and its blob id and its content hash both move,
 * so the manifest moves, so the digest the producer reports moves. What this
 * file does NOT do is check that the PRODUCTION sealer computes the same values
 * — that is gate-zero-candidate-seal.test.mjs's job, over a real repository,
 * because a suite that recomputed the sealer's recipe here would only be proving
 * it had copied it.
 */
function sealStagedTree(base, revision) {
  const target = join(base, "mcp-server", "src");
  const files = [];
  const walk = (at) => {
    for (const name of readdirSync(at).sort()) {
      const full = join(at, name);
      if (statSync(full).isDirectory()) { walk(full); continue; }
      if (!name.endsWith(".js")) continue;
      files.push({ path: `mcp-server/src/${relative(target, full).split(sep).join("/")}`,
        bytes: readFileSync(full) });
    }
  };
  walk(target);
  files.sort((a, b) => (a.path < b.path ? -1 : 1));

  const sealedFixtures = {};
  for (const name of SEALED_FIXTURES) {
    const path = join(base, "mcp-server", "test", name);
    sealedFixtures[`mcp-server/test/${name}`] =
      existsSync(path) ? digest(readFileSync(path)) : null;
  }
  const environmentPath = join(base, ...ENVIRONMENT_MANIFEST);
  const sourceDigest = digest(canonicalJson(Object.fromEntries(
    files.map(file => [file.path, blobId(file.bytes)]))));
  return {
    schema_version: CANDIDATE_MANIFEST_SCHEMA,
    git_sha: revision,
    // A stand-in for git's tree id, derived from the sealed ids so it moves with
    // them. The producer only checks its shape and carries it into the
    // provenance digest; what a real tree id IS comes from git, at build time.
    candidate_tree_id: createHash("sha1").update(sourceDigest).digest("hex"),
    file_count: files.length,
    byte_length: files.reduce((total, file) => total + file.bytes.length, 0),
    artifact_digest: digest(canonicalJson(Object.fromEntries(
      files.map(file => [file.path, digest(file.bytes)])))),
    source_digest: sourceDigest,
    environment_manifest_digest: existsSync(environmentPath)
      ? digest(canonicalJson(JSON.parse(readFileSync(environmentPath, "utf8"))))
      : null,
    fixture_set_digest: digest(canonicalJson(sealedFixtures)),
  };
}

/** The three vars bin/deploy-worker.sh stamps, for one sealed manifest. */
function stampsFor(manifest, { drop = null, rewrite = null, coverRewrite = false } = {}) {
  const stamped = rewrite === null ? manifest : rewrite({ ...manifest });
  const values = {
    // ALWAYS THE SEALED SHA, never the rewritten one: the wrapper stamps the
    // revision it sealed, so a case that edits `git_sha` inside the manifest is
    // a manifest that names another revision — which is its own refusal.
    [BUILD_STAMP_NAMES.gitSha]: manifest.git_sha,
    [BUILD_STAMP_NAMES.candidateManifest]: canonicalJson(stamped),
    // THE DIGEST IS OVER THE MANIFEST AS SEALED. A case that rewrites the
    // manifest after the seal is the tampered-var case and the two stamps stop
    // agreeing, which is exactly what a hand-edited Worker var looks like from
    // inside the Worker. `coverRewrite` re-seals instead, so a case can isolate
    // a single WRONG FIELD from the digest disagreement.
    [BUILD_STAMP_NAMES.candidateManifestDigest]:
      digest(rewrite === null || coverRewrite ? stamped : manifest),
  };
  if (drop !== null) delete values[drop];
  return values;
}

/**
 * Run `fn` with these build stamps in the process environment, and put the
 * environment back afterwards. This is `wrangler --var` for one call: the
 * producer reads `process.env` at call time, which is the same binding store
 * wrangler populates from a Worker's vars.
 */
async function withStamps(stamps, fn) {
  const before = new Map();
  for (const name of Object.values(BUILD_STAMP_NAMES)) {
    before.set(name, process.env[name]);
    if (Object.hasOwn(stamps, name)) process.env[name] = stamps[name];
    else delete process.env[name];
  }
  try {
    return await fn();
  } finally {
    for (const [name, value] of before) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
}

/**
 * A candidate tree: a copy of src, the sealed fixtures and the environment
 * manifest — and NO `.git`, which is the production condition. `revision` is
 * what a RUN differs by; the rest are the faults a case injects.
 */
function stageTree({
  revision = REVISION_ALL_SUCCEED, staffedSeat = true,
  withdrawnCards = [], ledgerCanary = null, predecessorWorld = null, candidateRecordWorld = null,
  substituteStore = true, environmentEdit = null, candidateEdit = null,
  fixtureEdit = null, deniedClass = false, reintroduceGitDerivation = false,
  withoutEnvironmentManifest = false, dropStamp = null, rewriteManifest = null,
  coverRewrittenManifest = false,
} = {}) {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const base = mkdtempSync(join(cache, "gate-zero-producer-"));
  staged.push(base);
  const target = join(base, "mcp-server", "src");
  mkdirSync(target, { recursive: true });
  cpSync(SRC, target, { recursive: true });
  // THE TWO FILES src IMPORTS FROM OUTSIDE ITSELF. The staged tools.js is a real
  // module graph and will not load without them; they are copied rather than
  // stubbed so the dispatch under test is the dispatch that ships.
  cpSync(join(REPO, "mcp-server", "continuity-reference-manifest.mjs"),
    join(base, "mcp-server", "continuity-reference-manifest.mjs"));
  cpSync(join(REPO, "mcp-server", "assets"), join(base, "mcp-server", "assets"),
    { recursive: true });

  // THE TWO SEALED ARTIFACTS, copied as bytes. A case that mutates one mutates
  // the bytes, which is the whole point of asserting the digest moves.
  const stagedTest = join(base, "mcp-server", "test");
  mkdirSync(stagedTest, { recursive: true });
  for (const name of SEALED_FIXTURES) cpSync(join(TEST_DIR, name), join(stagedTest, name));
  if (fixtureEdit !== null) {
    const path = join(stagedTest, SEALED_FIXTURES[1]);
    writeFileSync(path, `${readFileSync(path, "utf8")}\n// ${fixtureEdit}\n`);
  }
  mkdirSync(join(base, "ops", "config"), { recursive: true });
  cpSync(join(REPO, ...ENVIRONMENT_MANIFEST), join(base, ...ENVIRONMENT_MANIFEST));
  if (environmentEdit !== null) {
    const path = join(base, ...ENVIRONMENT_MANIFEST);
    const manifest = JSON.parse(readFileSync(path, "utf8"));
    manifest.version = environmentEdit;
    writeFileSync(path, JSON.stringify(manifest, null, 2));
  }
  if (withoutEnvironmentManifest) rmSync(join(base, ...ENVIRONMENT_MANIFEST));

  if (!staffedSeat)
    editOnce(join(target, REGISTRATION_FILE), STAFFED_SEAT_LINE, UNSTAFFED_SEAT_LINE,
      "the seat declaration");

  if (withdrawnCards.length > 0) {
    const path = join(target, RULINGS_FILE);
    let source = readFileSync(path, "utf8");
    RULED_DECISION_LINES.forEach((anchor, index) => {
      assert.equal(source.split(anchor).length - 1, 1,
        `the staging anchor no longer matches card ${11 + index}'s ruling line`);
      if (withdrawnCards.includes(index)) source = source.replace(anchor, NULL_DECISION_LINE);
    });
    writeFileSync(path, source);
  }

  if (deniedClass)
    editOnce(join(target, PRODUCER_FILE), AUTHORITY_CLASSES_LINE,
      'const PRODUCER_AUTHORITY_CLASSES = Object.freeze(["verified_partner"]);\n',
      "the producer's admitted authority classes");

  if (reintroduceGitDerivation)
    editOnce(join(target, PRODUCER_FILE), STAMPED_CANDIDATE_CALL, GIT_DERIVED_CANDIDATE_CALL,
      "the producer's one read of its build stamps");

  if (candidateEdit !== null) {
    const path = join(target, "global-boundaries.v5.js");
    writeFileSync(path, `${readFileSync(path, "utf8")}\n// ${candidateEdit}\n`);
  }

  if (substituteStore) {
    const store = join(target, STORES_FILE);
    cpSync(join(TEST_DIR, FIXTURE_FILE), store);
    // THE STORE IS WHERE A BROKEN WORLD IS CHOSEN. The producer derives its
    // addresses, so a test can no longer steer by writing one — it edits the
    // row the store holds, which is the honest place for a fault to live.
    if (ledgerCanary !== null)
      editOnce(store, LEDGER_CANARY_LINE, `const LEDGER_CANARY = ${JSON.stringify(ledgerCanary)};\n`,
        "the fixture store's canary line");
    if (predecessorWorld !== null)
      editOnce(store, PREDECESSOR_WORLD_LINE,
        `const PREDECESSOR_WORLD = ${JSON.stringify(predecessorWorld)};\n`,
        "the fixture store's predecessor world line");
    if (candidateRecordWorld !== null)
      editOnce(store, CANDIDATE_RECORD_WORLD_LINE,
        `const CANDIDATE_BUILD_RECORD_WORLD = ${JSON.stringify(candidateRecordWorld)};\n`,
        "the fixture store's release-candidate world line");
  }

  // SEALED LAST, so every edit a case asked for is inside the manifest the
  // stamps describe — which is what a real deploy does too: the wrapper seals
  // the revision it is about to upload.
  const manifest = sealStagedTree(base, revision);
  // AND THE PRODUCTION CONDITION IS ASSERTED, not assumed: a staged tree that
  // grew a `.git` would let a git-derived producer pass every case below.
  assert.equal(existsSync(join(base, ".git")), false,
    "a staged tree holds a .git, so the production condition is not being tested");
  return { base, target, manifest,
    stamps: stampsFor(manifest, { drop: dropStamp, rewrite: rewriteManifest,
                                  coverRewrite: coverRewrittenManifest }) };
}

const moduleOfTree = (target, file) => import(pathToFileURL(join(target, file)).href);

/**
 * SERVE A REAL AUTHENTICATED CALL in a staged tree, and answer what the gate
 * answered inside it.
 *
 * THIS IS THE ONLY WAY AN AUTHENTICATED CALL EXISTS NOW, and the shape of it is
 * the SIXTH correction round's first finding. The previous helper handed
 * identity.js a CONTINUATION — `serveReviewRequestAuthenticated(header,
 * correlationId, fn)` — and that callback parameter was an exported context
 * entry however the identity behind it was derived: a probe holding the
 * configured bearer ran its own code as `review_agent` through exactly this
 * helper's shape.
 *
 * So there is no `fn` any more, here or on the door. `serveAuthenticatedCall`
 * takes the request's own Authorization header, the server's correlation id and
 * the NAME of one of the server's own entries; identity.js resolves that name in
 * a frozen map it holds itself and runs what it resolved inside the context.
 * This helper cannot choose an identity, and it cannot choose CODE either — the
 * widest thing it can express is which of the server's entries runs, and the
 * entry it names is the Gate Zero outcome the whole suite is about.
 *
 * Answers `{ served: false }` when the bearer matched nothing, which is the
 * server's "this was not a review request" and the next door's turn.
 */
async function inServedRequest(target, { bearer = RECORDED_REVIEW_TOKEN,
                                         correlationId = CORRELATION_ID,
                                         entryName = GATE_ZERO_ENTRY } = {}) {
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  const answered = await identity.serveAuthenticatedCall(
    bearer === null ? "" : `Bearer ${bearer}`, correlationId, entryName);
  if (answered === null) return { served: false, answered: null };
  return { served: true, answered };
}

/**
 * What the gate answers in a staged tree, under this deploy's stamps.
 *
 * `seat` names the request: a bearer and a correlation id. `seat: null` runs the
 * same gate with no request and no authenticated call at all.
 */
async function emitFrom(options = {}, seat = {}) {
  const { target, stamps } = stageTree(options);
  const gate = await moduleOfTree(target, GATE_FILE);
  return withStamps(stamps, async () => {
    if (seat === null) return gate.emitGateZeroOutcome();
    const served = await inServedRequest(target, seat);
    assert.equal(served.served, true, "the recorded review bearer was not served");
    return served.answered;
  });
}

/**
 * THE CANDIDATE DIGEST RECOMPUTED HERE, from the sealed manifest the stamps
 * carry, by artifact-trust.js's own `artifactManifestDigest` — the same JCS
 * SHA-256 recipe ops.scac_artifact_manifest_digest recomputes in the database.
 * A producer that hashed something else, or that stamped a constant, is red.
 */
function candidateDigestOfStamps(manifest, policyDigest) {
  return artifactManifestDigest({
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
  });
}

// ===========================================================================
// CONTROL 1 — an authenticated call, over rows that are all there.
// ===========================================================================

test("PRODUCED: with every row present the gate is passable, with a real digest and instant", async () => {
  const emitted = await emitFrom({});
  assert.equal(emitted.passable, true, emitted.unavailable_because ?? emitted.reason_id);
  assert.equal(emitted.status, "outcome_produced");
  assert.equal(emitted.decision, "report");
  assert.equal(emitted.reason_id, null);
  assert.equal(emitted.producer_bound, true);
  assert.deepEqual(emitted.owed_seams, []);
  assert.equal(emitted.receipt_status, "pass");

  // A REAL DIGEST, by the recipe r7 DECLARES — the TAGGED preimage, over the
  // receipt that is right there, recomputed here rather than trusted, so a
  // producer that stamped a constant or hashed something else is red.
  assert.match(emitted.outcome_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(emitted.outcome_digest,
    digest([CONSUMER_GATE_RECEIPT_SCHEMA, emitted.receipt]));
  // AND IT IS NOT THE PLAIN ONE. r7's amended receipt_payload_digest_rule says
  // in its own words that a digest over the receipt alone does not satisfy it,
  // and the accepted plan names a plain digest by either reader as a failure
  // condition — so the refusal is asserted rather than implied by the positive.
  assert.notEqual(emitted.outcome_digest, digest(emitted.receipt),
    "the producer still emits the untagged digest r7 refuses");
  assert.equal(emitted.outcome_digest_recipe.domain_tag, CONSUMER_GATE_RECEIPT_SCHEMA);
  assert.equal(emitted.outcome_digest_recipe.schema_ref, CONSUMER_GATE_RECEIPT_SCHEMA);
  assert.equal(emitted.outcome_digest_recipe.self_digest_excluded, true);
  assert.equal(Object.hasOwn(emitted.receipt, "outcome_digest"), false,
    "the receipt carries its own digest, which the canonicalization rule forbids");

  // A REAL INSTANT, and it is the receipt's own. This value is the zero of the
  // v5 clock: everything downstream is timestamped strictly after it.
  assert.match(emitted.observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  assert.equal(emitted.observed_at, emitted.receipt.observed_at);
  assert.ok(Date.parse(emitted.receipt.ttl_expires_at) > Date.parse(emitted.observed_at));

  // AND THE JOIN IS THE THREE CLAUSES, each held over rows.
  assert.deepEqual(Object.keys(emitted.join).sort(),
    ["gate_graph", "predecessor_join", "scheduler_canary"]);
  for (const [name, clause] of Object.entries(emitted.join))
    assert.equal(clause.state, "held", name);
  assert.equal(emitted.negative_admission.result, "all_required_denials_observed");
  assert.equal(emitted.negative_admission.case_count, 7);
  assert.deepEqual(emitted.negative_admission.missed_cases, []);
  assert.deepEqual(emitted.effects, V5_NO_EFFECTS);
  assert.equal(emitted.persisted, false);
  assert.equal(emitted.durable_outcome_record_required, true);
});

test("RECEIPT: all twenty-one fields of consumer-gate-receipt.v1, and nothing else", async () => {
  const { receipt } = await emitFrom({});
  assert.deepEqual(Object.keys(receipt).sort(), [...CONSUMER_GATE_RECEIPT_FIELDS].sort());
  assert.equal(Object.keys(receipt).length, 21);

  assert.equal(receipt.gate_id, "gate-zero-read-only-accepted");
  assert.equal(receipt.receipt_producer_step_ref, GATE_ZERO_STEP_REF);
  assert.equal(receipt.producer_role, "independent_control_plane_oracle");
  assert.equal(receipt.independent_oracle_ref, "oracle:gate-producer:gate-zero-read-only");
  assert.equal(receipt.oracle_version, "1.0.0");
  assert.equal(receipt.subject_environment, "candidate");
  assert.equal(receipt.evidence_scope, "candidate-and-test");
  assert.equal(receipt.status, "pass");
  assert.equal(receipt.negative_admission_result, "all_required_denials_observed");

  for (const field of ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"])
    assert.match(receipt[field], /^sha256:[0-9a-f]{64}$/, field);
  // FIVE DISTINCT DIGESTS. A producer that computed one preimage and used it
  // five times would satisfy every pattern above and mean nothing.
  const digests = ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"].map(field => receipt[field]);
  assert.equal(new Set(digests).size, 5, "two of the five digests are the same value");

  // r7's evidence_ref shape, and it is lowercase-only — a single capital is
  // refused by benchmark-minimum with a bare error naming no field.
  assert.match(receipt.evidence_ref, /^safe:[a-z0-9][a-z0-9:_./-]*$/);
  assert.ok(receipt.comparator.length >= 5 && receipt.comparator.length <= 300);

  // THE THREE IDENTITIES, each an authenticated-receipt-identity.v1, and the
  // independence r7's identity rule requires.
  for (const field of ["subject_maker_identity", "producer_identity", "evaluator_identity"]) {
    assert.deepEqual(Object.keys(receipt[field]).sort(),
      ["actor_id", "authority_class", "session_ref"], field);
    assert.match(receipt[field].session_ref, /^session:[a-z0-9][a-z0-9:._/-]{8,199}$/, field);
  }
  // THE PRODUCER IDENTITY IS THE AUTHENTICATED ACTOR, field for field. This is
  // the mutation control the first review round asked for: a receipt signed by
  // anything other than the call that produced it is a receipt nobody
  // authenticated, and before this correction the module reconstructed one out
  // of card 9's holder constant.
  assert.equal(receipt.producer_identity.actor_id, REVIEWING_SEAT_ACTOR);
  assert.equal(receipt.evaluator_identity.actor_id, REVIEWING_SEAT_ACTOR);
  // DERIVED BY identity.js, not typed anywhere: `review_agent` is what that
  // module resolves a review-token machine identity to.
  assert.equal(receipt.producer_identity.authority_class, "review_agent");
  // AND THE SESSION IS THE SERVER'S OWN CORRELATION ID, not a digest of what the
  // run happened to be looking at. A second call under a second correlation id
  // gets a second session; the old design gave both the same one.
  assert.equal(receipt.producer_identity.session_ref, `session:${CORRELATION_ID}`);
  const elsewhere = "7b1c9d40-2e55-4a61-9f03-8ac4be21d7e6";
  const second = await emitFrom({}, { correlationId: elsewhere });
  assert.equal(second.receipt.producer_identity.session_ref, `session:${elsewhere}`);

  // THE SUBJECT MAKER IS THE RELEASE-CANDIDATE RECORD for the stamped revision —
  // amendment 9(b) — so BOTH halves come out of a row somebody had to be
  // authenticated to write. The actor is the `maker_actor` the ops recorder
  // filed, resolved through identity.js's partner registry, and the AUTHORITY
  // CLASS IS DERIVED THERE: the first draft wrote the constant
  // `candidate_builder` into this field, which named a class nothing in this
  // system derives, admits or checks.
  assert.equal(receipt.subject_maker_identity.actor_id, SUBJECT_MAKER_ACTOR);
  assert.equal(receipt.subject_maker_identity.authority_class, "verified_partner");
  // THE SESSION IS THE RECORD'S OWN CORRELATION, and this is the line the fifth
  // review round's finding 3 is about. It used to be the EVALUATOR'S session
  // with `:candidate-build` appended — the reviewer's seat, relabelled as the
  // maker's. It is now the correlation the recorder stamped on the release row,
  // so it does not move with the call doing the judging.
  assert.equal(receipt.subject_maker_identity.session_ref, SUBJECT_MAKER_SESSION);
  assert.equal(JSON.stringify(receipt).includes("candidate-build"), false,
    "the evaluator's session is still being relabelled as the maker's");
  assert.equal(second.receipt.subject_maker_identity.session_ref, SUBJECT_MAKER_SESSION,
    "the maker's session moved with the evaluator's correlation id");
  assert.notEqual(receipt.subject_maker_identity.actor_id, receipt.producer_identity.actor_id);
  assert.notEqual(receipt.subject_maker_identity.session_ref, receipt.producer_identity.session_ref);
});

// ===========================================================================
// CONTROL 2 — an unauthenticated invocation cannot obtain a receipt.
// ===========================================================================

test("IDENTITY: with no authenticated call there is no receipt, only a derived refusal", async () => {
  // The same staged tree, the same rows, the same staffed seat — and no call.
  // This is the shape the first review round found emitting a receipt signed
  // `codex-reviewer` to whatever imported the module.
  const emitted = await emitFrom({}, null);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.observed_at, null);
  assert.equal(emitted.producer_answer.receipt, null, "an unauthenticated call got a receipt");
  assert.equal(emitted.producer_answer.clauses, null,
    "an unauthenticated call reached the clauses");
  // The gate still NAMES card 9's seat holder — that is the declaration of who
  // may sign, and it is public. What must not exist is a signature: no identity,
  // no session ref, no receipt.
  assert.equal(JSON.stringify(emitted).includes("session:"), false,
    "a session ref was minted for an answer nobody authenticated");
  // And nowhere in the answer is there an `authenticated-receipt-identity.v1`:
  // asked of the SHAPE rather than of a field name, because the gate legitimately
  // names the receipt's field list in its own policy preimage.
  const identities = [];
  const walk = (value) => {
    if (Array.isArray(value)) { value.forEach(walk); return; }
    if (value === null || typeof value !== "object") return;
    const keys = Object.keys(value).sort().join(",");
    if (keys === "actor_id,authority_class,session_ref") identities.push(value);
    Object.values(value).forEach(walk);
  };
  walk(emitted);
  assert.deepEqual(identities, [],
    "an identity was derived for an answer nobody authenticated");

  // AND src ITSELF, imported bare, answers the same way. A CLI probe and a test
  // are the same case as far as this module is concerned.
  const bare = await producer.v5A02GateZeroEmitOutcome();
  assert.equal(bare.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(bare.receipt, null);
});

test("IDENTITY: an authenticated seat of a class this oracle does not admit is refused", () => {
  // r7's registry admits exactly one authority class for an independent
  // control-plane oracle, and the producer checks MEMBERSHIP rather than
  // asserting the class it expects. The case is reached the way every other case
  // here is: by editing that one line in a throwaway tree, so the check is
  // PROVED live rather than assumed unreachable.
  return emitFrom({ deniedClass: true }).then(emitted => {
    assert.equal(emitted.passable, false);
    assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
    assert.equal(emitted.producer_answer.receipt, null);
    assert.equal(emitted.producer_answer.clauses, null,
      "a refused class reached the clauses");
  });
});

test("IDENTITY: a request with no server-written correlation id obtains no session", async () => {
  // The correlation id is written per request by correlation.js, onto
  // env.CORRELATION_ID, and it is the only per-call identifier in this system no
  // caller writes. A request that arrives without one is SERVED — the server does
  // not drop a review-council call over a missing log id — and runs with the
  // context CLEARED, so it obtains no receipt.
  for (const correlationId of [null, "", "not-a-correlation-id", "3f2a6c18"]) {
    const emitted = await emitFrom({}, { correlationId });
    assert.equal(emitted.passable, false, String(correlationId));
    assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused", String(correlationId));
    assert.equal(emitted.producer_answer.receipt, null);
    assert.equal(JSON.stringify(emitted).includes("session:"), false,
      "a session ref was minted for a call with no server-written correlation id");
  }
});

// ===========================================================================
// CONTROL 11 — THERE IS NOTHING LEFT TO COMPOSE, AND NOTHING LEFT TO HAND OVER.
//
// THE FIFTH ROUND'S FINDING was a COMPOSITION: `reviewActorForToken(header)`
// minted a branded actor and `dispatchFor(actor)` returned a callable that
// entered a context, and a probe composed them. Both names are deleted and both
// capabilities are module-private.
//
// THE SIXTH ROUND'S FINDING was the CALLBACK that replaced them. The single
// entry left standing took `(header, correlationId, run)` and ran `run` inside
// `enterAuthenticatedCall`, so a probe holding the configured bearer needed no
// composition at all — it handed the door its own code. A callback parameter on
// an exported door IS an exported context entry, which amendment 8 forbids in as
// many words.
//
// SO THE ENTRY TAKES A NAME. `serveAuthenticatedCall(header, correlationId,
// entryName)` resolves the name in a frozen map identity.js holds itself, and
// there is no parameter anywhere on the file's export surface that a function
// may travel through. The cases below therefore assert two things rather than
// one: a caller-chosen bearer reaches nothing, AND the server's own bearer
// cannot be made to run caller code either.
// ===========================================================================

test("IDENTITY: a caller-chosen bearer is served by nobody and enters no context", async () => {
  const CALLERS_OWN = "a-bearer-the-caller-chose";
  const { target, stamps } = stageTree({});
  const identity = await moduleOfTree(target, IDENTITY_FILE);

  // THE ONE ENTRY IS A BEARER AND A NAME. A bearer the caller chose matches
  // nothing in the map identity.js read from the server's environment, so the
  // entry answers null — "not a review request" — and nothing runs.
  assert.equal(
    await identity.serveAuthenticatedCall(`Bearer ${CALLERS_OWN}`, CORRELATION_ID,
      GATE_ZERO_ENTRY),
    null);

  // AND A FUNCTION IN THE NAME'S PLACE IS REFUSED RATHER THAN RUN — with the
  // SERVER'S OWN bearer, which is the exact construction the sixth review
  // performed. This is the negative of the case that used to stand here.
  let ran = false;
  assert.equal(
    await identity.serveAuthenticatedCall(`Bearer ${RECORDED_REVIEW_TOKEN}`,
      CORRELATION_ID, () => { ran = true; return "served"; }),
    null);
  assert.equal(ran, false, "the configured bearer ran caller code inside the context");

  // NON-VACUOUS: the server's own bearer and a NAME the server holds do enter a
  // context, and what runs inside it — the server's own entry, not the caller's
  // — obtains the three-field identity r7 requires.
  const served = await withStamps(stamps, () => inServedRequest(target, {}));
  assert.equal(served.served, true, "the recorded review bearer was not served");
  assert.deepEqual(served.answered.receipt.evaluator_identity, {
    actor_id: REVIEWING_SEAT_ACTOR, session_ref: `session:${CORRELATION_ID}`,
    authority_class: "review_agent" });

  // AND NEITHER COMPOSITION NOR CALLBACK CAN BE WRITTEN, because no half is
  // exported under any name. This is the mutation control stated the way the
  // reviews stated it: the probe cannot NAME a function to call.
  for (const gone of ["runInAuthenticatedCall", "authenticatedCallIdentity",
    "reviewActorForToken", "dispatchFor", "dispatchAuthenticatedCall",
    "authenticatedCallReceiptIdentity", "actorFromProps", "sealServerReviewTokens",
    "buildContext", "committerIdentity", "enterAuthenticatedCall",
    "serveReviewRequestAuthenticated"]) {
    assert.equal(Object.hasOwn(identity, gone), false, `${gone} is still exported`);
    assert.equal(Object.hasOwn(identity.authenticatedIdentity, gone), false,
      `${gone} is still on the authenticated-identity surface`);
  }
  assert.equal(Object.keys(identity.authenticatedIdentity)
    .some(name => /seal|install|bind|set|dispatch/i.test(name)), false,
    "the surface grew something that reads like an installer or a dispatcher");

  // AND THE PRODUCER AGREES, through the real gate: a request nobody served
  // never establishes a call, so the producer refuses.
  const gate = await moduleOfTree(target, GATE_FILE);
  const emitted = await withStamps(stamps, async () => {
    const unserved = await inServedRequest(target, { bearer: CALLERS_OWN });
    assert.equal(unserved.served, false, "a caller-chosen bearer was served");
    return gate.emitGateZeroOutcome();
  });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.producer_answer.receipt, null);
});

/**
 * THE HOSTILE VALUES — AND THE SERVER'S REAL BEARER IS AMONG THEM, which is the
 * sixth review round's own instruction and the difference between this control
 * and the one it replaces.
 *
 * The previous set deliberately EXCLUDED the configured bearer, so the sweep only
 * ever proved that a caller who cannot authenticate runs nothing — and the review
 * then supplied that bearer by hand and watched a callback run inside a
 * `review_agent` context. Provenance was never the question; the question is
 * whether holding the credential buys the ability to run CODE. So the recorded
 * token is in here twice, raw and as an Authorization header, and the claim under
 * test is the strong one: NO export of identity.js, at any depth, runs a caller's
 * function — with or without the server's own credential — and none of them
 * enters an identity context around one. The enumeration feeds every reachable
 * callable every 1-, 2- and 3-tuple drawn from this set and follows any callable
 * ANSWER one step further.
 */
function hostileValues(probe) {
  return [
    null,
    "Bearer a-bearer-the-caller-chose",
    `Bearer ${RECORDED_REVIEW_TOKEN}`,
    RECORDED_REVIEW_TOKEN,
    CORRELATION_ID,
    JSON.stringify({ [REVIEWING_SEAT_ACTOR]: "a-bearer-the-caller-chose" }),
    JSON.stringify({ [RECORDED_AGENT_ACTOR]: "a-bearer-the-caller-chose" }),
    { slug: REVIEWING_SEAT_ACTOR, display: `Reviewer (${REVIEWING_SEAT_ACTOR})`,
      human: false, review: true, via: "review-token", client_id: null,
      correlation_id: CORRELATION_ID },
    { slug: "joe", human: true, via: "oauth-google", correlation_id: CORRELATION_ID },
    probe,
  ];
}

test("IDENTITY: no export of identity.js, at any depth, enters a context from caller bytes",
  async () => {
    const { target } = stageTree({});
    const identity = await moduleOfTree(target, IDENTITY_FILE);
    const door = identity.authenticatedIdentity;

    // THE SURFACE IS WALKED, NOT LISTED, AND WALKED RECURSIVELY — which is the
    // fifth review round's second finding. The old enumeration read
    // `Object.entries` of the namespace and then one level into each object
    // member, functions only; a mutation exporting `{ deep: { leak } }` was
    // never enumerated and returned a branded actor while the assertion beside
    // it claimed to cover "every callable on the module". This uses the SHARED
    // reachability walker — the one the producer's own unreachability guard uses
    // and the readers suite's part-by-part mutation controls measure — so
    // symbols, prototypes, accessors, accessor functions and inherited getters
    // under the exported child as receiver are all followed, at every depth.
    const surface = reachableCallables(identity, WHOLE_WALK);
    assert.ok(surface.length >= 20,
      `identity.js's callable surface enumerated as ${surface.length}, too few to be all of it`);
    for (const required of [".agentActorForToken", ".continuityActorForTokenMaps",
      ".hermesActorForToken", ".hermesCosActorForToken", ".propsForSlug",
      ".serveAuthenticatedCall", ".serveReviewRequest",
      ".authenticatedIdentity.connectionForGrant",
      ".authenticatedIdentity.receiptIdentity", ".authenticatedIdentity.partnerIdentity"])
      assert.ok(surface.some(([route]) => route === required),
        `${required} was not reached by the enumeration`);

    // AND THE ENUMERATION IS NON-VACUOUS AT DEPTH, measured rather than argued:
    // a nested container holding a callable is reached. This is exactly the
    // shape the review mutated in, asserted against the walker itself so the
    // control cannot pass by the walker having stopped working.
    const leak = () => null;
    assert.ok(reachableCallables({ api: { deep: { leak } } }, WHOLE_WALK)
      .some(([route, value]) => value === leak && route === ".api.deep.leak"),
      "the enumeration does not reach a callable nested two levels down");

    // A HOSTILE ARGUMENT THAT LOOKS BACK. If any export runs a caller's function
    // at all, this records the fact — and what the context held when it did.
    const seen = [];
    const probe = (...args) => {
      seen.push(door.receiptIdentity());
      return args[0] ?? null;
    };
    const hostile = hostileValues(probe);

    let answeredAnything = false;
    for (const [route, callable] of surface)
      for (const a of hostile) for (const b of hostile) for (const c of hostile) {
        let answered;
        try { answered = callable(a, b, c); } catch { continue; }
        if (answered === null || answered === undefined) continue;
        answeredAnything = true;
        // A callable ANSWER is followed one step further: a dispatcher handed
        // back from an export would be the context entry amendment 8 forbids,
        // and following it is how the fifth round's probe reached one.
        if (typeof answered === "function") {
          try { answered(probe); } catch { /* a refusal is an answer */ }
        }
        // And so is a callable held ON an answer, at any depth: an export that
        // returned `{ run }` would hand back the same capability one key down.
        for (const [, nested] of reachableCallables(Object(answered), WHOLE_WALK)) {
          if (nested === probe || nested === callable) continue;
          try { nested(probe); } catch { /* a refusal is an answer */ }
        }
        void route;
      }

    assert.ok(answeredAnything, "no export answered anything, so this proved nothing");
    for (const held of seen)
      assert.equal(held, null, "an export ran a caller's function inside an identity context");


    // AND THE PROBE WAS NEVER CALLED AT ALL, by any export, on any tuple —
    // including the tuples carrying the server's own bearer. `seen` records one
    // entry per invocation, so this is the stronger statement the sixth review
    // asked for: not "a caller's code read nothing", but "a caller's code did not
    // run".
    assert.deepEqual(seen, [],
      "an export invoked a caller-supplied function, whatever it read");

    // NON-VACUOUS, AND THIS IS WHAT MAKES THE EMPTY SET MEAN SOMETHING: the same
    // bearer, with a NAME instead of a function, DOES enter a context — and what
    // runs inside it is the server's own entry, which obtains the three-field
    // identity r7 requires. The context exists; nothing exported lets a caller
    // into it.
    const inside = await identity.serveAuthenticatedCall(
      `Bearer ${RECORDED_REVIEW_TOKEN}`, CORRELATION_ID, GATE_ZERO_ENTRY);
    assert.notEqual(inside, null, "the server's own bearer and name were not served");
    assert.deepEqual(seen, [], "the named entry ran caller code");
    assert.equal(inside.producer_bound, true);

    // AND A FUNCTION IN THE NAME'S PLACE, WITH THAT SAME BEARER, IS REFUSED —
    // stated here as well as in control 11 because this is the enumeration that
    // sweeps the surface, and a reader comparing the two rounds should find the
    // prohibited construction spelled out beside its refusal.
    assert.equal(await identity.serveAuthenticatedCall(
      `Bearer ${RECORDED_REVIEW_TOKEN}`, CORRELATION_ID, probe), null);
    assert.deepEqual(seen, [], "a function in the entry-name slot was called");

    // AND THE LEGACY MAP-TAKING DOORS STILL RETURN THEIR ACTORS, which is what
    // makes the loop above a real sweep rather than a sweep over doors that
    // answer nothing. What none of them can do any more is reach a context: an
    // actor is not a capability once nothing exported turns one into a call.
    assert.notEqual(identity.agentActorForToken(
      `Bearer ${RECORDED_AGENT_TOKEN}`, RECORDED_AGENT_TOKENS), null);
    assert.notEqual(door.connectionForGrant(
      identity.propsForSlug("joe", { via: "oauth-google" }), null, RECORDED_GRANT_WITNESS), null);
  });

test("DIGEST: each bound digest stands on its own stamped artifact", async () => {
  const first = await emitFrom({});
  // The receipts differ only by their instants, so two runs over one candidate
  // agree on all five bound digests.
  const second = await emitFrom({});
  for (const field of ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"])
    assert.equal(first.receipt[field], second.receipt[field], field);

  // (a) THE CANDIDATE DIGEST IS THE SEALED ARTIFACT MANIFEST FOR THE STAMPED
  // REVISION — recomputed here by artifact-trust.js's own artifactManifestDigest
  // over the manifest the stamps carry, which is the same JCS SHA-256 recipe
  // ops.scac_artifact_manifest_digest recomputes in the database. A producer
  // that hashed a DESCRIPTION of the candidate, or that stamped a constant,
  // would satisfy every shape assertion in this file and fail this one.
  const { target, manifest, stamps } = stageTree({});
  const gate = await moduleOfTree(target, GATE_FILE);
  const own = await withStamps(stamps, async () =>
    (await inServedRequest(target, {})).answered);
  assert.equal(own.receipt.candidate_digest,
    candidateDigestOfStamps(manifest, own.receipt.policy_digest),
    "the candidate digest is not the sealed artifact manifest the stamps carry");

  // (b) ONE BYTE OF THE CANDIDATE MOVES IT. A comment appended to one module in
  // the staged src moves that file's blob id and its content hash, so it moves
  // the sealed manifest, so it moves the stamp, so it moves this.
  const edited = await emitFrom({ candidateEdit: "one byte of the candidate tree" });
  assert.notEqual(edited.receipt.candidate_digest, first.receipt.candidate_digest);
  // And it moves NOTHING ELSE: the environment and the fixture set did not change.
  assert.equal(edited.receipt.environment_manifest_digest,
    first.receipt.environment_manifest_digest);
  assert.equal(edited.receipt.fixture_set_digest, first.receipt.fixture_set_digest);

  // (c) THE MANIFEST IS BOUND TO THE REVISION, so a stamp for another revision
  // moves the candidate digest even when the bytes are identical.
  const elsewhere = await emitFrom({ revision: REVISION_FAILED_ANCESTOR });
  assert.notEqual(elsewhere.receipt.candidate_digest, first.receipt.candidate_digest);
  assert.equal(elsewhere.receipt.subject_digest, first.receipt.subject_digest,
    "the subject is the gate, not the candidate, and must not move with it");

  // (d) THE ENVIRONMENT DIGEST IS OVER ops/config/environments.json, so one byte
  // of THAT file moves it and moves nothing else.
  const environment = await emitFrom({ environmentEdit: 2 });
  assert.notEqual(environment.receipt.environment_manifest_digest,
    first.receipt.environment_manifest_digest);
  assert.equal(environment.receipt.fixture_set_digest, first.receipt.fixture_set_digest);

  // (e) THE FIXTURE-SET DIGEST IS OVER THE SEALED FIXTURE BYTES, so one byte of
  // one sealed fixture moves it and moves nothing else.
  const fixtures = await emitFrom({ fixtureEdit: "one byte of the sealed fixture set" });
  assert.notEqual(fixtures.receipt.fixture_set_digest, first.receipt.fixture_set_digest);
  assert.equal(fixtures.receipt.candidate_digest, first.receipt.candidate_digest);
  assert.equal(fixtures.receipt.environment_manifest_digest,
    first.receipt.environment_manifest_digest);

  // (f) AND NONE OF THE FIVE IS A HASH OF ITS OWN NAME. The refutation the
  // review asked for, stated as an assertion rather than as a comment.
  for (const [field, described] of [
    ["candidate_digest", { head_revision: REVISION_ALL_SUCCEED,
      declared_checks: ["local-db-ci --class migration",
        "main canary", "ops/ci.sh --strict"] }],
    ["environment_manifest_digest", { tenant: "carr-internal",
      subject_environment: "candidate", evidence_scope: "candidate-and-test" }],
    ["fixture_set_digest", { service_key: "gate-zero-canary" }],
  ])
    assert.notEqual(first.receipt[field], digest(described),
      `${field} is a digest of a description of the artifact rather than of the artifact`);
});

// ===========================================================================
// CONTROL 13 — THE STAMPS, AND THE PRODUCTION CONDITION THEY EXIST FOR.
//
// Amendment 9's first clause, proved in four parts: a receipt is produced with
// NO `.git` anywhere (which every case above has already been doing, and which
// stageTree asserts); a missing stamp is refused BY NAME; a stamp that does not
// agree with its own digest is refused; and a git read put back into the
// producer turns the produced cases red, which is what makes the first part
// mean something.
// ===========================================================================

test("STAMPS: a receipt is produced with no .git anywhere, and a git read put back is red",
  async () => {
    const produced = await emitFrom({});
    assert.equal(produced.passable, true, produced.unavailable_because ?? produced.reason_id);
    assert.equal(produced.receipt.candidate_digest.startsWith("sha256:"), true);

    // AND THE PRODUCER HOLDS NO REPOSITORY READ AT ALL, asked of the source: no
    // filesystem import, no zlib, no walk. A module that cannot name these
    // cannot fall back to them.
    const source = readFileSync(join(SRC, PRODUCER_FILE), "utf8");
    for (const forbidden of ['"node:fs"', '"node:zlib"', '"node:path"', "readFileSync(",
      "statSync(", "inflateSync(", 'join(root'])
      assert.equal(source.includes(forbidden), false,
        `${forbidden} is a request-time repository read in the production producer`);

    // THE MUTATION CONTROL. One anchored edit puts a `.git` read back in front of
    // the stamp read. In a staged tree there is no repository, so the read throws,
    // the gate's guarded boundary answers its own refusal, and the case above
    // goes red — which is the whole evidence that the no-git run is a run and not
    // a tautology.
    const regressed = await emitFrom({ reintroduceGitDerivation: true });
    assert.equal(regressed.passable, false,
      "a git-derived candidate path still produced a receipt with no repository present");
  });

test("STAMPS: a stamp this deploy does not carry is refused by name", async () => {
  for (const [name, absent] of [
    [BUILD_STAMP_NAMES.gitSha, "GIT_SHA"],
    [BUILD_STAMP_NAMES.candidateManifest, "CANDIDATE_MANIFEST"],
    [BUILD_STAMP_NAMES.candidateManifestDigest, "CANDIDATE_MANIFEST_DIGEST"],
  ]) {
    const emitted = await emitFrom({ dropStamp: name });
    assert.equal(emitted.passable, false, name);
    assert.equal(emitted.reason_id, "gate_zero_candidate_metadata_absent", name);
    assert.deepEqual(emitted.producer_answer.absent_candidate_metadata, [absent]);
    assert.match(emitted.producer_answer.unavailable_because, new RegExp(absent));
    assert.equal(emitted.producer_answer.receipt, null);
    assert.equal(emitted.producer_answer.clauses, null);
  }
});

test("STAMPS: a manifest edited after its seal does not match its digest and is refused",
  async () => {
    // THE TWO STAMPS CHECK EACH OTHER. `stampsFor` digests the manifest AS
    // SEALED and then writes the rewritten one into the var, which is exactly
    // what a hand-edited Worker var looks like from inside the Worker.
    const tampered = await emitFrom({
      rewriteManifest: manifest => ({ ...manifest, byte_length: manifest.byte_length + 1 }) });
    assert.equal(tampered.passable, false);
    assert.equal(tampered.reason_id, "gate_zero_candidate_metadata_absent");
    assert.deepEqual(tampered.producer_answer.absent_candidate_metadata,
      ["CANDIDATE_MANIFEST_DIGEST:does_not_cover_the_stamped_manifest"]);

    // A MANIFEST FOR ANOTHER REVISION IS REFUSED TOO, by its own field name —
    // the sha and the manifest cannot drift apart.
    const mismatched = await emitFrom({
      coverRewrittenManifest: true,
      rewriteManifest: manifest => ({ ...manifest, git_sha: REVISION_UNFINISHED }) });
    assert.equal(mismatched.passable, false);
    assert.equal(mismatched.reason_id, "gate_zero_candidate_metadata_absent");
    assert.deepEqual(mismatched.producer_answer.absent_candidate_metadata,
      ["CANDIDATE_MANIFEST:git_sha"]);

    // AND SO IS A MANIFEST MISSING ONE OF THE SEALED DIGESTS. The environment
    // manifest deleted from the tree is no longer "a file that is not on disk" —
    // it is a field the wrapper could not seal, and it is named as one.
    const unsealed = await emitFrom({ withoutEnvironmentManifest: true,
      coverRewrittenManifest: true, rewriteManifest: manifest => manifest });
    assert.equal(unsealed.passable, false);
    assert.equal(unsealed.reason_id, "gate_zero_candidate_metadata_absent");
    assert.deepEqual(unsealed.producer_answer.absent_candidate_metadata,
      ["CANDIDATE_MANIFEST:environment_manifest_digest"]);

    // NOT PARSING AT ALL is its own name rather than a field name. The stamps
    // are written directly here because a manifest that is not JSON is not
    // something the sealer could produce — it is a var somebody wrote by hand.
    const { target, stamps } = stageTree({});
    const gate = await moduleOfTree(target, GATE_FILE);
    const unparsed = await withStamps(
      { ...stamps, [BUILD_STAMP_NAMES.candidateManifest]: "not-json-at-all" },
      async () => (await inServedRequest(target, {})).answered);
    assert.equal(unparsed.passable, false);
    assert.equal(unparsed.reason_id, "gate_zero_candidate_metadata_absent");
    assert.deepEqual(unparsed.producer_answer.absent_candidate_metadata,
      ["CANDIDATE_MANIFEST:not_json"]);
  });

// ===========================================================================
// CONTROL 3 — each clause turned off, one at a time.
// ===========================================================================

/**
 * ONE ROW BROKEN PER CASE, and each breaks the row of exactly one clause. The
 * rows behind the other two are untouched, so a clause that stopped being read
 * fails on one case rather than on none — which is the property a conjunction
 * over three booleans would not have.
 *
 * THE FAULT MOVED FROM THE BINDING TO THE STORE, and that is the correction:
 * the producer derives every address now, so there is no address a test can
 * write a bad value into. A broken world is a broken ROW.
 */
const CLAUSE_FALSIFIERS = Object.freeze([
  {
    name: "the predecessor clause, with the receipt and the card disagreeing",
    options: { predecessorWorld: "receipt-card-mismatch" },
    reason: "gate_zero_predecessor_clause_failed",
    clause: "predecessor_join",
  },
  {
    name: "the scheduler clause, with a canary bound to no receipt of its own",
    options: { ledgerCanary: CANARY_UNBOUND },
    reason: "gate_zero_scheduler_clause_failed",
    clause: "scheduler_canary",
  },
  {
    name: "the gate-graph clause, with a green gate over a failed ancestor",
    options: { revision: REVISION_FAILED_ANCESTOR },
    reason: "gate_zero_gate_graph_clause_failed",
    clause: "gate_graph",
  },
]);

test("CLAUSES: each of the three, turned off on its own, stops the gate passing", async () => {
  for (const falsifier of CLAUSE_FALSIFIERS) {
    const emitted = await emitFrom(falsifier.options);
    assert.equal(emitted.passable, false, falsifier.name);
    assert.equal(emitted.reason_id, falsifier.reason, falsifier.name);
    assert.equal(emitted.join[falsifier.clause].state, "failed", falsifier.name);
    // AND THE OTHER TWO STILL HELD, which is what makes it one clause and not
    // three failing together for an unrelated reason.
    for (const [name, clause] of Object.entries(emitted.join))
      if (name !== falsifier.clause)
        assert.equal(clause.state, "held", `${falsifier.name}: ${name} also failed`);
  }
  // THE CONTROL IS A CONTROL: the unmodified tree, staged the same way through
  // the same machinery, passes. Without this the three cases above would pass
  // just as well against a staging step that broke the file.
  assert.equal((await emitFrom({})).passable, true,
    "the staging itself breaks the run, so the falsifiers prove nothing");
});

test("TAMPER: an acceptance receipt the card does not carry is refused, and never echoed", async () => {
  // The receipt row signed one hash and the card carries another. Card 11 reads
  // the acceptance receipt BY THE STEP REF now — no hash is looked up and pasted
  // by anybody — and a store whose two sides disagree joins nothing.
  const emitted = await emitFrom({ predecessorWorld: "receipt-card-mismatch" });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_predecessor_clause_failed");
  const clause = emitted.join.predecessor_join;
  assert.deepEqual([...clause.detail.failed].sort(), [
    "step:wr40-repository-outcome",
    "step:wr46-dissolution-outcome",
    "step:wr54-backup-recovery-outcome",
  ]);
  // AND NO HASH TRAVELS. The reader compares and never echoes, which is as true
  // of a derived hash as it was of a supplied one.
  assert.equal(JSON.stringify(emitted).includes(FORGED_HASH.slice("sha256:".length)), false,
    "an acceptance hash came back out of the answer");
});

// ===========================================================================
// CONTROL 4 — the seat, and the three rulings.
// ===========================================================================

test("SEAT: putting the holder back to null takes the whole slice dark", async () => {
  // The producer module is in this tree, unedited, with a fully pasted run
  // binding and every row present. The seam is shut anyway, because the only
  // thing that opens it is card 9's declaration.
  const emitted = await emitFrom({ staffedSeat: false });
  assert.equal(emitted.producer_bound, false);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.oracle_seat_bound, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_seam_unavailable");
  assert.equal(emitted.producer_answer, null, "an unstaffed seat still reached the producer");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.observed_at, null);
  assert.deepEqual(emitted.owed_seams, ["seam:gate-zero-read-only-outcome-producer"]);
  assert.deepEqual([...emitted.undecided_governance_questions],
    ["which independent seat holds oracle:gate-producer:gate-zero-read-only"]);
});

test("READERS: withdrawing any one ruling leaves the producer with nothing to read", async () => {
  for (const [index, card] of ["card:11", "card:12", "card:13"].entries()) {
    const emitted = await emitFrom({ withdrawnCards: [index] });
    assert.equal(emitted.passable, false, card);
    // The producer is bound and was reached — it is the EVIDENCE that is gone,
    // and the reason says so rather than blaming the seam.
    assert.equal(emitted.producer_bound, true, card);
    assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable", card);
    assert.equal(emitted.outcome_digest, null, card);
    // A withdrawn ruling is not a failed clause: nothing was read, so nothing
    // failed, and the negative admission is not even attempted over it.
    assert.equal(emitted.producer_answer.negative_admission, null, card);
  }
});

test("EVIDENCE: a canary row that does not exist is unanswered, not failed", async () => {
  const emitted = await emitFrom({ ledgerCanary: CANARY_ABSENT });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable");
  assert.equal(emitted.join, null, "an outcome was emitted over a row that does not exist");
  assert.equal(emitted.producer_answer.clauses.scheduler_canary.state, "unknown");
});

test("EVIDENCE: a check with no conclusion yet is unanswered, not a failure", async () => {
  const emitted = await emitFrom({ revision: REVISION_UNFINISHED });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable");
  assert.equal(emitted.producer_answer.clauses.gate_graph.state, "unknown");
  assert.deepEqual(emitted.producer_answer.clauses.gate_graph.detail.not_answered,
    ["local-db-ci-migration"]);
});

// ===========================================================================
// CONTROL 5 — Q036.D1's own falsifier.
// ===========================================================================

test("PROPAGATION: an injected failure produces a FAILING outcome, kept and digested", async () => {
  // The top gate reports success and its ancestor did not. A clause that
  // conjoined self-conclusions would read this graph as green, which is the
  // false-green result Q036.D1 names as a failure of the gate itself.
  const emitted = await emitFrom({ revision: REVISION_FAILED_ANCESTOR });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_gate_graph_clause_failed");

  // AND IT IS AN OUTCOME, NOT AN ABSENCE. The retry policy this registration
  // carries says retryable, every run kept, failed runs retained — so a run that
  // read everything and found a failed gate has a receipt, a digest and an
  // instant, and its receipt says so in r7's own word.
  assert.equal(emitted.status, "outcome_produced");
  assert.equal(emitted.receipt_status, "fail");
  assert.equal(emitted.receipt.status, "fail");
  assert.match(emitted.outcome_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(emitted.outcome_digest,
    digest([CONSUMER_GATE_RECEIPT_SCHEMA, emitted.receipt]));
  assert.match(emitted.observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);

  // AND THE DENIALS WERE OBSERVED, which is the field's whole claim.
  assert.equal(emitted.receipt.negative_admission_result, "all_required_denials_observed");
  assert.deepEqual(emitted.negative_admission.missed_cases, []);
  assert.equal(emitted.negative_admission.observed_codes.length, 7);
  assert.ok(emitted.negative_admission.observed_codes
    .includes("gate_reporting_success_over_a_failed_ancestor"));

  // THE PROPAGATION ITSELF: the top gate reported success and is still refused.
  const clause = emitted.join.gate_graph;
  assert.deepEqual(clause.detail.conclusion_not_success, ["ops-ci-strict"]);
  assert.deepEqual(clause.detail.inherited_not_success, ["main-canary"]);
});

test("PROPAGATION: the required denials are re-derived, not asserted", async () => {
  const emitted = await emitFrom({});
  // Seven cases, and every one of them is a mutation of THIS run's readings put
  // back through this module's own live clauses. The mutation control for the
  // prover is the prover itself: it reports which cases it observed, and a case
  // that stopped firing lands in `missed_cases` and refuses the whole run.
  assert.deepEqual(emitted.negative_admission.observed_codes, [
    "gate_conclusion_not_success",
    "gate_reporting_success_over_a_failed_ancestor",
    "one_outcome_record_closing_two_predecessors",
    "predecessor_acceptance_receipt_hash_mismatch",
    "predecessor_not_accepted",
    "scheduler_canary_not_bound_to_its_receipt",
    "scheduler_observation_not_after_dispatch",
  ]);
});

// ===========================================================================
// CONTROL 8 — self-review.
// ===========================================================================

test("IDENTITY: a subject maker that is the reviewing seat is denied", async () => {
  // r7's rule denies same-seat self-review, and the guard has to exist whether
  // or not today's rows can reach it: the subject maker is a registered PARTNER
  // out of the release record and the producer is a review-council machine seat,
  // so the ACTORS cannot collide in the shipped configuration. The SESSIONS can,
  // and that is the half this case reaches — a release row whose correlation is
  // the correlation of the call now reviewing it. Two seats collide when they
  // share EITHER the actor or the session, so the guard is PROVED live rather
  // than assumed unreachable.
  const emitted = await emitFrom({ candidateRecordWorld: "same-session" });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.producer_answer.clauses, null,
    "a self-review reached the clauses before it was denied");
  assert.equal(emitted.producer_answer.receipt, null);
});

// ===========================================================================
// THE SUBJECT MAKER, AND THE RECORD IT COMES OUT OF (amendment 9(b)).
// ===========================================================================

test("PROVENANCE: the store admits only the row the database itself named a maker on",
  () => {
    // THE SIXTH REVIEW ROUND'S SECOND FINDING, asserted where the query is
    // written. The producer's subject maker used to be whatever `--maker` the
    // person running `tools/ops-record.py release candidate` typed, read back by
    // git_sha alone — caller-asserted metadata wearing a record's clothes.
    //
    // TWO THINGS CHANGED AND BOTH ARE CHECKED HERE. The recorder refuses --maker
    // and --maker-verification outright and asserts neither column, and migration
    // 0504 writes the pair itself from the login role the session authenticated
    // as. A row somebody typed a maker into cannot satisfy the store's pair, so it
    // is not a row this producer can read.
    const storeSource = readFileSync(join(SRC, STORES_FILE), "utf8");
    const recorder = readFileSync(join(REPO, "tools", "ops-record.py"), "utf8");
    const migration = readFileSync(
      join(REPO, "migrations", "0504_release_maker_session_provenance.sql"), "utf8");
    const environmentIdentity = readFileSync(
      join(REPO, "migrations", "0513_release_candidate_environment_identity.sql"), "utf8");
    const candidateQuery = storeSource.slice(storeSource.indexOf("from ops.release r"));
    assert.match(candidateQuery, /and r\.source_kind = 'wrapper'/,
      "the candidate store no longer requires the one writer's own source kind");
    assert.match(candidateQuery,
      /and r\.maker_verification_ref = '\$\{MAKER_AUTHORITY_PREFIX\}' \|\| r\.maker_actor/,
      "the candidate store reads a maker without requiring the authenticated derivation");
    // And the marker that predicate stands on is the spelling the DATABASE writes,
    // asserted as a literal on both sides so the two files cannot drift apart in
    // silence — a prefix that moved on one side would return every row or none.
    // The pairing moved off the recorder deliberately: the recorder no longer
    // writes either column, so it is no longer a party to the spelling.
    assert.match(storeSource,
      /const MAKER_AUTHORITY_PREFIX = "ops\.authority-principal:";/,
      "the store's authenticated-maker marker is not the derivation's spelling");
    assert.match(migration,
      /new\.maker_verification_ref := 'ops\.authority-principal:' \|\| v_slug;/,
      "the migration's derived marker is not the store's spelling");

    // AND THE COLUMN THE STORE KEYS ON IS ONE NO ROLE CAN WRITE, which is what
    // makes the two text columns above believable rather than merely conventional.
    assert.match(candidateQuery, /and r\.maker_authority_verified/,
      "the candidate store trusts the two text columns without the database's own mark");
    assert.match(candidateQuery, /and r\.environment = 'production'/,
      "the candidate store can confuse staging history with its Production subject");

    // Current release candidates are filed by carr_jobs and carry truthful
    // service provenance. They cannot supply Gate Zero's historical human
    // subject maker. The ruled reader still accepts exact older authority-filed
    // rows and refuses service rows; the recorder never accepts a caller maker.
    assert.equal(recorder.includes("ops.record_release_candidate("), false,
      "the recorder still files the candidate through the withdrawn authority door");
    assert.match(recorder, /CANDIDATE_INSERT = "{3}\n\s*insert into ops\.release/,
      "the recorder no longer files the candidate with one named insert");
    assert.match(recorder,
      /connection_kind = \("authority"\s*\n\s*if args\.action in \("approve", "staging-approve"\)\s*\n\s*else "routine" if args\.action in \("candidate", "ready", "reopen"\)\s*\n\s*else "write"\)/,
      "service candidates must file under carr_jobs while historical approvals retain Joe authority");
    assert.match(recorder,
      /the release candidate's maker is not a caller field/,
      "the recorder no longer refuses a caller-asserted maker");
    assert.equal(recorder.includes("args.maker, args.maker_verification"), false,
      "the candidate insert still reads the caller's maker flags");
    assert.match(recorder,
      /returning id, release_key, maker_actor, maker_session_user,\n\s*maker_authority_verified/,
      "the recorder no longer reads the row's recorded provenance back");

    // AND THE DATABASE IS WHERE THE MARK IS DEFENDED, not the wrapper. Four
    // clauses, each asserted against the migration that installs them: the filing
    // login is recorded from session_user and not from a caller field, the column
    // the store keys on is GENERATED and therefore unwritable by any role, a
    // session that is not a human authority is refused the marker in words, and
    // one revision may carry one authority-filed row.
    assert.match(migration, /new\.maker_session_user := session_user;/,
      "the filing login is no longer recorded from the session's own credential");
    assert.match(migration,
      /add column if not exists maker_authority_verified boolean\s*\n\s*generated always as \(ops\.authority_login_slug\(maker_session_user\) is not null\)\s*\n\s*stored;/,
      "the authenticated-maker column is writable by somebody");
    assert.match(migration,
      /the authority-derivation marker is written by the '\s*\n?\s*'database from session_user, never by a caller/,
      "a typed derivation marker is no longer refused in words");
    assert.match(migration,
      /create unique index if not exists release_authority_candidate_sha_uniq\s*\n\s*on ops\.release \(git_sha\) where maker_authority_verified;/,
      "0504's original global authority uniqueness changed instead of receiving a forward migration");
    assert.match(environmentIdentity,
      /drop index ops\.release_authority_candidate_sha_uniq;/,
      "the forward repair does not replace 0504's global index");
    assert.match(environmentIdentity,
      /create unique index release_authority_candidate_sha_uniq\s*\n\s*on ops\.release \(git_sha\)\s*\n\s*where maker_authority_verified and environment = 'production';/,
      "authority-filed Production candidates are not unique independently of staging history");

    // AND THE ONE LINE THIS MIGRATION DOES NOT CARRY IS A DECISION, asserted so it
    // cannot become drift. Neither spelling of the capability is here: not the
    // withdrawn door's EXECUTE grant and not `grant insert on ops.release to
    // carr_authority`, which is what "file the candidate on the authority
    // connection" would actually require. Measured on a disposable PostgreSQL: the
    // column, the generated column, the trigger and the index move every census
    // category by zero rows; either grant moves the sealed projection and
    // ops/siep11-, siep12- and siep18-*-local-pg-gate.py refuse it against the v25
    // seal. The forgery is closed either way — nothing can mint the marker — so
    // what waits on the successor is writing a GENUINE row, not trusting a forged
    // one, and open loop #594 carries it.
    const grantOutsideAComment = migration
      .split("\n")
      .filter(line => !line.trimStart().startsWith("--"))
      .join("\n");
    assert.equal(/grant\s+insert[^;]*on\s+table\s+ops\.release[^;]*to\s+carr_authority/i
      .test(grantOutsideAComment), false,
      "0504 grants the authority role a table capability no successor admitted");
    assert.equal(/grant\s+execute\s+on\s+function\s+ops\.record_release_candidate/i
      .test(grantOutsideAComment), false,
      "0504 grants the withdrawn door's capability");
    assert.match(migration,
      /db-relation-acl:ops\.release:carr_authority:insert/,
      "0504 does not name the exact ingress key its successor has to admit");
    assert.match(migration, /open loop #594/,
      "0504 does not point at the record that carries the deferred admission");
  });

test("ABSENCE: a revision with no AUTHENTICATED release candidate on record IS the absent ruled row",
  async () => {
    // A build nobody filed a release candidate for — and, since the store admits
    // only the authenticated derivation (the case above), a build whose only row
    // carries a maker somebody typed is the SAME case: no row comes back, so
    // there is no maker. The stamps are intact and every clause row is present;
    // what is genuinely absent is the RECORD naming who made the candidate. This
    // is the one case `gate_zero_run_binding_unnamed` is left with, and it says
    // which.
    const emitted = await emitFrom({ candidateRecordWorld: "absent" });
    assert.equal(emitted.passable, false);
    assert.equal(emitted.reason_id, "gate_zero_run_binding_unnamed");
    assert.equal(emitted.producer_answer.clauses, null);
    assert.deepEqual(emitted.producer_answer.unnamed_bindings, ["subject_maker"]);
  });

test("ABSENCE: release rows that disagree about the maker name nobody", async () => {
  // TWO ROWS, TWO MAKERS. A receipt that named one of them would be PICKING, and
  // an oracle that picks its own subject maker is not reading a record. Both
  // rows are well-formed and both makers are registered partners, so what
  // refuses is the disagreement itself.
  const emitted = await emitFrom({ candidateRecordWorld: "ambiguous" });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_run_binding_unnamed");
  assert.deepEqual(emitted.producer_answer.unnamed_bindings, ["subject_maker"]);
});

test("ABSENCE: a maker this system does not register as a partner names nobody", async () => {
  // A slug-shaped maker the store lets through — it passes the store's own
  // pattern — and which identity.js's partner registry does not know. The widest
  // value this path can carry into a receipt is a registered partner's name, so
  // the row is read and the principal is still absent.
  const emitted = await emitFrom({ candidateRecordWorld: "unregistered-maker" });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_run_binding_unnamed");
  assert.deepEqual(emitted.producer_answer.unnamed_bindings, ["subject_maker"]);
  assert.equal(JSON.stringify(emitted).includes("carr-release-bot"), false,
    "an unregistered maker slug came back out of the answer");
});

// ===========================================================================
// CONTROL 7 — no caller-supplied anything, by parse and by call.
// ===========================================================================

test("SURFACE: the producer's export list is exactly four constants and one callable", () => {
  assert.deepEqual(Object.keys(producer).sort(), [
    "V5_A02_GATE_ZERO_CLAUSE_STATES",
    "V5_A02_GATE_ZERO_PRODUCER_REASON_IDS",
    "V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION",
    "V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE",
    "v5A02GateZeroEmitOutcome",
  ]);
  // EXACTLY ONE export is callable, and its arity is zero. An exported builder
  // is the one shape that can be handed caller-supplied references — the PR 990
  // defect — and a function with no parameters has nowhere to put one.
  const callable = Object.entries(producer)
    .filter(([, value]) => typeof value === "function").map(([name]) => name);
  assert.deepEqual(callable, ["v5A02GateZeroEmitOutcome"]);
  assert.equal(producer.v5A02GateZeroEmitOutcome.length, 0);
  // No binding door, no setter, no registry, under any spelling — asked of what
  // could BE one. A constant named for the run binding's state is a fact a
  // reader can check; only a callable can be handed something.
  for (const [name, value] of Object.entries(producer))
    if (typeof value === "function")
      assert.equal(/bind|set|register|configure|inject/i.test(name), false,
        `${name} is a binding door on the producer surface`);
  // And nothing exported is writable or extensible, so a door cannot be added
  // to the namespace after the fact either.
  for (const [name, value] of Object.entries(producer))
    if (value !== null && typeof value === "object")
      assert.ok(Object.isFrozen(value), `${name} is not frozen`);
});

test("SURFACE: handing the producer an argument is a contract violation, thrown synchronously", () => {
  for (const argument of [
    { emitOutcome: () => ({ passable: true }) },
    { outcomeHash: `sha256:${"a".repeat(64)}` },
    { headSha: REVISION_ALL_SUCCEED },
    "allow", 1, true, null, undefined, [],
  ])
    assert.throws(() => producer.v5A02GateZeroEmitOutcome(argument),
      error => error instanceof V5BoundaryError
        && error.code === "gate_zero_producer_takes_no_argument",
      JSON.stringify(argument ?? null));
  // TWO arguments too, and the throw is synchronous rather than a rejection: a
  // caller reading a refusal never has to catch one, and a contract violation is
  // refused before anything is awaited.
  assert.throws(() => producer.v5A02GateZeroEmitOutcome(1, 2), V5BoundaryError);
});

test("SHAPE: the one callable wears amendment 2's closed shape", () => {
  const fn = producer.v5A02GateZeroEmitOutcome;
  assert.equal(Object.hasOwn(fn, "prototype"), false, "carries a prototype");
  assert.throws(() => Reflect.construct(fn, []), TypeError);
  assert.ok(Object.isFrozen(fn), "is not frozen");
  assert.equal(types.isProxy(fn), false, "is a Proxy");
  const descriptor = Object.getOwnPropertyDescriptor(fn, Symbol.hasInstance);
  assert.ok(descriptor !== undefined, "has no own Symbol.hasInstance");
  assert.equal(descriptor.writable, false);
  assert.equal(descriptor.configurable, false);
  assert.equal(descriptor.enumerable, false);
  // The operand is never read: every trap on it throws, and `instanceof` is
  // still false rather than the caller's own error.
  const hostile = new Proxy({}, {
    get() { throw new Error("the operand was read"); },
    getPrototypeOf() { throw new Error("the operand's chain was walked"); },
  });
  assert.equal(hostile instanceof fn, false);
});

/**
 * AMENDMENT 2'S SHAPE, ENUMERATED OVER EVERY MODULE THIS PR ADDED A CALLABLE TO.
 *
 * The second review round's standards finding: the new exports were plain
 * function declarations — a prototype, constructable, and `instanceof` answered
 * by walking the left operand's chain.
 *
 * THE ENUMERATION IS DYNAMIC, which the third round's third finding was about:
 * the list used to be written out by hand here, so the claim "any future export
 * arrives holding this shape or arrives red" was true of nothing. identity.js's
 * whole new surface is now ONE frozen namespace export, and the test walks
 * `Object.keys` of it — a seventh member added to that object is checked without
 * anyone remembering to add a name here. The file's OLDER doors are deliberately
 * out of scope: they predate the amendment and are not this PR's changed
 * surface. The testhelper is enumerated whole, because every callable in it is
 * new.
 */
function assertClosedShape(fn, where) {
  assert.equal(typeof fn, "function", `${where} is not callable`);
  assert.equal(Object.hasOwn(fn, "prototype"), false, `${where} carries a prototype`);
  assert.throws(() => Reflect.construct(fn, []), TypeError, `${where} is constructable`);
  assert.ok(Object.isFrozen(fn), `${where} is not frozen`);
  assert.equal(types.isProxy(fn), false, `${where} is a Proxy`);
  const descriptor = Object.getOwnPropertyDescriptor(fn, Symbol.hasInstance);
  assert.ok(descriptor !== undefined, `${where} has no own Symbol.hasInstance`);
  assert.ok(Object.hasOwn(descriptor, "value"), `${where}'s Symbol.hasInstance is an accessor`);
  assert.equal(descriptor.writable, false, where);
  assert.equal(descriptor.configurable, false, where);
  assert.equal(descriptor.enumerable, false, where);
  const hostile = new Proxy({}, {
    get() { throw new Error("the operand was read"); },
    getPrototypeOf() { throw new Error("the operand's chain was walked"); },
  });
  assert.equal(hostile instanceof fn, false, where);
}

test("SHAPE: every callable this PR added to identity.js and the walker wears amendment 2's shape", async () => {
  const identity = await import("../src/identity.js");
  const surface = identity.authenticatedIdentity;
  assert.ok(Object.isFrozen(surface), "identity.js's new surface is not frozen");
  // ENUMERATED, NOT LISTED. Every member of the namespace is checked, whatever
  // it is called and however many there are.
  const added = Object.keys(surface);
  assert.deepEqual(added.sort(),
    ["connectionForGrant", "partnerIdentity", "receiptIdentity"],
    "the authenticated-identity surface grew or lost a member");
  for (const name of added) assertClosedShape(surface[name], `identity.js#authenticatedIdentity.${name}`);
  assert.deepEqual(added.filter(name => typeof surface[name] !== "function"), []);
  // AND THE ONE ENTRY ONTO THE AUTHENTICATED CALL WEARS THE SHAPE TOO. It is a
  // top-level export rather than a member of the namespace above, because that
  // namespace is a set of READERS and this is not one.
  for (const name of ["serveAuthenticatedCall", "serveReviewRequest"])
    assertClosedShape(identity[name], `identity.js#${name}`);

  // AND THE NAME SPACE THE CONTEXT ENTRY WILL RESOLVE IS DECLARED AND FROZEN —
  // the whole point of naming rather than passing code. A caller can express one
  // of these and nothing else.
  assert.deepEqual(identity.SERVER_ENTRY_NAMES, [GATE_ZERO_ENTRY]);
  assert.ok(Object.isFrozen(identity.SERVER_ENTRY_NAMES));

  // AND THE SURFACE IS ALL OF IT — asserted DYNAMICALLY against the file's own
  // exports, which is finding 4 of the third re-review. The old line only said
  // that every member of the namespace was callable; it said nothing about a new
  // top-level callable sitting NEXT to the namespace, which would have slipped
  // past the whole enumeration while the comment claimed otherwise. identity.js's
  // top-level callables are exactly the pre-amendment doors listed here, so any
  // callable added to this file arrives either inside the namespace above (where
  // it is shape-checked) or in this diff (where it is red).
  assert.deepEqual(
    Object.keys(identity).filter(name => typeof identity[name] === "function").sort(),
    ["agentActorForToken", "agentSlugForClient", "authorizationClassForActor",
     "continuityActorForTokenMaps", "hermesActorForToken", "hermesActorForTokenMaps",
     "hermesCosActorForToken", "isKnownActor", "isKnownPartner",
     "organizationTenantForActor", "permittedActionOwnerSlugs", "personalScopeForActor",
     "propsForSlug", "serveAuthenticatedCall", "serveReviewRequest", "slugForEmail",
     "verifiedAgentSlugForClient"],
    "identity.js grew or lost a top-level callable export");

  const walker = await import("./gate-zero-reachability-walk.testhelper.mjs");
  const callables = Object.entries(walker).filter(([, value]) => typeof value === "function");
  assert.deepEqual(callables.map(([name]) => name).sort(),
    ["pathToValue", "reachableCallables", "topLevelIdentityOnly"]);
  for (const [name, value] of callables)
    assertClosedShape(value, `gate-zero-reachability-walk.testhelper.mjs#${name}`);

  // AND THE WALKER STILL WALKS — a shape assertion over a callable that stopped
  // working would pass every line above.
  assert.equal(walker.pathToValue({ outer: { inner: walker } }, walker), ".outer.inner");
});

/**
 * THE REACHABILITY GUARD, in the shape PR 1004's amendment 6 requires: a closed
 * set, parsed rather than grepped, and asserted to be exactly the modules that
 * may name this file. A producer that some other module could import and drive
 * is a producer with a second caller, and a second caller is an argument wearing
 * an import's clothes.
 */
function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync, statSync } = require("node:fs");
    const { join } = require("node:path");
    const vm = require("node:vm");
    const out = {};
    const walk = (dir, prefix) => {
      for (const name of readdirSync(dir).sort()) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) { walk(full, prefix + name + "/"); continue; }
        if (!name.endsWith(".js")) continue;
        const source = readFileSync(full, "utf8");
        out[prefix + name] =
          new vm.SourceTextModule(source, { identifier: name }).dependencySpecifiers;
      }
    };
    walk(process.argv[1], "");
    process.stdout.write(JSON.stringify(out));
  `;
  const run = spawnSync(process.execPath, ["--experimental-vm-modules", "-e", script, directory],
    { encoding: "utf8" });
  assert.equal(run.status, 0, `the module parser failed: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

test("REACHABILITY: exactly one module in src may import the producer, and none re-exports it", () => {
  const imports = moduleImports(SRC);
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");
  assert.ok(Object.hasOwn(imports, PRODUCER_FILE), "the parser did not see the producer at all");

  const importers = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one => one.endsWith(`/${PRODUCER_FILE}`)))
    .map(([name]) => name).sort();
  assert.deepEqual(importers, [GATE_FILE],
    "a module other than the gate can reach the producer");

  // The producer reaches no test file, by the same parser.
  for (const specifier of imports[PRODUCER_FILE])
    assert.equal(/\/test\/|\.testonly\.|\.testhelper\.|\.fixture\./.test(specifier), false,
      `the producer imports ${specifier}`);
});

/**
 * AND THE CALLABLE IS NOT REACHABLE FROM ANY PUBLIC NAMESPACE BUT ITS OWN — by
 * the repository's OWN runtime walk, not by a pair of source-text patterns.
 *
 * The first draft proved this with two regexes over the gate's source: one for
 * `export { v5A02GateZeroEmitOutcome ... }` and one for a star re-export of the
 * producer's path. Both are greps wearing a guard's clothes. `export {
 * v5A02GateZeroEmitOutcome as emit }` matches the first only by accident of
 * spelling; `export const api = { emit: v5A02GateZeroEmitOutcome }`, a
 * symbol-keyed property, a prototype, a getter, an accessor FUNCTION and an
 * inherited getter read under the exported child as receiver all match neither.
 *
 * The walk that does answer those was written and corrected over eight rounds
 * for the seam readers' own shared predicate. It is IMPORTED here rather than
 * retyped — gate-zero-reachability-walk.testhelper.mjs is the one copy, and the
 * readers suite's part-by-part mutation controls measure that same code.
 */
test("REACHABILITY: the producer callable is reachable from its own namespace and no other", async () => {
  const callable = producer.v5A02GateZeroEmitOutcome;
  // NON-VACUOUS FIRST: the walk finds it where it IS exported, so a walk that
  // silently answered null for everything could not pass this test.
  assert.equal(pathToValue(producer, callable, WHOLE_WALK), ".v5A02GateZeroEmitOutcome");

  const names = readdirSync(SRC).filter(name => name.endsWith(".js")).sort();
  assert.ok(names.length > 100, "every module in src must have been walked");
  const found = [];
  for (const name of names) {
    if (name === PRODUCER_FILE) continue;
    let namespace;
    try {
      namespace = await import(pathToFileURL(join(SRC, name)).href);
    } catch {
      // A module that cannot be loaded outside the Worker runtime exposes
      // nothing here either; the parser check above already covers its imports.
      continue;
    }
    const route = pathToValue(namespace, callable, WHOLE_WALK);
    if (route !== null) found.push(`${name}${route}`);
  }
  assert.deepEqual(found, [],
    "the producer's callable is reachable from a public namespace that is not its own");
});

test("SURFACE: no environment variable but the declared build stamps moves an answer", () => {
  // WHAT CHANGED, AND WHAT DID NOT (amendment 9, 2026-09-14). This module now
  // reads THREE environment variables, and they are declared, closed and named
  // in build-stamp.js: they carry WHAT THIS DEPLOY IS, stamped by the deploy
  // wrapper at upload time. What they are not, and what nothing else may be, is
  // an ADDRESS: no variable names a row, selects a store, opens a seam, chooses
  // an identity or moves a verdict. So the sweep below is unchanged in kind — a
  // hostile environment against a clean one, in a fresh process, asserting the
  // answers are identical — and the hostile set is asserted DISJOINT from the
  // three declared stamps, so this cannot pass by accidentally testing them.
  const declared = Object.values(BUILD_STAMP_NAMES);
  assert.deepEqual([...declared].sort(),
    ["CANDIDATE_MANIFEST", "CANDIDATE_MANIFEST_DIGEST", "GIT_SHA"]);

  // THE CHILD RUNS INSIDE A SERVED REQUEST, which the old shape did not — and
  // without that this whole case was comparing two identity refusals, since the
  // producer asks who is calling before it reads anything at all. It authenticates
  // through the same one entry index.js uses, over credentials passed identically
  // to every variant below, so the ONLY thing that differs between runs is the
  // environment under test.
  const script = `
    const { pathToFileURL } = require("node:url");
    (async () => {
      const { digest } = await import(pathToFileURL(process.argv[2]).href);
      const identity = await import(pathToFileURL(process.argv[3]).href);
      const answered = await identity.serveAuthenticatedCall(
        "Bearer " + process.env.REVIEW_BEARER, process.env.REVIEW_CORRELATION,
        process.env.REVIEW_ENTRY);
      process.stdout.write(JSON.stringify({ emitted: digest(answered) }));
    })().catch(error => { process.stderr.write(String(error)); process.exit(1); });
  `;
  const served = {
    ...process.env,
    REVIEW_TOKENS: RECORDED_REVIEW_TOKENS,
    REVIEW_BEARER: RECORDED_REVIEW_TOKEN,
    REVIEW_CORRELATION: CORRELATION_ID,
    REVIEW_ENTRY: GATE_ZERO_ENTRY,
  };
  const child = env => spawnSync(process.execPath,
    ["-e", script, join(SRC, GATE_FILE), join(SRC, "artifact-trust.js"), join(SRC, IDENTITY_FILE)],
    { encoding: "utf8", env });
  const hostile = {
    ...served,
    CARR_GATE_ZERO_HEAD_REVISION: REVISION_ALL_SUCCEED,
    CARR_GATE_ZERO_SERVICE_KEY: "gate-zero-canary",
    CARR_GATE_ZERO_CANARY_RUN_KEY: "gate-zero-run-0001",
    CARR_GATE_ZERO_SUBJECT_MAKER: SUBJECT_MAKER_ACTOR,
    CARR_GATE_ZERO_ACCEPTANCE_HASH: `sha256:${"1".repeat(64)}`,
    CARR_GATE_ZERO_RUN_BINDING: "named",
    CARR_GATE_ZERO_ACTOR: REVIEWING_SEAT_ACTOR,
    GATE_ZERO_PASSABLE: "true",
    CANDIDATE_MANIFEST_SCHEMA,
  };
  for (const name of declared)
    assert.equal(Object.hasOwn(hostile, name), false,
      `${name} is a declared build stamp and must not be in the hostile set`);

  const run = child(hostile);
  assert.equal(run.status, 0, `the child failed: ${run.stderr}`);
  const withEnvironment = JSON.parse(run.stdout).emitted;
  const clean = child(served);
  assert.equal(clean.status, 0, `the child failed: ${clean.stderr}`);
  assert.equal(withEnvironment, JSON.parse(clean.stdout).emitted,
    "an environment variable that is not a declared build stamp moved the emitted answer");

  // AND THE DECLARED STAMPS DO REACH THE ANSWER, which is what makes the
  // assertion above a statement about ADDRESSES rather than about a module that
  // reads no environment at all. One stamp supplied is one stamp fewer in the
  // refusal's named list, so the answer moves.
  const stamped = child({ ...served, [BUILD_STAMP_NAMES.gitSha]: REVISION_UNFINISHED });
  assert.equal(stamped.status, 0, `the child failed: ${stamped.stderr}`);
  assert.notEqual(JSON.parse(stamped.stdout).emitted, withEnvironment,
    "the declared build stamps do not reach the answer at all");
});

// ===========================================================================
// The closed vocabularies, and the one place they must agree.
// ===========================================================================

test("REASONS: every reason the producer can answer with is registered by the gate", () => {
  for (const id of producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS)
    assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(id),
      `${id} is a producer refusal the gate cannot express`);
  // EIGHT, not nine: `gate_zero_sealed_artifact_absent` left this list with
  // amendment 9 because the producer can no longer reach it — the sealed files
  // are digested by the deploy wrapper now, so their absence is a missing stamp
  // field. The GATE still registers the id, which is why the loop above still
  // passes over a strict subset.
  assert.equal(producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS.length, 8);
  assert.equal(producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS
    .includes("gate_zero_sealed_artifact_absent"), false);
  assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes("gate_zero_sealed_artifact_absent"));
  // And the source cites no id outside its own closed list: `reason()` raises on
  // an unregistered one, so a citation that is not here cannot be reached at all.
  const source = readFileSync(join(SRC, PRODUCER_FILE), "utf8");
  for (const cited of [...source.matchAll(/(?:refusal|reason)\("([a-z_]+)"/g)].map(m => m[1]))
    assert.ok(producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS.includes(cited), cited);
});

test("VOCABULARY: the producer answers in held/failed/unknown and no conditional mood", () => {
  assert.deepEqual([...producer.V5_A02_GATE_ZERO_CLAUSE_STATES], ["failed", "held", "unknown"]);
  const source = readFileSync(join(SRC, PRODUCER_FILE), "utf8");
  // THE CONDITIONAL MOOD IS GONE FROM PRODUCTION, which is the point of
  // promoting the clauses at all: they are applied to readings taken from ruled
  // stores, so they answer, rather than describing what a caller's shape would
  // have meant if it had been evidence.
  for (const forbidden of ["would_", "_if_authoritative", "is_not_authority",
    "caller_supplied_shapes_not_authority"])
    assert.equal(source.includes(forbidden), false,
      `${forbidden} is a conditional-mood spelling in the production producer`);
});

test("VOCABULARY: no value the producer hands the gate carries a privileged word", async () => {
  // The standing rule's closed union, swept AS EXACT MATCH AND AS SUBSTRING over
  // every key and every string leaf — the same sweep the gate surface is held to,
  // applied here because these values travel into the gate's own answer.
  const PRIVILEGED = Object.freeze([
    "allow", "commit", "prompt", "suppress", "release", "read", "covered",
    "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
    "satisfied", "complete", "admitted", "resumed", "attended", "verified",
    "present", "equivalent", "operational", "active", "green", "joins_exactly",
    "coverage_complete", "favorable",
  ]);
  // The four names main already answers with, which this module inherits from
  // the shape every V5 answer wears rather than inventing.
  // The names this module INHERITS rather than chooses: four field names every
  // V5 answer wears, and three identifiers r7 itself registers. Each carries the
  // union's "read" because Gate Zero is a READ-ONLY gate, and renaming any of
  // them would be renaming r7's own registry entry.
  const INHERITED = new Set([
    "request_read", "caller_evidence_admitted", "model_judgment_admitted",
    "gate_zero_step_ref", "step:gate-zero-read-only-outcome",
    "gate-zero-read-only-accepted", "oracle:gate-producer:gate-zero-read-only",
    "receipt:gate-zero-read-only-outcome",
    // AND THE ONE ADDED ON 2026-09-12, lifted out by its exact value the way
    // `status: "pass"` is rather than exempted by category. `verified_partner`
    // is identity.js's OWN derived authority class for an authenticated human
    // partner; the subject maker's class is now derived there rather than stated
    // as a constant, which is what the second review round required. Renaming it
    // here would be renaming identity.js's authorization vocabulary from inside
    // a receipt, and the word is a principal's class, never a verdict about
    // evidence — which is what the sweep exists to keep this module from
    // claiming.
    "verified_partner",
  ]);
  const found = [];
  const walk = (value, path) => {
    if (Array.isArray(value)) { value.forEach((one, at) => walk(one, `${path}[${at}]`)); return; }
    if (value !== null && typeof value === "object") {
      for (const [key, one] of Object.entries(value)) {
        if (!INHERITED.has(key)) check(key, `${path}.${key} (key)`);
        walk(one, `${path}.${key}`);
      }
      return;
    }
    if (typeof value === "string" && !INHERITED.has(value)) check(value, path);
  };
  const check = (text, where) => {
    const folded = text.toLowerCase();
    for (const word of PRIVILEGED)
      if (folded === word || folded.includes(word)) found.push(`${where}: ${word} in ${text}`);
  };
  // Both answers: the refusal src ships, and a produced outcome over rows.
  walk(await producer.v5A02GateZeroEmitOutcome(), "$");
  const produced = (await emitFrom({})).producer_answer;
  // `status: "pass"` is r7's own required value for a passing consumer-gate
  // receipt and is the one string this module may not rename. It is lifted out
  // by name rather than exempted by category.
  assert.equal(produced.receipt.status, "pass");
  walk({ ...produced, receipt: { ...produced.receipt, status: null } }, "$produced");
  assert.deepEqual(found, []);
  // NON-VACUOUS: the sweep catches what it exists to catch.
  check("ruled_store_readings", "$control");
  assert.equal(found.length, 1, "the sweep did not catch a word it must catch");
});
