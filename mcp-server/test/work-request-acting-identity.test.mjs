import test from "node:test";
import assert from "node:assert/strict";
import { actingIdentityProjection } from "../src/work-request-intake.js";

// Every authority receipt on a Work Request records a HUMAN slug, because
// ops.authority_actor_slug() maps the Postgres session role to joe or dell and
// the receipt columns are constrained to a human actor. Once Joe ruled that agent
// sessions carry his sponsored authority for internal system work, a card that
// can only ever say "joe" stopped being true enough to read.
//
// The distinguishing evidence was never missing — public.tool_call holds
// actor_id, authorization_class and via under the same idempotency key each
// receipt stores. These cases pin the join's answers.

const AT = new Date("2026-08-27T03:29:55.605Z");

test("an agent acting under a sponsorship reads as an agent, not as the sponsor", () => {
  const [row] = actingIdentityProjection([{
    act: "accept-ready-plan", recorded_slug: "joe", acted_at: AT,
    actor_slug: "joe-local", authorization_class: "sponsored_agent", via: "local-token",
  }]);
  assert.equal(row.recorded_as, "joe");
  assert.equal(row.performed_by, "joe-local");
  assert.equal(row.hand, "agent");
  assert.equal(row.via, "local-token");
});

test("a human at a keyboard reads as a human", () => {
  const [row] = actingIdentityProjection([{
    act: "review-and-triage", recorded_slug: "joe", acted_at: AT,
    actor_slug: "joe", authorization_class: "partner", via: "oauth-google",
  }]);
  assert.equal(row.hand, "human");
  assert.equal(row.performed_by, "joe");
});

test("no ledger row is reported as unknown, never guessed into a hand", () => {
  const [row] = actingIdentityProjection([{
    act: "review-and-triage", recorded_slug: "joe", acted_at: AT,
    actor_slug: null, authorization_class: null, via: null,
  }]);
  assert.equal(row.hand, "unknown");
  assert.equal(row.performed_by, null);
  assert.equal(row.authorization_class, null);
});

test("the recorded slug is preserved rather than overwritten by the real actor", () => {
  // The receipt is not being corrected — it cannot hold an agent identity. The
  // card reports both, so a reader can see the constraint rather than infer it.
  const [row] = actingIdentityProjection([{
    act: "accept-outcome-feedback", recorded_slug: "joe", acted_at: AT,
    actor_slug: "claude", authorization_class: "sponsored_agent", via: "oauth-google",
  }]);
  assert.equal(row.recorded_as, "joe");
  assert.equal(row.performed_by, "claude");
  assert.notEqual(row.recorded_as, row.performed_by);
});

test("every act on a request is projected, in order, with its own verdict", () => {
  const rows = actingIdentityProjection([
    { act: "review-and-triage", recorded_slug: "joe", acted_at: AT, actor_slug: "claude",
      authorization_class: "sponsored_agent", via: "oauth-google" },
    { act: "accept-ready-plan", recorded_slug: "joe", acted_at: AT, actor_slug: "joe",
      authorization_class: "partner", via: "oauth-google" },
  ]);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map((r) => r.act), ["review-and-triage", "accept-ready-plan"]);
  assert.deepEqual(rows.map((r) => r.hand), ["agent", "human"]);
});
