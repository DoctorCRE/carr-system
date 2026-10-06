import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const migration = fs.readFileSync(
  new URL("../../migrations/0324_siep_program_authority.sql", import.meta.url),
  "utf8",
);
const controllerMigration = fs.readFileSync(
  new URL("../../migrations/0335_engineering_controller_currentness.sql", import.meta.url),
  "utf8",
);
const dbGate = fs.readFileSync(new URL("../../ops/siep-program-local-pg-gate.py", import.meta.url), "utf8");
const migrationNames = fs.readdirSync(new URL("../../migrations/", import.meta.url))
  .filter((name) => name.endsWith(".sql"))
  .sort();

test("0324 is immediately before the already-merged 0325 source", () => {
  const siepIndex = migrationNames.indexOf("0324_siep_program_authority.sql");
  assert.notEqual(siepIndex, -1);
  assert.equal(migrationNames[siepIndex + 1], "0325_engineering_claim_envelope_eligibility.sql");
});

test("0324 keeps Work Request as SIEP package lifecycle authority", () => {
  assert.match(migration, /carr-system-integrity-elimination-v1/);
  assert.match(migration, /references ops\.work_request\(id\)/);
  assert.doesNotMatch(migration, /create table if not exists ops\.siep_(?:task|package_state|tracker)/);
  assert.match(migration, /create trigger siep_program_identity_guard_before_update/);
  assert.match(migration, /create trigger siep_program_transition_guard_before_update/);
});

test("0324 adds only reviewed graph structures plus an immutable command receipt", () => {
  for (const table of [
    "siep_package_contract",
    "siep_program_dependency",
    "siep_component_alias",
    "siep_evidence_link",
    "siep_lane_lock",
    "siep_command_receipt",
    "siep_job_evidence_binding",
  ]) {
    assert.match(migration, new RegExp(`create table if not exists ops\\.${table}`));
  }
  assert.match(migration, /before update or delete on ops\.siep_package_contract/);
  assert.match(migration, /before update or delete on ops\.siep_program_dependency/);
  assert.match(migration, /before update or delete on ops\.siep_component_alias/);
  assert.match(migration, /before update or delete on ops\.siep_evidence_link/);
  assert.match(migration, /before update or delete on ops\.siep_command_receipt/);
  assert.match(migration, /before update or delete on ops\.siep_job_evidence_binding/);
});

test("0324 exposes typed least-privilege verbs and closes raw DML", () => {
  for (const fn of [
    "siep_read_program",
    "siep_claim_package",
    "siep_transition_package",
    "siep_attach_evidence",
    "siep_bind_evidence_job",
    "siep_record_joe_decision",
    "siep_acquire_lane_lock",
    "siep_release_lane_lock",
    "siep_terminal_status",
  ]) {
    assert.match(migration, new RegExp(`create or replace function ops\\.${fn}`));
  }
  assert.match(migration, /revoke all on ops\.siep_package_contract, ops\.siep_program_dependency,/s);
  assert.match(migration, /from public,carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.match(migration, /grant execute on function ops\.siep_read_program\(\) to carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.match(migration, /grant execute on function ops\.siep_claim_package\(text,text,uuid,uuid\) to carr_writer/);
  assert.match(migration, /grant execute on function ops\.siep_transition_package\(text,integer,text,text,uuid,uuid\) to carr_writer/);
  assert.match(migration, /grant execute on function ops\.siep_attach_evidence\(text,text,uuid,text,text,text,uuid\) to carr_writer,carr_authority/);
  assert.match(migration, /grant execute on function ops\.siep_bind_evidence_job\(text,integer,text,uuid,uuid\) to carr_authority/);
  assert.match(migration, /grant execute on function ops\.siep_record_joe_decision\(text,text,text,uuid\) to carr_authority/);
  assert.match(migration, /grant execute on function ops\.siep_terminal_status\(\) to carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.doesNotMatch(migration, /grant (?:insert|update|delete|all) on ops\.siep_/i);
});

test("0324 seeds first-class package Work Requests, aliases, and reviewed critical edges", () => {
  for (const key of ["B0", "00", "01", "02", "03", "04", "05", "06A", "06B", "24A", "24B", "44"]) {
    assert.match(migration, new RegExp(`'${key}'`));
  }
  assert.doesNotMatch(migration, /^\s*\('06',\d+/m);
  assert.doesNotMatch(migration, /^\s*\('24',\d+/m);
  for (const alias of ["SCAC-00", "SCAC-16", "MPE-17A", "MPE-17H"]) {
    assert.match(migration, new RegExp(`'${alias}'`));
  }
  for (const edge of [
    ["03", "02"], ["03", "12"], ["03", "15"], ["03", "17"], ["03", "20"],
    ["04", "03"], ["04", "11"], ["04", "18"],
    ["05", "04"], ["05", "17"], ["05", "18"], ["05", "21"], ["05", "23"],
    ["23", "06A"], ["06B", "06A"], ["06B", "04"], ["06B", "23"],
    ["24A", "14"], ["25", "24A"], ["30", "15"], ["30", "23"],
    ["24B", "24A"], ["42", "05"], ["42", "06B"], ["42", "41"],
    ["43", "42"], ["44", "43"],
  ]) {
    assert.match(migration, new RegExp(`\\('${edge[0]}','${edge[1]}'\\)`));
  }
});

test("0324 embeds Studio as optional discovered capacity, never a DAG dependency", () => {
  assert.match(migration, /studio-executor/);
  assert.match(migration, /hardware_facts_discovered/);
  assert.match(migration, /benchmark_gated/);
  assert.match(migration, /optional_non_blocking/);
  assert.match(migration, /no_offline_root_signing_authority/);
  assert.match(migration, /Dell filesystem and admin actions remain on Dell MPE/);
  assert.doesNotMatch(migration, /M5 (?:Max|Pro)/);
  assert.doesNotMatch(migration, /depends_on_package_key[^;]*studio-executor/s);
});

test("0324 makes terminal closure evidence- and approval-gated", () => {
  assert.match(migration, /required_evidence_kinds/);
  assert.match(migration, /independent_review/);
  assert.match(migration, /two_clean_audit_cycles/);
  assert.match(migration, /joe_go_no_go/);
  assert.match(migration, /unresolved_findings/);
  assert.match(migration, /SIEP terminal authority refuses completion/);
  assert.match(migration, /pg_advisory_xact_lock/);
});

test("0324 derives command authority and binds evidence to current immutable facts", () => {
  assert.doesNotMatch(migration, /p_(?:executor|holder)_actor/);
  assert.doesNotMatch(migration, /p_executor_tier/);
  assert.match(migration, /work_request_version integer not null/);
  assert.match(migration, /manifest_digest text not null/);
  assert.match(migration, /server_digest:=ops\.siep_current_evidence_digest/);
  assert.match(migration, /e\.evidence_digest=ops\.siep_current_evidence_digest/);
  assert.match(migration, /e\.source_observed_at=d\.occurred_at/);
  assert.match(migration, /later\.new_value->>'package_key'=c\.package_key/);
  assert.match(migration, /select max\(dw\.closed_at\) from ops\.siep_program_dependency/);
  assert.match(migration, /session_user<>'carr_authority_joe'/);
  assert.match(migration, /lane\.session_ref<>p_session_ref or lane\.lease_token<>p_lease_token/);
  assert.match(migration, /SIEP transition requires the exact live holder-bound package lane lock/);
  assert.match(migration, /independent review requires an authority-attested artifact-bound receipt/);
  assert.match(migration, /source_row#>>'\{job,definition_key\}'='engineering-slice'/);
  assert.match(migration, /independent and terminal evidence requires the authenticated Joe authority session/);
  assert.match(migration, /SIEP Joe decision events are immutable/);
  assert.match(migration, /max\(e\.occurred_at\)\+interval '1 microsecond'/);
  assert.match(migration, /engineering_execution_envelope env on env\.job_id=j\.id/);
  assert.match(migration, /ja\.id=er\.job_attempt_id and ja\.job_id=j\.id/);
  assert.match(migration, /The raw lease token is a one-time delivery secret/);
  assert.match(migration, /select \* into prior from ops\.siep_command_receipt/);
  assert.doesNotMatch(migration, /select \* into prior from public\.event/);
  for (const fn of ["claim_package", "transition_package", "acquire_lane_lock", "release_lane_lock"]) {
    const body = migration.match(new RegExp(`create or replace function ops\\.siep_${fn}\\([\\s\\S]*?end \\$\\$;`))?.[0] || "";
    assert.match(body, /pg_advisory_xact_lock\(hashtextextended\('siep-lane:'/);
  }
  assert.match(migration, /unique\(package_key,ledger_kind,ledger_id\)/);
  assert.match(migration, /SIEP package transitions require exact version plus one/);

  assert.match(migration, /idempotency_key_reuse/);
  assert.match(migration, /safe:\[a-z0-9\]/);
});

test("0335 makes reviewer and SIEP Engineering stamps database-owned and replay-fail-closed", () => {
  assert.match(controllerMigration, /if new\.contract_version is not null then[\s\S]*?reviewer contract version is caller-controlled[\s\S]*?new\.contract_version:='engineering-review\.v1'/i);
  assert.match(controllerMigration, /if new\.engineering_contract_version is not null then[\s\S]*?SIEP Engineering contract version is caller-controlled[\s\S]*?new\.engineering_contract_version:='engineering-review\.v1'/i);
  assert.match(controllerMigration, /siep_bind_evidence_job_unchecked_0324[\s\S]*?historical or superseded SIEP Engineering evidence binding is not 0335 verified/i);
  assert.match(controllerMigration, /reviewer\.slug='joe' and reviewer\.active/i);
  assert.match(controllerMigration, /order by actor\.id for share/i);
  assert.match(dbGate, /caller-controlled/);
  assert.match(dbGate, /historical or superseded SIEP Engineering evidence binding is not 0335 verified/);
  assert.match(dbGate, /valid SIEP evidence binding was not stamped by the database/);
});

test("the rollback-only DB gate owns exact-set and adversarial behavior", () => {
  assert.match(dbGate, /^# ci: db-gate/m);
  assert.match(dbGate, /actual_packages != PACKAGES/);
  assert.match(dbGate, /actual_aliases != ALIASES/);
  assert.match(dbGate, /actual_edges != EDGES/);
  assert.match(dbGate, /len\(EDGES\) != 88/);
  assert.match(dbGate, /raw carr_writer update reached a SIEP Work Request/);
  assert.match(dbGate, /lane-lock replay was not a safe no-op/);
  assert.match(dbGate, /command receipt persisted a raw lease token/);
  assert.match(dbGate, /session:stolen/);
  assert.match(dbGate, /session:expired-takeover/);
  assert.match(dbGate, /safe:forged:flag/);
  assert.match(dbGate, /safe:stale:source/);
  assert.match(dbGate, /safe:generic:source/);
  assert.match(dbGate, /fresh typed Joe approval did not become current/);
  assert.match(dbGate, /later typed Joe revocation did not supersede approval/);
  assert.match(dbGate, /safe:siep00:relabel/);
  assert.match(dbGate, /terminal authority made a premature success claim/);
});
