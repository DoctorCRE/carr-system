// THE PRE-PR BASELINE, PINNED TO A COMMIT INSTEAD OF TO A MOVING REF — the
// fifth correction's first finding.
//
// Amendment 5 of 2026-09-12 lets a test exempt a value from the word sweep only
// when the test PROVES that value stood before this PR. The fourth correction
// proved it by reading `origin/main` at test time, and that is a moving ref: the
// moment PR 1004 merges, `origin/main` IS this branch, the "what changed"
// baseline contains this PR's own values, and a baseline that contains the thing
// it is supposed to predate proves nothing. The same merge turned the two
// `changed.length > 0` assertions red, because a merged branch changes no file
// against the ref it just became.
//
// SO THE BASELINE IS A COMMITTED VALUE SNAPSHOT, AUTHENTICATED TWICE.
//
//   BY DIGEST, always. `gate-zero-pre-pr-baseline.v5.json` holds the explicit
//   values — the pre-PR gate's whole surface vocabulary and its two answers —
//   and its canonical digest is pinned as a literal below. Editing the snapshot
//   without editing this file is red in every checkout, shallow ones included,
//   which is the half that must never be skippable.
//
//   BY REGENERATION, whenever the commit is reachable. `mcp-server/src` is
//   rebuilt from `git show <PRE_PR_COMMIT>:<path>`, file by file, and the suites walk
//   THAT tree with the same walkers they use on the branch and compare the
//   result to the snapshot. A checkout too shallow to hold the commit skips the
//   REGENERATION only — never the digest — and says so.
//
// The commit is the merge-base of this branch and main, so its values are the
// pre-PR ones by construction and stay so after the merge that made the old
// baseline meaningless. It is named ONCE, below, and every path here reads it
// from there — including the two suites, which is why re-pinning it after a
// merge-from-main is one edit and a regeneration rather than a hunt.
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { digest } from "../src/artifact-trust.js";

/**
 * The merge-base of this branch and main: the last commit that predates this
 * branch's changes.
 *
 * IT MOVED ON 2026-09-12, and the reason is the one this whole helper is about.
 * The pin was `229980a5…` while PR 1009 was still open. 1009 then merged and
 * CHANGED the gate's surface — byte-derived r7 presence, the renamed witness
 * fields, the reworded reader refusals — so `229980a5…` stopped being "the
 * state this branch is built on" and became a state two PRs back. A baseline
 * that predates somebody ELSE's merged work would report their strings as this
 * branch's inventions, which is the opposite of what the exemption is for.
 *
 * So the pin follows the merge-base and nothing else: `e05c8939` is 1009's
 * merge commit, the branch merged from it, and the snapshot below is
 * REGENERATED from it rather than edited. What does not move is the mechanism —
 * a commit, never a ref, authenticated by a digest pinned in this file.
 */
export const PRE_PR_COMMIT = "e05c89391426e169fa494ba9abf3a6012a899f5a";

/**
 * THE SNAPSHOT'S CANONICAL DIGEST, pinned here and nowhere else.
 *
 * This is what makes the committed values evidence rather than an assertion
 * about themselves: a snapshot edited to admit a new string digests differently
 * and is refused by every suite that loads it, whether or not the commit it
 * describes can be read in this checkout.
 */
export const PRE_PR_BASELINE_DIGEST =
  "sha256:aefd60e55cbde4c01ed72d2b9a6581e82b8fedaeae214b0e58f5c81564f50941";

/** The prefix every src path carries in git, stripped to get a tree-relative one. */
const SRC_PREFIX = "mcp-server/src/";

/** The repository this helper lives in, for the one question it asks git. */
const REPO_ROOT = fileURLToPath(new URL("../..", import.meta.url));

const BASELINE_FILE = fileURLToPath(new URL("./gate-zero-pre-pr-baseline.v5.json", import.meta.url));

/** The committed values, frozen so a suite cannot edit the evidence it is checking. */
export const PRE_PR_BASELINE = Object.freeze({
  ...JSON.parse(readFileSync(BASELINE_FILE, "utf8")),
});
Object.freeze(PRE_PR_BASELINE.gate_answer_digests);
Object.freeze(PRE_PR_BASELINE.surface_vocabulary);

assert.equal(digest(PRE_PR_BASELINE), PRE_PR_BASELINE_DIGEST,
  "the committed pre-PR baseline is not the snapshot this suite pins");
assert.equal(PRE_PR_BASELINE.commit, PRE_PR_COMMIT,
  "the committed pre-PR baseline describes a different commit");
assert.equal(PRE_PR_BASELINE.schema_version, "gate-zero-pre-pr-baseline.v1");

/**
 * AMENDMENT 2's CLOSED SHAPE, WORN BY EVERY CALLABLE THIS MODULE EXPORTS.
 *
 * The seventh review found these four helpers constructable, carrying an own
 * `prototype`, and answering `instanceof` by walking the left operand — so a
 * hostile operand's `getPrototypeOf` trap ran inside an `instanceof` against an
 * export of this module. Amendment 2's clauses (a), (b) and (d) close that:
 * bound (non-constructable, no own `prototype`), an own non-writable,
 * non-configurable, non-enumerable DATA `Symbol.hasInstance` that answers false
 * without reading its argument, no Proxy, and frozen afterwards. Binding is
 * neutral for an arrow, so no behaviour moves. The shape is asserted, export by
 * export, by the SHAPE test in `gate-zero-assurance.v5.test.mjs`.
 */
function closedCallable(callable) {
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return Object.freeze(closed);
}

/** `git`, run in this repository, refusing loudly rather than answering vaguely. */
function git(args, encoding = "utf8") {
  const run = spawnSync("git", args,
    { cwd: REPO_ROOT, encoding, maxBuffer: 256 * 1024 * 1024 });
  assert.equal(run.status, 0,
    `git ${args.join(" ")} could not be read: ${run.stderr ?? run.error}`);
  return run.stdout;
}

/**
 * Whether a commit is in THIS checkout — asked of git rather than assumed. A
 * shallow clone can hold the branch and not its merge-base, and the answer to
 * that is to skip the regeneration and keep the digest check, not to invent a
 * baseline.
 */
export const commitReachable = closedCallable(rev =>
  spawnSync("git", ["cat-file", "-e", `${rev}^{commit}`],
    { cwd: REPO_ROOT, encoding: "utf8" }).status === 0);

export const prePrCommitReachable = closedCallable(() => commitReachable(PRE_PR_COMMIT));

const stagedTrees = [];
let prePrTree = null;

/**
 * `mcp-server/src` AS THE PRE-PR COMMIT HAS IT, built with `git show` rather
 * than by patching the working tree.
 *
 * The fourth correction copied today's src and restored the paths that differed;
 * that reconstruction is only as good as the diff it is handed, and the diff was
 * against the ref that moved. Every path is read out of the commit itself, as
 * BYTES so the one binary asset in src survives the round trip, so what is
 * imported from this tree is the commit's module and nothing of this branch.
 */
export const stagePrePrTree = closedCallable(() => {
  if (prePrTree !== null) return prePrTree;
  assert.ok(prePrCommitReachable(),
    `${PRE_PR_COMMIT} is not in this checkout, so its tree cannot be staged`);

  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const base = mkdtempSync(join(cache, "gate-zero-pre-pr-"));
  stagedTrees.push(base);
  const target = join(base, "src");

  const paths = git(["ls-tree", "-r", "--name-only", PRE_PR_COMMIT, "--", "mcp-server/src"])
    .split("\n").filter(Boolean);
  assert.ok(paths.length > 0, `${PRE_PR_COMMIT} has no mcp-server/src to stage`);
  for (const path of paths) {
    assert.ok(path.startsWith(SRC_PREFIX), `git named a path outside src: ${path}`);
    const at = join(target, path.slice(SRC_PREFIX.length));
    mkdirSync(dirname(at), { recursive: true });
    writeFileSync(at, git(["show", `${PRE_PR_COMMIT}:${path}`], "buffer"));
  }
  prePrTree = target;
  return prePrTree;
});

/** Every tree this helper staged, removed together. */
export const releasePrePrTrees = closedCallable(() => {
  for (const base of stagedTrees) rmSync(base, { recursive: true, force: true });
  stagedTrees.length = 0;
  prePrTree = null;
});
