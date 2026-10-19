import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(fileURLToPath(new URL("../", import.meta.url)));

function generate(schema) {
  const script = `import { registerHooks } from "node:module";
const generator = new URL("./generate-verb-types.mjs", import.meta.url).href;
const fixture = ${JSON.stringify({ "unsupported-verb": { inputSchema: schema } })};
registerHooks({ resolve(specifier, context, next) {
  if (context.parentURL === generator && specifier === "./src/tools.js") {
    return { url: "data:text/javascript," + encodeURIComponent("export const TOOLS=" + JSON.stringify(fixture)), shortCircuit: true };
  }
  return next(specifier, context);
}});
await import(generator);`;
  return spawnSync(process.execPath, ["--input-type=module", "-"], {
    cwd: root, input: script, encoding: "utf8",
  });
}

test("generation rejects unsupported nested schema keywords", async (t) => {
  for (const [keyword, value] of Object.entries({
    not: { enum: ["forbidden"] }, if: { const: "conditional" }, then: { const: "consequence" },
    else: { const: "alternative" }, patternProperties: { "^x": { type: "string" } },
    dependencies: { value: ["other"] }, $ref: "https://example.invalid/schema", tsType: "any",
  })) {
    await t.test(keyword, () => {
      const schema = { type: "object", properties: { value: { type: "string", [keyword]: value } } };
      const result = generate(schema);
      assert.notEqual(result.status, 0, `${keyword} must stop generation`);
      assert.match(result.stderr, /unsupported-verb.*properties.value/);
      assert.ok(result.stderr.includes(keyword), result.stderr);
    });
  }
});

test("keyword rejection reaches every schema-bearing position", async (t) => {
  const unsupported = { type: "string", not: { const: "forbidden" } };
  for (const [location, schema] of [
    ["unsupported-verb", unsupported],
    ["unsupported-verb.items", { type: "array", items: unsupported }],
    ["unsupported-verb.additionalProperties", { type: "object", additionalProperties: unsupported }],
    ["unsupported-verb.oneOf[0]", { oneOf: [unsupported, { type: "null" }] }],
    ["unsupported-verb.anyOf[0]", { anyOf: [unsupported, { type: "null" }] }],
  ]) {
    await t.test(location, () => {
      const result = generate(schema);
      assert.notEqual(result.status, 0, location);
      assert.ok(result.stderr.includes(`${location}: unsupported schema keyword not`), result.stderr);
    });
  }
});

test("generation rejects ambiguous oneOf and constrained empty-object mappings", async (t) => {
  for (const [name, schema] of [
    ["overlapping alternatives", { oneOf: [{ type: "string" }, { type: "string", enum: ["x"] }] }],
    ["overlapping numeric types", { oneOf: [{ type: "integer" }, { type: "number" }] }],
    ["untyped alternative", { oneOf: [{ enum: ["x"] }, { type: "string" }] }],
    ["required undeclared field", { type: "object", additionalProperties: false, required: ["missing"] }],
    ["constrained empty object", { type: "object", additionalProperties: false, const: { unexpected: true } }],
  ]) {
    await t.test(name, () => {
      const result = generate(schema);
      assert.notEqual(result.status, 0, `${name} must stop generation`);
      assert.match(result.stderr, /unsupported-verb: unsupported/);
    });
  }
});

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

test("closed empty verb requests reject extra fields and non-objects through CI", async (t) => {
  const dir = mkdtempSync(path.join(root, "type-tests/request-"));
  const source = path.join(dir, "request.ts");
  const request = (value) => `import type { VerbInputs } from "../../.generated/verb-inputs.js";
const request: VerbInputs["pending-slice-completion-proposals"] = ${value};\n`;
  try {
    for (const [name, value] of [["extra field", '{ unexpected: "wrong" }'], ["non-object", '"wrong"']]) {
      await t.test(name, () => {
        writeFileSync(source, request(value));
        const ci = spawnSync("bash", ["ops/ci.sh", "--only", "types"], {
          encoding: "utf8", cwd: path.dirname(root),
        });
        assert.equal(ci.status, 1, ci.stdout + ci.stderr);
        assert.match(ci.stdout + ci.stderr, /TS2322/);
        assert.match(ci.stdout + ci.stderr, /Worker verb contract types failed/);
      });
    }
    writeFileSync(source, request("{}"));
    const valid = spawnSync("npm", ["run", "typecheck"], { encoding: "utf8", cwd: root });
    assert.equal(valid.status, 0, valid.stdout + valid.stderr);
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
