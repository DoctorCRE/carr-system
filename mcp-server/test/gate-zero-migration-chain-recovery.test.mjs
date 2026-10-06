// Applied migrations are immutable. Production recorded migration 0505 with the
// digest below; every checkout must retain those exact bytes and put later
// corrections in a new forward migration.

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";

const applied0505 = readFileSync(new URL(
  "../../migrations/0505_gate_zero_tagged_digest_and_candidate_reads.sql",
  import.meta.url));
const recovery0506Url = new URL(
  "../../migrations/0506_gate_zero_applied_0505_recovery.sql", import.meta.url);
const recovery0506 = existsSync(recovery0506Url)
  ? readFileSync(recovery0506Url, "utf8") : "";

test("migration 0505 remains byte-identical to the production-applied file", () => {
  assert.equal(
    createHash("sha256").update(applied0505).digest("hex"),
    "d9494cbdd700c61ca2d273eba802997e3367c4f39b558eb9a80c48eee99962ea");
});

test("migration 0506 carries the Gate Zero corrections after immutable 0505", () => {
  assert.match(recovery0506,
    /create or replace function ops\.benchmark_gate_zero_outcome\(\)/i);
  assert.match(recovery0506,
    /v_recomputed_digest := ops\.gate_zero_outcome_digest\(v_row\.receipt\)/i);
  assert.match(recovery0506,
    /create or replace function ops\.gate_zero_record_read_only_outcome\(\s*p_idempotency_key uuid,\s*p_receipt jsonb/i);
  assert.match(recovery0506,
    /returning it unchanged \(recorded full %, offered full %, recorded projection %, offered projection %\)/i);
  assert.match(recovery0506,
    /candidate_scoped_digest is informational only/i);
});
