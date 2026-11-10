import assert from "node:assert/strict";
import test from "node:test";
import * as watch from "./models/subject-watch-v2/Watch.map.ts";
import * as inbox from "./models/watch-alert-consumption/WatchInbox.map.ts";

test("subject watch map preserves chart regions, labels and unknown actions", () => {
  assert.deepEqual(watch.regions, ["lifecycle", "subscription"]);
  assert.deepEqual(watch.readerActions, []);
  assert.equal(watch.state({ lifecycle: "Watching", subscription: "Subscribed" }), "Watching | Subscribed");
  for (const [action, label] of Object.entries({
    Watch: "Watch",
    RepeatWatch: "Repeat watch",
    Pause: "Pause",
    Resume: "Resume",
    ReplacePolicy: "Replace policy",
    ReplacePausedPolicy: "Replace paused policy",
    Remove: "Remove",
    Unknown: "Unknown",
  })) {
    assert.equal(watch.step(action), label);
  }
});

test("watch inbox map preserves work state across every alert action", () => {
  assert.deepEqual(inbox.regions, ["notificationVisibility", "homePlacement", "canonicalWatchSubject"]);
  assert.deepEqual(inbox.readerActions, ["Acknowledge", "Settle", "Unsettle", "Snooze"]);
  for (const notificationVisibility of ["Unread", "Read"])
    for (const homePlacement of ["Inbox", "Settled", "Snoozed"]) {
      assert.equal(
        inbox.state({ notificationVisibility, homePlacement }),
        `${notificationVisibility} alert | ${homePlacement} card | Work still open`,
      );
    }
  for (const [action, label] of Object.entries({
    Acknowledge: "Read the alert",
    Settle: "Settle the card",
    Unsettle: "Return the card to Inbox",
    Snooze: "Snooze the card",
    Unknown: "Unknown",
  })) {
    assert.equal(inbox.step(action), label);
  }
});
