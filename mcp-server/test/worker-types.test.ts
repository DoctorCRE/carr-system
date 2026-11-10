// Tests generation and validation of Worker verb request types.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { appendFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(fileURLToPath(new URL("../", import.meta.url)));
const cacheRoot = mkdtempSync(path.join(tmpdir(), "worker-types-"));
const previousTmp = process.env.TMPDIR;
process.env.TMPDIR = cacheRoot;
after(() => {
  if (previousTmp === undefined) delete process.env.TMPDIR;
  else process.env.TMPDIR = previousTmp;
  rmSync(cacheRoot, { recursive: true, force: true });
});

before(() => {
  const generated = spawnSync("npm", ["run", "generate:types"], { encoding: "utf8", cwd: root });
  assert.equal(generated.status, 0, generated.stdout + generated.stderr);
});

function typecheck() {
  return spawnSync(path.join(root, "node_modules/.bin/tsc"), ["--project", "tsconfig.json"], {
    encoding: "utf8",
    cwd: root,
  });
}

function generate(schema: unknown) {
  return generateModule({ TOOLS: { "unsupported-verb": { inputSchema: schema } } });
}

function generateModule(module: unknown) {
  const script = `import { registerHooks } from "node:module";
const generator = new URL("./generate-verb-types.ts", import.meta.url).href;
const fixture = ${JSON.stringify(module)};
registerHooks({ resolve(specifier, context, next) {
  if (context.parentURL === generator && specifier === "./src/tools.js") {
    return { url: "data:text/javascript," + encodeURIComponent("export const TOOLS=" + JSON.stringify(fixture.TOOLS)), shortCircuit: true };
  }
  return next(specifier, context);
}});
await import(generator);`;
  return spawnSync(process.execPath, ["--input-type=module", "-"], {
    cwd: root,
    input: script,
    encoding: "utf8",
  });
}

test("compiler result contract cannot contradict the real producer declaration", () => {
  const source = path.join(root, ".generator-contract-mutant.mts");
  const generator = readFileSync(path.join(root, "generate-verb-types.ts"), "utf8")
    .replace("): Promise<string>", "): Promise<number>")
    .replace("output), declarations);", "output), declarations.toFixed());");
  try {
    writeFileSync(source, generator);
    const result = spawnSync(
      path.join(root, "node_modules/.bin/tsc"),
      [
        "--ignoreConfig",
        "--noEmit",
        "--strict",
        "--noUncheckedIndexedAccess",
        "--exactOptionalPropertyTypes",
        "--module",
        "nodenext",
        "--target",
        "esnext",
        "--types",
        "node",
        source,
      ],
      { cwd: root, encoding: "utf8" },
    );
    assert.notEqual(result.status, 0, "a contradictory compiler result must fail strict checking");
    assert.match(result.stdout + result.stderr, /TS(?:2339|2551).*toFixed.*string/);
  } finally {
    rmSync(source, { force: true });
  }
});

test("generation refuses mismatched legacy module and schema contracts at load", async (t) => {
  for (const [name, module] of [
    ["TOOLS array", { TOOLS: [] }],
    ["tool scalar", { TOOLS: { broken: 42 } }],
    ["schema scalar", { TOOLS: { broken: { inputSchema: "object" } } }],
    ["description type", { TOOLS: { broken: { inputSchema: { type: "object", description: 42 } } } }],
    ["nested type", { TOOLS: { broken: { inputSchema: { properties: { child: { type: 42 } } } } } }],
    ["required type", { TOOLS: { broken: { inputSchema: { required: [42] } } } }],
    ["properties array", { TOOLS: { broken: { inputSchema: { properties: [] } } } }],
    ["items scalar", { TOOLS: { broken: { inputSchema: { items: 42 } } } }],
    ["additionalProperties scalar", { TOOLS: { broken: { inputSchema: { additionalProperties: 42 } } } }],
    ["union scalar", { TOOLS: { broken: { inputSchema: { anyOf: 42 } } } }],
    ["enum scalar", { TOOLS: { broken: { inputSchema: { enum: 42 } } } }],
    ["minimum string", { TOOLS: { broken: { inputSchema: { minimum: "42" } } } }],
    ["uniqueItems number", { TOOLS: { broken: { inputSchema: { uniqueItems: 42 } } } }],
  ] as const) {
    await t.test(name, () => {
      const result = generateModule(module);
      assert.notEqual(result.status, 0, name);
      assert.match(result.stderr, /invalid schema contract/);
    });
  }
});

test("legacy Durable Object constructors do not add a runtime brand property", async (t) => {
  for (const [file, className] of [
    ["ai-budget.js", "AiNeuronBudget"],
    ["verb-request-budget.js", "VerbRequestBudget"],
    ["phone-audio-handoff.js", "PhoneAudioHandoff"],
  ]) {
    await t.test(className, () => {
      const result = spawnSync(process.execPath, ["--input-type=module", "-"], {
        cwd: root,
        encoding: "utf8",
        input: `import assert from "node:assert/strict";
import { ${className} } from "./src/${file}";
const instance = new ${className}({ storage: {} }, {});
assert.equal(Object.hasOwn(instance, "__DURABLE_OBJECT_BRAND"), false);
`,
      });
      assert.equal(result.status, 0, result.stdout + result.stderr);
    });
  }
});

test("Worker declarations contain no suppressions and preserve Durable Object bindings", () => {
  const output = readFileSync(path.join(root, ".generated/worker-configuration.d.ts"), "utf8");
  assert.doesNotMatch(output, /@ts-(?:expect-error|ignore|nocheck)/);
  for (const [binding, className] of [
    ["AI_NEURON_BUDGET", "AiNeuronBudget"],
    ["PHONE_AUDIO_HANDOFF", "PhoneAudioHandoff"],
    ["VERB_REQUEST_BUDGET", "VerbRequestBudget"],
    ["OAUTH_CONSENT_STATE", "OAuthConsentState"],
  ]) {
    const declaration = `${binding}: DurableObjectNamespace<import("../src/index").${className}>;`;
    assert.equal(output.split(declaration).length - 1, 2, declaration);
  }
});

test("Worker generation is byte-identical across repeated runs", () => {
  const file = path.join(root, ".generated/worker-configuration.d.ts");
  const initial = readFileSync(file);
  for (let run = 0; run < 2; run++) {
    const result = spawnSync("npm", ["exec", "--", "node", "generate-worker-types.ts"], {
      cwd: root,
      encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.deepEqual(readFileSync(file), initial);
  }
});

test("canonical generation preserves the generated bytes across repeated runs", () => {
  const output = path.join(root, ".generated/verb-inputs.d.ts");
  const baseline = readFileSync(output);
  for (let attempt = 0; attempt < 2; attempt++) {
    const result = spawnSync(process.execPath, ["generate-verb-types.ts"], { cwd: root, encoding: "utf8" });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.deepEqual(readFileSync(output), baseline);
  }
});

test("generation refuses unsupported prefixItems instead of widening tuple input", () => {
  const result = generate({ type: "array", prefixItems: [{ type: "string" }] });
  assert.notEqual(result.status, 0, "unsupported tuple keyword must stop generation");
  assert.match(result.stderr, /unsupported-verb: unsupported schema keyword prefixItems/);
});

test("generation rejects unsupported nested schema keywords", async (t) => {
  for (const [keyword, value] of [
    ["not", { enum: ["forbidden"] }],
    ["if", { const: "conditional" }],
    ["then", { const: "consequence" }],
    ["else", { const: "alternative" }],
    ["patternProperties", { "^x": { type: "string" } }],
    ["dependencies", { value: ["other"] }],
    ["$ref", "https://example.invalid/schema"],
    ["tsType", "any"],
  ] as const) {
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
  ] as const) {
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
  ] as const) {
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
  const check = typecheck;
  const request = (page: string) => `import type { VerbInputs } from "../../.generated/verb-inputs.js";
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
      encoding: "utf8",
      cwd: path.dirname(root),
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

test("closed empty verb requests reject extra fields and non-objects", async (t) => {
  const dir = mkdtempSync(path.join(root, "type-tests/request-"));
  const source = path.join(dir, "request.ts");
  const request = (value: string) => `import type { VerbInputs } from "../../.generated/verb-inputs.js";
const request: VerbInputs["list-my-codex-sessions"] = ${value};\n`;
  try {
    for (const [name, value] of [
      ["extra field", '{ unexpected: "wrong" }'],
      ["non-object", '"wrong"'],
    ] as const) {
      await t.test(name, () => {
        writeFileSync(source, request(value));
        const ci = typecheck();
        assert.equal(ci.status, 1, ci.stdout + ci.stderr);
        assert.match(ci.stdout + ci.stderr, /TS2322/);
      });
    }
    writeFileSync(source, request("{}"));
    const valid = typecheck();
    assert.equal(valid.status, 0, valid.stdout + valid.stderr);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the converted Worker module rejects a wrong binding name and type", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/env-"));
  const source = path.join(dir, "bindings.ts");
  const check = typecheck;
  const valid = `import { workspaceCommandCenterPosture } from "../../src/workspace-feature-flag.ts";
declare const env: Parameters<typeof workspaceCommandCenterPosture>[0];
workspaceCommandCenterPosture({ WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true" });
declare const bindings: Env;
const kv: KVNamespace = bindings.OAUTH_KV;
`;
  try {
    writeFileSync(source, valid);
    const accepted = check();
    assert.equal(accepted.status, 0, accepted.stdout + accepted.stderr);
    writeFileSync(
      source,
      valid +
        `env?.WORKSPACE_COMMAND_CENTER_READ_ENABLE;
const wrong: number = env?.WORKSPACE_COMMAND_CENTER_READ_ENABLED;
bindings.OAUTH_KVV;
const wrongBinding: number = bindings.OAUTH_KV;
`,
    );
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

test("typechecking rejects invalid Worker declarations without declaration exceptions", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/declarations-"));
  try {
    writeFileSync(path.join(dir, "broken.d.ts"), "interface ReviewBrokenEnv extends Env { OAUTH_KV: number; }\n");
    const result = typecheck();
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stdout + result.stderr, /TS2430.*ReviewBrokenEnv/s);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("typecheck regenerates Worker declarations without a stale cached Env augmentation", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/stale-"));
  const check = () => spawnSync("npm", ["run", "typecheck"], { encoding: "utf8", cwd: root });
  try {
    const valid = check();
    assert.equal(valid.status, 0, valid.stdout + valid.stderr);
    appendFileSync(
      path.join(root, ".generated/worker-configuration.d.ts"),
      "\ninterface Env { STALE_REVIEW_ONLY: string; }\n",
    );
    writeFileSync(
      path.join(dir, "stale.ts"),
      "declare const env: Env;\nconst stale: string = env.STALE_REVIEW_ONLY;\n",
    );
    const stale = check();
    assert.equal(stale.status, 1, stale.stdout + stale.stderr);
    assert.match(stale.stdout + stale.stderr, /TS2339.*STALE_REVIEW_ONLY/s);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the converted Leads reader checks its generated request contract", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/leads-"));
  const source = path.join(dir, "request.ts");
  const request = (lead: string) => `import { readLeadWorkspace } from "../../src/lead-workspace.ts";
import type { Db } from "../../src/verb-contract.ts";
declare const db: Db;
readLeadWorkspace(db, { workspace: "leads", lead_id: ${lead} });\n`;
  const check = typecheck;
  try {
    writeFileSync(source, request('"00000000-0000-4000-8000-000000000001"'));
    const accepted = check();
    assert.equal(accepted.status, 0, accepted.stdout + accepted.stderr);
    writeFileSync(source, request("42"));
    const rejected = check();
    assert.equal(rejected.status, 1, rejected.stdout + rejected.stderr);
    assert.match(rejected.stdout + rejected.stderr, /TS2322.*number.*string/s);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("converted work and cutover handlers check generated request contracts", () => {
  const dir = mkdtempSync(path.join(root, "type-tests/cutover-"));
  const source = path.join(dir, "request.ts");
  const request = (version: string, stage: string) => `import { workShapeTools } from "../../src/work-shape.ts";
import { workflowCutoverTools } from "../../src/workflow-cutover.v5.ts";
import type { Db, Actor } from "../../src/verb-contract.ts";
declare const db: Db, actor: Actor;
declare const shape: ReturnType<typeof workShapeTools>;
declare const cutover: ReturnType<typeof workflowCutoverTools>;
shape["set-work-shape-disposition"].handler(db, actor, {
  idempotency_key: "synthetic", work_request: "WR-TEST", base_version: ${version},
  disposition: "required", rationale: "Synthetic contract fixture",
});
cutover["advance-workflow-cutover-stage"].handler(db, actor, {
  idempotency_key: "synthetic", plan_id: "synthetic", reason: "Synthetic contract fixture",
  to_stage: ${stage},
});
`;
  const check = typecheck;
  try {
    writeFileSync(source, request("1", '"build_projection"'));
    const accepted = check();
    assert.equal(accepted.status, 0, accepted.stdout + accepted.stderr);
    writeFileSync(source, request('"wrong"', '"not_a_stage"'));
    const rejected = check();
    assert.equal(rejected.status, 1, "invalid work and cutover arguments must fail compilation");
    assert.match(rejected.stdout + rejected.stderr, /TS2322.*string.*number/s);
    assert.match(rejected.stdout + rejected.stderr, /TS2322.*not_a_stage/s);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
