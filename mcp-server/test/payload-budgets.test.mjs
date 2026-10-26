// payload-budgets.test.mjs — summary/names_only flags on the three heaviest
// read verbs must cut the payload, and absent flags must return today's shape.
//
// WHY: one full loop-board read at 100+ open loops carries body-sized labels
// and blocker_detail prose for every row, and outweighs every other verb call
// a session makes. The budget flags answer "what is open / blocked / owned /
// where the program stands / which verb serves this job" without paying for it.
//
// Run with: node --test mcp-server/test/payload-budgets.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";

import { TOOLS } from "../src/tools.js";

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = { id: "test-actor", human: true, slug: "joe" };

const dbFor = (rows) => ({ query: async () => ({ rows }) });

// ── loop-board ────────────────────────────────────────────────────────────────

const boardRows = [
  { number: "495", kind: "open_loop", domain: "system", status: "open",
    owner: "claude", marker: "none", title: null,
    label: "A long label that goes on well past eighty characters just to prove truncation works here",
    joint_owner: false, blocker_class: "human_only",
    blocker_detail: "long prose detail that must NOT appear in summary mode",
    since_text: "2026-08-22", due_on: null, version: 3 },
  { number: "170", kind: "open_loop", domain: "business", status: "open",
    owner: "joe", marker: "none", title: null, label: "Optimise Joe's outlook",
    joint_owner: false, blocker_class: null, blocker_detail: null,
    since_text: "2026-08-04", due_on: null, version: 4 },
];

function boardHandler() {
  // The two modes run DIFFERENT SQL over the same table; hand back rows shaped
  // like each query's own select list so the test proves the summary SQL really
  // selects no body/detail column.
  const queries = [];
  const db = { query: async (sql) => {
    queries.push(sql);
    if (/left\(/i.test(sql)) {
      return { rows: boardRows.map(r => ({
        number: r.number, kind: r.kind, status: r.status, owner: r.owner,
        label: r.label.slice(0, 80), blocker_class: r.blocker_class,
        since_text: r.since_text, due_on: null, version: r.version,
      })) };
    }
    return { rows: boardRows };
  } };
  return { db, queries };
}

test("loop-board summary returns counts and slim rows only", async () => {
  const { db, queries } = boardHandler();
  const result = await TOOLS["loop-board"].handler(db, actor, { summary: true });

  assert.equal(result.summary, true);
  assert.equal(result.count, 2);
  assert.deepEqual(result.by_status, { open: 2 });
  assert.deepEqual(result.by_blocker_class, { human_only: 1, none: 1 });
  assert.deepEqual(result.by_owner, { claude: 1, joe: 1 });
  // THE PAYLOAD IS COMPARED TO THE DECLARATION, not to a second hand-written
  // list. The previous version asserted a required-keys list plus a forbidden
  // list, which is inclusion-only: any EXTRA key passed. That is how the summary
  // rows shipped carrying an undeclared due_on while the flag's own description
  // promised seven keys and the query selected eight, and why a Codex review
  // caught it rather than this test. Reading the promise out of the schema means
  // the two cannot drift again in either direction — adding a column to the
  // query without declaring it fails here, and so does declaring one the query
  // does not return.
  const promise = TOOLS["loop-board"].inputSchema.properties.summary.description;
  const inner = promise.slice(promise.indexOf("{") + 1, promise.indexOf("}"));
  const declared = [];
  let depth = 0, token = "";
  for (const ch of inner) {
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (ch === "," && depth === 0) { declared.push(token); token = ""; continue; }
    token += ch;
  }
  declared.push(token);
  const declaredKeys = declared.map(t => t.trim().split(/[\s(]/)[0]).filter(Boolean).sort();
  assert.ok(declaredKeys.length >= 5, `parsed too few declared keys: ${declaredKeys}`);

  for (const loop of result.loops) {
    assert.deepEqual(Object.keys(loop).sort(), declaredKeys,
      "summary rows must carry EXACTLY what the summary flag promises — no extra key, no missing one");
    // Kept as a named guard on top of the exact match: these are the heavy
    // columns the budget exists to exclude, and naming them makes a regression
    // read as what it is rather than as an anonymous key-set difference.
    for (const forbidden of ["body", "blocker_detail", "domain", "marker", "joint_owner"])
      assert.equal(forbidden in loop, false, `summary rows must not carry ${forbidden}`);
  }
  assert.match(queries[queries.length - 1], /left\(/i, "labels are truncated in SQL");
  assert.doesNotMatch(queries[queries.length - 1], /blocker_detail|coalesce\(body/i,
    "the summary SQL must not even select the heavy columns");
});

test("loop-board without the flag returns today's full rows (backwards compatible)", async () => {
  const { db } = boardHandler();
  const result = await TOOLS["loop-board"].handler(db, actor, {});
  assert.equal(result.summary, undefined);
  assert.equal(result.count, 2);
  assert.equal("by_status" in result, false);
  assert.ok(result.loops[0].blocker_detail !== undefined, "full rows keep blocker_detail");
  assert.ok(!("current_version" in result.loops[0]), "row shape unchanged");
  assert.ok("label" in result.loops[0] && "version" in result.loops[0]);
});

// ── list-verbs ────────────────────────────────────────────────────────────────

test("list-verbs filter matches name AND description, case-insensitively", async () => {
  const byName = await TOOLS["list-verbs"].handler(null, actor, { filter: "CLOSE-LOOP" });
  assert.ok(byName.verbs.some(v => v.name === "close-loop"), "name match is case-insensitive");

  // 'do-it-or-close-it' appears in loop-board's DESCRIPTION, not its name.
  const byDescription = await TOOLS["list-verbs"].handler(null, actor, { filter: "do-it-or-close-it" });
  assert.ok(byDescription.verbs.some(v => v.name === "loop-board"),
    "a description-only match must be found — rule 49c627cc, match on behavior");

  const miss = await TOOLS["list-verbs"].handler(null, actor, { filter: "zzz-no-such-behavior" });
  assert.equal(miss.count, 0);

  const all = await TOOLS["list-verbs"].handler(null, actor, {});
  assert.equal(all.count, Object.keys(TOOLS).length, "absent filter returns every verb");
  assert.ok(all.verbs.every(v => "inputSchema" in v), "default mode keeps schemas");
});

test("list-verbs names_only drops schemas and composes with filter", async () => {
  const bare = await TOOLS["list-verbs"].handler(null, actor, { names_only: true });
  assert.equal(bare.names_only, true);
  assert.ok(bare.verbs.length > 10);
  for (const v of bare.verbs) {
    assert.equal("inputSchema" in v, false, "names_only carries no schemas");
    assert.ok(v.description.length > 0 && v.description.length <= 200);
  }

  const composed = await TOOLS["list-verbs"].handler(null, actor, { filter: "close-loop", names_only: true });
  assert.ok(composed.verbs.some(v => v.name === "close-loop"));
  assert.equal(composed.verbs.some(v => v.name === "loop-board"), false, "filter still narrows under names_only");
});
