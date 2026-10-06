// The review-tier map inside the Worker: the generated module, its reader, and
// the source-merge controller that consumes it.
//
// Three properties, the JS half of ops/review-tiers-selftest.py:
//   1. NO LOOSENING (rule a6e6ab4e): every path the merge controller refused
//      before the map existed (captured by calling the old code, see
//      ops/fixtures/review-tiers/pre-change-baseline.v1.json) is still refused.
//   2. CROSS-LANGUAGE CONSISTENCY: this reader's tier and noise decision equal
//      the Python reader's for every vector in tier-vectors.v1.json, and the
//      controller refuses exactly the tier-3 paths.
//   3. PARITY: the generated module carries the committed map unchanged.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  REVIEW_TIERS,
  REVIEW_TIERS_SOURCE,
  isReviewNoise,
  isTestFile,
  reviewChangeSize,
  reviewTierForPath,
  reviewTierForPaths,
} from "../src/review-tiers.js";
import { evaluateSourceMerge } from "../src/source-merge-policy.js";

const readRepoJson = relative => JSON.parse(readFileSync(new URL(`../../${relative}`, import.meta.url), "utf8"));
const BASELINE = readRepoJson("ops/fixtures/review-tiers/pre-change-baseline.v1.json");
const VECTORS = readRepoJson("ops/fixtures/review-tiers/tier-vectors.v1.json").vectors;
const MAP = readRepoJson(REVIEW_TIERS_SOURCE);

function mergeRefuses(filename) {
  const result = evaluateSourceMerge({ pull_request: { files: [{ filename, status: "modified" }] } });
  return result.reason_codes.includes("protected_source_authority_boundary");
}

test("every path the controller refused before the map is still refused", () => {
  assert.ok(BASELINE.merge_protected.length > 0);
  const loosened = BASELINE.merge_protected.filter(path => !mergeRefuses(path));
  assert.deepEqual(loosened, []);
});

test("the JS reader agrees with the Python reader on every vector", () => {
  assert.ok(VECTORS.length > 0);
  for (const { path, tier, noise, test: testFile } of VECTORS) {
    assert.equal(reviewTierForPath(path), tier, path);
    assert.equal(isReviewNoise(path), noise, path);
    assert.equal(isTestFile(path), testFile, path);
  }
});

test("the controller refuses exactly the tier-3 paths", () => {
  for (const { path, tier } of VECTORS) assert.equal(mergeRefuses(path), tier >= 3, path);
  for (const path of BASELINE.paths) assert.equal(mergeRefuses(path), reviewTierForPath(path) >= 3, path);
});

test("a change set takes its highest tier", () => {
  assert.equal(reviewTierForPaths([]), MAP.default_tier);
  assert.equal(reviewTierForPaths(["README.md", "mcp-server/src/tour-map-route-state.js"]), 2);
  assert.equal(reviewTierForPaths(["README.md", "CLAUDE.md"]), 3);
  const result = evaluateSourceMerge({ pull_request: { files: [
    { filename: "README.md", status: "modified" },
    { filename: "hooks/lint-gate.py", status: "modified" },
  ] } });
  assert.ok(result.reason_codes.includes("protected_source_authority_boundary"));
});

test("the generated module carries the committed map unchanged", () => {
  assert.equal(REVIEW_TIERS_SOURCE, "ops/config/review-tiers.v1.json");
  assert.equal(REVIEW_TIERS.default_tier, MAP.default_tier);
  assert.deepEqual(REVIEW_TIERS.tunable_scalars, MAP.tunable_scalars);
  const strip = rows => rows.map(({ why, ...rest }) => rest);
  assert.deepEqual(REVIEW_TIERS.rules.map(row => ({ ...row })), strip(MAP.rules));
  assert.deepEqual(REVIEW_TIERS.noise_exclusions.map(row => ({ ...row })), strip(MAP.noise_exclusions));
  assert.deepEqual(REVIEW_TIERS.never_exclude.map(row => ({ ...row })), strip(MAP.never_exclude));
  assert.deepEqual(REVIEW_TIERS.test_files.map(row => ({ ...row })), strip(MAP.test_files));
  assert.deepEqual(REVIEW_TIERS.change_size, MAP.change_size);
});

test("a path that is not a string is refused rather than treated as tier 1", () => {
  assert.equal(reviewTierForPath(undefined), 3);
  assert.equal(reviewTierForPath(""), 3);
  assert.ok(mergeRefuses(""));
});

test("test classification is evidence and preserves path tiers", async () => {
  const { isTestFile } = await import("../src/review-tiers.js");
  assert.equal(isTestFile("ops/example-selftest.py"), true);
  assert.equal(reviewTierForPath("ops/example-selftest.py"), 3);
  assert.equal(isTestFile("tests/package-lock.json"), true);
  assert.equal(isReviewNoise("tests/package-lock.json"), false);
  assert.equal(isTestFile("src/example.test.tsx.bak"), false);
});

test("code and test change vectors match the Python reader", () => {
  for (const row of readRepoJson("ops/fixtures/review-tiers/tier-vectors.v1.json").change_vectors) {
    assert.deepEqual(reviewChangeSize(row.changes), row.summary);
    assert.equal(reviewTierForPaths(row.changes.map(c => c.path)), row.path_tier);
  }
});

test("hyphenated Python test basenames are evidence with segment and suffix boundaries", () => {
  for (const path of ["test-example.py", "tools/test-progress-board.py", "./tools\\test-example.py"])
    assert.equal(isTestFile(path), true, path);
  for (const path of ["tools/contest-example.py", "tools/test-example.py.bak", "tools/test-example.js",
    "tools/test-example.py/source.py", "tools/test-example/source.py"])
    assert.equal(isTestFile(path), false, path);
  assert.deepEqual(reviewChangeSize([
    {path: "tools/progress_board.py", additions: 3, deletions: 0},
    {path: "tools/test-progress-board.py", additions: 400, deletions: 0},
  ]), {code_lines: 3, test_lines: 400, change_size: "small",
    code_paths: ["tools/progress_board.py"], test_paths: ["tools/test-progress-board.py"]});
});

test("missing and partial line counts stay unknown while explicit zero stays zero", () => {
  for (const metadata of [{}, {additions: 0}, {deletions: 0}, {additions: 3}, {deletions: 3}]) {
    const result = reviewChangeSize([{path: "lib/known.py", additions: 2, deletions: 1},
      {path: "lib/a.py", ...metadata}]);
    assert.equal(result.code_lines, null);
    assert.equal(result.change_size, "unknown");
    const evidence = reviewChangeSize([{path: "tests/a.py", ...metadata}]);
    assert.equal(evidence.test_lines, null);
    assert.equal(evidence.code_lines, 0);
    assert.equal(evidence.change_size, "small");
  }
  for (const changes of [[], [{path: "lib/a.py", additions: 0, deletions: 0}]]) {
    const result = reviewChangeSize(changes);
    assert.equal(result.code_lines, 0);
    assert.equal(result.test_lines, 0);
    assert.equal(result.change_size, "small");
  }
});
