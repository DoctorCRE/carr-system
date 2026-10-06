// a02-rule-enforcement-reader.test.mjs — the JS half of V5-A02's rule
// coverage read: how lifecycle-assurance.v5.js reports, or refuses, the record
// the database function returns.
//
// These tests exercise real JS logic only (shape and count validation, the
// fail-closed paths, the pass-through). What the SQL function derives from
// real rows is proved in a02-rule-enforcement-postgres.test.mjs, and each
// validation check's planted-bug mutant is killed in
// a02-rule-enforcement-reader-mutants.test.mjs. Nothing here greps source.

import test from "node:test";
import assert from "node:assert/strict";

import { V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { readRuleEnforcementCoverage } from "../src/lifecycle-assurance.v5.js";

function coverage(overrides = {}) {
  return {
    schema_version: "doctorcre-v5-a02-rule-enforcement-coverage.v2",
    observed_at: "2026-09-25T20:00:00Z",
    active_rule_count: 2,
    covered_rule_count: 2,
    gap_count: 0,
    coverage_state: "complete",
    coverage_complete: true,
    gaps: [],
    evidence_digest: `sha256:${"a".repeat(64)}`,
    ...overrides,
  };
}

class FakeDb {
  constructor(payload = coverage(), error = null) {
    this.payload = payload;
    this.error = error;
    this.calls = [];
  }
  async query(text, params = []) {
    this.calls.push({ text: text.replace(/\s+/g, " ").trim(), params });
    if (this.error) throw this.error;
    return { rows: [{ coverage: this.payload }] };
  }
}

test("the authoritative record is reported with exact counts, state and digest", async () => {
  const db = new FakeDb();
  const result = await readRuleEnforcementCoverage(db);
  assert.deepEqual(db.calls, [{
    text: "select ops.v5_a02_rule_enforcement_coverage() as coverage",
    params: [],
  }]);
  assert.equal(result.schema_version, "doctorcre-v5-a02-lifecycle-assurance.v1");
  assert.equal(result.answer, "rule_enforcement_coverage");
  assert.equal(result.status, "available");
  assert.equal(result.decision, "report");
  assert.equal(result.coverage_state, "complete");
  assert.equal(result.coverage_complete, true);
  assert.equal(result.active_rule_count, 2);
  assert.equal(result.covered_rule_count, 2);
  assert.equal(result.gap_count, 0);
  assert.deepEqual(result.gaps, []);
  assert.equal(result.evidence_digest, `sha256:${"a".repeat(64)}`);
  assert.equal(result.evidence_source, "ops.v5_a02_rule_enforcement_coverage()");
  assert.equal(result.decided_by, "authoritative_record_reader");
  assert.equal(result.model_judgment_admitted, false);
  assert.equal(result.request_read, false);
  assert.equal(result.caller_evidence_admitted, false);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
  assert.ok(Object.isFrozen(result));
});

for (const reason_id of [
  "active_rule_amended_needs_reapproval",
  "active_rule_control_unmapped",
  "active_rule_approved_control_not_installed",
  "rule_tests_not_passing",
  "rule_test_evidence_future_dated",
  "rule_test_evidence_changed_since_approval",
  "active_rule_fallback_absent",
]) {
  test(`a ${reason_id} gap is passed through and is never complete`, async () => {
    const gap = { rule_id: "11111111-1111-4111-8111-111111111111", reason_id, detail: "named" };
    const result = await readRuleEnforcementCoverage(new FakeDb(coverage({
      covered_rule_count: 1, gap_count: 1, coverage_state: "gaps", coverage_complete: false,
      gaps: [gap],
    })));
    assert.equal(result.status, "available");
    assert.equal(result.coverage_state, "gaps");
    assert.equal(result.coverage_complete, false);
    assert.deepEqual(result.gaps, [gap]);
  });
}

test("ruling 5b: an empty rule set is reported as `empty`, never complete", async () => {
  const result = await readRuleEnforcementCoverage(new FakeDb(coverage({
    active_rule_count: 0, covered_rule_count: 0, coverage_state: "empty", coverage_complete: false,
  })));
  assert.equal(result.status, "available");
  assert.equal(result.coverage_state, "empty");
  assert.equal(result.coverage_complete, false);
});

test("ruling 5b: an empty rule set claiming complete is refused", async () => {
  const result = await readRuleEnforcementCoverage(new FakeDb(coverage({
    active_rule_count: 0, covered_rule_count: 0,
  })));
  assert.equal(result.status, "unavailable");
  assert.equal(result.reason_id, "rule_coverage_record_invalid");
});

test("an unreadable record layer leaks no driver text and claims no coverage", async () => {
  const result = await readRuleEnforcementCoverage(
    new FakeDb(null, new Error("postgres at secret-host said green")));
  assert.equal(result.status, "unavailable");
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "control_implementation_reader_unavailable");
  assert.equal(JSON.stringify(result).includes("secret-host"), false);
  assert.equal(result.coverage_complete, undefined);
});

test("no record connection at all is unavailable, not an exception", () => {
  for (const database of [undefined, null, {}, [], { query: "nope" }]) {
    const result = readRuleEnforcementCoverage(database);
    assert.equal(result.status, "unavailable");
    assert.equal(result.reason_id, "control_implementation_reader_unavailable");
  }
});

test("zero or several rows, or an extra field, are refused as invalid", async () => {
  const none = { async query() { return { rows: [] }; } };
  const two = { async query() { return { rows: [{ coverage: coverage() }, { coverage: coverage() }] }; } };
  for (const db of [none, two, new FakeDb({ ...coverage(), extra: true })]) {
    const result = await readRuleEnforcementCoverage(db);
    assert.equal(result.status, "unavailable");
    assert.equal(result.reason_id, "rule_coverage_record_invalid");
  }
});
