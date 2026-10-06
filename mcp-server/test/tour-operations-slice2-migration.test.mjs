import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const migration = fs.readFileSync(path.join(root, "migrations/0427_tour_rights_projection_hardening.sql"), "utf8");

test("slice 2 hardening is forward-only and preserves exact provider-policy-receipt lineage", () => {
  assert.match(migration, /^begin;/m);
  assert.match(migration, /add column if not exists rights_provider text/i);
  assert.match(migration, /add column if not exists rights_policy_key text/i);
  assert.match(migration, /r\.id=\(select e\.rights_receipt_id/i);
  assert.match(migration, /r\.provider=e\.rights_provider and r\.policy_key=e\.rights_policy_key/i);
  assert.match(migration, /newer\.provider=r\.provider and newer\.policy_key=r\.policy_key/i);
  assert.match(migration, /pg_advisory_xact_lock/i);
  assert.match(migration, /rights receipt refuses source intake/i);
  assert.match(migration, /rights receipt refuses asserted field\/use/i);
  assert.match(migration, /0427 requires zero pre-existing projection seal receipts/i);
  assert.doesNotMatch(migration, /canonical_projection_digest[\s\S]{0,240}not valid/i);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table/i);
  assert.match(migration, /commit;/i);
});

test("slice 2 refuses direct public state escalation and arbitrary public asset URLs", () => {
  assert.match(migration, /projection creation requires draft status/i);
  assert.match(migration, /publication creation requires draft state/i);
  assert.match(migration, /projection seal cannot publish/i);
  assert.match(migration, /\^asset:public:\[A-Za-z0-9_-\]\+\$/);
  assert.match(migration, /char_length\(item->>'asset_ref'\) not between 29 and 269/i);
  assert.match(migration, /not in \('asset_ref','alt','caption','source'\)/i);
  assert.doesNotMatch(migration, /\('url','alt','caption'\)/i);
});

test("slice 2 seals one complete fact set atomically with a database-computed digest and role boundary", () => {
  assert.match(migration, /create or replace function ops\.tour_canonical_projection_digest/i);
  assert.match(migration, /public-tour-projection-digest\.v1/i);
  assert.match(migration, /base64\(tenant UTF-8\)/i);
  assert.match(migration, /array_to_string\(array\[/i);
  assert.match(migration, /replace\(encode\(convert_to\(p_tenant,'UTF8'\),'base64'\),E'\\n',''\)/i);
  assert.match(migration, /order by f\.property_id::text,convert_to\(f\.display_field_key,'UTF8'\),f\.field_assertion_id::text/i);
  assert.match(migration, /property_id\|field_assertion_id\|route_version/i);
  assert.match(migration, /date_trunc\('milliseconds',v_projection\.as_of at time zone 'UTC'\)/i);
  assert.match(migration, /projection canonical digest is database-computed/i);
  assert.match(migration, /create or replace function ops\.seal_tour_public_projection/i);
  assert.match(migration, /projection seal requires one complete selected-property fact set/i);
  assert.match(migration, /cross join \(values \('display\.name'::text\),\('display\.address'::text\)\) required\(field_key\)/i);
  assert.match(migration, /canonical_projection_digest\)/i);
  assert.match(migration, /tour_share_revocation_receipt_guard/i);
  assert.match(migration, /create or replace function ops\.append_tour_rights_receipt\(p_payload jsonb\)/i);
  assert.match(migration, /create or replace function ops\.revoke_tour_rights_receipt/i);
  assert.match(migration, /create or replace function ops\.append_tour_source_evidence\(p_payload jsonb\)/i);
  assert.match(migration, /create or replace function ops\.append_tour_field_assertion\(p_payload jsonb\)/i);
  assert.match(migration, /create or replace function ops\.create_tour_public_projection_draft/i);
  assert.match(migration, /create or replace function ops\.read_tour_public_projection/i);
  assert.match(migration, /with clock as materialized \(select statement_timestamp\(\) as evaluated_at\)/i);
  assert.match(migration, /r\.expires_at is not null and r\.expires_at <= clock\.evaluated_at/i);
  assert.match(migration, /newer\.receipt_version > r\.receipt_version[\s\S]*newer\.effective_at <= clock\.evaluated_at/i);
  assert.match(migration, /conflict\.opened_at <= clock\.evaluated_at/i);
  assert.match(migration, /resolution\.resolved_at <= clock\.evaluated_at/i);
  assert.match(migration, /join ops\.tour_field_assertion a/i);
  assert.match(migration, /join ops\.tour_source_evidence e/i);
  assert.match(migration, /join ops\.tour_rights_receipt r/i);
  assert.match(migration, /e\.retrieval_status='read' and e\.retrieved_at <= p\.as_of/i);
  assert.match(migration, /a\.confidence <> 'unknown'/i);
  assert.match(migration, /a\.observed_at <= p\.as_of/i);
  assert.match(migration, /from ops\.tour_fact_conflict conflict/i);
  assert.match(migration, /resolution\.selected_field_assertion_id=a\.id/i);
  assert.match(migration, /e\.retrieval_status <> 'read' or e\.retrieved_at > p\.as_of/i);
  for (const field of ["value", "source_evidence_id", "rights_receipt_id", "observed_at", "effective_from", "effective_to"]) assert.match(migration, new RegExp("'" + field + "'", "i"), field);
  assert.doesNotMatch(migration, /'stable_locator'/i);
  assert.match(migration, /drop trigger if exists tour_publication_creation_guard/i);
  assert.match(migration, /drop trigger if exists tour_share_revocation_receipt_guard/i);
  assert.match(migration, /drop trigger if exists tour_public_projection_append_only/i);
  assert.match(migration, /revoke all on table ops\.tour_rights_receipt/i);
  assert.match(migration, /grant execute on function ops\.seal_tour_public_projection\(text,uuid,jsonb,text,text\) to carr_authority/i);
  assert.match(migration, /grant execute on function ops\.append_tour_field_assertion\(jsonb\) to carr_authority/i);
  assert.doesNotMatch(migration, /grant\s+(?:all|insert|update|delete)\s+on\s+(?:table\s+)?ops\.tour_publication/i);
});
