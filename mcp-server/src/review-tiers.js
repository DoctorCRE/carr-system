// The review-tier map, read inside the Worker.
//
// doctrine: engineering-workflow-sop
//
// One map decides how much review a changed path gets (engineering-workflow-sop
// section 15, "Risk tier by path"). The data is ops/config/review-tiers.v1.json,
// rendered into ./review-tiers.generated.js by ops/sync-review-tiers.py because a
// Worker has no filesystem. This reader implements the same five string
// operations as lib/review_tiers.py; ops/fixtures/review-tiers/tier-vectors.v1.json
// and both test suites hold the two readers equal.
//
// Tier 3 never blocks a human: the merge controller declines to AUTO-merge and a
// human presses merge (decision 8daefaba, code-owner review stays advisory).

import { REVIEW_TIERS, REVIEW_TIERS_DIGEST, REVIEW_TIERS_SOURCE } from "./review-tiers.generated.js";

export { REVIEW_TIERS, REVIEW_TIERS_DIGEST, REVIEW_TIERS_SOURCE };

const TOP_TIER = 3;
export const MERGE_REFUSAL_TIER = 3;

function normalize(path) {
  if (typeof path !== "string" || !path) return null;
  let normal = path.replaceAll("\\", "/");
  while (normal.startsWith("./")) normal = normal.slice(2);
  return normal || null;
}

function matches(row, path) {
  let subject = path;
  let pattern = row.pattern;
  let basenamePrefix = row.basename_prefix || "";
  if (row.case_insensitive === true) {
    subject = subject.toLowerCase();
    pattern = pattern.toLowerCase();
    basenamePrefix = basenamePrefix.toLowerCase();
  }
  if (!subject.slice(subject.lastIndexOf("/") + 1).startsWith(basenamePrefix)) return false;
  switch (row.match) {
    case "path": return subject === pattern;
    case "prefix": return subject.startsWith(pattern);
    case "suffix": return subject.endsWith(pattern);
    case "basename": return subject.slice(subject.lastIndexOf("/") + 1) === pattern;
    case "contains": return subject.includes(pattern);
    default: throw new Error(`unknown review-tier match kind ${row.match}`);
  }
}

// The highest tier of every rule matching `path`, else the default. A path
// that cannot be read is the top tier: fail toward more review.
export function reviewTierForPath(path) {
  const normal = normalize(path);
  if (normal === null) return TOP_TIER;
  let tier = REVIEW_TIERS.default_tier;
  for (const row of REVIEW_TIERS.rules) {
    if (row.tier > tier && matches(row, normal)) tier = row.tier;
  }
  return tier;
}

// A change set takes the highest tier of its paths.
export function reviewTierForPaths(paths) {
  let tier = REVIEW_TIERS.default_tier;
  for (const path of paths || []) tier = Math.max(tier, reviewTierForPath(path));
  return tier;
}

// True when `path` is dropped before a model reads a diff. Never lowers a tier.
export function isReviewNoise(path) {
  const normal = normalize(path);
  if (normal === null) return false;
  if (isTestFile(path)) return false;
  if (REVIEW_TIERS.never_exclude.some(row => matches(row, normal))) return false;
  return REVIEW_TIERS.noise_exclusions.some(row => matches(row, normal));
}

export function isTestFile(path) {
  const normal = normalize(path);
  return normal !== null && REVIEW_TIERS.test_files.some(row => matches(row, normal));
}

export function reviewChangeSize(changes) {
  const code_paths = [], test_paths = [];
  let code_lines = 0, test_lines = 0;
  for (const change of changes) {
    const test = isTestFile(change.path);
    (test ? test_paths : code_paths).push(change.path);
    const counts = [change.additions, change.deletions];
    const known = counts.every(v => Number.isSafeInteger(v) && v >= 0);
    if (test) test_lines = known && test_lines !== null ? test_lines + counts[0] + counts[1] : null;
    else code_lines = known && code_lines !== null ? code_lines + counts[0] + counts[1] : null;
  }
  const limits = REVIEW_TIERS.change_size;
  const change_size = code_lines === null ? "unknown" : code_lines <= limits.small_max_code_lines ? "small"
    : code_lines <= limits.medium_max_code_lines ? "medium" : "large";
  return {code_lines, test_lines, change_size, code_paths, test_paths};
}
