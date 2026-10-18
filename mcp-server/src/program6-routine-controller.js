// The Deal Room's exact Program 6 browser boundary. This module deliberately
// knows only fixed routes and fixed registered verbs; it never accepts a
// browser-selected verb, actor, tenant, state, database id, or plan internals.
// The actual database/authority choice stays in mcp.js's callTool(), so this is
// a route adapter rather than a second mutation implementation.
import { callTool } from "./mcp.js";
import { ToolError } from "./tools.js";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const HUMAN_REF = /^WR-[0-9]{1,12}$/;
// 0179's durable human reference. Do not substitute the synthetic FEEDBACK-*
// values used by older unit fakes: the browser route must accept the reference
// PostgreSQL actually persists and reads back.

// The browser can describe the bounded human-facing scope. It cannot choose
// what operational runbook to run, what dependency permits it, or how far it
// may go. This fixed, reviewed template is the only plan the typed route makes.
export const SAFE_READY_PLAN = Object.freeze({
  runbook_ref: "doctrine:runbook#diagnosis-checklist-in-order-2-minutes",
  dependency_refs: Object.freeze([]),
  recovery_ref: "safe:recovery:rollback",
  observability_ref: "safe:observe:logs",
  caps: Object.freeze({ max_steps: 2, max_duration_minutes: 30 }),
});

const ROUTES = Object.freeze([
  { method: "GET", pattern: /^\/api\/system-work\/current$/, tool: "current-work-requests", fields: [] },
  { method: "GET", pattern: /^\/api\/system-work\/(WR-[0-9]{1,12})$/, tool: "work-request-card", fields: [] },
  { method: "POST", pattern: /^\/api\/system-work\/report$/, tool: "report-problem", fields: ["idempotency_key", "situation", "title", "desired_outcome", "acceptance_criteria"] },
  { method: "POST", pattern: /^\/api\/system-work\/(WR-[0-9]{1,12})\/triage$/, tool: "review-and-triage", fields: ["idempotency_key", "base_version", "classification"] },
  { method: "POST", pattern: /^\/api\/system-work\/(WR-[0-9]{1,12})\/plan$/, tool: "propose-ready-plan", fields: ["idempotency_key", "base_version", "scope_summary"], fixedPlan: true },
  { method: "POST", pattern: /^\/api\/system-work\/(WR-[0-9]{1,12})\/plan\/accept$/, tool: "accept-ready-plan", fields: ["idempotency_key", "base_version", "plan_hash"] },
  { method: "POST", pattern: /^\/api\/system-work\/(WR-[0-9]{1,12})\/outcomes$/, tool: "propose-outcome-feedback", fields: ["idempotency_key", "base_version", "plan_hash", "criterion_results", "evidence_refs", "blocker_code", "result_summary", "observed_minutes", "interaction_surface", "heavy_session_used", "manual_context_transfers"] },
  { method: "POST", pattern: /^\/api\/system-work\/(WR-[0-9]{1,12})\/outcomes\/accept$/, tool: "accept-outcome-feedback", fields: ["idempotency_key", "base_version", "feedback_hash"] },
]);

function json(body, status = 200) { return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS }); }

function routeFor(method, pathname) {
  for (const route of ROUTES) {
    const match = route.pattern.exec(pathname);
    if (match) return { route, match };
  }
  return null;
}

function hasExactFields(value, fields) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).length === fields.length &&
    fields.every((key) => Object.hasOwn(value, key));
}

async function bodyFor(request, fields) {
  if (request.headers.get("content-type")?.split(";", 1)[0] !== "application/json") return { error: "content_type_required" };
  let body;
  try { body = await request.json(); } catch { return { error: "invalid_json" }; }
  if (!hasExactFields(body, fields)) return { error: "invalid_request_fields" };
  return { body };
}

function errorStatus(payload) {
  if (payload?.error === "version_conflict" || payload?.error === "key_reuse") return 409;
  if (["human_only", "caller_authority_field_forbidden"].includes(payload?.error)) return 403;
  if (payload?.error === "authority_connection_unavailable") return 503;
  return 400;
}

/**
 * Creates only the Program 6 typed router. Deal Room session, CSRF, Origin,
 * reauthentication, one-time challenges, and the mutation kill switch belong
 * to the injected authorizeAction boundary, which runs before every POST.
 */
export function createProgram6RoutineController(overrides = {}) {
  const dependencies = {
    callToolFn: callTool,
    // Fail closed until the Deal Room boundary explicitly injects its verifier.
    authorizeAction: async () => json({ error: "program6_browser_mutations_unavailable" }, 503),
    ...overrides,
  };

  return {
    async fetch(request, env, ctx, actor, session) {
      const url = new URL(request.url);
      const selected = routeFor(request.method, url.pathname);
      if (!selected) return json({ error: "not_found" }, 404);
      const { route, match } = selected;
      if (route.method !== request.method) return json({ error: "method_not_allowed" }, 405);

      // The card is a read-only, same-session view. Neither the browser nor the
      // route can select a different registered read verb.
      if (route.method === "GET") {
        if (route.tool === "current-work-requests") {
          try {
            const result = await dependencies.callToolFn(env, actor, route.tool, {}, "full");
            return json({ ok: true, data: result });
          } catch (error) {
            if (error instanceof ToolError) return json(error.payload, errorStatus(error.payload));
            return json({ error: "system_work_read_failed" }, 500);
          }
        }
        const human_ref = match[1];
        if (!HUMAN_REF.test(human_ref)) return json({ error: "not_found" }, 404);
        try {
          const result = await dependencies.callToolFn(env, actor, route.tool, { work_request: human_ref }, "full");
          return json({ ok: true, data: result });
        } catch (error) {
          if (error instanceof ToolError) return json(error.payload, errorStatus(error.payload));
          return json({ error: "system_work_read_failed" }, 500);
        }
      }

      const parsed = await bodyFor(request, route.fields);
      if (parsed.error) return json({ error: parsed.error }, 400);
      const body = parsed.body;
      const human_ref = route.tool === "report-problem" ? null : match[1];
      if (human_ref && !HUMAN_REF.test(human_ref)) return json({ error: "not_found" }, 404);
      const args = { ...body, ...(human_ref ? { human_ref } : {}), ...(route.fixedPlan ? SAFE_READY_PLAN : {}) };
      const authorization = await dependencies.authorizeAction({ request, env, ctx, actor, session, action: route.tool, args });
      if (authorization instanceof Response) return authorization;
      if (authorization?.response instanceof Response) return authorization.response;

      try {
        const result = await dependencies.callToolFn(env, actor, route.tool, args, "full");
        return json({ ok: true, data: result });
      } catch (error) {
        if (error instanceof ToolError) return json(error.payload, errorStatus(error.payload));
        return json({ error: "system_work_mutation_failed" }, 500);
      }
    },
  };
}
