import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { ownershipIssuerConnection } from "../src/mcp.js";

const source = await readFile(new URL("../src/mcp.js", import.meta.url), "utf8");
const entry = await readFile(new URL("../src/index.js", import.meta.url), "utf8");
const auth = await readFile(new URL("../src/authenticated-canonical-ownership.js", import.meta.url), "utf8");

test("the controller runs through the isolated token door before normal MCP dispatch", () => {
  assert.match(entry, /ENGINEERING_CONTROLLER_TOKENS/);
  assert.match(auth, /engineering-controller-token/);
  const route = entry.slice(entry.indexOf("async function routeRequest"));
  assert.ok(route.indexOf("controllerActor") < route.indexOf("probeActor"));
  assert.match(entry, /dispatchEngineeringController\(request, env, ctx, controllerActor\)/);
  assert.match(entry, /execution_host_id: canonicalOwnershipExecutionHost\(env\)/);
});

test("the controller has a closed issuer-only lifecycle boundary", () => {
  assert.match(source, /ops\.authenticated_canonical_ownership_controller_binding/);
  assert.match(source, /ops\.canonical_ownership_lifecycle\(\$1::jsonb\)/);
  assert.match(source, /ops\.read_canonical_ownership_operation\(\$1::uuid\)/);
  assert.match(source, /DATABASE_URL_OWNERSHIP_ISSUER_G1/);
  assert.match(source, /DATABASE_URL_OWNERSHIP_ISSUER_G2/);
  assert.match(source, /"canary_only", "attended_active"/);
  assert.match(source, /CANONICAL_OWNERSHIP_CANARY_JOB_ID/);
  assert.match(source, /input\.issuer_generation \|\| Number\(env\?\.CANONICAL_OWNERSHIP_ISSUER_ACTIVE_GENERATION\)/);
  assert.doesNotMatch(source, /ops\.acquire_canonical_ownership_lease\(/);
  assert.doesNotMatch(source, /ops\.renew_canonical_ownership_lease\(/);
});

test("the dispatcher does not fall back to the regular read-permissive tool list", () => {
  const block = source.slice(source.indexOf("export async function dispatchEngineeringController"), source.indexOf("export const FOUNDATION_ASSURANCE_RUNTIME_BINDING_SCHEMA"));
  assert.match(block, /controllerToolList\(\)/);
  assert.match(block, /controllerOperationInput/);
  assert.doesNotMatch(block, /return dispatch\(/);
  assert.doesNotMatch(block, /DATABASE_URL_WRITER/);
});

test("issuer routing pins follow-ups to their generation and scopes canary mode to one job", () => {
  const env = {
    CANONICAL_OWNERSHIP_RUNTIME_MODE: "attended_active",
    CANONICAL_OWNERSHIP_ISSUER_ACTIVE_GENERATION: "2",
    DATABASE_URL_OWNERSHIP_ISSUER_G1: "postgres://g1",
    DATABASE_URL_OWNERSHIP_ISSUER_G2: "postgres://g2",
  };
  assert.deepEqual(ownershipIssuerConnection(env, { job_id: "job" }),
    { connectionString: "postgres://g2", generation: 2 });
  assert.deepEqual(ownershipIssuerConnection(env, { job_id: "job", issuer_generation: 1 }),
    { connectionString: "postgres://g1", generation: 1 });

  const canary = { ...env, CANONICAL_OWNERSHIP_RUNTIME_MODE: "canary_only",
    CANONICAL_OWNERSHIP_CANARY_JOB_ID: "chosen-job" };
  assert.equal(ownershipIssuerConnection(canary, { job_id: "other-job" }), null);
  assert.deepEqual(ownershipIssuerConnection(canary, { job_id: "chosen-job" }),
    { connectionString: "postgres://g2", generation: 2 });
});
