// DoctorCRE v5 slice V5-S01: global scope, actor authority and
// replaceable-platform boundaries.
//
// Eight settled decisions (Q003, Q007, Q020, Q030, Q033, Q073, Q092, Q141) are
// encoded here as ONE closed, versioned policy with a deterministic digest, and
// as six pure evaluators over that policy. Canonicalization and hashing come
// from artifact-trust.js and the partner predicates come from identity.js; this
// file reimplements neither and keeps no parallel actor registry.
//
// TWO KINDS OF NO, and the difference is deliberate:
//   * A POLICY ANSWER is returned — a frozen result whose `decision` is
//     "allow", "refuse", "deferred" or "needs_independent_privacy_route", with a
//     stable `reason_id`. A refusal is an answer the caller may record.
//   * A CONTRACT VIOLATION throws V5BoundaryError. Unknown fields, unknown
//     actions, unknown data classes, open schemas and unreadable timestamps are
//     not policy questions; the module cannot read the request at all, so it
//     fails closed rather than guessing which settled boundary was meant.
//
// The module is pure. It reads no filesystem, no network, no database, no
// scheduler, no environment and no clock: every evaluation that depends on time
// takes `now` from its caller, so two callers holding the same request always
// reach the same answer. It sends nothing, persists nothing and activates
// nothing. `V5_NO_EFFECTS` rides on every result to say so in the record.
//
// WHAT THIS FILE IS NOT. It is not an acceptance path. The portfolio
// constitution's exact-hash acceptance receipt, Gate Zero and the independent
// global no-PHI receipt are later runtime and acceptance inputs; none of them is
// evidence produced here, and computing this policy's digest accepts nothing.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, authorizationClassForActor, isKnownPartner } from "./identity.js";

export const V5_BOUNDARY_SCHEMA_VERSION = "doctorcre-v5-global-boundaries.v1";
export const V5_BOUNDARY_POLICY_VERSION = 1;

const SHA256_HEX = /^[0-9a-f]{64}$/;
// Captured rather than merely shape-matched, because the calendar has to be
// checked against the LITERAL fields; see assertInstant.
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

export class V5BoundaryError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5BoundaryError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
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

/** An open schema is a contract violation: an unread field is an unenforced one. */
function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertStringArray(value, path) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  value.forEach((item, index) => {
    if (typeof item !== "string" || item.length === 0) {
      fail("invalid_shape", `${path}[${index}] must be a non-empty string`, { path: `${path}[${index}]` });
    }
  });
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Timestamps are parsed, never inferred. A bare date, a locale string or an
 * offsetless stamp is refused rather than silently read in some ambient zone.
 *
 * THE CALENDAR IS CHECKED AGAINST THE LITERAL FIELDS, BEFORE PARSING, because
 * Date.parse silently NORMALIZES an impossible date rather than rejecting it:
 * "2026-02-31T00:00:00Z" becomes 3 March. A grant window computed from that is
 * bound to an instant nobody wrote, so the authorization would not be bound to
 * the literal instant supplied. Shape alone cannot catch it — 02-31 matches the
 * pattern perfectly — so month, day-of-month (leap-year aware), time and offset
 * ranges are each range-checked here. Valid explicit-offset instants are
 * untouched: an offset shifts the instant, never the calendar validity of the
 * literal date, so it is range-checked and otherwise left to the parser.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path, value });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const h = Number(hour), mi = Number(minute), s = Number(second);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || h > 23 || mi > 59 || s > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path, value });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path, value });
  return parsed;
}

function assertSha256Hex(value, path) {
  if (typeof value !== "string" || !SHA256_HEX.test(value)) {
    fail("invalid_digest", `${path} must be a 64-character lower-case sha256 hex digest`, { path });
  }
  return value;
}

/** Every result carries this: the module produces no effect of any kind. */
export const V5_NO_EFFECTS = deepFreeze({
  creates_effect: false,
  database_writes: 0,
  network_calls: 0,
  provider_actions: 0,
  notifications: 0,
  schedules: 0,
  deployments: 0,
  activations: 0,
  acceptances: 0,
});

// ---------------------------------------------------------------------------
// The settled decisions. Text and evidence digests are copied verbatim from the
// reviewed source binding; they are identity, not configuration. A caller that
// believes it holds a different subset can prove the disagreement through
// assertSettledDecisionBinding below rather than discovering it later.
// ---------------------------------------------------------------------------

export const V5_DECISION_SUBSET_CANONICAL_SHA256 =
  "fea5b8563ffd60ae13f2a80f790c88ab96216626f09f5f0a7f1f416f2e71b630";

export const V5_SETTLED_DECISIONS = deepFreeze({
  "Q003.D1": {
    settled_requirement: "Optimize v5 for Joe and Dell while preserving clean identity and authorization seams for future roles; do not build public SaaS machinery now.",
    source_evidence_digest: "5b7389d4f1145aa77cd735acaa5895611d767b3809675b46d69f42a7e3d4521d",
  },
  "Q007.D1": {
    settled_requirement: "V5 is online-first with bounded cached read continuity, a truthful outage state, and documented old-school fallback; it does not accept offline mutations.",
    source_evidence_digest: "640ce19bd6e4286812d4f833649660bf103af7c3c0d38fd09d348faa3eaf58a5",
  },
  "Q020.D1": {
    settled_requirement: "The Mac Studio may add local models, development capacity, browser automation, and private compute, but core DoctorCRE and Dell's deal and prospecting work must function without it. Joe retains v5 system design, policy, release, security, destructive-migration, and autonomy-tier activation authority. Dell's developer and release-admin authority remains deferred unless a later cited Joe redecision or narrow expiring receipted delegation grants it. Either verified partner may perform ordinary business actions within deal-owner, signer, policy, account, and exact-capability controls; continuity never converts into permanent system privilege.",
    source_evidence_digest: "47aef2de3979c0f74756d68f8b364aade821bfcc95d4329659aafb586e25e745",
  },
  "Q030.D1": {
    settled_requirement: "Use the Mac Studio aggressively where measured benefit exists, but keep it replaceable, health-visible, free of unique authority, and paired with a cloud fallback or explicit unavailable state.",
    source_evidence_digest: "6421a440f0138e7e8f4aec6d13429cf1f02785f7199a255a0ff872cb9a8c9d6a",
  },
  "Q033.D1": {
    settled_requirement: "V5 prohibits PHI and raw patient-level locations; supporting PHI requires an explicit future compliance and product-scope amendment rather than incidental storage.",
    source_evidence_digest: "1aa74e892a8bb348cccbf2f34e456fe31d0f895e75d4da7ec7f84b9f174961fb",
  },
  "Q073.D1": {
    settled_requirement: "V5 terminology, permissions, workflows, and outputs are strictly tenant and buyer representation while Joe remains at CARR.",
    source_evidence_digest: "f9fc311de43cb12ea50d6c8b598280be340d5a5e34f2da76ee179a020ce1296d",
  },
  "Q092.D1": {
    settled_requirement: "Keep exposed v5 behavior tenant and buyer only while representing brokerage side explicitly in the schema so future listing work does not require corrupting structural identifiers.",
    source_evidence_digest: "396084c25cba6047deaff489111bc371a7be29d0415c4f2dd5eb35ce37305524",
  },
  "Q141.D1": {
    settled_requirement: "Joe retains v5 system design, policy, release, security, destructive-migration, and autonomy-tier activation authority. Dell's developer and release-admin authority remains deferred unless a later cited Joe redecision or narrow expiring receipted delegation grants it. Either verified partner may perform ordinary business actions within deal-owner, signer, policy, account, and exact-capability controls; continuity never converts into permanent system privilege.",
    source_evidence_digest: "e71454e022f3c60ae27b673fa0b3899d616e78b638666df2691f3d642baa0a51",
  },
});

export const V5_SETTLED_DECISION_IDS = deepFreeze(Object.keys(V5_SETTLED_DECISIONS).sort());

/**
 * Refuse a caller whose decision subset has drifted from the reviewed one.
 *
 * Drift is checked in both directions — a missing decision and an extra one are
 * both drift — and every source-evidence digest must match exactly. Passing the
 * subset canonical hash is optional; supplying a different one is drift.
 */
export function assertSettledDecisionBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decisions", "decision_subset_canonical_sha256"], "binding");
  assertRequiredKeys(binding, ["decisions"], "binding");
  assertObject(binding.decisions, "binding.decisions");
  if ("decision_subset_canonical_sha256" in binding) {
    assertSha256Hex(binding.decision_subset_canonical_sha256, "binding.decision_subset_canonical_sha256");
    if (binding.decision_subset_canonical_sha256 !== V5_DECISION_SUBSET_CANONICAL_SHA256) {
      fail("decision_binding_drift", "the decision subset hash does not match the reviewed subset", {
        expected: V5_DECISION_SUBSET_CANONICAL_SHA256, actual: binding.decision_subset_canonical_sha256,
      });
    }
  }
  const supplied = Object.keys(binding.decisions).sort();
  const missing = V5_SETTLED_DECISION_IDS.filter(id => !supplied.includes(id));
  const extra = supplied.filter(id => !V5_SETTLED_DECISION_IDS.includes(id));
  if (missing.length > 0 || extra.length > 0) {
    fail("decision_binding_drift", "the supplied decision set is not the reviewed eight", { missing, extra });
  }
  for (const id of V5_SETTLED_DECISION_IDS) {
    const entry = assertObject(binding.decisions[id], `binding.decisions.${id}`);
    assertClosedKeys(entry, ["source_evidence_digest", "settled_requirement"], `binding.decisions.${id}`);
    assertRequiredKeys(entry, ["source_evidence_digest"], `binding.decisions.${id}`);
    assertSha256Hex(entry.source_evidence_digest, `binding.decisions.${id}.source_evidence_digest`);
    if (entry.source_evidence_digest !== V5_SETTLED_DECISIONS[id].source_evidence_digest) {
      fail("decision_binding_drift", `source-evidence digest drift on ${id}`, {
        decision_id: id, expected: V5_SETTLED_DECISIONS[id].source_evidence_digest,
        actual: entry.source_evidence_digest,
      });
    }
    if ("settled_requirement" in entry &&
        entry.settled_requirement !== V5_SETTLED_DECISIONS[id].settled_requirement) {
      fail("decision_binding_drift", `settled requirement text drift on ${id}`, { decision_id: id });
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// Q003 / Q020 / Q141 — actor authority.
//
// THREE CLASSES, and the split is the whole decision:
//
//   system_authority   Joe's retained authority over v5 design, policy, the
//                      release decision, security, destructive migration and
//                      autonomy-tier activation. Never delegable, by anyone, to
//                      anyone — a delegation naming one of these refuses before
//                      it is even matched against the request.
//
//   developer /        Dell's DEFERRED classes. These are the operational
//   release_admin      actions, distinct from Joe's retained release DECISION
//                      above: publishing a release Joe has decided on is
//                      release-admin work, deciding to release is not. Joe holds
//                      both inherently. Dell reaches one only through a later
//                      cited Joe redecision, or a narrow, exact-action,
//                      expiring, receipted Joe delegation, and only while that
//                      grant is current.
//
//   ordinary_business  Either verified partner, inside the deal-owner, signer,
//                      account, policy and exact-capability controls the action
//                      names. This is the continuity path, and it is why no
//                      outage anywhere in this module needs to widen authority:
//                      ordinary business never depended on a deferred class.
//
// No grant of any kind produces a permanent privilege here. A grant authorizes
// one action, once, while current; `permanent_privilege_granted: false` is on
// every result because that is the property the decision actually settles.
// ---------------------------------------------------------------------------

export const V5_AUTHORITY_CLASSES = deepFreeze([
  "system_authority", "developer", "release_admin", "ordinary_business",
]);

export const V5_ORDINARY_BUSINESS_CONTROLS = deepFreeze([
  "deal_owner", "signer", "account", "policy", "capability",
]);

export const V5_ACTIONS = deepFreeze({
  "system.design": { authority_class: "system_authority" },
  "system.policy": { authority_class: "system_authority" },
  "system.release_decision": { authority_class: "system_authority" },
  "system.security": { authority_class: "system_authority" },
  "system.destructive_migration": { authority_class: "system_authority" },
  "system.autonomy_tier_activation": { authority_class: "system_authority" },

  "developer.change_source": { authority_class: "developer" },
  "developer.run_migration_rehearsal": { authority_class: "developer" },

  "release_admin.publish_release": { authority_class: "release_admin" },
  "release_admin.rotate_release_credential": { authority_class: "release_admin" },

  "business.read_deal": {
    authority_class: "ordinary_business",
    required_controls: ["deal_owner", "policy", "capability"],
    required_capability: "deal.read",
  },
  "business.update_deal": {
    authority_class: "ordinary_business",
    required_controls: ["deal_owner", "account", "policy", "capability"],
    required_capability: "deal.write",
  },
  "business.send_client_document": {
    authority_class: "ordinary_business",
    required_controls: ["deal_owner", "account", "policy", "capability"],
    required_capability: "document.send",
  },
  "business.sign_document": {
    authority_class: "ordinary_business",
    required_controls: ["deal_owner", "signer", "account", "policy", "capability"],
    required_capability: "document.sign",
  },
  "business.record_prospecting_touch": {
    authority_class: "ordinary_business",
    required_controls: ["account", "policy", "capability"],
    required_capability: "prospecting.write",
  },
});

export const V5_ACTION_KEYS = deepFreeze(Object.keys(V5_ACTIONS).sort());

/** The system-authority holder. A constant of the settled decision, not config. */
export const V5_SYSTEM_AUTHORITY_PARTNER = "joe";
/** The partner whose developer and release-admin classes are deferred. */
export const V5_DEFERRED_AUTHORITY_PARTNER = "dell";
/** "Narrow" is a number, so it can be checked: seven days, end to end. */
export const V5_MAX_DELEGATION_WINDOW_SECONDS = 604800;

const DELEGATION_KEYS = Object.freeze([
  "delegation_ref", "granted_by", "granted_to", "action", "receipt_digest",
  "issued_at", "expires_at", "scope_note",
]);
const DELEGATION_REQUIRED = Object.freeze([
  "delegation_ref", "granted_by", "granted_to", "action", "receipt_digest", "issued_at", "expires_at",
]);
const REDECISION_KEYS = Object.freeze([
  "redecision_ref", "decided_by", "subject", "action", "cited_source_digest", "decided_at", "note",
]);
const REDECISION_REQUIRED = Object.freeze([
  "redecision_ref", "decided_by", "subject", "action", "cited_source_digest", "decided_at",
]);
const AUTHORITY_REQUEST_KEYS = Object.freeze([
  "actor", "action", "tenant", "now", "controls", "delegation", "redecision", "continuity_context",
]);
const CONTROLS_KEYS = Object.freeze([
  "deal_owner_slug", "signer_slug", "account_slug", "policy_scope", "capabilities",
]);
const CONTINUITY_CONTEXT_KEYS = Object.freeze(["local_platform_state", "connectivity", "note"]);

/** A wildcard is not a narrow grant; it is the absence of one. */
function isWildcardAction(value) {
  return typeof value !== "string" || value.length === 0 || value.includes("*") ||
    value.toLowerCase() === "any" || value.toLowerCase() === "all";
}

function refusal(reason_id, detail) {
  return deepFreeze({
    decision: "refuse", reason_id, ...detail,
    permanent_privilege_granted: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Validate one grant against the exact requested action. Returns a refusal
 * reason id, or null when the grant positively authorizes this action now.
 *
 * EVERY PRESENT GRANT IS VALIDATED AND ANY INVALID ONE REFUSES. A second grant
 * never rescues a bad one, and — the part that has to be enforced rather than
 * merely intended — a good grant never excuses an unread bad one either. Grant
 * shopping in either direction would make the narrowest control in the decision
 * the easiest one to route around. See the call site in evaluateActorAuthority.
 */
function checkDelegation(delegation, { action, actionEntry, actorSlug, now }) {
  assertObject(delegation, "request.delegation");
  assertClosedKeys(delegation, DELEGATION_KEYS, "request.delegation");
  assertRequiredKeys(delegation, DELEGATION_REQUIRED, "request.delegation");
  // The provenance pointer is part of the grant, not decoration: an allow whose
  // grant_ref is "" or an object names no receipt anyone could later retrieve.
  if (typeof delegation.delegation_ref !== "string" || delegation.delegation_ref.trim().length === 0) {
    return "delegation_ref_invalid";
  }
  if (delegation.granted_by !== V5_SYSTEM_AUTHORITY_PARTNER) return "delegation_grantor_not_system_authority";
  if (delegation.granted_to !== actorSlug) return "delegation_subject_mismatch";
  if (isWildcardAction(delegation.action)) return "delegation_wildcard_refused";
  if (!Object.prototype.hasOwnProperty.call(V5_ACTIONS, delegation.action)) return "delegation_action_unknown";
  if (V5_ACTIONS[delegation.action].authority_class === "system_authority") return "system_authority_not_delegable";
  if (delegation.action !== action) return "delegation_action_mismatch";
  if (actionEntry.authority_class !== "developer" && actionEntry.authority_class !== "release_admin") {
    return "delegation_action_class_not_delegable";
  }
  if (typeof delegation.receipt_digest !== "string" || !SHA256_HEX.test(delegation.receipt_digest)) {
    return "delegation_unreceipted";
  }
  if (delegation.expires_at === null || delegation.expires_at === "never" ||
      delegation.expires_at === "permanent") {
    return "delegation_permanent_refused";
  }
  const issued = assertInstant(delegation.issued_at, "request.delegation.issued_at");
  const expires = assertInstant(delegation.expires_at, "request.delegation.expires_at");
  if (expires <= issued) return "delegation_window_invalid";
  if ((expires - issued) / 1000 > V5_MAX_DELEGATION_WINDOW_SECONDS) return "delegation_window_overbroad";
  if (now < issued) return "delegation_not_yet_effective";
  if (now >= expires) return "delegation_expired";
  return null;
}

function checkRedecision(redecision, { action, actionEntry, actorSlug, now }) {
  assertObject(redecision, "request.redecision");
  assertClosedKeys(redecision, REDECISION_KEYS, "request.redecision");
  assertRequiredKeys(redecision, REDECISION_REQUIRED, "request.redecision");
  // Same reason as delegation_ref above: a cited redecision must actually cite.
  if (typeof redecision.redecision_ref !== "string" || redecision.redecision_ref.trim().length === 0) {
    return "redecision_ref_invalid";
  }
  if (redecision.decided_by !== V5_SYSTEM_AUTHORITY_PARTNER) return "redecision_author_not_system_authority";
  if (redecision.subject !== actorSlug) return "redecision_subject_mismatch";
  if (isWildcardAction(redecision.action)) return "redecision_wildcard_refused";
  if (!Object.prototype.hasOwnProperty.call(V5_ACTIONS, redecision.action)) return "redecision_action_unknown";
  if (V5_ACTIONS[redecision.action].authority_class === "system_authority") return "system_authority_not_delegable";
  if (redecision.action !== action) return "redecision_action_mismatch";
  if (actionEntry.authority_class !== "developer" && actionEntry.authority_class !== "release_admin") {
    return "redecision_action_class_not_delegable";
  }
  if (typeof redecision.cited_source_digest !== "string" || !SHA256_HEX.test(redecision.cited_source_digest)) {
    return "redecision_uncited";
  }
  const decidedAt = assertInstant(redecision.decided_at, "request.redecision.decided_at");
  if (decidedAt > now) return "redecision_not_yet_effective";
  return null;
}

function checkOrdinaryBusinessControls(actionEntry, controls, actorSlug) {
  assertObject(controls, "request.controls");
  assertClosedKeys(controls, CONTROLS_KEYS, "request.controls");
  for (const control of actionEntry.required_controls) {
    if (control === "deal_owner") {
      if (typeof controls.deal_owner_slug !== "string" || controls.deal_owner_slug.length === 0) {
        fail("missing_field", "request.controls.deal_owner_slug is required for this action",
          { path: "request.controls.deal_owner_slug" });
      }
      if (controls.deal_owner_slug !== actorSlug) return "deal_owner_mismatch";
    }
    if (control === "signer") {
      if (typeof controls.signer_slug !== "string" || controls.signer_slug.length === 0) {
        fail("missing_field", "request.controls.signer_slug is required for this action",
          { path: "request.controls.signer_slug" });
      }
      if (controls.signer_slug !== actorSlug) return "signer_mismatch";
    }
    if (control === "account") {
      if (typeof controls.account_slug !== "string" || controls.account_slug.length === 0) {
        fail("missing_field", "request.controls.account_slug is required for this action",
          { path: "request.controls.account_slug" });
      }
      if (controls.account_slug !== actorSlug) return "account_mismatch";
    }
    if (control === "policy") {
      if (controls.policy_scope === undefined || controls.policy_scope === null) {
        fail("missing_field", "request.controls.policy_scope is required for this action",
          { path: "request.controls.policy_scope" });
      }
      assertStringArray(controls.policy_scope, "request.controls.policy_scope");
      if (!controls.policy_scope.includes(actionEntry.action_key)) return "policy_scope_excludes_action";
    }
    if (control === "capability") {
      if (controls.capabilities === undefined || controls.capabilities === null) {
        fail("missing_field", "request.controls.capabilities is required for this action",
          { path: "request.controls.capabilities" });
      }
      assertStringArray(controls.capabilities, "request.controls.capabilities");
      if (!controls.capabilities.includes(actionEntry.required_capability)) return "capability_not_granted";
    }
  }
  return null;
}

/**
 * Evaluate one actor's authority for one exact action, at one exact instant.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. The request must be readable and closed; the tenant must be the one
 *      server-held tenant. Otherwise this throws.
 *   2. The actor must be a verified partner by identity.js's own predicate.
 *      A sponsored agent, an unsponsored runtime or a machine seat refuses.
 *   3. The action must be one of the closed registry keys. Otherwise this throws.
 *   4. system_authority: allow only the system-authority partner.
 *   5. developer / release_admin: the system-authority partner holds these
 *      inherently. For the deferred partner, exactly one present grant must
 *      validate against this exact action, now.
 *   6. ordinary_business: either verified partner, subject to every control the
 *      action names.
 *
 * `continuity_context` is validated and then deliberately unread. Continuity is
 * the reason a request exists during an outage; it is never an input to who may
 * act, and a test proves the answer is byte-identical with and without it.
 */
export function evaluateActorAuthority(request) {
  assertObject(request, "request");
  assertClosedKeys(request, AUTHORITY_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["actor", "action", "tenant", "now"], "request");
  if (request.tenant !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `request.tenant must be "${ORGANIZATION_TENANT_ID}"`,
      { expected: ORGANIZATION_TENANT_ID, actual: request.tenant });
  }
  const now = assertInstant(request.now, "request.now");
  if (request.continuity_context !== undefined && request.continuity_context !== null) {
    assertObject(request.continuity_context, "request.continuity_context");
    assertClosedKeys(request.continuity_context, CONTINUITY_CONTEXT_KEYS, "request.continuity_context");
  }
  const actor = assertObject(request.actor, "request.actor");
  if (typeof request.action !== "string" ||
      !Object.prototype.hasOwnProperty.call(V5_ACTIONS, request.action)) {
    fail("unknown_action", `"${request.action}" is not a registered v5 action`, { action: request.action });
  }
  const action = request.action;
  const actionEntry = { ...V5_ACTIONS[action], action_key: action };
  const base = {
    action, authority_class: actionEntry.authority_class,
    actor_slug: typeof actor.slug === "string" ? actor.slug : null,
    tenant: ORGANIZATION_TENANT_ID,
  };

  // Step 2. The partner test is identity.js's, not a second one written here.
  if (authorizationClassForActor(actor) !== "verified_partner" || !isKnownPartner(actor.slug)) {
    return refusal("actor_not_verified_partner", {
      ...base, authorization_class: authorizationClassForActor(actor),
    });
  }
  const actorSlug = actor.slug;

  // Step 4. Joe's retained classes. Not reachable by delegation or redecision.
  if (actionEntry.authority_class === "system_authority") {
    if (actorSlug !== V5_SYSTEM_AUTHORITY_PARTNER) {
      return refusal("system_authority_reserved_to_joe", { ...base, delegable: false });
    }
    return deepFreeze({
      decision: "allow", reason_id: "system_authority_retained", ...base,
      grant_kind: "retained_system_authority", permanent_privilege_granted: false,
      effects: V5_NO_EFFECTS,
    });
  }

  // Step 5. The deferred classes.
  if (actionEntry.authority_class === "developer" || actionEntry.authority_class === "release_admin") {
    if (actorSlug === V5_SYSTEM_AUTHORITY_PARTNER) {
      return deepFreeze({
        decision: "allow", reason_id: "system_authority_retained", ...base,
        grant_kind: "retained_system_authority", permanent_privilege_granted: false,
        effects: V5_NO_EFFECTS,
      });
    }
    const hasRedecision = request.redecision !== undefined && request.redecision !== null;
    const hasDelegation = request.delegation !== undefined && request.delegation !== null;
    if (!hasRedecision && !hasDelegation) {
      return refusal("deferred_authority_requires_grant", {
        ...base, deferred_for: actorSlug,
        accepted_grant_kinds: ["cited_joe_redecision", "narrow_expiring_receipted_delegation"],
      });
    }
    // EVERY PRESENT GRANT IS VALIDATED, and any invalid one refuses the whole
    // request. Validating only the first grant that happened to be checked let
    // evaluation ORDER decide the answer: a request carrying a specifically
    // prohibited delegation — expired, cross-action — was authorized whenever a
    // valid redecision sat beside it, because the delegation was never read.
    // A caller must not be able to attach a bad grant to a good one and have the
    // bad one ignored, so the presence of an invalid grant is itself the answer.
    // Order is fixed (redecision, then delegation) so that when both are invalid
    // the reported reason is deterministic rather than a function of shape.
    if (hasRedecision) {
      const reason = checkRedecision(request.redecision, { action, actionEntry, actorSlug, now });
      if (reason) return refusal(reason, { ...base, grant_kind: "cited_joe_redecision" });
    }
    if (hasDelegation) {
      const reason = checkDelegation(request.delegation, { action, actionEntry, actorSlug, now });
      if (reason) return refusal(reason, { ...base, grant_kind: "narrow_expiring_receipted_delegation" });
    }
    const validatedGrants = [
      ...(hasRedecision ? ["cited_joe_redecision"] : []),
      ...(hasDelegation ? ["narrow_expiring_receipted_delegation"] : []),
    ];
    if (hasRedecision) {
      return deepFreeze({
        decision: "allow", reason_id: "cited_joe_redecision_authorizes_bound_action", ...base,
        grant_kind: "cited_joe_redecision", grant_ref: request.redecision.redecision_ref,
        bound_action: request.redecision.action, validated_grants: validatedGrants,
        permanent_privilege_granted: false,
        effects: V5_NO_EFFECTS,
      });
    }
    return deepFreeze({
      decision: "allow", reason_id: "narrow_expiring_receipted_delegation_current", ...base,
      grant_kind: "narrow_expiring_receipted_delegation", grant_ref: request.delegation.delegation_ref,
      bound_action: request.delegation.action, expires_at: request.delegation.expires_at,
      validated_grants: validatedGrants,
      permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
    });
  }

  // Step 6. Ordinary business, for either verified partner, inside its controls.
  if (request.controls === undefined || request.controls === null) {
    fail("missing_field", "request.controls is required for an ordinary-business action",
      { path: "request.controls", action });
  }
  const controlReason = checkOrdinaryBusinessControls(actionEntry, request.controls, actorSlug);
  if (controlReason) {
    return refusal(controlReason, {
      ...base, required_controls: [...actionEntry.required_controls],
      required_capability: actionEntry.required_capability,
    });
  }
  return deepFreeze({
    decision: "allow", reason_id: "ordinary_business_within_controls", ...base,
    grant_kind: "ordinary_business", satisfied_controls: [...actionEntry.required_controls],
    required_capability: actionEntry.required_capability,
    permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Q073 / Q092 — representation scope.
//
// Exposed behavior is tenant and buyer only. The brokerage side is nonetheless
// a first-class STRUCTURAL value here, and that is the point of Q092: a landlord
// or seller counterparty already appears in real deals, so the schema names the
// side honestly instead of recording it as a tenant. What refuses is EXPOSURE
// and ACTIVATION, never the structural identifier — so future listing work needs
// an amendment, not a migration that un-corrupts identifiers nobody should have
// corrupted.
// ---------------------------------------------------------------------------

export const V5_REPRESENTATION_SIDES = deepFreeze(["tenant", "buyer", "landlord", "seller"]);
export const V5_EXPOSED_REPRESENTATION_SIDES = deepFreeze(["tenant", "buyer"]);
export const V5_BROKERAGE_SIDE_STRUCTURAL_ONLY = deepFreeze(["landlord", "seller"]);
export const V5_SCOPE_AMENDMENT_SEAM = "step:v5-scope-amendment-explicit-product-scope-decision";

const SCOPE_REQUEST_KEYS = Object.freeze([
  "representation_side", "surface", "intent", "activate_listing_side",
]);
export const V5_SCOPE_INTENTS = deepFreeze(["expose", "structural_record", "activate"]);

/**
 * Evaluate one representation-scope request.
 *
 *   1. The side must be one of the four registered sides; anything else throws.
 *   2. Any activation of a brokerage-side surface refuses, whatever the intent.
 *   3. tenant/buyer exposure allows.
 *   4. landlord/seller exposure refuses, while reporting that the structural
 *      identifier is legal and naming the amendment seam.
 *   5. landlord/seller structural_record allows as a structural value only, and
 *      says so: exposed:false.
 */
export function evaluateRepresentationScope(request) {
  assertObject(request, "request");
  assertClosedKeys(request, SCOPE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["representation_side", "intent"], "request");
  if (!V5_REPRESENTATION_SIDES.includes(request.representation_side)) {
    fail("unknown_representation_side", `"${request.representation_side}" is not a registered representation side`,
      { representation_side: request.representation_side, registered: [...V5_REPRESENTATION_SIDES] });
  }
  if (!V5_SCOPE_INTENTS.includes(request.intent)) {
    fail("unknown_scope_intent", `"${request.intent}" is not a registered scope intent`,
      { intent: request.intent, registered: [...V5_SCOPE_INTENTS] });
  }
  if ("surface" in request && (typeof request.surface !== "string" || request.surface.length === 0)) {
    fail("invalid_shape", "request.surface must be a non-empty string when present", { path: "request.surface" });
  }
  if ("activate_listing_side" in request && typeof request.activate_listing_side !== "boolean") {
    fail("invalid_shape", "request.activate_listing_side must be a boolean when present",
      { path: "request.activate_listing_side" });
  }
  const side = request.representation_side;
  const structural = V5_BROKERAGE_SIDE_STRUCTURAL_ONLY.includes(side);
  const base = {
    representation_side: side,
    surface: request.surface ?? null,
    intent: request.intent,
    structural_side_recognized: true,
    brokerage_side_structural_only: structural,
    listing_activation: "inactive_pending_amendment",
    amendment_seam: V5_SCOPE_AMENDMENT_SEAM,
  };

  if (request.intent === "activate" || request.activate_listing_side === true) {
    if (structural || request.activate_listing_side === true) {
      return deepFreeze({
        decision: "refuse", reason_id: "listing_side_activation_refused", ...base, exposed: false,
        permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
      });
    }
    return deepFreeze({
      decision: "refuse", reason_id: "scope_activation_requires_amendment", ...base, exposed: false,
      permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
    });
  }

  if (structural) {
    if (request.intent === "expose") {
      return deepFreeze({
        decision: "refuse", reason_id: "listing_side_exposure_refused", ...base, exposed: false,
        permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
      });
    }
    return deepFreeze({
      decision: "allow", reason_id: "brokerage_side_structural_value_only", ...base, exposed: false,
      permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
    });
  }

  return deepFreeze({
    decision: "allow", reason_id: "tenant_buyer_representation_in_scope", ...base, exposed: true,
    permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Q007 — online-first read continuity.
//
// An outage has exactly three truthful projections and none of them is an empty
// success: available (live), degraded (a bounded cached read, with its age
// stated), unavailable (say so, and name the documented fallback). A mutation
// offline is not degraded — it refuses.
// ---------------------------------------------------------------------------

export const V5_OPERATION_KINDS = deepFreeze(["read", "mutation"]);
export const V5_CONNECTIVITY_STATES = deepFreeze(["online", "degraded", "offline"]);
export const V5_AVAILABILITY_STATES = deepFreeze(["available", "degraded", "unavailable"]);
/** The bound on a cached read: fifteen minutes, stated rather than implied. */
export const V5_MAX_CACHED_READ_AGE_SECONDS = 900;
export const V5_DOCUMENTED_FALLBACK = "documented_old_school_fallback:phone_email_paper";

const CONTINUITY_REQUEST_KEYS = Object.freeze(["operation_kind", "connectivity", "cache", "dependency"]);
const CACHE_KEYS = Object.freeze(["state", "age_seconds", "max_age_seconds"]);
const DEPENDENCY_KEYS = Object.freeze(["ref", "state"]);
export const V5_CACHE_STATES = deepFreeze(["fresh", "stale", "absent"]);
export const V5_DEPENDENCY_STATES = deepFreeze(["current", "stale", "missing"]);

function continuityResult(fields) {
  return deepFreeze({
    ...fields,
    documented_fallback: V5_DOCUMENTED_FALLBACK,
    empty_result_reported_as_success: false,
    permanent_privilege_granted: false,
    effects: V5_NO_EFFECTS,
  });
}

export function evaluateReadContinuity(request) {
  assertObject(request, "request");
  assertClosedKeys(request, CONTINUITY_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["operation_kind", "connectivity"], "request");
  if (!V5_OPERATION_KINDS.includes(request.operation_kind)) {
    fail("unknown_operation_kind", `"${request.operation_kind}" is not a registered operation kind`,
      { operation_kind: request.operation_kind, registered: [...V5_OPERATION_KINDS] });
  }
  if (!V5_CONNECTIVITY_STATES.includes(request.connectivity)) {
    fail("unknown_connectivity_state", `"${request.connectivity}" is not a registered connectivity state`,
      { connectivity: request.connectivity, registered: [...V5_CONNECTIVITY_STATES] });
  }
  let cache = null;
  if (request.cache !== undefined && request.cache !== null) {
    cache = assertObject(request.cache, "request.cache");
    assertClosedKeys(cache, CACHE_KEYS, "request.cache");
    assertRequiredKeys(cache, ["state"], "request.cache");
    if (!V5_CACHE_STATES.includes(cache.state)) {
      fail("unknown_cache_state", `"${cache.state}" is not a registered cache state`,
        { state: cache.state, registered: [...V5_CACHE_STATES] });
    }
    if (cache.state !== "absent") {
      if (!Number.isFinite(cache.age_seconds) || cache.age_seconds < 0) {
        fail("invalid_shape", "request.cache.age_seconds must be a non-negative number for a present cache",
          { path: "request.cache.age_seconds" });
      }
      if ("max_age_seconds" in cache) {
        if (!Number.isFinite(cache.max_age_seconds) || cache.max_age_seconds <= 0) {
          fail("invalid_shape", "request.cache.max_age_seconds must be a positive number",
            { path: "request.cache.max_age_seconds" });
        }
        if (cache.max_age_seconds > V5_MAX_CACHED_READ_AGE_SECONDS) {
          fail("cache_bound_overbroad",
            `a caller may tighten the cached-read bound below ${V5_MAX_CACHED_READ_AGE_SECONDS}s, never widen it`,
            { requested: cache.max_age_seconds, ceiling: V5_MAX_CACHED_READ_AGE_SECONDS });
        }
      }
    }
  }
  let dependency = null;
  if (request.dependency !== undefined && request.dependency !== null) {
    dependency = assertObject(request.dependency, "request.dependency");
    assertClosedKeys(dependency, DEPENDENCY_KEYS, "request.dependency");
    assertRequiredKeys(dependency, ["state"], "request.dependency");
    if (!V5_DEPENDENCY_STATES.includes(dependency.state)) {
      fail("unknown_dependency_state", `"${dependency.state}" is not a registered dependency state`,
        { state: dependency.state, registered: [...V5_DEPENDENCY_STATES] });
    }
  }
  const base = {
    operation_kind: request.operation_kind,
    connectivity: request.connectivity,
    dependency_ref: dependency?.ref ?? null,
    dependency_state: dependency?.state ?? null,
  };

  // A mutation is online-only. No cache, dependency or continuity story changes
  // that; v5 does not accept an offline mutation at all.
  if (request.operation_kind === "mutation") {
    if (request.connectivity !== "online") {
      return continuityResult({
        decision: "refuse", reason_id: "offline_mutation_refused", ...base,
        availability: request.connectivity === "offline" ? "unavailable" : "degraded",
        source: "none", cached: false, cache_age_seconds: null,
      });
    }
    if (dependency && dependency.state === "missing") {
      return continuityResult({
        decision: "refuse", reason_id: "dependency_missing_unavailable", ...base,
        availability: "unavailable", source: "none", cached: false, cache_age_seconds: null,
      });
    }
    if (dependency && dependency.state === "stale") {
      return continuityResult({
        decision: "refuse", reason_id: "dependency_stale_mutation_refused", ...base,
        availability: "degraded", source: "none", cached: false, cache_age_seconds: null,
      });
    }
    return continuityResult({
      decision: "allow", reason_id: "online_mutation_permitted", ...base,
      availability: "available", source: "live", cached: false, cache_age_seconds: null,
    });
  }

  // A read is live while online, and otherwise may be served from a cache that
  // is still inside its bound. A missing dependency is reported, never emptied.
  if (request.connectivity === "online" && (!dependency || dependency.state === "current")) {
    return continuityResult({
      decision: "allow", reason_id: "online_read_live", ...base,
      availability: "available", source: "live", cached: false, cache_age_seconds: null,
    });
  }
  if (dependency && dependency.state === "missing") {
    return continuityResult({
      decision: "refuse", reason_id: "dependency_missing_unavailable", ...base,
      availability: "unavailable", source: "none", cached: false, cache_age_seconds: null,
    });
  }
  const bound = cache && "max_age_seconds" in cache ? cache.max_age_seconds : V5_MAX_CACHED_READ_AGE_SECONDS;
  if (cache && cache.state === "fresh" && cache.age_seconds <= bound) {
    return continuityResult({
      decision: "allow", reason_id: "bounded_cached_read", ...base,
      availability: "degraded", source: "cache", cached: true,
      cache_age_seconds: cache.age_seconds, cache_bound_seconds: bound,
    });
  }
  if (cache && cache.state === "fresh") {
    return continuityResult({
      decision: "refuse", reason_id: "cached_read_outside_bound", ...base,
      availability: "unavailable", source: "none", cached: false,
      cache_age_seconds: cache.age_seconds, cache_bound_seconds: bound,
    });
  }
  if (cache && cache.state === "stale") {
    return continuityResult({
      decision: "refuse", reason_id: "cached_read_stale", ...base,
      availability: "degraded", source: "none", cached: false,
      cache_age_seconds: cache.age_seconds, cache_bound_seconds: bound,
    });
  }
  // A stale dependency with nothing cached is degraded, not unavailable: the
  // surface is reachable and the answer would be wrong, which is a different
  // fact from the surface being gone, and both are different from an empty list.
  if (dependency && dependency.state === "stale") {
    return continuityResult({
      decision: "refuse", reason_id: "dependency_stale_degraded", ...base,
      availability: "degraded", source: "none", cached: false, cache_age_seconds: null,
    });
  }
  return continuityResult({
    decision: "refuse", reason_id: "read_unavailable_no_cache", ...base,
    availability: "unavailable", source: "none", cached: false, cache_age_seconds: null,
  });
}

// ---------------------------------------------------------------------------
// Q020 / Q030 — the replaceable local platform.
//
// The Mac Studio and the Hermes runtime are OPTIONAL nodes. Every capability
// that may run on one declares, here and in the open, what happens when it is
// gone: a cloud fallback, a visible queue, or an honest unavailable. A
// capability with no declared disposition cannot be registered, which is what
// makes "no hidden local dependency" a property of the file rather than a hope.
//
// Neither node ever carries unique authority. A request asserting that one does
// refuses; it does not degrade, and it does not quietly succeed.
// ---------------------------------------------------------------------------

export const V5_OPTIONAL_LOCAL_NODES = deepFreeze(["mac-studio", "hermes-pilot"]);
export const V5_LOCAL_NODE_STATES = deepFreeze(["available", "degraded", "unavailable"]);
export const V5_CANONICAL_AUTHORITY = "carr_cloud_record_layer";
export const V5_LOCAL_FALLBACK_KINDS = deepFreeze(["cloud_fallback", "visible_queue", "none"]);

export const V5_LOCAL_CAPABILITIES = deepFreeze({
  "local_model_inference": { fallback: "cloud_fallback" },
  "browser_automation": { fallback: "visible_queue" },
  "private_compute_batch": { fallback: "visible_queue" },
  "development_capacity": { fallback: "cloud_fallback" },
  "local_media_transcription": { fallback: "none" },
});

const LOCAL_REQUEST_KEYS = Object.freeze(["node", "node_state", "capability", "assert_unique_authority"]);

// A registered capability must state its disposition, and no capability may be
// authority-bearing. Checked at load: a later edit that forgets either one fails
// the module's own import rather than a caller's request.
for (const [capability, entry] of Object.entries(V5_LOCAL_CAPABILITIES)) {
  if (!V5_LOCAL_FALLBACK_KINDS.includes(entry.fallback)) {
    throw new V5BoundaryError("invalid_local_capability_registry",
      `local capability "${capability}" must declare a registered fallback disposition`, { capability });
  }
}

export function evaluateLocalPlatform(request) {
  assertObject(request, "request");
  assertClosedKeys(request, LOCAL_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["node", "node_state", "capability"], "request");
  if (!V5_OPTIONAL_LOCAL_NODES.includes(request.node)) {
    fail("unknown_local_node", `"${request.node}" is not a registered optional local node`,
      { node: request.node, registered: [...V5_OPTIONAL_LOCAL_NODES] });
  }
  if (!V5_LOCAL_NODE_STATES.includes(request.node_state)) {
    fail("unknown_local_node_state", `"${request.node_state}" is not a registered local node state`,
      { node_state: request.node_state, registered: [...V5_LOCAL_NODE_STATES] });
  }
  if (!Object.prototype.hasOwnProperty.call(V5_LOCAL_CAPABILITIES, request.capability)) {
    fail("unknown_local_capability", `"${request.capability}" is not a registered local capability`,
      { capability: request.capability });
  }
  if ("assert_unique_authority" in request && typeof request.assert_unique_authority !== "boolean") {
    fail("invalid_shape", "request.assert_unique_authority must be a boolean when present",
      { path: "request.assert_unique_authority" });
  }
  const entry = V5_LOCAL_CAPABILITIES[request.capability];
  const base = {
    node: request.node, node_state: request.node_state, capability: request.capability,
    declared_fallback: entry.fallback,
    carries_unique_authority: false,
    canonical_authority: V5_CANONICAL_AUTHORITY,
    authority_unchanged: true,
  };
  const result = fields => deepFreeze({
    ...base, ...fields, permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
  });

  if (request.assert_unique_authority === true) {
    return result({
      decision: "refuse", reason_id: "local_node_unique_authority_refused",
      availability: "unavailable", execution: "none",
    });
  }
  if (request.node_state === "available") {
    return result({ decision: "allow", reason_id: "local_node_available",
      availability: "available", execution: "local_node" });
  }
  if (entry.fallback === "cloud_fallback") {
    return result({ decision: "allow", reason_id: "approved_cloud_fallback",
      availability: "degraded", execution: "cloud_fallback" });
  }
  if (entry.fallback === "visible_queue") {
    return result({ decision: "deferred", reason_id: "visible_queue_pending_local_node",
      availability: "degraded", execution: "visible_queue", queue_visible: true });
  }
  return result({
    decision: "refuse",
    reason_id: request.node_state === "unavailable"
      ? "local_capability_unavailable" : "local_capability_degraded_no_fallback",
    availability: request.node_state === "unavailable" ? "unavailable" : "degraded",
    execution: "none",
  });
}

/** Every local capability and its declared disposition, so none can hide. */
export function v5LocalNodeDependencies() {
  return deepFreeze(Object.entries(V5_LOCAL_CAPABILITIES).map(([capability, entry]) => ({
    capability, fallback: entry.fallback, authority_bearing: false,
  })));
}

// ---------------------------------------------------------------------------
// Q033 — the global privacy boundary.
//
// PHI and raw patient-level locations refuse, and the refusal names the
// amendment that would be required rather than pretending one exists.
//
// THE SECOND RESULT IS DELIBERATELY NOT THE FIRST. A later aggregate heat map is
// a real product idea, and answering it with the PHI refusal would either read
// as "never" or invite somebody to route around a refusal they think is wrong.
// It gets its own decision — needs_independent_privacy_route — which is neither
// an acceptance nor a refusal: it names the independent privacy-route evidence
// the input would need, and this module never produces that evidence.
// ---------------------------------------------------------------------------

export const V5_PROHIBITED_DATA_CLASSES = deepFreeze([
  "phi", "patient_identifier", "patient_record", "raw_patient_location", "patient_visit_detail",
]);
export const V5_INDEPENDENT_PRIVACY_ROUTE_CLASSES = deepFreeze([
  "aggregate_patient_location_heatmap", "aggregate_patient_volume_estimate",
]);
export const V5_PERMITTED_DATA_CLASSES = deepFreeze([
  "market_comp", "property_attribute", "practice_business_profile",
  "tenant_business_contact", "lease_economics", "public_registry_record",
]);
export const V5_DATA_CLASSES = deepFreeze([
  ...V5_PROHIBITED_DATA_CLASSES, ...V5_INDEPENDENT_PRIVACY_ROUTE_CLASSES, ...V5_PERMITTED_DATA_CLASSES,
].sort());
export const V5_PHI_AMENDMENT_REQUIRED =
  "explicit_future_compliance_and_product_scope_amendment";
export const V5_INDEPENDENT_PRIVACY_ROUTE_EVIDENCE =
  "step:global-no-phi-boundary-independent-receipt";

const PRIVACY_REQUEST_KEYS = Object.freeze(["data_classes", "intended_use"]);

export function evaluatePrivacyBoundary(request) {
  assertObject(request, "request");
  assertClosedKeys(request, PRIVACY_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["data_classes"], "request");
  assertStringArray(request.data_classes, "request.data_classes");
  if (request.data_classes.length === 0) {
    fail("missing_field", "request.data_classes must name at least one class", { path: "request.data_classes" });
  }
  if ("intended_use" in request && (typeof request.intended_use !== "string" || request.intended_use.length === 0)) {
    fail("invalid_shape", "request.intended_use must be a non-empty string when present",
      { path: "request.intended_use" });
  }
  for (const dataClass of request.data_classes) {
    if (!V5_DATA_CLASSES.includes(dataClass)) {
      fail("unknown_data_class", `"${dataClass}" is not a registered v5 data class`,
        { data_class: dataClass });
    }
  }
  const base = {
    data_classes: [...request.data_classes].sort(),
    intended_use: request.intended_use ?? null,
    phi_permitted: false,
  };
  const prohibited = request.data_classes.filter(c => V5_PROHIBITED_DATA_CLASSES.includes(c)).sort();
  if (prohibited.length > 0) {
    return deepFreeze({
      decision: "refuse", reason_id: "phi_or_raw_patient_location_refused", ...base,
      prohibited_classes: prohibited, amendment_required: V5_PHI_AMENDMENT_REQUIRED,
      permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
    });
  }
  const routed = request.data_classes.filter(c => V5_INDEPENDENT_PRIVACY_ROUTE_CLASSES.includes(c)).sort();
  if (routed.length > 0) {
    return deepFreeze({
      decision: "needs_independent_privacy_route",
      reason_id: "aggregate_input_requires_independent_privacy_route", ...base,
      routed_classes: routed, accepted: false,
      required_evidence: V5_INDEPENDENT_PRIVACY_ROUTE_EVIDENCE,
      permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
    });
  }
  return deepFreeze({
    decision: "allow", reason_id: "no_prohibited_or_routed_class_present", ...base,
    permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The model-judgment seam.
//
// A model may classify untrusted content, and only inside a named seam whose
// label set is closed. It cannot widen authority, scope or privacy policy, and
// the check is structural rather than a matter of trust: a proposal carrying a
// field whose name reaches into authority, scope or privacy refuses before its
// label is even considered.
// ---------------------------------------------------------------------------

export const V5_MODEL_CLASSIFICATION_SEAMS = deepFreeze({
  inbound_document_kind: ["lease", "loi", "listing_sheet", "financial_statement", "unclassified"],
  read_freshness_hint: ["likely_fresh", "likely_stale", "unknown"],
  counterparty_side_hint: ["tenant_side", "brokerage_side", "unknown"],
});

// Field-name fragments that mean the proposal reached past classification.
const MODEL_WIDENING_FRAGMENTS = deepFreeze([
  "authority", "authorization", "grant", "delegation", "redecision", "privilege",
  "scope", "representation_side", "activate", "capability", "capabilities",
  "phi", "privacy", "data_class", "tenant", "actor", "partner", "override",
]);

const MODEL_PROPOSAL_KEYS = Object.freeze(["seam", "label", "confidence", "evidence_ref"]);

export function applyModelClassification(proposal) {
  assertObject(proposal, "proposal");
  const base = {
    seam: typeof proposal.seam === "string" ? proposal.seam : null,
    widens_authority: false, widens_scope: false, widens_privacy_policy: false,
    permanent_privilege_granted: false, effects: V5_NO_EFFECTS,
  };
  // The widening check runs BEFORE the closed-key check, so the refusal names
  // what was actually attempted instead of a generic unknown field.
  for (const key of Object.keys(proposal)) {
    const normalized = key.toLowerCase();
    if (MODEL_PROPOSAL_KEYS.includes(key)) continue;
    if (MODEL_WIDENING_FRAGMENTS.some(fragment => normalized.includes(fragment))) {
      return deepFreeze({
        decision: "refuse", reason_id: "model_widening_refused", ...base,
        offending_field: key,
      });
    }
  }
  assertClosedKeys(proposal, MODEL_PROPOSAL_KEYS, "proposal");
  assertRequiredKeys(proposal, ["seam", "label"], "proposal");
  if (!Object.prototype.hasOwnProperty.call(V5_MODEL_CLASSIFICATION_SEAMS, proposal.seam)) {
    fail("unknown_model_seam", `"${proposal.seam}" is not a registered model-classification seam`,
      { seam: proposal.seam, registered: Object.keys(V5_MODEL_CLASSIFICATION_SEAMS) });
  }
  if ("confidence" in proposal &&
      (!Number.isFinite(proposal.confidence) || proposal.confidence < 0 || proposal.confidence > 1)) {
    fail("invalid_shape", "proposal.confidence must be a number between 0 and 1",
      { path: "proposal.confidence" });
  }
  const labels = V5_MODEL_CLASSIFICATION_SEAMS[proposal.seam];
  if (typeof proposal.label !== "string" || !labels.includes(proposal.label)) {
    return deepFreeze({
      decision: "refuse", reason_id: "model_label_outside_seam", ...base,
      label: typeof proposal.label === "string" ? proposal.label : null,
      registered_labels: [...labels],
    });
  }
  return deepFreeze({
    decision: "allow", reason_id: "model_classification_within_seam", ...base,
    label: proposal.label, confidence: proposal.confidence ?? null,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no timestamp, actor, session, machine or
// acceptance fact — so two callers describing the same policy reach the same
// digest. The digest is an identity for these bytes and nothing else: it is not
// an acceptance, not a receipt, and not evidence for any consumer gate.
// ---------------------------------------------------------------------------

export function v5BoundaryPolicyPreimage() {
  return {
    schema_version: V5_BOUNDARY_SCHEMA_VERSION,
    policy_version: V5_BOUNDARY_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_subset_canonical_sha256: V5_DECISION_SUBSET_CANONICAL_SHA256,
    decisions: V5_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
    actor_authority: {
      system_authority_partner: V5_SYSTEM_AUTHORITY_PARTNER,
      deferred_authority_partner: V5_DEFERRED_AUTHORITY_PARTNER,
      max_delegation_window_seconds: V5_MAX_DELEGATION_WINDOW_SECONDS,
      authority_classes: [...V5_AUTHORITY_CLASSES],
      ordinary_business_controls: [...V5_ORDINARY_BUSINESS_CONTROLS],
      actions: V5_ACTION_KEYS.map(action_key => ({
        action_key,
        authority_class: V5_ACTIONS[action_key].authority_class,
        required_controls: [...(V5_ACTIONS[action_key].required_controls ?? [])],
        required_capability: V5_ACTIONS[action_key].required_capability ?? null,
      })),
    },
    representation_scope: {
      sides: [...V5_REPRESENTATION_SIDES],
      exposed_sides: [...V5_EXPOSED_REPRESENTATION_SIDES],
      brokerage_side_structural_only: [...V5_BROKERAGE_SIDE_STRUCTURAL_ONLY],
      amendment_seam: V5_SCOPE_AMENDMENT_SEAM,
    },
    read_continuity: {
      operation_kinds: [...V5_OPERATION_KINDS],
      connectivity_states: [...V5_CONNECTIVITY_STATES],
      availability_states: [...V5_AVAILABILITY_STATES],
      max_cached_read_age_seconds: V5_MAX_CACHED_READ_AGE_SECONDS,
      accepts_offline_mutation: false,
      documented_fallback: V5_DOCUMENTED_FALLBACK,
    },
    local_platform: {
      optional_nodes: [...V5_OPTIONAL_LOCAL_NODES],
      node_states: [...V5_LOCAL_NODE_STATES],
      canonical_authority: V5_CANONICAL_AUTHORITY,
      nodes_may_hold_unique_authority: false,
      capabilities: Object.keys(V5_LOCAL_CAPABILITIES).sort().map(capability => ({
        capability, fallback: V5_LOCAL_CAPABILITIES[capability].fallback, authority_bearing: false,
      })),
    },
    privacy_boundary: {
      prohibited_data_classes: [...V5_PROHIBITED_DATA_CLASSES].sort(),
      independent_privacy_route_classes: [...V5_INDEPENDENT_PRIVACY_ROUTE_CLASSES].sort(),
      permitted_data_classes: [...V5_PERMITTED_DATA_CLASSES].sort(),
      phi_amendment_required: V5_PHI_AMENDMENT_REQUIRED,
      independent_privacy_route_evidence: V5_INDEPENDENT_PRIVACY_ROUTE_EVIDENCE,
    },
    model_judgment: {
      seams: Object.keys(V5_MODEL_CLASSIFICATION_SEAMS).sort().map(seam => ({
        seam, labels: [...V5_MODEL_CLASSIFICATION_SEAMS[seam]],
      })),
      may_widen_authority: false,
      may_widen_scope: false,
      may_widen_privacy_policy: false,
    },
  };
}

/** The deterministic `sha256:` digest of the closed v5 boundary policy. */
export function v5BoundaryPolicyDigest() {
  return digest(v5BoundaryPolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5BoundaryPolicyCanonicalBytes() {
  return canonicalJson(v5BoundaryPolicyPreimage());
}

/**
 * The zero-effect projection of the whole boundary: what is settled, what the
 * policy hashes to, and the explicit statement that reading it accepts nothing.
 */
export function v5BoundaryProjection(options = {}) {
  assertObject(options, "options");
  assertClosedKeys(options, ["expected_policy_digest"], "options");
  const policyDigest = v5BoundaryPolicyDigest();
  if (options.expected_policy_digest !== undefined) {
    if (typeof options.expected_policy_digest !== "string" ||
        !/^sha256:[0-9a-f]{64}$/.test(options.expected_policy_digest)) {
      fail("invalid_expected_digest", "options.expected_policy_digest must be a sha256: reference",
        { path: "options.expected_policy_digest" });
    }
    if (options.expected_policy_digest !== policyDigest) {
      fail("stale_expected_digest",
        "the policy no longer hashes to the expected digest; re-read it rather than acting on the stale one",
        { expected: options.expected_policy_digest, actual: policyDigest });
    }
  }
  return deepFreeze({
    schema_version: V5_BOUNDARY_SCHEMA_VERSION,
    policy_version: V5_BOUNDARY_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    policy_digest: policyDigest,
    decision_ids: [...V5_SETTLED_DECISION_IDS],
    decision_subset_canonical_sha256: V5_DECISION_SUBSET_CANONICAL_SHA256,
    // Later runtime and acceptance inputs. Named so nobody mistakes this
    // projection for one of them; none is produced or satisfied here.
    portfolio_constitution_accepted: false,
    gate_zero_transitioned: false,
    global_no_phi_boundary_receipt_present: false,
    accepts_anything: false,
    effects: V5_NO_EFFECTS,
  });
}
