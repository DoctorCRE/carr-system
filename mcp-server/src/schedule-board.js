// Versioned, read-only Operations schedule projection. The database ledgers
// establish job identity and receipts; a recent native observation establishes
// whether a legacy scheduler is enabled. Cadence deadlines are explicitly
// labelled estimates, never represented as a provider's exact next fire.
import { personalScopeForActor } from "./identity.js";
import { ToolError } from "./tool-error.js";

const SERVICE_SQL = `
  select s.key, s.name, s.runtime, s.owner_actor,
         e.expected_cadence_seconds as cadence_seconds,
         e.cadence_grace_seconds as grace_seconds,
         r.state as last_state, r.ended_at as last_at,
         coalesce(r.evidence_ref, 'run:' || r.id::text) as last_receipt_ref,
         r.observed_at as last_observed_at,
         active.state as active_state,
         o.scheduler_state, o.observed_at as schedule_observed_at,
         o.receipt_ref as schedule_receipt_ref
    from ops.service s
    join ops.service_environment e on e.service_id=s.id
     and e.environment='production' and e.expected_cadence_seconds is not null
    left join lateral (
      select id,state,ended_at,evidence_ref,observed_at
        from ops.run where service_id=s.id and environment='production'
         and kind='job' and state in ('succeeded','failed','timed_out','cancelled','skipped')
       order by ended_at desc,id desc limit 1
    ) r on true
    left join lateral (
      select state from ops.run where service_id=s.id and environment='production'
        and kind='job' and state='running'
       order by observed_at desc,id desc limit 1
    ) active on true
    left join lateral (
      select scheduler_state,observed_at,receipt_ref
        from ops.legacy_schedule_observation_receipt
       where workflow_key=s.key
       order by observed_at desc,id desc limit 1
    ) o on true
   where s.retired_at is null
     and s.runtime in ('launchd','claude-code-scheduled-task','cron')
     and s.owner_actor=$1
   order by s.runtime,s.name,s.key`;

const CONTROL_SQL = `
  select d.key, d.version, d.enabled, d.owner_actor, d.recurrence,
         last.state as last_state, last.ended_at as last_at,
         last.scheduled_for as last_scheduled_for,
         receipt.receipt_ref as last_receipt_ref,
         due.next_due_at, active.state as active_state
    from ops.job_definition d
    left join lateral (
      select id,state,ended_at,scheduled_for,attempt
        from ops.job where definition_key=d.key and definition_version=d.version
         and mode='live' and state in ('succeeded','failed','timed_out','cancelled','dead_lettered','skipped')
       order by ended_at desc,id desc limit 1
    ) last on true
    left join lateral (
      select receipt_ref from ops.job_receipt
       where job_id=last.id and attempt=last.attempt
         and ((last.state='succeeded' and kind='completion')
           or (last.state='failed' and kind='failure')
           or (last.state='timed_out' and kind in ('timeout','failure'))
           or (last.state='cancelled' and kind='override')
           or (last.state='dead_lettered' and kind='dead_letter')
           or (last.state='skipped' and kind='skipped'))
       order by created_at desc,id desc limit 1
    ) receipt on true
    left join lateral (
      select case when state='retry_wait' then next_attempt_at else scheduled_for end as next_due_at
        from ops.job
       where definition_key=d.key and definition_version=d.version
         and mode='live' and state in ('queued','retry_wait','running')
       order by case when state='retry_wait' then next_attempt_at else scheduled_for end,id limit 1
    ) due on true
    left join lateral (
      select state from ops.job
       where definition_key=d.key and definition_version=d.version
         and mode='live' and state='running'
       order by started_at desc,id desc limit 1
    ) active on true
   where d.owner_actor in ($1,'system')
     and coalesce(d.recurrence->>'kind','') <> 'on_demand'
     and d.version=(select max(version) from ops.job_definition where key=d.key)
   order by d.key`;

const iso = (value) => {
  if (!value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
};
const actions = () => ({ pause: false, run: false, stop: false });
const CONTROL_FAILURE_STATES = new Set(["failed", "timed_out", "dead_lettered"]);
const validSeconds = (value) => Number.isInteger(Number(value)) && Number(value) > 0;
const recurrenceLabel = (value) => {
  const cron = typeof value?.cron === "string" ? value.cron.trim() : "";
  const timezone = typeof value?.timezone === "string" ? value.timezone.trim() : "";
  return cron ? `Cron: ${cron}${timezone ? ` · ${timezone}` : ""}` : "Schedule unknown";
};

function serviceJob(row, nowMs) {
  const owner = row.runtime === "claude-code-scheduled-task" ? "claude-code" : row.runtime;
  const observed = iso(row.schedule_observed_at);
  const schedulerFresh = observed !== null && nowMs - Date.parse(observed) <= 15 * 60_000
    && Date.parse(observed) <= nowMs + 5 * 60_000;
  const paused = schedulerFresh && row.scheduler_state === "disabled";
  const enabled = schedulerFresh && row.scheduler_state === "enabled";
  const at = iso(row.last_at);
  const receipt = row.last_receipt_ref || null;
  const lastRun = at && receipt
    ? { state: row.last_state || "unknown", at, receipt_ref: receipt } : null;
  const cadence = validSeconds(row.cadence_seconds) ? Number(row.cadence_seconds) : null;
  const grace = Number(row.grace_seconds) || 0;
  const dueMs = enabled && lastRun && cadence ? Date.parse(at) + cadence * 1000 : null;
  const nextDue = Number.isFinite(dueMs) ? new Date(dueMs).toISOString() : null;
  let state = "unknown";
  if (paused) state = "paused";
  else if (enabled && row.active_state === "running") state = "running";
  else if (enabled && lastRun && nextDue) {
    if (lastRun.state === "failed" || lastRun.state === "timed_out") state = "failed";
    else if (nowMs > dueMs + grace * 1000) state = "missed";
    else if (lastRun.state === "succeeded") state = "healthy";
  }
  return {
    key: row.key, name: row.name, owner, state,
    freshness: schedulerFresh ? (state === "missed" ? "stale" : "fresh") : "unknown",
    schedule: cadence ? cadence < 3600 ? `Every ${Math.round(cadence / 60)} minutes`
      : cadence < 86400 ? `Every ${cadence / 3600} hours` : `Every ${cadence / 86400} days` : "Schedule unknown",
    last_run: lastRun, next_due_at: nextDue,
    next_due_basis: nextDue ? "cadence_deadline" : null,
    scheduler_observed_at: observed, scheduler_receipt_ref: row.schedule_receipt_ref || null,
    actions: actions(),
  };
}

function controlJob(row, nowMs) {
  const at = iso(row.last_at);
  const receipt = row.last_receipt_ref || null;
  const lastRun = at && receipt
    ? { state: row.last_state || "unknown", at, receipt_ref: receipt } : null;
  const due = row.enabled ? iso(row.next_due_at) : null;
  const overdue = due !== null && Date.parse(due) < nowMs;
  let state = "unknown";
  if (!row.enabled) state = "paused";
  else if (row.active_state === "running") state = "running";
  else if (lastRun && CONTROL_FAILURE_STATES.has(lastRun.state)) state = "failed";
  else if (overdue) state = "missed";
  else if (due && lastRun?.state === "succeeded") state = "healthy";
  return {
    key: row.key, name: row.key.replaceAll(/[._-]/g, " "), owner: "control-plane",
    state, freshness: due ? (overdue ? "stale" : "fresh") : "unknown",
    schedule: recurrenceLabel(row.recurrence),
    last_run: lastRun, next_due_at: due,
    next_due_basis: due ? "queued_job" : null,
    actions: actions(),
  };
}

export function scheduleBoardTools() {
  return {
    "schedule-board": {
      write: false,
      description: "Read known scheduled jobs for the verified partner: scheduler evidence, last run receipt, next expected deadline or queued time, freshness, and supported actions. Missing provider evidence stays unknown.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (client, actor, _args, { now = () => new Date() } = {}) => {
        const scope = personalScopeForActor(actor);
        if (scope.status !== "personal")
          throw new ToolError({ error: "schedule_board_requires_partner_scope" });
        const observedAt = iso(now());
        const nowMs = Date.parse(observedAt);
        const serviceRows = (await client.query(SERVICE_SQL, [scope.sponsor])).rows;
        const controlRows = (await client.query(CONTROL_SQL, [scope.sponsor])).rows;
        const jobs = [
          ...serviceRows.map((row) => serviceJob(row, nowMs)),
          ...controlRows.map((row) => controlJob(row, nowMs)),
        ];
        const sources = ["launchd", "claude-code", "control-plane", "cron"].map((owner) => {
          const count = jobs.filter((job) => job.owner === owner).length;
          return { owner, state: count ? "read" : "unknown", count: count || null };
        });
        const overall = jobs.some((job) => ["missed", "failed"].includes(job.state))
          ? "attention" : sources.every((source) => source.state === "read")
            && jobs.every((job) => ["healthy", "paused", "running"].includes(job.state))
            ? "read" : "unknown";
        return { ok: true, schema: "schedule-board/v1", observed_at: observedAt,
          overall_state: overall, sources, jobs };
      },
    },
  };
}
