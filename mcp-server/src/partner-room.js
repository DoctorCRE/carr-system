// The partner room (Idea 78): one shared, append-only turn log that two brains
// write and a human watches — raw turns, never a recap.
//
// WHY THIS LIVES IN THE WORKER. The spike (spikes/partner-line-78, receipts
// 1–3) proved the local half: a process can drop a partner's turn into a live
// Claude Code session, and the wall on one Mac is same-UID. The moment the line
// crosses machines something else must be the boundary, and the Worker already
// IS that boundary for both partners — same OAuth identities, same locked
// profiles, no new inbound port, no tunnel, no new secret class. Each Mac polls
// the room and injects locally; the room is the transport AND the spectator
// surface. Council turns (grok, sol, hermes) are the same rows with a different
// seat, which is why their visibility costs nothing extra.
//
// ATTRIBUTION IS SERVER-DERIVED, the same refusal the spike relay states:
// whose name a turn lands under comes from the verified credential
// (personalScopeForActor), never from an argument. `seat` says which brain on
// that partner's side spoke; it is claimed, and rendered as claimed, under the
// verified sponsor. Consent posture for what a receiving Mac DOES with a turn
// is decision 351b9995 (auto-inject, visible, abortable) and is enforced
// client-side; the room itself only records.
//
// The room is the live wire, not the record: durable outcomes reached on the
// line still go through log-decision / add-loop / the deal verbs.

import { personalScopeForActor } from "./identity.js";

export const DEFAULT_ROOM = "partner-line";
// The room the Observatory panel reads and posts turns in: the one the local
// room bridge polls (ops/launchd/com.carr.room-bridge.plist CARR_ROOM_BRIDGE_ROOM).
// The queue projection stays in DEFAULT_ROOM under its fixed Hermes provenance.
export const OBSERVATORY_ROOM = "model-room";
const SLUG = /^[a-z0-9][a-z0-9-]{0,31}$/;
const UUID = /^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$/;
export const ROOM_BODY_MAX = 20000;
export const ROOM_READ_DEFAULT = 50;
export const ROOM_READ_MAX = 200;
export const ROOM_KINDS = ["turn", "system", "receipt"];
export const QUEUE_STALE_MS = 120_000;
const QUEUE_EVENT_KEYS = ["v", "board", "event_id", "event", "task_id", "card", "summary", "projected_at"];
const QUEUE_CARD_KEYS = ["title", "target", "effective_model", "status", "priority", "cap", "updated_at", "source_seq"];
const QUEUE_HEALTH_KEYS = ["v", "board", "source", "status", "checked_at", "event_cursor", "projection_digest"];

/** null when the name is not a room slug; the normalized name otherwise. */
export function normalizeRoomName(value) {
  const room = value === undefined || value === null ? DEFAULT_ROOM : String(value).trim().toLowerCase();
  return SLUG.test(room) ? room : null;
}

/** Clamp a caller's paging arguments to the room's own read contract. */
export function normalizeRoomPaging({ after_seq, limit }) {
  const after = Number.isInteger(after_seq) && after_seq > 0 ? after_seq : 0;
  const capped = Number.isInteger(limit)
    ? Math.min(Math.max(limit, 1), ROOM_READ_MAX) : ROOM_READ_DEFAULT;
  return { after, limit: capped };
}

// THE ONE ROOM READ (rule a8c55a47 — a manual path and an automated path that
// do the same job must be the same code). The read-room verb and the Deal
// Room's /api/room/turns endpoint are two doors onto the same wire; if the
// query were copied into the browser endpoint, the observatory could quietly
// drift from what every desk and every session sees. Both call this.
export async function readRoomTurns(c, args = {}) {
  const room = normalizeRoomName(args.room);
  if (room === null) return { ok: false, error: "room_invalid" };
  const { after, limit } = normalizeRoomPaging(args);
  const r = await c.query(
    `select id as seq, room_id, to_jsonb(at)#>>'{}' as at, sponsor, seat, kind, body, msg_id, origin_channel, origin_actor
       from v_partner_room_turn
      where room_id=$1 and id > $2
      order by id asc limit $3 /* partner-room:read */`,
    [room, after, limit],
  );
  const turns = r.rows;
  return { ok: true, room, turns,
    latest_seq: turns.length ? turns[turns.length - 1].seq : after,
    more: turns.length === limit };
}

/** A bounded newest-first window for a human opening the Observatory. The
 * oldest-first poll contract above remains unchanged for every bridge desk. */
export async function readRoomLatest(c, args = {}) {
  const room = normalizeRoomName(args.room);
  if (room === null) return { ok: false, error: "room_invalid" };
  const { limit } = normalizeRoomPaging(args);
  const before = Number.isSafeInteger(args.before_seq) && args.before_seq > 0 ? args.before_seq : null;
  const mode = args.mode === "conversation" ? "conversation" : "all";
  const [head, page] = await Promise.all([
    c.query("select max(id) as seq from v_partner_room_turn where room_id=$1 /* partner-room:latest-head */", [room]),
    c.query(`select id as seq, room_id, to_jsonb(at)#>>'{}' as at, sponsor, seat, kind, body, msg_id, origin_channel, origin_actor
       from v_partner_room_turn
      where room_id=$1 and ($2::bigint is null or id < $2)
        and ($3::text = 'all' or kind = 'turn')
      order by id desc limit $4 /* partner-room:latest-page */`, [room, before, mode, limit]),
  ]);
  const turns = [...page.rows].reverse();
  return { ok: true, room, mode, turns, latest_seq: head.rows[0]?.seq ?? "0",
    before_seq: turns.length ? turns[0].seq : null, more: page.rows.length === limit };
}

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

/** Only fully-validated, projector-shaped receipts become Queue data.  A room
 * turn is untrusted prose even when it claims to be JSON. */
export function queueEventFromTurn(turn) {
  if (turn?.kind !== "receipt" || typeof turn.body !== "string") return null;
  try {
    const outer = JSON.parse(turn.body);
    const event = outer?.queue_event;
    if (!exactKeys(outer, ["queue_event"]) || !exactKeys(event, QUEUE_EVENT_KEYS) ||
        event.v !== 1 || event.board !== "carr-build" || !Number.isInteger(event.event_id) ||
        typeof event.event !== "string" || !/^t_[a-z0-9]+$/i.test(event.task_id) ||
        !exactKeys(event.card, QUEUE_CARD_KEYS) || typeof event.summary !== "string" ||
        !Number.isFinite(Date.parse(event.projected_at))) return null;
    const card = event.card;
    if (!(["string", "object"].includes(typeof card.effective_model)) ||
        !["title", "target", "status", "priority", "cap", "updated_at"].every((key) => typeof card[key] === "string") ||
        !(card.source_seq === null || (Number.isInteger(card.source_seq) && card.source_seq >= 0)) ||
        !Number.isFinite(Date.parse(card.updated_at))) return null;
    return event;
  } catch { return null; }
}

/** Only a server-attributable Hermes projector receipt can extend queue
 * freshness. It carries no task/card data and never participates in the task
 * state map. */
export function queueProjectionHealthFromTurn(turn) {
  if (turn?.kind !== "receipt" || turn.seat !== "hermes" ||
      turn.sponsor !== "joe" ||
      turn.origin_channel !== "mcp" || turn.origin_actor !== "hermes-pilot" ||
      typeof turn.body !== "string") return null;
  try {
    const outer = JSON.parse(turn.body);
    const health = outer?.queue_projection_health;
    if (!exactKeys(outer, ["queue_projection_health"]) ||
        !exactKeys(health, QUEUE_HEALTH_KEYS) || health.v !== 1 ||
        health.board !== "carr-build" || health.source !== "hermes-queue-projector.v1" ||
        health.status !== "ok" || !Number.isInteger(health.event_cursor) ||
        health.event_cursor < 0 || typeof health.checked_at !== "string" ||
        !/Z$|\+00:00$/.test(health.checked_at) ||
        !Number.isFinite(Date.parse(health.checked_at)) ||
        !/Z$|\+00:00$/.test(String(turn.at || "")) ||
        !Number.isFinite(Date.parse(turn.at)) || Date.parse(health.checked_at) > Date.parse(turn.at) ||
        !(health.projection_digest === null || UUID.test(health.projection_digest)) ||
        ((health.event_cursor === 0) !== (health.projection_digest === null))) return null;
    return health;
  } catch { return null; }
}

/** The queue projector's only write shape.  Both event and health receipts
 * must carry the exact server-derived Hermes provenance the reader trusts. */
export function queueProjectionReceiptFromTurn(turn) {
  if (turn?.kind !== "receipt" || turn.seat !== "hermes" || turn.sponsor !== "joe" ||
      turn.origin_channel !== "mcp" || turn.origin_actor !== "hermes-pilot") return null;
  return queueProjectionHealthFromTurn(turn) || queueProjectionEventFromTurn(turn);
}

/** A Queue event is data only when it came through the same locked projector
 * provenance as health. Shape alone never turns ordinary room prose into Queue. */
export function queueProjectionEventFromTurn(turn) {
  if (turn?.kind !== "receipt" || turn.seat !== "hermes" || turn.sponsor !== "joe" ||
      turn.origin_channel !== "mcp" || turn.origin_actor !== "hermes-pilot") return null;
  return queueEventFromTurn(turn);
}

/** Shared queue read: latest state-complete event per canonical task. */
export async function readRoomQueue(c, args = {}, { now = Date.now() } = {}) {
  const room = normalizeRoomName(args.room);
  if (room === null) return { ok: false, error: "room_invalid" };
  const r = await c.query(
    `select id as seq, room_id, to_jsonb(at)#>>'{}' as at, sponsor, seat, kind, body, msg_id, origin_channel, origin_actor
       from v_partner_room_turn where room_id=$1 and kind='receipt'
      order by id desc limit 800 /* partner-room:queue */`, [room],
  );
  const latest = new Map();
  let projectionHealthAt = null;
  let latestQueueEventAt = null;
  for (const turn of r.rows) {
    const health = queueProjectionHealthFromTurn(turn);
    if (health && (!projectionHealthAt || Date.parse(turn.at) > Date.parse(projectionHealthAt)))
      projectionHealthAt = turn.at;
    const event = queueProjectionEventFromTurn(turn);
    if (event && Number.isFinite(Date.parse(turn.at)) &&
        (!latestQueueEventAt || Date.parse(turn.at) > Date.parse(latestQueueEventAt)))
      latestQueueEventAt = turn.at;
    if (!event || latest.has(event.task_id)) continue;
    latest.set(event.task_id, event);
  }
  const events = [...latest.values()].filter((event) => event.card.status !== "archived")
    .sort((a, b) => Date.parse(b.card.updated_at) - Date.parse(a.card.updated_at));
  const projectedAt = events.reduce((latestAt, event) =>
    !latestAt || Date.parse(event.projected_at) > Date.parse(latestAt) ? event.projected_at : latestAt, null);
  const healthCanExtend = projectionHealthAt &&
    (!latestQueueEventAt || Date.parse(projectionHealthAt) > Date.parse(latestQueueEventAt));
  const freshest = healthCanExtend ? projectionHealthAt : (latestQueueEventAt || projectedAt);
  return { ok: true, room, events, projected_at: freshest,
    live: Boolean(freshest) && now >= Date.parse(freshest) && now - Date.parse(freshest) <= QUEUE_STALE_MS };
}

// THE ONE ROOM APPEND, for the same reason. Sponsor is always resolved by the
// CALLER from a verified credential and passed in — this function never reads
// it off an argument object that came from a request body.
export async function appendRoomTurn(c, { room, sponsor, seat, kind, body, msgId, originChannel, originActor }) {
  if (!(["mcp", "browser-human"].includes(originChannel)) || !SLUG.test(originActor || ""))
    throw new TypeError("room provenance must be server-derived");
  const ins = await c.query(
    `insert into partner_room_turn (room_id, sponsor, seat, kind, body, msg_id, origin_channel, origin_actor)
     values ($1,$2,$3,$4,$5,$6,$7,$8)
     on conflict (msg_id) do nothing
     returning id, to_jsonb(at)#>>'{}' as at /* partner-room:say */`,
    [room, sponsor, seat, kind, body, msgId, originChannel, originActor],
  );
  if (!ins.rows.length) {
    const prior = await c.query(
      "select id, room_id, sponsor, seat, kind, origin_channel, origin_actor from partner_room_turn where msg_id=$1 /* partner-room:dedup-read */",
      [msgId],
    );
    if (!prior.rows.length) return { ok: false, error: "dedup_row_vanished", msg_id: msgId };
    const p = prior.rows[0];
    return { ok: true, deduplicated: true, room: p.room_id, seq: p.id,
      sponsor: p.sponsor, seat: p.seat, kind: p.kind, origin_channel: p.origin_channel,
      origin_actor: p.origin_actor, msg_id: msgId };
  }
  return { ok: true, room, seq: ins.rows[0].id, at: ins.rows[0].at,
    sponsor, seat, kind, origin_channel: originChannel, origin_actor: originActor, msg_id: msgId };
}

export function partnerRoomTools({ withEnvelope, ToolError }) {
  return {
    "add-room-turn": {
      write: true,
      description: "Append one raw turn to a partner room — the shared, human-watchable transcript the partner line and the council talks write into (Idea 78). The turn lands verbatim, attributed to the VERIFIED partner behind the calling credential (server-derived, never caller-supplied); `seat` names which brain on that partner's side spoke (claude, human, grok, sol, hermes, codex). Pass msg_id only when relaying a turn that already has one, so a second transport dedups instead of double-landing. The room is the live wire, not the record — durable outcomes still go through log-decision and the loop/deal verbs.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        body: { type: "string", description: "the turn, raw and unsummarised" },
        seat: { type: "string", description: "which brain on your side is speaking: claude | human | grok | sol | hermes | codex" },
        room: { type: "string", description: "room name; default partner-line" },
        kind: { type: "string", enum: ["turn", "system", "receipt"] },
        msg_id: { type: "string", description: "optional UUID carried across transports for dedup; a fresh one is minted when absent" },
      }, required: ["idempotency_key", "body", "seat"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "add-room-turn", args, async () => {
        const seat = String(args.seat ?? "").trim().toLowerCase();
        if (!SLUG.test(seat))
          throw new ToolError({ error: "seat_invalid",
            hint: "a seat is a plain slug: claude, human, grok, sol, hermes, codex" });
        const room = normalizeRoomName(args.room);
        if (room === null) throw new ToolError({ error: "room_invalid" });
        const kind = args.kind ?? "turn";
        if (!ROOM_KINDS.includes(kind)) throw new ToolError({ error: "kind_invalid" });
        const body = typeof args.body === "string" ? args.body : "";
        if (!body.trim()) throw new ToolError({ error: "body_required" });
        if (body.length > ROOM_BODY_MAX)
          throw new ToolError({ error: "body_too_long", limit: ROOM_BODY_MAX, got: body.length });
        if (args.msg_id !== undefined && !UUID.test(String(args.msg_id)))
          throw new ToolError({ error: "msg_id_invalid", hint: "msg_id must be a UUID" });

        const scope = personalScopeForActor(actor);
        if (scope.status !== "personal")
          throw new ToolError({ error: "no_sponsoring_partner",
            hint: "the room only takes turns from a credential a partner sponsors; this one has no partner behind it" });

        const msgId = args.msg_id === undefined ? crypto.randomUUID() : String(args.msg_id).toLowerCase();
        const appended = await appendRoomTurn(c, { room, sponsor: scope.sponsor, seat, kind, body, msgId,
          originChannel: "mcp", originActor: actor.slug });
        if (appended.ok !== true) { const { ok: _ok, ...failure } = appended; throw new ToolError(failure); }
        return appended;
      }),
    },

    "project-room-queue": {
      write: true,
      description: "Append one shape-checked carr-build projection receipt through the locked Hermes machine identity. The server fixes partner-line/joe/hermes/receipt and rejects arbitrary room prose. This is the unattended room bridge's projection door, not a general transcript writer.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        body: { type: "string", description: "exact queue_event or queue_projection_health JSON envelope" },
        msg_id: { type: "string", description: "deterministic UUID replay id from the projector" },
      }, required: ["idempotency_key", "body", "msg_id"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "project-room-queue", args, async () => {
        if (actor?.hermes !== true || actor.slug !== "hermes-pilot")
          throw new ToolError({ error: "queue_projector_identity_required",
            hint: "this door requires the server-derived hermes-pilot identity" });
        const body = typeof args.body === "string" ? args.body : "";
        if (body.length > ROOM_BODY_MAX)
          throw new ToolError({ error: "body_too_long", limit: ROOM_BODY_MAX, got: body.length });
        if (!UUID.test(String(args.msg_id || "")))
          throw new ToolError({ error: "msg_id_invalid", hint: "msg_id must be a UUID" });
        const msgId = String(args.msg_id).toLowerCase();
        const fixed = { sponsor: "joe", seat: "hermes", kind: "receipt", body,
          origin_channel: "mcp", origin_actor: "hermes-pilot", at: new Date().toISOString() };
        if (!queueProjectionReceiptFromTurn(fixed))
          throw new ToolError({ error: "queue_projection_receipt_invalid",
            hint: "body must be an exact reader-valid queue_event or queue_projection_health envelope" });

        const appended = await appendRoomTurn(c, { room: DEFAULT_ROOM, sponsor: "joe", seat: "hermes",
          kind: "receipt", body, msgId, originChannel: "mcp", originActor: "hermes-pilot" });
        if (appended.ok !== true) {
          const { ok: _ok, ...failure } = appended;
          throw new ToolError(failure);
        }
        let observed = { ...fixed, at: appended.at || fixed.at };
        if (appended.deduplicated) {
          const prior = await c.query(
            `select to_jsonb(at)#>>'{}' as at, sponsor, seat, kind, body, origin_channel, origin_actor
               from partner_room_turn where msg_id=$1 /* partner-room:projection-dedup-proof */`, [msgId]);
          observed = prior.rows[0] || {};
        }
        if (!queueProjectionReceiptFromTurn(observed))
          throw new ToolError({ error: "queue_projection_provenance_rejected",
            hint: "the durable row does not satisfy the same provenance and body parser as read-room-queue" });
        return appended;
      }),
    },

    "read-room": {
      description: "Read a partner room's turns after a cursor, oldest first — the poll half of the partner line and the spectator view's source. Returns raw turns exactly as written (who, when, seat, text — never a recap), the new cursor (latest_seq), and whether more rows are waiting. A quiet room returns an empty list, honestly; poll by passing the last latest_seq back as after_seq.",
      inputSchema: { type: "object", properties: {
        room: { type: "string", description: "room name; default partner-line" },
        after_seq: { type: "integer", description: "return turns with seq greater than this; 0 or absent reads from the start" },
        limit: { type: "integer", description: "max turns to return; default 50, cap 200" },
      } },
      handler: async (c, _actor, args) => {
        const read = await readRoomTurns(c, args);
        if (read.ok !== true) { const { ok: _ok, ...failure } = read; throw new ToolError(failure); }
        return read;
      },
    },
    "read-room-latest": {
      description: "Open the newest Model Room window without walking its entire history. Returns turns in reading order, the room head, and a before_seq cursor for older pages. mode conversation returns spoken turns only; all includes machine receipts for live health. The bridge poll remains read-room.",
      inputSchema: { type: "object", properties: {
        room: { type: "string", description: "room name; default partner-line" },
        before_seq: { type: "integer", description: "older turns have seq below this cursor" },
        limit: { type: "integer", description: "max turns; default 50, cap 200" },
        mode: { type: "string", enum: ["all", "conversation"] },
      } },
      handler: async (c, _actor, args) => {
        const read = await readRoomLatest(c, args);
        if (read.ok !== true) { const { ok: _ok, ...failure } = read; throw new ToolError(failure); }
        return read;
      },
    },
    "read-room-queue": {
      description: "Read the current non-archived carr-build Queue projection. Hermes task state is authoritative; this returns only the latest validated, state-complete projection receipt per task and declares live:false when its projection is stale.",
      inputSchema: { type: "object", properties: { room: { type: "string", description: "room name; default partner-line" } } },
      handler: async (c, _actor, args) => {
        const read = await readRoomQueue(c, args);
        if (read.ok !== true) { const { ok: _ok, ...failure } = read; throw new ToolError(failure); }
        return read;
      },
    },
  };
}
