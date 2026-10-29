// Hold the latest external work inventory the system-work census reads, one row per partner.
import { ToolError } from "./tool-error.ts";
import { organizationTenantForActor } from "./identity.js";
import { partnerAuthoritySlugForActor } from "./partner-authority.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_PAYLOAD_CHARS = 8_000_000;
const refuse = error => { throw new ToolError({ error }); };

export function censusInventoryTools({ withEnvelope }) {
  return {
    "publish-census-inventory": {
      write: true,
      description: "Replace the latest external work inventory for the signed-in partner. The inventory is display data and never grants authority.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", format: "uuid" }, payload: { type: "object" },
      }, required: ["idempotency_key", "payload"] },
      handler: async (c, actor, args) => {
        if (!UUID.test(args.idempotency_key ?? "")) refuse("invalid_idempotency_key");
        if (!args.payload || Array.isArray(args.payload) || typeof args.payload !== "object" || !Array.isArray(args.payload.items))
          refuse("census_inventory_invalid");
        const payload = JSON.stringify(args.payload);
        if (payload.length > MAX_PAYLOAD_CHARS) refuse("census_inventory_invalid");
        const sponsor = partnerAuthoritySlugForActor(actor) ?? refuse("board_sponsor_unavailable");
        return withEnvelope(c, actor, "publish-census-inventory", args, async () => {
          await c.query(
            `insert into public.system_work_census_inventory (organization_tenant_id,sponsoring_human_slug,payload)
             values ($1,$2,$3::jsonb)
             on conflict (organization_tenant_id,sponsoring_human_slug) do update set payload=excluded.payload,updated_at=now()`,
            [organizationTenantForActor(actor), sponsor, payload]);
          return { ok: true, item_count: args.payload.items.length };
        });
      },
    },
  };
}
