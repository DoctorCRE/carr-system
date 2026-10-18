import test from "node:test";
import assert from "node:assert/strict";
import pg from "pg";
import { TOOLS, ToolError } from "../src/tools.js";

const actor = { id: "10000000-0000-4000-8000-000000000002", slug: "joe", human: true };
const id = "20000000-0000-4000-8000-000000000001";
const bigint = value => pg.types.getTypeParser(20, "text")(value);

function database(version = "2") {
  const mutations = [];
  return { mutations, async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.includes("from doctrine_document") && !sql.includes("max_age_days"))
      return { rows: [{ id: "doc", slug: "test-doc", content_class: "reference", visibility: "shared" }] };
    if (sql.includes("max_age_days")) return { rows: [{ max_age_days: null }] };
    if (sql.includes("from doctrine_section"))
      return { rows: [{ id, document_id: "doc", section_key: "test", current_version: bigint(version), body: { version: "opaque" } }] };
    if (sql.includes("from doctrine_revision")) return { rows: [{ version: bigint(version) }] };
    if (sql.includes("from doctrine_gate_check") || sql.startsWith("select request_hash")) return { rows: [] };
    if (sql.includes("update doctrine_meta")) return { rows: [{ generation: "3" }] };
    if (/^(insert|update|delete)/.test(sql)) mutations.push(sql);
    if (sql.includes("returning id")) return { rows: [{ id: "receipt" }] };
    return { rows: [] };
  } };
}

test("doctrine-sections returns a numeric version that writes back unchanged", async () => {
  const db = database();
  const read = await TOOLS["doctrine-sections"].handler(db, actor, { section_ids: [id] });
  assert.equal(read.sections[0].current_version, 2);
  assert.equal(read.sections[0].body.version, "opaque");
  const write = await TOOLS["write-doctrine-section"].handler(db, actor, {
    idempotency_key: "doctrine-roundtrip", document: "test-doc", section_key: "test",
    base_version: read.sections[0].current_version, body_text: "Updated synthetic text",
  });
  assert.equal(write.version, 3);
});

for (const verb of ["write-doctrine-section", "set-doctrine-refs", "retire-doctrine-section", "change-doctrine-sections"]) {
  test(`${verb} refuses the rounded BIGINT pair before mutation`, async () => {
    const db = database("9007199254740993");
    const base = { section_key: "test", body_text: "Updated", base_version: 9007199254740992 };
    await assert.rejects(() => TOOLS[verb].handler(db, actor, {
      idempotency_key: `unsafe-${verb}`, document: "test-doc", section_id: id,
      reason: "Synthetic regression", ...base, items: [base],
    }), error => error instanceof ToolError && /version_invalid/.test(error.payload.error));
    assert.equal(db.mutations.some(sql => /update doctrine_section|insert into doctrine_revision/.test(sql)), false);
  });
  test(`${verb} refuses an unsafe stored token even with safe input`, async () => {
    const db = database("9007199254740993");
    const base = { section_key: "test", body_text: "Updated", base_version: 2 };
    await assert.rejects(() => TOOLS[verb].handler(db, actor, {
      idempotency_key: `unsafe-stored-${verb}`, document: "test-doc", section_id: id,
      reason: "Synthetic regression", ...base, items: [base],
    }), error => error instanceof ToolError && error.payload.error === "doctrine_version_invalid");
    assert.equal(db.mutations.some(sql => /update doctrine_section|insert into doctrine_revision/.test(sql)), false);
  });
}

test("doctrine-sections refuses unsafe stored versions and preserves the safe maximum", async () => {
  const read = await TOOLS["doctrine-sections"].handler(database("9007199254740991"), actor, { section_ids: [id] });
  assert.equal(read.sections[0].current_version, 9007199254740991);
  await assert.rejects(() => TOOLS["doctrine-sections"].handler(database("9007199254740993"), actor, { section_ids: [id] }),
    error => error instanceof ToolError && error.payload.error === "doctrine_version_invalid");
});

test("doctrine creation accepts zero and refuses an exhausted revision", async () => {
  const args = { idempotency_key: "doctrine-zero", document: "test-doc", section_key: "test", body_text: "Updated" };
  const out = await TOOLS["write-doctrine-section"].handler(database("0"), actor, { ...args, base_version: 0 });
  assert.equal(out.version, 1);
  const db = database("9007199254740991");
  await assert.rejects(() => TOOLS["write-doctrine-section"].handler(db, actor, { ...args, base_version: 9007199254740991 }),
    error => error instanceof ToolError && error.payload.error === "doctrine_version_invalid");
  assert.equal(db.mutations.some(sql => /insert into doctrine_revision/.test(sql)), false);
});

test("doctrine conflict reports normalize the current and last-writer versions", async () => {
  await assert.rejects(() => TOOLS["set-doctrine-refs"].handler(database("2"), actor, {
    idempotency_key: "conflict", section_id: id, base_version: 1,
  }), error => {
    assert.equal(error.payload.error, "version_conflict");
    assert.equal(error.payload.current_version, 2);
    assert.equal(error.payload.last_writer.version, 2);
    return true;
  });
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
  const read = await TOOLS["read-doctrine"].handler(db, actor, { document: "test-doc" });
  assert.equal(read.sections[0].version, 2);
  assert.equal(read.sections[0].body.version, "opaque");
  assert.equal(snapshot.sections[0].version, "2");
  const rules = await TOOLS["resolve-doctrine-rules"].handler(db, actor, {});
  assert.equal(rules.operative[0].current_version, 2);
});
