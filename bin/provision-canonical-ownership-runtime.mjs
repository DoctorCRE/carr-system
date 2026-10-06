#!/usr/bin/env node
// Canonical-ownership issuer lifecycle planner.
//
// This file is intentionally a dry-run-only control surface in WR126.  It
// describes the two generation-specific login/secret slots and validates the
// state transitions that a future, separately authorised provisioner may use.
// It never opens a database connection, calls Wrangler, reads a credential,
// writes a secret, or emits secret material.

import { readFile } from "node:fs/promises";

export const RUNTIME_MODES = Object.freeze([
  "disabled", "canary_only", "attended_active",
]);
export const ISSUER_STATES = Object.freeze(["revoked", "active", "draining"]);
export const OPERATIONS = Object.freeze(["stage", "rotate", "drain", "revoke", "readback"]);

// Names are part of the eventual Worker/Neon contract.  Generation is in both
// the login and the secret slot so a rotation cannot accidentally overwrite a
// value that another generation still uses.
export const ISSUER_SLOTS = Object.freeze({
  1: Object.freeze({
    generation: 1,
    capability: "carr_ownership_issuer",
    login: "carr_ownership_issuer_g1",
    secret: "DATABASE_URL_OWNERSHIP_ISSUER_G1",
  }),
  2: Object.freeze({
    generation: 2,
    capability: "carr_ownership_issuer",
    login: "carr_ownership_issuer_g2",
    secret: "DATABASE_URL_OWNERSHIP_ISSUER_G2",
  }),
});

const SECRET_LIKE = /(?:secret|token|password|credential|private|bearer|dsn|database_url)/i;

function fail(message, code = 64) {
  const error = new Error(message);
  error.code = code;
  throw error;
}

function generation(value) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || !ISSUER_SLOTS[parsed]) fail("generation must be 1 or 2");
  return parsed;
}

function mode(value) {
  if (!RUNTIME_MODES.includes(value)) fail(`mode must be one of ${RUNTIME_MODES.join(", ")}`);
  return value;
}

function operation(value) {
  if (!OPERATIONS.includes(value)) fail(`operation must be one of ${OPERATIONS.join(", ")}`);
  return value;
}

function advanceMode(current, requested) {
  if (requested === null || requested === undefined) return current;
  mode(requested);
  if (current === "disabled" && requested !== "canary_only")
    fail("disabled may transition only to canary_only");
  if (current === "canary_only" && !["canary_only", "attended_active"].includes(requested))
    fail("canary_only may transition only to canary_only or attended_active");
  if (current === "attended_active" && requested !== "attended_active")
    fail("attended_active cannot be moved backward by this planner");
  return requested;
}

export function emptyState() {
  return {
    schema_version: "canonical-ownership-issuer-runtime.v1",
    mode: "disabled",
    active_generation: null,
    generations: Object.fromEntries(Object.values(ISSUER_SLOTS).map(slot => [String(slot.generation), {
      generation: slot.generation,
      capability: slot.capability,
      login: slot.login,
      secret_slot: slot.secret,
      state: "revoked",
    }])),
  };
}

function copyState(input) {
  const value = input && typeof input === "object" && !Array.isArray(input) ? input : emptyState();
  const result = structuredClone(value);
  if (result.schema_version !== "canonical-ownership-issuer-runtime.v1") fail("state schema version is invalid");
  mode(result.mode);
  if (result.active_generation !== null) generation(result.active_generation);
  for (const slot of Object.values(ISSUER_SLOTS)) {
    const row = result.generations?.[String(slot.generation)];
    if (!row || row.capability !== slot.capability || row.login !== slot.login || row.secret_slot !== slot.secret ||
        !ISSUER_STATES.includes(row.state)) fail("issuer state has an invalid generation slot");
  }
  const active = Object.values(result.generations).filter(row => row.state === "active");
  if (active.length > 1) fail("at most one issuer generation may be active");
  if ((active[0]?.generation ?? null) !== (result.active_generation ?? null))
    fail("active_generation does not match issuer slot state");
  return result;
}

function redactedValue(value) {
  if (Array.isArray(value)) return value.map(redactedValue);
  if (!value || typeof value !== "object") return value;
  const result = {};
  for (const [key, child] of Object.entries(value)) {
    if (key === "secret_slot") {
      result[key] = child;
    } else if (SECRET_LIKE.test(key)) {
      result[key] = child === null || child === undefined || child === ""
        ? null : { present: true };
    } else {
      result[key] = redactedValue(child);
    }
  }
  return result;
}

export function redactedReadback(state) {
  // The planner itself never accepts secret values, but keeping this scrubber
  // at the public readback seam prevents a future state fixture from turning
  // into an accidental credential echo.
  return redactedValue(copyState(state));
}

export function transition(input, requestedOperation, requestedGeneration = null, requestedModeValue = null) {
  const state = copyState(input);
  const op = operation(requestedOperation);
  const next = structuredClone(state);
  const selected = requestedGeneration === null ? null : generation(requestedGeneration);
  if (requestedModeValue !== null) mode(requestedModeValue);

  if (op === "readback") return next;
  if (op === "stage") {
    if (selected === null) fail("stage requires a generation");
    if (state.mode === "attended_active") fail("stage is refused while attended_active");
    const row = next.generations[String(selected)];
    if (row.state !== "revoked") fail("stage requires a revoked generation");
    row.state = "active";
    next.active_generation = selected;
    next.mode = requestedModeValue === null
      ? (state.mode === "disabled" ? "canary_only" : state.mode)
      : advanceMode(state.mode, requestedModeValue);
    return copyState(next);
  }
  if (op === "rotate") {
    if (selected === null) fail("rotate requires the target generation");
    if (selected === state.active_generation && requestedModeValue !== null) {
      next.mode = advanceMode(state.mode, requestedModeValue);
      return copyState(next);
    }
    if (selected === state.active_generation) fail("rotate target must differ from active generation");
    const target = next.generations[String(selected)];
    if (target.state !== "revoked") fail("rotate target must be revoked");
    if (state.active_generation !== null)
      next.generations[String(state.active_generation)].state = "draining";
    target.state = "active";
    next.active_generation = selected;
    next.mode = requestedModeValue === null ? "canary_only" : advanceMode(state.mode, requestedModeValue);
    return copyState(next);
  }
  if (selected === null) fail(`${op} requires a generation`);
  const row = next.generations[String(selected)];
  if (op === "drain") {
    if (row.state !== "active") fail("drain requires an active generation");
    row.state = "draining";
    if (next.active_generation === selected) next.active_generation = null;
    return copyState(next);
  }
  if (op === "revoke") {
    if (row.state !== "draining") fail("revoke requires a draining generation");
    row.state = "revoked";
    if (next.active_generation === selected) next.active_generation = null;
    if (!Object.values(next.generations).some(item => item.state === "active")) next.mode = "disabled";
    return copyState(next);
  }
  return copyState(next);
}

function usage() {
  return "usage: provision-canonical-ownership-runtime.mjs --dry-run --operation <stage|rotate|drain|revoke|readback> [--generation 1|2] [--mode disabled|canary_only|attended_active] [--state-file PATH]";
}

async function main(argv) {
  let dryRun = false;
  let op = "readback";
  let selected = null;
  let requestedMode = null;
  let stateFile = null;
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") dryRun = true;
    else if (arg === "--operation") op = argv[++index];
    else if (arg === "--generation") selected = argv[++index];
    else if (arg === "--mode") requestedMode = argv[++index];
    else if (arg === "--state-file") stateFile = argv[++index];
    else if (arg === "--help") { console.log(usage()); return 0; }
    else fail(`unknown argument: ${arg}`);
  }
  if (!dryRun) fail("refusing live issuer provisioning; WR126 is dry-run only", 78);
  let state = emptyState();
  if (stateFile) state = JSON.parse(await readFile(stateFile, "utf8"));
  const result = transition(state, op, selected, requestedMode);
  console.log(JSON.stringify({
    ok: true,
    dry_run: true,
    operation: op,
    result: redactedReadback(result),
    effects: [],
    secret_bytes_emitted: false,
  }));
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv.slice(2)).catch(error => {
    console.error(`canonical-ownership-runtime: ${error.message}`);
    process.exitCode = error.code || 1;
  });
}
