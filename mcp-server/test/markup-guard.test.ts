// markup-guard.test.ts — the write door refuses tool-call markup in a field value.
//
// WHY. ops/store-markup-scan.py has been finding this damage AFTER the fact for
// weeks: a session composes several long fields as one block of text, the field
// swallows its own closing tag, and every parameter after that tag is written
// NULL. Six active shared rules were found carrying it on 2026-08-13, the oldest
// four days old, five with a partner's verbatim quote absorbed into the rule
// statement. On 2026-08-14 a session filing a loop ABOUT this defect reproduced
// it three times in a row while trying to describe it.
//
// The scan is a detector, and a detector cannot un-write a NULL. The record
// layer refuses to edit a closed row on purpose ("a closed loop is history"), so
// damage that reaches a row and is then closed is permanent. The only place this
// can actually be stopped is the write door, before the row exists.
//
// THE HARD PART, and the reason this is a test file and not a one-line regex:
// rows that legitimately WRITE ABOUT this defect contain the same strings — this
// very file, the rule that documents the defect, the loops that tracked the
// cleanup. A guard that cannot tell those apart blocks the system from
// describing its own bug. The signature of real corruption is STRUCTURAL: the
// field swallowed its own closing tag, or carries a bare marker that ate the
// field which should have followed. A mention keeps the markers quoted in
// backticks as prose. Same rule as ops/store-markup-scan.py's classify(), which
// has run against the live store for a week without a false positive.

import { strict as assert } from "node:assert";
import test from "node:test";
import { looksLikeToolCallMarkup } from "../src/input-validation.ts";

test("the exact 2026-08-14 corruption is refused", () => {
  // What actually landed: source_note swallowed </source_note> and ate the
  // whole unblocks field after it, which was then stored NULL.
  const v =
    "Reported by the peer session; counts verified independently." +
    '</source_note>\n<parameter name="unblocks">Makes it safe to push again.';
  assert.equal(looksLikeToolCallMarkup("source_note", v), true);
});

test("a field that swallowed its own closing tag is refused", () => {
  assert.equal(looksLikeToolCallMarkup("body", "the text</body>"), true);
  assert.equal(looksLikeToolCallMarkup("outcome", "done</outcome>"), true);
  // close_outcome's own closer is </outcome> — the scan strips the close_ prefix
  // and the guard must agree, or the two disagree about the same row.
  assert.equal(looksLikeToolCallMarkup("close_outcome", "done</outcome>"), true);
});

test("a bare parameter marker is refused wherever it appears", () => {
  assert.equal(looksLikeToolCallMarkup("body", 'text <parameter name="x">more'), true);
  assert.equal(looksLikeToolCallMarkup("title", '<invoke name="add-loop">'), true);
});

test("PROSE ABOUT the defect is allowed — the guard must not gag the system", () => {
  // Quoted in backticks: discussion, not damage. Every one of these is a real
  // shape from the store's own rules and loops.
  assert.equal(
    looksLikeToolCallMarkup("body", "A field swallowed its own `</parameter>` tag and the rest was NULL."),
    false,
  );
  assert.equal(
    looksLikeToolCallMarkup("body", "Markers to scan for: `<parameter`, `</parameter`, `<invoke`, `</invoke`."),
    false,
  );
  assert.equal(looksLikeToolCallMarkup("close_outcome", "Fixed the `<parameter name=` leak in the loop body."), false);
});

test("ordinary text is untouched", () => {
  assert.equal(looksLikeToolCallMarkup("body", "Landlord countered at $28/sf NNN."), false);
  assert.equal(looksLikeToolCallMarkup("body", ""), false);
  assert.equal(looksLikeToolCallMarkup("body", null), false);
  assert.equal(looksLikeToolCallMarkup("body", 42), false);
  // Angle brackets that are not tool-call markup: HTML in a note, a comparison.
  assert.equal(looksLikeToolCallMarkup("body", "rent < $30/sf and term > 5 years"), false);
  assert.equal(looksLikeToolCallMarkup("body", "<p>rendered note</p>"), false);
});

test("a backtick on a LATER line does not count as quoting", () => {
  // The scan requires the quoting backticks to sit on one line, so a stray
  // backtick further down the field cannot launder a real leak into a mention.
  const v = 'text <parameter name="unblocks">swallowed\n\nlater line with a ` in it';
  assert.equal(looksLikeToolCallMarkup("body", v), true);
});
