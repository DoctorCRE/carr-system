import assert from "node:assert/strict";
import test from "node:test";

import { workRequestIntakeTools } from "../src/work-request-intake.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const ACTOR = { id: "10000000-0000-4000-8000-000000000001", slug: "codex", human: false };
const HASH = `sha256:${"a".repeat(64)}`;
const PROPOSAL = {
  idempotency_key: "10000000-0000-4000-8000-000000000002", human_ref: "WR-000125", base_version: 4,
  predecessor_plan_hash: HASH, scope_summary: "Replace the accepted plan only after its exact predecessor is bound.",
  runbook_ref: "doctrine:runbook#wr125-canonical-ownership-plan-lifecycle-recovery", dependency_refs: [],
  recovery_ref: "safe:wr125:rollback", observability_ref: "safe:wr125:readback",
  caps: { max_steps: 6, max_duration_minutes: 120 },
};

function tools(events = []) {
  return workRequestIntakeTools({
    ToolError,
    withEnvelope: async (_c, _actor, _verb, _args, fn) => fn(),
    writeEvent: async (_c, _actor, verb, subjectType, subjectId, fields) => events.push({ verb, subjectType, subjectId, fields }),
  });
}
