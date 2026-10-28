// Audit 3 F7: owner=claude is the autonomous drain queue, so a narrow profile
// may not fill it, and every loop read names the surface that wrote the row.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const actor = profile => ({ id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "mcp", client_id: "claude", ...(profile ? { operational_profile: profile } : {}) });

class Recorder {
  constructor(rows = []) { this.sql = []; this.params = []; this.rows = rows; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select slug from loop_domain")) return { rows: [{ slug: "system" }] };
    if (sql.startsWith("select id, rel_path, col_order from loop_block"))
      return { rows: [{ id: "30000000-0000-0000-0000-000000000351", rel_path: "x.md", col_order: null }] };
    if (sql.startsWith("select number, kind") || sql.startsWith("select id as loop_id")) { this.sql.push(sql); return { rows: this.rows }; }
    if (sql.includes("from loop_item where kind = $1")) return { rows: [{ m: 400 }] };
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 1 }] };
    if (sql.startsWith("insert into loop_item")) { this.params = params; return { rows: [{ id: "20000000-0000-0000-0000-000000000401" }] }; }
    if (sql.startsWith("insert into event") || sql.startsWith("insert into tool_call")) return { rows: [] };
    this.sql.push(sql);
    return { rows: this.rows };
  }
}
const claudeLoop = { idempotency_key: "writer-surface-claude", kind: "open_loop", owner: "claude",
  domain: "system", body: "writer surface probe", blocker: "capability", blocker_detail: "coverage only",
  residue_kind: "provider_login", residue_host: "github.com" };

test("add-loop refuses owner=claude from every narrow profile, in any letter case", async () => {
  for (const profile of ["capture", "away", "unattended", "hermes", "hermes-cos"])
    for (const owner of ["claude", "Claude"])
      await assert.rejects(() => TOOLS["add-loop"].handler(new Recorder(), actor(profile), { ...claudeLoop, owner }),
        e => e instanceof ToolError && e.payload.error === "claude_owner_requires_full_profile" && e.payload.profile === profile);
});

test("add-loop still files owner=claude and owner=joe from a narrow profile, and owner=claude from full or local", async () => {
  for (const [profile, owner] of [["full", "claude"], [undefined, "claude"], ["capture", "joe"]]) {
    const db = new Recorder();
    const out = await TOOLS["add-loop"].handler(db, actor(profile), { ...claudeLoop, owner });
    assert.equal(out.ok, true, `${profile} ${owner}`);
    assert.equal(JSON.parse(db.params.at(-1)).writer_surface, profile ?? "local");
  }
});

test("update-loop refuses a narrow profile re-owning a loop to claude before touching the database", async () => {
  const db = { async query(text) {
    if (text.replace(/\s+/g, " ").trim().startsWith("select request_hash, response")) return { rows: [] };
    throw new Error("must refuse before any other query"); } };
  await assert.rejects(() => TOOLS["update-loop"].handler(db, actor("capture"),
    { idempotency_key: "writer-surface-update", number: "1", base_version: 1, owner: "claude" }),
    e => e.payload?.error === "claude_owner_requires_full_profile");
});

test("read-loop and loop-board select the writer surface, defaulting to unrecorded", async () => {
  const board = new Recorder([{ number: "1", writer_surface: "capture" }]);
  const full = await TOOLS["loop-board"].handler(board, actor("full"), {});
  const summary = await TOOLS["loop-board"].handler(board, actor("full"), { summary: true });
  assert.equal(full.loops?.[0]?.writer_surface ?? full.rows?.[0]?.writer_surface, "capture");
  assert.equal(summary.loops[0].writer_surface, "capture");
  const read = new Recorder([{ loop_id: "x", writer_surface: "unrecorded" }]);
  await TOOLS["read-loop"].handler(read, actor("full"), { number: "1" });
  assert.equal(board.sql.length + read.sql.length, 4);
  for (const sql of [...board.sql, ...read.sql].filter(q => !q.includes('loop_amendment_history')))
    assert.match(sql, /coalesce\(extra_cells->>'writer_surface','unrecorded'\) as writer_surface/);
});
