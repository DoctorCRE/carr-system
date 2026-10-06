// Deal Room board cells: what one intended change does when the answer is late,
// lost, refused, or never comes at all.
//
// Every claim here is EXECUTED against the real client-side model. The `patch`
// under test is a scripted transport, so "the answer was lost after the write
// landed", "the partner got there first" and "the server broke on the way out"
// are real sequences with real return values rather than descriptions of them.
// The one property everything else rests on: one intended action carries one
// idempotency key, unchanged across uncertainty and a deliberate retry.
import test from "node:test";
import assert from "node:assert/strict";

import {
  createFieldWriteState, performFieldWrite, classifyFieldWriteOutcome,
  unresolvedFieldWrites, pendingFieldWrite, fieldWriteMessage, sameFieldValue, cellKey,
  answerSupersededByFeed, nextCellBase,
} from "../../dealroom/js/field-write-reconciliation.mjs";
import { readFile } from "node:fs/promises";

import { createLiveClient } from "../../dealroom/js/live-client.js";
import { createFixtureClient } from "../../dealroom/js/fixture-client.js";

/**
 * A board that owns one write-state and one scripted transport. `sent` is what
 * actually left the page — the assertion surface for "sent once" and "sent the
 * same request again".
 */
function board(answers = [], { baseNow = null } = {}) {
  let writes = createFieldWriteState();
  const sent = [];
  let minted = 0;
  const queue = [...answers];
  return {
    sent,
    state: () => writes,
    keys: () => minted,
    write: (deal, field, value, base = null) => performFieldWrite({
      deal, field, value, base,
      // When a test supplies one, this is the board's changes feed: read again
      // when the answer lands, exactly as app.js reads state.fieldBase again.
      ...(baseNow ? { baseNow } : {}),
      getState: () => writes,
      setState: (next) => { writes = next; },
      newKey: () => `key-${++minted}`,
      patch: async (request) => {
        sent.push(request);
        const answer = queue.shift();
        if (!answer) throw new Error("no scripted answer left");
        return answer(request);
      },
    }),
  };
}

const ok = (extra = {}) => () => ({ status: "ok", ok: true, ...extra });
const conflictAnswer = (conflict) => () => ({ status: "conflict", conflict });
const dropped = (message = "connection dropped") => () => { throw new Error(message); };
const refusal = (payload) => () => {
  const error = new Error(`live patch-deal-field refused: ${payload.error}`);
  error.payload = payload;
  throw error;
};

test("a lost answer keeps the operation: the retry is the same request under the same key", async () => {
  // The write commits as e1, the answer never arrives, and the feed then moves
  // this cell's base to that very event. The retry must not notice — and must not
  // mistake its own event, arriving on the feed, for a partner's newer change.
  const b = board([dropped(), ok({ replayed: true, event_id: "e1" })]);

  const lost = await b.write("d1", "attention", true, "e0");
  assert.equal(lost.status, "unknown");
  assert.equal(lost.reason, "no_answer");
  assert.equal(lost.sent, true);
  assert.match(lost.message, /could not be confirmed/);
  assert.equal(pendingFieldWrite(b.state(), cellKey("d1", "attention")).status, "unknown");

  const retry = await b.write("d1", "attention", true, "e1");
  assert.equal(retry.status, "ok", "the server replayed the stored answer");
  assert.equal(retry.replayed, true);
  assert.equal(retry.retry, true, "and the board knows this was the same operation");
  assert.equal(retry.event_id, "e1", "the replay names the event this operation committed");
  assert.equal(retry.superseded, false,
    "which is the event the base moved to, so nothing newer happened to this cell");
  assert.equal(retry.message, null, "and the person is told nothing about a change that is their own");

  assert.equal(b.sent.length, 2);
  assert.deepEqual(b.sent[1], b.sent[0], "same key, same base, same value");
  assert.equal(b.sent[0].base_event_id, "e0", "the base is what was on screen, not what the feed says now");
  assert.equal(b.keys(), 1, "one intended action, one key");
  assert.deepEqual(b.state(), {}, "settled: the cell is clear for the next intent");
});

test("an answer is never superseded by the event it itself committed", async () => {
  // Four shapes of the same moment, because the difference between them is the
  // difference between a true sentence and a false one.

  // 1. An ordinary write, no retry and no partner, whose round trip outlasts a
  // poll: the feed delivers this write's own event while the request is still out.
  let base = "e0";
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let writes = createFieldWriteState();
  const delayed = performFieldWrite({
    deal: "d1", field: "phase", value: "Legal", base,
    baseNow: () => base,
    getState: () => writes, setState: (next) => { writes = next; },
    newKey: () => "key-1",
    patch: async () => { await gate; return { status: "ok", ok: true, event_id: "e1" }; },
  });
  base = "e1";
  release();
  const own = await delayed;
  assert.equal(own.superseded, false, "its own event is not a newer change to reconcile with");
  assert.equal(own.message, null);

  // 2. The lost-answer replay, which is the path this module exists for.
  const replayed = board([dropped(), ok({ replayed: true, event_id: "e1" })], { baseNow: () => "e1" });
  await replayed.write("d1", "owner", "dell", "e0");
  const retry = await replayed.write("d1", "owner", "dell", "e1");
  assert.equal(retry.superseded, false);

  // 3. A genuine partner event, newer than ours, delivered while we were out.
  const crossed = board([ok({ event_id: "e1" })], { baseNow: () => "e2" });
  const overtaken = await crossed.write("d1", "attention", true, "e0");
  assert.equal(overtaken.superseded, true, "an id this operation cannot account for is reconciled, not painted");
  assert.match(overtaken.message, /newer change to this cell/);

  // 4. An answer that names no event at all: the two bases are all the evidence
  // there is, and the conservative reading stands.
  const blind = board([ok()], { baseNow: () => "e9" });
  const unnamed = await blind.write("d1", "next_date", "2026-10-01", "e0");
  assert.equal(unnamed.event_id, null);
  assert.equal(unnamed.superseded, true, "no id means no way to recognise our own, so re-read");
});

test("a second click while the first request is open sends nothing", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let writes = createFieldWriteState();
  const sent = [];
  let minted = 0;
  const write = (value) => performFieldWrite({
    deal: "d1", field: "attention", value, base: "e0",
    getState: () => writes,
    setState: (next) => { writes = next; },
    newKey: () => `key-${++minted}`,
    patch: async (request) => { sent.push(request); await gate; return { status: "ok", ok: true }; },
  });

  const first = write(true);
  // The flag has not moved on screen, because nothing is applied before the
  // server answers — so the second click computes the same value all over again.
  const second = await write(true);
  assert.equal(second.status, "in_flight");
  assert.equal(second.sent, false);
  assert.match(second.message, /still being sent/);
  assert.equal(sent.length, 1, "one request left the page");

  release();
  assert.equal((await first).status, "ok");
  assert.equal(sent.length, 1);
  assert.equal(minted, 1);
});

test("a different intent cannot supersede an operation that is still unknown", async () => {
  const b = board([dropped(), ok({ replayed: true }), ok()]);

  const lost = await b.write("d1", "phase", "Negotiation", "e0");
  assert.equal(lost.status, "unknown");

  // A newer base and a different value: the tempting thing is to mint a fresh
  // key and let this one win. That is the defect, not the fix.
  const different = await b.write("d1", "phase", "Legal", "e1");
  assert.equal(different.status, "blocked");
  assert.equal(different.sent, false);
  assert.equal(b.sent.length, 1);
  assert.match(different.message, /earlier change that was never confirmed/);
  assert.equal(different.pending.request.idempotency_key, b.sent[0].idempotency_key);

  // The way out is that operation, offered by cell: retry it, same key.
  const unresolved = unresolvedFieldWrites(b.state(), "d1");
  assert.deepEqual(unresolved.map((entry) => [entry.cell, entry.field, entry.value]),
    [["d1|phase", "phase", "Negotiation"]]);
  const retry = await b.write("d1", unresolved[0].field, unresolved[0].value, "e1");
  assert.equal(retry.status, "ok");
  assert.equal(b.sent[1].idempotency_key, b.sent[0].idempotency_key);

  // Only once it is settled is the different intent a new operation — its own
  // key, and the base the feed has since delivered.
  const later = await b.write("d1", "phase", "Legal", "e1");
  assert.equal(later.status, "ok");
  assert.equal(b.sent.length, 3);
  assert.notEqual(b.sent[2].idempotency_key, b.sent[0].idempotency_key);
  assert.equal(b.sent[2].base_event_id, "e1");
  assert.deepEqual(b.state(), {});
});

test("the request an operation is defined by cannot be edited after it is sent", async () => {
  const b = board([dropped()]);
  const lost = await b.write("d1", "owner", "dell", "e0");
  assert.throws(() => { lost.request.value = "joe"; }, TypeError);
  assert.throws(() => { lost.request.idempotency_key = "other"; }, TypeError);
  assert.equal(pendingFieldWrite(b.state(), "d1|owner").request.value, "dell");
});

test("a genuine later edit is a new operation with its own key and today's base", async () => {
  const b = board([ok(), ok()]);
  await b.write("d1", "attention", true, "e0");
  await b.write("d1", "attention", false, "e1");
  assert.equal(b.sent.length, 2);
  assert.notEqual(b.sent[1].idempotency_key, b.sent[0].idempotency_key);
  assert.deepEqual([b.sent[0].base_event_id, b.sent[1].base_event_id], ["e0", "e1"]);
  assert.equal(b.keys(), 2);
});

test("a real partner conflict settles the cell and is handed back intact", async () => {
  const conflict = { conflict_id: "c1", deal: "d1", field: "owner",
    a: { actor: "dell", value: "dell" }, b: { actor: "joe", value: "joe" } };
  const b = board([conflictAnswer(conflict), ok()]);

  const crossed = await b.write("d1", "owner", "joe", "e0");
  assert.equal(crossed.status, "conflict");
  assert.deepEqual(crossed.conflict, conflict, "the server's own conflict, unrewritten");
  assert.equal(crossed.message, null, "an answered conflict is not an unknown");
  assert.deepEqual(b.state(), {}, "a conflict is a settled answer: nothing is left to retry");

  // And the person resolving it is not blocked from acting on the cell again.
  const after = await b.write("d1", "owner", "dell", "e2");
  assert.equal(after.status, "ok");
  assert.notEqual(b.sent[1].idempotency_key, b.sent[0].idempotency_key);
});

test("a refusal, a server fault, a spent key and a silence are four different answers", async () => {
  // Declined: the server made a decision about this request. Terminal, and its
  // own words are what the person reads.
  const declined = board([refusal({ error: "invalid_base_event", hint: "base_event_id does not name an event on this cell" })]);
  const refused = await declined.write("d1", "phase", "Legal", "gone");
  assert.equal(refused.status, "refused");
  assert.equal(refused.reason, "declined");
  assert.equal(refused.code, "invalid_base_event");
  assert.match(refused.message, /base_event_id does not name an event on this cell/);
  assert.deepEqual(declined.state(), {}, "an answered refusal leaves nothing pending");

  // The server's own exception arrives on the same channel as a refusal. It is
  // not one: the write may have committed, so it stays unknown and retryable.
  const broke = board([refusal({ error: "unhandled_verb_failure", hint: "TypeError: cannot read property of undefined" })]);
  const fault = await broke.write("d1", "phase", "Legal", "e0");
  assert.equal(fault.status, "unknown");
  assert.equal(fault.reason, "server_error");
  assert.match(fault.message, /reported an error instead of confirming it/);
  assert.doesNotMatch(fault.message, /TypeError/, "server diagnostics are not shown to a partner");
  assert.equal(pendingFieldWrite(broke.state(), "d1|phase").status, "unknown");

  // A key already carrying a different request. Re-sending is exactly what this
  // refusal prevents, so nothing is retained to re-send.
  const spent = board([refusal({ error: "key_reuse" })]);
  const reused = await spent.write("d1", "attention", true, "e0");
  assert.equal(reused.status, "refused");
  assert.equal(reused.reason, "key_reuse");
  assert.match(reused.message, /Open the deal and check what it holds now/);
  assert.deepEqual(spent.state(), {});

  // Nothing came back at all — said as that, and not as "the server answered
  // with an error", which is a different thing that now has its own sentence.
  const silent = board([dropped("Failed to fetch")]);
  const quiet = await silent.write("d1", "attention", true, "e0");
  assert.equal(quiet.reason, "no_answer");
  assert.match(quiet.message, /nothing came back from the server/);
  assert.equal(quiet.http_status, null);

  // A non-2xx that is not a decision: an answer came back, and it may still have
  // landed. The status travels with it.
  const httpFault = classifyFieldWriteOutcome({ error: Object.assign(new Error("502"), { status: 502 }) });
  assert.deepEqual({ status: httpFault.status, reason: httpFault.reason, http_status: httpFault.http_status },
    { status: "unknown", reason: "server_error", http_status: 502 });
  // And one that IS a decision, taken before the verb ran.
  const denied = classifyFieldWriteOutcome({ error: Object.assign(new Error("401"), { status: 401 }) });
  assert.deepEqual({ status: denied.status, reason: denied.reason, code: denied.code },
    { status: "refused", reason: "unauthorized", code: "http_401" });
  // An answer with no recognisable shape is an unknown, never a success.
  assert.equal(classifyFieldWriteOutcome({ response: null }).status, "unknown");
  assert.equal(classifyFieldWriteOutcome({ response: { status: "ok", ok: false } }).status, "unknown");
});

test("a transport failure returns an outcome instead of rejecting", async () => {
  const rejections = [];
  const onRejection = (reason) => rejections.push(reason);
  process.on("unhandledRejection", onRejection);
  try {
    const b = board([dropped("Failed to fetch"), dropped("Failed to fetch")]);
    await assert.doesNotReject(() => b.write("d1", "next_date", "2026-10-01", "e0"));
    await assert.doesNotReject(() => b.write("d1", "next_date", "2026-10-01", "e0"));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(b.sent.length, 2);
    assert.equal(b.sent[1].idempotency_key, b.sent[0].idempotency_key,
      "a retry that fails again is still the same operation");
    assert.equal(pendingFieldWrite(b.state(), "d1|next_date").attempts, 2);
  } finally {
    process.off("unhandledRejection", onRejection);
  }
  assert.deepEqual(rejections, [], "no rejection escaped to the page");
});

test("the operating-state object is compared by value, so a redraw is not a new intent", async () => {
  const parked = { state: "parked", reason: "client_paused", note: "Waiting on the client" };
  const b = board([dropped(), ok({ replayed: true })]);

  const lost = await b.write("d7", "operating_state", parked, "e0");
  assert.equal(lost.status, "unknown");

  // The dialog rebuilt the same intent from the same form: a structurally equal
  // object, written in a different order, is the SAME change.
  const again = { note: "Waiting on the client", reason: "client_paused", state: "parked" };
  assert.equal(sameFieldValue(parked, again), true);
  const retry = await b.write("d7", "operating_state", again, "e1");
  assert.equal(retry.status, "ok");
  assert.equal(b.sent[1].idempotency_key, b.sent[0].idempotency_key);
  assert.deepEqual(b.sent[1].value, parked, "the value that was sent, not the one rebuilt later");

  // A different reason is a different parking decision, and cannot quietly take
  // the unresolved one's place.
  const other = board([dropped()]);
  await other.write("d7", "operating_state", parked, "e0");
  const changed = await other.write("d7", "operating_state",
    { state: "parked", reason: "other", note: null }, "e0");
  assert.equal(changed.status, "blocked");
  assert.equal(other.sent.length, 1);
});

test("unresolved entries are per cell, and name the cell a retry belongs to", async () => {
  const b = board([dropped(), dropped(), ok()]);
  await b.write("d1", "attention", true, "e0");
  await b.write("d1", "phase", "Legal", "e0");
  await b.write("d2", "owner", "joe", "e0");

  assert.deepEqual(unresolvedFieldWrites(b.state()).map((entry) => entry.cell),
    ["d1|attention", "d1|phase"]);
  assert.deepEqual(unresolvedFieldWrites(b.state(), "d2"), []);
  assert.equal(unresolvedFieldWrites(b.state(), "d1").every((entry) => entry.message), true,
    "each one carries the sentence its row will show");
});

test("a delayed answer that lands after the feed moved is accepted but not painted", async () => {
  // The FIRST answer, not a retry: the request is out, a PARTNER's change to the
  // same cell arrives on the feed, and only then does the server answer. Our own
  // event is e1; the cell's newest is e2, which is not ours.
  let base = "e0";
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let writes = createFieldWriteState();
  const run = performFieldWrite({
    deal: "d1", field: "phase", value: "Negotiation", base,
    baseNow: () => base,
    getState: () => writes, setState: (next) => { writes = next; },
    newKey: () => "key-1",
    patch: async () => { await gate; return { status: "ok", ok: true, event_id: "e1" }; },
  });
  base = "e2";
  release();

  const result = await run;
  assert.equal(result.status, "ok", "the server accepted it, and that is not in doubt");
  assert.equal(result.superseded, true, "but it is no longer the newest word on this cell");
  assert.match(result.message, /showing the current value rather than applying this one/);
  assert.deepEqual(writes, {}, "answered is answered: nothing is left pending");
});

test("a replayed answer after a partner's newer change is superseded, for a cell and for operating state", async () => {
  for (const [field, value] of [
    ["attention", true],
    ["operating_state", { state: "parked", reason: "client_paused", note: null }],
  ]) {
    let base = "e0";
    // Our own write committed e1; the partner's later change is e7.
    const b = board([dropped(), ok({ replayed: true, event_id: "e1" })], { baseNow: () => base });

    const lost = await b.write("d9", field, value, base);
    assert.equal(lost.status, "unknown", `${field}: the answer never came back`);
    assert.equal(lost.superseded, false, "an unanswered write has no value to withhold");

    // The partner changes the same cell and the feed delivers it.
    base = "e7";
    const retry = await b.write("d9", field, value, base);
    assert.equal(retry.status, "ok", `${field}: the server replayed the recorded answer`);
    assert.equal(retry.replayed, true);
    assert.equal(b.sent[1].base_event_id, "e0", "the operation kept the base it was built on");
    assert.equal(retry.superseded, true, `${field}: so its value must not be painted over the newer one`);
    assert.match(retry.message, /was recorded\. The board has since seen a newer change/);
    assert.deepEqual(b.state(), {});
  }
});

test("an answer whose cell has not moved says nothing and is applied as before", async () => {
  let base = "e0";
  const b = board([ok()], { baseNow: () => base });
  const result = await b.write("d1", "owner", "dell", base);
  assert.equal(result.status, "ok");
  assert.equal(result.superseded, false);
  assert.equal(result.message, null, "a plain success is not narrated");
});

test("supersession is decided by the bases the client has and the event the answer names", () => {
  const request = { deal: "d1", field: "phase", value: "Legal", base_event_id: "e1", idempotency_key: "k" };
  assert.equal(answerSupersededByFeed(request, "e1"), false);
  assert.equal(answerSupersededByFeed(request, "e2"), true);
  // The third input: the event this operation committed. When the cell's newest
  // event IS that one, the thing that moved the base was this write arriving.
  assert.equal(answerSupersededByFeed(request, "e2", "e2"), false);
  assert.equal(answerSupersededByFeed(request, "e2", "e3"), true,
    "an id that is neither the base nor ours is someone else's, and is reconciled");
  assert.equal(answerSupersededByFeed(request, "e2", null), true, "no id, no recognition: re-read");
  assert.equal(answerSupersededByFeed({ ...request, base_event_id: null }, "e5", "e5"), false,
    "including on a cell that had no history when the request was built");
  // A cell with no event seen yet: null on both sides is not movement, and the
  // first event this board hears about that cell is.
  assert.equal(answerSupersededByFeed({ ...request, base_event_id: null }, null), false);
  assert.equal(answerSupersededByFeed({ ...request, base_event_id: null }, undefined), false);
  assert.equal(answerSupersededByFeed({ ...request, base_event_id: null }, "e1"), true);
  assert.equal(answerSupersededByFeed(null, "e1"), false);
  // The value plays no part: this is a statement about the cell's history, not
  // about what anyone wrote to it.
  assert.equal(answerSupersededByFeed({ ...request, value: "something else" }, "e1"), false);
});

// ------------------------------------------------- through the live client

/** A non-2xx answer, as fetch hands one over. */
const httpAnswer = (status, body) => ({
  ok: status >= 200 && status < 300, status,
  text: async () => body, json: async () => JSON.parse(body),
});

/** A 200 carrying an MCP tool result, which is what a real ok answer looks like. */
const rpcAnswer = (payload) => ({
  ok: true, status: 200,
  text: async () => JSON.stringify(payload),
  json: async () => ({ jsonrpc: "2.0", id: 1,
    result: { content: [{ type: "text", text: JSON.stringify(payload) }] } }),
});

/** One cell, written through the real live client over a scripted transport. */
function liveBoard(fetchImpl) {
  let writes = createFieldWriteState();
  const client = createLiveClient({ fetchImpl });
  return {
    client,
    state: () => writes,
    write: (value = true) => performFieldWrite({
      deal: "d1", field: "attention", value, base: "e0",
      getState: () => writes, setState: (next) => { writes = next; },
      newKey: () => "key-live",
      patch: (request) => client.patchDealField(request),
    }),
  };
}

test("the live client keeps a non-2xx status and keeps the server's stack out of the message", async () => {
  const stack = "Error: boom\n    at applyDealRoomField (/srv/mcp-server/src/tools.js:1990:3)";
  const client = createLiveClient({ fetchImpl: async () => httpAnswer(500, stack) });
  await assert.rejects(
    () => client.patchDealField({ deal: "d1", field: "attention", value: true,
      base_event_id: null, idempotency_key: "k1" }),
    (error) => {
      assert.equal(error.status, 500, "the status survives, which is what told every failure apart");
      assert.equal(error.message, "live patch-deal-field -> HTTP 500");
      assert.doesNotMatch(error.message, /applyDealRoomField|tools\.js/,
        "a server stack is not a statement about this deal, and this page prints messages");
      assert.equal(error.body, stack, "kept on the error for the console; no surface renders it");
      return true;
    });
});

test("through the live client: 401 is a decision, a 5xx and a dropped connection are not", async () => {
  const denied = liveBoard(async () => httpAnswer(401, "unauthorized"));
  const refused = await denied.write();
  assert.equal(refused.status, "refused");
  assert.equal(refused.reason, "unauthorized");
  assert.equal(refused.http_status, 401);
  assert.match(refused.message, /was not saved/);
  assert.match(refused.message, /Sign in again/);
  assert.match(refused.message, /\(HTTP 401\)$/, "the status is named; the body never is");
  assert.deepEqual(denied.state(), {},
    "nothing is retained, because retrying it is not the thing to do — signing in is");

  const forbidden = await liveBoard(async () => httpAnswer(403, "forbidden")).write();
  assert.equal(forbidden.status, "refused");
  assert.equal(forbidden.reason, "unauthorized");

  const broken = liveBoard(async () => httpAnswer(503, "upstream connect error"));
  const uncertain = await broken.write();
  assert.equal(uncertain.status, "unknown");
  assert.equal(uncertain.reason, "server_error");
  assert.equal(uncertain.http_status, 503);
  assert.match(uncertain.message, /reported an error instead of confirming it/);
  assert.doesNotMatch(uncertain.message, /never answered/, "it did answer — with an error");
  assert.equal(pendingFieldWrite(broken.state(), "d1|attention").status, "unknown",
    "and it stays retryable under the key it already used");

  const gone = liveBoard(async () => { throw new TypeError("Failed to fetch"); });
  const silent = await gone.write();
  assert.equal(silent.status, "unknown");
  assert.equal(silent.reason, "no_answer");
  assert.equal(silent.http_status, null);
  assert.match(silent.message, /nothing came back from the server/);
  assert.doesNotMatch(silent.message, /HTTP/, "there was no answer to name");
});

test("the board read's per-cell bases reach the caller through the live client", async () => {
  const base = { phase: { id: "e5", recorded_at: "2026-09-10T14:00:00.123456+00:00" } };
  const client = createLiveClient({ fetchImpl: async () => rpcAnswer({ actor: "joe",
    deals: [{ id: "d1", phase: "negotiation", field_base: base }], accounts: [] }) });

  const board = await client.getBoard();
  assert.deepEqual(board.deals[0].field_base, base,
    "unchanged, and still in the record layer's own field vocabulary");
  assert.equal(board.deals[0].phase, "Negotiation",
    "while the VALUE is translated for the board, as it always was");
});

test("the committed event identity rides the ok answer through the live client", async () => {
  const at = "2026-09-10T14:00:00.123456+00:00";
  const b = liveBoard(async () => rpcAnswer({ ok: true, deal_id: "d1", field: "attention",
    old_value: false, new_value: true, event_id: "e9", event_recorded_at: at }));
  const result = await b.write();
  assert.equal(result.status, "ok");
  assert.equal(result.event_id, "e9", "taken from the answer, not derived");
  assert.equal(result.event_recorded_at, at);
  assert.equal(result.superseded, false);
});

test("an ok answer with no event identity leaves the caller with no base to advance to", async () => {
  const b = liveBoard(async () => rpcAnswer({ ok: true, deal_id: "d1", field: "attention" }));
  const result = await b.write();
  assert.equal(result.status, "ok");
  assert.equal(result.event_id, null, "null is not an id, and the caller must not invent one");
  assert.equal(result.event_recorded_at, null);
});

/**
 * The real fixture client over the real seed. `createFixtureClient` reads its
 * seed with fetch, which has no file: transport in node, so the file is handed
 * to it directly — the client itself is untouched.
 */
async function fixtureBoard() {
  const seed = JSON.parse(await readFile(new URL("../../dealroom/data/board-seed.json", import.meta.url), "utf8"));
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => seed });
  try {
    return await createFixtureClient({ selfActor: "joe" });
  } finally {
    globalThis.fetch = realFetch;
  }
}

test("the fixture board answers with its committed event too, and two edits in a row both land", async () => {
  const client = await fixtureBoard();
  const { deals } = await client.getBoard();
  const deal = deals[0];

  // The base comes from the read itself, exactly as it does live — no feed, no
  // catch-up, no waiting a poll before the first edit of a session can land.
  const base = deal.field_base?.attention?.id ?? null;

  const first = await client.patchDealField({ deal: deal.id, field: "attention",
    value: !deal.attention, base_event_id: base, idempotency_key: "fixture-attention-1" });
  assert.equal(first.status, "ok");
  assert.ok(first.event_id, "the answer names the event it committed");
  assert.equal(first.event_id, first.event.id, "and it is that event's own id");
  assert.equal(first.event_recorded_at, first.event.recorded_at);

  // No poll in between. The second, different edit uses the id the first answer
  // returned, and lands — where a stale base would have drawn a conflict.
  const second = await client.patchDealField({ deal: deal.id, field: "attention",
    value: deal.attention, base_event_id: first.event_id, idempotency_key: "fixture-attention-2" });
  assert.equal(second.status, "ok", "no conflict with itself");
  assert.notEqual(second.event_id, first.event_id);

  // And the concurrency check is untouched: the superseded base is still refused.
  const stale = await client.patchDealField({ deal: deal.id, field: "attention",
    value: !deal.attention, base_event_id: first.event_id, idempotency_key: "fixture-attention-3" });
  assert.equal(stale.status, "conflict");
});

test("a fixture cell WITH history gets its base from the read, and the first edit lands", async () => {
  const client = await fixtureBoard();
  const { deals } = await client.getBoard();
  // The seed's own history: one deal carries prior phase and next_step events.
  const withHistory = deals.find((row) => row.field_base?.phase);
  assert.ok(withHistory, "the seed must still carry a record with phase history");
  assert.ok(withHistory.field_base.phase.id);
  assert.ok(withHistory.field_base.phase.recorded_at);
  assert.equal(withHistory.field_base.owner, undefined,
    "and a cell with no history has no entry, not a base of null");

  const first = await client.patchDealField({ deal: withHistory.id, field: "phase",
    value: "Closing", base_event_id: withHistory.field_base.phase.id,
    idempotency_key: "fixture-phase-1" });
  assert.equal(first.status, "ok", "a first edit on a record with history, with no feed at all");

  // The base the read handed out is the one the client's own concurrency check
  // compares against, so a superseded one is still refused.
  const stale = await client.patchDealField({ deal: withHistory.id, field: "phase",
    value: "Legal", base_event_id: withHistory.field_base.phase.id,
    idempotency_key: "fixture-phase-2" });
  assert.equal(stale.status, "conflict");
});

test("a cell's base only ever moves forward, by the record layer's own ordering", () => {
  const older = { id: "aaa", recorded_at: "2026-09-10T14:00:00.000Z" };
  const newer = { id: "bbb", recorded_at: "2026-09-10T15:00:00.000000+00:00" };

  assert.equal(nextCellBase(older, newer), newer);
  assert.equal(nextCellBase(newer, older), newer,
    "a page of catch-up history cannot drag a known newer base backwards");
  assert.equal(nextCellBase(null, older), older);
  assert.equal(nextCellBase(older, null), older, "and no candidate is not news");
  assert.equal(nextCellBase(older, { id: "aaa", recorded_at: "2026-09-10T14:00:00.000Z" }), older,
    "the same event, heard twice, is not movement");

  // Same instant in two spellings: the tie falls to the id, which is exactly
  // what (recorded_at, id) does on the server.
  const tieA = { id: "aaa", recorded_at: "2026-09-10T14:00:00.000Z" };
  const tieB = { id: "bbb", recorded_at: "2026-09-10T14:00:00+00:00" };
  assert.equal(nextCellBase(tieA, tieB), tieB);
  assert.equal(nextCellBase(tieB, tieA), tieB);

  // Microseconds apart, one format: the text still separates them.
  const fine = (suffix, id) => ({ id, recorded_at: `2026-09-10T14:00:00.${suffix}+00:00` });
  assert.equal(nextCellBase(fine("000001", "zzz"), fine("000002", "aaa")).recorded_at,
    "2026-09-10T14:00:00.000002+00:00");
  assert.equal(nextCellBase(fine("000002", "aaa"), fine("000001", "zzz")).recorded_at,
    "2026-09-10T14:00:00.000002+00:00");

  // A time nobody can read is not a reason to refuse news: that is the behaviour
  // the board had when the feed was its only source.
  const untimed = { id: "ccc", recorded_at: null };
  assert.equal(nextCellBase(older, untimed), untimed);
  assert.equal(nextCellBase(untimed, older), older);
});

test("the message names the cell when a toast has to, and speaks plainly when it does not", () => {
  const outcome = { status: "unknown", reason: "no_answer" };
  assert.equal(fieldWriteMessage(outcome, "Attention flag on Riverbank Dental"),
    "Attention flag on Riverbank Dental could not be confirmed — nothing came back from the server. It may already be saved: send it again from the Unconfirmed changes bar at the top of the page, or open the deal to check, before changing this cell again.");
  assert.match(fieldWriteMessage(outcome), /^This change could not be confirmed/);
  // Every sentence that asks for a retry names a control that is actually on
  // screen: the row's own button is hidden by a filter, a search or a workspace
  // switch, and the bar is not.
  for (const reason of ["no_answer", "server_error", "unresolved"]) {
    assert.match(fieldWriteMessage({ status: "unknown", reason }),
      /Unconfirmed changes bar at the top of the page/, reason);
  }
  assert.equal(fieldWriteMessage({ status: "ok" }), null);
  assert.equal(fieldWriteMessage({ status: "conflict" }), null);
  assert.match(fieldWriteMessage({ status: "refused", reason: "declined", code: "parking_reason_required" }),
    /refused by the server: parking reason required/);
  // The status is named only when there is one, and it is the only thing from a
  // non-2xx answer that a person is ever shown.
  assert.match(fieldWriteMessage({ status: "unknown", reason: "server_error", http_status: 503 }),
    /reported an error instead of confirming it\. .*\(HTTP 503\)$/);
  assert.doesNotMatch(fieldWriteMessage({ status: "unknown", reason: "server_error" }), /HTTP/);
});
