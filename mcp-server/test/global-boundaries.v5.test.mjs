// V5-S01 — the settled global boundaries, proved case by case.
//
// The suite is organised by the decision it proves, and every settled decision
// gets both halves: the positive case that must pass, and the negatives that
// must refuse. The delegation exception (Q020/Q141) gets the most attention it
// is worth — a rule that only ever refuses cannot be told apart from a rule that
// is broken, so the narrow, exact-action, expiring, receipted grant is asserted
// to WORK before its seven failure shapes are asserted to refuse.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_BOUNDARY_SCHEMA_VERSION,
  V5_BOUNDARY_POLICY_VERSION,
  V5_SETTLED_DECISIONS,
  V5_SETTLED_DECISION_IDS,
  V5_DECISION_SUBSET_CANONICAL_SHA256,
  V5_ACTIONS,
  V5_ACTION_KEYS,
  V5_MAX_DELEGATION_WINDOW_SECONDS,
  V5_REPRESENTATION_SIDES,
  V5_EXPOSED_REPRESENTATION_SIDES,
  V5_BROKERAGE_SIDE_STRUCTURAL_ONLY,
  V5_SCOPE_AMENDMENT_SEAM,
  V5_MAX_CACHED_READ_AGE_SECONDS,
  V5_OPTIONAL_LOCAL_NODES,
  V5_LOCAL_CAPABILITIES,
  V5_LOCAL_FALLBACK_KINDS,
  V5_CANONICAL_AUTHORITY,
  V5_PROHIBITED_DATA_CLASSES,
  V5_INDEPENDENT_PRIVACY_ROUTE_CLASSES,
  V5_INDEPENDENT_PRIVACY_ROUTE_EVIDENCE,
  V5_PHI_AMENDMENT_REQUIRED,
  V5_MODEL_CLASSIFICATION_SEAMS,
  V5_NO_EFFECTS,
  V5BoundaryError,
  assertSettledDecisionBinding,
  evaluateActorAuthority,
  evaluateRepresentationScope,
  evaluateReadContinuity,
  evaluateLocalPlatform,
  evaluatePrivacyBoundary,
  applyModelClassification,
  v5LocalNodeDependencies,
  v5BoundaryPolicyPreimage,
  v5BoundaryPolicyDigest,
  v5BoundaryPolicyCanonicalBytes,
  v5BoundaryProjection,
} from "../src/global-boundaries.v5.js";

const SRC_PATH = fileURLToPath(new URL("../src/global-boundaries.v5.js", import.meta.url));

// The eight source-evidence digests exactly as the reviewed S01 source binding
// carries them. Copied here so drift between the module and the binding is a
// test failure rather than a later discovery.
const REVIEWED_DECISION_BINDING = Object.freeze({
  "Q003.D1": "5b7389d4f1145aa77cd735acaa5895611d767b3809675b46d69f42a7e3d4521d",
  "Q007.D1": "640ce19bd6e4286812d4f833649660bf103af7c3c0d38fd09d348faa3eaf58a5",
  "Q020.D1": "47aef2de3979c0f74756d68f8b364aade821bfcc95d4329659aafb586e25e745",
  "Q030.D1": "6421a440f0138e7e8f4aec6d13429cf1f02785f7199a255a0ff872cb9a8c9d6a",
  "Q033.D1": "1aa74e892a8bb348cccbf2f34e456fe31d0f895e75d4da7ec7f84b9f174961fb",
  "Q073.D1": "f9fc311de43cb12ea50d6c8b598280be340d5a5e34f2da76ee179a020ce1296d",
  "Q092.D1": "396084c25cba6047deaff489111bc371a7be29d0415c4f2dd5eb35ce37305524",
  "Q141.D1": "e71454e022f3c60ae27b673fa0b3899d616e78b638666df2691f3d642baa0a51",
});

const reviewedBinding = () => ({
  decision_subset_canonical_sha256: V5_DECISION_SUBSET_CANONICAL_SHA256,
  decisions: Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
    .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }])),
});

// ---------------------------------------------------------------- fixtures

const partner = slug => Object.freeze({
  slug, display: slug === "joe" ? "Joe" : "Dell", human: true, via: "oauth-google",
  client_id: null, sponsoring_human_slug: null, human_slug: null, sponsor_required: false,
});
const JOE = partner("joe");
const DELL = partner("dell");
const SPONSORED_AGENT = Object.freeze({
  slug: "claude", display: "Claude", human: false, via: "oauth-google",
  client_id: "c1", sponsoring_human_slug: "joe", human_slug: "joe", sponsor_required: true,
});
const UNSPONSORED_AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "agent-token",
  client_id: null, sponsoring_human_slug: null, human_slug: null, sponsor_required: false,
});
const HERMES = Object.freeze({
  slug: "hermes-pilot", display: "Hermes (hermes-pilot)", human: false, hermes: true,
  via: "hermes-token", client_id: null, sponsoring_human_slug: "joe", human_slug: "joe",
  sponsor_required: false,
});

const NOW = "2026-09-09T12:00:00Z";
const RECEIPT = "a".repeat(64);
const CITATION = "b".repeat(64);

const authority = (over = {}) => Object.freeze({
  actor: JOE, action: "system.policy", tenant: ORGANIZATION_TENANT_ID, now: NOW, ...over,
});

const delegation = (over = {}) => Object.freeze({
  delegation_ref: "DEL-1", granted_by: "joe", granted_to: "dell",
  action: "release_admin.publish_release", receipt_digest: RECEIPT,
  issued_at: "2026-09-09T00:00:00Z", expires_at: "2026-09-10T00:00:00Z", ...over,
});

const redecision = (over = {}) => Object.freeze({
  redecision_ref: "RED-1", decided_by: "joe", subject: "dell",
  action: "developer.change_source", cited_source_digest: CITATION,
  decided_at: "2026-09-08T00:00:00Z", ...over,
});

const businessControls = (slug, over = {}) => Object.freeze({
  deal_owner_slug: slug, signer_slug: slug, account_slug: slug,
  policy_scope: [...V5_ACTION_KEYS],
  capabilities: ["deal.read", "deal.write", "document.send", "document.sign", "prospecting.write"],
  ...over,
});

const SYSTEM_ACTIONS = V5_ACTION_KEYS.filter(k => V5_ACTIONS[k].authority_class === "system_authority");
const DEFERRED_ACTIONS = V5_ACTION_KEYS.filter(k =>
  V5_ACTIONS[k].authority_class === "developer" || V5_ACTIONS[k].authority_class === "release_admin");
const BUSINESS_ACTIONS = V5_ACTION_KEYS.filter(k => V5_ACTIONS[k].authority_class === "ordinary_business");

const throwsCode = (fn, code) => assert.throws(fn, error => {
  assert.ok(error instanceof V5BoundaryError, `expected V5BoundaryError, got ${error?.name}: ${error?.message}`);
  assert.equal(error.code, code, `expected code ${code}, got ${error.code}`);
  return true;
});

// ------------------------------------------------- Q003/Q020/Q141 authority

test("Q003/Q020/Q141: Joe retains every system-authority action", () => {
  assert.ok(SYSTEM_ACTIONS.length >= 6, "the six retained system classes must all be registered");
  for (const action of SYSTEM_ACTIONS) {
    const result = evaluateActorAuthority(authority({ actor: JOE, action }));
    assert.equal(result.decision, "allow", action);
    assert.equal(result.grant_kind, "retained_system_authority", action);
    assert.equal(result.permanent_privilege_granted, false, action);
  }
  for (const key of ["system.design", "system.policy", "system.release_decision", "system.security",
    "system.destructive_migration", "system.autonomy_tier_activation"]) {
    assert.ok(SYSTEM_ACTIONS.includes(key), `${key} must be a retained system-authority action`);
  }
});

test("Q020/Q141: Dell is refused every system-authority action (unauthorized admin)", () => {
  for (const action of SYSTEM_ACTIONS) {
    const result = evaluateActorAuthority(authority({ actor: DELL, action }));
    assert.equal(result.decision, "refuse", action);
    assert.equal(result.reason_id, "system_authority_reserved_to_joe", action);
    assert.equal(result.delegable, false, action);
  }
});

test("Q141: a system-authority action cannot be reached by delegation or redecision", () => {
  for (const action of SYSTEM_ACTIONS) {
    const withDelegation = evaluateActorAuthority(authority({
      actor: DELL, action, delegation: delegation({ action }),
    }));
    assert.equal(withDelegation.decision, "refuse");
    assert.equal(withDelegation.reason_id, "system_authority_reserved_to_joe");
    const withRedecision = evaluateActorAuthority(authority({
      actor: DELL, action, redecision: redecision({ action }),
    }));
    assert.equal(withRedecision.decision, "refuse");
    assert.equal(withRedecision.reason_id, "system_authority_reserved_to_joe");
  }
});

test("Q141: Dell's developer and release-admin authority is deferred with no grant", () => {
  assert.ok(DEFERRED_ACTIONS.length >= 2);
  for (const action of DEFERRED_ACTIONS) {
    const result = evaluateActorAuthority(authority({ actor: DELL, action }));
    assert.equal(result.decision, "refuse", action);
    assert.equal(result.reason_id, "deferred_authority_requires_grant", action);
    assert.deepEqual([...result.accepted_grant_kinds],
      ["cited_joe_redecision", "narrow_expiring_receipted_delegation"]);
  }
  // Joe holds both classes inherently; the deferral is Dell's alone.
  for (const action of DEFERRED_ACTIONS) {
    assert.equal(evaluateActorAuthority(authority({ actor: JOE, action })).decision, "allow", action);
  }
});

test("THE SETTLED EXCEPTION, positively: a narrow expiring receipted delegation authorizes its bound action", () => {
  const result = evaluateActorAuthority(authority({
    actor: DELL, action: "release_admin.publish_release", delegation: delegation(),
  }));
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "narrow_expiring_receipted_delegation_current");
  assert.equal(result.grant_kind, "narrow_expiring_receipted_delegation");
  assert.equal(result.bound_action, "release_admin.publish_release");
  assert.equal(result.grant_ref, "DEL-1");
  assert.equal(result.expires_at, "2026-09-10T00:00:00Z");
  // The grant authorizes an action, never a standing privilege.
  assert.equal(result.permanent_privilege_granted, false);
});

test("THE SETTLED EXCEPTION, positively: a later cited Joe redecision authorizes its bound action", () => {
  const result = evaluateActorAuthority(authority({
    actor: DELL, action: "developer.change_source", redecision: redecision(),
  }));
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "cited_joe_redecision_authorizes_bound_action");
  assert.equal(result.grant_kind, "cited_joe_redecision");
  assert.equal(result.bound_action, "developer.change_source");
  assert.equal(result.permanent_privilege_granted, false);
});

test("Q141: the delegation grant refuses in every shape that is not narrow, exact, current and receipted", () => {
  const cases = [
    // missing — no grant at all
    [undefined, "deferred_authority_requires_grant"],
    // wildcard
    [delegation({ action: "*" }), "delegation_wildcard_refused"],
    [delegation({ action: "release_admin.*" }), "delegation_wildcard_refused"],
    [delegation({ action: "any" }), "delegation_wildcard_refused"],
    // expired
    [delegation({ issued_at: "2026-09-01T00:00:00Z", expires_at: "2026-09-02T00:00:00Z" }),
      "delegation_expired"],
    // cross-action
    [delegation({ action: "developer.change_source" }), "delegation_action_mismatch"],
    // overbroad window
    [delegation({ issued_at: "2026-09-01T00:00:00Z", expires_at: "2026-10-01T00:00:00Z" }),
      "delegation_window_overbroad"],
    // unreceipted
    [delegation({ receipt_digest: "not-a-digest" }), "delegation_unreceipted"],
    // permanent
    [delegation({ expires_at: null }), "delegation_permanent_refused"],
    [delegation({ expires_at: "never" }), "delegation_permanent_refused"],
    [delegation({ expires_at: "permanent" }), "delegation_permanent_refused"],
    // wrong grantor / wrong subject
    [delegation({ granted_by: "dell" }), "delegation_grantor_not_system_authority"],
    [delegation({ granted_to: "joe" }), "delegation_subject_mismatch"],
    // not yet effective, and an inverted window
    [delegation({ issued_at: "2026-09-20T00:00:00Z", expires_at: "2026-09-21T00:00:00Z" }),
      "delegation_not_yet_effective"],
    [delegation({ issued_at: "2026-09-10T00:00:00Z", expires_at: "2026-09-09T00:00:00Z" }),
      "delegation_window_invalid"],
    // an action nobody registered
    [delegation({ action: "release_admin.invent_authority" }), "delegation_action_unknown"],
  ];
  for (const [grant, reason_id] of cases) {
    const result = evaluateActorAuthority(authority({
      actor: DELL, action: "release_admin.publish_release",
      ...(grant === undefined ? {} : { delegation: grant }),
    }));
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
    assert.equal(result.permanent_privilege_granted, false);
  }
});

test("Q141: the window ceiling is exactly the declared one", () => {
  const issued_at = "2026-09-09T00:00:00Z";
  const atCeiling = new Date(Date.parse(issued_at) + V5_MAX_DELEGATION_WINDOW_SECONDS * 1000)
    .toISOString().replace(".000Z", "Z");
  const overCeiling = new Date(Date.parse(issued_at) + (V5_MAX_DELEGATION_WINDOW_SECONDS + 1) * 1000)
    .toISOString().replace(".000Z", "Z");
  const request = expires_at => authority({
    actor: DELL, action: "release_admin.publish_release",
    delegation: delegation({ issued_at, expires_at }),
  });
  assert.equal(evaluateActorAuthority(request(atCeiling)).decision, "allow");
  assert.equal(evaluateActorAuthority(request(overCeiling)).reason_id, "delegation_window_overbroad");
});

test("Q141: the redecision grant refuses when it is not cited, exact and effective", () => {
  const cases = [
    [redecision({ decided_by: "dell" }), "redecision_author_not_system_authority"],
    [redecision({ subject: "joe" }), "redecision_subject_mismatch"],
    [redecision({ action: "*" }), "redecision_wildcard_refused"],
    [redecision({ action: "release_admin.publish_release" }), "redecision_action_mismatch"],
    [redecision({ cited_source_digest: "unsourced" }), "redecision_uncited"],
    [redecision({ decided_at: "2026-12-01T00:00:00Z" }), "redecision_not_yet_effective"],
    [redecision({ action: "developer.invent" }), "redecision_action_unknown"],
  ];
  for (const [grant, reason_id] of cases) {
    const result = evaluateActorAuthority(authority({
      actor: DELL, action: "developer.change_source", redecision: grant,
    }));
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
  }
});

test("Q141: a present grant that fails is never rescued by a second grant", () => {
  const result = evaluateActorAuthority(authority({
    actor: DELL, action: "developer.change_source",
    redecision: redecision({ cited_source_digest: "unsourced" }),
    delegation: delegation({ action: "developer.change_source" }),
  }));
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "redecision_uncited");
});

// S01-AUTH-001. The independent review reproduced this: a valid redecision made
// the evaluator return allow for a request that ALSO carried an expired or
// cross-action delegation, because only the first grant was ever read. Both
// orders are asserted, because the defect was an ordering defect.
test("Q141: a valid grant does not excuse a second present invalid grant (both orders)", () => {
  // Each case is bound to the REQUESTED action and varies only its own defect,
  // so the reason proves that defect rather than an action mismatch standing in
  // for it. The cross-action case is stated separately and deliberately.
  const bound = over => delegation({ action: "developer.change_source", ...over });
  const validRedecisionPlus = [
    [bound({ issued_at: "2026-09-01T00:00:00Z", expires_at: "2026-09-02T00:00:00Z" }),
      "delegation_expired"],
    [delegation({ action: "release_admin.publish_release" }), "delegation_action_mismatch"],
    [bound({ receipt_digest: "not-a-digest" }), "delegation_unreceipted"],
    [bound({ expires_at: null }), "delegation_permanent_refused"],
    [bound({ action: "*" }), "delegation_wildcard_refused"],
    [bound({ issued_at: "2026-09-01T00:00:00Z", expires_at: "2026-10-01T00:00:00Z" }),
      "delegation_window_overbroad"],
    [bound({ delegation_ref: "" }), "delegation_ref_invalid"],
  ];
  for (const [badDelegation, reason_id] of validRedecisionPlus) {
    const result = evaluateActorAuthority(authority({
      actor: DELL, action: "developer.change_source",
      redecision: redecision(), delegation: badDelegation,
    }));
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
  }
  // The mirror: a valid delegation beside an invalid redecision.
  const validDelegationPlus = [
    [redecision({ action: "release_admin.publish_release", subject: "dell" }), "redecision_action_mismatch"],
    [redecision({ decided_by: "dell" }), "redecision_author_not_system_authority"],
    [redecision({ decided_at: "2026-12-01T00:00:00Z" }), "redecision_not_yet_effective"],
  ];
  for (const [badRedecision, reason_id] of validDelegationPlus) {
    const result = evaluateActorAuthority(authority({
      actor: DELL, action: "developer.change_source",
      delegation: delegation({ action: "developer.change_source" }), redecision: badRedecision,
    }));
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
  }
  // Two valid grants for the same bound action still allow, and the result says
  // both were actually validated rather than one being taken on trust.
  const both = evaluateActorAuthority(authority({
    actor: DELL, action: "developer.change_source",
    redecision: redecision(), delegation: delegation({ action: "developer.change_source" }),
  }));
  assert.equal(both.decision, "allow");
  assert.deepEqual([...both.validated_grants],
    ["cited_joe_redecision", "narrow_expiring_receipted_delegation"]);
  // A single grant reports only itself.
  const single = evaluateActorAuthority(authority({
    actor: DELL, action: "developer.change_source", redecision: redecision(),
  }));
  assert.deepEqual([...single.validated_grants], ["cited_joe_redecision"]);
});

// S01-AUTH-002. An allow whose grant_ref is "" or an object names no receipt
// and no cited decision, so it cannot be checked by anyone later.
test("Q141: a grant whose provenance reference is empty or not a string refuses", () => {
  for (const bad of ["", "   ", {}, [], 42, null, true]) {
    const withDelegation = evaluateActorAuthority(authority({
      actor: DELL, action: "release_admin.publish_release",
      delegation: delegation({ delegation_ref: bad }),
    }));
    assert.equal(withDelegation.decision, "refuse", `delegation_ref ${JSON.stringify(bad)}`);
    assert.equal(withDelegation.reason_id, "delegation_ref_invalid");

    const withRedecision = evaluateActorAuthority(authority({
      actor: DELL, action: "developer.change_source",
      redecision: redecision({ redecision_ref: bad }),
    }));
    assert.equal(withRedecision.decision, "refuse", `redecision_ref ${JSON.stringify(bad)}`);
    assert.equal(withRedecision.reason_id, "redecision_ref_invalid");
  }
  // A real reference still authorizes, and is echoed back intact.
  const allowed = evaluateActorAuthority(authority({
    actor: DELL, action: "release_admin.publish_release",
    delegation: delegation({ delegation_ref: "DEL-2026-09-09-01" }),
  }));
  assert.equal(allowed.decision, "allow");
  assert.equal(allowed.grant_ref, "DEL-2026-09-09-01");
});

// S01-AUTH-003. Date.parse turns 2026-02-31 into 3 March rather than refusing,
// so an impossible instant silently became a different, valid window.
test("Q141: a calendar-impossible instant refuses instead of being normalized", () => {
  const impossible = [
    "2026-02-31T00:00:00Z", "2026-02-30T00:00:00Z", "2026-04-31T00:00:00Z",
    "2026-13-01T00:00:00Z", "2026-00-10T00:00:00Z", "2026-01-00T00:00:00Z",
    "2026-01-32T00:00:00Z", "2026-01-01T24:00:00Z", "2026-01-01T00:60:00Z",
    "2026-01-01T00:00:60Z", "2026-01-01T00:00:00+24:00", "2026-01-01T00:00:00+00:60",
    "2027-02-29T00:00:00Z",
  ];
  for (const value of impossible) {
    throwsCode(() => evaluateActorAuthority(authority({ now: value })), "invalid_timestamp");
    throwsCode(() => evaluateActorAuthority(authority({
      actor: DELL, action: "release_admin.publish_release",
      delegation: delegation({ issued_at: value, expires_at: "2026-03-04T00:00:00Z" }),
    })), "invalid_timestamp");
  }
  // The exact counterexample the review executed: it previously returned allow.
  throwsCode(() => evaluateActorAuthority(authority({
    actor: DELL, action: "release_admin.publish_release",
    delegation: delegation({ issued_at: "2026-02-31T00:00:00Z", expires_at: "2026-03-04T00:00:00Z" }),
  })), "invalid_timestamp");

  // Valid instants are untouched, including leap day, an end-of-month day and
  // both signed explicit offsets.
  const valid = [
    "2028-02-29T00:00:00Z", "2026-01-31T23:59:59Z", "2026-12-31T23:59:59.999Z",
    "2026-09-09T12:00:00+00:00", "2026-09-09T07:00:00-05:00", "2026-09-09T17:00:00+05:30",
  ];
  for (const value of valid) {
    const result = evaluateActorAuthority(authority({ now: value }));
    assert.equal(result.decision, "allow", value);
  }
  // An offset instant still drives the window comparison correctly: the same
  // moment written two ways gives the same answer.
  const asOffset = evaluateActorAuthority(authority({
    actor: DELL, action: "release_admin.publish_release",
    now: "2026-09-09T07:00:00-05:00", delegation: delegation(),
  }));
  const asZulu = evaluateActorAuthority(authority({
    actor: DELL, action: "release_admin.publish_release",
    now: "2026-09-09T12:00:00Z", delegation: delegation(),
  }));
  assert.equal(asOffset.decision, "allow");
  assert.deepEqual(asOffset, asZulu);
});

test("Q020/Q141: ordinary business runs for either partner inside its controls", () => {
  for (const actor of [JOE, DELL]) {
    for (const action of BUSINESS_ACTIONS) {
      const result = evaluateActorAuthority(authority({
        actor, action, controls: businessControls(actor.slug),
      }));
      assert.equal(result.decision, "allow", `${actor.slug} ${action}`);
      assert.equal(result.grant_kind, "ordinary_business");
      assert.equal(result.permanent_privilege_granted, false);
    }
  }
});

test("Q020/Q141: ordinary business refuses on wrong owner, signer, account, policy or capability", () => {
  const base = { actor: DELL, action: "business.sign_document" };
  const cases = [
    [businessControls("dell", { deal_owner_slug: "joe" }), "deal_owner_mismatch"],
    [businessControls("dell", { signer_slug: "joe" }), "signer_mismatch"],
    [businessControls("dell", { account_slug: "joe" }), "account_mismatch"],
    [businessControls("dell", { policy_scope: ["business.read_deal"] }), "policy_scope_excludes_action"],
    [businessControls("dell", { capabilities: ["deal.read"] }), "capability_not_granted"],
  ];
  for (const [controls, reason_id] of cases) {
    const result = evaluateActorAuthority(authority({ ...base, controls }));
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
    assert.equal(result.required_capability, "document.sign");
  }
});

test("Q003: authority is a verified-partner boundary, not a runtime one", () => {
  for (const actor of [SPONSORED_AGENT, UNSPONSORED_AGENT, HERMES]) {
    const result = evaluateActorAuthority(authority({
      actor, action: "business.read_deal", controls: businessControls(actor.slug),
    }));
    assert.equal(result.decision, "refuse", actor.slug);
    assert.equal(result.reason_id, "actor_not_verified_partner", actor.slug);
  }
  // Even a sponsored agent holding a valid-looking delegation refuses: the
  // grant is Dell's, and the runtime is not Dell.
  const withGrant = evaluateActorAuthority(authority({
    actor: SPONSORED_AGENT, action: "release_admin.publish_release", delegation: delegation(),
  }));
  assert.equal(withGrant.reason_id, "actor_not_verified_partner");
});

test("Q020: continuity context never participates in the authority answer", () => {
  const withoutContext = evaluateActorAuthority(authority({
    actor: DELL, action: "business.read_deal", controls: businessControls("dell"),
  }));
  const withContext = evaluateActorAuthority(authority({
    actor: DELL, action: "business.read_deal", controls: businessControls("dell"),
    continuity_context: { local_platform_state: "unavailable", connectivity: "degraded" },
  }));
  assert.deepEqual(withContext, withoutContext);
  // And an outage does not open a deferred class either.
  const deferred = evaluateActorAuthority(authority({
    actor: DELL, action: "release_admin.publish_release",
    continuity_context: { local_platform_state: "unavailable" },
  }));
  assert.equal(deferred.reason_id, "deferred_authority_requires_grant");
});

test("authority: an unreadable request fails closed rather than guessing", () => {
  throwsCode(() => evaluateActorAuthority(authority({ tenant: "someone-else" })), "tenant_mismatch");
  throwsCode(() => evaluateActorAuthority(authority({ action: "system.invent" })), "unknown_action");
  throwsCode(() => evaluateActorAuthority({ ...authority(), sudo: true }), "unknown_field");
  throwsCode(() => evaluateActorAuthority({ actor: JOE, action: "system.policy" }), "missing_field");
  throwsCode(() => evaluateActorAuthority(authority({ now: "2026-09-09" })), "invalid_timestamp");
  throwsCode(() => evaluateActorAuthority(authority({ now: "yesterday" })), "invalid_timestamp");
  throwsCode(() => evaluateActorAuthority("joe"), "invalid_shape");
  throwsCode(() => evaluateActorAuthority(authority({
    actor: DELL, action: "release_admin.publish_release",
    delegation: { ...delegation(), forever: true },
  })), "unknown_field");
  throwsCode(() => evaluateActorAuthority(authority({
    actor: DELL, action: "business.read_deal",
  })), "missing_field");
});

// --------------------------------------------------------- Q073/Q092 scope

test("Q073: tenant and buyer representation is the exposed scope", () => {
  for (const side of V5_EXPOSED_REPRESENTATION_SIDES) {
    const result = evaluateRepresentationScope({
      representation_side: side, intent: "expose", surface: "deal-room",
    });
    assert.equal(result.decision, "allow", side);
    assert.equal(result.exposed, true, side);
    assert.equal(result.reason_id, "tenant_buyer_representation_in_scope");
    assert.equal(result.listing_activation, "inactive_pending_amendment");
  }
});

test("Q092: the brokerage side is a structural value whose exposure refuses", () => {
  for (const side of V5_BROKERAGE_SIDE_STRUCTURAL_ONLY) {
    const exposed = evaluateRepresentationScope({ representation_side: side, intent: "expose" });
    assert.equal(exposed.decision, "refuse", side);
    assert.equal(exposed.reason_id, "listing_side_exposure_refused");
    assert.equal(exposed.exposed, false);
    // The structural identifier stays honest, which is the whole point: future
    // listing work needs an amendment, not a repair of corrupted identifiers.
    assert.equal(exposed.structural_side_recognized, true);
    assert.equal(exposed.brokerage_side_structural_only, true);
    assert.equal(exposed.amendment_seam, V5_SCOPE_AMENDMENT_SEAM);

    const structural = evaluateRepresentationScope({
      representation_side: side, intent: "structural_record",
    });
    assert.equal(structural.decision, "allow", side);
    assert.equal(structural.reason_id, "brokerage_side_structural_value_only");
    assert.equal(structural.exposed, false);
  }
});

test("Q092: listing-side activation refuses on every path", () => {
  for (const side of V5_REPRESENTATION_SIDES) {
    const activated = evaluateRepresentationScope({
      representation_side: side, intent: "structural_record", activate_listing_side: true,
    });
    assert.equal(activated.decision, "refuse", side);
    assert.equal(activated.reason_id, "listing_side_activation_refused");
    assert.equal(activated.listing_activation, "inactive_pending_amendment");
  }
  const activateIntent = evaluateRepresentationScope({
    representation_side: "landlord", intent: "activate",
  });
  assert.equal(activateIntent.decision, "refuse");
  assert.equal(activateIntent.reason_id, "listing_side_activation_refused");
});

test("scope: an unregistered side or intent fails closed", () => {
  throwsCode(() => evaluateRepresentationScope({ representation_side: "sublandlord", intent: "expose" }),
    "unknown_representation_side");
  throwsCode(() => evaluateRepresentationScope({ representation_side: "tenant", intent: "publish" }),
    "unknown_scope_intent");
  throwsCode(() => evaluateRepresentationScope({ representation_side: "tenant", intent: "expose", saas: true }),
    "unknown_field");
  throwsCode(() => evaluateRepresentationScope({ representation_side: "tenant" }), "missing_field");
});

// ------------------------------------------------------- Q007 read continuity

test("Q007: an online read is live and available", () => {
  const result = evaluateReadContinuity({ operation_kind: "read", connectivity: "online" });
  assert.equal(result.decision, "allow");
  assert.equal(result.availability, "available");
  assert.equal(result.source, "live");
  assert.equal(result.cached, false);
});

test("Q007: a cached read inside its bound is allowed and reports itself as degraded", () => {
  const result = evaluateReadContinuity({
    operation_kind: "read", connectivity: "offline",
    cache: { state: "fresh", age_seconds: 120 },
  });
  assert.equal(result.decision, "allow");
  assert.equal(result.availability, "degraded");
  assert.equal(result.source, "cache");
  assert.equal(result.cached, true);
  assert.equal(result.cache_age_seconds, 120);
  assert.equal(result.cache_bound_seconds, V5_MAX_CACHED_READ_AGE_SECONDS);
});

test("Q007: a cached read outside its bound is unavailable, never an empty success", () => {
  const outside = evaluateReadContinuity({
    operation_kind: "read", connectivity: "offline",
    cache: { state: "fresh", age_seconds: V5_MAX_CACHED_READ_AGE_SECONDS + 1 },
  });
  assert.equal(outside.decision, "refuse");
  assert.equal(outside.reason_id, "cached_read_outside_bound");
  assert.equal(outside.availability, "unavailable");
  assert.equal(outside.empty_result_reported_as_success, false);

  const nothingCached = evaluateReadContinuity({ operation_kind: "read", connectivity: "offline" });
  assert.equal(nothingCached.decision, "refuse");
  assert.equal(nothingCached.reason_id, "read_unavailable_no_cache");
  assert.equal(nothingCached.availability, "unavailable");
  assert.equal(nothingCached.empty_result_reported_as_success, false);
  assert.match(nothingCached.documented_fallback, /^documented_old_school_fallback:/);

  const stale = evaluateReadContinuity({
    operation_kind: "read", connectivity: "offline", cache: { state: "stale", age_seconds: 10 },
  });
  assert.equal(stale.availability, "degraded");
  assert.equal(stale.reason_id, "cached_read_stale");
});

test("Q007: a caller may tighten the cached-read bound and may never widen it", () => {
  const tightened = evaluateReadContinuity({
    operation_kind: "read", connectivity: "degraded",
    cache: { state: "fresh", age_seconds: 90, max_age_seconds: 60 },
  });
  assert.equal(tightened.decision, "refuse");
  assert.equal(tightened.cache_bound_seconds, 60);
  throwsCode(() => evaluateReadContinuity({
    operation_kind: "read", connectivity: "offline",
    cache: { state: "fresh", age_seconds: 10, max_age_seconds: V5_MAX_CACHED_READ_AGE_SECONDS + 1 },
  }), "cache_bound_overbroad");
});

test("Q007: an offline mutation always refuses", () => {
  for (const connectivity of ["offline", "degraded"]) {
    const result = evaluateReadContinuity({ operation_kind: "mutation", connectivity });
    assert.equal(result.decision, "refuse", connectivity);
    assert.equal(result.reason_id, "offline_mutation_refused", connectivity);
    assert.equal(result.source, "none");
  }
  // Not even a perfectly fresh cache buys an offline write.
  const withCache = evaluateReadContinuity({
    operation_kind: "mutation", connectivity: "offline", cache: { state: "fresh", age_seconds: 1 },
  });
  assert.equal(withCache.reason_id, "offline_mutation_refused");
  assert.equal(evaluateReadContinuity({ operation_kind: "mutation", connectivity: "online" }).decision,
    "allow");
});

test("Q007: a missing or stale dependency is reported, not emptied", () => {
  const missing = evaluateReadContinuity({
    operation_kind: "read", connectivity: "online", dependency: { ref: "dep-1", state: "missing" },
  });
  assert.equal(missing.decision, "refuse");
  assert.equal(missing.availability, "unavailable");
  assert.equal(missing.reason_id, "dependency_missing_unavailable");
  assert.equal(missing.dependency_ref, "dep-1");

  const stale = evaluateReadContinuity({
    operation_kind: "read", connectivity: "online", dependency: { ref: "dep-1", state: "stale" },
  });
  assert.equal(stale.decision, "refuse");
  assert.equal(stale.availability, "degraded");
  assert.equal(stale.reason_id, "dependency_stale_degraded");

  const staleWithCache = evaluateReadContinuity({
    operation_kind: "read", connectivity: "online",
    dependency: { ref: "dep-1", state: "stale" }, cache: { state: "fresh", age_seconds: 5 },
  });
  assert.equal(staleWithCache.decision, "allow");
  assert.equal(staleWithCache.availability, "degraded");

  const staleMutation = evaluateReadContinuity({
    operation_kind: "mutation", connectivity: "online", dependency: { state: "stale" },
  });
  assert.equal(staleMutation.decision, "refuse");
  assert.equal(staleMutation.reason_id, "dependency_stale_mutation_refused");
});

test("continuity: unregistered states fail closed", () => {
  throwsCode(() => evaluateReadContinuity({ operation_kind: "sync", connectivity: "online" }),
    "unknown_operation_kind");
  throwsCode(() => evaluateReadContinuity({ operation_kind: "read", connectivity: "flaky" }),
    "unknown_connectivity_state");
  throwsCode(() => evaluateReadContinuity({
    operation_kind: "read", connectivity: "offline", cache: { state: "warm" },
  }), "unknown_cache_state");
  throwsCode(() => evaluateReadContinuity({
    operation_kind: "read", connectivity: "online", dependency: { state: "maybe" },
  }), "unknown_dependency_state");
  throwsCode(() => evaluateReadContinuity({ operation_kind: "read", connectivity: "online", force: true }),
    "unknown_field");
});

// ------------------------------------------------- Q020/Q030 local platform

test("Q030: an available optional node runs locally and still holds no unique authority", () => {
  for (const node of V5_OPTIONAL_LOCAL_NODES) {
    const result = evaluateLocalPlatform({
      node, node_state: "available", capability: "local_model_inference",
    });
    assert.equal(result.decision, "allow", node);
    assert.equal(result.execution, "local_node");
    assert.equal(result.availability, "available");
    assert.equal(result.carries_unique_authority, false);
    assert.equal(result.canonical_authority, V5_CANONICAL_AUTHORITY);
  }
});

test("Q020/Q030: losing the node yields fallback, queue, degraded or unavailable — never lost authority", () => {
  for (const node of V5_OPTIONAL_LOCAL_NODES) {
    for (const node_state of ["degraded", "unavailable"]) {
      const cloud = evaluateLocalPlatform({ node, node_state, capability: "local_model_inference" });
      assert.equal(cloud.decision, "allow");
      assert.equal(cloud.execution, "cloud_fallback");
      assert.equal(cloud.availability, "degraded");

      const queued = evaluateLocalPlatform({ node, node_state, capability: "browser_automation" });
      assert.equal(queued.decision, "deferred");
      assert.equal(queued.execution, "visible_queue");
      assert.equal(queued.queue_visible, true);
      assert.equal(queued.availability, "degraded");

      const none = evaluateLocalPlatform({ node, node_state, capability: "local_media_transcription" });
      assert.equal(none.decision, "refuse");
      assert.equal(none.execution, "none");
      assert.equal(none.availability, node_state === "unavailable" ? "unavailable" : "degraded");

      for (const result of [cloud, queued, none]) {
        assert.equal(result.authority_unchanged, true);
        assert.equal(result.carries_unique_authority, false);
        assert.equal(result.canonical_authority, V5_CANONICAL_AUTHORITY);
      }
    }
  }
});

test("Q030: a node asserted to hold unique authority refuses", () => {
  for (const node of V5_OPTIONAL_LOCAL_NODES) {
    const result = evaluateLocalPlatform({
      node, node_state: "available", capability: "development_capacity",
      assert_unique_authority: true,
    });
    assert.equal(result.decision, "refuse", node);
    assert.equal(result.reason_id, "local_node_unique_authority_refused");
    assert.equal(result.carries_unique_authority, false);
  }
});

test("Q030: every local capability declares its disposition, so none can hide", () => {
  const declared = v5LocalNodeDependencies();
  assert.equal(declared.length, Object.keys(V5_LOCAL_CAPABILITIES).length);
  for (const entry of declared) {
    assert.ok(V5_LOCAL_FALLBACK_KINDS.includes(entry.fallback), entry.capability);
    assert.equal(entry.authority_bearing, false, entry.capability);
    // Every declared capability is evaluable; a registry row nothing can answer
    // would be exactly the hidden dependency this asserts against.
    const result = evaluateLocalPlatform({
      node: "mac-studio", node_state: "unavailable", capability: entry.capability,
    });
    assert.ok(["allow", "deferred", "refuse"].includes(result.decision), entry.capability);
  }
});

test("local platform: an unregistered node, state or capability fails closed", () => {
  throwsCode(() => evaluateLocalPlatform({
    node: "laptop", node_state: "available", capability: "development_capacity",
  }), "unknown_local_node");
  throwsCode(() => evaluateLocalPlatform({
    node: "mac-studio", node_state: "asleep", capability: "development_capacity",
  }), "unknown_local_node_state");
  throwsCode(() => evaluateLocalPlatform({
    node: "mac-studio", node_state: "available", capability: "sign_releases",
  }), "unknown_local_capability");
  throwsCode(() => evaluateLocalPlatform({
    node: "mac-studio", node_state: "available", capability: "development_capacity", authority: "unique",
  }), "unknown_field");
});

// --------------------------------------------------- Q033 privacy boundary

test("Q033: PHI and raw patient-level locations refuse", () => {
  for (const dataClass of V5_PROHIBITED_DATA_CLASSES) {
    const result = evaluatePrivacyBoundary({ data_classes: [dataClass] });
    assert.equal(result.decision, "refuse", dataClass);
    assert.equal(result.reason_id, "phi_or_raw_patient_location_refused", dataClass);
    assert.equal(result.amendment_required, V5_PHI_AMENDMENT_REQUIRED);
    assert.equal(result.phi_permitted, false);
  }
  assert.ok(V5_PROHIBITED_DATA_CLASSES.includes("phi"));
  assert.ok(V5_PROHIBITED_DATA_CLASSES.includes("raw_patient_location"));
  // A prohibited class mixed into an otherwise permitted payload still refuses.
  const mixed = evaluatePrivacyBoundary({ data_classes: ["market_comp", "phi"] });
  assert.equal(mixed.decision, "refuse");
  assert.deepEqual([...mixed.prohibited_classes], ["phi"]);
});

test("Q033: the later aggregate heat map gets its OWN result, not the PHI refusal", () => {
  const phi = evaluatePrivacyBoundary({ data_classes: ["raw_patient_location"] });
  for (const dataClass of V5_INDEPENDENT_PRIVACY_ROUTE_CLASSES) {
    const routed = evaluatePrivacyBoundary({
      data_classes: [dataClass], intended_use: "aggregate_demand_heat_map",
    });
    assert.equal(routed.decision, "needs_independent_privacy_route", dataClass);
    assert.equal(routed.reason_id, "aggregate_input_requires_independent_privacy_route");
    assert.equal(routed.accepted, false);
    assert.equal(routed.required_evidence, V5_INDEPENDENT_PRIVACY_ROUTE_EVIDENCE);
    // Distinct from the refusal in both fields a caller would branch on, and
    // not silently accepted either.
    assert.notEqual(routed.decision, phi.decision);
    assert.notEqual(routed.reason_id, phi.reason_id);
    assert.notEqual(routed.decision, "allow");
  }
  // A raw class carried alongside the aggregate one is still the PHI refusal.
  const both = evaluatePrivacyBoundary({
    data_classes: ["aggregate_patient_location_heatmap", "raw_patient_location"],
  });
  assert.equal(both.decision, "refuse");
  assert.equal(both.reason_id, "phi_or_raw_patient_location_refused");
});

test("Q033: ordinary CRE data classes pass, and an unregistered class fails closed", () => {
  const result = evaluatePrivacyBoundary({
    data_classes: ["market_comp", "lease_economics", "practice_business_profile"],
  });
  assert.equal(result.decision, "allow");
  assert.equal(result.phi_permitted, false);
  throwsCode(() => evaluatePrivacyBoundary({ data_classes: ["patient_notes"] }), "unknown_data_class");
  throwsCode(() => evaluatePrivacyBoundary({ data_classes: [] }), "missing_field");
  throwsCode(() => evaluatePrivacyBoundary({ data_classes: ["market_comp"], hipaa_ok: true }),
    "unknown_field");
});

// ------------------------------------------------------ model judgment seam

test("model judgment: a label inside a typed seam classifies and widens nothing", () => {
  for (const [seam, labels] of Object.entries(V5_MODEL_CLASSIFICATION_SEAMS)) {
    const result = applyModelClassification({ seam, label: labels[0], confidence: 0.9 });
    assert.equal(result.decision, "allow", seam);
    assert.equal(result.label, labels[0]);
    assert.equal(result.widens_authority, false);
    assert.equal(result.widens_scope, false);
    assert.equal(result.widens_privacy_policy, false);
  }
});

test("model judgment: a model cannot widen authority, scope or privacy policy", () => {
  const widenings = [
    { seam: "inbound_document_kind", label: "lease", granted_authority: "release_admin" },
    { seam: "inbound_document_kind", label: "lease", delegation: { granted_to: "dell" } },
    { seam: "inbound_document_kind", label: "lease", representation_side: "landlord" },
    { seam: "inbound_document_kind", label: "lease", data_class_override: "phi" },
    { seam: "inbound_document_kind", label: "lease", privacy_route: "skip" },
    { seam: "inbound_document_kind", label: "lease", extra_capabilities: ["deal.write"] },
    { seam: "inbound_document_kind", label: "lease", tenant: "another-org" },
  ];
  for (const proposal of widenings) {
    const result = applyModelClassification(proposal);
    assert.equal(result.decision, "refuse", JSON.stringify(proposal));
    assert.equal(result.reason_id, "model_widening_refused");
  }
  const outside = applyModelClassification({ seam: "inbound_document_kind", label: "listing_agreement" });
  assert.equal(outside.decision, "refuse");
  assert.equal(outside.reason_id, "model_label_outside_seam");
  throwsCode(() => applyModelClassification({ seam: "authorization_decision", label: "allow" }),
    "unknown_model_seam");
  throwsCode(() => applyModelClassification({ seam: "read_freshness_hint", label: "unknown", note: "hi" }),
    "unknown_field");
});

// ------------------------------------------- policy digest, drift, freezing

test("the policy preimage is closed, versioned and carries the eight settled decisions", () => {
  const preimage = v5BoundaryPolicyPreimage();
  assert.equal(preimage.schema_version, V5_BOUNDARY_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_BOUNDARY_POLICY_VERSION);
  assert.equal(preimage.tenant, ORGANIZATION_TENANT_ID);
  assert.deepEqual(preimage.decisions.map(d => d.decision_id), [...V5_SETTLED_DECISION_IDS]);
  assert.equal(preimage.decisions.length, 8);
  for (const entry of preimage.decisions) {
    assert.equal(entry.source_evidence_digest, REVIEWED_DECISION_BINDING[entry.decision_id]);
    assert.equal(entry.settled_requirement, V5_SETTLED_DECISIONS[entry.decision_id].settled_requirement);
  }
  assert.equal(preimage.read_continuity.accepts_offline_mutation, false);
  assert.equal(preimage.local_platform.nodes_may_hold_unique_authority, false);
  assert.equal(preimage.model_judgment.may_widen_authority, false);
  assert.deepEqual(preimage.representation_scope.exposed_sides, ["tenant", "buyer"]);
});

test("the policy digest is deterministic and taken over the exact canonical bytes", () => {
  const first = v5BoundaryPolicyDigest();
  const second = v5BoundaryPolicyDigest();
  assert.equal(first, second);
  assert.match(first, /^sha256:[0-9a-f]{64}$/);
  const bytes = v5BoundaryPolicyCanonicalBytes();
  assert.equal(bytes, canonicalJson(v5BoundaryPolicyPreimage()));
  assert.equal(digest(bytes), first);
  // Nothing situational is bound: a second preimage object hashes identically.
  assert.equal(digest(v5BoundaryPolicyPreimage()), first);
  assert.deepEqual(JSON.parse(bytes), JSON.parse(JSON.stringify(v5BoundaryPolicyPreimage())));
});

test("decision or source-digest drift refuses", () => {
  assert.equal(assertSettledDecisionBinding(reviewedBinding()), true);

  const drifted = reviewedBinding();
  drifted.decisions["Q033.D1"] = { source_evidence_digest: "c".repeat(64) };
  throwsCode(() => assertSettledDecisionBinding(drifted), "decision_binding_drift");

  const missing = reviewedBinding();
  delete missing.decisions["Q141.D1"];
  throwsCode(() => assertSettledDecisionBinding(missing), "decision_binding_drift");

  const extra = reviewedBinding();
  extra.decisions["Q999.D1"] = { source_evidence_digest: "d".repeat(64) };
  throwsCode(() => assertSettledDecisionBinding(extra), "decision_binding_drift");

  const wrongSubset = reviewedBinding();
  wrongSubset.decision_subset_canonical_sha256 = "e".repeat(64);
  throwsCode(() => assertSettledDecisionBinding(wrongSubset), "decision_binding_drift");

  const rewordedText = reviewedBinding();
  rewordedText.decisions["Q073.D1"] = {
    source_evidence_digest: REVIEWED_DECISION_BINDING["Q073.D1"],
    settled_requirement: "V5 may represent landlords once convenient.",
  };
  throwsCode(() => assertSettledDecisionBinding(rewordedText), "decision_binding_drift");

  throwsCode(() => assertSettledDecisionBinding({ decisions: {}, notes: "x" }), "unknown_field");
});

test("the projection reports the digest and accepts nothing", () => {
  const projection = v5BoundaryProjection();
  assert.equal(projection.policy_digest, v5BoundaryPolicyDigest());
  assert.equal(projection.portfolio_constitution_accepted, false);
  assert.equal(projection.gate_zero_transitioned, false);
  assert.equal(projection.global_no_phi_boundary_receipt_present, false);
  assert.equal(projection.accepts_anything, false);
  assert.deepEqual(projection.decision_ids, [...V5_SETTLED_DECISION_IDS]);
  assert.deepEqual(
    v5BoundaryProjection({ expected_policy_digest: projection.policy_digest }), projection);
  throwsCode(() => v5BoundaryProjection({ expected_policy_digest: `sha256:${"f".repeat(64)}` }),
    "stale_expected_digest");
  throwsCode(() => v5BoundaryProjection({ expected_policy_digest: "nope" }), "invalid_expected_digest");
});

test("the policy is frozen: mutating it throws rather than silently rebinding", () => {
  assert.throws(() => { V5_SETTLED_DECISIONS["Q033.D1"] = { source_evidence_digest: "x" }; }, TypeError);
  assert.throws(() => { V5_SETTLED_DECISIONS["Q033.D1"].settled_requirement = "PHI is fine"; }, TypeError);
  assert.throws(() => { V5_ACTIONS["system.policy"].authority_class = "ordinary_business"; }, TypeError);
  assert.throws(() => { V5_ACTION_KEYS.push("system.invent"); }, TypeError);
  assert.throws(() => { V5_PROHIBITED_DATA_CLASSES.pop(); }, TypeError);
  assert.throws(() => { V5_NO_EFFECTS.creates_effect = true; }, TypeError);
  assert.throws(() => { V5_LOCAL_CAPABILITIES.browser_automation.fallback = "none"; }, TypeError);
  // The digest is unchanged by every attempt above.
  assert.equal(v5BoundaryPolicyDigest(), digest(v5BoundaryPolicyPreimage()));
});

test("frozen inputs are accepted and results are frozen", () => {
  const request = Object.freeze({
    actor: JOE, action: "business.read_deal", tenant: ORGANIZATION_TENANT_ID, now: NOW,
    controls: Object.freeze({
      deal_owner_slug: "joe", policy_scope: Object.freeze(["business.read_deal"]),
      capabilities: Object.freeze(["deal.read"]),
    }),
  });
  const result = evaluateActorAuthority(request);
  assert.equal(result.decision, "allow");
  assert.ok(Object.isFrozen(result));
  assert.throws(() => { result.decision = "refuse"; }, TypeError);
  // The module read the request and did not touch it.
  assert.deepEqual(Object.keys(request).sort(), ["action", "actor", "controls", "now", "tenant"]);
  for (const frozen of [
    evaluateRepresentationScope(Object.freeze({ representation_side: "tenant", intent: "expose" })),
    evaluateReadContinuity(Object.freeze({ operation_kind: "read", connectivity: "online" })),
    evaluateLocalPlatform(Object.freeze({
      node: "mac-studio", node_state: "available", capability: "development_capacity",
    })),
    evaluatePrivacyBoundary(Object.freeze({ data_classes: Object.freeze(["market_comp"]) })),
    applyModelClassification(Object.freeze({ seam: "read_freshness_hint", label: "unknown" })),
  ]) {
    assert.ok(Object.isFrozen(frozen));
    assert.deepEqual(frozen.effects, V5_NO_EFFECTS);
  }
});

test("the module performs no database, network, provider, filesystem or scheduling effect", () => {
  const source = readFileSync(SRC_PATH, "utf8");
  const forbidden = [
    /\bnode:fs\b/, /\bnode:net\b/, /\bnode:http\b/, /\bnode:https\b/, /\bnode:dns\b/,
    /\bnode:child_process\b/, /\bnode:worker_threads\b/, /\bnode:cluster\b/,
    /\bchild_process\b/, /\bfetch\s*\(/, /\bXMLHttpRequest\b/, /\bprocess\.env\b/,
    /\bDate\.now\b/, /\bsetTimeout\b/, /\bsetInterval\b/, /\brequire\s*\(/,
    /\bimport\s*\(/, /\bglobalThis\b/, /\bpg\b\s*\)/, /\bquery\s*\(/,
  ];
  for (const pattern of forbidden) {
    assert.ok(!pattern.test(source), `module source must not contain ${pattern}`);
  }
  const imports = [...source.matchAll(/^import\s[^;]*?from\s+"([^"]+)";/gm)].map(m => m[1]).sort();
  assert.deepEqual(imports, ["./artifact-trust.js", "./identity.js"]);
  // Effects are asserted in the record too, not only in the source.
  assert.equal(V5_NO_EFFECTS.creates_effect, false);
  for (const value of Object.values(V5_NO_EFFECTS)) {
    assert.ok(value === false || value === 0);
  }
});
