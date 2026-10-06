// Phase 1 admission gate: capture is free, authority is explicit, and the
// database plus server independently refuse activation without a complete
// admitted contract.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const ACTOR = { id: "11111111-1111-4111-8111-111111111111", slug: "joe", human: true };
const RULE = "22222222-2222-4222-8222-222222222222";

function fakeClient(route) {
  const calls = [];
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (/select request_hash, response/i.test(sql)) return { rows: [] };
      if (/insert into tool_call/i.test(sql) || /insert into event/i.test(sql)) return { rows: [] };
      return route(sql, params, calls);
    },
  };
}

test("admit-rule is an explicit human-only authority verb with all four D-04 dimensions", () => {
  const tool = TOOLS["admit-rule"];
  // humanOnly LABEL RETIRED (WR-000019 slice S1, 2026-08-27): dead since
  // executeRegisteredTool stopped reading it 2026-08-26 (decision dc57f62d);
  // this slice drops the stale declaration from tools.js.
  assert.equal(tool.humanOnly, undefined);
  const required = new Set(tool.inputSchema.required);
  for (const field of ["rule_id", "enforcement_class", "binding_moment",
                       "applicability", "projection", "reachability", "input_contract",
                       "fixture_refs", "enforcement_points", "reason"])
    assert.equal(required.has(field), true, `${field} must be required`);
});

test("teach captures guidance intake in the same envelope but leaves it non-authoritative", async () => {
  const c = fakeClient((sql) => {
    if (/insert into rule/i.test(sql)) return { rows: [{ id: RULE, personal_to: null }] };
    return { rows: [] };
  });
  await executeRegisteredTool(c, ACTOR, "teach", {
    idempotency_key: "capture-1", statement: "fixture rule", human_quote: "make this a rule",
    enforcement_home: "core",
  });
  const intake = c.calls.find(x => /insert into ops\.guidance_intake/i.test(x.sql));
  assert.ok(intake, "teach must create an intake row");
  assert.match(intake.sql, /'captured'/i);
  assert.equal(intake.params.includes(`rule:${RULE}`), true,
               "intake must point back to the proposed rule");
});

test("activate-rule is retired so approval can never be separated from enforcement", async () => {
  const c = fakeClient(() => ({ rows: [] }));
  await assert.rejects(
    () => executeRegisteredTool(c, ACTOR, "activate-rule", {
      idempotency_key: "activate-1", rule_id: RULE,
    }),
    e => e instanceof ToolError && e.payload.error === "direct_rule_activation_retired",
  );
  assert.equal(c.calls.some(x => /update rule set status='active'/i.test(x.sql)), false);
  assert.equal(c.calls.some(x => /insert into ops\.authority_receipt/i.test(x.sql)), false);
});

test("approve-rule is one human authority act, not separate admission and activation", () => {
  const tool = TOOLS["approve-rule"];
  // humanOnly LABEL RETIRED, authorityOnly UNCHANGED (WR-000019 slice S1,
  // 2026-08-27) — see the admit-rule test above for the same retirement.
  assert.equal(tool.humanOnly, undefined);
  assert.equal(tool.authorityOnly, true);
  assert.deepEqual(new Set(tool.inputSchema.required), new Set([
    "idempotency_key", "rule_id", "policy_kind", "control_keys", "reason",
  ]));
  assert.deepEqual(tool.inputSchema.properties.policy_kind.enum,
    ["machine_enforceable", "human_only"]);
  assert.equal(tool.inputSchema.properties.enforcement_points, undefined,
    "callers must not claim implementation/test evidence");
});

test("approve-rule delegates one atomic transaction and only returns enforced active coverage", async () => {
  const c = fakeClient((sql) => {
    if (/select ops\.approve_rule/i.test(sql)) return { rows: [{ result: {
      ok: true, rule_id: RULE, policy_status: "active", enforcement_status: "hard_enforced",
      installed_controls: ["platform_metering_pre_dispatch"], pending_controls: [],
      approval_receipt_id: "33333333-3333-4333-8333-333333333333",
    } }] };
    return { rows: [] };
  });
  const out = await executeRegisteredTool(c, ACTOR, "approve-rule", {
    idempotency_key: "approve-1", rule_id: RULE,
    policy_kind: "machine_enforceable",
    control_keys: ["platform_metering_pre_dispatch"], reason: "Joe approved it",
  });
  assert.equal(out.policy_status, "active");
  assert.equal(out.enforcement_status, "hard_enforced");
  assert.equal(c.calls.filter(x => /select ops\.approve_rule/i.test(x.sql)).length, 1);
  assert.equal(c.calls.some(x => /insert into ops\.rule_admission/i.test(x.sql)), false,
    "the MCP layer must not reproduce a partial client-side transaction");
  assert.equal(c.calls.some(x => /update rule set status='active'/i.test(x.sql)), false,
    "activation belongs inside the authority function");
});

test("amend-rule is now an authority verb (WR-000019 slice S10)", () => {
  // The proposed-rule direct-update path needed no authority principal; the
  // new active-rule statement path requires the SAME Joe-authority connection
  // as approve-rule/retire-rule (ops.amend_rule_statement calls
  // ops.authority_actor_slug() exactly like they do), so the whole verb moved
  // behind authorityOnly, matching its two siblings.
  assert.equal(TOOLS["amend-rule"].authorityOnly, true);
});

test("amend-rule still corrects a PROPOSED rule directly, no authority function involved", async () => {
  const c = fakeClient((sql) => {
    if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
      return { rows: [{ status: "proposed", statement: "old wording",
        human_quote: null, scope: {}, version: 1 }] };
    if (/select version from rule where id=\$1 for update/i.test(sql))
      return { rows: [{ version: 1 }] };
    if (/select version from rule where id=\$1$/i.test(sql))
      return { rows: [{ version: 2 }] };
    return { rows: [] };
  });
  const out = await executeRegisteredTool(c, ACTOR, "amend-rule", {
    idempotency_key: "amend-proposed-1", rule_id: RULE, base_version: 1,
    statement: "corrected wording", reason: "fixed a typo",
  });
  assert.deepEqual(out.changed, ["statement"]);
  assert.equal(out.status, "proposed");
  assert.equal(c.calls.some(x => /update rule set statement=\$1, human_quote=\$2, scope=\$3/i.test(x.sql)), true);
  assert.equal(c.calls.some(x => /select ops\.amend_rule_statement/i.test(x.sql)), false,
    "a still-proposed rule keeps its direct-update path; the guarded SQL function is for ACTIVE rules only");
});

test("amend-rule calls the guarded ops.amend_rule_statement to correct an ACTIVE rule's wording", async () => {
  const c = fakeClient((sql) => {
    if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "approved statement",
        human_quote: "Joe said it", scope: {}, version: 2 }] };
    if (/select version from rule where id=\$1 for update/i.test(sql))
      return { rows: [{ version: 2 }] };
    if (/select ops\.amend_rule_statement/i.test(sql))
      return { rows: [{ result: { ok: true, replayed: false, rule_id: RULE,
        rule_version_before: 2, rule_version_after: 3,
        amendment_receipt_id: "55555555-5555-4555-8555-555555555555" } }] };
    return { rows: [] };
  });
  const out = await executeRegisteredTool(c, ACTOR, "amend-rule", {
    idempotency_key: "amend-active-1", rule_id: RULE, base_version: 2,
    statement: "approved statement, corrected", reason: "fixed a wording defect, same meaning",
  });
  assert.equal(out.status, "active");
  assert.deepEqual(out.changed, ["statement"]);
  assert.equal(out.version, 3);
  assert.equal(out.amendment_receipt_id, "55555555-5555-4555-8555-555555555555");
  assert.equal(c.calls.filter(x => /select ops\.amend_rule_statement/i.test(x.sql)).length, 1);
  assert.equal(c.calls.some(x => /update rule set statement=\$1, human_quote=\$2, scope=\$3/i.test(x.sql)), false,
    "the active-rule path must go through the guarded function, never a direct UPDATE");
});

test("amend-rule still refuses to change an active rule's scope or quote", async () => {
  const cScope = fakeClient((sql) => {
    if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "approved statement",
        human_quote: "Joe said it", scope: {}, version: 2 }] };
    if (/select version from rule where id=\$1 for update/i.test(sql))
      return { rows: [{ version: 2 }] };
    return { rows: [] };
  });
  await assert.rejects(
    () => executeRegisteredTool(cScope, ACTOR, "amend-rule", {
      idempotency_key: "amend-active-scope-1", rule_id: RULE, base_version: 2,
      scope: { section: "different" }, reason: "try to widen scope under the old approval",
    }),
    e => e instanceof ToolError && e.payload.error === "active_rule_scope_frozen",
  );
  assert.equal(cScope.calls.some(x => /select ops\.amend_rule_statement/i.test(x.sql)), false);

  const cQuote = fakeClient((sql) => {
    if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "approved statement",
        human_quote: null, scope: {}, version: 2 }] };
    if (/select version from rule where id=\$1 for update/i.test(sql))
      return { rows: [{ version: 2 }] };
    return { rows: [] };
  });
  await assert.rejects(
    () => executeRegisteredTool(cQuote, ACTOR, "amend-rule", {
      idempotency_key: "amend-active-quote-1", rule_id: RULE, base_version: 2,
      human_quote: "a quote that was never said", reason: "try to fabricate testimony",
    }),
    e => e instanceof ToolError && e.payload.error === "active_rule_quote_frozen",
  );
  assert.equal(cQuote.calls.some(x => /select ops\.amend_rule_statement/i.test(x.sql)), false);
});

test("retire-rule is Joe-authority receipt-backed, never a direct status update", async () => {
  assert.equal(TOOLS["retire-rule"].authorityOnly, true);
  const c = fakeClient((sql) => {
    if (/select status, statement, personal_to from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "approved statement", personal_to: null }] };
    if (/select ops\.retire_rule/i.test(sql))
      return { rows: [{ result: { ok: true, status: "retired",
        retirement_receipt_id: "44444444-4444-4444-8444-444444444444" } }] };
    return { rows: [] };
  });
  const out = await executeRegisteredTool(c, ACTOR, "retire-rule", {
    idempotency_key: "retire-1", rule_id: RULE, reason: "Joe withdrew it",
  });
  assert.equal(out.status, "retired");
  assert.equal(c.calls.some(x => /select ops\.retire_rule/i.test(x.sql)), true);
  assert.equal(c.calls.some(x => /update rule set status='retired'/i.test(x.sql)), false);
});

test("applicable-rules delegates finite applicability selection to the policy compiler", async () => {
  const c = fakeClient((sql) => /from ops\.applicable_rules/i.test(sql)
    ? { rows: [{ rule_id: RULE, statement: "fixture", enforcement_class: "machine_enforceable" }] }
    : { rows: [] });
  const out = await executeRegisteredTool(c, ACTOR, "applicable-rules", {
    workflow: "nightly", surface: "worker", tier: "operational",
  });
  assert.equal(out.count, 1);
  assert.equal(out.rules[0].rule_id, RULE);
});
