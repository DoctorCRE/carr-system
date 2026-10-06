// Static shape of 0732. The behaviour is proved on a real database by
// mcp-server/test/f01-login-bundle-membership-postgres.sql in the migration
// class; this pins the parts a later edit could quietly loosen.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const migration = readFileSync(
  new URL("../../migrations/0732_f01_login_bundle_membership.sql", import.meta.url), "utf8");
const classifier = migration.slice(
  migration.indexOf("create or replace function ops.login_bundle_principal"),
  migration.indexOf("revoke all on function ops.login_bundle_principal"));
const gate = migration.slice(
  migration.indexOf("CREATE OR REPLACE FUNCTION ops.f01_context_actor_slug"),
  migration.indexOf("do $f01_login_bundle_patch$"));

test("the classifier refuses superusers and authority, jobs and exporter members before any bundle", () => {
  const refusals = ["r.rolsuper", "'carr_authority'", "'carr_jobs'", "'carr_exporter'"]
    .map(needle => classifier.indexOf(needle));
  const writer = classifier.indexOf("'carr_writer', 'MEMBER') then 'carr_writer'");
  const reader = classifier.indexOf("'carr_reader', 'MEMBER') then 'carr_reader'");
  assert.ok(refusals.every(at => at > 0), "every refusal is present");
  assert.ok(writer > Math.max(...refusals) && reader > writer, "refusals come first, writer before reader");
  assert.match(classifier, /security invoker/);
  assert.doesNotMatch(classifier, /security definer/i);
});

test("the classifier carries no EXECUTE grant, and the migration grants nothing", () => {
  assert.match(migration, /revoke all on function ops\.login_bundle_principal\(name\) from public/);
  assert.doesNotMatch(migration, /^\s*grant /im);
});

test("the F01 gate keeps authority logins first by name and classifies everyone else by membership", () => {
  const authority = gate.indexOf("IN ('carr_authority_joe', 'carr_authority_dell')");
  const membership = gate.indexOf("ops.login_bundle_principal(session_user)");
  assert.ok(authority > 0 && membership > authority);
  assert.doesNotMatch(gate, /session_user\s*=\s*'carr_(writer|reader)'/);
  assert.match(gate, /RAISE EXCEPTION 'f01_principal_refused: %', session_user USING ERRCODE = '42501'/);
  assert.match(gate, /v_slug !~ '\^\[a-z\]\[a-z0-9-\]\{1,62\}\$'/);
});

test("the four in-place patches each replace exactly one literal bundle comparison and keep authority", () => {
  for (const signature of [
    "ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)",
    "ops.f01_register_derivative_link(jsonb,text,text)",
    "ops.register_execution_environment_provider(jsonb,uuid)",
    "ops.transition_proposed_eval_candidate(text,text,text,jsonb,uuid)",
  ]) assert.ok(migration.includes(`('${signature}',`), signature);
  assert.match(migration, /\/ length\(target\.old_clause\) <> 1/);
  assert.match(migration, /after_proc\.proacl, after_proc\.prosecdef, after_proc\.proconfig/);
});
