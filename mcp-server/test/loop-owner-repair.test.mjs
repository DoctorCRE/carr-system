import { readMigration } from "./migration-source.testhelper.mjs";
import assert from "node:assert/strict";

import test from "node:test";
import { TOOLS } from "../src/tools.js";

const migrationUrl = new URL("../../migrations/0096_loop_owner_legacy_repair.sql", import.meta.url);
const legacyOwnerMap = new Map([
  ["claude/joe", "joint"],
  ["dell's brain→joe", "joe"],
  ["dell→joe", "joe"],
  ["joe + dell", "joint"],
  ["joe's brain", "joe"],
  ["joe's brain (cowork)", "joe"],
  ["joe/claude", "joint"],
  ["joe→dell", "dell"],
  ["✅ done 2026-07-29 (joe's go) — dell's brain→joe", "joe"],
]);

test("0096 maps every reviewed historical owner, refuses unknown values, then validates 0089", async () => {
  const sql = await readMigration(migrationUrl);
  const guard = sql.slice(sql.indexOf("and owner not in ("), sql.indexOf(")\n    ) unknown;"));
  for (const [oldOwner, newOwner] of legacyOwnerMap) {
    const sqlOwner = oldOwner.replaceAll("'", "''");
    assert.ok(guard.includes(`'${sqlOwner}'`),
      `the pre-update guard must permit the reviewed source spelling ${oldOwner}`);
    assert.ok(sql.includes(`('${sqlOwner}', '${newOwner}')`),
      `the reviewed map must include ${oldOwner} → ${newOwner}`);
  }
  assert.match(sql, /raise exception '0096: unmapped noncanonical loop owner\(s\): %'/);
  assert.ok(sql.indexOf("raise exception '0096:") < sql.indexOf("update loop_item li"),
    "the unknown-owner guard must run before the repair update");
  assert.ok(sql.indexOf("update loop_item li") < sql.indexOf("validate constraint loop_item_owner_known"),
    "the existing constraint is validated only after all reviewed repairs");
  assert.doesNotMatch(sql, /drop constraint|add constraint/i,
    "0096 must validate 0089's closed-set constraint, never relax or replace it");
});

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  loop: "20000000-0000-0000-0000-000000000017",
  doneBlock: "30000000-0000-0000-0000-000000000017",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "dealroom-cookie", client_id: "dealroom-pwa" };

class CloseLoopFake {
  constructor() {
    // This represents A17 after 0096 has made its historical joe→dell value
    // accountable as dell. The fake enforces 0089 on every UPDATE, as Postgres
    // does, so the close path proves it remains usable after the data repair.
    this.loop = { id: ids.loop, number: "A17", kind: "action_required", status: "open",
      owner: "dell", source_owner: "joe→dell", version: 4, close_outcome: null };
    this.events = [];
    this.calls = new Map();
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.includes("from loop_item li join loop_block lb"))
      return { rows: [{ ...this.loop, marker: null, due_on: null, section: "Open" }] };
    if (sql.startsWith("select version from loop_item where id=$1 for update"))
      return { rows: params[0] === this.loop.id ? [{ version: this.loop.version }] : [] };
    if (sql.startsWith("select id, rel_path, block_key from loop_block"))
      return { rows: [{ id: ids.doneBlock, rel_path: "DNA/Team/action-required.md", block_key: "done" }] };
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 0 }] };
    if (sql.startsWith("update loop_item set")) {
      assert.ok(["joe", "dell", "claude", "joint"].includes(this.loop.owner),
        "the close UPDATE would be rejected by loop_item_owner_known for a legacy owner");
      assert.equal(params.at(-1), this.loop.id);
      this.loop.status = params[0];
      this.loop.close_outcome = params[1];
      this.loop.version += 1;
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({ actor_id: params[1], verb: params[2], subject_id: params[4] });
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

test("close-loop completes a formerly directional action-required row after 0096 canonicalizes its owner", async () => {
  const db = new CloseLoopFake();
  const result = await TOOLS["close-loop"].handler(db, joe, {
    idempotency_key: "close-formerly-directional-a17",
    loop_id: ids.loop,
    base_version: 4,
    outcome: "Historical joe→dell owner canonicalized to Dell; close now succeeds.",
  });
  assert.deepEqual(result, {
    ok: true, loop_id: ids.loop, number: "A17", status: "done",
    moved_to_done_table_in: "DNA/Team/action-required.md", closed_rows_render_in: "done",
  });
  assert.equal(db.loop.owner, "dell");
  assert.equal(db.loop.source_owner, "joe→dell");
  assert.equal(db.loop.status, "done");
  assert.equal(db.events.length, 1);
});
