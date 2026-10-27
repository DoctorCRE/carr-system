import test from "node:test";
import assert from "node:assert/strict";
import { OAuthConsentState } from "../src/oauth-consent-state.js";

function fixture(alarm = null) {
  const values = new Map();
  let scheduled = 0;
  const storage = {
    get: async key => values.get(key),
    put: async (key, value) => values.set(key, value),
    delete: async key => values.delete(key),
    deleteAll: async () => values.clear(),
    getAlarm: async () => alarm,
    setAlarm: async time => { alarm = time; scheduled++; },
    transaction: async callback => callback(storage),
  };
  const subject = new OAuthConsentState({ storage });
  const pending = { state: "state", browser: "browser", csrf: "csrf", expiresAt: Date.now() + 600000 };
  const call = operation => subject.fetch(new Request(`https://consent.example/${operation}`, {
    method: "POST", body: JSON.stringify(pending),
  }));
  return { storage, pending, call, scheduled: () => scheduled,
    deliverAlarm: async () => { alarm = null; await subject.alarm(); } };
}

test("consent creation schedules cleanup once and preserves an existing alarm", async () => {
  const fresh = fixture();
  assert.equal((await fresh.call("create")).status, 204);
  assert.equal(await fresh.storage.getAlarm(), fresh.pending.expiresAt);
  assert.equal(fresh.scheduled(), 1);
  await assert.rejects(fresh.call("create"), /Consent already exists/);
  assert.equal(fresh.scheduled(), 1);

  const deadline = Date.now() + 300000;
  const existing = fixture(deadline);
  assert.equal((await existing.call("create")).status, 204);
  assert.equal(await existing.storage.getAlarm(), deadline);
  assert.equal(existing.scheduled(), 0);
});

test("consent cleanup and repeated alarm delivery never schedule another alarm", async () => {
  const f = fixture();
  await f.call("create");
  await f.deliverAlarm();
  assert.equal((await f.call("consume")).status, 400);
  assert.equal(await f.storage.getAlarm(), null);
  await f.deliverAlarm();
  assert.equal(await f.storage.getAlarm(), null);
  assert.equal(f.scheduled(), 1);
});
