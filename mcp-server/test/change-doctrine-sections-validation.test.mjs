import assert from "node:assert/strict";
import test from "node:test";

import { doctrineTools } from "../src/doctrine.js";

class ToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.payload = payload;
  }
}

const ACTOR = { id: "11111111-1111-4111-8111-111111111111", slug: "joe" };
const DOC = {
  id: "22222222-2222-4222-8222-222222222222",
  slug: "fixture-doc",
  content_class: "sop",
  visibility: "shared",
  owner_actor_id: null,
};

function makeTools() {
  return doctrineTools({
    withEnvelope: async (_c, _actor, _verb, _args, fn) => fn(),
    writeEvent: async () => {},
    ToolError,
  });
}

function validItem(sectionKey, baseVersion) {
  return { section_key: sectionKey, body_text: `body for ${sectionKey}`, base_version: baseVersion };
}

class ChangeSetFake {
  constructor() {
    this.calls = [];
    this.revision = 0;
  }

  async query(sql, params = []) {
    this.calls.push({ sql, params });
    if (sql.includes("from doctrine_document where id::text")) return { rows: [DOC] };
    if (sql.includes("doctrine_review_policy")) return { rows: [{ max_age_days: null }] };
    if (sql.includes("insert into doctrine_change_set"))
      return { rows: [{ id: "33333333-3333-4333-8333-333333333333" }] };
    if (sql.includes("from doctrine_section where document_id=$1 and section_key=$2")) {
      const key = params[1];
      return { rows: [{ id: `${key}-id`, section_key: key, current_version: key === "a" ? 2 : 4,
        current_revision_id: `${key}-revision`, status: "active" }] };
    }
    if (sql.includes("select check_key, severity, applies_to, impl_key, config")) return { rows: [] };
    if (sql.includes("insert into doctrine_gate_run")) return { rows: [{ id: "44444444-4444-4444-8444-444444444444" }] };
    if (sql.includes("insert into doctrine_change_item")) return { rows: [] };
    if (sql.includes("insert into doctrine_revision")) {
      this.revision += 1;
      return { rows: [{ id: `revision-${this.revision}` }] };
    }
    if (sql.includes("update doctrine_section set")) return { rows: [] };
    if (sql.includes("update doctrine_change_set set")) return { rows: [] };
    if (sql.includes("update doctrine_meta set generation")) return { rows: [{ generation: 19 }] };
    if (sql.includes("insert into doctrine_snapshot")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("malformed section item is refused before any database query or write", async () => {
  const db = new ChangeSetFake();
  const handler = makeTools()["change-doctrine-sections"].handler;

  await assert.rejects(
    () => handler(db, ACTOR, {
      idempotency_key: "bad-item",
      document: DOC.slug,
      items: [{ body_text: "missing its key", base_version: 1 }],
    }),
    error => error instanceof ToolError
      && error.payload.error === "change_set_item_invalid"
      && error.payload.malformed[0].item_index === 0
      && error.payload.malformed[0].fields.includes("section_key"),
  );
  assert.equal(db.calls.length, 0, "malformed input must not reach resolveDoc or create a change set");
});

test("non-object item is a structured refusal, never a raw localeCompare internal error", async () => {
  const db = new ChangeSetFake();
  const handler = makeTools()["change-doctrine-sections"].handler;

  await assert.rejects(
    () => handler(db, ACTOR, {
      idempotency_key: "bad-object",
      document: DOC.slug,
      items: [null],
    }),
    error => error instanceof ToolError
      && error.payload.error === "change_set_item_invalid"
      && error.payload.malformed[0].fields.includes("item"),
  );
  assert.equal(db.calls.some(call => /insert into doctrine_change_set/i.test(call.sql)), false);
});

test("valid atomic batch keeps the established section_key lock order", async () => {
  const db = new ChangeSetFake();
  const handler = makeTools()["change-doctrine-sections"].handler;

  const result = await handler(db, ACTOR, {
    idempotency_key: "valid-batch",
    document: DOC.slug,
    items: [validItem("z", 4), validItem("a", 2)],
  });

  assert.equal(result.ok, true);
  assert.deepEqual(
    db.calls
      .filter(call => /from doctrine_section where document_id=\$1 and section_key=\$2/i.test(call.sql))
      .map(call => call.params[1]),
    ["a", "z"],
  );
  assert.equal(db.calls.filter(call => /insert into doctrine_revision/i.test(call.sql)).length, 2,
    "both sections still commit through the same atomic change-set path");
});
