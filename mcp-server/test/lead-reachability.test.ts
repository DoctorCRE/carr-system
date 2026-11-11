// Contract tests exercise the registered board read without simulating verification.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";
import { TOOLS } from "../src/tools.js";

const contract = new Validator(
  JSON.parse(readFileSync(new URL("../contracts/lead-reachability.v1.json", import.meta.url), "utf8")),
);
const id = "30000000-0000-0000-0000-000000000001";
const evidence = {
  flag_id: id,
  kind: "verified",
  found: true,
  expired: false,
  proposes_correction: false,
  source: "https://example.test/registry/1",
  observed_at: "2020-01-01T00:00:00Z",
  expires_on: "2099-01-01",
  value: { checked: ["identity"] },
};
async function board(findings: object[] = [], fields: object = {}) {
  let calls = 0;
  const row = {
    id,
    party_id: id,
    base_version: 4,
    stage: "new",
    suppressed: false,
    contact_state: "active",
    contact_eligible: true,
    research_evidence: findings,
    ...fields,
  };
  const result = await TOOLS["lead-board"].handler(
    {
      query: async () => {
        calls++;
        return { rows: [{ leads: [row], detail: row }] };
      },
    },
    { id, slug: "joe", human: true },
    { workspace: "leads", lead_id: id },
  );
  assert.equal(calls, 1);
  assert.equal(result.leads[0].id, id);
  assert.deepEqual(result.leads[0].reachability, result.detail.reachability);
  assert.equal(contract.validate(result.leads[0].reachability).valid, true);
  return result.leads[0].reachability;
}
test("missing evidence remains unknown research, eligibility never proves reachability", async () => {
  const r = await board();
  assert.equal(r.schema_version, "lead-reachability.v1");
  assert.equal(r.classification, "research");
  assert.equal(r.evidence_state, "unknown");
  assert.equal(r.contact_eligible, true);
  assert.equal(r.research_state, "needs_research");
  assert.ok(r.missing_reasons.includes("verified_usable_route_not_established"));
});
test("identity verification and free form contact findings never assert a usable route", async () => {
  for (const kind of ["verified", "email", "cell", "office_phone"]) {
    const r = await board([{ ...evidence, kind }]);
    assert.equal(r.classification, "research");
    assert.equal(r.evidence_state, "current");
    assert.equal(r.evidence[0].flag_id, id);
    assert.equal(r.evidence[0].expires_on, "2099-01-01");
  }
});
test("snapshot refresh follows expiry, conflict, renewal, removal and contact restriction", async () => {
  const current = await board([evidence]);
  assert.equal(current.research_state, "verified");
  assert.equal((await board([{ ...evidence, expired: true }])).evidence_state, "expired");
  assert.equal((await board([evidence, { ...evidence, kind: "discrepancy" }])).evidence_state, "conflicting");
  assert.equal(
    (await board([evidence, { ...evidence, kind: "discrepancy", expired: true }])).evidence_state,
    "current",
  );
  assert.equal((await board()).evidence_state, "unknown");
  for (const fields of [{ contact_state: "paused" }, { suppressed: true }, { stage: "do_not_contact" }]) {
    const restricted = await board([evidence], fields);
    assert.equal(restricted.contact_eligible, false);
    assert.equal(restricted.classification, "research");
    assert.ok(restricted.missing_reasons.includes("contact_restricted"));
  }
  assert.equal((await board([evidence])).contact_eligible, true);
});
test("source revision changes with source facts and preserves future evidence as unknown", async () => {
  const a = await board([evidence]);
  assert.equal(a.source_revision, (await board([evidence])).source_revision);
  assert.notEqual(
    a.source_revision,
    (await board([{ ...evidence, source: "https://example.test/changed" }])).source_revision,
  );
  assert.notEqual(a.source_revision, (await board([evidence], { base_version: 5 })).source_revision);
  assert.equal((await board([{ ...evidence, observed_at: "2099-01-01T00:00:00Z" }])).evidence_state, "unknown");
});
