// DoctorCRE v5 slice V5-F10: partner-scoped Mail and Calendar adapters — the
// pure connector kernel.
//
// ONE PACKAGE, INSTALLED SEPARATELY. Q134 and Q146 ask for a single governed
// adapter that Joe and Dell each install against their OWN account on their OWN
// device, with no shared credential store, no Studio-held partner credentials,
// and no dependency running the other way into core J1. That shape is what this
// file encodes: every evaluator takes a compiled INSTALLATION as its first
// input, and an installation names exactly one partner. There is no registry of
// installations here and no way to hold two at once, because the moment one
// object can speak for both partners the isolation is a convention rather than a
// structure.
//
// TWO KINDS OF NO, the same distinction the rest of the v5 lane draws:
//   * A POLICY ANSWER is RETURNED — a frozen result whose `decision` is
//     "allow", "refuse", "unavailable", "include", "exclude_unrelated",
//     "withhold_ambiguous" or "needs_independent_privacy_route", with a stable
//     `reason_id`. A caller may record it.
//   * A CONTRACT VIOLATION THROWS V5F10Error. An unknown field, an unregistered
//     connector operation, an installation config carrying a credential, an item
//     carrying raw correspondence bytes, or a request to turn a WITHHELD
//     classification into an ingestion candidate are not policy questions. The
//     module cannot read the request as a governed one at all, so it fails
//     closed rather than guessing which boundary was meant.
//
// THE TWO STRUCTURAL REFUSALS THAT ARE THE POINT OF THE SLICE, and both throw:
//
//   1. CREDENTIALS NEVER LEAVE THE ORIGIN DEVICE. compilePartnerInstallation
//      scans its config's FIELD NAMES for credential-bearing fragments before it
//      reads anything else. A config carrying a token, a secret, a key or a
//      cookie has already carried it into this process; answering a policy
//      question about it would be the wrong response to a boundary that has
//      already been crossed. The installation this module compiles is an
//      identity and a scope — partner, device, account, source system — and
//      there is no field, optional or otherwise, that a secret could ride in.
//   2. RAW CORRESPONDENCE NEVER LEAVES THE ORIGIN DEVICE EITHER.
//      classifyCorrespondenceItem scans for body, attachment, preview and
//      transcript field names the same way. What crosses this seam is a CONTENT
//      DIGEST and typed metadata — the exact shape F01's admitCorporateArtifact
//      already takes, which is why the minimum-necessary boundary costs nothing
//      to honour here. This module reads no message text and cannot.
//
// WHAT THIS FILE DOES NOT DECIDE, because something else in this tree already
// does and a second authority for a settled thing is a defect:
//
//   * THE PRIVACY BOUNDARY IS S01's. evaluatePrivacyBoundary decides whether a
//     declared class may be carried; nothing here re-implements the PHI list or
//     the aggregate privacy route, and the refusal ids are carried through
//     verbatim. Classification is MANDATORY on every item and is evaluated
//     BEFORE relevance, exactly as admitCorporateArtifact evaluates it before it
//     builds an artifact: an item excluded as "unrelated" without ever meeting
//     the privacy boundary would be content that was never classified at all,
//     which is not the same as content that was classified and dropped.
//   * NATIVE IDENTITY IS F01's. The (source_system, native_id, native_id_epoch)
//     triple, the recycled-epoch rule and the field-level transition all live in
//     record-source-authority.v5.js. This module groups a device's queue BY that
//     triple and never re-decides it: an epoch split is NAMED and both sides are
//     forwarded, so F01 refuses the recycled one against established state
//     rather than this module refusing it a second time from less information.
//   * ADMISSION IS F01's. toCorporateArtifactCandidate builds the request shape
//     admitCorporateArtifact takes and stops there. Every result says
//     `admitted: false`. Calling the admission from inside an adapter would be a
//     second door into the record layer.
//   * PARTNERHOOD IS identity.js's. isKnownPartner decides who is a partner.
//
// TAINT IS NEVER LOWERED AT THIS SEAM. Outlook is a registered authoritative
// home and a mailbox item's custody is corporate, but the VALUE crossing here
// describes correspondence authored outside CARR. Every candidate
// takes `untrusted_external` and the caller cannot supply a taint class at all —
// a field it could set would be a laundering seam.
//
// THE JUDGEMENT CALL, named rather than buried. F01 registers five evidence
// classes and none of them is a calendar class. A calendar event in Exchange is
// a mailbox item — it lives in the same store, under the same account, with the
// same change key — so both item kinds map to `corporate_mailbox_item`, and the
// F10-side `item_kind` is carried separately so the distinction is never lost.
// The alternative, a sixth F01 evidence class, is an F01-owned change and this
// slice does not make it; see `no_calendar_evidence_class` in
// partnerConnectorGaps().
//
// THE OFFLINE QUEUE IS A READ QUEUE, AND Q007.D1 IS WHY THAT MATTERS. V5 does
// not accept offline mutations. Nothing queued here is a mutation: every entry
// is an OBSERVATION the device made of its own mailbox, held until the device
// can hand it over. `accepts_offline_mutation: false` rides on every queue
// result, and the constant is proved against S01's own policy in the suite so
// the two cannot drift apart silently.
//
// THE MODULE IS PURE. No filesystem, no network, no database, no scheduler, no
// environment, no clock: `now` arrives from the caller on every evaluation that
// needs it. It sends nothing, persists nothing, activates nothing and accepts
// nothing. V5_NO_EFFECTS rides on every result to say so in the record.
//
// AND IT PRODUCES NO ACCEPTANCE. The consumer gate
// `partner-mail-calendar-connectors-accepted` is produced by an independent
// oracle against production partner devices. Deploying an adapter does not
// satisfy it and neither does passing this suite. evaluateIngestionScope says so
// in its own answer rather than leaving a reader to infer it, and combined
// Joe/Dell correspondence coverage is UNAVAILABLE from this module under every
// input it can be handed.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, isKnownPartner } from "./identity.js";
import { V5_NO_EFFECTS, evaluatePrivacyBoundary } from "./global-boundaries.v5.js";
import {
  V5_F01_ARTIFACT_SCHEMA_VERSION,
  V5_F01_EVIDENCE_CLASSES,
  V5_F01_HOMES,
  V5_F01_TAINT_CLASSES,
} from "./record-source-authority.v5.js";

export { V5_NO_EFFECTS };

export const V5_F10_SCHEMA_VERSION = "doctorcre-v5-partner-mail-calendar.v1";
export const V5_F10_POLICY_VERSION = 1;

export const V5_F10_INSTALLATION_SCHEMA_VERSION =
  "doctorcre-v5-f10-partner-installation.v1";
export const V5_F10_CLASSIFICATION_SCHEMA_VERSION =
  "doctorcre-v5-f10-correspondence-classification.v1";
export const V5_F10_CANDIDATE_SCHEMA_VERSION =
  "doctorcre-v5-f10-artifact-candidate.v1";
export const V5_F10_QUEUE_SCHEMA_VERSION =
  "doctorcre-v5-f10-offline-queue-reconciliation.v1";
export const V5_F10_PROJECTION_SCHEMA_VERSION =
  "doctorcre-v5-f10-connector-projection.v1";

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
// Control characters, bidirectional overrides, zero-width and other invisible
// format characters. An identifier that renders as another identifier is an
// identity split waiting to happen, so it is refused rather than normalized.
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/u;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

/**
 * The separator used to fold a native-identity triple into one grouping key.
 *
 * THE INVARIANT: the separator must be a character no validated identifier can
 * contain, or two different triples could fold to one key and one native item
 * would silently shadow another. NUL is refused by assertSafeText and is outside
 * EXTERNAL_IDENT, so it can never appear in any half; the suite proves that
 * directly rather than assuming it. WRITTEN AS AN ESCAPE, NEVER AS A LITERAL.
 */
const KEY_SEPARATOR = "\u0000";

export class V5F10Error extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5F10Error";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5F10Error(code, message, detail);
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

/** An open schema is an unenforced one: an unread field is a field nobody checked. */
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

function assertArray(value, path, { min = 0, max = 256 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < min) {
    fail("invalid_shape", `${path} must hold at least ${min} entries`, { path, length: value.length });
  }
  if (value.length > max) {
    fail("too_many_entries", `${path} may hold at most ${max} entries`, { path, length: value.length });
  }
  return value;
}

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
 * Instants are parsed, never inferred, and the CALENDAR is checked against the
 * literal fields before parsing. Date.parse normalizes an impossible date into a
 * different one — "2026-02-31T00:00:00Z" silently becomes 3 March — and an
 * observation stamped with an instant nobody wrote is bound to the wrong moment.
 * This slice reads calendars for a living; it does not get to be careless about
 * one.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path, value });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const h = Number(hour), mi = Number(minute), s = Number(second);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || h > 23 || mi > 59 || s > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path, value });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path, value });
  return parsed;
}

function assertSha256Ref(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference to a 64-character lower-case digest`, { path });
  }
  return value;
}

// ---------------------------------------------------------------------------
// The closed vocabularies. Every one of them is hashed into the policy preimage
// below, so a vocabulary that changes moves the digest and stale readers are
// refused — the defect a sibling module in this lane already paid for once by
// leaving an axis inline.
// ---------------------------------------------------------------------------

/** The two things a partner mailbox holds that this slice reads. */
export const V5_F10_ITEM_KINDS = deepFreeze(["calendar_event", "mail_message"]);

/**
 * The one F01 evidence class both item kinds take. Checked against F01's own
 * registry at module load rather than trusted as a literal, so removing it
 * upstream breaks this module loudly instead of leaving it emitting a class F01
 * no longer registers.
 */
export const V5_F10_EVIDENCE_CLASS = "corporate_mailbox_item";

/** The authoritative home a partner mailbox item's custody belongs to. */
export const V5_F10_AUTHORITATIVE_HOME = "outlook";

/**
 * Taint is never lowered at an adapter boundary. The caller cannot supply this;
 * a field it could set would be exactly the laundering seam the rule forbids.
 */
export const V5_F10_TAINT_CLASS = "untrusted_external";

/** This adapter's own identity on every provenance record it builds. */
export const V5_F10_ADAPTER_KIND = "v5_f10_partner_mail_calendar_adapter";

/** One retrieval class per item kind. F10's identity, not an external fact. */
export const V5_F10_RETRIEVAL_CLASSES = deepFreeze({
  mail_message: "partner_device_mail_read",
  calendar_event: "partner_device_calendar_read",
});

/** What the origin device may say about an item's business relevance. */
export const V5_F10_RELEVANCE_STATES = deepFreeze([
  "ambiguous", "relevant_business_context", "unrelated",
]);

/** The closed answer set of classifyCorrespondenceItem. */
export const V5_F10_CLASSIFICATION_DECISIONS = deepFreeze([
  "exclude_unrelated", "include", "needs_independent_privacy_route", "refuse", "withhold_ambiguous",
]);

/** Where an installation stands on its partner's own machine. */
export const V5_F10_DEPLOYMENT_STATES = deepFreeze(["deployed", "not_deployed", "unknown"]);

/**
 * The two states an installation cannot ingest from, kept together so the reason
 * is one rule. `unknown` sits with `not_deployed` on purpose: a deployment
 * nobody has observed is not a deployment, and reading silence as readiness is
 * the exact shape of claim this slice must not make.
 */
export const V5_F10_NON_INGESTING_DEPLOYMENT_STATES = deepFreeze(["not_deployed", "unknown"]);

/** What a caller may ask this connector to cover. */
export const V5_F10_INGESTION_SCOPES = deepFreeze(["combined_partners", "single_partner"]);

/**
 * The connector's whole operation registry, each labelled by mode. The slice's
 * effect_class is `read_ingestion_only_until_separate_action_capability`, so the
 * WRITE half exists here to be REFUSED BY NAME. An operation absent from this
 * registry is not a policy question — the module has no entry to judge it by —
 * and throws.
 */
export const V5_F10_OPERATIONS = deepFreeze({
  list_calendar_events: { mode: "read", item_kind: "calendar_event" },
  list_mail_messages: { mode: "read", item_kind: "mail_message" },
  read_calendar_event_metadata: { mode: "read", item_kind: "calendar_event" },
  read_mail_message_metadata: { mode: "read", item_kind: "mail_message" },
  accept_calendar_invitation: { mode: "write", item_kind: "calendar_event" },
  create_calendar_event: { mode: "write", item_kind: "calendar_event" },
  delete_mail_message: { mode: "write", item_kind: "mail_message" },
  move_mail_message: { mode: "write", item_kind: "mail_message" },
  send_mail_message: { mode: "write", item_kind: "mail_message" },
  update_calendar_event: { mode: "write", item_kind: "calendar_event" },
});

export const V5_F10_OPERATION_KEYS = deepFreeze(Object.keys(V5_F10_OPERATIONS).sort());
export const V5_F10_READ_OPERATIONS = deepFreeze(
  V5_F10_OPERATION_KEYS.filter(key => V5_F10_OPERATIONS[key].mode === "read"));
export const V5_F10_WRITE_OPERATIONS = deepFreeze(
  V5_F10_OPERATION_KEYS.filter(key => V5_F10_OPERATIONS[key].mode === "write"));

/**
 * Where a write would be decided if this slice ever gained one. Named as a seam
 * so a reader cannot mistake the refusal for "never" and cannot mistake the
 * absence of a decision for a decision.
 */
export const V5_F10_ACTION_CAPABILITY_SEAM =
  "step:v5-f10-partner-connector-action-capability-decision";

/** The consumer gate this slice's combined coverage waits on. Produced elsewhere. */
export const V5_F10_CONNECTOR_GATE_ID = "partner-mail-calendar-connectors-accepted";
export const V5_F10_INDEPENDENT_RECEIPT_STEP =
  "step:partner-mail-calendar-connectors-independent-receipt";
export const V5_F10_SECRETS_BOUNDARY_RECEIPT_STEP =
  "step:global-secrets-boundary-independent-receipt";
export const V5_F10_J1_PRODUCTION_STEP = "step:j1-core-production-outcome";

/** The dispositions the queue may reach. */
export const V5_F10_QUEUE_DISPOSITIONS = deepFreeze([
  "ambiguous_revision_order_refused", "deliver", "duplicate_suppressed",
]);

/**
 * Field-name fragments that mean a credential reached the installation config.
 * Checked on NAMES, before any value is read, because the check has to work
 * without ever looking at the thing it is refusing.
 */
export const V5_F10_CREDENTIAL_FRAGMENTS = deepFreeze([
  "api_key", "apikey", "bearer", "certificate", "cookie", "credential", "key_material",
  "oauth", "passphrase", "password", "private_key", "refresh", "secret", "session", "token",
]);

/**
 * Field-name fragments that mean raw correspondence reached the classifier. The
 * fragments are chosen not to collide with the metadata this seam DOES carry:
 * `content_digest` and `byte_length` name a measurement of bytes, never bytes.
 */
export const V5_F10_RAW_CONTENT_FRAGMENTS = deepFreeze([
  "attachment", "body", "html", "message_text", "plaintext", "preview",
  "raw", "snippet", "subject_line", "transcript",
]);

// The three upstream vocabularies this module binds to BY IMPORT rather than by
// literal. One that moves upstream must break here, loudly, at load.
if (!V5_F01_EVIDENCE_CLASSES.includes(V5_F10_EVIDENCE_CLASS)) {
  throw new V5F10Error("upstream_vocabulary_drift",
    `F01 no longer registers "${V5_F10_EVIDENCE_CLASS}"; this adapter has no evidence class to emit`,
    { registered: [...V5_F01_EVIDENCE_CLASSES] });
}
if (!V5_F01_TAINT_CLASSES.includes(V5_F10_TAINT_CLASS)) {
  throw new V5F10Error("upstream_vocabulary_drift",
    `F01 no longer registers the "${V5_F10_TAINT_CLASS}" taint class`,
    { registered: [...V5_F01_TAINT_CLASSES] });
}
if (!V5_F01_HOMES.includes(V5_F10_AUTHORITATIVE_HOME)) {
  throw new V5F10Error("upstream_vocabulary_drift",
    `F01 no longer registers "${V5_F10_AUTHORITATIVE_HOME}" as an authoritative home`,
    { registered: [...V5_F01_HOMES] });
}

// ---------------------------------------------------------------------------
// The installation. One partner, one device, one account, one source system.
// ---------------------------------------------------------------------------

const INSTALLATION_KEYS = Object.freeze([
  "account", "deployment_state", "device_id", "installation_version",
  "item_kinds", "partner_slug", "source_system",
]);

/**
 * Refuse a config whose FIELD NAMES carry a credential, before anything else is
 * read. This throws rather than returning a refusal on purpose: a refusal is an
 * answer a caller records and moves on from, and there is nothing to move on
 * from here — the secret is already in this process's memory. The only useful
 * response is to stop.
 */
function assertNoCredentialFields(object, path) {
  for (const key of Object.keys(object)) {
    const normalized = key.toLowerCase();
    const fragment = V5_F10_CREDENTIAL_FRAGMENTS.find(f => normalized.includes(f));
    if (fragment !== undefined) {
      fail("credential_in_installation_config",
        `${path}.${key} looks like it carries a credential ("${fragment}"); partner credentials`
        + " never leave the origin device and this adapter holds none",
        { path: `${path}.${key}`, key, fragment });
    }
  }
}

/**
 * Compile one partner's installation into a frozen, digest-bearing identity.
 *
 * The digest binds partner, device, account, source system and installed item
 * kinds. It is an identity for THIS installation and nothing else: it is not an
 * enrollment, not an attestation and not evidence for any gate.
 */
export function compilePartnerInstallation(config) {
  assertObject(config, "config");
  assertNoCredentialFields(config, "config");
  assertClosedKeys(config, INSTALLATION_KEYS, "config");
  assertRequiredKeys(config, INSTALLATION_KEYS, "config");

  const partner_slug = assertExternalIdent(config.partner_slug, "config.partner_slug", { maxLength: 64 });
  // Partnerhood is identity.js's test, not a second one written here.
  if (!isKnownPartner(partner_slug)) {
    fail("unknown_partner",
      `"${partner_slug}" is not a CARR partner; an installation belongs to exactly one partner`,
      { partner_slug });
  }

  const installation_version = assertSafeInteger(config.installation_version,
    "config.installation_version", { min: 1, max: 1000000 });
  const device_id = assertExternalIdent(config.device_id, "config.device_id", { maxLength: 128 });
  const source_system = assertExternalIdent(config.source_system, "config.source_system", { maxLength: 128 });
  const account = assertExternalIdent(config.account, "config.account", { maxLength: 255 });
  const deployment_state = assertEnum(config.deployment_state, V5_F10_DEPLOYMENT_STATES,
    "config.deployment_state", "unknown_deployment_state");

  assertArray(config.item_kinds, "config.item_kinds", { min: 1, max: V5_F10_ITEM_KINDS.length });
  const seen = new Set();
  config.item_kinds.forEach((kind, index) => {
    assertEnum(kind, V5_F10_ITEM_KINDS, `config.item_kinds[${index}]`, "unknown_item_kind");
    if (seen.has(kind)) {
      fail("duplicate_item_kind", `config.item_kinds repeats "${kind}"`,
        { path: `config.item_kinds[${index}]`, item_kind: kind });
    }
    seen.add(kind);
  });
  const item_kinds = [...seen].sort();

  const preimage = {
    schema_version: V5_F10_INSTALLATION_SCHEMA_VERSION,
    policy_version: V5_F10_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    installation_version, partner_slug, device_id, source_system, account, item_kinds,
  };
  return deepFreeze({
    ...preimage,
    deployment_state,
    installation_digest: digest(preimage),
    // The partners this installation speaks for. Exactly one, always, and it is
    // a LIST so that "one" is a fact a reader can check rather than a shape they
    // have to infer from a singular field name.
    covers_partners: [partner_slug],
    // NO CREDENTIAL-SHAPED FIELD APPEARS ON THIS OBJECT, not even a `false` flag
    // saying there is none. The machine-readable claim belongs at policy level,
    // in v5F10PolicyPreimage().installation, where it is hashed; repeating it
    // here would put a credential-named key on the one object that travels, and
    // a key that must never hold a value is better off not existing.

    effects: V5_NO_EFFECTS,
  });
}

function assertCompiledInstallation(value, path) {
  assertObject(value, path);
  if (value.schema_version !== V5_F10_INSTALLATION_SCHEMA_VERSION) {
    fail("uncompiled_installation",
      `${path} must be the result of compilePartnerInstallation`,
      { path, schema_version: typeof value.schema_version === "string" ? value.schema_version : null });
  }
  if (typeof value.installation_digest !== "string" || !SHA256_REF.test(value.installation_digest)) {
    fail("uncompiled_installation", `${path}.installation_digest is missing or malformed`, { path });
  }
  return value;
}

/** The exact canonical bytes an installation digest is taken over. */
export function partnerInstallationCanonicalBytes(installation) {
  const compiled = assertCompiledInstallation(installation, "installation");
  return canonicalJson({
    schema_version: compiled.schema_version,
    policy_version: compiled.policy_version,
    tenant: compiled.tenant,
    installation_version: compiled.installation_version,
    partner_slug: compiled.partner_slug,
    device_id: compiled.device_id,
    source_system: compiled.source_system,
    account: compiled.account,
    item_kinds: [...compiled.item_kinds],
  });
}

// ---------------------------------------------------------------------------
// The connector operation gate. The effect_class, enforced one call at a time.
// ---------------------------------------------------------------------------

const OPERATION_REQUEST_KEYS = Object.freeze(["installation", "operation"]);

/**
 * Decide whether one connector operation is inside this slice.
 *
 * THE ORDER IS THE POLICY. The write refusal comes FIRST, before the deployment
 * check, because a write is outside this slice whether or not the partner's
 * machine is up. Answering "not deployed" to a send attempt would read as though
 * deploying the adapter would make sending available, which is the opposite of
 * what the effect_class says.
 */
export function evaluateConnectorOperation(request) {
  assertObject(request, "request");
  assertClosedKeys(request, OPERATION_REQUEST_KEYS, "request");
  assertRequiredKeys(request, OPERATION_REQUEST_KEYS, "request");
  const installation = assertCompiledInstallation(request.installation, "request.installation");

  if (typeof request.operation !== "string" ||
      !Object.prototype.hasOwnProperty.call(V5_F10_OPERATIONS, request.operation)) {
    fail("unknown_connector_operation",
      `"${String(request.operation)}" is not a registered connector operation`,
      { operation: typeof request.operation === "string" ? request.operation : null,
        registered: [...V5_F10_OPERATION_KEYS] });
  }
  const entry = V5_F10_OPERATIONS[request.operation];
  const base = {
    schema_version: V5_F10_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    installation_digest: installation.installation_digest,
    partner_slug: installation.partner_slug,
    operation: request.operation,
    mode: entry.mode,
    item_kind: entry.item_kind,
    // Q134/Q146: neither partner's connector is on core J1's critical path.
    blocks_core_j1: false,
    connector_gate_id: V5_F10_CONNECTOR_GATE_ID,
    connector_gate_satisfied: false,
    effects: V5_NO_EFFECTS,
  };

  if (entry.mode === "write") {
    return deepFreeze({
      decision: "refuse", reason_id: "write_capability_not_in_this_slice", ...base,
      effect_class: "read_ingestion_only_until_separate_action_capability",
      action_capability_seam: V5_F10_ACTION_CAPABILITY_SEAM,
      write_operations: [...V5_F10_WRITE_OPERATIONS],
    });
  }
  if (V5_F10_NON_INGESTING_DEPLOYMENT_STATES.includes(installation.deployment_state)) {
    return deepFreeze({
      decision: "refuse", reason_id: "partner_installation_not_deployed", ...base,
      deployment_state: installation.deployment_state,
    });
  }
  if (!installation.item_kinds.includes(entry.item_kind)) {
    return deepFreeze({
      decision: "refuse", reason_id: "item_kind_not_installed", ...base,
      installed_item_kinds: [...installation.item_kinds],
    });
  }
  return deepFreeze({
    decision: "allow", reason_id: "read_ingestion_within_effect_class", ...base,
    retrieval_class: V5_F10_RETRIEVAL_CLASSES[entry.item_kind],
  });
}

// ---------------------------------------------------------------------------
// The data boundary: minimum necessary business context, ambiguity stays private.
// ---------------------------------------------------------------------------

const CLASSIFY_REQUEST_KEYS = Object.freeze(["installation", "item", "now"]);
const ITEM_KEYS = Object.freeze([
  "account", "byte_length", "content_digest", "declared_data_classes", "item_kind",
  "native_identity", "native_version", "observed_at", "relevance_state",
]);
const NATIVE_IDENTITY_KEYS = Object.freeze(["native_id", "native_id_epoch", "source_system"]);

/**
 * Refuse an item whose FIELD NAMES carry raw correspondence, before anything
 * else is read. Same reasoning as the credential check: by the time a body field
 * is present the bytes have already crossed a boundary this slice exists to
 * hold, and a returned refusal would be a record of a crossing rather than a
 * prevention of one.
 */
function assertNoRawContentFields(object, path) {
  for (const key of Object.keys(object)) {
    const normalized = key.toLowerCase();
    const fragment = V5_F10_RAW_CONTENT_FRAGMENTS.find(f => normalized.includes(f));
    if (fragment !== undefined) {
      fail("raw_content_must_not_leave_origin_device",
        `${path}.${key} looks like it carries correspondence content ("${fragment}"); this seam`
        + " carries a content digest and typed metadata, never message text",
        { path: `${path}.${key}`, key, fragment });
    }
  }
}

function assertItemNativeIdentity(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, NATIVE_IDENTITY_KEYS, path);
  assertRequiredKeys(value, NATIVE_IDENTITY_KEYS, path);
  return deepFreeze({
    source_system: assertExternalIdent(value.source_system, `${path}.source_system`, { maxLength: 128 }),
    native_id: assertExternalIdent(value.native_id, `${path}.native_id`, { maxLength: 255 }),
    native_id_epoch: assertExternalIdent(value.native_id_epoch, `${path}.native_id_epoch`, { maxLength: 128 }),
  });
}

/**
 * Classify one mailbox item for a single partner's installation.
 *
 * THE ORDER, and every step of it is policy:
 *   1. raw-content field names  — throws; the bytes must not be here at all
 *   2. shape and identity       — throws; an unreadable item is not a judgement
 *   3. installation isolation   — refuses; this account is not that partner's
 *   4. the S01 privacy boundary — UNCONDITIONAL, before relevance, so an item
 *      dropped as unrelated is one that was classified and dropped rather than
 *      one that was never classified at all
 *   5. relevance                — include / exclude / withhold
 *
 * AMBIGUITY RESOLVES TO PRIVATE. Q146's boundary is "unrelated or ambiguous
 * content remains excluded/private", so `ambiguous` is its own answer and it
 * never falls through to include. There is no default and no confidence
 * threshold that could turn a maybe into a yes.
 */
export function classifyCorrespondenceItem(request) {
  assertObject(request, "request");
  assertClosedKeys(request, CLASSIFY_REQUEST_KEYS, "request");
  assertRequiredKeys(request, CLASSIFY_REQUEST_KEYS, "request");
  const installation = assertCompiledInstallation(request.installation, "request.installation");
  const now = assertInstant(request.now, "request.now");

  const raw = assertObject(request.item, "request.item");
  assertNoRawContentFields(raw, "request.item");
  assertClosedKeys(raw, ITEM_KEYS, "request.item");
  assertRequiredKeys(raw, ITEM_KEYS, "request.item");

  const item_kind = assertEnum(raw.item_kind, V5_F10_ITEM_KINDS, "request.item.item_kind", "unknown_item_kind");
  const native_identity = assertItemNativeIdentity(raw.native_identity, "request.item.native_identity");
  const account = assertExternalIdent(raw.account, "request.item.account", { maxLength: 255 });
  const native_version = assertSafeText(raw.native_version, "request.item.native_version", { maxLength: 255 });
  const content_digest = assertSha256Ref(raw.content_digest, "request.item.content_digest");
  const byte_length = assertSafeInteger(raw.byte_length, "request.item.byte_length", { min: 0 });
  const observedAt = assertInstant(raw.observed_at, "request.item.observed_at");
  const relevance_state = assertEnum(raw.relevance_state, V5_F10_RELEVANCE_STATES,
    "request.item.relevance_state", "unknown_relevance_state");
  assertArray(raw.declared_data_classes, "request.item.declared_data_classes", { min: 1, max: 32 });
  raw.declared_data_classes.forEach((dataClass, index) => {
    assertExternalIdent(dataClass, `request.item.declared_data_classes[${index}]`, { maxLength: 128 });
  });
  const declared_data_classes = [...new Set(raw.declared_data_classes)].sort();

  const base = {
    schema_version: V5_F10_CLASSIFICATION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    installation_digest: installation.installation_digest,
    partner_slug: installation.partner_slug,
    item_kind,
    native_identity,
    declared_data_classes,
    minimum_necessary: true,
    raw_content_observed: false,
    credentials_observed: 0,
    blocks_core_j1: false,
    admissible_item: null,
    delivery_key: null,
    effects: V5_NO_EFFECTS,
  };

  // Step 3. Per-partner isolation, checked structurally: one installation reads
  // one account on one device, and an item from anywhere else is not this
  // partner's to look at.
  if (!installation.item_kinds.includes(item_kind)) {
    return deepFreeze({
      decision: "refuse", reason_id: "item_kind_not_installed", ...base,
      installed_item_kinds: [...installation.item_kinds],
    });
  }
  if (account !== installation.account) {
    return deepFreeze({ decision: "refuse", reason_id: "account_outside_partner_installation", ...base });
  }
  if (native_identity.source_system !== installation.source_system) {
    return deepFreeze({ decision: "refuse", reason_id: "native_identity_source_mismatch", ...base });
  }
  if (observedAt > now) {
    return deepFreeze({ decision: "refuse", reason_id: "observed_after_now", ...base });
  }

  // Step 4. S01 decides, and it decides on EVERY item.
  const privacy = evaluatePrivacyBoundary({ data_classes: declared_data_classes });
  if (privacy.decision === "refuse") {
    return deepFreeze({
      decision: "refuse", reason_id: privacy.reason_id, ...base,
      prohibited_classes: [...(privacy.prohibited_classes ?? [])],
      amendment_required: privacy.amendment_required,
    });
  }
  if (privacy.decision === "needs_independent_privacy_route") {
    return deepFreeze({
      decision: "needs_independent_privacy_route", reason_id: privacy.reason_id, ...base,
      routed_classes: [...(privacy.routed_classes ?? [])],
      required_evidence: privacy.required_evidence,
    });
  }

  // Step 5. Relevance. Neither of the first two answers carries the item
  // forward, and neither one can be turned into a candidate.
  if (relevance_state === "unrelated") {
    return deepFreeze({ decision: "exclude_unrelated", reason_id: "unrelated_correspondence_excluded", ...base });
  }
  if (relevance_state === "ambiguous") {
    return deepFreeze({ decision: "withhold_ambiguous", reason_id: "ambiguity_remains_private", ...base });
  }

  const admissible_item = deepFreeze({
    item_kind, account, native_identity, native_version,
    content_digest, byte_length, observed_at: raw.observed_at, declared_data_classes,
  });
  return deepFreeze({
    decision: "include", reason_id: "relevant_business_context_within_boundary", ...base,
    admissible_item,
    // The exactly-once key the offline queue reconciles on. It binds the F01
    // native-identity triple to the observed content, so a replay of the same
    // observation collapses and a genuine revision does not.
    delivery_key: digest({
      schema_version: V5_F10_QUEUE_SCHEMA_VERSION,
      source_system: native_identity.source_system,
      native_id: native_identity.native_id,
      native_id_epoch: native_identity.native_id_epoch,
      content_digest,
    }),
  });
}

// ---------------------------------------------------------------------------
// The Source Integrator seam: an included item becomes an F01 admission REQUEST.
// ---------------------------------------------------------------------------

const CANDIDATE_REQUEST_KEYS = Object.freeze(["classification", "evidence_ref", "installation"]);

/**
 * Build the exact `request.artifact` shape admitCorporateArtifact takes.
 *
 * This module does NOT call it. F01 owns admission, holds the tenant and the
 * server instant, and answers with its own refusal matrix; an adapter that
 * admitted its own output would be a second door into the record layer. Every
 * result therefore says `admitted: false` and names the one function that can
 * change that.
 *
 * A WITHHELD CLASSIFICATION THROWS. Turning an ambiguous or unrelated item into
 * an ingestion candidate is not a policy question the caller gets an answer to —
 * it is the caller ignoring the answer it already has.
 */
export function toCorporateArtifactCandidate(request) {
  assertObject(request, "request");
  assertClosedKeys(request, CANDIDATE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, CANDIDATE_REQUEST_KEYS, "request");
  const installation = assertCompiledInstallation(request.installation, "request.installation");
  const evidence_ref = assertExternalIdent(request.evidence_ref, "request.evidence_ref", { maxLength: 255 });

  const classification = assertObject(request.classification, "request.classification");
  if (classification.schema_version !== V5_F10_CLASSIFICATION_SCHEMA_VERSION) {
    fail("uncompiled_classification",
      "request.classification must be the result of classifyCorrespondenceItem",
      { schema_version: typeof classification.schema_version === "string"
          ? classification.schema_version : null });
  }
  if (classification.decision !== "include" || classification.admissible_item === null ||
      classification.admissible_item === undefined) {
    fail("candidate_from_non_included_classification",
      `a "${String(classification.decision)}" classification carries no admissible item; excluded and`
      + " withheld correspondence does not become an ingestion candidate",
      { decision: typeof classification.decision === "string" ? classification.decision : null });
  }
  if (classification.installation_digest !== installation.installation_digest) {
    fail("classification_outside_installation",
      "request.classification was produced for a different partner installation",
      { classification_installation_digest: typeof classification.installation_digest === "string"
          ? classification.installation_digest : null,
        installation_digest: installation.installation_digest });
  }

  const item = classification.admissible_item;
  const artifact = deepFreeze({
    source_system: item.native_identity.source_system,
    source_account: item.account,
    native_identity: {
      source_system: item.native_identity.source_system,
      native_id: item.native_identity.native_id,
      native_id_epoch: item.native_identity.native_id_epoch,
    },
    native_version: item.native_version,
    content_digest: item.content_digest,
    byte_length: item.byte_length,
    observed_at: item.observed_at,
    provenance: {
      adapter_kind: V5_F10_ADAPTER_KIND,
      evidence_ref,
      retrieval_class: V5_F10_RETRIEVAL_CLASSES[item.item_kind],
    },
    evidence_class: V5_F10_EVIDENCE_CLASS,
    declared_data_classes: [...item.declared_data_classes],
    // Never lowered, never caller-supplied.
    taint_class: V5_F10_TAINT_CLASS,
  });

  return deepFreeze({
    schema_version: V5_F10_CANDIDATE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    installation_digest: installation.installation_digest,
    partner_slug: installation.partner_slug,
    // F10's own distinction, carried alongside because F01 registers no calendar
    // evidence class and both kinds land on corporate_mailbox_item.
    item_kind: item.item_kind,
    authoritative_home: V5_F10_AUTHORITATIVE_HOME,
    artifact_schema_version: V5_F01_ARTIFACT_SCHEMA_VERSION,
    artifact,
    delivery_key: classification.delivery_key,
    // The three statements that keep this from reading as an ingestion.
    admitted: false,
    f01_admission_required: true,
    f01_admission_entrypoint: "admitCorporateArtifact",
    blocks_core_j1: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The offline queue. Exactly-once delivery of observations off a device that was
// out of contact — and no mutation, ever.
// ---------------------------------------------------------------------------

const QUEUE_REQUEST_KEYS = Object.freeze(["already_delivered", "entries", "installation"]);

function nativeGroupKey(nativeIdentity) {
  return [nativeIdentity.source_system, nativeIdentity.native_id, nativeIdentity.native_id_epoch]
    .join(KEY_SEPARATOR);
}

function nativeItemKey(nativeIdentity) {
  return [nativeIdentity.source_system, nativeIdentity.native_id].join(KEY_SEPARATOR);
}

/**
 * Reconcile a partner device's queued observations.
 *
 * WHAT IT DECIDES, and it is exactly one thing: WHICH OF THESE OBSERVATIONS
 * SHOULD CROSS THE WIRE. That is a delivery question, and it is the queue's to
 * answer.
 *
 * WHAT IT REFUSES TO DECIDE, because F01 already decides it from more
 * information than a device queue holds:
 *   * WHICH REVISION WINS. Two observations of the same native item with
 *     different content are both delivered; F01's version comparator orders them
 *     against established state. The later one is MARKED as a revision so a
 *     reader can see the pair, and that is the whole of the claim made here.
 *   * WHETHER A RECYCLED NATIVE ID IS THE SAME RECORD. It is not, and F01 says
 *     so with its own refusal against established state. The queue NAMES the
 *     epoch split so the two sides cannot be read as a revision pair, and
 *     forwards both rather than refusing one from less context.
 *
 * THE ONE PLACE IT REFUSES OUTRIGHT is the case nobody downstream can untangle:
 * two different contents for the same native item at the SAME observed instant.
 * There is no order to take and no winner to pick, so both are refused rather
 * than one being chosen arbitrarily.
 *
 * AND NOTHING HERE IS A MUTATION. Q007.D1 settles that v5 accepts no offline
 * mutation; every entry in this queue is an observation the device made of its
 * own mailbox, and `accepts_offline_mutation: false` rides on the result.
 */
export function reconcileOfflineQueue(request) {
  assertObject(request, "request");
  assertClosedKeys(request, QUEUE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["entries", "installation"], "request");
  const installation = assertCompiledInstallation(request.installation, "request.installation");
  assertArray(request.entries, "request.entries", { min: 0, max: 256 });

  const alreadyDelivered = new Set();
  if (request.already_delivered !== undefined && request.already_delivered !== null) {
    assertArray(request.already_delivered, "request.already_delivered", { min: 0, max: 4096 });
    request.already_delivered.forEach((key, index) => {
      assertSha256Ref(key, `request.already_delivered[${index}]`);
      alreadyDelivered.add(key);
    });
  }

  // Pass one: validate every entry and index it. A queue that answers some
  // entries and throws on a later one has already told the caller something it
  // may act on, so validation completes before any disposition is taken.
  const rows = request.entries.map((entry, index) => {
    const path = `request.entries[${index}]`;
    assertObject(entry, path);
    if (entry.schema_version !== V5_F10_CLASSIFICATION_SCHEMA_VERSION) {
      fail("uncompiled_classification",
        `${path} must be the result of classifyCorrespondenceItem`,
        { path, schema_version: typeof entry.schema_version === "string" ? entry.schema_version : null });
    }
    if (entry.decision !== "include" || entry.admissible_item === null ||
        entry.admissible_item === undefined) {
      fail("queued_non_included_classification",
        `${path} is a "${String(entry.decision)}" classification; excluded and withheld`
        + " correspondence is never queued for delivery",
        { path, decision: typeof entry.decision === "string" ? entry.decision : null });
    }
    if (entry.installation_digest !== installation.installation_digest) {
      fail("entry_outside_installation",
        `${path} was produced for a different partner installation`,
        { path, entry_installation_digest: typeof entry.installation_digest === "string"
            ? entry.installation_digest : null });
    }
    const item = assertObject(entry.admissible_item, `${path}.admissible_item`);
    return {
      index,
      delivery_key: assertSha256Ref(entry.delivery_key, `${path}.delivery_key`),
      native_identity: assertItemNativeIdentity(item.native_identity, `${path}.admissible_item.native_identity`),
      content_digest: assertSha256Ref(item.content_digest, `${path}.admissible_item.content_digest`),
      observed_at: item.observed_at,
      observed_ms: assertInstant(item.observed_at, `${path}.admissible_item.observed_at`),
      item_kind: assertEnum(item.item_kind, V5_F10_ITEM_KINDS,
        `${path}.admissible_item.item_kind`, "unknown_item_kind"),
    };
  });

  // Pass two: group. A group is one native item under one epoch; an item key
  // spanning two epochs is an epoch split and is named rather than merged.
  const groups = new Map();
  const epochsByItem = new Map();
  for (const row of rows) {
    const groupKey = nativeGroupKey(row.native_identity);
    if (!groups.has(groupKey)) groups.set(groupKey, []);
    groups.get(groupKey).push(row);
    const itemKey = nativeItemKey(row.native_identity);
    if (!epochsByItem.has(itemKey)) epochsByItem.set(itemKey, new Set());
    epochsByItem.get(itemKey).add(row.native_identity.native_id_epoch);
  }

  // A same-instant content conflict is unorderable; the whole conflicting set is
  // marked BEFORE any entry is dispositioned, so both sides refuse rather than
  // whichever one the iteration happened to reach first.
  const unorderable = new Set();
  for (const group of groups.values()) {
    const byInstant = new Map();
    for (const row of group) {
      if (!byInstant.has(row.observed_at)) byInstant.set(row.observed_at, new Set());
      byInstant.get(row.observed_at).add(row.content_digest);
    }
    for (const [instant, digests] of byInstant) {
      if (digests.size > 1) {
        for (const row of group) if (row.observed_at === instant) unorderable.add(row.index);
      }
    }
  }

  const seenInBatch = new Set();
  const delivered = [];
  const entries = rows.map(row => {
    const groupKey = nativeGroupKey(row.native_identity);
    const itemKey = nativeItemKey(row.native_identity);
    const epoch_split = epochsByItem.get(itemKey).size > 1;
    const base = {
      index: row.index,
      delivery_key: row.delivery_key,
      item_kind: row.item_kind,
      native_identity: row.native_identity,
      observed_at: row.observed_at,
      // Named, never merged. F01 decides what a recycled id means.
      native_id_epoch_split: epoch_split,
    };
    if (unorderable.has(row.index)) {
      return {
        ...base, disposition: "ambiguous_revision_order_refused",
        reason_id: "same_instant_content_conflict_has_no_order", delivered: false,
        revision_of_native_item: false,
      };
    }
    if (alreadyDelivered.has(row.delivery_key)) {
      return {
        ...base, disposition: "duplicate_suppressed", reason_id: "already_delivered",
        delivered: false, revision_of_native_item: false,
      };
    }
    if (seenInBatch.has(row.delivery_key)) {
      return {
        ...base, disposition: "duplicate_suppressed", reason_id: "repeat_in_batch",
        delivered: false, revision_of_native_item: false,
      };
    }
    // A revision is a second CONTENT for the same native item under the same
    // epoch. The claim is only that the pair exists; the ordering is F01's.
    const revision = groups.get(groupKey)
      .some(other => other.index !== row.index && other.content_digest !== row.content_digest &&
        !unorderable.has(other.index) && other.observed_ms < row.observed_ms);
    seenInBatch.add(row.delivery_key);
    delivered.push(row.delivery_key);
    return {
      ...base, disposition: "deliver", reason_id: "first_delivery_of_observation",
      delivered: true, revision_of_native_item: revision,
    };
  });

  return deepFreeze({
    schema_version: V5_F10_QUEUE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    installation_digest: installation.installation_digest,
    partner_slug: installation.partner_slug,
    entries,
    delivered_keys: [...delivered].sort(),
    delivered_count: delivered.length,
    suppressed_count: entries.filter(e => e.disposition === "duplicate_suppressed").length,
    refused_count: entries.filter(e => e.disposition === "ambiguous_revision_order_refused").length,
    epoch_split_count: entries.filter(e => e.native_id_epoch_split).length,
    // Q007.D1. Every entry above is an observation; none of them is a mutation.
    accepts_offline_mutation: false,
    revision_order_decided: false,
    revision_order_authority: "record-source-authority.v5.js",
    blocks_core_j1: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The partner connector gate. What this slice may claim, and what it may not.
// ---------------------------------------------------------------------------

const SCOPE_REQUEST_KEYS = Object.freeze(["installation", "requested_partner_slug", "requested_scope"]);

/**
 * Decide what correspondence coverage a caller may claim.
 *
 * COMBINED JOE/DELL COVERAGE IS UNAVAILABLE FROM THIS MODULE UNDER EVERY INPUT.
 * The catalog is explicit that it stays unavailable until the independent
 * `partner-mail-calendar-connectors-accepted` receipt, and that DEPLOYING OR
 * TESTING AN ADAPTER DOES NOT SATISFY THAT GATE. So there is no argument, no
 * option and no field by which a caller could reach an available answer here: a
 * request cannot carry a receipt at all — it would be an unknown field — and the
 * answer names the gate and the producing step instead.
 *
 * AND NEITHER PARTNER DEPLOYMENT BLOCKS CORE J1. `blocks_core_j1: false` is on
 * every answer including the unavailable ones, because "Dell's adapter is not
 * deployed" and "core J1 is held up" are exactly the two facts this slice must
 * never let a reader conflate.
 */
export function evaluateIngestionScope(request) {
  assertObject(request, "request");
  assertClosedKeys(request, SCOPE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["installation", "requested_scope"], "request");
  const installation = assertCompiledInstallation(request.installation, "request.installation");
  const requested_scope = assertEnum(request.requested_scope, V5_F10_INGESTION_SCOPES,
    "request.requested_scope", "unknown_ingestion_scope");

  const base = {
    schema_version: V5_F10_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    installation_digest: installation.installation_digest,
    installation_partner_slug: installation.partner_slug,
    requested_scope,
    deployment_state: installation.deployment_state,
    connector_gate_id: V5_F10_CONNECTOR_GATE_ID,
    connector_gate_satisfied: false,
    combined_partner_coverage_claimed: false,
    // The two ways somebody would try to shortcut the gate, refused by name.
    satisfied_by_adapter_deployment: false,
    satisfied_by_adapter_test: false,
    blocks_core_j1: false,
    effects: V5_NO_EFFECTS,
  };

  if (requested_scope === "combined_partners") {
    if ("requested_partner_slug" in request) {
      fail("partner_slug_with_combined_scope",
        "combined_partners covers both partners and takes no partner slug",
        { path: "request.requested_partner_slug" });
    }
    return deepFreeze({
      decision: "unavailable",
      reason_id: "combined_partner_coverage_requires_independent_receipt", ...base,
      required_receipt_step: V5_F10_INDEPENDENT_RECEIPT_STEP,
      producer_role: "independent_partner_connector_oracle",
    });
  }

  assertRequiredKeys(request, ["requested_partner_slug"], "request");
  const requested_partner_slug = assertExternalIdent(request.requested_partner_slug,
    "request.requested_partner_slug", { maxLength: 64 });
  if (!isKnownPartner(requested_partner_slug)) {
    fail("unknown_partner", `"${requested_partner_slug}" is not a CARR partner`,
      { partner_slug: requested_partner_slug });
  }
  const scoped = { ...base, requested_partner_slug };
  if (requested_partner_slug !== installation.partner_slug) {
    return deepFreeze({
      decision: "refuse", reason_id: "partner_scope_outside_installation", ...scoped,
    });
  }
  if (V5_F10_NON_INGESTING_DEPLOYMENT_STATES.includes(installation.deployment_state)) {
    return deepFreeze({
      decision: "unavailable", reason_id: "partner_installation_not_deployed", ...scoped,
    });
  }
  return deepFreeze({
    decision: "allow", reason_id: "single_partner_scope_within_installation", ...scoped,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest. Nothing situational is
// bound — no instant, actor, device, session or acceptance — so two callers
// describing the same policy reach the same digest. The digest is an identity
// for these bytes and nothing else: not an acceptance, not a receipt, and not
// evidence for the connector gate.
// ---------------------------------------------------------------------------

export function v5F10PolicyPreimage() {
  return {
    schema_version: V5_F10_SCHEMA_VERSION,
    policy_version: V5_F10_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    requirement_ids: ["Q134", "Q146"],
    decision_ids: ["Q134.D2", "Q146.D1"],
    installation: {
      schema_version: V5_F10_INSTALLATION_SCHEMA_VERSION,
      item_kinds: [...V5_F10_ITEM_KINDS],
      deployment_states: [...V5_F10_DEPLOYMENT_STATES],
      non_ingesting_deployment_states: [...V5_F10_NON_INGESTING_DEPLOYMENT_STATES],
      partners_per_installation: 1,
      holds_credentials: false,
      credentials_centralized: false,
      credential_field_fragments: [...V5_F10_CREDENTIAL_FRAGMENTS],
    },
    effect_class: {
      effect_class: "read_ingestion_only_until_separate_action_capability",
      operations: V5_F10_OPERATION_KEYS.map(operation => ({
        operation,
        mode: V5_F10_OPERATIONS[operation].mode,
        item_kind: V5_F10_OPERATIONS[operation].item_kind,
      })),
      read_operations: [...V5_F10_READ_OPERATIONS],
      write_operations: [...V5_F10_WRITE_OPERATIONS],
      writes_to_source: false,
      action_capability_seam: V5_F10_ACTION_CAPABILITY_SEAM,
    },
    data_boundary: {
      schema_version: V5_F10_CLASSIFICATION_SCHEMA_VERSION,
      relevance_states: [...V5_F10_RELEVANCE_STATES],
      decisions: [...V5_F10_CLASSIFICATION_DECISIONS],
      ambiguity_resolves_to: "withhold_ambiguous",
      unrelated_resolves_to: "exclude_unrelated",
      privacy_boundary_authority: "global-boundaries.v5.js",
      privacy_evaluated_before_relevance: true,
      raw_content_field_fragments: [...V5_F10_RAW_CONTENT_FRAGMENTS],
      raw_content_crosses_seam: false,
    },
    source_integration: {
      schema_version: V5_F10_CANDIDATE_SCHEMA_VERSION,
      artifact_schema_version: V5_F01_ARTIFACT_SCHEMA_VERSION,
      evidence_class: V5_F10_EVIDENCE_CLASS,
      authoritative_home: V5_F10_AUTHORITATIVE_HOME,
      taint_class: V5_F10_TAINT_CLASS,
      taint_caller_supplied: false,
      adapter_kind: V5_F10_ADAPTER_KIND,
      retrieval_classes: Object.keys(V5_F10_RETRIEVAL_CLASSES).sort()
        .map(item_kind => ({ item_kind, retrieval_class: V5_F10_RETRIEVAL_CLASSES[item_kind] })),
      admits_its_own_output: false,
      native_identity_authority: "record-source-authority.v5.js",
    },
    offline_queue: {
      schema_version: V5_F10_QUEUE_SCHEMA_VERSION,
      dispositions: [...V5_F10_QUEUE_DISPOSITIONS],
      accepts_offline_mutation: false,
      revision_order_decided: false,
      epoch_split_merged: false,
      max_entries: 256,
    },
    connector_gate: {
      gate_id: V5_F10_CONNECTOR_GATE_ID,
      required_receipt_step: V5_F10_INDEPENDENT_RECEIPT_STEP,
      producer_role: "independent_partner_connector_oracle",
      ingestion_scopes: [...V5_F10_INGESTION_SCOPES],
      combined_coverage_reachable_here: false,
      satisfied_by_adapter_deployment: false,
      satisfied_by_adapter_test: false,
      blocks_core_j1: false,
      upstream_receipt_steps: [V5_F10_J1_PRODUCTION_STEP, V5_F10_SECRETS_BOUNDARY_RECEIPT_STEP],
    },
  };
}

/** The deterministic `sha256:` digest of the closed V5-F10 connector policy. */
export function v5F10PolicyDigest() {
  return digest(v5F10PolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5F10PolicyCanonicalBytes() {
  return canonicalJson(v5F10PolicyPreimage());
}

/**
 * The zero-effect projection of the whole connector: what is settled, what the
 * policy hashes to, and the explicit statement that reading it accepts nothing.
 */
export function v5F10ConnectorProjection(options = {}) {
  assertObject(options, "options");
  assertClosedKeys(options, ["expected_policy_digest"], "options");
  const policyDigest = v5F10PolicyDigest();
  if (options.expected_policy_digest !== undefined) {
    if (typeof options.expected_policy_digest !== "string" ||
        !SHA256_REF.test(options.expected_policy_digest)) {
      fail("invalid_expected_digest", "options.expected_policy_digest must be a sha256: reference",
        { path: "options.expected_policy_digest" });
    }
    if (options.expected_policy_digest !== policyDigest) {
      fail("stale_expected_digest",
        "the policy no longer hashes to the expected digest; re-read it rather than acting on the stale one",
        { expected: options.expected_policy_digest, actual: policyDigest });
    }
  }
  return deepFreeze({
    schema_version: V5_F10_PROJECTION_SCHEMA_VERSION,
    policy_version: V5_F10_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    policy_digest: policyDigest,
    requirement_ids: ["Q134", "Q146"],
    // Later runtime and acceptance inputs. Named so nobody mistakes this
    // projection for one of them; none is produced or satisfied here.
    connector_gate_id: V5_F10_CONNECTOR_GATE_ID,
    connector_gate_satisfied: false,
    partner_connector_receipt_present: false,
    global_secrets_boundary_receipt_present: false,
    j1_core_production_outcome_present: false,
    combined_partner_coverage_available: false,
    blocks_core_j1: false,
    accepts_anything: false,
    gaps: partnerConnectorGaps(),
    effects: V5_NO_EFFECTS,
  });
}

/**
 * The named gaps. Each is a MISSING FACT this repository does not hold, not a
 * build task deferred out of laziness, and none of them is guessed anywhere
 * above.
 */
export function partnerConnectorGaps() {
  return deepFreeze([
    {
      gap: "no_partner_connector_acceptance_receipt",
      where: "independent_partner_connector_oracle",
      what: "the partner-mail-calendar-connectors-accepted gate is produced by an independent"
        + " oracle against production partner devices; no receipt exists, so combined Joe/Dell"
        + " correspondence coverage is unavailable from every path in this module and no"
        + " argument can reach an available answer",
      landed: false,
    },
    {
      gap: "no_secrets_boundary_receipt",
      where: "step:global-secrets-boundary-independent-receipt",
      what: "the global secrets boundary this slice's credential rule sits under has no"
        + " independent receipt and no module in this tree; the structural refusal here is a"
        + " field-name check on one seam, which is not the same thing as an accepted boundary",
      landed: false,
    },
    {
      gap: "no_calendar_evidence_class",
      where: "mcp-server/src/record-source-authority.v5.js",
      what: "F01 registers five evidence classes and none is a calendar class, so calendar"
        + " events are admitted as corporate_mailbox_item — true of an Exchange store, and the"
        + " F10-side item_kind is carried alongside so the distinction is not lost. A sixth"
        + " F01 class is an F01-owned change this slice does not make",
      landed: false,
    },
    {
      gap: "no_mailbox_field_authority_registry",
      where: "the future Neon-backed F01 registry",
      what: "F01's field authority registry ships no entry for a mailbox or calendar entity, so"
        + " resolveObservation cannot yet run over a delivered item; this module builds"
        + " admitCorporateArtifact request shapes and invents no registry entry to sit under",
      landed: false,
    },
    {
      gap: "no_provider_client",
      where: "the partner's own device",
      what: "nothing here reaches a mail or calendar provider. The typed item is READ ON THE"
        + " PARTNER'S DEVICE by code that holds that partner's credential and never ships it;"
        + " that device-side reader is a deployment artifact, not a repository fact, and no"
        + " transport, consent grant or enrollment is modelled or assumed here",
      landed: false,
    },
    {
      gap: "no_action_capability_decision",
      where: V5_F10_ACTION_CAPABILITY_SEAM,
      what: "where a connector WRITE would ever be permitted is not decided; every registered"
        + " write operation refuses by name and the seam is hashed into the policy preimage, so"
        + " the day the clause is written the policy digest moves and stale readers refuse",
      landed: false,
    },
  ]);
}
