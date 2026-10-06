import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, canExercisePartnerAuthority } from "../src/tools.js";
import { authorityDsnForActor, callTool } from "../src/mcp.js";
import { authenticatedIdentity, agentActorForToken } from "../src/identity.js";
// `actorFromProps` is module-private under amendment 8 (PR 1013). The exported
// grant door is `authenticatedIdentity.connectionForGrant`; called without the
// server's witness it returns exactly the same actor, unbranded, which is what
// every case in this file is about.
const actorFromProps = (props, bindings = null) =>
  authenticatedIdentity.connectionForGrant(props, bindings);


const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "test" };
const dell = { id: "10000000-0000-0000-0000-000000000003", slug: "dell", display: "Dell", human: true, via: "test" };
const NATIVE_BINDINGS = JSON.stringify({ "codex-client": "codex", "claude-client": "claude" });
const codexForJoe = { id: "10000000-0000-0000-0000-000000000004", slug: "codex", display: "Codex", human: false, sponsoring_human_slug: "joe", native_agent_verified: true, via: "oauth-agent", client_id: "codex-client" };
const claudeForDell = { id: "10000000-0000-0000-0000-000000000005", slug: "claude", display: "Claude", human: false, sponsoring_human_slug: "dell", native_agent_verified: true, via: "oauth-agent", client_id: "claude-client" };

const FALLBACK_UUID = "11111111-2222-4333-8444-555555555555";
const ACCEPTANCE_UUIDS = {
  "joe-shadow": "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
  "joe-canary": "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb",
  "dell-shadow": "cccccccc-3333-4333-8333-cccccccccccc",
};

class AcceptanceAuthorityFake {
  constructor(sessionSlug) {
    this.sessionSlug = sessionSlug;
    this.acceptanceCalls = [];
    this.disableCalls = [];
    this.envelopeWrites = 0;
    this.eventWrites = 0;
    this.receiptLookups = [];
    this.acceptanceIds = [];
  }

  async query(text, params = []) {
    if (text.includes("from tool_call where idempotency_key")) return { rows: [] };
    if (text.includes("ops.record_workflow_acceptance")) {
      this.acceptanceCalls.push(params);
      if (params[1] === "canary" && this.sessionSlug !== "joe")
        throw new Error("canary workflow acceptance requires Joe authority session");
      // A real acceptance returns a uuid; the fixture must too, or the event
      // assertion below is testing a shape production never produces.
      this.acceptanceIds.push(`${this.sessionSlug}-${params[1]}`);
      return { rows: [{ id: ACCEPTANCE_UUIDS[`${this.sessionSlug}-${params[1]}`] || FALLBACK_UUID }] };
    }
    if (text.includes("ops.disable_legacy_schedule")) {
      this.disableCalls.push(params);
      if (this.sessionSlug !== "joe") throw new Error("legacy schedule retirement requires Joe authority session");
      return { rows: [{ receipt_ref: `legacy-disable:${params[10]}` }] };
    }
    if (text.includes("insert into tool_call")) {
      this.envelopeWrites += 1;
      return { rows: [] };
    }
    if (text.includes("from ops.legacy_schedule_disable_receipt where receipt_ref")) {
      this.receiptLookups.push(params[0]);
      return { rows: [{ id: "8f14e45f-ceea-467a-9a5f-6a1b2c3d4e5f" }] };
    }
    if (text.includes("insert into event")) {
      this.eventWrites += 1;
      // THE SUBJECT OF AN EVENT IS A UUID. This fake used to accept anything,
      // which is why the suite stayed green for months while every real call
      // died on `invalid input syntax for type uuid` and rolled its receipt
      // back. Assert the shape the database actually enforces.
      const subject = params[4];
      if (subject != null && !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(String(subject)))
        throw new Error(`invalid input syntax for type uuid: "${subject}"`);
      return { rows: [] };
    }
    throw new Error(`unexpected SQL: ${text}`);
  }
}

test("control-plane authority operations are explicit human authority verbs", () => {
  // humanOnly LABEL RETIRED, authorityOnly UNCHANGED (WR-000019 slice S1,
  // 2026-08-27): the label was dead since executeRegisteredTool stopped
  // reading it 2026-08-26 (decision dc57f62d); this slice drops the stale
  // declarations from tools.js. authorityOnly is the live gate here.
  for (const name of ["accept-workflow", "disable-legacy-schedule", "activate-guidance-registry",
    "decide-guidance-import-batch", "deactivate-guidance-registry"]) {
    assert.equal(TOOLS[name].humanOnly, undefined);
    assert.equal(TOOLS[name].authorityOnly, true);
  }
});

test("authority DSNs follow verified partners and their sponsored Codex or Claude agents", () => {
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn" }, joe), "joe-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback" }, joe), "fallback");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_DELL_URL: "dell-dsn" }, dell), "dell-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "joe-fallback" }, dell), null);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn", CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, codexForJoe), "joe-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_DELL_URL: "dell-dsn", CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, claudeForDell), "dell-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback", CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, codexForJoe), "fallback");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback", CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, claudeForDell), null);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback" }, { slug: "codex", human: false }), null);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn" }, { slug: "grok", human: false, sponsoring_human_slug: "joe" }), null);
});

test("partners, sponsored native agents, and the local machine doors cross the partner boundary", () => {
  assert.equal(canExercisePartnerAuthority(joe), true);
  assert.equal(canExercisePartnerAuthority(dell), true);
  assert.equal(canExercisePartnerAuthority(codexForJoe), true);
  assert.equal(canExercisePartnerAuthority(claudeForDell), true);
  // ADDED 2026-08-26 (Joe's ruling, decision dc57f62d): the `./run.sh call`
  // doors. Their sponsor is server-derived through LOCAL_SPONSOR, never
  // asserted by the Mac, which is why admitting them is safe.
  const joeLocal = agentActorForToken("Bearer joe-secret", '{"joe-local":"joe-secret"}', "local-token");
  const dellLocal = agentActorForToken("Bearer dell-secret", '{"dell-local":"dell-secret"}', "local-token");
  assert.equal(canExercisePartnerAuthority(joeLocal), true);
  assert.equal(canExercisePartnerAuthority(dellLocal), true);
  // STILL REFUSED, and these are the ones that matter: a slug with no sponsor
  // at all, and a sponsored slug that is not an admitted door. The ruling
  // removed a human-only gate; it did not make the boundary meaningless.
  assert.equal(canExercisePartnerAuthority({ ...codexForJoe, native_agent_verified: false }), false);
  assert.equal(canExercisePartnerAuthority({ slug: "codex", human: false }), false);
  assert.equal(canExercisePartnerAuthority({ slug: "grok", human: false, sponsoring_human_slug: "joe" }), false);
  assert.equal(canExercisePartnerAuthority({ slug: "joe-local", human: false }), false);
});

test("native OAuth authority is recomputed from current client bindings on every request", () => {
  const storedGrant = { slug: "codex", human: false, human_slug: "joe", via: "oauth-agent",
    client_id: "codex-client", native_agent_verified: true };
  const current = actorFromProps(storedGrant, NATIVE_BINDINGS);
  assert.equal(current.native_agent_verified, true);
  assert.equal(canExercisePartnerAuthority(current), true);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn",
    CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, current), "joe-dsn");
  const removed = actorFromProps(storedGrant, "{}");
  assert.equal(removed.native_agent_verified, undefined);
  assert.equal(canExercisePartnerAuthority(removed), false);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn",
    CARR_NATIVE_AGENT_OAUTH_CLIENTS: "{}" }, storedGrant), null);
});

test("authority operation fails closed instead of falling back to writer credentials", async () => {
  for (const [name, args] of [
    ["accept-workflow", { idempotency_key: "authority-missing", workflow_key: "fixture", mode: "shadow", receipt_ref: "r" }],
    ["activate-guidance-registry", { idempotency_key: "authority-missing", registry_id: "10000000-0000-0000-0000-000000000001", manifest_digest: "a".repeat(64), reason: "fixture" }],
    ["decide-guidance-import-batch", { idempotency_key: "authority-missing", batch_id: "10000000-0000-0000-0000-000000000001", manifest_digest: "a".repeat(64), reason: "fixture" }],
    ["deactivate-guidance-registry", { idempotency_key: "authority-missing", registry_id: "10000000-0000-0000-0000-000000000001", manifest_digest: "a".repeat(64), reason: "fixture" }],
  ]) await assert.rejects(() => callTool({ DATABASE_URL_WRITER: "writer-only" }, joe, name, args),
    e => e instanceof ToolError && e.payload.error === "authority_connection_unavailable");
});

test("workflow acceptance leaves canary Joe-only in DB while shadow remains partner-capable", async () => {
  const tool = TOOLS["accept-workflow"];
  assert.match(tool.description, /Shadow acceptance remains available to either admitted human partner/);
  assert.match(tool.description, /canary acceptance is Joe-only.*authenticated authority database session/i);

  const dellShadow = new AcceptanceAuthorityFake("dell");
  const shadow = await tool.handler(dellShadow, dell, {
    idempotency_key: "accept-shadow-dell", workflow_key: "fixture",
    mode: "shadow", receipt_ref: "receipt:shadow",
  });
  assert.equal(shadow.ok, true);
  assert.deepEqual(dellShadow.acceptanceCalls, [["fixture", "shadow", "receipt:shadow"]]);
  assert.equal(dellShadow.envelopeWrites, 1);
  assert.equal(dellShadow.eventWrites, 1);

  const joeCanary = new AcceptanceAuthorityFake("joe");
  const canary = await tool.handler(joeCanary, joe, {
    idempotency_key: "accept-canary-joe", workflow_key: "fixture",
    mode: "canary", receipt_ref: "receipt:canary",
  });
  assert.equal(canary.ok, true);
  assert.deepEqual(joeCanary.acceptanceCalls, [["fixture", "canary", "receipt:canary"]]);

  const dellCanary = new AcceptanceAuthorityFake("dell");
  await assert.rejects(() => tool.handler(dellCanary, dell, {
    idempotency_key: "accept-canary-dell", workflow_key: "fixture",
    mode: "canary", receipt_ref: "receipt:canary",
  }), /canary workflow acceptance requires Joe authority session/);
  assert.deepEqual(dellCanary.acceptanceCalls, [["fixture", "canary", "receipt:canary"]]);
  assert.equal(dellCanary.envelopeWrites, 0);
  assert.equal(dellCanary.eventWrites, 0);
});

test("legacy disable emits a distinct Joe-bound surface receipt", async () => {
  const tool = TOOLS["disable-legacy-schedule"];
  assert.deepEqual(tool.inputSchema.required, ["idempotency_key", "workflow_key", "surface_id", "locator", "reason", "pre_observation_ref", "post_observation_ref"]);
  const c = new AcceptanceAuthorityFake("joe");
  const result = await tool.handler(c, joe, {
    idempotency_key: "disable-fixture", workflow_key: "cc-update-audit",
    surface_id: "cc-update-audit.claude-code.v1", locator: "cc-update-audit", reason: "accepted evidence",
    pre_observation_ref: "native:enabled", post_observation_ref: "native:disabled",
  });
  assert.equal(result.receipt_ref, "legacy-disable:disable-fixture");
  // The audit event must name the receipt's uuid, never the workflow key.
  assert.deepEqual(c.receiptLookups, ["legacy-disable:disable-fixture"]);
  assert.equal(c.eventWrites, 1);
  assert.deepEqual(c.disableCalls, [["cc-update-audit", "cc-update-audit.claude-code.v1",
    "cc-update-audit", "accepted evidence", "native:enabled", "native:disabled",
    null, null, null, null, "disable-fixture"]]);
  const dellC = new AcceptanceAuthorityFake("dell");
  await assert.rejects(() => tool.handler(dellC, dell, {
    idempotency_key: "disable-dell", workflow_key: "cc-update-audit",
    surface_id: "cc-update-audit.claude-code.v1", locator: "cc-update-audit", reason: "no",
    pre_observation_ref: "native:enabled", post_observation_ref: "native:disabled",
  }), /requires Joe authority session/);
});

test("Notes duplicate disable binds both native scheduler pairs", async () => {
  const tool = TOOLS["disable-legacy-schedule"];
  const c = new AcceptanceAuthorityFake("joe");
  const result = await tool.handler(c, joe, {
    idempotency_key: "disable-notes-group", workflow_key: "notes-sweep-hourly",
    surface_id: "notes-sweep-hourly.claude-code.v1", locator: "notes-sweep-hourly",
    reason: "both native schedules disabled",
    pre_observation_ref: "claude:enabled", post_observation_ref: "claude:disabled",
    sibling_surface_id: "notes-sweep-hourly.launchd.v1", sibling_locator: "com.carr.notes-sweep",
    sibling_pre_observation_ref: "launchd:enabled", sibling_post_observation_ref: "launchd:disabled",
  });
  assert.equal(result.receipt_ref, "legacy-disable:disable-notes-group");
  assert.deepEqual(c.disableCalls[0], ["notes-sweep-hourly", "notes-sweep-hourly.claude-code.v1",
    "notes-sweep-hourly", "both native schedules disabled", "claude:enabled", "claude:disabled",
    "notes-sweep-hourly.launchd.v1", "com.carr.notes-sweep", "launchd:enabled", "launchd:disabled",
    "disable-notes-group"]);
  await assert.rejects(() => tool.handler(c, joe, {
    idempotency_key: "disable-notes-incomplete", workflow_key: "notes-sweep-hourly",
    surface_id: "notes-sweep-hourly.claude-code.v1", locator: "notes-sweep-hourly", reason: "incomplete",
    pre_observation_ref: "claude:enabled", post_observation_ref: "claude:disabled",
    sibling_surface_id: "notes-sweep-hourly.launchd.v1",
  }), error => error instanceof ToolError && error.payload.error === "duplicate_scheduler_evidence_incomplete");
});
