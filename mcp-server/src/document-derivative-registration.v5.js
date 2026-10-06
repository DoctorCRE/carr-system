// DoctorCRE v5 slice V5-F01 — the DOCUMENT half of the derivative-registration
// rule.
//
// WHY THIS FILE EXISTS. The reviewed F01 phase already binds one derived record
// to its original automatically: ops.f01_record_proposal writes a parsed proposal
// and its ops.f01_derivative_link row in one transaction, so a proposal cannot
// complete without naming the artifact it was parsed from. Document versions have
// no such edge. ops.f01_record_document writes a version and stops, and
// ops.f01_derivative_link has never held a row for a document. So the settled
// producer rule — every workflow that creates a derived record registers a link
// to the original BEFORE that derivative is considered complete — is currently
// true of proposals and simply absent for documents.
//
// WHAT THIS FILE ADDS, and it is deliberately one seam wide:
//
//   * A CLOSED, THREE-VALUED PROVENANCE VOCABULARY for one document version.
//     Derived from a stored artifact; original first-party work; legacy or
//     imported provenance nobody knows. Exactly one of the three, always stated,
//     never defaulted, never inferred.
//   * An EXACT, IMMUTABLE BINDING for the first of those three: this document
//     version came from that stored corporate artifact, evaluated by the reviewed
//     kernel's own evaluateDerivativeRegistration and stored as an ordinary
//     ops.f01_derivative_link row through the existing private SQL writer.
//   * A STORED PROVENANCE STATEMENT for all three, so that "original" and
//     "unknown" are things the record layer SAYS rather than things a reader
//     infers from an absent link. An absent link has always meant nothing at all,
//     and that is exactly why the other two cases need a row of their own.
//
// WHAT THIS FILE IS NOT, said plainly because every one of these is a way this
// seam could quietly become something worse:
//
//   * NOT A SECOND AUTHORITY. It compiles no registry, ships no policy, stores
//     nothing and decides no trust. The producer is the authenticated principal
//     the persistence tail already derives from its transaction context; there is
//     no producer allow-list here and no flag that confers one.
//   * NOT A COVERAGE CLAIM. Every record it builds carries
//     establishes_coverage: false and is_exhaustive_inventory: false in its own
//     hashed bytes. Registering a document's source says something true about ONE
//     document version and nothing whatever about whether some other derivative
//     of that artifact exists unregistered. ops.f01_derivative_coverage still
//     answers "unknown" for every artifact, and deletion still fails closed.
//   * NOT A DELETION PERMISSION. permits_deletion: false, on every record and in
//     every answer.
//   * NOT AN INFERENCE ENGINE. A source artifact is NEVER derived from a
//     document's own content digest, from its sealed object-storage bytes, or
//     from a OneDrive drive/item identifier. Those are the document's own bytes
//     and its own filing location; treating either as provenance would manufacture
//     an origin nobody recorded. Both prohibitions are asserted in the stored
//     record's hashed bytes and enforced as named refusals below.
//
// EFFECTS. None. Every function here is pure: no filesystem, no network, no
// database, no provider, no scheduler, no environment and no clock. `now`, the
// authenticated principal, the loaded source artifact and the loaded prior
// provenance all arrive from the caller, exactly as they do in the reviewed
// persistence tail. V5_NO_EFFECTS rides on every answer to say so in the record.
//
// INTEGRATION IS NOT COMPLETE FROM THIS FILE ALONE, and it says so out loud in
// code rather than in a comment. documentSourceIntegrationGaps() below RECOMPUTES
// which parent hunks are still unlanded from the core constants this module
// imports, so the statement cannot go stale, and
// assertDocumentSourceIntegrationComplete() throws while any of them is open. The
// four hunks live in files this change deliberately does not touch while they are
// under review:
//
//   1. record-source-authority-store.v5.js — "stored_document_source_provenance"
//      in V5_F01_STORE_RECORD_KINDS. Until it lands,
//      documentSourceProvenanceEnvelope() refuses by name rather than
//      hand-rolling a second copy of the envelope contract.
//   2. record-source-authority.v5.js — V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND in
//      V5_F01_RESERVED_DERIVATIVE_KINDS, so the PUBLIC registration surface
//      refuses a kind this contract's own writer produces. The candidate schema
//      already replaces ops.f01_reserved_derivative_kinds() with both kinds; the
//      kernel mirror is the parent's, and until it lands a public caller is
//      refused by the database with a raise instead of by the kernel with a named
//      answer.
//   3. record-source-authority-store.v5.js — recordDocumentIdentity must call the
//      six-argument ops.f01_record_document with the provenance and (when
//      derived) the derivative-link envelope. Not detectable from here.
//   4. domain.sql — the private-helper name lists, the direct-DML writer
//      alternation and the reserved-kind list, all enumerated in
//      ops/document-derivative-registration.candidate.sql section 6. Not
//      detectable from here.
//
// NOTHING AT MODULE EVALUATION READS A STORE BINDING, and that is a rule rather
// than an accident. The parent hunk makes record-source-authority-store.v5.js
// import THIS module, which closes an ES-module cycle: if the store is the entry
// point, its own `export const` bindings are still in the temporal dead zone while
// this module's body runs, so a top-level read of V5_F01_DERIVED_ONLY_FIELDS or
// V5_F01_STORE_RECORD_KINDS would throw a ReferenceError at import time and take
// the whole persistence tail with it. Every use of a store binding here is
// therefore inside a function body, evaluated when it is called; the load-time
// self-checks that used to run at module scope are exported as
// assertDocumentSourceContract() and invoked by the test suite instead.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_F01_AUTHORITY_INJECTION_FRAGMENTS,
  V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION,
  V5_F01_DOCUMENT_SCHEMA_VERSION,
  V5_F01_RESERVED_DERIVATIVE_KINDS,
  evaluateDerivativeRegistration,
  projectDocumentIdentity,
} from "./record-source-authority.v5.js";
import {
  V5_F01_DERIVED_ONLY_FIELDS,
  V5_F01_STORE_RECORD_KINDS,
  storedDerivativeLinkRecord,
  storedDocumentRecord,
  v5F01StoreEnvelope,
} from "./record-source-authority-store.v5.js";

export { V5_NO_EFFECTS };

export const V5_F01_DOCUMENT_SOURCE_SCHEMA_VERSION =
  "doctorcre-v5-f01-document-source-provenance.v1";

export const V5_F01_STORED_DOCUMENT_SOURCE_SCHEMA_VERSION =
  "doctorcre-v5-f01-stored-document-source-provenance.v1";

/** The store record_kind the parent must register. See the header note. */
export const V5_F01_DOCUMENT_SOURCE_RECORD_KIND = "stored_document_source_provenance";

/**
 * The derivative kind a document version is registered under.
 *
 * A CONSTANT, NOT A CALLER LABEL, for exactly the reason the kernel's
 * V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND is one: the producer that registers it
 * is a workflow inside this contract, and the kind it produces is part of that
 * workflow's identity rather than a name the caller may choose. A caller that
 * could choose it could register one document version twice under two kinds and
 * defeat the one-derivative-one-original index in ops.
 */
export const V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND = "f01_document_version";

/**
 * THE THREE HONEST ANSWERS about where one document version came from.
 *
 * There is deliberately no fourth. "Probably derived", "no link found" and "not
 * recorded yet" are all `legacy_provenance_unknown` wearing a more confident hat,
 * and collapsing them into "original" is precisely the invention this module
 * exists to make impossible.
 *
 *   derived_from_stored_artifact  A producer workflow made this version FROM a
 *                                 stored corporate artifact. The artifact is
 *                                 loaded, the binding is exact, and an
 *                                 ops.f01_derivative_link row is written in the
 *                                 same transaction as the document version.
 *   original_first_party          This version was authored here. It has no
 *                                 corporate source artifact because there is
 *                                 none — not because nobody registered one. The
 *                                 declaring workflow states the basis on which it
 *                                 says so, and no link row is written.
 *   legacy_provenance_unknown     This version predates the rule, or arrived by
 *                                 import, and where it came from is not known.
 *                                 Recorded as unknown. It is NEVER upgraded to
 *                                 "original" by the absence of evidence, and the
 *                                 stored row says so in its own hashed bytes.
 */
export const V5_F01_DOCUMENT_PROVENANCE_STATES = Object.freeze([
  "derived_from_stored_artifact",
  "original_first_party",
  "legacy_provenance_unknown",
]);

const DERIVED_STATE = "derived_from_stored_artifact";

const DIGEST_REF = /^sha256:[0-9a-f]{64}$/;
// The same external-identifier shape the kernel accepts, reproduced rather than
// imported because the kernel does not export its validators and this change must
// not widen the kernel's surface while it is under review. The test asserts the
// two agree on a shared corpus, so drift is a failure rather than a divergence.
const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

/**
 * The code points an identifier may not contain: C0 and C1 controls, the
 * zero-width and directional-format characters, the invisible-operator block, the
 * bidirectional isolates, and the byte-order mark. An identifier that renders as
 * another identifier is an identity split waiting to happen, so it is refused
 * rather than normalized.
 *
 * WRITTEN AS CODE POINTS, NEVER AS A CHARACTER-CLASS LITERAL. The kernel makes
 * the same choice for its KEY_SEPARATOR and for the same reason: a source file
 * that embeds the very characters it exists to refuse reads as binary to file(1),
 * to rg and to git diff, so the module that refuses invisible characters in its
 * inputs could not itself be reviewed as text. Ranges are ordinary hex integers
 * here, so the file stays plain ASCII and the intent is checkable by eye.
 */
const UNSAFE_CODE_POINT_RANGES = Object.freeze([
  [0x0000, 0x001f], // C0 controls
  [0x007f, 0x009f], // DEL and the C1 controls
  [0x200b, 0x200f], // zero-width space/joiner and the LTR/RTL marks
  [0x202a, 0x202e], // the bidirectional embedding and override controls
  [0x2060, 0x2064], // word joiner and the invisible operators
  [0x2066, 0x2069], // the bidirectional isolates
  [0xfeff, 0xfeff], // byte-order mark / zero-width no-break space
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

export class V5F01DocumentSourceError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5F01DocumentSourceError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5F01DocumentSourceError(code, message, detail);
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

/**
 * A getter can answer differently on each read, so a decision taken from one read
 * cannot be trusted to describe the input that was validated. Refuse the shape.
 */
function assertNoAccessorsOrHiddenKeys(object, path) {
  if (Object.prototype.hasOwnProperty.call(object, "__proto__")) {
    fail("prototype_key_refused",
      `${path}.__proto__ is an own key; the shape is refused rather than read`, { path });
  }
  if (Object.getOwnPropertySymbols(object).length > 0) {
    fail("symbol_key_refused", `${path} carries symbol keys, which would ride along unread`, { path });
  }
  for (const key of Object.getOwnPropertyNames(object)) {
    const descriptor = Object.getOwnPropertyDescriptor(object, key);
    if (descriptor.get !== undefined || descriptor.set !== undefined) {
      fail("accessor_property_refused",
        `${path}.${key} is an accessor; a value that can change between reads cannot bind a write`,
        { path: `${path}.${key}` });
    }
  }
}

/**
 * The two guards, reused BY IMPORT from the kernel and the store rather than
 * copied, so a fragment added to either list is enforced here without anybody
 * remembering to mirror it.
 *
 *   AUTHORITY INJECTION — a field purporting to confer authority. The producer is
 *     the authenticated principal; nothing a caller writes can make it one.
 *   DERIVED-VALUE INJECTION — a field the server owns. `produced_at`,
 *     `registered_by`, `recorded_at`, `derivative_coverage` and every digest of
 *     something this seam computes are on the store's list already, so a caller
 *     that tries to state when it produced a document, or how complete the
 *     coverage now is, is refused by name rather than by a generic unknown field.
 */
function assertNoInjectedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (allowed.includes(key)) continue;
    const normalized = key.toLowerCase();
    for (const fragment of V5_F01_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_authority_field_refused",
          `${path}.${key} names authority the caller cannot supply; the handler derives the producer principal`,
          { path: `${path}.${key}`, key, fragment });
      }
    }
    if (V5_F01_DERIVED_ONLY_FIELDS.includes(normalized)) {
      fail("caller_derived_field_refused",
        `${path}.${key} is derived by the server or loaded from the database; a caller may not supply it`,
        { path: `${path}.${key}`, key });
    }
  }
}

function assertClosed(object, allowed, required, path) {
  if (!isPlainObject(object)) fail("invalid_shape", `${path} must be a plain object`, { path });
  assertNoAccessorsOrHiddenKeys(object, path);
  assertNoInjectedKeys(object, allowed, path);
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
  for (const key of required) {
    if (!(key in object) || object[key] === undefined || object[key] === null) {
      fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
  }
  return object;
}

/**
 * THE LENGTH BOUND IS COUNTED IN UTF-16 CODE UNITS, because `String.prototype
 * .length` is, and the number matters on both sides of the seam. PostgreSQL's
 * length() counts CODE POINTS, so a bound written as length(x) <= 512 there would
 * admit 512 astral characters — 1024 code units — that this function refuses. The
 * candidate schema therefore mirrors the bound through
 * ops.f01_docsource_utf16_length(), which recomputes the same count, rather than
 * through length(); the two are the same number for the same string, and the SQL
 * half is neither weaker nor stricter than this one. The test suite pins the
 * arithmetic on both sides so the equivalence is checked rather than assumed.
 */
function assertSafeText(value, path, { maxLength = 512 } = {}) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  if (value.length > maxLength) {
    fail("text_too_long", `${path} may be at most ${maxLength} characters`,
      { path, length: value.length });
  }
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
    fail("malformed_unicode", `${path} contains an unpaired surrogate`, { path });
  }
  if (hasUnsafeCodePoint(value)) {
    fail("unsafe_unicode",
      `${path} contains a control, bidirectional or invisible format character`, { path });
  }
  if (value.normalize("NFC") !== value) {
    fail("non_canonical_unicode",
      `${path} is not in Unicode NFC; it is refused rather than normalized`, { path });
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

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !DIGEST_REF.test(value)) {
    fail("invalid_digest",
      `${path} must be a "sha256:" reference over 64 lower-case hex characters`, { path });
  }
  return value;
}

/**
 * Timestamps are parsed, never inferred, and the calendar is checked against the
 * LITERAL fields BEFORE parsing — Date.parse silently normalizes 2026-02-31 into
 * 3 March, and a comparison against an instant nobody wrote is not the comparison
 * the caller asked for. Same rule as the kernel's assertInstant, for the same
 * reason and under the same refusal name.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const daysInMonth = mo === 2
    ? ((y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28)
    : ([4, 6, 9, 11].includes(mo) ? 30 : 31);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth ||
      Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path });
  return parsed;
}

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `${path} must be "${ORGANIZATION_TENANT_ID}"; the handler derives it`,
      { path, expected: ORGANIZATION_TENANT_ID });
  }
  return value;
}

// ---------------------------------------------------------------------------
// The caller-facing declaration.
//
// FLAT, SMALL AND CLOSED. The producing workflow names WHICH of the three
// provenance answers is true, and — only in the derived case — which stored
// artifact and which workflow. Everything else is derived or loaded: the tenant,
// the producer principal, the instant the production is bound to, the source
// artifact's own creation time, the document's own digest, the evidence
// reference, and every coverage question. The derived guard above refuses each of
// them by name, so a caller learns what it attempted rather than being told a
// field is unknown.
//
// THE EVIDENCE IS DERIVED, NOT DECLARED, and that is a deliberate difference from
// the generic register-derivative-source-link surface. The evidence that this
// document version was produced IS the stored document version; a caller that
// could name arbitrary evidence for a record the server has just built could
// point the evidence at something else entirely.
// ---------------------------------------------------------------------------

const DECLARATION_KEYS = Object.freeze([
  "provenance_state", "source_artifact_digest",
  "producer_workflow", "producer_run_ref", "basis_statement",
]);

const DERIVED_REQUIRED = Object.freeze([
  "provenance_state", "source_artifact_digest", "producer_workflow", "producer_run_ref",
]);

const UNDERIVED_REQUIRED = Object.freeze(["provenance_state", "basis_statement"]);

/**
 * Validate one caller declaration. SHAPE ONLY; every semantic question is a
 * decision below, so the two kinds of no stay separate exactly as they do in the
 * kernel: an unreadable request throws, a readable one that must not be honoured
 * returns a refusal the caller may record.
 */
export function assertDocumentSourceDeclaration(declaration) {
  if (!isPlainObject(declaration)) {
    fail("invalid_shape", "source must be a plain object", { path: "source" });
  }
  assertNoAccessorsOrHiddenKeys(declaration, "source");
  const state = declaration.provenance_state;
  if (typeof state !== "string" || !V5_F01_DOCUMENT_PROVENANCE_STATES.includes(state)) {
    fail("unknown_document_provenance_state",
      `"${String(state)}" is not one of the three registered document provenance states`,
      { path: "source.provenance_state", value: typeof state === "string" ? state : null,
        registered: [...V5_F01_DOCUMENT_PROVENANCE_STATES] });
  }
  const derived = state === DERIVED_STATE;
  assertClosed(declaration, DECLARATION_KEYS,
    derived ? DERIVED_REQUIRED : UNDERIVED_REQUIRED, "source");

  if (derived) {
    return deepFreeze({
      provenance_state: state,
      source_artifact_digest: assertDigestRef(declaration.source_artifact_digest,
        "source.source_artifact_digest"),
      producer_workflow: assertExternalIdent(declaration.producer_workflow,
        "source.producer_workflow", { maxLength: 128 }),
      producer_run_ref: assertExternalIdent(declaration.producer_run_ref,
        "source.producer_run_ref", { maxLength: 255 }),
      basis_statement: declaration.basis_statement === undefined ||
        declaration.basis_statement === null
        ? null
        : assertSafeText(declaration.basis_statement, "source.basis_statement", { maxLength: 512 }),
    });
  }

  // A NON-DERIVED DECLARATION STATES ITS BASIS, and the basis is required rather
  // than optional. "This is an original" and "nobody knows where this came from"
  // are both claims a human may later have to weigh, and a claim with no stated
  // basis is the one that gets read as fact by whoever finds it next. The three
  // derived-only fields are echoed as whatever the caller supplied so the
  // decision below can refuse them BY NAME rather than dropping them here.
  return deepFreeze({
    provenance_state: state,
    source_artifact_digest: declaration.source_artifact_digest ?? null,
    producer_workflow: declaration.producer_workflow ?? null,
    producer_run_ref: declaration.producer_run_ref ?? null,
    basis_statement: assertSafeText(declaration.basis_statement, "source.basis_statement",
      { maxLength: 512 }),
  });
}

// ---------------------------------------------------------------------------
// The identity of one document version, as a derivative.
// ---------------------------------------------------------------------------

/**
 * The derivative id for one document version: (document_id, version_no).
 *
 * WHY THE PAIR AND NOT THE DIGEST. ops.f01_derivative_link is unique on
 * (tenant, derivative_kind, derivative_id), and that index is what makes "one
 * derivative has one original" structural rather than hopeful. Keying on the
 * document DIGEST would make every re-record of the same version a different
 * derivative, and the index would then refuse nothing at all. Keying on the pair
 * means a second registration of the same document version against a different
 * artifact is exactly what it looks like — a rewrite of where that version came
 * from — and it refuses, in the database as well as here.
 */
export function documentVersionDerivativeId(document_id, version_no) {
  assertExternalIdent(document_id, "document_id", { maxLength: 128 });
  if (!Number.isSafeInteger(version_no) || version_no < 1) {
    fail("invalid_shape", "version_no must be a positive safe integer", { path: "version_no" });
  }
  // THE SEPARATOR IS CHECKED RATHER THAN ASSUMED. ":" is inside EXTERNAL_IDENT,
  // so a document_id may legitimately contain one, and ("a:1", 2) would otherwise
  // fold to the same key as ("a", 12) reached from ("a:1", 2) — two document
  // versions sharing one provenance identity, one silently shadowing the other.
  // A colon in the document id is refused here rather than folded away.
  if (document_id.includes(":")) {
    fail("document_id_contains_separator",
      'a document_id may not contain ":"; it is the separator that keeps one document version\'s derivative identity distinct from another\'s',
      { path: "document_id", document_id });
  }
  return `${document_id}:${version_no}`;
}

// ---------------------------------------------------------------------------
// The stored provenance record.
// ---------------------------------------------------------------------------

/**
 * One document version's provenance statement, as the record layer stores it.
 *
 * THE SIX SELF-DESCRIBING CLAIMS at the foot are hashed WITH the record, and the
 * CHECK constraints in the candidate schema recompute them from the stored bytes.
 * A row that claimed coverage, claimed exhaustiveness, claimed a deletion
 * permission, or admitted that its source artifact had been inferred from the
 * document's own bytes or from its OneDrive identity cannot be inserted at all.
 */
export function storedDocumentSourceProvenanceRecord({
  document_id, version_no, document_digest, provenance_state,
  source_artifact_digest, derivative_link_digest, derivative_kind, derivative_id,
  producer_workflow, producer_run_ref, basis_statement,
  recorded_by, recorded_at,
}) {
  return {
    schema_version: V5_F01_STORED_DOCUMENT_SOURCE_SCHEMA_VERSION,
    document_source_schema_version: V5_F01_DOCUMENT_SOURCE_SCHEMA_VERSION,
    document_identity_schema_version: V5_F01_DOCUMENT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    document_id,
    version_no,
    document_digest,
    provenance_state,
    source_artifact_digest: source_artifact_digest ?? null,
    derivative_link_schema_version: derivative_link_digest == null
      ? null : V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION,
    derivative_link_digest: derivative_link_digest ?? null,
    derivative_kind: derivative_kind ?? null,
    derivative_id: derivative_id ?? null,
    producer_workflow: producer_workflow ?? null,
    producer_run_ref: producer_run_ref ?? null,
    basis_statement: basis_statement ?? null,
    // The six claims the row makes about itself.
    registration_is_provenance: true,
    source_artifact_inferred_from_document_bytes: false,
    source_artifact_inferred_from_onedrive_identity: false,
    is_exhaustive_inventory: false,
    establishes_coverage: false,
    permits_deletion: false,
    recorded_by,
    recorded_at,
  };
}

/**
 * Build the store envelope for one provenance record.
 *
 * REFUSES UNTIL THE PARENT HUNK LANDS, on purpose. v5F01StoreEnvelope validates
 * record_kind against V5_F01_STORE_RECORD_KINDS, which lives in the persistence
 * tail this change does not edit while it is under review. Rather than construct
 * an envelope by hand — which would be a second, drifting copy of the envelope
 * contract — this refuses by name and says which line has to move. The test suite
 * asserts the refusal, so "incomplete without the parent hunk" is a failing case
 * rather than a paragraph in a header.
 */
export function documentSourceProvenanceEnvelope(record, extra = {}) {
  if (!V5_F01_STORE_RECORD_KINDS.includes(V5_F01_DOCUMENT_SOURCE_RECORD_KIND)) {
    fail("store_record_kind_not_registered",
      `"${V5_F01_DOCUMENT_SOURCE_RECORD_KIND}" is not in V5_F01_STORE_RECORD_KINDS; the parent integration hunk in record-source-authority-store.v5.js has not landed, so this seam is incomplete`,
      { record_kind: V5_F01_DOCUMENT_SOURCE_RECORD_KIND,
        registered: [...V5_F01_STORE_RECORD_KINDS] });
  }
  return v5F01StoreEnvelope(V5_F01_DOCUMENT_SOURCE_RECORD_KIND, record, {
    establishes_coverage: false,
    is_exhaustive_inventory: false,
    permits_deletion: false,
    deletes_nothing: true,
    ...extra,
  });
}

// ---------------------------------------------------------------------------
// The decision.
// ---------------------------------------------------------------------------

const BINDING_REQUEST_KEYS = Object.freeze([
  "tenant", "document", "source", "source_artifact", "prior_provenance",
  "prior_document_digest", "recorded_by", "now",
]);

const BINDING_REQUIRED_KEYS = Object.freeze(["tenant", "document", "source", "recorded_by", "now"]);

const PRIOR_PROVENANCE_KEYS = Object.freeze([
  "document_id", "version_no", "document_digest", "provenance_state",
  "source_artifact_digest", "derivative_link_digest",
]);

function bindingResult(fields) {
  return deepFreeze({
    ...fields,
    // Stated in every answer, positive or negative. A document version's source
    // registration records where ONE version came from and does nothing else.
    establishes_coverage: false,
    is_exhaustive_inventory: false,
    permits_deletion: false,
    absent_link_means_verified_absence: false,
    source_artifact_inferred_from_document_bytes: false,
    source_artifact_inferred_from_onedrive_identity: false,
    // Q125's one forbidden inference, restated on this surface too, because this
    // is now a second place a document version can be completed from.
    object_storage_success_implies_official_filing: false,
    neon_success_implies_official_filing: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Decide one document version's source binding, and build the exact document
 * record it completes with.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *
 *   1.  The request must be readable, closed and carry the one server-held tenant.
 *   2.  The declaration must name exactly one of the three provenance states and
 *       carry the fields that state requires and no others.
 *   3.  The DOCUMENT is projected by the reviewed kernel, unchanged and unwrapped.
 *       Every incoherent-state, sealed-bytes and official-copy refusal it makes is
 *       returned here under its own reason, because a document nobody can
 *       coherently record has no provenance worth registering. The one exception
 *       is Q125's `incomplete_official_filing`, which the reviewed store
 *       deliberately PERSISTS rather than discards; it stays recordable here for
 *       the same reason, and the answer says so.
 *   4.  A NON-DERIVED declaration may not name a source artifact, a producer
 *       workflow or a run reference. "Original" that carries an origin is not one
 *       declaration, it is two contradictory ones.
 *   5.  A DERIVED declaration may not name, as its source, any digest that is the
 *       document's OWN bytes — the Neon content digest, the object-storage content
 *       digest, or the OneDrive content digest. Those three are the document, not
 *       its origin, and treating one as provenance is the exact inference this
 *       seam exists to prevent. A OneDrive drive or item identifier cannot reach
 *       this field at all, because it is not a digest; the answer says so by name
 *       so the prohibition is checkable rather than implied.
 *   6.  The DOCUMENT RECORD is built from the kernel's projection plus the values
 *       only the server holds — the prior document digest for the CAS, the derived
 *       principal and the server instant — through the reviewed store's own
 *       storedDocumentRecord. Its digest is what the derivative link binds to as
 *       the derived record's content, because that is the byte string the record
 *       layer actually stores.
 *   7.  PRIOR PROVENANCE IS LOADED, never accepted, AND IT IS THE PRIOR FOR THIS
 *       EXACT VERSION. A second registration of the SAME document version naming a
 *       different source artifact, different bytes or a different provenance state
 *       is a rewrite of where that version came from, and each refuses under its
 *       own name. A LATER VERSION IS A DIFFERENT DERIVATIVE and may legitimately
 *       name a different source, or none: version 2 of a lease abstract can come
 *       from a re-exported lease, or be authored here after version 1 was derived.
 *       So a prior statement about another version refuses rather than being read
 *       across, and the caller loads the statement for the exact (document_id,
 *       version_no) it is completing. There is deliberately no cross-version rule
 *       here; adding one would make a document's history immutable in a way the
 *       settled contract does not say it is.
 *   8.  The SOURCE ARTIFACT IS LOADED, never asserted, and the reviewed kernel's
 *       evaluateDerivativeRegistration decides the binding: absent artifact,
 *       mismatched artifact, a derivative that is its own source, a production
 *       after `now`, and a production preceding the artifact each refuse by their
 *       kernel name. This module reimplements none of it.
 *   9.  Only then allow, returning the exact preimages the record layer stores.
 *
 * WHAT THIS FUNCTION CANNOT DO, and does not pretend to. It cannot decide that a
 * producer is trusted. Trust is the authenticated principal the persistence tail
 * derives from its own transaction context and ops.f01_record_document re-derives
 * for itself; no field here confers it, and `recorded_by` is handed in rather than
 * chosen.
 */
export function evaluateDocumentSourceBinding(request) {
  if (!isPlainObject(request)) {
    fail("invalid_shape", "request must be a plain object", { path: "request" });
  }
  assertNoAccessorsOrHiddenKeys(request, "request");
  for (const key of Object.keys(request)) {
    if (!BINDING_REQUEST_KEYS.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at request`, { path: `request.${key}`, key });
    }
  }
  for (const key of BINDING_REQUIRED_KEYS) {
    if (request[key] === undefined || request[key] === null) {
      fail("missing_field", `request.${key} is required`, { path: `request.${key}` });
    }
  }
  assertTenant(request.tenant, "request.tenant");
  assertInstant(request.now, "request.now");
  const recorded_by = assertExternalIdent(request.recorded_by, "request.recorded_by",
    { maxLength: 128 });
  const prior_document_digest = request.prior_document_digest === undefined ||
    request.prior_document_digest === null
    ? null
    : assertDigestRef(request.prior_document_digest, "request.prior_document_digest");

  // Step 2.
  const declaration = assertDocumentSourceDeclaration(request.source);
  const derived = declaration.provenance_state === DERIVED_STATE;

  // Step 3. The kernel decides the document; this module adds nothing to it.
  const projected = projectDocumentIdentity({
    tenant: ORGANIZATION_TENANT_ID,
    document: request.document,
  });
  const filingIncomplete = projected.decision === "refuse" &&
    projected.reason_id === "incomplete_official_filing";

  const document_id = projected.neon_identity.document_id;
  const version_no = projected.neon_identity.version_no;
  const base = {
    tenant: ORGANIZATION_TENANT_ID,
    document_id,
    version_no,
    provenance_state: declaration.provenance_state,
    source_artifact_digest: derived ? declaration.source_artifact_digest : null,
    producer_workflow: derived ? declaration.producer_workflow : null,
    basis_statement: derived ? null : declaration.basis_statement,
    official_filing_state: projected.official_filing_state,
    prior_document_digest,
    recorded_by,
    recorded_at: request.now,
    document_record: null,
    document_digest: null,
    derivative_id: null,
    derivative_link: null,
    document_recordable: false,
  };

  if (projected.decision !== "allow" && !filingIncomplete) {
    return bindingResult({
      decision: "refuse", reason_id: projected.reason_id, ...base,
      violated_constraint: projected.violated_constraint ?? null,
      records_written: 0,
    });
  }

  // Step 4.
  if (!derived) {
    for (const field of ["source_artifact_digest", "producer_workflow", "producer_run_ref"]) {
      if (declaration[field] !== null) {
        return bindingResult({
          decision: "refuse",
          reason_id: declaration.provenance_state === "original_first_party"
            ? "source_named_by_original_document"
            : "source_named_by_legacy_document",
          ...base, offending_field: `source.${field}`, records_written: 0,
        });
      }
    }
  }

  // Step 5. The three digests that are the DOCUMENT rather than its origin.
  if (derived) {
    const ownBytes = [
      ["neon_identity.content_digest", projected.neon_identity.content_digest],
      ["object_storage_identity.content_digest",
        projected.object_storage_identity === null
          ? null : projected.object_storage_identity.content_digest],
      ["onedrive_identity.content_digest",
        projected.onedrive_identity === null ? null : projected.onedrive_identity.content_digest],
    ];
    for (const [where, value] of ownBytes) {
      if (value !== null && value === declaration.source_artifact_digest) {
        return bindingResult({
          decision: "refuse", reason_id: "document_bytes_are_not_a_source_artifact",
          ...base, offending_field: `document.${where}`, records_written: 0,
        });
      }
    }
  }

  // Step 6. THE DOCUMENT RECORD, built through the reviewed store's own helper so
  // the bytes this binds to are exactly the bytes ops.f01_document_version stores
  // and CHECK-recomputes. Building a second, similar record here would give the
  // derivative link a content digest the database never holds.
  const documentRecord = storedDocumentRecord({
    document_class: projected.document_class,
    neon_identity: projected.neon_identity,
    object_storage_identity: projected.object_storage_identity,
    onedrive_identity: projected.onedrive_identity,
    preparation_state: projected.preparation_state,
    delivery_state: projected.delivery_state,
    signature_state: projected.signature_state,
    validity_state: projected.validity_state,
    version_state: projected.version_state,
    official_filing_state: filingIncomplete
      ? "incomplete_official_filing" : projected.official_filing_state,
    prior_document_digest,
    recorded_by,
    recorded_at: request.now,
  });
  const document_digest = digest(documentRecord);
  // COMPUTED ONLY WHERE IT IS USED. The (document_id, version_no) fold refuses a
  // document_id containing the separator, and that refusal is correct for a
  // DERIVED document — two versions could otherwise share one provenance identity
  // in ops.f01_derivative_link. It would be wrong for an original or a legacy
  // import, which register no link at all: refusing to record those would be this
  // module inventing a naming rule for documents it has no derivative claim over.
  // The matching SQL constraint is conditioned on derivative_id the same way.
  const derivative_id = derived ? documentVersionDerivativeId(document_id, version_no) : null;
  Object.assign(base, {
    document_record: deepFreeze({ ...documentRecord }),
    document_digest,
    derivative_id,
    official_filing_state: documentRecord.official_filing_state,
  });

  // Step 7. A prior provenance row for this version, LOADED. Read before the
  // kernel call so a repointing attempt is named as a repointing rather than as
  // whatever the kernel happens to say about the newly named artifact.
  const prior = request.prior_provenance === undefined || request.prior_provenance === null
    ? null
    : readPriorProvenance(request.prior_provenance);
  if (prior !== null) {
    if (prior.document_id !== document_id || prior.version_no !== version_no) {
      return bindingResult({
        decision: "refuse", reason_id: "prior_provenance_names_another_document_version",
        ...base, prior_document_id: prior.document_id, prior_version_no: prior.version_no,
        records_written: 0,
      });
    }
    if (prior.provenance_state !== declaration.provenance_state) {
      return bindingResult({
        decision: "refuse", reason_id: "document_provenance_state_rebinding_refused",
        ...base, prior_provenance_state: prior.provenance_state, records_written: 0,
      });
    }
    if (prior.source_artifact_digest !== (derived ? declaration.source_artifact_digest : null)) {
      return bindingResult({
        decision: "refuse", reason_id: "document_source_repointing_refused",
        ...base, prior_source_artifact_digest: prior.source_artifact_digest, records_written: 0,
      });
    }
    if (prior.document_digest !== document_digest) {
      // NAMED prior_statement_document_digest, NEVER prior_document_digest. The
      // two are different facts that a single name would have merged: `base`
      // already carries prior_document_digest, which is the CAS OPERAND — the
      // stored current version this write was decided against, and the value
      // ops.f01_record_document compares to ops.f01_document_current. What refuses
      // here is the digest the PRIOR PROVENANCE STATEMENT records for this same
      // version. Spreading `base` first and then re-using its key silently
      // overwrote the CAS operand with the statement's digest, so a refusal
      // reported one as the other and a caller reading the answer would have
      // retried against a version pointer that was never current.
      return bindingResult({
        decision: "refuse", reason_id: "document_source_content_rebinding_refused",
        ...base, prior_statement_document_digest: prior.document_digest,
        records_written: 0,
      });
    }
  }

  if (!derived) {
    return bindingResult({
      decision: "allow",
      reason_id: declaration.provenance_state === "original_first_party"
        ? "document_declared_original_no_source_artifact"
        : "document_declared_legacy_provenance_unknown",
      ...base, document_recordable: true, records_written: 2,
    });
  }

  // Step 8. The kernel decides the binding.
  const registration = evaluateDerivativeRegistration({
    tenant: ORGANIZATION_TENANT_ID,
    registration: {
      source_artifact_digest: declaration.source_artifact_digest,
      derivative: {
        derivative_kind: V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND,
        derivative_id,
        content_digest: document_digest,
      },
      producer: {
        producer_workflow: declaration.producer_workflow,
        producer_run_ref: declaration.producer_run_ref,
      },
      // THE SERVER INSTANT, EVERY TIME. A producer's own clock is not evidence
      // about when the record layer saw the production, and a caller that could
      // state it could place a document version before the artifact it came from.
      produced_at: request.now,
      evidence: { evidence_ref: "stored_document_version", evidence_digest: document_digest },
    },
    source_artifact: request.source_artifact === undefined ? null : request.source_artifact,
    now: request.now,
  });

  if (registration.decision !== "allow") {
    return bindingResult({
      decision: "refuse", reason_id: registration.reason_id, ...base,
      loaded_artifact_digest: registration.loaded_artifact_digest ?? null,
      source_created_at: registration.source_created_at ?? null,
      records_written: 0,
    });
  }

  return bindingResult({
    decision: "allow", reason_id: "document_source_binding_registered", ...base,
    derivative_link: registration.derivative_link,
    produced_at: registration.derivative_link.produced_at,
    document_recordable: true,
    records_written: 3,
  });
}

/** Read one LOADED prior provenance row down to the fields a decision may use. */
function readPriorProvenance(value) {
  assertClosed(value, PRIOR_PROVENANCE_KEYS,
    ["document_id", "version_no", "document_digest", "provenance_state"],
    "request.prior_provenance");
  const state = value.provenance_state;
  if (!V5_F01_DOCUMENT_PROVENANCE_STATES.includes(state)) {
    fail("unknown_document_provenance_state",
      `"${String(state)}" is not a registered document provenance state`,
      { path: "request.prior_provenance.provenance_state" });
  }
  if (!Number.isSafeInteger(value.version_no) || value.version_no < 1) {
    fail("invalid_shape", "request.prior_provenance.version_no must be a positive safe integer",
      { path: "request.prior_provenance.version_no" });
  }
  return Object.freeze({
    document_id: assertExternalIdent(value.document_id,
      "request.prior_provenance.document_id", { maxLength: 128 }),
    version_no: value.version_no,
    document_digest: assertDigestRef(value.document_digest,
      "request.prior_provenance.document_digest"),
    provenance_state: state,
    source_artifact_digest: value.source_artifact_digest === undefined ||
      value.source_artifact_digest === null
      ? null
      : assertDigestRef(value.source_artifact_digest,
        "request.prior_provenance.source_artifact_digest"),
    derivative_link_digest: value.derivative_link_digest === undefined ||
      value.derivative_link_digest === null
      ? null
      : assertDigestRef(value.derivative_link_digest,
        "request.prior_provenance.derivative_link_digest"),
  });
}

// ---------------------------------------------------------------------------
// The records and envelopes, composed.
// ---------------------------------------------------------------------------

function requireAllowedBinding(binding) {
  if (!isPlainObject(binding) || binding.decision !== "allow") {
    fail("binding_not_allowed",
      "only an allowed binding has records; a refusal writes nothing",
      { decision: isPlainObject(binding) ? binding.decision ?? null : null });
  }
  return binding;
}

/**
 * Turn one ALLOWED binding into the records and the two envelopes that do not
 * depend on the parent hunk.
 *
 * SPLIT FROM composeDocumentSourceEnvelopes DELIBERATELY. The document and
 * derivative-link record kinds are already registered in the reviewed store, so
 * those envelopes can be built — and tested byte for byte — today. The provenance
 * envelope cannot, and pretending otherwise by hand-rolling it is exactly the
 * second copy of the envelope contract this module refuses to keep.
 */
export function composeDocumentSourceRecords(binding) {
  requireAllowedBinding(binding);
  const derived = binding.provenance_state === DERIVED_STATE;

  const documentEnvelope = v5F01StoreEnvelope("stored_document_version",
    { ...binding.document_record }, {
      object_storage_success_implies_official_filing: false,
      neon_success_implies_official_filing: false,
    });

  const derivativeEnvelope = derived
    ? v5F01StoreEnvelope("stored_derivative_link", storedDerivativeLinkRecord({
      link: binding.derivative_link,
      registered_by: binding.recorded_by,
      registered_at: binding.recorded_at,
    }), {
      establishes_coverage: false, is_exhaustive_inventory: false,
      permits_deletion: false, deletes_nothing: true,
    })
    : null;

  const provenanceRecord = storedDocumentSourceProvenanceRecord({
    document_id: binding.document_id,
    version_no: binding.version_no,
    document_digest: binding.document_digest,
    provenance_state: binding.provenance_state,
    source_artifact_digest: derived ? binding.source_artifact_digest : null,
    derivative_link_digest: derived ? derivativeEnvelope.record_digest : null,
    derivative_kind: derived ? V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND : null,
    derivative_id: derived ? binding.derivative_id : null,
    producer_workflow: derived ? binding.producer_workflow : null,
    producer_run_ref: derived ? binding.derivative_link.producer_run_ref : null,
    basis_statement: derived ? null : binding.basis_statement,
    recorded_by: binding.recorded_by,
    recorded_at: binding.recorded_at,
  });

  return deepFreeze({
    document_record: { ...binding.document_record },
    document_envelope: documentEnvelope,
    document_digest: documentEnvelope.record_digest,
    provenance_record: provenanceRecord,
    provenance_record_digest: digest(provenanceRecord),
    derivative_envelope: derivativeEnvelope,
    derivative_link_digest: derivativeEnvelope === null ? null : derivativeEnvelope.record_digest,
    // The two properties the SQL writer enforces, restated here so a reader of
    // either half sees the same contract.
    provenance_statement_required: true,
    derivative_link_required: derived,
  });
}

/**
 * The full set ops.f01_record_document writes in ONE transaction or not at all.
 *
 * TWO OR THREE ENVELOPES, NEVER A CHOICE OF ONE. A derived document yields a
 * document version, a provenance statement AND a derivative link; a non-derived
 * one yields the first two and MUST yield no link, because a link row for a
 * document with no source would be provenance pointing at nothing. The SQL writer
 * refuses both wrong shapes — an absent provenance envelope, and a link envelope
 * on a non-derived document — so "atomic and complete" is a property of the write
 * rather than a convention of this composer.
 */
export function composeDocumentSourceEnvelopes(binding) {
  const composed = composeDocumentSourceRecords(binding);
  return deepFreeze({
    ...composed,
    provenance_envelope: documentSourceProvenanceEnvelope(composed.provenance_record),
  });
}

// ---------------------------------------------------------------------------
// The contract self-checks, and the integration this module cannot finish.
// ---------------------------------------------------------------------------

/**
 * The invariants a later edit could break silently, checked on demand.
 *
 * A FUNCTION RATHER THAN A MODULE-SCOPE LOOP, and the reason is the import cycle
 * described in the header. Two of these read V5_F01_DERIVED_ONLY_FIELDS, which is
 * an `export const` in record-source-authority-store.v5.js — the module the parent
 * hunk makes import THIS one. Reading it while this module's body evaluates is a
 * temporal-dead-zone ReferenceError whenever the store is the entry point, which
 * is every time the persistence tail is loaded first. The checks are just as
 * binding run from the test suite, and they cannot take the store down at import
 * time from there.
 *
 * The parent adds the other half of this proof when it integrates: a regression
 * that imports record-source-authority-store.v5.js FIRST and asserts the store
 * still loads. That test cannot be written here, because nothing imports this
 * module yet.
 */
export function assertDocumentSourceContract() {
  // The declaration surface must never name a field either guard refuses, or the
  // contract would be unusable in exactly the case it was written for.
  for (const key of DECLARATION_KEYS) {
    const normalized = key.toLowerCase();
    for (const fragment of V5_F01_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("authority_guard_collides_with_contract",
          `the authority guard would refuse the legitimate field "${key}"`, { key });
      }
    }
    if (V5_F01_DERIVED_ONLY_FIELDS.includes(normalized)) {
      fail("derived_guard_collides_with_contract",
        `the derived guard would refuse the legitimate field "${key}"`, { key });
    }
  }

  // Exactly one of the three states is the derived one, and it is the one the
  // derivative-link path keys on.
  if (!V5_F01_DOCUMENT_PROVENANCE_STATES.includes(DERIVED_STATE) ||
      V5_F01_DOCUMENT_PROVENANCE_STATES.length !== 3) {
    fail("provenance_vocabulary_drift",
      "the three-valued document provenance vocabulary has changed shape");
  }

  // The derivative kind this module registers must not collide with a kind the
  // reviewed kernel already produces, or two producers would compete for one
  // (kind, id) identity in ops.f01_derivative_link.
  if (V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND === "f01_parsed_proposal") {
    fail("derivative_kind_collision",
      "the document derivative kind collides with the parsed-proposal kind");
  }
  return true;
}

/**
 * WHICH PARENT HUNKS ARE STILL OPEN, recomputed rather than remembered.
 *
 * `landed` is true, false, or null where this module cannot see the answer —
 * because the thing to change is a SQL file or a handler body, not a constant it
 * imports. A null is not a pass and is not counted as one:
 * assertDocumentSourceIntegrationComplete() below refuses on anything that is not
 * exactly true, so "we could not check it" never reads as "it was done".
 */
export function documentSourceIntegrationGaps() {
  return deepFreeze([
    {
      gap: "store_record_kind_not_registered",
      where: "mcp-server/src/record-source-authority-store.v5.js",
      what: `add "${V5_F01_DOCUMENT_SOURCE_RECORD_KIND}" to V5_F01_STORE_RECORD_KINDS`,
      landed: V5_F01_STORE_RECORD_KINDS.includes(V5_F01_DOCUMENT_SOURCE_RECORD_KIND),
    },
    {
      gap: "kernel_reserved_kind_not_registered",
      where: "mcp-server/src/record-source-authority.v5.js",
      what: `add "${V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND}" to V5_F01_RESERVED_DERIVATIVE_KINDS,`
        + " so the public registration surface refuses a kind this contract's own"
        + " writer produces and a caller cannot pre-claim a document version's"
        + " derivative identity before the document is written",
      landed: V5_F01_RESERVED_DERIVATIVE_KINDS.includes(V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND),
    },
    {
      gap: "document_writer_not_amended",
      where: "mcp-server/src/record-source-authority-store.v5.js",
      what: "recordDocumentIdentity must call the six-argument ops.f01_record_document"
        + " with the provenance envelope and, when the document is derived, the"
        + " derivative-link envelope",
      landed: null,
    },
    {
      gap: "domain_schema_not_folded",
      where: "domain.sql",
      what: "fold ops/document-derivative-registration.candidate.sql: the private-helper"
        + " name lists in sections 10 and 11, the f01_guard_direct_dml writer"
        + " alternation, ops.f01_reserved_derivative_kinds() and the replaced"
        + " ops.f01_record_document. Applying domain.sql AFTER the candidate reverts"
        + " all four",
      landed: null,
    },
  ]);
}

/**
 * Refuse while any hunk above is open. Nothing in this module calls it; it is for
 * the parent's handler, so an integration that is half done fails loudly at the
 * seam instead of writing a document version with no provenance edge.
 */
export function assertDocumentSourceIntegrationComplete() {
  const open = documentSourceIntegrationGaps().filter(entry => entry.landed !== true);
  if (open.length > 0) {
    fail("parent_integration_incomplete",
      `the document-source seam is not integrated: ${open.map(e => e.gap).join(", ")}`,
      { open: open.map(e => ({ gap: e.gap, where: e.where, landed: e.landed })) });
  }
  return true;
}
