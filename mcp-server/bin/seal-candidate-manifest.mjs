// seal-candidate-manifest.mjs — THE SEALED CANDIDATE MANIFEST FOR ONE REVISION,
// computed at BUILD TIME, where a git checkout exists.
//
// WHY IT IS HERE AND NOT IN THE PRODUCER (standing-rule amendment 9,
// 2026-09-14). The Gate Zero producer used to compute all of this at request
// time by reading `.git` — walking up for the directory, resolving HEAD, and
// zlib-inflating loose objects. The deployed Worker has no checkout: Cloudflare
// serves the bundled modules over a read-only virtual filesystem and supplies no
// `.git`, so that derivation could only refuse in production. The work moves to
// the one place that DOES stand in a repository — bin/deploy-worker.sh, which
// runs this script and stamps its output into the upload beside GIT_SHA.
//
// WHAT IT SEALS, and every field is a fact about the REVISION rather than about
// the checkout standing on it:
//
//   source_digest      git's own blob id for every candidate `.js` path in that
//                      revision's tree, by path. This is what the revision
//                      SEALED.
//   artifact_digest    sha256 of each of those blobs' CONTENT, by the same
//                      paths, read out of the object store at the same revision.
//   candidate_tree_id  the tree id of mcp-server/src itself, not the repository
//                      root's — the root moves when any file anywhere moves, and
//                      the candidate digest must stand on the candidate.
//   environment_manifest_digest / fixture_set_digest
//                      the two other sealed artifacts the receipt's digests
//                      stand on, by the producer's own recipes: JCS over the
//                      parsed environment manifest, and sha256-per-file over the
//                      sealed fixture bytes under their repository paths.
//
// SEALED AND OBSERVED CANNOT DIVERGE HERE, which is stronger than what the
// previous shape achieved rather than weaker. The earlier producer read the
// blob ids from HEAD and the bytes from the WORKING TREE, precisely so a dirty
// checkout would show up as a mismatch. This reads both from the object store at
// one revision, so there is no second reading to disagree — and the wrapper that
// runs it refuses a dirty tree before it deploys at all, which is where that
// question belongs.
//
// NOTHING HERE IS SHIPPED. This file is a build tool; the Worker bundle never
// imports it. What reaches production is the manifest's TEXT and its DIGEST, as
// two Worker vars.
//
// IT IS A LIBRARY, NOT AN ENTRYPOINT, and that is a registry fact rather than a
// style choice. ops/scac-mutation-inventory.mjs enumerates every TRACKED file
// that carries a shebang or a command-line main as a script entrypoint, and an
// entrypoint is an INGRESS: admitting one moves the frozen source inventory,
// which only a sealed registry successor and its migration may do. This module
// therefore has no shebang and no command-line main; the one thing that runs it
// is bin/deploy-worker.sh, which imports `sealCandidateManifest` in a single
// `node --input-type=module` evaluation and stamps what comes back. The same
// shape lib/canonical_freshness.py already carries for the same reason.

import { execFileSync } from "node:child_process";
import { canonicalJson, digest } from "../src/artifact-trust.js";
import { CANDIDATE_MANIFEST_SCHEMA } from "../src/build-stamp.js";

/** The candidate directory, as repository-relative path segments. */
const CANDIDATE_PATH = "mcp-server/src";
/** The environment matrix ops/environment-matrix-selftest.py holds the tree to. */
const ENVIRONMENT_MANIFEST_PATH = "ops/config/environments.json";
/** The sealed fixture set `evidence_scope: candidate-and-test` covers by name. */
const SEALED_FIXTURE_SET_PATHS = Object.freeze([
  "mcp-server/test/gate-zero-producer-stores.v5.fixture.mjs",
  "mcp-server/test/gate-zero-seam-stores.v5.fixture.mjs",
  "mcp-server/test/gate-zero-seam-stores.v5.receipt-fixture.mjs",
]);

const REVISION = /^[0-9a-f]{40}$/;

function git(repo, args, options = {}) {
  return execFileSync("git", ["-C", repo, ...args],
    { maxBuffer: 256 * 1024 * 1024, ...options });
}

function gitText(repo, args) {
  return git(repo, args, { encoding: "utf8" }).trim();
}

/**
 * Every blob this manifest covers, read out of the object store in ONE
 * `git cat-file --batch` rather than one subprocess per file.
 *
 * The batch protocol is `<oid> <type> <size>\n<content>\n` per request, in the
 * order asked. It is parsed as BYTES: a candidate file is source text today, but
 * a manifest that assumed so would seal something different from what git holds
 * the moment one is not.
 */
function blobContents(repo, ids) {
  if (ids.length === 0) return new Map();
  const out = git(repo, ["cat-file", "--batch"], { input: `${ids.join("\n")}\n` });
  const contents = new Map();
  let at = 0;
  for (const id of ids) {
    const newline = out.indexOf(0x0a, at);
    if (newline < 0) throw new Error(`cat-file answer ended before ${id}`);
    const header = out.subarray(at, newline).toString("latin1").split(" ");
    if (header.length !== 3 || header[1] !== "blob")
      throw new Error(`cat-file did not answer a blob for ${id}: ${header.join(" ")}`);
    const size = Number(header[2]);
    const start = newline + 1;
    contents.set(id, out.subarray(start, start + size));
    at = start + size + 1;
  }
  return contents;
}

/** path -> blob id for every file under `prefix` in `revision`'s tree. */
function sealedPaths(repo, revision, prefix) {
  const listed = gitText(repo, ["ls-tree", "-r", "-z", revision, "--", prefix]);
  const files = [];
  for (const entry of listed.split("\0")) {
    if (entry === "") continue;
    const parsed = /^(\d{6}) (blob|commit) ([0-9a-f]{40})\t(.+)$/.exec(entry);
    if (parsed === null) throw new Error(`ls-tree entry did not parse: ${entry}`);
    if (parsed[2] !== "blob") continue;
    files.push({ path: parsed[4], blob_id: parsed[3] });
  }
  return files.sort((a, b) => (a.path < b.path ? -1 : 1));
}

/** One named blob's bytes at `revision`, or a thrown failure naming the path. */
function blobAt(repo, revision, path) {
  const id = gitText(repo, ["rev-parse", `${revision}:${path}`]);
  if (!REVISION.test(id)) throw new Error(`${path} is not a blob at ${revision}`);
  return blobContents(repo, [id]).get(id);
}

export function sealCandidateManifest(repo, rev = "HEAD") {
  const gitSha = gitText(repo, ["rev-parse", "--verify", `${rev}^{commit}`]);
  if (!REVISION.test(gitSha))
    throw new Error(`${rev} did not resolve to an exact commit in ${repo}`);
  const candidateTreeId = gitText(repo, ["rev-parse", `${gitSha}:${CANDIDATE_PATH}`]);
  if (!REVISION.test(candidateTreeId))
    throw new Error(`${CANDIDATE_PATH} is not a tree at ${gitSha}`);

  const sealed = sealedPaths(repo, gitSha, CANDIDATE_PATH).filter(f => f.path.endsWith(".js"));
  if (sealed.length === 0) throw new Error(`${CANDIDATE_PATH} sealed no .js paths at ${gitSha}`);
  const contents = blobContents(repo, sealed.map(file => file.blob_id));

  const environmentBytes = blobAt(repo, gitSha, ENVIRONMENT_MANIFEST_PATH);
  const fixtureDigests = {};
  for (const path of SEALED_FIXTURE_SET_PATHS)
    fixtureDigests[path] = digest(blobAt(repo, gitSha, path));

  const manifest = {
    schema_version: CANDIDATE_MANIFEST_SCHEMA,
    git_sha: gitSha,
    candidate_tree_id: candidateTreeId,
    file_count: sealed.length,
    byte_length: sealed.reduce((total, file) => total + contents.get(file.blob_id).length, 0),
    artifact_digest: digest(canonicalJson(Object.fromEntries(
      sealed.map(file => [file.path, digest(contents.get(file.blob_id))])))),
    source_digest: digest(canonicalJson(Object.fromEntries(
      sealed.map(file => [file.path, file.blob_id])))),
    environment_manifest_digest: digest(canonicalJson(
      JSON.parse(environmentBytes.toString("utf8")))),
    fixture_set_digest: digest(canonicalJson(fixtureDigests)),
  };
  return { manifest, manifest_text: canonicalJson(manifest), digest: digest(manifest) };
}
