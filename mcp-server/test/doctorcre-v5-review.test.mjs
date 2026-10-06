import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const {
  canonicalJson, digest, decisionBindingErrors, productionOrderErrors,
  frozenDigestErrors, validateReviewPacket,
} = require("../../tools/doctorcre-v5-review.cjs");

const requirement = {
  id: "Q001", question: "Synthetic question", recommendation: "Synthetic recommendation",
  source_turn: "assistant-turn", source_item: "assistant-item",
  user_source_turn: "user-turn", user_source_item: "user-item",
  user_response: "Synthetic acceptance", target: "portfolio", acceptance_hook: "review",
};
function decision(source = requirement) {
  return {
    decision_id: source.id + ".D1", source_question_id: source.id,
    source_evidence_digest: digest(source),
    authority_source_refs: [
      { role: "assistant", thread_ref: source.source_turn, item_ref: source.source_item },
      { role: "user", thread_ref: source.user_source_turn, item_ref: source.user_source_item },
    ],
    conflict_refs: [], resolution: "Synthetic resolution", settled_requirement: "Synthetic obligation",
    disposition: "accepted", phase: "v5", target: "portfolio",
    acceptance_hook: "review", authoritative_owner: "owner", consumer_gates: ["gate"],
    acceptance_predicate: "Synthetic independent observation", oracle_ref: "oracle:synthetic",
    oracle_version: "1.0.0", oracle_type: "contract", evidence_type: "fixture", predecessor_edges: [],
  };
}
test("exact source references and nonempty decision bind successfully", () => {
  assert.deepEqual(decisionBindingErrors([decision()], [requirement]), []);
});
for (const [name, mutate, expected] of [
  ["unknown disposition", row => { row.disposition = "silently_shipped"; }, "decision_disposition"],
  ["unknown phase", row => { row.phase = "production_now"; }, "decision_phase"],
  ["empty predicate", row => { row.acceptance_predicate = "  "; }, "empty_decision_field"],
  ["invented authority", row => { row.authority_source_refs[1].item_ref = "invented"; }, "authority_reference"],
  ["candidate-added field", row => { row.authorizes_external_send = true; }, "decision_shape"],
  ["current decision gains future predicate", row => { row.future_activation_predicate = "future"; }, "future_predicate_on_current_decision"],
]) {
  test(name + " is independently rejected", () => {
    const row = decision(); mutate(row);
    assert(decisionBindingErrors([row], [requirement]).some(error => error.startsWith(expected)));
  });
}
test("recomputed digest cannot legitimize invented authority references", () => {
  const row = decision();
  row.authority_source_refs[0].thread_ref = "unrelated-task";
  row.source_evidence_digest = digest(requirement);
  assert(decisionBindingErrors([row], [requirement]).includes("authority_reference:Q001.D1"));
});
test("all sources need decisions and decision identities cannot be reused", () => {
  const next = { ...requirement, id: "Q002" };
  assert(decisionBindingErrors([decision()], [requirement, next]).includes("source_without_decision:Q002"));
  assert(decisionBindingErrors([decision(), decision()], [requirement]).includes("decision_identity:Q001.D1"));
});
test("inactive successor requires a separate future activation predicate", () => {
  const row = decision(); row.phase = "successor"; row.target = "typed_successor";
  assert(decisionBindingErrors([row], [requirement]).includes("successor_activation_contract:Q001.D1"));
  row.future_activation_predicate = "Separate future receipt must pass before any effect.";
  assert.deepEqual(decisionBindingErrors([row], [requirement]), []);
});
function producers() {
  return [
    { step_ref: "step:j1-kernel-production-outcome", evidence_scope: "production",
      subject_environment: "production", consumes_gate_ids: [] },
    { step_ref: "step:j1-core-production-outcome", evidence_scope: "production",
      subject_environment: "production", consumes_gate_ids: ["journey-one-kernel-production-accepted"] },
  ];
}
test("production scope and explicit kernel consumption must both hold", () => {
  assert.deepEqual(productionOrderErrors(producers()), []);
  for (const field of ["evidence_scope", "subject_environment"]) {
    const rows = producers(); rows[0][field] = "staging";
    assert(productionOrderErrors(rows).some(error => error.startsWith("j1_production_scope")));
  }
  const rows = producers(); rows[1].consumes_gate_ids = ["journey-one-preactivation-contract-bound"];
  assert(productionOrderErrors(rows).includes("j1_core_requires_kernel"));
});
test("canonical JSON orders integer-like property names lexically", () => {
  assert.equal(canonicalJson({ 2: "two", 10: "ten" }), '{"10":"ten","2":"two"}');
  assert.equal(digest({ b: 2, a: 1 }), digest({ a: 1, b: 2 }));
});
test("candidate IDs and schema claims cannot stand in for frozen semantic identity", () => {
  const changed = { decision_id: "Q005.D1", settled_requirement: "Actively create Tours and send email." };
  assert.deepEqual(frozenDigestErrors(changed, {}), [
    "design_changed_requires_new_semantic_review", "constitution_changed_requires_new_semantic_review",
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

