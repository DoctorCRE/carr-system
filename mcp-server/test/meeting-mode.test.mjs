// V5-UX-B11 — non-recording shared Meeting Mode acceptance proofs.
//
// UX05  double click, two-client acceptance, reconnect and rejoin yield one
//       logical action; disconnect after dispatch reconciles before any retry.
// UX13  one shared processing owner and action stream; repeated contributions
//       and acceptances dedupe; tentative stays proposed; revisions keep history.
// UX14  no audio is captured; nothing here is evidence for the D03 recorder.
//
// The verb-surface cases are pure. The store cases need a real PostgreSQL and
// skip in the unit class; the migration class supplies DATABASE_URL and sets
// CARR_MEETING_MODE_DB_REQUIRED=1, which turns a silent skip into a failure.
// Concurrency is proved with separate connections and real row locks, never by
// sequencing calls and calling them concurrent.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { randomUUID } from "node:crypto";

import {
  MEETING_MODE_SCHEMA_VERSION, MEETING_MODE_VERBS, MEETING_MODE_WRITE_VERBS,
  assertNoRecordingFields, meetingModeTools, meetingProjection, meetingRecap, validateMeetingCommand,
} from "../src/meeting-mode.js";
import {
  V5_J201_PROMPT_LEDGER_OWNER_SEAM, V5_J201_RECORDING_FRAGMENTS, V5_J201_REFUSED_ACTIVATION_INTENTS,
  meetingModeGaps,
} from "../src/meeting-call-mode-j201.v5.js";
import { TOOLS } from "../src/tools.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_MEETING_MODE_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const TENANT = "carr-internal";
const MIGRATION = fs.readFileSync(new URL("../../migrations/0556_meeting_mode_store.sql", import.meta.url), "utf8");
const SOURCE = fs.readFileSync(new URL("../src/meeting-mode.js", import.meta.url), "utf8");

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const lookupTool = name => (Object.hasOwn(TOOLS, name) ? TOOLS[name] : null);

/** No replay cache on purpose: every dedupe asserted below is the store's own. */
function harness() {
  const events = [];
  return {
    events,
    tools: meetingModeTools({
      withEnvelope: async (_c, _actor, _verb, _args, fn) => fn(),
      writeEvent: async (_c, _actor, verb, subjectType, subjectId, fields) => {
        events.push({ verb, subjectType, subjectId, fields });
      },
      ToolError,
      lookupTool,
    }),
  };
}

const refusal = error => e => e instanceof ToolError && e.payload.error === error;

// ---------------------------------------------------------------------------
// Pure: the verb surface.
// ---------------------------------------------------------------------------

test("the registry carries exactly the eight meeting verbs, as closed writer-connection doors", () => {
  const registered = Object.keys(TOOLS).filter(name => MEETING_MODE_VERBS.includes(name)).sort();
  assert.deepEqual(registered, [...MEETING_MODE_VERBS].sort());
  for (const name of MEETING_MODE_VERBS) {
    const tool = TOOLS[name];
    assert.equal(tool.registrySource, "mcp-server/src/meeting-mode.js", name);
    assert.equal(tool.writerConnection, true, `${name} must carry the actor context`);
    assert.notEqual(tool.authorityOnly, true, `${name} is called by the signed-in partner, never authority-only`);
    assert.notEqual(tool.humanOnly, true, name);
    assert.equal(tool.write === true, MEETING_MODE_WRITE_VERBS.includes(name), name);
    assert.equal(tool.inputSchema.additionalProperties, false, `${name} schema must be closed`);
    const names = JSON.stringify(tool.inputSchema).match(/"([a-z_]+)":\{/g) ?? [];
    for (const field of names.map(n => n.slice(1, -3))) {
      assert.doesNotMatch(field, /actor|tenant|created_by|started_by|decided_by/,
        `${name}.${field}: attribution is derived by the store, never supplied`);
      assert.equal(V5_J201_RECORDING_FRAGMENTS.some(f => field.includes(f)), false,
        `${name}.${field} must not name audio`);
    }
    if (tool.write) assert.ok(tool.inputSchema.required.includes("idempotency_key"), name);
  }
});

test("UX14: a field naming audio is refused by name, at any depth, before anything reaches the store", async () => {
  const { tools } = harness();
  let queries = 0;
  const client = { query: async () => { queries += 1; return { rows: [] }; } };
  const key = randomUUID();
  const cases = [
    ["start-meeting", { idempotency_key: key, title: "t", activation_intent: "one_tap_user_activation",
      client_instance: "mac-a", native_identity: { source_system: "zoom", native_id: "1", native_id_epoch: "e" },
      audio_capture: true }],
    ["add-meeting-note", { idempotency_key: key, meeting_id: key, body: "b", client_instance: "mac-a",
      transcript: "the raw words" }],
    ["propose-meeting-action", { idempotency_key: key, meeting_id: key, summary: "s",
      basis: "tentative_discussion", client_instance: "mac-a",
      command: { verb: "add-loop", args: { kind: "task", owner: "joe", notes: { voice_memo: "x" } } } }],
    ["read-meeting", { meeting_id: key, microphone: "on" }],
  ];
  for (const [verb, args] of cases) {
    await assert.rejects(() => tools[verb].handler(client, { slug: "joe" }, args),
      e => e instanceof ToolError && e.payload.error === "recording_field_refused"
        && e.payload.recording === "denied", verb);
  }
  assert.equal(queries, 0, "a refused audio field must never reach the database");
});

test("UX14: silent activation refuses by name and nothing else starts a meeting", async () => {
  const { tools } = harness();
  let queries = 0;
  const client = { query: async () => { queries += 1; return { rows: [] }; } };
  const base = { idempotency_key: randomUUID(), title: "Weekly", client_instance: "mac-a",
    native_identity: { source_system: "zoom", native_id: "8812", native_id_epoch: "2026-09-23" } };
  for (const intent of V5_J201_REFUSED_ACTIVATION_INTENTS) {
    await assert.rejects(() => tools["start-meeting"].handler(client, { slug: "joe" },
      { ...base, activation_intent: intent }),
      e => e.payload.error === "silent_activation_refused" && e.payload.attempted_activation_intent === intent);
  }
  await assert.rejects(() => tools["start-meeting"].handler(client, { slug: "joe" },
    { ...base, activation_intent: "detected_meeting" }), refusal("explicit_human_activation_required"));
  assert.equal(queries, 0);
});

test("UX14: the store has no audio column and pins recording to denied; the legacy recorder is not reused", () => {
  assert.match(MIGRATION, /recording text not null default 'denied' check \(recording = 'denied'\)/);
  const tables = [...MIGRATION.matchAll(/create table ops\.(\w+) \(([\s\S]*?)\n\);/g)];
  assert.equal(tables.length, 6);
  for (const [, table, body] of tables) {
    const columns = body.split("\n").map(line => line.trim().match(/^([a-z_]+) /)?.[1])
      .filter(name => name && !["check", "constraint", "primary", "unique", "foreign"].includes(name));
    for (const column of columns) {
      if (table === "meeting" && column === "recording") continue; // the denial marker itself
      assert.equal(V5_J201_RECORDING_FRAGMENTS.some(f => column.includes(f)), false, `${table}.${column}`);
    }
  }
  for (const text of [MIGRATION.replace(/^\s*--.*$/gm, ""), SOURCE.replace(/^\s*\/\/.*$/gm, "")]) {
    assert.doesNotMatch(text, /capture_session|call_capture/i);
  }
  assert.doesNotMatch(SOURCE, /from\s+"[^"]*(capture|record|audio)[^"]*"/i, "no recorder module is imported");
});

test("the J201 prompt-ledger gap is reported, not bypassed", () => {
  // The source never calls the kernel's prompt or activation evaluators: no
  // prompt is fabricated to make a detection look authorised.
  assert.doesNotMatch(SOURCE.replace(/^\s*\/\/.*$/gm, ""), /evaluateActivationPrompt|activateMeetingMode|unwiredOnceOnlyPromptDecision/);
  assert.equal(meetingModeGaps().prompt_reachable_here, false);
  const projection = meetingProjection(fixtureFacts([]), ToolError);
  assert.deepEqual(projection.detection_prompt, {
    available: false,
    reason_id: "no_durable_prompt_ledger_owner_exists_to_prove_this_ledger",
    seam: V5_J201_PROMPT_LEDGER_OWNER_SEAM,
  });
  assert.equal(projection.recording, "denied");
  assert.equal(projection.records_audio, false);
  assert.equal(projection.d03_recording.available, false);
  assert.equal(projection.d03_recording.legacy_recorder_presence_is_evidence, false);
  assert.equal(projection.schema_version, MEETING_MODE_SCHEMA_VERSION);
});

function fixtureFacts(actions, { ended = false, lease = null } = {}) {
  return { ok: true,
    meeting: { id: randomUUID(), mode_state: ended ? "ended" : "active_non_recording", recording: "denied" },
    lease, notes: [], actions, stream: [], more: false };
}

const cmd = { verb: "add-loop", args: { kind: "task", owner: "joe" } };
const action = (n, state, command, extra = {}) => ({ action_number: n, state, current_revision: 1,
  revisions: [{ revision: 1, summary: `a${n}`, command }], ...extra });

test("the recap is deterministic: done, delegated, needs approval, unresolved", () => {
  const actions = [
    action(1, "executed", cmd), action(2, "delegated", cmd, { assignee: "dell" }),
    action(3, "proposed", cmd), action(4, "proposed", null), action(5, "accepted", cmd),
    action(6, "declined", cmd),
  ];
  const recap = meetingRecap(actions);
  assert.deepEqual(recap.done.map(a => a.action_number), [1]);
  assert.deepEqual(recap.delegated.map(a => a.action_number), [2]);
  assert.deepEqual(recap.needs_approval.map(a => a.action_number), [3]);
  assert.deepEqual(recap.unresolved.map(a => [a.action_number, a.reason_id]),
    [[4, "tentative_discussion_without_command"], [5, "accepted_effect_not_yet_observed"]]);
  assert.deepEqual(recap.closed_without_action.map(a => a.action_number), [6]);
  assert.deepEqual(meetingRecap(actions), recap, "same input, same recap");
  // An accepted action is never reported done: acceptance is a decision, not an effect.
  assert.equal(recap.done.some(a => a.state === "accepted"), false);

  const ended = meetingProjection(fixtureFacts(actions, { ended: true,
    lease: { live: false, released: true } }), ToolError);
  assert.deepEqual(ended.status, { recording: "never_started", processing_complete: true, review_complete: false });
  assert.deepEqual(ended.recap.counts,
    { done: 1, delegated: 1, needs_approval: 1, unresolved: 2, closed_without_action: 1 });
  const reviewed = meetingProjection(fixtureFacts([action(1, "executed", cmd)], { ended: true }), ToolError);
  assert.equal(reviewed.status.review_complete, true);
});

test("the projection refuses a shape it does not know, including any recording state but denied", () => {
  assert.throws(() => meetingProjection({ ok: false, reason_id: "meeting_not_found" }, ToolError),
    refusal("meeting_not_found"));
  const facts = fixtureFacts([]);
  facts.meeting.recording = "active";
  assert.throws(() => meetingProjection(facts, ToolError), refusal("meeting_recording_state_invalid"));
});

test("a command may point only at an existing ordinary CARR write verb", () => {
  assert.deepEqual(validateMeetingCommand(cmd, lookupTool, ToolError), cmd);
  assert.equal(validateMeetingCommand(undefined, lookupTool, ToolError), null);
  const humanOnly = Object.keys(TOOLS).find(n => TOOLS[n].humanOnly === true);
  const authorityOnly = Object.keys(TOOLS).find(n => TOOLS[n].authorityOnly === true);
  for (const verb of ["no-such-verb", "read-meeting", "start-meeting", "standing-context", humanOnly, authorityOnly]) {
    assert.throws(() => validateMeetingCommand({ verb, args: {} }, lookupTool, ToolError),
      refusal("meeting_command_not_eligible"), verb);
  }
  assert.throws(() => validateMeetingCommand({ verb: "add-loop", args: { idempotency_key: "x" } }, lookupTool, ToolError),
    refusal("meeting_command_idempotency_key_refused"));
  assert.throws(() => validateMeetingCommand({ verb: "add-loop", args: {}, extra: 1 }, lookupTool, ToolError),
    refusal("meeting_command_invalid"));
  assert.throws(() => validateMeetingCommand({ verb: "add-loop", args: { notes: "x".repeat(9000) } }, lookupTool, ToolError),
    refusal("meeting_command_too_large"));
});

test("UX05: same-key meeting writes serialize before their replay read in the real envelope", async () => {
  // Through the REAL tools.js envelope: the advisory lock must be the first
  // statement, so two simultaneous clicks with one key cannot both miss the
  // tool_call row and both run the body.
  const seen = [];
  const client = { query: async (text) => { seen.push(text); throw new Error("stop after first statement"); } };
  const key = randomUUID();
  await assert.rejects(() => TOOLS["end-meeting"].handler(client, { id: randomUUID(), slug: "joe", human: true },
    { idempotency_key: key, meeting_id: randomUUID(), client_instance: "mac-a" }), /stop after first statement/);
  assert.match(seen[0], /pg_advisory_xact_lock/);
});

test("scalar validation refuses before the store", async () => {
  const { tools } = harness();
  const client = { query: async () => assert.fail("must not query") };
  const key = randomUUID();
  await assert.rejects(() => tools["add-meeting-note"].handler(client, {}, { idempotency_key: "nope",
    meeting_id: key, body: "b", client_instance: "mac-a" }), refusal("idempotency_key_invalid"));
  await assert.rejects(() => tools["add-meeting-note"].handler(client, {}, { idempotency_key: key,
    meeting_id: key, body: "b", client_instance: "has space" }), refusal("client_instance_invalid"));
  await assert.rejects(() => tools["start-meeting"].handler(client, {}, { idempotency_key: key,
    title: "two\nlines", activation_intent: "one_tap_user_activation", client_instance: "mac-a",
    native_identity: { source_system: "zoom", native_id: "1", native_id_epoch: "e" } }), refusal("meeting_title_invalid"));
  await assert.rejects(() => tools["start-meeting"].handler(client, {}, { idempotency_key: key,
    title: "ok", activation_intent: "one_tap_user_activation", client_instance: "mac-a", platform: "zoom",
    native_identity: { source_system: "zoom", native_id: "bad id", native_id_epoch: "e" } }), refusal("native_identity_invalid"));
});

// ---------------------------------------------------------------------------
// The store, on a real PostgreSQL.
// ---------------------------------------------------------------------------

async function database(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN), "REFUSED: this proof writes meeting rows and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg, slug, { human = true, tenant = TENANT } = {}) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("select set_config('carr.acting_actor_slug',$1,false)", [slug]);
  await client.query("select set_config('carr.verified_human_actor_slug',$1,false)", [human ? slug : ""]);
  await client.query("select set_config('carr.organization_tenant_id',$1,false)", [tenant]);
  return client;
}

async function mintActors(pg) {
  const admin = new pg.Client({ connectionString: DSN });
  await admin.connect();
  const suffix = randomUUID().slice(0, 8);
  const slugs = { a: `b11-joe-${suffix}`, b: `b11-dell-${suffix}`, doc: `b11-doc-${suffix}` };
  for (const [slug, kind] of [[slugs.a, "human"], [slugs.b, "human"], [slugs.doc, "automation"]]) {
    await admin.query("insert into public.actor(slug, kind, display_name, active) values ($1,$2,$1,true)", [slug, kind]);
  }
  return { admin, slugs };
}

/** One verb call in its own transaction, as mcp.js runs it. */
async function call(tools, client, verb, args) {
  await client.query(tools[verb].write ? "begin" : "begin read only");
  try {
    const result = await tools[verb].handler(client, {}, args);
    await client.query("commit");
    return result;
  } catch (e) {
    await client.query("rollback");
    throw e;
  }
}

async function waitForLockWait(observer, client) {
  const pid = client.processID;
  for (let i = 0; i < 100; i += 1) {
    const r = await observer.query(
      "select wait_event_type from pg_stat_activity where pid=$1", [pid]);
    if (r.rows[0]?.wait_event_type === "Lock") return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.fail("the second device never blocked on the first device's lock");
}

function nativeIdentity() {
  return { source_system: "zoom", native_id: `8812-${randomUUID().slice(0, 8)}`, native_id_epoch: "2026-09-23T15" };
}

function startArgs(identity, title = "Pipeline review") {
  return { idempotency_key: randomUUID(), title, platform: "zoom", native_identity: identity,
    activation_intent: "one_tap_user_activation" };
}

test("DB UX05/UX13: two devices starting one meeting at the same moment converge on one row; a double click and a rejoin are the same meeting", async t => {
  const pg = await database(t); if (!pg) return;
  const { admin, slugs } = await mintActors(pg);
  const joe = await connect(pg, slugs.a);
  const dell = await connect(pg, slugs.b);
  const { tools } = harness();
  try {
    const identity = nativeIdentity();
    const aArgs = { ...(startArgs(identity)), client_instance: "joe-mac" };
    const bArgs = { ...(startArgs(identity)), client_instance: "dell-mac" };
    // Device A starts and has NOT committed; device B's start must block on
    // A's unique-index entry rather than create a second meeting.
    await joe.query("begin");
    const first = await tools["start-meeting"].handler(joe, {}, aArgs);
    await dell.query("begin");
    const secondPending = tools["start-meeting"].handler(dell, {}, bArgs);
    await waitForLockWait(admin, dell);
    await joe.query("commit");
    const second = await secondPending;
    await dell.query("commit");

    assert.equal(first.joined_existing, false);
    assert.equal(second.joined_existing, true);
    assert.equal(second.meeting_id, first.meeting_id);
    assert.equal(first.records_audio, false);
    const count = await admin.query(
      "select count(*)::int as n from ops.meeting where source_system=$1 and native_id=$2", [identity.source_system, identity.native_id]);
    assert.equal(count.rows[0].n, 1);

    // A double click replays A's own key: one row, one start entry.
    const replay = await call(tools, joe, "start-meeting", aArgs);
    assert.equal(replay.deduplicated, true);
    assert.equal(replay.meeting_id, first.meeting_id);
    // A reconnecting device with a new key rejoins the same meeting.
    const rejoin = await call(tools, dell, "start-meeting", { ...bArgs, idempotency_key: randomUUID() });
    assert.equal(rejoin.meeting_id, first.meeting_id);
    const kinds = await admin.query(
      "select kind, count(*)::int as n from ops.meeting_stream where meeting_id=$1 group by kind order by kind", [first.meeting_id]);
    assert.deepEqual(kinds.rows, [{ kind: "meeting_joined", n: 2 }, { kind: "meeting_started", n: 1 }]);

    // An automation actor cannot start one: activation is a partner's one-tap.
    const doc = await connect(pg, slugs.doc, { human: false });
    await assert.rejects(() => call(tools, doc, "start-meeting",
      { ...(startArgs(nativeIdentity())), client_instance: "doc" }),
      refusal("meeting_start_requires_verified_partner"));
    await doc.end();
  } finally {
    await joe.end(); await dell.end(); await admin.end();
  }
});

test("DB UX13: one processing owner; a second device rejoins as participant; takeover fences the old holder", async t => {
  const pg = await database(t); if (!pg) return;
  const { admin, slugs } = await mintActors(pg);
  const joe = await connect(pg, slugs.a);
  const dell = await connect(pg, slugs.b);
  const { tools } = harness();
  try {
    const meeting = await call(tools, joe, "start-meeting",
      { ...(startArgs(nativeIdentity())), client_instance: "joe-mac" });
    const claim = (client, instance, extra = {}) => call(tools, client, "claim-meeting-processing",
      { idempotency_key: randomUUID(), meeting_id: meeting.meeting_id, client_instance: instance, ...extra });

    const a = await claim(joe, "joe-mac");
    assert.equal(a.decision, "acquired"); assert.equal(a.lease.lease_epoch, 1); assert.equal(a.is_holder, true);
    const b = await claim(dell, "dell-mac");
    assert.equal(b.decision, "held_by_other"); assert.equal(b.is_holder, false);
    assert.equal(b.lease.holder, slugs.a);
    const renewed = await claim(joe, "joe-mac");
    assert.equal(renewed.decision, "renewed"); assert.equal(renewed.lease.lease_epoch, 1);
    // The same partner on a second device is still a second device.
    assert.equal((await claim(joe, "joe-ipad")).decision, "held_by_other");

    const propose = (client, instance, epoch, summary) => call(tools, client, "propose-meeting-action", {
      idempotency_key: randomUUID(), meeting_id: meeting.meeting_id, summary, basis: "tentative_discussion",
      client_instance: instance, processing_epoch: epoch });
    await assert.rejects(() => propose(dell, "dell-mac", 1, "not the owner"), refusal("stale_processing_lease"));
    assert.equal((await propose(joe, "joe-mac", 1, "owner contribution")).action.state, "proposed");

    // Expiry is the server's clock; the proof moves the row, not the rule.
    await admin.query("update ops.meeting_processing_lease set acquired_at=now()-interval '10 minutes', expires_at=now()-interval '1 second' where meeting_id=$1", [meeting.meeting_id]);
    const takeover = await claim(dell, "dell-mac");
    assert.equal(takeover.decision, "taken_over_after_expiry"); assert.equal(takeover.lease.lease_epoch, 2);
    await assert.rejects(() => propose(joe, "joe-mac", 1, "stale owner"), refusal("stale_processing_lease"));
    assert.equal((await propose(dell, "dell-mac", 2, "new owner")).action.state, "proposed");
    const leases = await admin.query("select count(*)::int as n from ops.meeting_processing_lease where meeting_id=$1", [meeting.meeting_id]);
    assert.equal(leases.rows[0].n, 1, "one lease row: one processing owner, never two workers");
    assert.equal((await claim(joe, "joe-mac", { release: true })).decision, "not_holder");
    assert.equal((await claim(dell, "dell-mac", { release: true })).decision, "released");
  } finally {
    await joe.end(); await dell.end(); await admin.end();
  }
});

test("DB UX13: notes and revisions are attributed and append-only; a stale revision is refused", async t => {
  const pg = await database(t); if (!pg) return;
  const { admin, slugs } = await mintActors(pg);
  const joe = await connect(pg, slugs.a);
  const dell = await connect(pg, slugs.b);
  const { tools } = harness();
  try {
    const meeting = await call(tools, joe, "start-meeting",
      { ...(startArgs(nativeIdentity())), client_instance: "joe-mac" });
    const note = (client, instance, body, extra = {}) => call(tools, client, "add-meeting-note",
      { idempotency_key: randomUUID(), meeting_id: meeting.meeting_id, body, client_instance: instance, ...extra });
    const first = await note(joe, "joe-mac", "Tenant wants 5,000 SF");
    assert.deepEqual([first.note_number, first.revision], [1, 1]);
    const revised = await note(dell, "dell-mac", "Tenant wants 5,500 SF", { revises_note_number: 1, base_revision: 1 });
    assert.deepEqual([revised.note_number, revised.revision], [1, 2]);
    await assert.rejects(() => note(joe, "joe-mac", "stale edit", { revises_note_number: 1, base_revision: 1 }),
      e => e.payload.error === "meeting_note_revision_conflict" && e.payload.current_revision === 2);
    const replayArgs = { idempotency_key: randomUUID(), meeting_id: meeting.meeting_id, body: "second", client_instance: "joe-mac" };
    const once = await call(tools, joe, "add-meeting-note", replayArgs);
    const twice = await call(tools, joe, "add-meeting-note", replayArgs);
    assert.equal(twice.deduplicated, true); assert.equal(twice.note_number, once.note_number);

    const read = await call(tools, dell, "read-meeting", { meeting_id: meeting.meeting_id });
    assert.deepEqual(read.notes.map(n => [n.note_number, n.revision, n.author]),
      [[1, 1, slugs.a], [1, 2, slugs.b], [2, 1, slugs.a]]);
    await assert.rejects(() => admin.query("update ops.meeting_note set body='rewritten' where meeting_id=$1", [meeting.meeting_id]), /append-only/);
    await assert.rejects(() => admin.query("delete from ops.meeting_stream where meeting_id=$1", [meeting.meeting_id]), /append-only/);
  } finally {
    await joe.end(); await dell.end(); await admin.end();
  }
});

test("DB UX13/UX05: tentative stays proposed, repeated contributions dedupe, and simultaneous acceptance resolves once", async t => {
  const pg = await database(t); if (!pg) return;
  const { admin, slugs } = await mintActors(pg);
  const joe = await connect(pg, slugs.a);
  const dell = await connect(pg, slugs.b);
  const doc = await connect(pg, slugs.doc, { human: false });
  const { tools } = harness();
  try {
    const meeting = await call(tools, joe, "start-meeting",
      { ...(startArgs(nativeIdentity())), client_instance: "joe-mac" });
    const lease = await call(tools, doc, "claim-meeting-processing",
      { idempotency_key: randomUUID(), meeting_id: meeting.meeting_id, client_instance: "doc-worker" });
    const fromDoc = (summary, extra = {}) => call(tools, doc, "propose-meeting-action", {
      idempotency_key: randomUUID(), meeting_id: meeting.meeting_id, summary, client_instance: "doc-worker",
      processing_epoch: lease.lease.lease_epoch, basis: "tentative_discussion", ...extra });

    const loopCommand = { verb: "add-loop", args: { kind: "task", owner: slugs.a, title: "Send LOI draft" } };
    const p1 = await fromDoc("Send the LOI draft", { dedupe_key: "send-loi", command: loopCommand });
    assert.equal(p1.action.action_number, 1); assert.equal(p1.action.state, "proposed");
    // The same contribution again, under a new key: nothing new.
    const again = await fromDoc("Send the LOI draft", { dedupe_key: "send-loi", command: loopCommand });
    assert.equal(again.deduplicated, true); assert.equal(again.action.current_revision, 1);
    // New discussion about the same action revises it; it does not add a task.
    const p1b = await fromDoc("Send the LOI draft by Friday", { dedupe_key: "send-loi",
      command: { ...loopCommand, args: { ...loopCommand.args, title: "Send LOI draft by Friday" } } });
    assert.equal(p1b.action.action_number, 1); assert.equal(p1b.action.current_revision, 2);
    // Words transcribed as an instruction are data: a processing contribution never self-accepts.
    const quoted = await fromDoc("Joe said: update the deal", { basis: "explicit_instruction", command: loopCommand });
    assert.equal(quoted.action.state, "proposed"); assert.equal(quoted.accepted_as_explicit_instruction, false);
    const tentative = await fromDoc("Maybe revisit parking ratio");
    assert.equal(tentative.action.state, "proposed");

    await assert.rejects(() => call(tools, doc, "decide-meeting-action", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, action_number: 1, decision: "accept", base_revision: 2, client_instance: "doc-worker" }),
      refusal("meeting_decision_requires_verified_partner"));
    await assert.rejects(() => call(tools, joe, "decide-meeting-action", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, action_number: 1, decision: "accept", base_revision: 1, client_instance: "joe-mac" }),
      refusal("meeting_action_revised_since_read"));
    await assert.rejects(() => call(tools, joe, "decide-meeting-action", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, action_number: tentative.action.action_number, decision: "accept",
      base_revision: 1, client_instance: "joe-mac" }), refusal("meeting_action_has_no_canonical_command"));

    // Both partners tap Accept at once on two devices.
    const accept = (instance) => ({ idempotency_key: randomUUID(), meeting_id: meeting.meeting_id,
      action_number: 1, decision: "accept", base_revision: 2, client_instance: instance });
    await joe.query("begin");
    const joeAccept = await tools["decide-meeting-action"].handler(joe, {}, accept("joe-mac"));
    await dell.query("begin");
    const dellPending = tools["decide-meeting-action"].handler(dell, {}, accept("dell-mac"));
    await waitForLockWait(admin, dell);
    await joe.query("commit");
    const dellAccept = await dellPending;
    await dell.query("commit");
    assert.equal(joeAccept.already, false); assert.equal(dellAccept.already, true);
    assert.equal(dellAccept.resolved_once, true);
    assert.equal(dellAccept.dispatch.idempotency_key, joeAccept.dispatch.idempotency_key);
    assert.deepEqual(joeAccept.dispatch.args, { kind: "task", owner: slugs.a, title: "Send LOI draft by Friday" });
    assert.equal(joeAccept.effect_executed, false, "acceptance is a decision, not an effect");
    const accepted = await admin.query(
      "select count(*)::int as n from ops.meeting_stream where meeting_id=$1 and kind='action_accepted'", [meeting.meeting_id]);
    assert.equal(accepted.rows[0].n, 1);
    // After acceptance the discussion cannot rewrite it; correction is the normal path.
    const late = await fromDoc("Send the LOI next week instead", { dedupe_key: "send-loi", command: loopCommand });
    assert.equal(late.already_resolved, true);
    await assert.rejects(() => call(tools, dell, "decide-meeting-action", { ...accept("dell-mac"), decision: "decline" }),
      refusal("meeting_action_already_accepted_requires_correction"));

    // A partner's own explicit instruction with a command is authorised at once.
    const direct = await call(tools, dell, "propose-meeting-action", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, summary: "Dell: log the tour", basis: "explicit_instruction",
      command: loopCommand, client_instance: "dell-mac" });
    assert.equal(direct.accepted_as_explicit_instruction, true); assert.equal(direct.action.state, "accepted");
    assert.equal(direct.action.decided_by, slugs.b);

    const read = await call(tools, joe, "read-meeting", { meeting_id: meeting.meeting_id });
    const byNumber = Object.fromEntries(read.actions.map(a => [a.action_number, a]));
    assert.deepEqual(byNumber[1].revisions.map(r => [r.revision, r.source, r.proposed_by]),
      [[1, "processing", slugs.doc], [2, "processing", slugs.doc]]);
    assert.equal(read.actions.length, 4, "the repeated contribution added no action");
  } finally {
    await joe.end(); await dell.end(); await doc.end(); await admin.end();
  }
});

test("DB UX05: disconnect after dispatch reconciles against the canonical ledger before any retry", async t => {
  const pg = await database(t); if (!pg) return;
  const { admin, slugs } = await mintActors(pg);
  const joe = await connect(pg, slugs.a);
  const dell = await connect(pg, slugs.b);
  const { tools } = harness();
  try {
    const meeting = await call(tools, joe, "start-meeting",
      { ...(startArgs(nativeIdentity())), client_instance: "joe-mac" });
    const proposeAccepted = async (summary, command, decideExtra = {}) => {
      const p = await call(tools, dell, "propose-meeting-action", { idempotency_key: randomUUID(),
        meeting_id: meeting.meeting_id, summary, basis: "tentative_discussion", command, client_instance: "dell-mac" });
      return call(tools, joe, "decide-meeting-action", { idempotency_key: randomUUID(),
        meeting_id: meeting.meeting_id, action_number: p.action.action_number, decision: "accept",
        base_revision: 1, client_instance: "joe-mac", ...decideExtra });
    };
    const reconcile = (n, client = joe) => call(tools, client, "record-meeting-action-outcome",
      { idempotency_key: randomUUID(), meeting_id: meeting.meeting_id, action_number: n, client_instance: "joe-mac" });
    // What the canonical verb's envelope commits when it succeeds: one
    // public.tool_call row under the idempotency key it was called with.
    const commitCanonical = (key, verb) => admin.query(
      `insert into public.tool_call(idempotency_key, verb, actor_id, request_hash, response, organization_tenant_id)
       values ($1,$2,(select id from public.actor where slug=$3),'b11-proof','{"ok":true}'::jsonb,$4)`,
      [key, verb, slugs.a, TENANT]);

    const command = { verb: "add-loop", args: { kind: "task", owner: slugs.a, title: "Order survey" } };
    const decided = await proposeAccepted("Order the survey", command);
    const key = decided.dispatch.idempotency_key;
    assert.equal(decided.action.state, "accepted");

    // The device dispatched and dropped before it saw an answer. Reconcile first.
    const unknown = await reconcile(decided.action.action_number);
    assert.equal(unknown.reconciled, false); assert.equal(unknown.outcome_state, "not_observed");
    assert.equal(unknown.retry.idempotency_key, key, "a retry reuses the SAME key; it never mints a new one");
    assert.equal(unknown.retry.verb, "add-loop");
    let read = await call(tools, joe, "read-meeting", { meeting_id: meeting.meeting_id });
    assert.deepEqual(read.recap.unresolved.map(a => a.reason_id), ["accepted_effect_not_yet_observed"]);
    assert.equal(read.recap.done.length, 0, "not observed is never done");

    await commitCanonical(key, "add-loop");
    const done = await reconcile(decided.action.action_number, dell);
    assert.equal(done.reconciled, true); assert.equal(done.outcome_state, "executed");
    assert.equal(done.action.outcome.evidence, "public.tool_call");
    assert.equal(done.action.outcome.idempotency_key, key);
    assert.equal((await reconcile(decided.action.action_number)).already, true);

    // Delegation is a handoff through a canonical record, reconciled the same way.
    const delegated = await proposeAccepted("Dell to call the landlord", command,
      { disposition: "delegate", assignee_slug: slugs.b });
    assert.equal(delegated.action.assignee, slugs.b);
    await commitCanonical(delegated.dispatch.idempotency_key, "add-loop");
    assert.equal((await reconcile(delegated.action.action_number)).outcome_state, "delegated");

    // A key found under a different verb is not this action's effect.
    const mismatched = await proposeAccepted("Mismatch", command);
    await commitCanonical(mismatched.dispatch.idempotency_key, "log-activity");
    await assert.rejects(() => reconcile(mismatched.action.action_number), refusal("meeting_operation_key_bound_elsewhere"));

    read = await call(tools, dell, "read-meeting", { meeting_id: meeting.meeting_id });
    assert.deepEqual(read.recap.done.map(a => a.action_number), [decided.action.action_number]);
    assert.deepEqual(read.recap.delegated.map(a => [a.action_number, a.assignee]),
      [[delegated.action.action_number, slugs.b]]);
  } finally {
    await joe.end(); await dell.end(); await admin.end();
  }
});

test("DB: ending the meeting ends processing, keeps review open, and the stream resumes a reconnecting device", async t => {
  const pg = await database(t); if (!pg) return;
  const { admin, slugs } = await mintActors(pg);
  const joe = await connect(pg, slugs.a);
  const dell = await connect(pg, slugs.b);
  const doc = await connect(pg, slugs.doc, { human: false });
  const { tools } = harness();
  try {
    const meeting = await call(tools, joe, "start-meeting",
      { ...(startArgs(nativeIdentity())), client_instance: "joe-mac" });
    await call(tools, doc, "claim-meeting-processing",
      { idempotency_key: randomUUID(), meeting_id: meeting.meeting_id, client_instance: "doc-worker" });
    const seen = (await call(tools, dell, "read-meeting", { meeting_id: meeting.meeting_id })).meeting.last_seq;
    // Dell's device drops; Joe keeps working.
    await call(tools, joe, "add-meeting-note", { idempotency_key: randomUUID(), meeting_id: meeting.meeting_id,
      body: "While Dell was offline", client_instance: "joe-mac" });
    const proposal = await call(tools, joe, "propose-meeting-action", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, summary: "Draft counter", basis: "tentative_discussion",
      command: { verb: "add-loop", args: { kind: "task", owner: slugs.a } }, client_instance: "joe-mac" });
    const resumed = await call(tools, dell, "read-meeting", { meeting_id: meeting.meeting_id, after_seq: seen });
    assert.deepEqual(resumed.stream.map(s => s.kind), ["note_added", "action_proposed"]);
    assert.deepEqual(resumed.stream.map(s => s.seq), [seen + 1, seen + 2]);

    await assert.rejects(() => call(tools, doc, "end-meeting", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, client_instance: "doc-worker" }), refusal("meeting_end_requires_verified_partner"));
    const ended = await call(tools, dell, "end-meeting", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, client_instance: "dell-mac" });
    assert.equal(ended.already, false);
    await assert.rejects(() => call(tools, joe, "add-meeting-note", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, body: "late", client_instance: "joe-mac" }), refusal("meeting_ended"));
    await assert.rejects(() => call(tools, doc, "claim-meeting-processing", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, client_instance: "doc-worker" }), refusal("meeting_ended"));
    const again = await call(tools, joe, "start-meeting", { ...(startArgs({ source_system: "zoom",
      native_id: (await admin.query("select native_id from ops.meeting where id=$1", [meeting.meeting_id])).rows[0].native_id,
      native_id_epoch: "2026-09-23T15" })), client_instance: "joe-mac" });
    assert.equal(again.meeting_id, meeting.meeting_id); assert.equal(again.mode_state, "ended");

    let read = await call(tools, joe, "read-meeting", { meeting_id: meeting.meeting_id });
    assert.deepEqual(read.status, { recording: "never_started", processing_complete: true, review_complete: false });
    assert.equal(read.meeting.recording, "denied");
    assert.equal(read.lease.live, false);
    // Review continues after the meeting: the pending action is still decidable.
    await call(tools, joe, "decide-meeting-action", { idempotency_key: randomUUID(), meeting_id: meeting.meeting_id,
      action_number: proposal.action.action_number, decision: "decline", base_revision: 1, client_instance: "joe-mac" });
    read = await call(tools, joe, "read-meeting", { meeting_id: meeting.meeting_id });
    assert.equal(read.status.review_complete, true);
    assert.deepEqual(read.recap.counts, { done: 0, delegated: 0, needs_approval: 0, unresolved: 0, closed_without_action: 1 });
    const seqs = (await admin.query("select seq from ops.meeting_stream where meeting_id=$1 order by seq", [meeting.meeting_id])).rows.map(r => Number(r.seq));
    assert.deepEqual(seqs, seqs.map((_, i) => i + 1), "the stream is gapless");
    await assert.rejects(() => admin.query("update ops.meeting set recording='captured' where id=$1", [meeting.meeting_id]), /check/i);
  } finally {
    await joe.end(); await dell.end(); await doc.end(); await admin.end();
  }
});

test("DB: another tenant sees nothing, and the doors are granted to the writer bundle only", async t => {
  const pg = await database(t); if (!pg) return;
  const { admin, slugs } = await mintActors(pg);
  const joe = await connect(pg, slugs.a);
  const outsider = await connect(pg, slugs.b, { tenant: "other-tenant" });
  const { tools } = harness();
  try {
    const meeting = await call(tools, joe, "start-meeting",
      { ...(startArgs(nativeIdentity())), client_instance: "joe-mac" });
    await assert.rejects(() => call(tools, outsider, "read-meeting", { meeting_id: meeting.meeting_id }), refusal("meeting_not_found"));
    await assert.rejects(() => call(tools, outsider, "add-meeting-note", { idempotency_key: randomUUID(),
      meeting_id: meeting.meeting_id, body: "x", client_instance: "x" }), refusal("meeting_not_found"));
    const doors = ["ops.start_meeting(text,text,text,text,text,text,text,uuid)",
      "ops.claim_meeting_processing(uuid,text,boolean,uuid)", "ops.add_meeting_note(uuid,text,integer,integer,text,uuid)",
      "ops.propose_meeting_action(uuid,text,jsonb,text,text,integer,integer,bigint,text,uuid)",
      "ops.decide_meeting_action(uuid,integer,text,integer,text,text,text,uuid)",
      "ops.record_meeting_action_outcome(uuid,integer,text,uuid)", "ops.end_meeting(uuid,text,uuid)",
      "ops.meeting_facts(uuid,bigint,integer)"];
    for (const door of doors) {
      const r = await admin.query(`select has_function_privilege('carr_writer',$1,'execute') as w,
        has_function_privilege('carr_reader',$1,'execute') as r, has_function_privilege('carr_authority',$1,'execute') as a,
        (select prosecdef from pg_proc where oid=$1::regprocedure) as definer`, [door]);
      assert.deepEqual(r.rows[0], { w: true, r: false, a: false, definer: true }, door);
    }
    // Effective privilege, not the grant listing: the listing also names the
    // table owner (carr_ci in this lane), which is not a runtime role. Login
    // roles are checked alongside the bundles because a grant reaches them by
    // membership.
    const tables = await admin.query(`select count(distinct c.oid)::int as tables,
        count(*) filter (where has_table_privilege(r.rolname, c.oid, p))::int as n
      from pg_class c
      cross join (select rolname from pg_roles where rolname in
        ('carr_reader','carr_writer','carr_jobs','carr_authority','app_reader','app_writer')) r
      cross join unnest(array['select','insert','update','delete','truncate']) p
      where c.relnamespace='ops'::regnamespace and c.relname like 'meeting%' and c.relkind='r'`);
    assert.equal(tables.rows[0].tables, 6, "the six meeting tables are the ones checked");
    assert.equal(tables.rows[0].n, 0, "no runtime role may touch a meeting row directly");
  } finally {
    await joe.end(); await outsider.end(); await admin.end();
  }
});
