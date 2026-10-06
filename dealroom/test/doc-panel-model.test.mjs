import test from "node:test";
import assert from "node:assert/strict";
import {
  docPanelModality, escapeShouldClose, parseDocInput, formatReceiptLine, retryContextDriftNote,
} from "../js/doc-panel-model.js";

test("docPanelModality is closed unless open, then follows the same phone-width rule as the record panel", () => {
  assert.equal(docPanelModality({ open: false, phoneWidth: false }), "closed");
  assert.equal(docPanelModality({ open: false, phoneWidth: true }), "closed");
  assert.equal(docPanelModality({ open: true, phoneWidth: false }), "inline");
  assert.equal(docPanelModality({ open: true, phoneWidth: true }), "modal");
});

test("Escape closes an unpinned panel and never a pinned one", () => {
  assert.equal(escapeShouldClose({ pinned: false }), true);
  assert.equal(escapeShouldClose({ pinned: true }), false);
});

test("a leading /command splits into command + text; anything else is a plain note", () => {
  assert.deepEqual(parseDocInput("/set_next_step send the LOI tomorrow"), {
    command: "set_next_step", text: "send the LOI tomorrow",
  });
  assert.deepEqual(parseDocInput("/ADD_NOTE called the landlord"), {
    command: "add_note", text: "called the landlord",
  });
  assert.deepEqual(parseDocInput("called the landlord"), {
    command: "add_note", text: "called the landlord",
  });
  assert.deepEqual(parseDocInput("   "), { command: "add_note", text: "" });
});

test("formatReceiptLine gives every status its own distinct, honest copy", () => {
  const base = { command: "set_next_step", deal: "d1" };
  const ok = formatReceiptLine({ ...base, status: "ok", detail: {} });
  const conflict = formatReceiptLine({ ...base, status: "conflict", detail: {} });
  const unavailable = formatReceiptLine({ ...base, status: "unavailable", detail: { reason: "V5-J102 not deployed yet." } });
  const refused = formatReceiptLine({ ...base, status: "refused", detail: { reason: "Say what happens next." } });
  const lines = [ok, conflict, unavailable, refused].map((l) => l.text);
  assert.equal(new Set(lines).size, 4, "each status must render distinguishable text");
  assert.match(ok.text, /confirmed/);
  assert.match(conflict.text, /conflict/i);
  assert.match(unavailable.text, /V5-J102 not deployed yet\./);
  assert.match(refused.text, /Say what happens next\./);
  assert.equal(ok.tone, "ok");
  assert.equal(conflict.tone, "conflict");
  assert.equal(unavailable.tone, "unavailable");
  assert.equal(refused.tone, "refused");
});

test("retryContextDriftNote is silent when the captured deal is still the open one", () => {
  const captured = { dealId: "d1", label: "Acme HQ" };
  assert.equal(retryContextDriftNote(captured, { dealId: "d1" }), "");
  assert.equal(retryContextDriftNote(captured, captured), "");
});

test("retryContextDriftNote is silent when the captured attempt had no deal at all (e.g. add_note with nothing open)", () => {
  assert.equal(retryContextDriftNote({ dealId: null }, { dealId: "d2" }), "");
  assert.equal(retryContextDriftNote({}, { dealId: "d2" }), "");
  assert.equal(retryContextDriftNote(null, { dealId: "d2" }), "");
});

test("retryContextDriftNote names the captured deal honestly once it is no longer the open one", () => {
  const captured = { dealId: "d1", label: "Acme HQ" };
  const note = retryContextDriftNote(captured, { dealId: "d2" });
  assert.match(note, /Acme HQ/, "must name the deal it actually ran against, not the currently-open one");
  assert.match(note, /no longer the open record/i);
});

test("retryContextDriftNote falls back to the raw dealId when no friendly label was captured", () => {
  const note = retryContextDriftNote({ dealId: "deal-77" }, { dealId: null });
  assert.match(note, /deal-77/);
});

test("retryContextDriftNote treats 'nothing currently open' as drift too, when the captured attempt had a real deal", () => {
  const note = retryContextDriftNote({ dealId: "d1", label: "Acme HQ" }, {});
  assert.notEqual(note, "", "a captured deal that is no longer open at all must still be flagged, not silently treated as still current");
});
