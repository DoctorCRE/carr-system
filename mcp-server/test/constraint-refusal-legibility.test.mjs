import test from "node:test";
import assert from "node:assert/strict";
import { pgConstraintError, describeConstraint, ToolError } from "../src/tools.js";

const violation = () => Object.assign(new Error("new row violates check constraint"), {
  code: "23514", constraint: "deal_dates_ordered", table: "deal", column: undefined,
});

const catalogClient = (rows) => ({ query: async () => ({ rows }) });

test("a multi-column refusal names the fields the rule is actually about", async () => {
  const refusal = pgConstraintError(violation());
  assert.ok(refusal instanceof ToolError, "class 23 must translate at all");
  assert.equal(refusal.payload.column, null, "precondition: Postgres named no column");

  const described = await describeConstraint(catalogClient([{
    table_name: "deal", definition: "CHECK ((expires_on > signed_on))",
    columns: ["expires_on", "signed_on"],
  }]), refusal);

  assert.deepEqual(described.payload.columns, ["expires_on", "signed_on"]);
  assert.equal(described.payload.rule, "CHECK ((expires_on > signed_on))");
  assert.match(described.payload.hint, /expires_on, signed_on/,
    "the hint must name the fields; a caller who gets only a constraint name " +
    "cannot tell which of their inputs to change");
  assert.match(described.payload.hint, /combination/,
    "it must say the fields are judged together -- each can be individually valid");
});

test("a single-column refusal keeps its own hint, which is the better one", async () => {
  const refusal = pgConstraintError(Object.assign(new Error("bad enum"), {
    code: "23514", constraint: "loop_marker_check", table: "loop", column: "marker",
  }));
  const before = refusal.payload.hint;
  const described = await describeConstraint(catalogClient([{
    table_name: "loop", definition: "CHECK ((marker = ANY (ARRAY['bell'::text])))",
    columns: ["marker"],
  }]), refusal);
  assert.equal(described.payload.hint, before,
    "when the database already named the field, replacing its hint with the " +
    "multi-column explanation makes the message worse, not better");
  assert.deepEqual(described.payload.columns, ["marker"], "columns are still added");
});

test("a catalog lookup that fails returns the refusal untouched", async () => {
  const refusal = pgConstraintError(violation());
  const broken = { query: async () => { throw new Error("connection is gone"); } };
  const described = await describeConstraint(broken, refusal);
  assert.equal(described, refusal);
  assert.equal(described.payload.error, "invalid_field_value");
  assert.equal(described.payload.constraint, "deal_dates_ordered");
});

test("no client, no constraint name, and an empty catalog answer are all survivable", async () => {
  const refusal = pgConstraintError(violation());
  assert.equal(await describeConstraint(null, refusal), refusal);
  assert.equal(await describeConstraint(catalogClient([]), refusal), refusal);
  assert.equal(await describeConstraint(catalogClient([{ columns: [] }]), refusal), refusal);
  const noName = pgConstraintError(Object.assign(new Error("x"), { code: "23505" }));
  assert.equal(await describeConstraint(catalogClient([{ columns: ["a", "b"] }]), noName), noName);
});

test("the server actually calls the translator -- the whole point", async () => {
  const { readFileSync } = await import("node:fs");
  const server = readFileSync(new URL("../src/mcp.js", import.meta.url), "utf8");
  assert.match(server, /pgConstraintError\(e\)/,
    "mcp.js must call pgConstraintError on a failed verb, or every database " +
    "refusal reaches the caller as unhandled_verb_failure with a stack trace");
  assert.match(server, /await describeConstraint\(client, refusal\)/,
    "the enrichment must run on the still-open client inside callTool -- by " +
    "the time the RPC handler's catch runs, the pool is closed");
  const callToolStart = server.indexOf("export async function callTool");
  assert.ok(callToolStart >= 0, "mcp.js must export callTool");
  const callToolBody = server.slice(callToolStart);
  const rollbackAt = callToolBody.indexOf('await client.query("rollback")');
  assert.ok(rollbackAt >= 0, "callTool must roll back its open transaction");
  const catchBody = callToolBody.slice(rollbackAt);
  assert.ok(catchBody.indexOf("pgConstraintError") < catchBody.indexOf("throw e;"),
    "the translation must come before the bare rethrow, or it never runs");
});
