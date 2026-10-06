import test from "node:test";
import assert from "node:assert/strict";
import { runCommand, listCommands, buildDealContext } from "../js/commands.js";

// Exact shapes quoted from mcp-server/src/tools.js's handlers, as read for
// this fix (PR #1259 review): add-deal-note returns
//   { ok: true, deal_id, note_id, created_at }
// set-next-step returns
//   { ok: true, deal_id, next_step_id, next_action_id, supersedes, created_at }
// live-client.js's addDealNote/setNextStep pass this straight through with no
// wrapping (`return write(verb, args)`), unlike patchDealField which adds its
// own {status:'ok', ...}. Both live() shapes below are copied verbatim.

function liveClient(overrides = {}) {
  return {
    async addDealNote({ deal, idempotency_key }) {
      return { ok: true, deal_id: deal, note_id: "n-42", created_at: "2026-09-25T12:00:00.000Z", _idempotency_key_seen: idempotency_key };
    },
    async setNextStep({ deal, idempotency_key }) {
      return {
        ok: true, deal_id: deal, next_step_id: "ns-9", next_action_id: "na-3",
        supersedes: "ns-8", created_at: "2026-09-25T12:00:00.000Z", _idempotency_key_seen: idempotency_key,
      };
    },
    ...overrides,
  };
}

function fixtureClient(overrides = {}) {
  return {
    async setNextStep({ deal, text, next_date }) {
      return { status: "ok", event: { id: "evt1", deal, field: "next_step", new_value: text, next_date } };
    },
    async addDealNote({ deal, text }) {
      return { status: "ok", event: { id: "evt2", deal, field: "note", new_value: text } };
    },
    ...overrides,
  };
}

function commandParityGuard([uiPayload, docPayload]) {
  if (!uiPayload || !docPayload) return ["missing_entry_point"];
  return ["deal", "text", "next_date"]
    .filter((field) => uiPayload[field] !== docPayload[field])
    .map((field) => `${field}_diverged`);
}

test("listCommands names the two governed commands", () => {
  const names = listCommands().map((c) => c.name).sort();
  assert.deepEqual(names, ["add_note", "set_next_step"]);
});

test("runCommand refuses with no deal in context, before ever touching the client", async () => {
  let called = false;
  const client = fixtureClient({ setNextStep: async () => { called = true; return { status: "ok" }; } });
  const receipt = await runCommand(client, "set_next_step", "call the landlord", {});
  assert.equal(receipt.status, "refused");
  assert.equal(called, false);
});

test("runCommand refuses empty text before touching the client", async () => {
  const receipt = await runCommand(fixtureClient(), "add_note", "   ", { dealId: "d1" });
  assert.equal(receipt.status, "refused");
  assert.match(receipt.detail.reason, /note text/);
});

test("runCommand returns ok with a normalized event on a successful fixture write", async () => {
  const receipt = await runCommand(fixtureClient(), "set_next_step", "send the LOI", { dealId: "d1", nextDate: "2026-10-01" });
  assert.equal(receipt.status, "ok");
  assert.equal(receipt.command, "set_next_step");
  assert.equal(receipt.deal, "d1");
  assert.equal(receipt.detail.event.new_value, "send the LOI");
});

// This is blocker 1 from the independent review of PR #1259: a real live
// success was being reported as `refused` because toReceipt only recognized
// {status:'ok'}, never live-client's real {ok:true, ...} shape.
test("runCommand recognizes a real live setNextStep success ({ok:true,...}), not just {status:'ok'}", async () => {
  const receipt = await runCommand(liveClient(), "set_next_step", "send the LOI", { dealId: "d1" });
  assert.equal(receipt.status, "ok", "a live ok:true payload must never read as refused");
  assert.equal(receipt.detail.event.id, "ns-9");
  assert.equal(receipt.detail.event.deal_id, "d1");
  assert.equal(receipt.detail.event.next_action_id, "na-3");
  assert.equal(receipt.detail.event.supersedes, "ns-8");
});

test("runCommand recognizes a real live addDealNote success ({ok:true,...})", async () => {
  const receipt = await runCommand(liveClient(), "add_note", "called the landlord", { dealId: "d1" });
  assert.equal(receipt.status, "ok");
  assert.equal(receipt.detail.event.id, "n-42");
  assert.equal(receipt.detail.event.deal_id, "d1");
});

test("runCommand surfaces a client conflict as status:conflict, not a false ok", async () => {
  const client = fixtureClient({
    addDealNote: async () => ({ status: "conflict", conflict: { conflict_id: "c1", field: "note" } }),
  });
  const receipt = await runCommand(client, "add_note", "called the landlord", { dealId: "d1" });
  assert.equal(receipt.status, "conflict");
  assert.equal(receipt.detail.conflict.conflict_id, "c1");
});

test("runCommand reports unavailable, never a mock, when the client lacks the write", async () => {
  const client = {}; // no setNextStep / addDealNote at all — the honest V5-J102/F01-not-built case
  const receipt = await runCommand(client, "set_next_step", "send the LOI", { dealId: "d1" });
  assert.equal(receipt.status, "unavailable");
  assert.match(receipt.detail.reason, /V5-J102/);
});

test("runCommand catches a thrown client error and refuses instead of crashing the caller", async () => {
  const client = fixtureClient({ addDealNote: async () => { throw new Error("network down"); } });
  const receipt = await runCommand(client, "add_note", "called the landlord", { dealId: "d1" });
  assert.equal(receipt.status, "refused");
  assert.match(receipt.detail.reason, /network down/);
});

test("an unknown command name refuses instead of throwing", async () => {
  const receipt = await runCommand(fixtureClient(), "delete_everything", "x", { dealId: "d1" });
  assert.equal(receipt.status, "refused");
  assert.match(receipt.detail.reason, /Unknown command/);
});

// Blocker 1's retry-safety requirement: a caller-supplied idempotencyKey is
// the SAME key the server sees, so a retry replays instead of writing twice.
test("every receipt carries the idempotencyKey the attempt used, minted fresh when the caller supplies none", async () => {
  const receipt = await runCommand(liveClient(), "add_note", "x", { dealId: "d1" });
  assert.equal(typeof receipt.idempotencyKey, "string");
  assert.ok(receipt.idempotencyKey.length > 0);
});

test("a caller-supplied idempotencyKey reaches the client verbatim, and a retry with the same key sends the same key again", async () => {
  const seen = [];
  const client = liveClient({
    async addDealNote({ deal, idempotency_key }) {
      seen.push(idempotency_key);
      return { ok: true, deal_id: deal, note_id: "n-42", created_at: "t" };
    },
  });
  const first = await runCommand(client, "add_note", "called the landlord", { dealId: "d1" }, { idempotencyKey: "fixed-key-1" });
  const retry = await runCommand(client, "add_note", "called the landlord", { dealId: "d1" }, { idempotencyKey: first.idempotencyKey });
  assert.deepEqual(seen, ["fixed-key-1", "fixed-key-1"], "a retry must reuse the exact same idempotency_key, never mint a new one");
  assert.equal(first.idempotencyKey, retry.idempotencyKey);
});

test("omitting idempotencyKey mints a different key per call — only an explicit retry reuses one", async () => {
  const client = liveClient();
  const a = await runCommand(client, "add_note", "x", { dealId: "d1" });
  const b = await runCommand(client, "add_note", "x", { dealId: "d1" });
  assert.notEqual(a.idempotencyKey, b.idempotencyKey);
});

// ------------------------------------------------------- buildDealContext

test("buildDealContext defaults next_date to the deal's CURRENT value when the caller does not override it", () => {
  const deal = { id: "d1", name: "Acme", next_date: "2026-11-01" };
  const context = buildDealContext(deal);
  assert.equal(context.dealId, "d1");
  assert.equal(context.label, "Acme");
  assert.equal(context.nextDate, "2026-11-01", "no override must preserve the deal's existing date, not clear it");
});

test("buildDealContext honors an explicit override, including an explicit null (clearing the date)", () => {
  const deal = { id: "d1", name: "Acme", next_date: "2026-11-01" };
  assert.equal(buildDealContext(deal, { nextDate: "2026-12-25" }).nextDate, "2026-12-25");
  assert.equal(buildDealContext(deal, { nextDate: null }).nextDate, null, "an EXPLICIT null must still be honored — that is a deliberate clear, not an omission");
});

test("buildDealContext with no deal (nothing open) returns a context runCommand will refuse on", () => {
  const context = buildDealContext(undefined);
  assert.equal(context.dealId, null);
  assert.equal(context.nextDate, null);
});

// ------------------------------------------------------- real UI/Doc parity
//
// Blocker 2 from the review: a "parity" test that only proved two identical
// fake-client calls produce identical receipts was fake parity — it could
// not have caught the next_date divergence, because both simulated calls
// used identical context by construction. This test instead drives the
// ACTUAL two entry points' own context-building logic (the form's explicit
// override vs the Doc's no-override default) against ONE SHARED deal object,
// and inspects the literal payload sent to the client.

test("the UI form (explicit next_date) and the Doc composer (no override) issue the same command through the same code path, and neither corrupts the other's date handling", async () => {
  const deal = { id: "d7", name: "Riverside Clinic", next_date: "2026-10-15" };
  const sentPayloads = [];
  const client = {
    async setNextStep(args) { sentPayloads.push(args); return { ok: true, deal_id: args.deal, next_step_id: "ns-1", created_at: "t" }; },
  };

  // The UI form: its <input> is pre-filled with the deal's current date, so
  // "the user only edited the text" still resends that same date explicitly.
  const formContext = buildDealContext(deal, { nextDate: deal.next_date });
  const formReceipt = await runCommand(client, "set_next_step", "send the LOI", formContext);

  // The Doc composer: no date field exists in its typed command syntax, so
  // it never overrides — buildDealContext must default to the CURRENT date.
  const docContext = buildDealContext(deal);
  const docReceipt = await runCommand(client, "set_next_step", "send the LOI", docContext);

  assert.equal(formReceipt.status, "ok");
  assert.equal(docReceipt.status, "ok");
  // Both entry points must have sent the SAME next_date — the deal's real
  // current one — not have the Doc path silently null it out.
  assert.equal(sentPayloads[0].next_date, "2026-10-15");
  assert.equal(sentPayloads[1].next_date, "2026-10-15");
  assert.equal(sentPayloads[0].text, sentPayloads[1].text);
  assert.equal(sentPayloads[0].deal, sentPayloads[1].deal);
  assert.deepEqual(commandParityGuard(sentPayloads), []);
});

test("the UI/Doc parity guard kills a planted Doc-clears-next-date mutant", () => {
  const uiPayload = { deal: "d7", text: "send the LOI", next_date: "2026-10-15" };
  const mutantDocPayload = { ...uiPayload, next_date: null };
  assert.deepEqual(commandParityGuard([uiPayload, mutantDocPayload]), ["next_date_diverged"]);
});

test("the Doc composer preserves an existing next_date across a set_next_step that only changes the text", async () => {
  const deal = { id: "d9", name: "Coastal Med Plaza", next_date: "2026-11-20" };
  let sent = null;
  const client = { async setNextStep(args) { sent = args; return { ok: true, deal_id: args.deal, next_step_id: "ns-2", created_at: "t" }; } };
  const docContext = buildDealContext(deal); // Doc never supplies a date
  await runCommand(client, "set_next_step", "called the tenant rep", docContext);
  assert.equal(sent.next_date, "2026-11-20", "the Doc must never clear a date it was never asked to change");
});
