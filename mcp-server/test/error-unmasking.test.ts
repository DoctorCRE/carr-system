// error-unmasking.test.ts — a verb failure must name its own cause.
//
// WHAT THIS COSTS WHEN IT IS MISSING, measured on 2026-08-21 in one session:
//
//   * complete-capability-project took `select ... for update` on
//     ops.capability_verification, a table deliberately made immutable and on
//     which the serving role holds only insert and select. Row locks need
//     UPDATE. Postgres raised 42501 insufficient_privilege on every completion
//     the verb had ever attempted, and the caller saw "internal error". The AI
//     Engineering Suite therefore read 0 complete of 51 while six of its
//     projects were finished, and the count was mistaken for a measure of
//     output rather than a broken verb.
//
//   * close-loop's successor_loop and update-decision's decision_id each take
//     only a full uuid, while both verbs' own hints invite the human form —
//     "name the open loop", and an id the system itself prints in 8 characters.
//     A short id reaching a uuid column raises 22P02, and again the caller saw
//     "internal error". Two defects, one class.
//
// Every one of those was diagnosed by opening the handler's SQL, because the
// error said nothing. The detail was never actually secret: it was already
// recorded to the incident store and already returned in the JSON-RPC `data`
// field, which MCP clients drop. The only thing missing was putting it where
// the caller reads.
//
// Run with: node --test mcp-server/test/error-unmasking.test.ts

import assert from "node:assert/strict";
import { test } from "node:test";
import { ToolError } from "../src/tool-error.ts";

function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const legacy = object(await import(new URL("../src/tools.js", import.meta.url).href));

// A Postgres driver error carries `code` as the SQLSTATE string; the rest is
// optional and varies by fault class.
const translate = legacy.pgConstraintError;
assert.ok(typeof translate === "function");
const pgConstraintError = (error: unknown): unknown => translate(error);
const pgError = (fields: { code: string; message?: string; table?: string; constraint?: string }) =>
  Object.assign(new Error(fields.message || "db"), fields);

test("permission denied is translated, not swallowed — the 0-of-51 defect", () => {
  const translated = pgConstraintError(
    pgError({
      code: "42501",
      message: "permission denied for table capability_verification",
      table: "capability_verification",
    }),
  );
  assert.ok(translated instanceof ToolError, "42501 must produce a named ToolError, not fall through");
  const p = translated.payload;
  assert.equal(p.error, "database_refused_the_statement");
  assert.equal(p.fault, "insufficient_privilege");
  assert.equal(p.sqlstate, "42501");
  assert.ok(typeof p.message === "string");
  assert.match(
    p.message,
    /permission denied for table capability_verification/,
    "the caller must be told WHICH table refused; that is the whole diagnosis",
  );
  assert.ok(typeof p.hint === "string");
  assert.match(p.hint, /row lock/i, "the hint must name the row-lock cause, which is how this defect actually arose");
});

test("a bad uuid is translated — the short-id class, hit twice in one day", () => {
  const translated = pgConstraintError(
    pgError({
      code: "22P02",
      message: 'invalid input syntax for type uuid: "#213"',
    }),
  );
  assert.ok(translated instanceof ToolError);
  assert.equal(translated.payload.fault, "invalid_text_representation");
  assert.ok(typeof translated.payload.message === "string");
  assert.match(
    translated.payload.message,
    /#213/,
    "the offending value must survive into the message, or the caller cannot see which argument was wrong",
  );
  assert.ok(typeof translated.payload.hint === "string");
  assert.match(translated.payload.hint, /short id|uuid/i);
});

test("schema-drift faults are named rather than left bare", () => {
  for (const [code, fault] of [
    ["42703", "undefined_column"],
    ["42P01", "undefined_table"],
    ["42883", "undefined_function"],
  ] satisfies [string, string][]) {
    const translated = pgConstraintError(pgError({ code, message: `${fault} somewhere` }));
    assert.ok(translated instanceof ToolError, `${code} must translate`);
    assert.equal(translated.payload.fault, fault);
  }
});

test("constraint violations keep their existing, more specific shape", () => {
  // The pre-existing translation is better than the generic one for these —
  // it names the constraint. Widening the function must not flatten it.
  const translated = pgConstraintError(
    pgError({
      code: "23505",
      message: "duplicate key",
      constraint: "work_request_ref_key",
      table: "work_request",
    }),
  );
  assert.ok(translated instanceof ToolError);
  assert.equal(
    translated.payload.error,
    "invalid_field_value",
    "a constraint violation must not be reclassified as a database fault",
  );
  assert.equal(translated.payload.violation, "unique_violation");
  assert.equal(translated.payload.constraint, "work_request_ref_key");
});

test("an unrelated error is still left alone for the caller above to handle", () => {
  assert.equal(pgConstraintError(new Error("something else entirely")), null);
  assert.equal(
    pgConstraintError(pgError({ code: "08006", message: "connection failure" })),
    null,
    "codes with no translation must return null rather than a misleading name",
  );
  assert.equal(pgConstraintError(null), null);
  assert.equal(
    pgConstraintError({ code: 42501 }),
    null,
    "SQLSTATE is a string; a numeric code is not a Postgres error and must not be guessed at",
  );
});

test("connection strings are redacted out of anything surfaced", () => {
  const translated = pgConstraintError(
    pgError({
      code: "42501",
      message: "permission denied; connecting as postgresql://carr_writer:hunter2@db.example/carr", // ci-secret-scan: allow — invented fixture; the whole point of this case is that it must NOT survive into the payload
    }),
  );
  assert.ok(translated instanceof ToolError);
  assert.ok(typeof translated.payload.message === "string");
  assert.doesNotMatch(
    translated.payload.message,
    /hunter2/,
    "a credential must never ride out on an error, however useful the rest of the string is",
  );
  assert.ok(typeof translated.payload.message === "string");
  assert.match(translated.payload.message, /\[redacted\]/);
});
