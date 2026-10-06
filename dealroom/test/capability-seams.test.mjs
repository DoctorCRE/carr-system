import test from "node:test";
import assert from "node:assert/strict";
import { recordHomeSeam, lifecycleSeam, healthSeam } from "../js/capability-seams.js";

test("recordHomeSeam (V5-F01) reports available and returns the real read when the client implements the live record-home read", async () => {
  const detail = { kind: "current_policy", readback: { policy_digest: "abc123" } };
  const client = { getRecordHome: async () => detail };
  const result = await recordHomeSeam(client, "d1");
  assert.equal(result.available, true);
  assert.equal(result.capability, "V5-F01");
  assert.deepEqual(result.detail, detail);
});

test("recordHomeSeam is honestly unavailable — 'not deployed' — when the client has no live record-home read, regardless of what else it implements", async () => {
  const clientWithGetDeal = { getDeal: async (id) => ({ deal: { id, name: "Acme" } }) };
  for (const client of [{}, clientWithGetDeal, undefined, null]) {
    const result = await recordHomeSeam(client, "d1");
    assert.equal(result.available, false);
    assert.equal(result.capability, "V5-F01");
    assert.match(result.reason, /not deployed yet/);
  }
});

test("recordHomeSeam never calls client.getDeal — getDeal is a different, already-existing WO-1 read, not F01", async () => {
  let called = false;
  const client = {
    getDeal: async () => { called = true; return { deal: { id: "d1" } }; },
  };
  await recordHomeSeam(client, "d1");
  assert.equal(called, false);
});

test("recordHomeSeam refuses honestly — 'unavailable', never a fabricated deployed state — when the live read errors", async () => {
  const client = { getRecordHome: async () => { throw new Error("unauthorized"); } };
  const result = await recordHomeSeam(client, "d1");
  assert.equal(result.available, false);
  assert.equal(result.capability, "V5-F01");
  assert.match(result.reason, /unauthorized/);
});

test("lifecycleSeam (V5-J102) is unavailable today — the client has no typed transition yet", async () => {
  const client = { setNextStep: async () => ({ ok: true }) }; // only the generic write exists
  const result = await lifecycleSeam(client, "d1");
  assert.equal(result.available, false);
  assert.equal(result.capability, "V5-J102");
  assert.match(result.reason, /not deployed yet/);
});

test("lifecycleSeam reports available the moment a client implements transitionLifecycle, WITHOUT calling it", async () => {
  let called = false;
  const client = { transitionLifecycle: async ({ deal }) => { called = true; return { deal, phase: "Legal" }; } };
  const result = await lifecycleSeam(client, "d1");
  assert.equal(result.available, true);
  assert.equal(called, false, "a seam must answer availability without performing the write it is checking for");
});

test("lifecycleSeam refuses to probe by invoking even a transitionLifecycle that throws — it must never be called at all", async () => {
  const client = { transitionLifecycle: async () => { throw new Error("should never run"); } };
  const result = await lifecycleSeam(client, "d1");
  assert.equal(result.available, true);
});

test("healthSeam (V5-A01) is honestly unavailable without a client getHealth", async () => {
  const result = await healthSeam({});
  assert.equal(result.available, false);
  assert.equal(result.capability, "V5-A01");
});

test("healthSeam reports real health when the client answers — a read may safely be performed", async () => {
  const scope = { workflow_key: "doctorcre.release", workflow_version: 7, work_request_id: "WR-700" };
  let observed;
  const client = { getHealth: async (value) => { observed = value; return { status: "green" }; } };
  const result = await healthSeam(client, scope);
  assert.equal(result.available, true);
  assert.equal(result.detail.status, "green");
  assert.deepEqual(observed, scope);
});

test("healthSeam refuses honestly, never fabricating a value, when the real read throws", async () => {
  const client = { getHealth: async () => { throw new Error("unauthorized"); } };
  const result = await healthSeam(client);
  assert.equal(result.available, false);
  assert.match(result.reason, /unauthorized/);
});

test("every seam is honest about a missing or undefined client, not just an empty object", async () => {
  assert.equal((await recordHomeSeam(undefined, "d1")).available, false);
  assert.equal((await lifecycleSeam(null, "d1")).available, false);
  assert.equal((await healthSeam(undefined)).available, false);
});
