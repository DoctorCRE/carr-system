// Test safe doctrine version handling across reads and writes.

import assert from "node:assert/strict";
import test from "node:test";
import pg from "pg";
import { ToolError } from "../src/tool-error.ts";

function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const legacy = object(await import(new URL("../src/tools.js", import.meta.url).href));
const TOOLS = object(legacy.TOOLS);
function tool(name: string) {
  const entry = object(TOOLS[name]);
  return {
    inputSchema: object(entry.inputSchema),
    async handler(db: object, actor: object, args: object) {
      const handler = entry.handler;
      assert.ok(typeof handler === "function");
      return object(await handler(db, actor, args));
    },
  };
}

import { publicError } from "../src/public-errors.ts";

const keyModule = object(await import(new URL("../src/derived-key.ts", import.meta.url).href));
const derive = keyModule.derivedIdempotencyKey;
assert.ok(typeof derive === "function");
function derivedIdempotencyKey(namespace: string, value: string): string {
  assert.ok(typeof derive === "function");
  const result: unknown = derive(namespace, value);
  assert.ok(typeof result === "string");
  return result;
}

function first(value: unknown): unknown {
  assert.ok(Array.isArray(value) && value.length > 0);
  return value[0];
}
const actor = { id: "10000000-0000-4000-8000-000000000002", slug: "joe", human: true };
const id = "20000000-0000-4000-8000-000000000001";
const bigint = (value: string) => pg.types.getTypeParser(20, "text")(value);

function database(version = "2") {
  const mutations: string[] = [];
  return {
    mutations,
    async query(text: string, _params: unknown[] = []): Promise<{ rows: Record<string, unknown>[] }> {
      const sql = text.replace(/\s+/g, " ").trim();
      if (sql.includes("from doctrine_document") && !sql.includes("max_age_days"))
        return { rows: [{ id: "doc", slug: "test-doc", content_class: "reference", visibility: "shared" }] };
      if (sql.includes("max_age_days")) return { rows: [{ max_age_days: null }] };
      if (sql.includes("from doctrine_section"))
        return {
          rows: [
            {
              id,
              document_id: "doc",
              section_key: "test",
              current_version: bigint(version),
              body: { version: "opaque" },
            },
          ],
        };
      if (sql.includes("from doctrine_revision")) return { rows: [{ version: bigint(version) }] };
      if (sql.includes("from doctrine_gate_check") || sql.startsWith("select request_hash")) return { rows: [] };
      if (sql.includes("update doctrine_meta")) return { rows: [{ generation: "3" }] };
      if (/^(insert|update|delete)/.test(sql)) mutations.push(sql);
      if (sql.includes("returning id")) return { rows: [{ id: "receipt" }] };
      return { rows: [] };
    },
  };
}

test("doctrine-sections returns a numeric version that writes back unchanged", async () => {
  const db = database();
  const read = await tool("doctrine-sections").handler(db, actor, { section_ids: [id] });
  assert.equal(object(first(read.sections)).current_version, 2);
  assert.equal(object(object(first(read.sections)).body).version, "opaque");
  const write = await tool("write-doctrine-section").handler(db, actor, {
    idempotency_key: "00000571-0000-4000-8000-000000000119",
    document: "test-doc",
    section_key: "test",
    base_version: object(first(read.sections)).current_version,
    body_text: "Updated synthetic text",
  });
  assert.equal(write.version, 3);
});

for (const verb of [
  "write-doctrine-section",
  "set-doctrine-refs",
  "retire-doctrine-section",
  "change-doctrine-sections",
]) {
  test(`${verb} refuses the rounded BIGINT pair before mutation`, async () => {
    const db = database("9007199254740993");
    const base = { section_key: "test", body_text: "Updated", base_version: 9007199254740992 };
    await assert.rejects(
      () =>
        tool(verb).handler(db, actor, {
          idempotency_key: derivedIdempotencyKey("test", `unsafe-${verb}`),
          document: "test-doc",
          section_id: id,
          reason: "Synthetic regression",
          ...base,
          items: [base],
        }),
      (error) => error instanceof ToolError && /version_invalid/.test(error.payload.error),
    );
    assert.equal(
      db.mutations.some((sql) => /update doctrine_section|insert into doctrine_revision/.test(sql)),
      false,
    );
  });
  test(`${verb} refuses an unsafe stored token even with safe input`, async () => {
    const db = database("9007199254740993");
    const base = { section_key: "test", body_text: "Updated", base_version: 2 };
    await assert.rejects(
      () =>
        tool(verb).handler(db, actor, {
          idempotency_key: derivedIdempotencyKey("test", `unsafe-stored-${verb}`),
          document: "test-doc",
          section_id: id,
          reason: "Synthetic regression",
          ...base,
          items: [base],
        }),
      (error) => error instanceof ToolError && error.payload.error === "doctrine_version_invalid",
    );
    assert.equal(
      db.mutations.some((sql) => /update doctrine_section|insert into doctrine_revision/.test(sql)),
      false,
    );
  });
}

test("doctrine-sections refuses unsafe stored versions and preserves the safe maximum", async () => {
  const read = await tool("doctrine-sections").handler(database("9007199254740991"), actor, { section_ids: [id] });
  assert.equal(object(first(read.sections)).current_version, 9007199254740991);
  await assert.rejects(
    () => tool("doctrine-sections").handler(database("9007199254740993"), actor, { section_ids: [id] }),
    (error) => error instanceof ToolError && error.payload.error === "doctrine_version_invalid",
  );
});

test("doctrine creation accepts zero and refuses an exhausted revision", async () => {
  const args = {
    idempotency_key: "00000571-0000-4000-8000-000000000120",
    document: "test-doc",
    section_key: "test",
    body_text: "Updated",
  };
  const out = await tool("write-doctrine-section").handler(database("0"), actor, { ...args, base_version: 0 });
  assert.equal(out.version, 1);
  const db = database("9007199254740991");
  await assert.rejects(
    () => tool("write-doctrine-section").handler(db, actor, { ...args, base_version: 9007199254740991 }),
    (error) => error instanceof ToolError && error.payload.error === "doctrine_version_invalid",
  );
  assert.equal(
    db.mutations.some((sql) => /insert into doctrine_revision/.test(sql)),
    false,
  );
});

test("doctrine conflict reports normalize the current and last-writer versions", async () => {
  await assert.rejects(
    () =>
      tool("set-doctrine-refs").handler(database("2"), actor, {
        idempotency_key: "00000571-0000-4000-8000-000000000121",
        section_id: id,
        base_version: 1,
      }),
    (error) => {
      assert.ok(error instanceof ToolError);
      assert.equal(error.payload.error, "version_conflict");
      assert.equal(error.payload.current_version, 2);
      assert.equal(object(error.payload.last_writer).version, 2);
      return true;
    },
  );
});

test("snapshot and rule resolution normalize only declared section version fields", async () => {
  const snapshot = { document: { slug: "test-doc" }, sections: [{ id, version: "2", body: { version: "opaque" } }] };
  const db = database();
  const query = db.query.bind(db);
  db.query = async (sql, params) => {
    if (sql.includes("from doctrine_snapshot")) return { rows: [{ generation: "3", snapshot_json: snapshot }] };
    if (sql.includes("select generation")) return { rows: [{ generation: "3" }] };
    return query(sql, params);
  };
  const read = await tool("read-doctrine").handler(db, actor, { document: "test-doc" });
  assert.equal(object(first(read.sections)).version, 2);
  assert.equal(object(object(first(read.sections)).body).version, "opaque");
  assert.equal(object(first(snapshot.sections)).version, "2");
  const rules = await tool("resolve-doctrine-rules").handler(db, actor, {});
  assert.equal(object(first(rules.operative)).current_version, 2);
});

test("read-doctrine names an unsafe version in a synthetic refinement-plan snapshot", async () => {
  const db = database();
  const query = db.query.bind(db);
  db.query = async (sql, params) => {
    if (sql.includes("from doctrine_snapshot"))
      return {
        rows: [
          {
            generation: "3",
            snapshot_json: {
              document: {
                id: "synthetic-plan",
                slug: "synthetic-refinement-plan",
                title: "Synthetic refinement plan",
                content_class: "reference",
                visibility: "shared",
              },
              sections: [
                {
                  section_id: id,
                  section_key: "plan",
                  title: "Synthetic plan",
                  ordinal: 0,
                  version: "9007199254740993",
                  status: "active",
                  body: { text: "Synthetic plan body" },
                },
              ],
              edges_out: [],
            },
          },
        ],
      };
    if (sql.includes("select generation")) return { rows: [{ generation: "3" }] };
    return query(sql, params);
  };

  let failure: unknown;
  try {
    await tool("read-doctrine").handler(db, actor, { document: "synthetic-refinement-plan" });
  } catch (error) {
    failure = error;
  }
  assert.ok(failure instanceof ToolError);
  const payload = publicError(failure, {
    env: { CORRELATION_ID: "d170d000-0000-4000-8000-000000000031" },
    inputSchema: tool("read-doctrine").inputSchema,
  });
  assert.equal(payload.error, "doctrine_version_invalid");
  assert.match(payload.hint, /version.*safely/i);
  assert.equal(payload.correlation_id, "d170d000-0000-4000-8000-000000000031");
});

test("read-doctrine names a missing document and explains how to find a current reference", async (t) => {
  t.mock.method(console, "error", () => {});
  const db = {
    async query() {
      return { rows: [] };
    },
  };
  await assert.rejects(
    () =>
      tool("read-doctrine").handler(db, actor, {
        document: "synthetic-missing-refinement-plan",
      }),
    (failure) => {
      assert.ok(failure instanceof ToolError);
      const payload = publicError(failure, {
        env: { CORRELATION_ID: "d170d000-0000-4000-8000-000000000031" },
        inputSchema: tool("read-doctrine").inputSchema,
      });
      assert.equal(payload.error, "doctrine_doc_not_found");
      assert.match(payload.hint, /document/);
      assert.match(payload.hint, /doctrine-index/);
      assert.equal(payload.correlation_id, "d170d000-0000-4000-8000-000000000031");
      assert.deepEqual(Object.keys(payload).sort(), ["correlation_id", "error", "hint"]);
      return true;
    },
  );
});

test("read-doctrine keeps current slugs and aliases readable", async () => {
  const doc = {
    id,
    slug: "synthetic-current-plan",
    title: "Synthetic plan",
    content_class: "reference",
    visibility: "shared",
  };
  const db = {
    async query(sql: string, params: unknown[] = []) {
      if (
        (sql.includes("from doctrine_document") && params[0] === doc.slug) ||
        (sql.includes("from doctrine_slug_alias") && params[0] === "synthetic-old-plan")
      )
        return { rows: [doc] };
      if (sql.includes("from doctrine_meta")) return { rows: [{ generation: "3" }] };
      return { rows: [] };
    },
  };
  for (const document of [doc.slug, "synthetic-old-plan"]) {
    const read = await tool("read-doctrine").handler(db, actor, { document });
    assert.equal(read.ok, true);
    assert.deepEqual(read.document, doc);
    assert.deepEqual(read.sections, []);
  }
});
