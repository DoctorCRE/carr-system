import assert from "node:assert/strict";
import test from "node:test";

import {
  NO_CEREMONIAL_MERGE_DECISION,
  NO_CEREMONIAL_MERGE_DECISION_TITLE,
  SOURCE_MERGE_ACTIONS,
  SOURCE_MERGE_CHECK_APP_ID,
  SOURCE_MERGE_FORBIDDEN_ACTIONS,
  SOURCE_MERGE_RULESET_ID,
  evaluateSourceMerge,
  sourceMergeDigest,
  sourceMergePayload,
} from "../src/source-merge-policy.js";
import { ENGINEERING_REPOSITORY_ACTIONS } from "../src/engineering-runtime.js";
import { McpSourceMergeAuthorityClient, autoMergeOnce, planOrExecute } from "../bin/run-source-merge.mjs";

const HEAD = "a".repeat(40);
const BASE = "b".repeat(40);
const TREE = "c".repeat(40);
const SCOPE_ID = "99999999-9999-4999-8999-999999999999";
const CURRENTNESS = new Date(Date.now() - 1_000).toISOString();
const VALID_UNTIL = new Date(Date.now() + 10 * 60_000).toISOString();

function pathClaim(path) {
  return { lease_ref: `canonical-ownership-lease:${SCOPE_ID}`, path, mode: "file", operation: "write", claim_path: path, claim_mode: "file", claim_operation: "write" };
}

function passport() {
  const value = {
    schema_version: "engineering-passport.v1",
    work_request: { id: "wr:WR-900", state_version: 3, canonical_record_digest: `sha256:${"1".repeat(64)}` },
    accepted_plan_revision: { id: "plan:900", revision: 2, digest: `sha256:${"2".repeat(64)}` },
    plan_digest: `sha256:${"3".repeat(64)}`,
    slice_plan: { schema_version: "engineering-slice-plan.v1" },
    execution_envelopes: [],
    slices: [{ slice_ref: "slice:source", ordinal: 1, dependency_refs: [], state: "verified_complete", planned_check_refs: ["check:strict"], deviation_refs: [], manual_qa_required: false, release_requirement: "not_required" }],
    receipts: [{
      attempt_id: "attempt:source",
      slice_ref: "slice:source",
      outcome: "claimed_complete",
      attribution: { actor_ref: "actor:codex", session_ref: "session:executor", adapter_ref: "adapter:codex-desktop" },
      source_evidence: { source_sha: HEAD },
      checks: [{ check_ref: "check:strict", state: "passed", evidence_refs: [{ ref: "evidence:strict" }] }],
      deviations: [],
    }],
    reviewer_facts: [{
      attempt_id: "attempt:source",
      slice_ref: "slice:source",
      state: "passed",
      is_independent: true,
      reviewer_ref: "reviewer:claude",
      session_ref: "session:reviewer",
      evidence_refs: [{ ref: "evidence:review" }],
      reviewed_deviation_refs: [],
      resolved_deviation_refs: [],
    }],
    qa_facts: [],
    operator_receipt: { what_changed: [], why: "derived", evidence_refs: [], deviations: [], remaining_risk: [], manual_qa_items: [] },
    closure: {
      work: { state: "complete", evidence_refs: [], note: "complete" },
      proof: { state: "complete", evidence_refs: [], note: "complete" },
      explanation: { state: "complete", evidence_refs: [], note: "complete" },
      release: { state: "complete", evidence_refs: [], note: "source only" },
      learning: { state: "unresolved", route: null, evidence_refs: [], note: "not a merge gate" },
    },
    closure_state: "complete",
    stale_conflict: { state: "none", reason: null },
  };
  value.current_receipts = [...value.receipts];
  value.current_reviewer_facts = [...value.reviewer_facts];
  value.projection_digest = sourceMergeDigest(value);
  return value;
}

function authorization() {
  const p = passport();
  return {
    schema_version: "source-merge-authority.v1",
    derived_by: "source-merge-authority-projection",
    decision: {
      decision_ref: NO_CEREMONIAL_MERGE_DECISION,
      event_ref: "event:11111111-1111-4111-8111-111111111111",
      sponsoring_human_slug: "joe",
      title: NO_CEREMONIAL_MERGE_DECISION_TITLE,
    },
    work_request: p.work_request,
    accepted_plan_revision: p.accepted_plan_revision,
    exact_head_sha: HEAD,
    pr_number: 42,
    source_merge_only: true,
    allowed_actions: [...SOURCE_MERGE_ACTIONS],
    assurance_bindings: [{
      slice_ref: "slice:source", attempt_id: "attempt:source",
      evidence_manifest_ref: "assurance-manifest:11111111-1111-4111-8111-111111111111",
      review_manifest_ref: "assurance-manifest:22222222-2222-4222-8222-222222222222",
      evidence_ref: "assurance-evidence:33333333-3333-4333-8333-333333333333",
      reviewer_fact_ref: "engineering-review:44444444-4444-4444-8444-444444444444",
      review_extension_ref: "assurance-review:55555555-5555-4555-8555-555555555555",
      reviewer_state: "passed", evidence_digest: `sha256:${"4".repeat(64)}`,
      review_digest: `sha256:${"5".repeat(64)}`, repository_commit_sha: HEAD,
      repository_tree_sha: TREE, snapshot_valid_until: VALID_UNTIL,
    }],
    scope_ref: `source-merge-scope:${SCOPE_ID}`,
    scope_digest: `sha256:${"6".repeat(64)}`,
    currentness_evaluated_at: CURRENTNESS,
    authorized_path_claims: [
      pathClaim("mcp-server/src/routine-feature.js"),
      pathClaim("mcp-server/test/routine-feature.test.mjs"),
    ],
  };
}

function pullRequest() {
  return {
    number: 42,
    state: "open",
    draft: false,
    mergeable: true,
    mergeable_state: "clean",
    base: { ref: "main", sha: BASE },
    head: { sha: HEAD, tree_sha: TREE },
    labels: [],
    review_comments: 0,
    reviews: [],
    files: [
      { filename: "mcp-server/src/routine-feature.js", status: "added" },
      { filename: "mcp-server/test/routine-feature.test.mjs", status: "added" },
    ],
    check_runs: [{ name: "ops/ci.sh --strict", head_sha: HEAD, status: "completed", conclusion: "success", completed_at: "2026-08-31T22:00:00Z", app: { id: SOURCE_MERGE_CHECK_APP_ID, slug: "github-actions" } }],
    commit_statuses: [],
  };
}

function ruleset() {
  return {
    id: 20824501,
    enforcement: "active",
    target: "branch",
    bypass_actors: [],
    current_user_can_bypass: "never",
    conditions: { ref_name: { include: ["refs/heads/main"], exclude: [] } },
    rules: [
      { type: "deletion" },
      { type: "non_fast_forward" },
      { type: "required_status_checks", parameters: { strict_required_status_checks_policy: true, required_status_checks: [{ context: "ops/ci.sh --strict", integration_id: SOURCE_MERGE_CHECK_APP_ID }] } },
    ],
  };
}

function evaluation(overrides = {}) {
  return evaluateSourceMerge({
    authorization: authorization(),
    passport: passport(),
    pull_request: pullRequest(),
    repository_ruleset: ruleset(),
    repository_rulesets: [{ id: SOURCE_MERGE_RULESET_ID, enforcement: "active", target: "branch" }],
    ...overrides,
  });
}

test("eligible source merge needs no ceremonial owner approval and exposes one action", () => {
  const result = evaluation();
  assert.equal(result.eligible, true);
  assert.deepEqual(result.reason_codes, []);
  assert.deepEqual(result.allowed_actions, ["repository:merge-pr"]);
  assert.deepEqual(result.forbidden_actions, [...SOURCE_MERGE_FORBIDDEN_ACTIONS]);
  assert.deepEqual(sourceMergePayload(result), {
    merge_method: "squash",
    sha: HEAD,
    commit_title: "[source-merge-controller] PR #42",
  });
});

test("source merge remains outside the existing engineering executor authority", () => {
  assert.equal(ENGINEERING_REPOSITORY_ACTIONS.includes("repository:merge-pr"), false);
  assert.equal(SOURCE_MERGE_ACTIONS.some(action => ENGINEERING_REPOSITORY_ACTIONS.includes(action)), false);
});

test("controller authorization, not an independent reviewer, owns scope", () => {
  const auth = authorization();
  auth.authorized_path_claims = auth.authorized_path_claims.slice(0, 1);
  const result = evaluation({ authorization: auth });
  assert.equal(result.eligible, false);
  assert.ok(result.reason_codes.includes("changed_files_outside_authorized_scope"));
});

test("scope is exact files only and protected authority surfaces never auto-merge", () => {
  const tree = authorization();
  tree.authorized_path_claims[0].mode = "tree";
  assert.ok(evaluation({ authorization: tree }).reason_codes.includes("canonical_path_authority_invalid"));

  const protectedAuth = authorization();
  protectedAuth.authorized_path_claims.push(pathClaim("migrations/0471_example.sql"));
  protectedAuth.authorized_path_claims.sort((left, right) => left.path.localeCompare(right.path));
  const pr = pullRequest();
  pr.files.push({ filename: "migrations/0471_example.sql", status: "added" });
  const result = evaluation({ authorization: protectedAuth, pull_request: pr });
  assert.ok(result.reason_codes.includes("protected_source_authority_boundary"));

  const trustedPathAuth = authorization();
  trustedPathAuth.authorized_path_claims.push(pathClaim("mcp-server/src/tools.js"));
  trustedPathAuth.authorized_path_claims.sort((left, right) => left.path.localeCompare(right.path));
  const trustedPathPr = pullRequest();
  trustedPathPr.files.push({ filename: "mcp-server/src/tools.js", status: "modified" });
  assert.ok(evaluation({ authorization: trustedPathAuth, pull_request: trustedPathPr })
    .reason_codes.includes("protected_source_authority_boundary"));

  const digestAuth = authorization();
  digestAuth.authorized_path_claims.push(pathClaim("mcp-server/src/sha256.js"));
  digestAuth.authorized_path_claims.sort((left, right) => left.path.localeCompare(right.path));
  const digestPr = pullRequest();
  digestPr.files.push({ filename: "mcp-server/src/sha256.js", status: "modified" });
  assert.ok(evaluation({ authorization: digestAuth, pull_request: digestPr })
    .reason_codes.includes("protected_source_authority_boundary"));
});

test("historical failed attempts do not override the current verified generation", () => {
  const p = passport();
  const oldReceipt = structuredClone(p.current_receipts[0]);
  oldReceipt.attempt_id = "attempt:old";
  oldReceipt.outcome = "failed";
  oldReceipt.source_evidence.source_sha = "0".repeat(40);
  const oldReview = structuredClone(p.current_reviewer_facts[0]);
  oldReview.attempt_id = "attempt:old";
  oldReview.state = "failed";
  p.receipts.unshift(oldReceipt);
  p.reviewer_facts.unshift(oldReview);
  delete p.projection_digest;
  p.projection_digest = sourceMergeDigest(p);
  const result = evaluation({ passport: p });
  assert.equal(result.eligible, true);
  assert.deepEqual(result.reason_codes, []);
});

test("stale controller currentness stops merge", () => {
  const stale = authorization();
  stale.currentness_evaluated_at = new Date(Date.now() - 6 * 60_000).toISOString();
  assert.ok(evaluation({ authorization: stale }).reason_codes.includes("controller_currentness_stale"));
});

test("assurance bindings must cover the exact current receipt generation", () => {
  const wrongAttempt = authorization();
  wrongAttempt.assurance_bindings[0].attempt_id = "attempt:other";
  assert.ok(evaluation({ authorization: wrongAttempt }).reason_codes.includes("canonical_assurance_binding_missing"));

  const wrongTree = authorization();
  wrongTree.assurance_bindings[0].repository_tree_sha = "d".repeat(40);
  assert.ok(evaluation({ authorization: wrongTree }).reason_codes.includes("canonical_assurance_binding_missing"));

  const expired = authorization();
  expired.assurance_bindings[0].snapshot_valid_until = new Date(Date.now() - 1_000).toISOString();
  assert.ok(evaluation({ authorization: expired }).reason_codes.includes("canonical_assurance_binding_missing"));
});

test("lease claims corroborate but cannot broaden accepted file scope", () => {
  const treeClaim = authorization();
  treeClaim.authorized_path_claims[0].claim_path = "mcp-server";
  treeClaim.authorized_path_claims[0].claim_mode = "tree";
  assert.equal(evaluation({ authorization: treeClaim }).eligible, true);

  const uncovered = authorization();
  uncovered.authorized_path_claims[0].claim_path = "other";
  uncovered.authorized_path_claims[0].claim_mode = "tree";
  assert.ok(evaluation({ authorization: uncovered }).reason_codes.includes("canonical_path_authority_invalid"));
});

test("manual QA cannot be hidden by an otherwise complete Passport", () => {
  const p = passport();
  p.slices[0].manual_qa_required = true;
  const { projection_digest: _oldDigest, ...unsigned } = p;
  p.projection_digest = sourceMergeDigest(unsigned);
  const result = evaluation({ passport: p });
  assert.equal(result.eligible, false);
  assert.ok(result.reason_codes.includes("manual_qa_requires_human"));
});

test("projection scope is exact file-write authority only", () => {
  const auth = authorization();
  auth.authorized_path_claims[0].mode = "tree";
  const result = evaluation({ authorization: auth });
  assert.equal(result.eligible, false);
  assert.ok(result.reason_codes.includes("canonical_path_authority_invalid"));
});

test("renames and deletions remain outside source-merge authority", () => {
  for (const status of ["renamed", "removed"]) {
    const pr = pullRequest();
    pr.files[0].status = status;
    const result = evaluation({ pull_request: pr });
    assert.ok(result.reason_codes.includes("destructive_or_uninspectable_file_status"), status);
  }
});

test("exact Passport digest, exact head, current plan, and independent review all fail closed", () => {
  const badPassport = passport();
  badPassport.receipts[0].source_evidence.source_sha = "c".repeat(40);
  badPassport.reviewer_facts[0].session_ref = "session:executor";
  badPassport.stale_conflict = { state: "stale", reason: "plan moved" };
  const result = evaluation({ passport: badPassport });
  assert.equal(result.eligible, false);
  for (const reason of [
    "engineering_passport_digest_mismatch",
    "engineering_passport_not_closed",
    "engineering_independent_review_not_passed",
    "engineering_source_sha_mismatch",
  ]) assert.ok(result.reason_codes.includes(reason), reason);
});

test("wrong decision and expanded action authority stop merge", () => {
  const auth = authorization();
  auth.decision.sponsoring_human_slug = "reviewer";
  auth.allowed_actions.push("repository:release");
  const result = evaluation({ authorization: auth });
  for (const reason of ["merge_policy_decision_mismatch", "source_merge_action_boundary_mismatch"])
    assert.ok(result.reason_codes.includes(reason), reason);
  assert.deepEqual(result.allowed_actions, []);
  assert.throws(() => sourceMergePayload(result), /not eligible/);
});

test("every live ruleset check must succeed on the exact head", () => {
  const pr = pullRequest();
  pr.check_runs[0].conclusion = "neutral";
  const changedRuleset = ruleset();
  changedRuleset.rules.at(-1).parameters.required_status_checks.push({ context: "security-scan" });
  const result = evaluation({ pull_request: pr, repository_ruleset: changedRuleset });
  assert.ok(result.reason_codes.includes("required_check_not_successful:ops/ci.sh --strict"));
  assert.ok(result.reason_codes.includes("required_check_missing:security-scan"));
});

test("loose required checks refuse because expected-head alone cannot bind main", () => {
  const looseRuleset = ruleset();
  looseRuleset.rules.at(-1).parameters.strict_required_status_checks_policy = false;
  const result = evaluation({ repository_ruleset: looseRuleset });
  assert.equal(result.eligible, false);
  assert.ok(result.reason_codes.includes("ruleset_strict_update_missing"));
});

test("a required GitHub App check must come from the configured integration", () => {
  const protectedRuleset = ruleset();
  const pr = pullRequest();
  pr.check_runs[0].app = { id: 1357 };
  const mismatch = evaluation({ pull_request: pr, repository_ruleset: protectedRuleset });
  assert.ok(mismatch.reason_codes.includes("required_check_missing:ops/ci.sh --strict"));

  pr.check_runs[0].app.id = SOURCE_MERGE_CHECK_APP_ID;
  const match = evaluation({ pull_request: pr, repository_ruleset: protectedRuleset });
  assert.equal(match.eligible, true);
});

test("required CI identity and bypass capability are pinned against ruleset drift", () => {
  const drifted = ruleset();
  drifted.current_user_can_bypass = "always";
  drifted.rules.at(-1).parameters.required_status_checks = [{ context: "easy-check" }];
  const result = evaluation({ repository_ruleset: drifted });
  for (const reason of [
    "ruleset_bypass_capability_changed",
    "ruleset_required_check_changed",
    "ruleset_required_check_source_changed",
  ]) assert.ok(result.reason_codes.includes(reason), reason);
});

test("review requests, blocking labels, ruleset bypass, and dirty mergeability stop merge", () => {
  const pr = pullRequest();
  pr.mergeable_state = "dirty";
  pr.labels = ["security-review"];
  pr.reviews = [{ user: { login: "reviewer" }, state: "CHANGES_REQUESTED", submitted_at: "2026-08-31T22:01:00Z" }];
  const protectedRuleset = ruleset();
  protectedRuleset.bypass_actors = [{ actor_id: 1 }];
  const result = evaluation({ pull_request: pr, repository_ruleset: protectedRuleset });
  for (const reason of ["pr_not_cleanly_mergeable", "blocking_label_present", "changes_requested", "ruleset_bypass_present"])
    assert.ok(result.reason_codes.includes(reason), reason);
});

test("COMMENTED does not erase an earlier change request", () => {
  const pr = pullRequest();
  pr.reviews = [
    { user: { login: "reviewer" }, state: "CHANGES_REQUESTED", submitted_at: "2026-08-31T22:01:00Z" },
    { user: { login: "reviewer" }, state: "COMMENTED", submitted_at: "2026-08-31T22:02:00Z" },
  ];
  assert.ok(evaluation({ pull_request: pr }).reason_codes.includes("changes_requested"));
});

test("ruleset exclusions, hidden bypasses, and extra rulesets fail closed", () => {
  const excluded = ruleset();
  excluded.conditions.ref_name.exclude = ["refs/heads/main"];
  delete excluded.bypass_actors;
  const result = evaluation({
    repository_ruleset: excluded,
    repository_rulesets: [
      { id: SOURCE_MERGE_RULESET_ID, enforcement: "active", target: "branch" },
      { id: 999, enforcement: "active", target: "branch" },
    ],
  });
  for (const reason of [
    "ruleset_main_exclusion_changed",
    "ruleset_bypass_visibility_missing",
    "additional_active_branch_ruleset_unverified",
  ]) assert.ok(result.reason_codes.includes(reason), reason);
});

test("commit statuses cannot contradict or impersonate the app-bound required check", () => {
  const pr = pullRequest();
  pr.commit_statuses = [{ context: "ops/ci.sh --strict", sha: HEAD, state: "failure", updated_at: "2026-08-31T22:01:00Z" }];
  assert.equal(evaluation({ pull_request: pr }).eligible, true);

  pr.check_runs[0].conclusion = "failure";
  pr.commit_statuses[0].state = "success";
  assert.ok(evaluation({ pull_request: pr }).reason_codes.includes("required_check_not_successful:ops/ci.sh --strict"));
});

test("the actuator and evaluator are pinned to the protected-main ruleset", async () => {
  const wrongRuleset = ruleset();
  wrongRuleset.id += 1;
  assert.ok(evaluation({ repository_ruleset: wrongRuleset }).reason_codes.includes("ruleset_identity_changed"));

  const invalidLocator = locator();
  invalidLocator.ruleset_id = SOURCE_MERGE_RULESET_ID + 1;
  await assert.rejects(
    planOrExecute({ command: "plan", locator: invalidLocator, resolveAuthority: canonicalResolver(), token: "token", fetchImpl: liveFetch().fetchImpl }),
    /locator is invalid/,
  );
});

function response(value, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(value) };
}

function liveFetch() {
  let snapshots = 0;
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options });
    const path = new URL(url).pathname + new URL(url).search;
    if (options.method === "PUT" && path.endsWith("/pulls/42/merge"))
      return response({ merged: true, sha: "d".repeat(40), message: "merged" });
    if (path.includes("/pulls?state=open&base=main&sort=created&direction=asc"))
      return response([{ number: 42, draft: false, head: { sha: HEAD }, body: "ordinary PR body" }]);
    if (path.endsWith("/pulls/42")) {
      snapshots += 1;
      const pr = pullRequest();
      return response({ ...pr, labels: [], head: { sha: HEAD }, base: { ref: "main", sha: BASE } });
    }
    if (path.includes("/pulls/42/files?")) return response(pullRequest().files);
    if (path.includes("/pulls/42/reviews?")) return response([]);
    if (path.includes(`/commits/${HEAD}/check-runs?`)) return response({ check_runs: pullRequest().check_runs });
    if (path.includes(`/statuses/${HEAD}?`)) return response([]);
    if (path.endsWith(`/git/commits/${HEAD}`)) return response({ sha: HEAD, tree: { sha: TREE } });
    if (path.includes("/rulesets?includes_parents=true"))
      return response([{ id: SOURCE_MERGE_RULESET_ID, enforcement: "active", target: "branch" }]);
    if (path.endsWith("/rulesets/20824501")) return response(ruleset());
    return response({ message: "not found" }, 404);
  };
  return { fetchImpl, calls, snapshots: () => snapshots };
}

function locator() {
  return {
    decision_id: NO_CEREMONIAL_MERGE_DECISION.replace(/^decision:/, ""),
    work_request: "WR-900",
    head_sha: HEAD,
    repository: "jbookout/carr-system",
    ruleset_id: SOURCE_MERGE_RULESET_ID,
    pr_number: 42,
  };
}

function resolvedAuthority() {
  return { ...authorization(), passport: passport() };
}

function canonicalResolver(transform = value => value) {
  let reads = 0;
  return async () => {
    const value = structuredClone(resolvedAuthority());
    reads += 1;
    value.currentness_evaluated_at = new Date(Date.now() - reads * 1_000).toISOString();
    return transform(value);
  };
}

test("MCP resolver sends only immutable locators and accepts server-derived authority", async () => {
  let request;
  const client = new McpSourceMergeAuthorityClient("mcp-token", async (_url, options) => {
    request = JSON.parse(options.body);
    return { ok: true, json: async () => ({ result: { content: [{ type: "text", text: JSON.stringify(resolvedAuthority()) }] } }) };
  });
  const result = await client.resolve(locator());
  assert.equal(result.derived_by, "source-merge-authority-projection");
  assert.deepEqual(request.params.arguments, {
    decision_id: NO_CEREMONIAL_MERGE_DECISION.replace(/^decision:/, ""),
    work_request: "WR-900", pr_number: 42, head_sha: HEAD,
  });
  assert.equal("authorization" in request.params.arguments, false);
  assert.equal("passport" in request.params.arguments, false);
});

test("actuator plans, re-reads evidence, and conditionally merges the exact SHA", async () => {
  const planLive = liveFetch();
  const plan = await planOrExecute({ command: "plan", locator: locator(), resolveAuthority: canonicalResolver(), token: "token", fetchImpl: planLive.fetchImpl });
  assert.equal(plan.outcome, "eligible");
  assert.equal(planLive.snapshots(), 1);

  const executeLive = liveFetch();
  const result = await planOrExecute({ command: "execute", locator: locator(), resolveAuthority: canonicalResolver(), token: "token", expectedDigest: plan.eligibility_digest, fetchImpl: executeLive.fetchImpl });
  assert.equal(result.outcome, "merged");
  assert.equal(executeLive.snapshots(), 2);
  const merge = executeLive.calls.find(call => call.options.method === "PUT");
  assert.ok(merge);
  assert.deepEqual(JSON.parse(merge.options.body), {
    merge_method: "squash",
    sha: HEAD,
    commit_title: "[source-merge-controller] PR #42",
  });
});

test("automatic invoker discovers one canonical locator and merges without a ceremonial digest handoff", async () => {
  const live = liveFetch();
  const locators = [];
  const result = await autoMergeOnce({
    resolveAuthority: async value => { locators.push(value); return resolvedAuthority(); },
    token: "token", fetchImpl: live.fetchImpl,
  });
  assert.equal(result.outcome, "merged");
  assert.equal(locators.length, 2);
  assert.equal(locators[0].work_request, null);
  assert.equal(locators[0].head_sha, HEAD);
  assert.ok(live.calls.some(call => call.options.method === "PUT"));
});

test("actuator refuses if the eligibility digest changed", async () => {
  const live = liveFetch();
  await assert.rejects(planOrExecute({ command: "execute", locator: locator(), resolveAuthority: canonicalResolver(), token: "token", expectedDigest: `sha256:${"0".repeat(64)}`, fetchImpl: live.fetchImpl }), /eligibility_digest_changed/);
  assert.equal(live.calls.some(call => call.options.method === "PUT"), false);
});

test("actuator re-reads canonical authority and refuses a changed record before merge", async () => {
  const live = liveFetch();
  let reads = 0;
  const resolveAuthority = async () => {
    reads += 1;
    const value = resolvedAuthority();
    if (reads === 2) value.authorized_path_claims = value.authorized_path_claims.slice(0, 1);
    return value;
  };
  const plan = await planOrExecute({ command: "plan", locator: locator(), resolveAuthority: canonicalResolver(), token: "token", fetchImpl: live.fetchImpl });
  await assert.rejects(
    planOrExecute({ command: "execute", locator: locator(), resolveAuthority, token: "token", expectedDigest: plan.eligibility_digest, fetchImpl: live.fetchImpl }),
    /source_merge_evidence_changed/,
  );
  assert.equal(reads, 2);
  assert.equal(live.calls.some(call => call.options.method === "PUT"), false);
});
