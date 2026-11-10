import { type Schema, Validator } from "@cfworker/json-schema";
import { ToolError } from "./tool-error.ts";
import { vendorDirectoryPayload } from "./vendor-people.ts";
import type { Actor, Db } from "./verb-contract.ts";

const inputSchema: Schema = {
  type: "object",
  additionalProperties: false,
  properties: {
    market: { type: "string", minLength: 1, maxLength: 200, pattern: "\\S" },
  },
};
const validator = new Validator(inputSchema);

export function vendorMarketTools({
  canExercisePartnerAuthority,
  organizationTenantForActor,
}: {
  canExercisePartnerAuthority: (actor: Actor) => boolean;
  organizationTenantForActor: (actor: Actor) => string;
}) {
  return {
    "read-vendor-markets-v1": {
      write: false,
      description:
        "Read vendor-markets.v1, the reviewed market registry, aliases, explicit vendor memberships, distinct counts and Regional references. Partner authority in the server-bound tenant is required. Optional market resolves a producer-owned alias. Office evidence and declared service coverage remain separate. Empty registry is explicit; territory is never parsed. Refuses over 100 markets or 5000 memberships.",
      inputSchema,
      handler: async (c: Db, actor: Actor, args: { market?: string }) => {
        if (!validator.validate(args).valid) throw new ToolError({ error: "invalid_vendor_market_arguments" });
        if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: "partner_required" });
        if (organizationTenantForActor(actor) !== "carr-internal")
          throw new ToolError({ error: "vendor_market_tenant_refused" });
        const { rows } = await c.query(
          `with scope as (
        select coalesce(nullif(current_setting('carr.organization_tenant_id',true),''),'carr-internal') tenant
      ), markets as materialized (
        select * from public.vendor_market order by slug limit 101
      ), memberships as materialized (
        select m.* from public.vendor_market_membership m
        join public.vendor v on v.id=m.vendor_id and v.merged_into is null
        join public.party p on p.id=v.party_id and p.merged_into is null and p.deleted_at is null
        order by m.market_slug,m.vendor_id limit 5001
      ), vendors as (
        select v.id,v.vendor_ref,v.category,p.name,${vendorDirectoryPayload("v")} payload
        from (select id,vendor_ref,category,party_id,'{}'::jsonb row_payload from public.vendor) v join public.party p on p.id=v.party_id
        where v.id in (select vendor_id from memberships)
      ) select scope.tenant,
        (select count(*)::int from markets) market_count,
        (select count(*)::int from memberships) membership_count,
        (select market_slug from public.vendor_market_key where key=$1) selected_market,
        coalesce((select jsonb_agg(to_jsonb(m) || jsonb_build_object(
          'aliases',coalesce((select jsonb_agg(k.key order by k.key) from public.vendor_market_key k where k.market_slug=m.slug),'[]'::jsonb),
          'vendor_count',(select count(distinct vendor_id)::int from memberships where market_slug=m.slug),
          'memberships',coalesce((select jsonb_agg(to_jsonb(x) order by x.vendor_id) from memberships x where x.market_slug=m.slug),'[]'::jsonb),
          'regional_references',coalesce((select jsonb_agg(to_jsonb(r) order by r.regional_slug) from public.vendor_market_regional_reference r where r.market_slug=m.slug),'[]'::jsonb)
        ) order by m.slug) from markets m),'[]'::jsonb) markets,
        coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'ref',v.vendor_ref,'category',v.category,'name',v.name,
          'people',v.payload->'people','market_slugs',(select jsonb_agg(m.market_slug order by m.market_slug) from memberships m where m.vendor_id=v.id)) order by v.id) from vendors v),'[]'::jsonb) vendors
        from scope`,
          [args.market?.trim().toLowerCase() ?? null],
        );
        const row = rows[0];
        if (row.tenant !== "carr-internal") throw new ToolError({ error: "vendor_market_tenant_refused" });
        if (row.market_count > 100 || row.membership_count > 5000)
          throw new ToolError({ error: "vendor_market_capacity_exceeded" });
        if (args.market && row.market_count > 0 && !row.selected_market)
          throw new ToolError({ error: "vendor_market_not_found" });
        return {
          ok: true,
          schema_version: "vendor-markets.v1",
          state: row.market_count === 0 ? "empty" : "ready",
          selected_market: row.selected_market,
          markets: row.markets,
          vendors: row.vendors,
        };
      },
    },
  };
}
