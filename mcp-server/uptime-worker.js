import { ACTION, runMinute, STATE_KEY } from "./src/uptime-monitor.js";

function summary(state) {
  const fresh = state?.checked_at && Date.now() - Date.parse(state.checked_at) < 180000;
  return {
    schema: "carr-uptime.v1", ok: Boolean(fresh && state?.ok && state?.probes_ok &&
      state.finalized_slot === state.last_slot), checked_at: state?.checked_at || null,
    failures: state?.failures || 0, active_incident: state?.active_incident || null,
    pending_records: state?.pending_records || 0, pending_alerts: state?.pending_alerts || 0,
    checks: state?.checks || [], configuration_missing: state?.configuration_missing || [],
    alert_error: state?.alert_error || null, record_error: state?.record_error || null, action: ACTION,
  };
}

const json = (body) => Response.json(body, {
  status: body.ok ? 200 : 503, headers: { "Cache-Control": "no-store" },
});

export class UptimeLedger {
  constructor(ctx, env) {
    this.storage = ctx.storage;
    this.env = env;
    this.tail = Promise.resolve();
  }

  async fetch(request) {
    if (request.method === "POST" && new URL(request.url).pathname === "/tick") {
      const { scheduledTime } = await request.json();
      if (!Number.isSafeInteger(scheduledTime) || scheduledTime < 0) return new Response("Invalid schedule", { status: 400 });
      const task = this.tail.then(() => runMinute(this.storage, this.env, scheduledTime));
      this.tail = task.catch(() => {});
      return json(summary(await task));
    }
    await this.tail;
    return json(summary(await this.storage.get(STATE_KEY)));
  }
}

const ledger = (env) => env.UPTIME_LEDGER.get(env.UPTIME_LEDGER.idFromName("production"));

export default {
  async scheduled(controller, env) {
    const response = await ledger(env).fetch(new Request("https://ledger/tick", {
      method: "POST", body: JSON.stringify({ scheduledTime: controller.scheduledTime }),
    }));
    const status = await response.json();
    console.log(`${status.ok ? "OK" : "WARN"} production uptime: ${status.failures}/3 failures; ` +
      `${status.pending_records} incident loops pending; ${status.pending_alerts} alerts pending · ${status.action}`);
  },
  async fetch(request, env) {
    if (request.method !== "GET" || new URL(request.url).pathname !== "/healthz") return new Response("Not found", { status: 404 });
    return ledger(env).fetch(new Request("https://ledger/healthz"));
  },
};
