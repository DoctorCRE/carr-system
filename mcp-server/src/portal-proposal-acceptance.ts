// Compare isolated client choices and append only an explicitly accepted cart.
import { type Schema, Validator } from "@cfworker/json-schema";
import { REPLAY_WINDOW } from "./replay-receipt.ts";
import { ToolError } from "./tool-error.ts";
import type { Actor, Db, WriteEvent } from "./verb-contract.ts";

export const portalProposalReplayQuery = `select request_hash, response, false as permanent from tool_call where idempotency_key=$1
    and ($2 or created_at > now() - interval '${REPLAY_WINDOW}')
    union all select new_value#>>'{portal_acceptance,request_hash}' as request_hash,
      new_value - 'portal_acceptance' as response, true as permanent from event
      where idempotency_key=$1 and verb='accept-portal-proposal-v1' and subject_type='deal'
        and organization_tenant_id='carr-internal' and new_value ? 'portal_acceptance'
    order by permanent desc limit 2`;

type Scope = { deal_id: string; publication_id: string; tour_id: string };
export type ProposalReview = Scope & {
  schema_version: "portal-proposal-acceptance.v1";
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
} & (
    | { state: "reviewed"; property_ids?: string[]; selection_version_id?: string | null }
    | { state: "accepted"; property_ids: string[]; selection_version_id: string }
    | { state: "kept"; property_ids: string[]; selection_version_id: string | null }
  );
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
const decisionGuards: Schema[] = [
  {
    anyOf: [
      { properties: { state: { const: "reviewed" } } },
      {
        properties: { state: { const: "accepted" }, selection_version_id: uuid },
        required: ["property_ids", "selection_version_id"],
      },
      { properties: { state: { const: "kept" } }, required: ["property_ids", "selection_version_id"] },
    ],
  },
];
export const proposalDecisionSchema: Schema = {
  ...object({
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
  }),
  allOf: decisionGuards,
};
function validate(schema: Schema, value: unknown, code: string) {
  if (!new Validator(structuredClone(schema), "2020-12", false).validate(value).valid)
    throw new ToolError({ error: code });
}
export const proposalReviewSchema: Schema = {
  allOf: decisionGuards,
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
  withEnvelope: <T>(
    c: Db,
    actor: Actor,
    verb: string,
    args: object,
    fn: (requestHash: string) => Promise<T>,
  ) => Promise<T>;
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
    const review = await query(c, scope, null);
    if (review.state !== "reviewed") throw new ToolError({ error: "proposal_response_invalid" });
    return review;
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
            "select new_value - 'portal_acceptance' as new_value from public.event where organization_tenant_id='carr-internal' and subject_type='deal' and verb='accept-portal-proposal-v1' and subject_id=$1 and new_value->>'publication_id'=$2 and new_value->>'source_version'=$3 limit 1",
            [review.deal_id, review.publication_id, String(review.source_version)],
          )
        ).rows[0]?.new_value ?? null;
      if (decision !== null) validate(proposalDecisionSchema, decision, "proposal_response_invalid");
      return { ok: true, review, decision, pending: decision !== null ? null : proposalSignal(review) };
    },
    accept: async (c: Db, actor: Actor, input: unknown) => {
      validate(acceptSchema, input, "proposal_input_invalid");
      const args = structuredClone(input) as Acceptance;
      // Hold the canonical lock through envelope replay, append and audit.
      await authorize(c, actor, args);
      return deps.withEnvelope(c, actor, "accept-portal-proposal-v1", args, async (requestHash) => {
        const { deal_id, publication_id, tour_id, idempotency_key, ...request } = args;
        const review = await query(c, { deal_id, publication_id, tour_id }, request);
        if (review.state === "reviewed") throw new ToolError({ error: "proposal_response_invalid" });
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
          new: { ...receipt, portal_acceptance: { request_hash: requestHash } },
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
        "portal-proposal-acceptance.v1. Joe or Dell explicitly accepts exact canonical choices at reviewed source and cart versions, or keeps the current cart. Stale versions require review. Original-request retry replays its source-bound audit receipt even after tool_call expiry. Route and publication remain unchanged.",
      inputSchema: acceptSchema,
      handler: api.accept,
    },
  };
}
