// document-outbound-format.test.mjs — the gate for Joe's split on what format
// an outbound document leaves in.
//
// HIS WORDS, and the correction is the interesting half: "the LOI is sent over
// to the listing agent in word format so they can easily edit or revise. i know
// i said everything goes out pdf but thats really just spreadsheets that go out
// pdf so noone can see the formulas"
//
// SO THERE ARE TWO RULES, not one:
//   · an LOI or letter goes out in WORD, because the listing agent editing it
//     IS the negotiation workflow. A PDF makes them retype it.
//   · a SPREADSHEET goes out as PDF, so nobody can read our formulas.
// The older blanket "everything goes out PDF" is superseded and would, applied
// to an LOI, break the negotiation it exists to start.
//
// WHERE IT BINDS. `update-document-status` is the only place a human states a
// document was sent. Both files already exist by then — output_kinds {working,
// pdf} names ROLES, not formats, and the working file's extension is the
// template's own — so the question this gate asks is not "was the file made"
// but "which one did we hand over".
//
// WHY IT IS OVERRIDABLE. Joe's format rule ends with "unless the partner says
// otherwise", and a real send can legitimately go the other way — a listing
// agent who asks for PDF, a spreadsheet a client needs live. The gate takes
// `format_exception` for that, the same shape as confirm-merge's
// same_person_because, and records it, so the exception is a decision on the
// record rather than a silent inconsistency.
//
// Run with: node --test mcp-server/test/document-outbound-format.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  doc: "40000000-0000-0000-0000-000000000001",
  deal: "30000000-0000-0000-0000-000000000001",
  workingFile: "60000000-0000-0000-0000-00000000000w".replace("w", "1"),
  pdfFile: "60000000-0000-0000-0000-000000000002",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true, kind: "human",
  via: "mcp", client_id: "claude" };
const robot = { ...joe, human: false, kind: "agent" };

const detail = (e) => JSON.stringify(e.payload ?? e.body ?? e.message ?? e);

// row: what the document already carries before this call
class Fake {
  constructor({ kind = "docx", working = null, pdf = null, status = "handed_to_joe" } = {}) {
    this.kind = kind;
    this.row = { id: ids.doc, deal_id: ids.deal, sent_status: status,
                 working_attachment: working, pdf_attachment: pdf };
    this.updates = [];
    this.events = [];
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select * from document where id=")) return { rows: [this.row] };
    // the gate's own lookup: what kind of template is this document built from?
    if (sql.includes("outbound_template_kind")) return { rows: [{ template_kind: this.kind }] };
    if (sql.startsWith("update document set")) { this.updates.push(sql); return { rows: [] }; }
    if (sql.startsWith("insert into event")) { this.events.push(params); return { rows: [] }; }
    return { rows: [] };
  }
}

const verb = TOOLS["update-document-status"];
const mark = (fake, args = {}, actor = joe) => verb.handler(fake, actor, {
  idempotency_key: crypto.randomUUID(),
  document_id: ids.doc, status: "sent", ...args,
});

// ── an LOI or letter: WORD goes out ────────────────────────────────────────
test("an LOI marked sent with only a PDF on file is refused", async () => {
  const fake = new Fake({ kind: "docx", pdf: ids.pdfFile });
  await assert.rejects(() => mark(fake), (e) => {
    assert.match(detail(e), /outbound_format/, "the refusal names the shape");
    return true;
  });
  assert.deepEqual(fake.updates, [], "nothing is written when it refuses");
});

test("the refusal explains the workflow, not just the rule", async () => {
  const fake = new Fake({ kind: "docx", pdf: ids.pdfFile });
  await assert.rejects(() => mark(fake), (e) => {
    const body = detail(e).toLowerCase();
    assert.match(body, /edit|revise/,
      "it says WHY word — the listing agent editing it is the negotiation");
    assert.match(body, /format_exception/, "and it names the way through");
    return true;
  });
});

test("an LOI sent with the Word file recorded goes through", async () => {
  const fake = new Fake({ kind: "docx", working: ids.workingFile, pdf: ids.pdfFile });
  const out = await mark(fake);
  assert.equal(out.ok, true);
});

test("the Word file may arrive in the SAME call that marks it sent", async () => {
  const fake = new Fake({ kind: "docx" });
  const out = await mark(fake, { working_attachment: ids.workingFile });
  assert.equal(out.ok, true,
    "attachments and status often land together; the gate reads the merged state, not just the row");
});

// ── a spreadsheet: PDF goes out, so the formulas stay ours ──────────────────
test("a spreadsheet marked sent with no PDF is refused", async () => {
  const fake = new Fake({ kind: "xlsx", working: ids.workingFile });
  await assert.rejects(() => mark(fake), (e) => {
    assert.match(detail(e).toLowerCase(), /formula/,
      "it says why: a live spreadsheet shows our formulas");
    return true;
  });
});

test("a spreadsheet sent as PDF goes through", async () => {
  const fake = new Fake({ kind: "xlsx", working: ids.workingFile, pdf: ids.pdfFile });
  const out = await mark(fake);
  assert.equal(out.ok, true);
});

// ── the exception is a decision, not a bypass ───────────────────────────────
test("a stated exception opens the gate", async () => {
  const fake = new Fake({ kind: "docx", pdf: ids.pdfFile });
  const out = await mark(fake, {
    format_exception: "The listing agent asked for PDF only; their system rejects .docx attachments.",
  });
  assert.equal(out.ok, true, "Joe's rule ends with 'unless the partner says otherwise'");
});

test("the exception is recorded, so it is a decision on the record", async () => {
  const fake = new Fake({ kind: "docx", pdf: ids.pdfFile });
  await mark(fake, { format_exception: "listing agent asked for PDF only, their system blocks docx" });
  assert.match(JSON.stringify(fake.events), /listing agent asked for PDF only/,
    "an exception nobody can find later is indistinguishable from an inconsistency");
});

test("a throwaway exception does not open the gate", async () => {
  for (const excuse of ["", "  ", "ok", "n/a", "fine"]) {
    const fake = new Fake({ kind: "docx", pdf: ids.pdfFile });
    await assert.rejects(() => mark(fake, { format_exception: excuse }),
      `"${excuse}" should not clear a format rule`);
  }
});

// ── scope ──────────────────────────────────────────────────────────────────
test("draft and handed_to_joe are not gated — only the send is", async () => {
  for (const status of ["draft", "handed_to_joe"]) {
    const fake = new Fake({ kind: "docx" });
    const out = await mark(fake, { status });
    assert.equal(out.ok, true, `${status} is work in progress, not an outbound act`);
  }
});

test("a status-free update (recording lint results) is not gated", async () => {
  const fake = new Fake({ kind: "docx" });
  const out = await verb.handler(fake, joe, {
    idempotency_key: "cc76bb31-6edb-5937-ad53-48a937f84265", document_id: ids.doc, lint_passed: true,
  });
  assert.equal(out.ok, true, "this verb is also where lint and leak-check results land");
});

test("the human-only rule on 'sent' still fires, and before the format gate", async () => {
  const fake = new Fake({ kind: "docx", pdf: ids.pdfFile });
  await assert.rejects(() => mark(fake, {}, robot), (e) => {
    assert.match(detail(e), /human_only/,
      "automation claiming a send is the older, larger problem and must surface first");
    return true;
  });
});

test("an unknown template kind does not invent a rule", async () => {
  const fake = new Fake({ kind: null, pdf: ids.pdfFile });
  const out = await mark(fake);
  assert.equal(out.ok, true,
    "no negative finding from a collection that did not answer — a kind we cannot read is not a violation");
});
