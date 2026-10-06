// DoctorCRE v5 slice V5-J102: the healthcare CRE lifecycle — Prospect through
// Client/Engagement/Assignment/Deal — as one closed, versioned domain kernel.
//
// Thirteen settled decisions (Q069, Q072, Q077, Q078, Q079, Q080, Q081, Q082,
// Q083, Q094, Q095, Q096, Q103) are encoded here as typed transitions with
// declared prerequisites, evidence, permitted actors, side effects and
// reversibility. Canonicalization and hashing come from artifact-trust.js, the
// one server-held tenant from identity.js, and the no-effects marker from
// global-boundaries.v5.js; this file reimplements none of them.
//
// THE SETTLED REQUIREMENT CONTROLS, NOT THE RECOMMENDATION IT REPLACED. Four of
// the thirteen were MODIFIED by Joe against the assistant's original proposal,
// and in each case the older proposal is the tidier thing to implement.
// V5_J102_USER_CORRECTIONS below names all four, quotes the correction, and
// names the superseded recommendation, so a future reader who reaches for the
// neater rule can see it was already considered and overruled:
//
//   Q078 — an accepted LOI does NOT wait for a fully executed lease to become a
//          Deal. Selection AND commitment to the winning accepted lease LOI
//          creates a PENDING Deal in negotiation; lease signing then marks the
//          EXECUTED lease state. The "executed instruments only" rule is dead.
//   Q094 — a signed purchase contract is LEGALLY executed and BUSINESS pending.
//          It runs through due diligence and closes only on the actual final
//          closing date. Signing is not closing.
//   Q095 — multiple LOIs are normal and are RETAINED. One selected winning
//          property and one active lease-draft target, unless an explicitly
//          approved exception.
//   Q069 — a signed effective ETL (or an approved equivalent) creates the Client
//          AND the Engagement; Salesforce's early opportunity stays a separate
//          corporate case.
//
// WHAT IS CODE HERE AND WHAT MUST ARRIVE AS EVIDENCE:
//
//   IN CODE — the STRUCTURE the decisions settle. The four distinct entities,
//   the closed state vocabularies on each axis, the transition table, which
//   facts are coupled, which axes are orthogonal, and which refusals exist.
//   These are identity, not configuration.
//
//   AS SERVER-LOADED EVIDENCE — every fact about the world. Whether an
//   engagement letter is signed and effective, whether an LOI was accepted,
//   whether a lease was executed, whether a closing actually happened. This
//   module invents none of them and accepts none of them from a caller: an
//   evidence record must carry server provenance, and a caller-supplied
//   "signed: true" is refused by name rather than read.
//
// THE TWO DOORS, AND WHY THE SECOND ONE IS NARROW. A TRANSITION advances a
// subject that already exists, against its committed row. An INITIALIZATION
// creates the first row of a chain — a prospect relationship, an assignment under
// an already active engagement, a property negotiation under a still-open
// assignment — and it is a SEPARATE, deliberately narrow admission rather than a
// transition with its prerequisites switched off. It creates the earliest
// declared state of its kind and nothing later, it carries no evidence because no
// evidence in this rail can bind to a subject that does not yet exist, and it
// weakens no transition: the client status, the opened assignment, the submitted
// LOI and the pending Deal all still require their own evidence afterwards. See
// the INITIALIZATION section below for the whole of it.
//
// TWO KINDS OF NO, following S01 and F01 deliberately:
//   * A POLICY ANSWER is returned — a frozen result whose `decision` is "allow",
//     "refuse" or "reconcile", with a stable `reason_id`. A refusal is an answer
//     the caller may record.
//   * A CONTRACT VIOLATION throws V5J102Error. Unknown fields, unknown
//     vocabulary values, open schemas, accessor properties, unreadable
//     timestamps and caller-supplied authority or evidence fields are not policy
//     questions; the module cannot read the request at all, so it fails closed
//     rather than guessing which settled boundary was meant.
//
// THE ONE-AUTHORITY RULE. This module JUDGES and PROJECTS. It stores no state,
// no event, no receipt and no reference. Every function is pure: no filesystem,
// no network, no database, no provider, no scheduler, no environment and no
// clock. Every evaluation that depends on time takes `now` from its caller.
// `V5_NO_EFFECTS` rides on every result to say so in the record.
//
// WHAT THIS FILE IS NOT. It is not persistence, not a handler, not a Salesforce
// adapter and not an acceptance path. Tour activation is OUT OF SCOPE by
// settlement rather than by omission: Q072 and Q080 both moved active Tour
// behaviour into Journey 3 so Journey 1 cannot depend on it, and no transition
// below creates, activates or reads a Tour.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";

export { V5_NO_EFFECTS };

export const V5_J102_SCHEMA_VERSION = "doctorcre-v5-cre-lifecycle.v1";
// 3 rather than 2: the policy preimage below gained the initialization contracts
// (2), and then the reconciliation conflict-kind vocabulary the SQL write boundary
// now enforces (3). Its digest is a different identity for a different contract
// each time, and the version is moved with the bytes rather than left to be
// inferred from them.
export const V5_J102_POLICY_VERSION = 3;

export const V5_J102_SUBJECT_SCHEMA_VERSION = "doctorcre-v5-j102-lifecycle-subject.v1";
export const V5_J102_EVIDENCE_SCHEMA_VERSION = "doctorcre-v5-j102-lifecycle-evidence.v1";
export const V5_J102_EVENT_SCHEMA_VERSION = "doctorcre-v5-j102-lifecycle-event.v1";
export const V5_J102_TRANSITION_SCHEMA_VERSION = "doctorcre-v5-j102-lifecycle-transition.v1";
export const V5_J102_SALESFORCE_REFERENCE_SCHEMA_VERSION =
  "doctorcre-v5-j102-salesforce-reference.v1";
export const V5_J102_RECONCILIATION_SCHEMA_VERSION =
  "doctorcre-v5-j102-lifecycle-reconciliation-item.v1";
export const V5_J102_COMPATIBILITY_SCHEMA_VERSION =
  "doctorcre-v5-j102-legacy-compatibility-projection.v1";

const DIGEST_REF = /^sha256:[0-9a-f]{64}$/;
// Captured rather than merely shape-matched, because the calendar has to be
// checked against the LITERAL fields; see assertInstant.
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;
// Caller-declared external identifiers: subject ids, property ids, document ids,
// reason refs. Deliberately not open; combined with assertSafeText below,
// nothing invisible or reorderable rides in.
const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;

/**
 * The code-point ranges no identifier or free text may contain: C0 and C1
 * controls, zero-width joiners and spaces, the bidirectional overrides and
 * isolates, the invisible mathematical operators, and the byte-order mark. An
 * identifier that renders as another identifier is an identity split waiting to
 * happen, so it is refused rather than normalized.
 *
 * WRITTEN AS NUMERIC RANGES RATHER THAN A REGEX CHARACTER CLASS, deliberately.
 * The obvious spelling is a class of escapes, and F01 records what that costs at
 * its own copy of this guard: a literal control byte reaching the committed
 * source makes the blob read as binary to file(1), rg and git diff, so the
 * module that refuses invisible characters in its inputs stops being reviewable
 * as text itself. Hex code points cannot become bytes by accident, and the
 * ranges read as what they are.
 */
const UNSAFE_CODE_POINT_RANGES = Object.freeze([
  [0x0000, 0x001f], [0x007f, 0x009f], [0x200b, 0x200f], [0x202a, 0x202e],
  [0x2060, 0x2064], [0x2066, 0x2069], [0xfeff, 0xfeff],
]);

function hasUnsafeCodePoint(value) {
  for (const character of value) {
    const point = character.codePointAt(0);
    for (const [low, high] of UNSAFE_CODE_POINT_RANGES) {
      if (point >= low && point <= high) return true;
    }
  }
  return false;
}

export class V5J102Error extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J102Error";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J102Error(code, message, detail);
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
// The two caller guards.
//
// AUTHORITY INJECTION — a field purporting to confer authority. The list is
// F01's, restated here rather than imported, because this module is a PEER of
// F01 and not a client of it: importing the record-authority kernel's list would
// put a record-authority module in the lifecycle graph for the sake of an array.
// The suite asserts the two lists agree, so they cannot drift silently.
//
// ASSERTED-FACT INJECTION — the one this slice adds, and the one that matters
// here. `signed`, `approved`, `verified`, `executed`, `accepted`, `closed`,
// `is_client` and their kin are not authority claims, so the F01 list does not
// name them — but a caller that could state one would be deciding the very fact
// the transition exists to establish from evidence. Q072 settles that models
// PROPOSE and deterministic commands VALIDATE; a caller boolean is the same
// failure with the model taken out.
//
// BOTH GUARDS RUN ONLY ON KEYS THE SCHEMA DOES NOT ACCEPT. A LOADED subject
// legitimately carries `execution_state`, and a LOADED document legitimately
// carries `signature_state`; refusing those would refuse the state itself. What
// stops a caller choosing them is that the store loads subjects and evidence
// from the database and refuses a caller-supplied `subject` or `evidence` by
// name. The load-time self-check at the foot of this file proves that every key
// this module DOES accept while colliding with a guard fragment is one of four
// deliberate, server-derived exceptions.
// ---------------------------------------------------------------------------

export const V5_J102_AUTHORITY_INJECTION_FRAGMENTS = deepFreeze([
  "actor", "acting_as", "on_behalf_of", "impersonat", "admin", "sudo", "superuser",
  "authority", "authorized_by", "authorization", "authenticated_",
  "privilege", "grant", "delegation", "redecision", "approved_by", "signed_off",
  "override", "bypass", "force_", "trusted_caller", "permission",
  "owner_override", "is_owner", "owner_slug", "as_tenant", "tenant_override",
]);

export const V5_J102_ASSERTED_FACT_FRAGMENTS = deepFreeze([
  "signed", "signature", "approved", "approval", "verified", "verification",
  "executed", "execution", "accepted", "acceptance", "closed", "closing_confirmed",
  "is_client", "client_status", "deal_status", "stage", "phase_override",
  "countersigned", "attested", "confirmed_by_caller",
]);

function assertNoInjectedNames(keys, path, { allowAsserted = false } = {}) {
  for (const key of keys) {
    const normalized = key.toLowerCase();
    for (const fragment of V5_J102_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_authority_field_refused",
          `${path}.${key} names authority the caller cannot supply; the handler derives actor and tenant`,
          { path: `${path}.${key}`, key, fragment });
      }
    }
    if (allowAsserted) continue;
    for (const fragment of V5_J102_ASSERTED_FACT_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_asserted_fact_refused",
          `${path}.${key} asserts the fact this transition establishes from evidence; supply a reference, not a verdict`,
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
function assertClosedKeys(object, allowed, path, options = {}) {
  assertNoAccessorsOrHiddenKeys(object, path);
  const keys = Object.keys(object);
  assertNoInjectedNames(keys.filter(key => !allowed.includes(key)), path, options);
  for (const key of keys) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
  return object;
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object) || object[key] === undefined || object[key] === null) {
      fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
  }
  return object;
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
 * Strings that carry identity are checked, not merely typed. Well-formed, free
 * of control and invisible format characters, already in NFC, untrimmed
 * whitespace refused. Two identifiers that render identically but differ in
 * bytes would silently become two clients, or one client with two histories.
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
  if (hasUnsafeCodePoint(value)) {
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
 * "2026-02-31T00:00:00Z" becomes 3 March, and a closing date nobody wrote is not
 * the date the deal closed on.
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

function nullableInstant(value, path) {
  if (value === undefined || value === null) return null;
  assertInstant(value, path);
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !DIGEST_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference over 64 lower-case hex characters`, { path });
  }
  return value;
}

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `${path} must be "${ORGANIZATION_TENANT_ID}"; the handler derives it`,
      { path, expected: ORGANIZATION_TENANT_ID });
  }
  return value;
}

// ---------------------------------------------------------------------------
// The thirteen settled decisions. Text and evidence digests are copied verbatim
// from the reviewed J102 source binding; they are identity, not configuration. A
// caller that believes it holds a different subset proves the disagreement here
// rather than discovering it later.
// ---------------------------------------------------------------------------

export const V5_J102_SETTLED_DECISIONS = deepFreeze({
  "Q069.D1": {
    settled_requirement: "A signed effective ETL or approved equivalent creates Client status; a committed accepted lease LOI or signed purchase contract creates a pending Deal, while Salesforce's early opportunity remains a separate corporate case.",
    source_evidence_digest: "af430ac9ce47a3cab68b93ae6b098e6681846e3a0bda3b25fa29a1aeee7e7e9a",
  },
  "Q072.D1": {
    settled_requirement: "Models may propose lifecycle changes from unstructured evidence, but deterministic commands map signed effective ETL or approved equivalent to Client and active engagement, search initiation to Assignment research or search, LOI submission to Assignment negotiation without creating a Deal, commitment to the winning accepted LOI to a pending negotiation Deal, lease execution to executed lease, signed purchase contract to executed-but-pending due diligence until closing, and completion or payment evidence to their separate orthogonal axes; correction is receipted.",
    source_evidence_digest: "8e27b8e4a5e9c902be7f8529793ca0404cb5ce778adc48bbefa40f0deb8c96bc",
  },
  "Q077.D1": {
    settled_requirement: "Client status requires an active signed ETL or another approved representation agreement; work before signature remains prospect or pre-engagement work.",
    source_evidence_digest: "478b3f839f0f19045c0e2cb3dcf688e2a778ea1f8a76f58dd4e01a698f39346b",
  },
  "Q078.D1": {
    settled_requirement: "Selection and commitment to the winning accepted lease LOI creates a pending Deal in negotiation; lease signing creates the executed lease state.",
    source_evidence_digest: "9942840ef3d0454985c05d815a48b558f7864d0b22601ef38a2620be525c458a",
  },
  "Q079.D1": {
    settled_requirement: "Model Client, Engagement, Assignment, and Deal separately so one client can hold multiple mandates, negotiations, and transactions without duplication.",
    source_evidence_digest: "0f7e97b4c09ce2e514d278108416d335b1f7e429ff912f0b978d577c583be49d",
  },
  "Q080.D1": {
    settled_requirement: "Assignment owns research, search, and multiple negotiations; the selected accepted LOI creates a pending negotiation Deal, and signed lease or purchase evidence creates executed state with later diligence, close, commission, invoice, and payment axes. Active Tour behavior is excluded from Journey 1.",
    source_evidence_digest: "8321243bef91e506dbc88fcf7b0627ac4393639e57a7f419545b3ce04c9d8147",
  },
  "Q081.D1": {
    settled_requirement: "Migrate without a big-bang rename: introduce corrected semantics, classify from evidence, reconcile ambiguity, provide compatibility views, and retire old callers only after migration proof.",
    source_evidence_digest: "b62fd397ef5b07762f8d3b7376bb253feda6ed9c7b9235231e1d5430a26f5c2b",
  },
  "Q082.D1": {
    settled_requirement: "Replace free stage changes with typed transitions that declare prerequisites, evidence, actors, side effects, and reversibility; coupled facts commit atomically or refuse.",
    source_evidence_digest: "a2ba5a203159d94d5b228c8964277443b8a13dffbb37708875a7805a7a39da59",
  },
  "Q083.D1": {
    settled_requirement: "Store Salesforce opportunities as external corporate references progressively linked to the appropriate prospect, engagement, assignment, or Deal without copying Salesforce labels into DoctorCRE lifecycle state.",
    source_evidence_digest: "14e0f6c8b7872623f1adf098bb427c6f06245724cfea311383c5014acc72308c",
  },
  "Q094.D1": {
    settled_requirement: "A signed purchase contract is legally executed but remains a pending DoctorCRE Deal through due diligence and becomes closed only on the final closing date.",
    source_evidence_digest: "e26c7328f8b14ce1e57b336339b83368c996f29b12f956f7d834fca932270686",
  },
  "Q095.D1": {
    settled_requirement: "Allow concurrent LOIs and negotiations, then commit to one selected winning property and only one active lease-draft target unless an explicit exception is approved.",
    source_evidence_digest: "7b5dd449f161d46f2410ebf37517a542bd4d79c7a46fe21e2a743a08380fda43",
  },
  "Q096.D1": {
    settled_requirement: "When a pending Deal fails, cancel it with preserved reason and history and return the Assignment to search or negotiation without losing the Client relationship.",
    source_evidence_digest: "ced9487c70c73edd1fccf68ece6f46f3bfe8fe32bd75fff9a6455eb990b0a0db",
  },
  "Q103.D1": {
    settled_requirement: "Use optimistic concurrency with automatic merge only for nonoverlapping edits and visible reconciliation for lifecycle, financial, recipient, or document conflicts; expose ownership, freshness, and active automation.",
    source_evidence_digest: "5eee5209795ad6bcb956dfd10994c3c1680cec59f4445d5d783053a637c9c60c",
  },
});

export const V5_J102_SETTLED_DECISION_IDS =
  deepFreeze(Object.keys(V5_J102_SETTLED_DECISIONS).sort());

/**
 * The four places Joe overruled the assistant's original recommendation, kept in
 * code because each superseded rule is the one a future reader is most likely to
 * re-derive from first principles and quietly reinstate.
 */
export const V5_J102_USER_CORRECTIONS = deepFreeze([
  {
    requirement_id: "Q069",
    superseded_recommendation:
      "A signed ETL creates an engagement, and LOIs plus resulting lease or purchase work belong to a transaction.",
    user_correction:
      "once an etl is signed, we have a client. once a lease is agreed to or a purchase contract is signed, we have a deal for that client. all else agreed",
    encoded_as:
      "establish-client-and-engagement writes the Client status and the Engagement as one coupled fact.",
  },
  {
    requirement_id: "Q078",
    superseded_recommendation:
      "Require a fully executed lease, signed purchase contract, or signed renewal/amendment before a Deal exists; an accepted LOI remains negotiation.",
    user_correction:
      "accepted LOI would be a pending deal so still negotiation. it becomes an executed deal at lease signing",
    encoded_as:
      "commit-winning-property creates the PENDING Deal; record-lease-execution marks the executed lease state.",
  },
  {
    requirement_id: "Q094",
    superseded_recommendation:
      "For purchases, a signed purchase contract creates the executed deal directly.",
    user_correction:
      "no - a purchase contract signed is a pending deal that goes through due dilligence. technically it is an executed deal but it doesn't close until the final closing day",
    encoded_as:
      "record-purchase-contract-execution sets execution_state executed and leaves deal_state pending with diligence in progress; only record-deal-closing closes it.",
  },
  {
    requirement_id: "Q095",
    superseded_recommendation:
      "Multiple accepted LOIs should be allowed only when explicitly supported by the assignment.",
    user_correction:
      "agreed - in fact we always submit multiple LOIs if we can. we dont do multiple lease drafts though. common practice would be once we select the winning property in the LOI phase we commit to negotiations with them at that time.",
    encoded_as:
      "Concurrent LOIs and acceptances are unconstrained; the single-target constraint binds the SELECTED property and the active lease-draft target only.",
  },
]);

export function v5J102DecisionSubsetPreimage() {
  return {
    schema_version: "doctorcre-v5-j102-decision-subset.v1",
    decisions: V5_J102_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_J102_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_J102_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
  };
}

export function v5J102DecisionSubsetDigest() {
  return digest(v5J102DecisionSubsetPreimage());
}

/**
 * Refuse a caller whose decision subset has drifted from the reviewed thirteen.
 * Drift is checked in both directions — a missing decision and an extra one are
 * both drift — and every source-evidence digest must match exactly.
 */
export function assertJ102DecisionBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decisions", "decision_subset_digest"], "binding");
  assertRequiredKeys(binding, ["decisions"], "binding");
  assertObject(binding.decisions, "binding.decisions");
  assertNoAccessorsOrHiddenKeys(binding.decisions, "binding.decisions");
  if (binding.decision_subset_digest !== undefined && binding.decision_subset_digest !== null) {
    assertDigestRef(binding.decision_subset_digest, "binding.decision_subset_digest");
    if (binding.decision_subset_digest !== v5J102DecisionSubsetDigest()) {
      fail("decision_binding_drift", "the decision subset digest does not match the reviewed subset",
        { expected: v5J102DecisionSubsetDigest(), actual: binding.decision_subset_digest });
    }
  }
  const supplied = Object.keys(binding.decisions).sort();
  const missing = V5_J102_SETTLED_DECISION_IDS.filter(id => !supplied.includes(id));
  const extra = supplied.filter(id => !V5_J102_SETTLED_DECISION_IDS.includes(id));
  if (missing.length > 0 || extra.length > 0) {
    fail("decision_binding_drift", "the supplied decision set is not the reviewed thirteen",
      { missing, extra });
  }
  for (const id of V5_J102_SETTLED_DECISION_IDS) {
    const entry = assertObject(binding.decisions[id], `binding.decisions.${id}`);
    assertClosedKeys(entry, ["source_evidence_digest", "settled_requirement"], `binding.decisions.${id}`);
    assertRequiredKeys(entry, ["source_evidence_digest"], `binding.decisions.${id}`);
    const supplied_digest = entry.source_evidence_digest;
    if (typeof supplied_digest !== "string" || !/^[0-9a-f]{64}$/.test(supplied_digest)) {
      fail("invalid_digest",
        `binding.decisions.${id}.source_evidence_digest must be 64 lower-case hex characters`,
        { path: `binding.decisions.${id}.source_evidence_digest` });
    }
    if (supplied_digest !== V5_J102_SETTLED_DECISIONS[id].source_evidence_digest) {
      fail("decision_binding_drift", `source-evidence digest drift on ${id}`, {
        decision_id: id, expected: V5_J102_SETTLED_DECISIONS[id].source_evidence_digest,
        actual: supplied_digest,
      });
    }
    if (entry.settled_requirement !== undefined && entry.settled_requirement !== null &&
        entry.settled_requirement !== V5_J102_SETTLED_DECISIONS[id].settled_requirement) {
      fail("decision_binding_drift", `settled requirement text drift on ${id}`, { decision_id: id });
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// Q079 — the four entities, kept apart.
//
// One client may hold several engagements, each engagement several assignments,
// each assignment several property negotiations, and each assignment at most one
// live Deal. Nothing below flattens them: a Deal never carries the assignment's
// search phase, and an assignment never carries the deal's execution state.
// ---------------------------------------------------------------------------

export const V5_J102_SUBJECT_KINDS = deepFreeze([
  "relationship", "engagement", "assignment", "property_negotiation", "deal",
]);

/** Q069 / Q077. Everything before signature is prospect or pre-engagement work. */
export const V5_J102_RELATIONSHIP_STATES = deepFreeze([
  "prospect", "client", "client_paused", "client_ended",
]);

export const V5_J102_ENGAGEMENT_STATES = deepFreeze(["active", "expired", "terminated"]);

/**
 * The representation basis a Client status rests on. Q077 admits an ETL OR
 * another approved representation agreement; the two are DIFFERENT bases and are
 * recorded as such, because an equivalence rests on a typed approval an ETL does
 * not need, and a reader has to be able to tell which one a client stands on.
 */
export const V5_J102_REPRESENTATION_BASES = deepFreeze([
  "signed_engagement_letter", "approved_representation_equivalent",
]);

/** Q080. Research, search and every negotiation live on the assignment. */
export const V5_J102_ASSIGNMENT_PHASES = deepFreeze([
  "research", "search", "negotiation", "committed", "concluded",
]);

/** Q095. Concurrent negotiations are normal, and the alternatives are retained. */
export const V5_J102_NEGOTIATION_STATES = deepFreeze([
  "loi_drafted", "loi_submitted", "loi_countered", "loi_accepted",
  "loi_rejected", "loi_withdrawn", "selected_winner", "superseded",
]);

export const V5_J102_INSTRUMENT_KINDS = deepFreeze([
  "lease", "purchase", "renewal", "amendment",
]);

/**
 * THE DEAL'S AXES, AND WHY THERE ARE EIGHT OF THEM.
 *
 * Q080 lists execution, diligence, close/commencement, commission agreement,
 * invoicing, payment and completion as SEPARATE later axes, and Q094 turns on
 * exactly that separation: a signed purchase contract is executed on the
 * execution axis and pending on the business axis at the same instant.
 * Collapsing any two of these into one enum is what made the legacy record
 * unable to say "legally executed, not yet closed" — the single defect this
 * slice exists to remove. Each axis has its own closed vocabulary, its own
 * evidence and its own transition.
 */
export const V5_J102_DEAL_STATES = deepFreeze(["pending", "closed", "cancelled"]);
export const V5_J102_EXECUTION_STATES = deepFreeze(["unexecuted", "executed"]);
export const V5_J102_DILIGENCE_STATES = deepFreeze([
  "not_applicable", "in_progress", "waived", "satisfied", "failed",
]);
export const V5_J102_CLOSING_STATES = deepFreeze(["not_reached", "closed"]);
export const V5_J102_COMMISSION_STATES = deepFreeze(["absent", "agreed"]);
export const V5_J102_INVOICE_STATES = deepFreeze(["not_invoiced", "invoiced"]);
export const V5_J102_PAYMENT_STATES = deepFreeze(["unpaid", "partially_paid", "paid"]);
export const V5_J102_COMPLETION_STATES = deepFreeze(["open", "complete"]);

export const V5_J102_DEAL_AXES = deepFreeze([
  "deal_state", "execution_state", "diligence_state", "closing_state",
  "commission_agreement_state", "invoice_state", "payment_state", "completion_state",
]);

// `party_id` (owner ruling 2026-09-25, party link): an OPTIONAL declared
// reference from a prospect relationship to the CARR party record. Optional, so
// a relationship with no known party is still a relationship; the SQL writer
// holds a supplied value to an existing public.party row.
const RELATIONSHIP_KEYS = Object.freeze([
  "subject_kind", "subject_id", "relationship_state", "active_engagement_count", "party_id",
]);
const RELATIONSHIP_REQUIRED = Object.freeze([
  "subject_kind", "subject_id", "relationship_state", "active_engagement_count",
]);
const ENGAGEMENT_KEYS = Object.freeze([
  "subject_kind", "subject_id", "relationship_id", "engagement_state",
  "representation_basis", "effective_from", "effective_to",
]);
const ASSIGNMENT_KEYS = Object.freeze([
  "subject_kind", "subject_id", "engagement_id", "assignment_phase",
  "open_negotiation_count", "selected_property_id", "active_lease_draft_target_id",
  "pending_deal_id", "multi_target_exception_ref",
]);
const NEGOTIATION_KEYS = Object.freeze([
  "subject_kind", "subject_id", "assignment_id", "property_id", "negotiation_state",
]);
const DEAL_KEYS = Object.freeze([
  "subject_kind", "subject_id", "assignment_id", "property_id", "instrument_kind",
  "deal_state", "execution_state", "diligence_state", "closing_state",
  "commission_agreement_state", "invoice_state", "payment_state", "completion_state",
  "cancellation_reason", "closing_date",
]);

const SUBJECT_SHAPES = deepFreeze({
  relationship: { keys: RELATIONSHIP_KEYS, required: RELATIONSHIP_REQUIRED },
  // `effective_from` is NOT required, and that is a finding rather than an
  // oversight. F01's document identity carries preparation, delivery, signature,
  // validity and version states; it carries no dated effective window. An
  // engagement whose window is unknown is therefore recorded with a null and
  // reports it, rather than being stamped with the server instant — which would
  // be this module inventing a contract date.
  engagement: {
    keys: ENGAGEMENT_KEYS,
    required: ["subject_kind", "subject_id", "relationship_id", "engagement_state",
      "representation_basis"],
  },
  assignment: {
    keys: ASSIGNMENT_KEYS,
    required: ["subject_kind", "subject_id", "engagement_id", "assignment_phase",
      "open_negotiation_count"],
  },
  property_negotiation: { keys: NEGOTIATION_KEYS, required: NEGOTIATION_KEYS },
  deal: {
    keys: DEAL_KEYS,
    required: ["subject_kind", "subject_id", "assignment_id", "property_id", "instrument_kind",
      "deal_state", "execution_state", "diligence_state", "closing_state",
      "commission_agreement_state", "invoice_state", "payment_state", "completion_state"],
  },
});

const SUBJECT_ENUMS = deepFreeze({
  relationship: { relationship_state: V5_J102_RELATIONSHIP_STATES },
  engagement: {
    engagement_state: V5_J102_ENGAGEMENT_STATES,
    representation_basis: V5_J102_REPRESENTATION_BASES,
  },
  assignment: { assignment_phase: V5_J102_ASSIGNMENT_PHASES },
  property_negotiation: { negotiation_state: V5_J102_NEGOTIATION_STATES },
  deal: {
    instrument_kind: V5_J102_INSTRUMENT_KINDS,
    deal_state: V5_J102_DEAL_STATES,
    execution_state: V5_J102_EXECUTION_STATES,
    diligence_state: V5_J102_DILIGENCE_STATES,
    closing_state: V5_J102_CLOSING_STATES,
    commission_agreement_state: V5_J102_COMMISSION_STATES,
    invoice_state: V5_J102_INVOICE_STATES,
    payment_state: V5_J102_PAYMENT_STATES,
    completion_state: V5_J102_COMPLETION_STATES,
  },
});

const IDENT_FIELDS = deepFreeze([
  "subject_id", "relationship_id", "engagement_id", "assignment_id", "property_id",
  "pending_deal_id", "selected_property_id", "active_lease_draft_target_id",
  "multi_target_exception_ref", "party_id",
]);
const INSTANT_FIELDS = deepFreeze(["effective_from", "effective_to", "closing_date"]);
const COUNT_FIELDS = deepFreeze(["open_negotiation_count", "active_engagement_count"]);

/**
 * Validate one LOADED subject projection into a frozen copy.
 *
 * A COPY, so a caller that mutates the object it handed in cannot reach a
 * decision taken from it afterwards. Every declared key is present on the way
 * out, null where absent, so a transition never has to distinguish "no selected
 * property" from "the field was not projected".
 */
export function assertLifecycleSubject(subject, path = "subject") {
  assertObject(subject, path);
  const kind = subject.subject_kind;
  assertEnum(kind, V5_J102_SUBJECT_KINDS, `${path}.subject_kind`, "unknown_subject_kind");
  const shape = SUBJECT_SHAPES[kind];
  assertClosedKeys(subject, shape.keys, path, { allowAsserted: true });
  assertRequiredKeys(subject, shape.required, path);
  const out = { subject_kind: kind };
  for (const key of shape.keys) {
    if (key === "subject_kind") continue;
    const value = subject[key];
    if (value === undefined || value === null) { out[key] = null; continue; }
    if (SUBJECT_ENUMS[kind][key] !== undefined) {
      out[key] = assertEnum(value, SUBJECT_ENUMS[kind][key], `${path}.${key}`, "unknown_state_value");
    } else if (IDENT_FIELDS.includes(key)) {
      out[key] = assertExternalIdent(value, `${path}.${key}`, { maxLength: 128 });
    } else if (INSTANT_FIELDS.includes(key)) {
      out[key] = nullableInstant(value, `${path}.${key}`);
    } else if (COUNT_FIELDS.includes(key)) {
      out[key] = assertSafeInteger(value, `${path}.${key}`, { min: 0, max: 100000 });
    } else if (key === "cancellation_reason") {
      out[key] = assertSafeText(value, `${path}.${key}`, { maxLength: 1000 });
    } else {
      fail("invalid_shape", `${path}.${key} has no declared validator`, { path: `${path}.${key}` });
    }
  }
  return deepFreeze(out);
}

// ---------------------------------------------------------------------------
// Q072 / Q082 — the evidence vocabulary.
//
// EVERY EVIDENCE KIND NAMES ITS SOURCE, ITS EXACT REQUIRED SHAPE, AND THE
// SUBJECT IT BINDS TO. An F01 document kind additionally names the document
// states that must hold, so "signed" is a fact read off the record layer's own
// document identity rather than a word a caller wrote. A first-party record kind
// names the record kind it needs; a typed approval names the approval kind and
// the approver class.
//
// `binds_subject_kind` IS THE SECOND HALF OF "EVIDENCE-BOUND TRANSITIONS", and
// it is the half that was missing. A record that is authentic, of the right
// kind, in the right state and pinned to the right bytes still says nothing
// about WHICH deal closed, WHICH assignment committed or WHICH client signed.
// Without a binding, one closing settlement closes every deal it is pointed at,
// a commitment recorded for assignment A commits assignment B, and a lease
// executed for one client marks another client's deal executed — all with an
// authorized actor and a valid pin. So every evidence kind declares the subject
// kind it must name, every admitted evidence record carries a SERVER-DERIVED
// `subject_binding`, and the evaluator refuses when that binding is not the
// subject the transition moves. The binding is never a field on the transition
// request: it comes off the stored first-party record's own typed columns, or
// off an independently stored evidence→subject association, and the store and
// the SQL writer re-assert it under the same lock that holds the subject.
//
// `requires_author_class` IS THE THIRD HALF, for the four facts only a partner
// may state. Q082's permitted actors bound the actor PERFORMING the transition;
// they said nothing about who AUTHORED the record it rests on, so a sponsored
// agent could write the closing date, the winning-property commitment or the
// failure reason and a partner could then launder it through by performing the
// transition afterwards. The author's own authorization class is recorded on the
// record when it is written and is checked here.
//
// `assistant_text` and `model_output` ARE NOT SOURCES, and they are not merely
// absent from the list: V5_J102_REFUSED_EVIDENCE_SOURCES names them so a request
// carrying one refuses BY NAME rather than as an unknown value. Q072 settles
// that a model may PROPOSE; nothing it writes is ever the evidence.
// ---------------------------------------------------------------------------

export const V5_J102_EVIDENCE_SOURCES = deepFreeze([
  "f01_document", "f01_corporate_artifact", "first_party_record", "typed_approval",
]);

export const V5_J102_REFUSED_EVIDENCE_SOURCES = deepFreeze([
  "assistant_text", "model_output", "conversation_prose", "prompt",
  "caller_assertion", "salesforce_phase_label", "dashboard", "markdown_render",
]);

export const V5_J102_ACTOR_CLASSES = deepFreeze(["verified_partner", "sponsored_agent"]);

/**
 * WHERE A SUBJECT BINDING MAY COME FROM, and the list is exactly two entries
 * long because there are exactly two durable places one can be stored.
 *
 *   first_party_record         — the typed subject columns on the stored
 *                                first-party business record itself. The record
 *                                names the deal it is about when it is written,
 *                                by an authenticated author, and nothing on the
 *                                transition request can change that afterwards.
 *   stored_evidence_subject_link
 *                              — an independently stored association between one
 *                                exact evidence pin (a document id + version +
 *                                content digest, or an artifact digest) and one
 *                                subject. F01 owns documents and artifacts and
 *                                carries no lifecycle binding on either; this
 *                                slice therefore holds the association in its
 *                                OWN relation rather than patching F01's schema.
 *
 * A CALLER-SUPPLIED BINDING IS NOT ON THIS LIST, and that is the whole point. A
 * transition request naming "this lease belongs to that deal" would be the
 * caller asserting the fact the binding exists to establish.
 */
export const V5_J102_SUBJECT_BINDING_SOURCES = deepFreeze([
  "first_party_record", "stored_evidence_subject_link",
]);

const EVIDENCE_KIND_TABLE = deepFreeze({
  // Q069 / Q077, and the one evidence contract that had to be drawn against what
  // F01 ACTUALLY HOLDS rather than against what would be convenient.
  //
  // "ACTIVE signed ETL" needs three things: signed, current, and in force. The
  // first two are exact document states. The third is `validity_state:
  // "effective"`, which is the record layer's own authenticated statement that
  // the agreement is in force — F01 is the authoritative home for document
  // validity, and re-deriving that here from dates would be a second authority.
  //
  // THE DATED WINDOW IS CHECKED ONLY IF IT IS CARRIED, because F01's document
  // identity does not carry one. Where a window IS present, an agreement that has
  // not yet opened or has already closed refuses; where it is absent, the answer
  // says so rather than pretending the window was verified. Requiring a window
  // F01 cannot supply would make the ordinary ETL path unreachable, and inventing
  // one from the server clock would be worse than either.
  signed_engagement_letter: {
    source: "f01_document",
    binds_subject_kind: "relationship",
    document_states: {
      signature_state: "fully_executed",
      validity_state: "effective",
      version_state: "current",
    },
    checks_effective_window_if_carried: true,
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  // Q077's "another approved representation agreement". The DOCUMENT alone does
  // not settle it: an equivalence needs a typed authenticated approval saying
  // this class of agreement counts. That approval is a fact this slice READS,
  // never one it infers, and never a new business policy it writes.
  approved_representation_equivalent: {
    source: "typed_approval",
    binds_subject_kind: "relationship",
    approval_kind: "representation_equivalence_approval",
    requires_approver_class: "verified_partner",
    permitted_actor_classes: ["verified_partner"],
  },
  search_initiation: {
    source: "first_party_record",
    binds_subject_kind: "assignment",
    record_kind: "assignment_mandate",
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  submitted_loi: {
    source: "f01_document",
    binds_subject_kind: "property_negotiation",
    document_states: { delivery_state: "delivered", version_state: "current" },
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  counterparty_loi_acceptance: {
    source: "f01_corporate_artifact",
    binds_subject_kind: "property_negotiation",
    evidence_class_required: true,
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  // Q078 / Q095. The commitment is a DECISION, so its evidence is the
  // authenticated decision record — not a document, because no counterparty
  // signs anything at the moment a broker commits to one property.
  winner_selection_commitment: {
    source: "first_party_record",
    binds_subject_kind: "assignment",
    record_kind: "winning_property_commitment",
    // H5. The commitment is a partner's decision, so the partner has to be the
    // one who WROTE it. Requiring only that a partner performs the transition
    // would let an agent author the commitment and a partner wave it through.
    requires_author_class: "verified_partner",
    permitted_actor_classes: ["verified_partner"],
  },
  executed_lease: {
    source: "f01_document",
    binds_subject_kind: "deal",
    document_states: {
      signature_state: "fully_executed",
      validity_state: "effective",
      version_state: "current",
    },
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  // Q094. Fully executed on the SIGNATURE axis; the validity axis is
  // deliberately NOT required to be "effective", because a purchase contract
  // that is signed and in diligence is exactly a contract whose validity has not
  // yet run its course.
  signed_purchase_contract: {
    source: "f01_document",
    binds_subject_kind: "deal",
    document_states: { signature_state: "fully_executed", version_state: "current" },
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  diligence_outcome: {
    source: "first_party_record",
    binds_subject_kind: "deal",
    record_kind: "diligence_outcome",
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  // Q094's "final closing day", and the second contract drawn against what the
  // record layer actually holds.
  //
  // WHY THIS IS A FIRST-PARTY RECORD AND NOT A DOCUMENT. No F01 document state
  // says a closing happened on a particular date, and the instrument that would
  // carry one differs by deal: a purchase closes on a settlement statement, a
  // lease commences with no settlement statement at all. Requiring an executed
  // settlement document for both would invent a lease policy nobody settled.
  // What Q094 does settle is that the deal closes on the ACTUAL final closing
  // date, so the evidence is an authenticated, appended, receipted statement of
  // that date by a verified partner — which is a real business act with an
  // attributable author, and is exactly not a boolean on a transition request.
  // A supporting document may be referenced through the record; it is not
  // invented as mandatory here.
  final_closing_settlement: {
    source: "first_party_record",
    // BLOCK-2, at the place it did the most damage: ONE settlement record with
    // ONE date used to close any number of unrelated deals, because nothing tied
    // the record to a deal. It names its deal now, and a settlement bound to a
    // different one refuses however authentic it is.
    binds_subject_kind: "deal",
    record_kind: "closing_settlement",
    requires_closing_date: true,
    requires_author_class: "verified_partner",
    permitted_actor_classes: ["verified_partner"],
  },
  deal_failure_record: {
    source: "first_party_record",
    binds_subject_kind: "deal",
    record_kind: "deal_failure",
    requires_reason: true,
    requires_author_class: "verified_partner",
    permitted_actor_classes: ["verified_partner"],
  },
  commission_agreement: {
    source: "f01_document",
    binds_subject_kind: "deal",
    document_states: { signature_state: "fully_executed", version_state: "current" },
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  invoice_issued: {
    source: "first_party_record",
    binds_subject_kind: "deal",
    record_kind: "invoice",
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  payment_received: {
    source: "first_party_record",
    binds_subject_kind: "deal",
    record_kind: "payment",
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  completion_recorded: {
    source: "first_party_record",
    binds_subject_kind: "deal",
    record_kind: "completion",
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
  },
  // Q082's manual correction and Q072's "correction is receipted". A reason is
  // mandatory here; humanOnly is enforced at the store, where the authenticated
  // principal lives.
  manual_correction: {
    source: "first_party_record",
    // NULL, and it is the only null in this column. A correction is the one
    // record that may be about any subject kind, and no transition in the table
    // consumes it: `record-lifecycle-correction` is a receipt path, and the
    // binding it checks is against the subject named in the correction payload.
    // The record still CARRIES a typed binding — it is stored and re-read like
    // any other — it simply is not narrowed to one kind here.
    binds_subject_kind: null,
    record_kind: "lifecycle_correction",
    requires_reason: true,
    requires_author_class: "verified_partner",
    permitted_actor_classes: ["verified_partner"],
  },
  // Q095's explicit exception. Without one, the single-target constraint binds.
  multi_target_exception_approval: {
    source: "typed_approval",
    binds_subject_kind: "assignment",
    approval_kind: "multi_target_exception",
    requires_approver_class: "verified_partner",
    permitted_actor_classes: ["verified_partner"],
  },
});

/**
 * The record kinds only a verified partner may AUTHOR, derived from the table
 * above rather than restated. The store restricts its fact writer to this exact
 * set, and the SQL relation carries the same list as a CHECK, so an agent-
 * authored closing date cannot exist to be laundered later.
 */
export const V5_J102_PARTNER_AUTHORED_RECORD_KINDS = deepFreeze(
  Object.values(EVIDENCE_KIND_TABLE)
    .filter(c => c.source === "first_party_record" && c.requires_author_class === "verified_partner")
    .map(c => c.record_kind).sort());

export const V5_J102_EVIDENCE_KINDS = deepFreeze(Object.keys(EVIDENCE_KIND_TABLE).sort());

/** Kinds that establish legal execution of an instrument, for migration triage. */
export const V5_J102_EXECUTION_EVIDENCE_KINDS = deepFreeze([
  "executed_lease", "signed_purchase_contract",
]);

export function v5J102EvidenceContract(kind) {
  assertEnum(kind, V5_J102_EVIDENCE_KINDS, "kind", "unknown_evidence_kind");
  return deepFreeze({ evidence_kind: kind, ...EVIDENCE_KIND_TABLE[kind] });
}

const EVIDENCE_KEYS = Object.freeze([
  "evidence_kind", "source", "reference", "document", "artifact", "record",
  "approval", "provenance", "subject_binding",
]);
const EVIDENCE_PROVENANCE_KEYS = Object.freeze([
  "loaded_by", "reader", "loaded_at", "integrity",
]);
// The binding, and every field on it is server-derived. `bound_by` names WHICH
// durable place the association was read from, and `binding_digest` is the
// digest of the row that carries it — the stored record for a first-party fact,
// the stored association for a document or an artifact — so a reader can go and
// check the binding rather than taking the evidence record's word for it.
const SUBJECT_BINDING_KEYS = Object.freeze([
  "subject_kind", "subject_id", "bound_by", "binding_digest",
]);
// `closing_date` is deliberately NOT here. It lives on the first-party record
// shape and nowhere else, so there is one home for the fact and no chance of two
// closing dates disagreeing about the same deal.
const DOCUMENT_EVIDENCE_KEYS = Object.freeze([
  "document_id", "document_class", "version_no", "content_digest",
  "preparation_state", "delivery_state", "signature_state", "validity_state",
  "version_state", "effective_from", "effective_to",
]);
const ARTIFACT_EVIDENCE_KEYS = Object.freeze([
  "artifact_digest", "content_digest", "source_system", "evidence_class", "observed_at",
]);
const RECORD_EVIDENCE_KEYS = Object.freeze([
  "record_kind", "record_id", "content_digest", "recorded_by",
  // H5. The author's own class, stamped on the row by the writer that derived
  // it, not by whoever presents the record later.
  "recorded_by_authorization_class",
  "recorded_at", "reason", "detail", "closing_date", "supporting_document_id",
]);
const APPROVAL_EVIDENCE_KEYS = Object.freeze([
  "approval_kind", "approval_ref", "approver_slug", "approver_authorization_class",
  "approved_at", "scope",
]);

/**
 * THE PROVENANCE GATE. An evidence record is admitted only when the record layer
 * itself says it loaded it: `loaded_by` must be the server loader, the reader
 * must be named, and the integrity note must say the bytes were recomputed
 * rather than trusted. The store cannot be talked into forging this, because it
 * builds every evidence record from a readback and refuses a caller-supplied
 * `evidence` key by name. It is checked HERE as well, so the kernel is safe to
 * call from any future handler rather than only from the store shipped beside it.
 */
export const V5_J102_EVIDENCE_LOADER = "server_record_layer";
export const V5_J102_EVIDENCE_INTEGRITY = "recomputed_from_committed_row";

export function assertLifecycleEvidence(evidence, path = "evidence") {
  assertObject(evidence, path);
  assertClosedKeys(evidence, EVIDENCE_KEYS, path, { allowAsserted: true });
  assertRequiredKeys(evidence, ["evidence_kind", "source", "provenance", "subject_binding"], path);
  const kind = assertEnum(evidence.evidence_kind, V5_J102_EVIDENCE_KINDS,
    `${path}.evidence_kind`, "unknown_evidence_kind");
  const contract = EVIDENCE_KIND_TABLE[kind];

  if (V5_J102_REFUSED_EVIDENCE_SOURCES.includes(evidence.source)) {
    fail("refused_evidence_source",
      `${path}.source "${evidence.source}" is never evidence; a model or a caller may propose, never establish`,
      { path: `${path}.source`, source: evidence.source });
  }
  const source = assertEnum(evidence.source, V5_J102_EVIDENCE_SOURCES,
    `${path}.source`, "unknown_evidence_source");
  if (source !== contract.source) {
    fail("evidence_source_mismatch",
      `${path}.source is "${source}" but ${kind} is established from "${contract.source}"`,
      { path, evidence_kind: kind, expected: contract.source, actual: source });
  }

  const provenance = assertObject(evidence.provenance, `${path}.provenance`);
  assertClosedKeys(provenance, EVIDENCE_PROVENANCE_KEYS, `${path}.provenance`, { allowAsserted: true });
  assertRequiredKeys(provenance, EVIDENCE_PROVENANCE_KEYS, `${path}.provenance`);
  if (provenance.loaded_by !== V5_J102_EVIDENCE_LOADER) {
    fail("evidence_not_server_loaded",
      `${path}.provenance.loaded_by must be "${V5_J102_EVIDENCE_LOADER}"; a caller cannot supply evidence`,
      { path: `${path}.provenance.loaded_by` });
  }
  if (provenance.integrity !== V5_J102_EVIDENCE_INTEGRITY) {
    fail("evidence_integrity_not_recomputed",
      `${path}.provenance.integrity must be "${V5_J102_EVIDENCE_INTEGRITY}"; a trusted readback is not a verified one`,
      { path: `${path}.provenance.integrity` });
  }
  assertExternalIdent(provenance.reader, `${path}.provenance.reader`, { maxLength: 128 });
  assertInstant(provenance.loaded_at, `${path}.provenance.loaded_at`);

  // THE SUBJECT BINDING. Read here rather than in the evaluator, so a caller
  // reaching this function directly — the constraint evaluator, the legacy
  // classifier, a future handler — cannot admit an unbound evidence record at
  // all. WHETHER the binding is the right one is a policy question and is
  // answered in evaluateLifecycleTransition; whether one exists is not.
  const binding = assertObject(evidence.subject_binding, `${path}.subject_binding`);
  assertClosedKeys(binding, SUBJECT_BINDING_KEYS, `${path}.subject_binding`, { allowAsserted: true });
  assertRequiredKeys(binding, SUBJECT_BINDING_KEYS, `${path}.subject_binding`);
  const bound_by = assertEnum(binding.bound_by, V5_J102_SUBJECT_BINDING_SOURCES,
    `${path}.subject_binding.bound_by`, "unknown_subject_binding_source");
  // A first-party record binds through its own typed columns and through nothing
  // else; a document or an artifact binds through the stored association and
  // through nothing else. Crossing the two would let a document claim the
  // standing of a record it is not.
  const expected_bound_by = source === "first_party_record"
    ? "first_party_record" : "stored_evidence_subject_link";
  if (bound_by !== expected_bound_by) {
    fail("subject_binding_source_mismatch",
      `${path}.subject_binding.bound_by is "${bound_by}" but ${source} evidence binds through "${expected_bound_by}"`,
      { path: `${path}.subject_binding.bound_by`, expected: expected_bound_by, actual: bound_by });
  }

  const out = {
    evidence_kind: kind,
    source,
    reference: evidence.reference === undefined || evidence.reference === null
      ? null : assertExternalIdent(evidence.reference, `${path}.reference`, { maxLength: 255 }),
    document: null, artifact: null, record: null, approval: null,
    subject_binding: {
      subject_kind: assertEnum(binding.subject_kind, V5_J102_SUBJECT_KINDS,
        `${path}.subject_binding.subject_kind`, "unknown_subject_kind"),
      subject_id: assertExternalIdent(binding.subject_id, `${path}.subject_binding.subject_id`,
        { maxLength: 128 }),
      bound_by,
      binding_digest: assertDigestRef(binding.binding_digest, `${path}.subject_binding.binding_digest`),
    },
    provenance: {
      loaded_by: provenance.loaded_by, reader: provenance.reader,
      loaded_at: provenance.loaded_at, integrity: provenance.integrity,
    },
  };

  if (source === "f01_document") {
    const doc = assertObject(evidence.document, `${path}.document`);
    assertClosedKeys(doc, DOCUMENT_EVIDENCE_KEYS, `${path}.document`, { allowAsserted: true });
    assertRequiredKeys(doc, ["document_id", "document_class", "version_no", "content_digest",
      "preparation_state", "delivery_state", "signature_state", "validity_state", "version_state"],
      `${path}.document`);
    out.document = {
      document_id: assertExternalIdent(doc.document_id, `${path}.document.document_id`, { maxLength: 128 }),
      document_class: assertExternalIdent(doc.document_class, `${path}.document.document_class`,
        { maxLength: 128 }),
      version_no: assertSafeInteger(doc.version_no, `${path}.document.version_no`, { min: 1 }),
      content_digest: assertDigestRef(doc.content_digest, `${path}.document.content_digest`),
      preparation_state: assertSafeText(doc.preparation_state, `${path}.document.preparation_state`,
        { maxLength: 64 }),
      delivery_state: assertSafeText(doc.delivery_state, `${path}.document.delivery_state`,
        { maxLength: 64 }),
      signature_state: assertSafeText(doc.signature_state, `${path}.document.signature_state`,
        { maxLength: 64 }),
      validity_state: assertSafeText(doc.validity_state, `${path}.document.validity_state`,
        { maxLength: 64 }),
      version_state: assertSafeText(doc.version_state, `${path}.document.version_state`,
        { maxLength: 64 }),
      effective_from: nullableInstant(doc.effective_from, `${path}.document.effective_from`),
      effective_to: nullableInstant(doc.effective_to, `${path}.document.effective_to`),
    };
  } else if (source === "f01_corporate_artifact") {
    const artifact = assertObject(evidence.artifact, `${path}.artifact`);
    assertClosedKeys(artifact, ARTIFACT_EVIDENCE_KEYS, `${path}.artifact`, { allowAsserted: true });
    assertRequiredKeys(artifact, ["artifact_digest", "content_digest", "source_system", "observed_at"],
      `${path}.artifact`);
    if (contract.evidence_class_required === true &&
        (artifact.evidence_class === undefined || artifact.evidence_class === null)) {
      fail("missing_field", `${path}.artifact.evidence_class is required for ${kind}`,
        { path: `${path}.artifact.evidence_class`, evidence_kind: kind });
    }
    out.artifact = {
      artifact_digest: assertDigestRef(artifact.artifact_digest, `${path}.artifact.artifact_digest`),
      content_digest: assertDigestRef(artifact.content_digest, `${path}.artifact.content_digest`),
      source_system: assertExternalIdent(artifact.source_system, `${path}.artifact.source_system`,
        { maxLength: 128 }),
      evidence_class: artifact.evidence_class === undefined || artifact.evidence_class === null
        ? null
        : assertExternalIdent(artifact.evidence_class, `${path}.artifact.evidence_class`, { maxLength: 128 }),
      observed_at: nullableInstant(artifact.observed_at, `${path}.artifact.observed_at`),
    };
  } else if (source === "first_party_record") {
    const record = assertObject(evidence.record, `${path}.record`);
    assertClosedKeys(record, RECORD_EVIDENCE_KEYS, `${path}.record`, { allowAsserted: true });
    assertRequiredKeys(record, ["record_kind", "record_id", "content_digest", "recorded_by",
      "recorded_by_authorization_class", "recorded_at"], `${path}.record`);
    // The binding digest of a first-party record is the record's own digest, and
    // the two are checked against each other here: a binding that pointed at
    // some other row's bytes would be a binding nobody could re-derive.
    if (out.subject_binding.binding_digest !== record.content_digest) {
      fail("subject_binding_digest_mismatch",
        `${path}.subject_binding.binding_digest must be the bound record's own digest`,
        { path: `${path}.subject_binding.binding_digest` });
    }
    const record_kind = assertExternalIdent(record.record_kind, `${path}.record.record_kind`,
      { maxLength: 128 });
    if (record_kind !== contract.record_kind) {
      fail("evidence_record_kind_mismatch",
        `${path}.record.record_kind is "${record_kind}" but ${kind} is established from "${contract.record_kind}"`,
        { path, expected: contract.record_kind, actual: record_kind });
    }
    if (contract.requires_reason === true && (record.reason === undefined || record.reason === null)) {
      fail("missing_field",
        `${path}.record.reason is required for ${kind}; a reason is preserved, never inferred`,
        { path: `${path}.record.reason`, evidence_kind: kind });
    }
    if (contract.requires_closing_date === true &&
        (record.closing_date === undefined || record.closing_date === null)) {
      fail("missing_field",
        `${path}.record.closing_date is required for ${kind}; Q094 closes a deal on the actual date and on nothing else`,
        { path: `${path}.record.closing_date`, evidence_kind: kind });
    }
    out.record = {
      record_kind,
      record_id: assertExternalIdent(record.record_id, `${path}.record.record_id`, { maxLength: 128 }),
      content_digest: assertDigestRef(record.content_digest, `${path}.record.content_digest`),
      recorded_by: assertExternalIdent(record.recorded_by, `${path}.record.recorded_by`, { maxLength: 128 }),
      recorded_by_authorization_class: assertEnum(record.recorded_by_authorization_class,
        V5_J102_ACTOR_CLASSES, `${path}.record.recorded_by_authorization_class`, "unknown_actor_class"),
      recorded_at: nullableInstant(record.recorded_at, `${path}.record.recorded_at`),
      reason: record.reason === undefined || record.reason === null
        ? null : assertSafeText(record.reason, `${path}.record.reason`, { maxLength: 1000 }),
      detail: record.detail === undefined || record.detail === null
        ? null : assertSafeText(record.detail, `${path}.record.detail`, { maxLength: 2000 }),
      closing_date: nullableInstant(record.closing_date, `${path}.record.closing_date`),
      supporting_document_id:
        record.supporting_document_id === undefined || record.supporting_document_id === null
          ? null
          : assertExternalIdent(record.supporting_document_id, `${path}.record.supporting_document_id`,
            { maxLength: 128 }),
    };
  } else {
    const approval = assertObject(evidence.approval, `${path}.approval`);
    assertClosedKeys(approval, APPROVAL_EVIDENCE_KEYS, `${path}.approval`, { allowAsserted: true });
    assertRequiredKeys(approval, ["approval_kind", "approval_ref", "approver_slug",
      "approver_authorization_class", "approved_at"], `${path}.approval`);
    const approval_kind = assertExternalIdent(approval.approval_kind, `${path}.approval.approval_kind`,
      { maxLength: 128 });
    if (approval_kind !== contract.approval_kind) {
      fail("evidence_approval_kind_mismatch",
        `${path}.approval.approval_kind is "${approval_kind}" but ${kind} rests on "${contract.approval_kind}"`,
        { path, expected: contract.approval_kind, actual: approval_kind });
    }
    const approver_class = assertEnum(approval.approver_authorization_class, V5_J102_ACTOR_CLASSES,
      `${path}.approval.approver_authorization_class`, "unknown_actor_class");
    if (approver_class !== contract.requires_approver_class) {
      fail("approval_authority_insufficient",
        `${kind} requires an approval from a ${contract.requires_approver_class}; this one is from a ${approver_class}`,
        { path, evidence_kind: kind, required: contract.requires_approver_class, actual: approver_class });
    }
    out.approval = {
      approval_kind,
      approval_ref: assertExternalIdent(approval.approval_ref, `${path}.approval.approval_ref`,
        { maxLength: 255 }),
      approver_slug: assertExternalIdent(approval.approver_slug, `${path}.approval.approver_slug`,
        { maxLength: 128 }),
      approver_authorization_class: approver_class,
      approved_at: nullableInstant(approval.approved_at, `${path}.approval.approved_at`),
      scope: approval.scope === undefined || approval.scope === null
        ? null : assertSafeText(approval.scope, `${path}.approval.scope`, { maxLength: 500 }),
    };
  }
  return deepFreeze(out);
}

// ---------------------------------------------------------------------------
// Q082 — the transition table.
//
// Every entry declares, in the same five columns the decision names:
//   prerequisites      what must already be true, as ordered per-axis predicates
//   required_evidence  which evidence kinds, all of them, none optional
//   permitted_actors   which authorization classes may perform it
//   coupled_facts      the facts that land TOGETHER or not at all
//   reversibility      what it takes to undo it, honestly
//
// A transition that is not in this table cannot be performed. There is no
// free-form stage update and no "set the phase" verb anywhere in this slice,
// because Q082 removed it.
// ---------------------------------------------------------------------------

export const V5_J102_REVERSIBILITY = deepFreeze([
  "reversible_by_receipted_correction",
  "reversible_by_declared_transition",
  "irreversible_without_new_evidence",
]);

const TRANSITIONS = deepFreeze({
  "establish-client-and-engagement": {
    subject_kind: "relationship",
    decision_refs: ["Q069.D1", "Q072.D1", "Q077.D1", "Q079.D1"],
    // ONE of the two, and exactly one: an ETL and an approved equivalent are
    // different bases, and a request naming both cannot say which basis the
    // engagement rests on.
    evidence_alternatives: [["signed_engagement_letter"], ["approved_representation_equivalent"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { relationship_state: ["prospect"] },
    coupled_facts: ["relationship.relationship_state", "engagement.engagement_state",
      "engagement.representation_basis"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
  "open-assignment": {
    subject_kind: "assignment",
    decision_refs: ["Q072.D1", "Q079.D1", "Q080.D1"],
    evidence_alternatives: [["search_initiation"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    requires_active_engagement: true,
    // H2. THE PREREQUISITE THAT WAS MISSING. Without a `from` clause this
    // transition would take an assignment at `committed` — one holding a pending
    // Deal and a selected property — and write `search` over it on the strength
    // of a mandate record, keeping both fields. That is a rewind, an internally
    // inconsistent row, and a way past the `assignment_already_committed`
    // refusal Q095 rests on. Opening is for an assignment that has not yet
    // started negotiating; a committed or concluded one needs its own decision.
    from: { assignment_phase: ["research", "search"] },
    coupled_facts: ["assignment.assignment_phase"],
    reversibility: "reversible_by_declared_transition",
    creates_deal: false,
  },
  "record-loi-submission": {
    subject_kind: "property_negotiation",
    decision_refs: ["Q072.D1", "Q080.D1", "Q095.D1"],
    evidence_alternatives: [["submitted_loi"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { negotiation_state: ["loi_drafted", "loi_countered"] },
    coupled_facts: ["property_negotiation.negotiation_state", "assignment.assignment_phase"],
    reversibility: "reversible_by_declared_transition",
    creates_deal: false,
  },
  "record-loi-acceptance": {
    subject_kind: "property_negotiation",
    decision_refs: ["Q078.D1", "Q095.D1"],
    evidence_alternatives: [["counterparty_loi_acceptance"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { negotiation_state: ["loi_submitted", "loi_countered"] },
    coupled_facts: ["property_negotiation.negotiation_state"],
    reversibility: "reversible_by_declared_transition",
    creates_deal: false,
  },
  "commit-winning-property": {
    subject_kind: "assignment",
    decision_refs: ["Q069.D1", "Q078.D1", "Q080.D1", "Q095.D1"],
    evidence_alternatives: [["winner_selection_commitment"]],
    permitted_actor_classes: ["verified_partner"],
    from: { assignment_phase: ["search", "negotiation"] },
    coupled_facts: ["property_negotiation.negotiation_state", "assignment.assignment_phase",
      "assignment.selected_property_id", "assignment.pending_deal_id", "deal.deal_state"],
    reversibility: "reversible_by_declared_transition",
    creates_deal: true,
  },
  "record-lease-execution": {
    subject_kind: "deal",
    decision_refs: ["Q072.D1", "Q078.D1", "Q080.D1"],
    evidence_alternatives: [["executed_lease"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { deal_state: ["pending"], execution_state: ["unexecuted"] },
    instrument_kinds: ["lease", "renewal", "amendment"],
    coupled_facts: ["deal.execution_state"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
  "record-purchase-contract-execution": {
    subject_kind: "deal",
    decision_refs: ["Q072.D1", "Q094.D1"],
    evidence_alternatives: [["signed_purchase_contract"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { deal_state: ["pending"], execution_state: ["unexecuted"] },
    instrument_kinds: ["purchase"],
    coupled_facts: ["deal.execution_state", "deal.diligence_state"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
  "record-diligence-outcome": {
    subject_kind: "deal",
    decision_refs: ["Q080.D1", "Q094.D1"],
    evidence_alternatives: [["diligence_outcome"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { deal_state: ["pending"], diligence_state: ["in_progress"] },
    coupled_facts: ["deal.diligence_state"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
  "record-deal-closing": {
    subject_kind: "deal",
    decision_refs: ["Q080.D1", "Q082.D1", "Q094.D1"],
    evidence_alternatives: [["final_closing_settlement"]],
    permitted_actor_classes: ["verified_partner"],
    from: { deal_state: ["pending"], execution_state: ["executed"], closing_state: ["not_reached"] },
    coupled_facts: ["deal.deal_state", "deal.closing_state", "deal.closing_date"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
  "cancel-pending-deal": {
    subject_kind: "deal",
    decision_refs: ["Q096.D1"],
    evidence_alternatives: [["deal_failure_record"]],
    permitted_actor_classes: ["verified_partner"],
    from: { deal_state: ["pending"] },
    coupled_facts: ["deal.deal_state", "deal.cancellation_reason",
      "assignment.assignment_phase", "assignment.selected_property_id",
      "assignment.pending_deal_id"],
    reversibility: "irreversible_without_new_evidence",
    creates_deal: false,
  },
  "record-commission-agreement": {
    subject_kind: "deal",
    decision_refs: ["Q080.D1"],
    evidence_alternatives: [["commission_agreement"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { commission_agreement_state: ["absent"] },
    coupled_facts: ["deal.commission_agreement_state"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
  "record-invoice-issued": {
    subject_kind: "deal",
    decision_refs: ["Q080.D1"],
    evidence_alternatives: [["invoice_issued"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { invoice_state: ["not_invoiced"] },
    coupled_facts: ["deal.invoice_state"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
  "record-payment": {
    subject_kind: "deal",
    decision_refs: ["Q072.D1", "Q080.D1"],
    evidence_alternatives: [["payment_received"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { payment_state: ["unpaid", "partially_paid"] },
    coupled_facts: ["deal.payment_state"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
  "record-completion": {
    subject_kind: "deal",
    decision_refs: ["Q072.D1", "Q080.D1"],
    evidence_alternatives: [["completion_recorded"]],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    from: { completion_state: ["open"] },
    coupled_facts: ["deal.completion_state"],
    reversibility: "reversible_by_receipted_correction",
    creates_deal: false,
  },
});

export const V5_J102_TRANSITION_IDS = deepFreeze(Object.keys(TRANSITIONS).sort());

/** The full declared contract for one transition, for callers and for review. */
export function v5J102TransitionContract(transition_id) {
  assertEnum(transition_id, V5_J102_TRANSITION_IDS, "transition_id", "unknown_transition");
  const t = TRANSITIONS[transition_id];
  return deepFreeze({
    schema_version: V5_J102_TRANSITION_SCHEMA_VERSION,
    transition_id,
    subject_kind: t.subject_kind,
    decision_refs: [...t.decision_refs],
    prerequisites: t.from === undefined ? {} : Object.fromEntries(
      Object.entries(t.from).map(([axis, values]) => [axis, [...values]])),
    instrument_kinds: t.instrument_kinds === undefined ? null : [...t.instrument_kinds],
    requires_active_engagement: t.requires_active_engagement === true,
    required_evidence_alternatives: t.evidence_alternatives.map(set => [...set]),
    permitted_actor_classes: [...t.permitted_actor_classes],
    coupled_facts: [...t.coupled_facts],
    reversibility: t.reversibility,
    creates_deal: t.creates_deal,
    // Said on every contract, because Journey 3 is a separate decision and a
    // reader scanning this table should not have to infer the absence.
    creates_or_activates_tour: false,
    free_form_stage_update_permitted: false,
  });
}

// ---------------------------------------------------------------------------
// Q082 — THE TRANSITION TRUTH TABLE.
//
// The table above DECLARES what must already be true; the evaluator below
// ENFORCES it. This is the third thing, and the one that was missing: the
// COMPLETE enumeration of every value each of the four ordered guards can be
// shown, and what that guard does with it.
//
// WHY IT IS SOURCE AND NOT A FIXTURE. A declaration and an enforcer can disagree
// at any cell nobody thought to write a fixture for, and hand-picked refusal
// fixtures are exactly a list of the cells somebody thought of. Enumerating the
// cells from the vocabularies themselves is the only form of the check that
// grows when a state axis grows, and it is a reviewable artifact rather than a
// property of a test run — a reader can see the whole matrix without executing
// anything.
//
// WHAT A CELL CLAIMS, exactly, because it is the one thing here that can be
// over-read. `guard_admits` says THIS GUARD DOES NOT REFUSE THIS VALUE. It does
// NOT say the transition allows: a transition still needs its declared evidence,
// its loaded related subjects and its declared choices, and every one of those
// is decided after all four guards have passed. The table says this on itself
// rather than only here.
//
// THE ORDER IS PART OF THE TABLE, not decoration. The evaluator returns on the
// FIRST guard that refuses, and the prerequisite guard additionally returns on
// the first unmet AXIS in declaration order. Each prerequisite cell therefore
// carries its axis's position, so a fixture driving that cell knows which axes
// must be held in range for the refusal under test to be the one that fires
// rather than a refusal from an earlier axis wearing the same reason_id.
//
// NOTHING HERE DECIDES ANYTHING. Every value comes from the vocabularies the
// subject shapes already register and from TRANSITIONS itself. A second
// authority for a bound this module already holds would be a defect, so this
// function holds none: it reads, enumerates and freezes.
// ---------------------------------------------------------------------------

export const V5_J102_TRUTH_TABLE_SCHEMA_VERSION =
  "doctorcre-v5-j102-lifecycle-transition-truth-table.v1";

/**
 * The four guards that decide a transition on the SUBJECT AND THE ACTOR ALONE,
 * in the order evaluateLifecycleTransition applies them.
 *
 * These four are separated from everything else the evaluator does because they
 * are the ones a truth table can be complete about: each reads a single value
 * out of a CLOSED vocabulary and compares it against a list the transition
 * declares. The checks that follow — evidence, bindings, loaded related
 * subjects, declared choices — are not enumerable in the same way, and this
 * table says nothing about them rather than pretending to cover them.
 */
export const V5_J102_TRANSITION_GUARDS = deepFreeze([
  {
    guard: "subject_kind",
    position: 0,
    refusal_reason_id: "subject_kind_mismatch",
    declared_by: "subject_kind",
    vocabulary: "V5_J102_SUBJECT_KINDS",
  },
  {
    guard: "actor_class",
    position: 1,
    refusal_reason_id: "actor_class_not_permitted",
    declared_by: "permitted_actor_classes",
    vocabulary: "V5_J102_ACTOR_CLASSES",
  },
  {
    guard: "prerequisite",
    position: 2,
    refusal_reason_id: "prerequisite_not_met",
    declared_by: "prerequisites",
    vocabulary: "the registered states of the axis the entry names",
  },
  {
    guard: "instrument_kind",
    position: 3,
    refusal_reason_id: "instrument_kind_not_permitted",
    declared_by: "instrument_kinds",
    vocabulary: "V5_J102_INSTRUMENT_KINDS",
  },
  {
    // THE ONE GUARD WHOSE CELL IS A QUESTION ABOUT A SET rather than about a
    // value, and its question is narrowed to the only form that enumerates
    // cleanly: does THIS evidence kind, supplied ON ITS OWN and with nothing
    // else, satisfy exactly one declared alternative? A transition whose only
    // alternative names two kinds together is admitted by no single kind, which
    // is the right answer rather than a gap — the table reports the whole
    // vocabulary refusing, and a fixture supplying the pair is the separate
    // thing the `not_enumerated` list still names.
    guard: "required_evidence",
    position: 4,
    refusal_reason_id: "required_evidence_absent",
    declared_by: "required_evidence_alternatives",
    vocabulary: "V5_J102_EVIDENCE_KINDS",
    cell_asks: "does this evidence kind, supplied alone, satisfy exactly one declared alternative",
  },
]);

export const V5_J102_TRUTH_TABLE_GUARD_ORDER =
  deepFreeze(V5_J102_TRANSITION_GUARDS.map(g => g.guard));

const GUARD_REFUSAL_REASONS = Object.freeze(Object.fromEntries(
  V5_J102_TRANSITION_GUARDS.map(g => [g.guard, g.refusal_reason_id])));

function truthTableCells(transition_id) {
  const t = TRANSITIONS[transition_id];
  const cells = [];
  const cell = (guard, axis, value, guard_admits, axis_position) => cells.push({
    transition_id,
    guard,
    axis,
    value,
    guard_admits,
    refusal_reason_id: guard_admits ? null : GUARD_REFUSAL_REASONS[guard],
    // Null wherever the guard reads ONE axis, so a reader is never invited to
    // compare positions that are not in the same ordering.
    axis_position,
  });

  for (const kind of V5_J102_SUBJECT_KINDS) {
    cell("subject_kind", "subject_kind", kind, kind === t.subject_kind, null);
  }
  for (const actor_class of V5_J102_ACTOR_CLASSES) {
    cell("actor_class", "authorization_class", actor_class,
      t.permitted_actor_classes.includes(actor_class), null);
  }
  if (t.from !== undefined) {
    Object.entries(t.from).forEach(([axis, permitted], axis_position) => {
      for (const value of SUBJECT_ENUMS[t.subject_kind][axis]) {
        cell("prerequisite", axis, value, permitted.includes(value), axis_position);
      }
    });
  }
  if (t.instrument_kinds !== undefined) {
    for (const instrument_kind of V5_J102_INSTRUMENT_KINDS) {
      cell("instrument_kind", "instrument_kind", instrument_kind,
        t.instrument_kinds.includes(instrument_kind), null);
    }
  }
  for (const evidence_kind of V5_J102_EVIDENCE_KINDS) {
    // A one-element supply satisfies alternative S exactly when S IS that one
    // element, so "exactly one alternative equals [kind]" is the whole of the
    // question and holds for any alternative structure, including one this table
    // has never seen.
    const satisfies = t.evidence_alternatives
      .filter(set => set.length === 1 && set[0] === evidence_kind).length;
    cell("required_evidence", "evidence_kind", evidence_kind, satisfies === 1, null);
  }
  return cells;
}

/** The whole matrix: every guard of every transition against every value it can see. */
export function v5J102TransitionTruthTable() {
  const cells = V5_J102_TRANSITION_IDS.flatMap(id => truthTableCells(id));
  return deepFreeze({
    schema_version: V5_J102_TRUTH_TABLE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    guard_order: [...V5_J102_TRUTH_TABLE_GUARD_ORDER],
    guards: V5_J102_TRANSITION_GUARDS.map(g => ({ ...g })),
    guard_admits_means:
      "this guard does not refuse this value; the transition still requires its " +
      "declared evidence, its bound subject, its loaded related subjects and its " +
      "declared choices, every one of which is decided after all four guards pass",
    cells,
    cell_count: cells.length,
    admitting_cell_count: cells.filter(c => c.guard_admits).length,
    refusing_cell_count: cells.filter(c => !c.guard_admits).length,
    by_transition: Object.fromEntries(V5_J102_TRANSITION_IDS.map(id => {
      const own = cells.filter(c => c.transition_id === id);
      return [id, {
        subject_kind: TRANSITIONS[id].subject_kind,
        guards_declared: V5_J102_TRUTH_TABLE_GUARD_ORDER
          .filter(g => own.some(c => c.guard === g)),
        cell_count: own.length,
        admitting_cell_count: own.filter(c => c.guard_admits).length,
        refusing_cell_count: own.filter(c => !c.guard_admits).length,
      }];
    })),
    // This table enumerates the four guards and nothing else, and a reader
    // comparing it against the evaluator should see the boundary stated rather
    // than infer it from what is absent.
    not_enumerated: deepFreeze([
      "evidence supplied as a SET: the ambiguous-basis and unexpected-extra branches, and any alternative naming two kinds together",
      "the server-derived subject binding on each evidence record",
      "the document, record, artifact and approval state each evidence kind requires of its own source",
      "the loaded related subjects each transition re-checks",
      "the declared choices (mandate scope, instrument kind, return phase, payment level, diligence result)",
    ]),
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The evaluator.
// ---------------------------------------------------------------------------

const REQUEST_KEYS = Object.freeze([
  "tenant", "transition_id", "subject", "related", "evidence", "actor", "declared", "now",
]);
const RELATED_KEYS = Object.freeze([
  "relationship", "engagement", "assignment", "property_negotiation", "deal",
]);
const ACTOR_KEYS = Object.freeze(["slug", "human", "authorization_class", "derived_by"]);
const DECLARED_KEYS = Object.freeze([
  "mandate_scope", "instrument_kind", "return_phase", "payment_level",
  "diligence_result", "new_subject_id", "new_deal_id",
]);

export const V5_J102_ACTOR_DERIVATIONS = deepFreeze([
  "authenticated_handler_context", "server_established_transaction_context",
]);

function assertActor(actor, path = "actor") {
  assertObject(actor, path);
  assertClosedKeys(actor, ACTOR_KEYS, path, { allowAsserted: true });
  assertRequiredKeys(actor, ACTOR_KEYS, path);
  return deepFreeze({
    slug: assertExternalIdent(actor.slug, `${path}.slug`, { maxLength: 128 }),
    human: assertBoolean(actor.human, `${path}.human`),
    authorization_class: assertEnum(actor.authorization_class, V5_J102_ACTOR_CLASSES,
      `${path}.authorization_class`, "unknown_actor_class"),
    // The actor must say WHERE it came from, and the only two admitted answers
    // are server derivations. A caller cannot name itself into this field with a
    // value that means anything.
    derived_by: assertEnum(actor.derived_by, V5_J102_ACTOR_DERIVATIONS,
      `${path}.derived_by`, "actor_not_server_derived"),
  });
}

function transitionResult(fields) {
  return deepFreeze({
    schema_version: V5_J102_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    ...fields,
    // Said on every answer, allowed or refused. A lifecycle decision is a record
    // and never an act in the world: nothing here signs, sends, files, pays or
    // touches Salesforce, and no Tour is created or activated.
    free_form_stage_update: false,
    model_output_used_as_evidence: false,
    creates_or_activates_tour: false,
    effects: V5_NO_EFFECTS,
  });
}

function refuse(transition_id, reason_id, detail = {}) {
  return transitionResult({
    decision: "refuse", reason_id, transition_id, applied: false,
    proposed_state: null, coupled_facts_committed: [], events: [], ...detail,
  });
}

function lifecycleEvent(kind, subject_kind, subject_id, detail) {
  return {
    schema_version: V5_J102_EVENT_SCHEMA_VERSION,
    event_kind: kind,
    subject_kind,
    subject_id,
    ...detail,
  };
}

function allow(base, { proposed_state, events, coupled_facts, reason_id, extra = {} }) {
  return transitionResult({
    decision: "allow", reason_id, ...base,
    // `applied: false` on an ALLOW is deliberate and is not a slip. This kernel
    // DECIDES; the store APPLIES. A caller reading `allow` holds permission to
    // write, never a record that anything was written.
    applied: false,
    proposed_state: deepFreeze(proposed_state),
    coupled_facts_committed: [...coupled_facts],
    events: deepFreeze(events),
    atomic_or_refuse: true,
    ...extra,
  });
}

/**
 * Evaluate ONE typed lifecycle transition against LOADED state and LOADED
 * evidence.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1.  The request must be readable, closed, tenant-bound and carry a
 *       server-derived actor.
 *   2.  The transition must be registered. There is no default and no free-form
 *       stage update.
 *   3.  The subject must be the kind the transition acts on.
 *   4.  The actor's authorization class must be permitted for the transition.
 *   5.  Every declared prerequisite axis must already hold; the first that does
 *       not refuses by name, naming the axis and what was observed.
 *   6.  The instrument kind, where the transition names one, must match.
 *   7.  Exactly ONE declared evidence alternative must be satisfied, with every
 *       kind in it present and nothing extra riding along.
 *   8.  Each evidence record must satisfy its own contract: the F01 document
 *       states it names, the effective window where required, the closing date
 *       where required, and the actor class the evidence itself demands.
 *   9.  No evidence may be recorded, observed or approved after `now`.
 *   10. The transition's own invariants run: an active engagement for a new
 *       assignment, the single-target constraint for a commitment, the
 *       diligence check for a closing.
 *   11. The coupled facts are computed TOGETHER and returned as one proposed
 *       state with one event list. A caller that applies part of it has broken
 *       the contract; the store applies all of it in one transaction.
 */
export function evaluateLifecycleTransition(request) {
  assertObject(request, "request");
  // BOTH GUARDS ARE ARMED ON THE REQUEST ITSELF. Unlike the loaded subject and
  // evidence shapes, nothing legitimate at this level is named `signed`,
  // `approved` or `executed`, so an unknown key of that shape is refused as the
  // assertion it is rather than as a generic unknown field. The load-time audit
  // proves no key REQUEST_KEYS actually accepts collides with either guard.
  assertClosedKeys(request, REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "transition_id", "subject", "evidence", "actor", "now"],
    "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const transition_id = assertEnum(request.transition_id, V5_J102_TRANSITION_IDS,
    "request.transition_id", "unknown_transition");
  const t = TRANSITIONS[transition_id];
  const actor = assertActor(request.actor, "request.actor");
  const subject = assertLifecycleSubject(request.subject, "request.subject");

  const related = {};
  if (request.related !== undefined && request.related !== null) {
    const rawRelated = assertObject(request.related, "request.related");
    assertClosedKeys(rawRelated, RELATED_KEYS, "request.related", { allowAsserted: true });
    for (const key of RELATED_KEYS) {
      if (rawRelated[key] === undefined || rawRelated[key] === null) continue;
      const projected = assertLifecycleSubject(rawRelated[key], `request.related.${key}`);
      if (projected.subject_kind !== key) {
        fail("related_subject_kind_mismatch", `request.related.${key} is a ${projected.subject_kind}`,
          { path: `request.related.${key}`, expected: key, actual: projected.subject_kind });
      }
      related[key] = projected;
    }
  }

  const declared = {};
  if (request.declared !== undefined && request.declared !== null) {
    const rawDeclared = assertObject(request.declared, "request.declared");
    assertClosedKeys(rawDeclared, DECLARED_KEYS, "request.declared");
    for (const key of DECLARED_KEYS) {
      if (rawDeclared[key] === undefined || rawDeclared[key] === null) continue;
      declared[key] = rawDeclared[key];
    }
  }

  const rawEvidence = assertArray(request.evidence, "request.evidence", { min: 1, max: 8 });
  const evidence = rawEvidence.map((item, i) => assertLifecycleEvidence(item, `request.evidence[${i}]`));
  const byKind = new Map();
  evidence.forEach((item, i) => {
    if (byKind.has(item.evidence_kind)) {
      fail("duplicate_evidence_kind",
        `request.evidence[${i}] repeats ${item.evidence_kind}; two records for one kind is ambiguous evidence`,
        { evidence_kind: item.evidence_kind, first_index: byKind.get(item.evidence_kind) });
    }
    byKind.set(item.evidence_kind, i);
  });

  const base = {
    transition_id,
    subject_kind: subject.subject_kind,
    subject_id: subject.subject_id,
    reversibility: t.reversibility,
    decision_refs: [...t.decision_refs],
    actor_slug: actor.slug,
  };

  if (subject.subject_kind !== t.subject_kind) {
    return refuse(transition_id, "subject_kind_mismatch", { ...base, expected_subject_kind: t.subject_kind });
  }
  if (!t.permitted_actor_classes.includes(actor.authorization_class)) {
    return refuse(transition_id, "actor_class_not_permitted",
      { ...base, permitted_actor_classes: [...t.permitted_actor_classes],
        actor_authorization_class: actor.authorization_class });
  }
  if (t.from !== undefined) {
    for (const [axis, permitted] of Object.entries(t.from)) {
      if (!permitted.includes(subject[axis])) {
        return refuse(transition_id, "prerequisite_not_met",
          { ...base, unmet_axis: axis, observed: subject[axis] ?? null, permitted: [...permitted] });
      }
    }
  }
  if (t.instrument_kinds !== undefined && !t.instrument_kinds.includes(subject.instrument_kind)) {
    return refuse(transition_id, "instrument_kind_not_permitted",
      { ...base, instrument_kind: subject.instrument_kind ?? null,
        permitted_instrument_kinds: [...t.instrument_kinds] });
  }

  const satisfied = t.evidence_alternatives.filter(set => set.every(kind => byKind.has(kind)));
  if (satisfied.length === 0) {
    return refuse(transition_id, "required_evidence_absent",
      { ...base, required_evidence_alternatives: t.evidence_alternatives.map(s => [...s]),
        supplied_evidence_kinds: [...byKind.keys()].sort() });
  }
  if (satisfied.length > 1) {
    return refuse(transition_id, "ambiguous_evidence_basis",
      { ...base, satisfied_alternatives: satisfied.map(s => [...s]) });
  }
  const chosen = satisfied[0];
  const extraKinds = [...byKind.keys()].filter(kind => !chosen.includes(kind));
  if (extraKinds.length > 0) {
    return refuse(transition_id, "unexpected_evidence_supplied",
      { ...base, unexpected_evidence_kinds: extraKinds.sort() });
  }

  for (const kind of chosen) {
    const item = evidence[byKind.get(kind)];
    const contract = EVIDENCE_KIND_TABLE[kind];
    const observed = evidenceInstant(item);
    if (observed !== null && observed > now) {
      return refuse(transition_id, "evidence_observed_after_server_time", { ...base, evidence_kind: kind });
    }
    if (!contract.permitted_actor_classes.includes(actor.authorization_class)) {
      return refuse(transition_id, "actor_class_not_permitted_for_evidence",
        { ...base, evidence_kind: kind,
          permitted_actor_classes: [...contract.permitted_actor_classes] });
    }
    // BLOCK-2. The evidence must be about THIS subject, and the binding it is
    // checked against was read off a stored row rather than off the request.
    // Everything above this line can be satisfied by an authentic record for a
    // DIFFERENT deal, assignment or client.
    const binding = item.subject_binding;
    if (contract.binds_subject_kind !== null && binding.subject_kind !== contract.binds_subject_kind) {
      return refuse(transition_id, "evidence_bound_to_wrong_subject_kind",
        { ...base, evidence_kind: kind, bound_subject_kind: binding.subject_kind,
          required_subject_kind: contract.binds_subject_kind });
    }
    if (binding.subject_kind !== subject.subject_kind || binding.subject_id !== subject.subject_id) {
      return refuse(transition_id, "evidence_not_bound_to_subject",
        { ...base, evidence_kind: kind,
          bound_subject_kind: binding.subject_kind, bound_subject_id: binding.subject_id,
          bound_by: binding.bound_by });
    }
    // H5. WHO AUTHORED the fact, not merely who is presenting it now. A partner
    // performing the transition does not launder an agent-authored closing date,
    // winning-property commitment or failure reason.
    if (contract.requires_author_class !== undefined &&
        item.record !== null &&
        item.record.recorded_by_authorization_class !== contract.requires_author_class) {
      return refuse(transition_id, "evidence_author_class_not_permitted",
        { ...base, evidence_kind: kind,
          required_author_class: contract.requires_author_class,
          evidence_author_class: item.record.recorded_by_authorization_class,
          evidence_author: item.record.recorded_by });
    }
    if (contract.document_states !== undefined) {
      for (const [axis, required] of Object.entries(contract.document_states)) {
        if (item.document[axis] !== required) {
          return refuse(transition_id, "document_state_not_met",
            { ...base, evidence_kind: kind, document_axis: axis,
              required, observed: item.document[axis] });
        }
      }
    }
    if (contract.checks_effective_window_if_carried === true) {
      // Q077's "ACTIVE signed ETL". The activeness that is REQUIRED comes from
      // the document_states clause above: validity_state must be "effective",
      // which is F01's own authenticated statement that the agreement is in
      // force. The dated window is an additional check applied only when the
      // record layer carries one — see the contract note on this evidence kind
      // for why it cannot be mandatory.
      const from = item.document.effective_from;
      const until = item.document.effective_to;
      if (from !== null && Date.parse(from) > now) {
        return refuse(transition_id, "representation_not_yet_effective", { ...base, evidence_kind: kind });
      }
      // A boundary instant that has arrived counts as closed: an agreement whose
      // window has run out is a historical fact about a FORMER client.
      if (until !== null && Date.parse(until) <= now) {
        return refuse(transition_id, "representation_no_longer_active", { ...base, evidence_kind: kind });
      }
    }
    if (contract.requires_closing_date === true) {
      // The absent case is a CONTRACT VIOLATION rather than a refusal, and it is
      // caught in assertLifecycleEvidence: a closing_settlement record with no
      // date is not a closing this module can judge, it is a record the module
      // cannot read. What remains here is the one thing that is a policy
      // question — a date that has not arrived.
      const closing = item.record.closing_date;
      if (Date.parse(closing) > now) {
        return refuse(transition_id, "closing_date_in_the_future",
          { ...base, evidence_kind: kind, closing_date: closing });
      }
    }
  }

  return applyTransition({ transition_id, t, subject, related, declared, evidence, byKind, actor, now, base });
}

function evidenceInstant(item) {
  if (item.record !== null) return Date.parse(item.record.recorded_at);
  if (item.artifact !== null) return Date.parse(item.artifact.observed_at);
  if (item.approval !== null) return Date.parse(item.approval.approved_at);
  // A document's own effective and closing instants are checked by their own
  // contract clauses above; a document carries no single "observed at".
  return null;
}

function applyTransition({ transition_id, t, subject, related, declared, evidence, byKind, actor, now, base }) {
  const ev = kind => evidence[byKind.get(kind)];

  switch (transition_id) {
    case "establish-client-and-engagement": {
      const basis = byKind.has("signed_engagement_letter")
        ? "signed_engagement_letter" : "approved_representation_equivalent";
      const item = ev(basis);
      const engagement_id = declared.new_subject_id;
      if (typeof engagement_id !== "string") {
        return refuse(transition_id, "declared_engagement_id_required", base);
      }
      assertExternalIdent(engagement_id, "request.declared.new_subject_id", { maxLength: 128 });
      // Q069 and Q072 together: the Client status and the active Engagement are
      // ONE fact with two rows. A relationship promoted to client with no
      // engagement beneath it is precisely the flattening Q079 removes.
      const effective_from = basis === "signed_engagement_letter"
        ? item.document.effective_from : item.approval.approved_at;
      const effective_to = basis === "signed_engagement_letter" ? item.document.effective_to : null;
      return allow(base, {
        reason_id: "active_representation_creates_client_and_engagement",
        coupled_facts: [...t.coupled_facts],
        proposed_state: {
          relationship: { ...subject, relationship_state: "client",
            active_engagement_count: subject.active_engagement_count + 1 },
          engagement: {
            subject_kind: "engagement", subject_id: engagement_id,
            relationship_id: subject.subject_id, engagement_state: "active",
            representation_basis: basis, effective_from, effective_to,
          },
        },
        events: [
          lifecycleEvent("client_status_established", "relationship", subject.subject_id,
            { representation_basis: basis, evidence_reference: item.reference }),
          lifecycleEvent("engagement_opened", "engagement", engagement_id,
            { relationship_id: subject.subject_id, representation_basis: basis }),
        ],
        extra: {
          representation_basis: basis,
          // Named on this answer because the pre-signature half of Q077 is a rule
          // about what did NOT happen, and a reader should not have to infer it
          // from the absence of a row.
          pre_signature_work_remains_prospect: true,
          salesforce_opportunity_considered: false,
          // Said plainly rather than left to a null. WHAT ESTABLISHED THE
          // ACTIVENESS is one of three things, and a reader should not have to
          // work out which from the shape of the record: a typed approval, the
          // document's validity state plus a dated window the record layer
          // happened to carry, or — the ordinary case today — the document's
          // validity state alone, because F01 carries no window.
          effective_window_carried: basis === "signed_engagement_letter" && effective_from !== null,
          activeness_established_by: basis === "approved_representation_equivalent"
            ? "typed_representation_equivalence_approval"
            : effective_from === null
              ? "f01_document_validity_state"
              : "f01_document_validity_state_and_dated_window",
        },
      });
    }

    case "open-assignment": {
      const engagement = related.engagement ?? null;
      if (engagement === null) return refuse(transition_id, "engagement_not_loaded", base);
      if (engagement.engagement_state !== "active") {
        return refuse(transition_id, "engagement_not_active",
          { ...base, engagement_state: engagement.engagement_state });
      }
      const relationship = related.relationship ?? null;
      if (relationship === null || relationship.relationship_state !== "client") {
        return refuse(transition_id, "client_status_required",
          { ...base, relationship_state: relationship?.relationship_state ?? null });
      }
      if (engagement.subject_id !== subject.engagement_id) {
        return refuse(transition_id, "assignment_not_under_loaded_engagement",
          { ...base, engagement_id: subject.engagement_id });
      }
      if (relationship.subject_id !== engagement.relationship_id) {
        return refuse(transition_id, "engagement_not_under_loaded_relationship",
          { ...base, relationship_id: engagement.relationship_id });
      }
      // Q072: search initiation maps to research OR search, and the mandate says
      // which. Nothing here guesses; an unstated scope refuses.
      const scope = declared.mandate_scope;
      if (scope !== "research" && scope !== "search") {
        return refuse(transition_id, "mandate_scope_required",
          { ...base, permitted_mandate_scopes: ["research", "search"] });
      }
      // H2, continued. THE PHASE IS NOT THE ONLY THING THAT CAN REWIND. An
      // assignment left at `search` while still carrying a committed deal's
      // fields is exactly the internally inconsistent row this transition must
      // not produce, so the three commitment fields are checked by name rather
      // than trusted to have been cleared with the phase.
      if (subject.pending_deal_id !== null) {
        return refuse(transition_id, "assignment_holds_pending_deal",
          { ...base, pending_deal_id: subject.pending_deal_id });
      }
      if (subject.selected_property_id !== null || subject.active_lease_draft_target_id !== null) {
        return refuse(transition_id, "assignment_holds_committed_target",
          { ...base, selected_property_id: subject.selected_property_id,
            active_lease_draft_target_id: subject.active_lease_draft_target_id });
      }
      // Narrowing an assignment back to research while negotiations are open
      // would say the search had not started on a record that proves it had.
      if (scope === "research" && subject.open_negotiation_count > 0) {
        return refuse(transition_id, "open_negotiations_outlast_research_scope",
          { ...base, open_negotiation_count: subject.open_negotiation_count });
      }
      return allow(base, {
        reason_id: "search_initiation_opens_assignment",
        coupled_facts: [...t.coupled_facts],
        proposed_state: { assignment: { ...subject, assignment_phase: scope } },
        events: [lifecycleEvent("assignment_opened", "assignment", subject.subject_id,
          { engagement_id: engagement.subject_id, assignment_phase: scope,
            evidence_reference: ev("search_initiation").reference })],
        extra: {
          // Q079: one client, many mandates. Opening an assignment never
          // duplicates the client and never closes a sibling assignment.
          duplicates_client: false,
          closes_sibling_assignments: false,
        },
      });
    }

    case "record-loi-submission": {
      const assignment = related.assignment ?? null;
      if (assignment === null) return refuse(transition_id, "assignment_not_loaded", base);
      if (assignment.subject_id !== subject.assignment_id) {
        return refuse(transition_id, "negotiation_not_under_loaded_assignment", base);
      }
      if (assignment.assignment_phase === "committed") {
        // Q095: once the winner is selected the assignment has committed. A new
        // LOI on a fresh property at that point is a different decision and needs
        // its own evidence, not a silent reopen.
        return refuse(transition_id, "assignment_already_committed",
          { ...base, selected_property_id: assignment.selected_property_id });
      }
      if (!["research", "search", "negotiation"].includes(assignment.assignment_phase)) {
        return refuse(transition_id, "assignment_phase_not_open",
          { ...base, assignment_phase: assignment.assignment_phase });
      }
      return allow(base, {
        reason_id: "loi_submission_moves_assignment_to_negotiation",
        coupled_facts: [...t.coupled_facts],
        proposed_state: {
          property_negotiation: { ...subject, negotiation_state: "loi_submitted" },
          assignment: { ...assignment, assignment_phase: "negotiation",
            open_negotiation_count: assignment.open_negotiation_count +
              (subject.negotiation_state === "loi_drafted" ? 1 : 0) },
        },
        events: [lifecycleEvent("loi_submitted", "property_negotiation", subject.subject_id,
          { assignment_id: assignment.subject_id, property_id: subject.property_id,
            evidence_reference: ev("submitted_loi").reference })],
        extra: {
          // The single most important negative in this slice, stated ON the
          // answer rather than left to the absence of a deal row: Q072 and Q078
          // both settle that an LOI submission creates no Deal.
          creates_deal: false,
          concurrent_negotiations_permitted: true,
        },
      });
    }

    case "record-loi-acceptance":
      return allow(base, {
        reason_id: "counterparty_acceptance_recorded_without_deal",
        coupled_facts: [...t.coupled_facts],
        proposed_state: { property_negotiation: { ...subject, negotiation_state: "loi_accepted" } },
        events: [lifecycleEvent("loi_accepted", "property_negotiation", subject.subject_id,
          { property_id: subject.property_id,
            evidence_reference: ev("counterparty_loi_acceptance").reference })],
        extra: {
          // Q078's modified reading: acceptance alone is not commitment, and it
          // is commitment that creates the pending Deal.
          creates_deal: false,
          requires_selection_and_commitment_for_deal: true,
          concurrent_acceptances_permitted: true,
        },
      });

    case "commit-winning-property": {
      const negotiation = related.property_negotiation ?? null;
      if (negotiation === null) return refuse(transition_id, "winning_negotiation_not_loaded", base);
      if (negotiation.assignment_id !== subject.subject_id) {
        return refuse(transition_id, "negotiation_not_under_this_assignment", base);
      }
      if (negotiation.negotiation_state !== "loi_accepted") {
        return refuse(transition_id, "winning_negotiation_not_accepted",
          { ...base, negotiation_state: negotiation.negotiation_state });
      }
      if (subject.pending_deal_id !== null) {
        return refuse(transition_id, "assignment_already_holds_pending_deal",
          { ...base, pending_deal_id: subject.pending_deal_id });
      }
      // Q095, and the reason multi_target_exception_approval is EVIDENCE rather
      // than a flag: a second selected property needs an approval a partner
      // actually granted, and this path deliberately passes none.
      const constraint = evaluateSelectedPropertyConstraint({
        tenant: ORGANIZATION_TENANT_ID,
        assignment: subject,
        candidate_property_id: negotiation.property_id,
        exception_approval: null,
      });
      if (constraint.decision !== "allow") {
        return refuse(transition_id, constraint.reason_id,
          { ...base, selected_property_id: subject.selected_property_id,
            active_lease_draft_target_id: subject.active_lease_draft_target_id });
      }
      const instrument_kind = declared.instrument_kind;
      if (!V5_J102_INSTRUMENT_KINDS.includes(instrument_kind)) {
        return refuse(transition_id, "declared_instrument_kind_required",
          { ...base, permitted_instrument_kinds: [...V5_J102_INSTRUMENT_KINDS] });
      }
      const deal_id = declared.new_deal_id;
      if (typeof deal_id !== "string") return refuse(transition_id, "declared_deal_id_required", base);
      assertExternalIdent(deal_id, "request.declared.new_deal_id", { maxLength: 128 });
      const lease_like = instrument_kind !== "purchase";
      return allow(base, {
        reason_id: "selection_and_commitment_create_pending_deal",
        coupled_facts: [...t.coupled_facts],
        proposed_state: {
          property_negotiation: { ...negotiation, negotiation_state: "selected_winner" },
          assignment: {
            ...subject, assignment_phase: "committed",
            selected_property_id: negotiation.property_id,
            // Q095's "we dont do multiple lease drafts": the lease-draft target
            // is set for a lease-shaped instrument and left null for a purchase,
            // where the constraint has no meaning.
            active_lease_draft_target_id: lease_like ? negotiation.property_id : null,
            pending_deal_id: deal_id,
          },
          deal: {
            subject_kind: "deal", subject_id: deal_id, assignment_id: subject.subject_id,
            property_id: negotiation.property_id, instrument_kind,
            deal_state: "pending", execution_state: "unexecuted",
            diligence_state: "not_applicable", closing_state: "not_reached",
            commission_agreement_state: "absent", invoice_state: "not_invoiced",
            payment_state: "unpaid", completion_state: "open",
            cancellation_reason: null, closing_date: null,
          },
        },
        events: [
          lifecycleEvent("winning_property_selected", "property_negotiation", negotiation.subject_id,
            { property_id: negotiation.property_id }),
          lifecycleEvent("assignment_committed", "assignment", subject.subject_id,
            { selected_property_id: negotiation.property_id, pending_deal_id: deal_id }),
          lifecycleEvent("pending_deal_created", "deal", deal_id,
            { assignment_id: subject.subject_id, property_id: negotiation.property_id, instrument_kind,
              evidence_reference: ev("winner_selection_commitment").reference }),
        ],
        extra: {
          // Q095's "without erasing the alternatives", stated as a property of
          // the answer: this transition names no other negotiation and changes
          // none, and the count says so rather than the prose.
          alternative_negotiations_retained: true,
          alternative_negotiations_modified: 0,
          deal_state: "pending",
          execution_state: "unexecuted",
        },
      });
    }

    case "record-lease-execution":
      return allow(base, {
        reason_id: "lease_execution_marks_executed_lease",
        coupled_facts: [...t.coupled_facts],
        proposed_state: { deal: { ...subject, execution_state: "executed" } },
        events: [lifecycleEvent("lease_executed", "deal", subject.subject_id,
          { evidence_reference: ev("executed_lease").reference })],
        extra: {
          // Q080 keeps closing/commencement on its own axis, so execution says
          // nothing about it. A signed lease is executed and still pending until
          // closing evidence arrives.
          deal_state: subject.deal_state,
          closing_state: subject.closing_state,
          execution_implies_closing: false,
        },
      });

    case "record-purchase-contract-execution":
      return allow(base, {
        reason_id: "purchase_contract_executed_business_pending_through_diligence",
        coupled_facts: [...t.coupled_facts],
        // Q094 exactly, and this is the pair of facts the superseded
        // recommendation could not express: legally executed AND a pending
        // DoctorCRE deal, at the same instant.
        proposed_state: {
          deal: { ...subject, execution_state: "executed", diligence_state: "in_progress" },
        },
        events: [lifecycleEvent("purchase_contract_executed", "deal", subject.subject_id,
          { evidence_reference: ev("signed_purchase_contract").reference })],
        extra: {
          legally_executed: true,
          deal_state: "pending",
          closing_state: subject.closing_state,
          signing_closes_deal: false,
        },
      });

    case "record-diligence-outcome": {
      const result = declared.diligence_result;
      if (!["waived", "satisfied", "failed"].includes(result)) {
        return refuse(transition_id, "declared_diligence_result_required",
          { ...base, permitted: ["waived", "satisfied", "failed"] });
      }
      return allow(base, {
        reason_id: `diligence_${result}`,
        coupled_facts: [...t.coupled_facts],
        proposed_state: { deal: { ...subject, diligence_state: result } },
        events: [lifecycleEvent("diligence_outcome_recorded", "deal", subject.subject_id,
          { diligence_state: result, evidence_reference: ev("diligence_outcome").reference })],
        extra: {
          // A failed diligence does not itself cancel the deal. Cancelling is a
          // separate typed act with its own reason and its own receipt (Q096),
          // and collapsing the two would lose the reason.
          cancels_deal: false,
          deal_state: subject.deal_state,
        },
      });
    }

    case "record-deal-closing": {
      if (["in_progress", "failed"].includes(subject.diligence_state)) {
        return refuse(transition_id, "diligence_not_resolved",
          { ...base, diligence_state: subject.diligence_state });
      }
      const closing_date = ev("final_closing_settlement").record.closing_date;
      // Q082's coupled facts: the business state, the closing axis and the date
      // land together, or none of them does.
      return allow(base, {
        reason_id: "final_closing_evidence_closes_deal",
        coupled_facts: [...t.coupled_facts],
        proposed_state: {
          deal: { ...subject, deal_state: "closed", closing_state: "closed", closing_date },
        },
        events: [lifecycleEvent("deal_closed", "deal", subject.subject_id,
          { closing_date, evidence_reference: ev("final_closing_settlement").reference })],
        extra: {
          closing_date,
          closed_on_execution_evidence: false,
          // The four money and completion axes are untouched here on purpose,
          // and are echoed so the separation is checkable on the answer.
          commission_agreement_state: subject.commission_agreement_state,
          invoice_state: subject.invoice_state,
          payment_state: subject.payment_state,
          completion_state: subject.completion_state,
        },
      });
    }

    case "cancel-pending-deal": {
      const assignment = related.assignment ?? null;
      if (assignment === null) return refuse(transition_id, "assignment_not_loaded", base);
      if (assignment.subject_id !== subject.assignment_id) {
        return refuse(transition_id, "deal_not_under_loaded_assignment", base);
      }
      // Q096: back to search OR negotiation. Which one is a fact about what is
      // still open, not a preference, so "negotiation" refuses when nothing is
      // still being negotiated.
      const return_phase = declared.return_phase;
      if (!["search", "negotiation"].includes(return_phase)) {
        return refuse(transition_id, "declared_return_phase_required",
          { ...base, permitted: ["search", "negotiation"] });
      }
      if (return_phase === "negotiation" && assignment.open_negotiation_count < 1) {
        return refuse(transition_id, "no_open_negotiation_to_return_to",
          { ...base, open_negotiation_count: assignment.open_negotiation_count });
      }
      const reason = ev("deal_failure_record").record.reason;
      // M4. A SUPPLIED CLIENT ROW MUST BE THIS DEAL'S CLIENT.
      //
      // The relationship used to be echoed straight into the proposed state on
      // the strength of having been supplied, so cancelling deal D could rewrite
      // an unrelated client row — byte-identical, but with a new `updated_by` and
      // `updated_at`, which is a false answer to "who last touched this client".
      // Two things change. The chain deal → assignment → engagement →
      // relationship is VERIFIED before the relationship is read at all, and a
      // relationship outside it refuses rather than being ignored. And even a
      // verified relationship is NOT written: Q096 asks that the client survive a
      // failed deal, and the strongest form of surviving is not being touched.
      const relationship = related.relationship ?? null;
      const engagement = related.engagement ?? null;
      let relationship_chain_verified = false;
      if (relationship !== null) {
        if (engagement === null) {
          return refuse(transition_id, "relationship_chain_not_loaded",
            { ...base, missing_related: "engagement" });
        }
        if (engagement.subject_id !== assignment.engagement_id ||
            relationship.subject_id !== engagement.relationship_id) {
          return refuse(transition_id, "relationship_not_in_verified_chain",
            { ...base, assignment_engagement_id: assignment.engagement_id,
              engagement_id: engagement.subject_id,
              engagement_relationship_id: engagement.relationship_id,
              relationship_id: relationship.subject_id });
        }
        relationship_chain_verified = true;
      }
      return allow(base, {
        reason_id: "pending_deal_cancelled_assignment_returned",
        coupled_facts: [...t.coupled_facts],
        proposed_state: {
          deal: { ...subject, deal_state: "cancelled", cancellation_reason: reason },
          assignment: {
            ...assignment, assignment_phase: return_phase,
            selected_property_id: null, active_lease_draft_target_id: null,
            pending_deal_id: null,
          },
        },
        events: [
          lifecycleEvent("pending_deal_cancelled", "deal", subject.subject_id,
            { cancellation_reason: reason, evidence_reference: ev("deal_failure_record").reference }),
          lifecycleEvent("assignment_returned_to_market", "assignment", assignment.subject_id,
            { assignment_phase: return_phase }),
        ],
        extra: {
          cancellation_reason: reason,
          client_relationship_preserved: true,
          // Reported, and reported as NOT WRITTEN, so the answer says plainly
          // that the client row this transition did not touch is the client row
          // it did not touch.
          relationship_state: relationship?.relationship_state ?? null,
          relationship_chain_verified,
          relationship_rewritten: false,
          history_preserved: true,
          deal_row_deleted: false,
          negotiation_history_deleted: false,
        },
      });
    }

    case "record-commission-agreement":
      return axisResult(base, t, subject, "commission_agreement_state", "agreed",
        "commission_agreement_recorded", "commission_agreement_recorded", ev("commission_agreement"));

    case "record-invoice-issued":
      return axisResult(base, t, subject, "invoice_state", "invoiced",
        "invoice_issued", "invoice_issued", ev("invoice_issued"));

    case "record-payment": {
      const level = declared.payment_level;
      if (!["partially_paid", "paid"].includes(level)) {
        return refuse(transition_id, "declared_payment_level_required",
          { ...base, permitted: ["partially_paid", "paid"] });
      }
      if (subject.payment_state === "partially_paid" && level === "partially_paid") {
        return refuse(transition_id, "payment_level_would_not_change_state", base);
      }
      return axisResult(base, t, subject, "payment_state", level,
        `payment_${level}`, "payment_recorded", ev("payment_received"));
    }

    case "record-completion":
      // Q072 and Q080 keep completion ORTHOGONAL. It is deliberately not gated
      // on payment: deriving completion from the money axis is exactly the
      // collapse those decisions removed, and a deal can be operationally
      // complete while an invoice is still outstanding.
      return axisResult(base, t, subject, "completion_state", "complete",
        "completion_recorded", "completion_recorded", ev("completion_recorded"));

    default:
      // Unreachable: transition_id was validated against the registry above.
      return fail("unimplemented_transition", `${transition_id} has no evaluator`, { transition_id });
  }
}

/**
 * One orthogonal deal axis moving by one step, with every other axis echoed
 * unchanged so the answer says plainly what it did NOT touch. Q080's separation
 * is only real if a reader can check it on the result.
 */
function axisResult(base, t, subject, axis, value, reason_id, event_kind, item) {
  return allow(base, {
    reason_id,
    coupled_facts: [...t.coupled_facts],
    proposed_state: { deal: { ...subject, [axis]: value } },
    events: [lifecycleEvent(event_kind, "deal", subject.subject_id,
      { [axis]: value, evidence_reference: item.reference })],
    extra: {
      axis,
      unchanged_axes: V5_J102_DEAL_AXES.filter(a => a !== axis)
        .map(a => ({ axis: a, value: subject[a] })),
      deal_state: subject.deal_state,
    },
  });
}

// ---------------------------------------------------------------------------
// INITIALIZATION — the first row of a chain, and the ONE act in this slice that
// cannot be evidence-bound.
//
// WHY IT IS A SEPARATE EVALUATOR AND NOT A TRANSITION. A transition ADVANCES a
// subject: its `from` axes, its instrument kind and its prior conditions are all
// statements about a committed row. A transition that created its own primary
// subject would have nothing to check them against, and that is exactly the
// bypass the writer refuses by name (`j102_primary_subject_creation_refused`).
// So creation is not folded back into the transition table. It is its own narrow
// admission with its own vocabulary, and NOTHING BELOW WEAKENS A TRANSITION: the
// state an initialization produces is the EARLIEST declared state of its kind, so
// every existing prerequisite still has to be satisfied afterwards by the
// evidence-bound transition that follows.
//
// WHY THERE IS NO EVIDENCE HERE, stated plainly because it is the one place a
// reader should expect some. Every evidence kind in this slice BINDS TO A SUBJECT
// (`binds_subject_kind`), a first-party record carries that binding in its own
// typed columns, and `record-lifecycle-fact` refuses `bound_subject_not_found`
// for a subject that does not exist. An assignment mandate about assignment A
// therefore cannot be written until assignment A exists, and cannot be the
// evidence for creating it. The ordering is a fact about the rail, not a gap that
// was skipped, and the honest consequence is that an initialization rests on:
//
//   * the SERVER-DERIVED actor and its authorization class,
//   * the tenant,
//   * the PARENT CHAIN, loaded and re-checked under the writer's own lock — an
//     assignment only under an ACTIVE engagement held by a relationship that is
//     already a CLIENT (Q077), a negotiation only under an assignment that is
//     still open (Q095),
//   * a state shape that is FIXED, carries no lifecycle claim, and is refused if
//     the caller names any part of it.
//
// WHAT AN INITIALIZATION IS NOT. It is not a Client (a prospect relationship is
// `prospect`, and only a signed effective ETL or an approved equivalent moves
// it), not a Deal (Q078: only selection AND commitment create one), not an
// OPENED Assignment (`open-assignment` on a mandate record is the only way to
// reach `search` and the only producer of an `assignment_opened` event), not an
// LOI (a negotiation is created at `loi_drafted`, and `record-loi-submission`
// still requires the delivered document), and never a Tour.
//
// AND ONE THING IT DOES NOT PREVENT, said here rather than left to be found: a
// created assignment can reach `negotiation` through `record-loi-submission`
// without any mandate record ever being written, because that transition admits
// research, search and negotiation alike. Nothing in the thirteen settles
// whether a mandate must come first; see the note on initialize-assignment.
// ---------------------------------------------------------------------------

export const V5_J102_INITIALIZATION_SCHEMA_VERSION =
  "doctorcre-v5-j102-lifecycle-initialization.v1";

const INITIALIZATIONS = deepFreeze({
  // Q069 / Q077 / Q079. Journey 1 starts at a party we are pursuing, and pursuing
  // somebody is not a claim about the world that any document could evidence.
  // The row it creates says two things and nothing else: this relationship is a
  // PROSPECT, and it holds no engagements.
  "initialize-prospect-relationship": {
    subject_kind: "relationship",
    decision_refs: ["Q069.D1", "Q077.D1", "Q079.D1"],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    parent: null,
    required_context: [],
    declared_identifiers: [],
    optional_declared_identifiers: ["party_id"],
    initial_state: { relationship_state: "prospect", active_engagement_count: 0 },
    event_kind: "relationship_initialized",
    event_detail_fields: ["relationship_state"],
    reason_id: "prospect_relationship_initialized",
  },
  // Q077 / Q079 / Q080. One client may hold several mandates, so an assignment is
  // created UNDER an engagement rather than being the engagement. The client gate
  // is the whole of the admission here: an assignment cannot be started for a
  // prospect, or under an engagement that has expired or been terminated.
  //
  // HOW MUCH OF THAT GATE IS LIVE TODAY, because "cannot be started for a
  // prospect" is doing more work than "expired or terminated" is. The prospect
  // half is a live discriminator: `initialize-prospect-relationship` creates
  // relationships at `prospect` and only the ETL moves them, so an assignment
  // before signature really is refused. The lapsed half is DEFENCE IN DEPTH
  // rather than a live path: no shipped transition writes `expired`,
  // `terminated`, `client_paused` or `client_ended` at all, so those states are
  // reachable only through a receipted correction. The condition is kept because
  // the day a producer lands it must already be enforced — but it is not
  // currently exercised by any door, and the SQL fixture says so by name rather
  // than leaving a reader to assume it is covered.
  //
  // IT IS CREATED AT `research`, THE EARLIEST DECLARED PHASE, and that is a
  // deliberate narrowing rather than a convenience: creating the row at anything
  // later would let a caller reach a phase on no evidence at all.
  //
  // WHAT THAT DOES AND DOES NOT BUY, stated exactly, because the obvious reading
  // is wrong. `open-assignment` is still the ONLY way to reach `search` and the
  // ONLY producer of an `assignment_opened` event, so the mandate record remains
  // load-bearing for both. It is NOT the only way past `research`:
  // `record-loi-submission` admits an assignment in research, search OR
  // negotiation and writes `negotiation`, so a created assignment can reach
  // `negotiation` through an LOI without any mandate record ever being written.
  // A CREATED SHELL AND AN ASSIGNMENT OPENED ON A MANDATE ARE THEREFORE BOTH
  // `research`, and only `established_by_transition` on the row and the event
  // history tell them apart — not the phase axis a consumer reads.
  //
  // WHETHER A MANDATE SHOULD BE REQUIRED BEFORE AN LOI IS AN OWNER QUESTION AND
  // IS NOT ANSWERED HERE. None of the thirteen settles an ordering obligation:
  // Q072.D1 maps search initiation TO research or search and says nothing about
  // what else may reach them. The narrow change if the owner wants it is to drop
  // `research` from initialize-property-negotiation's admitted phases below,
  // which would force open-assignment first — and would also stop a
  // research-scope assignment from ever holding a draft, which is why it is not
  // encoded on a reviewer's or an author's say-so.
  "initialize-assignment": {
    subject_kind: "assignment",
    decision_refs: ["Q072.D1", "Q077.D1", "Q079.D1", "Q080.D1"],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    parent: { kind: "engagement", field: "engagement_id" },
    required_context: [
      {
        subject: "engagement",
        conditions: [{ field: "engagement_state", equals: "active" }],
        absent_reason_id: "engagement_not_loaded",
        unmet_reason_id: "engagement_not_active",
      },
      {
        subject: "relationship",
        chained_from: { subject: "engagement", field: "relationship_id" },
        conditions: [{ field: "relationship_state", equals: "client" }],
        absent_reason_id: "relationship_not_loaded",
        unmet_reason_id: "client_status_required",
        chain_reason_id: "relationship_not_in_verified_chain",
      },
    ],
    declared_identifiers: [],
    optional_declared_identifiers: [],
    initial_state: {
      assignment_phase: "research", open_negotiation_count: 0,
      selected_property_id: null, active_lease_draft_target_id: null,
      pending_deal_id: null, multi_target_exception_ref: null,
    },
    event_kind: "assignment_initialized",
    event_detail_fields: ["engagement_id", "assignment_phase"],
    reason_id: "assignment_initialized_under_active_engagement",
  },
  // Q095. "we always submit multiple LOIs if we can" — so a negotiation is a
  // child of the assignment and several may exist at once. It is created at
  // `loi_drafted`, which is what `record-loi-submission` requires and is the one
  // negotiation state that asserts nothing about a counterparty.
  //
  // THE ASSIGNMENT MUST STILL BE OPEN. Once the winner is selected the assignment
  // is `committed`, and the kernel already refuses a fresh LOI there
  // (`assignment_already_committed`). Creating a draft under a committed or
  // concluded assignment would be creating a row that could never be submitted,
  // so the same bound is applied at creation — transcribed from that refusal
  // rather than invented beside it.
  "initialize-property-negotiation": {
    subject_kind: "property_negotiation",
    decision_refs: ["Q080.D1", "Q095.D1"],
    permitted_actor_classes: ["verified_partner", "sponsored_agent"],
    parent: { kind: "assignment", field: "assignment_id" },
    required_context: [
      {
        subject: "assignment",
        conditions: [{ field: "assignment_phase", in: ["research", "search", "negotiation"] }],
        absent_reason_id: "assignment_not_loaded",
        unmet_reason_id: "assignment_phase_not_open",
        // Q095's own words for the case a reader will meet most often.
        unmet_reason_overrides: [
          { field: "assignment_phase", observed: "committed",
            reason_id: "assignment_already_committed" },
        ],
      },
    ],
    declared_identifiers: ["property_id"],
    optional_declared_identifiers: [],
    // Owner ruling 2026-09-25: at most ONE ACTIVE negotiation per (tenant,
    // assignment, property). The store supplies the live siblings it loaded; the
    // SQL writer re-checks under its own transaction and a partial unique index
    // is the structural backstop.
    unique_active_per_property: true,
    initial_state: { negotiation_state: "loi_drafted" },
    event_kind: "property_negotiation_initialized",
    event_detail_fields: ["assignment_id", "property_id", "negotiation_state"],
    reason_id: "property_negotiation_initialized_under_assignment",
  },
});

export const V5_J102_INITIALIZATION_IDS = deepFreeze(Object.keys(INITIALIZATIONS).sort());

/**
 * The negotiation states that count as ACTIVE for the one-per-property rule.
 * A dead negotiation (rejected, withdrawn, superseded) does not block a new one,
 * and neither does `selected_winner`: it has become a Deal, and while that Deal
 * is pending the assignment is `committed`, which already refuses a new draft;
 * once a cancellation reopens the assignment, the property may be pursued again.
 */
export const V5_J102_ACTIVE_NEGOTIATION_STATES = deepFreeze(["loi_drafted", "loi_submitted", "loi_countered", "loi_accepted"]);

/** Which initialization creates each subject kind, or null for the coupled ones. */
export const V5_J102_INITIALIZED_SUBJECT_KINDS = deepFreeze(
  V5_J102_INITIALIZATION_IDS.map(id => INITIALIZATIONS[id].subject_kind).sort());

/** The full declared contract for one initialization, for callers and for review. */
export function v5J102InitializationContract(initialization_id) {
  assertEnum(initialization_id, V5_J102_INITIALIZATION_IDS,
    "initialization_id", "unknown_initialization");
  const c = INITIALIZATIONS[initialization_id];
  return deepFreeze({
    schema_version: V5_J102_INITIALIZATION_SCHEMA_VERSION,
    initialization_id,
    subject_kind: c.subject_kind,
    decision_refs: [...c.decision_refs],
    permitted_actor_classes: [...c.permitted_actor_classes],
    parent_subject_kind: c.parent === null ? null : c.parent.kind,
    parent_reference_field: c.parent === null ? null : c.parent.field,
    declared_identifiers: [...c.declared_identifiers],
    optional_declared_identifiers: [...c.optional_declared_identifiers],
    unique_active_per_property: c.unique_active_per_property === true,
    required_context: c.required_context.map(rule => ({
      subject: rule.subject,
      chained_from: rule.chained_from === undefined ? null : { ...rule.chained_from },
      conditions: rule.conditions.map(condition => ({ ...condition })),
    })),
    initial_state: { ...c.initial_state },
    event_kind: c.event_kind,
    event_detail_fields: [...c.event_detail_fields],
    reason_id: c.reason_id,
    // The five negatives that keep an initialization from being read as a
    // transition, and the one that keeps it from being read as a bypass.
    requires_evidence: false,
    advances_lifecycle_state: false,
    establishes_client_status: false,
    creates_deal: false,
    creates_or_activates_tour: false,
    transition_prerequisites_bypassed: false,
  });
}

const INITIALIZATION_REQUEST_KEYS = Object.freeze([
  "tenant", "initialization_id", "related", "declared", "actor", "now",
  // The ids of the ACTIVE negotiations the store loaded for the same assignment
  // and property. Required (fail closed) for a property negotiation, refused for
  // anything else.
  "active_negotiations_for_property",
]);
// `new_subject_id` names the row being created; `property_id` is the one further
// identifier a negotiation needs and nothing can derive. THERE IS NO EVIDENCE KEY
// AND NO STATE KEY: a request naming one is an unknown field, and a request
// naming a phase, a state or an approval is refused by the two guards before that.
const INITIALIZATION_DECLARED_KEYS = Object.freeze(["new_subject_id", "property_id", "party_id"]);

function initializationResult(fields) {
  return deepFreeze({
    schema_version: V5_J102_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    ...fields,
    // An initialization creates the FIRST state of a subject; it advances none,
    // and every prerequisite of every transition remains to be satisfied.
    advances_lifecycle_state: false,
    transition_prerequisites_bypassed: false,
    free_form_stage_update: false,
    model_output_used_as_evidence: false,
    creates_or_activates_tour: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Evaluate ONE typed initialization against LOADED parent state.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. The request must be readable, closed, tenant-bound and carry a
 *      server-derived actor. There is no evidence parameter to supply.
 *   2. The initialization must be registered. There is no default.
 *   3. The actor's authorization class must be permitted.
 *   4. The declared identifiers must be present and well formed. They are
 *      IDENTIFIERS — which row this is, and which property it concerns — never
 *      facts about any of them.
 *   5. The parent must be LOADED, must be the parent this initialization runs
 *      under, and every declared context condition must hold of it. The chained
 *      context (an engagement's relationship) is verified rather than assumed.
 *   6. The created state is assembled from the FIXED initial state plus the
 *      parent reference plus the declared identifiers, and is then validated as a
 *      subject of its kind — so a shape that is not a legal subject cannot be
 *      proposed, and every declared key is present.
 */
export function evaluateLifecycleInitialization(request) {
  assertObject(request, "request");
  assertClosedKeys(request, INITIALIZATION_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "initialization_id", "declared", "actor", "now"],
    "request");
  assertTenant(request.tenant, "request.tenant");
  assertInstant(request.now, "request.now");
  const initialization_id = assertEnum(request.initialization_id, V5_J102_INITIALIZATION_IDS,
    "request.initialization_id", "unknown_initialization");
  const c = INITIALIZATIONS[initialization_id];
  const actor = assertActor(request.actor, "request.actor");

  const rawDeclared = assertObject(request.declared, "request.declared");
  assertClosedKeys(rawDeclared, INITIALIZATION_DECLARED_KEYS, "request.declared");
  const declared = {};
  for (const key of INITIALIZATION_DECLARED_KEYS) {
    if (rawDeclared[key] === undefined || rawDeclared[key] === null) continue;
    declared[key] = rawDeclared[key];
  }

  const related = {};
  if (request.related !== undefined && request.related !== null) {
    const rawRelated = assertObject(request.related, "request.related");
    assertClosedKeys(rawRelated, RELATED_KEYS, "request.related", { allowAsserted: true });
    for (const key of RELATED_KEYS) {
      if (rawRelated[key] === undefined || rawRelated[key] === null) continue;
      const projected = assertLifecycleSubject(rawRelated[key], `request.related.${key}`);
      if (projected.subject_kind !== key) {
        fail("related_subject_kind_mismatch", `request.related.${key} is a ${projected.subject_kind}`,
          { path: `request.related.${key}`, expected: key, actual: projected.subject_kind });
      }
      related[key] = projected;
    }
  }

  const base = {
    initialization_id,
    subject_kind: c.subject_kind,
    decision_refs: [...c.decision_refs],
    actor_slug: actor.slug,
    requires_evidence: false,
    evidence_supplied: 0,
  };
  const refuseInit = (reason_id, detail = {}) => initializationResult({
    decision: "refuse", reason_id, ...base, subject_id: null, applied: false,
    created_state: null, proposed_state: null, events: [], ...detail,
  });

  if (!c.permitted_actor_classes.includes(actor.authorization_class)) {
    return refuseInit("actor_class_not_permitted", {
      permitted_actor_classes: [...c.permitted_actor_classes],
      actor_authorization_class: actor.authorization_class,
    });
  }

  const subject_id = declared.new_subject_id;
  if (typeof subject_id !== "string") {
    return refuseInit("declared_subject_id_required", {});
  }
  assertExternalIdent(subject_id, "request.declared.new_subject_id", { maxLength: 128 });
  for (const field of c.declared_identifiers) {
    if (typeof declared[field] !== "string") {
      return refuseInit("declared_identifier_required", { missing_declared_identifier: field });
    }
    assertExternalIdent(declared[field], `request.declared.${field}`, { maxLength: 128 });
  }
  // An identifier the caller supplied that this initialization does not use is an
  // identifier nothing would read. It refuses rather than being dropped in
  // silence, because a caller that named a property on a relationship has
  // misunderstood which row it is creating.
  for (const field of c.optional_declared_identifiers) {
    if (declared[field] === undefined) continue;
    if (typeof declared[field] !== "string") {
      return refuseInit("declared_identifier_malformed", { malformed_declared_identifier: field });
    }
    assertExternalIdent(declared[field], `request.declared.${field}`, { maxLength: 128 });
  }
  for (const field of INITIALIZATION_DECLARED_KEYS) {
    if (field === "new_subject_id" || declared[field] === undefined) continue;
    if (!c.declared_identifiers.includes(field) && !c.optional_declared_identifiers.includes(field)) {
      return refuseInit("declared_identifier_not_used", { unexpected_declared_identifier: field });
    }
  }

  // THE PARENT CHAIN. Every context subject is READ from the loaded set, and the
  // one that identifies the next is verified rather than trusted.
  const context = {};
  for (const rule of c.required_context) {
    const loaded = related[rule.subject] ?? null;
    if (loaded === null) return refuseInit(rule.absent_reason_id, {});
    // The FIRST context subject is the parent this row is created under and is
    // read as loaded; a LATER one is reached through a field on the one before
    // it, and that link is verified rather than assumed.
    if (rule.chained_from !== undefined) {
      const anchor = context[rule.chained_from.subject] ?? null;
      if (anchor === null) return refuseInit(rule.absent_reason_id, {});
      if (anchor[rule.chained_from.field] !== loaded.subject_id) {
        return refuseInit(rule.chain_reason_id, {
          expected_id: anchor[rule.chained_from.field] ?? null,
          loaded_id: loaded.subject_id,
        });
      }
    }
    for (const condition of rule.conditions) {
      const observed = loaded[condition.field] ?? null;
      const met = condition.equals !== undefined
        ? observed === condition.equals
        : condition.in.includes(observed);
      if (!met) {
        const override = (rule.unmet_reason_overrides ?? [])
          .find(o => o.field === condition.field && o.observed === observed);
        return refuseInit(override === undefined ? rule.unmet_reason_id : override.reason_id, {
          unmet_field: condition.field,
          observed,
          permitted: condition.equals !== undefined ? [condition.equals] : [...condition.in],
        });
      }
    }
    context[rule.subject] = loaded;
  }

  const parent = c.parent === null ? null : context[c.parent.kind];
  const created_state = assertLifecycleSubject({
    subject_kind: c.subject_kind,
    subject_id,
    ...(c.parent === null ? {} : { [c.parent.field]: parent.subject_id }),
    ...Object.fromEntries(c.declared_identifiers.map(field => [field, declared[field]])),
    ...Object.fromEntries(c.optional_declared_identifiers.map(field => [field, declared[field] ?? null])),
    ...c.initial_state,
  }, "created_state");

  // ONE ACTIVE NEGOTIATION PER PROPERTY (owner ruling 2026-09-25). Checked after
  // the parent chain, so the refusal is about THIS assignment and property.
  if (c.unique_active_per_property === true) {
    const siblings = request.active_negotiations_for_property;
    if (!Array.isArray(siblings)) {
      return refuseInit("active_negotiation_census_absent", {
        why: "the store did not say which negotiations are already active for this assignment and property, so one-per-property cannot be judged",
      });
    }
    siblings.forEach((id, i) => assertExternalIdent(id,
      `request.active_negotiations_for_property[${i}]`, { maxLength: 128 }));
    if (siblings.length > 0) {
      return refuseInit("active_negotiation_exists_for_property", {
        assignment_id: parent.subject_id, property_id: declared.property_id,
        active_negotiation_ids: [...siblings].sort(),
        active_negotiation_states: [...V5_J102_ACTIVE_NEGOTIATION_STATES],
      });
    }
  } else if (request.active_negotiations_for_property !== undefined) {
    fail("unknown_field",
      "request.active_negotiations_for_property is read only for a property negotiation",
      { path: "request.active_negotiations_for_property" });
  }

  const detail = {};
  for (const field of c.event_detail_fields) {
    detail[field] = created_state[field];
  }

  return initializationResult({
    decision: "allow",
    reason_id: c.reason_id,
    ...base,
    subject_id,
    // `applied: false` for the same reason a transition's allow says so: this
    // kernel DECIDES and the store APPLIES.
    applied: false,
    created_state,
    proposed_state: deepFreeze({ [c.subject_kind]: created_state }),
    events: deepFreeze([
      lifecycleEvent(c.event_kind, c.subject_kind, subject_id, detail),
    ]),
    atomic_or_refuse: true,
    parent_subject_kind: c.parent === null ? null : c.parent.kind,
    parent_subject_id: parent === null ? null : parent.subject_id,
    context_verified: Object.keys(context).sort(),
    // The four negatives a reader of this answer needs, stated rather than
    // inferred from the absence of a row.
    establishes_client_status: false,
    creates_deal: false,
    opens_assignment: false,
    submits_loi: false,
  });
}

// ---------------------------------------------------------------------------
// Q095 — the single-target constraint, as its own evaluator.
//
// Multiple LOIs and multiple acceptances are UNCONSTRAINED: Joe's correction is
// that submitting several is normal practice, not an exception to tolerate. What
// is constrained is what comes after the choice — one selected winning property,
// one active lease-draft target — and only an explicit approved exception moves
// it.
// ---------------------------------------------------------------------------

const CONSTRAINT_KEYS = Object.freeze([
  "tenant", "assignment", "candidate_property_id", "exception_approval",
]);

export function evaluateSelectedPropertyConstraint(request) {
  assertObject(request, "request");
  assertClosedKeys(request, CONSTRAINT_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, ["tenant", "assignment", "candidate_property_id"], "request");
  assertTenant(request.tenant, "request.tenant");
  const assignment = assertLifecycleSubject(request.assignment, "request.assignment");
  if (assignment.subject_kind !== "assignment") {
    fail("invalid_shape", "request.assignment must be an assignment subject",
      { path: "request.assignment" });
  }
  const candidate = assertExternalIdent(request.candidate_property_id,
    "request.candidate_property_id", { maxLength: 128 });
  const approval = request.exception_approval === undefined || request.exception_approval === null
    ? null : assertLifecycleEvidence(request.exception_approval, "request.exception_approval");
  if (approval !== null && approval.evidence_kind !== "multi_target_exception_approval") {
    fail("evidence_kind_mismatch",
      "request.exception_approval must be a multi_target_exception_approval",
      { path: "request.exception_approval", actual: approval.evidence_kind });
  }

  const base = {
    schema_version: V5_J102_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    assignment_id: assignment.subject_id,
    candidate_property_id: candidate,
    selected_property_id: assignment.selected_property_id,
    active_lease_draft_target_id: assignment.active_lease_draft_target_id,
    // Restated on every answer of this evaluator, because the constraint is
    // narrow and reads easily as a cap on LOIs, which is the opposite of what
    // Joe settled.
    concurrent_lois_permitted: true,
    concurrent_acceptances_permitted: true,
    effects: V5_NO_EFFECTS,
  };

  const conflicts = [];
  if (assignment.selected_property_id !== null && assignment.selected_property_id !== candidate) {
    conflicts.push("selected_property_id");
  }
  if (assignment.active_lease_draft_target_id !== null &&
      assignment.active_lease_draft_target_id !== candidate) {
    conflicts.push("active_lease_draft_target_id");
  }
  if (conflicts.length === 0) {
    return deepFreeze({ decision: "allow", reason_id: "single_target_available", ...base,
      exception_used: false, conflicts: [] });
  }
  if (approval === null) {
    return deepFreeze({
      decision: "refuse",
      reason_id: conflicts.includes("selected_property_id")
        ? "winning_property_already_selected" : "lease_draft_target_already_active",
      ...base, exception_used: false, conflicts,
      // A stored exception REFERENCE on the assignment row is not an approval:
      // the approval itself has to be loaded and presented. Otherwise the
      // constraint could be lifted by writing a string into a column.
      stored_exception_ref_is_not_an_approval: true,
    });
  }
  return deepFreeze({
    decision: "allow", reason_id: "explicit_approved_exception", ...base,
    exception_used: true, conflicts,
    exception_approval_ref: approval.approval.approval_ref,
    exception_approved_by: approval.approval.approver_slug,
  });
}

// ---------------------------------------------------------------------------
// Q072 — the model seam. Models PROPOSE; deterministic commands decide.
// ---------------------------------------------------------------------------

export const V5_J102_MODEL_PROPOSAL_SEAMS = deepFreeze({
  inbound_lifecycle_document_kind: [
    "engagement_letter", "letter_of_intent", "lease", "purchase_contract",
    "closing_statement", "commission_agreement", "invoice", "unclassified",
  ],
  suggested_transition: [...V5_J102_TRANSITION_IDS],
  counterparty_response_hint: ["accepted", "countered", "rejected", "unknown"],
});

const MODEL_WIDENING_FRAGMENTS = deepFreeze([
  "authority", "authorization", "grant", "delegation", "privilege", "override",
  "actor", "tenant", "partner", "apply", "commit", "write", "state", "phase",
  "evidence", "approved", "signed", "executed", "closed",
]);

const MODEL_PROPOSAL_KEYS = Object.freeze(["seam", "label", "confidence", "evidence_ref"]);

/**
 * Admit one model classification as a PROPOSAL and nothing more.
 *
 * The widening check runs BEFORE the closed-key check so the refusal names what
 * was actually attempted rather than reporting a generic unknown field. Every
 * answer, allowed or refused, states that the proposal advances no state: a
 * model may say "this looks like an executed lease", and the executed-lease
 * transition still requires the F01 document.
 */
export function applyLifecycleModelProposal(proposal) {
  assertObject(proposal, "proposal");
  const base = {
    schema_version: V5_J102_SCHEMA_VERSION,
    seam: typeof proposal.seam === "string" ? proposal.seam : null,
    advances_state: false,
    is_evidence: false,
    requires_deterministic_validation: true,
    widens_authority: false,
    effects: V5_NO_EFFECTS,
  };
  for (const key of Object.keys(proposal)) {
    if (MODEL_PROPOSAL_KEYS.includes(key)) continue;
    const normalized = key.toLowerCase();
    if (MODEL_WIDENING_FRAGMENTS.some(fragment => normalized.includes(fragment))) {
      return deepFreeze({ decision: "refuse", reason_id: "model_widening_refused",
        ...base, offending_field: key });
    }
  }
  assertClosedKeys(proposal, MODEL_PROPOSAL_KEYS, "proposal", { allowAsserted: true });
  assertRequiredKeys(proposal, ["seam", "label"], "proposal");
  if (!Object.prototype.hasOwnProperty.call(V5_J102_MODEL_PROPOSAL_SEAMS, proposal.seam)) {
    fail("unknown_model_seam", `"${proposal.seam}" is not a registered lifecycle model seam`,
      { seam: proposal.seam, registered: Object.keys(V5_J102_MODEL_PROPOSAL_SEAMS) });
  }
  if ("confidence" in proposal && proposal.confidence !== undefined &&
      (!Number.isFinite(proposal.confidence) || proposal.confidence < 0 || proposal.confidence > 1)) {
    fail("invalid_shape", "proposal.confidence must be a number between 0 and 1",
      { path: "proposal.confidence" });
  }
  const labels = V5_J102_MODEL_PROPOSAL_SEAMS[proposal.seam];
  if (typeof proposal.label !== "string" || !labels.includes(proposal.label)) {
    return deepFreeze({ decision: "refuse", reason_id: "model_label_outside_seam", ...base,
      label: typeof proposal.label === "string" ? proposal.label : null,
      registered_labels: [...labels] });
  }
  return deepFreeze({ decision: "allow", reason_id: "model_proposal_within_seam", ...base,
    label: proposal.label, confidence: proposal.confidence ?? null,
    evidence_ref: proposal.evidence_ref ?? null });
}

// ---------------------------------------------------------------------------
// Q083 — the Salesforce reference.
//
// EXTERNAL, and progressively linked. The opportunity keeps its own name and its
// own phase, and neither ever becomes a DoctorCRE state: this function refuses a
// request that names a lifecycle axis at all, so the mapping cannot be performed
// by accident through this door.
// ---------------------------------------------------------------------------

export const V5_J102_SALESFORCE_LINK_TARGETS = deepFreeze([
  "relationship", "engagement", "assignment", "deal",
]);

const SALESFORCE_KEYS = Object.freeze([
  "tenant", "opportunity_id", "opportunity_name", "opportunity_phase",
  "linked_subject_kind", "linked_subject_id", "observed_at",
]);

const SALESFORCE_FORBIDDEN_FRAGMENTS = deepFreeze([
  "relationship_state", "engagement_state", "assignment_phase", "negotiation_state",
  "deal_state", "execution_state", "diligence_state", "closing_state",
  "lifecycle", "maps_to", "derived_state",
]);

export function projectSalesforceReference(request) {
  assertObject(request, "request");
  for (const key of Object.keys(request)) {
    if (SALESFORCE_KEYS.includes(key)) continue;
    const normalized = key.toLowerCase();
    if (SALESFORCE_FORBIDDEN_FRAGMENTS.some(fragment => normalized.includes(fragment))) {
      fail("salesforce_label_mapping_refused",
        `request.${key} would map a Salesforce label onto DoctorCRE lifecycle state; the two models stay apart`,
        { path: `request.${key}`, key });
    }
  }
  assertClosedKeys(request, SALESFORCE_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, ["tenant", "opportunity_id", "opportunity_name",
    "opportunity_phase", "observed_at"], "request");
  assertTenant(request.tenant, "request.tenant");
  const linked_subject_kind =
    request.linked_subject_kind === undefined || request.linked_subject_kind === null
      ? null
      : assertEnum(request.linked_subject_kind, V5_J102_SALESFORCE_LINK_TARGETS,
        "request.linked_subject_kind", "unknown_salesforce_link_target");
  if (linked_subject_kind !== null &&
      (request.linked_subject_id === undefined || request.linked_subject_id === null)) {
    fail("missing_field", "request.linked_subject_id is required once a link target is named",
      { path: "request.linked_subject_id" });
  }
  return deepFreeze({
    schema_version: V5_J102_SALESFORCE_REFERENCE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision: "allow",
    reason_id: linked_subject_kind === null
      ? "external_reference_recorded_unlinked" : "external_reference_progressively_linked",
    opportunity_id: assertExternalIdent(request.opportunity_id, "request.opportunity_id",
      { maxLength: 128 }),
    // Preserved verbatim, both of them. Q083 keeps the Salesforce name and phase
    // as SALESFORCE's own facts about its own record.
    opportunity_name: assertSafeText(request.opportunity_name, "request.opportunity_name",
      { maxLength: 500 }),
    opportunity_phase: assertSafeText(request.opportunity_phase, "request.opportunity_phase",
      { maxLength: 200 }),
    linked_subject_kind,
    linked_subject_id: linked_subject_kind === null
      ? null
      : assertExternalIdent(request.linked_subject_id, "request.linked_subject_id", { maxLength: 128 }),
    observed_at: nullableInstant(request.observed_at, "request.observed_at"),
    is_external_corporate_reference: true,
    // The four negatives that ARE the decision.
    creates_doctorcre_client: false,
    creates_doctorcre_deal: false,
    sets_lifecycle_state: false,
    phase_label_is_doctorcre_state: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Q103 — optimistic concurrency, and the one merge that is allowed.
//
// AUTOMATIC MERGE IS THE EXCEPTION, NOT THE RULE. It applies only when every
// edited field is in the routine class ACCORDING TO THIS MODULE'S OWN POLICY
// REGISTRY — never according to a label on the edit — AND the two edit sets
// touch no field in common AND the concurrent change is actually characterized.
// Anything touching lifecycle, financial, recipient or document fields
// reconciles VISIBLY with both versions preserved, and so does anything policy
// has not classified at all. There is no path in this function that resolves a
// conflict by taking the later write.
// ---------------------------------------------------------------------------

export const V5_J102_FIELD_CLASSES = deepFreeze([
  "lifecycle", "financial", "recipient", "document", "routine",
]);

export const V5_J102_MATERIAL_FIELD_CLASSES = deepFreeze([
  "lifecycle", "financial", "recipient", "document",
]);

/**
 * THE FOUR KINDS OF CONFLICT THIS EVALUATOR CAN FILE, as a vocabulary rather than
 * as four literals scattered through the branches below.
 *
 * IT IS EXPORTED BECAUSE THE WRITE BOUNDARY NEEDS IT. `conflict_kind` is a
 * derived-only field on the shipped path, so no caller of the store can choose
 * one — but `ops.j102_record_reconciliation_item` is granted to carr_writer, and
 * a direct caller could otherwise file a visible item labelled with a kind this
 * module never emits. A person reading the reconciliation queue would be reading
 * a label nothing in this slice authored. The SQL admission map transcribes this
 * list and the writer checks it, and the parity suite proves the two lists are
 * the same list.
 *
 * `reconciliationItem` below REFUSES anything outside it, so a fifth branch added
 * without a fifth entry here fails loudly at the point it is written rather than
 * silently widening what the database will accept.
 */
export const V5_J102_CONFLICT_KINDS = deepFreeze([
  "uncharacterized_concurrent_change",
  "overlapping_field_edit",
  "material_class_edit",
  "unclassified_field_edit",
]);

/**
 * THE FIELD CLASS IS POLICY, AND POLICY IS NOT A CALLER INPUT.
 *
 * `field_class` used to arrive on each edit and was believed. Labelling a
 * lifecycle or financial field `routine` therefore bought the auto-merge branch,
 * which is the one branch Q103 admits and the one that silently overwrites the
 * other partner — the exact outcome the decision exists to prevent. A caller
 * cannot supply it any more; it is DERIVED here, from this registry, and a field
 * the registry does not name is UNCLASSIFIED rather than routine.
 *
 * WHAT IS IN THE REGISTRY, and why it stops where it does. Every field this
 * module itself defines — the state axes, the structural ids and the money axes
 * — is classified, because this slice owns their meaning. NOTHING ELSE IS, and
 * the omission is deliberate: the customer-facing editable fields of a client,
 * an assignment or a deal record are not this module's to classify, and guessing
 * that (say) a phone number is routine would be inventing the very policy this
 * registry exists to hold. Those fields reconcile visibly, and the answer names
 * them as unclassified rather than pretending they were judged.
 *
 * THE ROUTINE CLASS IS DELIBERATELY EMPTY TODAY. The auto-merge branch below is
 * kept, because it is what Q103 settles and it becomes reachable the moment a
 * policy owner registers a routine field — but on today's registry no edit set
 * can reach it, and the module says so rather than leaving a reader to work it
 * out from the absence of entries.
 */
export const V5_J102_FIELD_CLASS_REGISTRY = deepFreeze({
  // Identity and structure.
  subject_kind: "lifecycle", subject_id: "lifecycle",
  relationship_id: "lifecycle", engagement_id: "lifecycle", assignment_id: "lifecycle",
  property_id: "lifecycle",
  // Q069 / Q077 — the client and the engagement.
  relationship_state: "lifecycle", active_engagement_count: "lifecycle",
  party_id: "lifecycle",
  engagement_state: "lifecycle", representation_basis: "lifecycle",
  effective_from: "lifecycle", effective_to: "lifecycle",
  // Q080 / Q095 — the assignment and its negotiations.
  assignment_phase: "lifecycle", open_negotiation_count: "lifecycle",
  selected_property_id: "lifecycle", active_lease_draft_target_id: "lifecycle",
  pending_deal_id: "lifecycle", multi_target_exception_ref: "lifecycle",
  negotiation_state: "lifecycle",
  // Q080 / Q094 — the deal's eight axes and its two recorded facts.
  instrument_kind: "lifecycle", deal_state: "lifecycle", execution_state: "lifecycle",
  diligence_state: "lifecycle", closing_state: "lifecycle", closing_date: "lifecycle",
  cancellation_reason: "lifecycle", completion_state: "lifecycle",
  commission_agreement_state: "financial", invoice_state: "financial",
  payment_state: "financial",
  // The document references a lifecycle record carries.
  supporting_document_id: "document", document_id: "document",
  content_digest: "document", version_no: "document",
});

/** The class of one edited field, or null when policy has not classified it. */
export function v5J102FieldClass(field) {
  return Object.prototype.hasOwnProperty.call(V5_J102_FIELD_CLASS_REGISTRY, field)
    ? V5_J102_FIELD_CLASS_REGISTRY[field] : null;
}

/**
 * The fact this registry does not hold, named the way every other missing fact
 * in this slice is named rather than left as an empty object nobody notices.
 */
export const V5_J102_UNCLASSIFIED_FIELD_POLICY = deepFreeze({
  fact: "policy_owned_classification_of_customer_editable_fields",
  why: "Q103's auto-merge is admitted only for ROUTINE fields, and only the owner of a field's meaning can say that it is routine. This module classifies the lifecycle, financial and document fields it defines itself; it classifies no customer-facing field of a client, assignment or deal record, and it does not guess. An edit naming an unclassified field reconciles visibly and is reported as unclassified.",
  produced_by: "not_produced_by_this_slice",
  routine_fields_registered: 0,
});

const MERGE_KEYS = Object.freeze([
  "tenant", "base_version_digest", "current_version_digest", "incoming", "concurrent", "actor",
]);
// `field_class` IS NOT HERE, and its absence is the fix. A caller naming it now
// gets `unknown_field` rather than the merge branch it was asking for.
const EDIT_KEYS = Object.freeze(["field", "value_digest", "edited_by", "edited_at"]);

function assertEdits(value, path) {
  assertArray(value, path, { min: 1, max: 256 });
  const seen = new Map();
  return value.map((raw, i) => {
    const p = `${path}[${i}]`;
    assertObject(raw, p);
    assertClosedKeys(raw, EDIT_KEYS, p, { allowAsserted: true });
    assertRequiredKeys(raw, EDIT_KEYS, p);
    const field = assertExternalIdent(raw.field, `${p}.field`, { maxLength: 128 });
    if (seen.has(field)) {
      fail("duplicate_edited_field", `${p} repeats "${field}"; one edit set names each field once`,
        { path: p, field, first_index: seen.get(field) });
    }
    seen.set(field, i);
    return {
      field,
      // DERIVED, never read off the edit.
      field_class: v5J102FieldClass(field),
      value_digest: assertDigestRef(raw.value_digest, `${p}.value_digest`),
      edited_by: assertExternalIdent(raw.edited_by, `${p}.edited_by`, { maxLength: 128 }),
      edited_at: nullableInstant(raw.edited_at, `${p}.edited_at`),
    };
  });
}

export function evaluateConcurrentEdit(request) {
  assertObject(request, "request");
  assertClosedKeys(request, MERGE_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, ["tenant", "base_version_digest", "current_version_digest",
    "incoming", "actor"], "request");
  assertTenant(request.tenant, "request.tenant");
  const base_version_digest = assertDigestRef(request.base_version_digest, "request.base_version_digest");
  const current_version_digest = assertDigestRef(request.current_version_digest,
    "request.current_version_digest");
  const actor = assertActor(request.actor, "request.actor");
  const incoming = assertEdits(request.incoming, "request.incoming");
  const concurrent = request.concurrent === undefined || request.concurrent === null
    ? [] : assertEdits(request.concurrent, "request.concurrent");

  const base = {
    schema_version: V5_J102_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    base_version_digest,
    current_version_digest,
    actor_slug: actor.slug,
    incoming_fields: incoming.map(e => e.field).sort(),
    concurrent_fields: concurrent.map(e => e.field).sort(),
    // Said on every answer of this evaluator, because it is the whole of Q103's
    // negative half.
    last_writer_wins: false,
    silent_overwrite: false,
    effects: V5_NO_EFFECTS,
  };

  // No concurrent movement at all: the ordinary optimistic path.
  if (base_version_digest === current_version_digest) {
    return deepFreeze({ decision: "allow", reason_id: "no_concurrent_movement", ...base,
      merged: false, auto_merged_fields: [], overlapping_fields: [], unclassified_fields: [],
      reconciliation_item: null, preserved_versions: [] });
  }

  const overlap = incoming.filter(e => concurrent.some(c => c.field === e.field))
    .map(e => e.field).sort();
  const materialIncoming = incoming.filter(e => V5_J102_MATERIAL_FIELD_CLASSES.includes(e.field_class))
    .map(e => e.field).sort();
  const materialConcurrent = concurrent.filter(e => V5_J102_MATERIAL_FIELD_CLASSES.includes(e.field_class))
    .map(e => e.field).sort();
  const unclassified = [...new Set([...incoming, ...concurrent]
    .filter(e => e.field_class === null).map(e => e.field))].sort();

  // A base that no longer matches current, with NOTHING known about what moved,
  // is not a demonstrably non-overlapping edit. It reconciles rather than
  // merging on the strength of an absence.
  if (concurrent.length === 0) {
    return deepFreeze({
      decision: "reconcile", reason_id: "concurrent_change_not_characterized", ...base,
      merged: false, auto_merged_fields: [], overlapping_fields: [],
      unclassified_fields: [...new Set(incoming.filter(e => e.field_class === null)
        .map(e => e.field))].sort(),
      reconciliation_item: reconciliationItem({
        conflict_kind: "uncharacterized_concurrent_change",
        base_version_digest, current_version_digest, incoming, concurrent, actor,
      }),
      preserved_versions: ["base", "current", "incoming"],
      resolved_by_machine: false,
    });
  }

  const conflict_kind = overlap.length > 0
    ? "overlapping_field_edit"
    : materialIncoming.length > 0 || materialConcurrent.length > 0
      ? "material_class_edit"
      // An edit policy has not classified is not a routine edit. It is an edit
      // nobody has said anything about, and merging on that silence is the same
      // last-writer-wins outcome reached by a longer route.
      : unclassified.length > 0
        ? "unclassified_field_edit"
        : null;

  if (conflict_kind !== null) {
    return deepFreeze({
      decision: "reconcile",
      reason_id: overlap.length > 0
        ? "overlapping_edits_require_reconciliation"
        : materialIncoming.length > 0 || materialConcurrent.length > 0
          ? "material_class_edits_require_reconciliation"
          : "field_classification_not_established",
      ...base,
      merged: false, auto_merged_fields: [],
      overlapping_fields: overlap,
      material_incoming_fields: materialIncoming,
      material_concurrent_fields: materialConcurrent,
      unclassified_fields: unclassified,
      reconciliation_item: reconciliationItem({
        conflict_kind, base_version_digest, current_version_digest, incoming, concurrent, actor,
      }),
      preserved_versions: ["base", "current", "incoming"],
      resolved_by_machine: false,
      ...(unclassified.length > 0
        ? { missing_fact: V5_J102_UNCLASSIFIED_FIELD_POLICY.fact,
            missing_fact_reason: V5_J102_UNCLASSIFIED_FIELD_POLICY.why,
            produced_by: V5_J102_UNCLASSIFIED_FIELD_POLICY.produced_by }
        : {}),
    });
  }

  // Demonstrably non-overlapping, POLICY-CLASSIFIED and entirely routine, against
  // an authenticated base and the current version the database actually holds.
  // This is the one case Q103 admits — and on today's registry, which registers
  // no routine field, nothing reaches it. That is the conservative half of the
  // fix rather than an oversight.
  return deepFreeze({
    decision: "allow", reason_id: "nonoverlapping_routine_edits_auto_merged", ...base,
    merged: true,
    auto_merged_fields: incoming.map(e => e.field).sort(),
    overlapping_fields: [],
    unclassified_fields: [],
    reconciliation_item: null,
    preserved_versions: ["base", "current", "incoming"],
  });
}

function reconciliationItem({ conflict_kind, base_version_digest, current_version_digest,
  incoming, concurrent, actor }) {
  // THE ONE PLACE EVERY ITEM IS BUILT, so it is the one place the vocabulary can
  // be held. A branch above that invents a fifth kind fails here rather than
  // shipping a label the SQL write boundary, the queue and the reader have never
  // heard of.
  if (!V5_J102_CONFLICT_KINDS.includes(conflict_kind)) {
    throw new V5J102Error("contract_self_check_failed",
      `this evaluator filed the unregistered conflict kind "${conflict_kind}"`);
  }
  return deepFreeze({
    schema_version: V5_J102_RECONCILIATION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    conflict_kind,
    base_version_digest,
    current_version_digest,
    // BOTH SIDES ARE PRESERVED IN FULL. A reconciliation item that recorded only
    // the loser would make the resolution unreviewable, which is the same defect
    // as resolving it silently.
    incoming_edits: incoming.map(e => ({ ...e })),
    concurrent_edits: concurrent.map(e => ({ ...e })),
    proposed_by: actor.slug,
    visible: true,
    applied: false,
    resolved_by_machine: false,
  });
}

// ---------------------------------------------------------------------------
// Q103 for TYPED TRANSITIONS: the automatic merge of a non-overlapping edit.
//
// evaluateConcurrentEdit above judges RAW field edits, and it cannot merge a
// lifecycle, financial or document edit however disjoint, because nothing can
// re-validate a raw value against the row it lands on. A typed transition is
// different in exactly that respect: the kernel can re-run the transition's
// WHOLE admission — prerequisites, instrument, evidence, bindings, related
// subjects — against the row as it now stands. So a transition decided against
// a base that has since moved is merged automatically when, and only when:
//
//   1. the movement is CHARACTERIZED: the stored row's own prior digest is the
//      caller's base, so exactly one committed transition moved it, and that
//      transition's written fields are known from its declared contract;
//   2. the fields this transition writes on the subject and the fields the
//      intervening transition wrote on it are DISJOINT; and
//   3. the transition is RE-ADMITTED against the current committed row — the
//      caller of this function does that, and a refusal there is an ordinary
//      refusal of a command that no longer applies, never a merge.
//
// Anything else — two or more intervening writes, a correction, an unknown
// writer, or any shared field — is a visible reconciliation item with both
// versions preserved. There is still no branch that takes the later write.
//
// THE WRITE SETS ARE OVER-APPROXIMATED, deliberately. A transition's written
// fields on a subject kind are its coupled facts on that kind PLUS every
// derived counter or mirror of that kind (V5_J102_DERIVED_FIELDS), whether or
// not this particular call moves it. Over-reporting a write can only turn a
// merge into a reconciliation; under-reporting one could merge over it.
// ---------------------------------------------------------------------------

/**
 * The fields the kernel moves on a subject without naming them as coupled facts.
 * The SQL admission map carries the same list as `derived_fields`, and the parity
 * suite holds the two to each other.
 */
export const V5_J102_DERIVED_FIELDS = deepFreeze([
  "relationship.active_engagement_count",
  "assignment.open_negotiation_count",
  "assignment.active_lease_draft_target_id",
]);

/**
 * Every field a transition may write, by subject kind: the primary subject and
 * the prefixes of its coupled facts, each carrying its coupled facts plus every
 * derived field of that kind. A SUPERSET of the SQL map's `writes`, by
 * construction and by test.
 */
export function v5J102TransitionWrites(transition_id) {
  const t = TRANSITIONS[transition_id];
  if (t === undefined) {
    fail("unknown_transition", `"${String(transition_id)}" is not a registered transition`,
      { registered: [...V5_J102_TRANSITION_IDS] });
  }
  const kinds = new Set([t.subject_kind, ...t.coupled_facts.map(f => f.split(".")[0])]);
  const out = {};
  for (const kind of [...kinds].sort()) {
    const fields = new Set();
    for (const fact of [...t.coupled_facts, ...V5_J102_DERIVED_FIELDS]) {
      const [k, field] = fact.split(".");
      if (k === kind) fields.add(field);
    }
    out[kind] = [...fields].sort();
  }
  return deepFreeze(out);
}

export const V5_J102_CONCURRENT_TRANSITION_OUTCOMES = deepFreeze([
  "no_concurrent_movement",
  "nonoverlapping_transition_merged_on_current_row",
  "concurrent_change_not_characterized",
  "overlapping_transition_writes_require_reconciliation",
]);

const CONCURRENT_TRANSITION_KEYS = Object.freeze([
  "tenant", "transition_id", "subject_kind", "base_version_digest", "current_version_digest",
  "current_prior_state_digest", "current_established_by_transition",
]);

export function evaluateConcurrentTransition(request) {
  assertObject(request, "request");
  assertClosedKeys(request, CONCURRENT_TRANSITION_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, ["tenant", "transition_id", "subject_kind", "base_version_digest",
    "current_version_digest"], "request");
  assertTenant(request.tenant, "request.tenant");
  const transition_id = assertEnum(request.transition_id, V5_J102_TRANSITION_IDS,
    "request.transition_id", "unknown_transition");
  const subject_kind = assertEnum(request.subject_kind, V5_J102_SUBJECT_KINDS,
    "request.subject_kind", "unknown_subject_kind");
  const base_version_digest = assertDigestRef(request.base_version_digest,
    "request.base_version_digest");
  const current_version_digest = assertDigestRef(request.current_version_digest,
    "request.current_version_digest");
  const prior = request.current_prior_state_digest === undefined ||
    request.current_prior_state_digest === null
    ? null : assertDigestRef(request.current_prior_state_digest, "request.current_prior_state_digest");
  const established = request.current_established_by_transition ?? null;
  const incoming_fields = v5J102TransitionWrites(transition_id)[subject_kind] ?? [];

  const base = {
    schema_version: V5_J102_RECONCILIATION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    transition_id, subject_kind, base_version_digest, current_version_digest,
    incoming_fields: [...incoming_fields],
    last_writer_wins: false,
    silent_overwrite: false,
    resolved_by_machine: false,
    effects: V5_NO_EFFECTS,
  };

  if (base_version_digest === current_version_digest) {
    return deepFreeze({ decision: "allow", reason_id: "no_concurrent_movement", ...base,
      merged: false, characterized: true, concurrent_transition_id: null,
      concurrent_fields: [], overlapping_fields: [], readmission_required: false,
      conflict_kind: null });
  }

  // ONE STEP, AND ONLY ONE, IS CHARACTERIZED. The stored row names the digest it
  // replaced; when that is the caller's base, the transition that produced the
  // row is the only write between them. A correction is not a transition and its
  // corrected fields are not a declared write set, so it never characterizes.
  const characterized = prior !== null && prior === base_version_digest &&
    typeof established === "string" && V5_J102_TRANSITION_IDS.includes(established);
  if (!characterized) {
    return deepFreeze({ decision: "reconcile", reason_id: "concurrent_change_not_characterized",
      ...base, merged: false, characterized: false,
      concurrent_transition_id: typeof established === "string" ? established : null,
      concurrent_fields: [], overlapping_fields: [], readmission_required: false,
      conflict_kind: "uncharacterized_concurrent_change",
      why: prior === base_version_digest
        ? "the write that moved this subject is not a registered transition, so its written fields are not declared"
        : "more than one write, or a write not decided against this base, lies between the base and the current row" });
  }

  const concurrent_fields = v5J102TransitionWrites(established)[subject_kind] ?? [];
  const overlapping_fields = incoming_fields.filter(f => concurrent_fields.includes(f)).sort();
  if (overlapping_fields.length > 0) {
    return deepFreeze({ decision: "reconcile",
      reason_id: "overlapping_transition_writes_require_reconciliation", ...base,
      merged: false, characterized: true, concurrent_transition_id: established,
      concurrent_fields: [...concurrent_fields], overlapping_fields,
      readmission_required: false, conflict_kind: "overlapping_field_edit" });
  }
  return deepFreeze({ decision: "allow",
    reason_id: "nonoverlapping_transition_merged_on_current_row", ...base,
    merged: true, characterized: true, concurrent_transition_id: established,
    concurrent_fields: [...concurrent_fields], overlapping_fields: [],
    // The merge is CONDITIONAL on this: the transition is decided again, in
    // full, against the current committed row, and only its answer lands.
    readmission_required: true, conflict_kind: null });
}

/**
 * Q103's second half: ownership, freshness and in-progress automation, PROJECTED
 * from trusted state.
 *
 * Every field here is supplied by the record layer and echoed. Nothing is
 * inferred: an absent last-material-change is reported as UNKNOWN rather than
 * filled in from the row's updated_at, and an unsupplied automation list is
 * reported as unknown rather than as "nothing is running" — which is the
 * difference between a quiet record and a record nobody asked about.
 */
const OWNERSHIP_KEYS = Object.freeze([
  "tenant", "subject_kind", "subject_id", "owner_slug", "last_material_change_at",
  "last_material_change_by", "state_digest", "active_automation", "now",
]);
const AUTOMATION_KEYS = Object.freeze(["automation_id", "kind", "started_at", "started_by"]);

export function projectOwnershipAndFreshness(request) {
  assertObject(request, "request");
  assertClosedKeys(request, OWNERSHIP_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, ["tenant", "subject_kind", "subject_id", "state_digest", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const subject_kind = assertEnum(request.subject_kind, V5_J102_SUBJECT_KINDS,
    "request.subject_kind", "unknown_subject_kind");
  const changed_at = nullableInstant(request.last_material_change_at, "request.last_material_change_at");
  if (changed_at !== null && Date.parse(changed_at) > now) {
    fail("last_material_change_after_now",
      "request.last_material_change_at is after the server instant; a freshness age cannot be negative",
      { path: "request.last_material_change_at" });
  }
  const automation = request.active_automation === undefined || request.active_automation === null
    ? null
    : assertArray(request.active_automation, "request.active_automation", { max: 64 })
      .map((raw, i) => {
        const p = `request.active_automation[${i}]`;
        assertObject(raw, p);
        assertClosedKeys(raw, AUTOMATION_KEYS, p, { allowAsserted: true });
        assertRequiredKeys(raw, AUTOMATION_KEYS, p);
        return {
          automation_id: assertExternalIdent(raw.automation_id, `${p}.automation_id`, { maxLength: 128 }),
          kind: assertExternalIdent(raw.kind, `${p}.kind`, { maxLength: 128 }),
          started_at: nullableInstant(raw.started_at, `${p}.started_at`),
          started_by: assertExternalIdent(raw.started_by, `${p}.started_by`, { maxLength: 128 }),
        };
      });
  return deepFreeze({
    schema_version: V5_J102_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision: "allow",
    reason_id: "ownership_and_freshness_projected_from_trusted_state",
    subject_kind,
    subject_id: assertExternalIdent(request.subject_id, "request.subject_id", { maxLength: 128 }),
    owner_slug: request.owner_slug === undefined || request.owner_slug === null
      ? null : assertExternalIdent(request.owner_slug, "request.owner_slug", { maxLength: 128 }),
    owner_known: request.owner_slug !== undefined && request.owner_slug !== null,
    state_digest: assertDigestRef(request.state_digest, "request.state_digest"),
    last_material_change_at: changed_at,
    last_material_change_by:
      request.last_material_change_by === undefined || request.last_material_change_by === null
        ? null
        : assertExternalIdent(request.last_material_change_by, "request.last_material_change_by",
          { maxLength: 128 }),
    freshness_age_seconds: changed_at === null
      ? null : Math.floor((now - Date.parse(changed_at)) / 1000),
    freshness_known: changed_at !== null,
    active_automation: automation,
    active_automation_known: automation !== null,
    inferred_fields: [],
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Q081 — migration without a big-bang rename.
//
// THREE THINGS, AND THE THIRD IS THE ONE THAT KEEPS THIS HONEST:
//   1. classifyLegacyLifecycleRow    — evidence-led classification, with
//      ambiguity routed to reconciliation instead of guessed.
//   2. projectLegacyCompatibilityView — the shape an old caller still reads.
//   3. v5J102MigrationReadiness       — which REFUSES to say migration is
//      complete, because the exact caller census does not exist and has not
//      been verified.
// ---------------------------------------------------------------------------

export const V5_J102_LEGACY_PHASES = deepFreeze([
  "research", "site_selection", "negotiation", "legal", "due_diligence", "closing",
]);

export const V5_J102_LEGACY_CLASSIFICATIONS = deepFreeze([
  "assignment", "deal", "requires_reconciliation",
]);

const LEGACY_ROW_KEYS = Object.freeze([
  "tenant", "legacy_row_id", "legacy_phase", "executed_instrument_evidence",
  "closing_evidence", "legacy_closed_flag", "legacy_outcome",
]);

/**
 * Classify one legacy row from EVIDENCE, never from its own phase label.
 *
 * ORDERED, so a second reader reaches the same answer:
 *   1. A closed flag that disagrees with the outcome is the coupled-fact defect
 *      Q082 removes; it goes to reconciliation before anything else is read,
 *      because a row that contradicts itself cannot be classified by a rule that
 *      trusts either half.
 *   2. Executed-instrument evidence present → it is a Deal, whatever the phase
 *      said. Closing evidence additionally makes it a CLOSED deal; without it the
 *      deal lands PENDING, which is Q094 applied to the migration path.
 *   3. No executed evidence and a phase in research/site_selection/negotiation/
 *      legal → it is an Assignment.
 *   4. A phase of due_diligence or closing with NO executed evidence is the
 *      ambiguous case: the label claims a stage only an executed instrument can
 *      reach, so the row goes to reconciliation rather than being classified.
 */
export function classifyLegacyLifecycleRow(request) {
  assertObject(request, "request");
  assertClosedKeys(request, LEGACY_ROW_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, ["tenant", "legacy_row_id", "legacy_phase"], "request");
  assertTenant(request.tenant, "request.tenant");
  const legacy_row_id = assertExternalIdent(request.legacy_row_id, "request.legacy_row_id",
    { maxLength: 128 });
  const legacy_phase = assertEnum(request.legacy_phase, V5_J102_LEGACY_PHASES,
    "request.legacy_phase", "unknown_legacy_phase");
  const executed =
    request.executed_instrument_evidence === undefined || request.executed_instrument_evidence === null
      ? null
      : assertLifecycleEvidence(request.executed_instrument_evidence,
        "request.executed_instrument_evidence");
  if (executed !== null && !V5_J102_EXECUTION_EVIDENCE_KINDS.includes(executed.evidence_kind)) {
    fail("evidence_kind_mismatch",
      "request.executed_instrument_evidence must establish legal execution of an instrument",
      { path: "request.executed_instrument_evidence", actual: executed.evidence_kind,
        registered: [...V5_J102_EXECUTION_EVIDENCE_KINDS] });
  }
  const closing = request.closing_evidence === undefined || request.closing_evidence === null
    ? null : assertLifecycleEvidence(request.closing_evidence, "request.closing_evidence");
  if (closing !== null && closing.evidence_kind !== "final_closing_settlement") {
    fail("evidence_kind_mismatch",
      "request.closing_evidence must be a final_closing_settlement",
      { path: "request.closing_evidence", actual: closing.evidence_kind });
  }
  const closedFlag = request.legacy_closed_flag === undefined || request.legacy_closed_flag === null
    ? null : assertBoolean(request.legacy_closed_flag, "request.legacy_closed_flag");
  const outcome = request.legacy_outcome === undefined || request.legacy_outcome === null
    ? null : assertSafeText(request.legacy_outcome, "request.legacy_outcome", { maxLength: 128 });

  const base = {
    schema_version: V5_J102_COMPATIBILITY_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    legacy_row_id, legacy_phase,
    // Said on every classification: a legacy label never becomes a state; it is
    // only a hint about which evidence to go looking for.
    label_used_as_state: false,
    big_bang_rename: false,
    effects: V5_NO_EFFECTS,
  };

  if (closedFlag !== null &&
      ((closedFlag === true && (outcome === null || outcome === "open")) ||
       (closedFlag === false && outcome !== null && outcome !== "open"))) {
    return deepFreeze({ decision: "reconcile", reason_id: "closed_flag_and_outcome_disagree",
      ...base, classification: "requires_reconciliation",
      legacy_closed_flag: closedFlag, legacy_outcome: outcome, requires_reconciliation: true });
  }
  if (executed !== null) {
    return deepFreeze({
      decision: "allow",
      reason_id: closing === null
        ? "executed_instrument_evidence_makes_deal"
        : "executed_and_closed_evidence_makes_closed_deal",
      ...base, classification: "deal", requires_reconciliation: false,
      projected_execution_state: "executed",
      projected_deal_state: closing === null ? "pending" : "closed",
      projected_closing_state: closing === null ? "not_reached" : "closed",
      // Q094 again, this time on the migration path: a row carrying an executed
      // contract and no closing evidence lands PENDING, not closed.
      closed_on_execution_evidence: false,
    });
  }
  if (["research", "site_selection", "negotiation", "legal"].includes(legacy_phase)) {
    return deepFreeze({
      decision: "allow", reason_id: "pre_execution_phase_becomes_assignment",
      ...base, classification: "assignment", requires_reconciliation: false,
      projected_assignment_phase: legacy_phase === "research" ? "research"
        : legacy_phase === "site_selection" ? "search" : "negotiation",
    });
  }
  return deepFreeze({
    decision: "reconcile", reason_id: "post_execution_phase_without_executed_evidence",
    ...base, classification: "requires_reconciliation", requires_reconciliation: true,
    missing_evidence: ["executed_instrument_evidence"],
  });
}

const COMPAT_KEYS = Object.freeze(["tenant", "assignment", "deal"]);

/**
 * The shape an old caller still reads while both models run side by side.
 *
 * A PROJECTION, and it says so on every answer. It is derived from the new
 * records on every read, holds no state of its own, and is explicitly NOT
 * authoritative — writing through it is not offered at all, because a write path
 * here would be the second authority this whole slice exists to avoid.
 */
export function projectLegacyCompatibilityView(request) {
  assertObject(request, "request");
  assertClosedKeys(request, COMPAT_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, ["tenant"], "request");
  assertTenant(request.tenant, "request.tenant");
  const assignment = request.assignment === undefined || request.assignment === null
    ? null : assertLifecycleSubject(request.assignment, "request.assignment");
  const deal = request.deal === undefined || request.deal === null
    ? null : assertLifecycleSubject(request.deal, "request.deal");
  if (assignment === null && deal === null) {
    fail("missing_field", "request must carry an assignment, a deal, or both", { path: "request" });
  }

  // The legacy record had ONE phase column spanning both entities. It is
  // reconstructed here for reading, and the entity that answered is named, so a
  // reader can see which half of the new model the old column came from.
  let legacy_phase = null;
  let phase_source = null;
  if (deal !== null && deal.deal_state !== "cancelled") {
    phase_source = "deal";
    legacy_phase = deal.closing_state === "closed" ? "closing"
      : deal.diligence_state === "in_progress" ? "due_diligence"
        : deal.execution_state === "executed" ? "legal" : "negotiation";
  } else if (assignment !== null) {
    phase_source = "assignment";
    legacy_phase = assignment.assignment_phase === "research" ? "research"
      : assignment.assignment_phase === "search" ? "site_selection"
        : assignment.assignment_phase === "concluded" ? "closing" : "negotiation";
  }

  return deepFreeze({
    schema_version: V5_J102_COMPATIBILITY_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision: "allow",
    reason_id: "legacy_shape_projected_from_current_records",
    legacy_phase,
    legacy_phase_source: phase_source,
    legacy_closed: deal !== null && deal.deal_state === "closed",
    legacy_outcome: deal === null ? null
      : deal.deal_state === "closed" ? "won"
        : deal.deal_state === "cancelled" ? "lost" : "open",
    assignment_id: assignment?.subject_id ?? null,
    deal_id: deal?.subject_id ?? null,
    // The four properties that keep this a view rather than a second home.
    authoritative: false,
    writable: false,
    derived_from_current_records: true,
    retires_any_caller: false,
    effects: V5_NO_EFFECTS,
  });
}

const SHADOW_KEYS = Object.freeze(["tenant", "legacy_row", "projected_view"]);
const SHADOW_LEGACY_KEYS = Object.freeze(["legacy_row_id", "phase", "closed", "outcome"]);

/**
 * The migration shadow comparison: what the OLD row says beside what the NEW
 * records project, field by field, with no verdict about which is right.
 *
 * DELIBERATELY NOT A REPAIR. A difference is REPORTED and a human resolves it.
 * Auto-correcting the legacy row from the projection would make the shadow prove
 * itself, which is the only outcome a shadow must never be able to produce.
 */
export function compareMigrationShadow(request) {
  assertObject(request, "request");
  assertClosedKeys(request, SHADOW_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, SHADOW_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");
  const legacy = assertObject(request.legacy_row, "request.legacy_row");
  assertClosedKeys(legacy, SHADOW_LEGACY_KEYS, "request.legacy_row", { allowAsserted: true });
  assertRequiredKeys(legacy, ["legacy_row_id", "phase"], "request.legacy_row");
  const view = assertObject(request.projected_view, "request.projected_view");
  if (view.schema_version !== V5_J102_COMPATIBILITY_SCHEMA_VERSION) {
    fail("unknown_schema_version",
      `request.projected_view.schema_version must be "${V5_J102_COMPATIBILITY_SCHEMA_VERSION}"`,
      { path: "request.projected_view.schema_version" });
  }
  const differences = [];
  const compare = (field, legacy_value, projected_value) => {
    if (legacy_value === undefined) return;
    if (legacy_value !== projected_value) {
      differences.push({ field, legacy: legacy_value ?? null, projected: projected_value ?? null });
    }
  };
  compare("phase", legacy.phase, view.legacy_phase);
  compare("closed", legacy.closed, view.legacy_closed);
  compare("outcome", legacy.outcome, view.legacy_outcome);
  return deepFreeze({
    schema_version: V5_J102_COMPATIBILITY_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision: differences.length === 0 ? "allow" : "reconcile",
    reason_id: differences.length === 0
      ? "shadow_projection_matches_legacy_row" : "shadow_projection_differs_from_legacy_row",
    legacy_row_id: assertExternalIdent(legacy.legacy_row_id, "request.legacy_row.legacy_row_id",
      { maxLength: 128 }),
    differences: deepFreeze(differences),
    requires_human_reconciliation: differences.length > 0,
    legacy_row_modified: false,
    projection_modified: false,
    effects: V5_NO_EFFECTS,
  });
}

const READINESS_KEYS = Object.freeze(["tenant", "caller_census", "shadow_runs", "latest_shadow_run"]);
const SHADOW_RUN_KEYS = Object.freeze([
  "run_digest", "compared_rows", "matching_rows", "differing_rows", "unlinked_rows",
  "many_to_one_subjects", "subjects_without_legacy_row", "snapshot_current", "clean",
]);
const CENSUS_KEYS = Object.freeze([
  "census_ref", "enumerated_callers", "migrated_callers", "attested_by", "attested_at",
]);

/**
 * Whether the old interface may be retired. It may not.
 *
 * THE MISSING FACT IS NAMED RATHER THAN WORKED AROUND. Q081 permits retirement
 * only after migration proof, and that proof needs an EXACT caller census
 * checked against the running system. No such census exists in this record
 * layer, nothing in this slice produces one, and this function therefore returns
 * `may_retire_callers: false` for EVERY input — including one that supplies a
 * census, because a census a caller hands in is a claim about the callers rather
 * than a verification of them. What a supplied census does buy is a more useful
 * answer: "an unverified census exists" is a different state from "no census at
 * all", and the reason_id says which.
 */
export function v5J102MigrationReadiness(request) {
  assertObject(request, "request");
  assertClosedKeys(request, READINESS_KEYS, "request", { allowAsserted: true });
  assertRequiredKeys(request, ["tenant"], "request");
  assertTenant(request.tenant, "request.tenant");
  let census = null;
  if (request.caller_census !== undefined && request.caller_census !== null) {
    const raw = assertObject(request.caller_census, "request.caller_census");
    assertClosedKeys(raw, CENSUS_KEYS, "request.caller_census", { allowAsserted: true });
    assertRequiredKeys(raw, ["census_ref", "enumerated_callers", "migrated_callers"],
      "request.caller_census");
    census = {
      census_ref: assertExternalIdent(raw.census_ref, "request.caller_census.census_ref",
        { maxLength: 255 }),
      enumerated_callers: assertSafeInteger(raw.enumerated_callers,
        "request.caller_census.enumerated_callers", { min: 0, max: 1000000 }),
      migrated_callers: assertSafeInteger(raw.migrated_callers,
        "request.caller_census.migrated_callers", { min: 0, max: 1000000 }),
      attested_by: raw.attested_by === undefined || raw.attested_by === null
        ? null : assertExternalIdent(raw.attested_by, "request.caller_census.attested_by",
          { maxLength: 128 }),
      attested_at: nullableInstant(raw.attested_at, "request.caller_census.attested_at"),
    };
  }
  const shadow_runs = request.shadow_runs === undefined || request.shadow_runs === null
    ? 0 : assertSafeInteger(request.shadow_runs, "request.shadow_runs", { min: 0, max: 1000000 });
  // THE NEWEST SHADOW RUN, as ops.j102_run_migration_shadow recorded it. Its
  // cleanliness is RE-DERIVED from its counts here, never read off its `clean`
  // flag: a run is clean only when it compared something, found no difference,
  // left no legacy row unlinked, mapped no two legacy rows onto one subject, and
  // left no Salesforce-referenced subject without a legacy row (owner ruling (f)).
  // A flag that disagrees with its own counts is refused, not believed. And a
  // clean run is PROOF only while `snapshot_current` holds: the reader recomputes
  // both input digests, and a run over a snapshot that has moved is stale.
  let latest = null;
  if (request.latest_shadow_run !== undefined && request.latest_shadow_run !== null) {
    const raw = assertObject(request.latest_shadow_run, "request.latest_shadow_run");
    assertClosedKeys(raw, SHADOW_RUN_KEYS, "request.latest_shadow_run", { allowAsserted: true });
    assertRequiredKeys(raw, SHADOW_RUN_KEYS, "request.latest_shadow_run");
    const count = k => assertSafeInteger(raw[k], `request.latest_shadow_run.${k}`,
      { min: 0, max: 1000000 });
    latest = {
      run_digest: assertDigestRef(raw.run_digest, "request.latest_shadow_run.run_digest"),
      compared_rows: count("compared_rows"), matching_rows: count("matching_rows"),
      differing_rows: count("differing_rows"), unlinked_rows: count("unlinked_rows"),
      many_to_one_subjects: count("many_to_one_subjects"),
      subjects_without_legacy_row: count("subjects_without_legacy_row"),
      snapshot_current: assertBoolean(raw.snapshot_current, "request.latest_shadow_run.snapshot_current"),
    };
    if (latest.compared_rows !== latest.matching_rows + latest.differing_rows) {
      fail("shadow_run_counts_inconsistent",
        "request.latest_shadow_run.compared_rows must equal matching_rows + differing_rows",
        { path: "request.latest_shadow_run" });
    }
    latest.clean = latest.compared_rows > 0 && latest.differing_rows === 0 &&
      latest.unlinked_rows === 0 && latest.many_to_one_subjects === 0 &&
      latest.subjects_without_legacy_row === 0;
    if (assertBoolean(raw.clean, "request.latest_shadow_run.clean") !== latest.clean) {
      fail("shadow_run_clean_flag_contradicts_counts",
        "request.latest_shadow_run.clean disagrees with the run's own counts",
        { path: "request.latest_shadow_run.clean", derived: latest.clean });
    }
  }
  const shadowClean = latest?.clean === true && latest.snapshot_current === true;
  return deepFreeze({
    schema_version: V5_J102_COMPATIBILITY_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision: "refuse",
    reason_id: census === null ? "caller_census_absent" : "caller_census_supplied_but_unverified",
    may_retire_callers: false,
    migration_complete: false,
    caller_census: census,
    caller_census_verified: false,
    shadow_runs,
    shadow_comparison_clean_run: shadowClean,
    latest_shadow_run: latest === null ? null : deepFreeze(latest),
    compatibility_view_available: true,
    big_bang_rename: false,
    missing_facts: deepFreeze([
      {
        fact: "exact_caller_census",
        why: "Q081 permits retiring the old interface only after migration proof, and no verified enumeration of the callers and projections still reading the legacy shape exists in this record layer.",
        produced_by: "not_produced_by_this_slice",
      },
      ...(shadowClean ? [] : [{
        fact: "shadow_comparison_clean_run",
        why: latest === null
          ? "No migration shadow has been run; ops.j102_run_migration_shadow produces one."
          : !latest.clean
            ? "The newest migration shadow run is not clean: it found differences, unlinked legacy rows, many-to-one mappings or referenced subjects with no legacy row, which a person must resolve before it counts as proof."
            : "The newest migration shadow run was clean over a snapshot that has since moved; run it again.",
        produced_by: "ops.j102_run_migration_shadow",
      }]),
    ]),
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no timestamp, actor, session, subject or
// acceptance fact — so two callers describing the same policy reach the same
// digest. The digest is an identity for these bytes and nothing else: it is not
// an acceptance, not a receipt, and not evidence for any consumer gate.
// ---------------------------------------------------------------------------

export function v5J102PolicyPreimage() {
  return {
    schema_version: V5_J102_SCHEMA_VERSION,
    policy_version: V5_J102_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_subset_digest: v5J102DecisionSubsetDigest(),
    subject_kinds: [...V5_J102_SUBJECT_KINDS],
    states: {
      relationship: [...V5_J102_RELATIONSHIP_STATES],
      engagement: [...V5_J102_ENGAGEMENT_STATES],
      representation_bases: [...V5_J102_REPRESENTATION_BASES],
      assignment: [...V5_J102_ASSIGNMENT_PHASES],
      property_negotiation: [...V5_J102_NEGOTIATION_STATES],
      instrument_kinds: [...V5_J102_INSTRUMENT_KINDS],
      deal_state: [...V5_J102_DEAL_STATES],
      execution_state: [...V5_J102_EXECUTION_STATES],
      diligence_state: [...V5_J102_DILIGENCE_STATES],
      closing_state: [...V5_J102_CLOSING_STATES],
      commission_agreement_state: [...V5_J102_COMMISSION_STATES],
      invoice_state: [...V5_J102_INVOICE_STATES],
      payment_state: [...V5_J102_PAYMENT_STATES],
      completion_state: [...V5_J102_COMPLETION_STATES],
    },
    deal_axes: [...V5_J102_DEAL_AXES],
    evidence_kinds: V5_J102_EVIDENCE_KINDS.map(kind => ({
      evidence_kind: kind,
      source: EVIDENCE_KIND_TABLE[kind].source,
      binds_subject_kind: EVIDENCE_KIND_TABLE[kind].binds_subject_kind ?? null,
      requires_author_class: EVIDENCE_KIND_TABLE[kind].requires_author_class ?? null,
      document_states: EVIDENCE_KIND_TABLE[kind].document_states ?? null,
      record_kind: EVIDENCE_KIND_TABLE[kind].record_kind ?? null,
      approval_kind: EVIDENCE_KIND_TABLE[kind].approval_kind ?? null,
      requires_approver_class: EVIDENCE_KIND_TABLE[kind].requires_approver_class ?? null,
      checks_effective_window_if_carried:
        EVIDENCE_KIND_TABLE[kind].checks_effective_window_if_carried === true,
      requires_closing_date: EVIDENCE_KIND_TABLE[kind].requires_closing_date === true,
      requires_reason: EVIDENCE_KIND_TABLE[kind].requires_reason === true,
      permitted_actor_classes: [...EVIDENCE_KIND_TABLE[kind].permitted_actor_classes],
    })),
    refused_evidence_sources: [...V5_J102_REFUSED_EVIDENCE_SOURCES],
    subject_binding_sources: [...V5_J102_SUBJECT_BINDING_SOURCES],
    partner_authored_record_kinds: [...V5_J102_PARTNER_AUTHORED_RECORD_KINDS],
    transitions: V5_J102_TRANSITION_IDS.map(id => v5J102TransitionContract(id)),
    initializations: V5_J102_INITIALIZATION_IDS.map(id => v5J102InitializationContract(id)),
    // Said on the policy itself, because "which door creates a row of this kind"
    // is the first question a reader of the two tables has: three kinds are
    // created by their own narrow initialization, two are COUPLED creations of a
    // transition whose primary was loaded, and no transition creates its own
    // primary subject.
    subject_creation_doors: {
      by_initialization: Object.fromEntries(V5_J102_INITIALIZATION_IDS
        .map(id => [INITIALIZATIONS[id].subject_kind, id])),
      coupled_to_transition: { engagement: "establish-client-and-engagement",
        deal: "commit-winning-property" },
      primary_subject_of_a_transition_is_never_created: true,
      initialization_requires_evidence: false,
    },
    field_classes: [...V5_J102_FIELD_CLASSES],
    material_field_classes: [...V5_J102_MATERIAL_FIELD_CLASSES],
    field_class_registry: Object.fromEntries(
      Object.keys(V5_J102_FIELD_CLASS_REGISTRY).sort()
        .map(field => [field, V5_J102_FIELD_CLASS_REGISTRY[field]])),
    caller_supplied_field_class_admitted: false,
    // The conflict vocabulary sits on the policy because the SQL write boundary
    // enforces it, and a control the database applies belongs in the identity of
    // the policy the database is applying.
    conflict_kinds: [...V5_J102_CONFLICT_KINDS],
    salesforce: {
      link_targets: [...V5_J102_SALESFORCE_LINK_TARGETS],
      phase_label_is_doctorcre_state: false,
    },
    legacy: {
      phases: [...V5_J102_LEGACY_PHASES],
      classifications: [...V5_J102_LEGACY_CLASSIFICATIONS],
      may_retire_callers: false,
    },
    journey_three_excluded: {
      tour_creation: false,
      tour_activation: false,
    },
  };
}

export function v5J102PolicyDigest() {
  return digest(v5J102PolicyPreimage());
}

export function v5J102PolicyCanonicalBytes() {
  return canonicalJson(v5J102PolicyPreimage());
}

/** The reviewable projection: the whole contract, with its own digest attached. */
export function v5J102Projection() {
  const preimage = v5J102PolicyPreimage();
  return deepFreeze({
    ...preimage,
    policy_digest: digest(preimage),
    user_corrections: V5_J102_USER_CORRECTIONS.map(c => ({ ...c })),
    // THE MATRIX TRAVELS WITH THE CONTRACT IT DESCRIBES. It is a pure
    // derivative of `transitions` above and adds no information to the policy,
    // so it is deliberately NOT in the preimage: putting it there would move the
    // policy digest for a restatement, and the digest is meant to move when the
    // policy does.
    transition_truth_table: v5J102TransitionTruthTable(),
    settled_decisions: V5_J102_SETTLED_DECISION_IDS.map(id => ({
      decision_id: id, ...V5_J102_SETTLED_DECISIONS[id],
    })),
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Load-time self-checks. Each guards an invariant a later edit could break
// silently, and each fails at IMPORT rather than at the first request.
// ---------------------------------------------------------------------------

for (const [transition_id, t] of Object.entries(TRANSITIONS)) {
  if (!V5_J102_SUBJECT_KINDS.includes(t.subject_kind)) {
    throw new V5J102Error("contract_self_check_failed",
      `${transition_id} acts on unregistered subject kind "${t.subject_kind}"`);
  }
  for (const set of t.evidence_alternatives) {
    for (const kind of set) {
      if (!V5_J102_EVIDENCE_KINDS.includes(kind)) {
        throw new V5J102Error("contract_self_check_failed",
          `${transition_id} requires unregistered evidence kind "${kind}"`);
      }
      // BLOCK-2, made structural. Every evidence kind a transition consumes must
      // bind to the SUBJECT KIND that transition moves, or the binding check in
      // the evaluator could never succeed and the transition would be dead —
      // which is a worse failure than the unbound one it replaces, and a silent
      // one. A new evidence kind that forgets its binding fails at import.
      if (EVIDENCE_KIND_TABLE[kind].binds_subject_kind !== t.subject_kind) {
        throw new V5J102Error("contract_self_check_failed",
          `${transition_id} moves a ${t.subject_kind} but its evidence kind "${kind}" binds to ` +
          `"${EVIDENCE_KIND_TABLE[kind].binds_subject_kind}"`);
      }
    }
  }
  for (const id of t.decision_refs) {
    if (!V5_J102_SETTLED_DECISION_IDS.includes(id)) {
      throw new V5J102Error("contract_self_check_failed",
        `${transition_id} cites unsettled decision "${id}"`);
    }
  }
  if (!V5_J102_REVERSIBILITY.includes(t.reversibility)) {
    throw new V5J102Error("contract_self_check_failed",
      `${transition_id} declares unregistered reversibility "${t.reversibility}"`);
  }
  if (t.from !== undefined) {
    for (const [axis, values] of Object.entries(t.from)) {
      const registered = SUBJECT_ENUMS[t.subject_kind][axis];
      if (registered === undefined) {
        throw new V5J102Error("contract_self_check_failed",
          `${transition_id} names prerequisite axis "${axis}", which is not a state axis of ${t.subject_kind}`);
      }
      for (const value of values) {
        if (!registered.includes(value)) {
          throw new V5J102Error("contract_self_check_failed",
            `${transition_id} names unregistered state "${value}" on ${axis}`);
        }
      }
    }
  }
}

// THE TRUTH TABLE'S GUARDS ARE ALL LIVE, IN BOTH DIRECTIONS.
//
// A guard that admits every value it can see is not a guard — it is a branch no
// input reaches, and the matrix would report it as covered while proving
// nothing. A guard that refuses every value is the opposite failure and worse:
// every transition it covers would be unreachable. Neither is visible by reading
// the transition table, because both arise from the RELATIONSHIP between a
// declared list and the vocabulary it draws from, and both fail at import here.
{
  const cells = v5J102TransitionTruthTable().cells;
  for (const guard of V5_J102_TRANSITION_GUARDS) {
    const own = cells.filter(c => c.guard === guard.guard);
    if (own.length === 0) {
      throw new V5J102Error("contract_self_check_failed",
        `the truth table enumerates no cell for the "${guard.guard}" guard`);
    }
    if (!own.some(c => c.guard_admits)) {
      throw new V5J102Error("contract_self_check_failed",
        `the "${guard.guard}" guard admits no value; every transition it covers would be unreachable`);
    }
    if (!own.some(c => !c.guard_admits)) {
      throw new V5J102Error("contract_self_check_failed",
        `the "${guard.guard}" guard refuses no value, so it is not a guard`);
    }
  }
}

// THE INITIALIZATION TABLE, held to the same invariants as the transition table
// AND to two of its own: an initialization may not create a subject a transition
// creates as a coupled fact, and the state it creates must be one the vocabulary
// registers. The first keeps the two doors disjoint — an engagement created
// outside establish-client-and-engagement would be a Client with no coupled
// status change, and a deal created outside commit-winning-property would be a
// Deal from no commitment, which is the exact thing Q078 removes.
const COUPLED_CREATED_SUBJECT_KINDS = Object.freeze(["engagement", "deal"]);

// ONE DOOR PER KIND. Two initializations creating one subject kind would collapse
// silently in the policy preimage's `by_initialization` map — Object.fromEntries
// keeps the last — so the projection would report one door where two exist, which
// is the one shape this file's own self-checks would otherwise miss.
const INITIALIZED_KIND_COUNTS = new Map();
for (const c of Object.values(INITIALIZATIONS)) {
  INITIALIZED_KIND_COUNTS.set(c.subject_kind,
    (INITIALIZED_KIND_COUNTS.get(c.subject_kind) ?? 0) + 1);
}

for (const [initialization_id, c] of Object.entries(INITIALIZATIONS)) {
  if (!V5_J102_SUBJECT_KINDS.includes(c.subject_kind)) {
    throw new V5J102Error("contract_self_check_failed",
      `${initialization_id} creates unregistered subject kind "${c.subject_kind}"`);
  }
  if (INITIALIZED_KIND_COUNTS.get(c.subject_kind) !== 1) {
    throw new V5J102Error("contract_self_check_failed",
      `${INITIALIZED_KIND_COUNTS.get(c.subject_kind)} initializations create a ` +
      `${c.subject_kind}; one kind has exactly one creation door, or the policy ` +
      "projection reports one where several exist");
  }
  // THE CREATED STATE IS THE EARLIEST DECLARED STATE OF ITS KIND, and this is the
  // check that makes "creation reaches nothing an evidence-bound transition is
  // supposed to establish" structural rather than a reading of three literals. A
  // later edit setting `assignment_phase: "committed"` or `negotiation_state:
  // "loi_accepted"` passes every other check in this file — registered value,
  // right kind, cited decisions, checked parent — and the SQL parity suite would
  // faithfully follow the map to the same wrong place. The vocabularies are
  // declared in lifecycle order, so index 0 is the earliest state each axis has.
  for (const [axis, value] of Object.entries(c.initial_state)) {
    const registered = SUBJECT_ENUMS[c.subject_kind][axis];
    if (registered !== undefined && registered[0] !== value) {
      throw new V5J102Error("contract_self_check_failed",
        `${initialization_id} creates a ${c.subject_kind} at ${axis} "${value}", and the ` +
        `earliest declared ${axis} is "${registered[0]}"; a creation carries no evidence, ` +
        "so it may not reach a state an evidence-bound transition establishes");
    }
  }
  if (COUPLED_CREATED_SUBJECT_KINDS.includes(c.subject_kind)) {
    throw new V5J102Error("contract_self_check_failed",
      `${initialization_id} would create a ${c.subject_kind}, which is a COUPLED creation of a ` +
      "transition; creating one outside that transition would be a client status or a Deal with " +
      "no commitment behind it");
  }
  for (const [axis, value] of Object.entries(c.initial_state)) {
    const registered = SUBJECT_ENUMS[c.subject_kind][axis];
    if (registered !== undefined && !registered.includes(value)) {
      throw new V5J102Error("contract_self_check_failed",
        `${initialization_id} creates a ${c.subject_kind} with unregistered ${axis} "${value}"`);
    }
  }
  for (const id of c.decision_refs) {
    if (!V5_J102_SETTLED_DECISION_IDS.includes(id)) {
      throw new V5J102Error("contract_self_check_failed",
        `${initialization_id} cites unsettled decision "${id}"`);
    }
  }
  for (const actor_class of c.permitted_actor_classes) {
    if (!V5_J102_ACTOR_CLASSES.includes(actor_class)) {
      throw new V5J102Error("contract_self_check_failed",
        `${initialization_id} admits unregistered actor class "${actor_class}"`);
    }
  }
  if (c.parent !== null && !V5_J102_SUBJECT_KINDS.includes(c.parent.kind)) {
    throw new V5J102Error("contract_self_check_failed",
      `${initialization_id} names unregistered parent kind "${c.parent.kind}"`);
  }
  // A parent that is named and never CHECKED would be a reference with no
  // prerequisite behind it, which is how an assignment ends up under a lapsed
  // engagement.
  if (c.parent !== null &&
      !c.required_context.some(rule => rule.subject === c.parent.kind)) {
    throw new V5J102Error("contract_self_check_failed",
      `${initialization_id} creates a row under a ${c.parent.kind} it never checks`);
  }
  for (const field of c.declared_identifiers) {
    if (!INITIALIZATION_DECLARED_KEYS.includes(field)) {
      throw new V5J102Error("contract_self_check_failed",
        `${initialization_id} needs declared identifier "${field}", which the closed request cannot carry`);
    }
  }
  // Every fact the event states must be a field of the row that was created, so
  // the history cannot say something the state does not.
  const created_fields = SUBJECT_SHAPES[c.subject_kind].keys;
  for (const field of c.event_detail_fields) {
    if (!created_fields.includes(field)) {
      throw new V5J102Error("contract_self_check_failed",
        `${initialization_id} states "${field}" in its event and the created ${c.subject_kind} has no such field`);
    }
  }
}

for (const [kind, contract] of Object.entries(EVIDENCE_KIND_TABLE)) {
  if (!Object.prototype.hasOwnProperty.call(contract, "binds_subject_kind")) {
    throw new V5J102Error("contract_self_check_failed",
      `evidence kind "${kind}" declares no subject binding; an unbound evidence kind is the BLOCK-2 defect`);
  }
  if (contract.binds_subject_kind !== null &&
      !V5_J102_SUBJECT_KINDS.includes(contract.binds_subject_kind)) {
    throw new V5J102Error("contract_self_check_failed",
      `evidence kind "${kind}" binds to unregistered subject kind "${contract.binds_subject_kind}"`);
  }
  if (contract.requires_author_class !== undefined) {
    if (!V5_J102_ACTOR_CLASSES.includes(contract.requires_author_class)) {
      throw new V5J102Error("contract_self_check_failed",
        `evidence kind "${kind}" requires unregistered author class "${contract.requires_author_class}"`);
    }
    // An author requirement wider than the performer requirement would be a
    // rule that reads as a restriction and is not one.
    if (!contract.permitted_actor_classes.includes(contract.requires_author_class)) {
      throw new V5J102Error("contract_self_check_failed",
        `evidence kind "${kind}" requires an author class its own permitted actors exclude`);
    }
  }
}

for (const [field, field_class] of Object.entries(V5_J102_FIELD_CLASS_REGISTRY)) {
  if (!V5_J102_FIELD_CLASSES.includes(field_class)) {
    throw new V5J102Error("contract_self_check_failed",
      `the field-class registry gives "${field}" the unregistered class "${field_class}"`);
  }
}
if (V5_J102_UNCLASSIFIED_FIELD_POLICY.routine_fields_registered !==
    Object.values(V5_J102_FIELD_CLASS_REGISTRY).filter(c => c === "routine").length) {
  throw new V5J102Error("contract_self_check_failed",
    "the unclassified-field policy no longer describes the registry's routine entries");
}

/**
 * THE DELIBERATE GUARD COLLISIONS, and there are exactly eight.
 *
 * The two guards run only on keys a schema does not accept, so an accepted key
 * that happens to contain a guard fragment is silently exempt. That exemption is
 * correct for these eight and requires review for any additional key, so the set is
 * enumerated with its reasons and the audit below fails at import the moment a
 * new one appears. Three shapes account for all of them:
 *
 *   THE SERVER-SUPPLIED VALUES — `actor` and `owner_slug`. Both come from the
 *   record layer, and the store refuses either from a caller by name.
 *   THE LOADED EVIDENCE — `exception_approval`, which is validated by
 *   assertLifecycleEvidence like any other evidence record rather than read.
 *   THE LEGACY AND ATTESTATION FIELDS — `executed_instrument_evidence`,
 *   `legacy_closed_flag`, `closed`, `attested_by` and `attested_at`. These describe what an OLD
 *   row or an outside census SAYS. They are not assertions about J102 state; they
 *   are the input a classification or a comparison is run against, and every
 *   function reading one treats it as a claim to check rather than a fact.
 */
const DELIBERATE_GUARD_COLLISIONS = Object.freeze({
  actor: "the server-derived principal; assertActor additionally requires derived_by to name a server derivation",
  owner_slug: "the current owner, read off trusted state for Q103's ownership projection",
  exception_approval: "a LOADED multi_target_exception_approval evidence record, validated as evidence",
  attested_by: "who attested a supplied caller census, which v5J102MigrationReadiness reports and never trusts",
  attested_at: "the supplied census timestamp, reported as a claim and never sufficient to authorize caller retirement",
  executed_instrument_evidence: "a LOADED execution evidence record a legacy row is classified from, kind-checked against V5_J102_EXECUTION_EVIDENCE_KINDS",
  legacy_closed_flag: "the OLD row's own closed column, read to detect the coupled-fact defect Q082 removes, never copied into a J102 state",
  closed: "the OLD row's closed column on the shadow-comparison side, compared against the projection and never written back",
});

for (const [label, keys] of Object.entries({
  request: REQUEST_KEYS, related: RELATED_KEYS, declared: DECLARED_KEYS,
  initialization_request: INITIALIZATION_REQUEST_KEYS,
  initialization_declared: INITIALIZATION_DECLARED_KEYS,
  constraint: CONSTRAINT_KEYS, merge: MERGE_KEYS, edit: EDIT_KEYS,
  salesforce: SALESFORCE_KEYS, legacy_row: LEGACY_ROW_KEYS, readiness: READINESS_KEYS,
  census: CENSUS_KEYS, ownership: OWNERSHIP_KEYS, automation: AUTOMATION_KEYS,
  compat: COMPAT_KEYS, shadow: SHADOW_KEYS, shadow_legacy: SHADOW_LEGACY_KEYS,
  model_proposal: MODEL_PROPOSAL_KEYS,
})) {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(DELIBERATE_GUARD_COLLISIONS, key)) continue;
    const normalized = key.toLowerCase();
    for (const fragment of [...V5_J102_AUTHORITY_INJECTION_FRAGMENTS,
      ...V5_J102_ASSERTED_FACT_FRAGMENTS]) {
      if (normalized.includes(fragment)) {
        throw new V5J102Error("contract_self_check_failed",
          `the ${label} key "${key}" collides with guard fragment "${fragment}" and is not one of the declared exemptions`);
      }
    }
  }
}

export { DELIBERATE_GUARD_COLLISIONS as V5_J102_DELIBERATE_GUARD_COLLISIONS };
