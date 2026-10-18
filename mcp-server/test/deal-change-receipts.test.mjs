// Deal Room change receipts: the session-scoped list that keeps a change
// inspectable after its toast disappears.
//
// These tests exercise the real model, the real live client transport, and the
// real wiring in app.js/index.html/app.css. Source assertions are used only
// where a claim is about placement (single home, no new endpoint, no storage);
// every behavioural claim is executed.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  REVERTIBLE_FIELDS, RECEIPT_LIMIT, escapeText, parkingReasonLabel,
  readableValue, normalizeChangeEvent, ingestChangeEvents, compareReceipts,
  receiptInstant, receiptTimeLabel, receiptViews, receiptsSignature, receiptListHtml, receiptRowHtml,
  createUndoState, beginUndo, settleUndo, classifyUndoOutcome, performUndo,
  createFeedProgress, observeChangeBatch, receiptsAnnouncement,
} from "../../dealroom/js/change-receipts.mjs";
import { createLiveClient } from "../../dealroom/js/live-client.js";
import { PHASES } from "../../dealroom/js/client.js";
import { DEAL_ROOM_FIELDS } from "../src/dealroom.js";

const file = (relative) => readFile(new URL(`../../${relative}`, import.meta.url), "utf8");

const respond = (payload) => ({
  ok: true, status: 200, json: async () => payload, text: async () => JSON.stringify(payload),
});

/**
 * The changes feed as the server really serves it: the whole deal event log,
 * oldest first, one bounded page per poll, cursor carried between polls.
 * Nothing here is a new endpoint — it is the existing GET the board runs.
 */
function pagedFeed(events, limit = 200) {
  const log = [...events];
  const paths = [];
  const client = createLiveClient({
    selfActor: "joe",
    fetchImpl: async (path) => {
      paths.push(path);
      const at = Number(new URL(path, "https://deals.invalid").searchParams.get("cursor") || 0);
      const page = log.slice(at, at + limit);
      return respond({
        events: page.map((entry) => ({ ...entry })), presence: [], capture_sessions: [],
        cursor: String(at + page.length),
      });
    },
  });
  return { client, paths, append: (entry) => log.push(entry) };
}

const newSession = () => ({ cursor: null, feed: createFeedProgress(), receipts: [], views: [] });

// The receipt half of app.js's pollOnce, in the same order: read the batch the
// board already consumed, note how far the cursor has got, fold it in, and
// expose nothing until the cursor is current.
async function pollSession(client, session) {
  const result = await client.getChanges(session.cursor);
  session.cursor = result.cursor;
  const batch = result.events || [];
  session.feed = observeChangeBatch(session.feed, batch);
  session.receipts = ingestChangeEvents(session.receipts, batch, context);
  session.views = session.feed.caught_up
    ? receiptViews(session.receipts, { selfActor: "joe", undo: {} })
    : [];
  return session;
}

const DEALS = new Map([["d1", "Riverbank Dental"], ["d2", "Cooper Vet"]]);
const context = {
  dealName: (id) => DEALS.get(id) || null,
  actorLabel: (slug) => ({ joe: "Joe", dell: "Dell" }[slug] || slug),
};

let stamp = 0;
function event(overrides = {}) {
  stamp += 1;
  return {
    id: `e${String(stamp).padStart(3, "0")}`,
    recorded_at: `2026-09-10T14:${String(stamp % 60).padStart(2, "0")}:00.000000+00:00`,
    actor: "joe", verb: "patch-deal-field", subject_type: "deal", subject_id: "d1",
    field: "phase", old_value: "Research", new_value: "Negotiation",
    ...overrides,
  };
}

const viewFor = (receipts, eventId, options = {}) =>
  receiptViews(receipts, { selfActor: "joe", ...options }).find((v) => v.event_id === eventId);

// ---------------------------------------------------------------- model

test("receipts accumulate across polls, dedupe by event id, and stay bounded", () => {
  const first = ingestChangeEvents([], [
    event({ id: "a1", recorded_at: "2026-09-10T14:00:01.000000+00:00" }),
    event({ id: "a2", recorded_at: "2026-09-10T14:00:02.000000+00:00" }),
  ], context);
  assert.deepEqual(first.map((r) => r.event_id), ["a2", "a1"]);

  // A repeated batch (an unchanged cursor, a replayed poll) adds nothing, and
  // an out-of-order arrival still lands in its place.
  const second = ingestChangeEvents(first, [
    event({ id: "a2", recorded_at: "2026-09-10T14:00:02.000000+00:00" }),
    event({ id: "a1", recorded_at: "2026-09-10T14:00:01.000000+00:00" }),
    event({ id: "a0", recorded_at: "2026-09-10T13:59:00.000000+00:00" }),
    event({ id: "a3", recorded_at: "2026-09-10T14:00:03.000000+00:00" }),
  ], context);
  assert.deepEqual(second.map((r) => r.event_id), ["a3", "a2", "a1", "a0"]);

  const many = [];
  for (let i = 1; i <= 30; i += 1) {
    many.push(event({ id: `b${String(i).padStart(2, "0")}`, recorded_at: `2026-09-10T15:${String(i).padStart(2, "0")}:00.000000+00:00` }));
  }
  const bounded = ingestChangeEvents(second, many, context);
  assert.equal(bounded.length, RECEIPT_LIMIT);
  assert.equal(bounded.at(0).event_id, "b30", "newest first");
  assert.equal(bounded.at(-1).event_id, "b06", "the oldest fall off, nothing else is dropped");
  assert.equal(bounded.filter((r) => r.event_id === "b30").length, 1);
});

test("ordering uses the server's own (recorded_at, id) key, microseconds included", () => {
  const sameMillisecond = ingestChangeEvents([], [
    event({ id: "c1", recorded_at: "2026-09-10T14:00:00.123400+00:00" }),
    event({ id: "c2", recorded_at: "2026-09-10T14:00:00.123900+00:00" }),
  ], context);
  assert.deepEqual(sameMillisecond.map((r) => r.event_id), ["c2", "c1"],
    "Date.parse stops at milliseconds; the microseconds the server recorded still decide");

  const sameInstant = ingestChangeEvents([], [
    event({ id: "d1x", recorded_at: "2026-09-10T14:00:00.000000+00:00" }),
    event({ id: "d2x", recorded_at: "2026-09-10T14:00:00.000000+00:00" }),
  ], context);
  assert.deepEqual(sameInstant.map((r) => r.event_id), ["d2x", "d1x"],
    "ties break on event id, the server's `order by recorded_at, id` reversed");

  assert.equal(compareReceipts(
    { event_id: "z", recorded_at: "2026-09-10T14:00:02.000000+00:00" },
    { event_id: "a", recorded_at: "2026-09-10T14:00:01.000000+00:00" },
  ) < 0, true);
});

test("ordering compares instants, not timestamp text: offsets and fraction widths", () => {
  // Regression: the same instant written at -05:00 sorted by its raw text, so
  // a later change with a smaller printed hour was pushed below an earlier one.
  const later = { event_id: "a", recorded_at: "2026-09-10T09:00:00.123900-05:00" };
  const earlier = { event_id: "z", recorded_at: "2026-09-10T14:00:00.123400Z" };
  assert.equal(compareReceipts(later, earlier) < 0, true, "the later instant sorts first");
  assert.equal(compareReceipts(earlier, later) > 0, true, "and the comparator is symmetric");
  assert.deepEqual(
    ingestChangeEvents([], [
      event({ id: earlier.event_id, recorded_at: earlier.recorded_at }),
      event({ id: later.event_id, recorded_at: later.recorded_at }),
    ], context).map((r) => r.event_id),
    ["a", "z"],
  );

  // Equivalent offsets are one instant: only the event id may break the tie.
  for (const [left, right] of [
    ["2026-09-10T14:00:00.500000Z", "2026-09-10T09:00:00.500000-05:00"],
    ["2026-09-10T14:00:00.500000+00:00", "2026-09-10T16:30:00.500000+02:30"],
    ["2026-09-10T14:00:00.5Z", "2026-09-10T14:00:00.500Z"],
  ]) {
    assert.equal(compareReceipts({ event_id: "m", recorded_at: left }, { event_id: "m", recorded_at: right }), 0,
      `${left} and ${right} are the same instant`);
    assert.equal(compareReceipts({ event_id: "a", recorded_at: left }, { event_id: "b", recorded_at: right }) > 0, true,
      "a true tie falls to the event id");
  }

  // Fractional precision decides only after the instant, at any width.
  const widths = [
    ["2026-09-10T14:00:00.2Z", "2026-09-10T14:00:00.199999Z"],
    ["2026-09-10T14:00:00.123456Z", "2026-09-10T14:00:00.123455Z"],
    ["2026-09-10T14:00:00.123456789Z", "2026-09-10T14:00:00.123456788Z"],
    ["2026-09-10T14:00:00.000001Z", "2026-09-10T14:00:00Z"],
  ];
  for (const [newer, older] of widths) {
    assert.equal(compareReceipts({ event_id: "z", recorded_at: newer }, { event_id: "a", recorded_at: older }) < 0, true,
      `${newer} is newer than ${older} and must not lose to an event id`);
  }

  assert.deepEqual(receiptInstant("2026-09-10T14:00:00.123900+00:00"),
    receiptInstant("2026-09-10T09:00:00.123900-05:00"));
  assert.equal(receiptInstant("2026-09-10T14:00:00.123456+00").ms, Date.parse("2026-09-10T14:00:00.123Z"),
    "a two-digit offset still resolves");
  assert.equal(receiptInstant("2026-09-10 14:00:00.123456+00:00").sub, 456000,
    "a space-separated stamp keeps its microseconds");
  assert.equal(receiptInstant(null), null);
  assert.equal(receiptInstant("whenever"), null);
  assert.equal(compareReceipts({ event_id: "a", recorded_at: "2026-09-10T14:00:00Z" }, { event_id: "b", recorded_at: "whenever" }) < 0, true,
    "a stamp that cannot be placed sorts last rather than being guessed at");
});

// ------------------------------------------------------- catching up

test("the cursor must reach the present before anything is shown, and no clock decides that", () => {
  let feed = createFeedProgress();
  assert.equal(feed.caught_up, false, "a session starts behind: the cursor begins at the oldest event");

  feed = observeChangeBatch(feed, new Array(200).fill({}));
  assert.equal(feed.page_size, 200, "the page size is learned from the feed, never assumed");
  assert.equal(feed.caught_up, false, "a full page cannot be the last one");
  feed = observeChangeBatch(feed, new Array(200).fill({}));
  assert.equal(feed.caught_up, false);
  feed = observeChangeBatch(feed, new Array(43).fill({}));
  assert.equal(feed.caught_up, true, "the short page is the one that reached the present");
  feed = observeChangeBatch(feed, new Array(200).fill({}));
  assert.equal(feed.caught_up, true, "and it stays reached; later pages are increments");

  // A log that fits in one page looks exactly like a full one, so it opens on
  // the following poll instead of the first. An empty page is unambiguous.
  let small = observeChangeBatch(createFeedProgress(), new Array(12).fill({}));
  assert.equal(small.caught_up, false);
  small = observeChangeBatch(small, []);
  assert.equal(small.caught_up, true);
  assert.equal(observeChangeBatch(createFeedProgress(), []).caught_up, true);
  assert.equal(observeChangeBatch(undefined, undefined).caught_up, true);

  // Timestamps have no authority here: reaching the end is a property of the
  // page, and a browser's idea of "now" is not evidence about the server.
  const ancient = observeChangeBatch(observeChangeBatch(createFeedProgress(), new Array(200).fill({})),
    [{ recorded_at: "1999-01-01T00:00:00Z" }]);
  assert.equal(ancient.caught_up, true);
  assert.equal(observeChangeBatch(createFeedProgress(), new Array(200).fill({ recorded_at: "2099-01-01T00:00:00Z" })).caught_up, false);
});

test("a log longer than one page never surfaces as recent, and never offers Undo mid-drain", async () => {
  const base = Date.parse("2026-08-01T00:00:00Z");
  const history = [];
  for (let i = 1; i <= 450; i += 1) {
    history.push({
      id: `ev${String(i).padStart(4, "0")}`,
      recorded_at: new Date(base + i * 1000).toISOString(),
      actor: i % 2 ? "joe" : "dell", verb: "patch-deal-field",
      subject_type: "deal", subject_id: "d1", field: "phase",
      old_value: { phase: "research" }, new_value: { phase: "negotiation" },
    });
  }
  const feed = pagedFeed(history);
  const session = newSession();

  await pollSession(feed.client, session);
  assert.equal(session.feed.caught_up, false, "page one of a 450-event log is the OLDEST 200");
  assert.equal(session.views.length, 0, "nothing from the beginning of the log is titled Recent changes");
  assert.equal(receiptListHtml(session.views), "");
  assert.ok(session.receipts.length > 0, "the drain still accumulates; it is exposure that waits");

  // A real change lands while the session is still catching up.
  feed.append({
    id: "live-1", recorded_at: new Date(base + 9e5).toISOString(), actor: "joe",
    verb: "patch-deal-field", subject_type: "deal", subject_id: "d1", field: "attention",
    old_value: { attention: false }, new_value: { attention: true },
  });

  await pollSession(feed.client, session);
  assert.equal(session.feed.caught_up, false, "still draining on page two");
  assert.equal(session.views.length, 0);
  assert.doesNotMatch(receiptListHtml(session.views), /data-undo=/,
    "no Undo control can exist for a change that only looks latest because the rest has not arrived");

  await pollSession(feed.client, session);
  assert.equal(session.feed.caught_up, true, "page three came back short");
  assert.equal(session.views.length, 25, "and now the newest 25 the session has seen are shown");
  assert.equal(session.views[0].event_id, "live-1", "including the one that arrived mid-drain");
  assert.equal(session.views[0].undo_status, "available", "which is genuinely the latest for its deal+field");
  assert.equal(session.views.some((view) => view.event_id === "ev0001"), false,
    "the oldest events are gone, not merely outranked");
  assert.equal(session.views.at(-1).event_id, "ev0427");

  assert.equal(feed.paths.length, 3, "three polls, no extra polling to catch up");
  assert.equal(feed.paths.every((path) => path.startsWith("/pipeline/changes")), true, "and no second transport");
});

test("the latest change to a deal+field decides Undo, including a partner's", () => {
  const mine = event({ id: "u1", field: "phase", recorded_at: "2026-09-10T16:00:01.000000+00:00" });
  const mineAgain = event({ id: "u2", field: "phase", recorded_at: "2026-09-10T16:00:02.000000+00:00" });
  const otherField = event({ id: "u3", field: "owner", old_value: null, new_value: "dell", recorded_at: "2026-09-10T16:00:03.000000+00:00" });
  let receipts = ingestChangeEvents([], [mine, mineAgain, otherField], context);

  assert.equal(viewFor(receipts, "u2").undo_status, "available");
  assert.equal(viewFor(receipts, "u2").can_undo, true);
  assert.equal(viewFor(receipts, "u1").undo_status, "superseded");
  assert.equal(viewFor(receipts, "u1").can_undo, false);
  assert.equal(viewFor(receipts, "u1").superseded, true);
  assert.equal(viewFor(receipts, "u3").undo_status, "available", "a different field is untouched");

  // Dell moves the same field afterwards: my newest phase change is now stale
  // for undo purposes, exactly as revert-deal-field would find it.
  const partner = event({ id: "u4", actor: "dell", field: "phase", recorded_at: "2026-09-10T16:00:04.000000+00:00" });
  receipts = ingestChangeEvents(receipts, [partner], context);
  assert.equal(viewFor(receipts, "u2").undo_status, "superseded");
  assert.equal(viewFor(receipts, "u4").undo_status, "partner", "a partner's change is readable, not actionable");
  assert.equal(viewFor(receipts, "u4").can_undo, false);
  assert.equal(viewFor(receipts, "u4").own, false);
  assert.equal(viewFor(receipts, "u3").undo_status, "available");
});

test("a field the server will not revert carries no Undo, and a note is summarised rather than copied", () => {
  const secret = "Landlord confided <b>the seller is divorcing</b> — do not repeat";
  const receipts = ingestChangeEvents([], [
    event({ id: "n1", verb: "set-next-step", field: "next_step", old_value: "Call broker", new_value: "Send LOI" }),
    event({ id: "n2", verb: "add-deal-note", field: "note", old_value: null, new_value: secret }),
  ], context);

  const step = viewFor(receipts, "n1");
  assert.equal(step.undo_status, "unsupported");
  assert.equal(step.can_undo, false);
  assert.equal(step.before, "Call broker");
  assert.equal(step.after, "Send LOI");

  const note = viewFor(receipts, "n2");
  assert.equal(note.action, "Note added");
  assert.equal(note.before, null);
  assert.equal(note.after, null);
  assert.equal(note.can_undo, false);
  const html = receiptListHtml([note]);
  assert.ok(!html.includes("divorcing"), "the note body stays in the deal thread, not duplicated here");
  assert.match(html, /data-open-deal="d1"/, "the receipt links to the record that holds the note");
  assert.deepEqual(REVERTIBLE_FIELDS.includes("note"), false);
});

test("nothing is invented: no field means no value pair, and an unnamed record is left out", () => {
  const receipts = ingestChangeEvents([], [
    event({ id: "x1", verb: "start-deal-review", field: null, old_value: null, new_value: null }),
    event({ id: "x2", subject_id: "d99" }),
    event({ id: "x3", subject_type: "client", subject_id: "d1" }),
    { id: "x4" },
    null,
  ], context);
  assert.deepEqual(receipts.map((r) => r.event_id), ["x1"]);
  const only = receipts[0];
  assert.equal(only.before, null);
  assert.equal(only.after, null);
  assert.equal(only.action, "Start deal review", "the verb is read back, not interpreted");
  assert.equal(only.kind, "activity");
  assert.equal(Object.keys(only).some((key) => /score|confidence|rank/i.test(key)), false);

  // A field event whose value keys never arrived shows no pair either.
  const partial = normalizeChangeEvent({ id: "x5", subject_id: "d1", subject_type: "deal", field: "phase", actor: "joe", recorded_at: "2026-09-10T14:00:00Z", verb: "patch-deal-field" }, context);
  assert.equal(partial.before, null);
  assert.equal(partial.after, null);
});

test("values read the way the board reads them, and an empty side says so", () => {
  assert.equal(readableValue("attention", true), "Flagged");
  assert.equal(readableValue("attention", false), "Not flagged");
  assert.equal(readableValue("owner", "dell", context), "Dell");
  assert.equal(readableValue("owner", null), "(empty)");
  assert.equal(readableValue("next_date", "2026-10-01"), "2026-10-01");
  assert.equal(readableValue("phase", "due_diligence"), "Due diligence", "slug shape is humanized, never remapped");
  assert.equal(readableValue("phase", "Negotiation"), "Negotiation");
  assert.equal(readableValue("operating_state", { state: "parked", reason: "client_paused" }),
    `Parked — ${parkingReasonLabel("client_paused")}`);
  assert.equal(readableValue("operating_state", { state: "active" }), "Active work");
  assert.equal(readableValue("next_step", "x".repeat(400)).length, 140);
});

test("time reads as a clock, so a row that is not re-rendered never goes stale", () => {
  const now = Date.parse("2026-09-10T20:00:00Z");
  assert.match(receiptTimeLabel("2026-09-10T14:05:00Z", now), /^\d{2}:\d{2}$/);
  assert.match(receiptTimeLabel("2026-09-02T14:05:00Z", now), /^[A-Z][a-z]{2} \d{1,2} · \d{2}:\d{2}$/);
  assert.equal(receiptTimeLabel(null, now), "time not recorded");
  assert.equal(receiptTimeLabel("not a time", now), "time not recorded");
});

// ---------------------------------------------------------------- undo

test("one in-flight undo per event: a double click sends exactly one write", async () => {
  let undo = createUndoState();
  let sent = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const call = {
    eventId: "u2",
    getState: () => undo,
    setState: (next) => { undo = next; },
    newKey: () => "key-1",
    revert: async (request) => { sent += 1; await gate; return { ok: true, reverted_event_id: request.event_id }; },
  };

  const first = performUndo(call);
  const second = performUndo(call);
  assert.equal(undo.u2.status, "pending", "the claim is published before the request is awaited");
  release();
  const [a, b] = await Promise.all([first, second]);

  assert.equal(sent, 1, "the second click issued no write");
  assert.equal(a.started, true);
  assert.equal(b.started, false);
  assert.equal(b.reason, "pending");
  assert.equal(undo.u2.status, "succeeded");

  // A third click after the server already said yes is also refused locally.
  const third = await performUndo(call);
  assert.equal(third.started, false);
  assert.equal(sent, 1);
});

test("a logical undo keeps one idempotency key across a deliberate retry, and is never retried on its own", async () => {
  let undo = createUndoState();
  let minted = 0;
  const keysSent = [];
  const base = {
    eventId: "u2",
    getState: () => undo,
    setState: (next) => { undo = next; },
    newKey: () => { minted += 1; return `key-${minted}`; },
  };

  const lost = await performUndo({ ...base, revert: async (request) => { keysSent.push(request.idempotency_key); throw new Error("connection lost"); } });
  assert.equal(lost.outcome.status, "unknown");
  assert.equal(undo.u2.status, "unknown");
  assert.match(undo.u2.message, /could not be confirmed/i);
  assert.equal(keysSent.length, 1, "an unknown answer is not retried automatically");

  const retry = await performUndo({ ...base, revert: async (request) => { keysSent.push(request.idempotency_key); return { ok: true }; } });
  assert.equal(retry.started, true);
  assert.deepEqual(keysSent, ["key-1", "key-1"], "the same logical action carries the same key");
  assert.equal(minted, 1);
  assert.equal(undo.u2.status, "succeeded");
});

test("a refusal stays visible and terminal; only the server's yes marks a row undone", async () => {
  assert.equal(classifyUndoOutcome({ response: { ok: true } }).status, "succeeded");
  assert.equal(classifyUndoOutcome({ response: { ok: false, error: "newer_change_exists" } }).status, "refused");
  assert.equal(classifyUndoOutcome({ response: null }).status, "unknown");
  assert.equal(classifyUndoOutcome({ response: {} }).status, "unknown", "a shapeless answer is not a success");
  assert.equal(classifyUndoOutcome({ response: { deal_id: "d1" } }).status, "unknown");
  assert.equal(classifyUndoOutcome({ response: { ok: "true" } }).status, "unknown");
  assert.equal(classifyUndoOutcome({ error: new Error("502") }).status, "unknown");

  const refusal = Object.assign(new Error("live revert-deal-field refused: newer_change_exists"), {
    payload: { error: "newer_change_exists", hint: "Open the deal and review the newer value before changing it." },
  });
  const classified = classifyUndoOutcome({ error: refusal });
  assert.equal(classified.status, "refused");
  assert.equal(classified.code, "newer_change_exists");
  assert.equal(classified.message, "Open the deal and review the newer value before changing it.",
    "the server's own wording is shown, not reworded away");

  let undo = createUndoState();
  const call = {
    eventId: "u2",
    getState: () => undo,
    setState: (next) => { undo = next; },
    newKey: () => "key-1",
    revert: async () => { throw refusal; },
  };
  await performUndo(call);
  assert.equal(undo.u2.status, "refused");

  const receipts = ingestChangeEvents([], [event({ id: "u2", field: "phase" })], context);
  const view = viewFor(receipts, "u2", { undo });
  assert.equal(view.undo_status, "refused");
  assert.equal(view.can_undo, false);
  assert.equal(view.badge, "Refused");
  assert.match(view.message, /review the newer value/);
  assert.match(receiptListHtml([view]), /receipt-message refused/);

  const again = await performUndo(call);
  assert.equal(again.started, false, "a refusal is an answer, not a prompt to try again");
});

test("the server's own exception is an unknown, not a refusal, and stays retryable on one key", async () => {
  // mcp-server/src/mcp.js returns an unhandled throw on the same isError
  // channel as a real refusal — with a payload whose own hint says it is not
  // one. Reading "there is a code" as "the server declined" badged the row
  // Refused, printed server diagnostics at a partner, and closed it forever.
  const serverFault = { error: "unhandled_verb_failure", verb: "revert-deal-field",
    cause: "TypeError: cannot read properties of undefined\n    at applyDealRoomField",
    hint: "this is the server's own exception, not a refusal of your arguments — the verb reached code that threw. Read it before retrying: an unhandled failure repeated with the same arguments fails the same way. If it names a database fault, the handler's SQL is the place to look." };

  const outcome = classifyUndoOutcome({ error: Object.assign(new Error("live revert-deal-field refused: unhandled_verb_failure"), { payload: serverFault }) });
  assert.equal(outcome.status, "unknown");
  assert.equal(outcome.code, "unhandled_verb_failure");
  assert.doesNotMatch(outcome.message, /exception|TypeError|SQL|handler|verb|arguments/i,
    "a note the server wrote for whoever maintains it is not shown to a partner");
  assert.ok(outcome.message.length <= 160, "and it stays one short line");
  assert.match(outcome.message, /could not be confirmed/i);
  assert.match(outcome.message, /check before trying again/i);

  // Through the real client, on the real channel.
  let posts = 0;
  const client = createLiveClient({
    selfActor: "joe",
    fetchImpl: async () => {
      posts += 1;
      return {
        ok: true, status: 200, text: async () => JSON.stringify(serverFault),
        json: async () => ({ jsonrpc: "2.0", id: 1, result: { isError: true, content: [{ type: "text", text: JSON.stringify(serverFault) }] } }),
      };
    },
  });
  let undo = createUndoState();
  let minted = 0;
  const keys = [];
  const base = {
    eventId: "u9",
    getState: () => undo,
    setState: (next) => { undo = next; },
    newKey: () => { minted += 1; return `key-${minted}`; },
  };
  const failed = await performUndo({ ...base, revert: async (request) => { keys.push(request.idempotency_key); return client.revertDealField(request); } });
  assert.equal(failed.outcome.status, "unknown");
  assert.equal(undo.u9.status, "unknown");
  assert.equal(posts, 1, "an unknown is never retried on the partner's behalf");

  const receipts = ingestChangeEvents([], [event({ id: "u9", field: "phase" })], context);
  const view = viewFor(receipts, "u9", { undo });
  assert.equal(view.undo_status, "unconfirmed");
  assert.equal(view.can_undo, true, "the row stays actionable, unlike a genuine refusal");
  assert.notEqual(view.badge, "Refused");
  assert.match(receiptRowHtml(view), /data-undo="u9">Try undo again</);

  // A deliberate second attempt reuses the one key, so the server's envelope
  // replays rather than writing twice.
  const retried = await performUndo({ ...base, revert: async (request) => { keys.push(request.idempotency_key); return { ok: true, replayed: true }; } });
  assert.equal(retried.outcome.status, "succeeded");
  assert.equal(minted, 1);
  assert.equal(keys[0], keys[1]);
});

test("a decline shows the server's own words; our wording only fills a silence", () => {
  // Precedence, both directions. The hint is the only text that knows why THIS
  // request was declined, so it is never replaced by a local phrase.
  const withHint = classifyUndoOutcome({
    error: Object.assign(new Error("refused"), {
      payload: { error: "newer_change_exists", hint: "Open the deal and review the newer value before changing it." },
    }),
  });
  assert.equal(withHint.message, "Open the deal and review the newer value before changing it.");

  const novelCode = classifyUndoOutcome({
    error: Object.assign(new Error("refused"), {
      payload: { error: "deal_locked_by_review", hint: "This deal is inside an open review session." },
    }),
  });
  assert.equal(novelCode.status, "refused");
  assert.equal(novelCode.message, "This deal is inside an open review session.",
    "a code this surface has never seen still speaks for itself");

  // Only when the server says nothing does the fallback table speak.
  const bare = classifyUndoOutcome({ error: Object.assign(new Error("refused"), { payload: { error: "newer_change_exists" } }) });
  assert.match(bare.message, /newer change to this field came first/i);
  const unknownBare = classifyUndoOutcome({ error: Object.assign(new Error("refused"), { payload: { error: "some_new_gate" } }) });
  assert.equal(unknownBare.message, "The server refused this undo: Some new gate.");

  // A hint on a server FAULT is still withheld: that one is not about this deal.
  const fault = classifyUndoOutcome({
    error: Object.assign(new Error("boom"), {
      payload: { error: "unhandled_verb_failure", hint: "this is the server's own exception, not a refusal of your arguments" },
    }),
  });
  assert.equal(fault.status, "unknown");
  assert.doesNotMatch(fault.message, /exception|arguments/i);

  // The same precedence on the non-throwing shape.
  assert.equal(classifyUndoOutcome({ response: { ok: false, error: "newer_change_exists", hint: "Server said so." } }).message, "Server said so.");
});

test("key_reuse stays a terminal safety refusal", async () => {
  const refusal = Object.assign(new Error("live revert-deal-field refused: key_reuse"), {
    payload: { error: "key_reuse" },
  });
  const outcome = classifyUndoOutcome({ error: refusal });
  assert.equal(outcome.status, "refused", "a reused key is the server stopping a second write, not a fault");
  assert.match(outcome.message, /safety key/i);
  assert.doesNotMatch(outcome.message, /Key reuse/, "not a slug read back at a partner");

  let undo = createUndoState();
  let attempts = 0;
  const call = {
    eventId: "u9",
    getState: () => undo,
    setState: (next) => { undo = next; },
    newKey: () => "key-1",
    revert: async () => { attempts += 1; throw refusal; },
  };
  await performUndo(call);
  assert.equal(undo.u9.status, "refused");
  assert.equal((await performUndo(call)).started, false, "terminal: the safety stop is not re-sent");
  assert.equal(attempts, 1);

  const receipts = ingestChangeEvents([], [event({ id: "u9", field: "phase" })], context);
  assert.equal(viewFor(receipts, "u9", { undo }).can_undo, false);
});

test("undo status never claims success it did not get", () => {
  const receipts = ingestChangeEvents([], [event({ id: "s1", field: "phase" })], context);
  const cases = {
    pending: ["pending", "Undoing…", false],
    unknown: ["unconfirmed", "Not confirmed", true],
    succeeded: ["undone", "Undone", false],
  };
  for (const [status, [expected, badge, canUndo]] of Object.entries(cases)) {
    const view = viewFor(receipts, "s1", { undo: { s1: { status, idempotency_key: "k", message: null } } });
    assert.equal(view.undo_status, expected);
    assert.equal(view.badge, badge);
    assert.equal(view.can_undo, canUndo);
  }
  assert.equal(settleUndo(createUndoState(), "never-claimed", { status: "succeeded" })["never-claimed"], undefined,
    "a status cannot appear for an event no click ever claimed");
  const claimed = beginUndo(createUndoState(), "s1", () => "k1");
  assert.equal(claimed.request.idempotency_key, "k1");
  assert.equal(claimed.request.event_id, "s1");
});

test("the list re-renders only when something a reader would notice changed", () => {
  const receipts = ingestChangeEvents([], [event({ id: "g1", field: "phase" })], context);
  const quiet = receiptsSignature(receiptViews(receipts, { selfActor: "joe", now: Date.parse("2026-09-10T18:00:00Z") }));
  const later = receiptsSignature(receiptViews(receipts, { selfActor: "joe", now: Date.parse("2026-09-10T19:30:00Z") }));
  assert.equal(quiet, later, "clock drift alone must not re-announce the log");
  const changed = receiptsSignature(receiptViews(receipts, { selfActor: "joe", undo: { g1: { status: "succeeded" } } }));
  assert.notEqual(quiet, changed);
});

// ---------------------------------------------------------------- rendering

test("every rendered value is escaped, including hostile deal names and values", () => {
  const hostile = new Map([["d1", '<img src=x onerror="alert(1)">']]);
  const receipts = ingestChangeEvents([], [
    event({
      id: '"><script>alert(1)</script>', field: "next_step",
      old_value: "</p><svg onload=alert(2)>", new_value: "'\"><iframe src=javascript:alert(3)>",
      actor: "<b>joe</b>",
    }),
  ], { dealName: (id) => hostile.get(id) || null, actorLabel: (slug) => slug });
  const html = receiptListHtml(receiptViews(receipts, { selfActor: "joe" }));

  assert.equal(html.includes("<script"), false);
  assert.equal(html.includes("<img"), false);
  assert.equal(html.includes("<svg"), false);
  assert.equal(html.includes("<iframe"), false);
  assert.doesNotMatch(html, /<[a-z]+[^>]*\son[a-z]+=/i, "no element carries an event-handler attribute");
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/,
    "the hostile name is shown as text, closing quote and bracket neutralised");
  assert.match(html, /data-receipt="&quot;&gt;&lt;script&gt;/);
  assert.equal(escapeText(`<>&"'`), "&lt;&gt;&amp;&quot;&#39;");
  assert.equal(escapeText(null), "");
});

test("a rendered row carries the deal, the actor, the time and the field", () => {
  const receipts = ingestChangeEvents([], [event({ id: "r1", actor: "dell", field: "phase" })], context);
  const html = receiptListHtml(receiptViews(receipts, { selfActor: "joe", now: Date.parse("2026-09-10T20:00:00Z") }));
  assert.match(html, /Riverbank Dental/);
  assert.match(html, /Dell/);
  assert.match(html, /<time class="receipt-time" datetime="2026-09-10T14:/);
  assert.match(html, /receipt-action">Phase</);
  assert.match(html, /receipt-before">Research</);
  assert.match(html, /receipt-after">Negotiation</);
  assert.match(html, /class="sr-only">changed to</, "the arrow is not the only statement of direction");
  assert.equal(/receipt-undo/.test(html), false, "no Undo on a partner's change");
});

test("every row stays a focus target, because Undo disappears the moment it is used", () => {
  const receipts = ingestChangeEvents([], [event({ id: "f1", field: "phase" })], context);
  const rowFor = (undo) => receiptRowHtml(viewFor(receipts, "f1", { undo }));

  const offered = rowFor({});
  assert.match(offered, /data-undo="f1"/);
  assert.match(offered, /data-receipt="f1" tabindex="-1"/);

  // Pending disables the control, success and refusal remove it outright. In
  // all three the row itself remains, so the keyboard has somewhere to land.
  for (const status of ["pending", "succeeded", "refused"]) {
    const row = rowFor({ f1: { status, idempotency_key: "k", message: null } });
    assert.doesNotMatch(row, /data-undo=/, `${status} must not leave a live Undo control`);
    assert.match(row, /data-receipt="f1" tabindex="-1"/, `${status} must keep the row focusable`);
  }
  assert.match(rowFor({ f1: { status: "pending", idempotency_key: "k" } }), /<button[^>]*disabled>Undoing/);
  assert.match(rowFor({ f1: { status: "unknown", idempotency_key: "k" } }), /data-undo="f1">Try undo again</,
    "an unconfirmed attempt keeps a control, so focus returns to it");
});

test("only what arrived is announced — one change never reads back the whole list", () => {
  const first = ingestChangeEvents([], [event({ id: "a1", field: "phase" })], context);
  const firstViews = receiptViews(first, { selfActor: "joe" });
  assert.equal(receiptsAnnouncement(null, firstViews), "",
    "the list opening is not news; none of it just happened");

  const seen = firstViews.map((view) => view.event_id);
  const second = ingestChangeEvents(first, [event({
    id: "a2", field: "owner", old_value: null, new_value: "dell",
    recorded_at: "2026-09-10T18:00:00.000000+00:00",
  })], context);
  const secondViews = receiptViews(second, { selfActor: "joe" });
  assert.equal(receiptsAnnouncement(seen, secondViews), "1 new change · Riverbank Dental · Owner");

  // An Undo changes a row's state without adding one: nothing is announced,
  // because the outcome is on the row the keyboard was just moved to.
  const afterUndo = receiptViews(second, { selfActor: "joe", undo: { a2: { status: "succeeded" } } });
  assert.equal(receiptsAnnouncement(secondViews.map((v) => v.event_id), afterUndo), "");
  assert.notEqual(receiptsSignature(secondViews), receiptsSignature(afterUndo),
    "the row is still re-rendered — it is the announcement that stays silent");

  const many = ingestChangeEvents(second, [
    event({ id: "a3", recorded_at: "2026-09-10T18:01:00.000000+00:00" }),
    event({ id: "a4", recorded_at: "2026-09-10T18:02:00.000000+00:00" }),
  ], context);
  assert.equal(receiptsAnnouncement(seen, receiptViews(many, { selfActor: "joe" })), "3 new changes");
  assert.equal(receiptsAnnouncement([], []), "");
});

// ---------------------------------------------------------------- wiring

test("client Undo eligibility mirrors the server's revertible field list", async () => {
  const tools = await file("mcp-server/src/tools.js");
  assert.deepEqual([...REVERTIBLE_FIELDS].sort(), [...DEAL_ROOM_FIELDS].sort(),
    "an extra client field would offer an Undo the server refuses");
  assert.match(tools, /if \(latest\?\.id !== row\.id\)\s+throw new ToolError\(\{ error: "newer_change_exists"/,
    "the latest-event gate is the server's, not this panel's");
});

test("receipts and Undo reuse the Deal Room's existing transport and nothing else", async () => {
  const requests = [];
  const client = createLiveClient({
    selfActor: "joe",
    fetchImpl: async (path, init = {}) => {
      requests.push({ path, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null });
      if (path.startsWith("/pipeline/changes")) {
        return respond({
          // The wire shape: values wrapped as {field: value}, phase as a slug.
          events: [{
            id: "w1", recorded_at: "2026-09-10T14:00:00.123456+00:00", actor: "joe",
            verb: "patch-deal-field", subject_type: "deal", subject_id: "d1", field: "phase",
            old_value: { phase: "research" }, new_value: { phase: "negotiation" },
          }],
          presence: [], capture_sessions: [], cursor: "cursor-1",
        });
      }
      return respond({
        jsonrpc: "2.0", id: 1,
        result: { content: [{ type: "text", text: JSON.stringify({ ok: true, deal_id: "d1", field: "phase", reverted_event_id: "w1" }) }] },
      });
    },
  });

  const changes = await client.getChanges(null);
  const receipts = ingestChangeEvents([], changes.events, context);
  const view = viewFor(receipts, "w1");
  assert.equal(view.before, "Research", "the live client unwraps {field: value}; the receipt reads it, it does not re-parse the wire");
  assert.equal(view.after, "Negotiation", "the live client's phase mapping is the one on screen");
  assert.equal(view.can_undo, true);

  let undo = createUndoState();
  const result = await performUndo({
    eventId: "w1",
    getState: () => undo,
    setState: (next) => { undo = next; },
    newKey: () => "idem-1",
    revert: (request) => client.revertDealField(request),
  });
  assert.equal(result.outcome.status, "succeeded");

  assert.deepEqual(requests.map((r) => `${r.method} ${r.path}`), ["GET /pipeline/changes", "POST /mcp"]);
  assert.equal(requests[1].body.params.name, "revert-deal-field");
  assert.deepEqual(requests[1].body.params.arguments, { event_id: "w1", idempotency_key: "idem-1" });
});

test("both sides of a phase change speak the board's vocabulary, not the record layer's", async () => {
  // The three slugs where the mapping is not the identity. Before this, only
  // new_value was translated, so a receipt read "Due diligence → Closing"
  // while the board chip for that same deal said "Diligence".
  const moves = [["pending", "research"], ["site_selection", "negotiation"], ["due_diligence", "closing"]];
  const wire = moves.map(([from, to], index) => ({
    id: `p${index}`, recorded_at: `2026-09-10T14:0${index}:00.000000+00:00`, actor: "joe",
    verb: "patch-deal-field", subject_type: "deal", subject_id: "d1", field: "phase",
    old_value: { phase: from }, new_value: { phase: to },
  }));
  const client = createLiveClient({
    selfActor: "joe",
    fetchImpl: async () => respond({ events: wire, presence: [], capture_sessions: [], cursor: "1" }),
  });

  const changes = await client.getChanges(null);
  const receipts = ingestChangeEvents([], changes.events, context);
  const pair = (id) => {
    const receipt = receipts.find((r) => r.event_id === id);
    return [receipt.before, receipt.after];
  };
  assert.deepEqual(pair("p0"), ["On Deck", "Research"]);
  assert.deepEqual(pair("p1"), ["Research", "Negotiation"]);
  assert.deepEqual(pair("p2"), ["Diligence", "Closing"]);
  for (const receipt of receipts) {
    for (const side of [receipt.before, receipt.after]) {
      assert.ok(PHASES.includes(side), `${side} is not a phase this board ever displays`);
    }
  }

  const live = await file("dealroom/js/live-client.js");
  assert.match(live, /if \(e\.field === 'phase'\) \{\s*for \(const side of \['old_value', 'new_value'\]\)/,
    "one table, both sides — no second phase vocabulary anywhere");
  assert.equal((live.match(/PHASE_TO_UI = \{/g) || []).length, 1);
});

test("a server refusal reaches the row through the existing client, unrewritten", async () => {
  let posts = 0;
  const client = createLiveClient({
    selfActor: "joe",
    fetchImpl: async () => {
      posts += 1;
      const payload = { error: "newer_change_exists", hint: "Open the deal and review the newer value before changing it." };
      return {
        ok: true, status: 200, text: async () => JSON.stringify(payload),
        json: async () => ({ jsonrpc: "2.0", id: 1, result: { isError: true, content: [{ type: "text", text: JSON.stringify(payload) }] } }),
      };
    },
  });
  let undo = createUndoState();
  const call = {
    eventId: "w1",
    getState: () => undo,
    setState: (next) => { undo = next; },
    newKey: () => "idem-1",
    revert: (request) => client.revertDealField(request),
  };
  const result = await performUndo(call);
  assert.equal(result.outcome.status, "refused");
  assert.equal(result.outcome.code, "newer_change_exists");
  assert.equal(undo.w1.status, "refused");
  assert.equal((await performUndo(call)).started, false);
  assert.equal(posts, 1, "a refused undo is not re-sent");
});

test("the model stays a model: exercising every export reaches no network", async () => {
  // The boundary itself, not a description of it: every network entry point a
  // browser offers is replaced with a tripwire, then the whole model surface is
  // driven. The one write the panel can make is injected (`revert`), so if the
  // module could reach the wire on its own, this is where it would show.
  const tripped = [];
  const originals = new Map();
  for (const name of ["fetch", "XMLHttpRequest", "WebSocket", "EventSource"]) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    try {
      Object.defineProperty(globalThis, name, {
        configurable: true, writable: true,
        value: function tripwire() { tripped.push(name); throw new Error(`the model reached ${name}`); },
      });
    } catch { originals.delete(name); }
  }
  try {
    const events = [
      event({ id: "z1", field: "phase" }),
      event({ id: "z2", field: "note", old_value: null, new_value: "free text" }),
      event({ id: "z3", field: null, old_value: null, new_value: null }),
    ];
    let feed = createFeedProgress();
    feed = observeChangeBatch(feed, events);
    const receipts = ingestChangeEvents(ingestChangeEvents([], events, context), events, context);
    const views = receiptViews(receipts, { selfActor: "joe", undo: {}, now: Date.now() });
    receiptListHtml(views);
    views.map(receiptRowHtml);
    receiptsSignature(views);
    receiptsAnnouncement([], views);
    receiptTimeLabel(events[0].recorded_at);
    receiptInstant(events[0].recorded_at);
    compareReceipts(receipts[0], receipts[1]);
    normalizeChangeEvent(events[0], context);
    readableValue("operating_state", { state: "parked", reason: "other" });
    parkingReasonLabel("client_paused");
    escapeText("<script>");
    let undo = createUndoState();
    const claim = beginUndo(undo, "z1", () => "k1");
    undo = settleUndo(claim.state, "z1", classifyUndoOutcome({ response: { ok: true } }));
    await performUndo({
      eventId: "z2", getState: () => undo, setState: (next) => { undo = next; },
      newKey: () => "k2", revert: async () => ({ ok: true }),
    });
    assert.deepEqual(tripped, [], "the model made no network call of its own");
    assert.equal(feed.caught_up, false);
    assert.equal(undo.z2.status, "succeeded");
  } finally {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});

test("the model holds no transport, storage, DOM or timer of its own", async () => {
  const model = await file("dealroom/js/change-receipts.mjs");
  // Comments are prose about the code, not the code: they name the server file
  // and the verb this panel reuses, and a scan that cannot tell the difference
  // fails on documentation. Strip them and read what actually executes.
  const code = model
    .replace(/^\s*\/\*[\s\S]*?\*\//gm, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  assert.ok(code.includes("export function ingestChangeEvents"), "the stripper kept the code");
  assert.ok(!code.includes("Pure model"), "and removed the prose");
  for (const forbidden of [/\bfetch\s*\(/, /XMLHttpRequest/, /WebSocket/, /EventSource/, /\blocalStorage\b/,
    /\bsessionStorage\b/, /\bdocument\b/, /\bwindow\b/, /\bnavigator\b/, /setTimeout|setInterval/]) {
    assert.doesNotMatch(code, forbidden, `change-receipts.mjs must not reach for ${forbidden}`);
  }
  assert.doesNotMatch(code, /https?:|\/api\/|\/pipeline\/|\/mcp\b/, "no endpoint literal is compiled into the model");
});

test("app.js keeps one home for the revertible list, the parking labels and escaping", async () => {
  const app = await file("dealroom/js/app.js");
  assert.match(app, /from '\.\/change-receipts\.mjs'/);
  assert.match(app, /REVERTIBLE_FIELDS\.includes\(event\.field\)/);
  assert.doesNotMatch(app, /\['phase','owner','attention','next_date','operating_state'\]/,
    "the revertible list moved into the model instead of being copied");
  assert.doesNotMatch(app, /function parkingReasonLabel/, "parking labels have one home now");
  assert.match(app, /const esc = escapeText;/);
  assert.doesNotMatch(app, /state\.undoEventId/, "the toast no longer holds a single ambient undo target");
  assert.match(app, /data-undo="\$\{esc\(undoEventId\)\}"/, "the toast Undo names its own event");
  assert.match(app, /const undoButton = event\.target\.closest\('\[data-undo\]'\); if \(undoButton\) \{ await runUndo\(undoButton\.dataset\.undo, undoButton\)/,
    "toast and receipt row share one event-specific handler");
});

test("app.js accumulates from the poll it already runs and adds no endpoint or storage", async () => {
  const app = await file("dealroom/js/app.js");
  // One read, one batch, two consumers. The behavioural half of this claim is
  // the drain test above: three polls of a 450-event log produce exactly three
  // GETs and no second transport. This half pins that the board and the
  // receipts are fed from the same response rather than reading twice.
  const pollOnce = app.slice(app.indexOf("async function pollOnce"), app.indexOf("function userIsEditing"));
  assert.equal((app.match(/getChanges\(/g) || []).length, 1, "the app reads the changes feed in exactly one place");
  assert.match(pollOnce, /const batch = result\.events \|\| \[\];/, "that response's events are named once");
  assert.match(pollOnce, /for \(const event of batch\)/, "the board consumes that batch");
  assert.match(pollOnce, /ingestChangeEvents\(state\.receipts, batch,/,
    "and the receipts are folded from the same batch, never from a second read");
  assert.equal((app.match(/ingestChangeEvents\(/g) || []).length, 1, "receipts have one accumulation point");
  assert.match(app, /revert: \(request\) => state\.client\.revertDealField\(request\)/,
    "Undo calls the verb the Deal Room already uses");
  assert.doesNotMatch(app, /fetch\(['"`]\//, "no new same-origin request path");
  assert.doesNotMatch(app, /\/api\/v1\//);
  for (const match of app.matchAll(/localStorage\.\w+\('([^']+)'/g)) {
    assert.ok(["dealroom-theme", "dealroom-color-assist"].includes(match[1]),
      `receipts must not be persisted; found localStorage key ${match[1]}`);
  }
  // No optimistic write: the deal map is not edited on the undo path.
  const runUndo = app.slice(app.indexOf("async function runUndo"), app.indexOf("async function patchField"));
  assert.doesNotMatch(runUndo, /state\.deals\.(set|delete)|deal\[/, "no deal value moves before the server answers");
  assert.match(runUndo, /if \(result\.outcome\.status === 'succeeded'\)/, "reload of the board is gated on confirmed success");
});

test("app.js holds the panel and the toast shut until the cursor is current", async () => {
  const app = await file("dealroom/js/app.js");
  assert.match(app, /state\.feed = observeChangeBatch\(state\.feed, batch\);/,
    "every batch reports how far the cursor has got");
  assert.match(app, /const announce = !initial && state\.feed\.caught_up;/);
  assert.match(app, /if \(announce && event\.actor === state\.selfActor/,
    "no toast — and so no Undo — for a page of history");
  assert.match(app, /const views = state\.feed\.caught_up\s*\?\s*receiptViews\(/,
    "and no rows exposed until the same moment");
  assert.match(app, /feed: createFeedProgress\(\)/);
  assert.doesNotMatch(app, /session_start|sessionStart|receiptCutoff/i,
    "the gate is the feed's own paging, not a browser clock");

  // Focus bookkeeping: spent on every render, and never scrolls the page.
  assert.match(app, /state\.receiptFocus = null;\s*const signature = receiptsSignature\(views\);/,
    "a skipped render must not leave a focus request to be spent later");
  assert.match(app, /target\?\.focus\(\{ preventScroll: true \}\)/);
});

test("the panel is labelled, reachable by keyboard and touch, and honest about its scope", async () => {
  const [html, css] = await Promise.all([file("dealroom/index.html"), file("dealroom/css/app.css")]);
  assert.match(html, /id="receiptsPanel"[^>]*aria-labelledby="receiptsTitle"/);
  assert.match(html, /<ol class="receipt-list" id="receiptsList" aria-label="Recent Deal Room changes"><\/ol>/);
  // Exactly what the list holds: not "changes you made", not a window over the
  // log — the newest bounded set this session has seen, held in the page.
  assert.match(html, /newest 25 changes this session has seen/i);
  assert.match(html, /kept in this page only/i);
  assert.match(html, /Change history/, "the durable home is named on the surface");
  assert.doesNotMatch(html, /permanent|forever|always available|survives a reload/i,
    "no retention promise this surface cannot keep");
  assert.equal((html.match(/<script/g) || []).length, 1, "CSP is script-src 'self': one module tag, no inline script");
  assert.doesNotMatch(html, /Clients|Vendors|Tours/, "navigation is out of this increment");

  assert.match(css, /\.receipt-undo\{[^}]*min-height:44px/);
  assert.match(css, /\.receipt-deal\{[^}]*min-height:44px/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)\{\.receipt\{animation:none\}\}/);
  assert.doesNotMatch(css, /@media\(prefers-reduced-motion:reduce\)\{[^}]*\.receipt[^}]*display:\s*none/);
  assert.match(css, /\.receipt\{[^}]*var\(--card\)/, "the panel uses the board's own surface tokens");
  assert.doesNotMatch(css, /\.receipt-score|\.receipt-meter/, "no invented score");
});

test("the announcement lives on its own status line, and the list is not a live region", async () => {
  const [html, app, css] = await Promise.all([
    file("dealroom/index.html"), file("dealroom/js/app.js"), file("dealroom/css/app.css"),
  ]);
  assert.match(html, /<p class="receipts-live" id="receiptsLive" role="status" aria-live="polite"><\/p>/);
  assert.doesNotMatch(html, /role="log"/, "role=log is an implicit live region; the list must not be one");
  assert.doesNotMatch(html, /<ol[^>]*aria-live/, "and it must not be declared one either");
  assert.match(app, /const announcement = opened \? receiptsAnnouncement\(state\.receiptAnnounced, views\) : '';/);
  assert.match(app, /if \(opened\) state\.receiptAnnounced = views\.map\(\(view\) => view\.event_id\);/,
    "the baseline is only set once the list is open, so opening it is not announced");
  assert.match(app, /\$\('#receiptsLive'\)\.textContent = announcement;/,
    "text only: an untrusted deal name never becomes markup in the status line");
  assert.match(css, /\.receipts-live:empty\{margin:0;min-height:0\}/, "silence takes no space");
});

test("a counted control in the board toolbar reaches the panel and takes the keyboard with it", async () => {
  const [html, app, css] = await Promise.all([
    file("dealroom/index.html"), file("dealroom/js/app.js"), file("dealroom/css/app.css"),
  ]);
  // In the toolbar the board already has, between the filters and the search —
  // not a new navigation entry.
  assert.match(html, /class="filters"[\s\S]*id="receiptsJump"[\s\S]*class="search-wrap"/);
  assert.match(html, /<button type="button" class="receipts-jump" id="receiptsJump" aria-label="Go to recent changes" hidden>Recent changes<span class="receipts-count" id="receiptsCount">0<\/span><\/button>/);
  assert.match(html, /<h2 id="receiptsTitle" tabindex="-1">/, "the jump target can hold focus");
  assert.equal((html.match(/class="workspace"/g) || []).length, 5, "the navigation itself is untouched");

  assert.match(app, /jump\.hidden = views\.length === 0;/, "no empty control before the first change");
  assert.match(app, /\$\('#receiptsCount'\)\.textContent = String\(views\.length\);/);
  assert.match(app, /jump\.setAttribute\('aria-label', `Go to recent changes — \$\{views\.length\} seen in this session`\)/);
  assert.match(app, /\$\('#receiptsJump'\)\.onclick = goToReceipts;/);
  assert.match(app, /panel\.scrollIntoView\(\{ block: 'start' \}\);\s*\$\('#receiptsTitle'\)\.focus\(\{ preventScroll: true \}\)/,
    "scroll and focus move together, and the scroll honours the reduced-motion rule already in the stylesheet");

  // Focus after an Undo: the button first, then the row, then the heading.
  const focusReceipt = app.slice(app.indexOf("function focusReceipt"), app.indexOf("function goToReceipts"));
  assert.match(focusReceipt, /\[data-undo="[\s\S]*\[data-receipt="[\s\S]*#receiptsTitle/,
    "focus falls back in that order as the control is replaced or removed");
  assert.match(app, /const fromList = Boolean\(trigger && \$\('#receiptsList'\)\?\.contains\(trigger\)\)/,
    "a toast Undo must not drag focus down the page");
  assert.match(app, /await runUndo\(undoButton\.dataset\.undo, undoButton\)/);

  assert.match(css, /\.receipts-jump\{[^}]*min-height:44px/);
  assert.match(css, /#receiptsTitle:focus-visible\{outline:3px solid var\(--orange\)/);
});
