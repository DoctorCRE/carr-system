// V5-S01 — an INDEPENDENT check of #929's "every present grant is validated" fix.
//
// The #929 suite proves the fix case by case, written by the same hand as the
// fix. This file does not reuse its cases. It states the property as an oracle
// and enumerates the whole grant space against it:
//
//   On a deferred-class action (developer / release_admin) for the deferred
//   partner, the answer is ALLOW if and only if at least one grant is present
//   AND every present grant is valid for this exact action now. When it
//   refuses, the reason is the redecision's own reason if the redecision is
//   invalid, otherwise the delegation's, otherwise deferred_authority_requires_grant.
//   An allow names only grants that were actually validated.
//
// Every grant shape is built independently here, including each distinct
// failure the evaluator is documented to refuse. Scope findings sit at the end:
// what the fix does NOT cover, pinned so any change to it is deliberate.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_ACTIONS,
  V5_ACTION_KEYS,
  evaluateActorAuthority,
} from "../src/global-boundaries.v5.js";

const NOW = "2026-09-25T12:00:00Z";
const DELL = Object.freeze({ slug: "dell", human: true, via: "oauth-google" });
const JOE = Object.freeze({ slug: "joe", human: true, via: "oauth-google" });
const HEX = c => c.repeat(64);

const DEFERRED = V5_ACTION_KEYS.filter(a =>
  ["developer", "release_admin"].includes(V5_ACTIONS[a].authority_class));

// Grant factories, bound to the requested action. `valid` is the one shape that
// must authorize; every other entry names the reason the evaluator must give.
function redecisions(action) {
  const other = DEFERRED.find(a => a !== action);
  const ok = { redecision_ref: "R-ind", decided_by: "joe", subject: "dell", action,
    cited_source_digest: HEX("c"), decided_at: "2026-09-20T00:00:00Z" };
  return [
    ["valid", ok, null],
    ["foreign-author", { ...ok, decided_by: "dell" }, "redecision_author_not_system_authority"],
    ["foreign-subject", { ...ok, subject: "joe" }, "redecision_subject_mismatch"],
    ["cross-action", { ...ok, action: other }, "redecision_action_mismatch"],
    ["system-action", { ...ok, action: "system.policy" }, "system_authority_not_delegable"],
    ["wildcard", { ...ok, action: "all" }, "redecision_wildcard_refused"],
    ["uncited", { ...ok, cited_source_digest: "x" }, "redecision_uncited"],
    ["future", { ...ok, decided_at: "2026-10-01T00:00:00Z" }, "redecision_not_yet_effective"],
    ["blank-ref", { ...ok, redecision_ref: " " }, "redecision_ref_invalid"],
  ];
}

function delegations(action) {
  const other = DEFERRED.find(a => a !== action);
  const ok = { delegation_ref: "D-ind", granted_by: "joe", granted_to: "dell", action,
    receipt_digest: HEX("d"), issued_at: "2026-09-25T00:00:00Z", expires_at: "2026-09-26T00:00:00Z" };
  return [
    ["valid", ok, null],
    ["foreign-grantor", { ...ok, granted_by: "dell" }, "delegation_grantor_not_system_authority"],
    ["foreign-grantee", { ...ok, granted_to: "joe" }, "delegation_subject_mismatch"],
    ["cross-action", { ...ok, action: other }, "delegation_action_mismatch"],
    ["system-action", { ...ok, action: "system.security" }, "system_authority_not_delegable"],
    ["wildcard", { ...ok, action: "developer.*" }, "delegation_wildcard_refused"],
    ["unreceipted", { ...ok, receipt_digest: HEX("D") }, "delegation_unreceipted"],
    ["permanent", { ...ok, expires_at: "never" }, "delegation_permanent_refused"],
    ["inverted", { ...ok, expires_at: "2026-09-24T00:00:00Z" }, "delegation_window_invalid"],
    ["overbroad", { ...ok, issued_at: "2026-09-20T00:00:00Z", expires_at: "2026-09-27T00:00:01Z" },
      "delegation_window_overbroad"],
    ["not-yet", { ...ok, issued_at: "2026-09-25T13:00:00Z", expires_at: "2026-09-26T00:00:00Z" },
      "delegation_not_yet_effective"],
    ["expired", { ...ok, issued_at: "2026-09-24T00:00:00Z", expires_at: "2026-09-25T12:00:00Z" },
      "delegation_expired"],
    ["blank-ref", { ...ok, delegation_ref: "" }, "delegation_ref_invalid"],
  ];
}

function oracle(redecision, delegation) {
  const present = [redecision, delegation].filter(Boolean);
  if (present.length === 0) return { decision: "refuse", reason_id: "deferred_authority_requires_grant" };
  if (redecision && redecision[2] !== null) return { decision: "refuse", reason_id: redecision[2] };
  if (delegation && delegation[2] !== null) return { decision: "refuse", reason_id: delegation[2] };
  return { decision: "allow" };
}

test("independent: the grant space on every deferred action matches the oracle exactly", () => {
  let cases = 0;
  let allows = 0;
  for (const action of DEFERRED) {
    for (const redecision of [null, ...redecisions(action)]) {
      for (const delegation of [null, ...delegations(action)]) {
        const request = { actor: DELL, action, tenant: ORGANIZATION_TENANT_ID, now: NOW };
        if (redecision) request.redecision = redecision[1];
        if (delegation) request.delegation = delegation[1];
        const got = evaluateActorAuthority(request);
        const want = oracle(redecision, delegation);
        const label = `${action} redecision=${redecision?.[0] ?? "none"} delegation=${delegation?.[0] ?? "none"}`;
        assert.equal(got.decision, want.decision, label);
        if (want.decision === "refuse") assert.equal(got.reason_id, want.reason_id, label);
        if (got.decision === "allow") {
          allows += 1;
          // An allow names exactly the grants that were present, all of which were valid.
          const expected = [...(redecision ? ["cited_joe_redecision"] : []),
            ...(delegation ? ["narrow_expiring_receipted_delegation"] : [])];
          assert.deepEqual([...got.validated_grants], expected, label);
          assert.equal(got.permanent_privilege_granted, false);
        }
        cases += 1;
      }
    }
  }
  // 4 deferred actions x (1+9) redecision shapes x (1+13) delegation shapes.
  assert.equal(cases, DEFERRED.length * 10 * 14);
  // Allows are exactly: valid alone (2 ways) and valid+valid, per action.
  assert.equal(allows, DEFERRED.length * 3);
});

test("independent: the answer does not depend on which grant the caller lists first", () => {
  for (const action of DEFERRED) {
    for (const redecision of redecisions(action)) {
      for (const delegation of delegations(action)) {
        const a = evaluateActorAuthority({ actor: DELL, action, tenant: ORGANIZATION_TENANT_ID, now: NOW,
          redecision: redecision[1], delegation: delegation[1] });
        const b = evaluateActorAuthority({ delegation: delegation[1], redecision: redecision[1],
          now: NOW, tenant: ORGANIZATION_TENANT_ID, action, actor: DELL });
        assert.deepEqual(JSON.parse(JSON.stringify(a)), JSON.parse(JSON.stringify(b)));
      }
    }
  }
});

test("independent: an unreadable second grant fails closed even beside a valid first", () => {
  const action = "developer.change_source";
  const [, validRedecision] = redecisions(action)[0];
  const [, validDelegation] = delegations(action)[0];
  for (const bad of [{ ...validDelegation, issued_at: "2026-02-31T00:00:00Z" },
    { ...validDelegation, extra: 1 }, "grant", 7]) {
    assert.throws(() => evaluateActorAuthority({ actor: DELL, action, tenant: ORGANIZATION_TENANT_ID,
      now: NOW, redecision: validRedecision, delegation: bad }), e => e.name === "V5BoundaryError");
  }
  for (const bad of [{ ...validRedecision, decided_at: "yesterday" }, []]) {
    assert.throws(() => evaluateActorAuthority({ actor: DELL, action, tenant: ORGANIZATION_TENANT_ID,
      now: NOW, redecision: bad, delegation: validDelegation }), e => e.name === "V5BoundaryError");
  }
});

// ---------------------------------------------------------- scope findings
//
// FINDING (independent check, 2026-09-25): the fix is scoped to the deferred
// path, which is the only path where a grant can contribute to an allow. On the
// retained path (Joe on any non-system action class he holds inherently) and
// on the ordinary-business path, a present grant is NOT read at all — neither
// validated nor refused. That cannot widen authority, because neither allow
// depends on a grant; and the allow carries no grant_ref, so an unread grant
// cannot surface later as provenance. Pinned here so a change is deliberate.

test("scope: off the deferred path a present grant is unread and never appears as provenance", () => {
  const badDelegation = { delegation_ref: "D-bad", granted_by: "dell", granted_to: "dell",
    action: "*", receipt_digest: "x", issued_at: "2026-09-25T00:00:00Z", expires_at: "never" };
  const joe = evaluateActorAuthority({ actor: JOE, action: "developer.change_source",
    tenant: ORGANIZATION_TENANT_ID, now: NOW, delegation: badDelegation });
  assert.equal(joe.decision, "allow");
  assert.equal(joe.reason_id, "system_authority_retained");
  assert.equal("grant_ref" in joe, false);
  assert.equal("validated_grants" in joe, false);

  const dellBusiness = evaluateActorAuthority({ actor: DELL, action: "business.read_deal",
    tenant: ORGANIZATION_TENANT_ID, now: NOW, delegation: badDelegation,
    controls: { deal_owner_slug: "dell", policy_scope: ["business.read_deal"], capabilities: ["deal.read"] } });
  assert.equal(dellBusiness.decision, "allow");
  assert.equal("grant_ref" in dellBusiness, false);

  // And a grant can never lift Dell into a system-authority action, valid or not.
  for (const action of V5_ACTION_KEYS.filter(a => V5_ACTIONS[a].authority_class === "system_authority")) {
    const answer = evaluateActorAuthority({ actor: DELL, action, tenant: ORGANIZATION_TENANT_ID, now: NOW,
      delegation: { ...delegations("developer.change_source")[0][1], action } });
    assert.equal(answer.reason_id, "system_authority_reserved_to_joe", action);
  }
});
