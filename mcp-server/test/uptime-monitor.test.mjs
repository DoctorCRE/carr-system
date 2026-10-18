import { test } from "node:test";
import assert from "node:assert/strict";
import {
  inQuietWindow, probeProduction, QUIET_END_HOUR, QUIET_START_HOUR, QUIET_TIME_ZONE, ROUND_TRIP_VERB,
  runMinute as tickMinute, STATE_KEY,
} from "../src/uptime-monitor.js";
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
const PROFILES = { ok: true, total: 2, profiles: [
  { profile_key: "builder", display_name: "Builder", charter: [], current_model: "fixture", current_desk: "hermes-desktop", sponsor_scope: "shared", status: "active", version: "2" },
  { profile_key: "doc", display_name: "Doc", charter: [], current_model: null, current_desk: null, sponsor_scope: "shared", status: "unstaffed", version: "1" },
] };
const rpc = (value) => ({ jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: JSON.stringify(value) }] } });
const env = { CARR_MCP_PROBE_TOKEN: "fixture-probe", UPTIME_RECORD_TOKEN: "fixture-record", UPTIME_HEALTHCHECKS_PING_URL: "https://hc-ping.com/00000000-0000-4000-8000-000000000021" };
const START = Date.parse("2026-10-05T19:00:00Z");
const runMinute = (storage, bindings, time, fetcher) => tickMinute(storage, bindings, time, fetcher, () => time);

function harness() {
  const values = new Map();
  const calls = [];
  const h = {
    calls, down: false, api: API, app: APP, profiles: rpc(PROFILES),
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
      if (name === "read-profiles") return Response.json(h.profiles);
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
  assert.equal(ROUND_TRIP_VERB, "read-profiles");
  assert.deepEqual(JSON.parse(h.calls[2].body).params, { name: "read-profiles", arguments: {} });
  assert.equal(h.calls[2].headers.Authorization, "Bearer fixture-probe");
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
    ["profiles", { jsonrpc: "2.0", id: 1, result: {} }, "verb-round-trip"],
    ["profiles", rpc({ ok: true, total: 0, profiles: [] }), "verb-round-trip"],
    ["profiles", rpc({ ...PROFILES, ok: false }), "verb-round-trip"],
    ["profiles", rpc({ ...PROFILES, total: 3 }), "verb-round-trip"],
    ["profiles", rpc({ ...PROFILES, profiles: [{ ...PROFILES.profiles[0], status: "retired" }] }), "verb-round-trip"],
    ["profiles", rpc({ ...PROFILES, profiles: [{ ...PROFILES.profiles[0], profile_key: "" }] }), "verb-round-trip"],
    ["profiles", rpc({ digest: [{ line: "row_counts", value: { deals: 0, leads: 0, clients: 0, vendors: 0 } }] }), "verb-round-trip"],
    ["profiles", { ...rpc(PROFILES), result: { ...rpc(PROFILES).result, isError: true } }, "verb-round-trip"],
  ]) {
    const h = harness(); h[field] = fixture;
    const result = await probeProduction(env, h.fetch);
    assert.equal(result.ok, false);
    assert.equal(result.checks.find((item) => item.name === check).ok, false);
  }
});

const DB_FAILURES = [
  ["database error surfaced as a tool error", () => Response.json({ jsonrpc: "2.0", id: 1,
    result: { isError: true, content: [{ type: "text", text: JSON.stringify({ error: "internal_error" }) }] } }), "invalid_json_or_transport"],
  ["JSON-RPC error", () => Response.json({ jsonrpc: "2.0", id: 1, error: { code: -32603, message: "fixture" } }), "invalid_json_or_transport"],
  ["wrong shape", () => Response.json(rpc({ ok: true, total: 0, profiles: [] })), "wrong_shape"],
  ["HTTP 500", () => new Response("fixture", { status: 500 }), "http_500"],
  ["HTTP 401", () => new Response("fixture", { status: 401 }), "http_401"],
];

test("a broken verb round trip alone fails the sample with its own reason", async () => {
  for (const [label, respond, reason] of DB_FAILURES) {
    const h = harness();
    const fetcher = async (url, options = {}) => (url.endsWith("/mcp") ? (h.calls.push({ url, ...options }), respond()) : h.fetch(url, options));
    const result = await probeProduction(env, fetcher);
    assert.equal(result.ok, false, label);
    assert.deepEqual(result.checks.map(({ name, ok }) => ({ name, ok })), [
      { name: "api-release", ok: true }, { name: "app-release", ok: true }, { name: "verb-round-trip", ok: false },
    ], label);
    assert.equal(result.checks[2].reason, reason, label);
  }
});

test("three verb-round-trip failures confirm one incident, alert once, and recovery files and closes one loop", async () => {
  for (const [label, respond] of DB_FAILURES) {
    const h = harness();
    let broken = true;
    const fetcher = async (url, options = {}) => {
      if (broken && url.endsWith("/mcp") && JSON.parse(options.body).params.name === "read-profiles") {
        h.calls.push({ url, ...options });
        return respond();
      }
      return h.fetch(url, options);
    };
    for (let minute = 0; minute < 4; minute++) {
      await runMinute(h.storage, env, START + minute * 60000, fetcher);
      assert.equal(h.calls.filter((call) => call.url.endsWith("/fail")).length, minute < 2 ? 0 : 1, label);
    }
    let state = await h.storage.get(STATE_KEY);
    assert.equal(state.incidents.length, 1, label);
    assert.equal(state.incidents[0].checks.find((check) => check.name === "verb-round-trip").ok, false, label);
    // The record writer is gated on a healthy round trip, so no loop is attempted while it is down.
    assert.equal(state.incidents[0].loop_id, null, label);
    broken = false;
    await runMinute(h.storage, env, START + 4 * 60000, fetcher);
    state = await h.storage.get(STATE_KEY);
    assert.equal(state.active_incident, null, label);
    assert.equal(state.incidents[0].loop_closed, true, label);
    assert.equal(state.incidents[0].recovery_sent, true, label);
  }
});

test("the minute probe never calls integrity-digest, healthy or failing", async () => {
  const h = harness();
  for (let minute = 0; minute < 3; minute++) await runMinute(h.storage, env, START + minute * 60000, h.fetch);
  h.down = true;
  for (let minute = 3; minute < 7; minute++) await runMinute(h.storage, env, START + minute * 60000, h.fetch);
  h.down = false;
  await runMinute(h.storage, env, START + 7 * 60000, h.fetch);
  const verbs = h.calls.filter((call) => call.url.endsWith("/mcp")).map((call) => JSON.parse(call.body).params.name);
  assert.equal(verbs.filter((name) => name === "read-profiles").length, 8);
  assert.equal(verbs.includes("integrity-digest"), false);
  assert.deepEqual([...new Set(verbs)].sort(), ["add-loop", "close-loop", "read-loop", "read-profiles"]);
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

// Overnight quiet window: production Neon may sleep 01:00-06:00 America/Chicago.
const at = (stamp) => Date.parse(stamp);
const dbCalls = (h) => h.calls.filter((call) => call.url.endsWith("/release") || call.url.endsWith("/mcp"));
const appCalls = (h) => h.calls.filter((call) => call.url.endsWith("/app-release"));
async function tickRange(h, from, to) {
  for (let time = from; time < to; time += 60000) await runMinute(h.storage, env, time, h.fetch);
}

test("quiet window is one Chicago-local constant pair with exact boundary minutes", () => {
  assert.deepEqual([QUIET_TIME_ZONE, QUIET_START_HOUR, QUIET_END_HOUR], ["America/Chicago", 1, 6]);
  // 2026-10-12 is CDT (UTC-5).
  assert.equal(inQuietWindow(at("2026-10-12T05:59:00Z")), false); // 00:59
  assert.equal(inQuietWindow(at("2026-10-12T06:00:00Z")), true); // 01:00
  assert.equal(inQuietWindow(at("2026-10-12T10:59:00Z")), true); // 05:59
  assert.equal(inQuietWindow(at("2026-10-12T11:00:00Z")), false); // 06:00
});

test("quiet window follows the fall-back DST edge on 2026-11-01", () => {
  assert.equal(inQuietWindow(at("2026-11-01T05:59:00Z")), false); // 00:59 CDT
  assert.equal(inQuietWindow(at("2026-11-01T06:00:00Z")), true); // 01:00 CDT
  assert.equal(inQuietWindow(at("2026-11-01T07:00:00Z")), true); // 01:00 CST, the repeated hour
  assert.equal(inQuietWindow(at("2026-11-01T11:30:00Z")), true); // 05:30 CST (06:30 under CDT)
  assert.equal(inQuietWindow(at("2026-11-01T11:59:00Z")), true); // 05:59 CST
  assert.equal(inQuietWindow(at("2026-11-01T12:00:00Z")), false); // 06:00 CST
  assert.equal(inQuietWindow(at("2026-10-31T11:30:00Z")), false); // 06:30 CDT the day before
});

test("quiet window follows the spring-forward DST edge on 2027-03-14", () => {
  assert.equal(inQuietWindow(at("2027-03-14T06:30:00Z")), false); // 00:30 CST (01:30 under CDT)
  assert.equal(inQuietWindow(at("2027-03-14T06:59:00Z")), false); // 00:59 CST
  assert.equal(inQuietWindow(at("2027-03-14T07:00:00Z")), true); // 01:00 CST
  assert.equal(inQuietWindow(at("2027-03-14T08:00:00Z")), true); // 03:00 CDT, 02:00 never happens
  assert.equal(inQuietWindow(at("2027-03-14T10:59:00Z")), true); // 05:59 CDT
  assert.equal(inQuietWindow(at("2027-03-14T11:00:00Z")), false); // 06:00 CDT
  assert.equal(inQuietWindow(at("2027-03-14T11:30:00Z")), false); // 06:30 CDT (05:30 under CST)
});

test("inside the window only the DB-free app probe runs, and skips are neither failures nor incidents", async () => {
  const h = harness();
  h.api = {}; // a broken DB-backed probe must not even be called
  h.profiles = { jsonrpc: "2.0", id: 1, error: { code: -1 } };
  await tickRange(h, at("2026-10-12T06:00:00Z"), at("2026-10-12T06:10:00Z"));
  const state = await h.storage.get(STATE_KEY);
  assert.equal(dbCalls(h).length, 0);
  assert.equal(appCalls(h).length, 10);
  assert.equal(state.failures, 0);
  assert.equal(state.incidents.length, 0);
  assert.equal(state.probes_ok, true);
  assert.equal(state.ok, true);
  assert.equal(state.quiet_window, true);
  assert.equal(state.complete, false);
  assert.deepEqual(state.checks.map(({ name, ok, skipped, reason }) => ({ name, ok, skipped, reason })), [
    { name: "api-release", ok: null, skipped: true, reason: "skipped_quiet_window" },
    { name: "app-release", ok: true, skipped: undefined, reason: "ok" },
    { name: "verb-round-trip", ok: null, skipped: true, reason: "skipped_quiet_window" },
  ]);
  assert.equal(h.calls.filter((call) => call.url.endsWith("/fail")).length, 0);
  assert.equal(h.calls.filter((call) => call.url.startsWith("https://hc-ping.com/") && !call.url.endsWith("/fail")).length, 10);
});

test("boundary minutes: 00:59 and 06:00 probe the database, 01:00 and 05:59 do not", async () => {
  for (const [stamp, db] of [["2026-10-12T05:59:00Z", 2], ["2026-10-12T06:00:00Z", 0], ["2026-10-12T10:59:00Z", 0], ["2026-10-12T11:00:00Z", 2]]) {
    const h = harness();
    await runMinute(h.storage, env, at(stamp), h.fetch);
    assert.equal(dbCalls(h).length, db, stamp);
    assert.equal(appCalls(h).length, 1, stamp);
    const sampled = await h.storage.get(STATE_KEY);
    assert.equal(sampled.ok, true, stamp);
    assert.equal(sampled.quiet_window, db === 0, stamp);
    assert.equal(sampled.complete, db !== 0, stamp);
  }
});

test("a DB-free failure inside the window still confirms an incident on its third minute", async () => {
  const h = harness();
  h.app = {};
  await tickRange(h, at("2026-10-12T07:00:00Z"), at("2026-10-12T07:03:00Z"));
  const state = await h.storage.get(STATE_KEY);
  assert.equal(state.failures, 3);
  assert.equal(state.incidents.length, 1);
  assert.equal(h.calls.filter((call) => call.url.endsWith("/fail")).length, 1);
  assert.equal(dbCalls(h).length, 0); // the loop is filed after the window, not by waking Neon
  assert.equal(state.pending_records, 1);
});

test("a failure streak spanning the window boundary is held, not reset, by skipped minutes", async () => {
  const h = harness();
  h.api = {};
  await tickRange(h, at("2026-10-12T05:58:00Z"), at("2026-10-12T06:00:00Z")); // 00:58, 00:59 fail
  assert.equal((await h.storage.get(STATE_KEY)).failures, 2);
  await tickRange(h, at("2026-10-12T06:00:00Z"), at("2026-10-12T11:00:00Z")); // whole window, app healthy
  let state = await h.storage.get(STATE_KEY);
  assert.equal(state.failures, 2);
  assert.equal(state.first_failure_at, "2026-10-12T05:58:00.000Z");
  assert.equal(state.incidents.length, 0);
  await runMinute(h.storage, env, at("2026-10-12T11:00:00Z"), h.fetch); // 06:00, still broken
  state = await h.storage.get(STATE_KEY);
  assert.equal(state.failures, 3);
  assert.equal(state.incidents.length, 1);
  assert.equal(state.incidents[0].first_failure_at, "2026-10-12T05:58:00.000Z");
  assert.equal(state.incidents[0].confirmed_at, "2026-10-12T11:00:00.000Z");
});

test("an in-window app failure cannot launder a held DB failure when the app recovers", async () => {
  const h = harness();
  h.api = {};
  await runMinute(h.storage, env, at("2026-10-12T05:59:00Z"), h.fetch); // 00:59 DB probe fails
  h.app = {};
  await runMinute(h.storage, env, at("2026-10-12T06:00:00Z"), h.fetch); // 01:00 app fails
  h.app = APP;
  await runMinute(h.storage, env, at("2026-10-12T06:01:00Z"), h.fetch); // 01:01 app passes
  const state = await h.storage.get(STATE_KEY);
  assert.equal(state.failures, 2);
  assert.deepEqual(state.failing_checks, ["api-release"]);
});

test("an app-only streak inside the window is reset when the app passes again", async () => {
  const h = harness();
  h.app = {};
  await tickRange(h, at("2026-10-12T07:00:00Z"), at("2026-10-12T07:02:00Z"));
  h.app = APP;
  await runMinute(h.storage, env, at("2026-10-12T07:02:00Z"), h.fetch);
  const state = await h.storage.get(STATE_KEY);
  assert.equal(state.failures, 0);
  assert.equal(state.first_failure_at, null);
});

test("an incident open at 01:00 recovers and files its loop only after the window", async () => {
  const h = harness();
  h.down = true;
  await tickRange(h, at("2026-10-12T05:57:00Z"), at("2026-10-12T06:00:00Z"));
  h.down = false;
  const before = h.calls.length;
  await tickRange(h, at("2026-10-12T06:00:00Z"), at("2026-10-12T11:00:00Z"));
  let state = await h.storage.get(STATE_KEY);
  assert.equal(state.active_incident, "production-" + at("2026-10-12T05:59:00Z") / 60000);
  assert.equal(state.incidents[0].recovered_at, null);
  assert.equal(dbCalls({ calls: h.calls.slice(before) }).length, 0);
  await runMinute(h.storage, env, at("2026-10-12T11:00:00Z"), h.fetch);
  state = await h.storage.get(STATE_KEY);
  assert.equal(state.active_incident, null);
  assert.equal(state.incidents[0].recovered_at, "2026-10-12T11:00:00.000Z");
  assert.equal(state.incidents[0].loop_closed, true);
  assert.equal(state.ok, true);
});
