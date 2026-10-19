import { test } from "node:test";
import assert from "node:assert/strict";
import { appendFileSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(fileURLToPath(new URL("../", import.meta.url)));

test("generated verb requests accept valid fields and reject a wrong field type", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/request-"));
  const source = path.join(dir, "request.ts");
  const check = () => spawnSync("npm", ["run", "typecheck"], { encoding: "utf8", cwd: root });
  const request = (page) => `import type { VerbInputs } from "../../.generated/verb-inputs.js";
const request: VerbInputs["standing-context"] = { detail: "boot", page: ${page} };
`;
  try {
    writeFileSync(source, request("1"));
    const valid = check();
    assert.equal(valid.status, 0, valid.stdout + valid.stderr);
    writeFileSync(source, request('"wrong"'));
    const invalid = check();
    assert.notEqual(invalid.status, 0, "a string page must fail before deployment");
    assert.match(invalid.stdout + invalid.stderr, /TS2322.*string.*number/s);
    const ci = spawnSync("bash", ["ops/ci.sh", "--only", "types"], {
      encoding: "utf8", cwd: path.dirname(root),
    });
    assert.equal(ci.status, 1, ci.stdout + ci.stderr);
    assert.match(ci.stdout + ci.stderr, /Worker verb contract types failed/);
    writeFileSync(source, request("1"));
    const restored = check();
    assert.equal(restored.status, 0, restored.stdout + restored.stderr);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the converted Worker module rejects a wrong binding name and type in CI", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/env-"));
  const source = path.join(dir, "bindings.ts");
  const check = () => spawnSync("bash", ["ops/ci.sh", "--only", "types"], {
    encoding: "utf8", cwd: path.dirname(root),
  });
  const valid = `import { workspaceCommandCenterPosture } from "../../src/workspace-feature-flag.js";
declare const env: Parameters<typeof workspaceCommandCenterPosture>[0];
workspaceCommandCenterPosture({ WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true" });
declare const bindings: Env;
const kv: KVNamespace = bindings.OAUTH_KV;
`;
  try {
    writeFileSync(source, valid);
    const accepted = check();
    assert.equal(accepted.status, 0, accepted.stdout + accepted.stderr);
    writeFileSync(source, valid + `env?.WORKSPACE_COMMAND_CENTER_READ_ENABLE;
const wrong: number = env?.WORKSPACE_COMMAND_CENTER_READ_ENABLED;
bindings.OAUTH_KVV;
const wrongBinding: number = bindings.OAUTH_KV;
`);
    const rejected = check();
    assert.equal(rejected.status, 1, rejected.stdout + rejected.stderr);
    assert.match(rejected.stdout + rejected.stderr, /TS2551.*WORKSPACE_COMMAND_CENTER_READ_ENABLE/s);
    assert.match(rejected.stdout + rejected.stderr, /TS2322.*string.*number/s);
    assert.match(rejected.stdout + rejected.stderr, /TS2551.*OAUTH_KVV/s);
    assert.match(rejected.stdout + rejected.stderr, /TS2322.*KVNamespace.*number/s);
    writeFileSync(source, valid);
    const restored = check();
    assert.equal(restored.status, 0, restored.stdout + restored.stderr);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("CI rejects invalid Worker declarations outside the legacy Durable Object exception", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/declarations-"));
  try {
    writeFileSync(path.join(dir, "broken.d.ts"),
      "interface ReviewBrokenEnv extends Env { OAUTH_KV: number; }\n");
    const result = spawnSync("bash", ["ops/ci.sh", "--only", "types"], {
      encoding: "utf8", cwd: path.dirname(root),
    });
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stdout + result.stderr, /TS2430.*ReviewBrokenEnv/s);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("CI regenerates Worker declarations without a stale cached Env augmentation", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/stale-"));
  const check = () => spawnSync("bash", ["ops/ci.sh", "--only", "types"], {
    encoding: "utf8", cwd: path.dirname(root),
  });
  try {
    const valid = check();
    assert.equal(valid.status, 0, valid.stdout + valid.stderr);
    appendFileSync(path.join(root, ".generated/worker-configuration.d.ts"),
      "\ninterface Env { STALE_REVIEW_ONLY: string; }\n");
    writeFileSync(path.join(dir, "stale.ts"),
      "declare const env: Env;\nconst stale: string = env.STALE_REVIEW_ONLY;\n");
    const stale = check();
    assert.equal(stale.status, 1, stale.stdout + stale.stderr);
    assert.match(stale.stdout + stale.stderr, /TS2339.*STALE_REVIEW_ONLY/s);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
