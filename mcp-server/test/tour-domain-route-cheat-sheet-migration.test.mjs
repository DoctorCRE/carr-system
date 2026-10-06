import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const migration = fs.readFileSync(path.join(root, "migrations/0429_tour_domain_route_cheat_sheet.sql"), "utf8");

test("slice 4 is additive and binds new tours to an opaque client/work subject", () => {
  assert.match(migration, /^begin;/m);
  assert.match(migration, /add column if not exists subject_type text/i);
  assert.match(migration, /add column if not exists subject_id text/i);
  assert.match(migration, /create or replace function ops\.create_tour_domain/i);
  assert.match(migration, /p_subject_type/i);
  assert.match(migration, /p_subject_id/i);
  assert.match(migration, /subject binding is invalid/i);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table/i);
  assert.match(migration, /commit;/i);
});

test("slice 4 preserves immutable accepted route versions and explicit reorder lineage", () => {
  for (const table of ["tour_route_version", "tour_route_stop", "tour_route_stop_transition", "tour_route_version_acceptance"]) {
    assert.match(migration, new RegExp(`create table if not exists ops\\.${table}`, "i"), table);
  }
  assert.match(migration, /locked_appointment/i);
  assert.match(migration, /dwell_minutes/i);
  assert.match(migration, /buffer_minutes/i);
  assert.match(migration, /stop_state.*active.*held.*excluded/i);
  assert.match(migration, /disposition.*unchanged.*reordered.*removed.*held.*excluded.*merged/i);
  assert.match(migration, /p_disposition in \('held','excluded'\) and p_new_route_stop_id is not null/i);
  assert.match(migration, /route acceptance requires an explicit disposition for every prior route stop/i);
  assert.match(migration, /route acceptance must preserve every locked appointment window, dwell, and buffer/i);
  assert.match(migration, /route acceptance refuses concurrent or stale route state/i);
  assert.match(migration, /route transition property identity mismatch/i);
  assert.match(migration, /tour_property_identity_lineage/i);
  assert.match(migration, /reordered route transition requires a sequence change/i);
  assert.match(migration, /p_expected_route_version<>v_accepted/i);
  assert.match(migration, /p_route_version<>v_latest\+1/i);
  assert.match(migration, /v\.route_version=v_accepted[\s\S]*tour_route_version_acceptance/i);
  assert.match(migration, /pg_advisory_xact_lock/i);
  assert.match(migration, /tour_route_version.*append-only/i);
  assert.match(migration, /tour_route_stop.*append-only/i);
  assert.match(migration, /tour_route_stop_transition.*append-only/i);
  assert.match(migration, /tour_route_version_acceptance.*append-only/i);
});

test("slice 4 keeps cheat sheets internal, append-only, and absent from public projections", () => {
  assert.match(migration, /create or replace function ops\.append_tour_cheat_sheet_revision/i);
  assert.match(migration, /create or replace function ops\.restore_tour_cheat_sheet_revision/i);
  assert.match(migration, /restore creates a new cheat sheet revision/i);
  assert.match(migration, /tour cheat sheet content must be an object/i);
  assert.match(migration, /tour_server_actor_id/i);
  assert.match(migration, /current_setting\('carr\.acting_actor_slug', true\)/i);
  assert.match(migration, /revoke all on table ops\.tour_route_version/i);
  assert.match(migration, /grant execute on function ops\.append_tour_cheat_sheet_revision\(text,uuid,jsonb,integer\) to carr_writer/i);
  assert.match(migration, /read_tour_public_projection[\s\S]*does not join cheat-sheet content/i);
  assert.doesNotMatch(migration, /grant\s+(?:all|insert|update|delete)\s+on\s+(?:table\s+)?ops\.tour_route_version/i);
});


test("slice 4 accepts only digest-backed active stops into the canonical projection membership", () => {
  assert.match(migration, /assertion_set_digest text/i);
  assert.match(migration, /tour_route_stop_property_once/i);
  assert.match(migration, /insert into ops\.tour_property_membership/i);
  assert.match(migration, /s\.stop_state='active'/i);
  assert.match(migration, /update ops\.tour set route_version=v_route\.route_version/i);
  assert.match(migration, /tour_rights_provider_policy_lock/i);
  assert.match(migration, /r\.provider=v_route\.routing_provider/i);
  assert.match(migration, /r\.policy_key=v_route\.routing_policy_key/i);
  assert.match(migration, /newer\.provider=r\.provider and newer\.policy_key=r\.policy_key/i);
  assert.match(migration, /drop trigger if exists tour_route_version_append_only/i);
});


test("slice 4 defaults neither revised route seam to public execution nor incomplete routes to canonical", () => {
  assert.match(migration, /revoke all on function ops\.append_tour_route_version\(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text\),ops\.append_tour_route_stop\(text,uuid,uuid,integer,text,text,timestamp with time zone,timestamp with time zone,boolean,integer,integer,text,text\) from public,carr_reader,carr_writer,carr_jobs,carr_authority/i);
  assert.match(migration, /route acceptance requires at least one active stop/i);
  assert.match(migration, /route acceptance requires an explicit transition for every new route stop/i);
});

// The successor adds only the stored subject fields to the deployed projection.
test("internal detail subject successor preserves the prior read and its authorization predicates", () => {
  const prior = fs.readFileSync(path.join(root, "migrations/0759_tour_reviewed_route_digest.sql"), "utf8");
  const next = fs.readFileSync(path.join(root, "migrations/0813_tour_internal_subject_read.sql"), "utf8");
  const functionBody = text => text.slice(text.indexOf("create or replace function ops.read_tour_internal_detail")).split("$$;")[0];
  assert.equal(functionBody(next).replace("'subject_type',t.subject_type,'subject_id',t.subject_id,\n    ", ""), functionBody(prior));
  const contract = JSON.parse(fs.readFileSync(path.join(root, "workspace/contracts/tour-operations-foundation.v1.json")));
  assert.deepEqual(contract.internal_detail_subject.fields, ["subject_type", "subject_id"]);
});
