import assert from "node:assert/strict";
import test from "node:test";

import {
  ENGINEERING_CONTROLLER_AUTHORITY_PROFILE,
  ENGINEERING_CONTROLLER_OPERATIONS,
  controllerOperationInput,
  engineeringControllerActorForToken,
  opaqueControllerResult,
  operationIdempotencyKey,
} from "../src/authenticated-canonical-ownership.js";

const JOB = "11111111-1111-4111-8111-111111111111";
const LEASE = "22222222-2222-4222-8222-222222222222";
const OWNERSHIP = "33333333-3333-4333-8333-333333333333";

test("the isolated controller token map resolves only branded codex", () => {
  const called = [];
  const actor = engineeringControllerActorForToken("Bearer controller-secret", JSON.stringify({ codex: "controller-secret" }),
    (header, raw, via) => {
      called.push({ header, raw, via });
      return { slug: "codex", via, branded: true };
    });
  assert.equal(actor.authority_profile, ENGINEERING_CONTROLLER_AUTHORITY_PROFILE);
  assert.equal(actor.engineering_controller, true);
  assert.deepEqual(called.map(item => item.via), ["engineering-controller-token"]);
  assert.equal(engineeringControllerActorForToken("Bearer controller-secret", JSON.stringify({ codex: "controller-secret", claude: "other" }), () => ({ slug: "codex" })), null);
  assert.equal(engineeringControllerActorForToken("Bearer controller-secret", JSON.stringify({ codex: "controller-secret" }), () => ({ slug: "claude", via: "engineering-controller-token" })), null);
});

test("controller operations reject unrelated tools and caller authority fields", () => {
  assert.deepEqual(ENGINEERING_CONTROLLER_OPERATIONS, [
    "canonical-ownership-acquire", "canonical-ownership-check",
    "canonical-ownership-renew", "canonical-ownership-release",
  ]);
  assert.throws(() => controllerOperationInput("standing-context", {}), /engineering_controller_tool_refused/);
  assert.throws(() => controllerOperationInput("canonical-ownership-acquire", {
    job_id: JOB, lease_token: LEASE, executor: "codex",
  }), /engineering_controller_input_invalid/);
  assert.deepEqual(controllerOperationInput("canonical-ownership-acquire", { job_id: JOB, lease_token: LEASE }),
    { job_id: JOB, lease_token: LEASE });
});

test("follow-up operations accept opaque refs, never raw canonical lease authority", () => {
  const previous = operationIdempotencyKey("canonical-ownership-acquire", JOB);
  const input = controllerOperationInput("canonical-ownership-check", {
    job_id: JOB, lease_token: LEASE,
    lease_ref: `canonical-ownership-lease:${OWNERSHIP}`,
    operation_ref: `engineering-controller-operation:g1:acquire:${previous}`,
  });
  assert.equal(input.lease_id, OWNERSHIP);
  assert.equal(input.issuer_generation, 1);
  assert.equal(input.prior_operation_key, previous);
  assert.throws(() => controllerOperationInput("canonical-ownership-check", {
    job_id: JOB, lease_token: LEASE, lease_ref: `canonical-ownership-lease:${OWNERSHIP}`,
    operation_ref: `engineering-controller-operation:g1:acquire:${previous}`, fencing_generation: 1,
  }), /engineering_controller_input_invalid/);
  assert.throws(() => controllerOperationInput("canonical-ownership-check", {
    job_id: JOB, lease_token: LEASE, lease_ref: `canonical-ownership-lease:${OWNERSHIP}`,
    operation_ref: `engineering-controller-operation:g3:acquire:${previous}`,
  }), /engineering_controller_operation_ref_invalid/);
});

test("controller responses return opaque references and deterministic retry identities", () => {
  const key = operationIdempotencyKey("canonical-ownership-acquire", JOB);
  assert.match(key, /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(key, operationIdempotencyKey("canonical-ownership-acquire", JOB));
  const response = opaqueControllerResult("canonical-ownership-acquire", {
    ok: true, lease_id: OWNERSHIP, lease_token: "must-not-escape", fencing_generation: 7,
  }, key, 2);
  assert.deepEqual(response, {
    ok: true,
    operation_ref: `engineering-controller-operation:g2:acquire:${key}`,
    lease_ref: `canonical-ownership-lease:${OWNERSHIP}`,
    state: "active",
  });
  assert.doesNotMatch(JSON.stringify(response), /must-not-escape|fencing_generation/);
});
