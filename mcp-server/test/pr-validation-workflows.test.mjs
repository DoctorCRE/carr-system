import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, writeFileSync, chmodSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import { runInNewContext } from "node:vm";
import test from "node:test";

const yaml = createRequire(import.meta.url)("js-yaml");
const files = ["ci.yml", "db-acceptance.yml"];
const read = name => yaml.load(readFileSync(new URL("../../.github/workflows/" + name, import.meta.url), "utf8"));
// Evaluate the workflows' scalar policy expressions, not a synthetic scheduler.
// Unknown expressions fail closed; these tests make no claims about runner time.
function expression(value, github, success = true, inputs = { shard_trial: false }) {
  if (value === undefined) return success;
  const raw = String(value).replace(/^\$\{\{\s*|\s*\}\}$/g, "");
  assert.match(raw, /^(?:github\.(?:workflow|ref|run_id|event_name|event\.action|event\.pull_request\.number|event\.pull_request\.base\.ref|event\.changes\.base\.ref)|inputs\.shard_trial|always\(\)|'[^']*'|[\s()=!&|]|true|false)+$/, `unsupported expression: ${raw}`);
  const safe = raw.replace(/github(?:\.[a-zA-Z_]+)+/g, path =>
    JSON.stringify(path.split(".").slice(1).reduce((value, key) => value?.[key], github) ?? ""));
  const result = runInNewContext(safe, { inputs, always: () => true }, { timeout: 1000 });
  // Actions implicitly adds success() unless a status function is present.
  return raw.includes("always()") || success ? result : false;
}
function context(event_name = "pull_request", action = "synchronize", number = 9, run_id = 1) {
  return { event_name, run_id, ref: event_name === "pull_request" ? `refs/pull/${number}/merge` : "refs/heads/main",
    event: { action, changes: {}, pull_request: event_name === "pull_request" ? { number, base: { ref: "main" } } : {} } };
}
// Subscription only: DB path filters are intentionally outside this test's scope.
function subscribed(workflow, event) {
  if (!Object.hasOwn(workflow.on, event.event_name)) return false;
  if (event.event_name !== "pull_request") return true;
  return (workflow.on.pull_request?.types ?? ["opened", "synchronize", "reopened"]).includes(event.event.action);
}
function policy(workflow, event, success = true, inputs = { shard_trial: false }) {
  const triggers = subscribed(workflow, event);
  const concurrency = workflow.concurrency;
  const group = concurrency.group.replace(/\$\{\{(.*?)\}\}/g,
    (_, expr) => expression(expr.trim(), { ...event, workflow: workflow.name }));
  const cancel = Boolean(expression(concurrency["cancel-in-progress"], event));
  const jobs = Object.entries(workflow.jobs);
  assert.ok(jobs.length);
  const runnable = triggers ? jobs.filter(([, job]) => expression(job.if, event, success, inputs)).map(([name]) => name) : [];
  return { triggers, group, cancel, runnable, jobs: jobs.map(([name]) => name) };
}
for (const file of files) {
  const workflow = read(file);
  test(`${file}: close is unsubscribed and cannot publish skipped replacement checks`, () => {
    const closed = policy(workflow, context("pull_request", "closed"));
    assert.equal(closed.triggers, false, "close must start no workflow, not a workflow of skipped jobs");
    assert.deepEqual(closed.runnable, []);
    for (const action of ["opened", "synchronize", "reopened"]) {
      const current = policy(workflow, context("pull_request", action));
      assert.equal(current.triggers, true);
      assert.deepEqual(current.runnable, current.jobs.filter(name => name !== "shadow-aggregate"));
    }
  });
  test(`${file}: ready-for-review does not restart identical source`, () => {
    assert.equal(policy(workflow, context("pull_request", "ready_for_review")).triggers, false);
  });
  test(`${file}: cancellation is scoped to the workflow and PR`, () => {
    const current = policy(workflow, context());
    assert.equal(current.cancel, true);
    assert.equal(policy(workflow, context("pull_request", "synchronize", 9, 2)).group, current.group);
    assert.notEqual(policy(workflow, context("pull_request", "opened", 10)).group, current.group);
    assert.notEqual(policy({ ...workflow, name: "Another workflow" }, context()).group, current.group);
    for (const event of ["schedule", "workflow_dispatch"]) {
      const one = policy(workflow, context(event, "", 9, 1)), two = policy(workflow, context(event, "", 9, 2));
      assert.equal(one.triggers, true);
      assert.equal(one.cancel, false);
      assert.notEqual(one.group, current.group);
      assert.notEqual(one.group, two.group, "pending non-PR verdicts must not replace each other");
      assert.deepEqual(one.runnable, one.jobs.filter(name => name !== "shadow-aggregate"));
    }
  });
  test(`${file}: unsubscribed push events never count as validation`, () => {
    const push = policy(workflow, context("push"));
    assert.equal(push.triggers, false);
    assert.deepEqual(push.runnable, []);
  });
}

test("DB shard aggregate is default-off, collects failed-trial diagnostics, and cannot run on PRs", () => {
  const db = read("db-acceptance.yml");
  assert.equal(db.on.workflow_dispatch.inputs.shard_trial.default, false);
  assert.deepEqual(policy(db, context("workflow_dispatch"), true).runnable, ["acceptance"]);
  const enabled = { shard_trial: true };
  assert.deepEqual(policy(db, context("workflow_dispatch"), true, enabled).runnable, ["acceptance", "shadow-aggregate"]);
  assert.deepEqual(policy(db, context("workflow_dispatch"), false, enabled).runnable, ["shadow-aggregate"]);
  assert.deepEqual(policy(db, context(), true, enabled).runnable, ["acceptance"]);
  assert.throws(() => expression("inputs.unknown", context()), /unsupported expression/);
});

test("metadata edits start neither workflow, so no skipped suite replaces the required verdict", () => {
  for (const file of files) {
    const edited = policy(read(file), context("pull_request", "edited"));
    assert.equal(edited.triggers, false, `${file}: an edit must start no workflow`);
    assert.deepEqual(edited.runnable, []);
  }
  assert.ok(read("ci.yml").jobs.classes.strategy.matrix.classes.includes("gates"));
});

test("strict aggregate runs after failed classes and accepts only success", () => {
  const ci = read("ci.yml"), gate = ci.jobs.checks;
  assert.equal(gate.name, "ops/ci.sh --strict", "the required context name must be a literal");
  assert.equal(gate.needs, "classes");
  assert.ok(policy(ci, context(), false).runnable.includes("checks"), "always() must override implicit success()");
  const step = gate.steps.find(step => step.name === "Fail unless every class group succeeded");
  assert.match(step.run, /python3 ops\/ci-evidence.py verdict/);
  const fixture = mkdtempSync(join(tmpdir(), "ci-verdict-test-"));
  try {
    const jobs = ci.jobs.classes.strategy.matrix.classes.map(group => ({
      name: "ops/ci.sh --strict --only " + group, status: "completed", conclusion: "success" }));
    const gh = join(fixture, "gh");
    writeFileSync(gh, "#!/usr/bin/env node\nconsole.log(" + JSON.stringify(JSON.stringify([{ jobs }])) + ");\n");
    chmodSync(gh, 0o755);
    for (const result of ["success", "failure", "cancelled", "skipped", "unknown", ""]) {
      const output = spawnSync("bash", ["-e", "-c", step.run], {
        cwd: new URL("../../", import.meta.url),
        env: { PATH: fixture + ":" + process.env.PATH, CLASSES_RESULT: result,
          GITHUB_REPOSITORY: "jbookout/carr-system", GITHUB_RUN_ID: "1", GITHUB_STEP_SUMMARY: join(fixture,"summary") },
        encoding: "utf8", timeout: 5000 });
      assert.ifError(output.error);
      assert.equal(output.status === 0, result === "success", output.stderr);
    }
  } finally { rmSync(fixture, { recursive: true, force: true }); }
});

test("running Worker canary remains independent from subscribed validation events", () => {
  const canary = read("main-canary.yml");
  assert.equal(canary.concurrency["cancel-in-progress"], false);
  assert.equal(canary.on.pull_request, undefined);
  for (const file of files) for (const event of [context(), context("schedule"), context("workflow_dispatch")]) {
    assert.notEqual(policy(read(file), event).group, canary.concurrency.group);
  }
});
