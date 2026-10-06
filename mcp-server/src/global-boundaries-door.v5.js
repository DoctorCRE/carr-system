// DoctorCRE v5 slice V5-S01, the LIVE DOOR for the global boundaries.
//
// global-boundaries.v5.js (#929) encodes the eight settled decisions as a pure,
// versioned policy. Until this file, nothing evaluated that policy against a
// live dispatch: forty-odd v5 modules import its constants, and exactly one
// registered verb (set-current-model-role-revision) asks its authority
// evaluator a question from inside its own handler. This module is the seam
// between a live verb dispatch and that policy, and nothing more.
//
// WHAT THE DOOR DOES, per dispatch, before the handler runs:
//   * actor authority (Q003/Q020/Q141) for the closed set of verbs whose
//     database functions ALREADY refuse a non-Joe authority session. The door
//     agrees with enforcement that exists; it does not invent a new one.
//   * privacy (Q033) over argument FIELD NAMES at any depth. Values are never
//     pattern-matched, so "patient volume" written in a note is not PHI here.
//   * representation scope (Q073/Q092): exposure or activation of a landlord or
//     seller representation refuses; the two structural fields the schema
//     already carries (record-counter.side, add-premises also_listing_side)
//     are evaluated as structural values and allowed.
//   * read continuity (Q007): a mutation evaluated under any connectivity but
//     "online" refuses. The connectivity comes from the SERVER's door context,
//     never from a caller field; the cloud door is online by construction.
//
// WHAT IT DOES NOT DO. It opens no connection, reads no clock, sends nothing.
// The verdict is a pure function of (verb, write flag, actor, args, door
// context, mode). The caller supplies `now`.
//
// SHADOW FIRST, AND THE FLAG IS A CONSTANT. V5_BOUNDARY_DOOR_MODE is "shadow":
// the verdict is computed and recorded, and nothing is refused. Enforcement on
// live partner authorization waits for Joe's go-ahead, so the flip to
// "enforce" is a reviewed source change, not an environment variable or a row
// a session could write. In shadow the door can never make a dispatch fail: an
// internal error is recorded as a verdict, not thrown.
//
// THE DOOR ONLY EVER ADDS A REFUSAL. An "allow" here authorizes nothing; every
// gate after it (humanOnly, authorityOnly, the authority database session, the
// handler) still runs and still refuses on its own terms.

import {
  V5_ACTIONS,
  V5_BOUNDARY_SCHEMA_VERSION,
  V5_CANONICAL_AUTHORITY,
  V5_CONNECTIVITY_STATES,
  V5_DECISION_SUBSET_CANONICAL_SHA256,
  V5_DOCUMENTED_FALLBACK,
  V5_LOCAL_CAPABILITIES,
  V5_LOCAL_NODE_STATES,
  V5_NO_EFFECTS,
  V5_OPTIONAL_LOCAL_NODES,
  V5_REPRESENTATION_SIDES,
  V5_SCOPE_INTENTS,
  V5_SETTLED_DECISIONS,
  V5_SETTLED_DECISION_IDS,
  V5BoundaryError,
  evaluateActorAuthority,
  evaluateLocalPlatform,
  evaluatePrivacyBoundary,
  evaluateReadContinuity,
  evaluateRepresentationScope,
  v5BoundaryPolicyDigest,
  v5BoundaryProjection,
  V5_ACTION_KEYS,
  V5_OPERATION_KINDS,
} from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID, authorizationClassForActor } from "./identity.js";
import { partnerAuthoritySlugForActor } from "./partner-authority.js";

export const V5_BOUNDARY_DOOR_SCHEMA_VERSION = "doctorcre-v5-global-boundaries-door.v1";

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

// ---------------------------------------------------------------------------
// The mode flag.
// ---------------------------------------------------------------------------

export const V5_BOUNDARY_DOOR_MODES = deepFreeze(["shadow", "enforce"]);
/**
 * THE FLAG. "shadow" until Joe says otherwise. Changing this line is the whole
 * of the enforcement switch, which is why it is a constant and not config.
 */
export const V5_BOUNDARY_DOOR_MODE = "shadow";

function assertMode(mode) {
  if (!V5_BOUNDARY_DOOR_MODES.includes(mode)) {
    fail("unknown_door_mode", `"${mode}" is not a registered boundary-door mode`,
      { mode, registered: [...V5_BOUNDARY_DOOR_MODES] });
  }
  return mode;
}

// ---------------------------------------------------------------------------
// The cloud door's own context. Online by construction: a request that reached
// the deployed Worker reached the canonical authority. A future door that can
// run while the cloud is unreachable must pass its real connectivity, and then
// every mutation it carries refuses.
// ---------------------------------------------------------------------------

export const V5_CLOUD_DOOR_CONTEXT = deepFreeze({
  door: "cloud_dispatch_seam",
  connectivity: "online",
});

const CONTEXT_KEYS = Object.freeze(["door", "connectivity", "now"]);

// ---------------------------------------------------------------------------
// Unauthorized-admin: the verbs the door evaluates as retained system
// authority. CLOSED, and each row names the enforcement that already refuses a
// non-Joe principal, so the door's matrix can be checked against it.
//
// DELIBERATELY ABSENT, with the reason, so nobody adds them by analogy:
//   amend-rule            only an ACTIVE rule's statement is Joe-guarded
//                         (ops.amend_rule_statement); amending a PROPOSED rule
//                         needs no authority principal. The door cannot see
//                         rule status without a database read, so mapping it
//                         would refuse Dell's legitimate proposed-rule edits.
//   accept-workflow shadow  either admitted partner may accept a shadow run.
//   activate-/deactivate-guidance-registry, decide-guidance-import-batch
//                         guarded by the registry's accountable human, who is
//                         not necessarily Joe.
//   developer / release_admin actions   no live verb is that action today.
// ---------------------------------------------------------------------------

export const V5_DOOR_SYSTEM_AUTHORITY_VERBS = deepFreeze({
  "approve-rule": {
    action: "system.policy", when: null,
    existing_enforcement: "ops.approve_rule_receipt_activation_v1 refuses a non-Joe authority session",
  },
  "retire-rule": {
    action: "system.policy", when: null,
    existing_enforcement: "ops.retire_rule refuses a non-Joe authority session",
  },
  "disable-legacy-schedule": {
    action: "system.autonomy_tier_activation", when: null,
    existing_enforcement: "ops.disable_legacy_schedule refuses a non-Joe authority session",
  },
  "accept-workflow": {
    action: "system.autonomy_tier_activation", when: { field: "mode", equals: "canary" },
    existing_enforcement: "ops.record_workflow_acceptance refuses canary acceptance by a non-Joe authority session",
  },
  "set-current-model-role-revision": {
    action: "system.design", when: null,
    existing_enforcement: "model-role-store.v5.js evaluates system.design in its own handler",
  },
});

for (const [verb, entry] of Object.entries(V5_DOOR_SYSTEM_AUTHORITY_VERBS)) {
  if (!Object.hasOwn(V5_ACTIONS, entry.action) || V5_ACTIONS[entry.action].authority_class !== "system_authority") {
    throw new V5BoundaryError("invalid_door_registry",
      `door verb "${verb}" must map to a registered system_authority action`, { verb, action: entry.action });
  }
}

function adminActionFor(verb, args) {
  if (!Object.hasOwn(V5_DOOR_SYSTEM_AUTHORITY_VERBS, verb)) return null;
  const entry = V5_DOOR_SYSTEM_AUTHORITY_VERBS[verb];
  if (entry.when && (!isPlainObject(args) || args[entry.when.field] !== entry.when.equals)) return null;
  return entry.action;
}

/**
 * The server-derived authority subject for a live actor.
 *
 *   verified human partner          -> that partner
 *   sponsored native/local agent    -> its server-derived sponsor, by the same
 *                                      partner-authority.js predicate that picks
 *                                      the authority database login
 *   anything else (probe, reviewer, Hermes, unsponsored) -> no subject
 *
 * Nothing a caller sends reaches this: the actor is the one the server built.
 */
export function doorAuthoritySubject(actor) {
  const authorization_class = authorizationClassForActor(actor);
  const subject = partnerAuthoritySlugForActor(actor);
  if (subject === null) return deepFreeze({ subject: null, source: "none", authorization_class });
  const source = actor?.human === true ? "verified_partner" : "server_derived_sponsor";
  return deepFreeze({ subject, source, authorization_class });
}

// ---------------------------------------------------------------------------
// Privacy: argument field names that mean a prohibited or routed data class.
// Exact names after normalization (camelCase and hyphens fold to snake_case).
// Bare "diagnosis" is deliberately absent: an engineering finding diagnoses a
// defect, and that word alone does not name patient data.
// ---------------------------------------------------------------------------

export const V5_DOOR_PRIVACY_FIELD_CLASSES = deepFreeze({
  phi: [
    "phi", "protected_health_information", "diagnosis_code", "diagnosis_codes", "medical_diagnosis",
    "patient_diagnosis", "icd10", "icd_10", "icd10_code", "icd_code", "cpt_code", "medication",
    "medications", "prescription", "prescriptions", "lab_result", "lab_results", "clinical_note",
    "clinical_notes", "treatment_plan", "medical_condition", "medical_history", "health_condition",
  ],
  patient_identifier: [
    "patient_name", "patient_first_name", "patient_last_name", "patient_id", "patient_ids",
    "patient_identifier", "patient_dob", "patient_date_of_birth", "patient_ssn", "patient_email",
    "patient_phone", "mrn", "medical_record_number", "health_plan_member_id", "insurance_member_id",
  ],
  patient_record: [
    "patient_record", "patient_records", "patient_chart", "medical_record", "medical_records",
    "health_record", "health_records", "ehr_record", "emr_record",
  ],
  raw_patient_location: [
    "patient_address", "patient_home_address", "patient_street", "patient_city", "patient_zip",
    "patient_zip_code", "patient_postal_code", "patient_lat", "patient_latitude", "patient_lng",
    "patient_lon", "patient_longitude", "patient_location", "patient_locations",
    "patient_coordinates", "patient_geocode",
  ],
  patient_visit_detail: [
    "patient_visit", "patient_visits", "patient_encounter", "patient_encounters", "encounter_date",
    "admission_date", "discharge_date", "visit_reason",
  ],
  aggregate_patient_location_heatmap: [
    "aggregate_patient_location_heatmap", "patient_heatmap", "patient_location_heatmap",
  ],
  aggregate_patient_volume_estimate: [
    "aggregate_patient_volume_estimate",
  ],
});

const PRIVACY_FIELD_TO_CLASS = (() => {
  const map = new Map();
  for (const [dataClass, names] of Object.entries(V5_DOOR_PRIVACY_FIELD_CLASSES)) {
    for (const name of names) {
      if (map.has(name)) throw new V5BoundaryError("invalid_door_registry", `field "${name}" maps twice`, { name });
      map.set(name, dataClass);
    }
  }
  return map;
})();

// ---------------------------------------------------------------------------
// Representation scope: the fields that assert which side we represent, the
// fields that would activate listing work, and the two structural fields the
// schema already carries.
// ---------------------------------------------------------------------------

export const V5_DOOR_REPRESENTATION_FIELDS = deepFreeze([
  "representation_side", "represented_side", "representing_side",
]);
export const V5_DOOR_LISTING_ACTIVATION_FIELDS = deepFreeze([
  "activate_listing_side", "listing_side_activation", "activate_listing", "enable_listing_side",
]);
export const V5_DOOR_STRUCTURAL_SIDE_FIELDS = deepFreeze({
  "record-counter": { field: "side", depth: "top", meaning: "whose paper a negotiation round is" },
  "add-premises": { field: "also_listing_side", depth: "ownership[]", meaning: "records the counterparty listing agent" },
});

/**
 * BOUNDED OUTPUT, WHATEVER THE INPUT. A caller controls how many fields match
 * and how long each key is, so nothing the door records may grow with either:
 * checks and refusals are merged by their identity with a count, at most
 * V5_DOOR_MAX_FIELD_PATHS example paths are kept per merged entry, and each
 * path is cut to V5_DOOR_MAX_FIELD_PATH_CHARS. Review of #1268 round 1 found
 * 9,000 matching rows producing one 800 KB log line; that is what this bounds.
 */
export const V5_DOOR_MAX_FIELD_PATHS = 5;
export const V5_DOOR_MAX_FIELD_PATH_CHARS = 160;

function boundedPath(path) {
  const text = String(path);
  return text.length > V5_DOOR_MAX_FIELD_PATH_CHARS
    ? `${text.slice(0, V5_DOOR_MAX_FIELD_PATH_CHARS - 1)}\u2026` : text;
}

/** Merge per-field checks that share an identity into one entry with a count. */
function mergeChecks(checks) {
  const merged = new Map();
  for (const check of checks) {
    const { field, fields, ...identity } = check;
    const key = JSON.stringify(identity);
    let entry = merged.get(key);
    if (!entry) {
      entry = { ...identity, count: 0, fields: [], fields_truncated: false };
      merged.set(key, entry);
    }
    const paths = fields ?? (field === undefined ? [] : [field]);
    entry.count += fields ? fields.length : 1;
    for (const path of paths) {
      if (entry.fields.length < V5_DOOR_MAX_FIELD_PATHS) entry.fields.push(boundedPath(path));
      else entry.fields_truncated = true;
    }
  }
  return [...merged.values()].map(entry => {
    const out = { ...entry };
    if (out.fields.length === 0) { delete out.fields; delete out.fields_truncated; }
    else out.field = out.fields[0];
    return out;
  });
}

/** The bound on the argument walk. Past it the scan is incomplete, and says so. */
export const V5_DOOR_MAX_SCANNED_NODES = 20000;
export const V5_DOOR_MAX_SCAN_DEPTH = 24;

export function normalizeFieldName(name) {
  return String(name)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[\s-]+/g, "_")
    .toLowerCase();
}

function scanArguments(args) {
  const privacyHits = [];
  const representationHits = [];
  const activationHits = [];
  let nodes = 0;
  let truncated = false;
  const walk = (value, path, depth) => {
    if (truncated) return;
    nodes += 1;
    if (nodes > V5_DOOR_MAX_SCANNED_NODES || depth > V5_DOOR_MAX_SCAN_DEPTH) { truncated = true; return; }
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${path}[${index}]`, depth + 1));
      return;
    }
    if (value === null || typeof value !== "object") return;
    for (const key of Object.keys(value)) {
      const normalized = normalizeFieldName(key);
      const childPath = path ? `${path}.${key}` : key;
      const dataClass = PRIVACY_FIELD_TO_CLASS.get(normalized);
      if (dataClass) privacyHits.push({ path: childPath, data_class: dataClass });
      if (V5_DOOR_REPRESENTATION_FIELDS.includes(normalized)) {
        representationHits.push({ path: childPath, value: value[key] });
      }
      if (V5_DOOR_LISTING_ACTIVATION_FIELDS.includes(normalized)) {
        activationHits.push({ path: childPath, value: value[key] });
      }
      walk(value[key], childPath, depth + 1);
    }
  };
  walk(args, "", 0);
  return { privacyHits, representationHits, activationHits, nodes, truncated };
}

function structuralSideValues(verb, args) {
  const out = [];
  if (!isPlainObject(args)) return out;
  if (verb === "record-counter" && typeof args.side === "string") {
    out.push({ path: "side", side: args.side });
  }
  if (verb === "add-premises" && Array.isArray(args.ownership)) {
    args.ownership.forEach((row, index) => {
      if (isPlainObject(row) && row.also_listing_side === true) {
        // The listing agent is by definition the brokerage side's representative.
        out.push({ path: `ownership[${index}].also_listing_side`, side: "landlord" });
      }
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// The per-dispatch verdict.
// ---------------------------------------------------------------------------

const POLICY_DIGEST = v5BoundaryPolicyDigest();

function checkResult(boundary, fields) {
  return { boundary, ...fields };
}

/** A contract violation inside an evaluator is a refusal at the door, never a pass. */
function guarded(boundary, fn) {
  try {
    return fn();
  } catch (error) {
    if (error instanceof V5BoundaryError) {
      return checkResult(boundary, {
        decision: "refuse", reason_id: `boundary_contract_violation:${error.code}`,
      });
    }
    throw error;
  }
}

/**
 * Evaluate one live dispatch against the settled boundaries.
 *
 * Returns a frozen verdict. `boundary_refused` is true when any evaluated boundary
 * refused (or could not be read); `enforced` is true only when the mode is
 * "enforce" AND the verdict would refuse. In shadow, `enforced` is always
 * false and the caller proceeds exactly as it would have without the door.
 */
export function evaluateDispatchBoundaries({
  verb, write, actor, args, context, mode = V5_BOUNDARY_DOOR_MODE,
} = {}) {
  assertMode(mode);
  if (typeof verb !== "string" || verb.length === 0) fail("invalid_shape", "verb must be a non-empty string", { path: "verb" });
  if (typeof write !== "boolean") fail("invalid_shape", "write must be a boolean", { path: "write" });
  if (!isPlainObject(context)) fail("invalid_shape", "context must be a plain object", { path: "context" });
  for (const key of Object.keys(context)) {
    if (!CONTEXT_KEYS.includes(key)) fail("unknown_field", `unknown field "${key}" at context`, { path: `context.${key}` });
  }
  if (!V5_CONNECTIVITY_STATES.includes(context.connectivity)) {
    fail("unknown_connectivity_state", `"${context.connectivity}" is not a registered connectivity state`,
      { connectivity: context.connectivity });
  }
  const operation_kind = write ? "mutation" : "read";
  const checks = [];

  // Q007: offline write.
  checks.push(guarded("read_continuity", () => {
    const answer = evaluateReadContinuity({ operation_kind, connectivity: context.connectivity });
    return checkResult("read_continuity", {
      decision: answer.decision, reason_id: answer.reason_id, availability: answer.availability,
    });
  }));

  // Q003/Q020/Q141: unauthorized admin.
  const subject = doorAuthoritySubject(actor);
  const adminAction = adminActionFor(verb, args);
  if (adminAction !== null) {
    checks.push(guarded("actor_authority", () => {
      // The subject partner, as the settled evaluator reads a partner. A seat
      // with no subject is handed over as itself and refuses as not a partner.
      const evaluatedActor = subject.subject === null ? actor : { slug: subject.subject, human: true };
      const request = { actor: evaluatedActor, action: adminAction, tenant: ORGANIZATION_TENANT_ID };
      if (context.now !== undefined) request.now = context.now;
      const answer = evaluateActorAuthority(request);
      return checkResult("actor_authority", {
        decision: answer.decision, reason_id: answer.reason_id, action: adminAction,
        authority_class: answer.authority_class,
      });
    }));
  }

  const scan = scanArguments(args ?? {});

  // Q033: PHI and raw patient-level locations.
  if (scan.privacyHits.length > 0) {
    checks.push(guarded("privacy", () => {
      const classes = [...new Set(scan.privacyHits.map(hit => hit.data_class))].sort();
      const answer = evaluatePrivacyBoundary({ data_classes: classes });
      return checkResult("privacy", {
        // needs_independent_privacy_route is not an acceptance: with no route
        // evidence at this door, the door holds it rather than passing it.
        decision: answer.decision === "allow" ? "allow" : "refuse",
        reason_id: answer.reason_id,
        data_classes: classes,
        fields: scan.privacyHits.map(hit => hit.path).sort(),
      });
    }));
  }
  if (scan.truncated) {
    checks.push(checkResult("privacy", {
      decision: "refuse", reason_id: "argument_scan_incomplete",
      scanned_nodes_bound: V5_DOOR_MAX_SCANNED_NODES, depth_bound: V5_DOOR_MAX_SCAN_DEPTH,
    }));
  }

  // Q073/Q092: listing-side exposure and activation.
  for (const hit of scan.representationHits) {
    checks.push(guarded("representation_scope", () => {
      const answer = evaluateRepresentationScope({ representation_side: hit.value, intent: "expose", surface: verb });
      return checkResult("representation_scope", {
        decision: answer.decision, reason_id: answer.reason_id, field: hit.path,
        representation_side: answer.representation_side,
      });
    }));
  }
  for (const hit of scan.activationHits) {
    if (hit.value === false || hit.value === null || hit.value === undefined) continue;
    checks.push(guarded("representation_scope", () => {
      // Any truthy value is an activation request; the settled evaluator reads
      // the listing side as the side being activated.
      const answer = evaluateRepresentationScope({
        representation_side: "landlord", intent: "activate", surface: verb, activate_listing_side: true,
      });
      return checkResult("representation_scope", {
        decision: answer.decision, reason_id: answer.reason_id, field: hit.path,
      });
    }));
  }
  for (const structural of structuralSideValues(verb, args)) {
    checks.push(guarded("representation_scope", () => {
      const answer = evaluateRepresentationScope({
        representation_side: structural.side, intent: "structural_record", surface: verb,
      });
      return checkResult("representation_scope", {
        decision: answer.decision, reason_id: answer.reason_id, field: structural.path,
        representation_side: answer.representation_side, structural: true,
      });
    }));
  }

  const mergedChecks = mergeChecks(checks);
  const refusalCounts = new Map();
  for (const check of mergedChecks) {
    if (check.decision === "allow") continue;
    const key = `${check.boundary}:${check.reason_id}`;
    const prior = refusalCounts.get(key);
    refusalCounts.set(key, {
      boundary: check.boundary, reason_id: check.reason_id, count: (prior?.count ?? 0) + check.count,
    });
  }
  const refusals = [...refusalCounts.values()];
  const boundary_refused = refusals.length > 0;
  return deepFreeze({
    schema_version: V5_BOUNDARY_DOOR_SCHEMA_VERSION,
    policy_schema_version: V5_BOUNDARY_SCHEMA_VERSION,
    policy_digest: POLICY_DIGEST,
    mode,
    door: context.door ?? null,
    verb,
    operation_kind,
    authority_subject: subject,
    checks: mergedChecks,
    refusals,
    boundary_refused,
    enforced: mode === "enforce" && boundary_refused,
    // Authority is never read from, or conditioned on, a local node.
    local_platform_consulted: false,
    permanent_privilege_granted: false,
    effects: V5_NO_EFFECTS,
  });
}

/** Thrown only in enforce mode. tools.js turns it into a ToolError by name. */
export class V5BoundaryDoorRefusal extends Error {
  constructor(verdict) {
    super(`v5 global boundary refused ${verdict.verb}: ${verdict.refusals.map(r => r.reason_id).join(", ")}`);
    this.name = "V5BoundaryDoorRefusal";
    this.payload = deepFreeze({
      error: "v5_boundary_refused",
      verb: verdict.verb,
      refusals: verdict.refusals.map(r => ({ ...r })),
      policy_digest: verdict.policy_digest,
      hint: "a settled v5 global boundary refused this request before any handler ran; nothing was recorded.",
    });
  }
}

// ---------------------------------------------------------------------------
// Shadow observation. Isolate-local, and it says so. One structured warn-level log line
// per would-refuse verdict goes to the Worker's logs; counters are surfaced by
// the read projection. Arguments are never logged, only field paths.
// ---------------------------------------------------------------------------

const observation = {
  started_at: null,
  evaluated: 0,
  boundary_refused: 0,
  enforced: 0,
  door_errors: 0,
  by_reason: new Map(),
  recent: [],
};
export const V5_DOOR_RECENT_LIMIT = 20;

function noteVerdict(verdict, nowIso) {
  if (observation.started_at === null) observation.started_at = nowIso ?? null;
  observation.evaluated += 1;
  if (!verdict.boundary_refused) return;
  observation.boundary_refused += 1;
  if (verdict.enforced) observation.enforced += 1;
  for (const refusal of verdict.refusals) {
    const key = `${refusal.boundary}:${refusal.reason_id}`;
    observation.by_reason.set(key, (observation.by_reason.get(key) ?? 0) + 1);
  }
  observation.recent.push({
    at: nowIso ?? null, verb: verdict.verb, mode: verdict.mode,
    subject: verdict.authority_subject.subject, source: verdict.authority_subject.source,
    refusals: verdict.refusals.map(r => ({ ...r })),
  });
  if (observation.recent.length > V5_DOOR_RECENT_LIMIT) observation.recent.shift();
}

export function doorObservationSnapshot() {
  return deepFreeze({
    scope: "this_worker_isolate_since_start",
    started_at: observation.started_at,
    evaluated: observation.evaluated,
    boundary_refused: observation.boundary_refused,
    enforced: observation.enforced,
    door_errors: observation.door_errors,
    by_reason: Object.fromEntries([...observation.by_reason.entries()].sort()),
    recent_refused: observation.recent.map(entry => ({ ...entry })),
  });
}

/** Test seam only: the counters are process state. */
export function resetDoorObservationForTest() {
  observation.started_at = null;
  observation.evaluated = 0;
  observation.boundary_refused = 0;
  observation.enforced = 0;
  observation.door_errors = 0;
  observation.by_reason = new Map();
  observation.recent = [];
}

/**
 * The one call the dispatch seam makes. Evaluates, records, and in enforce
 * mode throws V5BoundaryDoorRefusal. In shadow it NEVER throws: an internal
 * error becomes a door_error count and a log line, and dispatch proceeds.
 * `log` defaults to console.warn (stderr under Node, so a local break-glass
 * run never has a log line mixed into the JSON it prints on stdout; Workers
 * Logs capture it either way) and exists so tests can capture the line.
 */
export function passBoundaryDoor({ verb, write, actor, args, now, mode = V5_BOUNDARY_DOOR_MODE,
  context = V5_CLOUD_DOOR_CONTEXT, log = console.warn } = {}) {
  let verdict;
  try {
    verdict = evaluateDispatchBoundaries({ verb, write, actor, args, context: { ...context, now }, mode });
  } catch (error) {
    observation.door_errors += 1;
    try {
      log(JSON.stringify({ event: "v5_boundary_door_error", verb, mode,
        code: error?.code ?? null, message: String(error?.message ?? error).slice(0, 200) }));
    } catch { /* a lost log line never fails a dispatch */ }
    if (mode === "enforce") throw error;
    return null;
  }
  noteVerdict(verdict, now);
  if (verdict.boundary_refused) {
    try {
      log(JSON.stringify({ event: "v5_boundary_door", mode: verdict.mode, verb: verdict.verb,
        operation_kind: verdict.operation_kind, subject: verdict.authority_subject.subject,
        subject_source: verdict.authority_subject.source, enforced: verdict.enforced,
        refusals: verdict.refusals, policy_digest: verdict.policy_digest }));
    } catch { /* a lost log line never fails a dispatch */ }
  }
  if (verdict.enforced) throw new V5BoundaryDoorRefusal(verdict);
  return verdict;
}

// ---------------------------------------------------------------------------
// The read projection: the matrices, computed live from the settled evaluators
// rather than restated, so a production read-back is a read of the policy
// itself and not of a copy of it.
// ---------------------------------------------------------------------------

const MATRIX_NOW = "2026-01-01T00:00:00Z";

/**
 * Role matrix: every registered action, for each partner, with no grant.
 * `continuity_context` is the settled evaluator's own field for "why this
 * request exists during an outage"; the platform matrix passes each node state
 * through it to prove the answer does not move.
 */
export function v5RoleMatrix(continuity_context) {
  const full = slug => ({
    deal_owner_slug: slug, signer_slug: slug, account_slug: slug,
    policy_scope: [...V5_ACTION_KEYS],
    capabilities: ["deal.read", "deal.write", "document.send", "document.sign", "prospecting.write"],
  });
  return V5_ACTION_KEYS.map(action => {
    const row = { action, authority_class: V5_ACTIONS[action].authority_class };
    for (const slug of ["joe", "dell"]) {
      const request = { actor: { slug, human: true }, action, tenant: ORGANIZATION_TENANT_ID, now: MATRIX_NOW };
      if (continuity_context !== undefined) request.continuity_context = continuity_context;
      if (V5_ACTIONS[action].authority_class === "ordinary_business") request.controls = full(slug);
      const answer = evaluateActorAuthority(request);
      row[slug] = { decision: answer.decision, reason_id: answer.reason_id };
    }
    return row;
  });
}

/** Scope matrix: every side against every intent. */
export function v5ScopeMatrix() {
  const rows = [];
  for (const side of V5_REPRESENTATION_SIDES) {
    for (const intent of V5_SCOPE_INTENTS) {
      const answer = evaluateRepresentationScope({ representation_side: side, intent });
      rows.push({ representation_side: side, intent, decision: answer.decision,
        reason_id: answer.reason_id, exposed: answer.exposed });
    }
  }
  return rows;
}

/** Continuity matrix: every operation kind against every connectivity state. */
export function v5ContinuityMatrix() {
  const rows = [];
  for (const operation_kind of V5_OPERATION_KINDS) {
    for (const connectivity of V5_CONNECTIVITY_STATES) {
      const answer = evaluateReadContinuity({ operation_kind, connectivity });
      rows.push({ operation_kind, connectivity, decision: answer.decision,
        reason_id: answer.reason_id, availability: answer.availability });
    }
  }
  return rows;
}

/**
 * Platform matrix: every optional local node, every node state, every local
 * capability. Beside each row, the authority answer for both partners on a
 * system-authority and an ordinary-business action, which must be the same in
 * every row: losing a node degrades a capability and never moves authority.
 */
export function v5PlatformMatrix() {
  const baseline = JSON.stringify(v5RoleMatrix());
  const rows = [];
  const unchanged = {};
  for (const node of V5_OPTIONAL_LOCAL_NODES) {
    for (const node_state of V5_LOCAL_NODE_STATES) {
      unchanged[`${node}:${node_state}`] = JSON.stringify(v5RoleMatrix({
        local_platform_state: node_state, note: `${node} ${node_state}`,
      })) === baseline;
      for (const capability of Object.keys(V5_LOCAL_CAPABILITIES).sort()) {
        const answer = evaluateLocalPlatform({ node, node_state, capability });
        rows.push({ node, node_state, capability, decision: answer.decision, reason_id: answer.reason_id,
          availability: answer.availability, execution: answer.execution,
          canonical_authority: answer.canonical_authority, authority_unchanged: answer.authority_unchanged });
      }
    }
  }
  return {
    rows,
    canonical_authority: V5_CANONICAL_AUTHORITY,
    role_matrix_unchanged_by_node_state: unchanged,
  };
}

export function v5BoundaryDoorProjection({ observation: includeObservation = true } = {}) {
  return deepFreeze({
    projection: v5BoundaryProjection(),
    decisions: V5_SETTLED_DECISION_IDS.map(id => ({
      decision_id: id, source_evidence_digest: V5_SETTLED_DECISIONS[id].source_evidence_digest,
    })),
    decision_subset_canonical_sha256: V5_DECISION_SUBSET_CANONICAL_SHA256,
    matrices: {
      role: v5RoleMatrix(),
      scope: v5ScopeMatrix(),
      continuity: v5ContinuityMatrix(),
      platform: v5PlatformMatrix(),
    },
    documented_fallback: V5_DOCUMENTED_FALLBACK,
    door: {
      schema_version: V5_BOUNDARY_DOOR_SCHEMA_VERSION,
      mode: V5_BOUNDARY_DOOR_MODE,
      modes: [...V5_BOUNDARY_DOOR_MODES],
      cloud_context: { ...V5_CLOUD_DOOR_CONTEXT },
      system_authority_verbs: Object.entries(V5_DOOR_SYSTEM_AUTHORITY_VERBS)
        .map(([verb, entry]) => ({ verb, action: entry.action, when: entry.when,
          existing_enforcement: entry.existing_enforcement })),
      privacy_field_classes: V5_DOOR_PRIVACY_FIELD_CLASSES,
      representation_fields: [...V5_DOOR_REPRESENTATION_FIELDS],
      listing_activation_fields: [...V5_DOOR_LISTING_ACTIVATION_FIELDS],
      structural_side_fields: V5_DOOR_STRUCTURAL_SIDE_FIELDS,
      observation: includeObservation ? doorObservationSnapshot() : null,
    },
    accepts_anything: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The one read verb. Registered by tools.js; pure, no database access.
// ---------------------------------------------------------------------------

export function globalBoundariesDoorTools({ ToolError }) {
  return {
    "read-global-boundaries": {
      description: "Read DoctorCRE v5's settled global boundaries (V5-S01) as the live server evaluates them: the policy digest and the eight decision source-evidence digests; the role matrix (every registered action for Joe and Dell), the representation-scope matrix (tenant/buyer/landlord/seller against expose/structural/activate), the online-first continuity matrix, and the Mac Studio/Hermes platform matrix with the authority answer shown unchanged under every node state; plus the dispatch door's mode (shadow until Joe approves enforcement), its closed verb and field registries, and this Worker isolate's shadow counters. Each matrix is computed from the evaluators at read time, not copied. Pass expected_policy_digest to refuse if the deployed policy differs. Reads no database and accepts nothing.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          expected_policy_digest: { type: "string", description: "sha256:<64 hex>; refuses stale_expected_digest when the deployed policy hashes differently" },
        },
      },
      handler: async (_c, _actor, args) => {
        if (args.expected_policy_digest !== undefined) {
          try {
            v5BoundaryProjection({ expected_policy_digest: args.expected_policy_digest });
          } catch (error) {
            if (error instanceof V5BoundaryError)
              throw new ToolError({ error: error.code, ...(error.detail || {}) });
            throw error;
          }
        }
        return v5BoundaryDoorProjection();
      },
    },
  };
}
