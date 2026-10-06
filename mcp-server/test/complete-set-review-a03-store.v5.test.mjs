import assert from "node:assert/strict";
import test from "node:test";

import * as store from "../src/independent-review-cycle-store.v5.js";

const { V5_A03_STORE_SCHEMA_VERSION, completeSetReviewA03StoreTools } = store;

const KEY = "11111111-1111-4111-8111-111111111111";
const CASE = "22222222-2222-4222-8222-222222222222";
const DIGEST = `sha256:${"a".repeat(64)}`;
const NEXT_DIGEST = `sha256:${"b".repeat(64)}`;
const ACTOR = Object.freeze({ id: "33333333-3333-4333-8333-333333333333", slug: "reviewer" });

class TestToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.payload = payload;
  }
}

function fakeEnvelope(_c, _actor, _verb, _args, fn) { return fn(); }

function fakeClient(replies = []) {
  const calls = [];
  return {
    calls,
    async query(sql, params = []) {
      calls.push({ sql, params });
      const next = replies.shift();
      if (next instanceof Error) throw next;
      return { rows: next ?? [] };
    },
  };
}

const TOOLS = completeSetReviewA03StoreTools({
  withEnvelope: fakeEnvelope,
  writeEvent: async () => {},
  ToolError: TestToolError,
});

async function refused(promise, code) {
  await assert.rejects(promise, error => error instanceof TestToolError && error.payload.error === code);
}

test("store contract exposes the bounded record/read workflow and no generic verdict setter", () => {
  assert.equal(V5_A03_STORE_SCHEMA_VERSION, "doctorcre-v5-complete-set-review-store.v1");
  assert.deepEqual(Object.keys(TOOLS).sort(), [
    "open-complete-set-review",
    "read-complete-set-review",
    "record-complete-set-adjudication",
    "record-complete-set-finding-set",
    "record-complete-set-participant",
    "seal-complete-set-review-round",
  ]);
  assert.ok(!Object.keys(TOOLS).some(name => /set.*(pass|complete)|approve/i.test(name)));
});

test("the store exports no JavaScript mirror of a database gate", () => {
  // Separation, batch completeness and the round bound live only in 0719's
  // SECURITY DEFINER functions; an unwired JS copy would be a second authority.
  assert.deepEqual(Object.keys(store).sort(), ["V5_A03_STORE_SCHEMA_VERSION", "completeSetReviewA03StoreTools"]);
});

test("write tools derive the acting actor and delegate transition authority to named SQL functions", async () => {
  const c = fakeClient([
    [{ case_id: CASE, status: "open" }],
    [{ participant_id: "44444444-4444-4444-8444-444444444444" }],
    [{ submission_id: "55555555-5555-4555-8555-555555555555" }],
  ]);
  const opened = await TOOLS["open-complete-set-review"].handler(c, ACTOR, {
    idempotency_key: KEY, change_ref: "change:v5-a03", delivered_set_digest: DIGEST,
    maker_session_ref: "session:maker",
  });
  assert.equal(opened.case_id, CASE);
  assert.equal(c.calls[0].params.at(-1), ACTOR.id);
  assert.match(c.calls[0].sql, /ops\.v5_a03_open_review_case/);

  await TOOLS["record-complete-set-participant"].handler(c, ACTOR, {
    idempotency_key: KEY, case_id: CASE, role: "reviewer", dimension: "architecture",
    session_ref: "session:reviewer", context_binding: "fresh",
  });
  assert.equal(c.calls[1].params.at(-1), ACTOR.id);
  assert.match(c.calls[1].sql, /ops\.v5_a03_record_participant/);

  await TOOLS["record-complete-set-finding-set"].handler(c, ACTOR, {
    idempotency_key: KEY, case_id: CASE, round_ordinal: 1, dimension: "architecture",
    reviewer_session_ref: "session:reviewer", reviewed_set_digest: DIGEST,
    state: "submitted", finding_refs: ["finding:a03-001"], enumerated_before_repair: true,
  });
  assert.equal(c.calls[2].params.at(-1), ACTOR.id);
  assert.match(c.calls[2].sql, /ops\.v5_a03_record_finding_set/);
});

test("seal and adjudication handlers pass the server-derived actor, never a caller-shaped value", async () => {
  const c = fakeClient([
    [{ round_id: "66666666-6666-4666-8666-666666666666", round_ordinal: 1, state: "changes_required",
      detections: [], case_status: "open" }],
    [{ adjudication_id: "77777777-7777-4777-8777-777777777777", receipt_digest: NEXT_DIGEST, outcome: "fail" }],
  ]);
  const sealed = await TOOLS["seal-complete-set-review-round"].handler(c, ACTOR, {
    idempotency_key: KEY, case_id: CASE, round_ordinal: 1, batch_repair_digest: NEXT_DIGEST,
    repaired_finding_refs: ["finding:a03-001"], regression_suite_ref: "suite:a03", checks_executed: ["check:a"],
    post_repair_artifact_digest: NEXT_DIGEST, state: "changes_required",
  });
  assert.match(c.calls[0].sql, /ops\.v5_a03_seal_review_round/);
  assert.equal(c.calls[0].params.length, 10);
  assert.equal(c.calls[0].params.at(-1), ACTOR.id);
  assert.equal(c.calls[0].params.at(-2), KEY);
  assert.deepEqual([sealed.detections, sealed.case_status], [[], "open"]);

  const adjudicated = await TOOLS["record-complete-set-adjudication"].handler(c, ACTOR, {
    idempotency_key: KEY, case_id: CASE, adjudicator_session_ref: "session:judge", outcome: "fail",
    disputed_finding_refs: ["finding:a03-001"],
  });
  assert.match(c.calls[1].sql, /ops\.v5_a03_record_adjudication/);
  assert.equal(c.calls[1].params.length, 6);
  assert.equal(c.calls[1].params.at(-1), ACTOR.id);
  assert.equal(c.calls[1].params.at(-2), KEY);
  assert.equal(adjudicated.outcome, "fail");
});

test("caller cannot supply actor identity, completion, round count or adjudication receipt", async () => {
  for (const [name, args] of [
    ["open-complete-set-review", { idempotency_key: KEY, change_ref: "change:v5-a03", delivered_set_digest: DIGEST,
      maker_session_ref: "session:maker", maker_actor_id: ACTOR.id }],
    ["record-complete-set-participant", { idempotency_key: KEY, case_id: CASE, role: "reviewer",
      dimension: "architecture", session_ref: "session:reviewer", context_binding: "fresh", actor_id: ACTOR.id }],
    ["seal-complete-set-review-round", { idempotency_key: KEY, case_id: CASE, round_ordinal: 1,
      batch_repair_digest: NEXT_DIGEST, repaired_finding_refs: [], regression_suite_ref: "suite:a03",
      checks_executed: ["check:a"], recorded_rounds: 0 }],
    ["record-complete-set-adjudication", { idempotency_key: KEY, case_id: CASE,
      adjudicator_session_ref: "session:judge", outcome: "fail", disputed_finding_refs: ["finding:a"],
      receipt: { outcome: "pass" } }],
  ]) {
    const c = fakeClient();
    await refused(TOOLS[name].handler(c, ACTOR, args), "unregistered_field");
    assert.equal(c.calls.length, 0, name);
  }
});
