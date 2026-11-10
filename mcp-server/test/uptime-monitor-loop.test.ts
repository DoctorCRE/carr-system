// Tests uptime incident identity, replay, and concurrent loop creation.

import assert from "node:assert/strict";
import test from "node:test";
import type { Replay } from "./helpers/registered-tool.ts";
import { derivedIdempotencyKey, first as firstItem, parseJson, tool } from "./helpers/registered-tool.ts";

const source = "uptime-monitor:availability:v1";
const actor = (slug: string) => ({
  id: slug === "a" ? "10000000-0000-0000-0000-000000000001" : "10000000-0000-0000-0000-000000000002",
  slug,
  human: false,
  via: "local-token",
  client_id: slug,
});
const args = (key: string, source_note = source) => ({
  idempotency_key: derivedIdempotencyKey("test", key),
  kind: "open_loop",
  domain: "system",
  owner: "claude",
  title: "DoctorCRE uptime monitoring unavailable",
  body: "Restore the monitor",
  source_note,
  blocker: "other_lane",
  blocker_detail: "Orchestrator must restore the uptime monitor",
});

class Records {
  loops: { id: string; number: unknown; source_note: unknown; status: string }[] = [];
  calls = new Map<unknown, Replay>();
  async query(text: string, params: unknown[] = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.includes("pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response"))
      return { rows: this.calls.has(params[0]) ? [this.calls.get(params[0])] : [] };
    if (sql.startsWith("select slug from loop_domain")) return { rows: [{ slug: "system" }] };
    if (sql.includes("source_note=$1"))
      return { rows: this.loops.filter((row) => row.source_note === params[0] && row.status === "open") };
    if (sql.startsWith("select id, rel_path, col_order")) return { rows: [{ id: "block", rel_path: "fixture" }] };
    if (sql.includes("from loop_item where kind = $1")) return { rows: [{ m: this.loops.length }] };
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: this.loops.length }] };
    if (sql.startsWith("insert into loop_item")) {
      const row = { id: `loop-${this.loops.length + 1}`, number: params[1], source_note: params[9], status: "open" };
      this.loops.push(row);
      return { rows: [row] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], { request_hash: params[3], response: parseJson(params[4]) });
      return { rows: [] };
    }
    throw new Error(`Unexpected SQL: ${sql}`);
  }
}

test("monitor incident identity survives distinct principals without weakening replay authority", async () => {
  const db = new Records();
  const first = await tool("add-loop").handler(db, actor("a"), args("a-open"));
  await assert.rejects(
    () => tool("add-loop").handler(db, actor("b"), args("a-open")),
    (error) => error instanceof Error && error.message === "key_reuse",
  );
  const second = await tool("add-loop").handler(db, actor("b"), args("b-open"));
  assert.equal(second.loop_id, first.loop_id);
  assert.equal(second.deduplicated, true);
  assert.equal(db.loops.length, 1);
  firstItem(db.loops).status = "dropped";
  const next = await tool("add-loop").handler(db, actor("b"), args("b-next"));
  assert.notEqual(next.loop_id, first.loop_id);
  assert.equal(db.loops.length, 2);
});

test("ordinary source notes continue to allow separate loops", async () => {
  const db = new Records();
  await tool("add-loop").handler(db, actor("a"), args("a", "https://example.com"));
  await tool("add-loop").handler(db, actor("b"), args("b", "https://example.com"));
  assert.equal(db.loops.length, 2);
});

test("concurrent monitor writers return one incident under the transaction lock", async () => {
  const db = new Records();
  let writer = Promise.resolve();
  let readers = 0;
  let ready: () => void = () => {
    throw new Error("barrier not initialized");
  };
  const bothReady = new Promise<void>((resolve) => {
    ready = resolve;
  });
  async function transaction(principal: string, key: string) {
    let release: (() => void) | undefined;
    const client = {
      async query(sql: string, params: unknown[] = []) {
        if (sql.startsWith("select request_hash, response")) {
          if (++readers === 2) ready();
          await bothReady;
        }
        if (sql.includes("pg_advisory_xact_lock")) {
          const previous = writer;
          writer = new Promise<void>((resolve) => {
            release = resolve;
          });
          await previous;
          return { rows: [] };
        }
        return db.query(sql, params);
      },
    };
    try {
      return await tool("add-loop").handler(client, actor(principal), args(key));
    } finally {
      release?.();
    }
  }
  const [first, second] = await Promise.all([transaction("a", "concurrent-a"), transaction("b", "concurrent-b")]);
  assert.equal(first.loop_id, second.loop_id);
  assert.equal(db.loops.length, 1);
});
