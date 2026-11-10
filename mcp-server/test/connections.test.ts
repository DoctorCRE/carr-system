// Tests provider connection projections and observation refusals.

import assert from "node:assert/strict";
import test from "node:test";
import { CONNECTION_PROVIDERS, connectionsProjection } from "../src/connections.v1.ts";
import { resourceObservationTools } from "../src/resource-observation.v5.ts";
import { ToolError } from "../src/tool-error.ts";
import type { Db, WithEnvelope } from "../src/verb-contract.ts";

const withEnvelope: WithEnvelope = async (_c, _actor, _verb, _args, fn) => fn();
// SELECT-only fixtures supply the result read by the dashboard parser.
const database = (client: object): Db => client as Db;
const dashboard = {
  generated_at: "2026-10-02T12:00:00Z",
  providers: [
    {
      provider: "model_route",
      state: "ok",
      observed_at: "2026-10-02T12:00:00Z",
      charge: 99,
      estimate: 12,
      allowance: 500,
      configured_capacity: { configured: true },
    },
  ],
};
test("every named provider appears; configuration and capacity never imply connected or dollar spend", () => {
  const read = connectionsProjection(dashboard);
  assert.equal(read.schema, "doctorcre-connections.v1");
  assert.deepEqual(
    read.providers.map((p) => p.id),
    CONNECTION_PROVIDERS.map((p) => p.id),
  );
  assert.ok(read.providers.every((p) => p.status === "unknown" && p.checked_at === null && p.spend === null));
  assert.equal(read.devices.state, "unknown");
  assert.deepEqual(read.devices.items, []);
});
test("read is registered through existing resource seam and executes exactly one read query", async () => {
  const tool = resourceObservationTools({ ToolError, withEnvelope })["read-resource-dashboard"];
  assert.equal(tool.write, false);
  const calls: string[] = [];
  const value = await tool.handler(
    database({
      query: async (sql: string) => {
        calls.push(sql);
        return { rows: [{ dashboard }] };
      },
    }),
  );
  assert.equal(value.connections.ok, true);
  assert.deepEqual(value.providers, dashboard.providers);
  assert.deepEqual(calls, ["select ops.read_resource_dashboard() as dashboard"]);
});
test("malformed or unavailable observations refuse without inventing healthy states", async () => {
  const tool = resourceObservationTools({ ToolError, withEnvelope })["read-resource-dashboard"];
  for (const rows of [[], [{ dashboard: { providers: null } }]])
    await assert.rejects(() => tool.handler(database({ query: async () => ({ rows }) })));
});
