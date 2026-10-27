// Tests typed guidance precedents and legacy precedent lookup.
import assert from "node:assert/strict";
import test from "node:test";

import { TOOLS } from "../src/tools.js";

const legacyRow = {
  decision_id: "legacy-decision", entry_date: "2026-08-01", title: "Legacy decision",
  human_quote: "Keep the original ruling", agent_rationale: "Historic decision history.",
  author: "joe", provenance: { source: "decision" }, record_kind: "settled_ruling", score: 0.73,
};
const typedRow = {
  decision_id: "typed-precedent", entry_date: "2026-08-15", title: "Typed precedent",
  human_quote: "Preserve searchable precedent", agent_rationale: "Governed typed guidance.",
  author: "joe", provenance: { source: "typed-guidance" }, record_kind: "typed_precedent", score: 0.91,
};

test("find-precedent includes typed current precedents only when the guidance registry is active", async () => {
  const queries = [];
  const db = { async query(sql) {
    queries.push(sql);
    if (sql.includes("to_regclass('public.v_precedent')")) return { rows: [{ t: true }] };
    if (sql.includes("to_regclass('ops.v_guidance_registry_state')")) return { rows: [{ t: true }] };
    if (sql.includes("from ops.v_guidance_registry_state")) return { rows: [{ state: "active" }] };
    if (sql.includes("v_guidance_precedent")) return { rows: [typedRow, legacyRow] };
    throw new Error(`unexpected query: ${sql}`);
  } };

  const out = await TOOLS["find-precedent"].handler(db, { id: "joe" }, { query: "precedent" });

  assert.equal(out.count, 2);
  assert.deepEqual(out.rulings.map(row => row.decision_id), ["typed-precedent", "legacy-decision"]);
  assert.equal(out.rulings[0].provenance.source, "typed-guidance");
  assert.deepEqual(out.rulings.map(row => row.record_kind), ["typed_precedent", "settled_ruling"]);
  assert.match(out.note, /Only settled_ruling results are settled decisions/);
  assert.ok(queries.some(sql => sql.includes("ops.v_guidance_registry_state")));
  assert.ok(queries.some(sql => sql.includes("ops.v_guidance_precedent")));
});

test("find-precedent preserves its legacy query when typed guidance is inactive", async () => {
  const queries = [];
  const db = { async query(sql) {
    queries.push(sql);
    if (sql.includes("to_regclass('public.v_precedent')")) return { rows: [{ t: true }] };
    if (sql.includes("to_regclass('ops.v_guidance_registry_state')")) return { rows: [{ t: true }] };
    if (sql.includes("from ops.v_guidance_registry_state")) return { rows: [{ state: "inactive" }] };
    if (sql.includes("from v_precedent")) return { rows: [legacyRow] };
    throw new Error(`unexpected query: ${sql}`);
  } };

  const out = await TOOLS["find-precedent"].handler(db, { id: "joe" }, { query: "legacy" });

  assert.deepEqual(out.rulings.map(row => row.decision_id), ["legacy-decision"]);
  assert.ok(!queries.some(sql => sql.includes("ops.v_guidance_precedent")));
});
