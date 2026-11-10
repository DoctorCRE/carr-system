import assert from "node:assert/strict";
import test from "node:test";
import { state, step, title } from "../../workspace/statecharts/wr586-s3-deliverables/view.ts";

test("deliverables title and revoked sessions take precedence over every phase", () => {
  assert.equal(title, "Deal deliverables");
  for (const phase of ["absent", "started", "uploading", "expired", "verified", "finalized"]) {
    for (const shared of [false, true]) {
      for (const downloaded of [false, true]) {
        assert.equal(state({ authorized: false, phase, shared, downloaded }), "Session revoked");
      }
    }
  }
});

test("unfinished deliverables show their phase without visibility or download labels", () => {
  for (const phase of ["absent", "started", "uploading", "expired", "verified", "future-phase", ""]) {
    for (const shared of [false, true]) {
      for (const downloaded of [false, true]) {
        assert.equal(state({ authorized: true, phase, shared, downloaded }), phase);
      }
    }
  }
});

test("finalized deliverables distinguish private shared and downloaded labels", () => {
  for (const [shared, downloaded, expected] of [
    [false, false, "Private"],
    [false, true, "Private, downloaded"],
    [true, false, "Shared"],
    [true, true, "Shared, downloaded"],
  ] as const) {
    assert.equal(state({ authorized: true, phase: "finalized", shared, downloaded }), expected);
  }
});

test("all deliverables actions keep their words including Refuse and unknown fallbacks", () => {
  for (const [action, expected] of [
    ["Start", "Partner starts upload"],
    ["Claim", "Partner sends bytes"],
    ["Abort", "Rejected bytes roll back reservation"],
    ["Expire", "Unused reservation expires"],
    ["Verify", "Bytes, size, content and hash verified"],
    ["Finalize", "Partner finalizes privately"],
    ["Share", "Partner changes visibility with current version"],
    ["Download", "Partner downloads"],
    ["Revoke", "Session revoked"],
    ["Refuse", "Refuse"],
    ["Unknown", "Unknown"],
    ["", ""],
  ] as const) {
    assert.equal(step(action), expected);
  }
});
