import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const MIGRATION_URL = new URL("../../migrations/0719_doctorcre_a03_review_store.sql", import.meta.url);

const TABLES = ["review_case", "review_participant", "finding_set", "review_round", "adjudication", "case_outcome"];

test("0719 installs six append-only authorities behind SECURITY DEFINER functions", async () => {
  const sql = await readFile(MIGRATION_URL, "utf8");
  for (const table of TABLES)
    assert.match(sql, new RegExp(`create table ops\\.v5_a03_${table} \\(`));
  assert.match(sql, /create function ops\.v5_a03_rows_immutable\(\)/);
  for (const table of TABLES) {
    assert.match(sql, new RegExp(`create trigger v5_a03_${table}_immutable`));
    assert.match(sql, new RegExp(`create trigger v5_a03_${table}_truncate_immutable`));
  }
  for (const fn of ["open_review_case", "record_participant", "record_finding_set", "seal_review_round",
    "record_adjudication", "read_review_case"])
    assert.match(sql, new RegExp(`create function ops\\.v5_a03_${fn}\\(`));
  assert.equal((sql.match(/security definer/g) ?? []).length >= 6, true);
});

test("identity authority is server-derived and opposing actor or session reuse is refused", async () => {
  const sql = await readFile(MIGRATION_URL, "utf8");
  assert.match(sql, /p_actor_id uuid/);
  assert.match(sql, /references public\.actor\(id\)/);
  assert.match(sql, /v5_a03_participant_actor_current/);
  assert.match(sql, /existing\.actor_id=p_actor_id or existing\.session_ref=p_session_ref/);
  assert.match(sql, /v5_a03_reviewer_requires_fresh_context/);
  assert.doesNotMatch(sql, /p_maker_actor_id|p_reviewer_actor_id|p_adjudicator_actor_id/);
});

test("finding seal proves all dimensions, whole-set scope, one batch and non-weakened regression", async () => {
  const sql = await readFile(MIGRATION_URL, "utf8");
  assert.match(sql, /v5_a03_review_dimensions\(\)/);
  assert.match(sql, /v_dimension_count<>cardinality\(ops\.v5_a03_review_dimensions\(\)\)/);
  assert.match(sql, /p_reviewed_set_digest is distinct from v_case\.delivered_set_digest/);
  assert.match(sql, /v5_a03_review_not_bound_to_repaired_artifact/);
  assert.match(sql, /v_repaired is distinct from p_repaired_finding_refs/);
  assert.match(sql, /v_prior_checks <@ p_checks_executed/);
  assert.match(sql, /v5_a03_test_weakening/);
});

test("round-2 drift is recorded on the round and routes to adjudication; every path records an outcome", async () => {
  const sql = await readFile(MIGRATION_URL, "utf8");
  assert.match(sql, /array\['circular_reversion','repeated_finding','reviewer_instability'\]/);
  for (const detection of ["repeated_finding", "circular_reversion", "reviewer_instability"])
    assert.match(sql, new RegExp(`v_detections:=v_detections\\|\\|'${detection}'::text`));
  assert.doesNotMatch(sql, /raise exception 'v5_a03_(repeated_finding|circular_reversion|reviewer_instability)'/);
  assert.match(sql, /prior_set\.reviewed_set_digest=current_set\.reviewed_set_digest/);
  assert.match(sql, /'pass','clean_round'/);
  assert.match(sql, /'stronger_adjudication',null,made/);
});

test("the two-round bound is per change: one case per change_ref and per delivered set", async () => {
  const sql = await readFile(MIGRATION_URL, "utf8");
  assert.match(sql, /constraint v5_a03_review_case_one_per_change unique\(tenant,change_ref\)/);
  assert.match(sql, /constraint v5_a03_review_case_one_per_delivered_set unique\(tenant,delivered_set_digest\)/);
  assert.match(sql, /v5_a03_change_already_under_review/);
  assert.match(sql, /v5_a03_delivered_set_already_under_review/);
  assert.match(sql, /v5_a03_artifact_bound_to_other_case/);
});

test("round and adjudication guards make a third loop structurally impossible", async () => {
  const sql = await readFile(MIGRATION_URL, "utf8");
  assert.match(sql, /p_round_ordinal>2/);
  assert.match(sql, /v_round_count<>2/);
  assert.match(sql, /v5_a03_review_round_limit_exhausted/);
  assert.match(sql, /v5_a03_review_round_reopened_after_adjudication/);
  assert.match(sql, /p_outcome not in \('pass','fail','quarantine'\)/);
  assert.match(sql, /v5_a03_adjudicator_is_a_party/);
  assert.match(sql, /constraint v5_a03_adjudication_one unique \(case_id\)/);
});

test("runtime roles receive functions only, never direct table mutation", async () => {
  const sql = await readFile(MIGRATION_URL, "utf8");
  assert.doesNotMatch(sql, /grant\s+(insert|update|delete|truncate|all)\s+on\s+(table\s+)?ops\.v5_a03_/i);
  assert.match(sql, /grant execute on function ops\.v5_a03_open_review_case/);
  assert.match(sql, /grant execute on function ops\.v5_a03_read_review_case/);
});
