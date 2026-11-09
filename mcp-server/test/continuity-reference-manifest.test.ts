// Tests continuity reference manifest encoding and validation.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import test from "node:test";
import {
  CONTINUITY_STORAGE_CONTRACT,
  hydrateReferenceManifest,
  REFERENCE_MANIFEST_LIMIT,
  REFERENCE_MANIFEST_VERSION,
  referenceManifestSummary,
  SEMANTIC_STATE_LIMIT,
  STATE_LIST_FIELDS,
  splitReferenceManifest,
  storedJson,
  storedJsonBytes,
} from "../continuity-reference-manifest.ts";

function stateWithRefs(refs: string[]) {
  return {
    objective: "retain a complete logical checkpoint",
    latest_corrections: [{ text: "current correction", refs }],
    progress: [{ text: "x".repeat(3567) }, { text: "x".repeat(3567) }, { text: "x".repeat(3566) }],
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
  assert.equal(packed.reference_manifest.entries[0]?.field, "acceptance");
  assert.equal(packed.reference_manifest.entries[1]?.field, "latest_corrections");
  assert.equal(packed.reference_manifest.entries[2]?.field, "progress");
});

test("v12-sized semantic state with 1000 exact refs fits independent budgets", () => {
  const refs = Array.from(
    { length: 1000 },
    (_unused, index) => `ref:${String(index).padStart(4, "0")}:${"x".repeat(90)}`,
  );
  const logical = stateWithRefs(refs);
  const packed = splitReferenceManifest(logical);
  assert.ok(packed.semantic_state_bytes <= SEMANTIC_STATE_LIMIT);
  assert.ok(packed.reference_manifest_bytes <= REFERENCE_MANIFEST_LIMIT);
  assert.equal(packed.reference_count, 1000);
  assert.deepEqual(hydrateReferenceManifest(packed.state, packed.reference_manifest), logical);
});

function legalStateAt(bytes: number) {
  const logical = {
    objective: "x".repeat(4000),
    next_action: "continue",
    constraints: Array.from({ length: 6 }, (_value, index) => ({ text: index < 5 ? "x".repeat(3900) : "" })),
  };
  assert.ok(logical.constraints[5]);
  logical.constraints[5].text = "x".repeat(bytes - storedJsonBytes(logical));
  assert.ok(logical.constraints[5].text.length <= 4000);
  return logical;
}

test("stored JSON byte boundaries use PostgreSQL separator spaces", () => {
  const logical = legalStateAt(SEMANTIC_STATE_LIMIT);
  assert.equal(storedJsonBytes(logical), SEMANTIC_STATE_LIMIT);
  assert.equal(splitReferenceManifest(logical).semantic_state_bytes, SEMANTIC_STATE_LIMIT);
  assert.ok(logical.constraints[5]);
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
  assert.ok(corrupt.entries[0]);
  corrupt.entries.push(structuredClone(corrupt.entries[0]));
  assert.throws(
    () => hydrateReferenceManifest(packed.state, corrupt),
    (error) => error instanceof Error && "code" in error && error.code === "codex_reference_manifest_invalid",
  );
  const prototypePath = JSON.parse(JSON.stringify(packed.reference_manifest));
  prototypePath.entries[0].field = "__proto__";
  assert.throws(
    () => hydrateReferenceManifest(packed.state, prototypePath),
    (error) => error instanceof Error && "code" in error && error.code === "codex_reference_manifest_invalid",
  );

  const legacy = referenceManifestSummary(logical, {});
  assert.equal(legacy.manifest_present, false);
  assert.equal(legacy.reference_count, 2);
  assert.equal(legacy.stored_state_bytes, storedJsonBytes(logical));
  assert.equal(legacy.planned_semantic_state_bytes, packed.semantic_state_bytes);
  assert.equal(legacy.planned_reference_manifest_bytes, packed.reference_manifest_bytes);
});

test("local v12 capture packs losslessly when supplied without committing it", {
  skip: !fs.existsSync("/tmp/codex-capacity-live-v12.json"),
}, () => {
  const logical = JSON.parse(fs.readFileSync("/tmp/codex-capacity-live-v12.json", "utf8"));
  const packed = splitReferenceManifest(logical);
  assert.deepEqual(hydrateReferenceManifest(packed.state, packed.reference_manifest), logical);
  assert.ok(packed.semantic_state_bytes <= SEMANTIC_STATE_LIMIT);
  assert.ok(packed.reference_manifest_bytes <= REFERENCE_MANIFEST_LIMIT);
});

test("manifest refuses invalid shape, paths and ordering before hydration", () => {
  const state = { acceptance: [{ text: "one" }, { text: "two" }], progress: [{ text: "three" }] };
  const entry = (field: string, index: number, refs: unknown[] = ["ref"]) => ({ field, index, refs });
  const manifests = [
    { version: 2, entries: [] },
    { version: 1, entries: [entry("acceptance", 2)] },
    { version: 1, entries: [entry("acceptance", 0, [42])] },
    { version: 1, entries: [entry("progress", 0), entry("acceptance", 0)] },
    { version: 1, entries: [entry("acceptance", 1), entry("acceptance", 0)] },
  ];
  for (const manifest of manifests) {
    assert.throws(
      () => hydrateReferenceManifest(state, manifest),
      (error) => error instanceof Error && "code" in error && error.code === "codex_reference_manifest_invalid",
    );
    assert.deepEqual(state, { acceptance: [{ text: "one" }, { text: "two" }], progress: [{ text: "three" }] });
  }
});

test("all-field Unicode packing preserves the characterized bytes and digests", () => {
  const logical = {
    objective: "Unicode 🩺 café",
    metadata: { nested: [true, null, 42] },
    ...Object.fromEntries(
      STATE_LIST_FIELDS.map((field, index) => [
        field,
        [{ text: `item ${index}`, refs: ["", "same", "same", "資料"] }, { text: "no refs" }],
      ]),
    ),
    next_action: "continue",
  };
  const packed = splitReferenceManifest(logical);
  const digest = (value: unknown) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
  assert.equal(packed.semantic_state_bytes, 748);
  assert.equal(packed.reference_manifest_bytes, 890);
  assert.equal(digest(packed), "7b9358a1d913893e6fc406232a65396e038231dde5b1e00001d76132df7b37a3");
  assert.equal(digest(hydrateReferenceManifest(packed.state, packed.reference_manifest)), digest(logical));
  assert.equal(digest(logical), "5363f87a9e767ba8358c0e2a7505c5f6dc4d1b763cda1b05939e88f46ba1e8b4");
});

test("storage contract and PostgreSQL JSON representation stay versioned", () => {
  assert.equal(REFERENCE_MANIFEST_VERSION, 1);
  assert.deepEqual(CONTINUITY_STORAGE_CONTRACT, {
    version: "codex-continuity-storage.v2",
    semantic_state_max_bytes: 24_000,
    reference_manifest_max_bytes: 128_000,
    recovery_state: "full_logical_state",
  });
  assert.ok(Object.isFrozen(CONTINUITY_STORAGE_CONTRACT));
  assert.equal(storedJson({ refs: ["one", "two"], empty: {} }), '{"refs": ["one", "two"], "empty": {}}');
});
