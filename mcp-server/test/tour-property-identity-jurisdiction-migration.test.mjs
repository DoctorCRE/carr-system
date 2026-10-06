import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const migration = fs.readFileSync(path.join(root, "migrations/0428_tour_property_identity_jurisdiction.sql"), "utf8");

test("Slice 3 separates immutable canonical property identity from every external projection", () => {
  assert.match(migration, /^begin;/m);
  for (const table of [
    "tour_property_identifier_assertion",
    "tour_property_identifier_alias",
    "tour_property_identity_lineage",
    "tour_property_address_assertion",
    "tour_property_parcel_assertion",
    "tour_property_building_assertion",
    "tour_property_provider_projection",
  ]) assert.match(migration, new RegExp(`create table if not exists ops\\.${table}`, "i"), table);
  assert.match(migration, /canonical property identity.*not.*provider/i);
  assert.match(migration, /unique \(organization_tenant_id,property_id,identifier_scheme,normalized_identifier\)/i);
  assert.doesNotMatch(migration, /unique \(organization_tenant_id,identifier_scheme,normalized_identifier\)/i);
  assert.match(migration, /new\.review_state:='conflicted'/i);
  assert.match(migration, /identity lineage cannot self-reference/i);
  assert.match(migration, /tour_property_identifier_assertion_append_only/i);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table/i);
});

test("Slice 3 models jurisdiction as evidence-bound, versioned context for the initial Florida counties", () => {
  for (const table of [
    "tour_jurisdiction_dataset",
    "tour_property_jurisdiction_assertion",
  ]) assert.match(migration, new RegExp(`create table if not exists ops\\.${table}`, "i"), table);
  for (const county of ["Escambia", "Santa Rosa", "Okaloosa", "Walton", "Bay"])
    assert.match(migration, new RegExp(`'${county}'`, "i"), county);
  assert.match(migration, /authoritative dataset.*rights/i);
  assert.match(migration, /jurisdiction assertion does not make a legal determination/i);
  assert.match(migration, /as_of.*authoritative/i);
});

test("Slice 3 separates coordinate candidates from human entrance verification and grants only narrow seams", () => {
  for (const table of [
    "tour_property_coordinate_candidate",
    "tour_coordinate_entrance_verification_receipt",
  ]) assert.match(migration, new RegExp(`create table if not exists ops\\.${table}`, "i"), table);
  assert.match(migration, /coordinate candidate is not canonical/i);
  assert.match(migration, /entrance verification requires an entrance-compatible coordinate role/i);
  assert.match(migration, /create or replace function ops\.append_tour_property_identifier_assertion\(p_payload jsonb\)/i);
  assert.match(migration, /create or replace function ops\.append_tour_coordinate_candidate\(p_payload jsonb\)/i);
  assert.match(migration, /create or replace function ops\.append_tour_entrance_verification_receipt\(p_payload jsonb\)/i);
  assert.match(migration, /current_setting\('carr\.verified_human_actor_slug',true\)/i);
  assert.match(migration, /entrance verification requires a verified human authority session/i);
  assert.match(migration, /security definer set search_path=pg_catalog,ops,public,pg_temp/i);
  assert.match(migration, /revoke all on table ops\.tour_property_identifier_assertion/i);
  assert.match(migration, /grant execute on function ops\.append_tour_property_identifier_assertion\(jsonb\) to carr_authority/i);
  assert.match(migration, /grant execute on function ops\.append_tour_coordinate_candidate\(jsonb\) to carr_writer/i);
  assert.doesNotMatch(migration, /grant\s+(?:all|insert|update|delete)\s+on\s+(?:table\s+)?ops\.tour_property_coordinate_candidate/i);
  assert.match(migration, /commit;/i);
});


test("Slice 3 is reapplication-safe and exposes only exact-key, pinned-definer seams", () => {
  for (const table of [
    "tour_property_identifier_assertion", "tour_property_identifier_alias", "tour_property_identity_lineage",
    "tour_property_address_assertion", "tour_property_parcel_assertion", "tour_property_building_assertion",
    "tour_property_provider_projection", "tour_jurisdiction_dataset", "tour_property_jurisdiction_assertion",
    "tour_property_coordinate_candidate", "tour_coordinate_entrance_verification_receipt",
  ]) assert.match(migration, new RegExp(`drop trigger if exists ${table}_append_only on ops\\.${table};`, "i"), table);
  for (const fn of [
    "append_tour_property_identifier_assertion", "append_tour_coordinate_candidate", "append_tour_entrance_verification_receipt",
  ]) assert.match(migration, new RegExp(`create or replace function ops\\.${fn}\\(p_payload jsonb\\)\\nreturns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp`, "i"), fn);
  assert.match(migration, /jsonb_object_keys\(p_payload\)/i);
  assert.doesNotMatch(migration, /execute\s+.*p_payload/i);
  assert.match(migration, /revoke all on function ops\.tour_slice3_rights_guard/i);
});
