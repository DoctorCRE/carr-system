import { deriveJobPassports, jobPassportStatusLabel } from "./job-passport.js?v=job-passport-spatial-v1";

// MODEL ROOM OBSERVATORY — the panel Joe watches the model fleet from.
//
// Ruled by Joe on 2026-08-22 (decision 0892c539): "once you complete the desk —
// the next thing is creating a place where it can be observed by humans. I'd
// like to see all the dispatches, and communication, plus any other helpful
// information."  Placed in the Control Room family on the deal-room host so he
// pairs with the system from one app.
//
// THE SINGLE SOURCE OF TRUTH IS THE WIRE, and that is a settled architectural
// decision rather than a shortcut.  Everything on this page — the desk roster,
// each desk's liveness and sign-in state, the cursor lag, the bridge's cycle
// age, the assignments rail, the backend workers, the other partner's presence
// — is derived from partner-room turns fetched from /api/room/turns.  There is
// no second endpoint, no Worker reach into a local file, and no client-supplied
// identity anywhere.  The one fact the conversation could not carry on its own,
// "which desks exist on that Mac and were they alive", the bridge publishes
// INTO the wire as a throttled heartbeat receipt (tools/room-bridge/bridge.py).
//
// ATTRIBUTION IS SERVER-DERIVED. The composer posts a body and nothing else;
// the seat is always "human" and the sponsor comes from the authenticated
// session.  There is no seat selector on this page and there is no way to add
// one from here — see mcp-server/src/dealroom-web.js's roomTurnPost.
//
// MOTION IS A STATE CHANNEL (rules 9293d609 and eeb3d106) and it is always
// redundant: every state this file computes is rendered as colour AND shape AND
// rate, so the page reads correctly in a still screenshot, under colour assist,
// and with prefers-reduced-motion forced.  Where this file drives motion
// directly — comets, drift, parallax, count-ups — it checks REDUCED first and
// takes the static path instead, because turning the CSS off is not enough when
// JavaScript is the thing animating.

/* ------------------------------------------------------------------ palette */

// Fixed by the complete specification. Colour is never the only channel: every
// seat also carries its NAME in text wherever it appears.
export const SEAT_COLORS = {
  human: "#F08A2D",
  joe: "#F08A2D",
  claude: "#2DD496",
  opus: "#2DD496",
  sonnet: "#2DD496",
  sol: "#4E9EE8",
  codex: "#4E9EE8",
  hermes: "#9B7BE8",
  grok: "#E85BA0",
  system: "#7D8BB0",
};
export const SEAT_FALLBACK = "#7D8BB0";
export const PARTNER_HALO = { joe: "#F08A2D", dell: "#BFE3FF" };
export const HUMAN_PARTNERS = ["joe", "dell"];
export const PARTNER_LABEL = { joe: "Joe", dell: "Dell" };

export const BODY_MAX = 20000;
export const COUNTER_FROM = 18000;
export const DOM_TURN_CAP = 300;
export const PAGE_SIZE = 60;
export const POLL_VISIBLE_MS = 5000;
export const POLL_HIDDEN_MS = 30000;
export const POLL_BACKOFF_CEILING_MS = 60000;

// Thresholds, all from the specification, all in one place so the panel and its
// tests can never disagree about what "attention" means.
export const DESK_ATTENTION_S = 120;
export const DESK_URGENT_S = 600;
export const CYCLE_ATTENTION_S = 150;
export const CYCLE_URGENT_S = 600;
export const PRESENCE_ACTIVE_S = 600;
export const OUTER_RING_WINDOW_S = 24 * 60 * 60;
export const SESSION_WINDOW_S = 2 * 60 * 60;
export const SESSION_FADE_S = 45 * 60;

// Doc's staff are moons, not peers (Joe, 2026-08-25, loop 528). Doc SUMMONS the
// Hermes seats, so the sky has to say so: Doc, Claude and Codex are lead desks
// on full inner discs, and every other named profile is drawn small and orbits
// DOC'S NODE rather than the wire. What does not change: the wire stays the
// centre of the sky, Joe keeps the left hemisphere and Dell the right. Putting
// Doc in the middle would erase the partnership picture, which is the one thing
// this stage exists to show.
export const LEAD_PROFILE_KEYS = ["doc"];

export function isDocStaffProfile(profileKey) {
  const key = String(profileKey || "").toLowerCase();
  return Boolean(key) && !LEAD_PROFILE_KEYS.includes(key);
}

export function seatColor(seat) {
  return SEAT_COLORS[String(seat || "").toLowerCase()] || SEAT_FALLBACK;
}
export function partnerHalo(partner) {
  return PARTNER_HALO[String(partner || "").toLowerCase()] || SEAT_FALLBACK;
}

/* -------------------------------------------------------------- pure model */

/** The room returns bigint sequences as strings; every comparison here is
 *  numeric, so coercion happens once, at the door. */
export function seqOf(turn) {
  const value = Number(turn?.seq);
  return Number.isFinite(value) ? value : 0;
}

export function timeOf(turn) {
  const value = Date.parse(turn?.at ?? "");
  return Number.isFinite(value) ? value : 0;
}

export function relativeTime(atMs, nowMs) {
  if (!atMs) return "never";
  const seconds = Math.max(0, Math.round((nowMs - atMs) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

export function humanDuration(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

/** "33m" is a measurement; "33m ago" is a sentence. Joe's tweak-round ruling
 *  (2026-08-22): every age on this page says which direction it points. */
export function relativeAgo(atMs, nowMs) {
  if (!atMs) return "never";
  if (Math.round((nowMs - atMs) / 1000) < 5) return "just now";
  return `${relativeTime(atMs, nowMs)} ago`;
}

/** The anchor behind a relative age, for tooltips and receipt bodies — a
 *  clock time a human can cross-reference, in their own locale. */
export function absoluteTime(atMs) {
  if (!atMs) return "";
  return new Date(atMs).toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** A receipt's parsed body, or null. Receipts are the only turns whose body is
 *  a machine contract; everything else on the wire is prose and stays prose. */
export function parseReceipt(body) {
  if (typeof body !== "string" || !body.trim().startsWith("{")) return null;
  try {
    const parsed = JSON.parse(body);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function receiptKey(body) {
  const parsed = parseReceipt(body);
  const keys = parsed ? Object.keys(parsed) : [];
  return keys.length ? keys[0] : "receipt";
}

export function isHeartbeat(turn) {
  return turn?.kind === "receipt" && receiptKey(turn.body) === "heartbeat";
}

/* ----------------------------------------------- the human-legible surface */
// Joe's first-use verdict on the shipped panel (tweak-round ruling,
// 2026-08-22): "i don't feel like the data is very straightforward."  The
// receipts led with raw JSON, the row labels were machine words, and the
// header's cursor pair meant nothing without knowing the bridge protocol.
// Everything below turns a machine fact into a sentence BEFORE it reaches the
// reader; the raw contract stays available, nested one level down, because
// debugging is real too — it just never goes first.

/** Row labels for the receipt shapes this wire actually carries. Anything not
 *  listed gets its machine key humanized rather than shown raw. */
export const RECEIPT_LABELS = {
  heartbeat: "Bridge check-in",
  desk: "Desk status",
  desk_restarted: "Desk restart",
  worker_spawned: "Worker started",
  worker_completed: "Build finished",
  assignment: "Assignment",
  assignment_rejected: "Assignment refused",
  control: "Control request",
  control_refused: "Control refused",
  session_status: "Session gauge",
  // Forward hook for the named-agent-profiles build (Joe's direction,
  // 2026-08-22): persistent agent names with interchangeable models.
  agent_profile: "Agent profile",
  queue_event: "Queue milestone",
  receipt: "Receipt",
};

export function humanizeKey(key) {
  const words = String(key || "").replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Receipt";
}

export function receiptLabel(body) {
  const key = receiptKey(body);
  return RECEIPT_LABELS[key] || humanizeKey(key);
}

/** One machine value, said plainly. Ages inside a receipt are anchored to the
 *  RECEIPT's own moment, never the viewer's clock, so the text cannot rot. */
function plainValue(value, receiptAtMs) {
  if (value === true) return "yes";
  if (value === false) return "no";
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed) && /\d{4}-\d{2}-\d{2}T/.test(value)) {
      return receiptAtMs ? `${absoluteTime(parsed)} (${relativeAgo(parsed, receiptAtMs)})` : absoluteTime(parsed);
    }
    return value;
  }
  return String(value);
}

/** A machine object flattened to "Label: value" sentences — no braces, no
 *  quoted field names, nothing a human has to mentally deserialize. Depth is
 *  capped (verifier finding: unbounded recursion was a stack-overflow vector
 *  on a pathologically nested receipt); past the cap the machine-detail view
 *  still holds the whole thing. */
export function flattenReceipt(value, receiptAtMs, prefix = "", depth = 0) {
  if (value === null || value === undefined) return [];
  if (depth > 6) return [`${prefix ? `${prefix} · ` : ""}(deeper detail in the machine view)`];
  if (Array.isArray(value)) {
    return value.flatMap((entry) => flattenReceipt(entry, receiptAtMs, prefix, depth + 1));
  }
  if (typeof value !== "object") {
    return [prefix ? `${prefix}: ${plainValue(value, receiptAtMs)}` : plainValue(value, receiptAtMs)];
  }
  const lines = [];
  for (const [key, entry] of Object.entries(value)) {
    const label = humanizeKey(key);
    if (entry && typeof entry === "object") {
      lines.push(...flattenReceipt(entry, receiptAtMs, prefix ? `${prefix} · ${label}` : label, depth + 1));
    } else {
      lines.push(`${prefix ? `${prefix} · ` : ""}${label}: ${plainValue(entry, receiptAtMs)}`);
    }
  }
  return lines;
}

/** The whole receipt as sentences. Known shapes get a purpose-built telling;
 *  unknown shapes still arrive as readable lines rather than JSON. */
export function describeReceipt(body, receiptAtMs) {
  const parsed = parseReceipt(body);
  if (!parsed) return [String(body ?? "")];
  const key = receiptKey(body);
  const value = parsed[key];

  if (key === "queue_event" && value && typeof value === "object" && typeof value.summary === "string") {
    return [value.summary];
  }

  if (key === "heartbeat" && value && typeof value === "object") {
    const lines = [];
    for (const desk of Array.isArray(value.desks) ? value.desks : []) {
      if (!desk || typeof desk !== "object") continue;
      const bits = [`${desk.name || "desk"}${desk.seat ? ` (${desk.seat})` : ""}`];
      bits.push(desk.live ? "alive" : "not answering");
      if (desk.auth === true) bits.push("signed in");
      else if (desk.auth === false) bits.push("SIGNED OUT");
      const seen = Date.parse(desk.last_seen ?? "");
      if (Number.isFinite(seen)) bits.push(`seen ${relativeAgo(seen, receiptAtMs)}`);
      lines.push(bits.join(" · "));
    }
    if (Number.isFinite(Number(value.cursor))) {
      lines.push(`The bridge has delivered the wire through turn ${value.cursor}.`);
    }
    return lines.length ? lines : ["A bridge check-in with nothing to report."];
  }

  if (key === "agent_profile" && value && typeof value === "object") {
    const name = value.name || value.key || "profile";
    if (value.status === "active" && value.model) {
      return [`${name} staffed with ${value.model}${value.desk ? ` on ${value.desk}` : ""}`];
    }
    return [`${name} ${value.status || "unstaffed"}`];
  }
  if (key === "session_status" && value && typeof value === "object") {
    const pct = Number(value.context_pct);
    return [`Session "${value.name || "unnamed"}" is at ${Number.isFinite(pct) ? `${Math.round(pct)}%` : "unknown"} context${value.claimed ? `, working on: ${value.claimed}` : ", with nothing claimed"}.`];
  }

  const lines = flattenReceipt(value !== undefined ? value : parsed, receiptAtMs);
  return lines.length ? lines : ["An empty receipt."];
}

/** The header's bridge figure, as a phrase instead of a delivered/latest
 *  fraction only the protocol cares about. */
export function bridgeLagLabel(lag) {
  const value = Number(lag);
  if (lag === null || lag === undefined || !Number.isFinite(value)) return "—";
  const behind = Math.max(0, Math.round(value));
  if (behind === 0) return "caught up";
  return behind === 1 ? "1 turn behind" : `${behind} turns behind`;
}

/** A turn that is actually one model saying something — the conversation the
 *  panel's default view shows. NOOP and *(silent)* are this wire's literal
 *  keep-alive conventions, and a receipt is machine traffic by definition. */
export function isSubstantiveTurn(turn) {
  if (turn?.kind === "receipt") {
    const parsed = parseReceipt(turn.body)?.queue_event;
    // Projector bookkeeping, bootstrap creates, and comments stay in
    // Everything. Conversation gets only the moments a human needs to know:
    // work started, returned, blocked, or finished.
    return Boolean(parsed && typeof parsed.summary === "string" &&
      ["claimed", "completed", "blocked", "gave_up", "unblocked"].includes(parsed.event));
  }
  if (turn?.kind !== "turn" && turn?.kind !== "system") return false;
  const body = String(turn.body || "").trim();
  return Boolean(body) && body !== "NOOP" && body !== "*(silent)*";
}

/** A bridge receipt that reports something went wrong. Counted for the health
 *  strip's error figure — the specification's "error count from bridge
 *  receipts", enumerated rather than guessed at from the word "error". */
export function isErrorReceipt(turn) {
  if (turn?.kind !== "receipt") return false;
  const parsed = parseReceipt(turn.body);
  if (!parsed) return false;
  if (parsed.assignment_rejected || parsed.control_refused) return true;
  if (parsed.desk_restarted && parsed.desk_restarted.restarted === false) return true;
  const first = parsed[Object.keys(parsed)[0]];
  if (first && typeof first === "object") {
    if (first.timed_out_after_s !== undefined) return true;
    if (typeof first.status === "string" && first.status !== "completed") return true;
  }
  if (parsed.timed_out_after_s !== undefined) return true;
  if (typeof parsed.status === "string" && parsed.status !== "completed") return true;
  return false;
}

const WORKER_SPAWN = /worker\s+spawned/i;

/** A backend worker exists in no desktop app; the wire IS its visibility. A
 *  kind=system turn announcing a spawn names the worker by its own seat, and a
 *  later receipt under that seat closes the lifecycle. */
export function isWorkerSpawn(turn) {
  return turn?.kind === "system" && WORKER_SPAWN.test(String(turn.body || ""));
}

function fieldAfter(body, label) {
  const match = new RegExp(`${label}:\\s*([^.\\n]{1,160})`, "i").exec(String(body || ""));
  return match ? match[1].trim() : null;
}

/**
 * Everything the panel knows, computed from the turns it holds.
 *
 * Deliberately pure and deliberately total: given the same turns and the same
 * clock it returns the same model, which is what makes the thresholds testable
 * without a browser and what stops two parts of the page disagreeing about
 * whether a desk is healthy.
 */
export function deriveModel(turns, { now = Date.now(), viewer = "joe" } = {}) {
  const seats = new Map();
  const workers = new Map();
  const assignments = [];
  const sessionRows = new Map();
  const profileRows = new Map();
  let heartbeat = null;
  let heartbeatAt = 0;
  let errors = 0;
  let latestSeq = 0;

  // Named agent profiles (loop 520): the NAME persists, the model is staffing
  // detail. Truth arrives two ways — an {"agent_profile":...} receipt the
  // moment an assignment changes, and the full roster republished inside the
  // throttled heartbeat — and both flow through here, latest-at winning per
  // profile key, so a feed window is never stuck with a stale staffing.
  const upsertProfile = (raw, at) => {
    if (!raw || typeof raw.key !== "string" || !raw.key) return;
    const prior = profileRows.get(raw.key);
    if (prior && prior.at > at) return;
    profileRows.set(raw.key, {
      key: raw.key,
      name: typeof raw.name === "string" && raw.name ? raw.name : raw.key,
      model: typeof raw.model === "string" && raw.model ? raw.model : null,
      desk: typeof raw.desk === "string" && raw.desk ? raw.desk : null,
      status: typeof raw.status === "string" ? raw.status : "unstaffed",
      at,
    });
  };

  for (const turn of turns) {
    const seq = seqOf(turn);
    const at = timeOf(turn);
    const seat = String(turn.seat || "system").toLowerCase();
    const sponsor = String(turn.sponsor || "").toLowerCase();
    if (seq > latestSeq) latestSeq = seq;

    const prior = seats.get(seat);
    if (!prior || at >= prior.at) {
      seats.set(seat, { seat, at, seq, sponsor: sponsor || prior?.sponsor || "", kind: turn.kind });
    }

    if (turn.kind === "receipt") {
      const parsed = parseReceipt(turn.body);
      if (parsed?.heartbeat && at >= heartbeatAt) {
        heartbeat = parsed.heartbeat;
        heartbeatAt = at;
      }
      if (Array.isArray(parsed?.heartbeat?.profiles)) {
        for (const profile of parsed.heartbeat.profiles) upsertProfile(profile, at);
      }
      if (parsed?.agent_profile) upsertProfile(parsed.agent_profile, at);
      if (parsed?.assignment) {
        assignments.push({ ...parsed.assignment, at, seq, status: parsed.status || null });
      }
      // A relay session's own status, parsed exactly like the other two
      // machine-readable receipt shapes. Latest wins per session name; the
      // window and the fade are applied below, after the whole page is read.
      if (parsed?.session_status && typeof parsed.session_status.name === "string") {
        const status = parsed.session_status;
        const prior = sessionRows.get(status.name);
        if (!prior || at >= prior.at) {
          sessionRows.set(status.name, { name: status.name,
            contextPct: Number(status.context_pct),
            claimed: typeof status.claimed === "string" ? status.claimed : null,
            at, partner: sponsor || viewer });
        }
      }
      if (isErrorReceipt(turn)) errors += 1;
      const worker = workers.get(seat);
      if (worker && !worker.completedAt && seq > worker.seq) {
        worker.completedAt = at;
        worker.completedSeq = seq;
      }
    }

    if (isWorkerSpawn(turn)) {
      workers.set(seat, {
        seat, at, seq, sponsor,
        mission: fieldAfter(turn.body, "Mission"),
        executor: fieldAfter(turn.body, "Executor"),
        completedAt: null,
      });
    }
  }

  const desks = Array.isArray(heartbeat?.desks) ? heartbeat.desks : [];
  const deskSeats = new Set(desks.map((d) => String(d.seat || "").toLowerCase()).filter(Boolean));
  const heartbeatSponsor = seats.get("hermes")?.sponsor || viewer;

  // A desk learns its profile from either side of the wire: the record layer
  // (profile.desk names this desk) or the local registry (the desk row carries
  // a profile key). The record layer wins when both speak, because staffing is
  // its recorded act.
  const profiles = [...profileRows.values()].sort((a, b) => a.key.localeCompare(b.key));
  const profileByDesk = new Map();
  for (const profile of profiles) {
    if (profile.desk) profileByDesk.set(profile.desk, profile);
  }

  const roster = desks.map((desk) => {
    const seat = String(desk.seat || "").toLowerCase();
    const activity = seat ? seats.get(seat) : null;
    const lastSeen = Date.parse(desk.last_seen ?? "") || 0;
    const seenAt = Math.max(lastSeen, activity?.at || 0);
    const name = String(desk.name || "desk");
    const boundKey = typeof desk.profile === "string" ? desk.profile : null;
    const profile = profileByDesk.get(name)
      || (boundKey ? profileRows.get(boundKey) : null) || null;
    return {
      name,
      seat: seat || null,
      live: desk.live === true,
      auth: typeof desk.auth === "boolean" ? desk.auth : null,
      seenAt,
      partner: activity?.sponsor || heartbeatSponsor,
      profile,
      state: deskState({ seat, live: desk.live === true, auth: desk.auth, seenAt }, now),
    };
  });

  // The outer ring: seats the wire has heard from in the last day that have no
  // desk of their own, plus every backend worker (which never has one).
  const orbiters = [];
  for (const entry of seats.values()) {
    if (!entry.seat || entry.seat === "system") continue;
    if (deskSeats.has(entry.seat)) continue;
    if ((now - entry.at) / 1000 > OUTER_RING_WINDOW_S) continue;
    const worker = workers.get(entry.seat);
    orbiters.push({
      seat: entry.seat,
      partner: entry.sponsor || viewer,
      seenAt: entry.at,
      worker: worker || null,
      state: worker
        ? (worker.completedAt ? "dormant" : "healthy")
        : ((now - entry.at) / 1000 <= PRESENCE_ACTIVE_S ? "healthy" : "dormant"),
    });
  }
  orbiters.sort((a, b) => b.seenAt - a.seenAt);

  const other = String(viewer).toLowerCase() === "dell" ? "joe" : "dell";
  let presenceAt = 0;
  for (const turn of turns) {
    if (String(turn.sponsor || "").toLowerCase() === other) presenceAt = Math.max(presenceAt, timeOf(turn));
  }

  // A session that has not reported in two hours is gone, not quiet; one past
  // forty-five minutes is still listed but visibly faded, because a stale gauge
  // presented as current is the whole failure mode this list exists to avoid.
  const sessions = [...sessionRows.values()]
    .filter((row) => (now - row.at) / 1000 <= SESSION_WINDOW_S)
    .map((row) => ({ ...row, state: sessionState(row.contextPct),
      stale: (now - row.at) / 1000 > SESSION_FADE_S }))
    .sort((a, b) => (b.contextPct || 0) - (a.contextPct || 0));

  const cycleAgeS = heartbeatAt ? (now - heartbeatAt) / 1000 : null;
  const cursor = Number(heartbeat?.cursor);

  return {
    now, viewer, other,
    heartbeatAt,
    cycleAgeS,
    cursor: Number.isFinite(cursor) ? cursor : null,
    latestSeq,
    cursorLag: Number.isFinite(cursor) ? Math.max(0, latestSeq - cursor) : null,
    errors,
    desks: roster,
    profiles,
    orbiters,
    workers,
    assignments: assignments.sort((a, b) => b.seq - a.seq),
    sessions,
    onlineDesks: roster.filter((d) => d.live).length,
    signedIn: roster.filter((d) => d.auth === true).length,
    authKnown: roster.filter((d) => d.auth !== null).length,
    presence: { partner: other, at: presenceAt,
      state: presenceAt && (now - presenceAt) / 1000 <= PRESENCE_ACTIVE_S ? "healthy" : "dormant" },
    // Feature-gated: no typed projection, no Job Passport panel. The page
    // never attempts to infer a job from prose or a raw transcript.
    jobPassports: deriveJobPassports(turns, { now }),
  };
}

/**
 * THE PULSE SCALE, applied to one desk. Faster always means more urgent.
 *
 * A desk with no room seat has no wire at all and is dormant — still, hollow,
 * asking nothing of anybody.  A desk the bridge reports SIGNED OUT is urgent
 * whatever its clock says, because it cannot answer a single turn until a human
 * signs it back in; that is the whole point of surfacing sign-in state.
 *
 * The specification's third attention clause — a delivery in flight longer than
 * five minutes — is deliberately not implemented: in-flight duration is desk
 * state the heartbeat does not publish, and inventing it from the feed would be
 * a guess dressed as a measurement.  A timed-out delivery does reach the panel,
 * as an error receipt on the health strip.
 */
export function deskState(desk, now = Date.now()) {
  if (!desk.seat) return "dormant";
  if (desk.auth === false) return "urgent";
  if (desk.live !== true) return "urgent";
  if (!desk.seenAt) return "urgent";
  const seconds = (now - desk.seenAt) / 1000;
  if (seconds > DESK_URGENT_S) return "urgent";
  if (seconds > DESK_ATTENTION_S) return "attention";
  return "healthy";
}

export function cycleState(cycleAgeS) {
  if (cycleAgeS === null || cycleAgeS === undefined) return "urgent";
  if (cycleAgeS > CYCLE_URGENT_S) return "urgent";
  if (cycleAgeS > CYCLE_ATTENTION_S) return "attention";
  return "healthy";
}

/** Cursor lag only earns attention when it PERSISTS: one poll ahead of the
 *  bridge is the normal shape of a wire being read every five seconds by a
 *  browser and every minute by launchd. */
export function lagState(lag, consecutive) {
  if (lag === null || lag === undefined) return "dormant";
  if (lag === 0) return "healthy";
  return consecutive >= 2 ? "attention" : "healthy";
}

export function errorState(count, previous) {
  if (!count) return "healthy";
  return previous !== null && count > previous ? "urgent" : "attention";
}

/**
 * A relay session's context burn, on the same pulse scale as everything else.
 *
 * Joe's reason for the gauge: no agent burns tokens he cannot track, and the
 * fifty-percent handoff contract has to be VISIBLE or it is not a contract. So
 * fifty is where the row starts asking for attention and seventy-five is where
 * it starts shouting — a session past seventy-five is the loudest thing on the
 * rail, which is the point.
 */
export function sessionState(contextPct) {
  const value = Number(contextPct);
  if (!Number.isFinite(value)) return "dormant";
  if (value > 75) return "urgent";
  if (value >= 50) return "attention";
  return "healthy";
}

export function authState(signedIn, known, total) {
  if (!total || !known) return "dormant";
  return signedIn === known ? "healthy" : "urgent";
}

/** Filters compose: a turn survives only if every active filter admits it.
 *  Conversation mode REPLACES the kind filters rather than stacking on them:
 *  its controls are hidden there, and a hidden control that still filters is
 *  how the default view ends up saying "no conversation" over a suppressed
 *  one (the verifier reproduced exactly that). Seats and text still apply. */
/* --------------------------------------------------- the stage's node list */

// Pure, and deliberately so: the sky's hierarchy is the part most worth proving
// without a browser, because a picture that says the wrong thing about who
// summons whom is wrong silently (loop 528).
export function stageNodes(model) {
  const nodes = [];
  const boundKeys = new Set();
  for (const desk of model.desks) {
    if (!desk.seat) continue;
    // Named agent profiles (loop 520): where a profile is bound to a desk,
    // the PROFILE NAME is the primary label and the model staffing it is
    // the sub-line — the name persists, the model is visible detail.
    const profile = desk.profile || null;
    if (profile) boundKeys.add(profile.key);
    // A desk staffed by one of Doc's staff profiles is a MOON, however it got
    // its runtime: the hierarchy follows the profile, not the desk row.
    const moon = Boolean(profile) && isDocStaffProfile(profile.key);
    nodes.push({ id: `desk:${desk.name}`, ring: moon ? "moon" : "inner",
      profileKey: profile ? profile.key : null, seat: desk.seat, partner: desk.partner,
      label: profile ? profile.name : desk.name, state: desk.state, worker: false, seenAt: desk.seenAt,
      auth: desk.auth,
      sub: profile && profile.model
        ? (desk.auth === false ? `${profile.model} · signed out` : profile.model)
        : (desk.auth === false ? "signed out" : (desk.live ? "live wire" : "no live wire")) });
  }
  for (const orbiter of model.orbiters) {
    nodes.push({ id: `seat:${orbiter.seat}`, ring: "outer", profileKey: null,
      seat: orbiter.seat, partner: orbiter.partner,
      label: orbiter.worker ? `${orbiter.seat} · worker` : orbiter.seat, state: orbiter.state,
      worker: Boolean(orbiter.worker), seenAt: orbiter.seenAt, auth: null,
      sub: orbiter.worker
        ? (orbiter.worker.completedAt ? "worker · finished" : "worker · running")
        : "seen on the wire" });
  }
  // Identities without a desk still exist — that is the point of a persistent
  // name (Doc stays visible while parked, months before its runtime), and they
  // keep their place in the hierarchy while parked: Doc on the inner ring with
  // the lead desks, its staff as moons around it. Only the staffing state,
  // carried in the sub-line, says the runtime is not there yet.
  for (const profile of model.profiles || []) {
    if (boundKeys.has(profile.key)) continue;
    nodes.push({ id: `profile:${profile.key}`,
      ring: isDocStaffProfile(profile.key) ? "moon" : "inner",
      profileKey: profile.key, seat: profile.key,
      partner: model.viewer, label: profile.name,
      state: profile.status === "active" ? "healthy" : "dormant",
      worker: false, seenAt: profile.at || 0, auth: null,
      sub: profile.model || profile.status });
  }
  return nodes;
}

export function turnPasses(turn, filters) {
  if (filters.conversation) {
    if (!isSubstantiveTurn(turn)) return false;
  } else {
    if (turn.kind === "receipt") {
      if (!filters.receipts) return false;
      if (isHeartbeat(turn) && !filters.heartbeats) return false;
    }
    if (turn.kind === "turn" && !filters.turns) return false;
    if (turn.kind === "system" && !filters.system) return false;
  }
  if (filters.seats.size && !filters.seats.has(String(turn.seat || "").toLowerCase())) return false;
  if (filters.text) {
    const haystack = `${turn.seat} ${turn.sponsor} ${turn.body}`.toLowerCase();
    if (!haystack.includes(filters.text)) return false;
  }
  return true;
}

/* ------------------------------------------------------------------ the page */

const isBrowser = typeof document !== "undefined" && typeof window !== "undefined";

function boot() {
  const $ = (id) => document.getElementById(id);
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const REDUCED = () => reduced.matches;

  const state = {
    turns: [],
    byMsgId: new Map(),
    pending: new Map(),
    cursor: 0,
    oldestSeq: null,
    latestSeqHint: null,
    model: null,
    viewer: "joe",
    csrf: null,
    following: true,
    missed: 0,
    lagStreak: 0,
    lastErrors: null,
    pollMs: POLL_VISIBLE_MS,
    backoffMs: null,
    filters: { seats: new Set(), turns: true, system: true, receipts: true, heartbeats: false, text: "", conversation: true },
    focusSeat: null,
    timer: null,
    counted: new Map(),
  };

  /* --------------------------------------------------------- small helpers */

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /**
   * KEYED RECONCILIATION, and it is a correctness requirement here rather than a
   * performance nicety.
   *
   * This page re-derives its whole model every five seconds. Rebuilding the DOM
   * from that model each time looks harmless and is not: a replaced element
   * restarts its CSS animation, so every pulse on the page would stutter back to
   * the start of its breath on every poll — the exact thing the pulse scale is
   * supposed to communicate. It would also collapse any receipt the reader had
   * expanded, drop the feed's scroll position, and throw away hover state
   * mid-gesture.
   *
   * So: match by key, update in place, create only what is new, remove only what
   * is gone, and reorder without detaching anything that is already correct.
   */
  function reconcile(host, items, { key, create, update }) {
    const existing = new Map();
    for (const child of host.children) {
      if (child.dataset.key !== undefined) existing.set(child.dataset.key, child);
    }
    const wanted = items.map((item) => {
      const id = key(item);
      let node = existing.get(id);
      if (node) { existing.delete(id); update?.(node, item); } else {
        node = create(item);
        node.dataset.key = id;
        // New keyed rows need the same data pass as retained rows. Most
        // surfaces build their static skeleton in create(), while Job Passport
        // cards intentionally keep all live state in update(); doing both
        // keeps first paint and later polls identical.
        update?.(node, item);
      }
      return node;
    });
    for (const stale of existing.values()) stale.remove();
    // Place each node at its index only when it is not already there, so an
    // unchanged list performs zero DOM mutations.
    wanted.forEach((node, index) => {
      if (host.children[index] !== node) host.insertBefore(node, host.children[index] || null);
    });
    while (host.children.length > wanted.length) host.lastElementChild.remove();
  }

  function svg(tag, attrs = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value !== null && value !== undefined) node.setAttribute(name, String(value));
    }
    return node;
  }

  /** Values announce themselves. A figure that changes silently is a figure a
   *  partner never notices changing — which is the whole complaint the
   *  live-surface rule exists to answer. */
  function countTo(node, value, format = String) {
    if (!node) return;
    const target = Number(value);
    if (!Number.isFinite(target)) { node.textContent = format(value); return; }
    const from = state.counted.get(node.id ?? node) ?? target;
    state.counted.set(node.id ?? node, target);
    if (REDUCED() || from === target) { node.textContent = format(target); return; }
    const started = performance.now();
    const step = (stamp) => {
      const t = Math.min(1, (stamp - started) / 300);
      const eased = 1 - (1 - t) * (1 - t);
      node.textContent = format(Math.round(from + (target - from) * eased));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function setState(node, value) {
    if (node) node.dataset.state = value;
  }

  /** The text-figure version of countTo: a phrase cannot count up, so it
   *  announces its change with a brief brightening instead. Reduced motion
   *  turns the flash off in CSS; the text change itself is the information. */
  function announce(node, text) {
    if (!node || node.textContent === text) return;
    node.textContent = text;
    node.classList.remove("is-changed");
    void node.offsetWidth;
    node.classList.add("is-changed");
  }

  function seatChip(seat, extra) {
    const chip = el("span", "seat-chip", String(seat || "system"));
    chip.style.setProperty("--seat", seatColor(seat));
    if (extra) chip.classList.add(extra);
    return chip;
  }

  /* ------------------------------------------------------------- transport */

  async function fetchTurns(afterSeq, limit = PAGE_SIZE) {
    const response = await fetch(`/api/room/turns?after_seq=${afterSeq}&limit=${limit}`, {
      headers: { accept: "application/json" }, credentials: "same-origin",
    });
    if (response.status === 401) { window.location.href = "/auth/login?return_to=/room.html"; throw new Error("sign_in_required"); }
    if (!response.ok) throw new Error(`turns_${response.status}`);
    return response.json();
  }

  async function postTurn(payload) {
    const response = await fetch("/api/room/turn", {
      method: "POST", credentials: "same-origin",
      headers: { "content-type": "application/json", "x-carr-csrf": state.csrf || "" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(data.error || `post_${response.status}`), { data });
    return data;
  }

  /* ------------------------------------------------------------ the stage */

  const CENTER = { x: 600, y: 122 };
  // Two geometries for one composition. On a phone the stage is a 160px band,
  // and a 1200-wide viewBox would scale every node down to a speck — so the
  // rings pull in and the viewBox crops to the populated middle. Same nodes,
  // same connectors, same code: only the radii and the window change.
  const RINGS_WIDE = { inner: { rx: 250, ry: 70 }, outer: { rx: 470, ry: 100 } };
  const RINGS_TIGHT = { inner: { rx: 105, ry: 42 }, outer: { rx: 185, ry: 66 } };
  // The moon orbit is measured from DOC'S node, not from the wire, so it is a
  // small local ellipse rather than a third ring. Wide enough that a moon's
  // pulse (r 11) clears Doc's (r 22) on the vertical, which is the tight axis.
  const MOONS_WIDE = { rx: 58, ry: 36 };
  const MOONS_TIGHT = { rx: 46, ry: 32 };
  // A moon is drawn smaller again on the phone stage, because the tight orbit
  // has to clear Doc's own pulse ring in a band a third the height.
  const MOON_R = { pulse: 11, disc: 8, label: 19, badge: 7 };
  const MOON_R_TIGHT = { pulse: 8, disc: 6, label: 15, badge: 5 };
  const moonRadii = () => (isCompact() ? MOON_R_TIGHT : MOON_R);
  const VIEWBOX_WIDE = "0 6 1200 232";
  const VIEWBOX_TIGHT = "376 36 448 188";
  const nodeIndex = new Map();

  const isCompact = () => window.innerWidth <= 720;
  const rings = () => (isCompact() ? RINGS_TIGHT : RINGS_WIDE);

  function ringPoint(hemisphere, ring, index, total) {
    // Left hemisphere sweeps 120°→240°, right sweeps −60°→60°; each node sits
    // on its own slice so nothing collides however many arrive.
    const span = 120;
    const base = hemisphere === "left" ? 120 : -60;
    const angle = (base + (span * (index + 1)) / (total + 1)) * (Math.PI / 180);
    return { x: CENTER.x + ring.rx * Math.cos(angle), y: CENTER.y + ring.ry * Math.sin(angle) };
  }

  function moonPoint(anchor, index, total) {
    // A full sweep, starting above the node, so seven staff sit evenly around
    // Doc instead of stacking on one side the way a 120° hemisphere slice would.
    const moons = isCompact() ? MOONS_TIGHT : MOONS_WIDE;
    const angle = (-90 + (360 * index) / Math.max(total, 1)) * (Math.PI / 180);
    return { x: anchor.x + moons.rx * Math.cos(angle), y: anchor.y + moons.ry * Math.sin(angle) };
  }

  function connectorPath(point) {
    const midX = (CENTER.x + point.x) / 2;
    return `M ${CENTER.x} ${CENTER.y} C ${midX} ${CENTER.y}, ${midX} ${point.y}, ${point.x} ${point.y}`;
  }

  function moonConnectorPath(anchor, point) {
    // A tether to Doc, not to the wire — the whole point of the change.
    return `M ${anchor.x.toFixed(1)} ${anchor.y.toFixed(1)} L ${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
  }

  function drawCore() {
    const core = $("stageCore");
    core.replaceChildren();
    core.appendChild(svg("circle", { class: "stage-core-halo", cx: CENTER.x, cy: CENTER.y, r: 62 }));
    core.appendChild(svg("circle", { class: "stage-core-disc", cx: CENTER.x, cy: CENTER.y, r: 28 }));
    const label = svg("text", { class: "stage-core-label", x: CENTER.x, y: CENTER.y + 52 });
    label.textContent = "THE WIRE";
    core.appendChild(label);
    const spark = svg("g", { class: "stage-spark-orbit" });
    spark.appendChild(svg("circle", { class: "stage-spark", cx: CENTER.x + 44, cy: CENTER.y, r: 4 }));
    core.appendChild(spark);
    const hit = svg("circle", { class: "stage-core-hit", cx: CENTER.x, cy: CENTER.y, r: 30 });
    hit.addEventListener("click", clearFilters);
    const hitTitle = svg("title");
    hitTitle.textContent = "The shared wire — click to clear every filter";
    hit.appendChild(hitTitle);
    core.appendChild(hit);

    $("stageSvg").setAttribute("viewBox", isCompact() ? VIEWBOX_TIGHT : VIEWBOX_WIDE);
    const guides = $("stageGuides");
    guides.replaceChildren();
    for (const ring of [rings().inner, rings().outer]) {
      guides.appendChild(svg("ellipse", { class: "stage-guide", cx: CENTER.x, cy: CENTER.y, rx: ring.rx, ry: ring.ry }));
    }
    const edge = isCompact() ? [400, 800] : [24, 1176];
    const labelY = isCompact() ? 56 : 22;
    for (const [partner, x, anchor] of [["joe", edge[0], "start"], ["dell", edge[1], "end"]]) {
      const text = svg("text", { class: "stage-hemi-label", x, y: labelY, "text-anchor": anchor });
      text.textContent = PARTNER_LABEL[partner];
      text.style.setProperty("fill", partnerHalo(partner));
      guides.appendChild(text);
    }
  }

  let stageLayout = "";

  function renderStage(model) {
    const nodes = stageNodes(model);
    // THE SKY IS ONLY REDRAWN WHEN ITS SHAPE CHANGES. A node's position, its
    // drift phase and its halo animation all live in the element; rebuilding
    // them every five seconds would reset every orbit to the same starting
    // point and break the comets mid-flight. When only STATE changed — a desk
    // going urgent, a worker finishing — the existing nodes are updated where
    // they stand.
    const layout = nodes.map((n) => `${n.id}|${n.ring}|${n.partner}`).join(",") + `|${isCompact()}`;
    if (layout === stageLayout) {
      for (const node of nodes) {
        const entry = nodeIndex.get(node.id);
        if (!entry) continue;
        entry.node = node;
        const pulse = $("stageNodes").querySelector(`[data-node="${CSS.escape(node.id)}"] .stage-node-pulse`);
        if (pulse && pulse.dataset.state !== node.state) pulse.dataset.state = node.state;
      }
      return;
    }
    stageLayout = layout;

    const connectors = $("stageConnectors");
    const nodeLayer = $("stageNodes");
    connectors.replaceChildren();
    nodeLayer.replaceChildren();
    nodeIndex.clear();

    // Moons are placed LAST and separately, because their orbit is measured from
    // Doc's node — which does not have a position until the rings are laid out.
    const moons = nodes.filter((node) => node.ring === "moon");
    const buckets = { "left:inner": [], "left:outer": [], "right:inner": [], "right:outer": [] };
    for (const node of nodes) {
      if (node.ring === "moon") continue;
      const hemisphere = String(node.partner).toLowerCase() === "dell" ? "right" : "left";
      buckets[`${hemisphere}:${node.ring}`].push({ ...node, hemisphere });
    }

    const groups = {
      inner: { connectors: svg("g", { class: "stage-ring-depth stage-ring-inner" }), nodes: svg("g", { class: "stage-ring-depth stage-ring-inner" }) },
      outer: { connectors: svg("g", { class: "stage-ring-depth stage-ring-outer" }), nodes: svg("g", { class: "stage-ring-depth stage-ring-outer" }) },
    };

    for (const [key, list] of Object.entries(buckets)) {
      const [hemisphere, ring] = key.split(":");
      list.forEach((node, index) => {
        const point = ringPoint(hemisphere, rings()[ring], index, list.length);
        const path = svg("path", { class: `stage-connector${ring === "outer" ? " is-outer" : ""}`,
          d: connectorPath(point), "data-node": node.id });
        groups[ring].connectors.appendChild(path);
        groups[ring].nodes.appendChild(nodeElement(node, point));
        nodeIndex.set(node.id, { node, point, path });
      });
    }

    // Doc's anchor: whichever node currently carries the doc profile, desk-bound
    // or parked. If Doc is not on the sky at all its staff would have nothing to
    // orbit, so they fall back to the wire's outer ring — visible and honest,
    // rather than silently dropped.
    const docAnchor = [...nodeIndex.values()].find((entry) => entry.node.profileKey === "doc") || null;
    moons.forEach((node, index) => {
      const point = docAnchor
        ? moonPoint(docAnchor.point, index, moons.length)
        : ringPoint(String(node.partner).toLowerCase() === "dell" ? "right" : "left",
            rings().outer, index, moons.length);
      const path = svg("path", { class: `stage-connector${docAnchor ? " is-moon" : " is-outer"}`,
        d: docAnchor ? moonConnectorPath(docAnchor.point, point) : connectorPath(point),
        "data-node": node.id });
      // Moons ride the INNER depth group with Doc: parallax must move a moon and
      // the node it orbits by the same amount, or the staff drift off their star.
      const ring = docAnchor ? "inner" : "outer";
      groups[ring].connectors.appendChild(path);
      groups[ring].nodes.appendChild(nodeElement(node, point));
      nodeIndex.set(node.id, { node, point, path });
    });

    connectors.append(groups.inner.connectors, groups.outer.connectors);
    nodeLayer.append(groups.inner.nodes, groups.outer.nodes);
  }

  function nodeElement(node, point) {
    const anchor = svg("g", { transform: `translate(${point.x.toFixed(1)} ${point.y.toFixed(1)})` });
    const drift = svg("g", { class: "stage-node-drift" });
    // Offset periods so the sky never syncs — a hash of the node id keeps a
    // given desk's rhythm stable across re-renders instead of jumping.
    const hash = [...node.id].reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
    drift.style.setProperty("--drift-period", `${45 + (hash % 31)}s`);
    drift.style.setProperty("--drift-delay", `-${hash % 40}s`);

    const moon = node.ring === "moon";
    const group = svg("g", { class: `stage-node${node.ring === "outer" ? " is-outer" : ""}${moon ? " is-moon" : ""}`,
      tabindex: "0", role: "button", "data-node": node.id });
    group.style.setProperty("--node-seat", seatColor(node.seat));
    group.style.setProperty("--node-halo", partnerHalo(node.partner));

    // Size carries the hierarchy: a lead desk is a full disc, a staff seat Doc
    // summons is a moon. Eight same-size discs was the lie this replaces.
    const mr = moonRadii();
    group.appendChild(svg("circle", { class: "stage-node-pulse", r: moon ? mr.pulse : 22, "data-state": node.state }));
    group.appendChild(svg("circle", { class: "stage-node-disc", r: moon ? mr.disc : 17 }));
    if (node.worker) {
      group.appendChild(svg("rect", { class: "stage-node-worker", x: -5, y: -5, width: 10, height: 10, rx: 1 }));
    } else if (moon) {
      // A moon's disc is too small to hold text, but colour is never the only
      // channel — the name goes UNDER the moon instead of inside it.
      const label = svg("text", { class: "stage-node-glyph stage-moon-name", y: mr.label });
      label.textContent = String(node.label || node.seat || "?").slice(0, 14).toLowerCase();
      group.appendChild(label);
    } else {
      // The full seat name, not an initial: claude and codex were both a "C",
      // and a picture that needs prior knowledge to disambiguate is not a
      // picture (Joe's tweak-round ruling, 2026-08-22).
      const glyph = svg("text", { class: "stage-node-glyph stage-node-name", y: 1 });
      glyph.textContent = String(node.seat || "?").slice(0, 7).toLowerCase();
      group.appendChild(glyph);
    }
    group.appendChild(svg("circle", { class: "stage-node-badge",
      cx: moon ? mr.badge : 14, cy: moon ? -mr.badge : -13, r: moon ? 2.5 : 4 }));
    if (node.auth === false) {
      const badge = svg("text", { class: "stage-node-glyph", y: 30 });
      badge.textContent = "⛓";
      group.appendChild(badge);
      const chip = svg("text", { class: "stage-hemi-label", y: 44, "text-anchor": "middle" });
      chip.textContent = "RECONNECT";
      chip.style.setProperty("fill", "#F08A2D");
      chip.style.setProperty("cursor", "pointer");
      chip.addEventListener("click", (event) => { event.stopPropagation(); requestReconnect(node.label); });
      group.appendChild(chip);
    }

    const title = svg("title");
    title.textContent = `${node.label} · ${PARTNER_LABEL[node.partner] || node.partner} · ${node.state}`;
    group.appendChild(title);

    group.addEventListener("pointerenter", () => showTooltip(node, point));
    group.addEventListener("focus", () => showTooltip(node, point));
    group.addEventListener("pointerleave", hideTooltip);
    group.addEventListener("blur", hideTooltip);
    group.addEventListener("click", () => toggleSeatFilter(node.worker ? node.seat : node.seat));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleSeatFilter(node.seat); }
    });
    drift.appendChild(group);
    anchor.appendChild(drift);
    return anchor;
  }

  function showTooltip(node, point) {
    const tip = $("stageTooltip");
    tip.replaceChildren();
    tip.appendChild(el("strong", null, node.label));
    tip.appendChild(el("span", null, `${PARTNER_LABEL[node.partner] || node.partner} · seat ${node.seat}`));
    tip.appendChild(el("span", null, `${node.state} · ${node.sub}`));
    tip.appendChild(el("span", null, `seen ${relativeAgo(node.seenAt, Date.now())}`));
    if (node.auth === false) tip.appendChild(el("span", null, "signed out — reconnect to restore it"));
    const box = $("roomStage").getBoundingClientRect();
    const scale = box.width / 1200;
    tip.hidden = false;
    tip.style.setProperty("left", `${Math.min(Math.max(point.x * scale - 80, 8), box.width - 250)}px`);
    tip.style.setProperty("top", `${Math.max(8, point.y * scale + 26)}px`);
    highlightSeat(node.seat, true);
  }

  function hideTooltip() {
    $("stageTooltip").hidden = true;
    highlightSeat(null, false);
  }

  function highlightSeat(seat, on) {
    for (const card of $("wireFeed").querySelectorAll("[data-seat]")) {
      card.classList.toggle("is-highlight", Boolean(on) && card.dataset.seat === seat);
    }
  }

  /* --------------------------------------------------------------- comets */

  const cometCounts = new Map();

  function fireComet(nodeId, direction, color, arc = false) {
    if (REDUCED()) return litBadge(nodeId);
    const entry = nodeIndex.get(nodeId);
    if (!entry) return;
    const running = cometCounts.get(nodeId) || 0;
    if (running >= 3) return;
    cometCounts.set(nodeId, running + 1);

    const path = entry.path;
    const length = path.getTotalLength();
    const layer = $("stageComets");
    const comet = svg("g", { class: `comet${arc ? " is-arc" : ""}` });
    comet.style.setProperty("--comet", color);
    const head = svg("circle", { class: "comet-head", r: arc ? 5.5 : 4 });
    const trail = [svg("circle", { class: "comet-trail", r: 3, "fill-opacity": 0.45 }),
      svg("circle", { class: "comet-trail", r: 2, "fill-opacity": 0.2 })];
    comet.append(head, ...trail);
    layer.appendChild(comet);

    const duration = arc ? 2200 : 1800;
    const started = performance.now();
    const step = (stamp) => {
      const t = Math.min(1, (stamp - started) / duration);
      const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const at = direction === "out" ? eased : 1 - eased;
      const place = (circle, lag) => {
        const point = path.getPointAtLength(Math.min(length, Math.max(0, (at + lag) * length)));
        circle.setAttribute("cx", point.x.toFixed(2));
        circle.setAttribute("cy", point.y.toFixed(2));
      };
      place(head, 0);
      place(trail[0], direction === "out" ? -0.035 : 0.035);
      place(trail[1], direction === "out" ? -0.07 : 0.07);
      if (t < 1) { requestAnimationFrame(step); return; }
      comet.remove();
      cometCounts.set(nodeId, Math.max(0, (cometCounts.get(nodeId) || 1) - 1));
      litBadge(nodeId);
    };
    requestAnimationFrame(step);
  }

  /** The reduced-motion delivery cue: a brief solid brightening of the node's
   *  endpoint badge, with no movement at all. */
  function litBadge(nodeId) {
    const entry = nodeIndex.get(nodeId);
    const badge = entry && $("stageNodes").querySelector(`[data-node="${CSS.escape(nodeId)}"] .stage-node-badge`);
    if (!badge) return;
    badge.classList.add("is-lit");
    setTimeout(() => badge.classList.remove("is-lit"), 900);
  }

  function nodeIdForSeat(seat) {
    for (const [id, entry] of nodeIndex) {
      if (entry.node.seat === seat) return id;
    }
    return null;
  }

  /** Every new turn is a delivery on the stage: outbound from the room when a
   *  person or a council seat speaks, inbound to the room when a desk answers,
   *  and a full-stage arc — the brightest thing on the page — when one
   *  partner's side answers the other's. */
  function animateArrivals(fresh, model) {
    if (!fresh.length) return;
    for (const turn of fresh) {
      const seat = String(turn.seat || "").toLowerCase();
      const sponsor = String(turn.sponsor || "").toLowerCase();
      if (seat === "human") {
        for (const desk of model.desks) {
          if (desk.seat && desk.state !== "dormant") fireComet(`desk:${desk.name}`, "out", PARTNER_HALO.joe);
        }
        continue;
      }
      const id = nodeIdForSeat(seat);
      if (!id) continue;
      const entry = nodeIndex.get(id);
      const crossPartner = entry && String(entry.node.partner).toLowerCase() !== sponsor && sponsor !== "";
      fireComet(id, "in", seatColor(seat), Boolean(crossPartner));
    }
  }

  /* ---------------------------------------------------------------- desks */

  function deskSubLine(desk, now) {
    const bits = [];
    if (!desk.seat) bits.push("no wire registered");
    else if (desk.auth === false) bits.push("signed out");
    else if (!desk.live) bits.push("not live");
    else bits.push("idle");
    if (desk.auth === null && desk.seat) bits.push("sign-in unknown");
    bits.push(`seen ${relativeAgo(desk.seenAt, now)}`);
    return bits.join(" · ");
  }

  function renderDesks(model) {
    const host = $("deskList");
    if (!model.desks.length) {
      host.replaceChildren(el("p", "desk-empty",
        "No desk roster on the wire yet. The bridge publishes one every five minutes; until then this column stays honest and empty."));
      return;
    }
    // One flat, keyed list of partner headers and their cards. Flat because the
    // grouping is a rendering detail and reconciling nested lists would buy
    // nothing: the key already carries the partner.
    const items = [];
    for (const partner of HUMAN_PARTNERS) {
      const mine = model.desks.filter((d) => String(d.partner).toLowerCase() === partner);
      if (!mine.length) continue;
      items.push({ kind: "group", partner, count: mine.length });
      for (const desk of mine) items.push({ kind: "desk", desk });
    }
    reconcile(host, items, {
      key: (item) => (item.kind === "group" ? `g:${item.partner}` : `d:${item.desk.name}`),
      create: (item) => (item.kind === "group" ? deskGroupHead(item) : deskCard(item.desk, model.now)),
      update: (node, item) => {
        if (item.kind === "group") { node.textContent = `${PARTNER_LABEL[item.partner]} · ${item.count}`; return; }
        updateDeskCard(node, item.desk, model.now);
      },
    });
  }

  function deskGroupHead(item) {
    const head = el("div", "desk-group-head", `${PARTNER_LABEL[item.partner]} · ${item.count}`);
    head.style.setProperty("--halo", partnerHalo(item.partner));
    return head;
  }

  function deskCard(desk, now) {
    const card = el("button", "desk-card");
    card.type = "button";

    const dot = el("span", "dot");
    card.appendChild(dot);

    const body = el("div");
    const name = el("div", "desk-name");
    name.appendChild(el("b", null, ""));
    name.appendChild(document.createTextNode(""));
    body.appendChild(name);
    body.appendChild(el("div", "desk-sub"));
    const reconnect = el("span", "assignment-badge", "⛓ signed out — RECONNECT");
    reconnect.style.setProperty("color", "#F08A2D");
    reconnect.style.setProperty("border-color", "#F08A2D");
    reconnect.hidden = true;
    body.appendChild(reconnect);
    card.appendChild(body);

    // The card's two actions are bound ONCE, on creation, and read the desk's
    // current identity from the element rather than from a captured closure —
    // otherwise every update would have to rebind and the listeners would pile up.
    card.addEventListener("click", (event) => {
      if (card.dataset.auth === "false" && event.target.closest(".assignment-badge")) {
        event.preventDefault();
        requestReconnect(card.dataset.desk);
        return;
      }
      toggleSeatFilter(card.dataset.seat || null);
    });
    updateDeskCard(card, desk, now);
    return card;
  }

  function updateDeskCard(card, desk, now) {
    card.dataset.desk = desk.name;
    card.dataset.seat = desk.seat || "";
    card.dataset.auth = String(desk.auth);
    card.classList.toggle("is-dormant", desk.state === "dormant");
    card.style.setProperty("--halo", partnerHalo(desk.partner));
    card.style.setProperty("--seat", seatColor(desk.seat));
    card.setAttribute("aria-pressed", String(state.filters.seats.has(desk.seat)));

    const dot = card.querySelector(".dot");
    // Assigning the same value would still be a no-op for the animation, but the
    // guard keeps that guarantee explicit rather than incidental.
    if (dot.dataset.state !== desk.state) dot.dataset.state = desk.state;
    dot.style.setProperty("background", desk.state === "healthy" ? seatColor(desk.seat) : "");

    const name = card.querySelector(".desk-name");
    name.firstChild.textContent = desk.seat || "no seat";
    name.lastChild.textContent = ` · ${desk.name}`;
    const sub = card.querySelector(".desk-sub");
    const line = deskSubLine(desk, now);
    if (sub.textContent !== line) sub.textContent = line;
    card.querySelector(".assignment-badge").hidden = desk.auth !== false;
  }

  /* ----------------------------------------------------------------- wire */

  function renderWire(freshOnly) {
    const feed = $("wireFeed");
    const visible = state.turns.filter((turn) => turnPasses(turn, state.filters));
    const capped = visible.slice(-DOM_TURN_CAP);

    const items = [];
    if (state.oldestSeq !== null && state.oldestSeq > 1) items.push({ kind: "earlier" });
    if (!capped.length && !state.pending.size) items.push({ kind: "quiet" });
    for (const turn of capped) items.push({ kind: "turn", turn, pending: false });
    for (const pending of state.pending.values()) items.push({ kind: "turn", turn: pending, pending: true });

    reconcile(feed, items, {
      key: (item) => {
        if (item.kind !== "turn") return item.kind;
        return `t:${item.turn.msg_id ?? item.turn.seq}`;
      },
      create: (item) => {
        if (item.kind === "earlier") {
          const earlier = el("button", "wire-earlier", "Load earlier turns");
          earlier.type = "button";
          earlier.addEventListener("click", loadEarlier);
          return earlier;
        }
        if (item.kind === "quiet") {
          return el("p", "wire-quiet", state.filters.conversation
            ? "No conversation in the loaded wire yet — only machine check-ins. “Everything” shows those too."
            : "The wire is quiet.");
        }
        return turnNode(item.turn, item.pending);
      },
      // A turn's TEXT never changes once it is on the wire; only its age does.
      // Touching the time and nothing else is what keeps an expanded receipt
      // expanded and the reader's scroll position where they put it.
      update: (node, item) => {
        if (item.kind === "quiet") {
          const text = state.filters.conversation
            ? "No conversation in the loaded wire yet — only machine check-ins. “Everything” shows those too."
            : "The wire is quiet.";
          if (node.textContent !== text) node.textContent = text;
          return;
        }
        if (item.kind !== "turn") return;
        const time = node.querySelector(".turn-time, :scope > summary > span:last-child");
        if (!time) return;
        const text = item.pending ? "sending…"
          : (node.classList.contains("receipt")
            ? `${item.turn.seat} · ${relativeAgo(timeOf(item.turn), Date.now())}`
            : relativeAgo(timeOf(item.turn), Date.now()));
        if (time.textContent !== text) time.textContent = text;
      },
    });
    if (state.following) scrollToBottom(!freshOnly);
  }

  function turnNode(turn, pending = false) {
    const seat = String(turn.seat || "system").toLowerCase();
    const sponsor = String(turn.sponsor || "").toLowerCase();
    const isOther = sponsor && sponsor !== state.viewer;

    if (turn.kind === "system") {
      const node = el("div", "turn-system");
      node.dataset.seat = seat;
      node.style.setProperty("--seat", seatColor(seat));
      node.appendChild(el("b", null, seat));
      node.appendChild(document.createTextNode(` — ${turn.body}`));
      return node;
    }

    if (turn.kind === "receipt") {
      const details = el("details", "receipt");
      details.dataset.seat = seat;
      details.style.setProperty("--seat", seatColor(seat));
      const summary = el("summary");
      summary.appendChild(el("span", "receipt-key", receiptLabel(turn.body)));
      summary.appendChild(el("span", null, `${seat} · ${relativeAgo(timeOf(turn), Date.now())}`));
      summary.title = absoluteTime(timeOf(turn));
      details.appendChild(summary);
      // Sentences first (anchored to the receipt's own moment, so they never
      // rot); the raw machine contract stays one honest level down.
      const human = el("div", "receipt-human");
      for (const line of describeReceipt(turn.body, timeOf(turn))) {
        human.appendChild(el("p", "receipt-line", line));
      }
      details.appendChild(human);
      const machine = el("details", "receipt-machine");
      machine.appendChild(el("summary", null, "machine detail"));
      const parsed = parseReceipt(turn.body);
      machine.appendChild(el("pre", "receipt-json", parsed ? JSON.stringify(parsed, null, 2) : String(turn.body)));
      details.appendChild(machine);
      return details;
    }

    const card = el("article", `turn${isOther ? " is-other-partner" : ""}${pending ? " is-pending" : ""}`);
    card.dataset.seat = seat;
    card.style.setProperty("--seat", seatColor(seat));
    if (isOther) card.style.setProperty("--halo", partnerHalo(sponsor));
    const head = el("div", "turn-head");
    head.appendChild(seatChip(seat));
    if (isOther) {
      const tag = el("span", "partner-tag", PARTNER_LABEL[sponsor] || sponsor);
      tag.style.setProperty("--halo", partnerHalo(sponsor));
      head.appendChild(tag);
    }
    const time = el("span", "turn-time", pending ? "sending…" : relativeAgo(timeOf(turn), Date.now()));
    time.title = absoluteTime(timeOf(turn));
    head.appendChild(time);
    card.appendChild(head);
    card.appendChild(el("p", "turn-body", String(turn.body ?? "")));
    return card;
  }

  function scrollToBottom(instant) {
    const feed = $("wireFeed");
    feed.scrollTo({ top: feed.scrollHeight, behavior: instant || REDUCED() ? "auto" : "smooth" });
    state.missed = 0;
    $("wireResume").hidden = true;
  }

  function onFeedScroll() {
    const feed = $("wireFeed");
    const distance = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
    const following = distance <= 120;
    if (following !== state.following) {
      state.following = following;
      if (following) scrollToBottom(false);
    }
    if (!following && state.missed) {
      $("wireResume").hidden = false;
      $("wireResume").textContent = `Resume live · ${state.missed} new`;
    }
  }

  /* ---------------------------------------------------------- assignments */

  function renderAssignments(model) {
    const host = $("assignmentList");
    if (!model.assignments.length) {
      host.replaceChildren(el("p", "rail-empty",
        "No assignments on the wire yet. When a seat is handed work, it appears here."));
      return;
    }
    reconcile(host, model.assignments.slice(0, 40), {
      key: (item) => `a:${item.seq}`,
      create: (item) => {
        const card = el("div", "assignment");
        const head = el("div", "assignment-head");
        head.appendChild(seatChip(`@${item.seat}`));
        head.querySelector(".seat-chip")?.style.setProperty("--seat", seatColor(item.seat));
        head.appendChild(el("span", "assignment-ref", item.ref || "—"));
        card.appendChild(head);
        card.appendChild(el("p", "assignment-title", item.title || `${item.verb || "claim"} ${item.ref || ""}`));
        card.appendChild(el("div", "assignment-meta"));
        card.appendChild(el("span", "assignment-badge", "recorded — queue link pending"));
        return card;
      },
      // An assignment receipt is immutable once written; only its age moves.
      update: (node, item) => {
        const meta = node.querySelector(".assignment-meta");
        const text = `by ${item.by || "unknown"} · ${relativeAgo(item.at, model.now)}`;
        if (meta.textContent !== text) meta.textContent = text;
      },
    });
  }

  /** The context gauges: one row per relay session that has reported in the
   *  last two hours, so no agent burns tokens Joe cannot see burning. */
  function renderSessions(model) {
    const host = $("sessionList");
    if (!model.sessions.length) {
      host.replaceChildren(el("p", "rail-empty",
        "No session has reported its context yet. Rows appear here as relay sessions post a status receipt."));
      return;
    }
    reconcile(host, model.sessions, {
      key: (session) => `s:${session.name}`,
      create: () => {
        const row = el("div", "session-row");
        const head = el("div", "session-head");
        head.appendChild(el("span", "dot"));
        head.appendChild(el("span", "session-name"));
        head.appendChild(el("span", "session-pct"));
        row.appendChild(head);
        const bar = el("div", "session-bar");
        bar.appendChild(el("span", "session-fill"));
        row.appendChild(bar);
        row.appendChild(el("div", "session-claimed"));
        return row;
      },
      update: (row, session) => {
        row.classList.toggle("is-stale", session.stale);
        row.style.setProperty("--halo", partnerHalo(session.partner));
        const dot = row.querySelector(".dot");
        if (dot.dataset.state !== session.state) dot.dataset.state = session.state;
        row.querySelector(".session-name").textContent = session.name;
        row.querySelector(".session-pct").textContent =
          Number.isFinite(session.contextPct) ? `${Math.round(session.contextPct)}%` : "—";
        // The bar animates its width, which is the count-up equivalent for a
        // gauge: context that just jumped ten points should be seen jumping.
        row.querySelector(".session-fill").style.setProperty(
          "width", `${Math.max(0, Math.min(100, Number(session.contextPct) || 0))}%`);
        row.querySelector(".session-claimed").textContent =
          `${session.claimed || "nothing claimed"} · ${relativeAgo(session.at, model.now)}`;
      },
    });
  }

  /* ----------------------------------------------------------- Job Passport */

  function humanRef(ref) {
    return String(ref || "unknown").replace(/^[^:]+:/, "").replace(/[-_]/g, " ");
  }

  function renderJobPassport(model) {
    const feature = model.jobPassports;
    const section = $("jobPassport");
    if (!feature?.enabled) { section.hidden = true; return; }
    section.hidden = false;
    const rejected = feature.rejected.length;
    $("jobPassportSummary").textContent = `${feature.passports.length} Work Request${feature.passports.length === 1 ? "" : "s"} · deterministic wire projection${rejected ? ` · ${rejected} stale, malformed, or conflicting update${rejected === 1 ? "" : "s"} withheld` : ""}`;
    const host = $("jobPassportList");
    reconcile(host, feature.passports, {
      key: (passport) => `passport:${passport.work_request_id}`,
      create: (passport) => {
        const card = el("article", "passport-card");
        card.tabIndex = 0;
        card.setAttribute("aria-label", `Job Passport ${passport.work_request_id}`);
        return card;
      },
      update: (card, passport) => {
        card.dataset.status = passport.status;
        card.replaceChildren();
        const top = el("div", "passport-top");
        const workLabel = el("span", "passport-work", passport.work_request_id); workLabel.tabIndex = -1;
        top.appendChild(workLabel);
        top.appendChild(el("span", "passport-status", jobPassportStatusLabel(passport.status)));
        card.appendChild(top);
        const freshness = passport.freshness === "stale" ? "STALE — last signal" : "Observed";
        card.appendChild(el("p", "passport-freshness", `${freshness} ${relativeAgo(passport.observed_at, model.now)} · state v${passport.source_state.state_version}`));

        const staffing = el("div", "passport-staffing");
        const profile = el("div"); profile.appendChild(el("label", null, "Persistent profile"));
        profile.appendChild(el("span", null, passport.attempt_lane.persistent_profile.display_label));
        const actual = passport.attempt_lane.actual_staffing;
        const execution = el("div"); execution.appendChild(el("label", null, "Actual staffing"));
        execution.appendChild(el("span", null, `${humanRef(actual.surface)} · ${humanRef(actual.model_id)} · ${humanRef(actual.harness_id)}`));
        staffing.append(profile, execution); card.appendChild(staffing);

        if (passport.spatial_surface) {
          const surface = passport.spatial_surface;
          const home = el("section", "passport-home-zone");
          home.setAttribute("aria-label", "Spatial Home Zone and list parity");
          home.appendChild(el("h3", "passport-home-title", "Job Passport Home Zone"));
          home.appendChild(el("p", "passport-home-summary", `${surface.semantic_zoom.overview.summary}. ${surface.home_zone.attention_node_ids.length ? "Needs attention: evidence or approval requires review." : "No inferred stuck state."}`));
          const controls = el("div", "passport-home-controls");
          const returnHome = el("button", "passport-home-action", surface.home_zone.return_label); returnHome.type = "button";
          returnHome.addEventListener("click", () => card.querySelector(".passport-work")?.focus());
          controls.appendChild(returnHome);
          const detailToggle = el("button", "passport-home-action", "Show semantic detail"); detailToggle.type = "button";
          detailToggle.setAttribute("aria-pressed", card.dataset.zoomDetail === "true" ? "true" : "false");
          detailToggle.addEventListener("click", () => { card.dataset.zoomDetail = String(card.dataset.zoomDetail !== "true"); renderJobPassport(model); });
          controls.appendChild(detailToggle); home.appendChild(controls);
          const listView = el("ol", "passport-spatial-list");
          for (const nodeId of surface.list_order) {
            const nodeData = surface.nodes.find((row) => row.node_id === nodeId);
            const item = el("li", null, `${nodeData.accessibility.non_color_status_token}: ${nodeData.accessibility.label}`);
            item.tabIndex = 0; item.dataset.nodeId = nodeId;
            item.addEventListener("click", () => { card.dataset.detailOpen = "true"; card.querySelector("details")?.setAttribute("open", ""); });
            listView.appendChild(item);
          }
          home.appendChild(listView);
          if (card.dataset.zoomDetail === "true") home.appendChild(el("p", "passport-home-detail", surface.semantic_zoom.detail.summary));
          card.appendChild(home);
        }

        const map = el("div", "passport-map");
        for (const component of passport.component_map) {
          const node = el("button", "passport-node", humanRef(component.component_ref));
          node.type = "button"; node.dataset.current = String(component.current);
          node.title = component.depends_on_component_refs.length
            ? `Depends on ${component.depends_on_component_refs.map(humanRef).join(", ")}` : "No declared dependencies";
          const openDetail = () => {
            card.dataset.detailOpen = "true";
            card.querySelector("details")?.setAttribute("open", "");
          };
          node.addEventListener("click", openDetail);
          // Native buttons should already activate for Enter/Space, but this
          // explicit path keeps the component-to-evidence relationship intact
          // across browser/harness keyboard implementations.
          node.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDetail(); }
          });
          map.appendChild(node);
        }
        card.appendChild(map);
        const observation = passport.observed_movement;
        const alignment = observation.deviation_candidates?.length
          ? `${observation.deviation_candidates.length} deviation candidate${observation.deviation_candidates.length === 1 ? "" : "s"}; review required.`
          : `${observation.coverage_state.replace(/_/g, " ")} declared coverage · ${observation.activity_fidelity.replace(/_/g, " ")} observation · uncertainty: ${observation.uncertainty.replace(/_/g, " ")}`;
        card.appendChild(el("p", "passport-alignment", alignment));
        const timeline = el("div", "passport-timeline"); timeline.setAttribute("aria-label", "Observed progress timeline");
        for (const event of passport.timeline) {
          const step = event.declared_step_ref ? ` · ${humanRef(event.declared_step_ref)}` : "";
          const entry = el("span", "passport-event", `${event.sequence}. ${humanRef(event.event_type)}${step}`);
          entry.dataset.state = event.state; entry.title = `${event.occurred_at} · ${event.retention}`;
          timeline.appendChild(entry);
        }
        if (!passport.timeline.length) timeline.appendChild(el("span", "passport-event", "No observed progress event"));
        card.appendChild(timeline);
        const telemetry = passport.telemetry_measurements || [];
        if (!telemetry.length) {
          card.appendChild(el("p", "passport-telemetry", "Telemetry: unavailable — no trustworthy provider quota or billed-cost measurement was supplied. Session tokens, terminal text, and activity are not substituted."));
        } else {
          const summary = telemetry.map((measurement) => {
            const label = `${humanRef(measurement.metric_kind)} (${measurement.unit})`;
            if (measurement.value.kind === "unavailable") return `${label}: unavailable — ${measurement.value.unavailable_reason}`;
            const qualifier = measurement.value.kind === "estimate" ? `estimate via ${measurement.value.estimate_method}; uncertainty: ${measurement.value.uncertainty}` : "actual";
            return `${label}: ${measurement.value.amount} · ${qualifier} · ${humanRef(measurement.source.type)} · ${measurement.freshness}`;
          }).join("; ");
          card.appendChild(el("p", "passport-telemetry", `Telemetry: ${summary}.`));
        }
        if (passport.eval_portfolio) {
          const portfolio = passport.eval_portfolio;
          const ladder = el("section", "passport-eval");
          ladder.setAttribute("aria-label", "Evaluation ladder and stage diagnostics");
          ladder.appendChild(el("h3", "passport-eval-title", "Evaluation ladder · synthetic/offline"));
          const rungs = [...new Set(portfolio.cases.map((row) => row.rung))];
          const requirement = portfolio.policy.risk_requirements.find((row) => row.risk_class === portfolio.binding.risk_class && row.lifecycle === portfolio.binding.lifecycle);
          const completed = [...new Set(portfolio.results.filter((row) => row.status === "passed").map((row) => row.rung))];
          ladder.appendChild(el("p", "passport-eval-rungs", `Required: ${requirement?.required_rungs?.join(", ") || "unknown / default deny"} · completed: ${completed.join(", ") || "not run"} · represented: ${rungs.join(", ")} · no aggregate score`));
          const matrix = el("div", "passport-eval-matrix");
          const baseline = portfolio.results.find((row) => row.result_id === "result:codex-baseline") || portfolio.results[0];
          for (const stage of baseline.stage_results || []) {
            const dimensions = stage.dimension_ids.map(humanRef).join(", ");
            const cell = el("div", "passport-eval-cell", `${humanRef(stage.stage_id)} — ${stage.status.replace(/_/g, " ")} · ${dimensions}`);
            cell.dataset.state = stage.status;
            matrix.appendChild(cell);
          }
          ladder.appendChild(matrix);
          const comparisons = portfolio.frontier_comparisons || [];
          const candidate = portfolio.results.find((row) => row.result_id === comparisons[0]?.candidate_result_id);
          const critical = candidate?.dimension_results?.filter((row) => row.direction_vs_baseline === "regressed" || row.status !== "passed") || [];
          const frontier = el("p", "passport-eval-frontier", candidate
            ? `Quality frontier vs cheaper candidate: ${comparisons[0]?.promotion_state.replace(/_/g, " ") || "unknown"}; critical: ${critical.map((row) => humanRef(row.dimension_id)).join(", ") || "none"}; latency ${candidate.telemetry.latency_ms} ms; cost ${candidate.telemetry.cost_usd}.`
            : "Quality frontier comparison unavailable.");
          ladder.appendChild(frontier);
          const failures = portfolio.taxonomy.failure_modes || [];
          ladder.appendChild(el("p", "passport-eval-failures", `Failure taxonomy: ${failures.map((row) => humanRef(row.class_name)).join(", ") || "none"}.`));
          card.appendChild(ladder);
        }
        if (passport.activation_reliability) {
          const facts = passport.activation_reliability;
          const activation = facts.knowledge_activation;
          const reliability = facts.reliability;
          const canonical = facts.canonical;
          const issuedEnvelope = passport.engineering_passport?.execution_envelopes?.find((envelope) => envelope.work_request_id === passport.work_request_id) || null;
          const knowledge = el("section", "passport-activation-section");
          knowledge.setAttribute("aria-label", "Knowledge and Grounding");
          knowledge.appendChild(el("h3", null, "Knowledge & Grounding"));
          knowledge.appendChild(el("p", null, activation
            ? `Bound bundle ${activation.bundle_digest.slice(0, 16)}… · ${activation.closure.state.replace(/_/g, " ")} · ${activation.item_dispositions.length} redacted item disposition(s).`
            : "No activation receipt is available; grounding is withheld rather than inferred."));
          card.appendChild(knowledge);

          const route = el("section", "passport-activation-section");
          route.setAttribute("aria-label", "Route and Agent Topology");
          route.appendChild(el("h3", null, "Route & Agent Topology"));
          const environment = issuedEnvelope?.runtime_profile?.environment_provider_ref
            ? ` Execution environment: ${humanRef(issuedEnvelope.runtime_profile.environment_provider_ref)} · ${humanRef(issuedEnvelope.runtime_profile.environment_backend_kind)} · ${humanRef(issuedEnvelope.runtime_profile.environment_isolation_class)} · conformance ${humanRef(issuedEnvelope.runtime_profile.environment_conformance_ref)}.`
            : " Execution environment provider is unavailable for this legacy envelope.";
          route.appendChild(el("p", null, issuedEnvelope?.runtime_profile && issuedEnvelope?.execution_topology && issuedEnvelope?.evaluation_plan
            ? `Server-issued runtime: ${humanRef(issuedEnvelope.runtime_profile.ref)} · ${humanRef(issuedEnvelope.runtime_profile.model_id)}; topology: ${humanRef(issuedEnvelope.execution_topology.ref)}; evaluation plan: ${humanRef(issuedEnvelope.evaluation_plan.ref)}.${environment} Observed staffing: ${humanRef(actual.surface)} · ${humanRef(actual.harness_id)}.`
            : `Observed route: ${humanRef(actual.surface)} · ${humanRef(actual.model_id)} · ${humanRef(actual.harness_id)}. Server runtime/topology is withheld until an exact issued envelope is bound.`));
          card.appendChild(route);

          const outcome = el("section", "passport-activation-section");
          outcome.setAttribute("aria-label", "Evaluation and Outcome");
          outcome.appendChild(el("h3", null, "Evaluation & Outcome"));
          const environmentOutcome = reliability?.environment_evidence
            ? ` Environment session: ${humanRef(reliability.environment_evidence.session_ref)}; lease: ${reliability.environment_evidence.lease_state}; cleanup: ${reliability.environment_evidence.cleanup_state}; side effects: ${reliability.environment_evidence.side_effect_state}; operations: ${reliability.environment_evidence.operation_count}.`
            : " Environment cleanup evidence unavailable.";
          outcome.appendChild(el("p", null, reliability
            ? `Grounding: ${reliability.grounding_sufficiency.state}; deterministic checks: ${reliability.deterministic_checks.length}; judge: ${reliability.model_judgement.state}; human outcome: ${reliability.human_acceptance.state}; horizon: ${reliability.outcome_horizon.state}; closure: ${(canonical?.reliability?.state || reliability.closure.state).replace(/_/g, " ")} (${(canonical?.reliability?.reasons || reliability.closure.reasons).join(", ") || "canonical authority evidence required"}).${environmentOutcome}`
            : "No reliability receipt is available; outcome is not promoted."));
          card.appendChild(outcome);

          const learning = el("section", "passport-activation-section");
          learning.setAttribute("aria-label", "Learning");
          learning.appendChild(el("h3", null, "Learning"));
          learning.appendChild(el("p", null, canonical
            ? `Canonical learning lifecycle: ${canonical.learning.lifecycle.replace(/_/g, " ")}; candidates: ${canonical.learning.candidate_refs.map(humanRef).join(", ") || "none"}; canonical telemetry: ${canonical.telemetry.length} signal(s). All remain human-gated and unpromoted.`
            : reliability
              ? "Canonical learning projection is unavailable; executor receipt candidates are never used as learning state."
            : "No learning proposal is inferred from missing evidence."));
          card.appendChild(learning);
        }
        if (passport.engineering_passport) {
          const engineering = passport.engineering_passport;
          const panel = el("section", "passport-engineering");
          panel.setAttribute("aria-label", "Engineering Passport lifecycle");
          panel.appendChild(el("h3", "passport-engineering-title", `Engineering Passport · ${engineering.closure_state}`));
          const states = engineering.slices.map((slice) => {
            const deps = slice.dependency_refs.length ? ` · depends on ${slice.dependency_refs.map(humanRef).join(", ")}` : " · no dependencies";
            return `${humanRef(slice.slice_ref)}: ${slice.state.replace(/_/g, " ")}${deps}`;
          });
          const stateList = el("ul", "passport-engineering-slices");
          for (const state of states) stateList.appendChild(el("li", null, state));
          panel.appendChild(stateList);
          const deviations = engineering.operator_receipt.deviations.length
            ? `Planned-vs-actual deviations: ${engineering.operator_receipt.deviations.map(humanRef).join(", ")}.`
            : "Planned-vs-actual deviations: none recorded.";
          panel.appendChild(el("p", "passport-engineering-deviations", deviations));
          panel.appendChild(el("p", "passport-engineering-receipt", `Operator receipt: ${engineering.operator_receipt.why}. Remaining risk: ${engineering.operator_receipt.remaining_risk.map(humanRef).join(", ") || "none"}.`));
          const closureList = el("ul", "passport-engineering-closure");
          for (const field of ["work", "proof", "explanation", "release", "learning"]) {
            const route = engineering.closure[field].route ? ` · route ${humanRef(engineering.closure[field].route)}` : "";
            closureList.appendChild(el("li", null, `${humanRef(field)} disposition: ${engineering.closure[field].state.replace(/_/g, " ")}${route} — ${engineering.closure[field].note}`));
          }
          panel.appendChild(closureList);
          if (engineering.stale_conflict.state !== "none") panel.appendChild(el("p", "passport-engineering-conflict", `State posture: ${engineering.stale_conflict.state} — ${engineering.stale_conflict.reason}`));
          card.appendChild(panel);
        }
        const detail = el("details", "passport-detail");
        detail.open = card.dataset.detailOpen === "true";
        detail.addEventListener("toggle", () => { card.dataset.detailOpen = String(detail.open); });
        detail.appendChild(el("summary", null, "Evidence, checkpoint, and handoff"));
        const list = el("ul", "passport-detail-list");
        for (const ref of passport.evidence_refs) list.appendChild(el("li", null, ref));
        for (const finding of passport.eval_portfolio?.behavior_findings || []) list.appendChild(el("li", null, `Eval finding ${finding.finding_id}: ${finding.failure_mode_id} · ${finding.evidence_refs.join(", ")}`));
        const checkpoint = passport.state.verification === "verified_success" ? "Independent verification recorded; handoff only at a verified checkpoint." : "Executor claim remains unpromoted; no verified handoff implied.";
        list.appendChild(el("li", null, checkpoint));
        list.appendChild(el("li", null, `Adapter: ${actual.adapter_id} ${actual.adapter_version} · native session retained as ${actual.native_session_ref}`));
        detail.appendChild(list); card.appendChild(detail);
      },
    });
  }

  /* --------------------------------------------------------------- health */

  function renderHealth(model) {
    countTo($("healthCycle"), model.cycleAgeS === null ? NaN : Math.round(model.cycleAgeS),
      (v) => (Number.isFinite(Number(v)) ? humanDuration(Number(v)) : "—"));
    setState($("healthCycleDot"), cycleState(model.cycleAgeS));

    state.lagStreak = model.cursorLag ? state.lagStreak + 1 : 0;
    countTo($("healthLag"), model.cursorLag ?? NaN, (v) => (Number.isFinite(Number(v)) ? String(v) : "—"));
    setState($("healthLagDot"), lagState(model.cursorLag, state.lagStreak));

    const errState = errorState(model.errors, state.lastErrors);
    countTo($("healthErrors"), model.errors);
    setState($("healthErrorDot"), errState);
    state.lastErrors = model.errors;
    // The count is over the turns this page has loaded, and says so — a bare
    // "8 errors" reads as "8 things wrong right now", which it is not.
    const oldestAt = state.turns.length ? timeOf(state.turns[0]) : 0;
    announce($("healthErrorsSub"), oldestAt
      ? `refusals and failures in the loaded wire, since ${absoluteTime(oldestAt)}`
      : "refusals and failures in the loaded wire");

    countTo($("healthDesks"), model.onlineDesks);
    setState($("healthDeskDot"), model.desks.length
      ? (model.onlineDesks ? (model.onlineDesks === model.desks.length ? "healthy" : "attention") : "urgent")
      : "dormant");

    $("healthAuth").textContent = model.authKnown
      ? `${model.signedIn} of ${model.authKnown}` : "unknown";
    setState($("healthAuthDot"), authState(model.signedIn, model.authKnown, model.desks.length));

    countTo($("figCycle"), model.cycleAgeS === null ? NaN : Math.round(model.cycleAgeS),
      (v) => (Number.isFinite(Number(v)) ? humanDuration(Number(v)) : "—"));
    // The spec's cursor pair (delivered/latest) said this in protocol terms;
    // Joe's tweak-round ruling replaces it with the phrase the pair was always
    // trying to say. announce() keeps the no-silent-re-render rule.
    announce($("figBridge"), bridgeLagLabel(model.cursorLag));
    countTo($("figDesks"), model.onlineDesks, (v) => `${v} online`);

    setState($("presenceDot"), model.presence.state);
    $("presenceDot").style.setProperty("background", partnerHalo(model.presence.partner));
    $("presenceText").textContent = model.presence.at
      ? `${PARTNER_LABEL[model.presence.partner]} · ${relativeTime(model.presence.at, model.now)} ago`
      : `${PARTNER_LABEL[model.presence.partner]} · not on the wire today`;
  }

  /* -------------------------------------------------------------- filters */

  function renderSeatChips(model) {
    const host = $("seatChips");
    const seats = new Set();
    for (const desk of model.desks) if (desk.seat) seats.add(desk.seat);
    for (const orbiter of model.orbiters) seats.add(orbiter.seat);
    seats.add("human");
    reconcile(host, [...seats].sort(), {
      key: (seat) => `c:${seat}`,
      create: (seat) => {
        const chip = el("button", "chip", seat);
        chip.type = "button";
        chip.style.setProperty("--chip", seatColor(seat));
        chip.addEventListener("click", () => toggleSeatFilter(seat));
        return chip;
      },
      update: (chip, seat) => chip.setAttribute("aria-pressed", String(state.filters.seats.has(seat))),
    });
    for (const chip of host.children) {
      chip.setAttribute("aria-pressed", String(state.filters.seats.has(chip.textContent)));
    }
  }

  function toggleSeatFilter(seat) {
    if (!seat) return;
    if (state.filters.seats.has(seat)) state.filters.seats.delete(seat);
    else state.filters.seats.add(seat);
    render();
  }

  function clearFilters() {
    state.filters.seats.clear();
    state.filters.text = "";
    $("wireSearch").value = "";
    render();
  }

  function bindKindToggle(id, key) {
    const button = $(id);
    button.addEventListener("click", () => {
      state.filters[key] = !state.filters[key];
      button.setAttribute("aria-pressed", String(state.filters[key]));
      render();
    });
  }

  /* ------------------------------------------------------------- composer */

  const COMPOSER_LINE = 19;
  const COMPOSER_BASE = 40;

  /** Grow the composer by COUNTING LINES rather than by measuring the box.
   *  Measuring is the obvious way and it is wrong here: on first paint a
   *  textarea inside a fixed flex bar reports its own max-height back as
   *  scrollHeight, so the bar opened three lines tall on every load.  Counting
   *  needs no layout read at all, gives the same answer, and cannot be thrown
   *  off by a font that has not finished loading. */
  function sizeComposer(value) {
    const input = $("composerInput");
    const lines = Math.min(5, Math.max(1, String(value).split("\n").length));
    const wanted = COMPOSER_BASE + (lines - 1) * COMPOSER_LINE;
    input.style.setProperty("height", `${wanted}px`);
    // Only once the page has settled is a measurement trustworthy, and then it
    // is only ever used to grow for WRAPPED text, never to set the base.
    if (document.readyState === "complete" && input.scrollHeight > wanted) {
      input.style.setProperty("height", `${Math.min(132, input.scrollHeight)}px`);
    }
  }

  function updateCounter() {
    const value = $("composerInput").value;
    const counter = $("composerCount");
    const over = value.length > BODY_MAX;
    counter.hidden = value.length < COUNTER_FROM;
    counter.textContent = over
      ? `${value.length} / ${BODY_MAX} — too long to post`
      : `${value.length} / ${BODY_MAX}`;
    counter.classList.toggle("is-over", over);
    $("composerSend").disabled = over || !value.trim();
    sizeComposer(value);
  }

  async function sendComposer() {
    const input = $("composerInput");
    const body = input.value;
    if (!body.trim() || body.length > BODY_MAX) return;
    const localId = `pending:${Date.now()}:${Math.random().toString(36).slice(2)}`;
    state.pending.set(localId, { seq: Number.MAX_SAFE_INTEGER, at: new Date().toISOString(),
      seat: "human", sponsor: state.viewer, kind: "turn", body, msg_id: localId });
    input.value = "";
    updateCounter();
    render();
    scrollToBottom(false);

    const send = $("composerSend");
    send.disabled = true;
    const spinner = el("span", "composer-spinner");
    send.prepend(spinner);
    try {
      // The server mints the idempotency key AND the msg_id, so a retry after a
      // network hiccup can never double-post the same turn.
      await postTurn({ body });
      state.pending.delete(localId);
      await poll();
    } catch (error) {
      state.pending.delete(localId);
      render();
      toast(error?.data?.error === "body_too_long"
        ? "That turn is over the room's 20,000-character cap."
        : "The turn did not reach the wire.", () => { input.value = body; updateCounter(); input.focus(); });
    } finally {
      spinner.remove();
      updateCounter();
    }
  }

  async function requestReconnect(deskName) {
    try {
      await postTurn({ control: { action: "login", desk: deskName } });
      banner(`Sign-in requested for ${deskName} — approve it in the browser window the desk opens.`, false);
      await poll();
    } catch {
      toast(`Could not request a sign-in for ${deskName}.`);
    }
  }

  function toast(message, retry) {
    const host = $("roomToast");
    host.replaceChildren();
    host.appendChild(el("span", null, message));
    if (retry) {
      const button = el("button", null, "Retry");
      button.type = "button";
      button.addEventListener("click", () => { host.hidden = true; retry(); });
      host.appendChild(button);
    }
    host.hidden = false;
    setTimeout(() => { host.hidden = true; }, 9000);
  }

  function banner(message, bad = true) {
    const host = $("roomBanner");
    host.textContent = message;
    host.hidden = false;
    if (!bad) setTimeout(() => { host.hidden = true; }, 6000);
  }

  /* ------------------------------------------------------------- the loop */

  function absorb(payload, { prepend = false } = {}) {
    const arriving = [];
    for (const turn of payload.turns || []) {
      const id = String(turn.msg_id || `${turn.seq}`);
      if (state.byMsgId.has(id)) continue;
      state.byMsgId.set(id, turn);
      arriving.push(turn);
    }
    if (!arriving.length) return arriving;
    state.turns = [...state.turns, ...arriving].sort((a, b) => seqOf(a) - seqOf(b));
    if (state.turns.length > DOM_TURN_CAP * 4) state.turns = state.turns.slice(-DOM_TURN_CAP * 4);
    const oldest = state.turns.length ? seqOf(state.turns[0]) : null;
    state.oldestSeq = oldest;
    if (!prepend) {
      const latest = Number(payload.latest_seq);
      if (Number.isFinite(latest)) state.cursor = Math.max(state.cursor, latest);
    }
    return arriving;
  }

  function render(fresh = []) {
    const model = deriveModel(state.turns, { now: Date.now(), viewer: state.viewer });
    state.model = model;
    renderStage(model);
    renderSeatChips(model);
    renderDesks(model);
    renderWire(fresh.length > 0);
    renderAssignments(model);
    renderSessions(model);
    renderJobPassport(model);
    renderHealth(model);
    if (fresh.length) animateArrivals(fresh, model);
  }

  async function poll() {
    try {
      // Loop #521: the first poll used to fetch from seq 0 (the OLDEST page),
      // so the health strip derived its cycle-age tile from a stale window and
      // showed red for minutes after every page load. When the cursor is still
      // at the initial value, jump straight to the present: read the newest
      // full page instead of the oldest one. History stays reachable through
      // Load earlier.
      const initialFetch = state.cursor === 0;
      const from = initialFetch
        ? Math.max(0, (state.latestSeqHint ?? 0) - PAGE_SIZE)
        : state.cursor;
      const payload = await fetchTurns(from, PAGE_SIZE);
      if (Number.isFinite(Number(payload.latest_seq))) {
        state.latestSeqHint = Number(payload.latest_seq);
        if (initialFetch) {
          // First response arrived with the present: drop any turns below the
          // window we actually wanted so the panel never renders the old span.
          const cutoff = Math.max(0, Number(payload.latest_seq) - PAGE_SIZE);
          payload.turns = (payload.turns || []).filter((t) => Number(t.seq) > cutoff);
        }
      }
      if (payload.actor?.slug) state.viewer = String(payload.actor.slug).toLowerCase();
      if (payload.csrf_token) state.csrf = payload.csrf_token;
      $("composerInput").placeholder = `Speak into the room as ${PARTNER_LABEL[state.viewer] || "a partner"}…`;
      const fresh = absorb(payload);
      if (state.backoffMs) {
        // Recovery announces itself by counting the missed turns in, rather
        // than silently resuming as if nothing had happened.
        state.backoffMs = null;
        banner(`Wire back — ${fresh.length} turn${fresh.length === 1 ? "" : "s"} caught up.`, false);
      } else {
        $("roomBanner").hidden = true;
      }
      if (!state.following) state.missed += fresh.length;
      render(fresh);
      if (!state.following && state.missed) {
        $("wireResume").hidden = false;
        $("wireResume").textContent = `Resume live · ${state.missed} new`;
      }
    } catch (error) {
      if (String(error?.message) === "sign_in_required") return;
      state.backoffMs = Math.min(POLL_BACKOFF_CEILING_MS, (state.backoffMs || POLL_VISIBLE_MS) * 2);
      setState($("healthCycleDot"), "urgent");
      banner("wire unreachable — retrying");
    } finally {
      schedule();
    }
  }

  function schedule() {
    clearTimeout(state.timer);
    const base = document.hidden ? POLL_HIDDEN_MS : POLL_VISIBLE_MS;
    state.timer = setTimeout(poll, state.backoffMs || base);
  }

  async function loadEarlier() {
    const oldest = state.oldestSeq;
    if (!oldest || oldest <= 1) return;
    const from = Math.max(0, oldest - 1 - PAGE_SIZE);
    const payload = await fetchTurns(from, Math.min(PAGE_SIZE, oldest - 1 - from));
    absorb(payload, { prepend: true });
    render();
  }

  /* ------------------------------------------------------------- wiring up */

  function bindCollapsible(sectionId, toggleId) {
    const section = $(sectionId);
    const toggle = $(toggleId);
    // Mobile only: the other partner's column starts folded so a phone opens on
    // the wire rather than on a roster.
    toggle.addEventListener("click", () => {
      const collapsed = section.dataset.collapsed === "true";
      section.dataset.collapsed = String(!collapsed);
      toggle.setAttribute("aria-expanded", String(collapsed));
      toggle.textContent = collapsed ? "Hide" : "Show";
    });
  }

  /** Conversation is the front door; Everything is the machine room. One
   *  boolean, two buttons, and the kind chips only matter in Everything. */
  function setFeedMode(conversation) {
    state.filters.conversation = conversation;
    $("viewConversation").setAttribute("aria-pressed", String(conversation));
    $("viewEverything").setAttribute("aria-pressed", String(!conversation));
    $("wireFilters").dataset.mode = conversation ? "conversation" : "everything";
    render();
  }
  $("viewConversation").addEventListener("click", () => setFeedMode(true));
  $("viewEverything").addEventListener("click", () => setFeedMode(false));
  $("wireFilters").dataset.mode = "conversation";

  drawCore();
  bindKindToggle("kindTurns", "turns");
  bindKindToggle("kindSystem", "system");
  bindKindToggle("kindReceipts", "receipts");
  bindKindToggle("kindHeartbeats", "heartbeats");
  bindCollapsible("roomDesks", "desksToggle");
  bindCollapsible("roomRail", "railToggle");
  $("legendJoe").style.setProperty("border-color", PARTNER_HALO.joe);
  $("legendDell").style.setProperty("border-color", PARTNER_HALO.dell);
  $("legendFill").style.setProperty("background", SEAT_COLORS.claude);

  $("wireSearch").addEventListener("input", (event) => {
    state.filters.text = String(event.target.value || "").trim().toLowerCase();
    render();
  });
  $("wireFeed").addEventListener("scroll", onFeedScroll, { passive: true });
  $("wireResume").addEventListener("click", () => { state.following = true; scrollToBottom(true); });
  $("composerInput").addEventListener("input", updateCounter);
  $("composerInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendComposer(); }
  });
  $("roomComposer").addEventListener("submit", (event) => { event.preventDefault(); sendComposer(); });

  // Cursor parallax: the stage leans toward the pointer, and each ring is
  // displaced by its depth. Pointer-driven only, so a touch device simply never
  // tilts, and reduced motion pins it flat.
  $("roomStage").addEventListener("pointermove", (event) => {
    if (REDUCED()) return;
    const box = $("roomStage").getBoundingClientRect();
    $("roomStage").style.setProperty("--px", ((event.clientX - box.left) / box.width - 0.5).toFixed(3));
    $("roomStage").style.setProperty("--py", ((event.clientY - box.top) / box.height - 0.5).toFixed(3));
  }, { passive: true });
  $("roomStage").addEventListener("pointerleave", () => {
    $("roomStage").style.setProperty("--px", "0");
    $("roomStage").style.setProperty("--py", "0");
    hideTooltip();
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    // Crossing the phone/desktop boundary swaps the stage geometry, so the core
    // and the rings are both redrawn rather than left at the old scale.
    resizeTimer = setTimeout(() => { stageLayout = ""; drawCore(); render(); }, 180);
  }, { passive: true });
  document.addEventListener("visibilitychange", () => schedule());
  reduced.addEventListener("change", () => render());
  if (window.innerWidth <= 720) {
    $("roomDesks").dataset.collapsed = "true";
    $("roomRail").dataset.collapsed = "true";
    $("desksToggle").textContent = "Show";
    $("railToggle").textContent = "Show";
    $("desksToggle").setAttribute("aria-expanded", "false");
    $("railToggle").setAttribute("aria-expanded", "false");
  }
  updateCounter();
  poll();
}

if (isBrowser && document.getElementById("wireFeed")) boot();
