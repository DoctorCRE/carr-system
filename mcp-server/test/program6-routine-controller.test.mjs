import test from "node:test";
import assert from "node:assert/strict";
import { createProgram6RoutineController, SAFE_READY_PLAN } from "../src/program6-routine-controller.js";

const ACTOR = { slug: "joe", human: true, via: "dealroom-cookie" };
const SESSION = { key: "opaque-session", csrfToken: "csrf", reauthAt: 123 };
const REF = "WR-000123";
const HASH_A = `sha256:${"a".repeat(64)}`;
const HASH_B = `sha256:${"b".repeat(64)}`;

function request(path, method = "GET", body) {
  return new Request(`https://dealroom.doctorcre.com${path}`, {
    method,
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function subject(overrides = {}) {
  const calls = [];
  const authorizations = [];
  const controller = createProgram6RoutineController({
    callToolFn: async (_env, actor, name, args, profile) => {
      calls.push({ actor, name, args, profile });
      return { human_ref: args.human_ref || args.work_request, state: "ready", version: 3, durable: true };
    },
    authorizeAction: async (input) => { authorizations.push(input); return null; },
    ...overrides,
  });
  return { controller, calls, authorizations };
}

test("read endpoint is exact and returns registered durable card readback", async () => {
  const { controller, calls } = subject();
  const response = await controller.fetch(request(`/api/system-work/${REF}`), {}, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, data: { human_ref: REF, state: "ready", version: 3, durable: true } });
  assert.deepEqual(calls, [{ actor: ACTOR, name: "work-request-card", args: { work_request: REF }, profile: "full" }]);
});

test("current endpoint is a fixed authenticated collection read with no browser filters", async () => {
  const { controller, calls } = subject({
    callToolFn: async (_env, actor, name, args, profile) => {
    calls.push({ actor, name, args, profile });
    return { ok: true, items: [{ human_ref: REF, state: "captured", source: { freshness: "current" } }] };
  } });
  const response = await controller.fetch(request("/api/system-work/current"), {}, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.deepEqual(body.data.items, [{ human_ref: REF, state: "captured", source: { freshness: "current" } }]);
  assert.equal(Object.hasOwn(body.data, "advisory"), false);
  assert.deepEqual(calls, [{ actor: ACTOR, name: "current-work-requests", args: {}, profile: "full" }]);
  const withQuery = await controller.fetch(request("/api/system-work/current?state=ready"), {}, {}, ACTOR, SESSION);
  assert.equal(withQuery.status, 200, "query parameters cannot select collection scope");
});

test("report route admits only the capture material and never a caller-selected verb or authority", async () => {
  const { controller, calls, authorizations } = subject();
  const body = { idempotency_key: "10000000-0000-0000-0000-000000000001", situation: "stale intake", title: "Source refresh", desired_outcome: "Current source", acceptance_criteria: [{ id: "SOURCE", text: "source is current" }] };
  const response = await controller.fetch(request("/api/system-work/report", "POST", body), {}, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  assert.deepEqual(calls[0], { actor: ACTOR, name: "report-problem", args: body, profile: "full" });
  assert.equal(authorizations[0].action, "report-problem");
  assert.equal(authorizations[0].args.actor, undefined);

  const refused = await controller.fetch(request("/api/system-work/report", "POST", { ...body, actor: "joe" }), {}, {}, ACTOR, SESSION);
  assert.equal(refused.status, 400);
  assert.equal((await refused.json()).error, "invalid_request_fields");
  const incomplete = await controller.fetch(request("/api/system-work/report", "POST", {
    idempotency_key: body.idempotency_key, situation: body.situation,
  }), {}, {}, ACTOR, SESSION);
  assert.equal(incomplete.status, 400);
  assert.equal((await incomplete.json()).error, "invalid_request_fields");
  assert.equal(calls.length, 1);
});

test("fixed ready-plan route derives every operational control server-side", async () => {
  assert.equal(SAFE_READY_PLAN.runbook_ref, "doctrine:runbook#diagnosis-checklist-in-order-2-minutes");
  const { controller, calls, authorizations } = subject();
  const body = { idempotency_key: "10000000-0000-0000-0000-000000000002", base_version: 2, scope_summary: "Make this review routine usable." };
  const response = await controller.fetch(request(`/api/system-work/${REF}/plan`, "POST", body), {}, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  assert.deepEqual(calls[0], { actor: ACTOR, name: "propose-ready-plan", args: { ...body, human_ref: REF, ...SAFE_READY_PLAN }, profile: "full" });
  assert.deepEqual(authorizations[0].args, { ...body, human_ref: REF, ...SAFE_READY_PLAN });

  const refused = await controller.fetch(request(`/api/system-work/${REF}/plan`, "POST", { ...body, runbook_ref: "doctrine:runbook#other" }), {}, {}, ACTOR, SESSION);
  assert.equal(refused.status, 400);
  assert.equal((await refused.json()).error, "invalid_request_fields");
  assert.equal(calls.length, 1);
});

test("human state changes use the path ref, route-selected tool, and authorization boundary before dispatch", async () => {
  const { controller, calls, authorizations } = subject();
  const body = { idempotency_key: "10000000-0000-0000-0000-000000000003", base_version: 1, classification: "operational" };
  const response = await controller.fetch(request(`/api/system-work/${REF}/triage`, "POST", body), {}, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  assert.deepEqual(calls[0], { actor: ACTOR, name: "review-and-triage", args: { ...body, human_ref: REF }, profile: "full" });
  assert.deepEqual(authorizations[0].args, { ...body, human_ref: REF });
  assert.equal(authorizations[0].session, SESSION);
});

test("outcome acceptance is hash-selected and returns registered readback without a cosmetic feedback path", async () => {
  const { controller, calls } = subject();
  const body = { idempotency_key: "10000000-0000-0000-0000-000000000004", base_version: 3, feedback_hash: HASH_B };
  const response = await controller.fetch(request(`/api/system-work/${REF}/outcomes/accept`, "POST", body), {}, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  assert.deepEqual(calls[0], { actor: ACTOR, name: "accept-outcome-feedback", args: { ...body, human_ref: REF }, profile: "full" });
  const invalid = await controller.fetch(request(`/api/system-work/${REF}/outcomes/OUTCOME-a1b2c3d4e5f6-v1/accept`, "POST", body), {}, {}, ACTOR, SESSION);
  assert.equal(invalid.status, 404);
});

test("authorization refusal is returned before a registered mutation and tool errors stay structured", async () => {
  const { controller, calls } = subject({ authorizeAction: async () => new Response(JSON.stringify({ error: "reauth_required" }), { status: 401 }) });
  const body = { idempotency_key: "10000000-0000-0000-0000-000000000005", base_version: 3, plan_hash: HASH_A };
  const response = await controller.fetch(request(`/api/system-work/${REF}/plan/accept`, "POST", body), {}, {}, ACTOR, SESSION);
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "reauth_required");
  assert.equal(calls.length, 0);
});

test("current GET preserves the canonical queue when Jev credentials are unavailable", async () => {
  const canonical = { ok: true, items: [{ human_ref: REF, state: "ready" }] };
  const { controller } = subject({ callToolFn: async () => canonical });
  const response = await controller.fetch(request("/api/system-work/current"), {}, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.deepEqual(body.data, canonical);
  assert.equal(body.advisory.schema, "jev_c13_decision_queue_advisory/v1");
  assert.equal(body.advisory.status, "unavailable");
  assert.equal(body.advisory.reason, "missing_credentials");
  assert.deepEqual(body.advisory.items, []);
});

test("current GET returns vendor advice beside unchanged authorized data", async () => {
  const canonical = { ok: true, items: [{ human_ref: REF, title: "Synthetic ready decision", state: "ready",
    source: { label: "Synthetic", freshness: "current" }, next_human_action: "Review the synthetic acceptance criteria" }],
    next_human_action: { effect: "none" } };
  const before = structuredClone(canonical);
  const { controller, calls } = subject({
    callToolFn: async (_env, actor, name, args, profile) => {
      calls.push({ actor, name, args, profile });
      return canonical;
    },
    jevFetch: async () => Response.json({ model: "jev-1.13.0", answers: {
      [`item_0_${REF}_class`]: { type: "choice", choice: `0|${REF}|decision_ready`, confidence: 0.9,
        probabilities: { [`0|${REF}|decision_ready`]: 0.9, [`0|${REF}|information_needed`]: 0.025,
          [`0|${REF}|blocked`]: 0.025, [`0|${REF}|routine_review`]: 0.025, [`0|${REF}|unclear`]: 0.025 } },
      [`item_0_${REF}_priority`]: { type: "noul", noul: 0.8 },
      [`item_0_${REF}_relevance`]: { type: "noul", noul: 0.7 },
      [`item_0_${REF}_ambiguity`]: { type: "noul", noul: 0.1 },
    } }),
  });
  const waits = [];
  const response = await controller.fetch(request("/api/system-work/current?tenant=other&state=ready"),
    { TYPESAFE_API_KEY: "synthetic-key" }, { waitUntil: value => waits.push(value) }, ACTOR, SESSION);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.deepEqual(body.data, before);
  assert.deepEqual(canonical, before);
  assert.equal(body.advisory.status, "available");
  assert.equal(body.advisory.items[0].attention_class, "decision_ready");
  assert.deepEqual(calls, [{ actor: ACTOR, name: "current-work-requests", args: {}, profile: "full" }]);
  assert.deepEqual(waits, []);
});

test("vendor failure cannot fail the authorized queue read", async () => {
  const canonical = { ok: true, items: [{ human_ref: REF, title: "Synthetic decision", state: "ready",
    source: { label: "Synthetic", freshness: "current" }, next_human_action: "Review the synthetic record" }] };
  let attempts = 0;
  const { controller } = subject({ callToolFn: async () => canonical,
    jevFetch: async () => { attempts++; throw new Error("private synthetic diagnostics"); } });
  const response = await controller.fetch(request("/api/system-work/current"),
    { TYPESAFE_API_KEY: "synthetic-key" }, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.deepEqual(body.data, canonical);
  assert.equal(body.advisory.status, "unavailable");
  assert.equal(JSON.stringify(body).includes("private synthetic diagnostics"), false);
  assert.equal(attempts, 1);
});

test("canonical read failure remains a failure and sends no vendor request", async () => {
  let attempts = 0;
  const { controller } = subject({ callToolFn: async () => { throw new Error("canonical read failure"); },
    jevFetch: async () => { attempts++; return Response.json({}); } });
  const response = await controller.fetch(request("/api/system-work/current"),
    { TYPESAFE_API_KEY: "synthetic-key" }, {}, ACTOR, SESSION);
  assert.equal(response.status, 500);
  assert.deepEqual(await response.json(), { error: "system_work_read_failed" });
  assert.equal(attempts, 0);
});
