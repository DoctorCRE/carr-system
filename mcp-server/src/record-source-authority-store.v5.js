// DoctorCRE v5 slice V5-F01 phase 2: the persistence tail.
//
// The reviewed pure kernel in record-source-authority.v5.js decides. This module
// is what makes those decisions RECORDS: it loads the stored policy and the
// stored prior state, derives the actor and the instant from the server, runs
// the kernel unchanged, and hands the exact canonical preimages to the
// security-definer functions in ops (domain.sql) that own the compare-and-swap,
// the append-only history and the recomputed integrity.
//
// THE DIVISION OF LABOUR, because it is the whole design:
//
//   KERNEL      Judges. Owns the vocabulary, the refusal matrix, the version
//               comparison and the exact bytes each record hashes to. Unchanged
//               and unwrapped: this module imports it and reimplements none of
//               it.
//   THIS MODULE Derives. Loads policy and prior state FROM THE DATABASE, takes
//               the actor from the authenticated transaction context and the
//               instant from the server, refuses caller authority injection
//               before any write, and builds the envelopes.
//   DATABASE    Enforces. Recomputes every digest from committed bytes, refuses
//               a stale policy or state digest, refuses a broken event chain,
//               refuses direct DML, and returns the readback.
//
// NOTHING IS TRUSTED IN ONE DIRECTION ONLY. The caller cannot supply a tenant, an
// actor, a clock, a prior artifact, a prior link, a current state, an evaluated
// decision or a digest. This module cannot supply the database an actor either:
// ops.f01_context_actor_slug() derives its own from the server-established
// transaction context, and every write here first verifies that the database's
// answer matches the handler's authenticated one, refusing when they differ.
//
// WHICH SCHEMA THIS MODULE REQUIRES, said plainly because it is now more than
// domain.sql. recordDocumentIdentity below calls the SIX-argument
// ops.f01_record_document and passes a provenance statement on every write, and
// it reads ops.f01_document_version_source. Both arrive with the document-source
// hunk (ops/document-derivative-registration.candidate.sql), applied AFTER
// domain.sql in the same database. Against a database carrying only the
// four-argument writer every document write fails on a missing function; the
// local gate names the missing functions up front rather than letting that
// surface later, and this module does not degrade to the old writer, because a
// path that completes a document with no statement about its origin is the exact
// thing the producer rule exists to remove.
//
// evaluateArtifactDeletion additionally reads ops.f01_retention_clock and
// ops.f01_retention_clock_digest, which arrive WITH domain.sql. They are what make
// a retention period start at the server-stamped instant the record layer took
// custody of an artifact rather than at the instant its source says it observed
// it; against a database carrying the older domain schema the deletion path fails
// on a missing function, and this module does not fall back to the source's
// timestamp, because measuring retention from a value a source can backdate is
// the defect the clock exists to remove.
//
// WHAT THIS MODULE STILL IS NOT. It registers nothing: v5F01ToolRegistrations()
// below is a DESCRIPTION the parent may register from, and this file does not
// touch tools.js, mcp.js, the mutation registry or any generated catalog. It
// performs no provider call, sends nothing, deletes no bytes and completes no
// acceptance. F01 remains incomplete until the parent lands integration, the
// SCAC v26 surface, the disposable-database proof, CI and review.

import { canonicalJson, digest } from "./artifact-trust.js";
import {
  ORGANIZATION_TENANT_ID,
  isKnownActor,
  authorizationClassForActor,
} from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_F01_AUTHORITY_INJECTION_FRAGMENTS,
  V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION,
  V5_F01_DOCUMENT_SCHEMA_VERSION,
  V5_F01_HOLD_STATES,
  V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
  V5_F01_PROPOSAL_SCHEMA_VERSION,
  V5_F01_RESERVED_DERIVATIVE_KINDS,
  compileFieldAuthorityRegistry,
  compileRetentionRegistry,
  fieldAuthorityRegistryPreimage,
  retentionRegistryPreimage,
  resolveObservation,
  admitCorporateArtifact,
  evaluateDerivativeRegistration,
  evaluateParsedProposal,
  projectDocumentIdentity,
  evaluateDeletion,
  v5F01DecisionSubsetDigest,
  v5F01PolicyDigest,
} from "./record-source-authority.v5.js";
// THE DOCUMENT HALF OF THE DERIVATIVE-REGISTRATION RULE, imported rather than
// reimplemented. This module and document-derivative-registration.v5.js import
// each other: that module needs this one's envelope contract and record
// builders, and recordDocumentIdentity below needs its binding decision and its
// composer. The cycle is deliberate and is kept SAFE IN ONE DIRECTION ONLY by
// the other module having no module-scope read of anything exported here — every
// use of V5_F01_DERIVED_ONLY_FIELDS and V5_F01_STORE_RECORD_KINDS over there
// happens inside a function body or inside its exported assertion, never while
// its body is evaluating. Were that not true, importing THIS module first would
// evaluate that one against uninitialized bindings and throw a ReferenceError
// before a single test ran, which is why both import orders are asserted in the
// suite rather than assumed.
import {
  V5_F01_DOCUMENT_PROVENANCE_STATES,
  assertDocumentSourceDeclaration,
  composeDocumentSourceEnvelopes,
  evaluateDocumentSourceBinding,
} from "./document-derivative-registration.v5.js";

export const V5_F01_STORE_SCHEMA_VERSION =
  "doctorcre-v5-f01-record-source-authority-store.v1";

export const V5_F01_ENVELOPE_SCHEMA_VERSION =
  "doctorcre-v5-f01-stored-record-envelope.v1";

export const V5_F01_STORED_POLICY_SCHEMA_VERSION =
  "doctorcre-v5-f01-stored-policy.v1";
export const V5_F01_STORED_FIELD_STATE_SCHEMA_VERSION =
  "doctorcre-v5-f01-stored-field-state.v1";
export const V5_F01_STORED_DOCUMENT_SCHEMA_VERSION =
  "doctorcre-v5-f01-stored-document-version.v1";
export const V5_F01_STORED_HOLD_SCHEMA_VERSION =
  "doctorcre-v5-f01-stored-preservation-hold.v1";
export const V5_F01_STORED_DELETION_SCHEMA_VERSION =
  "doctorcre-v5-f01-stored-deletion-evaluation.v1";
export const V5_F01_STORED_DERIVATIVE_LINK_SCHEMA_VERSION =
  "doctorcre-v5-f01-stored-derivative-source-link.v1";

/** The exact record_kind vocabulary the ops relations enforce. */
export const V5_F01_STORE_RECORD_KINDS = Object.freeze([
  "stored_policy_version",
  "stored_field_state",
  "stored_source_event",
  "stored_state_transition",
  "stored_mutation_receipt",
  "stored_reconciliation_item",
  "stored_corporate_artifact",
  "stored_parsed_proposal",
  "stored_proposal_link",
  "stored_derivative_link",
  "stored_document_version",
  // One document version's statement about where that version came from. It is a
  // record in its own right rather than a field on the version, because "derived
  // from that artifact", "authored here" and "nobody knows" are three different
  // facts and an ABSENT link says none of them.
  "stored_document_source_provenance",
  "stored_preservation_hold",
  "stored_deletion_evaluation",
]);

export const V5_F01_OPERATIONS = Object.freeze([
  "read-record-source-authority",
  "register-record-source-authority-policy",
  "record-source-observation",
  "record-corporate-artifact",
  "record-parsed-proposal",
  "register-derivative-source-link",
  "record-document-identity",
  "record-artifact-preservation-hold",
  "evaluate-artifact-deletion",
]);

export class V5F01StoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5F01StoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5F01StoreError(code, message, detail);
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
// The closed caller surface.
//
// TWO GUARDS, and they name different attempts on purpose.
//
//   AUTHORITY INJECTION — a field purporting to confer authority. Reused from
//     the kernel by import rather than copied, so the two lists cannot drift.
//
//   DERIVED-VALUE INJECTION — a field the SERVER owns: the tenant, the instant,
//     the prior artifact, the prior link, the current state, the evaluated
//     decision, a digest of something this module computes. These are not
//     authority claims, so the kernel's list does not name them, but a caller
//     supplying one would be choosing the evidence its own write is judged
//     against. That is the same failure wearing different clothes.
//
// A DIGEST IS NOT ALWAYS AN INJECTION, and the difference matters. A caller may
// name `artifact_digest` to REFERENCE a stored artifact, and may state the
// candidate registry digests on the policy tool because the contract binds that
// tool to exact digests. Both are CHECKED against the database or against a
// recomputation, never trusted; a reference that resolves to nothing refuses,
// and a stated digest that does not match refuses. Everything else in the
// derived list is refused by name.
// ---------------------------------------------------------------------------

export const V5_F01_DERIVED_ONLY_FIELDS = deepFreeze([
  "tenant", "now", "server_time", "recorded_at", "evaluated_at", "installed_at",
  "installed_by", "recorded_by", "updated_by", "actor_slug", "sponsor",
  "sponsoring_human_slug", "authenticated_identity", "human", "via", "client_id",
  "current_state", "prior_state", "prior_artifact", "prior_link", "prior_holds",
  "hold_inventory", "holds", "created_at", "decision", "reason_id", "outcome",
  "applied", "policy_digest", "domain_policy_digest", "registry_digest",
  "state_digest", "event_digest", "receipt_digest", "transition_digest",
  "document_digest", "proposal_digest", "link_digest", "hold_digest",
  "evaluation_digest", "envelope_digest", "record_digest", "event_seq",
  "last_event_digest", "owner_source", "authoritative_home",
  // Derived by the server or loaded from the record layer for the derivative
  // seam. `produced_at` is the server instant of the transaction that registered
  // the link, `registered_by`/`registered_at` are the derived principal and
  // instant, `source_created_at` is loaded from the stored artifact, and
  // `derivative_coverage` is the record layer's own answer about whether the
  // registered links for an artifact are the whole set — the one value a caller
  // must never be able to state.
  "produced_at", "registered_by", "registered_at", "source_created_at",
  "derivative_coverage", "derivative_coverage_state", "derivative_coverage_digest",
  "registered_derivative_kinds", "derivatives",
  // THE RETENTION CLOCK AND THE CUSTODY INSTANT IT COMES FROM. A caller that
  // could state either would be choosing when its own artifact's retention
  // period started — which is the whole reason the period is measured from a
  // server stamp rather than from the source's observed instant. Both are read
  // off the stored row instead, and `source_content_digest` joins them because it
  // is the loaded artifact's own bytes, never a caller's description of them.
  "retention_clock", "retention_clock_digest", "custody", "source_content_digest",
  // The two the document seam adds. `provenance_digest` is the digest of a record
  // this module builds, and `derivative_link_digest` is the digest of the link it
  // registers; a caller that could state either would be naming the bytes its own
  // write is checked against. Both are read back from the database instead.
  "provenance_digest", "derivative_link_digest",
]);

function assertNoAccessorsOrHiddenKeys(object, path) {
  if (Object.prototype.hasOwnProperty.call(object, "__proto__")) {
    fail("prototype_key_refused", `${path}.__proto__ is an own key; the shape is refused rather than read`,
      { path });
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

function assertNoInjectedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (allowed.includes(key)) continue;
    const normalized = key.toLowerCase();
    for (const fragment of V5_F01_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_authority_field_refused",
          `${path}.${key} names authority the caller cannot supply; the handler derives actor and tenant`,
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

/** An open schema is a contract violation: an unread field is an unenforced one. */
function assertClosed(object, allowed, required, path) {
  if (!isPlainObject(object)) {
    fail("invalid_shape", `${path} must be a plain object`, { path });
  }
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

function assertIdempotencyKey(value, path) {
  if (typeof value !== "string" || value.length === 0 || value.length > 200) {
    fail("invalid_idempotency_key", `${path} must be a string of 1..200 characters`, { path });
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)) {
    fail("invalid_idempotency_key", `${path} is not a permitted idempotency key`, { path });
  }
  return value;
}

// ---------------------------------------------------------------------------
// The closed caller schemas, one per operation.
//
// `tenant`, `now`, `current_state`, `prior_artifact`, `prior_link` and every
// evaluated result are absent from every list below, and the derived guard above
// refuses them by name so the refusal says what was attempted.
// ---------------------------------------------------------------------------

const OBSERVATION_KEYS = Object.freeze([
  "entity", "field", "source_system", "account", "native_identity", "value_digest",
  "version", "observed_at", "provenance", "declared_data_classes", "taint_class", "readback",
]);

const ARTIFACT_KEYS = Object.freeze([
  "source_system", "source_class", "source_account", "native_identity", "native_version",
  "content_digest", "byte_length", "observed_at", "provenance", "evidence_class",
  "declared_data_classes", "taint_class",
]);

const PROPOSAL_KEYS = Object.freeze([
  "artifact_digest", "source_system", "source_account", "proposed_bindings",
  "confidence", "evidence_refs", "observed_at", "supersedes_link_digest",
]);

const DOCUMENT_KEYS = Object.freeze([
  "document_class", "neon_identity", "object_storage_identity", "onedrive_identity",
  "preparation_state", "delivery_state", "signature_state", "validity_state", "version_state",
]);

const HOLD_KEYS = Object.freeze(["hold_id", "artifact_digest", "state", "reason"]);

const DELETION_SUBJECT_KEYS = Object.freeze([
  "artifact_class", "artifact_home", "artifact_digest", "satisfied_constraints",
  "deletion_proof",
]);

// The caller-facing registration surface, and it is deliberately FLAT and small.
// The producing workflow names itself, the derivative it just made, and the
// immutable evidence of having made it. Everything else — the tenant, the actor,
// the instant the production is bound to, the source artifact's own creation
// time, and every coverage question — is derived or loaded, and the derived
// guard above refuses each of them by name.
const DERIVATIVE_REGISTRATION_KEYS = Object.freeze([
  "source_artifact_digest", "derivative_kind", "derivative_id", "derivative_content_digest",
  "producer_workflow", "producer_run_ref", "evidence_ref", "evidence_digest",
]);

const READ_SELECTOR_KEYS = Object.freeze([
  "kind", "entity", "field", "artifact_digest", "document_id", "hold_id",
]);

export const V5_F01_READ_KINDS = Object.freeze([
  "current_policy", "field_state", "field_events", "state_transitions",
  "mutation_receipts", "reconciliation_items", "artifact", "proposal_links",
  "derivative_links", "derivative_coverage",
  "document", "document_versions", "holds", "hold_history", "deletion_evaluations",
]);

const OPERATION_SCHEMAS = deepFreeze({
  "read-record-source-authority": {
    write: false, humanOnly: false, authorityOnly: false,
    keys: ["schema_version", "selector"], required: ["selector"],
  },
  "register-record-source-authority-policy": {
    write: true, humanOnly: true, authorityOnly: true,
    keys: ["schema_version", "idempotency_key", "field_registry", "retention_registry",
      "field_registry_digest", "retention_registry_digest", "expected_prior_policy_digest"],
    required: ["idempotency_key", "field_registry", "retention_registry",
      "field_registry_digest", "retention_registry_digest"],
  },
  "record-source-observation": {
    write: true, humanOnly: false, authorityOnly: false,
    keys: ["schema_version", "idempotency_key", "observation"],
    required: ["idempotency_key", "observation"],
  },
  "record-corporate-artifact": {
    write: true, humanOnly: false, authorityOnly: false,
    keys: ["schema_version", "idempotency_key", "artifact"],
    required: ["idempotency_key", "artifact"],
  },
  "record-parsed-proposal": {
    write: true, humanOnly: false, authorityOnly: false,
    keys: ["schema_version", "idempotency_key", "proposal"],
    required: ["idempotency_key", "proposal"],
  },
  "register-derivative-source-link": {
    write: true, humanOnly: false, authorityOnly: false,
    keys: ["schema_version", "idempotency_key", "registration"],
    required: ["idempotency_key", "registration"],
  },
  // `source` IS REQUIRED, and requiring it knowingly breaks the contract this
  // operation shipped with. A document version recorded with no statement about
  // where it came from is exactly the untracked derivative the producer rule
  // exists to prevent, and an absent statement has never meant "no source" — it
  // means nobody said. There is no default: a caller that does not know states
  // legacy_provenance_unknown and says on what basis, which is a different record
  // from "authored here" and reads as one.
  "record-document-identity": {
    write: true, humanOnly: false, authorityOnly: false,
    keys: ["schema_version", "idempotency_key", "document", "source",
      "expected_prior_document_digest"],
    required: ["idempotency_key", "document", "source"],
  },
  "record-artifact-preservation-hold": {
    write: true, humanOnly: true, authorityOnly: true,
    keys: ["schema_version", "idempotency_key", "hold", "expected_prior_hold_digest"],
    required: ["idempotency_key", "hold"],
  },
  "evaluate-artifact-deletion": {
    write: true, humanOnly: false, authorityOnly: false,
    keys: ["schema_version", "idempotency_key", "subject"],
    required: ["idempotency_key", "subject"],
  },
});

/** The closed caller schemas, for the parent's registration and for tests. */
export function v5F01StoreOperationSchemas() {
  return OPERATION_SCHEMAS;
}

/**
 * The registration description the parent may build a tool surface from.
 *
 * A DESCRIPTION, NOT A REGISTRATION. This module does not reach tools.js, mcp.js
 * or the mutation registry, and calling this function registers nothing.
 */
export function v5F01ToolRegistrations() {
  const roles = {
    "read-record-source-authority":
      "Read current registries and selected F01 records with recomputed integrity; no side write is required.",
    "register-record-source-authority-policy":
      "Append one exact field-authority plus retention-registry version using prior-digest CAS; no defaults or entries are invented.",
    "record-source-observation":
      "Judge evidence against stored policy/current state and atomically persist transition/event/receipt or reconciliation.",
    "record-corporate-artifact":
      "Persist one immutable source-agnostic artifact identity after privacy, provenance, prior-identity and time validation.",
    "record-parsed-proposal":
      "Persist a reviewable proposal and reversible history-preserving link to a stored artifact; never write a fact.",
    "register-derivative-source-link":
      "Bind one derived record to the exact stored artifact it came from, under the authenticated producer identity, before that derivative is complete; provenance only, never an inventory and never a deletion permission.",
    "record-document-identity":
      "Persist and read back one coherent versioned document identity/state record together with the required statement of where that version came from, registering its source link when it is derived; no byte transfer or send.",
    "record-artifact-preservation-hold":
      "Append a hold or release state for one stored artifact; never delete the artifact.",
    "evaluate-artifact-deletion":
      "Evaluate stored policy/holds/derivatives and persist the bounded evaluation/deletion receipt; never perform deletion.",
  };
  const handlers = {
    "read-record-source-authority": "readRecordSourceAuthority",
    "register-record-source-authority-policy": "registerRecordSourceAuthorityPolicy",
    "record-source-observation": "recordSourceObservation",
    "record-corporate-artifact": "recordCorporateArtifact",
    "record-parsed-proposal": "recordParsedProposal",
    "register-derivative-source-link": "registerDerivativeSourceLink",
    "record-document-identity": "recordDocumentIdentity",
    "record-artifact-preservation-hold": "recordArtifactPreservationHold",
    "evaluate-artifact-deletion": "evaluateArtifactDeletion",
  };
  return deepFreeze(V5_F01_OPERATIONS.map(name => ({
    name,
    write: OPERATION_SCHEMAS[name].write,
    humanOnly: OPERATION_SCHEMAS[name].humanOnly,
    authorityOnly: OPERATION_SCHEMAS[name].authorityOnly,
    role: roles[name],
    handler: handlers[name],
    input_keys: [...OPERATION_SCHEMAS[name].keys],
    required_keys: [...OPERATION_SCHEMAS[name].required],
    // Named rather than implied. Migration 0626 carries domain.sql and the
    // document-source hunk, recordSourceAuthorityStoreTools below is the door
    // tools.js registers, and SCAC v77 (0627) seals all nine verbs into the
    // registry the runtime selector reads. Acceptance is a separate, human act.
    registered_in_scac: true,
    registered_in_mutation_registry: true,
    migration_bound: true,
    accepted: false,
  })));
}

// ---------------------------------------------------------------------------
// The authenticated transaction context.
//
// The actor arrives from the handler's own authenticated context — never from a
// tool payload — and is then CHECKED AGAINST THE DATABASE's independently
// derived answer. Two derivations that disagree is not a value to reconcile; it
// is a request that cannot be attributed, so it refuses before any write.
// ---------------------------------------------------------------------------

const CONTEXT_KEYS = Object.freeze(["actor"]);

function assertAuthenticatedContext(context) {
  assertClosed(context ?? {}, CONTEXT_KEYS, CONTEXT_KEYS, "context");
  const actor = context.actor;
  if (!isPlainObject(actor) || !isKnownActor(actor.slug)) {
    fail("unauthenticated_actor",
      "context.actor must be an authenticated actor from the server-established grant",
      { path: "context.actor" });
  }
  return deepFreeze({
    slug: actor.slug,
    human: actor.human === true,
    authorization_class: authorizationClassForActor(actor),
    derived_by: "authenticated_handler_context",
  });
}

function assertOperationAuthority(operation, principal) {
  const schema = OPERATION_SCHEMAS[operation];
  if (schema.humanOnly && principal.human !== true) {
    fail("human_only_operation_refused",
      `${operation} is humanOnly; ${principal.slug} is not a human principal`,
      { operation, actor_slug: principal.slug });
  }
  if (schema.authorityOnly && principal.authorization_class !== "verified_partner") {
    fail("authority_only_operation_refused",
      `${operation} is authorityOnly; ${principal.slug} holds ${principal.authorization_class}`,
      { operation, actor_slug: principal.slug,
        authorization_class: principal.authorization_class });
  }
  return true;
}

// ---------------------------------------------------------------------------
// Envelopes.
//
// `record` is the exact preimage the kernel produced (or, for the four store-own
// kinds, the exact preimage defined here). `record_digest` is its digest, and
// the ops CHECK constraints recompute both inside PostgreSQL, so an envelope
// that lies about its own bytes cannot be stored at all.
// ---------------------------------------------------------------------------

function storeEnvelope(record_kind, record, extra = {}) {
  if (!V5_F01_STORE_RECORD_KINDS.includes(record_kind)) {
    fail("unknown_record_kind", `"${record_kind}" is not a registered stored record kind`,
      { record_kind });
  }
  return deepFreeze({
    schema_version: V5_F01_ENVELOPE_SCHEMA_VERSION,
    record_kind,
    tenant: ORGANIZATION_TENANT_ID,
    record,
    record_digest: digest(record),
    domain_policy_digest: v5F01PolicyDigest(),
    decision_subset_digest: v5F01DecisionSubsetDigest(),
    ...extra,
  });
}

/** The exact canonical bytes an envelope hashes to, for the byte-for-byte fixtures. */
export function v5F01EnvelopeCanonicalBytes(envelope) {
  return canonicalJson(envelope);
}

export function v5F01StoreEnvelope(record_kind, record, extra = {}) {
  return storeEnvelope(record_kind, record, extra);
}

// ---------------------------------------------------------------------------
// Store-own record preimages. Four kinds have no kernel preimage because the
// kernel deliberately stores nothing: the installed policy version, the current
// field state, the stored document version and the preservation hold.
// ---------------------------------------------------------------------------

export function storedPolicyRecord({
  registry_version, field_registry, field_registry_digest,
  retention_registry, retention_registry_digest, prior_policy_digest,
  installed_by, installed_at,
}) {
  return {
    schema_version: V5_F01_STORED_POLICY_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    registry_version,
    field_registry,
    field_registry_digest,
    retention_registry,
    retention_registry_digest,
    domain_policy_digest: v5F01PolicyDigest(),
    decision_subset_digest: v5F01DecisionSubsetDigest(),
    prior_policy_digest: prior_policy_digest ?? null,
    installed_by,
    installed_at,
  };
}

export function storedFieldStateRecord({
  entity, field, account, native_identity, value_digest, version, owner_source,
  authoritative_home, observed_at, event_seq, last_event_digest, policy_digest,
  updated_by, updated_at,
}) {
  return {
    schema_version: V5_F01_STORED_FIELD_STATE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    entity, field,
    account: account ?? null,
    native_identity: native_identity ?? null,
    value_digest, version, owner_source, authoritative_home,
    observed_at,
    event_seq,
    last_event_digest,
    policy_digest,
    updated_by,
    updated_at,
  };
}

export function storedDocumentRecord({
  document_class, neon_identity, object_storage_identity, onedrive_identity,
  preparation_state, delivery_state, signature_state, validity_state, version_state,
  official_filing_state, prior_document_digest, recorded_by, recorded_at,
}) {
  return {
    schema_version: V5_F01_STORED_DOCUMENT_SCHEMA_VERSION,
    document_identity_schema_version: V5_F01_DOCUMENT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    document_class,
    neon_identity,
    object_storage_identity: object_storage_identity ?? null,
    onedrive_identity: onedrive_identity ?? null,
    preparation_state, delivery_state, signature_state, validity_state, version_state,
    official_filing_state,
    prior_document_digest: prior_document_digest ?? null,
    homes: {
      identity_and_state: "neon_record_layer",
      working_and_sealed_bytes: "object_storage",
      official_executed_copy: "onedrive",
    },
    recorded_by,
    recorded_at,
  };
}

export function storedHoldRecord({
  hold_id, artifact_digest, state, reason, placed_at, released_at,
  prior_hold_digest, recorded_by, recorded_at,
}) {
  return {
    schema_version: V5_F01_STORED_HOLD_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    hold_id,
    artifact_digest,
    state,
    reason,
    placed_at,
    released_at: released_at ?? null,
    prior_hold_digest: prior_hold_digest ?? null,
    recorded_by,
    recorded_at,
  };
}

export function storedDeletionEvaluationRecord({
  artifact_class, artifact_home, artifact_digest, decision, reason_id,
  retention_registry_digest, hold_inventory_digest, derivative_coverage_state,
  derivative_coverage_digest, retention_clock, retention_clock_digest,
  deletion_receipt, evaluated_by, evaluated_at,
}) {
  return {
    schema_version: V5_F01_STORED_DELETION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    artifact_class, artifact_home, artifact_digest,
    decision, reason_id,
    retention_registry_digest,
    // THE TRIGGER THE PERIOD WAS MEASURED FROM, beside the registry digest and on
    // the same terms as the hold inventory and the coverage answer: the projected
    // clock travels in the record, and the digest of the database's own full
    // answer travels beside it so ops.f01_record_deletion_evaluation can
    // re-derive it under the lock it already holds and refuse a forged or stale
    // one. Neither is a caller value; both are read off the stored artifact row.
    retention_clock: retention_clock ?? null,
    retention_clock_digest: retention_clock_digest ?? null,
    hold_inventory_digest,
    // The coverage answer the evaluation was taken against, and the digest of
    // it. The database re-derives the digest at apply time, exactly as it does
    // for the hold inventory, so a link registered between the decision and the
    // write refuses instead of being missed.
    derivative_coverage_state,
    derivative_coverage_digest,
    deletion_receipt: deletion_receipt ?? null,
    evaluated_by,
    evaluated_at,
  };
}

/**
 * One registered derivative-source link, as the record layer stores it.
 *
 * The kernel produced everything about the LINK; this adds only the two values
 * the server owns — which authenticated producer principal registered it and
 * when. Neither may be supplied by a caller, and ops.f01_register_derivative_link
 * re-derives `registered_by` and refuses a record that names anyone else.
 */
export function storedDerivativeLinkRecord({ link, registered_by, registered_at }) {
  return {
    schema_version: V5_F01_STORED_DERIVATIVE_LINK_SCHEMA_VERSION,
    derivative_link_schema_version: V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    source_artifact_digest: link.source_artifact_digest,
    derivative_kind: link.derivative_kind,
    derivative_id: link.derivative_id,
    derivative_content_digest: link.derivative_content_digest,
    producer_workflow: link.producer_workflow,
    producer_run_ref: link.producer_run_ref,
    produced_at: link.produced_at,
    evidence_ref: link.evidence_ref,
    evidence_digest: link.evidence_digest,
    registration_is_provenance: link.registration_is_provenance,
    is_exhaustive_inventory: link.is_exhaustive_inventory,
    establishes_coverage: link.establishes_coverage,
    permits_deletion: link.permits_deletion,
    registered_by,
    registered_at,
  };
}

// ---------------------------------------------------------------------------
// Rehydrating a stored registry into the compiled shape the kernel accepts.
//
// The kernel refuses any registry it did not compile, and it proves that by
// RECOMPUTING the digest rather than trusting the `compiled: true` flag. That is
// exactly the property wanted here: the stored preimage is rebuilt into the
// compiled shape and the kernel's own check decides whether the bytes still hash
// to the digest the database recorded. A tampered stored registry therefore
// refuses inside the kernel, without this module needing a second validator.
// ---------------------------------------------------------------------------

function rehydrateRegistry(preimage, expected_digest, label) {
  if (!isPlainObject(preimage)) {
    fail("corrupt_stored_registry", `the stored ${label} is not a readable object`, { label });
  }
  const recomputed = digest(preimage);
  if (recomputed !== expected_digest) {
    fail("corrupt_stored_registry",
      `the stored ${label} no longer hashes to its recorded digest; it is refused, not repaired`,
      { label, expected: expected_digest, actual: recomputed });
  }
  return Object.freeze({ compiled: true, ...preimage, registry_digest: recomputed });
}

/** Project a stored record down to exactly the keys a kernel entry point accepts. */
function projectKeys(source, keys) {
  const out = {};
  for (const key of keys) {
    if (source[key] !== undefined) out[key] = source[key];
  }
  return out;
}

const KERNEL_CURRENT_STATE_KEYS = Object.freeze([
  "entity", "field", "tenant", "account", "native_identity", "value_digest", "version",
  "owner_source", "observed_at", "event_seq", "last_event_digest",
]);

/**
 * Build one derivative-registration request for the kernel out of a flat caller
 * payload, the LOADED source artifact and the server's own instant.
 *
 * Shared by the public registration operation and by the parsed-proposal
 * producer path below, so the two cannot drift into registering different
 * things. `produced_at` is the server instant every time: a producer's own clock
 * is not evidence about when the record layer saw the production, and a caller
 * that could state it could place a derivative before the artifact it came from.
 */
/**
 * Project one row from ops.f01_stored_artifact down to the closed shape the
 * kernel accepts for a LOADED source artifact.
 *
 * THE CONTENT DIGEST IS THE POINT OF THIS FUNCTION. `artifact_digest` identifies
 * the stored artifact RECORD; `content_digest` is the digest of the bytes that
 * record describes, and it is the one the kernel needs to refuse a byte-identical
 * copy registered as a derivative of the thing it copies.
 *
 * A STORED ARTIFACT WITHOUT ONE IS REFUSED, NOT WORKED AROUND. ops.f01_corporate_artifact
 * carries content_digest NOT NULL and CHECK-bound to the hashed record, so a row
 * that reaches here without one is a row the readback should never have produced.
 * Treating that as "the comparison could not be made, carry on" would make the
 * one guard that stops a self-registration optional exactly when the database is
 * already saying something is wrong, so it fails closed like every other corrupt
 * stored record in this module.
 */
function loadedSourceArtifact(stored) {
  if (stored === null || stored === undefined) return null;
  const content_digest = isPlainObject(stored.artifact) ? stored.artifact.content_digest : undefined;
  if (typeof content_digest !== "string" || !/^sha256:[0-9a-f]{64}$/.test(content_digest)) {
    fail("corrupt_stored_artifact",
      "the stored source artifact carries no readable content digest; it is refused, not repaired",
      { artifact_digest: typeof stored.artifact_digest === "string" ? stored.artifact_digest : null });
  }
  return {
    artifact_digest: stored.artifact_digest,
    created_at: stored.created_at,
    content_digest,
  };
}

/**
 * One reason per stored provenance state, and no default.
 *
 * A MAP RATHER THAN AN OBJECT LITERAL, because the lookup key arrives from a
 * database readback: `{}["constructor"]` answers with something truthy, and a
 * reason derived from an inherited property would be a reason nobody wrote. A Map
 * has no prototype chain to fall through.
 *
 * THE THREE STATES ARE CHECKED AGAINST THE VOCABULARY, INSIDE THIS FUNCTION. The
 * document module and this one import each other, so reading its exported
 * vocabulary at module scope would be a temporal-dead-zone ReferenceError
 * whenever that module is the entry point. Reading it here — when a document
 * outcome is actually being projected — keeps the two lists from drifting without
 * reintroducing the cycle hazard the seam is built to avoid.
 */
const DOCUMENT_PROVENANCE_REASONS = new Map([
  ["derived_from_stored_artifact", "document_source_binding_registered"],
  ["original_first_party", "document_declared_original_no_source_artifact"],
  ["legacy_provenance_unknown", "document_declared_legacy_provenance_unknown"],
]);

function documentProvenanceReason(provenance_state, operation) {
  for (const state of V5_F01_DOCUMENT_PROVENANCE_STATES) {
    if (!DOCUMENT_PROVENANCE_REASONS.has(state)) {
      fail("invalid_stored_outcome",
        `the stored provenance state "${state}" has no reason on this surface; the vocabulary and the reasons have drifted`,
        { operation, provenance_state: state });
    }
  }
  const reason = typeof provenance_state === "string"
    ? DOCUMENT_PROVENANCE_REASONS.get(provenance_state)
    : undefined;
  if (reason === undefined) {
    // FAIL CLOSED. The six-argument writer returns the state it committed on
    // every document write, so an absent or unregistered one is a database that
    // did not write what this module asked it to — not a document to report as
    // recorded under a reason invented here.
    fail("invalid_stored_outcome",
      "the committed document write named no registered provenance state; a version's origin is never inferred",
      { operation, provenance_state: typeof provenance_state === "string" ? provenance_state : null,
        registered: [...V5_F01_DOCUMENT_PROVENANCE_STATES] });
  }
  return reason;
}

function derivativeRegistrationRequest({ registration, storedArtifact, now }) {
  return {
    tenant: ORGANIZATION_TENANT_ID,
    registration: {
      source_artifact_digest: registration.source_artifact_digest,
      derivative: {
        derivative_kind: registration.derivative_kind,
        derivative_id: registration.derivative_id,
        content_digest: registration.derivative_content_digest,
      },
      producer: {
        producer_workflow: registration.producer_workflow,
        producer_run_ref: registration.producer_run_ref,
      },
      produced_at: now,
      evidence: {
        evidence_ref: registration.evidence_ref,
        evidence_digest: registration.evidence_digest,
      },
    },
    // LOADED, and now carrying the artifact's own CONTENT digest as well as its
    // record digest, so the kernel compares the derivative's bytes with the
    // source's bytes rather than with the identity of the row that describes them.
    source_artifact: loadedSourceArtifact(storedArtifact),
    now,
  };
}

/** The closed shape the kernel accepts for one loaded coverage answer. */
const KERNEL_COVERAGE_KEYS = Object.freeze([
  "state", "reason_id", "registered_derivative_kinds",
]);

/**
 * The closed shape the kernel accepts for one LOADED retention clock.
 *
 * ops.f01_retention_clock returns more than this — the artifact digest, the
 * source's own observed instant, and the integrity note — so the answer is
 * PROJECTED rather than forwarded, exactly as the coverage answer is. The digest
 * that travels beside it in the stored record is taken over the DATABASE's full
 * answer, which is what the writer re-derives; the projection is only what the
 * decision is allowed to read.
 */
const KERNEL_RETENTION_CLOCK_KEYS = Object.freeze([
  "kind", "event_kind", "started_at", "reference", "provenance", "event_digest",
  "verified", "source_observed_at_used",
]);

/**
 * The closed shape the document-source binding accepts for one LOADED prior
 * provenance statement.
 *
 * ops.f01_document_version_source returns more than this — the envelope, its
 * digest and the self-limiting claims — so the row is PROJECTED rather than
 * forwarded, exactly as the coverage answer is. A richer readback must not be
 * able to break the decision, and a decision must not be able to read a field
 * nobody meant it to.
 */
const DOCUMENT_PRIOR_PROVENANCE_KEYS = Object.freeze([
  "document_id", "version_no", "document_digest", "provenance_state",
  "source_artifact_digest", "derivative_link_digest",
]);

// ---------------------------------------------------------------------------
// The store.
// ---------------------------------------------------------------------------

function requireDb(db) {
  if (!db || typeof db.query !== "function") {
    fail("database_handle_required",
      "createRecordSourceAuthorityStore requires an injected database handle with query(text, params)");
  }
  return db;
}

async function one(client, text, params = []) {
  const result = await client.query(text, params);
  const rows = result?.rows ?? [];
  return rows.length > 0 ? rows[0] : null;
}

const J = value => JSON.stringify(value ?? null);

/**
 * Build the store. Everything it touches is injected: the database handle, and
 * the authenticated context supplied per call. It opens no connection, reads no
 * environment, discovers no credential and holds no clock.
 */
export function createRecordSourceAuthorityStore({ db } = {}) {
  const handle = requireDb(db);

  async function withTransaction(fn) {
    if (typeof handle.transaction === "function") return handle.transaction(fn);
    await handle.query("BEGIN");
    try {
      const result = await fn(handle);
      await handle.query("COMMIT");
      return result;
    } catch (error) {
      try { await handle.query("ROLLBACK"); } catch { /* the original error is the answer */ }
      throw error;
    }
  }

  /**
   * Open one operation: derive the actor and the instant FROM THE SERVER, and
   * refuse when the database's independently derived actor is not the one the
   * handler authenticated.
   */
  async function openOperation(client, operation, principal) {
    const row = await one(client,
      "SELECT ops.f01_principal() AS principal, ops.f01_now_text() AS server_now");
    if (!row) {
      fail("transaction_context_unavailable",
        "the database did not return a principal; the transaction context was never established",
        { operation });
    }
    const dbPrincipal = typeof row.principal === "string" ? JSON.parse(row.principal) : row.principal;
    if (dbPrincipal?.actor_slug !== principal.slug) {
      fail("actor_context_mismatch",
        "the database-derived actor is not the handler's authenticated actor; the write cannot be attributed",
        { operation, handler_actor: principal.slug, database_actor: dbPrincipal?.actor_slug ?? null });
    }
    if (OPERATION_SCHEMAS[operation].authorityOnly &&
        (dbPrincipal.human !== principal.human ||
         dbPrincipal.authorization_class !== principal.authorization_class)) {
      fail("actor_context_mismatch", "the database principal does not attest the required human authority",
        { operation, actor_slug: principal.slug });
    }
    return { now: row.server_now, database_principal: dbPrincipal };
  }

  async function loadCurrentPolicy(client, { required = true, operation } = {}) {
    const row = await one(client, "SELECT ops.f01_current_policy() AS policy");
    const policy = row && row.policy
      ? (typeof row.policy === "string" ? JSON.parse(row.policy) : row.policy)
      : null;
    if (policy === null) {
      if (!required) return null;
      fail("no_installed_policy",
        "no field-authority and retention registry version is installed; this module invents none",
        { operation });
    }
    return {
      ...policy,
      compiled_field_registry: rehydrateRegistry(
        policy.field_registry, policy.field_registry_digest, "field-authority registry"),
      compiled_retention_registry: rehydrateRegistry(
        policy.retention_registry, policy.retention_registry_digest, "retention registry"),
    };
  }

  function requestDigest(operation, payload, principal) {
    // The idempotency key is bound to the OPERATION, the exact payload and the
    // actor. A replay of the same bytes returns the same record; the same key
    // over different bytes refuses rather than substituting one write for
    // another.
    return digest({
      schema_version: V5_F01_STORE_SCHEMA_VERSION,
      operation,
      actor_slug: principal.slug,
      payload,
    });
  }

  function begin(operation, payload, context) {
    const schema = OPERATION_SCHEMAS[operation];
    const principal = assertAuthenticatedContext(context);
    assertOperationAuthority(operation, principal);
    const validated = assertClosed(payload ?? {}, schema.keys, schema.required, "payload");
    if (validated.schema_version !== undefined &&
        validated.schema_version !== V5_F01_STORE_SCHEMA_VERSION) {
      fail("unknown_schema_version",
        `payload.schema_version must be "${V5_F01_STORE_SCHEMA_VERSION}"`,
        { operation, expected: V5_F01_STORE_SCHEMA_VERSION });
    }
    if (schema.write) assertIdempotencyKey(validated.idempotency_key, "payload.idempotency_key");
    return { principal, payload: validated };
  }

  function result(operation, decision, reason_id, extra = {}) {
    return deepFreeze({
      schema_version: V5_F01_STORE_SCHEMA_VERSION,
      operation,
      tenant: ORGANIZATION_TENANT_ID,
      decision,
      reason_id,
      ...extra,
      // Stated on every result: persistence is a record, never an act in the
      // world. Nothing here sends, files, purges or activates anything.
      provider_calls: 0,
      bytes_deleted: false,
      external_purge_performed: false,
      effects: V5_NO_EFFECTS,
    });
  }

  // SQL constructs and integrity-checks the saved outcome. Initial application
  // and replay use this same projection; no freshly evaluated decision can
  // overwrite the meaning of a previously committed mutation.
  function resultFromOutcome(operation, outcome, principal) {
    if (!isPlainObject(outcome) || outcome.operation !== operation ||
        outcome.actor_slug !== principal.slug) {
      fail("invalid_stored_outcome", "stored outcome operation or actor does not match", { operation });
    }
    const record = outcome.readback?.record ?? {};
    const extra = { actor_slug: outcome.actor_slug, readback: outcome };
    let decision = "allow";
    let reason;
    switch (operation) {
      case "register-record-source-authority-policy":
        reason = "policy_version_installed";
        Object.assign(extra, {
          policy_digest: outcome.readback.policy_digest,
          prior_policy_digest: outcome.readback.prior_policy_digest,
          field_registry_digest: outcome.readback.field_registry_digest,
          retention_registry_digest: outcome.readback.retention_registry_digest,
          entries_invented: 0,
        });
        break;
      case "record-source-observation":
        decision = outcome.outcome === "accepted" ? "accept" : outcome.outcome;
        reason = outcome.reason_id;
        Object.assign(extra, {
          entity: outcome.entity ?? outcome.readback?.current_state?.entity,
          field: outcome.field ?? outcome.readback?.current_state?.field,
          policy_digest: outcome.policy_digest,
          version_ordering: outcome.version_ordering ?? null,
          conflict_kind: outcome.conflict_kind ?? null,
          silent_last_write_wins: false,
          current_state_transition_digest: outcome.current_state_transition_digest ?? null,
          event_digest: outcome.event_digest ?? null,
          mutation_receipt_digest: outcome.mutation_receipt_digest ?? null,
          reconciliation_item_digest: outcome.reconciliation_item_digest ?? null,
          any_one_substitutes_for_another: false,
        });
        break;
      case "record-corporate-artifact":
        reason = "artifact_admitted_as_evidence";
        Object.assign(extra, { artifact_digest: outcome.artifact_digest,
          is_fact: false, makes_field_authoritative: false, immutable: true });
        break;
      case "record-parsed-proposal":
        reason = "proposal_reviewable_only";
        Object.assign(extra, { artifact_digest: record.artifact_digest,
          proposal_digest: outcome.proposal_digest, link_digest: outcome.link_digest,
          supersedes_link_digest: record.supersedes_link_digest ?? null,
          // The producer half of the approved registration rule: this derived
          // record did not complete without its source registration, and the
          // digest of that registration is reported rather than assumed.
          derivative_link_digest: outcome.derivative_link_digest ?? null,
          derivative_registration_bound: true,
          becomes_fact: false, advances_state: false, carries_effect_authority: false,
          requires_human_review: true });
        break;
      case "register-derivative-source-link":
        reason = outcome.outcome === "already_registered"
          ? "derivative_source_link_already_registered"
          : "derivative_source_link_registered";
        Object.assign(extra, {
          link_digest: outcome.link_digest,
          source_artifact_digest: record.source_artifact_digest,
          derivative_kind: record.derivative_kind,
          derivative_id: record.derivative_id,
          producer_workflow: record.producer_workflow,
          // Said on every registration answer, because the whole risk here is
          // that somebody reads a growing link table as a complete one.
          establishes_coverage: false,
          is_exhaustive_inventory: false,
          permits_deletion: false,
        });
        break;
      case "record-document-identity": {
        const incomplete = record.official_filing_state === "incomplete_official_filing";
        decision = incomplete ? "refuse" : "allow";
        // THE ANSWER NAMES WHICH OF THE THREE ORIGINS WAS RECORDED, and it takes
        // that from the COMMITTED write rather than from what this module decided
        // a moment earlier — the same rule every other field on this answer
        // follows, and the reason a replay reports what landed instead of what a
        // fresh evaluation would say now.
        //
        // WHY NOT ONE REASON FOR EVERY ALLOWED DOCUMENT. Because "this version was
        // derived from that artifact", "this version was authored here" and
        // "nobody knows where this version came from" are three different facts,
        // and the whole point of making the statement mandatory was that an
        // absent link stops standing in for all three. A single
        // document_identity_and_states_coherent collapsed them again at the last
        // step: the record layer stored the distinction and the answer threw it
        // away, so a caller recording a truthful legacy import read back the same
        // reason as one claiming first-party authorship. The document's own
        // coherence is still reported — it is what `decision: "allow"` and
        // `official_filing_state` say — and it is no longer the only thing said.
        reason = incomplete
          ? "incomplete_official_filing"
          : documentProvenanceReason(outcome.provenance_state, operation);
        Object.assign(extra, { document_id: record.neon_identity.document_id,
          document_digest: outcome.document_digest,
          prior_document_digest: record.prior_document_digest ?? null,
          official_filing_state: record.official_filing_state,
          official_copy_required: record.signature_state === "fully_executed",
          official_copy_filing_state: record.onedrive_identity?.filing_state ?? "absent",
          object_storage_success_implies_official_filing: false,
          neon_success_implies_official_filing: false,
          // THE PROVENANCE HALF, REPORTED FROM THE COMMITTED WRITE rather than
          // from what this module decided a moment earlier. The database wrote
          // the statement, and — for a derived version — the link, in the same
          // transaction as the version; these are its answers about what landed.
          provenance_state: outcome.provenance_state ?? null,
          provenance_digest: outcome.provenance_digest ?? null,
          derivative_link_digest: outcome.derivative_link_digest ?? null,
          derivative_registration_bound: outcome.derivative_registration_bound === true,
          provenance_readback: outcome.provenance_readback ?? null,
          // Said on every document answer for the same reason it is said on every
          // registration answer: a growing set of registered documents is not a
          // complete picture of what was derived from an artifact, and an absent
          // statement is not an absent source.
          establishes_coverage: false,
          is_exhaustive_inventory: false,
          permits_deletion: false,
          absent_statement_means_no_source: false });
        break;
      }
      case "record-artifact-preservation-hold":
        reason = `hold_state_${record.state}_appended`;
        Object.assign(extra, { hold_id: record.hold_id, artifact_digest: record.artifact_digest,
          hold_digest: outcome.hold_digest, prior_hold_digest: record.prior_hold_digest ?? null,
          deletes_artifact: false, append_only: true });
        break;
      case "evaluate-artifact-deletion": {
        decision = outcome.outcome;
        reason = outcome.reason_id;
        const inventory = outcome.hold_inventory ?? [];
        let blocking = [];
        if (reason === "active_hold_blocks_deletion") blocking = inventory.filter(h => h.state === "active");
        else if (reason === "unknown_hold_state_blocks_deletion") blocking = inventory.filter(h => h.state === "unknown");
        else if (["hold_placed_after_now", "released_hold_missing_release_time", "hold_release_time_incoherent"].includes(reason)) {
          const now = Date.parse(record.evaluated_at);
          blocking = inventory.filter(h => {
            const placed = Date.parse(h.placed_at);
            const released = h.released_at == null ? null : Date.parse(h.released_at);
            if (reason === "hold_placed_after_now") return placed > now;
            if (reason === "released_hold_missing_release_time") return h.state === "released" && released === null;
            return ["released", "expired"].includes(h.state) && released !== null &&
              (released < placed || released > now);
          }).slice(0, 1);
        }
        Object.assign(extra, { artifact_class: record.artifact_class,
          artifact_digest: record.artifact_digest, evaluation_digest: outcome.evaluation_digest,
          blocking_holds: blocking.map(h => h.hold_id),
          // CLASS POLICY, from the installed retention registry. Kept under the
          // same name it has always had, and kept apart from the instance
          // observation beside it, because they answer different questions.
          surviving_derivatives: outcome.surviving_derivatives ?? record.deletion_receipt?.surviving_derivatives ?? [],
          observed_surviving_derivatives:
            record.deletion_receipt?.observed_surviving_derivatives ?? null,
          derivative_coverage_state: record.derivative_coverage_state,
          derivative_coverage_digest: record.derivative_coverage_digest,
          // Reported from the COMMITTED evaluation, so a replay says which
          // trigger the stored decision was measured from rather than which one
          // the database would derive now.
          retention_clock: record.retention_clock ?? null,
          retention_clock_digest: record.retention_clock_digest ?? null,
          hold_inventory_digest: record.hold_inventory_digest,
          silent_purge: false, purge_without_proof: false, deletion_performed: false });
        break;
      }
      default: fail("invalid_stored_outcome", "not a replayable write operation", { operation });
    }
    if (typeof reason !== "string" || !reason) {
      fail("invalid_stored_outcome", "stored outcome is missing its original reason", { operation });
    }
    return result(operation, decision, reason, extra);
  }

  async function replayOutcome(client, operation, request, principal) {
    const row = await one(client,
      "SELECT ops.f01_replay_outcome($1::text, $2::text, $3::text) AS outcome",
      [operation, request.idempotency_key, requestDigest(operation, request, principal)]);
    const outcome = typeof row?.outcome === "string" ? JSON.parse(row.outcome) : row?.outcome;
    return outcome == null ? null : resultFromOutcome(operation, outcome, principal);
  }

  // -- 1. read-record-source-authority --------------------------------------

  async function readRecordSourceAuthority(payload, context) {
    const operation = "read-record-source-authority";
    const { principal, payload: request } = begin(operation, payload, context);
    const selector = assertClosed(request.selector, READ_SELECTOR_KEYS, ["kind"], "payload.selector");
    if (!V5_F01_READ_KINDS.includes(selector.kind)) {
      fail("unknown_read_kind", `"${selector.kind}" is not a registered read kind`,
        { kind: selector.kind, registered: [...V5_F01_READ_KINDS] });
    }
    return withTransaction(async client => {
      await openOperation(client, operation, principal);
      const row = await one(client, "SELECT ops.f01_read($1::text, $2::jsonb) AS body",
        [selector.kind, J(projectKeys(selector, READ_SELECTOR_KEYS.filter(k => k !== "kind")))]);
      const body = typeof row.body === "string" ? JSON.parse(row.body) : row.body;
      return result(operation, "allow", "read_recomputed_from_committed_rows", {
        actor_slug: principal.slug,
        kind: selector.kind,
        readback: body,
        integrity: "recomputed_not_trusted",
        stale_fallback_permitted: false,
      });
    });
  }

  // -- 2. register-record-source-authority-policy ---------------------------

  async function registerRecordSourceAuthorityPolicy(payload, context) {
    const operation = "register-record-source-authority-policy";
    const { principal, payload: request } = begin(operation, payload, context);

    // The candidate registries are COMPILED by the kernel, which is where every
    // missing, ambiguous, duplicate, contradictory or privacy-prohibited entry
    // refuses. This module supplies no entry and no default of its own.
    const compiledField = compileFieldAuthorityRegistry(request.field_registry);
    const compiledRetention = compileRetentionRegistry(request.retention_registry);

    // The caller STATES the digests, exactly as the contract requires, and they
    // are CHECKED against the compilation rather than believed.
    if (request.field_registry_digest !== compiledField.registry_digest) {
      fail("field_registry_digest_mismatch",
        "the stated field-authority registry digest is not the digest of the supplied registry",
        { expected: compiledField.registry_digest, stated: request.field_registry_digest });
    }
    if (request.retention_registry_digest !== compiledRetention.registry_digest) {
      fail("retention_registry_digest_mismatch",
        "the stated retention registry digest is not the digest of the supplied registry",
        { expected: compiledRetention.registry_digest, stated: request.retention_registry_digest });
    }

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const current = await loadCurrentPolicy(client, { required: false, operation });
      const expectedPrior = request.expected_prior_policy_digest ?? null;
      // The CAS is decided against the STORED current, not the caller's belief
      // about it. The unique index in ops makes the same check structural under
      // a concurrent install.
      if ((current?.policy_digest ?? null) !== expectedPrior) {
        fail("stale_policy_digest",
          "the stored current policy is not the one this installation was decided against",
          { stored: current?.policy_digest ?? null, expected: expectedPrior });
      }
      const record = storedPolicyRecord({
        registry_version: compiledField.registry_version,
        field_registry: fieldAuthorityRegistryPreimage(compiledField),
        field_registry_digest: compiledField.registry_digest,
        retention_registry: retentionRegistryPreimage(compiledRetention),
        retention_registry_digest: compiledRetention.registry_digest,
        prior_policy_digest: expectedPrior,
        installed_by: principal.slug,
        installed_at: now,
      });
      const envelope = storeEnvelope("stored_policy_version", record, {
        humanOnly: true, authorityOnly: true, installs_defaults: false,
      });
      const row = await one(client,
        "SELECT ops.f01_install_policy($1::jsonb, $2::text, $3::text, $4::text) AS outcome",
        [J(envelope), expectedPrior, request.idempotency_key,
         requestDigest(operation, request, principal)]);
      const outcome = typeof row.outcome === "string" ? JSON.parse(row.outcome) : row.outcome;
      return resultFromOutcome(operation, outcome, principal);
    });
  }

  // -- 3. record-source-observation -----------------------------------------

  async function recordSourceObservation(payload, context) {
    const operation = "record-source-observation";
    const { principal, payload: request } = begin(operation, payload, context);
    const observation = assertClosed(request.observation, OBSERVATION_KEYS,
      ["entity", "field", "source_system"], "payload.observation");

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const policy = await loadCurrentPolicy(client, { operation });

      // PRIOR STATE IS LOADED, never accepted. The kernel is handed the stored
      // record projected down to the keys it accepts, so nothing the caller
      // wrote can reach the comparison it is judged against.
      const stateRow = await one(client,
        "SELECT ops.f01_current_field_state($1::text, $2::text) AS state",
        [observation.entity, observation.field]);
      const storedState = stateRow && stateRow.state
        ? (typeof stateRow.state === "string" ? JSON.parse(stateRow.state) : stateRow.state)
        : null;
      const currentStateDigest = storedState?.state_digest ?? null;
      const currentState = storedState
        ? projectKeys(storedState.current_state, KERNEL_CURRENT_STATE_KEYS)
        : null;

      const resolution = resolveObservation({
        tenant: ORGANIZATION_TENANT_ID,
        registry: policy.compiled_field_registry,
        observation: { ...observation, tenant: ORGANIZATION_TENANT_ID },
        current_state: currentState,
        now,
      });

      let stateEnvelope = null;
      let transitionEnvelope = null;
      let eventEnvelope = null;
      let receiptEnvelope = null;
      let reconciliationEnvelope = null;

      if (resolution.decision === "accept") {
        transitionEnvelope = storeEnvelope("stored_state_transition",
          resolution.current_state_transition, { alone_sufficient: false });
        eventEnvelope = storeEnvelope("stored_source_event", resolution.event,
          { alone_sufficient: false });
        receiptEnvelope = storeEnvelope("stored_mutation_receipt", resolution.mutation_receipt,
          { alone_sufficient: false, binds_transition_and_event: true });
        stateEnvelope = storeEnvelope("stored_field_state", storedFieldStateRecord({
          entity: resolution.entity,
          field: resolution.field,
          account: resolution.event.account,
          native_identity: resolution.event.native_identity,
          value_digest: resolution.current_state_transition.to_value_digest,
          version: resolution.current_state_transition.to_version,
          owner_source: resolution.owner_source,
          authoritative_home: resolution.authoritative_home,
          observed_at: resolution.event.observed_at,
          event_seq: resolution.event.event_seq,
          last_event_digest: digest(resolution.event),
          policy_digest: policy.policy_digest,
          updated_by: principal.slug,
          updated_at: now,
        }));
      }
      if (resolution.reconciliation_item !== null && resolution.reconciliation_item !== undefined) {
        reconciliationEnvelope = storeEnvelope("stored_reconciliation_item",
          resolution.reconciliation_item, { visible: true, resolved_by_machine: false });
      }

      const row = await one(client,
        `SELECT ops.f01_apply_observation($1::text, $2::text, $3::text, $4::text, $5::text,
                                          $6::jsonb, $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb,
                                          $11::text, $12::text, $13::jsonb) AS outcome`,
        [resolution.decision, resolution.entity, resolution.field,
         policy.policy_digest, currentStateDigest,
         stateEnvelope ? J(stateEnvelope) : null,
         transitionEnvelope ? J(transitionEnvelope) : null,
         eventEnvelope ? J(eventEnvelope) : null,
         receiptEnvelope ? J(receiptEnvelope) : null,
         reconciliationEnvelope ? J(reconciliationEnvelope) : null,
         request.idempotency_key, requestDigest(operation, request, principal),
         J({ reason_id: resolution.reason_id, version_ordering: resolution.version_ordering ?? null,
             conflict_kind: resolution.conflict_kind ?? null })]);
      const outcome = typeof row.outcome === "string" ? JSON.parse(row.outcome) : row.outcome;

      return resultFromOutcome(operation, outcome, principal);
    });
  }

  // -- 4. record-corporate-artifact -----------------------------------------

  async function recordCorporateArtifact(payload, context) {
    const operation = "record-corporate-artifact";
    const { principal, payload: request } = begin(operation, payload, context);
    const artifact = assertClosed(request.artifact, ARTIFACT_KEYS, ["source_system"],
      "payload.artifact");

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;

      // PRIOR IDENTITY IS LOADED. A caller's copy of "the artifact we already
      // have" is not evidence about what is stored, so it is never accepted.
      let priorArtifact = null;
      const identity = artifact.native_identity;
      if (isPlainObject(identity) && typeof artifact.source_account === "string" &&
          typeof artifact.native_version === "string") {
        const row = await one(client,
          `SELECT ops.f01_stored_artifact_by_identity($1::text, $2::text, $3::text,
                                                      $4::text, $5::text) AS prior`,
          [artifact.source_system, artifact.source_account, identity.native_id ?? null,
           identity.native_id_epoch ?? null, artifact.native_version]);
        const stored = row && row.prior
          ? (typeof row.prior === "string" ? JSON.parse(row.prior) : row.prior)
          : null;
        if (stored) priorArtifact = projectKeys(stored.artifact, ARTIFACT_KEYS);
      }

      const admitted = admitCorporateArtifact({
        tenant: ORGANIZATION_TENANT_ID,
        artifact,
        prior_artifact: priorArtifact,
        now,
      });
      if (admitted.decision !== "allow") {
        return result(operation, admitted.decision, admitted.reason_id, {
          actor_slug: principal.slug,
          artifact_digest: admitted.artifact_digest ?? null,
          records_written: 0,
          is_fact: false,
          readback: null,
        });
      }

      const envelope = storeEnvelope("stored_corporate_artifact", admitted.artifact, {
        is_fact: false, makes_field_authoritative: false, immutable: true,
      });
      const row = await one(client,
        "SELECT ops.f01_record_artifact($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      const outcome = typeof row.outcome === "string" ? JSON.parse(row.outcome) : row.outcome;
      return resultFromOutcome(operation, outcome, principal);
    });
  }

  // -- 5. record-parsed-proposal --------------------------------------------

  async function recordParsedProposal(payload, context) {
    const operation = "record-parsed-proposal";
    const { principal, payload: request } = begin(operation, payload, context);
    const proposal = assertClosed(request.proposal, PROPOSAL_KEYS, ["artifact_digest"],
      "payload.proposal");

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const policy = await loadCurrentPolicy(client, { operation });

      // The referenced artifact is RESOLVED, not asserted. Naming a digest is a
      // lookup; it can never bring an artifact into existence.
      const artifactRow = await one(client,
        "SELECT ops.f01_stored_artifact($1::text) AS artifact", [proposal.artifact_digest]);
      const storedArtifact = artifactRow && artifactRow.artifact
        ? (typeof artifactRow.artifact === "string"
            ? JSON.parse(artifactRow.artifact) : artifactRow.artifact)
        : null;
      if (storedArtifact === null) {
        return result(operation, "refuse", "unknown_artifact_reference", {
          actor_slug: principal.slug,
          artifact_digest: proposal.artifact_digest,
          becomes_fact: false, advances_state: false, carries_effect_authority: false,
          requires_human_review: true,
          readback: null,
        });
      }

      const evaluated = evaluateParsedProposal({
        tenant: ORGANIZATION_TENANT_ID,
        registry: policy.compiled_field_registry,
        proposal,
        now,
      });
      if (evaluated.decision !== "allow") {
        return result(operation, evaluated.decision, evaluated.reason_id, {
          actor_slug: principal.slug,
          artifact_digest: proposal.artifact_digest,
          becomes_fact: false, advances_state: false, carries_effect_authority: false,
          requires_human_review: true,
          readback: null,
        });
      }

      const notFact = {
        becomes_fact: false, advances_state: false,
        carries_effect_authority: false, requires_human_review: true,
      };
      const proposalRecord = {
        ...evaluated.link,
        schema_version: V5_F01_PROPOSAL_SCHEMA_VERSION,
      };
      const proposalEnvelope = storeEnvelope("stored_parsed_proposal", proposalRecord, notFact);
      const linkEnvelope = storeEnvelope("stored_proposal_link", evaluated.link, {
        ...notFact, reversible: true, history_preserved: true,
      });

      // THE PRODUCER HALF OF THE APPROVED REGISTRATION RULE, and it is bound HERE
      // rather than left to whoever calls this tool. (That rule is the session
      // approval the kernel's registration header names; Q129.D1 settles the
      // retention registry and not this.)
      //
      // A parsed proposal is a record DERIVED from a stored corporate artifact,
      // so this workflow is a producer under that rule: it registers which
      // original produced the derivative before the derivative is complete,
      // automatically, with nobody having to remember.
      // The registration is built from values this module already holds — the
      // resolved artifact, the proposal's own digest, the server instant and the
      // authenticated principal — so the caller adds nothing and can withhold
      // nothing. ops.f01_record_proposal REQUIRES the envelope and writes both
      // records in one transaction or neither, which is what makes "before the
      // derivative is considered complete" a property rather than a convention.
      const proposalDigest = proposalEnvelope.record_digest;
      const registration = evaluateDerivativeRegistration(derivativeRegistrationRequest({
        registration: {
          source_artifact_digest: proposal.artifact_digest,
          derivative_kind: V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
          derivative_id: proposalDigest,
          derivative_content_digest: proposalDigest,
          producer_workflow: "f01_record_parsed_proposal",
          producer_run_ref: request.idempotency_key,
          evidence_ref: "stored_parsed_proposal",
          evidence_digest: proposalDigest,
        },
        storedArtifact,
        now,
      }));
      if (registration.decision !== "allow") {
        // The derivative could not be bound to its source, so the derivative
        // does not complete. Nothing is written: a proposal recorded without its
        // provenance edge is exactly the untracked derivative this rule exists
        // to stop being created.
        return result(operation, "refuse", registration.reason_id, {
          actor_slug: principal.slug,
          artifact_digest: proposal.artifact_digest,
          derivative_registration_bound: false,
          becomes_fact: false, advances_state: false, carries_effect_authority: false,
          requires_human_review: true,
          records_written: 0,
          readback: null,
        });
      }
      const derivativeEnvelope = storeEnvelope("stored_derivative_link",
        storedDerivativeLinkRecord({
          link: registration.derivative_link,
          registered_by: principal.slug,
          registered_at: now,
        }), {
          establishes_coverage: false, is_exhaustive_inventory: false,
          permits_deletion: false, deletes_nothing: true,
        });

      const row = await one(client,
        `SELECT ops.f01_record_proposal($1::jsonb, $2::jsonb, $3::jsonb, $4::text, $5::text)
                AS outcome`,
        [J(proposalEnvelope), J(linkEnvelope), J(derivativeEnvelope), request.idempotency_key,
         requestDigest(operation, request, principal)]);
      const outcome = typeof row.outcome === "string" ? JSON.parse(row.outcome) : row.outcome;
      return resultFromOutcome(operation, outcome, principal);
    });
  }

  // -- 6. register-derivative-source-link -----------------------------------

  /**
   * Bind ONE derived record to the exact stored artifact it came from.
   *
   * WHO MAY WRITE ONE. The authenticated producer principal, derived from the
   * transaction context exactly like every other write here and checked against
   * the handler's own. There is no `trusted: true`, no producer allow-list a
   * caller can name itself into, and no path by which a reader becomes a
   * producer: ops.f01_register_derivative_link refuses a read-only principal in
   * its own body, and the grant loop never gives one EXECUTE on it.
   *
   * WHAT ONE MEANS, and it is deliberately narrow. "This derivative came from
   * that artifact." It does not mean the artifact's derivatives are now known,
   * it does not make an absent link an absent derivative, and it permits no
   * deletion of anything. Those claims are refused in the record itself, in the
   * result, and in the CHECK constraints on the stored row.
   *
   * NOR DOES IT MEAN THE DERIVATIVE EXISTS. Nothing here resolves the named
   * derivative or checks its bytes: the source artifact is LOADED, the derivative
   * is taken on the producer's word. That residual is safe only while coverage is
   * unknown, and it is written down at the two other places that would have to
   * change — the kernel's registration header and ops.f01_derivative_coverage —
   * as a precondition on ever establishing coverage, not a note about it.
   *
   * KINDS THIS CONTRACT PRODUCES ITSELF ARE REFUSED HERE, before any statement
   * runs. A caller cannot register an `f01_parsed_proposal` link: that kind is
   * written only by recordParsedProposal, whose derivative identity is a digest a
   * caller can predict, over an append-only unique identity with no release path.
   * ops.f01_register_derivative_link refuses the same kind in its own body — the
   * two are independent, as with every other guard in this seam — but the refusal
   * a caller should meet is this one, which names the reason instead of raising.
   */
  async function registerDerivativeSourceLink(payload, context) {
    const operation = "register-derivative-source-link";
    const { principal, payload: request } = begin(operation, payload, context);
    const registration = assertClosed(request.registration, DERIVATIVE_REGISTRATION_KEYS,
      DERIVATIVE_REGISTRATION_KEYS, "payload.registration");
    if (V5_F01_RESERVED_DERIVATIVE_KINDS.includes(registration.derivative_kind)) {
      return result(operation, "refuse", "reserved_derivative_kind", {
        actor_slug: principal.slug,
        source_artifact_digest: registration.source_artifact_digest,
        derivative_kind: registration.derivative_kind,
        derivative_id: registration.derivative_id,
        reserved_derivative_kinds: [...V5_F01_RESERVED_DERIVATIVE_KINDS],
        establishes_coverage: false, is_exhaustive_inventory: false, permits_deletion: false,
        records_written: 0,
        readback: null,
      });
    }

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;

      // The source artifact is RESOLVED, not asserted, exactly as it is for a
      // proposal and a hold. Naming a digest is a lookup; it can never bring an
      // artifact into existence, and a link to an artifact nobody stored would
      // be provenance pointing at nothing.
      const artifactRow = await one(client,
        "SELECT ops.f01_stored_artifact($1::text) AS artifact",
        [registration.source_artifact_digest]);
      const storedArtifact = artifactRow && artifactRow.artifact
        ? (typeof artifactRow.artifact === "string"
            ? JSON.parse(artifactRow.artifact) : artifactRow.artifact)
        : null;

      const evaluated = evaluateDerivativeRegistration(derivativeRegistrationRequest({
        registration, storedArtifact, now,
      }));
      if (evaluated.decision !== "allow") {
        return result(operation, "refuse", evaluated.reason_id, {
          actor_slug: principal.slug,
          source_artifact_digest: registration.source_artifact_digest,
          derivative_kind: registration.derivative_kind,
          derivative_id: registration.derivative_id,
          establishes_coverage: false, is_exhaustive_inventory: false, permits_deletion: false,
          records_written: 0,
          readback: null,
        });
      }

      const envelope = storeEnvelope("stored_derivative_link", storedDerivativeLinkRecord({
        link: evaluated.derivative_link,
        registered_by: principal.slug,
        registered_at: now,
      }), {
        establishes_coverage: false, is_exhaustive_inventory: false,
        permits_deletion: false, deletes_nothing: true,
      });
      const row = await one(client,
        "SELECT ops.f01_register_derivative_link($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      const outcome = typeof row.outcome === "string" ? JSON.parse(row.outcome) : row.outcome;
      return resultFromOutcome(operation, outcome, principal);
    });
  }

  // -- 7. record-document-identity ------------------------------------------

  /**
   * ONE REFUSAL IS PERSISTED RATHER THAN DISCARDED, and it is the point of Q125.
   *
   * A fully executed document with no filed OneDrive copy is not a request the
   * record layer should forget; it is a document whose OFFICIAL FILING IS
   * VISIBLY INCOMPLETE. So that outcome is stored with
   * official_filing_state = "incomplete_official_filing" and reported as a
   * refusal to treat the filing as complete. Every OTHER refusal — incoherent
   * state axes, a sealed-bytes digest mismatch, a filed copy whose digest
   * disagrees with Neon — writes nothing, because those describe a document
   * nobody can coherently record at all.
   *
   * AND ONE STATEMENT IS NOW MANDATORY. Every version carries a declaration of
   * where it came from, and a DERIVED one carries its ops.f01_derivative_link
   * row as well — written by ops.f01_record_document in the same transaction as
   * the version, or the version does not complete. That is the settled producer
   * rule made structural for documents, exactly as ops.f01_record_proposal makes
   * it structural for parsed proposals.
   *
   * WHAT THIS FUNCTION SUPPLIES AND WHAT IT REFUSES TO. The producing workflow
   * names WHICH of the three provenance answers is true and, in the derived case,
   * which stored artifact and which producing run. Everything else is derived or
   * LOADED here: the principal, the instant, the source artifact's own identity
   * and creation time, the prior statement for this exact version, the document's
   * own digest and the evidence reference. A caller supplies none of them and can
   * withhold none of them.
   */
  async function recordDocumentIdentity(payload, context) {
    const operation = "record-document-identity";
    const { principal, payload: request } = begin(operation, payload, context);
    const document = assertClosed(request.document, DOCUMENT_KEYS,
      ["document_class", "neon_identity"], "payload.document");
    // SHAPE ONLY, and before any statement runs: an unreadable declaration is a
    // contract violation rather than a policy question, and knowing which of the
    // three states was declared is what decides whether a source artifact has to
    // be loaded at all. Every semantic question stays with the binding below.
    const declaration = assertDocumentSourceDeclaration(request.source);

    const projected = projectDocumentIdentity({ tenant: ORGANIZATION_TENANT_ID, document });
    const filingIncomplete = projected.decision === "refuse" &&
      projected.reason_id === "incomplete_official_filing";
    if (projected.decision !== "allow" && !filingIncomplete) {
      return result(operation, "refuse", projected.reason_id, {
        actor_slug: principal.slug,
        violated_constraint: projected.violated_constraint ?? null,
        official_filing_state: projected.official_filing_state,
        object_storage_success_implies_official_filing: false,
        neon_success_implies_official_filing: false,
        records_written: 0,
        readback: null,
      });
    }

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const documentId = projected.neon_identity.document_id;
      const versionNo = projected.neon_identity.version_no;
      const priorRow = await one(client,
        "SELECT ops.f01_read('document'::text, $1::jsonb) AS body", [J({ document_id: documentId })]);
      const priorBody = typeof priorRow.body === "string" ? JSON.parse(priorRow.body) : priorRow.body;
      const priorDigest = priorBody?.body?.record_digest ?? null;
      const expectedPrior = request.expected_prior_document_digest ?? null;
      if (priorDigest !== expectedPrior) {
        fail("stale_document_digest",
          "the stored current document version is not the one this write was decided against",
          { stored: priorDigest, expected: expectedPrior });
      }

      // THE PRIOR STATEMENT FOR THIS EXACT VERSION IS LOADED, never accepted. It
      // is read for (document_id, version_no) rather than for the document,
      // because a statement is about ONE version: a second registration of the
      // same version naming a different origin is a rewrite of where that version
      // came from, and only the row for that version can show it. NULL means "no
      // statement", never "no source".
      const provenanceRow = await one(client,
        "SELECT ops.f01_document_version_source($1::text, $2::integer) AS provenance",
        [documentId, versionNo]);
      const storedProvenance = provenanceRow && provenanceRow.provenance
        ? (typeof provenanceRow.provenance === "string"
            ? JSON.parse(provenanceRow.provenance) : provenanceRow.provenance)
        : null;

      // THE SOURCE ARTIFACT IS LOADED, never asserted, and only where one is
      // claimed. Naming a digest is a lookup; it cannot bring an artifact into
      // existence, and the identity handed to the kernel is the STORED artifact's
      // own — its digest and its creation instant — rather than the caller's
      // description of it.
      let storedArtifact = null;
      if (declaration.provenance_state === "derived_from_stored_artifact") {
        const artifactRow = await one(client,
          "SELECT ops.f01_stored_artifact($1::text) AS artifact",
          [declaration.source_artifact_digest]);
        const loaded = artifactRow && artifactRow.artifact
          ? (typeof artifactRow.artifact === "string"
              ? JSON.parse(artifactRow.artifact) : artifactRow.artifact)
          : null;
        // The SAME projection every other producer path uses, so the document
        // seam's binding is judged against the artifact's own content digest
        // rather than against its record digest alone.
        storedArtifact = loadedSourceArtifact(loaded);
      }

      const binding = evaluateDocumentSourceBinding({
        tenant: ORGANIZATION_TENANT_ID,
        document,
        source: request.source,
        source_artifact: storedArtifact,
        prior_provenance: storedProvenance === null
          ? null : projectKeys(storedProvenance, DOCUMENT_PRIOR_PROVENANCE_KEYS),
        prior_document_digest: expectedPrior,
        recorded_by: principal.slug,
        now,
      });
      if (binding.decision !== "allow") {
        // The version does not complete, so nothing is written: a document
        // recorded without its provenance edge is exactly the untracked
        // derivative this rule exists to stop being created.
        return result(operation, "refuse", binding.reason_id, {
          actor_slug: principal.slug,
          document_id: binding.document_id,
          version_no: binding.version_no,
          provenance_state: binding.provenance_state,
          violated_constraint: binding.violated_constraint ?? null,
          offending_field: binding.offending_field ?? null,
          official_filing_state: binding.official_filing_state,
          derivative_registration_bound: false,
          object_storage_success_implies_official_filing: false,
          neon_success_implies_official_filing: false,
          establishes_coverage: false,
          is_exhaustive_inventory: false,
          permits_deletion: false,
          absent_statement_means_no_source: false,
          records_written: 0,
          readback: null,
        });
      }

      // TWO OR THREE ENVELOPES, NEVER A CHOICE OF ONE. The composer builds the
      // document version, the provenance statement and — only when the version is
      // derived — the link, from the same binding, so the digests they name each
      // other by are the digests the database recomputes.
      const composed = composeDocumentSourceEnvelopes(binding);
      const row = await one(client,
        `SELECT ops.f01_record_document($1::jsonb, $2::jsonb, $3::jsonb, $4::text,
                                        $5::text, $6::text) AS outcome`,
        [J(composed.document_envelope), J(composed.provenance_envelope),
         composed.derivative_envelope === null ? null : J(composed.derivative_envelope),
         expectedPrior, request.idempotency_key,
         requestDigest(operation, request, principal)]);
      const outcome = typeof row.outcome === "string" ? JSON.parse(row.outcome) : row.outcome;
      return resultFromOutcome(operation, outcome, principal);
    });
  }

  // -- 8. record-artifact-preservation-hold ---------------------------------

  async function recordArtifactPreservationHold(payload, context) {
    const operation = "record-artifact-preservation-hold";
    const { principal, payload: request } = begin(operation, payload, context);
    const hold = assertClosed(request.hold, HOLD_KEYS,
      ["hold_id", "artifact_digest", "state"], "payload.hold");
    if (!V5_F01_HOLD_STATES.includes(hold.state)) {
      fail("unknown_hold_state", `"${hold.state}" is not a registered hold state`,
        { state: hold.state, registered: [...V5_F01_HOLD_STATES] });
    }

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const artifactRow = await one(client,
        "SELECT ops.f01_stored_artifact($1::text) AS artifact", [hold.artifact_digest]);
      const storedArtifact = artifactRow && artifactRow.artifact
        ? (typeof artifactRow.artifact === "string"
            ? JSON.parse(artifactRow.artifact) : artifactRow.artifact)
        : null;
      if (storedArtifact === null) {
        return result(operation, "refuse", "unknown_artifact_reference", {
          actor_slug: principal.slug, hold_id: hold.hold_id,
          artifact_digest: hold.artifact_digest,
          deletes_artifact: false, records_written: 0, readback: null,
        });
      }

      // The prior hold state is LOADED, so a release names the state it actually
      // replaces and `placed_at` is carried forward from the row that placed it
      // rather than restated by whoever releases it.
      const historyRow = await one(client,
        "SELECT ops.f01_read('hold_history'::text, $1::jsonb) AS body",
        [J({ hold_id: hold.hold_id })]);
      const history = typeof historyRow.body === "string"
        ? JSON.parse(historyRow.body) : historyRow.body;
      const entries = Array.isArray(history?.body) ? history.body : [];
      const prior = entries.length > 0 ? entries[entries.length - 1] : null;
      const priorDigest = prior?.record_digest ?? null;
      const expectedPrior = request.expected_prior_hold_digest ?? null;
      if (priorDigest !== expectedPrior) {
        fail("stale_hold_digest",
          "the stored current hold state is not the one this append was decided against",
          { stored: priorDigest, expected: expectedPrior });
      }
      if (prior !== null && prior.record.artifact_digest !== hold.artifact_digest) {
        fail("hold_artifact_rebinding_refused",
          "a hold stays bound to the artifact it was placed on; it is never repointed",
          { hold_id: hold.hold_id, stored: prior.record.artifact_digest,
            supplied: hold.artifact_digest });
      }

      const record = storedHoldRecord({
        hold_id: hold.hold_id,
        artifact_digest: hold.artifact_digest,
        state: hold.state,
        reason: hold.reason ?? null,
        placed_at: prior?.record.placed_at ?? now,
        released_at: hold.state === "released"
          ? now
          : (hold.state === "active" ? null : (prior?.record.released_at ?? null)),
        prior_hold_digest: expectedPrior,
        recorded_by: principal.slug,
        recorded_at: now,
      });
      const envelope = storeEnvelope("stored_preservation_hold", record, {
        humanOnly: true, authorityOnly: true, append_only: true, deletes_artifact: false,
      });
      const row = await one(client,
        "SELECT ops.f01_record_hold($1::jsonb, $2::text, $3::text, $4::text) AS outcome",
        [J(envelope), expectedPrior, request.idempotency_key,
         requestDigest(operation, request, principal)]);
      const outcome = typeof row.outcome === "string" ? JSON.parse(row.outcome) : row.outcome;
      return resultFromOutcome(operation, outcome, principal);
    });
  }

  // -- 9. evaluate-artifact-deletion ----------------------------------------

  async function evaluateArtifactDeletion(payload, context) {
    const operation = "evaluate-artifact-deletion";
    const { principal, payload: request } = begin(operation, payload, context);
    const subject = assertClosed(request.subject, DELETION_SUBJECT_KEYS,
      ["artifact_class", "artifact_home", "artifact_digest"], "payload.subject");

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const policy = await loadCurrentPolicy(client, { operation });

      const artifactRow = await one(client,
        "SELECT ops.f01_stored_artifact($1::text) AS artifact", [subject.artifact_digest]);
      const storedArtifact = artifactRow && artifactRow.artifact
        ? (typeof artifactRow.artifact === "string"
            ? JSON.parse(artifactRow.artifact) : artifactRow.artifact)
        : null;
      if (storedArtifact === null) {
        return result(operation, "refuse", "unknown_artifact_reference", {
          actor_slug: principal.slug, artifact_digest: subject.artifact_digest,
          silent_purge: false, purge_without_proof: false, records_written: 0, readback: null,
        });
      }

      // THE HOLDS AND THE CREATION MOMENT ARE LOADED. An absent inventory is not
      // an empty one, and a caller that could describe its own holds could
      // describe them away. The inventory digest travels into the record and
      // the database re-derives it at apply time, so a hold placed between the
      // decision and the write refuses instead of being missed.
      const holdsRow = await one(client,
        `SELECT ops.f01_hold_inventory($1::text) AS holds,
                ops.f01_hold_inventory_digest($1::text) AS holds_digest`,
        [subject.artifact_digest]);
      const holds = typeof holdsRow.holds === "string"
        ? JSON.parse(holdsRow.holds) : holdsRow.holds;
      const holdInventoryDigest = holdsRow.holds_digest;

      // THE COVERAGE ANSWER IS LOADED, exactly like the holds, and for the same
      // reason: a caller that could describe its own coverage could describe it
      // as complete. ops.f01_derivative_coverage answers from the registered
      // links and from what is known about how they got there; in this slice it
      // answers "unknown" for every artifact, and that is the honest answer
      // rather than a placeholder. The digest travels into the record and the
      // database re-derives it, so a link registered between the decision and
      // the write refuses instead of being missed.
      const coverageRow = await one(client,
        `SELECT ops.f01_derivative_coverage($1::text) AS coverage,
                ops.f01_derivative_coverage_digest($1::text) AS coverage_digest`,
        [subject.artifact_digest]);
      const coverage = typeof coverageRow?.coverage === "string"
        ? JSON.parse(coverageRow.coverage) : coverageRow?.coverage ?? null;
      const coverageDigest = coverageRow?.coverage_digest ?? null;
      const derivativeRow = await one(client,
        "SELECT ops.f01_stored_derivatives($1::text) AS derivatives", [subject.artifact_digest]);
      const derivatives = typeof derivativeRow?.derivatives === "string"
        ? JSON.parse(derivativeRow.derivatives) : derivativeRow?.derivatives ?? null;

      // THE RETENTION CLOCK IS LOADED, on exactly the terms the holds and the
      // coverage answer are, and for the same reason: a caller that could state
      // when its artifact's retention period started could state that it started
      // long enough ago. ops.f01_retention_clock reads the SERVER-STAMPED custody
      // instant off the stored row — never the source's observed_at, which stays
      // the artifact's identity and provenance and is passed below under its own
      // name. The digest travels into the record and the writer re-derives it
      // under the retention lock, so a forged or stale trigger refuses instead of
      // being recorded.
      const clockRow = await one(client,
        `SELECT ops.f01_retention_clock($1::text) AS clock,
                ops.f01_retention_clock_digest($1::text) AS clock_digest`,
        [subject.artifact_digest]);
      const loadedClock = typeof clockRow?.clock === "string"
        ? JSON.parse(clockRow.clock) : clockRow?.clock ?? null;
      const retentionClock = loadedClock === null
        ? null : projectKeys(loadedClock, KERNEL_RETENTION_CLOCK_KEYS);
      const retentionClockDigest = clockRow?.clock_digest ?? null;

      const evaluated = evaluateDeletion({
        tenant: ORGANIZATION_TENANT_ID,
        registry: policy.compiled_retention_registry,
        subject: {
          artifact_class: subject.artifact_class,
          artifact_home: subject.artifact_home,
          artifact_digest: subject.artifact_digest,
          // The SOURCE's observed instant, under its own name and doing its own
          // job: the artifact's identity and the lifetime a deletion proof has to
          // sit inside. It is not the retention clock and never was.
          created_at: storedArtifact.created_at,
          retention_clock: retentionClock,
          holds,
          deletion_proof: subject.deletion_proof ?? null,
          derivative_coverage: coverage === null ? null : projectKeys(coverage, KERNEL_COVERAGE_KEYS),
          derivatives,
          satisfied_constraints: subject.satisfied_constraints ?? null,
        },
        now,
      });

      const record = storedDeletionEvaluationRecord({
        artifact_class: subject.artifact_class,
        artifact_home: subject.artifact_home,
        artifact_digest: subject.artifact_digest,
        decision: evaluated.decision,
        reason_id: evaluated.reason_id,
        retention_registry_digest: policy.retention_registry_digest,
        hold_inventory_digest: holdInventoryDigest,
        derivative_coverage_state: coverage?.state ?? "unknown",
        derivative_coverage_digest: coverageDigest,
        retention_clock: retentionClock,
        retention_clock_digest: retentionClockDigest,
        deletion_receipt: evaluated.deletion_receipt,
        evaluated_by: principal.slug,
        evaluated_at: now,
      });
      const envelope = storeEnvelope("stored_deletion_evaluation", record, {
        silent_purge: false, purge_without_proof: false,
        bytes_deleted: false, rows_deleted: false, external_purge_performed: false,
      });
      const row = await one(client,
        `SELECT ops.f01_record_deletion_evaluation($1::jsonb, $2::text, $3::text, $4::text)
                AS outcome`,
        [J(envelope), holdInventoryDigest, request.idempotency_key,
         requestDigest(operation, request, principal)]);
      const outcome = typeof row.outcome === "string" ? JSON.parse(row.outcome) : row.outcome;
      return resultFromOutcome(operation, outcome, principal);
    });
  }

  return Object.freeze({
    schema_version: V5_F01_STORE_SCHEMA_VERSION,
    operations: V5_F01_OPERATIONS,
    readRecordSourceAuthority,
    registerRecordSourceAuthorityPolicy,
    recordSourceObservation,
    recordCorporateArtifact,
    recordParsedProposal,
    registerDerivativeSourceLink,
    recordDocumentIdentity,
    recordArtifactPreservationHold,
    evaluateArtifactDeletion,
  });
}

// ---------------------------------------------------------------------------
// Load-time self-checks. Each is an invariant a later edit could break silently.
// ---------------------------------------------------------------------------

for (const operation of V5_F01_OPERATIONS) {
  if (!Object.prototype.hasOwnProperty.call(OPERATION_SCHEMAS, operation)) {
    throw new V5F01StoreError("operation_without_schema",
      `${operation} is registered without a closed caller schema`, { operation });
  }
}
for (const [operation, schema] of Object.entries(OPERATION_SCHEMAS)) {
  if (!V5_F01_OPERATIONS.includes(operation)) {
    throw new V5F01StoreError("schema_without_operation",
      `${operation} has a schema but is not a registered operation`, { operation });
  }
  if (schema.write && !schema.keys.includes("idempotency_key")) {
    throw new V5F01StoreError("write_without_idempotency_key",
      `${operation} writes and must accept an idempotency_key`, { operation });
  }
  // The guards must never refuse a field the contract legitimately accepts.
  for (const key of schema.keys) {
    const normalized = key.toLowerCase();
    for (const fragment of V5_F01_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        throw new V5F01StoreError("authority_guard_collides_with_contract",
          `the authority guard would refuse the legitimate field "${key}"`, { operation, key });
      }
    }
    if (V5_F01_DERIVED_ONLY_FIELDS.includes(normalized)) {
      throw new V5F01StoreError("derived_guard_collides_with_contract",
        `the derived guard would refuse the legitimate field "${key}"`, { operation, key });
    }
  }
}
// Only the policy and hold tools are authorityOnly, and both are humanOnly.
for (const [operation, schema] of Object.entries(OPERATION_SCHEMAS)) {
  const expected = operation === "register-record-source-authority-policy" ||
    operation === "record-artifact-preservation-hold";
  if (schema.authorityOnly !== expected || (schema.authorityOnly && !schema.humanOnly)) {
    throw new V5F01StoreError("authority_surface_drift",
      `${operation} does not carry the settled humanOnly/authorityOnly disposition`, { operation });
  }
}

// ---------------------------------------------------------------------------
// THE LIVE DOOR: the nine operations as registered MCP verbs.
//
// This is the integration tail the description above kept naming as the
// parent's job. It adds no judgment and no persistence of its own: every verb
// is the store method above, run unchanged, on the connection mcp.js already
// opened for that verb — the ordinary writer connection (opened `begin read
// only` for the one read, which is why every verb declares writerConnection),
// or the per-partner authority connection for the two authorityOnly verbs — and inside the
// transaction mcp.js already began. It opens no connection, begins and commits
// nothing itself, reads no environment and holds no clock.
//
// WHAT THE DOOR DOES, and each is a refusal rather than a repair:
//   * It hands the store a transaction-scoped handle whose `transaction` runs
//     the body on the SAME client, so the store's BEGIN/COMMIT never nests
//     inside mcp.js's transaction and a refusal rolls the whole verb back.
//   * It hands the store the actor mcp.js authenticated, as the only context
//     key the store accepts. The store re-derives the principal from the
//     database and refuses on any disagreement; this door cannot widen that.
//   * Every write runs inside the shared withEnvelope, so the tool_call replay
//     row and the F01 idempotency row are both keyed by the caller's key.
//   * A kernel, store or document-source refusal, and a database refusal the
//     F01 writers raise by name (f01_*), reach the caller as a ToolError that
//     keeps the stable code. Anything else is rethrown untouched.
//
// WHAT IT STILL IS NOT. It installs no policy, invents no field owner, calls
// no provider, moves no bytes, deletes nothing and activates no connector.
// ---------------------------------------------------------------------------

export const V5_F01_TOOL_REGISTRATION_SOURCE = "record-source-authority";

const TOOL_DESCRIPTIONS = deepFreeze({
  "read-record-source-authority":
    "DoctorCRE v5 F01 read. Return one selected record-authority view — the current field-authority and retention policy, one field's current state, its append-only events, state transitions, mutation receipts, reconciliation items, a stored corporate artifact, its proposal and derivative links and derivative coverage, a document's current identity or its versions, and preservation holds or deletion evaluations — with every digest RECOMPUTED from the committed rows by the database. A corrupt newest row refuses (f01_corrupt_stored_record) rather than falling back to an older healthy one. selector.kind is one of: current_policy, field_state, field_events, state_transitions, mutation_receipts, reconciliation_items, artifact, proposal_links, derivative_links, derivative_coverage, document, document_versions, holds, hold_history, deletion_evaluations.",
  "register-record-source-authority-policy":
    "HUMAN-ONLY, AUTHORITY-ONLY. Install one exact version of the F01 source×entity×field authority registry and the retention registry, compare-and-swapped against the prior policy digest (null for the first install). Every field entry must name exactly one owner source, one write direction (inbound/outbound/bidirectional/none), a version comparator, a conflict behaviour (refuse/reconcile) and the human class that resolves its conflicts; missing, duplicate, ambiguous or conflicting entries refuse. NOTHING IS DEFAULTED OR INVENTED: this verb records the owners a partner states and ships none of its own. Runs on the partner's authority connection; the installer is derived from that session, never from the payload.",
  "record-source-observation":
    "Judge one source observation of one field against the INSTALLED policy and the stored current state, and persist exactly one of: an accepted transition with its event and mutation receipt, a no-change confirmation, a refusal, or a visible reconciliation item. Stale (older-version) observations, recycled native IDs, a source writing against its registered write direction, a non-owner trying to establish a field, cross-tenant or cross-account evidence, and missing provenance refuse; a same-version or newer conflicting value follows the field's registered conflict behaviour — refuse, or reconcile into a visible item that leaves current state untouched. There is no silent last-write-wins. Refuses with no_installed_policy until a partner installs one.",
  "record-corporate-artifact":
    "Persist one immutable, source-agnostic corporate artifact identity (Salesforce export, OneDrive official copy, object-storage bytes, email, etc.) as EVIDENCE, after privacy, provenance, prior-identity and time validation. An artifact is never a fact and never makes a field authoritative; a second identity for the same source bytes refuses as artifact_identity_conflict, and Tour-only evidence is refused as a corporate source by name.",
  "record-parsed-proposal":
    "Persist one reviewable proposal parsed from a stored artifact, with a reversible, history-preserving link to it and its derivative-source registration. A proposal never becomes a fact, never advances state, carries no effect authority and always requires human review; an effect-bearing proposal refuses.",
  "register-derivative-source-link":
    "Bind one derived record to the exact stored artifact it came from, under the authenticated producer identity, before that derivative is complete. Provenance only: a link never establishes coverage, is never an exhaustive inventory and never permits deletion; a derivative claiming to be its own source, or produced before its source or after now, refuses.",
  "record-document-identity":
    "Persist and read back one versioned document identity — Neon operating identity, object-storage draft/sealed bytes, OneDrive official executed copy — with its preparation, delivery, signature, validity, version and filing states and its content hash, compare-and-swapped against the prior document digest (null for version 1). `source` is REQUIRED and states where this version came from: derived from a stored artifact (its derivative link is registered in the same transaction), authored here, or legacy_provenance_unknown with a stated basis — an absent statement is never read as 'no source'. Incoherent states refuse; a fully executed document without a filed official copy is recorded but reported as incomplete_official_filing. Transfers no bytes and sends nothing.",
  "record-artifact-preservation-hold":
    "HUMAN-ONLY, AUTHORITY-ONLY. Append one preservation hold (or its release) for one stored artifact, compare-and-swapped against the prior hold digest. A hold never deletes anything; it only blocks a later deletion evaluation.",
  "evaluate-artifact-deletion":
    "Evaluate whether one stored artifact MAY be deleted, from the installed retention policy, the server-stamped custody clock, its holds and its registered derivatives, and persist the bounded evaluation or deletion-proof receipt. An active or unknown hold, unknown derivative coverage, a missing proof or an unsatisfied governing constraint refuses. It NEVER deletes bytes and never purges anything.",
});

const DIGEST_SCHEMA = Object.freeze({ type: "string", pattern: "^sha256:[0-9a-f]{64}$" });
const PRIOR_DIGEST_SCHEMA = Object.freeze({ type: ["string", "null"] });
const TOOL_KEY_SCHEMAS = deepFreeze({
  schema_version: { type: "string" },
  idempotency_key: { type: "string" },
  selector: { type: "object" },
  field_registry: { type: "object" },
  retention_registry: { type: "object" },
  field_registry_digest: DIGEST_SCHEMA,
  retention_registry_digest: DIGEST_SCHEMA,
  expected_prior_policy_digest: PRIOR_DIGEST_SCHEMA,
  observation: { type: "object" },
  artifact: { type: "object" },
  proposal: { type: "object" },
  registration: { type: "object" },
  document: { type: "object" },
  source: { type: "object" },
  expected_prior_document_digest: PRIOR_DIGEST_SCHEMA,
  hold: { type: "object" },
  expected_prior_hold_digest: PRIOR_DIGEST_SCHEMA,
  subject: { type: "object" },
});

const F01_DATABASE_REFUSAL = /^(f01_[a-z0-9_]+)/;
const F01_ERROR_NAMES = Object.freeze(["V5F01StoreError", "V5F01Error", "V5F01DocumentSourceError"]);

/** The closed JSON schema for one verb, built from the store's own key list. */
export function v5F01ToolInputSchema(operation) {
  const schema = OPERATION_SCHEMAS[operation];
  if (!schema) {
    throw new V5F01StoreError("unknown_operation", `"${operation}" is not an F01 operation`, { operation });
  }
  const properties = {};
  for (const key of schema.keys) {
    if (!TOOL_KEY_SCHEMAS[key]) {
      throw new V5F01StoreError("tool_key_without_schema",
        `${operation} accepts "${key}" but the door declares no JSON type for it`, { operation, key });
    }
    properties[key] = { ...TOOL_KEY_SCHEMAS[key] };
  }
  return { type: "object", additionalProperties: false, properties, required: [...schema.required] };
}

/**
 * A handle over ONE already-open client whose transaction() runs the body on
 * that same client. mcp.js owns BEGIN/COMMIT for every verb; a store that
 * issued its own would commit half a verb, or warn and nest.
 */
export function v5F01TransactionScopedHandle(client) {
  if (!client || typeof client.query !== "function") {
    throw new V5F01StoreError("database_handle_required", "the verb has no open database client");
  }
  const handle = {
    query: (text, params) => client.query(text, params),
    transaction: fn => fn(handle),
  };
  return handle;
}

/** Translate an F01 refusal into the tool surface's refusal, keeping its code. */
export function v5F01ToolRefusal(error, ToolError) {
  if (error && F01_ERROR_NAMES.includes(error.name) && typeof error.code === "string") {
    return new ToolError({
      error: error.code, message: error.message,
      ...(error.detail !== undefined ? { detail: error.detail } : {}),
    });
  }
  const match = typeof error?.message === "string" ? F01_DATABASE_REFUSAL.exec(error.message) : null;
  if (match && typeof error?.code === "string") {
    return new ToolError({ error: match[1], message: error.message, sqlstate: error.code });
  }
  return null;
}

/**
 * The nine F01 verbs, ready for tools.js's registerTools. `createStore` is
 * injectable for the offline suite; production uses the store above.
 */
export function recordSourceAuthorityStoreTools({
  withEnvelope, ToolError, createStore = createRecordSourceAuthorityStore,
} = {}) {
  if (typeof withEnvelope !== "function" || typeof ToolError !== "function") {
    throw new V5F01StoreError("tool_wiring_incomplete",
      "recordSourceAuthorityStoreTools needs the shared withEnvelope and ToolError");
  }
  const tools = {};
  for (const registration of v5F01ToolRegistrations()) {
    const { name, write, humanOnly, authorityOnly, handler: method } = registration;
    const run = async (client, actor, args) => {
      const store = createStore({ db: v5F01TransactionScopedHandle(client) });
      try {
        const answer = await store[method](args ?? {}, { actor: { ...(actor ?? {}) } });
        // THE KERNEL'S V5_NO_EFFECTS SAYS database_writes: 0, which is true of a
        // pure evaluator and false of a verb that just committed F01 rows. The
        // door restates the one count that changed and leaves every other
        // zero — no provider, network, notification, schedule or activation —
        // exactly as the store reported it.
        return {
          ok: answer.decision !== "refuse",
          ...answer,
          effects: { ...answer.effects, database_writes: write ? "f01_record_layer_rows_only" : 0 },
        };
      } catch (error) {
        const refusal = v5F01ToolRefusal(error, ToolError);
        if (refusal) throw refusal;
        throw error;
      }
    };
    tools[name] = {
      write,
      // EVERY VERB, THE READ INCLUDED, RUNS ON THE WRITER CONNECTION, exactly as
      // the J102 door does. A verb that is neither `write` nor `writerConnection`
      // goes to mcp.js's reader connection, which carries no actor context, and
      // since 0732 ops.f01_context_actor_slug() derives the reader bundle as the
      // fixed slug 'carr-reader' — so openOperation's strict equality refused
      // every caller of the read (live 2026-09-27: handler joe-local, database
      // carr-reader). With this flag mcp.js opens the read `begin read only` on
      // the writer login and sets carr.acting_actor_slug first, so the database
      // derives the caller's own slug and the unchanged check passes. It widens
      // nothing: ops.f01_read is STABLE, the transaction is read-only, writes
      // already ran here, and the two authorityOnly verbs still route to the
      // partner's authority login (connectionRouteForTool checks that first).
      writerConnection: true,
      ...(humanOnly ? { humanOnly: true } : {}),
      ...(authorityOnly ? { authorityOnly: true } : {}),
      description: TOOL_DESCRIPTIONS[name],
      inputSchema: v5F01ToolInputSchema(name),
      handler: write
        ? async (c, actor, args) => withEnvelope(c, actor, name, args, () => run(c, actor, args))
        : async (c, actor, args) => run(c, actor, args),
    };
  }
  return tools;
}

// The door must cover exactly the operations the store serves, each described.
for (const operation of V5_F01_OPERATIONS) {
  if (typeof TOOL_DESCRIPTIONS[operation] !== "string") {
    throw new V5F01StoreError("tool_without_description",
      `${operation} is served but the door does not describe it`, { operation });
  }
  v5F01ToolInputSchema(operation);
}
