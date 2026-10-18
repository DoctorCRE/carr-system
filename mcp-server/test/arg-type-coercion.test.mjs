
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool, coerceArgsToSchema, assertRequiredArgs } from "../src/tools.js";

const S = (properties) => ({ type: "object", properties });


test("THE BUG — a declared boolean arriving as the string \"false\" becomes false, not truthy", () => {
  const args = { personal: "false" };
  coerceArgsToSchema(S({ personal: { type: "boolean" } }), args);
  assert.equal(args.personal, false);
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
  const args = { human_quote: "true", statement: "false", note: "42" };
  coerceArgsToSchema(S({
    human_quote: { type: "string" }, statement: { type: "string" }, note: { type: "string" },
  }), args);
  assert.deepEqual(args, { human_quote: "true", statement: "false", note: "42" });
});

test("a union type is skipped rather than guessed at", () => {
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
  const args = { scope: { section: "Operating mechanics", anything: "true" } };
  coerceArgsToSchema(S({ scope: { type: "object" } }), args);
  assert.deepEqual(args, { scope: { section: "Operating mechanics", anything: "true" } });
});


test("teach's REAL inputSchema still declares personal as a boolean", () => {
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

  assert.equal(inserts[0][4], null, "a shared rule must not be stored with a personal_to actor");
  assert.equal(out.scope_applied, "shared");
  assert.equal(out.personal_requested, false);
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
  for (const [name, tool] of Object.entries(TOOLS)) {
    assert.doesNotThrow(() => coerceArgsToSchema(tool.inputSchema, {}),
      `coerceArgsToSchema threw walking ${name}`);
  }
});


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
  assert.doesNotThrow(() => assertRequiredArgs(schema, { flag: false, n: 0, s: "x" }));
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
