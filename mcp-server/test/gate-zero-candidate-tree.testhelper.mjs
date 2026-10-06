// THE CANDIDATE-TREE STAGING, ONE COPY, SHARED (2026-09-14, PR 1014 third
// correction; merged with PR 1013's fifth correction under amendment 9).
//
// WHY IT IS HERE. The producer takes no argument, so the only honest way to ask
// what it answers under a different world is to stage a whole candidate tree and
// read what the module then says. Step B's write verb owes the SAME staging for
// the opposite reason: its test must drive the registered verb through the real
// dispatch path over a receipt the real producer really emitted, never a
// hand-built object, and the only place such a receipt exists is inside one of
// these trees.
//
// IT IS LIFTED, NOT RETYPED - the same discipline, and for the same reason, as
// gate-zero-reachability-walk.testhelper.mjs beside it: a retyped harness is a
// second implementation that passes because it was written from the same
// misunderstanding as the code it checks. Every suite below imports THIS file,
// so a correction to the staging corrects all of the proofs at once. The bytes
// are Step A's own staging, moved rather than rewritten.
//
// WHAT A STAGED TREE HOLDS, AND WHAT IT POINTEDLY DOES NOT (standing-rule
// amendment 9, 2026-09-14). There is NO `.git` - that is the production
// condition, because Cloudflare serves the bundle over a read-only virtual
// filesystem with no repository in it - and `stageTree` asserts its absence
// before it returns. What stands in its place is the pair a deploy really has:
//
//   * mcp-server/src - a copy, with the store module replaced by the fixture,
//     and with card 9's seat declaration or the three `decision_id:` lines
//     edited when a case asks;
//   * mcp-server/test - the sealed fixture bytes the fixture-set digest covers;
//   * ops/config/environments.json - the environment manifest its digest covers;
//   * and the THREE BUILD STAMPS `bin/deploy-worker.sh` writes with
//     `wrangler --var`, computed here over exactly those staged bytes by
//     `sealStagedTree`, so a byte-level control still moves the digest.
//
// IT DECIDES NOTHING. Every function here copies bytes, edits a line it has
// asserted matches exactly once, computes a digest over what it staged, or
// imports a module out of the tree it built. No assertion about Gate Zero lives
// in this file.

import assert from "node:assert/strict";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync,
  statSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, relative, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { BUILD_STAMP_NAMES, CANDIDATE_MANIFEST_SCHEMA } from "../src/build-stamp.js";
import {
  CANDIDATE_BUILD_CORRELATION_ID, CANDIDATE_MAKER_ACTOR, REVISION_ALL_SUCCEED,
} from "./gate-zero-producer-stores.v5.fixture.mjs";

export const TEST_DIR = fileURLToPath(new URL("./", import.meta.url));
export const SRC = fileURLToPath(new URL("../src/", import.meta.url));
export const REPO = fileURLToPath(new URL("../../", import.meta.url));
export const PRODUCER_FILE = "gate-zero-producer.v5.js";
export const REGISTRATION_FILE = "gate-zero-producer-registration.v5.js";
export const RULINGS_FILE = "gate-zero-seam-rulings.v5.js";
export const STORES_FILE = "gate-zero-seam-stores.v5.js";
export const GATE_FILE = "gate-zero-assurance.v5.js";
export const IDENTITY_FILE = "identity.js";
export const TOOLS_FILE = "tools.js";
export const MCP_FILE = "mcp.js";

/**
 * THE ONE LINE A STAGED TREE GAINS, and the smallest one that could work.
 *
 * WHY IT IS NEEDED AT ALL (Step B, 2026-09-13). The authenticated call is
 * entered by identity.js, around THE SERVER'S OWN DISPATCH, which identity.js
 * resolves itself through `await import("./mcp.js")`. A suite therefore cannot
 * be handed the actor and cannot be handed the context: both live behind that
 * import, which is exactly the property amendment 8 exists to create. What a
 * suite CAN do is stage the tree it drives, and the seam goes in the one place
 * that keeps every part under test real -- the bearer match, the seat
 * narrowing and the context entry all run as they ship, and only the JSON-RPC
 * parse and the Neon pool, neither of which this slice is about, are skipped.
 *
 * IT IS OFF UNLESS A CASE TURNS IT ON: the edited line does nothing at all
 * unless the global is a function, so a staged tree behaves exactly like the
 * shipped one for every case that does not install one.
 */
const DISPATCH_ANCHOR = "export async function dispatch(request, env, ctx, actor) {\n";
const DISPATCH_SEAM = "__carrStagedDispatchSeam";
const DISPATCH_SEAMED = DISPATCH_ANCHOR
  + `  if (typeof globalThis[${JSON.stringify(DISPATCH_SEAM)}] === "function")\n`
  + `    return globalThis[${JSON.stringify(DISPATCH_SEAM)}](actor);\n`;
/**
 * THE VERB EVERY CASE IS DISPATCHED THROUGH. Any registered read verb would do —
 * what is under test is that the producer runs inside a real dispatched call —
 * and `loop-board` is chosen because its handler reaches its database client
 * immediately and finishes cleanly over no rows, so the stub client below is a
 * one-line seam into the middle of a real dispatched call.
 */
export const DISPATCHED_VERB = "loop-board";
export const FIXTURE_FILE = "gate-zero-producer-stores.v5.fixture.mjs";

/** The sealed fixture set the receipt's fixture_set_digest covers, by path. */
export const SEALED_FIXTURES = Object.freeze([
  "gate-zero-producer-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.receipt-fixture.mjs",
]);
export const ENVIRONMENT_MANIFEST = ["ops", "config", "environments.json"];

/**
 * THE RECORDED AUTHENTICATED CONTEXT. A bearer and the one-entry map shape the
 * Worker's REVIEW_TOKENS secret holds, which is everything index.js's review
 * door reads — recorded here so the call under test is the call the server
 * makes rather than a literal written out. The token is a fixture string: it
 * authenticates against the map beside it and against nothing else.
 */
export const REVIEWING_SEAT_ACTOR = "codex-reviewer";
export const RECORDED_REVIEW_TOKEN = "gate-zero-recorded-review-bearer-2026-09-12";
export const RECORDED_REVIEW_TOKENS = JSON.stringify({ [REVIEWING_SEAT_ACTOR]: RECORDED_REVIEW_TOKEN });
export const CORRELATION_ID = "3f2a6c18-9b4d-4e7a-8c11-5d0e2f7a6b93";

/**
 * A LEGACY MAP-TAKING DOOR'S RECORDED SECRET, and the OAuth grant door's
 * recorded witness. Both are here because the enumeration control has to show
 * every door refusing a caller's bytes — and, non-vacuously, the same doors
 * still answering for the server's own.
 */
export const RECORDED_AGENT_ACTOR = "codex";
export const RECORDED_AGENT_TOKEN = "gate-zero-recorded-agent-bearer-2026-09-12";
export const RECORDED_AGENT_TOKENS = JSON.stringify({ [RECORDED_AGENT_ACTOR]: RECORDED_AGENT_TOKEN });
export const RECORDED_GRANT_WITNESS = "gate-zero-recorded-oauth-client-secret-2026-09-12";

/**
 * THE SERVER'S OWN SECRET SOURCE, AND THERE IS NO OTHER ONE ANY MORE.
 *
 * identity.js reads its credentials ONCE, at module initialisation, out of the
 * environment the SERVER PROCESS was started with — `process.env`, which is what
 * wrangler populates this Worker's secrets into (nodejs_compat, 2026-07-01
 * compatibility date). There is no function to install a map, so there is no
 * first-caller race to win.
 *
 * SO THE HARNESS BECOMES THE SERVER, the only way a suite now can: it writes the
 * recorded secrets into the test process's environment, HERE, at import, before
 * any staged tree exists. Every `moduleOfTree(target, IDENTITY_FILE)` below is a
 * fresh module instance whose initialisation reads exactly these values, the way
 * a deployed Worker's does.
 *
 * WHETHER THE ROOT `../src/identity.js` SAW THEM IS DELIBERATELY NOT RELIED ON.
 * It depends on which import a suite declares first, which is not a property a
 * proof may stand on, so no case in any suite that imports this file obtains an
 * authenticated call from the root module: every one of them goes through a
 * STAGED identity.js, whose credentials are these and whose module graph is the
 * one the case dispatches through.
 */
process.env.REVIEW_TOKENS = RECORDED_REVIEW_TOKENS;
process.env.AGENT_TOKENS = RECORDED_AGENT_TOKENS;
process.env.GOOGLE_CLIENT_SECRET = RECORDED_GRANT_WITNESS;

/** The maker and session the release-candidate record names, from the fixture. */
export const SUBJECT_MAKER_ACTOR = CANDIDATE_MAKER_ACTOR;
export const SUBJECT_MAKER_SESSION = `session:${CANDIDATE_BUILD_CORRELATION_ID}`;

/**
 * THE STAGED TREES THIS PROCESS BUILT, and the one way to remove them. Each
 * suite registers it with `after(cleanupStagedTrees)`; a suite that forgets
 * leaves trees under node_modules/.cache rather than failing, which is why the
 * registration is one line at the top of each file rather than a hook here.
 */
const staged = [];

export function cleanupStagedTrees() {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
  staged.length = 0;
}

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
const PREDECESSOR_WORLD_COMPARISON =
  'PREDECESSOR_WORLD === "receipt-card-mismatch"';
export const DYNAMIC_PREDECESSOR_WORLD = "__carrGateZeroPredecessorWorld";
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

export function editOnce(path, anchor, replacement, what) {
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
export function sealStagedTree(base, revision) {
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
export function stampsFor(manifest, { drop = null, rewrite = null, coverRewrite = false } = {}) {
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
export async function withStamps(stamps, fn) {
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
export function stageTree({
  revision = REVISION_ALL_SUCCEED, staffedSeat = true,
  withdrawnCards = [], ledgerCanary = null, predecessorWorld = null, candidateRecordWorld = null,
  substituteStore = true, environmentEdit = null, candidateEdit = null,
  fixtureEdit = null, deniedClass = false, reintroduceGitDerivation = false,
  mutablePredecessorWorld = false,
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

  // THE DISPATCH SEAM, on every staged tree and inert on all of them until a case
  // installs the global. See DISPATCH_SEAMED above for why it exists; it is
  // applied HERE, before the seal, so the manifest the stamps describe covers the
  // bytes the case actually runs -- a seam sealed out of the manifest would be a
  // tree whose digest describes a different tree than the one under test.
  editOnce(join(target, MCP_FILE), DISPATCH_ANCHOR, DISPATCH_SEAMED,
    "mcp.js's dispatch entry");

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
    if (mutablePredecessorWorld) {
      assert.equal(predecessorWorld, null,
        "a staged predecessor world cannot be both fixed and mutable");
      editOnce(store, PREDECESSOR_WORLD_LINE,
        `const predecessorWorld = () => globalThis[${JSON.stringify(DYNAMIC_PREDECESSOR_WORLD)}] ?? "clean";\n`,
        "the fixture store's mutable predecessor world line");
      editOnce(store, PREDECESSOR_WORLD_COMPARISON,
        'predecessorWorld() === "receipt-card-mismatch"',
        "the fixture store's mutable predecessor comparison");
    }
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

export const moduleOfTree = (target, file) => import(pathToFileURL(join(target, file)).href);

/**
 * RUN `fn(actor)` INSIDE A REAL SERVED REVIEW REQUEST in a staged tree, and
 * answer what it returned.
 *
 * THIS IS THE ONE DOOR ONTO AN AUTHENTICATED CALL, and since PR 1013's SIXTH
 * correction round it no longer takes one. `serveReviewRequestAuthenticated`
 * took a continuation, and a callback parameter on an exported door is the
 * exported context entry amendment 8 forbids however the identity behind it is
 * derived. It is gone, and this helper does not reach for a replacement door:
 * it drives `serveReviewRequest`, the SAME export index.js's /mcp route calls,
 * with a request that carries the bearer and an env that carries the server's
 * correlation id.
 *
 * SO NOTHING HERE CHOOSES AN IDENTITY OR A CONTEXT. The staged identity.js
 * matches the bearer itself, asks the staged registration who the staffed Gate
 * Zero oracle is, and enters the authenticated call only when what it matched IS
 * that seat. `fn` receives the actor from the server's own dispatch, inside
 * whatever context the server decided to enter -- which for a second review lane,
 * and for an unstaffed seat, is no context at all. That is the behaviour under
 * test, not a limitation of the harness.
 *
 * Answers `{ served: false }` when the bearer matched nothing, which is the
 * server's "this was not a review request" and the next door's turn.
 */
export async function inServedReview(target, { bearer = RECORDED_REVIEW_TOKEN,
                                               correlationId = CORRELATION_ID } = {}, fn) {
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  const authorization = bearer === null ? "" : `Bearer ${bearer}`;
  const request = { headers: { get: name =>
    (typeof name === "string" && name.toLowerCase() === "authorization") ? authorization : null } };
  let answered = null;
  let ran = false;
  globalThis[DISPATCH_SEAM] = async actor => {
    ran = true;
    answered = await fn(actor);
    return answered;
  };
  try {
    const served = await identity.serveReviewRequest(
      request, { CORRELATION_ID: correlationId }, {});
    if (served === null) return { served: false, answered: null };
    assert.equal(ran, true, "the served request never reached the server's dispatch");
    return { served: true, answered };
  } finally {
    delete globalThis[DISPATCH_SEAM];
  }
}

/**
 * THE REACHABILITY GUARD, in the shape PR 1004's amendment 6 requires: a closed
 * set, parsed rather than grepped, and asserted to be exactly the modules that
 * may name this file. A producer that some other module could import and drive
 * is a producer with a second caller, and a second caller is an argument wearing
 * an import's clothes.
 */
export function moduleImports(directory) {
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
