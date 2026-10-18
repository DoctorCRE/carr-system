// Server-only front door for the existing Work Request/accepted-plan path.
// Callers name a Work Request and accepted plan; the database derives the
// tenant, canonical refs, required/advisory classification, and timestamps.
import { organizationTenantForActor } from "./identity.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const WR = /^WR-[0-9]{1,12}$/;
const ATTEMPT_RELIABILITY_NOT_VISIBLE_MESSAGE = "attempt reliability is not visible to tenant";
// The security-definer read function deliberately uses one stable refusal for
// both a missing AttemptReceipt and a receipt owned by another tenant.  Map
// only that exact PostgreSQL raise-exception (P0001 + exact message) to the
// public not-found ToolError.  Other database faults must remain failures so
// schema, connectivity, and privilege regressions cannot masquerade as a
// missing record.
export function isAttemptReliabilityVisibilityRefusal(error) {
  return error?.code === "P0001" && error?.message === ATTEMPT_RELIABILITY_NOT_VISIBLE_MESSAGE;
}
/** The DB read is payload-only; every panel fact uses this one MCP wrapper. */
export function activationReliabilityWire(payload) {
  return { job_passport: { schema_version: "job-passport-wire.v1", kind: "activation_reliability_projection", payload } };
}

export function evidenceActivationTools({ withEnvelope, ToolError }) {
  return {
    "read-execution-environment-providers": {
      description: "Read the governed execution-environment provider registry, conformance posture, operator surface, and rollback state. Provider manifests contain no credentials or raw task content.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c) => {
        const providers = (await c.query("select ops.read_execution_environment_providers() as providers /* read-execution-environment-providers */")).rows[0]?.providers;
        if (!Array.isArray(providers)) throw new ToolError({ error: "execution_environment_registry_unavailable" });
        return { ok: true, providers };
      },
    },
    "activate-context-bundle": {
      description: "Compile and bind bounded canonical knowledge to one accepted plan. The caller supplies no artifact bodies, refs, revisions, required flags, tenant, or authority.",
      write: true,
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { human_ref: { type: "string" }, plan_ref: { type: "string" }, idempotency_key: { type: "string" } },
        required: ["human_ref", "plan_ref", "idempotency_key"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "activate-context-bundle", args, async () => {
        if (!WR.test(String(args.human_ref || "")) || !/^PLAN-[0-9a-f]{12}-v[1-9][0-9]*$/.test(String(args.plan_ref || "")) || !UUID.test(String(args.idempotency_key || "")))
          throw new ToolError({ error: "activation_reference_invalid" });
        const tenant = organizationTenantForActor(actor);
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* activate-context-bundle:tenant */", [tenant]);
        const compiled = await c.query("select ops.compile_context_bundle($1::text,$2::text,$3::text) as bundle /* activate-context-bundle:compile */", [args.human_ref, args.plan_ref, tenant]);
        const bundle = compiled.rows[0]?.bundle;
        if (!bundle) throw new ToolError({ error: "activation_plan_not_ready" });
        const result = await c.query("select * from ops.activate_context_bundle($1::text,$2::text,$3::jsonb,$4::uuid) /* activate-context-bundle:activate */", [args.human_ref, args.plan_ref, JSON.stringify(bundle), args.idempotency_key]);
        const row = result.rows[0];
        if (!row) throw new ToolError({ error: "activation_refused" });
        return { ok: true, human_ref: args.human_ref, plan_ref: args.plan_ref, binding_id: row.binding_id, bundle_digest: row.bundle_digest, replayed: row.replayed };
      }),
    },
    "read-context-activation": {
      description: "Read the redacted, exact context activation projection for one Work Request and binding. Tenant scope is derived from the authenticated actor.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { human_ref: { type: "string" }, binding_id: { type: "string" } },
        required: ["human_ref", "binding_id"],
      },
      handler: async (c, actor, args) => {
        if (!WR.test(String(args.human_ref || "")) || !/^ctx-[0-9a-f]{16}$/.test(String(args.binding_id || "")))
          throw new ToolError({ error: "activation_reference_invalid" });
        const row = (await c.query(
          `with tenant_scope as materialized (
             select set_config('carr.organization_tenant_id',$1::text,true) /* read-context-activation:tenant */
           )
           select ops.read_context_activation($2::text,$3::text) as activation
             from tenant_scope /* read-context-activation */`,
          [organizationTenantForActor(actor), args.human_ref, args.binding_id],
        )).rows[0]?.activation;
        if (!row) throw new ToolError({ error: "activation_not_found" });
        return { ok: true, human_ref: args.human_ref, binding_id: args.binding_id, activation: row };
      },
    },
    "render-context-activation": {
      description: "Render ephemeral, exact frozen context revisions for a fresh governed session. Required missing, stale, or digest-mismatched revisions refuse; bodies are never persisted in the activation bundle or receipt.",
      inputSchema: { type: "object", additionalProperties: false, properties: { human_ref: { type: "string" }, binding_id: { type: "string" } }, required: ["human_ref", "binding_id"] },
      handler: async (c, actor, args) => {
        if (!WR.test(String(args.human_ref || "")) || !/^ctx-[0-9a-f]{16}$/.test(String(args.binding_id || ""))) throw new ToolError({ error: "activation_reference_invalid" });
        const rows = (await c.query(
          `with tenant_scope as materialized (
             select set_config('carr.organization_tenant_id',$1::text,true) /* render-context-activation:tenant */
           )
           select ops.render_context_activation_for_brief($2::text,$3::text) as items
             from tenant_scope /* render-context-activation */`,
          [organizationTenantForActor(actor), args.human_ref, args.binding_id],
        )).rows[0]?.items;
        if (!Array.isArray(rows)) throw new ToolError({ error: "context_render_refused" });
        return { ok: true, human_ref: args.human_ref, binding_id: args.binding_id, ephemeral: true, items: rows };
      },
    },
    "read-attempt-reliability": {
      description: "Read canonical authority-derived reliability posture for one tenant-scoped AttemptReceipt. Executor claims are not promotion evidence.",
      inputSchema: { type: "object", additionalProperties: false, properties: { attempt_id: { type: "string" } }, required: ["attempt_id"] },
      handler: async (c, actor, args) => {
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* read-attempt-reliability:tenant */", [organizationTenantForActor(actor)]);
        let result;
        try {
          result = await c.query("select ops.read_attempt_receipt_reliability($1::text) as reliability /* read-attempt-reliability */", [args.attempt_id]);
        } catch (error) {
          if (isAttemptReliabilityVisibilityRefusal(error)) throw new ToolError({ error: "attempt_reliability_not_found" });
          throw error;
        }
        const reliability = result.rows[0]?.reliability;
        if (!reliability) throw new ToolError({ error: "attempt_reliability_not_found" });
        return { ok: true, reliability, ...activationReliabilityWire(reliability) };
      },
    },
  };
}
