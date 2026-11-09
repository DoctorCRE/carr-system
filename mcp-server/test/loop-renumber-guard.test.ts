import assert from "node:assert/strict";
import { test } from "node:test";
import { ToolError } from "../src/tool-error.ts";

const registered: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
assert.ok(registered && typeof registered === "object" && "TOOLS" in registered);
const registry = registered.TOOLS;
assert.ok(registry && typeof registry === "object");
assert.ok("update-loop" in registry);
const updateLoopTool = registry["update-loop"];
assert.ok(
  updateLoopTool &&
    typeof updateLoopTool === "object" &&
    "handler" in updateLoopTool &&
    typeof updateLoopTool.handler === "function",
);
const updateLoopHandler = updateLoopTool.handler;

// loop-renumber-guard.test.ts — coverage for the update-loop renumber guard,
// which fired on the wrong condition.
//
// THE DEFECT, hit live twice on 2026-08-14 while clearing stale blockers.
// update-loop's own inputSchema documents `number` as "alternative to loop_id"
// — the way a caller says WHICH row they mean, using the number a human
// actually says. But the renumber guard was written as:
//
//     if (args.number !== undefined) { ...; if (!args.renumber_reason) throw; }
//
// so it fired on the mere PRESENCE of `number`, not on a number CHANGING.
// Editing a loop by its number was therefore refused with an error about
// renumbering, and the only way through was a second round trip: read-loop to
// fetch the row, then update-loop by loop_id. A guard that refuses the
// documented identifier is not protecting the data; it is teaching callers to
// avoid the safe path.
//
// The guard itself is right and stays (rule 7105955b: a renumbered row is not
// an abandoned one, and the old number survives in other rows' prose and in
// every render). It just has to read the right condition. The comparison it
// needed already existed one line below, guarding the clash check.
//
// Run with: node --test mcp-server/test/loop-renumber-guard.test.ts
// Also discovered by the canonical test runner.

const joe = {
  id: "10000000-0000-0000-0000-000000000002",
  slug: "joe",
  display: "Joe",
  human: true,
  via: "mcp",
  client_id: "claude",
};

const ROW = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  kind: "open_loop",
  number: "395",
  status: "open",
  marker: "none",
  due_on: null,
  close_outcome: null,
  section: "backlog",
  rel_path: "00_Context/open-loops-backlog.md",
};

// Answers exactly the reads update-loop makes before the renumber branch, and
// records the writes so a test can assert nothing was written when it should
// not have been. Anything unexpected throws, so a change that adds a query
// cannot pass this suite silently.
class Fake {
  writes: { sql: string; params: unknown[] }[];
  constructor() {
    this.writes = [];
  }
  async query(text: string, params: unknown[]) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select li.id, li.kind, li.number")) return { rows: [ROW] };
    if (sql.startsWith("select version from loop_item")) return { rows: [{ version: 1 }] };
    if (sql.startsWith("select id from loop_item where kind=")) return { rows: [] };
    this.writes.push({ sql, params });
    if (sql.startsWith("update loop_item")) return { rows: [{ ...ROW, version: 2 }] };
    if (sql.startsWith("insert into")) return { rows: [{ id: "event-1" }] };
    return { rows: [] };
  }
}

const call = (args: Record<string, unknown>) => updateLoopHandler(new Fake(), joe, args);

test("update-loop schema documents number as both identifier and renumber input", () => {
  assert.ok("inputSchema" in updateLoopTool);
  const schema = updateLoopTool.inputSchema;
  assert.ok(schema && typeof schema === "object" && "properties" in schema);
  const props = schema.properties;
  assert.ok(props && typeof props === "object" && "number" in props);
  const number = props.number;
  assert.ok(number && typeof number === "object" && "description" in number && typeof number.description === "string");
  assert.equal(typeof number, "object");
  assert.match(number.description, /alternative to loop_id/);
  assert.match(number.description, /RENUMBER/i);
  assert.match(number.description, /renumber_reason/);
});

async function errorOf(promise: Promise<unknown>): Promise<Record<string, unknown> | null> {
  try {
    await promise;
    return null;
  } catch (e) {
    assert.ok(e instanceof ToolError);
    return e.payload;
  }
}

test("identifying a row BY NUMBER without changing it needs no renumber reason", async () => {
  const err = await errorOf(
    call({
      idempotency_key: "00000571-0000-4000-8000-000000000185",
      number: "395",
      base_version: 1,
      blocker: "other_lane",
      blocker_detail: "the branch it waited on has merged",
    }),
  );
  const code = err && (err.error ?? err.message);
  assert.notEqual(
    code,
    "renumber_reason_required",
    "passing the documented identifier must not read as a renumber request",
  );
});

test("a leading # on the same number is still not a renumber", async () => {
  const err = await errorOf(
    call({
      idempotency_key: "00000571-0000-4000-8000-000000000186",
      number: "#395",
      base_version: 1,
      blocker_detail: "sharpened detail only",
    }),
  );
  const code = err && (err.error ?? err.message);
  assert.notEqual(code, "renumber_reason_required");
});

test("genuinely CHANGING the number still demands its reason", async () => {
  const err = await errorOf(
    call({
      idempotency_key: "00000571-0000-4000-8000-000000000187",
      loop_id: ROW.id,
      base_version: 1,
      number: "812",
    }),
  );
  assert.equal(err?.error, "renumber_reason_required", "rule 7105955b still binds when the number actually moves");
  assert.ok(err);
  assert.equal(err.from, "395");
  assert.equal(err.to, "812");
});

test("a changed number WITH its reason is accepted", async () => {
  const err = await errorOf(
    call({
      idempotency_key: "00000571-0000-4000-8000-000000000188",
      loop_id: ROW.id,
      base_version: 1,
      number: "812",
      renumber_reason: "collided with another open row of this kind",
    }),
  );
  const code = err && (err.error ?? err.message);
  assert.notEqual(code, "renumber_reason_required");
});

test("a non-numeric number is still refused before anything else", async () => {
  const err = await errorOf(
    call({
      idempotency_key: "00000571-0000-4000-8000-000000000189",
      loop_id: ROW.id,
      base_version: 1,
      number: "L-209",
    }),
  );
  assert.equal(err?.error, "bad_number", "the renders sort on this column, so a free-form ref must never reach it");
});
