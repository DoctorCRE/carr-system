#!/usr/bin/env node
// Lease-bound Engineering Passport controller.  This is a local adapter, not
// an MCP verb: it owns the fixed carr_jobs connection, calls only the scoped
// engineering claim function, and gives the Codex child no database capability.

import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { Pool } from "@neondatabase/serverless";
import {
  runEngineeringWorker,
} from "../src/engineering-runtime.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const ADAPTER = path.join(REPO, "tools", "room-bridge", "engineering_dispatch_adapter.py");
const PYTHON = path.join(REPO, ".venv", "bin", "python");
// This is an execution identity, never a controller/operator option.  The
// adapter resolves and validates this exact desk before the envelope is sent.
const DESK = "engineering-codex";
const WORKER = "room-bridge-engineering-controller";
const CONTROLLER_WORKER = WORKER;
const WORKER_URL_ENV = "CARR_ENGINEERING_WORKER_URL";
const CONTROLLER_TOKEN_ENV = "CARR_ENGINEERING_CONTROLLER_TOKEN";
const CONTROLLER_TOOLS = Object.freeze([
  "canonical-ownership-acquire", "canonical-ownership-check",
  "canonical-ownership-renew", "canonical-ownership-release",
]);

class ControllerError extends Error {
  constructor(payload) { super(JSON.stringify(payload)); this.payload = payload; }
}

function jobsDsn() {
  const value = process.env.CARR_DB_JOBS_URL;
  if (typeof value !== "string" || !value) throw new ControllerError({ error: "engineering_jobs_dsn_missing" });
  let parsed;
  try { parsed = new URL(value); } catch { throw new ControllerError({ error: "engineering_jobs_dsn_invalid" }); }
  if (!/^postgres(?:ql)?:$/.test(parsed.protocol) || decodeURIComponent(parsed.username) !== "carr_jobs")
    throw new ControllerError({ error: "engineering_jobs_dsn_not_carr_jobs" });
  return value;
}

function safeAdapterEnv() {
  // Deliberately constructed rather than redacted: a future credential name
  // cannot accidentally cross from the controller to a model process.
  const permitted = ["HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TERM"];
  return Object.fromEntries(permitted.filter(key => process.env[key]).map(key => [key, process.env[key]]));
}

function workerEndpoint() {
  const raw = process.env[WORKER_URL_ENV];
  if (typeof raw !== "string" || !raw) throw new ControllerError({ error: "engineering_worker_url_missing" });
  let url;
  try { url = new URL(raw); } catch { throw new ControllerError({ error: "engineering_worker_url_invalid" }); }
  const loopbackHttp = url.protocol === "http:" &&
    ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (!(url.protocol === "https:" || loopbackHttp) || url.username || url.password || url.hash)
    throw new ControllerError({ error: "engineering_worker_url_invalid" });
  return url;
}

function controllerToken() {
  const token = process.env[CONTROLLER_TOKEN_ENV];
  if (typeof token !== "string" || !token) throw new ControllerError({ error: "engineering_controller_token_missing" });
  // A token is an isolated Worker bearer. Never include it in the request body,
  // logs, error payloads, or the child environment.
  return token;
}

async function controllerRpc(method, params = undefined) {
  const url = workerEndpoint();
  const token = controllerToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30_000);
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "authorization": `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: randomUUID(), method,
        ...(params === undefined ? {} : { params }) }),
      signal: controller.signal,
    });
  } catch (error) {
    throw new ControllerError({ error: error?.name === "AbortError"
      ? "engineering_worker_timeout" : "engineering_worker_unavailable" });
  } finally {
    clearTimeout(timer);
  }
  let body;
  try { body = await response.json(); } catch {
    throw new ControllerError({ error: "engineering_worker_response_invalid", status: response.status });
  }
  if (!response.ok || body?.error || body?.result?.isError) {
    // The response body is untrusted and must never become an error channel for
    // a bearer or a database/ownership token echoed by a faulty endpoint.
    throw new ControllerError({ error: "engineering_controller_refused", status: response.status });
  }
  return body.result;
}

async function preflightAuthenticatedController() {
  const result = await controllerRpc("tools/list");
  const names = (result?.tools || []).map(tool => tool?.name).sort();
  if (names.length !== CONTROLLER_TOOLS.length ||
      names.some((name, index) => name !== [...CONTROLLER_TOOLS].sort()[index]))
    throw new ControllerError({ error: "engineering_controller_surface_invalid" });
}

function controllerOperationResult(result) {
  if (result?.isError) throw new ControllerError({ error: "engineering_controller_operation_refused" });
  const text = result?.content?.find(item => item?.type === "text")?.text;
  if (typeof text !== "string") throw new ControllerError({ error: "engineering_controller_result_invalid" });
  try {
    const parsed = JSON.parse(text);
    if (!parsed?.ok || typeof parsed.lease_ref !== "string" || typeof parsed.operation_ref !== "string")
      throw new Error("controller operation was not successful");
    return parsed;
  } catch {
    throw new ControllerError({ error: "engineering_controller_result_invalid" });
  }
}

async function ownershipOperation(name, claim, leaseRef = undefined, operationRef = undefined) {
  const args = { job_id: claim.job_id, lease_token: claim.lease_token };
  if (name !== "canonical-ownership-acquire") {
    args.lease_ref = leaseRef;
    args.operation_ref = operationRef;
  }
  return controllerOperationResult(await controllerRpc("tools/call", {
    name, arguments: args,
  }));
}

function runAdapter(input) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [ADAPTER], { env: safeAdapterEnv(), stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.on("error", () => reject(new ControllerError({ error: "engineering_adapter_unavailable" })));
    child.on("close", code => {
      if (code !== 0) return reject(new ControllerError({ error: "engineering_adapter_refused", exit_code: code }));
      try {
        const parsed = JSON.parse(stdout);
        if (!parsed?.ok || !parsed.receipt || typeof parsed.receipt !== "object") throw new Error("invalid adapter response");
        resolve(parsed.receipt);
      } catch {
        reject(new ControllerError({ error: "engineering_adapter_response_invalid" }));
      }
    });
    child.stdin.end(JSON.stringify(input));
  });
}

async function withCanonicalOwnership(claim, execute) {
  let lease;
  try {
    lease = await ownershipOperation("canonical-ownership-acquire", claim);
    const receipt = await execute();
    // Renewal is deliberately not automatic.  An attended controller may use
    // the closed renew operation explicitly, but an execution that outlives
    // its accepted 900-second lease fails closed instead of manufacturing
    // unattended authority.
    lease = await ownershipOperation("canonical-ownership-check", claim,
      lease.lease_ref, lease.operation_ref);
    await ownershipOperation("canonical-ownership-release", claim,
      lease.lease_ref, lease.operation_ref);
    return receipt;
  } catch (error) {
    if (lease?.lease_ref) {
      try {
        await ownershipOperation("canonical-ownership-release", claim,
          lease.lease_ref, lease.operation_ref);
      } catch { /* preserve primary failure */ }
    }
    throw error;
  }
}

function preflightDedicatedDesk() {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [ADAPTER, "--preflight"], {
      env: safeAdapterEnv(), stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.on("error", () => reject(new ControllerError({ error: "engineering_desk_preflight_unavailable" })));
    child.on("close", code => {
      if (code !== 0) return reject(new ControllerError({ error: "engineering_desk_preflight_refused" }));
      try {
        const row = JSON.parse(stdout);
        if (row?.ok !== true || row?.desk?.name !== DESK || row?.desk?.kind !== "codex-session") throw new Error("bad preflight");
        resolve();
      } catch {
        reject(new ControllerError({ error: "engineering_desk_preflight_invalid" }));
      }
    });
  });
}

async function main() {
  if (process.argv.includes("--dry-run")) {
    console.log(JSON.stringify({ ok: true, dry_run: true, worker: WORKER,
      controller_worker: CONTROLLER_WORKER, desk: DESK,
      jobs_credential: "CARR_DB_JOBS_URL", controller_credential: CONTROLLER_TOKEN_ENV,
      issuer_credential: null, child_credentials: [] }));
    return;
  }
  // Prove the exact unseated, model-pinned desk before opening the jobs pool.
  // A missing or altered desk therefore cannot claim a live lease.
  await preflightDedicatedDesk();
  // Authenticate the isolated Worker controller before claiming a live job.
  // This is a closed tools/list proof; issuer slots are never loaded locally.
  await preflightAuthenticatedController();
  const pool = new Pool({ connectionString: jobsDsn() });
  try {
    const client = await pool.connect();
    try {
      const result = await runEngineeringWorker({
        c: client, worker: WORKER, desk: DESK, ToolError: ControllerError,
        withCanonicalOwnership,
        dispatchEnvelope: async (_desk, envelope, task) => runAdapter({
          desk: DESK, envelope, task, executor_slug: "codex",
        }),
      });
      // This is the operator readback: references and states only, never model
      // text, task content, or a credential.
      console.log(JSON.stringify({ ok: true, worker: WORKER, claimed: result.claimed,
        completed: result.completed, results: result.results }, null, 0));
    } finally {
      client.release();
    }
  } finally {
    await pool.end();
  }
}

main().catch(error => {
  const payload = error instanceof ControllerError ? error.payload : { error: "engineering_controller_failed" };
  console.error(JSON.stringify(payload));
  process.exitCode = 1;
});
