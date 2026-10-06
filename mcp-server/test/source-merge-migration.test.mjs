import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const migration = fs.readFileSync(path.join(ROOT,
  "migrations/0470_source_merge_authority_projection.sql"), "utf8");
const workflow = fs.readFileSync(path.join(ROOT,
  ".github/workflows/source-merge-controller.yml"), "utf8");

test("canonical migration runner owns the transaction boundary", () => {
  assert.doesNotMatch(migration, /^(?:begin|commit|rollback)(?:\s+transaction)?;\s*$/im);
});

test("accepted plan hash owns exact source-merge paths", () => {
  assert.match(migration, /create table ops\.source_merge_plan_scope/i);
  assert.match(migration, /after insert on ops\.sourced_work_request_plan_acceptance_receipt/i);
  assert.match(migration, /p\.caps->'source_merge'/i);
  assert.match(migration, /source_merge_scope_valid\(p_caps->'source_merge'\)/i);
  assert.match(migration, /authorized_paths/i);
  assert.match(migration, /authorized_paths jsonb not null/i);
  assert.doesNotMatch(migration.slice(0, migration.indexOf("create or replace function ops.source_merge_authority_projection")),
    /canonical_ownership_(claim|path_valid)/i);
  assert.match(migration, /'mode','file',[\s\S]{0,80}'operation','write'/i);
  assert.match(migration, /caps is already part of the canonical[\s\S]+plan preimage/i);
});

test("reader sees only the narrow security-definer projection", () => {
  assert.match(migration,
    /function ops\.source_merge_authority_projection\([\s\S]+security definer/i);
  assert.match(migration,
    /source_merge_authority_projection\([\s\S]+returns jsonb language plpgsql stable security definer/i);
  assert.match(migration,
    /revoke all on ops\.source_merge_plan_scope from public,carr_reader,carr_writer,carr_jobs,carr_authority/i);
  assert.match(migration,
    /grant execute on function ops\.source_merge_authority_projection\(uuid,text,text,integer\)\s+to carr_reader/i);
  assert.doesNotMatch(migration, /grant\s+(select|insert|update|delete)\s+on\s+ops\.(assurance|canonical_ownership|source_merge)/i);
  assert.doesNotMatch(migration, /current_setting\('carr\.organization_tenant_id'/i);
  assert.match(migration, /tenant constant text:='carr-internal'/i);
  assert.match(migration, /order by e\.recorded_at desc,e\.id desc/i);
});

test("projection fails closed on current work, current generation, and manual QA", () => {
  assert.match(migration, /w\.state is distinct from 'ready' or w\.blocker_code is not null/i);
  assert.match(migration, /manual_qa_required' is distinct from 'false'/i);
  assert.match(migration, /source_merge_candidate_not_unique/i);
  assert.match(migration, /candidate_receipt\.receipt#>>'\{source_evidence,source_sha\}'=p_head_sha/i);
  assert.match(migration, /candidate_successor\.id is null/i);
});

test("dark assurance and ownership rows are projected without expanding accepted scope", () => {
  const projection = migration.slice(migration.indexOf("create or replace function ops.source_merge_authority_projection"));
  assert.match(projection, /from ops\.assurance_evidence_extension evidence/i);
  assert.match(projection, /join ops\.canonical_ownership_claim claim/i);
  assert.match(projection, /from jsonb_array_elements_text\(scope\.authorized_paths\) accepted\(path\)/i);
  assert.match(projection, /jsonb_array_length\(lease_path_claims\)<>jsonb_array_length\(scope\.authorized_paths\)/i);
  assert.doesNotMatch(migration, /grant\s+select\s+on\s+ops\.(assurance|canonical_ownership)/i);
  assert.match(projection, /source_merge_plan_scope/);
});

test("automatic invoker runs trusted protected-main code with narrowly named credentials", () => {
  assert.match(workflow, /workflow_run:[\s\S]+workflows:\s*\[CI\]/i);
  assert.match(workflow, /schedule:[\s\S]+cron:\s*"\*\/15 \* \* \* \*"/i);
  assert.match(workflow, /uses:\s*actions\/checkout@[0-9a-f]{40}[\s\S]+ref:\s*main[\s\S]+persist-credentials:\s*false/i);
  assert.match(workflow, /CARR_SOURCE_MERGE_READER_TOKEN:\s*\$\{\{ secrets\.CARR_SOURCE_MERGE_READER_TOKEN \}\}/);
  assert.match(workflow, /run:\s*node mcp-server\/bin\/run-source-merge\.mjs auto/i);
  assert.doesNotMatch(workflow, /^\s{2}pull_request:/m);
});
