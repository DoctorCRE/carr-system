import test from "node:test";
import assert from "node:assert/strict";
import { claimInert, releaseInert, isClaimedByOther } from "../js/inert-registry.js";

// A minimal stand-in for a DOM element: just the inert property and the
// aria-hidden attribute surface the registry touches. No jsdom needed — the
// registry has no other DOM dependency.
function fakeRegion({ inert = false, ariaHidden = null } = {}) {
  const attrs = new Map();
  if (ariaHidden !== null) attrs.set("aria-hidden", ariaHidden);
  return {
    inert,
    getAttribute(name) { return attrs.has(name) ? attrs.get(name) : null; },
    setAttribute(name, value) { attrs.set(name, value); },
    removeAttribute(name) { attrs.delete(name); },
  };
}

test("a single owner's claim makes the region inert, and its release restores the pre-claim state exactly", () => {
  const region = fakeRegion({ inert: false, ariaHidden: null });
  claimInert(region, "a");
  assert.equal(region.inert, true);
  assert.equal(region.getAttribute("aria-hidden"), "true");
  releaseInert(region, "a");
  assert.equal(region.inert, false);
  assert.equal(region.getAttribute("aria-hidden"), null);
});

test("a region already inert before either owner touches it is captured and restored as the pre-claim baseline, not forced false", () => {
  const region = fakeRegion({ inert: true, ariaHidden: "true" });
  claimInert(region, "a");
  releaseInert(region, "a");
  assert.equal(region.inert, true, "must restore the TRUE baseline it actually had, never hardcode false");
});

test("two owners claiming the same region: the FIRST owner's release does not un-inert it while the second still holds a claim", () => {
  const region = fakeRegion();
  claimInert(region, "record-panel");
  claimInert(region, "doc");
  releaseInert(region, "record-panel");
  assert.equal(region.inert, true, "the record panel releasing first must not undo Doc's still-active claim");
  assert.equal(isClaimedByOther(region, "record-panel"), true);
  releaseInert(region, "doc");
  assert.equal(region.inert, false, "only once BOTH owners have released does the region return to its pre-claim state");
});

test("reverse order: the SECOND owner (doc) claiming and releasing before the first (record-panel) releases does not undo the first owner's claim", () => {
  // This is the round-3 mirror case the shared registry exists to close: a
  // snapshot/restore-per-file approach has Doc remember "false" as its own
  // pre-claim baseline and force that back on release, discarding the record
  // panel's still-active claim made in between. Reference counting cannot
  // make this mistake because there is only ONE baseline, captured once.
  const region = fakeRegion({ inert: false, ariaHidden: null });
  claimInert(region, "doc");
  claimInert(region, "record-panel");
  releaseInert(region, "doc");
  assert.equal(region.inert, true, "the record panel's still-active claim must survive Doc's release");
  assert.equal(isClaimedByOther(region, "doc"), true);
  releaseInert(region, "record-panel");
  assert.equal(region.inert, false);
});

test("releasing an owner id that never claimed the region is a no-op", () => {
  const region = fakeRegion({ inert: false });
  releaseInert(region, "nobody");
  assert.equal(region.inert, false);
});

test("isClaimedByOther is false when only the asking owner (or nobody) holds the claim", () => {
  const region = fakeRegion();
  assert.equal(isClaimedByOther(region, "doc"), false);
  claimInert(region, "doc");
  assert.equal(isClaimedByOther(region, "doc"), false, "doc is not 'other' to itself");
  assert.equal(isClaimedByOther(region, "record-panel"), true);
});

test("a repeated claim by the same owner is idempotent and does not require a matching number of releases", () => {
  const region = fakeRegion();
  claimInert(region, "doc");
  claimInert(region, "doc");
  claimInert(region, "doc");
  releaseInert(region, "doc");
  assert.equal(region.inert, false, "one owner's repeated claim is still ONE claim, not three");
});
