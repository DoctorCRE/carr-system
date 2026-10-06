// Deal Room board sync: where the values on the board come from, and what
// happens when two reads, a write and a poll are in flight at once.
//
// Every ordering claim here is EXECUTED. The reads are deferred promises
// resolved in a deliberate order, so an overlapping refresh, a slow read, a
// failed read and a poll that lands late are real sequences rather than
// descriptions of one. Source assertions appear only where the claim is about
// placement — that a code path was removed, or that app.js wires this module in
// rather than keeping a second copy of the rule.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  createBoardSync, createHolds, mergeBoardSnapshot, batchTouchesBoard,
  resolveCurrentRow, SYNC_STATES, HEALTH,
} from "../../dealroom/js/board-sync.mjs";

const file = (relative) => readFile(new URL(`../../${relative}`, import.meta.url), "utf8");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

/** Let every already-settled microtask run before asserting. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

const deal = (overrides = {}) => ({
  id: "d1", name: "Riverbank Dental", phase: "Research", owner: "joe", attention: false,
  next_step: "Call broker", next_date: "2026-10-01", operating_state: "active",
  parking_reason: null, account_client_id: null, ...overrides,
});

const board = (deals, extra = {}) => ({ actor: "joe", deals, accounts: [], ...extra });

const changes = (events = [], extra = {}) => ({
  events, presence: [], capture_sessions: [], cursor: "c1", ...extra,
});

const changeEvent = (overrides = {}) => ({
  id: "e1", recorded_at: "2026-09-10T14:00:00.000000+00:00", actor: "dell",
  verb: "patch-deal-field", subject_type: "deal", subject_id: "d1",
  field: "phase", old_value: "Research", new_value: "Negotiation", ...overrides,
});

/**
 * A coordinator over scripted reads. `boardReads` and `changeReads` are queues
 * of thunks; each call takes the next one, so a test decides exactly what the
 * Nth read does and when it answers.
 */
function harness({ boardReads = [], changeReads = [], now = null } = {}) {
  const applied = [];
  const statuses = [];
  let clock = 1000;
  const sync = createBoardSync({
    readBoard: () => {
      const next = boardReads.shift();
      if (!next) throw new Error("no scripted board read left");
      return next();
    },
    readChanges: (cursor) => {
      const next = changeReads.shift();
      if (!next) throw new Error("no scripted change read left");
      return next(cursor);
    },
    applyBoard: (payload, meta) => { applied.push({ payload, meta }); },
    onStatus: (status) => { statuses.push(status); },
    now: now || (() => { clock += 1; return clock; }),
  });
  return {
    sync, applied, statuses,
    last: () => applied.at(-1),
    deals: () => (applied.at(-1)?.payload.deals || []),
    dealById: (id) => (applied.at(-1)?.payload.deals || []).find((row) => row.id === id) || null,
  };
}

// ------------------------------------------------- values come from the board

test("a board snapshot is the only thing that sets a value, and the feed never does", async () => {
  const ancient = changeEvent({
    id: "old-1", recorded_at: "2025-01-04T09:00:00.000000+00:00",
    field: "phase", old_value: "On Deck", new_value: "On Deck",
  });
  const h = harness({
    boardReads: [() => Promise.resolve(board([deal({ phase: "Closing" })]))],
    changeReads: [() => Promise.resolve(changes([ancient], { cursor: "page-1" }))],
  });

  await h.sync.refreshBoard({ reason: "load" });
  assert.equal(h.dealById("d1").phase, "Closing");

  // The oldest page of the log arrives. It carries a phase from January.
  const poll = await h.sync.pollChanges();
  assert.equal(poll.applied, true);
  assert.equal(poll.changes.events[0].new_value, "On Deck", "the page really does carry an old value");
  assert.equal(h.applied.length, 1, "polling applied no board snapshot of its own");
  assert.equal(h.dealById("d1").phase, "Closing", "and the board value did not move");
  assert.equal(h.sync.status().cursor, "page-1", "the cursor advanced; only the values are off limits");
});

test("app.js no longer copies any field value out of a change event", async () => {
  const app = await file("dealroom/js/app.js");
  const pollOnce = app.slice(app.indexOf("async function pollOnce"), app.indexOf("function userIsEditing"));
  assert.ok(pollOnce.includes("const batch = result.events || [];"), "the slice is the poll");
  for (const field of ["phase", "owner", "attention", "next_date", "next_step", "operating_state"]) {
    assert.doesNotMatch(pollOnce, new RegExp(`deal\\.${field}\\s*=`),
      `pollOnce must not write ${field} from an event`);
  }
  assert.doesNotMatch(pollOnce, /event\.new_value/, "no event value reaches the board at all");
  // What the feed still does, all of it exercised by deal-change-receipts.test.mjs.
  assert.match(pollOnce, /state\.presence = result\.presence \|\| \[\];/);
  assert.match(pollOnce, /state\.captureSessions = result\.capture_sessions \|\| \[\];/);
  // The feed still names each cell's base — through the one function that also
  // takes a write's answer, so neither source can drag a base backwards.
  assert.match(pollOnce, /noteCellBase\(cellKey\(event\.subject_id, event\.field\), event\)/);
  assert.equal((app.match(/state\.fieldBase\.set\(/g) || []).length, 1,
    "and exactly one place writes that map");
  assert.match(pollOnce, /ingestChangeEvents\(state\.receipts, batch,/);
  assert.match(pollOnce, /batchTouchesBoard\(batch\)\) state\.boardSync\.requestRefresh\('change-feed'\)/,
    "news asks for an authoritative read instead of applying itself");
});

// ------------------------------------------------------ serialized board reads

test("overlapping refreshes never run two board reads, and collapse into one follow-up", async () => {
  const first = deferred();
  const second = deferred();
  const h = harness({ boardReads: [() => first.promise, () => second.promise] });

  const a = h.sync.refreshBoard({ reason: "load" });
  const b = h.sync.refreshBoard({ reason: "user" });
  const c = h.sync.refreshBoard({ reason: "user-again" });
  await settle();
  assert.equal(h.sync.stats().board_reads, 1, "the second and third requests started no read");
  assert.equal(h.sync.status().refresh_pending, true);

  first.resolve(board([deal({ phase: "Research" })]));
  await settle();
  assert.equal(h.sync.stats().board_reads, 2, "exactly one follow-up, however many asked");
  second.resolve(board([deal({ phase: "Legal" })]));

  const [outA, outB, outC] = await Promise.all([a, b, c]);
  assert.equal(outA.reason, "applied");
  assert.equal(outB, outC, "the coalesced callers share one answer");
  assert.equal(outB.reason, "applied");
  assert.equal(h.applied.length, 2);
  assert.equal(h.dealById("d1").phase, "Legal", "the later snapshot is the one on screen");
  assert.equal(h.sync.stats().board_coalesced, 1);
  assert.equal(h.sync.status().refresh_pending, false);
});

test("a caller that asks after its own write waits for a NEW read, not the open one", async () => {
  // The open read was issued before the write was confirmed, so joining it
  // would answer with data that cannot contain it. This is why a request that
  // arrives mid-flight queues a fresh read instead of sharing the current one.
  const open = deferred();
  const follow = deferred();
  const h = harness({ boardReads: [() => open.promise, () => follow.promise] });

  h.sync.refreshBoard({ reason: "background" });
  await settle();
  h.sync.noteLocalWrite("d1", { phase: "Closing" });
  const afterWrite = h.sync.refreshBoard({ reason: "after-write" });
  open.resolve(board([deal({ phase: "Research" })]));
  await settle();
  follow.resolve(board([deal({ phase: "Closing" })]));

  const outcome = await afterWrite;
  assert.equal(outcome.reason, "applied");
  assert.equal(outcome.ticket.reason, "after-write", "the caller got its own read's answer");
  assert.equal(h.applied.length, 2, "both reads landed; the caller waited for the second");
  assert.equal(h.dealById("d1").phase, "Closing");
});

test("a superseded answer is discarded: it applies nothing and moves no clock", async () => {
  const hung = deferred();
  const fresh = deferred();
  const h = harness({ boardReads: [() => hung.promise, () => fresh.promise] });

  const hungRead = h.sync.refreshBoard({ reason: "first" });
  await settle();
  // The reconnect path: do not queue behind a read that may never answer.
  const forced = h.sync.refreshBoard({ reason: "reconnect", force: true });
  fresh.resolve(board([deal({ phase: "Closing" })]));
  assert.equal((await forced).applied, true);
  const readAt = h.sync.status().last_read_at;

  hung.resolve(board([deal({ phase: "On Deck" })]));
  const late = await hungRead;
  assert.equal(late.applied, false);
  assert.equal(late.reason, "superseded");
  assert.equal(h.applied.length, 1, "the late answer never reached the page");
  assert.equal(h.dealById("d1").phase, "Closing");
  assert.equal(h.sync.status().last_read_at, readAt, "and it did not claim a fresh read");
  assert.equal(h.sync.stats().board_superseded, 1);
});

// --------------------------------------------------- slow, failed, recovering

test("a failed read leaves the last successful values on screen and says so", async () => {
  const h = harness({
    boardReads: [
      () => Promise.resolve(board([deal({ phase: "Research" })])),
      () => Promise.reject(new Error("board read -> 503")),
      () => Promise.resolve(board([deal({ phase: "Legal" })])),
    ],
  });

  await h.sync.refreshBoard({ reason: "load" });
  const good = h.sync.status();
  assert.equal(good.state, SYNC_STATES.READY);
  assert.ok(good.last_read_at, "a successful read is dated");

  const failed = await h.sync.refreshBoard({ reason: "retry" });
  assert.equal(failed.ok, false);
  assert.equal(failed.reason, "read_failed");
  const after = h.sync.status();
  assert.equal(after.state, SYNC_STATES.RECONNECTING);
  assert.equal(after.last_read_at, good.last_read_at,
    "the freshness of the values on screen is unchanged, because they are unchanged");
  assert.match(after.last_error, /503/);
  assert.equal(h.applied.length, 1, "nothing was re-rendered from a failure");
  assert.equal(h.dealById("d1").phase, "Research");

  await h.sync.refreshBoard({ reason: "retry" });
  assert.equal(h.sync.status().state, SYNC_STATES.READY);
  assert.equal(h.sync.status().last_error, null);
  assert.notEqual(h.sync.status().last_read_at, good.last_read_at);
  assert.equal(h.dealById("d1").phase, "Legal");
});

test("the badge never claims more than has actually been read", async () => {
  const slow = deferred();
  const h = harness({
    boardReads: [() => slow.promise, () => Promise.resolve(board([deal()]))],
    changeReads: [() => Promise.resolve(changes())],
  });
  assert.equal(h.sync.status().state, SYNC_STATES.STARTING);
  assert.equal(h.sync.status().last_read_at, null);

  const read = h.sync.refreshBoard({ reason: "load" });
  assert.equal(h.statuses.at(-1).state, SYNC_STATES.SYNCING, "a read a person is waiting on says so");

  // A successful change poll is not evidence that the VALUES are current.
  await h.sync.pollChanges();
  assert.equal(h.sync.status().state, SYNC_STATES.SYNCING);
  assert.equal(h.sync.status().snapshots, 0);
  assert.equal(h.sync.status().feed_health, HEALTH.OK);
  assert.equal(h.sync.status().board_health, HEALTH.UNREAD,
    "the feed's success is recorded against the feed and nothing else");

  slow.resolve(board([deal()]));
  await read;
  assert.equal(h.sync.status().state, SYNC_STATES.READY);
  assert.equal(h.sync.status().snapshots, 1);

  // A background refresh (the feed asked for it) changes no badge while it runs.
  const quiet = h.sync.requestRefresh("change-feed");
  assert.equal(h.sync.status().state, SYNC_STATES.READY, "a background read does not flash the badge");
  await quiet;
  assert.equal(h.sync.status().state, SYNC_STATES.READY);
});

// Regression, root's reproduction: after a board read failed, a poll that
// succeeded with an empty page repainted the badge "ready" — a working feed
// answering for values it does not carry.
test("a working feed cannot clear a failed board read, and a fresh board cannot hide a failed feed", async () => {
  const h = harness({
    boardReads: [
      () => Promise.resolve(board([deal()])),
      () => Promise.reject(new Error("board -> 503")),
      () => Promise.resolve(board([deal({ phase: "Legal" })])),
    ],
    changeReads: [
      () => Promise.resolve(changes([], { cursor: "c1" })),
      () => Promise.resolve(changes([], { cursor: "c2" })),
      () => Promise.reject(new Error("changes -> 500")),
      () => Promise.resolve(changes([], { cursor: "c3" })),
    ],
  });
  await h.sync.refreshBoard({ reason: "load" });
  await h.sync.pollChanges();
  assert.equal(h.sync.status().state, SYNC_STATES.READY);

  // One direction: the board read fails, then the feed answers.
  await h.sync.refreshBoard({ reason: "retry" });
  assert.equal(h.sync.status().board_health, HEALTH.FAILED);
  const afterFeed = await h.sync.pollChanges();
  assert.equal(afterFeed.applied, true, "the feed really did answer");
  assert.equal(h.sync.status().feed_health, HEALTH.OK);
  assert.equal(h.sync.status().state, SYNC_STATES.RECONNECTING,
    "a feed that works says nothing about whether the values are current");
  assert.match(h.sync.status().last_error, /503/, "and the board's trouble is the one reported");
  assert.equal(h.sync.status().cursor, "c2", "while the feed still did its own job");

  // The other direction: the board recovers while the feed is the broken one.
  const feedFailed = await h.sync.pollChanges();
  assert.equal(feedFailed.ok, false);
  assert.equal(h.sync.status().feed_health, HEALTH.FAILED);
  await h.sync.refreshBoard({ reason: "retry" });
  assert.equal(h.sync.status().board_health, HEALTH.OK);
  assert.equal(h.dealById("d1").phase, "Legal", "the snapshot did apply");
  assert.equal(h.sync.status().state, SYNC_STATES.RECONNECTING,
    "a fresh snapshot does not hide a feed that stopped answering");
  assert.match(h.sync.status().last_error, /500/);

  await h.sync.pollChanges();
  assert.equal(h.sync.status().state, SYNC_STATES.READY, "both paths healthy, and only then");
  assert.equal(h.sync.status().last_error, null);
});

// Regression, root's reproduction: start A, queue B, force C, settle A — and B
// started a third read behind C, bumped the sequence, and stopped C applying.
test("forcing a read cancels the queued one, so old work cannot overtake the new generation", async () => {
  const a = deferred();
  const c = deferred();
  const h = harness({ boardReads: [() => a.promise, () => c.promise] });

  const readA = h.sync.refreshBoard({ reason: "A" });
  const queuedB = h.sync.refreshBoard({ reason: "B" });
  await settle();
  assert.equal(h.sync.status().refresh_pending, true);

  const forcedC = h.sync.refreshBoard({ reason: "C", force: true });
  assert.equal(h.sync.status().refresh_pending, false, "the queued read is out of the queue");
  assert.equal(h.sync.stats().board_reads, 2, "C started; B did not");
  assert.equal(h.sync.stats().board_abandoned, 1, "and A is no longer waited on");

  a.resolve(board([deal({ phase: "On Deck" })]));
  await settle();
  assert.equal(h.sync.stats().board_reads, 2, "B did not start behind C when A settled");
  assert.equal(h.applied.length, 0, "and A's own answer, abandoned, applied nothing");

  c.resolve(board([deal({ phase: "Legal" })]));
  const outC = await forcedC;
  assert.equal(outC.applied, true, "C applies, which is the whole point of forcing it");
  assert.equal(h.dealById("d1").phase, "Legal");

  // B's caller is answered rather than left waiting on a read that was dropped.
  const outB = await queuedB;
  assert.equal(outB.applied, true, "B was re-homed onto C rather than run late");
  assert.equal(outB.ticket.reason, "C");
  assert.equal(h.sync.stats().board_rehomed, 1);
  assert.equal((await readA).reason, "superseded");
  assert.equal(h.sync.stats().board_reads, 2, "three requests, two reads, none of them stale");
});

// Regression, root's reproduction: setOnline bumped the generation but left the
// hung feed read registered, so every later tick skipped as "busy" forever.
test("a reconnect supersedes a hung feed, and its late answer cannot move the cursor", async () => {
  const hung = deferred();
  const h = harness({
    boardReads: [() => Promise.resolve(board([deal()]))],
    changeReads: [
      () => Promise.resolve(changes([], { cursor: "c1" })),
      () => hung.promise,
      () => Promise.resolve(changes([], { cursor: "c3" })),
    ],
  });
  await h.sync.refreshBoard({ reason: "load" });
  await h.sync.pollChanges();
  assert.equal(h.sync.status().cursor, "c1");

  const stuck = h.sync.pollChanges();
  assert.equal((await h.sync.pollChanges()).reason, "busy", "an interval tick is right to skip");

  h.sync.setOnline(false);
  h.sync.setOnline(true);
  assert.equal(h.sync.stats().change_abandoned, 1, "the hung read is no longer waited on");

  const fresh = await h.sync.pollChanges();
  assert.equal(fresh.applied, true, "polling works again after a reconnect");
  assert.equal(h.sync.status().cursor, "c3");

  // The hung read answers at last, carrying a page built on the OLD cursor.
  hung.resolve(changes([changeEvent({ id: "late" })], { cursor: "c2" }));
  assert.equal((await stuck).reason, "superseded");
  assert.equal(h.sync.status().cursor, "c3", "a late answer cannot move the cursor backwards");
  assert.equal(h.sync.stats().change_superseded, 1);
});

test("a poll can be forced past a read that never answers, without waiting for an offline event", async () => {
  const hung = deferred();
  const h = harness({
    changeReads: [() => hung.promise, () => Promise.resolve(changes([], { cursor: "c9" }))],
  });
  const stuck = h.sync.pollChanges();
  assert.equal((await h.sync.pollChanges()).reason, "busy");

  const forced = await h.sync.pollChanges({ force: true });
  assert.equal(forced.applied, true);
  assert.equal(h.sync.status().cursor, "c9");
  assert.equal(h.sync.stats().change_abandoned, 1);

  hung.resolve(changes([], { cursor: "c-old" }));
  assert.equal((await stuck).reason, "superseded");
  assert.equal(h.sync.status().cursor, "c9");
});

test("going offline stops trusting what is in flight; coming back only says we are trying", async () => {
  const open = deferred();
  const openChanges = deferred();
  const h = harness({
    boardReads: [() => open.promise],
    changeReads: [() => openChanges.promise],
  });
  const read = h.sync.refreshBoard({ reason: "load" });
  const poll = h.sync.pollChanges();
  await settle();

  h.sync.setOnline(false);
  assert.equal(h.sync.status().state, SYNC_STATES.OFFLINE);

  open.resolve(board([deal({ phase: "On Deck" })]));
  openChanges.resolve(changes([], { cursor: "c9" }));
  assert.equal((await read).reason, "superseded", "an answer from before the drop is not evidence about now");
  assert.equal((await poll).reason, "superseded");
  assert.equal(h.applied.length, 0);
  assert.equal(h.sync.status().cursor, null, "and a discarded page does not move the cursor");

  h.sync.setOnline(true);
  assert.equal(h.sync.status().state, SYNC_STATES.RECONNECTING,
    "back on the network is not the same as having read anything");
  assert.equal(h.sync.status().snapshots, 0);
  assert.equal(h.sync.status().board_health, HEALTH.FAILED);
  assert.equal(h.sync.status().feed_health, HEALTH.FAILED,
    "both paths stay unproven until each one answers again");
  assert.match(h.sync.status().last_error, /no network connection/i);
});

// ------------------------------------- a confirmed local write is not undone

test("a snapshot issued before a confirmed write cannot put the old value back", async () => {
  const open = deferred();
  const next = deferred();
  const h = harness({ boardReads: [() => open.promise, () => next.promise] });

  const inFlight = h.sync.refreshBoard({ reason: "background" });
  await settle();
  // The partner changes the phase and parks the record; the server said yes to
  // both while the read above was still open.
  h.sync.noteLocalWrite("d1", { phase: "Closing" });
  h.sync.noteLocalWrite("d1", { operating_state: "parked", parking_reason: "client_paused" });
  assert.equal(h.sync.status().held_fields, 3);

  open.resolve(board([deal({ phase: "Research", operating_state: "active", next_step: "Call broker" })]));
  await inFlight;
  const shown = h.dealById("d1");
  assert.equal(shown.phase, "Closing", "the confirmed value stands");
  assert.equal(shown.operating_state, "parked");
  assert.equal(shown.parking_reason, "client_paused");
  assert.equal(shown.next_step, "Call broker", "every field the write did not touch is the board's");
  assert.deepEqual(h.last().meta.held_fields.sort(),
    ["d1|operating_state", "d1|parking_reason", "d1|phase"]);
  assert.equal(h.sync.status().held_fields, 3, "still held: that read could not have contained them");

  // The next read is issued AFTER the write was confirmed, so it is allowed to
  // disagree — including with a newer value someone else wrote.
  const later = h.sync.refreshBoard({ reason: "after-write" });
  next.resolve(board([deal({ phase: "Legal", operating_state: "parked", parking_reason: "other" })]));
  await later;
  assert.equal(h.dealById("d1").phase, "Legal", "the board is authoritative again");
  assert.equal(h.dealById("d1").parking_reason, "other");
  assert.equal(h.sync.status().held_fields, 0, "and nothing is held any more");
});

// ------------------------------------------- identity across a replacement

// Regression, independent review's blocking defect 1: an open review agenda
// captured row OBJECTS, and snapshot replacement left it rendering the values
// they had when it started — permanently, from the first refresh onward.
test("a snapshot's per-cell bases reach the app untouched, even under a held value", async () => {
  // The board read now carries `field_base` — each editable cell's latest
  // committed event — beside the values. The coordinator must pass it through: it
  // is not a value, no hold applies to it, and the app's own forward-only rule is
  // what decides whether an older one is taken. This is the pairing the write path
  // depends on: a HELD value on a row whose base is the older one the read saw.
  const open = deferred();
  const h = harness({ boardReads: [() => open.promise] });
  const base = { phase: { id: "e5", recorded_at: "2026-09-10T14:00:00.000000+00:00" } };

  const inFlight = h.sync.refreshBoard({ reason: "background" });
  await settle();
  h.sync.noteLocalWrite("d1", { phase: "Closing" });
  open.resolve(board([deal({ phase: "Research", field_base: base })]));
  await inFlight;

  const shown = h.dealById("d1");
  assert.equal(shown.phase, "Closing", "the confirmed value still stands over the older read");
  assert.deepEqual(shown.field_base, base, "and the read's bases arrive exactly as the server sent them");
  assert.deepEqual(h.last().meta.held_fields, ["d1|phase"],
    "the hold is on the value; the base is not a value and is not held");
});

test("a row captured before a snapshot resolves to current values by id, in the captured order", async () => {
  const h = harness({
    boardReads: [
      () => Promise.resolve(board([
        deal({ id: "d1", phase: "Research", next_step: "Call broker" }),
        deal({ id: "d2", name: "Cooper Vet", phase: "Legal" }),
        deal({ id: "d3", name: "Left The Board" }),
      ])),
      () => Promise.resolve(board([
        deal({ id: "d2", name: "Cooper Vet", phase: "Closing" }),
        deal({ id: "d1", phase: "Negotiation", next_step: "Send LOI" }),
      ])),
    ],
  });
  await h.sync.refreshBoard({ reason: "load" });
  // What an agenda captures when it starts: the row objects, in the order it
  // chose — which is not the board's order and must not become it.
  const captured = [h.dealById("d1"), h.dealById("d3"), h.dealById("d2")];

  await h.sync.refreshBoard({ reason: "periodic" });
  assert.equal(captured[0].phase, "Research",
    "the defect itself, executed: a captured copy never changes again");
  assert.equal(h.dealById("d1").phase, "Negotiation", "while the board moved on");

  const rows = new Map(h.deals().map((row) => [row.id, row]));
  const resolved = captured.map((row) => resolveCurrentRow(row, rows));
  assert.deepEqual(resolved.map((row) => row.id), ["d1", "d3", "d2"],
    "the captured set and order are preserved: nothing joins or leaves mid-review");
  assert.equal(resolved[0].phase, "Negotiation", "and each row reads what the board says now");
  assert.equal(resolved[0].next_step, "Send LOI");
  assert.equal(resolved[2].phase, "Closing");
  assert.equal(resolved[1], captured[1],
    "a record the board no longer holds stays as last seen — not dropped, not invented");

  // Shapes that must not throw in the middle of a review.
  assert.equal(resolveCurrentRow(null, rows), null);
  assert.equal(resolveCurrentRow(undefined, rows), null);
  assert.equal(resolveCurrentRow({ name: "no id" }, rows).name, "no id");
  assert.equal(resolveCurrentRow(captured[0], null), captured[0]);
  assert.equal(resolveCurrentRow(captured[0], h.deals()).phase, "Negotiation",
    "a plain list resolves the same way a Map does");
});

// Regression, independent review's residual 6: a request landing in the one
// microtask between a read clearing and its follow-up starting used to see no
// open read, start its own, and then be superseded by the follow-up.
test("a request that lands between a read settling and its follow-up starting does not double-read", async () => {
  const first = deferred();
  const h = harness({
    boardReads: [() => first.promise, () => Promise.resolve(board([deal({ phase: "Legal" })]))],
  });

  const readA = h.sync.refreshBoard({ reason: "A" });
  // Registered BEFORE the queued entry's own callback, so it runs in exactly
  // that window: boardRun already cleared, the follow-up not yet started.
  let inGap = null;
  readA.then(() => { inGap = h.sync.refreshBoard({ reason: "gap" }); });
  const queuedB = h.sync.refreshBoard({ reason: "B" });

  first.resolve(board([deal({ phase: "Research" })]));
  await settle();
  assert.equal(h.sync.stats().board_reads, 2, "one follow-up, not two concurrent reads");
  assert.equal(h.sync.stats().board_superseded, 0, "so nothing had to be thrown away");
  const outcome = await inGap;
  assert.equal(outcome.applied, true, "the request in the gap is answered by the follow-up");
  assert.equal(outcome.ticket.reason, "B");
  assert.equal((await queuedB).ticket.reason, "B");
  assert.equal(h.dealById("d1").phase, "Legal");
});

test("a hold survives a failed read, and is not resurrected onto a record the board dropped", async () => {
  const h = harness({
    boardReads: [
      () => Promise.reject(new Error("gone")),
      () => Promise.resolve(board([deal({ id: "d2", name: "Cooper Vet" })])),
    ],
  });
  h.sync.noteLocalWrite("d1", { attention: true });
  await h.sync.refreshBoard({ reason: "load" });
  assert.equal(h.sync.status().held_fields, 1, "a read that failed released nothing");

  await h.sync.refreshBoard({ reason: "retry" });
  assert.deepEqual(h.deals().map((row) => row.id), ["d2"],
    "the board decides which records exist; a hold does not re-create one");
  assert.equal(h.sync.status().held_fields, 0);
});

test("a render fault is not a read fault, and a board that could not be shown is never ready", async () => {
  const applied = [];
  let explode = true;
  const sync = createBoardSync({
    readBoard: async () => board([deal()]),
    readChanges: async () => changes(),
    applyBoard: (payload) => {
      if (explode) throw new Error("render blew up");
      applied.push(payload);
    },
  });
  sync.noteLocalWrite("d1", { attention: true });
  const outcome = await sync.refreshBoard({ reason: "load" });
  assert.equal(outcome.reason, "apply_failed");
  assert.equal(sync.status().snapshots, 0, "an unrendered snapshot is not a snapshot");
  assert.equal(sync.status().last_read_at, null);
  assert.equal(sync.status().held_fields, 1);
  // The screen is now older than an answer we already threw away. Saying
  // "ready" would be a claim about the page that the page cannot support.
  assert.equal(sync.status().board_health, HEALTH.RENDER_FAILED);
  assert.equal(sync.status().state, SYNC_STATES.ERROR);
  assert.match(sync.status().last_error, /render blew up/);

  // A working feed does not repair it either: the feed did not fail.
  await sync.pollChanges();
  assert.equal(sync.status().feed_health, HEALTH.OK);
  assert.equal(sync.status().state, SYNC_STATES.ERROR, "still not ready, and still not a connection problem");

  explode = false;
  await sync.refreshBoard({ reason: "retry" });
  assert.equal(applied.length, 1);
  assert.equal(sync.status().snapshots, 1);
  assert.equal(sync.status().state, SYNC_STATES.READY, "a snapshot that reached the page clears it");
  assert.equal(sync.status().last_error, null);
});

// -------------------------------------------------------- serialized polling

test("a poll tick during an open poll is dropped, and a slow page cannot land after a newer one", async () => {
  const first = deferred();
  const h = harness({
    changeReads: [
      () => first.promise,
      () => Promise.resolve(changes([changeEvent({ id: "e2" })], { cursor: "c2" })),
    ],
  });

  const slow = h.sync.pollChanges();
  const tick = await h.sync.pollChanges();
  assert.equal(tick.applied, false);
  assert.equal(tick.reason, "busy");
  assert.equal(h.sync.stats().change_reads, 1, "the interval's second tick issued no request");
  assert.equal(h.sync.stats().change_skipped, 1);

  first.resolve(changes([changeEvent({ id: "e1" })], { cursor: "c1" }));
  assert.equal((await slow).applied, true);
  assert.equal(h.sync.status().cursor, "c1");

  const second = await h.sync.pollChanges();
  assert.equal(second.applied, true);
  assert.equal(h.sync.status().cursor, "c2", "the cursor only ever moves forward, one applied page at a time");
});

test("a failed poll is visible and does not move the cursor", async () => {
  const h = harness({
    changeReads: [
      () => Promise.resolve(changes([], { cursor: "c1" })),
      () => Promise.reject(new Error("changes -> 500")),
      () => Promise.resolve(changes([], { cursor: "c2" })),
    ],
    boardReads: [() => Promise.resolve(board([deal()]))],
  });
  await h.sync.refreshBoard({ reason: "load" });
  await h.sync.pollChanges();
  assert.equal(h.sync.status().state, SYNC_STATES.READY);

  const failed = await h.sync.pollChanges();
  assert.equal(failed.ok, false);
  assert.equal(h.sync.status().state, SYNC_STATES.RECONNECTING);
  assert.equal(h.sync.status().cursor, "c1");
  assert.match(h.sync.status().last_error, /500/);

  await h.sync.pollChanges();
  assert.equal(h.sync.status().state, SYNC_STATES.READY);
  assert.equal(h.sync.status().cursor, "c2");
});

// ------------------------------------------------------------- an empty board

test("an empty board still carries presence and capture, and the first record arrives by board read", async () => {
  const h = harness({
    boardReads: [
      () => Promise.resolve(board([])),
      () => Promise.resolve(board([deal({ id: "d9", name: "New Practice" })])),
    ],
    changeReads: [
      () => Promise.resolve(changes([], {
        presence: [{ deal_id: "d9", actor: "dell", field: "next_step", expires_at: "2099-01-01T00:00:00Z" }],
        capture_sessions: [{ id: "cap-1", state: "transcribing" }],
        cursor: "c1",
      })),
    ],
  });

  await h.sync.refreshBoard({ reason: "load" });
  assert.deepEqual(h.deals(), [], "an empty board is a real answer, not a failure");
  assert.equal(h.sync.status().snapshots, 1);

  const poll = await h.sync.pollChanges();
  assert.equal(poll.applied, true, "polling is not skipped because the board is empty");
  assert.equal(poll.changes.presence.length, 1, "presence still arrives");
  assert.equal(poll.changes.capture_sessions[0].state, "transcribing");

  // A record this session has never seen is exactly what a new deal looks like
  // to an empty board, so it is a reason to read the board again.
  assert.equal(batchTouchesBoard([changeEvent({ subject_id: "d9" })]), true);
  await h.sync.requestRefresh("change-feed");
  assert.deepEqual(h.deals().map((row) => row.name), ["New Practice"]);
});

test("what counts as board news", () => {
  assert.equal(batchTouchesBoard([]), false);
  assert.equal(batchTouchesBoard(null), false);
  assert.equal(batchTouchesBoard([null, "nonsense", {}]), false);
  assert.equal(batchTouchesBoard([{ subject_type: "client", subject_id: "c1" }]), false,
    "another subject's event is not this board's business");
  assert.equal(batchTouchesBoard([changeEvent({ field: null, verb: "start-deal-review" })]), true,
    "a deal event with no field can still change what the board shows");
  assert.equal(batchTouchesBoard([{ subject_id: "d1" }]), true, "subject_type may be absent");
  assert.equal(batchTouchesBoard([{ subject_type: "client", subject_id: "c1" }, changeEvent()]), true);
});

// ------------------------------------------------------------ the pure pieces

test("merging holds copies rather than mutates, and only holds newer than the read win", () => {
  const holds = createHolds();
  holds.note("d1", { phase: "Closing" }, 5);
  holds.note("d1", { owner: "dell" }, 2);
  holds.note("d2", { attention: true }, 9);
  assert.equal(holds.size, 3);

  const source = board([deal({ phase: "Research", owner: "joe" }), deal({ id: "d2", attention: false })]);
  const frozen = JSON.stringify(source);
  const merged = mergeBoardSnapshot(source, holds, 3);
  assert.equal(JSON.stringify(source), frozen, "the snapshot handed in is never edited");
  assert.equal(merged.deals[0].phase, "Closing", "confirmed after the read was issued: held");
  assert.equal(merged.deals[0].owner, "joe", "confirmed before it was issued: the board's value");
  assert.equal(merged.deals[1].attention, true);
  assert.deepEqual(merged.held_fields.sort(), ["d1|phase", "d2|attention"]);
  assert.equal(merged.actor, "joe", "everything else on the board passes through");

  holds.release(5);
  assert.deepEqual(holds.list().map((h) => `${h.deal_id}|${h.field}`), ["d2|attention"]);
  holds.release(9);
  assert.equal(holds.size, 0);
  assert.equal(holds.get("d1"), null);

  // Degenerate shapes stay degenerate rather than inventing rows.
  assert.deepEqual(mergeBoardSnapshot({}, holds, 1).deals, []);
  assert.deepEqual(mergeBoardSnapshot(null, null, 1).deals, []);
  holds.note("d1", null, 1);
  holds.note(null, { phase: "x" }, 1);
  assert.equal(holds.size, 0);
});

test("the coordinator refuses to exist without the reads it coordinates", () => {
  assert.throws(() => createBoardSync(), /readBoard/);
  assert.throws(() => createBoardSync({ readBoard: () => {} }), /readChanges/);
  assert.throws(() => createBoardSync({ readBoard: () => {}, readChanges: () => {} }), /applyBoard/);
});

test("the coordinator holds no transport, storage, DOM or timer of its own", async () => {
  const model = await file("dealroom/js/board-sync.mjs");
  const code = model
    .replace(/^\s*\/\*[\s\S]*?\*\//gm, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  assert.ok(code.includes("export function createBoardSync"), "the stripper kept the code");
  assert.ok(!code.includes("Pure coordinator"), "and removed the prose");
  for (const forbidden of [/\bfetch\s*\(/, /XMLHttpRequest/, /WebSocket/, /EventSource/, /\blocalStorage\b/,
    /\bsessionStorage\b/, /\bdocument\b/, /\bwindow\b/, /\bnavigator\b/, /setTimeout|setInterval/]) {
    assert.doesNotMatch(code, forbidden, `board-sync.mjs must not reach for ${forbidden}`);
  }
  assert.doesNotMatch(code, /https?:|\/api\/|\/pipeline\/|\/mcp\b/, "no endpoint literal is compiled in");

  // The boundary itself: drive the whole surface with every network entry point
  // replaced by a tripwire.
  const tripped = [];
  const originals = new Map();
  for (const name of ["fetch", "XMLHttpRequest", "WebSocket", "EventSource"]) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    try {
      Object.defineProperty(globalThis, name, {
        configurable: true, writable: true,
        value: function tripwire() { tripped.push(name); throw new Error(`the coordinator reached ${name}`); },
      });
    } catch { originals.delete(name); }
  }
  try {
    const h = harness({
      boardReads: [() => Promise.resolve(board([deal()]))],
      changeReads: [() => Promise.resolve(changes([changeEvent()]))],
    });
    h.sync.noteLocalWrite("d1", { attention: true });
    await h.sync.refreshBoard({ reason: "load" });
    await h.sync.pollChanges();
    h.sync.invalidate();
    h.sync.setOnline(false);
    h.sync.setOnline(true);
    h.sync.status();
    h.sync.holds();
    h.sync.stats();
    assert.deepEqual(tripped, [], "the coordinator made no network call of its own");
  } finally {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});

// ----------------------------------------------------------------- app wiring

test("app.js reads the board in exactly one place, through the coordinator", async () => {
  const app = await file("dealroom/js/app.js");
  assert.match(app, /from '\.\/board-sync\.mjs'/);
  assert.equal((app.match(/getBoard\(/g) || []).length, 1, "one board read path");
  assert.equal((app.match(/getChanges\(/g) || []).length, 1, "one changes read path");
  assert.match(app, /readBoard: \(\) => state\.client\.getBoard\(\{ workspace:'all' \}\)/,
    "the existing board verb, unchanged");
  assert.match(app, /readChanges: \(cursor\) => state\.client\.getChanges\(cursor\)/);
  assert.equal((app.match(/state\.deals = new Map/g) || []).length, 1,
    "one place builds the board, and only an applied snapshot reaches it");
  assert.match(app, /function applyBoardSnapshot\(home\)/);
  assert.match(app, /applyBoard: \(board\) => \{ applyBoardSnapshot\(board\); if \(!userIsEditing\(\)\) renderPreservingFocus\(\); \}/,
    "a snapshot does not rebuild the table under a partner's cursor");
  assert.doesNotMatch(app, /state\.cursor/, "the cursor belongs to the coordinator now");
});

test("app.js holds ids, not row objects, and does not drop the keyboard when a snapshot lands", async () => {
  const app = await file("dealroom/js/app.js");

  // Defect 1: the agenda keeps the set and order it captured, and resolves the
  // values at the moment it draws them.
  const renderAgenda = app.slice(app.indexOf("function renderAgenda"), app.indexOf("async function advanceAgenda"));
  assert.match(renderAgenda, /const captured = review\.deals\[review\.index\];\s*const deal = resolveCurrentRow\(captured, state\.deals\);/,
    "the agenda card reads the row the board holds now, not the copy it captured");
  assert.match(renderAgenda, /review\.deals\.length/, "the captured list still decides the progress count");
  const imported = app.slice(app.indexOf("import {\n  createBoardSync"), app.indexOf("} from './board-sync.mjs'"));
  assert.match(imported, /resolveCurrentRow/, "the identity rule is imported, not re-implemented here");
  assert.doesNotMatch(app, /function resolveCurrentRow/, "and it has one home");

  // Defect 2: the National Accounts grid is all buttons, which userIsEditing
  // does not see, and a snapshot now lands on a timer.
  const preserve = app.slice(app.indexOf("function renderPreservingFocus"), app.indexOf("function workspaceDeals"));
  assert.match(preserve, /grid\.contains\(active\)/, "only a focus inside the grid is claimed");
  assert.match(preserve, /closest\('\[data-account\]'\)\?\.dataset\.account/);
  assert.match(preserve, /\$\(`\[data-account="\$\{CSS\.escape\(focused\)\}"\]`\)\?\.focus\(\{ preventScroll: true \}\)/,
    "restored by id, and never scrolls the page to do it");
  assert.match(preserve, /if \(!focused\) return;/,
    "a render nobody was focused inside must not move focus at all");
  assert.equal((app.match(/renderPreservingFocus\(\)/g) || []).length, 2,
    "the definition and the one snapshot call site");
});

test("app.js holds a confirmed write and asks for a fresh read rather than awaiting one", async () => {
  const app = await file("dealroom/js/app.js");
  const confirmLocalWrite = app.slice(app.indexOf("function confirmLocalWrite"), app.indexOf("/**\n * One undo, one event"));
  // The row is looked up NOW: one captured before a snapshot arrived is no
  // longer the object being rendered, so writing to it would show nothing.
  assert.match(confirmLocalWrite, /const deal = state\.deals\.get\(dealId\) \|\| null;\s*if \(deal\) Object\.assign\(deal, patch\);/);
  assert.match(confirmLocalWrite, /state\.boardSync\.noteLocalWrite\(dealId, patch\);/);
  assert.match(confirmLocalWrite, /state\.boardSync\.requestRefresh\('after-write'\);/);
  assert.doesNotMatch(confirmLocalWrite, /await/, "acknowledgement never waits on a board read");

  const patchField = app.slice(app.indexOf("async function patchField"), app.indexOf("async function patchOperatingState"));
  assert.match(patchField, /if \(result\.status === 'conflict'\) return showConflict/,
    "the existing conflict path is untouched");
  assert.match(patchField, /const deal = confirmLocalWrite\(dealId, \{ \[field\]: value \}\);/);
  assert.match(patchField, /renderBoardOnly\(\);\s*showToast\(/, "the value is on screen before anything is re-read");

  // Every optimistic local edit goes through the one path, so none of them can
  // be reverted by a snapshot from a read that was already open.
  assert.equal((app.match(/noteLocalWrite\(/g) || []).length, 1, "one place holds a value");
  assert.equal((app.match(/confirmLocalWrite\(/g) || []).length, 5, "its definition and the four write sites");
  for (const [name, end] of [
    ["async function patchOperatingState", "function openForm"],
    ["function nextStepForm", "function parkDealForm"],
    ["function marketAgentForm", "function addTeamDealForm"],
  ]) {
    const section = app.slice(app.indexOf(name), app.indexOf(end));
    assert.match(section, /confirmLocalWrite\(dealId, \{/, `${name} must hold what it just put on screen`);
    assert.doesNotMatch(section, /await loadHome\(\)/, `${name} must not block on a whole reload`);
  }
});

// Placement only. What these writes DO under a lost answer, a double click, a
// refusal and a partner conflict is executed in field-write-reconciliation.test.mjs
// (the model) and dealroom.test.js (the model against the real verb).
test("app.js sends one cell change under one key, and never mints a second one per attempt", async () => {
  const app = await file("dealroom/js/app.js");
  assert.match(app, /from '\.\/field-write-reconciliation\.mjs'/);
  assert.match(app, /fieldWrites: createFieldWriteState\(\)/,
    "the retained request per cell sits beside fieldBase, as bookkeeping");
  assert.doesNotMatch(app, /function performFieldWrite|function beginFieldWrite/,
    "the reconciliation rule has one home, and app.js is not a second copy of it");

  const send = app.slice(app.indexOf("function sendCellWrite"), app.indexOf("function cellSubject"));
  assert.match(send, /const cell = cellKey\(dealId, field\);/);
  assert.match(send, /base: state\.fieldBase\.get\(cell\)\?\.id \|\| null/,
    "the base is still the cell's last seen event, and only for a NEW operation");
  assert.match(send, /newKey: uuidv4/);
  assert.match(send, /patch: \(request\) => state\.client\.patchDealField\(request\)/,
    "the verb the Deal Room already uses, with the request the model built");

  for (const [name, end] of [
    ["async function patchField", "async function patchOperatingState"],
    ["async function patchOperatingState", "function openForm"],
  ]) {
    const section = app.slice(app.indexOf(name), app.indexOf(end));
    assert.match(section, /await sendCellWrite\(/, `${name} goes through the one write path`);
    assert.doesNotMatch(section, /uuidv4\(\)/, `${name} must not mint a key per attempt`);
    assert.doesNotMatch(section, /idempotency_key/, `${name} must not build the request itself`);
    assert.match(section, /result\.status !== 'ok'/,
      `${name} must not treat an unanswered write as a saved one`);
  }

  // No optimistic value, no timer, no blind re-send: a retry is a person's act.
  const patchField = app.slice(app.indexOf("async function patchField"), app.indexOf("async function patchOperatingState"));
  assert.ok(patchField.indexOf("await sendCellWrite(") < patchField.indexOf("confirmLocalWrite("),
    "nothing is applied to the board before the server answers");
  assert.equal((app.match(/retryCellWrite\(/g) || []).length, 2,
    "the definition and the one control that calls it");
  assert.doesNotMatch(app, /set(Timeout|Interval)\([^)]*(retry|sendCellWrite|patchField)/i,
    "no timer re-sends a write nobody asked to re-send");

  // The unconfirmed write is on the row, not only in a toast that fades.
  const rowHtml = app.slice(app.indexOf("function rowHtml"), app.indexOf("function applyPresence"));
  assert.match(rowHtml, /unresolvedFieldWrites\(state\.fieldWrites, deal\.id\)/);
  assert.match(rowHtml, /data-retry-write="\$\{esc\(entry\.cell\)\}"/, "the control names the cell it re-sends");
  assert.match(rowHtml, /aria-label="[^"]*was not confirmed/, "and says so to a screen reader");
  assert.match(app, /const retryWrite = event\.target\.closest\('\[data-retry-write\]'\);\s*if \(retryWrite\) \{ await retryCellWrite\(retryWrite\.dataset\.retryWrite, retryWrite\); return; \}/);
  const retry = app.slice(app.indexOf("async function retryCellWrite"), app.indexOf("async function patchField"));
  assert.match(retry, /const \{ deal, field, value \} = entry\.request;/,
    "the retry is the retained request, not a fresh reading of the board");

  // A park that did not land keeps the dialog, the reason and the note.
  const park = app.slice(app.indexOf("function parkDealForm"), app.indexOf("function marketAgentForm"));
  assert.match(park, /\{ surface:'inline' \}/, "the reason is shown on the form, not over it in a toast");
  assert.match(park, /if \(result\.status !== 'ok'\) throw new Error\(result\.message/,
    "only a parked record closes the form");
});

// Placement for the two review corrections. The behaviour — an answer that
// arrives after the feed moved, for an ordinary cell and for operating state —
// is executed in field-write-reconciliation.test.mjs and, against the real verb,
// in dealroom.test.js.
test("app.js answers a modal inside the modal, and never paints a replayed answer over newer state", async () => {
  const app = await file("dealroom/js/app.js");

  // 1. A control pressed inside #dealDialog is answered inside #dealDialog: a
  // showModal() dialog is in the top layer, so the toast is behind its backdrop.
  const notice = app.slice(app.indexOf("function showDealDialogNotice"), app.indexOf("/**\n * Say what happened to a change"));
  assert.match(notice, /if \(!dialog\?\.open\) return false;/, "and degrades to the ordinary surface when none is open");
  assert.match(notice, /\$\('\.deal-content', dialog\)/, "placed in the dialog's own content, above the fold");
  assert.match(notice, /notice\.setAttribute\('role', 'alert'\)/);
  assert.match(notice, /data-retry-write="\$\{esc\(retryCell\)\}"/,
    "the same deliberate retry the row carries, reachable without dismissing the dialog");
  assert.match(notice, /\.focus\(\{ preventScroll: true \}\)/, "the keyboard lands on the answer");
  assert.match(notice, /esc\(message\)/, "and the sentence is escaped like every other rendered string");

  const surface = app.slice(app.indexOf("function writeSurfaceFor"), app.indexOf("/**\n * Put the answer where"));
  assert.match(surface, /dialog\?\.open && dialog\.contains\(trigger\) \? 'dialog' : 'toast'/,
    "the surface is decided by where the control actually is");
  assert.match(app, /await patchOperatingState\(dealId, \{ state:'active' \}, \{ surface:writeSurfaceFor\(operating\) \}\)/,
    "Restore from the deal dialog reports into the deal dialog");
  assert.match(app, /await retryCellWrite\(retryWrite\.dataset\.retryWrite, retryWrite\); return; \}/,
    "and a retry is answered on the surface it was pressed from");
  const retry = app.slice(app.indexOf("async function retryCellWrite"), app.indexOf("async function patchField"));
  assert.match(retry, /const options = \{ surface: writeSurfaceFor\(trigger\) \};/);

  // 2. An accepted answer the feed has moved past is reported, not applied.
  const send = app.slice(app.indexOf("function sendCellWrite"), app.indexOf("/** The cell, named"));
  assert.match(send, /baseNow: \(\) => state\.fieldBase\.get\(cell\)\?\.id \|\| null/,
    "the cell's base is read again when the answer lands, not remembered from the way out");
  // The other half of the same rule: an accepted answer names the event it
  // committed, and that becomes the base for the next write to this cell.
  assert.match(send, /if \(result\.status === 'ok' && !result\.superseded && result\.event_id\) \{\s*noteCellBase\(cell, \{ id: result\.event_id, recorded_at: result\.event_recorded_at \}\);/,
    "a replayed answer about an older operation can never reset the base");
  // And the third source of a base, which is what closes the cold first write:
  // the authoritative read itself, seeded through the same forward-only rule.
  const snapshot = app.slice(app.indexOf("function applyBoardSnapshot"), app.indexOf("async function pollOnce"));
  assert.match(snapshot, /for \(const \[field, seen\] of Object\.entries\(deal\.field_base \|\| \{\}\)\) \{\s*noteCellBase\(cellKey\(deal\.id, field\), seen\);/,
    "every editable cell's base comes from the same read as its value");
  assert.doesNotMatch(snapshot, /state\.fieldBase\.set\(/,
    "and it goes through the rule, not around it: a read already open when a write "
    + "was confirmed carries that cell's OLDER base");

  const note = app.slice(app.indexOf("function noteCellBase"), app.indexOf("/** The cell, named"));
  assert.match(note, /nextCellBase\(state\.fieldBase\.get\(cell\) \|\| null, seen\)/,
    "one ordering rule, imported, for both sources");
  assert.match(note, /const seen = \{ id: event\.id, recorded_at: event\.recorded_at \?\? null \};/,
    "and a base is an identity and a time, never a value out of an event");
  assert.doesNotMatch(app, /function nextCellBase/, "and it has one home");
  const reconcile = app.slice(app.indexOf("function reconcileNewerState"), app.indexOf("async function retryCellWrite"));
  assert.doesNotMatch(reconcile, /confirmLocalWrite|noteLocalWrite/,
    "the request's value is not written to the row and not held against the next snapshot");
  assert.match(reconcile, /state\.boardSync\.requestRefresh\('after-write'\)/,
    "the authoritative read decides what the cell holds");
  for (const [name, end] of [
    ["async function patchField", "async function patchOperatingState"],
    ["async function patchOperatingState", "function openForm"],
  ]) {
    const section = app.slice(app.indexOf(name), app.indexOf(end));
    assert.ok(section.indexOf("result.superseded") < section.indexOf("confirmLocalWrite("),
      `${name} must decide about newer state before it applies anything`);
    assert.match(section, /reconcileNewerState\(/);
  }
  assert.equal((app.match(/reconcileNewerState\(/g) || []).length, 3,
    "the definition and the two write paths");
  assert.equal((app.match(/noteLocalWrite\(/g) || []).length, 1,
    "still exactly one place that holds a value");
});

test("app.js keeps every unconfirmed change reachable, outside the filters that hide its row", async () => {
  const app = await file("dealroom/js/app.js");

  const node = app.slice(app.indexOf("function pendingWritesNode"), app.indexOf("function renderPendingWrites"));
  assert.match(node, /const existing = \$\('#pendingWrites'\);\s*if \(existing\) return existing;/,
    "built once, not on every render");
  assert.match(node, /main\.insertAdjacentElement\('afterbegin', node\)/,
    "above the workspace, so no filter, search or workspace switch can take it away");
  assert.match(node, /node\.setAttribute\('role', 'status'\)/,
    "a standing list, not an alert that re-announces on every poll");

  const bar = app.slice(app.indexOf("function renderPendingWrites"), app.indexOf("function renderBoardOnly"));
  assert.match(bar, /const pending = unresolvedFieldWrites\(state\.fieldWrites\);/,
    "one state, read whole — no second store of what is outstanding");
  assert.match(bar, /node\.hidden = pending\.length === 0;/, "and no bar when nothing is outstanding");
  assert.match(bar, /Unconfirmed changes · \$\{pending\.length\}/,
    "the count is the operations themselves, never a number kept alongside them");
  assert.match(bar, /if \(signature === state\.pendingSignature\) return;/,
    "rewritten only when the set changes, so the button under a finger survives a poll");
  assert.match(bar, /const deal = state\.deals\.get\(entry\.deal\);\s*const name = deal\?\.name \|\| 'a record this board is not showing right now';/,
    "a record the board does not hold is said to be that, not given a borrowed name");
  assert.match(bar, /data-retry-write="\$\{esc\(entry\.cell\)\}"/, "the same deliberate retry, same key");
  assert.match(bar, /data-open-deal="\$\{esc\(entry\.deal\)\}"/,
    "and a way to reach the record itself when its row is filtered out");
  assert.ok((bar.match(/esc\(/g) || []).length >= 5, "every rendered value is escaped");

  // It is drawn before the board's own visibility is considered, because the
  // accounts home hides the board section entirely.
  const board = app.slice(app.indexOf("function renderBoardOnly"), app.indexOf("function renderStats"));
  assert.ok(board.indexOf("renderPendingWrites();") < board.indexOf("$('#boardSection').hidden"),
    "an unconfirmed change belongs to the page, not to whichever workspace is showing");
  assert.equal((app.match(/renderPendingWrites\(\)/g) || []).length, 2,
    "the definition and the one call site every render already passes through");

  // And the sentence that sends a person to it names it.
  const helper = await file("dealroom/js/field-write-reconciliation.mjs");
  for (const reason of ["no_answer", "server_error", "unresolved"]) {
    const line = helper.slice(helper.indexOf(`${reason}: '`), helper.indexOf("',", helper.indexOf(`${reason}: '`)));
    assert.match(line, /Unconfirmed changes bar at the top of the page/, reason);
  }
});

test("app.js swaps one dialog's contents instead of opening it twice", async () => {
  const app = await file("dealroom/js/app.js");
  const openForm = app.slice(app.indexOf("function openForm"), app.indexOf("function nextStepForm"));
  // #formDialog is shared by every form, and the conflict chooser is raised FROM
  // the park form while that dialog is still open. showModal() on an open dialog
  // throws, and the exception landed on the error line the submit had just
  // cleared — the chooser appeared with a browser message above it.
  assert.match(openForm, /if \(!dialog\.open\) dialog\.showModal\(\);/);
  assert.equal((openForm.match(/dialog\.showModal\(\)/g) || []).length, 1,
    "one place opens it, and only when it is not already open");
  assert.ok(openForm.indexOf("$('#dialogBody').innerHTML = body;") < openForm.indexOf("dialog.showModal()"),
    "the body and the submit handler are replaced first, so the swap is complete before it is shown");
});

test("app.js is honest in the badge and refuses to show an empty board it never read", async () => {
  const app = await file("dealroom/js/app.js");
  const detail = app.slice(app.indexOf("function syncDetail("), app.indexOf("function setSync"));
  const setSync = app.slice(app.indexOf("function setSync"), app.indexOf("/**\n * Read the board authoritatively"));
  // Both paths are reported, separately, because they fail separately.
  assert.match(detail, /Board values are from the last successful read at \$\{at\}/,
    "the detail names the moment the values are from");
  assert.match(detail, /No board read has succeeded yet in this session\./);
  assert.match(detail, /status\.board_health === HEALTH\.RENDER_FAILED/,
    "the health vocabulary has one home, in the coordinator");
  assert.match(detail, /The change feed is not answering/);
  assert.doesNotMatch(detail, /=== 'ok'|=== 'failed'/, "no second copy of those strings in app.js");
  assert.doesNotMatch(`${detail}${setSync}`, /up to date|real.?time|instantly|guaranteed|complete/i,
    "no freshness or completeness promise a poll cannot keep");
  assert.match(setSync, /if \(el\.textContent !== label\) el\.textContent = label;/,
    "role=status is a live region: unchanged text is not re-announced");
  assert.match(setSync, /SYNC_STATES\.ERROR \? 'Board view error'/,
    "a snapshot that could not be shown is not reported as a connection problem");

  const loadHome = app.slice(app.indexOf("async function loadHome"), app.indexOf("function applyBoardSnapshot"));
  assert.match(loadHome, /if \(!outcome\.applied && state\.boardSync\.status\(\)\.snapshots === 0\)/);
  assert.match(loadHome, /throw outcome\.error/,
    "a first read that failed is not rendered as a board with no work on it");

  assert.match(app, /state\.boardSync\.setOnline\(false\)/);
  assert.match(app, /state\.boardSync\.refreshBoard\(\{ reason:'reconnect', force:true \}\)/,
    "reconnecting supersedes the request left hanging rather than queueing behind it");
  assert.match(app, /pollOnce\(false, \{ force:true \}\)/,
    "and supersedes a hung feed read too, or every later tick would skip as busy");
});

test("app.js reaches the freshness detail without a mouse, and outside the live region", async () => {
  const app = await file("dealroom/js/app.js");
  const node = app.slice(app.indexOf("function syncDetailNode"), app.indexOf("/**\n * What is true of each read path"));
  assert.match(node, /node\.className = 'sr-only';/, "the stylesheet's existing hidden-text class");
  assert.match(node, /badge\.insertAdjacentElement\('afterend', node\)/,
    "a SIBLING: a timestamp changing inside role=status would be read out on every poll");
  assert.match(node, /node\.setAttribute\('aria-live', 'off'\)/);
  assert.match(node, /badge\.setAttribute\('tabindex', '0'\)/, "reachable by keyboard");
  assert.match(node, /badge\.setAttribute\('aria-describedby', 'syncFreshness'\)/);
  assert.match(node, /const existing = \$\('#syncFreshness'\);\s*if \(existing\) return existing;/,
    "built once, not on every status publish");
  assert.match(app, /syncDetailNode\(\)\.textContent = detail;/);
  // The half this does NOT solve is named in the source rather than implied:
  // a sighted touch user still has no way to summon the detail.
  assert.match(app, /sighted touch user/i);
});

test("app.js asks again on a bounded interval, because the feed cannot promise completeness", async () => {
  const app = await file("dealroom/js/app.js");
  assert.match(app, /const BOARD_REFRESH_MS = 15000;/);
  assert.match(app, /state\.boardRefreshTimer = setInterval\(\(\) => \{\s*state\.boardSync\.requestRefresh\('periodic'\);\s*\}, BOARD_REFRESH_MS\);/,
    "a periodic authoritative read, coalesced through the same single-read queue");
  const constant = app.slice(app.indexOf("/**\n * How long the board will go without ASKING"), app.indexOf("const BOARD_REFRESH_MS"));
  assert.match(constant, /Not a freshness guarantee/);
  assert.match(constant, /commit landed out of order/,
    "the reason it exists: a cursor can miss an event, so news-only refresh can be stale forever");
  assert.doesNotMatch(constant, /guarantees that|always current|never stale/i);
  // The interval is a backstop, not the mechanism: a change still shows up on
  // the next poll's refresh request, which is far sooner.
  assert.match(app, /batchTouchesBoard\(batch\)\) state\.boardSync\.requestRefresh\('change-feed'\)/);
});

// --------------------------------------------------- one realistic sequence

test("a long history drains without touching values, and one burst of news costs one board read", async () => {
  // 450 events, 200 to a page: the shape deal-change-receipts.test.mjs drains.
  const page = (n, size, cursor) => changes(
    Array.from({ length: size }, (_, i) => changeEvent({
      id: `ev${n}-${i}`, recorded_at: "2025-02-01T00:00:00.000000+00:00",
      field: "phase", old_value: "On Deck", new_value: "On Deck",
    })),
    { cursor },
  );
  const h = harness({
    boardReads: [
      () => Promise.resolve(board([deal({ phase: "Closing" })])),
      () => Promise.resolve(board([deal({ phase: "Legal" })])),
    ],
    changeReads: [
      () => Promise.resolve(page(1, 200, "c200")),
      () => Promise.resolve(page(2, 200, "c400")),
      () => Promise.resolve(page(3, 50, "c450")),
      () => Promise.resolve(changes([
        changeEvent({ id: "news-1", new_value: "Legal" }),
        changeEvent({ id: "news-2", field: "owner", new_value: "dell" }),
      ], { cursor: "c452" })),
    ],
  });

  await h.sync.refreshBoard({ reason: "load" });
  for (let i = 0; i < 3; i += 1) await h.sync.pollChanges();
  assert.equal(h.sync.stats().board_reads, 1, "draining history reads the board no further times");
  assert.equal(h.dealById("d1").phase, "Closing", "and 450 old events changed nothing on it");
  assert.equal(h.sync.status().cursor, "c450");

  // app.js gates this call on the feed being caught up; here the news page is
  // the caller's own decision, exercised as app.js makes it.
  const news = await h.sync.pollChanges();
  assert.equal(batchTouchesBoard(news.changes.events), true);
  await h.sync.requestRefresh("change-feed");
  assert.equal(h.sync.stats().board_reads, 2, "two events, one read");
  assert.equal(h.dealById("d1").phase, "Legal");
  assert.equal(h.sync.status().state, SYNC_STATES.READY);
});
