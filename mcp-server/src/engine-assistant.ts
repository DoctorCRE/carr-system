// Send assistant saves to DoctorCRE Engine from the Worker.
import { isCalendarDate } from "./calendar-date.ts";
import { ToolError } from "./tool-error.ts";
import type { VerbInputs } from "./verb-contract.ts";

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
    }
  | {
      path: "/jobs/invoice-emails";
      id: "invoice_id";
      body: {
        idempotency_key: string;
        evidence_ref: string;
        sender: string;
        deal_name: string;
        occurred_at: string;
        client_name: string | null;
        property_address: string | null;
      };
    };

const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
export const engineErrorSlug = /^[a-z][a-z0-9_]{0,63}$/;

async function invoiceCaptureKey(evidenceRef: string): Promise<string> {
  // RFC UUIDv5 with the same URL namespace as the Python lead-stage job.
  const namespace = [0x6b, 0xa7, 0xb8, 0x11, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8];
  const input = new Uint8Array([...namespace, ...new TextEncoder().encode(evidenceRef)]);
  const digest = new DataView(await crypto.subtle.digest("SHA-1", input));
  digest.setUint8(6, (digest.getUint8(6) & 0x0f) | 0x50);
  digest.setUint8(8, (digest.getUint8(8) & 0x3f) | 0x80);
  const hex = Array.from(new Uint8Array(digest.buffer, 0, 16), (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export async function captureAssistantInvoice(
  env: EngineEnv,
  partner: string,
  args: VerbInputs["record-deal-invoice"],
) {
  const { native_ref, from_address, deal_name, occurred_at } = args;
  if (!uuid.test(args.idempotency_key))
    throw new ToolError({ error: "invalid_idempotency_key", field: "idempotency_key" });
  const occurred = Date.parse(occurred_at);
  if (
    !/^local-mail:\S/.test(native_ref) ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(from_address) ||
    !deal_name.trim() ||
    !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d\d:\d\d)$/.test(occurred_at) ||
    !isCalendarDate(occurred_at.slice(0, 10)) ||
    !Number.isFinite(occurred) ||
    occurred > Date.now()
  )
    throw new ToolError({ error: "invalid_engine_invoice_evidence" });

  const { id, replayed } = await saveAssistantChange(env, partner, {
    path: "/jobs/invoice-emails",
    id: "invoice_id",
    body: {
      idempotency_key: await invoiceCaptureKey(native_ref),
      evidence_ref: native_ref,
      sender: from_address.toLowerCase(),
      deal_name: deal_name.trim(),
      occurred_at,
      client_name: args.client_name?.trim() || null,
      property_address: args.property_address?.trim() || null,
    },
  });
  return { ok: true, invoice_id: id, evidence_ref: native_ref, ...(replayed && { replayed }) };
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
    "Engine-Job": save.path === "/jobs/invoice-emails" ? "mail_capture" : "agent",
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
        error: code === "key_reuse" && save.path !== "/jobs/invoice-emails" ? code : "engine_assistant_refused",
        engine_status: response.status,
        ...(typeof code === "string" && engineErrorSlug.test(code) ? { engine_error: code } : {}),
      });
    }
    const id = result?.[save.id];
    const validCapture =
      save.path !== "/jobs/invoice-emails" ||
      (response.status === 200 && result?.evidence_ref === save.body.evidence_ref);
    if (typeof id === "string" && uuid.test(id) && validCapture)
      return { id, ...(result?.replayed === true ? { replayed: true } : {}) };
    // A truncated success may follow a commit. The same key recovers its receipt.
  }
  throw new ToolError({ error: "engine_assistant_unavailable" });
}
