// partner-room.test.mjs — the shared room's contract, written BEFORE the verbs
// (rule e65efc68). The room is Idea 78's spectator surface: an append-only turn
// log two brains write and a human watches, served by the Worker so both Macs
// read one transcript.
//
// The contract under test, in one breath: a turn lands verbatim, attributed to
// the VERIFIED partner (server-derived from the credential, never from an
// argument), only from a credential a partner actually sponsors; replays and
// cross-transport duplicates land exactly once; and the read side is a plain
// cursor over the view, oldest first.

import test from "node:test";
import assert from "node:assert/strict";

import { readFile } from "node:fs/promises";
import { TOOLS } from "../src/tools.js";
import { readRoomQueue, queueProjectionEventFromTurn, queueProjectionHealthFromTurn } from "../src/partner-room.js";

// ── actors ──────────────────────────────────────────────────────────────────
// Shapes mirror identity.js: a direct human partner, the two sponsored local
// machine doors, and a locked reviewer machine identity (registered in
// SERVER_MACHINE_IDENTITIES with marker `review` via `review-token`).
const joe = { id: "actor-joe", slug: "joe", human: true, via: "oauth-google" };
const dellLocal = { id: "actor-dell-local", slug: "dell-local", human: false,
  via: "local-token", agent: true, sponsoring_human_slug: "dell" };
const reviewer = { id: "actor-reviewer", slug: "codex-reviewer", human: false,
  via: "review-token", review: true };
const hermes = { id: "actor-hermes", slug: "hermes-pilot", human: false,
  via: "hermes-token", hermes: true, sponsoring_human_slug: "joe" };

// ── fake client ─────────────────────────────────────────────────────────────
// House style: pattern-match on SQL prefixes, track state as fields. It must
// also answer withEnvelope's tool_call queries; `toolCalls` doubles as the
// replay store so the same fake proves the replay path.
class RoomFake {
  constructor({ rows = [], msgIdTaken = null } = {}) {
    this.rows = rows;                 // pre-existing turns, for reads + dedup
    this.msgIdTaken = msgIdTaken;     // a msg_id that already exists
    this.inserted = [];               // insert params captured
    this.toolCalls = new Map();       // idempotency_key -> {request_hash, response}
    this.reads = [];                  // read params captured
    this.nextId = 100;
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.toolCalls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null });
      return { rows: [] };
    }
    if (sql.startsWith("insert into partner_room_turn")) {
      const msgId = params[5];
      if (this.msgIdTaken && msgId === this.msgIdTaken) return { rows: [] }; // on conflict do nothing
      this.inserted.push(params);
      return { rows: [{ id: this.nextId++, at: "2026-08-20T01:00:00+00:00" }] };
    }
    if (sql.startsWith("select id, room_id, sponsor, seat") && sql.includes("from partner_room_turn where msg_id")) {
      const hit = this.rows.find((r) => r.msg_id === params[0]);
      return { rows: hit ? [hit] : [] };
    }
    if (sql.includes("partner-room:projection-dedup-proof")) {
      const hit = this.rows.find((r) => r.msg_id === params[0]);
      return { rows: hit ? [hit] : [] };
    }
    if (sql.includes("from v_partner_room_turn")) {
      this.reads.push(params);
      if (sql.includes("partner-room:latest-head")) {
        const latest = this.rows.filter((r) => r.room_id === params[0]).at(-1);
        return { rows: [{ seq: latest?.seq ?? null }] };
      }
      if (sql.includes("partner-room:latest-page")) {
        const [room, before, mode, limit] = params;
        return { rows: this.rows.filter((r) => r.room_id === room && (before === null || r.seq < before)
          && (mode === "all" || r.kind === "turn")).sort((a, b) => b.seq - a.seq).slice(0, limit) };
      }
      if (sql.includes("partner-room:queue")) {
        return { rows: this.rows.filter((r) => r.room_id === params[0] && r.kind === "receipt").sort((a, b) => b.seq - a.seq) };
      }
      const [room, after, limit] = params;
      const match = this.rows
        .filter((r) => r.room_id === room && r.seq > after)
        .slice(0, limit);
      return { rows: match };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

const MSG = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const HEALTH_BODY = JSON.stringify({ queue_projection_health: {
  v: 1, board: "carr-build", source: "hermes-queue-projector.v1", status: "ok",
  checked_at: "2026-08-20T01:00:00Z", event_cursor: 42, projection_digest: MSG,
} });

// ── add-room-turn ─────────────────────────────────────────────────────────────

test("add-room-turn: a partner's turn lands verbatim, attributed to the verified sponsor", async () => {
  const db = new RoomFake();
  const out = await TOOLS["add-room-turn"].handler(db, joe, {
    idempotency_key: "k-say-1", seat: "claude", body: "raw text, exactly as spoken",
  });
  assert.equal(out.ok, true);
  assert.equal(out.sponsor, "joe");
  assert.equal(out.seat, "claude");
  assert.equal(out.room, "partner-line");
  assert.equal(typeof out.seq, "number");
  assert.equal(db.inserted.length, 1);
  const [room, sponsor, seat, kind, body, msgId, originChannel, originActor] = db.inserted[0];
  assert.deepEqual([room, sponsor, seat, kind, body, originChannel, originActor],
    ["partner-line", "joe", "claude", "turn", "raw text, exactly as spoken", "mcp", "joe"]);
  assert.equal(typeof msgId, "string");
});

test("read-room-latest returns the newest window in conversation order without changing the poll cursor", async () => {
  const rows = [1, 2, 3, 4].map((seq) => ({ seq, room_id: "model-room", kind: "turn", body: `turn ${seq}` }));
  const db = new RoomFake({ rows });
  const latest = await TOOLS["read-room-latest"].handler(db, joe, { room: "model-room", limit: 2 });
  assert.deepEqual(latest.turns.map((row) => row.seq), [3, 4]);
  assert.equal(latest.latest_seq, 4);
  assert.equal(latest.before_seq, 3);
  assert.equal(latest.more, true);
  const older = await TOOLS["read-room-latest"].handler(db, joe, { room: "model-room", before_seq: 3, limit: 2 });
  assert.deepEqual(older.turns.map((row) => row.seq), [1, 2]);
  const poll = await TOOLS["read-room"].handler(db, joe, { room: "model-room", after_seq: 0, limit: 2 });
  assert.deepEqual(poll.turns.map((row) => row.seq), [1, 2]);
});

test("add-room-turn: provenance is server-derived MCP identity, never a claimed human seat", async () => {
  const db = new RoomFake();
  const out = await TOOLS["add-room-turn"].handler(db, dellLocal, {
    idempotency_key: "k-origin-mcp", seat: "human", body: "@queue enqueue target=joe cap=production :: ship it",
    origin_channel: "browser-human", origin_actor: "joe",
  });
  assert.equal(out.origin_channel, "mcp");
  assert.equal(out.origin_actor, "dell-local");
  assert.equal(db.inserted[0][6], "mcp");
  assert.equal(db.inserted[0][7], "dell-local");
});

test("add-room-turn: a caller-supplied sponsor is ignored in favour of the verified one", async () => {
  const db = new RoomFake();
  const out = await TOOLS["add-room-turn"].handler(db, joe, {
    idempotency_key: "k-say-2", seat: "claude", body: "hi", sponsor: "dell",
  });
  assert.equal(out.sponsor, "joe");
  assert.equal(db.inserted[0][1], "joe");
});

test("add-room-turn: a sponsored machine door speaks as its partner (dell-local -> dell)", async () => {
  const db = new RoomFake();
  const out = await TOOLS["add-room-turn"].handler(db, dellLocal, {
    idempotency_key: "k-say-3", seat: "claude", body: "from dell's mac",
  });
  assert.equal(out.sponsor, "dell");
  assert.equal(db.inserted[0][1], "dell");
});

test("add-room-turn: a credential with no sponsoring partner is refused, and writes nothing", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, reviewer, {
      idempotency_key: "k-say-4", seat: "claude", body: "should not land",
    }),
    (err) => err.payload?.error === "no_sponsoring_partner",
  );
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: an empty turn is refused, and writes nothing", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, joe, { idempotency_key: "k-say-5", seat: "claude", body: "   " }),
    (err) => err.payload?.error === "body_required",
  );
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: an oversized turn is refused by size, named", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, joe, {
      idempotency_key: "k-say-6", seat: "claude", body: "x".repeat(20001),
    }),
    (err) => err.payload?.error === "body_too_long",
  );
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: a seat that is not a plain slug is refused", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, joe, {
      idempotency_key: "k-say-7", seat: "Not A Seat!", body: "hi",
    }),
    (err) => err.payload?.error === "seat_invalid",
  );
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: replaying the same idempotency_key returns the stored response, no second row", async () => {
  const db = new RoomFake();
  const args = { idempotency_key: "k-say-8", seat: "claude", body: "once only" };
  const first = await TOOLS["add-room-turn"].handler(db, joe, args);
  const second = await TOOLS["add-room-turn"].handler(db, joe, args);
  assert.equal(second.replayed, true);
  assert.equal(second.seq, first.seq);
  assert.equal(db.inserted.length, 1);
});

test("add-room-turn: a msg_id already in the room dedups instead of double-landing", async () => {
  const db = new RoomFake({
    msgIdTaken: MSG,
    rows: [{ msg_id: MSG, id: 42, room_id: "partner-line", sponsor: "joe", seat: "claude" }],
  });
  const out = await TOOLS["add-room-turn"].handler(db, joe, {
    idempotency_key: "k-say-9", seat: "claude", body: "same turn via a second transport", msg_id: MSG,
  });
  assert.equal(out.ok, true);
  assert.equal(out.deduplicated, true);
  assert.equal(out.seq, 42);
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: a malformed msg_id is refused", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, joe, {
      idempotency_key: "k-say-10", seat: "claude", body: "hi", msg_id: "not-a-uuid",
    }),
    (err) => err.payload?.error === "msg_id_invalid",
  );
});

// ── project-room-queue ─────────────────────────────────────────────────────

test("project-room-queue: fixes reader provenance server-side", async () => {
  const db = new RoomFake();
  const out = await TOOLS["project-room-queue"].handler(db, hermes, {
    idempotency_key: "k-project-1", body: HEALTH_BODY, msg_id: MSG,
    room: "other-room", seat: "joe", kind: "turn", sponsor: "dell",
  });
  assert.deepEqual({ room: out.room, sponsor: out.sponsor, seat: out.seat, kind: out.kind,
    origin_channel: out.origin_channel, origin_actor: out.origin_actor, msg_id: out.msg_id }, {
    room: "partner-line", sponsor: "joe", seat: "hermes", kind: "receipt",
    origin_channel: "mcp", origin_actor: "hermes-pilot", msg_id: MSG,
  });
  assert.deepEqual(db.inserted[0], ["partner-line", "joe", "hermes", "receipt",
    HEALTH_BODY, MSG, "mcp", "hermes-pilot"]);
  const accepted = queueProjectionHealthFromTurn({
    at: out.at, sponsor: out.sponsor, seat: out.seat, kind: out.kind, body: HEALTH_BODY,
    origin_channel: out.origin_channel, origin_actor: out.origin_actor,
  });
  assert.equal(accepted?.event_cursor, 42,
    "the append response itself must satisfy the production reader's parser");
});

test("project-room-queue: joe-local and arbitrary Hermes prose both fail closed", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["project-room-queue"].handler(db, dellLocal, {
      idempotency_key: "k-project-2", body: HEALTH_BODY, msg_id: MSG,
    }),
    (err) => err.payload?.error === "queue_projector_identity_required",
  );
  await assert.rejects(
    TOOLS["project-room-queue"].handler(db, hermes, {
      idempotency_key: "k-project-3", body: "ordinary room prose", msg_id: MSG,
    }),
    (err) => err.payload?.error === "queue_projection_receipt_invalid",
  );
  assert.equal(db.inserted.length, 0);
});

test("project-room-queue: a dedup row with rejected provenance is not healthy", async () => {
  const db = new RoomFake({ msgIdTaken: MSG, rows: [{
    msg_id: MSG, id: 42, room_id: "partner-line", sponsor: "joe", seat: "hermes",
    kind: "receipt", body: HEALTH_BODY, at: "2026-08-20T01:00:00+00:00",
    origin_channel: "mcp", origin_actor: "joe-local",
  }] });
  await assert.rejects(
    TOOLS["project-room-queue"].handler(db, hermes, {
      idempotency_key: "k-project-4", body: HEALTH_BODY, msg_id: MSG,
    }),
    (err) => err.payload?.error === "queue_projection_provenance_rejected",
  );
  assert.equal(db.inserted.length, 0);
});

test("project-room-queue: replaying a durable event whose task moved state still dedups", async () => {
  const dupMsg = "dddddddd-eeee-4fff-8000-111111111111";
  const EVENT_READY = JSON.stringify({ queue_event: {
    v: 1, board: "carr-build", event_id: 1302, event: "task.updated", task_id: "t_abc123",
    card: { title: "x", target: "joe", effective_model: "sonnet", status: "ready",
      priority: "p2", cap: "production", updated_at: "2026-09-01T04:08:19Z", source_seq: 1 },
    summary: "task ready, created.", projected_at: "2026-09-01T04:08:19Z",
  } });
  const EVENT_BLOCKED = JSON.stringify({ queue_event: {
    v: 1, board: "carr-build", event_id: 1302, event: "task.updated", task_id: "t_abc123",
    card: { title: "x", target: "joe", effective_model: "sonnet", status: "blocked",
      priority: "p2", cap: "production", updated_at: "2026-09-19T00:00:00Z", source_seq: 1 },
    summary: "task is blocked.", projected_at: "2026-09-19T00:00:00Z",
  } });

  // 1. append event 1302's receipt.
  const db = new RoomFake();
  const first = await TOOLS["project-room-queue"].handler(db, hermes, {
    idempotency_key: "k-project-5", body: EVENT_READY, msg_id: dupMsg,
  });
  assert.equal(first.ok, true);
  assert.equal(db.inserted.length, 1);

  // 2. mutate the task's durable row: receipt_for now renders a different body
  // (status ready -> blocked) for the SAME event id, so the durable row on
  // disk no longer equals what this cycle would recompute.
  db.msgIdTaken = dupMsg;
  db.rows.push({ msg_id: dupMsg, id: first.seq, room_id: "partner-line", sponsor: "joe",
    seat: "hermes", kind: "receipt", body: EVENT_BLOCKED, at: "2026-09-01T04:08:19+00:00",
    origin_channel: "mcp", origin_actor: "hermes-pilot" });

  // 3. replay the same event id (same msg_id). Before the fix this threw
  // queue_projection_provenance_rejected because observed.body (blocked) no
  // longer matched the freshly recomputed body (ready).
  const replay = await TOOLS["project-room-queue"].handler(db, hermes, {
    idempotency_key: "k-project-6", body: EVENT_READY, msg_id: dupMsg,
  });
  assert.equal(replay.ok, true);
  assert.equal(replay.deduplicated, true);
  assert.equal(db.inserted.length, 1, "the replay must not append a second row");

  // A fresh msg_id (no durable row yet) must still take the insert path —
  // proving the dedup branch, not the guard, is what changed.
  const freshMsg = "22222222-3333-4444-8555-666666666666";
  const fresh = await TOOLS["project-room-queue"].handler(db, hermes, {
    idempotency_key: "k-project-7", body: EVENT_BLOCKED, msg_id: freshMsg,
  });
  assert.equal(fresh.ok, true);
  assert.equal(fresh.deduplicated, undefined);
  assert.equal(db.inserted.length, 2);
});

// ── read-room ───────────────────────────────────────────────────────────────

test("read-room: returns turns after the cursor, oldest first, with the new cursor", async () => {
  const db = new RoomFake({
    rows: [
      { seq: 5, room_id: "partner-line", at: "t5", sponsor: "joe", seat: "claude", kind: "turn", body: "a", msg_id: "m5" },
      { seq: 6, room_id: "partner-line", at: "t6", sponsor: "dell", seat: "claude", kind: "turn", body: "b", msg_id: "m6" },
      { seq: 7, room_id: "other-room", at: "t7", sponsor: "joe", seat: "grok", kind: "turn", body: "c", msg_id: "m7" },
    ],
  });
  const out = await TOOLS["read-room"].handler(db, joe, { after_seq: 5 });
  assert.equal(out.ok, true);
  assert.equal(out.room, "partner-line");
  assert.deepEqual(out.turns.map((t) => t.body), ["b"]);
  assert.equal(out.latest_seq, 6);
  assert.deepEqual(db.reads[0], ["partner-line", 5, 50]);
});

test("read-room: exposes truthful legacy and server-derived provenance fields", async () => {
  const db = new RoomFake({ rows: [
    { seq: 5, room_id: "partner-line", at: "t5", sponsor: "joe", seat: "human", kind: "turn", body: "old", msg_id: "m5", origin_channel: "legacy", origin_actor: "legacy" },
    { seq: 6, room_id: "partner-line", at: "t6", sponsor: "joe", seat: "human", kind: "turn", body: "new", msg_id: "m6", origin_channel: "browser-human", origin_actor: "joe" },
  ] });
  const out = await TOOLS["read-room"].handler(db, joe, {});
  assert.deepEqual(out.turns.map(({ origin_channel, origin_actor }) => ({ origin_channel, origin_actor })), [
    { origin_channel: "legacy", origin_actor: "legacy" },
    { origin_channel: "browser-human", origin_actor: "joe" },
  ]);
});

test("partner-room provenance migration backfills only legacy and replaces the reader view", async () => {
  const sql = await readFile(new URL("../../migrations/0298_partner_room_origin.sql", import.meta.url), "utf8");
  assert.match(sql, /add column if not exists origin_channel text not null default 'legacy'/i);
  assert.match(sql, /add column if not exists origin_actor text not null default 'legacy'/i);
  assert.match(sql, /create or replace view v_partner_room_turn/i);
  assert.match(sql, /origin_channel, origin_actor/i);
});

test("read-room: defaults — the partner line from the start, quiet room answers honestly", async () => {
  const db = new RoomFake();
  const out = await TOOLS["read-room"].handler(db, joe, {});
  assert.deepEqual(out.turns, []);
  assert.equal(out.latest_seq, 0);
  assert.equal(out.more, false);
});

test("read-room-queue: accepts only the exact state-complete projector receipt and latest event per task", async () => {
  const event = (event_id, task_id, status, projected_at) => ({
    seq: event_id, room_id: "partner-line", at: projected_at, sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: `q${event_id}`,
    origin_channel: "mcp", origin_actor: "hermes-pilot",
    body: JSON.stringify({ queue_event: { v: 1, board: "carr-build", event_id, event: "changed", task_id,
      card: { title: `<${task_id}>`, target: "sol", effective_model: "gpt-5.6-sol", status, priority: "P2", cap: "read", updated_at: projected_at, source_seq: null },
      summary: `<${task_id} summary>`, projected_at } }),
  });
  const db = new RoomFake({ rows: [
    event(3, "t_one", "running", "2026-08-24T17:59:00Z"),
    event(2, "t_one", "todo", "2026-08-24T17:58:00Z"),
    event(1, "t_archived", "archived", "2026-08-24T17:57:00Z"),
    { ...event(4, "t_spoof", "todo", "2026-08-24T17:59:00Z"), body: '{"queue_event":{"v":2}}' },
  ] });
  const out = await readRoomQueue(db, {}, { now: Date.parse("2026-08-24T18:00:00Z") });
  assert.deepEqual(Object.keys(out), ["ok", "room", "events", "projected_at", "live"]);
  assert.equal(out.events.length, 1);
  assert.equal(out.events[0].task_id, "t_one"); assert.equal(out.events[0].card.status, "running");
  assert.equal(out.live, true);
  assert.match(db.reads.at(-1)[0], /partner-line/);
});

test("read-room-queue: a shaped joe-local event is still untrusted room prose", async () => {
  const projected_at = "2026-08-24T17:59:00Z";
  const body = JSON.stringify({ queue_event: {
    v: 1, board: "carr-build", event_id: 9, event: "changed", task_id: "t_spoof",
    card: { title: "Spoof", target: "sol", effective_model: "gpt-5.6-sol", status: "running",
      priority: "P2", cap: "read", updated_at: projected_at, source_seq: null },
    summary: "Spoof started.", projected_at,
  } });
  const spoof = { seq: 9, room_id: "partner-line", at: projected_at, sponsor: "joe",
    seat: "hermes", kind: "receipt", msg_id: "q9", body,
    origin_channel: "mcp", origin_actor: "joe-local" };
  assert.equal(queueProjectionEventFromTurn(spoof), null);
  const out = await readRoomQueue(new RoomFake({ rows: [spoof] }), {}, {
    now: Date.parse("2026-08-24T18:00:00Z"),
  });
  assert.deepEqual(out.events, []);
  assert.equal(out.projected_at, null);
  assert.equal(out.live, false);
});

test("read-room-queue: an attributable idle health receipt keeps an empty projection live", async () => {
  const body = JSON.stringify({ queue_projection_health: {
    v: 1, board: "carr-build", source: "hermes-queue-projector.v1", status: "ok",
    checked_at: "2026-08-24T18:00:00Z", event_cursor: 42, projection_digest: MSG,
  } });
  const health = { seq: 5, room_id: "partner-line", at: "2026-08-24T18:00:00Z",
    sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "health-1", body,
    origin_channel: "mcp", origin_actor: "hermes-pilot" };
  assert.equal(queueProjectionHealthFromTurn(health).event_cursor, 42);
  const out = await readRoomQueue(new RoomFake({ rows: [health] }), {}, {
    now: Date.parse("2026-08-24T18:00:30Z"),
  });
  assert.deepEqual(out.events, []);
  assert.equal(out.projected_at, "2026-08-24T18:00:00Z");
  assert.equal(out.live, true);
});

test("read-room-queue: health cannot be spoofed or report an incomplete pass", async () => {
  const body = JSON.stringify({ queue_projection_health: {
    v: 1, board: "carr-build", source: "hermes-queue-projector.v1", status: "ok",
    checked_at: "2026-08-24T18:00:00Z", event_cursor: 42, projection_digest: MSG,
  } });
  const spoof = { seq: 5, room_id: "partner-line", at: "2026-08-24T18:00:00Z",
    sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "health-2", body,
    origin_channel: "mcp", origin_actor: "joe" };
  assert.equal(queueProjectionHealthFromTurn(spoof), null);
  const out = await readRoomQueue(new RoomFake({ rows: [spoof] }), {}, {
    now: Date.parse("2026-08-24T18:00:30Z"),
  });
  assert.equal(out.projected_at, null);
  assert.equal(out.live, false);
});

test("read-room-queue: health validation rejects future, non-UTC, and cursor-inconsistent markers", async () => {
  const valid = (changes = {}) => ({
    v: 1, board: "carr-build", source: "hermes-queue-projector.v1", status: "ok",
    checked_at: "2026-08-24T18:00:00Z", event_cursor: 42, projection_digest: MSG, ...changes,
  });
  const base = { seq: 5, room_id: "partner-line", at: "2026-08-24T18:00:30Z",
    sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "health-3",
    origin_channel: "mcp", origin_actor: "hermes-pilot" };
  for (const health of [
    valid({ checked_at: "2026-08-24T18:01:00Z" }),
    valid({ checked_at: "2026-08-24T18:00:00-04:00" }),
    valid({ event_cursor: 0 }),
    valid({ event_cursor: 42, projection_digest: null }),
    valid({ board: "other-board" }),
    valid({ v: 2 }),
  ]) {
    assert.equal(queueProjectionHealthFromTurn({ ...base, body: JSON.stringify({ queue_projection_health: health }) }), null);
  }
});

test("read-room-queue: liveness uses the server append time, not a stale body clock", async () => {
  const health = { v: 1, board: "carr-build", source: "hermes-queue-projector.v1", status: "ok",
    checked_at: "2026-08-24T17:59:00Z", event_cursor: 42, projection_digest: MSG };
  const row = { seq: 5, room_id: "partner-line", at: "2026-08-24T18:00:00Z",
    sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "health-4",
    body: JSON.stringify({ queue_projection_health: health }), origin_channel: "mcp", origin_actor: "hermes-pilot" };
  const out = await readRoomQueue(new RoomFake({ rows: [row] }), {}, {
    now: Date.parse("2026-08-24T18:01:30Z"),
  });
  assert.equal(out.live, true);
  assert.equal(out.projected_at, "2026-08-24T18:00:00Z");
});
