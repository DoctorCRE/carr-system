import test from "node:test";
import assert from "node:assert/strict";

import {
  FOUNDATION_ASSURANCE_ORACLE_ACTORS,
  FOUNDATION_ASSURANCE_ORACLE_VERBS,
  FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION,
  assertFoundationAssuranceOracleSeat,
  foundationAssuranceOracleLane,
  foundationAssuranceProducerForVerb,
} from "../src/foundation-assurance-minimum-registration.v5.js";

test("registration is a closed nine-verb, nine-actor assignment", () => {
  assert.equal(FOUNDATION_ASSURANCE_ORACLE_VERBS.length, 9);
  assert.equal(FOUNDATION_ASSURANCE_ORACLE_ACTORS.length, 9);
  assert.equal(FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers.length, 9);
  assert.equal(Object.isFrozen(FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION), true);
  assert.equal(FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers
    .filter(item => item.kind === "member_receipt").length, 7);
  assert.equal(FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers
    .filter(item => item.kind === "benchmark_coverage").length, 1);
  assert.equal(FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers
    .filter(item => item.kind === "minimum_outcome").length, 1);
});

test("every verb resolves to exactly one immutable lane", () => {
  for (const verb of FOUNDATION_ASSURANCE_ORACLE_VERBS) {
    const row = foundationAssuranceProducerForVerb(verb);
    assert.equal(row.verb, verb);
    assert.equal(foundationAssuranceOracleLane(verb), row.actor_slug);
    assert.equal(Object.isFrozen(row), true);
  }
  assert.equal(foundationAssuranceProducerForVerb("unknown"), null);
  assert.equal(foundationAssuranceOracleLane("unknown"), null);
});

test("seat assertion requires the exact review-token actor and bound identity", () => {
  const verb = "produce-global-secrets-boundary-receipt";
  const actor = { slug: "codex-fa-secrets", human: false, review: true, via: "review-token" };
  const identity = {
    actor_id: actor.slug,
    session_ref: "session:00000000-0000-4000-8000-000000000001",
    authority_class: "review_agent",
  };
  assert.equal(assertFoundationAssuranceOracleSeat(actor, verb, identity).identity, identity);
  for (const wrong of [
    { ...actor, slug: "codex-fa-source" },
    { ...actor, human: true },
    { ...actor, review: false },
    { ...actor, via: "agent-token" },
  ]) assert.throws(() => assertFoundationAssuranceOracleSeat(wrong, verb, identity),
    /foundation_assurance_oracle_seat_required/);
  assert.throws(() => assertFoundationAssuranceOracleSeat(actor, verb,
    { ...identity, actor_id: "codex-fa-source" }), /foundation_assurance_oracle_identity_unavailable/);
});
