// WR-000113 — the R03 notification verbs.
//
// LIBRARY ONLY: no shebang and no main-module construct (the SCAC
// inventory scans for those by substring, so they are described, never spelled).
//
// acknowledge-notification is a plain `write: true` routine-writer verb,
// matching the carr_writer EXECUTE grant on ops.acknowledge_notification.
// notification-feed declares `writerConnection: true` and NO write flag:
// ops.notification_feed_facts resolves the caller's own actor from the
// transaction-local setting, which only the writer path installs -- on the
// reader connection it would resolve nobody.

import type { Actor, Db, VerbInputs, WithEnvelope, WriteEvent } from "./verb-contract.ts";
import type { ToolError as ToolErrorClass } from "./tool-error.ts";

type Delivery = { channel: "in_app" | "device"; state: "pending" | "delivered" | "suppressed_quiet_hours" | "failed" };
type Notification = {
  id: string; severity: "action_required" | "completion" | "failure";
  reason: string; subject_type: string; subject_ref: string; deep_link: string;
  created_at: string; read_at: string | null; delivery: Delivery[];
};

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.replace(/^ +| +$/g, "").length > 0;
}

function isDelivery(value: unknown): value is Delivery {
  return isObject(value) && (value.channel === "in_app" || value.channel === "device")
    && typeof value.state === "string"
    && ["pending", "delivered", "suppressed_quiet_hours", "failed"].includes(value.state);
}

function isNotificationRoute(link: string, subjectType: string, subjectRef: string): boolean {
  if (/^\/([A-Za-z0-9._~-][A-Za-z0-9._~/-]*)?$/.test(link)) {
    return !link.split('/').some(segment => segment === '.' || segment === '..');
  }
  if (!UUID.test(subjectRef)) return false;
  if (subjectType === 'deal') return link === `/deals?deal=${subjectRef}`;
  if (subjectType === 'lease') {
    const match = /^\/clients\?record=([^?&#]+)$/.exec(link);
    return match !== null && UUID.test(match[1]);
  }
  return false;
}

function isNotification(value: unknown): value is Notification {
  return isObject(value) && typeof value.id === "string"
    && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value.id)
    && typeof value.severity === "string"
    && ["action_required", "completion", "failure"].includes(value.severity)
    && isText(value.reason) && Array.from(value.reason).length <= 500
    && isText(value.subject_type) && isText(value.subject_ref)
    && typeof value.deep_link === "string" && value.deep_link.length <= 301
    && isNotificationRoute(value.deep_link, value.subject_type, value.subject_ref)
    && isText(value.created_at) && (value.read_at === null || isText(value.read_at))
    && Array.isArray(value.delivery) && value.delivery.every(isDelivery)
    && new Set(value.delivery.map(entry => entry.channel)).size === value.delivery.length;
}

function isPreferenceFacts(value: unknown): value is {
  ok: true; exists: boolean; device_opt_in: boolean; quiet_hours_start: string | null;
  quiet_hours_end: string | null; timezone: string; version: number; quiet_now: boolean;
} {
  return isObject(value) && value.ok === true && typeof value.exists === "boolean"
    && typeof value.device_opt_in === "boolean" && typeof value.quiet_now === "boolean"
    && (value.quiet_hours_start === null || isText(value.quiet_hours_start))
    && (value.quiet_hours_end === null || isText(value.quiet_hours_end))
    && (value.quiet_hours_start === null) === (value.quiet_hours_end === null)
    && isText(value.timezone) && typeof value.version === "number"
    && Number.isSafeInteger(value.version) && value.version >= 1;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Pure shaper over ops.notification_preference_facts. */
export function notificationPreferenceProjection(prefs: unknown, ToolError: typeof ToolErrorClass) {
  if (!isPreferenceFacts(prefs)) {
    throw new ToolError({ error: "notification_preferences_unavailable" });
  }
  return {
    ok: true,
    exists: prefs.exists,
    device_opt_in: prefs.device_opt_in,
    quiet_hours_start: prefs.quiet_hours_start,
    quiet_hours_end: prefs.quiet_hours_end,
    timezone: prefs.timezone,
    version: prefs.version,
    quiet_now: prefs.quiet_now,
  };
}

/**
 * Pure shaper over ops.notification_feed_facts.
 *
 * WR-000116: `prefs` is the SECOND definer's answer, read on the same
 * connection inside the same read-only transaction, so the two reads see one
 * snapshot and cannot disagree about the preference row. It is OUTPUT-ONLY:
 * notification-feed's inputSchema does not move for it.
 */
export function notificationFeedProjection(facts: unknown, prefs: unknown, ToolError: typeof ToolErrorClass) {
  if (!isObject(facts) || facts.ok !== true || typeof facts.unread_count !== "number"
      || !Number.isSafeInteger(facts.unread_count) || facts.unread_count < 0
      || !Array.isArray(facts.notifications) || !facts.notifications.every(isNotification)
      || facts.notifications.length > 200
      || new Set(facts.notifications.map(row => row.id)).size !== facts.notifications.length
      || facts.notifications.filter(row => row.read_at === null).length > facts.unread_count) {
    throw new ToolError({ error: "notification_feed_unavailable" });
  }
  // The feed must not stop working because the preference door did: an absent
  // or malformed prefs answer yields false and never throws.
  const quietNow = isPreferenceFacts(prefs) && prefs.quiet_now;
  return {
    ok: true,
    unread_count: facts.unread_count,
    quiet_now: quietNow,
    notifications: facts.notifications.map(row => ({
      id: row.id,
      severity: row.severity,
      reason: row.reason,
      subject_type: row.subject_type,
      subject_ref: row.subject_ref,
      deep_link: row.deep_link,
      created_at: row.created_at,
      read_at: row.read_at,
      delivery: row.delivery.map((entry) => ({ channel: entry.channel, state: entry.state })),
      quiet_suppressed: quietNow
        || row.delivery.some(entry => entry.state === "suppressed_quiet_hours"),
    })),
  };
}

export function notificationTools({ withEnvelope, writeEvent, ToolError }: { withEnvelope: WithEnvelope; writeEvent: WriteEvent; ToolError: typeof ToolErrorClass }) {
  return {
    "acknowledge-notification": {
      write: true,
      description: "Mark one notification addressed to the acting actor as read. It writes ops.notification_read and nothing else: no notification row and no source task row moves.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        notification_id: { type: "string" },
      }, required: ["idempotency_key", "notification_id"] },
      handler: async (c: Db, actor: Actor, args: VerbInputs["acknowledge-notification"]) =>
        withEnvelope(c, actor, "acknowledge-notification", args, async () => {
          if (!UUID.test(String(args.notification_id || ""))) {
            throw new ToolError({ error: "notification_not_found" });
          }
          const r = await c.query(
            "select ops.acknowledge_notification($1::uuid,$2::uuid) as acknowledged",
            [args.notification_id, args.idempotency_key]);
          const result = r.rows[0]?.acknowledged;
          if (!result || result.ok !== true) {
            throw new ToolError({ error: result?.reason_id || "notification_not_found",
              notification_id: args.notification_id });
          }
          if (result.deduplicated !== true) {
            await writeEvent(c, actor, "acknowledge-notification", "notification",
              args.notification_id, {
                field: "read",
                new: { read_at: result.read_at },
                cause: "automation_job",
                idempotency_key: args.idempotency_key,
              });
          }
          return { ok: true, notification_id: args.notification_id,
            read_at: result.read_at, deduplicated: result.deduplicated === true };
        }),
    },

    "notification-feed": {
      writerConnection: true,
      description: "Read the acting actor's own notification feed and unread count. There is no recipient argument: the function resolves the caller itself.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        after: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 200 },
      }, required: [] },
      handler: async (c: Db, _a: Actor, args: VerbInputs["notification-feed"]) => {
        const r = await c.query(
          "select ops.notification_feed_facts($1::timestamptz,$2::integer) as facts",
          [args.after ?? null, args.limit ?? 50]);
        const p = await c.query("select ops.notification_preference_facts() as prefs");
        return notificationFeedProjection(r.rows[0]?.facts, p.rows[0]?.prefs, ToolError);
      },
    },

    // WR-000116. ZERO properties with additionalProperties:false is this read's
    // whole defence: it is what makes "no caller argument names an actor"
    // unforgeable rather than merely absent. writerConnection with NO write
    // flag, because ops.notification_preference_facts is stable and resolves
    // the caller from a context only the writer path installs.
    "read-notification-preferences": {
      writerConnection: true,
      description: "Read the acting actor's own notification preferences and whether quiet hours cover this moment. There is no actor argument: the function resolves the caller itself.",
      inputSchema: { type: "object", additionalProperties: false, properties: {}, required: [] },
      handler: async (c: Db, _a: Actor, _args: VerbInputs["read-notification-preferences"]) => {
        const r = await c.query("select ops.notification_preference_facts() as prefs");
        return notificationPreferenceProjection(r.rows[0]?.prefs, ToolError);
      },
    },

    // NOT authorityOnly: the app calls this as the signed-in partner, who holds
    // no sponsor-scoped authority binding, and an authority-only door is one
    // the product cannot open however correct its SQL is.
    "set-notification-preference": {
      write: true,
      writerConnection: true,
      description: "Set the acting actor's own notification preferences under a compare-and-swap on base_version, with a fresh idempotency key. Quiet hours are two local times plus a timezone, set together or cleared together.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        base_version: { type: "integer", minimum: 1 },
        device_opt_in: { type: "boolean" },
        quiet_hours_start: { type: "string" },
        quiet_hours_end: { type: "string" },
        timezone: { type: "string" },
        clear_quiet_hours: { type: "boolean" },
      }, required: ["idempotency_key", "base_version"] },
      handler: async (c: Db, actor: Actor, args: VerbInputs["set-notification-preference"]) =>
        withEnvelope(c, actor, "set-notification-preference", args, async () => {
          const r = await c.query(
            `select ops.set_notification_preference($1::integer,$2::boolean,$3::time,$4::time,
                                                    $5::text,$6::boolean,$7::uuid) as applied`,
            [args.base_version, args.device_opt_in ?? null,
              args.quiet_hours_start ?? null, args.quiet_hours_end ?? null,
              args.timezone ?? null, args.clear_quiet_hours ?? null,
              args.idempotency_key]);
          const result = r.rows[0]?.applied;
          if (!result || result.ok !== true) {
            throw new ToolError({ error: result?.reason_id || "notification_preference_not_set",
              ...(result?.current_version === undefined
                ? {} : { current_version: result.current_version }) });
          }
          if (result.deduplicated !== true) {
            await writeEvent(c, actor, "set-notification-preference", "notification_preference",
              args.idempotency_key, {
                field: "notification_preference",
                new: {
                  device_opt_in: result.device_opt_in,
                  quiet_hours_start: result.quiet_hours_start,
                  quiet_hours_end: result.quiet_hours_end,
                  timezone: result.timezone,
                  version: result.version,
                },
                cause: "automation_job",
                idempotency_key: args.idempotency_key,
              });
          }
          return {
            ok: true,
            exists: true,
            device_opt_in: result.device_opt_in === true,
            quiet_hours_start: result.quiet_hours_start ?? null,
            quiet_hours_end: result.quiet_hours_end ?? null,
            timezone: result.timezone ?? "UTC",
            version: result.version,
            deduplicated: result.deduplicated === true,
          };
        }),
    },
  };
}
