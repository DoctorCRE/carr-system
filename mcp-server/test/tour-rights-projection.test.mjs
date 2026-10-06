import assert from "node:assert/strict";
import test from "node:test";
import { tourRightsProjectionTools } from "../src/tour-rights-projection.js";

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = { id: "actor-00000000-0000-4000-8000-000000000001", slug: "codex" };
const ids = {
  rights: "10000000-0000-4000-8000-000000000001",
  revokedRights: "10000000-0000-4000-8000-000000000002",
  evidence: "20000000-0000-4000-8000-000000000001",
  assertion: "30000000-0000-4000-8000-000000000001",
  addressAssertion: "30000000-0000-4000-8000-000000000002",
  property: "40000000-0000-4000-8000-000000000001",
  tour: "50000000-0000-4000-8000-000000000001",
  projection: "60000000-0000-4000-8000-000000000001",
};
const digest = value => `sha256:${value.repeat(64)}`;
const idempotency = "70000000-0000-4000-8000-000000000001";
const publicFact = {
  property_id: ids.property,
  field_assertion_id: ids.assertion,
  display_field_key: "display.name",
  value: "Medical Plaza",
  source_evidence_id: ids.evidence,
  rights_receipt_id: ids.rights,
  observed_at: "2026-08-27T12:05:00Z",
  effective_from: "2026-08-27T00:00:00Z",
  effective_to: null,
};
const publicAddressFact = {
  ...publicFact,
  field_assertion_id: ids.addressAssertion,
  display_field_key: "display.address",
  value: "100 Clinic Way",
};
const databaseProjection = {
  projection_id: ids.projection,
  tour_id: ids.tour,
  projection_version: 1,
  route_version: 1,
  as_of: "2026-08-27T12:15:00Z",
  projection_digest: digest("f"),
  facts: [publicFact, publicAddressFact],
};

function harness({ projection = databaseProjection } = {}) {
  const calls = [];
  const envelopes = [];
  const events = [];
  const client = {
    async query(sql, params) {
      calls.push({ sql, params });
      if (sql.includes("append_tour_rights_receipt")) return { rows: [{ rights_receipt_id: ids.rights }] };
      if (sql.includes("revoke_tour_rights_receipt")) return { rows: [{ rights_receipt_id: ids.revokedRights }] };
      if (sql.includes("append_tour_source_evidence")) return { rows: [{ source_evidence_id: ids.evidence }] };
      if (sql.includes("append_tour_field_assertion")) return { rows: [{ field_assertion_id: ids.assertion }] };
      if (sql.includes("create_tour_public_projection_draft")) return { rows: [{ projection_id: ids.projection }] };
      if (sql.includes("seal_tour_public_projection")) return { rows: [{ projection_digest: digest("f") }] };
      if (sql.includes("read_tour_public_projection")) return { rows: [{ projection }] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
  const withEnvelope = async (c, a, verb, args, fn) => {
    assert.equal(c, client);
    assert.equal(a, actor);
    assert.equal(args.idempotency_key, idempotency);
    envelopes.push({ verb, args });
    return fn();
  };
  const writeEvent = async (...args) => { events.push(args); };
  return { client, calls, envelopes, events, tools: tourRightsProjectionTools({ withEnvelope, writeEvent, ToolError }) };
}

const rightsArgs = {
  idempotency_key: idempotency,
  provider: "county-records",
  sku: "public-records",
  policy_key: "escambia-property",
  receipt_version: 1,
  receipt_digest: digest("a"),
  terms_url: "https://example.invalid/terms",
  reviewed_at: "2026-08-27T12:00:00Z",
  intended_use: "tour source and public fact projection",
  allowed_field_classes: ["display.name", "display.address"],
  allowed_use_classes: ["source_intake", "canonical_fact", "client_public_display"],
  effective_at: "2026-08-27T00:00:00Z",
  expires_at: null,
  supersedes_receipt_id: null,
};

test("write tools derive tenant and actor/reviewer identity and call only bounded database seams", async () => {
  const h = harness();
  assert.deepEqual(Object.keys(h.tools).sort(), [
    "append-tour-field-assertion",
    "append-tour-rights-receipt",
    "append-tour-source-evidence",
    "create-tour-public-projection-draft",
    "read-tour-public-projection",
    "revoke-tour-rights-receipt",
    "seal-tour-public-projection",
  ]);
  assert.equal(h.tools["append-tour-rights-receipt"].authorityOnly, true);
  assert.equal(h.tools["revoke-tour-rights-receipt"].authorityOnly, true);
  assert.equal(h.tools["seal-tour-public-projection"].authorityOnly, true);
  assert.equal(h.tools["append-tour-source-evidence"].authorityOnly, undefined);
  assert.equal(h.tools["append-tour-field-assertion"].authorityOnly, true);
  assert.equal(h.tools["create-tour-public-projection-draft"].authorityOnly, undefined);
  assert.equal(h.tools["read-tour-public-projection"].writerConnection, true);

  assert.deepEqual(await h.tools["append-tour-rights-receipt"].handler(h.client, actor, rightsArgs), { ok: true, rights_receipt_id: ids.rights });
  const rightsPayload = JSON.parse(h.calls.at(-1).params[0]);
  assert.equal(rightsPayload.organization_tenant_id, "carr-internal");
  assert.equal(rightsPayload.reviewer, actor.id);
  assert.equal(rightsPayload.status, "active");
  assert.equal(rightsPayload.revoked_at, null);

  assert.deepEqual(await h.tools["revoke-tour-rights-receipt"].handler(h.client, actor, {
    idempotency_key: idempotency, rights_receipt_id: ids.rights,
    revoked_at: "2026-08-27T13:00:00Z", receipt_digest: digest("b"),
  }), { ok: true, rights_receipt_id: ids.revokedRights, supersedes_receipt_id: ids.rights, status: "revoked" });
  assert.deepEqual(h.calls.at(-1).params, ["carr-internal", ids.rights, "2026-08-27T13:00:00Z", actor.id, digest("b")]);

  assert.deepEqual(await h.tools["append-tour-source-evidence"].handler(h.client, actor, {
    idempotency_key: idempotency, stable_locator: "county:escambia:parcel:1",
    evidence_class: "direct_source", retrieved_at: "2026-08-27T12:10:00Z", retrieval_status: "read",
    content_digest: digest("c"), rights_receipt_id: ids.rights, rights_provider: "county-records",
    rights_policy_key: "escambia-property", data_classification: "public",
  }), { ok: true, source_evidence_id: ids.evidence });
  assert.equal(JSON.parse(h.calls.at(-1).params[0]).organization_tenant_id, "carr-internal");

  assert.deepEqual(await h.tools["append-tour-field-assertion"].handler(h.client, actor, {
    idempotency_key: idempotency, property_id: ids.property, field_key: "display.name", value: "Medical Plaza",
    source_evidence_id: ids.evidence, rights_receipt_id: ids.rights, observed_at: "2026-08-27T12:05:00Z",
    effective_from: "2026-08-27T00:00:00Z", effective_to: null, confidence: "high",
    data_classification: "public", review_state: "reviewed",
  }), { ok: true, field_assertion_id: ids.assertion });
  assert.equal(JSON.parse(h.calls.at(-1).params[0]).organization_tenant_id, "carr-internal");

  assert.deepEqual(await h.tools["create-tour-public-projection-draft"].handler(h.client, actor, {
    idempotency_key: idempotency, tour_id: ids.tour, projection_version: 1, route_version: 1,
    as_of: "2026-08-27T12:15:00Z",
  }), { ok: true, projection_id: ids.projection, status: "draft" });
  assert.deepEqual(h.calls.at(-1).params, ["carr-internal", ids.tour, 1, 1, "2026-08-27T12:15:00Z"]);

  const selectedFacts = [{ property_id: ids.property, field_assertion_id: ids.assertion, display_field_key: "display.name" }];
  assert.deepEqual(await h.tools["seal-tour-public-projection"].handler(h.client, actor, {
    idempotency_key: idempotency, projection_id: ids.projection, selected_facts: selectedFacts,
    receipt_digest: digest("d"),
  }), { ok: true, projection_id: ids.projection, projection_digest: digest("f"), status: "approved" });
  assert.deepEqual(h.calls.at(-1).params, ["carr-internal", ids.projection, JSON.stringify(selectedFacts), actor.id, digest("d")]);

  assert.equal(h.envelopes.length, 6);
  assert.equal(h.events.length, 6);
});

test("read is tenant-scoped, approved-only, and never enters a write envelope", async () => {
  const h = harness();
  const result = await h.tools["read-tour-public-projection"].handler(h.client, actor, { projection_id: ids.projection });
  assert.equal(result.ok, true);
  assert.equal(result.projection.projection_id, ids.projection);
  assert.deepEqual(h.calls[0].params, ["carr-internal", ids.projection]);
  assert.equal(h.envelopes.length, 0);
  assert.equal(h.events.length, 0);
});

test("public read canonicalizes PostgreSQL offset timestamps", async () => {
  const databaseShaped = {
    ...databaseProjection,
    as_of: "2026-08-27T12:15:00+00:00",
    facts: [publicFact, publicAddressFact].map(item => ({
      ...item,
      observed_at: "2026-08-27T07:05:00-05:00",
      effective_from: "2026-08-26T19:00:00-05:00",
      effective_to: null,
    })),
  };
  const h = harness({ projection: databaseShaped });
  const result = await h.tools["read-tour-public-projection"].handler(
    h.client, actor, { projection_id: ids.projection });
  assert.equal(result.projection.as_of, "2026-08-27T12:15:00Z");
  assert.equal(result.projection.facts[0].observed_at, "2026-08-27T12:05:00Z");
  assert.equal(result.projection.facts[0].effective_from, "2026-08-27T00:00:00Z");
});

test("public read projection ignores internal-only metadata and rejects forbidden fact classes", async () => {
  const internalOnly = {
    ...databaseProjection,
    broker_contact: { email: "internal@example.invalid" },
    analysis: "broker conclusion",
    credentials: "secret",
    notes: "internal note",
    restrictions: "provider terms",
    recommendation: "choose this property",
    ranking: 1,
    facts: [
      { ...publicFact, internal_note: "do not expose", broker_rank: 1 },
      { ...publicAddressFact, internal_note: "do not expose" },
    ],
  };
  const cleanHarness = harness();
  const clean = await cleanHarness.tools["read-tour-public-projection"].handler(
    cleanHarness.client, actor, { projection_id: ids.projection });
  const h = harness({ projection: internalOnly });
  const projected = await h.tools["read-tour-public-projection"].handler(
    h.client, actor, { projection_id: ids.projection });
  assert.deepEqual(projected.projection, clean.projection);
  assert.deepEqual(Object.keys(projected.projection).sort(), [
    "as_of", "facts", "projection_digest", "projection_id", "projection_version", "route_version", "tour_id",
  ]);
  assert.deepEqual(Object.keys(projected.projection.facts[0]).sort(), [
    "display_field_key", "effective_from", "effective_to", "field_assertion_id", "observed_at",
    "property_id", "rights_receipt_id", "source_evidence_id", "value",
  ]);

  for (const display_field_key of [
    "broker_contact", "analysis", "credentials", "notes", "restrictions", "recommendation", "ranking",
  ]) {
    const rejected = harness({ projection: {
      ...databaseProjection,
      facts: [{ ...publicFact, display_field_key }, publicAddressFact],
    } });
    await assert.rejects(
      rejected.tools["read-tour-public-projection"].handler(
        rejected.client, actor, { projection_id: ids.projection }),
      error => error instanceof ToolError && error.payload.error === "tour_public_projection_invalid",
    );
  }
});

test("public read snapshots plain data once and rejects hostile arrays or accessors", async () => {
  const poisonedFacts = [publicFact, publicAddressFact];
  Object.defineProperty(poisonedFacts, "map", {
    value: () => [{ ...publicFact, value: { notes: "broker-only", credentials: "secret" } }],
  });
  const poisoned = harness({ projection: { ...databaseProjection, facts: poisonedFacts } });
  await assert.rejects(
    poisoned.tools["read-tour-public-projection"].handler(
      poisoned.client, actor, { projection_id: ids.projection }),
    error => error instanceof ToolError && error.payload.error === "tour_public_projection_invalid",
  );

  const changingFact = { ...publicFact };
  delete changingFact.value;
  let reads = 0;
  Object.defineProperty(changingFact, "value", {
    enumerable: true,
    get() { return reads++ === 0 ? "Medical Plaza" : { notes: "broker-only", credentials: "secret" }; },
  });
  const changing = harness({ projection: {
    ...databaseProjection, facts: [changingFact, publicAddressFact],
  } });
  await assert.rejects(
    changing.tools["read-tour-public-projection"].handler(
      changing.client, actor, { projection_id: ids.projection }),
    error => error instanceof ToolError && error.payload.error === "tour_public_projection_invalid",
  );
  assert.equal(reads, 0, "rejected accessors must never execute");
});

test("public read projection refuses incomplete, duplicate, or temporally impossible facts", async () => {
  for (const facts of [
    [publicFact],
    [publicFact, publicAddressFact, { ...publicAddressFact, field_assertion_id: ids.assertion }],
    [{ ...publicFact, observed_at: "2026-08-28T00:00:00Z" }, publicAddressFact],
    [{ ...publicFact, effective_from: "2026-08-28T00:00:00Z" }, publicAddressFact],
    [{ ...publicFact, effective_to: "2026-08-27T12:00:00Z" }, publicAddressFact],
  ]) {
    const h = harness({ projection: { ...databaseProjection, facts } });
    await assert.rejects(
      h.tools["read-tour-public-projection"].handler(
        h.client, actor, { projection_id: ids.projection }),
      error => error instanceof ToolError && error.payload.error === "tour_public_projection_invalid",
    );
  }
});

test("authority selectors and actor/reviewer impersonation are refused before database access", async () => {
  for (const injected of [
    { organization_tenant_id: "other" }, { tenant: "other" }, { actor_id: "other" },
    { actor: "other" }, { reviewer: "other" },
  ]) {
    const h = harness();
    await assert.rejects(
      h.tools["append-tour-rights-receipt"].handler(h.client, actor, { ...rightsArgs, ...injected }),
      error => error instanceof ToolError && error.payload.error === "caller_authority_field_forbidden",
    );
    assert.equal(h.calls.length, 0);
    assert.equal(h.envelopes.length, 0);
  }
});

test("malformed identifiers, digests, timestamps, and selected fact sets fail closed", async () => {
  const h = harness();
  await assert.rejects(h.tools["revoke-tour-rights-receipt"].handler(h.client, actor, {
    idempotency_key: idempotency, rights_receipt_id: "not-a-uuid", revoked_at: "bad", receipt_digest: "bad",
  }), error => error instanceof ToolError && error.payload.error === "tour_input_invalid");
  await assert.rejects(h.tools["seal-tour-public-projection"].handler(h.client, actor, {
    idempotency_key: idempotency, projection_id: ids.projection,
    selected_facts: [{ property_id: ids.property, field_assertion_id: ids.assertion, display_field_key: "display.name", value: "leak" }],
    receipt_digest: digest("d"),
  }), error => error instanceof ToolError && error.payload.error === "tour_selected_facts_invalid");
  assert.equal(h.calls.length, 0);
});

test("PostgreSQL-unrepresentable rights text fails before envelope or database access", async () => {
  const invalidCases = [
    { provider: "bad\u0000provider" },
    { provider: "bad\ud800provider" },
    { policy_key: "bad\udc00policy" },
    { intended_use: "bad\ud800use" },
    { allowed_field_classes: ["display.name\u0000internal"] },
    { allowed_use_classes: ["client_public_display\ud800"] },
  ];
  for (const invalid of invalidCases) {
    const h = harness();
    await assert.rejects(
      h.tools["append-tour-rights-receipt"].handler(
        h.client, actor, { ...rightsArgs, ...invalid }),
      error => error instanceof ToolError && error.payload.error === "tour_input_invalid",
    );
    assert.equal(h.calls.length, 0);
    assert.equal(h.envelopes.length, 0);
  }
});

test("field assertions bind one detached PostgreSQL-safe JSON snapshot to the envelope and database write", async () => {
  const h = harness();
  const value = { name: "Medical Plaza", details: [null, true, 4200] };
  const args = {
    idempotency_key: idempotency, property_id: ids.property, field_key: "display.name", value,
    source_evidence_id: ids.evidence, rights_receipt_id: ids.rights,
    observed_at: "2026-08-27T12:05:00Z", effective_from: "2026-08-27T00:00:00Z",
    effective_to: null, confidence: "high", data_classification: "public", review_state: "reviewed",
  };
  await h.tools["append-tour-field-assertion"].handler(h.client, actor, args);
  value.name = "changed after validation";
  value.details[2] = 0;
  assert.deepEqual(JSON.parse(JSON.stringify(h.envelopes[0].args.value)),
    { name: "Medical Plaza", details: [null, true, 4200] });
  assert.notEqual(h.envelopes[0].args.value, value);
  assert.deepEqual(JSON.parse(h.calls[0].params[0]).value,
    { name: "Medical Plaza", details: [null, true, 4200] });
});

test("field assertions reject hostile or PostgreSQL-unrepresentable JSON before envelope or database access", async () => {
  const changing = {};
  let reads = 0;
  Object.defineProperty(changing, "name", {
    enumerable: true,
    get() { return reads++ === 0 ? "safe" : "changed"; },
  });
  const sparse = new Array(2);
  sparse[0] = "only";
  const withToJson = { name: "safe", toJSON() { return { name: "changed" }; } };
  for (const value of [
    null, "bad\u0000value", "bad\ud800value", { nested: "bad\udc00value" },
    sparse, { amount: Number.NaN }, { amount: Number.POSITIVE_INFINITY },
    withToJson, changing, JSON.parse('{"__proto__":{"a":1}}'),
  ]) {
    const h = harness();
    await assert.rejects(
      h.tools["append-tour-field-assertion"].handler(h.client, actor, {
        idempotency_key: idempotency, property_id: ids.property, field_key: "display.name", value,
        source_evidence_id: ids.evidence, rights_receipt_id: ids.rights,
        observed_at: "2026-08-27T12:05:00Z", effective_from: "2026-08-27T00:00:00Z",
        effective_to: null, confidence: "high", data_classification: "public", review_state: "reviewed",
      }),
      error => error instanceof ToolError && error.payload.error === "tour_input_invalid" &&
        error.payload.field === "value",
    );
    assert.equal(h.calls.length, 0);
    assert.equal(h.envelopes.length, 0);
  }
  assert.equal(reads, 0, "rejected assertion accessors must never execute");
});
