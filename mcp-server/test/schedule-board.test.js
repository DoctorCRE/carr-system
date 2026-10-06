import test from "node:test";
import assert from "node:assert/strict";

import { scheduleBoardTools } from "../src/schedule-board.js";

const actor = { slug: "joe", human: true };
const now = "2026-09-28T16:00:00.000Z";

test("schedule-board filters to the verified sponsor and marks a missed run and paused job", async () => {
  const calls = [];
  const client = { query: async (sql, args) => {
    calls.push({ sql, args });
    if (sql.includes("ops.service")) return { rows: [
      {
        key: "nightly-record-layer", name: "Nightly record layer", runtime: "launchd",
        owner_actor: "joe", cadence_seconds: 86400, grace_seconds: 3600,
        last_state: "succeeded", last_at: "2026-09-26T07:00:00.000Z",
        last_receipt_ref: "run:nightly-26", last_observed_at: "2026-09-26T07:00:00.000Z",
        scheduler_state: "enabled", schedule_observed_at: "2026-09-28T15:55:00.000Z",
        schedule_receipt_ref: "scheduler:nightly",
      },
      {
        key: "paused-task", name: "Paused task", runtime: "claude-code-scheduled-task",
        owner_actor: "joe", cadence_seconds: 86400, grace_seconds: 3600,
        last_state: "succeeded", last_at: "2026-09-27T07:00:00.000Z",
        last_receipt_ref: "run:paused", last_observed_at: "2026-09-27T07:00:00.000Z",
        scheduler_state: "disabled", schedule_observed_at: "2026-09-28T15:55:00.000Z",
        schedule_receipt_ref: "scheduler:paused",
      },
    ] };
    return { rows: [] };
  } };
  const result = await scheduleBoardTools()["schedule-board"].handler(client, actor, {}, { now: () => now });
  assert.deepEqual(calls.map((call) => call.args), [["joe"], ["joe"]]);
  assert.equal(result.schema, "schedule-board/v1");
  assert.equal(result.jobs[0].state, "missed");
  assert.equal(result.jobs[0].next_due_at, "2026-09-27T07:00:00.000Z");
  assert.equal(result.jobs[0].next_due_basis, "cadence_deadline");
  assert.equal(result.jobs[1].state, "paused");
  assert.equal(result.jobs[1].next_due_at, null);
  assert.deepEqual(result.jobs[0].actions, { pause: false, run: false, stop: false });
  assert.equal(result.sources.find((source) => source.owner === "cron").state, "unknown");
});

test("missing run and scheduler observations stay unknown, never a healthy empty schedule", async () => {
  const client = { query: async (sql) => ({ rows: sql.includes("ops.service") ? [{
    key: "silent-task", name: "Silent task", runtime: "claude-code-scheduled-task",
    owner_actor: "joe", cadence_seconds: 604800, grace_seconds: 0,
    last_state: null, last_at: null, last_receipt_ref: null, last_observed_at: null,
    scheduler_state: null, schedule_observed_at: null, schedule_receipt_ref: null,
  }] : [] }) };
  const result = await scheduleBoardTools()["schedule-board"].handler(client, actor, {}, { now: () => now });
  assert.equal(result.jobs[0].state, "unknown");
  assert.equal(result.jobs[0].next_due_at, null);
  assert.equal(result.sources.find((source) => source.owner === "claude-code").state, "read");
  assert.equal(result.overall_state, "unknown");
});

test("a control-plane result needs a completion receipt, and shared principals cannot read personal schedules", async () => {
  const control = {
    key: "daily-review", version: 1, enabled: true,
    recurrence: { cron: "0 7 * * *", timezone: "America/Chicago" },
    last_state: "succeeded", last_at: "2026-09-28T07:00:00.000Z",
    next_due_at: "2026-09-29T07:00:00.000Z", last_receipt_ref: null,
  };
  const client = { query: async (sql) => ({ rows: sql.includes("ops.service") ? [] : [control] }) };
  const read = scheduleBoardTools()["schedule-board"].handler;
  const unknown = await read(client, actor, {}, { now: () => now });
  assert.equal(unknown.jobs[0].last_run, null);
  assert.equal(unknown.jobs[0].state, "unknown");
  control.last_receipt_ref = "job:daily-review:complete";
  const verified = await read(client, actor, {}, { now: () => now });
  assert.equal(verified.jobs[0].state, "healthy");
  assert.equal(verified.jobs[0].schedule, "Cron: 0 7 * * * · America/Chicago");
  assert.equal(verified.jobs[0].last_run.receipt_ref, control.last_receipt_ref);
  assert.equal(verified.overall_state, "unknown", "unread scheduler owners cannot imply full coverage");
  await assert.rejects(read(client, { slug: "probe", human: false }, {}, { now: () => now }),
    /schedule_board_requires_partner_scope/);
});

test("receipt-backed control-plane failures demand attention even without a queued due time", async () => {
  const control = {
    key: "daily-review", version: 1, enabled: true,
    recurrence: { cron: "0 7 * * *", timezone: "America/Chicago" },
    last_at: "2026-09-28T07:00:00.000Z", next_due_at: null,
    last_receipt_ref: "job:daily-review:failure",
  };
  const client = { query: async (sql) => ({ rows: sql.includes("ops.service") ? [] : [control] }) };
  const read = scheduleBoardTools()["schedule-board"].handler;
  for (const lastState of ["failed", "timed_out", "dead_lettered"]) {
    for (const due of [null, "2026-09-29T07:00:00.000Z", "2026-09-27T07:00:00.000Z"]) {
      control.last_state = lastState;
      control.next_due_at = due;
      const result = await read(client, actor, {}, { now: () => now });
      assert.equal(result.jobs[0].last_run.state, lastState);
      assert.equal(result.jobs[0].state, "failed", `${lastState} with due ${due}`);
      assert.equal(result.jobs[0].freshness, due
        ? Date.parse(due) < Date.parse(now) ? "stale" : "fresh" : "unknown");
      assert.equal(result.overall_state, "attention", `${lastState} with due ${due}`);
    }
  }
  control.last_receipt_ref = null;
  control.next_due_at = null;
  const unverified = await read(client, actor, {}, { now: () => now });
  assert.equal(unverified.jobs[0].last_run, null);
  assert.equal(unverified.jobs[0].state, "unknown");
  assert.equal(unverified.overall_state, "unknown");
});

test("control-plane query reads every terminal job and its matching immutable receipt", async () => {
  const queries = [];
  const client = { query: async (sql) => {
    queries.push(sql);
    return { rows: [] };
  } };
  await scheduleBoardTools()["schedule-board"].handler(client, actor, {}, { now: () => now });
  const controlSql = queries.find((sql) => sql.includes("from ops.job_definition"));
  assert.ok(controlSql);
  const terminalStates = controlSql.match(/mode='live' and state in \(([^)]+)\)/)?.[1]
    .match(/'[^']+'/g)?.map((value) => value.slice(1, -1));
  assert.deepEqual(new Set(terminalStates), new Set([
    "succeeded", "failed", "timed_out", "cancelled", "dead_lettered", "skipped",
  ]));
  const stateKinds = Object.fromEntries([...controlSql.matchAll(
    /last\.state='([^']+)' and kind(?:='([^']+)'| in \(([^)]+)\))/g,
  )].map((match) => [match[1], match[2] ? [match[2]]
    : match[3].match(/'[^']+'/g).map((value) => value.slice(1, -1))]));
  assert.deepEqual(stateKinds, {
    succeeded: ["completion"], failed: ["failure"],
    timed_out: ["timeout", "failure"], cancelled: ["override"],
    dead_lettered: ["dead_letter"], skipped: ["skipped"],
  });
});

test("a newer receipt-backed skip replaces an older success without claiming health", async () => {
  const control = {
    key: "daily-review", version: 1, enabled: true,
    recurrence: { cron: "0 7 * * *", timezone: "America/Chicago" },
    last_state: "skipped", last_at: "2026-09-28T07:00:00.000Z",
    last_receipt_ref: "skipped:daily-review:2",
    next_due_at: "2026-09-29T07:00:00.000Z",
  };
  const client = { query: async (sql) => ({ rows: sql.includes("ops.service") ? [] : [control] }) };
  const result = await scheduleBoardTools()["schedule-board"].handler(client, actor, {}, { now: () => now });
  assert.equal(result.jobs[0].last_run.state, "skipped");
  assert.equal(result.jobs[0].last_run.receipt_ref, "skipped:daily-review:2");
  assert.equal(result.jobs[0].state, "unknown");
  assert.equal(result.overall_state, "unknown");
});
