import assert from "node:assert/strict";
import test from "node:test";
import * as upstream from "@neondatabase/serverless";
import { neon, neonConfig, Pool, types } from "../src/postgres.ts";

test("HTTP and pooled clients share calendar-only DATE decoding", () => {
  assert.equal(neon, upstream.neon);
  assert.equal(Pool, upstream.Pool);
  assert.equal(neonConfig, upstream.neonConfig);
  assert.equal(types, upstream.types);
  const oid = types.builtins.DATE;
  assert.ok(oid !== undefined);
  for (const value of ["2026-11-09", "2024-02-29", "infinity", "-infinity"])
    assert.equal(types.getTypeParser(oid, "text")(value), value);
});
