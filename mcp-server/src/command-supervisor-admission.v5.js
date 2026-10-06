// V5-F07 phase 2 — the hardened local supervisor's ADMISSION DECISION.
//
// This closes catalog `checkable_done` item 4 — "supervisor substitution / path
// / symlink / multilink / nonce / replay / rollback negatives refuse" — and the
// unknown-command half of item 1, as a pure evaluator over a typed observation.
// It is the DECISION and nothing else.
//
// WHAT THIS FILE IS NOT, said first because the name invites the wrong reading.
// It is not the broker process, not the OS sandbox profiles, not the command
// transport, and not a supervisor. It launches nothing, opens nothing, resolves
// no path, follows no link, stats no inode and holds no lock. Every fact it
// decides on is a TYPED OBSERVATION THE CALLER SUPPLIES. An `allow` from here
// says the twelve registered negatives did not fire on the facts as reported;
// it admits nobody to anything, and every result says so in its own fields.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — a frozen result whose `decision` is "allow"
//     or "refuse" with a stable `reason_id`. A refusal is an answer the caller
//     may record. "I cannot say" is one of these: an observation that does not
//     state whether a link was followed is a thing a caller is allowed to
//     report, and it BLOCKS. Silence is never an allow.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, unknown
//     states, open schemas and malformed digests are not policy questions; the
//     module cannot read the request at all, so it fails closed rather than
//     guessing which negative was meant.
//
// NO CALLER MAY NAME WHICH CHECKS APPLY. `enforced_checks`, `skip_checks` and
// `trusted` are unknown fields, so a request that tries to narrow the test
// cannot be read at all — the same closed-key discipline that makes
// `enforced_axes` unreadable in command-version-compatibility.v5.js. The check
// list is a module constant, in a fixed order, and the order is load-bearing:
// the first check that does not pass refuses, and everything after it reports
// `not_reached`. That is how "a revoked capability refuses BEFORE any digest is
// read" is a structural property of this file rather than a promise about it.
//
// A LABEL NEVER OUTVOTES A DIGEST. An executable that keeps its name while its
// bytes change is a different executable, and a registry entry that keeps its
// command id while its sealed fields change is a different entry. Labels are
// carried into the answer so a reader can see what was claimed; they decide
// nothing. This is command-version-compatibility.v5.js's rule applied to
// handlers rather than to versions.
//
// WHAT IS DELIBERATELY DEFERRED, AND WHY IT IS NOT GUESSED HERE.
// Catalog `checkable_done` item 1 has two halves. The unknown-command half is
// decided here. THE DIRECT-CREDENTIAL HALF IS NOT, and shipping a guess would
// be worse than shipping the gap: writing it means choosing the boundary
// between "the command gateway refuses direct credentials" and "the receipted
// human break-glass door stays open", and this repository currently ships an
// AUTHORIZED direct-credential ingress under a reason flag. Which of those the
// slice means is settled by decisions Q055.D1 and Q056.D1, whose text lives in
// the doctrine store and is not readable from source. So the clause ships as a
// NAMED SEAM — V5_DIRECT_CREDENTIAL_CLAUSE_SEAM below — following the
// V5_POLICY_OBSERVATION_SEAM pattern, a request cannot carry a direct-credential
// claim at all (it would be an unknown field), and every result states
// `direct_credential_attempt_decided: false` so no consumer can read an `allow`
// here as covering it. The seam is hashed into the policy preimage, so the day
// the clause is written the policy digest moves and stale readers are refused.
//
// WHERE THE NEGATIVES COME FROM. Nothing here is invented that already exists:
// the single-use fail-closed capability, the executable-bytes binding, the
// operator binding and the revocation posture are ops/settlement-run-token.py's
// shapes, and the single-link discipline is tools/ops-record.py's. They are
// ported as DECISION PREDICATES OVER A TYPED OBSERVATION, never as live system
// calls. The one primitive with no precedent anywhere in this repository is the
// monotonic anti-rollback counter, so its vocabulary is NEW — and therefore it
// is a closed enumerated constant hashed into the policy preimage rather than
// inline string literals, which is the defect this lane already paid for once
// when a sibling module's deployment-axis states were left inline and the policy
// digest stopped moving when that vocabulary changed.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID, authorizationClassForActor } from "./identity.js";
import {
  V5_COMMAND_VERSION_SCHEMA_VERSION,
  V5_COMMAND_VERSION_REASON_IDS,
} from "./command-version-compatibility.v5.js";

export const V5_COMMAND_SUPERVISOR_SCHEMA_VERSION = "doctorcre-v5-command-supervisor-admission.v1";
export const V5_COMMAND_SUPERVISOR_POLICY_VERSION = 1;

/** The sealed shape whose digest a registry entry's own seal must reproduce. */
export const V5_REGISTRY_ENTRY_KIND = "command-supervisor-registry-entry.v1";

/**
 * The deferred clause. Where the boundary between a refused direct credential
 * and the receipted human break-glass door would be decided — NOT decided here,
 * and not guessed. Q055.D1 and Q056.D1 settle it; both are doctrine-store text.
 */
export const V5_DIRECT_CREDENTIAL_CLAUSE_SEAM =
  "step:v5-f07-direct-credential-refusal-boundary-decision";

/** The decisions that would have to be read before that clause could be written. */
export const V5_DIRECT_CREDENTIAL_BLOCKING_DECISION_IDS = Object.freeze(["Q055.D1", "Q056.D1"]);

/**
 * The registered negatives, IN THE ORDER THEY ARE APPLIED. The order is policy,
 * not presentation: the three door checks come first so that a supervisor in
 * safe mode, and a capability that has been revoked, refuse before any digest is
 * read — the way an unlabelled deployment blocks before any version is compared
 * in command-version-compatibility.v5.js.
 */
export const V5_SUPERVISOR_ADMISSION_CHECKS = Object.freeze([
  "supervisor_mode",
  "capability_state",
  "capability_holder_binding",
  "command_registration",
  "command_version_compatibility",
  "registry_entry_integrity",
  "executable_substitution",
  "path_resolution",
  "symlink",
  "link_count",
  "nonce_replay",
  "rollback_counter",
]);

/** The three checks that are decided before any digest is read. */
export const V5_CHECKS_BEFORE_ANY_DIGEST_IS_READ = Object.freeze([
  "supervisor_mode",
  "capability_state",
  "capability_holder_binding",
]);

/**
 * STATE VOCABULARY ONE — what one check can say.
 *
 * `unobservable` is a first-class outcome and it BLOCKS. A supervisor that
 * treats "the observation did not say" as "nothing was wrong" has replaced a
 * check with an assumption.
 */
export const V5_ADMISSION_CHECK_STATES = Object.freeze([
  "satisfied", "violated", "unobservable", "not_reached",
]);

/**
 * STATE VOCABULARY TWO — the monotonic anti-rollback counter.
 *
 * This is the one primitive with no precedent in this repository, which is
 * exactly why it is enumerated here and hashed into the policy preimage instead
 * of living as inline literals at the comparison site. `uncomparable` is not
 * padding: a counter compared against a floor belonging to a DIFFERENT counter
 * is not evidence of monotonicity, and without this state the easiest way past
 * an anti-rollback control would be to advance an unrelated counter.
 */
export const V5_ROLLBACK_COUNTER_STATES = Object.freeze([
  "advanced", "regressed", "uncomparable", "unchanged", "unstated",
]);

export const V5_SUPERVISOR_MODES = Object.freeze(["normal", "safe"]);

/** settlement-run-token.py's refusal set, as states a caller may report. */
export const V5_CAPABILITY_STATES = Object.freeze(["active", "expired", "revoked", "unissued"]);

/**
 * The nonce claim. `unknown` is the caller saying it cannot tell whether the
 * nonce was already spent, and it refuses: an unknown single-use state is
 * indistinguishable from a replay.
 */
export const V5_NONCE_STATES = Object.freeze(["consumed", "unconsumed", "unknown"]);

/**
 * How the command's path was reached. Exactly one of these is admissible.
 * `path_lookup` and `absolute_path` are named rather than lumped together
 * because the catalog's excluded scope names PATH lookup specifically, and a
 * refusal that cannot say which one happened is not a usable refusal.
 */
export const V5_PATH_RESOLUTION_MODES = Object.freeze([
  "absolute_path", "descriptor_relative", "path_lookup", "unstated",
]);

export const V5_ADMISSIBLE_PATH_RESOLUTION = "descriptor_relative";

/** tools/ops-record.py's rule: exactly one link, or the file is not the file. */
export const V5_REQUIRED_LINK_COUNT = 1;

export const V5_COMMAND_SUPERVISOR_REASON_IDS = Object.freeze([
  "admitted_after_all_supervisor_negatives_cleared",
  "capability_expired",
  "capability_holder_mismatch",
  "capability_revoked",
  "capability_unissued",
  "command_not_registered",
  "command_version_answer_unobservable",
  "command_version_incompatible",
  "executable_digest_substituted",
  "executable_digest_unobservable",
  "link_count_not_single",
  "link_count_unstated",
  "nonce_absent",
  "nonce_already_consumed",
  "nonce_state_unobservable",
  "path_not_descriptor_relative",
  "path_outside_declared_root",
  "path_resolution_unstated",
  "registry_entry_digest_moved",
  "rollback_counter_regressed",
  "rollback_counter_uncomparable",
  "rollback_counter_unchanged",
  "rollback_counter_unstated",
  "safe_mode_refuses_all_commands",
  "symlink_followed",
  "symlink_state_unobservable",
]);

// A `sha256:`-prefixed 64-hex digest. Spellings are NOT normalized into one
// another: comparison is byte equality, so an observed digest must be written
// exactly as the registry wrote it.
const SHA256_REF = /^sha256:[0-9a-f]{64}$/;

// A stable identifier: no whitespace, no separators that would let one id read
// as two. Same posture as settlement-run-token.py's ID_RE.
const STABLE_ID = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}$/;

const REQUEST_KEYS = Object.freeze(["actor", "capability", "observation", "registry", "version_compatibility"]);
const ACTOR_KEYS = Object.freeze(["human", "probe", "review", "slug", "sponsoring_human_slug"]);
const CAPABILITY_KEYS = Object.freeze(["capability_id", "issued_to", "mode", "state"]);
const REGISTRY_KEYS = Object.freeze(["entries", "registry_digest"]);
const REGISTRY_ENTRY_KEYS = Object.freeze([
  "declared_root", "executable_digest", "executable_label", "sealed_entry_digest",
]);
const OBSERVATION_KEYS = Object.freeze([
  "command_id", "executable_digest", "executable_label", "nonce", "path", "rollback",
]);
const PATH_KEYS = Object.freeze([
  "link_count", "resolution", "resolved_path", "resolved_root", "symlink_followed",
]);
const NONCE_KEYS = Object.freeze(["nonce_id", "state"]);
const ROLLBACK_KEYS = Object.freeze(["admitted", "proposed"]);
const COUNTER_KEYS = Object.freeze(["counter_id", "value"]);

// The normalized shapes, re-checked at the evaluator door.
const NORMALIZED_REGISTRY_KEYS = Object.freeze([
  "entries", "registry_digest", "report_kind", "schema_version",
]);
const NORMALIZED_ENTRY_KEYS = Object.freeze([
  "command_id", "declared_root", "executable_digest", "executable_label", "sealed_entry_digest",
]);
const NORMALIZED_CAPABILITY_KEYS = Object.freeze([
  "capability_id", "issued_to", "mode", "report_kind", "schema_version", "state",
]);
const NORMALIZED_OBSERVATION_KEYS = Object.freeze([
  "command_id", "executable_digest", "executable_label", "nonce", "path", "report_kind",
  "rollback", "schema_version",
]);
const NORMALIZED_PATH_KEYS = Object.freeze([
  "link_count", "resolution", "resolved_path", "resolved_root", "symlink_followed",
]);
const NORMALIZED_NONCE_KEYS = Object.freeze(["nonce_id", "state"]);
const NORMALIZED_ROLLBACK_KEYS = Object.freeze(["admitted", "proposed"]);
const NORMALIZED_COUNTER_KEYS = Object.freeze(["counter_id", "value"]);

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
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

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

/** An open schema is an unenforced one; an unread field could be a smuggled control. */
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

function assertNonEmptyString(value, path) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  return value;
}

function assertStableId(value, path) {
  assertNonEmptyString(value, path);
  if (!STABLE_ID.test(value)) {
    fail("invalid_identifier", `${path} must be a stable identifier`, { path });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference to a 64-character lower-case digest`, { path });
  }
  return value;
}

function optionalString(value, path) {
  return value === undefined || value === null ? null : assertNonEmptyString(value, path);
}

/** Present-and-boolean, or the caller explicitly did not say. Absent means the same. */
function optionalBoolean(value, path) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "boolean") fail("invalid_shape", `${path} must be true, false or null`, { path });
  return value;
}

function optionalCount(value, path) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    fail("invalid_shape", `${path} must be a non-negative safe integer or null`, { path });
  }
  return value;
}

function assertEnum(value, allowed, path, code) {
  if (typeof value !== "string" || !allowed.includes(value)) {
    fail(code, `${path} must be one of the registered values`, { path, registered: [...allowed] });
  }
  return value;
}

/**
 * An absolute root with no trailing separator, no relative segment and no empty
 * segment. A root that is itself ambiguous cannot contain anything.
 */
function assertRootPath(value, path) {
  assertNonEmptyString(value, path);
  if (!value.startsWith("/") || value === "/" || value.endsWith("/")) {
    fail("invalid_path", `${path} must be an absolute root with no trailing separator`, { path });
  }
  const segments = value.split("/").slice(1);
  if (segments.some(segment => segment === "" || segment === "." || segment === "..")) {
    fail("invalid_path", `${path} must not contain an empty or relative segment`, { path });
  }
  return value;
}

/**
 * Containment decided on the segments, not on a string prefix: "/opt/carrx" is
 * not inside "/opt/carr", and a path carrying "." or ".." has not been resolved
 * at all, so it cannot be shown to be inside anything.
 */
function isResolvedInsideRoot(root, candidate) {
  if (typeof root !== "string" || typeof candidate !== "string") return false;
  if (!candidate.startsWith("/")) return false;
  const rootSegments = root.split("/").slice(1);
  const candidateSegments = candidate.split("/").slice(1);
  if (candidateSegments.length <= rootSegments.length) return false;
  if (candidateSegments.some(segment => segment === "" || segment === "." || segment === "..")) return false;
  return rootSegments.every((segment, index) => candidateSegments[index] === segment);
}

// ---------------------------------------------------------------------------
// The signed supervisor registry.
//
// Each entry says which bytes are registered for one command id, which root
// that command's path must resolve inside, and what the entry's own sealed
// digest was at signing. The seal is RECOMPUTED here from the entry's own
// fields — that is the "a registry entry whose own digest has moved refuses"
// half of the substitution negative, and it is why the digest kernel is
// reused rather than a second canonicalizer written.
// ---------------------------------------------------------------------------

/**
 * The digest a well-formed entry's seal must equal. Exported so a caller — or a
 * reviewer — can compute the seal by hand rather than trusting this file's own
 * answer about its own rule.
 */
export function commandSupervisorRegistryEntryDigest(entry) {
  assertObject(entry, "entry");
  assertClosedKeys(entry, ["command_id", "declared_root", "executable_digest", "executable_label"], "entry");
  assertRequiredKeys(entry, ["command_id", "declared_root", "executable_digest"], "entry");
  return digest({
    schema_version: V5_COMMAND_SUPERVISOR_SCHEMA_VERSION,
    entry_kind: V5_REGISTRY_ENTRY_KIND,
    command_id: assertStableId(entry.command_id, "entry.command_id"),
    executable_digest: assertDigestRef(entry.executable_digest, "entry.executable_digest"),
    executable_label: optionalString(entry.executable_label, "entry.executable_label"),
    declared_root: assertRootPath(entry.declared_root, "entry.declared_root"),
  });
}

export function normalizeCommandSupervisorRegistry(registry) {
  assertObject(registry, "registry");
  assertClosedKeys(registry, REGISTRY_KEYS, "registry");
  assertRequiredKeys(registry, ["entries", "registry_digest"], "registry");
  const entriesInput = assertObject(registry.entries, "registry.entries");
  const entries = {};
  for (const commandId of Object.keys(entriesInput)) {
    const at = `registry.entries.${commandId}`;
    assertStableId(commandId, at);
    const entry = assertObject(entriesInput[commandId], at);
    assertClosedKeys(entry, REGISTRY_ENTRY_KEYS, at);
    assertRequiredKeys(entry, ["declared_root", "executable_digest", "sealed_entry_digest"], at);
    entries[commandId] = {
      command_id: commandId,
      executable_digest: assertDigestRef(entry.executable_digest, `${at}.executable_digest`),
      executable_label: optionalString(entry.executable_label, `${at}.executable_label`),
      declared_root: assertRootPath(entry.declared_root, `${at}.declared_root`),
      sealed_entry_digest: assertDigestRef(entry.sealed_entry_digest, `${at}.sealed_entry_digest`),
    };
  }
  return deepFreeze({
    report_kind: "supervisor_registry",
    schema_version: V5_COMMAND_SUPERVISOR_SCHEMA_VERSION,
    registry_digest: assertDigestRef(registry.registry_digest, "registry.registry_digest"),
    entries,
  });
}

// ---------------------------------------------------------------------------
// The supervisor capability.
//
// settlement-run-token.py's single-use capability, reduced to the part that is
// a DECISION: which mode the supervisor is in, what state the capability is in,
// and who it was issued to. This module records no consumption and holds no
// lock; both live in the runtime that owns the store.
// ---------------------------------------------------------------------------

export function normalizeSupervisorCapability(capability) {
  assertObject(capability, "capability");
  assertClosedKeys(capability, CAPABILITY_KEYS, "capability");
  assertRequiredKeys(capability, ["capability_id", "issued_to", "mode", "state"], "capability");
  return deepFreeze({
    report_kind: "supervisor_capability",
    schema_version: V5_COMMAND_SUPERVISOR_SCHEMA_VERSION,
    capability_id: assertStableId(capability.capability_id, "capability.capability_id"),
    issued_to: assertStableId(capability.issued_to, "capability.issued_to"),
    mode: assertEnum(capability.mode, V5_SUPERVISOR_MODES, "capability.mode", "unknown_supervisor_mode"),
    state: assertEnum(capability.state, V5_CAPABILITY_STATES, "capability.state", "unknown_capability_state"),
  });
}

// ---------------------------------------------------------------------------
// The command execution observation.
//
// Every field here is something a runtime supervisor WOULD observe and this
// module does not. Fields a caller may honestly be unable to answer —
// `symlink_followed`, `link_count`, `resolution`, the nonce, the counter — are
// optional AND nullable, and every one of them BLOCKS when unstated. That is
// deliberate: making them required would turn an honest "I could not tell" into
// an unreadable request, and making them default would turn it into an allow.
// ---------------------------------------------------------------------------

function normalizeCounter(value, path) {
  if (value === undefined || value === null) return null;
  assertObject(value, path);
  assertClosedKeys(value, COUNTER_KEYS, path);
  assertRequiredKeys(value, ["counter_id", "value"], path);
  if (typeof value.value !== "number" || !Number.isSafeInteger(value.value) || value.value < 0) {
    fail("invalid_shape", `${path}.value must be a non-negative safe integer`, { path: `${path}.value` });
  }
  return { counter_id: assertStableId(value.counter_id, `${path}.counter_id`), value: value.value };
}

export function normalizeCommandExecutionObservation(observation) {
  assertObject(observation, "observation");
  assertClosedKeys(observation, OBSERVATION_KEYS, "observation");
  assertRequiredKeys(observation, ["command_id", "path"], "observation");

  const pathInput = assertObject(observation.path, "observation.path");
  assertClosedKeys(pathInput, PATH_KEYS, "observation.path");
  const resolution = pathInput.resolution === undefined || pathInput.resolution === null
    ? "unstated"
    : assertEnum(pathInput.resolution, V5_PATH_RESOLUTION_MODES, "observation.path.resolution",
      "unknown_path_resolution_mode");

  let nonce = null;
  if (observation.nonce !== undefined && observation.nonce !== null) {
    const nonceInput = assertObject(observation.nonce, "observation.nonce");
    assertClosedKeys(nonceInput, NONCE_KEYS, "observation.nonce");
    assertRequiredKeys(nonceInput, ["nonce_id", "state"], "observation.nonce");
    nonce = {
      nonce_id: assertStableId(nonceInput.nonce_id, "observation.nonce.nonce_id"),
      state: assertEnum(nonceInput.state, V5_NONCE_STATES, "observation.nonce.state", "unknown_nonce_state"),
    };
  }

  let rollback = null;
  if (observation.rollback !== undefined && observation.rollback !== null) {
    const rollbackInput = assertObject(observation.rollback, "observation.rollback");
    assertClosedKeys(rollbackInput, ROLLBACK_KEYS, "observation.rollback");
    rollback = {
      admitted: normalizeCounter(rollbackInput.admitted, "observation.rollback.admitted"),
      proposed: normalizeCounter(rollbackInput.proposed, "observation.rollback.proposed"),
    };
  }

  return deepFreeze({
    report_kind: "command_execution_observation",
    schema_version: V5_COMMAND_SUPERVISOR_SCHEMA_VERSION,
    command_id: assertStableId(observation.command_id, "observation.command_id"),
    executable_digest: observation.executable_digest === undefined || observation.executable_digest === null
      ? null
      : assertDigestRef(observation.executable_digest, "observation.executable_digest"),
    executable_label: optionalString(observation.executable_label, "observation.executable_label"),
    path: {
      resolution,
      resolved_path: optionalString(pathInput.resolved_path, "observation.path.resolved_path"),
      resolved_root: optionalString(pathInput.resolved_root, "observation.path.resolved_root"),
      symlink_followed: optionalBoolean(pathInput.symlink_followed, "observation.path.symlink_followed"),
      link_count: optionalCount(pathInput.link_count, "observation.path.link_count"),
    },
    nonce,
    rollback,
  });
}

// ---------------------------------------------------------------------------
// Revalidation at the door.
//
// A NORMALIZED REPORT IS REVALIDATED IN FULL, NOT RECOGNIZED BY ITS MARKER. The
// `report_kind` marker says which normalizer a report CLAIMS to come from; it
// proves nothing, because anything can be written by hand or round-tripped
// through JSON and edited. A correctly-shaped hand-built report is accepted by
// design — that is what "revalidated in full" means — and an incoherent one is
// unreadable rather than refusable.
// ---------------------------------------------------------------------------

function badReport(path, message) {
  fail("unnormalized_report", message, { path });
}

function assertReportShape(value, keys, path) {
  if (!isPlainObject(value)) badReport(path, `${path} must be a plain object`);
  for (const key of Object.keys(value)) {
    if (!keys.includes(key)) badReport(`${path}.${key}`, `unknown field "${key}" at ${path}`);
  }
  for (const key of keys) {
    if (!(key in value)) badReport(`${path}.${key}`, `${path}.${key} is missing from the normalized report`);
  }
  return value;
}

function reportString(value, path, { nullable = false } = {}) {
  if (value === null && nullable) return null;
  if (typeof value !== "string" || value.length === 0) badReport(path, `${path} must be a non-empty string`);
  return value;
}

function reportDigest(value, path, { nullable = false } = {}) {
  if (value === null && nullable) return null;
  if (typeof value !== "string" || !SHA256_REF.test(value)) badReport(path, `${path} must be a readable digest`);
  return value;
}

function reportEnum(value, allowed, path) {
  if (typeof value !== "string" || !allowed.includes(value)) {
    badReport(path, `${path} is not one of the registered values`);
  }
  return value;
}

function assertNormalizedRegistry(value, path) {
  assertReportShape(value, NORMALIZED_REGISTRY_KEYS, path);
  if (value.report_kind !== "supervisor_registry" ||
      value.schema_version !== V5_COMMAND_SUPERVISOR_SCHEMA_VERSION) {
    badReport(path, `${path} must be produced by normalizeCommandSupervisorRegistry`);
  }
  reportDigest(value.registry_digest, `${path}.registry_digest`);
  if (!isPlainObject(value.entries)) badReport(`${path}.entries`, `${path}.entries must be a plain object`);
  for (const commandId of Object.keys(value.entries)) {
    const at = `${path}.entries.${commandId}`;
    const entry = assertReportShape(value.entries[commandId], NORMALIZED_ENTRY_KEYS, at);
    if (entry.command_id !== commandId) {
      badReport(`${at}.command_id`, `${at}.command_id does not match the key it is filed under`);
    }
    if (!STABLE_ID.test(commandId)) badReport(at, `${at} is not filed under a stable identifier`);
    reportDigest(entry.executable_digest, `${at}.executable_digest`);
    reportDigest(entry.sealed_entry_digest, `${at}.sealed_entry_digest`);
    reportString(entry.executable_label, `${at}.executable_label`, { nullable: true });
    reportString(entry.declared_root, `${at}.declared_root`);
  }
  return value;
}

function assertNormalizedCapability(value, path) {
  assertReportShape(value, NORMALIZED_CAPABILITY_KEYS, path);
  if (value.report_kind !== "supervisor_capability" ||
      value.schema_version !== V5_COMMAND_SUPERVISOR_SCHEMA_VERSION) {
    badReport(path, `${path} must be produced by normalizeSupervisorCapability`);
  }
  reportString(value.capability_id, `${path}.capability_id`);
  reportString(value.issued_to, `${path}.issued_to`);
  reportEnum(value.mode, V5_SUPERVISOR_MODES, `${path}.mode`);
  reportEnum(value.state, V5_CAPABILITY_STATES, `${path}.state`);
  return value;
}

function assertNormalizedCounter(value, path) {
  if (value === null) return null;
  const counter = assertReportShape(value, NORMALIZED_COUNTER_KEYS, path);
  reportString(counter.counter_id, `${path}.counter_id`);
  if (typeof counter.value !== "number" || !Number.isSafeInteger(counter.value) || counter.value < 0) {
    badReport(`${path}.value`, `${path}.value must be a non-negative safe integer`);
  }
  return counter;
}

function assertNormalizedObservation(value, path) {
  assertReportShape(value, NORMALIZED_OBSERVATION_KEYS, path);
  if (value.report_kind !== "command_execution_observation" ||
      value.schema_version !== V5_COMMAND_SUPERVISOR_SCHEMA_VERSION) {
    badReport(path, `${path} must be produced by normalizeCommandExecutionObservation`);
  }
  reportString(value.command_id, `${path}.command_id`);
  reportDigest(value.executable_digest, `${path}.executable_digest`, { nullable: true });
  reportString(value.executable_label, `${path}.executable_label`, { nullable: true });

  const pathReport = assertReportShape(value.path, NORMALIZED_PATH_KEYS, `${path}.path`);
  reportEnum(pathReport.resolution, V5_PATH_RESOLUTION_MODES, `${path}.path.resolution`);
  reportString(pathReport.resolved_path, `${path}.path.resolved_path`, { nullable: true });
  reportString(pathReport.resolved_root, `${path}.path.resolved_root`, { nullable: true });
  if (pathReport.symlink_followed !== null && typeof pathReport.symlink_followed !== "boolean") {
    badReport(`${path}.path.symlink_followed`, `${path}.path.symlink_followed must be a boolean or null`);
  }
  if (pathReport.link_count !== null &&
      (typeof pathReport.link_count !== "number" || !Number.isSafeInteger(pathReport.link_count) ||
        pathReport.link_count < 0)) {
    badReport(`${path}.path.link_count`, `${path}.path.link_count must be a non-negative safe integer or null`);
  }

  if (value.nonce !== null) {
    const nonce = assertReportShape(value.nonce, NORMALIZED_NONCE_KEYS, `${path}.nonce`);
    reportString(nonce.nonce_id, `${path}.nonce.nonce_id`);
    reportEnum(nonce.state, V5_NONCE_STATES, `${path}.nonce.state`);
  }
  if (value.rollback !== null) {
    const rollback = assertReportShape(value.rollback, NORMALIZED_ROLLBACK_KEYS, `${path}.rollback`);
    assertNormalizedCounter(rollback.admitted, `${path}.rollback.admitted`);
    assertNormalizedCounter(rollback.proposed, `${path}.rollback.proposed`);
  }
  return value;
}

/**
 * The four-axis compatibility answer is an INPUT, never recomputed here.
 *
 * It is read FIELD BY FIELD and never key-closed, for the same reason the
 * release payload is: that module's answer grows fields over time, and an
 * answer from a newer sibling must not make this module throw. What is checked
 * is that it is that module's answer (its schema version), that it names a
 * reason from that module's own closed set, and that it agrees it granted no
 * admission. Its `decision` is READ. The axes are not re-derived — a second
 * implementation of the comparison would be a second place for it to disagree.
 */
function readVersionCompatibility(value, path) {
  if (value === undefined || value === null) return null;
  assertObject(value, path);
  if (value.schema_version !== V5_COMMAND_VERSION_SCHEMA_VERSION) {
    fail("foreign_version_answer",
      `${path} is not an answer from command-version-compatibility.v5.js`,
      { path, expected: V5_COMMAND_VERSION_SCHEMA_VERSION });
  }
  if (value.decision !== "allow" && value.decision !== "refuse") {
    fail("foreign_version_answer", `${path}.decision must be "allow" or "refuse"`, { path: `${path}.decision` });
  }
  if (!V5_COMMAND_VERSION_REASON_IDS.includes(value.reason_id)) {
    fail("foreign_version_answer", `${path}.reason_id is not one of that module's reason ids`,
      { path: `${path}.reason_id` });
  }
  if (value.runtime_admission_granted !== false) {
    fail("foreign_version_answer",
      `${path}.runtime_admission_granted must be false; that module admits nobody and neither does this one`,
      { path: `${path}.runtime_admission_granted` });
  }
  return { decision: value.decision, reason_id: value.reason_id };
}

// ---------------------------------------------------------------------------
// The checks.
//
// Each returns one frozen-shaped outcome. `satisfied` is the only state that
// lets the next check run.
// ---------------------------------------------------------------------------

/**
 * The canonical fields come LAST, so a detail key can never overwrite the
 * outcome's own `state` or `reason_id`. That is not defensive tidiness: a
 * detail field named `state` silently rewriting a check's verdict is exactly
 * how a refusal would read as a pass.
 */
function outcome(check, state, reason_id, reason, detail = {}) {
  return { ...detail, check, state, reason_id, reason };
}

function satisfied(check, detail = {}) {
  return outcome(check, "satisfied", null, null, detail);
}

function checkSupervisorMode(capability) {
  if (capability.mode === "safe") {
    // Its own reason id, never the generic one. A safe-mode refusal that reads
    // like an ordinary refusal hides the fact that NOTHING would have been
    // admitted, which is the single most important thing an operator reading
    // the record needs to know.
    return outcome("supervisor_mode", "violated", "safe_mode_refuses_all_commands",
      "the supervisor is in safe mode, in which every command refuses regardless of its other facts",
      { mode: capability.mode });
  }
  return satisfied("supervisor_mode", { mode: capability.mode });
}

const CAPABILITY_STATE_REASON = Object.freeze({
  expired: "capability_expired",
  revoked: "capability_revoked",
  unissued: "capability_unissued",
});

function checkCapabilityState(capability) {
  if (capability.state === "active") {
    return satisfied("capability_state", { capability_state: capability.state });
  }
  return outcome("capability_state", "violated", CAPABILITY_STATE_REASON[capability.state],
    `the supervisor capability is ${capability.state}`, { capability_state: capability.state });
}

function checkCapabilityHolderBinding(capability, actorSlug) {
  if (capability.issued_to === actorSlug) {
    return satisfied("capability_holder_binding", { issued_to: capability.issued_to, actor_slug: actorSlug });
  }
  return outcome("capability_holder_binding", "violated", "capability_holder_mismatch",
    "the capability was issued to a different holder than the actor proposing this command",
    { issued_to: capability.issued_to, actor_slug: actorSlug });
}

function checkCommandRegistration(registry, commandId) {
  const registered = Object.prototype.hasOwnProperty.call(registry.entries, commandId);
  if (registered) return satisfied("command_registration", { command_id: commandId, registered: true });
  // At the door, and with nothing about which checks it would have faced. The
  // refusal names the command the caller already knows it sent and nothing else.
  return outcome("command_registration", "violated", "command_not_registered",
    "the proposed command is not registered with this supervisor",
    { command_id: commandId, registered: false });
}

function checkCommandVersionCompatibility(answer) {
  if (answer === null) {
    return outcome("command_version_compatibility", "unobservable", "command_version_answer_unobservable",
      "no four-axis command-version compatibility answer was supplied, so version skew is undetermined",
      { version_decision: null, version_reason_id: null });
  }
  if (answer.decision !== "allow") {
    return outcome("command_version_compatibility", "violated", "command_version_incompatible",
      "the four-axis command-version comparison refused for this deployment",
      { version_decision: answer.decision, version_reason_id: answer.reason_id });
  }
  return satisfied("command_version_compatibility",
    { version_decision: answer.decision, version_reason_id: answer.reason_id });
}

function checkRegistryEntryIntegrity(entry) {
  const recomputed = commandSupervisorRegistryEntryDigest({
    command_id: entry.command_id,
    executable_digest: entry.executable_digest,
    executable_label: entry.executable_label,
    declared_root: entry.declared_root,
  });
  if (recomputed === entry.sealed_entry_digest) {
    return satisfied("registry_entry_integrity", { sealed_entry_digest: entry.sealed_entry_digest });
  }
  return outcome("registry_entry_integrity", "violated", "registry_entry_digest_moved",
    "the registry entry's own sealed digest is not the digest of the entry as it now reads",
    { sealed_entry_digest: entry.sealed_entry_digest, recomputed_entry_digest: recomputed });
}

function checkExecutableSubstitution(entry, observation) {
  const observed = observation.executable_digest;
  const detail = {
    registered_digest: entry.executable_digest,
    observed_digest: observed,
    registered_label: entry.executable_label,
    observed_label: observation.executable_label,
    label_matched: entry.executable_label !== null && observation.executable_label !== null
      ? entry.executable_label === observation.executable_label
      : null,
  };
  if (observed === null) {
    return outcome("executable_substitution", "unobservable", "executable_digest_unobservable",
      "the observation reports no digest for the bytes that would run", detail);
  }
  if (observed !== entry.executable_digest) {
    // A matching label alongside a differing digest is the substitution this
    // check exists for, not a mitigating fact. It is carried into the answer so
    // the record shows what was claimed, and it changes nothing.
    return outcome("executable_substitution", "violated", "executable_digest_substituted",
      "the observed executable bytes are not the registered ones", detail);
  }
  return satisfied("executable_substitution", detail);
}

function checkPathResolution(entry, observation) {
  const observedPath = observation.path;
  const detail = {
    resolution: observedPath.resolution,
    declared_root: entry.declared_root,
    resolved_root: observedPath.resolved_root,
    resolved_path: observedPath.resolved_path,
  };
  if (observedPath.resolution === "unstated") {
    return outcome("path_resolution", "unobservable", "path_resolution_unstated",
      "the observation does not state how the command path was reached", detail);
  }
  if (observedPath.resolution !== V5_ADMISSIBLE_PATH_RESOLUTION) {
    return outcome("path_resolution", "violated", "path_not_descriptor_relative",
      `the command path was reached by ${observedPath.resolution}, not relative to a held directory descriptor`,
      detail);
  }
  if (observedPath.resolved_root === null || observedPath.resolved_path === null) {
    return outcome("path_resolution", "unobservable", "path_resolution_unstated",
      "the observation does not state the resolved root and path", detail);
  }
  if (observedPath.resolved_root !== entry.declared_root ||
      !isResolvedInsideRoot(entry.declared_root, observedPath.resolved_path)) {
    return outcome("path_resolution", "violated", "path_outside_declared_root",
      "the resolved command path is not inside the root declared for this command", detail);
  }
  return satisfied("path_resolution", detail);
}

function checkSymlink(observation) {
  const followed = observation.path.symlink_followed;
  if (followed === null) {
    // Unobservable, and it BLOCKS. This is the clause where a default would be
    // most tempting and most wrong: a supervisor that reads silence about link
    // following as "no link was followed" has stopped checking.
    return outcome("symlink", "unobservable", "symlink_state_unobservable",
      "the observation does not state whether a symbolic link was followed, so it cannot be shown that none was",
      { symlink_followed: null });
  }
  if (followed === true) {
    return outcome("symlink", "violated", "symlink_followed",
      "a symbolic link was followed reaching the command", { symlink_followed: true });
  }
  return satisfied("symlink", { symlink_followed: false });
}

function checkLinkCount(observation) {
  const count = observation.path.link_count;
  if (count === null) {
    return outcome("link_count", "unobservable", "link_count_unstated",
      "the observation does not state the link count of the bytes that would run", { link_count: null });
  }
  if (count !== V5_REQUIRED_LINK_COUNT) {
    return outcome("link_count", "violated", "link_count_not_single",
      `the command has ${count} links; a second name for the same inode is a second way to change it`,
      { link_count: count, required_link_count: V5_REQUIRED_LINK_COUNT });
  }
  return satisfied("link_count", { link_count: count, required_link_count: V5_REQUIRED_LINK_COUNT });
}

function checkNonceReplay(observation) {
  const nonce = observation.nonce;
  if (nonce === null) {
    return outcome("nonce_replay", "violated", "nonce_absent",
      "the proposed command carries no single-use nonce", { nonce_id: null, nonce_state: null });
  }
  const detail = { nonce_id: nonce.nonce_id, nonce_state: nonce.state };
  if (nonce.state === "consumed") {
    return outcome("nonce_replay", "violated", "nonce_already_consumed",
      "the nonce is reported already consumed, which is a replay of a command that has run", detail);
  }
  if (nonce.state === "unknown") {
    return outcome("nonce_replay", "unobservable", "nonce_state_unobservable",
      "the caller cannot say whether this nonce was already spent, which is indistinguishable from a replay",
      detail);
  }
  return satisfied("nonce_replay", detail);
}

/**
 * The monotonic anti-rollback counter, derived rather than accepted.
 *
 * The caller supplies two counters — the floor already admitted and the one
 * proposed — and this function decides which of the five closed states holds.
 * The caller does NOT get to name the state: a control whose outcome is one of
 * the caller's own fields is not a control.
 */
function rollbackCounterState(rollback) {
  if (rollback === null || rollback.admitted === null || rollback.proposed === null) return "unstated";
  if (rollback.admitted.counter_id !== rollback.proposed.counter_id) return "uncomparable";
  if (rollback.proposed.value < rollback.admitted.value) return "regressed";
  if (rollback.proposed.value === rollback.admitted.value) return "unchanged";
  return "advanced";
}

const ROLLBACK_STATE_REASON = Object.freeze({
  regressed: "rollback_counter_regressed",
  unchanged: "rollback_counter_unchanged",
  uncomparable: "rollback_counter_uncomparable",
  unstated: "rollback_counter_unstated",
});

const ROLLBACK_STATE_MESSAGE = Object.freeze({
  regressed: "the proposed monotonic counter is below the floor already admitted, which is a rollback",
  unchanged: "the proposed monotonic counter has not advanced past the floor already admitted",
  uncomparable: "the proposed counter and the admitted floor are different counters and cannot establish monotonicity",
  unstated: "the observation does not state both the admitted counter floor and the proposed counter",
});

function checkRollbackCounter(observation) {
  const rollback = observation.rollback;
  const state = rollbackCounterState(rollback);
  const detail = {
    rollback_counter_state: state,
    admitted_counter_id: rollback && rollback.admitted ? rollback.admitted.counter_id : null,
    admitted_value: rollback && rollback.admitted ? rollback.admitted.value : null,
    proposed_counter_id: rollback && rollback.proposed ? rollback.proposed.counter_id : null,
    proposed_value: rollback && rollback.proposed ? rollback.proposed.value : null,
  };
  if (state === "advanced") return satisfied("rollback_counter", detail);
  const blockingState = state === "unstated" || state === "uncomparable" ? "unobservable" : "violated";
  return outcome("rollback_counter", blockingState, ROLLBACK_STATE_REASON[state],
    ROLLBACK_STATE_MESSAGE[state], detail);
}

// ---------------------------------------------------------------------------
// The decision.
// ---------------------------------------------------------------------------

/**
 * Decide whether one proposed command execution clears every registered
 * supervisor negative, on the facts as observed.
 *
 *   1. The request must be readable and closed. There is no field by which a
 *      caller can name which checks apply, waive one, or assert that a fact it
 *      did not observe was fine.
 *   2. The checks run in V5_SUPERVISOR_ADMISSION_CHECKS order and the first one
 *      that does not reach `satisfied` decides the answer; every later check
 *      reports `not_reached`. Safe mode, a dead capability and a capability
 *      bound to somebody else therefore all refuse before any digest is read.
 *   3. An `allow` is not an admission. It says these twelve negatives did not
 *      fire, and the result's own fields say what it is not.
 */
export function evaluateCommandSupervisorAdmission(request) {
  assertObject(request, "request");
  assertClosedKeys(request, REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["actor", "capability", "observation", "registry"], "request");

  const actorInput = assertObject(request.actor, "request.actor");
  // Closed to exactly the fields identity.js's classifier reads. The
  // classification itself comes from there; this module keeps no second actor
  // registry and decides no authority of its own.
  assertClosedKeys(actorInput, ACTOR_KEYS, "request.actor");
  assertRequiredKeys(actorInput, ["slug"], "request.actor");
  const actorSlug = assertStableId(actorInput.slug, "request.actor.slug");
  const actorAuthorizationClass = authorizationClassForActor(actorInput);

  const registry = assertNormalizedRegistry(request.registry, "request.registry");
  const capability = assertNormalizedCapability(request.capability, "request.capability");
  const observation = assertNormalizedObservation(request.observation, "request.observation");
  const versionAnswer = readVersionCompatibility(request.version_compatibility, "request.version_compatibility");

  const entry = Object.prototype.hasOwnProperty.call(registry.entries, observation.command_id)
    ? registry.entries[observation.command_id]
    : null;

  const evaluators = {
    supervisor_mode: () => checkSupervisorMode(capability),
    capability_state: () => checkCapabilityState(capability),
    capability_holder_binding: () => checkCapabilityHolderBinding(capability, actorSlug),
    command_registration: () => checkCommandRegistration(registry, observation.command_id),
    command_version_compatibility: () => checkCommandVersionCompatibility(versionAnswer),
    registry_entry_integrity: () => checkRegistryEntryIntegrity(entry),
    executable_substitution: () => checkExecutableSubstitution(entry, observation),
    path_resolution: () => checkPathResolution(entry, observation),
    symlink: () => checkSymlink(observation),
    link_count: () => checkLinkCount(observation),
    nonce_replay: () => checkNonceReplay(observation),
    rollback_counter: () => checkRollbackCounter(observation),
  };

  const checkStates = {};
  const checksSatisfied = [];
  const checksNotReached = [];
  let blockingCheck = null;
  for (const check of V5_SUPERVISOR_ADMISSION_CHECKS) {
    if (blockingCheck !== null) {
      checkStates[check] = outcome(check, "not_reached", null,
        `not reached: ${blockingCheck} refused first`, { blocked_by: blockingCheck });
      checksNotReached.push(check);
      continue;
    }
    const state = evaluators[check]();
    checkStates[check] = state;
    if (state.state === "satisfied") checksSatisfied.push(check);
    else blockingCheck = check;
  }

  const admitted = blockingCheck === null;
  return deepFreeze({
    schema_version: V5_COMMAND_SUPERVISOR_SCHEMA_VERSION,
    policy_version: V5_COMMAND_SUPERVISOR_POLICY_VERSION,
    decision: admitted ? "allow" : "refuse",
    reason_id: admitted
      ? "admitted_after_all_supervisor_negatives_cleared"
      : checkStates[blockingCheck].reason_id,
    checks_required: [...V5_SUPERVISOR_ADMISSION_CHECKS],
    checks_satisfied: checksSatisfied,
    checks_not_reached: checksNotReached,
    blocking_check: blockingCheck,
    check_states: checkStates,
    command: {
      command_id: observation.command_id,
      registered: entry !== null,
      registry_digest: registry.registry_digest,
    },
    supervisor: {
      mode: capability.mode,
      capability_id: capability.capability_id,
      capability_state: capability.state,
      capability_issued_to: capability.issued_to,
      actor_slug: actorSlug,
      // Read from identity.js and CARRIED, not acted on. This module grants no
      // authority by class and refuses none by class either; the class is in the
      // record so a reader can see who asked.
      actor_authorization_class: actorAuthorizationClass,
    },
    // What this answer is not, in its own fields rather than in a comment.
    // An allow says twelve negatives did not fire on the facts as reported.
    authenticated: false,
    authorizes_command_dispatch: false,
    runtime_admission_granted: false,
    launches_process: false,
    // The lock is runtime. This module decides on the caller's CLAIM about
    // single-use state; it records no consumption and holds nothing, so two
    // callers holding the same unconsumed claim both get the same answer.
    enforces_single_use_lock: false,
    decided_on_caller_supplied_consumption_claim: true,
    // The deferred half of catalog checkable_done item 1. Named in every result
    // so no consumer can read an allow here as deciding it.
    direct_credential_attempt_decided: false,
    direct_credential_clause_seam: V5_DIRECT_CREDENTIAL_CLAUSE_SEAM,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no command, actor, capability, machine or
// acceptance fact — so two callers describing the same policy reach the same
// digest. The digest is an identity for these bytes and nothing else: it is not
// an acceptance, not a receipt, and not evidence for any consumer gate.
//
// BOTH STATE VOCABULARIES ARE ENUMERATED HERE, and so is every other closed
// vocabulary this module decides against. That is the point: a vocabulary that
// is not in the preimage can change without the policy digest moving, which is
// how a consumer ends up pinned to a policy it is no longer reading. Every list
// is sorted EXPLICITLY rather than left in declaration order, so the digest is
// stable by construction rather than by the luck of a list that happens to be
// alphabetical today.
// ---------------------------------------------------------------------------

export function v5CommandSupervisorPolicyPreimage() {
  return {
    schema_version: V5_COMMAND_SUPERVISOR_SCHEMA_VERSION,
    policy_version: V5_COMMAND_SUPERVISOR_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    registry_entry_kind: V5_REGISTRY_ENTRY_KIND,
    checks_in_order: [...V5_SUPERVISOR_ADMISSION_CHECKS],
    checks_before_any_digest_is_read: [...V5_CHECKS_BEFORE_ANY_DIGEST_IS_READ],
    check_states: [...V5_ADMISSION_CHECK_STATES].sort(),
    rollback_counter_states: [...V5_ROLLBACK_COUNTER_STATES].sort(),
    supervisor_modes: [...V5_SUPERVISOR_MODES].sort(),
    capability_states: [...V5_CAPABILITY_STATES].sort(),
    nonce_states: [...V5_NONCE_STATES].sort(),
    path_resolution_modes: [...V5_PATH_RESOLUTION_MODES].sort(),
    reason_ids: [...V5_COMMAND_SUPERVISOR_REASON_IDS].sort(),
    admissible_path_resolution: V5_ADMISSIBLE_PATH_RESOLUTION,
    required_link_count: V5_REQUIRED_LINK_COUNT,
    label_may_outvote_digest: false,
    caller_may_select_checks: false,
    caller_may_name_rollback_state: false,
    unstated_observation_blocks: true,
    first_unsatisfied_check_decides: true,
    version_compatibility_is_an_input: true,
    recomputes_version_compatibility: false,
    // The deferred clause, hashed in. The day it is written this digest moves,
    // and a consumer pinned to the deferred policy is refused rather than
    // silently reading a policy that now decides more than it did.
    direct_credential_clause: "deferred",
    direct_credential_clause_seam: V5_DIRECT_CREDENTIAL_CLAUSE_SEAM,
    direct_credential_clause_blocking_decision_ids: [...V5_DIRECT_CREDENTIAL_BLOCKING_DECISION_IDS].sort(),
    decides_direct_credential_attempts: false,
    launches_process: false,
    enforces_single_use_lock: false,
    authenticates: false,
    authorizes_command_dispatch: false,
    grants_runtime_admission: false,
  };
}

/** The deterministic `sha256:` digest of the closed supervisor-admission policy. */
export function v5CommandSupervisorPolicyDigest() {
  return digest(v5CommandSupervisorPolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5CommandSupervisorPolicyCanonicalBytes() {
  return canonicalJson(v5CommandSupervisorPolicyPreimage());
}
