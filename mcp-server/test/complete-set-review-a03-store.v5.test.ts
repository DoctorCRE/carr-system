// Tests retired complete-set review verbs are unavailable through MCP.
import assert from "node:assert/strict";
import test from "node:test";

function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}

// The legacy registry is parsed at its runtime seam until its own conversion.
const registry = object(await import(new URL("../src/tools.js", import.meta.url).href));
const TOOLS = object(registry.TOOLS);
const executeRegisteredTool = registry.executeRegisteredTool;

test("retired record-layer verbs are unavailable through MCP dispatch", async () => {
  for (const name of [
    "accept-benchmark-manifest-draft",
    "advance-journey-one-clock",
    "assign-execution-route",
    "attest-attempt-evaluation",
    "attest-capability-project",
    "attest-execution-environment-conformance",
    "begin-capability-project",
    "issue-execution-envelope",
    "open-complete-set-review",
    "prepare-capability-project",
    "produce-assurance-fabric-preactivation-receipt",
    "produce-foundation-assurance-benchmark-coverage",
    "produce-foundation-control-plane-preactivation-receipt",
    "produce-global-execution-contract-receipt",
    "produce-global-no-phi-boundary-receipt",
    "produce-global-prompt-injection-boundary-receipt",
    "produce-global-secrets-boundary-receipt",
    "produce-global-source-authority-receipt",
    "propose-benchmark-manifest-draft",
    "propose-evaluation-case",
    "read-complete-set-review",
    "record-assurance-health-evidence",
    "record-attempt-receipt",
    "record-complete-set-adjudication",
    "record-complete-set-finding-set",
    "record-complete-set-participant",
    "record-foundation-assurance-minimum-outcome",
    "record-gate-zero-read-only-outcome",
    "record-model-role-revision",
    "register-execution-environment-provider",
    "review-benchmark-manifest-draft",
    "seal-complete-set-review-round",
    "set-current-model-role-revision",
    "start-capability-project",
    "transition-evaluation-case",
    "transition-execution-environment-provider",
  ]) {
    assert.equal(Object.hasOwn(TOOLS, name), false, name);
    assert.ok(typeof executeRegisteredTool === "function");
    await assert.rejects(
      executeRegisteredTool({}, { slug: "joe" }, name, {}),
      (error) => object(object(error).payload).error === "unknown_tool",
    );
  }
});
