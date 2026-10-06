// NOT COLLECTED BY THE SUITE. ops/ci.sh's unit class globs
// mcp-server/test/*.test.mjs, and this file's suffix is .js -- so nothing here
// runs, and nothing that must run may be added here.
//
// WR-000113 wired the R03 notification mint into record-signal in
// mcp-server/src/investigation.js. Its acceptance proofs therefore live in
// mcp-server/test/notifications.test.mjs, which IS collected:
//   R03-DEDUPE, R03-NO-PROGRESS-SPAM, R03-NO-SPONSOR,
//   R03-SIGNAL-SURVIVES-MINT-FAILURE, R03-QUIET-HOURS, R03-STATUS-DISTINCT
//   and R03-DEEP-LINK-RECHECKS all drive record-signal's own handler.
// The grant and recipient halves live in mcp-server/test/r03-notifications-postgres.sql.
//
// This note is the whole of WR-000113's change to this file: adding a case here
// would be adding a case that never runs.

import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";
import { validateEvidencePacket, validateSignal } from "../src/investigation.js";

test("signal validation requires numeric detection and evidence", () => {
  assert.equal(validateSignal({
    observed_value: 18,
    baseline_value: 5,
    threshold_value: 14,
    comparison: "gte",
    evidence_refs: ["query:deal-next-date:v1"],
  }), null);

  assert.deepEqual(validateSignal({
    observed_value: "18",
    threshold_value: 14,
    comparison: "gte",
    evidence_refs: ["query:deal-next-date:v1"],
  }), { error: "non_numeric_signal", field: "observed_value" });

  assert.deepEqual(validateSignal({
    observed_value: 18,
    threshold_value: 14,
    comparison: "gte",
    evidence_refs: [],
  }), { error: "signal_evidence_required" });

  assert.deepEqual(validateSignal({
    observed_value: 11,
    threshold_value: 14,
    comparison: "gte",
    evidence_refs: ["query:deal-next-date:v1"],
  }), { error: "threshold_not_crossed", comparison: "gte" });
});

test("worker packet accepts facts or explicit negative evidence, never recommendations", () => {
  assert.equal(validateEvidencePacket({
    raw_facts: [{ days_overdue: 18 }],
    evidence_refs: ["deal:123:next_action"],
  }), null);

  assert.equal(validateEvidencePacket({
    raw_facts: [],
    evidence_refs: [],
    nothing_found: true,
  }), null);

  assert.deepEqual(validateEvidencePacket({ raw_facts: [], evidence_refs: [] }), {
    error: "evidence_packet_incomplete",
    hint: "return raw_facts and evidence_refs, or set nothing_found=true explicitly",
  });

  assert.deepEqual(validateEvidencePacket({
    raw_facts: [{ days_overdue: 18 }],
    evidence_refs: ["deal:123:next_action"],
    recommendation: "escalate the deal",
  }), {
    error: "distributed_judgment_refused",
    hint: "workers return scoped facts; the investigation owner adjudicates",
  });
});

test("investigation registry exposes the bounded loop and marks writes", () => {
  const reads = ["next-signals", "investigation-neighborhood", "get-investigation"];
  const writes = ["record-signal", "open-investigation", "open-investigation-branch",
    "record-branch-evidence", "adjudicate-investigation-branch", "close-investigation"];

  for (const name of reads) {
    assert.ok(TOOLS[name], `${name} should be registered`);
    assert.equal(Boolean(TOOLS[name].write), false, `${name} should be read-only`);
  }
  for (const name of writes) {
    assert.ok(TOOLS[name], `${name} should be registered`);
    assert.equal(TOOLS[name].write, true, `${name} should be a write`);
  }

  assert.equal(TOOLS["record-branch-evidence"].inputSchema.additionalProperties, false);
  assert.equal(Object.hasOwn(TOOLS["record-branch-evidence"].inputSchema.properties, "recommendation"), false);
  assert.equal(TOOLS["close-investigation"].inputSchema.required.includes("strongest_alternative"), true);
  assert.equal(TOOLS["open-investigation-branch"].inputSchema.properties.max_depth, undefined);
});
