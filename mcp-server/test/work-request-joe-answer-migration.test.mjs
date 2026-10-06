import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const migration = fs.readFileSync(path.join(root, "migrations/0575_answer_needs_joe_work_request.sql"), "utf8");

test("0575 records Joe's answer and makes only the needs_joe-to-triaged transition", () => {
  assert.doesNotMatch(migration, /^(begin|commit);/m, "0339+ migrations run in the runner's single transaction");
  assert.match(migration, /add column if not exists joe_answer_text text/);
  assert.match(migration, /add column if not exists joe_answered_by_actor_id uuid references public\.actor\(id\)/);
  assert.match(migration, /add column if not exists joe_answered_at timestamptz/);
  assert.match(migration, /create table if not exists ops\.work_request_joe_answer_receipt/);
  assert.match(migration, /create or replace function ops\.answer_work_request_for_joe\(/);
  // The CAS guard: state must be exactly needs_joe at exactly the caller's base_version.
  assert.match(migration, /v_work_request\.state is distinct from 'needs_joe'/);
  assert.match(migration, /only the exact current needs_joe Work Request may be answered/);
  // The only state this function ever sets.
  assert.match(migration, /set state = 'triaged'/);
  assert.doesNotMatch(migration, /set state = '(?!triaged)/);
  // The actor is server-derived, never caller-supplied.
  assert.match(migration, /ops\.authority_actor_slug\(\)/);
  assert.match(migration, /authority session user is not an active human actor/);
  // Idempotent replay is receipt-backed, matching the sibling triage/decline/supersede verbs.
  assert.match(migration, /idempotency key already names a different answer to Joe/);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table|alter\s+table\s+ops\.work_request\s+drop/i);
});

test("0575 requires scope_confirmed exactly true and non-empty acceptance_criteria before it will transition", () => {
  // "authorized human decision and evidence recorded; scope and acceptance
  // criteria revalidated" is the full guard text -- this function must
  // enforce both halves, not just the human-decision half.
  assert.match(migration, /p_scope_confirmed boolean/);
  assert.match(migration, /p_scope_confirmed is distinct from true/);
  assert.match(migration, /scope_confirmed must be exactly true/);
  assert.match(migration, /jsonb_typeof\(v_work_request\.acceptance_criteria\) is distinct from 'array'/);
  assert.match(migration, /jsonb_array_length\(v_work_request\.acceptance_criteria\) = 0/);
  assert.match(migration, /acceptance_criteria_missing/);
  // The receipt carries the exact digest of the criteria the human confirmed
  // against, so a later reader can prove which text was revalidated.
  assert.match(migration, /acceptance_criteria_digest text not null check \(acceptance_criteria_digest ~ '\^sha256:\[0-9a-f\]\{64\}\$'\)/);
  assert.match(migration, /v_criteria_digest := 'sha256:' \|\| encode\(public\.digest\(v_work_request\.acceptance_criteria::text, 'sha256'\), 'hex'\)/);
  // evidence_ref travels through, optional, non-empty when present.
  assert.match(migration, /evidence_ref text check \(evidence_ref is null or btrim\(evidence_ref\) <> ''\)/);
  // The receipt's own not-null constraint enforces scope_confirmed at the
  // storage layer too, not only in the function body.
  assert.match(migration, /scope_confirmed boolean not null check \(scope_confirmed\)/);
});

test("0575 grants the door to authority only", () => {
  assert.match(migration, /revoke all on function ops\.answer_work_request_for_joe\(text,integer,text,boolean,text,uuid\) from public, carr_reader, carr_writer, carr_jobs;/);
  assert.match(migration, /grant execute on function ops\.answer_work_request_for_joe\(text,integer,text,boolean,text,uuid\) to carr_authority;/);
  assert.doesNotMatch(migration, /grant execute on function ops\.answer_work_request_for_joe\([^)]*\) to carr_writer/);
  assert.match(migration, /revoke all on table ops\.work_request_joe_answer_receipt from public, carr_reader, carr_writer, carr_jobs, carr_authority;/);
  assert.match(migration, /grant select on table ops\.work_request_joe_answer_receipt to carr_reader;/);
});
