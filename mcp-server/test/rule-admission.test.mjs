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

for (const verb of ["teach", "amend-rule"]) {
  const args = verb === "teach"
    ? { idempotency_key: "scope-teach", statement: "fixture rule",
        human_quote: "fixture quote", enforcement_home: "core" }
    : { idempotency_key: "scope-amend", rule_id: RULE, base_version: 1,
        statement: "corrected wording", reason: "fixture correction" };

  for (const [shape, scope] of Object.entries({
    string: "shared", array: [], null: null, boolean: false, number: 1,
  })) {
    test(`${verb} refuses a ${shape} scope before any database write`, async () => {
      const c = fakeClient(sql => {
        if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
          return { rows: [{ status: "proposed", statement: "old wording",
            human_quote: null, scope: {}, version: 1 }] };
        if (/select version from rule/i.test(sql)) return { rows: [{ version: 1 }] };
        if (/insert into rule/i.test(sql)) return { rows: [{ id: RULE, personal_to: null }] };
        return { rows: [] };
      });
      await assert.rejects(
        () => TOOLS[verb].handler(c, ACTOR, { ...args, scope }),
        e => {
          assert.ok(e instanceof ToolError);
          assert.equal(e.payload.error, "invalid_object");
          assert.equal(e.payload.field, "scope");
          assert.match(e.payload.hint, /scope.*object/i);
          return true;
        },
      );
      assert.equal(c.calls.some(x => /\b(insert|update|delete)\b/i.test(x.sql)), false);
    });
  }

  for (const scope of [{}, { section: "fixture" }, undefined]) {
    test(`${verb} accepts ${scope === undefined ? "omitted" : JSON.stringify(scope)} scope`, async () => {
      const c = fakeClient(sql => {
        if (/insert into rule/i.test(sql)) return { rows: [{ id: RULE, personal_to: null }] };
        if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
          return { rows: [{ status: "proposed", statement: "old wording",
            human_quote: null, scope: { section: "original" }, version: 1 }] };
        if (/select version from rule where id=\$1 for update/i.test(sql))
          return { rows: [{ version: 1 }] };
        if (/select version from rule where id=\$1$/i.test(sql))
          return { rows: [{ version: 2 }] };
        return { rows: [] };
      });
      const out = await executeRegisteredTool(c, ACTOR, verb, {
        ...args, ...(scope === undefined ? {} : { scope }),
      });
      assert.equal(out.ok, true);
      const write = c.calls.find(x => verb === "teach"
        ? /insert into rule/i.test(x.sql) : /update rule set statement=/i.test(x.sql));
      const stored = JSON.parse(write.params[verb === "teach" ? 3 : 2]);
      assert.deepEqual(stored, scope ?? (verb === "teach" ? {} : { section: "original" }));
    });
  }
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
  // 0901: a taught gate rule derives policy_kind and control_keys from its
  // teach record, so only the act itself is required.
  assert.deepEqual(new Set(tool.inputSchema.required), new Set([
    "idempotency_key", "rule_id", "reason",
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
        human_quote: "Joe said it", scope: { section: "original" }, version: 2 }] };
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


for (const statement of [undefined, "corrected statement"])
test(`amend-rule receipts additive scope and ${statement ? "wording" : "unchanged wording"} with the same identity`, async () => {
  const oldScope = { source: "import", section: "record-layer", nested: { quote_absent: true } };
  const scope = { ...oldScope, moment: "Before amending a rule" };
  const c = fakeClient(sql => {
    if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "approved statement",
        human_quote: "Joe said it", scope: oldScope, version: 2 }] };
    if (/select version from rule where id=\$1 for update/i.test(sql))
      return { rows: [{ version: 2 }] };
    if (/select ops\.amend_rule_scope/i.test(sql)) return { rows: [{ result: {
      ok: true, rule_version_after: 3, amendment_receipt_id: "scope-receipt", replayed: false,
    } }] };
    return { rows: [] };
  });
  const out = await executeRegisteredTool(c, ACTOR, "amend-rule", {
    idempotency_key: "scope-backfill", rule_id: RULE, base_version: 2,
    scope, ...(statement ? { statement } : {}), reason: "Backfill selection condition from the rule text",
  });
  assert.equal(out.rule_id, RULE);
  assert.equal(out.status, "active");
  assert.equal(out.version, 3);
  assert.equal(out.amendment_receipt_id, "scope-receipt");
  assert.deepEqual(out.changed, statement ? ["statement", "scope"] : ["scope"]);
  const call = c.calls.find(x => /select ops\.amend_rule_scope/i.test(x.sql));
  assert.deepEqual(call.params, [RULE, JSON.stringify(scope), statement || "approved statement", 2,
    "scope-backfill", "Backfill selection condition from the rule text"]);
  assert.equal(c.calls.some(x => /update rule/i.test(x.sql)), false);
  const event = c.calls.find(x => /insert into event/i.test(x.sql));
  assert.ok(event);
});

for (const scope of [
  {}, { source: "changed" }, { source: "import", nested: { a: 2 } },
  { source: "import", nested: { a: 1, b: 2 } },
  { source: "import", nested: { a: 1 }, verbs: ["teach", "amend-rule"] },
]) test(`active scope rewrite refuses ${JSON.stringify(scope)}`, async () => {
  const c = fakeClient(sql => {
    if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "approved", human_quote: "quote",
        scope: { source: "import", nested: { a: 1 }, verbs: ["teach"] }, version: 2 }] };
    if (/select version from rule where id=\$1 for update/i.test(sql))
      return { rows: [{ version: 2 }] };
    return { rows: [] };
  });
  await assert.rejects(() => executeRegisteredTool(c, ACTOR, "amend-rule", {
    idempotency_key: "scope-rewrite", rule_id: RULE, base_version: 2, scope,
    reason: "Try rewriting approved scope",
  }), e => e instanceof ToolError && e.payload.error === "active_rule_scope_frozen");
  assert.equal(c.calls.some(x => /select ops\.amend_rule_|update rule|insert into event/i.test(x.sql)), false);
});

for (const [label, args, error] of [
  ["stale version", { base_version: 1 }, "version_conflict"],
  ["missing reason", { reason: " " }, "reason_required"],
  ["changed active quote", { human_quote: "new quote" }, "human_quote_immutable"],
]) test(`active scope backfill refuses ${label} before writing`, async () => {
  const c = fakeClient(sql => {
    if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "approved", human_quote: "quote", scope: {}, version: 2 }] };
    if (/select version from rule where id=\$1 for update/i.test(sql))
      return { rows: [{ version: 2 }] };
    return { rows: [] };
  });
  await assert.rejects(() => executeRegisteredTool(c, ACTOR, "amend-rule", {
    idempotency_key: "scope-check", rule_id: RULE, base_version: 2,
    scope: { moment: "Before a rule edit" }, reason: "Backfill scope", ...args,
  }), e => e instanceof ToolError && e.payload.error === error);
  assert.equal(c.calls.some(x => /select ops\.amend_rule_|update rule|insert into event/i.test(x.sql)), false);
});


test("scope key order alone is a no-op", async () => {
  const c = fakeClient(sql => {
    if (/select status, statement, human_quote, scope, version from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "approved", human_quote: "quote",
        scope: { source: "import", nested: { a: 1, b: 2 } }, version: 2 }] };
    if (/select version from rule where id=\$1 for update/i.test(sql)) return { rows: [{ version: 2 }] };
    return { rows: [] };
  });
  await assert.rejects(() => executeRegisteredTool(c, ACTOR, "amend-rule", {
    idempotency_key: "scope-order", rule_id: RULE, base_version: 2,
    scope: { nested: { b: 2, a: 1 }, source: "import" }, reason: "Reordered JSON",
  }), e => e instanceof ToolError && e.payload.error === "no_change");
  assert.equal(c.calls.some(x => /select ops\.amend_rule_|update rule|insert into event/i.test(x.sql)), false);
});


test("retire-rule returns a typed refusal for a database lifecycle refusal", async () => {
  const c = fakeClient(sql => {
    if (/select status, statement, personal_to from rule/i.test(sql))
      return { rows: [{ status: "active", statement: "fixture", personal_to: null }] };
    if (/select ops\.retire_rule/i.test(sql))
      throw Object.assign(new Error("system rule retirement requires Joe authority"), { code: "P0001" });
    return { rows: [] };
  });
  await assert.rejects(() => executeRegisteredTool(c, ACTOR, "retire-rule", {
    idempotency_key: "retire-refusal", rule_id: RULE, reason: "withdraw fixture",
  }), e => e instanceof ToolError && e.payload.error === "rule_retirement_refused"
    && e.payload.message === "system rule retirement requires Joe authority");
  assert.equal(c.calls.some(x => /insert into event/i.test(x.sql)), false);
});
