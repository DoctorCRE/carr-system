
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
  assert.equal(disjointFromIntervening(["city"], [{ field: null, actor: "system", verb: "system-sweep" }]), true);
});

test("case sensitivity is exact — a field name must match verbatim, never fuzzily", () => {
  assert.equal(disjointFromIntervening(["City"], [{ field: "city", actor: "joe" }]), true,
    "different case is a different string — this is a deliberately strict, not a lenient, comparison");
});
