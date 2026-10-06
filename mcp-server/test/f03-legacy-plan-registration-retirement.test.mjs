// V5-F03: new engineering-slice-plan.v1 registrations are retired at the
// registration doors only (migration 0616 and the register-engineering-slice-plan
// handler).  Stored v1 plans keep the read path requirePlan has always given them.
import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

import {
  ENGINEERING_SLICE_PLAN_REGISTRABLE_VERSIONS,
  ENGINEERING_SLICE_PLAN_VERSIONS,
  canonicalDigest,
  requirePlan,
  requireRegistrablePlanVersion,
} from "../src/engineering-runtime.js";
import { TOOLS } from "../src/tools.js";

class EngineeringToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const corpus = JSON.parse(fs.readFileSync(new URL("./fixtures/f03-design-contract-parity.v1.json", import.meta.url), "utf8"));
const MIGRATION = fs.readFileSync(new URL("../../migrations/0616_f03_retire_legacy_slice_plan_registration.sql", import.meta.url), "utf8");
const RUNTIME = fs.readFileSync(new URL("../src/engineering-runtime.js", import.meta.url), "utf8");

function sealed(base) {
  const plan = structuredClone(corpus.bases[base]);
  delete plan.plan_digest;
  return { ...plan, plan_digest: canonicalDigest(plan) };
}

// THE REAL DOOR.  These cases drive TOOLS["register-engineering-slice-plan"],
// so the real tools.js withEnvelope runs: advisory lock, tool_call replay read,
// handler, tool_call insert.  An earlier version of this file stubbed
// withEnvelope, which hid that the version gate ran AFTER the replay read.
//
// RecordingClient records every statement and plays a minimal database: the
// tool_call ledger lives in `ledger`, keyed by idempotency key, exactly as the
// envelope writes and reads it.  `seedMatchingReplay` models a row stored before
// the 0616 cutoff: when the envelope reads that key it gets back a row whose
// request_hash is the hash the envelope itself just computed for this request,
// captured from crypto.subtle.digest, which is what a pre-cutoff registration of
// the identical request would have stored.
class RecordingClient {
  constructor() { this.statements = []; this.ledger = new Map(); this.seeded = new Map(); this.registered = 0; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.statements.push(sql);
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [{}] };
    if (sql.startsWith("select request_hash, response")) {
      const key = params[0];
      if (this.ledger.has(key)) return { rows: [this.ledger.get(key)] };
      if (this.seeded.has(key)) return { rows: [{ request_hash: lastRequestHash, response: this.seeded.get(key) }] };
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      // Keep the identity the envelope writes, in its insert's parameter order,
      // so a replay is judged against a row that says who made it.
      this.ledger.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] });
      return { rows: [] };
    }
    if (sql.startsWith("select * from ops.engineering_register_slice_plan(")) {
      this.registered += 1;
      return { rows: [{ id: "00000000-0000-4000-8000-000000000001", work_request_id: "00000000-0000-4000-8000-0000000000aa",
        accepted_plan_id: "00000000-0000-4000-8000-0000000000bb", plan_digest: params[2] }] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    return { rows: [] };
  }
  seedMatchingReplay(key, response) { this.seeded.set(key, response); }
}

let lastRequestHash = null;
const subtle = globalThis.crypto.subtle;
const realDigest = subtle.digest.bind(subtle);
subtle.digest = async (algorithm, data) => {
  const out = await realDigest(algorithm, data);
  lastRequestHash = [...new Uint8Array(out)].map(byte => byte.toString(16).padStart(2, "0")).join("");
  return out;
};

const actor = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, kind: "human",
  via: "mcp", client_id: "claude" };
const register = (client, callArgs) => TOOLS["register-engineering-slice-plan"].handler(client, actor, callArgs);

const args = (plan, idempotency_key = globalThis.crypto.randomUUID()) => ({
  idempotency_key,
  work_request: "WR-000001",
  plan,
  plan_digest: plan.plan_digest,
});

async function refusalOf(promise) {
  try { await promise; } catch (error) { return error.payload ?? { error: error.message }; }
  return null;
}

test("a fresh v1 registration refuses before any statement, through the real envelope", async () => {
  const client = new RecordingClient();
  const v1 = sealed("v1-legacy");
  assert.equal(requirePlan(structuredClone(v1), EngineeringToolError).schema_version, "engineering-slice-plan.v1");
  const refusal = await refusalOf(register(client, args(v1)));
  assert.equal(refusal?.error, "engineering_slice_plan_version_not_registrable");
  assert.equal(refusal.schema_version, "engineering-slice-plan.v1");
  assert.deepEqual(refusal.registrable, ["engineering-slice-plan.v2"]);
  assert.deepEqual(client.statements, [], "no statement may run for a refused registration");
});

test("a v1 key stored before the cutoff does not replay: it refuses before any statement", async () => {
  const client = new RecordingClient();
  const call = args(sealed("v1-legacy"));
  client.seedMatchingReplay(call.idempotency_key, { ok: true, engineering_slice_plan_id: "pre-cutoff", plan_digest: call.plan_digest });
  const outcome = await register(client, call).then(value => ({ value }), error => ({ refusal: error.payload }));
  assert.equal(outcome.value, undefined, `a pre-cutoff v1 key must not replay: ${JSON.stringify(outcome.value)}`);
  assert.equal(outcome.refusal?.error, "engineering_slice_plan_version_not_registrable");
  assert.deepEqual(client.statements, []);
});

test("a malformed plan refuses before any statement even when its key has a stored response", async () => {
  const client = new RecordingClient();
  const bad = sealed("v2-short");
  delete bad.slices[0].design_contract;
  const call = args(bad);
  client.seedMatchingReplay(call.idempotency_key, { ok: true, engineering_slice_plan_id: "stored" });
  const refusal = await refusalOf(register(client, call));
  assert.equal(refusal?.error, "engineering_slice_schema_invalid");
  assert.deepEqual(client.statements, []);
});

test("a fresh valid v2 plan registers, then the same key replays without a second registration", async () => {
  const client = new RecordingClient();
  const call = args(sealed("v2-short"));
  const first = await register(client, call);
  assert.equal(first.ok, true);
  assert.equal(first.replayed, undefined);
  assert.equal(client.registered, 1);
  assert.equal(client.ledger.has(call.idempotency_key), true, "the envelope stored the response");
  assert.ok(client.statements.some(sql => sql.startsWith("select * from ops.engineering_register_slice_plan(")));

  const statementsBefore = client.statements.length;
  const second = await register(client, structuredClone(call));
  assert.deepEqual(second, { replayed: true, ...first });
  assert.equal(client.registered, 1, "a replay must not register again");
  const replayStatements = client.statements.slice(statementsBefore);
  assert.equal(replayStatements.length, 2, JSON.stringify(replayStatements));
  assert.ok(replayStatements[0].startsWith("select pg_advisory_xact_lock"));
  assert.ok(replayStatements[1].startsWith("select request_hash, response"));
});

test("a stored v2 key re-sent with different content is key reuse, not a replay", async () => {
  const client = new RecordingClient();
  const call = args(sealed("v2-short"));
  await register(client, call);
  const changed = { ...call, plan: sealed("v2-full") };
  changed.plan_digest = changed.plan.plan_digest;
  const refusal = await refusalOf(register(client, changed));
  assert.equal(refusal?.error, "key_reuse");
  assert.equal(client.registered, 1);
});

test("stored v1 plans keep their read path: requirePlan still accepts v1", () => {
  assert.deepEqual([...ENGINEERING_SLICE_PLAN_VERSIONS], ["engineering-slice-plan.v1", "engineering-slice-plan.v2"]);
  assert.deepEqual([...ENGINEERING_SLICE_PLAN_REGISTRABLE_VERSIONS], ["engineering-slice-plan.v2"]);
  for (const base of ["v1-legacy", "v2-short", "v2-full", "v2-pair"])
    assert.equal(requirePlan(sealed(base), EngineeringToolError).plan_digest, sealed(base).plan_digest, base);
});

test("the registration gate is called only from the register handler, never from a read path", () => {
  const calls = RUNTIME.match(/requireRegistrablePlanVersion\(/g) || [];
  // one declaration plus exactly one call site
  assert.equal(calls.length, 2);
  const register = RUNTIME.slice(RUNTIME.indexOf('"register-engineering-slice-plan": {'), RUNTIME.indexOf('"admit-engineering-slice": {'));
  assert.match(register, /requireRegistrablePlanVersion\(requirePlan\(args\.plan, ToolError\), ToolError\)/);
  // The gate must run before withEnvelope, whose replay read would otherwise
  // hand back a stored pre-cutoff v1 response.
  assert.ok(register.indexOf("requireRegistrablePlanVersion(") < register.indexOf("withEnvelope("),
    "the version gate must run before the envelope's replay read");
  const requirePlanBody = RUNTIME.slice(RUNTIME.indexOf("export function requirePlan("), RUNTIME.indexOf("function sourceParts("));
  assert.doesNotMatch(requirePlanBody, /requireRegistrablePlanVersion|REGISTRABLE/);
});

test("unknown and missing versions are not registrable either", () => {
  for (const schema_version of [undefined, null, "engineering-slice-plan.v3", "engineering-slice-plan.V2", ""])
    assert.throws(() => requireRegistrablePlanVersion({ schema_version }, EngineeringToolError),
      error => error.payload.error === "engineering_slice_plan_version_not_registrable");
  assert.equal(requireRegistrablePlanVersion({ schema_version: "engineering-slice-plan.v2" }, EngineeringToolError).schema_version,
    "engineering-slice-plan.v2");
});

test("0616 re-issues only the register seam, refusing non-v2 before the replay and the insert", () => {
  const creates = MIGRATION.match(/create or replace function ops\.[a-z_]+/gi) || [];
  assert.deepEqual(creates.map(row => row.toLowerCase()), ["create or replace function ops.engineering_register_slice_plan"]);
  assert.doesNotMatch(MIGRATION, /engineering_slice_plan_refusal\s*\(p_plan jsonb\)\s*returns/i);
  assert.doesNotMatch(MIGRATION, /^\s*(grant|revoke)\b/im);
  const gate = MIGRATION.indexOf("if p_plan->>'schema_version' is distinct from 'engineering-slice-plan.v2' then");
  const planCheck = MIGRATION.indexOf("plan_refusal := ops.engineering_slice_plan_refusal(p_plan);");
  const insert = MIGRATION.indexOf("insert into ops.engineering_slice_plan");
  assert.ok(planCheck > 0 && gate > planCheck && insert > gate, "order: whole-plan check, version gate, insert/replay");
});

test("0616 carries the 0507a register body verbatim except the one marked block", () => {
  const body = sql => {
    const start = sql.indexOf("create or replace function ops.engineering_register_slice_plan(");
    return sql.slice(start, sql.indexOf("end $$;", start));
  };
  const prior = body(fs.readFileSync(new URL("../../migrations/0507a_engineering_slice_plan_validators.sql", import.meta.url), "utf8"));
  const current = body(MIGRATION);
  const blockStart = current.indexOf("  -- V5-F03 v1 retirement (0616)");
  const blockEnd = current.indexOf("  end if;\n", current.indexOf("is not registrable", blockStart)) + "  end if;\n".length;
  assert.ok(blockStart > 0 && blockEnd > blockStart);
  assert.equal(current.slice(0, blockStart) + current.slice(blockEnd), prior);
});
