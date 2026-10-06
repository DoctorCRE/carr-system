import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const migration = fs.readFileSync(path.join(root, "migrations/0565_tour_property_registration.sql"), "utf8");

test("0565 registers a property only together with its first rights-bound identifier assertion", () => {
  assert.doesNotMatch(migration, /^(begin|commit);/m, "0339+ migrations run in the runner's single transaction");
  assert.match(migration, /create or replace function ops\.register_tour_property\(p_payload jsonb\)\nreturns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp/);
  // The payload is exact-keyed and carries NO property_id: the database mints it.
  assert.match(migration, /array\['assertion_digest','confidence','identifier_scheme','identifier_value','normalized_identifier','observed_at','organization_tenant_id','review_state','rights_receipt_id','source_evidence_id'\]/);
  assert.match(migration, /insert into ops\.tour_property \(organization_tenant_id,property_status\) values \(v_tenant,'active'\)/);
  // The assertion goes through the 0428 seam so its rights guard and conflict marking apply unchanged.
  assert.match(migration, /ops\.append_tour_property_identifier_assertion\(jsonb_build_object\(/);
  assert.match(migration, /tour property is already registered under this identifier/);
  assert.match(migration, /identifier normalization must be lowercase and trimmed/);
  assert.doesNotMatch(migration, /execute\s+.*p_payload/i);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table|alter\s+table\s+ops\.tour_property\b/i);
});

test("0565 grants the door to authority only and proves itself on a disposable transaction", () => {
  assert.match(migration, /revoke all on function ops\.register_tour_property\(jsonb\) from public,carr_reader,carr_writer,carr_jobs,carr_authority;/);
  assert.match(migration, /grant execute on function ops\.register_tour_property\(jsonb\) to carr_authority;/);
  assert.doesNotMatch(migration, /grant execute on function ops\.register_tour_property\(jsonb\) to carr_writer/);
  assert.match(migration, /ROLLBACK_0565_PROOF/);
  assert.match(migration, /0565 FAILED: duplicate registration was accepted/);
  assert.match(migration, /0565 FAILED: caller-supplied property_id was accepted/);
  assert.match(migration, /end \$proof\$;\s*$/);
});
