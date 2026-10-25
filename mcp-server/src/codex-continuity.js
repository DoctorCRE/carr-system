import { organizationTenantForActor, personalScopeForActor } from "./identity.js";

const MAX_CHECKPOINT_VERSION = Number.MAX_SAFE_INTEGER;

function checkpointVersion(value, ToolError) {
  const normalized = typeof value === "string" && /^[1-9][0-9]*$/.test(value)
    ? Number(value) : value;
  if (!Number.isSafeInteger(normalized) || normalized < 1 ||
      normalized > MAX_CHECKPOINT_VERSION)
    throw new ToolError({ error: "codex_checkpoint_version_invalid" });
  return normalized;
}

export function codexContinuityTools({ ToolError, assertNoCallerAuthorityFields }) {
  const guard = args => assertNoCallerAuthorityFields?.(args);
  return {
    "list-my-codex-sessions": {
      description: "List bounded first-hand native Codex checkpoint bindings for the authenticated human sponsor. Availability means a checkpoint was recorded; the local native host must be checked separately before opening.",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
      handler: async (c, actor, args) => {
        guard(args);
        if (Object.keys(args || {}).length)
          throw new ToolError({ error: "codex_session_list_arguments_invalid" });
        const scope = personalScopeForActor(actor);
        if (scope.status !== "personal")
          throw new ToolError({ error: "codex_sponsor_required" });
        const result = await c.query(
          `select native_task_id,checkpoint_version,updated_at
             from codex_continuity_checkpoint
            where organization_tenant_id=$1 and owner_actor_id=(select id from actor where slug=$2)
            order by updated_at desc,native_task_id asc limit 100`,
          [organizationTenantForActor(actor), scope.sponsor]);
        return { ok: true, sessions: result.rows.map(row => ({
          native_session_id: row.native_task_id,
          host: "codex_desktop",
          availability: "checkpoint_recorded",
          checkpoint_version: checkpointVersion(row.checkpoint_version, ToolError),
          updated_at: row.updated_at instanceof Date ? row.updated_at.toISOString() : row.updated_at,
        })) };
      },
    },

  };
}
