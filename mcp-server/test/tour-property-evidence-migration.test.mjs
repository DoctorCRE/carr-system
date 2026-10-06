import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("property evidence is exposed only through a tenant-scoped, rights-filtered read function", () => {
  const sql = readFileSync(new URL("../../migrations/0754_tour_property_evidence.sql", import.meta.url), "utf8");
  assert.match(sql, /function ops\.read_tour_property_evidence\(p_tenant text,p_property uuid,p_as_of timestamptz\)/);
  assert.match(sql, /stable security definer set search_path=pg_catalog,ops,public,pg_temp/);
  assert.match(sql, /tour_jurisdiction_dataset/);
  assert.match(sql, /tour_property_parcel_assertion/);
  assert.match(sql, /tour_source_evidence/);
  assert.match(sql, /allowed_field_classes \? raw\.field/);
  assert.match(sql, /allowed_use_classes \? 'canonical_fact'/);
  assert.match(sql, /'unknown'::text geometry_precision, a\.assertion_method geometry_method/);
  assert.doesNotMatch(sql, /'jurisdiction_reference'::text geometry_precision/);
  assert.match(sql, /revoke all on function ops\.read_tour_property_evidence/);
  assert.match(sql, /grant execute on function ops\.read_tour_property_evidence\(text,uuid,timestamp with time zone\) to carr_writer,carr_authority/);
  assert.doesNotMatch(sql, /grant select on table ops\.tour_property_jurisdiction_assertion/);
});
