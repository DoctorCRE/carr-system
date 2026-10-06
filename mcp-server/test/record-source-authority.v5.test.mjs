// V5-F01 phase 1 — the authoritative-home and source-authority kernel, proved
// case by case.
//
// The suite is organised by the decision it proves, and every settled decision
// gets both halves: the positive case that must pass, and the negatives that
// must refuse. The positive halves matter as much as the refusals — a contract
// that only ever says no cannot be told apart from one that is broken — so the
// owner path, the three bound records, the artifact and proposal round trips,
// the full document state matrix and a permitted deletion are all asserted to
// WORK before their failure shapes are asserted to refuse.
//
// Everything here is synthetic. No fixture names a real deal, client, document
// or provider account, and no test reaches a network, a database, a queue or a
// provider. The registries below are TEST POLICY: they are what a caller might
// supply, never a claim about what CARR's real field owners or retention periods
// are. That distinction is the slice's whole point and the fixtures respect it.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { evaluatePrivacyBoundary } from "../src/global-boundaries.v5.js";
import {
  V5_F01_SCHEMA_VERSION,
  V5_F01_POLICY_VERSION,
  V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
  V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
  V5_F01_SETTLED_DECISIONS,
  V5_F01_SETTLED_DECISION_IDS,
  V5_F01_AUTHORITY_INJECTION_FRAGMENTS,
  V5_F01_HOMES,
  V5_F01_FACT_CLASSES,
  V5_F01_AUTHORITATIVE_FACT_CLASSES,
  V5_F01_NON_AUTHORITATIVE_FACT_CLASSES,
  V5_F01_WRITE_DIRECTIONS,
  V5_F01_VERSION_COMPARATORS,
  V5_F01_CONFLICT_BEHAVIORS,
  V5_F01_TAINT_CLASSES,
  V5_F01_RECORD_KINDS,
  V5_F01_EVIDENCE_CLASSES,
  V5_F01_TOUR_ONLY_EVIDENCE_CLASSES,
  V5_F01_PREPARATION_STATES,
  V5_F01_DELIVERY_STATES,
  V5_F01_SIGNATURE_STATES,
  V5_F01_VALIDITY_STATES,
  V5_F01_VERSION_STATES,
  V5_F01_FILING_STATES,
  V5_F01_HOLD_STATES,
  V5_F01_RETENTION_CLOCK_KINDS,
  V5_F01_DEFAULT_RETENTION_CLOCK_KIND,
  V5_F01_DERIVATIVE_COVERAGE_STATES,
  V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION,
  V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
  V5_F01_RESERVED_DERIVATIVE_KINDS,
  V5_NO_EFFECTS,
  V5F01Error,
  assertF01DecisionBinding,
  projectRecordHome,
  compileFieldAuthorityRegistry,
  fieldAuthorityRegistryPreimage,
  resolveObservation,
  admitCorporateArtifact,
  evaluateParsedProposal,
  projectDocumentIdentity,
  compileRetentionRegistry,
  retentionRegistryPreimage,
  evaluateDerivativeRegistration,
  evaluateDeletion,
  v5F01DecisionSubsetPreimage,
  v5F01DecisionSubsetDigest,
  v5F01PolicyPreimage,
  v5F01PolicyDigest,
  v5F01PolicyCanonicalBytes,
  v5F01AuthorityProjection,
} from "../src/record-source-authority.v5.js";

const SRC_PATH = fileURLToPath(new URL("../src/record-source-authority.v5.js", import.meta.url));

// The nine source-evidence digests exactly as the reviewed F01 source binding
// carries them. Typed here independently so drift between the module and the
// binding is a test failure rather than a later discovery.
const BINDING_DIGESTS = Object.freeze({
  "Q004.D1": "9e0b113ecff9d1b13086fcb03b4165ccd8b88f82d688bb576c58af54fa3b68ad",
  "Q052.D1": "b70bda92d47c19b006ffda90679743af5ce48d160d110ebe9a880c09c3fb8f0d",
  "Q054.D1": "46d76ca4ac6f7b59566e00145575c9352755b4c73e847c083e767238ecd3d2b3",
  "Q071.D1": "0011d2df6d9be7ec44948eec44a93399d25c9d10e0f101f554027d17ebc81097",
  "Q108.D1": "dccf85d5fd869dc721d37a5c69574f4df4cffa09b5b1254ba2a8a57dac8f7233",
  "Q125.D1": "396b6350c7c1af42ab2013c0ea6d27da6f3c15767ed6ecd9ca02b07bade8d07b",
  "Q129.D1": "e955d7280dbabeed2feccc5a1dfcceefa80d3bd241f7ec6f4f95b3e99e52d405",
  "Q135.D1": "06dc23361c4e972066af9a5f0dea499061ccdf8b45fad8c265da1714d9c8f623",
  "Q155.D1": "1fdce0e93cd911106682ce465fedff7c29461b98d5fbc6df2f6b1d001cc6aec6",
});

const NOW = "2026-09-09T12:00:00Z";
const T = {
  early: "2026-09-01T09:00:00Z",
  mid: "2026-09-05T09:00:00Z",
  late: "2026-09-08T09:00:00Z",
  future: "2026-09-10T09:00:00Z",
};

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const VALUE_A = D(1);
const VALUE_B = D(2);
const VALUE_C = D(3);

function throwsCode(fn, code, message) {
  assert.throws(fn, error => {
    assert.ok(error instanceof V5F01Error, `expected V5F01Error, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `${message ?? ""} expected code ${code}, got ${error.code}`);
    return true;
  });
}

// --- test policy -----------------------------------------------------------
//
// Ten field entries. Between them they exercise every one of the seven homes,
// all four version comparators and both conflict behaviours, which is what makes
// "one positive registry exercises every authoritative home" checkable rather
// than asserted.

const SOURCES = {
  sf: "salesforce",
  neon: "neon_record_layer",
  onedrive: "onedrive",
  storage: "object_storage",
  outlook: "outlook",
  repo: "repository",
  edge: "cloudflare",
  future: "future_corporate_source",
};

function entry(overrides) {
  return {
    entity: "deal",
    field: "unset",
    authoritative_home: "neon_record_layer",
    owner_source: SOURCES.neon,
    permitted_sources: [{ source_system: SOURCES.neon, direction: "bidirectional" }],
    requires_account_identity: false,
    requires_native_identity: false,
    version_comparator: "integer_sequence",
    conflict_behavior: "reconcile",
    human_resolver_class: "deal_owner",
    readback_required: false,
    sensitivity_classes: ["lease_economics"],
    taint_class: "first_party_record_layer",
    ...overrides,
  };
}

const REGISTRY_POLICY = Object.freeze({
  schema_version: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
  registry_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  entries: [
    // salesforce home — the corporate transaction field, owned by Salesforce,
    // written inbound through the governed adapter, read back, reconciled.
    entry({
      entity: "deal", field: "commission_amount",
      authoritative_home: "salesforce", owner_source: SOURCES.sf,
      permitted_sources: [
        { source_system: SOURCES.sf, direction: "inbound" },
        { source_system: SOURCES.neon, direction: "outbound" },
      ],
      requires_account_identity: true, requires_native_identity: true,
      readback_required: true, conflict_behavior: "reconcile",
      human_resolver_class: "deal_owner", sensitivity_classes: ["lease_economics"],
      taint_class: "corporate_source_of_record",
    }),
    // salesforce home, instant comparator, conflict REFUSES rather than queues.
    entry({
      entity: "deal", field: "close_date",
      authoritative_home: "salesforce", owner_source: SOURCES.sf,
      permitted_sources: [
        { source_system: SOURCES.sf, direction: "inbound" },
        { source_system: SOURCES.neon, direction: "none" },
      ],
      requires_account_identity: true, requires_native_identity: true,
      version_comparator: "instant", conflict_behavior: "refuse",
      human_resolver_class: "system_authority", taint_class: "corporate_source_of_record",
    }),
    // opaque provider version: only equality is knowable.
    entry({
      entity: "deal", field: "provider_stage",
      authoritative_home: "salesforce", owner_source: SOURCES.sf,
      permitted_sources: [
        { source_system: SOURCES.sf, direction: "inbound" },
        { source_system: SOURCES.future, direction: "inbound" },
      ],
      requires_account_identity: true, requires_native_identity: true,
      version_comparator: "opaque_equality", conflict_behavior: "reconcile",
      taint_class: "corporate_source_of_record",
    }),
    // an opaque token set with an EXPLICIT ascending order supplied by policy.
    entry({
      entity: "deal", field: "lifecycle",
      authoritative_home: "salesforce", owner_source: SOURCES.sf,
      permitted_sources: [{ source_system: SOURCES.sf, direction: "inbound" }],
      requires_account_identity: true, requires_native_identity: true,
      version_comparator: "declared_order",
      version_order: ["alpha", "beta", "gamma"],
      conflict_behavior: "reconcile", taint_class: "corporate_source_of_record",
    }),
    // neon home — first-party operating truth.
    entry({ entity: "deal", field: "next_step" }),
    // neon home with a SECOND permitted inbound source that does not own the
    // field. This is the shape a forbidden overwrite needs: an ordered
    // comparator, so "newer" is knowable, and a writer who is not the owner.
    entry({
      entity: "deal", field: "shared_note",
      permitted_sources: [
        { source_system: SOURCES.neon, direction: "inbound" },
        { source_system: SOURCES.sf, direction: "inbound" },
      ],
    }),
    // repository home.
    entry({
      entity: "contract_schema", field: "version",
      authoritative_home: "repository", owner_source: SOURCES.repo,
      permitted_sources: [{ source_system: SOURCES.repo, direction: "inbound" }],
      sensitivity_classes: ["public_registry_record"],
    }),
    // onedrive home — the official executed copy pointer.
    entry({
      entity: "document", field: "official_copy_item_id",
      authoritative_home: "onedrive", owner_source: SOURCES.onedrive,
      permitted_sources: [
        { source_system: SOURCES.onedrive, direction: "inbound" },
        { source_system: SOURCES.neon, direction: "outbound" },
      ],
      requires_account_identity: true, requires_native_identity: true,
      sensitivity_classes: ["lease_economics"], taint_class: "corporate_source_of_record",
    }),
    // object storage home — working and sealed bytes.
    entry({
      entity: "document", field: "draft_object_key",
      authoritative_home: "object_storage", owner_source: SOURCES.storage,
      permitted_sources: [{ source_system: SOURCES.storage, direction: "inbound" }],
      sensitivity_classes: ["lease_economics"],
    }),
    // outlook home — mailbox history.
    entry({
      entity: "thread", field: "last_message_at",
      authoritative_home: "outlook", owner_source: SOURCES.outlook,
      permitted_sources: [{ source_system: SOURCES.outlook, direction: "inbound" }],
      requires_account_identity: true, requires_native_identity: true,
      version_comparator: "instant",
      sensitivity_classes: ["tenant_business_contact"], taint_class: "corporate_source_of_record",
    }),
    // cloudflare home — command transport.
    entry({
      entity: "command", field: "transport_route",
      authoritative_home: "cloudflare", owner_source: SOURCES.edge,
      permitted_sources: [{ source_system: SOURCES.edge, direction: "inbound" }],
      sensitivity_classes: ["public_registry_record"],
    }),
  ],
});

const REGISTRY = compileFieldAuthorityRegistry(REGISTRY_POLICY);

const RETENTION_POLICY = Object.freeze({
  schema_version: V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
  registry_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  classes: [
    {
      artifact_class: "executed_lease",
      authoritative_home: "onedrive",
      default_retention_days: 30,
      governing_constraints: ["brokerage_records_policy"],
      deletion_proof_required: true,
      surviving_derivatives: ["lease_abstract", "deal_economics_summary"],
    },
    {
      artifact_class: "draft_document",
      authoritative_home: "object_storage",
      default_retention_days: 0,
      governing_constraints: [],
      deletion_proof_required: false,
      surviving_derivatives: [],
    },
    {
      artifact_class: "corporate_field_snapshot",
      authoritative_home: "salesforce",
      default_retention_days: 7,
      governing_constraints: ["corporate_source_policy"],
      deletion_proof_required: true,
      surviving_derivatives: ["reconciliation_history"],
    },
  ],
});

const RETENTION = compileRetentionRegistry(RETENTION_POLICY);

const NATIVE = Object.freeze({
  source_system: SOURCES.sf, native_id: "006SYNTHETIC0001", native_id_epoch: "epoch-1",
});
const PROVENANCE = Object.freeze({
  adapter_kind: "governed_browser_adapter",
  evidence_ref: "synthetic-evidence-0001",
  retrieval_class: "corporate_record_export",
});

// commission_amount requires a readback, so the default fixture keeps the
// readback digest in step with the value unless a test overrides it on purpose.
// Without that, changing a value silently produces a readback_value_mismatch and
// a test appears to prove something it never reached.
function observation(overrides = {}) {
  const merged = {
    entity: "deal", field: "commission_amount", tenant: ORGANIZATION_TENANT_ID,
    source_system: SOURCES.sf, account: "acct-synthetic-1",
    native_identity: { ...NATIVE },
    value_digest: VALUE_A, version: 5, observed_at: T.mid,
    provenance: { ...PROVENANCE },
    taint_class: "corporate_source_of_record",
    ...overrides,
  };
  if (!("readback" in overrides)) {
    merged.readback = {
      confirmed: true, readback_at: merged.observed_at, readback_value_digest: merged.value_digest,
    };
  }
  return merged;
}

function currentState(overrides = {}) {
  return {
    entity: "deal", field: "commission_amount", tenant: ORGANIZATION_TENANT_ID,
    account: "acct-synthetic-1",
    native_identity: { ...NATIVE },
    value_digest: VALUE_B, version: 4, owner_source: SOURCES.sf,
    observed_at: T.early, event_seq: 7, last_event_digest: D(9),
    ...overrides,
  };
}

function resolve(overrides = {}) {
  return resolveObservation({
    tenant: ORGANIZATION_TENANT_ID, registry: REGISTRY, now: NOW,
    observation: observation(overrides.observation ?? {}),
    current_state: "current_state" in overrides ? overrides.current_state : currentState(),
  });
}

// ===========================================================================
// The settled decision binding.
// ===========================================================================

test("the nine settled decisions match the reviewed source binding exactly", () => {
  assert.deepEqual([...V5_F01_SETTLED_DECISION_IDS], Object.keys(BINDING_DIGESTS).sort());
  for (const [id, source_evidence_digest] of Object.entries(BINDING_DIGESTS)) {
    assert.equal(V5_F01_SETTLED_DECISIONS[id].source_evidence_digest, source_evidence_digest, id);
    assert.ok(V5_F01_SETTLED_DECISIONS[id].settled_requirement.length > 40, id);
  }
  assert.equal(assertF01DecisionBinding({ decisions: BINDING_DIGESTS_AS_ENTRIES() }), true);
  assert.equal(
    assertF01DecisionBinding({
      decisions: BINDING_DIGESTS_AS_ENTRIES(),
      decision_subset_digest: v5F01DecisionSubsetDigest(),
    }), true);
  // The derived subset digest is taken over exactly the preimage a reviewer can
  // rebuild by hand from the binding's own values.
  assert.equal(v5F01DecisionSubsetDigest(), digest(v5F01DecisionSubsetPreimage()));
  assert.equal(v5F01DecisionSubsetDigest(), digest({
    schema_version: "doctorcre-v5-f01-decision-subset.v1",
    decisions: Object.keys(BINDING_DIGESTS).sort().map(decision_id => ({
      decision_id,
      settled_requirement: V5_F01_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: BINDING_DIGESTS[decision_id],
    })),
  }));
});

function BINDING_DIGESTS_AS_ENTRIES() {
  return Object.fromEntries(Object.entries(BINDING_DIGESTS)
    .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }]));
}

test("decision drift refuses in both directions and on every digest", () => {
  const missing = BINDING_DIGESTS_AS_ENTRIES();
  delete missing["Q129.D1"];
  throwsCode(() => assertF01DecisionBinding({ decisions: missing }), "decision_binding_drift");

  const extra = { ...BINDING_DIGESTS_AS_ENTRIES(), "Q999.D1": { source_evidence_digest: "a".repeat(64) } };
  throwsCode(() => assertF01DecisionBinding({ decisions: extra }), "decision_binding_drift");

  const wrong = BINDING_DIGESTS_AS_ENTRIES();
  wrong["Q054.D1"] = { source_evidence_digest: "b".repeat(64) };
  throwsCode(() => assertF01DecisionBinding({ decisions: wrong }), "decision_binding_drift");

  const wrongText = BINDING_DIGESTS_AS_ENTRIES();
  wrongText["Q052.D1"] = {
    source_evidence_digest: BINDING_DIGESTS["Q052.D1"],
    settled_requirement: "event source everything",
  };
  throwsCode(() => assertF01DecisionBinding({ decisions: wrongText }), "decision_binding_drift");

  throwsCode(() => assertF01DecisionBinding({
    decisions: BINDING_DIGESTS_AS_ENTRIES(), decision_subset_digest: `sha256:${"f".repeat(64)}`,
  }), "decision_binding_drift");
  throwsCode(() => assertF01DecisionBinding({ decisions: BINDING_DIGESTS_AS_ENTRIES(), unexpected: 1 }),
    "unknown_field");
});

// ===========================================================================
// Q004 / Q108 / Q155 — one typed authority home for every fact.
// ===========================================================================

test("Q004: every one of the seven homes is the home of at least one fact class", () => {
  const reached = new Set();
  for (const fact_class of V5_F01_AUTHORITATIVE_FACT_CLASSES) {
    const result = projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class });
    assert.equal(result.decision, "allow", fact_class);
    assert.equal(result.authoritative, true, fact_class);
    assert.ok(V5_F01_HOMES.includes(result.authoritative_home), fact_class);
    reached.add(result.authoritative_home);
  }
  assert.deepEqual([...reached].sort(), [...V5_F01_HOMES].sort());
});

test("Q108/Q155: no surface can be authoritative, and each says which kind it is", () => {
  const byDisposition = {};
  for (const fact_class of V5_F01_NON_AUTHORITATIVE_FACT_CLASSES) {
    const plain = projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class });
    assert.equal(plain.decision, "allow", fact_class);
    assert.equal(plain.authoritative, false, fact_class);
    assert.equal(plain.authoritative_home, null, fact_class);
    (byDisposition[plain.disposition] ??= []).push(fact_class);

    // Claiming authority for it refuses, and so does claiming a home for it.
    const claimed = projectRecordHome({
      tenant: ORGANIZATION_TENANT_ID, fact_class, claimed_authoritative: true,
    });
    assert.equal(claimed.decision, "refuse", fact_class);
    assert.equal(claimed.reason_id, "surface_cannot_hold_authority", fact_class);
    const homed = projectRecordHome({
      tenant: ORGANIZATION_TENANT_ID, fact_class, claimed_home: "neon_record_layer",
    });
    assert.equal(homed.decision, "refuse", fact_class);
    assert.equal(homed.reason_id, "surface_cannot_hold_authority", fact_class);
  }
  // Markdown, dashboards, browser state, prompts, local files and conversation
  // prose are surfaces; summaries and embeddings are disposable; diagrams and
  // operator views are projections; temporary JSON is review evidence.
  assert.deepEqual(byDisposition.non_authoritative_surface.sort(),
    ["browser_session", "conversation_prose", "dashboard", "local_file", "markdown_render", "prompt"]);
  assert.deepEqual(byDisposition.disposable_index.sort(), ["embedding", "summary"]);
  assert.deepEqual(byDisposition.generated_projection.sort(), ["diagram", "operator_view"]);
  assert.deepEqual(byDisposition.review_evidence, ["temporary_json"]);
});

test("Q155: graphs, children, decisions, contracts and acceptance live in the record layer", () => {
  for (const fact_class of [
    "authoritative_graph", "graph_child_reference", "decision", "contract", "acceptance",
    "document_metadata", "source_conversation", "rule", "work_item", "operating_fact",
  ]) {
    const result = projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class });
    assert.equal(result.authoritative_home, "neon_record_layer", fact_class);
  }
});

test("Q004: a claimed home that is not the one canonical home refuses", () => {
  const result = projectRecordHome({
    tenant: ORGANIZATION_TENANT_ID, fact_class: "corporate_transaction_field",
    claimed_home: "neon_record_layer",
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "home_mismatch");
  assert.equal(result.authoritative_home, "salesforce");
  const matched = projectRecordHome({
    tenant: ORGANIZATION_TENANT_ID, fact_class: "corporate_transaction_field",
    claimed_home: "salesforce",
  });
  assert.equal(matched.decision, "allow");
  assert.equal(matched.reason_id, "single_authoritative_home");
});

test("Q004: unknown classes, unknown homes and the wrong tenant all throw", () => {
  throwsCode(() => projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class: "gut_feel" }),
    "unknown_fact_class");
  throwsCode(() => projectRecordHome({
    tenant: ORGANIZATION_TENANT_ID, fact_class: "code", claimed_home: "sharepoint",
  }), "unknown_home");
  throwsCode(() => projectRecordHome({ tenant: "other-tenant", fact_class: "code" }), "tenant_mismatch");
  throwsCode(() => projectRecordHome({ tenant: ORGANIZATION_TENANT_ID }), "missing_field");
});

// ===========================================================================
// Q054 — the source × entity × field registry is compiled, not assumed.
// ===========================================================================

function policyWith(...extraEntries) {
  return {
    ...REGISTRY_POLICY,
    entries: [...REGISTRY_POLICY.entries.map(e => ({ ...e })), ...extraEntries],
  };
}
function policyOf(...entries) {
  return { ...REGISTRY_POLICY, entries };
}

test("Q054: a compiled registry covers every home, is sorted, and hashes to its own digest", () => {
  assert.equal(REGISTRY.compiled, true);
  assert.equal(REGISTRY.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(REGISTRY.registry_digest, digest(fieldAuthorityRegistryPreimage(REGISTRY)));
  assert.ok(Object.isFrozen(REGISTRY));
  assert.ok(Object.isFrozen(REGISTRY.entries));

  const homes = new Set(REGISTRY.entries.map(e => e.authoritative_home));
  assert.deepEqual([...homes].sort(), [...V5_F01_HOMES].sort(),
    "the positive registry must exercise every authoritative home");
  const comparators = new Set(REGISTRY.entries.map(e => e.version_comparator));
  assert.deepEqual([...comparators].sort(), [...V5_F01_VERSION_COMPARATORS].sort(),
    "and every version comparator");
  const behaviors = new Set(REGISTRY.entries.map(e => e.conflict_behavior));
  assert.deepEqual([...behaviors].sort(), [...V5_F01_CONFLICT_BEHAVIORS].sort());

  // Sorted deterministically, so two callers compiling the same policy in a
  // different order reach the same digest.
  const reversed = compileFieldAuthorityRegistry({
    ...REGISTRY_POLICY, entries: [...REGISTRY_POLICY.entries].reverse(),
  });
  assert.equal(reversed.registry_digest, REGISTRY.registry_digest);
});

test("Q054: ambiguous, duplicated or self-contradicting policy refuses at compile", () => {
  const dup = entry({ entity: "deal", field: "commission_amount", authoritative_home: "salesforce",
    owner_source: SOURCES.sf,
    permitted_sources: [{ source_system: SOURCES.sf, direction: "inbound" }] });
  throwsCode(() => compileFieldAuthorityRegistry(policyWith(dup)), "duplicate_registry_entry");

  throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({
    field: "dup_source",
    permitted_sources: [
      { source_system: SOURCES.neon, direction: "inbound" },
      { source_system: SOURCES.neon, direction: "outbound" },
    ],
  }))), "duplicate_permitted_source");

  throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({
    field: "absent_owner", owner_source: SOURCES.sf,
    permitted_sources: [{ source_system: SOURCES.neon, direction: "inbound" }],
  }))), "owner_source_not_permitted");

  // An owner that may only be written TO could never establish the field.
  throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({
    field: "mute_owner",
    permitted_sources: [{ source_system: SOURCES.neon, direction: "outbound" }],
  }))), "owner_source_cannot_write");
  throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({
    field: "silent_owner",
    permitted_sources: [{ source_system: SOURCES.neon, direction: "none" }],
  }))), "owner_source_cannot_write");
});

test("Q054: an opaque version order must be declared, complete and used", () => {
  throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({
    field: "no_order", version_comparator: "declared_order",
  }))), "missing_version_order");

  throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({
    field: "dup_order", version_comparator: "declared_order",
    version_order: ["a", "b", "a"],
  }))), "duplicate_version_token");

  // Policy that is never read is policy nobody can rely on.
  throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({
    field: "stray_order", version_comparator: "integer_sequence", version_order: ["a", "b"],
  }))), "unused_version_order");

  const ok = compileFieldAuthorityRegistry(policyOf(entry({
    field: "ordered", version_comparator: "declared_order", version_order: ["a", "b", "c"],
  })));
  assert.deepEqual(ok.entries[0].version_order, ["a", "b", "c"]);
});

test("Q054: unregistered vocabulary values throw rather than being guessed", () => {
  const cases = [
    [{ field: "x", authoritative_home: "sharepoint" }, "unknown_home"],
    [{ field: "x", permitted_sources: [{ source_system: SOURCES.neon, direction: "sideways" }] },
      "unknown_write_direction"],
    [{ field: "x", version_comparator: "vibes" }, "unknown_version_comparator"],
    [{ field: "x", conflict_behavior: "last_write_wins" }, "unknown_conflict_behavior"],
    [{ field: "x", taint_class: "probably_fine" }, "unknown_taint_class"],
    [{ field: "x", sensitivity_classes: ["invented_class"] }, "unknown_data_class"],
  ];
  for (const [override, code] of cases) {
    throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry(override))), code, JSON.stringify(override));
  }
  throwsCode(() => compileFieldAuthorityRegistry({ ...REGISTRY_POLICY, schema_version: "other.v9" }),
    "unknown_schema_version");
  throwsCode(() => compileFieldAuthorityRegistry({ ...REGISTRY_POLICY, tenant: "other" }), "tenant_mismatch");
  throwsCode(() => compileFieldAuthorityRegistry({ ...REGISTRY_POLICY, entries: [] }), "invalid_shape");
  throwsCode(() => compileFieldAuthorityRegistry({ ...REGISTRY_POLICY, registry_version: 0 }),
    "invalid_shape");
  throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({ field: "x", surprise: true }))),
    "unknown_field");
});

test("MUTATION KILL (privacy): a registry entry declaring a prohibited class refuses at compile", () => {
  for (const phiClass of ["phi", "raw_patient_location", "patient_identifier"]) {
    throwsCode(() => compileFieldAuthorityRegistry(policyOf(entry({
      field: "patient_thing", sensitivity_classes: [phiClass],
    }))), "registry_entry_privacy_refused", phiClass);
  }
  // The aggregate class is neither refused nor silently permitted: it compiles
  // carrying the independent-route marker S01 assigns it.
  const routed = compileFieldAuthorityRegistry(policyOf(entry({
    field: "heatmap_input", sensitivity_classes: ["aggregate_patient_location_heatmap"],
  })));
  assert.equal(routed.entries[0].privacy_route, "needs_independent_privacy_route");
  assert.equal(REGISTRY.entries[0].privacy_route, "permitted");
});

test("Q054: only a registry this module compiled and nobody edited is accepted", () => {
  // A raw policy carries no unknown key — it is simply not compiled, and the
  // compile markers it lacks are what say so.
  throwsCode(() => resolveObservation({
    tenant: ORGANIZATION_TENANT_ID, registry: REGISTRY_POLICY, observation: observation(), now: NOW,
  }), "missing_field");

  const forged = { ...REGISTRY, compiled: false };
  throwsCode(() => resolveObservation({
    tenant: ORGANIZATION_TENANT_ID, registry: forged, observation: observation(), now: NOW,
  }), "registry_not_compiled");

  // An edited registry that keeps the old digest is caught because the digest is
  // recomputed rather than trusted. This is what stops a caller fabricating the
  // compiled shape to route around policy validation.
  const edited = {
    ...REGISTRY,
    entries: REGISTRY.entries.map(e => e.field === "commission_amount"
      ? { ...e, owner_source: SOURCES.future,
          permitted_sources: [...e.permitted_sources, { source_system: SOURCES.future, direction: "inbound" }] }
      : e),
  };
  throwsCode(() => resolveObservation({
    tenant: ORGANIZATION_TENANT_ID, registry: edited, observation: observation(), now: NOW,
  }), "registry_digest_mismatch");
});

test("MUTATION KILL (owner): a compiled registry is a copy, so mutating the policy afterwards changes nothing", () => {
  const mutable = {
    schema_version: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
    registry_version: 1,
    tenant: ORGANIZATION_TENANT_ID,
    entries: [entry({
      entity: "deal", field: "commission_amount", authoritative_home: "salesforce",
      owner_source: SOURCES.sf,
      permitted_sources: [{ source_system: SOURCES.sf, direction: "inbound" }],
      requires_account_identity: true, requires_native_identity: true, readback_required: true,
      taint_class: "corporate_source_of_record",
    })],
  };
  const compiled = compileFieldAuthorityRegistry(mutable);
  const before = compiled.registry_digest;

  // Every hostile edit a caller could make to its own object after validation.
  mutable.entries[0].owner_source = SOURCES.future;
  mutable.entries[0].permitted_sources.push({ source_system: SOURCES.future, direction: "inbound" });
  mutable.entries[0].conflict_behavior = "refuse";
  mutable.entries.push(entry({ field: "smuggled" }));
  mutable.registry_version = 99;

  assert.equal(compiled.registry_digest, before);
  assert.equal(compiled.entries.length, 1);
  assert.equal(compiled.entries[0].owner_source, SOURCES.sf);
  assert.equal(compiled.entries[0].permitted_sources.length, 1);
  assert.equal(compiled.registry_version, 1);
  // And the compiled copy itself cannot be edited in place either.
  assert.throws(() => { compiled.entries[0].owner_source = SOURCES.future; }, TypeError);
});

// ===========================================================================
// Q052 / Q054 — resolving an observation, and the three records it produces.
// ===========================================================================

function resolveFor(obs, current = null) {
  return resolveObservation({
    tenant: ORGANIZATION_TENANT_ID, registry: REGISTRY, now: NOW,
    observation: obs, current_state: current,
  });
}

function sharedNote(overrides = {}) {
  return {
    entity: "deal", field: "shared_note", tenant: ORGANIZATION_TENANT_ID,
    source_system: SOURCES.neon, value_digest: VALUE_A, version: 5,
    observed_at: T.mid, provenance: { ...PROVENANCE },
    ...overrides,
  };
}
function sharedNoteState(overrides = {}) {
  return {
    entity: "deal", field: "shared_note", tenant: ORGANIZATION_TENANT_ID,
    value_digest: VALUE_B, version: 4, owner_source: SOURCES.neon,
    observed_at: T.early, event_seq: 3, last_event_digest: D(9),
    ...overrides,
  };
}

test("Q052: the owner establishes a field and gets all three bound records", () => {
  const result = resolveFor(observation());
  assert.equal(result.decision, "accept");
  assert.equal(result.reason_id, "field_established");
  assert.equal(result.applied, true);
  assert.equal(result.silent_last_write_wins, false);

  const { current_state_transition: t, event: e, mutation_receipt: r } = result;
  assert.equal(t.record_kind, "current_state_transition");
  assert.equal(e.record_kind, "append_only_event");
  assert.equal(r.record_kind, "mutation_receipt");
  assert.deepEqual([t, e, r].map(x => x.record_kind).sort(), [...V5_F01_RECORD_KINDS].sort());

  assert.equal(t.from_value_digest, null);
  assert.equal(t.to_value_digest, VALUE_A);
  assert.equal(t.from_version, null);
  assert.equal(t.to_version, 5);
  assert.equal(t.authoritative_home, "salesforce");

  assert.equal(e.event_kind, "source_field_established");
  assert.equal(e.event_seq, 1);
  assert.equal(e.previous_event_digest, null);
  assert.equal(e.append_only, true);
  assert.equal(e.rewrites_prior_event, false);

  // The handler derives the actor. The receipt says so rather than leaving a
  // slot for whoever calls next to fill.
  assert.equal(r.actor, null);
  assert.equal(r.actor_derived_by, "authenticated_handler_context");
});

test("MUTATION KILL (freshness): the owner's newer value is accepted and the transition carries both ends", () => {
  const result = resolveFor(observation({ version: 5, value_digest: VALUE_A }), currentState());
  assert.equal(result.decision, "accept");
  assert.equal(result.reason_id, "owner_value_updated");
  assert.equal(result.version_ordering, "newer");
  assert.equal(result.current_state_transition.from_value_digest, VALUE_B);
  assert.equal(result.current_state_transition.to_value_digest, VALUE_A);
  assert.equal(result.current_state_transition.from_version, 4);
  assert.equal(result.current_state_transition.to_version, 5);
  // The event extends the chain rather than rewriting it.
  assert.equal(result.event.event_seq, 8);
  assert.equal(result.event.previous_event_digest, D(9));
  assert.equal(result.event.event_kind, "source_field_observed");

  // A version bump with the same value is still a real change to the record.
  const bumped = resolveFor(observation({ version: 6, value_digest: VALUE_B }), currentState());
  assert.equal(bumped.decision, "accept");
  assert.equal(bumped.reason_id, "owner_version_advanced");
  assert.equal(bumped.current_state_transition.to_version, 6);
});

test("Q052: none of the three records substitutes for another", () => {
  const { current_state_transition: t, event: e, mutation_receipt: r, separation } =
    resolveFor(observation(), currentState());

  const td = digest(t), ed = digest(e), rd = digest(r);
  assert.equal(new Set([td, ed, rd]).size, 3, "the three preimages must be distinct");
  assert.equal(separation.current_state_transition_digest, td);
  assert.equal(separation.event_digest, ed);
  assert.equal(separation.mutation_receipt_digest, rd);
  assert.equal(separation.any_one_substitutes_for_another, false);

  // The receipt binds BOTH other records; neither of them binds the receipt.
  // That asymmetry is what makes the separation checkable rather than promised.
  assert.equal(r.current_state_transition_digest, td);
  assert.equal(r.event_digest, ed);
  const receiptOnlyFields = ["current_state_transition_digest", "event_digest", "domain_policy_digest"];
  for (const field of receiptOnlyFields) {
    assert.ok(!(field in t), `${field} must not appear on the transition`);
    assert.ok(!(field in e), `${field} must not appear on the event`);
  }
  assert.ok(!canonicalJson(t).includes(rd));
  assert.ok(!canonicalJson(e).includes(rd));
  for (const record of [t, e, r]) assert.equal(record.alone_sufficient, false);

  assert.equal(r.domain_policy_digest, v5F01PolicyDigest());
  assert.equal(r.registry_digest, REGISTRY.registry_digest);
});

test("MUTATION KILL (freshness): a stale observation refuses and changes nothing", () => {
  const result = resolveFor(observation({ version: 3 }), currentState());
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "stale_observation_refused");
  assert.equal(result.version_ordering, "older");
  assert.equal(result.applied, false);
  assert.equal(result.current_state_transition, null);
  assert.equal(result.event, null);
  assert.equal(result.mutation_receipt, null);
});

test("Q054: an equal version with a different value becomes a visible reconciliation item", () => {
  const reconciled = resolveFor(observation({ version: 4, value_digest: VALUE_C }), currentState());
  assert.equal(reconciled.decision, "reconcile");
  assert.equal(reconciled.reason_id, "equal_version_contradiction");
  assert.equal(reconciled.applied, false);
  assert.equal(reconciled.conflict_behavior, "reconcile");
  const item = reconciled.reconciliation_item;
  assert.equal(item.conflict_kind, "equal_version_contradiction");
  assert.equal(item.human_resolver_class, "deal_owner");
  assert.equal(item.visible, true);
  assert.equal(item.applied, false);
  assert.equal(item.resolved_by_machine, false);
  assert.equal(item.established.value_digest, VALUE_B);
  assert.equal(item.observed.value_digest, VALUE_C);

  // The same contradiction on a field whose policy says refuse, refuses — and
  // still carries the item, because a conflict nobody can see is worse than one
  // that blocks.
  const strict = resolveFor({
    entity: "deal", field: "close_date", tenant: ORGANIZATION_TENANT_ID,
    source_system: SOURCES.sf, account: "acct-synthetic-1",
    native_identity: { ...NATIVE }, value_digest: VALUE_C, version: T.early,
    observed_at: T.mid, provenance: { ...PROVENANCE },
  }, {
    entity: "deal", field: "close_date", tenant: ORGANIZATION_TENANT_ID,
    account: "acct-synthetic-1", native_identity: { ...NATIVE },
    value_digest: VALUE_B, version: T.early, owner_source: SOURCES.sf,
    observed_at: T.early, event_seq: 1, last_event_digest: D(9),
  });
  assert.equal(strict.decision, "refuse");
  assert.equal(strict.reason_id, "equal_version_contradiction");
  assert.equal(strict.conflict_behavior, "refuse");
  assert.equal(strict.reconciliation_item.human_resolver_class, "system_authority");
});

test("MUTATION KILL (freshness): an opaque provider version is never ordered lexically", () => {
  const opaque = (version, value_digest) => ({
    entity: "deal", field: "provider_stage", tenant: ORGANIZATION_TENANT_ID,
    source_system: SOURCES.sf, account: "acct-synthetic-1", native_identity: { ...NATIVE },
    value_digest, version, observed_at: T.mid, provenance: { ...PROVENANCE },
  });
  const state = {
    entity: "deal", field: "provider_stage", tenant: ORGANIZATION_TENANT_ID,
    account: "acct-synthetic-1", native_identity: { ...NATIVE },
    value_digest: VALUE_B, version: "zzz-token", owner_source: SOURCES.sf,
    observed_at: T.early, event_seq: 2, last_event_digest: D(9),
  };
  // "aaa" sorts before "zzz" and "zzz9" sorts after; neither answer is knowable,
  // so both reconcile instead of one being read as newer.
  for (const token of ["aaa-token", "zzz9-token"]) {
    const result = resolveFor(opaque(token, VALUE_C), state);
    assert.equal(result.decision, "reconcile", token);
    assert.equal(result.reason_id, "opaque_version_ordering_unknowable", token);
    assert.equal(result.version_ordering, "indeterminate", token);
    assert.equal(result.applied, false, token);
  }
  // Equality IS knowable, so an identical token confirming the same value is a
  // confirmation and not a conflict.
  const same = resolveFor(opaque("zzz-token", VALUE_B), state);
  assert.equal(same.decision, "no_change");
  assert.equal(same.reason_id, "observation_confirms_established_value");
  assert.equal(same.version_ordering, "equal");

  // A DIFFERENT opaque token carrying the same value is the other confirmation:
  // the ordering stays unknowable, but there is nothing to reconcile, so it is a
  // no-change rather than a conflict manufactured out of an unreadable version.
  const unknowableButAgreeing = resolveFor(opaque("aaa-token", VALUE_B), state);
  assert.equal(unknowableButAgreeing.decision, "no_change");
  assert.equal(unknowableButAgreeing.reason_id, "opaque_version_confirms_established_value");
  assert.equal(unknowableButAgreeing.version_ordering, "indeterminate");

  // A declared order supplied by policy DOES order, and a token outside it throws
  // rather than being placed by guesswork.
  const lifecycle = (version, value_digest) => ({
    entity: "deal", field: "lifecycle", tenant: ORGANIZATION_TENANT_ID,
    source_system: SOURCES.sf, account: "acct-synthetic-1", native_identity: { ...NATIVE },
    value_digest, version, observed_at: T.mid, provenance: { ...PROVENANCE },
  });
  const lifecycleState = {
    entity: "deal", field: "lifecycle", tenant: ORGANIZATION_TENANT_ID,
    account: "acct-synthetic-1", native_identity: { ...NATIVE },
    value_digest: VALUE_B, version: "beta", owner_source: SOURCES.sf,
    observed_at: T.early, event_seq: 1, last_event_digest: D(9),
  };
  assert.equal(resolveFor(lifecycle("gamma", VALUE_C), lifecycleState).decision, "accept");
  assert.equal(resolveFor(lifecycle("alpha", VALUE_C), lifecycleState).reason_id,
    "stale_observation_refused");
  throwsCode(() => resolveFor(lifecycle("omega", VALUE_C), lifecycleState),
    "version_outside_declared_order");
});

test("MUTATION KILL (direction): a source that may only be written TO cannot write back", () => {
  // neon is permitted on commission_amount, but outbound only.
  const outbound = resolveFor(observation({
    source_system: SOURCES.neon, native_identity: { ...NATIVE, source_system: SOURCES.neon },
  }), currentState());
  assert.equal(outbound.decision, "refuse");
  assert.equal(outbound.reason_id, "forbidden_write_direction");
  assert.equal(outbound.declared_direction, "outbound");

  // direction "none" refuses too, and it refuses even when the observation is
  // newer — direction is checked before freshness, on purpose.
  const none = resolveFor({
    entity: "deal", field: "close_date", tenant: ORGANIZATION_TENANT_ID,
    source_system: SOURCES.neon, account: "acct-synthetic-1",
    native_identity: { ...NATIVE, source_system: SOURCES.neon },
    value_digest: VALUE_C, version: T.late, observed_at: T.mid, provenance: { ...PROVENANCE },
  }, {
    entity: "deal", field: "close_date", tenant: ORGANIZATION_TENANT_ID,
    account: "acct-synthetic-1", native_identity: { ...NATIVE },
    value_digest: VALUE_B, version: T.early, owner_source: SOURCES.sf,
    observed_at: T.early, event_seq: 1, last_event_digest: D(9),
  });
  assert.equal(none.decision, "refuse");
  assert.equal(none.reason_id, "forbidden_write_direction");

  // A source nobody listed is refused before direction is even consulted.
  const stranger = resolveFor(observation({
    source_system: SOURCES.future, native_identity: { ...NATIVE, source_system: SOURCES.future },
  }), currentState());
  assert.equal(stranger.reason_id, "source_not_permitted");
});

test("MUTATION KILL (owner): a non-owner may confirm the owner's value but never replace it", () => {
  // Newer, permitted, inbound — and still not applied, because Salesforce does
  // not own this field. Neither source silently overwrites the other.
  const overwrite = resolveFor(
    sharedNote({ source_system: SOURCES.sf, version: 9, value_digest: VALUE_C }),
    sharedNoteState());
  assert.equal(overwrite.decision, "reconcile");
  assert.equal(overwrite.reason_id, "forbidden_overwrite_refused");
  assert.equal(overwrite.applied, false);
  assert.equal(overwrite.current_state_transition, null);
  assert.equal(overwrite.reconciliation_item.conflict_kind, "forbidden_overwrite_by_non_owner");

  // Confirming the same value at a newer version is allowed and moves nothing.
  const confirm = resolveFor(
    sharedNote({ source_system: SOURCES.sf, version: 9, value_digest: VALUE_B }),
    sharedNoteState());
  assert.equal(confirm.decision, "no_change");
  assert.equal(confirm.reason_id, "non_owner_confirmation_only");
  assert.equal(confirm.applied, false);

  // And a non-owner cannot bring a field into existence at all.
  const establish = resolveFor(sharedNote({ source_system: SOURCES.sf, version: 1 }), null);
  assert.equal(establish.decision, "refuse");
  assert.equal(establish.reason_id, "non_owner_cannot_establish_field");
  assert.equal(establish.reconciliation_item.conflict_kind, "non_owner_establishment_attempt");

  // The owner on the same field does apply.
  const owner = resolveFor(sharedNote({ version: 9, value_digest: VALUE_C }), sharedNoteState());
  assert.equal(owner.decision, "accept");
});

test("MUTATION KILL (account): cross-tenant and cross-account observations refuse", () => {
  const crossTenant = resolveFor(observation({ tenant: "other-tenant" }), currentState());
  assert.equal(crossTenant.decision, "refuse");
  assert.equal(crossTenant.reason_id, "cross_tenant_refused");

  const stateElsewhere = resolveFor(observation(), currentState({ tenant: "other-tenant" }));
  assert.equal(stateElsewhere.reason_id, "cross_tenant_refused");

  const crossAccount = resolveFor(observation({ account: "acct-synthetic-2" }), currentState());
  assert.equal(crossAccount.decision, "refuse");
  assert.equal(crossAccount.reason_id, "cross_account_refused");
  assert.equal(crossAccount.established_account, "acct-synthetic-1");
  assert.equal(crossAccount.observed_account, "acct-synthetic-2");
  assert.equal(crossAccount.applied, false);

  // The request itself is refused for a tenant this module does not serve.
  throwsCode(() => resolveObservation({
    tenant: "other-tenant", registry: REGISTRY, observation: observation(), now: NOW,
  }), "tenant_mismatch");
});

test("Q054: a recycled native id is a different record wearing an old name", () => {
  const recycled = resolveFor(
    observation({ native_identity: { ...NATIVE, native_id_epoch: "epoch-2" } }), currentState());
  assert.equal(recycled.decision, "refuse");
  assert.equal(recycled.reason_id, "recycled_native_id_refused");
  assert.equal(recycled.established_epoch, "epoch-1");
  assert.equal(recycled.observed_epoch, "epoch-2");

  const otherRecord = resolveFor(
    observation({ native_identity: { ...NATIVE, native_id: "006SYNTHETIC0002" } }), currentState());
  assert.equal(otherRecord.reason_id, "native_id_mismatch");

  // The declared native-identity source must be the observing source.
  const mismatched = resolveFor(
    observation({ native_identity: { ...NATIVE, source_system: SOURCES.outlook } }), currentState());
  assert.equal(mismatched.reason_id, "native_identity_source_mismatch");
});

test("Q054: every piece of required identity is refused by its own name when absent", () => {
  const cases = [
    [{ version: undefined }, "missing_version"],
    [{ observed_at: undefined }, "missing_observed_time"],
    [{ provenance: undefined }, "missing_provenance"],
    [{ account: undefined }, "missing_account_identity"],
    [{ native_identity: undefined }, "missing_native_identity"],
  ];
  for (const [override, reason_id] of cases) {
    const result = resolveFor(observation(override), currentState());
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
    assert.equal(result.applied, false);
  }
  // An unknown field binding is refused rather than invented.
  const unknown = resolveFor(observation({ field: "invented_field" }), null);
  assert.equal(unknown.decision, "refuse");
  assert.equal(unknown.reason_id, "unknown_field_binding");
  assert.equal(unknown.authoritative_home, null);
});

test("Q054: a required readback must be present, confirmed and about this value", () => {
  const missing = resolveFor(observation({ readback: undefined }), currentState());
  assert.equal(missing.reason_id, "missing_readback");
  const unconfirmed = resolveFor(observation({
    readback: { confirmed: false, readback_at: T.mid, readback_value_digest: VALUE_A },
  }), currentState());
  assert.equal(unconfirmed.reason_id, "readback_not_confirmed");
  const wrongValue = resolveFor(observation({
    readback: { confirmed: true, readback_at: T.mid, readback_value_digest: VALUE_C },
  }), currentState());
  assert.equal(wrongValue.reason_id, "readback_value_mismatch");
  // A field whose policy does not need one is unaffected.
  assert.equal(resolveFor(sharedNote(), sharedNoteState()).decision, "accept");
});

test("Q054: an observation dated after now is unreadable, not fresher", () => {
  const result = resolveFor(observation({ observed_at: T.future }), currentState());
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "observation_after_now");
});

test("MUTATION KILL (privacy): S01 decides on the observation's declared classes too", () => {
  const phi = resolveFor(observation({ declared_data_classes: ["phi"] }), currentState());
  assert.equal(phi.decision, "refuse");
  assert.equal(phi.reason_id, "phi_or_raw_patient_location_refused");
  assert.deepEqual(phi.prohibited_classes, ["phi"]);
  assert.equal(phi.applied, false);
  assert.equal(phi.current_state_transition, null);

  const raw = resolveFor(observation({ declared_data_classes: ["raw_patient_location"] }), currentState());
  assert.equal(raw.reason_id, "phi_or_raw_patient_location_refused");

  // The aggregate class routes independently; it is neither refused nor allowed
  // here, and this module produces none of the evidence it asks for.
  const aggregate = resolveFor(
    observation({ declared_data_classes: ["aggregate_patient_location_heatmap"] }), currentState());
  assert.equal(aggregate.decision, "needs_independent_privacy_route");
  assert.equal(aggregate.required_evidence,
    evaluatePrivacyBoundary({ data_classes: ["aggregate_patient_location_heatmap"] }).required_evidence);
  assert.equal(aggregate.applied, false);
});

test("Q052: no input reaches accept by being the last writer", () => {
  // Every shape that could plausibly be resolved by preferring the newest caller
  // is enumerated here, and none of them applies.
  const nonApplying = [
    resolveFor(observation({ version: 3 }), currentState()),                       // stale
    resolveFor(observation({ version: 4, value_digest: VALUE_C }), currentState()), // equal, differing
    resolveFor(sharedNote({ source_system: SOURCES.sf, version: 9, value_digest: VALUE_C }),
      sharedNoteState()),                                                          // non-owner, newer
    resolveFor(sharedNote({ source_system: SOURCES.sf, version: 1 }), null),        // non-owner establish
  ];
  for (const result of nonApplying) {
    assert.equal(result.applied, false, result.reason_id);
    assert.equal(result.silent_last_write_wins, false, result.reason_id);
    assert.equal(result.current_state_transition, null, result.reason_id);
    assert.equal(result.event, null, result.reason_id);
    assert.equal(result.mutation_receipt, null, result.reason_id);
    assert.notEqual(result.decision, "accept", result.reason_id);
  }
});

// ===========================================================================
// Q071 — source-agnostic immutable artifacts and reviewable parsed proposals.
// ===========================================================================

function artifactOf(overrides = {}) {
  return {
    source_system: SOURCES.sf, source_class: "corporate_crm",
    source_account: "acct-synthetic-1",
    native_identity: { ...NATIVE },
    native_version: "rev-1",
    content_digest: VALUE_A, byte_length: 2048,
    observed_at: T.mid, provenance: { ...PROVENANCE },
    evidence_class: "corporate_record_export",
    taint_class: "corporate_source_of_record",
    declared_data_classes: ["lease_economics"],
    ...overrides,
  };
}
function admit(overrides = {}, prior_artifact = null) {
  return admitCorporateArtifact({
    tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(overrides), now: NOW, prior_artifact,
  });
}

test("Q071: Salesforce, OneDrive and a source nobody has named yet use one contract", () => {
  const cases = [
    { source_system: SOURCES.sf, evidence_class: "corporate_field_snapshot" },
    { source_system: SOURCES.onedrive, evidence_class: "corporate_document_bytes" },
    { source_system: SOURCES.outlook, evidence_class: "corporate_mailbox_item" },
    { source_system: SOURCES.future, evidence_class: "corporate_record_export" },
  ];
  const digests = new Set();
  for (const override of cases) {
    const result = admit({
      ...override, native_identity: { ...NATIVE, source_system: override.source_system },
    });
    assert.equal(result.decision, "allow", override.source_system);
    assert.equal(result.reason_id, "artifact_admitted_as_evidence");
    // Evidence is never itself a fact and never makes a field authoritative.
    assert.equal(result.is_fact, false);
    assert.equal(result.makes_field_authoritative, false);
    assert.equal(result.immutable, true);
    assert.ok(result.artifact_digest.startsWith("sha256:"));
    digests.add(result.artifact_digest);

    // Round trip: every declared identity field comes back unchanged.
    assert.equal(result.round_trip.source_system, override.source_system);
    assert.equal(result.round_trip.source_account, "acct-synthetic-1");
    assert.equal(result.round_trip.native_version, "rev-1");
    assert.equal(result.round_trip.content_digest, VALUE_A);
    assert.equal(result.round_trip.byte_length, 2048);
    assert.equal(result.round_trip.observed_at, T.mid);
    assert.deepEqual(result.round_trip.provenance, PROVENANCE);
    assert.equal(result.round_trip.tenant, ORGANIZATION_TENANT_ID);
  }
  assert.equal(digests.size, 4, "four distinct sources, four distinct artifact identities");
  // The digest is deterministic for the same bytes.
  assert.equal(admit().artifact_digest, admit().artifact_digest);
  // No live adapter was needed to prove any of it.
  assert.equal(v5F01PolicyPreimage().corporate_sources.live_adapter_included, false);
});

test("MUTATION KILL (proposal): Tour evidence can never certify a generic corporate fact", () => {
  for (const evidence_class of V5_F01_TOUR_ONLY_EVIDENCE_CLASSES) {
    const result = admit({ evidence_class });
    assert.equal(result.decision, "refuse", evidence_class);
    assert.equal(result.reason_id, "tour_only_evidence_not_generic_authority", evidence_class);
    assert.equal(result.artifact, null);
  }
  // A class that is neither a corporate one nor a Tour one is unreadable, not
  // merely unauthorised.
  throwsCode(() => admit({ evidence_class: "someones_spreadsheet" }), "unknown_evidence_class");
  for (const evidence_class of V5_F01_EVIDENCE_CLASSES) {
    assert.equal(admit({ evidence_class }).decision, "allow", evidence_class);
  }
});

test("Q071: an artifact missing any part of its identity refuses by name", () => {
  const cases = [
    [{ source_account: undefined }, "missing_source_account"],
    [{ native_identity: undefined }, "missing_native_identity"],
    [{ native_version: undefined }, "missing_native_version"],
    [{ observed_at: undefined }, "missing_observed_time"],
    [{ provenance: undefined }, "missing_provenance"],
  ];
  for (const [override, reason_id] of cases) {
    const result = admit(override);
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
  }
  throwsCode(() => admitCorporateArtifact({
    tenant: "other-tenant", artifact: artifactOf(), now: NOW,
  }), "tenant_mismatch");
  assert.equal(admit({ observed_at: T.future }).reason_id, "observation_after_now");
  assert.equal(admit({ native_identity: { ...NATIVE, source_system: SOURCES.outlook } }).reason_id,
    "native_identity_source_mismatch");
});

test("Q071: immutable means one identity never describes two sets of bytes", () => {
  const prior = artifactOf({ content_digest: VALUE_A });
  const conflict = admit({ content_digest: VALUE_C }, prior);
  assert.equal(conflict.decision, "refuse");
  assert.equal(conflict.reason_id, "artifact_identity_conflict");
  assert.equal(conflict.established_content_digest, VALUE_A);
  assert.equal(conflict.observed_content_digest, VALUE_C);

  // Re-admitting the same bytes under the same identity is fine.
  assert.equal(admit({ content_digest: VALUE_A }, prior).decision, "allow");
  // A new native version is a new artifact, not a conflict.
  assert.equal(admit({ content_digest: VALUE_C, native_version: "rev-2" }, prior).decision, "allow");
});

test("MUTATION KILL (privacy): an artifact declaring a prohibited class refuses; aggregate routes", () => {
  const phi = admit({ declared_data_classes: ["patient_record"] });
  assert.equal(phi.decision, "refuse");
  assert.equal(phi.reason_id, "phi_or_raw_patient_location_refused");
  const routed = admit({ declared_data_classes: ["aggregate_patient_volume_estimate"] });
  assert.equal(routed.decision, "needs_independent_privacy_route");
  assert.equal(routed.artifact, null);
  assert.equal(admit({ declared_data_classes: ["market_comp"] }).decision, "allow");
});

function proposalOf(overrides = {}) {
  return {
    artifact_digest: D(7), source_system: SOURCES.sf, source_account: "acct-synthetic-1",
    proposed_bindings: [{
      entity: "deal", field: "commission_amount", value_digest: VALUE_C, version: 9,
    }],
    confidence: 0.82, evidence_refs: ["synthetic-evidence-0001"], observed_at: T.mid,
    ...overrides,
  };
}
function propose(overrides = {}) {
  return evaluateParsedProposal({
    tenant: ORGANIZATION_TENANT_ID, registry: REGISTRY, proposal: proposalOf(overrides), now: NOW,
  });
}

test("Q071: a parsed proposal is reviewable, evidence-scored, reversible and never a fact", () => {
  const result = propose();
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "proposal_reviewable_only");
  // Asserted on the ALLOWED result, which is the whole point: allowed here means
  // worth a human's review, never true.
  assert.equal(result.becomes_fact, false);
  assert.equal(result.advances_state, false);
  assert.equal(result.carries_effect_authority, false);
  assert.equal(result.requires_human_review, true);

  assert.equal(result.link.confidence, 0.82);
  assert.deepEqual(result.link.evidence_refs, ["synthetic-evidence-0001"]);
  assert.equal(result.link.reversible, true);
  assert.equal(result.link.history_preserved, true);
  assert.equal(result.link.supersedes_link_digest, null);
  assert.equal(result.link.proposed_bindings[0].human_resolver_class, "deal_owner");
  assert.equal(result.link.registry_digest, REGISTRY.registry_digest);
  assert.ok(result.proposal_digest.startsWith("sha256:"));

  // Superseding names the link it replaces rather than erasing it.
  const superseding = propose({ supersedes_link_digest: D(5) });
  assert.equal(superseding.link.supersedes_link_digest, D(5));
  assert.equal(superseding.link.history_preserved, true);
  assert.notEqual(superseding.proposal_digest, result.proposal_digest);
});

test("MUTATION KILL (proposal): a proposal that reaches past review refuses by name", () => {
  const effectBearing = [
    { apply: true }, { commit: true }, { execute: true }, { auto_accept: true },
    { treat_as_fact: true }, { authoritative: true }, { is_fact: true },
    { advance_state: true }, { effect_ref: "x" }, { publish: true }, { send_now: true },
    { accepted_by_review: true }, { deploy: true }, { activate: true },
  ];
  for (const extra of effectBearing) {
    const result = propose(extra);
    assert.equal(result.decision, "refuse", JSON.stringify(extra));
    assert.equal(result.reason_id, "effect_bearing_proposal_refused", JSON.stringify(extra));
    assert.equal(result.offending_field, Object.keys(extra)[0]);
    assert.equal(result.becomes_fact, false);
    assert.equal(result.link, null);
  }
  // A field that is merely unknown is still refused, just not as an effect.
  throwsCode(() => propose({ notes: "hello" }), "unknown_field");
});

test("Q071: a proposal cannot invent a field binding, and its provenance is mandatory", () => {
  const unknown = propose({
    proposed_bindings: [{ entity: "deal", field: "invented", value_digest: VALUE_C, version: 1 }],
  });
  assert.equal(unknown.decision, "refuse");
  assert.equal(unknown.reason_id, "unknown_field_binding");
  assert.equal(unknown.link, null);

  for (const key of ["artifact_digest", "source_account", "evidence_refs", "observed_at", "confidence"]) {
    throwsCode(() => propose({ [key]: undefined }), "missing_field", key);
  }
  throwsCode(() => propose({ confidence: 1.5 }), "invalid_shape");
  throwsCode(() => propose({ evidence_refs: [] }), "invalid_shape");
  throwsCode(() => propose({
    proposed_bindings: [
      { entity: "deal", field: "next_step", value_digest: VALUE_C, version: 1 },
      { entity: "deal", field: "next_step", value_digest: VALUE_A, version: 2 },
    ],
  }), "duplicate_proposed_binding");
  throwsCode(() => propose({ artifact_digest: "not-a-digest" }), "invalid_digest");
  assert.equal(propose({ observed_at: T.future }).reason_id, "observation_after_now");
  throwsCode(() => evaluateParsedProposal({
    tenant: "other-tenant", registry: REGISTRY, proposal: proposalOf(), now: NOW,
  }), "tenant_mismatch");
});

// ===========================================================================
// Q125 / Q135 — document identity, five state axes, and the official copy.
// ===========================================================================

const NEON_ID = Object.freeze({
  document_id: "doc-synthetic-1", content_digest: VALUE_A, version_no: 3,
});
const STORAGE_ID = Object.freeze({
  object_key: "drafts/doc-synthetic-1/v3", content_digest: VALUE_A, byte_length: 4096, sealed: true,
});
const ONEDRIVE_ID = Object.freeze({
  drive_id: "drive-synthetic-1", item_id: "item!synthetic-1", content_digest: VALUE_A,
  filing_state: "filed",
});

function documentOf(overrides = {}) {
  return {
    document_class: "lease",
    neon_identity: { ...NEON_ID },
    object_storage_identity: { ...STORAGE_ID },
    onedrive_identity: { ...ONEDRIVE_ID },
    preparation_state: "approved_for_delivery",
    delivery_state: "delivered",
    signature_state: "fully_executed",
    validity_state: "effective",
    version_state: "current",
    ...overrides,
  };
}
function projectDoc(overrides = {}) {
  return projectDocumentIdentity({ tenant: ORGANIZATION_TENANT_ID, document: documentOf(overrides) });
}

test("Q135: all five state axes and all three identities round-trip unchanged", () => {
  const declared = documentOf();
  const result = projectDocumentIdentity({ tenant: ORGANIZATION_TENANT_ID, document: declared });
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "document_identity_and_states_coherent");

  // The readback is the exact declared shape, field for field.
  assert.deepEqual(result.readback, declared);
  for (const axis of ["preparation_state", "delivery_state", "signature_state",
    "validity_state", "version_state"]) {
    assert.equal(result[axis], declared[axis], axis);
  }
  assert.deepEqual(result.neon_identity, NEON_ID);
  assert.deepEqual(result.object_storage_identity, STORAGE_ID);
  assert.deepEqual(result.onedrive_identity, ONEDRIVE_ID);

  // The identity split is stated, not implied.
  assert.deepEqual(result.homes, {
    identity_and_state: "neon_record_layer",
    working_and_sealed_bytes: "object_storage",
    official_executed_copy: "onedrive",
  });
  assert.ok(result.document_digest.startsWith("sha256:"));

  // Every registered value on every axis is reachable through some coherent
  // document, so no state in the vocabulary is decorative.
  const reached = {
    preparation_state: new Set(), delivery_state: new Set(), signature_state: new Set(),
    validity_state: new Set(), version_state: new Set(),
  };
  const coherent = [
    { preparation_state: "not_started", delivery_state: "undelivered", signature_state: "unsigned",
      validity_state: "draft", version_state: "current", onedrive_identity: null },
    { preparation_state: "drafting", delivery_state: "undelivered", signature_state: "unsigned",
      validity_state: "draft", version_state: "withdrawn", onedrive_identity: null },
    { preparation_state: "ready_for_review", delivery_state: "undelivered", signature_state: "unsigned",
      validity_state: "void", version_state: "superseded", onedrive_identity: null },
    { preparation_state: "approved_for_delivery", delivery_state: "delivery_failed",
      signature_state: "unsigned", validity_state: "draft", version_state: "current",
      onedrive_identity: null },
    { preparation_state: "approved_for_delivery", delivery_state: "delivered",
      signature_state: "partially_signed", validity_state: "draft", version_state: "current",
      onedrive_identity: null },
    { preparation_state: "approved_for_delivery", delivery_state: "delivered",
      signature_state: "signature_declined", validity_state: "void", version_state: "current",
      onedrive_identity: null },
    { preparation_state: "approved_for_delivery", delivery_state: "delivered",
      signature_state: "fully_executed", validity_state: "effective", version_state: "current" },
    { preparation_state: "approved_for_delivery", delivery_state: "delivered",
      signature_state: "fully_executed", validity_state: "expired", version_state: "superseded" },
    { preparation_state: "approved_for_delivery", delivery_state: "delivered",
      signature_state: "fully_executed", validity_state: "superseded", version_state: "superseded" },
  ];
  for (const states of coherent) {
    const projection = projectDoc(states);
    assert.equal(projection.decision, "allow", JSON.stringify(states));
    for (const axis of Object.keys(reached)) reached[axis].add(projection[axis]);
  }
  assert.deepEqual([...reached.preparation_state].sort(), [...V5_F01_PREPARATION_STATES].sort());
  assert.deepEqual([...reached.delivery_state].sort(), [...V5_F01_DELIVERY_STATES].sort());
  assert.deepEqual([...reached.signature_state].sort(), [...V5_F01_SIGNATURE_STATES].sort());
  assert.deepEqual([...reached.validity_state].sort(), [...V5_F01_VALIDITY_STATES].sort());
  assert.deepEqual([...reached.version_state].sort(), [...V5_F01_VERSION_STATES].sort());
});

test("MUTATION KILL (document): a missing official copy is visibly incomplete, never inferred away", () => {
  // Absent, pending and failed each leave the official filing incomplete, and
  // the Neon record and the sealed object-storage copy are both present and
  // healthy in every one of these cases. That is exactly the inference Q125
  // forbids, so it refuses rather than reporting success with a caveat.
  for (const [onedrive_identity, filing] of [
    [null, "absent"],
    [{ ...ONEDRIVE_ID, filing_state: "pending" }, "pending"],
    [{ ...ONEDRIVE_ID, filing_state: "failed" }, "failed"],
  ]) {
    const result = projectDoc({ onedrive_identity });
    assert.equal(result.decision, "refuse", filing);
    assert.equal(result.reason_id, "incomplete_official_filing", filing);
    assert.equal(result.official_filing_state, "incomplete_official_filing", filing);
    assert.equal(result.official_copy_required, true, filing);
    assert.equal(result.official_copy_filing_state, filing, filing);
    // Both homes succeeded and neither implies the filing happened.
    assert.deepEqual(result.object_storage_identity, STORAGE_ID, filing);
    assert.deepEqual(result.neon_identity, NEON_ID, filing);
    assert.equal(result.object_storage_success_implies_official_filing, false, filing);
    assert.equal(result.neon_success_implies_official_filing, false, filing);
  }
  // A document that is not fully executed needs no official copy yet.
  const unsigned = projectDoc({
    signature_state: "unsigned", validity_state: "draft", onedrive_identity: null,
  });
  assert.equal(unsigned.decision, "allow");
  assert.equal(unsigned.official_filing_state, "not_required");
  assert.equal(unsigned.official_copy_required, false);
});

test("Q135: structurally impossible state combinations refuse, each by its own constraint", () => {
  const cases = [
    [{ preparation_state: "drafting", delivery_state: "delivered", signature_state: "unsigned",
      validity_state: "draft", onedrive_identity: null },
      "delivery_requires_approved_preparation"],
    [{ delivery_state: "undelivered", signature_state: "fully_executed", validity_state: "effective" },
      "signature_requires_delivery"],
    [{ signature_state: "partially_signed", validity_state: "effective", onedrive_identity: null },
      "effective_requires_full_execution"],
    [{ validity_state: "draft" }, "draft_cannot_be_fully_executed"],
  ];
  for (const [override, violated_constraint] of cases) {
    const result = projectDoc(override);
    assert.equal(result.decision, "refuse", violated_constraint);
    assert.equal(result.reason_id, "document_state_incoherent", violated_constraint);
    assert.equal(result.violated_constraint, violated_constraint);
    assert.equal(result.official_filing_state, "not_evaluated");
  }
});

test("Q125: two homes claiming different bytes for one document is never settled by a machine", () => {
  const sealed = projectDoc({
    object_storage_identity: { ...STORAGE_ID, content_digest: VALUE_C },
  });
  assert.equal(sealed.decision, "refuse");
  assert.equal(sealed.reason_id, "sealed_bytes_digest_mismatch");

  const official = projectDoc({ onedrive_identity: { ...ONEDRIVE_ID, content_digest: VALUE_C } });
  assert.equal(official.decision, "refuse");
  assert.equal(official.reason_id, "official_copy_digest_mismatch");
  assert.equal(official.official_filing_state, "incomplete_official_filing");

  // An UNSEALED working copy is allowed to differ: it is a draft, not the record.
  const working = projectDoc({
    object_storage_identity: { ...STORAGE_ID, sealed: false, content_digest: VALUE_C },
  });
  assert.equal(working.decision, "allow");
});

test("Q135: the Neon identity is mandatory and every state value is checked", () => {
  throwsCode(() => projectDoc({ neon_identity: undefined }), "missing_field");
  throwsCode(() => projectDoc({ neon_identity: { ...NEON_ID, version_no: 0 } }), "invalid_shape");
  throwsCode(() => projectDoc({ neon_identity: { ...NEON_ID, content_digest: "nope" } }),
    "invalid_digest");
  const axes = [
    ["preparation_state", "unknown_preparation_state"],
    ["delivery_state", "unknown_delivery_state"],
    ["signature_state", "unknown_signature_state"],
    ["validity_state", "unknown_validity_state"],
    ["version_state", "unknown_version_state"],
  ];
  for (const [axis, code] of axes) throwsCode(() => projectDoc({ [axis]: "probably_fine" }), code);
  throwsCode(() => projectDoc({ onedrive_identity: { ...ONEDRIVE_ID, filing_state: "maybe" } }),
    "unknown_filing_state");
  throwsCode(() => projectDocumentIdentity({ tenant: "other", document: documentOf() }),
    "tenant_mismatch");
  throwsCode(() => projectDoc({ extra_field: 1 }), "unknown_field");
});

// ===========================================================================
// Q129 — retention, holds, deletion proof and surviving derivatives.
// ===========================================================================

const LONG_AGO = "2026-01-01T00:00:00Z";

// The coverage answer the persistence tail LOADS. Every deletion fixture below
// supplies an established one by default, because the alternative — leaving it
// out — would make every case in the section refuse for the same reason and
// prove nothing about retention. The cases that exercise unknown and absent
// coverage state it explicitly.
function coverageOf(overrides = {}) {
  return {
    state: "established",
    reason_id: "producer_closure_verified",
    registered_derivative_kinds: ["deal_economics_summary", "lease_abstract"],
    ...overrides,
  };
}

// THE RETENTION CLOCK THE PERSISTENCE TAIL LOADS, and it is a different fact from
// the artifact's `created_at` beside it. `created_at` is the SOURCE's observed
// instant — identity and provenance, and the window a deletion proof has to sit
// inside. The clock is the SERVER-STAMPED instant the record layer took custody,
// which is what the retention period actually runs from. The two are deliberately
// different values in every fixture below, so a test that measured from the wrong
// one produces a visibly wrong number of days rather than the right answer by
// coincidence.
const CUSTODY = "2026-01-08T00:00:00Z";

function clockOf(overrides = {}) {
  return {
    kind: "server_recorded_custody",
    event_kind: null,
    started_at: CUSTODY,
    reference: "ops.f01_corporate_artifact.recorded_at",
    provenance: "server_stamped_custody",
    event_digest: null,
    verified: true,
    source_observed_at_used: false,
    ...overrides,
  };
}

function subjectOf(overrides = {}) {
  return {
    artifact_class: "executed_lease", artifact_home: "onedrive", artifact_digest: VALUE_A,
    created_at: LONG_AGO,
    retention_clock: clockOf(),
    holds: [],
    deletion_proof: {
      proof_ref: "proof-synthetic-1", artifact_digest: VALUE_A, proof_digest: D(4),
      executed_at: T.late,
    },
    derivative_coverage: coverageOf(),
    derivatives: ["deal_economics_summary", "lease_abstract"],
    satisfied_constraints: ["brokerage_records_policy"],
    ...overrides,
  };
}
function deletion(overrides = {}) {
  return evaluateDeletion({
    tenant: ORGANIZATION_TENANT_ID, registry: RETENTION, subject: subjectOf(overrides), now: NOW,
  });
}

test("Q129: a compiled retention registry binds home, period, constraints, proof and derivatives", () => {
  assert.equal(RETENTION.compiled, true);
  assert.equal(RETENTION.registry_digest, digest(retentionRegistryPreimage(RETENTION)));
  assert.equal(RETENTION.classes.length, 3);
  const lease = RETENTION.classes.find(c => c.artifact_class === "executed_lease");
  assert.equal(lease.authoritative_home, "onedrive");
  assert.equal(lease.default_retention_days, 30);
  assert.deepEqual(lease.governing_constraints, ["brokerage_records_policy"]);
  assert.equal(lease.deletion_proof_required, true);
  assert.deepEqual(lease.surviving_derivatives, ["deal_economics_summary", "lease_abstract"]);
  // A class that names no clock resolves to custody on the COMPILED entry, so no
  // evaluation ever has to ask what an absent field meant.
  assert.deepEqual(lease.retention_clock, { kind: "server_recorded_custody", event_kind: null });

  const dup = { ...RETENTION_POLICY, classes: [...RETENTION_POLICY.classes, RETENTION_POLICY.classes[0]] };
  throwsCode(() => compileRetentionRegistry(dup), "duplicate_retention_class");
  throwsCode(() => compileRetentionRegistry({
    ...RETENTION_POLICY,
    classes: [{ ...RETENTION_POLICY.classes[0], authoritative_home: "sharepoint" }],
  }), "unknown_home");
  throwsCode(() => compileRetentionRegistry({
    ...RETENTION_POLICY,
    classes: [{ ...RETENTION_POLICY.classes[0], default_retention_days: -1 }],
  }), "invalid_shape");
  throwsCode(() => compileRetentionRegistry({ ...RETENTION_POLICY, tenant: "other" }), "tenant_mismatch");
  throwsCode(() => compileRetentionRegistry({ ...RETENTION_POLICY, schema_version: "x" }),
    "unknown_schema_version");
  throwsCode(() => evaluateDeletion({
    tenant: ORGANIZATION_TENANT_ID, registry: { ...RETENTION, compiled: false },
    subject: subjectOf(), now: NOW,
  }), "registry_not_compiled");
});

test("Q129: a fully satisfied deletion is permitted and names what survives", () => {
  const result = deletion();
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "deletion_permitted");
  assert.equal(result.silent_purge, false);
  assert.equal(result.purge_without_proof, false);
  assert.deepEqual(result.surviving_derivatives, ["deal_economics_summary", "lease_abstract"]);
  assert.deepEqual(result.observed_surviving_derivatives,
    ["deal_economics_summary", "lease_abstract"]);
  assert.equal(result.derivative_coverage_state, "established");

  const receipt = result.deletion_receipt;
  assert.equal(receipt.artifact_class, "executed_lease");
  assert.equal(receipt.artifact_home, "onedrive");
  assert.equal(receipt.deletion_proof_required, true);
  assert.equal(receipt.deletion_proof_ref, "proof-synthetic-1");
  assert.equal(receipt.deletion_proof_digest, D(4));
  assert.equal(receipt.retention_registry_digest, RETENTION.registry_digest);
  assert.equal(receipt.domain_policy_digest, v5F01PolicyDigest());
  assert.equal(receipt.actor, null);
  assert.equal(receipt.actor_derived_by, "authenticated_handler_context");

  // A class whose policy needs no proof and no waiting period still succeeds
  // through the same path, with the proof requirement recorded as false.
  const draft = deletion({
    artifact_class: "draft_document", artifact_home: "object_storage",
    created_at: T.late, deletion_proof: null, derivatives: [], satisfied_constraints: [],
  });
  assert.equal(draft.decision, "allow");
  assert.equal(draft.deletion_receipt.deletion_proof_required, false);
  assert.equal(draft.deletion_receipt.deletion_proof_ref, null);
});

test("MUTATION KILL (hold): an active hold blocks, and a hold nobody can read blocks too", () => {
  const active = deletion({
    holds: [{ hold_id: "hold-1", state: "active", reason: "litigation", placed_at: T.early }],
  });
  assert.equal(active.decision, "refuse");
  assert.equal(active.reason_id, "active_hold_blocks_deletion");
  assert.deepEqual(active.blocking_holds, ["hold-1"]);
  assert.equal(active.deletion_receipt, null);

  // Fail closed: an unreadable hold is not the same as no hold.
  const unknown = deletion({
    holds: [{ hold_id: "hold-2", state: "unknown", placed_at: T.early }],
  });
  assert.equal(unknown.decision, "refuse");
  assert.equal(unknown.reason_id, "unknown_hold_state_blocks_deletion");
  assert.deepEqual(unknown.blocking_holds, ["hold-2"]);

  // Released and expired holds do not block, and both appear on the receipt.
  const cleared = deletion({
    holds: [
      { hold_id: "hold-3", state: "released", placed_at: T.early, released_at: T.mid },
      { hold_id: "hold-4", state: "expired", placed_at: T.early },
    ],
  });
  assert.equal(cleared.decision, "allow");
  assert.deepEqual(cleared.deletion_receipt.released_holds, ["hold-3", "hold-4"]);

  // An active hold beats a released one in the same request.
  const mixed = deletion({
    holds: [
      { hold_id: "hold-5", state: "released", placed_at: T.early, released_at: T.mid },
      { hold_id: "hold-6", state: "active", placed_at: T.early },
    ],
  });
  assert.equal(mixed.reason_id, "active_hold_blocks_deletion");
  assert.deepEqual(mixed.blocking_holds, ["hold-6"]);

  throwsCode(() => deletion({
    holds: [
      { hold_id: "hold-7", state: "active", placed_at: T.early },
      { hold_id: "hold-7", state: "released", placed_at: T.early, released_at: T.mid },
    ],
  }), "duplicate_hold");
  throwsCode(() => deletion({ holds: [{ hold_id: "h", state: "lapsed", placed_at: T.early }] }),
    "unknown_hold_state_value");
  assert.deepEqual([...V5_F01_HOLD_STATES].sort(), ["active", "expired", "released", "unknown"]);
});

test("MUTATION KILL (hold): nothing is purged without its period, constraints, proof and derivatives", () => {
  // THE PERIOD RUNS FROM THE CUSTODY CLOCK, so the case that must refuse is one
  // whose CLOCK is recent. An earlier revision moved `created_at` here, which
  // measured the period from the instant a source said it observed the artifact.
  const tooSoon = deletion({ retention_clock: clockOf({ started_at: T.early }) });
  assert.equal(tooSoon.decision, "refuse");
  assert.equal(tooSoon.reason_id, "retention_period_not_elapsed");
  assert.equal(tooSoon.default_retention_days, 30);
  assert.equal(tooSoon.elapsed_days, 8);

  const unsatisfied = deletion({ satisfied_constraints: [] });
  assert.equal(unsatisfied.decision, "refuse");
  assert.equal(unsatisfied.reason_id, "governing_constraint_unsatisfied");
  assert.deepEqual(unsatisfied.unsatisfied_constraints, ["brokerage_records_policy"]);

  const noProof = deletion({ deletion_proof: null });
  assert.equal(noProof.decision, "refuse");
  assert.equal(noProof.reason_id, "missing_deletion_proof");

  const wrongProof = deletion({
    deletion_proof: {
      proof_ref: "proof-synthetic-2", artifact_digest: VALUE_C, proof_digest: D(4),
      executed_at: T.late,
    },
  });
  assert.equal(wrongProof.decision, "refuse");
  assert.equal(wrongProof.reason_id, "deletion_proof_mismatch");
  assert.equal(wrongProof.proof_artifact_digest, VALUE_C);

  // A derivative nobody registered blocks the deletion instead of vanishing.
  const stray = deletion({
    derivatives: ["deal_economics_summary", "lease_abstract", "shadow_copy"] });
  assert.equal(stray.decision, "refuse");
  assert.equal(stray.reason_id, "unregistered_derivative_blocks_deletion");
  assert.deepEqual(stray.unregistered_derivatives, ["shadow_copy"]);

  const wrongHome = deletion({ artifact_home: "object_storage" });
  assert.equal(wrongHome.decision, "refuse");
  assert.equal(wrongHome.reason_id, "artifact_home_mismatch");

  const unknownClass = deletion({ artifact_class: "mystery_artifact" });
  assert.equal(unknownClass.decision, "refuse");
  assert.equal(unknownClass.reason_id, "unknown_artifact_class");

  // Not one of them reported success, and not one of them produced a receipt.
  for (const result of [tooSoon, unsatisfied, noProof, wrongProof, stray, wrongHome, unknownClass]) {
    assert.equal(result.decision, "refuse", result.reason_id);
    assert.equal(result.deletion_receipt, null, result.reason_id);
    assert.equal(result.silent_purge, false, result.reason_id);
  }
});

// ===========================================================================
// WHEN A RETENTION PERIOD STARTS.
//
// The accepted rule, in the words it was accepted in: a default retention period
// starts at the SERVER-STAMPED instant the record layer took custody of the
// artifact. The source's `observed_at` stays identity and provenance and never
// becomes the clock — under that name or any other. A class may register an
// explicit retention-clock EVENT, but only a typed event with its own provenance,
// and nothing in this slice produces one, so such a class refuses rather than
// quietly measuring from custody instead.
//
// This rule was accepted directly by the user in
//   native task 01a0869f-fe0d-7493-bda3-ab8b3c0d6683
//   user turn   01a08825-f388-70a2-b1a0-18ee866edde7
// and carries no canonical decision id, because none was issued for it. It is
// named here for what it is, exactly as the registration rule below is.
// ===========================================================================

test("retention: the period runs from server custody, and a backdated observation cannot shorten it", () => {
  // THE DEFECT THIS FORECLOSES. Measuring from `created_at` means a source that
  // reports an old observation — or anything able to write one — shortens the
  // period, and an artifact backdated far enough arrives already expired. Here the
  // source claims it observed the lease in 2019; the record layer took custody
  // eight days before `now`, and the 30-day period has NOT elapsed.
  const backdated = deletion({
    created_at: "2019-01-01T00:00:00Z",
    retention_clock: clockOf({ started_at: T.early }),
  });
  assert.equal(backdated.decision, "refuse");
  assert.equal(backdated.reason_id, "retention_period_not_elapsed");
  assert.equal(backdated.elapsed_days, 8, "eight days of custody, not seven years of hearsay");

  // ...and the mirror image, which is what makes the first case mean something.
  // The same artifact with a RECENT source observation and an old custody instant
  // is deletable: the clock is custody either way, and it moved neither time.
  const observedYesterday = deletion({ created_at: T.late });
  assert.equal(observedYesterday.decision, "allow");
  assert.equal(observedYesterday.deletion_receipt.retention_started_at, CUSTODY);

  // THREE TIMESTAMPS, THREE ROLES, none of them standing in for another.
  const receipt = deletion().deletion_receipt;
  assert.equal(receipt.retention_clock.started_at, CUSTODY, "custody starts the period");
  assert.notEqual(receipt.retention_clock.started_at, LONG_AGO,
    "the source's observed instant is not the clock");
  assert.equal(receipt.retention_clock.source_observed_at_used, false);
  assert.equal(receipt.elapsed_days, deletion().elapsed_days);
});

test("retention: the trigger actually used is bound into the evaluation and the receipt", () => {
  const result = deletion();
  assert.equal(result.decision, "allow");
  // On the ANSWER, so a caller can see what it was judged against.
  assert.deepEqual(result.retention_clock, clockOf());
  assert.equal(result.registered_retention_clock_kind, "server_recorded_custody");
  assert.ok(Object.isFrozen(result.retention_clock));

  // And in the RECEIPT's own hashed bytes, beside the registry digest — kind,
  // instant, reference, provenance and (for an event) its digest.
  const receipt = result.deletion_receipt;
  assert.equal(receipt.retention_registry_digest, RETENTION.registry_digest);
  assert.equal(receipt.retention_clock.kind, "server_recorded_custody");
  assert.equal(receipt.retention_clock.event_kind, null);
  assert.equal(receipt.retention_clock.reference, "ops.f01_corporate_artifact.recorded_at");
  assert.equal(receipt.retention_clock.provenance, "server_stamped_custody");
  assert.equal(receipt.retention_clock.event_digest, null);
  assert.equal(receipt.retention_clock.verified, true);
  assert.equal(receipt.default_retention_days, 30);

  // A DIFFERENT TRIGGER IS A DIFFERENT RECEIPT. If the clock were not hashed with
  // it, an evaluation could be re-told with another start instant and hash the
  // same — which is the whole reason it travels in the bytes.
  const moved = deletion({ retention_clock: clockOf({ started_at: "2026-02-01T00:00:00Z" }) });
  assert.equal(moved.decision, "allow");
  assert.notEqual(digest(moved.deletion_receipt), digest(receipt));

  // REPLAY IS IMMUTABLE: the same request evaluated twice is byte-identical, so a
  // second look at a stored decision cannot quietly become a different one.
  assert.equal(canonicalJson(deletion().deletion_receipt), canonicalJson(receipt));
  assert.equal(digest(deletion().deletion_receipt), digest(receipt));
});

test("MUTATION KILL (retention clock): a missing, aliased, unverified or future trigger refuses", () => {
  // ABSENT. A period that starts nowhere is not a period, and there is no default
  // to fall back to — falling back to `created_at` is the defect, not the remedy.
  for (const missing of [undefined, null]) {
    const result = deletion({ retention_clock: missing });
    assert.equal(result.decision, "refuse");
    assert.equal(result.reason_id, "retention_clock_missing");
    assert.equal(result.deletion_receipt, null);
  }

  // THE ALIAS, refused by name. This is the exact shape the rule forbids: the
  // source's observed instant wearing the clock's name.
  const aliased = deletion({
    retention_clock: clockOf({ started_at: LONG_AGO, source_observed_at_used: true }),
  });
  assert.equal(aliased.decision, "refuse");
  assert.equal(aliased.reason_id, "retention_clock_uses_source_observed_at");
  assert.equal(aliased.deletion_receipt, null);

  // UNVERIFIED. A trigger nothing authenticated is not evidence about anything,
  // and this module cannot make it one.
  const unverified = deletion({ retention_clock: clockOf({ verified: false }) });
  assert.equal(unverified.decision, "refuse");
  assert.equal(unverified.reason_id, "retention_clock_unverified");

  // A CLOCK FROM THE FUTURE describes custody nobody has taken yet.
  const ahead = deletion({ retention_clock: clockOf({ started_at: T.future }) });
  assert.equal(ahead.decision, "refuse");
  assert.equal(ahead.reason_id, "retention_clock_after_now");

  // A CUSTODY CLOCK THAT NAMES AN EVENT is two answers at once, and refuses.
  const bothKinds = deletion({ retention_clock: clockOf({ event_kind: "lease_terminated" }) });
  assert.equal(bothKinds.decision, "refuse");
  assert.equal(bothKinds.reason_id, "retention_clock_kind_mismatch");

  // And an unregistered kind is a contract violation rather than a near miss.
  for (const kind of ["SERVER_RECORDED_CUSTODY", "custody", "whenever", ""]) {
    throwsCode(() => deletion({ retention_clock: clockOf({ kind }) }),
      "unknown_retention_clock_kind", kind);
  }
  throwsCode(() => deletion({ retention_clock: { ...clockOf(), extra: 1 } }), "unknown_field");
  throwsCode(() => deletion({ retention_clock: clockOf({ verified: "true" }) }), "invalid_shape");
  throwsCode(() => deletion({ retention_clock: clockOf({ started_at: "2026-02-31T00:00:00Z" }) }),
    "invalid_timestamp");
  for (const key of ["kind", "started_at", "reference", "provenance", "verified",
    "source_observed_at_used"]) {
    throwsCode(() => deletion({ retention_clock: clockOf({ [key]: undefined }) }),
      "missing_field", key);
  }
  assert.deepEqual([...V5_F01_RETENTION_CLOCK_KINDS].sort(),
    ["explicit_retention_clock_event", "server_recorded_custody"]);
  assert.equal(V5_F01_DEFAULT_RETENTION_CLOCK_KIND, "server_recorded_custody");
});

test("MUTATION KILL (retention clock): an explicit event clock fails closed, and a forged event cannot bypass it", () => {
  // A CLASS MAY NAME AN EVENT, and naming one is the whole of what a class may do:
  // the event's own producer is somebody else's work and does not exist yet.
  const eventPolicy = {
    ...RETENTION_POLICY,
    classes: RETENTION_POLICY.classes.map(entry => entry.artifact_class === "executed_lease"
      ? { ...entry, retention_clock: { kind: "explicit_retention_clock_event",
          event_kind: "lease_terminated" } }
      : entry),
  };
  const eventRegistry = compileRetentionRegistry(eventPolicy);
  const lease = eventRegistry.classes.find(c => c.artifact_class === "executed_lease");
  assert.deepEqual(lease.retention_clock,
    { kind: "explicit_retention_clock_event", event_kind: "lease_terminated" });

  const evaluate = (clock, registry = eventRegistry) => evaluateDeletion({
    tenant: ORGANIZATION_TENANT_ID, registry,
    subject: subjectOf(clock === undefined ? {} : { retention_clock: clock }), now: NOW,
  });

  // THE CUSTODY CLOCK IS NOT THAT EVENT, and the refusal says so by name rather
  // than measuring from custody because it happens to be the thing available.
  const custody = evaluate(clockOf());
  assert.equal(custody.decision, "refuse");
  assert.equal(custody.reason_id, "retention_clock_event_not_established");
  assert.equal(custody.registered_retention_clock_kind, "explicit_retention_clock_event");
  assert.equal(custody.loaded_retention_clock_kind, "server_recorded_custody");
  assert.equal(custody.deletion_receipt, null);

  // A FORGED EVENT CANNOT BYPASS IT. Nothing in this slice authenticates a
  // retention-clock event, so an event that says it is verified is a claim about
  // itself; the flag it cannot set for itself is what refuses, and the ONE field
  // that would otherwise expire the artifact early is the one it wanted to move.
  const forged = evaluate({
    kind: "explicit_retention_clock_event",
    event_kind: "lease_terminated",
    started_at: "2019-01-01T00:00:00Z",
    reference: "synthetic-event-0001",
    provenance: "synthetic_test_caller",
    event_digest: D(8),
    verified: false,
    source_observed_at_used: false,
  });
  assert.equal(forged.decision, "refuse");
  assert.equal(forged.reason_id, "retention_clock_unverified");
  assert.equal(forged.deletion_receipt, null);

  // AND AN EVENT OF THE WRONG KIND, OR ONE WITH NOTHING TO HASH IT BY, refuses
  // too — a trigger that cannot be bound into the receipt is not a trigger the
  // receipt can be checked against later.
  const wrongKind = evaluate({
    kind: "explicit_retention_clock_event", event_kind: "matter_closed",
    started_at: LONG_AGO, reference: "synthetic-event-0002",
    provenance: "synthetic_test_producer", event_digest: D(8),
    verified: true, source_observed_at_used: false,
  });
  assert.equal(wrongKind.reason_id, "retention_clock_event_kind_mismatch");
  assert.equal(wrongKind.registered_retention_clock_event_kind, "lease_terminated");

  const unbound = evaluate({
    kind: "explicit_retention_clock_event", event_kind: "lease_terminated",
    started_at: LONG_AGO, reference: "synthetic-event-0003",
    provenance: "synthetic_test_producer", event_digest: null,
    verified: true, source_observed_at_used: false,
  });
  assert.equal(unbound.reason_id, "retention_clock_event_unbound");

  // THE DEFAULT STILL WORKS, which is the other half of "fail closed honestly":
  // the class that names no event deletes on custody exactly as before.
  const draft = evaluateDeletion({
    tenant: ORGANIZATION_TENANT_ID, registry: eventRegistry,
    subject: subjectOf({
      artifact_class: "draft_document", artifact_home: "object_storage",
      deletion_proof: null, derivatives: [], satisfied_constraints: [],
    }),
    now: NOW,
  });
  assert.equal(draft.decision, "allow");
  assert.equal(draft.deletion_receipt.retention_clock.kind, "server_recorded_custody");
});

test("retention: a class that names no clock defaults to custody, and its stored bytes do not move", () => {
  // EXISTING REGISTRY INPUTS DEFAULT TO CUSTODY. Every class in the shared test
  // policy omits `retention_clock`, and every one of them compiles to the default.
  for (const entry of RETENTION.classes) {
    assert.deepEqual(entry.retention_clock,
      { kind: "server_recorded_custody", event_kind: null });
  }

  // AND THE BYTES A REGISTRY HASHES TO DO NOT MOVE FOR THEM. This is the
  // compatibility rule rather than a convenience: a registry installed before this
  // contract named a clock is stored as the exact preimage it hashed to, and a
  // preimage that now emitted a resolved default for it would stop hashing to the
  // digest the database recorded — a refusal to READ an already-installed policy,
  // which is rewriting history by another route.
  const preimage = retentionRegistryPreimage(RETENTION);
  for (const entry of preimage.classes) {
    assert.ok(!Object.prototype.hasOwnProperty.call(entry, "retention_clock"),
      `${entry.artifact_class} must hash exactly as it did before the clock existed`);
  }
  assert.equal(RETENTION.registry_digest, digest(preimage));

  // Stating the default explicitly means the same thing, so it hashes the same.
  const restated = compileRetentionRegistry({
    ...RETENTION_POLICY,
    classes: RETENTION_POLICY.classes.map(entry => ({
      ...entry, retention_clock: { kind: "server_recorded_custody" },
    })),
  });
  assert.equal(restated.registry_digest, RETENTION.registry_digest);

  // A class that names the EXPLICIT clock means something else, and says so in
  // the digest rather than hashing like a class that takes the default.
  const explicit = compileRetentionRegistry({
    ...RETENTION_POLICY,
    classes: RETENTION_POLICY.classes.map(entry => entry.artifact_class === "draft_document"
      ? { ...entry, retention_clock: { kind: "explicit_retention_clock_event",
          event_kind: "draft_abandoned" } }
      : entry),
  });
  assert.notEqual(explicit.registry_digest, RETENTION.registry_digest);
  assert.deepEqual(
    retentionRegistryPreimage(explicit).classes.find(c => c.artifact_class === "draft_document")
      .retention_clock,
    { kind: "explicit_retention_clock_event", event_kind: "draft_abandoned" });

  // The two halves of the policy contract: an explicit clock must name its event,
  // and a default clock must not name one it would never read.
  throwsCode(() => compileRetentionRegistry({
    ...RETENTION_POLICY,
    classes: [{ ...RETENTION_POLICY.classes[0],
      retention_clock: { kind: "explicit_retention_clock_event" } }],
  }), "missing_retention_clock_event_kind");
  throwsCode(() => compileRetentionRegistry({
    ...RETENTION_POLICY,
    classes: [{ ...RETENTION_POLICY.classes[0],
      retention_clock: { kind: "server_recorded_custody", event_kind: "lease_terminated" } }],
  }), "unused_retention_clock_event_kind");
  throwsCode(() => compileRetentionRegistry({
    ...RETENTION_POLICY,
    classes: [{ ...RETENTION_POLICY.classes[0], retention_clock: { kind: "observed_at" } }],
  }), "unknown_retention_clock_kind");

  // A STORED REGISTRY THAT PREDATES THE FIELD STILL EVALUATES. This is the shape
  // the persistence tail rehydrates: the exact stored preimage, with no clock on
  // any class, rebuilt into the compiled shape and checked by the kernel's own
  // digest recomputation.
  const rehydrated = Object.freeze({
    compiled: true, ...preimage, registry_digest: digest(preimage),
  });
  const evaluated = evaluateDeletion({
    tenant: ORGANIZATION_TENANT_ID, registry: rehydrated, subject: subjectOf(), now: NOW,
  });
  assert.equal(evaluated.decision, "allow");
  assert.equal(evaluated.deletion_receipt.retention_clock.kind, "server_recorded_custody");
});

// ===========================================================================
// The bounded derivative-registration rule — a SESSION APPROVAL, not Q129.D1.
//
// Q129.D1 settles the per-class retention registry, including which derivative
// KINDS survive a deletion of that class. It does not settle provenance
// registration. The rule proved below was approved in
//   native task 01a0869f-fe0d-7493-bda3-ab8b3c0d6683
//   user turn   01a08779-6b68-7013-bab1-369cf616254f
// and carries no canonical decision id, because none was issued for it. The test
// names below say "registration" rather than "Q129" for that reason, and the
// nine-decision obligation test proves it beside the nine rather than inside one.
//
// The rule itself: every workflow that creates a derived record registers a link
// to the original before that derivative is complete; only trusted producer
// workflows write the links; deletion is blocked whenever registration coverage
// is unknown.
// ===========================================================================

// THE LOADED SOURCE ARTIFACT CARRIES TWO DIGESTS AND THEY ARE NOT THE SAME FACT.
// `artifact_digest` is the identity of the stored artifact RECORD — taken over its
// source system, account, native identity, provenance and observed instant as well
// as its content — and `content_digest` is the digest of the BYTES that record
// describes. A byte-identical copy of the source has the second and never the
// first, which is why comparing a derivative against the first alone let a copy
// register cleanly as something derived from the thing it copies.
const SOURCE_BYTES = D(12);
const SOURCE_ARTIFACT = Object.freeze({
  artifact_digest: VALUE_A, created_at: LONG_AGO, content_digest: SOURCE_BYTES,
});

function registrationOf(overrides = {}) {
  return {
    source_artifact_digest: VALUE_A,
    derivative: {
      derivative_kind: "lease_abstract",
      derivative_id: "abstract-synthetic-1",
      content_digest: VALUE_B,
    },
    producer: {
      producer_workflow: "synthetic_test_producer",
      producer_run_ref: "synthetic-run-0001",
    },
    produced_at: T.mid,
    evidence: {
      evidence_ref: "synthetic-evidence-0040",
      evidence_digest: D(6),
    },
    ...overrides,
  };
}

function register(overrides = {}, requestOverrides = {}) {
  return evaluateDerivativeRegistration({
    tenant: ORGANIZATION_TENANT_ID,
    registration: registrationOf(overrides),
    source_artifact: { ...SOURCE_ARTIFACT },
    now: NOW,
    ...requestOverrides,
  });
}

test("registration: the reserved internal producer kinds are a list, and the shared evaluator still serves them", () => {
  // THE LIST IS THE CONTRACT, not the single literal. A second internal producer
  // kind is covered by adding an element, and this asserts the shape rather than
  // just today's one member.
  assert.ok(Array.isArray(V5_F01_RESERVED_DERIVATIVE_KINDS));
  assert.ok(Object.isFrozen(V5_F01_RESERVED_DERIVATIVE_KINDS));
  assert.ok(V5_F01_RESERVED_DERIVATIVE_KINDS.includes(V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND),
    "the kind this contract's own proposal producer writes must be reserved");
  // The second in-contract producer kind: a document version, whose derivative
  // identity is the (document_id, version_no) fold and is therefore even easier
  // for a caller to predict than a proposal digest. Written as the LITERAL the
  // schema mirror uses, not imported from the document module, so the two lists
  // are compared rather than derived from one another.
  assert.ok(V5_F01_RESERVED_DERIVATIVE_KINDS.includes("f01_document_version"),
    "the kind the document producer writes must be reserved too");
  assert.equal(V5_F01_RESERVED_DERIVATIVE_KINDS.length, 2,
    "every reserved kind is one an in-contract writer produces; there is no third");
  assert.throws(() => { V5_F01_RESERVED_DERIVATIVE_KINDS.push("anything"); }, TypeError);

  // AND THE EVALUATOR MUST NOT REFUSE THEM, which is the half that is easy to get
  // wrong. The in-contract proposal producer calls this same function to build
  // its own link, so a refusal here would break the one path that legitimately
  // writes the reserved kind. The refusal belongs to the public caller surface —
  // the store proves it there, and ops.f01_register_derivative_link proves it in
  // SQL — and deliberately not here.
  const internal = register({ derivative: {
    derivative_kind: V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
    derivative_id: "abstract-synthetic-internal-1",
    content_digest: VALUE_B,
  } });
  assert.equal(internal.decision, "allow");
  assert.equal(internal.derivative_link.derivative_kind, V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND);
  assert.equal(internal.derivative_link.establishes_coverage, false);
});

test("registration: a trusted producer binds one derivative to the exact artifact it came from", () => {
  const result = register();
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "derivative_source_link_registered");

  const link = result.derivative_link;
  assert.equal(link.schema_version, V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION);
  assert.equal(link.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(link.source_artifact_digest, VALUE_A);
  assert.equal(link.derivative_kind, "lease_abstract");
  assert.equal(link.derivative_id, "abstract-synthetic-1");
  assert.equal(link.derivative_content_digest, VALUE_B);
  assert.equal(link.producer_workflow, "synthetic_test_producer");
  assert.equal(link.producer_run_ref, "synthetic-run-0001");
  assert.equal(link.produced_at, T.mid);
  assert.equal(link.evidence_ref, "synthetic-evidence-0040");
  assert.equal(link.evidence_digest, D(6));

  // THE FOUR CLAIMS A LINK MAKES ABOUT ITSELF, and they are in the hashed bytes
  // rather than in a comment. This is what a later reader has to be unable to
  // misread: rows appeared, and nothing about what is KNOWN changed.
  assert.equal(link.registration_is_provenance, true);
  assert.equal(link.is_exhaustive_inventory, false);
  assert.equal(link.establishes_coverage, false);
  assert.equal(link.permits_deletion, false);
  assert.equal(result.establishes_coverage, false);
  assert.equal(result.is_exhaustive_inventory, false);
  assert.equal(result.permits_deletion, false);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
  assert.ok(Object.isFrozen(link));

  // The proposal producer path names its kind from the module, not from a
  // caller, so the two cannot drift.
  assert.equal(V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND, "f01_parsed_proposal");
});

test("MUTATION KILL (registration): the source artifact is LOADED, never asserted", () => {
  // Naming a digest is a lookup. A caller that could register against an
  // artifact nobody stored would be writing provenance pointing at nothing, and
  // the link would still count as a registered derivative afterwards.
  const absent = register({}, { source_artifact: null });
  assert.equal(absent.decision, "refuse");
  assert.equal(absent.reason_id, "unknown_source_artifact");
  assert.equal(absent.derivative_link, null);

  const missing = evaluateDerivativeRegistration({
    tenant: ORGANIZATION_TENANT_ID, registration: registrationOf(), now: NOW,
  });
  assert.equal(missing.reason_id, "unknown_source_artifact");

  // The loaded artifact must be the one the registration names.
  const other = register({}, { source_artifact: { artifact_digest: VALUE_C, created_at: LONG_AGO } });
  assert.equal(other.decision, "refuse");
  assert.equal(other.reason_id, "source_artifact_mismatch");
  assert.equal(other.loaded_artifact_digest, VALUE_C);

  // A record whose bytes ARE the source's bytes is the source under a second
  // name; registering it would make an artifact its own provenance.
  const itself = register({ derivative: {
    derivative_kind: "lease_abstract", derivative_id: "abstract-synthetic-2",
    content_digest: VALUE_A,
  } });
  assert.equal(itself.decision, "refuse");
  assert.equal(itself.reason_id, "derivative_is_its_own_source");
});

test("MUTATION KILL (registration): a byte-identical copy is not a derivative, and the comparison is against the SOURCE'S BYTES", () => {
  // THE DEFECT THIS FORECLOSES, exactly. The refusal used to compare the
  // derivative's CONTENT digest with the source's RECORD digest — two values that
  // are never equal for a copy, because a record digest is taken over identity and
  // provenance as well as bytes. So "the same bytes cannot masquerade as a
  // derivative" was a comment describing a comparison nothing made: a producer
  // could register a verbatim copy of a lease as an abstract of it, and the
  // provenance edge would say the lease produced itself.
  const copy = register({ derivative: {
    derivative_kind: "lease_abstract",
    derivative_id: "abstract-synthetic-copy-1",
    content_digest: SOURCE_BYTES,
  } });
  assert.equal(copy.decision, "refuse");
  assert.equal(copy.reason_id, "derivative_is_its_own_source");
  assert.equal(copy.self_source_comparison, "source_content_digest",
    "the refusal names the comparison that fired, so one is never read as the other");
  assert.equal(copy.derivative_link, null);
  assert.notEqual(SOURCE_BYTES, VALUE_A,
    "the fixture is only meaningful while the source's bytes and its record identity differ");

  // AND DISTINCT DERIVED BYTES ARE STILL ACCEPTED, which is the half that stops
  // the rule above from being a refusal of every registration. The answer reports
  // the digest it compared against rather than leaving a reader to assume one.
  const derived = register();
  assert.equal(derived.decision, "allow");
  assert.equal(derived.source_content_digest, SOURCE_BYTES);
  assert.equal(derived.source_content_compared, true);
  assert.equal(derived.derivative_link.derivative_content_digest, VALUE_B);

  // THE RECORD-IDENTITY CLAIM KEEPS ITS OWN REFUSAL. A derivative whose bytes are
  // the stored artifact ROW's canonical bytes is a different and equally
  // impossible claim, and it did not stop being one when the real comparison
  // arrived.
  const asRecord = register({ derivative: {
    derivative_kind: "lease_abstract", derivative_id: "abstract-synthetic-copy-2",
    content_digest: VALUE_A,
  } });
  assert.equal(asRecord.reason_id, "derivative_is_its_own_source");
  assert.equal(asRecord.self_source_comparison, "source_artifact_record_digest");

  // A LOADED ARTIFACT WITH NO CONTENT DIGEST SAYS THE COMPARISON WAS NOT MADE.
  // This is the shape a caller outside the persistence tail can still produce —
  // the document seam passes the artifact its own loader returned — and the honest
  // answer is that this evaluator could not weigh the bytes, never that it did and
  // they differed. The private SQL inserter re-derives the source's content digest
  // from the authenticated stored row regardless, so the uncompared case is a
  // reported gap rather than a way through.
  const uncompared = register({}, {
    source_artifact: { artifact_digest: VALUE_A, created_at: LONG_AGO },
  });
  assert.equal(uncompared.decision, "allow");
  assert.equal(uncompared.source_content_digest, null);
  assert.equal(uncompared.source_content_compared, false);
  // ...and the record-identity comparison still fires there, because it needs
  // nothing the caller withheld.
  assert.equal(register({ derivative: {
    derivative_kind: "lease_abstract", derivative_id: "abstract-synthetic-copy-3",
    content_digest: VALUE_A,
  } }, { source_artifact: { artifact_digest: VALUE_A, created_at: LONG_AGO } }).reason_id,
  "derivative_is_its_own_source");

  // The loaded content digest is CHECKED, not merely carried.
  throwsCode(() => register({}, {
    source_artifact: { ...SOURCE_ARTIFACT, content_digest: "not-a-digest" },
  }), "invalid_digest");
  throwsCode(() => register({}, {
    source_artifact: { ...SOURCE_ARTIFACT, unexpected_field: 1 },
  }), "unknown_field");
});

test("registration: the parsed-proposal and document producer shapes stay intact", () => {
  // THE TWO IN-CONTRACT PRODUCER PATHS, in the exact shape their writers build.
  // Both name a derivative whose content digest is the digest of the RECORD they
  // are about to store, so neither can collide with the source's own bytes — and
  // both must keep working, because the content comparison added above sits on the
  // same path they use.
  const proposal = register({
    derivative: {
      derivative_kind: V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
      derivative_id: D(13),
      content_digest: D(13),
    },
    producer: { producer_workflow: "f01_record_parsed_proposal",
      producer_run_ref: "synthetic-run-0002" },
    evidence: { evidence_ref: "stored_parsed_proposal", evidence_digest: D(13) },
  });
  assert.equal(proposal.decision, "allow");
  assert.equal(proposal.derivative_link.derivative_kind, V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND);
  assert.equal(proposal.source_content_compared, true);

  const documentVersion = register({
    derivative: {
      derivative_kind: "f01_document_version",
      derivative_id: "synthetic-document-0001:1",
      content_digest: D(14),
    },
    producer: { producer_workflow: "synthetic_test_document_producer",
      producer_run_ref: "synthetic-run-0003" },
    evidence: { evidence_ref: "stored_document_version", evidence_digest: D(14) },
  });
  assert.equal(documentVersion.decision, "allow");
  assert.equal(documentVersion.derivative_link.derivative_id, "synthetic-document-0001:1");
  // Both kinds are reserved on the PUBLIC surface and accepted here, which is the
  // division the store and the SQL writer enforce rather than this evaluator.
  assert.ok(V5_F01_RESERVED_DERIVATIVE_KINDS.includes(V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND));
  assert.ok(V5_F01_RESERVED_DERIVATIVE_KINDS.includes("f01_document_version"));
});

test("MUTATION KILL (registration): a production must describe something that happened", () => {
  const future = register({ produced_at: T.future });
  assert.equal(future.decision, "refuse");
  assert.equal(future.reason_id, "production_after_now");
  assert.equal(future.produced_at, T.future);

  const beforeSource = register({}, {
    source_artifact: { artifact_digest: VALUE_A, created_at: T.late },
  });
  assert.equal(beforeSource.decision, "refuse");
  assert.equal(beforeSource.reason_id, "production_precedes_source_artifact");
  assert.equal(beforeSource.source_created_at, T.late);

  // Produced exactly at the source's own creation instant is fine: a derivative
  // made in the same transaction as its source is ordinary, not suspicious.
  assert.equal(register({ produced_at: LONG_AGO }).decision, "allow");

  // And every identity is checked, not merely typed.
  throwsCode(() => register({ source_artifact_digest: "not-a-digest" }), "invalid_digest");
  throwsCode(() => register({ derivative: {
    derivative_kind: "lease abstract", derivative_id: "x", content_digest: VALUE_B } }),
    "invalid_identifier");
  // BUILT AT RUN TIME, never written into this file. The byte-hygiene test below
  // scans both committed files for exactly this code point, so the offender is
  // constructed from its numeric value and never appears in either source.
  const bidiOverride = String.fromCharCode(0x202E);
  throwsCode(() => register({ derivative: {
    derivative_kind: "lease_abstract",
    derivative_id: `x${bidiOverride}reversed`,
    content_digest: VALUE_B } }), "unsafe_unicode");
  throwsCode(() => register({ evidence: { evidence_ref: "e", evidence_digest: "nope" } }),
    "invalid_digest");
  throwsCode(() => register({ produced_at: "2026-02-31T00:00:00Z" }), "invalid_timestamp");
  for (const key of ["source_artifact_digest", "derivative", "producer", "produced_at", "evidence"]) {
    throwsCode(() => register({ [key]: undefined }), "missing_field", key);
  }
  throwsCode(() => register({ derivative: {
    derivative_kind: "lease_abstract", derivative_id: "x", content_digest: VALUE_B,
    also: true } }), "unknown_field");
  throwsCode(() => evaluateDerivativeRegistration({
    tenant: "other-tenant", registration: registrationOf(),
    source_artifact: { ...SOURCE_ARTIFACT }, now: NOW,
  }), "tenant_mismatch");
});

test("MUTATION KILL (coverage): unknown registration coverage blocks the deletion", () => {
  // THE RULE THE DECISION ADDED. An empty or partial link set is not a verified
  // absence, so an artifact whose coverage nobody has established cannot be
  // deleted — however clean everything else about it is.
  const unknown = deletion({ derivative_coverage: coverageOf({
    state: "unknown", reason_id: "producer_closure_not_established",
    registered_derivative_kinds: [],
  }) });
  assert.equal(unknown.decision, "refuse");
  assert.equal(unknown.reason_id, "derivative_coverage_unknown");
  assert.equal(unknown.derivative_coverage_state, "unknown");
  assert.equal(unknown.derivative_coverage_reason_id, "producer_closure_not_established");
  assert.equal(unknown.deletion_receipt, null);

  // Unknown coverage blocks even when the observed inventory looks complete and
  // agrees with the class policy exactly. "We found all the ones we know about"
  // is not the same fact as "these are all of them".
  const looksComplete = deletion({
    derivative_coverage: coverageOf({ state: "unknown",
      registered_derivative_kinds: ["deal_economics_summary", "lease_abstract"] }),
  });
  assert.equal(looksComplete.reason_id, "derivative_coverage_unknown");

  // AN ABSENT ANSWER IS NOT AN ESTABLISHED ONE. Omitting the field is a question
  // nobody asked, and it refuses under its own name so the caller can tell the
  // two apart.
  const absent = deletion({ derivative_coverage: undefined });
  assert.equal(absent.decision, "refuse");
  assert.equal(absent.reason_id, "derivative_coverage_missing");
  const explicitNull = deletion({ derivative_coverage: null });
  assert.equal(explicitNull.reason_id, "derivative_coverage_missing");

  // Coverage is checked BEFORE the inventory, so an unknown answer is never
  // reported as a missing list — the caller is told the real reason.
  const noInventory = deletion({
    derivative_coverage: coverageOf({ state: "unknown" }), derivatives: undefined,
  });
  assert.equal(noInventory.reason_id, "derivative_coverage_unknown");
  const establishedNoInventory = deletion({ derivatives: undefined });
  assert.equal(establishedNoInventory.reason_id, "derivative_inventory_missing");

  // A state outside the registered vocabulary throws rather than being read as a
  // near-miss for "established".
  for (const state of ["ESTABLISHED", "established_enough", "probably", "", "verified"]) {
    throwsCode(() => deletion({ derivative_coverage: coverageOf({ state }) }),
      "unknown_derivative_coverage_state", state);
  }
  throwsCode(() => deletion({ derivative_coverage: coverageOf({ state: true }) }),
    "unknown_derivative_coverage_state");
  throwsCode(() => deletion({ derivative_coverage: { established: true } }), "unknown_field");
  throwsCode(() => deletion({ derivative_coverage: coverageOf({
    registered_derivative_kinds: ["lease_abstract", "lease_abstract"] }) }),
    "duplicate_registered_derivative_kind");
  assert.deepEqual([...V5_F01_DERIVATIVE_COVERAGE_STATES].sort(), ["established", "unknown"]);
});

test("Q129: class policy names which derivative KINDS survive, not that each one exists", () => {
  // THE CORRECTION, AND WHY IT IS ONE. An earlier revision refused a deletion
  // when a kind the class policy lists as surviving was absent from the observed
  // inventory. Q129 settles that the registry "defines ... surviving derivatives
  // separately for every artifact class" — which kinds survive when the artifact
  // goes — and says nothing about every instance having one of each. With real
  // registration ingress the old rule is actively wrong: a lease that only ever
  // produced an abstract could never be deleted, no matter how complete its
  // coverage, and the only way past it would be to pad the inventory with kinds
  // that do not exist. That is the opposite of the property wanted.
  const lease = RETENTION.classes.find(c => c.artifact_class === "executed_lease");
  assert.deepEqual(lease.surviving_derivatives, ["deal_economics_summary", "lease_abstract"]);

  const partial = deletion({
    derivatives: ["lease_abstract"],
    derivative_coverage: coverageOf({ registered_derivative_kinds: ["lease_abstract"] }),
  });
  assert.equal(partial.decision, "allow",
    "an instance with fewer kinds than its class permits is still deletable");
  assert.deepEqual(partial.observed_surviving_derivatives, ["lease_abstract"]);
  // On an ALLOW the two names carry the same list, and that is exactly what makes
  // the longer one true here: the step above proved every observed kind is one
  // the class policy says survives.
  assert.deepEqual(partial.observed_derivatives, ["lease_abstract"]);

  // WHAT THE OLD RULE WAS PROTECTING IS KEPT, by saying which is which rather
  // than by refusing. The receipt reports the class policy AND the instance
  // observation under different names, so neither can be read as the other, and
  // no survivor is named that nobody looked for.
  const receipt = partial.deletion_receipt;
  assert.deepEqual(receipt.surviving_derivatives,
    ["deal_economics_summary", "lease_abstract"], "class policy, verbatim from the registry");
  assert.equal(receipt.surviving_derivatives_are_class_policy, true);
  assert.deepEqual(receipt.observed_surviving_derivatives, ["lease_abstract"],
    "instance observation, verbatim from what was registered");
  assert.equal(receipt.derivative_coverage_state, "established");

  // An instance with NO derivatives at all is deletable under established
  // coverage, and its receipt says so rather than implying two survivors.
  const none = deletion({
    derivatives: [], derivative_coverage: coverageOf({ registered_derivative_kinds: [] }),
  });
  assert.equal(none.decision, "allow");
  assert.deepEqual(none.deletion_receipt.observed_surviving_derivatives, []);
  assert.deepEqual(none.deletion_receipt.surviving_derivatives,
    ["deal_economics_summary", "lease_abstract"]);

  // AND THE OTHER DIRECTION STILL BLOCKS, which is the half that protects the
  // artifact: a derivative the class policy does not register as surviving stops
  // the deletion rather than vanishing with it.
  const stray = deletion({ derivatives: ["lease_abstract", "shadow_copy"] });
  assert.equal(stray.decision, "refuse");
  assert.equal(stray.reason_id, "unregistered_derivative_blocks_deletion");
  assert.deepEqual(stray.unregistered_derivatives, ["shadow_copy"]);
  // THE LABEL ON A REFUSAL TELLS THE TRUTH ABOUT WHAT WAS OBSERVED. shadow_copy
  // is, by the very rule that produced this refusal, NOT a surviving derivative,
  // so the observed list cannot ride under a name that says it is. The honest
  // name is the whole reason a reader can tell policy from observation here.
  assert.deepEqual(stray.observed_derivatives, ["lease_abstract", "shadow_copy"]);
  assert.equal(stray.observed_surviving_derivatives, null,
    "a refusal must not report observed kinds as surviving ones");
  assert.deepEqual(stray.surviving_derivatives, ["deal_economics_summary", "lease_abstract"],
    "the class policy is still reported, under its own name");
  assert.equal(stray.deletion_receipt, null);
});

// ===========================================================================
// The input contract: nothing a caller sends can select authority or change a
// decision after it was validated.
// ===========================================================================

// One call per public entry point, each in its known-good form, so a hostile
// field can be added to any of them and the refusal compared.
const ENTRY_POINTS = [
  ["projectRecordHome", extra => projectRecordHome({
    tenant: ORGANIZATION_TENANT_ID, fact_class: "code", ...extra })],
  ["compileFieldAuthorityRegistry", extra => compileFieldAuthorityRegistry({
    ...REGISTRY_POLICY, ...extra })],
  ["resolveObservation", extra => resolveObservation({
    tenant: ORGANIZATION_TENANT_ID, registry: REGISTRY, observation: observation(), now: NOW,
    current_state: currentState(), ...extra })],
  ["admitCorporateArtifact", extra => admitCorporateArtifact({
    tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(), now: NOW, ...extra })],
  ["evaluateParsedProposal", extra => evaluateParsedProposal({
    tenant: ORGANIZATION_TENANT_ID, registry: REGISTRY, proposal: proposalOf(), now: NOW, ...extra })],
  ["projectDocumentIdentity", extra => projectDocumentIdentity({
    tenant: ORGANIZATION_TENANT_ID, document: documentOf(), ...extra })],
  ["compileRetentionRegistry", extra => compileRetentionRegistry({ ...RETENTION_POLICY, ...extra })],
  ["evaluateDerivativeRegistration", extra => evaluateDerivativeRegistration({
    tenant: ORGANIZATION_TENANT_ID, registration: registrationOf(),
    source_artifact: { ...SOURCE_ARTIFACT }, now: NOW, ...extra })],
  ["evaluateDeletion", extra => evaluateDeletion({
    tenant: ORGANIZATION_TENANT_ID, registry: RETENTION, subject: subjectOf(), now: NOW, ...extra })],
  ["v5F01AuthorityProjection", extra => v5F01AuthorityProjection({ ...extra })],
];

test("MUTATION KILL (authority injection): no caller field can supply or select authority", () => {
  const injections = [
    { actor: { slug: "joe" } },
    { authenticated_actor: "joe" },
    { acting_as: "joe" },
    { on_behalf_of: "dell" },
    { admin: true },
    { sudo: true },
    { superuser: true },
    { authority: "system_authority" },
    { authorized_by: "joe" },
    { authorization: "granted" },
    { privilege: "all" },
    { grant: "everything" },
    { delegation: { granted_by: "joe" } },
    { redecision: { decided_by: "joe" } },
    { override: true },
    { bypass_checks: true },
    { force_apply: true },
    { permission: "write" },
    { owner_override: "salesforce" },
    { is_owner: true },
    { owner_slug: "joe" },
    { as_tenant: "other" },
    { tenant_override: "other" },
    { trusted_caller: true },
    { approved_by: "joe" },
    { signed_off: true },
    { impersonate: "joe" },
  ];
  for (const [name, call] of ENTRY_POINTS) {
    for (const injection of injections) {
      throwsCode(() => call(injection), "caller_authority_field_refused",
        `${name} + ${Object.keys(injection)[0]}:`);
    }
  }
  // Every fragment the guard knows is exercised by at least one injection above,
  // so none of them is dead policy.
  const attempted = injections.flatMap(i => Object.keys(i).map(k => k.toLowerCase()));
  for (const fragment of V5_F01_AUTHORITY_INJECTION_FRAGMENTS) {
    assert.ok(attempted.some(key => key.includes(fragment)),
      `no injection exercises the guard fragment "${fragment}"`);
  }
});

test("the tenant is checked and never selected", () => {
  // A caller may state the tenant. Stating a different one refuses, which is
  // what makes it checked rather than chosen.
  for (const [name, call] of ENTRY_POINTS) {
    if (name === "v5F01AuthorityProjection") continue;
    if (name === "compileFieldAuthorityRegistry" || name === "compileRetentionRegistry") {
      throwsCode(() => call({ tenant: "other-tenant" }), "tenant_mismatch", name);
      continue;
    }
    throwsCode(() => call({ tenant: "other-tenant" }), "tenant_mismatch", name);
  }
  assert.equal(projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class: "code" }).tenant,
    ORGANIZATION_TENANT_ID);
});

test("open schemas refuse: an unread field is an unenforced one", () => {
  for (const [name, call] of ENTRY_POINTS) {
    throwsCode(() => call({ helpful_extra: true }), "unknown_field", name);
  }
});

test("prototype, accessor and symbol keys are refused rather than read", () => {
  const polluted = JSON.parse('{"tenant":"carr-internal","fact_class":"code","__proto__":{"x":1}}');
  throwsCode(() => projectRecordHome(polluted), "prototype_key_refused");

  // A getter can answer differently on a second read, so the value that was
  // validated would not be the value that was used.
  const getterRequest = { tenant: ORGANIZATION_TENANT_ID };
  let reads = 0;
  Object.defineProperty(getterRequest, "fact_class", {
    enumerable: true, configurable: true,
    get() { reads += 1; return reads === 1 ? "code" : "corporate_transaction_field"; },
  });
  throwsCode(() => projectRecordHome(getterRequest), "accessor_property_refused");

  const symbolRequest = { tenant: ORGANIZATION_TENANT_ID, fact_class: "code" };
  symbolRequest[Symbol("smuggled")] = "authority";
  throwsCode(() => projectRecordHome(symbolRequest), "symbol_key_refused");

  // The same defence protects a nested policy object, not just the top level.
  const nestedGetter = { ...REGISTRY_POLICY, entries: [{ ...REGISTRY_POLICY.entries[4] }] };
  Object.defineProperty(nestedGetter.entries[0], "owner_source", {
    enumerable: true, configurable: true, get() { return SOURCES.neon; },
  });
  throwsCode(() => compileFieldAuthorityRegistry(nestedGetter), "accessor_property_refused");

  // A null-prototype object is not hostile and is accepted.
  const bare = Object.assign(Object.create(null), {
    tenant: ORGANIZATION_TENANT_ID, fact_class: "code",
  });
  assert.equal(projectRecordHome(bare).decision, "allow");
});

test("identifiers that render as other identifiers are refused, never normalized", () => {
  const hostile = [
    ["\uD800", "malformed_unicode"],                       // lone surrogate
    ["acct\u0000null", "unsafe_unicode"],                 // control character
    ["acct\u202Ereversed", "unsafe_unicode"],             // bidi override
    ["acct\u200Bzero", "unsafe_unicode"],                 // zero width space
    ["acct\uFEFFbom", "unsafe_unicode"],                  // byte order mark
    ["acce\u0301nt", "non_canonical_unicode"],            // decomposed, not NFC
    [" acct-1", "untrimmed_text"],
    ["acct-1 ", "untrimmed_text"],
    ["", "invalid_shape"],
    ["-leading-dash", "invalid_identifier"],
    ["acct 1", "invalid_identifier"],                      // a space is not an identifier
  ];
  for (const [account, code] of hostile) {
    throwsCode(() => resolveFor(observation({ account }), currentState()), code, JSON.stringify(account));
  }
  // The same checks guard native ids, entity and field names.
  throwsCode(() => resolveFor(observation({
    native_identity: { ...NATIVE, native_id: "006\u202ESYNTH" },
  }), currentState()), "unsafe_unicode");
  throwsCode(() => resolveFor(observation({ entity: "de\u0000al" }), null), "unsafe_unicode");
});

test("timestamps are parsed, never inferred, and an impossible calendar date refuses", () => {
  const badInstants = [
    "2026-02-31T00:00:00Z",     // normalizes to 3 March if parsed naively
    "2026-13-01T00:00:00Z",
    "2026-09-09T25:00:00Z",
    "2026-09-09T00:60:00Z",
    "2026-09-09",               // a bare date has no instant
    "2026-09-09T12:00:00",      // no offset: no ambient zone is assumed
    "September 9, 2026",
    "2026-09-09T12:00:00+25:00",
  ];
  for (const observed_at of badInstants) {
    throwsCode(() => resolveFor(observation({ observed_at }), currentState()),
      "invalid_timestamp", observed_at);
  }
  // A leap day that exists is accepted; the one that does not is not.
  assert.equal(resolveFor(observation({ observed_at: "2028-02-29T00:00:00Z", version: 6 }),
    currentState({ observed_at: "2026-01-01T00:00:00Z" })).decision, "refuse");
  throwsCode(() => resolveFor(observation({ observed_at: "2026-02-29T00:00:00Z" }), currentState()),
    "invalid_timestamp");
  // An explicit offset is fine.
  assert.equal(resolveFor(observation({ observed_at: "2026-09-05T09:00:00-05:00" }),
    currentState()).decision, "accept");
});

test("digests must be sha256 references, in the form this module itself produces", () => {
  const badDigests = ["nope", "sha1:" + "a".repeat(40), "a".repeat(64),
    "sha256:" + "A".repeat(64), "sha256:" + "a".repeat(63), "sha256:"];
  for (const value_digest of badDigests) {
    throwsCode(() => resolveFor(observation({ value_digest }), currentState()),
      "invalid_digest", String(value_digest));
  }
  // The digest a caller must supply is exactly the shape artifact-trust emits.
  assert.match(digest({ any: "value" }), /^sha256:[0-9a-f]{64}$/);
});

test("MUTATION KILL (document): mutating an input after validation cannot change a decision", () => {
  // The document projection echoes a snapshot, so editing the caller's object
  // afterwards leaves the readback and the digest exactly where they were.
  const mutableDoc = documentOf();
  const projected = projectDocumentIdentity({
    tenant: ORGANIZATION_TENANT_ID, document: mutableDoc,
  });
  const digestBefore = projected.document_digest;
  mutableDoc.signature_state = "unsigned";
  mutableDoc.neon_identity.content_digest = VALUE_C;
  mutableDoc.onedrive_identity.filing_state = "failed";
  assert.equal(projected.signature_state, "fully_executed");
  assert.equal(projected.neon_identity.content_digest, VALUE_A);
  assert.equal(projected.onedrive_identity.filing_state, "filed");
  assert.equal(projected.readback.signature_state, "fully_executed");
  assert.equal(projected.document_digest, digestBefore);
  assert.equal(projected.decision, "allow");

  // The same for a resolution: the three records keep the values that were read.
  const mutableObs = observation();
  const resolved = resolveObservation({
    tenant: ORGANIZATION_TENANT_ID, registry: REGISTRY, now: NOW,
    observation: mutableObs, current_state: currentState(),
  });
  mutableObs.value_digest = VALUE_C;
  mutableObs.version = 999;
  mutableObs.provenance.evidence_ref = "rewritten";
  assert.equal(resolved.current_state_transition.to_value_digest, VALUE_A);
  assert.equal(resolved.current_state_transition.to_version, 5);
  assert.equal(resolved.event.provenance.evidence_ref, "synthetic-evidence-0001");
});

test("frozen inputs are accepted and every result is frozen", () => {
  const frozenRequest = Object.freeze({
    tenant: ORGANIZATION_TENANT_ID, registry: REGISTRY, now: NOW,
    observation: Object.freeze({
      ...observation(),
      native_identity: Object.freeze({ ...NATIVE }),
      provenance: Object.freeze({ ...PROVENANCE }),
      readback: Object.freeze({ confirmed: true, readback_at: T.mid, readback_value_digest: VALUE_A }),
    }),
    current_state: Object.freeze({ ...currentState(), native_identity: Object.freeze({ ...NATIVE }) }),
  });
  const result = resolveObservation(frozenRequest);
  assert.equal(result.decision, "accept");
  assert.ok(Object.isFrozen(result));
  assert.throws(() => { result.decision = "refuse"; }, TypeError);
  assert.throws(() => { result.current_state_transition.to_value_digest = VALUE_C; }, TypeError);
  assert.throws(() => { result.event.event_seq = 99; }, TypeError);
  assert.throws(() => { result.mutation_receipt.actor = "joe"; }, TypeError);

  for (const frozen of [
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class: "decision" }),
    admit(), propose(), projectDoc(), deletion(), register(), v5F01AuthorityProjection(),
  ]) {
    assert.ok(Object.isFrozen(frozen));
    assert.deepEqual(frozen.effects, V5_NO_EFFECTS);
  }
  // The exported vocabularies cannot be edited by a caller either.
  assert.throws(() => { V5_F01_HOMES.push("sharepoint"); }, TypeError);
  assert.throws(() => { V5_F01_SETTLED_DECISIONS["Q004.D1"].settled_requirement = "anything"; }, TypeError);
  assert.throws(() => { V5_F01_TOUR_ONLY_EVIDENCE_CLASSES.pop(); }, TypeError);
  assert.throws(() => { V5_F01_WRITE_DIRECTIONS.push("whatever"); }, TypeError);
  assert.throws(() => { V5_F01_TAINT_CLASSES.pop(); }, TypeError);
  assert.throws(() => { V5_F01_FACT_CLASSES.push("gut_feel"); }, TypeError);
  assert.equal(v5F01PolicyDigest(), digest(v5F01PolicyPreimage()));
});

test("the module performs no database, network, provider, filesystem or scheduling effect", () => {
  const source = readFileSync(SRC_PATH, "utf8");
  const forbidden = [
    /\bnode:fs\b/, /\bnode:net\b/, /\bnode:http\b/, /\bnode:https\b/, /\bnode:dns\b/,
    /\bnode:child_process\b/, /\bnode:worker_threads\b/, /\bnode:cluster\b/,
    /\bchild_process\b/, /\bfetch\s*\(/, /\bXMLHttpRequest\b/, /\bprocess\.env\b/,
    /\bDate\.now\b/, /\bsetTimeout\b/, /\bsetInterval\b/, /\brequire\s*\(/,
    /\bimport\s*\(/, /\bglobalThis\b/, /\bpg\b\s*\)/, /\bquery\s*\(/,
    // Process execution by its real names. A bare /\bexec\s*\(/ would also match
    // RegExp.prototype.exec, which is pure and is how this module reads an
    // ISO instant, so the check names the process forms instead of guessing.
    /\bWebSocket\b/, /\bopen\s*\(/, /\bexecSync\b/, /\bexecFile/, /\bspawn\b/,
  ];
  for (const pattern of forbidden) {
    assert.ok(!pattern.test(source), `module source must not contain ${pattern}`);
  }
  const imports = [...source.matchAll(/^import\s[^;]*?from\s+"([^"]+)";/gm)].map(m => m[1]).sort();
  assert.deepEqual(imports,
    ["./artifact-trust.js", "./global-boundaries.v5.js", "./identity.js"]);

  // The reused symbols are the real ones, not local reimplementations.
  assert.equal(typeof evaluatePrivacyBoundary, "function");
  assert.equal(V5_NO_EFFECTS.creates_effect, false);
  for (const value of Object.values(V5_NO_EFFECTS)) assert.ok(value === false || value === 0);

  // Effects are asserted in the record too, not only in the source.
  for (const result of [
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class: "code" }),
    resolveFor(observation(), currentState()), admit(), propose(), projectDoc(), deletion(),
    register(), v5F01AuthorityProjection(),
  ]) {
    assert.equal(result.effects.creates_effect, false);
    assert.equal(result.effects.database_writes, 0);
    assert.equal(result.effects.network_calls, 0);
    assert.equal(result.effects.provider_actions, 0);
    assert.equal(result.effects.notifications, 0);
    assert.equal(result.effects.deployments, 0);
    assert.equal(result.effects.activations, 0);
    assert.equal(result.effects.acceptances, 0);
  }
});

test("the domain contract hashes deterministically and the projection accepts nothing", () => {
  const preimage = v5F01PolicyPreimage();
  assert.equal(preimage.schema_version, V5_F01_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_F01_POLICY_VERSION);
  assert.equal(preimage.tenant, ORGANIZATION_TENANT_ID);
  assert.deepEqual(preimage.decisions.map(d => d.decision_id), [...V5_F01_SETTLED_DECISION_IDS]);

  // The settled prohibitions are IN the hashed bytes, so they cannot be relaxed
  // without the digest moving.
  assert.equal(preimage.record_homes.markdown_authoritative, false);
  assert.equal(preimage.record_homes.summaries_and_embeddings_disposable, true);
  assert.equal(preimage.record_homes.temporary_json_is_review_evidence, true);
  assert.equal(preimage.field_authority.silent_last_write_wins_permitted, false);
  assert.equal(preimage.field_authority.lexical_ordering_of_opaque_versions, false);
  assert.equal(preimage.field_authority.non_owner_may_overwrite_owner_value, false);
  assert.equal(preimage.resolution_records.whole_product_event_sourcing, false);
  assert.equal(preimage.resolution_records.any_one_record_substitutes_for_another, false);
  assert.equal(preimage.corporate_sources.proposal_becomes_fact, false);
  assert.equal(preimage.document_identity.official_filing_inferable_from_other_homes, false);
  assert.equal(preimage.retention.unknown_hold_blocks_deletion, true);
  assert.equal(preimage.retention.silent_purge_permitted, false);
  assert.equal(
    preimage.retention.surviving_derivatives_are_class_policy_not_instance_inventory, true);
  // WHEN A RETENTION PERIOD STARTS is in the hashed bytes too, so measuring one
  // from the source's own timestamp again would move the contract digest rather
  // than passing as an implementation detail.
  assert.deepEqual(preimage.retention.retention_clock_kinds, [...V5_F01_RETENTION_CLOCK_KINDS]);
  assert.equal(preimage.retention.default_retention_clock_kind,
    V5_F01_DEFAULT_RETENTION_CLOCK_KIND);
  assert.equal(preimage.retention.retention_starts_at_server_recorded_custody, true);
  assert.equal(preimage.retention.source_observed_at_starts_retention, false);
  assert.equal(preimage.retention.unknown_retention_clock_blocks_deletion, true);
  assert.equal(preimage.retention.caller_may_supply_retention_clock, false);
  // Said out loud rather than implied by the refusals: no producer for an explicit
  // retention-clock event exists in this slice, so a class that registers one
  // cannot reach a deletion at all.
  assert.equal(preimage.retention.explicit_retention_clock_event_producer_established, false);
  // The settled derivative-registration rule is IN the hashed bytes, so relaxing
  // any half of it moves the contract digest rather than passing unnoticed.
  assert.equal(preimage.derivative_registration.registration_precedes_derivative_completion, true);
  assert.equal(preimage.derivative_registration.only_trusted_producer_workflows_register_links,
    true);
  assert.equal(preimage.derivative_registration.links_are_provenance_not_exhaustive_inventory,
    true);
  assert.equal(preimage.derivative_registration.empty_link_set_means_verified_absence, false);
  assert.equal(preimage.derivative_registration.unknown_coverage_blocks_deletion, true);
  assert.equal(preimage.derivative_registration.caller_may_assert_coverage, false);
  assert.equal(preimage.derivative_registration.registration_authorizes_deletion, false);
  assert.deepEqual(preimage.derivative_registration.coverage_states,
    [...V5_F01_DERIVATIVE_COVERAGE_STATES]);
  assert.equal(preimage.caller_authority.caller_may_supply_actor, false);
  assert.equal(preimage.caller_authority.caller_may_select_tenant, false);
  // The caller's own registries are policy, not module identity, so they are not
  // in the hashed bytes.
  assert.ok(!canonicalJson(preimage).includes(REGISTRY.registry_digest));

  assert.equal(v5F01PolicyDigest(), digest(preimage));
  assert.equal(v5F01PolicyCanonicalBytes(), canonicalJson(preimage));

  const projection = v5F01AuthorityProjection();
  assert.equal(projection.policy_digest, v5F01PolicyDigest());
  assert.deepEqual(projection.decision_ids, [...V5_F01_SETTLED_DECISION_IDS]);
  assert.deepEqual(projection.homes, [...V5_F01_HOMES]);
  // Phase 1 completes none of the tail, and says so rather than being silent.
  assert.equal(projection.persistence_installed, false);
  assert.equal(projection.handlers_registered, false);
  assert.equal(projection.live_adapter_available, false);
  assert.equal(projection.provider_readback_performed, false);
  assert.equal(projection.f01_source_complete, false);
  assert.equal(projection.foundation_or_global_source_authority_accepted, false);
  assert.equal(projection.accepts_anything, false);

  assert.deepEqual(v5F01AuthorityProjection({ expected_policy_digest: projection.policy_digest }),
    projection);
  throwsCode(() => v5F01AuthorityProjection({ expected_policy_digest: `sha256:${"f".repeat(64)}` }),
    "stale_expected_digest");
  throwsCode(() => v5F01AuthorityProjection({ expected_policy_digest: "nope" }), "invalid_digest");
});

test("one positive registry exercises every home and all nine decision obligations", () => {
  // One concrete, positive obligation per settled decision, taken through the
  // real evaluators against the one synthetic registry. The point is coverage
  // that can be counted rather than asserted: if a decision loses its
  // obligation here, the set below stops matching the nine settled ids.
  const proved = {};
  const prove = (id, value) => { proved[id] = value; assert.ok(value, id); };

  // Q004 — one typed home for every fact, and no hidden authority anywhere.
  const homes = new Set(V5_F01_AUTHORITATIVE_FACT_CLASSES.map(fact_class =>
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class }).authoritative_home));
  prove("Q004.D1", homes.size === V5_F01_HOMES.length &&
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class: "prompt",
      claimed_authoritative: true }).decision === "refuse");

  // Q052 — current state, append-only event and mutation receipt, all three.
  const accepted = resolveFor(observation(), currentState());
  prove("Q052.D1", accepted.decision === "accept" &&
    accepted.current_state_transition.record_kind === "current_state_transition" &&
    accepted.event.record_kind === "append_only_event" &&
    accepted.mutation_receipt.record_kind === "mutation_receipt" &&
    accepted.event.append_only === true);

  // Q054 — Salesforce field authority through a governed adapter, reconciled
  // visibly, with Neon unable to write back over it.
  const reconciled = resolveFor(observation({ version: 4, value_digest: VALUE_C }), currentState());
  const backwrite = resolveFor(observation({
    source_system: SOURCES.neon, native_identity: { ...NATIVE, source_system: SOURCES.neon },
  }), currentState());
  prove("Q054.D1", accepted.event.provenance.adapter_kind === "governed_browser_adapter" &&
    reconciled.decision === "reconcile" && reconciled.reconciliation_item.visible === true &&
    backwrite.reason_id === "forbidden_write_direction");

  // Q071 — a generic artifact and a reviewable proposal, with no live adapter.
  const artifact = admit();
  const proposal = propose();
  prove("Q071.D1", artifact.decision === "allow" && artifact.is_fact === false &&
    proposal.decision === "allow" && proposal.becomes_fact === false &&
    admit({ evidence_class: "tour_rights_receipt" }).decision === "refuse");

  // Q108 — typed homes for facts, rules, decisions, work, documents and source
  // conversations; Markdown non-authoritative; summaries and embeddings
  // disposable.
  const typed = ["operating_fact", "rule", "decision", "work_item", "document_metadata",
    "source_conversation"].every(fact_class =>
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class })
      .authoritative_home === "neon_record_layer");
  const disposable = ["summary", "embedding"].every(fact_class =>
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class })
      .disposition === "disposable_index");
  prove("Q108.D1", typed && disposable &&
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class: "markdown_render",
      claimed_authoritative: true }).decision === "refuse");

  // Q125 — the three homes, and the OneDrive failure that stays visible.
  const filed = projectDoc();
  const unfiled = projectDoc({ onedrive_identity: null });
  prove("Q125.D1", filed.decision === "allow" && filed.official_filing_state === "filed" &&
    unfiled.reason_id === "incomplete_official_filing" &&
    unfiled.object_storage_success_implies_official_filing === false);

  // Q129 — a central per-class registry with home, period, holds, proof,
  // constraints and surviving derivatives. THAT AND NOTHING ELSE. An earlier
  // revision conjoined the derivative-registration properties into this oracle,
  // which quietly made them part of Q129.D1's acceptance predicate; they are a
  // session approval rather than settled text, and are proved on their own below.
  const permitted = deletion();
  prove("Q129.D1", permitted.decision === "allow" &&
    permitted.deletion_receipt.surviving_derivatives.length === 2 &&
    permitted.deletion_receipt.observed_surviving_derivatives.length === 2 &&
    deletion({ holds: [{ hold_id: "h", state: "active", placed_at: T.early }] })
      .reason_id === "active_hold_blocks_deletion");

  // Q135 — five independent state axes over the Neon/OneDrive/object-storage
  // identity split, round-tripping.
  prove("Q135.D1", filed.preparation_state === "approved_for_delivery" &&
    filed.delivery_state === "delivered" && filed.signature_state === "fully_executed" &&
    filed.validity_state === "effective" && filed.version_state === "current" &&
    filed.neon_identity !== null && filed.object_storage_identity !== null &&
    filed.onedrive_identity !== null);

  // Q155 — graphs, children, decisions, contracts and acceptance in the record
  // layer; diagrams, views and temporary JSON as projections and evidence.
  const recordLayer = ["authoritative_graph", "graph_child_reference", "decision", "contract",
    "acceptance"].every(fact_class =>
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class })
      .authoritative_home === "neon_record_layer");
  const projections = ["diagram", "operator_view"].every(fact_class =>
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class })
      .disposition === "generated_projection");
  prove("Q155.D1", recordLayer && projections &&
    projectRecordHome({ tenant: ORGANIZATION_TENANT_ID, fact_class: "temporary_json" })
      .disposition === "review_evidence");

  assert.deepEqual(Object.keys(proved).sort(), [...V5_F01_SETTLED_DECISION_IDS],
    "every settled decision must have a positive obligation proved here");

  // THE REGISTRATION RULE IS PROVED SEPARATELY, AND NAMED FOR WHAT IT IS.
  //
  // It is not one of the nine and it has no canonical decision id, so it is kept
  // OUT of `proved` — a `prove()` call under an invented id would put it in the
  // set above and make the count wrong; a `prove()` call under Q129.D1 would
  // attach it to a settled decision's oracle, which is where it was. Its only
  // honest citation is the session that approved it:
  //
  //   native task 01a0869f-fe0d-7493-bda3-ab8b3c0d6683
  //   user turn   01a08779-6b68-7013-bab1-369cf616254f
  //
  // Both halves still have to hold; only the attribution changed.
  assert.equal(register().derivative_link.establishes_coverage, false,
    "session-approved registration rule: a link establishes no coverage");
  assert.equal(deletion({ derivative_coverage: coverageOf({ state: "unknown" }) }).reason_id,
    "derivative_coverage_unknown",
    "session-approved registration rule: unknown coverage blocks the deletion");
  assert.ok(!Object.prototype.hasOwnProperty.call(proved, "Q129.D2"),
    "no decision id may be invented for the registration rule");

  // AND SO IS THE RETENTION-CLOCK RULE, for the same reason and on the same terms.
  //
  // Q129.D1 settles that the registry carries a default retention period per
  // class. It does not settle WHEN that period starts, and reading "default
  // retention" as "from the artifact's observed instant" was an implementation
  // choice rather than settled text. The rule that a period starts at
  // server-stamped custody was accepted directly by the user in
  //
  //   native task 01a0869f-fe0d-7493-bda3-ab8b3c0d6683
  //   user turn   01a08825-f388-70a2-b1a0-18ee866edde7
  //
  // and carries no canonical decision id, because none was issued for it. It is
  // kept OUT of `proved` so the count above stays the nine, and asserted here so
  // it is still binding.
  assert.equal(deletion({ retention_clock: clockOf({ started_at: T.early }) }).reason_id,
    "retention_period_not_elapsed",
    "accepted retention-clock rule: the period runs from custody");
  assert.equal(deletion({ retention_clock: null }).reason_id, "retention_clock_missing",
    "accepted retention-clock rule: an underivable trigger blocks the deletion");
  assert.equal(deletion().deletion_receipt.retention_clock.source_observed_at_used, false,
    "accepted retention-clock rule: the source's observed instant is never the clock");
  assert.ok(!Object.prototype.hasOwnProperty.call(proved, "Q129.D3"),
    "no decision id may be invented for the retention-clock rule either");
});

// ===========================================================================
// Corrections raised by the frozen independent review of commit 2aa98b43.
//
// Each block below is a durable invariant, not a special case for the probe
// literals that found it: the tests drive the same rules through fixtures of
// their own, and every one of them is paired with the positive case it must not
// break.
// ===========================================================================

const TEST_PATH = fileURLToPath(new URL("./record-source-authority.v5.test.mjs", import.meta.url));

test("both committed files are ordinary reviewable text, with no hidden code points", () => {
  // The module refuses control characters in its inputs, so it cannot contain
  // any itself. A literal NUL in the dedupe separator made the source read as
  // binary to file(1), rg and git diff, which is how a 2575-line module became
  // un-reviewable by ordinary tooling.
  //
  // The scan is written with NUMERIC code points on purpose. Spelling the
  // offenders as literals or escapes here would put the very bytes under test
  // into the file doing the testing.
  for (const path of [SRC_PATH, TEST_PATH]) {
    const text = readFileSync(path, "utf8");
    const offenders = [];
    for (let i = 0; i < text.length; i += 1) {
      const code = text.charCodeAt(i);
      const isOrdinaryWhitespace = code === 9 || code === 10 || code === 13;
      if (isOrdinaryWhitespace) continue;
      const isC0 = code < 0x20;
      const isC1 = code >= 0x7f && code <= 0x9f;
      const isZeroWidthOrBidi = (code >= 0x200b && code <= 0x200f) ||
        (code >= 0x202a && code <= 0x202e) || (code >= 0x2060 && code <= 0x2064) ||
        (code >= 0x2066 && code <= 0x2069);
      const isBom = code === 0xfeff;
      const isLoneSurrogate = code >= 0xd800 && code <= 0xdfff &&
        !(code <= 0xdbff && i + 1 < text.length &&
          text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff);
      if (isC0 || isC1 || isZeroWidthOrBidi || isBom || isLoneSurrogate) {
        offenders.push({ index: i, code: code.toString(16) });
      }
    }
    assert.deepEqual(offenders, [], `${path} must contain no control or invisible code points`);
    assert.ok(text.isWellFormed(), `${path} must be well-formed Unicode`);
  }
  // THE SEPARATOR IS TESTED BY ITS BEHAVIOUR, NOT BY ITS VALUE. Asserting that a
  // NUL is rejected inside an identifier proves nothing about the separator the
  // module actually folds with — it stays true however that constant is set. So
  // this drives the property that matters: for every character a validated
  // identifier is allowed to contain, the pair that WOULD collide if the
  // separator were that character must still compile to two distinct entries.
  // Any separator drawn from the identifier charset fails one of these, and so
  // does an empty one.
  const collidingUnder = ["_", "-", ".", ":", "/", "@", "!", "+", "=", ""];
  for (const character of collidingUnder) {
    const left = entry({ entity: "a", field: `b${character}c` });
    const right = entry({ entity: `a${character}b`, field: "c" });
    const compiled = compileFieldAuthorityRegistry(policyOf(left, right));
    assert.equal(compiled.entries.length, 2,
      `entity/field pairs must not fold to one key when the separator is "${character}"`);
  }
});

test("BOUNDARY (current state): an established record is bound to the registry before it is believed", () => {
  // Owner: the record can only have been established by the registered owner.
  const wrongOwner = resolveFor(observation(), currentState({ owner_source: SOURCES.outlook }));
  assert.equal(wrongOwner.decision, "refuse");
  assert.equal(wrongOwner.reason_id, "current_state_owner_not_registered_owner");
  assert.equal(wrongOwner.established_owner_source, SOURCES.outlook);
  assert.equal(wrongOwner.registered_owner_source, SOURCES.sf);
  assert.equal(wrongOwner.current_state_transition, null);

  // Required identity: absence is a state that should never have been written,
  // not an absent constraint.
  const noAccount = resolveFor(observation(), currentState({ account: null }));
  assert.equal(noAccount.reason_id, "current_state_missing_account_identity");
  const noNative = resolveFor(observation(), currentState({ native_identity: null }));
  assert.equal(noNative.reason_id, "current_state_missing_native_identity");

  // The established native identity must belong to the source that owns it.
  const foreignNative = resolveFor(observation(),
    currentState({ native_identity: { ...NATIVE, source_system: SOURCES.outlook } }));
  assert.equal(foreignNative.reason_id, "current_state_native_identity_source_mismatch");

  // A field whose policy requires neither identity is unaffected.
  assert.equal(resolveFor(sharedNote(), sharedNoteState()).decision, "accept");
});

test("BOUNDARY (event chain): the chain is authenticated, extended, and cannot overflow", () => {
  // An established record has had at least one event, so sequence zero
  // contradicts its own existence.
  const zeroSeq = resolveFor(observation(),
    currentState({ event_seq: 0, last_event_digest: null }));
  assert.equal(zeroSeq.decision, "refuse");
  assert.equal(zeroSeq.reason_id, "current_state_event_sequence_invalid");

  // And the digest of that last event is the link the next one extends.
  const broken = resolveFor(observation(), currentState({ last_event_digest: null }));
  assert.equal(broken.decision, "refuse");
  assert.equal(broken.reason_id, "current_state_event_chain_broken");
  assert.equal(broken.event, null);

  // A sequence that cannot be incremented exactly stops rather than landing on
  // a float that is no longer a distinct ordinal.
  const overflow = resolveFor(observation(),
    currentState({ event_seq: Number.MAX_SAFE_INTEGER }));
  assert.equal(overflow.decision, "refuse");
  assert.equal(overflow.reason_id, "event_sequence_overflow");
  assert.equal(overflow.max_safe_event_seq, Number.MAX_SAFE_INTEGER);
  const nearLimit = resolveFor(observation(),
    currentState({ event_seq: Number.MAX_SAFE_INTEGER - 1 }));
  assert.equal(nearLimit.decision, "accept", "one below the ceiling still works");
  assert.equal(nearLimit.event.event_seq, Number.MAX_SAFE_INTEGER);

  // POSITIVE GENESIS: no established state, sequence one, no predecessor.
  const genesis = resolveFor(observation(), null);
  assert.equal(genesis.decision, "accept");
  assert.equal(genesis.event.event_seq, 1);
  assert.equal(genesis.event.previous_event_digest, null);
  assert.equal(genesis.event.event_kind, "source_field_established");

  // POSITIVE CONTINUATION: each accepted change extends the chain by exactly
  // one and carries the prior digest forward, so a real sequence can be walked.
  let seq = genesis.event.event_seq;
  let previous = digest(genesis.event);
  let value = genesis.current_state_transition.to_value_digest;
  let version = genesis.current_state_transition.to_version;
  for (let step = 0; step < 3; step += 1) {
    const next = resolveFor(
      observation({ version: version + 1, value_digest: D(20 + step) }),
      currentState({
        value_digest: value, version, event_seq: seq, last_event_digest: previous,
      }));
    assert.equal(next.decision, "accept", `continuation step ${step}`);
    assert.equal(next.event.event_seq, seq + 1);
    assert.equal(next.event.previous_event_digest, previous);
    assert.equal(next.current_state_transition.from_value_digest, value);
    assert.equal(next.current_state_transition.from_version, version);
    seq = next.event.event_seq;
    previous = digest(next.event);
    value = next.current_state_transition.to_value_digest;
    version = next.current_state_transition.to_version;
  }
  assert.equal(seq, 4);
});

test("MUTATION KILL (owner): registry taint is authoritative and a caller cannot soften it", () => {
  // The exact laundering shape: a corporate-source observation declaring itself
  // first-party would turn a tainted value into a trusted one.
  const downgrade = resolveFor(
    observation({ taint_class: "first_party_record_layer" }), currentState());
  assert.equal(downgrade.decision, "refuse");
  assert.equal(downgrade.reason_id, "taint_class_mismatch");
  assert.equal(downgrade.registered_taint_class, "corporate_source_of_record");
  assert.equal(downgrade.declared_taint_class, "first_party_record_layer");

  // It refuses in the other direction too: disagreement is the fault, not the
  // direction of travel.
  const upgrade = resolveFor(
    sharedNote({ taint_class: "corporate_source_of_record" }), sharedNoteState());
  assert.equal(upgrade.reason_id, "taint_class_mismatch");

  // Restating the registered class is a harmless redundancy check, and omitting
  // it inherits. Both accept, and both record the registry's class.
  const restated = resolveFor(
    observation({ taint_class: "corporate_source_of_record" }), currentState());
  assert.equal(restated.decision, "accept");
  assert.equal(restated.event.taint_class, "corporate_source_of_record");
  const inherited = resolveFor(observation(), currentState());
  assert.equal(inherited.decision, "accept");
  assert.equal(inherited.event.taint_class, "corporate_source_of_record");
  assert.equal(digest(restated.event), digest(inherited.event),
    "restating the registered taint must not change the record");
});

test("BOUNDARY (readback): every supplied readback is read, and a used one must be possible", () => {
  // Validated even where policy does not require one: a field checked only when
  // it is required is a field a caller can attach anything to the rest of the
  // time. shared_note has readback_required false.
  throwsCode(() => resolveFor(
    sharedNote({ readback: { smuggled: true } }), sharedNoteState()), "unknown_field");
  throwsCode(() => resolveFor(
    sharedNote({ readback: { confirmed: true, readback_at: T.mid } }), sharedNoteState()),
    "missing_field");
  throwsCode(() => resolveFor(
    sharedNote({ readback: { confirmed: "yes", readback_at: T.mid, readback_value_digest: VALUE_A } }),
    sharedNoteState()), "invalid_shape");
  throwsCode(() => resolveFor(
    sharedNote({ readback: { confirmed: true, readback_at: "not-a-time", readback_value_digest: VALUE_A } }),
    sharedNoteState()), "invalid_timestamp");
  // A well-formed readback on a field that does not need one is simply carried.
  assert.equal(resolveFor(sharedNote({
    readback: { confirmed: true, readback_at: T.mid, readback_value_digest: VALUE_A },
  }), sharedNoteState()).decision, "accept");

  // A readback proves the source still held the value after it was observed and
  // before now. Outside that window it proves nothing about this observation.
  const early = resolveFor(observation({
    observed_at: T.late,
    readback: { confirmed: true, readback_at: T.mid, readback_value_digest: VALUE_A },
  }), currentState());
  assert.equal(early.decision, "refuse");
  assert.equal(early.reason_id, "readback_precedes_observation");

  const late = resolveFor(observation({
    readback: { confirmed: true, readback_at: T.future, readback_value_digest: VALUE_A },
  }), currentState());
  assert.equal(late.decision, "refuse");
  assert.equal(late.reason_id, "readback_after_now");

  // The boundaries themselves are inside the window, not outside it.
  for (const readback_at of [T.mid, T.late, NOW]) {
    const ok = resolveFor(observation({
      observed_at: T.mid,
      readback: { confirmed: true, readback_at, readback_value_digest: VALUE_A },
    }), currentState());
    assert.equal(ok.decision, "accept", readback_at);
  }
});

test("BOUNDARY (privacy): no artifact is admitted without meeting the privacy boundary", () => {
  const unclassified = admitCorporateArtifact({
    tenant: ORGANIZATION_TENANT_ID, now: NOW,
    artifact: (() => { const a = artifactOf(); delete a.declared_data_classes; return a; })(),
  });
  assert.equal(unclassified.decision, "refuse");
  assert.equal(unclassified.reason_id, "missing_sensitivity_classification");
  assert.equal(unclassified.artifact, null);

  throwsCode(() => admit({ declared_data_classes: [] }), "invalid_shape");
  throwsCode(() => admit({ declared_data_classes: ["market_comp", "market_comp"] }),
    "duplicate_data_class");

  // S01 now runs on every admission rather than only when classes happen to be
  // present, so each of its three answers is reachable.
  assert.equal(admit({ declared_data_classes: ["phi"] }).decision, "refuse");
  assert.equal(admit({ declared_data_classes: ["aggregate_patient_location_heatmap"] }).decision,
    "needs_independent_privacy_route");
  assert.equal(admit({ declared_data_classes: ["market_comp"] }).decision, "allow");
});

test("BOUNDARY (artifact): prior evidence is validated to the same standard before it is compared", () => {
  // A prior whose identity DIFFERS used to skip validation entirely, so the one
  // piece of evidence that could refuse the admission was never read.
  throwsCode(() => admit({}, artifactOf({ native_version: "other", source_system: "bad space" })),
    "invalid_identifier");
  throwsCode(() => admit({}, artifactOf({ native_version: "other", source_account: 42 })),
    "invalid_shape");
  throwsCode(() => admit({}, artifactOf({ native_version: "other", content_digest: "bad" })),
    "invalid_digest");
  throwsCode(() => admit({}, (() => {
    const a = artifactOf({ native_version: "other" }); delete a.declared_data_classes; return a;
  })()), "missing_field");
  throwsCode(() => admit({}, artifactOf({ native_version: "other", evidence_class: "invented" })),
    "unknown_evidence_class");

  // A well-formed prior still behaves exactly as before.
  const prior = artifactOf({ content_digest: VALUE_A });
  assert.equal(admit({ content_digest: VALUE_C }, prior).reason_id, "artifact_identity_conflict");
  assert.equal(admit({ content_digest: VALUE_A }, prior).decision, "allow");
  assert.equal(admit({ content_digest: VALUE_C, native_version: "rev-2" }, prior).decision, "allow");
});

test("MUTATION KILL (proposal): one reference is one piece of evidence", () => {
  throwsCode(() => propose({ evidence_refs: ["same", "same"] }), "duplicate_evidence_ref");
  throwsCode(() => propose({ evidence_refs: ["a", "b", "a"] }), "duplicate_evidence_ref");
  // Distinct refs are accepted, and the link digest depends only on the SET, so
  // two callers listing the same evidence in a different order agree.
  const forward = propose({ evidence_refs: ["ref-a", "ref-b"] });
  const reversed = propose({ evidence_refs: ["ref-b", "ref-a"] });
  assert.equal(forward.decision, "allow");
  assert.equal(forward.proposal_digest, reversed.proposal_digest);
  assert.deepEqual(forward.link.evidence_refs, ["ref-a", "ref-b"]);
});

test("MUTATION KILL (hold): an absent inventory is not a verified empty one", () => {
  // "No holds were supplied" and "we looked and there are none" are different
  // facts, and only the second can authorize a deletion.
  const noHolds = deletion({ holds: undefined });
  assert.equal(noHolds.decision, "refuse");
  assert.equal(noHolds.reason_id, "holds_inventory_missing");
  assert.equal(noHolds.deletion_receipt, null);

  const noDerivatives = deletion({ derivatives: undefined });
  assert.equal(noDerivatives.decision, "refuse");
  assert.equal(noDerivatives.reason_id, "derivative_inventory_missing");
  assert.equal(noDerivatives.deletion_receipt, null);

  // The explicitly empty case is still available and still works.
  assert.equal(deletion({
    artifact_class: "draft_document", artifact_home: "object_storage",
    created_at: T.late, deletion_proof: null, derivatives: [], satisfied_constraints: [], holds: [],
  }).decision, "allow");

  // A SUPPLIED INVENTORY IS STILL READ — but it is read as an observation, which
  // is the semantic correction this subcase now carries. An earlier revision
  // refused here with surviving_derivative_unaccounted whenever a kind the CLASS
  // policy permits was missing from the observed inventory. Q129 settles which
  // derivative KINDS survive a deletion, not that every instance produced one of
  // each, so a lease that only ever produced an abstract is deletable; the case
  // is proved in full in "Q129: class policy names which derivative KINDS
  // survive, not that each one exists". Nothing that guards a deletion was
  // dropped with it: the missing-inventory, missing-holds, unknown-coverage,
  // unregistered-kind and duplicate refusals above and below are untouched, and
  // this allowance still passes every one of them.
  const partial = deletion({ derivatives: ["lease_abstract"] });
  assert.equal(partial.decision, "allow");
  assert.equal(partial.reason_id, "deletion_permitted");

  // What the retired rule was protecting is kept by SAYING WHICH IS WHICH rather
  // than by refusing: the class policy and the instance observation ride under
  // different names, on the answer and in the hashed receipt, so the receipt
  // still names no survivor anybody failed to look for.
  assert.deepEqual(partial.surviving_derivatives, ["deal_economics_summary", "lease_abstract"]);
  assert.deepEqual(partial.observed_surviving_derivatives, ["lease_abstract"]);
  assert.deepEqual(partial.deletion_receipt.surviving_derivatives,
    ["deal_economics_summary", "lease_abstract"]);
  assert.deepEqual(partial.deletion_receipt.observed_surviving_derivatives, ["lease_abstract"]);
  assert.equal(partial.deletion_receipt.surviving_derivatives_are_class_policy, true);
  assert.equal(partial.unaccounted_derivatives, undefined,
    "the retired instance-existence rule must not return under its old name");

  // The inventory is judged, not merely echoed. A kind the class policy never
  // registered as surviving still blocks the deletion outright, and a repeated
  // entry is a malformed inventory rather than a policy question.
  const stray = deletion({ derivatives: ["lease_abstract", "shadow_copy"] });
  assert.equal(stray.decision, "refuse");
  assert.equal(stray.reason_id, "unregistered_derivative_blocks_deletion");
  assert.deepEqual(stray.unregistered_derivatives, ["shadow_copy"]);
  assert.equal(stray.deletion_receipt, null);
  assert.equal(stray.silent_purge, false);
  throwsCode(() => deletion({
    derivatives: ["lease_abstract", "lease_abstract", "deal_economics_summary"],
  }), "duplicate_derivative");
});

test("MUTATION KILL (hold): hold and proof timestamps must describe something that could have happened", () => {
  const placedAhead = deletion({
    holds: [{ hold_id: "h", state: "released", placed_at: T.future, released_at: T.future }],
  });
  assert.equal(placedAhead.decision, "refuse");
  assert.equal(placedAhead.reason_id, "hold_placed_after_now");

  const noRelease = deletion({ holds: [{ hold_id: "h", state: "released", placed_at: T.mid }] });
  assert.equal(noRelease.decision, "refuse");
  assert.equal(noRelease.reason_id, "released_hold_missing_release_time");

  const backwards = deletion({
    holds: [{ hold_id: "h", state: "released", placed_at: T.late, released_at: T.early }],
  });
  assert.equal(backwards.reason_id, "hold_release_time_incoherent");
  const releasedAhead = deletion({
    holds: [{ hold_id: "h", state: "released", placed_at: T.early, released_at: T.future }],
  });
  assert.equal(releasedAhead.reason_id, "hold_release_time_incoherent");
  const expiredBackwards = deletion({
    holds: [{ hold_id: "h", state: "expired", placed_at: T.late, released_at: T.early }],
  });
  assert.equal(expiredBackwards.reason_id, "hold_release_time_incoherent");

  // An expired hold need not name a release moment, and a coherent released one
  // still clears the way.
  assert.equal(deletion({ holds: [{ hold_id: "h", state: "expired", placed_at: T.early }] })
    .decision, "allow");
  assert.equal(deletion({
    holds: [{ hold_id: "h", state: "released", placed_at: T.early, released_at: T.mid }],
  }).decision, "allow");

  // The proof must sit inside the artifact's own lifetime.
  const proofAhead = deletion({
    deletion_proof: {
      proof_ref: "p", artifact_digest: VALUE_A, proof_digest: D(4), executed_at: T.future,
    },
  });
  assert.equal(proofAhead.decision, "refuse");
  assert.equal(proofAhead.reason_id, "deletion_proof_after_now");

  const proofBefore = deletion({
    deletion_proof: {
      proof_ref: "p", artifact_digest: VALUE_A, proof_digest: D(4),
      executed_at: "2025-01-01T00:00:00Z",
    },
  });
  assert.equal(proofBefore.decision, "refuse");
  assert.equal(proofBefore.reason_id, "deletion_proof_precedes_artifact");

  // The boundaries are inside the window.
  for (const executed_at of [LONG_AGO, T.late, NOW]) {
    assert.equal(deletion({
      deletion_proof: {
        proof_ref: "p", artifact_digest: VALUE_A, proof_digest: D(4), executed_at,
      },
    }).decision, "allow", executed_at);
  }
  for (const result of [placedAhead, noRelease, backwards, proofAhead, proofBefore]) {
    assert.equal(result.deletion_receipt, null, result.reason_id);
    assert.equal(result.silent_purge, false, result.reason_id);
  }
});

// ===========================================================================
// Corrections raised by the frozen independent re-review of commit 3d8783bd
// (F01-DOMAIN-007, F01-DOMAIN-008, F01-DOMAIN-009).
//
// One shape connects all three: a caller SUPPLIES evidence the policy did not
// demand, the module reads its syntax and then ignores its content, and the
// impossible evidence rides along inside an accepted or allowed answer. The
// tests below prove the general rule in each place — supplied evidence is judged
// on its own terms — and each one is paired with the positive case it must not
// break, including the "policy did not require any" path that must keep working
// when nothing is supplied at all.
// ===========================================================================

test("BOUNDARY (readback): a supplied readback binds confirmation, value and time even when policy asks for none", () => {
  // shared_note has readback_required false, so every refusal below comes from
  // the SUPPLIED evidence rather than from policy demanding it.
  const optional = REGISTRY.entries.find(e => e.field === "shared_note");
  assert.equal(optional.readback_required, false, "the fixture must exercise the optional path");

  const cases = [
    [{ confirmed: false, readback_at: T.mid, readback_value_digest: VALUE_A },
      "readback_not_confirmed"],
    [{ confirmed: true, readback_at: T.mid, readback_value_digest: VALUE_C },
      "readback_value_mismatch"],
    [{ confirmed: true, readback_at: T.early, readback_value_digest: VALUE_A },
      "readback_precedes_observation"],
    [{ confirmed: true, readback_at: T.future, readback_value_digest: VALUE_A },
      "readback_after_now"],
  ];
  for (const [readback, reason_id] of cases) {
    // Against an established record...
    const continued = resolveFor(sharedNote({ readback }), sharedNoteState());
    assert.equal(continued.decision, "refuse", reason_id);
    assert.equal(continued.reason_id, reason_id);
    assert.equal(continued.applied, false, reason_id);
    assert.equal(continued.current_state_transition, null, reason_id);
    assert.equal(continued.event, null, reason_id);
    assert.equal(continued.mutation_receipt, null, reason_id);
    // ...and on the genesis path, where the owner would otherwise establish the
    // field outright and no earlier state exists to contradict it.
    const genesis = resolveFor(sharedNote({ readback }), null);
    assert.equal(genesis.decision, "refuse", `genesis ${reason_id}`);
    assert.equal(genesis.reason_id, reason_id, "genesis");
    assert.equal(genesis.event, null, "genesis");
  }

  // POSITIVE, no evidence supplied: a field whose policy needs no readback still
  // accepts without one. Validating supplied evidence must not invent a
  // requirement nobody declared.
  const none = resolveFor(sharedNote(), sharedNoteState());
  assert.equal(none.decision, "accept");
  assert.equal(none.event.event_seq, 4);

  // POSITIVE, evidence supplied and sound: the window is inclusive at both ends,
  // so a readback taken at the moment of observation and one taken exactly at
  // `now` are both accepted rather than sitting one tick outside the rule.
  for (const readback_at of [T.mid, T.late, NOW]) {
    const ok = resolveFor(sharedNote({
      readback: { confirmed: true, readback_at, readback_value_digest: VALUE_A },
    }), sharedNoteState());
    assert.equal(ok.decision, "accept", readback_at);
    assert.equal(ok.current_state_transition.to_value_digest, VALUE_A, readback_at);
  }

  // The policy obligation is RETAINED SEPARATELY: a field that requires a
  // readback still refuses when none is supplied, and that refusal is still its
  // own reason rather than being folded into the content checks.
  const required = REGISTRY.entries.find(e => e.field === "commission_amount");
  assert.equal(required.readback_required, true);
  assert.equal(resolveFor(observation({ readback: undefined }), currentState()).reason_id,
    "missing_readback");
  assert.equal(resolveFor(observation(), currentState()).decision, "accept");
});

test("BOUNDARY (current state): an established snapshot observed after now cannot authorize anything", () => {
  // The observation is impeccable and the established record is well formed in
  // every other respect; only its observation moment is impossible.
  const future = resolveFor(observation(), currentState({ observed_at: T.future }));
  assert.equal(future.decision, "refuse");
  assert.equal(future.reason_id, "current_state_observed_after_now");
  assert.equal(future.established_observed_at, T.future);
  assert.equal(future.applied, false);
  assert.equal(future.current_state_transition, null);
  assert.equal(future.event, null);
  assert.equal(future.mutation_receipt, null);
  assert.equal(future.reconciliation_item, null);

  // Refused BEFORE the version comparison, which is what stops an impossible
  // snapshot from being answered as if it were a party to a disagreement: the
  // same future state refuses identically whether the observation is newer,
  // equal-and-contradicting, stale, or a plain confirmation. None of them
  // reports a version ordering, because none of them got that far.
  for (const [override, label] of [
    [{ version: 5, value_digest: VALUE_A }, "newer"],
    [{ version: 4, value_digest: VALUE_C }, "equal, contradicting"],
    [{ version: 3, value_digest: VALUE_A }, "stale"],
    [{ version: 4, value_digest: VALUE_B }, "confirming"],
  ]) {
    const result = resolveFor(observation(override), currentState({ observed_at: T.future }));
    assert.equal(result.reason_id, "current_state_observed_after_now", label);
    assert.equal(result.version_ordering, undefined, label);
    assert.equal(result.current_state_transition, null, label);
  }

  // A second field with different policy behaves the same way, so this is the
  // rule and not a property of one fixture.
  const sharedFuture = resolveFor(sharedNote(), sharedNoteState({ observed_at: T.future }));
  assert.equal(sharedFuture.decision, "refuse");
  assert.equal(sharedFuture.reason_id, "current_state_observed_after_now");

  // POSITIVE BOUNDARY: a snapshot observed in the past accepts, and so does one
  // observed at exactly `now` — the rule is "after now", not "not now".
  for (const observed_at of [T.early, T.late, NOW]) {
    const ok = resolveFor(observation({ version: 6 }), currentState({ observed_at }));
    assert.equal(ok.decision, "accept", observed_at);
    assert.equal(ok.event.event_seq, 8, observed_at);
  }
  // And the established record's own syntax is still checked, not skipped.
  throwsCode(() => resolveFor(observation(), currentState({ observed_at: "2026-02-31T00:00:00Z" })),
    "invalid_timestamp");
});

test("BOUNDARY (deletion proof): a supplied proof binds artifact and lifetime even when policy asks for none", () => {
  // draft_document requires no proof, needs no waiting period and has no
  // constraints or derivatives, so every refusal below comes from the SUPPLIED
  // proof alone.
  const optional = RETENTION.classes.find(c => c.artifact_class === "draft_document");
  assert.equal(optional.deletion_proof_required, false, "the fixture must exercise the optional path");

  const draft = (overrides = {}) => deletion({
    artifact_class: "draft_document", artifact_home: "object_storage",
    created_at: T.late, deletion_proof: null, derivatives: [], satisfied_constraints: [],
    holds: [], ...overrides,
  });
  const proof = (overrides = {}) => ({
    proof_ref: "proof-forged-1", artifact_digest: VALUE_A, proof_digest: D(4),
    executed_at: T.late, ...overrides,
  });

  const wrongArtifact = draft({ deletion_proof: proof({ artifact_digest: VALUE_C }) });
  assert.equal(wrongArtifact.decision, "refuse");
  assert.equal(wrongArtifact.reason_id, "deletion_proof_mismatch");
  assert.equal(wrongArtifact.proof_artifact_digest, VALUE_C);

  const ahead = draft({ deletion_proof: proof({ executed_at: T.future }) });
  assert.equal(ahead.decision, "refuse");
  assert.equal(ahead.reason_id, "deletion_proof_after_now");
  assert.equal(ahead.executed_at, T.future);

  const before = draft({ deletion_proof: proof({ executed_at: T.mid }) });
  assert.equal(before.decision, "refuse");
  assert.equal(before.reason_id, "deletion_proof_precedes_artifact");
  assert.equal(before.created_at, T.late);

  // No refusal produced a receipt, and the unverified proof reference reached no
  // part of the answer — the durable false claim is the whole harm here.
  for (const result of [wrongArtifact, ahead, before]) {
    assert.equal(result.deletion_receipt, null, result.reason_id);
    assert.equal(result.silent_purge, false, result.reason_id);
    assert.equal(result.purge_without_proof, false, result.reason_id);
    assert.ok(!JSON.stringify(result).includes("proof-forged-1"),
      `${result.reason_id} must not copy an unverified proof into its answer`);
  }

  // POSITIVE, no proof supplied: the proof-not-required class still deletes
  // through the same path, with the requirement recorded as false and the proof
  // slots explicitly empty rather than absent.
  const unproved = draft();
  assert.equal(unproved.decision, "allow");
  assert.equal(unproved.reason_id, "deletion_permitted");
  assert.equal(unproved.deletion_receipt.deletion_proof_required, false);
  assert.equal(unproved.deletion_receipt.deletion_proof_ref, null);
  assert.equal(unproved.deletion_receipt.deletion_proof_digest, null);

  // POSITIVE, a sound proof supplied where none was required: it is allowed and
  // recorded, because the correction binds optional evidence rather than
  // forbidding it. The lifetime window is inclusive at both ends.
  for (const executed_at of [T.late, NOW]) {
    const proved = draft({ deletion_proof: proof({ proof_ref: "proof-sound-1", executed_at }) });
    assert.equal(proved.decision, "allow", executed_at);
    assert.equal(proved.deletion_receipt.deletion_proof_ref, "proof-sound-1", executed_at);
    assert.equal(proved.deletion_receipt.deletion_proof_digest, D(4), executed_at);
    assert.equal(proved.deletion_receipt.deletion_proof_required, false, executed_at);
  }

  // The policy obligation is RETAINED SEPARATELY: a class that requires a proof
  // still refuses when none is supplied, and the fully satisfied case still
  // allows.
  assert.equal(deletion({ deletion_proof: null }).reason_id, "missing_deletion_proof");
  assert.equal(deletion().decision, "allow");
});

test("BOUNDARY: impossible supplied evidence never reaches an accepted or allowed answer", () => {
  // The four shapes the independent edge probe reproduced, gathered in one place
  // so a future edit that re-separates "validated" from "required" fails here
  // whichever of the three sites it touches.
  const reproduced = [
    ["future optional readback", resolveFor(sharedNote({
      readback: { confirmed: true, readback_at: T.future, readback_value_digest: VALUE_A },
    }), sharedNoteState())],
    ["future current state", resolveFor(observation(), currentState({ observed_at: T.future }))],
    ["future optional deletion proof", deletion({
      artifact_class: "draft_document", artifact_home: "object_storage", created_at: T.late,
      derivatives: [], satisfied_constraints: [], holds: [],
      deletion_proof: {
        proof_ref: "proof-1", artifact_digest: VALUE_A, proof_digest: D(4), executed_at: T.future,
      },
    })],
    ["wrong-artifact optional deletion proof", deletion({
      artifact_class: "draft_document", artifact_home: "object_storage", created_at: T.late,
      derivatives: [], satisfied_constraints: [], holds: [],
      deletion_proof: {
        proof_ref: "proof-1", artifact_digest: VALUE_C, proof_digest: D(4), executed_at: T.late,
      },
    })],
  ];
  for (const [label, result] of reproduced) {
    assert.equal(result.decision, "refuse", label);
    assert.equal(result.effects.creates_effect, false, label);
    assert.ok(!JSON.stringify(result).includes("proof-1"), `${label} must record no proof reference`);
  }
  // The three positive controls the same probe checked still pass, so the
  // corrections closed the hole without closing the door.
  assert.equal(resolveFor(observation(), null).decision, "accept", "genesis");
  assert.equal(resolveFor(observation(), currentState()).decision, "accept", "continuation");
  assert.equal(deletion({
    artifact_class: "draft_document", artifact_home: "object_storage", created_at: T.late,
    deletion_proof: null, derivatives: [], satisfied_constraints: [], holds: [],
  }).decision, "allow", "proof-not-required deletion without proof");
});
