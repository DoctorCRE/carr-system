// loop-header-prose.test.mjs — coverage for edit-loop-header and loop-headers,
// the two verbs that close loop #294 (2026-08-13).
//
// THE GAP THEY CLOSE. The standing paragraph at the top of each loops render
// (open-loops.md, its backlog file, team-loops.md, action-required.md, the idea
// bank) is DATA, not code: migration 0024 put it in loop_block.prose_md on
// purpose, so a partner's own doctrine paragraph stays his to change rather
// than being a code edit. But no verb ever wrote that column, and none read it
// either — so when open-loops.md's header went stale, pointing closed rows at a
// file that had been a FROZEN ARCHIVE since 2026-07-31, there was no way to
// correct it short of a raw table write. It cost something real: on 2026-08-09
// a session closed 40+ loops, read the stale pointer, looked in the retired
// archive, found nothing, and told Joe the closed-loop history was broken. It
// was not. 152 outcomes were exactly where they belonged.
//
// What these tests pin down, defect-first:
//   1. the version guard actually bites (this prose is doctrine; a concurrent
//      writer must not be silently overwritten),
//   2. a BLANK header is refused — blanking is a deletion of the only
//      explanation a reader of that render ever gets, and two of these blocks
//      hold a single short line, where a blank is indistinguishable from a
//      column that was never populated,
//   3. a no-op edit is refused rather than writing an event that records no
//      change,
//   4. the OLD text is carried in the event in full, so an edit to a partner's
//      paragraph is always reversible,
//   5. an ambiguous file substring returns its candidates instead of picking a
//      render, the same discipline every other loop verb follows on numbers.
//
// Run with: node --test mcp-server/test/loop-header-prose.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { derivedIdempotencyKey } from "../src/derived-key.ts";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  hot: "572ab3d6-a9b1-4ef5-b0a1-808b88b61208",   // the real open-loops.md hot block
  backlog: "fd4a8e53-87f2-428a-aac3-283335535e0a",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "mcp", client_id: "claude" };

const STALE = "Resolved rows -> `00_Context/open-loops-closed.md` (never delete).";
const FIXED = "Closed rows leave this render; read them through `v_export_loops_closed`.";

class HeaderFake {
  constructor({ version = 2, blocks } = {}) {
    this.blocks = blocks || [
      { id: ids.hot, rel_path: "00_Context/open-loops.md", kind: "open_loop",
        block_key: "hot", version, prose_md: STALE },
    ];
    this.events = [];
    this.calls = new Map();
    this.updates = [];
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.startsWith("select id, rel_path, kind, block_key, version") && sql.includes("where id=$1"))
      return { rows: this.blocks.filter(b => b.id === params[0]).map(b => ({ ...b })) };
    if (sql.startsWith("select id, rel_path, kind, block_key, version") && sql.includes("rel_path ilike $1")) {
      const needle = String(params[0]).replace(/%/g, "");
      return { rows: this.blocks
        .filter(b => b.rel_path.includes(needle) && b.block_key === params[1])
        .map(b => ({ ...b })) };
    }
    if (sql.startsWith("select id as block_id, rel_path as file")) {
      return { rows: this.blocks.map(b => ({ block_id: b.id, file: b.rel_path, kind: b.kind,
        section: b.block_key, seq: 1, version: b.version, prose_md: b.prose_md })) };
    }
    if (sql.startsWith("select version from loop_block where id=$1 for update")) {
      const b = this.blocks.find(x => x.id === params[0]);
      return { rows: b ? [{ version: b.version }] : [] };
    }
    if (sql.startsWith("select created_at from loop_block where id=$1"))
      return { rows: [{ created_at: "2026-08-01T00:00:00.000Z" }] };
    if (sql.includes("from event e join actor a on a.id=e.actor_id"))
      return { rows: [] };
    if (sql.startsWith("update loop_block set prose_md=$1")) {
      const b = this.blocks.find(x => x.id === params[2]);
      b.prose_md = params[0];
      b.version += 1;                     // the loop_block_touch trigger, in production
      this.updates.push({ id: params[2], prose_md: params[0], updated_by: params[1] });
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({ verb: params[2], subject_type: params[3], subject_id: params[4],
        old_value: params[6] ? JSON.parse(params[6]) : null,
        new_value: params[7] ? JSON.parse(params[7]) : null });
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

const call = (db, args) => TOOLS["edit-loop-header"].handler(db, joe, args);

test("edit-loop-header: the stale pointer is replaced, and the OLD text survives in the event", async () => {
  const db = new HeaderFake({ version: 2 });
  const res = await call(db, { idempotency_key: "00000571-0000-4000-8000-000000000173", block_id: ids.hot,
    base_version: 2, prose_md: FIXED });

  assert.equal(res.ok, true);
  assert.equal(res.block_id, ids.hot);
  assert.equal(res.file, "00_Context/open-loops.md");
  assert.equal(res.section, "hot");
  assert.equal(db.blocks[0].prose_md, FIXED);

  // Reversibility is the whole reason old_value is populated here: this is a
  // paragraph a partner wrote, and an edit with no way back is not a correction.
  const ev = db.events.at(-1);
  assert.equal(ev.verb, "edit-loop-header");
  assert.equal(ev.subject_type, "loop_block");
  assert.equal(ev.subject_id, ids.hot);
  assert.equal(ev.old_value.prose_md, STALE);
  assert.equal(ev.new_value.prose_md, FIXED);
});

test("edit-loop-header: resolves by file + section when no block_id is given", async () => {
  const db = new HeaderFake({ version: 2 });
  const res = await call(db, { idempotency_key: "00000571-0000-4000-8000-000000000174", file: "open-loops.md",
    section: "hot", base_version: 2, prose_md: FIXED });
  assert.equal(res.block_id, ids.hot);
  assert.equal(db.blocks[0].prose_md, FIXED);
});

test("edit-loop-header: a file substring matching two renders returns candidates, never a guess", async () => {
  // 'open-loops' matches both open-loops.md and open-loops-backlog.md. Picking
  // one would edit the wrong file's doctrine and look like it worked.
  const db = new HeaderFake({ blocks: [
    { id: ids.hot, rel_path: "00_Context/open-loops.md", kind: "open_loop",
      block_key: "hot", version: 2, prose_md: STALE },
    { id: ids.backlog, rel_path: "00_Context/open-loops-backlog.md", kind: "open_loop",
      block_key: "hot", version: 1, prose_md: "backlog prose" },
  ] });
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000175", file: "open-loops", section: "hot",
      base_version: 2, prose_md: FIXED }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "ambiguous_file");
      assert.equal(err.payload.candidates.length, 2);
      return true;
    });
  assert.equal(db.updates.length, 0);
});

test("edit-loop-header: a BLANK header is refused — blanking is a deletion, not an edit", async () => {
  for (const blank of ["", "   ", "\n\n"]) {
    const db = new HeaderFake({ version: 2 });
    await assert.rejects(
      () => call(db, { idempotency_key: derivedIdempotencyKey("test", `hdr-blank-${blank.length}`), block_id: ids.hot,
        base_version: 2, prose_md: blank }),
      (err) => {
        assert.ok(err instanceof ToolError);
        assert.equal(err.payload.error, "empty_prose");
        return true;
      });
    assert.equal(db.blocks[0].prose_md, STALE, "the stored prose must be untouched");
    assert.equal(db.updates.length, 0);
  }
});

test("edit-loop-header: a byte-identical rewrite is refused rather than logging a change that did not happen", async () => {
  const db = new HeaderFake({ version: 2 });
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000176", block_id: ids.hot,
      base_version: 2, prose_md: STALE }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "nothing_to_update");
      return true;
    });
  assert.equal(db.events.length, 0);
});

test("edit-loop-header: a stale base_version refuses — a concurrent writer is never silently overwritten", async () => {
  const db = new HeaderFake({ version: 4 });
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000177", block_id: ids.hot,
      base_version: 2, prose_md: FIXED }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "version_conflict");
      return true;
    });
  assert.equal(db.blocks[0].prose_md, STALE);
});

test("edit-loop-header: a missing base_version refuses, so nobody edits doctrine without reading it", async () => {
  const db = new HeaderFake({ version: 2 });
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000178", block_id: ids.hot, prose_md: FIXED }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "missing_base_version");
      return true;
    });
  assert.equal(db.blocks[0].prose_md, STALE);
});

test("edit-loop-header: neither block_id nor file+section is a refusal, not a broad match", async () => {
  const db = new HeaderFake({ version: 2 });
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000179", file: "open-loops.md", prose_md: FIXED }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "need_block_id_or_file_and_section");
      return true;
    });
});

test("edit-loop-header: an unknown block_id is not_found rather than a silent no-op", async () => {
  const db = new HeaderFake({ version: 2 });
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000180",
      block_id: "00000000-0000-0000-0000-00000000dead", base_version: 1, prose_md: FIXED }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "not_found");
      return true;
    });
});

test("loop-headers: returns every block with the version an edit needs, so no write verb is used as a probe", async () => {
  const db = new HeaderFake({ version: 2 });
  const res = await TOOLS["loop-headers"].handler(db, joe, {});
  assert.equal(res.count, 1);
  assert.equal(res.blocks[0].block_id, ids.hot);
  assert.equal(res.blocks[0].version, 2);
  assert.equal(res.blocks[0].prose_md, STALE);
  assert.equal(TOOLS["loop-headers"].write, false, "the read side must never be a write verb");
});
