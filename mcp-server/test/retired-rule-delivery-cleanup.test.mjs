import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const migration = fs.readFileSync(
  new URL("../../migrations/0463_retired_rule_delivery_cleanup.sql", import.meta.url), "utf8");

test("retirement removes only the retired rule's delivery row in the same transaction", () => {
  assert.match(migration,
    /if old\.status in \('proposed','active'\) and new\.status='retired' then[\s\S]*?delete from ops\.rule_load_layer where rule_id=new\.id/);
  assert.match(migration,
    /after update of status on public\.rule[\s\S]*?when \(old\.status is distinct from new\.status\)/);
  assert.doesNotMatch(migration, /truncate|delete from ops\.rule_load_layer\s*;/i);
});

test("cleanup is trigger-only, pinned, and grants no direct runtime authority", () => {
  assert.match(migration,
    /language plpgsql security definer[\s\S]*?set search_path=pg_catalog,public,ops/);
  assert.match(migration,
    /revoke all on function ops\.retired_rule_delivery_cleanup\(\)[\s\S]*?public,carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.doesNotMatch(migration, /grant execute/i);
  assert.match(migration, /Production application remains Joe-gated/);
});
