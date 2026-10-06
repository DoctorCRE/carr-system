import test from "node:test";
import assert from "node:assert/strict";

import { incidentTools, INCIDENT_OCCURRENCE_JOIN_SQL } from "../src/incident.js";
import { ToolError } from "../src/tools.js";
import { workRequestIntakeTools } from "../src/work-request-intake.js";

const ACTOR = { id: "10000000-0000-0000-0000-000000000009", slug: "codex",
  human: false, sponsoring_human_slug: "joe", organization_tenant_id: "carr-internal" };
const KEY = "20000000-0000-0000-0000-000000000001";
const ARGS = { idempotency_key: KEY, incident_ref: "INC-20260908-01", work_request: "WR-000069" };

function tools({ envelope, event = async () => {} } = {}) {
  return incidentTools({
    withEnvelope: envelope || (async (_c, _actor, _name, _args, body) => body()),
    writeEvent: event,
    ToolError,
    authorizationClassForActor: () => "agent",
  });
}

class LinkClient {
  constructor({ targetState = "ready", existing = [], insert = true } = {}) {
    this.targetState = targetState;
    this.existing = existing;
    this.insert = insert;
    this.calls = [];
  }
  async query(text, params = []) {
    const sql = String(text).replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.includes("from ops.incident where ref"))
      return { rows: [{ id: "30000000-0000-0000-0000-000000000001", ref: ARGS.incident_ref }] };
    if (sql.includes("ops.work_request_card"))
      return { rows: this.targetState ? [{ ref: ARGS.work_request, state: this.targetState }] : [] };
    if (sql.includes("from ops.incident_link") && sql.includes("ref<>"))
      return { rows: this.existing.map((ref) => ({ ref })) };
    if (sql.startsWith("insert into ops.incident_link"))
      return { rows: this.insert ? [{ incident_id: "30000000-0000-0000-0000-000000000001" }] : [] };
    throw new Error(`unexpected SQL: ${sql}`);
  }
}

async function refused(fn) {
  try { await fn(); }
  catch (error) {
    assert.ok(error instanceof ToolError, `expected ToolError, received ${error}`);
    return error.payload;
  }
  assert.fail("expected refusal");
}

test("link-incident-work-request is a closed ordinary-session write", () => {
  const verb = tools()["link-incident-work-request"];
  assert.equal(verb.write, true);
  assert.equal(verb.humanOnly, undefined);
  assert.equal(verb.authorityOnly, undefined);
  assert.equal(verb.inputSchema.additionalProperties, false);
  assert.deepEqual(verb.inputSchema.required,
    ["idempotency_key", "incident_ref", "work_request"]);
  assert.deepEqual(Object.keys(verb.inputSchema.properties),
    ["idempotency_key", "incident_ref", "work_request"]);
});

test("the same-key lock is acquired before the existing envelope can read replay state", async () => {
  const db = new LinkClient();
  const order = [];
  const original = db.query.bind(db);
  db.query = async (sql, params) => {
    if (String(sql).startsWith("select pg_advisory_xact_lock"))
      order.push(params[0] === KEY ? "idempotency-lock" : "semantic-lock");
    return original(sql, params);
  };
  const verb = tools({
    envelope: async (_c, _actor, name, args, body) => {
      order.push("envelope-read");
      assert.equal(name, "link-incident-work-request");
      assert.deepEqual(args, ARGS);
      return body();
    },
    event: async () => order.push("event"),
  })["link-incident-work-request"];
  const result = await verb.handler(db, ACTOR, ARGS);
  assert.deepEqual(order.slice(0, 3), ["idempotency-lock", "envelope-read", "semantic-lock"]);
  assert.equal(result.linked, true);
  assert.equal(result.already_linked, false);
  assert.equal(result.link_kind, "work_request");
  assert.equal(result.occurrence_effect, "none");
  assert.equal(result.lifecycle_effect, "none");
  assert.equal(order.filter((step) => step === "event").length, 1);
});

test("UUID case variants normalize to the same pre-envelope lock and stored key", async () => {
  const db = new LinkClient();
  let envelopeArgs;
  await tools({ envelope: async (_c, _actor, _name, args, body) => {
    envelopeArgs = args;
    return body();
  } })["link-incident-work-request"].handler(db, ACTOR,
    { ...ARGS, idempotency_key: KEY.toUpperCase() });
  assert.equal(db.calls[0].params[0], KEY);
  assert.equal(envelopeArgs.idempotency_key, KEY);
});

test("semantic replay adds neither a second edge nor a second event", async () => {
  const db = new LinkClient({ insert: false });
  let events = 0;
  const result = await tools({ event: async () => { events += 1; } })
    ["link-incident-work-request"].handler(db, ACTOR, ARGS);
  assert.equal(result.linked, false);
  assert.equal(result.already_linked, true);
  assert.equal(result.link_kind, "work_request");
  assert.equal(events, 0);
});

test("a conflicting contextual work-request edge refuses without a write", async () => {
  const db = new LinkClient({ existing: ["WR-000068"] });
  const payload = await refused(() => tools()["link-incident-work-request"].handler(db, ACTOR, ARGS));
  assert.equal(payload.error, "incident_work_request_conflict");
  assert.equal(payload.existing_work_request, "WR-000068");
  assert.ok(!db.calls.some(({ sql }) => sql.startsWith("insert into ops.incident_link")));
});

test("all five public work-request states are accepted; hidden and invalid states refuse", async () => {
  for (const state of ["captured", "triaged", "ready", "declined", "superseded"])
    assert.equal((await tools()["link-incident-work-request"].handler(
      new LinkClient({ targetState: state }), ACTOR, { ...ARGS, idempotency_key: KEY })).ok, true, state);
  for (const state of [null, "executing", "deleted"]) {
    const payload = await refused(() => tools()["link-incident-work-request"].handler(
      new LinkClient({ targetState: state }), ACTOR, ARGS));
    assert.equal(payload.error, "work_request_not_found", String(state));
  }
});

test("the occurrence aggregate counts only run/deployment links and marks unresolved legacy targets", () => {
  assert.match(INCIDENT_OCCURRENCE_JOIN_SQL, /kind in \('run','deployment'\)/);
  assert.match(INCIDENT_OCCURRENCE_JOIN_SQL, /greatest\(1, link_count \+ unpaired_correlation_count\)/);
  assert.match(INCIDENT_OCCURRENCE_JOIN_SQL, /greatest\(1, link_count, correlation_count\)/);
  assert.match(INCIDENT_OCCURRENCE_JOIN_SQL, /legacy_overlap_unknown/);
  assert.match(INCIDENT_OCCURRENCE_JOIN_SQL, /unresolved_occurrence_edge_count/);
  assert.doesNotMatch(INCIDENT_OCCURRENCE_JOIN_SQL, /legacy_overlap_unknown_count/);
  assert.doesNotMatch(INCIDENT_OCCURRENCE_JOIN_SQL, /kind in \('run','deployment','work_request'/);
});

test("work-request-card carries incident evidence while retaining no executable actions", async () => {
  const evidence = [{ incident_ref: ARGS.incident_ref, occurrences: 1,
    occurrence_evidence_status: "complete", unresolved_occurrence_edge_count: 0,
    association: { kind: "work_request", ref: ARGS.work_request },
    evidence: [{ evidence_type: "fact", kind: "fact", text: "failed at 85.1 percent",
      source_ref: `correlation:${KEY}`, recorded_at: "2026-09-08T12:00:00Z" }] }];
  const client = { query: async (sql) => String(sql).includes("ops.work_request_card")
    ? { rows: [{ ref: ARGS.work_request, title: "WR69", desired_outcome: "Link evidence",
      acceptance_criteria: [], state: "captured", version: 1, incident_evidence: evidence }] }
    : { rows: [] } };
  const card = workRequestIntakeTools({ withEnvelope: async () => assert.fail("read used envelope"),
    writeEvent: async () => assert.fail("read wrote event"), ToolError })["work-request-card"];
  const result = await card.handler(client, ACTOR, { work_request: ARGS.work_request });
  assert.deepEqual(result.incident_evidence, evidence);
  assert.deepEqual(result.actions, []);
});
