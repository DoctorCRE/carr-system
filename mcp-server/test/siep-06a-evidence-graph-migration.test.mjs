import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const migration = fs.readFileSync(
  new URL("../../migrations/0453_siep06a_evidence_graph.sql", import.meta.url),
  "utf8",
);
const gate = fs.readFileSync(
  new URL("../../ops/siep-evidence-graph-local-pg-gate.py", import.meta.url),
  "utf8",
);

test("0337 is a projection over canonical ledgers, never a shadow graph", () => {
  assert.match(migration, /create or replace function ops\.siep_read_evidence_graph\(p_component text default null\)/);
  assert.match(migration, /language plpgsql stable security definer/);
  assert.match(migration, /set search_path=pg_catalog,ops,public/);
  assert.doesNotMatch(migration, /create table/i);
  assert.doesNotMatch(migration, /\b(?:insert into|update|delete from)\b/i);
  assert.match(migration, /must not create a physical evidence graph/);
});

test("0337 traverses the exact package-bound engineering and decision lineage", () => {
  for (const relation of [
    "ops.siep_evidence_link",
    "ops.siep_job_evidence_binding",
    "ops.job_receipt",
    "ops.job_attempt",
    "ops.engineering_execution_envelope",
    "ops.engineering_slice_plan",
    "ops.engineering_slice_receipt",
    "ops.engineering_reviewer_fact",
    "public.event",
  ]) {
    assert.match(migration, new RegExp(relation.replaceAll(".", "\\.")));
  }
  assert.match(migration, /er\.job_attempt_id=ja\.id/);
  assert.match(migration, /rf\.receipt_id=er\.id/);
  assert.match(migration, /rf\.reviewer_actor_id<>er\.executor_actor_id/);
  assert.match(migration, /j\.payload->>'manifest_digest'=b\.manifest_digest/);
  assert.match(migration, /de\.actor_id=e\.linked_actor_id/);
  assert.match(migration, /e\.attested_session_principal='carr_authority_joe'/);
  assert.match(migration, /ops\.siep_current_evidence_digest/);
  assert.match(migration, /ops\.siep_current_approval/);
});

test("0337 returns deterministic redacted graph material and reasoned currentness", () => {
  assert.match(migration, /siep-evidence-graph\.v1/);
  assert.match(migration, /graph_digest/);
  assert.match(migration, /edge_digest/);
  assert.match(migration, /node_digest/);
  assert.match(migration, /siep_evidence_node_digest\(to_jsonb\(env\)\)/);
  assert.match(migration, /join graph_package g on g\.package_key=d\.package_key/);
  assert.match(migration, /order by node_key/);
  assert.match(migration, /order by source_key,relation,target_key,basis_ref/);
  for (const reason of [
    "missing_source",
    "digest_mismatch",
    "manifest_mismatch",
    "stale_work_request_version",
    "observed_at_mismatch",
    "future_source",
    "purpose_or_lineage_mismatch",
    "superseded_authority",
  ]) assert.match(migration, new RegExp(reason));
  assert.match(migration, /session_independence','deferred_to_siep_03/);
  assert.match(migration, /terminal_authority','siep_06b/);
  assert.doesNotMatch(migration, /'lease_token'/);
  assert.doesNotMatch(migration, /'session_ref'/);
  assert.doesNotMatch(migration, /'payload'/);
  assert.doesNotMatch(migration, /'receipt_ref'/);
  assert.match(migration, /bound_evidence_kind/);
  assert.match(migration, /status_pass/);
  assert.match(migration, /operation_matches/);
  assert.match(migration, /current_coverage/);
  assert.match(migration, /bool_or\(a\.is_current\)/);
  assert.match(migration, /count\(distinct a\.audit_cycle\)/);
  assert.match(migration, /immutable_integrity_valid/);
  assert.match(migration, /structural_invalid_link_count/);
});

test("0337 exposes read authority only to existing runtime bundles", () => {
  assert.match(migration, /revoke all on function ops\.siep_read_evidence_graph\(text\)\s+from public,carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.match(migration, /grant execute on function ops\.siep_read_evidence_graph\(text\)\s+to carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.match(migration, /revoke all on function ops\.siep_evidence_node_digest\(jsonb\)/);
  assert.doesNotMatch(migration, /grant (?:insert|update|delete|all) on/i);
});

test("rollback-only evidence graph gate covers aliases, redaction, lineage, and integrity", () => {
  assert.match(gate, /^# ci: db-gate/m);
  assert.match(gate, /SCAC-00/);
  assert.match(gate, /known SIEP package or component alias/);
  assert.match(gate, /graph digest was not deterministic/);
  assert.match(gate, /graph leaked forbidden material/);
  assert.match(gate, /exact engineering lineage was not projected/);
  assert.match(gate, /source digest mutation was not surfaced/);
  assert.match(gate, /later Joe revocation was not surfaced/);
});
