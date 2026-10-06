// DoctorCRE v5 slice V5-RW02 — attended Salesforce reconciliation and
// per-action evaluation, as a pure deterministic kernel.
//
// Eight settled decisions (Q059.D5, Q070.D1, Q084.D1, Q097.D1, Q098.D1,
// Q099.D1, Q100.D1, Q123.D4) are encoded here as one closed, versioned
// contract with a deterministic policy digest, plus pure evaluators over typed
// observations of an ATTENDED browser session. The reviewed catalog names the
// concrete output: an origin/org/account/record verified browser sequence for
// duplicate detection, preview, opportunity write and readback, document
// preparation and resumable recovery, with per-action trust evidence kept
// separate. Its three `checkable_done` clauses map to this file as:
//
//   1. "auth challenge / UI drift / unexpected recipient / policy conflict stop
//      safely"                     -> evaluatePageObservation, an ordered stop
//                                     ladder with no bypass and no auto-retry.
//   2. "duplicate / readback / idempotent resume fixtures pass"
//                                  -> evaluateDuplicateSearch,
//                                     evaluateWriteReadback, evaluateResume.
//   3. "each action records distinct evidence and cannot inherit trust"
//                                  -> the evidence record sealed by
//                                     evaluateWriteReadback and read by
//                                     evaluateActionTrustWindow, which refuses
//                                     any record of another action kind.
//
// WHAT THIS FILE IS NOT, said first because the name invites the wrong reading.
// It is not the Salesforce browser adapter, not the Connector Gateway, not a
// capability minter and not a transport. It drives no browser, opens no
// connection, reads no credential, calls no provider, writes no record-layer row
// and sends nothing. Every fact it decides on is a TYPED OBSERVATION THE CALLER
// SUPPLIES, and every answer says so in `outward_effect_granted: false`. The
// best answer an action can reach here is `admissible_pending_attended_runtime`:
// every RW02 rule passed on the facts as reported, and the runtime inputs the
// catalog names for activation are still missing and are listed by name.
//
// NOTHING A CALLER BUILT IS TRUSTED AS BUILT. A public hash proves only that
// somebody hashed something, so no door in this file accepts a finished preview,
// a step key or a duplicate verdict. Admission, readback and resume each take
// the REQUEST and re-derive: the preview is rebuilt through buildActionPreview
// (so every preview rule and every credential check runs again), the step key is
// derived from case, action kind and intent ordinal, and the duplicate search is
// re-evaluated and must be about the same case, the same step and the same org.
//
// ONE ACTION, ONE ORG, ONE SEAT, ONE RECORD. The preview preimage carries the
// org binding — origin, org id and the signed-in Salesforce seat — and the
// target record. The page an action is taken on must be bound to exactly that
// org and seat, a targeted action's page must pin exactly that record, and the
// F06 envelope's `account` principal must be exactly that seat. A capability for
// one org, seat or record therefore cannot admit an action on another.
//
// ATTENDED IS STRUCTURAL. Q084.D1 and Q100.D1 say Salesforce browser work is
// launched attended and stays attended while reliability is established; the
// catalog excludes unattended execution and MFA/CAPTCHA bypass. So
// `execution_mode: "unattended"` is refused by name at every door, every
// authentication challenge is a STOP whose only resolution is a human at the
// browser, and no answer in this file offers a way around a challenge.
//
// PER-ACTION CONFIRMATION IS STRUCTURAL. No write reaches Salesforce or the
// record layer without a partner confirmation bound to the EXACT preview digest,
// action kind and step key of that one action, given by the SAME actor the F06
// envelope names, and no older than one capability lifetime. There is no batch,
// session or "all" confirmation to name.
//
// CONFIRMATION FRESHNESS IS NOT YET BOUNDED, SO NOTHING IS ADMISSIBLE. The only
// bound a caller cannot inflate would be a capability lifetime CEILING, and V5-F06
// does not hold one (V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM). Bounding freshness
// by the caller's own expires_at minus issued_at alone would let a twenty-year
// capability admit a ten-year-old confirmation. So admission runs every check and
// every refusal keeps precedence, but where it would have answered
// `admissible_pending_attended_runtime` it answers `unavailable`, naming that
// seam, until F06 binds a ceiling and the lifetime is within it. The ceiling is a
// module constant, never a caller argument. The actor's authority is the real F06
// capability presentation, whose first check is the global-boundaries actor
// authority answer, computed here rather than trusted.
//
// SALESFORCE STATE IS NOT DOCTORCRE LIFECYCLE. The catalog excludes it and
// Q097.D1 settles the opportunity as a PARALLEL corporate case. The one
// record-layer write this slice may propose is the external-id link
// (`salesforce_id`) on the deal; a preview that would carry a Salesforce phase,
// outcome, value, placeholder or any V5-J102 lifecycle axis into DoctorCRE is
// refused.
//
// THE BUSINESS RULES THIS KERNEL READS FROM DOCTRINE, not invented here:
//   * Every field names its fact class and V5-F01's projectRecordHome decides
//     whether that class's home is the surface the field is written to. A
//     corporate transaction field's home is Salesforce; a DoctorCRE operating
//     fact written into Salesforce as if Salesforce owned it is refused.
//   * Never auto-merge on a name (salesforce-read-sop): a name-similar
//     candidate is a human disambiguation, never a silent join.
//   * Commission and close date are PLACEHOLDERS, never figures or forecasts
//     (pipeline-coo-doctrine): they are labelled in every preview, must have
//     placeholder shape, and may never be written to the record layer.
//   * The lane comes from the Out of Market checkbox, never inferred from a
//     city (salesforce-read-sop): an inferred lane is refused.
//   * The Salesforce deal exists before any signature document is generated
//     (lead-system, Jul 14 correction): ETL and commission-agreement
//     preparation require an opportunity whose readback is present.
//
// PROTECTED SENDS ARE NOT HERE. Q098.D1's workflow continues through
// protected-send approval and delivery verification, but the catalog assigns the
// outward email/document effect to V5-RW01, which depends on this slice. The
// send kinds are declared as excluded and refused by name, naming RW01.
//
// RESUME IS ANCHORED ON THE PROVIDER. A caller-held journal is a HINT, never the
// authority: the decision to skip, re-read or re-preview is made from the
// provider readback, and a hint that disagrees with the provider is itself a
// stop (inconsistent result), never a tie-break. A consumed capability is never
// reused. A create's readback must carry THIS step's idempotency marker, so an
// equal-valued opportunity that already existed cannot confirm it.
//
// TRUST IS PER ACTION AND NEVER ACTIVE HERE. Q099.D1: autonomy is earned
// separately for each Salesforce action, never through global trust. Evidence
// records are sealed per action kind and a window refuses any record of another
// kind. The seal is a public digest: it proves a record was not EDITED, not that
// this kernel produced it. Authenticating evidence is the durable evidence store
// (V5_RW02_EVIDENCE_STORE_SEAM), which is why the window can only be READ here
// and activation-review eligibility answers `unavailable`.
//
// TWO KINDS OF NO, following the sibling v5 modules:
//   * A POLICY ANSWER is returned — a frozen result with a `decision` from this
//     module's registered vocabulary and a stable `reason_id`.
//   * A CONTRACT VIOLATION throws V5RW02Error. Unknown fields, unknown
//     vocabulary, malformed digests, credential-shaped values and unreadable
//     instants are not policy questions; the module fails closed.
//
// OBLIGATIONS THIS KERNEL CANNOT CHECK, named so nobody mistakes silence for a
// pass (V5_RW02_OPEN_OBLIGATIONS):
//   * CASE ANCHOR COMPLETENESS is the CALLER's obligation. Duplicate linking
//     matches a candidate's typed anchor against the anchors the case carries.
//     A case that omits an anchor the record layer holds — a prospect-only case
//     whose deal already exists — cannot match an opportunity linked to the
//     omitted anchor, and would read as `create_admissible`. The case must be
//     resolved from the record layer with every anchor it holds.
//   * WHICH SALESFORCE FIELDS ARE PLACEHOLDERS is the field map's to settle
//     (V5_RW02_FIELD_MAP_SEAM). The placeholder rules bind every field LABELLED
//     `commission_placeholder` or `close_date_placeholder`; a commission field
//     submitted with ordinary semantics is not detectable until the field map
//     names the commission and close-date fields.
//
// THE DATA BOUNDARY. No field in any closed key set can hold a credential. Any
// string that CONTAINS something shaped like one — a JWT anywhere in the text, a
// dotted triple, a PEM block, a `password=`/`token=`/`secret=`/`sid=` pair, a
// bearer or Basic authorization value, a Salesforce session id (raw or
// URL-encoded), a Salesforce refresh token, an AWS access key id, a GitHub token
// or an API key — throws `credential_shaped_value`. The scan covers every string
// the module normalizes AND every string and key inside the F06 envelope,
// capability, presentation, authority and attempt objects handed through to
// V5-F06, before they are passed on.
// No error in this file echoes a caller's value, unknown key or unregistered
// vocabulary word; errors name the PATH only. Readback mismatches report field
// NAMES only, never values.
//
// The module is pure: no filesystem, network, database, environment or clock.
// Every time-dependent evaluation takes `now` from its caller.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS, V5_ACTIONS } from "./global-boundaries.v5.js";
import {
  V5_ATTENDED_ACTIVATION_RECEIPT_STEP,
  V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM,
  evaluateAttemptResolution,
  evaluateCapabilityPresentation,
  evaluateConsumptionOrder,
} from "./workflow-effect-envelope.v5.js";
import { projectRecordHome } from "./record-source-authority.v5.js";
import { V5_J102_ASSIGNMENT_PHASES, V5_J102_DEAL_AXES } from "./cre-lifecycle.v5.js";

export { V5_NO_EFFECTS };

export const V5_RW02_SCHEMA_VERSION = "doctorcre-v5-salesforce-reconciliation.v1";
export const V5_RW02_POLICY_VERSION = 3;
export const V5_RW02_EVIDENCE_SCHEMA_VERSION = "doctorcre-v5-rw02-action-evidence.v1";
export const V5_RW02_PREVIEW_SCHEMA_VERSION = "doctorcre-v5-rw02-action-preview.v2";

/** The effect class the catalog records on this item. */
export const V5_RW02_EFFECT_CLASS = "attended_external_provider_mutation";

/**
 * The F06 envelope action every RW02 mutation is sealed under. The shared
 * V5_ACTIONS registry has no Salesforce action, and adding one would move a
 * shared policy digest outside this slice's lease; a Salesforce opportunity
 * write is a deal update under deal-owner, account, policy and capability
 * controls. The RW02 action kind is bound into the payload digest, so a
 * capability issued for one kind refuses against another.
 */
export const V5_RW02_F06_ACTION = "business.update_deal";

// ---------------------------------------------------------------------------
// Local primitives. Each v5 module carries its own copy on purpose.
// ---------------------------------------------------------------------------

const STABLE_ID = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}$/;
const DIGEST_REF = /^sha256:[0-9a-f]{64}$/;
const FIELD_NAME = /^[A-Za-z][A-Za-z0-9_]{0,79}$/;
const HTTPS_ORIGIN = /^https:\/\/[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/;
// A Salesforce Opportunity record id: key prefix 006, then 12 characters, with
// the optional 3-character case-safe suffix.
const OPPORTUNITY_ID = /^006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$/;
const CALENDAR_DATE = /^\d{4}-\d{2}-\d{2}$/;
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁩﻿]/u;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

// Credential shapes, searched ANYWHERE in a string. None of these is a policy
// question: a value that carries one means the data boundary has been crossed.
export const V5_RW02_CREDENTIAL_PATTERNS = Object.freeze({
  jwt_anywhere: /eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}/,
  dotted_triple: /^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$/,
  pem_block: /-----BEGIN /,
  secret_pair: /(?:password|passwd|pwd|passcode|token|secret|api[_-]?key|session[_-]?id|client[_-]?secret|\bsid)\s*[=:]/i,
  bearer: /\bbearer\s+\S+/i,
  // Base64 user:password after "Basic": mixed case and a digit or padding, so
  // prose such as "Basic understanding" is not mistaken for one.
  basic_auth: /\b[Bb][Aa][Ss][Ii][Cc]\s+(?=[A-Za-z0-9+/]*[0-9+/=])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])[A-Za-z0-9+/]{12,}={0,2}/,
  salesforce_session_id: /00D[A-Za-z0-9]{12,15}![A-Za-z0-9._]{16,}/,
  salesforce_session_id_url_encoded: /00D[A-Za-z0-9]{12,15}%21[A-Za-z0-9._%-]{16,}/i,
  salesforce_refresh_token: /\b5Aep861[A-Za-z0-9._]{20,}/,
  aws_access_key_id: /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/,
  github_token: /\bgh[pousr]_[A-Za-z0-9]{36,}/,
  api_key: /\bsk-[A-Za-z0-9_-]{16,}/,
});
const CREDENTIAL_PATTERN_LIST = Object.freeze(Object.values(V5_RW02_CREDENTIAL_PATTERNS));

export class V5RW02Error extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5RW02Error";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5RW02Error(code, message, detail);
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

/** An unknown key is refused WITHOUT naming it: a key can itself carry a secret. */
function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field at ${path}`, { path, allowed: [...allowed] });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertArray(value, path, { min = 0, max = 256 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < min) fail("invalid_shape", `${path} must hold at least ${min} entries`, { path });
  if (value.length > max) fail("too_many_entries", `${path} may hold at most ${max} entries`, { path });
  return value;
}

function containsCredential(value) {
  return CREDENTIAL_PATTERN_LIST.some(pattern => pattern.test(value));
}

/**
 * Walk an object handed through to V5-F06 and refuse any string or key shaped
 * like a credential. Errors name the path only, never the value or the key.
 */
function assertNoCredentialDeep(value, path, depth = 0) {
  if (depth > 16) fail("invalid_shape", `${path} is nested too deeply`, { path });
  if (typeof value === "string") {
    if (containsCredential(value)) {
      fail("credential_shaped_value",
        `${path} is shaped like credential material; no credential may enter this module`, { path });
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((v, i) => assertNoCredentialDeep(v, `${path}[${i}]`, depth + 1));
    return;
  }
  if (value !== null && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      if (containsCredential(k)) {
        fail("credential_shaped_value",
          `a key under ${path} is shaped like credential material; no credential may enter this module`,
          { path });
      }
      assertNoCredentialDeep(v, `${path}.<key>`, depth + 1);
    }
  }
}

/** Every string that enters this module passes through here. */
function assertSafeText(value, path, { maxLength = 512 } = {}) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  if (value.length > maxLength) fail("text_too_long", `${path} is too long`, { path, maxLength });
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
    fail("malformed_unicode", `${path} contains an unpaired surrogate`, { path });
  }
  if (UNSAFE_TEXT.test(value)) {
    fail("unsafe_unicode", `${path} contains a control, bidirectional or invisible character`, { path });
  }
  if (containsCredential(value)) {
    // The value itself is deliberately NOT echoed into the error.
    fail("credential_shaped_value",
      `${path} is shaped like credential material; no credential may enter this module`, { path });
  }
  return value;
}

function assertStableId(value, path) {
  assertSafeText(value, path, { maxLength: 256 });
  if (!STABLE_ID.test(value)) fail("invalid_identifier", `${path} must be a stable identifier`, { path });
  return value;
}

function assertOptionalStableId(value, path) {
  if (value === undefined || value === null) return null;
  return assertStableId(value, path);
}

function assertOpportunityId(value, path) {
  assertSafeText(value, path, { maxLength: 18 });
  if (!OPPORTUNITY_ID.test(value)) {
    fail("invalid_opportunity_id", `${path} must be a Salesforce Opportunity record id`, { path });
  }
  return value;
}

function assertOptionalOpportunityId(value, path) {
  if (value === undefined || value === null) return null;
  return assertOpportunityId(value, path);
}

function assertOrigin(value, path) {
  assertSafeText(value, path, { maxLength: 253 });
  if (!HTTPS_ORIGIN.test(value)) {
    fail("invalid_origin", `${path} must be an https origin with no path, port or credentials`, { path });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !DIGEST_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference over 64 lower-case hex characters`, { path });
  }
  return value;
}

/** An unregistered value is refused WITHOUT echoing it. */
function assertEnum(value, registered, path, code) {
  if (typeof value !== "string" || !registered.includes(value)) {
    fail(code, `the value at ${path} is not registered`, { path, registered: [...registered] });
  }
  return value;
}

function assertPositiveInteger(value, path) {
  if (!Number.isSafeInteger(value) || value < 1) {
    fail("invalid_shape", `${path} must be a positive integer`, { path });
  }
  return value;
}

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `${path} must be "${ORGANIZATION_TENANT_ID}"`, { path });
  }
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path });
  const [, year, month, day, hour, minute, second] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) ||
      Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) {
    fail("invalid_timestamp", `${path} names an instant that does not exist on the calendar`, { path });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path });
  return parsed;
}

/** A field value: a string, a finite number, a boolean, or null. Nothing nested. */
function assertFieldValue(value, path) {
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) fail("invalid_shape", `${path} must be finite`, { path });
    return value;
  }
  if (typeof value === "string") {
    // Empty strings are legitimate field values; only non-empty ones are text-checked.
    if (value.length === 0) return value;
    return assertSafeText(value, path, { maxLength: 2048 });
  }
  fail("invalid_shape", `${path} must be a string, number, boolean or null`, { path });
}

// ---------------------------------------------------------------------------
// The settled decisions. Text and evidence digests are copied verbatim from the
// reviewed r7 design basis (document doctorcre-v5-design-basis, artifact
// doctorcre-v5-design-basis-r7-review.json), reassembled from its 62 record-layer
// chunks on 2026-09-24; the reassembled bytes hash to the current
// normalized_r7_sha256 below. Identity, not configuration: they are hashed into
// the policy preimage so a drifted copy moves the digest.
// ---------------------------------------------------------------------------

export const V5_RW02_SOURCE_BINDING = deepFreeze({
  document: "doctorcre-v5-design-basis",
  artifact_file: "doctorcre-v5-design-basis-r7-review.json",
  normalized_r7_sha256: "4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7",
  catalog_section: "doctorcre-v5-astra-integration-review#v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09",
  catalog_item: "V5-RW02",
});

export const V5_RW02_SETTLED_DECISIONS = deepFreeze({
  "Q059.D5": {
    settled_requirement: "After the governed Tour journey, reconcile Salesforce through the attended representative connector workflow before enabling autonomous email or native-device capability.",
    source_evidence_digest: "5c58f2233ea82aab0f66bd966467e24875504900bd96fa98be4fa300436fd513",
  },
  "Q070.D1": {
    settled_requirement: "Use governed attended Salesforce browser workflows to create the corporate opportunity, send ETLs and commission agreements, and maintain phases, with preview, approval where protected, stop conditions, idempotency, and readback.",
    source_evidence_digest: "430b5bd85c8f11289eb5d31dcd0e0fbe1acf054969672bdb65d26b0cc01c19ab",
  },
  "Q084.D1": {
    settled_requirement: "Launch Salesforce browser automation attended, with exact preview and readback and immediate stops on authentication, challenges, UI drift, unexpected recipients, policy conflict, or inconsistent results.",
    source_evidence_digest: "855dbbbb6fe8271317bf3385450abf40469b694f390a220ce3a55cfe5a64d176",
  },
  "Q097.D1": {
    settled_requirement: "Treat the Salesforce opportunity as a parallel corporate case linked across prospect, engagement, assignment, and Deal; keeping it accurate is mandatory because corporate credit and payment depend on it.",
    source_evidence_digest: "68e0a4eccc791145bca32ecab5ff4d1b00bad708c20e05c88b9f6d349c9b4d11",
  },
  "Q098.D1": {
    settled_requirement: "The attended Salesforce ETL workflow resolves parties, detects duplicates, previews fields, creates and reads back the opportunity, generates the approved ETL, obtains protected-send approval, verifies delivery, and resumes idempotently.",
    source_evidence_digest: "c13472831ed9fa39792756fb218514c8be2e8a565d63378315be5403a2b65d07",
  },
  "Q099.D1": {
    settled_requirement: "Earn autonomy separately for each Salesforce action using fixtures, supervised production samples, exact readback, a clean evaluation window, recovery, and explicit activation; never use global trust.",
    source_evidence_digest: "21c5fd4e04dc8a759ec741b2e9b480def66c83bf8776df3d1a6e4dda58b548d1",
  },
  "Q100.D1": {
    settled_requirement: "Keep Salesforce browser work attended while reliability is being established; Doc may perform the sequence while a partner remains available only for authentication and protected sends.",
    source_evidence_digest: "f2e3513db2b80bd0be95bcc16f70fa4c45c1aa1fb293ab428e1e27e2d186e586",
  },
  "Q123.D4": {
    settled_requirement: "Add governed attended Salesforce writing in a later v5 wave after the first launch contract, without blocking daily use.",
    source_evidence_digest: "55457ebf6cdb2c1959eadcb5358db6bf67a7366d4f941c28be19a5ddaf397b35",
  },
});

export const V5_RW02_DECISION_IDS = deepFreeze(Object.keys(V5_RW02_SETTLED_DECISIONS).sort());

/**
 * The catalog's runtime or acceptance evidence inputs for this item. None has
 * been issued, so every admission lists all four as missing. A future output is
 * never a prerequisite for building this code; it is a prerequisite for USING it.
 */
export const V5_RW02_RUNTIME_EVIDENCE_INPUTS = deepFreeze([
  "step:journey-three-production-outcome",
  "step:representative-workflow-preactivation-contract-receipt",
  "step:attended-external-effect-contract-independent-receipt",
  "step:attended-external-effect-capability-issuance",
]);

// ---------------------------------------------------------------------------
// Seams this slice names and does not fill, because filling them would ship an
// invented binding.
// ---------------------------------------------------------------------------

export const V5_RW02_SALESFORCE_ADAPTER_SEAM = "step:v5-rw02-governed-salesforce-browser-adapter-admission";
export const V5_RW02_ORG_BINDING_SEAM = "step:v5-rw02-salesforce-org-origin-account-binding";
export const V5_RW02_FIELD_MAP_SEAM = "step:v5-rw02-salesforce-opportunity-field-map";
export const V5_RW02_IDEMPOTENCY_MARKER_SEAM = "step:v5-rw02-provider-idempotency-marker-field";
export const V5_RW02_EVALUATION_WINDOW_SEAM = "step:v5-rw02-per-action-evaluation-window-thresholds";
export const V5_RW02_EVIDENCE_STORE_SEAM = "step:v5-rw02-durable-per-action-evidence-store";
export const V5_RW02_ENGAGEMENT_LINK_SEAM = "step:v5-rw02-non-deal-case-link-record-verb";

/**
 * The capability lifetime ceiling RW02 bounds confirmation freshness by. V5-F06
 * holds no ceiling yet (V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM), so this is
 * `null` and admission answers `unavailable`. It is a module constant on
 * purpose: a ceiling a caller supplies is no ceiling.
 */
export const V5_RW02_CAPABILITY_LIFETIME_CEILING_SECONDS = null;

/**
 * Whether confirmation freshness is bounded for a capability of this lifetime
 * under this ceiling. Pure; admission calls it only with the module constant.
 */
export function rw02ConfirmationFreshnessBound(lifetime_ms, ceiling_seconds) {
  if (!Number.isFinite(lifetime_ms) || lifetime_ms <= 0) {
    fail("invalid_shape", "lifetime_ms must be a positive number of milliseconds", { path: "lifetime_ms" });
  }
  if (ceiling_seconds === null) {
    return deepFreeze({ bounded: false, reason_id: "capability_lifetime_ceiling_unbound",
      seam: V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM });
  }
  if (!Number.isInteger(ceiling_seconds) || ceiling_seconds <= 0) {
    fail("invalid_shape", "ceiling_seconds must be a positive integer or null", { path: "ceiling_seconds" });
  }
  if (lifetime_ms > ceiling_seconds * 1000) {
    return deepFreeze({ bounded: false, reason_id: "capability_lifetime_exceeds_ceiling",
      seam: V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM });
  }
  return deepFreeze({ bounded: true, reason_id: null, seam: null });
}

export const V5_RW02_SEAMS = deepFreeze([
  V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM,
  V5_RW02_ENGAGEMENT_LINK_SEAM,
  V5_RW02_EVALUATION_WINDOW_SEAM,
  V5_RW02_EVIDENCE_STORE_SEAM,
  V5_RW02_FIELD_MAP_SEAM,
  V5_RW02_IDEMPOTENCY_MARKER_SEAM,
  V5_RW02_ORG_BINDING_SEAM,
  V5_RW02_SALESFORCE_ADAPTER_SEAM,
].sort());

/** What this kernel cannot check, and who owes it. See the module header. */
export const V5_RW02_OPEN_OBLIGATIONS = deepFreeze({
  case_anchor_completeness: {
    owner: "caller",
    obligation: "Resolve the case from the record layer with every anchor it holds (prospect, engagement, assignment, deal). An omitted anchor cannot match an opportunity linked to it, so a duplicate search would read create_admissible.",
  },
  placeholder_field_designation: {
    owner: V5_RW02_FIELD_MAP_SEAM,
    obligation: "Name which Salesforce fields are the commission and close-date placeholders. Until then a commission field submitted with ordinary semantics is not detectable, and only fields labelled as placeholders are held to placeholder rules.",
  },
});

/**
 * R7: a connector is admitted with version, auth, data, effect and removal
 * bindings. None is bound for the Salesforce browser adapter, so it is declared
 * here as NOT ADMITTED with each binding named as owed.
 */
export const V5_RW02_ADAPTER_ADMISSION = deepFreeze({
  adapter: "salesforce_browser_adapter",
  admitted: false,
  owed_bindings: ["auth", "data", "effect", "removal", "version"],
  seam: V5_RW02_SALESFORCE_ADAPTER_SEAM,
  credential_location: "provider_gateway_only",
  credential_enters_model_or_context: false,
});

// ---------------------------------------------------------------------------
// The workflow and its action kinds.
// ---------------------------------------------------------------------------

/** Q098.D1's sequence, up to where V5-RW01 takes over the outward send. */
export const V5_RW02_WORKFLOW_STAGES = deepFreeze([
  "resolve_parties",
  "detect_duplicates",
  "preview_fields",
  "partner_confirmation",
  "write",
  "readback",
  "document_preparation",
]);

/**
 * Each action kind this slice may evaluate. `surface` is where the write lands;
 * `requires_target_present` means the opportunity must already exist with a
 * present readback (Salesforce deal before any signature document);
 * `record_layer_verb` names the deployed verb the one record-layer write would
 * traverse — this module never calls it.
 */
export const V5_RW02_ACTION_KINDS = deepFreeze({
  opportunity_create: {
    surface: "salesforce", requires_target_present: false, target_forbidden: true,
    requires_duplicate_clearance: true, record_layer_verb: null,
  },
  opportunity_phase_update: {
    surface: "salesforce", requires_target_present: true, target_forbidden: false,
    requires_duplicate_clearance: false, record_layer_verb: null,
  },
  opportunity_link_record: {
    surface: "record_layer", requires_target_present: true, target_forbidden: false,
    requires_duplicate_clearance: false, record_layer_verb: "update-deal",
  },
  etl_document_prepare: {
    surface: "salesforce", requires_target_present: true, target_forbidden: false,
    requires_duplicate_clearance: false, record_layer_verb: null,
  },
  commission_agreement_prepare: {
    surface: "salesforce", requires_target_present: true, target_forbidden: false,
    requires_duplicate_clearance: false, record_layer_verb: null,
  },
});

export const V5_RW02_ACTION_KIND_KEYS = deepFreeze(Object.keys(V5_RW02_ACTION_KINDS).sort());

/** Named so a caller that asks for them is refused by name, not by accident. */
export const V5_RW02_EXCLUDED_ACTIONS = deepFreeze({
  etl_protected_send: "owned_by_v5_rw01",
  commission_agreement_protected_send: "owned_by_v5_rw01",
  delivery_verification: "owned_by_v5_rw01",
});

/** The V5-F01 home each surface writes into; projectRecordHome is asked against it. */
export const V5_RW02_SURFACE_HOMES = deepFreeze({
  salesforce: "salesforce",
  record_layer: "neon_record_layer",
});

/** The V5-F01 fact classes a preview field may declare. */
export const V5_RW02_FIELD_FACT_CLASSES = deepFreeze([
  "corporate_transaction_field", "operating_fact",
]);

/** What a field in a preview MEANS. The field names themselves are the owed field map. */
export const V5_RW02_FIELD_SEMANTICS = deepFreeze([
  "ordinary",
  "phase",
  "commission_placeholder",
  "close_date_placeholder",
  "out_of_market_flag",
  "external_id_link",
]);

export const V5_RW02_PLACEHOLDER_SEMANTICS = deepFreeze([
  "close_date_placeholder", "commission_placeholder",
]);

export const V5_RW02_FIELD_PROVENANCE = deepFreeze([
  "salesforce_observed",
  "doctorcre_record",
  "partner_entered",
  "inferred",
]);

/**
 * DoctorCRE lifecycle fields a Salesforce-derived preview may never carry into
 * the record layer. Built from V5-J102's own vocabulary plus the deal columns
 * that hold lifecycle meaning, rather than retyped beside it.
 */
export const V5_RW02_DOCTORCRE_LIFECYCLE_FIELDS = deepFreeze([
  ...new Set([
    ...V5_J102_DEAL_AXES,
    "assignment_phase",
    ...V5_J102_ASSIGNMENT_PHASES.map(p => `assignment_phase_${p}`),
    "phase", "outcome", "won_value", "closed_on", "deal_type", "segment", "lane", "city",
  ]),
].sort());

/** The one record-layer field the link action may write. */
export const V5_RW02_LINK_FIELD = "salesforce_id";

// ---------------------------------------------------------------------------
// The stop ladder (checkable_done 1, Q084.D1).
// ---------------------------------------------------------------------------

/** Order is load-bearing: the first check that does not pass decides. */
export const V5_RW02_PAGE_CHECKS = deepFreeze([
  "execution_mode",
  "authentication_challenge",
  "origin",
  "org",
  "signed_in_account",
  "record",
  "ui_contract",
  "recipients",
  "policy_conflict",
  "result_consistency",
]);

export const V5_RW02_CHALLENGE_STATES = deepFreeze([
  "none", "login_required", "mfa_challenge", "captcha", "session_expired", "unstated",
]);

export const V5_RW02_CONSISTENCY_STATES = deepFreeze(["consistent", "inconsistent", "unstated"]);
export const V5_RW02_EXECUTION_MODES = deepFreeze(["attended", "unattended"]);

export const V5_RW02_STOP_REASON_IDS = deepFreeze([
  "authentication_challenge",
  "binding_account_not_previewed",
  "binding_org_not_previewed",
  "binding_origin_not_previewed",
  "challenge_state_unobservable",
  "inconsistent_result",
  "org_mismatch",
  "origin_mismatch",
  "policy_conflict",
  "record_mismatch",
  "record_not_pinned",
  "result_consistency_unobservable",
  "signed_in_account_mismatch",
  "ui_drift",
  "unattended_execution_excluded",
  "unexpected_recipient",
]);

// ---------------------------------------------------------------------------
// Answer base. Every answer says what it did NOT grant.
// ---------------------------------------------------------------------------

function answerBase(extra) {
  return {
    schema_version: V5_RW02_SCHEMA_VERSION,
    policy_version: V5_RW02_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    effect_class: V5_RW02_EFFECT_CLASS,
    ...extra,
    outward_effect_granted: false,
    autonomy_active: false,
    attended_activation_receipt: V5_ATTENDED_ACTIVATION_RECEIPT_STEP,
    attended_activation_receipt_present: false,
    effects: V5_NO_EFFECTS,
  };
}

function stopAnswer(answer_kind, reason_id, detail) {
  return deepFreeze(answerBase({
    answer_kind,
    decision: "stop",
    reason_id,
    ...detail,
    bypass_permitted: false,
    automatic_retry_permitted: false,
    resolution_owner: "partner_at_the_browser",
  }));
}

// ---------------------------------------------------------------------------
// Case identity, org binding and the step key.
// ---------------------------------------------------------------------------

const CASE_KEYS = Object.freeze([
  "assignment_ref", "deal_ref", "engagement_ref", "prospect_ref", "workflow_ref",
]);

/**
 * Q097.D1: the opportunity is a parallel corporate case linked across prospect,
 * engagement, assignment and Deal. At least one DoctorCRE anchor is required;
 * the workflow_ref names this reconciliation run's case.
 */
function normalizeCase(value, path) {
  const raw = assertObject(value, path);
  assertClosedKeys(raw, CASE_KEYS, path);
  assertRequiredKeys(raw, ["workflow_ref"], path);
  const out = {
    workflow_ref: assertStableId(raw.workflow_ref, `${path}.workflow_ref`),
    prospect_ref: assertOptionalStableId(raw.prospect_ref, `${path}.prospect_ref`),
    engagement_ref: assertOptionalStableId(raw.engagement_ref, `${path}.engagement_ref`),
    assignment_ref: assertOptionalStableId(raw.assignment_ref, `${path}.assignment_ref`),
    deal_ref: assertOptionalStableId(raw.deal_ref, `${path}.deal_ref`),
  };
  if (!out.prospect_ref && !out.engagement_ref && !out.assignment_ref && !out.deal_ref) {
    fail("case_unanchored",
      `${path} must link at least one of prospect, engagement, assignment or deal`, { path });
  }
  return out;
}

/** The anchor types a candidate link can name. Each maps to one case key. */
export const V5_RW02_CASE_ANCHOR_TYPES = deepFreeze(["assignment", "deal", "engagement", "prospect"]);

/** A typed link matches only the case anchor OF THAT TYPE. */
function linkMatchesCase(link, kase) {
  return link !== null && kase[`${link.anchor_type}_ref`] === link.ref;
}

const ORG_BINDING_KEYS = Object.freeze(["account_ref", "org_id", "origin"]);

/** The Salesforce org, origin and signed-in seat ONE action is bound to. */
function normalizeOrgBinding(value, path) {
  const raw = assertObject(value, path);
  assertClosedKeys(raw, ORG_BINDING_KEYS, path);
  assertRequiredKeys(raw, ORG_BINDING_KEYS, path);
  return {
    origin: assertOrigin(raw.origin, `${path}.origin`),
    org_id: assertStableId(raw.org_id, `${path}.org_id`),
    account_ref: assertStableId(raw.account_ref, `${path}.account_ref`),
  };
}

function stepKeyOf(kase, action_kind, intent_ordinal) {
  return digest({
    kind: "rw02-step-key.v1", tenant: ORGANIZATION_TENANT_ID, action_kind, case: kase, intent_ordinal,
  });
}

const STEP_KEY_REQUEST_KEYS = Object.freeze(["action_kind", "case", "intent_ordinal", "tenant"]);

/**
 * The step key: the deterministic identity of ONE intended action, computable
 * before anything happens. It is the idempotency marker a provider readback is
 * searched for, and the F06 envelope's step_id. `intent_ordinal` separates two
 * genuinely distinct intents of one kind (a second phase move), and replaying
 * the same intent folds to the same key. No door in this module accepts a step
 * key from a caller; each derives it with this function.
 */
export function rw02StepKey(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, STEP_KEY_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, STEP_KEY_REQUEST_KEYS, "request");
  assertTenant(raw.tenant, "request.tenant");
  const action_kind = assertActionKind(raw.action_kind, "request.action_kind");
  const kase = normalizeCase(raw.case, "request.case");
  const intent_ordinal = assertPositiveInteger(raw.intent_ordinal, "request.intent_ordinal");
  return stepKeyOf(kase, action_kind, intent_ordinal);
}

/** The F06 envelope workflow_id for one case. */
export function rw02WorkflowId(caseValue) {
  const kase = normalizeCase(caseValue, "case");
  return digest({ kind: "rw02-workflow-id.v1", tenant: ORGANIZATION_TENANT_ID, case: kase });
}

function assertActionKind(value, path) {
  if (typeof value === "string" && Object.hasOwn(V5_RW02_EXCLUDED_ACTIONS, value)) {
    fail("action_excluded_from_rw02",
      `the action at ${path} is an outward send; the catalog assigns it to V5-RW01, which depends on this slice`,
      { path, owner: "V5-RW01" });
  }
  return assertEnum(value, V5_RW02_ACTION_KIND_KEYS, path, "unknown_action_kind");
}

// ---------------------------------------------------------------------------
// 1. The page observation and the stop ladder.
// ---------------------------------------------------------------------------

const BINDING_KEYS = Object.freeze([
  "expected_account_ref", "expected_org_id", "expected_origin", "expected_recipients",
  "expected_record_id", "expected_ui_contract_digest",
]);
const OBSERVATION_KEYS = Object.freeze([
  "challenge", "org_id", "origin", "policy_conflicts", "recipients", "record_id",
  "result_consistency", "signed_in_account_ref", "ui_contract_digest",
]);
const PAGE_REQUEST_KEYS = Object.freeze(["binding", "execution_mode", "observation"]);

function normalizeRecipients(value, path) {
  if (value === undefined || value === null) return null;
  return [...assertArray(value, path, { max: 64 })
    .map((r, i) => assertStableId(r, `${path}[${i}]`))].sort();
}

function normalizePage(request) {
  const raw = assertObject(request, "page");
  assertClosedKeys(raw, PAGE_REQUEST_KEYS, "page");
  assertRequiredKeys(raw, PAGE_REQUEST_KEYS, "page");
  const execution_mode = assertEnum(raw.execution_mode, V5_RW02_EXECUTION_MODES,
    "page.execution_mode", "unknown_execution_mode");

  const b = assertObject(raw.binding, "page.binding");
  assertClosedKeys(b, BINDING_KEYS, "page.binding");
  assertRequiredKeys(b, ["expected_account_ref", "expected_org_id", "expected_origin",
    "expected_ui_contract_digest"], "page.binding");
  const binding = {
    expected_origin: assertOrigin(b.expected_origin, "page.binding.expected_origin"),
    expected_org_id: assertStableId(b.expected_org_id, "page.binding.expected_org_id"),
    expected_account_ref: assertStableId(b.expected_account_ref, "page.binding.expected_account_ref"),
    expected_record_id: b.expected_record_id === undefined || b.expected_record_id === null
      ? null : assertStableId(b.expected_record_id, "page.binding.expected_record_id"),
    expected_ui_contract_digest: assertDigestRef(b.expected_ui_contract_digest,
      "page.binding.expected_ui_contract_digest"),
    expected_recipients: normalizeRecipients(b.expected_recipients, "page.binding.expected_recipients"),
  };

  const o = assertObject(raw.observation, "page.observation");
  assertClosedKeys(o, OBSERVATION_KEYS, "page.observation");
  assertRequiredKeys(o, ["challenge", "org_id", "origin", "result_consistency",
    "signed_in_account_ref", "ui_contract_digest"], "page.observation");
  const observation = {
    origin: assertSafeText(o.origin, "page.observation.origin", { maxLength: 2048 }),
    org_id: assertStableId(o.org_id, "page.observation.org_id"),
    signed_in_account_ref: assertStableId(o.signed_in_account_ref, "page.observation.signed_in_account_ref"),
    record_id: o.record_id === undefined || o.record_id === null
      ? null : assertStableId(o.record_id, "page.observation.record_id"),
    ui_contract_digest: assertDigestRef(o.ui_contract_digest, "page.observation.ui_contract_digest"),
    challenge: assertEnum(o.challenge, V5_RW02_CHALLENGE_STATES, "page.observation.challenge",
      "unknown_challenge_state"),
    recipients: normalizeRecipients(o.recipients, "page.observation.recipients"),
    policy_conflicts: [...assertArray(o.policy_conflicts ?? [], "page.observation.policy_conflicts", { max: 64 })
      .map((c, i) => assertStableId(c, `page.observation.policy_conflicts[${i}]`))].sort(),
    result_consistency: assertEnum(o.result_consistency, V5_RW02_CONSISTENCY_STATES,
      "page.observation.result_consistency", "unknown_consistency_state"),
  };
  return { execution_mode, binding, observation };
}

/**
 * Decide whether the attended browser sequence may continue on this page.
 *
 * `continue` means none of the registered stops fired on the facts as reported.
 * Every stop is immediate, carries `bypass_permitted: false` and
 * `automatic_retry_permitted: false`, and its only resolution is the partner at
 * the browser. An unstated challenge or consistency state STOPS: silence is
 * never a clear page. A continuing answer reports the org binding it verified,
 * so a consuming door can compare it with what the action was previewed on.
 */
export function evaluatePageObservation(request) {
  const { execution_mode, binding, observation } = normalizePage(request);
  const kind = "rw02-page-observation.v1";
  const detail = check => ({ blocking_check: check, checks_required: [...V5_RW02_PAGE_CHECKS] });

  if (execution_mode !== "attended") {
    return stopAnswer(kind, "unattended_execution_excluded", detail("execution_mode"));
  }
  if (observation.challenge === "unstated") {
    return stopAnswer(kind, "challenge_state_unobservable", detail("authentication_challenge"));
  }
  if (observation.challenge !== "none") {
    return stopAnswer(kind, "authentication_challenge",
      { ...detail("authentication_challenge"), challenge: observation.challenge });
  }
  // Exact comparison: a path, port, trailing dot or look-alike host is a mismatch.
  if (observation.origin !== binding.expected_origin) {
    return stopAnswer(kind, "origin_mismatch", detail("origin"));
  }
  if (observation.org_id !== binding.expected_org_id) {
    return stopAnswer(kind, "org_mismatch", detail("org"));
  }
  if (observation.signed_in_account_ref !== binding.expected_account_ref) {
    return stopAnswer(kind, "signed_in_account_mismatch", detail("signed_in_account"));
  }
  if (binding.expected_record_id !== null && observation.record_id !== binding.expected_record_id) {
    return stopAnswer(kind, "record_mismatch", detail("record"));
  }
  if (observation.ui_contract_digest !== binding.expected_ui_contract_digest) {
    return stopAnswer(kind, "ui_drift", detail("ui_contract"));
  }
  if (observation.recipients !== null) {
    const expected = binding.expected_recipients ?? [];
    const unexpected = observation.recipients.filter(r => !expected.includes(r));
    if (unexpected.length > 0) {
      return stopAnswer(kind, "unexpected_recipient",
        { ...detail("recipients"), unexpected_recipient_count: unexpected.length });
    }
  }
  if (observation.policy_conflicts.length > 0) {
    return stopAnswer(kind, "policy_conflict",
      { ...detail("policy_conflict"), policy_conflicts: observation.policy_conflicts });
  }
  if (observation.result_consistency === "unstated") {
    return stopAnswer(kind, "result_consistency_unobservable", detail("result_consistency"));
  }
  if (observation.result_consistency === "inconsistent") {
    return stopAnswer(kind, "inconsistent_result", detail("result_consistency"));
  }
  return deepFreeze(answerBase({
    answer_kind: kind,
    decision: "continue",
    reason_id: "page_verified_attended",
    blocking_check: null,
    checks_required: [...V5_RW02_PAGE_CHECKS],
    verified_binding: {
      origin: binding.expected_origin,
      org_id: binding.expected_org_id,
      account_ref: binding.expected_account_ref,
      record_id: binding.expected_record_id,
    },
    org_binding_seam: V5_RW02_ORG_BINDING_SEAM,
  }));
}

/**
 * A continuing page must be the org, origin and seat the action was previewed
 * on, and a targeted action's page must PIN the previewed record. Returns the
 * stop reason, or null.
 */
function pageBindingStop(page, preview, { requireRecordPin }) {
  const vb = page.verified_binding;
  if (vb.origin !== preview.org_binding.origin) return "binding_origin_not_previewed";
  if (vb.org_id !== preview.org_binding.org_id) return "binding_org_not_previewed";
  if (vb.account_ref !== preview.org_binding.account_ref) return "binding_account_not_previewed";
  if (requireRecordPin && vb.record_id !== preview.target_opportunity_id) return "record_not_pinned";
  return null;
}

// ---------------------------------------------------------------------------
// 2a. Duplicate detection (Q098.D1 "detects duplicates").
// ---------------------------------------------------------------------------

export const V5_RW02_NAME_MATCH_STATES = deepFreeze(["exact", "similar", "none"]);
export const V5_RW02_MARKER_STATES = deepFreeze(["present", "absent", "unstated"]);
export const V5_RW02_SEARCH_COMPLETENESS = deepFreeze(["complete", "truncated", "unstated"]);

export const V5_RW02_DUPLICATE_OUTCOMES = deepFreeze([
  "already_effected",
  "create_admissible",
  "human_disambiguation_required",
  "link_existing",
  "stop",
]);

const DUPLICATE_REQUEST_KEYS = Object.freeze(["case", "intent_ordinal", "page", "search", "tenant"]);
const SEARCH_KEYS = Object.freeze(["candidates", "completeness"]);
const CANDIDATE_KEYS = Object.freeze(["linked_anchor", "name_match", "opportunity_id", "step_marker"]);
const LINKED_ANCHOR_KEYS = Object.freeze(["anchor_type", "ref"]);

function normalizeLinkedAnchor(value, path) {
  if (value === undefined || value === null) return null;
  const raw = assertObject(value, path);
  assertClosedKeys(raw, LINKED_ANCHOR_KEYS, path);
  assertRequiredKeys(raw, LINKED_ANCHOR_KEYS, path);
  return {
    anchor_type: assertEnum(raw.anchor_type, V5_RW02_CASE_ANCHOR_TYPES, `${path}.anchor_type`,
      "unknown_anchor_type"),
    ref: assertStableId(raw.ref, `${path}.ref`),
  };
}

function normalizeSearch(value, path) {
  const raw = assertObject(value, path);
  assertClosedKeys(raw, SEARCH_KEYS, path);
  assertRequiredKeys(raw, SEARCH_KEYS, path);
  const completeness = assertEnum(raw.completeness, V5_RW02_SEARCH_COMPLETENESS,
    `${path}.completeness`, "unknown_search_completeness");
  const seen = new Set();
  const candidates = assertArray(raw.candidates, `${path}.candidates`, { max: 200 }).map((c, i) => {
    const p = `${path}.candidates[${i}]`;
    assertObject(c, p);
    assertClosedKeys(c, CANDIDATE_KEYS, p);
    assertRequiredKeys(c, ["name_match", "opportunity_id", "step_marker"], p);
    const opportunity_id = assertOpportunityId(c.opportunity_id, `${p}.opportunity_id`);
    if (seen.has(opportunity_id)) fail("duplicate_candidate", `${p} repeats an opportunity`, { path: p });
    seen.add(opportunity_id);
    return {
      opportunity_id,
      step_marker: assertEnum(c.step_marker, V5_RW02_MARKER_STATES, `${p}.step_marker`, "unknown_marker_state"),
      linked_anchor: normalizeLinkedAnchor(c.linked_anchor, `${p}.linked_anchor`),
      name_match: assertEnum(c.name_match, V5_RW02_NAME_MATCH_STATES, `${p}.name_match`, "unknown_name_match"),
    };
  });
  candidates.sort((a, b) => (a.opportunity_id < b.opportunity_id ? -1 : 1));
  return { completeness, candidates };
}

/**
 * Classify one provider duplicate search for one intended create.
 *
 * The step key is derived here for `opportunity_create` from the case and the
 * intent ordinal. The step marker is that key as written with the opportunity
 * (the provider field that carries it is V5_RW02_IDEMPOTENCY_MARKER_SEAM). A
 * candidate carrying THIS step's marker means the create already happened — the
 * resume answer is to read it back, never to create again. A candidate whose
 * TYPED link names one of this case's anchors — a prospect link against the
 * case's prospect, a deal link against its deal — means link, not create; an id
 * equal to an anchor of another type is not a link. Anchor completeness is the
 * caller's obligation (V5_RW02_OPEN_OBLIGATIONS.case_anchor_completeness). A
 * name-similar candidate is a human question: never auto-merge on a name.
 *
 * The answer reports the case, step key and verified org binding it was about,
 * so admission can refuse a search that was run for something else.
 */
export function evaluateDuplicateSearch(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, DUPLICATE_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, DUPLICATE_REQUEST_KEYS, "request");
  assertTenant(raw.tenant, "request.tenant");
  const kase = normalizeCase(raw.case, "request.case");
  const intent_ordinal = assertPositiveInteger(raw.intent_ordinal, "request.intent_ordinal");
  const step_key = stepKeyOf(kase, "opportunity_create", intent_ordinal);
  const search = normalizeSearch(raw.search, "request.search");
  const page = evaluatePageObservation(raw.page);
  const kind = "rw02-duplicate-search.v1";
  const base = { step_key, case: kase, idempotency_marker_seam: V5_RW02_IDEMPOTENCY_MARKER_SEAM };

  if (page.decision !== "continue") {
    return stopAnswer(kind, page.reason_id, { ...base, page_reason_id: page.reason_id });
  }
  const scoped = { ...base, searched_on: page.verified_binding };
  if (search.completeness !== "complete") {
    return stopAnswer(kind, "inconsistent_result",
      { ...scoped, detail_reason: "duplicate_search_incomplete", completeness: search.completeness });
  }
  if (search.candidates.some(c => c.step_marker === "unstated")) {
    return stopAnswer(kind, "inconsistent_result", { ...scoped, detail_reason: "step_marker_unobservable" });
  }
  const marked = search.candidates.filter(c => c.step_marker === "present");
  if (marked.length > 1) {
    return stopAnswer(kind, "inconsistent_result", {
      ...scoped, detail_reason: "step_marker_on_multiple_opportunities",
      opportunity_ids: marked.map(c => c.opportunity_id),
    });
  }
  if (marked.length === 1) {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "already_effected", reason_id: "step_marker_found_on_provider",
      ...scoped, opportunity_id: marked[0].opportunity_id, required_next_step: "readback",
      create_permitted: false,
    }));
  }
  const linked = search.candidates.filter(c => linkMatchesCase(c.linked_anchor, kase));
  if (linked.length > 1) {
    return stopAnswer(kind, "inconsistent_result", {
      ...scoped, detail_reason: "case_linked_to_multiple_opportunities",
      opportunity_ids: linked.map(c => c.opportunity_id),
    });
  }
  if (linked.length === 1) {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "link_existing", reason_id: "opportunity_already_linked_to_case",
      ...scoped, opportunity_id: linked[0].opportunity_id, create_permitted: false,
    }));
  }
  const named = search.candidates.filter(c => c.name_match !== "none");
  if (named.length > 0) {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "human_disambiguation_required",
      reason_id: "name_match_is_never_an_automatic_join",
      ...scoped, candidate_opportunity_ids: named.map(c => c.opportunity_id), create_permitted: false,
    }));
  }
  return deepFreeze(answerBase({
    answer_kind: kind, decision: "create_admissible", reason_id: "no_duplicate_on_complete_search",
    ...scoped, create_permitted_after_preview_and_confirmation: true,
  }));
}

// ---------------------------------------------------------------------------
// 2b. The preview (Q070.D1, Q084.D1 "exact preview").
// ---------------------------------------------------------------------------

const PREVIEW_REQUEST_KEYS = Object.freeze([
  "action_kind", "case", "fields", "intent_ordinal", "org_binding", "target_opportunity_id", "tenant",
]);
const PREVIEW_FIELD_KEYS = Object.freeze(["fact_class", "field", "provenance", "semantics", "value"]);

function previewPreimage(p) {
  return {
    kind: "rw02-action-preview.v2",
    schema_version: V5_RW02_PREVIEW_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    action_kind: p.action_kind,
    surface: p.surface,
    step_key: p.step_key,
    case: p.case,
    org_binding: p.org_binding,
    target_opportunity_id: p.target_opportunity_id,
    fields: p.fields,
  };
}

/** A placeholder has placeholder shape: a number or a calendar date, or nothing. */
function placeholderShapeOk(f) {
  if (f.value === null) return true;
  if (f.semantics === "commission_placeholder") return typeof f.value === "number";
  return typeof f.value === "string" && CALENDAR_DATE.test(f.value);
}

/**
 * Build the exact preview a partner confirms. Its digest is the payload digest
 * the F06 envelope seals, so the capability, the confirmation and the preview
 * are one thing and a substitution anywhere refuses. The step key is DERIVED
 * here from case, action kind and intent ordinal; the org binding and target
 * record are part of the preimage.
 *
 * Every consuming door calls this function again on the caller's request rather
 * than accepting its output, so every rule below runs on every use.
 */
export function buildActionPreview(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, PREVIEW_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, ["action_kind", "case", "fields", "intent_ordinal", "org_binding", "tenant"],
    "request");
  assertTenant(raw.tenant, "request.tenant");
  const action_kind = assertActionKind(raw.action_kind, "request.action_kind");
  const spec = V5_RW02_ACTION_KINDS[action_kind];
  const kase = normalizeCase(raw.case, "request.case");
  const intent_ordinal = assertPositiveInteger(raw.intent_ordinal, "request.intent_ordinal");
  const step_key = stepKeyOf(kase, action_kind, intent_ordinal);
  const org_binding = normalizeOrgBinding(raw.org_binding, "request.org_binding");
  const target_opportunity_id = assertOptionalOpportunityId(raw.target_opportunity_id,
    "request.target_opportunity_id");
  const kind = "rw02-action-preview.v2";
  const refuse = (reason_id, detail) => deepFreeze(answerBase({
    answer_kind: kind, decision: "refuse", reason_id, action_kind, step_key, ...detail,
  }));

  const seenFields = new Set();
  const fields = assertArray(raw.fields, "request.fields", { min: 1, max: 128 }).map((f, i) => {
    const p = `request.fields[${i}]`;
    assertObject(f, p);
    assertClosedKeys(f, PREVIEW_FIELD_KEYS, p);
    assertRequiredKeys(f, PREVIEW_FIELD_KEYS, p);
    const field = assertSafeText(f.field, `${p}.field`, { maxLength: 80 });
    if (!FIELD_NAME.test(field)) fail("invalid_field_name", `${p}.field is not a field name`, { path: p });
    if (seenFields.has(field)) fail("duplicate_field", `${p}.field repeats an earlier field`, { path: p });
    seenFields.add(field);
    return {
      field,
      value: assertFieldValue(f.value, `${p}.value`),
      fact_class: assertEnum(f.fact_class, V5_RW02_FIELD_FACT_CLASSES, `${p}.fact_class`, "unknown_fact_class"),
      semantics: assertEnum(f.semantics, V5_RW02_FIELD_SEMANTICS, `${p}.semantics`, "unknown_field_semantics"),
      provenance: assertEnum(f.provenance, V5_RW02_FIELD_PROVENANCE, `${p}.provenance`, "unknown_provenance"),
    };
  }).sort((a, b) => (a.field < b.field ? -1 : 1));

  if (spec.target_forbidden && target_opportunity_id !== null) {
    return refuse("create_names_an_existing_target", {});
  }
  if (spec.requires_target_present && target_opportunity_id === null) {
    return refuse("target_opportunity_required", {});
  }
  for (const f of fields) {
    if (f.semantics === "out_of_market_flag" && f.provenance === "inferred") {
      return refuse("lane_inferred_not_authoritative", { field: f.field });
    }
  }
  const placeholders = fields.filter(f => V5_RW02_PLACEHOLDER_SEMANTICS.includes(f.semantics));
  for (const f of placeholders) {
    if (!placeholderShapeOk(f)) return refuse("placeholder_shape_invalid", { field: f.field });
  }

  if (spec.surface === "record_layer") {
    // A placeholder is never a figure, so it never becomes a DoctorCRE value.
    if (placeholders.length > 0) {
      return refuse("placeholder_is_not_a_figure", { fields: placeholders.map(f => f.field) });
    }
    if (kase.deal_ref === null) {
      return refuse("record_layer_link_needs_deal", { seam: V5_RW02_ENGAGEMENT_LINK_SEAM });
    }
    const crossing = fields.filter(f => V5_RW02_DOCTORCRE_LIFECYCLE_FIELDS.includes(f.field) ||
      f.semantics === "phase");
    if (crossing.length > 0) {
      return refuse("salesforce_state_is_not_doctorcre_lifecycle", { fields: crossing.map(f => f.field) });
    }
    // The ONE record-layer write: the external-id link, nothing else.
    if (fields.length !== 1 || fields[0].field !== V5_RW02_LINK_FIELD ||
        fields[0].semantics !== "external_id_link" || fields[0].value !== target_opportunity_id) {
      return refuse("record_layer_link_carries_only_external_id", { fields: fields.map(f => f.field) });
    }
  } else {
    if (fields.some(f => f.semantics === "external_id_link")) {
      return refuse("external_id_link_is_record_layer_only", {});
    }
    if (action_kind === "opportunity_phase_update" &&
        (fields.length !== 1 || fields[0].semantics !== "phase")) {
      return refuse("phase_update_carries_exactly_one_phase_field", {});
    }
  }

  // V5-F01 decides, per field, whether the field's fact class lives on the
  // surface it is being written to. Nothing here restates F01's table.
  for (const f of fields) {
    const home = projectRecordHome({
      tenant: ORGANIZATION_TENANT_ID, fact_class: f.fact_class,
      claimed_home: V5_RW02_SURFACE_HOMES[spec.surface], claimed_authoritative: true,
    });
    if (home.decision !== "allow") {
      return refuse("field_home_is_not_this_surface", {
        field: f.field, fact_class: f.fact_class, f01_reason_id: home.reason_id,
        f01_authoritative_home: home.authoritative_home,
      });
    }
  }

  const sealed = {
    action_kind, surface: spec.surface, step_key, case: kase, org_binding, target_opportunity_id, fields,
  };
  const preview_digest = digest(previewPreimage(sealed));
  return deepFreeze(answerBase({
    answer_kind: kind,
    decision: "preview_ready",
    reason_id: "exact_preview_sealed",
    preview: {
      schema_version: V5_RW02_PREVIEW_SCHEMA_VERSION,
      ...sealed,
      intent_ordinal,
      preview_digest,
    },
    preview_digest,
    step_key,
    payload_digest_for_envelope: preview_digest,
    f06_action: V5_RW02_F06_ACTION,
    placeholder_fields: placeholders.map(f => f.field),
    placeholders_are_figures: false,
    record_layer_verb: spec.record_layer_verb,
    requires_partner_confirmation: true,
    confirmation_binds: { preview_digest, action_kind, step_key },
    field_map_seam: V5_RW02_FIELD_MAP_SEAM,
  }));
}

// ---------------------------------------------------------------------------
// 2c. Per-action admission: the rebuilt preview, the page and its binding,
//     duplicate clearance, target, confirmation, the F06 envelope binding and
//     the real F06 capability presentation.
// ---------------------------------------------------------------------------

export const V5_RW02_ADMISSION_CHECKS = deepFreeze([
  "execution_mode",
  "preview",
  "page",
  "page_binding",
  "duplicate_clearance",
  "target_present",
  "partner_confirmation",
  "envelope_binding",
  "capability_presentation",
]);

const ADMISSION_REQUEST_KEYS = Object.freeze([
  "confirmation", "duplicate_search", "execution_mode", "f06", "page", "preview_request",
  "target_readback", "tenant",
]);
const CONFIRMATION_KEYS = Object.freeze([
  "action_kind", "confirmation_ref", "confirmed_at", "confirmed_by", "preview_digest", "step_key",
]);
const TARGET_READBACK_KEYS = Object.freeze(["opportunity_id", "state"]);
export const V5_RW02_TARGET_READBACK_STATES = deepFreeze([
  "present", "absent", "indeterminate", "unstated",
]);
const F06_PRESENTATION_KEYS = Object.freeze(["authority", "capability", "envelope", "now", "presentation"]);

/**
 * Decide whether ONE action may proceed to its attended write.
 *
 * The best answer is `admissible_pending_attended_runtime`. It means every RW02
 * check passed on the facts as reported AND the real F06 capability
 * presentation allowed — and the catalog's runtime evidence inputs are still
 * absent, so nothing is granted. They are listed in `runtime_inputs_missing`.
 *
 * CONFIRMATION FRESHNESS. The confirmation must not postdate the presentation
 * and must be no older than ONE CAPABILITY LIFETIME (the capability's own
 * expires_at minus issued_at); either failure is a refusal. That lifetime is the
 * caller's, so it is not a bound by itself: until V5-F06 binds a lifetime ceiling
 * and the lifetime is within it, an action that passes every other check answers
 * `unavailable`, naming V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM, instead of
 * `admissible_pending_attended_runtime`.
 */
export function evaluateActionAdmission(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, ADMISSION_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, ["confirmation", "execution_mode", "f06", "page", "preview_request", "tenant"],
    "request");
  assertTenant(raw.tenant, "request.tenant");
  const execution_mode = assertEnum(raw.execution_mode, V5_RW02_EXECUTION_MODES,
    "request.execution_mode", "unknown_execution_mode");
  const kind = "rw02-action-admission.v1";

  if (execution_mode !== "attended" || raw.page?.execution_mode !== "attended") {
    return stopAnswer(kind, "unattended_execution_excluded", { blocking_check: "execution_mode" });
  }

  const built = buildActionPreview(raw.preview_request);
  if (built.decision !== "preview_ready") {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "refuse", reason_id: "preview_refused", blocking_check: "preview",
      preview_reason_id: built.reason_id, checks_required: [...V5_RW02_ADMISSION_CHECKS],
    }));
  }
  const preview = built.preview;
  const spec = V5_RW02_ACTION_KINDS[preview.action_kind];
  const base = {
    action_kind: preview.action_kind, step_key: preview.step_key, preview_digest: preview.preview_digest,
    checks_required: [...V5_RW02_ADMISSION_CHECKS],
  };
  const refuse = (check, reason_id, detail) => deepFreeze(answerBase({
    answer_kind: kind, decision: "refuse", reason_id, blocking_check: check, ...base, ...detail,
  }));

  const page = evaluatePageObservation(raw.page);
  if (page.decision !== "continue") {
    return stopAnswer(kind, page.reason_id, { ...base, blocking_check: "page" });
  }
  const bindingStop = pageBindingStop(page, preview, { requireRecordPin: spec.requires_target_present });
  if (bindingStop !== null) {
    return stopAnswer(kind, bindingStop, { ...base, blocking_check: "page_binding" });
  }

  if (spec.requires_duplicate_clearance) {
    if (raw.duplicate_search === undefined || raw.duplicate_search === null) {
      return refuse("duplicate_clearance", "duplicate_search_required_before_create", {});
    }
    const dup = evaluateDuplicateSearch(raw.duplicate_search);
    if (canonicalJson(dup.case) !== canonicalJson(preview.case)) {
      return refuse("duplicate_clearance", "duplicate_search_for_other_case", {});
    }
    if (dup.step_key !== preview.step_key) {
      return refuse("duplicate_clearance", "duplicate_search_for_other_step", {});
    }
    if (dup.decision === "stop") {
      return stopAnswer(kind, dup.reason_id, { ...base, blocking_check: "duplicate_clearance" });
    }
    const on = dup.searched_on;
    if (on.origin !== preview.org_binding.origin || on.org_id !== preview.org_binding.org_id ||
        on.account_ref !== preview.org_binding.account_ref) {
      return refuse("duplicate_clearance", "duplicate_search_on_other_org", {});
    }
    if (dup.decision !== "create_admissible") {
      return refuse("duplicate_clearance", "create_not_admissible_after_duplicate_search",
        { duplicate_decision: dup.decision });
    }
  } else if (raw.duplicate_search !== undefined && raw.duplicate_search !== null) {
    fail("unexpected_field", "request.duplicate_search applies only to opportunity_create",
      { path: "request.duplicate_search" });
  }

  if (spec.requires_target_present) {
    const t = raw.target_readback;
    if (t === undefined || t === null) {
      return refuse("target_present", "target_readback_required", {});
    }
    assertObject(t, "request.target_readback");
    assertClosedKeys(t, TARGET_READBACK_KEYS, "request.target_readback");
    assertRequiredKeys(t, TARGET_READBACK_KEYS, "request.target_readback");
    const opportunity_id = assertOpportunityId(t.opportunity_id, "request.target_readback.opportunity_id");
    const state = assertEnum(t.state, V5_RW02_TARGET_READBACK_STATES, "request.target_readback.state",
      "unknown_readback_state");
    if (opportunity_id !== preview.target_opportunity_id) {
      return stopAnswer(kind, "record_mismatch", { ...base, blocking_check: "target_present" });
    }
    if (state !== "present") {
      // The Salesforce deal must exist, read back, before a document or a phase
      // move or a link; an unknown target is not a present one.
      return refuse("target_present", state === "absent" ? "target_opportunity_absent"
        : "target_readback_indeterminate", { target_readback_state: state });
    }
  }

  const c = assertObject(raw.confirmation, "request.confirmation");
  assertClosedKeys(c, CONFIRMATION_KEYS, "request.confirmation");
  assertRequiredKeys(c, CONFIRMATION_KEYS, "request.confirmation");
  assertStableId(c.confirmation_ref, "request.confirmation.confirmation_ref");
  assertStableId(c.confirmed_by, "request.confirmation.confirmed_by");
  const confirmedAt = assertInstant(c.confirmed_at, "request.confirmation.confirmed_at");
  assertDigestRef(c.preview_digest, "request.confirmation.preview_digest");
  assertDigestRef(c.step_key, "request.confirmation.step_key");
  if (c.preview_digest !== preview.preview_digest) {
    return refuse("partner_confirmation", "confirmation_for_other_preview", {});
  }
  if (c.action_kind !== preview.action_kind) {
    return refuse("partner_confirmation", "confirmation_for_other_action", {});
  }
  if (c.step_key !== preview.step_key) {
    return refuse("partner_confirmation", "confirmation_for_other_step", {});
  }

  const f = assertObject(raw.f06, "request.f06");
  assertClosedKeys(f, F06_PRESENTATION_KEYS, "request.f06");
  assertNoCredentialDeep(f, "request.f06");
  assertRequiredKeys(f, ["capability", "envelope", "now", "presentation"], "request.f06");
  const now = assertInstant(f.now, "request.f06.now");
  const envelope = assertObject(f.envelope, "request.f06.envelope");
  const capability = assertObject(f.capability, "request.f06.capability");
  if (c.confirmed_by !== envelope.principals?.actor_slug) {
    return refuse("partner_confirmation", "confirmation_by_other_actor", {});
  }
  if (confirmedAt > now) {
    return refuse("partner_confirmation", "confirmation_after_presentation", {});
  }
  const lifetime = assertInstant(capability.expires_at, "request.f06.capability.expires_at") -
    assertInstant(capability.issued_at, "request.f06.capability.issued_at");
  if (now - confirmedAt > lifetime) {
    return refuse("partner_confirmation", "confirmation_stale", {});
  }

  if (envelope.action !== V5_RW02_F06_ACTION) {
    return refuse("envelope_binding", "envelope_action_not_rw02", {});
  }
  if (envelope.payload_digest !== preview.preview_digest) {
    return refuse("envelope_binding", "envelope_payload_is_not_this_preview", {});
  }
  if (envelope.step_id !== preview.step_key) {
    return refuse("envelope_binding", "envelope_step_is_not_this_step", {});
  }
  if (envelope.workflow_id !== rw02WorkflowId(preview.case)) {
    return refuse("envelope_binding", "envelope_workflow_is_not_this_case", {});
  }
  if (envelope.principals?.account_ref !== preview.org_binding.account_ref) {
    return refuse("envelope_binding", "capability_for_other_account", {});
  }

  // The real F06 evaluator, run here — never a caller's copy of its answer.
  const presentation = evaluateCapabilityPresentation({
    envelope: f.envelope, capability: f.capability, presentation: f.presentation,
    authority: f.authority, now: f.now,
  });
  if (presentation.decision !== "allow") {
    return refuse("capability_presentation", "capability_presentation_refused", {
      f06_reason_id: presentation.reason_id, f06_blocking_check: presentation.blocking_check,
    });
  }

  const freshness = rw02ConfirmationFreshnessBound(lifetime, V5_RW02_CAPABILITY_LIFETIME_CEILING_SECONDS);
  if (!freshness.bounded) {
    return deepFreeze(answerBase({
      answer_kind: kind,
      decision: "unavailable",
      reason_id: freshness.reason_id,
      blocking_check: "partner_confirmation",
      ...base,
      org_binding: preview.org_binding,
      seam: freshness.seam,
      runtime_inputs_missing: [...V5_RW02_RUNTIME_EVIDENCE_INPUTS],
      adapter_admission: V5_RW02_ADAPTER_ADMISSION,
      seams_owed: [...V5_RW02_SEAMS],
    }));
  }

  return deepFreeze(answerBase({
    answer_kind: kind,
    decision: "admissible_pending_attended_runtime",
    reason_id: "all_rw02_checks_passed_nothing_granted",
    blocking_check: null,
    ...base,
    org_binding: preview.org_binding,
    envelope_digest: presentation.idempotency_key,
    idempotency_key: presentation.idempotency_key,
    consumption_must_commit_before_provider_call: true,
    record_layer_verb: spec.record_layer_verb,
    runtime_inputs_missing: [...V5_RW02_RUNTIME_EVIDENCE_INPUTS],
    adapter_admission: V5_RW02_ADAPTER_ADMISSION,
    seams_owed: [...V5_RW02_SEAMS],
  }));
}

// ---------------------------------------------------------------------------
// 2d. Write readback (Q084.D1 "exact readback"; F06 unknown-outcome quarantine).
// ---------------------------------------------------------------------------

export const V5_RW02_EVIDENCE_CLASSES = deepFreeze([
  "fixture", "supervised_production_sample", "recovery_exercise",
]);

export const V5_RW02_EVIDENCE_OUTCOMES = deepFreeze([
  "exact_match", "mismatch", "stopped", "unknown_resolved_by_readback",
]);

const READBACK_REQUEST_KEYS = Object.freeze([
  "evidence_class", "f06", "observed_at", "page", "preview_request", "provider_readback", "tenant",
]);
const F06_ATTEMPT_KEYS = Object.freeze(["attempt", "capability", "envelope"]);
const PROVIDER_READBACK_KEYS = Object.freeze(["completeness", "fields", "opportunity_id", "step_marker"]);
const READBACK_FIELD_KEYS = Object.freeze(["field", "value"]);

function sealEvidence(record) {
  return deepFreeze({ ...record, evidence_digest: digest({ kind: "rw02-evidence.v1", ...record }) });
}

/**
 * Resolve one attempted write against the provider readback.
 *
 * The preview is rebuilt from its request, the page must be the previewed org
 * and seat (and pin the previewed record for a targeted action), and the F06
 * ordering proof and quarantine run first, for real. While the outcome is
 * unknown the only answer is `readback_required`. Once the effect is confirmed
 * present, every preview field must read back with the IDENTICAL value and type,
 * and a create's readback must carry THIS step's marker — an equal-valued
 * opportunity that already existed is not this write. Only an exact match
 * produces the per-action evidence record.
 */
export function evaluateWriteReadback(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, READBACK_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, ["evidence_class", "f06", "observed_at", "page", "preview_request", "tenant"],
    "request");
  assertTenant(raw.tenant, "request.tenant");
  const evidence_class = assertEnum(raw.evidence_class, V5_RW02_EVIDENCE_CLASSES,
    "request.evidence_class", "unknown_evidence_class");
  assertInstant(raw.observed_at, "request.observed_at");
  const kind = "rw02-write-readback.v1";

  const built = buildActionPreview(raw.preview_request);
  if (built.decision !== "preview_ready") {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "refuse", reason_id: "preview_refused", preview_reason_id: built.reason_id,
    }));
  }
  const preview = built.preview;
  const spec = V5_RW02_ACTION_KINDS[preview.action_kind];
  const base = { action_kind: preview.action_kind, step_key: preview.step_key, preview_digest: preview.preview_digest };

  const f = assertObject(raw.f06, "request.f06");
  assertClosedKeys(f, F06_ATTEMPT_KEYS, "request.f06");
  assertRequiredKeys(f, F06_ATTEMPT_KEYS, "request.f06");
  assertNoCredentialDeep(f, "request.f06");
  if (f.envelope?.payload_digest !== preview.preview_digest || f.envelope?.step_id !== preview.step_key) {
    fail("attempt_for_other_preview", "request.f06.envelope is not sealed over this preview and step",
      { path: "request.f06.envelope" });
  }

  const page = evaluatePageObservation(raw.page);
  const order = evaluateConsumptionOrder({ envelope: f.envelope, capability: f.capability, attempt: f.attempt });
  const resolution = evaluateAttemptResolution({
    envelope: f.envelope, capability: f.capability, attempt: f.attempt,
    consumption_order: order, proposed_next_step: "provider_readback",
  });
  const evidenceBase = {
    schema_version: V5_RW02_EVIDENCE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    action_kind: preview.action_kind,
    step_key: preview.step_key,
    preview_digest: preview.preview_digest,
    envelope_digest: resolution.envelope_digest,
    evidence_class,
    observed_at: raw.observed_at,
  };
  const stopped = () => sealEvidence({ ...evidenceBase, outcome: "stopped", readback_digest: null });

  if (order.decision !== "allow") {
    // A provider call that preceded consumption is a broken attempt, not a result.
    return stopAnswer(kind, "inconsistent_result", {
      ...base, detail_reason: "consumption_order_refused", f06_reason_id: order.reason_id, evidence: stopped(),
    });
  }
  if (page.decision !== "continue") {
    return stopAnswer(kind, page.reason_id, { ...base, evidence: stopped() });
  }
  const bindingStop = pageBindingStop(page, preview, { requireRecordPin: spec.requires_target_present });
  if (bindingStop !== null) {
    return stopAnswer(kind, bindingStop, { ...base, evidence: stopped() });
  }
  // A readback joined on some other effect is not a readback of this one. A
  // reported outcome that disagrees with its readback resolves to unknown and is
  // handled by the quarantine branch below.
  if (resolution.decision !== "allow" && resolution.reason_id !== "outcome_readback_conflict") {
    return stopAnswer(kind, "inconsistent_result", {
      ...base, detail_reason: "attempt_resolution_refused", f06_reason_id: resolution.reason_id,
      evidence: stopped(),
    });
  }
  if (resolution.resolution === "unknown") {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "readback_required", reason_id: "outcome_unknown_readback_first",
      ...base, retry_permitted: false, f06_reason_id: resolution.reason_id,
    }));
  }
  if (resolution.resolution === "confirmed_failure") {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "confirmed_failure", reason_id: "effect_absent_on_provider",
      ...base, retry_requires: ["fresh_preview_confirmation", "fresh_capability"],
      consumed_capability_reusable: false,
    }));
  }

  // confirmed_success: the field-by-field readback.
  const rb = raw.provider_readback;
  if (rb === undefined || rb === null) {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "readback_required", reason_id: "field_readback_missing",
      ...base, retry_permitted: false,
    }));
  }
  assertObject(rb, "request.provider_readback");
  assertClosedKeys(rb, PROVIDER_READBACK_KEYS, "request.provider_readback");
  assertRequiredKeys(rb, PROVIDER_READBACK_KEYS, "request.provider_readback");
  const completeness = assertEnum(rb.completeness, V5_RW02_SEARCH_COMPLETENESS,
    "request.provider_readback.completeness", "unknown_search_completeness");
  const step_marker = assertEnum(rb.step_marker, V5_RW02_MARKER_STATES,
    "request.provider_readback.step_marker", "unknown_marker_state");
  const rbOpp = assertOpportunityId(rb.opportunity_id, "request.provider_readback.opportunity_id");
  const readFields = new Map();
  assertArray(rb.fields, "request.provider_readback.fields", { max: 256 }).forEach((x, i) => {
    const p = `request.provider_readback.fields[${i}]`;
    assertObject(x, p);
    assertClosedKeys(x, READBACK_FIELD_KEYS, p);
    assertRequiredKeys(x, READBACK_FIELD_KEYS, p);
    const name = assertSafeText(x.field, `${p}.field`, { maxLength: 80 });
    if (readFields.has(name)) fail("duplicate_field", `${p}.field repeats an earlier field`, { path: p });
    readFields.set(name, assertFieldValue(x.value, `${p}.value`));
  });
  const readback_digest = digest({
    kind: "rw02-readback.v1", opportunity_id: rbOpp, step_marker,
    fields: [...readFields.entries()].sort(([a], [b]) => (a < b ? -1 : 1)),
  });

  if (completeness !== "complete") {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "readback_required", reason_id: "field_readback_incomplete",
      ...base, retry_permitted: false,
    }));
  }
  if (preview.target_opportunity_id !== null && rbOpp !== preview.target_opportunity_id) {
    return stopAnswer(kind, "record_mismatch", {
      ...base, evidence: sealEvidence({ ...evidenceBase, outcome: "stopped", readback_digest }),
    });
  }
  if (spec.requires_duplicate_clearance && step_marker !== "present") {
    // A create is proved by ITS marker, never by an opportunity that merely
    // holds the same values.
    return stopAnswer(kind, "inconsistent_result", {
      ...base, detail_reason: "readback_not_this_steps_opportunity",
      evidence: sealEvidence({ ...evidenceBase, outcome: "stopped", readback_digest }),
    });
  }
  const mismatched = preview.fields
    .filter(pf => !readFields.has(pf.field) || canonicalJson(readFields.get(pf.field)) !== canonicalJson(pf.value))
    .map(pf => pf.field);
  if (mismatched.length > 0) {
    return stopAnswer(kind, "inconsistent_result", {
      ...base, mismatched_fields: mismatched,
      evidence: sealEvidence({ ...evidenceBase, outcome: "mismatch", readback_digest }),
    });
  }
  const outcome = f.attempt?.outcome?.state === "succeeded" ? "exact_match" : "unknown_resolved_by_readback";
  return deepFreeze(answerBase({
    answer_kind: kind, decision: "confirmed", reason_id: "exact_readback",
    ...base, opportunity_id: rbOpp,
    evidence: sealEvidence({ ...evidenceBase, outcome, readback_digest }),
    evidence_store_seam: V5_RW02_EVIDENCE_STORE_SEAM,
  }));
}

// ---------------------------------------------------------------------------
// 2e. Idempotent resume (Q098.D1 "resumes idempotently").
// ---------------------------------------------------------------------------

export const V5_RW02_JOURNAL_HINTS = deepFreeze(["not_started", "effected", "unknown", "absent"]);
export const V5_RW02_RESUME_OUTCOMES = deepFreeze([
  "proceed_to_preview", "readback_required", "skip_already_effected", "stop",
]);

const RESUME_REQUEST_KEYS = Object.freeze([
  "action_kind", "case", "intent_ordinal", "journal_hint", "provider", "tenant",
]);
const PROVIDER_EFFECT_KEYS = Object.freeze(["effect", "opportunity_id"]);
export const V5_RW02_PROVIDER_EFFECT_STATES = deepFreeze([
  "effect_present", "effect_absent", "indeterminate", "unstated",
]);

/**
 * Where does one interrupted action resume? The PROVIDER decides; the caller's
 * journal is a hint that may only raise a stop when it disagrees. The step key
 * is derived here, and a present effect must name the opportunity it is on.
 *
 *   provider present  + hint anything but "effected" -> skip (journal behind)
 *   provider present  + hint "effected"              -> skip
 *   provider absent   + hint "effected"              -> STOP: a write the journal
 *                                                        recorded is not there
 *   provider absent   + other hint                   -> proceed to a FRESH preview
 *   provider unknown                                 -> readback first
 */
export function evaluateResume(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, RESUME_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, RESUME_REQUEST_KEYS, "request");
  assertTenant(raw.tenant, "request.tenant");
  const action_kind = assertActionKind(raw.action_kind, "request.action_kind");
  const kase = normalizeCase(raw.case, "request.case");
  const intent_ordinal = assertPositiveInteger(raw.intent_ordinal, "request.intent_ordinal");
  const step_key = stepKeyOf(kase, action_kind, intent_ordinal);
  const hint = assertEnum(raw.journal_hint, V5_RW02_JOURNAL_HINTS, "request.journal_hint", "unknown_journal_hint");
  const pr = assertObject(raw.provider, "request.provider");
  assertClosedKeys(pr, PROVIDER_EFFECT_KEYS, "request.provider");
  assertRequiredKeys(pr, ["effect"], "request.provider");
  const effect = assertEnum(pr.effect, V5_RW02_PROVIDER_EFFECT_STATES, "request.provider.effect",
    "unknown_effect_state");
  const opportunity_id = assertOptionalOpportunityId(pr.opportunity_id, "request.provider.opportunity_id");
  const kind = "rw02-resume.v1";
  const base = { action_kind, step_key, journal_hint: hint, provider_effect: effect,
    authority: "provider_readback", journal_is_authority: false };

  if (effect === "indeterminate" || effect === "unstated") {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "readback_required", reason_id: "provider_effect_unknown", ...base,
      retry_permitted: false,
    }));
  }
  if (effect === "effect_present") {
    if (opportunity_id === null) {
      fail("missing_field", "request.provider.opportunity_id is required when the effect is present",
        { path: "request.provider.opportunity_id" });
    }
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "skip_already_effected",
      reason_id: hint === "effected" ? "provider_confirms_journal" : "journal_behind_provider",
      ...base, opportunity_id, required_next_step: "readback", repeat_write_permitted: false,
    }));
  }
  if (hint === "effected") {
    return stopAnswer(kind, "inconsistent_result",
      { ...base, detail_reason: "journal_records_effect_provider_lacks" });
  }
  return deepFreeze(answerBase({
    answer_kind: kind, decision: "proceed_to_preview", reason_id: "provider_confirms_not_effected",
    ...base,
    requires: ["fresh_page_verification", "fresh_preview", "fresh_partner_confirmation", "fresh_capability"],
    consumed_capability_reusable: false,
  }));
}

// ---------------------------------------------------------------------------
// 3. Per-action trust (Q099.D1). Distinct evidence, no inheritance, never active.
// ---------------------------------------------------------------------------

const TRUST_REQUEST_KEYS = Object.freeze(["action_kind", "evidence", "tenant", "trust_scope"]);
const EVIDENCE_KEYS = Object.freeze([
  "action_kind", "envelope_digest", "evidence_class", "evidence_digest", "observed_at",
  "outcome", "preview_digest", "readback_digest", "schema_version", "step_key", "tenant",
]);
export const V5_RW02_TRUST_SCOPES = deepFreeze(["per_action"]);
const SUCCESS_OUTCOMES = Object.freeze(["exact_match", "unknown_resolved_by_readback"]);

/**
 * Read one action kind's evidence window.
 *
 * Every record must be a sealed RW02 evidence record OF THIS ACTION KIND; a
 * record of another kind is refused rather than counted, which is what "cannot
 * inherit trust" means structurally. A `global` scope is refused by name. The
 * window is the records since the last mismatch or stop. The seal proves a
 * record was not edited, not who produced it, so the window is a READING only:
 * activation-review eligibility answers `unavailable` naming the threshold seam,
 * the evidence store seam is named beside it, and `autonomy_active` is false on
 * every answer.
 */
export function evaluateActionTrustWindow(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, TRUST_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, ["action_kind", "evidence", "tenant"], "request");
  assertTenant(raw.tenant, "request.tenant");
  const action_kind = assertActionKind(raw.action_kind, "request.action_kind");
  const kind = "rw02-action-trust-window.v1";
  if (raw.trust_scope !== undefined && raw.trust_scope !== "per_action") {
    return deepFreeze(answerBase({
      answer_kind: kind, decision: "refuse", reason_id: "global_trust_excluded", action_kind,
    }));
  }
  const seen = new Set();
  const records = assertArray(raw.evidence, "request.evidence", { max: 10000 }).map((e, i) => {
    const p = `request.evidence[${i}]`;
    assertObject(e, p);
    assertClosedKeys(e, EVIDENCE_KEYS, p);
    assertRequiredKeys(e, EVIDENCE_KEYS, p);
    if (e.schema_version !== V5_RW02_EVIDENCE_SCHEMA_VERSION) {
      fail("unnormalized_evidence", `${p} is not an RW02 evidence record`, { path: p });
    }
    assertTenant(e.tenant, `${p}.tenant`);
    const { evidence_digest, ...rest } = e;
    if (digest({ kind: "rw02-evidence.v1", ...rest }) !== evidence_digest) {
      fail("evidence_seal_broken", `${p} does not hash to its own evidence_digest`, { path: p });
    }
    assertEnum(e.evidence_class, V5_RW02_EVIDENCE_CLASSES, `${p}.evidence_class`, "unknown_evidence_class");
    assertEnum(e.outcome, V5_RW02_EVIDENCE_OUTCOMES, `${p}.outcome`, "unknown_evidence_outcome");
    return { index: i, record: e, observed: assertInstant(e.observed_at, `${p}.observed_at`) };
  });

  for (const { index, record } of records) {
    if (record.action_kind !== action_kind) {
      return deepFreeze(answerBase({
        answer_kind: kind, decision: "refuse", reason_id: "evidence_foreign_to_action", action_kind,
        offending_index: index, offending_action_kind: record.action_kind, inherits_from: [],
      }));
    }
    // One effect can succeed once. A second success record for the same step and
    // envelope — even re-observed at another instant — is the same sample
    // counted twice, and a window that could be padded that way is not clean.
    const effectKey = SUCCESS_OUTCOMES.includes(record.outcome)
      ? `effect:${record.step_key}:${record.envelope_digest}` : null;
    if (seen.has(record.evidence_digest) || (effectKey !== null && seen.has(effectKey))) {
      return deepFreeze(answerBase({
        answer_kind: kind, decision: "refuse", reason_id: "evidence_counted_twice", action_kind,
        offending_index: index,
      }));
    }
    seen.add(record.evidence_digest);
    if (effectKey !== null) seen.add(effectKey);
  }

  const ordered = [...records].sort((a, b) => a.observed - b.observed || a.index - b.index);
  let lastFailure = -1;
  ordered.forEach((r, i) => {
    if (r.record.outcome === "mismatch" || r.record.outcome === "stopped") lastFailure = i;
  });
  const window = ordered.slice(lastFailure + 1);
  const counts = Object.fromEntries(V5_RW02_EVIDENCE_CLASSES.map(c => [c, 0]));
  for (const r of window) counts[r.record.evidence_class] += 1;

  return deepFreeze(answerBase({
    answer_kind: kind,
    decision: "window_read",
    reason_id: "per_action_window_read",
    action_kind,
    trust_scope: "per_action",
    inherits_from: [],
    evidence_total: records.length,
    failures_total: ordered.filter(r => r.record.outcome === "mismatch" || r.record.outcome === "stopped").length,
    window_since_last_failure: {
      records: window.length,
      counts_by_class: counts,
      recovery_exercised: counts.recovery_exercise > 0,
      window_digest: digest({ kind: "rw02-window.v1", action_kind,
        evidence: window.map(r => r.record.evidence_digest) }),
    },
    evidence_authenticated: false,
    evidence_store_seam: V5_RW02_EVIDENCE_STORE_SEAM,
    activation_review_eligibility: "unavailable",
    activation_review_eligibility_seam: V5_RW02_EVALUATION_WINDOW_SEAM,
    activation_gate: "system.autonomy_tier_activation",
    activation_gate_authority_class: V5_ACTIONS["system.autonomy_tier_activation"].authority_class,
  }));
}

// ---------------------------------------------------------------------------
// The closed policy, its digest, and the zero-effect projection.
// ---------------------------------------------------------------------------

export function v5Rw02PolicyPreimage() {
  return {
    kind: "doctorcre-v5-rw02-policy.v2",
    schema_version: V5_RW02_SCHEMA_VERSION,
    policy_version: V5_RW02_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    effect_class: V5_RW02_EFFECT_CLASS,
    source_binding: V5_RW02_SOURCE_BINDING,
    decisions: V5_RW02_SETTLED_DECISIONS,
    runtime_evidence_inputs: V5_RW02_RUNTIME_EVIDENCE_INPUTS,
    seams: V5_RW02_SEAMS,
    open_obligations: V5_RW02_OPEN_OBLIGATIONS,
    capability_lifetime_ceiling_seconds: V5_RW02_CAPABILITY_LIFETIME_CEILING_SECONDS,
    case_anchor_types: V5_RW02_CASE_ANCHOR_TYPES,
    adapter_admission: V5_RW02_ADAPTER_ADMISSION,
    f06_action: V5_RW02_F06_ACTION,
    workflow_stages: V5_RW02_WORKFLOW_STAGES,
    action_kinds: V5_RW02_ACTION_KINDS,
    excluded_actions: V5_RW02_EXCLUDED_ACTIONS,
    surface_homes: V5_RW02_SURFACE_HOMES,
    field_fact_classes: V5_RW02_FIELD_FACT_CLASSES,
    field_semantics: V5_RW02_FIELD_SEMANTICS,
    placeholder_semantics: V5_RW02_PLACEHOLDER_SEMANTICS,
    field_provenance: V5_RW02_FIELD_PROVENANCE,
    lifecycle_fields: V5_RW02_DOCTORCRE_LIFECYCLE_FIELDS,
    link_field: V5_RW02_LINK_FIELD,
    credential_patterns: Object.fromEntries(
      Object.entries(V5_RW02_CREDENTIAL_PATTERNS).map(([k, re]) => [k, re.source])),
    page_checks: V5_RW02_PAGE_CHECKS,
    challenge_states: V5_RW02_CHALLENGE_STATES,
    stop_reason_ids: V5_RW02_STOP_REASON_IDS,
    admission_checks: V5_RW02_ADMISSION_CHECKS,
    duplicate_outcomes: V5_RW02_DUPLICATE_OUTCOMES,
    resume_outcomes: V5_RW02_RESUME_OUTCOMES,
    evidence_classes: V5_RW02_EVIDENCE_CLASSES,
    evidence_outcomes: V5_RW02_EVIDENCE_OUTCOMES,
    trust_scopes: V5_RW02_TRUST_SCOPES,
  };
}

export function v5Rw02PolicyDigest() {
  return digest(v5Rw02PolicyPreimage());
}

export function v5Rw02Projection() {
  return deepFreeze(answerBase({
    answer_kind: "rw02-projection.v1",
    catalog_item: "V5-RW02",
    policy_digest: v5Rw02PolicyDigest(),
    decision_ids: [...V5_RW02_DECISION_IDS],
    source_build_dependencies: ["V5-F01", "V5-F06", "V5-J301"],
    downstream: ["V5-RW01"],
    runtime_inputs_missing: [...V5_RW02_RUNTIME_EVIDENCE_INPUTS],
    seams_owed: [...V5_RW02_SEAMS],
    open_obligations: V5_RW02_OPEN_OBLIGATIONS,
    confirmation_freshness_bounded: false,
    adapter_admission: V5_RW02_ADAPTER_ADMISSION,
    excluded: ["unattended execution", "MFA/CAPTCHA bypass", "Salesforce state as DoctorCRE lifecycle",
      "global trust"],
  }));
}

/** The module's public surface, exactly. The suite proves exports equal this list. */
export const V5_RW02_PUBLIC_SURFACE = deepFreeze([
  "V5RW02Error",
  "V5_NO_EFFECTS",
  "V5_RW02_ACTION_KINDS",
  "V5_RW02_ACTION_KIND_KEYS",
  "V5_RW02_ADAPTER_ADMISSION",
  "V5_RW02_ADMISSION_CHECKS",
  "V5_RW02_CAPABILITY_LIFETIME_CEILING_SECONDS",
  "V5_RW02_CASE_ANCHOR_TYPES",
  "V5_RW02_CHALLENGE_STATES",
  "V5_RW02_CONSISTENCY_STATES",
  "V5_RW02_CREDENTIAL_PATTERNS",
  "V5_RW02_DECISION_IDS",
  "V5_RW02_DOCTORCRE_LIFECYCLE_FIELDS",
  "V5_RW02_DUPLICATE_OUTCOMES",
  "V5_RW02_EFFECT_CLASS",
  "V5_RW02_ENGAGEMENT_LINK_SEAM",
  "V5_RW02_EVALUATION_WINDOW_SEAM",
  "V5_RW02_EVIDENCE_CLASSES",
  "V5_RW02_EVIDENCE_OUTCOMES",
  "V5_RW02_EVIDENCE_SCHEMA_VERSION",
  "V5_RW02_EVIDENCE_STORE_SEAM",
  "V5_RW02_EXCLUDED_ACTIONS",
  "V5_RW02_EXECUTION_MODES",
  "V5_RW02_F06_ACTION",
  "V5_RW02_FIELD_FACT_CLASSES",
  "V5_RW02_FIELD_MAP_SEAM",
  "V5_RW02_FIELD_PROVENANCE",
  "V5_RW02_FIELD_SEMANTICS",
  "V5_RW02_IDEMPOTENCY_MARKER_SEAM",
  "V5_RW02_JOURNAL_HINTS",
  "V5_RW02_LINK_FIELD",
  "V5_RW02_MARKER_STATES",
  "V5_RW02_NAME_MATCH_STATES",
  "V5_RW02_OPEN_OBLIGATIONS",
  "V5_RW02_ORG_BINDING_SEAM",
  "V5_RW02_PAGE_CHECKS",
  "V5_RW02_PLACEHOLDER_SEMANTICS",
  "V5_RW02_POLICY_VERSION",
  "V5_RW02_PREVIEW_SCHEMA_VERSION",
  "V5_RW02_PROVIDER_EFFECT_STATES",
  "V5_RW02_PUBLIC_SURFACE",
  "V5_RW02_RESUME_OUTCOMES",
  "V5_RW02_RUNTIME_EVIDENCE_INPUTS",
  "V5_RW02_SALESFORCE_ADAPTER_SEAM",
  "V5_RW02_SCHEMA_VERSION",
  "V5_RW02_SEAMS",
  "V5_RW02_SEARCH_COMPLETENESS",
  "V5_RW02_SETTLED_DECISIONS",
  "V5_RW02_SOURCE_BINDING",
  "V5_RW02_STOP_REASON_IDS",
  "V5_RW02_SURFACE_HOMES",
  "V5_RW02_TARGET_READBACK_STATES",
  "V5_RW02_TRUST_SCOPES",
  "V5_RW02_WORKFLOW_STAGES",
  "buildActionPreview",
  "evaluateActionAdmission",
  "evaluateActionTrustWindow",
  "evaluateDuplicateSearch",
  "evaluatePageObservation",
  "evaluateResume",
  "evaluateWriteReadback",
  "rw02ConfirmationFreshnessBound",
  "rw02StepKey",
  "rw02WorkflowId",
  "v5Rw02PolicyDigest",
  "v5Rw02PolicyPreimage",
  "v5Rw02Projection",
]);
