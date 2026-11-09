// Persist private installation subscriptions with authenticated ownership and logout fencing.

import { type Schema, Validator } from "@cfworker/json-schema";
import deliverySchema from "../contracts/web-push-delivery.v1.json" with { type: "json" };
import type { ToolError as ErrorClass } from "./tool-error.ts";
import type { Actor, Db } from "./verb-contract.ts";

type Operation = "register" | "revoke" | "list" | "logout" | "status";
type Subscription = {
  installation_id: string;
  idempotency_key: string;
  base_generation: number;
  endpoint: string;
  p256dh: string;
  auth: string;
  vapid_key_version: string;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const endpoint =
  /^https:\/\/(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com|[a-z0-9-]+\.notify\.windows\.com|web\.push\.apple\.com)\/[^\s#]+$/;
const properties = {
  installation_id: { type: "string", format: "uuid" },
  idempotency_key: { type: "string", format: "uuid" },
  base_generation: { type: "integer", minimum: 0 },
  endpoint: { type: "string", maxLength: 2048 },
  p256dh: { type: "string", maxLength: 87 },
  auth: { type: "string", maxLength: 22 },
  vapid_key_version: { type: "string", const: "v1" },
};
const errors = new Set([
  "push_session_required",
  "push_session_closed",
  "push_invalid_request",
  "push_key_reuse",
  "push_subscription_unavailable",
  "push_generation_conflict",
  "push_invalid_subscription",
  "push_installation_limit",
  "push_rate_limited",
]);

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isSubscription(value: unknown): value is Subscription {
  return (
    object(value) &&
    typeof value.installation_id === "string" &&
    uuid.test(value.installation_id) &&
    typeof value.idempotency_key === "string" &&
    uuid.test(value.idempotency_key) &&
    typeof value.base_generation === "number" &&
    Number.isSafeInteger(value.base_generation) &&
    value.base_generation >= 0 &&
    typeof value.endpoint === "string" &&
    value.endpoint.length <= 2048 &&
    endpoint.test(value.endpoint) &&
    typeof value.p256dh === "string" &&
    /^B[A-Za-z0-9_-]{86}$/.test(value.p256dh) &&
    typeof value.auth === "string" &&
    /^[A-Za-z0-9_-]{22}$/.test(value.auth) &&
    value.vapid_key_version === "v1"
  );
}
function decode(value: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/")), (ch) => ch.charCodeAt(0));
}

export function webPushTools({ ToolError }: { ToolError: typeof ErrorClass }) {
  const definition = (operation: Operation) => {
    const fields =
      operation === "register"
        ? properties
        : operation === "revoke"
          ? {
              installation_id: properties.installation_id,
              idempotency_key: properties.idempotency_key,
              base_generation: properties.base_generation,
            }
          : {};
    return {
      ...(["list", "status"].includes(operation) ? {} : { write: true }),
      humanOnly: true,
      humanCallerOnly: true,
      writerConnection: true,
      description: `Web push registry v1 ${operation}. Uses the authenticated browser partner and session. Returns no endpoints or encryption keys.`,
      inputSchema: { type: "object", additionalProperties: false, properties: fields, required: Object.keys(fields) },
      handler: async (c: Db, actor: Actor, args: unknown) => {
        if (
          actor.human !== true ||
          !["joe", "dell"].includes(String(actor.slug)) ||
          typeof actor.web_push_session !== "string" ||
          !/^[a-f0-9]{64}$/.test(actor.web_push_session)
        )
          throw new ToolError({ error: "push_session_required" });
        if (!object(args) || Object.keys(args).some((key) => !Object.hasOwn(fields, key)))
          throw new ToolError({ error: "push_invalid_request" });
        if (operation === "register") {
          if (!isSubscription(args)) throw new ToolError({ error: "push_invalid_subscription" });
          try {
            const raw = decode(args.p256dh);
            const auth = decode(args.auth);
            if (
              raw.length !== 65 ||
              auth.length !== 16 ||
              btoa(String.fromCharCode(...raw))
                .replace(/\+/g, "-")
                .replace(/\//g, "_")
                .replace(/=+$/, "") !== args.p256dh ||
              btoa(String.fromCharCode(...auth))
                .replace(/\+/g, "-")
                .replace(/\//g, "_")
                .replace(/=+$/, "") !== args.auth
            )
              throw new Error();
            await crypto.subtle.importKey("raw", raw, { name: "ECDH", namedCurve: "P-256" }, false, []);
          } catch {
            throw new ToolError({ error: "push_invalid_subscription" });
          }
        } else if (
          operation === "revoke" &&
          (typeof args.installation_id !== "string" ||
            !uuid.test(args.installation_id) ||
            typeof args.idempotency_key !== "string" ||
            !uuid.test(args.idempotency_key) ||
            typeof args.base_generation !== "number" ||
            !Number.isSafeInteger(args.base_generation) ||
            args.base_generation < 1)
        ) {
          throw new ToolError({ error: "push_invalid_request" });
        }
        try {
          if (operation === "status") {
            const value: unknown = (await c.query("select ops.web_push_transport('status','{}'::jsonb) as result"))
              .rows[0]?.result;
            if (!new Validator(deliverySchema as unknown as Schema).validate(value).valid) throw new Error();
            return value;
          }
          const r = await c.query("select ops.web_push_registry($1::text,$2::jsonb,$3::text) as result", [
            operation,
            JSON.stringify(args),
            actor.web_push_session,
          ]);
          const result: unknown = r.rows[0]?.result;
          if (!object(result) || result.ok !== true || result.schema_version !== "web-push-registry.v1")
            throw new Error();
          return result;
        } catch (error) {
          throw new ToolError({
            error: error instanceof Error && errors.has(error.message) ? error.message : "push_registry_unavailable",
          });
        }
      },
    };
  };
  return {
    "register-web-push-subscription-v1": definition("register"),
    "revoke-web-push-subscription-v1": definition("revoke"),
    "read-web-push-subscriptions-v1": definition("list"),
    "read-web-push-delivery-status-v1": definition("status"),
    "invalidate-web-push-session-v1": definition("logout"),
  };
}
