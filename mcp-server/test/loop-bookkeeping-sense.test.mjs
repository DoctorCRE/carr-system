// loop-bookkeeping-sense.test.mjs — close-loop must tell a LOOP merged into
// another loop apart from a PULL REQUEST that landed on main.
//
// THE DEFECT, hit live on 2026-08-22 closing loop #500. The bookkeeping test
// was /\b(renumbered|superseded|merged|split)\b/i applied ANYWHERE in the
// outcome text. The outcome said "Merged as PR #469" — a pull request that had
// landed, with the work finished and verified twice — and close-loop refused
// it as continuing work, telling the caller to close it as "dropped" and name a
// successor loop that does not exist.
//
// Two unrelated senses of one word. Since main here is PR-only with automerge,
// every honest close of a code loop names a landed pull request, so the guard
// fired hardest against the most common true completion it would ever see.
// "split" and "superseded" carry the same double meaning (a split file, a
// superseded API endpoint).
//
// Worse, it filtered WORDING rather than substance: rewriting "merged" as
// "landed" walked straight through, which is how that close eventually went in.
// A guard escaped with a synonym enforces nothing, while still being able to
// push a caller into recording finished work as abandoned — which corrupts
// every completion measure built on the resolution field, the exact harm the
// done/dropped split exists to prevent. Recorded as defect 3fe38a2f.
//
// Run with: node --test mcp-server/test/loop-bookkeeping-sense.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { derivedIdempotencyKey } from "../src/derived-key.ts";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude" };

const CLOSING = {
  id: "aaaaaaaa-0000-0000-0000-000000000500",
  kind: "open_loop", number: "500", status: "open",
  marker: "bell", due_on: null, close_outcome: null,
  section: "hot", rel_path: "00_Context/open-loops.md",
};
const SUCCESSOR = {
  id: "bbbbbbbb-0000-0000-0000-000000000213",
  kind: "open_loop", number: "213", status: "open",
  marker: "decision", due_on: null, close_outcome: null,
  section: "backlog", rel_path: "00_Context/open-loops-backlog.md",
};

class Fake {
  constructor() { this.writes = []; }
  async query(text, params) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select version from loop_item")) return { rows: [{ version: 2 }] };
    if (sql.startsWith("select li.id, li.kind, li.number")) {
      if (sql.includes("where li.id = $1"))
        return { rows: [params[0] === SUCCESSOR.id ? SUCCESSOR : CLOSING] };
      if (sql.includes("where li.number = $1"))
        return { rows: [params[0] === SUCCESSOR.number ? SUCCESSOR : CLOSING] };
    }
    this.writes.push({ sql, params });
    if (sql.startsWith("update loop_item")) return { rows: [{ ...CLOSING, version: 3 }] };
    if (sql.startsWith("insert into")) return { rows: [{ id: "event-1" }] };
    return { rows: [] };
  }
}

async function close(outcome, extra = {}) {
  const args = {
    idempotency_key: derivedIdempotencyKey("test", `k-${Math.abs(hash(outcome))}`), loop_id: CLOSING.id,
    kind: "open_loop", base_version: 2, resolution: "done", outcome, ...extra,
  };
  try { return { result: await TOOLS["close-loop"].handler(new Fake(), joe, args), error: null }; }
  catch (e) { return { result: null, error: e instanceof ToolError ? (e.payload ?? e.detail ?? e) : e }; }
}

function hash(s) { let h = 0; for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) | 0; return h; }

// ── the regression itself ───────────────────────────────────────────────
test("a landed pull request closes as done, not as bookkeeping", async () => {
  const { error } = await close(
    "Built, landed and proven the same morning it was filed. Merged as PR #469, "
    + "and the smoke passes live in the canonical tree.");
  assert.equal(error, null,
    "'Merged as PR #469' is a completion, not a loop absorbed into another loop");
});

test("the other double-meaning words are safe mid-sentence too", async () => {
  for (const outcome of [
    "Done. The oversized selftest was split into three files, all green.",
    "Done. The v1 endpoint it depended on is superseded, so the workaround came out.",
    "Done. Renumbered fixtures in the test file to match the new ordering.",
  ]) {
    const { error } = await close(outcome);
    assert.equal(error, null, `must close cleanly: ${outcome}`);
  }
});

// ── what the guard is actually for, still enforced ──────────────────────
test("a loop named as the destination is caught mid-sentence", async () => {
  // The precision this test file gained from a parallel session's branch
  // (claude/close-loop-bookkeeping-precision, 2026-08-21): a bookkeeping close
  // does not have to OPEN with its declaration to be one. What makes it
  // bookkeeping is that it names the loop the work moved to.
  for (const outcome of [
    "Done for now — merged into loop 501, which carries the rest.",
    "Merged with #501 after the two turned out to be one job.",
    "Split into 502 and 503 so each has its own completion test.",
    "Renumbered to #504; the old number still appears in two renders.",
    "Superseded by loop 505, carried forward verbatim.",
  ]) {
    const { error } = await close(outcome);
    assert.equal(error?.error, "bookkeeping_close_is_dropped",
      `naming the destination loop is a bookkeeping close: ${outcome}`);
  }
});

test("the same verbs with no loop named are left alone", async () => {
  // The deliberate narrowing. These name no loop and pass no successor, so they
  // could never have completed a bookkeeping close anyway — the prefix and
  // successor checks below would have refused them later and less clearly.
  for (const outcome of [
    "Done. Now on main as cdaa2be5.",
    "Done — merged to main once the stale base was updated.",
    "Done. The effort was merged with another team's, and theirs shipped.",
  ]) {
    const { error } = await close(outcome);
    assert.equal(error, null, `no loop named, so not a bookkeeping close: ${outcome}`);
  }
});

test("an outcome OPENING with a bookkeeping declaration is still caught", async () => {
  const { error } = await close("Superseded by #213, carried forward verbatim.");
  assert.equal(error?.error, "bookkeeping_close_is_dropped",
    "a self-declared bookkeeping close must not be recorded as done");
});

test("naming a successor loop is itself a bookkeeping signal", async () => {
  const { error } = await close(
    "The work now lives in the consolidated record.", { successor_loop: "213" });
  assert.equal(error?.error, "bookkeeping_close_is_dropped",
    "naming a successor says the work continues, whatever the prose says");
});

test("a bookkeeping close as dropped still demands the declaration and successor", async () => {
  const { error: noSuccessor } = await close(
    "Superseded by the consolidated record.", { resolution: "dropped" });
  assert.equal(noSuccessor?.error, "successor_loop_required",
    "a bookkeeping close cannot read as plain abandonment");

  const { error: badPrefix } = await close(
    "Abandoned; #213 covers it now.", { resolution: "dropped", successor_loop: "213" });
  assert.equal(badPrefix?.error, "bookkeeping_outcome_prefix",
    "the outcome must open by declaring the renumber or supersede");
});

test("a well-formed bookkeeping close still goes through", async () => {
  const { error } = await close(
    "Superseded by #213, not abandoned — carried forward verbatim as Milestone 2.",
    { resolution: "dropped", successor_loop: "213" });
  assert.equal(error, null, "the path this guard exists to shape must stay open");
});
