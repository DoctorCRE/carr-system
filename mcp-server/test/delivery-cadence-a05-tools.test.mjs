// V5-A05 -- production door acceptance proofs (migration 0617, sealed as SCAC v75 by 0618).
//
// EVERY CASE RUNS THROUGH THE REAL VERB HANDLERS, not through SQL directly:
// the point is to prove the wired path (verb -> SQL function -> signal_event
// -> mint_notification), matching notifications.test.mjs's own rule that a
// proof calling ops.mint_notification directly proves the function rather
// than the product.
//
// The DB cases skip in the unit class; the migration class supplies
// DATABASE_URL, matching R03-SIGNAL-SURVIVES-MINT-FAILURE's own convention.
// The unit cases at the top (route decision, seats, schema) always run.

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { a05SeatForActor, deliveryCadenceA05Tools } from "../src/delivery-cadence-a05-tools.js";
import { TOOLS } from "../src/tools.js";
import { connectionRouteForTool } from "../src/mcp.js";

const DSN = process.env.DATABASE_URL || "";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

function harness() {
  const envelopes = new Map();
  const events = [];
  return {
    events,
    withEnvelope: async (c, actor, verb, args, fn) => {
      const key = `${verb}:${args.idempotency_key}`;
      if (envelopes.has(key)) return envelopes.get(key);
      const value = await fn();
      envelopes.set(key, value);
      return value;
    },
    writeEvent: async (c, actor, verb, subjectType, subjectId, fields) => {
      events.push({ verb, subjectType, subjectId, fields });
    },
  };
}

const PARTNER_ACTOR = id => ({ id, slug: "joe", human: true, via: "oauth-google" });
const ACTOR = (id, slug) => ({ id, slug, human: true, via: "oauth-google" });
// The machine credential `./run.sh call` presents (identity.js agentActorForToken
// with the local-token door): the daily sweep's "system" seat.
const SYSTEM_ACTOR = id => ({ id, slug: "joe-local", display: "Agent (joe-local)", human: false,
  agent: true, via: "local-token", client_id: null, sponsoring_human_slug: "joe",
  human_slug: "joe", sponsor_required: false, native_agent_verified: true });
// A partner-sponsored native model agent: authenticated, sponsored, and still
// NOT a seat that may raise an urgent alert or reset the cadence clock.
const MODEL_AGENT_ACTOR = id => ({ id, slug: "claude", human: false, agent: true,
  via: "oauth-google", sponsoring_human_slug: "joe", human_slug: "joe",
  sponsor_required: true, native_agent_verified: true });

function a05(harnessed = harness()) {
  return deliveryCadenceA05Tools({
    withEnvelope: harnessed.withEnvelope, writeEvent: harnessed.writeEvent, ToolError,
  });
}

// ---------------------------------------------------------------------------
// Unit: the reader-route decision (review round 2, item 1).
// ---------------------------------------------------------------------------

test("V5A05-READER-ROUTE: cadence-status leaves the carr_reader route mcp.js takes for undeclared reads", () => {
  const tool = TOOLS["cadence-status"];
  assert.ok(tool, "cadence-status is registered");
  assert.equal(tool.write, false);
  assert.equal(connectionRouteForTool(tool), "writer_read_only",
    "ops.v5_a05_cadence_status is not executable by carr_reader; on the reader route the sweep's only read fails with 42501");
  // The same decision, held to its own contract so this test fails if the
  // helper is loosened rather than if the flag is dropped.
  assert.equal(connectionRouteForTool({ write: false }), "reader");
  assert.equal(connectionRouteForTool({ write: true }), "writer");
  assert.equal(connectionRouteForTool(TOOLS["record-cadence-receipt"]), "writer");
  assert.equal(connectionRouteForTool(TOOLS["raise-delivery-cadence-alert"]), "writer");
});

test("V5A05-SEATS: only a verified partner or the local machine credential holds a raising seat", () => {
  assert.equal(a05SeatForActor(PARTNER_ACTOR("x")), "authority");
  assert.equal(a05SeatForActor(ACTOR("x", "dell")), "authority");
  assert.equal(a05SeatForActor(SYSTEM_ACTOR("x")), "system");
  assert.equal(a05SeatForActor(MODEL_AGENT_ACTOR("x")), "other");
  assert.equal(a05SeatForActor({ ...SYSTEM_ACTOR("x"), via: "agent-token" }), "other",
    "the machine slug through any door but local-token is not the system seat");
  assert.equal(a05SeatForActor({ ...SYSTEM_ACTOR("x"), native_agent_verified: false }), "other");
  assert.equal(a05SeatForActor({ ...SYSTEM_ACTOR("x"), slug: "codex" }), "other");
  assert.equal(a05SeatForActor({ slug: "joe", human: false, via: "oauth-google" }), "other",
    "a partner slug without human:true is not the partner");
  assert.equal(a05SeatForActor(null), "other");
});

test("V5A05-SCHEMA: urgency and authority are not caller inputs the router honours", () => {
  const schema = TOOLS["raise-delivery-cadence-alert"].inputSchema;
  assert.equal(schema.additionalProperties, false);
  assert.ok(schema.properties.incident_ref, "an urgent reason must be able to cite an incident");
  assert.match(TOOLS["raise-delivery-cadence-alert"].description, /IGNORED/);
  assert.doesNotMatch(TOOLS["raise-delivery-cadence-alert"].description,
    /are the only other inputs that can/,
    "the description must not claim the booleans still move routing");
});

// Refusals that happen before any database read: an urgent reason from a seat
// that may not raise one is refused by the pure derivation. The stub client
// answers only the migration probe, so a refusal that tried to write would fail.
function probeOnlyClient() {
  const queries = [];
  return {
    queries,
    query: async (text) => {
      queries.push(text);
      if (text.includes("to_regprocedure")) return { rows: [{ status_fn: true, record_fn: true, mint_fn: true }] };
      throw new Error(`unexpected query in a refusal path: ${text.slice(0, 80)}`);
    },
  };
}

test("V5A05-FORGED-URGENCY: a model-agent seat naming security_incident is refused and writes nothing", async () => {
  const c = probeOnlyClient();
  await assert.rejects(
    a05()["raise-delivery-cadence-alert"].handler(c, MODEL_AGENT_ACTOR("a"), {
      idempotency_key: randomUUID(), reason_id: "security_incident",
      subject_type: "engineering_program", subject_ref: "doctorcre-v5",
    }),
    error => error.error === "urgent_alert_requires_system_or_authority_seat");
  assert.equal(c.queries.some(q => /insert/i.test(q)), false);
});

test("V5A05-FORGED-URGENCY: a model agent may not reset the cadence clock with a bare check-in", async () => {
  const c = probeOnlyClient();
  await assert.rejects(
    a05()["record-cadence-receipt"].handler(c, MODEL_AGENT_ACTOR("a"), {
      idempotency_key: randomUUID(), subject_type: "engineering_program", subject_ref: "doctorcre-v5",
    }),
    error => error.error === "cadence_receipt_requires_system_or_authority_seat");
});

// ---------------------------------------------------------------------------
// DB proofs.
// ---------------------------------------------------------------------------

async function skipUnlessDatabase(t) {
  if (!DSN) { t.skip("the migration class supplies DATABASE_URL"); return null; }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof writes cadence receipts and mints notifications against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg, slug = "joe") {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("select set_config('carr.acting_actor_slug',$1,false)", [slug]);
  await client.query("select set_config('carr.verified_human_actor_slug',$1,false)", [slug]);
  // ops.v5_a05_cadence_status (migration 0617) reads the completion register's
  // server-derived tenant; without this the FIRST case (cadence-status with no
  // receipt yet) throws before ever reaching the assertion under test.
  await client.query("select set_config('carr.organization_tenant_id',$1,false)", ["carr-internal"]);
  return client;
}

const wrap = client => ({ query: (text, values = []) => client.query(text, values) });

// The write route mcp.js gives a `write: true` verb: one transaction, as carr_writer.
async function dispatched(client, fn) {
  await client.query("begin");
  await client.query("set local role carr_writer");
  try {
    const value = await fn();
    await client.query("reset role");
    await client.query("commit");
    return value;
  } catch (error) {
    await client.query("reset role").catch(() => {});
    await client.query("rollback");
    throw error;
  }
}

// The route connectionRouteForTool names, reproduced against a real role:
// "reader" is carr_reader with no transaction semantics beyond one statement;
// "writer_read_only" is `begin read only` as carr_writer.
async function onRoute(client, route, fn) {
  const role = route === "reader" ? "carr_reader" : "carr_writer";
  await client.query(route === "writer_read_only" ? "begin read only" : "begin");
  await client.query(`set local role ${role}`);
  try {
    return await fn();
  } finally {
    await client.query("reset role").catch(() => {});
    await client.query("rollback").catch(() => {});
  }
}

async function joeAndDell(client) {
  const joe = (await client.query(
    "select id from public.actor where slug='joe' and kind='human' and active")).rows[0];
  assert.ok(joe, "this proof needs the partner actor row db/schema.sql seeds");
  const dell = (await client.query(
    "select id from public.actor where slug='dell' and kind='human' and active")).rows[0];
  return { joe, dell };
}

// Every incident these proofs seed is removed once the file finishes: the
// migration class shares one database, and program3-incident-gate.py counts
// every open production incident, so a leftover row fails a later gate.
const SEEDED_INCIDENTS = [];
after(async () => {
  if (!DSN || !SEEDED_INCIDENTS.length) return;
  const pg = (await import("pg")).default ?? (await import("pg"));
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  try {
    await client.query("delete from ops.incident where ref = any($1::text[])", [SEEDED_INCIDENTS]);
  } finally {
    await client.end().catch(() => {});
  }
});

async function seedIncident(client, overrides = {}) {
  const ref = `INC-A05-${randomUUID().slice(0, 8)}`;
  SEEDED_INCIDENTS.push(ref);
  const f = { severity: "SEV-1", state: "detected", environment: "production", ...overrides };
  await client.query(
    `insert into ops.incident(ref, title, severity, state, environment, detected_source,
                              source_kind, source_ref, signature)
     values ($1, 'V5-A05 proof incident', $2, $3, $4, 'v5-a05-test', 'operator', $1, $5)`,
    [ref, f.severity, f.state, f.environment, `v5-a05-test|${f.environment}|${ref}|proof`]);
  return ref;
}

async function withPreference(client, actorId, pref, fn) {
  const prior = (await client.query(
    "select * from ops.notification_preference where actor=$1", [actorId])).rows[0];
  await client.query("delete from ops.notification_preference where actor=$1", [actorId]);
  await client.query(
    `insert into ops.notification_preference(actor, device_opt_in, quiet_hours_start, quiet_hours_end, timezone)
     values ($1, true, $2, $3, $4)`, [actorId, pref.start, pref.end, pref.timezone]);
  try {
    return await fn();
  } finally {
    await client.query("delete from ops.notification_preference where actor=$1", [actorId]);
    if (prior)
      await client.query(
        `insert into ops.notification_preference(actor, device_opt_in, quiet_hours_start, quiet_hours_end, timezone, version)
         values ($1,$2,$3,$4,$5,$6)`,
        [prior.actor, prior.device_opt_in, prior.quiet_hours_start, prior.quiet_hours_end, prior.timezone, prior.version]);
  }
}

async function zoneWhereLocalTimeIsIn(client, from, to) {
  const row = (await client.query(
    `select name from pg_timezone_names
      where (now() at time zone name)::time >= $1::time
        and (now() at time zone name)::time <  $2::time
        and name like '%/%'
      order by name limit 1`, [from, to])).rows[0];
  assert.ok(row, `no timezone currently sits between ${from} and ${to}`);
  return row.name;
}

async function deliveryRows(client, notificationId) {
  return (await client.query(
    "select channel, state, held_until from ops.notification_delivery where notification_id=$1 order by channel",
    [notificationId])).rows;
}

async function backdateOnlyReceipt(client, receiptId) {
  // ops.v5_a05_cadence_receipt is append-only (v5_a05_cadence_receipt_immutable,
  // migration 0617) by design; this setup-only backdate disables that trigger
  // for one statement and re-enables it immediately. Production never does this.
  await client.query(
    "alter table ops.v5_a05_cadence_receipt disable trigger v5_a05_cadence_receipt_immutable");
  await client.query(
    `update ops.v5_a05_cadence_receipt set issued_at = now() - interval '20 days', expires_at = now() - interval '6 days'
      where id = $1`, [receiptId]);
  await client.query(
    "alter table ops.v5_a05_cadence_receipt enable trigger v5_a05_cadence_receipt_immutable");
}

test("V5A05-READER-ROUTE-DB: cadence-status is refused on the carr_reader route and answers on the writer read-only route", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);
  const subject = { subject_type: "engineering_program", subject_ref: `v5a05-route-${randomUUID()}` };
  const verb = a05()["cadence-status"];

  // The route mcp.js WOULD have taken without writerConnection -- the review's
  // blocker, reproduced against the real role rather than a privileged client.
  await onRoute(client, "reader", async () => {
    await assert.rejects(verb.handler(wrap(client), PARTNER_ACTOR(joe.id), subject),
      error => error.code === "42501");
  });
  // The route it takes now.
  const route = connectionRouteForTool(TOOLS["cadence-status"]);
  assert.equal(route, "writer_read_only");
  const status = await onRoute(client, route, () =>
    verb.handler(wrap(client), SYSTEM_ACTOR(joe.id), subject));
  assert.equal(status.status, "no_receipt_on_record");
  assert.equal(status.interval_days, 14);
});

test("V5A05-CADENCE-RECEIPT: record then read status current, then a backdated prior receipt reads missed", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);

  const verbs = a05();
  const subject = { subject_type: "engineering_program", subject_ref: `v5a05-test-${randomUUID()}` };
  const read = () => onRoute(client, "writer_read_only", () =>
    verbs["cadence-status"].handler(wrap(client), PARTNER_ACTOR(joe.id), subject));

  assert.equal((await read()).status, "no_receipt_on_record");

  const receiptArgs = { idempotency_key: randomUUID(), ...subject };
  const recorded = await dispatched(client, () =>
    verbs["record-cadence-receipt"].handler(wrap(client), SYSTEM_ACTOR(joe.id), receiptArgs));
  assert.equal(recorded.ok, true);
  assert.equal(recorded.deduplicated, false);
  assert.equal(recorded.replan_of, null);

  // Fresh harness: the in-memory withEnvelope cache is per-request, so the
  // second call exercises ops.v5_a05_record_cadence_receipt's real idempotency.
  const dup = await dispatched(client, () =>
    a05()["record-cadence-receipt"].handler(wrap(client), SYSTEM_ACTOR(joe.id), receiptArgs));
  assert.equal(dup.deduplicated, true, "the same idempotency_key never inserts twice");
  assert.equal(dup.receipt_id, recorded.receipt_id);

  const current = await read();
  assert.equal(current.status, "current");
  assert.equal(current.requires_replan, false);

  await backdateOnlyReceipt(client, recorded.receipt_id);
  const missed = await read();
  assert.equal(missed.status, "missed");
  assert.equal(missed.requires_replan, true);

  const replanned = await dispatched(client, () =>
    verbs["record-cadence-receipt"].handler(wrap(client), PARTNER_ACTOR(joe.id),
      { idempotency_key: randomUUID(), ...subject }));
  assert.equal(replanned.replan_of, recorded.receipt_id);
});

test("V5A05-CADENCE-MISS-VERIFIED: the server re-reads the cadence status; a miss the server cannot see is refused", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);
  const subject = { subject_type: "engineering_program", subject_ref: `v5a05-miss-${randomUUID()}` };

  const receipt = await dispatched(client, () =>
    a05()["record-cadence-receipt"].handler(wrap(client), SYSTEM_ACTOR(joe.id),
      { idempotency_key: randomUUID(), ...subject }));

  // Current: a caller claiming a miss (and asserting authority) is refused.
  await assert.rejects(dispatched(client, () =>
    a05()["raise-delivery-cadence-alert"].handler(wrap(client), SYSTEM_ACTOR(joe.id), {
      idempotency_key: randomUUID(), reason_id: "cadence_miss_replan_required", ...subject,
      requires_joe_authority: true,
    })), error => error.error === "cadence_miss_not_verified" && error.cadence_status === "current");

  await backdateOnlyReceipt(client, receipt.receipt_id);
  const raised = await dispatched(client, () =>
    a05()["raise-delivery-cadence-alert"].handler(wrap(client), SYSTEM_ACTOR(joe.id), {
      idempotency_key: randomUUID(), reason_id: "cadence_miss_replan_required", ...subject,
    }));
  assert.equal(raised.derivation.verified_by.kind, "server_clock_cadence_status");
  assert.equal(raised.routing.requires_joe_authority, true);
  assert.equal(raised.routing.routing, "batch_for_morning");
  assert.equal(raised.routing.bypasses_quiet_hours, false);
});

test("V5A05-ESCALATION-URGENT: a partner citing an open production SEV-1 incident delivers immediately and bypasses quiet hours", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);
  const incidentRef = await seedIncident(client);

  const result = await withPreference(client, joe.id, { start: "00:00", end: "23:59", timezone: "UTC" }, () =>
    dispatched(client, () =>
      a05()["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), {
        idempotency_key: randomUUID(), reason_id: "security_incident", incident_ref: incidentRef,
        subject_type: "engineering_program", subject_ref: `v5a05-urgent-${randomUUID()}`,
      })));

  assert.equal(result.routing.severity, "urgent");
  assert.equal(result.routing.routing, "deliver_immediately");
  assert.equal(result.derivation.verified_by.incident_ref, incidentRef);
  assert.equal(result.signal.severity, "critical",
    "MUTATION SURVIVAL: an urgent V5-A05 reason must not be silently downgraded below critical");
  assert.equal(result.notification.minted, true, "an urgent V5-A05 alert must mint a real notification");
  assert.equal(result.notification.bypassed_quiet_hours, true,
    "MUTATION SURVIVAL: an urgent alert must still deliver during forced quiet hours");
  assert.equal(result.notification.held_for_morning, false);
  const rows = await deliveryRows(client, result.notification.notification_id);
  assert.deepEqual(rows.map(r => [r.channel, r.state]), [["device", "pending"], ["in_app", "pending"]]);
  const stored = await client.query("select severity from ops.notification where id = $1",
    [result.notification.notification_id]);
  // ops.notification's severity vocabulary is not signal_event's: critical is
  // mapped through MINT_SEVERITY to failure at mint time.
  assert.equal(stored.rows[0]?.severity, "failure");
});

test("V5A05-FORGED-URGENCY-DB: urgent reasons without a verified incident, or citing one that is closed, low or not production, are refused and write nothing", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);

  const cases = [
    { name: "no incident cited", incident: undefined, expect: "urgent_alert_requires_verified_incident" },
    { name: "an incident nobody filed", incident: "INC-A05-does-not-exist", expect: "incident_not_found" },
    { name: "a SEV-3 incident", incident: await seedIncident(client, { severity: "SEV-3" }),
      expect: "urgent_alert_incident_not_verified", check: "incident_severity_not_urgent" },
    { name: "a staging incident", incident: await seedIncident(client, { environment: "staging" }),
      expect: "urgent_alert_incident_not_verified", check: "incident_not_production" },
  ];
  for (const [index, reason] of ["security_incident", "data_loss", "outward_harm", "security_incident"].entries()) {
    const kase = cases[index];
    const subjectRef = `v5a05-forged-${randomUUID()}`;
    await assert.rejects(dispatched(client, () =>
      a05()["raise-delivery-cadence-alert"].handler(wrap(client), SYSTEM_ACTOR(joe.id), {
        idempotency_key: randomUUID(), reason_id: reason, subject_type: "engineering_program",
        subject_ref: subjectRef, requires_joe_authority: true, unresolved_intent: true,
        ...(kase.incident ? { incident_ref: kase.incident } : {}),
      })), error => error.error === kase.expect &&
        (!kase.check || error.failed_checks.includes(kase.check)), kase.name);
    const written = await client.query(
      "select count(*)::int as n from signal_event where producer='v5-a05-delivery-cadence' and subject_ref=$1",
      [subjectRef]);
    assert.equal(written.rows[0].n, 0, `${kase.name}: a refused raise writes no signal`);
  }

  // A model-agent seat is refused even with a perfectly good incident.
  const good = await seedIncident(client);
  await assert.rejects(dispatched(client, () =>
    a05()["raise-delivery-cadence-alert"].handler(wrap(client), MODEL_AGENT_ACTOR(joe.id), {
      idempotency_key: randomUUID(), reason_id: "outward_harm", incident_ref: good,
      subject_type: "engineering_program", subject_ref: `v5a05-agent-${randomUUID()}`,
    })), error => error.error === "urgent_alert_requires_system_or_authority_seat");
});

test("V5A05-FORGED-AUTHORITY: caller booleans cannot make an ordinary blocker wake Joe", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);

  const result = await dispatched(client, () =>
    a05()["raise-delivery-cadence-alert"].handler(wrap(client), MODEL_AGENT_ACTOR(joe.id), {
      idempotency_key: randomUUID(), reason_id: "review_blocker",
      subject_type: "engineering_program", subject_ref: `v5a05-forged-authority-${randomUUID()}`,
      requires_joe_authority: true, unresolved_intent: true,
    }));
  assert.deepEqual([...result.ignored_caller_assertions].sort(), ["requires_joe_authority", "unresolved_intent"]);
  assert.equal(result.routing.requires_joe_authority, false);
  assert.equal(result.routing.unresolved_intent, false);
  assert.equal(result.routing.wakes_joe, false);
  assert.equal(result.routing.routing, "no_queue_entry");
  assert.equal(result.signal.severity, "info");
  assert.equal(result.notification.minted, false);
  assert.equal(result.notification.reason_id, "severity_not_notifiable");
});

test("V5A05-MORNING-HOLD: an ordinary authority-needing alert raised outside quiet hours is held until the morning window, on every surface", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);
  // Mid-afternoon for the recipient: outside quiet hours (22:00-07:00) and
  // outside the morning window (07:00-11:00).
  const afternoon = await zoneWhereLocalTimeIsIn(client, "13:00", "18:00");
  const subjectRef = `v5a05-hold-${randomUUID()}`;

  await withPreference(client, joe.id, { start: "22:00", end: "07:00", timezone: afternoon }, async () => {
    const result = await dispatched(client, () =>
      a05()["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), {
        idempotency_key: randomUUID(), reason_id: "decision_required",
        subject_type: "engineering_program", subject_ref: subjectRef,
      }));
    assert.equal(result.routing.routing, "batch_for_morning");
    assert.equal(result.notification.minted, true);
    assert.equal(result.notification.held_for_morning, true,
      "an ordinary blocker raised at mid-afternoon must be held, not delivered");
    const expected = (await client.query(
      `select ((date_trunc('day', now() at time zone $1) + interval '1 day' + time '07:00') at time zone $1) as at`,
      [afternoon])).rows[0].at;
    assert.equal(new Date(result.notification.held_until).getTime(), new Date(expected).getTime(),
      "the hold ends at the start of the recipient's next morning window");

    const rows = await deliveryRows(client, result.notification.notification_id);
    assert.deepEqual(rows.map(r => [r.channel, r.state]),
      [["device", "held_until_morning"], ["in_app", "held_until_morning"]],
      "MUTATION SURVIVAL: the device row must not be pending outside the morning window");

    // Not surfaced before morning: the morning-brief batch (on the reader
    // route, as morning-brief reads it) and the feed both omit it.
    const batch = await onRoute(client, "reader", async () =>
      (await client.query("select ops.v5_a05_assurance_cadence_batch('joe') as b")).rows[0].b);
    assert.equal(batch.some(row => row.subject_ref === subjectRef), false);
    const feed = await onRoute(client, "writer_read_only", async () =>
      (await client.query("select ops.notification_feed_facts(null, 200) as f")).rows[0].f);
    assert.equal(feed.notifications.some(row => row.subject_ref === subjectRef), false);

    // Once the hold has elapsed (setup-only: move the release instant into the
    // past), the morning brief is where it surfaces.
    await client.query(
      "update ops.notification_delivery set held_until = now() - interval '1 minute' where notification_id=$1",
      [result.notification.notification_id]);
    const released = await onRoute(client, "reader", async () =>
      (await client.query("select ops.v5_a05_assurance_cadence_batch('joe') as b")).rows[0].b);
    assert.equal(released.some(row => row.subject_ref === subjectRef), true);
  });
});

test("V5A05-MORNING-WINDOW: the same ordinary alert raised inside the morning window is not held", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);
  const morning = await zoneWhereLocalTimeIsIn(client, "08:00", "10:00");

  await withPreference(client, joe.id, { start: "22:00", end: "07:00", timezone: morning }, async () => {
    const result = await dispatched(client, () =>
      a05()["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), {
        idempotency_key: randomUUID(), reason_id: "decision_required",
        subject_type: "engineering_program", subject_ref: `v5a05-window-${randomUUID()}`,
      }));
    assert.equal(result.notification.held_for_morning, false);
    const rows = await deliveryRows(client, result.notification.notification_id);
    assert.deepEqual(rows.map(r => [r.channel, r.state]), [["device", "pending"], ["in_app", "pending"]]);
  });
});

test("V5A05-QUIET-HOURS-ORDINARY-NOT-BYPASSED: during quiet hours an ordinary alert is held, never a bypass", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);
  const night = await zoneWhereLocalTimeIsIn(client, "02:00", "03:00");

  await withPreference(client, joe.id, { start: "22:00", end: "07:00", timezone: night }, async () => {
    const result = await dispatched(client, () =>
      a05()["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), {
        idempotency_key: randomUUID(), reason_id: "decision_required",
        subject_type: "engineering_program", subject_ref: `v5a05-quiet-ordinary-${randomUUID()}`,
      }));
    assert.equal(result.routing.bypasses_quiet_hours, false);
    assert.equal(result.notification.bypassed_quiet_hours, false);
    assert.equal(result.notification.held_for_morning, true);
    const rows = await deliveryRows(client, result.notification.notification_id);
    assert.equal(rows.find(r => r.channel === "device").state, "held_until_morning");
  });
});

test("V5A05-DURABLE-MISS-RECORD: the signal_event row survives even when the notification never mints (finding 8)", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe } = await joeAndDell(client);

  const args = {
    idempotency_key: randomUUID(), reason_id: "decision_required",
    subject_type: "engineering_program", subject_ref: `v5a05-durable-${randomUUID()}`,
  };
  const first = await dispatched(client, () =>
    a05()["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), args));
  assert.equal(first.duplicate, false);

  // Fresh harness, so the second call exercises signal_event's real
  // (producer,signal_key) upsert rather than the in-memory envelope cache.
  const second = await dispatched(client, () =>
    a05()["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), args));
  assert.equal(second.duplicate, true);
  assert.equal(second.notification.minted, false);

  const stored = await client.query(
    "select signal_kind from signal_event where producer=$1 and signal_key=$2",
    ["v5-a05-delivery-cadence",
     `v5-a05:decision_required:engineering_program:${args.subject_ref}:${args.idempotency_key}`]);
  assert.equal(stored.rows.length, 1,
    "MUTATION SURVIVAL / finding 8: the signal_event row must persist regardless of notification outcome");
});

test("V5A05-MORNING-BRIEF-BATCH: the batch returns only the named partner's own V5-A05 notifications and refuses a non-partner slug", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const { joe, dell } = await joeAndDell(client);
  assert.ok(dell, "this proof needs the dell partner actor row db/schema.sql seeds");

  const joeSubject = `v5a05-batch-joe-${randomUUID()}`;
  const dellSubject = `v5a05-batch-dell-${randomUUID()}`;
  const joeIncident = await seedIncident(client);
  await dispatched(client, () =>
    a05()["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), {
      idempotency_key: randomUUID(), reason_id: "security_incident", incident_ref: joeIncident,
      subject_type: "engineering_program", subject_ref: joeSubject,
    }));
  const dellIncident = await seedIncident(client);
  await dispatched(client, () =>
    a05()["raise-delivery-cadence-alert"].handler(wrap(client), ACTOR(dell.id, "dell"), {
      idempotency_key: randomUUID(), reason_id: "security_incident", incident_ref: dellIncident,
      subject_type: "engineering_program", subject_ref: dellSubject,
    }));

  await onRoute(client, "reader", async () => {
    const joeBatch = (await client.query(
      "select ops.v5_a05_assurance_cadence_batch($1) as batch", ["joe"])).rows[0].batch;
    assert.ok(Array.isArray(joeBatch));
    assert.ok(joeBatch.some(row => row.subject_ref === joeSubject),
      "the recipient's own alert must be present");
    assert.equal(joeBatch.some(row => row.subject_ref === dellSubject), false,
      "MUTATION SURVIVAL: dropping the recipient filter would leak dell's alert into joe's batch");
  });
  for (const foreign of ["joe-local", "claude", "nobody"]) {
    await onRoute(client, "reader", async () => {
      await assert.rejects(
        client.query("select ops.v5_a05_assurance_cadence_batch($1) as batch", [foreign]),
        error => error.code === "42501", `the batch must refuse recipient ${foreign}`);
    });
  }
});
