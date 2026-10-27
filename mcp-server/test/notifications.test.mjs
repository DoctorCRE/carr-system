// WR-000113 — the R03 notification acceptance proofs.
//
// EVERY MINT CASE RUNS THROUGH record-signal, not through SQL. The mint has ONE
// production call site and a proof that calls ops.mint_notification directly
// proves the function rather than the product.
//
// The store cases skip in the unit class; the migration class supplies
// DATABASE_URL and sets CARR_R03_DB_REQUIRED=1, which turns a skip into a
// failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { notificationTools, notificationFeedProjection, notificationPreferenceProjection }
  from "../src/notifications.js";
import { investigationTools } from "../src/investigation.js";
import { docConversationTools } from "../src/doc-conversation.js";
import { TOOLS } from "../src/tools.js";
import { PROFILES } from "../src/mcp.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_R03_DB_REQUIRED === "1";
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

async function skipUnlessDatabase(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof mints notifications and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg, slug = "joe") {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("select set_config('carr.acting_actor_slug',$1,false)", [slug]);
  await client.query("select set_config('carr.verified_human_actor_slug',$1,false)", [slug]);
  return client;
}

const wrap = client => ({ query: (text, values = []) => client.query(text, values) });

/**
 * The dispatcher owns ONE transaction per verb (mcp.js:707 begin, :730 commit),
 * and the savepoint the mint runs inside only exists in a transaction block. A
 * proof that called the handler bare would be proving a shape production never
 * uses, so every handler call here is wrapped the way mcp.js wraps it.
 */
async function dispatched(client, fn) {
  await client.query("begin");
  try {
    const value = await fn();
    await client.query("commit");
    return value;
  } catch (error) {
    await client.query("rollback");
    throw error;
  }
}

/** The partner actor the mint resolves to, and the automation actor that signals. */
async function fixtureActors(client) {
  const joe = (await client.query(
    "select id from public.actor where slug='joe' and kind='human' and active")).rows[0];
  assert.ok(joe, "this proof needs the partner actor row db/schema.sql seeds");
  const producer = (await client.query(
    `insert into public.actor(slug, kind, display_name, active) values ('codex','automation','Codex',true)
       on conflict (slug) do update set active = true returning id`)).rows[0];
  return { joe, producer };
}

const signalArgs = (overrides = {}) => ({
  idempotency_key: randomUUID(),
  producer: "wr113-proof",
  signal_key: `key-${randomUUID()}`,
  signal_kind: "budget_threshold",
  subject_type: "deal",
  subject_ref: "deal-1",
  metric_name: "spend_units",
  observed_value: 120,
  threshold_value: 100,
  comparison: "gt",
  severity: "critical",
  detected_at: "2026-09-17T12:00:00.000Z",
  evidence_refs: ["evidence:one"],
  payload: {},
  ...overrides,
});

/** Joe's own verified session: personalScopeForActor returns personal/joe. */
const PARTNER_ACTOR = id => ({ id, slug: "joe", human: true, via: "oauth-google" });
/** An unsponsored runtime token: personalScopeForActor returns status "none". */
const UNSPONSORED_ACTOR = id => ({ id, slug: "codex", human: false, via: "cli" });

function investigation(harnessed) {
  return investigationTools({
    withEnvelope: harnessed.withEnvelope, writeEvent: harnessed.writeEvent, ToolError,
  });
}

// ---------------------------------------------------------------------------
// The verb surface.
// ---------------------------------------------------------------------------

test("R03-VERBS-REACHABLE: both verbs are registered with the flags their grants require", () => {
  assert.ok(TOOLS["acknowledge-notification"]);
  assert.ok(TOOLS["notification-feed"]);
  assert.equal(TOOLS["acknowledge-notification"].write, true);
  assert.equal(TOOLS["acknowledge-notification"].authorityOnly, undefined,
    "the acknowledge function is granted to carr_writer; an authorityOnly flag would refuse it");
  assert.equal(TOOLS["notification-feed"].write, undefined);
  assert.equal(TOOLS["notification-feed"].writerConnection, true,
    "the feed resolves the caller's actor from a context only the writer path installs");
  for (const profile of ["capture", "away"]) {
    for (const name of ["acknowledge-notification", "notification-feed"]) {
      assert.ok(!PROFILES[profile].has(name), `${name} must not be in the ${profile} profile`);
    }
  }
  // The criterion's own words are "reachable through /mcp on the deployed
  // Worker". That is a step-twelve fact and this case does not claim it.
});

test("the feed shaper refuses anything that is not the definer function's own shape", () => {
  assert.throws(() => notificationFeedProjection(null, null, ToolError), /notification_feed_unavailable/);
  assert.throws(() => notificationFeedProjection({ ok: false }, null, ToolError), /notification_feed_unavailable/);
});

// ---------------------------------------------------------------------------
// The mint, through record-signal.
// ---------------------------------------------------------------------------

function quietWindowAt(instant) {
  // A two-hour window around the database clock includes the current instant
  // at every time of day, including the wrap across midnight.
  return [-1, 1].map(hours => new Date(instant + hours * 3600000).toISOString().slice(11, 19));
}

test("quiet-hours acceptance fixture includes midnight and the final minute", () => {
  for (const clock of ["2026-10-01T00:00:00Z", "2026-10-01T23:59:30Z"]) {
    const [start, end] = quietWindowAt(Date.parse(clock));
    const local = clock.slice(11, 19);
    assert.ok(start <= end ? local >= start && local < end : local >= start || local < end);
  }
});

// ---------------------------------------------------------------------------
// WR-000116 — the notification-preference pair.
//
// The store cases skip in the unit class and are REQUIRED in the migration
// class (CARR_R03_DB_REQUIRED=1), so a silent skip cannot pass as a pass.
// ---------------------------------------------------------------------------

/** The verbs under test, wired to the same envelope/event harness. */
function preferences(harnessed) {
  return notificationTools({
    withEnvelope: harnessed.withEnvelope, writeEvent: harnessed.writeEvent, ToolError,
  });
}

const prefRows = async client =>
  Number((await client.query("select count(*) n from ops.notification_preference")).rows[0].n);

async function fixedQuietClock(client) {
  const { definition } = (await client.query(
    "select pg_get_functiondef('ops.notification_quiet_now(uuid)'::regprocedure) as definition")).rows[0];
  await client.query(definition.replaceAll('now()', "'2026-10-27 07:00:00+00'::timestamptz"));
  return () => client.query(definition);
}

test("AC-PREF-READ: the schema accepts no property at all", () => {
  const schema = TOOLS["read-notification-preferences"].inputSchema;
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(Object.keys(schema.properties).sort(), []);
  assert.deepEqual(schema.required, []);
  assert.equal(TOOLS["read-notification-preferences"].write, undefined);
  assert.equal(TOOLS["read-notification-preferences"].writerConnection, true);
  assert.equal(TOOLS["read-notification-preferences"].authorityOnly, undefined,
    "the app calls this as the signed-in partner; an authorityOnly flag would refuse it");
});

test("AC-PREF-WRITE: the write verb's schema names exactly the documented fields and no actor",
  () => {
    const schema = TOOLS["set-notification-preference"].inputSchema;
    assert.equal(schema.additionalProperties, false);
    // An EXACT sorted key list, not a loop over forbidden names: a newly
    // invented actor-shaped field fails this and a denylist would not.
    assert.deepEqual(Object.keys(schema.properties).sort(), [
      "base_version", "clear_quiet_hours", "device_opt_in",
      "idempotency_key", "quiet_hours_end", "quiet_hours_start", "timezone",
    ]);
    assert.deepEqual(schema.required.slice().sort(), ["base_version", "idempotency_key"]);
    assert.equal(TOOLS["set-notification-preference"].write, true);
    assert.equal(TOOLS["set-notification-preference"].writerConnection, true);
    assert.equal(TOOLS["set-notification-preference"].authorityOnly, undefined);
    for (const profile of ["capture", "away"]) {
      for (const name of ["read-notification-preferences", "set-notification-preference"]) {
        assert.ok(!PROFILES[profile].has(name), `${name} must not be in the ${profile} profile`);
      }
    }
  });

test("the preference shaper refuses anything that is not the definer function's own shape", () => {
  assert.throws(() => notificationPreferenceProjection(null, ToolError),
    /notification_preferences_unavailable/);
  assert.throws(() => notificationPreferenceProjection({ ok: false }, ToolError),
    /notification_preferences_unavailable/);
});

test("AC-PREF-READ: returns documented defaults for an actor with no row and inserts nothing",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);

    const before = await prefRows(client);
    const answer = await dispatched(client, () =>
      preferences(harness())["read-notification-preferences"].handler(c, PARTNER_ACTOR(joe.id), {}));
    const after = await prefRows(client);

    assert.deepEqual(answer, {
      ok: true, exists: false, device_opt_in: false,
      quiet_hours_start: null, quiet_hours_end: null,
      timezone: "UTC", version: 1, quiet_now: false,
    });
    // THE COUNT, not merely the values: a read that inserted a defaults row
    // would return exactly the same object and pass without this line.
    assert.equal(after, before, "the read inserted a row: defaults must be computed, not written");
  });

test("AC-PREF-READ: returns the actor's own row, and the answer follows the acting context and not any argument",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const joeClient = await connect(pg, "joe");
    const dellClient = await connect(pg, "dell");
    t.after(() => Promise.all([joeClient.end().catch(() => {}), dellClient.end().catch(() => {})]));
    const { joe } = await fixtureActors(joeClient);
    const dell = (await joeClient.query(
      "select id from public.actor where slug='dell' and kind='human' and active")).rows[0];
    assert.ok(dell, "this proof needs the second partner actor row db/schema.sql seeds");

    await joeClient.query("delete from ops.notification_preference where actor = any($1::uuid[])",
      [[joe.id, dell.id]]);
    await joeClient.query(
      `insert into ops.notification_preference(actor, device_opt_in, quiet_hours_start,
                                               quiet_hours_end, timezone, version)
         values ($1,true,'22:00','07:00','America/Chicago',5)`, [joe.id]);

    const read = client => dispatched(client, () =>
      preferences(harness())["read-notification-preferences"]
        .handler(wrap(client), PARTNER_ACTOR(joe.id), {}));

    const mine = await read(joeClient);
    assert.equal(mine.exists, true);
    assert.equal(mine.device_opt_in, true);
    assert.equal(mine.quiet_hours_start, "22:00:00");
    assert.equal(mine.quiet_hours_end, "07:00:00");
    assert.equal(mine.timezone, "America/Chicago");
    assert.equal(mine.version, 5);

    // The SAME call shape, a different acting context, a different answer.
    const theirs = await read(dellClient);
    assert.equal(theirs.exists, false, "the other partner must not see this row");
    assert.equal(theirs.timezone, "UTC");
    await joeClient.query("delete from ops.notification_preference where actor=$1", [joe.id]);
  });

test("AC-PREF-WRITE: the first save against base_version 1 with no row succeeds and lands at version 2, a stale base_version returns version_conflict and leaves version unmoved, and replaying one idempotency_key returns the same result without moving the version",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
    // A FRESH HARNESS PER CALL, deliberately: withEnvelope memoises on
    // (verb, idempotency_key) and a shared harness would answer the replay from
    // its own cache without the database ever seeing the second call. Two
    // separate requests is what production does, and the replay this criterion
    // names is the FUNCTION's, proved through ops.notification_preference_write.
    const set = args => dispatched(client, () =>
      preferences(harness())["set-notification-preference"]
        .handler(c, PARTNER_ACTOR(joe.id), args));
    const storedVersion = async () => (await client.query(
      "select version from ops.notification_preference where actor=$1", [joe.id])).rows[0]?.version;

    // THE FIRST SAVE. The read documents version 1 for a missing row, so this
    // is the version a partner's first save carries, and it must not be a
    // conflict.
    const first = await set({ idempotency_key: randomUUID(), base_version: 1,
      device_opt_in: true, timezone: "UTC" });
    assert.equal(first.ok, true);
    assert.equal(first.version, 2, "the first save must land at version 2");
    assert.equal(first.exists, true);
    assert.equal(await storedVersion(), 2);

    // A STALE base_version. The refusal is a named reason, not a raise, and it
    // leaves the row exactly where it was.
    await assert.rejects(() => set({ idempotency_key: randomUUID(), base_version: 1,
      device_opt_in: false }), error => {
      assert.equal(error.error, "version_conflict");
      assert.equal(error.current_version, 2);
      return true;
    });
    assert.equal(await storedVersion(), 2, "a refused swap must leave the version unmoved");

    // THE REPLAY. Same key, same payload: the same result and NO second bump.
    const key = randomUUID();
    const once = await set({ idempotency_key: key, base_version: 2, device_opt_in: false });
    assert.equal(once.version, 3);
    assert.equal(once.deduplicated, false);
    const twice = await set({ idempotency_key: key, base_version: 2, device_opt_in: false });
    assert.equal(twice.version, 3, "a replayed key must not bump the version a second time");
    assert.equal(twice.deduplicated, true);
    assert.equal(await storedVersion(), 3, "a replay must not move the stored row");
    assert.deepEqual({ ...twice, deduplicated: false }, once);

    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
  });

test("AC-PREF-WRITE: exactly one of start/end is refused by name before any write, an unknown timezone is refused, and clear_quiet_hours nulls both",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
    const set = args => dispatched(client, () =>
      preferences(harness())["set-notification-preference"]
        .handler(c, PARTNER_ACTOR(joe.id), args));
    const stored = async () => (await client.query(
      `select quiet_hours_start, quiet_hours_end, timezone, version
         from ops.notification_preference where actor=$1`, [joe.id])).rows[0];

    const opened = await set({ idempotency_key: randomUUID(), base_version: 1,
      device_opt_in: true, timezone: "UTC" });
    assert.equal(opened.version, 2);

    // ONLY a start, and nothing stored supplies the end: refused BY NAME, and
    // not as a 23514 check violation from the table's own constraint.
    await assert.rejects(() => set({ idempotency_key: randomUUID(), base_version: 2,
      quiet_hours_start: "22:00" }), error => {
      assert.equal(error.error, "notification_preference_quiet_hours_incomplete");
      return true;
    });
    assert.equal((await stored()).version, 2, "a refusal must happen BEFORE any write");

    // An unknown zone: refused here, rather than poisoning the READ door with a
    // 22023 on every later call.
    await assert.rejects(() => set({ idempotency_key: randomUUID(), base_version: 2,
      timezone: "Nowhere/Nada" }), error => {
      assert.equal(error.error, "notification_preference_timezone_unknown");
      return true;
    });
    assert.equal((await stored()).version, 2);

    // BOTH together is a complete pair and succeeds.
    const both = await set({ idempotency_key: randomUUID(), base_version: 2,
      quiet_hours_start: "22:00", quiet_hours_end: "07:00" });
    assert.equal(both.quiet_hours_start, "22:00:00");
    assert.equal(both.quiet_hours_end, "07:00:00");

    // Setting only the END when a START is already stored is a complete
    // POST-MERGE pair and must succeed: the check is against what the row would
    // become, not against the arguments alone.
    const endOnly = await set({ idempotency_key: randomUUID(), base_version: both.version,
      quiet_hours_end: "06:30" });
    assert.equal(endOnly.quiet_hours_start, "22:00:00");
    assert.equal(endOnly.quiet_hours_end, "06:30:00");

    // A clear that also carries a time is two intentions at once.
    await assert.rejects(() => set({ idempotency_key: randomUUID(),
      base_version: endOnly.version, clear_quiet_hours: true, quiet_hours_start: "23:00" }),
    error => {
      assert.equal(error.error, "notification_preference_quiet_hours_conflicting_request");
      return true;
    });

    // The clear nulls BOTH: without the explicit flag the pair could never be
    // returned to its off state through this door.
    const cleared = await set({ idempotency_key: randomUUID(),
      base_version: endOnly.version, clear_quiet_hours: true });
    assert.equal(cleared.quiet_hours_start, null);
    assert.equal(cleared.quiet_hours_end, null);
    const row = await stored();
    assert.equal(row.quiet_hours_start, null);
    assert.equal(row.quiet_hours_end, null);

    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
  });

// ---------------------------------------------------------------------------
// AC-PREF-FEED. The shaper half is pure and runs in every class; the live half
// (the promoted production Worker) is release-time and is not claimed here.
//
// These cases pin the database clock while comparing two real timezones.
// ---------------------------------------------------------------------------

const feedFacts = (delivery = []) => ({
  ok: true, unread_count: 1,
  notifications: [{ id: "n1", severity: "failure", reason: "r", subject_type: "deal",
    subject_ref: "d1", deep_link: "/deals/d1", created_at: "2026-09-18T00:00:00Z",
    read_at: null, delivery }],
});

test("AC-PREF-FEED: notification-feed's inputSchema is byte-identical to its v32 shape", () => {
  assert.deepEqual(TOOLS["notification-feed"].inputSchema, {
    type: "object", additionalProperties: false,
    properties: { after: { type: "string" },
      limit: { type: "integer", minimum: 1, maximum: 200 } },
    required: [],
  });
  assert.equal(TOOLS["notification-feed"].write, undefined);
  assert.equal(TOOLS["notification-feed"].writerConnection, true);
});

test("AC-PREF-FEED: quiet_now marks every row, a cleared window marks none, and a mint-time suppressed device row stays marked after the window ends",
  () => {
    const quiet = notificationFeedProjection(feedFacts(), { ok: true, quiet_now: true }, ToolError);
    assert.equal(quiet.quiet_now, true);
    assert.equal(quiet.notifications[0].quiet_suppressed, true);

    const cleared = notificationFeedProjection(feedFacts(), { ok: true, quiet_now: false }, ToolError);
    assert.equal(cleared.quiet_now, false);
    assert.equal(cleared.notifications[0].quiet_suppressed, false,
      "quiet hours cleared marks none");

    // A fact about WHAT HAPPENED, not a fact about now: the device push that
    // was suppressed at mint time stays marked once the window ends.
    const historical = notificationFeedProjection(
      feedFacts([{ channel: "device", state: "suppressed_quiet_hours" }]),
      { ok: true, quiet_now: false }, ToolError);
    assert.equal(historical.quiet_now, false);
    assert.equal(historical.notifications[0].quiet_suppressed, true);

    // The feed must not stop working because the preference door did.
    for (const broken of [null, undefined, {}, { ok: false }, "nonsense"]) {
      const answer = notificationFeedProjection(feedFacts(), broken, ToolError);
      assert.equal(answer.quiet_now, false);
      assert.equal(answer.notifications[0].quiet_suppressed, false);
    }
  });

test("AC-PREF-FEED wrap-around: quiet hours 22:00-07:00 in a zone where it is 02:00, one notification, feed marks it",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    const restoreClock = await fixedQuietClock(client);
    t.after(async () => {
      try { await restoreClock(); } finally { await client.end(); }
    });
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    const night = "America/Chicago";
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
    await client.query(
      `insert into ops.notification_preference(actor, device_opt_in, quiet_hours_start,
                                               quiet_hours_end, timezone)
         values ($1,true,'22:00','07:00',$2)`, [joe.id, night]);

    const verbs = preferences(harness());
    const feed = await dispatched(client, () =>
      verbs["notification-feed"].handler(c, PARTNER_ACTOR(joe.id), {}));

    // Under a naive `start <= now < end` this is false at every hour of the
    // night, and this is the case that goes red for it.
    assert.equal(feed.quiet_now, true, `22:00-07:00 must cover 02:00 in ${night}`);
    for (const row of feed.notifications) {
      assert.equal(row.quiet_suppressed, true);
    }
    // The word AC-PREF-FEED uses is MARKS, not omits.
    const total = Number((await client.query(
      "select count(*) n from ops.notification where recipient_actor=$1", [joe.id])).rows[0].n);
    assert.equal(feed.notifications.length, Math.min(total, 50),
      "a shorter list is a failure, not a pass");
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
  });

test("AC-PREF-FEED daytime: quiet hours 09:00-17:00 in a zone where it is 12:00 marks the row (this case passes under BOTH implementations and proves nothing on its own)",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    const restoreClock = await fixedQuietClock(client);
    t.after(async () => {
      try { await restoreClock(); } finally { await client.end(); }
    });
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    const day = "Asia/Karachi";
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
    await client.query(
      `insert into ops.notification_preference(actor, device_opt_in, quiet_hours_start,
                                               quiet_hours_end, timezone)
         values ($1,true,'09:00','17:00',$2)`, [joe.id, day]);
    const feed = await dispatched(client, () =>
      preferences(harness())["notification-feed"].handler(c, PARTNER_ACTOR(joe.id), {}));
    assert.equal(feed.quiet_now, true);
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
  });

test("AC-PREF-FEED: two zones, one instant, one stored window, opposite answers",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    const restoreClock = await fixedQuietClock(client);
    t.after(async () => {
      try { await restoreClock(); } finally { await client.end(); }
    });
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    const night = "America/Chicago";
    const day = "Asia/Karachi";
    const read = async zone => {
      await client.query(
        `insert into ops.notification_preference(actor, device_opt_in, quiet_hours_start,
                                                 quiet_hours_end, timezone)
           values ($1,true,'22:00','07:00',$2)
         on conflict (actor) do update set quiet_hours_start='22:00',
           quiet_hours_end='07:00', timezone=excluded.timezone`, [joe.id, zone]);
      return dispatched(client, () =>
        preferences(harness())["read-notification-preferences"]
          .handler(c, PARTNER_ACTOR(joe.id), {}));
    };
    // THE ASSERTION A UTC-ONLY IMPLEMENTATION CANNOT PASS.
    assert.equal((await read(night)).quiet_now, true);
    assert.equal((await read(day)).quiet_now, false);
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
  });
