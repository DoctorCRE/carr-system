import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  assertProjectionDigest,
  canonicalProjectionDigest,
  validateProjectionComplete,
  validateProjectionDraft,
} from "../src/tour-operations-contract.js";

const root = path.resolve(import.meta.dirname, "../..");
const fixture = JSON.parse(fs.readFileSync(path.join(root, "mcp-server/test/fixtures/tour-operations-foundation.v1.json"), "utf8"));
const { projection_fact: fact, field_assertion: assertion, membership, projection, source_evidence: evidence, rights_receipt: rights } = fixture;
const addressAssertion = { ...assertion, id: "44444444-4444-4444-8444-444444444445", field_key: "display.address", value: "1 Synthetic Way" };
const addressFact = { ...fact, id: "55555555-5555-4555-8555-555555555556", field_assertion_id: addressAssertion.id, display_field_key: addressAssertion.field_key };
const completeFacts = [fact, addressFact];
const completeAssertions = [assertion, addressAssertion];
const completeInput = {
  projection,
  memberships: [membership],
  facts: completeFacts,
  assertions: completeAssertions,
  evidence: [evidence],
  rights: [rights],
  evidenceById: { [evidence.id]: evidence },
  rightsById: { [rights.id]: rights },
  lineage: [],
  revocations: [],
};
const withProjectionBody = (status = "approved") => ({
  ...completeInput,
  projection: { ...projection, status },
});

function sealedProjectionInput() {
  const input = withProjectionBody("draft");
  input.facts = input.facts.map(item => { const itemAssertion = completeAssertions.find(candidate => candidate.id === item.field_assertion_id); return { ...item, value: itemAssertion.value, source_evidence_id: itemAssertion.source_evidence_id, rights_receipt_id: itemAssertion.rights_receipt_id, observed_at: itemAssertion.observed_at, effective_from: itemAssertion.effective_from, effective_to: itemAssertion.effective_to }; });
  const digest = canonicalProjectionDigest(input);
  return {
    input,
    digest,
    projection: {
      ...input.projection,
      status: "approved",
      projection_digest: digest,
      seal_receipt: {
        id: "seal-1",
        organization_tenant_id: fixture.tenant,
        projection_id: projection.id,
        sealed_at: "2026-08-25T12:05:00Z",
        sealed_state: "approved",
        canonical_projection_digest: digest,
        actor_id: "actor:joe",
        receipt_digest: "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      },
    },
  };
}

test("canonical projection digest is key-order independent and detects tampering", () => {
  const first = { ...withProjectionBody("approved"), facts: [fact] };
  const reordered = {
    revocations: [],
    rightsById: { [rights.id]: { ...rights } },
    evidenceById: { [evidence.id]: { ...evidence } },
    assertions: { [assertion.id]: { ...assertion } },
    facts: [{ ...fact }],
    memberships: [{ ...membership }],
    projection: { status: "approved", as_of: projection.as_of, route_version: projection.route_version, projection_version: projection.projection_version, tour_id: projection.tour_id, organization_tenant_id: projection.organization_tenant_id, id: projection.id, facts_only: projection.facts_only },
    lineage: [],
  };
  const digest = canonicalProjectionDigest(first);
  assert.match(digest, /^sha256:[a-f0-9]{64}$/);
  assert.equal(canonicalProjectionDigest(reordered), digest);
  assert.equal(assertProjectionDigest(first, digest), true);
  assert.throws(() => assertProjectionDigest({ ...first, facts: [{ ...fact, display_field_key: "display.address" }] }, digest), /PROJECTION_DIGEST_MISMATCH|DIGEST/);
  assert.throws(() => assertProjectionDigest(first, "sha256:" + "0".repeat(64)), /PROJECTION_DIGEST_MISMATCH|DIGEST/);
});

test("canonical projection digest matches the database UTF-8 byte contract vector", () => {
  const input = {
    projection: {
      organization_tenant_id: "tenant-α",
      tour_id: "10000000-0000-4000-8000-000000000040",
      id: "10000000-0000-4000-8000-000000000060",
      projection_version: 1,
      route_version: 2,
      as_of: "2026-08-25T12:00:00Z",
    },
    facts: [
      {
        property_id: "10000000-0000-4000-8000-000000000011",
        field_assertion_id: "10000000-0000-4000-8000-000000000031",
        route_version: 2,
        display_field_key: "display.address",
      },
      {
        property_id: "10000000-0000-4000-8000-000000000010",
        field_assertion_id: "10000000-0000-4000-8000-000000000030",
        route_version: 2,
        display_field_key: "display.name",
      },
    ],
  };
  assert.equal(canonicalProjectionDigest(input),
    "sha256:73c90187e235a2e7262bf8de28ea4b61f69721cb8e60e8876092d3337d134bb7");
});

test("canonical projection digest matches PostgreSQL lowercase UUID text", () => {
  const lower = withProjectionBody("approved");
  const upper = {
    ...lower,
    projection: {
      ...lower.projection,
      id: lower.projection.id.toUpperCase(),
      tour_id: lower.projection.tour_id.toUpperCase(),
    },
    facts: lower.facts.map(item => ({
      ...item,
      property_id: item.property_id.toUpperCase(),
      field_assertion_id: item.field_assertion_id.toUpperCase(),
    })),
  };
  assert.equal(canonicalProjectionDigest(upper), canonicalProjectionDigest(lower));
});

test("canonical projection digest binds immutable public-map coordinate selection", () => {
  const base = { ...withProjectionBody("approved"), map_points: [{
    property_id: membership.property_id,
    coordinate_candidate_id: "10000000-0000-4000-8000-000000000070",
    entrance_verification_receipt_id: "10000000-0000-4000-8000-000000000071",
    route_version: projection.route_version,
  }] };
  const digest = canonicalProjectionDigest(base);
  assert.notEqual(digest, canonicalProjectionDigest({ ...base, map_points: [] }));
  assert.throws(() => assertProjectionDigest({ ...base, map_points: [{ ...base.map_points[0], coordinate_candidate_id: "10000000-0000-4000-8000-000000000072" }] }, digest), /PROJECTION_DIGEST_MISMATCH/);
});

test("canonical projection digest refuses authority accessors instead of rereading them", () => {
  const changingFact = { ...fact };
  delete changingFact.field_assertion_id;
  let reads = 0;
  Object.defineProperty(changingFact, "field_assertion_id", {
    enumerable: true,
    get() { return reads++ === 0 ? fact.field_assertion_id : addressAssertion.id; },
  });
  assert.throws(() => canonicalProjectionDigest({
    ...withProjectionBody("approved"), facts: [changingFact],
  }), /PROJECTION_INCOMPLETE/);
  assert.equal(reads, 0, "digest authority accessors must never execute");
});

test("canonical projection digest refuses coercible identifier objects", () => {
  let reads = 0;
  const coercibleId = {
    toString() {
      reads++;
      return reads === 1 ? fact.property_id : membership.property_id;
    },
  };
  assert.throws(() => canonicalProjectionDigest({
    ...withProjectionBody("approved"),
    facts: [{ ...fact, property_id: coercibleId }],
  }), /PROJECTION_INCOMPLETE/);
  assert.equal(reads, 0, "identifier objects must not be coerced");
});

test("canonical projection digest ignores inherited Array slice overrides", () => {
  const input = withProjectionBody("approved");
  const expected = canonicalProjectionDigest(input);
  const originalSlice = Array.prototype.slice;
  let actual;
  try {
    Array.prototype.slice = () => [];
    actual = canonicalProjectionDigest(input);
  } finally {
    Array.prototype.slice = originalSlice;
  }
  assert.equal(actual, expected);
});

test("complete-projection validation includes sealed public-map points in digest parity", () => {
  const sealed = sealedProjectionInput();
  const map_points = [{
    property_id: membership.property_id,
    coordinate_candidate_id: "10000000-0000-4000-8000-000000000070",
    entrance_verification_receipt_id: "10000000-0000-4000-8000-000000000071",
    route_version: projection.route_version,
  }];
  const digest = canonicalProjectionDigest({ ...sealed.input, map_points });
  const projectionWithMap = {
    ...sealed.projection,
    projection_digest: digest,
    seal_receipt: { ...sealed.projection.seal_receipt, canonical_projection_digest: digest },
  };
  assert.equal(validateProjectionComplete({ ...sealed.input, projection: projectionWithMap, map_points }), true);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: projectionWithMap }), /PROJECTION_DIGEST_MISMATCH/);
});

test("projection creation is draft-only and a complete seal requires the full selected, rights-checked fact set", () => {
  assert.equal(validateProjectionDraft(projection), true);
  assert.throws(() => validateProjectionDraft({ ...projection, status: "approved" }), /PROJECTION_DRAFT_REQUIRED|DRAFT/);
  assert.throws(() => validateProjectionDraft({ ...projection, status: "published" }), /PROJECTION_DRAFT_REQUIRED|DRAFT/);

  const sealed = sealedProjectionInput();
  const result = validateProjectionComplete({ ...sealed.input, projection: sealed.projection });
  assert.ok(result, "complete projection with a matching seal must be accepted");
  assert.equal(assertProjectionDigest({ ...sealed.input, projection: sealed.projection }, sealed.digest), true);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: sealed.projection, memberships: [] }), /PROJECTION_INCOMPLETE|MEMBERSHIP|COMPLETE/);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: sealed.projection, facts: [] }), /PROJECTION_INCOMPLETE|FACT|COMPLETE/);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: (({ seal_receipt: _ignored, ...withoutSeal }) => withoutSeal)(sealed.projection) }), /PROJECTION_SEAL_REQUIRED|SEAL|INCOMPLETE|PROJECTION/);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: { ...sealed.projection, status: "draft" } }), /PROJECTION_STATUS_APPROVED_REQUIRED/);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: { ...sealed.projection, seal_receipt: { ...sealed.projection.seal_receipt, canonical_projection_digest: "sha256:" + "0".repeat(64) } } }), /PROJECTION_SEAL_REQUIRED/);
  const tamperedDigest = "sha256:" + "0".repeat(64);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: {
    ...sealed.projection,
    projection_digest: tamperedDigest,
    seal_receipt: { ...sealed.projection.seal_receipt, canonical_projection_digest: tamperedDigest },
  } }), /PROJECTION_DIGEST_MISMATCH|DIGEST/);

  const extraAssertion = {
    ...assertion,
    id: "10000000-0000-4000-8000-000000000098",
    property_id: "10000000-0000-4000-8000-000000000099",
    field_key: "internal_note",
    value: "broker-only secret",
    data_classification: "internal",
  };
  const unboundInternalFact = {
    ...fact,
    property_id: extraAssertion.property_id,
    field_assertion_id: extraAssertion.id,
    display_field_key: extraAssertion.field_key,
  };
  assert.throws(() => validateProjectionComplete({
    ...sealed.input,
    projection: sealed.projection,
    facts: [...sealed.input.facts, unboundInternalFact],
    assertions: [...sealed.input.assertions, extraAssertion],
  }), /PROJECTION_INCOMPLETE/);
  assert.throws(() => validateProjectionComplete({
    ...sealed.input,
    projection: sealed.projection,
    facts: [...sealed.input.facts, { ...fact, id: "duplicate-fact" }],
  }), /PROJECTION_INCOMPLETE/);

  let coercions = 0;
  const coerciblePropertyId = {
    toString() {
      coercions++;
      return membership.property_id;
    },
  };
  assert.throws(() => validateProjectionComplete({
    ...sealed.input,
    projection: sealed.projection,
    memberships: [{ ...membership, property_id: coerciblePropertyId }],
    facts: sealed.input.facts.map(item => ({ ...item, property_id: coerciblePropertyId })),
    assertions: sealed.input.assertions.map(item => ({ ...item, property_id: coerciblePropertyId })),
  }), /PROJECTION_INCOMPLETE/);
  assert.equal(coercions, 0, "complete-projection identity objects must not be coerced");
});

test("internal-only assertions neither enter nor perturb the public projection, but an internal fact is refused", () => {
  const sealed = sealedProjectionInput();
  const internalAssertion = {
    ...assertion,
    id: "internal-assertion",
    field_key: "internal_note",
    value: "broker-only note",
    data_classification: "internal",
  };
  const withUnreferencedInternal = {
    ...sealed.input,
    projection: sealed.projection,
    assertions: [...completeAssertions, internalAssertion], evidence: [evidence], rights: [rights],
  };
  assert.ok(validateProjectionComplete(withUnreferencedInternal), "unreferenced internal material must not interfere with the public read model");
  const internalFact = { ...fact, field_assertion_id: internalAssertion.id, display_field_key: "internal_note" };
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: sealed.projection, facts: [internalFact], assertions: [internalAssertion] }), /PUBLIC_FIELD|PUBLIC_ASSERTION|INTERNAL|PROJECTION/);
});

test("unresolved evidence, confidence, and field conflicts are quarantined", () => {
  const sealed = sealedProjectionInput();
  const conflictId = "10000000-0000-4000-8000-000000000090";
  const resolution = {
    id: "10000000-0000-4000-8000-000000000091",
    organization_tenant_id: fixture.tenant,
    conflict_id: conflictId,
    selected_field_assertion_id: fact.field_assertion_id,
    rationale: "Verified source selected",
    evidence: { source_evidence_id: evidence.id },
    resolver_actor_id: "actor:proof",
    resolved_at: "2026-08-24T12:00:00Z",
    receipt_digest: "sha256:" + "d".repeat(64),
    created_at: "2026-08-24T12:00:01Z",
  };
  const participant = {
    organization_tenant_id: fixture.tenant,
    conflict_id: conflictId,
    field_assertion_id: fact.field_assertion_id,
    participant_role: "candidate",
  };
  assert.throws(() => validateProjectionComplete({
    ...sealed.input,
    projection: sealed.projection,
    evidence: [{ ...evidence, retrieval_status: "partial" }],
  }), /EVIDENCE_UNRESOLVED/);
  assert.throws(() => validateProjectionComplete({
    ...sealed.input,
    projection: sealed.projection,
    assertions: completeAssertions.map(item => ({ ...item, confidence: "unknown" })),
  }), /PUBLIC_ASSERTION_UNRESOLVED/);
  assert.throws(() => validateProjectionComplete({
    ...sealed.input,
    projection: sealed.projection,
    conflicts: [{
      id: conflictId,
      organization_tenant_id: fixture.tenant,
      property_id: membership.property_id,
      field_key: fact.display_field_key,
      state: "open",
      opened_at: "2026-08-24T00:00:00Z",
    }],
  }), /PUBLIC_ASSERTION_CONFLICTED/);
  assert.equal(validateProjectionComplete({
    ...sealed.input,
    projection: sealed.projection,
    conflicts: [{
      id: conflictId, organization_tenant_id: fixture.tenant,
      property_id: membership.property_id, field_key: fact.display_field_key,
      state: "open", opened_at: "2026-08-24T00:00:00Z",
    }],
    conflict_resolutions: [resolution],
    conflict_participants: [participant],
  }), true);
  const baseConflictInput = {
    ...sealed.input,
    projection: sealed.projection,
    conflicts: [{
      id: conflictId, organization_tenant_id: fixture.tenant,
      property_id: membership.property_id, field_key: fact.display_field_key,
      state: "open", opened_at: "2026-08-24T00:00:00Z",
    }],
    conflict_participants: [participant],
  };
  for (const incompleteResolution of [
    (({ receipt_digest: _ignored, ...rest }) => rest)(resolution),
    { ...resolution, rationale: "" },
    { ...resolution, evidence: null },
    { ...resolution, resolver_actor_id: "" },
    { ...resolution, created_at: undefined },
  ]) assert.throws(() => validateProjectionComplete({
    ...baseConflictInput, conflict_resolutions: [incompleteResolution],
  }), /PUBLIC_ASSERTION_CONFLICTED/);
  const changingResolution = { ...resolution };
  delete changingResolution.resolved_at;
  let resolutionReads = 0;
  Object.defineProperty(changingResolution, "resolved_at", {
    enumerable: true,
    get() { return resolutionReads++ === 0 ? "2026-08-26T12:00:00Z" : "2026-08-24T12:00:00Z"; },
  });
  assert.throws(() => validateProjectionComplete({
    ...baseConflictInput, conflict_resolutions: [changingResolution],
  }), /PUBLIC_ASSERTION_CONFLICTED|PROJECTION_INCOMPLETE/);
  assert.equal(resolutionReads, 0, "resolution accessors must never execute");
  assert.throws(() => validateProjectionComplete({
    ...baseConflictInput,
    conflict_resolutions: [resolution],
    conflict_participants: [{ ...participant, field_assertion_id: addressAssertion.id }],
  }), /PUBLIC_ASSERTION_CONFLICTED/);
  const shadowedResolutions = [];
  Object.defineProperty(shadowedResolutions, "some", { value: () => true });
  assert.throws(() => validateProjectionComplete({
    ...baseConflictInput,
    conflict_resolutions: shadowedResolutions,
  }), /PROJECTION_INCOMPLETE|PUBLIC_ASSERTION_CONFLICTED/);
  const shadowedConflicts = [];
  Object.defineProperty(shadowedConflicts, "some", { value: () => false });
  assert.throws(() => validateProjectionComplete({
    ...sealed.input, projection: sealed.projection, conflicts: shadowedConflicts,
  }), /PROJECTION_INCOMPLETE/);
  const shadowedParticipants = [];
  Object.defineProperty(shadowedParticipants, "some", { value: () => true });
  assert.throws(() => validateProjectionComplete({
    ...baseConflictInput,
    conflict_resolutions: [resolution],
    conflict_participants: shadowedParticipants,
  }), /PROJECTION_INCOMPLETE|PUBLIC_ASSERTION_CONFLICTED/);
});
