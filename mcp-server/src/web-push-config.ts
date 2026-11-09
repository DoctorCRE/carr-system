// Read the public VAPID configuration for signed-in partners.

import type { ToolError as ErrorClass } from "./tool-error.ts";
import type { Actor, Db } from "./verb-contract.ts";

type ConfigReader = Db & { vapidPublicKey?: unknown };

export function webPushConfigTools({ ToolError }: { ToolError: typeof ErrorClass }) {
  return {
    "read-web-push-config-v1": {
      humanOnly: true,
      humanCallerOnly: true,
      description:
        "Read the configured v1 VAPID public applicationServerKey for a signed-in partner. Refuses absent or invalid configuration.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c: ConfigReader, actor: Actor, _args: unknown) => {
        if (actor.human !== true || !["joe", "dell"].includes(String(actor.slug)))
          throw new ToolError({ error: "push_session_required" });
        const key = c.vapidPublicKey;
        if (key === undefined || key === "") throw new ToolError({ error: "push_not_configured" });
        try {
          if (typeof key !== "string" || !/^[A-Za-z0-9_-]{87}$/.test(key)) throw new Error();
          const raw = Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/")), (ch) => ch.charCodeAt(0));
          if (
            raw.length !== 65 ||
            raw[0] !== 4 ||
            btoa(String.fromCharCode(...raw))
              .replace(/\+/g, "-")
              .replace(/\//g, "_")
              .replace(/=+$/, "") !== key
          )
            throw new Error();
          await crypto.subtle.importKey("raw", raw, { name: "ECDH", namedCurve: "P-256" }, false, []);
        } catch {
          throw new ToolError({ error: "push_config_invalid" });
        }
        return { ok: true, schema_version: "web-push-config.v1", public_key: key, vapid_key_version: "v1" };
      },
    },
  };
}
