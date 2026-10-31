// A JSON-string array must survive the choke point AS AN ARRAY.
//
// The production failure of 2026-08-15: coerceArgsToSchema required
// Array.isArray before it would touch an array argument, so a JSON string went
// through untouched. doctrine-sections then measured .length on ~80 characters
// and refused a batch of TWO as "batch_too_large, max 50"; a single id (~38
// characters) slipped under the limit and died casting a string to uuid[] as a
// bare "internal error". claim-doctrine-sections iterated the same string into
// characters, so no session could claim a doctrine section — and doctrine is
// single-writer by ORDER 38, so the write path was down for hours.
//
// ASSERTED AT THE CHOKE POINT, not through a handler. The 2026-08-13 ruling put
// coercion in one schema-driven place rather than in seventeen handlers, so that
// is where the fix lives and where it has to be proven. Testing it through one
// handler would pass while every other verb taking an array stayed broken.
import test from "node:test";
import assert from "node:assert/strict";
import { coerceArgsToSchema } from "../src/tools.js";

const schema = { type: "object", properties: {
  section_ids: { type: "array", items: { type: "string" } } } };
const A = "d7eca7c6-95bc-4f40-8690-cb47ca62ac59";
const B = "f85501dd-3625-4f50-a8e0-fcb2cb81456f";

test('a two-id JSON string becomes two ids, not eighty characters', () => {
  const args = { section_ids: JSON.stringify([A, B]) };
  coerceArgsToSchema(schema, args);
  assert.deepEqual(args.section_ids, [A, B]);
});

test('a plain array passes through unchanged', () => {
  const ids = [A], args = { section_ids: ids };
  coerceArgsToSchema(schema, args);
  assert.equal(args.section_ids, ids);
});

test('a non-JSON string remains available for the handler to refuse', () => {
  const args = { section_ids: 'not-json' };
  coerceArgsToSchema(schema, args);
  assert.equal(args.section_ids, 'not-json');
});

test('an already-correct call is not disturbed', () => {
  const args = { section_ids: [A, B], unrelated: 'x' };
  coerceArgsToSchema(schema, args);
  assert.deepEqual(args, { section_ids: [A, B], unrelated: 'x' });
});
