// Verify dated practice evidence through the registered import and read contracts.

import assert from "node:assert/strict";
import test from "node:test";
import { practiceOriginationTools } from "../src/practice-origination.ts";

const tools = practiceOriginationTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn() });
const actor = { id: "00000000-0000-4000-8000-000000000001" };
test("coverage explicitly reports disabled formation and recorder feeds", async () => {
  const db = { query: async () => ({ rows: [], command: "SELECT", rowCount: 0, oid: 0, fields: [] }) };
  const result = await tools["list-practice-origination"].handler(db, actor, {});
  assert.equal(result.contract, "practice-origination.v1");
  assert.equal(result.coverage.find((x) => x.source === "recorder-baldwin-al")?.status, "unavailable");
  assert.equal(result.coverage.find((x) => x.source === "recorder-other")?.status, "uncovered");
  assert.equal(result.coverage.find((x) => x.source === "pecos")?.status, "unavailable");
});

test("canonical evidence hashing agrees with SHA-256 across padding and UTF-8 boundaries", async () => {
  const { createHash } = await import("node:crypto");
  const { sha256 } = await import("../src/sha256.ts");
  for (const text of ["", "abc", "évidence 🩺", "a".repeat(55), "a".repeat(56), "a".repeat(64), "a".repeat(1000)]) {
    assert.equal(sha256(text), createHash("sha256").update(text).digest("hex"));
  }
});
