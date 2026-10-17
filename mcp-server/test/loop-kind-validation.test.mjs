// loop-kind-validation.test.mjs — coverage for the add-loop `kind` defect
// found live 2026-08-14:
//
//   A session filing a cross-session work claim ("Worktree lifecycle build
//   in flight") got {"error":"no_block","section":"open","hint":"the loop
//   importer has not run for this kind — nothing to render into"} — but the
//   loop importer HAD run, for every kind, on 2026-08-01, and every
//   (kind, block_key) pair add-loop can ask for existed in loop_block.
//
//   Root cause: kind is documented in inputSchema as a required enum, but
//   inputSchema is advisory only (mcp.js's callTool passes
//   `rpc.params?.arguments` straight to the handler — same transport gap as
//   decision 7026246b's marker defect), and the handler never checked kind.
//   A call that OMITTED kind fell through the placement ternary to section
//   "open" (undefined is neither "idea" nor "open_loop"), the loop_block
//   lookup ran with kind=NULL, matched nothing, and no_block fired. The
//   thrown payload's `kind: args.kind` was undefined, which JSON
//   serialization silently drops — so the error hid the one field that
//   named the real mistake and blamed the importer instead.
//
// Run with: node --test mcp-server/test/loop-kind-validation.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude" };

// Validation must refuse before any query beyond the idempotency-replay
// lookup — a fake that throws on everything else proves it.
class RefuseBeforeDbFake {
  async query(text) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response"))
      return { rows: [] };
    throw new Error(`unhandled fake query (kind validation should refuse before this): ${sql}`);
  }
}

test("add-loop: THE BUG — an omitted kind is refused as missing_kind, never as a no_block blaming the importer", async () => {
  const db = new RefuseBeforeDbFake();
  await assert.rejects(
    () => TOOLS["add-loop"].handler(db, joe, {
      idempotency_key: "add-loop-missing-kind-self-test",
      // the live failing shape: title present, kind absent
      title: "Worktree lifecycle build in flight",
      owner: "claude",
    }),
    (err) => {
      assert.ok(err instanceof ToolError, "must be a structured ToolError");
      assert.equal(err.payload.error, "missing_kind");
      assert.notEqual(err.payload.error, "no_block");
      assert.deepEqual(err.payload.allowed,
        ["open_loop", "team_loop", "action_required", "idea"]);
      // the hint must not send the reader chasing the importer
      assert.ok(!String(err.payload.hint).includes("importer"));
      return true;
    });
});

test("add-loop: a misspelled kind is refused by name with the allowed list", async () => {
  const db = new RefuseBeforeDbFake();
  await assert.rejects(
    () => TOOLS["add-loop"].handler(db, joe, {
      idempotency_key: "add-loop-unknown-kind-self-test",
      kind: "work_claim", title: "not a real kind", owner: "claude",
    }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "unknown_kind");
      assert.equal(err.payload.got, "work_claim");
      assert.deepEqual(err.payload.allowed,
        ["open_loop", "team_loop", "action_required", "idea"]);
      return true;
    });
});

// Happy-path fake (same shape as loop-version-and-marker.test.mjs's
// AddLoopHappyFake) so a legal kind is proven to still reach ok:true —
// the validation must not over-refuse.
class AddLoopHappyFake {
  constructor() {
    this.blockId = "30000000-0000-0000-0000-000000000351";
    this.events = [];
    this.calls = new Map();
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.startsWith("select slug from loop_domain where slug=$1"))
      return { rows: params[0] === "system" ? [{ slug: "system" }] : [] };
    if (sql.startsWith("select id, rel_path, col_order from loop_block"))
      return params[0] === "open_loop" && params[1] === "backlog"
        ? { rows: [{ id: this.blockId, rel_path: "00_Context/open-loops-backlog.md", col_order: null }] }
        : { rows: [] };
    if (sql.includes("from loop_item where kind = $1")) return { rows: [{ m: 400 }] }; // nextLoopNumber
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 1 }] };
    if (sql.startsWith("insert into loop_item"))
      return { rows: [{ id: "20000000-0000-0000-0000-000000000401" }] };
    if (sql.startsWith("insert into event")) {
      this.events.push({ verb: params[2] }); return { rows: [] };
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

test("add-loop: a legal kind still reaches a genuine ok:true (the validation does not over-refuse)", async () => {
  const db = new AddLoopHappyFake();
  const result = await TOOLS["add-loop"].handler(db, joe, {
    idempotency_key: "add-loop-legal-kind-happy-path",
    kind: "open_loop", owner: "claude", domain: "system",
    body: "kind-validation self-test, close immediately",
    blocker: "capability", blocker_detail: "coverage only, not a real block",
    residue_kind: "provider_login", residue_host: "github.com",
  });
  assert.equal(result.ok, true);
  assert.equal(result.kind, "open_loop");
  assert.equal(result.section, "backlog");
  assert.equal(db.events.length, 1);
  assert.equal(db.events[0].verb, "add-loop");
});
