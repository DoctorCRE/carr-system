// WR-000119 — the dispatch spine's acceptance proofs, ONE BLOCK PER CRITERION
// on the WR-000119 card: AC-DS-LINK, AC-DS-ACK, AC-DS-HISTORY, AC-DS-REGISTRY.
//
// EVERY IDENTITY CASE MOVES THE DERIVED ACTOR. Neither verb takes an actor
// argument, so the ONLY way to prove the derivation is to change the
// transaction context and issue the SAME call again. A case that mints one
// actor and asserts one answer passes under an implementation that ignores the
// derivation entirely, and there is not one in this file.
//
// EVERY FIXTURE TIMESTAMP IS RELATIVE TO now(). The transaction clock is
// constant inside a statement, so a hard-coded instant proves nothing about
// ordering and fails on one run months from now.
//
// The store cases skip in the unit class; the migration class supplies
// DATABASE_URL and sets CARR_DISPATCH_SPINE_DB_REQUIRED=1, which turns a skip
// into a failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { dispatchSpineTools, dispatchLinkProjection, dispatchAckProjection }
  from "../src/dispatch-spine.js";
import { sessionDispatchProjection } from "../src/session-identity.js";
import { TOOLS } from "../src/tools.js";

// The probe below runs python3 as a child process. The node class runs it from
// mcp-server/, the migration class from the repository root, so a cwd-relative
// path to the hook resolves in one lane and not the other. Anchor it to THIS
// file instead of to whatever directory the runner happened to start in.
const GATE_PATH = fileURLToPath(
  new URL("../../hooks/completion-evidence-gate.py", import.meta.url));

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_DISPATCH_SPINE_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

const noEnvelope = (_c, _a, _v, _args, fn) => fn();
const noEvent = async () => {};
const tools = dispatchSpineTools({
  withEnvelope: noEnvelope, writeEvent: noEvent, ToolError });

async function skipUnlessDatabase(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof appends wire turns and link rows and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

const wrap = client => ({ query: (text, values = []) => client.query(text, values) });
const actorOf = client => slug =>
  client.query("select set_config('carr.acting_actor_slug',$1,true)", [slug]);

/**
 * ONE fixture, rolled back whole: three dispatches to one session --
 *   alpha  linked AND acknowledged
 *   beta   linked, NO ack row
 *   gamma  no link at all (pre-spine history)
 * -- plus a second actor, because every identity assertion below is about a
 * derivation. The capability book's BEFORE triggers refuse a row created in
 * any state but claimed, so THAT seed alone runs with the session's
 * replication role relaxed and it is restored before a single spine call.
 */
async function seeded(pg, fn) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  const tag = randomUUID().slice(0, 8);
  const session = `wr119-${tag}`;
  const intruder = `wr119-${tag}-intruder`;
  const ids = {
    session, intruder,
    turnAlpha: randomUUID(), turnBeta: randomUUID(), turnGamma: randomUUID(),
    refAlpha: randomUUID(), refBeta: randomUUID(), refAbsent: randomUUID(),
  };
  try {
    await client.query("begin");
    await client.query(
      `insert into public.actor(slug, kind, display_name, active)
       values ($1,'automation','WR119 intruder',true)`, [intruder]);
    await client.query(
      `insert into public.partner_room_turn(room_id, sponsor, seat, kind, body, msg_id,
         origin_channel, origin_actor, at)
       values ('partner-line','joe','hermes','turn',$1,$4,'mcp','hermes-pilot', now() - interval '9 minutes'),
              ('partner-line','joe','hermes','turn',$2,$5,'mcp','hermes-pilot', now() - interval '6 minutes'),
              ('partner-line','joe','hermes','turn',$3,$6,'mcp','hermes-pilot', now() - interval '3 minutes')`,
      [`dispatch alpha for ${session}`, `dispatch beta for ${session}`,
        `dispatch gamma for ${session}`, ids.turnAlpha, ids.turnBeta, ids.turnGamma]);
    await actorOf(client)("hermes-pilot");
    await client.query(
      "select ops.record_dispatch_link($1::uuid,$2::text,null,$3::uuid)",
      [ids.turnAlpha, session, ids.refAlpha]);
    await client.query(
      "select ops.record_dispatch_link($1::uuid,$2::text,null,$3::uuid)",
      [ids.turnBeta, session, ids.refBeta]);
    await client.query(
      "select ops.acknowledge_dispatch($1::uuid,'received','desk alpha log offset 4096')",
      [ids.refAlpha]);
    await client.query(
      "select ops.acknowledge_dispatch($1::uuid,'acknowledged','took the turn up')",
      [ids.refAlpha]);
    return await fn(client, ids);
  } finally {
    await client.query("rollback").catch(() => {});
    await client.end().catch(() => {});
  }
}

const historyFor = async (client, session) => {
  const r = await client.query(
    "select ops.session_dispatch_history($1::text,null,50) as facts", [session]);
  return sessionDispatchProjection(r.rows[0].facts, ToolError);
};
const sentFor = (answer, ref) =>
  answer.events.find(e => e.stage === "sent" && e.dispatch_ref === ref);

// ===========================================================================
// AC-DS-LINK — record-dispatch-link refuses any identity other than the
// server-derived hermes-pilot, requires a turn that exists and a session_id,
// takes an optional work_request_id, mints a dispatch_ref and is idempotent on
// the assignment. One link row per assignment; none for an unassigned turn.
// ===========================================================================

test("AC-DS-LINK: the verb declares a write on the writer connection and names no actor", () => {
  for (const name of ["record-dispatch-link", "acknowledge-dispatch"]) {
    const tool = TOOLS[name];
    assert.ok(tool, `${name} is not registered`);
    // The OPPOSITE of WR-000117's pair: without write:true the server opens a
    // read-only transaction and the insert fails at runtime.
    assert.equal(tool.write, true, `${name} does not declare write`);
    assert.equal(tool.writerConnection, true, `${name} does not declare the writer connection`);
    assert.notEqual(tool.authorityOnly, true, `${name} is authority-only`);
    assert.equal(tool.inputSchema.additionalProperties, false,
      `${name} leaves additionalProperties open`);
    for (const forbidden of ["actor", "actor_slug", "by_actor", "sponsor", "tenant"]) {
      assert.equal(forbidden in tool.inputSchema.properties, false,
        `${name} lets a caller name ${forbidden}`);
    }
  }
});

test("AC-DS-LINK: only the derived hermes-pilot identity may mint a link", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    // SAME CALL, SAME ARGUMENTS, DIFFERENT DERIVED ACTOR.
    await actorOf(client)(ids.intruder);
    await assert.rejects(
      tools["record-dispatch-link"].handler(wrap(client), {}, {
        dispatch_ref: randomUUID(), turn_msg_id: ids.turnGamma, session_id: ids.session }),
      e => e.error === "dispatch_link_hermes_pilot_only");
    const after = await client.query(
      "select count(*)::int as n from public.room_dispatch_link where session_id = $1",
      [ids.session]);
    assert.equal(after.rows[0].n, 2, "the refused mint still wrote a row");
    await actorOf(client)("hermes-pilot");
  });
});

test("AC-DS-LINK: one link per assignment, and none for a turn never assigned", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    const retry = await tools["record-dispatch-link"].handler(wrap(client), {}, {
      dispatch_ref: randomUUID(), turn_msg_id: ids.turnAlpha, session_id: ids.session });
    assert.equal(retry.deduplicated, true, "a retried assignment minted a second link");
    assert.equal(retry.dispatch_ref, ids.refAlpha);
    const counted = await client.query(
      `select count(*)::int as n from public.room_dispatch_link l
         join public.partner_room_turn t on t.id = l.turn_id
        where t.msg_id = $1`, [ids.turnGamma]);
    assert.equal(counted.rows[0].n, 0, "an unassigned turn carries a link row");
  });
});

test("AC-DS-LINK: an optional work_request_id is stored, read back, and validated", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    const wr = randomUUID();
    const ref = randomUUID();
    const minted = await tools["record-dispatch-link"].handler(wrap(client), {}, {
      dispatch_ref: ref, turn_msg_id: ids.turnGamma, session_id: ids.session,
      work_request_id: wr });
    assert.equal(minted.deduplicated, false, "a fresh assignment deduplicated");
    assert.equal(minted.dispatch_ref, ref);
    // STORED: the column carries the value the caller named.
    const stored = await client.query(
      `select work_request_id::text as wr from public.room_dispatch_link
        where dispatch_ref = $1`, [ref]);
    assert.equal(stored.rows[0].wr, wr, "the work_request_id was not stored");
    // The fixture's own links were minted WITHOUT one and are still null, so
    // this asserts the VALUE and not merely that the column is populated.
    const omitted = await client.query(
      `select work_request_id from public.room_dispatch_link
        where dispatch_ref = $1`, [ids.refAlpha]);
    assert.equal(omitted.rows[0].work_request_id, null,
      "an omitted work_request_id was invented");
    // READ BACK: the history projects it per dispatch as work_request_ref.
    const answer = await historyFor(client, ids.session);
    assert.equal(sentFor(answer, ref).work_request_ref, wr,
      "the stored work_request_id is not read back on its own sent row");
    assert.equal(sentFor(answer, ids.refAlpha).work_request_ref, null,
      "one dispatch's work_request_id leaked onto another");
    // VALIDATED in the handler, so a non-uuid never reaches the definer.
    await assert.rejects(
      tools["record-dispatch-link"].handler(wrap(client), {}, {
        dispatch_ref: randomUUID(), turn_msg_id: ids.turnGamma,
        session_id: ids.session, work_request_id: "WR-000119" }),
      e => e.error === "dispatch_work_request_invalid");
  });
});

test("AC-DS-LINK: a turn that does not exist is refused, and so is an empty session", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    await assert.rejects(
      tools["record-dispatch-link"].handler(wrap(client), {}, {
        dispatch_ref: randomUUID(), turn_msg_id: randomUUID(), session_id: ids.session }),
      e => e.error === "dispatch_turn_not_found");
    await assert.rejects(
      tools["record-dispatch-link"].handler(wrap(client), {}, {
        dispatch_ref: randomUUID(), turn_msg_id: ids.turnGamma, session_id: "   " }),
      e => e.error === "session_id_required");
  });
});

// ===========================================================================
// AC-DS-ACK — acknowledge-dispatch takes a dispatch_ref and a stage, derives
// the actor with no actor argument, appends ONE row per (dispatch_ref, stage)
// and refuses a second BY NAME; it never writes partner_room_turn or
// room_dispatch_link. A link with no ack reads back received null with reason
// not_acknowledged, DISTINCT from no_dispatch_spine, and never as failed.
// ===========================================================================

test("AC-DS-ACK: the ack stamps the derived caller and a restatement is one row", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    // The SAME ack from a DIFFERENT derived identity: by_actor follows the
    // context, which is what makes the evidence first-hand.
    await actorOf(client)(ids.intruder);
    const beta = await tools["acknowledge-dispatch"].handler(wrap(client), {}, {
      dispatch_ref: ids.refBeta, stage: "received", evidence: "desk beta log offset 512" });
    assert.equal(beta.deduplicated, false);
    assert.equal(beta.by_actor, ids.intruder, "the ack did not stamp its own caller");
    const again = await tools["acknowledge-dispatch"].handler(wrap(client), {}, {
      dispatch_ref: ids.refBeta, stage: "received", evidence: "desk beta log offset 512" });
    assert.equal(again.deduplicated, true, "a restated ack was appended twice");
    assert.equal(again.ack_id, beta.ack_id);
    const rows = await client.query(
      "select count(*)::int as n from public.room_dispatch_ack where dispatch_ref = $1 and stage = 'received'",
      [ids.refBeta]);
    assert.equal(rows.rows[0].n, 1);
    await actorOf(client)("hermes-pilot");
  });
});

test("AC-DS-ACK: an ack for a dispatch with no link is refused by name", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    await assert.rejects(
      tools["acknowledge-dispatch"].handler(wrap(client), {}, {
        dispatch_ref: ids.refAbsent, stage: "received" }),
      e => e.error === "dispatch_link_not_found");
  });
});

test("AC-DS-ACK: the wire and the link relation are never written by an ack", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    const before = await client.query(
      `select (select count(*) from public.partner_room_turn) as turns,
              (select count(*) from public.room_dispatch_link) as links`);
    await tools["acknowledge-dispatch"].handler(wrap(client), {}, {
      dispatch_ref: ids.refBeta, stage: "received", evidence: "desk beta log offset 512" });
    const after = await client.query(
      `select (select count(*) from public.partner_room_turn) as turns,
              (select count(*) from public.room_dispatch_link) as links`);
    assert.deepEqual(after.rows[0], before.rows[0],
      "an acknowledgement moved the wire or the link relation");
  });
});

// THE CENTRE CASE. Two nulls that must never collapse, asserted in ONE answer
// and asserted DIFFERENT -- a constant reason could only ever be one of them.
test("AC-DS-ACK: not_acknowledged and no_dispatch_spine are different nulls", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    const answer = await historyFor(client, ids.session);
    const beta = sentFor(answer, ids.refBeta);
    const gamma = answer.events.find(
      e => e.stage === "sent" && e.link_source === "body_match");

    assert.ok(beta, "the linked, unacknowledged dispatch is missing");
    assert.equal(beta.link_source, "proved");
    assert.equal(beta.stage_unavailable_reason, "not_acknowledged");

    assert.ok(gamma, "the pre-spine dispatch is missing");
    assert.equal(gamma.dispatch_ref, null);
    assert.equal(gamma.stage_unavailable_reason, "no_dispatch_spine");

    assert.notEqual(beta.stage_unavailable_reason, gamma.stage_unavailable_reason,
      "the two nulls collapsed into one reason");

    // A SILENT DESK IS NEVER RENDERED AS received, acknowledged OR failed.
    const betaStages = answer.events
      .filter(e => e.dispatch_ref === ids.refBeta).map(e => e.stage);
    assert.deepEqual(betaStages, ["sent"], `a silent desk produced ${betaStages}`);
  });
});

// ===========================================================================
// AC-DS-HISTORY — sent from the link join (link_source proved) or from the
// body match for pre-spine history (link_source body_match); received and
// acknowledged from ack rows carrying the ack row id as evidence; acted
// unchanged; stage_unavailable_reason PER DISPATCH. The inputSchema does not
// change.
// ===========================================================================

test("AC-DS-HISTORY: read-dispatch-history's inputSchema did not change", () => {
  // The contract WR-000117 shipped, restated here in full. A schema_digest that
  // moves is a contract change, and this Work Request makes none.
  assert.deepEqual(TOOLS["read-dispatch-history"].inputSchema, {
    type: "object", additionalProperties: false, properties: {
      session_id: { type: "string", minLength: 1, maxLength: 200 },
      cursor: { type: "string", minLength: 1, maxLength: 500 },
      limit: { type: "integer", minimum: 1, maximum: 100 },
    }, required: ["session_id"],
  });
});

test("AC-DS-HISTORY: a proved dispatch is distinguishable from an inferred one", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    const answer = await historyFor(client, ids.session);
    const alpha = sentFor(answer, ids.refAlpha);
    assert.equal(alpha.link_source, "proved");
    assert.match(alpha.stage_evidence, /linked by public\.room_dispatch_link id \d+/);
    // The reason is NULL on a proved-and-acknowledged dispatch: there is no
    // silence left to explain.
    assert.equal(alpha.stage_unavailable_reason, null);

    const gamma = answer.events.find(e => e.link_source === "body_match");
    assert.equal(gamma.stage, "sent");
    assert.doesNotMatch(gamma.stage_evidence, /room_dispatch_link/,
      "a body match claimed a link row");
  });
});

test("AC-DS-HISTORY: received and acknowledged carry the ack row that proves them", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    const answer = await historyFor(client, ids.session);
    for (const stage of ["received", "acknowledged"]) {
      const row = answer.events.find(
        e => e.stage === stage && e.dispatch_ref === ids.refAlpha);
      assert.ok(row, `the ${stage} stage is missing`);
      assert.match(row.stage_evidence,
        new RegExp(`^public\\.room_dispatch_ack id \\d+ for dispatch_ref ${ids.refAlpha}$`));
      assert.equal(row.stage_unavailable_reason, null);
    }
    // The top-level values are READ OFF THE NEWEST PROVED ROW, not constants.
    assert.notEqual(answer.received, null, "received stayed null with an ack row present");
    assert.notEqual(answer.acknowledged, null, "acknowledged stayed null with an ack row present");
  });
});

test("AC-DS-HISTORY: every sent row carries its own reason, not one over the answer", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async (client, ids) => {
    const answer = await historyFor(client, ids.session);
    const reasons = answer.events.filter(e => e.stage === "sent")
      .map(e => e.stage_unavailable_reason).sort();
    // Three dispatches, THREE different situations, in ONE answer.
    assert.deepEqual(reasons, [null, "no_dispatch_spine", "not_acknowledged"].sort(),
      `the per-dispatch reason collapsed: ${JSON.stringify(reasons)}`);
  });
});

test("AC-DS-HISTORY: the shaper passes the per-dispatch fields through untouched", () => {
  const shaped = sessionDispatchProjection({
    ok: true, session_id: "s", received: null, acknowledged: null,
    stage_unavailable_reason: "not_acknowledged",
    events: [{ event_id: "turn:1", at: "t", stage: "sent", stage_evidence: "e",
      link_source: "proved", dispatch_ref: "d", stage_unavailable_reason: "not_acknowledged" }],
  }, ToolError);
  assert.equal(shaped.events[0].link_source, "proved");
  assert.equal(shaped.events[0].dispatch_ref, "d");
  assert.equal(shaped.events[0].stage_unavailable_reason, "not_acknowledged");
});

// ===========================================================================
// AC-DS-REGISTRY — both verbs classify as WRITES in the completion-evidence
// gate. The registry half of this criterion (0532, v35, v34 preserved,
// relation_dml moved by exactly the two new relations' grants) is proved in
// mcp-server/test/siep-11-mutation-registry.test.mjs and in 0532's own
// preflight; what belongs here is the classification, executed rather than
// read, because `acknowledge` is deliberately NOT a write prefix.
// ===========================================================================

test("AC-DS-REGISTRY: both verbs classify as writes, and acknowledge is not a prefix", () => {
  const probe = [
    "import importlib.util, pathlib, json",
    `spec = importlib.util.spec_from_file_location('gate', ${JSON.stringify(GATE_PATH)})`,
    "gate = importlib.util.module_from_spec(spec)",
    "spec.loader.exec_module(gate)",
    "print(json.dumps({",
    "  'record': gate.is_write_action('record-dispatch-link'),",
    "  'ack': gate.is_write_action('acknowledge-dispatch'),",
    "  'notification': gate.is_write_action('acknowledge-notification'),",
    "  'prefix': 'acknowledge' in gate.WRITE_ACTION_PREFIXES,",
    "  'exact': sorted(a for a in gate.WRITE_ACTION_EXACT if a.startswith('acknowledge')),",
    "}))",
  ].join("\n");
  const out = JSON.parse(execFileSync("python3", ["-c", probe], { encoding: "utf8" }));
  assert.equal(out.record, true, "record-dispatch-link does not classify as a write");
  assert.equal(out.ack, true, "acknowledge-dispatch does not classify as a write");
  assert.equal(out.notification, true, "the sibling exact entry was disturbed");
  // A PREFIX WOULD SILENTLY CAPTURE A FUTURE READ named the same way. Every
  // acknowledge write is listed exactly, including board answer receipts.
  assert.equal(out.prefix, false, "acknowledge was promoted to a write prefix");
  assert.deepEqual(out.exact, ["acknowledge-board-answer", "acknowledge-dispatch", "acknowledge-notification"]);
});

test("AC-DS-REGISTRY: a refused write is surfaced as its reason and never as a success", () => {
  assert.throws(() => dispatchLinkProjection(
    { ok: false, reason_id: "dispatch_link_hermes_pilot_only" }, ToolError),
  e => e.error === "dispatch_link_hermes_pilot_only");
  assert.throws(() => dispatchAckProjection(
    { ok: false, reason_id: "dispatch_link_not_found" }, ToolError),
  e => e.error === "dispatch_link_not_found");
  // A shape that is not an answer at all is still a refusal, never an ok.
  assert.throws(() => dispatchLinkProjection(null, ToolError),
    e => e.error === "dispatch_link_refused");
  assert.throws(() => dispatchAckProjection(undefined, ToolError),
    e => e.error === "dispatch_ack_refused");
});
