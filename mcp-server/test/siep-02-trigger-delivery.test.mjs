import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  deriveRuleDeliverySource,
  literalTriggerMatches,
  ruleDeliverySourceDigest,
} from "../src/rule-delivery-source.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "../..");
const fixture = JSON.parse(await readFile(join(HERE, "fixtures/siep-02-trigger-delivery.v1.json"), "utf8"));
const reviewedMapBytes = await readFile(join(REPO, "ops/config/rule-enforcement-map.json"));
const reviewedMap = JSON.parse(reviewedMapBytes);
const packIndex = Object.entries(reviewedMap.rule_packs).map(([pack, value]) => ({
  pack, title: value.title, triggers: value.triggers, rule_count: 1,
}));
const mapDigest = `sha256:${createHash("sha256").update(reviewedMapBytes).digest("hex")}`;
const HASH_A = `sha256:${"a".repeat(64)}`;
const HASH_B = `sha256:${"b".repeat(64)}`;

function planFor(value) {
  return {
    work_request_ref: "WR-123",
    work_request_title: value.work_request_title,
    desired_outcome: value.desired_outcome,
    acceptance_criteria: [{ criterion_id: "DONE", text: "The exact requested outcome is verified." }],
    base_version: 3,
    plan_ref: "PLAN-abcdef123456-v1",
    plan_hash: HASH_A,
    scope_summary: value.scope_summary,
    runbook_ref: "doctrine:runbook#safe-plan",
    dependency_refs: [],
    recovery_ref: "safe:rollback:fixture",
    observability_ref: "safe:readback:fixture",
    caps: { max_steps: 5, max_duration_minutes: 60 },
  };
}

function admissionFor(value) {
  return value.tier === "heavy" ? {
    admission_ref: "HBA-abcdef123456-v1",
    admission_hash: HASH_B,
    builder_session_ref: "session:builder:siep02-fixture",
    master_plan: { product_goal: "Complete the bounded target product." },
  } : null;
}

async function project(value, overrides = {}) {
  return deriveRuleDeliverySource({
    plan: planFor(value),
    heavyClassification: { tier: value.tier, reasons: value.reasons },
    admittedHeavyContract: admissionFor(value),
    packIndex,
    mapDigest,
    ...overrides,
  });
}

test("fixture is pinned to the exact reviewed trigger map", () => {
  assert.equal(fixture.schema_version, "siep-02-trigger-delivery-fixture.v1");
  assert.equal(fixture.reviewed_map_sha256, mapDigest);
  assert.equal(fixture.cases.length, 3);
});

test("server-bound Work Request facts and exact 0320 reasons deterministically select sorted packs", async () => {
  for (const value of fixture.cases) {
    const projection = await project(value);
    assert.deepEqual(projection.required_packs, value.expected_packs, value.case_ref);
    assert.deepEqual(projection.matched.map(item => item.pack), value.expected_packs);
    assert.equal(projection.build_classifier.function, "ops.heavy_build_classification");
    assert.deepEqual(projection.build_classifier.reasons, [...value.reasons].sort());
    assert.equal(projection.trigger_map.map_digest, mapDigest);
    assert.match(projection.contract_digest, /^sha256:[0-9a-f]{64}$/);
  }
});

test("literal matching handles punctuation, phrases, case, and word edges", () => {
  assert.equal(literalTriggerMatches("Inspect X.COM metrics", "x.com"), true);
  assert.equal(literalTriggerMatches("run a first-of-kind build", "first-of-kind"), true);
  assert.equal(literalTriggerMatches("the pull request is ready", "pull request"), true);
  assert.equal(literalTriggerMatches("a clientage taxonomy", "client"), false);
  assert.equal(literalTriggerMatches("redeployment", "deploy"), false);
});

test("caller labels cannot downgrade or inject packs into the closed projection", async () => {
  const value = fixture.cases[0];
  const baseline = await project(value);
  const spoofed = await deriveRuleDeliverySource({
    plan: { ...planFor(value), tier: "standard", required_packs: ["joe-comms"], actor: "joe" },
    heavyClassification: { tier: value.tier, reasons: value.reasons },
    admittedHeavyContract: admissionFor(value),
    packIndex,
    mapDigest,
    tier: "standard",
    packs: ["joe-comms"],
    actor: "joe",
    tenant: "forged",
  });
  assert.deepEqual(spoofed.required_packs, baseline.required_packs);
  assert.equal(spoofed.contract_digest, baseline.contract_digest);
  assert.equal(JSON.stringify(spoofed).includes("joe-comms"), false);
});

test("the pack index is registry input, never observed-work text", async () => {
  const value = { ...fixture.cases[0], work_request_title: "Bounded change",
    desired_outcome: "Apply the reviewed delta.", scope_summary: "Proceed safely.",
    tier: "standard", reasons: [] };
  const projection = await project(value, { admittedHeavyContract: null });
  assert.deepEqual(projection.required_packs, []);
  assert.ok(projection.required_packs.length < packIndex.length);
});

test("typed document field names never masquerade as observed trigger values", async () => {
  const value = { ...fixture.cases[0], work_request_title: "Bounded change",
    desired_outcome: "Apply the reviewed delta.", scope_summary: "Proceed safely.",
    reasons: ["signal:architecture_or_multi_surface"] };
  const plan = { ...planFor(value), runbook_ref: "safe:runbook:neutral",
    acceptance_criteria: [{ criterion_id: "BASELINE", text: "Confirm the exact result." }] };
  const admission = { ...admissionFor(value), master_plan: {
    baseline_comparison: "Compare the exact result.",
    release_strategy: "Use the bounded path.",
    rollback_strategy: "Restore the prior state.",
  } };
  const projection = await project(value, { plan, admittedHeavyContract: admission,
    packIndex: [{ pack: "engineering-git", triggers: ["baseline"], rule_count: 1 }] });
  assert.deepEqual(projection.required_packs, []);
});

test("refs, ids, hashes, enum labels, and DAG node names never fire work triggers", async () => {
  const value = { ...fixture.cases[0], work_request_title: "Bounded change",
    desired_outcome: "Apply the reviewed delta.", scope_summary: "Proceed safely.",
    tier: "standard", reasons: [] };
  const plan = { ...planFor(value), runbook_ref: "doctrine:git#client-email-deploy",
    dependency_refs: ["safe:git:client:deploy"], recovery_ref: "safe:email:rollback",
    observability_ref: "safe:client:metrics",
    acceptance_criteria: [{ criterion_id: "SCHEMA-MIGRATION", text: "Confirm the exact result." }] };
  const projection = await project(value, { plan, admittedHeavyContract: null });
  assert.deepEqual(projection.required_packs, []);
});

test("empty packs, malformed classifier output, and an invalid digest are refused", async () => {
  const value = fixture.cases[0];
  await assert.rejects(() => project(value, { packIndex: [{ pack: "engineering-git",
    triggers: ["schema"], rule_count: 0 }] }), /fully deliverable/);
  await assert.rejects(() => project(value, { heavyClassification: {
    tier: "caller-standard", reasons: [] } }), /typed heavy-build classifier/);
  await assert.rejects(() => project(value, { mapDigest: "sha256:not-a-digest" }),
    /coherent installed rule map digest/);
});

test("semantic key order is stable while bound facts change the digest", async () => {
  const value = fixture.cases[0];
  const first = await project(value);
  const reordered = await project(value, { admittedHeavyContract: {
    master_plan: { product_goal: "Complete the bounded target product." },
    builder_session_ref: "session:builder:siep02-fixture",
    admission_hash: HASH_B,
    admission_ref: "HBA-abcdef123456-v1",
  } });
  assert.equal(first.contract_digest, reordered.contract_digest);
  assert.equal(await ruleDeliverySourceDigest(first), first.contract_digest);
  const changedPlan = await project(value, { plan: { ...planFor(value), plan_hash: HASH_B } });
  assert.notEqual(changedPlan.contract_digest, first.contract_digest);
  const changedMap = await project(value, { mapDigest: `sha256:${"c".repeat(64)}` });
  assert.notEqual(changedMap.contract_digest, first.contract_digest);
});

test("SIEP-02 source never uses the enforcement compiler or flips policy", async () => {
  const files = [
    await readFile(join(REPO, "mcp-server/src/rule-delivery-source.js"), "utf8"),
    await readFile(join(REPO, "mcp-server/src/work-request-intake.js"), "utf8"),
  ].join("\n");
  assert.equal(files.includes("ops.applicable_rules"), false);
  assert.equal(files.includes("set_rule_delivery_mode"), false);
});
