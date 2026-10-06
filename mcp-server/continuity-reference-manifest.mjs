// Shared physical packing for Codex continuity references.  The API remains a
// full logical state; this module only separates refs for bounded persistence.

export const SEMANTIC_STATE_LIMIT = 24_000;
export const REFERENCE_MANIFEST_LIMIT = 128_000;
export const REFERENCE_MANIFEST_VERSION = 1;
export const CONTINUITY_STORAGE_CONTRACT = Object.freeze({
  version: "codex-continuity-storage.v2",
  semantic_state_max_bytes: SEMANTIC_STATE_LIMIT,
  reference_manifest_max_bytes: REFERENCE_MANIFEST_LIMIT,
  recovery_state: "full_logical_state",
});
export const STATE_LIST_FIELDS = [
  "acceptance", "latest_corrections", "constraints", "decisions", "progress",
  "blockers", "hypotheses", "verified_evidence", "artifacts", "pending_operations",
  "receipts",
];
const LIST_FIELD_SET = new Set(STATE_LIST_FIELDS);
const FORBIDDEN_KEYS = new Set(["__proto__", "prototype", "constructor"]);

function plainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}

function safeKeys(value) {
  return plainObject(value) && Object.keys(value).every(key => !FORBIDDEN_KEYS.has(key));
}

// PostgreSQL jsonb::text uses a space after object/array separators. Object key
// order changes no byte total, so preserving insertion order is sufficient here.
export function storedJson(value) {
  if (Array.isArray(value)) return `[${value.map(storedJson).join(", ")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value).map(([key, item]) =>
      `${JSON.stringify(key)}: ${storedJson(item)}`).join(", ")}}`;
  return JSON.stringify(value);
}

export function storedJsonBytes(value) {
  return new TextEncoder().encode(storedJson(value)).byteLength;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function invalid(reason) {
  const error = new Error(reason);
  error.code = "codex_reference_manifest_invalid";
  throw error;
}

function manifestEntry(field, index, refs) {
  return { field, index, refs: clone(refs) };
}

/** Split exact ref arrays out of a validated logical state. */
export function splitReferenceManifest(logicalState) {
  if (!safeKeys(logicalState)) invalid("state_object_invalid");
  const state = clone(logicalState);
  const entries = [];
  for (const field of STATE_LIST_FIELDS) {
    if (state[field] === undefined) continue;
    if (!Array.isArray(state[field])) invalid("state_list_invalid");
    state[field].forEach((item, index) => {
      if (!safeKeys(item)) invalid("state_item_invalid");
      if (Object.hasOwn(item, "refs")) {
        if (!Array.isArray(item.refs)) invalid("state_refs_invalid");
        entries.push(manifestEntry(field, index, item.refs));
        delete item.refs;
      }
    });
  }
  return {
    state,
    reference_manifest: { version: REFERENCE_MANIFEST_VERSION, entries },
    semantic_state_bytes: storedJsonBytes(state),
    reference_manifest_bytes: storedJsonBytes({ version: REFERENCE_MANIFEST_VERSION, entries }),
    reference_count: entries.reduce((total, entry) => total + entry.refs.length, 0),
  };
}

function validateManifest(manifest, state) {
  // Empty is the immutable legacy representation, whose direct refs must stay
  // byte-for-byte as originally written and whose v1 digest must not change.
  if (manifest === undefined || manifest === null ||
      (safeKeys(manifest) && Object.keys(manifest).length === 0)) return [];
  if (!safeKeys(manifest) || Object.keys(manifest).length !== 2 ||
      manifest.version !== REFERENCE_MANIFEST_VERSION || !Array.isArray(manifest.entries))
    invalid("manifest_shape_invalid");
  let priorField = -1;
  let priorIndex = -1;
  const seen = new Set();
  return manifest.entries.map(entry => {
    if (!safeKeys(entry) || Object.keys(entry).length !== 3 ||
        !LIST_FIELD_SET.has(entry.field) || !Number.isSafeInteger(entry.index) || entry.index < 0 ||
        !Array.isArray(entry.refs) || entry.refs.some(ref => typeof ref !== "string" || ref.length > 500))
      invalid("manifest_entry_invalid");
    const list = state[entry.field];
    if (!Array.isArray(list) || entry.index >= list.length || !safeKeys(list[entry.index]) ||
        Object.hasOwn(list[entry.index], "refs")) invalid("manifest_path_invalid");
    const key = `${entry.field}\u0000${entry.index}`;
    if (seen.has(key)) invalid("manifest_duplicate_path");
    seen.add(key);
    const fieldOrder = STATE_LIST_FIELDS.indexOf(entry.field);
    if (fieldOrder < priorField || (fieldOrder === priorField && entry.index <= priorIndex))
      invalid("manifest_order_invalid");
    priorField = fieldOrder;
    priorIndex = entry.index;
    return entry;
  });
}

/** Hydrate physical state only after every manifest invariant has passed. */
export function hydrateReferenceManifest(physicalState, manifest) {
  if (!safeKeys(physicalState)) invalid("physical_state_invalid");
  const state = clone(physicalState);
  const entries = validateManifest(manifest, state);
  for (const entry of entries)
    state[entry.field][entry.index].refs = clone(entry.refs);
  return state;
}

export function referenceManifestSummary(physicalState, manifest) {
  const entries = validateManifest(manifest, physicalState && clone(physicalState));
  const logicalState = hydrateReferenceManifest(physicalState, manifest);
  const planned = splitReferenceManifest(logicalState);
  const manifestPresent = !(manifest === undefined || manifest === null ||
    (safeKeys(manifest) && Object.keys(manifest).length === 0));
  return {
    manifest_present: manifestPresent,
    reference_count: planned.reference_count,
    stored_state_bytes: storedJsonBytes(physicalState),
    stored_reference_manifest_bytes: storedJsonBytes(manifest || {}),
    planned_semantic_state_bytes: planned.semantic_state_bytes,
    planned_reference_manifest_bytes: planned.reference_manifest_bytes,
  };
}
