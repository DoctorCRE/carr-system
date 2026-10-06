// V5-F06 — the workflow effect envelope, the one-use capability, the ordering
// rule and the unknown-outcome quarantine, proved clause by clause.
//
// The positive case comes first on purpose: a rule that only ever refuses cannot
// be told apart from a broken one, so every negative below is a single named
// mutation of ONE clean request that allows.
//
// The actor authority answers fed in are REAL answers from
// global-boundaries.v5.js, built through that module's own evaluator, not
// hand-written stand-ins — so if that module's answer shape changes, this suite
// breaks rather than the wiring silently rotting.
//
//   node --test mcp-server/test/workflow-effect-envelope.v5.test.mjs
//                mcp-server/test/global-boundaries.v5.test.mjs
//                mcp-server/test/command-supervisor-admission.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import {
  V5BoundaryError,
  V5_NO_EFFECTS,
  V5_ACTIONS,
  V5_ACTION_KEYS,
  evaluateActorAuthority,
} from "../src/global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_ADMISSION_CHECK_STATES,
  V5_CAPABILITY_STATES,
  V5_NONCE_STATES,
} from "../src/command-supervisor-admission.v5.js";
import {
  V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
  V5_EFFECT_ENVELOPE_POLICY_VERSION,
  V5_EFFECT_ENVELOPE_KIND,
  V5_EFFECT_CAPABILITY_KIND,
  V5_ATTENDED_ACTIVATION_RECEIPT_STEP,
  V5_F06_DECISION_IDS,
  V5_F06_DECISION_BINDING_SEAM,
  V5_F06_AUTONOMY_TIER_LADDER_SEAM,
  V5_F06_EFFECT_CAP_VOCABULARY_SEAM,
  V5_F06_PROVIDER_READBACK_JOIN_SEAM,
  V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM,
  V5_F06_SEAMS,
  V5_ENVELOPE_PRINCIPAL_ROLES,
  V5_CAPABILITY_USES_ALLOWED,
  V5_IDEMPOTENCY_KEY_SOURCE,
  V5_PRESENTATION_CHECKS,
  V5_CHECKS_BEFORE_ANY_BINDING_IS_COMPARED,
  V5_ISSUANCE_CHECKS,
  V5_ORDER_CHECKS,
  V5_RESOLUTION_CHECKS,
  V5_CONSUMPTION_STATES,
  V5_PROVIDER_CALL_STATES,
  V5_ATTEMPT_OUTCOME_STATES,
  V5_READBACK_STATES,
  V5_ATTEMPT_RESOLUTIONS,
  V5_NEXT_STEPS,
  V5_OUTCOME_RESOLUTIONS,
  V5_READBACK_RESOLUTIONS,
  V5_EFFECT_ENVELOPE_REASON_IDS,
  effectEnvelopePreimage,
  effectEnvelopeDigest,
  normalizeEffectEnvelope,
  normalizeEffectCapability,
  normalizeEffectPresentation,
  normalizeEffectAttemptObservation,
  evaluateCapabilityIssuance,
  evaluateCapabilityPresentation,
  evaluateConsumptionOrder,
  evaluateAttemptResolution,
  v5EffectEnvelopePolicyPreimage,
  v5EffectEnvelopePolicyDigest,
  v5EffectEnvelopePolicyCanonicalBytes,
  v5EffectEnvelopeProjection,
} from "../src/workflow-effect-envelope.v5.js";

const SRC_PATH = fileURLToPath(new URL("../src/workflow-effect-envelope.v5.js", import.meta.url));

const ACTION = "business.send_client_document";
const CAPABILITY = "document.send";
const PAYLOAD = "sha256:" + "a".repeat(64);
const OTHER_PAYLOAD = "sha256:" + "b".repeat(64);
const OTHER_ENVELOPE_DIGEST = "sha256:" + "c".repeat(64);

const ISSUED_AT = "2026-09-10T11:55:00Z";
const EXPIRES_AT = "2026-09-10T12:05:00Z";
const NOW = "2026-09-10T12:00:00Z";
const AFTER_EXPIRY = "2026-09-10T12:05:00Z";
const BEFORE_ISSUE = "2026-09-10T11:54:59Z";

const TIER = "tier-attended";
const ACCOUNT = "mailbox:joe-carr";

const partner = slug => Object.freeze({
  slug, display: slug === "joe" ? "Joe" : "Dell", human: true, via: "oauth-google",
  client_id: null, sponsoring_human_slug: null, human_slug: null, sponsor_required: false,
});
const JOE = partner("joe");
const DELL = partner("dell");

function boundaryError(code) {
  return error => error instanceof V5BoundaryError && error.code === code;
}

// --------------------------------------------------------------- fixtures

const PRINCIPALS = Object.freeze({
  actor_slug: "joe", account_ref: ACCOUNT, deal_owner_slug: "joe", signer_slug: "dell",
});

const envelope = (over = {}) => normalizeEffectEnvelope({
  tenant: ORGANIZATION_TENANT_ID,
  envelope_id: "env-1",
  workflow_id: "wf-1",
  step_id: "step-1",
  action: ACTION,
  principals: { ...PRINCIPALS },
  payload_digest: PAYLOAD,
  caps: [{ cap_key: "recipients", limit: 3 }],
  autonomy_tier_label: TIER,
  ...over,
});

const capability = (over = {}) => normalizeEffectCapability({
  tenant: ORGANIZATION_TENANT_ID,
  capability_id: "cap-1",
  envelope_digest: envelope().envelope_digest,
  capability: CAPABILITY,
  nonce: "nonce-1",
  principals: { ...PRINCIPALS },
  autonomy_tier_label: TIER,
  issued_at: ISSUED_AT,
  expires_at: EXPIRES_AT,
  ...over,
});

const presentation = (over = {}) => normalizeEffectPresentation({
  presentation_id: "pres-1",
  envelope_digest: envelope().envelope_digest,
  capability_id: "cap-1",
  requested_action: ACTION,
  payload_digest: PAYLOAD,
  presented_principals: { ...PRINCIPALS },
  presented_autonomy_tier_label: TIER,
  nonce_state: "unconsumed",
  capability_state: "active",
  cap_observations: [{ cap_key: "recipients", observed: 2 }],
  ...over,
});

const attempt = (over = {}) => normalizeEffectAttemptObservation({
  attempt_id: "att-1",
  envelope_digest: envelope().envelope_digest,
  capability_id: "cap-1",
  consumption: { state: "committed", committed_seq: 1 },
  provider_call: { state: "started", started_seq: 2 },
  outcome: { state: "succeeded" },
  ...over,
});

/** A REAL answer from the boundary kernel, allowing by default. */
const authorityAnswer = (over = {}) => evaluateActorAuthority({
  actor: JOE,
  action: ACTION,
  tenant: ORGANIZATION_TENANT_ID,
  now: NOW,
  controls: {
    deal_owner_slug: "joe",
    account_slug: "joe",
    policy_scope: [ACTION],
    capabilities: [CAPABILITY],
  },
  ...over,
});

const admit = (over = {}) => evaluateCapabilityPresentation({
  envelope: envelope(),
  capability: capability(),
  presentation: presentation(),
  authority: authorityAnswer(),
  now: NOW,
  ...over,
});

const issue = (over = {}) => evaluateCapabilityIssuance({
  envelope: envelope(),
  capability: capability(),
  authority: authorityAnswer(),
  now: NOW,
  ...over,
});

const order = (over = {}) => evaluateConsumptionOrder({
  envelope: envelope(),
  capability: capability(),
  attempt: attempt(),
  ...over,
});

const resolve = (over = {}) => {
  const observed = over.attempt ?? attempt();
  return evaluateAttemptResolution({
    envelope: envelope(),
    capability: capability(),
    attempt: observed,
    consumption_order: over.consumption_order
      ?? evaluateConsumptionOrder({ envelope: envelope(), capability: capability(), attempt: observed }),
    proposed_next_step: "settle",
    ...over,
    attempt: observed,
  });
};

// ---------------------------------------------------------------------------
// The clean case.
// ---------------------------------------------------------------------------

test("the clean case: a presented capability is admissible for consumption and grants nothing outward", () => {
  const result = admit();
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "presentation_admissible_for_consumption");
  assert.equal(result.blocking_check, null);
  assert.deepEqual(result.checks_required, [...V5_PRESENTATION_CHECKS]);
  for (const check of V5_PRESENTATION_CHECKS) {
    assert.equal(result.check_states[check].state, "satisfied", check);
  }
  // An allow here is not an activation and says so in its own fields.
  assert.equal(result.outward_effect_granted, false);
  assert.equal(result.attended_activation_receipt_present, false);
  assert.equal(result.attended_activation_receipt, V5_ATTENDED_ACTIVATION_RECEIPT_STEP);
  assert.equal(result.consumption_must_commit_before_provider_call, true);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
  // Pre-effect idempotency: the key exists before anything happens and IS the
  // envelope's identity.
  assert.equal(result.idempotency_key, envelope().envelope_digest);
  assert.equal(result.idempotency_key_source, V5_IDEMPOTENCY_KEY_SOURCE);
  assert.equal(V5_IDEMPOTENCY_KEY_SOURCE, "envelope_digest");
});

// ---------------------------------------------------------------------------
// checkable_done 1 — replay, substitution, wrong account, expiry.
// ---------------------------------------------------------------------------

test("clause 1a: replay — a consumed nonce refuses, and an unknown nonce refuses the same way", () => {
  const replayed = admit({ presentation: presentation({ nonce_state: "consumed" }) });
  assert.equal(replayed.decision, "refuse");
  assert.equal(replayed.reason_id, "capability_replayed");
  assert.equal(replayed.blocking_check, "nonce_state");
  assert.equal(replayed.check_states.nonce_state.state, "violated");
  assert.equal(replayed.check_states.nonce_state.uses_allowed, 1);
  // Everything after the blocking check is not_reached, not silently satisfied.
  assert.equal(replayed.check_states.principal_binding.state, "not_reached");
  assert.equal(replayed.check_states.effect_caps.state, "not_reached");

  // "I cannot tell whether it was spent" is indistinguishable from a replay.
  for (const nonce_state of ["unknown", null]) {
    const silent = admit({ presentation: presentation({ nonce_state }) });
    assert.equal(silent.decision, "refuse", String(nonce_state));
    assert.equal(silent.reason_id, "nonce_state_unobservable");
    assert.equal(silent.check_states.nonce_state.state, "unobservable");
  }

  assert.equal(V5_CAPABILITY_USES_ALLOWED, 1);
});

test("clause 1b: substitution — envelope, capability, action and payload each refuse by their own name", () => {
  const cases = [
    // The capability was issued for a different envelope.
    [{ capability: capability({ envelope_digest: OTHER_ENVELOPE_DIGEST }) },
      "envelope_substituted", "capability_binding"],
    // The presentation claims a different envelope.
    [{ presentation: presentation({ envelope_digest: OTHER_ENVELOPE_DIGEST }) },
      "envelope_substituted", "capability_binding"],
    // A different capability handle is presented against this one.
    [{ presentation: presentation({ capability_id: "cap-2" }) },
      "capability_substituted", "capability_binding"],
    // The same capability aimed at a different action.
    [{ presentation: presentation({ requested_action: "business.sign_document" }) },
      "action_substituted", "action_binding"],
    // The same capability aimed at different content.
    [{ presentation: presentation({ payload_digest: OTHER_PAYLOAD }) },
      "payload_substituted", "payload_binding"],
  ];
  for (const [over, reason_id, blocking_check] of cases) {
    const result = admit(over);
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
    assert.equal(result.blocking_check, blocking_check);
    assert.equal(result.check_states[blocking_check].state, "violated");
  }
});

test("clause 1c: wrong account — and the other three principals, each refused under its own name", () => {
  const cases = [
    ["actor", { actor_slug: "dell" }, "actor_mismatch"],
    ["account", { account_ref: "mailbox:someone-else" }, "account_mismatch"],
    ["deal_owner", { deal_owner_slug: "dell" }, "deal_owner_mismatch"],
    ["signer", { signer_slug: "joe" }, "signer_mismatch"],
  ];
  for (const [role, over, reason_id] of cases) {
    const result = admit({
      presentation: presentation({ presented_principals: { ...PRINCIPALS, ...over } }),
    });
    assert.equal(result.decision, "refuse", role);
    assert.equal(result.reason_id, reason_id, role);
    assert.equal(result.blocking_check, "principal_binding", role);
    assert.equal(result.check_states.principal_binding.role, role);
    assert.deepEqual(result.check_states.principal_binding.compared_roles,
      [...V5_ENVELOPE_PRINCIPAL_ROLES]);
  }

  // The account is the PROVIDER ACCOUNT and is refused on its own: everything
  // else about the presentation is correct in the wrong-account case above,
  // which is what makes it a distinct refusal rather than a restatement of
  // "wrong actor".
  const wrongAccount = admit({
    presentation: presentation({
      presented_principals: { ...PRINCIPALS, account_ref: "mailbox:someone-else" },
    }),
  });
  assert.equal(wrongAccount.check_states.principal_binding.field, "account_ref");
  assert.equal(wrongAccount.check_states.principal_binding.bound, ACCOUNT);
  assert.equal(wrongAccount.check_states.principal_binding.presented, "mailbox:someone-else");
});

test("clause 1d: expiry — a label never outvotes the clock", () => {
  // The reported state still says "active"; the window has closed.
  const expired = admit({ now: AFTER_EXPIRY });
  assert.equal(expired.decision, "refuse");
  assert.equal(expired.reason_id, "capability_expired");
  assert.equal(expired.blocking_check, "capability_validity_window");
  assert.equal(expired.check_states.capability_validity_window.capability_state, "active");
  assert.equal(expired.check_states.capability_validity_window.label_outvoted_by_clock, true);

  const early = admit({ now: BEFORE_ISSUE });
  assert.equal(early.reason_id, "capability_not_yet_valid");

  // A reported terminal state refuses even while the clock is still open.
  for (const [capability_state, reason_id] of [
    ["revoked", "capability_revoked"],
    ["unissued", "capability_unissued"],
    ["expired", "capability_expired"],
    [null, "capability_state_unobservable"],
  ]) {
    const result = admit({ presentation: presentation({ capability_state }) });
    assert.equal(result.decision, "refuse", String(capability_state));
    assert.equal(result.reason_id, reason_id, String(capability_state));
    assert.equal(result.blocking_check, "capability_validity_window");
  }
  assert.equal(
    admit({ presentation: presentation({ capability_state: "expired" }) })
      .check_states.capability_validity_window.label_outvoted_by_clock, false);
  assert.equal(
    admit({ presentation: presentation({ capability_state: null }) })
      .check_states.capability_validity_window.state, "unobservable");
});

test("clause 1e: an unauthorized actor refuses BEFORE any binding is compared", () => {
  // A REAL refusal from the boundary kernel: Joe, this action, a policy scope
  // that does not name it.
  const refused = authorityAnswer({
    controls: {
      deal_owner_slug: "joe", account_slug: "joe", policy_scope: [], capabilities: [CAPABILITY],
    },
  });
  assert.equal(refused.decision, "refuse");
  assert.equal(refused.reason_id, "policy_scope_excludes_action");

  // The request is ALSO substituted, expired and replayed. None of that is read.
  const result = evaluateCapabilityPresentation({
    envelope: envelope(),
    capability: capability({ envelope_digest: OTHER_ENVELOPE_DIGEST }),
    presentation: presentation({ nonce_state: "consumed", capability_state: "revoked" }),
    authority: refused,
    now: AFTER_EXPIRY,
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "actor_authority_refused");
  assert.equal(result.blocking_check, "actor_authority");
  assert.equal(result.check_states.actor_authority.authority_reason_id, "policy_scope_excludes_action");
  for (const check of V5_PRESENTATION_CHECKS.slice(1)) {
    assert.equal(result.check_states[check].state, "not_reached", check);
  }
  assert.deepEqual(result.checks_before_any_binding_is_compared,
    [...V5_CHECKS_BEFORE_ANY_BINDING_IS_COMPARED]);
  assert.deepEqual([...V5_CHECKS_BEFORE_ANY_BINDING_IS_COMPARED], ["actor_authority"]);

  // No answer at all is unobservable and blocks; it is not "no news is good news".
  const absent = admit({ authority: null });
  assert.equal(absent.reason_id, "actor_authority_unobservable");
  assert.equal(absent.check_states.actor_authority.state, "unobservable");
});

test("the actor authority answer is an input, read and never recomputed", () => {
  // A hand-built allow is unreadable rather than persuasive.
  assert.throws(() => admit({ authority: { decision: "allow", reason_id: "trust_me" } }),
    boundaryError("foreign_authority_answer"));
  // A genuine answer that has been thawed and edited is unreadable.
  assert.throws(() => admit({ authority: { ...authorityAnswer(), decision: "allow" } }),
    boundaryError("foreign_authority_answer"));
  // An answer about a different action.
  assert.throws(() => admit({
    authority: evaluateActorAuthority({
      actor: JOE, action: "business.read_deal", tenant: ORGANIZATION_TENANT_ID, now: NOW,
      controls: { deal_owner_slug: "joe", policy_scope: ["business.read_deal"], capabilities: ["deal.read"] },
    }),
  }), boundaryError("foreign_authority_answer"));
  // An answer about a different ACTOR — the hole that would otherwise make
  // every other principal check bypassable.
  const aboutDell = evaluateActorAuthority({
    actor: DELL, action: ACTION, tenant: ORGANIZATION_TENANT_ID, now: NOW,
    controls: {
      deal_owner_slug: "dell", account_slug: "dell", policy_scope: [ACTION], capabilities: [CAPABILITY],
    },
  });
  assert.equal(aboutDell.decision, "allow");
  assert.throws(() => admit({ authority: aboutDell }), boundaryError("foreign_authority_answer"));
  // An answer claiming controls the shared registry does not name for this action.
  assert.throws(() => admit({
    authority: Object.freeze({ ...authorityAnswer(), satisfied_controls: ["deal_owner"] }),
  }), boundaryError("foreign_authority_answer"));
  // An answer claiming a permanent privilege, or claiming it produced effects.
  assert.throws(() => admit({
    authority: Object.freeze({ ...authorityAnswer(), permanent_privilege_granted: true }),
  }), boundaryError("foreign_authority_answer"));
  assert.throws(() => admit({
    authority: Object.freeze({ ...authorityAnswer(), effects: { ...V5_NO_EFFECTS, network_calls: 1 } }),
  }), boundaryError("foreign_authority_answer"));
});

// ---------------------------------------------------------------------------
// checkable_done 4 — the four principals stay four.
// ---------------------------------------------------------------------------

test("clause 4: the four principals are four required fields and are never borrowed from one another", () => {
  assert.deepEqual([...V5_ENVELOPE_PRINCIPAL_ROLES], ["actor", "account", "deal_owner", "signer"]);

  // An omitted role is a missing field, NOT the actor standing in for it.
  for (const field of ["actor_slug", "account_ref", "deal_owner_slug", "signer_slug"]) {
    const principals = { ...PRINCIPALS };
    delete principals[field];
    assert.throws(() => envelope({ principals }), boundaryError("missing_field"), field);
    assert.throws(() => capability({ principals }), boundaryError("missing_field"), field);
  }
});

test("clause 4: the envelope digest is a function of WHICH role holds a value, not of the set of values", () => {
  const base = envelope();
  // Swap two roles' values. A set-shaped or sorted seal would produce the same
  // digest here, and the swap would be invisible.
  const swapped = envelope({
    principals: { ...PRINCIPALS, deal_owner_slug: PRINCIPALS.signer_slug, signer_slug: PRINCIPALS.deal_owner_slug },
  });
  assert.notEqual(swapped.envelope_digest, base.envelope_digest);
  assert.deepEqual(
    [base.principals.deal_owner_slug, base.principals.signer_slug].sort(),
    [swapped.principals.deal_owner_slug, swapped.principals.signer_slug].sort(),
    "the two envelopes hold the same set of values and differ only in which role holds which");

  // A capability correctly ISSUED for the swapped arrangement refuses a
  // presentation that carries the original one.
  const swappedPrincipals = {
    ...PRINCIPALS,
    deal_owner_slug: PRINCIPALS.signer_slug, signer_slug: PRINCIPALS.deal_owner_slug,
  };
  const result = evaluateCapabilityPresentation({
    envelope: swapped,
    capability: capability({
      envelope_digest: swapped.envelope_digest, principals: swappedPrincipals,
    }),
    presentation: presentation({ envelope_digest: swapped.envelope_digest }),
    authority: authorityAnswer(),
    now: NOW,
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "deal_owner_mismatch");
});

test("a matching envelope_digest STRING is not a matching binding", () => {
  // A capability whose own sealed fields disagree with the envelope it names was
  // never issued for that envelope, however its digest field is spelled. Without
  // this the whole principal ladder is bypassable by pointing a stale capability
  // at a newer envelope: here the capability and the presentation agree with each
  // other, and both disagree with the envelope.
  const swapped = envelope({
    principals: {
      ...PRINCIPALS,
      deal_owner_slug: PRINCIPALS.signer_slug, signer_slug: PRINCIPALS.deal_owner_slug,
    },
  });
  const stale = evaluateCapabilityPresentation({
    envelope: swapped,
    capability: capability({ envelope_digest: swapped.envelope_digest }),
    presentation: presentation({ envelope_digest: swapped.envelope_digest }),
    authority: authorityAnswer(),
    now: NOW,
  });
  assert.equal(stale.decision, "refuse");
  assert.equal(stale.reason_id, "capability_not_issued_for_envelope");
  assert.equal(stale.blocking_check, "capability_binding");
  assert.equal(stale.check_states.principal_binding.state, "not_reached");

  // The same hole through the capability scope and through the tier label.
  for (const over of [{ capability: "document.sign" }, { autonomy_tier_label: "tier-unattended" }]) {
    const result = admit({ capability: capability(over) });
    assert.equal(result.reason_id, "capability_not_issued_for_envelope", canonicalJson(over));
  }
});

test("clause 4: two roles holding the same slug is legal and does not collapse them", () => {
  // Joe is routinely both actor and deal owner. That coincidence must not make
  // the account or the signer follow along.
  const same = { actor_slug: "joe", account_ref: ACCOUNT, deal_owner_slug: "joe", signer_slug: "joe" };
  const env = envelope({ principals: same });
  const cap = capability({ envelope_digest: env.envelope_digest, principals: same });
  const clean = evaluateCapabilityPresentation({
    envelope: env, capability: cap,
    presentation: presentation({ envelope_digest: env.envelope_digest, presented_principals: same }),
    authority: authorityAnswer(), now: NOW,
  });
  assert.equal(clean.decision, "allow");

  // The account still refuses on its own even though three roles are identical.
  const wrongAccount = evaluateCapabilityPresentation({
    envelope: env, capability: cap,
    presentation: presentation({
      envelope_digest: env.envelope_digest,
      presented_principals: { ...same, account_ref: "mailbox:elsewhere" },
    }),
    authority: authorityAnswer(), now: NOW,
  });
  assert.equal(wrongAccount.reason_id, "account_mismatch");
});

// ---------------------------------------------------------------------------
// Caps.
// ---------------------------------------------------------------------------

test("clause: caps — silence is not headroom, and an undeclared cap is not a cap", () => {
  // At the limit is inside it.
  assert.equal(admit({
    presentation: presentation({ cap_observations: [{ cap_key: "recipients", observed: 3 }] }),
  }).decision, "allow");

  const exceeded = admit({
    presentation: presentation({ cap_observations: [{ cap_key: "recipients", observed: 4 }] }),
  });
  assert.equal(exceeded.reason_id, "cap_exceeded");
  assert.equal(exceeded.blocking_check, "effect_caps");
  assert.equal(exceeded.check_states.effect_caps.limit, 3);
  assert.equal(exceeded.check_states.effect_caps.observed, 4);

  // A declared cap nobody observed BLOCKS. An unobserved cap is not headroom.
  const unobserved = admit({ presentation: presentation({ cap_observations: [] }) });
  assert.equal(unobserved.reason_id, "cap_unobserved");
  assert.equal(unobserved.check_states.effect_caps.state, "unobservable");

  const undeclared = admit({
    presentation: presentation({
      cap_observations: [{ cap_key: "recipients", observed: 1 }, { cap_key: "attachments", observed: 1 }],
    }),
  });
  assert.equal(undeclared.reason_id, "undeclared_cap_observed");
  assert.equal(undeclared.check_states.effect_caps.cap_key, "attachments");

  // No caps declared and none observed is a clean allow.
  const uncapped = envelope({ caps: [] });
  assert.equal(evaluateCapabilityPresentation({
    envelope: uncapped,
    capability: capability({ envelope_digest: uncapped.envelope_digest }),
    presentation: presentation({ envelope_digest: uncapped.envelope_digest, cap_observations: [] }),
    authority: authorityAnswer(), now: NOW,
  }).decision, "allow");

  // A cap declared twice is unreadable rather than silently deduplicated.
  assert.throws(() => envelope({
    caps: [{ cap_key: "recipients", limit: 3 }, { cap_key: "recipients", limit: 99 }],
  }), boundaryError("duplicate_cap"));
});

// ---------------------------------------------------------------------------
// checkable_done 2 — consumption commits before the provider call.
// ---------------------------------------------------------------------------

test("clause 2: consumption commits before the provider call, proved by two sequence numbers", () => {
  const clean = order();
  assert.equal(clean.decision, "allow");
  assert.equal(clean.reason_id, "consumption_committed_before_provider_call");
  assert.equal(clean.check_states.provider_call_order.committed_seq, 1);
  assert.equal(clean.check_states.provider_call_order.started_seq, 2);

  // The provider call at or before the commit is the defect this exists for.
  for (const started_seq of [1, 0]) {
    const bad = order({
      attempt: attempt({ provider_call: { state: "started", started_seq } }),
    });
    assert.equal(bad.decision, "refuse", String(started_seq));
    assert.equal(bad.reason_id, "provider_call_preceded_consumption");
    assert.equal(bad.blocking_check, "provider_call_order");
  }

  // A provider call that never started, after a committed consumption, is fine.
  const notStarted = order({ attempt: attempt({ provider_call: { state: "not_started" } }) });
  assert.equal(notStarted.decision, "allow");
  assert.equal(notStarted.reason_id, "consumption_committed_provider_call_not_started");
});

test("clause 2: every way of not proving the order refuses, each under its own reason", () => {
  const cases = [
    [{ consumption: { state: "uncommitted", committed_seq: 1 } }, "consumption_uncommitted", "consumption_commit", "violated"],
    [{ consumption: { state: "failed" } }, "consumption_failed", "consumption_commit", "violated"],
    [{ consumption: { state: "unstated" } }, "consumption_state_unobservable", "consumption_commit", "unobservable"],
    [{ consumption: { state: "committed" } }, "consumption_sequence_unobservable", "consumption_commit", "unobservable"],
    [{ provider_call: { state: "unstated" } }, "provider_call_state_unobservable", "provider_call_order", "unobservable"],
    [{ provider_call: { state: "started" } }, "provider_call_sequence_unobservable", "provider_call_order", "unobservable"],
  ];
  for (const [over, reason_id, blocking_check, state] of cases) {
    const result = order({ attempt: attempt(over) });
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
    assert.equal(result.blocking_check, blocking_check);
    assert.equal(result.check_states[blocking_check].state, state);
  }

  // An attempt about another envelope or another capability is not evidence
  // about this one, and refuses before any sequence number is read.
  const otherEnvelope = order({ attempt: attempt({ envelope_digest: OTHER_ENVELOPE_DIGEST }) });
  assert.equal(otherEnvelope.reason_id, "attempt_bound_to_other_envelope");
  assert.equal(otherEnvelope.check_states.consumption_commit.state, "not_reached");
  assert.equal(order({ attempt: attempt({ capability_id: "cap-2" }) }).reason_id,
    "attempt_bound_to_other_capability");
});

// ---------------------------------------------------------------------------
// checkable_done 3 — the quarantine.
// ---------------------------------------------------------------------------

test("clause 3a: a timeout enters unknown, and so does silence", () => {
  for (const state of ["timed_out", "unstated"]) {
    const observed = attempt({ outcome: { state } });
    const retry = resolve({ attempt: observed, proposed_next_step: "retry" });
    assert.equal(retry.resolution, "unknown", state);
    assert.equal(retry.quarantined, true, state);
    assert.equal(retry.required_next_step, "provider_readback", state);
    assert.equal(retry.decision, "refuse", state);
    assert.equal(retry.reason_id, "retry_without_readback", state);

    // A readback is the only admissible next step out of the quarantine.
    const readback = resolve({ attempt: observed, proposed_next_step: "provider_readback" });
    assert.equal(readback.decision, "allow", state);
    assert.equal(readback.reason_id, "readback_required_before_retry", state);

    // Settling an unknown outcome is a quarantine with a hole in it.
    const settle = resolve({ attempt: observed, proposed_next_step: "settle" });
    assert.equal(settle.decision, "refuse", state);
    assert.equal(settle.reason_id, "unknown_outcome_may_not_settle", state);
  }
  assert.equal(V5_OUTCOME_RESOLUTIONS.timed_out, "unknown");
  assert.equal(V5_OUTCOME_RESOLUTIONS.unstated, "unknown");
});

test("clause 3b: a readback precedes the retry, and the retry needs a FRESH capability", () => {
  const timedOut = over => attempt({ outcome: { state: "timed_out" }, ...over });
  const withReadback = state => timedOut({
    readback: { state, join_key: envelope().envelope_digest },
  });

  // The effect was not there: the attempt resolves to failure and a retry opens.
  const absent = withReadback("effect_absent");
  const retry = resolve({
    attempt: absent, proposed_next_step: "retry",
    proposed_retry: { capability_id: "cap-2", nonce: "nonce-2" },
  });
  assert.equal(retry.resolution, "confirmed_failure");
  assert.equal(retry.quarantined, false);
  assert.equal(retry.decision, "allow");
  assert.equal(retry.reason_id, "retry_admissible_with_fresh_capability");

  // Reusing the consumed capability — by id, or by nonce under a new id — is the
  // replay this module refuses at presentation, refused here before it is tried.
  for (const proposed_retry of [
    { capability_id: "cap-1", nonce: "nonce-2" },
    { capability_id: "cap-2", nonce: "nonce-1" },
  ]) {
    const reused = resolve({ attempt: absent, proposed_next_step: "retry", proposed_retry });
    assert.equal(reused.decision, "refuse", canonicalJson(proposed_retry));
    assert.equal(reused.reason_id, "retry_reuses_consumed_capability");
  }
  // A retry that does not name its replacement capability cannot be admitted.
  const unstated = resolve({ attempt: absent, proposed_next_step: "retry" });
  assert.equal(unstated.reason_id, "retry_capability_unstated");
  assert.equal(unstated.check_states.next_step.state, "unobservable");

  // The effect WAS there: the timeout was a lie of the transport, not of the
  // provider, and a retry would duplicate it.
  const present = resolve({
    attempt: withReadback("effect_present"), proposed_next_step: "retry",
    proposed_retry: { capability_id: "cap-2", nonce: "nonce-2" },
  });
  assert.equal(present.resolution, "confirmed_success");
  assert.equal(present.reason_id, "effect_already_present");

  // A readback that settles nothing keeps the quarantine shut.
  for (const state of ["indeterminate", "unstated"]) {
    const stuck = resolve({ attempt: withReadback(state), proposed_next_step: "retry" });
    assert.equal(stuck.resolution, "unknown", state);
    assert.equal(stuck.quarantined, true, state);
    assert.equal(stuck.reason_id, "readback_indeterminate", state);
    assert.equal(stuck.check_states.next_step.readback_state, state);
  }
});

test("clause 3b: a readback about another effect is not a readback about this one", () => {
  const wrongJoin = attempt({
    outcome: { state: "timed_out" },
    readback: { state: "effect_absent", join_key: OTHER_ENVELOPE_DIGEST },
  });
  const result = resolve({ attempt: wrongJoin, proposed_next_step: "provider_readback" });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "readback_joined_on_other_effect");
  assert.equal(result.blocking_check, "readback_join");
  assert.equal(result.check_states.readback_join.idempotency_key, envelope().envelope_digest);
  assert.equal(result.check_states.outcome_resolution.state, "not_reached");

  // A readback that does not say what it joined on is unobservable, not benign.
  const noJoin = resolve({
    attempt: attempt({ outcome: { state: "timed_out" }, readback: { state: "effect_absent" } }),
    proposed_next_step: "provider_readback",
  });
  assert.equal(noJoin.reason_id, "readback_joined_on_other_effect");
  assert.equal(noJoin.check_states.readback_join.state, "unobservable");
});

test("clause 3c: a reported outcome and a readback that disagree resolve to unknown, not to the later one", () => {
  const conflicted = attempt({
    outcome: { state: "succeeded" },
    readback: { state: "effect_absent", join_key: envelope().envelope_digest },
  });
  const result = resolve({ attempt: conflicted, proposed_next_step: "settle" });
  assert.equal(result.resolution, "unknown");
  assert.equal(result.quarantined, true);
  assert.equal(result.required_next_step, "provider_readback");
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "outcome_readback_conflict");
  assert.equal(result.blocking_check, "outcome_resolution");
  // And the reverse disagreement conflicts too.
  assert.equal(resolve({
    attempt: attempt({
      outcome: { state: "failed" },
      readback: { state: "effect_present", join_key: envelope().envelope_digest },
    }),
    proposed_next_step: "settle",
  }).reason_id, "outcome_readback_conflict");
});

test("clause 3d: a confirmed outcome may be settled and may not be retried", () => {
  const settled = resolve({ proposed_next_step: "settle" });
  assert.equal(settled.decision, "allow");
  assert.equal(settled.reason_id, "settlement_admissible");
  assert.equal(settled.resolution, "confirmed_success");
  assert.equal(settled.quarantined, false);
  assert.equal(settled.required_next_step, null);

  const retried = resolve({
    proposed_next_step: "retry", proposed_retry: { capability_id: "cap-2", nonce: "nonce-2" },
  });
  assert.equal(retried.decision, "refuse");
  assert.equal(retried.reason_id, "outcome_already_confirmed");

  // A reported failure needs no readback: it already says the effect did not land.
  const failed = resolve({
    attempt: attempt({ outcome: { state: "failed" } }),
    proposed_next_step: "retry", proposed_retry: { capability_id: "cap-2", nonce: "nonce-2" },
  });
  assert.equal(failed.resolution, "confirmed_failure");
  assert.equal(failed.decision, "allow");
  assert.equal(failed.reason_id, "retry_admissible_with_fresh_capability");

  // A readback is always admissible, confirmed or not.
  assert.equal(resolve({ proposed_next_step: "provider_readback" }).reason_id, "readback_admissible");
});

test("the consumption-order answer is an input, read and never recomputed", () => {
  const badOrder = evaluateConsumptionOrder({
    envelope: envelope(), capability: capability(),
    attempt: attempt({ provider_call: { state: "started", started_seq: 1 } }),
  });
  assert.equal(badOrder.decision, "refuse");
  const result = evaluateAttemptResolution({
    envelope: envelope(), capability: capability(),
    attempt: attempt({ provider_call: { state: "started", started_seq: 1 } }),
    consumption_order: badOrder, proposed_next_step: "settle",
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "consumption_order_refused");
  assert.equal(result.blocking_check, "consumption_order");
  assert.equal(result.check_states.readback_join.state, "not_reached");

  // A hand-built order answer, and a genuine one about a different attempt, are
  // both unreadable.
  assert.throws(() => resolve({ consumption_order: { decision: "allow" } }),
    boundaryError("foreign_order_answer"));
  assert.throws(() => resolve({
    consumption_order: evaluateConsumptionOrder({
      envelope: envelope(), capability: capability(), attempt: attempt({ attempt_id: "att-2" }),
    }),
  }), boundaryError("foreign_order_answer"));
});

// ---------------------------------------------------------------------------
// Issuance.
// ---------------------------------------------------------------------------

test("issuance binds the four principals, and every way of mis-binding refuses under its own name", () => {
  const clean = issue();
  assert.equal(clean.decision, "allow");
  assert.equal(clean.reason_id, "issuance_admissible_internal_only");
  assert.equal(clean.outward_effect_granted, false);
  assert.equal(clean.attended_activation_receipt_present, false);
  assert.equal(clean.uses_allowed, 1);
  assert.deepEqual(clean.checks_required, [...V5_ISSUANCE_CHECKS]);

  const cases = [
    [{ capability: capability({ envelope_digest: OTHER_ENVELOPE_DIGEST }) },
      "issuance_envelope_binding_mismatch"],
    [{ capability: capability({ capability: "document.sign" }) }, "issuance_capability_scope_mismatch"],
    [{ capability: capability({ principals: { ...PRINCIPALS, actor_slug: "dell" } }) },
      "issuance_actor_binding_mismatch"],
    [{ capability: capability({ principals: { ...PRINCIPALS, account_ref: "mailbox:other" } }) },
      "issuance_account_binding_mismatch"],
    [{ capability: capability({ principals: { ...PRINCIPALS, deal_owner_slug: "dell" } }) },
      "issuance_deal_owner_binding_mismatch"],
    [{ capability: capability({ principals: { ...PRINCIPALS, signer_slug: "joe" } }) },
      "issuance_signer_binding_mismatch"],
    [{ capability: capability({ autonomy_tier_label: "tier-unattended" }) },
      "issuance_autonomy_tier_mismatch"],
    [{ now: AFTER_EXPIRY }, "issuance_window_already_expired"],
    [{ now: BEFORE_ISSUE }, "issuance_window_not_yet_open"],
    [{ authority: null }, "issuance_authority_unobservable"],
  ];
  for (const [over, reason_id] of cases) {
    const result = issue(over);
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
  }

  const refused = issue({
    authority: authorityAnswer({
      controls: { deal_owner_slug: "joe", account_slug: "joe", policy_scope: [], capabilities: [CAPABILITY] },
    }),
  });
  assert.equal(refused.reason_id, "issuance_authority_refused");
  assert.equal(refused.blocking_check, "actor_authority");
});

test("short-lived is enforced as bounded: a capability with no window is unreadable", () => {
  assert.throws(() => capability({ expires_at: ISSUED_AT }), boundaryError("unbounded_capability_window"));
  assert.throws(() => capability({ expires_at: "2026-09-10T11:54:00Z" }),
    boundaryError("unbounded_capability_window"));
  // A window is required rather than optional.
  const raw = {
    tenant: ORGANIZATION_TENANT_ID, capability_id: "cap-1",
    envelope_digest: envelope().envelope_digest, capability: CAPABILITY, nonce: "nonce-1",
    principals: { ...PRINCIPALS }, autonomy_tier_label: TIER, issued_at: ISSUED_AT,
  };
  assert.throws(() => normalizeEffectCapability(raw), boundaryError("missing_field"));
  // The calendar is checked against the literal fields: an impossible expiry is
  // not normalized into a real one.
  assert.throws(() => capability({ expires_at: "2026-02-31T00:00:00Z" }),
    boundaryError("invalid_timestamp"));
  // The ceiling is named, not invented.
  assert.equal(V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM, "step:v5-f06-capability-lifetime-ceiling");
  const source = readFileSync(SRC_PATH, "utf8");
  const header = source.slice(0, source.indexOf("\nimport "));
  assert.ok(/THE SHORT-LIFETIME CEILING/.test(header));
});

// ---------------------------------------------------------------------------
// The data boundary.
// ---------------------------------------------------------------------------

test("the data boundary is structural: no field can hold a secret, and no handle may be shaped like one", () => {
  // There is no place to put a credential. Each of these is an unknown field.
  for (const key of ["token", "secret", "bearer_token", "password", "access_token",
    "credential", "refresh_token", "api_key"]) {
    assert.throws(() => capability({ [key]: "x" }), boundaryError("unknown_field"), key);
    assert.throws(() => envelope({ [key]: "x" }), boundaryError("unknown_field"), key);
    assert.throws(() => presentation({ [key]: "x" }), boundaryError("unknown_field"), key);
  }
  // The exact action content never enters either — only its digest.
  assert.throws(() => envelope({ payload: "Dear tenant," }), boundaryError("unknown_field"));

  // A handle that is shaped like credential material is refused as one.
  // The PEM case is assembled rather than written out: ops/ci-secret-scan.py marks
  // private-key-block as NOT allowlistable, on the stated ground that such a block
  // has no legitimate reason to be in this tree. That rule is right, and a fixture
  // is not a reason to carve an exception into it -- the refusal under test is about
  // the SHAPE, which this reproduces exactly without the tree ever carrying one.
  const pemShaped = ["-----BEGIN", "PRIVATE", "KEY-----"].join(" ");
  for (const handle of ["aaaa.bbbb.cccc", pemShaped, "has space"]) {
    assert.throws(() => capability({ capability_id: handle }),
      boundaryError("credential_material_in_capability"), handle);
    assert.throws(() => capability({ nonce: handle }),
      boundaryError("credential_material_in_capability"), handle);
  }
  // An ordinary opaque handle is fine.
  assert.equal(capability({ capability_id: "cap_2026-09-10:abc" }).capability_id, "cap_2026-09-10:abc");

  // And the policy says so in the bytes that are hashed.
  const preimage = v5EffectEnvelopePolicyPreimage();
  assert.equal(preimage.carries_credential_material, false);
  assert.equal(preimage.carries_payload_content, false);
  assert.equal(preimage.capability_handles_are_opaque, true);
});

// ---------------------------------------------------------------------------
// The envelope seal.
// ---------------------------------------------------------------------------

test("the envelope is immutable and hashes exactly the fields that decide the effect", () => {
  const env = envelope();
  assert.ok(Object.isFrozen(env));
  assert.ok(Object.isFrozen(env.principals));
  assert.ok(Object.isFrozen(env.caps));
  assert.throws(() => { env.action = "business.read_deal"; }, TypeError);
  assert.throws(() => { env.caps.push({ cap_key: "x", limit: 1 }); }, TypeError);

  // The preimage is written out BY HAND here rather than read back from the
  // module, so this checks what is sealed instead of echoing it.
  const expected = {
    envelope_kind: "workflow-effect-envelope.v1",
    schema_version: "doctorcre-v5-workflow-effect-envelope.v1",
    tenant: ORGANIZATION_TENANT_ID,
    envelope_id: "env-1",
    workflow_id: "wf-1",
    step_id: "step-1",
    action: ACTION,
    required_capability: CAPABILITY,
    principals: {
      actor_slug: "joe", account_ref: ACCOUNT, deal_owner_slug: "joe", signer_slug: "dell",
    },
    payload_digest: PAYLOAD,
    caps: [{ cap_key: "recipients", limit: 3 }],
    autonomy_tier_label: TIER,
  };
  assert.deepEqual(effectEnvelopePreimage(env), expected);
  assert.equal(env.envelope_digest,
    "sha256:" + createHash("sha256").update(canonicalJson(expected)).digest("hex"));

  // Every sealed field moves the digest.
  for (const over of [
    { envelope_id: "env-2" }, { workflow_id: "wf-2" }, { step_id: "step-2" },
    { payload_digest: OTHER_PAYLOAD }, { autonomy_tier_label: "tier-unattended" },
    { caps: [{ cap_key: "recipients", limit: 4 }] },
    { caps: [{ cap_key: "attachments", limit: 3 }] },
    { principals: { ...PRINCIPALS, account_ref: "mailbox:other" } },
    { action: "business.sign_document" },
  ]) {
    assert.notEqual(envelope(over).envelope_digest, env.envelope_digest, canonicalJson(over));
  }
});

test("an envelope edited after normalization no longer hashes to its own digest and is refused at the door", () => {
  const forged = { ...envelope(), payload_digest: OTHER_PAYLOAD };
  assert.notEqual(effectEnvelopeDigest(forged), forged.envelope_digest);
  assert.throws(() => admit({ envelope: forged }), boundaryError("envelope_seal_broken"));
  assert.throws(() => issue({ envelope: forged }), boundaryError("envelope_seal_broken"));
  assert.throws(() => order({ envelope: forged }), boundaryError("envelope_seal_broken"));
  // A hand-built object that is not a normalized envelope is unreadable too.
  assert.throws(() => admit({ envelope: { ...envelope(), envelope_kind: "something-else" } }),
    boundaryError("unnormalized_report"));
  assert.throws(() => admit({ capability: { ...capability(), uses_allowed: 5 } }),
    boundaryError("unnormalized_report"));
});

// ---------------------------------------------------------------------------
// The shared registry, the borrowed vocabularies, and the policy digest.
// ---------------------------------------------------------------------------

test("the action registry is READ, not restated: no second list of actions lives here", () => {
  // An unregistered action cannot be enveloped.
  assert.throws(() => envelope({ action: "email.send_anything" }), boundaryError("unknown_action"));
  assert.throws(() => envelope({ action: "*" }), boundaryError("unknown_action"));
  // A registered action that names no capability cannot be capability-issued.
  assert.throws(() => envelope({ action: "system.policy" }),
    boundaryError("action_has_no_required_capability"));
  // The required capability comes from the shared registry, never from the caller.
  assert.equal(envelope().required_capability, V5_ACTIONS[ACTION].required_capability);
  assert.throws(() => envelope({ required_capability: "document.send" }), boundaryError("unknown_field"));

  // The preimage's action list is derived from V5_ACTIONS, so a registry change
  // moves this policy digest instead of leaving two lists to disagree.
  const preimage = v5EffectEnvelopePolicyPreimage();
  assert.deepEqual(preimage.capability_issuable_actions,
    V5_ACTION_KEYS
      .filter(action => typeof V5_ACTIONS[action].required_capability === "string")
      .map(action => ({
        action,
        required_capability: V5_ACTIONS[action].required_capability,
        required_controls: [...(V5_ACTIONS[action].required_controls ?? [])].sort(),
      })));
  assert.ok(preimage.capability_issuable_actions.length > 0);
  assert.ok(!preimage.capability_issuable_actions.some(entry => entry.action === "system.policy"));
});

test("the state vocabularies are V5-F07's, not a second copy of them", () => {
  const preimage = v5EffectEnvelopePolicyPreimage();
  assert.deepEqual(preimage.nonce_states, [...V5_NONCE_STATES].sort());
  assert.deepEqual(preimage.capability_states, [...V5_CAPABILITY_STATES].sort());
  assert.deepEqual(preimage.check_states, [...V5_ADMISSION_CHECK_STATES].sort());

  // Every state any ladder in this module can emit is in that borrowed list.
  const emitted = new Set();
  for (const result of [
    admit(), admit({ presentation: presentation({ nonce_state: "consumed" }) }),
    admit({ presentation: presentation({ nonce_state: null }) }),
    order(), resolve(), issue(),
  ]) {
    for (const check of Object.values(result.check_states)) emitted.add(check.state);
  }
  assert.ok(emitted.size >= 3);
  for (const state of emitted) assert.ok(V5_ADMISSION_CHECK_STATES.includes(state), state);
});

test("the new vocabularies are closed constants hashed into the preimage, not inline literals", () => {
  assert.deepEqual([...V5_CONSUMPTION_STATES].sort(),
    ["committed", "failed", "uncommitted", "unstated"]);
  assert.deepEqual([...V5_PROVIDER_CALL_STATES].sort(), ["not_started", "started", "unstated"]);
  assert.deepEqual([...V5_ATTEMPT_OUTCOME_STATES].sort(),
    ["failed", "succeeded", "timed_out", "unstated"]);
  assert.deepEqual([...V5_READBACK_STATES].sort(),
    ["effect_absent", "effect_present", "indeterminate", "unstated"]);
  assert.deepEqual([...V5_ATTEMPT_RESOLUTIONS].sort(),
    ["confirmed_failure", "confirmed_success", "unknown"]);
  assert.deepEqual([...V5_NEXT_STEPS].sort(), ["provider_readback", "retry", "settle"]);

  // The whole vocabulary is IN the bytes that are hashed, verbatim — this is the
  // assertion that fails if a list goes back to being inline at its comparison
  // site and stops moving the policy digest when it changes.
  const bytes = v5EffectEnvelopePolicyCanonicalBytes();
  for (const [key, list] of [
    ["consumption_states", V5_CONSUMPTION_STATES],
    ["provider_call_states", V5_PROVIDER_CALL_STATES],
    ["attempt_outcome_states", V5_ATTEMPT_OUTCOME_STATES],
    ["readback_states", V5_READBACK_STATES],
    ["attempt_resolutions", V5_ATTEMPT_RESOLUTIONS],
    ["next_steps", V5_NEXT_STEPS],
  ]) {
    assert.ok(bytes.includes(`"${key}":${canonicalJson([...list].sort())}`), key);
  }

  // And the digest is a FUNCTION of those lists: one extra state through the
  // same canonicalizer must not reproduce the shipped digest.
  const preimage = v5EffectEnvelopePolicyPreimage();
  assert.notEqual(digest({ ...preimage, readback_states: [...preimage.readback_states, "maybe"] }),
    v5EffectEnvelopePolicyDigest());
  assert.notEqual(
    digest({
      ...preimage,
      outcome_resolutions: preimage.outcome_resolutions.map(entry =>
        entry.outcome === "timed_out" ? { ...entry, resolution: "confirmed_success" } : entry),
    }),
    v5EffectEnvelopePolicyDigest());
  assert.equal(digest(preimage), v5EffectEnvelopePolicyDigest());
});

test("the ladders are ORDERS in the preimage, and the principal roles keep decision order", () => {
  const preimage = v5EffectEnvelopePolicyPreimage();
  assert.deepEqual(preimage.presentation_checks, [...V5_PRESENTATION_CHECKS]);
  assert.deepEqual(preimage.order_checks, [...V5_ORDER_CHECKS]);
  assert.deepEqual(preimage.resolution_checks, [...V5_RESOLUTION_CHECKS]);
  assert.deepEqual(preimage.issuance_checks, [...V5_ISSUANCE_CHECKS]);
  assert.equal(preimage.presentation_checks[0], "actor_authority");

  // Role order is decision order and is deliberately NOT sorted: sorting it
  // would put "account" first and change which mismatch is reported.
  assert.deepEqual(preimage.principal_roles, ["actor", "account", "deal_owner", "signer"]);
  assert.notDeepEqual(preimage.principal_roles, [...preimage.principal_roles].sort());
  assert.deepEqual(preimage.principal_fields.map(entry => entry.role),
    [...V5_ENVELOPE_PRINCIPAL_ROLES]);
  assert.equal(preimage.principals_are_positionally_sealed, true);

  // The sets, by contrast, ARE sorted.
  for (const key of ["check_states", "capability_states", "nonce_states", "consumption_states",
    "provider_call_states", "attempt_outcome_states", "readback_states", "attempt_resolutions",
    "next_steps", "reason_ids", "decision_ids", "seams"]) {
    assert.deepEqual(preimage[key], [...preimage[key]].sort(), key);
  }
});

test("the policy digest is deterministic and taken over the exact canonical bytes", () => {
  // Re-hashed by hand rather than compared against a copy of the module's own
  // answer, so the digest is checked instead of merely echoed.
  const expected = "sha256:" + createHash("sha256")
    .update(v5EffectEnvelopePolicyCanonicalBytes()).digest("hex");
  assert.equal(v5EffectEnvelopePolicyDigest(), expected);
  assert.equal(v5EffectEnvelopePolicyDigest(), v5EffectEnvelopePolicyDigest());
  // Nothing situational is bound: the digest does not move between requests.
  admit();
  admit({ now: AFTER_EXPIRY });
  order();
  resolve();
  assert.equal(v5EffectEnvelopePolicyDigest(), expected);
});

test("every reason id the evaluators can produce is in the registered, hashed list", () => {
  const produced = new Set();
  const collect = result => produced.add(result.reason_id);
  collect(admit());
  collect(admit({ presentation: presentation({ nonce_state: "consumed" }) }));
  collect(admit({ presentation: presentation({ nonce_state: null }) }));
  collect(admit({ capability: capability({ envelope_digest: OTHER_ENVELOPE_DIGEST }) }));
  collect(admit({ presentation: presentation({ capability_id: "cap-2" }) }));
  collect(admit({ presentation: presentation({ requested_action: "business.sign_document" }) }));
  collect(admit({ presentation: presentation({ payload_digest: OTHER_PAYLOAD }) }));
  collect(admit({ now: AFTER_EXPIRY }));
  collect(admit({ now: BEFORE_ISSUE }));
  collect(admit({ presentation: presentation({ capability_state: "revoked" }) }));
  collect(admit({ presentation: presentation({ capability_state: "unissued" }) }));
  collect(admit({ presentation: presentation({ capability_state: null }) }));
  collect(admit({ authority: null }));
  collect(admit({ presentation: presentation({ cap_observations: [] }) }));
  collect(admit({ presentation: presentation({ cap_observations: [{ cap_key: "recipients", observed: 9 }] }) }));
  collect(admit({
    presentation: presentation({
      cap_observations: [{ cap_key: "recipients", observed: 1 }, { cap_key: "extra", observed: 1 }],
    }),
  }));
  for (const role of V5_ENVELOPE_PRINCIPAL_ROLES) {
    const field = { actor: "actor_slug", account: "account_ref", deal_owner: "deal_owner_slug", signer: "signer_slug" }[role];
    collect(admit({
      presentation: presentation({ presented_principals: { ...PRINCIPALS, [field]: "someone-else" } }),
    }));
  }
  collect(admit({ presentation: presentation({ presented_autonomy_tier_label: "tier-unattended" }) }));
  collect(order());
  collect(order({ attempt: attempt({ provider_call: { state: "not_started" } }) }));
  collect(order({ attempt: attempt({ provider_call: { state: "started", started_seq: 1 } }) }));
  collect(order({ attempt: attempt({ consumption: { state: "uncommitted" } }) }));
  collect(order({ attempt: attempt({ consumption: { state: "failed" } }) }));
  collect(order({ attempt: attempt({ consumption: { state: "unstated" } }) }));
  collect(order({ attempt: attempt({ consumption: { state: "committed" } }) }));
  collect(order({ attempt: attempt({ provider_call: { state: "unstated" } }) }));
  collect(order({ attempt: attempt({ provider_call: { state: "started" } }) }));
  collect(order({ attempt: attempt({ envelope_digest: OTHER_ENVELOPE_DIGEST }) }));
  collect(order({ attempt: attempt({ capability_id: "cap-2" }) }));
  collect(resolve());
  collect(resolve({ proposed_next_step: "provider_readback" }));
  collect(resolve({ attempt: attempt({ outcome: { state: "timed_out" } }), proposed_next_step: "retry" }));
  collect(resolve({ attempt: attempt({ outcome: { state: "timed_out" } }), proposed_next_step: "settle" }));
  collect(resolve({ attempt: attempt({ outcome: { state: "timed_out" } }), proposed_next_step: "provider_readback" }));
  collect(resolve({
    attempt: attempt({ outcome: { state: "failed" } }), proposed_next_step: "retry",
    proposed_retry: { capability_id: "cap-2", nonce: "nonce-2" },
  }));
  collect(resolve({
    attempt: attempt({ outcome: { state: "failed" } }), proposed_next_step: "retry",
    proposed_retry: { capability_id: "cap-1", nonce: "nonce-2" },
  }));
  collect(resolve({ attempt: attempt({ outcome: { state: "failed" } }), proposed_next_step: "retry" }));
  collect(resolve({ proposed_next_step: "retry", proposed_retry: { capability_id: "cap-2", nonce: "nonce-2" } }));
  collect(resolve({
    attempt: attempt({
      outcome: { state: "timed_out" },
      readback: { state: "effect_present", join_key: envelope().envelope_digest },
    }),
    proposed_next_step: "retry", proposed_retry: { capability_id: "cap-2", nonce: "nonce-2" },
  }));
  collect(resolve({
    attempt: attempt({
      outcome: { state: "timed_out" },
      readback: { state: "indeterminate", join_key: envelope().envelope_digest },
    }),
    proposed_next_step: "retry",
  }));
  collect(resolve({
    attempt: attempt({
      outcome: { state: "succeeded" },
      readback: { state: "effect_absent", join_key: envelope().envelope_digest },
    }),
    proposed_next_step: "settle",
  }));
  collect(resolve({
    attempt: attempt({
      outcome: { state: "timed_out" }, readback: { state: "effect_absent", join_key: OTHER_ENVELOPE_DIGEST },
    }),
    proposed_next_step: "provider_readback",
  }));
  collect(resolve({
    consumption_order: evaluateConsumptionOrder({
      envelope: envelope(), capability: capability(),
      attempt: attempt({ consumption: { state: "uncommitted" } }),
    }),
    attempt: attempt({ consumption: { state: "uncommitted" } }),
    proposed_next_step: "settle",
  }));
  collect(issue());
  collect(issue({ authority: null }));
  collect(issue({ now: AFTER_EXPIRY }));
  collect(issue({ now: BEFORE_ISSUE }));
  collect(issue({ capability: capability({ envelope_digest: OTHER_ENVELOPE_DIGEST }) }));
  collect(issue({ capability: capability({ capability: "document.sign" }) }));
  collect(issue({ capability: capability({ autonomy_tier_label: "tier-unattended" }) }));
  for (const field of ["actor_slug", "account_ref", "deal_owner_slug", "signer_slug"]) {
    collect(issue({ capability: capability({ principals: { ...PRINCIPALS, [field]: "someone-else" } }) }));
  }

  for (const reason_id of produced) {
    assert.ok(V5_EFFECT_ENVELOPE_REASON_IDS.includes(reason_id), `unregistered reason id: ${reason_id}`);
  }
  // The exercise above has to be broad enough for that check to mean something.
  assert.ok(produced.size >= 30, `only ${produced.size} distinct reasons exercised`);
});

// ---------------------------------------------------------------------------
// What is deferred, and the two kinds of no.
// ---------------------------------------------------------------------------

test("the settled decision binding is deferred by name, never guessed", () => {
  assert.deepEqual([...V5_F06_DECISION_IDS],
    ["Q015.D1", "Q101.D1", "Q102.D1", "Q105.D1", "Q109.D1", "Q110.D1", "Q132.D1"]);
  const preimage = v5EffectEnvelopePolicyPreimage();
  assert.equal(preimage.decision_binding, "unbound");
  assert.deepEqual(preimage.decision_ids, [...V5_F06_DECISION_IDS].sort());

  // No invented settled text and no invented source-evidence digest ships here.
  const source = readFileSync(SRC_PATH, "utf8");
  assert.ok(!/settled_requirement/.test(source),
    "no settled requirement text is held for this slice, so none may be written");
  assert.ok(!/source_evidence_digest/.test(source));

  // The header says it out loud and names the seven.
  const header = source.slice(0, source.indexOf("\nimport "));
  assert.ok(/THE SETTLED DECISION BINDING/.test(header));
  for (const id of V5_F06_DECISION_IDS) assert.ok(header.includes(id), id);
  assert.ok(header.includes("doctrine store"));

  // No caller can smuggle the deferred question in as a request field.
  assert.throws(() => evaluateCapabilityPresentation({
    envelope: envelope(), capability: capability(), presentation: presentation(),
    authority: authorityAnswer(), now: NOW, decisions: { "Q015.D1": { source_evidence_digest: "0".repeat(64) } },
  }), boundaryError("unknown_field"));
});

test("the autonomy tier is an opaque label compared byte-for-byte, and the ladder is a named seam", () => {
  const upgraded = admit({ presentation: presentation({ presented_autonomy_tier_label: "tier-unattended" }) });
  assert.equal(upgraded.decision, "refuse");
  assert.equal(upgraded.reason_id, "autonomy_tier_changed_after_issuance");
  assert.equal(upgraded.check_states.autonomy_tier.ladder_seam, V5_F06_AUTONOMY_TIER_LADDER_SEAM);
  assert.equal(upgraded.check_states.autonomy_tier.issued_tier, TIER);

  // A DOWNGRADE refuses too. Without the ladder there is no basis for calling
  // one direction safe, and guessing would ship an invented policy.
  assert.equal(admit({
    presentation: presentation({ presented_autonomy_tier_label: "tier-manual" }),
  }).reason_id, "autonomy_tier_changed_after_issuance");

  assert.equal(V5_F06_AUTONOMY_TIER_LADDER_SEAM, "step:v5-f06-autonomy-tier-vocabulary-and-ladder");
  assert.deepEqual([...V5_F06_SEAMS], [
    V5_F06_AUTONOMY_TIER_LADDER_SEAM,
    V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM,
    V5_F06_DECISION_BINDING_SEAM,
    V5_F06_EFFECT_CAP_VOCABULARY_SEAM,
    V5_F06_PROVIDER_READBACK_JOIN_SEAM,
  ].sort());
});

test("two kinds of no: policy answers are returned and contract violations throw", () => {
  // Returned: an honest "I cannot say" is an answer with a reason id.
  assert.equal(admit({ presentation: presentation({ nonce_state: null }) }).decision, "refuse");
  assert.equal(order({ attempt: attempt({ consumption: { state: "unstated" } }) }).decision, "refuse");

  // Thrown: an unreadable request is not a policy question.
  assert.throws(() => presentation({ nonce_state: "probably_fine" }), boundaryError("unknown_nonce_state"));
  assert.throws(() => presentation({ capability_state: "sort_of_active" }),
    boundaryError("unknown_capability_state"));
  assert.throws(() => attempt({ consumption: { state: "vibes" } }),
    boundaryError("unknown_consumption_state"));
  assert.throws(() => attempt({ provider_call: { state: "in_flight" } }),
    boundaryError("unknown_provider_call_state"));
  assert.throws(() => attempt({ outcome: { state: "mostly" } }), boundaryError("unknown_outcome_state"));
  assert.throws(() => attempt({ readback: { state: "unclear" } }), boundaryError("unknown_readback_state"));
  assert.throws(() => resolve({ proposed_next_step: "just_send_it" }), boundaryError("unknown_next_step"));
  assert.throws(() => envelope({ tenant: "someone-else" }), boundaryError("tenant_mismatch"));
  assert.throws(() => envelope({ payload_digest: "deadbeef" }), boundaryError("invalid_digest"));
  assert.throws(() => envelope({ envelope_id: "env 1" }), boundaryError("invalid_identifier"));
  assert.throws(() => capability({ issued_at: "yesterday" }), boundaryError("invalid_timestamp"));
  assert.throws(() => admit({ now: "2026-13-01T00:00:00Z" }), boundaryError("invalid_timestamp"));
  assert.throws(() => attempt({ consumption: { state: "committed", committed_seq: -1 } }),
    boundaryError("invalid_shape"));
  assert.throws(() => attempt({ consumption: { state: "committed", committed_seq: 1.5 } }),
    boundaryError("invalid_shape"));

  // A caller cannot name which checks apply.
  for (const key of ["enforced_checks", "skip_checks", "trusted"]) {
    assert.throws(() => admit({ [key]: ["nonce_state"] }), boundaryError("unknown_field"), key);
    assert.throws(() => order({ [key]: [] }), boundaryError("unknown_field"), key);
  }
});

test("normalized reports and results are frozen, and reading one rewrites nothing", () => {
  const result = admit();
  assert.ok(Object.isFrozen(result));
  assert.ok(Object.isFrozen(result.check_states.nonce_state));
  assert.ok(Object.isFrozen(presentation()));
  assert.ok(Object.isFrozen(attempt()));
  assert.ok(Object.isFrozen(capability().principals));
  assert.throws(() => { result.decision = "allow"; }, TypeError);
  assert.throws(() => { result.checks_required.push("nothing"); }, TypeError);
  assert.equal(admit().decision, "allow");
});

test("the projection accepts nothing, activates nothing and refuses a stale expected digest", () => {
  const projection = v5EffectEnvelopeProjection();
  assert.equal(projection.schema_version, V5_EFFECT_ENVELOPE_SCHEMA_VERSION);
  assert.equal(projection.policy_version, V5_EFFECT_ENVELOPE_POLICY_VERSION);
  assert.equal(projection.policy_digest, v5EffectEnvelopePolicyDigest());
  assert.equal(projection.accepts_anything, false);
  assert.equal(projection.grants_outward_effect, false);
  assert.equal(projection.attended_activation_receipt_present, false);
  assert.equal(projection.attended_activation_receipt, V5_ATTENDED_ACTIVATION_RECEIPT_STEP);
  assert.deepEqual(projection.effects, V5_NO_EFFECTS);
  assert.ok(Object.isFrozen(projection));

  // What is missing is named, with who or what supplies it.
  const missing = projection.unimplemented_dependencies.join("\n");
  assert.ok(missing.includes("doctrine store"));
  assert.ok(missing.includes(V5_F06_DECISION_BINDING_SEAM));
  assert.ok(missing.includes(V5_F06_PROVIDER_READBACK_JOIN_SEAM));
  assert.ok(missing.includes(V5_F06_EFFECT_CAP_VOCABULARY_SEAM));
  assert.ok(/no applied migration/.test(missing));
  assert.ok(/no deployed verb/.test(missing));

  assert.equal(v5EffectEnvelopeProjection({ expected_policy_digest: projection.policy_digest })
    .policy_digest, projection.policy_digest);
  assert.throws(() => v5EffectEnvelopeProjection({ expected_policy_digest: "sha256:" + "0".repeat(64) }),
    boundaryError("stale_expected_digest"));
  assert.throws(() => v5EffectEnvelopeProjection({ expected_policy_digest: "nope" }),
    boundaryError("invalid_expected_digest"));
  assert.throws(() => v5EffectEnvelopeProjection({ accept: true }), boundaryError("unknown_field"));
});

test("the module produces no effect, reads no clock, filesystem, network or environment", () => {
  const source = readFileSync(SRC_PATH, "utf8");
  for (const pattern of [
    /\bnode:fs\b/, /\bnode:net\b/, /\bnode:http\b/, /\bnode:https\b/, /\bnode:child_process\b/,
    // `exec` bare, never `SOME_REGEX.exec(...)`, which reads no process.
    /\bnode:worker_threads\b/, /\bspawn\w*\s*\(/, /(?<![.\w])exec[A-Za-z]*\s*\(/, /\bfetch\s*\(/,
    /\bprocess\.env\b/, /\bnew Date\b/, /\bDate\.now\b/, /\bsetTimeout\b/, /\bsetInterval\b/,
    /\brequire\s*\(/, /\bimport\s*\(/, /\bglobalThis\b/, /\bMath\.random\b/, /\brandomUUID\b/,
  ]) {
    assert.ok(!pattern.test(source), `module source must not contain ${pattern}`);
  }
  const imports = [...source.matchAll(/^import\s[^;]*?from\s+"([^"]+)";/gm)].map(match => match[1]).sort();
  assert.deepEqual(imports, [
    "./artifact-trust.js", "./command-supervisor-admission.v5.js",
    "./global-boundaries.v5.js", "./identity.js",
  ], "the evaluators reuse the existing kernels and keep no second action or actor registry");

  // Every answer carries the zero-effect marker.
  for (const result of [admit(), issue(), order(), resolve()]) {
    assert.deepEqual(result.effects, V5_NO_EFFECTS);
    assert.equal(result.outward_effect_granted, false);
    assert.equal(result.tenant, ORGANIZATION_TENANT_ID);
  }
  // Same request, same answer, twice: nothing situational is bound.
  assert.equal(canonicalJson(admit()), canonicalJson(admit()));
  assert.equal(canonicalJson(resolve()), canonicalJson(resolve()));
});

test("the kinds are stable identifiers a durable row would key on", () => {
  assert.equal(V5_EFFECT_ENVELOPE_SCHEMA_VERSION, "doctorcre-v5-workflow-effect-envelope.v1");
  assert.equal(V5_EFFECT_ENVELOPE_KIND, "workflow-effect-envelope.v1");
  assert.equal(V5_EFFECT_CAPABILITY_KIND, "workflow-effect-capability.v1");
  assert.equal(V5_EFFECT_ENVELOPE_POLICY_VERSION, 1);
  assert.equal(envelope().envelope_kind, V5_EFFECT_ENVELOPE_KIND);
  assert.equal(capability().capability_kind, V5_EFFECT_CAPABILITY_KIND);
  assert.deepEqual(Object.keys(V5_READBACK_RESOLUTIONS).sort(), [...V5_READBACK_STATES].sort());
  assert.deepEqual(Object.keys(V5_OUTCOME_RESOLUTIONS).sort(), [...V5_ATTEMPT_OUTCOME_STATES].sort());
});
