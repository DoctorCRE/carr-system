// GENERATED -- do not hand-edit.
// Source: ops/config/rule-triage.v1.json
// Regenerate with: ./.venv/bin/python ops/sync-core-rule-ids.py
// Drift check (CI): ./.venv/bin/python ops/sync-core-rule-ids.py --check
//
// WR-000019 slice S11 (boot diet). doctrine.js's standing-context verb reads
// this to know which rule short ids the S7 triage classified `home: "core"`
// (20 rules as of that triage), so it can deliver them in FULL TEXT rather
// than a gist -- both in the shadow-mode core_preview measurement and in the
// enforced-mode branch. Cloudflare Workers have no filesystem at request
// time, so this is a checked-in module, never a runtime JSON read.

export const CORE_RULE_TRIAGE_SOURCE = "ops/config/rule-triage.v1.json";
export const CORE_RULE_WORK_REQUEST = "WR-000019";
export const CORE_RULE_TRIAGE_SLICE = "s7";
export const CORE_RULE_COUNT = 20;

export const CORE_RULE_IDS = Object.freeze([
  "0f38532e",
  "14181e60",
  "1fddcffb",
  "24e10ee8",
  "2b889e80",
  "4a53ff82",
  "4f7c348f",
  "58b44ccb",
  "6a4e6283",
  "737a68d6",
  "88e9b5eb",
  "a7784a18",
  "a8c55a47",
  "ab814a26",
  "b42e217e",
  "bc9188b4",
  "c20dc3d5",
  "c53beeaa",
  "c6f69dee",
  "e4d965d9",
]);
