// Tests pull request validation workflow event and readiness rules.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { runInNewContext } from "node:vm";

type Step = {
  name?: string;
  id?: string;
  run: string;
  if: string;
  env: Record<string, string>;
  with?: { path?: string };
};
type Job = {
  name: string;
  if: string;
  steps: Step[];
  needs: string[];
  env: Record<string, string>;
  strategy: { matrix: { shard: number[]; classes?: string[] } };
  container: { image: string };
  services: { postgres: { image: string; env: Record<string, string> } };
};
type Workflow = {
  name: string;
  on: Record<string, { types?: string[] }>;
  concurrency: { group: string; "cancel-in-progress": string | boolean };
  jobs: Record<string, Job>;
};
type Needs = Record<string, { result: string; outputs?: Record<string, string> }>;
function object(value: unknown): Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const yaml = object(createRequire(import.meta.url)("js-yaml"));
function required<T>(value: T | undefined): T {
  assert.ok(value !== undefined);
  return value;
}

const files = ["doc-drift.yml"];
const PRODUCT_CLASSES = ["artifact", "binding", "gates", "lint", "pushfloor", "secret", "types", "unit"];
function selectorNeeds() {
  return {
    select: {
      result: "success",
      outputs: {
        full: "false",
        classes: JSON.stringify(PRODUCT_CLASSES),
        ...Object.fromEntries(
          ["lint", "fast", "gates", "secret", "unit", "unit-coverage", "launchd-python39"].map((job) => [job, "true"]),
        ),
      },
    },
  };
}
function read(name: string): Workflow {
  assert.ok(typeof yaml.load === "function");
  const value: unknown = yaml.load(readFileSync(new URL(`../../.github/workflows/${name}`, import.meta.url), "utf8"));
  const workflow = object(value);
  assert.equal(typeof workflow.name, "string");
  object(workflow.on);
  if (workflow.concurrency !== undefined) object(workflow.concurrency);
  for (const job of Object.values(object(workflow.jobs))) {
    const fields = object(job);
    if (fields.steps !== undefined) {
      assert.ok(Array.isArray(fields.steps));
      for (const step of fields.steps) object(step);
    }
  }
  // Repository YAML fixtures are checked above for their workflow/job/step containers.
  // Scalar policy values are exercised by the assertions below rather than normalized.
  return workflow as Workflow;
}
// Evaluate the workflows' scalar policy expressions, not a synthetic scheduler.
// Unknown expressions fail closed; these tests make no claims about runner time.
function expression(value: unknown, github: object, success = true, cancelled = false): unknown {
  if (value === undefined) return success && !cancelled;
  const raw = String(value).replace(/^\$\{\{\s*|\s*\}\}$/g, "");
  assert.match(
    raw,
    /^(?:github\.(?:workflow|repository|ref|run_id|event_name|event\.workflow_run\.(?:conclusion|head_branch|event|head_repository\.full_name)|event\.action|event\.pull_request\.number|event\.pull_request\.draft|event\.pull_request\.base\.ref|event\.changes\.base(?:\.ref)?)|always\(\)|cancelled\(\)|'[^']*'|[\s()=!&|]|true|false|null)+$/,
    `unsupported expression: ${raw}`,
  );
  const safe = raw.replace(/github(?:\.[a-zA-Z_]+)+/g, (path) =>
    JSON.stringify(
      path
        .split(".")
        .slice(1)
        .reduce<unknown>((value, key) => (value == null ? undefined : object(value)[key]), github) ?? null,
    ),
  );
  const result = runInNewContext(safe, { always: () => true, cancelled: () => cancelled }, { timeout: 1000 });
  // Actions implicitly adds success() unless a status function is present.
  return raw.includes("always()") || raw.includes("cancelled()") || success ? result : false;
}
function context(
  event_name = "pull_request",
  action = "synchronize",
  number = 9,
  run_id = 1,
): {
  event_name: string;
  run_id: number;
  ref: string;
  event: {
    action: string;
    changes: object;
    pull_request: { number?: number; base?: { ref: string }; draft?: boolean };
  };
} {
  return {
    event_name,
    run_id,
    ref: event_name === "pull_request" ? `refs/pull/${number}/merge` : "refs/heads/main",
    event: {
      action,
      changes: {},
      pull_request: event_name === "pull_request" ? { number, base: { ref: "main" } } : {},
    },
  };
}
test("expression rejects repetitive unknown selector fields within a bounded process", () => {
  const result = spawnSync(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      `
    import assert from "node:assert/strict";
    import { runInNewContext } from "node:vm";
    ${expression.toString()}
    const input = "needs.select.outputs." + "_nullneeds.select.outputs.".repeat(40) + "!";
    assert.throws(() => expression(input, {}), /unsupported expression/);
  `,
    ],
    { encoding: "utf8", timeout: 2000 },
  );
  assert.equal(result.status, 0, result.stderr || String(result.error));
});
// Subscription and path applicability have separate interfaces.
function subscribed(workflow: Workflow, event: ReturnType<typeof context>) {
  if (!Object.hasOwn(workflow.on, event.event_name)) return false;
  if (event.event_name !== "pull_request") return true;
  return (workflow.on.pull_request?.types ?? ["opened", "synchronize", "reopened"]).includes(event.event.action);
}
function policy(workflow: Workflow, event: ReturnType<typeof context>, success = true, cancelled = false) {
  const triggers = subscribed(workflow, event);
  const concurrency = workflow.concurrency;
  const group = concurrency.group.replace(/\$\{\{(.*?)\}\}/g, (_: string, expr: string) =>
    String(expression(expr.trim(), { ...event, workflow: workflow.name })),
  );
  const cancel = Boolean(expression(concurrency["cancel-in-progress"], event));
  const jobs = Object.entries(workflow.jobs);
  assert.ok(jobs.length);
  const runnable = triggers
    ? jobs.filter(([, job]) => expression(job.if, event, success, cancelled)).map(([name]) => name)
    : [];
  return { triggers, group, cancel, runnable, jobs: jobs.map(([name]) => name) };
}
for (const file of files) {
  const workflow = read(file);
  const validationJobs = ["check", "verdict"];
  test(`${file}: close is unsubscribed and cannot publish skipped replacement checks`, () => {
    const closed = policy(workflow, context("pull_request", "closed"));
    assert.equal(closed.triggers, false, "close must start no workflow, not a workflow of skipped jobs");
    assert.deepEqual(closed.runnable, []);
    for (const action of ["opened", "synchronize", "reopened"]) {
      const current = policy(workflow, context("pull_request", action));
      assert.equal(current.triggers, true);
      assert.deepEqual(current.runnable, current.triggers ? validationJobs : []);
    }
  });
  test(`${file}: readiness respects workflow subscriptions and drafts run only the sentinel`, () => {
    const ready = policy(workflow, context("pull_request", "ready_for_review"));
    assert.equal(ready.triggers, true);
    assert.deepEqual(ready.runnable, validationJobs);
    const event = context("pull_request", "converted_to_draft");
    event.event.pull_request.draft = true;
    const draft = policy(workflow, event);
    assert.equal(draft.triggers, true);
    assert.deepEqual(draft.runnable, ["verdict"]);
  });
  test(`${file}: cancellation is scoped to the workflow and PR`, () => {
    const current = policy(workflow, context());
    assert.equal(current.cancel, true);
    for (const action of ["opened", "reopened", "ready_for_review", "converted_to_draft"]) {
      const transition = policy(workflow, context("pull_request", action, 9, 3));
      assert.equal(transition.cancel, true);
      assert.equal(transition.group, current.group);
    }
    assert.equal(policy(workflow, context("pull_request", "synchronize", 9, 2)).group, current.group);
    assert.notEqual(policy(workflow, context("pull_request", "opened", 10)).group, current.group);
    assert.notEqual(policy({ ...workflow, name: "Another workflow" }, context()).group, current.group);
    for (const event of ["schedule", "workflow_dispatch"]) {
      const one = policy(workflow, context(event, "", 9, 1)),
        two = policy(workflow, context(event, "", 9, 2));
      assert.equal(one.triggers, true);
      assert.equal(one.cancel, false);
      assert.notEqual(one.group, current.group);
      assert.notEqual(one.group, two.group, "pending non-PR verdicts must not replace each other");
      assert.deepEqual(one.runnable, validationJobs);
    }
  });
  test(`${file}: unsubscribed push events never count as validation`, () => {
    const push = policy(workflow, context("push"));
    assert.equal(push.triggers, false);
    assert.deepEqual(push.runnable, []);
  });
}

test("PR gates retain two parallel partitions and secret scanning runs independently", () => {
  const ci = read("ci.yml");
  assert.deepEqual(required(ci.jobs.gates).strategy.matrix.shard, [1, 2]);
  const gates = required(required(ci.jobs.gates).steps.find((step) => step.name === "Gates checks"));
  assert.equal(gates.run, `ops/ci.sh --strict --only gates --gate-shard "\${{ matrix.shard }}/2"`);
  const fast = required(required(ci.jobs.fast).steps.find((step) => step.name === "Fast checks"));
  assert.doesNotMatch(fast.run, /\b(?:gates|secret)\b/);
  assert.equal(
    required(required(ci.jobs.secret).steps.find((step) => step.name === "Secret checks")).run,
    "ops/ci.sh --strict --only secret",
  );
  assert.ok(required(ci.jobs.checks).needs.includes("gates") && required(ci.jobs.checks).needs.includes("secret"));
});

test("unit fixtures use the job container's disposable PostgreSQL service endpoint", () => {
  const evaluate = (value: string | undefined, classes: string) =>
    typeof value === "string" && value.startsWith(`\${{`)
      ? runInNewContext(value.slice(3, -2), {
          matrix: { classes },
          contains: (value: string, needle: string) => value.includes(needle),
        })
      : value;
  for (const file of ["ci.yml", "main-canary.yml", "unit-shards.yml"]) {
    const job = required(read(file).jobs[file === "unit-shards.yml" ? "unit" : "classes"]);
    const run = required(
      job.steps.find((step) => ["Run this group's check classes", "Run assigned MCP suites"].includes(step.name ?? "")),
    );
    for (const group of job.strategy.matrix.classes || ["unit"]) {
      const dsn = evaluate(run.env.CARR_CI_DATABASE_URL, group);
      if (group.split(" ").includes("unit")) {
        assert.match(required(evaluate(job.container.image, group)), /@sha256:[0-9a-f]{64}$/);
        assert.match(required(evaluate(job.services.postgres.image, group)), /@sha256:[0-9a-f]{64}$/);
        const endpoint = new URL(required(dsn));
        assert.equal(endpoint.hostname, "postgres");
        assert.equal(endpoint.port, "5432");
        assert.equal(endpoint.pathname, "/carr_ci");
        assert.equal(endpoint.username, job.services.postgres.env.POSTGRES_USER);
        assert.equal(endpoint.password, "");
        assert.equal(endpoint.search + endpoint.hash, "");
        assert.equal(run.env.PGPASSWORD, job.services.postgres.env.POSTGRES_PASSWORD);
        assert.equal(required(job.env.CARR_CI_POSTGRES_IMAGE).includes("both"), true);
      } else {
        assert.equal(dsn, "");
      }
    }
  }
});

test("strict aggregate runs after failed classes and requires complete successful evidence", () => {
  const ci = read("ci.yml"),
    gate = required(ci.jobs.checks);
  assert.deepEqual(gate.needs, [
    "select",
    "lint",
    "fast",
    "gates",
    "secret",
    "unit",
    "unit-coverage",
    "scope",
    "classes",
    "migration-fixtures",
    "launchd-python39",
  ]);
  assert.deepEqual(
    required(ci.jobs["unit-coverage"]).needs,
    ["select", "unit"],
    "coverage must wait for the entire shard matrix",
  );
  const cardinality = spawnSync("python3", ["ci_unit_shards.py", "matrix"], {
    cwd: new URL("../../ops/", import.meta.url),
    encoding: "utf8",
    timeout: 5000,
  });
  assert.equal(cardinality.status, 0, cardinality.stderr);
  const units = read("unit-shards.yml").jobs;
  assert.equal(required(units.unit).strategy.matrix, `\${{ fromJSON(needs.plan.outputs.matrix) }}`);
  assert.match(
    required(required(units.plan).steps.find((step) => step.id === "assign")).run,
    /python3 ops\/ci_unit_shards.py matrix/,
  );
  const parsed: unknown = JSON.parse(cardinality.stdout);
  const admitted = object(parsed).include;
  assert.ok(Array.isArray(admitted));
  assert.deepEqual(
    admitted,
    Array.from({ length: admitted.length }, (_, index) => ({ shard: index + 1, count: admitted.length })),
  );
  assert.ok(admitted.length > 0 && admitted.length <= 16);
  assert.equal(required(units.unit).name, `MCP unit shard \${{ matrix.shard }} of \${{ matrix.count }}`);
  const step = required(gate.steps.find((step) => step.name === "Fail unless every class group succeeded"));
  assert.match(step.run, /python3 ops\/ci-evidence.py verdict/);
  const fixture = mkdtempSync(join(tmpdir(), "ci-verdict-test-"));
  try {
    assert.equal(step.env.CI_NEEDS, `\${{ toJSON(needs) }}`);
    assert.equal(step.env.CI_EVENT, `\${{ github.event_name }}`);
    for (const [event, active] of [
      ["pull_request", ["lint", "launchd-python39", "fast", "gates", "secret", "unit", "unit-coverage"]],
      ["merge_group", ["scope", "classes", "migration-fixtures", "unit-coverage"]],
    ] satisfies [string, string[]][]) {
      const needs: Needs = Object.fromEntries(
        gate.needs.map((name) => [name, { result: active.includes(name) ? "success" : "skipped" }]),
      );
      required(needs.scope).outputs = { full: "true" };
      needs.select = selectorNeeds().select;
      const verdict = (current: Needs) => {
        const output = spawnSync("bash", ["-e", "-c", step.run], {
          cwd: new URL("../../", import.meta.url),
          env: {
            PATH: process.env.PATH,
            CI_DRAFT: "false",
            CI_EVENT: event,
            CI_NEEDS: JSON.stringify(current),
            GITHUB_STEP_SUMMARY: join(fixture, "summary"),
          },
          encoding: "utf8",
          timeout: 5000,
        });
        assert.ifError(output.error);
        return output;
      };
      const passing = verdict(needs);
      assert.equal(passing.status, 0, `${event}: inactive jobs may skip; ${passing.stdout}${passing.stderr}`);
      for (const lane of active)
        for (const state of ["failure", "cancelled", "skipped", "unknown", "", "missing"]) {
          const broken = structuredClone(needs);
          if (state === "missing") delete broken[lane];
          else required(broken[lane]).result = state;
          const output = verdict(broken);
          assert.notEqual(output.status, 0, `${event}: ${lane}=${state} must block success`);
        }
    }
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});

test("running Worker canary remains independent from subscribed validation events", () => {
  const canary = read("main-canary.yml");
  assert.equal(canary.concurrency["cancel-in-progress"], false);
  assert.equal(canary.on.pull_request, undefined);
  for (const file of files)
    for (const event of [context(), context("schedule"), context("workflow_dispatch")]) {
      assert.notEqual(policy(read(file), event).group, canary.concurrency.group);
    }
});

function deployContext(event_name = "workflow_run", run = {}) {
  return {
    event_name,
    repository: "DoctorCRE/carr-system",
    ref: "refs/heads/main",
    event: {
      workflow_run: {
        conclusion: "success",
        head_branch: "main",
        event: "push",
        head_repository: { full_name: "DoctorCRE/carr-system" },
        ...run,
      },
    },
  };
}

test("deploy accepts successful main push and dispatched canaries from this repository", () => {
  const condition = required(read("deploy.yml").jobs.deploy).if;
  for (const event of ["push", "workflow_dispatch"]) {
    assert.equal(expression(condition, deployContext("workflow_run", { event })), true);
  }
  for (const run of [
    { event: "pull_request" },
    { event: "pull_request_target" },
    { event: "merge_group" },
    { event: "schedule" },
    { event: undefined },
    { head_repository: { full_name: "someone/carr-system" } },
    { head_repository: {} },
    { head_repository: null },
    { head_branch: "feature" },
    { conclusion: "failure" },
    { conclusion: "cancelled" },
  ]) {
    assert.equal(expression(condition, deployContext("workflow_run", run)), false, JSON.stringify(run));
  }
});

test("deploy preserves manual dispatch on main and refuses other events and refs", () => {
  const condition = required(read("deploy.yml").jobs.deploy).if;
  const manual = { event_name: "workflow_dispatch", ref: "refs/heads/main", event: {} };
  assert.equal(expression(condition, manual), true);
  assert.equal(expression(condition, { ...manual, ref: "refs/heads/feature" }), false);
  for (const event_name of ["push", "pull_request", "schedule"]) {
    assert.equal(expression(condition, deployContext(event_name)), false);
  }
});

test("PR secret validation installs main's pinned Gitleaks before scanning", () => {
  const steps = required(read("ci.yml").jobs.secret).steps;
  const install = steps.findIndex((step) => step.run === "python3 ops/gitleaks.py --install");
  const scan = steps.findIndex((step) => step.run === "ops/ci.sh --strict --only secret");
  assert.ok(install >= 0 && install < scan);
});

test("MCP shards retain per-program evidence even after failure", () => {
  const steps = required(read("unit-shards.yml").jobs.unit).steps;
  const run = required(steps.find((step) => step.name === "Run assigned MCP suites"));
  assert.equal(run.env.CARR_CI_PROGRAM_RECORDS, `\${{ runner.temp }}/ci-programs.json`);
  const retain = required(steps.find((step) => step.with?.path?.includes("ci-programs.json")));
  assert.equal(retain.if, `\${{ always() }}`);
});

test("Python 3.9 imports block full events and selected PRs", () => {
  const ci = read("ci.yml");
  const job = required(ci.jobs["launchd-python39"]);
  assert.match(job.container.image, /^python:3\.9\.25-slim-bookworm@sha256:[a-f0-9]{64}$/);
  assert.equal(required(job.steps.at(-1)).run, "python -I tools/dictation-rig/tests/test_python39_imports.py");
  const guard = required(
    required(ci.jobs.checks).steps.find((step) => step.name === "Require launchd Python 3.9 imports"),
  );
  for (const event_name of ["pull_request", "merge_group", "schedule"]) {
    for (const result of ["success", "failure", "cancelled", "skipped", undefined]) {
      const blocked = runInNewContext(guard.if.replaceAll("launchd-python39", "imports"), {
        github: { event_name },
        needs: { imports: { result } },
      });
      assert.equal(blocked, event_name !== "pull_request" && result !== "success");
      if (blocked) assert.notEqual(spawnSync("bash", ["-e", "-c", guard.run]).status, 0);
    }
  }
});
