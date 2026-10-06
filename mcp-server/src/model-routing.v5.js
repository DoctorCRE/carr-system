// DoctorCRE v5 slice V5-F04: durable role descriptions, measured route
// qualification, deterministic route selection and the honest-unavailable path
// (decisions Q031.D1, Q047.D1, Q106.D1, Q130.D1).
//
// THE SHAPE OF THE SLICE. A ROLE is a durable job description — skills, rules,
// authority, evidence requirements, quality floors. A model/version/effort on a
// backend is an OCCUPANT of that role and is replaceable: no occupant field
// exists on a role description, so a role's digest cannot move when the occupant
// does, and the job/context/capability/receipt envelope is built with no backend
// field in its preimage at all. That is what makes "the same jobs, contexts,
// capabilities and receipts across every runtime" a property of the bytes rather
// than a promise.
//
// FOUR TRUST SEAMS, and they are the reason this file is split the way it is:
//
//   1. POLICY IS INPUT, NOT INVENTION. Every weight, rank, price, latency budget
//      and task-class permission arrives in an explicit, versioned, closed
//      `model-routing-policy.v1` document that is hashed on the way in. This
//      module invents no benchmark, no price, no numeric business floor and no
//      real model's qualification. It refuses a policy that is missing a rank or
//      a cost component rather than defaulting one, because a defaulted zero cost
//      makes an unpriced route look cheapest.
//
//      STRENGTH IS ONE DECLARED SCALE, NOT TWO NUMBER MAPS. The policy declares
//      exactly one versioned `quality_scale` — an ordered list of named grades,
//      weakest first. An occupant's strength and a role's or job's minimum
//      strength are BOTH stated as names on that same declared scale, so the
//      no-downgrade comparison is between two positions in one ordering rather
//      than between two independently-declared integers that might not share a
//      unit. A grade the scale does not list is refused; a floor no available
//      route reaches is honestly `unavailable`, never a quiet weakening.
//
//   2. QUALIFICATION IS AUTHENTICATED EVIDENCE, NOT A CALLER BOOLEAN. There is
//      no `qualified: true` field anywhere below and a closed schema refuses one.
//      A route qualifies only when a `route-qualification.v1` record — measured,
//      currently valid, and exact on task class, backend, model, version, effort,
//      tools, data classes, risk coverage and quality floors — is handed over by
//      a TRUSTED PROJECTION VERIFIER installed as server code through
//      `createModelRoutingGate`. The verifier's return is digest-bound to the
//      exact request bytes it was given, exactly as V5-A00 binds its evidence.
//      `proposeRoutePlan` runs the identical selection over UNAUTHENTICATED
//      records and says so on every field it returns: a schema-valid, ranked
//      PROPOSAL is not a qualification, and the adapter boundary refuses one.
//
//   3. COST NEVER QUALIFIES. Cost is read only to ORDER routes that already
//      qualify. A cheaper route with no qualification record, an expired one, a
//      mismatched one, or one below the required strength is refused with its own
//      reason and never selected — and when the weaker route is the only one
//      standing, the answer is an honest `unavailable`, never a quiet downgrade.
//      There is no field, flag or option anywhere here that accepts a downgrade.
//
//   4. PROVENANCE IS OBJECT IDENTITY, NOT A FLAG AND NOT A DIGEST. A decision may
//      bind an envelope only if THIS module's gate produced it; an envelope may
//      reach the adapter boundary only if `bindEnvelopeToRoute` produced it; a
//      prompt payload only if `buildPromptPayload` produced it. Each is recorded
//      in a module-private WeakSet at the moment it is frozen, and the recorded
//      key is the frozen object itself, so `{ ...boundEnvelope, route: other }`
//      is a different object and is refused however well it hashes. `digest` is
//      exported and every preimage below is public: a hash proves the bytes did
//      not change, never that this module produced them.
//
//      WHAT THAT IS WORTH, EXACTLY. A WeakSet is PROCESS-LOCAL and dies with the
//      process. It does not survive `JSON.stringify`, a queue, a database row or
//      an HTTP hop, and nothing here pretends otherwise: an envelope that crossed
//      any of those is not authentic to this module and is refused rather than
//      re-admitted on its digest. Durable, transferable authenticity needs a
//      signed capability token issued by the record layer; that does not exist in
//      this repository and is named in `v5ModelRoutingProjection` rather than
//      imitated. `proposeRoutePlan` is deliberately outside all of it: a public,
//      offline, UNAUTHENTICATED ranking that no branding will ever mark.
//
// REUSED, NOT REDECIDED. Canonical hashing is artifact-trust.js's `digest`. The
// tenant is identity.js's. The authority classes, the optional local nodes and
// their states, the local-capability fallback dispositions and the PHI boundary
// are global-boundaries.v5.js's: local availability is answered by that module's
// `evaluateLocalPlatform` and the data classes by its `evaluatePrivacyBoundary`,
// so this file holds no second privacy policy and no second local-node policy.
// The small parsing helpers are re-stated because the sibling v5 modules keep
// theirs private; they are deliberately identical in behaviour, not a new policy.
//
// WHAT THIS FILE IS NOT. It is not a gateway, a provider client, a scheduler, a
// record store or an acceptance path. It reads no filesystem, no network, no
// database, no environment and no clock: every instant arrives as `now`. It
// makes no provider call and contains no code that could. `V5_NO_EFFECTS` rides
// on every result to say so.
//
// NAMED AS MISSING RATHER THAN BUILT. See `v5ModelRoutingProjection`: there is
// no live producer of `route-qualification.v1` in this repository, no durable
// record store or migration for one, and no dispatch. Those are integration and
// V5-F06/V5-F07 work. Nothing below fabricates them.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_NO_EFFECTS,
  V5_AUTHORITY_CLASSES,
  V5_CANONICAL_AUTHORITY,
  V5_OPTIONAL_LOCAL_NODES,
  V5_LOCAL_NODE_STATES,
  V5_LOCAL_CAPABILITIES,
  V5_DATA_CLASSES,
  V5_PROHIBITED_DATA_CLASSES,
  evaluateLocalPlatform,
  evaluatePrivacyBoundary,
} from "./global-boundaries.v5.js";

export const V5_ROUTING_SCHEMA_VERSION = "doctorcre-v5-model-routing.v1";
export const V5_ROUTING_POLICY_SCHEMA_VERSION = "model-routing-policy.v1";
export const V5_ROLE_DESCRIPTION_SCHEMA_VERSION = "role-description.v1";
export const V5_ROUTE_QUALIFICATION_SCHEMA_VERSION = "route-qualification.v1";
export const V5_JOB_ENVELOPE_SCHEMA_VERSION = "model-job-envelope.v1";
export const V5_ROUTING_RESULT_SCHEMA_VERSION = "model-route-decision.v1";
export const V5_PROMPT_PAYLOAD_SCHEMA_VERSION = "model-prompt-payload.v1";

/** Q130: the product identity is a constant. No backend may change it. */
export const V5_PUBLIC_PRODUCT_IDENTITY = "DoctorCRE";

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
// Reference tokens: lower-case, and wide enough for the `namespace:value` refs
// and the underscored data-class names S01 already registers.
const REF = /^[a-z0-9][a-z0-9_.:/-]{0,127}$/;
// `policy.occupant_grades` is keyed by the OCCUPANT identity `occupantKey` builds —
// `model@version/effort` — rather than by a bare reference token, so it carries
// its own grammar: three lower-case reference tokens joined by the two
// separators that function uses, and containing neither of them, so the key
// parses back to exactly one model, one version and one effort. A route whose
// model, version or effort itself carried a separator could not be keyed
// unambiguously; its rank is then undeclarable and the route is refused by
// `policy_rank_missing` rather than silently matched against another one's rank.
// Each part is also SHORTER than a bare `REF` (64 against 128): a model, version
// or effort token longer than 64 characters is a valid ref that cannot appear in
// a grammar-valid occupant key, so its grade is undeclarable and its route is
// refused by `policy_rank_missing`. Fail-closed, and stated here so it is not a
// surprise.
const OCCUPANT_PART = "[a-z0-9][a-z0-9_.:-]{0,63}";
const OCCUPANT_REF = new RegExp(`^${OCCUPANT_PART}@${OCCUPANT_PART}/${OCCUPANT_PART}$`);
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

export class V5RoutingError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5RoutingError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5RoutingError(code, message, detail);
}

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

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

// ---------------------------------------------------------------------------
// Local provenance registers.
//
// Membership is by OBJECT IDENTITY of the frozen product, recorded here at the
// moment this module creates it. Nothing a caller can construct, spread, copy,
// parse or re-hash is a member, because none of those is the same object.
//
// The scope of the claim is exactly "this object was produced by this module in
// this process, and has not been replaced by a look-alike since". It is NOT a
// claim about serialization, storage, transport, or any other process. See the
// header note and `v5ModelRoutingProjection().unimplemented_dependencies`.
// ---------------------------------------------------------------------------

export const V5_PROVENANCE_SCOPE = "process_local_object_identity_only";

const JOB_ENVELOPES = new WeakSet();
const AUTHENTIC_ROUTING_DECISIONS = new WeakSet();
const ROUTE_BOUND_ENVELOPES = new WeakSet();
const VALIDATED_PROMPT_PAYLOADS = new WeakSet();

/** True only for an envelope `buildJobEnvelope` produced in this process. */
export function isJobEnvelope(value) {
  return typeof value === "object" && value !== null && JOB_ENVELOPES.has(value);
}

/** True only for a decision this module's gate produced over authenticated evidence. */
export function isAuthenticRoutingDecision(value) {
  return typeof value === "object" && value !== null && AUTHENTIC_ROUTING_DECISIONS.has(value);
}

/** True only for an envelope `bindEnvelopeToRoute` produced in this process. */
export function isRouteBoundEnvelope(value) {
  return typeof value === "object" && value !== null && ROUTE_BOUND_ENVELOPES.has(value);
}

/** True only for a payload `buildPromptPayload` produced in this process. */
export function isValidatedPromptPayload(value) {
  return typeof value === "object" && value !== null && VALIDATED_PROMPT_PAYLOADS.has(value);
}

/** A structural copy taken before hashing, so a caller cannot mutate the preimage later. */
function copy(value) {
  if (Array.isArray(value)) return value.map(copy);
  if (isPlainObject(value)) {
    const out = {};
    for (const key of Object.keys(value)) out[key] = copy(value[key]);
    return out;
  }
  return value;
}

/** An open schema is an unenforced one; an unknown field is a contract violation. */
function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertRef(value, path) {
  if (typeof value !== "string" || !REF.test(value)) {
    fail("invalid_shape", `${path} must be a lower-case reference token`, { path, value });
  }
  return value;
}

/** The key grammar of a rank map keyed by occupant rather than by plain ref. */
function assertOccupantRef(value, path) {
  if (typeof value !== "string" || !OCCUPANT_REF.test(value)) {
    fail("invalid_shape",
      `${path} must be a lower-case "model@version/effort" occupant key`, { path, value });
  }
  return value;
}

function assertText(value, path) {
  if (typeof value !== "string" || value.trim().length === 0) {
    fail("invalid_shape", `${path} must be non-empty text`, { path });
  }
  return value;
}

/** A sorted, duplicate-free list of reference tokens. Order is never meaningful. */
function assertRefSet(value, path) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  value.forEach((item, index) => assertRef(item, `${path}[${index}]`));
  const unique = new Set(value);
  if (unique.size !== value.length) fail("duplicate_entry", `${path} contains a duplicate`, { path });
  return [...value].sort();
}

function assertTextList(value, path, { minimum = 1 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < minimum) fail("invalid_shape", `${path} must name at least ${minimum} entry`, { path });
  value.forEach((item, index) => assertText(item, `${path}[${index}]`));
  return [...value];
}

function assertInteger(value, path) {
  if (!Number.isSafeInteger(value)) fail("invalid_shape", `${path} must be a safe integer`, { path, value });
  return value;
}

function assertNonNegativeNumber(value, path) {
  if (!Number.isFinite(value) || value < 0) {
    fail("invalid_shape", `${path} must be a finite non-negative number`, { path, value });
  }
  return value;
}

function assertSha256Ref(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference`, { path });
  }
  return value;
}

function assertSha256Hex(value, path) {
  if (typeof value !== "string" || !SHA256_HEX.test(value)) {
    fail("invalid_digest", `${path} must be a 64-character lower-case sha256 hex digest`, { path });
  }
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Instants are parsed, never inferred, and the calendar is checked against the
 * LITERAL fields before parsing — `Date.parse` normalizes 2026-02-31 into 3 March
 * rather than refusing it, and a qualification window computed from an instant
 * nobody wrote is not bound to the evidence it claims to be bound to.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path, value });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) ||
      Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path, value });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path, value });
  return parsed;
}

// ---------------------------------------------------------------------------
// The settled decisions this slice implements.
//
// Text and source-evidence digests are copied verbatim from the reviewed F04
// source binding; they are identity, not configuration. The subset digest is
// COMPUTED from those bytes rather than pasted, so it cannot silently disagree
// with the table above it.
// ---------------------------------------------------------------------------

export const V5_ROUTING_DECISIONS = deepFreeze({
  "Q031.D1": {
    settled_requirement: "Route models by measured qualification, risk, privacy, latency, and expected total cost; local models earn task classes through evaluation and never silently replace a stronger required model.",
    source_evidence_digest: "5f10ccd4bbfec68187e0d41a7437a97afaccefb7e57fc54c7b241af49b710fae",
  },
  "Q047.D1": {
    settled_requirement: "Prefer the Mac Studio for evaluated local inference, rendering, OCR, embeddings, research, development, browser automation, and private compute while retaining fallback and no unique authority.",
    source_evidence_digest: "23d7a07d0334d3905b3f406a439309758579d363bad341f14fc7c386aa4c375b",
  },
  "Q106.D1": {
    settled_requirement: "Agents are durable human-readable job descriptions with skills, rules, authority, evidence, and quality floors; qualified model instances are replaceable occupants.",
    source_evidence_digest: "ef7521effb9256cb13a709265dacfefe9f86150b7b166e608437171952998de7",
  },
  "Q130.D1": {
    settled_requirement: "Hermes remains a replaceable backend platform for evaluated open-model workers and agents; it never becomes user-facing or authoritative and uses the same jobs, contexts, capabilities, and receipts.",
    source_evidence_digest: "f1fd79cfa15c15f473aec2d98ebd2d9ccaae22b5929694f8b5081cd6975273ff",
  },
});

export const V5_ROUTING_DECISION_IDS = deepFreeze(Object.keys(V5_ROUTING_DECISIONS).sort());

export const V5_ROUTING_DECISION_SUBSET_DIGEST = digest(
  V5_ROUTING_DECISION_IDS.map(decision_id => ({
    decision_id,
    settled_requirement: V5_ROUTING_DECISIONS[decision_id].settled_requirement,
    source_evidence_digest: V5_ROUTING_DECISIONS[decision_id].source_evidence_digest,
  })),
);

/** Refuse a caller whose decision subset has drifted, in either direction. */
export function assertRoutingDecisionBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decisions", "decision_subset_digest"], "binding");
  assertRequiredKeys(binding, ["decisions"], "binding");
  assertObject(binding.decisions, "binding.decisions");
  if ("decision_subset_digest" in binding) {
    assertSha256Ref(binding.decision_subset_digest, "binding.decision_subset_digest");
    if (binding.decision_subset_digest !== V5_ROUTING_DECISION_SUBSET_DIGEST) {
      fail("decision_binding_drift", "the decision subset digest does not match the reviewed subset", {
        expected: V5_ROUTING_DECISION_SUBSET_DIGEST, actual: binding.decision_subset_digest,
      });
    }
  }
  const supplied = Object.keys(binding.decisions).sort();
  const missing = V5_ROUTING_DECISION_IDS.filter(id => !supplied.includes(id));
  const extra = supplied.filter(id => !V5_ROUTING_DECISION_IDS.includes(id));
  if (missing.length > 0 || extra.length > 0) {
    fail("decision_binding_drift", "the supplied decision set is not the reviewed four", { missing, extra });
  }
  for (const id of V5_ROUTING_DECISION_IDS) {
    const entry = assertObject(binding.decisions[id], `binding.decisions.${id}`);
    assertClosedKeys(entry, ["source_evidence_digest", "settled_requirement"], `binding.decisions.${id}`);
    assertRequiredKeys(entry, ["source_evidence_digest"], `binding.decisions.${id}`);
    assertSha256Hex(entry.source_evidence_digest, `binding.decisions.${id}.source_evidence_digest`);
    if (entry.source_evidence_digest !== V5_ROUTING_DECISIONS[id].source_evidence_digest) {
      fail("decision_binding_drift", `source-evidence digest drift on ${id}`, { decision_id: id });
    }
    if ("settled_requirement" in entry &&
        entry.settled_requirement !== V5_ROUTING_DECISIONS[id].settled_requirement) {
      fail("decision_binding_drift", `settled requirement text drift on ${id}`, { decision_id: id });
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// Q106 — role descriptions.
//
// The six role keys come from the settled requirement, not from this file. A
// role description carries skills, rules, authority, evidence requirements and
// quality floors as HUMAN-READABLE text and REFERENCE tokens; the floors are
// named refs, never numbers, because this slice has no authority to set a
// numeric business floor and the versioned policy is where any such number would
// have to be declared.
//
// A ROLE CONFERS NO AUTHORITY BY ITSELF. It DECLARES the authority class and the
// capability references its jobs may draw on; whether an actor may actually act
// is still S01's `evaluateActorAuthority` question. `system_authority` is
// therefore refused outright as a role class: S01 reserves those actions to Joe
// and makes them non-delegable, so a durable job description whose occupant is a
// replaceable model could never be filled and must not pretend otherwise.
// ---------------------------------------------------------------------------

export const V5_ROLE_KEYS = deepFreeze([
  "program_manager", "architect", "builder", "reviewer", "release_controller", "operator",
]);

export const V5_OCCUPIABLE_AUTHORITY_CLASSES = deepFreeze(
  V5_AUTHORITY_CLASSES.filter(cls => cls !== "system_authority"),
);

const ROLE_KEYS_ALLOWED = Object.freeze([
  "role_key", "title", "mission", "skills", "rules", "authority",
  "evidence_requirements", "quality_floor_refs", "minimum_strength_ref", "task_classes",
]);
const ROLE_AUTHORITY_KEYS = Object.freeze(["authority_class", "capability_refs"]);

/**
 * Validate one durable role description and seal it with a digest over its
 * content. No occupant field exists, so no occupant can move the digest.
 */
export function defineRole(description) {
  assertObject(description, "role");
  assertClosedKeys(description, ROLE_KEYS_ALLOWED, "role");
  assertRequiredKeys(description, ROLE_KEYS_ALLOWED, "role");
  if (!V5_ROLE_KEYS.includes(description.role_key)) {
    fail("unknown_role_key", `"${description.role_key}" is not a settled v5 role`, {
      role_key: description.role_key, registered: [...V5_ROLE_KEYS],
    });
  }
  assertText(description.title, "role.title");
  assertText(description.mission, "role.mission");
  const skills = assertTextList(description.skills, "role.skills");
  const rules = assertTextList(description.rules, "role.rules");
  const evidence = assertTextList(description.evidence_requirements, "role.evidence_requirements");
  const floors = assertRefSet(description.quality_floor_refs, "role.quality_floor_refs");
  if (floors.length === 0) {
    fail("invalid_shape", "role.quality_floor_refs must name at least one quality floor",
      { path: "role.quality_floor_refs" });
  }
  assertRef(description.minimum_strength_ref, "role.minimum_strength_ref");
  const taskClasses = assertRefSet(description.task_classes, "role.task_classes");
  if (taskClasses.length === 0) {
    fail("invalid_shape", "role.task_classes must name at least one task class",
      { path: "role.task_classes" });
  }
  const authority = assertObject(description.authority, "role.authority");
  assertClosedKeys(authority, ROLE_AUTHORITY_KEYS, "role.authority");
  assertRequiredKeys(authority, ROLE_AUTHORITY_KEYS, "role.authority");
  if (!V5_AUTHORITY_CLASSES.includes(authority.authority_class)) {
    fail("unknown_authority_class", `"${authority.authority_class}" is not a registered v5 authority class`,
      { authority_class: authority.authority_class, registered: [...V5_AUTHORITY_CLASSES] });
  }
  if (!V5_OCCUPIABLE_AUTHORITY_CLASSES.includes(authority.authority_class)) {
    fail("role_authority_class_not_occupiable",
      "system authority is retained and non-delegable, so no role occupied by a replaceable model may declare it",
      { authority_class: authority.authority_class });
  }
  const capabilityRefs = assertRefSet(authority.capability_refs, "role.authority.capability_refs");

  const content = {
    schema_version: V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    role_key: description.role_key,
    title: description.title,
    mission: description.mission,
    skills,
    rules,
    authority: { authority_class: authority.authority_class, capability_refs: capabilityRefs },
    evidence_requirements: evidence,
    quality_floor_refs: floors,
    minimum_strength_ref: description.minimum_strength_ref,
    task_classes: taskClasses,
  };
  return deepFreeze({
    ...content,
    role_digest: digest(content),
    // Stated on the object because these are the Q106 properties a reviewer is
    // checking, and they must be true of every role this function can produce.
    occupant_bound: false,
    occupants_replaceable: true,
    role_confers_no_authority_by_itself: true,
    effects: V5_NO_EFFECTS,
  });
}

/** Recompute a role's seal, so a forged or edited role description refuses. */
function assertSealedRole(role, path = "role") {
  assertObject(role, path);
  if (role.schema_version !== V5_ROLE_DESCRIPTION_SCHEMA_VERSION) {
    fail("role_schema_version_invalid", `${path}.schema_version must be ${V5_ROLE_DESCRIPTION_SCHEMA_VERSION}`,
      { path });
  }
  const resealed = defineRole({
    role_key: role.role_key, title: role.title, mission: role.mission,
    skills: role.skills, rules: role.rules, authority: role.authority,
    evidence_requirements: role.evidence_requirements,
    quality_floor_refs: role.quality_floor_refs,
    minimum_strength_ref: role.minimum_strength_ref,
    task_classes: role.task_classes,
  });
  if (resealed.role_digest !== role.role_digest) {
    fail("role_digest_mismatch", `${path} does not hash to its own role_digest`,
      { expected: resealed.role_digest, actual: role.role_digest });
  }
  return resealed;
}

const OCCUPANT_KEYS = Object.freeze(["model_key", "model_version", "effort", "backend_key"]);

function assertOccupant(occupant, path) {
  assertObject(occupant, path);
  assertClosedKeys(occupant, OCCUPANT_KEYS, path);
  assertRequiredKeys(occupant, OCCUPANT_KEYS, path);
  for (const key of OCCUPANT_KEYS) assertRef(occupant[key], `${path}.${key}`);
  return occupant;
}

/** The exact occupant identity every qualification and rank is keyed by. */
export function occupantKey(occupant) {
  assertOccupant(occupant, "occupant");
  return `${occupant.model_key}@${occupant.model_version}/${occupant.effort}`;
}

/**
 * Assign a replaceable occupant to a durable role.
 *
 * The role's digest is returned UNCHANGED, which is the whole Q106 claim: the
 * job description is durable and the model instance filling it is not part of
 * its identity. Nothing here grants the occupant anything.
 */
export function assignOccupant(role, occupant) {
  const sealed = assertSealedRole(role);
  assertOccupant(occupant, "occupant");
  return deepFreeze({
    schema_version: V5_ROUTING_SCHEMA_VERSION,
    role_key: sealed.role_key,
    role_digest: sealed.role_digest,
    role_digest_unchanged_by_occupant: true,
    occupant: { ...occupant },
    occupant_key: occupantKey(occupant),
    occupant_replaceable: true,
    authority_class: sealed.authority.authority_class,
    occupant_grants_no_authority: true,
    canonical_authority: V5_CANONICAL_AUTHORITY,
    public_product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The versioned routing policy.
//
// EVERY NUMBER IN THIS SLICE ENTERS FROM OUTSIDE. Risk and privacy ranks,
// egress classes, permitted data classes, cost components, the ranking order
// and the ordered list of quality grades are declared by the policy document
// and hashed with it; the one remaining number, measured p95 latency, arrives
// on the qualification record because it is a measurement rather than a policy.
// The module supplies the closed grammar and the deterministic comparison, and
// supplies no value of its own.
//
// STRENGTH IS COMPARED ON ONE DECLARED SCALE. `quality_scale` names the grades
// and their order; `occupant_grades` says which grade each model@version/effort
// holds; `strength_refs` says which grade each named minimum-strength reference
// requires. Both maps must name grades the scale lists, so the floor and the
// occupant are never two numbers from two unrelated scales. A floor may be
// unreachable by every route this policy declares — that is a legitimate state
// and its answer is `unavailable`, not a weaker occupant.
//
// THE RANKING ORDER IS THE POLICY'S, THE DETERMINISM IS THIS FILE'S.
// `ranking.key_order` must be an exact permutation of the six registered keys —
// not a subset, not a superset, no repeats — so a policy cannot drop a criterion
// by omission or weight one twice. Direction is fixed here and never negotiable:
// quality descends (stronger first) and every other key ascends (less risk, less
// egress, less latency, less total cost, local before non-local). Ties fall
// through to `route_key`, so no two admitted routes can ever compare equal and
// the selection is a total order.
//
// COST IS TOTAL COST, and it is summed rather than modelled: base plus the
// declared expected retry, review and fallback components. This module has no
// price list and no retry-rate estimate, so all four components are required and
// a route missing any of them is refused rather than treated as free.
// ---------------------------------------------------------------------------

export const V5_BACKEND_KINDS = deepFreeze(["local", "hosted_open_model_platform", "cloud"]);
export const V5_PRIVACY_RESTRICTIONS = deepFreeze(["none", "local_only"]);
export const V5_RANKING_KEYS = deepFreeze([
  "quality_rank", "residual_risk_rank", "privacy_rank",
  "latency_ms_p95", "expected_total_cost_units", "local_preference_rank",
]);
/** The one key that sorts high-to-low; every other registered key sorts low-to-high. */
const DESCENDING_RANKING_KEYS = Object.freeze(["quality_rank"]);
const COST_COMPONENT_KEYS = Object.freeze([
  "base_cost_units", "expected_retry_cost_units",
  "expected_review_cost_units", "expected_fallback_cost_units",
]);

const POLICY_KEYS = Object.freeze([
  "schema_version", "policy_id", "policy_version", "ranking", "risk_ranks", "privacy_ranks",
  "quality_scale", "occupant_grades", "strength_refs", "maximum_qualification_age_ms",
  "backends", "task_classes", "routes",
]);
const POLICY_REQUIRED = Object.freeze([
  "schema_version", "policy_id", "policy_version", "ranking", "risk_ranks", "privacy_ranks",
  "quality_scale", "occupant_grades", "strength_refs", "backends", "task_classes", "routes",
]);
const BACKEND_KEYS_ALLOWED = Object.freeze([
  "backend_key", "kind", "local_node", "egress_class", "permitted_data_classes",
  "user_facing", "grants_authority",
]);
const TASK_CLASS_KEYS = Object.freeze([
  "task_class", "permitted_backend_keys", "permitted_data_classes", "required_quality_floor_refs",
  "local_preference_enabled", "local_capability", "privacy_restriction",
]);
const ROUTE_KEYS_ALLOWED = Object.freeze([
  "route_key", "task_class", "backend_key", "model_key", "model_version", "effort",
  "residual_risk_class", "cost_components",
]);

/**
 * A closed map of reference key to integer rank, held on a NULL-PROTOTYPE object
 * so that `constructor`, `prototype` and friends are ordinary keys rather than
 * inherited ones: an undeclared `constructor` must refuse by name, not resolve
 * to a function or throw a raw TypeError three calls later.
 *
 * Only `risk_ranks` and `privacy_ranks` are integer maps, and each is used to
 * compare two values drawn from THAT SAME map — job risk against qualified risk,
 * one egress class against another — so a rank means something only relative to
 * its own map and never has to be commensurable with another one. Strength does
 * have to be commensurable across two declarations, which is exactly why it is
 * not an integer map at all: see `compileQualityScale`.
 */
function assertRankMap(value, path, assertKey = assertRef) {
  assertObject(value, path);
  const keys = Object.keys(value);
  if (keys.length === 0) fail("invalid_shape", `${path} must declare at least one rank`, { path });
  const ranks = Object.create(null);
  for (const key of keys) {
    assertKey(key, `${path}.<key>`);
    ranks[key] = assertInteger(value[key], `${path}.${key}`);
  }
  return Object.freeze(ranks);
}

const QUALITY_SCALE_KEYS = Object.freeze(["scale_id", "scale_version", "grades"]);

/**
 * THE ONE DECLARED STRENGTH SCALE.
 *
 * `grades` is an ordered list of NAMED grades, weakest first, owned and
 * versioned by the policy. Its positions are the only strength numbers in this
 * slice, and this module derives them from the declared order rather than
 * accepting them: two independently-declared numeric maps could each be
 * well-formed and still not share a unit, and a floor of 20 measured against a
 * quality of 30 would then mean nothing at all.
 *
 * Both directions of the no-downgrade comparison resolve through this object:
 * `occupant_grades` says which grade an occupant holds, `strength_refs` says
 * which grade a named minimum-strength reference requires, and each value must
 * be a grade this scale lists. Changing the order, adding, removing or renaming
 * a grade changes `scale_version`, changes the policy bytes and therefore
 * changes `policy_digest`, so a decision made under the old scale cannot be
 * carried into a prompt built under the new one.
 */
function compileQualityScale(value, path = "policy.quality_scale") {
  assertObject(value, path);
  assertClosedKeys(value, QUALITY_SCALE_KEYS, path);
  assertRequiredKeys(value, QUALITY_SCALE_KEYS, path);
  assertRef(value.scale_id, `${path}.scale_id`);
  assertInteger(value.scale_version, `${path}.scale_version`);
  if (value.scale_version < 1) {
    fail("invalid_shape", `${path}.scale_version must be >= 1`, { path: `${path}.scale_version` });
  }
  if (!Array.isArray(value.grades) || value.grades.length < 2) {
    fail("quality_scale_invalid",
      `${path}.grades must be an ordered list of at least two named grades, weakest first`,
      { path: `${path}.grades` });
  }
  value.grades.forEach((grade, index) => assertRef(grade, `${path}.grades[${index}]`));
  if (new Set(value.grades).size !== value.grades.length) {
    fail("quality_scale_invalid",
      `${path}.grades names one grade twice; a grade holds exactly one position on the scale`,
      { path: `${path}.grades` });
  }
  const ranks = Object.create(null);
  value.grades.forEach((grade, index) => { ranks[grade] = index; });
  return Object.freeze({
    scale_id: value.scale_id,
    scale_version: value.scale_version,
    grades: Object.freeze([...value.grades]),
    ranks: Object.freeze(ranks),
  });
}

/** Resolve one grade NAME to its position on the declared scale, or refuse it. */
function gradeRank(scale, grade, path) {
  if (typeof grade !== "string" || !hasOwn(scale.ranks, grade)) {
    fail("unknown_quality_grade",
      `${path} names "${grade}", which the declared quality scale "${scale.scale_id}" (version ${scale.scale_version}) does not list`,
      {
        path, grade: typeof grade === "string" ? grade : null,
        scale_id: scale.scale_id, scale_version: scale.scale_version,
        declared_grades: [...scale.grades],
      });
  }
  return scale.ranks[grade];
}

/**
 * A closed map whose VALUES are grade names on the one declared scale. Both
 * `occupant_grades` and `strength_refs` are validated through here, which is
 * what makes the two sides of the strength comparison the same scale by
 * construction rather than by convention.
 */
function assertGradeMap(value, path, scale, assertKey = assertRef) {
  assertObject(value, path);
  const keys = Object.keys(value);
  if (keys.length === 0) fail("invalid_shape", `${path} must declare at least one entry`, { path });
  const grades = Object.create(null);
  for (const key of keys) {
    assertKey(key, `${path}.<key>`);
    gradeRank(scale, value[key], `${path}.${key}`);
    grades[key] = value[key];
  }
  return Object.freeze(grades);
}

/**
 * There is ONE data-class vocabulary and S01 owns it. A policy that names an
 * unregistered class is refused rather than quietly given a private meaning, and
 * a policy that permits a globally prohibited class is refused outright — a
 * routing document is not a place to widen the PHI boundary.
 */
function assertRegisteredDataClasses(classes, path) {
  for (const dataClass of classes) {
    if (!V5_DATA_CLASSES.includes(dataClass)) {
      fail("unknown_data_class", `${path} names "${dataClass}", which is not a registered v5 data class`,
        { path, data_class: dataClass });
    }
    if (V5_PROHIBITED_DATA_CLASSES.includes(dataClass)) {
      fail("prohibited_data_class_in_policy",
        `${path} names "${dataClass}", which the global boundary prohibits`,
        { path, data_class: dataClass });
    }
  }
  return classes;
}

function lookupRank(map, key, path) {
  if (!hasOwn(map, key)) {
    fail("policy_rank_missing",
      `${path} declares no rank for "${key}"; the policy must state it rather than have one defaulted`,
      { path, key });
  }
  return map[key];
}

/**
 * Look one declared grade up and resolve it on the declared scale. A key the
 * policy never declared is `policy_rank_missing` — the same refusal an omitted
 * risk or privacy rank gets — and a declared grade the scale does not list is
 * `unknown_quality_grade`. The two are different operational facts.
 */
function lookupGradeRank(map, key, path, scale) {
  if (!hasOwn(map, key)) {
    fail("policy_rank_missing",
      `${path} declares no grade for "${key}"; the policy must state it rather than have one defaulted`,
      { path, key });
  }
  return gradeRank(scale, map[key], `${path}.${key}`);
}

/**
 * Validate a `model-routing-policy.v1` document and seal it with the digest of
 * the exact bytes supplied. The returned object keeps its own frozen `source`,
 * so every later use can re-verify the seal instead of trusting the wrapper.
 */
export function compileRoutingPolicy(policy) {
  assertObject(policy, "policy");
  assertClosedKeys(policy, POLICY_KEYS, "policy");
  assertRequiredKeys(policy, POLICY_REQUIRED, "policy");
  if (policy.schema_version !== V5_ROUTING_POLICY_SCHEMA_VERSION) {
    fail("policy_schema_version_invalid",
      `policy.schema_version must be ${V5_ROUTING_POLICY_SCHEMA_VERSION}`,
      { actual: policy.schema_version });
  }
  assertRef(policy.policy_id, "policy.policy_id");
  assertInteger(policy.policy_version, "policy.policy_version");
  if (policy.policy_version < 1) fail("invalid_shape", "policy.policy_version must be >= 1", { path: "policy.policy_version" });

  const ranking = assertObject(policy.ranking, "policy.ranking");
  assertClosedKeys(ranking, ["key_order"], "policy.ranking");
  assertRequiredKeys(ranking, ["key_order"], "policy.ranking");
  if (!Array.isArray(ranking.key_order) || ranking.key_order.length !== V5_RANKING_KEYS.length ||
      new Set(ranking.key_order).size !== V5_RANKING_KEYS.length ||
      !ranking.key_order.every(key => V5_RANKING_KEYS.includes(key))) {
    fail("ranking_key_order_invalid",
      "policy.ranking.key_order must be an exact permutation of the registered ranking keys",
      { registered: [...V5_RANKING_KEYS], supplied: ranking.key_order });
  }
  const keyOrder = Object.freeze([...ranking.key_order]);

  const riskRanks = assertRankMap(policy.risk_ranks, "policy.risk_ranks");
  const privacyRanks = assertRankMap(policy.privacy_ranks, "policy.privacy_ranks");
  const qualityScale = compileQualityScale(policy.quality_scale);
  // Both of these resolve through `qualityScale` and neither carries a number of
  // its own. `occupant_grades` is keyed by occupant identity, because that is
  // the key `admitRoute` and the route table below look a grade up by;
  // `strength_refs` is keyed by the named minimum-strength reference a role or a
  // job cites. Two names, one ordering.
  const occupantGrades = assertGradeMap(
    policy.occupant_grades, "policy.occupant_grades", qualityScale, assertOccupantRef);
  const strengthRefs = assertGradeMap(policy.strength_refs, "policy.strength_refs", qualityScale);
  // Optional, and its absence is a real choice rather than a default: when no
  // policy age bound is declared, the only thing bounding staleness is each
  // record's own `expires_at`, which the producer set.
  let maximumQualificationAgeMs = null;
  if (policy.maximum_qualification_age_ms !== undefined && policy.maximum_qualification_age_ms !== null) {
    maximumQualificationAgeMs = assertInteger(
      policy.maximum_qualification_age_ms, "policy.maximum_qualification_age_ms");
    if (maximumQualificationAgeMs <= 0) {
      fail("invalid_shape", "policy.maximum_qualification_age_ms must be positive when present",
        { path: "policy.maximum_qualification_age_ms" });
    }
  }

  if (!Array.isArray(policy.backends) || policy.backends.length === 0) {
    fail("invalid_shape", "policy.backends must name at least one backend", { path: "policy.backends" });
  }
  // Null-prototype: a backend or task class legitimately named `constructor` or
  // `prototype` must be an ordinary declared key, and an UNDECLARED one must
  // miss rather than resolve to something off Object.prototype.
  const backends = Object.create(null);
  policy.backends.forEach((entry, index) => {
    const path = `policy.backends[${index}]`;
    assertObject(entry, path);
    assertClosedKeys(entry, BACKEND_KEYS_ALLOWED, path);
    assertRequiredKeys(entry, [
      "backend_key", "kind", "egress_class", "permitted_data_classes", "user_facing", "grants_authority",
    ], path);
    assertRef(entry.backend_key, `${path}.backend_key`);
    if (hasOwn(backends, entry.backend_key)) {
      fail("duplicate_entry", `${path}.backend_key is declared twice`, { path });
    }
    if (!V5_BACKEND_KINDS.includes(entry.kind)) {
      fail("unknown_backend_kind", `"${entry.kind}" is not a registered backend kind`,
        { path: `${path}.kind`, registered: [...V5_BACKEND_KINDS] });
    }
    // Q130 and Q047: no backend is user-facing and no backend mints authority.
    // These are declared as literal false so a policy that says otherwise is a
    // refusal a reviewer can point at, not a field this module quietly ignores.
    if (entry.user_facing !== false) {
      fail("backend_user_facing_refused",
        "no model backend is user-facing; the product surface is DoctorCRE",
        { path: `${path}.user_facing`, backend_key: entry.backend_key });
    }
    if (entry.grants_authority !== false) {
      fail("backend_authority_mint_refused",
        "a backend never mints authority; authority stays with the canonical record layer",
        { path: `${path}.grants_authority`, backend_key: entry.backend_key });
    }
    assertRef(entry.egress_class, `${path}.egress_class`);
    lookupRank(privacyRanks, entry.egress_class, "policy.privacy_ranks");
    const permitted = assertRegisteredDataClasses(
      assertRefSet(entry.permitted_data_classes, `${path}.permitted_data_classes`),
      `${path}.permitted_data_classes`);
    let localNode = null;
    if (entry.kind === "local") {
      if (!V5_OPTIONAL_LOCAL_NODES.includes(entry.local_node)) {
        fail("unknown_local_node",
          `${path}.local_node must be one of the registered optional local nodes`,
          { path: `${path}.local_node`, registered: [...V5_OPTIONAL_LOCAL_NODES] });
      }
      localNode = entry.local_node;
    } else if (entry.local_node !== undefined && entry.local_node !== null) {
      fail("invalid_shape", `${path}.local_node is only meaningful for a local backend`,
        { path: `${path}.local_node` });
    }
    backends[entry.backend_key] = deepFreeze({
      backend_key: entry.backend_key, kind: entry.kind, local_node: localNode,
      egress_class: entry.egress_class, permitted_data_classes: permitted,
      user_facing: false, grants_authority: false,
    });
  });

  if (!Array.isArray(policy.task_classes) || policy.task_classes.length === 0) {
    fail("invalid_shape", "policy.task_classes must name at least one task class",
      { path: "policy.task_classes" });
  }
  const taskClasses = Object.create(null);
  policy.task_classes.forEach((entry, index) => {
    const path = `policy.task_classes[${index}]`;
    assertObject(entry, path);
    assertClosedKeys(entry, TASK_CLASS_KEYS, path);
    assertRequiredKeys(entry, [
      "task_class", "permitted_backend_keys", "permitted_data_classes",
      "required_quality_floor_refs", "local_preference_enabled", "privacy_restriction",
    ], path);
    assertRef(entry.task_class, `${path}.task_class`);
    if (hasOwn(taskClasses, entry.task_class)) {
      fail("duplicate_entry", `${path}.task_class is declared twice`, { path });
    }
    const permittedBackends = assertRefSet(entry.permitted_backend_keys, `${path}.permitted_backend_keys`);
    for (const key of permittedBackends) {
      if (!hasOwn(backends, key)) {
        fail("unknown_backend_key", `${path}.permitted_backend_keys names an undeclared backend`,
          { path, backend_key: key });
      }
    }
    const permittedData = assertRegisteredDataClasses(
      assertRefSet(entry.permitted_data_classes, `${path}.permitted_data_classes`),
      `${path}.permitted_data_classes`);
    const requiredFloors = assertRefSet(entry.required_quality_floor_refs, `${path}.required_quality_floor_refs`);
    if (typeof entry.local_preference_enabled !== "boolean") {
      fail("invalid_shape", `${path}.local_preference_enabled must be a boolean`,
        { path: `${path}.local_preference_enabled` });
    }
    if (!V5_PRIVACY_RESTRICTIONS.includes(entry.privacy_restriction)) {
      fail("unknown_privacy_restriction", `"${entry.privacy_restriction}" is not a registered privacy restriction`,
        { path: `${path}.privacy_restriction`, registered: [...V5_PRIVACY_RESTRICTIONS] });
    }
    // A task class that may reach a local node must name which S01 local
    // capability it is, because S01 — not this file — owns what happens when
    // that node is gone.
    const reachesLocal = permittedBackends.some(key => backends[key].kind === "local");
    let localCapability = null;
    if (reachesLocal) {
      if (typeof entry.local_capability !== "string" ||
          !hasOwn(V5_LOCAL_CAPABILITIES, entry.local_capability)) {
        fail("unknown_local_capability",
          `${path}.local_capability must name a registered S01 local capability`,
          { path: `${path}.local_capability`, registered: Object.keys(V5_LOCAL_CAPABILITIES) });
      }
      localCapability = entry.local_capability;
    } else if (entry.local_capability !== undefined && entry.local_capability !== null) {
      fail("invalid_shape", `${path}.local_capability is only meaningful when a local backend is permitted`,
        { path: `${path}.local_capability` });
    }
    if (entry.privacy_restriction === "local_only" &&
        permittedBackends.some(key => backends[key].kind !== "local")) {
      fail("privacy_restriction_contradicted",
        `${path} is local-only yet permits a non-local backend`,
        { path, task_class: entry.task_class });
    }
    taskClasses[entry.task_class] = deepFreeze({
      task_class: entry.task_class,
      permitted_backend_keys: permittedBackends,
      permitted_data_classes: permittedData,
      required_quality_floor_refs: requiredFloors,
      local_preference_enabled: entry.local_preference_enabled,
      local_capability: localCapability,
      privacy_restriction: entry.privacy_restriction,
    });
  });

  if (!Array.isArray(policy.routes) || policy.routes.length === 0) {
    fail("invalid_shape", "policy.routes must name at least one route", { path: "policy.routes" });
  }
  const routes = [];
  const seenRouteKeys = new Set();
  const seenRouteIdentities = new Set();
  policy.routes.forEach((entry, index) => {
    const path = `policy.routes[${index}]`;
    assertObject(entry, path);
    assertClosedKeys(entry, ROUTE_KEYS_ALLOWED, path);
    assertRequiredKeys(entry, ROUTE_KEYS_ALLOWED, path);
    assertRef(entry.route_key, `${path}.route_key`);
    if (seenRouteKeys.has(entry.route_key)) {
      fail("duplicate_entry", `${path}.route_key is declared twice`, { path, route_key: entry.route_key });
    }
    seenRouteKeys.add(entry.route_key);
    if (!hasOwn(taskClasses, entry.task_class)) {
      fail("unknown_task_class", `${path}.task_class is not declared by this policy`,
        { path, task_class: entry.task_class });
    }
    if (!hasOwn(backends, entry.backend_key)) {
      fail("unknown_backend_key", `${path}.backend_key is not declared by this policy`,
        { path, backend_key: entry.backend_key });
    }
    for (const key of ["model_key", "model_version", "effort"]) assertRef(entry[key], `${path}.${key}`);
    const identity = `${entry.task_class}|${entry.backend_key}|${entry.model_key}|${entry.model_version}|${entry.effort}`;
    if (seenRouteIdentities.has(identity)) {
      fail("duplicate_entry",
        `${path} repeats an exact task-class/backend/model/version/effort identity`, { path, identity });
    }
    seenRouteIdentities.add(identity);
    assertRef(entry.residual_risk_class, `${path}.residual_risk_class`);
    lookupRank(riskRanks, entry.residual_risk_class, "policy.risk_ranks");
    // Every route's occupant must hold a grade on the declared scale. A route
    // whose model, version or effort carries one of the two occupant-key
    // separators cannot be keyed unambiguously, so no grammar-valid
    // `occupant_grades` key can name it and it refuses here rather than being
    // matched against some other occupant's grade.
    const occupantIdentity = `${entry.model_key}@${entry.model_version}/${entry.effort}`;
    lookupGradeRank(occupantGrades, occupantIdentity, "policy.occupant_grades", qualityScale);
    const cost = assertObject(entry.cost_components, `${path}.cost_components`);
    assertClosedKeys(cost, COST_COMPONENT_KEYS, `${path}.cost_components`);
    assertRequiredKeys(cost, COST_COMPONENT_KEYS, `${path}.cost_components`);
    let total = 0;
    for (const key of COST_COMPONENT_KEYS) {
      total += assertNonNegativeNumber(cost[key], `${path}.cost_components.${key}`);
    }
    routes.push(deepFreeze({
      route_key: entry.route_key, task_class: entry.task_class, backend_key: entry.backend_key,
      model_key: entry.model_key, model_version: entry.model_version, effort: entry.effort,
      occupant_key: occupantIdentity,
      occupant_grade: occupantGrades[occupantIdentity],
      residual_risk_class: entry.residual_risk_class,
      cost_components: Object.freeze({ ...cost }),
      expected_total_cost_units: total,
    }));
  });

  const source = deepFreeze(copy(policy));
  return deepFreeze({
    schema_version: V5_ROUTING_POLICY_SCHEMA_VERSION,
    policy_id: policy.policy_id,
    policy_version: policy.policy_version,
    policy_digest: digest(source),
    ranking_key_order: keyOrder,
    risk_ranks: riskRanks,
    privacy_ranks: privacyRanks,
    quality_scale: qualityScale,
    occupant_grades: occupantGrades,
    strength_refs: strengthRefs,
    maximum_qualification_age_ms: maximumQualificationAgeMs,
    backends: Object.freeze(backends),
    task_classes: Object.freeze(taskClasses),
    routes: Object.freeze(routes.sort((a, b) => (a.route_key < b.route_key ? -1 : 1))),
    source,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Verify a compiled policy and return it RECOMPILED from its own verified bytes.
 *
 * Returning the recompilation rather than the object handed in is deliberate:
 * the seal covers `source`, so the indexes beside it are only trustworthy if
 * they are rebuilt from those exact bytes. A wrapper carrying a valid source and
 * a quietly edited route table is therefore ignored rather than believed.
 */
function assertCompiledPolicy(policy) {
  assertObject(policy, "policy");
  if (policy.schema_version !== V5_ROUTING_POLICY_SCHEMA_VERSION || !isPlainObject(policy.source) ||
      typeof policy.policy_digest !== "string") {
    fail("policy_not_compiled",
      "pass a policy compiled by compileRoutingPolicy, so its digest binds the exact bytes supplied");
  }
  if (digest(policy.source) !== policy.policy_digest) {
    fail("policy_digest_mismatch", "the compiled policy no longer hashes to its own policy_digest",
      { expected: digest(policy.source), actual: policy.policy_digest });
  }
  return compileRoutingPolicy(policy.source);
}

// ---------------------------------------------------------------------------
// Q031 — route qualification records.
//
// A qualification is a MEASUREMENT with a window, produced elsewhere and read
// here. It names the exact task class, backend, model, version and effort it was
// measured for; the exact tools, data classes and risk coverage that were in
// scope; the quality floors it met; and the digest of the measurement record
// behind it. There is no confidence score, no self-report and no boolean: a
// record either matches this request exactly and is current, or it does not.
//
// The names below are deliberately reserved: a record carrying `qualified`,
// `self_reported_quality`, `model_says_qualified` or similar is refused by name
// rather than merely by the closed-key check, so the bypass has an error a
// reviewer can search for.
// ---------------------------------------------------------------------------

const QUALIFICATION_KEYS = Object.freeze([
  "qualification_id", "task_class", "backend_key", "model_key", "model_version", "effort",
  "qualified_tool_ids", "permitted_data_classes", "qualified_max_risk_class",
  "met_quality_floor_refs", "measured_latency_ms_p95", "measured_at", "expires_at",
  "measurement_digest", "verifier_id",
]);

/**
 * Exported so the evaluation kernel refuses the SAME reserved names on the
 * OBSERVATIONS it counts that this module refuses on the record it reads. One
 * list: a second copy would drift, and the name somebody actually reaches for
 * would end up refused on one side of the seam and admitted on the other.
 */
export const V5_SELF_CERTIFICATION_FIELDS = Object.freeze([
  "qualified", "is_qualified", "self_qualified", "self_reported_quality", "model_says_qualified",
  "model_self_assessment", "confidence", "assumed_capable", "trust_me", "override", "bypass",
]);

/** Validate one measured qualification record. */
export function assertQualificationRecord(record, path = "qualification") {
  assertObject(record, path);
  const selfCertified = Object.keys(record).filter(key => V5_SELF_CERTIFICATION_FIELDS.includes(key)).sort();
  if (selfCertified.length > 0) {
    fail("self_certified_qualification_refused",
      "qualification is a measurement read from a trusted verifier, never a claim carried in the record",
      { path, fields: selfCertified });
  }
  assertClosedKeys(record, QUALIFICATION_KEYS, path);
  assertRequiredKeys(record, QUALIFICATION_KEYS, path);
  for (const key of ["qualification_id", "task_class", "backend_key", "model_key", "model_version",
    "effort", "qualified_max_risk_class", "verifier_id"]) {
    assertRef(record[key], `${path}.${key}`);
  }
  const tools = assertRefSet(record.qualified_tool_ids, `${path}.qualified_tool_ids`);
  const dataClasses = assertRefSet(record.permitted_data_classes, `${path}.permitted_data_classes`);
  const floors = assertRefSet(record.met_quality_floor_refs, `${path}.met_quality_floor_refs`);
  assertNonNegativeNumber(record.measured_latency_ms_p95, `${path}.measured_latency_ms_p95`);
  const measuredAt = assertInstant(record.measured_at, `${path}.measured_at`);
  const expiresAt = assertInstant(record.expires_at, `${path}.expires_at`);
  if (expiresAt <= measuredAt) {
    fail("qualification_window_invalid", `${path}.expires_at must be after ${path}.measured_at`, { path });
  }
  assertSha256Ref(record.measurement_digest, `${path}.measurement_digest`);
  return deepFreeze({
    ...record,
    qualified_tool_ids: tools,
    permitted_data_classes: dataClasses,
    met_quality_floor_refs: floors,
    measured_at_ms: measuredAt,
    expires_at_ms: expiresAt,
    occupant_key: `${record.model_key}@${record.model_version}/${record.effort}`,
  });
}

// ---------------------------------------------------------------------------
// Q106 / Q130 — the job and its envelope.
//
// THE ENVELOPE PREIMAGE CONTAINS NO BACKEND, NO MODEL, NO VERSION AND NO EFFORT.
// That is the mechanism behind "the same jobs, contexts, capabilities and
// receipts across every runtime": the binding digest is computed from the role,
// the job, the context digest, the capability references and the receipt
// binding, and from nothing else, so choosing Hermes instead of a cloud model
// cannot move a single byte of it. `bindEnvelopeToRoute` attaches the chosen
// route ALONGSIDE that digest and never inside it.
// ---------------------------------------------------------------------------

const JOB_KEYS = Object.freeze([
  "job_id", "task_class", "data_classes", "required_tool_ids", "risk_class",
  "context_digest", "capability_refs", "receipt_binding_ref", "intended_use",
  "minimum_strength_ref", "pinned_route",
]);
const JOB_REQUIRED = Object.freeze([
  "job_id", "task_class", "data_classes", "required_tool_ids", "risk_class",
  "context_digest", "capability_refs", "receipt_binding_ref",
]);

function assertJob(job, path = "job") {
  assertObject(job, path);
  assertClosedKeys(job, JOB_KEYS, path);
  assertRequiredKeys(job, JOB_REQUIRED, path);
  for (const key of ["job_id", "task_class", "risk_class", "receipt_binding_ref"]) {
    assertRef(job[key], `${path}.${key}`);
  }
  const dataClasses = assertRefSet(job.data_classes, `${path}.data_classes`);
  if (dataClasses.length === 0) {
    fail("invalid_shape", `${path}.data_classes must name at least one class`, { path: `${path}.data_classes` });
  }
  const tools = assertRefSet(job.required_tool_ids, `${path}.required_tool_ids`);
  const capabilityRefs = assertRefSet(job.capability_refs, `${path}.capability_refs`);
  assertSha256Ref(job.context_digest, `${path}.context_digest`);
  if (job.intended_use !== undefined && job.intended_use !== null) assertText(job.intended_use, `${path}.intended_use`);
  if (job.minimum_strength_ref !== undefined && job.minimum_strength_ref !== null) {
    assertRef(job.minimum_strength_ref, `${path}.minimum_strength_ref`);
  }
  let pinned = null;
  if (job.pinned_route !== undefined && job.pinned_route !== null) {
    pinned = assertOccupant(job.pinned_route, `${path}.pinned_route`);
  }
  return deepFreeze({
    ...job,
    data_classes: dataClasses,
    required_tool_ids: tools,
    capability_refs: capabilityRefs,
    intended_use: job.intended_use ?? null,
    minimum_strength_ref: job.minimum_strength_ref ?? null,
    pinned_route: pinned ? { ...pinned } : null,
  });
}

/**
 * The digest of the WHOLE checked job, not of the handful of fields the
 * envelope preimage spells out.
 *
 * A decision is only meaningful for the exact admission inputs it was made
 * over: change the tools, the data classes, the risk class, the context, the
 * capability set, the receipt binding, the minimum strength or the pin, and the
 * same `job_id` is a different question. This digest is what a decision, an
 * envelope and a bound envelope all carry, so a decision cannot be re-attached
 * to a second job that merely shares an id.
 */
function checkedJobDigest(job) {
  return digest({
    schema_version: V5_JOB_ENVELOPE_SCHEMA_VERSION,
    part: "checked_job",
    job: copy({ ...job }),
  });
}

/**
 * Build the runtime-independent job envelope.
 *
 * `binding_digest` is the value the adapter boundary carries across every
 * backend unchanged; nothing about the occupant contributes to it. `job_digest`
 * is inside the preimage, so the envelope's seal covers the whole checked job
 * and not only the five fields written out beside it.
 */
export function buildJobEnvelope({ role, job } = {}) {
  const sealed = assertSealedRole(role);
  const checked = assertJob(job);
  if (!sealed.task_classes.includes(checked.task_class)) {
    fail("task_class_outside_role",
      `role "${sealed.role_key}" does not carry task class "${checked.task_class}"`,
      { role_key: sealed.role_key, task_class: checked.task_class });
  }
  const missingCapabilities = checked.capability_refs
    .filter(ref => !sealed.authority.capability_refs.includes(ref));
  if (missingCapabilities.length > 0) {
    fail("job_exceeds_role_capabilities",
      "a job may draw only on capability references its role already declares",
      { role_key: sealed.role_key, missing: missingCapabilities });
  }
  const preimage = {
    schema_version: V5_JOB_ENVELOPE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    public_product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
    role: { role_key: sealed.role_key, role_digest: sealed.role_digest },
    job: {
      job_id: checked.job_id,
      task_class: checked.task_class,
      data_classes: [...checked.data_classes],
      required_tool_ids: [...checked.required_tool_ids],
      risk_class: checked.risk_class,
    },
    job_digest: checkedJobDigest(checked),
    context_digest: checked.context_digest,
    capability_refs: [...checked.capability_refs],
    receipt_binding_ref: checked.receipt_binding_ref,
  };
  const envelope = deepFreeze({
    ...preimage,
    binding_digest: digest(preimage),
    // Present and null on purpose: the route is chosen later and lives beside
    // the binding, never inside it.
    route: null,
    qualification_authenticated: false,
    backend_independent: true,
    effects: V5_NO_EFFECTS,
  });
  JOB_ENVELOPES.add(envelope);
  return envelope;
}

function assertEnvelope(envelope, path = "envelope") {
  assertObject(envelope, path);
  if (envelope.schema_version !== V5_JOB_ENVELOPE_SCHEMA_VERSION) {
    fail("envelope_schema_version_invalid", `${path}.schema_version must be ${V5_JOB_ENVELOPE_SCHEMA_VERSION}`,
      { path });
  }
  const recomputed = digest({
    schema_version: envelope.schema_version,
    tenant: envelope.tenant,
    public_product_identity: envelope.public_product_identity,
    role: envelope.role,
    job: envelope.job,
    job_digest: envelope.job_digest,
    context_digest: envelope.context_digest,
    capability_refs: envelope.capability_refs,
    receipt_binding_ref: envelope.receipt_binding_ref,
  });
  if (recomputed !== envelope.binding_digest) {
    fail("envelope_binding_digest_mismatch", `${path} does not hash to its own binding_digest`,
      { expected: recomputed, actual: envelope.binding_digest });
  }
  return envelope;
}

/**
 * Attach a selected route to an envelope without touching its binding.
 *
 * FOUR THINGS MUST HOLD, and none of them is a flag on the input:
 *   * the envelope is one THIS module built (object identity, not digest);
 *   * the decision is one THIS module's gate produced over authenticated
 *     evidence (object identity again, so a hand-built object carrying
 *     `qualification_authenticated: true` is refused with its own code);
 *   * the decision selected a route rather than refusing;
 *   * the decision was made over THIS EXACT JOB — same role digest, same
 *     `job_id`, and the same digest over the whole checked job, so a decision
 *     made for one set of tools, data classes, risk class, context, capability
 *     set, minimum strength or pin cannot be re-used for another.
 */
export function bindEnvelopeToRoute({ envelope, routing_result } = {}) {
  if (!isJobEnvelope(envelope)) {
    fail("job_envelope_not_locally_produced",
      "only an envelope buildJobEnvelope produced in this process may be bound; a reconstructed or already-bound object is refused however it hashes",
      { provenance_scope: V5_PROVENANCE_SCOPE });
  }
  assertEnvelope(envelope);
  assertObject(routing_result, "routing_result");
  if (routing_result.schema_version !== V5_ROUTING_RESULT_SCHEMA_VERSION) {
    fail("routing_result_schema_version_invalid", "routing_result is not a model-route-decision.v1");
  }
  if (routing_result.qualification_authenticated !== true) {
    fail("unauthenticated_route_refused",
      "only a route selected over authenticated qualification evidence may be bound to an envelope",
      { qualification_status: routing_result.qualification_status ?? null });
  }
  // The flag above is checked first only so an honest unauthenticated PROPOSAL
  // gets the refusal that names its actual status. It proves nothing on its
  // own: any object can carry it, which is what this check is for.
  if (!isAuthenticRoutingDecision(routing_result)) {
    fail("routing_decision_not_locally_produced",
      "a routing decision binds an envelope only if this module's gate produced it in this process; a matching shape, a matching flag and a matching digest are not provenance",
      { provenance_scope: V5_PROVENANCE_SCOPE });
  }
  if (routing_result.decision !== "route" || !routing_result.selected_route) {
    fail("routing_result_not_a_route", "routing_result did not select a route",
      { decision: routing_result.decision, reason_id: routing_result.reason_id });
  }
  if (routing_result.job_id !== envelope.job.job_id || routing_result.role_digest !== envelope.role.role_digest) {
    fail("routing_result_binding_mismatch", "routing_result belongs to a different job or role",
      { job_id: routing_result.job_id, role_digest: routing_result.role_digest });
  }
  if (routing_result.job_digest !== envelope.job_digest) {
    fail("routing_result_binding_mismatch",
      "routing_result was decided over different job bytes; the job id matching is not enough, because tools, data classes, risk, context, capabilities, minimum strength and any pin are all admission inputs",
      { expected: envelope.job_digest, actual: routing_result.job_digest ?? null });
  }
  const bound = deepFreeze({
    ...envelope,
    route: { ...routing_result.selected_route },
    selected_qualification_id: routing_result.selected_qualification_id,
    // The evidence the decision actually rests on, carried so a receipt built
    // from this envelope can cite the exact measurement bytes and the verifier
    // that attested them rather than only a qualification id.
    selected_measurement_digest: routing_result.selected_measurement_digest,
    selected_verifier_id: routing_result.selected_verifier_id,
    policy_digest: routing_result.policy_digest,
    quality_scale_id: routing_result.quality_scale_id,
    quality_scale_version: routing_result.quality_scale_version,
    qualification_authenticated: true,
    // Unchanged by construction; asserted here so a reader does not have to
    // take the spread on faith.
    binding_digest: envelope.binding_digest,
    job_digest: envelope.job_digest,
    backend_independent: true,
    effects: V5_NO_EFFECTS,
  });
  ROUTE_BOUND_ENVELOPES.add(bound);
  return bound;
}

// ---------------------------------------------------------------------------
// The prompt payload boundary.
//
// Secrets and authority never enter a model prompt. The check is STRUCTURAL, not
// a matter of trust or scanning: the payload's schema is closed to
// `{part_id, data_class, text}` parts, every part's data class must be permitted
// for the task class and the backend, and a part or payload carrying a field
// whose NAME reaches into secrets, credentials, capabilities or authority is
// refused by name before anything is hashed.
//
// This boundary cannot inspect the meaning of `text`, and does not pretend to.
// What it guarantees is that no field of this payload is a place authority or a
// credential could be carried, and that the data classes are admitted ones.
// ---------------------------------------------------------------------------

const PROMPT_PART_KEYS = Object.freeze(["part_id", "data_class", "text"]);
const PROMPT_PAYLOAD_KEYS = Object.freeze(["parts"]);
const FORBIDDEN_PAYLOAD_FRAGMENTS = deepFreeze([
  "secret", "credential", "password", "passphrase", "token", "api_key", "apikey",
  "private_key", "session_key", "authorization", "authority", "capability", "capabilities",
  "grant", "delegation", "redecision", "permission", "privilege", "override", "escalate",
]);

function assertNoForbiddenFields(object, path) {
  for (const key of Object.keys(object)) {
    const normalized = key.toLowerCase();
    const hit = FORBIDDEN_PAYLOAD_FRAGMENTS.find(fragment => normalized.includes(fragment));
    if (hit) {
      fail("secret_or_authority_in_prompt_refused",
        "no secret and no authority payload may enter a model prompt",
        { path: `${path}.${key}`, field: key, fragment: hit });
    }
  }
}

/**
 * Build the payload the occupant is allowed to see for one bound envelope.
 * The envelope's capability references and receipt binding are deliberately NOT
 * copied in: the model is told what to work on, never what it may do.
 */
export function buildPromptPayload({ policy, bound_envelope, payload } = {}) {
  const compiled = assertCompiledPolicy(policy);
  if (!isRouteBoundEnvelope(bound_envelope)) {
    fail("bound_envelope_not_locally_produced",
      "a prompt is built only for an envelope bindEnvelopeToRoute produced in this process; a spread copy with an edited route is a different object and is refused",
      { provenance_scope: V5_PROVENANCE_SCOPE });
  }
  assertEnvelope(bound_envelope, "bound_envelope");
  if (!bound_envelope.route) fail("routing_result_not_a_route", "bound_envelope carries no route");
  // The policy is an argument, and `compileRoutingPolicy` is public — so a
  // caller could otherwise hand in a policy of their own whose backends permit
  // everything and walk straight past the admission below. The prompt is built
  // under the EXACT policy the route was decided under or not at all; a changed
  // policy means the routing question has to be asked again.
  if (bound_envelope.policy_digest !== compiled.policy_digest) {
    fail("policy_binding_mismatch",
      "this prompt would be admitted under a different policy than the one the route was decided under",
      { expected: bound_envelope.policy_digest, actual: compiled.policy_digest });
  }
  assertObject(payload, "payload");
  assertNoForbiddenFields(payload, "payload");
  assertClosedKeys(payload, PROMPT_PAYLOAD_KEYS, "payload");
  assertRequiredKeys(payload, PROMPT_PAYLOAD_KEYS, "payload");
  if (!Array.isArray(payload.parts) || payload.parts.length === 0) {
    fail("invalid_shape", "payload.parts must carry at least one part", { path: "payload.parts" });
  }
  if (!hasOwn(compiled.task_classes, bound_envelope.job.task_class)) {
    fail("unknown_task_class", "the bound envelope's task class is not declared by this policy",
      { task_class: bound_envelope.job.task_class });
  }
  const taskClass = compiled.task_classes[bound_envelope.job.task_class];
  if (!hasOwn(compiled.backends, bound_envelope.route.backend_key)) {
    fail("unknown_backend_key", "the bound route names a backend this policy does not declare",
      { backend_key: bound_envelope.route.backend_key });
  }
  const backend = compiled.backends[bound_envelope.route.backend_key];
  const seenParts = new Set();
  const parts = payload.parts.map((part, index) => {
    const path = `payload.parts[${index}]`;
    assertObject(part, path);
    assertNoForbiddenFields(part, path);
    assertClosedKeys(part, PROMPT_PART_KEYS, path);
    assertRequiredKeys(part, PROMPT_PART_KEYS, path);
    assertRef(part.part_id, `${path}.part_id`);
    if (seenParts.has(part.part_id)) fail("duplicate_entry", `${path}.part_id is repeated`, { path });
    seenParts.add(part.part_id);
    assertRef(part.data_class, `${path}.data_class`);
    if (typeof part.text !== "string") fail("invalid_shape", `${path}.text must be a string`, { path });
    if (!bound_envelope.job.data_classes.includes(part.data_class)) {
      fail("prompt_data_class_outside_job",
        `${path}.data_class is not one the job declared`,
        { path, data_class: part.data_class, declared: [...bound_envelope.job.data_classes] });
    }
    if (!taskClass.permitted_data_classes.includes(part.data_class)) {
      fail("prompt_data_class_outside_task_class", `${path}.data_class is not permitted for this task class`,
        { path, data_class: part.data_class });
    }
    if (!backend.permitted_data_classes.includes(part.data_class)) {
      fail("prompt_data_class_outside_backend_policy",
        `${path}.data_class is outside the backend's declared data policy`,
        { path, data_class: part.data_class, backend_key: backend.backend_key });
    }
    return { part_id: part.part_id, data_class: part.data_class, text: part.text };
  });
  // The preimage stays backend- and policy-independent on purpose: the same job
  // asked of three different backends must produce the same prompt bytes, which
  // is half of "the same jobs and contexts across every runtime". The policy
  // this payload was admitted under rides BESIDE the digest and is checked at
  // the adapter boundary; its integrity rests on the provenance register above,
  // not on the hash — which is the honest description either way, since the
  // preimage is public.
  const preimage = {
    schema_version: V5_PROMPT_PAYLOAD_SCHEMA_VERSION,
    envelope_binding_digest: bound_envelope.binding_digest,
    parts: [...parts].sort((a, b) => (a.part_id < b.part_id ? -1 : 1)),
  };
  const built = deepFreeze({
    ...preimage,
    payload_digest: digest(preimage),
    policy_digest: compiled.policy_digest,
    carries_secrets: false,
    carries_authority: false,
    capability_refs_included: false,
    effects: V5_NO_EFFECTS,
  });
  VALIDATED_PROMPT_PAYLOADS.add(built);
  return built;
}

// ---------------------------------------------------------------------------
// Q031 / Q047 — admission and deterministic selection.
// ---------------------------------------------------------------------------

/**
 * Find the qualification for one exact route, or say precisely why none matched.
 *
 * The near-miss reasons matter: "there is a record for this model at a different
 * version" is a different operational fact from "this model has never been
 * evaluated for this task", and a reviewer reading a refusal needs to be able to
 * tell them apart without re-deriving it.
 */
function matchQualification(route, qualifications) {
  const exact = qualifications.find(q =>
    q.task_class === route.task_class && q.backend_key === route.backend_key &&
    q.model_key === route.model_key && q.model_version === route.model_version &&
    q.effort === route.effort);
  if (exact) return { match: exact };
  const sameModel = qualifications.filter(q => q.model_key === route.model_key);
  if (sameModel.some(q => q.task_class !== route.task_class &&
      q.model_version === route.model_version && q.effort === route.effort)) {
    return { reason_id: "qualification_task_class_mismatch" };
  }
  const sameTask = sameModel.filter(q => q.task_class === route.task_class);
  if (sameTask.some(q => q.backend_key !== route.backend_key)) {
    return { reason_id: "qualification_backend_mismatch" };
  }
  if (sameTask.some(q => q.model_version !== route.model_version)) {
    return { reason_id: "qualification_model_version_mismatch" };
  }
  if (sameTask.some(q => q.effort !== route.effort)) {
    return { reason_id: "qualification_effort_mismatch" };
  }
  return { reason_id: "no_qualification_record" };
}

/**
 * Admit or refuse one candidate route. Checks run in a FIXED order and the first
 * failure wins, so the reported reason is a property of the request rather than
 * of iteration order.
 */
function admitRoute({ route, compiled, taskClass, job, requiredStrengthRank, requiredFloors,
  qualifications, nowMs, localNodeStates }) {
  const backend = compiled.backends[route.backend_key];
  const base = {
    route_key: route.route_key, backend_key: route.backend_key, backend_kind: backend.kind,
    model_key: route.model_key, model_version: route.model_version, effort: route.effort,
  };
  const refuse = (reason_id, detail) =>
    ({ ...base, admitted: false, reason_id, qualification_id: null, ranking: null, ...detail });

  if (!taskClass.permitted_backend_keys.includes(route.backend_key)) {
    return refuse("backend_not_permitted_for_task_class");
  }
  const backendDataGap = job.data_classes.filter(c => !backend.permitted_data_classes.includes(c));
  if (backendDataGap.length > 0) {
    return refuse("backend_data_policy_excludes_data_class", { missing_data_classes: backendDataGap });
  }
  if (taskClass.privacy_restriction === "local_only" && backend.kind !== "local") {
    return refuse("local_only_privacy_restriction");
  }
  // Strength is the position, on the ONE declared scale, of the grade the
  // policy gives this exact model@version/effort — compared against the
  // position of the grade the required minimum-strength reference resolves to
  // on that same scale. Cost is not consulted here and cannot be: an
  // under-strength route is refused before any price is looked at.
  const qualityRank = lookupGradeRank(
    compiled.occupant_grades, route.occupant_key, "policy.occupant_grades", compiled.quality_scale);
  if (qualityRank < requiredStrengthRank) {
    return refuse("below_required_strength", {
      quality_rank: qualityRank, required_strength_rank: requiredStrengthRank,
      occupant_grade: compiled.occupant_grades[route.occupant_key],
      quality_scale_id: compiled.quality_scale.scale_id,
    });
  }
  const matched = matchQualification(route, qualifications);
  if (!matched.match) return refuse(matched.reason_id);
  const qualification = matched.match;
  const withId = detail => ({ ...detail, qualification_id: qualification.qualification_id });

  if (nowMs < qualification.measured_at_ms) {
    return { ...refuse("qualification_not_yet_effective"), ...withId({}) };
  }
  if (nowMs >= qualification.expires_at_ms) {
    return { ...refuse("qualification_expired"), ...withId({ expires_at: qualification.expires_at }) };
  }
  if (compiled.maximum_qualification_age_ms !== null &&
      nowMs - qualification.measured_at_ms > compiled.maximum_qualification_age_ms) {
    return { ...refuse("qualification_stale_for_policy"), ...withId({ measured_at: qualification.measured_at }) };
  }
  const toolGap = job.required_tool_ids.filter(t => !qualification.qualified_tool_ids.includes(t));
  if (toolGap.length > 0) {
    return { ...refuse("tool_not_qualified"), ...withId({ missing_tool_ids: toolGap }) };
  }
  const dataGap = job.data_classes.filter(c => !qualification.permitted_data_classes.includes(c));
  if (dataGap.length > 0) {
    return { ...refuse("data_class_not_qualified"), ...withId({ missing_data_classes: dataGap }) };
  }
  const jobRisk = lookupRank(compiled.risk_ranks, job.risk_class, "policy.risk_ranks");
  const coveredRisk = lookupRank(compiled.risk_ranks, qualification.qualified_max_risk_class, "policy.risk_ranks");
  if (coveredRisk < jobRisk) {
    return { ...refuse("risk_class_not_qualified"), ...withId({
      job_risk_class: job.risk_class, qualified_max_risk_class: qualification.qualified_max_risk_class,
    }) };
  }
  const floorGap = requiredFloors.filter(f => !qualification.met_quality_floor_refs.includes(f));
  if (floorGap.length > 0) {
    return { ...refuse("quality_floor_not_met"), ...withId({ missing_quality_floor_refs: floorGap }) };
  }
  // Q047 / S01: whether a local node can actually run this is S01's question,
  // answered by S01's evaluator. Only an execution on the node itself admits the
  // LOCAL route; S01's cloud-fallback disposition is a statement about the
  // capability, not permission to run this route somewhere else, so the fallback
  // has to be a different qualified route chosen by the ranking below.
  let localState = null;
  if (backend.kind === "local") {
    localState = hasOwn(localNodeStates, backend.local_node)
      ? localNodeStates[backend.local_node] : undefined;
    if (localState === undefined) {
      return { ...refuse("local_node_state_unknown"), ...withId({ local_node: backend.local_node }) };
    }
    const platform = evaluateLocalPlatform({
      node: backend.local_node, node_state: localState, capability: taskClass.local_capability,
    });
    if (platform.execution !== "local_node") {
      return { ...refuse("local_route_not_executable"), ...withId({
        local_node: backend.local_node, local_node_state: localState,
        local_platform_decision: platform.decision, local_platform_reason_id: platform.reason_id,
      }) };
    }
  }
  return {
    ...base,
    admitted: true,
    reason_id: "route_qualified",
    qualification_id: qualification.qualification_id,
    // The evidence itself, not just its id: which measurement bytes, and which
    // verifier attested them.
    measurement_digest: qualification.measurement_digest,
    verifier_id: qualification.verifier_id,
    occupant_grade: compiled.occupant_grades[route.occupant_key],
    local_node: backend.local_node,
    local_node_state: localState,
    ranking: Object.freeze({
      quality_rank: qualityRank,
      residual_risk_rank: lookupRank(compiled.risk_ranks, route.residual_risk_class, "policy.risk_ranks"),
      privacy_rank: lookupRank(compiled.privacy_ranks, backend.egress_class, "policy.privacy_ranks"),
      latency_ms_p95: qualification.measured_latency_ms_p95,
      expected_total_cost_units: route.expected_total_cost_units,
      local_preference_rank: taskClass.local_preference_enabled && backend.kind === "local" ? 0 : 1,
    }),
  };
}

/** The total order: policy key order, fixed directions, `route_key` as the final tiebreak. */
function compareCandidates(keyOrder) {
  return (a, b) => {
    for (const key of keyOrder) {
      const left = a.ranking[key];
      const right = b.ranking[key];
      if (left !== right) {
        return DESCENDING_RANKING_KEYS.includes(key) ? (right - left) : (left - right);
      }
    }
    return a.route_key < b.route_key ? -1 : 1;
  };
}

const REQUEST_KEYS = Object.freeze([
  "policy", "role", "job", "now", "local_node_states", "qualifications",
]);
const REQUEST_REQUIRED = Object.freeze(["policy", "role", "job", "now"]);

function assertLocalNodeStates(value, path) {
  // Null-prototype for the same reason the policy maps are: an absent node
  // state must read as absent, never as something inherited.
  if (value === undefined || value === null) return Object.freeze(Object.create(null));
  assertObject(value, path);
  for (const key of Object.keys(value)) {
    if (!V5_OPTIONAL_LOCAL_NODES.includes(key)) {
      fail("unknown_local_node", `${path}.${key} is not a registered optional local node`,
        { path, registered: [...V5_OPTIONAL_LOCAL_NODES] });
    }
    if (!V5_LOCAL_NODE_STATES.includes(value[key])) {
      fail("unknown_local_node_state", `${path}.${key} is not a registered local node state`,
        { path: `${path}.${key}`, registered: [...V5_LOCAL_NODE_STATES] });
    }
  }
  return Object.freeze(Object.assign(Object.create(null), value));
}

/**
 * The single selection predicate. Both the authenticated gate and the
 * unauthenticated proposal path run THIS function over the same inputs, so the
 * only difference between a proposal and a decision is where the qualification
 * records came from — never how they were judged.
 */
function selectRoute({ request, qualifications, authenticated }) {
  assertObject(request, "request");
  assertClosedKeys(request, REQUEST_KEYS, "request");
  assertRequiredKeys(request, REQUEST_REQUIRED, "request");
  const compiled = assertCompiledPolicy(request.policy);
  const role = assertSealedRole(request.role);
  const job = assertJob(request.job);
  const nowMs = assertInstant(request.now, "request.now");
  const localNodeStates = assertLocalNodeStates(request.local_node_states, "request.local_node_states");
  const checked = qualifications.map((record, index) =>
    assertQualificationRecord(record, `qualifications[${index}]`));
  const qualificationIds = checked.map(q => q.qualification_id);
  if (new Set(qualificationIds).size !== qualificationIds.length) {
    fail("duplicate_entry", "two qualification records share one qualification_id");
  }
  // TWO RECORDS FOR ONE EXACT ROUTE ARE AN AMBIGUOUS PROJECTION, NOT A CHOICE
  // TO MAKE HERE. `matchQualification` takes the first exact match and the
  // matched record's measured latency feeds the ranking, so two different
  // records measured for the same task class, backend, model, version and
  // effort would make both the reported reason and the selected route depend on
  // the order the verifier happened to return them in.
  //
  // The two obvious repairs are both refusals of a different kind. "Latest
  // measured_at wins" would invent a freshness authority this module was never
  // granted — the record layer decides which measurement is current, and a
  // clock skew or a backdated re-measurement would silently pick the wrong one.
  // Dropping the older record would throw away a receipt that is still evidence
  // of something. So the ambiguity is refused by name and the PRODUCER upstream
  // chooses which measurement is authoritative before this module reads it;
  // nothing is discarded here and no history is lost by refusing.
  const byRouteTuple = new Map();
  for (const record of checked) {
    const tuple = [record.task_class, record.backend_key, record.model_key,
      record.model_version, record.effort].join("|");
    if (!byRouteTuple.has(tuple)) byRouteTuple.set(tuple, []);
    byRouteTuple.get(tuple).push(record.qualification_id);
  }
  // Sorted, so the refusal a caller sees is a property of the SET of records
  // rather than of the order they arrived in.
  for (const [tuple, ids] of [...byRouteTuple.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
    if (ids.length > 1) {
      fail("ambiguous_qualification_projection",
        "two different qualification records are measured for one exact task-class/backend/model/version/effort route; which one is current is the producer's decision, and this module will not pick between them by array order or by inventing a latest-proof-wins rule",
        { route_tuple: tuple, qualification_ids: [...ids].sort() });
    }
  }

  const common = {
    schema_version: V5_ROUTING_RESULT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    policy_id: compiled.policy_id,
    policy_version: compiled.policy_version,
    policy_digest: compiled.policy_digest,
    role_key: role.role_key,
    role_digest: role.role_digest,
    job_id: job.job_id,
    // The whole checked job, not just its id: an envelope binds to this value,
    // so a decision cannot be re-used for a second job that shares a job id but
    // differs in tools, data classes, risk class, context, capabilities,
    // minimum strength or pin.
    job_digest: checkedJobDigest(job),
    task_class: job.task_class,
    quality_scale_id: compiled.quality_scale.scale_id,
    quality_scale_version: compiled.quality_scale.scale_version,
    now: request.now,
    qualification_authenticated: authenticated,
    qualification_status: authenticated
      ? "authenticated_by_trusted_projection_verifier"
      : "schema_valid_proposal_only",
    // Said on the object, because the difference between the two paths is
    // exactly where the records came from. Only the first can bind an envelope,
    // and binding checks the object's provenance rather than this string.
    provenance: authenticated
      ? "gate_verified_in_process"
      : "public_offline_proposal_unauthenticated",
    // Never true anywhere in this file. There is no field, option or code path
    // that accepts a weaker model than the job requires.
    downgraded_from_required_strength: false,
    cost_can_qualify: false,
    backend_grants_authority: false,
    canonical_authority: V5_CANONICAL_AUTHORITY,
    public_product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
    dispatch_implemented: false,
    effects: V5_NO_EFFECTS,
  };
  const answer = fields => deepFreeze({
    ...common,
    selected_route: null, selected_qualification_id: null,
    selected_measurement_digest: null, selected_verifier_id: null,
    selected_quality_grade: null, fallback: null,
    considered: [], admissible_for_dispatch: false,
    ...fields,
  });

  // Request-level admission first: these refusals are about the REQUEST, and
  // none of them is a statement about any route's qualification.
  if (!role.task_classes.includes(job.task_class)) {
    return answer({ decision: "refuse", reason_id: "task_class_outside_role" });
  }
  const capabilityGap = job.capability_refs.filter(ref => !role.authority.capability_refs.includes(ref));
  if (capabilityGap.length > 0) {
    return answer({
      decision: "refuse", reason_id: "job_exceeds_role_capabilities",
      missing_capability_refs: capabilityGap,
    });
  }
  if (!hasOwn(compiled.task_classes, job.task_class)) {
    fail("unknown_task_class", `"${job.task_class}" is not declared by this policy`,
      { task_class: job.task_class });
  }
  const taskClass = compiled.task_classes[job.task_class];
  // S01 owns the PHI boundary; this module asks it rather than re-deciding it.
  const privacy = evaluatePrivacyBoundary({
    data_classes: [...job.data_classes],
    ...(job.intended_use ? { intended_use: job.intended_use } : {}),
  });
  if (privacy.decision === "refuse") {
    return answer({
      decision: "refuse", reason_id: "data_class_prohibited_by_global_boundary",
      global_boundary_reason_id: privacy.reason_id,
    });
  }
  if (privacy.decision === "needs_independent_privacy_route") {
    return answer({
      decision: "refuse", reason_id: "data_class_needs_independent_privacy_route",
      global_boundary_reason_id: privacy.reason_id, required_evidence: privacy.required_evidence,
    });
  }
  const taskDataGap = job.data_classes.filter(c => !taskClass.permitted_data_classes.includes(c));
  if (taskDataGap.length > 0) {
    return answer({
      decision: "refuse", reason_id: "data_class_outside_task_class",
      missing_data_classes: taskDataGap,
    });
  }

  // Required strength. The role's floor is the baseline; a job may raise it and
  // may never lower it, because lowering it in the request is exactly the silent
  // downgrade the decision forbids. Both references resolve to a NAMED GRADE on
  // the policy's one declared scale, and the comparison below is between two
  // positions in that single ordering.
  const roleStrengthRank = lookupGradeRank(
    compiled.strength_refs, role.minimum_strength_ref, "policy.strength_refs", compiled.quality_scale);
  let requiredStrengthRank = roleStrengthRank;
  let requiredStrengthRef = role.minimum_strength_ref;
  if (job.minimum_strength_ref) {
    const jobRank = lookupGradeRank(
      compiled.strength_refs, job.minimum_strength_ref, "policy.strength_refs", compiled.quality_scale);
    if (jobRank < roleStrengthRank) {
      return answer({
        decision: "refuse", reason_id: "job_lowers_role_minimum_strength",
        role_minimum_strength_ref: role.minimum_strength_ref, job_minimum_strength_ref: job.minimum_strength_ref,
        required_strength_rank: roleStrengthRank,
        required_strength_grade: compiled.strength_refs[role.minimum_strength_ref],
      });
    }
    requiredStrengthRank = jobRank;
    requiredStrengthRef = job.minimum_strength_ref;
  }
  const requiredFloors = [...new Set([
    ...role.quality_floor_refs, ...taskClass.required_quality_floor_refs,
  ])].sort();

  const candidates = compiled.routes
    .filter(route => route.task_class === job.task_class)
    .map(route => admitRoute({
      route, compiled, taskClass, job, requiredStrengthRank, requiredFloors,
      qualifications: checked, nowMs, localNodeStates,
    }));
  const considered = deepFreeze(
    [...candidates].sort((a, b) => (a.route_key < b.route_key ? -1 : 1))
      .map(entry => ({ ...entry })));
  const withContext = fields => answer({
    ...fields,
    considered,
    required_strength_rank: requiredStrengthRank,
    required_strength_ref: requiredStrengthRef,
    required_strength_grade: compiled.strength_refs[requiredStrengthRef],
    required_quality_floor_refs: requiredFloors,
    privacy_restriction: taskClass.privacy_restriction,
    ranking_key_order: [...compiled.ranking_key_order],
  });

  if (candidates.length === 0) {
    return withContext({ decision: "unavailable", reason_id: "no_route_declared_for_task_class" });
  }
  const admitted = candidates.filter(entry => entry.admitted);

  // Q031: an exact pin is honoured exactly. It never falls back, never
  // substitutes and never downgrades — it selects that route or it refuses.
  if (job.pinned_route) {
    const pin = job.pinned_route;
    const pinned = candidates.find(entry =>
      entry.backend_key === pin.backend_key && entry.model_key === pin.model_key &&
      entry.model_version === pin.model_version && entry.effort === pin.effort);
    if (!pinned) {
      return withContext({
        decision: "refuse", reason_id: "pinned_route_not_in_policy", pinned_route: { ...pin },
      });
    }
    if (!pinned.admitted) {
      return withContext({
        decision: "refuse", reason_id: "pinned_route_not_qualified",
        pinned_route: { ...pin }, pin_refusal_reason_id: pinned.reason_id,
      });
    }
    return withContext({
      decision: "route", reason_id: "pinned_route_qualified",
      selected_route: selectedRouteView(pinned, compiled),
      selected_qualification_id: pinned.qualification_id,
      selected_measurement_digest: pinned.measurement_digest,
      selected_verifier_id: pinned.verifier_id,
      selected_quality_grade: pinned.occupant_grade,
      selected_strength_rank: pinned.ranking.quality_rank,
      pinned_route: { ...pin },
      admissible_for_dispatch: authenticated,
    });
  }

  if (admitted.length === 0) {
    // An honest unavailable, and the reason distinguishes the three cases a
    // reader actually needs: a privacy restriction with nowhere permitted to go,
    // a weaker route deliberately left unused, and simply nothing current.
    if (taskClass.privacy_restriction === "local_only") {
      return withContext({
        decision: "unavailable",
        reason_id: "local_only_privacy_restriction_no_permitted_fallback",
        privacy_breach_avoided: true,
      });
    }
    if (candidates.some(entry => entry.reason_id === "below_required_strength")) {
      return withContext({
        decision: "unavailable", reason_id: "no_qualified_route_at_required_strength",
        privacy_breach_avoided: false,
      });
    }
    return withContext({
      decision: "unavailable", reason_id: "no_qualified_route_available",
      privacy_breach_avoided: false,
    });
  }

  const ranked = [...admitted].sort(compareCandidates(compiled.ranking_key_order));
  const selected = ranked[0];

  // Q047: when the task class prefers local and a local route was refused only
  // because the node could not run it, the fallback is stated explicitly rather
  // than being an unremarked change of backend.
  let fallback = null;
  if (taskClass.local_preference_enabled && compiled.backends[selected.backend_key].kind !== "local") {
    const lostLocal = candidates
      .filter(entry => entry.backend_kind === "local" && !entry.admitted &&
        (entry.reason_id === "local_route_not_executable" || entry.reason_id === "local_node_state_unknown"))
      .sort((a, b) => (a.route_key < b.route_key ? -1 : 1))[0];
    if (lostLocal) {
      fallback = Object.freeze({
        from_route_key: lostLocal.route_key,
        from_reason_id: lostLocal.reason_id,
        to_route_key: selected.route_key,
        explicit: true,
        privacy_restriction_respected: true,
        data_policy_respected: true,
      });
    }
  }

  return withContext({
    decision: "route",
    reason_id: fallback ? "qualified_permitted_fallback_selected" : "highest_ranked_qualified_route",
    selected_route: selectedRouteView(selected, compiled),
    selected_qualification_id: selected.qualification_id,
    selected_measurement_digest: selected.measurement_digest,
    selected_verifier_id: selected.verifier_id,
    selected_quality_grade: selected.occupant_grade,
    selected_strength_rank: selected.ranking.quality_rank,
    fallback,
    admissible_for_dispatch: authenticated,
  });
}

function selectedRouteView(entry, compiled) {
  const route = compiled.routes.find(r => r.route_key === entry.route_key);
  return Object.freeze({
    route_key: entry.route_key,
    backend_key: entry.backend_key,
    backend_kind: entry.backend_kind,
    local_node: entry.local_node ?? null,
    model_key: entry.model_key,
    model_version: entry.model_version,
    effort: entry.effort,
    occupant_key: route.occupant_key,
    occupant_grade: route.occupant_grade,
    residual_risk_class: route.residual_risk_class,
    expected_total_cost_units: route.expected_total_cost_units,
    cost_components: route.cost_components,
    ranking: entry.ranking,
  });
}

/**
 * Rank and explain a route WITHOUT authenticating anything.
 *
 * Every record is schema-checked and the selection is the same one the gate
 * performs, but nothing here establishes that the records are real. The result
 * says so on `qualification_authenticated`, `qualification_status`, `provenance`
 * and `admissible_for_dispatch`; it is never branded as gate-produced, and
 * `bindEnvelopeToRoute` refuses it on both counts — so a proposal is useful for
 * review and planning and cannot become a route. This function is public and
 * offline on purpose: anyone may rank routes and nobody may authenticate one
 * this way.
 */
export function proposeRoutePlan(request) {
  assertObject(request, "request");
  const supplied = request.qualifications;
  if (!Array.isArray(supplied)) {
    fail("invalid_shape", "request.qualifications must be an array of proposed qualification records",
      { path: "request.qualifications" });
  }
  return selectRoute({ request, qualifications: supplied, authenticated: false });
}

/**
 * The exact bytes a verifier binds. The policy contributes only its DIGEST, so
 * the binding is over the request's identity rather than over an inlined policy
 * document that could differ byte-for-byte while meaning the same thing.
 */
function requestBindingPreimage(request) {
  assertObject(request, "request");
  assertClosedKeys(request, REQUEST_KEYS, "request");
  assertRequiredKeys(request, REQUEST_REQUIRED, "request");
  const compiled = assertCompiledPolicy(request.policy);
  const role = assertSealedRole(request.role);
  const job = assertJob(request.job);
  assertInstant(request.now, "request.now");
  return deepFreeze({
    schema_version: V5_ROUTING_SCHEMA_VERSION,
    policy_digest: compiled.policy_digest,
    role_digest: role.role_digest,
    job: copy({ ...job }),
    now: request.now,
    local_node_states: copy(assertLocalNodeStates(request.local_node_states, "request.local_node_states")),
  });
}

/**
 * Install the trusted projection verifier and get the routing gate.
 *
 * `authenticateQualifications` is SERVER CODE, never a tool argument, and it is
 * the only way to reach an authenticated decision. It receives the exact request
 * and must return `{ request_binding_digest, qualifications }`: the digest binds
 * the authentication to the bytes it was given, and the records are the
 * measurements the verifier read from the record layer. A verifier that returns
 * records for a different request is refused rather than trusted, which is what
 * stops an authentication being replayed onto a second job.
 *
 * `trusted_verifier_ids` IS REQUIRED AND EXPLICIT. A qualification record names
 * the verifier that attested it, and the gate accepts only the identities it was
 * configured with — an installed verifier handing back a record attributed to
 * some other authority is "undeclared authority", and it refuses. There is no
 * self-certifying field and no wildcard: an empty or missing list is a
 * configuration refusal, not "accept everything".
 *
 * THE VERIFIER IS SYNCHRONOUS, DELIBERATELY. This gate cannot await anything —
 * it is a pure function of its inputs — so a verifier that returns a promise is
 * refused by name rather than silently treated as an object. A real record-layer
 * read behind this seam therefore has to be a synchronous read of an
 * already-loaded projection; that constraint is V5-F06/V5-F07's to carry and is
 * named here rather than hidden.
 *
 * The gate cannot dispatch, cannot call a provider and cannot write anything;
 * its only product is a frozen decision, branded as this module's own.
 */
export function createModelRoutingGate({ authenticateQualifications, trusted_verifier_ids } = {}) {
  if (typeof authenticateQualifications !== "function") {
    fail("authenticated_verifier_required",
      "route qualification must come from an installed trusted projection verifier, never from the caller");
  }
  if (!Array.isArray(trusted_verifier_ids) || trusted_verifier_ids.length === 0) {
    fail("trusted_verifier_ids_required",
      "configure the exact verifier identities this gate accepts; there is no wildcard and a record is never trusted because it says it should be",
      { path: "trusted_verifier_ids" });
  }
  const trusted = Object.freeze(assertRefSet(trusted_verifier_ids, "trusted_verifier_ids"));
  return Object.freeze({
    trusted_verifier_ids: trusted,
    evaluate(request) {
      assertObject(request, "request");
      assertClosedKeys(request, REQUEST_KEYS, "request");
      if (request.qualifications !== undefined) {
        fail("caller_supplied_qualifications_refused",
          "the gate reads qualifications from its verifier; a caller may not supply them",
          { path: "request.qualifications" });
      }
      // Before re-deriving anything: an absent key is a `missing_field` naming
      // that key, and stays one rather than becoming an `invalid_shape` from
      // whichever re-derivation below happened to read it first.
      assertRequiredKeys(request, REQUEST_REQUIRED, "request");
      // CANONICALIZED, RE-DERIVED AND FROZEN BEFORE ANYTHING READS IT. The
      // binding digest, the verifier and the selection must all see the same
      // bytes, and ALL FOUR of these arrive as caller-supplied objects.
      //
      // The policy and the role are not exempt just because this module can
      // produce them: `assertCompiledPolicy` and `assertSealedRole` admit any
      // structurally valid look-alike by RE-DERIVATION rather than by object
      // identity, so an ordinary caller may legitimately pass a mutable clone.
      // Without this, a verifier could bind its digest over the role it was
      // handed and then re-seal a weaker `minimum_strength_ref` onto that same
      // object before `selectRoute` re-derives it — the one privilege records
      // alone cannot reach, because a record never sets a floor. Re-deriving
      // here replaces each input with this module's own deep-frozen product, so
      // the required strength and the route table are fixed before the verifier
      // is called and cannot move under it.
      //
      // Each is guarded on presence so a missing key still reports its own
      // `missing_field` below rather than an `invalid_shape` from re-derivation,
      // and each re-derivation returns a NEW object, so the caller's own policy,
      // role, job and node states are left untouched and unfrozen.
      const safeRequest = Object.freeze({
        ...request,
        ...(request.policy !== undefined ? { policy: assertCompiledPolicy(request.policy) } : {}),
        ...(request.role !== undefined ? { role: assertSealedRole(request.role) } : {}),
        ...(request.job !== undefined ? { job: deepFreeze(copy(request.job)) } : {}),
        ...(request.local_node_states !== undefined
          ? { local_node_states: deepFreeze(copy(request.local_node_states)) }
          : {}),
      });
      const input = requestBindingPreimage(safeRequest);
      const verified = authenticateQualifications(safeRequest);
      if (verified !== null && typeof verified === "object" && typeof verified.then === "function") {
        fail("asynchronous_verifier_refused",
          "the trusted projection verifier must answer synchronously; this gate cannot await a record-layer read and refuses a promise rather than treating it as a verification",
          { synchronous_verifier_required: true });
      }
      assertObject(verified, "verification");
      assertClosedKeys(verified, ["request_binding_digest", "qualifications"], "verification");
      assertRequiredKeys(verified, ["request_binding_digest", "qualifications"], "verification");
      if (verified.request_binding_digest !== digest(input)) {
        fail("verification_binding_mismatch",
          "the verifier's binding digest does not cover this exact request",
          { expected: digest(input), actual: verified.request_binding_digest });
      }
      if (!Array.isArray(verified.qualifications)) {
        fail("invalid_shape", "verification.qualifications must be an array",
          { path: "verification.qualifications" });
      }
      verified.qualifications.forEach((record, index) => {
        const path = `verification.qualifications[${index}]`;
        assertObject(record, path);
        if (typeof record.verifier_id !== "string" || !trusted.includes(record.verifier_id)) {
          fail("untrusted_verifier_id",
            `${path}.verifier_id names an authority this gate was not configured to accept`,
            {
              path, verifier_id: typeof record.verifier_id === "string" ? record.verifier_id : null,
              trusted_verifier_ids: [...trusted],
            });
        }
      });
      const decision = selectRoute({
        request: safeRequest, qualifications: copy(verified.qualifications), authenticated: true,
      });
      // The brand is the provenance. It is applied here, to the frozen decision
      // this gate produced, and nowhere else in this module.
      if (decision.qualification_authenticated === true) AUTHENTIC_ROUTING_DECISIONS.add(decision);
      return decision;
    },
    /**
     * The exact bytes a verifier must bind. Exposed so a verifier implementation
     * and this gate cannot drift into two definitions of "this request".
     */
    requestBindingDigest(request) {
      return digest(requestBindingPreimage(request));
    },
  });
}

// ---------------------------------------------------------------------------
// The zero-effect projection.
// ---------------------------------------------------------------------------

/**
 * What this slice implements, what it refuses to invent, and what is genuinely
 * missing. The last list is named rather than built: there is no live producer
 * of measured qualifications in this repository and this slice creates none.
 */
export function v5ModelRoutingProjection() {
  return deepFreeze({
    schema_version: V5_ROUTING_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    policy_schema_version: V5_ROUTING_POLICY_SCHEMA_VERSION,
    role_schema_version: V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
    qualification_schema_version: V5_ROUTE_QUALIFICATION_SCHEMA_VERSION,
    envelope_schema_version: V5_JOB_ENVELOPE_SCHEMA_VERSION,
    decision_ids: [...V5_ROUTING_DECISION_IDS],
    decision_subset_digest: V5_ROUTING_DECISION_SUBSET_DIGEST,
    role_keys: [...V5_ROLE_KEYS],
    ranking_keys: [...V5_RANKING_KEYS],
    public_product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
    canonical_authority: V5_CANONICAL_AUTHORITY,
    // Properties, not aspirations: each is enforced above and tested.
    cost_can_qualify_a_route: false,
    // Scoped exactly: within this process, a decision, a bound envelope and a
    // prompt payload are this module's own objects or they are refused. The
    // scope is the honest part of the claim — see the two lines under it.
    silent_downgrade_possible_in_process: false,
    provenance_scope: V5_PROVENANCE_SCOPE,
    provenance_survives_serialization: false,
    strength_scale_is_policy_declared_and_shared: true,
    backend_can_mint_authority: false,
    backend_can_be_user_facing: false,
    policy_values_invented_here: false,
    // Q047 names capabilities S01's registry does not carry. F04 does not widen
    // that registry, and does not pretend the gap is closed: a task class for
    // any of these refuses today with `unknown_local_capability`.
    q047_capabilities_not_registered_in_s01: [
      "document_rendering", "ocr", "embeddings", "batch_research",
    ],
    // Named gaps. None of these is simulated, stubbed into a fake success, or
    // implied by any result this module returns.
    unimplemented_dependencies: [
      "live route-qualification.v1 producer: this module accepts task-class evaluation records, but nothing in this slice produces or authenticates live observations, so its qualification inputs remain fixtures. Production and authentication need dispatch integration (V5-F06/V5-F07). A producer must also project exactly ONE current measurement per exact task-class/backend/model/version/effort route: two records for one route refuse here as an ambiguous projection rather than being resolved by a freshness rule this module has no authority to invent, and choosing the authoritative one — while retaining the superseded measurements as history — is the producer's job",
      "durable qualification and decision record store: this slice adds no table, no migration and no SQL integration",
      "model dispatch: V5-F06/V5-F07 own the integration; this pure module returns route plans and does not dispatch models",
      "live backend health source: local node state is a request input, never observed here",
      "durable, transferable provenance: decisions, bound envelopes and prompt payloads are branded by object identity in a process-local WeakSet, which does not survive serialization, a queue, a database row or an HTTP hop; a signed capability token issued by the record layer is what a cross-process boundary would need, and no such token, key, signature or verification exists in this slice",
      "S01 local-capability registry coverage for Q047: V5_LOCAL_CAPABILITIES registers no capability for document rendering, OCR, embeddings or batch research, so a task class naming one refuses today; widening that registry is S01's to do and F04 does not do it here",
    ],
    effects: V5_NO_EFFECTS,
  });
}

/** The canonical bytes of the decision subset, so a reviewer can rehash by hand. */
export function v5RoutingDecisionCanonicalBytes() {
  return canonicalJson(V5_ROUTING_DECISION_IDS.map(decision_id => ({
    decision_id,
    settled_requirement: V5_ROUTING_DECISIONS[decision_id].settled_requirement,
    source_evidence_digest: V5_ROUTING_DECISIONS[decision_id].source_evidence_digest,
  })));
}
