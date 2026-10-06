// V5-F05, half one — rule taxonomy, typed applicability, coverage and the
// relation graph, proved case by case.
//
// The suite is organised by the decision it proves, and every decision gets
// both halves. The positive case is asserted FIRST in each section, because a
// rule that only ever refuses cannot be told apart from a rule that is broken:
// the exception has to fire before its five failure shapes mean anything.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5F05Error,
  V5_F05_UNIVERSE_SCHEMA_VERSION,
  V5_F05_COVERAGE_SCHEMA_VERSION,
  V5_F05_COVERAGE_DECISIONS,
  V5_F05_SETTLED_DECISIONS,
  V5_F05_SETTLED_DECISION_IDS,
  V5_F05_FACT_DIMENSIONS,
  V5_F05_RULE_CLASSES,
  V5_F05_RELATIONS,
  V5_F05_UNKNOWN_FACT,
  V5_F05_DELIVERY_MODES,
  V5_F05_CODE_ENFORCED_CONSTRAINT_MODE_EMITTED,
  V5_F05_HISTORICAL_BUCKETS,
  V5_F05_REFUSED_ASSERTION_FIELDS,
  V5_F05_AUTHORITY_INJECTION_FRAGMENTS,
  V5_NO_EFFECTS,
  assertF05DecisionBinding,
  compileRuleUniverse,
  requireCompiledUniverse,
  deriveRuleApplicability,
  projectRuleForModel,
  verifyCoverageReceipt,
  ruleUniversePreimage,
  estimateTokens,
  ruleKernelIntegrationGaps,
  assertRuleKernelIntegrationComplete,
  v5F05DecisionSubsetDigest,
  v5F05RuleKernelPreimage,
  v5F05RuleKernelDigest,
  v5F05RuleKernelCanonicalBytes,
} from "../src/rule-applicability.v5.js";

// The seven source-evidence digests exactly as the reviewed F05 source binding
// carries them. Copied here so drift between the module and the binding is a
// test failure rather than a later discovery.
const REVIEWED_DECISION_BINDING = Object.freeze({
  "Q050.D1": "aceecd2a205cb8e54ba3a25f4d76794755f282347aedff647aa64c6d7849870e",
  "Q051.D1": "a025a6f44b4ce876eedb74f4ddef3cf55a106e65fd8503ac5b9f3f6b10a47e4e",
  "Q064.D1": "37d4a659edb38342b0dbfda64ef0ac6cb86c69bd47cd59bf4edd09f724a971eb",
  "Q065.D1": "97a70bb7fb499e647ace6061a9abb430746615e940beda49b7fbc224d69740b0",
  "Q066.D1": "0841228973232c76214c3bfb286870ca26e6c582b57e7bcc7eeecce0e86e5438",
  "Q068.D1": "b4ed3495d133e30e425dbe17ce207f1aad00de3497ef61a1fd3734a00ce90ec8",
  "Q087.D1": "b5fe67a82599096c8e1d1e2b6c8f83bdd8d82dabd8c1f8e3775c0a74c7b14b1e",
});

// --------------------------------------------------------------- fixtures

const NOW = "2026-09-09T12:00:00Z";
const FRESH_EVIDENCE = "2026-09-09T11:00:00Z";
const A_DAY_BEFORE_NOW = "2026-09-08T12:00:00Z";
const A_SECOND_MORE_THAN_A_DAY = "2026-09-08T11:59:59Z";
const RETRIEVED = "2026-09-09T10:00:00Z";
const DAY_IN_SECONDS = 86400;

const code = fn => {
  try { fn(); } catch (error) { return error instanceof V5F05Error ? error.code : `not-a-V5F05Error:${error}`; }
  return "no-throw";
};

/**
 * A rule's bounded typed source reference. Every mandatory rule must carry one:
 * a control whose text has no named source cannot be told apart from text that
 * arrived in an email.
 */
const prov = (source_record_id, ch) => ({
  source_record_id,
  source_version: 1,
  source_content_digest: `sha256:${ch.repeat(64)}`,
  retrieved_at: RETRIEVED,
});

const worktreeRule = () => ({
  rule_id: "worktree-first",
  version: 3,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { action: ["repo.commit"] },
  control_effect: { control_key: "isolated_worktree", effect: "require" },
  binding_text: "Before the first write to tracked files, take a session-owned worktree.\n"
    + "The canonical checkout is the integration lane only.",
  summary: "worktree per session",
  tests: ["check:worktree-isolated"],
  retirement: { behavior: "permanent_until_superseded" },
  relations: [{ relation: "supersedes", target_rule_id: "legacy-branching", target_version: 1 }],
  // Every removing edge is bounded, not only an exception_to.
  scoped_validity: { action: ["repo.commit"] },
  provenance: prov("r-rule-worktree", "a"),
});

const legacyRule = () => ({
  rule_id: "legacy-branching",
  version: 1,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { action: ["repo.commit"] },
  control_effect: { control_key: "shared_checkout", effect: "require" },
  binding_text: "Branch inside the shared checkout and coordinate by hand.",
  tests: ["check:legacy-branching"],
  retirement: { behavior: "superseded_only" },
  provenance: prov("r-rule-legacy", "b"),
});

const noPhiRule = (evidence = { verified_at: FRESH_EVIDENCE, control_version: "7" },
  { binding_text = "No PHI and no raw patient-level location may enter any payload, "
    + "on any path, for any actor." } = {}) => ({
  rule_id: "no-phi",
  version: 2,
  rule_class: "code_enforced",
  scope: "global",
  owner: "joe",
  mandatory: true,
  trigger: {},
  control_effect: { control_key: "phi_payload", effect: "forbid" },
  // A code_enforced rule may omit binding text under Q066 only if its resulting
  // constraint can stand in for the rule. Nothing here verifies the evidence
  // that would license that, so the text is what gets delivered and the fixture
  // carries it; the no-text variant below is the refusal case.
  ...(binding_text === null ? {} : { binding_text }),
  code_enforcement: {
    implementation_ref: "mcp-server/src/global-boundaries.v5.js:evaluatePrivacyBoundary",
    control_id: "global.no_phi",
    control_version: "7",
    resulting_constraint: "No PHI or raw patient-level location may enter any payload.",
    evidence: evidence === null ? null : {
      verifier_id: evidence.verifier_id ?? "ops.ci",
      verified_at: evidence.verified_at,
      control_version: evidence.control_version,
      implementation_digest: `sha256:${"1".repeat(64)}`,
      evidence_digest: `sha256:${"2".repeat(64)}`,
    },
  },
  tests: ["check:no-phi"],
  retirement: { behavior: "permanent_until_superseded" },
  provenance: prov("r-rule-no-phi", "c"),
});

const sendGateRule = () => ({
  rule_id: "send-gate",
  version: 1,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { action: ["document.send"], audience: ["client"] },
  control_effect: { control_key: "client_send", effect: "require" },
  binding_text: "A client-facing document is reviewed by a second seat before it is sent.",
  tests: ["check:client-send-review"],
  retirement: { behavior: "permanent_until_superseded" },
  provenance: prov("r-rule-send-gate", "d"),
});

const sendGateExceptionRule = () => ({
  rule_id: "send-gate-exception",
  version: 1,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: false,
  trigger: { action: ["document.send"] },
  binding_text: "In development, a send rehearsal does not need the second-seat review.",
  tests: ["check:send-gate-exception"],
  retirement: { behavior: "permanent_until_superseded" },
  relations: [{ relation: "exception_to", target_rule_id: "send-gate", target_version: 1 }],
  scoped_validity: { environment: ["development"] },
  // A REMOVER CARRIES PROVENANCE even though it is not itself mandatory. This
  // rule deletes the mandatory `send-gate` control; before the correction it did
  // so while naming no source record at all, and the kernel-only receipt read
  // coverage_complete with consequential_action_permitted true. Removal
  // capability is mandatory capability, on provenance as on the class table.
  provenance: prov("r-rule-send-gate-exception", "0"),
});

const toneRule = () => ({
  rule_id: "client-review",
  version: 1,
  rule_class: "scoped_judgment",
  scope: "shared",
  owner: "joe",
  mandatory: false,
  trigger: { audience: ["client"] },
  binding_text: "Write to a client the way Joe would: plain, specific, no hedging.",
  no_machine_control_reason: "Only a reader applying context can tell plain from curt.",
  retirement: { behavior: "permanent_until_superseded" },
});

const awayModeRule = () => ({
  rule_id: "away-mode",
  version: 1,
  rule_class: "runtime_state",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { actor_class: ["unattended_run"] },
  control_effect: { control_key: "unattended_dispatch", effect: "forbid" },
  binding_text: "Away mode: unattended dispatch is off until Joe returns.",
  retirement: { behavior: "expires_at", expires_at: "2026-09-08T00:00:00Z" },
  provenance: prov("r-rule-away-mode", "e"),
});

const basePolicy = () => ({
  schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
  universe_version: 4,
  tenant: ORGANIZATION_TENANT_ID,
  completeness: "complete_authoritative_universe",
  declared_actions: ["deal.update", "document.send", "repo.commit"],
  declared_resource_classes: ["deal", "document", "repository"],
  rules: [
    worktreeRule(), legacyRule(), noPhiRule(), sendGateRule(),
    sendGateExceptionRule(), toneRule(), awayModeRule(),
  ],
});

const commitFacts = () => ({
  action: "repo.commit",
  actor_class: "sponsored_agent",
  audience: "internal",
  environment: "isolated_worktree",
  lifecycle_transition: "create",
  resource_class: "repository",
  risk_tier: "consequential",
});

const sendFacts = (overrides = {}) => ({
  action: "document.send",
  actor_class: "verified_partner",
  audience: "client",
  environment: "production",
  lifecycle_transition: "send",
  resource_class: "document",
  risk_tier: "consequential",
  ...overrides,
});

const derive = (policy, facts, extra = {}) => deriveRuleApplicability({
  tenant: ORGANIZATION_TENANT_ID,
  universe: compileRuleUniverse(policy),
  facts,
  now: NOW,
  ...extra,
});

/** Derive against a universe OBJECT rather than a policy, for the forgery cases. */
const deriveWith = (universe, facts = commitFacts()) => deriveRuleApplicability({
  tenant: ORGANIZATION_TENANT_ID, universe, facts, now: NOW,
});

// ------------------------------------------- the reviewed decision binding

test("the seven reviewed decisions are bound verbatim and their digest is derived", () => {
  assert.deepEqual([...V5_F05_SETTLED_DECISION_IDS], Object.keys(REVIEWED_DECISION_BINDING).sort());
  for (const [id, expected] of Object.entries(REVIEWED_DECISION_BINDING)) {
    assert.equal(V5_F05_SETTLED_DECISIONS[id].source_evidence_digest, expected);
  }
  assert.equal(assertF05DecisionBinding({
    decision_subset_digest: v5F05DecisionSubsetDigest(),
    decisions: Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
      .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }])),
  }), true);
});

test("a drifted, short or over-long decision subset refuses in both directions", () => {
  const drifted = Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
    .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }]));
  drifted["Q050.D1"] = { source_evidence_digest: "0".repeat(64) };
  assert.equal(code(() => assertF05DecisionBinding({ decisions: drifted })), "decision_binding_drift");

  const short = { ...drifted };
  delete short["Q087.D1"];
  short["Q050.D1"] = { source_evidence_digest: REVIEWED_DECISION_BINDING["Q050.D1"] };
  assert.equal(code(() => assertF05DecisionBinding({ decisions: short })), "decision_binding_drift");

  assert.equal(code(() => assertF05DecisionBinding({
    decision_subset_digest: `sha256:${"9".repeat(64)}`,
    decisions: Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
      .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }])),
  })), "decision_binding_drift");
});

// ----------------------------------------- Q051, the six rule classes

test("Q051 the class table is closed and each class fixes its own enforcement", () => {
  const kernel = v5F05RuleKernelPreimage();
  assert.deepEqual(kernel.rule_classes.map(entry => entry.rule_class), [...V5_F05_RULE_CLASSES]);
  const byClass = Object.fromEntries(kernel.rule_classes.map(entry => [entry.rule_class, entry]));
  assert.equal(byClass.code_enforced.enforcement, "code_control");
  assert.equal(byClass.code_enforced.binding_text_required, false);
  assert.equal(byClass.scoped_judgment.may_be_mandatory, false);
  assert.equal(byClass.preference.may_be_mandatory, false);
  assert.equal(byClass.runtime_state.must_expire, true);
});

test("Q051 a judgment or preference rule may not claim to be mandatory", () => {
  const policy = basePolicy();
  policy.rules.push({
    ...toneRule(), rule_id: "tone-as-control", mandatory: true,
    control_effect: { control_key: "tone", effect: "require" },
  });
  assert.equal(code(() => compileRuleUniverse(policy)), "judgment_rule_cannot_be_mandatory");
});

test("Q051 every required piece of rule metadata is required, one refusal each", () => {
  const cases = [
    ["missing_binding_text", rule => { delete rule.binding_text; }, "worktree-first"],
    ["missing_test_ref", rule => { rule.tests = []; }, "worktree-first"],
    ["missing_control_effect", rule => { delete rule.control_effect; }, "worktree-first"],
    ["unused_control_effect", rule => { rule.mandatory = false; }, "worktree-first"],
    ["unused_code_enforcement", rule => {
      rule.code_enforcement = noPhiRule().code_enforcement;
    }, "worktree-first"],
    ["missing_no_machine_control_reason", rule => { delete rule.no_machine_control_reason; },
      "client-review"],
    ["missing_code_enforcement", rule => { delete rule.code_enforcement; }, "no-phi"],
    // Q050's per-rule source provenance, and the slot Q068 needs on the rule
    // path: a mandatory rule with no named source is authority with no text of
    // record behind it.
    ["missing_rule_provenance", rule => { delete rule.provenance; }, "send-gate"],
  ];
  for (const [expected, mutate, rule_id] of cases) {
    const policy = basePolicy();
    mutate(policy.rules.find(rule => rule.rule_id === rule_id));
    assert.equal(code(() => compileRuleUniverse(policy)), expected, expected);
  }
});

test("Q051 a runtime_state rule must expire, and an expired one is retired at derive", () => {
  const policy = basePolicy();
  const away = policy.rules.find(rule => rule.rule_id === "away-mode");
  away.retirement = { behavior: "permanent_until_superseded" };
  assert.equal(code(() => compileRuleUniverse(policy)), "runtime_state_must_expire");

  const receipt = derive(basePolicy(), { ...commitFacts(), actor_class: "unattended_run" });
  assert.deepEqual(receipt.retired.map(entry => entry.rule_id), ["away-mode"]);
  assert.equal(receipt.retired[0].reason_id, "retired_by_expiry");
  assert.ok(!receipt.effective.some(entry => entry.rule_id === "away-mode"));
});

// ------------------------------- Q064, typed applicability and coverage

test("Q064 typed facts decide applicability, and the receipt enumerates the whole universe", () => {
  const receipt = derive(basePolicy(), commitFacts());

  assert.equal(receipt.schema_version, V5_F05_COVERAGE_SCHEMA_VERSION);
  assert.equal(receipt.decision, "allow");
  assert.equal(receipt.reason_id, "coverage_complete");
  assert.equal(receipt.coverage_complete, true);
  assert.equal(receipt.consequential_action_permitted, true);
  assert.deepEqual(receipt.blocking_reasons, []);
  assert.deepEqual(receipt.effects, V5_NO_EFFECTS);

  assert.deepEqual(receipt.effective.map(e => e.rule_id), ["no-phi", "worktree-first"]);
  assert.deepEqual(receipt.superseded.map(e => e.rule_id), ["legacy-branching"]);
  assert.deepEqual(receipt.not_applicable.map(e => e.rule_id).sort(),
    ["client-review", "send-gate", "send-gate-exception"]);
  assert.deepEqual(receipt.retired.map(e => e.rule_id), ["away-mode"]);

  // The partition, asserted rather than trusted: an unlisted rule is
  // indistinguishable from an absent one.
  const buckets = [
    ...receipt.effective, ...receipt.possibly_binding, ...receipt.not_applicable,
    ...receipt.retired, ...receipt.superseded, ...receipt.overridden,
    ...receipt.suppressed_by_exception,
  ].map(entry => entry.rule_id).sort();
  assert.deepEqual(buckets, [...receipt.universe_rule_ids].sort());
  assert.equal(new Set(buckets).size, buckets.length);

  // The reason each applicable rule applied, dimension by dimension.
  const worktree = receipt.effective.find(e => e.rule_id === "worktree-first");
  assert.deepEqual(worktree.matched, [{ dimension: "action", fact_value: "repo.commit",
    matched: ["repo.commit"] }]);
  assert.equal(receipt.effective.find(e => e.rule_id === "no-phi").reason_id, "universal_trigger");
  assert.equal(receipt.not_applicable.find(e => e.rule_id === "client-review").mismatch.dimension,
    "audience");
});

test("Q064/Q065 an unknown fact makes a rule POSSIBLY binding, never absent", () => {
  const facts = commitFacts();
  delete facts.action;
  const receipt = derive(basePolicy(), facts);

  assert.deepEqual(receipt.possibly_binding.map(e => e.rule_id).sort(),
    ["legacy-branching", "send-gate-exception", "worktree-first"]);
  assert.equal(receipt.possibly_binding[0].reason_id, "required_fact_unknown");
  assert.deepEqual(receipt.unknown_facts, [{ dimension: "action", reason_id: "fact_absent" }]);
  assert.equal(receipt.coverage_complete, false);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.equal(receipt.read_only_exploration_permitted, true);
  assert.ok(receipt.blocking_reasons.includes("possible_binding_rule_undecided"));
  assert.ok(receipt.blocking_reasons.includes("typed_facts_unknown"));

  // A possible binding rule is DELIVERED, with its text, exactly like an
  // effective one — that is the half Q065 is about.
  const delivered = receipt.delivery.find(entry => entry.rule_id === "worktree-first");
  assert.equal(delivered.bucket, "possibly_binding");
  assert.equal(delivered.mode, "full_binding_text");
  assert.equal(delivered.omissible, false);
});

test("Q064 the explicit unknown sentinel reads the same as an absent fact", () => {
  const receipt = derive(basePolicy(), { ...commitFacts(), action: V5_F05_UNKNOWN_FACT });
  assert.deepEqual(receipt.unknown_facts,
    [{ dimension: "action", reason_id: "fact_declared_unknown" }]);
  assert.ok(receipt.possibly_binding.some(e => e.rule_id === "worktree-first"));
});

test("Q064 a definite mismatch settles a rule even while another dimension is unknown", () => {
  const facts = sendFacts({ audience: "internal" });
  delete facts.environment;
  const receipt = derive(basePolicy(), facts);
  // send-gate needs audience=client, which is KNOWN and wrong; the unknown
  // environment cannot rescue it into "possibly binding".
  assert.ok(receipt.not_applicable.some(e => e.rule_id === "send-gate"));
  assert.ok(!receipt.possibly_binding.some(e => e.rule_id === "send-gate"));
});

test("Q064 an action outside the declared vocabulary is unknown, not inapplicable", () => {
  const facts = { ...commitFacts(), action: "deal.close" };
  const receipt = derive(basePolicy(), facts);
  assert.deepEqual(receipt.unknown_facts,
    [{ dimension: "action", reason_id: "fact_outside_declared_vocabulary", value: "deal.close" }]);
  assert.ok(receipt.possibly_binding.some(e => e.rule_id === "worktree-first"));
  assert.equal(receipt.consequential_action_permitted, false);
});

test("Q064 an unreadable typed fact is a contract violation, not a policy answer", () => {
  assert.equal(code(() => derive(basePolicy(), { ...commitFacts(), risk_tier: "spicy" })),
    "unknown_risk_tier");
  assert.equal(code(() => derive(basePolicy(), { ...commitFacts(), audience: 7 })), "invalid_shape");
  assert.equal(code(() => derive(basePolicy(), { ...commitFacts(), weather: "fine" })), "unknown_field");
});

test("Q064 a partial universe cannot support a consequential action", () => {
  const policy = basePolicy();
  policy.completeness = "partial_unknown_coverage";
  const receipt = derive(policy, commitFacts());
  assert.equal(receipt.coverage_complete, false);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.deepEqual(receipt.blocking_reasons, ["universe_coverage_unknown"]);
  assert.equal(receipt.read_only_exploration_permitted, true);
});

// ------------------------- Q064, semantic retrieval adds and never removes

test("Q064 semantic retrieval adds guidance and cannot remove a control", () => {
  const withoutRetrieval = derive(basePolicy(), sendFacts());
  const withRetrieval = derive(basePolicy(), sendFacts(), {
    semantic_candidates: [
      { rule_id: "worktree-first", reason: "the task mentions a commit", similarity: 0.71 },
      { rule_id: "client-review", reason: "already bound, restated by retrieval" },
    ],
  });

  // The effective set is byte-identical: retrieval is read after it is final.
  assert.deepEqual(withRetrieval.effective.map(e => e.rule_id),
    withoutRetrieval.effective.map(e => e.rule_id));
  assert.equal(withRetrieval.semantic_may_remove_controls, false);

  const addition = withRetrieval.semantic_additions.find(e => e.rule_id === "worktree-first");
  assert.equal(addition.elevates_to_control, false);
  assert.equal(addition.delivered_as, "guidance_only");
  assert.equal(addition.omissible, true);
  assert.deepEqual(withRetrieval.semantic_reinforcements.map(e => e.rule_id), ["client-review"]);
});

test("Q064 retrieval that omits a bound control changes nothing about it", () => {
  const receipt = derive(basePolicy(), sendFacts(), {
    semantic_candidates: [{ rule_id: "client-review", reason: "tone" }],
  });
  // send-gate was never retrieved and is still effective and still delivered.
  assert.ok(receipt.effective.some(e => e.rule_id === "send-gate"));
  assert.ok(receipt.delivery.some(e => e.rule_id === "send-gate" && e.mode === "full_binding_text"));
});

test("Q064 retrieved guidance from a rule that no longer binds is labelled historical", () => {
  // Retrieval may surface a rule the system has RETIRED or superseded — that is
  // useful, and the bucket said which. What it did not say is what the bucket
  // MEANS once the full text is in front of a model: the same paragraph reads
  // as an instruction whether or not the rule still stands.
  const receipt = derive(basePolicy(), commitFacts(), {
    semantic_candidates: [
      { rule_id: "away-mode", reason: "the thread mentions unattended dispatch" },
      { rule_id: "legacy-branching", reason: "the thread mentions the shared checkout" },
      { rule_id: "send-gate", reason: "the thread mentions a client send" },
    ],
  });
  const byId = Object.fromEntries(receipt.semantic_additions.map(e => [e.rule_id, e]));

  assert.equal(byId["away-mode"].bucket, "retired");
  assert.equal(byId["away-mode"].historical, true);
  assert.equal(byId["away-mode"].authority_state, "historical_non_authority");
  assert.equal(byId["legacy-branching"].bucket, "superseded");
  assert.equal(byId["legacy-branching"].historical, true);
  assert.equal(byId["legacy-branching"].authority_state, "historical_non_authority");

  // A rule the typed facts merely ruled out never bound here at all, so it is
  // labelled non-authority guidance rather than history.
  assert.equal(byId["send-gate"].bucket, "not_applicable");
  assert.equal(byId["send-gate"].historical, false);
  assert.equal(byId["send-gate"].authority_state, "non_authority_guidance");

  // None of them is elevated, and no rule lifecycle is decided here: `bucket`
  // is the only input to the label.
  assert.ok(receipt.semantic_additions.every(e => e.elevates_to_control === false
    && e.delivered_as === "guidance_only"));
  const preimage = v5F05RuleKernelPreimage();
  assert.deepEqual([...V5_F05_HISTORICAL_BUCKETS],
    ["overridden", "retired", "superseded", "suppressed_by_exception"]);
  assert.deepEqual(preimage.historical_buckets, [...V5_F05_HISTORICAL_BUCKETS]);
  assert.equal(preimage.semantic_addition_from_historical_bucket_is_labelled_non_authority, true);
});

test("Q064 retrieval cannot suppress, and cannot invent a rule", () => {
  assert.equal(code(() => derive(basePolicy(), sendFacts(), {
    semantic_candidates: [{ rule_id: "send-gate", reason: "not needed here", suppress: true }],
  })), "caller_assertion_field_refused");
  assert.equal(code(() => derive(basePolicy(), sendFacts(), {
    semantic_candidates: [{ rule_id: "invented-rule", reason: "felt relevant" }],
  })), "semantic_candidate_unknown_rule");
});

// ------------------------------------- Q087, the relation graph

test("Q087 an exception fires inside its scope, and only inside it", () => {
  const inside = derive(basePolicy(), sendFacts({ environment: "development" }));
  assert.deepEqual(inside.suppressed_by_exception.map(e => e.rule_id), ["send-gate"]);
  assert.equal(inside.suppressed_by_exception[0].by_rule_id, "send-gate-exception");
  assert.equal(inside.suppressed_by_exception[0].reason_id, "removed_by_exception_to");
  assert.ok(!inside.effective.some(e => e.rule_id === "send-gate"));
  assert.equal(inside.consequential_action_permitted, true);

  const outside = derive(basePolicy(), sendFacts({ environment: "production" }));
  assert.deepEqual(outside.suppressed_by_exception, []);
  assert.ok(outside.effective.some(e => e.rule_id === "send-gate"));
});

test("Q087 an exception whose own scope is unknown removes nothing and blocks", () => {
  const facts = sendFacts();
  delete facts.environment;
  const receipt = derive(basePolicy(), facts);
  assert.ok(receipt.effective.some(e => e.rule_id === "send-gate"));
  assert.deepEqual(receipt.pending_relations, [{
    rule_id: "send-gate-exception", relation: "exception_to", target_rule_id: "send-gate",
    reason_id: "removal_scope_unknown", undecided_dimensions: ["environment"],
  }]);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.ok(receipt.blocking_reasons.includes("relation_resolution_pending"));
});

test("Q087 a superseded rule leaves the effective set and says who removed it", () => {
  const receipt = derive(basePolicy(), commitFacts());
  assert.deepEqual(receipt.superseded, [{
    rule_id: "legacy-branching", version: 1, rule_class: "workflow", scope: "shared", owner: "joe",
    mandatory: true, reason_id: "removed_by_supersedes", by_rule_id: "worktree-first",
    matched: [{ dimension: "action", fact_value: "repo.commit", matched: ["repo.commit"] }],
  }]);
});

test("Q087 a dangling, drifted, self or cyclic relation refuses at compile", () => {
  const dangling = basePolicy();
  dangling.rules.find(r => r.rule_id === "worktree-first").relations =
    [{ relation: "supersedes", target_rule_id: "never-existed", target_version: 1 }];
  assert.equal(code(() => compileRuleUniverse(dangling)), "dangling_relation");

  const drifted = basePolicy();
  drifted.rules.find(r => r.rule_id === "worktree-first").relations =
    [{ relation: "supersedes", target_rule_id: "legacy-branching", target_version: 9 }];
  assert.equal(code(() => compileRuleUniverse(drifted)), "relation_version_mismatch");

  const self = basePolicy();
  self.rules.find(r => r.rule_id === "worktree-first").relations =
    [{ relation: "supersedes", target_rule_id: "worktree-first", target_version: 3 }];
  assert.equal(code(() => compileRuleUniverse(self)), "self_relation");

  const cyclic = basePolicy();
  const legacy = cyclic.rules.find(r => r.rule_id === "legacy-branching");
  legacy.relations = [{ relation: "overrides", target_rule_id: "worktree-first", target_version: 3 }];
  legacy.scoped_validity = { action: ["repo.commit"] };
  assert.equal(code(() => compileRuleUniverse(cyclic)), "relation_cycle");
});

test("Q087 identical triggers that require and forbid one control refuse at compile", () => {
  const policy = basePolicy();
  policy.rules.push({
    rule_id: "no-worktree",
    version: 1, rule_class: "workflow", scope: "shared", owner: "dell", mandatory: true,
    trigger: { action: ["repo.commit"] },
    control_effect: { control_key: "isolated_worktree", effect: "forbid" },
    binding_text: "Commit in the shared checkout.",
    tests: ["check:no-worktree"],
    retirement: { behavior: "permanent_until_superseded" },
    provenance: prov("r-rule-no-worktree", "f"),
  });
  assert.equal(code(() => compileRuleUniverse(policy)), "unresolved_binding_conflict");
});

test("Q087 a conflict that only appears at fact time refuses the receipt, and no model picks", () => {
  const policy = basePolicy();
  policy.rules.push({
    rule_id: "send-freeze",
    version: 1, rule_class: "workflow", scope: "shared", owner: "joe", mandatory: true,
    // A DIFFERENT trigger, so the compiler cannot see the overlap; the facts can.
    trigger: { risk_tier: ["consequential"] },
    control_effect: { control_key: "client_send", effect: "forbid" },
    binding_text: "Client sends are frozen during the migration window.",
    tests: ["check:send-freeze"],
    retirement: { behavior: "permanent_until_superseded" },
    provenance: prov("r-rule-send-freeze", "7"),
  });
  const receipt = derive(policy, sendFacts());
  assert.equal(receipt.decision, "refuse");
  assert.equal(receipt.reason_id, "unresolved_binding_conflict");
  assert.deepEqual(receipt.binding_conflicts, [{
    control_key: "client_send", reason_id: "unresolved_binding_conflict",
    require_rule_ids: ["send-gate"], forbid_rule_ids: ["send-freeze"], resolved_by_model: false,
  }]);
  assert.equal(receipt.model_resolves_conflicts, false);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.equal(receipt.read_only_exploration_permitted, false);
});

test("Q087 a removing edge with no scoped validity is a silent repeal, whatever it is called", () => {
  const exception = basePolicy();
  delete exception.rules.find(r => r.rule_id === "send-gate-exception").scoped_validity;
  assert.equal(code(() => compileRuleUniverse(exception)), "removing_edge_without_scoped_validity");

  // The leg that used to compile: `overrides` and `supersedes` were bounded by
  // nothing at all, so the word on the edge decided whether an unbounded repeal
  // was refused. All three relations remove; all three now need a bound.
  for (const relation of ["supersedes", "overrides"]) {
    const policy = basePolicy();
    const worktree = policy.rules.find(r => r.rule_id === "worktree-first");
    worktree.relations = [{ relation, target_rule_id: "legacy-branching", target_version: 1 }];
    delete worktree.scoped_validity;
    assert.equal(code(() => compileRuleUniverse(policy)),
      "removing_edge_without_scoped_validity", relation);
  }

  // ...and the mirror: a bound on a rule that removes nothing is unread policy.
  const unused = basePolicy();
  unused.rules.find(r => r.rule_id === "client-review").scoped_validity = { audience: ["client"] };
  assert.equal(code(() => compileRuleUniverse(unused)), "unused_scoped_validity");
});

// --------------------- Q087/Q064 the removal channel that deleted controls
//
// The reviewer's reproduction, run exactly as filed. Before this correction it
// compiled clean: a `preference` rule — the class Q051 forbids from being
// mandatory because "prose must not claim enforcement code does not provide" —
// held an `overrides` edge against a mandatory `code_enforced`-adjacent control
// in a different scope owned by a different partner, with a universal trigger.
// `send-gate` left the effective set, appeared in no blocking reason and in no
// conflict, and the receipt read coverage_complete with
// consequential_action_permitted true.
//
// Three independent holes composed, so the reproduction is peeled one layer at
// a time and each layer must refuse on its own.

const joePrefersNoReview = (overrides = {}) => ({
  rule_id: "joe-prefers-no-review", version: 1,
  rule_class: "preference", scope: "joe-personal", owner: "dell", mandatory: false,
  trigger: {},                                   // universal
  binding_text: "Joe does not want a second seat on his own sends.",
  no_machine_control_reason: "Only a person can judge tone.",
  retirement: { behavior: "permanent_until_superseded" },
  relations: [{ relation: "overrides", target_rule_id: "send-gate", target_version: 1 }],
  // Present so each layer below refuses on the hole it is peeling rather than
  // on a missing source; the missing-source case is its own test.
  provenance: prov("r-rule-joe-prefers-no-review", "1"),
  ...overrides,
});

test("Q087 the reviewer's preference-deletes-a-mandatory-control reproduction refuses", () => {
  // Layer one: an unbounded removing edge.
  const unbounded = basePolicy();
  unbounded.rules.push(joePrefersNoReview());
  assert.equal(code(() => compileRuleUniverse(unbounded)),
    "removing_edge_without_scoped_validity");

  // Layer two: bounded, but the class may not remove a mandatory control. Q051
  // denies preference and scoped_judgment the power to REQUIRE; deleting is
  // strictly stronger, so the same two classes are denied that too.
  const bounded = basePolicy();
  bounded.rules.push(joePrefersNoReview({ scoped_validity: { audience: ["client"] } }));
  assert.equal(code(() => compileRuleUniverse(bounded)),
    "removing_class_cannot_remove_mandatory_control");
  for (const rule_class of ["preference", "scoped_judgment"]) {
    const policy = basePolicy();
    policy.rules.push(joePrefersNoReview({
      rule_class, scoped_validity: { audience: ["client"] },
    }));
    assert.equal(code(() => compileRuleUniverse(policy)),
      "removing_class_cannot_remove_mandatory_control", rule_class);
  }

  // Layer three: a removal-capable class, still owned elsewhere and scoped
  // elsewhere. No dominance order is invented in either direction — it fails
  // closed and names the seam that does not exist.
  const crossOwner = basePolicy();
  crossOwner.rules.push(joePrefersNoReview({
    rule_id: "dell-workflow", rule_class: "workflow", owner: "dell", scope: "shared",
    tests: ["check:dell-workflow"], scoped_validity: { audience: ["client"] },
    provenance: prov("r-rule-dell", "f"),
  }));
  delete crossOwner.rules.at(-1).no_machine_control_reason;
  assert.equal(code(() => compileRuleUniverse(crossOwner)), "missing_relation_authority");

  const crossScope = basePolicy();
  crossScope.rules.push(joePrefersNoReview({
    rule_id: "joe-personal-workflow", rule_class: "workflow", owner: "joe", scope: "joe-personal",
    tests: ["check:joe-personal-workflow"], scoped_validity: { audience: ["client"] },
    provenance: prov("r-rule-joe-personal", "f"),
  }));
  delete crossScope.rules.at(-1).no_machine_control_reason;
  const crossScopeError = (() => {
    try { compileRuleUniverse(crossScope); } catch (error) { return error; }
    return null;
  })();
  assert.equal(crossScopeError.code, "missing_relation_authority");
  assert.equal(crossScopeError.detail.missing_seam, "no_relation_authority_grant_verifier");
  assert.deepEqual(crossScopeError.detail.remover, { owner: "joe", scope: "joe-personal" });
  assert.deepEqual(crossScopeError.detail.target, { owner: "joe", scope: "shared" });

  // The named gap is disclosed rather than implied, so an authorized
  // cross-scope supersession is visibly unrepresentable rather than silently
  // absent.
  assert.ok(ruleKernelIntegrationGaps()
    .some(gap => gap.gap === "no_relation_authority_grant_verifier" && gap.landed === false));
});

// ------------- removal-capability parity on PROVENANCE, not only on the class
//
// The correction reasoned its way to removal-capability parity for the class
// table — "removing a mandatory control is strictly stronger than declaring
// one" — and then required provenance only when `mandatory: true`. So a
// NON-MANDATORY rule could delete a mandatory control while naming no source
// record, version or digest, which is the exact state the refusal message calls
// out as indistinguishable from text that arrived in an email. The shipped
// exception fixture WAS one: it removed the mandatory `send-gate` and the
// kernel-only receipt read suppressed_by_exception with
// consequential_action_permitted true and no signal of any kind.

test("Q087 any rule that removes another must name the source of its own text", () => {
  // The positive first: the exception fixture now carries a real typed source
  // reference, and its removal still fires exactly as before.
  const inside = derive(basePolicy(), sendFacts({ environment: "development" }));
  assert.deepEqual(inside.suppressed_by_exception.map(e => e.rule_id), ["send-gate"]);
  assert.equal(inside.consequential_action_permitted, true);

  // The negative, on the same fixture: take the source away and the universe
  // does not compile, so no receipt can report the removal at all.
  for (const rule_id of ["send-gate-exception", "worktree-first"]) {
    const policy = basePolicy();
    delete policy.rules.find(r => r.rule_id === rule_id).provenance;
    const error = (() => {
      try { compileRuleUniverse(policy); } catch (caught) { return caught; }
      return null;
    })();
    assert.equal(error.code, "missing_rule_provenance", rule_id);
    assert.deepEqual(error.detail.removing_edges,
      rule_id === "send-gate-exception" ? ["exception_to"] : ["supersedes"], rule_id);
  }

  // send-gate-exception is NOT mandatory, which is the whole point: the old
  // condition read `mandatory === true` and this rule passed it.
  assert.equal(sendGateExceptionRule().mandatory, false);
  const error = (() => {
    const policy = basePolicy();
    delete policy.rules.find(r => r.rule_id === "send-gate-exception").provenance;
    try { compileRuleUniverse(policy); } catch (caught) { return caught; }
    return null;
  })();
  assert.equal(error.detail.mandatory, false);

  // All three relation kinds, since all three remove.
  for (const relation of ["supersedes", "overrides", "exception_to"]) {
    const policy = basePolicy();
    const exception = policy.rules.find(r => r.rule_id === "send-gate-exception");
    exception.relations = [{ relation, target_rule_id: "send-gate", target_version: 1 }];
    delete exception.provenance;
    assert.equal(code(() => compileRuleUniverse(policy)), "missing_rule_provenance", relation);
  }

  // And it is stated in the hashed kernel projection, not only in the refusal.
  const preimage = v5F05RuleKernelPreimage();
  assert.equal(preimage.removing_edge_requires_source_provenance, true);
  assert.equal(preimage.removing_rule_requires_source_provenance, true);
});

test("Q087 a same-owner same-scope removal follows policy, inside its bound and nowhere else", () => {
  // The positive: a workflow rule of the same owner and scope removes a
  // mandatory control where its scoped validity matches...
  const policy = basePolicy();
  policy.rules.push({
    rule_id: "send-gate-superseded-by", version: 1, rule_class: "workflow",
    scope: "shared", owner: "joe", mandatory: false,
    trigger: { action: ["document.send"] },
    binding_text: "Client sends to an internal audience no longer take a second seat.",
    tests: ["check:send-gate-successor"],
    retirement: { behavior: "permanent_until_superseded" },
    relations: [{ relation: "supersedes", target_rule_id: "send-gate", target_version: 1 }],
    scoped_validity: { environment: ["staging"] },
    provenance: prov("r-rule-successor", "8"),
  });

  const inside = derive(policy, sendFacts({ environment: "staging" }));
  assert.deepEqual(inside.superseded.map(e => e.rule_id), ["send-gate"]);
  assert.ok(!inside.effective.some(e => e.rule_id === "send-gate"));

  // ...and does NOT where it does not. The skipped edge is recorded, not erased.
  const outside = derive(policy, sendFacts({ environment: "production" }));
  assert.ok(outside.effective.some(e => e.rule_id === "send-gate"));
  assert.deepEqual(outside.superseded, []);
  const skipped = outside.skipped_relations.find(e => e.target_rule_id === "send-gate"
    && e.rule_id === "send-gate-superseded-by");
  assert.equal(skipped.reason_id, "removal_scope_not_matched");
  assert.equal(skipped.mismatch.dimension, "environment");

  // An unknown removal scope blocks rather than resolving either way.
  const unknownFacts = sendFacts();
  delete unknownFacts.environment;
  const unknown = derive(policy, unknownFacts);
  assert.ok(unknown.effective.some(e => e.rule_id === "send-gate"));
  assert.ok(unknown.pending_relations.some(e => e.rule_id === "send-gate-superseded-by"
    && e.reason_id === "removal_scope_unknown"));
  assert.equal(unknown.consequential_action_permitted, false);
});

test("Q087 a supersession chain is one pass, and the edge that did not fire is in the receipt", () => {
  // A supersedes B, B supersedes C. A removes B; B is then not standing, so
  // B's removal of C never fires and C returns to the effective set. The
  // direction is fail-safe — an extra control, never a missing one — and the
  // point of this test is that the receipt SAYS so instead of erasing it.
  const chain = () => ({
    rule_class: "workflow", scope: "shared", owner: "joe", mandatory: false,
    trigger: { action: ["document.send"] },
    tests: ["check:chain"],
    retirement: { behavior: "permanent_until_superseded" },
    scoped_validity: { action: ["document.send"] },
    provenance: prov("r-rule-chain", "2"),
  });
  const policy = basePolicy();
  policy.rules.push(
    { ...chain(), rule_id: "chain-a", version: 1, binding_text: "A, the newest.",
      relations: [{ relation: "supersedes", target_rule_id: "chain-b", target_version: 1 }] },
    { ...chain(), rule_id: "chain-b", version: 1, binding_text: "B, the middle.",
      relations: [{ relation: "supersedes", target_rule_id: "chain-c", target_version: 1 }] },
    { ...chain(), rule_id: "chain-c", version: 1, binding_text: "C, the oldest." },
  );
  // chain-c removes nothing, so it carries no bound; an unread bound refuses.
  delete policy.rules.at(-1).scoped_validity;

  const receipt = derive(policy, sendFacts());
  assert.deepEqual(receipt.superseded.map(e => e.rule_id), ["chain-b"]);
  assert.ok(receipt.effective.some(e => e.rule_id === "chain-c"));
  assert.deepEqual(receipt.skipped_relations.filter(e => e.rule_id === "chain-b"), [{
    rule_id: "chain-b", relation: "supersedes", target_rule_id: "chain-c",
    reason_id: "remover_not_standing", remover_state: "superseded",
  }]);
  assert.equal(receipt.removal_is_single_pass_in_compiler_order, true);
});

test("Q087 an edge whose target is not effective is recorded rather than dropped", () => {
  // send-gate-exception points at send-gate, which the commit facts rule out.
  const receipt = derive(basePolicy(), commitFacts());
  const skipped = receipt.skipped_relations
    .find(e => e.rule_id === "send-gate-exception" && e.target_rule_id === "send-gate");
  assert.equal(skipped.reason_id, "target_not_effective");
  assert.equal(skipped.target_state, "not_applicable");
  // Never a blocking reason: the direction is always an extra control.
  assert.ok(!receipt.blocking_reasons.includes("relation_resolution_pending"));
  assert.equal(receipt.consequential_action_permitted, true);
});

// ---------------------------------- Q066, what the model actually receives

test("Q066 an interpreted rule arrives with its full binding text, not its summary", () => {
  const universe = compileRuleUniverse(basePolicy());
  const projected = projectRuleForModel({ universe, rule_id: "worktree-first", now: NOW });
  assert.equal(projected.mode, "full_binding_text");
  assert.equal(projected.delivered, true);
  assert.equal(projected.binding_text, worktreeRule().binding_text);
  assert.equal(projected.summary_is_navigation_only, true);
  assert.equal(projected.resulting_constraint, null);
  assert.equal(projected.estimated_tokens, estimateTokens(worktreeRule().binding_text));
});

// --------- Q066, code-enforcement evidence is a claim, and it says so
//
// `code_enforcement.evidence` is five strings supplied by whoever supplied the
// rule. `verifier_id` is bound to no authority; `implementation_digest` and
// `evidence_digest` are compared to nothing, because there is nothing here to
// compare them to. So the kernel does NOT deliver a resulting constraint in
// place of the rule on that basis. It delivers the full binding text and
// stamps the caller's claim `evidence_verified_by_kernel: false`.

const swapNoPhi = (policy, ...args) => {
  policy.rules[policy.rules.findIndex(r => r.rule_id === "no-phi")] = noPhiRule(...args);
  return policy;
};

test("Q066 a code_enforced rule is delivered as full binding text, never as a bare constraint", () => {
  const universe = compileRuleUniverse(basePolicy());
  const projected = projectRuleForModel({ universe, rule_id: "no-phi", now: NOW });

  assert.equal(projected.mode, "full_binding_text");
  assert.equal(projected.reason_id, "model_interprets_this_rule");
  assert.equal(projected.binding_text, noPhiRule().binding_text);
  // The constraint is NOT the delivery. It rides in the claim, labelled.
  assert.equal(projected.resulting_constraint, null);
  assert.equal(projected.code_enforcement_claim.claimed_resulting_constraint,
    "No PHI or raw patient-level location may enter any payload.");
  assert.equal(projected.code_enforcement_claim.control_id, "global.no_phi");
  assert.equal(projected.code_enforcement_claim.evidence_age_seconds, 3600);

  // The one fact a downstream reader must not lose.
  assert.equal(projected.evidence_verified_by_kernel, false);
  assert.equal(projected.code_enforcement_claim.evidence_verified_by_kernel, false);
  assert.equal(projected.code_enforcement_claim.verifier_trusted_by_kernel, false);
  assert.equal(V5_F05_CODE_ENFORCED_CONSTRAINT_MODE_EMITTED, false);
  assert.ok(V5_F05_DELIVERY_MODES.includes("code_enforced_constraint"));
});

test("Q066 no delivery on any path carries the constraint-only mode", () => {
  for (const facts of [commitFacts(), sendFacts()]) {
    const receipt = derive(basePolicy(), facts, {
      semantic_candidates: [{ rule_id: "no-phi", reason: "PHI came up in the thread" }],
    });
    assert.ok(receipt.delivery.every(entry => entry.mode !== "code_enforced_constraint"));
    assert.ok(receipt.semantic_additions.every(e => e.mode !== "code_enforced_constraint"));
    assert.equal(receipt.code_enforcement_evidence_verified_by_kernel, false);
  }
});

test("Q066 a code_enforced rule with no binding text and no trusted verifier refuses", () => {
  // The honest end of the fallback: with no verifier and no text, there is
  // nothing deliverable, so it fails closed rather than shipping a constraint
  // backed by an unverified claim.
  const policy = swapNoPhi(basePolicy(),
    { verified_at: FRESH_EVIDENCE, control_version: "7" }, { binding_text: null });
  const receipt = derive(policy, commitFacts());
  assert.equal(receipt.decision, "refuse");
  assert.deepEqual(receipt.delivery_refusals,
    [{ rule_id: "no-phi", reason_id: "code_enforcement_unverified_and_no_binding_text" }]);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.equal(receipt.read_only_exploration_permitted, false);

  // And the gap text says exactly this, rather than the false claim it carried:
  // it used to say every such rule "fails closed to binding text", which was
  // wrong twice over — it refused rather than falling back, and any universe
  // carrying fabricated evidence was delivered as a code_enforced_constraint.
  const gap = ruleKernelIntegrationGaps().find(g => g.gap === "no_code_enforcement_verifier");
  assert.ok(gap.what.includes("code_enforcement_unverified_and_no_binding_text"));
  assert.ok(gap.what.includes("FULL BINDING TEXT"));
  assert.ok(gap.what.includes("code_enforced_constraint is never emitted"));
});

test("Q066 fabricated current evidence from an unknown verifier elides nothing", () => {
  // The attack the old delivery rule admitted: anyone who can author the
  // universe declares the rule code_enforced, attaches evidence dated a minute
  // ago from a verifier of their choosing, and the model received ONLY the
  // resulting constraint — never the rule. Now the binding text is delivered
  // regardless, and the fabricated evidence is labelled as a claim.
  const fabricated = swapNoPhi(basePolicy(), {
    verifier_id: "verifier.attacker", verified_at: "2026-09-09T11:59:00Z", control_version: "7",
  });
  const projected = projectRuleForModel({
    universe: compileRuleUniverse(fabricated), rule_id: "no-phi", now: NOW });

  assert.equal(projected.mode, "full_binding_text");
  assert.equal(projected.binding_text, noPhiRule().binding_text);
  assert.equal(projected.code_enforcement_claim.verifier_id, "verifier.attacker");
  // Internally consistent — and internal consistency is all that verdict is.
  assert.equal(projected.code_enforcement_claim.internally_consistent, true);
  assert.equal(projected.code_enforcement_claim.internal_consistency_reason_id,
    "evidence_internally_consistent");
  assert.equal(projected.code_enforcement_claim.evidence_verified_by_kernel, false);
  assert.equal(projected.evidence_verified_by_kernel, false);
});

test("Q066 evidence age is the caller's policy or it is no policy, and never an assurance", () => {
  const universeOf = evidence => compileRuleUniverse(swapNoPhi(basePolicy(), evidence));
  const project = (evidence, policy) => projectRuleForModel({
    universe: universeOf(evidence), rule_id: "no-phi", now: NOW,
    ...(policy === undefined ? {} : { enforcement_evidence_policy: policy }),
  });

  // NO POLICY SUPPLIED: the age is reported and no verdict is drawn. The flat
  // 86400-second window this module used to enforce was invented here and named
  // in no settled decision.
  const old = project({ verified_at: "2020-01-01T00:00:00Z", control_version: "7" });
  assert.equal(old.mode, "full_binding_text");
  assert.equal(old.code_enforcement_claim.internal_consistency_reason_id,
    "evidence_internally_consistent");
  assert.equal(old.code_enforcement_claim.max_evidence_age_seconds, null);
  assert.equal(old.code_enforcement_claim.evidence_age_policy_supplied, false);

  // POLICY SUPPLIED: the caller's own number, recorded with their name on it,
  // and inclusive at the bound.
  const policy = { max_evidence_age_seconds: DAY_IN_SECONDS };
  const past = project({ verified_at: A_SECOND_MORE_THAN_A_DAY, control_version: "7" }, policy);
  assert.equal(past.code_enforcement_claim.internal_consistency_reason_id,
    "evidence_older_than_supplied_policy");
  assert.equal(past.code_enforcement_claim.evidence_age_seconds, DAY_IN_SECONDS + 1);
  assert.equal(past.code_enforcement_claim.evidence_age_policy_supplied, true);
  // Past the caller's window is a DIAGNOSTIC, not a delivery failure: the
  // binding text is still what the model gets, so nothing is withheld.
  assert.equal(past.mode, "full_binding_text");

  const atBound = project({ verified_at: A_DAY_BEFORE_NOW, control_version: "7" }, policy);
  assert.equal(atBound.code_enforcement_claim.internal_consistency_reason_id,
    "evidence_internally_consistent");

  // The other three internal-consistency verdicts, kept apart.
  assert.equal(project(null).code_enforcement_claim.internal_consistency_reason_id,
    "evidence_absent");
  assert.equal(project(null).code_enforcement_claim.evidence_present, false);
  const drifted = project({ verified_at: FRESH_EVIDENCE, control_version: "6" });
  assert.equal(drifted.code_enforcement_claim.internal_consistency_reason_id,
    "evidence_control_version_mismatch");
  assert.equal(drifted.code_enforcement_claim.control_version, "7");
  assert.equal(drifted.code_enforcement_claim.verified_control_version, "6");
  assert.equal(project({ verified_at: "2026-09-09T12:30:00Z", control_version: "7" })
    .code_enforcement_claim.internal_consistency_reason_id, "evidence_from_the_future");

  // And the receipt says which policy, if any, was in force.
  const withPolicy = derive(basePolicy(), commitFacts(),
    { enforcement_evidence_policy: policy });
  assert.equal(withPolicy.enforcement_evidence_max_age_seconds, DAY_IN_SECONDS);
  assert.equal(withPolicy.enforcement_evidence_age_policy_supplied, true);
  assert.equal(derive(basePolicy(), commitFacts()).enforcement_evidence_max_age_seconds, null);
});

test("Q066 a caller cannot assert enforcement instead of evidencing it", () => {
  const policy = basePolicy();
  policy.rules.find(r => r.rule_id === "no-phi").enforced = true;
  assert.equal(code(() => compileRuleUniverse(policy)), "caller_assertion_field_refused");

  const verified = basePolicy();
  verified.rules.find(r => r.rule_id === "worktree-first").verified = true;
  assert.equal(code(() => compileRuleUniverse(verified)), "caller_assertion_field_refused");

  const granted = basePolicy();
  granted.rules.find(r => r.rule_id === "worktree-first").authority_override = "joe";
  assert.equal(code(() => compileRuleUniverse(granted)), "caller_authority_field_refused");
});

// -------------------------------- reproducibility and forgery resistance

test("the same universe and facts reproduce the same digests, byte for byte", () => {
  const first = compileRuleUniverse(basePolicy());
  const second = compileRuleUniverse(basePolicy());
  assert.equal(first.universe_digest, second.universe_digest);
  assert.equal(first.universe_digest, digest(ruleUniversePreimage(first)));

  const a = derive(basePolicy(), commitFacts());
  const b = derive(basePolicy(), commitFacts());
  assert.equal(a.receipt_digest, b.receipt_digest);
  assert.equal(verifyCoverageReceipt(a), true);

  // Rule ORDER in the policy is not part of the universe's identity.
  const reordered = basePolicy();
  reordered.rules.reverse();
  assert.equal(compileRuleUniverse(reordered).universe_digest, first.universe_digest);
});

test("an edited compiled universe is refused, digest recomputed not trusted", () => {
  const compiled = compileRuleUniverse(basePolicy());
  const clone = structuredClone(compiled);
  assert.equal(requireCompiledUniverse(clone).universe_digest, compiled.universe_digest);

  clone.rules[0].binding_text = "anything I like";
  assert.equal(code(() => requireCompiledUniverse(clone)), "universe_digest_mismatch");

  const reordered = structuredClone(compiled);
  reordered.removal_order = [...reordered.removal_order].reverse();
  assert.equal(code(() => requireCompiledUniverse(reordered)), "universe_digest_mismatch");

  assert.equal(code(() => requireCompiledUniverse({
    compiled: true, schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION, universe_version: 1,
    tenant: ORGANIZATION_TENANT_ID, completeness: "complete_authoritative_universe",
    declared_actions: ["x"], declared_resource_classes: ["y"], rules: [], removal_order: [],
    universe_digest: compiled.universe_digest,
  })), "universe_digest_mismatch");

  // A prototype-only "clone" inherits every value and owns none of them.
  assert.equal(code(() => requireCompiledUniverse(Object.create(compiled))), "invalid_shape");
});

// ------------- a SELF-REHASHED forgery, which is the case a digest cannot see
//
// digest() is an unkeyed sha256 over canonical JSON and it is exported, so the
// forger below does exactly what the compiler does: builds the object it wants,
// computes ruleUniversePreimage over it, and presents the pair. Every case here
// passes the digest check. Each one used to reach deriveRuleApplicability and
// decide a receipt; each one now fails on the invariant it broke, because the
// universe is REDERIVED rather than believed.

const forge = mutate => {
  const forged = structuredClone(compileRuleUniverse(basePolicy()));
  mutate(forged);
  forged.universe_digest = digest(ruleUniversePreimage(forged));
  return forged;
};

test("B2 a self-rehashed forged universe is refused: the digest is not the provenance", () => {
  // The forger's own work passes the hash check, which is the whole point.
  const untouched = forge(() => {});
  assert.equal(untouched.universe_digest, compileRuleUniverse(basePolicy()).universe_digest);
  assert.equal(requireCompiledUniverse(untouched).universe_digest, untouched.universe_digest);

  // 1. A CYCLE. Two rules pointing at each other has no deterministic winner;
  //    the removal walk would have resolved it in whatever order the forger
  //    chose, which is precisely the nondeterminism Q087 exists to refuse.
  const cyclic = forge(u => {
    const legacy = u.rules.find(r => r.rule_id === "legacy-branching");
    legacy.relations = [{ relation: "overrides", target_rule_id: "worktree-first",
      target_version: 3 }];
    legacy.scoped_validity = { action: ["repo.commit"] };
    u.removal_order = ["legacy-branching", ...u.removal_order.filter(id => id !== "legacy-branching")];
  });
  assert.equal(code(() => requireCompiledUniverse(cyclic)), "relation_cycle");
  assert.equal(code(() => deriveWith(cyclic)), "relation_cycle");

  // 2. THE REMOVAL ORDER ITSELF, self-rehashed. Putting a target before its
  //    remover flips which rule survives, and the order is caller-supplied data
  //    the receipt reads without re-deriving.
  const flipped = forge(u => { u.removal_order = [...u.removal_order].reverse(); });
  assert.equal(code(() => requireCompiledUniverse(flipped)), "universe_not_canonically_compiled");

  // 3. A MANDATORY JUDGMENT RULE. The class table's central Q051 invariant, and
  //    it only ever ran inside compileRule.
  const mandatoryPreference = forge(u => {
    const tone = u.rules.find(r => r.rule_id === "client-review");
    tone.mandatory = true;
    tone.control_effect = { control_key: "tone", effect: "require" };
    tone.provenance = prov("r-rule-tone", "9");
  });
  assert.equal(code(() => requireCompiledUniverse(mandatoryPreference)),
    "judgment_rule_cannot_be_mandatory");

  // 4. A MANDATORY RULE WITH A NULL CONTROL EFFECT. This one used to reach
  //    `rule.control_effect.control_key` and throw a raw TypeError — outside
  //    this module's own two-kinds-of-no contract. It is a V5F05Error now.
  const nullControl = forge(u => {
    u.rules.find(r => r.rule_id === "send-gate").control_effect = null;
  });
  assert.equal(code(() => requireCompiledUniverse(nullControl)), "missing_control_effect");
  const nullControlError = (() => {
    try { deriveWith(nullControl); } catch (error) { return error; }
    return null;
  })();
  assert.ok(nullControlError instanceof V5F05Error);
  assert.equal(nullControlError.code, "missing_control_effect");

  // 5. A DANGLING EDGE, which the removal walk skipped in silence.
  const dangling = forge(u => {
    u.rules.find(r => r.rule_id === "worktree-first").relations =
      [{ relation: "supersedes", target_rule_id: "never-existed", target_version: 1 }];
  });
  assert.equal(code(() => requireCompiledUniverse(dangling)), "dangling_relation");

  // 6. THE B1 REPRODUCTION, fabricated straight into the compiled shape rather
  //    than compiled: a preference deleting a mandatory control across owner
  //    and scope, with the removal order the forger wants.
  const repeal = forge(u => {
    u.rules.push({
      rule_id: "joe-prefers-no-review", version: 1, rule_class: "preference",
      enforcement: "partner_preference", scope: "joe-personal", owner: "dell", mandatory: false,
      trigger: {}, control_effect: null,
      binding_text: "Joe does not want a second seat on his own sends.",
      summary: null, code_enforcement: null, tests: [],
      no_machine_control_reason: "Only a person can judge tone.",
      retirement: { behavior: "permanent_until_superseded", expires_at: null },
      relations: [{ relation: "overrides", target_rule_id: "send-gate", target_version: 1 }],
      scoped_validity: null, provenance: null,
    });
    u.rules.sort((a, b) => (a.rule_id < b.rule_id ? -1 : 1));
    u.removal_order = ["joe-prefers-no-review",
      ...u.removal_order.filter(id => id !== "joe-prefers-no-review")];
  });
  assert.equal(code(() => requireCompiledUniverse(repeal)),
    "removing_edge_without_scoped_validity");

  // 7. A FORGED DERIVED FIELD. `enforcement` is computed from rule_class, so a
  //    forger claiming code_control on a workflow rule changes nothing it can
  //    rehash its way out of.
  const forgedEnforcement = forge(u => {
    u.rules.find(r => r.rule_id === "worktree-first").enforcement = "code_control";
  });
  assert.equal(code(() => requireCompiledUniverse(forgedEnforcement)),
    "universe_not_canonically_compiled");
});

test("B2 an accessor inside a compiled universe cannot answer two reads differently", () => {
  const compiled = structuredClone(compileRuleUniverse(basePolicy()));
  let reads = 0;
  Object.defineProperty(compiled.rules[0], "scope", {
    get: () => (reads++ === 0 ? compiled.rules[1].scope : "anything"),
    enumerable: true, configurable: true,
  });
  assert.equal(code(() => requireCompiledUniverse(compiled)), "accessor_property_refused");
});

test("an accessor, a prototype key or a symbol key is refused rather than read", () => {
  const accessor = basePolicy();
  Object.defineProperty(accessor.rules[0], "scope", {
    get: () => "shared", enumerable: true, configurable: true,
  });
  assert.equal(code(() => compileRuleUniverse(accessor)), "accessor_property_refused");

  const polluted = JSON.parse(
    JSON.stringify(basePolicy()).replace('"rules":[{', '"rules":[{"__proto__":{"x":1},'));
  assert.equal(code(() => compileRuleUniverse(polluted)), "prototype_key_refused");

  const symbolic = basePolicy();
  symbolic.rules[0][Symbol("hidden")] = true;
  assert.equal(code(() => compileRuleUniverse(symbolic)), "symbol_key_refused");

  // A NON-ENUMERABLE own data property. The closed-key sweep read Object.keys
  // while the accessor sweep read getOwnPropertyNames, so a field named
  // `enforced` or `authority` could ride along unseen by the guard that exists
  // to refuse exactly those names. Nothing read it — and "an unread field is an
  // unenforced one" is this module's own standard, not an excuse.
  for (const [key, expected] of [
    ["enforced", "caller_assertion_field_refused"],
    ["authority_grant", "caller_authority_field_refused"],
    ["not_a_rule_field", "unknown_field"],
  ]) {
    const hidden = basePolicy();
    Object.defineProperty(hidden.rules[0], key, {
      value: true, enumerable: false, configurable: true, writable: true,
    });
    assert.equal(code(() => compileRuleUniverse(hidden)), expected, key);
  }
});

test("a non-enumerable own key on a NESTED rule is refused by both entry points", () => {
  // The two entry points disagreed about the same object. compileRuleUniverse
  // refuses a hidden own field through assertClosedKeys, which reads own
  // property names; requireCompiledUniverse snapshots the whole universe before
  // checking anything below the top level, and the snapshot copied Object.keys
  // — so the field was silently DELETED from a nested rule instead. Nothing read
  // it either way, and a guard that quietly drops what another guard refuses is
  // how "an unread field is an unenforced one" stops being true.
  for (const key of ["enforced", "authority_grant", "not_a_rule_field"]) {
    const compiled = structuredClone(compileRuleUniverse(basePolicy()));
    Object.defineProperty(compiled.rules[0], key, {
      value: true, enumerable: false, configurable: true, writable: true,
    });
    assert.equal(code(() => requireCompiledUniverse(compiled)), "non_enumerable_key_refused", key);
  }

  // The same shape reaching the compiler is still refused there, on the name.
  const hidden = basePolicy();
  Object.defineProperty(hidden.rules[0], "enforced", {
    value: true, enumerable: false, configurable: true, writable: true,
  });
  assert.equal(code(() => compileRuleUniverse(hidden)), "caller_assertion_field_refused");

  // Nested two levels down, where no closed-key sweep runs at all.
  const deep = structuredClone(compileRuleUniverse(basePolicy()));
  Object.defineProperty(deep.rules[0].retirement, "expires_whenever", {
    value: "2030-01-01T00:00:00Z", enumerable: false, configurable: true, writable: true,
  });
  assert.equal(code(() => requireCompiledUniverse(deep)), "non_enumerable_key_refused");

  // An ordinary compiled universe is untouched by the check.
  const clean = compileRuleUniverse(basePolicy());
  assert.equal(requireCompiledUniverse(structuredClone(clean)).universe_digest,
    clean.universe_digest);
});

test("the guard-collision self-check covers the compiled universe shape it accepts", () => {
  // requireCompiledUniverse runs assertClosedKeys over the COMPILED shape, and
  // that key list was absent from the load-time self-check — so a key added to
  // the compiled universe that collided with either guard would have refused
  // every legitimate compiled universe rather than failing this module's import.
  const compiled = compileRuleUniverse(basePolicy());
  const keys = Object.keys(compiled);
  assert.ok(keys.includes("removal_order"));
  assert.ok(keys.includes("universe_digest"));
  assert.ok(keys.includes("completeness"));
  for (const key of keys) {
    assert.ok(!V5_F05_REFUSED_ASSERTION_FIELDS.includes(key), key);
    for (const fragment of V5_F05_AUTHORITY_INJECTION_FRAGMENTS) {
      assert.ok(!key.includes(fragment), `${key} collides with the fragment "${fragment}"`);
    }
  }
  // Which is the property that matters: the shape the module emits is a shape
  // the module accepts.
  assert.equal(requireCompiledUniverse(structuredClone(compiled)).universe_digest,
    compiled.universe_digest);
});

test("a coverage receipt that was edited no longer hashes to its own digest", () => {
  const receipt = derive(basePolicy(), commitFacts());
  const forged = { ...receipt, consequential_action_permitted: true, effective: [] };
  assert.equal(code(() => verifyCoverageReceipt(forged)), "coverage_receipt_digest_mismatch");
});

test("identity strings are checked, not merely typed", () => {
  const invisible = basePolicy();
  // Written as an escape, never as a literal: a zero-width space in the source
  // would make the test that refuses invisible characters itself unreadable.
  invisible.rules[0].rule_id = `worktree${String.fromCodePoint(0x200B)}first`;
  assert.equal(code(() => compileRuleUniverse(invisible)), "unsafe_unicode");

  const untrimmed = basePolicy();
  untrimmed.rules[0].scope = " shared";
  assert.equal(code(() => compileRuleUniverse(untrimmed)), "untrimmed_text");

  const carriage = basePolicy();
  carriage.rules[0].binding_text = "line one\r\nline two";
  assert.equal(code(() => compileRuleUniverse(carriage)), "unsafe_unicode");

  const duplicated = basePolicy();
  duplicated.rules.push(worktreeRule());
  assert.equal(code(() => compileRuleUniverse(duplicated)), "duplicate_rule");
});

// ----------------------------------------- the closed kernel projection

test("the kernel projection is closed, hashed and states what it does not do", () => {
  const preimage = v5F05RuleKernelPreimage();
  assert.equal(v5F05RuleKernelDigest(), digest(preimage));
  assert.equal(v5F05RuleKernelCanonicalBytes(), canonicalJson(preimage));
  assert.deepEqual(JSON.parse(v5F05RuleKernelCanonicalBytes()).decisions.map(d => d.decision_id),
    [...V5_F05_SETTLED_DECISION_IDS]);
  assert.equal(preimage.semantic_retrieval_may_remove_controls, false);
  assert.equal(preimage.model_resolves_conflicts, false);
  assert.deepEqual(preimage.fact_dimensions.map(d => d.dimension), [...V5_F05_FACT_DIMENSIONS]);
  assert.deepEqual(preimage.relations, [...V5_F05_RELATIONS]);

  // What the corrections above state in the hashed projection rather than in a
  // comment, so a consumer that reads only the digest still reads them.
  assert.equal(preimage.removing_edge_requires_scoped_validity, true);
  assert.equal(
    preimage.removing_edge_requires_mandatory_capable_class_against_mandatory_target, true);
  assert.equal(preimage.removing_edge_requires_source_provenance, true);
  assert.equal(preimage.removing_edge_may_cross_owner_or_scope, false);
  assert.equal(preimage.removal_is_single_pass_in_compiler_order, true);
  assert.equal(preimage.removing_rule_requires_source_provenance, true);
  assert.equal(preimage.semantic_addition_from_historical_bucket_is_labelled_non_authority, true);
  assert.equal(preimage.code_enforced_constraint_mode_emitted, false);
  assert.equal(preimage.code_enforcement_evidence_verified_by_kernel, false);
  assert.equal(preimage.enforcement_evidence_age_policy_is_caller_supplied, true);
  assert.equal(preimage.default_enforcement_evidence_max_age_seconds, null);
  assert.equal(preimage.mandatory_rule_requires_source_provenance, true);
  assert.equal(preimage.rule_taint_class_resolved_by_kernel, false);
  assert.equal(preimage.write_gate_field, "consequential_action_permitted");
});

test("Q065 a blocked receipt reads read_only, never allow, and still names the write gate", () => {
  const facts = commitFacts();
  delete facts.action;
  const receipt = derive(basePolicy(), facts);
  // Nothing hard-refused, but the write is blocked: the decision says so
  // itself, so a caller testing `decision === "allow"` is not handed a yes.
  assert.equal(receipt.decision, "read_only");
  assert.equal(receipt.reason_id, "coverage_incomplete_read_only");
  assert.equal(receipt.read_only_exploration_permitted, true);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.equal(receipt.write_gate_field, "consequential_action_permitted");
  assert.equal(receipt[receipt.write_gate_field], false);
});

// The receipts every decision value can come from, each built the way a real
// caller reaches it. rule-applicability-decision-mutants.v5.test.mjs holds
// planted-bug mutants of this same invariant.
function receiptsForEveryDecision() {
  const unknownFacts = commitFacts();
  delete unknownFacts.action;
  const partial = basePolicy();
  partial.completeness = "partial_unknown_coverage";
  const conflict = basePolicy();
  conflict.rules.push({
    rule_id: "send-freeze",
    version: 1, rule_class: "workflow", scope: "shared", owner: "joe", mandatory: true,
    trigger: { risk_tier: ["consequential"] },
    control_effect: { control_key: "client_send", effect: "forbid" },
    binding_text: "Client sends are frozen during the migration window.",
    tests: ["check:send-freeze"],
    retirement: { behavior: "permanent_until_superseded" },
    provenance: prov("r-rule-send-freeze", "7"),
  });
  const undeliverable = swapNoPhi(basePolicy(),
    { verified_at: FRESH_EVIDENCE, control_version: "7" }, { binding_text: null });
  return {
    complete: derive(basePolicy(), commitFacts()),
    typed_facts_unknown: derive(basePolicy(), unknownFacts),
    universe_partial: derive(partial, commitFacts()),
    binding_conflict: derive(conflict, sendFacts()),
    delivery_refused: derive(undeliverable, commitFacts()),
  };
}

test("`decision` agrees with the write gate on every receipt: allow only when the write is permitted", () => {
  assert.deepEqual([...V5_F05_COVERAGE_DECISIONS], ["allow", "read_only", "refuse"]);
  const receipts = receiptsForEveryDecision();
  const expected = {
    complete: ["allow", true],
    typed_facts_unknown: ["read_only", false],
    universe_partial: ["read_only", false],
    binding_conflict: ["refuse", false],
    delivery_refused: ["refuse", false],
  };
  for (const [label, receipt] of Object.entries(receipts)) {
    const [decision, permitted] = expected[label];
    assert.equal(receipt.decision, decision, label);
    assert.equal(receipt.consequential_action_permitted, permitted, label);
    // The invariant itself, stated independently of the table above.
    assert.equal(receipt.decision === "allow", receipt.consequential_action_permitted === true, label);
    assert.equal(receipt.decision === "refuse", receipt.read_only_exploration_permitted === false, label);
    assert.ok(V5_F05_COVERAGE_DECISIONS.includes(receipt.decision), label);
    assert.equal(verifyCoverageReceipt(receipt), true, label);
  }
});

test("a re-digested receipt whose `decision` contradicts its write gate is refused", () => {
  // The digest proves the bytes were not edited; it cannot prove the bytes
  // were right. A forger who recomputes the digest must still not be able to
  // hand a caller `decision: "allow"` on a blocked receipt.
  const redigest = receipt => {
    const { receipt_digest, effects, ...rest } = receipt;
    return { ...rest, receipt_digest: digest(rest), effects };
  };
  const { complete, typed_facts_unknown, binding_conflict } = receiptsForEveryDecision();
  for (const [base, decision] of [
    [typed_facts_unknown, "allow"], [binding_conflict, "allow"], [binding_conflict, "read_only"],
    [complete, "read_only"], [complete, "refuse"], [typed_facts_unknown, "refuse"],
  ]) {
    const forged = redigest({ ...base, decision });
    assert.equal(code(() => verifyCoverageReceipt(forged)), "coverage_receipt_decision_inconsistent",
      `${base.decision} -> ${decision}`);
  }
  const unknownValue = redigest({ ...complete, decision: "permit" });
  assert.equal(code(() => verifyCoverageReceipt(unknownValue)), "coverage_receipt_decision_inconsistent");
});

test("the unbuilt runtime seams are named and fail closed", () => {
  const gaps = ruleKernelIntegrationGaps();
  assert.ok(gaps.length >= 6);
  assert.ok(gaps.every(gap => gap.landed !== true));
  assert.deepEqual(gaps.map(gap => gap.gap).sort(), [
    "no_action_admission_enforcement",
    "no_code_enforcement_verifier",
    "no_live_rule_store_reader",
    "no_relation_authority_grant_verifier",
    "no_rule_provenance_taint_resolver",
    "no_rule_registry_persistence",
  ]);
  // The provenance gap text names both rules that must carry a source, so it
  // does not read as covering mandatory rules alone.
  const provenanceGap = gaps.find(gap => gap.gap === "no_rule_provenance_taint_resolver");
  assert.ok(provenanceGap.what.includes("removing edge"));
  assert.equal(code(() => assertRuleKernelIntegrationComplete()), "kernel_integration_incomplete");
});
