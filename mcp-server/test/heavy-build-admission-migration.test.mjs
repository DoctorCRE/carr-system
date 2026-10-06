import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const migration = fs.readFileSync(path.join(REPO, "migrations/0320_heavy_build_admission.sql"), "utf8");

test("heavy-build tier is server-derived from the current Work Request and proposed plan", () => {
  assert.match(migration, /classify_sourced_work_request_build\s*\(/i);
  assert.match(migration, /acceptance_criteria[\s\S]+max_steps[\s\S]+dependency_refs/i);
  assert.match(migration, /shape_disposition[\s\S]+work_shape_revision/i);
});

test("research, master-plan, and review receipts are append-only and hash-bound", () => {
  assert.match(migration, /create table ops\.heavy_build_admission_revision/i);
  assert.match(migration, /create table ops\.heavy_build_plan_review/i);
  assert.match(migration, /heavy_build_admission_rows_immutable/i);
  assert.match(migration, /admission_hash[\s\S]+review_hash/i);
  assert.match(migration, /builder_session_ref[\s\S]+reviewer_session_ref/i);
});

test("the database refuses heavy ready-plan acceptance without a fresh passing review", () => {
  assert.match(migration, /heavy_build_ready_plan_gate/i);
  assert.match(migration, /old\.state\s*=\s*'triaged'[\s\S]+new\.state\s*=\s*'ready'/i);
  assert.match(migration, /heavy build plan requires a typed research and master-plan admission receipt/i);
  assert.match(migration, /heavy build plan requires a fresh passing independent review/i);
  assert.match(migration, /verdict\s*=\s*'pass'/i);
});

test("direct table writes remain unavailable to runtime roles", () => {
  assert.match(migration, /revoke all on table ops\.heavy_build_admission_revision,[\s\S]+ops\.heavy_build_plan_review[\s\S]+from public,carr_reader,carr_writer,carr_jobs,carr_authority/i);
  assert.match(migration, /grant execute on function ops\.record_sourced_heavy_build_admission[\s\S]+to carr_writer/i);
  assert.match(migration, /grant execute on function ops\.review_sourced_heavy_build_plan[\s\S]+to carr_writer/i);
});
