import { readMigration } from "./migration-source.testhelper.mjs";
import test from "node:test";
import assert from "node:assert/strict";


const migration = await readMigration(new URL("../../migrations/0304_hermes_runtime_admission.sql", import.meta.url));

test("Hermes admission projects the existing envelope and does not create a parallel runtime store", () => {
  assert.match(migration, /create or replace function ops\.hermes_runtime_admission_for_brief/i);
  assert.match(migration, /ops\.execution_envelope_v1/i);
  assert.match(migration, /ops\.context_activation_binding/i);
  assert.match(migration, /ops\.work_request_execution_assignment/i);
  assert.doesNotMatch(migration, /create table/i);
  assert.doesNotMatch(migration, /insert into ops\.(job|tool_call|attempt_receipt)/i);
});

test("Hermes admission binds tenant, sponsor, profile, expiry, read-only surface, and exact context", () => {
  for (const fragment of [
    "current_setting('carr.organization_tenant_id'",
    "sponsoring_human_id=sponsor.id",
    "b.expires_at > now()",
    "e.expires_at > now()",
    "p.status='active'",
    "read_only",
    "hermes_desktop",
    "context_activation_ref",
    "agent_principal_id",
    "configuration_fingerprint",
    "device_binding_status",
    "server_envelope_identity_mismatch",
  ]) assert.match(migration, new RegExp(fragment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), fragment);
  assert.match(migration, /grant execute on function ops\.hermes_runtime_admission_for_brief[\s\S]+to carr_reader, carr_writer/i);
});

test("Hermes admission has typed fail-closed unknown and stale paths", () => {
  assert.match(migration, /p_runtime_slug is distinct from 'hermes-pilot'/i);
  assert.match(migration, /runtime_identity_not_registered/i);
  assert.match(migration, /'not_registered'[\s\S]+runtime_or_activation_missing/i);
  assert.match(migration, /'stale'[\s\S]+activation_or_envelope_not_exact/i);
  assert.match(migration, /'stale'[\s\S]+server_envelope_identity_mismatch/i);
});
