import { readMigration } from "./migration-source.testhelper.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const sql = readMigration(path.join(ROOT, "migrations/0300_operational_hermes_bot_profiles.sql"));

test("0300 staffs the fixed eight-profile Hermes roster with advisory model rulings", () => {
  for (const key of ["doc", "deal-steward", "intake-clerk", "marketing-ops", "system-watch",
    "designer", "builder", "reviewer"])
    assert.match(sql, new RegExp(`'${key}'`));
  assert.match(sql, /xai-oauth\/grok-4\.6/);
  assert.match(sql, /nous\/deepseek\/deepseek-v4-pro/);
  assert.match(sql, /nous\/moonshotai\/kimi-k3/);
  assert.match(sql, /openrouter\/stealth\/ox-alpha/);
  assert.match(sql, /current_desk = 'hermes-desktop'/);
  assert.match(sql, /insert into agent_profile \(profile_key, display_name, charter, status\)/);
  assert.match(sql, /on conflict \(profile_key\) do nothing/);
  assert.match(sql, /insert into agent_profile_assignment/);
  assert.match(sql, /03000000-0000-4000-8000-000000000008/);
  assert.match(sql, /ruling_basis.*'human'/s);
});

test("0300 seeds every identity so snapshot rehearsal does not depend on 0284 data", () => {
  const seed = sql.split("on conflict (profile_key) do nothing", 1)[0];
  for (const key of ["doc", "deal-steward", "intake-clerk", "marketing-ops", "system-watch",
    "designer", "builder", "reviewer"])
    assert.match(seed, new RegExp(`'${key}'`), `${key} must be in the idempotent seed`);
});

test("0300 distinguishes five everyday seats from the three-seat contingency bench", () => {
  assert.match(sql, /profile_key in \('doc','deal-steward','intake-clerk','marketing-ops','system-watch'\)/);
  assert.match(sql, /profile_key in \('designer','builder','reviewer'\)/);
  assert.match(sql, /operational and contingency profile groups are not both active/);
});

test("0300 has no authority or Hermes credential mutation", () => {
  assert.doesNotMatch(sql, /insert into actor|update actor|grant .* on agent_profile.*authority/i);
  assert.match(sql, /CARR profile only, no authority grant/);
  assert.match(sql, /not create Hermes credentials/);
});
