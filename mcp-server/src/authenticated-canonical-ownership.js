// The engineering controller is not a normal MCP profile.  Its bearer is
// allowed to name exactly one of four canonical-ownership operations, and the
// operation's work/plan/executor authority is re-read by the Worker.

import { sha256 } from "./sha256.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LEASE_REF = /^canonical-ownership-lease:([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i;
const OPERATION_REF = /^engineering-controller-operation:g([12]):([a-z-]+):([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i;

export const ENGINEERING_CONTROLLER_AUTHORITY_PROFILE = "engineering-ownership-controller";
export const ENGINEERING_CONTROLLER_WORKER = "room-bridge-engineering-controller";
export const ENGINEERING_CONTROLLER_DESK = "engineering-codex";
export const ENGINEERING_CONTROLLER_EXECUTOR = "codex";

export const ENGINEERING_CONTROLLER_OPERATIONS = Object.freeze([
  "canonical-ownership-acquire",
  "canonical-ownership-check",
  "canonical-ownership-renew",
  "canonical-ownership-release",
]);

const LEASE_OPERATIONS = new Set(ENGINEERING_CONTROLLER_OPERATIONS.slice(1));
const exactKeys = (value, keys) => value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).sort().join(",") === [...keys].sort().join(",");

function uuid(value, field) {
  if (typeof value !== "string" || !UUID.test(value)) {
    const error = new Error("engineering_controller_input_invalid");
    error.payload = { error: "engineering_controller_input_invalid", field };
    throw error;
  }
  return value;
}

/**
 * Accept only an isolated, single-principal token map.  Delegating the actual
 * bearer comparison to identity.js preserves its authenticated-connection
 * brand; this guard makes an accidental second map entry a refusal, not a
 * second controller identity.
 */
export function engineeringControllerActorForToken(authorizationHeader, rawTokens, actorForToken) {
  let tokens;
  try { tokens = JSON.parse(rawTokens || "{}"); } catch { return null; }
  if (!exactKeys(tokens, [ENGINEERING_CONTROLLER_EXECUTOR]) ||
      typeof tokens.codex !== "string" || !tokens.codex) return null;
  const actor = actorForToken(authorizationHeader, rawTokens, "engineering-controller-token");
  if (!actor || actor.slug !== ENGINEERING_CONTROLLER_EXECUTOR ||
      actor.via !== "engineering-controller-token") return null;
  // Identity's connection brand is object identity.  Decorate the branded
  // object in place rather than cloning it.
  return Object.assign(actor, {
    authority_profile: ENGINEERING_CONTROLLER_AUTHORITY_PROFILE,
    engineering_controller: true,
    controller_desk: ENGINEERING_CONTROLLER_DESK,
  });
}

export function isEngineeringControllerActor(actor) {
  return actor?.engineering_controller === true &&
    actor?.slug === ENGINEERING_CONTROLLER_EXECUTOR &&
    actor?.via === "engineering-controller-token" &&
    actor?.authority_profile === ENGINEERING_CONTROLLER_AUTHORITY_PROFILE &&
    actor?.controller_desk === ENGINEERING_CONTROLLER_DESK;
}

/** Refuse every normal MCP tool before a database connection is opened. */
export function controllerOperationInput(name, args) {
  if (!ENGINEERING_CONTROLLER_OPERATIONS.includes(name)) {
    const error = new Error("engineering_controller_tool_refused");
    error.payload = { error: "engineering_controller_tool_refused", tool: name || null };
    throw error;
  }
  const keys = LEASE_OPERATIONS.has(name)
    ? ["job_id", "lease_token", "lease_ref", "operation_ref"] : ["job_id", "lease_token"];
  if (!exactKeys(args, keys)) {
    const error = new Error("engineering_controller_input_invalid");
    error.payload = { error: "engineering_controller_input_invalid", operation: name };
    throw error;
  }
  const input = { job_id: uuid(args.job_id, "job_id"), lease_token: uuid(args.lease_token, "lease_token") };
  if (LEASE_OPERATIONS.has(name)) {
    const match = typeof args.lease_ref === "string" ? args.lease_ref.match(LEASE_REF) : null;
    if (!match) {
      const error = new Error("engineering_controller_lease_ref_invalid");
      error.payload = { error: "engineering_controller_lease_ref_invalid" };
      throw error;
    }
    input.lease_id = match[1].toLowerCase();
    input.lease_ref = `canonical-ownership-lease:${input.lease_id}`;
    const operation = typeof args.operation_ref === "string" ? args.operation_ref.match(OPERATION_REF) : null;
    if (!operation || !ENGINEERING_CONTROLLER_OPERATIONS.includes(`canonical-ownership-${operation[2]}`)) {
      const error = new Error("engineering_controller_operation_ref_invalid");
      error.payload = { error: "engineering_controller_operation_ref_invalid" };
      throw error;
    }
    input.issuer_generation = Number(operation[1]);
    input.prior_operation_key = operation[3].toLowerCase();
  }
  return Object.freeze(input);
}

export function controllerToolList() {
  return ENGINEERING_CONTROLLER_OPERATIONS.map(name => ({
    name,
    description: "Engineering controller canonical ownership operation.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: LEASE_OPERATIONS.has(name) ? ["job_id", "lease_token", "lease_ref", "operation_ref"] : ["job_id", "lease_token"],
      properties: {
        job_id: { type: "string" }, lease_token: { type: "string" },
        ...(LEASE_OPERATIONS.has(name) ? { lease_ref: { type: "string" }, operation_ref: { type: "string" } } : {}),
      },
    },
    annotations: { readOnlyHint: false, destructiveHint: name === "canonical-ownership-release", idempotentHint: true, openWorldHint: false },
  }));
}

// Nothing in a controller response names a database primary key or returns a
// secret.  The opaque lease reference can be supplied to the next operation;
// the job lease remains live authority and is revalidated each time.
export function opaqueControllerResult(operation, result, operationKey, issuerGeneration) {
  if (!result || result.ok !== true || typeof result.lease_id !== "string" || !UUID.test(result.lease_id)) {
    const error = new Error("engineering_controller_operation_refused");
    error.payload = { error: result?.error || "engineering_controller_operation_refused" };
    throw error;
  }
  return Object.freeze({
    ok: true,
    operation_ref: `engineering-controller-operation:g${issuerGeneration}:${operation.replace(/^canonical-ownership-/, "")}:${operationKey}`,
    lease_ref: `canonical-ownership-lease:${result.lease_id.toLowerCase()}`,
    state: operation === "canonical-ownership-release" ? "released" : "active",
  });
}

// A lifecycle operation needs a stable retry identity, but no client receives
// or chooses one.  Derive a RFC4122 v5-shaped UUID from server-confirmed
// identifiers.  The database still binds it to the minted runtime session.
export function operationIdempotencyKey(operation, stableSubject) {
  const hex = sha256(`${operation}:${stableSubject}`).slice(0, 32).split("");
  hex[12] = "5";
  hex[16] = "89ab"[parseInt(hex[16], 16) % 4];
  return `${hex.slice(0, 8).join("")}-${hex.slice(8, 12).join("")}-${hex.slice(12, 16).join("")}-${hex.slice(16, 20).join("")}-${hex.slice(20, 32).join("")}`;
}
