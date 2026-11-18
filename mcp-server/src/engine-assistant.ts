// Send assistant saves to DoctorCRE Engine from the Worker.
import { ToolError } from "./tool-error.ts";

type EngineEnv = {
  ENGINE_URL?: string;
  ENGINE_ACCESS_CLIENT_ID?: string;
  ENGINE_ACCESS_CLIENT_SECRET?: string;
};

type Save =
  | {
      path: "/jobs/activities";
      id: "activity_id";
      body: {
        idempotency_key: string;
        kind: string;
        happened_at: string;
        summary: string;
        about: { deal_id: string }[];
      };
    }
  | {
      path: "/jobs/next-steps";
      id: "next_step_id";
      body: {
        idempotency_key: string;
        deal_id: string;
        what: string;
        owner?: string;
        due_on?: string;
        replaces: "current";
      };
    };

const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const errorSlug = /^[a-z][a-z0-9_]{0,63}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function saveAssistantChange(env: EngineEnv, partner: string, save: Save) {
  const url = URL.parse(env.ENGINE_URL ?? "");
  const clientId = env.ENGINE_ACCESS_CLIENT_ID;
  const secret = env.ENGINE_ACCESS_CLIENT_SECRET;
  if (
    url?.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash ||
    !clientId ||
    !secret
  )
    throw new ToolError({ error: "engine_assistant_configuration" });

  const body = JSON.stringify(save.body);
  const headers = {
    "Content-Type": "application/json",
    "CF-Access-Client-Id": clientId,
    "CF-Access-Client-Secret": secret,
    "Engine-Job": "agent",
    "Engine-For-User": partner,
  };
  const target = new URL(save.path, url);
  for (let attempt = 0; attempt < 3; attempt++) {
    const response = await fetch(target, {
      method: "POST",
      headers,
      body,
      redirect: "error",
      signal: AbortSignal.timeout(5_000),
    }).catch(() => null);
    if (!response) continue;
    if (response.status >= 500) {
      await response.body?.cancel().catch(() => {});
      continue;
    }
    const payload = await response.json().catch(() => null);
    const result = isRecord(payload) ? payload : null;
    if (!response.ok) {
      const code = result?.error;
      throw new ToolError({
        error: code === "key_reuse" ? code : "engine_assistant_refused",
        engine_status: response.status,
        ...(typeof code === "string" && errorSlug.test(code) ? { engine_error: code } : {}),
      });
    }
    const id = result?.[save.id];
    if (typeof id === "string" && uuid.test(id))
      return { id, ...(result?.replayed === true ? { replayed: true } : {}) };
    // A truncated success may follow a commit. The same key recovers its receipt.
  }
  throw new ToolError({ error: "engine_assistant_unavailable" });
}
