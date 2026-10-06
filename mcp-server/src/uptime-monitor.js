export const API_ORIGIN = "https://api.doctorcre.com";
export const APP_ORIGIN = "https://app.doctorcre.com";
export const STATE_KEY = "uptime.v1";
export const ACTION = "on breach: one deduplicated uptime incident loop; owner orchestrator; " +
  "fix: restore the failed production route, contract, or monitor credential; " +
  "verify: all three JSON probes pass; auto-clear: recovery notice accepted and that loop closed";

const sha = (value) => typeof value === "string" && /^[a-f0-9]{40}$/.test(value);
const text = (value) => typeof value === "string" && value.length > 0;
const version = (value) => typeof value === "string" && /^\d+\.\d+\.\d+$/.test(value);

function apiShape(value) {
  return value?.ok === true && value.env?.value === "production" &&
    value.provider === "cloudflare-workers" && text(value.worker_version?.id) &&
    sha(value.git_sha?.value) && Number.isInteger(value.verb_count) && value.verb_count > 0 &&
    text(value.schema?.highest_applied_migration) && Number.isInteger(value.schema?.applied_count) &&
    value.schema.applied_count > 0 && value.schema.reason === null;
}

function appShape(value) {
  return value?.service === "doctorcre-app" && value.environment === "production" &&
    sha(value.source_commit) && text(value.provider_version_id) &&
    value.carr_contract?.schema === "doctorcre-carr-interface.v1" && version(value.carr_contract.version) &&
    value.route_contract?.schema === "doctorcre-app-routes.v1" && version(value.route_contract.version);
}

function rpcValue(value) {
  if (value?.jsonrpc !== "2.0" || value.id !== 1 || value.error ||
      value.result?.isError || !Array.isArray(value.result?.content)) throw new Error("rpc_shape");
  const content = value.result.content.find((item) => item.type === "text");
  return JSON.parse(content?.text);
}

function digestShape(value) {
  const rows = rpcValue(value)?.digest;
  if (!Array.isArray(rows)) return false;
  const counts = rows.find((row) => row.line === "row_counts")?.value;
  return ["deals", "leads", "clients", "vendors"].every((key) => Number.isInteger(counts?.[key]) && counts[key] >= 0);
}

async function request(url, options, fetcher, timeoutMs, decode) {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => { controller.abort(); reject(new Error("timeout")); }, timeoutMs);
  });
  try {
    return await Promise.race([deadline, (async () => {
      const response = await fetcher(url, { ...options, signal: controller.signal, redirect: "manual" });
      if (!response.ok) throw new Error(`http_${response.status}`);
      return await decode(response);
    })()]);
  } finally { clearTimeout(timer); }
}

function rpcOptions(token, name, args = {}) {
  return {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }),
  };
}

export async function probeProduction(env, fetcher = fetch, timeoutMs = 8000) {
  const targets = [
    ["api-release", `${API_ORIGIN}/release`, {}, apiShape],
    ["app-release", `${APP_ORIGIN}/app-release`, {}, appShape],
    ["verb-round-trip", `${API_ORIGIN}/mcp`, rpcOptions(env.CARR_MCP_PROBE_TOKEN, "integrity-digest"), digestShape],
  ];
  const checks = await Promise.all(targets.map(async ([name, url, options, shape]) => {
    const start = Date.now();
    try {
      if (name === "verb-round-trip" && !env.CARR_MCP_PROBE_TOKEN) throw new Error("missing_CARR_MCP_PROBE_TOKEN");
      const valid = await request(url, options, fetcher, timeoutMs, async (response) => shape(await response.json()));
      return { name, ok: Boolean(valid), reason: valid ? "ok" : "wrong_shape", elapsed_ms: Date.now() - start };
    } catch (error) {
      const reason = /^(http_\d{3}|timeout|missing_CARR_MCP_PROBE_TOKEN)$/.test(error.message) ? error.message : "invalid_json_or_transport";
      return { name, ok: false, reason, elapsed_ms: Date.now() - start };
    }
  }));
  return { ok: checks.every((check) => check.ok), checks };
}

const iso = (time) => new Date(time).toISOString();
const complete = (incident) => incident.recovery_sent && incident.loop_closed;

async function callRecord(env, fetcher, name, args) {
  if (!env.UPTIME_RECORD_TOKEN) throw new Error("missing_UPTIME_RECORD_TOKEN");
  const value = await request(`${API_ORIGIN}/mcp`, rpcOptions(env.UPTIME_RECORD_TOKEN, name, args),
    fetcher, 8000, async (response) => rpcValue(await response.json()));
  if (!value || value.error) throw new Error("record_refused");
  return value;
}

async function ping(env, fetcher, failure, body) {
  const url = env.UPTIME_HEALTHCHECKS_PING_URL;
  if (!/^https:\/\/hc-ping\.com\/[a-f0-9-]{36}$/.test(url || "")) throw new Error("missing_or_invalid_UPTIME_HEALTHCHECKS_PING_URL");
  await request(`${url}${failure ? "/fail" : ""}`, { method: "POST", body }, fetcher, 8000,
    async (response) => { if ((await response.text()).trim() !== "OK") throw new Error("ping_shape"); });
}

function incidentText(incident) {
  return `DoctorCRE production uptime incident ${incident.id}. ` +
    `First failure ${incident.first_failure_at}; confirmed ${incident.confirmed_at}; ` +
    `recovered ${incident.recovered_at || "pending"}. ` +
    `Checks: ${incident.checks.map((check) => `${check.name}=${check.reason} (${check.elapsed_ms}ms)`).join(", ")}. ${ACTION}`;
}

async function deliverAlerts(storage, state, env, fetcher) {
  state.alert_error = null;
  try {
    const active = state.incidents.find((incident) => incident.id === state.active_incident);
    const pending = (active ? [active] : state.incidents).filter((incident) => !incident.down_sent);
    if (pending.length) {
      await ping(env, fetcher, true, pending.map(incidentText).join("\n"));
      for (const incident of pending) incident.down_sent = true;
      await storage.put(STATE_KEY, state);
    }
    if (!active) {
      const recovered = state.incidents.filter((incident) => incident.recovered_at && !incident.recovery_sent);
      if (state.probes_ok && recovered.length) {
        await ping(env, fetcher, false, `RECOVERED. ${recovered.map(incidentText).join("\n")}`);
        for (const incident of recovered) incident.recovery_sent = true;
      } else if (!recovered.length) {
        await ping(env, fetcher, false, `Monitor alive; failure streak ${state.failures}/3. ${ACTION}`);
      }
    }
  } catch {
    state.alert_error = "Healthchecks delivery unavailable; verify UPTIME_HEALTHCHECKS_PING_URL and its notification integration";
  }
  await storage.put(STATE_KEY, state);
}

async function deliverRecord(storage, state, env, fetcher) {
  state.record_error = null;
  for (const incident of state.incidents.filter((item) => !item.loop_id || (item.recovered_at && !item.loop_closed))) {
    try {
      if (!incident.loop_id) {
        const value = await callRecord(env, fetcher, "add-loop", {
          idempotency_key: `uptime:${incident.id}:open`, kind: "open_loop", domain: "system",
          owner: "claude", marker: "none", body: incident.open_body,
          source_note: "carr-uptime Cron monitor; orchestrator owns remediation; immutable incident timings are retained in the monitor ledger",
          blocker: "other_lane", blocker_detail: "The orchestrator production-recovery lane must restore the failed DoctorCRE route or contract; this observer has no deployment authority",
        });
        if (value.ok !== true || !text(value.loop_id)) throw new Error("loop_shape");
        incident.loop_id = value.loop_id;
        await storage.put(STATE_KEY, state);
      }
      if (incident.recovered_at && !incident.loop_closed) {
        const loop = await callRecord(env, fetcher, "read-loop", { loop_id: incident.loop_id });
        if (loop.loop_id !== incident.loop_id || !Number.isInteger(loop.version)) throw new Error("loop_shape");
        if (loop.status === "open") {
          const seconds = (Date.parse(incident.recovered_at) - Date.parse(incident.first_failure_at)) / 1000;
          const value = await callRecord(env, fetcher, "close-loop", {
            idempotency_key: `uptime:${incident.id}:close`, loop_id: incident.loop_id, base_version: loop.version,
            resolution: "done", outcome: `${incidentText(incident)} Observed outage ${seconds} seconds; all three production JSON probes passed on recovery.`,
          });
          if (value.ok !== true) throw new Error("close_shape");
        } else if (!["done", "dropped"].includes(loop.status)) throw new Error("loop_status");
        incident.loop_status = loop.status === "open" ? "done" : loop.status;
        incident.loop_closed = true;
      }
    } catch {
      state.record_error = "Incident loop pending; verify UPTIME_RECORD_TOKEN and CARR record availability";
    }
  }
  await storage.put(STATE_KEY, state);
}

export async function runMinute(storage, env, scheduledTime, fetcher = fetch, now = Date.now) {
  const state = await storage.get(STATE_KEY) || {
    schema: "carr-uptime.v1", action: ACTION, last_slot: -1, checked_at: null,
    failures: 0, first_failure_at: null, active_incident: null, incidents: [],
  };
  const slot = Math.floor(scheduledTime / 60000);
  if (slot < state.last_slot || (slot === state.last_slot && state.finalized_slot === slot)) return state;
  if (slot > state.last_slot) {
    if (slot > state.last_slot + 1) { state.failures = 0; state.first_failure_at = null; }
    const sample = await probeProduction(env, fetcher);
    state.last_slot = slot;
    state.scheduled_at = iso(scheduledTime);
    state.checked_at = iso(now());
    state.probes_ok = sample.ok;
    state.checks = sample.checks;
    state.ok = false;
    if (sample.ok) {
      state.failures = 0;
      state.first_failure_at = null;
      if (state.active_incident) {
        state.incidents.find((item) => item.id === state.active_incident).recovered_at = state.checked_at;
        state.active_incident = null;
      }
    } else {
      if (!state.failures) state.first_failure_at = state.checked_at;
      state.failures++;
      if (state.failures >= 3 && !state.active_incident) {
        const incident = {
          id: `production-${slot}`, first_failure_at: state.first_failure_at, confirmed_at: state.checked_at,
          recovered_at: null, checks: sample.checks, down_sent: false, recovery_sent: false, loop_id: null, loop_closed: false,
        };
        incident.open_body = incidentText(incident);
        state.active_incident = incident.id;
        state.incidents.push(incident);
      }
    }
    // Persist the incident before contacting either provider. Their transition/idempotency contracts make replay safe.
    await storage.put(STATE_KEY, state);
  }
  await deliverAlerts(storage, state, env, fetcher);
  if (state.checks.find((check) => check.name === "verb-round-trip").ok) await deliverRecord(storage, state, env, fetcher);
  const finished = state.incidents.filter(complete).slice(-20);
  state.incidents = state.incidents.filter((incident) => !complete(incident) || finished.includes(incident));
  state.pending_records = state.incidents.filter((incident) => !incident.loop_id || (incident.recovered_at && !incident.loop_closed)).length;
  state.pending_alerts = state.incidents.filter((incident) => !incident.down_sent || (incident.recovered_at && !incident.recovery_sent)).length;
  state.configuration_missing = ["CARR_MCP_PROBE_TOKEN", "UPTIME_RECORD_TOKEN", "UPTIME_HEALTHCHECKS_PING_URL"].filter((key) => !env[key]);
  state.ok = state.probes_ok && !state.alert_error && !state.record_error && !state.pending_records &&
    !state.pending_alerts && !state.configuration_missing.length;
  state.finalized_slot = state.last_slot;
  await storage.put(STATE_KEY, state);
  return state;
}
