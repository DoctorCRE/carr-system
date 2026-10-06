// DoctorCRE v5 slice V5-F01 phase 1: authoritative record homes, source
// authority and document identity — the pure domain kernel.
//
// Nine settled decisions (Q004, Q052, Q054, Q071, Q108, Q125, Q129, Q135, Q155)
// are encoded here as ONE closed, versioned domain contract with a deterministic
// digest, plus pure evaluators over that contract. Canonicalization and hashing
// come from artifact-trust.js, the privacy boundary comes from
// global-boundaries.v5.js, and the one server-held tenant comes from
// identity.js; this file reimplements none of them.
//
// WHAT IS CODE HERE AND WHAT MUST ARRIVE AS POLICY, because the line is the
// point of the slice:
//
//   IN CODE — the STRUCTURE the architecture clause settles. The seven
//   authoritative homes, which fact class lives in which home, the surfaces that
//   can never be authoritative, and the closed vocabularies for write direction,
//   version comparison, conflict behaviour, taint, document state, deletion,
//   derivative-registration coverage and the retention clock. These are identity,
//   not configuration.
//
//   AS TYPED POLICY INPUT — every AUTHORITY ASSIGNMENT. Which source owns which
//   field, which direction each source may write, how that field's versions
//   compare, which human class resolves its conflicts, what a class's retention
//   period is, which derivatives survive a deletion. This module invents none of
//   them and ships no default for any of them; missing, ambiguous, duplicate or
//   conflicting policy refuses. The future Neon-backed registry supplies them.
//
//   ONE DEFAULT IS STRUCTURE RATHER THAN POLICY, and it is named here so it is not
//   mistaken for the other kind. WHEN a retention period starts is not a value a
//   class may choose freely: it starts at the server-stamped instant the record
//   layer took custody, and a class may name an explicit typed EVENT instead but
//   may never name the source's own observed timestamp. HOW LONG the period runs
//   remains entirely policy, and this module still ships no period for anything.
//
// TWO KINDS OF NO, following S01 deliberately:
//   * A POLICY ANSWER is returned — a frozen result whose `decision` is "allow",
//     "accept", "no_change", "reconcile", "refuse" or
//     "needs_independent_privacy_route", with a stable `reason_id`. A refusal is
//     an answer the caller may record.
//   * A CONTRACT VIOLATION throws V5F01Error. Unknown fields, unknown vocabulary
//     values, open schemas, accessor properties, malformed Unicode, unreadable
//     timestamps and caller-supplied authority fields are not policy questions;
//     the module cannot read the request at all, so it fails closed rather than
//     guessing which settled boundary was meant.
//
// THE ONE-AUTHORITY RULE. This module VALIDATES and PROJECTS the future
// Neon-backed registry. It stores no registry, no queue, no current state, no
// event, no receipt, no document and no proposal, so it creates no second
// authority. Every function is pure: no filesystem, no network, no database, no
// provider, no scheduler, no environment and no clock. Every evaluation that
// depends on time takes `now` from its caller. `V5_NO_EFFECTS` rides on every
// result to say so in the record.
//
// WHAT THIS FILE IS NOT. It is not persistence, not a handler, not a provider
// adapter and not an acceptance path. It does not make F01 source complete: the
// Neon registry, server-derived actor and tenant handlers, readbacks, legacy
// compatibility and the shared registry tail are all deferred. The SCAC software
// artifact registry in artifact-trust.js is NOT business-document authority and
// is not reached from here; only its canonicalizer and hash are reused. Tour
// tables are not the generic authority for anything, and Tour-only evidence is
// refused as a corporate source by name.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_NO_EFFECTS,
  V5_DATA_CLASSES,
  evaluatePrivacyBoundary,
} from "./global-boundaries.v5.js";

export { V5_NO_EFFECTS };

export const V5_F01_SCHEMA_VERSION = "doctorcre-v5-record-source-authority.v1";
export const V5_F01_POLICY_VERSION = 1;

export const V5_F01_FIELD_REGISTRY_SCHEMA_VERSION =
  "doctorcre-v5-f01-field-authority-registry.v1";
export const V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION =
  "doctorcre-v5-f01-retention-registry.v1";

const DIGEST_REF = /^sha256:[0-9a-f]{64}$/;
// Captured rather than merely shape-matched, because the calendar has to be
// checked against the LITERAL fields; see assertInstant.
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;
// Internal vocabulary values, all of them constants declared in this file.
const VOCAB_SLUG = /^[a-z][a-z0-9_]*$/;
// Caller-declared external identifiers: entity and field names, source system
// and account slugs, native ids, object keys, drive item ids. Deliberately
// wider than VOCAB_SLUG because a corporate source names its own things
// ("Opportunity", "Commission__c", "01A!b-c/d"), and deliberately not open:
// combined with assertSafeText below, nothing invisible or reorderable rides in.
const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
// Control characters, bidirectional overrides, zero-width and other invisible
// format characters. An identifier that renders as another identifier is an
// identity split waiting to happen, so it is refused rather than normalized.
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/u;

/**
 * The separator used to fold a two-part name into one dedupe key.
 *
 * THE INVARIANT: the separator must be a character that no validated identifier
 * can contain. If it could appear inside either half, two different name pairs
 * could fold to one key and one would silently shadow the other. NUL is refused
 * by assertSafeText and is outside EXTERNAL_IDENT, so it can never appear in
 * either half; the test proves that directly rather than assuming it.
 *
 * WRITTEN AS AN ESCAPE, NEVER AS A LITERAL. A literal NUL byte here made the
 * committed blob read as `data` to file(1) and as binary to rg and git diff, so
 * the module that refuses control characters in its own inputs could not itself
 * be reviewed as text. The escape keeps the source ordinary and the key exact.
 */
const KEY_SEPARATOR = "\u0000";
const foldKey = (left, right) => `${left}${KEY_SEPARATOR}${right}`;

export class V5F01Error extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5F01Error";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5F01Error(code, message, detail);
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
// The caller-authority guard.
//
// The later handler derives the authenticated actor and tenant from its own
// context. A caller must therefore never be able to hand this module a field
// that PURPORTS to confer authority — an actor, an admin flag, an override, a
// grant. Those are refused by name before the closed-key check runs, so the
// refusal says what was attempted rather than reporting a generic unknown field.
//
// `tenant` is deliberately NOT on this list. It is a declared field that is
// CHECKED against the one server-held tenant, exactly as S01 checks it: a caller
// may state the tenant and cannot select it, because stating a different one
// refuses. Checked-not-selected is the property that matters.
//
// The load-time self-check at the foot of this file proves no fragment here
// collides with any key the module actually accepts, so the guard can never
// refuse a legitimate request.
// ---------------------------------------------------------------------------

export const V5_F01_AUTHORITY_INJECTION_FRAGMENTS = deepFreeze([
  "actor", "acting_as", "on_behalf_of", "impersonat", "admin", "sudo", "superuser",
  "authority", "authorized_by", "authorization", "authenticated_",
  "privilege", "grant", "delegation", "redecision", "approved_by", "signed_off",
  "override", "bypass", "force_", "trusted_caller", "permission",
  "owner_override", "is_owner", "owner_slug", "as_tenant", "tenant_override",
]);

function assertNoCallerAuthorityFields(keys, path) {
  for (const key of keys) {
    const normalized = key.toLowerCase();
    for (const fragment of V5_F01_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_authority_field_refused",
          `${path}.${key} names authority the caller cannot supply; the handler derives actor and tenant`,
          { path: `${path}.${key}`, key, fragment });
      }
    }
  }
}

/**
 * A property defined as a getter can return a different value on each read, so a
 * decision taken from one read cannot be trusted to describe the input that was
 * validated. Refuse the shape rather than reading it twice and hoping.
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

/** An open schema is a contract violation: an unread field is an unenforced one. */
function assertClosedKeys(object, allowed, path) {
  assertNoAccessorsOrHiddenKeys(object, path);
  const keys = Object.keys(object);
  assertNoCallerAuthorityFields(keys, path);
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

/**
 * Strings that carry identity are checked, not merely typed.
 *
 * Well-formed (no lone surrogate), free of control and invisible format
 * characters, already in NFC, and untrimmed whitespace refused. Two identifiers
 * that render identically but differ in bytes would silently become two records
 * or, worse, one record with two histories; the cheapest place to stop that is
 * before either is stored.
 */
function assertSafeText(value, path, { maxLength = 512 } = {}) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  if (value.length > maxLength) {
    fail("text_too_long", `${path} may be at most ${maxLength} characters`, { path, length: value.length });
  }
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
    fail("malformed_unicode", `${path} contains an unpaired surrogate`, { path });
  }
  if (UNSAFE_TEXT.test(value)) {
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
    fail("invalid_identifier", `${path} is not a permitted external identifier`, { path });
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

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Timestamps are parsed, never inferred. A bare date, a locale string or an
 * offsetless stamp is refused rather than silently read in some ambient zone.
 *
 * THE CALENDAR IS CHECKED AGAINST THE LITERAL FIELDS, BEFORE PARSING, because
 * Date.parse silently NORMALIZES an impossible date rather than rejecting it:
 * "2026-02-31T00:00:00Z" becomes 3 March, and a freshness comparison against an
 * instant nobody wrote is not the comparison the caller asked for.
 *
 * This mirrors S01's assertInstant exactly and deliberately. That helper is not
 * exported and S01 is outside this phase's two-path write cap, so the semantics
 * are reproduced here rather than the boundary being widened to reach it.
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
 * A mutation-immune snapshot of validated caller data.
 *
 * Every compiled registry and every echoed identity is built through this, so a
 * caller that mutates its own object after validation cannot reach a decision
 * taken later. The walk also refuses accessors, so a getter cannot smuggle a
 * different value into the copy than the one that was checked.
 */
function snapshot(value, path, depth = 0) {
  if (depth > 12) fail("input_too_deep", `${path} nests deeper than the contract admits`, { path });
  if (Array.isArray(value)) {
    return Object.freeze(value.map((item, index) => snapshot(item, `${path}[${index}]`, depth + 1)));
  }
  if (isPlainObject(value)) {
    assertNoAccessorsOrHiddenKeys(value, path);
    const out = {};
    for (const key of Object.keys(value)) out[key] = snapshot(value[key], `${path}.${key}`, depth + 1);
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

// ---------------------------------------------------------------------------
// The nine settled decisions. Text and evidence digests are copied verbatim from
// the reviewed F01 source binding; they are identity, not configuration. A
// caller that believes it holds a different subset proves the disagreement here
// rather than discovering it later.
// ---------------------------------------------------------------------------

export const V5_F01_SETTLED_DECISIONS = deepFreeze({
  "Q004.D1": {
    settled_requirement: "One typed authority home for every fact; no hidden authority in prompts, local files, browser sessions, dashboards, or conversation history.",
    source_evidence_digest: "9e0b113ecff9d1b13086fcb03b4165ccd8b88f82d688bb576c58af54fa3b68ad",
  },
  "Q052.D1": {
    settled_requirement: "Authoritative current state plus append-only events and mutation receipts; no whole-product pure event sourcing.",
    source_evidence_digest: "b70bda92d47c19b006ffda90679743af5ce48d160d110ebe9a880c09c3fb8f0d",
  },
  "Q054.D1": {
    settled_requirement: "Neon operating truth; Salesforce field-level corporate authority/provenance through governed browser adapter; visible reconciliation, no silent overwrite.",
    source_evidence_digest: "46d76ca4ac6f7b59566e00145575c9352755b4c73e847c083e767238ecd3d2b3",
  },
  "Q071.D1": {
    settled_requirement: "Source-agnostic immutable-artifact and parsed-proposal contract for Salesforce, OneDrive, and future corporate sources; live adapters are separate.",
    source_evidence_digest: "0011d2df6d9be7ec44948eec44a93399d25c9d10e0f101f554027d17ebc81097",
  },
  "Q108.D1": {
    settled_requirement: "Facts, rules, decisions, work, documents, and source conversations use typed homes; Markdown non-authoritative; summaries/embeddings disposable.",
    source_evidence_digest: "dccf85d5fd869dc721d37a5c69574f4df4cffa09b5b1254ba2a8a57dac8f7233",
  },
  "Q125.D1": {
    settled_requirement: "OneDrive official executed copies; object storage drafts/sealed shares; Neon identity/hashes/state/provenance/policy; OneDrive failure visibly incomplete.",
    source_evidence_digest: "396b6350c7c1af42ab2013c0ea6d27da6f3c15767ed6ecd9ca02b07bade8d07b",
  },
  "Q129.D1": {
    settled_requirement: "Central retention registry per artifact class: home, default retention, holds, deletion proof, constraints, surviving derivatives.",
    source_evidence_digest: "e955d7280dbabeed2feccc5a1dfcceefa80d3bd241f7ec6f4f95b3e99e52d405",
  },
  "Q135.D1": {
    settled_requirement: "Documents carry preparation, delivery, signature, validity, and version states with OneDrive/object-storage/Neon identity split.",
    source_evidence_digest: "06dc23361c4e972066af9a5f0dea499061ccdf8b45fad8c265da1714d9c8f623",
  },
  "Q155.D1": {
    settled_requirement: "Authoritative graphs, children, decisions, contracts, and acceptance live in record layer; diagrams/views/temporary JSON are projections/evidence.",
    source_evidence_digest: "1fdce0e93cd911106682ce465fedff7c29461b98d5fbc6df2f6b1d001cc6aec6",
  },
});

export const V5_F01_SETTLED_DECISION_IDS = deepFreeze(Object.keys(V5_F01_SETTLED_DECISIONS).sort());

/**
 * The canonical digest of the reviewed nine-decision subset.
 *
 * DERIVED rather than hand-typed: a literal here would be a second copy of the
 * same fact that could drift from the table above without anything noticing. The
 * test rebuilds the expected preimage from the source binding's own values, so
 * drift between this module and the binding is still a test failure.
 */
export function v5F01DecisionSubsetPreimage() {
  return {
    schema_version: "doctorcre-v5-f01-decision-subset.v1",
    decisions: V5_F01_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_F01_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_F01_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
  };
}

export function v5F01DecisionSubsetDigest() {
  return digest(v5F01DecisionSubsetPreimage());
}

/**
 * Refuse a caller whose decision subset has drifted from the reviewed one.
 * Drift is checked in both directions — a missing decision and an extra one are
 * both drift — and every source-evidence digest must match exactly.
 */
export function assertF01DecisionBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decisions", "decision_subset_digest"], "binding");
  assertRequiredKeys(binding, ["decisions"], "binding");
  assertObject(binding.decisions, "binding.decisions");
  assertNoAccessorsOrHiddenKeys(binding.decisions, "binding.decisions");
  if ("decision_subset_digest" in binding && binding.decision_subset_digest !== undefined) {
    assertDigestRef(binding.decision_subset_digest, "binding.decision_subset_digest");
    if (binding.decision_subset_digest !== v5F01DecisionSubsetDigest()) {
      fail("decision_binding_drift", "the decision subset digest does not match the reviewed subset",
        { expected: v5F01DecisionSubsetDigest(), actual: binding.decision_subset_digest });
    }
  }
  const supplied = Object.keys(binding.decisions).sort();
  const missing = V5_F01_SETTLED_DECISION_IDS.filter(id => !supplied.includes(id));
  const extra = supplied.filter(id => !V5_F01_SETTLED_DECISION_IDS.includes(id));
  if (missing.length > 0 || extra.length > 0) {
    fail("decision_binding_drift", "the supplied decision set is not the reviewed nine", { missing, extra });
  }
  for (const id of V5_F01_SETTLED_DECISION_IDS) {
    const entry = assertObject(binding.decisions[id], `binding.decisions.${id}`);
    assertClosedKeys(entry, ["source_evidence_digest", "settled_requirement"], `binding.decisions.${id}`);
    assertRequiredKeys(entry, ["source_evidence_digest"], `binding.decisions.${id}`);
    const supplied_digest = entry.source_evidence_digest;
    if (typeof supplied_digest !== "string" || !/^[0-9a-f]{64}$/.test(supplied_digest)) {
      fail("invalid_digest", `binding.decisions.${id}.source_evidence_digest must be 64 lower-case hex characters`,
        { path: `binding.decisions.${id}.source_evidence_digest` });
    }
    if (supplied_digest !== V5_F01_SETTLED_DECISIONS[id].source_evidence_digest) {
      fail("decision_binding_drift", `source-evidence digest drift on ${id}`, {
        decision_id: id, expected: V5_F01_SETTLED_DECISIONS[id].source_evidence_digest, actual: supplied_digest,
      });
    }
    if ("settled_requirement" in entry && entry.settled_requirement !== undefined &&
        entry.settled_requirement !== V5_F01_SETTLED_DECISIONS[id].settled_requirement) {
      fail("decision_binding_drift", `settled requirement text drift on ${id}`, { decision_id: id });
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// Q004 / Q108 / Q155 — the authoritative homes.
//
// Seven homes, and every fact class names exactly one of them. The projection is
// total: a class with no home is not "unowned", it is a class this contract does
// not recognise, and it refuses.
//
// The surfaces that can NEVER be authoritative are enumerated with the same
// weight as the homes, because Q004 is a prohibition as much as an assignment.
// Three dispositions separate them honestly rather than lumping them together:
//   non_authoritative_surface — a place a fact may APPEAR but never LIVE
//                               (Markdown, dashboards, browser state, prompts,
//                               local files, conversation prose).
//   disposable_index          — rebuildable from the home and safe to discard
//                               (summaries, embeddings).
//   generated_projection      — derived FROM the home for reading
//                               (diagrams, operator views).
//   review_evidence           — a temporary artifact of a review, never a record
//                               (temporary JSON).
// ---------------------------------------------------------------------------

export const V5_F01_HOMES = deepFreeze([
  "repository",
  "neon_record_layer",
  "onedrive",
  "object_storage",
  "outlook",
  "salesforce",
  "cloudflare",
]);

export const V5_F01_NON_AUTHORITATIVE_DISPOSITIONS = deepFreeze([
  "non_authoritative_surface", "disposable_index", "generated_projection", "review_evidence",
]);

const FACT_CLASS_TABLE = deepFreeze({
  // repository — code truth
  code: { home: "repository", disposition: "authoritative" },
  config: { home: "repository", disposition: "authoritative" },
  migration: { home: "repository", disposition: "authoritative" },
  test: { home: "repository", disposition: "authoritative" },
  contract_schema: { home: "repository", disposition: "authoritative" },
  // neon_record_layer — typed operating truth (Q108 and Q155 together)
  operating_fact: { home: "neon_record_layer", disposition: "authoritative" },
  rule: { home: "neon_record_layer", disposition: "authoritative" },
  decision: { home: "neon_record_layer", disposition: "authoritative" },
  work_item: { home: "neon_record_layer", disposition: "authoritative" },
  authoritative_graph: { home: "neon_record_layer", disposition: "authoritative" },
  graph_child_reference: { home: "neon_record_layer", disposition: "authoritative" },
  contract: { home: "neon_record_layer", disposition: "authoritative" },
  acceptance: { home: "neon_record_layer", disposition: "authoritative" },
  document_metadata: { home: "neon_record_layer", disposition: "authoritative" },
  source_conversation: { home: "neon_record_layer", disposition: "authoritative" },
  // the governed byte and transport homes
  official_executed_company_copy: { home: "onedrive", disposition: "authoritative" },
  draft_document_bytes: { home: "object_storage", disposition: "authoritative" },
  sealed_share_bytes: { home: "object_storage", disposition: "authoritative" },
  mailbox_history: { home: "outlook", disposition: "authoritative" },
  corporate_transaction_field: { home: "salesforce", disposition: "authoritative" },
  command_transport: { home: "cloudflare", disposition: "authoritative" },
  // the surfaces that cannot hold authority, named as explicitly as the homes
  markdown_render: { home: null, disposition: "non_authoritative_surface" },
  dashboard: { home: null, disposition: "non_authoritative_surface" },
  browser_session: { home: null, disposition: "non_authoritative_surface" },
  conversation_prose: { home: null, disposition: "non_authoritative_surface" },
  prompt: { home: null, disposition: "non_authoritative_surface" },
  local_file: { home: null, disposition: "non_authoritative_surface" },
  summary: { home: null, disposition: "disposable_index" },
  embedding: { home: null, disposition: "disposable_index" },
  diagram: { home: null, disposition: "generated_projection" },
  operator_view: { home: null, disposition: "generated_projection" },
  temporary_json: { home: null, disposition: "review_evidence" },
});

export const V5_F01_FACT_CLASSES = deepFreeze(Object.keys(FACT_CLASS_TABLE).sort());
export const V5_F01_AUTHORITATIVE_FACT_CLASSES = deepFreeze(
  V5_F01_FACT_CLASSES.filter(c => FACT_CLASS_TABLE[c].disposition === "authoritative"));
export const V5_F01_NON_AUTHORITATIVE_FACT_CLASSES = deepFreeze(
  V5_F01_FACT_CLASSES.filter(c => FACT_CLASS_TABLE[c].disposition !== "authoritative"));

const HOME_REQUEST_KEYS = Object.freeze(["tenant", "fact_class", "claimed_home", "claimed_authoritative"]);

/**
 * Project the one authoritative home for one fact class.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. The request must be readable, closed and carry the one server-held tenant.
 *   2. The fact class must be registered; an unregistered one throws.
 *   3. A non-authoritative class claiming authority REFUSES, naming its
 *      disposition. Not claiming authority is allowed and reports home: null.
 *   4. An authoritative class whose claimed_home differs from the canonical one
 *      REFUSES; a claimed home that is not a registered home throws.
 *   5. Otherwise allow, reporting the single canonical home.
 */
export function projectRecordHome(request) {
  assertObject(request, "request");
  assertClosedKeys(request, HOME_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "fact_class"], "request");
  assertTenant(request.tenant, "request.tenant");
  assertEnum(request.fact_class, V5_F01_FACT_CLASSES, "request.fact_class", "unknown_fact_class");
  if (request.claimed_authoritative !== undefined && request.claimed_authoritative !== null) {
    assertBoolean(request.claimed_authoritative, "request.claimed_authoritative");
  }
  if (request.claimed_home !== undefined && request.claimed_home !== null) {
    assertEnum(request.claimed_home, V5_F01_HOMES, "request.claimed_home", "unknown_home");
  }
  const entry = FACT_CLASS_TABLE[request.fact_class];
  const base = {
    tenant: ORGANIZATION_TENANT_ID,
    fact_class: request.fact_class,
    disposition: entry.disposition,
    authoritative_home: entry.home,
    claimed_home: request.claimed_home ?? null,
    hidden_authority_permitted: false,
    effects: V5_NO_EFFECTS,
  };

  if (entry.disposition !== "authoritative") {
    if (request.claimed_authoritative === true || request.claimed_home !== undefined) {
      return deepFreeze({
        decision: "refuse", reason_id: "surface_cannot_hold_authority", ...base, authoritative: false,
      });
    }
    return deepFreeze({
      decision: "allow", reason_id: `class_is_${entry.disposition}`, ...base, authoritative: false,
    });
  }
  if (request.claimed_home !== undefined && request.claimed_home !== entry.home) {
    return deepFreeze({
      decision: "refuse", reason_id: "home_mismatch", ...base, authoritative: true,
    });
  }
  return deepFreeze({
    decision: "allow", reason_id: "single_authoritative_home", ...base, authoritative: true,
  });
}

// ---------------------------------------------------------------------------
// Q054 — the source × entity × field authority registry.
//
// THE VOCABULARIES BELOW ARE CODE. THE ENTRIES ARE NOT. This module ships no
// field, no owner, no direction and no resolver: compileFieldAuthorityRegistry
// validates a caller's typed policy and hands back a frozen, digest-bound
// snapshot. Every assignment is the future Neon-backed authority's to make.
//
// DIRECTION IS RELATIVE TO THE RECORD LAYER, always:
//   inbound        the source may write INTO the record layer.
//   outbound       the record layer writes OUT to the source; an observation
//                  arriving FROM that source is a forbidden write direction.
//   bidirectional  both.
//   none           the source may be read for context and may never write.
//
// VERSION COMPARISON IS DECLARED, NEVER GUESSED. An opaque provider version gets
// equality or an explicit ascending order supplied by policy. Lexical ordering of
// an opaque token is not a comparison, it is a coin toss with a plausible shape,
// so `opaque_equality` answers "different" and routes to reconciliation rather
// than inventing which side is newer.
// ---------------------------------------------------------------------------

export const V5_F01_WRITE_DIRECTIONS = deepFreeze(["inbound", "outbound", "bidirectional", "none"]);
export const V5_F01_VERSION_COMPARATORS = deepFreeze([
  "integer_sequence", "instant", "opaque_equality", "declared_order",
]);
export const V5_F01_CONFLICT_BEHAVIORS = deepFreeze(["refuse", "reconcile"]);
export const V5_F01_TAINT_CLASSES = deepFreeze([
  "first_party_record_layer", "corporate_source_of_record", "untrusted_parsed", "untrusted_external",
]);

const REGISTRY_KEYS = Object.freeze(["schema_version", "registry_version", "tenant", "entries"]);
const ENTRY_KEYS = Object.freeze([
  "entity", "field", "authoritative_home", "owner_source", "permitted_sources",
  "requires_account_identity", "requires_native_identity", "version_comparator", "version_order",
  "conflict_behavior", "human_resolver_class", "readback_required", "sensitivity_classes", "taint_class",
]);
const ENTRY_REQUIRED = Object.freeze([
  "entity", "field", "authoritative_home", "owner_source", "permitted_sources",
  "requires_account_identity", "requires_native_identity", "version_comparator",
  "conflict_behavior", "human_resolver_class", "readback_required", "sensitivity_classes", "taint_class",
]);
const PERMITTED_SOURCE_KEYS = Object.freeze(["source_system", "direction"]);

const WRITING_DIRECTIONS = Object.freeze(["inbound", "bidirectional"]);

function compileEntry(raw, index, seen) {
  const path = `policy.entries[${index}]`;
  assertObject(raw, path);
  assertClosedKeys(raw, ENTRY_KEYS, path);
  assertRequiredKeys(raw, ENTRY_REQUIRED, path);

  const entity = assertExternalIdent(raw.entity, `${path}.entity`, { maxLength: 128 });
  const field = assertExternalIdent(raw.field, `${path}.field`, { maxLength: 128 });
  const key = foldKey(entity, field);
  if (seen.has(key)) {
    fail("duplicate_registry_entry",
      `${path} repeats the binding for ${entity}.${field}; a field with two entries has no owner`,
      { path, entity, field, first_index: seen.get(key) });
  }
  seen.set(key, index);

  const authoritative_home = assertEnum(raw.authoritative_home, V5_F01_HOMES,
    `${path}.authoritative_home`, "unknown_home");
  const owner_source = assertExternalIdent(raw.owner_source, `${path}.owner_source`, { maxLength: 128 });

  assertArray(raw.permitted_sources, `${path}.permitted_sources`, { min: 1, max: 32 });
  const sources = [];
  const sourceSeen = new Map();
  raw.permitted_sources.forEach((entry, i) => {
    const sourcePath = `${path}.permitted_sources[${i}]`;
    assertObject(entry, sourcePath);
    assertClosedKeys(entry, PERMITTED_SOURCE_KEYS, sourcePath);
    assertRequiredKeys(entry, PERMITTED_SOURCE_KEYS, sourcePath);
    const source_system = assertExternalIdent(entry.source_system, `${sourcePath}.source_system`,
      { maxLength: 128 });
    if (sourceSeen.has(source_system)) {
      fail("duplicate_permitted_source",
        `${sourcePath} repeats source "${source_system}"; two directions for one source is ambiguous policy`,
        { path: sourcePath, source_system, first_index: sourceSeen.get(source_system) });
    }
    sourceSeen.set(source_system, i);
    const direction = assertEnum(entry.direction, V5_F01_WRITE_DIRECTIONS,
      `${sourcePath}.direction`, "unknown_write_direction");
    sources.push({ source_system, direction });
  });

  // An owner that cannot write is not an owner; the policy contradicts itself.
  const ownerEntry = sources.find(s => s.source_system === owner_source);
  if (ownerEntry === undefined) {
    fail("owner_source_not_permitted",
      `${path}.owner_source "${owner_source}" is not among the permitted sources`,
      { path, owner_source });
  }
  if (!WRITING_DIRECTIONS.includes(ownerEntry.direction)) {
    fail("owner_source_cannot_write",
      `${path}.owner_source "${owner_source}" has direction "${ownerEntry.direction}" and could never establish the field`,
      { path, owner_source, direction: ownerEntry.direction });
  }

  const version_comparator = assertEnum(raw.version_comparator, V5_F01_VERSION_COMPARATORS,
    `${path}.version_comparator`, "unknown_version_comparator");
  let version_order = null;
  if (version_comparator === "declared_order") {
    if (raw.version_order === undefined || raw.version_order === null) {
      fail("missing_version_order",
        `${path}.version_order must list the ascending tokens; declared_order without an order cannot compare`,
        { path });
    }
    assertArray(raw.version_order, `${path}.version_order`, { min: 2, max: 128 });
    const orderSeen = new Set();
    version_order = raw.version_order.map((token, i) => {
      const value = assertSafeText(token, `${path}.version_order[${i}]`, { maxLength: 128 });
      if (orderSeen.has(value)) {
        fail("duplicate_version_token", `${path}.version_order repeats "${value}"; the order is ambiguous`,
          { path, token: value });
      }
      orderSeen.add(value);
      return value;
    });
  } else if (raw.version_order !== undefined && raw.version_order !== null) {
    fail("unused_version_order",
      `${path}.version_order is only read for the declared_order comparator; unused policy is ambiguous policy`,
      { path, version_comparator });
  }

  const conflict_behavior = assertEnum(raw.conflict_behavior, V5_F01_CONFLICT_BEHAVIORS,
    `${path}.conflict_behavior`, "unknown_conflict_behavior");
  const human_resolver_class = assertExternalIdent(raw.human_resolver_class,
    `${path}.human_resolver_class`, { maxLength: 128 });
  const requires_account_identity = assertBoolean(raw.requires_account_identity,
    `${path}.requires_account_identity`);
  const requires_native_identity = assertBoolean(raw.requires_native_identity,
    `${path}.requires_native_identity`);
  const readback_required = assertBoolean(raw.readback_required, `${path}.readback_required`);

  assertArray(raw.sensitivity_classes, `${path}.sensitivity_classes`, { min: 1, max: 16 });
  const classSeen = new Set();
  const sensitivity_classes = raw.sensitivity_classes.map((cls, i) => {
    const value = assertEnum(cls, V5_DATA_CLASSES, `${path}.sensitivity_classes[${i}]`, "unknown_data_class");
    if (classSeen.has(value)) {
      fail("duplicate_sensitivity_class", `${path}.sensitivity_classes repeats "${value}"`, { path });
    }
    classSeen.add(value);
    return value;
  }).sort();
  // The S01 boundary decides, here as everywhere. A registry entry that declares
  // a prohibited class is refused at COMPILE, so a fixture carrying PHI cannot
  // exist to be resolved against later.
  const privacy = evaluatePrivacyBoundary({ data_classes: sensitivity_classes });
  if (privacy.decision === "refuse") {
    fail("registry_entry_privacy_refused",
      `${path}.sensitivity_classes declares a prohibited class; S01 refuses it and so does this registry`,
      { path, reason_id: privacy.reason_id, prohibited_classes: [...(privacy.prohibited_classes ?? [])] });
  }

  const taint_class = assertEnum(raw.taint_class, V5_F01_TAINT_CLASSES, `${path}.taint_class`,
    "unknown_taint_class");

  return {
    entity, field, authoritative_home, owner_source,
    permitted_sources: sources.slice().sort((a, b) => a.source_system < b.source_system ? -1 : 1),
    requires_account_identity, requires_native_identity,
    version_comparator, version_order,
    conflict_behavior, human_resolver_class, readback_required,
    sensitivity_classes, taint_class,
    privacy_route: privacy.decision === "needs_independent_privacy_route"
      ? "needs_independent_privacy_route" : "permitted",
  };
}

/** The exact bytes a compiled registry hashes to, so a reviewer can check it by hand. */
export function fieldAuthorityRegistryPreimage(compiled) {
  return {
    schema_version: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
    registry_version: compiled.registry_version,
    tenant: compiled.tenant,
    entries: compiled.entries.map(entry => ({
      entity: entry.entity,
      field: entry.field,
      authoritative_home: entry.authoritative_home,
      owner_source: entry.owner_source,
      permitted_sources: entry.permitted_sources.map(s => ({ ...s })),
      requires_account_identity: entry.requires_account_identity,
      requires_native_identity: entry.requires_native_identity,
      version_comparator: entry.version_comparator,
      version_order: entry.version_order === null ? null : [...entry.version_order],
      conflict_behavior: entry.conflict_behavior,
      human_resolver_class: entry.human_resolver_class,
      readback_required: entry.readback_required,
      sensitivity_classes: [...entry.sensitivity_classes],
      taint_class: entry.taint_class,
      privacy_route: entry.privacy_route,
    })),
  };
}

/**
 * Validate one typed field-authority policy and return a frozen, digest-bound
 * snapshot of it. The snapshot is a COPY: mutating the caller's policy object
 * afterwards cannot reach any decision taken from the compiled registry.
 */
export function compileFieldAuthorityRegistry(policy) {
  assertObject(policy, "policy");
  assertClosedKeys(policy, REGISTRY_KEYS, "policy");
  assertRequiredKeys(policy, REGISTRY_KEYS, "policy");
  if (policy.schema_version !== V5_F01_FIELD_REGISTRY_SCHEMA_VERSION) {
    fail("unknown_schema_version",
      `policy.schema_version must be "${V5_F01_FIELD_REGISTRY_SCHEMA_VERSION}"`,
      { expected: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION });
  }
  assertTenant(policy.tenant, "policy.tenant");
  assertSafeInteger(policy.registry_version, "policy.registry_version", { min: 1 });
  assertArray(policy.entries, "policy.entries", { min: 1, max: 512 });

  const seen = new Map();
  const entries = policy.entries.map((raw, index) => compileEntry(raw, index, seen));
  entries.sort((a, b) => (a.entity === b.entity
    ? (a.field < b.field ? -1 : 1)
    : (a.entity < b.entity ? -1 : 1)));

  const compiled = {
    compiled: true,
    schema_version: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
    registry_version: policy.registry_version,
    tenant: ORGANIZATION_TENANT_ID,
    entries,
  };
  const registry_digest = digest(fieldAuthorityRegistryPreimage(compiled));
  return snapshot({ ...compiled, registry_digest }, "registry");
}

const COMPILED_REGISTRY_KEYS = Object.freeze([
  "compiled", "schema_version", "registry_version", "tenant", "entries", "registry_digest",
]);

/**
 * Accept only a registry this module compiled and nobody has edited since.
 *
 * The digest is RECOMPUTED rather than trusted, so a hand-forged object carrying
 * `compiled: true` and a copied digest is refused: the bytes have to hash to the
 * claim. This is what stops a caller from routing around compile-time policy
 * validation by fabricating the compiled shape directly.
 */
function requireCompiledRegistry(registry, path) {
  assertObject(registry, path);
  assertClosedKeys(registry, COMPILED_REGISTRY_KEYS, path);
  assertRequiredKeys(registry, COMPILED_REGISTRY_KEYS, path);
  if (registry.compiled !== true || registry.schema_version !== V5_F01_FIELD_REGISTRY_SCHEMA_VERSION) {
    fail("registry_not_compiled", `${path} must be the output of compileFieldAuthorityRegistry`, { path });
  }
  assertDigestRef(registry.registry_digest, `${path}.registry_digest`);
  const recomputed = digest(fieldAuthorityRegistryPreimage(registry));
  if (recomputed !== registry.registry_digest) {
    fail("registry_digest_mismatch",
      `${path} no longer hashes to its own digest; it was edited after compilation`,
      { path, expected: registry.registry_digest, actual: recomputed });
  }
  return registry;
}

// ---------------------------------------------------------------------------
// Q052 / Q054 — resolving one observation against current state.
//
// THREE RECORDS COME OUT OF AN ACCEPTED CHANGE AND NONE STANDS IN FOR ANOTHER.
// That is the whole of Q052: authoritative current state, an append-only event,
// and a mutation receipt. The receipt BINDS the other two by digest, so it can
// never be mistaken for either; neither of the other two references the receipt,
// so neither can be mistaken for it. The module emits preimages — the exact
// bytes the future Neon tables will store — and stores nothing itself.
//
// NOTHING IS EVER DECIDED BY WHO WROTE LAST. Every path that could be a silent
// last-write-wins is named and routed: a stale observation refuses, an
// equal-version disagreement becomes a visible reconciliation item, an ordering
// that cannot be known stays unknown, and a non-owner may confirm the owner's
// value but never replace it.
// ---------------------------------------------------------------------------

export const V5_F01_TRANSITION_SCHEMA_VERSION = "doctorcre-v5-f01-current-state-transition.v1";
export const V5_F01_EVENT_SCHEMA_VERSION = "doctorcre-v5-f01-source-event.v1";
export const V5_F01_RECEIPT_SCHEMA_VERSION = "doctorcre-v5-f01-mutation-receipt.v1";
export const V5_F01_RECONCILIATION_SCHEMA_VERSION = "doctorcre-v5-f01-reconciliation-item.v1";

export const V5_F01_RECORD_KINDS = deepFreeze([
  "current_state_transition", "append_only_event", "mutation_receipt",
]);

const RESOLVE_KEYS = Object.freeze(["tenant", "registry", "current_state", "observation", "now"]);
const NATIVE_IDENTITY_KEYS = Object.freeze(["source_system", "native_id", "native_id_epoch"]);
const PROVENANCE_KEYS = Object.freeze(["adapter_kind", "evidence_ref", "retrieval_class"]);
const READBACK_KEYS = Object.freeze(["confirmed", "readback_at", "readback_value_digest"]);
const OBSERVATION_KEYS = Object.freeze([
  "entity", "field", "tenant", "source_system", "account", "native_identity",
  "value_digest", "version", "observed_at", "provenance", "declared_data_classes",
  "taint_class", "readback",
]);
const CURRENT_STATE_KEYS = Object.freeze([
  "entity", "field", "tenant", "account", "native_identity", "value_digest", "version",
  "owner_source", "observed_at", "event_seq", "last_event_digest",
]);

function assertNativeIdentity(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, NATIVE_IDENTITY_KEYS, path);
  assertRequiredKeys(value, NATIVE_IDENTITY_KEYS, path);
  return {
    source_system: assertExternalIdent(value.source_system, `${path}.source_system`, { maxLength: 128 }),
    native_id: assertExternalIdent(value.native_id, `${path}.native_id`, { maxLength: 255 }),
    // The epoch is how a RECYCLED identifier becomes visible. A corporate source
    // that reuses a native id for a different record supplies a different epoch;
    // the same id under a different epoch is a different record wearing an old
    // name, and it is refused rather than merged into the record it resembles.
    native_id_epoch: assertExternalIdent(value.native_id_epoch, `${path}.native_id_epoch`, { maxLength: 128 }),
  };
}

function assertProvenance(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, PROVENANCE_KEYS, path);
  assertRequiredKeys(value, PROVENANCE_KEYS, path);
  return {
    adapter_kind: assertExternalIdent(value.adapter_kind, `${path}.adapter_kind`, { maxLength: 128 }),
    evidence_ref: assertExternalIdent(value.evidence_ref, `${path}.evidence_ref`, { maxLength: 255 }),
    retrieval_class: assertExternalIdent(value.retrieval_class, `${path}.retrieval_class`, { maxLength: 128 }),
  };
}

function assertVersion(entry, value, path) {
  if (entry.version_comparator === "integer_sequence") {
    return assertSafeInteger(value, path, { min: 0 });
  }
  if (entry.version_comparator === "instant") {
    assertInstant(value, path);
    return value;
  }
  const token = assertSafeText(value, path, { maxLength: 128 });
  if (entry.version_comparator === "declared_order" && !entry.version_order.includes(token)) {
    fail("version_outside_declared_order",
      `${path} is not in this field's declared version order; ordering is never inferred from the token`,
      { path, declared_order: [...entry.version_order] });
  }
  return token;
}

/**
 * Compare an observed version with the established one.
 *
 * Returns "newer", "older", "equal" or "indeterminate". An opaque provider
 * version that merely DIFFERS is indeterminate — never "newer" — because the
 * only honest thing a comparator can say about two opaque tokens is whether they
 * are the same one.
 */
function compareVersions(entry, observed, current) {
  if (entry.version_comparator === "integer_sequence") {
    if (observed === current) return "equal";
    return observed > current ? "newer" : "older";
  }
  if (entry.version_comparator === "instant") {
    const a = Date.parse(observed);
    const b = Date.parse(current);
    if (a === b) return "equal";
    return a > b ? "newer" : "older";
  }
  if (entry.version_comparator === "declared_order") {
    const a = entry.version_order.indexOf(observed);
    const b = entry.version_order.indexOf(current);
    if (a === b) return "equal";
    return a > b ? "newer" : "older";
  }
  return observed === current ? "equal" : "indeterminate";
}

function resolutionResult(fields) {
  return deepFreeze({
    ...fields,
    silent_last_write_wins: false,
    applied: fields.decision === "accept",
    effects: V5_NO_EFFECTS,
  });
}

function reconciliationItem(entry, observation, current, conflict_kind) {
  return {
    schema_version: V5_F01_RECONCILIATION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    entity: entry.entity,
    field: entry.field,
    conflict_kind,
    human_resolver_class: entry.human_resolver_class,
    authoritative_home: entry.authoritative_home,
    owner_source: entry.owner_source,
    established: current === null ? null : {
      value_digest: current.value_digest,
      version: current.version,
      owner_source: current.owner_source,
      observed_at: current.observed_at,
    },
    observed: {
      value_digest: observation.value_digest,
      version: observation.version,
      source_system: observation.source_system,
      account: observation.account,
      observed_at: observation.observed_at,
    },
    applied: false,
    visible: true,
    resolved_by_machine: false,
  };
}

/**
 * Resolve one source observation against the established current state.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1.  The request must be readable, closed, tenant-bound, and carry a registry
 *       this module compiled and nobody has edited since.
 *   2.  The (entity, field) pair must be bound in the registry.
 *   3.  Identity the entry REQUIRES must be present: account, native identity,
 *       version, observed time, provenance. Each absence is its own refusal.
 *   4.  The S01 privacy boundary decides on the entry's classes together with
 *       the observation's declared ones. PHI refuses; an aggregate class routes.
 *   5.  The source must be permitted for this field.
 *   6.  Its direction must permit writing INTO the record layer.
 *   7.  Tenant, then account, then native-identity epoch must match the
 *       established record. Cross-tenant, cross-account and recycled native id
 *       each refuse by name, and the established record must itself be
 *       believable: bound to the registered owner, on an intact event chain, and
 *       observed no later than `now`.
 *   8.  A readback the entry REQUIRES must be present, and ANY supplied readback
 *       — required or not — must be confirmed, about this value, and read
 *       between the observation and `now`.
 *   9.  The observation must not be observed after `now`.
 *   10. With no established value: the OWNER establishes it; a non-owner cannot.
 *   11. With one: compare versions. Older refuses. Indeterminate conflicts.
 *       Equal with a different value conflicts. Newer with the same value or a
 *       newer owner value is accepted. Newer from a NON-owner with a different
 *       value is a forbidden overwrite and conflicts rather than applying.
 *   12. A conflict is routed by the entry's own conflict_behavior — refuse or
 *       reconcile — and carries a visible reconciliation item either way.
 */
export function resolveObservation(request) {
  assertObject(request, "request");
  assertClosedKeys(request, RESOLVE_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "registry", "observation", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const registry = requireCompiledRegistry(request.registry, "request.registry");

  const rawObservation = assertObject(request.observation, "request.observation");
  assertClosedKeys(rawObservation, OBSERVATION_KEYS, "request.observation");
  assertRequiredKeys(rawObservation, ["entity", "field", "tenant", "source_system"], "request.observation");
  const entity = assertExternalIdent(rawObservation.entity, "request.observation.entity", { maxLength: 128 });
  const field = assertExternalIdent(rawObservation.field, "request.observation.field", { maxLength: 128 });
  const source_system = assertExternalIdent(rawObservation.source_system,
    "request.observation.source_system", { maxLength: 128 });

  // Step 2. Unknown binding: this module will not guess a home or an owner.
  const entry = registry.entries.find(e => e.entity === entity && e.field === field);
  const base = {
    tenant: ORGANIZATION_TENANT_ID, entity, field, source_system,
    registry_digest: registry.registry_digest,
    authoritative_home: entry ? entry.authoritative_home : null,
    owner_source: entry ? entry.owner_source : null,
    current_state_transition: null, event: null, mutation_receipt: null,
    reconciliation_item: null,
  };
  if (entry === undefined) {
    return resolutionResult({ decision: "refuse", reason_id: "unknown_field_binding", ...base });
  }
  if (rawObservation.tenant !== ORGANIZATION_TENANT_ID) {
    return resolutionResult({ decision: "refuse", reason_id: "cross_tenant_refused", ...base });
  }

  // Step 3. Required identity, each absence named separately.
  if (rawObservation.version === undefined || rawObservation.version === null) {
    return resolutionResult({ decision: "refuse", reason_id: "missing_version", ...base });
  }
  if (rawObservation.observed_at === undefined || rawObservation.observed_at === null) {
    return resolutionResult({ decision: "refuse", reason_id: "missing_observed_time", ...base });
  }
  if (rawObservation.provenance === undefined || rawObservation.provenance === null) {
    return resolutionResult({ decision: "refuse", reason_id: "missing_provenance", ...base });
  }
  if (entry.requires_account_identity &&
      (rawObservation.account === undefined || rawObservation.account === null)) {
    return resolutionResult({ decision: "refuse", reason_id: "missing_account_identity", ...base });
  }
  if (entry.requires_native_identity &&
      (rawObservation.native_identity === undefined || rawObservation.native_identity === null)) {
    return resolutionResult({ decision: "refuse", reason_id: "missing_native_identity", ...base });
  }

  const version = assertVersion(entry, rawObservation.version, "request.observation.version");
  const observedAt = assertInstant(rawObservation.observed_at, "request.observation.observed_at");
  const provenance = assertProvenance(rawObservation.provenance, "request.observation.provenance");
  const value_digest = assertDigestRef(rawObservation.value_digest, "request.observation.value_digest");
  const account = rawObservation.account === undefined || rawObservation.account === null
    ? null
    : assertExternalIdent(rawObservation.account, "request.observation.account", { maxLength: 128 });
  const native_identity = rawObservation.native_identity === undefined ||
    rawObservation.native_identity === null
    ? null
    : assertNativeIdentity(rawObservation.native_identity, "request.observation.native_identity");
  if (native_identity !== null && native_identity.source_system !== source_system) {
    return resolutionResult({ decision: "refuse", reason_id: "native_identity_source_mismatch", ...base });
  }
  // THE REGISTRY OWNS THE TAINT, AND A CALLER CANNOT SOFTEN IT. The observation
  // may restate the registered class, which is a useful redundancy check, and
  // may omit it, which inherits. What it may not do is DISAGREE: a Salesforce
  // observation declaring itself first-party would launder a corporate-source
  // value into a first-party one, so a mismatch refuses in either direction
  // rather than the caller's word winning.
  if (rawObservation.taint_class !== undefined && rawObservation.taint_class !== null) {
    const declared = assertEnum(rawObservation.taint_class, V5_F01_TAINT_CLASSES,
      "request.observation.taint_class", "unknown_taint_class");
    if (declared !== entry.taint_class) {
      return resolutionResult({
        decision: "refuse", reason_id: "taint_class_mismatch", ...base,
        registered_taint_class: entry.taint_class, declared_taint_class: declared,
      });
    }
  }
  const taint_class = entry.taint_class;

  // ANY SUPPLIED READBACK IS READ, whether or not policy asks for one. A field
  // that is validated only when it is required is a field a caller can attach
  // anything to the rest of the time, and an unread field is an unenforced one.
  // The SHAPE is checked here; the CONTENT — confirmation, value binding and
  // time window — is judged at step 8, on every supplied readback alike.
  let readback = null;
  if (rawObservation.readback !== undefined && rawObservation.readback !== null) {
    const rawReadback = assertObject(rawObservation.readback, "request.observation.readback");
    assertClosedKeys(rawReadback, READBACK_KEYS, "request.observation.readback");
    assertRequiredKeys(rawReadback, READBACK_KEYS, "request.observation.readback");
    readback = {
      confirmed: assertBoolean(rawReadback.confirmed, "request.observation.readback.confirmed"),
      readback_at: rawReadback.readback_at,
      readback_value_digest: assertDigestRef(rawReadback.readback_value_digest,
        "request.observation.readback.readback_value_digest"),
    };
    assertInstant(readback.readback_at, "request.observation.readback.readback_at");
  }

  let declared_data_classes = [];
  if (rawObservation.declared_data_classes !== undefined && rawObservation.declared_data_classes !== null) {
    assertArray(rawObservation.declared_data_classes, "request.observation.declared_data_classes",
      { min: 1, max: 16 });
    declared_data_classes = rawObservation.declared_data_classes.map((cls, i) =>
      assertEnum(cls, V5_DATA_CLASSES, `request.observation.declared_data_classes[${i}]`,
        "unknown_data_class"));
  }

  const observation = {
    entity, field, source_system, account, native_identity, value_digest, version,
    observed_at: rawObservation.observed_at, provenance, taint_class,
    declared_data_classes: [...declared_data_classes].sort(),
  };

  // Step 4. S01 decides privacy; this phase adds no classifier and no bypass.
  const privacyClasses = [...new Set([...entry.sensitivity_classes, ...declared_data_classes])].sort();
  const privacy = evaluatePrivacyBoundary({ data_classes: privacyClasses });
  if (privacy.decision === "refuse") {
    return resolutionResult({
      decision: "refuse", reason_id: privacy.reason_id, ...base,
      privacy_decision: privacy.decision,
      prohibited_classes: [...(privacy.prohibited_classes ?? [])],
    });
  }
  if (privacy.decision === "needs_independent_privacy_route") {
    return resolutionResult({
      decision: "needs_independent_privacy_route", reason_id: privacy.reason_id, ...base,
      privacy_decision: privacy.decision,
      routed_classes: [...(privacy.routed_classes ?? [])],
      required_evidence: privacy.required_evidence,
    });
  }

  // Steps 5 and 6. Permitted, and permitted to write inbound.
  const permitted = entry.permitted_sources.find(s => s.source_system === source_system);
  if (permitted === undefined) {
    return resolutionResult({ decision: "refuse", reason_id: "source_not_permitted", ...base });
  }
  if (!WRITING_DIRECTIONS.includes(permitted.direction)) {
    return resolutionResult({
      decision: "refuse", reason_id: "forbidden_write_direction", ...base,
      declared_direction: permitted.direction,
    });
  }

  // Step 9, taken BEFORE the readback checks below. An observation from the
  // future is not fresher, it is unreadable, and its own time has to be sound
  // before a readback can be judged relative to it.
  if (observedAt > now) {
    return resolutionResult({ decision: "refuse", reason_id: "observation_after_now", ...base });
  }

  // Step 8. TWO OBLIGATIONS LIVE HERE AND THEY ARE KEPT APART ON PURPOSE.
  //
  //   POLICY   — a field whose entry requires a readback refuses without one.
  //              That obligation belongs to the entry and to nothing else.
  //   EVIDENCE — any readback that IS supplied must be confirmed, about THIS
  //              value, and read at a moment that could actually have happened,
  //              whether or not policy asked for one.
  //
  // Validating the second only when the first applies is what let an impossible
  // readback ride along with an accepted transition: the request supplied
  // evidence, the evidence contradicted the request, and the answer was accept.
  // A field checked only when it is required is a field a caller may attach
  // anything to the rest of the time, so the supplied readback is judged on its
  // own terms and the "policy requires one" refusal is retained separately.
  if (entry.readback_required && readback === null) {
    return resolutionResult({ decision: "refuse", reason_id: "missing_readback", ...base });
  }
  if (readback !== null) {
    if (readback.confirmed !== true) {
      return resolutionResult({ decision: "refuse", reason_id: "readback_not_confirmed", ...base });
    }
    if (readback.readback_value_digest !== value_digest) {
      return resolutionResult({ decision: "refuse", reason_id: "readback_value_mismatch", ...base });
    }
    // A readback proves the source still held the value AFTER it was observed
    // and BEFORE now. Dated earlier it proves nothing about this observation;
    // dated later it describes a reading nobody has taken yet, and either way it
    // is not a confirmation of anything.
    const readbackAt = assertInstant(readback.readback_at, "request.observation.readback.readback_at");
    if (readbackAt < observedAt) {
      return resolutionResult({
        decision: "refuse", reason_id: "readback_precedes_observation", ...base,
        observed_at: observation.observed_at, readback_at: readback.readback_at,
      });
    }
    if (readbackAt > now) {
      return resolutionResult({
        decision: "refuse", reason_id: "readback_after_now", ...base,
        readback_at: readback.readback_at,
      });
    }
  }

  const isOwner = source_system === entry.owner_source;

  // Step 10. Nothing established yet.
  if (request.current_state === undefined || request.current_state === null) {
    if (!isOwner) {
      return resolutionResult({
        decision: "refuse", reason_id: "non_owner_cannot_establish_field", ...base,
        reconciliation_item: deepFreeze(reconciliationItem(entry, observation, null,
          "non_owner_establishment_attempt")),
      });
    }
    return acceptedResolution({ entry, observation, current: null, base, registry, reason_id: "field_established" });
  }

  // Step 7 completes here, against the established record.
  const raw = assertObject(request.current_state, "request.current_state");
  assertClosedKeys(raw, CURRENT_STATE_KEYS, "request.current_state");
  assertRequiredKeys(raw,
    ["entity", "field", "tenant", "value_digest", "version", "owner_source", "observed_at", "event_seq"],
    "request.current_state");
  const current = {
    entity: assertExternalIdent(raw.entity, "request.current_state.entity", { maxLength: 128 }),
    field: assertExternalIdent(raw.field, "request.current_state.field", { maxLength: 128 }),
    tenant: raw.tenant,
    account: raw.account === undefined || raw.account === null
      ? null : assertExternalIdent(raw.account, "request.current_state.account", { maxLength: 128 }),
    native_identity: raw.native_identity === undefined || raw.native_identity === null
      ? null : assertNativeIdentity(raw.native_identity, "request.current_state.native_identity"),
    value_digest: assertDigestRef(raw.value_digest, "request.current_state.value_digest"),
    version: assertVersion(entry, raw.version, "request.current_state.version"),
    owner_source: assertExternalIdent(raw.owner_source, "request.current_state.owner_source",
      { maxLength: 128 }),
    observed_at: raw.observed_at,
    event_seq: assertSafeInteger(raw.event_seq, "request.current_state.event_seq", { min: 0 }),
    last_event_digest: raw.last_event_digest === undefined || raw.last_event_digest === null
      ? null : assertDigestRef(raw.last_event_digest, "request.current_state.last_event_digest"),
  };
  const currentObservedAt = assertInstant(current.observed_at, "request.current_state.observed_at");
  if (current.entity !== entity || current.field !== field) {
    fail("current_state_binding_mismatch",
      "request.current_state describes a different entity or field than the observation",
      { observed: `${entity}.${field}`, established: `${current.entity}.${current.field}` });
  }
  if (current.tenant !== ORGANIZATION_TENANT_ID) {
    return resolutionResult({ decision: "refuse", reason_id: "cross_tenant_refused", ...base });
  }

  // THE ESTABLISHED STATE IS EVIDENCE TOO, AND IT IS AUTHENTICATED BEFORE IT IS
  // COMPARED AGAINST. Everything below this comment reads the caller's picture of
  // the record; until it is bound to the registry and to an intact event chain,
  // an unbound or broken picture could produce an apparently accepted
  // transition, event and receipt, and could sever the chain those records
  // extend. Each of these refuses rather than reconciling, because a state whose
  // provenance cannot be trusted is not a party to a disagreement.

  // Bound to a moment that has happened. A snapshot observed after `now`
  // describes a record nobody could have read yet, so it is not the established
  // state a mutation may be compared against. Checked HERE — before the version
  // comparison and before any transition, event or receipt is built — because an
  // impossible picture of the record must not authorize a change to it or extend
  // the append-only chain. This is the established state's half of the rule the
  // observation already obeys.
  if (currentObservedAt > now) {
    return resolutionResult({
      decision: "refuse", reason_id: "current_state_observed_after_now", ...base,
      established_observed_at: current.observed_at,
    });
  }

  // Bound to the registry: the record can only have been established by the
  // source the registry says owns the field.
  if (current.owner_source !== entry.owner_source) {
    return resolutionResult({
      decision: "refuse", reason_id: "current_state_owner_not_registered_owner", ...base,
      established_owner_source: current.owner_source, registered_owner_source: entry.owner_source,
    });
  }
  // Bound to the identity the registry requires. Absence here is not "no
  // constraint", it is a state that should never have been written.
  if (entry.requires_account_identity && current.account === null) {
    return resolutionResult({
      decision: "refuse", reason_id: "current_state_missing_account_identity", ...base,
    });
  }
  if (entry.requires_native_identity && current.native_identity === null) {
    return resolutionResult({
      decision: "refuse", reason_id: "current_state_missing_native_identity", ...base,
    });
  }
  // The established record's native identity must belong to the source that
  // owns it; a Salesforce-owned field carrying an Outlook native id describes
  // two different records at once.
  if (current.native_identity !== null &&
      current.native_identity.source_system !== current.owner_source) {
    return resolutionResult({
      decision: "refuse", reason_id: "current_state_native_identity_source_mismatch", ...base,
      established_native_source: current.native_identity.source_system,
      established_owner_source: current.owner_source,
    });
  }
  // Bound to an intact append-only chain. An established record has had at least
  // one event, so a sequence of zero contradicts its own existence, and the
  // digest of that last event is the link the next one extends.
  if (current.event_seq < 1) {
    return resolutionResult({
      decision: "refuse", reason_id: "current_state_event_sequence_invalid", ...base,
      established_event_seq: current.event_seq,
    });
  }
  if (current.last_event_digest === null) {
    return resolutionResult({
      decision: "refuse", reason_id: "current_state_event_chain_broken", ...base,
      established_event_seq: current.event_seq,
    });
  }
  // A sequence that cannot be incremented exactly stops here rather than
  // silently landing on a float that is no longer a distinct ordinal.
  if (!Number.isSafeInteger(current.event_seq + 1)) {
    return resolutionResult({
      decision: "refuse", reason_id: "event_sequence_overflow", ...base,
      established_event_seq: current.event_seq,
      max_safe_event_seq: Number.MAX_SAFE_INTEGER,
    });
  }

  if (entry.requires_account_identity && current.account !== account) {
    return resolutionResult({
      decision: "refuse", reason_id: "cross_account_refused", ...base,
      established_account: current.account, observed_account: account,
    });
  }
  if (native_identity !== null && current.native_identity !== null) {
    if (native_identity.native_id === current.native_identity.native_id &&
        native_identity.native_id_epoch !== current.native_identity.native_id_epoch) {
      return resolutionResult({
        decision: "refuse", reason_id: "recycled_native_id_refused", ...base,
        native_id: native_identity.native_id,
        established_epoch: current.native_identity.native_id_epoch,
        observed_epoch: native_identity.native_id_epoch,
      });
    }
    if (native_identity.native_id !== current.native_identity.native_id) {
      return resolutionResult({ decision: "refuse", reason_id: "native_id_mismatch", ...base });
    }
  }

  // Step 11. Freshness.
  const ordering = compareVersions(entry, version, current.version);
  const sameValue = value_digest === current.value_digest;

  if (ordering === "older") {
    return resolutionResult({
      decision: "refuse", reason_id: "stale_observation_refused", ...base,
      version_ordering: ordering,
    });
  }
  if (ordering === "indeterminate") {
    if (sameValue) {
      return resolutionResult({
        decision: "no_change", reason_id: "opaque_version_confirms_established_value", ...base,
        version_ordering: ordering,
      });
    }
    return conflictResolution(entry, observation, current, base,
      "indeterminate_version_ordering", "opaque_version_ordering_unknowable", ordering);
  }
  if (ordering === "equal") {
    if (sameValue) {
      return resolutionResult({
        decision: "no_change", reason_id: "observation_confirms_established_value", ...base,
        version_ordering: ordering,
      });
    }
    return conflictResolution(entry, observation, current, base,
      "equal_version_contradiction", "equal_version_contradiction", ordering);
  }
  // ordering === "newer"
  if (!isOwner && !sameValue) {
    return conflictResolution(entry, observation, current, base,
      "forbidden_overwrite_by_non_owner", "forbidden_overwrite_refused", ordering);
  }
  if (!isOwner && sameValue) {
    // A permitted non-owner may CONFIRM the owner's value at a newer version. It
    // does not advance the record, because the record's version belongs to the
    // owner; saying otherwise would let a bystander move the freshness marker.
    return resolutionResult({
      decision: "no_change", reason_id: "non_owner_confirmation_only", ...base,
      version_ordering: ordering,
    });
  }
  return acceptedResolution({
    entry, observation, current, base, registry,
    reason_id: sameValue ? "owner_version_advanced" : "owner_value_updated",
    version_ordering: ordering,
  });
}

function conflictResolution(entry, observation, current, base, conflict_kind, reason_id, ordering) {
  const item = deepFreeze(reconciliationItem(entry, observation, current, conflict_kind));
  return resolutionResult({
    decision: entry.conflict_behavior === "refuse" ? "refuse" : "reconcile",
    reason_id, ...base,
    conflict_kind,
    conflict_behavior: entry.conflict_behavior,
    human_resolver_class: entry.human_resolver_class,
    version_ordering: ordering,
    reconciliation_item: item,
  });
}

/**
 * Build the three bound records of an accepted change.
 *
 * The receipt carries BOTH other digests; neither of the others carries the
 * receipt's. That asymmetry is what makes "none alone substitutes for another"
 * a property a test can check rather than a promise in a comment.
 */
function acceptedResolution({ entry, observation, current, base, registry, reason_id, version_ordering = null }) {
  const transition = deepFreeze({
    record_kind: "current_state_transition",
    schema_version: V5_F01_TRANSITION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    entity: entry.entity,
    field: entry.field,
    authoritative_home: entry.authoritative_home,
    owner_source: entry.owner_source,
    from_value_digest: current === null ? null : current.value_digest,
    to_value_digest: observation.value_digest,
    from_version: current === null ? null : current.version,
    to_version: observation.version,
    observed_at: observation.observed_at,
    alone_sufficient: false,
  });
  const transition_digest = digest(transition);

  const event = deepFreeze({
    record_kind: "append_only_event",
    schema_version: V5_F01_EVENT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    entity: entry.entity,
    field: entry.field,
    event_kind: current === null ? "source_field_established" : "source_field_observed",
    event_seq: (current === null ? 0 : current.event_seq) + 1,
    previous_event_digest: current === null ? null : current.last_event_digest,
    source_system: observation.source_system,
    account: observation.account,
    native_identity: observation.native_identity === null ? null : { ...observation.native_identity },
    version: observation.version,
    observed_at: observation.observed_at,
    provenance: { ...observation.provenance },
    taint_class: observation.taint_class,
    append_only: true,
    rewrites_prior_event: false,
    alone_sufficient: false,
  });
  const event_digest = digest(event);

  const receipt = deepFreeze({
    record_kind: "mutation_receipt",
    schema_version: V5_F01_RECEIPT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    entity: entry.entity,
    field: entry.field,
    reason_id,
    current_state_transition_digest: transition_digest,
    event_digest,
    registry_digest: registry.registry_digest,
    domain_policy_digest: v5F01PolicyDigest(),
    human_resolver_class: entry.human_resolver_class,
    // The handler derives the authenticated actor. This module refuses to accept
    // one and says so in the record rather than leaving the slot to be filled by
    // whoever calls next.
    actor: null,
    actor_derived_by: "authenticated_handler_context",
    alone_sufficient: false,
  });

  return resolutionResult({
    decision: "accept", reason_id, ...base,
    version_ordering,
    current_state_transition: transition,
    event,
    mutation_receipt: receipt,
    separation: deepFreeze({
      current_state_transition_digest: transition_digest,
      event_digest,
      mutation_receipt_digest: digest(receipt),
      receipt_binds_both: true,
      any_one_substitutes_for_another: false,
    }),
  });
}

// ---------------------------------------------------------------------------
// Q071 — immutable corporate artifacts and parsed proposals.
//
// SOURCE-AGNOSTIC ON PURPOSE. `source_system` is a caller-declared slug, so
// Salesforce, OneDrive and a corporate source nobody has named yet all use the
// same contract and no live adapter is needed to exercise it. What is closed is
// the EVIDENCE CLASS, and one group of those classes is closed shut: Tour rights
// receipts, Tour source evidence and Tour field assertions are the Tour domain's
// own machinery and can never certify a generic corporate fact. A Tour outcome
// certifying a Salesforce field is exactly the confusion this decision split.
//
// A PARSED PROPOSAL IS A PROPOSAL. It never becomes a fact, never advances a
// state and never carries effect authority — those three are asserted false on
// every result, including the allowed one, because "allowed" here means "worth a
// human's review", not "true". Its link to an artifact is evidence-scored,
// reversible and history-preserving: superseding a link names the link it
// replaces instead of erasing it.
// ---------------------------------------------------------------------------

export const V5_F01_ARTIFACT_SCHEMA_VERSION = "doctorcre-v5-f01-corporate-artifact.v1";
export const V5_F01_PROPOSAL_SCHEMA_VERSION = "doctorcre-v5-f01-parsed-proposal.v1";
export const V5_F01_PROPOSAL_LINK_SCHEMA_VERSION = "doctorcre-v5-f01-proposal-link.v1";

export const V5_F01_EVIDENCE_CLASSES = deepFreeze([
  "corporate_record_export", "corporate_field_snapshot", "corporate_document_bytes",
  "corporate_mailbox_item", "corporate_report_render",
]);

/** Tour machinery. Named so it can be refused by name rather than by omission. */
export const V5_F01_TOUR_ONLY_EVIDENCE_CLASSES = deepFreeze([
  "tour_rights_receipt", "tour_source_evidence", "tour_field_assertion",
  "tour_public_projection", "tour_route_version",
]);

const ARTIFACT_KEYS = Object.freeze([
  "source_system", "source_class", "source_account", "native_identity", "native_version",
  "content_digest", "byte_length", "observed_at", "provenance", "evidence_class",
  "declared_data_classes", "taint_class",
]);
const ARTIFACT_REQUIRED = Object.freeze([
  "source_system", "source_account", "native_identity", "native_version",
  "content_digest", "byte_length", "observed_at", "provenance", "evidence_class", "taint_class",
  // Mandatory: P09 binds sensitivity to every source, and an unclassified
  // artifact is one the privacy boundary was never asked about.
  "declared_data_classes",
]);

/**
 * Validate one artifact's complete identity and return a normalized copy.
 *
 * Shared by the artifact being admitted and by any prior artifact offered as
 * immutability evidence, so both are held to the same standard. A comparison is
 * only as trustworthy as the weaker of the two things being compared.
 */
function validateArtifactIdentity(raw, path, evidence_class) {
  const declared = raw.declared_data_classes;
  assertArray(declared, `${path}.declared_data_classes`, { min: 1, max: 16 });
  const seen = new Set();
  const declared_data_classes = declared.map((cls, i) => {
    const value = assertEnum(cls, V5_DATA_CLASSES, `${path}.declared_data_classes[${i}]`,
      "unknown_data_class");
    if (seen.has(value)) {
      fail("duplicate_data_class", `${path}.declared_data_classes repeats "${value}"`, { path });
    }
    seen.add(value);
    return value;
  }).sort();

  return {
    source_system: assertExternalIdent(raw.source_system, `${path}.source_system`, { maxLength: 128 }),
    source_class: raw.source_class === undefined || raw.source_class === null
      ? null
      : assertExternalIdent(raw.source_class, `${path}.source_class`, { maxLength: 128 }),
    source_account: assertExternalIdent(raw.source_account, `${path}.source_account`, { maxLength: 128 }),
    native_identity: assertNativeIdentity(raw.native_identity, `${path}.native_identity`),
    native_version: assertSafeText(raw.native_version, `${path}.native_version`, { maxLength: 128 }),
    content_digest: assertDigestRef(raw.content_digest, `${path}.content_digest`),
    byte_length: assertSafeInteger(raw.byte_length, `${path}.byte_length`, { min: 0 }),
    observed_at: raw.observed_at,
    provenance: assertProvenance(raw.provenance, `${path}.provenance`),
    evidence_class,
    declared_data_classes,
    taint_class: assertEnum(raw.taint_class, V5_F01_TAINT_CLASSES, `${path}.taint_class`,
      "unknown_taint_class"),
  };
}
const ADMIT_KEYS = Object.freeze(["tenant", "artifact", "now", "prior_artifact"]);

function artifactPreimage(artifact) {
  return {
    schema_version: V5_F01_ARTIFACT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    source_system: artifact.source_system,
    source_class: artifact.source_class,
    source_account: artifact.source_account,
    native_identity: { ...artifact.native_identity },
    native_version: artifact.native_version,
    content_digest: artifact.content_digest,
    byte_length: artifact.byte_length,
    observed_at: artifact.observed_at,
    provenance: { ...artifact.provenance },
    evidence_class: artifact.evidence_class,
    declared_data_classes: [...artifact.declared_data_classes],
    taint_class: artifact.taint_class,
  };
}

function artifactResult(fields) {
  return deepFreeze({
    ...fields,
    // An artifact is evidence. It is never itself a fact, and admitting one
    // makes nothing authoritative.
    is_fact: false,
    makes_field_authoritative: false,
    immutable: true,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Admit one immutable corporate artifact.
 *
 * ORDERED:
 *   1. Readable, closed, tenant-bound request.
 *   2. Complete identity: source system, account, native identity, native
 *      version, content digest, byte length, observed time, provenance.
 *   3. A Tour-only evidence class refuses by name.
 *   4. An unregistered evidence class throws.
 *   5. S01 decides privacy on the declared classes.
 *   6. An observed time after `now` refuses.
 *   7. A prior artifact under the same identity with different bytes refuses:
 *      immutable means the identity cannot be reused for other content.
 */
export function admitCorporateArtifact(request) {
  assertObject(request, "request");
  assertClosedKeys(request, ADMIT_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "artifact", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");

  const raw = assertObject(request.artifact, "request.artifact");
  assertClosedKeys(raw, ARTIFACT_KEYS, "request.artifact");

  const base = {
    tenant: ORGANIZATION_TENANT_ID,
    source_system: typeof raw.source_system === "string" ? raw.source_system : null,
    evidence_class: typeof raw.evidence_class === "string" ? raw.evidence_class : null,
    artifact_digest: null,
    artifact: null,
  };

  // Step 2, each absence named so the caller learns what identity is missing.
  for (const [key, reason] of [
    ["source_account", "missing_source_account"],
    ["native_identity", "missing_native_identity"],
    ["native_version", "missing_native_version"],
    ["observed_at", "missing_observed_time"],
    ["provenance", "missing_provenance"],
    ["declared_data_classes", "missing_sensitivity_classification"],
  ]) {
    if (raw[key] === undefined || raw[key] === null) {
      return artifactResult({ decision: "refuse", reason_id: reason, ...base });
    }
  }
  assertRequiredKeys(raw, ARTIFACT_REQUIRED, "request.artifact");

  // Step 3, before the registered-class check, so the refusal names the real
  // attempt instead of reporting an unknown value.
  if (typeof raw.evidence_class === "string" &&
      V5_F01_TOUR_ONLY_EVIDENCE_CLASSES.includes(raw.evidence_class)) {
    return artifactResult({
      decision: "refuse", reason_id: "tour_only_evidence_not_generic_authority", ...base,
      tour_only_evidence_classes: [...V5_F01_TOUR_ONLY_EVIDENCE_CLASSES],
    });
  }
  const evidence_class = assertEnum(raw.evidence_class, V5_F01_EVIDENCE_CLASSES,
    "request.artifact.evidence_class", "unknown_evidence_class");

  const artifact = validateArtifactIdentity(raw, "request.artifact", evidence_class);
  const observedAt = assertInstant(artifact.observed_at, "request.artifact.observed_at");
  if (artifact.native_identity.source_system !== artifact.source_system) {
    return artifactResult({ decision: "refuse", reason_id: "native_identity_source_mismatch", ...base });
  }

  // Step 5. S01 decides, and it decides on EVERY artifact. The classification is
  // mandatory (checked above) precisely so this can be unconditional: an
  // artifact admitted without declared classes would be bytes that never met the
  // privacy boundary at all, which is not the same as bytes that met it and
  // passed.
  const privacy = evaluatePrivacyBoundary({ data_classes: artifact.declared_data_classes });
  if (privacy.decision === "refuse") {
    return artifactResult({
      decision: "refuse", reason_id: privacy.reason_id, ...base,
      prohibited_classes: [...(privacy.prohibited_classes ?? [])],
    });
  }
  if (privacy.decision === "needs_independent_privacy_route") {
    return artifactResult({
      decision: "needs_independent_privacy_route", reason_id: privacy.reason_id, ...base,
      routed_classes: [...(privacy.routed_classes ?? [])],
      required_evidence: privacy.required_evidence,
    });
  }

  if (observedAt > now) {
    return artifactResult({ decision: "refuse", reason_id: "observation_after_now", ...base });
  }

  const preimage = artifactPreimage(artifact);
  const artifact_digest = digest(preimage);

  // Step 7. Immutability, checked against a prior artifact when the caller holds
  // one: the same source, account, native id, epoch and native version may never
  // describe two different sets of bytes.
  //
  // THE PRIOR IS VALIDATED TO THE SAME STANDARD BEFORE IT IS COMPARED. Reading
  // only the few fields the comparison happens to touch meant a malformed prior
  // — an invalid source slug, a non-string account, a content digest that is not
  // a digest — sailed through unread whenever its identity differed, so the one
  // piece of evidence that could have refused the admission was never checked.
  // A prior that cannot be read is a contract violation, not a silent pass.
  if (request.prior_artifact !== undefined && request.prior_artifact !== null) {
    const priorRaw = assertObject(request.prior_artifact, "request.prior_artifact");
    assertClosedKeys(priorRaw, ARTIFACT_KEYS, "request.prior_artifact");
    assertRequiredKeys(priorRaw, ARTIFACT_REQUIRED, "request.prior_artifact");
    const priorEvidenceClass = assertEnum(priorRaw.evidence_class, V5_F01_EVIDENCE_CLASSES,
      "request.prior_artifact.evidence_class", "unknown_evidence_class");
    const prior = snapshot(
      validateArtifactIdentity(priorRaw, "request.prior_artifact", priorEvidenceClass),
      "prior_artifact");
    assertInstant(prior.observed_at, "request.prior_artifact.observed_at");
    const sameIdentity =
      prior.source_system === artifact.source_system &&
      prior.source_account === artifact.source_account &&
      prior.native_identity.native_id === artifact.native_identity.native_id &&
      prior.native_identity.native_id_epoch === artifact.native_identity.native_id_epoch &&
      prior.native_version === artifact.native_version;
    if (sameIdentity && prior.content_digest !== artifact.content_digest) {
      return artifactResult({
        decision: "refuse", reason_id: "artifact_identity_conflict", ...base,
        artifact_digest,
        established_content_digest: prior.content_digest,
        observed_content_digest: artifact.content_digest,
      });
    }
  }

  return artifactResult({
    decision: "allow", reason_id: "artifact_admitted_as_evidence", ...base,
    artifact_digest,
    artifact: deepFreeze(preimage),
    round_trip: deepFreeze(preimage),
  });
}

// A proposal that reaches past "reviewable" is refused before its shape is even
// read, so the refusal names the attempt. These fragments are checked against the
// load-time self-check below, so none of them can collide with a legitimate key.
const PROPOSAL_EFFECT_FRAGMENTS = deepFreeze([
  "apply", "commit", "execute", "effect", "auto_accept", "accepted", "authoritative",
  "activate", "publish", "deploy", "send", "treat_as_fact", "is_fact", "advance",
]);

const PROPOSAL_KEYS = Object.freeze([
  "artifact_digest", "source_system", "source_account", "proposed_bindings",
  "confidence", "evidence_refs", "observed_at", "supersedes_link_digest",
]);
const PROPOSAL_REQUIRED = Object.freeze([
  "artifact_digest", "source_system", "source_account", "proposed_bindings",
  "confidence", "evidence_refs", "observed_at",
]);
const BINDING_KEYS = Object.freeze(["entity", "field", "value_digest", "version"]);
const PROPOSAL_REQUEST_KEYS = Object.freeze(["tenant", "registry", "proposal", "now"]);

function proposalResult(fields) {
  return deepFreeze({
    ...fields,
    // The three properties Q071 settles, asserted on EVERY result including the
    // allowed one. "Allowed" here means worth a human's review, never true.
    becomes_fact: false,
    advances_state: false,
    carries_effect_authority: false,
    requires_human_review: true,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Evaluate one parsed proposal against an admitted artifact.
 *
 * ORDERED:
 *   1. Any key that reaches past classification into effect refuses by name.
 *   2. Readable, closed, tenant-bound request; a compiled registry.
 *   3. Complete provenance: artifact digest, source system, account, evidence
 *      refs, observed time, confidence.
 *   4. Every proposed binding must name a field the registry knows; an unknown
 *      one refuses rather than creating a binding by assertion.
 *   5. The proposal is returned as a reviewable, evidence-scored, reversible
 *      link. It changes nothing.
 */
export function evaluateParsedProposal(request) {
  assertObject(request, "request");
  assertClosedKeys(request, PROPOSAL_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "registry", "proposal", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const registry = requireCompiledRegistry(request.registry, "request.registry");

  const raw = assertObject(request.proposal, "request.proposal");
  assertNoAccessorsOrHiddenKeys(raw, "request.proposal");
  const base = {
    tenant: ORGANIZATION_TENANT_ID,
    artifact_digest: typeof raw.artifact_digest === "string" ? raw.artifact_digest : null,
    proposal_digest: null,
    link: null,
  };

  // Step 1, before the closed-key check for the same reason S01 checks widening
  // first: the caller learns what it actually attempted.
  for (const key of Object.keys(raw)) {
    if (PROPOSAL_KEYS.includes(key)) continue;
    const normalized = key.toLowerCase();
    if (PROPOSAL_EFFECT_FRAGMENTS.some(fragment => normalized.includes(fragment))) {
      return proposalResult({
        decision: "refuse", reason_id: "effect_bearing_proposal_refused", ...base,
        offending_field: key,
      });
    }
  }
  assertClosedKeys(raw, PROPOSAL_KEYS, "request.proposal");
  assertRequiredKeys(raw, PROPOSAL_REQUIRED, "request.proposal");

  const artifact_digest = assertDigestRef(raw.artifact_digest, "request.proposal.artifact_digest");
  const source_system = assertExternalIdent(raw.source_system, "request.proposal.source_system",
    { maxLength: 128 });
  const source_account = assertExternalIdent(raw.source_account, "request.proposal.source_account",
    { maxLength: 128 });
  if (!Number.isFinite(raw.confidence) || raw.confidence < 0 || raw.confidence > 1) {
    fail("invalid_shape", "request.proposal.confidence must be a number between 0 and 1",
      { path: "request.proposal.confidence" });
  }
  assertArray(raw.evidence_refs, "request.proposal.evidence_refs", { min: 1, max: 32 });
  // A proposal is EVIDENCE-SCORED, so the count has to mean something. Listing
  // one receipt twice makes a single piece of evidence look like two, and it
  // also makes two equivalent evidence sets hash differently, which breaks the
  // link digest as an identity. Duplicates refuse rather than being folded away
  // silently, so the caller learns its evidence set was not what it thought.
  const evidenceSeen = new Set();
  const evidence_refs = raw.evidence_refs.map((ref, i) => {
    const value = assertExternalIdent(ref, `request.proposal.evidence_refs[${i}]`, { maxLength: 255 });
    if (evidenceSeen.has(value)) {
      fail("duplicate_evidence_ref",
        `request.proposal.evidence_refs repeats "${value}"; one reference is one piece of evidence`,
        { path: `request.proposal.evidence_refs[${i}]`, evidence_ref: value });
    }
    evidenceSeen.add(value);
    return value;
  }).sort();
  const observedAt = assertInstant(raw.observed_at, "request.proposal.observed_at");
  if (observedAt > now) {
    return proposalResult({ decision: "refuse", reason_id: "observation_after_now", ...base });
  }
  const supersedes_link_digest = raw.supersedes_link_digest === undefined ||
    raw.supersedes_link_digest === null
    ? null
    : assertDigestRef(raw.supersedes_link_digest, "request.proposal.supersedes_link_digest");

  assertArray(raw.proposed_bindings, "request.proposal.proposed_bindings", { min: 1, max: 64 });
  const bindings = [];
  const bindingSeen = new Set();
  for (let i = 0; i < raw.proposed_bindings.length; i += 1) {
    const path = `request.proposal.proposed_bindings[${i}]`;
    const item = assertObject(raw.proposed_bindings[i], path);
    assertClosedKeys(item, BINDING_KEYS, path);
    assertRequiredKeys(item, BINDING_KEYS, path);
    const entity = assertExternalIdent(item.entity, `${path}.entity`, { maxLength: 128 });
    const field = assertExternalIdent(item.field, `${path}.field`, { maxLength: 128 });
    const key = foldKey(entity, field);
    if (bindingSeen.has(key)) {
      fail("duplicate_proposed_binding", `${path} repeats ${entity}.${field}`, { path });
    }
    bindingSeen.add(key);
    const entry = registry.entries.find(e => e.entity === entity && e.field === field);
    if (entry === undefined) {
      return proposalResult({
        decision: "refuse", reason_id: "unknown_field_binding", ...base, entity, field,
      });
    }
    bindings.push({
      entity, field,
      value_digest: assertDigestRef(item.value_digest, `${path}.value_digest`),
      version: assertVersion(entry, item.version, `${path}.version`),
      human_resolver_class: entry.human_resolver_class,
    });
  }

  const link = {
    schema_version: V5_F01_PROPOSAL_LINK_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    artifact_digest,
    source_system,
    source_account,
    proposed_bindings: bindings,
    confidence: raw.confidence,
    evidence_refs,
    observed_at: raw.observed_at,
    // Reversible and history-preserving: superseding names the link it replaces
    // rather than deleting it, so the earlier reading stays readable.
    supersedes_link_digest,
    reversible: true,
    history_preserved: true,
    registry_digest: registry.registry_digest,
  };
  const proposal_digest = digest({ schema_version: V5_F01_PROPOSAL_SCHEMA_VERSION, ...link });

  return proposalResult({
    decision: "allow", reason_id: "proposal_reviewable_only", ...base,
    proposal_digest,
    link: deepFreeze(link),
    round_trip: deepFreeze({ ...link }),
  });
}

// ---------------------------------------------------------------------------
// Q125 / Q135 — document identity, state and home.
//
// FIVE INDEPENDENT STATE AXES, because collapsing them is how a document ends up
// "sent" and nobody can say whether it was signed. Preparation, delivery,
// signature, validity and version each move on their own.
//
// THREE IDENTITIES, one per home. Neon always holds identity, hashes and state.
// Object storage holds working and sealed bytes. OneDrive holds the official
// executed company copy — and that is the one that cannot be inferred. A
// document that is fully executed with no filed OneDrive copy is
// `incomplete_official_filing`, and the result says out loud that neither the
// object-storage success nor the Neon success implies otherwise. That inference
// is precisely the failure Q125 forbids, so it is refused rather than reported
// as a warning beside a success.
// ---------------------------------------------------------------------------

export const V5_F01_DOCUMENT_SCHEMA_VERSION = "doctorcre-v5-f01-document-identity.v1";

export const V5_F01_PREPARATION_STATES = deepFreeze([
  "not_started", "drafting", "ready_for_review", "approved_for_delivery",
]);
export const V5_F01_DELIVERY_STATES = deepFreeze(["undelivered", "delivered", "delivery_failed"]);
export const V5_F01_SIGNATURE_STATES = deepFreeze([
  "unsigned", "partially_signed", "fully_executed", "signature_declined",
]);
export const V5_F01_VALIDITY_STATES = deepFreeze([
  "draft", "effective", "expired", "superseded", "void",
]);
export const V5_F01_VERSION_STATES = deepFreeze(["current", "superseded", "withdrawn"]);
export const V5_F01_FILING_STATES = deepFreeze(["filed", "pending", "failed"]);

/**
 * Structural coherence, not business policy.
 *
 * Each constraint says a state cannot be true unless an earlier axis reached a
 * state that must physically precede it. A document cannot be delivered before
 * it was approved for delivery, signed before it was delivered, or effective
 * before it was executed. Business rules about WHICH documents need WHICH states
 * belong to the future registry, not here.
 */
const DOCUMENT_COHERENCE = deepFreeze([
  {
    constraint: "delivery_requires_approved_preparation",
    when: doc => ["delivered", "delivery_failed"].includes(doc.delivery_state),
    satisfied: doc => doc.preparation_state === "approved_for_delivery",
  },
  {
    constraint: "signature_requires_delivery",
    when: doc => ["partially_signed", "fully_executed", "signature_declined"].includes(doc.signature_state),
    satisfied: doc => doc.delivery_state === "delivered",
  },
  {
    constraint: "effective_requires_full_execution",
    when: doc => doc.validity_state === "effective",
    satisfied: doc => doc.signature_state === "fully_executed",
  },
  {
    constraint: "draft_cannot_be_fully_executed",
    when: doc => doc.validity_state === "draft",
    satisfied: doc => ["unsigned", "partially_signed"].includes(doc.signature_state),
  },
]);

const NEON_IDENTITY_KEYS = Object.freeze(["document_id", "content_digest", "version_no"]);
const OBJECT_STORAGE_IDENTITY_KEYS = Object.freeze(["object_key", "content_digest", "byte_length", "sealed"]);
const ONEDRIVE_IDENTITY_KEYS = Object.freeze(["drive_id", "item_id", "content_digest", "filing_state"]);
const DOCUMENT_KEYS = Object.freeze([
  "document_class", "neon_identity", "object_storage_identity", "onedrive_identity",
  "preparation_state", "delivery_state", "signature_state", "validity_state", "version_state",
]);
const DOCUMENT_REQUIRED = Object.freeze([
  "document_class", "neon_identity",
  "preparation_state", "delivery_state", "signature_state", "validity_state", "version_state",
]);
const DOCUMENT_REQUEST_KEYS = Object.freeze(["tenant", "document"]);

function documentResult(fields) {
  return deepFreeze({
    ...fields,
    // Stated on every result, allowed or refused, so no reader can mistake a
    // successful byte write for an official filing.
    object_storage_success_implies_official_filing: false,
    neon_success_implies_official_filing: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Validate and project one document's identities and states.
 *
 * ORDERED:
 *   1. Readable, closed, tenant-bound request.
 *   2. The Neon identity is mandatory: Neon holds identity, hashes and state, so
 *      a document without one has no record-layer existence to describe.
 *   3. All five state axes must carry a registered value.
 *   4. Structural coherence, each constraint named.
 *   5. A sealed object-storage copy and a filed OneDrive copy must agree with the
 *      Neon content digest; a mismatch refuses rather than picking a winner.
 *   6. Full execution demands a FILED OneDrive official copy. Absent, pending or
 *      failed each yield incomplete_official_filing.
 *   7. Otherwise allow, echoing every identity and state field for round-trip.
 */
export function projectDocumentIdentity(request) {
  assertObject(request, "request");
  assertClosedKeys(request, DOCUMENT_REQUEST_KEYS, "request");
  assertRequiredKeys(request, DOCUMENT_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");

  const raw = assertObject(request.document, "request.document");
  assertClosedKeys(raw, DOCUMENT_KEYS, "request.document");
  assertRequiredKeys(raw, DOCUMENT_REQUIRED, "request.document");

  const document_class = assertExternalIdent(raw.document_class, "request.document.document_class",
    { maxLength: 128 });

  const neonRaw = assertObject(raw.neon_identity, "request.document.neon_identity");
  assertClosedKeys(neonRaw, NEON_IDENTITY_KEYS, "request.document.neon_identity");
  assertRequiredKeys(neonRaw, NEON_IDENTITY_KEYS, "request.document.neon_identity");
  const neon_identity = {
    document_id: assertExternalIdent(neonRaw.document_id, "request.document.neon_identity.document_id",
      { maxLength: 128 }),
    content_digest: assertDigestRef(neonRaw.content_digest,
      "request.document.neon_identity.content_digest"),
    version_no: assertSafeInteger(neonRaw.version_no, "request.document.neon_identity.version_no",
      { min: 1 }),
  };

  let object_storage_identity = null;
  if (raw.object_storage_identity !== undefined && raw.object_storage_identity !== null) {
    const osRaw = assertObject(raw.object_storage_identity, "request.document.object_storage_identity");
    assertClosedKeys(osRaw, OBJECT_STORAGE_IDENTITY_KEYS, "request.document.object_storage_identity");
    assertRequiredKeys(osRaw, OBJECT_STORAGE_IDENTITY_KEYS, "request.document.object_storage_identity");
    object_storage_identity = {
      object_key: assertExternalIdent(osRaw.object_key,
        "request.document.object_storage_identity.object_key", { maxLength: 255 }),
      content_digest: assertDigestRef(osRaw.content_digest,
        "request.document.object_storage_identity.content_digest"),
      byte_length: assertSafeInteger(osRaw.byte_length,
        "request.document.object_storage_identity.byte_length", { min: 0 }),
      sealed: assertBoolean(osRaw.sealed, "request.document.object_storage_identity.sealed"),
    };
  }

  let onedrive_identity = null;
  if (raw.onedrive_identity !== undefined && raw.onedrive_identity !== null) {
    const odRaw = assertObject(raw.onedrive_identity, "request.document.onedrive_identity");
    assertClosedKeys(odRaw, ONEDRIVE_IDENTITY_KEYS, "request.document.onedrive_identity");
    assertRequiredKeys(odRaw, ONEDRIVE_IDENTITY_KEYS, "request.document.onedrive_identity");
    onedrive_identity = {
      drive_id: assertExternalIdent(odRaw.drive_id, "request.document.onedrive_identity.drive_id",
        { maxLength: 128 }),
      item_id: assertExternalIdent(odRaw.item_id, "request.document.onedrive_identity.item_id",
        { maxLength: 255 }),
      content_digest: assertDigestRef(odRaw.content_digest,
        "request.document.onedrive_identity.content_digest"),
      filing_state: assertEnum(odRaw.filing_state, V5_F01_FILING_STATES,
        "request.document.onedrive_identity.filing_state", "unknown_filing_state"),
    };
  }

  const states = {
    preparation_state: assertEnum(raw.preparation_state, V5_F01_PREPARATION_STATES,
      "request.document.preparation_state", "unknown_preparation_state"),
    delivery_state: assertEnum(raw.delivery_state, V5_F01_DELIVERY_STATES,
      "request.document.delivery_state", "unknown_delivery_state"),
    signature_state: assertEnum(raw.signature_state, V5_F01_SIGNATURE_STATES,
      "request.document.signature_state", "unknown_signature_state"),
    validity_state: assertEnum(raw.validity_state, V5_F01_VALIDITY_STATES,
      "request.document.validity_state", "unknown_validity_state"),
    version_state: assertEnum(raw.version_state, V5_F01_VERSION_STATES,
      "request.document.version_state", "unknown_version_state"),
  };

  const echo = {
    document_class,
    neon_identity,
    object_storage_identity,
    onedrive_identity,
    ...states,
  };
  const base = {
    tenant: ORGANIZATION_TENANT_ID,
    document_class,
    neon_identity: deepFreeze({ ...neon_identity }),
    object_storage_identity: object_storage_identity === null
      ? null : deepFreeze({ ...object_storage_identity }),
    onedrive_identity: onedrive_identity === null ? null : deepFreeze({ ...onedrive_identity }),
    ...states,
    homes: deepFreeze({
      identity_and_state: "neon_record_layer",
      working_and_sealed_bytes: "object_storage",
      official_executed_copy: "onedrive",
    }),
    readback: deepFreeze(snapshot(echo, "document")),
    document_digest: digest({ schema_version: V5_F01_DOCUMENT_SCHEMA_VERSION,
      tenant: ORGANIZATION_TENANT_ID, ...echo }),
  };

  // Step 4.
  for (const rule of DOCUMENT_COHERENCE) {
    if (rule.when(states) && !rule.satisfied(states)) {
      return documentResult({
        decision: "refuse", reason_id: "document_state_incoherent", ...base,
        violated_constraint: rule.constraint,
        official_filing_state: "not_evaluated",
      });
    }
  }

  // Step 5. Two homes claiming different bytes for one document is a conflict a
  // machine must not settle by preferring whichever it read last.
  if (object_storage_identity !== null && object_storage_identity.sealed === true &&
      object_storage_identity.content_digest !== neon_identity.content_digest) {
    return documentResult({
      decision: "refuse", reason_id: "sealed_bytes_digest_mismatch", ...base,
      official_filing_state: "not_evaluated",
    });
  }
  if (onedrive_identity !== null && onedrive_identity.filing_state === "filed" &&
      onedrive_identity.content_digest !== neon_identity.content_digest) {
    return documentResult({
      decision: "refuse", reason_id: "official_copy_digest_mismatch", ...base,
      official_filing_state: "incomplete_official_filing",
    });
  }

  // Step 6. The one inference Q125 forbids.
  const officialCopyRequired = states.signature_state === "fully_executed";
  const officialCopyFiled = onedrive_identity !== null && onedrive_identity.filing_state === "filed";
  if (officialCopyRequired && !officialCopyFiled) {
    return documentResult({
      decision: "refuse", reason_id: "incomplete_official_filing", ...base,
      official_filing_state: "incomplete_official_filing",
      official_copy_required: true,
      official_copy_filing_state: onedrive_identity === null ? "absent" : onedrive_identity.filing_state,
    });
  }

  return documentResult({
    decision: "allow", reason_id: "document_identity_and_states_coherent", ...base,
    official_filing_state: officialCopyFiled ? "filed" : "not_required",
    official_copy_required: officialCopyRequired,
    official_copy_filing_state: onedrive_identity === null ? "absent" : onedrive_identity.filing_state,
  });
}

// ---------------------------------------------------------------------------
// Q129 — retention, holds, deletion proof and surviving derivatives.
//
// The registry is per ARTIFACT CLASS and, like the field registry, it is caller
// policy: no retention period, constraint or derivative is invented here. What
// this module owns is the refusal matrix, and it is deliberately fail-closed in
// both directions a purge can go wrong — a hold nobody can read blocks exactly
// as hard as an active one, and a derivative nobody registered blocks the
// deletion rather than quietly disappearing with it.
//
// THERE IS NO SILENT PURGE SUCCESS. `silent_purge: false` rides on every result,
// and no path returns allow without either a matching deletion proof or a policy
// that positively declares none is required.
// ---------------------------------------------------------------------------

export const V5_F01_HOLD_STATES = deepFreeze(["active", "released", "expired", "unknown"]);
export const V5_F01_DELETION_RECEIPT_SCHEMA_VERSION = "doctorcre-v5-f01-deletion-receipt.v1";

// ---------------------------------------------------------------------------
// WHEN A RETENTION PERIOD STARTS, and it is not the source's clock.
//
// THE RULE, in the words it was accepted in. A default retention period starts at
// the SERVER-STAMPED INSTANT THE RECORD LAYER TOOK CUSTODY of the artifact — the
// `recorded_at` of the stored row. A source's `observed_at` stays what it has
// always been: identity and provenance, the moment the source says it saw the
// value. It is never the retention clock under another name, because it is the one
// timestamp a corporate source (or anything that can write to one) chooses, and a
// backdated one would shorten a retention period nobody agreed to shorten.
//
// A CLASS MAY NAME AN EXPLICIT CLOCK, and only one shape of it: a TYPED EVENT WITH
// ITS OWN PROVENANCE — a lease terminating, a matter closing — identified by an
// event kind the class registers. What it may never name is the artifact's own
// `observed_at` wearing an event's name; every clock carries
// `source_observed_at_used: false` in the bytes the evaluation and the receipt
// hash, so an implementation that quietly aliased the two would move the digest
// rather than pass unnoticed.
//
// THE TRIGGER ACTUALLY USED IS BOUND, not merely chosen. The kind, the instant it
// started, the reference it came from, its provenance and — for an event — the
// event's own digest travel into the deletion evaluation and into the deletion
// receipt beside the retention registry digest, and the persistence tail's SQL
// writer re-derives the whole clock under the lock it already takes and refuses a
// forged or stale one.
//
// THERE IS NO TRUSTED EVENT LOOKUP IN THIS SLICE, and this contract does not
// invent one. Nothing here produces, approves or authenticates a retention-clock
// event, and a caller-supplied one is not evidence about anything. So a class that
// registers an explicit event clock CANNOT reach a deletion today: an absent,
// unknown, mismatched or unverified trigger refuses, by name, every time. The
// default custody clock works, which is the honest division — the thing that can
// be established is usable, and the thing that cannot is refused rather than
// approximated.
//
// AN EXISTING REGISTRY THAT NAMES NO CLOCK DEFAULTS TO CUSTODY, and that default
// is applied when the class is READ rather than by rewriting anything. A stored
// registry keeps its exact bytes and its exact digest — the preimage below emits
// `retention_clock` only for a class that names the explicit event clock — so no
// history is rewritten and no custody is fabricated for a row that never claimed
// one. What a legacy class gets is the same answer this contract gives to a class
// that states the default outright, which is the compatibility rule, not a repair.
// ---------------------------------------------------------------------------

export const V5_F01_RETENTION_CLOCK_KINDS = deepFreeze([
  "server_recorded_custody",
  "explicit_retention_clock_event",
]);

export const V5_F01_DEFAULT_RETENTION_CLOCK_KIND = "server_recorded_custody";
export const V5_F01_EXPLICIT_RETENTION_CLOCK_KIND = "explicit_retention_clock_event";

// ---------------------------------------------------------------------------
// The bounded derivative-registration rule.
//
// WHOSE RULE THIS IS, stated exactly, because the distinction is load-bearing
// and an earlier revision of this header blurred it. Q129.D1 settles the
// RETENTION REGISTRY: per artifact class, a home, a period, governing
// constraints, whether a deletion proof is required, and which derivative kinds
// survive. It says nothing about registering provenance for derived records.
//
// The registration rule below is NOT Q129.D1 and carries no canonical decision
// id, because none has been issued for it. It is a session approval, and the
// only honest citation is the session itself:
//
//   native task 01a0869f-fe0d-7493-bda3-ab8b3c0d6683
//   user turn   01a08779-6b68-7013-bab1-369cf616254f
//
// Nothing here should be read as a new settled decision, and nothing downstream
// should treat those two identifiers as one. They are a provenance reference for
// an approved addition, not an acceptance and not an entry in the nine.
//
// THE APPROVED RULE, in the words it was approved in: every workflow that
// creates a derived record registers a link to the original artifact BEFORE the
// derivative is considered complete; only trusted producer workflows write those
// links; and deletion is blocked whenever registration coverage is unknown.
// Existing and externally created derivatives stay coverage-unknown until
// somebody establishes otherwise, so an empty link table never means "safe to
// delete". The rule fills a missing retention behaviour; it authorizes deleting
// nothing.
//
// A LINK IS PROVENANCE, NOT AN INVENTORY, and the distinction is the whole of
// the fail-closed story. Registering "this abstract came from that lease" says
// something true about ONE derivative. It says nothing whatever about whether
// some other derivative exists that nobody registered — from a workflow written
// before this rule, from a provider, from a person with a copy. So every link
// this module emits carries `is_exhaustive_inventory: false` and
// `establishes_coverage: false` in its own hashed bytes, and the deletion
// evaluator refuses on coverage BEFORE it ever looks at an inventory.
//
// COVERAGE IS AN INPUT, NEVER A CALLER CLAIM. evaluateDeletion takes a
// `derivative_coverage` object that the persistence tail derives from the
// database, and refuses unless its state is exactly "established". This module
// ships no way to reach that state and invents none: the two facts that would
// establish it — a closed set of producer workflows that may derive from an
// artifact class, and a registered completion from every one of them for this
// artifact — have no ingress in this slice. Until they do, the honest answer is
// "unknown", and the honest consequence is that deletion refuses.
//
// CLASS POLICY IS NOT INSTANCE OBSERVATION. `surviving_derivatives` on a
// retention class is Q129 policy about WHICH KINDS survive a deletion of that
// class. It is not an assertion that every instance of the class has one of
// each. See the accounting note in evaluateDeletion step 8.
//
// THE ONE RESIDUAL ASSUMPTION, named rather than left for a reader to find. A
// registration is an ATTESTATION THAT A DERIVATIVE EXISTS, not a verified fact
// that it does. Nothing in this module or in the record layer resolves
// `derivative_id`, and nothing checks bytes against `derivative_content_digest`;
// the source artifact is loaded, the derivative is taken on the producer's word.
// Under unknown coverage that is safe in the only direction it can move — an
// attested derivative blocks a deletion, never permits one — and no path here
// can reach `established`. It would stop being safe the moment coverage could be
// established, because attested-only kinds would then travel into a deletion
// receipt as `observed_surviving_derivatives`: a receipt naming survivors that
// may never have existed. Hence the standing precondition, which is a
// requirement on future work and not a caveat about it: DO NOT BUILD AN INGRESS
// THAT ESTABLISHES COVERAGE until producer outputs are independently bound and
// verified by something other than the caller registering them. No proof of that
// exists today and none is implied anywhere in this file.
// ---------------------------------------------------------------------------

export const V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION =
  "doctorcre-v5-f01-derivative-source-link.v1";

/**
 * The two honest answers about whether the registered links for one artifact are
 * the whole set. There is deliberately no third value: "probably", "assumed" and
 * "none found" are all "unknown" wearing a more confident hat.
 */
export const V5_F01_DERIVATIVE_COVERAGE_STATES = deepFreeze(["unknown", "established"]);

/**
 * The derivative kind this module's own parsed-proposal path produces.
 *
 * Named as a constant rather than accepted from a caller, because the producer
 * that registers it is a workflow inside this contract and the kind it produces
 * is part of that workflow's identity, not a label the caller may choose.
 */
export const V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND = "f01_parsed_proposal";

/**
 * The derivative kinds an in-contract producer owns, which the PUBLIC
 * registration surface must refuse.
 *
 * A LIST, so the next internal producer kind is covered by adding an element
 * rather than by remembering to repeat a comparison somewhere.
 *
 * WHY REFUSING THESE MATTERS, and it is not tidiness. A parsed proposal's
 * derivative identity IS its proposal digest, and that digest is computed from
 * caller payload plus the installed registry digest — so a caller can predict it.
 * The stored identity index is unique per (tenant, kind, id) over an append-only
 * table with no release path, so pre-registering the predicted identity against
 * some other artifact would make the genuine proposal write conflict for ever,
 * and would leave a provenance edge asserting that proposal came from an artifact
 * it did not.
 *
 * THE SHARED EVALUATOR STILL ACCEPTS THEM, on purpose. evaluateDerivativeRegistration
 * is what the in-contract proposal producer itself calls, so refusing there would
 * break the very path that legitimately writes this kind. The refusal belongs at
 * the public caller surface, which is the only place the distinction exists.
 *
 * "f01_document_version" IS THE SECOND MEMBER, and it is here for the same reason
 * rather than by analogy. A document version's derivative identity is the
 * (document_id, version_no) fold, which a caller can predict even more easily than
 * a proposal digest; the identity index in ops is unique per (tenant, kind, id)
 * over an append-only table with no release path. A pre-registration of
 * ("f01_document_version", "<some document>:1") against an unrelated artifact
 * would make the genuine record-document-identity write conflict for that version
 * for ever, and would leave a provenance edge asserting the document came from an
 * artifact it did not. The kind is written only by the document producer inside
 * this contract, in the same transaction that completes the version; the mirror of
 * this list in ops.f01_reserved_derivative_kinds() carries both names too.
 */
export const V5_F01_RESERVED_DERIVATIVE_KINDS = deepFreeze([
  V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
  "f01_document_version",
]);

const DERIVATIVE_IDENTITY_KEYS = Object.freeze([
  "derivative_kind", "derivative_id", "content_digest",
]);
const DERIVATIVE_PRODUCER_KEYS = Object.freeze(["producer_workflow", "producer_run_ref"]);
const DERIVATIVE_EVIDENCE_KEYS = Object.freeze(["evidence_ref", "evidence_digest"]);
const DERIVATIVE_REGISTRATION_KEYS = Object.freeze([
  "source_artifact_digest", "derivative", "producer", "produced_at", "evidence",
]);
// THE LOADED SOURCE ARTIFACT, and `content_digest` is the field the self-source
// comparison actually needs. `artifact_digest` is the digest of the artifact
// RECORD — its identity in the record layer, over source system, account, native
// identity, provenance and the rest — while `content_digest` is the digest of the
// BYTES that artifact describes. Comparing a derivative's content digest with the
// artifact's record digest answers a question nobody asked: two things that are
// never equal for any honest derivative AND never equal for a byte-identical copy
// either, which is the copy the rule exists to refuse.
//
// IT IS OPTIONAL RATHER THAN REQUIRED, and the reason is a boundary rather than a
// convenience. The document seam calls this evaluator with the source artifact the
// persistence tail loaded, and that call site is outside this phase's write cap;
// requiring the field here would refuse every document binding until that module
// changed. So an absent content digest means the comparison COULD NOT BE MADE, and
// the answer says so in `source_content_compared` instead of implying it passed.
// The persistence tail supplies it on every path it owns, and the private SQL
// inserter re-derives it from the authenticated stored artifact regardless, so the
// uncompared case is a reported gap rather than a way through.
const DERIVATIVE_SOURCE_ARTIFACT_KEYS = Object.freeze([
  "artifact_digest", "created_at", "content_digest",
]);
const DERIVATIVE_SOURCE_ARTIFACT_REQUIRED = Object.freeze(["artifact_digest", "created_at"]);
const DERIVATIVE_REQUEST_KEYS = Object.freeze([
  "tenant", "registration", "source_artifact", "now",
]);
const DERIVATIVE_COVERAGE_KEYS = Object.freeze([
  "state", "reason_id", "registered_derivative_kinds",
]);

const RETENTION_POLICY_KEYS = Object.freeze(["schema_version", "registry_version", "tenant", "classes"]);
const RETENTION_CLASS_KEYS = Object.freeze([
  "artifact_class", "authoritative_home", "default_retention_days", "governing_constraints",
  "deletion_proof_required", "surviving_derivatives", "retention_clock",
]);
// `retention_clock` is OPTIONAL and every other key is required. Absent means the
// default custody clock — see the section header — so a registry written before
// this contract named a clock compiles unchanged and means exactly what it always
// meant.
const RETENTION_CLASS_REQUIRED = Object.freeze([
  "artifact_class", "authoritative_home", "default_retention_days", "governing_constraints",
  "deletion_proof_required", "surviving_derivatives",
]);
const RETENTION_CLOCK_POLICY_KEYS = Object.freeze(["kind", "event_kind"]);
// The clock the persistence tail LOADS for one artifact: which trigger, when it
// started, where that instant came from, whose provenance it carries, and whether
// anything actually verified it.
const RETENTION_CLOCK_KEYS = Object.freeze([
  "kind", "event_kind", "started_at", "reference", "provenance", "event_digest",
  "verified", "source_observed_at_used",
]);
const RETENTION_CLOCK_REQUIRED = Object.freeze([
  "kind", "started_at", "reference", "provenance", "verified", "source_observed_at_used",
]);

/**
 * Validate one class's declared retention clock, or return the default.
 *
 * A class that names the explicit event clock MUST name the event kind, and a
 * class that takes the default MUST NOT: an unread event kind is an unenforced
 * one, and a clock that names an event nobody consults is policy that looks
 * stricter than it is.
 */
function compileRetentionClock(raw, path) {
  if (raw === undefined || raw === null) {
    return { kind: V5_F01_DEFAULT_RETENTION_CLOCK_KIND, event_kind: null };
  }
  assertObject(raw, path);
  assertClosedKeys(raw, RETENTION_CLOCK_POLICY_KEYS, path);
  assertRequiredKeys(raw, ["kind"], path);
  const kind = assertEnum(raw.kind, V5_F01_RETENTION_CLOCK_KINDS, `${path}.kind`,
    "unknown_retention_clock_kind");
  if (kind === V5_F01_EXPLICIT_RETENTION_CLOCK_KIND) {
    if (raw.event_kind === undefined || raw.event_kind === null) {
      fail("missing_retention_clock_event_kind",
        `${path}.event_kind must name the typed event this class's retention starts from; an explicit clock with no event kind names nothing`,
        { path });
    }
    return {
      kind,
      event_kind: assertExternalIdent(raw.event_kind, `${path}.event_kind`, { maxLength: 128 }),
    };
  }
  if (raw.event_kind !== undefined && raw.event_kind !== null) {
    fail("unused_retention_clock_event_kind",
      `${path}.event_kind is only read for the "${V5_F01_EXPLICIT_RETENTION_CLOCK_KIND}" clock; unused policy is ambiguous policy`,
      { path, kind });
  }
  return { kind, event_kind: null };
}

/**
 * The clock one COMPILED-OR-STORED class runs on.
 *
 * A stored registry compiled before this contract carries no `retention_clock` at
 * all, and it gets the default rather than a refusal — that is the compatibility
 * rule, stated in the section header and applied here at READ time so the stored
 * bytes and their digest stay exactly as they were. A class that DOES carry one is
 * re-validated rather than trusted, so an edited stored registry cannot smuggle an
 * unregistered clock kind into an evaluation.
 */
function retentionClockOfClass(entry, path) {
  return compileRetentionClock(entry.retention_clock ?? null, path);
}
const HOLD_KEYS = Object.freeze(["hold_id", "state", "reason", "placed_at", "released_at"]);
const DELETION_PROOF_KEYS = Object.freeze(["proof_ref", "artifact_digest", "proof_digest", "executed_at"]);
const DELETION_SUBJECT_KEYS = Object.freeze([
  // `created_at` IS THE SOURCE'S OBSERVED INSTANT and stays exactly that: the
  // artifact's identity and provenance, and the lifetime a deletion proof has to
  // sit inside. `retention_clock` is the separate, LOADED answer to a different
  // question — when the retention period started — and the two are never each
  // other. See the retention-clock section header.
  "artifact_class", "artifact_home", "artifact_digest", "created_at", "retention_clock",
  "holds", "deletion_proof", "derivative_coverage", "derivatives", "satisfied_constraints",
]);
const DELETION_REQUEST_KEYS = Object.freeze(["tenant", "registry", "subject", "now"]);

function registrationResult(fields) {
  return deepFreeze({
    ...fields,
    // Stated in every answer, positive or negative: registering a link records
    // where a derivative came from and does nothing else. It does not make the
    // link set complete, does not make an absence verified, and permits no
    // deletion of anything.
    establishes_coverage: false,
    is_exhaustive_inventory: false,
    permits_deletion: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Validate one trusted producer's registration of one derived record against the
 * original artifact it was derived from.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. The request must be readable, closed and carry the one server-held tenant.
 *   2. Every identity is checked, not merely typed: the source artifact digest,
 *      the derivative's kind, id and content digest, the producing workflow and
 *      its run reference, and the immutable evidence the production left behind.
 *   3. THE SOURCE ARTIFACT IS LOADED, never asserted. A caller that names a
 *      digest cannot bring an artifact into existence by naming it, so an absent
 *      one refuses and a loaded one that is not the artifact named refuses.
 *   4. A derivative whose bytes ARE the source's bytes is not a derivative; it is
 *      the artifact under a second name, and registering it would make the source
 *      look like its own provenance. THE COMPARISON IS AGAINST THE LOADED
 *      ARTIFACT'S CONTENT DIGEST — the bytes — and separately against its record
 *      digest, because those are two different claims and only the first one is
 *      the "same bytes under a second name" this step is named for.
 *   5. Time must describe something that could have happened: a production after
 *      `now` has not happened, and one before the source artifact existed is
 *      about something else.
 *   6. Only then allow, returning the exact link preimage the record layer
 *      stores — which says in its own hashed bytes that it is provenance, that it
 *      is not an inventory, and that it establishes no coverage.
 *
 * WHAT THIS FUNCTION CANNOT DO, and does not pretend to. It cannot decide that a
 * producer is trusted: trust is the authenticated principal the persistence tail
 * derives from its own transaction context, and no field here confers it.
 */
export function evaluateDerivativeRegistration(request) {
  assertObject(request, "request");
  assertClosedKeys(request, DERIVATIVE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "registration", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");

  const raw = assertObject(request.registration, "request.registration");
  assertClosedKeys(raw, DERIVATIVE_REGISTRATION_KEYS, "request.registration");
  assertRequiredKeys(raw, DERIVATIVE_REGISTRATION_KEYS, "request.registration");

  const source_artifact_digest = assertDigestRef(raw.source_artifact_digest,
    "request.registration.source_artifact_digest");

  const identity = assertObject(raw.derivative, "request.registration.derivative");
  assertClosedKeys(identity, DERIVATIVE_IDENTITY_KEYS, "request.registration.derivative");
  assertRequiredKeys(identity, DERIVATIVE_IDENTITY_KEYS, "request.registration.derivative");
  const derivative_kind = assertExternalIdent(identity.derivative_kind,
    "request.registration.derivative.derivative_kind", { maxLength: 128 });
  const derivative_id = assertExternalIdent(identity.derivative_id,
    "request.registration.derivative.derivative_id", { maxLength: 255 });
  const derivative_content_digest = assertDigestRef(identity.content_digest,
    "request.registration.derivative.content_digest");

  const producer = assertObject(raw.producer, "request.registration.producer");
  assertClosedKeys(producer, DERIVATIVE_PRODUCER_KEYS, "request.registration.producer");
  assertRequiredKeys(producer, DERIVATIVE_PRODUCER_KEYS, "request.registration.producer");
  const producer_workflow = assertExternalIdent(producer.producer_workflow,
    "request.registration.producer.producer_workflow", { maxLength: 128 });
  const producer_run_ref = assertExternalIdent(producer.producer_run_ref,
    "request.registration.producer.producer_run_ref", { maxLength: 255 });

  const evidence = assertObject(raw.evidence, "request.registration.evidence");
  assertClosedKeys(evidence, DERIVATIVE_EVIDENCE_KEYS, "request.registration.evidence");
  assertRequiredKeys(evidence, DERIVATIVE_EVIDENCE_KEYS, "request.registration.evidence");
  const evidence_ref = assertExternalIdent(evidence.evidence_ref,
    "request.registration.evidence.evidence_ref", { maxLength: 255 });
  const evidence_digest = assertDigestRef(evidence.evidence_digest,
    "request.registration.evidence.evidence_digest");

  const producedAt = assertInstant(raw.produced_at, "request.registration.produced_at");

  const base = {
    tenant: ORGANIZATION_TENANT_ID,
    source_artifact_digest,
    derivative_kind,
    derivative_id,
    derivative_content_digest,
    producer_workflow,
    // Filled in once the artifact is LOADED, and reported on every answer from
    // that point on. `source_content_compared: false` is the honest statement
    // that this evaluator could not weigh the derivative's bytes against the
    // source's; it is never the statement that they differ.
    source_content_digest: null,
    source_content_compared: false,
    derivative_link: null,
  };

  // Step 3.
  if (request.source_artifact === undefined || request.source_artifact === null) {
    return registrationResult({
      decision: "refuse", reason_id: "unknown_source_artifact", ...base,
    });
  }
  const stored = assertObject(request.source_artifact, "request.source_artifact");
  assertClosedKeys(stored, DERIVATIVE_SOURCE_ARTIFACT_KEYS, "request.source_artifact");
  assertRequiredKeys(stored, DERIVATIVE_SOURCE_ARTIFACT_REQUIRED, "request.source_artifact");
  const storedDigest = assertDigestRef(stored.artifact_digest, "request.source_artifact.artifact_digest");
  const createdAt = assertInstant(stored.created_at, "request.source_artifact.created_at");
  const source_content_digest = stored.content_digest === undefined || stored.content_digest === null
    ? null
    : assertDigestRef(stored.content_digest, "request.source_artifact.content_digest");
  base.source_content_digest = source_content_digest;
  base.source_content_compared = source_content_digest !== null;
  if (storedDigest !== source_artifact_digest) {
    return registrationResult({
      decision: "refuse", reason_id: "source_artifact_mismatch", ...base,
      loaded_artifact_digest: storedDigest,
    });
  }

  // Step 4. TWO COMPARISONS, AND THEY ANSWER TWO DIFFERENT QUESTIONS.
  //
  //   THE BYTES. A derivative whose content digest is the LOADED ARTIFACT'S
  //   content digest is a byte-identical copy of the source. That is the case the
  //   rule is about — the same bytes wearing a derivative's name — and it is the
  //   comparison an earlier revision did not make: it weighed the derivative's
  //   content digest against the artifact's RECORD digest, which is never equal
  //   for a copy, so a copy registered cleanly as its own provenance.
  //
  //   THE RECORD. A derivative whose content digest is the artifact's record
  //   digest claims its bytes are the stored artifact ROW's canonical bytes. That
  //   is a different and equally impossible claim, so it keeps its refusal rather
  //   than being dropped when the real comparison arrived. `self_source_comparison`
  //   says which of the two fired, so one refusal is never read as the other.
  if (source_content_digest !== null && derivative_content_digest === source_content_digest) {
    return registrationResult({
      decision: "refuse", reason_id: "derivative_is_its_own_source", ...base,
      self_source_comparison: "source_content_digest",
    });
  }
  if (derivative_content_digest === source_artifact_digest) {
    return registrationResult({
      decision: "refuse", reason_id: "derivative_is_its_own_source", ...base,
      self_source_comparison: "source_artifact_record_digest",
    });
  }

  // Step 5.
  if (producedAt > now) {
    return registrationResult({
      decision: "refuse", reason_id: "production_after_now", ...base,
      produced_at: raw.produced_at,
    });
  }
  if (producedAt < createdAt) {
    return registrationResult({
      decision: "refuse", reason_id: "production_precedes_source_artifact", ...base,
      produced_at: raw.produced_at, source_created_at: stored.created_at,
    });
  }

  return registrationResult({
    decision: "allow", reason_id: "derivative_source_link_registered", ...base,
    derivative_link: deepFreeze({
      schema_version: V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION,
      tenant: ORGANIZATION_TENANT_ID,
      source_artifact_digest,
      derivative_kind,
      derivative_id,
      derivative_content_digest,
      producer_workflow,
      producer_run_ref,
      produced_at: raw.produced_at,
      evidence_ref,
      evidence_digest,
      // The four claims the row makes about itself, hashed with it, so a stored
      // link can never be read back as something stronger than it is.
      registration_is_provenance: true,
      is_exhaustive_inventory: false,
      establishes_coverage: false,
      permits_deletion: false,
    }),
  });
}

/**
 * Read one derivative-coverage answer, which the caller LOADED rather than
 * decided. An absent one is not "established"; it is a question nobody asked.
 */
function assertDerivativeCoverage(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, DERIVATIVE_COVERAGE_KEYS, path);
  assertRequiredKeys(value, ["state"], path);
  const state = assertEnum(value.state, V5_F01_DERIVATIVE_COVERAGE_STATES,
    `${path}.state`, "unknown_derivative_coverage_state");
  const reason_id = value.reason_id === undefined || value.reason_id === null
    ? null
    : assertExternalIdent(value.reason_id, `${path}.reason_id`, { maxLength: 128 });
  let registered_derivative_kinds = null;
  if (value.registered_derivative_kinds !== undefined &&
      value.registered_derivative_kinds !== null) {
    assertArray(value.registered_derivative_kinds, `${path}.registered_derivative_kinds`,
      { min: 0, max: 256 });
    const seen = new Set();
    registered_derivative_kinds = value.registered_derivative_kinds.map((item, i) => {
      const kind = assertExternalIdent(item, `${path}.registered_derivative_kinds[${i}]`,
        { maxLength: 128 });
      if (seen.has(kind)) {
        fail("duplicate_registered_derivative_kind",
          `${path}.registered_derivative_kinds repeats "${kind}"`, { path, derivative_kind: kind });
      }
      seen.add(kind);
      return kind;
    }).sort();
  }
  return { state, reason_id, registered_derivative_kinds };
}

/**
 * The exact bytes a compiled retention registry hashes to.
 *
 * `retention_clock` IS EMITTED ONLY FOR A CLASS THAT NAMES THE EXPLICIT EVENT
 * CLOCK, and that conditional is the compatibility rule rather than an oversight.
 * A registry installed before this contract existed hashes to bytes that carry no
 * clock at all; emitting the resolved default for it would change those bytes,
 * and the stored registry would stop hashing to the digest the database recorded —
 * a refusal to READ an already-installed policy, which is rewriting history by
 * another route. A class that takes the default and a class that omits the field
 * mean the same thing, so they canonicalize to the same bytes; a class that names
 * the explicit clock means something else, and says so in the digest.
 */
/**
 * Read one LOADED retention clock, which the caller DERIVED from the stored row
 * rather than decided.
 *
 * A caller cannot select this any more than it can select coverage: the
 * persistence tail reads the server-stamped custody instant off the artifact row
 * it already holds, and the SQL writer re-derives the whole answer before the
 * evaluation is stored. What this function does is refuse to READ a clock that is
 * not one of the registered shapes, so an unregistered kind, an unreadable instant
 * or a missing verification flag is a contract violation rather than a value some
 * later comparison happens to fall through.
 */
function assertLoadedRetentionClock(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, RETENTION_CLOCK_KEYS, path);
  assertRequiredKeys(value, RETENTION_CLOCK_REQUIRED, path);
  return {
    kind: assertEnum(value.kind, V5_F01_RETENTION_CLOCK_KINDS, `${path}.kind`,
      "unknown_retention_clock_kind"),
    event_kind: value.event_kind === undefined || value.event_kind === null
      ? null
      : assertExternalIdent(value.event_kind, `${path}.event_kind`, { maxLength: 128 }),
    started_at: value.started_at,
    reference: assertExternalIdent(value.reference, `${path}.reference`, { maxLength: 255 }),
    provenance: assertExternalIdent(value.provenance, `${path}.provenance`, { maxLength: 128 }),
    event_digest: value.event_digest === undefined || value.event_digest === null
      ? null
      : assertDigestRef(value.event_digest, `${path}.event_digest`),
    verified: assertBoolean(value.verified, `${path}.verified`),
    // The anti-alias flag, hashed with every evaluation and receipt that carries
    // this clock. A trigger that admits it is the source's own observed instant
    // under another name is refused rather than run.
    source_observed_at_used: assertBoolean(value.source_observed_at_used,
      `${path}.source_observed_at_used`),
  };
}

export function retentionRegistryPreimage(compiled) {
  return {
    schema_version: V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
    registry_version: compiled.registry_version,
    tenant: compiled.tenant,
    classes: compiled.classes.map(entry => {
      const clock = entry.retention_clock ?? null;
      const explicit = clock !== null && clock.kind === V5_F01_EXPLICIT_RETENTION_CLOCK_KIND;
      return {
        artifact_class: entry.artifact_class,
        authoritative_home: entry.authoritative_home,
        default_retention_days: entry.default_retention_days,
        governing_constraints: [...entry.governing_constraints],
        deletion_proof_required: entry.deletion_proof_required,
        surviving_derivatives: [...entry.surviving_derivatives],
        ...(explicit
          ? { retention_clock: { kind: clock.kind, event_kind: clock.event_kind } }
          : {}),
      };
    }),
  };
}

/** Validate one typed retention policy and return a frozen, digest-bound copy. */
export function compileRetentionRegistry(policy) {
  assertObject(policy, "policy");
  assertClosedKeys(policy, RETENTION_POLICY_KEYS, "policy");
  assertRequiredKeys(policy, RETENTION_POLICY_KEYS, "policy");
  if (policy.schema_version !== V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION) {
    fail("unknown_schema_version",
      `policy.schema_version must be "${V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION}"`,
      { expected: V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION });
  }
  assertTenant(policy.tenant, "policy.tenant");
  assertSafeInteger(policy.registry_version, "policy.registry_version", { min: 1 });
  assertArray(policy.classes, "policy.classes", { min: 1, max: 256 });

  const seen = new Map();
  const classes = policy.classes.map((raw, index) => {
    const path = `policy.classes[${index}]`;
    assertObject(raw, path);
    assertClosedKeys(raw, RETENTION_CLASS_KEYS, path);
    assertRequiredKeys(raw, RETENTION_CLASS_REQUIRED, path);
    const artifact_class = assertExternalIdent(raw.artifact_class, `${path}.artifact_class`,
      { maxLength: 128 });
    if (seen.has(artifact_class)) {
      fail("duplicate_retention_class",
        `${path} repeats "${artifact_class}"; a class with two policies has none`,
        { path, artifact_class, first_index: seen.get(artifact_class) });
    }
    seen.set(artifact_class, index);
    const dedupe = (values, key) => {
      assertArray(values, `${path}.${key}`, { min: 0, max: 64 });
      const set = new Set();
      return values.map((value, i) => {
        const item = assertExternalIdent(value, `${path}.${key}[${i}]`, { maxLength: 128 });
        if (set.has(item)) fail(`duplicate_${key}`, `${path}.${key} repeats "${item}"`, { path });
        set.add(item);
        return item;
      }).sort();
    };
    return {
      artifact_class,
      authoritative_home: assertEnum(raw.authoritative_home, V5_F01_HOMES,
        `${path}.authoritative_home`, "unknown_home"),
      default_retention_days: assertSafeInteger(raw.default_retention_days,
        `${path}.default_retention_days`, { min: 0, max: 36600 }),
      governing_constraints: dedupe(raw.governing_constraints, "governing_constraints"),
      deletion_proof_required: assertBoolean(raw.deletion_proof_required,
        `${path}.deletion_proof_required`),
      surviving_derivatives: dedupe(raw.surviving_derivatives, "surviving_derivatives"),
      // Resolved on the compiled entry either way, so an evaluation never has to
      // ask what an absent field meant. The PREIMAGE above is the half that stays
      // byte-compatible; this is the half that makes the answer explicit.
      retention_clock: compileRetentionClock(raw.retention_clock ?? null,
        `${path}.retention_clock`),
    };
  });
  classes.sort((a, b) => a.artifact_class < b.artifact_class ? -1 : 1);

  const compiled = {
    compiled: true,
    schema_version: V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
    registry_version: policy.registry_version,
    tenant: ORGANIZATION_TENANT_ID,
    classes,
  };
  return snapshot({ ...compiled, registry_digest: digest(retentionRegistryPreimage(compiled)) },
    "retention_registry");
}

const COMPILED_RETENTION_KEYS = Object.freeze([
  "compiled", "schema_version", "registry_version", "tenant", "classes", "registry_digest",
]);

function requireCompiledRetentionRegistry(registry, path) {
  assertObject(registry, path);
  assertClosedKeys(registry, COMPILED_RETENTION_KEYS, path);
  assertRequiredKeys(registry, COMPILED_RETENTION_KEYS, path);
  if (registry.compiled !== true || registry.schema_version !== V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION) {
    fail("registry_not_compiled", `${path} must be the output of compileRetentionRegistry`, { path });
  }
  assertDigestRef(registry.registry_digest, `${path}.registry_digest`);
  const recomputed = digest(retentionRegistryPreimage(registry));
  if (recomputed !== registry.registry_digest) {
    fail("registry_digest_mismatch",
      `${path} no longer hashes to its own digest; it was edited after compilation`,
      { path, expected: registry.registry_digest, actual: recomputed });
  }
  return registry;
}

function deletionResult(fields) {
  return deepFreeze({
    ...fields,
    silent_purge: false,
    purge_without_proof: false,
    effects: V5_NO_EFFECTS,
  });
}

const MS_PER_DAY = 86400000;

/**
 * Decide whether one artifact may be deleted.
 *
 * ORDERED, and every step is a refusal a caller can act on:
 *   1. Readable, closed, tenant-bound request; a compiled retention registry.
 *   2. The artifact class must be registered.
 *   3. Its declared home must be the class's authoritative home.
 *   4. Holds: an ACTIVE hold blocks — that is the definite, nameable block. An
 *      UNKNOWN hold state blocks too, because a hold nobody can read is not a
 *      hold nobody placed. Released and expired holds do not block.
 *   5. THE RETENTION CLOCK. The LOADED trigger must be present, must be the kind
 *      this class registers, must not be the source's observed instant under
 *      another name, must be verified, and must name an instant that has
 *      happened. Absent, unknown, mismatched or unverified each refuse by name.
 *   6. The default retention period must have elapsed SINCE THAT TRIGGER — the
 *      server-stamped custody instant by default, never the source's own.
 *   7. Every governing constraint the class names must be satisfied.
 *   8. A deletion proof the class REQUIRES must be present, and ANY supplied
 *      proof — required or not — must be about this exact artifact and executed
 *      between the artifact's creation and now.
 *   9. REGISTRATION COVERAGE must be established. Unknown coverage blocks, and
 *      it blocks before any inventory is read, because an inventory drawn from
 *      an incomplete registry is not an inventory.
 *  10. Every observed derivative must be a registered surviving derivative; an
 *      unregistered one blocks rather than vanishing with the artifact.
 *  11. Only then allow, with a deletion receipt preimage naming BOTH what the
 *      class policy says survives and what was actually observed to exist — and
 *      the exact trigger the retention period was measured from.
 */
export function evaluateDeletion(request) {
  assertObject(request, "request");
  assertClosedKeys(request, DELETION_REQUEST_KEYS, "request");
  assertRequiredKeys(request, DELETION_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const registry = requireCompiledRetentionRegistry(request.registry, "request.registry");

  const raw = assertObject(request.subject, "request.subject");
  assertClosedKeys(raw, DELETION_SUBJECT_KEYS, "request.subject");
  assertRequiredKeys(raw,
    ["artifact_class", "artifact_home", "artifact_digest", "created_at"], "request.subject");

  const artifact_class = assertExternalIdent(raw.artifact_class, "request.subject.artifact_class",
    { maxLength: 128 });
  const artifact_home = assertEnum(raw.artifact_home, V5_F01_HOMES, "request.subject.artifact_home",
    "unknown_home");
  const artifact_digest = assertDigestRef(raw.artifact_digest, "request.subject.artifact_digest");
  const createdAt = assertInstant(raw.created_at, "request.subject.created_at");

  const entry = registry.classes.find(c => c.artifact_class === artifact_class);
  const base = {
    tenant: ORGANIZATION_TENANT_ID,
    artifact_class, artifact_home, artifact_digest,
    retention_registry_digest: registry.registry_digest,
    authoritative_home: entry ? entry.authoritative_home : null,
    // CLASS POLICY. Which derivative KINDS this class's policy says survive a
    // deletion. Reported on every answer because it is what the registry says,
    // and kept separate from the instance observation below because they are
    // different facts and conflating them is how a policy allowance turns into a
    // claim that something exists.
    surviving_derivatives: entry ? [...entry.surviving_derivatives] : [],
    // INSTANCE OBSERVATION, in two fields that answer two different questions.
    //
    //   observed_derivatives            what was actually observed for THIS
    //                                   artifact, whatever it turned out to be.
    //   observed_surviving_derivatives  the same list, named as survivors, and
    //                                   filled ONLY where every observed kind is
    //                                   one the class policy says survives.
    //
    // Both start null, because "nothing was reported" and "nothing was found" are
    // different answers and only one of them may ever reach a receipt. A refusal
    // for an unregistered kind fills the first and leaves the second null: the
    // kind that caused the refusal is by definition not a survivor, so a list
    // containing it must not be called one.
    observed_derivatives: null,
    observed_surviving_derivatives: null,
    derivative_coverage_state: "unknown",
    // THE TRIGGER THE PERIOD IS MEASURED FROM, reported on every answer once it
    // has been read, and null before it has. `registered_retention_clock_kind` is
    // what the CLASS says the clock must be; `retention_clock` is the trigger that
    // was actually loaded. Keeping them apart is what lets a refusal say whether
    // the policy or the trigger was the problem.
    registered_retention_clock_kind: entry
      ? retentionClockOfClass(entry, "registry.retention_clock").kind : null,
    retention_clock: null,
    deletion_receipt: null,
  };
  if (entry === undefined) {
    return deletionResult({ decision: "refuse", reason_id: "unknown_artifact_class", ...base });
  }
  if (artifact_home !== entry.authoritative_home) {
    return deletionResult({ decision: "refuse", reason_id: "artifact_home_mismatch", ...base });
  }

  // Step 4.
  //
  // AN ABSENT INVENTORY IS NOT AN EMPTY ONE. "No holds were supplied" and "we
  // looked and there are no holds" are different facts, and only the second one
  // can authorize a deletion. An omitted list is therefore refused rather than
  // read as a clean bill of health; an explicitly empty list is accepted and is
  // how a caller states the verified-empty case.
  if (raw.holds === undefined || raw.holds === null) {
    return deletionResult({ decision: "refuse", reason_id: "holds_inventory_missing", ...base });
  }
  const holds = [];
  assertArray(raw.holds, "request.subject.holds", { min: 0, max: 64 });
  const holdSeen = new Set();
  raw.holds.forEach((item, i) => {
    const path = `request.subject.holds[${i}]`;
    assertObject(item, path);
    assertClosedKeys(item, HOLD_KEYS, path);
    assertRequiredKeys(item, ["hold_id", "state", "placed_at"], path);
    const hold_id = assertExternalIdent(item.hold_id, `${path}.hold_id`, { maxLength: 128 });
    if (holdSeen.has(hold_id)) {
      fail("duplicate_hold", `${path} repeats hold "${hold_id}"`, { path, hold_id });
    }
    holdSeen.add(hold_id);
    const placedAt = assertInstant(item.placed_at, `${path}.placed_at`);
    const state = assertEnum(item.state, V5_F01_HOLD_STATES, `${path}.state`, "unknown_hold_state_value");
    const releasedAt = item.released_at === undefined || item.released_at === null
      ? null : assertInstant(item.released_at, `${path}.released_at`);
    holds.push({ hold_id, state, placed_at: placedAt, released_at: releasedAt, path });
  });

  // Hold timestamps have to describe something that could have happened. A hold
  // placed in the future has not been placed; a RELEASED hold with no release
  // time, or released before it was placed, or released after now, is a hold
  // whose release nobody can point to — and the release is the entire reason it
  // has stopped blocking. Active and unknown holds block regardless, so they are
  // checked below rather than here.
  for (const hold of holds) {
    if (hold.placed_at > now) {
      return deletionResult({
        decision: "refuse", reason_id: "hold_placed_after_now", ...base, blocking_holds: [hold.hold_id],
      });
    }
    if (hold.state === "released") {
      if (hold.released_at === null) {
        return deletionResult({
          decision: "refuse", reason_id: "released_hold_missing_release_time", ...base,
          blocking_holds: [hold.hold_id],
        });
      }
      if (hold.released_at < hold.placed_at || hold.released_at > now) {
        return deletionResult({
          decision: "refuse", reason_id: "hold_release_time_incoherent", ...base,
          blocking_holds: [hold.hold_id],
        });
      }
    }
    // An expired hold need not name a release moment, but if it names one the
    // same ordering applies.
    if (hold.state === "expired" && hold.released_at !== null &&
        (hold.released_at < hold.placed_at || hold.released_at > now)) {
      return deletionResult({
        decision: "refuse", reason_id: "hold_release_time_incoherent", ...base,
        blocking_holds: [hold.hold_id],
      });
    }
  }
  const active = holds.filter(h => h.state === "active").map(h => h.hold_id);
  if (active.length > 0) {
    return deletionResult({
      decision: "refuse", reason_id: "active_hold_blocks_deletion", ...base, blocking_holds: active,
    });
  }
  const unreadable = holds.filter(h => h.state === "unknown").map(h => h.hold_id);
  if (unreadable.length > 0) {
    return deletionResult({
      decision: "refuse", reason_id: "unknown_hold_state_blocks_deletion", ...base,
      blocking_holds: unreadable,
    });
  }

  // Step 5. THE RETENTION CLOCK, AND WHY IT IS A SEPARATE INPUT.
  //
  // `created_at` above is the SOURCE's observed instant. Measuring a retention
  // period from it means a source that reports an old observation — or anything
  // able to write one — shortens the period, and an artifact backdated far enough
  // arrives already expired. So the trigger is LOADED separately: by default the
  // server-stamped instant the record layer took custody of the row, which no
  // caller and no source can choose.
  //
  // EVERY FAILURE HERE IS A REFUSAL, and each one is named. A trigger nobody
  // supplied, a trigger of the wrong kind for this class, a class whose explicit
  // event has no established producer, an event of the wrong kind, an unverified
  // one, one that claims to be the source's observed instant under another name,
  // and one dated after `now` — none of them may be rounded down to "start the
  // clock somewhere sensible".
  const classClock = retentionClockOfClass(entry, "registry.retention_clock");
  if (raw.retention_clock === undefined || raw.retention_clock === null) {
    return deletionResult({ decision: "refuse", reason_id: "retention_clock_missing", ...base });
  }
  const clock = assertLoadedRetentionClock(raw.retention_clock, "request.subject.retention_clock");
  const clockStartedAt = assertInstant(clock.started_at, "request.subject.retention_clock.started_at");
  base.retention_clock = deepFreeze({ ...clock });
  if (clock.source_observed_at_used === true) {
    return deletionResult({
      decision: "refuse", reason_id: "retention_clock_uses_source_observed_at", ...base,
    });
  }
  if (clock.kind !== classClock.kind) {
    return deletionResult({
      decision: "refuse",
      // A class that registered an explicit event clock and was handed the
      // default custody one has not been given a WRONG trigger; it has been given
      // the only trigger this slice can produce, which is not the one its policy
      // names. Saying so by name is the difference between "your input is
      // malformed" and "nothing here can establish that event yet".
      reason_id: classClock.kind === V5_F01_EXPLICIT_RETENTION_CLOCK_KIND
        ? "retention_clock_event_not_established"
        : "retention_clock_kind_mismatch",
      ...base,
      loaded_retention_clock_kind: clock.kind,
    });
  }
  if (classClock.kind === V5_F01_EXPLICIT_RETENTION_CLOCK_KIND) {
    if (clock.event_kind !== classClock.event_kind) {
      return deletionResult({
        decision: "refuse", reason_id: "retention_clock_event_kind_mismatch", ...base,
        registered_retention_clock_event_kind: classClock.event_kind,
        loaded_retention_clock_event_kind: clock.event_kind,
      });
    }
    // The event has to be bindable, not merely named: without its own digest
    // there is nothing for the evaluation and the receipt to hash it by.
    if (clock.event_digest === null) {
      return deletionResult({
        decision: "refuse", reason_id: "retention_clock_event_unbound", ...base,
      });
    }
  } else if (clock.event_kind !== null) {
    return deletionResult({
      decision: "refuse", reason_id: "retention_clock_kind_mismatch", ...base,
      loaded_retention_clock_event_kind: clock.event_kind,
    });
  }
  // A caller-supplied trigger is not a verified one, and this module has no way to
  // make it one. Whatever loaded the clock has to say it authenticated it.
  if (clock.verified !== true) {
    return deletionResult({
      decision: "refuse", reason_id: "retention_clock_unverified", ...base,
    });
  }
  if (clockStartedAt > now) {
    return deletionResult({
      decision: "refuse", reason_id: "retention_clock_after_now", ...base,
    });
  }

  // Step 6. The period runs from the TRIGGER, never from the source's own clock.
  const elapsedDays = Math.floor((now - clockStartedAt) / MS_PER_DAY);
  if (elapsedDays < entry.default_retention_days) {
    return deletionResult({
      decision: "refuse", reason_id: "retention_period_not_elapsed", ...base,
      default_retention_days: entry.default_retention_days, elapsed_days: elapsedDays,
    });
  }

  // Step 7.
  let satisfied = [];
  if (raw.satisfied_constraints !== undefined && raw.satisfied_constraints !== null) {
    assertArray(raw.satisfied_constraints, "request.subject.satisfied_constraints", { min: 0, max: 64 });
    satisfied = raw.satisfied_constraints.map((value, i) =>
      assertExternalIdent(value, `request.subject.satisfied_constraints[${i}]`, { maxLength: 128 }));
  }
  const unsatisfied = entry.governing_constraints.filter(c => !satisfied.includes(c));
  if (unsatisfied.length > 0) {
    return deletionResult({
      decision: "refuse", reason_id: "governing_constraint_unsatisfied", ...base,
      unsatisfied_constraints: unsatisfied,
      governing_constraints: [...entry.governing_constraints],
    });
  }

  // Step 8.
  let deletion_proof = null;
  if (raw.deletion_proof !== undefined && raw.deletion_proof !== null) {
    const proof = assertObject(raw.deletion_proof, "request.subject.deletion_proof");
    assertClosedKeys(proof, DELETION_PROOF_KEYS, "request.subject.deletion_proof");
    assertRequiredKeys(proof, DELETION_PROOF_KEYS, "request.subject.deletion_proof");
    deletion_proof = {
      proof_ref: assertExternalIdent(proof.proof_ref, "request.subject.deletion_proof.proof_ref",
        { maxLength: 255 }),
      artifact_digest: assertDigestRef(proof.artifact_digest,
        "request.subject.deletion_proof.artifact_digest"),
      proof_digest: assertDigestRef(proof.proof_digest, "request.subject.deletion_proof.proof_digest"),
      executed_at: proof.executed_at,
    };
    assertInstant(deletion_proof.executed_at, "request.subject.deletion_proof.executed_at");
  }

  // TWO OBLIGATIONS, KEPT APART exactly as the readback's are:
  //
  //   POLICY   — a class whose entry requires a deletion proof refuses without
  //              one. That obligation belongs to the entry.
  //   EVIDENCE — any proof that IS supplied must be about THIS artifact and must
  //              sit inside the artifact's own lifetime, whether or not policy
  //              asked for one.
  //
  // The second half is not decoration: the allow receipt below copies the
  // supplied proof_ref and proof_digest verbatim, so an unvalidated optional
  // proof would turn caller input into a durable claim that a purge of THIS
  // artifact was proved — for a different artifact, or for work nobody has done
  // yet. Executed before the artifact existed, a proof is about something else;
  // executed after now, it describes a deletion nobody has performed.
  if (entry.deletion_proof_required && deletion_proof === null) {
    return deletionResult({ decision: "refuse", reason_id: "missing_deletion_proof", ...base });
  }
  if (deletion_proof !== null) {
    if (deletion_proof.artifact_digest !== artifact_digest) {
      return deletionResult({
        decision: "refuse", reason_id: "deletion_proof_mismatch", ...base,
        proof_artifact_digest: deletion_proof.artifact_digest,
      });
    }
    const executedAt = assertInstant(deletion_proof.executed_at,
      "request.subject.deletion_proof.executed_at");
    if (executedAt < createdAt) {
      return deletionResult({
        decision: "refuse", reason_id: "deletion_proof_precedes_artifact", ...base,
        created_at: raw.created_at, executed_at: deletion_proof.executed_at,
      });
    }
    if (executedAt > now) {
      return deletionResult({
        decision: "refuse", reason_id: "deletion_proof_after_now", ...base,
        executed_at: deletion_proof.executed_at,
      });
    }
  }

  // Step 9. COVERAGE BEFORE INVENTORY.
  //
  // This is the rule the approved derivative registration added — the session
  // approval named in the section header above, not Q129.D1 — and it sits ahead
  // of the inventory deliberately. Reading a list of registered
  // derivatives tells you what was registered; it tells you nothing about what
  // was never registered, and "we found nothing" is the exact shape a missing
  // producer integration takes. So an absent coverage answer refuses, and a
  // coverage answer that is anything other than "established" refuses, naming
  // the reason the loader gave rather than inventing one.
  //
  // The caller cannot talk its way past this: `derivative_coverage` is derived
  // from the record layer by the persistence tail, and a `state` outside the
  // registered vocabulary throws rather than being read as a near-miss.
  if (raw.derivative_coverage === undefined || raw.derivative_coverage === null) {
    return deletionResult({ decision: "refuse", reason_id: "derivative_coverage_missing", ...base });
  }
  const coverage = assertDerivativeCoverage(raw.derivative_coverage, "request.subject.derivative_coverage");
  if (coverage.state !== "established") {
    return deletionResult({
      decision: "refuse", reason_id: "derivative_coverage_unknown", ...base,
      derivative_coverage_state: coverage.state,
      derivative_coverage_reason_id: coverage.reason_id,
      registered_derivative_kinds: coverage.registered_derivative_kinds,
    });
  }

  // Step 10.
  //
  // Same rule as the holds: an omitted derivative inventory is not a verified
  // empty one.
  if (raw.derivatives === undefined || raw.derivatives === null) {
    return deletionResult({
      decision: "refuse", reason_id: "derivative_inventory_missing", ...base,
      derivative_coverage_state: coverage.state,
    });
  }
  assertArray(raw.derivatives, "request.subject.derivatives", { min: 0, max: 64 });
  const derivativeSeen = new Set();
  const derivatives = raw.derivatives.map((value, i) => {
    const item = assertExternalIdent(value, `request.subject.derivatives[${i}]`, { maxLength: 128 });
    if (derivativeSeen.has(item)) {
      fail("duplicate_derivative", `request.subject.derivatives repeats "${item}"`,
        { path: `request.subject.derivatives[${i}]`, derivative: item });
    }
    derivativeSeen.add(item);
    return item;
  });
  const unregistered = derivatives.filter(d => !entry.surviving_derivatives.includes(d));
  if (unregistered.length > 0) {
    return deletionResult({
      decision: "refuse", reason_id: "unregistered_derivative_blocks_deletion", ...base,
      derivative_coverage_state: coverage.state,
      // `observed_derivatives`, NOT `observed_surviving_derivatives`, and the
      // difference is the whole point of the correction this section carries. On
      // this path at least one observed kind is by definition NOT a survivor —
      // that is why the deletion refuses — so calling the list "surviving" would
      // be the one place a label lied about what was observed. The allow path
      // below may use the longer name, because the check just above it
      // guarantees every observed kind is class-permitted there.
      observed_derivatives: [...derivatives].sort(),
      unregistered_derivatives: unregistered,
    });
  }

  // THE ACCOUNTING RUNS ONE WAY, AND THIS IS THE CORRECTION Q129 ASKS FOR.
  //
  // An earlier revision also refused when a kind the class POLICY lists as
  // surviving was absent from the observed inventory — reason_id
  // surviving_derivative_unaccounted. That rule reads a class-level allowance as
  // an instance-level existence claim, and Q129 does not say that: the registry
  // "defines ... surviving derivatives separately for every artifact class",
  // which settles WHICH KINDS survive when the artifact goes, not that every
  // lease has an abstract AND an economics summary. With real registration
  // ingress the rule is actively wrong — an instance that legitimately produced
  // one of the two permitted kinds could never be deleted, no matter how
  // complete its coverage — and it would push a caller towards padding the
  // inventory to get past it, which is the opposite of the property wanted.
  //
  // What the old rule was protecting against is real, and is kept: the receipt
  // must not name survivors nobody looked for. That is now handled by SAYING
  // WHICH IS WHICH. `surviving_derivatives` is the class policy, verbatim from
  // the registry; `observed_surviving_derivatives` is what was actually
  // registered for this artifact under established coverage. A reader can tell
  // the two apart, so neither can be mistaken for the other.
  const observed = [...derivatives].sort();

  return deletionResult({
    decision: "allow", reason_id: "deletion_permitted", ...base,
    default_retention_days: entry.default_retention_days,
    elapsed_days: elapsedDays,
    deletion_proof_required: entry.deletion_proof_required,
    derivative_coverage_state: coverage.state,
    // Both names carry the same list HERE and only here, because the check above
    // has just established that every observed kind is one the class policy says
    // survives. That is what makes the longer name true on this path and false on
    // the refusal path above.
    observed_derivatives: observed,
    observed_surviving_derivatives: observed,
    registered_derivative_kinds: coverage.registered_derivative_kinds,
    deletion_receipt: deepFreeze({
      schema_version: V5_F01_DELETION_RECEIPT_SCHEMA_VERSION,
      tenant: ORGANIZATION_TENANT_ID,
      artifact_class, artifact_home, artifact_digest,
      retention_registry_digest: registry.registry_digest,
      // BESIDE THE REGISTRY DIGEST, AND HASHED WITH THE RECEIPT: the exact trigger
      // this deletion's period was measured from. A receipt that named the period
      // but not what started it could not be checked against the row it came from,
      // and the one thing a later reader must be able to establish is that the
      // clock was custody rather than the source's own observed instant.
      retention_clock: { ...clock },
      domain_policy_digest: v5F01PolicyDigest(),
      retention_started_at: clock.started_at,
      default_retention_days: entry.default_retention_days,
      elapsed_days: elapsedDays,
      deletion_proof_required: entry.deletion_proof_required,
      deletion_proof_ref: deletion_proof === null ? null : deletion_proof.proof_ref,
      deletion_proof_digest: deletion_proof === null ? null : deletion_proof.proof_digest,
      // Policy and observation, named apart and both hashed into the receipt.
      surviving_derivatives: [...entry.surviving_derivatives],
      surviving_derivatives_are_class_policy: true,
      observed_surviving_derivatives: observed,
      derivative_coverage_state: coverage.state,
      released_holds: holds.map(h => h.hold_id).sort(),
      actor: null,
      actor_derived_by: "authenticated_handler_context",
    }),
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned domain preimage and its digest.
//
// Nothing situational is bound — no timestamp, actor, session, registry or
// acceptance fact — so two callers describing the same contract reach the same
// digest. The caller's own field and retention registries are deliberately NOT
// in here: they are policy this module validates, not identity it carries.
//
// The digest is an identity for these bytes and nothing else. It is not an
// acceptance, not a receipt, and not evidence for any consumer gate.
// ---------------------------------------------------------------------------

export function v5F01PolicyPreimage() {
  return {
    schema_version: V5_F01_SCHEMA_VERSION,
    policy_version: V5_F01_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_subset_digest: v5F01DecisionSubsetDigest(),
    decisions: V5_F01_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_F01_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_F01_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
    record_homes: {
      homes: [...V5_F01_HOMES],
      fact_classes: V5_F01_FACT_CLASSES.map(fact_class => ({
        fact_class,
        home: FACT_CLASS_TABLE[fact_class].home,
        disposition: FACT_CLASS_TABLE[fact_class].disposition,
      })),
      markdown_authoritative: false,
      dashboard_authoritative: false,
      browser_session_authoritative: false,
      conversation_prose_authoritative: false,
      prompt_authoritative: false,
      local_file_authoritative: false,
      summaries_and_embeddings_disposable: true,
      diagrams_and_views_are_projections: true,
      temporary_json_is_review_evidence: true,
    },
    field_authority: {
      registry_schema_version: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
      write_directions: [...V5_F01_WRITE_DIRECTIONS],
      version_comparators: [...V5_F01_VERSION_COMPARATORS],
      conflict_behaviors: [...V5_F01_CONFLICT_BEHAVIORS],
      taint_classes: [...V5_F01_TAINT_CLASSES],
      owners_and_directions_are_policy_input: true,
      lexical_ordering_of_opaque_versions: false,
      silent_last_write_wins_permitted: false,
      non_owner_may_overwrite_owner_value: false,
    },
    resolution_records: {
      record_kinds: [...V5_F01_RECORD_KINDS],
      current_state_transition_schema_version: V5_F01_TRANSITION_SCHEMA_VERSION,
      event_schema_version: V5_F01_EVENT_SCHEMA_VERSION,
      mutation_receipt_schema_version: V5_F01_RECEIPT_SCHEMA_VERSION,
      reconciliation_schema_version: V5_F01_RECONCILIATION_SCHEMA_VERSION,
      whole_product_event_sourcing: false,
      any_one_record_substitutes_for_another: false,
      actor_derived_by: "authenticated_handler_context",
    },
    corporate_sources: {
      artifact_schema_version: V5_F01_ARTIFACT_SCHEMA_VERSION,
      proposal_schema_version: V5_F01_PROPOSAL_SCHEMA_VERSION,
      proposal_link_schema_version: V5_F01_PROPOSAL_LINK_SCHEMA_VERSION,
      evidence_classes: [...V5_F01_EVIDENCE_CLASSES],
      tour_only_evidence_classes: [...V5_F01_TOUR_ONLY_EVIDENCE_CLASSES],
      source_agnostic: true,
      live_adapter_included: false,
      proposal_becomes_fact: false,
      proposal_carries_effect_authority: false,
      proposal_link_reversible: true,
    },
    document_identity: {
      schema_version: V5_F01_DOCUMENT_SCHEMA_VERSION,
      preparation_states: [...V5_F01_PREPARATION_STATES],
      delivery_states: [...V5_F01_DELIVERY_STATES],
      signature_states: [...V5_F01_SIGNATURE_STATES],
      validity_states: [...V5_F01_VALIDITY_STATES],
      version_states: [...V5_F01_VERSION_STATES],
      filing_states: [...V5_F01_FILING_STATES],
      coherence_constraints: DOCUMENT_COHERENCE.map(rule => rule.constraint),
      identity_split: {
        identity_and_state: "neon_record_layer",
        working_and_sealed_bytes: "object_storage",
        official_executed_copy: "onedrive",
      },
      official_filing_inferable_from_other_homes: false,
    },
    retention: {
      registry_schema_version: V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
      deletion_receipt_schema_version: V5_F01_DELETION_RECEIPT_SCHEMA_VERSION,
      hold_states: [...V5_F01_HOLD_STATES],
      periods_constraints_and_derivatives_are_policy_input: true,
      unknown_hold_blocks_deletion: true,
      silent_purge_permitted: false,
      surviving_derivatives_are_class_policy_not_instance_inventory: true,
      // WHEN A RETENTION PERIOD STARTS, in the hashed bytes, so relaxing it moves
      // the contract digest rather than passing unnoticed. The default is the
      // server-stamped custody instant; the source's observed instant never
      // starts a period, under that name or any other; an unreadable or
      // unverified trigger blocks the deletion; and no caller supplies one.
      retention_clock_kinds: [...V5_F01_RETENTION_CLOCK_KINDS],
      default_retention_clock_kind: V5_F01_DEFAULT_RETENTION_CLOCK_KIND,
      retention_starts_at_server_recorded_custody: true,
      source_observed_at_starts_retention: false,
      unknown_retention_clock_blocks_deletion: true,
      caller_may_supply_retention_clock: false,
      explicit_retention_clock_event_producer_established: false,
    },
    derivative_registration: {
      link_schema_version: V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION,
      coverage_states: [...V5_F01_DERIVATIVE_COVERAGE_STATES],
      // The APPROVED REGISTRATION RULE — the session approval named in the
      // section header, not a tenth settled decision — carried in the hashed
      // bytes so relaxing any of it moves the contract digest rather than
      // passing unnoticed. It is a property of this contract, and deliberately
      // NOT an entry in `decisions` above: that list is the nine, and adding a
      // tenth id here would be inventing one.
      registration_precedes_derivative_completion: true,
      only_trusted_producer_workflows_register_links: true,
      links_are_provenance_not_exhaustive_inventory: true,
      empty_link_set_means_verified_absence: false,
      unknown_coverage_blocks_deletion: true,
      caller_may_assert_coverage: false,
      registration_authorizes_deletion: false,
    },
    caller_authority: {
      injection_fragments: [...V5_F01_AUTHORITY_INJECTION_FRAGMENTS].sort(),
      proposal_effect_fragments: [...PROPOSAL_EFFECT_FRAGMENTS].sort(),
      caller_may_supply_actor: false,
      caller_may_select_tenant: false,
    },
  };
}

/** The deterministic `sha256:` digest of the closed F01 domain contract. */
export function v5F01PolicyDigest() {
  return digest(v5F01PolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5F01PolicyCanonicalBytes() {
  return canonicalJson(v5F01PolicyPreimage());
}

/**
 * The zero-effect projection of the whole F01 domain: what is settled, what the
 * contract hashes to, and the explicit statement that reading it accepts nothing
 * and completes nothing.
 */
export function v5F01AuthorityProjection(options = {}) {
  assertObject(options, "options");
  assertClosedKeys(options, ["expected_policy_digest"], "options");
  const policy_digest = v5F01PolicyDigest();
  if (options.expected_policy_digest !== undefined && options.expected_policy_digest !== null) {
    assertDigestRef(options.expected_policy_digest, "options.expected_policy_digest");
    if (options.expected_policy_digest !== policy_digest) {
      fail("stale_expected_digest",
        "the contract no longer hashes to the expected digest; re-read it rather than acting on the stale one",
        { expected: options.expected_policy_digest, actual: policy_digest });
    }
  }
  return deepFreeze({
    schema_version: V5_F01_SCHEMA_VERSION,
    policy_version: V5_F01_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    policy_digest,
    decision_ids: [...V5_F01_SETTLED_DECISION_IDS],
    decision_subset_digest: v5F01DecisionSubsetDigest(),
    homes: [...V5_F01_HOMES],
    // Deferred work, named so nobody reads this projection as one of them.
    persistence_installed: false,
    handlers_registered: false,
    live_adapter_available: false,
    provider_readback_performed: false,
    f01_source_complete: false,
    foundation_or_global_source_authority_accepted: false,
    accepts_anything: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Load-time self-checks.
//
// Each one is an invariant a later edit could break silently. Checking them here
// means the module's own import fails rather than a caller's request, and it is
// what lets the caller-authority guard above be strict without risking a false
// refusal of a legitimate field.
// ---------------------------------------------------------------------------

const ALL_ACCEPTED_KEY_SETS = Object.freeze([
  ["decisions", "decision_subset_digest"],
  HOME_REQUEST_KEYS, REGISTRY_KEYS, ENTRY_KEYS, PERMITTED_SOURCE_KEYS, COMPILED_REGISTRY_KEYS,
  RESOLVE_KEYS, NATIVE_IDENTITY_KEYS, PROVENANCE_KEYS, READBACK_KEYS, OBSERVATION_KEYS,
  CURRENT_STATE_KEYS, ARTIFACT_KEYS, ADMIT_KEYS, PROPOSAL_KEYS, BINDING_KEYS, PROPOSAL_REQUEST_KEYS,
  NEON_IDENTITY_KEYS, OBJECT_STORAGE_IDENTITY_KEYS, ONEDRIVE_IDENTITY_KEYS, DOCUMENT_KEYS,
  DOCUMENT_REQUEST_KEYS, RETENTION_POLICY_KEYS, RETENTION_CLASS_KEYS, COMPILED_RETENTION_KEYS,
  RETENTION_CLOCK_POLICY_KEYS, RETENTION_CLOCK_KEYS,
  HOLD_KEYS, DELETION_PROOF_KEYS, DELETION_SUBJECT_KEYS, DELETION_REQUEST_KEYS,
  DERIVATIVE_IDENTITY_KEYS, DERIVATIVE_PRODUCER_KEYS, DERIVATIVE_EVIDENCE_KEYS,
  DERIVATIVE_REGISTRATION_KEYS, DERIVATIVE_SOURCE_ARTIFACT_KEYS, DERIVATIVE_REQUEST_KEYS,
  DERIVATIVE_COVERAGE_KEYS,
  ["expected_policy_digest"],
]);

for (const keys of ALL_ACCEPTED_KEY_SETS) {
  for (const key of keys) {
    for (const fragment of V5_F01_AUTHORITY_INJECTION_FRAGMENTS) {
      if (key.toLowerCase().includes(fragment)) {
        throw new V5F01Error("authority_guard_collides_with_contract",
          `the caller-authority guard would refuse the legitimate field "${key}"`, { key, fragment });
      }
    }
  }
}
for (const key of PROPOSAL_KEYS) {
  for (const fragment of PROPOSAL_EFFECT_FRAGMENTS) {
    if (key.toLowerCase().includes(fragment)) {
      throw new V5F01Error("effect_guard_collides_with_contract",
        `the proposal effect guard would refuse the legitimate field "${key}"`, { key, fragment });
    }
  }
}
// Every home must be the home of at least one authoritative fact class, and
// every fact class must name a registered home or none at all.
for (const home of V5_F01_HOMES) {
  if (!V5_F01_AUTHORITATIVE_FACT_CLASSES.some(c => FACT_CLASS_TABLE[c].home === home)) {
    throw new V5F01Error("unreachable_home", `no fact class lives in home "${home}"`, { home });
  }
}
for (const fact_class of V5_F01_FACT_CLASSES) {
  const entry = FACT_CLASS_TABLE[fact_class];
  if (entry.disposition === "authoritative") {
    if (!V5_F01_HOMES.includes(entry.home)) {
      throw new V5F01Error("invalid_fact_class_home",
        `fact class "${fact_class}" names an unregistered home`, { fact_class });
    }
  } else {
    if (entry.home !== null || !V5_F01_NON_AUTHORITATIVE_DISPOSITIONS.includes(entry.disposition)) {
      throw new V5F01Error("invalid_fact_class_disposition",
        `fact class "${fact_class}" must name a registered non-authoritative disposition and no home`,
        { fact_class });
    }
  }
}
// Vocabulary values are internal constants and stay in one shape, so a caller
// comparing against them never has to guess at casing or separators.
for (const [name, vocabulary] of Object.entries({
  V5_F01_HOMES, V5_F01_WRITE_DIRECTIONS, V5_F01_VERSION_COMPARATORS, V5_F01_CONFLICT_BEHAVIORS,
  V5_F01_TAINT_CLASSES, V5_F01_EVIDENCE_CLASSES, V5_F01_TOUR_ONLY_EVIDENCE_CLASSES,
  V5_F01_PREPARATION_STATES, V5_F01_DELIVERY_STATES, V5_F01_SIGNATURE_STATES,
  V5_F01_VALIDITY_STATES, V5_F01_VERSION_STATES, V5_F01_FILING_STATES, V5_F01_HOLD_STATES,
  V5_F01_RECORD_KINDS, V5_F01_FACT_CLASSES, V5_F01_NON_AUTHORITATIVE_DISPOSITIONS,
  V5_F01_DERIVATIVE_COVERAGE_STATES, V5_F01_RETENTION_CLOCK_KINDS,
  V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND: [V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND],
})) {
  for (const value of vocabulary) {
    if (!VOCAB_SLUG.test(value)) {
      throw new V5F01Error("invalid_vocabulary_value",
        `${name} carries "${value}", which is not a lower-case vocabulary slug`, { name, value });
    }
  }
}
// The two named retention-clock kinds must be registered, and must be different
// kinds. A default that named an unregistered value would make every class's
// resolved clock unreadable; a default equal to the explicit kind would silently
// give every legacy class an event nobody can produce.
if (!V5_F01_RETENTION_CLOCK_KINDS.includes(V5_F01_DEFAULT_RETENTION_CLOCK_KIND) ||
    !V5_F01_RETENTION_CLOCK_KINDS.includes(V5_F01_EXPLICIT_RETENTION_CLOCK_KIND) ||
    V5_F01_DEFAULT_RETENTION_CLOCK_KIND === V5_F01_EXPLICIT_RETENTION_CLOCK_KIND) {
  throw new V5F01Error("invalid_retention_clock_vocabulary",
    "the default and explicit retention-clock kinds must be two registered, distinct kinds",
    { default_kind: V5_F01_DEFAULT_RETENTION_CLOCK_KIND,
      explicit_kind: V5_F01_EXPLICIT_RETENTION_CLOCK_KIND });
}
