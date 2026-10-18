// Queue projection contracts.  The browser receives only projector receipts;
// this model must never infer a live board from an old or malformed payload.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  QUEUE_COLUMNS, queueColumnFor, queueIsStale, queueProjection,
} from "../../dealroom/js/queue-model.mjs";

const NOW = Date.parse("2026-08-24T18:00:00Z");
const card = (id, status, updated_at = "2026-08-24T17:59:45Z") => ({
  task_id: id, title: `<unsafe ${id}>`, target: "sol",
  effective_model: "gpt-5.6-sol", status, priority: "P2", cap: "read", updated_at,
});

test("canonical statuses map exactly once into the six Queue columns", () => {
  assert.deepEqual(QUEUE_COLUMNS.map((column) => column.id),
    ["backlog", "ready", "running", "review", "blocked", "done"]);
  for (const status of ["triage", "todo"]) assert.equal(queueColumnFor(status), "backlog");
  for (const status of ["ready", "scheduled"]) assert.equal(queueColumnFor(status), "ready");
  assert.equal(queueColumnFor("running"), "running");
  assert.equal(queueColumnFor("review"), "review");
  assert.equal(queueColumnFor("blocked"), "blocked");
  assert.equal(queueColumnFor("done"), "done");
  assert.equal(queueColumnFor("invented"), null, "unknown states are withheld, never guessed");
});

test("latest receipt wins by task ID, so a status transition moves one card instead of duplicating it", () => {
  const projection = queueProjection([
    { event_id: "e1", task_id: "t_one", summary: "<summary one>", card: card("t_one", "todo", "2026-08-24T17:58:00Z") },
    { event_id: "e2", task_id: "t_one", summary: "<summary one>", card: card("t_one", "running", "2026-08-24T17:59:00Z") },
    { event_id: "e3", task_id: "t_two", summary: "<summary two>", card: card("t_two", "review") },
  ]);
  assert.equal(projection.cards.length, 2);
  assert.equal(projection.byColumn.running[0].task_id, "t_one");
  assert.equal(projection.byColumn.backlog.length, 0);
  assert.equal(projection.byColumn.review[0].task_id, "t_two");
});

test("missing, malformed, or aged projection is visibly not live", () => {
  assert.equal(queueIsStale(null, NOW), true);
  assert.equal(queueIsStale("not-a-date", NOW), true);
  assert.equal(queueIsStale("2026-08-24T17:57:59Z", NOW), true);
  assert.equal(queueIsStale("2026-08-24T17:59:59Z", NOW), false);
});

test("Queue rendering stays text-only and reduced motion keeps cards in the document flow", async () => {
  const source = await readFile(new URL("../../dealroom/js/queue.js", import.meta.url), "utf8");
  const css = await readFile(new URL("../../dealroom/css/queue.css", import.meta.url), "utf8");
  assert.match(source, /textContent/, "untrusted card fields must use text nodes");
  assert.doesNotMatch(source, /innerHTML\s*=/, "no user-controlled string enters HTML parsing");
  assert.match(source, /data-task-id/, "reconciliation is keyed by canonical task id");
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.doesNotMatch(css, /prefers-reduced-motion: reduce[\s\S]{0,1000}display:\s*none/);
});


test("canonical receipt fields take precedence over nested card metadata", () => {
  const projection = queueProjection([{
    task_id: "t_canonical",
    summary: "Canonical summary",
    card: { ...card("t_other", "ready"), summary: null },
  }]);
  assert.equal(projection.cards.length, 1);
  assert.equal(projection.cards[0].task_id, "t_canonical");
  assert.equal(projection.cards[0].summary, "Canonical summary");
  assert.equal(projection.byColumn.ready[0].task_id, "t_canonical");
});
