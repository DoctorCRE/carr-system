import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { TOOLS } from "../src/tools.js";

const sponsored = (runtime, sponsor, via = "local-token") => ({
  slug: runtime, human: false, via,
  sponsoring_human_slug: sponsor, human_slug: sponsor, sponsor_required: false,
});

// memory.js is mixed read/write code. Only these two current write SQL literal
// shapes may use the actor table; classification is content-based so a future
// read-shaped literal in that file cannot hide behind a filename allowlist.
const MEMORY_WRITE_LITERAL_PATTERNS = [
  /insert into memory_item[\s\S]*select id from actor where slug=\$10/i,
  /update memory_item[\s\S]*select id from actor where slug=\$6/i,
];
const memoryWriteLiteralClassified = literal =>
  MEMORY_WRITE_LITERAL_PATTERNS.some(pattern => pattern.test(literal));

// engineering-runtime.js has two exact actor lookup shapes inside the
// write:true admitEngineeringSlice path. Keep this content-specific: a future
// actor lookup elsewhere in that file must still be reviewed by the scanner.
const ENGINEERING_ADMISSION_LITERAL_PATTERNS = [
  /select id,\s*slug from actor where id=\$1::uuid and slug='codex' and active and kind='automation' for share/i,
  /select id,\s*slug from actor where slug=\$1 and active and kind='automation' for share/i,
];
const engineeringAdmissionLiteralClassified = literal =>
  ENGINEERING_ADMISSION_LITERAL_PATTERNS.some(pattern => pattern.test(literal));

test("registered search-doctrine resolves joe-local sponsorship to Joe's human actor", async () => {
  const calls = [];
  const client = {
    query: async (sql, params) => {
      calls.push({ sql, params });
      if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "joe-human-id" }] };
      return { rows: [{ section_key: "diagnosis-checklist", final_score: 1 }] };
    },
  };
  const result = await TOOLS["search-doctrine"].handler(client, sponsored("joe-local", "joe"), {
    q: "record layer outage diagnosis",
    content_classes: ["runbook"],
    limit: 3,
  });

  assert.deepEqual(result.hits, [{ section_key: "diagnosis-checklist", final_score: 1 }]);
  const retrievalCall = calls.find(call => /search_doctrine_situations/.test(call.sql));
  assert.ok(retrievalCall, "search-doctrine must call the shared database ranker");
  // The trailing true is the zero-hit fallback opt-in (loop 518): the live
  // verb always allows it; strict semantics live behind the OFF default.
  assert.deepEqual(retrievalCall.params,
    ["record layer outage diagnosis", "joe-human-id", ["runbook"], 3, null, true]);
  const scopeCall = calls.find(call => /retrieval_visibility_actor_id/.test(call.sql));
  assert.deepEqual(scopeCall.params, ["joe"]);
  // The active-human constraint still binds; migration 0223 moved it INSIDE
  // retrieval_visibility_actor_id, because filtering on actor.kind and
  // actor.active from the read connection is precisely what returned
  // "permission denied for table actor" for every search. The function body
  // carries the predicate now, and the migration test below asserts it there.
  assert.doesNotMatch(scopeCall.sql, /\bfrom\s+actor\b/,
    "the read path must not select from the actor table directly");
  assert.match(scopeCall.sql, /retrieval_visibility_actor_id/);
  assert.equal(calls.some(call => /ts_rank_cd|doctrine_revision/.test(call.sql)), false,
    "the verb must not retain a duplicate ranking query");
});

test("registered search-doctrine resolves Dell sponsorship only to Dell's human actor", async () => {
  const calls = [];
  const client = { query: async (sql, params) => {
    calls.push({ sql, params });
    if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "dell-human-id" }] };
    return { rows: [] };
  } };
  await TOOLS["search-doctrine"].handler(client, sponsored("codex", "dell", "oauth-google"), {
    q: "personal operating preference", limit: 5,
  });
  assert.deepEqual(calls[0].params, ["dell"]);
  assert.deepEqual(calls.find(call => /search_doctrine_situations/.test(call.sql)).params,
    ["personal operating preference", "dell-human-id", null, 5, null, true]);
});

test("unsponsored search is shared-only and invalid required sponsorship is refused", async () => {
  const sharedCalls = [];
  const sharedClient = { query: async (sql, params) => {
    sharedCalls.push({ sql, params });
    return { rows: [] };
  } };
  await TOOLS["search-doctrine"].handler(sharedClient,
    { slug: "codex", human: false, via: "agent-token", sponsoring_human_slug: null },
    { q: "shared doctrine", limit: 2 });
  assert.equal(sharedCalls.some(call => /retrieval_visibility_actor_id/.test(call.sql)), false);
  assert.deepEqual(sharedCalls[0].params, ["shared doctrine", null, null, 2, null, true]);

  let queried = false;
  const refusedClient = { query: async () => { queried = true; return { rows: [] }; } };
  await assert.rejects(
    TOOLS["search-doctrine"].handler(refusedClient,
      { slug: "codex", human: false, via: "oauth-google",
        sponsoring_human_slug: null, sponsor_required: true },
      { q: "personal doctrine" }),
    // Typed since 2026-08-21: the refusal carries a payload rather than a
    // colon-packed message string, so mcp.js can name it instead of reporting
    // the bare "internal_error" it gives any untyped throw.
    error => {
      assert.equal(error.payload?.error, "retrieval_scope_refused");
      assert.equal(error.payload?.reason, "missing_or_ambiguous_sponsor");
      return true;
    },
  );
  assert.equal(queried, false, "invalid sponsor must be refused before any visibility query");

  const inactiveCalls = [];
  const inactiveClient = { query: async (sql, params) => {
    inactiveCalls.push({ sql, params });
    return { rows: [] };
  } };
  await assert.rejects(
    TOOLS["search-doctrine"].handler(inactiveClient, sponsored("joe-local", "joe"),
      { q: "personal doctrine" }),
    // The payload now also reports WHICH sponsor and HOW MANY rows matched,
    // because 0 and 2 are different production faults and the old message
    // distinguished neither.
    error => {
      assert.equal(error.payload?.error, "retrieval_scope_refused");
      assert.equal(error.payload?.reason, "sponsor_not_active_human");
      assert.equal(error.payload?.sponsor, "joe");
      assert.equal(error.payload?.matching_rows, 0);
      return true;
    },
  );
  assert.equal(inactiveCalls.length, 1);
  assert.match(inactiveCalls[0].sql, /retrieval_visibility_actor_id/,
    "sponsor resolution goes through the definer function, not the actor table");
});

test("situation curation verbs are registered in the MCP tool surface", () => {
  for (const name of [
    "propose-retrieval-concept", "propose-retrieval-phrase",
    "propose-retrieval-mapping", "propose-retrieval-retirement",
    "approve-retrieval-proposals", "retire-retrieval-curation",
  ]) assert.ok(TOOLS[name], `${name} is registered`);
});

test("the visibility resolver still constrains to an active human, in the migration", () => {
  // The predicate this test used to assert on the WIRE now lives in SQL. If it
  // is ever dropped there, the read path would resolve any actor row by slug.
  const here = path.dirname(fileURLToPath(import.meta.url));
  const migration = fs.readFileSync(
    path.join(here, "../../migrations/0223_doctrine_search_reads_without_writing_or_reading_actor.sql"), "utf8");
  const fn = migration.slice(migration.indexOf("function retrieval_visibility_actor_id"));
  assert.match(fn, /kind\s*=\s*'human'/, "resolver must still require a human actor");
  assert.match(fn, /active\s*=\s*true/, "resolver must still require an active actor");
  assert.match(fn, /security definer/, "the reader depends on definer rights here");
  assert.match(fn, /grant execute on function retrieval_visibility_actor_id\(text\) to carr_reader/,
    "carr_reader must be able to call it");
  assert.doesNotMatch(migration, /grant\s+select\s+on\s+table\s+public\.actor\s+to\s+carr_reader/i,
    "the fix must not widen the read credential to the identity table");
});


// THE STANDING HAZARD, not the instance (added 2026-08-21 after this bug).
//
// carr_reader holds column SELECT on actor.id and actor.slug and NONE on kind
// or active. Postgres refuses a predicate over a column you cannot read, so any
// read verb that filters the actor table on kind or active dies with "permission
// denied for table actor" — a driver error, flattened by mcp.js to a bare
// "internal error" that names nothing. That is the most natural query anyone
// would write, which is why it needs a contract rather than vigilance.
//
// This asserts the SHAPE. Every site that filters actor this way is listed with
// the verb that owns it and why it is safe. A new one fails this test until
// someone classifies it — and if it belongs to a READ verb, the answer is a
// definer function like retrieval_visibility_actor_id, never a wider grant.
//
// NOTE the predicate forms: four of the five sites say bare `and active`, not
// `active = true`. A scan for `active\s*=` alone would have missed every one.
test("no read verb filters the actor table on a column carr_reader cannot read", () => {
  const CLASSIFIED = {
    "capability-program.js": "start-capability-project and siblings — write:true",
    "investigation.js": "ownedOpenRun, reached only by investigation write verbs",
    "tools.js": "set-national-account-owner, resolve-post-call-candidate, deal-room field update — all write:true",
  };

  const here = path.dirname(fileURLToPath(import.meta.url));
  const srcDir = path.join(here, "../src");
  // Matches join as well as from, and an optional schema qualifier. Nothing in
  // src writes public.actor today, but migrations use that style constantly, so
  // the next person to qualify a schema would otherwise write an invisible site.
  const ACTOR = /\b(?:from|join)\s+(?:public\.)?actor\b/i;
  // Covers =, <>, !=, IN, IS, a bare or aliased `active`, and a negated one.
  // An alias prefix is allowed (a.kind), an arbitrary suffix is NOT: the looser
  // form matched error_kind in an unrelated insert and produced a false positive.
  // Any mention of kind or active inside an actor statement counts, and there is
  // deliberately no cleverness about whether it is a filter or a projection:
  // carr_reader cannot read either column, so SELECTING one is denied exactly
  // like filtering on one. An earlier lookahead here tried to exclude
  // projections and silently lost `active is true` as well.
  const PREDICATE = /\b(?:\w+\.)?kind\b|\b(?:\w+\.)?active\b/i;

  // THE UNIT IS THE STRING LITERAL, NOT A LINE WINDOW. A fixed window is a
  // guess: this repo formats SQL across many lines, so a predicate four lines
  // below its FROM slipped through. It is also unsafe in the other direction —
  // an earlier attempt walked back to the nearest quote character and happily
  // spanned an apostrophe in a comment, flagging a write-path query that has no
  // such predicate at all. A SQL statement lives inside ONE literal, so that is
  // the unit. Backticks may span newlines; the quoted forms may not, which is
  // exactly the language rule that stops a comment apostrophe swallowing code.
  const LITERAL = /`(?:[^`\\]|\\.)*`|'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g;

  const found = [];
  for (const name of fs.readdirSync(srcDir).filter(f => f.endsWith(".js")).sort()) {
    const text = fs.readFileSync(path.join(srcDir, name), "utf8");
    for (const m of text.matchAll(LITERAL)) {
      if (!ACTOR.test(m[0]) || !PREDICATE.test(m[0])) continue;
      const lineNo = text.slice(0, m.index).split("\n").length;
      found.push({ name, lineNo, literal: m[0], text: m[0].replace(/\s+/g, " ").trim().slice(0, 90) });
    }
  }

  assert.ok(found.length > 0, "the scanner itself must still match something, or it has silently rotted");

  const unclassified = found.filter(f =>
    !(CLASSIFIED[f.name] ||
      (f.name === "relationship-network.js" && /join public\.actor a on a\.id=/.test(f.literal) && !/\ba\.(?:kind|active)\b/.test(f.literal)) ||
      (f.name === "memory.js" && memoryWriteLiteralClassified(f.literal)) ||
      // undo-lead-move is a partner-only writer. Its predecessor association
      // must authenticate the historical approver; no read handler uses this SQL.
      (f.name === "lead-automation.js" &&
        f.literal.includes("select count(*)=1 and bool_and(m.id=$2) as associated") &&
        f.literal.includes("e.verb='approve-lead-move' and e.cause='human_stated'") &&
        f.literal.includes("m.approved_by=e.actor_id")) ||
      (f.name === "engineering-runtime.js" && engineeringAdmissionLiteralClassified(f.literal))));
  assert.deepEqual(unclassified, [],
    `these filter the actor table on kind or active and are not classified as write-only paths. ` +
    `If any belongs to a READ verb it is the outage of 2026-08-17 again: ` +
    unclassified.map(f => `${f.name}:${f.lineNo} ${f.text}`).join(" | "));

  // The read path specifically must never appear here at all.
  assert.equal(found.some(f => f.name === "situation-retrieval.js"), false,
    "doctrine search must resolve its sponsor through retrieval_visibility_actor_id, not the table");
});

test("memory actor classification is literal-specific, not filename-wide", () => {
  const writeInsert = "insert into memory_item (kind) values ((select id from actor where slug=$10 and kind='human'))";
  const writeUpdate = "update memory_item set owner_actor_id=(select id from actor where slug=$6 and active)";
  const readShape = "select id from actor where slug=$1 and kind='human' and active=true";
  assert.equal(memoryWriteLiteralClassified(writeInsert), true);
  assert.equal(memoryWriteLiteralClassified(writeUpdate), true);
  assert.equal(memoryWriteLiteralClassified(readShape), false,
    "a future memory read literal must remain unclassified and fail the architectural gate");
});

test("engineering admission actor classification is literal-specific", () => {
  const priorSession = "select id, slug from actor where id=$1::uuid and slug='codex' and active and kind='automation' for share";
  const executor = "select id, slug from actor where slug=$1 and active and kind='automation' for share";
  const readShape = "select id from actor where slug=$1 and kind='human' and active=true";
  assert.equal(engineeringAdmissionLiteralClassified(priorSession), true);
  assert.equal(engineeringAdmissionLiteralClassified(executor), true);
  assert.equal(engineeringAdmissionLiteralClassified(readShape), false,
    "a future Engineering read literal must remain unclassified and fail the architectural gate");
});

// The contract above is only worth its allowlist if the scanner cannot be
// walked around. A second session attacked the first version and found FIVE
// evasions; every one is pinned here, plus two shapes that must NOT be flagged,
// because a noisy contract gets switched off. The patterns are duplicated on
// purpose — if someone edits the ones above without editing these, this fails.
test("the actor-filter scanner cannot be walked around", () => {
  const ACTOR = /\b(?:from|join)\s+(?:public\.)?actor\b/i;
  const PREDICATE = /\b(?:\w+\.)?kind\b|\b(?:\w+\.)?active\b/i;
  const LITERAL = /`(?:[^`\\]|\\.)*`|'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g;
  const flags = src => [...src.matchAll(LITERAL)].some(m => ACTOR.test(m[0]) && PREDICATE.test(m[0]));

  const mustFlag = {
    "the outage query itself": `c.query("select id from actor where slug=$1 and kind='human' and active=true")`,
    "bare and active, the form four live sites use": `c.query("select id from actor where slug=$1 and active")`,
    "schema-qualified, the style every migration uses": `c.query("select id from public.actor where slug=$1 and active")`,
    "aliased column": "c.query(`select a.id from actor a where a.slug=$1 and a.active`)",
    "active is true": `c.query("select id from actor where active is true")`,
    "negated": `c.query("select id from actor where slug=$1 and not active")`,
    "inequality on kind": `c.query("select id from actor where kind <> 'automation'")`,
    "predicate four lines below the from": "c.query(`\n select id\n from actor\n where slug=$1\n and active\n`)",
  };
  for (const [why, src] of Object.entries(mustFlag))
    assert.equal(flags(src), true, `scanner must catch: ${why}`);

  const mustNotFlag = {
    "a clean lookup on granted columns only": `c.query("select id from actor where slug=$1")`,
    "an apostrophe in a nearby comment must not swallow code":
      `// the token's actor is active by now\nc.query("select id from actor where slug=$1")`,
  };
  for (const [why, src] of Object.entries(mustNotFlag))
    assert.equal(flags(src), false, `scanner must not flag: ${why}`);
});
