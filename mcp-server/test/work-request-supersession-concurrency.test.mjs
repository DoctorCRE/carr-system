import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const proof = resolve(repo, "ops/work-request-supersession-concurrency-proof.mjs");

test("supersession graph races serialize on disposable PostgreSQL 18", { timeout: 180_000 }, () => {
  const run = spawnSync(process.execPath, [proof], {
    cwd: repo, encoding: "utf8", timeout: 170_000,
  });
  assert.equal(run.error, undefined, run.error?.message);
  assert.equal(run.status, 0, `${run.stdout}\n${run.stderr}`);

  const report = JSON.parse(run.stdout);
  assert.equal(report.database, "disposable PostgreSQL 18; synthetic data only");
  assert.equal(report.opposing_supersession.filter((result) => result.ok).length, 1);
  assert.match(report.invalid_state_cross_tenant_fixture, /refused synthetic invalid/);
  assert.equal(report.supersede_vs_successor_decline.filter((result) => result.ok).length, 1);
  assert.equal(report.persisted_graph, "acyclic after each race");
});
