// Encrypt and submit one Web Push request with a bounded, caller-owned HTTP transport.
import { buildPushPayload } from "@block65/webcrypto-web-push";

export type PushDelivery = {
  endpoint: string;
  p256dh: string;
  auth: string;
  notification_id: string;
  target_ref: string;
  ttl: number;
};
export type PushKeys = { publicKey: string; privateKey: string; subject: string };
export type PushOutcome = {
  outcome: "provider_accepted" | "failed" | "auth" | "gone" | "retry" | "unknown";
  retry_after: number;
};
export async function submitPush(
  delivery: PushDelivery,
  keys: PushKeys,
  timeout: number,
  http: typeof fetch = fetch,
): Promise<PushOutcome> {
  if (
    !/^https:\/\/(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com|[a-z0-9-]+\.notify\.windows\.com|web\.push\.apple\.com)\/[^\s#]+$/.test(
      delivery.endpoint,
    )
  )
    return { outcome: "failed", retry_after: 0 };
  let request: Awaited<ReturnType<typeof buildPushPayload>>;
  const signal = AbortSignal.timeout(timeout);
  try {
    request = await buildPushPayload(
      {
        data: {
          schema_version: "web-push.v1",
          notification_id: delivery.notification_id,
          target_ref: delivery.target_ref,
          category: "Partner review needed",
        },
        options: { ttl: delivery.ttl, topic: delivery.notification_id.replaceAll("-", ""), urgency: "normal" },
      },
      { endpoint: delivery.endpoint, expirationTime: null, keys: { p256dh: delivery.p256dh, auth: delivery.auth } },
      keys,
    );
  } catch {
    return { outcome: "failed", retry_after: 0 };
  }
  try {
    signal.throwIfAborted();
    const response = await http(delivery.endpoint, { ...request, redirect: "error", signal });
    const header = response.headers.get("Retry-After");
    const seconds =
      header && /^\d+$/.test(header) ? Number(header) : header ? (Date.parse(header) - Date.now()) / 1000 : 0;
    const retry_after = Number.isFinite(seconds) ? Math.max(0, Math.min(86400, Math.ceil(seconds))) : 0;
    await response.body?.cancel();
    const status = response.status;
    return {
      outcome:
        status >= 200 && status < 300
          ? "provider_accepted"
          : status === 401 || status === 403
            ? "auth"
            : status === 404 || status === 410
              ? "gone"
              : status === 429 || status >= 500
                ? "retry"
                : "failed",
      retry_after,
    };
  } catch {
    return { outcome: "unknown", retry_after: 0 };
  }
}
