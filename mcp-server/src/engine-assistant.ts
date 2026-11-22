// Assistant requests to DoctorCRE Engine from the Worker.
import { derivedIdempotencyKey } from "./derived-key.ts";
import engineClient from "./engine-client.json" with { type: "json" };
import { isInvoiceEvidence } from "./invoice-evidence.ts";
import { ToolError } from "./tool-error.ts";
import type { VerbInputs } from "./verb-contract.ts";

export type EngineEnv = {
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

type AgentSave =
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

type Save =
  | (AgentSave & { job?: "agent" })
  | {
      job: "mail_capture";
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
const isUuid = (value: unknown): value is string => typeof value === "string" && uuid.test(value);
export const engineErrorSlug = /^[a-z][a-z0-9_]{0,63}$/;

export async function captureAssistantInvoice(
  env: EngineEnv,
  partner: string,
  args: VerbInputs["record-deal-invoice"],
) {
  const { native_ref, from_address, deal_name, occurred_at } = args;
  if (!isUuid(args.idempotency_key))
    throw new ToolError({ error: "invalid_idempotency_key", field: "idempotency_key" });
  if (!isInvoiceEvidence(args, Date.now()) || !/(?:Z|[+-]\d\d:\d\d)$/.test(occurred_at))
    throw new ToolError({ error: "invalid_engine_invoice_evidence" });

  const { id, replayed } = await saveAssistantChange(env, partner, {
    job: "mail_capture",
    path: "/jobs/invoice-emails",
    id: "invoice_id",
    body: {
      idempotency_key: derivedIdempotencyKey("record-deal-invoice", args.idempotency_key),
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

function dealNoteReceipt(result: Record<string, unknown>, hasPlan: boolean) {
  const { next_step_id, next_step_required, proposal_id } = result;
  if (hasPlan)
    return isUuid(next_step_id) && next_step_required === false ? { next_step_id, next_step_required } : null;
  return typeof next_step_required === "boolean" && (proposal_id === null || isUuid(proposal_id))
    ? { proposal_id, next_step_required }
    : null;
}

function engineJobRequest(env: EngineEnv, path: string, job: string, partner: string) {
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

  return {
    target: new URL(path, url),
    headers: {
      "User-Agent": engineClient.userAgent,
      "CF-Access-Client-Id": clientId,
      "CF-Access-Client-Secret": secret,
      "Engine-Job": job,
      "Engine-For-User": partner,
    },
  };
}

export type DealWatchTarget = {
  watch_id: string;
  lifecycle_version: number;
  deal_id: string;
  subscriber: string;
  since: string;
};

function engineResult(payload: unknown): Record<string, unknown> | null {
  return typeof payload === "object" && payload !== null && !Array.isArray(payload)
    ? Object.fromEntries(Object.entries(payload))
    : null;
}

const isReviewKeyList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((key: unknown) => typeof key === "string" && /^[a-f0-9]{16}$/.test(key));

export async function readEngineCalendarAgenda(env: EngineEnv, partner: string) {
  const { target, headers } = engineJobRequest(env, "/jobs/calendar/agenda", "agent", partner);
  const response = await fetch(target, { headers, redirect: "manual", signal: AbortSignal.timeout(5_000) });
  if (!response.ok) {
    await response.body?.cancel().catch(() => {});
    throw new Error("engine_calendar_unavailable");
  }
  const payload: unknown = await response.json();
  if (payload === null) return null;
  const snapshot = engineResult(payload);
  if (
    !snapshot ||
    !Array.isArray(snapshot.follow_up_reviews) ||
    !Array.isArray(snapshot.warnings) ||
    !isReviewKeyList(snapshot.confirmed) ||
    !isReviewKeyList(snapshot.dismissed)
  )
    throw new Error("invalid_engine_calendar_response");
  return { snapshot, confirmed: new Set(snapshot.confirmed), dismissed: new Set(snapshot.dismissed) };
}

function isMissingEngineRecord(status: number, result: Record<string, unknown> | null) {
  // The router's generic 404 also says not_found, without a correlation ID.
  return status === 404 && result?.error === "not_found" && isUuid(result.correlation_id);
}

export async function readAssistantDeal(
  env: EngineEnv,
  partner: string,
  ref: string,
): Promise<{ type: "deal"; id: string }> {
  const id = ref.toLowerCase();
  const { target, headers } = engineJobRequest(env, `/jobs/deals/${id}`, "agent", partner);
  const response = await fetch(target, {
    method: "GET",
    headers,
    redirect: "manual",
    signal: AbortSignal.timeout(5_000),
  }).catch(() => {
    throw new ToolError({ error: "engine_assistant_unavailable" });
  });
  const result = engineResult(await response.json().catch(() => null));
  if (isMissingEngineRecord(response.status, result)) throw new ToolError({ error: "subject_not_found", ref });
  if (response.status >= 500 || (response.ok && result?.deal_id !== id))
    throw new ToolError({ error: "engine_assistant_unavailable" });
  if (!response.ok) {
    const code = result?.error;
    throw new ToolError({
      error: "engine_assistant_refused",
      engine_status: response.status,
      ...(typeof code === "string" && engineErrorSlug.test(code) ? { engine_error: code } : {}),
    });
  }
  return { type: "deal", id };
}

function readDealWatchField(value: unknown) {
  const change = engineResult(value);
  const { change_id, table_name, action, field, old_value, new_value, actor, for_user, changed_at } = change ?? {};
  if (
    !change ||
    typeof change_id !== "string" ||
    !/^\d+$/.test(change_id) ||
    typeof table_name !== "string" ||
    (action !== "insert" && action !== "update" && action !== "delete") ||
    typeof field !== "string" ||
    !/^[a-z][a-z0-9_]*$/.test(field) ||
    typeof actor !== "string" ||
    (for_user !== null && typeof for_user !== "string") ||
    !("old_value" in change) ||
    !("new_value" in change) ||
    typeof changed_at !== "string" ||
    !Number.isFinite(Date.parse(changed_at))
  )
    throw new Error("invalid_engine_watch_change");
  return { change_id, table_name, action, field, old_value, new_value, actor, for_user, changed_at };
}

type EngineWatchRead = { status: "ok"; changes: ReturnType<typeof readDealWatchField>[] } | { status: "missing" };

export async function readEngineDealWatch(
  env: EngineEnv,
  watch: DealWatchTarget,
  timeout: number,
): Promise<EngineWatchRead> {
  const { target, headers } = engineJobRequest(
    env,
    `/jobs/deal-watches/deal/${watch.deal_id}`,
    "agent",
    watch.subscriber,
  );
  target.searchParams.set("subscriber", watch.subscriber);
  target.searchParams.set("since", watch.since);
  const response = await fetch(target, { headers, redirect: "manual", signal: AbortSignal.timeout(timeout) }).catch(
    () => {
      throw new Error("engine_watch_unavailable");
    },
  );
  if (!response.ok) {
    if (response.status === 404) {
      const refusal = engineResult(await response.json().catch(() => null));
      if (isMissingEngineRecord(response.status, refusal)) return { status: "missing" };
    } else {
      await response.body?.cancel().catch(() => {});
    }
    throw new Error("engine_watch_unavailable");
  }
  const payload = engineResult(await response.json().catch(() => null));
  if (
    payload?.kind !== "deal" ||
    payload.id !== watch.deal_id ||
    payload.deal_id !== watch.deal_id ||
    !Array.isArray(payload.changes)
  )
    throw new Error("invalid_engine_watch_response");
  return {
    status: "ok",
    changes: payload.changes.map(readDealWatchField),
  };
}

export async function saveAssistantChange(env: EngineEnv, partner: string, save: Save) {
  const { target, headers } = engineJobRequest(env, save.path, save.job ?? "agent", partner);
  const body = JSON.stringify(save.body);
  let fetchError: unknown;
  for (let attempt = 0; attempt < 3; attempt++) {
    let response: Response;
    try {
      response = await fetch(target, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body,
        redirect: "manual",
        signal: AbortSignal.timeout(5_000),
      });
    } catch (error) {
      fetchError = error;
      continue;
    }
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel().catch(() => {});
      throw new ToolError({ error: "engine_assistant_refused", engine_status: response.status });
    }
    if (response.status >= 500) {
      await response.body?.cancel().catch(() => {});
      continue;
    }
    const result = engineResult(await response.json().catch(() => null));
    if (!response.ok) {
      const code = result?.error;
      if (
        (save.path === "/jobs/activities/deal-note" || save.path === "/jobs/next-steps") &&
        "deal_id" in save.body &&
        isMissingEngineRecord(response.status, result)
      )
        throw new ToolError({ error: "subject_not_found", ref: save.body.deal_id });
      throw new ToolError({
        error: code === "key_reuse" ? code : "engine_assistant_refused",
        engine_status: response.status,
        ...(typeof code === "string" && engineErrorSlug.test(code) ? { engine_error: code } : {}),
      });
    }
    // A truncated success may follow a commit. The same key recovers its receipt.
    if (!result) continue;
    if (save.job === "mail_capture") {
      if (typeof result.evidence_ref !== "string") continue;
      if (result.evidence_ref !== save.body.evidence_ref)
        throw new ToolError({ error: "engine_assistant_refused", engine_status: response.status });
    }
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
  const unavailable = new ToolError({ error: "engine_assistant_unavailable" });
  if (fetchError instanceof Error) {
    const redact = (text: string) =>
      text
        .replaceAll(headers["CF-Access-Client-Secret"], "[redacted]")
        .replaceAll(headers["CF-Access-Client-Id"], "[redacted]");
    unavailable.cause = { name: redact(fetchError.name), message: redact(fetchError.message) };
  }
  throw unavailable;
}
