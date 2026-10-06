import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import {
  REFERENCE_MANIFEST_LIMIT, SEMANTIC_STATE_LIMIT, hydrateReferenceManifest,
  referenceManifestSummary, splitReferenceManifest, storedJsonBytes,
} from "../continuity-reference-manifest.mjs";

function stateWithRefs(refs) {
  return {
    objective: "retain a complete logical checkpoint",
    latest_corrections: [{ text: "current correction", refs }],
    progress: [{ text: "x".repeat(3567) }, { text: "x".repeat(3567) },
      { text: "x".repeat(3566) }],
    next_action: "read the complete logical checkpoint",
  };
}

test("physical manifest preserves duplicate, empty, ordered refs exactly", () => {
  const logical = {
    objective: "preserve references",
    acceptance: [{ text: "optional references", refs: ["", "duplicate", "duplicate"] }],
    latest_corrections: [{ text: "required references", refs: ["approval:current", "approval:current"] }],
    progress: [{ text: "empty list remains an explicit list", refs: [] }],
    next_action: "verify exact readback",
  };
  const packed = splitReferenceManifest(logical);
  assert.deepEqual(hydrateReferenceManifest(packed.state, packed.reference_manifest), logical);
  assert.equal(packed.reference_count, 5);
  assert.equal(packed.reference_manifest.entries[0].field, "acceptance");
  assert.equal(packed.reference_manifest.entries[1].field, "latest_corrections");
  assert.equal(packed.reference_manifest.entries[2].field, "progress");
});

test("v12-sized semantic state with 1000 exact refs fits independent budgets", () => {
  const refs = Array.from({ length: 1000 }, (_unused, index) =>
    `ref:${String(index).padStart(4, "0")}:${"x".repeat(90)}`);
  const logical = stateWithRefs(refs);
  const packed = splitReferenceManifest(logical);
  assert.ok(packed.semantic_state_bytes <= SEMANTIC_STATE_LIMIT);
  assert.ok(packed.reference_manifest_bytes <= REFERENCE_MANIFEST_LIMIT);
  assert.equal(packed.reference_count, 1000);
  assert.deepEqual(hydrateReferenceManifest(packed.state, packed.reference_manifest), logical);
});

function legalStateAt(bytes) {
  const logical = {
    objective: "x".repeat(4000), next_action: "continue",
    constraints: Array.from({ length: 6 }, (_value, index) => ({ text: index < 5 ? "x".repeat(3900) : "" })),
  };
  logical.constraints[5].text = "x".repeat(bytes - storedJsonBytes(logical));
  assert.ok(logical.constraints[5].text.length <= 4000);
  return logical;
}

test("stored JSON byte boundaries use PostgreSQL separator spaces", () => {
  const logical = legalStateAt(SEMANTIC_STATE_LIMIT);
  assert.equal(storedJsonBytes(logical), SEMANTIC_STATE_LIMIT);
  assert.equal(splitReferenceManifest(logical).semantic_state_bytes, SEMANTIC_STATE_LIMIT);
  logical.constraints[5].text += "x";
  assert.equal(splitReferenceManifest(logical).semantic_state_bytes, SEMANTIC_STATE_LIMIT + 1);

  const refs = Array.from({ length: 253 }, () => "r".repeat(500));
  const base = splitReferenceManifest(stateWithRefs([...refs, "r"]));
  refs.push("r".repeat(REFERENCE_MANIFEST_LIMIT - base.reference_manifest_bytes + 1));
  assert.equal(splitReferenceManifest(stateWithRefs(refs)).reference_manifest_bytes, REFERENCE_MANIFEST_LIMIT);
  refs[refs.length - 1] += "r";
  assert.equal(splitReferenceManifest(stateWithRefs(refs)).reference_manifest_bytes, REFERENCE_MANIFEST_LIMIT + 1);
});

test("corrupt manifest paths fail closed and legacy summary retains actual storage", () => {
  const logical = stateWithRefs(["legacy:one", "legacy:two"]);
  const packed = splitReferenceManifest(logical);
  const corrupt = structuredClone(packed.reference_manifest);
  corrupt.entries.push(structuredClone(corrupt.entries[0]));
  assert.throws(() => hydrateReferenceManifest(packed.state, corrupt),
    error => error?.code === "codex_reference_manifest_invalid");
  const prototypePath = JSON.parse(JSON.stringify(packed.reference_manifest));
  prototypePath.entries[0].field = "__proto__";
  assert.throws(() => hydrateReferenceManifest(packed.state, prototypePath),
    error => error?.code === "codex_reference_manifest_invalid");

  const legacy = referenceManifestSummary(logical, {});
  assert.equal(legacy.manifest_present, false);
  assert.equal(legacy.reference_count, 2);
  assert.equal(legacy.stored_state_bytes, storedJsonBytes(logical));
  assert.equal(legacy.planned_semantic_state_bytes, packed.semantic_state_bytes);
  assert.equal(legacy.planned_reference_manifest_bytes, packed.reference_manifest_bytes);
});

test("local v12 capture packs losslessly when supplied without committing it", { skip: !fs.existsSync("/tmp/codex-capacity-live-v12.json") }, () => {
  const logical = JSON.parse(fs.readFileSync("/tmp/codex-capacity-live-v12.json", "utf8"));
  const packed = splitReferenceManifest(logical);
  assert.deepEqual(hydrateReferenceManifest(packed.state, packed.reference_manifest), logical);
  assert.ok(packed.semantic_state_bytes <= SEMANTIC_STATE_LIMIT);
  assert.ok(packed.reference_manifest_bytes <= REFERENCE_MANIFEST_LIMIT);
});
