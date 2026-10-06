import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool } from "../src/tools.js";

const AGENT = { id: "10000000-0000-0000-0000-000000000021", slug: "codex", human: false, via: "test" };

async function rejected(fn) {
  try { await fn(); assert.fail("expected refusal"); }
  catch (e) { assert.ok(e instanceof ToolError, `expected ToolError, got ${e}`); return e.payload; }
}

// Mirrors ops.record_resource_observation (migration 0580): idempotent
// insert, key reuse with different content refuses, only local_compute and
// model_route are collectible (neon/github/cloudflare wait on C03-C05), and
// observed_at may not be more than 5 minutes in the future. Mirrors
// ops.read_resource_dashboard's staleness re-check: an observation older
// than STALE_AFTER_MS reads back state='stale' with an age-bearing reason,
// no matter what state was originally recorded.
const STALE_AFTER_MS = 15 * 60 * 1000;
const FUTURE_SKEW_MS = 5 * 60 * 1000;

class ResourceObservationFake {
  constructor(now = () => new Date("2026-09-24T12:00:00Z")) {
    this.calls = []; this.toolCalls = new Map(); this.receipts = new Map(); this.rows = [];
    this.now = now;
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });
    if (sql.startsWith("select request_hash, response")) {
      const row = this.toolCalls.get(params[0]);
      return { rows: row ? [row] : [] };
    }
    if (sql.startsWith("select ops.read_resource_dashboard")) {
      const parseJsonField = (value) => (typeof value === "string" ? JSON.parse(value) : value);
      const nowMs = this.now().getTime();
      const providers = ["neon", "github", "cloudflare", "local_compute", "model_route"].map((provider) => {
        const latest = [...this.rows].reverse().find((r) => r.provider === provider);
        if (latest) {
          const ageMs = nowMs - new Date(latest.observed_at).getTime();
          const stale = ageMs > STALE_AFTER_MS;
          return {
            ...latest,
            policy: parseJsonField(latest.policy),
            measured_capacity: parseJsonField(latest.measured_capacity),
            configured_capacity: parseJsonField(latest.configured_capacity),
            model_route: parseJsonField(latest.model_route),
            state: stale ? "stale" : latest.state,
            reason: stale
              ? `observation is ${Math.round(ageMs / 60000)} minute(s) old, past the 15 minute freshness threshold`
              : latest.reason,
          };
        }
        return {
          provider, account: null, project: null, product: null, period: null, as_of: null,
          quantity: null, quantity_unit: null, allowance: null, policy: null, estimate: null, charge: null,
          measured_capacity: null, configured_capacity: null, model_route: null,
          state: ["neon", "github", "cloudflare"].includes(provider) ? "unconfigured" : "collector_absent",
          reason: ["neon", "github", "cloudflare"].includes(provider)
            ? "no collector configured for this provider yet (V5-UX-C03/C04/C05 not built)"
            : "no collector observation received yet",
          source: null, observed_at: null,
        };
      });
      return { rows: [{ dashboard: { schema: "doctorcre-resource-dashboard.v1", generated_at: "2026-09-24T00:00:00Z", providers } }] };
    }
    if (sql.includes("ops.record_resource_observation")) {
      const [provider, account, project, product, period, asOf, quantity, quantityUnit, allowance,
        policy, estimate, charge, measuredCapacity, configuredCapacity, modelRoute, state, reason,
        source, observedAt, idempotencyKey] = params;
      const existing = this.receipts.get(idempotencyKey);
      const digest = JSON.stringify(params.slice(0, 19));
      if (existing) {
        if (existing.digest !== digest) throw new Error("resource_observation_key_reuse");
        return { rows: [{ id: existing.id, provider: existing.provider, state: existing.state,
          reason: existing.reason, observed_at: existing.observed_at, replayed: true }] };
      }
      if (!["neon", "github", "cloudflare", "local_compute", "model_route"].includes(provider))
        throw new Error("resource_observation_provider_invalid");
      // C03-C05 are not built: only the two local, credential-less providers
      // have a real collector today. Refusing the other three keeps a
      // carr_writer agent from fabricating a healthy external reading.
      if (!["local_compute", "model_route"].includes(provider))
        throw new Error("resource_observation_provider_not_yet_collectible");
      if (!["ok", "partial", "stale", "unconfigured", "collector_absent", "host_offline"].includes(state))
        throw new Error("resource_observation_state_invalid");
      if (new Date(observedAt).getTime() > this.now().getTime() + FUTURE_SKEW_MS)
        throw new Error("resource_observation_observed_at_in_future");
      const id = `30000000-0000-0000-0000-${String(this.rows.length + 1).padStart(12, "0")}`;
      const row = { id, provider, account, project, product, period, as_of: asOf, quantity, quantity_unit: quantityUnit,
        allowance, policy, estimate, charge, measured_capacity: measuredCapacity, configured_capacity: configuredCapacity,
        model_route: modelRoute, state, reason, source, observed_at: observedAt };
      this.rows.push(row);
      this.receipts.set(idempotencyKey, { id, provider, state, reason, observed_at: observedAt, digest });
      return { rows: [{ id, provider, state, reason, observed_at: observedAt, replayed: false }] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    throw new Error(`ResourceObservationFake: unhandled query: ${sql}`);
  }
}

test("read-resource-dashboard always returns all five providers, none fabricated", async () => {
  const client = new ResourceObservationFake();
  const result = await executeRegisteredTool(client, AGENT, "read-resource-dashboard", {});
  assert.equal(result.ok, true);
  assert.equal(result.providers.length, 5);
  const byProvider = Object.fromEntries(result.providers.map((p) => [p.provider, p]));
  for (const provider of ["neon", "github", "cloudflare"]) {
    assert.equal(byProvider[provider].state, "unconfigured");
    assert.match(byProvider[provider].reason, /C03\/C04\/C05 not built/);
    assert.equal(byProvider[provider].quantity, null);
  }
  for (const provider of ["local_compute", "model_route"]) {
    assert.equal(byProvider[provider].state, "collector_absent");
    assert.match(byProvider[provider].reason, /no collector observation received yet/);
  }
});

test("record-resource-observation writes a local_compute row with measured and configured capacity kept separate", async () => {
  const client = new ResourceObservationFake();
  const args = {
    idempotency_key: "40000000-0000-0000-0000-000000000001",
    provider: "local_compute",
    measured_capacity: { cpu_cores: 24, memory_available_gb: 100.2 },
    configured_capacity: { host: "mac-studio", cpu_cores: 24, memory_gb: 192 },
    state: "ok",
    source: "tools/resource-collector.py",
    observed_at: "2026-09-24T12:00:00Z",
  };
  const result = await executeRegisteredTool(client, AGENT, "record-resource-observation", args);
  assert.equal(result.ok, true);
  assert.equal(result.provider, "local_compute");
  assert.equal(result.state, "ok");
  assert.equal(result.replayed, undefined);
  const dashboard = await executeRegisteredTool(client, AGENT, "read-resource-dashboard", {});
  const localCompute = dashboard.providers.find((p) => p.provider === "local_compute");
  assert.deepEqual(localCompute.measured_capacity, args.measured_capacity);
  assert.deepEqual(localCompute.configured_capacity, args.configured_capacity);
  assert.notDeepEqual(localCompute.measured_capacity, localCompute.configured_capacity);
});

test("record-resource-observation replays on the same idempotency_key without a second row", async () => {
  const client = new ResourceObservationFake();
  const args = {
    idempotency_key: "40000000-0000-0000-0000-000000000002",
    provider: "model_route",
    model_route: { launchd_label: "local.ds4-flash-next", reachable: true },
    state: "ok",
    source: "tools/resource-collector.py",
    observed_at: "2026-09-24T12:05:00Z",
  };
  const first = await executeRegisteredTool(client, AGENT, "record-resource-observation", args);
  const second = await executeRegisteredTool(client, AGENT, "record-resource-observation", args);
  assert.equal(first.replayed, undefined);
  assert.equal(second.replayed, true);
  assert.equal(second.id, first.id);
  assert.equal(client.rows.length, 1);
  // The withEnvelope-level replay never reaches ops.record_resource_observation
  // a second time -- only one insert-shaped call should have been made.
  assert.equal(client.calls.filter((c) => c.sql.includes("ops.record_resource_observation")).length, 1);
});

test("record-resource-observation refuses an unknown provider before it reaches the database", async () => {
  const client = new ResourceObservationFake();
  const payload = await rejected(() => executeRegisteredTool(client, AGENT, "record-resource-observation", {
    idempotency_key: "40000000-0000-0000-0000-000000000003",
    provider: "aws",
    state: "ok", source: "manual", observed_at: "2026-09-24T12:00:00Z",
  }));
  assert.equal(payload.error, "value_not_in_declared_vocabulary");
});

test("record-resource-observation host_offline observation carries a reason, never a fabricated success", async () => {
  const client = new ResourceObservationFake();
  const args = {
    idempotency_key: "40000000-0000-0000-0000-000000000004",
    provider: "model_route",
    model_route: { launchd_label: "local.ds4-flash-next", reachable: false },
    state: "host_offline",
    reason: "local.ds4-flash-next did not answer GET /v1/models within 2.0s",
    source: "tools/resource-collector.py",
    observed_at: "2026-09-24T11:59:00Z",
  };
  const result = await executeRegisteredTool(client, AGENT, "record-resource-observation", args);
  assert.equal(result.state, "host_offline");
  assert.match(result.reason, /did not answer/);
});

// C03-C05 are not built: neon, github and cloudflare pass JSON-schema
// validation (they are members of the canonical provider list) but the write
// door itself refuses them, so no carr_writer agent can fabricate a healthy
// external-provider reading the read door would then serve as fact. This is
// a plain thrown Error, not a handler-issued ToolError, mirroring the real
// door's `raise exception` -- tools.js's choke point only rewrites class-23
// constraint violations and passes everything else through unchanged.
const NOT_YET_COLLECTIBLE_PROVIDERS = ["neon", "github", "cloudflare"];
for (const [index, provider] of NOT_YET_COLLECTIBLE_PROVIDERS.entries()) {
  test(`record-resource-observation refuses ${provider}: C03-C05 have no collector yet`, async () => {
    const client = new ResourceObservationFake();
    await assert.rejects(
      () => executeRegisteredTool(client, AGENT, "record-resource-observation", {
        idempotency_key: `40000000-0000-0000-0000-00000000001${index}`,
        provider, state: "ok", source: "manual", observed_at: "2026-09-24T12:00:00Z",
      }),
      /resource_observation_provider_not_yet_collectible/,
    );
    // And the read door still shows it unconfigured -- the refused write
    // left no trace for this test's own fake to leak either.
    const dashboard = await executeRegisteredTool(client, AGENT, "read-resource-dashboard", {});
    assert.equal(dashboard.providers.find((p) => p.provider === provider).state, "unconfigured");
  });
}

test("read-resource-dashboard reports a dead collector as stale, not ok forever", async () => {
  const client = new ResourceObservationFake(() => new Date("2026-09-24T12:00:00Z"));
  await executeRegisteredTool(client, AGENT, "record-resource-observation", {
    idempotency_key: "40000000-0000-0000-0000-000000000005",
    provider: "model_route",
    model_route: { launchd_label: "local.ds4-flash-next", reachable: true },
    state: "ok",
    source: "tools/resource-collector.py",
    // 20 minutes before the fake's fixed "now" -- past the 15 minute
    // freshness threshold this contract documents.
    observed_at: "2026-09-24T11:40:00Z",
  });
  const dashboard = await executeRegisteredTool(client, AGENT, "read-resource-dashboard", {});
  const modelRoute = dashboard.providers.find((p) => p.provider === "model_route");
  assert.equal(modelRoute.state, "stale");
  assert.match(modelRoute.reason, /20 minute\(s\) old/);
});

test("read-resource-dashboard keeps a fresh observation as its own state, not stale", async () => {
  const client = new ResourceObservationFake(() => new Date("2026-09-24T12:00:00Z"));
  await executeRegisteredTool(client, AGENT, "record-resource-observation", {
    idempotency_key: "40000000-0000-0000-0000-000000000006",
    provider: "local_compute",
    measured_capacity: { cpu_cores: 24 },
    configured_capacity: { cpu_cores: 24 },
    state: "ok",
    source: "tools/resource-collector.py",
    // 5 minutes before "now" -- comfortably under the 15 minute threshold.
    observed_at: "2026-09-24T11:55:00Z",
  });
  const dashboard = await executeRegisteredTool(client, AGENT, "read-resource-dashboard", {});
  assert.equal(dashboard.providers.find((p) => p.provider === "local_compute").state, "ok");
});

test("record-resource-observation refuses an observed_at more than 5 minutes in the future", async () => {
  const client = new ResourceObservationFake(() => new Date("2026-09-24T12:00:00Z"));
  await assert.rejects(
    () => executeRegisteredTool(client, AGENT, "record-resource-observation", {
      idempotency_key: "40000000-0000-0000-0000-000000000007",
      provider: "local_compute",
      state: "ok",
      source: "tools/resource-collector.py",
      // One hour past the fake's fixed "now": a fabricated future timestamp
      // must not buy this row permanent front-of-queue ordering.
      observed_at: "2026-09-24T13:00:00Z",
    }),
    /resource_observation_observed_at_in_future/,
  );
});

test("record-resource-observation accepts a local_compute observation reporting partial sensor coverage", async () => {
  const client = new ResourceObservationFake();
  const args = {
    idempotency_key: "40000000-0000-0000-0000-000000000008",
    provider: "local_compute",
    // cpu_cores read; memory_* did not (psutil absent from this run).
    measured_capacity: { cpu_cores: 24 },
    configured_capacity: { host: "mac-studio", cpu_cores: 24, memory_gb: 192 },
    state: "partial",
    reason: "host sensors partially unavailable to this run: missing memory_total_gb, memory_available_gb",
    source: "tools/resource-collector.py",
    observed_at: "2026-09-24T12:00:00Z",
  };
  const result = await executeRegisteredTool(client, AGENT, "record-resource-observation", args);
  assert.equal(result.state, "partial");
  assert.match(result.reason, /memory_total_gb/);
});
