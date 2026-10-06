import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool, TOOLS } from "../src/tools.js";

const AGENT = { id: "10000000-0000-0000-0000-000000000021", slug: "codex", human: false, via: "test" };
const AUTHORITY_AGENT = { id: "10000000-0000-0000-0000-000000000022", slug: "joe", human: true, via: "test" };

async function rejected(fn) {
  try { await fn(); assert.fail("expected refusal"); }
  catch (e) {
    if (e instanceof ToolError) return e.payload;
    throw e;
  }
}

// A minimal fake DB standing in for migrations/0602's write and read doors, plus the two upstream stores retire-workflow-cutover-plan
// composes: ops.workflow_acceptance (accept-workflow's store) and
// ops.legacy_schedule_disable_receipt (disable-legacy-schedule's store).
// Mirrors resource-observation.test.mjs's fake shape (mcp-server/test/
// resource-observation.test.mjs): match on the leading SQL text, keep state
// in plain JS structures, refuse the same way the real SECURITY DEFINER
// functions refuse.
class WorkflowCutoverFake {
  constructor() {
    this.calls = []; this.toolCalls = new Map();
    this.plans = new Map(); this.transitions = []; this.callers = new Map();
    this.slices = []; this.sliceRegistry = new Map();
    this.acceptances = new Map(); // id -> {workflow_key, workflow_version, mode, status}
    this.disableReceipts = new Map(); // id -> {workflow_key}
    this._planSeq = 0; this._callerSeq = 0; this._sliceSeq = 0;
  }

  seedAcceptance(id, row) { this.acceptances.set(id, row); }
  seedDisableReceipt(id, row) { this.disableReceipts.set(id, row); }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });

    if (sql.startsWith("select request_hash, response")) {
      const row = this.toolCalls.get(params[0]);
      return { rows: row ? [row] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };

    if (sql.includes("ops.open_workflow_cutover_plan")) {
      const [workflowKey, workflowVersion, recoveryPlan, idempotencyKey] = params;
      const existing = [...this.plans.values()].find(p => p.idempotency_key === idempotencyKey);
      if (existing) return { rows: [existing] };
      if (!workflowKey) throw new Error("workflow_key_required");
      if (!recoveryPlan) throw new Error("recovery_plan_required");
      const prior = [...this.plans.values()]
        .find(p => p.workflow_key === workflowKey && p.workflow_version === workflowVersion && p.status === "active");
      if (prior) { prior.status = "superseded"; prior.supersede_reason = "superseded by a new open"; }
      this._planSeq += 1;
      const id = `50000000-0000-0000-0000-${String(this._planSeq).padStart(12, "0")}`;
      const row = { id, workflow_key: workflowKey, workflow_version: workflowVersion, stage: "read_legacy",
        status: "active", recovery_plan: recoveryPlan, superseded_by: null, idempotency_key: idempotencyKey };
      this.plans.set(id, row);
      if (prior) prior.superseded_by = id;
      return { rows: [row] };
    }

    if (sql.includes("ops.cancel_workflow_cutover_plan")) {
      const [planId, reason, idempotencyKey] = params;
      const plan = this.plans.get(planId);
      if (plan && plan.cancel_idempotency_key === idempotencyKey) return { rows: [plan] };
      if (!reason) throw new Error("reason_required");
      if (!plan) throw new Error("workflow_cutover_plan_not_found");
      if (plan.status !== "active") throw new Error("workflow_cutover_plan_not_active");
      plan.status = "cancelled"; plan.cancel_idempotency_key = idempotencyKey;
      return { rows: [plan] };
    }

    if (sql.includes("ops.advance_workflow_cutover_stage")) {
      const [planId, toStage, evidenceRef, reason, idempotencyKey] = params;
      const replayed = this.transitions.find(t => t.idempotency_key === idempotencyKey);
      if (replayed) return { rows: [this.plans.get(replayed.plan_id)] };
      if (toStage === "retired") throw new Error("retired_stage_requires_retire_door");
      if (!reason) throw new Error("reason_required");
      const plan = this.plans.get(planId);
      if (!plan) return { rows: [] };
      if (plan.status !== "active") throw new Error("workflow_cutover_plan_not_active");
      const stages = ["read_legacy", "build_projection", "shadow_compare", "single_write_authority",
        "cutover", "monitor", "recovery_ready"];
      const fromIdx = stages.indexOf(plan.stage);
      const toIdx = stages.indexOf(toStage);
      if (toIdx < 0) throw new Error("workflow_cutover_stage_invalid");
      if (toIdx !== fromIdx + 1) throw new Error("workflow_cutover_stage_not_sequential");
      if (["shadow_compare", "single_write_authority", "cutover"].includes(toStage)) {
        if (!evidenceRef) throw new Error(`evidence_ref_required_for_stage: ${toStage}`);
        const acc = this.acceptances.get(evidenceRef);
        const wantMode = toStage === "shadow_compare" ? "shadow" : "canary";
        if (!acc || acc.workflow_key !== plan.workflow_key || acc.workflow_version !== plan.workflow_version ||
            acc.mode !== wantMode || acc.status !== "accepted")
          throw new Error(`${wantMode}_acceptance_evidence_not_found_or_not_accepted`);
      }
      plan.stage = toStage;
      this.transitions.push({ plan_id: planId, from_stage: stages[fromIdx], to_stage: toStage,
        evidence_ref: evidenceRef, reason, idempotency_key: idempotencyKey });
      return { rows: [plan] };
    }

    if (sql.includes("ops.retire_workflow_cutover_plan")) {
      const [planId, disableReceiptId, reason, idempotencyKey] = params;
      if (params.length !== 4) throw new Error("retire takes exactly four arguments; the census is derived in SQL");
      const replayed = this.transitions.find(t => t.idempotency_key === idempotencyKey);
      if (replayed) return { rows: [this.plans.get(replayed.plan_id)] };
      if (!reason) throw new Error("reason_required");
      if (!disableReceiptId) throw new Error("disable_receipt_id_required");
      const plan = this.plans.get(planId);
      if (!plan) return { rows: [] };
      if (plan.status !== "active") throw new Error("workflow_cutover_plan_not_active");
      if (plan.stage !== "recovery_ready") throw new Error("workflow_cutover_plan_not_recovery_ready");
      const receipt = this.disableReceipts.get(disableReceiptId);
      if (!receipt) throw new Error("legacy_schedule_disable_receipt_not_found");
      if (receipt.workflow_key !== plan.workflow_key) throw new Error("legacy_schedule_disable_receipt_workflow_mismatch");
      // Mirrors the real ops.retire_workflow_cutover_plan: the census is
      // derived in SQL and is unavailable until the census store lands, so
      // this check always refuses, ordered last.
      throw new Error("workflow_cutover_retire_refused_census_unknown");
      plan.stage = "retired";
      this.transitions.push({ plan_id: planId, from_stage: "recovery_ready", to_stage: "retired",
        evidence_ref: disableReceiptId, reason, idempotency_key: idempotencyKey });
      return { rows: [plan] };
    }

    if (sql.includes("ops.record_workflow_caller")) {
      const [workflowKey, workflowVersion, callerLocator, callerKind, status, blockedReason, evidenceRef] = params;
      if (!workflowKey) throw new Error("workflow_key_required");
      if (!callerLocator) throw new Error("caller_locator_required");
      if (status === "done" && !evidenceRef) throw new Error("caller_done_requires_evidence_ref");
      if (status === "blocked" && !blockedReason) throw new Error("caller_blocked_requires_reason");
      const key = `${workflowKey}::${workflowVersion}::${callerLocator}`;
      this._callerSeq += 1;
      const id = this.callers.get(key)?.id ?? `51000000-0000-0000-0000-${String(this._callerSeq).padStart(12, "0")}`;
      const row = { id, workflow_key: workflowKey, workflow_version: workflowVersion, caller_locator: callerLocator,
        caller_kind: callerKind, status, blocked_reason: blockedReason, evidence_ref: evidenceRef };
      this.callers.set(key, row);
      return { rows: [row] };
    }

    if (sql.includes("ops.workflow_cutover_board")) {
      const [workflowKey, workflowVersion] = params;
      const plan = [...this.plans.values()]
        .find(p => p.workflow_key === workflowKey && p.workflow_version === workflowVersion && p.status === "active");
      const callers = [...this.callers.values()]
        .filter(c => c.workflow_key === workflowKey && c.workflow_version === workflowVersion);
      const counts = {};
      for (const c of callers) counts[c.status] = (counts[c.status] ?? 0) + 1;
      const board = {
        schema: "doctorcre-v5-r02-workflow-cutover-board.v1",
        workflow_key: workflowKey, workflow_version: workflowVersion,
        plan: plan ? { id: plan.id, stage: plan.stage, status: plan.status, recovery_plan: plan.recovery_plan,
          superseded_by: plan.superseded_by } : null,
        stage_history: this.transitions.filter(t => plan && t.plan_id === plan.id),
        caller_counts: counts,
        callers: callers.map(c => ({ caller_locator: c.caller_locator, caller_kind: c.caller_kind,
          status: c.status, blocked_reason: c.blocked_reason, evidence_ref: c.evidence_ref })),
      };
      return { rows: [{ board }] };
    }

    if (sql.includes("ops.register_slice_checkable_done")) {
      const [sliceId, criteriaJson] = params;
      const criteria = JSON.parse(criteriaJson);
      if (this.sliceRegistry.has(sliceId)) throw new Error("slice_checkable_done_already_registered");
      if (new Set(criteria.map(c => c.criterion)).size !== criteria.length)
        throw new Error("criteria_must_be_distinct");
      this.sliceRegistry.set(sliceId, criteria);
      return { rows: criteria };
    }

    if (sql.includes("ops.mark_slice_progress") || sql.includes("ops.mark_slice_completion")) {
      const complete = sql.includes("ops.mark_slice_completion");
      const [sliceId, statusOrReceipt, receiptOrReason] = params;
      const status = complete ? "complete" : statusOrReceipt;
      const criteriaReceiptJson = complete ? statusOrReceipt : receiptOrReason;
      const idempotencyKey = complete ? params[3] : params[4];
      const existing = this.slices.find(s => s.idempotency_key === idempotencyKey);
      if (existing) return { rows: [existing] };
      if (!complete && !["in_progress", "blocked"].includes(status))
        throw new Error("slice_progress_status_invalid");
      const registered = this.sliceRegistry.get(sliceId);
      if (!registered) throw new Error("slice_completion_unknown_slice_id");
      const submitted = JSON.parse(criteriaReceiptJson);
      const names = submitted.map(el => el.criterion);
      if (new Set(names).size !== names.length) throw new Error("slice_completion_duplicate_criterion");
      if (names.length !== registered.length || !registered.every(r => names.includes(r.criterion)))
        throw new Error("slice_completion_criteria_set_mismatch");
      const computed = submitted.map(el => {
        const binding = registered.find(r => r.criterion === el.criterion);
        const acc = el.evidence_ref ? this.acceptances.get(el.evidence_ref) : null;
        const pass = Boolean(binding.evidence_kind === "acceptance" && acc && acc.status === "accepted" &&
          acc.workflow_key === binding.workflow_key && acc.workflow_version === binding.workflow_version &&
          acc.mode === binding.acceptance_mode);
        return { criterion: el.criterion, evidence_ref: el.evidence_ref ?? null, pass };
      });
      if (complete && !computed.every(el => el.pass))
        throw new Error("slice_completion_complete_requires_every_criterion_proven");
      this._sliceSeq += 1;
      const row = { id: `52000000-0000-0000-0000-${String(this._sliceSeq).padStart(12, "0")}`,
        slice_id: sliceId, status, criteria_receipt: computed, idempotency_key: idempotencyKey,
        created_at: "2026-09-24T12:00:00Z" };
      this.slices.push(row);
      return { rows: [row] };
    }

    if (sql.includes("ops.read_slice_done_state")) {
      const [sliceId] = params;
      return { rows: [{ state: { slice_id: sliceId, registered: this.sliceRegistry.has(sliceId),
        criteria: (this.sliceRegistry.get(sliceId) ?? []).map(c => ({ criterion: c.criterion,
          evidence_kind: c.evidence_kind, live_check_candidate: null, candidate_passes: false })),
        held_by_partner: false, latest_mark: null, release_members: [] } }] };
    }

    // 0628 bind doors: nine positional arguments, the seventh being
    // bound_member_id. The door refuses a shipped_release naming no member;
    // mirrored here so the verb's refusal envelope is exercised.
    if (sql.includes("ops.bind_slice_criterion_evidence") || sql.includes("ops.rebind_slice_criterion_evidence")) {
      if (params.length !== 9) throw new Error(`bind doors take nine arguments, got ${params.length}`);
      const [sliceId, criterion, kind, source, key, writeReason, memberId, reason] = params;
      this.bindCalls = [...(this.bindCalls ?? []), params];
      if (kind === "shipped_release" && !memberId)
        throw new Error("shipped_release_binding_requires_this_slice_member: (none)");
      return { rows: [{ id: "53000000-0000-0000-0000-000000000001", slice_id: sliceId, criterion,
        evidence_kind: kind, live_check_source: source, live_check_key: key, write_required_reason: writeReason,
        bound_member_id: memberId, bound_via: sql.includes("rebind") ? "authority" : "automation", reason,
        created_at: "2026-09-25T00:00:00Z" }] };
    }

    // 0628 round 3: automation proposes; a partner confirms in a batch.
    if (sql.includes("ops.propose_slice_completion")) {
      const [sliceId, receipt, reason] = params;
      this.proposeCalls = [...(this.proposeCalls ?? []), params];
      if (sliceId === "V5-HELD") throw new Error("slice_mark_held_by_partner: V5-HELD");
      return { rows: [{ id: "55000000-0000-0000-0000-000000000001", slice_id: sliceId,
        criteria_receipt: JSON.parse(receipt).map(el => ({ ...el, pass: true })), reason,
        proposed_by_actor_slug: "joe-local", created_at: "2026-09-25T00:00:00Z" }] };
    }
    if (sql.includes("ops.confirm_slice_completions")) {
      const [sliceIds, reason, key, expectedJson] = params;
      this.confirmCalls = [...(this.confirmCalls ?? []), params];
      if (!reason) throw new Error("reason_required");
      const expected = expectedJson ? JSON.parse(expectedJson) : {};
      return { rows: [...new Set(sliceIds)].sort().map(sliceId => sliceId === "V5-HELD"
        ? { slice_id: sliceId, outcome: "held", mark_id: null, proposal_id: null, criteria_receipt: null }
        : expected[sliceId] && expected[sliceId] !== "70000000-0000-0000-0000-000000000001"
          ? { slice_id: sliceId, outcome: "proposal_superseded", mark_id: null, proposal_id: "p1", criteria_receipt: null }
          : { slice_id: sliceId, outcome: "confirmed", mark_id: `mark-${key}`, proposal_id: "p1", criteria_receipt: [] }) };
    }
    if (sql.includes("ops.pending_slice_completion_proposals")) {
      return { rows: [{ pending: [{ slice_id: "V5-F08", proposal_id: "p1", passes_now: true }] }] };
    }

    if (sql.includes("ops.read_slice_completion")) {
      const [sliceId] = params;
      const rows = this.slices.filter(s => s.slice_id === sliceId);
      const latest = rows.at(-1);
      return { rows: latest ? [latest] : [{}] };
    }

    throw new Error(`WorkflowCutoverFake: unhandled query: ${sql}`);
  }
}

test("open-workflow-cutover-plan starts at read_legacy and requires a stated recovery plan", async () => {
  const client = new WorkflowCutoverFake();
  const result = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000001",
    workflow_key: "lead-board-nightly", workflow_version: 1,
    recovery_plan: "revert to the legacy launchd job by re-enabling com.carr.lead-board-nightly",
  });
  assert.equal(result.ok, true);
  assert.equal(result.stage, "read_legacy");
  assert.equal(result.status, "active");
});

test("open-workflow-cutover-plan refuses a missing recovery plan (Q116: a bounded recovery path is required up front)", async () => {
  const client = new WorkflowCutoverFake();
  await assert.rejects(
    () => executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
      idempotency_key: "60000000-0000-0000-0000-000000000002",
      workflow_key: "lead-board-nightly", workflow_version: 1, recovery_plan: "",
    }),
    (e) => e instanceof ToolError && (e.payload.error === "missing_required" || e.payload.error === "value_too_short"),
  );
});

test("Q153 stale-plan supersession: opening a second plan for the same workflow supersedes the first", async () => {
  const client = new WorkflowCutoverFake();
  const first = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000003",
    workflow_key: "renewal-feed", workflow_version: 1, recovery_plan: "re-enable legacy schedule",
  });
  const second = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000004",
    workflow_key: "renewal-feed", workflow_version: 1, recovery_plan: "re-enable legacy schedule v2",
  });
  assert.notEqual(second.plan_id, first.plan_id);
  const firstRow = client.plans.get(first.plan_id);
  assert.equal(firstRow.status, "superseded");
  assert.equal(firstRow.superseded_by, second.plan_id);
  const secondRow = client.plans.get(second.plan_id);
  assert.equal(secondRow.status, "active");
});

test("Q116: advance-workflow-cutover-stage refuses to skip a stage", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000005",
    workflow_key: "calendar-prebrief", workflow_version: 1, recovery_plan: "revert",
  });
  const payload = await rejected(() => executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
    idempotency_key: "60000000-0000-0000-0000-000000000006",
    plan_id: opened.plan_id, to_stage: "cutover", reason: "trying to skip ahead",
  }));
  assert.match(payload.detail, /workflow_cutover_stage_not_sequential/);
});

test("Q116: advance to shadow_compare requires an accepted shadow acceptance for the exact workflow identity", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000007",
    workflow_key: "calendar-prebrief", workflow_version: 1, recovery_plan: "revert",
  });
  await executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
    idempotency_key: "60000000-0000-0000-0000-000000000008",
    plan_id: opened.plan_id, to_stage: "build_projection", reason: "legacy read complete",
  });
  const payload = await rejected(() => executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
    idempotency_key: "60000000-0000-0000-0000-000000000009",
    plan_id: opened.plan_id, to_stage: "shadow_compare", evidence_ref: "missing-acceptance", reason: "trying anyway",
  }));
  assert.match(payload.detail, /shadow_acceptance_evidence_not_found_or_not_accepted/);
  client.seedAcceptance("70000000-0000-0000-0000-000000000001",
    { workflow_key: "calendar-prebrief", workflow_version: 1, mode: "shadow", status: "accepted" });
  const advanced = await executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
    idempotency_key: "60000000-0000-0000-0000-000000000010",
    plan_id: opened.plan_id, to_stage: "shadow_compare",
    evidence_ref: "70000000-0000-0000-0000-000000000001", reason: "shadow parity checks pass",
  });
  assert.equal(advanced.stage, "shadow_compare");
});

test("advance-workflow-cutover-stage can never reach stage=retired -- that door is retire-workflow-cutover-plan alone", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000011",
    workflow_key: "x", workflow_version: 1, recovery_plan: "revert",
  });
  await assert.rejects(
    () => executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
      idempotency_key: "60000000-0000-0000-0000-000000000012",
      plan_id: opened.plan_id, to_stage: "retired", reason: "trying to shortcut retirement",
    }),
    (e) => e instanceof ToolError && e.payload.error === "retired_stage_requires_retire_door",
  );
});

test("every door that can change what a live workflow may enqueue is authority-only; writers keep caller and progress records", () => {
  for (const name of ["open-workflow-cutover-plan", "advance-workflow-cutover-stage", "cancel-workflow-cutover-plan",
    "retire-workflow-cutover-plan", "register-slice-checkable-done", "mark-slice-completion"]) {
    assert.equal(TOOLS[name].authorityOnly, true, `${name} must be authority-only`);
    assert.equal(TOOLS[name].write, true);
  }
  for (const name of ["record-workflow-caller", "mark-slice-progress", "workflow-cutover-board", "read-slice-completion"])
    assert.equal(TOOLS[name].authorityOnly, undefined, `${name} should not be authority-only`);
  // 0628: the partner override doors are authority-only; the automated
  // seat's doors ride the writer connection (the database refuses any actor
  // outside ops.slice_marker_seat).
  for (const name of ["rebind-slice-criterion-evidence", "set-slice-mark-hold", "confirm-slice-completions"]) {
    assert.equal(TOOLS[name].authorityOnly, true, `${name} must be authority-only`);
    assert.equal(TOOLS[name].write, true);
  }
  for (const name of ["register-slice-criteria-from-catalog", "bind-slice-criterion-evidence",
    "record-release-slice-members", "propose-slice-completion"]) {
    assert.equal(TOOLS[name].authorityOnly, undefined, `${name} is a seat door on the writer connection`);
    assert.equal(TOOLS[name].write, true);
  }
  assert.equal(TOOLS["list-shipped-releases"].write, false);
  assert.equal(TOOLS["pending-slice-completion-proposals"].write, false);
  // Automation proposes; no writer-connection verb can set complete.
  assert.equal(TOOLS["auto-mark-slice-completion"], undefined);
  assert.deepEqual(TOOLS["mark-slice-progress"].inputSchema.properties.status.enum, ["in_progress", "blocked"]);
  assert.equal("status" in TOOLS["mark-slice-completion"].inputSchema.properties, false);
});

test("cancel-workflow-cutover-plan leaves the plan cancelled and frees the slot for a new open", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AUTHORITY_AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000030",
    workflow_key: "cancel-fixture", workflow_version: 1, recovery_plan: "revert",
  });
  const cancelled = await executeRegisteredTool(client, AUTHORITY_AGENT, "cancel-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000031", plan_id: opened.plan_id, reason: "wrong workflow",
  });
  assert.equal(cancelled.status, "cancelled");
  const again = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "cancel-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000032", plan_id: opened.plan_id, reason: "twice",
  }));
  assert.match(again.detail, /workflow_cutover_plan_not_active/);
});

test("Q116: retire-workflow-cutover-plan refuses before recovery_ready, and refuses without a real disable-legacy-schedule receipt", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000013",
    workflow_key: "y", workflow_version: 1, recovery_plan: "revert",
  });
  const early = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "retire-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000014",
    plan_id: opened.plan_id, disable_receipt_id: "80000000-0000-0000-0000-000000000001", reason: "too early",
  }));
  assert.match(early.detail, /workflow_cutover_plan_not_recovery_ready/);
  client.plans.get(opened.plan_id).stage = "recovery_ready";
  const noReceipt = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "retire-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000015",
    plan_id: opened.plan_id, disable_receipt_id: "80000000-0000-0000-0000-000000000099", reason: "no real receipt",
  }));
  assert.match(noReceipt.detail, /legacy_schedule_disable_receipt_not_found/);
  client.seedDisableReceipt("80000000-0000-0000-0000-000000000001", { workflow_key: "y" });
  // P1 fix (PR #1245 review, item 6 / Q157): retire-workflow-cutover-plan now
  // refuses outright when the independent workflow-truth census answers
  // unavailable, rather than proceeding on the receipt/evidence chain alone
  // and merely decorating the response with the raw reading. The census
  // route answers unavailable today (the durable census store has not
  // landed), so THIS build refuses every retire -- that is the intended,
  // correct fail-closed behavior, not a bug to work around in the fixture.
  // The check is ordered LAST in the store (after stage/receipt checks,
  // which the two sub-cases above already proved still fire first), so a
  // fully valid plan+receipt still gets refused specifically for the census.
  const refused = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "retire-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000016",
    plan_id: opened.plan_id, disable_receipt_id: "80000000-0000-0000-0000-000000000001",
    reason: "legacy schedule confirmed disabled",
  }));
  assert.equal(refused.error, "workflow_cutover_retire_refused");
  assert.match(refused.detail, /workflow_cutover_retire_refused_census_unknown/);
  // The plan must still be exactly where it was -- refused, not partially
  // applied.
  assert.equal(client.plans.get(opened.plan_id).stage, "recovery_ready");
});

test("Q157: record-workflow-caller refuses status=done without evidence, and refuses status=blocked without a reason", async () => {
  const client = new WorkflowCutoverFake();
  const noEvidence = await rejected(() => executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000017",
    workflow_key: "z", workflow_version: 1, caller_locator: "tools/legacy-caller.py",
    caller_kind: "script", status: "done",
  }));
  assert.match(noEvidence.detail, /caller_done_requires_evidence_ref/);
  const noReason = await rejected(() => executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000018",
    workflow_key: "z", workflow_version: 1, caller_locator: "tools/legacy-caller.py",
    caller_kind: "script", status: "blocked",
  }));
  assert.match(noReason.detail, /caller_blocked_requires_reason/);
  const recorded = await executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000019",
    workflow_key: "z", workflow_version: 1, caller_locator: "tools/legacy-caller.py",
    caller_kind: "script", status: "done", evidence_ref: "migrated in PR #9999",
  });
  assert.equal(recorded.status, "done");
});

test("workflow-cutover-board reports caller counts and never claims the census route is anything but unknown while it is fail-closed (Q157)", async () => {
  const client = new WorkflowCutoverFake();
  await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000020",
    workflow_key: "board-fixture", workflow_version: 1, recovery_plan: "revert",
  });
  await executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000021",
    workflow_key: "board-fixture", workflow_version: 1, caller_locator: "a", caller_kind: "script",
    status: "done", evidence_ref: "receipt-a",
  });
  await executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000022",
    workflow_key: "board-fixture", workflow_version: 1, caller_locator: "b", caller_kind: "script",
    status: "remaining",
  });
  const board = await executeRegisteredTool(client, AGENT, "workflow-cutover-board", {
    workflow_key: "board-fixture", workflow_version: 1,
  });
  assert.equal(board.plan.stage, "read_legacy");
  assert.deepEqual(board.caller_counts, { done: 1, remaining: 1 });
  // The V5-F09/A01 census reader answers unavailable today (documented,
  // fail-closed state); the board must never promote that into "done".
  assert.equal(board.census.state, "unknown");
});

test("Q153: slice completion resolves each criterion against its registered binding and refuses duplicates", async () => {
  const client = new WorkflowCutoverFake();
  await executeRegisteredTool(client, AUTHORITY_AGENT, "register-slice-checkable-done", {
    idempotency_key: "60000000-0000-0000-0000-000000000023", slice_id: "V5-R02",
    criteria: [
      { criterion: "A", evidence_kind: "acceptance", workflow_key: "wf", workflow_version: 1, acceptance_mode: "canary" },
      { criterion: "B", evidence_kind: "acceptance", workflow_key: "wf", workflow_version: 1, acceptance_mode: "shadow" },
    ],
  });
  client.seedAcceptance("70000000-0000-0000-0000-000000000011",
    { workflow_key: "wf", workflow_version: 1, mode: "canary", status: "accepted" });
  client.seedAcceptance("70000000-0000-0000-0000-000000000012",
    { workflow_key: "other", workflow_version: 1, mode: "shadow", status: "accepted" });
  const duplicate = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "mark-slice-completion", {
    idempotency_key: "60000000-0000-0000-0000-000000000024", slice_id: "V5-R02",
    criteria_receipt: [
      { criterion: "A", evidence_ref: "70000000-0000-0000-0000-000000000011" },
      { criterion: "A", evidence_ref: "70000000-0000-0000-0000-000000000011" },
    ],
  }));
  assert.match(duplicate.detail, /slice_completion_duplicate_criterion/);
  const wrongWorkflow = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "mark-slice-completion", {
    idempotency_key: "60000000-0000-0000-0000-000000000025", slice_id: "V5-R02",
    criteria_receipt: [
      { criterion: "A", evidence_ref: "70000000-0000-0000-0000-000000000011" },
      { criterion: "B", evidence_ref: "70000000-0000-0000-0000-000000000012" },
    ],
  }));
  assert.match(wrongWorkflow.detail, /slice_completion_complete_requires_every_criterion_proven/);
  const progress = await executeRegisteredTool(client, AGENT, "mark-slice-progress", {
    idempotency_key: "60000000-0000-0000-0000-000000000026", slice_id: "V5-R02", status: "in_progress",
    criteria_receipt: [
      { criterion: "A", evidence_ref: "70000000-0000-0000-0000-000000000011" },
      { criterion: "B", evidence_ref: null },
    ],
  });
  assert.equal(progress.status, "in_progress");
  assert.deepEqual(progress.criteria_receipt.map(el => el.pass), [true, false]);
  const read = await executeRegisteredTool(client, AGENT, "read-slice-completion", { slice_id: "V5-R02" });
  assert.equal(read.marked, true);
  assert.equal(read.status, "in_progress");
});

test("read-slice-completion reports marked:false for a slice that was never marked", async () => {
  const client = new WorkflowCutoverFake();
  const read = await executeRegisteredTool(client, AGENT, "read-slice-completion", { slice_id: "V5-R99" });
  assert.equal(read.marked, false);
  assert.equal(read.status, null);
});

test("register-slice-criteria-from-catalog takes no criteria: the server reads them from the catalog", () => {
  const schema = TOOLS["register-slice-criteria-from-catalog"].inputSchema;
  assert.deepEqual(Object.keys(schema.properties).sort(), ["idempotency_key", "slice_id"]);
  assert.equal(schema.additionalProperties, false);
});

test("the seat may bind the server-resolved kinds but never 'unbound' or refusal_proof", () => {
  const seatKinds = TOOLS["bind-slice-criterion-evidence"].inputSchema.properties.evidence_kind.enum;
  assert.deepEqual([...seatKinds].sort(), ["accepted_record", "live_check", "shipped_release"]);
  const partnerKinds = TOOLS["rebind-slice-criterion-evidence"].inputSchema.properties.evidence_kind.enum;
  assert.deepEqual([...partnerKinds].sort(), ["accepted_record", "live_check", "shipped_release", "unbound"]);
  const sources = TOOLS["bind-slice-criterion-evidence"].inputSchema.properties.live_check_source.enum;
  for (const source of ["portfolio_revision_acceptance", "portfolio_acceptance_effect_free"])
    assert.ok(sources.includes(source), source);
  assert.ok(!sources.includes("ci_gate"));
  const registered = TOOLS["register-slice-checkable-done"].inputSchema.properties.criteria.items.properties;
  assert.ok(registered.evidence_kind.enum.includes("accepted_record"));
  assert.ok(!registered.evidence_kind.enum.includes("refusal_proof"));
  assert.ok("bound_member_id" in TOOLS["bind-slice-criterion-evidence"].inputSchema.properties);
});

test("bind-slice-criterion-evidence passes bound_member_id to the door and surfaces its refusal", async () => {
  const client = new WorkflowCutoverFake();
  const member = "54000000-0000-0000-0000-000000000001";
  const ok = await executeRegisteredTool(client, AGENT, "bind-slice-criterion-evidence", {
    idempotency_key: "60000000-0000-0000-0000-000000000031", slice_id: "V5-F08",
    criterion: "scanner flags PHI", evidence_kind: "shipped_release", bound_member_id: member,
    reason: "Jev evidence_matching",
  });
  assert.equal(ok.bound_member_id, member);
  assert.equal(ok.bound_via, "automation");
  assert.deepEqual(client.bindCalls[0].slice(0, 8), ["V5-F08", "scanner flags PHI", "shipped_release", null,
    null, null, member, "Jev evidence_matching"]);
  const refused = await rejected(() => executeRegisteredTool(client, AGENT, "bind-slice-criterion-evidence", {
    idempotency_key: "60000000-0000-0000-0000-000000000032", slice_id: "V5-F08",
    criterion: "scanner flags PHI", evidence_kind: "shipped_release", reason: "no member",
  }));
  assert.equal(refused.error, "slice_criterion_binding_refused");
  assert.match(refused.detail, /shipped_release_binding_requires_this_slice_member/);
  for (const [kind, extra] of [["unbound", {}],
    ["refusal_proof", { live_check_source: "ci_gate", live_check_key: "ci", write_required_reason: "w" }]]) {
    const denied = await rejected(() => executeRegisteredTool(client, AGENT, "bind-slice-criterion-evidence", {
      idempotency_key: "60000000-0000-0000-0000-000000000033", slice_id: "V5-S00",
      criterion: "negatives refuse", evidence_kind: kind, reason: "seat tries", ...extra,
    }));
    assert.ok(denied, `the seat's schema refuses evidence_kind=${kind}`);
  }
  assert.equal(client.bindCalls.length, 2, "a schema refusal never reaches the door");
});

test("propose-slice-completion records a proposal, never a mark, and surfaces the door's refusal", async () => {
  const client = new WorkflowCutoverFake();
  const receipt = [{ criterion: "scanner flags PHI", evidence_ref: "54000000-0000-0000-0000-000000000001" }];
  const ok = await executeRegisteredTool(client, AGENT, "propose-slice-completion", {
    idempotency_key: "60000000-0000-0000-0000-000000000041", slice_id: "V5-F08", criteria_receipt: receipt,
    reason: "every criterion proven",
  });
  assert.equal(ok.status, "proposed");
  assert.equal(ok.awaiting_partner_confirmation, true);
  assert.equal(ok.proposal_id, "55000000-0000-0000-0000-000000000001");
  assert.equal("marked_via" in ok, false);
  const held = await rejected(() => executeRegisteredTool(client, AGENT, "propose-slice-completion", {
    idempotency_key: "60000000-0000-0000-0000-000000000042", slice_id: "V5-HELD", criteria_receipt: receipt,
  }));
  assert.equal(held.error, "slice_completion_proposal_refused");
  assert.match(held.detail, /slice_mark_held_by_partner/);
});

test("confirm-slice-completions is one partner act for many slices, with a per-slice outcome", async () => {
  const client = new WorkflowCutoverFake();
  const out = await executeRegisteredTool(client, AUTHORITY_AGENT, "confirm-slice-completions", {
    idempotency_key: "60000000-0000-0000-0000-000000000043", slice_ids: ["V5-F08", "V5-HELD", "V5-J303"],
    reason: "partner confirms the batch",
  });
  assert.equal(out.confirmed, 2);
  assert.equal(out.not_confirmed, 1);
  assert.deepEqual(out.results.map(r => [r.slice_id, r.outcome]),
    [["V5-F08", "confirmed"], ["V5-HELD", "held"], ["V5-J303", "confirmed"]]);
  assert.deepEqual(client.confirmCalls[0], [["V5-F08", "V5-HELD", "V5-J303"], "partner confirms the batch",
    "60000000-0000-0000-0000-000000000043", null]);
  const pinned = await executeRegisteredTool(client, AUTHORITY_AGENT, "confirm-slice-completions", {
    idempotency_key: "60000000-0000-0000-0000-000000000045", slice_ids: ["V5-F08", "V5-J303"],
    reason: "partner confirms what was reviewed",
    expected_proposal_ids: { "V5-F08": "70000000-0000-0000-0000-000000000001",
      "V5-J303": "70000000-0000-0000-0000-000000000009" },
  });
  assert.deepEqual(pinned.results.map(r => [r.slice_id, r.outcome]),
    [["V5-F08", "confirmed"], ["V5-J303", "proposal_superseded"]]);
  assert.equal(client.confirmCalls[1][3], JSON.stringify({ "V5-F08": "70000000-0000-0000-0000-000000000001",
    "V5-J303": "70000000-0000-0000-0000-000000000009" }));
  const badPin = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "confirm-slice-completions", {
    idempotency_key: "60000000-0000-0000-0000-000000000046", slice_ids: ["V5-F08"], reason: "r",
    expected_proposal_ids: { "V5-F08": "not-a-uuid" },
  }));
  assert.ok(badPin, "an expected proposal id must be a uuid");
  const noReason = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "confirm-slice-completions", {
    idempotency_key: "60000000-0000-0000-0000-000000000044", slice_ids: ["V5-F08"],
  }));
  assert.ok(noReason, "a confirmation needs a reason");
  const pending = await executeRegisteredTool(client, AGENT, "pending-slice-completion-proposals", {});
  assert.deepEqual(pending, { ok: true, count: 1, pending: [{ slice_id: "V5-F08", proposal_id: "p1", passes_now: true }] });
});

test("read-slice-completion returns the server's live done_state alongside the latest mark", async () => {
  const client = new WorkflowCutoverFake();
  const read = await executeRegisteredTool(client, AGENT, "read-slice-completion", { slice_id: "V5-S00" });
  assert.equal(read.done_state.slice_id, "V5-S00");
  assert.equal(read.done_state.registered, false);
  assert.ok(client.calls.some(c => c.sql.includes("ops.read_slice_done_state")));
});
