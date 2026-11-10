// renewal-lease-ledger.test.ts — authenticated first-party renewal authority.

import assert from "node:assert/strict";
import test from "node:test";
import { ToolError } from "../src/tool-error.ts";
import type { VerbInputs } from "../src/verb-contract.ts";

// Keep the legacy registry at its runtime seam until its separate conversion.
const registryPath = "../src/tools.js";
const registry: unknown = await import(registryPath);
function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const legacy = object(registry);
const invoke = legacy.executeRegisteredTool;
assert.ok(typeof invoke === "function");
async function executeRegisteredTool(client: object, actor: object, verb: keyof VerbInputs, args: object) {
  assert.ok(typeof invoke === "function");
  const result: unknown = await invoke(client, actor, verb, args);
  return object(result);
}
const tools = object(legacy.TOOLS);
const TOOLS = {
  "record-executed-lease": object(tools["record-executed-lease"]),
};

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  dell: "10000000-0000-0000-0000-000000000003",
  deal: "60000000-0000-0000-0000-000000000002",
  client: "60000000-0000-0000-0000-000000000001",
  oldLease: "70000000-0000-0000-0000-000000000001",
  newLease: "70000000-0000-0000-0000-000000000002",
};
const JOE = { id: ids.joe, slug: "joe", display: "Joe", human: true, via: "oauth-google" };
const DELL = { id: ids.dell, slug: "dell", display: "Dell", human: true, via: "oauth-google" };

type CurrentLease = { id: string; version: number; expiration_on?: string };
class LeaseClient {
  owner: string;
  session: string;
  current: CurrentLease | null;
  queries: { sql: string; params: unknown[] }[];
  constructor({
    owner = "joe",
    session = "joe",
    current = null,
  }: { owner?: string; session?: string; current?: CurrentLease | null } = {}) {
    this.owner = owner;
    this.session = session;
    this.current = current;
    this.queries = [];
  }
  async query(text: string, params: unknown[] = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.queries.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.includes("from ops.record_executed_lease")) {
      if (this.owner !== this.session) throw new Error("lease authority does not own the current deal");
      if (this.current && params[1] !== this.current.version)
        throw new Error(`lease version conflict: expected ${this.current.version}`);
      return {
        rows: [
          {
            lease_id: ids.newLease,
            version: 1,
            superseded_lease_id: this.current?.id || null,
            deal_id: ids.deal,
            client_id: ids.client,
          },
        ],
      };
    }
    if (sql.startsWith("insert into event") || sql.startsWith("insert into tool_call")) return { rows: [] };
    throw new Error(`unexpected SQL: ${sql}`);
  }
}

const args = {
  idempotency_key: "44444444-4444-4444-4444-444444444444",
  deal: "Example Lease",
  executed_on: "2026-08-01",
  commencement_on: "2026-09-01",
  expiration_on: "2031-08-31",
  term_months: 60,
  evidence_kind: "executed_lease",
  evidence_ref: "lease-abstract:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  source: "CARR-held executed lease abstract",
};

test("record-executed-lease creates one authenticated current lease fact", async () => {
  const c = new LeaseClient();
  const result = await executeRegisteredTool(c, JOE, "record-executed-lease", args);
  assert.deepEqual(result, { ok: true, lease_id: ids.newLease, version: 1, superseded_lease_id: null });
  const insert = c.queries.find(({ sql }) => sql.startsWith("insert into lease"));
  assert.equal(insert, undefined, "the authority connection never receives direct table writes");
  const call = c.queries.find(({ sql }) => sql.includes("ops.record_executed_lease"));
  assert.ok(call);
  assert.equal(call.params[0], "Example Lease");
  assert.equal(call.params[4], "2031-08-31");
  assert.equal(call.params[6], "executed_lease");
});

test("record-executed-lease replaces the current fact only with its fresh base version", async () => {
  const c = new LeaseClient({ current: { id: ids.oldLease, version: 3, expiration_on: "2030-01-01" } });
  const result = await executeRegisteredTool(c, JOE, "record-executed-lease", { ...args, base_version: 3 });
  assert.equal(result.superseded_lease_id, ids.oldLease);
  await assert.rejects(
    executeRegisteredTool(
      new LeaseClient({ current: { id: ids.oldLease, version: 4 } }),
      JOE,
      "record-executed-lease",
      { ...args, base_version: 3, idempotency_key: "55555555-5555-5555-5555-555555555555" },
    ),
    (error) => error instanceof ToolError && error.payload.error === "version_conflict",
  );
});

test("record-executed-lease is sponsor-fenced and rejects weak evidence or incoherent dates", async () => {
  await assert.rejects(
    executeRegisteredTool(new LeaseClient({ owner: "dell", session: "joe" }), JOE, "record-executed-lease", args),
    (error) => error instanceof ToolError && error.payload.error === "not_deal_owner",
  );
  for (const bad of [
    { evidence_kind: "web_research" },
    { evidence_ref: "" },
    { expiration_on: "2026-08-01", commencement_on: "2026-09-01" },
  ]) {
    await assert.rejects(
      executeRegisteredTool(new LeaseClient(), JOE, "record-executed-lease", {
        ...args,
        ...bad,
        idempotency_key: crypto.randomUUID(),
      }),
      (error) => error instanceof ToolError,
    );
  }
  await assert.rejects(
    executeRegisteredTool(new LeaseClient({ owner: "joe", session: "dell" }), DELL, "record-executed-lease", args),
    (error) => error instanceof ToolError && error.payload.error === "not_deal_owner",
  );
});

test("record-executed-lease is authority-only and serializes same-key replay before mutation", async () => {
  assert.equal(TOOLS["record-executed-lease"].authorityOnly, true);
  const c = new LeaseClient();
  await executeRegisteredTool(c, JOE, "record-executed-lease", args);
  const lock = c.queries.findIndex(({ sql }) => sql.startsWith("select pg_advisory_xact_lock"));
  const replay = c.queries.findIndex(({ sql }) => sql.startsWith("select request_hash, response"));
  const mutation = c.queries.findIndex(({ sql }) => sql.includes("ops.record_executed_lease"));
  assert.ok(lock >= 0 && lock < replay && replay < mutation);
});
