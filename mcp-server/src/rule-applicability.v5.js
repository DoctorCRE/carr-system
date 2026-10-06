// DoctorCRE v5 slice V5-F05, half one: rule taxonomy, typed applicability,
// coverage receipt, the supersession/override/exception graph, and the binding
// text vs code-enforced-constraint delivery rule.
//
// Four settled decisions live here in full — Q051 (where each kind of rule
// lives), Q064 (applicability from typed facts plus a coverage receipt), Q066
// (full binding text for anything a model interprets) and Q087 (explicit
// relation graph, no model conflict resolution).
//
// Q065 IS SPLIT AND THE DETERMINISTIC HALF IS HERE, not in the assembler: the
// possibly-binding bucket, the never-omit rule on a possible binding
// constraint, `read_only_exploration_permitted` and
// `consequential_action_permitted` are all computed by deriveRuleApplicability
// below. Q050, Q068, and the budget and mode half of Q065 require context
// assembly that this module does not implement. Their settled decisions stay
// in this table as requirements; retaining them does not claim an implementation.
//
// `decision` NEVER READS ALLOW ON A BLOCKED RECEIPT. It has three values:
// "allow" only when `consequential_action_permitted` is true; "read_only" when
// nothing hard-refused but the write is blocked (facts unknown, a
// possibly-binding rule standing, an incomplete universe); "refuse" on a
// binding conflict or a delivery refusal. It used to read "allow" on the
// read-only case, which was safe only for callers that read the write gate and
// a trap for any caller that tested `decision === "allow"`, the idiom the rest
// of this codebase uses. The write gate is still
// `consequential_action_permitted`, `write_gate_field` still names it, and
// verifyCoverageReceipt refuses a receipt whose `decision` disagrees with it.
//
// WHAT IS CODE HERE AND WHAT MUST ARRIVE AS TYPED POLICY:
//
//   IN CODE — the STRUCTURE Q051/Q064/Q087 settle. The six rule classes and
//   what each class may claim, the seven typed fact dimensions and the closed
//   vocabularies for five of them, the three relation kinds, the two control
//   effects, the delivery rule, and the graph checks. These are identity.
//
//   AS TYPED POLICY INPUT — every RULE. Its id, version, scope, owner, trigger,
//   enforcement, tests, retirement, binding text and relations. This module
//   ships no rule and no default for any of them; a missing, ambiguous,
//   duplicated, dangling or cyclic one refuses. The live rule store (166 shared
//   / 31 personal today) is the supplier; this module is not a second registry
//   and holds no rule of its own.
//
// TWO KINDS OF NO, following S01 and F01 deliberately:
//   * A POLICY ANSWER is returned — a frozen result whose `decision` is "allow",
//     "read_only" or "refuse" with a stable `reason_id`. A coverage receipt that blocks a
//     consequential action is an ANSWER the caller records, not an exception.
//   * A CONTRACT VIOLATION throws V5F05Error. Unknown fields, unknown
//     vocabulary, open schemas, accessors, prototype keys, malformed Unicode,
//     dangling or cyclic relations and caller-asserted enforcement are not
//     policy questions; the module cannot read the input at all.
//
// THE ONE-AUTHORITY RULE. Every function is pure: no filesystem, no network, no
// database, no provider, no scheduler, no environment and no clock. Every
// evaluation that depends on time takes `now` from its caller. Nothing is
// stored, nothing is registered, nothing is activated; `V5_NO_EFFECTS` rides on
// every result to say so in the record.
//
// WHAT THIS FILE IS NOT. It is not the rule store, not a router, not a daemon,
// not an admission service and not a production authority. It reuses
// artifact-trust.js for canonicalization and hashing, identity.js for the one
// server-held tenant and global-boundaries.v5.js for the no-effects marker, and
// reimplements none of them.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";

export { V5_NO_EFFECTS };

export const V5_F05_KERNEL_SCHEMA_VERSION = "doctorcre-v5-f05-rule-applicability.v1";
export const V5_F05_UNIVERSE_SCHEMA_VERSION = "doctorcre-v5-f05-rule-universe.v1";
export const V5_F05_COVERAGE_SCHEMA_VERSION = "doctorcre-v5-f05-coverage-receipt.v1";
export const V5_F05_POLICY_VERSION = 1;

// The closed vocabulary of a coverage receipt's `decision`, and the one place
// it is computed from the gates. "allow" is reserved for a permitted
// consequential write; see the header.
export const V5_F05_COVERAGE_DECISIONS = Object.freeze(["allow", "read_only", "refuse"]);

/**
 * The coverage `decision` for a receipt's two gates. Exported so a receipt
 * built outside deriveRuleApplicability (the runtime's zero-rules receipt)
 * reaches the same value by the same rule rather than by copying a literal.
 * Only a literal `true` counts: a missing or non-boolean gate never reads as
 * allow or read_only.
 */
export function coverageDecision({ consequential_action_permitted, read_only_exploration_permitted }) {
  if (consequential_action_permitted === true) return "allow";
  if (read_only_exploration_permitted === true) return "read_only";
  return "refuse";
}

const DIGEST_REF = /^sha256:[0-9a-f]{64}$/;
// Captured rather than merely shape-matched, because the calendar has to be
// checked against the LITERAL fields; see assertInstant.
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;
// Caller-declared identifiers: rule ids, control keys, actions, resource
// classes, scopes, owners, test refs. Wide enough for the ids a real rule store
// already uses ("4f7c348f", "check:r06-model-route"), and deliberately closed.
const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;

const INVISIBLE = "\\u200B-\\u200F\\u202A-\\u202E\\u2060-\\u2064\\u2066-\\u2069\\uFEFF";
// Identifiers admit no control character at all.
const UNSAFE_TEXT = new RegExp(`[\\u0000-\\u001F\\u007F-\\u009F${INVISIBLE}]`, "u");
// Binding text is prose and may hold tab and newline; carriage return is
// refused so the same rule text cannot hash two ways across line endings.
const UNSAFE_PROSE = new RegExp(`[\\u0000-\\u0008\\u000B-\\u001F\\u007F-\\u009F${INVISIBLE}]`, "u");

export class V5F05Error extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5F05Error";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5F05Error(code, message, detail);
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

// ---------------------------------------------------------------------------
// The caller-assertion guard.
//
// TWO LISTS, because two different bypasses are being refused.
//
//   FRAGMENTS — a field whose NAME reaches into authority. Following F01: the
//   later handler derives the actor and the authority envelope, so a caller
//   must never be able to hand this slice a field that purports to confer one.
//
//   EXACT NAMES — the caller-boolean bypass Q066 names directly. `enforced:
//   true` is not enforcement evidence, `verified: true` is not a verification,
//   and `suppress: true` is not a rule retirement. Each of these is a claim
//   that would replace a check, so the field is refused rather than read.
//
// `actor` is deliberately absent from both. Naming an actor is how S01's own
// evaluator is called; the manifest COMPUTES the authority envelope from it
// rather than accepting one, which is the property that matters.
//
// The load-time self-check at the foot of this file proves neither list
// collides with any key this slice actually accepts.
// ---------------------------------------------------------------------------

export const V5_F05_AUTHORITY_INJECTION_FRAGMENTS = deepFreeze([
  "acting_as", "on_behalf_of", "impersonat", "authority", "authoriz",
  "privilege", "grant", "delegation", "redecision", "approved_by", "signed_off",
  "override", "bypass", "force_", "permission", "sudo", "superuser",
  "declassif", "untaint",
]);

export const V5_F05_REFUSED_ASSERTION_FIELDS = deepFreeze([
  "enforced", "is_enforced", "enforcement_verified", "verified", "validated",
  "authenticated", "attested", "trusted", "approved", "admin", "trust_me",
  "applicable", "binding", "mandatory_override", "skip_rules",
  "suppress", "remove", "removes", "omit", "exclude",
]);

function assertNoCallerAssertions(keys, path) {
  for (const key of keys) {
    const normalized = key.toLowerCase();
    if (V5_F05_REFUSED_ASSERTION_FIELDS.includes(normalized)) {
      fail("caller_assertion_field_refused",
        `${path}.${key} asserts a fact this slice must check; a caller boolean is not evidence`,
        { path: `${path}.${key}`, key });
    }
    for (const fragment of V5_F05_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_authority_field_refused",
          `${path}.${key} names authority the caller cannot supply`,
          { path: `${path}.${key}`, key, fragment });
      }
    }
  }
}

/**
 * A getter can return a different value on each read, so a decision taken from
 * one read cannot be trusted to describe the input that was validated. Refuse
 * the shape rather than reading it twice and hoping. `__proto__` as an OWN key
 * matters here in particular: JSON.parse creates one, and this slice validates
 * inputs that arrive as parsed bytes.
 */
function assertNoAccessorsOrHiddenKeys(object, path) {
  if (Object.prototype.hasOwnProperty.call(object, "__proto__")) {
    fail("prototype_key_refused", `${path}.__proto__ is an own key; the shape is refused rather than read`,
      { path: `${path}.__proto__` });
  }
  if (Object.getOwnPropertySymbols(object).length > 0) {
    fail("symbol_key_refused", `${path} carries symbol keys, which would ride along unread`, { path });
  }
  for (const key of Object.getOwnPropertyNames(object)) {
    const descriptor = Object.getOwnPropertyDescriptor(object, key);
    if (descriptor.get !== undefined || descriptor.set !== undefined) {
      fail("accessor_property_refused",
        `${path}.${key} is an accessor; a value that can change between reads cannot bind a decision`,
        { path: `${path}.${key}` });
    }
  }
}

/**
 * An open schema is a contract violation: an unread field is an unenforced one.
 *
 * OWN PROPERTY NAMES, not enumerable keys. A non-enumerable own data property
 * named `enforced` or `authority` would otherwise slip past
 * assertNoCallerAssertions while assertNoAccessorsOrHiddenKeys — which already
 * walks getOwnPropertyNames — saw it. Nothing reads such a field today; the
 * module's own doctrine is that an unread field is an unenforced one, and two
 * guards disagreeing about what a key IS is how that doctrine rots.
 */
function assertClosedKeys(object, allowed, path) {
  assertNoAccessorsOrHiddenKeys(object, path);
  const keys = Object.getOwnPropertyNames(object);
  assertNoCallerAssertions(keys, path);
  for (const key of keys) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object) || object[key] === undefined || object[key] === null) {
      fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
  }
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertArray(value, path, { min = 0, max = 512 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < min) fail("invalid_shape", `${path} must name at least ${min} item(s)`, { path });
  if (value.length > max) fail("invalid_shape", `${path} may name at most ${max} items`, { path });
  return value;
}

function assertBoolean(value, path) {
  if (typeof value !== "boolean") fail("invalid_shape", `${path} must be a boolean`, { path });
  return value;
}

function assertSafeInteger(value, path, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    fail("invalid_shape", `${path} must be a safe integer between ${min} and ${max}`, { path });
  }
  return value;
}

/**
 * Strings that carry identity are checked, not merely typed: well-formed, free
 * of control and invisible format characters, already NFC, and untrimmed
 * whitespace refused. Two rule ids that render identically but differ in bytes
 * would silently become two rules, or one rule with two histories.
 */
function assertSafeText(value, path, { maxLength = 512, prose = false } = {}) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  if (value.length > maxLength) {
    fail("text_too_long", `${path} may be at most ${maxLength} characters`, { path, length: value.length });
  }
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
    fail("malformed_unicode", `${path} contains an unpaired surrogate`, { path });
  }
  if ((prose ? UNSAFE_PROSE : UNSAFE_TEXT).test(value)) {
    fail("unsafe_unicode", `${path} contains a control, bidirectional or invisible format character`, { path });
  }
  if (value.normalize("NFC") !== value) {
    fail("non_canonical_unicode", `${path} is not in Unicode NFC; it is refused rather than normalized`, { path });
  }
  if (value.trim() !== value) {
    fail("untrimmed_text", `${path} has leading or trailing whitespace`, { path });
  }
  return value;
}

function assertExternalIdent(value, path, { maxLength = 255 } = {}) {
  assertSafeText(value, path, { maxLength });
  if (!EXTERNAL_IDENT.test(value)) {
    fail("invalid_identifier", `${path} is not a permitted identifier`, { path });
  }
  return value;
}

function assertEnum(value, registered, path, code) {
  if (typeof value !== "string" || !registered.includes(value)) {
    fail(code, `"${String(value)}" is not registered at ${path}`,
      { path, value: typeof value === "string" ? value : null, registered: [...registered] });
  }
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Timestamps are parsed, never inferred, and the calendar is checked against
 * the LITERAL fields before parsing: Date.parse silently normalizes an
 * impossible date rather than rejecting it, and a freshness or retirement
 * comparison against an instant nobody wrote is not the comparison the caller
 * asked for. Mirrors S01 and F01 exactly; neither exports the helper.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const h = Number(hour), mi = Number(minute), s = Number(second);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || h > 23 || mi > 59 || s > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path });
  return parsed;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !DIGEST_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference over 64 lower-case hex characters`, { path });
  }
  return value;
}

/**
 * A mutation-immune snapshot of validated caller data. Every compiled universe
 * and every echoed input is built through this, so a caller that mutates its
 * own object after validation cannot reach a decision taken later.
 *
 * A NON-ENUMERABLE OWN DATA PROPERTY IS REFUSED, NOT DROPPED. The walk used to
 * copy `Object.keys` while assertNoAccessorsOrHiddenKeys read
 * getOwnPropertyNames, so a hidden own field named `enforced` or `authority`
 * survived the accessor sweep and then vanished silently from the copy. Two
 * entry points then disagreed about the same object: compileRuleUniverse
 * refused it through assertClosedKeys and requireCompiledUniverse — which
 * snapshots the whole universe before checking anything but the top level —
 * quietly dropped it from a NESTED rule. Nothing read such a field either way;
 * "an unread field is an unenforced one" is this module's own standard, and a
 * guard that silently deletes what another guard refuses is how it rots.
 */
function snapshot(value, path, depth = 0) {
  if (depth > 12) fail("input_too_deep", `${path} nests deeper than the contract admits`, { path });
  if (Array.isArray(value)) {
    return Object.freeze(value.map((item, index) => snapshot(item, `${path}[${index}]`, depth + 1)));
  }
  if (isPlainObject(value)) {
    assertNoAccessorsOrHiddenKeys(value, path);
    const out = {};
    for (const key of Object.getOwnPropertyNames(value)) {
      if (!Object.getOwnPropertyDescriptor(value, key).enumerable) {
        fail("non_enumerable_key_refused",
          `${path}.${key} is a non-enumerable own property; it is refused rather than dropped from the snapshot`,
          { path: `${path}.${key}`, key });
      }
      out[key] = snapshot(value[key], `${path}.${key}`, depth + 1);
    }
    return Object.freeze(out);
  }
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) return value;
  fail("invalid_shape", `${path} must be a string, number, boolean, null, array or plain object`, { path });
  return undefined;
}

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `${path} must be "${ORGANIZATION_TENANT_ID}"; the handler derives it`,
      { path, expected: ORGANIZATION_TENANT_ID });
  }
  return value;
}

/**
 * The guards, exported once as a frozen namespace rather than twenty named
 * exports. rule-context-runtime.v5.js consumes this namespace so applicability
 * and runtime delivery validate inputs with the same guards.
 */
export const V5_F05_GUARDS = Object.freeze({
  fail, isPlainObject, deepFreeze, snapshot,
  assertObject, assertArray, assertBoolean, assertSafeInteger,
  assertClosedKeys, assertRequiredKeys, assertNoAccessorsOrHiddenKeys,
  // Exported on its own for the two sub-objects whose SHAPE belongs to S01 and
  // must not be closed here — `actor` and `controls`. The assembler still has
  // to sweep them for authority-injection and caller-assertion field names,
  // because those two objects are exactly the ones that decide authority.
  assertNoCallerAssertions,
  assertSafeText, assertExternalIdent, assertEnum, assertInstant,
  assertDigestRef, assertTenant,
});

// ---------------------------------------------------------------------------
// The seven settled decisions of V5-F05. Text and evidence digests are copied
// verbatim from the reviewed source binding; they are identity, not
// configuration. A caller that believes it holds a different subset proves the
// disagreement here rather than discovering it later.
// ---------------------------------------------------------------------------

export const V5_F05_SETTLED_DECISIONS = deepFreeze({
  "Q050.D1": {
    settled_requirement: "A Context Assembler produces a task-specific reproducible manifest of actor, authority, records, full binding rules, provenance, freshness, conflicts, omissions, budget choices, and query identifiers.",
    source_evidence_digest: "aceecd2a205cb8e54ba3a25f4d76794755f282347aedff647aa64c6d7849870e",
  },
  "Q051.D1": {
    settled_requirement: "Classify constraints into code, workflow, tests, scoped judgment, preferences, or runtime state; deliver only necessary model context while never omitting or merely claiming enforcement of a binding rule.",
    source_evidence_digest: "a025a6f44b4ce876eedb74f4ddef3cf55a106e65fd8503ac5b9f3f6b10a47e4e",
  },
  "Q064.D1": {
    settled_requirement: "Derive mandatory rule applicability from typed actor, action, resource, audience, risk, environment, and transition facts and produce a coverage receipt; semantic retrieval may add guidance but never remove controls.",
    source_evidence_digest: "37d4a659edb38342b0dbfda64ef0ac6cb86c69bd47cd59bf4edd09f724a971eb",
  },
  "Q065.D1": {
    settled_requirement: "Include uncertain guidance where useful, resolve conflicts deterministically, allow marked read-only exploration, and block consequential writes when authority is unestablished; never omit a possible binding constraint for token savings.",
    source_evidence_digest: "97a70bb7fb499e647ace6061a9abb430746615e940beda49b7fbc224d69740b0",
  },
  "Q066.D1": {
    settled_requirement: "Models receive full binding text for rules they interpret; summaries are navigation only, while controls completely enforced in code may be represented by their resulting constraint.",
    source_evidence_digest: "0841228973232c76214c3bfb286870ca26e6c582b57e7bcc7eeecce0e86e5438",
  },
  "Q068.D1": {
    settled_requirement: "Treat all email, web, Salesforce, MLS, document, and uploaded content as tainted data that cannot grant authority, alter policy, request secrets, change recipients, or invoke tools.",
    source_evidence_digest: "b4ed3495d133e30e425dbe17ce207f1aad00de3497ef61a1fd3734a00ce90ec8",
  },
  "Q087.D1": {
    settled_requirement: "Represent supersession, override, and exceptions explicitly and fail closed on unresolved cycles or binding conflicts; models may detect but never resolve conflicts during action execution.",
    source_evidence_digest: "b5fe67a82599096c8e1d1e2b6c8f83bdd8d82dabd8c1f8e3775c0a74c7b14b1e",
  },
});

export const V5_F05_SETTLED_DECISION_IDS = deepFreeze(Object.keys(V5_F05_SETTLED_DECISIONS).sort());

/**
 * The exact bytes the reviewed seven-decision subset hashes to. DERIVED rather
 * than hand-typed: a literal would be a second copy of the same fact that could
 * drift without anything noticing. The test rebuilds the preimage from the
 * source binding's own values, so drift is still a test failure.
 */
export function v5F05DecisionSubsetPreimage() {
  return {
    schema_version: "doctorcre-v5-f05-decision-subset.v1",
    decisions: V5_F05_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_F05_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_F05_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
  };
}

export function v5F05DecisionSubsetDigest() {
  return digest(v5F05DecisionSubsetPreimage());
}

/** Refuse a caller whose decision subset has drifted from the reviewed seven. */
export function assertF05DecisionBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decisions", "decision_subset_digest"], "binding");
  assertRequiredKeys(binding, ["decisions"], "binding");
  assertObject(binding.decisions, "binding.decisions");
  assertNoAccessorsOrHiddenKeys(binding.decisions, "binding.decisions");
  if (binding.decision_subset_digest !== undefined && binding.decision_subset_digest !== null) {
    assertDigestRef(binding.decision_subset_digest, "binding.decision_subset_digest");
    if (binding.decision_subset_digest !== v5F05DecisionSubsetDigest()) {
      fail("decision_binding_drift", "the decision subset digest does not match the reviewed subset",
        { expected: v5F05DecisionSubsetDigest(), actual: binding.decision_subset_digest });
    }
  }
  const supplied = Object.keys(binding.decisions).sort();
  const missing = V5_F05_SETTLED_DECISION_IDS.filter(id => !supplied.includes(id));
  const extra = supplied.filter(id => !V5_F05_SETTLED_DECISION_IDS.includes(id));
  if (missing.length > 0 || extra.length > 0) {
    fail("decision_binding_drift", "the supplied decision set is not the reviewed seven", { missing, extra });
  }
  for (const id of V5_F05_SETTLED_DECISION_IDS) {
    const entry = assertObject(binding.decisions[id], `binding.decisions.${id}`);
    assertClosedKeys(entry, ["source_evidence_digest", "settled_requirement"], `binding.decisions.${id}`);
    assertRequiredKeys(entry, ["source_evidence_digest"], `binding.decisions.${id}`);
    if (typeof entry.source_evidence_digest !== "string" ||
        !/^[0-9a-f]{64}$/.test(entry.source_evidence_digest)) {
      fail("invalid_digest",
        `binding.decisions.${id}.source_evidence_digest must be 64 lower-case hex characters`,
        { path: `binding.decisions.${id}.source_evidence_digest` });
    }
    if (entry.source_evidence_digest !== V5_F05_SETTLED_DECISIONS[id].source_evidence_digest) {
      fail("decision_binding_drift", `source-evidence digest drift on ${id}`, {
        decision_id: id, expected: V5_F05_SETTLED_DECISIONS[id].source_evidence_digest,
        actual: entry.source_evidence_digest,
      });
    }
    if (entry.settled_requirement !== undefined &&
        entry.settled_requirement !== V5_F05_SETTLED_DECISIONS[id].settled_requirement) {
      fail("decision_binding_drift", `settled requirement text drift on ${id}`, { decision_id: id });
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// Q064 — the typed fact dimensions.
//
// Applicability is derived from these seven and nothing else. Five carry closed
// vocabularies because they are structural: who is acting, for whom, at what
// risk, in which environment, across which lifecycle edge. Two — action and
// resource_class — are DECLARED BY THE UNIVERSE, because a rule store names its
// own verbs and its own objects, and a closed list here would be this module
// inventing policy it has no authority to invent.
//
// THE UNKNOWN SENTINEL IS PART OF THE TYPE. A fact that is absent, or present
// as "unknown", makes every rule that triggers on that dimension UNDECIDED
// rather than inapplicable. That is the whole of the "silent permission"
// failure Q065 names: the cheap wrong answer is to treat a fact nobody supplied
// as a fact that did not match.
// ---------------------------------------------------------------------------

export const V5_F05_FACT_DIMENSIONS = deepFreeze([
  "action", "actor_class", "audience", "environment",
  "lifecycle_transition", "resource_class", "risk_tier",
]);

export const V5_F05_ACTOR_CLASSES = deepFreeze([
  "verified_partner", "sponsored_agent", "delegated_subagent", "unattended_run", "external_party",
]);
export const V5_F05_AUDIENCES = deepFreeze([
  "internal", "partner", "client", "counterparty", "vendor", "public",
]);
export const V5_F05_RISK_TIERS = deepFreeze(["routine", "elevated", "consequential", "irreversible"]);
export const V5_F05_CONSEQUENTIAL_RISK_TIERS = deepFreeze(["consequential", "irreversible"]);
export const V5_F05_ENVIRONMENTS = deepFreeze([
  "development", "isolated_worktree", "staging", "production",
]);
export const V5_F05_LIFECYCLE_TRANSITIONS = deepFreeze([
  "none", "create", "update", "activate", "amend", "retire", "delete", "publish", "send",
]);

/** Absent and "unknown" are the same state, and neither is "did not match". */
export const V5_F05_UNKNOWN_FACT = "unknown";

const CLOSED_DIMENSION_VALUES = deepFreeze({
  actor_class: V5_F05_ACTOR_CLASSES,
  audience: V5_F05_AUDIENCES,
  environment: V5_F05_ENVIRONMENTS,
  lifecycle_transition: V5_F05_LIFECYCLE_TRANSITIONS,
  risk_tier: V5_F05_RISK_TIERS,
});
const DECLARED_DIMENSIONS = deepFreeze(["action", "resource_class"]);

// ---------------------------------------------------------------------------
// Q051 — the six rule classes, and what each class may claim.
//
// The table is the decision. A class fixes its enforcement mechanism, whether
// its binding text is required, whether it must ship tests, and — the part rule
// ab814a26 exists for — whether it may be MANDATORY at all. Judgment guidance
// and personal preference may not: a rule whose only enforcement is a mind
// reading prose cannot claim to deny an action, and prose that claims
// enforcement code does not provide is the exact defect the decision names.
//
// runtime_state must EXPIRE. A temporary operational condition that never
// retires is a standing rule wearing a temporary name.
// ---------------------------------------------------------------------------

export const V5_F05_RULE_CLASSES = deepFreeze([
  "code_enforced", "workflow", "test", "scoped_judgment", "preference", "runtime_state",
]);

export const V5_F05_ENFORCEMENT_MECHANISMS = deepFreeze([
  "code_control", "workflow_definition", "behavioral_test",
  "model_judgment", "partner_preference", "runtime_state_flag",
]);

const RULE_CLASS_TABLE = deepFreeze({
  code_enforced: {
    enforcement: "code_control", binding_text_required: false,
    code_enforcement_required: true, tests_required: true,
    may_be_mandatory: true, must_expire: false, judgment_reason_required: false,
  },
  workflow: {
    enforcement: "workflow_definition", binding_text_required: true,
    code_enforcement_required: false, tests_required: true,
    may_be_mandatory: true, must_expire: false, judgment_reason_required: false,
  },
  test: {
    enforcement: "behavioral_test", binding_text_required: true,
    code_enforcement_required: false, tests_required: true,
    may_be_mandatory: true, must_expire: false, judgment_reason_required: false,
  },
  scoped_judgment: {
    enforcement: "model_judgment", binding_text_required: true,
    code_enforcement_required: false, tests_required: false,
    may_be_mandatory: false, must_expire: false, judgment_reason_required: true,
  },
  preference: {
    enforcement: "partner_preference", binding_text_required: true,
    code_enforcement_required: false, tests_required: false,
    may_be_mandatory: false, must_expire: false, judgment_reason_required: true,
  },
  runtime_state: {
    enforcement: "runtime_state_flag", binding_text_required: true,
    code_enforcement_required: false, tests_required: false,
    may_be_mandatory: true, must_expire: true, judgment_reason_required: false,
  },
});

export const V5_F05_RETIREMENT_BEHAVIORS = deepFreeze([
  "permanent_until_superseded", "expires_at", "superseded_only",
]);

export const V5_F05_CONTROL_EFFECTS = deepFreeze(["require", "forbid"]);

// ---------------------------------------------------------------------------
// Q087. Three relations, all explicit, all directed remover -> target.
//
// ALL THREE REMOVE. `supersedes`, `overrides` and `exception_to` differ in what
// they SAY and not in what they DO: each takes an otherwise-applicable rule out
// of the effective set. So the three admission conditions below apply to every
// one of them, not to exception_to alone.
//
//   1. BOUNDED SCOPE. A removing edge must carry a non-empty `scoped_validity`
//      predicate. A rule with `trigger: {}` and an `overrides` edge is an
//      unbounded repeal whatever the edge is called; refusing it only for
//      exception_to left the other two as the same silent repeal by another
//      name.
//   2. A REMOVAL-CAPABLE CLASS AGAINST A MANDATORY TARGET. Removing a mandatory
//      control is strictly stronger than declaring one, so a class that Q051
//      forbids from BEING mandatory may not DELETE a mandatory rule either.
//      Removal capability is mandatory capability; there is no second column to
//      drift out of step with `may_be_mandatory`.
//   3. NAMED AUTHORITY ACROSS OWNER OR SCOPE. A rule may remove another rule of
//      the SAME owner and the SAME scope: that is one owner revising their own
//      policy, which the compiler's other checks already validate. A rule
//      owned by someone else, or scoped elsewhere, may NOT — and this module
//      invents no dominance order to decide which of two owners wins. It fails
//      closed with `missing_relation_authority` and names the missing seam:
//      there is no verified grant that would let one owner or scope remove
//      another's control. See ruleKernelIntegrationGaps().
//   4. A NAMED SOURCE FOR THE REMOVER'S OWN TEXT. Same reasoning, applied to
//      provenance rather than to the class table: any rule holding a removing
//      edge must name the record, version and digest its text came from, so a
//      mandatory control cannot be deleted by a rule whose text is untraceable.
//      See compileRule.
// ---------------------------------------------------------------------------
export const V5_F05_RELATIONS = deepFreeze(["supersedes", "overrides", "exception_to"]);

/** Deterministic token estimate. Declared so a budget decision is reproducible. */
export const V5_F05_CHARS_PER_TOKEN_ESTIMATE = 4;

export function estimateTokens(text) {
  if (typeof text !== "string") return 0;
  return Math.ceil(text.length / V5_F05_CHARS_PER_TOKEN_ESTIMATE);
}

// ---------------------------------------------------------------------------
// Compiling one rule universe.
// ---------------------------------------------------------------------------

export const V5_F05_UNIVERSE_COMPLETENESS = deepFreeze([
  "complete_authoritative_universe", "partial_unknown_coverage",
]);

const UNIVERSE_KEYS = Object.freeze([
  "schema_version", "universe_version", "tenant", "completeness",
  "declared_actions", "declared_resource_classes", "rules",
]);
const RULE_KEYS = Object.freeze([
  "rule_id", "version", "rule_class", "scope", "owner", "mandatory",
  "trigger", "control_effect", "binding_text", "summary", "code_enforcement",
  "tests", "no_machine_control_reason", "retirement", "relations", "scoped_validity",
  "provenance",
]);
const RULE_REQUIRED = Object.freeze([
  "rule_id", "version", "rule_class", "scope", "owner", "mandatory", "trigger", "retirement",
]);
const CONTROL_EFFECT_KEYS = Object.freeze(["control_key", "effect"]);
const RETIREMENT_KEYS = Object.freeze(["behavior", "expires_at"]);
const RELATION_KEYS = Object.freeze(["relation", "target_rule_id", "target_version"]);
const CODE_ENFORCEMENT_KEYS = Object.freeze([
  "implementation_ref", "control_id", "control_version", "resulting_constraint", "evidence",
]);
const ENFORCEMENT_EVIDENCE_KEYS = Object.freeze([
  "verifier_id", "verified_at", "control_version", "implementation_digest", "evidence_digest",
]);
// Q050's per-rule source provenance and freshness, and the slot Q068 needs on
// the RULE path. A BOUNDED TYPED REFERENCE and nothing else: an id, the version
// and content digest that reference was read at, and when. There is no origin
// or taint field here on purpose — a taint class a caller could type onto its
// own rule is the laundering field this slice exists to refuse. The taint is
// resolved by whoever holds the records, which is the assembler; this module
// only guarantees the reference exists and is well formed.
const RULE_PROVENANCE_KEYS = Object.freeze([
  "source_record_id", "source_version", "source_content_digest", "retrieved_at",
]);

/**
 * A dimension -> permitted values map, used for both a rule's trigger and an
 * exception's scoped validity. An omitted dimension means "any value", which is
 * why an EMPTY map is a universal rule rather than an unusable one.
 */
function compilePredicate(raw, path, declared) {
  assertObject(raw, path);
  assertClosedKeys(raw, V5_F05_FACT_DIMENSIONS, path);
  const out = {};
  for (const dimension of V5_F05_FACT_DIMENSIONS) {
    if (!(dimension in raw) || raw[dimension] === undefined || raw[dimension] === null) continue;
    const dimensionPath = `${path}.${dimension}`;
    assertArray(raw[dimension], dimensionPath, { min: 1, max: 64 });
    const seen = new Set();
    const values = raw[dimension].map((value, index) => {
      const itemPath = `${dimensionPath}[${index}]`;
      if (value === V5_F05_UNKNOWN_FACT) {
        fail("unknown_sentinel_in_predicate",
          `${itemPath} names the unknown sentinel; a rule cannot trigger on the absence of a fact`,
          { path: itemPath });
      }
      const registered = CLOSED_DIMENSION_VALUES[dimension];
      if (registered !== undefined) {
        assertEnum(value, registered, itemPath, `unknown_${dimension}`);
      } else {
        assertExternalIdent(value, itemPath, { maxLength: 128 });
        if (!declared[dimension].includes(value)) {
          fail("undeclared_predicate_value",
            `${itemPath} names "${value}", which the universe does not declare under ${dimension}`,
            { path: itemPath, dimension, value });
        }
      }
      if (seen.has(value)) {
        fail("duplicate_predicate_value", `${dimensionPath} repeats "${value}"`, { path: dimensionPath, value });
      }
      seen.add(value);
      return value;
    }).sort();
    out[dimension] = values;
  }
  return out;
}

function compileEnforcementEvidence(raw, path) {
  assertObject(raw, path);
  assertClosedKeys(raw, ENFORCEMENT_EVIDENCE_KEYS, path);
  assertRequiredKeys(raw, ENFORCEMENT_EVIDENCE_KEYS, path);
  assertInstant(raw.verified_at, `${path}.verified_at`);
  return {
    verifier_id: assertExternalIdent(raw.verifier_id, `${path}.verifier_id`, { maxLength: 128 }),
    verified_at: raw.verified_at,
    control_version: assertExternalIdent(raw.control_version, `${path}.control_version`, { maxLength: 64 }),
    implementation_digest: assertDigestRef(raw.implementation_digest, `${path}.implementation_digest`),
    evidence_digest: assertDigestRef(raw.evidence_digest, `${path}.evidence_digest`),
  };
}

function compileCodeEnforcement(raw, path) {
  assertObject(raw, path);
  assertClosedKeys(raw, CODE_ENFORCEMENT_KEYS, path);
  assertRequiredKeys(raw,
    ["implementation_ref", "control_id", "control_version", "resulting_constraint"], path);
  return {
    implementation_ref: assertExternalIdent(raw.implementation_ref, `${path}.implementation_ref`),
    control_id: assertExternalIdent(raw.control_id, `${path}.control_id`, { maxLength: 128 }),
    control_version: assertExternalIdent(raw.control_version, `${path}.control_version`, { maxLength: 64 }),
    // The constraint the model is told INSTEAD of the rule text. It has to be
    // readable on its own, because nothing else about the rule reaches context.
    resulting_constraint: assertSafeText(raw.resulting_constraint, `${path}.resulting_constraint`,
      { maxLength: 2048, prose: true }),
    // Deliberately optional at compile time. A control with no current
    // verification is a real state of the world; it is delivery that fails
    // closed, so "we never checked" and "we checked and it holds" stay apart.
    evidence: raw.evidence === undefined || raw.evidence === null
      ? null : compileEnforcementEvidence(raw.evidence, `${path}.evidence`),
  };
}

function compileRuleProvenance(raw, path) {
  assertObject(raw, path);
  assertClosedKeys(raw, RULE_PROVENANCE_KEYS, path);
  assertRequiredKeys(raw, RULE_PROVENANCE_KEYS, path);
  assertInstant(raw.retrieved_at, `${path}.retrieved_at`);
  return {
    source_record_id: assertExternalIdent(raw.source_record_id, `${path}.source_record_id`,
      { maxLength: 128 }),
    source_version: assertSafeInteger(raw.source_version, `${path}.source_version`, { min: 1 }),
    source_content_digest: assertDigestRef(raw.source_content_digest,
      `${path}.source_content_digest`),
    retrieved_at: raw.retrieved_at,
  };
}

function compileRule(raw, index, seen, declared) {
  const path = `policy.rules[${index}]`;
  assertObject(raw, path);
  assertClosedKeys(raw, RULE_KEYS, path);
  assertRequiredKeys(raw, RULE_REQUIRED, path);

  const rule_id = assertExternalIdent(raw.rule_id, `${path}.rule_id`, { maxLength: 128 });
  if (seen.has(rule_id)) {
    fail("duplicate_rule", `${path} repeats rule "${rule_id}"; two entries for one id have no version`,
      { path, rule_id, first_index: seen.get(rule_id) });
  }
  seen.set(rule_id, index);

  const version = assertSafeInteger(raw.version, `${path}.version`, { min: 1 });
  const rule_class = assertEnum(raw.rule_class, V5_F05_RULE_CLASSES, `${path}.rule_class`, "unknown_rule_class");
  const shape = RULE_CLASS_TABLE[rule_class];
  const scope = assertExternalIdent(raw.scope, `${path}.scope`, { maxLength: 128 });
  const owner = assertExternalIdent(raw.owner, `${path}.owner`, { maxLength: 128 });
  const mandatory = assertBoolean(raw.mandatory, `${path}.mandatory`);

  if (mandatory && !shape.may_be_mandatory) {
    fail("judgment_rule_cannot_be_mandatory",
      `${path} is a ${rule_class} rule claiming to be mandatory; prose must not claim enforcement code does not provide`,
      { path, rule_id, rule_class });
  }

  const trigger = compilePredicate(raw.trigger, `${path}.trigger`, declared);

  let control_effect = null;
  if (mandatory) {
    if (raw.control_effect === undefined || raw.control_effect === null) {
      fail("missing_control_effect",
        `${path}.control_effect is required for a mandatory rule; a control with no effect cannot conflict or comply`,
        { path, rule_id });
    }
    assertObject(raw.control_effect, `${path}.control_effect`);
    assertClosedKeys(raw.control_effect, CONTROL_EFFECT_KEYS, `${path}.control_effect`);
    assertRequiredKeys(raw.control_effect, CONTROL_EFFECT_KEYS, `${path}.control_effect`);
    control_effect = {
      control_key: assertExternalIdent(raw.control_effect.control_key,
        `${path}.control_effect.control_key`, { maxLength: 128 }),
      effect: assertEnum(raw.control_effect.effect, V5_F05_CONTROL_EFFECTS,
        `${path}.control_effect.effect`, "unknown_control_effect"),
    };
  } else if (raw.control_effect !== undefined && raw.control_effect !== null) {
    fail("unused_control_effect",
      `${path}.control_effect is only read for a mandatory rule; unused policy is ambiguous policy`, { path });
  }

  let binding_text = null;
  if (raw.binding_text !== undefined && raw.binding_text !== null) {
    binding_text = assertSafeText(raw.binding_text, `${path}.binding_text`,
      { maxLength: 20000, prose: true });
  }
  if (shape.binding_text_required && binding_text === null) {
    fail("missing_binding_text",
      `${path}.binding_text is required for a ${rule_class} rule; a model cannot interpret an identifier`,
      { path, rule_id, rule_class });
  }
  // Navigation only, always. Never delivered as the rule, never hashed as one.
  const summary = raw.summary === undefined || raw.summary === null
    ? null : assertSafeText(raw.summary, `${path}.summary`, { maxLength: 512, prose: true });

  let code_enforcement = null;
  if (raw.code_enforcement !== undefined && raw.code_enforcement !== null) {
    code_enforcement = compileCodeEnforcement(raw.code_enforcement, `${path}.code_enforcement`);
  }
  if (shape.code_enforcement_required && code_enforcement === null) {
    fail("missing_code_enforcement",
      `${path}.code_enforcement is required for a code_enforced rule; the class IS the claim that code holds it`,
      { path, rule_id });
  }
  if (!shape.code_enforcement_required && code_enforcement !== null) {
    fail("unused_code_enforcement",
      `${path}.code_enforcement is only read for a code_enforced rule; a ${rule_class} rule claiming a code control is the defect Q051 names`,
      { path, rule_id, rule_class });
  }

  let tests = [];
  if (raw.tests !== undefined && raw.tests !== null) {
    assertArray(raw.tests, `${path}.tests`, { min: 0, max: 64 });
    const testSeen = new Set();
    tests = raw.tests.map((ref, i) => {
      const value = assertExternalIdent(ref, `${path}.tests[${i}]`);
      if (testSeen.has(value)) fail("duplicate_test_ref", `${path}.tests repeats "${value}"`, { path });
      testSeen.add(value);
      return value;
    }).sort();
  }
  if (shape.tests_required && tests.length === 0) {
    fail("missing_test_ref",
      `${path}.tests must name at least one behavioral oracle for a ${rule_class} rule`,
      { path, rule_id, rule_class });
  }

  let no_machine_control_reason = null;
  if (raw.no_machine_control_reason !== undefined && raw.no_machine_control_reason !== null) {
    no_machine_control_reason = assertSafeText(raw.no_machine_control_reason,
      `${path}.no_machine_control_reason`, { maxLength: 512, prose: true });
  }
  if (shape.judgment_reason_required && no_machine_control_reason === null) {
    fail("missing_no_machine_control_reason",
      `${path}.no_machine_control_reason must state in one line why no machine can hold a ${rule_class} rule`,
      { path, rule_id, rule_class });
  }
  if (!shape.judgment_reason_required && no_machine_control_reason !== null) {
    fail("unused_no_machine_control_reason",
      `${path}.no_machine_control_reason is only read for a judgment or preference rule`, { path });
  }

  assertObject(raw.retirement, `${path}.retirement`);
  assertClosedKeys(raw.retirement, RETIREMENT_KEYS, `${path}.retirement`);
  assertRequiredKeys(raw.retirement, ["behavior"], `${path}.retirement`);
  const behavior = assertEnum(raw.retirement.behavior, V5_F05_RETIREMENT_BEHAVIORS,
    `${path}.retirement.behavior`, "unknown_retirement_behavior");
  let expires_at = null;
  if (behavior === "expires_at") {
    if (raw.retirement.expires_at === undefined || raw.retirement.expires_at === null) {
      fail("missing_expiry", `${path}.retirement.expires_at is required for the expires_at behavior`, { path });
    }
    assertInstant(raw.retirement.expires_at, `${path}.retirement.expires_at`);
    expires_at = raw.retirement.expires_at;
  } else if (raw.retirement.expires_at !== undefined && raw.retirement.expires_at !== null) {
    fail("unused_expiry",
      `${path}.retirement.expires_at is only read for the expires_at behavior`, { path });
  }
  if (shape.must_expire && behavior !== "expires_at") {
    fail("runtime_state_must_expire",
      `${path} is a runtime_state rule with no expiry; a temporary condition that never retires is a standing rule`,
      { path, rule_id });
  }

  let relations = [];
  if (raw.relations !== undefined && raw.relations !== null) {
    assertArray(raw.relations, `${path}.relations`, { min: 0, max: 32 });
    const relationSeen = new Set();
    relations = raw.relations.map((entry, i) => {
      const relationPath = `${path}.relations[${i}]`;
      assertObject(entry, relationPath);
      assertClosedKeys(entry, RELATION_KEYS, relationPath);
      assertRequiredKeys(entry, RELATION_KEYS, relationPath);
      const relation = assertEnum(entry.relation, V5_F05_RELATIONS, `${relationPath}.relation`,
        "unknown_relation");
      const target_rule_id = assertExternalIdent(entry.target_rule_id, `${relationPath}.target_rule_id`,
        { maxLength: 128 });
      const target_version = assertSafeInteger(entry.target_version, `${relationPath}.target_version`,
        { min: 1 });
      if (target_rule_id === rule_id) {
        fail("self_relation", `${relationPath} points at its own rule; a rule cannot supersede itself`,
          { path: relationPath, rule_id });
      }
      const key = `${relation}|${target_rule_id}`;
      if (relationSeen.has(key)) {
        fail("duplicate_relation", `${relationPath} repeats ${relation} -> ${target_rule_id}`,
          { path: relationPath });
      }
      relationSeen.add(key);
      return { relation, target_rule_id, target_version };
    }).sort((a, b) => (a.target_rule_id === b.target_rule_id
      ? (a.relation < b.relation ? -1 : 1)
      : (a.target_rule_id < b.target_rule_id ? -1 : 1)));
  }

  // EVERY relation removes, so every relation needs a bound. See the Q087
  // header above: refusing an unbounded exception_to while admitting an
  // unbounded overrides refused the word, not the act.
  let scoped_validity = null;
  if (raw.scoped_validity !== undefined && raw.scoped_validity !== null) {
    scoped_validity = compilePredicate(raw.scoped_validity, `${path}.scoped_validity`, declared);
  }
  if (relations.length > 0) {
    if (scoped_validity === null || Object.keys(scoped_validity).length === 0) {
      fail("removing_edge_without_scoped_validity",
        `${path}.scoped_validity must bound the ${relations.length} removing edge(s) on this rule; an unbounded ${relations[0].relation} is a silent repeal`,
        { path, rule_id, relations: relations.map(r => r.relation) });
    }
  } else if (scoped_validity !== null) {
    fail("unused_scoped_validity",
      `${path}.scoped_validity is only read for a rule that holds a removing relation`, { path });
  }

  // Q050 asks for per-rule source provenance and freshness; Q068 needs somewhere
  // for the rule path to say where its text came from. Two kinds of rule must
  // carry it, and they are the same kind of thing:
  //
  //   * A MANDATORY rule, because a mandatory rule is authority and unbound
  //     authority is the whole failure.
  //   * A RULE THAT HOLDS A REMOVING EDGE, whatever its own `mandatory` flag
  //     says. REMOVAL CAPABILITY IS MANDATORY CAPABILITY — the same sentence the
  //     class table is built on at the Q087 header, and the parity it was
  //     missing. A non-mandatory rule deleting a mandatory control is exercising
  //     control over that control; a remover whose text names no source record,
  //     version or digest is exactly the state the message below calls out, and
  //     a kernel-only consumer would otherwise get a clean coverage_complete
  //     receipt in which a mandatory control was deleted by untraceable text.
  //
  // A guidance rule that removes nothing may carry provenance and is checked
  // when it does.
  let provenance = null;
  if (raw.provenance !== undefined && raw.provenance !== null) {
    provenance = compileRuleProvenance(raw.provenance, `${path}.provenance`);
  }
  if (provenance === null && (mandatory || relations.length > 0)) {
    fail("missing_rule_provenance",
      mandatory
        ? `${path}.provenance is required for a mandatory rule; a control whose text has no source cannot be told apart from text that arrived in an email`
        : `${path}.provenance is required for a rule that holds ${relations.length} removing edge(s); removing a control is strictly stronger than declaring one, and a remover whose text has no source cannot be told apart from text that arrived in an email`,
      { path, rule_id, mandatory, removing_edges: relations.map(r => r.relation) });
  }

  return {
    rule_id, version, rule_class, enforcement: shape.enforcement, scope, owner, mandatory,
    trigger, control_effect, binding_text, summary, code_enforcement, tests,
    no_machine_control_reason, retirement: { behavior, expires_at },
    relations, scoped_validity, provenance,
  };
}

/** The exact bytes a compiled universe hashes to, so a reviewer can check it by hand. */
export function ruleUniversePreimage(compiled) {
  return {
    schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
    universe_version: compiled.universe_version,
    tenant: compiled.tenant,
    completeness: compiled.completeness,
    declared_actions: [...compiled.declared_actions],
    declared_resource_classes: [...compiled.declared_resource_classes],
    // Bound because resolution READS it. A removal order the digest did not
    // cover could be reordered after compilation to change which rule survives.
    removal_order: [...compiled.removal_order],
    rules: compiled.rules.map(rule => ({
      rule_id: rule.rule_id,
      version: rule.version,
      rule_class: rule.rule_class,
      enforcement: rule.enforcement,
      scope: rule.scope,
      owner: rule.owner,
      mandatory: rule.mandatory,
      trigger: { ...rule.trigger },
      control_effect: rule.control_effect === null ? null : { ...rule.control_effect },
      binding_text: rule.binding_text,
      summary: rule.summary,
      code_enforcement: rule.code_enforcement === null ? null : {
        implementation_ref: rule.code_enforcement.implementation_ref,
        control_id: rule.code_enforcement.control_id,
        control_version: rule.code_enforcement.control_version,
        resulting_constraint: rule.code_enforcement.resulting_constraint,
        evidence: rule.code_enforcement.evidence === null
          ? null : { ...rule.code_enforcement.evidence },
      },
      tests: [...rule.tests],
      no_machine_control_reason: rule.no_machine_control_reason,
      retirement: { ...rule.retirement },
      relations: rule.relations.map(r => ({ ...r })),
      scoped_validity: rule.scoped_validity === null ? null : { ...rule.scoped_validity },
      provenance: rule.provenance === null ? null : { ...rule.provenance },
    })),
  };
}

function assertDeclaredIdentList(raw, path) {
  assertArray(raw, path, { min: 1, max: 256 });
  const seen = new Set();
  return raw.map((value, index) => {
    const item = assertExternalIdent(value, `${path}[${index}]`, { maxLength: 128 });
    if (seen.has(item)) fail("duplicate_declared_value", `${path} repeats "${item}"`, { path, value: item });
    seen.add(item);
    return item;
  }).sort();
}

/**
 * Refuse a graph the compiler cannot resolve. Q087 in one function.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. Every relation target must exist at the exact version named. A dangling
 *      or version-drifted edge is refused, never silently ignored — an ignored
 *      supersession leaves the superseded rule binding.
 *   2. No cycle over the union of all three relation kinds. A cycle has no
 *      deterministic winner, and picking one would be the model's job by
 *      another name.
 *   3. A removing edge against a MANDATORY target may only be held by a class
 *      Q051 allows to be mandatory. A preference or a scoped judgment cannot
 *      declare a control, so it cannot delete one either.
 *   4. A removing edge must stay inside ONE owner and ONE scope. Crossing either
 *      needs an authority this module has no verified mechanism to check, so it
 *      fails closed rather than picking a winner between two owners.
 *   5. No two mandatory rules with the SAME trigger and opposite effects on one
 *      control key unless a relation orders them. Identical triggers are the
 *      decidable case and belong at compile time; genuine fact-time overlap is
 *      caught by deriveRuleApplicability, which has the facts.
 */
function assertRelationGraph(rules) {
  const byId = new Map(rules.map(rule => [rule.rule_id, rule]));
  for (const rule of rules) {
    for (const relation of rule.relations) {
      const target = byId.get(relation.target_rule_id);
      if (target === undefined) {
        fail("dangling_relation",
          `rule "${rule.rule_id}" ${relation.relation} "${relation.target_rule_id}", which the universe does not contain`,
          { rule_id: rule.rule_id, relation: relation.relation, target_rule_id: relation.target_rule_id });
      }
      if (target.version !== relation.target_version) {
        fail("relation_version_mismatch",
          `rule "${rule.rule_id}" ${relation.relation} "${relation.target_rule_id}" at version ${relation.target_version}, but the universe carries version ${target.version}`,
          { rule_id: rule.rule_id, target_rule_id: relation.target_rule_id,
            expected: relation.target_version, actual: target.version });
      }
    }
  }

  // Iterative depth-first search, so a deep graph cannot blow the stack.
  const WHITE = 0, GREY = 1, BLACK = 2;
  const colour = new Map(rules.map(rule => [rule.rule_id, WHITE]));
  const ordered = [];
  for (const root of rules) {
    if (colour.get(root.rule_id) !== WHITE) continue;
    const stack = [{ id: root.rule_id, index: 0, path: [root.rule_id] }];
    colour.set(root.rule_id, GREY);
    while (stack.length > 0) {
      const frame = stack[stack.length - 1];
      const edges = byId.get(frame.id).relations;
      if (frame.index >= edges.length) {
        colour.set(frame.id, BLACK);
        ordered.push(frame.id);
        stack.pop();
        continue;
      }
      const next = edges[frame.index++].target_rule_id;
      const state = colour.get(next);
      if (state === GREY) {
        fail("relation_cycle",
          `the relation graph contains a cycle through "${next}"; a cycle has no deterministic winner`,
          { cycle: [...frame.path, next] });
      }
      if (state === WHITE) {
        colour.set(next, GREY);
        stack.push({ id: next, index: 0, path: [...frame.path, next] });
      }
    }
  }

  // Steps 3 and 4, after the cycle check so a cyclic graph still reports the
  // cycle: an unresolvable graph is the more fundamental refusal.
  for (const rule of rules) {
    for (const relation of rule.relations) {
      const target = byId.get(relation.target_rule_id);
      if (target.mandatory && !RULE_CLASS_TABLE[rule.rule_class].may_be_mandatory) {
        fail("removing_class_cannot_remove_mandatory_control",
          `rule "${rule.rule_id}" is a ${rule.rule_class} rule and ${relation.relation} the mandatory rule "${target.rule_id}"; a class that may not declare a control may not delete one`,
          { rule_id: rule.rule_id, rule_class: rule.rule_class, relation: relation.relation,
            target_rule_id: target.rule_id });
      }
      if (rule.owner !== target.owner || rule.scope !== target.scope) {
        fail("missing_relation_authority",
          `rule "${rule.rule_id}" (owner "${rule.owner}", scope "${rule.scope}") ${relation.relation} "${target.rule_id}" (owner "${target.owner}", scope "${target.scope}"); removing another owner's or another scope's rule needs an authority this kernel has no verified way to check`,
          { rule_id: rule.rule_id, relation: relation.relation, target_rule_id: target.rule_id,
            remover: { owner: rule.owner, scope: rule.scope },
            target: { owner: target.owner, scope: target.scope },
            missing_seam: "no_relation_authority_grant_verifier" });
      }
    }
  }

  const related = new Set();
  for (const rule of rules) {
    for (const relation of rule.relations) {
      related.add(`${rule.rule_id}|${relation.target_rule_id}`);
      related.add(`${relation.target_rule_id}|${rule.rule_id}`);
    }
  }
  const mandatory = rules.filter(rule => rule.mandatory);
  for (let i = 0; i < mandatory.length; i += 1) {
    for (let j = i + 1; j < mandatory.length; j += 1) {
      const a = mandatory[i], b = mandatory[j];
      if (a.control_effect.control_key !== b.control_effect.control_key) continue;
      if (a.control_effect.effect === b.control_effect.effect) continue;
      if (canonicalJson(a.trigger) !== canonicalJson(b.trigger)) continue;
      if (related.has(`${a.rule_id}|${b.rule_id}`)) continue;
      fail("unresolved_binding_conflict",
        `rules "${a.rule_id}" and "${b.rule_id}" require and forbid "${a.control_effect.control_key}" under the same trigger with no relation between them`,
        { rule_ids: [a.rule_id, b.rule_id], control_key: a.control_effect.control_key });
    }
  }

  // Removers before their targets, so a rule's own survival is already settled
  // when its removals are applied. The DFS post-order above is reverse
  // topological, hence the reversal here.
  return ordered.reverse();
}

/**
 * Validate one typed rule universe and return a frozen, digest-bound snapshot.
 * The snapshot is a COPY: mutating the caller's policy afterwards cannot reach
 * any decision taken from the compiled universe.
 */
export function compileRuleUniverse(policy) {
  assertObject(policy, "policy");
  assertClosedKeys(policy, UNIVERSE_KEYS, "policy");
  assertRequiredKeys(policy, UNIVERSE_KEYS, "policy");
  if (policy.schema_version !== V5_F05_UNIVERSE_SCHEMA_VERSION) {
    fail("unknown_schema_version", `policy.schema_version must be "${V5_F05_UNIVERSE_SCHEMA_VERSION}"`,
      { expected: V5_F05_UNIVERSE_SCHEMA_VERSION });
  }
  assertTenant(policy.tenant, "policy.tenant");
  assertSafeInteger(policy.universe_version, "policy.universe_version", { min: 1 });
  const completeness = assertEnum(policy.completeness, V5_F05_UNIVERSE_COMPLETENESS,
    "policy.completeness", "unknown_completeness");
  const declared = {
    action: assertDeclaredIdentList(policy.declared_actions, "policy.declared_actions"),
    resource_class: assertDeclaredIdentList(policy.declared_resource_classes,
      "policy.declared_resource_classes"),
  };
  assertArray(policy.rules, "policy.rules", { min: 1, max: 2048 });

  const seen = new Map();
  const rules = policy.rules.map((raw, index) => compileRule(raw, index, seen, declared));
  rules.sort((a, b) => (a.rule_id < b.rule_id ? -1 : 1));
  const removal_order = assertRelationGraph(rules);

  const compiled = {
    compiled: true,
    schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
    universe_version: policy.universe_version,
    tenant: ORGANIZATION_TENANT_ID,
    completeness,
    declared_actions: declared.action,
    declared_resource_classes: declared.resource_class,
    rules,
    removal_order,
  };
  const universe_digest = digest(ruleUniversePreimage(compiled));
  return snapshot({ ...compiled, universe_digest }, "universe");
}

const COMPILED_UNIVERSE_KEYS = Object.freeze([
  "compiled", "schema_version", "universe_version", "tenant", "completeness",
  "declared_actions", "declared_resource_classes", "rules", "removal_order", "universe_digest",
]);

/**
 * Accept only a universe whose contents ARE a canonical compilation.
 *
 * A DIGEST IS NOT PROVENANCE. `digest()` is an unkeyed sha256 over canonical
 * JSON and it is exported, so any caller can build whatever object it likes,
 * hash it itself, and present the pair. Recomputing the hash catches an edit
 * made AFTER compilation and catches nothing at all about a fabrication, which
 * is the case that matters: every compile-time invariant — the class table, the
 * mandatory-control metadata, dangling and version-drifted edges, cycles, the
 * removing-edge conditions, and the removal ORDER that decides which rule
 * survives — only ever runs inside compileRuleUniverse.
 *
 * So the universe is REDERIVED. Its own rules are fed back through
 * compileRuleUniverse and the result must be byte-identical. A forged cyclic
 * graph, a hand-chosen removal order, a mandatory preference, a mandatory rule
 * with a null control effect or a rule with no provenance now fails on the
 * invariant it broke, whether or not the forger rehashed its work.
 *
 * WHY REDERIVATION AND NOT A PRIVATE BRAND. A module-private WeakSet or Symbol
 * is the stronger primitive and it is unusable here: the assembler's own
 * contract canonicalizes the whole request to BYTES and reads the JSON.parse of
 * them, so a compiled universe reaching assembleContextManifest is always a
 * fresh object with no identity to brand. A brand would have made the frozen-
 * bytes path — the property this slice is built on — impossible to satisfy.
 *
 * ORDERED, cheapest first, so the error a caller sees names what they did:
 *   1. Shape, closed keys and the digest, which is the ordinary
 *      edited-after-compilation case and deserves its own reason.
 *   2. Rederivation. Compile errors propagate with their own codes; a forged
 *      universe fails on the invariant it broke.
 *   3. The rederived digest must equal the presented one, which is what catches
 *      a self-consistent forgery whose rules are individually legal but whose
 *      removal order or derived fields are not the canonical ones.
 */
export function requireCompiledUniverse(universe, path = "universe") {
  assertObject(universe, path);
  assertClosedKeys(universe, COMPILED_UNIVERSE_KEYS, path);
  assertRequiredKeys(universe, COMPILED_UNIVERSE_KEYS, path);
  if (universe.compiled !== true || universe.schema_version !== V5_F05_UNIVERSE_SCHEMA_VERSION) {
    fail("universe_not_compiled", `${path} must be the output of compileRuleUniverse`, { path });
  }
  // ONE mutation-immune read, taken before any check. Every step below reads
  // this copy, so an accessor buried in a rule cannot answer the digest check
  // one way and the rederivation another.
  const presented = snapshot(universe, path);
  assertDigestRef(presented.universe_digest, `${path}.universe_digest`);
  const recomputed = digest(ruleUniversePreimage(presented));
  if (recomputed !== presented.universe_digest) {
    fail("universe_digest_mismatch",
      `${path} no longer hashes to its own digest; it was edited after compilation`,
      { path, expected: presented.universe_digest, actual: recomputed });
  }
  assertArray(presented.rules, `${path}.rules`, { min: 1, max: 2048 });
  const rederived = compileRuleUniverse({
    schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
    universe_version: presented.universe_version,
    tenant: presented.tenant,
    completeness: presented.completeness,
    declared_actions: presented.declared_actions,
    declared_resource_classes: presented.declared_resource_classes,
    // `enforcement` is DERIVED from rule_class by the compiler and is not an
    // input; dropping it here is what makes a forged enforcement mechanism show
    // up as a digest difference in step 3 rather than an unknown_field.
    rules: presented.rules.map((rule, index) => {
      const { enforcement: _derived, ...input } = assertObject(rule, `${path}.rules[${index}]`);
      return input;
    }),
  });
  if (rederived.universe_digest !== presented.universe_digest) {
    fail("universe_not_canonically_compiled",
      `${path} carries rules that do not compile to the universe it presents; a self-computed digest over a fabricated shape is not a compilation`,
      { path, expected: rederived.universe_digest, actual: presented.universe_digest });
  }
  return presented;
}

// ---------------------------------------------------------------------------
// Q066 — what a model actually receives for one rule.
//
// ONE MODE IS REACHABLE TODAY AND THE SECOND IS DELIBERATELY NOT. A rule the
// model must INTERPRET arrives with its full binding text. Q066 also settles
// that a control enforced COMPLETELY IN CODE may be represented by its
// resulting constraint instead — and that affordance is unreachable here,
// because nothing this module can call establishes the antecedent.
//
// WHY THE CONSTRAINT MODE IS NOT EMITTED. `code_enforcement.evidence` is five
// strings supplied by the same caller that supplied the rule. The kernel can
// check that they are internally consistent — that the evidence names the
// control version the rule declares, that it is not dated in the future, that
// it is no older than a window the CALLER supplied. It cannot check that any of
// it happened. `verifier_id` is bound to no authority, and
// `implementation_digest`/`evidence_digest` are compared to nothing, because
// there is nothing here to compare them to. Delivering only a resulting
// constraint on that basis would hand the model less than the rule while
// telling the record the control was verified. So a code_enforced rule falls
// back to its FULL BINDING TEXT, and refuses outright when it has none.
//
// The caller's claim is not discarded — it rides along as
// `code_enforcement_claim`, stamped `evidence_verified_by_kernel: false`, so a
// downstream reader gets the evidence AND the fact that nobody checked it. The
// four internal-consistency verdicts are kept apart on purpose: absent
// evidence, evidence about a different control version, evidence from the
// future and evidence past the caller's own window are four different problems
// with four different fixes.
// ---------------------------------------------------------------------------

export const V5_F05_DELIVERY_MODES = deepFreeze([
  "full_binding_text", "code_enforced_constraint", "refused",
]);

/**
 * Q066's constraint-only mode is settled policy and is NOT emitted by this
 * module. It stays in the vocabulary because the decision names it; the flag
 * says, in the hashed kernel projection rather than in a comment, that no code
 * path here produces it. A test asserts no delivery ever carries it.
 */
export const V5_F05_CODE_ENFORCED_CONSTRAINT_MODE_EMITTED = false;

const ENFORCEMENT_EVIDENCE_POLICY_KEYS = Object.freeze(["max_evidence_age_seconds"]);

/**
 * How stale a code-enforcement verification may be before the receipt says so.
 *
 * THERE IS NO DEFAULT, deliberately. The window this module used to ship — a
 * flat 86400 seconds — was invented here, is named in no settled decision, and
 * was read downstream as policy. An UNSUPPLIED policy now yields an age and no
 * verdict, which is the honest answer. A SUPPLIED one is the caller's own
 * number, recorded in the receipt with the fact that they supplied it. Neither
 * is a security assurance: the age of unverified evidence is a diagnostic about
 * a claim, never a statement about the running system.
 */
function readEnforcementEvidencePolicy(raw, path) {
  if (raw === undefined || raw === null) return null;
  assertObject(raw, path);
  assertClosedKeys(raw, ENFORCEMENT_EVIDENCE_POLICY_KEYS, path);
  assertRequiredKeys(raw, ENFORCEMENT_EVIDENCE_POLICY_KEYS, path);
  return assertSafeInteger(raw.max_evidence_age_seconds, `${path}.max_evidence_age_seconds`,
    { min: 0, max: 315_360_000 });
}

/** The caller's enforcement claim, echoed with the fact that nobody verified it. */
function codeEnforcementClaim(control, now, maxAgeSeconds) {
  const base = {
    implementation_ref: control.implementation_ref,
    control_id: control.control_id,
    control_version: control.control_version,
    // Present so a reader can see what the caller says the control does. It is
    // NOT the delivery: `binding_text` is, and this is why the delivered record
    // keeps `resulting_constraint: null` at the top level.
    claimed_resulting_constraint: control.resulting_constraint,
    evidence_verified_by_kernel: false,
    verifier_trusted_by_kernel: false,
    max_evidence_age_seconds: maxAgeSeconds,
    evidence_age_policy_supplied: maxAgeSeconds !== null,
  };
  if (control.evidence === null) {
    return {
      ...base, evidence_present: false, verifier_id: null, verified_at: null,
      implementation_digest: null, evidence_digest: null, evidence_age_seconds: null,
      internally_consistent: false, internal_consistency_reason_id: "evidence_absent",
    };
  }
  const evidence = control.evidence;
  const evidence_age_seconds = (now - Date.parse(evidence.verified_at)) / 1000;
  let internal_consistency_reason_id = "evidence_internally_consistent";
  if (evidence.control_version !== control.control_version) {
    internal_consistency_reason_id = "evidence_control_version_mismatch";
  } else if (evidence_age_seconds < 0) {
    internal_consistency_reason_id = "evidence_from_the_future";
  } else if (maxAgeSeconds !== null && evidence_age_seconds > maxAgeSeconds) {
    internal_consistency_reason_id = "evidence_older_than_supplied_policy";
  }
  return {
    ...base, evidence_present: true,
    verifier_id: evidence.verifier_id,
    verified_at: evidence.verified_at,
    verified_control_version: evidence.control_version,
    implementation_digest: evidence.implementation_digest,
    evidence_digest: evidence.evidence_digest,
    evidence_age_seconds,
    internally_consistent: internal_consistency_reason_id === "evidence_internally_consistent",
    internal_consistency_reason_id,
  };
}

function deliveryForRule(rule, now, maxEvidenceAgeSeconds) {
  const base = {
    rule_id: rule.rule_id, version: rule.version, rule_class: rule.rule_class,
    mandatory: rule.mandatory, enforcement: rule.enforcement,
    summary_is_navigation_only: true, summary: rule.summary,
    code_enforcement_claim: rule.code_enforcement === null
      ? null : codeEnforcementClaim(rule.code_enforcement, now, maxEvidenceAgeSeconds),
  };
  if (rule.binding_text === null) {
    // A code_enforced rule is allowed by the class table to carry no binding
    // text, because Q066 lets a fully-code-enforced control be represented by
    // its constraint. With no trusted verifier that representation is not
    // available, so the rule has nothing deliverable and fails closed.
    return {
      ...base,
      mode: "refused",
      reason_id: rule.rule_class === "code_enforced"
        ? "code_enforcement_unverified_and_no_binding_text"
        : "binding_text_missing",
      binding_text: null, resulting_constraint: null, estimated_tokens: 0,
    };
  }
  return {
    ...base, mode: "full_binding_text", reason_id: "model_interprets_this_rule",
    binding_text: rule.binding_text, resulting_constraint: null,
    estimated_tokens: estimateTokens(rule.binding_text),
  };
}

const PROJECT_RULE_KEYS = Object.freeze([
  "universe", "rule_id", "now", "enforcement_evidence_policy",
]);

/** Project one rule's model delivery on its own, for a caller that holds an id. */
export function projectRuleForModel(request) {
  assertObject(request, "request");
  assertClosedKeys(request, PROJECT_RULE_KEYS, "request");
  assertRequiredKeys(request, ["universe", "rule_id", "now"], "request");
  const universe = requireCompiledUniverse(request.universe, "request.universe");
  const now = assertInstant(request.now, "request.now");
  const maxEvidenceAge = readEnforcementEvidencePolicy(request.enforcement_evidence_policy,
    "request.enforcement_evidence_policy");
  const rule_id = assertExternalIdent(request.rule_id, "request.rule_id", { maxLength: 128 });
  const rule = universe.rules.find(entry => entry.rule_id === rule_id);
  if (rule === undefined) {
    fail("unknown_rule", `"${rule_id}" is not in this universe`, { rule_id });
  }
  const projected = deliveryForRule(rule, now, maxEvidenceAge);
  return deepFreeze({
    schema_version: V5_F05_KERNEL_SCHEMA_VERSION,
    ...projected,
    delivered: projected.mode !== "refused",
    evidence_verified_by_kernel: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Q064 / Q065 / Q087 — the coverage receipt.
// ---------------------------------------------------------------------------

const DERIVE_KEYS = Object.freeze([
  "tenant", "universe", "facts", "semantic_candidates", "now", "enforcement_evidence_policy",
]);
const SEMANTIC_CANDIDATE_KEYS = Object.freeze(["rule_id", "reason", "similarity"]);

export const V5_F05_COVERAGE_BUCKETS = deepFreeze([
  "effective", "possibly_binding", "not_applicable",
  "retired", "superseded", "overridden", "suppressed_by_exception",
]);

/**
 * The buckets whose rules ONCE bound and no longer do. A semantic candidate
 * landing in one of these is delivered as guidance — retrieval is allowed to
 * surface history — and is labelled `historical_non_authority` so the model is
 * never handed a retired rule's text with nothing but a bucket name to tell it
 * apart from a standing one.
 */
export const V5_F05_HISTORICAL_BUCKETS = deepFreeze([
  "overridden", "retired", "superseded", "suppressed_by_exception",
]);

/**
 * Read the typed facts. Absent and "unknown" collapse to the same state, and a
 * declared-dimension value the universe does not know is also unknown — the
 * universe simply may not have caught up with a new action yet, and guessing
 * would be the silent-permission failure. A CLOSED-dimension value outside its
 * vocabulary is a different thing: the fact is unreadable, so it throws.
 */
function readFacts(raw, universe) {
  assertObject(raw, "request.facts");
  assertClosedKeys(raw, V5_F05_FACT_DIMENSIONS, "request.facts");
  const known = {};
  const unknown = [];
  const declared = {
    action: universe.declared_actions,
    resource_class: universe.declared_resource_classes,
  };
  for (const dimension of V5_F05_FACT_DIMENSIONS) {
    const path = `request.facts.${dimension}`;
    const value = raw[dimension];
    if (value === undefined || value === null) {
      unknown.push({ dimension, reason_id: "fact_absent" });
      continue;
    }
    assertSafeText(value, path, { maxLength: 128 });
    if (value === V5_F05_UNKNOWN_FACT) {
      unknown.push({ dimension, reason_id: "fact_declared_unknown" });
      continue;
    }
    const registered = CLOSED_DIMENSION_VALUES[dimension];
    if (registered !== undefined) {
      assertEnum(value, registered, path, `unknown_${dimension}`);
      known[dimension] = value;
      continue;
    }
    assertExternalIdent(value, path, { maxLength: 128 });
    if (!declared[dimension].includes(value)) {
      unknown.push({ dimension, reason_id: "fact_outside_declared_vocabulary", value });
      continue;
    }
    known[dimension] = value;
  }
  return { known, unknown: unknown.sort((a, b) => (a.dimension < b.dimension ? -1 : 1)) };
}

/**
 * Match one predicate against the known facts.
 *
 * A definite mismatch settles the question even when another dimension is
 * unknown — the rule cannot apply either way. An unknown dimension with no
 * mismatch anywhere leaves the answer UNDECIDED, which is what makes a possibly
 * binding rule visible instead of quietly absent.
 */
function matchPredicate(predicate, facts) {
  const reasons = [];
  const undecided = [];
  for (const dimension of Object.keys(predicate).sort()) {
    const permitted = predicate[dimension];
    const value = facts[dimension];
    if (value === undefined) {
      undecided.push(dimension);
      continue;
    }
    if (!permitted.includes(value)) {
      return { verdict: "no", reasons: [], mismatch: { dimension, fact_value: value, permitted: [...permitted] } };
    }
    reasons.push({ dimension, fact_value: value, matched: [...permitted] });
  }
  if (undecided.length > 0) return { verdict: "undecided", reasons, undecided_dimensions: undecided };
  if (reasons.length === 0) return { verdict: "yes", reasons: [], universal: true };
  return { verdict: "yes", reasons };
}

function compileSemanticCandidates(raw, universe) {
  if (raw === undefined || raw === null) return [];
  assertArray(raw, "request.semantic_candidates", { min: 0, max: 256 });
  const seen = new Set();
  return raw.map((entry, index) => {
    const path = `request.semantic_candidates[${index}]`;
    assertObject(entry, path);
    assertClosedKeys(entry, SEMANTIC_CANDIDATE_KEYS, path);
    assertRequiredKeys(entry, ["rule_id", "reason"], path);
    const rule_id = assertExternalIdent(entry.rule_id, `${path}.rule_id`, { maxLength: 128 });
    if (!universe.rules.some(rule => rule.rule_id === rule_id)) {
      fail("semantic_candidate_unknown_rule",
        `${path}.rule_id "${rule_id}" is not in the universe; retrieval cannot invent a rule`,
        { path, rule_id });
    }
    if (seen.has(rule_id)) {
      fail("duplicate_semantic_candidate", `${path} repeats "${rule_id}"`, { path, rule_id });
    }
    seen.add(rule_id);
    const reason = assertSafeText(entry.reason, `${path}.reason`, { maxLength: 512, prose: true });
    let similarity = null;
    if (entry.similarity !== undefined && entry.similarity !== null) {
      if (!Number.isFinite(entry.similarity) || entry.similarity < 0 || entry.similarity > 1) {
        fail("invalid_shape", `${path}.similarity must be a number between 0 and 1`, { path });
      }
      similarity = entry.similarity;
    }
    return { rule_id, reason, similarity };
  }).sort((a, b) => (a.rule_id < b.rule_id ? -1 : 1));
}

function coverageReceiptPreimage(receipt) {
  const { receipt_digest, effects, ...rest } = receipt;
  return rest;
}

/**
 * Derive which rules bind this task, and produce the coverage receipt.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. The request must be readable and closed, tenant-bound, and carry a
 *      universe this module compiled and nobody has edited since.
 *   2. Read the typed facts. Unknown ones are collected, never assumed.
 *   3. Drop retired rules first, by their own expiry against `now`.
 *   4. Classify every remaining rule from its trigger: applicable, possibly
 *      binding (a dimension it needs is unknown) or not applicable, with the
 *      dimension-level reason either way.
 *   5. Walk the relation graph in the compiler's removal order. A rule removes
 *      its targets only if it is itself applicable AND still standing, and only
 *      inside its scoped validity. A remover that is merely POSSIBLY binding
 *      removes nothing and is recorded as a pending relation instead — the
 *      target stays effective. Every edge that did NOT fire is recorded in
 *      `skipped_relations` with the reason, so a removal nobody applied is
 *      visible rather than silent. This is ONE PASS in the compiler's order: a
 *      remover that was itself removed earlier in the pass does not fire, so in
 *      an A-supersedes-B-supersedes-C chain, C survives. The direction is
 *      fail-safe — an extra control, never a missing one — and the skipped edge
 *      is now in the receipt instead of being erased.
 *   6. Detect binding conflicts across the surviving mandatory set. Two rules
 *      requiring and forbidding one control key with no relation between them
 *      is refused; nothing here picks a winner.
 *   7. Deliver each effective and each possibly binding rule under Q066. Any
 *      delivery refusal is a blocking reason.
 *   8. Fold in semantic candidates as GUIDANCE. They are read after the
 *      effective set is final, which is why retrieval cannot remove a control:
 *      no code path exists in which a candidate is an input to step 4 or 5.
 */
export function deriveRuleApplicability(request) {
  assertObject(request, "request");
  assertClosedKeys(request, DERIVE_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "universe", "facts", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const universe = requireCompiledUniverse(request.universe, "request.universe");
  const now = assertInstant(request.now, "request.now");
  const maxEvidenceAge = readEnforcementEvidencePolicy(request.enforcement_evidence_policy,
    "request.enforcement_evidence_policy");
  const { known, unknown } = readFacts(request.facts, universe);
  const candidates = compileSemanticCandidates(request.semantic_candidates, universe);

  const byId = new Map(universe.rules.map(rule => [rule.rule_id, rule]));
  const state = new Map();
  const applicable = [];
  const possibly_binding = [];
  const not_applicable = [];
  const retired = [];

  for (const rule of universe.rules) {
    const stub = { rule_id: rule.rule_id, version: rule.version, rule_class: rule.rule_class,
      scope: rule.scope, owner: rule.owner, mandatory: rule.mandatory };
    if (rule.retirement.behavior === "expires_at" && now >= Date.parse(rule.retirement.expires_at)) {
      state.set(rule.rule_id, "retired");
      retired.push({ ...stub, reason_id: "retired_by_expiry", expires_at: rule.retirement.expires_at });
      continue;
    }
    const match = matchPredicate(rule.trigger, known);
    if (match.verdict === "no") {
      state.set(rule.rule_id, "not_applicable");
      not_applicable.push({ ...stub, reason_id: "typed_fact_mismatch", mismatch: match.mismatch });
      continue;
    }
    if (match.verdict === "undecided") {
      state.set(rule.rule_id, "possibly_binding");
      possibly_binding.push({ ...stub, reason_id: "required_fact_unknown",
        undecided_dimensions: [...match.undecided_dimensions], matched: match.reasons });
      continue;
    }
    state.set(rule.rule_id, "effective");
    applicable.push({ ...stub,
      reason_id: match.universal === true ? "universal_trigger" : "typed_facts_match",
      matched: match.reasons });
  }

  const superseded = [];
  const overridden = [];
  const suppressed_by_exception = [];
  const pending_relations = [];
  const skipped_relations = [];
  const removedBy = new Map();

  for (const rule_id of universe.removal_order) {
    const rule = byId.get(rule_id);
    if (rule.relations.length === 0) continue;
    const removerState = state.get(rule_id);
    for (const relation of rule.relations) {
      const edge = { rule_id, relation: relation.relation,
        target_rule_id: relation.target_rule_id };
      const targetState = state.get(relation.target_rule_id);
      if (targetState !== "effective") {
        // The target was retired, ruled out by the facts, or already removed by
        // an earlier remover in this same pass. Recorded rather than dropped:
        // an edge that pointed at a rule and never fired is a fact about this
        // derivation, and Q087 is about supersession being explicit.
        skipped_relations.push({ ...edge, reason_id: "target_not_effective",
          target_state: targetState ?? null });
        continue;
      }
      if (removerState === "possibly_binding") {
        pending_relations.push({ ...edge, reason_id: "remover_applicability_unknown" });
        continue;
      }
      if (removerState !== "effective") {
        // Includes the finite-supersession case: a remover that was itself
        // superseded earlier in this pass does not fire, so its target returns
        // to the effective set. Fail-safe, and no longer silent.
        skipped_relations.push({ ...edge, reason_id: "remover_not_standing",
          remover_state: removerState ?? null });
        continue;
      }
      const scope = matchPredicate(rule.scoped_validity, known);
      if (scope.verdict === "undecided") {
        pending_relations.push({ ...edge, reason_id: "removal_scope_unknown",
          undecided_dimensions: [...scope.undecided_dimensions] });
        continue;
      }
      if (scope.verdict === "no") {
        // Outside the removing edge's bounded validity; the target stands.
        skipped_relations.push({ ...edge, reason_id: "removal_scope_not_matched",
          mismatch: scope.mismatch });
        continue;
      }
      state.set(relation.target_rule_id, relation.relation === "supersedes" ? "superseded"
        : relation.relation === "overrides" ? "overridden" : "suppressed_by_exception");
      removedBy.set(relation.target_rule_id, { by_rule_id: rule_id, relation: relation.relation });
    }
  }
  skipped_relations.sort((a, b) => (a.rule_id === b.rule_id
    ? (a.target_rule_id < b.target_rule_id ? -1 : 1)
    : (a.rule_id < b.rule_id ? -1 : 1)));

  const effective = [];
  for (const entry of applicable) {
    const current = state.get(entry.rule_id);
    const removal = removedBy.get(entry.rule_id);
    if (current === "effective") { effective.push(entry); continue; }
    const record = { ...entry, reason_id: `removed_by_${removal.relation}`, by_rule_id: removal.by_rule_id };
    if (current === "superseded") superseded.push(record);
    else if (current === "overridden") overridden.push(record);
    else suppressed_by_exception.push(record);
  }

  const byControl = new Map();
  for (const entry of effective) {
    const rule = byId.get(entry.rule_id);
    if (!rule.mandatory) continue;
    const key = rule.control_effect.control_key;
    if (!byControl.has(key)) byControl.set(key, { require: [], forbid: [] });
    byControl.get(key)[rule.control_effect.effect].push(rule.rule_id);
  }
  const binding_conflicts = [];
  for (const key of [...byControl.keys()].sort()) {
    const sides = byControl.get(key);
    if (sides.require.length > 0 && sides.forbid.length > 0) {
      binding_conflicts.push({
        control_key: key, reason_id: "unresolved_binding_conflict",
        require_rule_ids: [...sides.require].sort(), forbid_rule_ids: [...sides.forbid].sort(),
        resolved_by_model: false,
      });
    }
  }

  const delivery = [];
  const delivery_refusals = [];
  for (const entry of [...effective, ...possibly_binding].sort((a, b) =>
    (a.rule_id < b.rule_id ? -1 : 1))) {
    const projected = deliveryForRule(byId.get(entry.rule_id), now, maxEvidenceAge);
    const bucket = state.get(entry.rule_id) === "effective" ? "effective" : "possibly_binding";
    const record = { ...projected, bucket, omissible: false };
    delivery.push(record);
    if (projected.mode === "refused") {
      delivery_refusals.push({ rule_id: entry.rule_id, reason_id: projected.reason_id });
    }
  }

  const semantic_reinforcements = [];
  const semantic_additions = [];
  for (const candidate of candidates) {
    const rule = byId.get(candidate.rule_id);
    if (state.get(candidate.rule_id) === "effective" ||
        state.get(candidate.rule_id) === "possibly_binding") {
      semantic_reinforcements.push({ rule_id: candidate.rule_id, reason: candidate.reason,
        already_bound: true });
      continue;
    }
    const projected = deliveryForRule(rule, now, maxEvidenceAge);
    const bucket = state.get(candidate.rule_id);
    const historical = V5_F05_HISTORICAL_BUCKETS.includes(bucket);
    semantic_additions.push({
      rule_id: candidate.rule_id, reason: candidate.reason, similarity: candidate.similarity,
      bucket,
      // A mandatory rule the typed facts ruled out is NOT elevated by having
      // looked relevant to a retriever; it arrives as guidance and says so.
      elevates_to_control: false,
      delivered_as: "guidance_only",
      // A rule this system has RETIRED, superseded, overridden or excepted away
      // still reads as instruction text once it is in front of a model. The
      // bucket already said which, one field away; this says what the bucket
      // MEANS, in the record rather than in the reader's head. Nothing about a
      // rule's lifecycle is decided here — `bucket` is still the only input.
      historical,
      authority_state: historical ? "historical_non_authority" : "non_authority_guidance",
      mode: projected.mode, binding_text: projected.binding_text,
      resulting_constraint: projected.resulting_constraint,
      code_enforcement_claim: projected.code_enforcement_claim,
      estimated_tokens: projected.estimated_tokens,
      omissible: true,
    });
  }

  const universe_complete = universe.completeness === "complete_authoritative_universe";
  const coverage_complete = universe_complete && unknown.length === 0 &&
    possibly_binding.length === 0 && pending_relations.length === 0;

  const blocking_reasons = [];
  if (!universe_complete) blocking_reasons.push("universe_coverage_unknown");
  if (unknown.length > 0) blocking_reasons.push("typed_facts_unknown");
  if (possibly_binding.length > 0) blocking_reasons.push("possible_binding_rule_undecided");
  if (pending_relations.length > 0) blocking_reasons.push("relation_resolution_pending");
  if (binding_conflicts.length > 0) blocking_reasons.push("unresolved_binding_conflict");
  if (delivery_refusals.length > 0) blocking_reasons.push("rule_delivery_failed_closed");

  const refused = binding_conflicts.length > 0 || delivery_refusals.length > 0;
  const consequential_action_permitted = coverage_complete && !refused;
  const read_only_exploration_permitted = !refused;
  const receipt = {
    schema_version: V5_F05_COVERAGE_SCHEMA_VERSION,
    policy_version: V5_F05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    universe_version: universe.universe_version,
    universe_digest: universe.universe_digest,
    universe_completeness: universe.completeness,
    now: request.now,
    decision: coverageDecision({ consequential_action_permitted, read_only_exploration_permitted }),
    reason_id: binding_conflicts.length > 0 ? "unresolved_binding_conflict"
      : delivery_refusals.length > 0 ? "rule_delivery_failed_closed"
      : coverage_complete ? "coverage_complete" : "coverage_incomplete_read_only",
    facts: { ...known },
    unknown_facts: unknown,
    // Q064's coverage receipt: the COMPLETE supplied universe, every rule in
    // exactly one bucket. The test asserts the partition rather than trusting
    // it, because an unlisted rule is indistinguishable from an absent one.
    universe_rule_ids: universe.rules.map(rule => rule.rule_id),
    effective, possibly_binding, not_applicable, retired,
    superseded, overridden, suppressed_by_exception, pending_relations,
    // Edges that pointed at something and did not fire. Never blocking — the
    // direction is always an extra control — but never erased either.
    skipped_relations,
    removal_is_single_pass_in_compiler_order: true,
    binding_conflicts,
    delivery, delivery_refusals,
    // The one thing a reader must not misread about the delivery records above.
    code_enforcement_evidence_verified_by_kernel: false,
    enforcement_evidence_max_age_seconds: maxEvidenceAge,
    enforcement_evidence_age_policy_supplied: maxEvidenceAge !== null,
    semantic_reinforcements, semantic_additions,
    semantic_may_remove_controls: false,
    model_resolves_conflicts: false,
    coverage_complete,
    consequential_action_permitted,
    read_only_exploration_permitted,
    // The write gate, named in the record rather than in a comment. `decision`
    // agrees with it (allow only when it is true), but this is the field an
    // admission call site reads. Q065's marked read-only exploration is
    // `decision: "read_only"` with this gate false.
    write_gate_field: "consequential_action_permitted",
    blocking_reasons,
  };
  return deepFreeze({
    ...receipt,
    receipt_digest: digest(coverageReceiptPreimage(receipt)),
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Recompute a receipt's digest, so a hand-edited copy cannot pass as one, and
 * check that its `decision` is the one its gates imply. The digest proves the
 * bytes were not edited, not that they were right: a receipt re-hashed after
 * setting `decision: "allow"` on a blocked write must still not pass.
 */
export function verifyCoverageReceipt(receipt) {
  assertObject(receipt, "receipt");
  assertDigestRef(receipt.receipt_digest, "receipt.receipt_digest");
  const recomputed = digest(coverageReceiptPreimage(receipt));
  if (recomputed !== receipt.receipt_digest) {
    fail("coverage_receipt_digest_mismatch",
      "the coverage receipt no longer hashes to its own digest",
      { expected: receipt.receipt_digest, actual: recomputed });
  }
  const implied = coverageDecision(receipt);
  if (receipt.decision !== implied) {
    fail("coverage_receipt_decision_inconsistent",
      "the coverage receipt's decision is not the one its write gate and read gate imply",
      { decision: typeof receipt.decision === "string" ? receipt.decision : null, implied });
  }
  return true;
}

// ---------------------------------------------------------------------------
// The closed projection, and the seams that are deliberately not built.
// ---------------------------------------------------------------------------

export function v5F05RuleKernelPreimage() {
  return {
    schema_version: V5_F05_KERNEL_SCHEMA_VERSION,
    policy_version: V5_F05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_subset_digest: v5F05DecisionSubsetDigest(),
    decisions: V5_F05_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_F05_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_F05_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
    fact_dimensions: V5_F05_FACT_DIMENSIONS.map(dimension => ({
      dimension,
      closed_values: CLOSED_DIMENSION_VALUES[dimension] === undefined
        ? null : [...CLOSED_DIMENSION_VALUES[dimension]],
      declared_by_universe: DECLARED_DIMENSIONS.includes(dimension),
    })),
    unknown_fact_sentinel: V5_F05_UNKNOWN_FACT,
    rule_classes: V5_F05_RULE_CLASSES.map(rule_class => ({
      rule_class, ...RULE_CLASS_TABLE[rule_class],
    })),
    relations: [...V5_F05_RELATIONS],
    control_effects: [...V5_F05_CONTROL_EFFECTS],
    retirement_behaviors: [...V5_F05_RETIREMENT_BEHAVIORS],
    delivery_modes: [...V5_F05_DELIVERY_MODES],
    // What the relation graph admits, so a reader does not have to infer it
    // from three separate refusals.
    removing_edge_requires_scoped_validity: true,
    removing_edge_requires_mandatory_capable_class_against_mandatory_target: true,
    removing_edge_requires_source_provenance: true,
    removing_edge_may_cross_owner_or_scope: false,
    removal_is_single_pass_in_compiler_order: true,
    // Retrieval may surface a retired, superseded, overridden or excepted rule
    // as guidance; every such addition is labelled historical_non_authority.
    historical_buckets: [...V5_F05_HISTORICAL_BUCKETS],
    semantic_addition_from_historical_bucket_is_labelled_non_authority: true,
    // What this kernel does NOT establish about a code_enforced rule.
    code_enforced_constraint_mode_emitted: V5_F05_CODE_ENFORCED_CONSTRAINT_MODE_EMITTED,
    code_enforcement_evidence_verified_by_kernel: false,
    enforcement_evidence_age_policy_is_caller_supplied: true,
    default_enforcement_evidence_max_age_seconds: null,
    mandatory_rule_requires_source_provenance: true,
    removing_rule_requires_source_provenance: true,
    rule_taint_class_resolved_by_kernel: false,
    write_gate_field: "consequential_action_permitted",
    semantic_retrieval_may_remove_controls: false,
    model_resolves_conflicts: false,
  };
}

export function v5F05RuleKernelDigest() {
  return digest(v5F05RuleKernelPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5F05RuleKernelCanonicalBytes() {
  return canonicalJson(v5F05RuleKernelPreimage());
}

/**
 * WHICH RUNTIME SEAMS ARE STILL OPEN, stated rather than implied.
 *
 * `landed` is false where this module can see the answer and null where it
 * cannot, because the thing to build is a handler, a table or a live store
 * rather than a constant it imports. A null is not a pass:
 * assertRuleKernelIntegrationComplete refuses on anything that is not exactly
 * true, so "we could not check it" never reads as "it was done".
 */
export function ruleKernelIntegrationGaps() {
  return deepFreeze([
    {
      gap: "no_live_rule_store_reader",
      where: "mcp-server/src/rule-applicability.v5.js",
      what: "the compiled universe is supplied by the caller; nothing here reads the live"
        + " 166-shared/31-personal rule store, ops/config/rule-triage.v1.json or Neon",
      landed: false,
    },
    {
      gap: "no_action_admission_enforcement",
      where: "mcp-server/src/tools.js",
      what: "no verb consults this coverage receipt before a consequential write; the receipt"
        + " is produced and returned, never enforced at a call site",
      landed: false,
    },
    {
      gap: "no_rule_registry_persistence",
      where: "domain.sql",
      what: "no table holds a rule's class, trigger, relations or enforcement evidence; this"
        + " slice deliberately adds no migration and no ordinal",
      landed: false,
    },
    {
      gap: "no_code_enforcement_verifier",
      where: "ops/",
      what: "nothing produces or checks enforcement evidence; code_enforcement.evidence is"
        + " caller-supplied and is echoed as an unverified claim, so a code_enforced rule is"
        + " delivered as its FULL BINDING TEXT and refuses with"
        + " code_enforcement_unverified_and_no_binding_text when it has none."
        + " code_enforced_constraint is never emitted",
      landed: false,
    },
    {
      gap: "no_relation_authority_grant_verifier",
      where: "mcp-server/src/rule-applicability.v5.js",
      what: "a removing relation is admitted only within one owner and one scope; there is no"
        + " verified grant mechanism that would let one owner or scope remove another's"
        + " control, so a legitimate cross-scope supersession fails closed with"
        + " missing_relation_authority and cannot be expressed at all",
      landed: false,
    },
    {
      gap: "no_rule_provenance_taint_resolver",
      where: "mcp-server/src/rule-applicability.v5.js",
      what: "a mandatory rule and any rule holding a removing edge must name a source record,"
        + " version and content digest, but this module holds no records and resolves none of"
        + " it; whether that source is tainted is decided by the assembler against the manifest"
        + " it was given, and by nothing at all for a caller that uses this kernel on its own",
      landed: false,
    },
  ]);
}

export function assertRuleKernelIntegrationComplete() {
  const open = ruleKernelIntegrationGaps().filter(entry => entry.landed !== true);
  if (open.length > 0) {
    fail("kernel_integration_incomplete",
      `the F05 rule kernel is not integrated: ${open.map(e => e.gap).join(", ")}`,
      { open: open.map(e => ({ gap: e.gap, where: e.where, landed: e.landed })) });
  }
  return true;
}

// ---------------------------------------------------------------------------
// Load-time self-checks. A later edit that breaks one of these fails this
// module's own import rather than a caller's request.
// ---------------------------------------------------------------------------

// COMPILED_UNIVERSE_KEYS is in the list because requireCompiledUniverse runs
// assertClosedKeys over it, so a key added to the compiled shape that collided
// with either guard would refuse every legitimate compiled universe — the same
// failure the other lists are here to prevent, on the one shape every caller of
// this module has to present.
const ACCEPTED_KEY_LISTS = [
  UNIVERSE_KEYS, COMPILED_UNIVERSE_KEYS, RULE_KEYS, CONTROL_EFFECT_KEYS, RETIREMENT_KEYS,
  RELATION_KEYS, CODE_ENFORCEMENT_KEYS, ENFORCEMENT_EVIDENCE_KEYS, RULE_PROVENANCE_KEYS,
  ENFORCEMENT_EVIDENCE_POLICY_KEYS, PROJECT_RULE_KEYS, DERIVE_KEYS, SEMANTIC_CANDIDATE_KEYS,
  V5_F05_FACT_DIMENSIONS,
];

for (const list of ACCEPTED_KEY_LISTS) {
  for (const key of list) {
    if (V5_F05_REFUSED_ASSERTION_FIELDS.includes(key)) {
      throw new V5F05Error("guard_collides_with_accepted_key",
        `the refused-assertion list would refuse the accepted key "${key}"`, { key });
    }
    for (const fragment of V5_F05_AUTHORITY_INJECTION_FRAGMENTS) {
      if (key.includes(fragment)) {
        throw new V5F05Error("guard_collides_with_accepted_key",
          `the authority-injection guard would refuse the accepted key "${key}"`, { key, fragment });
      }
    }
  }
}

for (const rule_class of V5_F05_RULE_CLASSES) {
  const shape = RULE_CLASS_TABLE[rule_class];
  if (shape === undefined || !V5_F05_ENFORCEMENT_MECHANISMS.includes(shape.enforcement)) {
    throw new V5F05Error("invalid_rule_class_table",
      `rule class "${rule_class}" must declare a registered enforcement mechanism`, { rule_class });
  }
  // Q051's whole point: a class that cannot be enforced by a machine cannot be
  // mandatory, and a class that can must ship the tests that prove it.
  // runtime_state is the one exemption and it pays for it with must_expire: its
  // control is the expiry, so an oracle for a condition that ends on Tuesday
  // would be a test of the clock.
  if (shape.may_be_mandatory && !shape.tests_required && !shape.must_expire) {
    throw new V5F05Error("invalid_rule_class_table",
      `mandatory-capable class "${rule_class}" must require tests`, { rule_class });
  }
}
