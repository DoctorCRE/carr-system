import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { canonicalJson, digest } from "../src/artifact-trust.js";

test("canonical bytes retain sorted UTF-16 keys, array order and escaping", () => {
  assert.equal(
    canonicalJson({ z: [3, { b: true, a: null }], a: 'line\n"' }),
    '{"a":"line\\n\\"","z":[3,{"a":null,"b":true}]}',
  );
  assert.equal(canonicalJson({ 2: "two", 10: "ten" }), '{"10":"ten","2":"two"}');
  assert.equal(canonicalJson({ "\uffff": 2, "\ud800\udc00": 1 }), '{"𐀀":1,"￿":2}');
});

test("canonicalization preserves existing non-JSON edge behavior", () => {
  assert.equal(canonicalJson(undefined), undefined);
  assert.equal(canonicalJson(Symbol("value")), undefined);
  assert.equal(
    canonicalJson(() => 1),
    undefined,
  );
  const sparse: unknown[] = Array(5);
  Object.assign(sparse, { 0: undefined, 2: NaN, 3: Infinity, 4: -0 });
  assert.equal(canonicalJson(sparse), "[,,null,null,0]");
  assert.equal(canonicalJson({ z: undefined, a: new Date(0) }), '{"a":{},"z":undefined}');
  assert.equal(canonicalJson(Buffer.from([2, 1])), '{"0":2,"1":1}');
  assert.throws(() => canonicalJson(1n), TypeError);
  const cycle: { self?: unknown } = {};
  cycle.self = cycle;
  assert.throws(() => canonicalJson(cycle), RangeError);
});

test("digest preserves raw strings and buffers and canonicalizes other values", () => {
  assert.equal(digest("abc"), "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  assert.equal(digest(Buffer.from("abc")), digest("abc"));
  assert.equal(
    digest(Buffer.from([0xff, 0x00, 0x80])),
    "sha256:ef192b7af54e943f206ab27075ec1805384c972c9959fc5820f1fa7d5268fcef",
  );
  const bytes = '{"a":1,"z":[false,null]}';
  assert.equal(digest({ z: [false, null], a: 1 }), `sha256:${createHash("sha256").update(bytes).digest("hex")}`);
  assert.equal(digest({ a: 1, z: [false, null] }), digest({ z: [false, null], a: 1 }));
  assert.notEqual(digest([1, 2]), digest([2, 1]));
  assert.throws(() => digest(undefined), { code: "ERR_INVALID_ARG_TYPE" });
  assert.throws(() => digest(1n), TypeError);
});
