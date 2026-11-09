import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import { deriveJobPassports, jobPassportStatusLabel, parseJobPassportReceipt } from "../../dealroom/js/job-passport.ts";

type Reader = typeof deriveJobPassports;
const fixtures = [
  ["codex_desktop.observatory-projection.v1.json", "observatory_projection"],
  ["codex_desktop.execution-envelope.v1.json", "execution_envelope"],
  ["carr-evaluation-kernel.synthetic.v1.json", "evaluation_kernel"],
  ["codex_desktop.spatial-surface.v1.json", "spatial_surface"],
  ["codex_desktop.elapsed-time.telemetry-measurement.v1.json", "telemetry_measurement"],
  ["codex_desktop.billed-cost.telemetry-measurement.v1.json", "telemetry_measurement"],
];
function wire(kind: string, payload: unknown) {
  return {
    kind: "receipt",
    seq: "1",
    body: JSON.stringify({ job_passport: { schema_version: "job-passport-wire.v1", kind, payload } }),
  };
}
function fingerprint(reader: Reader) {
  const results: unknown[] = [];
  for (const pair of fixtures) {
    const [name, kind] = pair;
    assert.ok(name && kind);
    const value: unknown = JSON.parse(
      readFileSync(new URL(`../../control-room/contracts/fixtures/execution-fabric/${name}`, import.meta.url), "utf8"),
    );
    assert.ok(value && typeof value === "object" && !Array.isArray(value));
    const record = Object.fromEntries(Object.entries(value));
    results.push(reader([wire(kind, record)], { now: 123 }));
    for (const key of Object.keys(record)) {
      for (const replacement of [undefined, null, false, 0, "", [], {}]) {
        const mutated = structuredClone(record);
        if (replacement === undefined) delete mutated[key];
        else mutated[key] = replacement;
        try {
          results.push(reader([wire(kind, mutated)], { now: 123 }));
        } catch (error) {
          assert.ok(error instanceof Error);
          results.push({ error: error.name, message: error.message });
        }
      }
    }
  }
  return { count: results.length, sha256: createHash("sha256").update(JSON.stringify(results)).digest("hex") };
}
// Captured from job-passport.js at main 315e2d9a before conversion.
const originalFingerprint = { count: 538, sha256: "148e311367537af8a5ef37a81c03a9d1965ed44946d218c713d1d9623da8bbbe" };

test("TypeScript reader reproduces the original JavaScript characterization", () => {
  assert.deepEqual(fingerprint(deriveJobPassports), originalFingerprint);
});

test("erased browser module reproduces the original JavaScript characterization", async () => {
  const source = readFileSync(new URL("../../dealroom/js/job-passport.ts", import.meta.url), "utf8");
  const javascript = stripTypeScriptTypes(source);
  const browser: unknown = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString("base64")}`);
  assert.ok(
    browser &&
      typeof browser === "object" &&
      "deriveJobPassports" in browser &&
      typeof browser.deriveJobPassports === "function",
  );
  // The runtime export was checked above; its interface is the same erased source.
  assert.deepEqual(fingerprint(browser.deriveJobPassports as Reader), originalFingerprint);
});

test("wrapper refusal and label fallbacks preserve the public interface", () => {
  assert.deepEqual(parseJobPassportReceipt(null), { ok: false, reason: "not_json" });
  assert.deepEqual(parseJobPassportReceipt("{bad"), { ok: false, reason: "malformed_json" });
  assert.deepEqual(parseJobPassportReceipt("{}"), { ok: false, reason: "not_job_passport" });
  assert.deepEqual(parseJobPassportReceipt(wire("unknown", {}).body), { ok: false, reason: "malformed_job_passport" });
  assert.equal(jobPassportStatusLabel("blocked"), "Blocked");
  assert.equal(jobPassportStatusLabel("unrecognized"), "Unknown / partial");
});
