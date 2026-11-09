import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const patchName = "@apidevtools+json-schema-ref-parser+11.9.3.patch";
const patch = readFileSync(path.join(root, "patches", patchName), "utf8");
const oldLine = patch.split("\n").find((line) => line.startsWith("-export declare const getNewOptions"));
const newLine = patch.split("\n").find((line) => line.startsWith("+export declare const getNewOptions"));
assert.ok(oldLine && newLine);
const original = readFileSync(
  path.join(root, "node_modules/@apidevtools/json-schema-ref-parser/dist/lib/options.d.ts"),
  "utf8",
).replace(newLine.slice(1), oldLine.slice(1));
const packageJson = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));

for (const [scenario, version, declaration, ci] of [
  ["version drift locally", "11.9.4", original, false],
  ["version drift in CI", "11.9.4", original, true],
  ["unappliable patch locally", "11.9.3", "export declare const changed: string;\n", false],
] as const) {
  test(`postinstall fails on ${scenario}`, () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), "patch-install-"));
    try {
      const dependency = path.join(scratch, "node_modules/@apidevtools/json-schema-ref-parser");
      mkdirSync(path.join(dependency, "dist/lib"), { recursive: true });
      mkdirSync(path.join(scratch, "node_modules/.bin"), { recursive: true });
      mkdirSync(path.join(scratch, "patches"));
      symlinkSync(
        path.join(root, "node_modules/patch-package/index.js"),
        path.join(scratch, "node_modules/.bin/patch-package"),
      );
      cpSync(path.join(root, "patches", patchName), path.join(scratch, "patches", patchName));
      writeFileSync(
        path.join(scratch, "package.json"),
        JSON.stringify({
          name: "patch-install-probe",
          version: "1.0.0",
          scripts: { postinstall: packageJson.scripts.postinstall },
        }),
      );
      writeFileSync(
        path.join(dependency, "package.json"),
        JSON.stringify({ name: "@apidevtools/json-schema-ref-parser", version }),
      );
      writeFileSync(path.join(dependency, "dist/lib/options.d.ts"), declaration);
      const env = { ...process.env, NODE_ENV: "development", CI: ci ? "true" : "" };
      const result = spawnSync("npm", ["run", "postinstall"], { cwd: scratch, env, encoding: "utf8" });
      assert.equal(result.status, 1, result.stdout + result.stderr);
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });
}
