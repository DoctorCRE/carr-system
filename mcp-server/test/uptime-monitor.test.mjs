import { test } from "node:test";
import assert from "node:assert/strict";
import { probeProduction, runMinute as tickMinute, STATE_KEY } from "../src/uptime-monitor.js";
import worker, { UptimeLedger } from "../uptime-worker.js";
import { build } from "esbuild";
import { Miniflare, Response as WorkerResponse, convertV4MiniflareOptions } from "miniflare";
import { fileURLToPath } from "node:url";

const API = {
  ok: true, ts: "2026-10-05T19:00:00Z", env: { value: "production" },
  provider: "cloudflare-workers", worker_version: { id: "fixture-worker" },
  git_sha: { value: "a".repeat(40) }, verb_count: 407,
  schema: { highest_applied_migration: "fixture.sql", applied_count: 525, reason: null },
};
const APP = {
  service: "doctorcre-app", environment: "production", source_commit: "b".repeat(40),
  provider_version_id: "fixture-app", carr_contract: { schema: "doctorcre-carr-interface.v1", version: "1.43.0" },
  route_contract: { schema: "doctorcre-app-routes.v1", version: "1.20.0" },
};
const DIGEST = { digest: [{ line: "row_counts", value: { deals: 0, leads: 0, clients: 0, vendors: 0 } }] };
const rpc = (value) => ({ jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: JSON.stringify(value) }] } });
const env = { CARR_MCP_PROBE_TOKEN: "fixture-probe", UPTIME_RECORD_TOKEN: "fixture-record", UPTIME_HEALTHCHECKS_PING_URL: "https://hc-ping.com/00000000-0000-4000-8000-000000000021" };
const START = Date.parse("2026-10-05T19:00:00Z");
const runMinute = (storage, bindings, time, fetcher) => tickMinute(storage, bindings, time, fetcher, () => time);

function harness() {
  const values = new Map();
  const calls = [];
  const h = {
    calls, down: false, api: API, app: APP, digest: rpc(DIGEST),
    storage: {
      async get(key) { return structuredClone(values.get(key)); },
      async put(key, value) { values.set(key, structuredClone(value)); },
    },
    async fetch(url, options = {}) {
      calls.push({ url, ...options });
      if (url.startsWith("https://hc-ping.com/")) return new Response("OK");
      if (h.down) return new Response("unavailable", { status: 503 });
      if (url.endsWith("/release")) return Response.json(h.api);
      if (url.endsWith("/app-release")) return Response.json(h.app);
      const name = JSON.parse(options.body).params.name;
      if (name === "integrity-digest") return Response.json(h.digest);
      if (name === "add-loop") return Response.json(rpc({ ok: true, loop_id: "00000000-0000-4000-8000-000000000001" }));
      if (name === "read-loop") return Response.json(rpc({ loop_id: "00000000-0000-4000-8000-000000000001", version: 1, status: "open" }));
      if (name === "close-loop") return Response.json(rpc({ ok: true }));
      throw new Error(`unexpected verb ${name}`);
    },
  };
  return h;
}

test("validates all three production contracts, including the read-only verb", async () => {
  const h = harness();
  const result = await probeProduction(env, h.fetch);
  assert.equal(result.ok, true);
  assert.deepEqual(result.checks.map(({ name, ok }) => ({ name, ok })), [
    { name: "api-release", ok: true }, { name: "app-release", ok: true }, { name: "verb-round-trip", ok: true },
  ]);
  assert.equal(JSON.parse(h.calls[2].body).params.name, "integrity-digest");
});

test("confirms on third failure, alerts once through a prolonged outage, then recovers and files one timed loop", async () => {
  const h = harness();
  h.down = true;
  for (let minute = 0; minute < 6; minute++) {
    await runMinute(h.storage, env, START + minute * 60000, h.fetch);
    assert.equal(h.calls.filter((call) => call.url.endsWith("/fail")).length, minute < 2 ? 0 : 1);
  }
  let state = await h.storage.get(STATE_KEY);
  assert.equal(state.incidents.length, 1);
  assert.equal(state.incidents[0].first_failure_at, "2026-10-05T19:00:00.000Z");
  assert.equal(state.incidents[0].confirmed_at, "2026-10-05T19:02:00.000Z");
  assert.equal(state.incidents[0].loop_id, null);
  h.down = false;
  await runMinute(h.storage, env, START + 6 * 60000, h.fetch);
  await runMinute(h.storage, env, START + 7 * 60000, h.fetch);
  state = await h.storage.get(STATE_KEY);
  assert.equal(state.incidents[0].recovered_at, "2026-10-05T19:06:00.000Z");
  assert.equal(state.incidents[0].loop_closed, true);
  assert.equal(state.incidents[0].recovery_sent, true);
  const verbs = h.calls.filter((call) => call.url.endsWith("/mcp")).map((call) => JSON.parse(call.body).params);
  assert.equal(verbs.filter((call) => call.name === "add-loop").length, 1);
  assert.match(verbs.find((call) => call.name === "add-loop").arguments.body, /19:00:00.*19:02:00.*recovered pending/s);
  assert.match(verbs.find((call) => call.name === "close-loop").arguments.outcome, /19:00:00.*19:02:00.*19:06:00/s);
  assert.match(verbs.find((call) => call.name === "close-loop").arguments.outcome, /360 seconds/);
  assert.match(state.action, /owner orchestrator.*fix:.*verify:.*auto-clear:/);
});

test("HTTP 200 with wrong release, app, or nested RPC shape fails the respective probe", async () => {
  for (const [field, fixture, check] of [
    ["api", { ok: true }, "api-release"], ["app", { service: "doctorcre-app" }, "app-release"],
    ["digest", { jsonrpc: "2.0", id: 1, result: {} }, "verb-round-trip"],
    ["digest", rpc({ digest: [] }), "verb-round-trip"],
    ["digest", { ...rpc(DIGEST), result: { ...rpc(DIGEST).result, isError: true } }, "verb-round-trip"],
  ]) {
    const h = harness(); h[field] = fixture;
    const result = await probeProduction(env, h.fetch);
    assert.equal(result.ok, false);
    assert.equal(result.checks.find((item) => item.name === check).ok, false);
  }
});

test("slow response headers and slow JSON bodies both time out", async () => {
  for (const fetcher of [
    () => new Promise(() => {}),
    async () => ({ ok: true, json: () => new Promise(() => {}) }),
  ]) {
    const result = await probeProduction(env, fetcher, 10);
    assert.deepEqual(result.checks.map((check) => check.reason), ["timeout", "timeout", "timeout"]);
  }
});

test("success resets the streak; duplicate, old, and missed scheduled minutes cannot confirm an incident", async () => {
  const h = harness(); h.api = {};
  await runMinute(h.storage, env, START, h.fetch);
  await runMinute(h.storage, env, START + 60000, h.fetch);
  const count = h.calls.length;
  await runMinute(h.storage, env, START + 60000, h.fetch);
  await runMinute(h.storage, env, START, h.fetch);
  assert.equal(h.calls.length, count);
  await runMinute(h.storage, env, START + 180000, h.fetch);
  assert.equal((await h.storage.get(STATE_KEY)).failures, 1);
  h.api = API;
  await runMinute(h.storage, env, START + 240000, h.fetch);
  h.api = {};
  await runMinute(h.storage, env, START + 300000, h.fetch);
  assert.equal((await h.storage.get(STATE_KEY)).failures, 1);
  assert.equal((await h.storage.get(STATE_KEY)).incidents.length, 0);
});

test("incident timings use observation time when the scheduler delivers late", async () => {
  const h = harness(); h.api = {};
  for (let i = 0; i < 3; i++) {
    await tickMinute(h.storage, env, START + i * 60000, h.fetch, () => START + i * 60000 + 15000);
  }
  const state = await h.storage.get(STATE_KEY);
  assert.equal(state.incidents[0].first_failure_at, "2026-10-05T19:00:15.000Z");
  assert.equal(state.incidents[0].confirmed_at, "2026-10-05T19:02:15.000Z");
});

test("an interrupted add-loop retries the exact payload after recovery", async () => {
  const h = harness(); h.api = {};
  let firstPayload;
  let committed = false;
  const fetcher = async (url, options) => {
    const params = url.endsWith("/mcp") ? JSON.parse(options.body).params : null;
    if (params?.name === "add-loop") {
      if (!committed) {
        firstPayload = options.body; committed = true;
        throw new Error("response lost after commit");
      }
      assert.equal(options.body, firstPayload);
    }
    return h.fetch(url, options);
  };
  for (let i = 0; i < 3; i++) await runMinute(h.storage, env, START + i * 60000, fetcher);
  h.api = API;
  await runMinute(h.storage, env, START + 180000, fetcher);
  assert.equal((await h.storage.get(STATE_KEY)).incidents[0].loop_closed, true);
});

test("missing secrets and a rejected Healthchecks ping retain delivery debt without claiming healthy", async () => {
  const h = harness(); h.api = {};
  const missing = { CARR_MCP_PROBE_TOKEN: env.CARR_MCP_PROBE_TOKEN };
  for (let i = 0; i < 3; i++) await runMinute(h.storage, missing, START + i * 60000, h.fetch);
  let state = await h.storage.get(STATE_KEY);
  assert.deepEqual(state.configuration_missing, ["UPTIME_RECORD_TOKEN", "UPTIME_HEALTHCHECKS_PING_URL"]);
  assert.equal(state.pending_alerts, 1);
  assert.equal(state.pending_records, 1);
  const rejected = async (url, options) => url.startsWith("https://hc-ping.com/") ? new Response("OK (not found)") : h.fetch(url, options);
  await runMinute(h.storage, env, START + 180000, rejected);
  state = await h.storage.get(STATE_KEY);
  assert.equal(state.incidents[0].down_sent, false);
  h.api = API;
  await runMinute(h.storage, env, START + 240000, h.fetch);
  state = await h.storage.get(STATE_KEY);
  assert.equal(state.ok, true);
  assert.equal(state.pending_alerts, 0);
  assert.equal(state.pending_records, 0);
});

test("all probes must pass for recovery; a later outage gets a separate incident and loop key", async () => {
  const h = harness(); h.api = {};
  for (let i = 0; i < 3; i++) await runMinute(h.storage, env, START + i * 60000, h.fetch);
  h.api = API; h.app = {};
  await runMinute(h.storage, env, START + 180000, h.fetch);
  assert.equal((await h.storage.get(STATE_KEY)).incidents[0].recovery_sent, false);
  h.app = APP;
  await runMinute(h.storage, env, START + 240000, h.fetch);
  h.api = {};
  for (let i = 5; i < 8; i++) await runMinute(h.storage, env, START + i * 60000, h.fetch);
  const state = await h.storage.get(STATE_KEY);
  assert.equal(state.incidents.length, 2);
  assert.equal(h.calls.filter((call) => call.url.endsWith("/fail")).length, 2);
  const keys = h.calls.filter((call) => call.url.endsWith("/mcp"))
    .map((call) => JSON.parse(call.body).params).filter((call) => call.name === "add-loop")
    .map((call) => call.arguments.idempotency_key);
  assert.deepEqual(keys, ["uptime:production-29853782:open", "uptime:production-29853787:open"]);
});

test("Cron entrypoint serializes overlapping delivery and only exposes read-only health", async () => {
  const h = harness(); h.api = {};
  const original = globalThis.fetch;
  globalThis.fetch = h.fetch;
  try {
    const ledger = new UptimeLedger({ storage: h.storage }, env);
    const bindings = { UPTIME_LEDGER: { idFromName: () => "production", get: () => ledger } };
    await Promise.all([0, 1, 1, 2].map((minute) => worker.scheduled({ scheduledTime: START + minute * 60000 }, bindings)));
    assert.equal(h.calls.filter((call) => call.url.endsWith("/fail")).length, 1);
    const response = await worker.fetch(new Request("https://monitor.invalid/healthz"), bindings);
    const body = await response.json();
    assert.equal(response.status, 503);
    assert.equal(body.failures, 3);
    assert.equal(body.schema, "carr-uptime.v1");
    assert.match(body.action, /one deduplicated uptime incident loop/);
    assert.equal(JSON.stringify(body).includes("fixture-probe"), false);
    assert.equal((await worker.fetch(new Request("https://monitor.invalid/tick", { method: "POST" }), bindings)).status, 404);
  } finally { globalThis.fetch = original; }
});

test("real workerd Cron and SQLite ledger retain the incident across Worker reload", async () => {
  const h = harness(); h.api = {};
  const bundled = await build({ entryPoints: [fileURLToPath(new URL("../uptime-worker.js", import.meta.url))], bundle: true, write: false, format: "esm" });
  const options = {
    modules: true, script: bundled.outputFiles[0].text, compatibilityDate: "2026-09-01",
    bindings: env, durableObjects: { UPTIME_LEDGER: { className: "UptimeLedger", useSQLite: true } },
    outboundService: async (request) => {
      const response = await h.fetch(request.url, { method: request.method, body: await request.text() });
      return new WorkerResponse(await response.text(), { status: response.status });
    },
  };
  const persistence = {
    resourcePersistencePath: fileURLToPath(new URL(`../../out/_to_delete/uptime-fixture-${crypto.randomUUID()}/`, import.meta.url)),
    unsafeEnableSharedStorage: false,
  };
  const mf = new Miniflare({ ...convertV4MiniflareOptions(options), ...persistence });
  try {
    for (let i = 0; i < 3; i++) {
      await (await mf.getWorker()).scheduled({ scheduledTime: new Date(START + i * 60000), cron: "* * * * *" });
    }
    await mf.setOptions({ ...convertV4MiniflareOptions({ ...options, bindings: { ...env, RELOAD_FIXTURE: "1" } }), ...persistence });
    const down = await mf.dispatchFetch("https://monitor.invalid/healthz");
    assert.equal(down.status, 503);
    assert.equal((await down.json()).failures, 3);
    h.api = API;
    await (await mf.getWorker()).scheduled({ scheduledTime: new Date(START + 180000), cron: "* * * * *" });
    const recovered = await mf.dispatchFetch("https://monitor.invalid/healthz");
    const status = await recovered.json();
    assert.equal(recovered.status, 200, JSON.stringify({ status, calls: h.calls.map((call) => call.url) }));
    assert.equal(status.pending_records, 0);
    assert.equal(status.active_incident, null);
    assert.equal(h.calls.filter((call) => call.url.endsWith("/fail")).length, 1);
    assert.equal(h.calls.filter((call) => call.url.startsWith("https://hc-ping.com/") && !call.url.endsWith("/fail")).length, 3);
  } finally { await mf.dispose(); }
});

test("historical recovery never clears a newer confirmed outage", async () => {
  const h = harness();
  let rejectSuccess = false;
  let channel = "up";
  const accepted = [];
  const fetcher = async (url, options) => {
    if (url.startsWith("https://hc-ping.com/")) {
      if (!url.endsWith("/fail") && rejectSuccess) throw new Error("offline");
      channel = url.endsWith("/fail") ? "down" : "up";
      accepted.push(channel);
    }
    return h.fetch(url, options);
  };
  h.api = {};
  for (let minute = 0; minute < 3; minute++) await runMinute(h.storage, env, START + minute * 60000, fetcher);
  h.api = API; rejectSuccess = true;
  await runMinute(h.storage, env, START + 180000, fetcher);
  h.api = {};
  for (let minute = 4; minute < 6; minute++) await runMinute(h.storage, env, START + minute * 60000, fetcher);
  rejectSuccess = false;
  const before = accepted.length;
  const state = await runMinute(h.storage, env, START + 360000, fetcher);
  assert.ok(state.active_incident);
  assert.equal(channel, "down");
  assert.deepEqual(accepted.slice(before), ["down"]);
  assert.equal(state.incidents[1].down_sent, true);
  h.api = API;
  const recovered = await runMinute(h.storage, env, START + 420000, fetcher);
  assert.equal(channel, "up");
  assert.equal(recovered.pending_alerts, 0);
});

test("provider clock stays alive below three failures without clearing a confirmed incident", async () => {
  const h = harness();
  let providerTime = 0;
  let expires = -1;
  let channel = "up";
  const fetcher = async (url, options) => {
    if (url.startsWith("https://hc-ping.com/")) {
      channel = url.endsWith("/fail") ? "down" : "up";
      expires = providerTime + 180000;
    }
    return h.fetch(url, options);
  };
  const tick = async (minute, duration = 0) => {
    providerTime = minute * 60000 + duration;
    return tickMinute(h.storage, env, START + minute * 60000, fetcher, () => START + providerTime);
  };
  await tick(0);
  h.api = {};
  await tick(1); await tick(2);
  assert.ok(expires > 188000, `dead-man expires at ${expires}`);
  h.api = API;
  assert.equal((await tick(3, 8000)).incidents.length, 0);
  h.api = {};
  await tick(4); await tick(5); await tick(6);
  assert.equal(channel, "down");
  await tick(7);
  assert.equal(channel, "down");
});

test("reload after every persisted effect boundary is non-green and the same slot resumes", async () => {
  for (const boundary of [2, 3, 4, 5]) {
    const h = harness();
    await runMinute(h.storage, env, START, h.fetch);
    h.api = {};
    await runMinute(h.storage, env, START + 60000, h.fetch);
    await runMinute(h.storage, env, START + 120000, h.fetch);
    const put = h.storage.put;
    let writes = 0;
    h.storage.put = async (...args) => {
      if (++writes >= boundary) throw new Error("storage interruption");
      await put(...args);
    };
    const original = globalThis.fetch;
    globalThis.fetch = h.fetch;
    try {
      await assert.rejects(runMinute(h.storage, env, START + 180000, h.fetch), /storage interruption/);
      const saved = await h.storage.get(STATE_KEY);
      assert.equal(saved.ok, false);
      const ledger = new UptimeLedger({ storage: h.storage }, env);
      assert.equal((await ledger.fetch(new Request("https://ledger/healthz"))).status, 503);
      const count = h.calls.filter((call) => call.url.endsWith("/release")).length;
      h.storage.put = put;
      const resumed = await ledger.fetch(new Request("https://ledger/tick", {
        method: "POST", body: JSON.stringify({ scheduledTime: START + 180000 }),
      }));
      assert.equal(resumed.status, 503);
      const finished = await h.storage.get(STATE_KEY);
      assert.equal(finished.finalized_slot, finished.last_slot);
      assert.equal(finished.failures, 3);
      assert.equal(finished.pending_alerts, 0);
      assert.equal(finished.pending_records, 0);
      assert.equal(h.calls.filter((call) => call.url.endsWith("/release")).length, count);
    } finally { globalThis.fetch = original; }
  }
});

test("interruption after a healthy tick cannot persist false green on its first failed sample", async () => {
  const h = harness();
  await runMinute(h.storage, env, START, h.fetch);
  h.api = {};
  const put = h.storage.put;
  let writes = 0;
  h.storage.put = async (...args) => {
    if (++writes > 1) throw new Error("storage interruption");
    await put(...args);
  };
  await assert.rejects(runMinute(h.storage, env, START + 60000, h.fetch));
  assert.equal((await h.storage.get(STATE_KEY)).ok, false);
});

test("terminal dropped records and refused older records do not starve later incidents", async () => {
  for (const status of ["dropped", "unrecognized"]) {
    const h = harness();
    let opens = 0;
    const fetcher = async (url, options) => {
      const params = url.endsWith("/mcp") ? JSON.parse(options.body).params : null;
      if (params?.name === "add-loop") return Response.json(rpc({ ok: true, loop_id: `fixture-${++opens}` }));
      if (params?.name === "read-loop") return Response.json(rpc({ loop_id: params.arguments.loop_id, version: 2, status }));
      return h.fetch(url, options);
    };
    h.api = {};
    for (let minute = 0; minute < 3; minute++) await runMinute(h.storage, env, START + minute * 60000, fetcher);
    h.api = API;
    await runMinute(h.storage, env, START + 180000, fetcher);
    h.api = {};
    for (let minute = 4; minute < 7; minute++) await runMinute(h.storage, env, START + minute * 60000, fetcher);
    const state = await h.storage.get(STATE_KEY);
    assert.equal(opens, 2);
    assert.equal(state.incidents[1].loop_id, "fixture-2");
    if (status === "dropped") {
      assert.equal(state.incidents[0].loop_closed, true);
      assert.equal(state.incidents[0].loop_status, "dropped");
    } else assert.ok(state.record_error);
  }
});
