// conflict-tiering.test.mjs — WR-000019 slice S6, CONFLICT TIERING.
//
// disjointFromIntervening is the pure decision at the heart of versionGuard's
// auto-rebase: given the set of fields THIS call is about to write and the
// intervening events since base_version, is this a trivial version-number
// race (every intervening write touched a DIFFERENT field) or a real
// collision (some intervening write touched a field this call also touches)?
// Isolated from the DB round trip the same way compareVersion is, so the
// branch is provable by `node --test` alone.
//
// The integration path (versionGuard itself, wired through update-deal) is
// covered end to end in dealroom.test.js ("update-deal auto-rebases a
// disjoint-field version race and still surfaces a same-field one").

import { test } from "node:test";
import assert from "node:assert/strict";
import { disjointFromIntervening } from "../src/tools.js";

test("disjoint fields: every intervening event touched a different field — rebase", () => {
  assert.equal(disjointFromIntervening(["notes_path"], [
    { field: "city", actor: "joe", verb: "update-deal" },
  ]), true);
});

test("same field: an intervening event touched a field this call also touches — real conflict", () => {
  assert.equal(disjointFromIntervening(["city"], [
    { field: "notes_path", actor: "dell" },
    { field: "city", actor: "joe" },
  ]), false);
});

test("multiple touched fields: any single collision anywhere blocks the rebase", () => {
  assert.equal(disjointFromIntervening(["phase", "outcome", "closed_on"], [
    { field: "outcome", actor: "dell" },
  ]), false);
  assert.equal(disjointFromIntervening(["phase", "outcome", "closed_on"], [
    { field: "city", actor: "dell" },
    { field: "notes_path", actor: "dell" },
  ]), true);
});

test("no touchedFields declared (the default for every OTHER versionGuard caller) never rebases", () => {
  assert.equal(disjointFromIntervening(null, [{ field: "city", actor: "joe" }]), false);
  assert.equal(disjointFromIntervening(undefined, [{ field: "city", actor: "joe" }]), false);
  assert.equal(disjointFromIntervening([], [{ field: "city", actor: "joe" }]), false);
});

test("no intervening events is not a race at all, and does not rebase (there is nothing to rebase onto)", () => {
  assert.equal(disjointFromIntervening(["city"], []), false);
});

test("an intervening event with no field (e.g. a non-field-scoped event) is treated conservatively as non-colliding", () => {
  // field is null/absent on some event rows (see versionGuard's own event
  // query); such a row carries no evidence of touching any specific column,
  // so it cannot itself block a rebase — but it also cannot manufacture one
  // where no touchedFields were declared (covered above).
  assert.equal(disjointFromIntervening(["city"], [{ field: null, actor: "system", verb: "system-sweep" }]), true);
});

test("case sensitivity is exact — a field name must match verbatim, never fuzzily", () => {
  assert.equal(disjointFromIntervening(["City"], [{ field: "city", actor: "joe" }]), true,
    "different case is a different string — this is a deliberately strict, not a lenient, comparison");
});
