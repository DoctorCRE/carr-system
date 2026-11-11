import { ToolError } from "./tool-error.ts";
import type { Actor, Db, VerbInputs, WithEnvelope, WriteEvent } from "./verb-contract.ts";

const uuid = { type: "string", format: "uuid" };
export function clientPublicationTools({
  withEnvelope,
  writeEvent,
  authorizeDeal,
}: {
  authorizeDeal: (c: Db, actor: Actor, deal: string) => Promise<string>;
  withEnvelope: WithEnvelope;
  writeEvent: WriteEvent;
}) {
  const preview = async (c: Db, actor: Actor, args: VerbInputs["preview-deal-client-view"]) => {
    await authorizeDeal(c, actor, args.deal_id);
    const result = await c.query(
      `select ops.client_portal_projection(split_part(v.client_portal_url,'/',4)) as portal from public.v_deal_client_portal v where v.deal_id=$1`,
      [args.deal_id],
    );
    return {
      ok: true,
      schema_version: "client-publications.v1",
      portal: result.rows[0]?.portal ?? null,
    };
  };
  return {
    "list-deal-client-publications": {
      write: false,
      description:
        "List authored client view items, including hidden drafts and versions, for an authorized partner deal.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: { deal_id: uuid },
        required: ["deal_id"],
      },
      handler: async (c: Db, actor: Actor, args: VerbInputs["list-deal-client-publications"]) => {
        await authorizeDeal(c, actor, args.deal_id);
        return {
          ok: true,
          schema_version: "client-publications.v1",
          publications: (await c.query("select ops.list_client_publications($1) as items", [args.deal_id])).rows[0]
            .items,
        };
      },
    },
    "preview-deal-client-view": {
      write: false,
      description:
        "Preview exactly the current published client projection for an authorized deal. No credentials, financials or internal notes. Null when no active portal exists.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: { deal_id: uuid },
        required: ["deal_id"],
      },
      handler: preview,
    },
    "set-client-publication": {
      write: true,
      humanOnly: true,
      description:
        "Partner authors and publishes or withdraws one item in the deal client view. Files must be finalized and shared. Uses an optimistic version (zero creates) and idempotency key. Does not create or edit tours, send or deploy content.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: {
          idempotency_key: { type: "string", minLength: 1, maxLength: 200 },
          deal_id: uuid,
          publication_id: uuid,
          base_version: { type: "integer", minimum: 0 },
          published: { type: "boolean" },
          kind: {
            type: "string",
            enum: ["milestone", "document", "tour", "report", "deliverable"],
          },
          title: { type: "string", minLength: 1, maxLength: 200 },
          content: { type: "string", minLength: 1, maxLength: 10000 },
          deliverable_id: uuid,
          deliverable_version: { type: "integer", minimum: 1 },
        },
        required: ["idempotency_key", "deal_id", "publication_id", "base_version", "published", "kind", "title"],
      },
      handler: async (c: Db, actor: Actor, args: VerbInputs["set-client-publication"]) => {
        const sponsor = await authorizeDeal(c, actor, args.deal_id);
        if (
          args.kind === "deliverable"
            ? !args.deliverable_id || !args.deliverable_version || args.content !== undefined
            : !args.content?.trim() || args.deliverable_id !== undefined || args.deliverable_version !== undefined
        )
          throw new ToolError({ error: "publication_content_invalid" });
        await c.query("select pg_advisory_xact_lock(hashtextextended($1,0))", [args.idempotency_key]);
        return withEnvelope(c, actor, "set-client-publication", args, async () => {
          let publication: { id: string; version: number; published: boolean };
          try {
            publication = (
              await c.query("select ops.write_client_publication($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) as publication", [
                args.deal_id,
                args.publication_id,
                args.base_version,
                args.published,
                args.kind,
                args.title,
                args.content ?? null,
                args.deliverable_id ?? null,
                args.deliverable_version ?? null,
                sponsor,
              ])
            ).rows[0].publication;
          } catch (error) {
            if (error instanceof Error && /^publication_[a-z_]+$/.test(error.message))
              throw new ToolError({ error: error.message });
            throw error;
          }
          await writeEvent(c, actor, "set-client-publication", "deal", args.deal_id, {
            field: "client_publication",
            new: publication,
            cause: "human_stated",
            idempotency_key: args.idempotency_key,
          });
          return {
            ok: true,
            schema_version: "client-publications.v1",
            publication,
          };
        });
      },
    },
  };
}
