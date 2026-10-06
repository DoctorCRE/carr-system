// V5-F01 phase 2 — the persistence tail, proved case by case.
//
// Everything here is synthetic and nothing reaches a real database, provider or
// network. One test reads a checked-in schema file as TEXT — see the
// four-argument-writer case below, which proves a packaging property no fixture
// can see — and nothing else touches the filesystem. The store is exercised
// against a SCRIPTED FAKE HANDLE that
// records every statement and every parameter, so these tests prove the things a
// Node suite can actually prove about a persistence layer:
//
//   * which values are DERIVED and which are refused from the caller,
//   * which envelopes are built and which are deliberately left null,
//   * that prior state, prior artifacts, prior links and holds are LOADED,
//   * that the compare-and-swap operands travel to the database,
//   * that the canonical bytes are exactly what the SQL fixtures will compare.
//
// The things a fake cannot prove — real concurrency, real append-only refusal,
// real direct-DML refusal, real corrupt-row readback — are the disposable-
// database proof's job, and record-source-authority-live-pg.v5.test.mjs (run by
// the migration class on its disposable database) proves them there instead of
// being asserted twice here.
//
// NO FIXTURE NAMES A REAL THING. Every account, native id, object key, drive
// item, digest and retention period below is unmistakably test data, and the
// registries are TEST POLICY: what a caller might supply, never a claim about
// CARR's real field owners or retention periods.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
  V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
  V5_F01_PROPOSAL_SCHEMA_VERSION,
  V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
  V5_F01_RESERVED_DERIVATIVE_KINDS,
  compileFieldAuthorityRegistry,
  compileRetentionRegistry,
  fieldAuthorityRegistryPreimage,
  retentionRegistryPreimage,
  v5F01PolicyDigest,
  v5F01DecisionSubsetDigest,
  V5F01Error,
} from "../src/record-source-authority.v5.js";
import {
  V5_F01_STORE_SCHEMA_VERSION,
  V5_F01_ENVELOPE_SCHEMA_VERSION,
  V5_F01_OPERATIONS,
  V5_F01_READ_KINDS,
  V5_F01_STORE_RECORD_KINDS,
  V5_F01_DERIVED_ONLY_FIELDS,
  V5F01StoreError,
  createRecordSourceAuthorityStore,
  v5F01StoreOperationSchemas,
  v5F01ToolRegistrations,
  v5F01StoreEnvelope,
  v5F01EnvelopeCanonicalBytes,
  storedPolicyRecord,
  storedFieldStateRecord,
  storedHoldRecord,
} from "../src/record-source-authority-store.v5.js";
// The document half of the derivative-registration rule. Imported here so the
// suite can rebuild the exact binding the store reaches independently, and
// compare byte for byte rather than trusting the store's own answer about what
// it sent.
import {
  V5_F01_DOCUMENT_PROVENANCE_STATES,
  V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND,
  V5F01DocumentSourceError,
  composeDocumentSourceEnvelopes,
  evaluateDocumentSourceBinding,
} from "../src/document-derivative-registration.v5.js";

// --- synthetic policy ------------------------------------------------------

const SERVER_NOW = "2026-09-09T12:00:00.000Z";
const T = {
  early: "2026-09-01T09:00:00Z",
  mid: "2026-09-05T09:00:00Z",
  late: "2026-09-08T09:00:00Z",
  future: "2026-09-10T09:00:00Z",
};
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const VALUE_A = D(1);
const VALUE_B = D(2);

// THE STORED ARTIFACT'S OWN BYTES, which are not its record identity. Every
// scripted `stored_artifact` below carries this as the artifact's content digest,
// exactly as ops.f01_stored_artifact returns it, because the store now hands the
// kernel the artifact's CONTENT digest as well as its record digest — the only
// comparison that can tell a byte-identical copy from something derived.
const SOURCE_CONTENT = D(26);

// THE SERVER-STAMPED CUSTODY INSTANT, a week before the server's `now` and
// deliberately unlike T.early, which is what the artifact's SOURCE says it
// observed. A retention period runs from the first and never from the second, and
// keeping the two values apart is what makes a test measuring from the wrong one
// produce a visibly wrong number of days.
const CUSTODY_AT = "2026-09-02T00:00:00.000Z";

const FIELD_POLICY = Object.freeze({
  schema_version: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
  registry_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  entries: [
    {
      entity: "deal", field: "commission_amount",
      authoritative_home: "salesforce", owner_source: "salesforce",
      permitted_sources: [
        { source_system: "salesforce", direction: "inbound" },
        { source_system: "neon_record_layer", direction: "outbound" },
      ],
      requires_account_identity: true, requires_native_identity: true,
      version_comparator: "integer_sequence",
      conflict_behavior: "reconcile", human_resolver_class: "deal_owner",
      readback_required: true, sensitivity_classes: ["lease_economics"],
      taint_class: "corporate_source_of_record",
    },
    {
      entity: "deal", field: "next_step",
      authoritative_home: "neon_record_layer", owner_source: "neon_record_layer",
      permitted_sources: [{ source_system: "neon_record_layer", direction: "bidirectional" }],
      requires_account_identity: false, requires_native_identity: false,
      version_comparator: "integer_sequence",
      conflict_behavior: "reconcile", human_resolver_class: "deal_owner",
      readback_required: false, sensitivity_classes: ["lease_economics"],
      taint_class: "first_party_record_layer",
    },
  ],
});

const RETENTION_POLICY = Object.freeze({
  schema_version: V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
  registry_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  classes: [
    {
      artifact_class: "synthetic_test_lease",
      authoritative_home: "onedrive",
      default_retention_days: 1,
      governing_constraints: ["synthetic_test_constraint"],
      deletion_proof_required: true,
      surviving_derivatives: ["synthetic_test_abstract"],
    },
  ],
});

const FIELD_REGISTRY = compileFieldAuthorityRegistry(FIELD_POLICY);
const RETENTION_REGISTRY = compileRetentionRegistry(RETENTION_POLICY);
const FIELD_PREIMAGE = fieldAuthorityRegistryPreimage(FIELD_REGISTRY);
const RETENTION_PREIMAGE = retentionRegistryPreimage(RETENTION_REGISTRY);

const STORED_POLICY_RECORD = storedPolicyRecord({
  registry_version: 1,
  field_registry: FIELD_PREIMAGE,
  field_registry_digest: FIELD_REGISTRY.registry_digest,
  retention_registry: RETENTION_PREIMAGE,
  retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  prior_policy_digest: null,
  installed_by: "joe",
  installed_at: SERVER_NOW,
});
const POLICY_DIGEST = digest(STORED_POLICY_RECORD);

const STORED_POLICY = Object.freeze({
  policy_seq: 1,
  registry_version: 1,
  policy_digest: POLICY_DIGEST,
  prior_policy_digest: null,
  field_registry: FIELD_PREIMAGE,
  field_registry_digest: FIELD_REGISTRY.registry_digest,
  retention_registry: RETENTION_PREIMAGE,
  retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  domain_policy_digest: v5F01PolicyDigest(),
  installed_by: "joe",
  installed_at: SERVER_NOW,
  integrity: "recomputed_from_committed_row",
});

// --- actors ----------------------------------------------------------------

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});
const ctx = actor => ({ actor });

// --- derivative-registration coverage --------------------------------------
//
// WHAT THE REAL DATABASE ANSWERS TODAY is the unknown one, for every artifact:
// ops.f01_derivative_coverage has no way to establish a closed producer set, and
// this suite does not pretend otherwise. The established shape below exists so
// the store's PLUMBING can be proved — that it loads the answer, passes it to
// the kernel unchanged, and binds the digest of it into the record — without
// which the whole coverage path would be untested on the accept side. Which of
// the two a fixture uses is always stated at the call site.

const UNKNOWN_COVERAGE = Object.freeze({
  state: "unknown",
  reason_id: "producer_closure_not_established",
  registered_derivative_kinds: [],
  registered_link_count: 0,
  is_exhaustive_inventory: false,
  empty_link_set_means_verified_absence: false,
});

const ESTABLISHED_COVERAGE = Object.freeze({
  state: "established",
  reason_id: "synthetic_test_closure_verified",
  registered_derivative_kinds: ["synthetic_test_abstract"],
  registered_link_count: 1,
  is_exhaustive_inventory: false,
});

// --- the retention clock ----------------------------------------------------
//
// WHAT THE REAL ops.f01_retention_clock ANSWERS: the server-stamped instant the
// record layer took custody of the artifact, the reference it came from, and both
// halves of the anti-alias statement — the source's own observed instant, reported
// AND reported as not the thing the period runs from. The shape below is that
// answer, including the fields the kernel's closed shape does not accept, so the
// store's projection is exercised rather than assumed.
const RETENTION_CLOCK = Object.freeze({
  artifact_digest: D(51),
  kind: "server_recorded_custody",
  event_kind: null,
  started_at: CUSTODY_AT,
  reference: "ops.f01_corporate_artifact.recorded_at",
  provenance: "server_stamped_custody",
  event_digest: null,
  verified: true,
  source_observed_at: T.early,
  source_observed_at_used: false,
  integrity: "recomputed_from_committed_row",
});

/** The eight fields the kernel accepts, projected exactly as the store projects them. */
const PROJECTED_CLOCK = Object.freeze({
  kind: RETENTION_CLOCK.kind,
  event_kind: RETENTION_CLOCK.event_kind,
  started_at: RETENTION_CLOCK.started_at,
  reference: RETENTION_CLOCK.reference,
  provenance: RETENTION_CLOCK.provenance,
  event_digest: RETENTION_CLOCK.event_digest,
  verified: RETENTION_CLOCK.verified,
  source_observed_at_used: RETENTION_CLOCK.source_observed_at_used,
});

// --- the scripted fake handle ---------------------------------------------

class FakeDb {
  constructor(script = {}) {
    this.script = { ...script };
    this.calls = [];
    this.began = 0;
    this.committed = 0;
    this.rolledBack = 0;
  }

  set(key, value) { this.script[key] = value; return this; }

  async query(text, params = []) {
    this.calls.push({ text, params });
    if (text === "BEGIN") { this.began += 1; return { rows: [] }; }
    if (text === "COMMIT") { this.committed += 1; return { rows: [] }; }
    if (text === "ROLLBACK") { this.rolledBack += 1; return { rows: [] }; }

    if (text.includes("ops.f01_principal()") && text.includes("ops.f01_now_text()")) {
      return { rows: [{
        principal: this.script.principal ?? {
          actor_slug: "joe", human: true,
          authorization_class: "verified_partner",
          derived_by: "server_established_transaction_context",
        },
        server_now: this.script.server_now ?? SERVER_NOW,
      }] };
    }
    if (text.includes("ops.f01_replay_outcome(")) {
      if (this.script.replayError) throw this.script.replayError;
      return { rows: [{ outcome: this.script.replay_outcome ?? null }] };
    }
    if (text.includes("ops.f01_derivative_coverage(")) {
      return { rows: [{
        coverage: this.script.coverage ?? UNKNOWN_COVERAGE,
        coverage_digest: this.script.coverage_digest
          ?? digest(this.script.coverage ?? UNKNOWN_COVERAGE),
      }] };
    }
    if (text.includes("ops.f01_stored_derivatives(")) {
      return { rows: [{ derivatives: this.script.stored_derivatives ?? null }] };
    }
    if (text.includes("ops.f01_retention_clock(")) {
      // `undefined` means "not scripted" and yields the ordinary custody answer;
      // an explicit null is a database that could derive no clock at all, which a
      // fixture states on purpose.
      const clock = this.script.retention_clock === undefined
        ? RETENTION_CLOCK : this.script.retention_clock;
      return { rows: [{
        clock,
        clock_digest: this.script.retention_clock_digest
          ?? (clock === null ? null : digest(clock)),
      }] };
    }
    if (text.includes("ops.f01_current_policy()")) {
      return { rows: [{ policy: this.script.policy ?? null }] };
    }
    if (text.includes("ops.f01_current_field_state(")) {
      return { rows: [{ state: this.script.field_state ?? null }] };
    }
    if (text.includes("ops.f01_stored_artifact_by_identity(")) {
      return { rows: [{ prior: this.script.prior_artifact ?? null }] };
    }
    if (text.includes("ops.f01_stored_artifact(")) {
      return { rows: [{ artifact: this.script.stored_artifact ?? null }] };
    }
    if (text.includes("ops.f01_hold_inventory(")) {
      return { rows: [{
        holds: this.script.holds ?? [],
        holds_digest: this.script.holds_digest ?? digest(this.script.holds ?? []),
      }] };
    }
    if (text.includes("ops.f01_read('document'")) {
      return { rows: [{ body: { body: this.script.document_current ?? null } }] };
    }
    if (text.includes("ops.f01_document_version_source(")) {
      // NULL means "no statement", never "no source". The default is null
      // because that is what the database answers for a version nobody has
      // recorded a statement about.
      return { rows: [{ provenance: this.script.document_provenance ?? null }] };
    }
    if (text.includes("ops.f01_read('hold_history'")) {
      return { rows: [{ body: { body: this.script.hold_history ?? [] } }] };
    }
    if (text.includes("ops.f01_read(")) {
      return { rows: [{ body: this.script.read_body ?? { body: null } }] };
    }
    for (const fn of ["f01_install_policy", "f01_apply_observation", "f01_record_artifact",
      "f01_record_proposal", "f01_register_derivative_link", "f01_record_document",
      "f01_record_hold", "f01_record_deletion_evaluation"]) {
      if (text.includes(`ops.${fn}(`)) {
        this.lastWrite = { fn, params };
        if (this.script.writeError) throw this.script.writeError;
        const envelope = index => params[index] == null ? null : JSON.parse(params[index]);
        const verified = value => value && ({ record: value.record, record_digest: value.record_digest });
        const env = envelope(fn === "f01_apply_observation" ? 5 : 0);
        const rec = env?.record;
        const outcome = { actor_slug: "joe", outcome: "recorded" };
        switch (fn) {
          case "f01_install_policy":
            Object.assign(outcome, { operation: "register-record-source-authority-policy", outcome: "installed",
              readback: { ...rec, policy_digest: env.record_digest } }); break;
          case "f01_apply_observation":
            Object.assign(outcome, { operation: "record-source-observation",
              outcome: params[0] === "accept" ? "accepted" : params[0], entity: params[1], field: params[2],
              policy_digest: params[3], ...envelope(12),
              current_state_transition_digest: envelope(6)?.record_digest ?? null,
              event_digest: envelope(7)?.record_digest ?? null,
              mutation_receipt_digest: envelope(8)?.record_digest ?? null,
              reconciliation_item_digest: envelope(9)?.record_digest ?? null,
              readback: { current_state: rec } }); break;
          case "f01_record_artifact":
            Object.assign(outcome, { operation: "record-corporate-artifact", artifact_digest: env.record_digest,
              readback: { artifact: rec } }); break;
          case "f01_record_proposal":
            Object.assign(outcome, { operation: "record-parsed-proposal", proposal_digest: env.record_digest,
              link_digest: envelope(1).record_digest,
              // The third envelope is the producer registration the writer
              // REQUIRES; a fake that ignored it would let a regression through.
              derivative_link_digest: envelope(2).record_digest,
              derivative_registration_bound: true,
              readback: verified(envelope(1)) }); break;
          case "f01_register_derivative_link":
            Object.assign(outcome, { operation: "register-derivative-source-link",
              outcome: "registered", link_digest: env.record_digest,
              establishes_coverage: false, is_exhaustive_inventory: false,
              permits_deletion: false,
              coverage: this.script.coverage ?? UNKNOWN_COVERAGE,
              readback: verified(env) }); break;
          case "f01_record_document": {
            // THE SIX-ARGUMENT WRITER. The provenance envelope is the second
            // argument and is MANDATORY; the derivative link is the third and is
            // present only for a derived version. A fake that ignored either
            // would let exactly the regression this seam exists to prevent
            // through, so both are read back into the outcome the way
            // ops.f01_record_document builds it.
            const provenance = envelope(1);
            const derivative = envelope(2);
            if (provenance === null) {
              throw Object.assign(new Error("f01_document_provenance_required"),
                { code: "f01_document_provenance_required" });
            }
            Object.assign(outcome, { operation: "record-document-identity",
              document_digest: env.record_digest,
              official_filing_state: rec.official_filing_state,
              provenance_state: provenance.record.provenance_state,
              provenance_digest: provenance.record_digest,
              derivative_link_digest: derivative === null ? null : derivative.record_digest,
              derivative_registration_bound:
                provenance.record.provenance_state === "derived_from_stored_artifact",
              provenance_readback: verified(provenance),
              readback: verified(env) });
            break;
          }
          case "f01_record_hold":
            Object.assign(outcome, { operation: "record-artifact-preservation-hold", hold_digest: env.record_digest,
              hold_state: rec.state, readback: verified(env) }); break;
          case "f01_record_deletion_evaluation":
            Object.assign(outcome, { operation: "evaluate-artifact-deletion", outcome: rec.decision,
              reason_id: rec.reason_id, evaluation_digest: env.record_digest, readback: verified(env),
              hold_inventory: this.script.holds ?? [],
              surviving_derivatives: this.script.policy?.retention_registry.classes
                .find(c => c.artifact_class === rec.artifact_class)?.surviving_derivatives ?? [] }); break;
        }
        this.lastOutcome = outcome;
        return { rows: [{ outcome }] };
      }
    }
    throw new Error(`unscripted statement: ${text}`);
  }

  /** The parameters the last security-definer writer was called with. */
  write() {
    assert.ok(this.lastWrite, "expected a writer call");
    return this.lastWrite;
  }

  param(index) { return this.write().params[index]; }
  json(index) {
    const value = this.param(index);
    return value === null || value === undefined ? null : JSON.parse(value);
  }
}

function storeWith(script = {}) {
  const db = new FakeDb(script);
  return { db, store: createRecordSourceAuthorityStore({ db }) };
}

function policyScript(extra = {}) {
  return { policy: STORED_POLICY, ...extra };
}

// THE THREE ERROR TYPES A CONTRACT VIOLATION MAY ARRIVE AS, and no fourth. The
// store raises its own, the kernel raises its own, and the document-source module
// raises its own for a declaration it cannot read at all. Naming all three keeps
// this helper from silently accepting a TypeError or a ReferenceError — which is
// exactly what a broken import cycle would produce — as a legitimate refusal.
async function refuses(promise, code) {
  await assert.rejects(promise, error => {
    assert.ok(error instanceof V5F01StoreError || error instanceof V5F01Error ||
      error instanceof V5F01DocumentSourceError,
      `expected a store, kernel or document-source error, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code ${code}, got ${error.code}`);
    return true;
  });
}

// ===========================================================================
// The registration seam.
// ===========================================================================

test("the nine named operations are exactly the proposed tool surface", () => {
  assert.deepEqual([...V5_F01_OPERATIONS].sort(), [
    "evaluate-artifact-deletion",
    "read-record-source-authority",
    "record-artifact-preservation-hold",
    "record-corporate-artifact",
    "record-document-identity",
    "record-parsed-proposal",
    "record-source-observation",
    "register-derivative-source-link",
    "register-record-source-authority-policy",
  ]);
  const registrations = v5F01ToolRegistrations();
  assert.equal(registrations.length, 9);
  for (const entry of registrations) {
    assert.ok(V5_F01_OPERATIONS.includes(entry.name));
    assert.ok(entry.role.length > 40, entry.name);
    // The migration is bound; the registry successor and acceptance are not.
    assert.equal(entry.registered_in_scac, true);
    assert.equal(entry.registered_in_mutation_registry, true);
    assert.equal(entry.migration_bound, true);
    assert.equal(entry.accepted, false);
  }
});

test("only the policy and hold tools are humanOnly plus authorityOnly", () => {
  const schemas = v5F01StoreOperationSchemas();
  const authority = V5_F01_OPERATIONS.filter(op => schemas[op].authorityOnly);
  assert.deepEqual(authority.sort(),
    ["record-artifact-preservation-hold", "register-record-source-authority-policy"]);
  for (const op of authority) assert.equal(schemas[op].humanOnly, true, op);
  assert.equal(schemas["read-record-source-authority"].write, false);
  for (const op of V5_F01_OPERATIONS) {
    if (op === "read-record-source-authority") continue;
    assert.equal(schemas[op].write, true, op);
    assert.ok(schemas[op].required.includes("idempotency_key"), op);
  }
});

test("every write schema is closed and names no derived field", () => {
  const schemas = v5F01StoreOperationSchemas();
  for (const op of V5_F01_OPERATIONS) {
    for (const key of schemas[op].keys) {
      assert.ok(!V5_F01_DERIVED_ONLY_FIELDS.includes(key.toLowerCase()),
        `${op}.${key} collides with a derived-only field`);
    }
  }
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("tenant"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("now"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("current_state"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("prior_artifact"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("prior_link"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("holds"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("created_at"));
  // The two the document-source seam adds: both are digests of records the
  // server builds, so naming either would be choosing the bytes a write is
  // checked against.
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("provenance_digest"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("derivative_link_digest"));
});

// ===========================================================================
// Caller authority and derived-value injection.
// ===========================================================================

test("a caller cannot supply tenant, now, actor or an evaluated result", async () => {
  const { store } = storeWith(policyScript());
  const base = {
    idempotency_key: "syn-observation-0001",
    observation: {
      entity: "deal", field: "next_step", source_system: "neon_record_layer",
      value_digest: VALUE_A, version: 1, observed_at: T.mid,
      provenance: { adapter_kind: "synthetic_test_adapter",
        evidence_ref: "synthetic-evidence-0001", retrieval_class: "corporate_record_export" },
    },
  };
  for (const [key, value, code] of [
    ["tenant", ORGANIZATION_TENANT_ID, "caller_derived_field_refused"],
    ["now", SERVER_NOW, "caller_derived_field_refused"],
    ["current_state", {}, "caller_derived_field_refused"],
    ["decision", "accept", "caller_derived_field_refused"],
    ["policy_digest", POLICY_DIGEST, "caller_derived_field_refused"],
    ["actor", JOE, "caller_authority_field_refused"],
    ["authorized_by", "joe", "caller_authority_field_refused"],
    ["override", true, "caller_authority_field_refused"],
  ]) {
    await refuses(store.recordSourceObservation({ ...base, [key]: value }, ctx(JOE)), code);
  }
  await refuses(store.recordSourceObservation({ ...base, surprise: 1 }, ctx(JOE)), "unknown_field");
});

test("the observation payload itself refuses tenant, actor and prior-state injection", async () => {
  const { store } = storeWith(policyScript());
  const call = extra => store.recordSourceObservation({
    idempotency_key: "syn-observation-0002",
    observation: {
      entity: "deal", field: "next_step", source_system: "neon_record_layer",
      value_digest: VALUE_A, version: 1, observed_at: T.mid,
      provenance: { adapter_kind: "synthetic_test_adapter",
        evidence_ref: "synthetic-evidence-0002", retrieval_class: "corporate_record_export" },
      ...extra,
    },
  }, ctx(JOE));
  await refuses(call({ tenant: ORGANIZATION_TENANT_ID }), "caller_derived_field_refused");
  await refuses(call({ event_seq: 9 }), "caller_derived_field_refused");
  await refuses(call({ last_event_digest: D(9) }), "caller_derived_field_refused");
  await refuses(call({ owner_source: "salesforce" }), "caller_derived_field_refused");
  await refuses(call({ acting_as: "joe" }), "caller_authority_field_refused");
});

test("an accessor, a symbol key or an own __proto__ refuses rather than being read", async () => {
  const { store } = storeWith(policyScript());
  const withAccessor = { idempotency_key: "syn-accessor-0001" };
  Object.defineProperty(withAccessor, "observation", { get: () => ({}), enumerable: true });
  await refuses(store.recordSourceObservation(withAccessor, ctx(JOE)), "accessor_property_refused");

  const withSymbol = { idempotency_key: "syn-symbol-0001", observation: {} };
  withSymbol[Symbol("hidden")] = 1;
  await refuses(store.recordSourceObservation(withSymbol, ctx(JOE)), "symbol_key_refused");

  const withProto = JSON.parse('{"idempotency_key":"syn-proto-0001","__proto__":{"x":1}}');
  await refuses(store.recordSourceObservation(withProto, ctx(JOE)), "prototype_key_refused");
});

test("an unauthenticated or unknown actor refuses before any statement runs", async () => {
  const { db, store } = storeWith(policyScript());
  await refuses(store.readRecordSourceAuthority(
    { selector: { kind: "current_policy" } }, { actor: { slug: "nobody" } }), "unauthenticated_actor");
  await refuses(store.readRecordSourceAuthority(
    { selector: { kind: "current_policy" } }, {}), "missing_field");
  assert.equal(db.calls.length, 0, "no statement may run for an unauthenticated request");
});

test("humanOnly plus authorityOnly refuses an ordinary evidence writer before any write", async () => {
  const { db, store } = storeWith(policyScript());
  await refuses(store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0001",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  }, ctx(AGENT)), "human_only_operation_refused");
  await refuses(store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-0001",
    hold: { hold_id: "synthetic-hold-0001", artifact_digest: D(7), state: "active" },
  }, ctx(AGENT)), "human_only_operation_refused");
  assert.equal(db.calls.length, 0, "an authorityOnly refusal happens before the transaction");
});

test("a database-derived actor that differs from the handler's refuses the write", async () => {
  const { db, store } = storeWith(policyScript({
    principal: { actor_slug: "dell", human: true, authorization_class: "verified_partner" },
  }));
  await refuses(store.recordSourceObservation({
    idempotency_key: "syn-observation-0003",
    observation: {
      entity: "deal", field: "next_step", source_system: "neon_record_layer",
      value_digest: VALUE_A, version: 1, observed_at: T.mid,
      provenance: { adapter_kind: "synthetic_test_adapter",
        evidence_ref: "synthetic-evidence-0003", retrieval_class: "corporate_record_export" },
    },
  }, ctx(JOE)), "actor_context_mismatch");
  assert.equal(db.rolledBack, 1, "the transaction is rolled back, not left open");
  assert.equal(db.committed, 0);
});

// ===========================================================================
// register-record-source-authority-policy.
// ===========================================================================

test("a policy version installs with exact digests and a null prior on genesis", async () => {
  const { db, store } = storeWith({ policy: null, outcome: { outcome: "installed" } });
  const answer = await store.registerRecordSourceAuthorityPolicy({
    schema_version: V5_F01_STORE_SCHEMA_VERSION,
    idempotency_key: "syn-policy-genesis-0001",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  }, ctx(JOE));

  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "policy_version_installed");
  assert.equal(answer.entries_invented, 0);
  assert.equal(answer.prior_policy_digest, null);
  assert.equal(db.write().fn, "f01_install_policy");

  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_policy_version");
  assert.equal(envelope.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(envelope.record_digest, digest(envelope.record));
  assert.equal(envelope.record.installed_by, "joe", "installed_by is derived, never supplied");
  assert.equal(envelope.record.installed_at, SERVER_NOW, "the instant comes from the server");
  assert.equal(envelope.record.field_registry_digest, digest(envelope.record.field_registry));
  assert.equal(envelope.record.retention_registry_digest,
    digest(envelope.record.retention_registry));
  assert.equal(envelope.record.domain_policy_digest, v5F01PolicyDigest());
  assert.equal(envelope.record.decision_subset_digest, v5F01DecisionSubsetDigest());
  assert.equal(db.param(1), null, "the CAS operand for genesis is null");
  assert.equal(db.param(2), "syn-policy-genesis-0001");
  assert.ok(/^sha256:[0-9a-f]{64}$/.test(db.param(3)), "the idempotency request digest travels");
});

test("a stated registry digest that is not the registry's own digest refuses", async () => {
  const { db, store } = storeWith({ policy: null });
  await refuses(store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0002",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: D(99),
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  }, ctx(JOE)), "field_registry_digest_mismatch");
  await refuses(store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0003",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: D(98),
  }, ctx(JOE)), "retention_registry_digest_mismatch");
  assert.equal(db.calls.length, 0, "the digest check precedes the transaction");
});

test("installing against a stale prior-current digest refuses before the write", async () => {
  const { db, store } = storeWith(policyScript());
  await refuses(store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0004",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
    expected_prior_policy_digest: D(97),
  }, ctx(JOE)), "stale_policy_digest");
  assert.equal(db.lastWrite, undefined, "no writer runs on a stale CAS");
  assert.equal(db.rolledBack, 1);
});

test("a registry the kernel refuses never reaches the database", async () => {
  const { db, store } = storeWith({ policy: null });
  const contradictory = {
    ...FIELD_POLICY,
    entries: [{ ...FIELD_POLICY.entries[0], owner_source: "nobody_owns_this" }],
  };
  await assert.rejects(() => store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0005",
    field_registry: contradictory, retention_registry: RETENTION_POLICY,
    field_registry_digest: D(96), retention_registry_digest: D(95),
  }, ctx(JOE)), error => {
    assert.equal(error.code, "owner_source_not_permitted");
    return true;
  });
  assert.equal(db.calls.length, 0);
});

// ===========================================================================
// record-source-observation — the four separate records.
// ===========================================================================

const OWNER_OBSERVATION = Object.freeze({
  entity: "deal", field: "commission_amount", source_system: "salesforce",
  account: "synthetic-account-0001",
  native_identity: {
    source_system: "salesforce",
    native_id: "SYNTHETIC-NATIVE-0001",
    native_id_epoch: "synthetic-epoch-1",
  },
  value_digest: VALUE_A, version: 5, observed_at: T.mid,
  provenance: {
    adapter_kind: "synthetic_test_adapter",
    evidence_ref: "synthetic-evidence-0010",
    retrieval_class: "corporate_record_export",
  },
  taint_class: "corporate_source_of_record",
  readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_A },
});

function storedState(overrides = {}) {
  const record = storedFieldStateRecord({
    entity: "deal", field: "commission_amount",
    account: "synthetic-account-0001",
    native_identity: {
      source_system: "salesforce",
      native_id: "SYNTHETIC-NATIVE-0001",
      native_id_epoch: "synthetic-epoch-1",
    },
    value_digest: VALUE_B, version: 4, owner_source: "salesforce",
    authoritative_home: "salesforce",
    observed_at: T.early, event_seq: 7, last_event_digest: D(9),
    policy_digest: POLICY_DIGEST, updated_by: "joe", updated_at: SERVER_NOW,
    ...overrides,
  });
  return { state_digest: digest(record), current_state: record, integrity: "recomputed_from_committed_row" };
}

test("an accepted owner update writes state, transition, event and receipt and nothing else", async () => {
  const state = storedState();
  const { db, store } = storeWith(policyScript({ field_state: state }));
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-observation-accept-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));

  assert.equal(answer.decision, "accept");
  assert.equal(answer.reason_id, "owner_value_updated");
  assert.equal(answer.silent_last_write_wins, false);
  assert.equal(answer.any_one_substitutes_for_another, false);
  assert.equal(db.write().fn, "f01_apply_observation");

  const [decision, entity, field, policyDigest, expectedState] = db.write().params;
  assert.equal(decision, "accept");
  assert.equal(entity, "deal");
  assert.equal(field, "commission_amount");
  assert.equal(policyDigest, POLICY_DIGEST, "the policy CAS operand is the stored digest");
  assert.equal(expectedState, state.state_digest, "the state CAS operand is the stored digest");

  const stateEnvelope = db.json(5);
  const transition = db.json(6);
  const event = db.json(7);
  const receipt = db.json(8);
  const reconciliation = db.json(9);

  assert.equal(reconciliation, null, "an accepted change is not a conflict");
  assert.equal(stateEnvelope.record_kind, "stored_field_state");
  assert.equal(transition.record_kind, "stored_state_transition");
  assert.equal(event.record_kind, "stored_source_event");
  assert.equal(receipt.record_kind, "stored_mutation_receipt");

  // The three domain records are DISTINCT and the receipt alone binds the other
  // two. That asymmetry is the checkable form of "none substitutes for another".
  const digests = new Set([transition.record_digest, event.record_digest, receipt.record_digest]);
  assert.equal(digests.size, 3);
  assert.equal(receipt.record.current_state_transition_digest, transition.record_digest);
  assert.equal(receipt.record.event_digest, event.record_digest);
  assert.equal(transition.record.mutation_receipt_digest, undefined);
  assert.equal(event.record.mutation_receipt_digest, undefined);
  assert.equal(receipt.record.actor, null, "the kernel records no actor");
  assert.equal(receipt.record.actor_derived_by, "authenticated_handler_context");

  // The event extends the stored chain rather than starting a new one.
  assert.equal(event.record.event_seq, 8);
  assert.equal(event.record.previous_event_digest, D(9));
  assert.equal(event.record.append_only, true);
  assert.equal(event.record.rewrites_prior_event, false);

  // Current state is bound to both.
  assert.equal(stateEnvelope.record.event_seq, 8);
  assert.equal(stateEnvelope.record.last_event_digest, event.record_digest);
  assert.equal(stateEnvelope.record.value_digest, transition.record.to_value_digest);
  assert.equal(stateEnvelope.record.updated_by, "joe");
  assert.equal(stateEnvelope.record.updated_at, SERVER_NOW);
});

test("a first observation by the owner establishes the field with a null prior chain", async () => {
  const { db, store } = storeWith(policyScript({ field_state: null }));
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-observation-establish-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));
  assert.equal(answer.decision, "accept");
  assert.equal(answer.reason_id, "field_established");
  assert.equal(db.param(4), null, "no established state means a null CAS operand");
  const event = db.json(7);
  assert.equal(event.record.event_seq, 1);
  assert.equal(event.record.previous_event_digest, null);
  assert.equal(event.record.event_kind, "source_field_established");
  assert.equal(db.json(6).record.from_value_digest, null);
});

test("an equal-version contradiction writes the reconciliation item and no mutation record", async () => {
  const state = storedState({ version: 5, value_digest: VALUE_B });
  const { db, store } = storeWith(policyScript({ field_state: state }));
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-observation-conflict-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));

  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "equal_version_contradiction");
  assert.equal(answer.conflict_kind, "equal_version_contradiction");
  assert.equal(db.json(5), null, "no current state is written");
  assert.equal(db.json(6), null, "no transition is written");
  assert.equal(db.json(7), null, "no event is written");
  assert.equal(db.json(8), null, "no receipt is written");
  const item = db.json(9);
  assert.equal(item.record_kind, "stored_reconciliation_item");
  assert.equal(item.record.visible, true);
  assert.equal(item.record.applied, false);
  assert.equal(item.record.resolved_by_machine, false);
  assert.equal(item.record.human_resolver_class, "deal_owner");
});

test("a stale observation refuses and writes no record at all", async () => {
  const state = storedState({ version: 9 });
  const { db, store } = storeWith(policyScript({ field_state: state }));
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-observation-stale-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "stale_observation_refused");
  assert.equal(db.param(0), "refuse");
  for (const index of [5, 6, 7, 8, 9]) assert.equal(db.json(index), null);
});

test("an observation is judged against the LOADED state, never a caller's picture of it", async () => {
  const state = storedState();
  const { db, store } = storeWith(policyScript({ field_state: state }));
  await store.recordSourceObservation({
    idempotency_key: "syn-observation-loaded-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));
  const loadCall = db.calls.find(c => c.text.includes("ops.f01_current_field_state("));
  assert.ok(loadCall, "the store loads the current state from the database");
  assert.deepEqual(loadCall.params, ["deal", "commission_amount"]);
});

test("an observation with no installed policy refuses rather than inventing a registry", async () => {
  const { db, store } = storeWith({ policy: null });
  await refuses(store.recordSourceObservation({
    idempotency_key: "syn-observation-nopolicy-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE)), "no_installed_policy");
  assert.equal(db.lastWrite, undefined);
});

test("a stored registry that no longer hashes to its digest refuses instead of being repaired", async () => {
  const tampered = {
    ...STORED_POLICY,
    field_registry: { ...FIELD_PREIMAGE, registry_version: 99 },
  };
  const { store } = storeWith({ policy: tampered });
  await refuses(store.recordSourceObservation({
    idempotency_key: "syn-observation-corrupt-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE)), "corrupt_stored_registry");
});

// ===========================================================================
// record-corporate-artifact.
// ===========================================================================

const ARTIFACT = Object.freeze({
  source_system: "salesforce",
  source_class: "synthetic_test_object",
  source_account: "synthetic-account-0001",
  native_identity: {
    source_system: "salesforce",
    native_id: "SYNTHETIC-ARTIFACT-0001",
    native_id_epoch: "synthetic-epoch-1",
  },
  native_version: "synthetic-version-1",
  content_digest: VALUE_A,
  byte_length: 1024,
  observed_at: T.mid,
  provenance: {
    adapter_kind: "synthetic_test_adapter",
    evidence_ref: "synthetic-evidence-0020",
    retrieval_class: "corporate_record_export",
  },
  evidence_class: "corporate_record_export",
  declared_data_classes: ["lease_economics"],
  taint_class: "corporate_source_of_record",
});

test("an artifact is admitted as immutable evidence and never as a fact", async () => {
  const { db, store } = storeWith({ prior_artifact: null, outcome: { outcome: "recorded" } });
  const answer = await store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-0001", artifact: ARTIFACT,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.is_fact, false);
  assert.equal(answer.makes_field_authoritative, false);
  assert.equal(answer.immutable, true);
  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_corporate_artifact");
  assert.equal(envelope.record_digest, digest(envelope.record));
  assert.equal(envelope.is_fact, false);
  assert.equal(answer.artifact_digest, envelope.record_digest);
});

test("the prior artifact is LOADED by identity and a caller's copy is refused by name", async () => {
  const priorRecord = { ...ARTIFACT, content_digest: VALUE_B, tenant: ORGANIZATION_TENANT_ID };
  const { db, store } = storeWith({ prior_artifact: { artifact: priorRecord } });
  const answer = await store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-0002", artifact: ARTIFACT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "artifact_identity_conflict");
  assert.equal(db.lastWrite, undefined, "a conflicting identity never reaches the writer");

  const loadCall = db.calls.find(c => c.text.includes("ops.f01_stored_artifact_by_identity("));
  assert.deepEqual(loadCall.params, ["salesforce", "synthetic-account-0001",
    "SYNTHETIC-ARTIFACT-0001", "synthetic-epoch-1", "synthetic-version-1"]);

  await refuses(store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-0003", artifact: ARTIFACT, prior_artifact: priorRecord,
  }, ctx(JOE)), "caller_derived_field_refused");
});

test("Tour-only evidence is refused as a generic corporate source", async () => {
  const { db, store } = storeWith({ prior_artifact: null });
  const answer = await store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-tour-0001",
    artifact: { ...ARTIFACT, evidence_class: "tour_rights_receipt" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "tour_only_evidence_not_generic_authority");
  assert.equal(db.lastWrite, undefined);
});

test("an artifact observed after server time refuses", async () => {
  const { db, store } = storeWith({ prior_artifact: null });
  const answer = await store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-future-0001",
    artifact: { ...ARTIFACT, observed_at: T.future },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "observation_after_now");
  assert.equal(db.lastWrite, undefined);
});

// ===========================================================================
// record-parsed-proposal.
// ===========================================================================

const PROPOSAL = Object.freeze({
  artifact_digest: D(21),
  source_system: "salesforce",
  source_account: "synthetic-account-0001",
  proposed_bindings: [
    { entity: "deal", field: "commission_amount", value_digest: VALUE_A, version: 6 },
  ],
  confidence: 0.75,
  evidence_refs: ["synthetic-evidence-0030"],
  observed_at: T.mid,
});

test("a proposal is stored as reviewable and never as a fact", async () => {
  const { db, store } = storeWith(policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    outcome: { outcome: "recorded" },
  }));
  const answer = await store.recordParsedProposal({
    idempotency_key: "syn-proposal-0001", proposal: PROPOSAL,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.becomes_fact, false);
  assert.equal(answer.advances_state, false);
  assert.equal(answer.carries_effect_authority, false);
  assert.equal(answer.requires_human_review, true);

  const proposalEnvelope = db.json(0);
  const linkEnvelope = db.json(1);
  assert.equal(proposalEnvelope.record_kind, "stored_parsed_proposal");
  assert.equal(linkEnvelope.record_kind, "stored_proposal_link");
  assert.equal(proposalEnvelope.record.schema_version, V5_F01_PROPOSAL_SCHEMA_VERSION);
  assert.equal(linkEnvelope.record.reversible, true);
  assert.equal(linkEnvelope.record.history_preserved, true);
  assert.notEqual(proposalEnvelope.record_digest, linkEnvelope.record_digest,
    "the proposal and its link are two records, not one");

  // THE PRODUCER RULE, BOUND AUTOMATICALLY. A parsed proposal is derived from a
  // stored artifact, so the third envelope registers which original produced it
  // — built here from values the store already holds, with the caller supplying
  // nothing and able to withhold nothing.
  const derivativeEnvelope = db.json(2);
  assert.equal(derivativeEnvelope.record_kind, "stored_derivative_link");
  assert.equal(derivativeEnvelope.record.source_artifact_digest, PROPOSAL.artifact_digest);
  assert.equal(derivativeEnvelope.record.derivative_kind, "f01_parsed_proposal");
  assert.equal(derivativeEnvelope.record.derivative_id, proposalEnvelope.record_digest,
    "the derivative identity is the proposal's own digest");
  assert.equal(derivativeEnvelope.record.derivative_content_digest,
    proposalEnvelope.record_digest);
  assert.equal(derivativeEnvelope.record.producer_workflow, "f01_record_parsed_proposal");
  assert.equal(derivativeEnvelope.record.producer_run_ref, "syn-proposal-0001");
  assert.equal(derivativeEnvelope.record.produced_at, SERVER_NOW, "produced_at is the server's");
  assert.equal(derivativeEnvelope.record.registered_by, "joe");
  assert.equal(derivativeEnvelope.record_digest, digest(derivativeEnvelope.record));
  // Registering provenance establishes nothing and permits nothing, and the
  // stored record says so in the bytes it hashes to.
  assert.equal(derivativeEnvelope.record.establishes_coverage, false);
  assert.equal(derivativeEnvelope.record.is_exhaustive_inventory, false);
  assert.equal(derivativeEnvelope.record.permits_deletion, false);
  assert.equal(derivativeEnvelope.establishes_coverage, false);
  assert.equal(answer.derivative_registration_bound, true);
  assert.equal(answer.derivative_link_digest, derivativeEnvelope.record_digest);
  // The idempotency key travels to the writer AFTER the three envelopes.
  assert.equal(db.param(3), "syn-proposal-0001");
});

test("a proposal whose source registration cannot be bound writes nothing at all", async () => {
  // The stored artifact is loaded and its creation instant is real evidence: an
  // artifact recorded AFTER the server's own instant would make the derivative
  // precede its source, and a proposal recorded without a provenance edge is
  // exactly the untracked derivative the settled rule exists to prevent.
  const { db, store } = storeWith(policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: "2026-10-01T00:00:00Z" },
  }));
  const answer = await store.recordParsedProposal({
    idempotency_key: "syn-proposal-noprov-0001", proposal: PROPOSAL,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "production_precedes_source_artifact");
  assert.equal(answer.derivative_registration_bound, false);
  assert.equal(answer.records_written, 0);
  assert.equal(db.lastWrite, undefined, "neither the proposal nor its link reached the database");
});

test("a proposal naming an artifact the database does not hold refuses", async () => {
  const { db, store } = storeWith(policyScript({ stored_artifact: null }));
  const answer = await store.recordParsedProposal({
    idempotency_key: "syn-proposal-0002", proposal: PROPOSAL,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_artifact_reference");
  assert.equal(db.lastWrite, undefined, "a proposal cannot assert an artifact into existence");
});

test("a proposal that reaches past review into effect refuses by name", async () => {
  const { store } = storeWith(policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
  }));
  await refuses(store.recordParsedProposal({
    idempotency_key: "syn-proposal-0003",
    proposal: { ...PROPOSAL, apply_immediately: true },
  }, ctx(JOE)), "unknown_field");
});

// ===========================================================================
// register-derivative-source-link.
// ===========================================================================

const REGISTRATION = Object.freeze({
  source_artifact_digest: D(21),
  derivative_kind: "synthetic_test_abstract",
  derivative_id: "synthetic-abstract-0001",
  derivative_content_digest: D(22),
  producer_workflow: "synthetic_test_producer",
  producer_run_ref: "synthetic-run-0001",
  evidence_ref: "synthetic-evidence-0040",
  evidence_digest: D(23),
});

function registrationScript(extra = {}) {
  return {
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    ...extra,
  };
}

test("a producer registers one derivative against the artifact it came from", async () => {
  const { db, store } = storeWith(registrationScript());
  const answer = await store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-0001", registration: REGISTRATION,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "derivative_source_link_registered");
  assert.equal(answer.source_artifact_digest, D(21));
  assert.equal(answer.derivative_kind, "synthetic_test_abstract");
  assert.equal(answer.derivative_id, "synthetic-abstract-0001");
  assert.equal(answer.producer_workflow, "synthetic_test_producer");
  // WHAT A REGISTRATION IS NOT, said on the answer as well as in the record.
  assert.equal(answer.establishes_coverage, false);
  assert.equal(answer.is_exhaustive_inventory, false);
  assert.equal(answer.permits_deletion, false);
  assert.equal(answer.bytes_deleted, false);
  assert.equal(answer.effects.creates_effect, false);
  assert.equal(answer.provider_calls, 0);

  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_derivative_link");
  assert.equal(envelope.record_digest, digest(envelope.record));
  assert.equal(envelope.record.registered_by, "joe", "the producer principal is derived");
  assert.equal(envelope.record.registered_at, SERVER_NOW);
  assert.equal(envelope.record.produced_at, SERVER_NOW,
    "produced_at is the server's instant, not a producer's clock");
  assert.equal(envelope.record.registration_is_provenance, true);
  assert.equal(envelope.record.is_exhaustive_inventory, false);
  assert.equal(envelope.record.establishes_coverage, false);
  assert.equal(envelope.record.permits_deletion, false);
  assert.equal(db.param(1), "syn-derivative-0001");
});

test("a registration against an artifact the database does not hold refuses", async () => {
  const { db, store } = storeWith(registrationScript({ stored_artifact: null }));
  const answer = await store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-unknown-0001", registration: REGISTRATION,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_source_artifact");
  assert.equal(answer.records_written, 0);
  assert.equal(db.lastWrite, undefined, "naming a digest cannot conjure an artifact");
});

test("a registration that would predate or duplicate its source refuses", async () => {
  const late = storeWith(registrationScript({
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: "2026-10-01T00:00:00Z" },
  }));
  const early = await late.store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-early-0001", registration: REGISTRATION,
  }, ctx(JOE));
  assert.equal(early.decision, "refuse");
  assert.equal(early.reason_id, "production_precedes_source_artifact");
  assert.equal(late.db.lastWrite, undefined);

  const self = storeWith(registrationScript());
  const answer = await self.store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-self-0001",
    registration: { ...REGISTRATION, derivative_content_digest: D(21) },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "derivative_is_its_own_source");
  assert.equal(self.db.lastWrite, undefined);
});

test("the artifact's own CONTENT digest is loaded, and a byte-identical copy refuses", async () => {
  // THE COMPARISON THAT WAS MISSING. The store used to hand the kernel the stored
  // artifact's record digest and its creation instant and nothing else, so the
  // only self-source comparison available was against the RECORD identity — which
  // a verbatim copy never matches. Here the registration claims bytes identical to
  // the artifact's own, and it refuses.
  const copy = storeWith(registrationScript());
  const refused = await copy.store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-copy-0001",
    registration: { ...REGISTRATION, derivative_content_digest: SOURCE_CONTENT },
  }, ctx(JOE));
  assert.equal(refused.decision, "refuse");
  assert.equal(refused.reason_id, "derivative_is_its_own_source");
  assert.equal(refused.records_written, 0);
  assert.equal(copy.db.lastWrite, undefined, "a copy never reaches the database");

  // THE LOADED CONTENT DIGEST IS WHAT TRAVELS. Distinct derived bytes are still
  // registered, and the same load answers both questions.
  const distinct = storeWith(registrationScript());
  const allowed = await distinct.store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-distinct-0001", registration: REGISTRATION,
  }, ctx(JOE));
  assert.equal(allowed.decision, "allow");
  assert.equal(distinct.db.json(0).record.derivative_content_digest, D(22));
  const loadCall = distinct.db.calls.find(c => c.text.includes("ops.f01_stored_artifact("));
  assert.deepEqual(loadCall.params, [D(21)]);

  // A STORED ARTIFACT WITH NO READABLE CONTENT DIGEST IS REFUSED, NOT WAVED
  // THROUGH. ops.f01_corporate_artifact carries content_digest NOT NULL and
  // CHECK-bound to the hashed record, so a readback without one is a row the
  // database should never have produced — and treating it as "the comparison
  // could not be made, carry on" would make the guard optional exactly when
  // something is already wrong.
  const corrupt = storeWith(registrationScript({
    stored_artifact: { artifact_digest: D(21), artifact: {}, created_at: T.early },
  }));
  await refuses(corrupt.store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-corrupt-0001", registration: REGISTRATION,
  }, ctx(JOE)), "corrupt_stored_artifact");
  assert.equal(corrupt.db.lastWrite, undefined);

  // The parsed-proposal producer path loads the same way, so its own derivative
  // is judged against the artifact's bytes too.
  const proposal = storeWith(policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT },
      created_at: T.early },
  }));
  const recorded = await proposal.store.recordParsedProposal({
    idempotency_key: "syn-proposal-content-0001", proposal: PROPOSAL,
  }, ctx(JOE));
  assert.equal(recorded.decision, "allow");
  assert.notEqual(proposal.db.json(2).record.derivative_content_digest, SOURCE_CONTENT);
});

test("a caller may not register a kind this contract produces itself", async () => {
  // THE DENIAL SHAPE THIS CLOSES, and it is permanent rather than noisy. A parsed
  // proposal's derivative identity IS the proposal digest, computed from caller
  // payload plus the installed registry digest — so a caller can predict it. The
  // stored identity is unique per (tenant, kind, id) on an append-only table with
  // no release path, so a pre-registered ("f01_parsed_proposal", <predicted id>)
  // pointed at some other artifact would make the genuine proposal write conflict
  // for ever, and would leave a provenance edge asserting the proposal came from
  // an artifact it did not.
  const { db, store } = storeWith(registrationScript());
  const answer = await store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-reserved-0001",
    registration: {
      ...REGISTRATION,
      derivative_kind: V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
      derivative_id: D(24),
      derivative_content_digest: D(24),
    },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "reserved_derivative_kind");
  assert.equal(answer.derivative_kind, V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND);
  assert.deepEqual(answer.reserved_derivative_kinds, [...V5_F01_RESERVED_DERIVATIVE_KINDS]);
  assert.equal(answer.records_written, 0);
  assert.equal(answer.establishes_coverage, false);
  // BEFORE ANY STATEMENT RUNS, not merely before the writer: a refusal that
  // opened a transaction and claimed an idempotency key would burn the key on a
  // registration that can never be accepted.
  assert.equal(db.calls.length, 0, "a reserved kind is refused before the transaction");

  // AND THE PATH THAT LEGITIMATELY WRITES THE KIND IS UNTOUCHED. The proposal
  // producer builds its own link and reaches ops.f01_record_proposal, not this
  // surface, so reserving the kind here closes the caller route without closing
  // the producer route.
  const producer = storeWith(policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
  }));
  const recorded = await producer.store.recordParsedProposal({
    idempotency_key: "syn-proposal-reserved-0001", proposal: PROPOSAL,
  }, ctx(JOE));
  assert.equal(recorded.decision, "allow");
  assert.equal(producer.db.json(2).record.derivative_kind,
    V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND);
});

test("MUTATION KILL (registration): no caller field can forge trust, time or coverage", async () => {
  const { db, store } = storeWith(registrationScript());
  const call = extra => store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-forge-0001",
    registration: { ...REGISTRATION, ...extra },
  }, ctx(JOE));

  // THE PRODUCER IS THE AUTHENTICATED PRINCIPAL. There is no trusted flag, no
  // producer identity to claim, and no coverage to declare.
  await refuses(call({ registered_by: "joe" }), "caller_derived_field_refused");
  await refuses(call({ registered_at: SERVER_NOW }), "caller_derived_field_refused");
  await refuses(call({ produced_at: T.mid }), "caller_derived_field_refused");
  await refuses(call({ derivative_coverage: ESTABLISHED_COVERAGE }),
    "caller_derived_field_refused");
  await refuses(call({ derivative_coverage_state: "established" }),
    "caller_derived_field_refused");
  await refuses(call({ registered_derivative_kinds: ["synthetic_test_abstract"] }),
    "caller_derived_field_refused");
  await refuses(call({ tenant: ORGANIZATION_TENANT_ID }), "caller_derived_field_refused");
  await refuses(call({ trusted_caller: true }), "caller_authority_field_refused");
  await refuses(call({ authorized_by: "joe" }), "caller_authority_field_refused");
  await refuses(call({ establishes_coverage: true }), "unknown_field");
  await refuses(call({ permits_deletion: true }), "unknown_field");
  assert.equal(db.lastWrite, undefined, "not one forgery reached the database");

  // Every field of the registration is required: a partial provenance edge is
  // not a provenance edge.
  for (const key of Object.keys(REGISTRATION)) {
    await refuses(store.registerDerivativeSourceLink({
      idempotency_key: "syn-derivative-partial-0001",
      registration: Object.fromEntries(
        Object.entries(REGISTRATION).filter(([name]) => name !== key)),
    }, ctx(JOE)), "missing_field");
  }
});

test("the registration operation is an ordinary write, not an authority surface", () => {
  const schema = v5F01StoreOperationSchemas()["register-derivative-source-link"];
  assert.equal(schema.write, true);
  // Producers are workflows. Requiring a human here would mean Joe or Dell had
  // to approve every abstract the system makes, which is the exact opposite of
  // the settled decision.
  assert.equal(schema.humanOnly, false);
  assert.equal(schema.authorityOnly, false);
  assert.ok(schema.required.includes("idempotency_key"));
  const registration = v5F01ToolRegistrations()
    .find(entry => entry.name === "register-derivative-source-link");
  assert.equal(registration.handler, "registerDerivativeSourceLink");
  assert.equal(registration.accepted, false);
  assert.equal(registration.registered_in_scac, true);
});

// ===========================================================================
// record-document-identity.
// ===========================================================================

const DOCUMENT = Object.freeze({
  document_class: "synthetic_test_agreement",
  neon_identity: {
    document_id: "synthetic-document-0001", content_digest: VALUE_A, version_no: 1,
  },
  object_storage_identity: {
    object_key: "synthetic/test/object-0001", content_digest: VALUE_A,
    byte_length: 2048, sealed: true,
  },
  onedrive_identity: {
    drive_id: "synthetic-drive-0001", item_id: "synthetic-item-0001",
    content_digest: VALUE_A, filing_state: "filed",
  },
  preparation_state: "approved_for_delivery",
  delivery_state: "delivered",
  signature_state: "fully_executed",
  validity_state: "effective",
  version_state: "current",
});

// THE THREE HONEST ANSWERS, one fixture each. There is deliberately no fourth
// and no default: every call below states which of them is true, because a
// version recorded with no statement is exactly the untracked derivative the
// producer rule exists to prevent.
const ORIGINAL_SOURCE = Object.freeze({
  provenance_state: "original_first_party",
  basis_statement: "synthetic test fixture: authored in this record layer, no corporate original",
});

const LEGACY_SOURCE = Object.freeze({
  provenance_state: "legacy_provenance_unknown",
  basis_statement: "synthetic test fixture: imported before the rule, origin not known",
});

const DERIVED_SOURCE = Object.freeze({
  provenance_state: "derived_from_stored_artifact",
  source_artifact_digest: D(21),
  producer_workflow: "synthetic_test_document_producer",
  producer_run_ref: "synthetic-run-0002",
});

/** The scripted database for one document write. The artifact is LOADED. */
function documentScript(extra = {}) {
  return {
    document_current: null,
    document_provenance: null,
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    outcome: { outcome: "recorded" },
    ...extra,
  };
}

/**
 * The binding the store must reach, rebuilt here from the same inputs.
 *
 * INDEPENDENT ON PURPOSE. Comparing the store's envelopes against envelopes this
 * suite composed itself is what makes "the exact preimage travelled" checkable;
 * reading the store's own answer back would only prove it is self-consistent.
 */
function expectedComposition({ document = DOCUMENT, source = ORIGINAL_SOURCE,
  // The same three fields the store projects out of ops.f01_stored_artifact,
  // including the artifact's own CONTENT digest — the value the kernel needs to
  // tell a byte-identical copy from something derived.
  source_artifact = { artifact_digest: D(21), created_at: T.early,
    content_digest: SOURCE_CONTENT },
  prior_provenance = null, prior_document_digest = null } = {}) {
  return composeDocumentSourceEnvelopes(evaluateDocumentSourceBinding({
    tenant: ORGANIZATION_TENANT_ID,
    document,
    source,
    source_artifact: source.provenance_state === "derived_from_stored_artifact"
      ? source_artifact : undefined,
    prior_provenance,
    prior_document_digest,
    recorded_by: "joe",
    now: SERVER_NOW,
  }));
}

test("a coherent, fully filed document round-trips every axis and identity", async () => {
  const { db, store } = storeWith(documentScript());
  const answer = await store.recordDocumentIdentity({
    idempotency_key: "syn-document-0001", document: DOCUMENT, source: ORIGINAL_SOURCE,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.official_filing_state, "filed");
  assert.equal(answer.object_storage_success_implies_official_filing, false);
  assert.equal(answer.neon_success_implies_official_filing, false);
  // THE REASON NAMES WHICH ORIGIN WAS RECORDED, taken from the committed write.
  // "Authored here" is a claim; the answer says so rather than reporting the
  // document's coherence and leaving the origin to be inferred.
  assert.equal(answer.reason_id, "document_declared_original_no_source_artifact");

  const envelope = db.json(0);
  const record = envelope.record;
  assert.equal(record.preparation_state, "approved_for_delivery");
  assert.equal(record.delivery_state, "delivered");
  assert.equal(record.signature_state, "fully_executed");
  assert.equal(record.validity_state, "effective");
  assert.equal(record.version_state, "current");
  assert.deepEqual(record.neon_identity, DOCUMENT.neon_identity);
  assert.deepEqual(record.object_storage_identity, DOCUMENT.object_storage_identity);
  assert.deepEqual(record.onedrive_identity, DOCUMENT.onedrive_identity);
  assert.equal(record.homes.official_executed_copy, "onedrive");
  assert.equal(record.homes.working_and_sealed_bytes, "object_storage");
  assert.equal(record.homes.identity_and_state, "neon_record_layer");
  assert.equal(envelope.record_digest, digest(record));

  // THE WRITER TAKES SIX ARGUMENTS AND THE SECOND IS NOT OPTIONAL. An original
  // writes the version and its statement; it writes NO link, because a link row
  // for a document with no source would be provenance pointing at nothing.
  assert.equal(db.write().params.length, 6);
  const provenance = db.json(1);
  assert.equal(db.json(2), null, "an original registers no derivative link");
  assert.equal(provenance.record_kind, "stored_document_source_provenance");
  assert.equal(provenance.record.provenance_state, "original_first_party");
  assert.equal(provenance.record.document_id, "synthetic-document-0001");
  assert.equal(provenance.record.version_no, 1);
  assert.equal(provenance.record.document_digest, envelope.record_digest,
    "the statement names the exact bytes the version was stored as");
  assert.equal(provenance.record.source_artifact_digest, null);
  assert.equal(provenance.record.derivative_link_digest, null);
  assert.equal(provenance.record.basis_statement, ORIGINAL_SOURCE.basis_statement);
  assert.equal(provenance.record.recorded_by, "joe", "the principal is derived");
  assert.equal(provenance.record.recorded_at, SERVER_NOW, "the instant is the server's");
  assert.equal(provenance.record_digest, digest(provenance.record));
  // The six claims a statement makes about itself, in its own hashed bytes.
  assert.equal(provenance.record.registration_is_provenance, true);
  assert.equal(provenance.record.establishes_coverage, false);
  assert.equal(provenance.record.is_exhaustive_inventory, false);
  assert.equal(provenance.record.permits_deletion, false);
  assert.equal(provenance.record.source_artifact_inferred_from_document_bytes, false);
  assert.equal(provenance.record.source_artifact_inferred_from_onedrive_identity, false);
  assert.equal(provenance.establishes_coverage, false);

  // BYTE FOR BYTE, against a composition this suite built independently.
  const expected = expectedComposition();
  assert.equal(canonicalJson(envelope), canonicalJson(expected.document_envelope));
  assert.equal(canonicalJson(provenance), canonicalJson(expected.provenance_envelope));
  assert.equal(expected.derivative_link_required, false);

  assert.equal(answer.provenance_state, "original_first_party");
  assert.equal(answer.provenance_digest, provenance.record_digest);
  assert.equal(answer.derivative_link_digest, null);
  assert.equal(answer.derivative_registration_bound, false);
  assert.equal(answer.absent_statement_means_no_source, false);
  assert.equal(answer.establishes_coverage, false);
  assert.equal(answer.permits_deletion, false);
  assert.equal(db.param(3), null, "the CAS operand for a first version is null");
  assert.equal(db.param(4), "syn-document-0001");
});

test("a derived document version writes version, statement and link in one call", async () => {
  const { db, store } = storeWith(documentScript());
  const answer = await store.recordDocumentIdentity({
    idempotency_key: "syn-document-derived-0001", document: DOCUMENT, source: DERIVED_SOURCE,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "document_source_binding_registered");
  assert.equal(answer.provenance_state, "derived_from_stored_artifact");
  assert.equal(answer.derivative_registration_bound, true);

  assert.equal(db.write().params.length, 6);
  const version = db.json(0);
  const provenance = db.json(1);
  const link = db.json(2);
  assert.equal(version.record_kind, "stored_document_version");
  assert.equal(provenance.record_kind, "stored_document_source_provenance");
  assert.equal(link.record_kind, "stored_derivative_link");

  // THE THREE RECORDS NAME EACH OTHER BY DIGEST, which is what makes the set
  // atomic rather than merely simultaneous.
  assert.equal(provenance.record.document_digest, version.record_digest);
  assert.equal(provenance.record.derivative_link_digest, link.record_digest);
  assert.equal(link.record.derivative_content_digest, version.record_digest);
  assert.equal(link.record.evidence_digest, version.record_digest);
  assert.equal(link.record.evidence_ref, "stored_document_version");
  assert.equal(link.record.derivative_kind, V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND);
  assert.equal(link.record.derivative_id, "synthetic-document-0001:1",
    "the derivative identity is the (document_id, version_no) fold");
  assert.equal(link.record.source_artifact_digest, D(21));
  assert.equal(link.record.producer_workflow, "synthetic_test_document_producer");
  assert.equal(link.record.producer_run_ref, "synthetic-run-0002");
  assert.equal(link.record.produced_at, SERVER_NOW,
    "produced_at is the server's instant, never a producer's clock");
  assert.equal(link.record.registered_by, "joe");
  assert.equal(link.record.establishes_coverage, false);
  assert.equal(link.record.is_exhaustive_inventory, false);
  assert.equal(link.record.permits_deletion, false);

  const expected = expectedComposition({ source: DERIVED_SOURCE });
  assert.equal(canonicalJson(version), canonicalJson(expected.document_envelope));
  assert.equal(canonicalJson(provenance), canonicalJson(expected.provenance_envelope));
  assert.equal(canonicalJson(link), canonicalJson(expected.derivative_envelope));
  assert.equal(expected.derivative_link_required, true);

  assert.equal(answer.provenance_digest, provenance.record_digest);
  assert.equal(answer.derivative_link_digest, link.record_digest);
  // Registering where one version came from establishes nothing.
  assert.equal(answer.establishes_coverage, false);
  assert.equal(answer.is_exhaustive_inventory, false);
  assert.equal(answer.permits_deletion, false);
  assert.equal(answer.absent_statement_means_no_source, false);
});

test("the source artifact is LOADED: forged, absent and unavailable each refuse with no write",
  async () => {
    // LOADED, and by the identity the DATABASE holds. The store hands the kernel
    // the stored artifact's own digest and creation instant; a caller states a
    // reference and nothing else.
    const loaded = storeWith(documentScript());
    await loaded.store.recordDocumentIdentity({
      idempotency_key: "syn-document-loaded-0001", document: DOCUMENT, source: DERIVED_SOURCE,
    }, ctx(JOE));
    const loadCall = loaded.db.calls.find(c => c.text.includes("ops.f01_stored_artifact("));
    assert.deepEqual(loadCall.params, [D(21)], "the named artifact is resolved, not asserted");

    // ABSENT: naming a digest cannot bring an artifact into existence.
    const absent = storeWith(documentScript({ stored_artifact: null }));
    const unknown = await absent.store.recordDocumentIdentity({
      idempotency_key: "syn-document-unknown-0001", document: DOCUMENT, source: DERIVED_SOURCE,
    }, ctx(JOE));
    assert.equal(unknown.decision, "refuse");
    assert.equal(unknown.reason_id, "unknown_source_artifact");
    assert.equal(unknown.records_written, 0);
    assert.equal(unknown.derivative_registration_bound, false);
    assert.equal(absent.db.lastWrite, undefined, "no version, statement or link was written");

    // FORGED: the stored artifact is not the one the declaration names.
    const other = storeWith(documentScript({
      stored_artifact: { artifact_digest: D(29), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    }));
    const mismatch = await other.store.recordDocumentIdentity({
      idempotency_key: "syn-document-mismatch-0001", document: DOCUMENT, source: DERIVED_SOURCE,
    }, ctx(JOE));
    assert.equal(mismatch.decision, "refuse");
    assert.equal(mismatch.reason_id, "source_artifact_mismatch");
    assert.equal(other.db.lastWrite, undefined);

    // A PRODUCTION THAT PRECEDES ITS SOURCE describes something else entirely.
    const late = storeWith(documentScript({
      stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: "2026-10-01T00:00:00Z" },
    }));
    const early = await late.store.recordDocumentIdentity({
      idempotency_key: "syn-document-early-0001", document: DOCUMENT, source: DERIVED_SOURCE,
    }, ctx(JOE));
    assert.equal(early.decision, "refuse");
    assert.equal(early.reason_id, "production_precedes_source_artifact");
    assert.equal(late.db.lastWrite, undefined);

    // AND THE DOCUMENT'S OWN BYTES ARE NOT ITS ORIGIN. VALUE_A is this document's
    // Neon, object-storage and OneDrive content digest; naming it as the source
    // artifact is the exact inference this seam exists to make impossible.
    const ownBytes = storeWith(documentScript({
      stored_artifact: { artifact_digest: VALUE_A, artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    }));
    const inferred = await ownBytes.store.recordDocumentIdentity({
      idempotency_key: "syn-document-ownbytes-0001", document: DOCUMENT,
      source: { ...DERIVED_SOURCE, source_artifact_digest: VALUE_A },
    }, ctx(JOE));
    assert.equal(inferred.decision, "refuse");
    assert.equal(inferred.reason_id, "document_bytes_are_not_a_source_artifact");
    assert.equal(inferred.offending_field, "document.neon_identity.content_digest");
    assert.equal(ownBytes.db.lastWrite, undefined);
  });

test("a source declaration is required, closed, and refuses derived-value injection", async () => {
  const { db, store } = storeWith(documentScript());
  // REQUIRED. This is a knowing break with the contract this operation shipped
  // with: there is no default, because every default would be a manufactured
  // provenance claim.
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-nosource-0001", document: DOCUMENT,
  }, ctx(JOE)), "missing_field");

  // Exactly three states, and nothing that merely resembles one.
  assert.deepEqual([...V5_F01_DOCUMENT_PROVENANCE_STATES].sort(),
    ["derived_from_stored_artifact", "legacy_provenance_unknown", "original_first_party"]);
  for (const state of ["derived", "original", "unknown", "probably_derived", ""]) {
    await refuses(store.recordDocumentIdentity({
      idempotency_key: "syn-document-badstate-0001", document: DOCUMENT,
      source: { provenance_state: state, basis_statement: "synthetic" },
    }, ctx(JOE)), "unknown_document_provenance_state");
  }
  // A non-derived declaration states its basis, and may not name an origin.
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-nobasis-0001", document: DOCUMENT,
    source: { provenance_state: "original_first_party" },
  }, ctx(JOE)), "missing_field");
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-injected-0001", document: DOCUMENT,
    source: { ...ORIGINAL_SOURCE, recorded_at: SERVER_NOW },
  }, ctx(JOE)), "caller_derived_field_refused");
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-injected-0002", document: DOCUMENT,
    source: { ...DERIVED_SOURCE, derivative_link_digest: D(33) },
  }, ctx(JOE)), "caller_derived_field_refused");
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-injected-0003", document: DOCUMENT,
    source: { ...DERIVED_SOURCE, produced_at: T.mid },
  }, ctx(JOE)), "caller_derived_field_refused");
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-injected-0004", document: DOCUMENT,
    source: { ...DERIVED_SOURCE, trusted_caller: true },
  }, ctx(JOE)), "caller_authority_field_refused");
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-injected-0005", document: DOCUMENT,
    source: { ...ORIGINAL_SOURCE, evidence_ref: "somewhere-else" },
  }, ctx(JOE)), "unknown_field");
  // And the payload-level derived guard refuses the two digests the seam adds.
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-injected-0006", document: DOCUMENT,
    source: ORIGINAL_SOURCE, provenance_digest: D(34),
  }, ctx(JOE)), "caller_derived_field_refused");
  assert.equal(db.calls.length, 0, "not one malformed declaration opened a transaction");
});

test("an ORIGINAL that names a source, and a LEGACY import, are different records", async () => {
  // "Original" carrying an origin is not one declaration, it is two contradictory
  // ones, and it refuses rather than being read as either.
  const contradictory = storeWith(documentScript());
  const refused = await contradictory.store.recordDocumentIdentity({
    idempotency_key: "syn-document-original-source-0001", document: DOCUMENT,
    source: { ...ORIGINAL_SOURCE, source_artifact_digest: D(21) },
  }, ctx(JOE));
  assert.equal(refused.decision, "refuse");
  assert.equal(refused.reason_id, "source_named_by_original_document");
  assert.equal(contradictory.db.lastWrite, undefined);

  // UNKNOWN IS RECORDABLE, AND IS NEVER UPGRADED TO "ORIGINAL". A truthful import
  // states that nobody knows where it came from, and the stored bytes say so.
  const legacy = storeWith(documentScript());
  const answer = await legacy.store.recordDocumentIdentity({
    idempotency_key: "syn-document-legacy-0001", document: DOCUMENT, source: LEGACY_SOURCE,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "document_declared_legacy_provenance_unknown");
  assert.equal(answer.provenance_state, "legacy_provenance_unknown");
  assert.equal(legacy.db.json(1).record.provenance_state, "legacy_provenance_unknown");
  assert.equal(legacy.db.json(1).record.basis_statement, LEGACY_SOURCE.basis_statement);
  assert.equal(legacy.db.json(2), null, "an unknown origin registers no link either");
  assert.equal(answer.derivative_registration_bound, false);
  assert.equal(answer.absent_statement_means_no_source, false);
});

test("the answer's reason is the ORIGIN that was committed, and an unregistered one fails closed",
  async () => {
    // THREE STATES, THREE REASONS, ALL DIFFERENT. Collapsing them into one
    // coherence reason is what made a truthful legacy import read back exactly
    // like a claim of first-party authorship — the distinction the record layer
    // had just gone to the trouble of storing, thrown away at the last step.
    const answers = {};
    for (const [label, source] of [
      ["derived", DERIVED_SOURCE], ["original", ORIGINAL_SOURCE], ["legacy", LEGACY_SOURCE],
    ]) {
      const { store } = storeWith(documentScript());
      answers[label] = await store.recordDocumentIdentity({
        idempotency_key: `syn-document-reason-${label}`, document: DOCUMENT, source,
      }, ctx(JOE));
      assert.equal(answers[label].decision, "allow", label);
    }
    assert.equal(answers.derived.reason_id, "document_source_binding_registered");
    assert.equal(answers.original.reason_id, "document_declared_original_no_source_artifact");
    assert.equal(answers.legacy.reason_id, "document_declared_legacy_provenance_unknown");
    assert.equal(new Set(Object.values(answers).map(a => a.reason_id)).size, 3);
    // Every one of the three states is registered as a reason: a fourth state
    // added to the vocabulary without a reason here is a failure rather than a
    // document reported under somebody else's origin.
    assert.deepEqual([...V5_F01_DOCUMENT_PROVENANCE_STATES].sort(),
      Object.values(answers).map(a => a.provenance_state).sort());

    // AND THE COMMITTED STATE IS WHAT DECIDES. A writer that returned a state
    // this contract does not register is a database that did not write what the
    // store asked it to, so the answer refuses rather than reporting the document
    // as recorded under an invented reason.
    const forged = storeWith(documentScript());
    await forged.store.recordDocumentIdentity({
      idempotency_key: "syn-document-reason-committed", document: DOCUMENT,
      source: ORIGINAL_SOURCE,
    }, ctx(JOE));
    const replay = storeWith({ replay_outcome: {
      ...forged.db.lastOutcome, provenance_state: "probably_original",
    } });
    await refuses(replay.store.recordDocumentIdentity({
      idempotency_key: "syn-document-reason-committed", document: DOCUMENT,
      source: ORIGINAL_SOURCE,
    }, ctx(JOE)), "invalid_stored_outcome");

    // A filing that is visibly incomplete keeps ITS reason, because that refusal
    // is about the OneDrive copy rather than about where the version came from.
    const { store } = storeWith(documentScript());
    const incomplete = await store.recordDocumentIdentity({
      idempotency_key: "syn-document-reason-incomplete",
      document: { ...DOCUMENT, onedrive_identity: null }, source: LEGACY_SOURCE,
    }, ctx(JOE));
    assert.equal(incomplete.decision, "refuse");
    assert.equal(incomplete.reason_id, "incomplete_official_filing");
    assert.equal(incomplete.provenance_state, "legacy_provenance_unknown");
  });

test("the prior statement is loaded for the EXACT version and a rewrite refuses", async () => {
  const composed = expectedComposition({ source: DERIVED_SOURCE });
  const priorForThisVersion = {
    document_id: "synthetic-document-0001",
    version_no: 1,
    document_digest: composed.document_digest,
    provenance_state: "derived_from_stored_artifact",
    source_artifact_digest: D(21),
    derivative_link_digest: composed.derivative_link_digest,
    // The reader returns more than the decision may use; the store projects.
    provenance_digest: composed.provenance_record_digest,
    establishes_coverage: false,
    integrity: "recomputed_from_committed_row",
  };

  // THE LOOKUP IS BY (document_id, version_no), not by document. A statement is
  // about one version, and only that row can show a rewrite of it.
  const same = storeWith(documentScript({ document_provenance: priorForThisVersion }));
  const repeat = await same.store.recordDocumentIdentity({
    idempotency_key: "syn-document-prior-0001", document: DOCUMENT, source: DERIVED_SOURCE,
  }, ctx(JOE));
  const readCall = same.db.calls.find(c => c.text.includes("ops.f01_document_version_source("));
  assert.deepEqual(readCall.params, ["synthetic-document-0001", 1]);
  assert.equal(repeat.decision, "allow", "the same statement about the same version agrees");

  // A DIFFERENT SOURCE for the same version is a rewrite of where it came from.
  const repointed = storeWith(documentScript({
    document_provenance: { ...priorForThisVersion, source_artifact_digest: D(28) },
  }));
  const rewrite = await repointed.store.recordDocumentIdentity({
    idempotency_key: "syn-document-prior-0002", document: DOCUMENT, source: DERIVED_SOURCE,
  }, ctx(JOE));
  assert.equal(rewrite.decision, "refuse");
  assert.equal(rewrite.reason_id, "document_source_repointing_refused");
  assert.equal(repointed.db.lastWrite, undefined);

  // A DIFFERENT STATE for the same version is a rewrite too.
  const restated = storeWith(documentScript({
    document_provenance: { ...priorForThisVersion,
      provenance_state: "original_first_party",
      source_artifact_digest: null, derivative_link_digest: null },
  }));
  const changed = await restated.store.recordDocumentIdentity({
    idempotency_key: "syn-document-prior-0003", document: DOCUMENT, source: DERIVED_SOURCE,
  }, ctx(JOE));
  assert.equal(changed.decision, "refuse");
  assert.equal(changed.reason_id, "document_provenance_state_rebinding_refused");
  assert.equal(restated.db.lastWrite, undefined);

  // AND A LATER, DIFFERENT ARTIFACT IS NOT A REWRITE. A second VERSION of the
  // same document may name a different original; the statement is per version.
  const nextVersion = {
    ...DOCUMENT,
    neon_identity: { ...DOCUMENT.neon_identity, version_no: 2 },
  };
  const advanced = storeWith(documentScript({ document_provenance: null }));
  const second = await advanced.store.recordDocumentIdentity({
    idempotency_key: "syn-document-prior-0004", document: nextVersion,
    source: { ...DERIVED_SOURCE, source_artifact_digest: D(21) },
  }, ctx(JOE));
  assert.equal(second.decision, "allow");
  assert.deepEqual(
    advanced.db.calls.find(c => c.text.includes("ops.f01_document_version_source(")).params,
    ["synthetic-document-0001", 2]);
  assert.equal(advanced.db.json(2).record.derivative_id, "synthetic-document-0001:2");
});

test("full execution without a filed OneDrive copy is STORED as visibly incomplete", async () => {
  for (const onedrive of [
    null,
    { drive_id: "synthetic-drive-0001", item_id: "synthetic-item-0001",
      content_digest: VALUE_A, filing_state: "pending" },
    { drive_id: "synthetic-drive-0001", item_id: "synthetic-item-0001",
      content_digest: VALUE_A, filing_state: "failed" },
  ]) {
    const { db, store } = storeWith(documentScript());
    const answer = await store.recordDocumentIdentity({
      idempotency_key: `syn-document-incomplete-${onedrive?.filing_state ?? "absent"}`,
      document: { ...DOCUMENT, onedrive_identity: onedrive },
      source: ORIGINAL_SOURCE,
    }, ctx(JOE));
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.reason_id, "incomplete_official_filing");
    assert.equal(answer.official_filing_state, "incomplete_official_filing");
    // The incompleteness is a RECORD, not an absence. An object-storage success
    // beside it must never read as an official filing.
    assert.equal(db.write().fn, "f01_record_document");
    assert.equal(db.json(0).record.official_filing_state, "incomplete_official_filing");
    assert.equal(db.json(0).object_storage_success_implies_official_filing, false);
    // ...and it is recorded WITH its statement, like every other version. A
    // visibly incomplete filing is still a document that came from somewhere.
    assert.equal(db.json(1).record.provenance_state, "original_first_party");
    assert.equal(db.json(1).record.document_digest, db.json(0).record_digest);
  }
});

test("an incoherent document writes nothing at all", async () => {
  const { db, store } = storeWith({ document_current: null });
  const answer = await store.recordDocumentIdentity({
    idempotency_key: "syn-document-incoherent-0001",
    document: { ...DOCUMENT, delivery_state: "undelivered" },
    source: ORIGINAL_SOURCE,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "document_state_incoherent");
  assert.equal(answer.violated_constraint, "signature_requires_delivery");
  assert.equal(db.calls.length, 0, "an incoherent document never opens a transaction");
});

test("a sealed-bytes digest that disagrees with Neon refuses rather than picking a winner", async () => {
  const { db, store } = storeWith({ document_current: null });
  const answer = await store.recordDocumentIdentity({
    idempotency_key: "syn-document-sealed-0001",
    document: {
      ...DOCUMENT,
      object_storage_identity: { ...DOCUMENT.object_storage_identity, content_digest: VALUE_B },
    },
    source: ORIGINAL_SOURCE,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "sealed_bytes_digest_mismatch");
  assert.equal(db.calls.length, 0);
});

test("a document write against a stale current version refuses", async () => {
  const { db, store } = storeWith({ document_current: { record_digest: D(31) } });
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-stale-0001",
    document: DOCUMENT,
    source: ORIGINAL_SOURCE,
    expected_prior_document_digest: D(32),
  }, ctx(JOE)), "stale_document_digest");
  assert.equal(db.lastWrite, undefined);
  // The CAS is decided BEFORE the statement is loaded or the artifact resolved:
  // a write that cannot land must not go looking for provenance to attach to it.
  assert.equal(db.calls.some(c => c.text.includes("ops.f01_document_version_source(")), false);
});

test("a document derivative kind cannot be registered through the public surface", async () => {
  // THE SECOND RESERVED KIND, and it is reserved for the same permanent reason
  // the first is. A document version's derivative identity is the
  // (document_id, version_no) fold, which a caller can predict exactly; the ops
  // identity index is unique per (tenant, kind, id) over an append-only table
  // with no release path. One pre-registration pointed at another artifact would
  // make the genuine document write conflict for that version for ever.
  const { db, store } = storeWith(registrationScript());
  const answer = await store.registerDerivativeSourceLink({
    idempotency_key: "syn-derivative-document-0001",
    registration: {
      ...REGISTRATION,
      derivative_kind: V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND,
      derivative_id: "synthetic-document-0001:1",
      derivative_content_digest: D(25),
    },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "reserved_derivative_kind");
  assert.equal(answer.derivative_kind, V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND);
  assert.ok(answer.reserved_derivative_kinds.includes(V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND));
  assert.equal(answer.records_written, 0);
  // BEFORE ANY STATEMENT RUNS: a refusal that opened a transaction would burn the
  // idempotency key on a registration that can never be accepted.
  assert.equal(db.calls.length, 0);

  // AND THE PRODUCER ROUTE IS UNTOUCHED: the document writer builds the same kind
  // itself, in the transaction that completes the version.
  const producer = storeWith(documentScript());
  const recorded = await producer.store.recordDocumentIdentity({
    idempotency_key: "syn-document-producer-0001", document: DOCUMENT, source: DERIVED_SOURCE,
  }, ctx(JOE));
  assert.equal(recorded.decision, "allow");
  assert.equal(producer.db.json(2).record.derivative_kind,
    V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND);
});

test("RE-APPLYING domain.sql DROPS the four-argument document writer rather than granting it",
  async () => {
    // WHAT THIS CATCHES, AND WHY IT IS TEXT RATHER THAN A FIXTURE. domain.sql still
    // CREATE OR REPLACEs the FOUR-argument ops.f01_record_document, because it has
    // to stay installable standing alone. The document-source hunk DROPs that
    // overload and installs the six-argument writer in its place. So applying
    // domain.sql a SECOND time, onto a database that already carries the hunk,
    // re-creates the old writer beside the new one — and domain.sql's grant loop
    // iterates over whatever ops.f01_% functions EXIST rather than over a written
    // list, so it would then GRANT EXECUTE to carr_writer and both authority logins
    // on a path that completes a DERIVED document with no provenance edge. That is
    // the exact hole the replacement exists to close.
    //
    // NO SQL FIXTURE SEES IT: the fixtures apply each file once, in order, so the
    // second apply is the case nobody runs. The evidence available to a Node suite
    // is the schema text, and the thing worth proving in it is not that a drop
    // exists but that it names the RIGHT TWO SIGNATURES and sits in the RIGHT
    // PLACE. A guard that tested for the four-argument form, or dropped the
    // six-argument one, or ran after the grants, would read as a fix and be none.
    const domainSql = readFileSync(new URL("../../domain.sql", import.meta.url), "utf8");

    // THE STORE'S OWN STATEMENT IS THE AUTHORITY on what the successor's signature
    // is, so the guard is compared against the call the persistence layer actually
    // makes — not against a second hand-written copy of the same string, which
    // would drift with it.
    const { db, store } = storeWith(documentScript());
    const recorded = await store.recordDocumentIdentity({
      idempotency_key: "syn-document-overload-0001", document: DOCUMENT, source: DERIVED_SOURCE,
    }, ctx(JOE));
    assert.equal(recorded.decision, "allow");
    const writerCall = db.calls.find(c => c.text.includes("ops.f01_record_document("));
    assert.ok(writerCall, "the document write reached the writer");
    const successorArgs = [...writerCall.text.matchAll(/\$\d+::(\w+)/g)].map(m => m[1]);
    assert.deepEqual(successorArgs, ["jsonb", "jsonb", "jsonb", "text", "text", "text"],
      "the store calls the six-argument writer");

    // (1) THE OLD CORE IS STILL THERE, ONCE. This correction is packaging: it does
    // not fold the hunk into domain.sql and does not install a second writer.
    const created = [...domainSql.matchAll(
      /CREATE OR REPLACE FUNCTION ops\.f01_record_document\(([^)]*)\)/g)];
    assert.equal(created.length, 1, "domain.sql defines the document writer exactly once");
    assert.deepEqual(
      created[0][1].split(",").map(a => a.trim().split(/\s+/).pop()),
      ["jsonb", "text", "text", "text"],
      "and what it defines is still the standalone four-argument core");

    // (2) THE GUARD IS THE FILE'S ONLY DROP OF THIS WRITER, and it names the EXACT
    // four-argument overload. A drop with no argument list, or with the successor's,
    // would take out the writer the store depends on.
    const guardStart = domainSql.indexOf("DO $document_writer_overload$");
    const guardEnd = domainSql.indexOf("$document_writer_overload$;", guardStart);
    assert.ok(guardStart !== -1 && guardEnd > guardStart, "the overload guard is present");
    const guard = domainSql.slice(guardStart, guardEnd);
    const drops = [...domainSql.matchAll(
      /DROP FUNCTION(?: IF EXISTS)? ops\.f01_record_document\s*\(([^)]*)\)/g)];
    assert.equal(drops.length, 1, "exactly one drop of the document writer in the whole file");
    assert.deepEqual(drops[0][1].split(",").map(a => a.trim()),
      ["jsonb", "text", "text", "text"],
      "the drop names the four-argument overload and no other signature");
    assert.ok(drops[0].index > guardStart && drops[0].index < guardEnd,
      "and that drop is the guarded one, not a bare statement elsewhere");

    // (3) THE CONDITION IS THE SUCCESSOR'S EXISTENCE, CHECKED BY EXACT SIGNATURE.
    // Checking by NAME alone would drop the overload on a database that has only
    // the old core, leaving no document writer at all.
    const condition = guard.match(
      /to_regprocedure\('ops\.f01_record_document\(([^)]*)\)'\)\s*IS NOT NULL/);
    assert.ok(condition, "the guard asks whether the successor exists, by signature");
    assert.deepEqual(condition[1].split(",").map(a => a.trim()), successorArgs,
      "the guard checks the EXACT signature the store calls");
    assert.ok(guard.indexOf(condition[0]) < guard.indexOf("DROP FUNCTION"),
      "the drop is reached only through that check");
    assert.ok(guard.indexOf("DROP FUNCTION") < guard.indexOf("END IF"),
      "the drop sits inside the IF rather than after it");

    // (4) AND IT RUNS AFTER THE DEFINITION IT REMOVES AND BEFORE THE GRANT LOOP.
    // After the grants it would revoke nothing already given; before the CREATE OR
    // REPLACE it would drop the overload and then put it straight back.
    const grantLoop = domainSql.indexOf("DO $grants$");
    assert.ok(grantLoop !== -1, "the grant loop is where it was");
    assert.ok(created[0].index < guardStart,
      "the guard runs after the writer it removes is re-created");
    assert.ok(guardEnd < grantLoop,
      "and before the loop that would otherwise grant EXECUTE on it");

    // (5) THE GUARD WIDENS NOTHING. It creates no object and grants no authority;
    // removing a function this file itself just re-created is all it does.
    assert.equal(/\b(GRANT|REVOKE|CREATE)\b/.test(guard), false,
      "the guard grants nothing and installs nothing");
  });

test("BOTH IMPORT ORDERS LOAD: the store/document-source cycle has no module-scope read",
  async () => {
    // WHY THIS IS TWO PROCESSES. ESM evaluates a module graph once per process, so
    // the order is fixed by whichever specifier is reached first; a second
    // dynamic import in the same process reuses the already-evaluated instances
    // and proves nothing. These two modules import each other, so the order
    // decides which one is mid-evaluation when the other's body runs.
    //
    // THE FAILURE THIS CATCHES IS TOTAL, NOT SUBTLE. If the document-source
    // module reads V5_F01_DERIVED_ONLY_FIELDS or V5_F01_STORE_RECORD_KINDS at
    // MODULE SCOPE, then importing the store first evaluates that read against a
    // `const` still in its temporal dead zone and throws ReferenceError before a
    // single test — or a single handler — can run. Anything that imports the
    // store first, which is every consumer of the persistence tail, fails at load.
    const url = name => new URL(`../src/${name}`, import.meta.url).href;
    const orders = {
      "store-first": ["record-source-authority-store.v5.js",
        "document-derivative-registration.v5.js"],
      "module-first": ["document-derivative-registration.v5.js",
        "record-source-authority-store.v5.js"],
    };
    for (const [order, [first, second]] of Object.entries(orders)) {
      const script = `await import(${JSON.stringify(url(first))});\n` +
        `await import(${JSON.stringify(url(second))});\n` +
        `process.stdout.write("loaded");`;
      let output;
      try {
        output = execFileSync(process.execPath, ["--input-type=module", "-e", script],
          { encoding: "utf8", timeout: 30000, stdio: ["ignore", "pipe", "pipe"] });
      } catch (error) {
        assert.fail(`${order} import failed: ${error.stderr || error.message}`);
      }
      assert.equal(output.trim(), "loaded", order);
    }
  });

// ===========================================================================
// record-artifact-preservation-hold.
// ===========================================================================

test("a hold is appended, never edited, and never deletes the artifact", async () => {
  const { db, store } = storeWith({
    stored_artifact: { artifact_digest: D(41), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    hold_history: [], outcome: { outcome: "appended" },
  });
  const answer = await store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-active-0001",
    hold: {
      hold_id: "synthetic-hold-0001", artifact_digest: D(41),
      state: "active", reason: "synthetic test hold",
    },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.deletes_artifact, false);
  assert.equal(answer.append_only, true);
  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_preservation_hold");
  assert.equal(envelope.deletes_artifact, false);
  assert.equal(envelope.record.state, "active");
  assert.equal(envelope.record.placed_at, SERVER_NOW, "placed_at comes from the server");
  assert.equal(envelope.record.released_at, null);
  assert.equal(envelope.record.prior_hold_digest, null);
  assert.equal(envelope.record.recorded_by, "joe");
});

test("a release carries the stored placed_at forward and names the state it replaces", async () => {
  const priorRecord = storedHoldRecord({
    hold_id: "synthetic-hold-0001", artifact_digest: D(41), state: "active",
    reason: "synthetic test hold", placed_at: T.early, released_at: null,
    prior_hold_digest: null, recorded_by: "joe", recorded_at: T.early,
  });
  const priorDigest = digest(priorRecord);
  const { db, store } = storeWith({
    stored_artifact: { artifact_digest: D(41), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    hold_history: [{ record: priorRecord, record_digest: priorDigest }],
    outcome: { outcome: "appended" },
  });
  const answer = await store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-release-0001",
    hold: { hold_id: "synthetic-hold-0001", artifact_digest: D(41), state: "released" },
    expected_prior_hold_digest: priorDigest,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  const record = db.json(0).record;
  assert.equal(record.state, "released");
  assert.equal(record.placed_at, T.early, "placed_at is loaded, not restated by the releaser");
  assert.equal(record.released_at, SERVER_NOW);
  assert.equal(record.prior_hold_digest, priorDigest);
  assert.equal(db.param(1), priorDigest, "the CAS operand travels to the database");
});

test("a hold append against a stale prior state refuses", async () => {
  const { db, store } = storeWith({
    stored_artifact: { artifact_digest: D(41), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    hold_history: [{ record: { artifact_digest: D(41) }, record_digest: D(42) }],
  });
  await refuses(store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-stale-0001",
    hold: { hold_id: "synthetic-hold-0001", artifact_digest: D(41), state: "released" },
    expected_prior_hold_digest: D(43),
  }, ctx(JOE)), "stale_hold_digest");
  assert.equal(db.lastWrite, undefined);
});

test("a hold on an artifact the database does not hold refuses", async () => {
  const { db, store } = storeWith({ stored_artifact: null });
  const answer = await store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-unknown-0001",
    hold: { hold_id: "synthetic-hold-0002", artifact_digest: D(44), state: "active" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_artifact_reference");
  assert.equal(db.lastWrite, undefined);
});

test("all four hold states are writable and an unregistered one refuses", async () => {
  for (const state of ["active", "released", "expired", "unknown"]) {
    const { db, store } = storeWith({
      stored_artifact: { artifact_digest: D(41), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
      hold_history: [], outcome: { outcome: "appended" },
    });
    const answer = await store.recordArtifactPreservationHold({
      idempotency_key: `syn-hold-${state}-0001`,
      hold: { hold_id: `synthetic-hold-${state}`, artifact_digest: D(41), state },
    }, ctx(JOE));
    assert.equal(answer.decision, "allow", state);
    assert.equal(db.json(0).record.state, state);
  }
  const { store } = storeWith({
    stored_artifact: { artifact_digest: D(41), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
  });
  await refuses(store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-bogus-0001",
    hold: { hold_id: "synthetic-hold-bogus", artifact_digest: D(41), state: "lifted" },
  }, ctx(JOE)), "unknown_hold_state");
});

// ===========================================================================
// evaluate-artifact-deletion.
// ===========================================================================

const DELETION_SUBJECT = Object.freeze({
  artifact_class: "synthetic_test_lease",
  artifact_home: "onedrive",
  artifact_digest: D(51),
  satisfied_constraints: ["synthetic_test_constraint"],
  deletion_proof: {
    proof_ref: "synthetic-proof-0001",
    artifact_digest: D(51),
    proof_digest: D(52),
    executed_at: T.late,
  },
});

/**
 * The scripted database for one deletion evaluation.
 *
 * `coverage` defaults to the UNKNOWN answer, because that is what the real
 * ops.f01_derivative_coverage returns for every artifact today. A fixture that
 * wants the accept side has to say so, and says why.
 */
function deletionScript(holds, coverage = UNKNOWN_COVERAGE, stored_derivatives = null) {
  return policyScript({
    stored_artifact: { artifact_digest: D(51), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
    holds, holds_digest: digest(holds),
    coverage, stored_derivatives,
    outcome: { outcome: "allow" },
  });
}

test("a permitted deletion persists an evaluation and performs no deletion", async () => {
  // ESTABLISHED COVERAGE IS SCRIPTED HERE AND NOWHERE IN THE REAL SCHEMA. This
  // proves the store loads the coverage answer, hands it to the kernel and binds
  // its digest into the record; it is not a claim that any database can answer
  // "established" today, and the SQL fixture asserts the opposite.
  const { db, store } = storeWith(
    deletionScript([], ESTABLISHED_COVERAGE, ["synthetic_test_abstract"]));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-allow-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "deletion_permitted");
  assert.equal(answer.deletion_performed, false);
  assert.equal(answer.silent_purge, false);
  assert.equal(answer.bytes_deleted, false);
  assert.equal(answer.external_purge_performed, false);

  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_deletion_evaluation");
  assert.equal(envelope.bytes_deleted, false);
  assert.equal(envelope.external_purge_performed, false);
  assert.ok(envelope.record.deletion_receipt, "an allow carries a receipt");
  // CLASS POLICY and INSTANCE OBSERVATION, stored under different names.
  assert.deepEqual(envelope.record.deletion_receipt.surviving_derivatives,
    ["synthetic_test_abstract"]);
  assert.deepEqual(envelope.record.deletion_receipt.observed_surviving_derivatives,
    ["synthetic_test_abstract"]);
  assert.equal(envelope.record.derivative_coverage_state, "established");
  assert.equal(envelope.record.derivative_coverage_digest, digest(ESTABLISHED_COVERAGE),
    "the loaded coverage digest is bound into the record the database re-derives");
  assert.equal(db.param(1), digest([]), "the loaded hold-inventory digest travels as a CAS operand");

  // THE TRIGGER THE PERIOD WAS MEASURED FROM travels in the record, and the digest
  // beside it is taken over the DATABASE's full answer — the value
  // ops.f01_record_deletion_evaluation re-derives under the retention lock.
  assert.deepEqual(envelope.record.retention_clock, PROJECTED_CLOCK);
  assert.equal(envelope.record.retention_clock_digest, digest(RETENTION_CLOCK));
  assert.equal(envelope.record.deletion_receipt.retention_clock.started_at, CUSTODY_AT);
  assert.equal(envelope.record.deletion_receipt.retention_started_at, CUSTODY_AT);
  assert.equal(envelope.record.deletion_receipt.retention_clock.source_observed_at_used, false);
  assert.equal(answer.retention_clock_digest, digest(RETENTION_CLOCK));
  const clockCall = db.calls.find(c => c.text.includes("ops.f01_retention_clock("));
  assert.deepEqual(clockCall.params, [D(51)], "the clock is LOADED for this exact artifact");

  // The kernel's coverage shape is CLOSED, and the loader's answer carries more
  // fields than it accepts. The store projects rather than forwarding, so a
  // richer readback cannot break the evaluation.
  const loadCall = db.calls.find(c => c.text.includes("ops.f01_derivative_coverage("));
  assert.deepEqual(loadCall.params, [D(51)]);
});

test("unknown registration coverage blocks the deletion the store would otherwise allow", async () => {
  // Identical to the case above in every respect except the coverage answer —
  // the same clean subject, no holds, the same proof — so the refusal is
  // attributable to coverage and to nothing else. This is the answer the real
  // ops.f01_derivative_coverage gives for every artifact today.
  const { db, store } = storeWith(deletionScript([]));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-coverage-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "derivative_coverage_unknown");
  assert.equal(answer.derivative_coverage_state, "unknown");
  assert.equal(db.json(0).record.deletion_receipt, null, "a refusal carries no receipt");
  assert.equal(db.json(0).record.derivative_coverage_state, "unknown");
  assert.equal(db.json(0).record.derivative_coverage_digest, digest(UNKNOWN_COVERAGE));

  // Registered links do not change the answer. A partial registry is still a
  // registry nobody can vouch for, and the store never reads rows as coverage.
  const withLinks = storeWith(deletionScript([], {
    ...UNKNOWN_COVERAGE, registered_derivative_kinds: ["synthetic_test_abstract"],
    registered_link_count: 1,
  }, null));
  const second = await withLinks.store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-coverage-0002", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(second.decision, "refuse");
  assert.equal(second.reason_id, "derivative_coverage_unknown");

  // And a caller cannot supply the answer itself.
  await refuses(store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-coverage-0003",
    subject: { ...DELETION_SUBJECT, derivative_coverage: ESTABLISHED_COVERAGE },
  }, ctx(JOE)), "caller_derived_field_refused");
});

test("holds are LOADED and an active one blocks; a caller cannot supply its own", async () => {
  const holds = [{ hold_id: "synthetic-hold-0001", state: "active", placed_at: T.early,
    released_at: null }];
  const { db, store } = storeWith(deletionScript(holds));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-hold-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "active_hold_blocks_deletion");
  assert.deepEqual(answer.blocking_holds, ["synthetic-hold-0001"]);
  assert.equal(db.json(0).record.deletion_receipt, null, "a refusal carries no receipt");

  await refuses(store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-hold-0002",
    subject: { ...DELETION_SUBJECT, holds: [] },
  }, ctx(JOE)), "caller_derived_field_refused");
  await refuses(store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-hold-0003",
    subject: { ...DELETION_SUBJECT, created_at: T.early },
  }, ctx(JOE)), "caller_derived_field_refused");
});

test("an unknown hold state blocks exactly as hard as an active one", async () => {
  const holds = [{ hold_id: "synthetic-hold-0009", state: "unknown", placed_at: T.early,
    released_at: null }];
  const { store } = storeWith(deletionScript(holds));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-unknown-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_hold_state_blocks_deletion");
});

test("an omitted derivative inventory refuses rather than reading as verified-empty", async () => {
  // Established coverage and NO inventory: coverage is not the only fail-closed
  // gate, and an unavailable list is still not an empty one.
  const { store } = storeWith(deletionScript([], ESTABLISHED_COVERAGE, null));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-derivatives-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "derivative_inventory_missing");
});

test("the retention period is measured from LOADED custody, never from the artifact's observed instant",
  async () => {
    // THE DEFECT THIS FORECLOSES, at the seam where it actually mattered. The
    // store used to hand the kernel the stored artifact's `created_at` — the
    // instant its SOURCE says it observed it — as the start of the retention
    // period. Here the source observed the lease eight days ago and the record
    // layer took custody twelve hours ago; the class's one-day period has NOT
    // elapsed, and a store measuring from the source's timestamp would have
    // allowed the deletion.
    const fresh = storeWith({
      ...deletionScript([], ESTABLISHED_COVERAGE, ["synthetic_test_abstract"]),
      retention_clock: { ...RETENTION_CLOCK, started_at: "2026-09-09T00:00:00.000Z" },
    });
    const answer = await fresh.store.evaluateArtifactDeletion({
      idempotency_key: "syn-deletion-custody-0001", subject: DELETION_SUBJECT,
    }, ctx(JOE));
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.reason_id, "retention_period_not_elapsed");
    assert.equal(fresh.db.json(0).record.deletion_receipt, null);
    // The source's own instant is still what the store passes as the artifact's
    // creation — it is the artifact's identity and the window a deletion proof
    // has to sit inside — and it is eight days old, which is what an evaluation
    // measuring from it would have used.
    assert.equal(fresh.db.calls.some(c => c.text.includes("ops.f01_stored_artifact(")), true);

    // ...and with a week of custody behind it, the same subject is deletable.
    const settled = storeWith(
      deletionScript([], ESTABLISHED_COVERAGE, ["synthetic_test_abstract"]));
    const allowed = await settled.store.evaluateArtifactDeletion({
      idempotency_key: "syn-deletion-custody-0002", subject: DELETION_SUBJECT,
    }, ctx(JOE));
    assert.equal(allowed.decision, "allow");
    assert.equal(allowed.retention_clock.started_at, CUSTODY_AT);
  });

test("MUTATION KILL (retention clock): a caller cannot supply one and an underivable one refuses",
  async () => {
    // A CALLER MAY NOT STATE WHEN ITS OWN RETENTION PERIOD STARTED, and the
    // refusal names the attempt rather than reporting an unknown field.
    const { db, store } = storeWith(
      deletionScript([], ESTABLISHED_COVERAGE, ["synthetic_test_abstract"]));
    for (const key of ["retention_clock", "custody", "recorded_at"]) {
      await refuses(store.evaluateArtifactDeletion({
        idempotency_key: "syn-deletion-forgedclock-0001",
        subject: { ...DELETION_SUBJECT, [key]: PROJECTED_CLOCK },
      }, ctx(JOE)), "caller_derived_field_refused");
    }
    assert.equal(db.calls.length, 0, "not one forged clock opened a transaction");

    // A DATABASE THAT CAN DERIVE NO CLOCK is a deletion that cannot be evaluated,
    // and the refusal is recorded rather than resolved to some other timestamp.
    const underivable = storeWith({
      ...deletionScript([], ESTABLISHED_COVERAGE, ["synthetic_test_abstract"]),
      retention_clock: null, retention_clock_digest: null,
    });
    const answer = await underivable.store.evaluateArtifactDeletion({
      idempotency_key: "syn-deletion-noclock-0001", subject: DELETION_SUBJECT,
    }, ctx(JOE));
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.reason_id, "retention_clock_missing");
    assert.equal(underivable.db.json(0).record.retention_clock, null);
    assert.equal(underivable.db.json(0).record.deletion_receipt, null);

    // AND A LOADED CLOCK THAT ADMITS IT IS THE SOURCE'S OBSERVED INSTANT refuses
    // by name. This is the alias the whole mechanism exists to prevent, and the
    // store cannot talk its way past it either.
    const aliased = storeWith({
      ...deletionScript([], ESTABLISHED_COVERAGE, ["synthetic_test_abstract"]),
      retention_clock: { ...RETENTION_CLOCK, started_at: T.early, source_observed_at_used: true },
    });
    const refusal = await aliased.store.evaluateArtifactDeletion({
      idempotency_key: "syn-deletion-aliasclock-0001", subject: DELETION_SUBJECT,
    }, ctx(JOE));
    assert.equal(refusal.decision, "refuse");
    assert.equal(refusal.reason_id, "retention_clock_uses_source_observed_at");
  });

test("a deletion evaluation for an artifact the database does not hold refuses", async () => {
  const { db, store } = storeWith(policyScript({ stored_artifact: null }));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-unknown-artifact-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_artifact_reference");
  assert.equal(db.lastWrite, undefined);
});

// ===========================================================================
// read-record-source-authority.
// ===========================================================================

test("every registered read kind reaches the recomputing read function", async () => {
  for (const kind of V5_F01_READ_KINDS) {
    const { db, store } = storeWith({ read_body: { kind, body: null } });
    const answer = await store.readRecordSourceAuthority({ selector: { kind } }, ctx(JOE));
    assert.equal(answer.decision, "allow", kind);
    assert.equal(answer.integrity, "recomputed_not_trusted", kind);
    assert.equal(answer.stale_fallback_permitted, false, kind);
    const readCall = db.calls.find(c => c.text.includes("ops.f01_read("));
    assert.equal(readCall.params[0], kind);
  }
});

test("an unregistered read kind refuses and a read writes nothing", async () => {
  const { db, store } = storeWith({});
  await refuses(store.readRecordSourceAuthority(
    { selector: { kind: "everything" } }, ctx(JOE)), "unknown_read_kind");
  await refuses(store.readRecordSourceAuthority(
    { selector: { kind: "current_policy", tenant: ORGANIZATION_TENANT_ID } }, ctx(JOE)),
    "caller_derived_field_refused");
  assert.equal(db.lastWrite, undefined);
});

// ===========================================================================
// Idempotency binding.
// ===========================================================================

test("the idempotency request digest is bound to the operation, actor and exact payload", async () => {
  const payload = {
    idempotency_key: "syn-idempotency-0001",
    observation: { ...OWNER_OBSERVATION },
  };
  const first = storeWith(policyScript({ field_state: storedState() }));
  await first.store.recordSourceObservation(payload, ctx(JOE));
  const firstDigest = first.db.param(11);

  const second = storeWith(policyScript({ field_state: storedState() }));
  await second.store.recordSourceObservation(payload, ctx(JOE));
  assert.equal(second.db.param(11), firstDigest, "the same payload replays to the same digest");

  const third = storeWith(policyScript({ field_state: storedState() }));
  await third.store.recordSourceObservation({
    ...payload,
    observation: { ...OWNER_OBSERVATION, value_digest: VALUE_B,
      readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_B } },
  }, ctx(JOE));
  assert.notEqual(third.db.param(11), firstDigest,
    "a different payload under the same key must not reuse the binding");
});

test("a malformed or missing idempotency key refuses before any statement", async () => {
  const { db, store } = storeWith(policyScript());
  await refuses(store.recordSourceObservation(
    { observation: OWNER_OBSERVATION }, ctx(JOE)), "missing_field");
  await refuses(store.recordSourceObservation(
    { idempotency_key: "not a key", observation: OWNER_OBSERVATION }, ctx(JOE)),
    "invalid_idempotency_key");
  await refuses(store.recordSourceObservation(
    { idempotency_key: "x".repeat(201), observation: OWNER_OBSERVATION }, ctx(JOE)),
    "invalid_idempotency_key");
  assert.equal(db.calls.length, 0);
});

// ===========================================================================
// Canonical bytes — the exact preimages the SQL fixtures compare against.
// ===========================================================================

test("an envelope hashes to its own record digest and to stable canonical bytes", () => {
  const record = storedHoldRecord({
    hold_id: "synthetic-hold-0001", artifact_digest: D(41), state: "active",
    reason: "synthetic test hold", placed_at: T.early, released_at: null,
    prior_hold_digest: null, recorded_by: "joe", recorded_at: SERVER_NOW,
  });
  const envelope = v5F01StoreEnvelope("stored_preservation_hold", record, {
    deletes_artifact: false,
  });
  assert.equal(envelope.record_digest, digest(record));
  assert.equal(digest(envelope), digest(JSON.parse(v5F01EnvelopeCanonicalBytes(envelope))));
  assert.equal(envelope.schema_version, V5_F01_ENVELOPE_SCHEMA_VERSION);
  assert.equal(envelope.tenant, ORGANIZATION_TENANT_ID);
});

test("an unregistered record kind cannot be enveloped", () => {
  assert.throws(() => v5F01StoreEnvelope("stored_whatever", {}), error => {
    assert.equal(error.code, "unknown_record_kind");
    return true;
  });
  assert.equal(V5_F01_STORE_RECORD_KINDS.length, 14);
  assert.ok(V5_F01_STORE_RECORD_KINDS.includes("stored_derivative_link"));
  // Registered here, in the persistence tail, because that is where the envelope
  // contract lives. The document-source module refuses to hand-roll an envelope
  // of its own and instead builds through v5F01StoreEnvelope, so this membership
  // is what makes that path possible at all.
  assert.ok(V5_F01_STORE_RECORD_KINDS.includes("stored_document_source_provenance"));
});

// The bytes below are the JS side of the byte-for-byte comparison the SQL
// fixtures make against ops.f01_canonical_json. They are asserted here so a
// change to either side is a failure in BOTH suites rather than a silent drift.
test("canonical bytes match the documented shape for Unicode and timestamp edges", () => {
  const cases = [
    [{ a: 1, b: "x" }, '{"a":1,"b":"x"}'],
    [{ b: 1, a: 2 }, '{"a":2,"b":1}'],
    [{ "é": "café" }, '{"é":"café"}'],
    [{ k: "line\nbreak" }, '{"k":"line\\nbreak"}'],
    [{ k: "quote\"and\\slash" }, '{"k":"quote\\"and\\\\slash"}'],
    [{ k: "" }, '{"k":""}'],
    [{ k: "\u{1f5c2}" }, '{"k":"\u{1f5c2}"}'],
    [{ k: null }, '{"k":null}'],
    [{ k: true, j: false }, '{"j":false,"k":true}'],
    [{ k: [1, 2, 3] }, '{"k":[1,2,3]}'],
    [{ k: 0.75 }, '{"k":0.75}'],
    [{ k: 9007199254740991 }, '{"k":9007199254740991}'],
    [{ observed_at: "2026-02-28T23:59:59.999Z" }, '{"observed_at":"2026-02-28T23:59:59.999Z"}'],
    [{ observed_at: "2028-02-29T00:00:00Z" }, '{"observed_at":"2028-02-29T00:00:00Z"}'],
    [{ observed_at: "2026-12-31T23:59:60+00:00" },
      '{"observed_at":"2026-12-31T23:59:60+00:00"}'],
    [{ observed_at: "2026-09-09T12:00:00-07:00" }, '{"observed_at":"2026-09-09T12:00:00-07:00"}'],
  ];
  for (const [value, expected] of cases) {
    assert.equal(canonicalJson(value), expected, JSON.stringify(value));
  }
  // Key order is UTF-16 code unit order, which is what the PostgreSQL sort key
  // reproduces. An astral key sorts BELOW a high BMP key, because JavaScript
  // compares the surrogate units (0xD800..) rather than the code point
  // (0x10000..). Plain code-point ordering would get this exactly backwards, so
  // the SQL side builds a surrogate-aware sort key rather than ordering by key.
  // Written as escapes rather than literals so the property under test is the
  // code points, not whatever an editor decided to paste.
  const astral = String.fromCodePoint(0x10000);   // units 0xD800 0xDC00
  const highBmp = String.fromCharCode(0xFF00);    // one unit, 0xFF00
  assert.ok(astral.codePointAt(0) > highBmp.codePointAt(0), "astral is the higher code point");
  assert.ok(astral.charCodeAt(0) < highBmp.charCodeAt(0), "astral is the lower code unit");
  assert.equal(canonicalJson({ [highBmp]: 2, [astral]: 1 }),
    `{"${astral}":1,"${highBmp}":2}`);
});

test("the store schema versions are stable identities", () => {
  assert.equal(V5_F01_STORE_SCHEMA_VERSION,
    "doctorcre-v5-f01-record-source-authority-store.v1");
  assert.equal(V5_F01_ENVELOPE_SCHEMA_VERSION,
    "doctorcre-v5-f01-stored-record-envelope.v1");
  assert.equal(STORED_POLICY_RECORD.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(digest(STORED_POLICY_RECORD), POLICY_DIGEST);
});


test("replay returns persisted observation decision before reading changed state", async () => {
  const original = {
    operation: "record-source-observation", outcome: "accepted", actor_slug: "joe",
    entity: "deal", field: "commission_amount", reason_id: "field_established",
    version_ordering: null, conflict_kind: null, policy_digest: POLICY_DIGEST,
    current_state_transition_digest: D(71), event_digest: D(72),
    mutation_receipt_digest: D(73), reconciliation_item_digest: null,
    readback: { current_state: { entity: "deal", field: "commission_amount" } },
  };
  const { db, store } = storeWith({ replay_outcome: original });
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-replay-observation", observation: OWNER_OBSERVATION,
  }, ctx(JOE));
  assert.equal(answer.decision, "accept");
  assert.equal(answer.reason_id, "field_established");
  assert.equal(answer.event_digest, D(72));
  assert.deepEqual(answer.readback, original);
  assert.equal(db.lastWrite, undefined);
  assert.equal(db.calls.some(c => /f01_current_(policy|field_state)/.test(c.text)), false);
});

test("caller derivative inventory refuses by name before database work", async () => {
  const { db, store } = storeWith();
  await refuses(store.evaluateArtifactDeletion({
    idempotency_key: "syn-forged-derivatives",
    subject: { ...DELETION_SUBJECT, derivatives: ["synthetic_test_abstract"] },
  }, ctx(JOE)), "caller_derived_field_refused");
  assert.equal(db.calls.length, 0);
});


const REPLAY_CASES = [
  ["registerRecordSourceAuthorityPolicy", {
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  }, { policy: null }],
  ["recordSourceObservation", { observation: OWNER_OBSERVATION }, policyScript()],
  ["recordCorporateArtifact", { artifact: ARTIFACT }, {}],
  ["recordParsedProposal", { proposal: PROPOSAL }, policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
  })],
  ["registerDerivativeSourceLink", { registration: REGISTRATION }, {
    stored_artifact: { artifact_digest: D(21), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early },
  }],
  ["recordDocumentIdentity", { document: DOCUMENT, source: ORIGINAL_SOURCE },
    { document_current: null, document_provenance: null }],
  ["recordArtifactPreservationHold", { hold: {
    hold_id: "synthetic-replay-hold", artifact_digest: D(41), state: "active",
  } }, { stored_artifact: { artifact_digest: D(41), artifact: { content_digest: SOURCE_CONTENT }, created_at: T.early } }],
  ["evaluateArtifactDeletion", { subject: DELETION_SUBJECT }, deletionScript([])],
];

for (const [method, fields, script] of REPLAY_CASES) {
  test(`${method} first application and replay project the same committed outcome`, async () => {
    const payload = { idempotency_key: `syn-replay-${method}`, ...fields };
    const initial = storeWith(script);
    const answer = await initial.store[method](payload, ctx(JOE));
    assert.ok(initial.db.lastOutcome);
    // No current policy, artifact, document or hold is available on this retry:
    // replay must not resolve against newer or now-unavailable domain state.
    const replay = storeWith({ replay_outcome: initial.db.lastOutcome,
      server_now: "2026-10-01T00:00:00.000Z" });
    const repeated = await replay.store[method](payload, ctx(JOE));
    assert.deepEqual(repeated, answer);
    assert.equal(replay.db.lastWrite, undefined);
    assert.deepEqual(replay.db.calls.filter(c => c.text.startsWith("SELECT")).map(c => c.text), [
      "SELECT ops.f01_principal() AS principal, ops.f01_now_text() AS server_now",
      "SELECT ops.f01_replay_outcome($1::text, $2::text, $3::text) AS outcome",
    ]);
    const firstBinding = initial.db.calls.find(c => c.text.includes("f01_replay_outcome")).params;
    const replayBinding = replay.db.calls.find(c => c.text.includes("f01_replay_outcome")).params;
    assert.deepEqual(replayBinding, firstBinding);
  });
}

test("observation replay also preserves committed refusal and reconciliation diagnostics", async () => {
  for (const version of [3, 4]) {
    const payload = { idempotency_key: `syn-replay-observation-${version}`,
      observation: { ...OWNER_OBSERVATION, version } };
    const initial = storeWith(policyScript({ field_state: storedState() }));
    const answer = await initial.store.recordSourceObservation(payload, ctx(JOE));
    assert.notEqual(answer.decision, "accept");
    const replay = storeWith({ replay_outcome: initial.db.lastOutcome });
    assert.deepEqual(await replay.store.recordSourceObservation(payload, ctx(JOE)), answer);
    assert.equal(replay.db.lastWrite, undefined);
  }
});

test("authority operations require the database to attest human and authorization class", async () => {
  for (const principal of [
    { actor_slug: "joe", human: false, authorization_class: "verified_partner" },
    { actor_slug: "joe", human: true, authorization_class: "agent_internal" },
  ]) {
    const { db, store } = storeWith({ principal });
    await refuses(store.recordArtifactPreservationHold({ idempotency_key: "syn-db-authority",
      hold: { hold_id: "synthetic-hold", artifact_digest: D(41), state: "active" },
    }, ctx(JOE)), "actor_context_mismatch");
    assert.equal(db.calls.some(c => c.text.includes("f01_replay_outcome")), false);
    assert.equal(db.lastWrite, undefined);
  }
});

test("ordinary evidence requests do not infer human authority from a writer-role principal", async () => {
  const { store } = storeWith({ principal: {
    actor_slug: "joe", human: false, authorization_class: "agent_internal",
  } });
  const answer = await store.recordCorporateArtifact({ idempotency_key: "syn-ordinary-writer",
    artifact: ARTIFACT }, ctx(JOE));
  assert.equal(answer.decision, "allow");
});

test("SQL replay integrity or substitution refusal rolls back without evaluating or writing", async () => {
  for (const code of ["f01_idempotency_payload_mismatch", "f01_idempotency_actor_mismatch",
    "f01_idempotency_operation_mismatch", "f01_corrupt_stored_record", "f01_unsettled_idempotency"]) {
    const error = Object.assign(new Error(code), { code });
    const { db, store } = storeWith({ replayError: error });
    await assert.rejects(store.recordSourceObservation({ idempotency_key: "syn-replay-refusal",
      observation: OWNER_OBSERVATION }, ctx(JOE)), e => e === error);
    assert.equal(db.lastWrite, undefined);
    assert.equal(db.rolledBack, 1);
    assert.equal(db.calls.some(c => c.text.includes("f01_current_policy")), false);
  }
});

test("a mismatched returned replay operation or actor is not presented as a valid outcome", async () => {
  for (const replay_outcome of [
    { operation: "record-document-identity", actor_slug: "joe" },
    { operation: "record-source-observation", actor_slug: "dell" },
  ]) {
    const { store } = storeWith({ replay_outcome });
    await refuses(store.recordSourceObservation({ idempotency_key: "syn-replay-binding",
      observation: OWNER_OBSERVATION }, ctx(JOE)), "invalid_stored_outcome");
  }
});
