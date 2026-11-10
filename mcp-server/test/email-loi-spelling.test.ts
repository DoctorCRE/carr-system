import assert from "node:assert/strict";
import test from "node:test";
import { ToolError } from "../src/tool-error.ts";

function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const legacy = object(await import(new URL("../src/tools.js", import.meta.url).href));
async function executeRegisteredTool(db: object, actor: object, name: string, args: object) {
  const execute = legacy.executeRegisteredTool;
  assert.ok(typeof execute === "function");
  return object(await execute(db, actor, name, args));
}

const row = (phase: string, detail: string, deal_type = "lease") => ({
  deal_id: "deal",
  name: "Synthetic deal",
  client_id: "client",
  version: 1,
  deal_type,
  phase,
  activity_id: "activity",
  activity_version: 1,
  summary: "Synthetic email",
  detail,
  occurred_at: "2026-10-20T12:00:00Z",
});
const check = (phase: string, evidence_quote: string) =>
  executeRegisteredTool(
    { query: async () => ({ rows: [row(phase, evidence_quote)] }) },
    {},
    "preview-email-deal-progress",
    { deal: "deal", activity_id: "activity", evidence_quote },
  );

test("dotted LOI evidence supports the same reviewable deal progress as LOI", async () => {
  assert.equal((await check("site_selection", "We received the L.O.I. yesterday.")).to_phase, "negotiation");
  assert.equal((await check("negotiation", "The L.O.I. was signed by both parties.")).to_phase, "legal");
});

test("dotted LOI does not let an unsigned lease qualify as lease evidence", async () => {
  await assert.rejects(
    check("legal", "The L.O.I. was signed and the lease is attached."),
    (error) => error instanceof ToolError && error.payload?.error === "email_deal_quote_invalid",
  );
});

test("unrelated words do not qualify as an LOI milestone", async () => {
  await assert.rejects(
    check("site_selection", "The soil report arrived yesterday."),
    (error) => error instanceof ToolError && error.payload?.error === "email_deal_quote_invalid",
  );
});
