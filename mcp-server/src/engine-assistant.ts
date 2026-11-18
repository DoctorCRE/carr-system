// Send assistant saves to DoctorCRE Engine from the Worker.
import { ToolError } from "./tool-error.ts";

type EngineEnv = {
  ENGINE_URL?: string;
  ENGINE_ACCESS_CLIENT_ID?: string;
  ENGINE_ACCESS_CLIENT_SECRET?: string;
};

export function assistantOldRole(kind: unknown, id: string) {
  if (kind === "lead" || kind === "client" || kind === "vendor") return { kind, id };
  return null;
}

type OldRoleSubject = { old_role: NonNullable<ReturnType<typeof assistantOldRole>> };
type DealSubject = { deal_id: string };
type PartySubject = { party_id: string };

type Save =
  | {
      path: "/jobs/activities";
      id: "activity_id";
      body: {
        idempotency_key: string;
        kind: string;
        happened_at?: string;
        summary: string;
        about: (DealSubject | PartySubject)[];
      };
    }
  | {
      path: "/jobs/next-steps";
      id: "next_step_id";
      body: {
        idempotency_key: string;
        what: string;
        owner?: string;
        due_on?: string;
        replaces: "current";
      } & (DealSubject | OldRoleSubject);
    }
  | {
      path: "/jobs/next-steps/complete";
      id: "next_step_id";
      body: { idempotency_key: string } & (DealSubject | PartySubject | OldRoleSubject);
    }
  | {
      path: "/jobs/activities/deal-note";
      id: "activity_id";
      body: {
        idempotency_key: string;
        deal_id: string;
        text: string;
        plan?: { what: string; due_on: string };
      };
    };

const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const isUuid = (value: unknown): value is string => typeof value === "string" && uuid.test(value);
export const engineErrorSlug = /^[a-z][a-z0-9_]{0,63}$/;

function dealNoteReceipt(result: Record<string, unknown>, hasPlan: boolean) {
  const { next_step_id, next_step_required, proposal_id } = result;
  if (hasPlan)
    return isUuid(next_step_id) && next_step_required === false ? { next_step_id, next_step_required } : null;
  return typeof next_step_required === "boolean" && (proposal_id === null || isUuid(proposal_id))
    ? { proposal_id, next_step_required }
    : null;
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
    const payload: unknown = await response.json().catch(() => null);
    const result: Record<string, unknown> | null =
      typeof payload === "object" && payload !== null && !Array.isArray(payload)
        ? Object.fromEntries(Object.entries(payload))
        : null;
    if (!response.ok) {
      const code = result?.error;
      throw new ToolError({
        error: code === "key_reuse" ? code : "engine_assistant_refused",
        engine_status: response.status,
        ...(typeof code === "string" && engineErrorSlug.test(code) ? { engine_error: code } : {}),
      });
    }
    // A truncated success may follow a commit. The same key recovers its receipt.
    if (!result) continue;
    const id = result[save.id];
    if (!isUuid(id)) continue;
    const receipt = { id, ...(result.replayed === true ? { replayed: true } : {}) };
    if (save.path === "/jobs/activities/deal-note") {
      const note = dealNoteReceipt(result, save.body.plan !== undefined);
      if (!note) continue;
      return { ...receipt, ...note };
    }
    if ("old_role" in save.body) {
      if (!isUuid(result.party_id)) continue;
      return { ...receipt, party_id: result.party_id };
    }
    return receipt;
  }
  throw new ToolError({ error: "engine_assistant_unavailable" });
}
