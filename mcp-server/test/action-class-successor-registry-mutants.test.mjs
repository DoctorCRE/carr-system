// action-class-successor-registry-mutants.test.mjs — planted-bug mutants for
// the two JS-side guards action-class-successor-registry.test.mjs's own
// fake-client assertions cannot, by construction, prove are load-bearing:
// that the duplicate-registration refusal actually stops a second insert, and
// that the action_class format check actually runs before any write. Same
// technique as amend-closed-loop-mutants.test.mjs: mutate a COPY of the
// source, load the copy, and assert the same probe that passes on the real
// source FAILS against the mutant.
//
// Run with: node --test mcp-server/test/action-class-successor-registry-mutants.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const FILE = "action-class-successor-registry.v5.js";
const WORK = mkdtempSync(join(tmpdir(), "action-class-successor-registry-mutants-"));
test.after(() => rmSync(WORK, { recursive: true, force: true }));

let serial = 0;

function mutate(anchor, replacement) {
  const source = readFileSync(join(SRC, FILE), "utf8");
  const count = source.split(anchor).length - 1;
  assert.equal(count, 1, `mutant anchor must occur exactly once in ${FILE}: ${JSON.stringify(anchor)}`);
  return source.replace(anchor, replacement);
}

async function loadModule(source) {
  serial += 1;
  const path = join(WORK, `${serial}-module.mjs`);
  writeFileSync(path, source);
  return import(pathToFileURL(path).href);
}

async function loadReal() {
  return import(pathToFileURL(join(SRC, FILE)).href);
}

async function loadMutant(anchor, replacement) {
  return loadModule(mutate(anchor, replacement));
}

class ToolError extends Error {
  constructor(payload) { super(payload?.error || "tool_error"); this.payload = payload; }
}
const withEnvelope = async (_c, _actor, _verb, _args, fn) => fn();
const writeEvent = async () => {};

const GOOD = {
  idempotency_key: "11111111-1111-1111-1111-111111111111",
  action_class: "salesforce_unattended_write",
  title: "Unattended Salesforce field writes",
  goal: "Let a future, activated automation write qualifying Salesforce fields without a human in the loop.",
  owner: "joe",
  activation_predicate: { requires: ["independent conformance receipt"] },
};

function fakeClient({ existing = false } = {}) {
  const writes = [];
  return {
    writes,
    async query(text, params) {
      const sql = text.replace(/\s+/g, " ").trim();
      if (sql.startsWith("select 1 from action_class_successor"))
        return { rows: existing ? [{ "?column?": 1 }] : [] };
      writes.push({ sql, params });
      if (sql.startsWith("insert into action_class_successor"))
        return { rows: [{ id: "new-row-1", status: "inactive", created_at: new Date() }] };
      return { rows: [] };
    },
  };
}

async function register(mod, args, client) {
  const tools = mod.actionClassSuccessorRegistryTools({ withEnvelope, writeEvent, ToolError });
  const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe" };
  try { return { result: await tools["register-action-class-successor"].handler(client, joe, args), error: null }; }
  catch (e) { return { result: null, error: e instanceof ToolError ? e.payload : e }; }
}

test("real source: duplicate action_class is refused and nothing is inserted", async () => {
  const mod = await loadReal();
  const client = fakeClient({ existing: true });
  const { error } = await register(mod, { ...GOOD }, client);
  assert.equal(error?.error, "action_class_already_registered");
  assert.equal(client.writes.length, 0);
});

test("MUTANT: removing the duplicate-registration guard lets a second insert through", async () => {
  const mod = await loadMutant(
    `        const existing = await c.query(
          "select 1 from action_class_successor where action_class=$1", [actionClass]);
        if (existing.rows.length)
          refuse(ToolError, "action_class_already_registered", { action_class: actionClass,
            hint: "this door appends new classes only; it does not edit an existing entry" });
`,
    "",
  );
  const client = fakeClient({ existing: true });
  const { error } = await register(mod, { ...GOOD }, client);
  assert.notEqual(error?.error, "action_class_already_registered",
    "the mutant must NOT refuse a duplicate -- proving the real guard above is what refuses it");
  assert.ok(client.writes.some((w) => w.sql.startsWith("insert into action_class_successor")),
    "the mutant inserts a second row for an already-registered action_class");
});

test("real source: a malformed action_class is refused before any write", async () => {
  const mod = await loadReal();
  const client = fakeClient();
  const { error } = await register(mod, { ...GOOD, action_class: "Not Valid!" }, client);
  assert.equal(error?.error, "action_class_invalid");
  assert.equal(client.writes.length, 0);
});

test("MUTANT: a permissive action_class pattern lets a malformed slug through to the insert", async () => {
  const mod = await loadMutant(
    "const ACTION_CLASS = /^[a-z][a-z0-9_]{2,63}$/;",
    "const ACTION_CLASS = /.*/;",
  );
  const client = fakeClient();
  const { error } = await register(mod, { ...GOOD, action_class: "Not Valid!" }, client);
  assert.notEqual(error?.error, "action_class_invalid",
    "the mutant's wide-open pattern must NOT refuse malformed input -- proving the real regex above is the guard");
  assert.ok(client.writes.some((w) => w.sql.startsWith("insert into action_class_successor")));
});

test("real source: an activation_predicate that is not a plain object is refused before any write", async () => {
  const mod = await loadReal();
  const client = fakeClient();
  const { error } = await register(mod, { ...GOOD, activation_predicate: "not an object" }, client);
  assert.equal(error?.error, "invalid_shape");
  assert.equal(client.writes.length, 0);
});

test("MUTANT: removing the activation_predicate shape check lets a string predicate through", async () => {
  const mod = await loadMutant(
    '        assertPlainObject(ToolError, args.activation_predicate, "activation_predicate");\n',
    "",
  );
  const client = fakeClient();
  const { error } = await register(mod, { ...GOOD, activation_predicate: "not an object" }, client);
  assert.notEqual(error?.error, "invalid_shape",
    "the mutant must NOT refuse a non-object predicate -- proving the real check above is the guard");
  assert.ok(client.writes.some((w) => w.sql.startsWith("insert into action_class_successor")));
});
