// Characterize the unread-triage model's reader-facing projection.
import assert from "node:assert/strict";
import test from "node:test";
import { readerActions, regions, state, step } from "../../ops/fixtures/outlook-triage-unread/UnreadFix.map.mts";

test("unread reader keeps region order and empty reader actions", () => {
  assert.deepEqual(regions, ["coverage", "mail"]);
  assert.deepEqual(readerActions, []);
  assert.equal(state({ mail: "Waiting", coverage: "Verified" }), "Verified | Waiting");
  assert.equal(state({ coverage: "Changed" }), "Changed | ");
});

test("unread reader translates model actions and preserves unknown actions", () => {
  for (const [action, label] of Object.entries({
    CoverageDeadlineExpires: "Coverage budget expires",
    StableCompleteScansMatchTheSelectedInboxBadge: "Unread coverage matches the selected Inbox badge",
    UnreadMailIsUnchanged: "Unread mail is unchanged",
    MailArrivesOrUnreadStatusChanges: "Mail arrives or unread status changes",
    CoverageCannotBeVerified: "Coverage cannot be verified",
    ReadMailHasBeenFiled: "Read mail has been filed",
    UnreadMailChangedDuringFiling: "Unread mail changed during filing",
  })) {
    assert.equal(step(action), label);
  }
  assert.equal(step("FutureModelAction"), "FutureModelAction");
  assert.equal(step(""), "");
});
