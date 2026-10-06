// arg-type-coercion.test.mjs — coverage for loop 353, found live 2026-08-13.
//
// THE DEFECT. `teach` decides a taught rule's SCOPE (shared, binding both
// partners, versus personal, binding one) from a declared-boolean argument. It
// stored that scope with loose truthiness (`args.personal ? actor.id : null`)
// and echoed it back with strict equality (`args.personal === true`). MCP
// tool-call arguments are never validated against inputSchema server-side —
// mcp.js's callTool passes `rpc.params?.arguments` straight through — so a
// boolean sent as the STRING "false" was truthy at the storage line and false
// at the echo line. Two live calls on 2026-08-13 asked for a SHARED rule,
// received `personal_requested:false` AND `scope_applied:"personal:joe"`, and
// stored a rule that would have bound one partner while its own text bound
// both. Only a hand comparison of two exported files caught it.
//
// THE SAME ROOT CAUSE as decision 7026246b's compareVersion fix, which repaired
// exactly one field (base_version). A sweep of mcp-server/src found 17 more
// sites reading a declared boolean or number with loose truthiness or bare
// arithmetic, SEVEN of them writing a wrong value straight to the database. So
// the fix is at the choke point (executeRegisteredTool), not per handler.
//
// Run with: node --test mcp-server/test/arg-type-coercion.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool, coerceArgsToSchema, assertRequiredArgs } from "../src/tools.js";

const S = (properties) => ({ type: "object", properties });

// ────────────────────────────────────────────────────────────────────────
// coerceArgsToSchema — pure logic, no DB.
// ────────────────────────────────────────────────────────────────────────

test("THE BUG — a declared boolean arriving as the string \"false\" becomes false, not truthy", () => {
  const args = { personal: "false" };
  coerceArgsToSchema(S({ personal: { type: "boolean" } }), args);
  assert.equal(args.personal, false);
  // The exact assertion the defect failed: the storage line and the echo line
  // must now agree, whichever comparison style each uses.
  assert.equal(args.personal ? "personal" : "shared", "shared");
  assert.equal(args.personal === true, false);
});

test("\"true\" becomes true, and real booleans pass through untouched", () => {
  const args = { a: "true", b: "TRUE", c: " true ", d: true, e: false };
  coerceArgsToSchema(S({
    a: { type: "boolean" }, b: { type: "boolean" }, c: { type: "boolean" },
    d: { type: "boolean" }, e: { type: "boolean" },
  }), args);
  assert.deepEqual(args, { a: true, b: true, c: true, d: true, e: false });
});

test("an unmappable value in a declared boolean throws instead of staying silently truthy", () => {
  for (const bad of ["yes", "1", "", 1, {}]) {
    assert.throws(
      () => coerceArgsToSchema(S({ flag: { type: "boolean" } }), { flag: bad }),
      (e) => e instanceof ToolError && e.payload.error === "invalid_boolean" && e.payload.field === "flag",
      `expected invalid_boolean for ${JSON.stringify(bad)}`);
  }
});

test("declared numbers coerce from numeric strings and refuse non-numeric ones", () => {
  const args = { base_version: "3", limit: " 60 ", n: 7 };
  coerceArgsToSchema(S({
    base_version: { type: "integer" }, limit: { type: "integer" }, n: { type: "number" },
  }), args);
  assert.deepEqual(args, { base_version: 3, limit: 60, n: 7 });

  assert.throws(
    () => coerceArgsToSchema(S({ limit: { type: "integer" } }), { limit: "abc" }),
    (e) => e instanceof ToolError && e.payload.error === "invalid_number");
});

test("STRICTLY SCHEMA-DRIVEN — a declared string holding \"true\" is never touched", () => {
  // The safety property that makes a boundary coercer safe at all: free text is
  // untouchable by construction, because only the DECLARED type is consulted
  // and the value is never sniffed. human_quote, note and rationale are all
  // declared strings and routinely carry prose.
  const args = { human_quote: "true", statement: "false", note: "42" };
  coerceArgsToSchema(S({
    human_quote: { type: "string" }, statement: { type: "string" }, note: { type: "string" },
  }), args);
  assert.deepEqual(args, { human_quote: "true", statement: "false", note: "42" });
});

test("a union type is skipped rather than guessed at", () => {
  // log-decision's `about` takes string OR array; patch-deal-field has nullable
  // strings. Coercing either to one scalar would destroy the other.
  const args = { about: "C-127", other: "5", nullable: "true" };
  coerceArgsToSchema(S({
    about: { oneOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] },
    other: { anyOf: [{ type: "string" }, { type: "null" }] },
    nullable: { type: ["string", "null"] },
  }), args);
  assert.deepEqual(args, { about: "C-127", other: "5", nullable: "true" });
});

test("absent, null and undefined fields are left alone, not defaulted", () => {
  const args = { present: "true", explicitNull: null };
  coerceArgsToSchema(S({
    present: { type: "boolean" }, explicitNull: { type: "boolean" }, missing: { type: "boolean" },
  }), args);
  assert.deepEqual(args, { present: true, explicitNull: null });
  assert.equal("missing" in args, false);
});

test("RECURSION — nested flags coerce, including one inside an array item's nested object", () => {
  // add-premises carries also_listing_side inside ownership[], and force_new one
  // level deeper inside ownership[].new_party. A top-level-only walk misses both.
  const schema = S({
    ownership: { type: "array", items: S({
      also_listing_side: { type: "boolean" },
      new_party: S({ force_new: { type: "boolean" } }),
    }) },
  });
  const args = { ownership: [
    { also_listing_side: "false", new_party: { force_new: "false" } },
    { also_listing_side: "true" },
  ] };
  coerceArgsToSchema(schema, args);
  assert.equal(args.ownership[0].also_listing_side, false);
  assert.equal(args.ownership[0].new_party.force_new, false);
  assert.equal(args.ownership[1].also_listing_side, true);
});

test("a free-form object with no declared properties is a no-op, not a crash", () => {
  // teach's own `scope` is {type:"object"} with no properties.
  const args = { scope: { section: "Operating mechanics", anything: "true" } };
  coerceArgsToSchema(S({ scope: { type: "object" } }), args);
  assert.deepEqual(args, { scope: { section: "Operating mechanics", anything: "true" } });
});

// ────────────────────────────────────────────────────────────────────────
// The real teach schema, and the choke point that applies the coercion.
// ────────────────────────────────────────────────────────────────────────

test("teach's REAL inputSchema still declares personal as a boolean", () => {
  // If this ever stops being true the coercion silently stops covering the
  // field that caused the defect, so it is asserted rather than assumed.
  assert.equal(TOOLS["teach"].inputSchema.properties.personal.type, "boolean");
});

test("REGRESSION — teach through the choke point stores SHARED when personal is \"false\"", async () => {
  const inserts = [];
  const client = { query: async (text, params = []) => {
    if (/insert into rule/i.test(text)) {
      inserts.push(params);
      return { rows: [{ id: "r1", personal_to: params[4] }] };
    }
    return { rows: [] };
  } };
  const actor = { id: "actor-joe", slug: "joe", human: true };

  const out = await executeRegisteredTool(client, actor, "teach", {
    idempotency_key: "k1", statement: "a rule", human_quote: "teach it", personal: "false",
    enforcement_home: "core",
  });

  // personal_to is params[4]; null means SHARED.
  assert.equal(inserts[0][4], null, "a shared rule must not be stored with a personal_to actor");
  assert.equal(out.scope_applied, "shared");
  assert.equal(out.personal_requested, false);
  // The pre-fix response was the contradiction itself: these two disagreed.
  assert.equal(out.scope_applied === "shared", out.personal_requested === false);
});

test("teach with a genuine personal:true still stores PERSONAL", async () => {
  const inserts = [];
  const client = { query: async (text, params = []) => {
    if (/insert into rule/i.test(text)) {
      inserts.push(params);
      return { rows: [{ id: "r1", personal_to: params[4] }] };
    }
    return { rows: [] };
  } };
  const actor = { id: "actor-joe", slug: "joe", human: true };

  for (const val of [true, "true"]) {
    inserts.length = 0;
    const out = await executeRegisteredTool(client, actor, "teach", {
      idempotency_key: "k2", statement: "a rule", human_quote: "teach it", personal: val,
      enforcement_home: "core",
    });
    assert.equal(inserts[0][4], "actor-joe", `personal:${JSON.stringify(val)} must store personal_to`);
    assert.equal(out.scope_applied, "personal:joe");
    assert.equal(out.personal_requested, true);
  }
});

test("teach with personal omitted defaults to SHARED", async () => {
  const inserts = [];
  const client = { query: async (text, params = []) => {
    if (/insert into rule/i.test(text)) {
      inserts.push(params);
      return { rows: [{ id: "r1", personal_to: params[4] }] };
    }
    return { rows: [] };
  } };
  const out = await executeRegisteredTool(client, { id: "actor-joe", slug: "joe", human: true }, "teach", {
    idempotency_key: "k3", statement: "a rule", human_quote: "teach it",
    enforcement_home: "core",
  });
  assert.equal(inserts[0][4], null);
  assert.equal(out.scope_applied, "shared");
});

test("the choke point rejects a garbage boolean before the handler runs", async () => {
  let handlerRan = false;
  const client = { query: async () => { handlerRan = true; return { rows: [] }; } };
  await assert.rejects(
    () => executeRegisteredTool(client, { id: "a", slug: "joe", human: true }, "teach", {
      idempotency_key: "k4", statement: "s", human_quote: "q", personal: "maybe",
    }),
    (e) => e instanceof ToolError && e.payload.error === "invalid_boolean");
  assert.equal(handlerRan, false, "nothing should have touched the DB");
});

test("every verb's inputSchema is walkable without throwing on well-typed args", () => {
  // A coercer that crashed on some verb's schema shape would take down the whole
  // choke point. Walking all of them with an empty payload proves the walk is
  // total over the live registry, not just the schemas this file hand-writes.
  for (const [name, tool] of Object.entries(TOOLS)) {
    assert.doesNotThrow(() => coerceArgsToSchema(tool.inputSchema, {}),
      `coerceArgsToSchema threw walking ${name}`);
  }
});

// ────────────────────────────────────────────────────────────────────────
// REQUIRED ARGUMENTS, added 2026-08-14 after the same choke point let a verb
// answer confidently with nothing.
//
// THE DEFECT. Every verb declares `required` in its inputSchema and NOTHING
// enforced it. mcp.js passes rpc.params.arguments straight through, and the
// local CLI path does too, so a required field that was misspelled — or simply
// absent — arrived as undefined and the handler ran anyway. search-doctrine
// builds websearch_to_tsquery('english', undefined), which is not an error in
// Postgres: it matches nothing. Measured live before this fix:
//
//     search-doctrine {"query":"HIPAA"}  ->  ok:true, hits:[], total:0
//     search-doctrine {}                 ->  ok:true, hits:[], total:0
//     search-doctrine {"q":"HIPAA"}      ->  20 hits
//
// A call carrying NO ARGUMENTS AT ALL returned a clean, confident empty answer.
//
// WHY THAT IS WORSE THAN A CRASH, and the reason this is not cosmetic: an empty
// result set is indistinguishable from a genuine absence. On 2026-08-14 a
// session searched doctrine for a settled council ruling, was handed total:0,
// and concluded the ruling did not exist — then filed a defect saying the
// doctrine read path was broken. The ruling was there. The parameter name was
// wrong. Rule c53beeaa already says an ok:true confirms the call PARSED and
// never that the values landed; this is that rule enforced at the door.
//
// The near-miss suggestion exists because that is the actual failure mode: the
// caller had the schema in front of them and still sent `query` for `q`.

test("required: a missing required argument is refused, not answered emptily", () => {
  const schema = { type: "object", properties: { q: { type: "string" } }, required: ["q"] };
  assert.throws(() => assertRequiredArgs(schema, {}),
    (e) => e instanceof ToolError && /missing_required/.test(JSON.stringify(e.payload)));
});

test("required: a MISNAMED argument is refused and the near-miss is named", () => {
  const schema = { type: "object", properties: { q: { type: "string" } }, required: ["q"] };
  try {
    assertRequiredArgs(schema, { query: "HIPAA" });
    assert.fail("should have thrown");
  } catch (e) {
    const p = JSON.stringify(e.payload);
    assert.match(p, /missing_required/);
    assert.match(p, /"q"/, "must name the field it wanted");
    assert.match(p, /query/, "must echo the unrecognised key the caller sent");
  }
});

test("required: read-doctrine's real shape — document vs slug/doc_id", () => {
  const schema = { type: "object", properties: { document: { type: "string" } },
                   required: ["document"] };
  for (const wrong of [{ slug: "x" }, { doc_id: "x" }]) {
    assert.throws(() => assertRequiredArgs(schema, wrong),
      (e) => /missing_required/.test(JSON.stringify(e.payload)),
      `${JSON.stringify(wrong)} must be refused`);
  }
  assert.doesNotThrow(() => assertRequiredArgs(schema, { document: "x" }));
});

test("required: null and empty string are absent; false and 0 are PRESENT", () => {
  const schema = { type: "object",
                   properties: { flag: { type: "boolean" }, n: { type: "integer" },
                                 s: { type: "string" } },
                   required: ["flag", "n", "s"] };
  // A caller who genuinely means false or zero has supplied the argument.
  assert.doesNotThrow(() => assertRequiredArgs(schema, { flag: false, n: 0, s: "x" }));
  // null and "" carry no instruction and are what an unset template produces.
  assert.throws(() => assertRequiredArgs(schema, { flag: false, n: 0, s: "" }),
    (e) => /missing_required/.test(JSON.stringify(e.payload)));
  assert.throws(() => assertRequiredArgs(schema, { flag: null, n: 0, s: "x" }),
    (e) => /missing_required/.test(JSON.stringify(e.payload)));
});

test("required: a schema with no required list accepts anything", () => {
  assert.doesNotThrow(() =>
    assertRequiredArgs({ type: "object", properties: { a: { type: "string" } } }, {}));
  assert.doesNotThrow(() => assertRequiredArgs(undefined, {}));
});

test("required: every verb that declares required fields is actually guarded", () => {
  // The choke point must apply to ALL of them, not a hand-kept list — that is
  // the whole reason this lives in executeRegisteredTool.
  const withRequired = Object.entries(TOOLS)
    .filter(([, t]) => Array.isArray(t?.inputSchema?.required) && t.inputSchema.required.length);
  assert.ok(withRequired.length > 20,
    `expected many verbs to declare required fields, found ${withRequired.length}`);
  for (const [name, t] of withRequired) {
    assert.throws(() => assertRequiredArgs(t.inputSchema, {}),
      (e) => e instanceof ToolError,
      `${name} declares required ${JSON.stringify(t.inputSchema.required)} but an empty call was accepted`);
  }
});
