// THE CANDIDATE SEALER, proved over a REAL repository — the build-time half of
// standing-rule amendment 9 (2026-09-14).
//
// WHY IT IS ITS OWN SUITE. The producer's own suite runs under the production
// condition, which is no `.git` at all, and proves that the receipt's digests
// move with the STAMP. That leaves one link unproved: that the stamp moves with
// the BYTES. Only a process standing in a checkout can prove it, so this file
// builds one — `git init`, a commit, and the sealer run against it — and the two
// suites together carry the chain the receipt stands on:
//
//   one byte changes -> the sealed manifest moves   (here)
//                    -> the stamped digest moves    (here)
//                    -> the receipt's digest moves  (gate-zero-producer.v5)
//
// NOTHING HERE IMPORTS THE PRODUCER. The sealer is a build tool; the Worker
// bundle never loads it, and this suite is about what it computes, not about
// what consumes it.
//
//   node --test mcp-server/test/gate-zero-candidate-seal.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { CANDIDATE_MANIFEST_SCHEMA } from "../src/build-stamp.js";
import { sealCandidateManifest } from "../bin/seal-candidate-manifest.mjs";

const CACHE = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
const staged = [];
after(() => {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
});

const git = (repo, ...args) => execFileSync("git", ["-C", repo, ...args],
  { encoding: "utf8", env: { ...process.env, GIT_CONFIG_GLOBAL: "/dev/null",
                             GIT_CONFIG_SYSTEM: "/dev/null" } });

/**
 * A REAL REPOSITORY, small enough to commit in milliseconds and shaped exactly
 * like the one the wrapper seals: candidate modules under mcp-server/src, the
 * three sealed fixtures under mcp-server/test, and the environment manifest.
 */
function stageRepository({ candidate = "export const ONE = 1;\n",
                           environment = { version: 1 },
                           fixture = "// the sealed fixture\n" } = {}) {
  mkdirSync(CACHE, { recursive: true });
  const repo = mkdtempSync(join(CACHE, "candidate-seal-"));
  staged.push(repo);
  mkdirSync(join(repo, "mcp-server", "src", "nested"), { recursive: true });
  mkdirSync(join(repo, "mcp-server", "test"), { recursive: true });
  mkdirSync(join(repo, "ops", "config"), { recursive: true });
  writeFileSync(join(repo, "mcp-server", "src", "one.js"), candidate);
  writeFileSync(join(repo, "mcp-server", "src", "nested", "two.js"), "export const TWO = 2;\n");
  // NOT A CANDIDATE PATH, and the manifest must not carry it: the sealed set is
  // the `.js` files under mcp-server/src and nothing else.
  writeFileSync(join(repo, "mcp-server", "src", "notes.md"), "# not a module\n");
  for (const name of ["gate-zero-producer-stores.v5.fixture.mjs",
    "gate-zero-seam-stores.v5.fixture.mjs",
    "gate-zero-seam-stores.v5.receipt-fixture.mjs"])
    writeFileSync(join(repo, "mcp-server", "test", name), fixture);
  writeFileSync(join(repo, "ops", "config", "environments.json"),
    JSON.stringify(environment, null, 2));

  git(repo, "init", "--quiet");
  git(repo, "config", "user.email", "sealer@example.invalid");
  git(repo, "config", "user.name", "The Sealer");
  git(repo, "config", "commit.gpgsign", "false");
  git(repo, "add", "-A");
  git(repo, "commit", "--quiet", "-m", "the staged candidate");
  return repo;
}

test("SEAL: the manifest is a fact about the revision, and its digest covers it", () => {
  const repo = stageRepository();
  const sealed = sealCandidateManifest(repo);
  const head = git(repo, "rev-parse", "HEAD").trim();

  assert.equal(sealed.manifest.schema_version, CANDIDATE_MANIFEST_SCHEMA);
  assert.equal(sealed.manifest.git_sha, head);
  // THE CANDIDATE'S OWN TREE, not the repository root's. The root tree moves
  // when any file anywhere moves; the candidate digest must stand on the
  // candidate.
  assert.equal(sealed.manifest.candidate_tree_id,
    git(repo, "rev-parse", `${head}:mcp-server/src`).trim());
  assert.notEqual(sealed.manifest.candidate_tree_id,
    git(repo, "rev-parse", `${head}^{tree}`).trim());
  // TWO `.js` PATHS, and the markdown file beside them is not one of them.
  assert.equal(sealed.manifest.file_count, 2);
  assert.equal(sealed.manifest.byte_length,
    readFileSync(join(repo, "mcp-server", "src", "one.js")).length
    + readFileSync(join(repo, "mcp-server", "src", "nested", "two.js")).length);

  // THE SEALED HALF IS GIT'S OWN ADDRESSING, recomputed here from git rather
  // than from the sealer, so a sealer that hashed something else is red.
  assert.equal(sealed.manifest.source_digest, digest(canonicalJson({
    "mcp-server/src/nested/two.js": git(repo, "rev-parse", `${head}:mcp-server/src/nested/two.js`).trim(),
    "mcp-server/src/one.js": git(repo, "rev-parse", `${head}:mcp-server/src/one.js`).trim(),
  })));
  // AND THE OBSERVED HALF IS THOSE BLOBS' CONTENTS, under the same paths.
  assert.equal(sealed.manifest.artifact_digest, digest(canonicalJson({
    "mcp-server/src/nested/two.js": digest(Buffer.from("export const TWO = 2;\n")),
    "mcp-server/src/one.js": digest(Buffer.from("export const ONE = 1;\n")),
  })));
  // THE ENVIRONMENT DIGEST IS JCS OVER THE PARSED MANIFEST, which is the recipe
  // the producer used to compute for itself and now consumes.
  assert.equal(sealed.manifest.environment_manifest_digest,
    digest(canonicalJson({ version: 1 })));

  // THE DIGEST COVERS THE MANIFEST, by the same recipe, so the producer's
  // cross-check between the two stamps is a check it can actually make.
  assert.equal(sealed.digest, digest(sealed.manifest));
  assert.equal(sealed.manifest_text, canonicalJson(sealed.manifest));
  assert.equal(digest(JSON.parse(sealed.manifest_text)), sealed.digest);
});

test("SEAL: one byte of any sealed artifact moves the stamp, and moves nothing else", () => {
  const base = sealCandidateManifest(stageRepository());

  // (a) ONE BYTE OF ONE CANDIDATE MODULE. Both halves move — git's blob id and
  // the content hash — and so does the candidate tree id, the byte length and
  // the manifest digest. The environment and fixture digests do not.
  const candidate = sealCandidateManifest(
    stageRepository({ candidate: "export const ONE = 11;\n" }));
  assert.notEqual(candidate.manifest.source_digest, base.manifest.source_digest);
  assert.notEqual(candidate.manifest.artifact_digest, base.manifest.artifact_digest);
  assert.notEqual(candidate.manifest.candidate_tree_id, base.manifest.candidate_tree_id);
  assert.notEqual(candidate.digest, base.digest);
  assert.equal(candidate.manifest.environment_manifest_digest,
    base.manifest.environment_manifest_digest);
  assert.equal(candidate.manifest.fixture_set_digest, base.manifest.fixture_set_digest);

  // (b) ONE BYTE OF THE ENVIRONMENT MANIFEST.
  const environment = sealCandidateManifest(
    stageRepository({ environment: { version: 2 } }));
  assert.notEqual(environment.manifest.environment_manifest_digest,
    base.manifest.environment_manifest_digest);
  assert.equal(environment.manifest.source_digest, base.manifest.source_digest);
  assert.equal(environment.manifest.fixture_set_digest, base.manifest.fixture_set_digest);
  assert.notEqual(environment.digest, base.digest);

  // (c) ONE BYTE OF THE SEALED FIXTURE SET.
  const fixtures = sealCandidateManifest(
    stageRepository({ fixture: "// the sealed fixture, edited\n" }));
  assert.notEqual(fixtures.manifest.fixture_set_digest, base.manifest.fixture_set_digest);
  assert.equal(fixtures.manifest.source_digest, base.manifest.source_digest);
  assert.equal(fixtures.manifest.environment_manifest_digest,
    base.manifest.environment_manifest_digest);
  assert.notEqual(fixtures.digest, base.digest);

  // (d) AND A REPOSITORY SEALED TWICE IS SEALED THE SAME WAY, so the assertions
  // above are about the bytes and not about the walk order or the clock.
  const repo = stageRepository();
  assert.deepEqual(sealCandidateManifest(repo), sealCandidateManifest(repo));
});

test("SEAL: a revision this machine cannot read is a visible failure, never a guess", () => {
  const repo = stageRepository();
  assert.throws(() => sealCandidateManifest(repo, "0".repeat(40)));
  assert.throws(() => sealCandidateManifest(repo, "not-a-revision"));
  // AND A REPOSITORY WITH NO CANDIDATE DIRECTORY FAILS RATHER THAN SEALING AN
  // EMPTY MANIFEST. An empty file set would produce a perfectly well-formed
  // digest of nothing, which is the shape this whole amendment exists to refuse.
  const empty = mkdtempSync(join(CACHE, "candidate-seal-empty-"));
  staged.push(empty);
  git(empty, "init", "--quiet");
  git(empty, "config", "user.email", "sealer@example.invalid");
  git(empty, "config", "user.name", "The Sealer");
  writeFileSync(join(empty, "README.md"), "nothing to seal\n");
  git(empty, "add", "-A");
  git(empty, "commit", "--quiet", "-m", "no candidate");
  assert.throws(() => sealCandidateManifest(empty));
});
