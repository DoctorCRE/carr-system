// Source-merge-only authority gate.
//
// The Engineering executor deliberately stops at opening a PR.  This module is
// the later controller gate: it accepts no repository-write actions except an
// exact-head squash merge, and only after the persisted Engineering Passport,
// the controller's exact authorized file set, GitHub's live required checks,
// and the live protected-main ruleset all reconcile.

import { sha256 } from "./sha256.js";
import { MERGE_REFUSAL_TIER, reviewTierForPath } from "./review-tiers.js";

export const NO_CEREMONIAL_MERGE_DECISION =
  "decision:4eaae0e1-f3b0-4e5d-af93-c44f39adc687";
export const NO_CEREMONIAL_MERGE_DECISION_TITLE =
  "Routine authorized green PRs merge without asking Joe for ceremonial approval";
export const SOURCE_MERGE_RULESET_ID = 20824501;
export const SOURCE_MERGE_CHECK_CONTEXT = "ops/ci.sh --strict";
export const SOURCE_MERGE_CHECK_APP_ID = 15368;
export const SOURCE_MERGE_ACTIONS = Object.freeze(["repository:merge-pr"]);
export const SOURCE_MERGE_FORBIDDEN_ACTIONS = Object.freeze([
  "repository:deploy",
  "repository:release",
  "repository:migrate-production",
  "repository:write-credential",
  "repository:external-send",
  "repository:expand-scope",
]);

const SHA = /^[0-9a-f]{40}$/;
const BLOCKING_LABEL_TOKENS = Object.freeze([
  "do-not-merge", "security", "privacy", "incident", "migration",
  "decision", "council", "release", "deployment",
]);

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") return Object.keys(value).sort().reduce((out, key) => {
    if (value[key] !== undefined) out[key] = canonicalize(value[key]);
    return out;
  }, {});
  return value;
}

export function sourceMergeDigest(value) {
  return `sha256:${sha256(JSON.stringify(canonicalize(value)))}`;
}

function stableAuthorityEvidence(authorization) {
  if (!object(authorization)) return authorization;
  const { currentness_evaluated_at: _freshnessObservation, ...stable } = authorization;
  return stable;
}

function object(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function strings(value) {
  return Array.isArray(value) && value.every(item => typeof item === "string" && item.length > 0);
}

function unique(value) {
  return strings(value) && new Set(value).size === value.length;
}

function exactKeys(value, keys) {
  return object(value) && Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}

function comparePaths(left, right) {
  const lowerLeft = left.toLowerCase();
  const lowerRight = right.toLowerCase();
  return lowerLeft < lowerRight ? -1 : lowerLeft > lowerRight ? 1 : left < right ? -1 : left > right ? 1 : 0;
}

function latestReviews(reviews) {
  const latest = new Map();
  for (const review of Array.isArray(reviews) ? reviews : []) {
    if (!["APPROVED", "CHANGES_REQUESTED", "DISMISSED"].includes(review?.state)) continue;
    const login = String(review?.user?.login || "").toLowerCase();
    const submitted = String(review?.submitted_at || "");
    if (login && (!latest.has(login) || submitted >= String(latest.get(login)?.submitted_at || "")))
      latest.set(login, review);
  }
  return [...latest.values()];
}

function actorSlug(value) {
  return String(value || "").replace(/^(?:actor:|reviewer:)/, "");
}

function requiredChecks(ruleset, reasons) {
  if (!object(ruleset) || ruleset.enforcement !== "active" || ruleset.target !== "branch") {
    reasons.push("ruleset_not_active_branch_control");
    return [];
  }
  if (ruleset.id !== SOURCE_MERGE_RULESET_ID) reasons.push("ruleset_identity_changed");
  const includes = ruleset?.conditions?.ref_name?.include;
  const excludes = ruleset?.conditions?.ref_name?.exclude;
  if (!Array.isArray(includes) || includes.length !== 1 || includes[0] !== "refs/heads/main")
    reasons.push("ruleset_main_scope_changed");
  if (!Array.isArray(excludes) || excludes.length !== 0) reasons.push("ruleset_main_exclusion_changed");
  if (!Array.isArray(ruleset.bypass_actors)) reasons.push("ruleset_bypass_visibility_missing");
  else if (ruleset.bypass_actors.length) reasons.push("ruleset_bypass_present");
  if (ruleset.current_user_can_bypass !== "never")
    reasons.push("ruleset_bypass_capability_changed");
  const types = new Set((ruleset.rules || []).map(rule => rule?.type));
  if (!types.has("deletion") || !types.has("non_fast_forward"))
    reasons.push("ruleset_history_protection_missing");
  const statusRules = (ruleset.rules || []).filter(rule => rule?.type === "required_status_checks");
  if (statusRules.length !== 1) {
    reasons.push("ruleset_required_check_shape_changed");
    return [];
  }
  const parameters = statusRules[0].parameters || {};
  // The merge endpoint conditionally binds only the PR head SHA. Strict status
  // checks are the protected-main half of the transaction: if main advances,
  // GitHub makes the PR out of date and refuses the merge at execution time.
  if (parameters.strict_required_status_checks_policy !== true)
    reasons.push("ruleset_strict_update_missing");
  const required = parameters.required_status_checks || [];
  const contexts = required.map(item => item?.context);
  if (required.length !== 1 || contexts[0] !== SOURCE_MERGE_CHECK_CONTEXT)
    reasons.push("ruleset_required_check_changed");
  if (required.length !== 1 || required[0]?.integration_id !== SOURCE_MERGE_CHECK_APP_ID)
    reasons.push("ruleset_required_check_source_changed");
  return required.filter(item => typeof item?.context === "string" && item.context.length > 0)
    .map(item => ({ context: item.context, integration_id: item.integration_id ?? null }));
}

function checkContexts(headSha, required, checkRuns, commitStatuses, reasons) {
  for (const check of required) {
    const context = check.context;
    const candidates = (Array.isArray(checkRuns) ? checkRuns : []).filter(run =>
      run?.name === context && run?.head_sha === headSha &&
      (check.integration_id === null || run?.app?.id === check.integration_id));
    const statuses = check.integration_id === null
      ? (Array.isArray(commitStatuses) ? commitStatuses : []).filter(status =>
        status?.context === context && status?.sha === headSha)
      : [];
    if (!candidates.length && !statuses.length) {
      reasons.push(`required_check_missing:${context}`);
      continue;
    }
    const latest = candidates.sort((left, right) =>
      String(left?.completed_at || left?.started_at || "").localeCompare(
        String(right?.completed_at || right?.started_at || ""))).at(-1);
    if (latest && (latest.status !== "completed" || latest.conclusion !== "success"))
      reasons.push(`required_check_not_successful:${context}`);
    const latestStatus = statuses.sort((left, right) =>
      String(left?.updated_at || left?.created_at || "").localeCompare(
        String(right?.updated_at || right?.created_at || ""))).at(-1);
    if (latestStatus && latestStatus.state !== "success")
      reasons.push(`required_status_not_successful:${context}`);
  }
}

function receiptReviewPairs(passport, reasons) {
  const receipts = Array.isArray(passport?.current_receipts) ? passport.current_receipts : [];
  const reviews = Array.isArray(passport?.current_reviewer_facts) ? passport.current_reviewer_facts : [];
  if (!receipts.length) reasons.push("engineering_receipts_missing");
  for (const receipt of receipts) {
    if (receipt?.outcome !== "claimed_complete") reasons.push("engineering_receipt_not_complete");
    if (!SHA.test(receipt?.source_evidence?.source_sha || "")) reasons.push("engineering_source_sha_invalid");
    if (!Array.isArray(receipt?.checks) || receipt.checks.length === 0 ||
        receipt.checks.some(check => check?.state !== "passed"))
      reasons.push("engineering_planned_check_not_passed");
    if (!Array.isArray(receipt?.deviations) || receipt.deviations.some(deviation =>
      deviation?.review_state !== "resolved" || deviation?.plan_revision_required !== false))
      reasons.push("engineering_deviation_unresolved");
    const matches = reviews.filter(review => review?.attempt_id === receipt?.attempt_id &&
      review?.slice_ref === receipt?.slice_ref);
    if (matches.length !== 1) {
      reasons.push(matches.length ? "engineering_review_disagreement" : "engineering_review_missing");
      continue;
    }
    const review = matches[0];
    if (review.state !== "passed" || review.is_independent !== true ||
        review.session_ref === receipt?.attribution?.session_ref ||
        actorSlug(review.reviewer_ref) === actorSlug(receipt?.attribution?.actor_ref) ||
        !Array.isArray(review.evidence_refs) || review.evidence_refs.length === 0)
      reasons.push("engineering_independent_review_not_passed");
  }
  if (reviews.length !== receipts.length) reasons.push("engineering_review_disagreement");
  return receipts;
}

function pathCovered(filename, claim) {
  if (claim?.operation !== "write" || claim?.mode !== "file" ||
      typeof claim?.path !== "string" || !claim.path) return false;
  return filename === claim.path;
}

function claimCoversAcceptedPath(claim) {
  if (claim?.claim_operation !== "write" || !["file", "tree"].includes(claim?.claim_mode) ||
      typeof claim?.claim_path !== "string" || !claim.claim_path) return false;
  return claim.claim_mode === "file" ? claim.claim_path === claim.path
    : claim.claim_path === claim.path || claim.path.startsWith(`${claim.claim_path}/`);
}

// Protected paths come from the one review-tier map
// (ops/config/review-tiers.v1.json, engineering-workflow-sop section 15): a
// tier-3 path is never auto-merged, so a human presses merge. The map replaced
// this module's own list without dropping any path it held
// (ops/fixtures/review-tiers/pre-change-baseline.v1.json).
function protectedSourcePath(filename) {
  return reviewTierForPath(filename) >= MERGE_REFUSAL_TIER;
}

function authorizationReasons(authorization, passport, pullRequest, reasons) {
  const fields = [
    "accepted_plan_revision", "allowed_actions", "assurance_bindings",
    "authorized_path_claims", "currentness_evaluated_at", "decision", "derived_by",
    "exact_head_sha", "pr_number", "schema_version", "scope_digest", "scope_ref",
    "source_merge_only", "work_request",
  ];
  if (!exactKeys(authorization, fields)) {
    reasons.push("controller_authorization_schema_invalid");
    return;
  }
  if (authorization.schema_version !== "source-merge-authority.v1" ||
      authorization.derived_by !== "source-merge-authority-projection")
    reasons.push("controller_authorization_not_server_derived");
  if (!exactKeys(authorization.decision, ["decision_ref", "event_ref", "sponsoring_human_slug", "title"]) ||
      authorization.decision?.decision_ref !== NO_CEREMONIAL_MERGE_DECISION ||
      authorization.decision?.sponsoring_human_slug !== "joe" ||
      authorization.decision?.title !== NO_CEREMONIAL_MERGE_DECISION_TITLE)
    reasons.push("merge_policy_decision_mismatch");
  if (authorization.source_merge_only !== true ||
      JSON.stringify(authorization.allowed_actions) !== JSON.stringify(SOURCE_MERGE_ACTIONS))
    reasons.push("source_merge_action_boundary_mismatch");
  if (!SHA.test(authorization.exact_head_sha || "") ||
      authorization.exact_head_sha !== pullRequest?.head?.sha)
    reasons.push("authorized_head_sha_mismatch");
  if (authorization.pr_number !== pullRequest?.number) reasons.push("authorized_pr_number_mismatch");
  if (JSON.stringify(authorization.work_request) !== JSON.stringify(passport?.work_request))
    reasons.push("authorized_work_request_mismatch");
  if (JSON.stringify(authorization.accepted_plan_revision) !== JSON.stringify(passport?.accepted_plan_revision))
    reasons.push("authorized_plan_revision_mismatch");
  if (!/^source-merge-scope:[0-9a-f-]{36}$/.test(authorization.scope_ref || "") ||
      !/^sha256:[0-9a-f]{64}$/.test(authorization.scope_digest || ""))
    reasons.push("controller_authority_currentness_invalid");
  const evaluatedAt = Date.parse(String(authorization.currentness_evaluated_at || ""));
  const now = Date.now();
  if (!Number.isFinite(evaluatedAt) || evaluatedAt > now + 30_000 || now - evaluatedAt > 5 * 60_000)
    reasons.push("controller_currentness_stale");
  const receipts = Array.isArray(passport?.current_receipts) ? passport.current_receipts : [];
  const receiptKeys = receipts.map(receipt => `${receipt?.slice_ref}\0${receipt?.attempt_id}`);
  const bindings = Array.isArray(authorization.assurance_bindings) ? authorization.assurance_bindings : [];
  const bindingKeys = bindings.map(binding => `${binding?.slice_ref}\0${binding?.attempt_id}`);
  if (bindings.length !== receipts.length || !unique(receiptKeys) || !unique(bindingKeys) ||
      JSON.stringify([...bindingKeys].sort()) !== JSON.stringify([...receiptKeys].sort()) ||
      !unique(bindings.map(binding => binding?.evidence_ref)) ||
      !unique(bindings.map(binding => binding?.review_extension_ref)) ||
      bindings.some(binding => {
        const validUntil = Date.parse(String(binding?.snapshot_valid_until || ""));
        return !exactKeys(binding, [
          "attempt_id", "evidence_digest", "evidence_manifest_ref", "evidence_ref",
          "repository_commit_sha", "repository_tree_sha", "review_digest",
          "review_extension_ref", "review_manifest_ref", "reviewer_fact_ref",
          "reviewer_state", "slice_ref", "snapshot_valid_until",
        ]) || binding?.reviewer_state !== "passed" ||
          binding?.repository_commit_sha !== authorization.exact_head_sha ||
          binding?.repository_tree_sha !== pullRequest?.head?.tree_sha ||
          !Number.isFinite(validUntil) || validUntil <= now ||
          !/^sha256:[0-9a-f]{64}$/.test(binding?.evidence_digest || "") ||
          !/^sha256:[0-9a-f]{64}$/.test(binding?.review_digest || "") ||
          !/^assurance-manifest:[0-9a-f-]{36}$/.test(binding?.evidence_manifest_ref || "") ||
          !/^assurance-manifest:[0-9a-f-]{36}$/.test(binding?.review_manifest_ref || "") ||
          !/^assurance-evidence:[0-9a-f-]{36}$/.test(binding?.evidence_ref || "") ||
          !/^engineering-review:[0-9a-f-]{36}$/.test(binding?.reviewer_fact_ref || "") ||
          !/^assurance-review:[0-9a-f-]{36}$/.test(binding?.review_extension_ref || "");
      })) reasons.push("canonical_assurance_binding_missing");
  if (!Array.isArray(authorization.authorized_path_claims) ||
      authorization.authorized_path_claims.length === 0) {
    reasons.push("canonical_path_authority_missing");
  } else {
    const claimPaths = authorization.authorized_path_claims.map(claim => claim?.path);
    if (authorization.authorized_path_claims.some(claim =>
      !exactKeys(claim, ["claim_mode", "claim_operation", "claim_path", "lease_ref", "mode", "operation", "path"]) ||
      claim?.mode !== "file" || claim?.operation !== "write" ||
      !/^canonical-ownership-lease:[0-9a-f-]{36}$/.test(claim?.lease_ref || "") ||
      !claimCoversAcceptedPath(claim) ||
      typeof claim?.path !== "string" || claim.path.length === 0 || claim.path.length > 500 ||
      typeof claim?.claim_path !== "string" || claim.claim_path.length === 0 || claim.claim_path.length > 500 ||
      !/^[!-~]+$/.test(claim.path) || claim.path.startsWith("/") || claim.path.endsWith("/") ||
      !/^[!-~]+$/.test(claim.claim_path) || claim.claim_path.startsWith("/") || claim.claim_path.endsWith("/") ||
      claim.path.includes("\\") || /[*?\[\]{}!]/.test(claim.path) ||
      claim.claim_path.includes("\\") || /[*?\[\]{}!]/.test(claim.claim_path) ||
      claim.path.split("/").some(part => !part || part === "." || part === "..") ||
      claim.claim_path.split("/").some(part => !part || part === "." || part === "..")) ||
      !unique(claimPaths) || new Set(claimPaths.map(path => path.toLowerCase())).size !== claimPaths.length ||
      JSON.stringify(claimPaths) !== JSON.stringify([...claimPaths].sort(comparePaths)))
      reasons.push("canonical_path_authority_invalid");
    const files = Array.isArray(pullRequest?.files) ? pullRequest.files : [];
    if (!files.length || files.some(item => !authorization.authorized_path_claims.some(claim =>
      pathCovered(item?.filename, claim))))
      reasons.push("changed_files_outside_authorized_scope");
    if (files.some(item => !["added", "modified"].includes(item?.status)))
      reasons.push("destructive_or_uninspectable_file_status");
  }
}

export function evaluateSourceMerge({ authorization, passport, pull_request: pullRequest, repository_ruleset: ruleset, repository_rulesets: rulesets }) {
  const reasons = [];
  if (!object(passport) || passport.schema_version !== "engineering-passport.v1")
    reasons.push("engineering_passport_invalid");
  if (object(passport)) {
    const { projection_digest: projectionDigest, ...projection } = passport;
    if (projectionDigest !== sourceMergeDigest(projection)) reasons.push("engineering_passport_digest_mismatch");
  }
  if (passport?.closure_state !== "complete" || passport?.stale_conflict?.state !== "none" ||
      ["work", "proof", "explanation", "release"].some(key => passport?.closure?.[key]?.state !== "complete"))
    reasons.push("engineering_passport_not_closed");
  if (!Array.isArray(passport?.slices) || passport.slices.length === 0 ||
      passport.slices.some(slice => slice?.state !== "verified_complete"))
    reasons.push("engineering_slice_not_verified");
  if (passport?.slices?.some(slice => slice?.manual_qa_required === true))
    reasons.push("manual_qa_requires_human");

  const receipts = receiptReviewPairs(passport, reasons);
  authorizationReasons(authorization, passport, pullRequest, reasons);

  const headSha = pullRequest?.head?.sha || "";
  if (!Number.isInteger(pullRequest?.number) || pullRequest.number < 1) reasons.push("pr_number_invalid");
  if (pullRequest?.state !== "open") reasons.push("pr_not_open");
  if (pullRequest?.draft === true) reasons.push("draft_pr");
  if (pullRequest?.base?.ref !== "main" || !SHA.test(pullRequest?.base?.sha || "")) reasons.push("wrong_base_branch");
  if (!SHA.test(headSha)) reasons.push("invalid_head_sha");
  if (pullRequest?.mergeable !== true || pullRequest?.mergeable_state !== "clean")
    reasons.push("pr_not_cleanly_mergeable");
  if ((pullRequest?.files || []).some(item => protectedSourcePath(item?.filename || "")))
    reasons.push("protected_source_authority_boundary");
  if (receipts.some(receipt => receipt?.source_evidence?.source_sha !== headSha))
    reasons.push("engineering_source_sha_mismatch");

  const labels = (pullRequest?.labels || []).map(label => String(label).toLowerCase());
  if (labels.some(label => BLOCKING_LABEL_TOKENS.some(token => label.includes(token))))
    reasons.push("blocking_label_present");
  if (Number(pullRequest?.review_comments || 0) > 0) reasons.push("review_comments_present");
  if (latestReviews(pullRequest?.reviews).some(review => review?.state === "CHANGES_REQUESTED"))
    reasons.push("changes_requested");

  if (!Array.isArray(rulesets) || rulesets.some(item => item?.enforcement === "active" &&
      item?.target === "branch" && item?.id !== SOURCE_MERGE_RULESET_ID))
    reasons.push("additional_active_branch_ruleset_unverified");
  const required = requiredChecks(ruleset, reasons);
  checkContexts(headSha, required, pullRequest?.check_runs, pullRequest?.commit_statuses, reasons);
  const reasonCodes = [...new Set(reasons)].sort();
  const result = {
    schema_version: "source-merge-eligibility.v1",
    eligible: reasonCodes.length === 0,
    reason_codes: reasonCodes,
    decision_ref: NO_CEREMONIAL_MERGE_DECISION,
    repository: "jbookout/carr-system",
    pr_number: Number.isInteger(pullRequest?.number) ? pullRequest.number : null,
    head_sha: SHA.test(headSha) ? headSha : null,
    base_sha: SHA.test(pullRequest?.base?.sha || "") ? pullRequest.base.sha : null,
    authority_digest: object(authorization) ? sourceMergeDigest(stableAuthorityEvidence(authorization)) : null,
    passport_digest: passport?.projection_digest || null,
    required_check_contexts: required.map(item => item.context),
    allowed_actions: reasonCodes.length === 0 ? [...SOURCE_MERGE_ACTIONS] : [],
    forbidden_actions: [...SOURCE_MERGE_FORBIDDEN_ACTIONS],
  };
  result.eligibility_digest = sourceMergeDigest(result);
  return result;
}

export function sourceMergePayload(eligibility) {
  if (!eligibility?.eligible || !SHA.test(eligibility?.head_sha || "") ||
      JSON.stringify(eligibility.allowed_actions) !== JSON.stringify(SOURCE_MERGE_ACTIONS))
    throw new Error("source merge is not eligible");
  return {
    merge_method: "squash",
    sha: eligibility.head_sha,
    commit_title: `[source-merge-controller] PR #${eligibility.pr_number}`,
  };
}
