import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const script = new URL("../check-party-names.ts", import.meta.url);

function check(input: string) {
  return spawnSync(process.execPath, [script.pathname], { input, encoding: "utf8", timeout: 30_000 });
}

test("name checker preserves batch order and uses the registered validator without database access", () => {
  const result = check(JSON.stringify(["Robin Vale", "R. Vale", "José Álvarez", "", "Jane", "李明"]));
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "[true,false,true,false,false,true]");
  assert.equal(result.stderr, "");
});

test("name checker returns an empty batch and maps falsy names to refusals", () => {
  assert.equal(check("[]").stdout, "[]");
  const result = check('[null,false,0,""]');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "[false,false,false,false]");
});

test("name checker fails closed on malformed input", () => {
  for (const input of ["{", "null", "{}"]) {
    const result = check(input);
    assert.notEqual(result.status, 0, input);
    assert.equal(result.stdout, "", input);
  }
});

test("name checker leaves non-string coercion to the registered validator", () => {
  const result = check('[42,{},true,[],["Robin Vale"]]');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "[false,true,false,false,true]");
});

test("name checker preserves every canonical full-name fixture", () => {
  const cases: unknown = JSON.parse(readFileSync(new URL("./fixtures/full-name-cases.json", import.meta.url), "utf8"));
  assert.ok(cases !== null && typeof cases === "object" && "accept" in cases && "refuse" in cases);
  assert.ok(Array.isArray(cases.accept) && Array.isArray(cases.refuse));
  const result = check(JSON.stringify([...cases.accept, ...cases.refuse]));
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, JSON.stringify([...cases.accept.map(() => true), ...cases.refuse.map(() => false)]));
});

test("name checker preserves string iteration for legacy input", () => {
  const result = check('"AB"');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "[false,false]");
});

test("name checker hides unexpected registry failures and publishes no partial results", () => {
  const hook = `import { registerHooks } from "node:module";
    registerHooks({ load(url, context, nextLoad) {
      if (url.endsWith("/src/tools.js")) return { format: "module", shortCircuit: true,
        source: 'export async function executeRegisteredTool() { throw new Error("synthetic private diagnostic"); }' };
      return nextLoad(url, context);
    } });`;
  const result = spawnSync(
    process.execPath,
    ["--import", `data:text/javascript,${encodeURIComponent(hook)}`, script.pathname],
    {
      input: '["Robin Vale"]',
      encoding: "utf8",
      timeout: 30_000,
    },
  );
  assert.notEqual(result.status, 0);
  assert.equal(result.stdout, "");
  assert.match(result.stderr, /name validation failed/);
  assert.doesNotMatch(result.stderr, /synthetic private diagnostic/);
});
