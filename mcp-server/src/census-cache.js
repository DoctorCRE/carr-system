// Store the system-work census's immutable content-addressed pages outside the board table.
import { ToolError } from "./tool-error.ts";
import { organizationTenantForActor } from "./identity.js";
import { partnerAuthoritySlugForActor } from "./partner-authority.js";

export const CENSUS_CACHE_KINDS = ["external", "snapshot"];
export const CENSUS_CACHE_RETENTION_DAYS = 14;
// A page is rewritten on every publish that still names it, so a page untouched this long is no longer referenced.
const TRIM_BATCH = 500;
const MAX_PAYLOAD_CHARS = 262144;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DIGEST = /^[0-9a-f]{64}$/;
const address = {
  kind: { type: "string", enum: CENSUS_CACHE_KINDS },
  digest: { type: "string", pattern: "^[0-9a-f]{64}$" },
};

const refuse = error => { throw new ToolError({ error }); };
function pageAddress(args) {
  if (!CENSUS_CACHE_KINDS.includes(args.kind) || typeof args.digest !== "string" || !DIGEST.test(args.digest))
    refuse("census_cache_page_invalid");
}
const sponsor = actor => partnerAuthoritySlugForActor(actor) ?? refuse("board_sponsor_unavailable");

export function censusCacheTools({ withEnvelope }) {
  return {
    "publish-census-cache-page": {
      write: true,
      description: "Store or touch one immutable census cache page by kind and SHA-256 digest, then drop pages untouched for 14 days. The page is display data and never grants authority.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", format: "uuid" }, ...address, payload: { type: "object" },
      }, required: ["idempotency_key", "kind", "digest", "payload"] },
      handler: async (c, actor, args) => {
        pageAddress(args);
        if (!UUID.test(args.idempotency_key ?? "")) refuse("invalid_idempotency_key");
        if (!args.payload || Array.isArray(args.payload) || typeof args.payload !== "object") refuse("census_cache_page_invalid");
        const payload = JSON.stringify(args.payload);
        if (payload.length > MAX_PAYLOAD_CHARS) refuse("census_cache_page_invalid");
        const principal = sponsor(actor);
        return withEnvelope(c, actor, "publish-census-cache-page", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const stored = await c.query(
            `insert into public.system_work_census_cache (organization_tenant_id,sponsoring_human_slug,kind,digest,payload)
             values ($1,$2,$3,$4,$5::jsonb)
             on conflict (organization_tenant_id,sponsoring_human_slug,kind,digest) do update set touched_at=now()
             returning payload = $5::jsonb as same`, [tenant, principal, args.kind, args.digest, payload]);
          // The digest names the content, so a different stored payload under it is a corrupt page, never a newer version.
          if (!stored.rows[0].same) refuse("census_cache_digest_conflict");
          const trimmed = await c.query(
            `delete from public.system_work_census_cache where (organization_tenant_id,sponsoring_human_slug,kind,digest) in
               (select organization_tenant_id,sponsoring_human_slug,kind,digest from public.system_work_census_cache
                 where touched_at < now() - make_interval(days => $1) order by touched_at limit $2)`,
            [CENSUS_CACHE_RETENTION_DAYS, TRIM_BATCH]);
          return { ok: true, kind: args.kind, digest: args.digest, trimmed: trimmed.rowCount };
        });
      },
    },
    "read-census-cache-page": {
      write: false,
      description: "Read one stored census cache page by kind and SHA-256 digest, for example a page named by a board snapshot's pagination.pages.",
      inputSchema: { type: "object", additionalProperties: false, properties: address, required: ["kind", "digest"] },
      handler: async (c, actor, args) => {
        pageAddress(args);
        const rows = await c.query(
          `select payload from public.system_work_census_cache
            where organization_tenant_id=$1 and sponsoring_human_slug=$2 and kind=$3 and digest=$4`,
          [organizationTenantForActor(actor), sponsor(actor), args.kind, args.digest]);
        if (!rows.rows.length) refuse("census_cache_page_not_found");
        return { ok: true, kind: args.kind, digest: args.digest, payload: rows.rows[0].payload };
      },
    },
  };
}
