import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = relative => JSON.parse(fs.readFileSync(path.join(ROOT, relative), "utf8"));
const source = relative => fs.readFileSync(path.join(ROOT, relative), "utf8");
const UUID = /\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/i;

test("sourced captured-request projection is a closed, versioned read contract", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.version, "1.0.0");
  assert.match(contract.status, /^phase1_/);
  assert.equal(contract.transport, "authenticated MCP-backed read, not implemented by this static prototype");
  // WIDENED DELIBERATELY (migration 0426). The card used to admit three states
  // and pin ONE projection state, which is why a withdrawn request read as
  // queued: there was no other value the contract permitted. canonical_from now
  // carries the two terminals a request captured in error can reach, and
  // projection_state is a per-state map rather than a single literal, so the
  // crosswalk decides it instead of the reader hardcoding it.
  assert.deepEqual(contract.lifecycle, {
    canonical_from: ["captured", "triaged", "ready", "declined", "superseded"],
    projection_state: {captured: "queued", triaged: "queued", ready: "queued", declined: "declined", superseded: "declined"}
  });
  assert.deepEqual(contract.output.required_fields, [
    "human_ref", "title", "desired_outcome", "acceptance_criteria", "source", "triage", "plan", "pending_outcome_feedback", "outcome_feedback", "outcome_feedback_history", "accepted_feedback_count", "shape", "withdrawal", "state", "next_human_action", "actions"
  ]);
  assert.deepEqual(contract.output.actions, []);
  assert.deepEqual(contract.output.next_human_action.captured, {label: "Review and triage", effect: "none"});
});

test("triaged projection supplies only durable triage readback and another inert next step", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.output.triage.captured, null);
  assert.deepEqual(contract.output.triage.triaged, ["classification", "human_actor_slug", "triaged_at"]);
  assert.match(contract.output.triage.rule, /never an action authority/i);
  assert.deepEqual(contract.output.next_human_action, {
    captured: {label: "Review and triage", effect: "none"},
    triaged: {label: "Prepare scope and acceptance", effect: "none"},
    ready: {label: "Plan accepted", effect: "none"},
    ready_with_pending_feedback: {label: "Review outcome feedback", effect: "none"},
    ready_with_accepted_feedback: {label: "Outcome feedback accepted", effect: "none"},
    declined: {label: "Declined", effect: "none"},
    superseded: {label: "Superseded", effect: "none"}
  });
});

test("a withdrawn request keeps its record, leaves the queue, and asks nothing of a human", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  // The two canonical terminals a request captured in error can reach, and the
  // crosswalk's answer for each. superseded reading as declined is the other
  // contract's declared judgment call, not a choice made here.
  assert.equal(contract.lifecycle.projection_state.declined, "declined");
  assert.equal(contract.lifecycle.projection_state.superseded, "declined");
  // NEITHER TERMINAL MAY READ AS QUEUED. This is the assertion that fails if the
  // reader ever goes back to a hardcoded projection state.
  for (const state of ["declined", "superseded"])
    assert.notEqual(contract.lifecycle.projection_state[state], "queued",
      `${state} must not project as queued; a closed record is not waiting in line`);
  // Two terminals, one projection state — so the reason has to travel with it or
  // the collapse loses the only thing that told them apart.
  assert.deepEqual(contract.output.withdrawal.required_fields, ["exit_reason", "closed_at", "superseded_by_ref"]);
  assert.match(contract.output.withdrawal.rule, /REASON SURVIVES COLLAPSE/);
  assert.match(contract.output.withdrawal.rule, /claims no completion/i);
  assert.match(contract.output.withdrawal.superseded_by_ref, /never a database UUID/i);
  assert.match(contract.output.withdrawal.captured_only, /captured and no later state/i);
  // Triage is null on a withdrawn row because the canonical shape constraint
  // forbids the columns, not because the projection chose to hide them.
  assert.equal(contract.output.triage.withdrawn, null);
  assert.match(contract.output.triage.rule, /withdrawal is captured-only/i);
  assert.equal(contract.prohibitions.includes(
    "render a withdrawn request as queued, or offer a human any next action on one"), true);
  assert.equal(contract.prohibitions.includes(
    "drop a withdrawn request from the read rather than returning its record"), true);
  // Every declared next_human_action is inert, the two new ones included.
  for (const [key, action] of Object.entries(contract.output.next_human_action))
    assert.equal(action.effect, "none", `next_human_action.${key} must remain inert`);
});

test("accepted outcome feedback is bounded history, never a completion or displacement claim", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.deepEqual(contract.output.outcome_feedback.outcome_values, ["criteria_met", "criteria_not_met", "inconclusive"]);
  assert.match(contract.output.outcome_feedback.rule, /never execution, completion, release, approval, or a state transition/i);
  assert.match(contract.output.outcome_feedback_history.rule, /last 20 human-accepted/i);
  assert.match(contract.output.outcome_feedback_history.rule, /pending proposals never appear/i);
  assert.match(contract.output.accepted_feedback_count, /not a completion count/i);
  assert.equal(contract.prohibitions.includes("interpret accepted outcome feedback as success, displacement, execution, completion, or a state transition"), true);
});

test("pending feedback is a distinct reload-safe proposal and never contaminates accepted history", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.output.pending_outcome_feedback.status, "pending_human_acceptance");
  assert.match(contract.output.pending_outcome_feedback.rule, /exact current ready Work Request version and accepted plan/i);
  assert.match(contract.output.pending_outcome_feedback.rule, /never appears in accepted history/i);
  assert.match(contract.output.pending_outcome_feedback.rule, /never claims acceptance, execution, completion, success, release, or displacement/i);
});

test("the safe card names only durable human references and source evidence", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.output.human_ref, "durable human-readable Work Request reference; never a database UUID");
  assert.equal(contract.output.source.required_fields.join(","), "label,freshness,provenance");
  assert.match(JSON.stringify(contract), /exact source label/i);
  assert.doesNotMatch(JSON.stringify(contract), UUID);
  assert.doesNotMatch(JSON.stringify(contract), /business_payload|raw_query|client_payload/i);
});

test("Review and triage is an inert navigation affordance, never an action route", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.deepEqual(contract.output.next_human_action.captured, {
    label: "Review and triage",
    effect: "none"
  });
  assert.deepEqual(contract.output.next_human_action.triaged, {
    label: "Prepare scope and acceptance",
    effect: "none"
  });
  assert.equal(contract.action_card, undefined, "the card shape has one next_human_action home");
  const controlRoom = source("public/js/app.js");
  assert.doesNotMatch(controlRoom, /method\s*:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i);
  assert.doesNotMatch(controlRoom, /\/mcp\b/i);
  assert.doesNotMatch(controlRoom, /data-action=["'](?:dispatch|claim|approve|execute)/i);
  assert.match(JSON.stringify(contract.prohibitions), /POST, dispatch, claim, approve, or execute routes/);
});

test("the projection cannot derive state, completion, or controls from UI copy", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.prohibitions.includes("derive canonical state from a UI label"), true);
  assert.equal(contract.prohibitions.includes("claim completion before confirmed_closed"), true);
  assert.equal(contract.prohibitions.includes("invent action controls"), true);
  assert.equal(contract.prohibitions.includes("accept a client-supplied tenant, actor, or source reference"), true);
  assert.match(contract.integration_boundary, /authenticated server-side reader/i);
});
