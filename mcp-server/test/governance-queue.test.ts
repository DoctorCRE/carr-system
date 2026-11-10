// governance-queue.test.mjs — WR-000019 slice S6, BATCH REVIEW QUEUE.
//
// governance-queue is a read-only projection across three pending-decision
// lanes (rule approvals, guidance import batches, retrieval proposals), read
// through one SECURITY DEFINER function (ops.read_governance_queue(),
// migration 0345) because carr_reader has no direct grant on `rule` or
// `retrieval_proposal`. This suite proves the verb's own shape: write:false,
// a single query against that function, and the three lanes surfaced with
// their counts — never a new write path. The needs_joe list it also carries
// is proven in needs-joe.test.mjs.

import assert from "node:assert/strict";
import { test } from "node:test";

function record(value: unknown): Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const registered = record(await import(new URL("../src/tools.js", import.meta.url).href));
const tool = record(record(registered.TOOLS)["governance-queue"]);
const schema = record(tool.inputSchema);
async function governanceQueue(client: unknown, actor: unknown, args: unknown) {
  assert.equal(typeof tool.handler, "function");
  const handler = tool.handler;
  assert.ok(typeof handler === "function");
  return record(await handler(client, actor, args));
}

const actor = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true };

test("governance-queue is read-only and takes no arguments", () => {
  assert.equal(tool.write, false);
  assert.deepEqual(schema.properties, {});
  assert.equal(schema.additionalProperties, false);
});

test("governance-queue reads its three lanes through one function call and surfaces them with counts", async () => {
  const queue = {
    pending_rule_approvals: [{ rule_id: "rule-1", statement: "always X", enforcement_status: "blocked" }],
    pending_guidance_import_batches: [{ batch_id: "batch-1", manifest_digest: "a".repeat(64), entry_count: 3 }],
    pending_retrieval_proposals: [],
  };
  const calls: { sql: string; params: unknown }[] = [];
  const client = {
    query: async (sql: string, params?: unknown) => {
      calls.push({ sql, params });
      if (/read_governance_queue/.test(sql)) return { rows: [{ queue }] };
      return { rows: [] }; // the needs_joe sources; covered by needs-joe.test.mjs
    },
  };
  const result = await governanceQueue(client, actor, {});
  assert.equal(
    calls.filter(({ sql }) => /read_governance_queue/.test(sql)).length,
    1,
    "one call, straight through the SECURITY DEFINER projection",
  );
  assert.equal(result.ok, true);
  assert.deepEqual(result.pending_rule_approvals, queue.pending_rule_approvals);
  assert.deepEqual(result.pending_guidance_import_batches, queue.pending_guidance_import_batches);
  assert.deepEqual(result.pending_retrieval_proposals, []);
  assert.deepEqual(result.pending_convention_proposals, []);
  assert.deepEqual(result.counts, {
    pending_convention_proposals: 0,
    pending_rule_approvals: 1,
    pending_guidance_import_batches: 1,
    pending_retrieval_proposals: 0,
    total: 2,
  });
});

test("governance-queue tolerates an empty queue without throwing", async () => {
  const client = {
    query: async (sql: string) => {
      if (/read_governance_queue/.test(sql)) return { rows: [{ queue: {} }] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
  const result = await governanceQueue(client, actor, {});
  assert.equal(result.ok, true);
  assert.deepEqual(result.counts, {
    pending_convention_proposals: 0,
    pending_rule_approvals: 0,
    pending_guidance_import_batches: 0,
    pending_retrieval_proposals: 0,
    total: 0,
  });
});

test("governance-queue lists team-memory convention proposals with the adopt-rule approval", async () => {
  const section = {
    section_id: "00000000-0000-4000-8000-000000000001",
    base_version: 2,
    section_key: ["conventions", "proposed", "k01"].join("/"),
    updated_at: "2026-10-28T00:00:00Z",
  };
  const queue = {
    pending_convention_proposals: [
      { ...section, body_text: 'Convention: Assess when asked.\nCard: {"id":"K01","placement":"CORE"}\nWhy: notes.' },
      {
        ...section,
        section_id: "x",
        section_key: "conventions/proposed/loose",
        body_text: "Proposed convention: free prose",
      },
      {
        ...section,
        section_id: "y",
        body_text: "Convention: Stay in the territory.\nPacks: client-deal, source-study\nSource: x",
      },
      { ...section, section_id: "z", body_text: "Convention: Stay in the territory.\nSource: x" },
    ],
  };
  const calls: string[] = [];
  const client = {
    query: async (sql: string) => {
      calls.push(sql);
      if (/read_governance_queue/.test(sql)) return { rows: [{ queue }] };
      return { rows: [] };
    },
  };
  const result = await governanceQueue(client, actor, {});
  assert.ok(Array.isArray(result.pending_convention_proposals));
  const [adoptable, malformed, packed, packless] = result.pending_convention_proposals.map(record);
  assert.ok(adoptable && malformed && packed && packless);
  assert.equal(adoptable.adoptable, true);
  assert.equal(adoptable.statement, "Assess when asked.");
  assert.equal(adoptable.card_id, "K01");
  assert.deepEqual(adoptable.approve_with, { verb: "adopt-rule", section_id: section.section_id, base_version: 2 });
  assert.equal(malformed.adoptable, false);
  assert.ok(typeof malformed.problem === "string");
  assert.match(malformed.problem, /Convention:/);
  assert.deepEqual([packed.adoptable, packed.card_id, packed.packs], [true, null, ["client-deal", "source-study"]]);
  assert.equal(packless.adoptable, false, "a convention with no card and no Packs line is not adoptable");
  assert.ok(typeof packless.problem === "string");
  assert.match(packless.problem, /Packs:/);
  assert.equal(record(result.counts).pending_convention_proposals, 4);
  assert.equal(record(result.counts).total, 4);
  assert.ok(!calls.some((sql) => /doctrine_section/.test(sql)), "the lane rides the one definer read");
});
