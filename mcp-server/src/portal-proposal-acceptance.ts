// Compare isolated client choices and append only an explicitly accepted cart.
import { type Schema, Validator } from "@cfworker/json-schema";
import { ToolError } from "./tool-error.ts";
import type { Actor, Db, WithEnvelope, WriteEvent } from "./verb-contract.ts";

type Scope = { deal_id: string; publication_id: string; tour_id: string };
export type ProposalReview = Scope & {
  schema_version: "portal-proposal-acceptance.v1";
  state: "reviewed" | "accepted" | "kept";
  publication_version: number;
  source_version: number;
  cart_version: number;
  cart_version_id: string | null;
  selected: string[];
  mine: string[];
  notes: Record<string, string>;
  added: string[];
  removed: string[];
  retained: string[];
  retired: string[];
  property_ids?: string[];
  selection_version_id?: string | null;
};
const uuid: Schema = { type: "string", format: "uuid" };
const ids: Schema = { type: "array", maxItems: 100, uniqueItems: true, items: uuid };
const integer: Schema = { type: "integer", minimum: 0, maximum: 2147483646 };
const nullableId: Schema = { anyOf: [uuid, { type: "null" }] };
const object = (properties: Record<string, Schema>): Schema => ({
  type: "object",
  additionalProperties: false,
  properties,
  required: Object.keys(properties),
});
const scopeFields = { deal_id: uuid, publication_id: uuid, tour_id: uuid };
const readSchema = object(scopeFields);
const acceptSchema = object({
  ...scopeFields,
  idempotency_key: uuid,
  publication_version: integer,
  source_version: integer,
  cart_version: integer,
  cart_version_id: nullableId,
  decision: { enum: ["accept", "keep"] },
  property_ids: ids,
});
type Acceptance = Scope & {
  idempotency_key: string;
  publication_version: number;
  source_version: number;
  cart_version: number;
  cart_version_id: string | null;
  decision: "accept" | "keep";
  property_ids: string[];
};
const decisionSchema = object({
  ...scopeFields,
  ok: { const: true },
  schema_version: { const: "portal-proposal-acceptance.v1" },
  state: { enum: ["accepted", "kept"] },
  publication_version: integer,
  source_version: integer,
  cart_version: integer,
  cart_version_id: nullableId,
  property_ids: ids,
  selection_version_id: nullableId,
});
function validate(schema: Schema, value: unknown, code: string) {
  if (!new Validator(structuredClone(schema), "2020-12", false).validate(value).valid)
    throw new ToolError({ error: code });
}
export const proposalReviewSchema: Schema = {
  type: "object",
  required: [
    "schema_version",
    "state",
    ...Object.keys(scopeFields),
    "publication_version",
    "source_version",
    "cart_version",
    "cart_version_id",
    "selected",
    "mine",
    "notes",
    "added",
    "removed",
    "retained",
    "retired",
  ],
  properties: {
    ...scopeFields,
    schema_version: { const: "portal-proposal-acceptance.v1" },
    state: { enum: ["reviewed", "accepted", "kept"] },
    publication_version: integer,
    source_version: integer,
    cart_version: integer,
    cart_version_id: nullableId,
    ...Object.fromEntries(
      ["selected", "mine", "added", "removed", "retained", "retired", "property_ids"].map((k) => [k, ids]),
    ),
    notes: { type: "object", additionalProperties: { type: "string", maxLength: 2000 } },
    selection_version_id: nullableId,
  },
};
export function parseProposalReview(value: unknown): ProposalReview {
  validate(proposalReviewSchema, value, "proposal_response_invalid");
  return value as ProposalReview;
}
function proposalSignal(review: ProposalReview) {
  const key = `${review.publication_id}:${review.source_version}`;
  return {
    key,
    class: "review_needed",
    topic: "action_confirmation",
    owner_module: "tours",
    source_version: review.source_version,
    publication_id: review.publication_id,
    deal_id: review.deal_id,
    tour_id: review.tour_id,
    deep_link: `/deals/${review.deal_id}?proposal=${review.publication_id}&version=${review.source_version}`,
    line: "Client Tour List choices await your review.",
    requires_acceptance: true,
  };
}
export function portalProposalAcceptance(deps: {
  withEnvelope: WithEnvelope;
  writeEvent: WriteEvent;
  partnerAuthoritySlugForActor: (actor: Actor) => string | null;
}) {
  async function authorize(c: Db, actor: Actor, scope: Scope) {
    if (
      actor.human !== true ||
      !["joe", "dell"].includes(String(actor.slug)) ||
      deps.partnerAuthoritySlugForActor(actor) !== actor.slug
    )
      throw new ToolError({ error: "human_only" });
    const deal = (await c.query("select id,owner,version from public.deal where id=$1", [scope.deal_id])).rows[0];
    await c.query("select set_config('carr.tour_deal_access',$1,true)", [
      JSON.stringify({
        slug: actor.slug,
        deals: deal ? { [deal.id]: { owner: deal.owner, version: deal.version } } : {},
      }),
    ]);
    return query(c, scope, null);
  }
  async function query(c: Db, scope: Scope, request: unknown) {
    try {
      return parseProposalReview(
        (
          await c.query("select ops.review_portal_proposal($1::uuid,$2::uuid,$3::uuid,$4::jsonb) result", [
            scope.deal_id,
            scope.publication_id,
            scope.tour_id,
            request === null ? null : JSON.stringify(request),
          ])
        ).rows[0]?.result,
      );
    } catch (error) {
      if (error instanceof Error && /^proposal_[a-z_]+$/.test(error.message))
        throw new ToolError({ error: error.message });
      throw error;
    }
  }
  return {
    read: async (c: Db, actor: Actor, input: unknown) => {
      validate(readSchema, input, "proposal_input_invalid");
      const review = await authorize(c, actor, input as Scope);
      const decision =
        (
          await c.query<{ new_value: unknown }>(
            "select new_value from public.event where verb='accept-portal-proposal-v1' and subject_id=$1 and new_value->>'publication_id'=$2 and new_value->>'source_version'=$3 order by recorded_at desc,id desc limit 1",
            [review.deal_id, review.publication_id, String(review.source_version)],
          )
        ).rows[0]?.new_value ?? null;
      if (decision !== null) validate(decisionSchema, decision, "proposal_response_invalid");
      return { ok: true, review, decision, pending: decision !== null ? null : proposalSignal(review) };
    },
    accept: async (c: Db, actor: Actor, input: unknown) => {
      validate(acceptSchema, input, "proposal_input_invalid");
      const args = structuredClone(input) as Acceptance;
      // Hold the canonical lock through envelope replay, append and audit.
      await authorize(c, actor, args);
      return deps.withEnvelope(c, actor, "accept-portal-proposal-v1", args, async () => {
        const { deal_id, publication_id, tour_id, idempotency_key, ...request } = args;
        const review = await query(c, { deal_id, publication_id, tour_id }, request);
        const receipt = {
          ok: true,
          schema_version: review.schema_version,
          state: review.state,
          deal_id,
          publication_id,
          tour_id,
          publication_version: review.publication_version,
          source_version: review.source_version,
          cart_version: review.cart_version,
          cart_version_id: review.cart_version_id,
          property_ids: args.property_ids,
          selection_version_id: review.selection_version_id,
        };
        await deps.writeEvent(c, actor, "accept-portal-proposal-v1", "deal", deal_id, {
          field: "tour_selection_proposal",
          new: receipt,
          idempotency_key,
        });
        return receipt;
      });
    },
  };
}
export function portalProposalAcceptanceTools(deps: Parameters<typeof portalProposalAcceptance>[0]) {
  const api = portalProposalAcceptance(deps);
  return {
    "read-portal-proposal-review-v1": {
      writerConnection: true,
      authorityOnly: true,
      humanOnly: true,
      humanCallerOnly: true,
      description:
        "portal-proposal-acceptance.v1. Compare a live client proposal with the same-deal Tour cart. Returns a bounded action-confirmation signal. Does not change canonical work.",
      inputSchema: readSchema,
      handler: api.read,
    },
    "accept-portal-proposal-v1": {
      write: true,
      authorityOnly: true,
      humanOnly: true,
      humanCallerOnly: true,
      description:
        "portal-proposal-acceptance.v1. Joe or Dell explicitly accepts exact canonical choices at reviewed source and cart versions, or keeps the current cart. Stale versions require review. Original-request retry replays its receipt for 30 days. Route and publication remain unchanged.",
      inputSchema: acceptSchema,
      handler: api.accept,
    },
  };
}
