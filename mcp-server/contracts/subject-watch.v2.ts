import { type Schema, Validator } from "@cfworker/json-schema";

export const subjectWatchPolicySchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    change_kinds: {
      type: "array",
      minItems: 1,
      maxItems: 3,
      uniqueItems: true,
      items: {
        type: "string",
        enum: ["reply_arrived" as const, "lease_threshold_crossed" as const, "listing_status_changed" as const],
      },
    },
    include_self_changes: { type: "boolean", default: true },
    lease_threshold_months: {
      type: "array",
      minItems: 1,
      maxItems: 20,
      uniqueItems: true,
      items: { type: "integer", minimum: 0, maximum: 120 },
    },
    lease_threshold_days: {
      type: "array",
      minItems: 1,
      maxItems: 20,
      uniqueItems: true,
      items: { type: "integer", minimum: 0, maximum: 3650 },
    },
  },
  required: ["change_kinds"],
} satisfies Schema;

const uuid = {
  type: "string",
  pattern: "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$",
} satisfies Schema;
const watch = {
  type: "object",
  additionalProperties: false,
  required: [
    "id",
    "actor_id",
    "subject_type",
    "subject_id",
    "state",
    "policy",
    "lifecycle_version",
    "baseline",
    "subscribed_at",
  ],
  properties: {
    id: uuid,
    actor_id: uuid,
    subject_id: uuid,
    subject_type: { enum: ["deal" as const, "lease" as const, "listing" as const] },
    state: { enum: ["watching" as const, "paused" as const, "removed" as const] },
    policy: { ...subjectWatchPolicySchema, required: ["change_kinds", "include_self_changes"] },
    lifecycle_version: { type: "integer", minimum: 1 },
    baseline: {
      type: "object",
      additionalProperties: false,
      required: ["schema_version", "subject_type", "subject_id", "version"],
      properties: {
        schema_version: { const: "subject-watch-baseline.v1" },
        subject_type: { enum: ["deal" as const, "lease" as const, "listing" as const] },
        subject_id: uuid,
        version: { type: "integer", minimum: 1 },
      },
    },
    subscribed_at: { type: "string", format: "date-time" },
  },
} satisfies Schema;
export const subjectWatchV2ResponseSchema = {
  type: "object",
  required: ["ok", "schema_version"],
  properties: {
    ok: { const: true },
    schema_version: { const: "subject-watch.v2" },
    health: {
      type: ["object", "null"],
      additionalProperties: false,
      required: ["last_success_at", "pending_count", "oldest_pending_at", "status"],
      properties: {
        last_success_at: { type: ["string", "null"], format: "date-time" },
        oldest_pending_at: { type: ["string", "null"], format: "date-time" },
        pending_count: { type: "integer", minimum: 0, maximum: 1001 },
        status: { enum: ["healthy" as const, "delayed" as const, "needs_repair" as const] },
      },
    },
    watch: { anyOf: [watch, { type: "null" }] },
    watches: { type: "array", maxItems: 100, items: watch },
    rows_changed: { type: "integer", minimum: 0, maximum: 1 },
    has_more: { type: "boolean" },
    next_cursor: { anyOf: [uuid, { type: "null" }] },
  },
  oneOf: [{ required: ["watch"] }, { required: ["watches", "has_more", "next_cursor"] }],
} satisfies Schema;
const policyValidator = new Validator(subjectWatchPolicySchema);
type Policy = {
  change_kinds: (typeof subjectWatchPolicySchema.properties.change_kinds.items.enum)[number][];
  include_self_changes?: boolean;
  lease_threshold_days?: number[];
  lease_threshold_months?: number[];
};
type Watch = {
  id: string;
  actor_id: string;
  subject_id: string;
  subject_type: (typeof watch.properties.subject_type.enum)[number];
  state: (typeof watch.properties.state.enum)[number];
  policy: Policy & { include_self_changes: boolean };
  lifecycle_version: number;
  baseline: {
    schema_version: "subject-watch-baseline.v1";
    subject_type: Watch["subject_type"];
    subject_id: string;
    version: number;
  };
  subscribed_at: string;
};
type Response = {
  ok: true;
  schema_version: "subject-watch.v2";
  health?: {
    last_success_at: string | null;
    oldest_pending_at: string | null;
    pending_count: number;
    status: (typeof subjectWatchV2ResponseSchema.properties.health.properties.status.enum)[number];
  } | null;
  rows_changed?: number;
} & (
  | { watch: Watch | null; watches?: Watch[]; has_more?: boolean; next_cursor?: string | null }
  | { watch?: never; watches: Watch[]; has_more: boolean; next_cursor: string | null }
);
export function validSubjectWatchPolicy(value: unknown): value is Policy {
  if (!policyValidator.validate(value).valid) return false;
  // The policy schema has validated every field before this narrowing.
  const policy = value as Policy;
  return (
    !policy.change_kinds.includes("lease_threshold_crossed") ||
    Array.isArray(policy.lease_threshold_days) ||
    Array.isArray(policy.lease_threshold_months)
  );
}
const response = new Validator(subjectWatchV2ResponseSchema);
export function parseSubjectWatchV2(value: unknown): Response {
  if (!response.validate(value).valid) throw new Error("invalid_subject_watch_v2");
  // The response schema has validated the nested watch and policy shapes.
  const parsed = value as Response;
  for (const watch of parsed.watches ?? (parsed.watch ? [parsed.watch] : []))
    if (!validSubjectWatchPolicy(watch.policy)) throw new Error("invalid_subject_watch_v2");
  return parsed;
}
