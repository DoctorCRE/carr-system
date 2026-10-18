// Deal Room polling surface. Authentication stays outside this module: callers
// pass the already-resolved actor and an injected query client. That keeps the
// contract mountable behind both OAuth and the Deal Room session-cookie gate.

const JSON_HEADERS = { "content-type": "application/json" };
const PLACEHOLDER_FIELDS = new Set([
  "sf_commission_placeholder",
  "sf_close_date_placeholder",
]);

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

function base64UrlEncode(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlDecode(text) {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const binary = atob(padded);
  return new TextDecoder().decode(Uint8Array.from(binary, ch => ch.charCodeAt(0)));
}

export function encodePipelineCursor(recordedAt, id) {
  return base64UrlEncode(JSON.stringify({ recorded_at: recordedAt, id }));
}

export function decodePipelineCursor(cursor) {
  if (!cursor) return null;
  try {
    const decoded = JSON.parse(base64UrlDecode(cursor));
    if (!decoded || typeof decoded.recorded_at !== "string" || typeof decoded.id !== "string")
      throw new Error("bad cursor shape");
    // Validate shape only — pg timestamp wire format ("2026-08-07
    // 21:44:19.123456+00") is not JS-Date-parseable, and Postgres itself is
    // the real validator when this value hits the ::timestamptz cast.
    if (!/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}(:?\d{2})?|Z)?$/.test(decoded.recorded_at))
      throw new Error("bad cursor time");
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(decoded.id))
      throw new Error("bad cursor id");
    return decoded;
  } catch {
    throw new Error("invalid_cursor");
  }
}

// Defense in depth over the SQL view: a historical field-less event may carry
// an object, so recursively remove placeholder keys from every response shape.
export function stripDealPlaceholders(value) {
  if (Array.isArray(value)) return value.map(stripDealPlaceholders);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !PLACEHOLDER_FIELDS.has(key))
      .map(([key, nested]) => [key, stripDealPlaceholders(nested)]));
  }
  return value;
}

export async function pipelineChanges(request, client, actor, options = {}) {
  if (!actor) return json({ error: "unauthorized" }, 401);
  if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);

  const url = new URL(request.url);
  let cursor;
  try {
    cursor = decodePipelineCursor(url.searchParams.get("cursor"));
  } catch {
    return json({ error: "invalid_cursor" }, 400);
  }

  const limit = options.limit || 200;
  const events = await client.query(
    `select id, to_jsonb(recorded_at)#>>'{}' as recorded_at, actor, verb, subject_type, subject_id, field, old_value, new_value
       from v_deal_room_event
      where ($1::timestamptz is null or (recorded_at, id) > ($1::timestamptz, $2::uuid))
      order by recorded_at asc, id asc
      limit $3`,
    [cursor?.recorded_at || null, cursor?.id || null, limit],
  );
  const presence = await client.query(
    `select actor, deal_id, field, to_jsonb(expires_at)#>>'{}' as expires_at
       from v_deal_room_presence
      where expires_at > now()
      order by actor, deal_id, field`,
  );
  // Capture state is a current snapshot rather than a second event cursor.
  // Every poll sees changes immediately, while the existing deal-event cursor
  // remains byte-for-byte compatible and keeps its single total order.
  const captureSessions = await client.query(
    `select session_id, device_id, state,
            to_jsonb(started_at)#>>'{}' as started_at,
            to_jsonb(state_at)#>>'{}' as state_at
       from v_capture_session_status
      order by started_at, session_id`,
  );

  const cleanEvents = events.rows
    .filter(row => !PLACEHOLDER_FIELDS.has(row.field))
    .map(stripDealPlaceholders);
  const last = cleanEvents.at(-1);
  // recorded_at rides the cursor verbatim: it round-trips back into a
  // $::timestamptz cast, and Postgres always parses its own wire format.
  // JS Date cannot (microsecond precision + offset threw "Invalid time
  // value" on the first live poll), so no Date ever touches it.
  const nextCursor = last
    ? encodePipelineCursor(String(last.recorded_at), String(last.id))
    : (url.searchParams.get("cursor") || "");

  return json({ events: cleanEvents, presence: presence.rows.map(stripDealPlaceholders),
    capture_sessions: captureSessions.rows, cursor: nextCursor });
}

export const DEAL_ROOM_FIELDS = Object.freeze(["phase", "owner", "attention", "next_date", "operating_state"]);
const PARKING_REASONS = Object.freeze(["prospect_never_active", "client_paused", "other"]);

function dealRoomFieldError(field, value) {
  if (!DEAL_ROOM_FIELDS.includes(field))
    return { error: "field_not_patchable", field, allowed: DEAL_ROOM_FIELDS };
  if (field === "attention" && typeof value !== "boolean")
    return { error: "invalid_field_value", field, expected: "boolean" };
  if (field === "phase" && (typeof value !== "string" || !value.trim()))
    return { error: "invalid_field_value", field, expected: "non-empty string" };
  if (field === "owner" && value !== null && !["joe", "dell"].includes(value))
    return { error: "invalid_field_value", field, expected: "joe, dell, or null" };
  if (field === "next_date" && value !== null &&
      (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)))
    return { error: "invalid_field_value", field, expected: "YYYY-MM-DD or null" };
  if (field === "operating_state") {
    if (!value || typeof value !== "object" || Array.isArray(value) ||
        !["active", "parked"].includes(value.state))
      return { error: "invalid_field_value", field,
        expected: "{state: active|parked, reason?: prospect_never_active|client_paused|other, note?: string}" };
    if (value.state === "parked" && !PARKING_REASONS.includes(value.reason))
      return { error: "parking_reason_required", allowed: PARKING_REASONS };
    if (value.state === "active" && (value.reason != null || value.note != null))
      return { error: "active_deal_has_no_parking_reason" };
    if (value.note != null && (typeof value.note !== "string" || value.note.trim().length > 500))
      return { error: "invalid_parking_note", max_length: 500 };
  }
  return null;
}

export function validDealRoomValue(field, value) {
  return dealRoomFieldError(field, value) === null;
}

// Project only validated values, excluding any unrelated or sensitive keys.
export function dealRoomEvidenceValue(field, value) {
  if (!validDealRoomValue(field, value)) return null;
  if (field !== "operating_state") return value;
  return { state: value.state, reason: value.reason ?? null, note: value.note ?? null };
}

export function assertDealRoomField(field, value, ToolError) {
  const error = dealRoomFieldError(field, value);
  if (error) throw new ToolError(error);
}
