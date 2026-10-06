// V5-F01 — the live door (recordSourceAuthorityStoreTools) and the numbered
// migration, proved offline.
//
// The door adds no judgment; what it adds is WIRING, and wiring fails in four
// ways a reader would not see: a write that skips the shared envelope, a
// store transaction that nests inside the verb's, an authority flag that
// quietly drops, and a refusal that loses its stable code on the way out. Each
// has a case below. The migration's one claim — that it is the two reviewed
// SQL sources byte for byte, in order — is re-derived here from the sources.
//
// The end-to-end proof against real PostgreSQL is
// record-source-authority-live-pg.v5.test.mjs, run by the migration class.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  V5_F01_OPERATIONS,
  V5_F01_TOOL_REGISTRATION_SOURCE,
  V5F01StoreError,
  recordSourceAuthorityStoreTools,
  v5F01StoreOperationSchemas,
  v5F01ToolInputSchema,
  v5F01ToolRefusal,
  v5F01TransactionScopedHandle,
} from "../src/record-source-authority-store.v5.js";
import { V5F01Error } from "../src/record-source-authority.v5.js";
import { V5F01DocumentSourceError } from "../src/document-derivative-registration.v5.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../..");

class ToolError extends Error {
  constructor(payload) { super(payload?.error ?? "tool_error"); this.payload = payload; }
}

const AGENT = Object.freeze({ slug: "codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe" });

function recordingEnvelope() {
  const calls = [];
  const withEnvelope = async (client, actor, verb, args, fn) => {
    calls.push({ verb, key: args?.idempotency_key });
    return fn();
  };
  return { calls, withEnvelope };
}

/** A store double whose every method records how it was reached. */
function fakeStoreFactory(behaviour = {}) {
  const seen = [];
  const createStore = ({ db }) => new Proxy({}, {
    get: (_t, method) => async (payload, context) => {
      seen.push({ method, payload, context, db });
      if (behaviour.throws) throw behaviour.throws;
      return { decision: behaviour.decision ?? "allow", reason_id: "fake",
        effects: { creates_effect: false, database_writes: 0, network_calls: 0 } };
    },
  });
  return { seen, createStore };
}

test("the door serves exactly the store's nine operations under one source name", () => {
  const { withEnvelope } = recordingEnvelope();
  const tools = recordSourceAuthorityStoreTools({ withEnvelope, ToolError });
  assert.deepEqual(Object.keys(tools).sort(), [...V5_F01_OPERATIONS].sort());
  assert.equal(V5_F01_TOOL_REGISTRATION_SOURCE, "record-source-authority");
  const tools_js = readFileSync(resolve(REPO, "mcp-server/src/tools.js"), "utf8");
  assert.match(tools_js, /registerTools\(recordSourceAuthorityStoreTools\(\{ withEnvelope, ToolError \}\),\s*"record-source-authority"\)/);
  assert.match(tools_js, /"record-source-authority": "mcp-server\/src\/record-source-authority-store\.v5\.js"/);
});

test("authority flags carry through: policy and hold are humanOnly AND authorityOnly, nothing else is", () => {
  const { withEnvelope } = recordingEnvelope();
  const tools = recordSourceAuthorityStoreTools({ withEnvelope, ToolError });
  const schemas = v5F01StoreOperationSchemas();
  for (const [name, tool] of Object.entries(tools)) {
    assert.equal(tool.write, schemas[name].write, name);
    assert.equal(tool.humanOnly === true, schemas[name].humanOnly, `${name} humanOnly`);
    assert.equal(tool.authorityOnly === true, schemas[name].authorityOnly, `${name} authorityOnly`);
    assert.ok(tool.description.length > 120, `${name} is described`);
  }
  assert.deepEqual(Object.entries(tools).filter(([, t]) => t.authorityOnly).map(([n]) => n).sort(),
    ["record-artifact-preservation-hold", "register-record-source-authority-policy"]);
});

test("every input schema is closed and matches the store's own key list", () => {
  const schemas = v5F01StoreOperationSchemas();
  for (const name of V5_F01_OPERATIONS) {
    const schema = v5F01ToolInputSchema(name);
    assert.equal(schema.additionalProperties, false, name);
    assert.deepEqual(Object.keys(schema.properties).sort(), [...schemas[name].keys].sort(), name);
    assert.deepEqual(schema.required, [...schemas[name].required], name);
    if (schemas[name].write) assert.ok(schema.required.includes("idempotency_key"), name);
  }
  assert.throws(() => v5F01ToolInputSchema("delete-everything"),
    error => error instanceof V5F01StoreError && error.code === "unknown_operation");
});

test("every write goes through the shared envelope; the read does not", async () => {
  const { calls, withEnvelope } = recordingEnvelope();
  const { seen, createStore } = fakeStoreFactory();
  const tools = recordSourceAuthorityStoreTools({ withEnvelope, ToolError, createStore });
  const client = { query: async () => ({ rows: [] }) };
  for (const name of V5_F01_OPERATIONS) {
    await tools[name].handler(client, AGENT, { idempotency_key: `k-${name}` });
  }
  const enveloped = calls.map(c => c.verb).sort();
  assert.deepEqual(enveloped, V5_F01_OPERATIONS.filter(n => n !== "read-record-source-authority").sort());
  for (const call of calls) assert.equal(call.key, `k-${call.verb}`);
  assert.equal(seen.length, V5_F01_OPERATIONS.length);
});

test("the store is handed the authenticated actor as its only context key", async () => {
  const { withEnvelope } = recordingEnvelope();
  const { seen, createStore } = fakeStoreFactory();
  const tools = recordSourceAuthorityStoreTools({ withEnvelope, ToolError, createStore });
  await tools["record-source-observation"].handler({ query: async () => ({ rows: [] }) }, AGENT,
    { idempotency_key: "k", observation: {} });
  assert.deepEqual(Object.keys(seen[0].context), ["actor"]);
  assert.deepEqual(seen[0].context.actor, { ...AGENT });
  assert.notEqual(seen[0].context.actor, AGENT, "a copy, so the store cannot mutate the dispatcher's actor");
});

test("the transaction-scoped handle never issues BEGIN or COMMIT on the verb's client", async () => {
  const statements = [];
  const client = { query: async text => { statements.push(text); return { rows: [] }; } };
  const handle = v5F01TransactionScopedHandle(client);
  const out = await handle.transaction(async inner => {
    assert.equal(inner, handle);
    await inner.query("SELECT 1");
    return "done";
  });
  assert.equal(out, "done");
  assert.deepEqual(statements, ["SELECT 1"]);
  assert.throws(() => v5F01TransactionScopedHandle(null),
    error => error.code === "database_handle_required");
});

test("a refused policy answer is reported ok:false and the write-count claim is corrected", async () => {
  const { withEnvelope } = recordingEnvelope();
  const refuse = fakeStoreFactory({ decision: "refuse" });
  const tools = recordSourceAuthorityStoreTools({ withEnvelope, ToolError, createStore: refuse.createStore });
  const answer = await tools["record-source-observation"].handler({ query: async () => ({ rows: [] }) },
    AGENT, { idempotency_key: "k", observation: {} });
  assert.equal(answer.ok, false);
  assert.equal(answer.effects.database_writes, "f01_record_layer_rows_only");
  assert.equal(answer.effects.network_calls, 0);
  const allow = fakeStoreFactory({ decision: "allow" });
  const readTools = recordSourceAuthorityStoreTools({ withEnvelope, ToolError, createStore: allow.createStore });
  const read = await readTools["read-record-source-authority"].handler({ query: async () => ({ rows: [] }) },
    AGENT, { selector: { kind: "current_policy" } });
  assert.equal(read.ok, true);
  assert.equal(read.effects.database_writes, 0);
});

test("kernel, store and document-source refusals keep their stable code through the door", async () => {
  const { withEnvelope } = recordingEnvelope();
  for (const thrown of [
    new V5F01StoreError("no_installed_policy", "no policy"),
    new V5F01Error("unknown_field", "bad", { path: "x" }),
    new V5F01DocumentSourceError("source_statement_required", "say where it came from"),
  ]) {
    const { createStore } = fakeStoreFactory({ throws: thrown });
    const tools = recordSourceAuthorityStoreTools({ withEnvelope, ToolError, createStore });
    await assert.rejects(tools["record-source-observation"].handler(
      { query: async () => ({ rows: [] }) }, AGENT, { idempotency_key: "k", observation: {} }),
    error => {
      assert.ok(error instanceof ToolError, thrown.name);
      assert.equal(error.payload.error, thrown.code);
      if (thrown.detail !== undefined) assert.deepEqual(error.payload.detail, thrown.detail);
      return true;
    });
  }
});

test("a database refusal raised by an F01 writer keeps its f01_ code; anything else is rethrown untouched", async () => {
  const pgError = Object.assign(new Error("f01_stale_document_digest: prior moved"), { code: "40001" });
  const refusal = v5F01ToolRefusal(pgError, ToolError);
  assert.equal(refusal.payload.error, "f01_stale_document_digest");
  assert.equal(refusal.payload.sqlstate, "40001");
  // A message that merely CONTAINS f01_ later on is not an F01 refusal.
  assert.equal(v5F01ToolRefusal(Object.assign(new Error("boom near f01_x"), { code: "XX000" }), ToolError), null);
  // No SQLSTATE means it did not come from the database.
  assert.equal(v5F01ToolRefusal(new Error("f01_looks_like_one"), ToolError), null);
  // An unrelated error with a code is not laundered into a refusal.
  assert.equal(v5F01ToolRefusal(Object.assign(new TypeError("x"), { code: "E1" }), ToolError), null);

  const { withEnvelope } = recordingEnvelope();
  const plain = new TypeError("genuinely broken");
  const { createStore } = fakeStoreFactory({ throws: plain });
  const tools = recordSourceAuthorityStoreTools({ withEnvelope, ToolError, createStore });
  await assert.rejects(tools["read-record-source-authority"].handler(
    { query: async () => ({ rows: [] }) }, AGENT, { selector: { kind: "current_policy" } }),
  error => error === plain);
});

test("the door refuses to build without the shared envelope and ToolError", () => {
  assert.throws(() => recordSourceAuthorityStoreTools({ ToolError }),
    error => error.code === "tool_wiring_incomplete");
  assert.throws(() => recordSourceAuthorityStoreTools({ withEnvelope: async () => null }),
    error => error.code === "tool_wiring_incomplete");
});

// ---------------------------------------------------------------------------
// The numbered migration is the two reviewed sources, byte for byte.
// ---------------------------------------------------------------------------

function f01Migration() {
  const names = readdirSync(resolve(REPO, "migrations"))
    .filter(name => /^\d{4}_f01_record_source_authority\.sql$/.test(name));
  assert.equal(names.length, 1, "exactly one numbered F01 migration exists");
  return readFileSync(resolve(REPO, "migrations", names[0]), "utf8");
}

test("the migration embeds domain.sql verbatim, then the document-source hunk verbatim but for its psql line", () => {
  const migration = f01Migration();
  const domain = readFileSync(resolve(REPO, "domain.sql"), "utf8");
  const hunk = readFileSync(resolve(REPO, "ops/document-derivative-registration.candidate.sql"), "utf8");
  const PSQL_ONLY = "\\set ON_ERROR_STOP on\n";
  assert.equal(hunk.split(PSQL_ONLY).length, 2, "the hunk carries exactly one psql-only line");
  const hunkAsMigrated = hunk.replace(PSQL_ONLY,
    "-- (psql-only ON_ERROR_STOP line removed: psycopg applies this file)\n");
  const at = migration.indexOf(domain);
  const bt = migration.indexOf(hunkAsMigrated);
  assert.ok(at > 0, "domain.sql appears byte for byte");
  assert.ok(bt > at + domain.length - 1, "the document-source hunk appears byte for byte AFTER domain.sql");
  assert.equal(migration.indexOf(domain, at + 1), -1, "domain.sql appears once");
  assert.ok(!/^\\/m.test(migration), "no psql meta-command survives into a psycopg-applied file");
});

test("the migration's own additions insert no policy and grant no DML or private helper", () => {
  const migration = f01Migration();
  const domain = readFileSync(resolve(REPO, "domain.sql"), "utf8");
  const hunk = readFileSync(resolve(REPO, "ops/document-derivative-registration.candidate.sql"), "utf8")
    .replace("\\set ON_ERROR_STOP on\n", "-- (psql-only ON_ERROR_STOP line removed: psycopg applies this file)\n");
  const own = migration.replace(domain, "").replace(hunk, "")
    .split("\n").filter(line => !/^\s*--/.test(line)).join("\n");
  assert.ok(!/\binsert\s+into\b/i.test(own), "the wrapper inserts no row");
  assert.ok(!/grant\s+(insert|update|delete|truncate)/i.test(own), "the wrapper grants no DML");
  assert.match(own, /f01_already_installed/, "a second install refuses rather than repairing");
  assert.match(own, /GRANT EXECUTE ON FUNCTION %s TO carr_authority/);
  for (const helper of ["f01_claim_idempotency", "f01_settle_idempotency",
    "f01_insert_derivative_link", "f01_insert_document_source_provenance"]) {
    assert.ok(own.includes(`'${helper}'`), `${helper} is excluded from the group grant by name`);
  }
});
