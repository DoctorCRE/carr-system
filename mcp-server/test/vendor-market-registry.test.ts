import assert from "node:assert/strict";
import test from "node:test";
import type { Db } from "../src/verb-contract.ts";

const registryPath = "../src/tools.js";
const registry: unknown = await import(registryPath);
assert.ok(typeof registry === "object" && registry !== null && "TOOLS" in registry);
const tools = registry.TOOLS;
assert.ok(typeof tools === "object" && tools !== null && "read-vendor-markets-v1" in tools);
const verb = tools["read-vendor-markets-v1"];
assert.ok(typeof verb === "object" && verb !== null && "handler" in verb && typeof verb.handler === "function");
const read = verb.handler;
const unused: Db = {
  query: async () => {
    throw new Error("database must not be touched");
  },
};
test("vendor market read refuses unauthenticated and machine callers before SQL", async () => {
  for (const actor of [{ id: "anonymous" }, { id: "machine", slug: "joe", human: false }])
    await assert.rejects(read(unused, actor, {}), /partner_required/);
});
test("vendor market read refuses invalid selectors before SQL", async () => {
  await assert.rejects(
    read(unused, { id: "joe", slug: "joe", human: true }, { market: "  " }),
    /invalid_vendor_market_arguments/,
  );
});
