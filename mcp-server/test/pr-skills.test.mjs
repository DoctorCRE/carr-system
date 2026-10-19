import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const checker = fileURLToPath(new URL("../../ops/check-pr-skills.mjs", import.meta.url));
function check(body, overrides = {}) {
  const directory = mkdtempSync(join(tmpdir(), "pr-skills-"));
  try {
    const event = join(directory, "event.json");
    writeFileSync(event, JSON.stringify({ repository: { full_name: "jbookout/carr-system" },
      pull_request: { body, user: { login: "jbookout" },
        head: { ref: "feature/example", repo: { full_name: "jbookout/carr-system" } }, ...overrides } }));
    return spawnSync(process.execPath, [checker], {
      env: { PATH: process.env.PATH, GITHUB_EVENT_PATH: event }, encoding: "utf8",
    });
  } finally { rmSync(directory, { recursive: true, force: true }); }
}

test("a PR with a non-empty Skills used line passes", () => {
  const result = check("## Change\nFix behavior.\n\nSkills used: poteto-mode, tdd\n");
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

for (const [name, body] of [
  ["missing", "A change"], ["null", null], ["blank", "Skills used:  \t"],
  ["newline", "Skills used:\n## How verified\nTests pass"],
  ["comment", "Skills used: <!-- List skills -->"],
  ["hidden", "<!-- Skills used: tdd -->"],
  ["inline mention", "Please add Skills used: tdd"],
]) test(`${name} declarations fail`, () => {
  const result = check(body);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /requires a non-empty Skills used:/);
});
for (const login of ["dependabot[bot]", "renovate[bot]", "renovate"]) {
  test(`${login} dependency PRs are exempt`, () => {
    const result = check(null, { user: { login } });
    assert.equal(result.status, 0, result.stderr);
  });
}
test("the release pipeline schema PR is exempt", () => {
  const result = check("Opened by ops/release-pipeline.py: the release applied production migrations", {
    head: { ref: "release/schema-snapshot-1234abcd", repo: { full_name: "jbookout/carr-system" } },
  });
  assert.equal(result.status, 0, result.stderr);
});

for (const [name, overrides] of [
  ["branch alone", { head: { ref: "release/schema-snapshot-1234abcd", repo: { full_name: "jbookout/carr-system" } } }],
  ["fork", { head: { ref: "release/schema-snapshot-1234abcd", repo: { full_name: "someone/fork" } } }],
  ["untrusted author", { user: { login: "someone" }, head: { ref: "release/schema-snapshot-1234abcd", repo: { full_name: "jbookout/carr-system" } } }],
]) test(`release exemption rejects ${name}`, () => {
  const body = name === "branch alone" ? "A manual PR" : "Opened by ops/release-pipeline.py: synthetic";
  assert.equal(check(body, overrides).status, 1);
});
test("a dependency-looking branch by a person is not exempt", () => {
  assert.equal(check(null, { head: { ref: "dependabot/npm/example" } }).status, 1);
});
test("the validator never evaluates PR text", () => {
  assert.equal(check("Skills used: $(exit 91); `exit 92`\r\n").status, 0);
});

test("the PR workflow checks body edits and the template prompts for skills", () => {
  const workflow = readFileSync(new URL("../../.github/workflows/pr-skills.yml", import.meta.url), "utf8");
  assert.match(workflow, /types: \[opened, edited, synchronize, reopened, ready_for_review\]/);
  assert.match(workflow, /permissions:\n  contents: read/);
  assert.doesNotMatch(workflow, /pull_request_target|secrets\.|contents: write/);
  const template = readFileSync(new URL("../../.github/PULL_REQUEST_TEMPLATE.md", import.meta.url), "utf8");
  assert.match(template, /^Skills used:/m);
});
