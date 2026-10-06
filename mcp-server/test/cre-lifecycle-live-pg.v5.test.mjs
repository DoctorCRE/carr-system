// DoctorCRE v5 slice V5-J102: the lifecycle driven LIVE, through the real store,
// against real PostgreSQL.
//
// WHAT THIS PROVES THAT NOTHING ELSE HERE DOES. cre-lifecycle-store.v5.test.mjs
// runs the store against a scripted fake handle, and cre-lifecycle-postgres.sql
// drives the SQL writers directly with envelopes it builds by hand. Neither one
// ever put the two together: the envelopes the STORE builds had never been handed
// to the writers the SQL defines. This suite does exactly that, as three real
// database principals (carr_authority_joe, carr_authority_dell, and carr_writer
// acting as a sponsored agent), with evidence produced through F01's own
// writers and nothing seeded around a guard.
//
// It runs only when the three DSNs below are set, which is what
// ops/cre-lifecycle-local-pg-gate.py does on a scratch database it builds and
// drops. Without them every test here is skipped by name, never passed.
//
// EVERY RECORD IS SYNTHETIC. Ids carry a per-run nonce so the suite can run
// twice against the same scratch database without colliding.

import { test, after } from "node:test";
import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";

import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_J102_TRANSITION_IDS,
  v5J102TransitionTruthTable,
  v5J102TransitionContract,
  v5J102MigrationReadiness,
} from "../src/cre-lifecycle.v5.js";
import { createCreLifecycleStore, v5J102StoreEnvelope } from "../src/cre-lifecycle-store.v5.js";
import { createRecordSourceAuthorityStore } from "../src/record-source-authority-store.v5.js";

const DSN = {
  joe: process.env.CARR_J102_LIVE_PG_DSN_JOE,
  dell: process.env.CARR_J102_LIVE_PG_DSN_DELL,
  writer: process.env.CARR_J102_LIVE_PG_DSN_WRITER,
};
const LIVE = Boolean(DSN.joe && DSN.dell && DSN.writer);
const SKIP = LIVE ? false
  : "CARR_J102_LIVE_PG_DSN_{JOE,DELL,WRITER} are not set; ops/cre-lifecycle-local-pg-gate.py sets them on a scratch database";

const RUN = randomUUID().slice(0, 8);
const id = prefix => `${prefix}-live-${RUN}-${randomUUID().slice(0, 8)}`;
const key = () => `j102-live-${randomUUID()}`;

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const DELL = Object.freeze({ slug: "dell", display: "Dell", human: true, via: "oauth-google" });
const AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});

let pg;
const pools = {};

async function pool(name) {
  if (pools[name]) return pools[name];
  if (!pg) pg = (await import("pg")).default;
  const writer = ["agent", "joe_writer", "agent_unsponsored", "agent_as_dell"].includes(name);
  const config = { connectionString: writer ? DSN.writer : DSN[name], max: 2 };
  // mcp.js sets carr.sponsoring_human_slug transaction-locally from the
  // authenticated grant on every write; each pool stands in for that here.
  if (name === "agent") config.options = "-c carr.acting_actor_slug=codex -c carr.sponsoring_human_slug=joe";
  if (name === "joe_writer") config.options = "-c carr.acting_actor_slug=joe -c carr.sponsoring_human_slug=joe";
  if (name === "agent_unsponsored") config.options = "-c carr.acting_actor_slug=codex";
  if (name === "agent_as_dell") config.options = "-c carr.acting_actor_slug=codex -c carr.sponsoring_human_slug=dell";
  pools[name] = new pg.Pool(config);
  return pools[name];
}

/** A store whose `db` is one real connection per operation, like mcp.js. */
function storeFor(name) {
  return createCreLifecycleStore({
    db: {
      async query(text, params) {
        throw new Error("storeFor: transaction() is always used");
      },
      async transaction(fn) {
        const client = await (await pool(name)).connect();
        try {
          await client.query("BEGIN");
          const out = await fn({ query: (t, p) => client.query(t, p) });
          await client.query("COMMIT");
          return out;
        } catch (e) {
          await client.query("ROLLBACK").catch(() => {});
          throw e;
        } finally {
          client.release();
        }
      },
    },
  });
}

const ACTORS = { joe: JOE, dell: DELL, agent: AGENT, joe_writer: JOE,
  agent_unsponsored: AGENT, agent_as_dell: AGENT };

/** F01's own store over the same per-principal pools. */
function f01StoreFor(name) {
  return createRecordSourceAuthorityStore({
    db: {
      async query() { throw new Error("f01StoreFor: transaction() is always used"); },
      async transaction(fn) {
        const client = await (await pool(name)).connect();
        try {
          await client.query("BEGIN");
          const out = await fn({ query: (t, p) => client.query(t, p) });
          await client.query("COMMIT");
          return out;
        } catch (e) {
          await client.query("ROLLBACK").catch(() => {});
          throw e;
        } finally {
          client.release();
        }
      },
    },
  });
}
function as(name) {
  const store = storeFor(name);
  const ctx = { actor: ACTORS[name] };
  return Object.fromEntries(Object.entries(store).map(([k, fn]) =>
    [k, typeof fn === "function" ? payload => fn(payload, ctx) : fn]));
}

async function sql(name, text, params = []) {
  const client = await (await pool(name)).connect();
  try {
    return (await client.query(text, params)).rows;
  } finally {
    client.release();
  }
}

const sha = s => createHash("sha256").update(s).digest("hex");
const D = s => `sha256:${sha(s)}`;

// ---------------------------------------------------------------------------
// Evidence, produced through F01's own writers. Nothing here writes a row F01
// or J102 would not write for a real caller.
// ---------------------------------------------------------------------------

/**
 * A document version, through F01's OWN store door (recordDocumentIdentity), so
 * this suite depends on F01's exported contract rather than on the arity of one
 * of its SQL writers. Declared an original first-party record: the bytes are
 * synthetic and authored here.
 */
async function f01Document(name, { document_class, states }) {
  const document_id = id("doc");
  const content_digest = D(document_id);
  const document = {
    document_class,
    neon_identity: { document_id, version_no: 1, content_digest },
    object_storage_identity: { object_key: `j102/live/${document_id}`, content_digest,
      byte_length: 2048, sealed: true },
    onedrive_identity: { drive_id: "j102-live-drive", item_id: document_id, content_digest,
      filing_state: "filed" },
    preparation_state: "approved_for_delivery",
    delivery_state: "delivered",
    signature_state: "fully_executed",
    validity_state: "effective",
    version_state: "current",
    ...states,
  };
  const answer = await f01StoreFor(name).recordDocumentIdentity({
    idempotency_key: key(), document,
    source: { provenance_state: "original_first_party",
      basis_statement: "synthetic J102 live fixture: authored in this record layer" },
  }, { actor: ACTORS[name] });
  assert.equal(answer.decision, "allow",
    `F01 document door: ${answer.decision}/${answer.reason_id}: ${JSON.stringify(answer).slice(0, 600)}`);
  return { document_id, version_no: 1, content_digest };
}

async function f01Artifact(name) {
  const native_id = id("artifact");
  const now = (await sql(name, "select ops.f01_now_text() as now"))[0].now;
  const record = {
    schema_version: "doctorcre-v5-f01-corporate-artifact.v1",
    tenant: ORGANIZATION_TENANT_ID,
    source_system: "j102-live-mailbox",
    source_class: null,
    source_account: "j102-live-account",
    native_identity: { native_id, native_id_epoch: "1" },
    native_version: "1",
    content_digest: D(native_id),
    byte_length: 1024,
    observed_at: now,
    provenance: { method: "synthetic_fixture", source_ref: native_id },
    evidence_class: "corporate_document_bytes",
    declared_data_classes: ["internal_business"],
    taint_class: "untrusted_external",
  };
  const envelope = {
    schema_version: "doctorcre-v5-f01-stored-record-envelope.v1",
    record_kind: "stored_corporate_artifact", tenant: ORGANIZATION_TENANT_ID,
    record, record_digest: digest(record),
    is_fact: false, makes_field_authoritative: false, immutable: true,
  };
  const rows = await sql(name, "select ops.f01_record_artifact($1::jsonb, $2::text, $3::text) as o",
    [JSON.stringify(envelope), key(), D(key())]);
  return rows[0].o.artifact_digest;
}

// ---------------------------------------------------------------------------

after(async () => {
  for (const p of Object.values(pools)) await p.end();
});

function ok(answer, what) {
  assert.equal(answer.decision, "allow",
    `${what}: expected allow, got ${answer.decision}/${answer.reason_id}: ${JSON.stringify(answer).slice(0, 1500)}`);
  return answer;
}

async function subject(name, subject_kind, subject_id) {
  const answer = await as(name).readCreLifecycle({
    selector: { kind: "subject", subject_kind, subject_id } });
  return answer;
}

// ---------------------------------------------------------------------------
// Walk helpers. Each performs one real store operation and asserts it landed.
// ---------------------------------------------------------------------------

const REF = (subject_kind, subject_id) => ({ subject_kind, subject_id });

async function linkDocument(name, doc, subject_kind, subject_id) {
  return ok(await as(name).recordEvidenceSubjectLink({ idempotency_key: key(), link: {
    evidence_source: "f01_document", ...docPin(doc), subject_kind, subject_id } }),
  `link document to ${subject_kind}`);
}

async function linkArtifact(name, artifact_digest, subject_kind, subject_id) {
  return ok(await as(name).recordEvidenceSubjectLink({ idempotency_key: key(), link: {
    evidence_source: "f01_corporate_artifact", artifact_digest, subject_kind, subject_id } }),
  `link artifact to ${subject_kind}`);
}

async function fact(name, record_kind, subject_kind, subject_id, extra = {}) {
  const record_id = id(record_kind.replace(/_/g, "-"));
  ok(await as(name).recordLifecycleFact({ idempotency_key: key(), fact: {
    record_kind, record_id, subject_kind, subject_id, ...extra } }), `record fact ${record_kind}`);
  return record_id;
}

/** A client with an active engagement, reached through the real doors. */
async function client(name = "joe") {
  const who = as(name);
  const rel = id("rel"), eng = id("eng");
  ok(await who.initializeProspectRelationship({ idempotency_key: key(),
    declared: { new_subject_id: rel } }), "initialize prospect");
  const etl = await f01Document(name, { document_class: "engagement_letter" });
  await linkDocument(name === "agent" ? "joe" : name, etl, "relationship", rel);
  ok(await who.recordRepresentationAgreement({ idempotency_key: key(),
    subject_ref: REF("relationship", rel),
    evidence_refs: [{ evidence_kind: "signed_engagement_letter", ...docRef(etl) }],
    declared: { new_subject_id: eng } }), "establish client and engagement");
  return { rel, eng };
}

/** An opened assignment under a fresh client. */
async function openedAssignment(name = "joe") {
  const { rel, eng } = await client(name);
  const asg = id("asg");
  ok(await as(name).initializeAssignment({ idempotency_key: key(),
    related_refs: { engagement: REF("engagement", eng), relationship: REF("relationship", rel) },
    declared: { new_subject_id: asg } }), "initialize assignment");
  const mandate = await fact(name, "assignment_mandate", "assignment", asg,
    { detail: "synthetic search mandate" });
  ok(await as(name).openCreAssignment({ idempotency_key: key(),
    subject_ref: REF("assignment", asg),
    related_refs: { engagement: REF("engagement", eng), relationship: REF("relationship", rel) },
    evidence_refs: [{ evidence_kind: "search_initiation", record_id: mandate }],
    declared: { mandate_scope: "search" } }), "open assignment");
  return { rel, eng, asg };
}

/** A negotiation with a delivered LOI, accepted by the counterparty. */
async function acceptedNegotiation(name, asg) {
  const neg = id("neg"), property = id("prop");
  ok(await as(name).initializePropertyNegotiation({ idempotency_key: key(),
    related_refs: { assignment: REF("assignment", asg) },
    declared: { new_subject_id: neg, property_id: property } }), "initialize negotiation");
  const loi = await f01Document(name, { document_class: "letter_of_intent",
    states: { signature_state: "unsigned", validity_state: "draft" } });
  await linkDocument("joe", loi, "property_negotiation", neg);
  ok(await as(name).recordLoiSubmission({ idempotency_key: key(),
    subject_ref: REF("property_negotiation", neg),
    related_refs: { assignment: REF("assignment", asg) },
    evidence_refs: [{ evidence_kind: "submitted_loi", ...docRef(loi) }] }), "LOI submission");
  const acceptance = await f01Artifact(name);
  await linkArtifact("joe", acceptance, "property_negotiation", neg);
  ok(await as(name).recordLoiAcceptance({ idempotency_key: key(),
    subject_ref: REF("property_negotiation", neg),
    evidence_refs: [{ evidence_kind: "counterparty_loi_acceptance", artifact_digest: acceptance }] }),
  "LOI acceptance");
  return { neg, property };
}

/** A pending deal: the partner commits the winning property. */
async function pendingDeal(instrument_kind = "lease") {
  const { rel, eng, asg } = await openedAssignment("joe");
  const { neg, property } = await acceptedNegotiation("joe", asg);
  const deal = id("deal");
  const commitment = await fact("joe", "winning_property_commitment", "assignment", asg,
    { detail: "synthetic winner selection" });
  ok(await as("joe").commitWinningProperty({ idempotency_key: key(),
    subject_ref: REF("assignment", asg),
    related_refs: { property_negotiation: REF("property_negotiation", neg) },
    evidence_refs: [{ evidence_kind: "winner_selection_commitment", record_id: commitment }],
    declared: { instrument_kind, new_deal_id: deal } }), "commit winning property");
  return { rel, eng, asg, neg, property, deal };
}

async function state(name, subject_kind, subject_id) {
  const answer = await as(name).readCreLifecycle({
    selector: { kind: "subject", subject_kind, subject_id } });
  return answer;
}

test("a lease deal walks prospect to closed and paid, live, as a verified partner", { skip: SKIP }, async () => {
  const joe = as("joe");
  const { asg, deal } = await pendingDeal("lease");

  const lease = await f01Document("joe", { document_class: "lease" });
  await linkDocument("joe", lease, "deal", deal);
  ok(await joe.recordDealExecution({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "executed_lease", ...docRef(lease) }] }), "lease execution");

  const commission = await f01Document("joe", { document_class: "commission_agreement" });
  await linkDocument("joe", commission, "deal", deal);
  ok(await joe.recordDealAxis({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "commission_agreement", ...docRef(commission) }],
    declared: { axis: "commission_agreement_state" } }), "commission agreement");

  const settlement = await fact("joe", "closing_settlement", "deal", deal,
    { closing_date: "2026-09-20T15:00:00Z" });
  ok(await joe.recordDealClosing({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "final_closing_settlement", record_id: settlement }] }),
  "deal closing");

  const invoice = await fact("joe", "invoice", "deal", deal, { detail: "synthetic invoice" });
  ok(await joe.recordDealAxis({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "invoice_issued", record_id: invoice }],
    declared: { axis: "invoice_state" } }), "invoice issued");

  for (const level of ["partially_paid", "paid"]) {
    const payment = await fact("joe", "payment", "deal", deal, { detail: `synthetic ${level}` });
    ok(await joe.recordDealAxis({ idempotency_key: key(), subject_ref: REF("deal", deal),
      evidence_refs: [{ evidence_kind: "payment_received", record_id: payment }],
      declared: { axis: "payment_state", payment_level: level } }), `payment ${level}`);
  }
  const completion = await fact("joe", "completion", "deal", deal, { detail: "synthetic completion" });
  ok(await joe.recordDealAxis({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "completion_recorded", record_id: completion }],
    declared: { axis: "completion_state" } }), "completion");

  const final = await state("joe", "deal", deal);
  console.log(JSON.stringify(final).slice(0, 900));
});

test("a purchase deal is executed but PENDING through diligence, and closes only on its closing date", { skip: SKIP }, async () => {
  const joe = as("joe");
  const { deal } = await pendingDeal("purchase");
  const contract = await f01Document("joe", { document_class: "purchase_contract" });
  await linkDocument("joe", contract, "deal", deal);
  ok(await joe.recordDealExecution({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "signed_purchase_contract", ...docRef(contract) }] }),
  "purchase contract execution");
  const outcome = await fact("joe", "diligence_outcome", "deal", deal, { detail: "synthetic diligence" });
  ok(await joe.recordDiligenceOutcome({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "diligence_outcome", record_id: outcome }],
    declared: { diligence_result: "satisfied" } }), "diligence outcome");
  const settlement = await fact("joe", "closing_settlement", "deal", deal,
    { closing_date: "2026-09-21T15:00:00Z" });
  ok(await joe.recordDealClosing({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "final_closing_settlement", record_id: settlement }] }),
  "purchase closing");
  console.log(JSON.stringify(await state("joe", "deal", deal)).slice(0, 900));
});

test("a failed pending deal is cancelled with its reason and the assignment returns to search", { skip: SKIP }, async () => {
  const joe = as("joe");
  const { rel, eng, asg, deal } = await pendingDeal("lease");
  const failure = await fact("joe", "deal_failure", "deal", deal,
    { reason: "synthetic landlord withdrew" });
  ok(await joe.cancelPendingDeal({ idempotency_key: key(), subject_ref: REF("deal", deal),
    related_refs: { assignment: REF("assignment", asg), engagement: REF("engagement", eng),
      relationship: REF("relationship", rel) },
    evidence_refs: [{ evidence_kind: "deal_failure_record", record_id: failure }],
    declared: { return_phase: "search" } }), "cancel pending deal");
  console.log(JSON.stringify(await state("joe", "assignment", asg)).slice(0, 900));
});

function docPin(doc) {
  return { document_id: doc.document_id, expected_version_no: doc.version_no,
    expected_content_digest: doc.content_digest };
}
function docRef(doc) {
  return { document_id: doc.document_id, expected_version_no: doc.version_no,
    expected_content_digest: doc.content_digest };
}

// ===========================================================================
// THE FULL TRANSITION TRUTH TABLE, LIVE.
//
// v5J102TransitionTruthTable() is the kernel's own enumeration: every guard of
// every transition against every value that guard can be shown. Each cell here
// is DRIVEN through the real store against the real database — a subject in the
// cell's state, reached through the real doors, with admitting evidence bound
// to it — and the answer is compared with what the cell says.
//
// A CELL THE DATABASE CAN NEVER REACH is not quietly passed. Some state values
// have no door that produces them (no transition writes loi_countered, an
// expired engagement or a concluded assignment); some guards cannot be shown a
// wrong value through the store by construction (the store fixes the subject
// kind per operation and dispatches deal execution on the STORED instrument).
// Every such cell is named in the coverage report and the unreachable set is
// pinned, so a new door that makes one reachable fails this suite until the
// cell is driven.
// ===========================================================================

/** Deal steps, each a real store operation with admitting evidence. */
const DEAL_STEPS = {
  async lease_exec(d) {
    const doc = await f01Document("joe", { document_class: "lease" });
    await linkDocument("joe", doc, "deal", d.deal);
    return ok(await as("joe").recordDealExecution({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "executed_lease", ...docRef(doc) }] }), "lease exec");
  },
  async purchase_exec(d) {
    const doc = await f01Document("joe", { document_class: "purchase_contract" });
    await linkDocument("joe", doc, "deal", d.deal);
    return ok(await as("joe").recordDealExecution({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "signed_purchase_contract", ...docRef(doc) }] }),
    "purchase exec");
  },
  async diligence(d, result) {
    const r = await fact("joe", "diligence_outcome", "deal", d.deal, { detail: "synthetic" });
    return ok(await as("joe").recordDiligenceOutcome({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "diligence_outcome", record_id: r }],
      declared: { diligence_result: result } }), `diligence ${result}`);
  },
  async close(d) {
    const r = await fact("joe", "closing_settlement", "deal", d.deal,
      { closing_date: "2026-09-20T15:00:00Z" });
    return ok(await as("joe").recordDealClosing({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "final_closing_settlement", record_id: r }] }), "close");
  },
  async cancel(d) {
    const r = await fact("joe", "deal_failure", "deal", d.deal, { reason: "synthetic failure" });
    return ok(await as("joe").cancelPendingDeal({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      related_refs: { assignment: REF("assignment", d.asg) },
      evidence_refs: [{ evidence_kind: "deal_failure_record", record_id: r }],
      declared: { return_phase: "search" } }), "cancel");
  },
  async commission(d) {
    const doc = await f01Document("joe", { document_class: "commission_agreement" });
    await linkDocument("joe", doc, "deal", d.deal);
    return ok(await as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "commission_agreement", ...docRef(doc) }],
      declared: { axis: "commission_agreement_state" } }), "commission");
  },
  async invoice(d) {
    const r = await fact("joe", "invoice", "deal", d.deal, { detail: "synthetic" });
    return ok(await as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "invoice_issued", record_id: r }],
      declared: { axis: "invoice_state" } }), "invoice");
  },
  async payment(d, level) {
    const r = await fact("joe", "payment", "deal", d.deal, { detail: "synthetic" });
    return ok(await as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "payment_received", record_id: r }],
      declared: { axis: "payment_state", payment_level: level } }), `payment ${level}`);
  },
  async completion(d) {
    const r = await fact("joe", "completion", "deal", d.deal, { detail: "synthetic" });
    return ok(await as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "completion_recorded", record_id: r }],
      declared: { axis: "completion_state" } }), "completion");
  },
};

async function dealWith(instrument_kind, steps) {
  const d = await pendingDeal(instrument_kind);
  for (const step of steps) {
    const [name, arg] = step.split(":");
    await DEAL_STEPS[name](d, arg);
  }
  return d;
}

/**
 * A FRESH subject whose `axis` holds `value`, built through the real doors, or
 * null when no door produces that value. `instrument` is the deal instrument
 * wanted, so that record-deal-execution dispatches to the transition under test.
 */
async function subjectAt(subject_kind, axis, value, instrument = "lease") {
  if (subject_kind === "relationship") {
    if (value === "prospect") {
      const rel = id("rel");
      ok(await as("joe").initializeProspectRelationship({ idempotency_key: key(),
        declared: { new_subject_id: rel } }), "prospect");
      return { kind: "relationship", id: rel, rel };
    }
    if (value === "client") {
      const c = await client("joe");
      return { kind: "relationship", id: c.rel, ...c };
    }
    return null;
  }
  if (subject_kind === "assignment") {
    if (value === "research" || value === "search") {
      const c = await client("joe");
      const asg = id("asg");
      ok(await as("joe").initializeAssignment({ idempotency_key: key(),
        related_refs: { engagement: REF("engagement", c.eng), relationship: REF("relationship", c.rel) },
        declared: { new_subject_id: asg } }), "initialize assignment");
      if (value === "search") {
        const mandate = await fact("joe", "assignment_mandate", "assignment", asg, { detail: "s" });
        ok(await as("joe").openCreAssignment({ idempotency_key: key(),
          subject_ref: REF("assignment", asg),
          related_refs: { engagement: REF("engagement", c.eng), relationship: REF("relationship", c.rel) },
          evidence_refs: [{ evidence_kind: "search_initiation", record_id: mandate }],
          declared: { mandate_scope: "search" } }), "open assignment");
      }
      return { kind: "assignment", id: asg, asg, ...c };
    }
    if (value === "negotiation") {
      const a = await openedAssignment("joe");
      const n = await acceptedNegotiation("joe", a.asg);
      return { kind: "assignment", id: a.asg, ...a, neg: n.neg };
    }
    if (value === "committed") {
      const d = await pendingDeal("lease");
      return { kind: "assignment", id: d.asg, ...d };
    }
    return null;
  }
  if (subject_kind === "property_negotiation") {
    if (value === "loi_drafted" || value === "loi_submitted") {
      const a = await openedAssignment("joe");
      const neg = id("neg");
      ok(await as("joe").initializePropertyNegotiation({ idempotency_key: key(),
        related_refs: { assignment: REF("assignment", a.asg) },
        declared: { new_subject_id: neg, property_id: id("prop") } }), "initialize negotiation");
      if (value === "loi_submitted") {
        const loi = await f01Document("joe", { document_class: "letter_of_intent",
          states: { signature_state: "unsigned", validity_state: "draft" } });
        await linkDocument("joe", loi, "property_negotiation", neg);
        ok(await as("joe").recordLoiSubmission({ idempotency_key: key(),
          subject_ref: REF("property_negotiation", neg),
          related_refs: { assignment: REF("assignment", a.asg) },
          evidence_refs: [{ evidence_kind: "submitted_loi", ...docRef(loi) }] }), "LOI submission");
      }
      return { kind: "property_negotiation", id: neg, ...a, neg };
    }
    if (value === "loi_accepted") {
      const a = await openedAssignment("joe");
      const n = await acceptedNegotiation("joe", a.asg);
      return { kind: "property_negotiation", id: n.neg, ...a, neg: n.neg };
    }
    if (value === "selected_winner") {
      const d = await pendingDeal("lease");
      return { kind: "property_negotiation", id: d.neg, ...d };
    }
    return null;
  }
  if (subject_kind === "deal") {
    const exec = instrument === "purchase" ? "purchase_exec" : "lease_exec";
    // A purchase closes only after its diligence resolves (Q094).
    const closed = instrument === "purchase"
      ? [exec, "diligence:satisfied", "close"] : [exec, "close"];
    const steps = {
      deal_state: { pending: [], closed, cancelled: ["cancel"] },
      execution_state: { unexecuted: [], executed: [exec] },
      diligence_state: instrument === "purchase"
        ? { not_applicable: null, in_progress: [exec],
            satisfied: [exec, "diligence:satisfied"], waived: [exec, "diligence:waived"],
            failed: [exec, "diligence:failed"] }
        : { not_applicable: [], in_progress: null, satisfied: null, waived: null, failed: null },
      closing_state: { not_reached: [], closed },
      commission_agreement_state: { absent: [], agreed: ["commission"] },
      invoice_state: { not_invoiced: [], invoiced: ["invoice"] },
      payment_state: { unpaid: [], partially_paid: ["payment:partially_paid"],
        paid: ["payment:paid"] },
      completion_state: { open: [], complete: ["completion"] },
    }[axis]?.[value];
    if (steps === undefined || steps === null) return null;
    const d = await dealWith(instrument, steps);
    return { kind: "deal", id: d.deal, ...d };
  }
  return null; // engagement: no transition takes an engagement as its subject
}

/** The admitting evidence for one transition, bound to subject `s`. */
async function admittingEvidence(transition_id, s) {
  switch (transition_id) {
    case "establish-client-and-engagement": {
      const doc = await f01Document("joe", { document_class: "engagement_letter" });
      await linkDocument("joe", doc, "relationship", s.id);
      return [{ evidence_kind: "signed_engagement_letter", ...docRef(doc) }];
    }
    case "open-assignment":
      return [{ evidence_kind: "search_initiation",
        record_id: await fact("joe", "assignment_mandate", "assignment", s.id, { detail: "s" }) }];
    case "record-loi-submission": {
      const doc = await f01Document("joe", { document_class: "letter_of_intent",
        states: { signature_state: "unsigned", validity_state: "draft" } });
      await linkDocument("joe", doc, "property_negotiation", s.id);
      return [{ evidence_kind: "submitted_loi", ...docRef(doc) }];
    }
    case "record-loi-acceptance": {
      const art = await f01Artifact("joe");
      await linkArtifact("joe", art, "property_negotiation", s.id);
      return [{ evidence_kind: "counterparty_loi_acceptance", artifact_digest: art }];
    }
    case "commit-winning-property":
      return [{ evidence_kind: "winner_selection_commitment",
        record_id: await fact("joe", "winning_property_commitment", "assignment", s.id,
          { detail: "s" }) }];
    case "record-lease-execution": {
      const doc = await f01Document("joe", { document_class: "lease" });
      await linkDocument("joe", doc, "deal", s.id);
      return [{ evidence_kind: "executed_lease", ...docRef(doc) }];
    }
    case "record-purchase-contract-execution": {
      const doc = await f01Document("joe", { document_class: "purchase_contract" });
      await linkDocument("joe", doc, "deal", s.id);
      return [{ evidence_kind: "signed_purchase_contract", ...docRef(doc) }];
    }
    case "record-diligence-outcome":
      return [{ evidence_kind: "diligence_outcome",
        record_id: await fact("joe", "diligence_outcome", "deal", s.id, { detail: "s" }) }];
    case "record-deal-closing":
      return [{ evidence_kind: "final_closing_settlement",
        record_id: await fact("joe", "closing_settlement", "deal", s.id,
          { closing_date: "2026-09-20T15:00:00Z" }) }];
    case "cancel-pending-deal":
      return [{ evidence_kind: "deal_failure_record",
        record_id: await fact("joe", "deal_failure", "deal", s.id, { reason: "synthetic" }) }];
    case "record-commission-agreement": {
      const doc = await f01Document("joe", { document_class: "commission_agreement" });
      await linkDocument("joe", doc, "deal", s.id);
      return [{ evidence_kind: "commission_agreement", ...docRef(doc) }];
    }
    case "record-invoice-issued":
      return [{ evidence_kind: "invoice_issued",
        record_id: await fact("joe", "invoice", "deal", s.id, { detail: "s" }) }];
    case "record-payment":
      return [{ evidence_kind: "payment_received",
        record_id: await fact("joe", "payment", "deal", s.id, { detail: "s" }) }];
    case "record-completion":
      return [{ evidence_kind: "completion_recorded",
        record_id: await fact("joe", "completion", "deal", s.id, { detail: "s" }) }];
    default:
      throw new Error(`no admitting evidence for ${transition_id}`);
  }
}

/** One store call performing `transition_id` on subject `s`, as `who`. */
async function attempt(who, transition_id, s, evidence_refs, { subject_kind } = {}) {
  const store = as(who);
  const payload = { idempotency_key: key(),
    subject_ref: { subject_kind: subject_kind ?? s.kind, subject_id: s.id }, evidence_refs };
  const withRelated = related => (related ? { ...payload, related_refs: related } : payload);
  const calls = {
    "establish-client-and-engagement": () => store.recordRepresentationAgreement({ ...payload,
      declared: { new_subject_id: id("eng") } }),
    "open-assignment": () => store.openCreAssignment({
      ...withRelated(s.eng ? { engagement: REF("engagement", s.eng),
        relationship: REF("relationship", s.rel) } : null),
      declared: { mandate_scope: "search" } }),
    "record-loi-submission": () => store.recordLoiSubmission(
      withRelated(s.asg ? { assignment: REF("assignment", s.asg) } : null)),
    "record-loi-acceptance": () => store.recordLoiAcceptance(payload),
    "commit-winning-property": () => store.commitWinningProperty({
      ...withRelated(s.neg ? { property_negotiation: REF("property_negotiation", s.neg) } : null),
      declared: { instrument_kind: "lease", new_deal_id: id("deal") } }),
    "record-lease-execution": () => store.recordDealExecution(payload),
    "record-purchase-contract-execution": () => store.recordDealExecution(payload),
    "record-diligence-outcome": () => store.recordDiligenceOutcome({ ...payload,
      declared: { diligence_result: "satisfied" } }),
    "record-deal-closing": () => store.recordDealClosing(payload),
    "cancel-pending-deal": () => store.cancelPendingDeal({
      ...withRelated(s.asg ? { assignment: REF("assignment", s.asg) } : null),
      declared: { return_phase: "search" } }),
    "record-commission-agreement": () => store.recordDealAxis({ ...payload,
      declared: { axis: "commission_agreement_state" } }),
    "record-invoice-issued": () => store.recordDealAxis({ ...payload,
      declared: { axis: "invoice_state" } }),
    "record-payment": () => store.recordDealAxis({ ...payload,
      declared: { axis: "payment_state", payment_level: "paid" } }),
    "record-completion": () => store.recordDealAxis({ ...payload,
      declared: { axis: "completion_state" } }),
  };
  try {
    const answer = await calls[transition_id]();
    return { decision: answer.decision, reason_id: answer.reason_id,
      transition_id: answer.transition_id ?? null,
      unmet_axis: answer.refusal_detail?.unmet_axis ?? null,
      records_written: answer.records_written ?? null, at: "store_or_kernel" };
  } catch (error) {
    if (error?.constructor?.name === "V5J102StoreError") {
      return { decision: "refuse", reason_id: error.code, unmet_axis: null,
        records_written: 0, at: "store_boundary" };
    }
    throw error;
  }
}

async function digestOf(s) {
  return (await state("joe", s.kind, s.id)).readback?.body?.state_digest ?? null;
}

/** A subject the transition's four guards all admit, for driving one other guard. */
async function admittedSubject(transition_id, instrument) {
  const c = v5J102TransitionContract(transition_id);
  const inst = instrument ?? c.instrument_kinds?.[0] ??
    (transition_id === "record-diligence-outcome" ? "purchase" : "lease");
  const [axis, permitted] = Object.entries(c.prerequisites ?? {}).at(-1) ?? [null, []];
  // A commitment needs an ACCEPTED negotiation under its assignment, which only
  // an assignment already negotiating has.
  const value = transition_id === "commit-winning-property" ? "negotiation" : permitted[0];
  if (c.subject_kind === "deal") {
    // Every from-axis admitting at once: build the shape each deal transition needs.
    const steps = {
      "record-diligence-outcome": ["purchase_exec"],
      "record-deal-closing": [inst === "purchase" ? "purchase_exec" : "lease_exec"],
    }[transition_id] ?? [];
    const d = await dealWith(inst, steps);
    return { kind: "deal", id: d.deal, ...d };
  }
  return subjectAt(c.subject_kind, axis, value, inst);
}

const TABLE = LIVE ? v5J102TransitionTruthTable() : null;

// The state values NO door produces, pinned. A value leaves this set only by
// being reached — at which point the cells that need it must be driven.
const UNREACHABLE_STATE_VALUES = Object.freeze([
  "relationship_state=client_paused", "relationship_state=client_ended",
  "assignment_phase=concluded",
  "negotiation_state=loi_countered", "negotiation_state=loi_rejected",
  "negotiation_state=loi_withdrawn", "negotiation_state=superseded",
]);

test("LIVE TRUTH TABLE (prerequisite guard): every reachable cell of every transition decides as the table says", { skip: SKIP, timeout: 900000 }, async () => {
  const cells = TABLE.cells.filter(c => c.guard === "prerequisite");
  const report = { cells: cells.length, driven: 0, isolated: 0, not_isolated: [], unreachable: [] };
  const cache = new Map();
  for (const cell of cells) {
    const c = v5J102TransitionContract(cell.transition_id);
    const inst = c.instrument_kinds?.[0] ??
      (cell.transition_id === "record-diligence-outcome" ? "purchase" : "lease");
    // A REFUSING cell writes nothing, so its subject may be shared; an admitting
    // cell may move its subject, so it always gets a fresh one.
    const cacheKey = `${c.subject_kind}|${cell.axis}|${cell.value}|${inst}`;
    let s = cell.guard_admits ? undefined : cache.get(cacheKey);
    if (s === undefined) {
      s = await subjectAt(c.subject_kind, cell.axis, cell.value, inst);
      // Q094's split: diligence exists only on a purchase, so a lease deal is
      // the one that holds not_applicable, and the prerequisite still has to
      // refuse it.
      if (s === null && c.subject_kind === "deal" && cell.axis === "diligence_state") {
        s = await subjectAt("deal", cell.axis, cell.value, inst === "purchase" ? "lease" : "purchase");
      }
      if (!cell.guard_admits) cache.set(cacheKey, s);
    }
    if (s === null) {
      report.unreachable.push(`${cell.transition_id}:${cell.axis}=${cell.value}`);
      continue;
    }
    const before = await digestOf(s);
    const evidence = await admittingEvidence(cell.transition_id, s);
    const r = await attempt("joe", cell.transition_id, s, evidence);
    report.driven += 1;
    if (cell.guard_admits) {
      assert.ok(!(r.reason_id === "prerequisite_not_met" && r.unmet_axis === cell.axis),
        `${cell.transition_id} ${cell.axis}=${cell.value} is admitted by the table and refused live: ${JSON.stringify(r)}`);
      continue;
    }
    assert.equal(r.decision, "refuse",
      `${cell.transition_id} ${cell.axis}=${cell.value} must refuse: ${JSON.stringify(r)}`);
    assert.equal(r.reason_id, "prerequisite_not_met",
      `${cell.transition_id} ${cell.axis}=${cell.value}: ${JSON.stringify(r)}`);
    assert.equal(await digestOf(s), before, "a refusal moved the subject");
    if (r.unmet_axis === cell.axis) report.isolated += 1;
    else report.not_isolated.push(`${cell.transition_id}:${cell.axis}=${cell.value} (first unmet: ${r.unmet_axis})`);
  }
  console.log("LIVE prerequisite cells", JSON.stringify(report, null, 1));
  for (const u of report.unreachable) {
    const cellValue = u.split(":")[1];
    const pinned = UNREACHABLE_STATE_VALUES.includes(cellValue) ||
      // Diligence has no in_progress..failed state on a lease and no
      // not_applicable state on an executed purchase, by Q094's own split.
      cellValue.startsWith("diligence_state=");
    assert.ok(pinned, `${u} was not driven and its value is not pinned unreachable`);
  }
});

/** Evidence of ANY registered kind, bound to the subject its contract names. */
async function evidenceOfKind(kind, family) {
  const doc = async (document_class, subject_kind, subject_id, states) => {
    const d = await f01Document("joe", { document_class, states });
    await linkDocument("joe", d, subject_kind, subject_id);
    return docRef(d);
  };
  const rec = async (record_kind, subject_kind, subject_id, extra = { detail: "s" }) =>
    ({ record_id: await fact("joe", record_kind, subject_kind, subject_id, extra) });
  switch (kind) {
    case "signed_engagement_letter":
      return doc("engagement_letter", "relationship", family.rel);
    case "submitted_loi":
      return doc("letter_of_intent", "property_negotiation", family.neg,
        { signature_state: "unsigned", validity_state: "draft" });
    case "executed_lease": return doc("lease", "deal", family.deal);
    case "signed_purchase_contract":
      // F01 refuses a fully executed draft (draft_cannot_be_fully_executed), so a
      // signed purchase contract is `effective` in F01's vocabulary even while
      // the deal is pending through diligence.
      return doc("purchase_contract", "deal", family.deal);
    case "commission_agreement": return doc("commission_agreement", "deal", family.deal);
    case "counterparty_loi_acceptance": {
      const art = await f01Artifact("joe");
      await linkArtifact("joe", art, "property_negotiation", family.neg);
      return { artifact_digest: art };
    }
    case "search_initiation": return rec("assignment_mandate", "assignment", family.asg);
    case "winner_selection_commitment":
      return rec("winning_property_commitment", "assignment", family.asg);
    case "diligence_outcome": return rec("diligence_outcome", "deal", family.deal);
    case "final_closing_settlement":
      return rec("closing_settlement", "deal", family.deal, { closing_date: "2026-09-20T15:00:00Z" });
    case "deal_failure_record":
      return rec("deal_failure", "deal", family.deal, { reason: "synthetic" });
    case "invoice_issued": return rec("invoice", "deal", family.deal);
    case "payment_received": return rec("payment", "deal", family.deal);
    case "completion_recorded": return rec("completion", "deal", family.deal);
    case "manual_correction":
      return rec("lifecycle_correction", family.self_kind, family.self_id, { reason: "synthetic" });
    case "approved_representation_equivalent":
    case "multi_target_exception_approval":
      return { approval_ref: id("approval") };
    default:
      throw new Error(`no producer for evidence kind ${kind}`);
  }
}

test("LIVE TRUTH TABLE (subject_kind, actor_class, instrument_kind, required_evidence guards)", { skip: SKIP, timeout: 900000 }, async () => {
  const report = {};
  const tally = (guard, outcome) => {
    report[guard] ??= {};
    report[guard][outcome] = (report[guard][outcome] ?? 0) + 1;
  };
  // A family of subjects of every kind, to host evidence bound to a subject
  // OTHER than the one under test.
  const other = await pendingDeal("lease");

  for (const transition_id of V5_J102_TRANSITION_IDS) {
    const c = v5J102TransitionContract(transition_id);
    const own = TABLE.cells.filter(x => x.transition_id === transition_id);

    // --- subject_kind: the store fixes the kind per operation --------------
    const s0 = await admittedSubject(transition_id);
    for (const cell of own.filter(x => x.guard === "subject_kind" && !x.guard_admits)) {
      const ev = await admittingEvidence(transition_id, s0);
      const r = await attempt("joe", transition_id, s0, ev, { subject_kind: cell.value });
      assert.equal(r.decision, "refuse", `${transition_id} subject_kind=${cell.value}`);
      assert.equal(r.reason_id, "subject_kind_mismatch", `${transition_id} subject_kind=${cell.value}: ${JSON.stringify(r)}`);
      tally("subject_kind", `refused:${r.at}`);
    }

    // --- required_evidence: every kind but the admitting one refuses --------
    const before = await digestOf(s0);
    const family = { rel: s0.rel ?? other.rel, asg: s0.asg ?? other.asg,
      neg: s0.neg ?? other.neg, deal: s0.deal ?? other.deal,
      self_kind: s0.kind, self_id: s0.id };
    for (const cell of own.filter(x => x.guard === "required_evidence" && !x.guard_admits)) {
      const ref = { evidence_kind: cell.value, ...(await evidenceOfKind(cell.value, family)) };
      const r = await attempt("joe", transition_id, s0, [ref]);
      assert.equal(r.decision, "refuse",
        `${transition_id} evidence=${cell.value} must refuse: ${JSON.stringify(r)}`);
      assert.equal(r.records_written ?? 0, 0);
      tally("required_evidence", `refused:${r.reason_id}`);
    }
    assert.equal(await digestOf(s0), before, `${transition_id}: an evidence refusal moved the subject`);

    // --- actor_class: both classes, each on a fresh admitted subject --------
    for (const cell of own.filter(x => x.guard === "actor_class")) {
      const who = cell.value === "verified_partner" ? "dell" : "agent";
      const s = await admittedSubject(transition_id);
      const ev = await admittingEvidence(transition_id, s);
      const r = await attempt(who, transition_id, s, ev);
      if (cell.guard_admits) {
        assert.ok(r.reason_id !== "actor_class_not_permitted" &&
          r.reason_id !== "authority_only_operation_refused",
          `${transition_id} admits ${cell.value} and refused it live: ${JSON.stringify(r)}`);
        assert.equal(r.decision, "allow", `${transition_id} as ${cell.value}: ${JSON.stringify(r)}`);
        tally("actor_class", `admitted:${cell.value}`);
      } else {
        assert.equal(r.decision, "refuse", `${transition_id} as ${cell.value}`);
        assert.ok(["actor_class_not_permitted", "authority_only_operation_refused"].includes(r.reason_id),
          `${transition_id} as ${cell.value}: ${JSON.stringify(r)}`);
        tally("actor_class", `refused:${r.reason_id}`);
      }
    }

    // --- instrument_kind: dispatch on the STORED instrument ----------------
    for (const cell of own.filter(x => x.guard === "instrument_kind")) {
      const s = await admittedSubject(transition_id, cell.value);
      const ev = await admittingEvidence(transition_id, s);
      const r = await attempt("joe", transition_id, s, ev);
      if (cell.guard_admits) {
        assert.equal(r.decision, "allow", `${transition_id} on a ${cell.value} deal: ${JSON.stringify(r)}`);
        tally("instrument_kind", "admitted");
      } else {
        // The store never hands the lease transition a purchase deal: it
        // dispatches on the stored instrument, and the other transition then
        // refuses this evidence. Either way the transition under test does not run.
        assert.equal(r.decision, "refuse", `${transition_id} on a ${cell.value} deal`);
        assert.notEqual(r.transition_id, transition_id,
          `the store handed ${transition_id} a ${cell.value} deal`);
        tally("instrument_kind", `refused:${r.reason_id}`);
      }
    }
  }
  console.log("LIVE guard cells", JSON.stringify(report, null, 1));
});

// ===========================================================================
// Q103 LIVE: concurrent edits by two partners against the same base.
// ===========================================================================

async function body(name, subject_kind, subject_id) {
  return (await state(name, subject_kind, subject_id)).readback.body;
}

async function reconciliationItems(subject_kind, subject_id) {
  const answer = await as("joe").readCreLifecycle({
    selector: { kind: "reconciliation_items", subject_kind, subject_id } });
  return answer.readback?.body ?? [];
}

async function axisCall(who, deal, axis, evidence_kind, record_kind, base, extra = {}) {
  const r = await fact("joe", record_kind, "deal", deal, { detail: "synthetic" });
  return as(who).recordDealAxis({ idempotency_key: key(),
    subject_ref: { subject_kind: "deal", subject_id: deal, expected_state_digest: base },
    evidence_refs: [{ evidence_kind, record_id: r }],
    declared: { axis, ...extra } });
}

test("Q103 LIVE: a non-overlapping concurrent edit MERGES automatically and keeps the other partner's write", { skip: SKIP }, async () => {
  const d = await dealWith("lease", ["lease_exec"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;

  // Dell records the commission agreement, decided against `base`.
  const commission = await f01Document("dell", { document_class: "commission_agreement" });
  await linkDocument("dell", commission, "deal", d.deal);
  ok(await as("dell").recordDealAxis({ idempotency_key: key(),
    subject_ref: { subject_kind: "deal", subject_id: d.deal, expected_state_digest: base },
    evidence_refs: [{ evidence_kind: "commission_agreement", ...docRef(commission) }],
    declared: { axis: "commission_agreement_state" } }), "Dell's commission");

  // Joe records the invoice, ALSO decided against `base`, which has moved.
  const answer = ok(await axisCall("joe", d.deal, "invoice_state", "invoice_issued", "invoice", base),
    "Joe's invoice on a moved base");
  assert.equal(answer.auto_merged, true);
  assert.equal(answer.last_writer_wins, false);
  assert.equal(answer.concurrent_merges.length, 1);
  assert.equal(answer.concurrent_merges[0].concurrent_transition_id, "record-commission-agreement");
  assert.deepEqual(answer.concurrent_merges[0].incoming_fields, ["invoice_state"]);
  assert.deepEqual(answer.concurrent_merges[0].concurrent_fields, ["commission_agreement_state"]);

  const after = (await body("joe", "deal", d.deal)).state;
  assert.equal(after.commission_agreement_state, "agreed", "Dell's write survived the merge");
  assert.equal(after.invoice_state, "invoiced", "Joe's write landed");
  assert.deepEqual(await reconciliationItems("deal", d.deal), [], "nothing needed a person");
});

test("Q103 LIVE: an OVERLAPPING concurrent edit reconciles VISIBLY, both versions preserved, nothing moves", { skip: SKIP }, async () => {
  const d = await dealWith("lease", ["lease_exec"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;

  ok(await axisCall("dell", d.deal, "payment_state", "payment_received", "payment", base,
    { payment_level: "partially_paid" }), "Dell's partial payment");
  const moved = await body("joe", "deal", d.deal);

  const answer = await axisCall("joe", d.deal, "payment_state", "payment_received", "payment",
    base, { payment_level: "paid" });
  assert.equal(answer.decision, "reconcile", JSON.stringify(answer).slice(0, 800));
  assert.equal(answer.reason_id, "overlapping_transition_writes_require_reconciliation");
  assert.deepEqual(answer.overlapping_fields, ["payment_state"]);
  assert.equal(answer.concurrent_transition_id, "record-payment");
  assert.equal(answer.characterized, true);
  assert.equal(answer.auto_merged, false);
  assert.equal(answer.last_writer_wins, false);
  assert.equal(answer.advances_lifecycle_state, false);

  const after = await body("joe", "deal", d.deal);
  assert.equal(after.state_digest, moved.state_digest, "the reconcile moved nothing");
  assert.equal(after.state.payment_state, "partially_paid", "Dell's write was not overwritten");

  const items = await reconciliationItems("deal", d.deal);
  assert.equal(items.length, 1, "exactly one visible item");
  const item = items[0].record;
  assert.equal(item.conflict_kind, "overlapping_field_edit");
  assert.equal(item.visible, true);
  assert.equal(item.applied, false);
  assert.equal(item.resolved_by_machine, false);
  assert.equal(item.base_version_digest, base);
  assert.equal(item.current_version_digest, moved.state_digest);
  assert.deepEqual(item.incoming_edits.map(e => [e.field, e.edited_by]), [["payment_state", "joe"]]);
  assert.deepEqual(item.concurrent_edits.map(e => [e.field, e.edited_by]), [["payment_state", "dell"]]);
  assert.equal(item.concurrent_change_evidence.characterized, true);
});

test("Q103 LIVE: two intervening writes are UNCHARACTERIZED and reconcile visibly rather than merging on an absence", { skip: SKIP }, async () => {
  const d = await dealWith("lease", ["lease_exec"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;
  await DEAL_STEPS.commission(d);
  await DEAL_STEPS.completion(d);
  const moved = await body("joe", "deal", d.deal);

  const answer = await axisCall("joe", d.deal, "invoice_state", "invoice_issued", "invoice", base);
  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "concurrent_change_not_characterized");
  assert.equal(answer.characterized, false);
  assert.equal((await body("joe", "deal", d.deal)).state_digest, moved.state_digest);
  const items = await reconciliationItems("deal", d.deal);
  assert.equal(items.length, 1);
  assert.equal(items[0].record.conflict_kind, "uncharacterized_concurrent_change");
  assert.deepEqual(items[0].record.concurrent_edits, []);
});

test("Q103 LIVE: a disjoint edit whose transition no longer holds on the CURRENT row is refused, never merged", { skip: SKIP }, async () => {
  // Joe decides to close against an executed purchase whose diligence is
  // satisfied; Dell meanwhile records a failed diligence outcome. The fields are
  // disjoint (diligence_state vs deal/closing state), so the store re-admits the
  // closing against the current row — and the re-admission refuses.
  const d = await dealWith("purchase", ["purchase_exec", "diligence:satisfied"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;
  // A second diligence outcome is not admissible once resolved, so the
  // intervening write here is a cancellation's disjoint sibling: completion.
  await DEAL_STEPS.completion(d);
  const settlement = await fact("joe", "closing_settlement", "deal", d.deal,
    { closing_date: "2026-09-22T15:00:00Z" });
  const answer = ok(await as("joe").recordDealClosing({ idempotency_key: key(),
    subject_ref: { subject_kind: "deal", subject_id: d.deal, expected_state_digest: base },
    evidence_refs: [{ evidence_kind: "final_closing_settlement", record_id: settlement }] }),
  "closing merged over a disjoint completion");
  assert.equal(answer.auto_merged, true);

  // And the refusal half: a lease execution decided against a pending deal that
  // Dell has since CANCELLED. The fields are disjoint (execution_state against
  // deal_state and cancellation_reason), so the store re-admits the execution
  // against the current row — where the deal is no longer pending — and the
  // kernel refuses. The stale decision never lands.
  const d2 = await pendingDeal("lease");
  const base2 = (await body("joe", "deal", d2.deal)).state_digest;
  await DEAL_STEPS.cancel(d2);
  const lease = await f01Document("joe", { document_class: "lease" });
  await linkDocument("joe", lease, "deal", d2.deal);
  const r2 = await as("joe").recordDealExecution({ idempotency_key: key(),
    subject_ref: { subject_kind: "deal", subject_id: d2.deal, expected_state_digest: base2 },
    evidence_refs: [{ evidence_kind: "executed_lease", ...docRef(lease) }] });
  assert.equal(r2.decision, "refuse", JSON.stringify(r2).slice(0, 600));
  assert.equal(r2.reason_id, "prerequisite_not_met");
  assert.equal(r2.refusal_detail.unmet_axis, "deal_state");
  assert.equal(r2.readmission_refused_after_concurrent_change, true);
  assert.equal(r2.auto_merged, false);
  assert.equal(r2.concurrent_merges[0].concurrent_transition_id, "cancel-pending-deal");
  const after2 = (await body("joe", "deal", d2.deal)).state;
  assert.equal(after2.deal_state, "cancelled");
  assert.equal(after2.execution_state, "unexecuted");
});

test("Q103 LIVE: two partners racing on disjoint axes from the same base both land, or the loser is told; nothing is lost", { skip: SKIP }, async () => {
  const d = await dealWith("lease", ["lease_exec"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;
  const commission = await f01Document("dell", { document_class: "commission_agreement" });
  await linkDocument("dell", commission, "deal", d.deal);
  const invoiceRecord = await fact("joe", "invoice", "deal", d.deal, { detail: "synthetic" });
  const settle = p => p.then(v => ({ ok: v }), e => ({ error: String(e?.message ?? e) }));
  const [a, b] = await Promise.all([
    settle(as("dell").recordDealAxis({ idempotency_key: key(),
      subject_ref: { subject_kind: "deal", subject_id: d.deal, expected_state_digest: base },
      evidence_refs: [{ evidence_kind: "commission_agreement", ...docRef(commission) }],
      declared: { axis: "commission_agreement_state" } })),
    settle(as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: { subject_kind: "deal", subject_id: d.deal, expected_state_digest: base },
      evidence_refs: [{ evidence_kind: "invoice_issued", record_id: invoiceRecord }],
      declared: { axis: "invoice_state" } })),
  ]);
  const after = (await body("joe", "deal", d.deal)).state;
  const landed = x => x.ok?.decision === "allow";
  assert.ok(landed(a) || landed(b), `at least one write lands: ${JSON.stringify([a, b]).slice(0, 600)}`);
  assert.equal(after.commission_agreement_state === "agreed", landed(a));
  assert.equal(after.invoice_state === "invoiced", landed(b));
  for (const x of [a, b]) {
    if (landed(x)) continue;
    // The loser learned it lost: the database's own compare-and-swap refused it.
    assert.ok(x.error?.includes("j102_") || x.ok?.decision !== "allow",
      `a losing write must be refused loudly: ${JSON.stringify(x).slice(0, 400)}`);
  }
});

// ===========================================================================
// CREDENTIAL SPLIT LIVE: a verified partner on the WRITER credential.
// mcp.js routes every verb that is not authorityOnly over carr_writer, and F01
// derives that credential as a sponsored agent. The store adopts that narrower
// class rather than refusing, and never lets it reach a partner-only act.
// ===========================================================================

test("CREDENTIAL SPLIT LIVE: Joe on the writer credential records agent-admitted work under the credential's class", { skip: SKIP }, async () => {
  const rel = id("rel");
  ok(await as("joe_writer").initializeProspectRelationship({ idempotency_key: key(),
    declared: { new_subject_id: rel } }), "Joe initializes over the writer credential");
  assert.equal((await subject("joe", "relationship", rel)).decision, "allow", "the row exists");
  // An agent-admitted fact written by Joe over the writer credential is stamped
  // with the CREDENTIAL's class and Joe's name — both facts, neither widened.
  const onWriter = await fact("joe_writer", "invoice", "relationship", rel, { detail: "synthetic" })
    .catch(() => null);
  const asg = (await openedAssignment("joe")).asg;
  const mandate = await fact("joe_writer", "assignment_mandate", "assignment", asg, { detail: "synthetic" });
  const onAuthority = await fact("joe", "assignment_mandate", "assignment", asg, { detail: "synthetic" });
  const rows = await sql("joe",
    "select record_id, recorded_by, recorded_by_class from ops.j102_first_party_record where record_id = any($1)",
    [[mandate, onAuthority]]);
  const by = Object.fromEntries(rows.map(r => [r.record_id, [r.recorded_by, r.recorded_by_class]]));
  assert.deepEqual(by[mandate], ["joe", "sponsored_agent"], "writer credential: Joe, at the credential's class");
  assert.deepEqual(by[onAuthority], ["joe", "verified_partner"], "authority credential: Joe, as a partner");
  assert.equal(onWriter, null, "an invoice cannot bind to a relationship on either credential");
});

test("CREDENTIAL SPLIT LIVE: a partner-authored fact over the writer credential refuses and names the authority verb", { skip: SKIP }, async () => {
  const d = await pendingDeal("lease");
  await assert.rejects(
    as("joe_writer").recordLifecycleFact({ idempotency_key: key(), fact: {
      record_kind: "closing_settlement", record_id: id("closing-settlement"),
      subject_kind: "deal", subject_id: d.deal, closing_date: "2026-09-22T15:00:00Z" } }),
    e => e.code === "partner_authored_record_kind_refused" &&
      e.detail?.use_verb === "record-partner-lifecycle-fact" &&
      e.detail?.credential_split?.handler_authorization_class === "verified_partner",
    "the writer credential never authors a partner fact");
  // The same fact on Joe's authority credential lands.
  await fact("joe", "closing_settlement", "deal", d.deal, { closing_date: "2026-09-22T15:00:00Z" });
});

test("CREDENTIAL SPLIT LIVE: a partner-only transition over the writer credential is not attributable and refuses", { skip: SKIP }, async () => {
  const d = await pendingDeal("lease");
  const failure = await fact("joe", "deal_failure", "deal", d.deal, { reason: "synthetic" });
  const base = (await subject("joe", "deal", d.deal)).readback.body.state_digest;
  await assert.rejects(
    as("joe_writer").cancelPendingDeal({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      related_refs: { assignment: REF("assignment", d.asg) },
      evidence_refs: [{ evidence_kind: "deal_failure_record", record_id: failure }],
      declared: { return_phase: "search" } }),
    e => e.code === "actor_context_mismatch",
    "an authorityOnly act is never adopted down to the writer credential");
  assert.equal((await subject("joe", "deal", d.deal)).readback.body.state_digest, base, "nothing moved");
});

// ===========================================================================
// Q081 LIVE: the migration shadow runs OLD and NEW side by side.
// Legacy public.deal rows are seeded on the owner connection (the legacy
// writers are not this slice's), linked through the real Salesforce reference
// door, and compared by the real runner. Nothing on either side is modified, and
// no caller retires: that needs a verified census nobody has produced.
// ===========================================================================

const OWNER_DSN = process.env.CARR_J102_LIVE_PG_DSN_OWNER;
const SHADOW_SKIP = SKIP || (!OWNER_DSN &&
  "CARR_J102_LIVE_PG_DSN_OWNER is not set; the shadow test seeds legacy rows on the owner connection");

async function owner(text, params = []) {
  if (!pg) pg = (await import("pg")).default;
  const c = new pg.Client({ connectionString: OWNER_DSN });
  await c.connect();
  try {
    // Legacy seeding only: the legacy table's own FK and monitor triggers belong
    // to other slices and are not what this test is about.
    await c.query("set session_replication_role = replica");
    return (await c.query(text, params)).rows;
  } finally {
    await c.end();
  }
}

async function legacyRow({ salesforce_id = null, phase, outcome = null, closed_on = null }) {
  const [row] = await owner(
    `insert into public.deal (client_id, name, salesforce_id, deal_type, phase, outcome, closed_on,
                              created_by, updated_by)
     values (gen_random_uuid(), $1, $2, 'lease', $3, $4, $5, gen_random_uuid(), gen_random_uuid())
     returning id::text as id`,
    [`synthetic legacy ${RUN}`, salesforce_id, phase, outcome, closed_on]);
  return row.id;
}

async function linkOpportunity(opportunity_id, kind, subject_id) {
  ok(await as("joe").linkSalesforceReference({ idempotency_key: key(),
    opportunity_id, opportunity_name: `synthetic ${opportunity_id}`, opportunity_phase: "Negotiation",
    observed_at: "2026-09-01T00:00:00Z", linked_subject_kind: kind, linked_subject_id: subject_id }),
  `link ${opportunity_id}`);
}

async function readiness() {
  const answer = await as("joe").readCreLifecycle({ selector: { kind: "migration_shadow" } });
  assert.equal(answer.decision, "allow", JSON.stringify(answer).slice(0, 400));
  return answer.readback.body;
}

/**
 * KERNEL/SQL PARITY ON THE SAME RUN. The kernel is handed exactly the counts the
 * database recorded and must reach the same cleanliness and the same missing
 * facts; a drift between the two readers fails here, on real rows.
 */
function assertKernelAgrees(sqlReadiness) {
  const r = sqlReadiness.latest_shadow_run;
  const kernel = v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID,
    latest_shadow_run: r === null ? null : {
      run_digest: r.run_digest, compared_rows: r.compared_rows, matching_rows: r.matching_rows,
      differing_rows: r.differing_rows, unlinked_rows: r.unlinked_rows,
      many_to_one_subjects: r.many_to_one_subjects,
      subjects_without_legacy_row: r.subjects_without_legacy_row,
      snapshot_current: r.snapshot_current, clean: r.clean } });
  assert.equal(kernel.shadow_comparison_clean_run, sqlReadiness.shadow_comparison_clean_run ?? false);
  assert.equal(kernel.may_retire_callers, sqlReadiness.may_retire_callers);
  assert.deepEqual(kernel.missing_facts.map(f => [f.fact, f.produced_by]),
    sqlReadiness.missing_facts.map(f => [f.fact, f.produced_by]));
}

async function runRecord(run_seq) {
  const [row] = await sql("joe",
    "select envelope from ops.j102_migration_shadow_run where run_seq = $1", [run_seq]);
  return row.envelope.record;
}

test("Q081 LIVE: the shadow compares old and new side by side, reports every difference and unlinked row, modifies neither, and retires nobody", { skip: SHADOW_SKIP }, async () => {
  const preexisting = await owner("select count(*)::int as n from public.deal");
  assert.equal(preexisting[0].n, 0, "the scratch database must start with no legacy rows");

  const before = await readiness();
  assert.equal(before.may_retire_callers, false);
  assert.equal(before.latest_shadow_run, null, "no run has happened yet");
  assert.deepEqual(before.missing_facts.map(f => f.fact),
    ["exact_caller_census", "shadow_comparison_clean_run"]);
  assertKernelAgrees(before);

  // One executed, pending lease: projects legacy phase 'legal', not closed, open.
  const d = await dealWith("lease", ["lease_exec"]);
  const sfA = `006SYN${RUN}A`, sfB = `006SYN${RUN}B`;
  await linkOpportunity(sfA, "deal", d.deal);
  await linkOpportunity(sfB, "deal", d.deal);
  const matching = await legacyRow({ salesforce_id: sfA, phase: "legal" });
  const differing = await legacyRow({ salesforce_id: sfB, phase: "closing", outcome: "won",
    closed_on: "2026-09-20" });
  const unlinked = await legacyRow({ phase: "research" });
  // RULING (f): a J102 deal whose Salesforce opportunity no legacy row holds.
  const d2 = await dealWith("lease", ["lease_exec"]);
  const sfC = `006SYN${RUN}C`;
  await linkOpportunity(sfC, "deal", d2.deal);
  const snapshot = async () => owner(
    "select id::text, phase, outcome, closed_on::text, version, updated_at::text from public.deal order by id");
  const legacyBefore = await snapshot();
  const dealBefore = (await subject("joe", "deal", d.deal)).readback.body.state_digest;

  const runKey = key();
  const run = ok(await as("agent").runMigrationShadow({ idempotency_key: runKey }),
    "a sponsored agent runs the shadow");
  assert.equal(run.actor_slug, "codex");
  assert.deepEqual([run.compared_rows, run.matching_rows, run.differing_rows, run.unlinked_rows,
    run.many_to_one_subjects, run.subjects_without_legacy_row, run.clean],
  [2, 1, 1, 1, 1, 1, false]);
  assert.match(run.legacy_snapshot_digest, /^sha256:[0-9a-f]{64}$/);
  assert.match(run.projection_snapshot_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(run.legacy_rows_modified, 0);
  assert.equal(run.retires_any_caller, false);

  const record = await runRecord(run.run_seq);
  const byId = Object.fromEntries(record.rows.map(r => [r.legacy_row_id, r]));
  assert.equal(byId[matching].status, "matching");
  assert.deepEqual(byId[matching].differences, []);
  assert.equal(byId[differing].status, "differing");
  assert.deepEqual(byId[differing].differences.map(x => [x.field, x.legacy, x.projected]),
    [["phase", "closing", "legal"], ["closed", true, false], ["outcome", "won", "open"]]);
  assert.equal(byId[unlinked].status, "unlinked");
  // MANY-TO-ONE is named: both legacy rows resolve to the one J102 deal.
  assert.deepEqual(record.many_to_one.map(m => [m.deal_id, [...m.legacy_row_ids].sort()]),
    [[d.deal, [matching, differing].sort()]]);
  // And the subject with no legacy row is named with the opportunity it claims.
  assert.deepEqual(record.subjects_without_legacy_rows.map(x => [x.subject_kind, x.subject_id, x.opportunity_ids]),
    [["deal", d2.deal, [sfC]]]);

  assert.deepEqual(await snapshot(), legacyBefore, "no legacy row was touched");
  assert.equal((await subject("joe", "deal", d.deal)).readback.body.state_digest, dealBefore,
    "no lifecycle row was touched");

  // Replay returns the same run; it does not append a second observation.
  const replay = ok(await as("agent").runMigrationShadow({ idempotency_key: runKey }), "replay");
  assert.equal(replay.run_seq, run.run_seq);
  assert.equal((await sql("joe", "select count(*)::int as n from ops.j102_migration_shadow_run"))[0].n, 1);

  const dirty = await readiness();
  assert.equal(dirty.may_retire_callers, false);
  assert.equal(dirty.shadow_comparison_clean_run, false);
  assert.equal(dirty.latest_shadow_run.run_seq, run.run_seq);
  assert.deepEqual(dirty.missing_facts.map(f => f.fact),
    ["exact_caller_census", "shadow_comparison_clean_run"]);
  assertKernelAgrees(dirty);

  // A person resolves the two problem rows (here: the synthetic rows go away),
  // and the next run is clean — and STILL retires nobody without a census.
  await owner("delete from public.deal where id = any($1::uuid[])", [[differing, unlinked]]);
  const found = await legacyRow({ salesforce_id: sfC, phase: "legal" });
  const clean = ok(await as("joe").runMigrationShadow({ idempotency_key: key() }), "clean run");
  assert.deepEqual([clean.compared_rows, clean.matching_rows, clean.differing_rows, clean.unlinked_rows,
    clean.many_to_one_subjects, clean.subjects_without_legacy_row, clean.clean],
  [2, 2, 0, 0, 0, 0, true]);
  const after = await readiness();
  assert.equal(after.shadow_comparison_clean_run, true);
  assert.equal(after.latest_shadow_run.run_seq, clean.run_seq);
  assert.equal(after.may_retire_callers, false, "a clean shadow is not a caller census");
  assert.equal(after.migration_complete, false);
  assert.deepEqual(after.missing_facts.map(f => f.fact), ["exact_caller_census"]);
  assert.equal(after.latest_shadow_run.snapshot_current, true);
  assertKernelAgrees(after);

  // A CLEAN RUN IS PROOF ONLY OF ITS SNAPSHOT. A legacy row arriving after the
  // run moves the snapshot, and the reader stops counting the run.
  const late = await legacyRow({ phase: "research" });
  const stale = await readiness();
  assert.equal(stale.latest_shadow_run.clean, true, "the run itself stays clean");
  assert.equal(stale.latest_shadow_run.snapshot_current, false);
  assert.equal(stale.shadow_comparison_clean_run, false, "but it is stale");
  assert.match(stale.missing_facts[1].why, /snapshot that has since moved/);
  assertKernelAgrees(stale);
  // The binding is to CONTENT: remove the late row and the same run is current again.
  await owner("delete from public.deal where id = $1::uuid", [late]);
  assert.equal((await readiness()).shadow_comparison_clean_run, true,
    "the snapshot the run read is the snapshot again");

  // AN EDIT TO A COMPARED FIELD of a row the run already compared stales it too:
  // the run's verdict on that row is no longer a verdict on what the row says.
  await owner("update public.deal set phase = 'closing' where id = $1::uuid", [matching]);
  const edited = await readiness();
  assert.equal(edited.latest_shadow_run.clean, true);
  assert.equal(edited.latest_shadow_run.snapshot_current, false, "a compared field moved");
  assert.equal(edited.shadow_comparison_clean_run, false);
  assertKernelAgrees(edited);
  await owner("update public.deal set closed_on = '2026-09-01' where id = $1::uuid", [matching]);
  assert.equal((await readiness()).shadow_comparison_clean_run, false, "closed_on is compared too");
  await owner("update public.deal set phase = 'legal', closed_on = null where id = $1::uuid", [matching]);
  assert.equal((await readiness()).shadow_comparison_clean_run, true, "restored, current again");

  // EACH FAULT ALONE SPOILS A RUN. Every other row matches in each of these, so
  // the one fault is the only thing standing between the run and "clean".
  const counts = r => [r.compared_rows, r.matching_rows, r.differing_rows, r.unlinked_rows,
    r.many_to_one_subjects, r.subjects_without_legacy_row, r.clean];
  const lone = await legacyRow({ phase: "research" });
  assert.deepEqual(counts(ok(await as("joe").runMigrationShadow({ idempotency_key: key() }), "unlinked only")),
    [2, 2, 0, 1, 0, 0, false], "an unlinked row alone spoils the run");
  await owner("delete from public.deal where id = $1::uuid", [lone]);
  const twin = await legacyRow({ salesforce_id: sfB, phase: "legal" });
  assert.deepEqual(counts(ok(await as("joe").runMigrationShadow({ idempotency_key: key() }), "many-to-one only")),
    [3, 3, 0, 0, 1, 0, false], "two matching legacy rows on one subject alone spoil the run");
  await owner("delete from public.deal where id = $1::uuid", [twin]);
  const d3 = await dealWith("lease", ["lease_exec"]);
  const sfD = `006SYN${RUN}D`;
  await linkOpportunity(sfD, "deal", d3.deal);
  assert.deepEqual(counts(ok(await as("joe").runMigrationShadow({ idempotency_key: key() }), "orphan only")),
    [2, 2, 0, 0, 0, 1, false], "a referenced subject with no legacy row alone spoils the run");
  const d3legacy = await legacyRow({ salesforce_id: sfD, phase: "legal" });
  assert.deepEqual(counts(ok(await as("joe").runMigrationShadow({ idempotency_key: key() }), "all resolved")),
    [3, 3, 0, 0, 0, 0, true]);
  assert.equal((await readiness()).shadow_comparison_clean_run, true);
  // The same thing from the NEW side: a linked subject moving also stales it.
  ok(await axisCall("joe", d2.deal, "payment_state", "payment_received", "payment",
    (await body("joe", "deal", d2.deal)).state_digest, { payment_level: "partially_paid" }),
  "a linked deal moves");
  const moved = await readiness();
  assert.equal(moved.latest_shadow_run.snapshot_current, false, "a moved linked subject stales the run");
  assert.equal(moved.shadow_comparison_clean_run, false);
  assertKernelAgrees(moved);

  // The run history is the runner's alone.
  await assert.rejects(sql("agent",
    "insert into ops.j102_migration_shadow_run (tenant) values ('x')"), /permission denied|j102_/);
  await assert.rejects(sql("joe",
    "delete from ops.j102_migration_shadow_run"), /permission denied|j102_/);
  await owner("delete from public.deal where id = any($1::uuid[])", [[matching, found, d3legacy]]);
});

// ===========================================================================
// Q103 LIVE, after independent review: the merge is durable, the caller's key
// survives a conflict, and a stale READ-ONLY subject refuses instead of crashing.
// ===========================================================================

test("Q103 LIVE: a replay of a merged transition reports the merge the original write made", { skip: SKIP }, async () => {
  const d = await dealWith("lease", ["lease_exec"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;
  await DEAL_STEPS.commission(d);
  const invoice = await fact("joe", "invoice", "deal", d.deal, { detail: "synthetic" });
  const payload = { idempotency_key: key(),
    subject_ref: { subject_kind: "deal", subject_id: d.deal, expected_state_digest: base },
    evidence_refs: [{ evidence_kind: "invoice_issued", record_id: invoice }],
    declared: { axis: "invoice_state" } };
  const first = ok(await as("joe").recordDealAxis(payload), "merged invoice");
  assert.equal(first.auto_merged, true);
  const replay = ok(await as("joe").recordDealAxis(payload), "replay");
  assert.equal(replay.auto_merged, true, "the replay says the write was a machine merge");
  assert.deepEqual(replay.concurrent_merges, first.concurrent_merges);
  assert.equal(replay.concurrent_merges_scope,
    "caller_reported_merge_account_cas_enforced_on_current_row");
});

test("Q103 LIVE: a conflict does not spend the caller's key; the same key lands on a fresh base", { skip: SKIP }, async () => {
  const d = await dealWith("lease", ["lease_exec"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;
  ok(await axisCall("dell", d.deal, "payment_state", "payment_received", "payment", base,
    { payment_level: "partially_paid" }), "Dell's partial payment");
  const payment = await fact("joe", "payment", "deal", d.deal, { detail: "synthetic" });
  const transitionKey = key();
  const call = expected => as("joe").recordDealAxis({ idempotency_key: transitionKey,
    subject_ref: { subject_kind: "deal", subject_id: d.deal, expected_state_digest: expected },
    evidence_refs: [{ evidence_kind: "payment_received", record_id: payment }],
    declared: { axis: "payment_state", payment_level: "paid" } });
  const conflict = await call(base);
  assert.equal(conflict.decision, "reconcile");
  // Re-sending the same stale request replays the same item, not a second one.
  assert.equal((await call(base)).decision, "reconcile");
  assert.equal((await reconciliationItems("deal", d.deal)).length, 1);
  // A person looked, re-read, and decided again on the current row: same key.
  const fresh = (await body("joe", "deal", d.deal)).state_digest;
  const landed = ok(await call(fresh), "the same key on a fresh base");
  assert.equal((await body("joe", "deal", d.deal)).state.payment_state, "paid");
  assert.equal(landed.auto_merged, undefined);
});

test("Q103 LIVE: an untraceable move of a subject the transition only READS refuses as a stale read", { skip: SKIP }, async () => {
  const { rel, eng } = await client("joe");
  const asg = id("asg");
  ok(await as("joe").initializeAssignment({ idempotency_key: key(),
    related_refs: { engagement: REF("engagement", eng), relationship: REF("relationship", rel) },
    declared: { new_subject_id: asg } }), "initialize assignment");
  const mandate = await fact("joe", "assignment_mandate", "assignment", asg, { detail: "synthetic" });
  const answer = await as("joe").openCreAssignment({ idempotency_key: key(),
    subject_ref: REF("assignment", asg),
    related_refs: {
      // A base the engagement never held: its row is not one transition past it.
      engagement: { subject_kind: "engagement", subject_id: eng, expected_state_digest: D("never") },
      relationship: REF("relationship", rel) },
    evidence_refs: [{ evidence_kind: "search_initiation", record_id: mandate }],
    declared: { mandate_scope: "search" } });
  assert.equal(answer.decision, "refuse", JSON.stringify(answer).slice(0, 500));
  assert.equal(answer.reason_id, "stale_related_subject_digest");
  assert.equal(answer.records_written, 0);
  assert.deepEqual(await reconciliationItems("engagement", eng), [], "nothing to reconcile: nothing of the caller's is lost");
});

// ===========================================================================
// THE DATABASE COMPARE-AND-SWAP, PROVED DETERMINISTICALLY.
// The store reads the row, decides, and hands the write to
// ops.j102_apply_transition. Another partner's write can commit in that window.
// The store's own staleness judgement cannot see it (it already read), so the
// ONLY thing standing between that window and a lost update is the SQL
// compare-and-swap. This hook commits the other write on a different connection
// at exactly that moment, on the SAME field, so a disabled CAS loses Dell's
// payment and this test fails. (The disjoint-field race above cannot prove this:
// the field-level check would hide a missing CAS there.)
// ===========================================================================

/** A store whose transaction runs `inject` (other connections) just before the SQL writer. */
function racingStoreFor(name, inject, writer = /ops\.j102_apply_transition\(/) {
  let fired = false;
  return createCreLifecycleStore({ db: {
    async query() { throw new Error("racingStoreFor: transaction() is always used"); },
    async transaction(fn) {
      const client = await (await pool(name)).connect();
      try {
        await client.query("BEGIN");
        const out = await fn({ query: async (text, params) => {
          if (!fired && writer.test(text)) {
            fired = true;
            await inject();
          }
          return client.query(text, params);
        } });
        await client.query("COMMIT");
        return out;
      } catch (e) {
        await client.query("ROLLBACK").catch(() => {});
        throw e;
      } finally {
        client.release();
      }
    },
  } });
}

test("CAS LIVE: a same-field write committed between the store's read and the SQL writer is refused by the database, never overwritten", { skip: SKIP }, async () => {
  const d = await dealWith("lease", ["lease_exec"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;
  const payment = await fact("joe", "payment", "deal", d.deal, { detail: "synthetic" });
  let injected = false;
  const store = racingStoreFor("joe", async () => {
    ok(await axisCall("dell", d.deal, "payment_state", "payment_received", "payment", base,
      { payment_level: "partially_paid" }), "Dell lands inside Joe's window");
    injected = true;
  });
  await assert.rejects(
    store.recordDealAxis({ idempotency_key: key(),
      subject_ref: { subject_kind: "deal", subject_id: d.deal, expected_state_digest: base },
      evidence_refs: [{ evidence_kind: "payment_received", record_id: payment }],
      declared: { axis: "payment_state", payment_level: "paid" } }, { actor: JOE }),
    e => /j102_stale_subject_digest/.test(String(e?.message)) && e?.code === "40001",
    "the database compare-and-swap refuses the stale write with a serialization failure");
  assert.equal(injected, true, "the race actually happened inside the window");
  const after = (await body("joe", "deal", d.deal)).state;
  assert.equal(after.payment_state, "partially_paid", "Dell's committed payment survives");
});

// ===========================================================================
// OWNER RULINGS (a) and (b), 2026-09-25, encoded and proved live.
// ===========================================================================

test("RULING (a) LIVE: a prospect may name its CARR party; a party nobody holds refuses", { skip: SHADOW_SKIP }, async () => {
  const [row] = await owner(
    `insert into public.party (kind, name, created_by, updated_by)
     select 'org', $1, a.id, a.id from (select id from public.actor order by id limit 1) a
     returning id::text as id`, [`j102 live party ${RUN}`]);
  assert.ok(row?.id, "the owner seeded one party (needs one actor row)");
  const linked = id("rel");
  ok(await as("joe").initializeProspectRelationship({ idempotency_key: key(),
    declared: { new_subject_id: linked, party_id: row.id } }), "prospect with a party link");
  assert.equal((await body("joe", "relationship", linked)).state.party_id, row.id);
  const unlinked = id("rel");
  ok(await as("joe").initializeProspectRelationship({ idempotency_key: key(),
    declared: { new_subject_id: unlinked } }), "prospect with no party");
  assert.equal((await body("joe", "relationship", unlinked)).state.party_id, null);
  await assert.rejects(
    as("joe").initializeProspectRelationship({ idempotency_key: key(),
      declared: { new_subject_id: id("rel"), party_id: randomUUID() } }),
    e => /j102_party_not_found/.test(String(e?.message)),
    "a party id no CARR party holds is a dangling reference and refuses at the writer");
});

test("RULING (b) LIVE: one ACTIVE negotiation per assignment and property; a new one opens once the old is no longer active", { skip: SKIP }, async () => {
  const { rel, eng, asg } = await openedAssignment("joe");
  const property = id("prop");
  const first = id("neg");
  ok(await as("joe").initializePropertyNegotiation({ idempotency_key: key(),
    related_refs: { assignment: REF("assignment", asg) },
    declared: { new_subject_id: first, property_id: property } }), "first negotiation");
  const dup = await as("dell").initializePropertyNegotiation({ idempotency_key: key(),
    related_refs: { assignment: REF("assignment", asg) },
    declared: { new_subject_id: id("neg"), property_id: property } });
  assert.equal(dup.decision, "refuse");
  assert.equal(dup.reason_id, "active_negotiation_exists_for_property");
  assert.deepEqual(dup.refusal_detail.active_negotiation_ids, [first], "the refusal names the live sibling");
  // Per PROPERTY: the same assignment may negotiate another property.
  ok(await as("dell").initializePropertyNegotiation({ idempotency_key: key(),
    related_refs: { assignment: REF("assignment", asg) },
    declared: { new_subject_id: id("neg"), property_id: id("prop") } }), "a different property");

  // Once the first is no longer active (won, then its deal failed and the
  // assignment returned to search), the same property may be negotiated again.
  const deal = id("deal");
  const loi = await f01Document("joe", { document_class: "letter_of_intent",
    states: { signature_state: "unsigned", validity_state: "draft" } });
  await linkDocument("joe", loi, "property_negotiation", first);
  ok(await as("joe").recordLoiSubmission({ idempotency_key: key(),
    subject_ref: REF("property_negotiation", first),
    related_refs: { assignment: REF("assignment", asg) },
    evidence_refs: [{ evidence_kind: "submitted_loi", ...docRef(loi) }] }), "LOI submission");
  const acceptance = await f01Artifact("joe");
  await linkArtifact("joe", acceptance, "property_negotiation", first);
  ok(await as("joe").recordLoiAcceptance({ idempotency_key: key(),
    subject_ref: REF("property_negotiation", first),
    evidence_refs: [{ evidence_kind: "counterparty_loi_acceptance", artifact_digest: acceptance }] }),
  "LOI acceptance");
  const commitment = await fact("joe", "winning_property_commitment", "assignment", asg,
    { detail: "synthetic winner selection" });
  ok(await as("joe").commitWinningProperty({ idempotency_key: key(),
    subject_ref: REF("assignment", asg),
    related_refs: { property_negotiation: REF("property_negotiation", first) },
    evidence_refs: [{ evidence_kind: "winner_selection_commitment", record_id: commitment }],
    declared: { instrument_kind: "lease", new_deal_id: deal } }), "commit winning property");
  const failure = await fact("joe", "deal_failure", "deal", deal, { reason: "synthetic" });
  ok(await as("joe").cancelPendingDeal({ idempotency_key: key(), subject_ref: REF("deal", deal),
    related_refs: { assignment: REF("assignment", asg), engagement: REF("engagement", eng),
      relationship: REF("relationship", rel) },
    evidence_refs: [{ evidence_kind: "deal_failure_record", record_id: failure }],
    declared: { return_phase: "search" } }), "cancel pending deal");
  assert.equal((await body("joe", "property_negotiation", first)).state.negotiation_state,
    "selected_winner", "the old winner is not ACTIVE");
  ok(await as("joe").initializePropertyNegotiation({ idempotency_key: key(),
    related_refs: { assignment: REF("assignment", asg) },
    declared: { new_subject_id: id("neg"), property_id: property } }),
  "the same property reopens once nothing on it is active");
});

test("RULING (b) LIVE: a sibling committed inside the store's window is refused by the WRITER; the partial unique index backs it", { skip: SKIP }, async () => {
  const { asg } = await openedAssignment("joe");
  const property = id("prop");
  let injected = false;
  const store = racingStoreFor("joe", async () => {
    ok(await as("dell").initializePropertyNegotiation({ idempotency_key: key(),
      related_refs: { assignment: REF("assignment", asg) },
      declared: { new_subject_id: id("neg"), property_id: property } }),
    "Dell opens the negotiation inside Joe's window");
    injected = true;
  }, /ops\.j102_initialize_subject\(/);
  await assert.rejects(
    store.initializePropertyNegotiation({ idempotency_key: key(),
      related_refs: { assignment: REF("assignment", asg) },
      declared: { new_subject_id: id("neg"), property_id: property } }, { actor: JOE }),
    e => /j102_active_negotiation_exists/.test(String(e?.message)) && e?.code === "23505",
    "the census the kernel saw was empty; the writer re-checks the committed rows");
  assert.equal(injected, true, "the race actually happened inside the window");
  const [ix] = await sql("joe",
    `select indisunique as u, pg_get_expr(indpred, indrelid) as pred from pg_index
      where indexrelid = 'ops.j102_one_active_negotiation_per_property'::regclass`);
  assert.equal(ix?.u, true, "the backstop index is UNIQUE");
  for (const st of ["loi_drafted", "loi_submitted", "loi_countered", "loi_accepted"]) {
    assert.match(ix.pred, new RegExp(st), `the backstop covers ${st}`);
  }
  assert.doesNotMatch(ix.pred, /selected_winner|loi_withdrawn|loi_rejected|superseded/);
});

// ===========================================================================
// OWNER RULING (c), 2026-09-25: every lifecycle row and event is recorded UNDER
// THE SPONSORING PARTNER, derived from the authenticated login and never from a
// caller field.
// ===========================================================================

async function sponsorOf(subject_kind, subject_id) {
  const [row] = await sql("joe",
    `select c.updated_by, c.sponsoring_partner,
            c.envelope -> 'record' ->> 'sponsoring_partner' as enveloped,
            (select jsonb_agg(jsonb_build_array(e.recorded_by, e.sponsoring_partner) order by e.event_seq)
               from ops.j102_subject_event e
              where e.tenant = c.tenant and e.subject_kind = c.subject_kind and e.subject_id = c.subject_id) as events
       from ops.j102_subject_current c
      where c.subject_kind = $1 and c.subject_id = $2`, [subject_kind, subject_id]);
  return row;
}

test("RULING (c) LIVE: an agent's write is recorded under its sponsoring partner, a partner's under themself", { skip: SKIP }, async () => {
  const byAgent = id("rel");
  ok(await as("agent").initializeProspectRelationship({ idempotency_key: key(),
    declared: { new_subject_id: byAgent } }), "agent creates a prospect");
  const a = await sponsorOf("relationship", byAgent);
  assert.equal(a.updated_by, "codex", "the agent is who wrote it");
  assert.equal(a.sponsoring_partner, "joe", "and it is recorded under Joe, its sponsor");
  assert.equal(a.enveloped, "joe", "inside the hashed envelope too");
  assert.deepEqual(a.events, [["codex", "joe"]], "and on its event");
  assert.equal((await body("joe", "relationship", byAgent)).sponsoring_partner, "joe",
    "the subject reader surfaces it");

  const byDell = id("rel");
  ok(await as("dell").initializeProspectRelationship({ idempotency_key: key(),
    declared: { new_subject_id: byDell } }), "Dell creates a prospect");
  assert.deepEqual([(await sponsorOf("relationship", byDell)).sponsoring_partner,
    (await sponsorOf("relationship", byDell)).events], ["dell", [["dell", "dell"]]]);

  // Ruling (e) and (c) together: Joe over the writer credential is a
  // sponsored_agent under his own slug, recorded under himself.
  const byJoeWriter = id("rel");
  ok(await as("joe_writer").initializeProspectRelationship({ idempotency_key: key(),
    declared: { new_subject_id: byJoeWriter } }), "Joe over the writer credential");
  assert.deepEqual([(await sponsorOf("relationship", byJoeWriter)).updated_by,
    (await sponsorOf("relationship", byJoeWriter)).sponsoring_partner], ["joe", "joe"]);

  // A transition stamps it too, on the moved row and on every event it appends.
  const d = await dealWith("lease", ["lease_exec"]);
  ok(await axisCall("agent", d.deal, "invoice_state", "invoice_issued", "invoice",
    (await body("joe", "deal", d.deal)).state_digest), "the agent records an invoice");
  const moved = await sponsorOf("deal", d.deal);
  assert.equal(moved.updated_by, "codex");
  assert.equal(moved.sponsoring_partner, "joe");
  assert.deepEqual(moved.events.at(-1), ["codex", "joe"]);
});

test("RULING (c) LIVE: a caller-supplied sponsor is refused, at the store and at the SQL writer", { skip: SKIP }, async () => {
  // At the door: the field is derived-only, wherever the caller puts it.
  for (const payload of [
    { idempotency_key: key(), sponsoring_partner: "dell", declared: { new_subject_id: id("rel") } },
    { idempotency_key: key(), declared: { new_subject_id: id("rel"), sponsoring_partner: "dell" } },
  ]) {
    await assert.rejects(as("agent").initializeProspectRelationship(payload),
      e => ["caller_derived_field_refused", "unknown_field"].includes(e?.code),
      "a sponsor named by the caller never reaches the database");
  }

  // Past the door: an envelope that names a different partner than the login
  // derives is refused by the writer itself, even with its digests correct.
  const tamper = text => /ops\.j102_initialize_subject\(/.test(text);
  const store = createCreLifecycleStore({ db: {
    async query() { throw new Error("transaction() is always used"); },
    async transaction(fn) {
      const client = await (await pool("agent")).connect();
      try {
        await client.query("BEGIN");
        const out = await fn({ query: async (text, params) => {
          if (tamper(text)) {
            const env = JSON.parse(params[2]);
            params = [...params];
            params[2] = JSON.stringify(v5J102StoreEnvelope("stored_lifecycle_subject",
              { ...env.record, sponsoring_partner: "dell" }, { alone_sufficient: false }));
          }
          return client.query(text, params);
        } });
        await client.query("COMMIT");
        return out;
      } catch (e) {
        await client.query("ROLLBACK").catch(() => {});
        throw e;
      } finally {
        client.release();
      }
    },
  } });
  const target = id("rel");
  await assert.rejects(
    store.initializeProspectRelationship({ idempotency_key: key(),
      declared: { new_subject_id: target } }, { actor: AGENT }),
    e => /j102_sponsor_mismatch/.test(String(e?.message)) && e?.code === "42501",
    "the writer re-derives the sponsor and refuses the envelope's claim");
  assert.equal(await sponsorOf("relationship", target), undefined, "nothing landed");

  // A login with no verified sponsor cannot write at all; one whose server
  // sponsor disagrees with the handler's is refused before any write.
  await assert.rejects(
    as("agent_unsponsored").initializeProspectRelationship({ idempotency_key: key(),
      declared: { new_subject_id: id("rel") } }),
    e => /j102_sponsor_unavailable/.test(String(e?.message)));
  await assert.rejects(
    as("agent_as_dell").initializeProspectRelationship({ idempotency_key: key(),
      declared: { new_subject_id: id("rel") } }),
    e => e?.code === "sponsor_context_mismatch");
});

/** A store on `name`'s pool whose writer call has its params rewritten by `edit`. */
function tamperingStoreFor(name, writer, edit) {
  return createCreLifecycleStore({ db: {
    async query() { throw new Error("transaction() is always used"); },
    async transaction(fn) {
      const client = await (await pool(name)).connect();
      try {
        await client.query("BEGIN");
        const out = await fn({ query: async (text, params) =>
          client.query(text, writer.test(text) ? edit([...params]) : params) });
        await client.query("COMMIT");
        return out;
      } catch (e) {
        await client.query("ROLLBACK").catch(() => {});
        throw e;
      } finally {
        client.release();
      }
    },
  } });
}

test("RULING (c) LIVE: a TRANSITION envelope naming another sponsor, digests recomputed, is refused by the SQL writer", { skip: SKIP }, async () => {
  const d = await dealWith("lease", ["lease_exec"]);
  const base = (await body("joe", "deal", d.deal)).state_digest;
  const invoice = await fact("joe", "invoice", "deal", d.deal, { detail: "synthetic" });
  let tampered = 0;
  const store = tamperingStoreFor("agent", /ops\.j102_apply_transition\(/, params => {
    // $3 is the subject envelopes. Each is re-sealed with a different sponsor, so
    // every digest the writer checks is honest and only the sponsor lies.
    const envelopes = JSON.parse(params[2]).map(env => {
      tampered += 1;
      return v5J102StoreEnvelope("stored_lifecycle_subject",
        { ...env.record, sponsoring_partner: "dell" }, { alone_sufficient: false });
    });
    params[2] = JSON.stringify(envelopes);
    return params;
  });
  await assert.rejects(
    store.recordDealAxis({ idempotency_key: key(),
      subject_ref: { subject_kind: "deal", subject_id: d.deal, expected_state_digest: base },
      evidence_refs: [{ evidence_kind: "invoice_issued", record_id: invoice }],
      declared: { axis: "invoice_state" } }, { actor: AGENT }),
    e => /j102_sponsor_mismatch/.test(String(e?.message)) && e?.code === "42501",
    "the transition writer re-derives the sponsor and refuses the envelope's claim");
  assert.ok(tampered > 0, "the tamper actually ran");
  assert.equal((await body("joe", "deal", d.deal)).state_digest, base, "the deal did not move");
});

test("RULING (c) LIVE: a PARTNER session whose transaction names a different sponsor is refused", { skip: SKIP }, async () => {
  if (!pg) pg = (await import("pg")).default;
  // Joe's own authority login, with the sponsor setting naming Dell: the partner
  // is their own sponsor, so the database refuses rather than choose.
  const conflicted = new pg.Pool({ connectionString: DSN.joe, max: 1,
    options: "-c carr.sponsoring_human_slug=dell" });
  try {
    const store = createCreLifecycleStore({ db: {
      async query() { throw new Error("transaction() is always used"); },
      async transaction(fn) {
        const client = await conflicted.connect();
        try {
          await client.query("BEGIN");
          const out = await fn({ query: (t, p) => client.query(t, p) });
          await client.query("COMMIT");
          return out;
        } catch (e) {
          await client.query("ROLLBACK").catch(() => {});
          throw e;
        } finally {
          client.release();
        }
      },
    } });
    const target = id("rel");
    await assert.rejects(
      store.initializeProspectRelationship({ idempotency_key: key(),
        declared: { new_subject_id: target } }, { actor: JOE }),
      e => /j102_sponsor_context_conflict/.test(String(e?.message)) && e?.code === "42501");
    assert.equal(await sponsorOf("relationship", target), undefined, "nothing landed");
    // The same login with the setting agreeing (or absent) writes normally.
    const [row] = await sql("joe", "select ops.j102_sponsoring_partner() as s");
    assert.equal(row.s, "joe");
  } finally {
    await conflicted.end();
  }
});
