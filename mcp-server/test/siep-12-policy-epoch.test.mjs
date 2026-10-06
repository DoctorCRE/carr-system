import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import {
  assertCurrentPolicyEpoch,
  normalizePolicyEpochStatus,
  PolicyEpochRefusal,
} from "../src/policy-epoch.js";

const migrationUrl = new URL("../../migrations/0455_siep12_policy_epoch.sql", import.meta.url);

function status(overrides = {}) {
  return {
    current_epoch: 3,
    request_epoch: 3,
    epoch_state: "current",
    compatibility_state: "compatible",
    reason_id: null,
    current_entry_digest: `sha256:${"a".repeat(64)}`,
    registry_version: "scac-mutation-registry.v2",
    registry_digest: `sha256:${"b".repeat(64)}`,
    compatibility_authority: "fact_only_not_enforcement",
    ...overrides,
  };
}

test("epoch guard accepts only exact current, well-formed server status", () => {
  assert.equal(assertCurrentPolicyEpoch(status()).current_epoch, 3);
  for (const epoch_state of ["stale", "rolled_back", "future", null
  ]) {
    assert.throws(() => assertCurrentPolicyEpoch(status({
      epoch_state,
      compatibility_state: "incompatible",
      reason_id: "scac.refusal.epoch_incompatible",
    })),
      error => error instanceof PolicyEpochRefusal && error.reason === "epoch_incompatible");
  }
  assert.throws(() => normalizePolicyEpochStatus(status({ request_epoch: 2 })), /epoch_status_malformed/);
  assert.throws(() => normalizePolicyEpochStatus(status({ compatibility_authority: "authorizing" })),
    /epoch_status_malformed/);
  assert.throws(() => normalizePolicyEpochStatus(status({ current_epoch: 0 })), /epoch_status_malformed/);
  assert.throws(() => normalizePolicyEpochStatus(status({ registry_digest: "caller-label" })),
    /epoch_status_malformed/);
});

test("epoch helper exposes a fact only and no mutation executor", async () => {
  const module = await import("../src/policy-epoch.js");
  assert.equal(module.beforeEpochBoundMutation, undefined);
  assert.equal(assertCurrentPolicyEpoch(status()).compatibility_state, "compatible");
});

test("migration defines one append-only typed authority and preserves downstream boundaries", () => {
  const sql = fs.readFileSync(migrationUrl, "utf8");
  assert.match(sql, /create table ops\.scac_policy_epoch\b/i);
  assert.doesNotMatch(sql, /create table ops\.scac_policy_epoch_(?:release|command|compatibility)\b/i);
  assert.match(sql, /scac_policy_epoch_status\(bigint,text\)/i);
  assert.match(sql, /deferrable initially deferred/i);
  assert.match(sql, /schema_migrations/i);
  assert.match(sql, /doctrine_meta/i);
  assert.match(sql, /rule_delivery_policy/i);
  assert.match(sql, /scac-mutation-registry\.v2/);
  assert.match(sql, /before update or delete on ops\.scac_policy_epoch/i);
  assert.match(sql, /grant execute on function ops\.scac_policy_epoch_status\(bigint,text\),ops\.scac_mutation_registration_v2\(text,text\) to carr_reader,carr_writer,carr_jobs,carr_authority/i);
  assert.doesNotMatch(sql, /grant (?:insert|update|delete|truncate|all) on ops\.scac_policy_epoch/i);
  assert.doesNotMatch(sql, /^\s*(?:begin|commit)\s*;/im);
  assert.match(sql, /atomic_database_mediation_operational[^\n]*false/i);
  assert.match(sql, /production_enforcement_active[^\n]*false/i);
  assert.doesNotMatch(sql, /mutation_authorized/i);
});
