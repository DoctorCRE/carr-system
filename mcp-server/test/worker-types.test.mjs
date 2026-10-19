import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
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
