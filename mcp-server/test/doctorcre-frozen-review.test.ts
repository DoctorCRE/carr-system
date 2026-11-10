// Test DoctorCRE v5 review source, identity, and activation validation.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  canonicalJson,
  decisionBindingErrors,
  digest,
  frozenDigestErrors,
  productionOrderErrors,
  validateReviewPacket,
} from "../../tools/doctorcre-frozen-review.mts";

function required<T>(value: T | undefined): T {
  assert(value !== undefined, "missing fixture row");
  return value;
}
const requirement = {
  id: "Q001",
  question: "Synthetic question",
  recommendation: "Synthetic recommendation",
  source_turn: "assistant-turn",
  source_item: "assistant-item",
  user_source_turn: "user-turn",
  user_source_item: "user-item",
  user_response: "Synthetic acceptance",
  target: "portfolio",
  acceptance_hook: "review",
};
type DecisionFixture = ReturnType<typeof decisionData> & {
  future_activation_predicate?: string;
  authorizes_external_send?: boolean;
};
function decisionData(source = requirement) {
  return {
    decision_id: source.id + ".D1",
    source_question_id: source.id,
    source_evidence_digest: digest(source),
    authority_source_refs: [
      { role: "assistant", thread_ref: source.source_turn, item_ref: source.source_item },
      { role: "user", thread_ref: source.user_source_turn, item_ref: source.user_source_item },
    ],
    conflict_refs: [],
    resolution: "Synthetic resolution",
    settled_requirement: "Synthetic obligation",
    disposition: "accepted",
    phase: "v5",
    target: "portfolio",
    acceptance_hook: "review",
    authoritative_owner: "owner",
    consumer_gates: ["gate"],
    acceptance_predicate: "Synthetic independent observation",
    oracle_ref: "oracle:synthetic",
    oracle_version: "1.0.0",
    oracle_type: "contract",
    evidence_type: "fixture",
    predecessor_edges: [],
  };
}
function decision(source = requirement): DecisionFixture {
  return decisionData(source);
}
test("exact source references and nonempty decision bind successfully", () => {
  assert.deepEqual(decisionBindingErrors([decision()], [requirement]), []);
});
for (const [name, mutate, expected] of [
  [
    "unknown disposition",
    (row) => {
      row.disposition = "silently_shipped";
    },
    "decision_disposition",
  ],
  [
    "unknown phase",
    (row) => {
      row.phase = "production_now";
    },
    "decision_phase",
  ],
  [
    "empty predicate",
    (row) => {
      row.acceptance_predicate = "  ";
    },
    "empty_decision_field",
  ],
  [
    "invented authority",
    (row) => {
      required(row.authority_source_refs[1]).item_ref = "invented";
    },
    "authority_reference",
  ],
  [
    "candidate-added field",
    (row) => {
      row.authorizes_external_send = true;
    },
    "decision_shape",
  ],
  [
    "current decision gains future predicate",
    (row) => {
      row.future_activation_predicate = "future";
    },
    "future_predicate_on_current_decision",
  ],
] satisfies [string, (row: DecisionFixture) => void, string][]) {
  test(name + " is independently rejected", () => {
    const row = decision();
    mutate(row);
    assert(decisionBindingErrors([row], [requirement]).some((error) => error.startsWith(expected)));
  });
}
test("recomputed digest cannot legitimize invented authority references", () => {
  const row = decision();
  required(row.authority_source_refs[0]).thread_ref = "unrelated-task";
  row.source_evidence_digest = digest(requirement);
  assert(decisionBindingErrors([row], [requirement]).includes("authority_reference:Q001.D1"));
});
test("all sources need decisions and decision identities cannot be reused", () => {
  const next = { ...requirement, id: "Q002" };
  assert(decisionBindingErrors([decision()], [requirement, next]).includes("source_without_decision:Q002"));
  assert(decisionBindingErrors([decision(), decision()], [requirement]).includes("decision_identity:Q001.D1"));
});
test("inactive successor requires a separate future activation predicate", () => {
  const row = decision();
  row.phase = "successor";
  row.target = "typed_successor";
  assert(decisionBindingErrors([row], [requirement]).includes("successor_activation_contract:Q001.D1"));
  row.future_activation_predicate = "Separate future receipt must pass before any effect.";
  assert.deepEqual(decisionBindingErrors([row], [requirement]), []);
});
function producers() {
  return [
    {
      step_ref: "step:j1-kernel-production-outcome",
      evidence_scope: "production",
      subject_environment: "production",
      consumes_gate_ids: [],
    },
    {
      step_ref: "step:j1-core-production-outcome",
      evidence_scope: "production",
      subject_environment: "production",
      consumes_gate_ids: ["journey-one-kernel-production-accepted"],
    },
  ];
}
test("production scope and explicit kernel consumption must both hold", () => {
  assert.deepEqual(productionOrderErrors(producers()), []);
  for (const field of ["evidence_scope", "subject_environment"] satisfies (
    | "evidence_scope"
    | "subject_environment"
  )[]) {
    const rows = producers();
    required(rows[0])[field] = "staging";
    assert(productionOrderErrors(rows).some((error) => error.startsWith("j1_production_scope")));
  }
  const rows = producers();
  required(rows[1]).consumes_gate_ids = ["journey-one-preactivation-contract-bound"];
  assert(productionOrderErrors(rows).includes("j1_core_requires_kernel"));
});
test("canonical JSON orders integer-like property names lexically", () => {
  assert.equal(canonicalJson({ 2: "two", 10: "ten" }), '{"10":"ten","2":"two"}');
  assert.equal(digest({ b: 2, a: 1 }), digest({ a: 1, b: 2 }));
});
test("candidate IDs and schema claims cannot stand in for frozen semantic identity", () => {
  const changed = { decision_id: "Q005.D1", settled_requirement: "Actively create Tours and send email." };
  assert.deepEqual(frozenDigestErrors(changed, {}), [
    "design_changed_requires_new_semantic_review",
    "constitution_changed_requires_new_semantic_review",
  ]);
});
test("malformed review input always denies without granting construction authority", () => {
  for (const value of [null, {}, { design: {} }, { design: [], constitution: {} }]) {
    const result = validateReviewPacket(value);
    assert.equal(result.ok, false);
    assert(result.errors.length > 0);
    assert.match(result.result_scope, /no acceptance or execution authority/);
  }
});

test("canonical serialization pins nested arrays, escaping, unicode and SHA-256", () => {
  const value = { z: [null, true, { b: 'é\n"', a: -0 }], a: "quote" };
  assert.equal(canonicalJson(value), String.raw`{"a":"quote","z":[null,true,{"a":0,"b":"é\n\""}]}`);
  assert.equal(digest({ a: 1, b: 2 }), "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777");
});
test("CLI denies invalid JSON and malformed packets with a nonzero exit", () => {
  for (const input of ["{", "null", "{}", '{"design":[],"constitution":{}}']) {
    const result = spawnSync(
      process.execPath,
      [fileURLToPath(new URL("../../tools/doctorcre-frozen-review.mts", import.meta.url))],
      { input, encoding: "utf8", timeout: 10_000 },
    );
    assert.equal(result.status, 1);
    const output: unknown = JSON.parse(result.stdout);
    assert(output !== null && typeof output === "object" && "ok" in output && "errors" in output);
    assert.equal(output.ok, false);
    assert(Array.isArray(output.errors) && output.errors.length > 0);
  }
});
test("malformed collections refuse and changed source digests cannot bind", () => {
  assert.deepEqual(decisionBindingErrors(null, []), ["decision_or_source_array_missing"]);
  assert.deepEqual(decisionBindingErrors([null, []], []), ["malformed_decision", "malformed_decision"]);
  assert.deepEqual(productionOrderErrors(null), ["producer_array_missing"]);
  const row = decision();
  row.source_evidence_digest = "changed";
  assert(decisionBindingErrors([row], [requirement]).includes("source_digest:Q001.D1"));
});

test("missing JSON values retain the native digest refusal", () => {
  assert.throws(() => digest(undefined), { code: "ERR_INVALID_ARG_TYPE" });
});
