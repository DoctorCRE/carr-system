import assert from "node:assert/strict";
import test from "node:test";
import { migrationNames, readMigration } from "./migration-source.testhelper.ts";
import * as doc from "./models/doc-request/view.ts";
import * as bulk from "./models/home-inbox-bulk/view.ts";
import * as inbox from "./models/home-inbox-settlement/view.ts";
import * as people from "./models/people-directory/Chart.map.ts";
import * as watch from "./models/subject-watch-events/view.ts";

test("document labels retain every phase, answer and access combination", () => {
  for (const [phase, label] of Object.entries({
    absent: "No new ask",
    pending: "Ask saving",
    accepted: "Ask accepted",
  })) {
    for (const [answer, answerLabel] of Object.entries({
      idle: "answer not yet saved",
      pending: "answer saving",
      committed: "answer saved",
    })) {
      for (const authorized of [false, true]) {
        assert.equal(
          doc.state({ phase, answer, authorized }),
          `${label}, access ${authorized ? "active" : "revoked"}, ${answerLabel}`,
        );
      }
    }
  }
  assert.equal(doc.step("AnswerRollback"), "Answer transaction fails");
  assert.equal(doc.step("Unknown"), undefined);
});

test("bulk and settlement labels retain undo, precedence and hidden idle steps", () => {
  assert.equal(bulk.state({ phase: "undone", current: { 1: "working" } }), "Undone to working");
  assert.equal(bulk.state({ phase: "applied", current: { 1: "snoozed" } }), "snoozed");
  assert.equal(bulk.step("Stay"), null);
  assert.equal(bulk.step("Unknown"), undefined);
  for (const [value, expected] of [
    [{ disposition: "snoozed", override: true, source: "done" }, "Snoozed"],
    [{ disposition: "set-aside", override: true, source: "done" }, "Settled manually"],
    [{ disposition: "inbox", override: true, source: "done" }, "Explicitly returned to Inbox"],
    [{ disposition: "inbox", override: false, source: "done" }, "Auto-settled"],
    [{ disposition: "inbox", override: false, source: "working" }, "Working"],
    [{ disposition: "inbox", override: false, source: "open" }, "Open"],
  ] satisfies [{ disposition: string; override: boolean; source: string }, string][]) {
    assert.equal(inbox.state(value), expected);
  }
  assert.equal(inbox.step("Stay"), null);
  assert.equal(inbox.step("Unknown"), undefined);
});

test("directory and watch views retain action fallbacks and state precedence", () => {
  assert.deepEqual(people.regions, ["main"]);
  assert.deepEqual(people.readerActions, []);
  assert.equal(people.state({ main: "Ready" }), "Ready");
  assert.equal(people.step("SearchNameOrLoadActors"), "Search name or load actors");
  assert.equal(people.step("Unknown"), "Unknown");
  assert.equal(watch.state({ subscribed: false, delivered: [1] }), "Unsubscribed");
  assert.equal(watch.state({ subscribed: true, delivered: [1] }), "Notified");
  assert.equal(watch.state({ subscribed: true, delivered: [] }), "Quiet");
  assert.equal(watch.step("Consume"), "consume source");
  assert.equal(watch.step("Unknown"), "Unknown");
});

test("migration reader returns the same source for names, paths and URLs", () => {
  const name = migrationNames()[0];
  assert.ok(name);
  const source = readMigration(name);
  assert.ok(source.length > 0);
  assert.equal(readMigration(`/unused/${name}`), source);
  assert.equal(readMigration(new URL(`file:///unused/${name}`)), source);
});
