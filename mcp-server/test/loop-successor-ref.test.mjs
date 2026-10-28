// loop-successor-ref.test.mjs — close-loop's successor_loop must accept the
// form a partner actually says.
//
// THE DEFECT, hit four times live on 2026-08-21 while consolidating the five
// markdown-endgame loops into one record. close-loop describes successor_loop
// as "the open row that carries the work forward", and its own missing-field
// refusal says "name the open loop that now carries this work". Both read as an
// invitation to pass the number. But the value went straight to resolveLoop as
// loop_id — the uuid-only branch — so "#213" landed in a where clause on a uuid
// column. Postgres raised invalid input syntax, and the caller got a bare
// `internal error` that named nothing: not the field, not the expected form,
// not even which of the two rows was the problem.
//
// That is the shape rule 3a9dbafd forbids (never make a partner decode an id)
// and the shape rule c53beeaa warns about from the other side: an error that
// teaches the caller nothing means the next session hits it too and cannot tell
// a server fault from its own bad argument. Recorded as defect 3eb1ad6d.
//
// Run with: node --test mcp-server/test/loop-successor-ref.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { derivedIdempotencyKey } from "../src/derived-key.ts";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude" };

const CLOSING = {
  id: "aaaaaaaa-0000-0000-0000-000000000223",
  kind: "open_loop", number: "223", status: "open",
  marker: "none", due_on: null, close_outcome: null,
  section: "backlog", rel_path: "00_Context/open-loops-backlog.md",
};
const SUCCESSOR = {
  id: "bbbbbbbb-0000-0000-0000-000000000213",
  kind: "open_loop", number: "213", status: "open",
  marker: "decision", due_on: null, close_outcome: null,
  section: "backlog", rel_path: "00_Context/open-loops-backlog.md",
};

const OUTCOME = "Superseded by #213, not abandoned — carried forward verbatim as "
  + "Milestone 2 of the single markdown-endgame record.";

// Routes the two resolveLoop branches apart so a test can prove WHICH one ran,
// and reproduces the real failure: a non-uuid reaching the uuid branch raises,
// exactly as Postgres does on a uuid column.
class Fake {
  constructor() { this.resolvedBy = []; this.writes = []; }
  async query(text, params) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select version from loop_item")) return { rows: [{ version: 2 }] };
    if (sql.startsWith("select li.id, li.kind, li.number")) {
      if (sql.includes("where li.id = $1")) {
        const id = params[0];
        this.resolvedBy.push({ branch: "loop_id", value: id });
        if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
          throw new Error(`invalid input syntax for type uuid: "${id}"`);
        }
        return { rows: [id === SUCCESSOR.id ? SUCCESSOR : CLOSING] };
      }
      if (sql.includes("where li.number = $1")) {
        const num = params[0];
        this.resolvedBy.push({ branch: "number", value: num });
        return { rows: [num === SUCCESSOR.number ? SUCCESSOR : CLOSING] };
      }
    }
    this.writes.push({ sql, params });
    if (sql.startsWith("update loop_item")) return { rows: [{ ...CLOSING, version: 3 }] };
    if (sql.startsWith("insert into")) return { rows: [{ id: "event-1" }] };
    return { rows: [] };
  }
}

async function closeWith(successor_loop, fake = new Fake()) {
  const args = {
    idempotency_key: derivedIdempotencyKey("test", `k-${successor_loop}`), loop_id: CLOSING.id, kind: "open_loop",
    base_version: 2, resolution: "dropped", outcome: OUTCOME, successor_loop,
  };
  try { return { fake, result: await TOOLS["close-loop"].handler(fake, joe, args), error: null }; }
  catch (e) { return { fake, result: null, error: e instanceof ToolError ? (e.payload ?? e.detail ?? e) : e }; }
}

test("a bare number resolves the successor, the way a partner would say it", async () => {
  const { fake, error } = await closeWith("213");
  assert.equal(error, null, "'213' is the documented form and must not raise");
  assert.ok(fake.resolvedBy.some(r => r.branch === "number" && r.value === "213"),
    "the successor must be looked up by number, not pushed through the uuid branch");
});

test("a leading hash is accepted too — '#213' is what the hint asks for", async () => {
  const { fake, error } = await closeWith("#213");
  assert.equal(error, null, "the hash is how the number appears in every render");
  assert.ok(fake.resolvedBy.some(r => r.branch === "number" && r.value === "213"),
    "the hash must be stripped before the lookup, not passed through");
});

test("a loop_id still resolves, so existing callers keep working", async () => {
  const { fake, error } = await closeWith(SUCCESSOR.id);
  assert.equal(error, null, "the uuid form was never wrong, only insufficient");
  assert.ok(fake.resolvedBy.some(r => r.branch === "loop_id" && r.value === SUCCESSOR.id),
    "a uuid must take the id branch rather than being treated as a number");
});

test("no successor reference reaches the uuid branch unless it is a uuid", async () => {
  // The regression itself: before the fix, every one of these took the uuid
  // branch and died in the driver rather than in a named refusal.
  for (const ref of ["213", "#213", " 213 "]) {
    const { fake } = await closeWith(ref);
    const badUuidLookups = fake.resolvedBy.filter(
      r => r.branch === "loop_id"
        && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(r.value));
    assert.deepEqual(badUuidLookups, [],
      `"${ref}" must never be looked up as a uuid — that is the internal error`);
  }
});
