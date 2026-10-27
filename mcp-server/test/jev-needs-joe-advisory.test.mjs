// Test bounded Needs Joe advice, projections and per-item abstentions.
import test from "node:test";
import assert from "node:assert/strict";
import { needsJoeAdvisory } from "../src/jev-needs-joe-advisory.ts";

const NOW = new Date("2026-10-25T12:00:00Z");
const first = { human_ref: "WR-000123", title: "Review synthetic queue advice", state: "ready",
  source: { label: "Synthetic source", freshness: "current" },
  next_human_action: "Review the synthetic acceptance criteria" };
const queue = { ok: true, items: [first, { ...first, human_ref: "WR-000124", title: "Supply synthetic project facts" }] };

function vendorResult() {
  const answers = {};
  for (const [index, ref] of ["WR-000123", "WR-000124"].entries()) {
    const prefix = `item_${index}_${ref}_`;
    const attention = index === 0 ? "decision_ready" : "information_needed";
    answers[`${prefix}class`] = { type: "choice", choice: `${index}|${ref}|${attention}`,
      confidence: 0.9, probabilities: Object.fromEntries(
        ["decision_ready", "information_needed", "blocked", "routine_review", "unclear"]
          .map(value => [`${index}|${ref}|${value}`, value === attention ? 0.92 : 0.02])) };
    for (const [dimension, value] of Object.entries({ priority: index === 0 ? 0.8 : 0.2, relevance: 0.7, ambiguity: 0.1 }))
      answers[`${prefix}${dimension}`] = { type: "noul", noul: value };
  }
  return { model: "jev-1.13.0", answers, usage: { input_tokens: 100, output_tokens: 30 } };
}

function vendor(result = vendorResult()) {
  const requests = [];
  return { requests, fetchImpl: async (url, init) => {
    requests.push({ url, init, body: JSON.parse(init.body) });
    return Response.json(result);
  } };
}

test("typed advice binds separate item judgments and sends only six bounded fields through the official SDK", async () => {
  const remote = vendor();
  const canonical = structuredClone(queue);
  canonical.items[0].private_note = "DO NOT SEND";
  canonical.items[0].source.internal = "DO NOT SEND";
  canonical.items[0].title = "x".repeat(170);
  const before = structuredClone(canonical);
  const advice = await needsJoeAdvisory(canonical, { apiKey: "synthetic-key", ...remote, now: NOW });
  assert.deepEqual(canonical, before);
  assert.equal(advice.status, "available");
  assert.deepEqual(advice.items.map(item => [item.human_ref, item.attention_class, item.priority_probability]),
    [["WR-000123", "decision_ready", 0.8], ["WR-000124", "information_needed", 0.2]]);
  assert.equal(advice.items[0].calibration_status, "unverified_model_output");
  assert.equal(advice.source_observed_at, NOW.toISOString());
  assert.match(advice.snapshot_digest, /^sha256:[a-f0-9]{64}$/);
  assert.match(advice.question_config_digest, /^sha256:[a-f0-9]{64}$/);
  assert.equal(advice.model, "jev-1.13.0");
  assert.equal(remote.requests.length, 1);
  const { url, body, init } = remote.requests[0];
  assert.equal(url, "https://api.typesafe.ai/v1/systemone");
  assert.equal(init.method, "POST");
  assert.equal(body.model, "jev-latest");
  assert.deepEqual(body.state.items[0], { human_ref: "WR-000123", title: "x".repeat(160), state: "ready",
    source_label: "Synthetic source", source_freshness: "current", next_human_action: first.next_human_action });
  assert.equal(JSON.stringify(body).includes("DO NOT SEND"), false);
  assert.equal(Object.keys(body.questions).length, 8);
  for (const [key, question] of Object.entries(body.questions)) {
    const index = key.startsWith("item_0_") ? 0 : 1;
    assert.ok(question.instructions.includes(`state.items[${index}]`));
    assert.ok(question.instructions.includes(index === 0 ? "WR-000123" : "WR-000124"));
  }
});

test("projection failures leave the advisory unavailable without vendor egress", async () => {
  for (const items of [[], null, [first, first], Array.from({ length: 21 }, (_, i) => ({ ...first, human_ref: `WR-${i}` })),
    [{ ...first, human_ref: "WR-000123xxxxxxxxxxxxx" }], [{ ...first, human_ref: "WR-1234567890123" }]]) {
    const remote = vendor();
    const result = await needsJoeAdvisory({ items }, { apiKey: "synthetic-key", ...remote, now: NOW });
    assert.equal(result.status, "unavailable");
    assert.equal(result.reason, "invalid_projection");
    assert.deepEqual(result.items, []);
    assert.equal(remote.requests.length, 0);
  }
});

test("malformed projection metadata cannot make the read fail", async () => {
  const circular = { items: [] };
  circular.items.push(circular);
  for (const input of [undefined, circular]) {
    const remote = vendor();
    const result = await needsJoeAdvisory(input, { apiKey: "synthetic-key", ...remote, now: NOW });
    assert.equal(result.status, "unavailable");
    assert.deepEqual(result.items, []);
    assert.equal(remote.requests.length, 0);
  }
});

test("invalid vendor envelopes are unavailable and disclose no raw vendor fields", async () => {
  const cases = [null, [], { model: "jev-1.13.0", answers: [] }, { model: "arbitrary vendor text", answers: {} }];
  const missing = vendorResult();
  delete missing.answers["item_0_WR-000123_priority"];
  cases.push(missing);
  const extra = vendorResult();
  extra.answers.unrequested = { type: "noul", noul: 0.5 };
  cases.push(extra);
  for (const result of cases) {
    const advice = await needsJoeAdvisory(queue, { apiKey: "synthetic-key", ...vendor(result), now: NOW });
    assert.equal(advice.status, "unavailable");
    assert.equal(advice.reason, "invalid_jev_answer");
    assert.deepEqual(advice.items, []);
  }
});

test("malformed probabilities and cross-item choices abstain only their own item", async () => {
  const changes = [
    answer => { answer.choice = "1|WR-000124|decision_ready"; },
    answer => { answer.type = "score"; },
    answer => { delete answer.probabilities["0|WR-000123|unclear"]; },
    answer => { answer.probabilities["0|WR-000123|unclear"] = 0.9; },
    answer => { answer.probabilities["0|WR-000123|decision_ready"] = -0.1; },
    answer => { answer.confidence = 0.1; },
  ];
  for (const change of changes) {
    const result = vendorResult();
    change(result.answers["item_0_WR-000123_class"]);
    const advice = await needsJoeAdvisory(queue, { apiKey: "synthetic-key", ...vendor(result), now: NOW });
    assert.equal(advice.status, "partial");
    assert.deepEqual(advice.items[0], { human_ref: "WR-000123", index: 0, judged: false, reason_code: "invalid_jev_answer" });
    assert.equal(advice.items[1].attention_class, "information_needed");
  }
  for (const noul of [null, "0.7", -0.1, 1.1]) {
    const result = vendorResult();
    result.answers["item_0_WR-000123_priority"].noul = noul;
    const advice = await needsJoeAdvisory(queue, { apiKey: "synthetic-key", ...vendor(result), now: NOW });
    assert.equal(advice.items[0].judged, false);
    assert.equal(Object.hasOwn(advice.items[0], "priority_probability"), false);
  }
});

test("sparse evidence and unclear classifications return explicit abstentions", async () => {
  const sparse = structuredClone(queue);
  sparse.items[0].next_human_action = "Review";
  let advice = await needsJoeAdvisory(sparse, { apiKey: "synthetic-key", ...vendor(), now: NOW });
  assert.equal(advice.items[0].reason_code, "insufficient_recorded_evidence");
  assert.equal(advice.items[0].judged, false);
  const result = vendorResult();
  result.answers["item_0_WR-000123_class"].choice = "0|WR-000123|unclear";
  result.answers["item_0_WR-000123_class"].probabilities["0|WR-000123|decision_ready"] = 0.02;
  result.answers["item_0_WR-000123_class"].probabilities["0|WR-000123|unclear"] = 0.92;
  advice = await needsJoeAdvisory(queue, { apiKey: "synthetic-key", ...vendor(result), now: NOW });
  assert.equal(advice.items[0].reason_code, "advisory_abstained");
  assert.equal(advice.items[0].judged, false);
});

test("vendor refusal and malformed JSON return unavailable without retries or error disclosure", async () => {
  for (const [response, reason] of [[new Response("synthetic private error", { status: 503 }), "jev_unavailable"],
    [new Response("not-json"), "invalid_jev_answer"]]) {
    let attempts = 0;
    const advice = await needsJoeAdvisory(queue, { apiKey: "synthetic-key", now: NOW,
      fetchImpl: async () => { attempts++; return response; } });
    assert.equal(attempts, 1);
    assert.equal(advice.status, "unavailable");
    assert.equal(advice.reason, reason);
    assert.equal(JSON.stringify(advice).includes("synthetic private error"), false);
  }
});

test("a stalled vendor is aborted at the bounded SDK timeout", async () => {
  let attempts = 0;
  let aborted = false;
  const advice = await needsJoeAdvisory(queue, { apiKey: "synthetic-key", now: NOW,
    fetchImpl: async (_url, init) => {
      attempts++;
      return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => {
        aborted = true;
        reject(new Error("synthetic timeout"));
      }, { once: true }));
    } });
  assert.equal(attempts, 1);
  assert.equal(aborted, true);
  assert.equal(advice.status, "unavailable");
  assert.equal(advice.reason, "jev_unavailable");
});

test("the full twenty-item projection has eighty bound questions and bounded egress", async () => {
  const full = { items: Array.from({ length: 20 }, (_, index) => ({
    human_ref: `WR-${index}`, title: "t".repeat(200), state: "s".repeat(100),
    source: { label: "l".repeat(200), freshness: "f".repeat(100) }, next_human_action: "a".repeat(200),
  })) };
  const remote = vendor({ model: "jev-1.13.0", answers: {} });
  await needsJoeAdvisory(full, { apiKey: "synthetic-key", ...remote, now: NOW });
  const body = remote.requests[0].body;
  assert.equal(body.state.items.length, 20);
  assert.equal(Object.keys(body.questions).length, 80);
  assert.ok(JSON.stringify(body).length < 52000);
  assert.equal(body.state.items[19].state.length, 32);
  assert.equal(body.state.items[19].source_label.length, 80);
  assert.equal(body.state.items[19].source_freshness.length, 32);
  assert.equal(body.state.items[19].next_human_action.length, 160);
});

test("escaped projection text cannot exceed the serialized vendor egress limit", async () => {
  const full = { items: Array.from({ length: 20 }, (_, index) => ({
    human_ref: `WR-${index}`, title: "\u0000".repeat(160), state: "\u0000".repeat(32),
    source: { label: "\u0000".repeat(80), freshness: "\u0000".repeat(32) },
    next_human_action: "\u0000".repeat(160),
  })) };
  const remote = vendor();
  const advice = await needsJoeAdvisory(full, { apiKey: "synthetic-key", ...remote, now: NOW });
  assert.equal(advice.status, "unavailable");
  assert.equal(advice.reason, "egress_limit");
  assert.equal(remote.requests.length, 0);
});
