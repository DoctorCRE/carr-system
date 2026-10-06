// V5-F05 live rule-context seam — tests precede the implementation.
//
// The pure kernels already prove classification, graph resolution, coverage,
// delivery, and admission.  This suite proves the missing join: the server
// reads one authoritative universe snapshot, derives coverage from it, and
// never lets a caller supply identity, time, completeness, or a permission.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_F05_RUNTIME_SCHEMA_VERSION,
  readActionContext,
  ruleContextRuntimeTools,
} from "../src/rule-context-runtime.v5.js";
import {
  V5_F05_UNIVERSE_SCHEMA_VERSION,
  verifyCoverageReceipt,
} from "../src/rule-applicability.v5.js";

const NOW = "2026-09-26T06:30:00.000Z";
const ACTOR = Object.freeze({
  id: "10000000-0000-4000-8000-000000000031",
  slug: "joe-local",
  human: false,
  sponsoring_human_slug: "joe",
});

const provenance = (id, fill) => ({
  source_record_id: id,
  source_version: 1,
  source_content_digest: `sha256:${fill.repeat(64)}`,
  retrieved_at: "2026-09-26T06:00:00.000Z",
});

const rule = (overrides = {}) => ({
  rule_id: "rule-worktree",
  version: 3,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { action: ["repo.commit"] },
  control_effect: { control_key: "isolated_worktree", effect: "require" },
  binding_text: "Use an isolated worktree before changing tracked source.",
  tests: ["check:worktree-isolated"],
  retirement: { behavior: "permanent_until_superseded" },
  provenance: provenance("rule:worktree", "a"),
  ...overrides,
});

const policy = (rules = [rule()], overrides = {}) => ({
  schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
  universe_version: 82,
  tenant: ORGANIZATION_TENANT_ID,
  completeness: "complete_authoritative_universe",
  declared_actions: ["repo.commit"],
  declared_resource_classes: ["repository"],
  rules,
  ...overrides,
});

const facts = (overrides = {}) => ({
  action: "repo.commit",
  audience: "internal",
  environment: "isolated_worktree",
  lifecycle_transition: "create",
  resource_class: "repository",
  risk_tier: "consequential",
  ...overrides,
});

class FakeClient {
  constructor(snapshot) { this.snapshot = structuredClone(snapshot); this.calls = []; }
  async query(sql, params) {
    this.calls.push({ sql: sql.replace(/\s+/g, " ").trim(), params });
    if (!sql.includes("ops.f05_rule_universe")) throw new Error(`unexpected query: ${sql}`);
    return { rows: [{ result: structuredClone(this.snapshot) }] };
  }
}

const snapshot = (overrides = {}) => ({
  observed_at: NOW,
  policy: policy(),
  active_rule_count: 1,
  projected_rule_count: 1,
  missing_rule_ids: [],
  ...overrides,
});

test("same authoritative inputs reproduce the exact universe and coverage digests", async () => {
  const request = { facts: facts() };
  const first = await readActionContext(new FakeClient(snapshot()), ACTOR, request);
  const second = await readActionContext(new FakeClient(snapshot()), ACTOR, request);

  assert.equal(first.schema_version, V5_F05_RUNTIME_SCHEMA_VERSION);
  assert.equal(first.universe_digest, second.universe_digest);
  assert.equal(first.coverage_receipt.receipt_digest, second.coverage_receipt.receipt_digest);
  assert.equal(first.coverage_receipt.coverage_complete, true);
  assert.equal(first.consequential_action_permitted, true);
  assert.deepEqual(first.coverage_receipt.universe_rule_ids, ["rule-worktree"]);
  assert.deepEqual(first.effects, {
    creates_effect: false,
    database_writes: 0,
    network_calls: 0,
    provider_actions: 0,
    notifications: 0,
    schedules: 0,
    deployments: 0,
    activations: 0,
    acceptances: 0,
  });
});

test("database row order cannot change a digest", async () => {
  const a = rule();
  const b = rule({
    rule_id: "rule-send",
    version: 1,
    trigger: { action: ["repo.commit"] },
    control_effect: { control_key: "review-before-send", effect: "require" },
    binding_text: "Require review before a consequential external send.",
    tests: ["check:review-before-send"],
    provenance: provenance("rule:send", "b"),
  });
  const one = snapshot({ policy: policy([a, b]), active_rule_count: 2, projected_rule_count: 2 });
  const two = snapshot({ policy: policy([b, a]), active_rule_count: 2, projected_rule_count: 2 });
  const left = await readActionContext(new FakeClient(one), ACTOR, { facts: facts() });
  const right = await readActionContext(new FakeClient(two), ACTOR, { facts: facts() });
  assert.equal(left.universe_digest, right.universe_digest);
  assert.equal(left.coverage_receipt.receipt_digest, right.coverage_receipt.receipt_digest);
});

test("a missing possible binding rule makes the universe partial and blocks the write", async () => {
  // PLANTED BUG WITNESS: trusting policy.completeness here would grant the
  // consequential action even though the store names an active rule it could
  // not project.  The runtime must re-derive completeness from the census.
  const db = new FakeClient(snapshot({
    active_rule_count: 2,
    projected_rule_count: 1,
    missing_rule_ids: ["rule-not-typed"],
  }));
  const result = await readActionContext(db, ACTOR, { facts: facts() });
  assert.equal(result.coverage_receipt.universe_completeness, "partial_unknown_coverage");
  assert.equal(result.coverage_receipt.coverage_complete, false);
  assert.equal(result.consequential_action_permitted, false);
  // Blocked, so the decision must not read as permission to a caller that
  // tests `decision === "allow"` instead of the write gate.
  assert.equal(result.coverage_receipt.decision, "read_only");
  assert.deepEqual(result.coverage_receipt.blocking_reasons, ["universe_coverage_unknown"]);
  assert.deepEqual(result.source.missing_rule_ids, ["rule-not-typed"]);
});

test("coverage receipt partitions and enumerates every rule from the store", async () => {
  const notApplicable = rule({
    rule_id: "rule-client-send",
    trigger: { action: ["document.send"] },
    control_effect: { control_key: "client-send-review", effect: "require" },
    binding_text: "Review client documents before sending them.",
    tests: ["check:client-send-review"],
    provenance: provenance("rule:client-send", "c"),
  });
  const result = await readActionContext(new FakeClient(snapshot({
    policy: policy([rule(), notApplicable], {
      declared_actions: ["document.send", "repo.commit"],
    }),
    active_rule_count: 2,
    projected_rule_count: 2,
  })), ACTOR, { facts: facts() });
  assert.deepEqual(result.coverage_receipt.universe_rule_ids,
    ["rule-client-send", "rule-worktree"]);
  assert.deepEqual(result.coverage_receipt.effective.map(x => x.rule_id), ["rule-worktree"]);
  assert.deepEqual(result.coverage_receipt.not_applicable.map(x => x.rule_id), ["rule-client-send"]);
  assert.equal(result.coverage_receipt.coverage_complete, true);
  assert.equal(result.coverage_receipt.decision, "allow");
  assert.equal(result.consequential_action_permitted, true);
});

test("an unresolved binding conflict refuses and cannot be called permission", async () => {
  const forbid = rule({
    rule_id: "rule-forbid-worktree",
    control_effect: { control_key: "isolated_worktree", effect: "forbid" },
    binding_text: "Do not use an isolated worktree for this action.",
    tests: ["check:no-worktree"],
    provenance: provenance("rule:no-worktree", "d"),
  });
  await assert.rejects(
    () => readActionContext(new FakeClient(snapshot({
      policy: policy([rule(), forbid]), active_rule_count: 2, projected_rule_count: 2,
    })), ACTOR, { facts: facts() }),
    error => error?.code === "unresolved_binding_conflict",
  );
});

test("a census whose counts, projected rules and missing list disagree is refused, never read as partial", async () => {
  // Each snapshot is internally inconsistent in exactly one way. Reading any
  // of them as merely "partial" would let a store that under-reports its
  // missing rules (or over-reports its projections) shape coverage.
  const inconsistent = {
    "missing list shorter than the gap": snapshot({
      active_rule_count: 2, projected_rule_count: 1, missing_rule_ids: [] }),
    "missing list longer than the gap": snapshot({
      active_rule_count: 1, projected_rule_count: 1, missing_rule_ids: ["rule-hidden"] }),
    "projected count disagrees with the policy rules": snapshot({
      active_rule_count: 2, projected_rule_count: 2, missing_rule_ids: [] }),
    "more projected than active": snapshot({
      active_rule_count: 0, projected_rule_count: 1, missing_rule_ids: [] }),
  };
  for (const [label, bad] of Object.entries(inconsistent)) {
    await assert.rejects(
      () => readActionContext(new FakeClient(bad), ACTOR, { facts: facts() }),
      error => error?.code === "runtime_census_mismatch",
      `${label} must refuse as a census mismatch`,
    );
  }
  // The consistent partial census still reads, and still blocks.
  const partial = await readActionContext(new FakeClient(snapshot({
    active_rule_count: 2, projected_rule_count: 1, missing_rule_ids: ["rule-missing"],
  })), ACTOR, { facts: facts() });
  assert.equal(partial.consequential_action_permitted, false);
  assert.deepEqual([...partial.source.missing_rule_ids], ["rule-missing"]);
});

test("server-derived identity, time, completeness, and model advice cannot be supplied by the caller", async () => {
  for (const forbidden of [
    "actor", "tenant", "now", "universe", "completeness",
    "semantic_candidates", "consequential_action_permitted",
  ]) {
    await assert.rejects(
      () => readActionContext(new FakeClient(snapshot()), ACTOR,
        { facts: facts(), [forbidden]: forbidden === "now" ? NOW : {} }),
      error => error?.code === "runtime_authority_injection",
      `caller field ${forbidden} must refuse`,
    );
  }
  await assert.rejects(
    () => readActionContext(new FakeClient(snapshot()), ACTOR,
      { facts: facts({ actor_class: "verified_partner" }) }),
    error => error?.code === "runtime_authority_injection",
    "caller facts.actor_class must refuse",
  );
});

test("tool registration is read-only and closes its request schema", async () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  const tools = ruleContextRuntimeTools({ ToolError });
  assert.deepEqual(Object.keys(tools), ["read-action-context", "bind-rule-context-contract"]);
  assert.equal(tools["read-action-context"].write, false);
  assert.equal(tools["read-action-context"].writerConnection, true);
  assert.deepEqual(tools["read-action-context"].inputSchema.required, ["facts"]);
  assert.equal(tools["read-action-context"].inputSchema.additionalProperties, false);
  const result = await tools["read-action-context"].handler(
    new FakeClient(snapshot()), ACTOR, { facts: facts() });
  assert.equal(result.consequential_action_permitted, true);
  assert.equal(result.coverage_receipt.facts.actor_class, "sponsored_agent");
});

test("the only contract writer is authority-only and validates the DB-derived full rule", async () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  const withEnvelope = async (_c, _actor, _verb, _args, fn) => fn();
  const tools = ruleContextRuntimeTools({ ToolError, withEnvelope });
  const calls = [];
  const db = { query: async (sql, params) => {
    calls.push({ sql: sql.replace(/\s+/g, " ").trim(), params });
    return { rows: [{ result: { ok: true, replayed: false, contract: rule() } }] };
  } };
  const input = {
    idempotency_key: "00000000-0000-4000-8000-000000000082",
    rule_id: "00000000-0000-4000-8000-000000000005",
    contract: {
      rule_class: "workflow", mandatory: true,
      trigger: { action: ["repo.commit"], resource_class: ["repository"] },
      control_effect: { control_key: "isolated_worktree", effect: "require" },
      tests: ["check:worktree-isolated"],
      retirement: { behavior: "permanent_until_superseded" },
    },
  };
  const tool = tools["bind-rule-context-contract"];
  assert.equal(tool.write, true);
  assert.equal(tool.authorityOnly, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  const result = await tool.handler(db, ACTOR, input);
  assert.equal(result.ok, true);
  assert.match(calls[0].sql, /ops\.bind_f05_rule_contract/);
  assert.deepEqual(calls[0].params, [input.rule_id, input.contract, input.idempotency_key]);
});

// ---------------------------------------------------------------------------
// Zero projected rules: the production state after V5-F05 shipped. The store
// holds active rules and no typed contract has been bound yet, so the SQL
// census projects an EMPTY policy. The kernel correctly refuses to compile an
// empty universe; the runtime must still answer with a partial receipt that
// names every missing rule, and must never read "no rules" as permission.
// ---------------------------------------------------------------------------

const PROD_FACTS = Object.freeze({
  action: "send-email", audience: "client", environment: "production",
  lifecycle_transition: "send", resource_class: "deal", risk_tier: "consequential",
});

const MISSING = Object.freeze([
  "00000000-0000-4000-8000-0000000000a1",
  "00000000-0000-4000-8000-0000000000b2",
  "00000000-0000-4000-8000-0000000000c3",
]);

// Exactly what ops.f05_rule_universe returns when nothing is bound: the
// caller's action and resource class are still declared, rules is [].
const unbound = (overrides = {}) => snapshot({
  policy: policy([], {
    completeness: "partial_unknown_coverage",
    declared_actions: ["send-email"],
    declared_resource_classes: ["deal"],
  }),
  active_rule_count: MISSING.length,
  projected_rule_count: 0,
  missing_rule_ids: [...MISSING],
  ...overrides,
});

test("no bound contracts: the read returns a partial, blocked, digest-bound receipt naming every active rule", async () => {
  // PLANTED BUG WITNESS (prod, after #1305): this read threw
  // invalid_shape policy.rules "must name at least 1 item(s)" from the kernel,
  // hiding the missing-rule list and making the verb unusable before a bind.
  const result = await readActionContext(new FakeClient(unbound()), ACTOR, { facts: { ...PROD_FACTS } });
  const receipt = result.coverage_receipt;

  assert.equal(result.schema_version, V5_F05_RUNTIME_SCHEMA_VERSION);
  assert.equal(result.consequential_action_permitted, false);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.equal(receipt.coverage_complete, false);
  assert.equal(receipt.universe_completeness, "partial_unknown_coverage");
  assert.equal(receipt.read_only_exploration_permitted, true);
  // The zero-rules receipt and the kernel's partial receipt agree: blocked
  // reads read_only, never allow.
  assert.equal(receipt.decision, "read_only");
  assert.equal(receipt.reason_id, "coverage_incomplete_read_only");
  assert.equal(receipt.write_gate_field, "consequential_action_permitted");
  assert.ok(receipt.blocking_reasons.includes("universe_coverage_unknown"));
  assert.ok(receipt.blocking_reasons.includes("no_rule_contract_projected"));
  assert.deepEqual([...receipt.universe_rule_ids], []);
  assert.deepEqual([...receipt.missing_rule_ids], [...MISSING]);
  assert.equal(receipt.active_rule_count, MISSING.length);
  assert.equal(receipt.projected_rule_count, 0);
  assert.deepEqual([...result.source.missing_rule_ids], [...MISSING]);
  assert.equal(result.source.active_rule_count, MISSING.length);
  assert.equal(result.source.projected_rule_count, 0);
  assert.equal(receipt.facts.actor_class, "sponsored_agent");
  assert.equal(receipt.facts.action, "send-email");
  assert.equal(result.universe_digest, receipt.universe_digest);
  assert.match(result.universe_digest, /^sha256:[0-9a-f]{64}$/);
  assert.match(receipt.receipt_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(verifyCoverageReceipt(receipt), true);

  // Digest-bound: a copy that flips the gate, or drops a missing rule, no
  // longer hashes to its own digest.
  const forged = structuredClone(receipt);
  forged.consequential_action_permitted = true;
  assert.throws(() => verifyCoverageReceipt(forged),
    error => error?.code === "coverage_receipt_digest_mismatch");
  const shortened = structuredClone(receipt);
  shortened.missing_rule_ids = shortened.missing_rule_ids.slice(1);
  assert.throws(() => verifyCoverageReceipt(shortened),
    error => error?.code === "coverage_receipt_digest_mismatch");

  // Deterministic, and bound to the missing list and the census.
  const again = await readActionContext(new FakeClient(unbound()), ACTOR, { facts: { ...PROD_FACTS } });
  assert.equal(again.coverage_receipt.receipt_digest, receipt.receipt_digest);
  assert.equal(again.universe_digest, result.universe_digest);
  const fewer = await readActionContext(new FakeClient(unbound({
    active_rule_count: 2, missing_rule_ids: MISSING.slice(0, 2),
  })), ACTOR, { facts: { ...PROD_FACTS } });
  assert.notEqual(fewer.coverage_receipt.receipt_digest, receipt.receipt_digest);
});

test("zero active and zero projected rules still never read as complete or permitted", async () => {
  const result = await readActionContext(new FakeClient(unbound({
    policy: policy([], {
      completeness: "complete_authoritative_universe",
      declared_actions: ["send-email"],
      declared_resource_classes: ["deal"],
    }),
    active_rule_count: 0, missing_rule_ids: [],
  })), ACTOR, { facts: { ...PROD_FACTS } });
  assert.equal(result.consequential_action_permitted, false);
  assert.equal(result.coverage_receipt.coverage_complete, false);
  assert.equal(result.coverage_receipt.universe_completeness, "partial_unknown_coverage");
  assert.equal(result.coverage_receipt.decision, "read_only");
  assert.ok(result.coverage_receipt.blocking_reasons.includes("no_rule_contract_projected"));
});

test("a zero-projection census still has to add up, and its policy and facts are still validated", async () => {
  const refusals = {
    "missing list shorter than the gap": [unbound({ missing_rule_ids: MISSING.slice(1) }),
      "runtime_census_mismatch"],
    "missing list longer than the gap": [unbound({ active_rule_count: 2 }), "runtime_census_mismatch"],
    "unsorted missing list": [unbound({ missing_rule_ids: [...MISSING].reverse() }),
      "runtime_census_mismatch"],
    "foreign tenant": [unbound({ policy: { ...unbound().policy, tenant: "someone-else" } }),
      "tenant_mismatch"],
    "unknown policy schema": [unbound({ policy: { ...unbound().policy, schema_version: "v0" } }),
      "unknown_schema_version"],
    "extra policy key": [unbound({ policy: { ...unbound().policy, permitted: true } }),
      "unknown_field"],
  };
  for (const [label, [bad, code]] of Object.entries(refusals)) {
    await assert.rejects(
      () => readActionContext(new FakeClient(bad), ACTOR, { facts: { ...PROD_FACTS } }),
      error => error?.code === code,
      `${label} must refuse with ${code}`,
    );
  }
  await assert.rejects(
    () => readActionContext(new FakeClient(unbound()), ACTOR,
      { facts: { ...PROD_FACTS, risk_tier: "whatever" } }),
    error => error?.code === "unknown_risk_tier",
    "an out-of-vocabulary fact is refused on the empty path too",
  );
});

test("the kernel's non-empty validation is unchanged: a malformed projected rule still refuses", async () => {
  const { binding_text: _dropped, ...noText } = rule();
  await assert.rejects(
    () => readActionContext(new FakeClient(snapshot({ policy: policy([noText]) })), ACTOR,
      { facts: facts() }),
    error => error?.code === "missing_binding_text",
  );
});
