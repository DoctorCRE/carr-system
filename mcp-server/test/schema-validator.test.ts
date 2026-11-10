import assert from "node:assert/strict";
import test from "node:test";
import { compileSchema } from "../../workspace/contracts/schema-validator.ts";

test("local pointers unescape tokens, select entries and ignore ref siblings", () => {
  const root = { $defs: { "a/b~c": { type: "integer" } } };
  const validate = compileSchema(root, { $ref: "#/$defs/a~1b~0c", const: "ignored" });
  assert.deepEqual(validate(2), { valid: true, errors: [] });
  assert.deepEqual(validate("2"), { valid: false, errors: ["$: expected integer, got string"] });
  assert.deepEqual(compileSchema(root, { $ref: "#/$defs/missing" })(42), { valid: true, errors: [] });
  assert.throws(() => compileSchema({ $ref: "elsewhere.json" }), {
    message: "Only local schema references are supported: elsewhere.json",
  });
});

test("recursive references refuse at validation and independent uses remain valid", () => {
  const validate = compileSchema({ $defs: { loop: { $ref: "#/$defs/loop" } }, $ref: "#/$defs/loop" });
  assert.throws(() => validate(null), { message: "Recursive schema reference is unsupported: #/$defs/loop" });
  assert.equal(
    compileSchema({ $defs: { s: { type: "string" } }, allOf: [{ $ref: "#/$defs/s" }, { $ref: "#/$defs/s" }] })("x")
      .valid,
    true,
  );
});

test("oneOf counts zero, one and overlapping branches without branch error leakage", () => {
  const validate = compileSchema({ oneOf: [{ type: "number" }, { type: "integer" }] });
  assert.deepEqual(validate("x").errors, ["$: expected exactly one matching oneOf branch, found 0"]);
  assert.deepEqual(validate(1.5), { valid: true, errors: [] });
  assert.deepEqual(validate(1).errors, ["$: expected exactly one matching oneOf branch, found 2"]);
  assert.deepEqual(compileSchema({ allOf: [false, { not: { const: 1 } }] })(1).errors, [
    "$: schema refused the value",
    "$: value matches forbidden not schema",
  ]);
});

test("inspection covers nested schemas and annotations without inspecting annotation data", () => {
  for (const schema of [
    { mystery: 1 },
    { properties: { x: { mystery: 1 } } },
    { $defs: { x: { mystery: 1 } } },
    { items: { mystery: 1 } },
    { additionalProperties: { mystery: 1 } },
    { not: { mystery: 1 } },
  ]) {
    assert.throws(() => compileSchema(schema), { message: "Unsupported JSON Schema keyword: mystery" });
  }
  assert.equal(compileSchema({ default: { mystery: 1 }, "x-custom": 1 })(null).valid, true);
  assert.throws(() => Reflect.apply(compileSchema, undefined, [null]), { message: "Schema root must be an object" });
  assert.throws(() => compileSchema({ pattern: "[" }), SyntaxError);
});

test("string and numeric limits preserve ordered exact messages", () => {
  assert.deepEqual(compileSchema({ minLength: 4, pattern: "^z", format: "uuid" })("a").errors, [
    "$: shorter than minLength",
    "$: does not match pattern",
    "$: invalid UUID",
  ]);
  assert.deepEqual(compileSchema({ maxLength: 1, format: "date-time" })("bad").errors, [
    "$: longer than maxLength",
    "$: invalid date-time",
  ]);
  assert.deepEqual(compileSchema({ minimum: 2, maximum: 0 })(1).errors, ["$: below minimum", "$: above maximum"]);
  assert.equal(compileSchema({ type: ["number", "null"] })(null).valid, true);
  assert.equal(compileSchema({ type: "number" })(1).valid, true);
  assert.deepEqual(compileSchema({ const: { a: 1 }, enum: [null] })({ a: 2 }).errors, [
    "$: value does not match const",
    "$: value is outside enum",
  ]);
});

test("array and object constraints preserve paths and error ordering", () => {
  assert.deepEqual(
    compileSchema({ minItems: 3, maxItems: 1, uniqueItems: true, items: { type: "string" } })([1, 1]).errors,
    [
      "$: fewer than minItems",
      "$: more than maxItems",
      "$: duplicate array items",
      "$[0]: expected string, got integer",
      "$[1]: expected string, got integer",
    ],
  );
  assert.deepEqual(
    compileSchema({
      required: ["x"],
      properties: { a: { type: "string" }, ignored: false },
      additionalProperties: false,
      minProperties: 4,
      maxProperties: 1,
    })({ a: 1, ignored: 2 }).errors,
    [
      "$.x: required property missing",
      "$.a: expected string, got integer",
      "$.ignored: additional property is not allowed",
      "$: fewer than minProperties",
      "$: more than maxProperties",
    ],
  );
  assert.deepEqual(compileSchema({ additionalProperties: { type: "boolean" } })({ a: 1 }).errors, [
    "$.a: expected boolean, got integer",
  ]);
});

test("unique-by validates declarations including referenced item properties", () => {
  for (const field of ["", " id", "id ", "1id", 1, null])
    assert.throws(() => Reflect.apply(compileSchema, undefined, [{ type: "array", "x-carr-unique-by": field }]), {
      message: "x-carr-unique-by must be a non-empty property name",
    });
  assert.throws(() => compileSchema({ "x-carr-unique-by": "id" }), {
    message: "x-carr-unique-by is only valid on arrays",
  });
  assert.throws(() => compileSchema({ type: "array", "x-carr-unique-by": "id", items: {} }), {
    message: "x-carr-unique-by item schema declares no id property",
  });
  assert.equal(
    compileSchema({
      type: "array",
      "x-carr-unique-by": "id",
      $defs: { item: { properties: { id: {} } } },
      items: { $ref: "#/$defs/item" },
    })([{ id: 1 }]).valid,
    true,
  );
});

test("unique-by distinguishes scalar types and refuses missing or nonfinite keys", () => {
  const validate = compileSchema({ type: "array", "x-carr-unique-by": "id", items: { properties: { id: {} } } });
  assert.deepEqual(validate([{ id: 1 }, { id: "1" }, { id: true }]), { valid: true, errors: [] });
  assert.deepEqual(validate([{ id: 1 }, { id: 1 }]).errors, ["$: duplicate values for x-carr-unique-by id"]);
  assert.deepEqual(
    validate([{}, null, { id: null }, { id: {} }, { id: [] }, { id: Infinity }, { id: undefined }]).errors,
    [
      "$[0]: x-carr-unique-by field id is required",
      "$[1]: x-carr-unique-by field id is required",
      ...[2, 3, 4, 5, 6].map((i) => `$[${i}]: x-carr-unique-by field id must be scalar`),
    ],
  );
});
