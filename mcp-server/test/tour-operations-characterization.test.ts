import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { sha256 } from "../src/sha256.ts";
import * as contract from "../src/tour-operations-contract.ts";

const parsed: unknown = JSON.parse(
  readFileSync(new URL("./fixtures/tour-operations-foundation.v1.json", import.meta.url), "utf8"),
);
assert.ok(parsed && typeof parsed === "object" && !Array.isArray(parsed));
const fixture = Object.fromEntries(Object.entries(parsed));
function result(run: () => unknown) {
  try {
    return { value: run() };
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
}
function outputs() {
  const values: unknown[] = [
    undefined,
    null,
    false,
    0,
    NaN,
    Infinity,
    "",
    "safe",
    "bad\u0000",
    "\ud800",
    [],
    {},
    { value: 4, unit: "sf" },
    [{ asset_ref: "asset:public:abcdefghijklmnop" }],
    { nested: null },
    1n,
  ];
  const getter = Object.defineProperty({}, "value", {
    enumerable: true,
    get() {
      throw new Error("GETTER_INVOKED");
    },
  });
  values.push(getter, new Array(1), new Date("2026-08-25T00:00:00Z"));
  const cycle: Record<string, unknown> = {};
  cycle.self = cycle;
  values.push(cycle);
  const rows: unknown[] = [];
  for (const value of values) {
    rows.push(result(() => contract.snapshotCanonicalJsonValue(value)));
    rows.push(result(() => contract.validateCanonicalFieldAssertion(value)));
    rows.push(result(() => contract.validateProjectionDraft(value)));
    rows.push(result(() => contract.canonicalProjectionDigest(value)));
    rows.push(
      result(() =>
        contract.evaluateRightsReceipt(value, { at: "2026-08-25T12:00:00Z", useClass: "client_public_display" }),
      ),
    );
    for (const key of [...contract.PUBLIC_TOUR_FIELD_KEYS, "internal_note"]) {
      rows.push(result(() => contract.snapshotPublicValue(key, value)));
      rows.push(contract.publicValueIsSafe(key, value));
    }
  }
  for (const [key, value] of Object.entries(fixture)) {
    rows.push([key, result(() => contract.validateCanonicalFieldAssertion(value))]);
    rows.push([key, result(() => contract.validateProjectionDraft(value))]);
  }
  rows.push(
    result(() =>
      contract.canonicalProjectionDigest({ projection: fixture.projection, facts: [fixture.projection_fact] }),
    ),
  );
  rows.push(
    result(() =>
      contract.validateProjectionFact(
        fixture.projection_fact,
        fixture.field_assertion,
        fixture.membership,
        fixture.projection,
        fixture.rights_receipt,
        { evidence: fixture.source_evidence },
      ),
    ),
  );
  return rows;
}
test("tour contract output bytes match the JavaScript characterization", () => {
  const digest = createHash("sha256").update(JSON.stringify(outputs())).digest("hex");
  assert.equal(digest, "b4f5949be4b1c7f3c2f7953c243fcb44b6d495b361756e89b71c9864fbd5bcc7");
});

test("SHA-256 strict word reads preserve multi-block and UTF-8 digests", () => {
  for (const value of ["", "abc", "☃醫生", "x".repeat(55), "x".repeat(56), "x".repeat(64), "x".repeat(1024)]) {
    assert.equal(sha256(value), createHash("sha256").update(value).digest("hex"));
  }
});

test("tour contract exported allowlists and timestamp grammar remain exact", () => {
  assert.deepEqual(contract.REQUIRED_PUBLIC_PROPERTY_FIELDS, ["display.name", "display.address"]);
  assert.equal(contract.PUBLIC_ASSET_REFERENCE_RE.source, "^asset:public:[A-Za-z0-9_-]{16,256}$");
  assert.equal(contract.requiredTimestamp("2026-08-25T12:00:00Z"), true);
  assert.equal(contract.requiredTimestamp("2026-02-30T12:00:00Z"), false);
});

test("public metrics retain validation when the array iterator is replaced", () => {
  const original = Array.prototype[Symbol.iterator];
  let safe: unknown;
  try {
    Array.prototype[Symbol.iterator] = function* () {
      return undefined;
    };
    safe = contract.publicValueIsSafe("size", { value: 4, secret: "internal" });
  } finally {
    Array.prototype[Symbol.iterator] = original;
  }
  assert.equal(safe, false);
});
