import { connectionsProjection } from './connections.v1.js';
// DoctorCRE v5 slices V5-UX-C02 (Resource dashboard and metering read
// contract) and V5-UX-C06 (Local compute capacity and model-route
// visibility).
//
// TWO VERBS, ONE STORE. `read-resource-dashboard` is the one read contract
// the app calls: it always returns a row for every provider this contract
// owns -- neon, github, cloudflare, local_compute, model_route -- even when
// no collector for that provider exists yet (migrations/0580's
// ops.read_resource_dashboard names the exact reason: "no collector
// configured" for the three external providers C03-C05 have not built yet,
// "no collector observation received yet" for the two local ones before the
// first collector run). Nothing here invents a quantity, an allowance, or a
// capacity number for an absent source.
//
// `record-resource-observation` is the one write door, for the local,
// credential-less collector (tools/resource-collector.py) only. That script
// runs through `./run.sh call record-resource-observation '<json>'` --
// see tools/resource-collector.py's header for why that carries no
// credential of its own. Any other caller may use the verb too (it is a
// plain sponsored-agent write, not restricted to one script), but nothing
// else in this repo calls it yet.
//
// C06's checkable_done: "Source contract distinguishes measured capacity
// from configured capacity." measured_capacity and configured_capacity are
// two independent, independently-nullable fields on every local_compute
// observation -- never collapsed into one number, and an absent sensor is
// carried as null plus `reason`, never substituted with zero.

const PROVIDERS = Object.freeze(["neon", "github", "cloudflare", "local_compute", "model_route"]);
const STATES = Object.freeze(["ok", "partial", "stale", "unconfigured", "collector_absent", "host_offline"]);

const jsonOrNullSchema = { type: ["object", "null"] };

export function resourceObservationTools({ withEnvelope, ToolError }) {
  return {
    "read-resource-dashboard": {
      write: false,
      description: "Read the full DoctorCRE resource-metering provider matrix: neon, github, cloudflare, local_compute, model_route -- one entry each, always present. A provider with no collector observation yet reads back state 'unconfigured' (external providers whose adapter is not built) or 'collector_absent' (local providers awaiting their first collector run), with an explicit reason. Never fabricates a quantity, allowance, or capacity for an absent source; policy caps are never shown as consumed usage or an invoice.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c) => {
        const row = (await c.query("select ops.read_resource_dashboard() as dashboard")).rows[0];
        const dashboard = row?.dashboard;
        if (!dashboard || !Array.isArray(dashboard.providers))
          throw new ToolError({ error: "resource_dashboard_unavailable" });
        return { ok: true, ...dashboard, connections: connectionsProjection(dashboard) };
      },
    },

    "record-resource-observation": {
      write: true,
      description: "Write door for the local, credential-less resource collector: append one observation for one provider (neon, github, cloudflare, local_compute, model_route). Idempotent on idempotency_key; a reused key with different content refuses as key_reuse. local_compute observations carry measured_capacity and configured_capacity as two separate, independently-nullable fields -- an absent sensor is null with a reason, never a substituted zero. Append-only: there is no update or delete path, only a newer observation.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          provider: { type: "string", enum: [...PROVIDERS] },
          account: { type: ["string", "null"] },
          project: { type: ["string", "null"] },
          product: { type: ["string", "null"] },
          period: { type: ["string", "null"] },
          as_of: { type: ["string", "null"] },
          quantity: { type: ["number", "null"] },
          quantity_unit: { type: ["string", "null"] },
          allowance: { type: ["number", "null"] },
          policy: jsonOrNullSchema,
          estimate: { type: ["number", "null"] },
          charge: { type: ["number", "null"] },
          measured_capacity: jsonOrNullSchema,
          configured_capacity: jsonOrNullSchema,
          model_route: jsonOrNullSchema,
          state: { type: "string", enum: [...STATES] },
          reason: { type: ["string", "null"] },
          source: { type: "string" },
          observed_at: { type: "string" },
        },
        required: ["idempotency_key", "provider", "state", "source", "observed_at"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-resource-observation", args, async () => {
        for (const field of ["policy", "measured_capacity", "configured_capacity", "model_route"]) {
          if (args[field] !== undefined && args[field] !== null && typeof args[field] !== "object")
            throw new ToolError({ error: "resource_observation_field_invalid", field });
        }
        const row = (await c.query(
          `select * from ops.record_resource_observation(
             $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)`,
          [
            args.provider, args.account ?? null, args.project ?? null, args.product ?? null,
            args.period ?? null, args.as_of ?? null, args.quantity ?? null, args.quantity_unit ?? null,
            args.allowance ?? null, args.policy ? JSON.stringify(args.policy) : null,
            args.estimate ?? null, args.charge ?? null,
            args.measured_capacity ? JSON.stringify(args.measured_capacity) : null,
            args.configured_capacity ? JSON.stringify(args.configured_capacity) : null,
            args.model_route ? JSON.stringify(args.model_route) : null,
            args.state, args.reason ?? null, args.source, args.observed_at,
            args.idempotency_key, actor.slug || null,
          ],
        )).rows[0];
        if (!row) throw new ToolError({ error: "resource_observation_refused" });
        // No `replayed` field here: withEnvelope's own outer wrap supplies it
        // on replay (same convention as answer-work-request-for-joe) -- a
        // second field here would collide with the outer one under object
        // spread and silently mask a real replay as a fresh write.
        return { ok: true, id: row.id, provider: row.provider, state: row.state, reason: row.reason, observed_at: row.observed_at };
      }),
    },
  };
}
