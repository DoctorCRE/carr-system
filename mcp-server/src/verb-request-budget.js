import { isKnownPartner, organizationTenantForActor } from "./identity.js";

const PERIOD_MS = 60_000;
const LIMITS = Object.freeze({ human: 60, agent: 1200 });

export class VerbRequestBudget {
  constructor(state) { this.storage = state.storage; }

  async fetch(request) {
    const { kind } = await request.json();
    if (!Object.hasOwn(LIMITS, kind)) return new Response(null, { status: 400 });
    return this.storage.transaction(async tx => {
      const now = Date.now();
      const windowStart = Math.floor(now / PERIOD_MS) * PERIOD_MS;
      let counter = await tx.get("counter");
      if (!counter || counter.windowStart !== windowStart) counter = { windowStart, count: 0 };
      const retryAfter = Math.max(1, Math.ceil((windowStart + PERIOD_MS - now) / 1000));
      if (counter.count >= LIMITS[kind]) return Response.json({ allowed: false, retryAfter });
      counter.count += 1;
      await tx.put("counter", counter);
      if (counter.count === 1) await tx.setAlarm(windowStart + PERIOD_MS);
      return Response.json({ allowed: true });
    });
  }

  async alarm() {
    await this.storage.transaction(async tx => {
      const counter = await tx.get("counter");
      if (!counter || counter.windowStart + PERIOD_MS <= Date.now()) await tx.delete("counter");
      else await tx.setAlarm(counter.windowStart + PERIOD_MS);
    });
  }
}

function refused(error, retryAfter) {
  return Response.json({ error }, {
    status: 429,
    headers: { "retry-after": String(retryAfter), "cache-control": "no-store" },
  });
}

export async function verbRequestBudgetRefusal(env, actor, sessionKey) {
  const kind = actor?.human === true && isKnownPartner(actor.slug) ? "human" : "agent";
  const principal = kind === "human" ? [actor.slug, sessionKey || "oauth"]
    : [actor?.slug, actor?.sponsoring_human_slug || null];
  try {
    if (typeof actor?.slug !== "string" || !actor.slug) throw new Error("Missing authenticated principal");
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
      JSON.stringify([organizationTenantForActor(actor), kind, principal])));
    const key = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    const binding = env.VERB_REQUEST_BUDGET;
    const response = await binding.get(binding.idFromName(key)).fetch(new Request("https://budget.internal/consume", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind }),
    }));
    if (!response.ok) throw new Error("Budget unavailable");
    const result = await response.json();
    if (result.allowed === true) return null;
    if (result.allowed !== false || !Number.isInteger(result.retryAfter) || result.retryAfter < 1)
      throw new Error("Invalid budget response");
    return refused("verb_request_budget_exceeded", result.retryAfter);
  } catch {
    // A 5xx would schedule database failure tracing before this gate recovers.
    return refused("verb_request_budget_unavailable", 60);
  }
}
