// V5-RW02 persistence seam — tests written before the store implementation.
// Synthetic observations only; no Salesforce, network, credential, or real row.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { digest } from "../src/artifact-trust.js";
import {
  V5_RW02_STORE_OPERATIONS,
  V5RW02StoreError,
  createSalesforceReconciliationStore,
} from "../src/salesforce-reconciliation-store-rw02.v5.js";

const T = ORGANIZATION_TENANT_ID;
const ACTOR = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const ctx = { actor: ACTOR };
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;

const page = (over = {}) => ({
  execution_mode: "attended",
  binding: {
    expected_origin: "https://synthetic-org.invalid",
    expected_org_id: "00Dsynthetic0001",
    expected_account_ref: "sf-seat-synthetic-partner",
    expected_ui_contract_digest: D(1),
  },
  observation: {
    origin: "https://synthetic-org.invalid",
    org_id: "00Dsynthetic0001",
    signed_in_account_ref: "sf-seat-synthetic-partner",
    ui_contract_digest: D(1),
    challenge: "none",
    result_consistency: "consistent",
    ...over,
  },
});

const kase = Object.freeze({ workflow_ref: "rw02-case-synthetic-1", deal_ref: "deal-synthetic-1" });

class FakeDb {
  constructor({ evidence = [], principal = "joe" } = {}) {
    this.evidence = evidence;
    this.principal = principal;
    this.calls = [];
    this.rows = [];
  }
  async query(text, params = []) {
    this.calls.push({ text, params });
    if (["BEGIN", "COMMIT", "ROLLBACK"].includes(text)) return { rows: [] };
    if (text.includes("ops.f01_principal()")) return { rows: [{
      principal: { actor_slug: this.principal, human: true, authorization_class: "verified_partner" },
      server_now: "2026-09-26T05:00:00.000Z",
    }] };
    if (text.includes("ops.rw02_replay(")) return { rows: [{ outcome: null }] };
    if (text.includes("ops.rw02_action_evidence(")) return { rows: this.evidence.map(record => ({ record })) };
    if (text.includes("ops.rw02_record(")) {
      const outcome = JSON.parse(params[5]);
      const row = { operation: params[0], actor_slug: "joe", request_digest: params[2],
        action_kind: params[3], step_key: params[4], outcome,
        record_digest: D(this.rows.length + 10), recorded_at: "2026-09-26T05:00:00.000Z" };
      this.rows.push(row);
      // The shape ops.rw02_record returns: the stored outcome plus its digest
      // and binding columns.
      return { rows: [{ outcome: { ...outcome, operation: params[0], actor_slug: "joe",
        recorded_at: row.recorded_at, record_digest: row.record_digest,
        action_kind: row.action_kind, step_key: row.step_key } }] };
    }
    throw new Error(`unexpected SQL: ${text}`);
  }
}

const readbackEvidence = (action_kind = "opportunity_create", step_key = "rw02-step-synthetic-1") => ({
  schema_version: "doctorcre-v5-rw02-action-evidence.v1",
  tenant: T,
  action_kind,
  step_key,
  preview_digest: D(2),
  envelope_digest: D(3),
  evidence_class: "fixture",
  observed_at: "2026-09-26T04:59:00Z",
  outcome: "exact_match",
  readback_digest: D(4),
  evidence_digest: D(5),
});

test("declares exactly the four durable runtime operations", () => {
  assert.deepEqual([...V5_RW02_STORE_OPERATIONS], [
    "record-salesforce-page-stop",
    "record-salesforce-duplicate-check",
    "record-salesforce-write-readback",
    "read-salesforce-action-evidence",
  ]);
});

test("CD1: only a fail-closed page stop can be durably recorded", async () => {
  const db = new FakeDb();
  const store = createSalesforceReconciliationStore({ db });
  await assert.rejects(
    store.recordPageStop({ idempotency_key: "rw02-page-clean", page: page() }, ctx),
    e => e instanceof V5RW02StoreError && e.code === "page_not_stopped",
  );
  assert.equal(db.rows.length, 0);

  const stopped = await store.recordPageStop({ idempotency_key: "rw02-page-challenge",
    page: page({ challenge: "mfa_challenge" }) }, ctx);
  assert.equal(stopped.decision, "recorded");
  assert.equal(stopped.evaluation.decision, "stop");
  assert.equal(stopped.evaluation.reason_id, "authentication_challenge");
  assert.equal(stopped.effects.provider_actions, 0);
  assert.equal(stopped.effects.database_writes, "rw02_record_layer_rows_only");
});

test("CD2: duplicate decisions are re-derived and idempotency binds the exact observation", async () => {
  const db = new FakeDb();
  const store = createSalesforceReconciliationStore({ db });
  const base = { idempotency_key: "rw02-duplicate-1", tenant: T, case: kase, intent_ordinal: 1,
    page: page(), search: { completeness: "complete", candidates: [] } };
  const first = await store.recordDuplicateCheck(base, ctx);
  assert.equal(first.evaluation.decision, "create_admissible");
  const firstDigest = db.rows[0].request_digest;
  await store.recordDuplicateCheck({ ...base, idempotency_key: "rw02-duplicate-2",
    search: { completeness: "truncated", candidates: [] } }, ctx);
  assert.notEqual(db.rows[1].request_digest, firstDigest,
    "different provider observations must not collapse onto one replay identity");
  assert.equal(db.rows[1].outcome.evaluation.decision, "stop");
});

test("CD2/CD3: write readback persists only the evaluator's sealed per-action evidence", async () => {
  const db = new FakeDb();
  const evidence = readbackEvidence();
  const store = createSalesforceReconciliationStore({
    db,
    evaluators: { evaluateWriteReadback: () => ({ decision: "confirmed", reason_id: "exact_readback",
      action_kind: evidence.action_kind, step_key: evidence.step_key, evidence,
      outward_effect_granted: false, autonomy_active: false }) },
  });
  const answer = await store.recordWriteReadback({ idempotency_key: "rw02-readback-1",
    observation: { synthetic: "closed evaluator input" } }, ctx);
  assert.equal(answer.evaluation.decision, "confirmed");
  assert.deepEqual(db.rows[0].outcome.evaluation.evidence, evidence);
  assert.equal(db.rows[0].action_kind, evidence.action_kind);
  assert.equal(db.rows[0].step_key, evidence.step_key);
});

test("CD3: readback evidence sealed for another action or step is refused before SQL", async () => {
  for (const [label, evidence] of [
    ["action", readbackEvidence("opportunity_phase_update", "rw02-step-synthetic-1")],
    ["step", readbackEvidence("opportunity_create", "rw02-step-other")],
  ]) {
    const db = new FakeDb();
    const store = createSalesforceReconciliationStore({
      db,
      evaluators: { evaluateWriteReadback: () => ({ decision: "confirmed", reason_id: "exact_readback",
        action_kind: "opportunity_create", step_key: "rw02-step-synthetic-1", evidence,
        outward_effect_granted: false, autonomy_active: false }) },
    });
    await assert.rejects(
      store.recordWriteReadback({ idempotency_key: `rw02-readback-foreign-${label}`,
        observation: { synthetic: "closed evaluator input" } }, ctx),
      e => e instanceof V5RW02StoreError && e.code === "evidence_binding_mismatch",
      label,
    );
    assert.equal(db.rows.length, 0, label);
  }
});

test("CD3: evidence read is per action, rejects a foreign row before trust evaluation, and never activates", async () => {
  const own = readbackEvidence("opportunity_create", "rw02-step-a");
  const db = new FakeDb({ evidence: [own, readbackEvidence("opportunity_phase_update", "rw02-step-b")] });
  let called = false;
  const store = createSalesforceReconciliationStore({ db, evaluators: {
    evaluateActionTrustWindow: () => { called = true; return { decision: "window_read" }; },
  } });
  await assert.rejects(
    store.readActionEvidence({ action_kind: "opportunity_create" }, ctx),
    e => e instanceof V5RW02StoreError && e.code === "foreign_action_evidence",
  );
  assert.equal(called, false);

  const cleanDb = new FakeDb({ evidence: [own] });
  const clean = createSalesforceReconciliationStore({ db: cleanDb, evaluators: {
    evaluateActionTrustWindow: request => ({ decision: "window_read", action_kind: request.action_kind,
      evidence_total: request.evidence.length, evidence_authenticated: true, autonomy_active: true }),
  } });
  const answer = await clean.readActionEvidence({ action_kind: "opportunity_create" }, ctx);
  // Even an evaluator claiming authentication and autonomy is overridden: who
  // authenticates provider evidence is an open human ruling.
  assert.equal(answer.evidence_authenticated, false);
  assert.equal(answer.evidence_origin, "rw02_record_layer_readback");
  assert.equal(answer.evidence_authentication, "open_human_ruling");
  assert.equal(answer.autonomy_active, false);
  assert.equal(answer.activation_review_eligibility, "unavailable");
});

test("caller-derived tenant, actor, decision, digest, and evidence are refused before SQL", async () => {
  const db = new FakeDb();
  const store = createSalesforceReconciliationStore({ db });
  for (const key of ["tenant", "actor_slug", "decision", "record_digest", "evidence"]) {
    await assert.rejects(
      store.recordPageStop({ idempotency_key: `rw02-inject-${key}`, page: page({ challenge: "mfa" }),
        [key]: key === "evidence" ? [] : "caller-value" }, ctx),
      e => e instanceof V5RW02StoreError && e.code === "caller_derived_field_refused",
      key,
    );
  }
  assert.equal(db.rows.length, 0);
});

test("the handler actor must be the database principal, or nothing is recorded", async () => {
  const db = new FakeDb({ principal: "dell" });
  const store = createSalesforceReconciliationStore({ db });
  await assert.rejects(
    store.recordPageStop({ idempotency_key: "rw02-actor-mismatch", page: page({ challenge: "mfa_challenge" }) }, ctx),
    e => e instanceof V5RW02StoreError && e.code === "actor_context_mismatch",
  );
  assert.equal(db.rows.length, 0);
  assert.equal(db.calls.some(call => call.text.includes("ops.rw02_record(")), false);
  await assert.rejects(
    store.readActionEvidence({ action_kind: "opportunity_create" }, ctx),
    e => e instanceof V5RW02StoreError && e.code === "actor_context_mismatch",
  );
});

test("the evidence read admits the reader bundle's database identity; a write never does", async () => {
  // Live 2026-09-27: a read verb runs on the reader login, where the database
  // principal is always 'carr-reader', never the handler's actor.
  const db = new FakeDb({ principal: "carr-reader" });
  const store = createSalesforceReconciliationStore({ db });
  const out = await store.readActionEvidence({ action_kind: "opportunity_create" }, ctx);
  assert.equal(out.evidence_authenticated, false);
  await assert.rejects(
    store.recordPageStop({ idempotency_key: "rw02-reader-write", page: page({ challenge: "mfa_challenge" }) }, ctx),
    e => e instanceof V5RW02StoreError && e.code === "actor_context_mismatch",
  );
  assert.equal(db.rows.length, 0, "the reader identity never records anything");
});

test("the evidence read asks the REAL window evaluator for per-action scope only", async () => {
  const fields = { schema_version: "doctorcre-v5-rw02-action-evidence.v1", tenant: T,
    action_kind: "opportunity_create", step_key: "rw02-step-synthetic-1", preview_digest: D(2),
    envelope_digest: D(3), evidence_class: "fixture", observed_at: "2026-09-26T04:59:00Z",
    outcome: "exact_match", readback_digest: D(4) };
  const sealed = { ...fields, evidence_digest: digest({ kind: "rw02-evidence.v1", ...fields }) };
  let seenScope;
  const spyDb = new FakeDb({ evidence: [sealed] });
  await createSalesforceReconciliationStore({ db: spyDb, evaluators: {
    evaluateActionTrustWindow: request => { seenScope = request.trust_scope;
      return { decision: "window_read" }; },
  } }).readActionEvidence({ action_kind: "opportunity_create" }, ctx);
  assert.equal(seenScope, "per_action");

  const answer = await createSalesforceReconciliationStore({ db: new FakeDb({ evidence: [sealed] }) })
    .readActionEvidence({ action_kind: "opportunity_create" }, ctx);
  assert.equal(answer.decision, "window_read");
  assert.equal(answer.reason_id, "per_action_window_read");
  assert.equal(answer.trust_scope, "per_action");
  assert.deepEqual(answer.inherits_from, []);
  assert.equal(answer.evidence_total, 1);
});
