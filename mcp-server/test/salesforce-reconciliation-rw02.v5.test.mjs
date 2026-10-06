// V5-RW02 — attended Salesforce reconciliation and per-action evaluation,
// proved clause by clause against the reviewed catalog's `checkable_done` and
// the eight mapped decisions, plus every attack the independent review of
// PR 1238 ran against the first head (its probes are reproduced here as tests).
//
// SYNTHETIC DATA ONLY. Every origin is under the reserved `.invalid` TLD, every
// opportunity id is a made-up 006-prefixed id, and every case, deal and party
// ref is a placeholder. No real client, deal, org or person appears here.
//
// The F06 answers are REAL: envelopes, capabilities and presentations are built
// through workflow-effect-envelope.v5.js and the actor authority through
// global-boundaries.v5.js, so a drift in either breaks this suite rather than
// the wiring rotting silently.
//
//   node --test mcp-server/test/salesforce-reconciliation-rw02.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { digest } from "../src/artifact-trust.js";
import { evaluateActorAuthority, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  normalizeEffectAttemptObservation,
  normalizeEffectCapability,
  normalizeEffectEnvelope,
  normalizeEffectPresentation,
} from "../src/workflow-effect-envelope.v5.js";
import * as rw02 from "../src/salesforce-reconciliation-rw02.v5.js";

const {
  V5RW02Error,
  V5_RW02_ACTION_KIND_KEYS,
  V5_RW02_CAPABILITY_LIFETIME_CEILING_SECONDS,
  V5_RW02_CREDENTIAL_PATTERNS,
  V5_RW02_FIELD_MAP_SEAM,
  V5_RW02_OPEN_OBLIGATIONS,
  V5_RW02_DECISION_IDS,
  V5_RW02_EXCLUDED_ACTIONS,
  V5_RW02_PAGE_CHECKS,
  V5_RW02_PUBLIC_SURFACE,
  V5_RW02_RUNTIME_EVIDENCE_INPUTS,
  V5_RW02_SEAMS,
  V5_RW02_SETTLED_DECISIONS,
  V5_RW02_EVALUATION_WINDOW_SEAM,
  V5_RW02_EVIDENCE_STORE_SEAM,
  buildActionPreview,
  evaluateActionAdmission,
  evaluateActionTrustWindow,
  evaluateDuplicateSearch,
  evaluatePageObservation,
  evaluateResume,
  evaluateWriteReadback,
  rw02ConfirmationFreshnessBound,
  rw02StepKey,
  rw02WorkflowId,
  v5Rw02PolicyDigest,
  v5Rw02Projection,
} = rw02;

const SRC = fileURLToPath(new URL("../src/salesforce-reconciliation-rw02.v5.js", import.meta.url));
const TOOLS = fileURLToPath(new URL("../src/tools.js", import.meta.url));

const T = ORGANIZATION_TENANT_ID;
const ORIGIN = "https://synthetic-org.invalid";
const OTHER_ORIGIN = "https://other-org.invalid";
const ORG = "00Dsynthetic0001";
const OTHER_ORG = "00Dsynthetic0002";
const SEAT = "sf-seat-synthetic-partner";
const OTHER_SEAT = "sf-seat-synthetic-other";
const UI = "sha256:" + "1".repeat(64);
const OPP = "006SYNTH0000001";
const OPP2 = "006SYNTH0000002";
const OPP3 = "006SYNTH0000003";
const NOW = "2026-09-24T12:00:00Z";
const ISSUED = "2026-09-24T11:58:00Z";
const EXPIRES = "2026-09-24T12:08:00Z";
const CONFIRMED = "2026-09-24T11:59:00Z";

const CASE = Object.freeze({ workflow_ref: "rw02-case-synthetic-1", deal_ref: "deal-synthetic-1",
  engagement_ref: "engagement-synthetic-1" });
const CASE2 = Object.freeze({ workflow_ref: "rw02-case-synthetic-2", deal_ref: "deal-synthetic-2" });
const BINDING = Object.freeze({ origin: ORIGIN, org_id: ORG, account_ref: SEAT });
const CEILING_SEAM = "step:v5-f06-capability-lifetime-ceiling";


const TARGETED = new Set(["opportunity_phase_update", "opportunity_link_record", "etl_document_prepare",
  "commission_agreement_prepare"]);

const isErr = code => e => e instanceof V5RW02Error && e.code === code;

// ------------------------------------------------------------------ fixtures

const page = (obs = {}, bind = {}, mode = "attended") => ({
  execution_mode: mode,
  binding: {
    expected_origin: ORIGIN, expected_org_id: ORG, expected_account_ref: SEAT,
    expected_ui_contract_digest: UI, ...bind,
  },
  observation: {
    origin: ORIGIN, org_id: ORG, signed_in_account_ref: SEAT, ui_contract_digest: UI,
    challenge: "none", result_consistency: "consistent", ...obs,
  },
});
/** A self-consistent page on a different org, origin or seat. */
const pageOn = ({ origin = ORIGIN, org = ORG, seat = SEAT, record = undefined } = {}) => page(
  { origin, org_id: org, signed_in_account_ref: seat, ...(record ? { record_id: record } : {}) },
  { expected_origin: origin, expected_org_id: org, expected_account_ref: seat,
    ...(record ? { expected_record_id: record } : {}) });
const pinned = (record = OPP) => pageOn({ record });

const stepKey = (action_kind, intent_ordinal = 1, kase = CASE) =>
  rw02StepKey({ tenant: T, case: kase, action_kind, intent_ordinal });

const field = (name, value, semantics = "ordinary", provenance = "partner_entered",
  fact_class = "corporate_transaction_field") => ({ field: name, value, semantics, provenance, fact_class });

const CREATE_FIELDS = Object.freeze([
  field("Name", "Synthetic Clinic Lease"),
  field("StageName", "Research", "phase"),
  field("Out_of_Market_Deal__c", false, "out_of_market_flag"),
  field("Total_Commission__c", 1000, "commission_placeholder"),
  field("CloseDate", "2027-01-01", "close_date_placeholder"),
]);
const DOC_FIELDS = Object.freeze([field("Template_Ref__c", "etl-template-synthetic", "ordinary", "doctorcre_record")]);
const LINK_FIELDS = Object.freeze([field("salesforce_id", OPP, "external_id_link", "salesforce_observed", "operating_fact")]);

const defaultFields = kind => (kind === "opportunity_create" ? CREATE_FIELDS
  : kind === "opportunity_link_record" ? LINK_FIELDS
    : kind === "opportunity_phase_update" ? [field("StageName", "Negotiation", "phase")] : DOC_FIELDS);

const req = (action_kind = "opportunity_create", over = {}) => ({
  tenant: T, action_kind, case: CASE, intent_ordinal: 1, org_binding: { ...BINDING },
  ...(TARGETED.has(action_kind) ? { target_opportunity_id: OPP } : {}),
  fields: defaultFields(action_kind).map(f => ({ ...f })), ...over,
});
const preview = (action_kind, over) => buildActionPreview(req(action_kind, over));

const dupSearch = (candidates = [], completeness = "complete", over = {}) => ({
  tenant: T, case: CASE, intent_ordinal: 1, page: page(), search: { completeness, candidates }, ...over,
});

const PRINCIPALS = Object.freeze({
  actor_slug: "joe", account_ref: SEAT, deal_owner_slug: "joe", signer_slug: "joe",
});
const ACTION = "business.update_deal";
const CAPNAME = "deal.write";

const envelopeFor = (p, over = {}) => normalizeEffectEnvelope({
  tenant: T, envelope_id: "env-synthetic-1", workflow_id: rw02WorkflowId(p.preview.case),
  step_id: p.preview.step_key, action: ACTION, principals: { ...PRINCIPALS },
  payload_digest: p.preview_digest, caps: [], autonomy_tier_label: "tier-attended", ...over,
});
const capabilityFor = (env, over = {}) => normalizeEffectCapability({
  tenant: T, capability_id: "cap-synthetic-1", envelope_digest: env.envelope_digest, capability: CAPNAME,
  nonce: "nonce-synthetic-1", principals: { ...env.principals }, autonomy_tier_label: "tier-attended",
  issued_at: ISSUED, expires_at: EXPIRES, ...over,
});
const presentationFor = (env, over = {}) => normalizeEffectPresentation({
  presentation_id: "pres-synthetic-1", envelope_digest: env.envelope_digest, capability_id: "cap-synthetic-1",
  requested_action: ACTION, payload_digest: env.payload_digest, presented_principals: { ...env.principals },
  presented_autonomy_tier_label: "tier-attended", nonce_state: "unconsumed", capability_state: "active",
  cap_observations: [], ...over,
});
const authority = () => evaluateActorAuthority({
  actor: Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: null,
    sponsoring_human_slug: null, human_slug: null, sponsor_required: false }),
  action: ACTION, tenant: T, now: NOW,
  controls: { deal_owner_slug: "joe", account_slug: "joe", policy_scope: [ACTION], capabilities: [CAPNAME] },
});

const confirmationFor = (p, over = {}) => ({
  confirmation_ref: "confirm-synthetic-1", confirmed_at: CONFIRMED, confirmed_by: "joe",
  preview_digest: p.preview_digest, action_kind: p.preview.action_kind, step_key: p.preview.step_key, ...over,
});

/** Admission over a preview REQUEST; the envelope is sealed over the rebuilt preview. */
const admission = (r = req(), over = {}, f06over = {}) => {
  // A refused preview has no digest to seal; the envelope then comes from a
  // valid one, because admission must refuse on the preview before reading it.
  const built = buildActionPreview(r);
  const p = built.decision === "preview_ready" ? built : buildActionPreview(req());
  const env = f06over.envelope ?? envelopeFor(p);
  const targeted = TARGETED.has(r.action_kind);
  return evaluateActionAdmission({
    tenant: T, execution_mode: "attended", page: targeted ? pinned(r.target_opportunity_id) : page(),
    preview_request: r,
    ...(r.action_kind === "opportunity_create" ? { duplicate_search: dupSearch() } : {}),
    ...(targeted ? { target_readback: { opportunity_id: r.target_opportunity_id, state: "present" } } : {}),
    confirmation: confirmationFor(p),
    f06: {
      envelope: env, capability: f06over.capability ?? capabilityFor(env),
      presentation: f06over.presentation ?? presentationFor(env), authority: authority(), now: NOW,
    },
    ...over,
  });
};

const attemptFor = (env, over = {}) => normalizeEffectAttemptObservation({
  attempt_id: "att-synthetic-1", envelope_digest: env.envelope_digest, capability_id: "cap-synthetic-1",
  consumption: { state: "committed", committed_seq: 1 }, provider_call: { state: "started", started_seq: 2 },
  outcome: { state: "succeeded" }, ...over,
});

const readbackOf = (p, overrides = {}, opp = OPP, step_marker = "present") => ({
  opportunity_id: opp, completeness: "complete", step_marker,
  fields: [...p.preview.fields.map(f => ({ field: f.field, value: f.field in overrides ? overrides[f.field] : f.value })),
    { field: "LastModifiedDate", value: "2026-09-24T12:00:01Z" }],
});

const readback = (r = req(), over = {}, attemptOver = {}) => {
  const p = buildActionPreview(r);
  const env = envelopeFor(p);
  return evaluateWriteReadback({
    tenant: T, page: TARGETED.has(r.action_kind) ? pinned(r.target_opportunity_id) : page(),
    preview_request: r, evidence_class: "fixture", observed_at: NOW,
    f06: { envelope: env, capability: capabilityFor(env), attempt: attemptFor(env, attemptOver) },
    provider_readback: readbackOf(p), ...over,
  });
};

function assertGrantsNothing(answer) {
  assert.equal(answer.outward_effect_granted, false);
  assert.equal(answer.autonomy_active, false);
  assert.equal(answer.attended_activation_receipt_present, false);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
}

/** Throws with `code`, and neither the message nor the detail carries `secret`. */
function assertThrowsQuietly(fn, code, secret) {
  let caught;
  try { fn(); } catch (e) { caught = e; }
  assert.ok(caught instanceof V5RW02Error, "expected a V5RW02Error");
  assert.equal(caught.code, code);
  const said = caught.message + JSON.stringify(caught.detail ?? null);
  assert.ok(!said.includes(secret), `error echoed the caller's value: ${code}`);
}

// ---------------------------------------------------------------------------
// Contract identity.
// ---------------------------------------------------------------------------

test("the eight mapped decisions are exactly the catalog's, each with verbatim text and a digest", () => {
  assert.deepEqual([...V5_RW02_DECISION_IDS],
    ["Q059.D5", "Q070.D1", "Q084.D1", "Q097.D1", "Q098.D1", "Q099.D1", "Q100.D1", "Q123.D4"]);
  for (const id of V5_RW02_DECISION_IDS) {
    assert.match(V5_RW02_SETTLED_DECISIONS[id].source_evidence_digest, /^[0-9a-f]{64}$/, id);
    assert.ok(V5_RW02_SETTLED_DECISIONS[id].settled_requirement.length > 40, id);
  }
  assert.match(V5_RW02_SETTLED_DECISIONS["Q084.D1"].settled_requirement, /immediate stops on authentication/);
  assert.match(V5_RW02_SETTLED_DECISIONS["Q099.D1"].settled_requirement, /never use global trust/);
});

test("exports equal the declared public surface exactly", () => {
  assert.deepEqual(Object.keys(rw02).sort(), [...V5_RW02_PUBLIC_SURFACE].sort());
});

test("the projection grants nothing, lists every runtime input as missing and names every seam", () => {
  const p = v5Rw02Projection();
  assertGrantsNothing(p);
  assert.deepEqual(p.runtime_inputs_missing, [...V5_RW02_RUNTIME_EVIDENCE_INPUTS]);
  assert.ok(p.runtime_inputs_missing.includes("step:journey-three-production-outcome"));
  assert.deepEqual(p.seams_owed, [...V5_RW02_SEAMS]);
  assert.ok(p.seams_owed.includes(CEILING_SEAM));
  assert.equal(p.confirmation_freshness_bounded, false);
  assert.equal(p.adapter_admission.admitted, false);
  assert.equal(p.adapter_admission.credential_enters_model_or_context, false);
  assert.deepEqual(p.source_build_dependencies, ["V5-F01", "V5-F06", "V5-J301"]);
  assert.equal(p.policy_digest, v5Rw02PolicyDigest());
});

test("the module is pure: no filesystem, network, environment, clock or dynamic import", () => {
  const src = readFileSync(SRC, "utf8");
  for (const banned of [/\bfetch\s*\(/, /from\s+["']node:/, /process\.env/, /Date\.now\s*\(/,
    /new Date\s*\(\s*\)/, /\bimport\s*\(/, /require\s*\(/]) {
    assert.doesNotMatch(src, banned, String(banned));
  }
});

// ---------------------------------------------------------------------------
// checkable_done 1 — auth challenge / UI drift / unexpected recipient / policy
// conflict stop safely (Q084.D1, Q100.D1).
// ---------------------------------------------------------------------------

test("CD1 clean page: an attended, bound, unchallenged page continues, reports its binding, grants nothing", () => {
  const a = evaluatePageObservation(page());
  assert.equal(a.decision, "continue");
  assert.deepEqual(a.verified_binding, { origin: ORIGIN, org_id: ORG, account_ref: SEAT, record_id: null });
  assertGrantsNothing(a);
});

const STOP_CASES = [
  ["unattended execution", page({}, {}, "unattended"), "unattended_execution_excluded", "execution_mode"],
  ["login challenge", page({ challenge: "login_required" }), "authentication_challenge", "authentication_challenge"],
  ["MFA challenge", page({ challenge: "mfa_challenge" }), "authentication_challenge", "authentication_challenge"],
  ["CAPTCHA", page({ challenge: "captcha" }), "authentication_challenge", "authentication_challenge"],
  ["expired session", page({ challenge: "session_expired" }), "authentication_challenge", "authentication_challenge"],
  ["unstated challenge", page({ challenge: "unstated" }), "challenge_state_unobservable", "authentication_challenge"],
  ["look-alike origin", page({ origin: "https://synthetic-org.invalid.attacker.invalid" }), "origin_mismatch", "origin"],
  ["origin with a path", page({ origin: ORIGIN + "/x" }), "origin_mismatch", "origin"],
  ["other org", page({ org_id: OTHER_ORG }), "org_mismatch", "org"],
  ["other signed-in account", page({ signed_in_account_ref: OTHER_SEAT }), "signed_in_account_mismatch", "signed_in_account"],
  ["other record", page({ record_id: OPP2 }, { expected_record_id: OPP }), "record_mismatch", "record"],
  ["UI drift", page({ ui_contract_digest: "sha256:" + "2".repeat(64) }), "ui_drift", "ui_contract"],
  ["unexpected recipient", page({ recipients: ["party-a", "party-z"] }, { expected_recipients: ["party-a"] }),
    "unexpected_recipient", "recipients"],
  ["policy conflict", page({ policy_conflicts: ["rule-synthetic-1"] }), "policy_conflict", "policy_conflict"],
  ["inconsistent result", page({ result_consistency: "inconsistent" }), "inconsistent_result", "result_consistency"],
  ["unstated consistency", page({ result_consistency: "unstated" }), "result_consistency_unobservable", "result_consistency"],
];

for (const [name, request, reason, check] of STOP_CASES) {
  test(`CD1 stop: ${name} stops with no bypass and no automatic retry`, () => {
    const a = evaluatePageObservation(request);
    assert.equal(a.decision, "stop");
    assert.equal(a.reason_id, reason);
    assert.equal(a.blocking_check, check);
    assert.equal(a.bypass_permitted, false);
    assert.equal(a.automatic_retry_permitted, false);
    assert.equal(a.resolution_owner, "partner_at_the_browser");
    assertGrantsNothing(a);
  });
}

test("CD1 ordering: a challenge is decided before any binding is compared", () => {
  const a = evaluatePageObservation(page({ challenge: "mfa_challenge", org_id: OTHER_ORG }));
  assert.equal(a.reason_id, "authentication_challenge");
  assert.deepEqual(a.checks_required, [...V5_RW02_PAGE_CHECKS]);
});

test("CD1 a stop on the page stops the duplicate search, the admission and the readback", () => {
  const d = evaluateDuplicateSearch(dupSearch([], "complete", { page: page({ challenge: "captcha" }) }));
  assert.equal(d.decision, "stop");
  assert.equal(d.reason_id, "authentication_challenge");
  const a = admission(req(), { page: page({ ui_contract_digest: "sha256:" + "3".repeat(64) }) });
  assert.equal(a.decision, "stop");
  assert.equal(a.reason_id, "ui_drift");
  assert.equal(a.blocking_check, "page");
  const r = readback(req(), { page: page({ challenge: "mfa_challenge" }) });
  assert.equal(r.decision, "stop");
  assert.equal(r.reason_id, "authentication_challenge");
  assert.equal(r.evidence.outcome, "stopped");
});

test("CD1 unattended is refused at the admission door, whether the request or the page says so", () => {
  const byRequest = admission(req(), { execution_mode: "unattended" });
  assert.equal(byRequest.reason_id, "unattended_execution_excluded");
  assert.equal(byRequest.blocking_check, "execution_mode");
  const byPage = admission(req(), { page: page({}, {}, "unattended") });
  assert.equal(byPage.decision, "stop");
  assert.equal(byPage.reason_id, "unattended_execution_excluded");
  assert.equal(byPage.blocking_check, "execution_mode");
});

// ---------------------------------------------------------------------------
// Data boundary: credentials never enter, and errors never echo.
// ---------------------------------------------------------------------------

const CREDENTIAL_SAMPLES = {
  jwt_anywhere: "see eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl here",
  dotted_triple: "abcdefghij.klmnopqrst.uvwxyzabcd",
  pem_block: "note -----BEGIN KEY",
  secret_pair: "password=hunter2-synthetic",
  bearer: "Bearer abcdef123456",
  salesforce_session_id: "sid 00Dxx0000001gPL!AR8AQJXg5vYzP.qhbZrs4r1D8pX0WQ",
  api_key: "key sk-ant-api03-abcdefghijklmnopq", // synthetic, not a key; ci-secret-scan: allow
  basic_auth: "Authorization: Basic dXNlcjpzeW50aGV0aWM=",
  salesforce_session_id_url_encoded: "sid%3D00Dxx0000001gPL%21AR8AQJXg5vYzP.qhbZrs4r1D8pX0WQ",
  salesforce_refresh_token: "rt 5Aep861SYNTHETICxxxxxxxxxxxxxxxxxxxxxx", // synthetic; ci-secret-scan: allow
  aws_access_key_id: "key AKIASYNTHETIC00000XY", // synthetic, not a key; ci-secret-scan: allow
  github_token: "ghp_SYNTHETICsyntheticSYNTHETICsynthetic0", // synthetic; ci-secret-scan: allow
};

test("data boundary: sid= is a secret pair again, and prose is not a credential", () => {
  assert.ok(V5_RW02_CREDENTIAL_PATTERNS.secret_pair.test("sid=abc123"));
  assert.ok(V5_RW02_CREDENTIAL_PATTERNS.secret_pair.test("SID: abc123"));
  for (const prose of ["consider: the lane", "Basic understanding of the lane", "basic terms agreed 2026",
    "AKIA short", "gh_pages", "Basic Understandings", "basic understandings2", "BASIC UNDERSTANDING2"]) {
    assert.equal(Object.entries(V5_RW02_CREDENTIAL_PATTERNS).filter(([, re]) => re.test(prose)).length, 0, prose);
  }
});

test("data boundary: strings and keys inside the F06 objects are scanned before F06 sees them", () => {
  const secret = "00Dxx0000001gPL!AR8AQJXg5vYzP.qhbZrs4r1D8pX0WQ";
  const r = req();
  const p = buildActionPreview(r);
  const env = envelopeFor(p);
  assertThrowsQuietly(() => admission(r, {}, { capability: { ...capabilityFor(env), nonce: secret } }),
    "credential_shaped_value", secret);
  assertThrowsQuietly(() => admission(r, {}, { presentation: { ...presentationFor(env), note: ["ok", secret] } }),
    "credential_shaped_value", secret);
  assertThrowsQuietly(() => admission(r, {}, { presentation: { ...presentationFor(env), "password=x1": 1 } }),
    "credential_shaped_value", "password=x1");
  assertThrowsQuietly(() => readback(r, { f06: { envelope: env, capability: capabilityFor(env),
    attempt: { ...attemptFor(env), attempt_id: `att ${secret}` } } }), "credential_shaped_value", secret);
  let deep = "leaf";
  for (let i = 0; i < 20; i++) deep = { n: deep };
  assert.throws(() => admission(r, {}, { presentation: { ...presentationFor(env), note: deep } }),
    isErr("invalid_shape"));
});

test("data boundary: every credential shape is registered and has a sample only it catches", () => {
  assert.deepEqual(Object.keys(CREDENTIAL_SAMPLES).sort(), Object.keys(V5_RW02_CREDENTIAL_PATTERNS).sort());
  for (const [name, sample] of Object.entries(CREDENTIAL_SAMPLES)) {
    const hits = Object.entries(V5_RW02_CREDENTIAL_PATTERNS).filter(([, re]) => re.test(sample)).map(([k]) => k);
    assert.deepEqual(hits, [name], name);
  }
});

for (const [name, sample] of Object.entries(CREDENTIAL_SAMPLES)) {
  test(`data boundary: a field value carrying a ${name} throws without echoing it`, () => {
    assertThrowsQuietly(() => preview("opportunity_create", {
      fields: [...CREATE_FIELDS.map(f => ({ ...f })), field("Note__c", sample)],
    }), "credential_shaped_value", sample);
  });
}

test("data boundary: invisible or control characters are refused", () => {
  assert.throws(() => preview("opportunity_create", {
    fields: [...CREATE_FIELDS.map(f => ({ ...f })), field("Note__c", "zero​width")],
  }), isErr("unsafe_unicode"));
});

test("data boundary: unregistered values and unknown keys are refused without being echoed", () => {
  const secret = "00Dxx0000001gPL!AR8AQJXg5vYzP.qhbZrs4r1D8pX0WQ";
  assertThrowsQuietly(() => evaluatePageObservation(page({ challenge: secret })), "unknown_challenge_state", secret);
  assertThrowsQuietly(() => evaluateResume({ tenant: T, action_kind: "opportunity_create", case: CASE,
    intent_ordinal: 1, journal_hint: "Bearer abc.def", provider: { effect: "effect_absent" } }),
  "unknown_journal_hint", "Bearer abc");
  assertThrowsQuietly(() => evaluateResume({ tenant: T, action_kind: "sk-ant-api03-abcdef", case: CASE,
    intent_ordinal: 1, journal_hint: "absent", provider: { effect: "effect_absent" } }),
  "unknown_action_kind", "sk-ant");
  assertThrowsQuietly(() => evaluateResume({ tenant: T, action_kind: "opportunity_create", case: CASE,
    intent_ordinal: 1, journal_hint: "absent", provider: { effect: "effect_absent" }, "access_token=abc123": 1 }),
  "unknown_field", "access_token");
  assertThrowsQuietly(() => evaluatePageObservation({ ...page(), session_cookie: "x" }), "unknown_field",
    "session_cookie");
});

test("a case with no DoctorCRE anchor is unreadable", () => {
  assert.throws(() => preview("opportunity_create", { case: { workflow_ref: "rw02-case-unanchored" } }),
    isErr("case_unanchored"));
});

// ---------------------------------------------------------------------------
// checkable_done 2a — duplicate detection (Q098.D1, never auto-merge on a name).
// ---------------------------------------------------------------------------

const cand = (opportunity_id, over = {}) => ({ opportunity_id, step_marker: "absent", name_match: "none", ...over });

test("CD2 duplicates: an empty complete search admits a create, pending preview and confirmation", () => {
  const d = evaluateDuplicateSearch(dupSearch([cand(OPP2)]));
  assert.equal(d.decision, "create_admissible");
  assert.equal(d.step_key, stepKey("opportunity_create"));
  assert.deepEqual(d.searched_on, { origin: ORIGIN, org_id: ORG, account_ref: SEAT, record_id: null });
  assertGrantsNothing(d);
});

test("CD2 duplicates: this step's marker on the provider means already effected — read back, never create", () => {
  const d = evaluateDuplicateSearch(dupSearch([cand(OPP, { step_marker: "present" }), cand(OPP2)]));
  assert.equal(d.decision, "already_effected");
  assert.equal(d.opportunity_id, OPP);
  assert.equal(d.required_next_step, "readback");
  assert.equal(d.create_permitted, false);
});

const link = (anchor_type, ref) => ({ linked_anchor: { anchor_type, ref } });

test("CD2 duplicates: an opportunity linked to ANY anchor of this case is linked, not duplicated", () => {
  for (const [type, ref] of [["deal", CASE.deal_ref], ["engagement", CASE.engagement_ref]]) {
    const d = evaluateDuplicateSearch(dupSearch([cand(OPP, link(type, ref))]));
    assert.equal(d.decision, "link_existing", type);
    assert.equal(d.create_permitted, false);
  }
  const full = { workflow_ref: "rw02-case-synthetic-9", prospect_ref: "p-9", engagement_ref: "e-9",
    assignment_ref: "a-9", deal_ref: "d-9" };
  for (const type of ["prospect", "engagement", "assignment", "deal"]) {
    const d = evaluateDuplicateSearch(dupSearch([cand(OPP, link(type, full[`${type}_ref`]))], "complete",
      { case: full }));
    assert.equal(d.decision, "link_existing", type);
  }
  const notOurs = evaluateDuplicateSearch(dupSearch([cand(OPP, link("deal", "deal-someone-else"))]));
  assert.equal(notOurs.decision, "create_admissible");
});

test("review N2: a link is typed, so an id equal to an anchor of another type is not a link", () => {
  const shared = { workflow_ref: "rw02-case-synthetic-8", prospect_ref: "shared-8" };
  for (const type of ["deal", "engagement", "assignment"]) {
    const d = evaluateDuplicateSearch(dupSearch([cand(OPP, link(type, "shared-8"))], "complete", { case: shared }));
    assert.equal(d.decision, "create_admissible", type);
  }
  assert.equal(evaluateDuplicateSearch(dupSearch([cand(OPP, link("prospect", "shared-8"))], "complete",
    { case: shared })).decision, "link_existing");
  assertThrowsQuietly(() => evaluateDuplicateSearch(dupSearch([cand(OPP, link("account", "shared-8"))])),
    "unknown_anchor_type", "account");
  assert.throws(() => evaluateDuplicateSearch(dupSearch([cand(OPP, { linked_ref: CASE.deal_ref })])),
    isErr("unknown_field"));
  assert.throws(() => evaluateDuplicateSearch(dupSearch([cand(OPP, { linked_anchor: { anchor_type: "deal" } })])),
    isErr("missing_field"));
  assert.throws(() => evaluateDuplicateSearch(dupSearch([cand(OPP,
    { linked_anchor: { anchor_type: "deal", ref: CASE.deal_ref, also: "prospect" } })])), isErr("unknown_field"));
});

test("review N4/N13: what the kernel cannot check is named, with its owner", () => {
  assert.equal(V5_RW02_OPEN_OBLIGATIONS.case_anchor_completeness.owner, "caller");
  assert.equal(V5_RW02_OPEN_OBLIGATIONS.placeholder_field_designation.owner, V5_RW02_FIELD_MAP_SEAM);
  assert.deepEqual(v5Rw02Projection().open_obligations, V5_RW02_OPEN_OBLIGATIONS);
  // N4 as behaviour: a prospect-only case cannot see the opportunity linked to its omitted deal.
  const omitted = { workflow_ref: "rw02-case-synthetic-1", prospect_ref: "p-1" };
  assert.equal(evaluateDuplicateSearch(dupSearch([cand(OPP, link("deal", CASE.deal_ref))], "complete",
    { case: omitted })).decision, "create_admissible");
  // N13 as behaviour: a commission field labelled ordinary is not held to placeholder rules.
  assert.equal(preview("opportunity_create", { fields: [...CREATE_FIELDS.map(f => ({ ...f })),
    field("Commission_Estimate__c", "12,000 est.")] }).decision, "preview_ready");
});

test("CD2 duplicates: a name match is a human question, never an automatic join", () => {
  for (const name_match of ["exact", "similar"]) {
    const d = evaluateDuplicateSearch(dupSearch([cand(OPP, { name_match }), cand(OPP2)]));
    assert.equal(d.decision, "human_disambiguation_required");
    assert.deepEqual(d.candidate_opportunity_ids, [OPP]);
    assert.equal(d.create_permitted, false);
  }
});

test("CD2 duplicates: incomplete searches, unknown markers and double markers stop", () => {
  for (const [request, detail] of [
    [dupSearch([], "truncated"), "duplicate_search_incomplete"],
    [dupSearch([], "unstated"), "duplicate_search_incomplete"],
    [dupSearch([cand(OPP, { step_marker: "unstated" })]), "step_marker_unobservable"],
    [dupSearch([cand(OPP, { step_marker: "present" }), cand(OPP2, { step_marker: "present" })]),
      "step_marker_on_multiple_opportunities"],
    [dupSearch([cand(OPP, link("deal", CASE.deal_ref)), cand(OPP2, link("engagement", CASE.engagement_ref))]),
      "case_linked_to_multiple_opportunities"],
  ]) {
    const d = evaluateDuplicateSearch(request);
    assert.equal(d.decision, "stop", detail);
    assert.equal(d.reason_id, "inconsistent_result");
    assert.equal(d.detail_reason, detail);
  }
});

// ---------------------------------------------------------------------------
// Preview rules (Q070.D1, Q097.D1, F01 homes, placeholder / lane / lifecycle).
// ---------------------------------------------------------------------------

test("preview: sealed over org binding and derived step key; placeholders labelled as not figures", () => {
  const p = preview("opportunity_create");
  assert.equal(p.decision, "preview_ready");
  assert.equal(p.step_key, stepKey("opportunity_create"));
  assert.deepEqual(p.preview.org_binding, { ...BINDING });
  assert.deepEqual(p.placeholder_fields, ["CloseDate", "Total_Commission__c"]);
  assert.equal(p.placeholders_are_figures, false);
  assert.equal(p.payload_digest_for_envelope, p.preview_digest);
  assert.equal(p.f06_action, "business.update_deal");
  // Field order does not change identity; a value, the org or the seat does.
  const reordered = preview("opportunity_create", { fields: [...CREATE_FIELDS].reverse().map(f => ({ ...f })) });
  assert.equal(reordered.preview_digest, p.preview_digest);
  for (const over of [
    { fields: CREATE_FIELDS.map(f => (f.field === "StageName" ? { ...f, value: "Negotiation" } : { ...f })) },
    { org_binding: { ...BINDING, org_id: OTHER_ORG } },
    { org_binding: { ...BINDING, origin: OTHER_ORIGIN } },
    { org_binding: { ...BINDING, account_ref: OTHER_SEAT } },
    { intent_ordinal: 2 },
  ]) {
    assert.notEqual(preview("opportunity_create", over).preview_digest, p.preview_digest);
  }
});

test("preview: a caller may not supply a step key, surface or digest — those are derived", () => {
  for (const extra of [{ step_key: "sha256:" + "a".repeat(64) }, { surface: "record_layer" },
    { preview_digest: "sha256:" + "b".repeat(64) }]) {
    assert.throws(() => preview("opportunity_create", extra), isErr("unknown_field"));
  }
});

test("preview: an inferred out-of-market lane is refused", () => {
  const p = preview("opportunity_create", {
    fields: CREATE_FIELDS.map(f => (f.semantics === "out_of_market_flag" ? { ...f, provenance: "inferred" } : { ...f })),
  });
  assert.equal(p.decision, "refuse");
  assert.equal(p.reason_id, "lane_inferred_not_authoritative");
});

test("preview: placeholders must have placeholder shape and never reach the record layer", () => {
  for (const [name, value] of [["Total_Commission__c", "1000"], ["CloseDate", "next spring"]]) {
    const p = preview("opportunity_create", {
      fields: CREATE_FIELDS.map(f => (f.field === name ? { ...f, value } : { ...f })),
    });
    assert.equal(p.reason_id, "placeholder_shape_invalid", name);
  }
  const toRecord = preview("opportunity_link_record", { fields: [...LINK_FIELDS.map(f => ({ ...f })),
    field("won_value", 999999, "commission_placeholder", "salesforce_observed", "operating_fact")] });
  assert.equal(toRecord.reason_id, "placeholder_is_not_a_figure");
});

test("preview: V5-F01 decides each field's home against the surface it is written to", () => {
  const opFact = preview("opportunity_create", {
    fields: [...CREATE_FIELDS.map(f => ({ ...f })), field("Deal_State__c", "pending", "ordinary",
      "doctorcre_record", "operating_fact")],
  });
  assert.equal(opFact.reason_id, "field_home_is_not_this_surface");
  assert.equal(opFact.f01_reason_id, "home_mismatch");
  assert.equal(opFact.f01_authoritative_home, "neon_record_layer");
  const corpOnRecord = preview("opportunity_link_record", {
    fields: [field("salesforce_id", OPP, "external_id_link", "salesforce_observed", "corporate_transaction_field")],
  });
  assert.equal(corpOnRecord.reason_id, "field_home_is_not_this_surface");
  assert.equal(corpOnRecord.f01_authoritative_home, "salesforce");
});

test("preview: Salesforce state never crosses into DoctorCRE lifecycle; the link carries only the external id", () => {
  const ok = preview("opportunity_link_record");
  assert.equal(ok.decision, "preview_ready");
  assert.equal(ok.record_layer_verb, "update-deal");
  for (const bad of [
    field("phase", "won", "phase", "inferred", "operating_fact"),
    field("payment_state", "paid", "ordinary", "salesforce_observed", "operating_fact"),
  ]) {
    const p = preview("opportunity_link_record", { fields: [...LINK_FIELDS.map(f => ({ ...f })), bad] });
    assert.equal(p.reason_id, "salesforce_state_is_not_doctorcre_lifecycle", bad.field);
  }
  const wrongId = preview("opportunity_link_record", {
    fields: [field("salesforce_id", OPP2, "external_id_link", "salesforce_observed", "operating_fact")],
  });
  assert.equal(wrongId.reason_id, "record_layer_link_carries_only_external_id");
  // Even an innocuous extra field is refused: the link carries the id and nothing else.
  const extra = preview("opportunity_link_record", { fields: [...LINK_FIELDS.map(f => ({ ...f })),
    field("notes_path", "notes/synthetic.md", "ordinary", "doctorcre_record", "operating_fact")] });
  assert.equal(extra.reason_id, "record_layer_link_carries_only_external_id");
  // Fields are sorted, so an extra field AFTER salesforce_id is the case only the
  // field count can catch.
  const trailing = preview("opportunity_link_record", { fields: [...LINK_FIELDS.map(f => ({ ...f })),
    field("sync_note", "synthetic", "ordinary", "doctorcre_record", "operating_fact")] });
  assert.equal(trailing.reason_id, "record_layer_link_carries_only_external_id");
  const wrongSemantics = preview("opportunity_link_record", {
    fields: [field("salesforce_id", OPP, "ordinary", "salesforce_observed", "operating_fact")] });
  assert.equal(wrongSemantics.reason_id, "record_layer_link_carries_only_external_id");
  const wrongName = preview("opportunity_link_record", {
    fields: [field("sf_opportunity_id", OPP, "external_id_link", "salesforce_observed", "operating_fact")] });
  assert.equal(wrongName.reason_id, "record_layer_link_carries_only_external_id");
  const noDeal = preview("opportunity_link_record", { case: { workflow_ref: "rw02-case-3", engagement_ref: "e-3" } });
  assert.equal(noDeal.reason_id, "record_layer_link_needs_deal");
});

test("preview: an external-id link is refused on the Salesforce surface", () => {
  const p = preview("opportunity_create", { fields: [...CREATE_FIELDS.map(f => ({ ...f })),
    field("Ext__c", OPP, "external_id_link", "salesforce_observed")] });
  assert.equal(p.reason_id, "external_id_link_is_record_layer_only");
});

test("preview: the link write names a deployed record-layer verb that accepts salesforce_id", () => {
  const tools = readFileSync(TOOLS, "utf8");
  const at = tools.indexOf('"update-deal": {');
  assert.ok(at > 0, "update-deal is registered");
  const block = tools.slice(at, at + 1500);
  assert.match(block, /salesforce_id/);
  assert.match(block, /required: \["idempotency_key","deal","base_version","fields"\]/);
});

test("preview: document preparation and phase moves require a target; a create may not name one", () => {
  assert.equal(preview("etl_document_prepare", { target_opportunity_id: null }).reason_id,
    "target_opportunity_required");
  assert.equal(preview("opportunity_create", { target_opportunity_id: OPP }).reason_id,
    "create_names_an_existing_target");
  assert.equal(preview("opportunity_phase_update", { fields: CREATE_FIELDS.map(f => ({ ...f })) }).reason_id,
    "phase_update_carries_exactly_one_phase_field");
});

test("protected sends are RW01's and are refused by name", () => {
  for (const kind of Object.keys(V5_RW02_EXCLUDED_ACTIONS)) {
    assert.throws(() => rw02StepKey({ tenant: T, case: CASE, action_kind: kind, intent_ordinal: 1 }),
      isErr("action_excluded_from_rw02"));
  }
});

// ---------------------------------------------------------------------------
// Per-action admission (Q059.D5, Q070.D1, Q100.D1 and every predicate's
// "distinct current exact-action capability ... atomically consumed" clause).
// ---------------------------------------------------------------------------

test("admission clean case: every RW02 check and the real F06 presentation pass, and it is unavailable until F06 bounds lifetime", () => {
  assert.equal(V5_RW02_CAPABILITY_LIFETIME_CEILING_SECONDS, null);
  for (const kind of V5_RW02_ACTION_KIND_KEYS) {
    const a = admission(req(kind));
    assert.equal(a.decision, "unavailable", kind);
    assert.equal(a.reason_id, "capability_lifetime_ceiling_unbound");
    assert.equal(a.blocking_check, "partner_confirmation");
    assert.equal(a.seam, CEILING_SEAM);
    assert.equal(a.action_kind, kind);
    assert.deepEqual(a.org_binding, { ...BINDING });
    assert.equal(a.idempotency_key, undefined);
    assert.deepEqual(a.seams_owed, [...V5_RW02_SEAMS]);
    assert.deepEqual(a.runtime_inputs_missing, [...V5_RW02_RUNTIME_EVIDENCE_INPUTS]);
    assert.equal(a.adapter_admission.admitted, false);
    assertGrantsNothing(a);
  }
});

test("review A1-A3: admission re-runs every preview rule on the request it is given", () => {
  const lifecycle = admission(req("opportunity_link_record", { fields: [...LINK_FIELDS.map(f => ({ ...f })),
    field("phase", "won", "phase", "inferred", "operating_fact")] }));
  assert.equal(lifecycle.decision, "refuse");
  assert.equal(lifecycle.reason_id, "preview_refused");
  assert.equal(lifecycle.preview_reason_id, "salesforce_state_is_not_doctorcre_lifecycle");
  const lane = admission(req("opportunity_create", {
    fields: CREATE_FIELDS.map(f => (f.semantics === "out_of_market_flag" ? { ...f, provenance: "inferred" } : { ...f })),
  }));
  assert.equal(lane.preview_reason_id, "lane_inferred_not_authoritative");
  assert.throws(() => admission(req("opportunity_create", { surface: "record_layer" })), isErr("unknown_field"));
  assert.throws(() => admission(req("opportunity_create", {
    fields: [...CREATE_FIELDS.map(f => ({ ...f })), field("Note__c", "password=hunter2")],
  })), isErr("credential_shaped_value"));
  assert.throws(() => evaluateActionAdmission({ tenant: T, execution_mode: "attended", page: page(),
    preview: {}, confirmation: {}, f06: {} }), isErr("unknown_field"));
});

test("review C1: the admission page must be the previewed origin, org and seat", () => {
  for (const [over, reason] of [
    [{ origin: OTHER_ORIGIN }, "binding_origin_not_previewed"],
    [{ org: OTHER_ORG }, "binding_org_not_previewed"],
    [{ seat: OTHER_SEAT }, "binding_account_not_previewed"],
  ]) {
    const a = admission(req(), { page: pageOn(over) });
    assert.equal(a.decision, "stop", reason);
    assert.equal(a.reason_id, reason);
    assert.equal(a.blocking_check, "page_binding");
  }
});

test("review C2: a targeted action's page must pin exactly the previewed record", () => {
  const other = admission(req("etl_document_prepare"), { page: pinned(OPP2) });
  assert.equal(other.reason_id, "record_not_pinned");
  const unpinned = admission(req("etl_document_prepare"), { page: page() });
  assert.equal(unpinned.reason_id, "record_not_pinned");
  assert.equal(unpinned.blocking_check, "page_binding");
});

test("review R5: the F06 account principal must be the previewed seat", () => {
  const r = req("etl_document_prepare", { org_binding: { ...BINDING, account_ref: OTHER_SEAT } });
  const a = admission(r, { page: pageOn({ seat: OTHER_SEAT, record: OPP }) });
  assert.equal(a.decision, "refuse");
  assert.equal(a.reason_id, "capability_for_other_account");
});

test("review B1-B2: the duplicate search must be for this case, this step and this org", () => {
  const otherCase = admission(req(), { duplicate_search: dupSearch([], "complete",
    { case: { workflow_ref: "rw02-case-synthetic-1", prospect_ref: "p-1" } }) });
  assert.equal(otherCase.reason_id, "duplicate_search_for_other_case");
  const otherStep = admission(req(), { duplicate_search: dupSearch([], "complete", { intent_ordinal: 2 }) });
  assert.equal(otherStep.reason_id, "duplicate_search_for_other_step");
  for (const over of [{ origin: OTHER_ORIGIN }, { org: OTHER_ORG }, { seat: OTHER_SEAT }]) {
    const onOther = admission(req(), { duplicate_search: dupSearch([], "complete", { page: pageOn(over) }) });
    assert.equal(onOther.reason_id, "duplicate_search_on_other_org", JSON.stringify(over));
  }
  const stopped = admission(req(), { duplicate_search: dupSearch([], "truncated") });
  assert.equal(stopped.decision, "stop");
  assert.equal(stopped.blocking_check, "duplicate_clearance");
});

test("admission: a create needs a clean duplicate search; other kinds may not carry one", () => {
  assert.equal(admission(req(), { duplicate_search: null }).reason_id, "duplicate_search_required_before_create");
  assert.equal(admission(req(), { duplicate_search: dupSearch([cand(OPP, { name_match: "similar" })]) }).reason_id,
    "create_not_admissible_after_duplicate_search");
  assert.throws(() => admission(req("etl_document_prepare"), { duplicate_search: dupSearch() }),
    isErr("unexpected_field"));
});

test("admission: a confirmation bound to another preview, action, step or actor refuses", () => {
  const r = req();
  const p = buildActionPreview(r);
  assert.equal(admission(r, { confirmation: confirmationFor(p, { preview_digest: "sha256:" + "9".repeat(64) }) }).reason_id,
    "confirmation_for_other_preview");
  assert.equal(admission(r, { confirmation: confirmationFor(p, { action_kind: "opportunity_phase_update" }) }).reason_id,
    "confirmation_for_other_action");
  assert.equal(admission(r, { confirmation: confirmationFor(p, { step_key: stepKey("opportunity_create", 2) }) }).reason_id,
    "confirmation_for_other_step");
  assert.equal(admission(r, { confirmation: confirmationFor(p, { confirmed_by: "dell" }) }).reason_id,
    "confirmation_by_other_actor");
});

test("review C3: a confirmation from the future or older than one capability lifetime refuses", () => {
  const r = req();
  const p = buildActionPreview(r);
  assert.equal(admission(r, { confirmation: confirmationFor(p, { confirmed_at: "2026-09-24T12:00:01Z" }) }).reason_id,
    "confirmation_after_presentation");
  assert.equal(admission(r, { confirmation: confirmationFor(p, { confirmed_at: "2025-09-24T11:59:00Z" }) }).reason_id,
    "confirmation_stale");
  // Exactly one lifetime (10 minutes) old is not refused as stale (it reaches the
  // unbound-ceiling answer); one second more is.
  assert.equal(admission(r, { confirmation: confirmationFor(p, { confirmed_at: "2026-09-24T11:50:00Z" }) }).reason_id,
    "capability_lifetime_ceiling_unbound");
  assert.equal(admission(r, { confirmation: confirmationFor(p, { confirmed_at: "2026-09-24T11:49:59Z" }) }).reason_id,
    "confirmation_stale");
});

test("review N1: a twenty-year capability cannot admit a ten-year-old confirmation", () => {
  const r = req();
  const p = buildActionPreview(r);
  const env = envelopeFor(p);
  const cap = capabilityFor(env, { issued_at: "2016-09-24T11:58:00Z", expires_at: "2036-09-24T11:58:00Z" });
  const a = admission(r, { confirmation: confirmationFor(p, { confirmed_at: "2016-09-24T12:00:00Z" }) },
    { envelope: env, capability: cap });
  assert.notEqual(a.decision, "admissible_pending_attended_runtime");
  assert.equal(a.decision, "unavailable");
  assert.equal(a.reason_id, "capability_lifetime_ceiling_unbound");
  assert.equal(a.seam, CEILING_SEAM);
  assertGrantsNothing(a);
});

test("confirmation freshness bound: unbound, over the ceiling, within it, and unreadable inputs", () => {
  assert.deepEqual({ ...rw02ConfirmationFreshnessBound(600_000, null) },
    { bounded: false, reason_id: "capability_lifetime_ceiling_unbound", seam: CEILING_SEAM });
  assert.deepEqual({ ...rw02ConfirmationFreshnessBound(600_001, 600) },
    { bounded: false, reason_id: "capability_lifetime_exceeds_ceiling", seam: CEILING_SEAM });
  assert.deepEqual({ ...rw02ConfirmationFreshnessBound(600_000, 600) }, { bounded: true, reason_id: null, seam: null });
  const twentyYears = Date.parse("2036-09-24T00:00:00Z") - Date.parse("2016-09-24T00:00:00Z");
  assert.equal(rw02ConfirmationFreshnessBound(twentyYears, 900).bounded, false);
  for (const bad of [0, -1, Number.NaN, "600000"]) {
    assert.throws(() => rw02ConfirmationFreshnessBound(bad, 600), isErr("invalid_shape"), String(bad));
  }
  for (const bad of [0, -5, 1.5, "600"]) {
    assert.throws(() => rw02ConfirmationFreshnessBound(600_000, bad), isErr("invalid_shape"), String(bad));
  }
});

test("admission: there is no batch or session confirmation to name", () => {
  const r = req();
  const p = buildActionPreview(r);
  assert.throws(() => admission(r, { confirmation: { ...confirmationFor(p), scope: "session" } }), isErr("unknown_field"));
  assert.throws(() => admission(r, { confirmation: { ...confirmationFor(p), covers_all_actions: true } }),
    isErr("unknown_field"));
});

test("admission: document preparation requires the opportunity to read back present first", () => {
  const r = req("etl_document_prepare");
  assert.equal(admission(r, { target_readback: undefined }).reason_id, "target_readback_required");
  assert.equal(admission(r, { target_readback: { opportunity_id: OPP, state: "absent" } }).reason_id,
    "target_opportunity_absent");
  assert.equal(admission(r, { target_readback: { opportunity_id: OPP, state: "indeterminate" } }).reason_id,
    "target_readback_indeterminate");
  assert.equal(admission(r, { target_readback: { opportunity_id: OPP3, state: "present" } }).reason_id,
    "record_mismatch");
});

test("review R1-R2: a capability for one action or case cannot be presented for another", () => {
  const etl = buildActionPreview(req("etl_document_prepare"));
  const env = envelopeFor(etl);
  const f06 = { envelope: env, capability: capabilityFor(env), presentation: presentationFor(env) };
  assert.equal(admission(req("commission_agreement_prepare"), {}, f06).reason_id, "envelope_payload_is_not_this_preview");
  assert.equal(admission(req("etl_document_prepare", { case: CASE2 }), {}, f06).reason_id,
    "envelope_payload_is_not_this_preview");
});

test("admission: replayed, expired or wrong-account capabilities refuse through the real F06 ladder", () => {
  const r = req();
  const env = envelopeFor(buildActionPreview(r));
  const cases = [
    [{ presentation: presentationFor(env, { nonce_state: "consumed" }) }, "capability_replayed"],
    [{ capability: capabilityFor(env, { issued_at: "2026-09-24T11:40:00Z", expires_at: "2026-09-24T11:59:30Z" }) },
      "capability_expired"],
    [{ presentation: presentationFor(env, { presented_principals: { ...PRINCIPALS, account_ref: OTHER_SEAT } }) },
      "account_mismatch"],
  ];
  for (const [f06over, f06reason] of cases) {
    const a = admission(r, {}, { envelope: env, ...f06over });
    assert.equal(a.decision, "refuse", f06reason);
    assert.equal(a.reason_id, "capability_presentation_refused");
    assert.equal(a.f06_reason_id, f06reason);
  }
});

test("admission: an envelope sealed for another action, step or case refuses", () => {
  const r = req();
  const p = buildActionPreview(r);
  assert.equal(admission(r, {}, { envelope: envelopeFor(p, { step_id: "step-other" }) }).reason_id,
    "envelope_step_is_not_this_step");
  assert.equal(admission(r, {}, { envelope: envelopeFor(p, { workflow_id: "wf-other" }) }).reason_id,
    "envelope_workflow_is_not_this_case");
  assert.equal(admission(r, {}, { envelope: envelopeFor(p, { action: "business.send_client_document" }) }).reason_id,
    "envelope_action_not_rw02");
});

// ---------------------------------------------------------------------------
// checkable_done 2b — readback (Q084.D1 exact readback; F06 quarantine).
// ---------------------------------------------------------------------------

test("CD2 readback: an exact match with this step's marker confirms and seals one evidence record", () => {
  const r = readback();
  assert.equal(r.decision, "confirmed");
  assert.equal(r.evidence.outcome, "exact_match");
  assert.equal(r.evidence.action_kind, "opportunity_create");
  assert.match(r.evidence.evidence_digest, /^sha256:/);
  assertGrantsNothing(r);
});

test("review D1: a create is not confirmed by an equal-valued opportunity without this step's marker", () => {
  const p = buildActionPreview(req());
  for (const marker of ["absent", "unstated"]) {
    const r = readback(req(), { provider_readback: readbackOf(p, {}, OPP3, marker) });
    assert.equal(r.decision, "stop", marker);
    assert.equal(r.detail_reason, "readback_not_this_steps_opportunity");
  }
});

test("CD2 readback: any differing field stops, naming fields not values, and types are compared strictly", () => {
  const r0 = req();
  const p = buildActionPreview(r0);
  const r = readback(r0, { provider_readback: readbackOf(p, { StageName: "Closing-SECRETVALUE" }) });
  assert.equal(r.decision, "stop");
  assert.equal(r.reason_id, "inconsistent_result");
  assert.deepEqual(r.mismatched_fields, ["StageName"]);
  assert.ok(!JSON.stringify(r).includes("SECRETVALUE"));
  assert.equal(r.evidence.outcome, "mismatch");
  assert.deepEqual(readback(r0, { provider_readback: readbackOf(p, { Total_Commission__c: "1000" }) }).mismatched_fields,
    ["Total_Commission__c"]);
  const missing = readbackOf(p);
  missing.fields = missing.fields.filter(f => f.field !== "CloseDate");
  assert.deepEqual(readback(r0, { provider_readback: missing }).mismatched_fields, ["CloseDate"]);
});

test("CD2 readback: an incomplete field readback is not a readback", () => {
  const p = buildActionPreview(req());
  const r = readback(req(), { provider_readback: { ...readbackOf(p), completeness: "truncated" } });
  assert.equal(r.decision, "readback_required");
  assert.equal(r.reason_id, "field_readback_incomplete");
});

test("CD2 readback: a timeout is unknown and requires a readback before anything else", () => {
  const r = readback(req(), { provider_readback: null }, { outcome: { state: "timed_out" } });
  assert.equal(r.decision, "readback_required");
  assert.equal(r.reason_id, "outcome_unknown_readback_first");
  assert.equal(r.retry_permitted, false);
  // A matching field readback that is NOT joined to this effect's idempotency
  // key does not lift the quarantine: the fields could be someone else's write.
  const unjoined = readback(req(), {}, { outcome: { state: "timed_out" } });
  assert.equal(unjoined.decision, "readback_required");
  assert.equal(unjoined.reason_id, "outcome_unknown_readback_first");
  assert.equal(unjoined.evidence, undefined);
});

test("CD2 readback: a timeout resolved present by the provider confirms as unknown_resolved_by_readback", () => {
  const env = envelopeFor(buildActionPreview(req()));
  const r = readback(req(), {}, { outcome: { state: "timed_out" },
    readback: { state: "effect_present", join_key: env.envelope_digest } });
  assert.equal(r.decision, "confirmed");
  assert.equal(r.evidence.outcome, "unknown_resolved_by_readback");
});

test("CD2 readback: a provider readback joined on another effect stops", () => {
  const r = readback(req(), {}, { outcome: { state: "timed_out" },
    readback: { state: "effect_present", join_key: "sha256:" + "e".repeat(64) } });
  assert.equal(r.decision, "stop");
  assert.equal(r.detail_reason, "attempt_resolution_refused");
  assert.equal(r.f06_reason_id, "readback_joined_on_other_effect");
});

test("CD2 readback: an absent effect is a confirmed failure; the consumed capability is never reusable", () => {
  const env = envelopeFor(buildActionPreview(req()));
  const r = readback(req(), { provider_readback: null }, { outcome: { state: "failed" },
    readback: { state: "effect_absent", join_key: env.envelope_digest } });
  assert.equal(r.decision, "confirmed_failure");
  assert.equal(r.consumed_capability_reusable, false);
  assert.deepEqual(r.retry_requires, ["fresh_preview_confirmation", "fresh_capability"]);
});

test("CD2 readback: a provider call before consumption committed stops", () => {
  const r = readback(req(), {}, {
    consumption: { state: "committed", committed_seq: 3 }, provider_call: { state: "started", started_seq: 2 },
  });
  assert.equal(r.decision, "stop");
  assert.equal(r.detail_reason, "consumption_order_refused");
});

test("CD2 readback: the attempt must be sealed over this preview and step", () => {
  const other = buildActionPreview(req("opportunity_create", { intent_ordinal: 2 }));
  const env = envelopeFor(other);
  assert.throws(() => evaluateWriteReadback({
    tenant: T, page: page(), preview_request: req(), evidence_class: "fixture", observed_at: NOW,
    f06: { envelope: env, capability: capabilityFor(env), attempt: attemptFor(env) },
    provider_readback: readbackOf(other),
  }), isErr("attempt_for_other_preview"));
});

test("CD2 readback: an attempt sealed over this preview but another step is refused", () => {
  const p = buildActionPreview(req());
  const env = envelopeFor(p, { step_id: "step-other" });
  assert.equal(env.payload_digest, p.preview_digest);
  assert.throws(() => evaluateWriteReadback({
    tenant: T, page: page(), preview_request: req(), evidence_class: "fixture", observed_at: NOW,
    f06: { envelope: env, capability: capabilityFor(env), attempt: attemptFor(env) },
    provider_readback: readbackOf(p),
  }), isErr("attempt_for_other_preview"));
});

test("CD2 readback: an attempt for this step but sealed over another payload is refused", () => {
  const p = buildActionPreview(req());
  const env = envelopeFor(p, { payload_digest: "sha256:" + "9".repeat(64) });
  assert.equal(env.step_id, p.preview.step_key);
  assert.throws(() => evaluateWriteReadback({
    tenant: T, page: page(), preview_request: req(), evidence_class: "fixture", observed_at: NOW,
    f06: { envelope: env, capability: capabilityFor(env), attempt: attemptFor(env) },
    provider_readback: readbackOf(p),
  }), isErr("attempt_for_other_preview"));
});

test("CD2 readback: the page must be the previewed org and, for a targeted action, pin the record", () => {
  assert.equal(readback(req(), { page: pageOn({ org: OTHER_ORG }) }).reason_id, "binding_org_not_previewed");
  assert.equal(readback(req("etl_document_prepare"), { page: page() }).reason_id, "record_not_pinned");
  const p = buildActionPreview(req("etl_document_prepare"));
  assert.equal(readback(req("etl_document_prepare"), { provider_readback: readbackOf(p, {}, OPP2) }).reason_id,
    "record_mismatch");
});

test("CD2 readback: a refused preview request is refused before anything is read", () => {
  const r = readback(req(), { preview_request: req("opportunity_create", { target_opportunity_id: OPP }) });
  assert.equal(r.decision, "refuse");
  assert.equal(r.preview_reason_id, "create_names_an_existing_target");
});

// ---------------------------------------------------------------------------
// checkable_done 2c — idempotent resume, anchored on the provider.
// ---------------------------------------------------------------------------

const resume = (effect, journal_hint, opportunity_id) => evaluateResume({
  tenant: T, action_kind: "opportunity_create", case: CASE, intent_ordinal: 1, journal_hint,
  provider: opportunity_id ? { effect, opportunity_id } : { effect },
});

test("CD2 resume: the provider decides; the journal is only ever a hint", () => {
  const behind = resume("effect_present", "not_started", OPP);
  assert.equal(behind.decision, "skip_already_effected");
  assert.equal(behind.reason_id, "journal_behind_provider");
  assert.equal(behind.step_key, stepKey("opportunity_create"));
  assert.equal(behind.repeat_write_permitted, false);
  assert.equal(behind.journal_is_authority, false);
  assert.equal(resume("effect_present", "effected", OPP).reason_id, "provider_confirms_journal");

  const lying = resume("effect_absent", "effected");
  assert.equal(lying.decision, "stop");
  assert.equal(lying.detail_reason, "journal_records_effect_provider_lacks");

  const fresh = resume("effect_absent", "not_started");
  assert.equal(fresh.decision, "proceed_to_preview");
  assert.equal(fresh.consumed_capability_reusable, false);
  assert.ok(fresh.requires.includes("fresh_capability"));

  for (const effect of ["indeterminate", "unstated"]) {
    for (const hint of ["not_started", "effected", "unknown", "absent"]) {
      const r = resume(effect, hint);
      assert.equal(r.decision, "readback_required", `${effect}/${hint}`);
      assert.equal(r.retry_permitted, false);
    }
  }
});

test("review G1: a present effect must name the opportunity it is on", () => {
  assert.throws(() => resume("effect_present", "not_started"), isErr("missing_field"));
});

test("CD2 resume: the same intent always folds to the same step key; distinct intents do not", () => {
  assert.equal(stepKey("opportunity_create"), stepKey("opportunity_create"));
  assert.notEqual(stepKey("opportunity_phase_update", 1), stepKey("opportunity_phase_update", 2));
  assert.notEqual(stepKey("opportunity_create"), stepKey("etl_document_prepare"));
  assert.notEqual(stepKey("opportunity_create"), stepKey("opportunity_create", 1, CASE2));
});

// ---------------------------------------------------------------------------
// checkable_done 3 — each action records distinct evidence and cannot inherit
// trust (Q099.D1).
// ---------------------------------------------------------------------------

function evidenceFor(action_kind, { cls = "fixture", ordinal = 1, observed_at = NOW, mismatch = false } = {}) {
  const fields = action_kind === "opportunity_phase_update"
    ? [field("StageName", `Phase-${ordinal}`, "phase")]
    : defaultFields(action_kind).map(f => ({ ...f }));
  const r = req(action_kind, { intent_ordinal: ordinal, fields });
  const p = buildActionPreview(r);
  const env = envelopeFor(p);
  const out = evaluateWriteReadback({
    tenant: T, page: TARGETED.has(action_kind) ? pinned(OPP) : page(), preview_request: r,
    evidence_class: cls, observed_at,
    f06: { envelope: env, capability: capabilityFor(env), attempt: attemptFor(env) },
    provider_readback: readbackOf(p, mismatch ? { [fields[0].field]: "drifted" } : {}),
  });
  return out.evidence;
}

const reseal = (record, over) => {
  const { evidence_digest, ...rest } = { ...record, ...over };
  return { ...rest, evidence_digest: digest({ kind: "rw02-evidence.v1", ...rest }) };
};

test("CD3 evidence records are distinct per action kind and per step", () => {
  const a = evidenceFor("opportunity_create");
  const b = evidenceFor("opportunity_phase_update");
  const c = evidenceFor("opportunity_phase_update", { ordinal: 2 });
  assert.notEqual(a.evidence_digest, b.evidence_digest);
  assert.notEqual(b.evidence_digest, c.evidence_digest);
  assert.equal(a.action_kind, "opportunity_create");
  assert.equal(b.action_kind, "opportunity_phase_update");
});

test("CD3 a window refuses evidence from another action — trust is never inherited", () => {
  const w = evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_phase_update",
    evidence: [evidenceFor("opportunity_phase_update"), evidenceFor("opportunity_create")] });
  assert.equal(w.decision, "refuse");
  assert.equal(w.reason_id, "evidence_foreign_to_action");
  assert.equal(w.offending_action_kind, "opportunity_create");
  assert.deepEqual(w.inherits_from, []);
});

test("CD3 global trust is refused by name", () => {
  const w = evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create", evidence: [], trust_scope: "global" });
  assert.equal(w.reason_id, "global_trust_excluded");
});

test("CD3 a sample counted twice, an edited record or a foreign tenant is refused", () => {
  const e = evidenceFor("opportunity_create");
  assert.equal(evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create", evidence: [e, e] }).reason_id,
    "evidence_counted_twice");
  const reobserved = evidenceFor("opportunity_create", { observed_at: "2026-09-24T12:30:00Z" });
  assert.equal(evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create",
    evidence: [e, reobserved] }).reason_id, "evidence_counted_twice");
  const failure = evidenceFor("opportunity_create", { mismatch: true });
  assert.equal(failure.outcome, "mismatch");
  assert.equal(evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create",
    evidence: [failure, failure] }).reason_id, "evidence_counted_twice");
  assert.throws(() => evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create",
    evidence: [{ ...e, evidence_class: "supervised_production_sample" }] }), isErr("evidence_seal_broken"));
  assert.throws(() => evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create",
    evidence: [reseal(e, { tenant: "another-tenant" })] }), isErr("tenant_mismatch"));
});

test("review F1: a window of self-sealed records is a reading, never authenticated or eligible", () => {
  const forged = Array.from({ length: 50 }, (_, i) => reseal(evidenceFor("opportunity_create"),
    { step_key: "sha256:" + String(i).padStart(64, "0"), evidence_class: "supervised_production_sample" }));
  const w = evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create", evidence: forged });
  assert.equal(w.window_since_last_failure.records, 50);
  assert.equal(w.evidence_authenticated, false);
  assert.equal(w.evidence_store_seam, V5_RW02_EVIDENCE_STORE_SEAM);
  assert.equal(w.activation_review_eligibility, "unavailable");
  assertGrantsNothing(w);
});

test("CD3 the window restarts after a failure, counts per class, and never activates autonomy", () => {
  const evidence = [
    evidenceFor("opportunity_phase_update", { ordinal: 1, observed_at: "2026-09-20T12:00:00Z" }),
    evidenceFor("opportunity_phase_update", { ordinal: 2, observed_at: "2026-09-21T12:00:00Z", mismatch: true }),
    evidenceFor("opportunity_phase_update", { ordinal: 3, observed_at: "2026-09-22T12:00:00Z",
      cls: "supervised_production_sample" }),
    evidenceFor("opportunity_phase_update", { ordinal: 4, observed_at: "2026-09-23T12:00:00Z", cls: "recovery_exercise" }),
  ];
  const w = evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_phase_update", evidence });
  assert.equal(w.decision, "window_read");
  assert.equal(w.evidence_total, 4);
  assert.equal(w.failures_total, 1);
  assert.equal(w.window_since_last_failure.records, 2);
  assert.deepEqual(w.window_since_last_failure.counts_by_class,
    { fixture: 0, supervised_production_sample: 1, recovery_exercise: 1 });
  assert.equal(w.window_since_last_failure.recovery_exercised, true);
  assert.equal(w.activation_review_eligibility, "unavailable");
  assert.equal(w.activation_review_eligibility_seam, V5_RW02_EVALUATION_WINDOW_SEAM);
  assert.equal(w.activation_gate, "system.autonomy_tier_activation");
  assertGrantsNothing(w);
});

test("every action kind is independently evaluable (no shared window)", () => {
  for (const kind of V5_RW02_ACTION_KIND_KEYS) {
    const w = evaluateActionTrustWindow({ tenant: T, action_kind: kind, evidence: [] });
    assert.equal(w.action_kind, kind);
    assert.equal(w.window_since_last_failure.records, 0);
  }
});
