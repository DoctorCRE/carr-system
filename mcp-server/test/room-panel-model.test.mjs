// The Model Room panel's derivation, proved without a browser.
//
// EVERYTHING ON THAT PAGE COMES OUT OF THIS ONE FUNCTION, which is exactly why
// it is worth a suite of its own: the desk roster, each desk's pulse state, the
// cursor lag, the bridge's cycle age, the assignments rail, the backend workers
// on the outer ring, the other partner's presence line.  If deriveModel is
// wrong, the panel is confidently wrong — and a dashboard that is confidently
// wrong about which desks are alive is worse than no dashboard.
//
// The fixtures below are the shapes the live wire actually returns, including
// the one that has bitten before: `seq` arrives as a STRING, because Postgres
// bigints come back as strings, and every comparison in the panel is numeric.

import test from "node:test";
import assert from "node:assert/strict";
import {
  authState, bridgeLagLabel, cycleState, deriveModel, describeReceipt, deskState,
  errorState, humanizeKey, isErrorReceipt, isHeartbeat, isSubstantiveTurn,
  isDocStaffProfile, isWorkerSpawn, lagState, receiptKey, receiptLabel, relativeAgo,
  relativeTime, seatColor, sessionState, stageNodes, turnPasses,
} from "../../dealroom/js/room.js";

const NOW = Date.parse("2026-08-22T15:00:00Z");
const at = (offsetSeconds) => new Date(NOW - offsetSeconds * 1000).toISOString();

function heartbeat(desks, cursor, ageSeconds = 20) {
  return { seq: "200", at: at(ageSeconds), sponsor: "joe", seat: "hermes", kind: "receipt",
    msg_id: "hb", body: JSON.stringify({ heartbeat: { desks, cursor, cycle_at: at(ageSeconds) } }) };
}

const DESK_ROWS = [
  { name: "joe-desk", seat: "claude", live: true, last_seen: at(30), auth: true },
  { name: "codex-desk", seat: "codex", live: true, last_seen: at(400), auth: true },
  { name: "old-desk", seat: "sol", live: false, last_seen: at(30), auth: true },
  { name: "unwired-desk", seat: null, live: false, last_seen: at(30), auth: null },
];

test("the wire's string sequences never leak into a numeric comparison", () => {
  const model = deriveModel([
    { seq: "9", at: at(60), sponsor: "joe", seat: "human", kind: "turn", body: "nine", msg_id: "a" },
    { seq: "100", at: at(30), sponsor: "joe", seat: "claude", kind: "turn", body: "hundred", msg_id: "b" },
  ], { now: NOW, viewer: "joe" });
  assert.equal(model.latestSeq, 100, "'9' must not sort above '100'");
});

test("the desk roster, its pulse states, and the dormant case all come from the heartbeat", () => {
  const model = deriveModel([heartbeat(DESK_ROWS, 199)], { now: NOW, viewer: "joe" });
  const byName = Object.fromEntries(model.desks.map((d) => [d.name, d]));
  assert.equal(byName["joe-desk"].state, "healthy", "live, signed in, seen 30s ago");
  assert.equal(byName["codex-desk"].state, "attention", "seen between two and ten minutes ago");
  assert.equal(byName["old-desk"].state, "urgent", "the heartbeat says it is not live");
  assert.equal(byName["unwired-desk"].state, "dormant", "no room seat means nothing is asking for you");
  assert.equal(model.onlineDesks, 2);
});

test("the pulse scale is exactly the ruled one, at both boundaries", () => {
  const base = { seat: "claude", live: true, auth: true };
  assert.equal(deskState({ ...base, seenAt: NOW - 119_000 }, NOW), "healthy");
  assert.equal(deskState({ ...base, seenAt: NOW - 121_000 }, NOW), "attention");
  assert.equal(deskState({ ...base, seenAt: NOW - 599_000 }, NOW), "attention");
  assert.equal(deskState({ ...base, seenAt: NOW - 601_000 }, NOW), "urgent");
  assert.equal(deskState({ ...base, seat: null, seenAt: NOW }, NOW), "dormant");
});

test("a signed-out desk is urgent however recently it was seen, and unknown sign-in is not", () => {
  const fresh = { seat: "claude", live: true, seenAt: NOW - 1000 };
  assert.equal(deskState({ ...fresh, auth: false }, NOW), "urgent");
  assert.equal(deskState({ ...fresh, auth: true }, NOW), "healthy");
  // A probe that could not answer must never be read as "signed out" — a Mac
  // with no vendor CLI installed would otherwise show a wall of red.
  assert.equal(deskState({ ...fresh, auth: null }, NOW), "healthy");
});

test("signed-in counts ignore desks whose probe could not answer", () => {
  const model = deriveModel([heartbeat([
    { name: "a", seat: "claude", live: true, last_seen: at(10), auth: true },
    { name: "b", seat: "codex", live: true, last_seen: at(10), auth: false },
    { name: "c", seat: "sol", live: true, last_seen: at(10), auth: null },
  ], 199)], { now: NOW, viewer: "joe" });
  assert.equal(model.signedIn, 1);
  assert.equal(model.authKnown, 2, "the unknown desk is excluded from the denominator, not counted as out");
  assert.equal(authState(model.signedIn, model.authKnown, model.desks.length), "urgent");
  assert.equal(authState(2, 2, 2), "healthy");
  assert.equal(authState(0, 0, 0), "dormant");
});

test("a backend worker rides the outer ring on its lifecycle turns alone", () => {
  const spawn = { seq: "75", at: at(300), sponsor: "joe", seat: "opus", kind: "system", msg_id: "s75",
    body: "WORKER SPAWNED — seat opus, a backend build worker under Joe's council session. "
      + "Mission: build the Model Room observatory. Executor: Opus, isolated worktree, relay attests." };
  assert.equal(isWorkerSpawn(spawn), true);
  assert.equal(isWorkerSpawn({ ...spawn, kind: "turn" }), false, "only a system turn announces a spawn");

  const running = deriveModel([heartbeat(DESK_ROWS, 199), spawn], { now: NOW, viewer: "joe" });
  const node = running.orbiters.find((o) => o.seat === "opus");
  assert.ok(node?.worker, "a worker has no desk, so it can only appear on the outer ring");
  assert.equal(node.state, "healthy", "it breathes from spawn until its completion receipt");
  assert.match(node.worker.mission, /Model Room observatory/);
  assert.match(node.worker.executor, /Opus/);

  const done = deriveModel([heartbeat(DESK_ROWS, 199), spawn,
    { seq: "300", at: at(10), sponsor: "joe", seat: "opus", kind: "receipt", msg_id: "r300",
      body: JSON.stringify({ shipped: { pr: 999 } }) }], { now: NOW, viewer: "joe" });
  assert.equal(done.orbiters.find((o) => o.seat === "opus").state, "dormant",
    "and it sits still once its completion receipt lands");
});

test("only turns from the last day reach the outer ring", () => {
  const stale = { seq: "1", at: at(48 * 3600), sponsor: "joe", seat: "grok", kind: "turn", body: "old", msg_id: "old" };
  const model = deriveModel([heartbeat(DESK_ROWS, 199), stale], { now: NOW, viewer: "joe" });
  assert.equal(model.orbiters.find((o) => o.seat === "grok"), undefined);
});

test("a seat that owns a desk is never duplicated onto the outer ring", () => {
  const model = deriveModel([heartbeat(DESK_ROWS, 199),
    { seq: "201", at: at(10), sponsor: "joe", seat: "claude", kind: "turn", body: "hi", msg_id: "x" }],
  { now: NOW, viewer: "joe" });
  assert.equal(model.orbiters.find((o) => o.seat === "claude"), undefined);
});

test("cycle age, cursor lag and error counts follow the ruled thresholds", () => {
  assert.equal(cycleState(149), "healthy");
  assert.equal(cycleState(151), "attention");
  assert.equal(cycleState(601), "urgent");
  assert.equal(cycleState(null), "urgent", "no heartbeat at all is the loudest state, not the quietest");

  // A single poll ahead of the bridge is the normal shape of a five-second
  // browser reading a wire a launchd job reads every minute.
  assert.equal(lagState(0, 0), "healthy");
  assert.equal(lagState(3, 1), "healthy");
  assert.equal(lagState(3, 2), "attention");
  assert.equal(lagState(null, 5), "dormant");

  assert.equal(errorState(0, null), "healthy");
  assert.equal(errorState(2, null), "attention");
  assert.equal(errorState(3, 2), "urgent", "a rising count is worse than a standing one");
  assert.equal(errorState(2, 2), "attention");
});

test("cursor lag is measured against the bridge's own published cursor", () => {
  const model = deriveModel([heartbeat(DESK_ROWS, 199),
    { seq: "205", at: at(5), sponsor: "joe", seat: "human", kind: "turn", body: "new", msg_id: "n" }],
  { now: NOW, viewer: "joe" });
  assert.equal(model.cursor, 199);
  assert.equal(model.latestSeq, 205);
  assert.equal(model.cursorLag, 6);
});

test("bridge receipts that report a failure are counted, and ordinary ones are not", () => {
  const receipt = (body) => ({ seq: "1", at: at(5), sponsor: "joe", seat: "hermes", kind: "receipt", body: JSON.stringify(body) });
  assert.equal(isErrorReceipt(receipt({ assignment_rejected: { reason: "malformed" } })), true);
  assert.equal(isErrorReceipt(receipt({ control_refused: { reason: "throttled" } })), true);
  assert.equal(isErrorReceipt(receipt({ desk: "codex-desk", timed_out_after_s: 1800 })), true);
  assert.equal(isErrorReceipt(receipt({ desk: "codex-desk", status: "quota_exhausted" })), true);
  assert.equal(isErrorReceipt(receipt({ desk_restarted: { desk: "joe-desk", restarted: false } })), true);
  assert.equal(isErrorReceipt(receipt({ assignment: { ref: "WR-1" }, status: "recorded_no_queue_verb_yet" })), true);
  assert.equal(isErrorReceipt(receipt({ heartbeat: { desks: [], cursor: 1 } })), false);
  assert.equal(isErrorReceipt(receipt({ control_executed: { action: "login" } })), false);
  assert.equal(isErrorReceipt({ kind: "turn", body: '{"control_refused":{}}' }), false,
    "a conversational turn quoting a receipt is not a receipt");
});

test("assignments come only from receipts carrying the grammar's assignment key, newest first", () => {
  const model = deriveModel([
    { seq: "10", at: at(600), sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "a1",
      body: JSON.stringify({ assignment: { seat: "codex", verb: "assign", ref: "WR-9", title: "first", by: "joe" } }) },
    { seq: "12", at: at(60), sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "a2",
      body: JSON.stringify({ assignment: { seat: "claude", verb: "claim", ref: "L-508", title: null, by: "joe" } }) },
    { seq: "13", at: at(30), sponsor: "joe", seat: "human", kind: "turn", msg_id: "a3",
      body: "@codex assign WR-9 not an assignment record, just the command" },
  ], { now: NOW, viewer: "joe" });
  assert.deepEqual(model.assignments.map((a) => a.ref), ["L-508", "WR-9"]);
});

test("presence reads the OTHER partner, and it comes from the session's viewer", () => {
  const turns = [
    { seq: "1", at: at(120), sponsor: "dell", seat: "human", kind: "turn", body: "morning", msg_id: "d" },
    { seq: "2", at: at(10), sponsor: "joe", seat: "human", kind: "turn", body: "morning", msg_id: "j" },
  ];
  const joeSees = deriveModel(turns, { now: NOW, viewer: "joe" });
  assert.equal(joeSees.presence.partner, "dell");
  assert.equal(joeSees.presence.state, "healthy");

  const dellSees = deriveModel(turns, { now: NOW, viewer: "dell" });
  assert.equal(dellSees.presence.partner, "joe");

  const quiet = deriveModel([{ seq: "1", at: at(4000), sponsor: "dell", seat: "human", kind: "turn", body: "hi", msg_id: "q" }],
    { now: NOW, viewer: "joe" });
  assert.equal(quiet.presence.state, "dormant", "beyond ten minutes the presence dot sits still");
});

test("filters compose, and heartbeat receipts stay out of the conversation by default", () => {
  const filters = { seats: new Set(), turns: true, system: true, receipts: true, heartbeats: false, text: "" };
  const hb = heartbeat(DESK_ROWS, 199);
  const chat = { seq: "1", at: at(10), sponsor: "joe", seat: "claude", kind: "turn", body: "deploy is green" };
  const assignment = { seq: "2", at: at(10), sponsor: "joe", seat: "hermes", kind: "receipt",
    body: JSON.stringify({ assignment: { ref: "WR-1" } }) };

  assert.equal(isHeartbeat(hb), true);
  assert.equal(receiptKey(assignment.body), "assignment");
  assert.equal(turnPasses(hb, filters), false);
  assert.equal(turnPasses(assignment, filters), true);
  assert.equal(turnPasses(hb, { ...filters, heartbeats: true }), true);

  assert.equal(turnPasses(chat, { ...filters, seats: new Set(["codex"]) }), false);
  assert.equal(turnPasses(chat, { ...filters, seats: new Set(["claude"]) }), true);
  // seat AND text must both admit it
  assert.equal(turnPasses(chat, { ...filters, seats: new Set(["claude"]), text: "green" }), true);
  assert.equal(turnPasses(chat, { ...filters, seats: new Set(["claude"]), text: "rollback" }), false);
  assert.equal(turnPasses(chat, { ...filters, turns: false }), false);
});

test("Conversation admits only useful Queue milestones, and says the projector summary rather than its bookkeeping", () => {
  const queue = (event) => ({ kind: "receipt", body: JSON.stringify({ queue_event: {
    v: 1, event, summary: "Sol returned Attest PR 514 for review.",
  } }) });
  assert.equal(isSubstantiveTurn(queue("claimed")), true);
  assert.equal(isSubstantiveTurn(queue("completed")), true);
  assert.equal(isSubstantiveTurn(queue("commented")), false);
  assert.deepEqual(describeReceipt(queue("completed").body, NOW), ["Sol returned Attest PR 514 for review."]);
});

test("seat colour is fixed per seat and falls back rather than throwing", () => {
  assert.equal(seatColor("human"), "#F08A2D");
  assert.equal(seatColor("CLAUDE"), "#2DD496");
  assert.equal(seatColor("codex"), seatColor("sol"));
  assert.equal(seatColor("a-seat-nobody-has-invented"), "#7D8BB0");
  assert.equal(seatColor(undefined), "#7D8BB0");
});

test("relative time is short, human, and never negative", () => {
  assert.equal(relativeTime(NOW - 14_000, NOW), "14s");
  assert.equal(relativeTime(NOW - 3 * 60_000, NOW), "3m");
  assert.equal(relativeTime(NOW - 5 * 3600_000, NOW), "5h");
  assert.equal(relativeTime(NOW + 9000, NOW), "0s", "a clock skew must not render as a negative age");
  assert.equal(relativeTime(0, NOW), "never");
});

test("session context gauges are parsed from status receipts, freshest per session", () => {
  const status = (name, pct, claimed, ageSeconds, sponsor = "joe") => ({
    seq: String(300 + ageSeconds), at: at(ageSeconds), sponsor, seat: "hermes", kind: "receipt",
    msg_id: `s${name}${ageSeconds}`,
    body: JSON.stringify({ session_status: { name, context_pct: pct, claimed } }),
  });

  const model = deriveModel([
    status("release-chain", 20, "PR #491 deploy gate", 600),
    status("release-chain", 82, "PR #491 deploy gate", 90),      // newer wins
    status("observatory-build", 55, "Model Room panel", 120, "dell"),
    status("napping-session", 30, "nothing", 50 * 60),           // stale but listed
    status("long-gone", 90, "finished hours ago", 3 * 60 * 60),  // outside the window
  ], { now: NOW, viewer: "joe" });

  assert.deepEqual(model.sessions.map((s) => s.name),
    ["release-chain", "observatory-build", "napping-session"],
    "loudest first, and a session silent for two hours is gone rather than quiet");
  const release = model.sessions[0];
  assert.equal(release.contextPct, 82, "the freshest receipt wins, not the first one seen");
  assert.equal(release.state, "urgent");
  assert.equal(release.claimed, "PR #491 deploy gate");
  assert.equal(release.stale, false);
  assert.equal(model.sessions[1].state, "attention");
  assert.equal(model.sessions[1].partner, "dell", "the bar takes the sponsoring partner's halo");
  assert.equal(model.sessions[2].stale, true, "past forty-five minutes it fades rather than lying");

  assert.equal(sessionState(49), "healthy");
  assert.equal(sessionState(50), "attention");
  assert.equal(sessionState(75), "attention");
  assert.equal(sessionState(76), "urgent");
  assert.equal(sessionState("not a number"), "dormant");
});

test("an empty wire derives an honest empty model rather than throwing", () => {
  const model = deriveModel([], { now: NOW, viewer: "joe" });
  assert.deepEqual(model.desks, []);
  assert.deepEqual(model.orbiters, []);
  assert.deepEqual(model.assignments, []);
  assert.equal(model.cursor, null);
  assert.equal(model.cursorLag, null);
  assert.equal(model.cycleAgeS, null);
  assert.equal(cycleState(model.cycleAgeS), "urgent");
});

/* ------------------------- the legibility round (Joe's ruling, 2026-08-22) */

test("receipt rows wear plain-language labels, never machine words", () => {
  const body = (key) => JSON.stringify({ [key]: {} });
  assert.equal(receiptLabel(body("worker_completed")), "Build finished");
  assert.equal(receiptLabel(body("heartbeat")), "Bridge check-in");
  assert.equal(receiptLabel(body("session_status")), "Session gauge");
  assert.equal(receiptLabel(body("some_future_shape")), "Some future shape",
    "an unknown key is humanized rather than shown raw");
  assert.equal(humanizeKey("desk_restarted"), "Desk restarted");
});

test("a heartbeat receipt reads as sentences, with no JSON syntax anywhere", () => {
  const receiptAt = NOW;
  const body = JSON.stringify({ heartbeat: { desks: [
    { name: "codex-desk", seat: "codex", live: true, last_seen: at(65), auth: true },
    { name: "joe-desk", seat: "claude", live: true, last_seen: at(3), auth: false },
  ], cursor: 211, cycle_at: at(2) } });
  const lines = describeReceipt(body, receiptAt);
  const text = lines.join("\n");
  assert.match(text, /codex-desk \(codex\)/);
  assert.match(text, /signed in/);
  assert.match(text, /SIGNED OUT/, "a signed-out desk is said loudly");
  assert.match(text, /seen 1m ago/, "ages anchor to the receipt's own moment");
  assert.match(text, /through turn 211/);
  for (const forbidden of ["{", "}", '":', "last_seen"]) {
    assert.ok(!text.includes(forbidden), `no machine syntax: ${forbidden}`);
  }
});

test("an unknown receipt shape still arrives as readable lines, not JSON", () => {
  const body = JSON.stringify({ deploy_probe: { status: "timed_out", timed_out_after_s: 30,
    detail: { attempt: 2, ok: false } } });
  const lines = describeReceipt(body, NOW);
  const text = lines.join("\n");
  assert.match(text, /Status: timed_out/);
  assert.match(text, /Ok: no/, "booleans become words");
  assert.ok(!text.includes("{") && !text.includes('"'), "no braces, no quoted keys");
});

test("the bridge figure is a phrase, not a delivered/latest fraction", () => {
  assert.equal(bridgeLagLabel(null), "—");
  assert.equal(bridgeLagLabel(0), "caught up");
  assert.equal(bridgeLagLabel(1), "1 turn behind");
  assert.equal(bridgeLagLabel(5), "5 turns behind");
});

test("every age says which direction it points", () => {
  assert.equal(relativeAgo(NOW - 3_000, NOW), "just now");
  assert.equal(relativeAgo(NOW - 2_000_000, NOW), "33m ago");
  assert.equal(relativeAgo(0, NOW), "never");
});

test("conversation mode admits exactly the turns where a model says something", () => {
  const prose = { kind: "turn", seat: "claude", sponsor: "joe", body: "The release chain is armed." };
  const noop = { kind: "turn", seat: "codex", sponsor: "joe", body: "NOOP" };
  const silent = { kind: "turn", seat: "claude", sponsor: "joe", body: "*(silent)*" };
  const system = { kind: "system", seat: "opus", sponsor: "joe", body: "WORKER SPAWNED — the observatory build." };
  const heartbeatTurn = { kind: "receipt", seat: "hermes", sponsor: "joe",
    body: JSON.stringify({ heartbeat: { desks: [], cursor: 1 } }) };
  assert.equal(isSubstantiveTurn(prose), true);
  assert.equal(isSubstantiveTurn(noop), false, "keep-alives are not conversation");
  assert.equal(isSubstantiveTurn(silent), false);
  assert.equal(isSubstantiveTurn(system), true, "spawn announcements are part of the story");
  assert.equal(isSubstantiveTurn(heartbeatTurn), false, "machine traffic by definition");

  // Composing with the existing filters: conversation overrides even a
  // receipts+heartbeats filter that would otherwise admit the machine row.
  const everythingOn = { seats: new Set(), turns: true, system: true, receipts: true,
    heartbeats: true, text: "", conversation: true };
  assert.equal(turnPasses(heartbeatTurn, everythingOn), false);
  assert.equal(turnPasses(noop, everythingOn), false);
  assert.equal(turnPasses(prose, everythingOn), true);
  assert.equal(turnPasses(prose, { ...everythingOn, conversation: false }), true,
    "everything mode is unchanged");
});

/* ------------------------------ the verifier's findings, folded in and pinned */

test("hidden kind filters cannot suppress the conversation (the false-empty trap)", () => {
  const prose = { kind: "turn", seat: "claude", sponsor: "joe", body: "Real words." };
  const system = { kind: "system", seat: "opus", sponsor: "joe", body: "WORKER SPAWNED — build." };
  // Kind chips toggled off in Everything, then the user switches to
  // Conversation where those controls are hidden: they must be inert.
  const hiddenOff = { seats: new Set(), turns: false, system: false, receipts: false,
    heartbeats: false, text: "", conversation: true };
  assert.equal(turnPasses(prose, hiddenOff), true);
  assert.equal(turnPasses(system, hiddenOff), true);
  assert.equal(turnPasses(prose, { ...hiddenOff, conversation: false }), false,
    "back in Everything the same flags apply again");
});

test("a pathologically nested receipt flattens without overflowing the stack", () => {
  let nested = { leaf: "value" };
  for (let i = 0; i < 5000; i += 1) nested = { wrap: nested };
  const lines = describeReceipt(JSON.stringify({ deep_probe: nested }), NOW);
  assert.ok(lines.length >= 1, "returns lines rather than throwing");
  assert.match(lines.join("\n"), /deeper detail in the machine view/);
});

test("a heartbeat with a malformed desk entry is skipped, not fatal", () => {
  const body = JSON.stringify({ heartbeat: { desks: [null, { name: "joe-desk", seat: "claude", live: true }], cursor: 7 } });
  const text = describeReceipt(body, NOW).join("\n");
  assert.match(text, /joe-desk/);
  assert.match(text, /through turn 7/);
});

test("the bridge figure defends its own floor", () => {
  assert.equal(bridgeLagLabel(NaN), "—");
  assert.equal(bridgeLagLabel("not a number"), "—");
  assert.equal(bridgeLagLabel(-1), "caught up", "a negative lag is a clock skew, not a debt");
  assert.equal(bridgeLagLabel(1.5), "2 turns behind");
});

test("both boolean words render, not just one (the surviving mutation)", () => {
  const text = describeReceipt(JSON.stringify({ probe: { ok: true, failed: false } }), NOW).join("\n");
  assert.match(text, /Ok: yes/);
  assert.match(text, /Failed: no/);
});

// ── Named agent profiles (loop 520) ─────────────────────────────────────────
// The NAME persists; the model behind it is staffing detail. Profile truth
// reaches the panel two ways and both must land in the model: an
// {"agent_profile":...} receipt the moment an assignment changes, and the full
// roster republished inside the throttled heartbeat so any feed window carries
// current truth. Latest-at wins per profile key.

function profileReceipt(seq, ageSeconds, profile) {
  return { seq: String(seq), at: at(ageSeconds), sponsor: "joe", seat: "claude",
    kind: "receipt", msg_id: `p${seq}`,
    body: JSON.stringify({ agent_profile: profile }) };
}

test("an assignment receipt lands the profile in the model", () => {
  const model = deriveModel([
    profileReceipt(10, 120, { key: "builder", name: "Builder", model: "opus", desk: null, status: "active" }),
  ], { now: NOW, viewer: "joe" });
  assert.equal(model.profiles.length, 1);
  assert.equal(model.profiles[0].key, "builder");
  assert.equal(model.profiles[0].model, "opus");
  assert.equal(model.profiles[0].status, "active");
});

test("the heartbeat roster republishes profile truth, and latest-at wins per key", () => {
  const hb = { seq: "200", at: at(20), sponsor: "joe", seat: "hermes", kind: "receipt",
    msg_id: "hbp", body: JSON.stringify({ heartbeat: {
      desks: DESK_ROWS, cursor: 200, cycle_at: at(20),
      profiles: [
        { key: "builder", name: "Builder", model: "sonnet", desk: "joe-desk", status: "active" },
        { key: "doc", name: "Doc", model: null, desk: null, status: "parked" },
      ] } }) };
  const model = deriveModel([
    profileReceipt(10, 120, { key: "builder", name: "Builder", model: "opus", desk: null, status: "active" }),
    hb,
  ], { now: NOW, viewer: "joe" });
  const byKey = Object.fromEntries(model.profiles.map((p) => [p.key, p]));
  assert.equal(byKey.builder.model, "sonnet", "the newer heartbeat roster outranks the older receipt");
  assert.equal(byKey.doc.status, "parked", "a parked identity is still on the roster — identity now, runtime later");
});

test("an older heartbeat roster never overwrites a newer assignment receipt", () => {
  const hb = { seq: "200", at: at(300), sponsor: "joe", seat: "hermes", kind: "receipt",
    msg_id: "hbp2", body: JSON.stringify({ heartbeat: {
      desks: [], cursor: 200, cycle_at: at(300),
      profiles: [{ key: "builder", name: "Builder", model: "sonnet", desk: null, status: "active" }] } }) };
  const model = deriveModel([
    hb,
    profileReceipt(300, 10, { key: "builder", name: "Builder", model: "opus", desk: null, status: "active" }),
  ], { now: NOW, viewer: "joe" });
  assert.equal(model.profiles[0].model, "opus");
});

test("a desk bound to a profile carries the profile for the label switch", () => {
  const hb = { seq: "200", at: at(20), sponsor: "joe", seat: "hermes", kind: "receipt",
    msg_id: "hbp3", body: JSON.stringify({ heartbeat: {
      desks: [{ name: "joe-desk", seat: "claude", live: true, last_seen: at(30), auth: true, profile: "builder" }],
      cursor: 200, cycle_at: at(20),
      profiles: [{ key: "builder", name: "Builder", model: "opus", desk: "joe-desk", status: "active" }] } }) };
  const model = deriveModel([hb], { now: NOW, viewer: "joe" });
  assert.equal(model.desks[0].profile?.name, "Builder");
  assert.equal(model.desks[0].profile?.model, "opus");
});

test("a profile receipt reads as a sentence, not flattened keys", () => {
  const staffed = describeReceipt(JSON.stringify(
    { agent_profile: { key: "builder", name: "Builder", model: "opus", desk: "joe-desk", status: "active" } }), NOW).join("\n");
  assert.match(staffed, /Builder staffed with opus on joe-desk/);
  const parked = describeReceipt(JSON.stringify(
    { agent_profile: { key: "doc", name: "Doc", model: null, desk: null, status: "parked" } }), NOW).join("\n");
  assert.match(parked, /Doc parked/);
  const unstaffed = describeReceipt(JSON.stringify(
    { agent_profile: { key: "reviewer", name: "Reviewer", model: null, desk: null, status: "unstaffed" } }), NOW).join("\n");
  assert.match(unstaffed, /Reviewer unstaffed/);
});

// ── Doc's staff are moons, not peers (loop 528) ──────────────────────────────
//
// Joe, 2026-08-25: "Hermes bots should show as moons orbiting the Doc profile,
// because Doc summons them." The sky's hierarchy is a claim about who summons
// whom, and it was wrong for as long as eight named seats were the same disc.
// These prove the three parts of that claim that do not need a browser: who is
// a lead, who is a moon, and that the partnership picture is left alone.

function profileRow(key, name, extra = {}) {
  return { key, name, model: "opus", desk: null, status: "active", ...extra };
}

function skyModel(profiles, desks = DESK_ROWS) {
  const hb = { seq: "200", at: at(20), sponsor: "joe", seat: "hermes", kind: "receipt",
    msg_id: "hb528", body: JSON.stringify({ heartbeat: {
      desks, cursor: 200, cycle_at: at(20), profiles } }) };
  return deriveModel([hb], { now: NOW, viewer: "joe" });
}

test("every named profile except Doc is one of Doc's staff", () => {
  assert.equal(isDocStaffProfile("doc"), false, "Doc summons; Doc is not summoned");
  assert.equal(isDocStaffProfile("DOC"), false, "the key is matched case-insensitively");
  for (const key of ["builder", "reviewer", "designer", "deal-steward",
                     "intake-clerk", "marketing-ops", "system-watch"]) {
    assert.equal(isDocStaffProfile(key), true, `${key} is staff Doc summons`);
  }
  assert.equal(isDocStaffProfile(null), false, "an unnamed seat is not staff");
  assert.equal(isDocStaffProfile(""), false, "an unnamed seat is not staff");
});

test("Doc rides the inner ring with the lead desks and its staff ride the moon tier", () => {
  const nodes = stageNodes(skyModel([
    profileRow("doc", "Doc"),
    profileRow("builder", "Builder"),
    profileRow("reviewer", "Reviewer"),
    profileRow("designer", "Designer"),
    profileRow("deal-steward", "Deal Steward"),
  ]));
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  assert.equal(byId["profile:doc"].ring, "inner", "Doc is a lead, on a full disc");
  for (const key of ["builder", "reviewer", "designer", "deal-steward"]) {
    assert.equal(byId[`profile:${key}`].ring, "moon", `${key} orbits Doc, not the wire`);
  }
  assert.equal(byId["desk:joe-desk"].ring, "inner", "claude is a lead desk");
  assert.equal(byId["desk:codex-desk"].ring, "inner", "codex is a lead desk");
});

test("a desk staffed by one of Doc's staff is a moon, however it got its runtime", () => {
  const nodes = stageNodes(skyModel(
    [profileRow("doc", "Doc"), profileRow("builder", "Builder", { desk: "hermes-desktop" })],
    [...DESK_ROWS, { name: "hermes-desktop", seat: "hermes", live: true, last_seen: at(30), auth: true }]));
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  assert.equal(byId["desk:hermes-desktop"].ring, "moon", "the hierarchy follows the profile, not the desk row");
  assert.equal(byId["desk:hermes-desktop"].label, "Builder");
  assert.equal(byId["profile:builder"], undefined, "a bound profile is not also drawn parked");
});

test("the outer ring and the partnership hemispheres are untouched by the moon tier", () => {
  const model = deriveModel([
    { seq: "200", at: at(20), sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "hb528b",
      body: JSON.stringify({ heartbeat: { desks: DESK_ROWS, cursor: 200, cycle_at: at(20),
        profiles: [profileRow("doc", "Doc"), profileRow("system-watch", "System Watch")] } }) },
    { seq: "201", at: at(60), sponsor: "dell", seat: "grok", kind: "turn", body: "here", msg_id: "g1" },
  ], { now: NOW, viewer: "joe" });
  const nodes = stageNodes(model);
  const grok = nodes.find((n) => n.id === "seat:grok");
  assert.equal(grok.ring, "outer", "a seat heard on the wire still rides the outer ring");
  assert.equal(grok.partner, "dell", "and keeps its sponsoring partner's hemisphere");
  assert.equal(nodes.find((n) => n.id === "profile:doc").partner, "joe",
    "Doc sits in the viewer's hemisphere — it is never moved to the centre");
});
