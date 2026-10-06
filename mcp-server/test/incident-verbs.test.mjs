// incident-verbs.test.mjs — the five verbs the 2026-08-23 rules-and-verbs
// council put first, and the two things that could make them worse than the
// break-glass path they replace.
//
// THE TWO FAILURE MODES THIS SUITE IS AIMED AT, because a passing suite that
// misses them would be worse than no suite at all:
//
//   1. A LAXER DOOR WITH A NICER NAME. close-incident exists so a partner stops
//      needing CARR_BREAK_GLASS=1 + tools/db-tap.py to close an evidence-
//      complete incident. If it closes rows tools/ops-record.py's `resolve`
//      would have refused, it did not replace that path — it undercut it. The
//      parity block below runs each of resolve_preconditions' four refusals
//      against this implementation and asserts the same answer.
//
//   2. A SECOND PILE. The whole reason ops.incident carries a signature column
//      and a partial unique index (0116) is that repeated identical failures
//      must collapse into one row. A hand-opened incident whose fingerprint is
//      built in a DIFFERENT field order from the collectors' would never
//      collide with theirs, and the same outage would sit on the board twice
//      under two spellings. The fingerprint block asserts byte equality with
//      trace.js's own signature, which is the shape assess() already writes.
//
// Run with: node --test mcp-server/test/incident-verbs.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  closePreconditions, adjudicationChanges, incidentFingerprint,
  partnerAuthority, readinessFor, occurrenceSourceRef, SEVERITIES, OWNERS,
} from "../src/incident.js";
import { incidentSignature } from "../src/trace.js";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { PROFILES, callTool } from "../src/mcp.js";

const NOW = "2026-08-23T12:00:00.000Z";
const LATER = "2026-08-24T12:00:00.000Z";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "oauth-google", client_id: "claude",
  correlation_id: "11111111-2222-3333-4444-555555555555" };
const dell = { ...joe, id: "10000000-0000-0000-0000-000000000003", slug: "dell", display: "Dell" };
const agent = { id: "10000000-0000-0000-0000-000000000009", slug: "codex",
  display: "Codex", human: false, via: "oauth-google", sponsoring_human_slug: "joe",
  native_agent_verified: true };
const probe = { id: "10000000-0000-0000-0000-000000000008", slug: "smoke-probe",
  display: "probe", human: false, probe: true, via: "probe-token" };
const localMachine = { id: "10000000-0000-0000-0000-000000000007", slug: "joe-local",
  display: "Agent (joe-local)", human: false, agent: true, via: "local-token",
  sponsoring_human_slug: "joe", native_agent_verified: true };

// ── 1. THE FINGERPRINT IS THE COLLECTORS' SIGNATURE, NOT A SECOND SHAPE ──────

test("a hand-opened incident's fingerprint is byte-identical to the collectors'", () => {
  const mine = incidentFingerprint({
    service: "nightly-record-layer", environment: "production",
    operation: "nightly.vault-drift-watch", failureClass: "exit_1",
  });
  const theirs = incidentSignature({
    serviceKey: "nightly-record-layer", environment: "production",
    routeKey: "nightly.vault-drift-watch", failureClass: "exit_1",
  });
  assert.equal(mine.ok, true);
  assert.equal(mine.signature, theirs,
    "a different field order means 0116's partial unique index never collides the two writers, " +
    "and one outage sits on the board twice");
  assert.equal(mine.signature, "nightly-record-layer|production|nightly.vault-drift-watch|exit_1");
});

test("a fingerprint missing any one of its four fields is refused, and says which", () => {
  for (const missing of ["service", "environment", "operation", "failureClass"]) {
    const args = { service: "a", environment: "production", operation: "b", failureClass: "c" };
    delete args[missing];
    const r = incidentFingerprint(args);
    assert.equal(r.ok, false, `${missing} absent must refuse`);
    assert.equal(r.missing, missing, "the caller must be told which field, not just that one is gone");
  }
  assert.equal(incidentFingerprint({ service: "a", environment: "production",
    operation: "   ", failureClass: "c" }).ok, false, "whitespace is not an operation");
});

test("an occurrence's source ref is the shape trace.js already writes", () => {
  assert.equal(occurrenceSourceRef("abc"), "correlation:abc");
});

// ── 2. PARITY WITH tools/ops-record.py's resolve_preconditions ───────────────
//
// Each case here is one branch of that function, in its order.

test("an already-resolved incident does not close twice", () => {
  for (const state of ["resolved", "reviewed"]) {
    const r = closePreconditions({ ref: "INC-1", state }, { rootCause: "x", now: NOW });
    assert.equal(r.ok, false);
    assert.match(r.error, new RegExp(`already ${state}`));
  }
});

test("a close with no root cause is refused, and the message says why that matters", () => {
  const r = closePreconditions(
    { ref: "INC-1", state: "monitoring", recovery_evidence_ref: "ops.run:1" },
    { rootCause: "   ", now: NOW });
  assert.equal(r.ok, false);
  assert.match(r.error, /root cause is required/);
  assert.match(r.error, /not that the row is cleared/,
    "the refusal has to teach the distinction, or it reads as bureaucracy");
});

test("evidence already on the incident beats evidence typed by the caller", () => {
  const r = closePreconditions(
    { ref: "INC-1", state: "monitoring", recovery_evidence_ref: "ops.run:green" },
    { rootCause: "a stale credential", evidence: "trust me", now: NOW });
  assert.equal(r.ok, true);
  assert.equal(r.fields.recovery_evidence_ref, "ops.run:green");
});

test("an incident with no evidence anywhere refuses, and names both ways out", () => {
  const r = closePreconditions({ ref: "INC-1", state: "detected" },
    { rootCause: "nothing was ever broken", now: NOW });
  assert.equal(r.ok, false);
  assert.match(r.error, /no recovery evidence/);
  assert.match(r.error, /duplicate/, "the duplicate route is the other way out and must be named");
});

test("supplied evidence closes an incident that never recovered", () => {
  const r = closePreconditions({ ref: "INC-1", state: "detected" },
    { rootCause: "a deliberate acceptance probe", evidence: "ops/restore-drill 2026-08-20", now: NOW });
  assert.equal(r.ok, true);
  assert.equal(r.fields.recovery_evidence_ref, "ops/restore-drill 2026-08-20");
  assert.equal(r.fields.stamp_monitoring_until_now, true,
    "a row with no window still needs a monitoring_until under 0115's constraint");
});

test("an open monitoring window refuses the close and does not hide the date", () => {
  const r = closePreconditions(
    { ref: "INC-1", state: "monitoring", recovery_evidence_ref: "ops.run:green",
      monitoring_until: LATER },
    { rootCause: "a stale credential", now: NOW });
  assert.equal(r.ok, false);
  assert.match(r.error, /still inside its monitoring window until 2026-08-24 12:00Z/);
  assert.match(r.error, /symptom stopped, not that the cause/);
  assert.match(r.error, /allow_early/, "the escape hatch must be named in the refusal");
});

test("allow_early opens the window and the reason lands ON the incident", () => {
  const r = closePreconditions(
    { ref: "INC-1", state: "monitoring", recovery_evidence_ref: "ops.run:green",
      monitoring_until: LATER },
    { rootCause: "an induced failure", allowEarly: "the probe can never produce a green run",
      now: NOW });
  assert.equal(r.ok, true);
  assert.deepEqual(r.facts,
    ["closed before its monitoring window elapsed: the probe can never produce a green run"],
    "a reason kept only in shell history is a reason nobody reads back");
  assert.equal(r.fields.stamp_monitoring_until_now, false,
    "an early close keeps the window it was closed inside, so the row shows the watch it cut short");
});

test("an EMPTY allow_early is not an allow_early", () => {
  const r = closePreconditions(
    { ref: "INC-1", state: "monitoring", recovery_evidence_ref: "ops.run:green",
      monitoring_until: LATER },
    { rootCause: "x", allowEarly: "   ", now: NOW });
  assert.equal(r.ok, false, "a blank reason must not buy the exception a reason exists to justify");
});

test("an ELAPSED window closes with no exception recorded", () => {
  const r = closePreconditions(
    { ref: "INC-1", state: "monitoring", recovery_evidence_ref: "ops.run:green",
      monitoring_until: "2026-08-22T00:00:00.000Z" },
    { rootCause: "a stale credential", now: NOW });
  assert.equal(r.ok, true);
  assert.deepEqual(r.facts, [], "nothing was excused, so nothing should be recorded as excused");
});

// ── 3. THE DUPLICATE ARM, which the Python side has no column for ────────────

test("a duplicate carries its own evidence and its own window waiver, and says so", () => {
  const r = closePreconditions(
    { ref: "INC-2", state: "detected", duplicate_of_ref: "INC-1", monitoring_until: LATER },
    { rootCause: "the same event as INC-1", now: NOW });
  assert.equal(r.ok, true);
  assert.equal(r.fields.recovery_evidence_ref, "ops.incident:INC-1");
  assert.match(r.facts[0], /duplicate of INC-1/);
  assert.match(r.facts[0], /carries the investigation and the watch/,
    "the waiver must state WHY the window did not apply, not merely that it did not");
});

test("the duplicate arm does not excuse a missing root cause", () => {
  const r = closePreconditions(
    { ref: "INC-2", state: "detected", duplicate_of_ref: "INC-1" }, { rootCause: "", now: NOW });
  assert.equal(r.ok, false);
  assert.match(r.error, /root cause is required/);
});

// ── 4. PARTNER AUTHORITY — the boundary the council asked for by name ────────

test("Joe and Dell both carry partner authority; neither outranks the other", () => {
  for (const actor of [joe, dell])
    assert.equal(partnerAuthority(actor, "verified_partner").ok, true, `${actor.slug} must pass`);
});

test("sponsored agents and the local machine doors inherit sponsor authority; unsponsored doors are refused", () => {
  assert.equal(partnerAuthority(agent, "sponsored_agent").ok, true);
  // MOVED 2026-08-26 (Joe's ruling, decision dc57f62d): localMachine is the
  // `./run.sh call` door and now carries its sponsor's authority, so it is
  // asserted as admitted here rather than refused below. The probe and the
  // unsponsored actor stay refused — the ruling opened the partners' own doors,
  // not every door.
  assert.equal(partnerAuthority(localMachine, "sponsored_agent").ok, true);
  for (const [actor, cls] of [[probe, "probe_agent"], [null, "unsponsored_agent"]]) {
    const r = partnerAuthority(actor, cls);
    assert.equal(r.ok, false, `${actor?.slug || "no actor"} must not close an incident`);
    assert.equal(r.error, "partner_authority_required");
    assert.match(r.hint, /partner|Joe|Dell/);
  }
});

test("a human principal that is not a partner is still refused", () => {
  // The gate is the SERVER'S authorization class, never the human flag alone —
  // so a future human seat that is not Joe or Dell does not inherit the ledger.
  const r = partnerAuthority({ ...joe, slug: "someone-else" }, "sponsored_agent");
  assert.equal(r.ok, false);
  assert.match(r.hint, /Joe or Dell/);
});

test("an unsponsored refusal names the accepted authority paths", () => {
  const r = partnerAuthority(probe, "probe_agent");
  assert.match(r.hint, /Joe or Dell/);
  assert.match(r.hint, /Codex\/Claude/);
});

// ── 5. ADJUDICATION ─────────────────────────────────────────────────────────

const live = { id: "aaa", ref: "INC-9", severity: "SEV-3", owner_actor: "joe", state: "detected" };

test("severity is checked against the ledger's own constraint, not a shorter list", () => {
  assert.deepEqual([...SEVERITIES], ["SEV-0", "SEV-1", "SEV-2", "SEV-3", "SEV-4"]);
  assert.equal(adjudicationChanges(live, { severity: "SEV-1" }).fields.severity, "SEV-1");
  assert.equal(adjudicationChanges(live, { severity: "critical" }).ok, false);
  assert.equal(adjudicationChanges(live, { severity: "SEV-9" }).ok, false);
});

test("an owner is a person the board can filter on", () => {
  assert.deepEqual([...OWNERS], ["joe", "dell"]);
  assert.equal(adjudicationChanges(live, { owner: "dell" }).fields.owner_actor, "dell");
  const r = adjudicationChanges(live, { owner: "joe/dell" });
  assert.equal(r.ok, false);
  assert.match(r.error, /nobody picks up/);
});

test("an incident cannot be a duplicate of itself, or of another duplicate", () => {
  assert.match(adjudicationChanges(live, { canonical: { id: "aaa", ref: "INC-9" } }).error,
    /duplicate of itself/);
  const chained = adjudicationChanges(live,
    { canonical: { id: "bbb", ref: "INC-8", duplicate_of_id: "ccc" } });
  assert.equal(chained.ok, false);
  assert.match(chained.error, /itself recorded as a duplicate/,
    "a chain leaves a reader walking pointers with no guaranteed end");
});

test("recording a duplicate sets the pointer and the next step, and never the state", () => {
  const r = adjudicationChanges(live, { canonical: { id: "bbb", ref: "INC-8" }, note: "same deploy" });
  assert.equal(r.ok, true);
  assert.equal(r.fields.duplicate_of_id, "bbb");
  assert.match(r.fields.next_action, /close as a duplicate of INC-8/);
  assert.equal(r.fields.state, undefined, "adjudication is a judgment, not a close");
  assert.equal(r.fields.resolved_at, undefined);
  assert.match(r.facts[0], /same deploy/, "the reason must reach the incident's own history");
});

test("an adjudication that changes nothing is refused rather than logged as a decision", () => {
  const r = adjudicationChanges(live, { severity: "SEV-3", owner: "joe" });
  assert.equal(r.ok, false);
  assert.match(r.error, /nothing to adjudicate/);
});

// ── 6. THE BOARD'S READINESS COLUMN AGREES WITH THE VERB ────────────────────

test("ready_to_close is computed by the close guard itself, never by a second copy", () => {
  const closable = readinessFor(
    { ref: "INC-1", state: "monitoring", recovery_evidence_ref: "ops.run:g",
      monitoring_until: "2026-08-22T00:00:00.000Z" }, NOW);
  assert.deepEqual(closable, { ready_to_close: true, blocked_by: null });

  const waiting = readinessFor(
    { ref: "INC-2", state: "monitoring", recovery_evidence_ref: "ops.run:g",
      monitoring_until: LATER }, NOW);
  assert.equal(waiting.ready_to_close, false);
  assert.match(waiting.blocked_by, /monitoring window open/);

  const bare = readinessFor({ ref: "INC-3", state: "detected" }, NOW);
  assert.equal(bare.ready_to_close, false);
  assert.match(bare.blocked_by, /no recovery evidence/,
    "the two blockers are different work — one waits on a human, one on the clock");

  assert.deepEqual(readinessFor({ ref: "INC-4", state: "resolved" }, NOW),
    { ready_to_close: false, blocked_by: "already resolved" });
});

test("a recurrence clears readiness for both board and close; a later recovery can become ready again", () => {
  const recovered = { ref: "INC-5", state: "monitoring",
    recovery_evidence_ref: "ops.run:old-green",
    monitoring_until: "2026-08-22T00:00:00.000Z" };
  assert.equal(readinessFor(recovered, NOW).ready_to_close, true);
  assert.equal(closePreconditions(recovered, { rootCause: "cause", now: NOW }).ok, true);

  // Exact row shape every recurrence writer must leave after a NEW failure.
  const recurred = { ...recovered, state: "detected",
    recovery_evidence_ref: null, monitoring_until: null };
  const board = readinessFor(recurred, NOW);
  const close = closePreconditions(recurred, { rootCause: "cause", now: NOW });
  assert.equal(board.ready_to_close, false);
  assert.match(board.blocked_by, /no recovery evidence/);
  assert.equal(close.ok, false);
  assert.match(close.error, /no recovery evidence/,
    "the board and partner close must reject the same invalidated recovery");

  const recoveredAgain = { ...recurred, state: "monitoring",
    recovery_evidence_ref: "ops.run:new-green",
    monitoring_until: "2026-08-22T00:00:00.000Z" };
  assert.equal(readinessFor(recoveredAgain, NOW).ready_to_close, true);
  assert.equal(closePreconditions(recoveredAgain,
    { rootCause: "new recovery held", now: NOW }).ok, true,
    "invalidating stale evidence must not prevent a later genuine recovery");
});

// ── 7. THE REGISTRY ENTRIES THEMSELVES ──────────────────────────────────────

test("the incident verbs include bounded triage", () => {
  for (const name of ["incident-board", "get-incident", "open-incident",
                      "triage-incident", "close-incident", "adjudicate-incident"])
    assert.ok(TOOLS[name], `${name} is missing from the registry`);
});

test("the reads are reads and the writes are writes", () => {
  assert.equal(TOOLS["incident-board"].write, false);
  assert.equal(TOOLS["get-incident"].write, false);
  for (const name of ["open-incident", "triage-incident", "close-incident", "adjudicate-incident"])
    assert.equal(TOOLS[name].write, true, `${name} must run the write envelope`);
});

test("close and adjudicate are humanOnly; open and the reads are not", () => {
  assert.equal(TOOLS["close-incident"].humanOnly, true);
  assert.equal(TOOLS["adjudicate-incident"].humanOnly, true);
  assert.ok(!TOOLS["open-incident"].humanOnly,
    "automation must be able to say that something broke");
  assert.ok(!TOOLS["triage-incident"].humanOnly,
    "bounded triage is an actionable workflow step for sponsored automation");
  assert.ok(!TOOLS["incident-board"].humanOnly);
});

test("every write verb requires an idempotency key", () => {
  for (const name of ["open-incident", "triage-incident", "close-incident", "adjudicate-incident"])
    assert.ok(TOOLS[name].inputSchema.required.includes("idempotency_key"), name);
});

test("the descriptions draw the line against the two adjacent verbs", () => {
  // codex's chair asked for this in words: "do not leave three ways to mint
  // parallel unresolved records." The boundary has to be readable by the model
  // choosing a verb, which means it lives in the description.
  const open = TOOLS["open-incident"].description;
  assert.match(open, /record-defect/);
  assert.match(open, /report-problem/);
  assert.match(TOOLS["incident-board"].description, /record-defect/);
});

test("close-incident's description names the break-glass path it replaces", () => {
  const d = TOOLS["close-incident"].description;
  assert.match(d, /CARR_BREAK_GLASS/, "a replacement has to say what it replaces");
  assert.match(d, /break-glass stays for emergencies/i,
    "and it must not read as a retirement of the emergency path");
  assert.match(d, /Joe or Dell/);
});

// ── 8. PROFILES ─────────────────────────────────────────────────────────────

test("an unattended seat may open an incident and may not close one", () => {
  for (const profile of ["capture", "away"]) {
    assert.ok(PROFILES[profile].has("open-incident"),
      `${profile} is the seat most likely to see a failure first`);
    assert.ok(PROFILES[profile].has("triage-incident"),
      `${profile} can record a next step without claiming a root cause`);
    assert.ok(!PROFILES[profile].has("close-incident"), `${profile} must not close`);
    assert.ok(!PROFILES[profile].has("adjudicate-incident"), `${profile} must not reclassify`);
  }
});

test("the locked machine profiles gain nothing", () => {
  for (const profile of ["probe", "reviewer", "hermes"])
    for (const verb of ["open-incident", "close-incident", "adjudicate-incident"])
      assert.ok(!PROFILES[profile].has(verb), `${profile} must not hold ${verb}`);
});

// ── 9. THE HANDLERS, against a fake client ──────────────────────────────────

class Fake {
  constructor(plan = {}) { this.plan = plan; this.sql = []; }
  async query(text, params = []) {
    const s = text.replace(/\s+/g, " ").trim();
    this.sql.push([s, params]);
    for (const [match, rows] of Object.entries(this.plan))
      if (s.includes(match)) return { rows: typeof rows === "function" ? rows(params) : rows };
    return { rows: [] };
  }
}

const refuse = async (fn) => {
  try { await fn(); } catch (e) {
    assert.ok(e instanceof ToolError, `expected a ToolError, got ${e}`);
    return e.payload;
  }
  throw new Error("expected a refusal and got none");
};

test("close-incident refuses an unsponsored runtime before it reads anything", async () => {
  const fake = new Fake();
  const payload = await refuse(() => TOOLS["close-incident"].handler(fake, probe,
    { idempotency_key: "k", ref: "INC-20260823-01", root_cause: "x" }));
  assert.equal(payload.error, "partner_authority_required");
  assert.deepEqual(fake.sql, [],
    "the boundary must close before the transaction touches the ledger at all");
});

test("adjudicate-incident refuses the same door", async () => {
  const fake = new Fake();
  const payload = await refuse(() => TOOLS["adjudicate-incident"].handler(fake, probe,
    { idempotency_key: "k", ref: "INC-20260823-01", severity: "SEV-1" }));
  assert.equal(payload.error, "partner_authority_required");
  assert.deepEqual(fake.sql, []);
});

test("open-incident refuses a service the ledger has never heard of", async () => {
  const fake = new Fake({ "from tool_call where idempotency_key": [] });
  const payload = await refuse(() => TOOLS["open-incident"].handler(fake, joe, {
    idempotency_key: "k1", service: "typo-service", environment: "production",
    operation: "nightly.thing", failure_class: "exit_1",
  }));
  assert.equal(payload.error, "no_such_service");
  assert.match(payload.hint, /never heard of/);
});

test("open-incident refuses an environment it was not given", async () => {
  const fake = new Fake({ "from tool_call where idempotency_key": [] });
  const payload = await refuse(() => TOOLS["open-incident"].handler(fake, joe, {
    idempotency_key: "k2", service: "carr-mcp", environment: "prod",
    operation: "nightly.thing", failure_class: "exit_1",
  }));
  assert.equal(payload.error, "invalid_environment");
  assert.match(payload.hint, /never guessed at/);
});

test("triage moves one detected incident and returns fields visible in both reads", async () => {
  const before = { id: "inc-1", ref: "INC-20260823-01", state: "detected",
    next_action: "inspect trace", business_impact: null, db_now: NOW };
  const after = { ...before, state: "triaged", next_action: "inspect failed run",
    business_impact: "unknown" };
  const beforeBoard = await TOOLS["incident-board"].handler(new Fake({
    "from ops.incident i left join": [before],
  }), agent, {});
  assert.deepEqual(beforeBoard.by_state, { detected: 1 });
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "update ops.incident set state = 'triaged'": [after],
    "from ops.incident i left join": [after],
    "select count(*)::int as n from ops.incident_fact": [{ n: 0 }],
    "from ops.incident_fact where incident_id": [],
    "from ops.incident_hypothesis": [],
    "from ops.incident_link": [],
    "from ops.incident_service": [],
    "where duplicate_of_id": [],
    "from ops.v_trace": [],
  });
  const out = await TOOLS["triage-incident"].handler(fake, agent, {
    idempotency_key: "triage-1", ref: before.ref,
    next_action: after.next_action, impact_assessment: "unknown",
  });
  assert.deepEqual(out, { ref: before.ref, state: "triaged",
    next_action: after.next_action, business_impact: "unknown" });
  const update = fake.sql.find(([sql]) => sql.startsWith("update ops.incident set state = 'triaged'"));
  assert.match(update[0], /where ref = \$1 and state = 'detected'/);
  assert.doesNotMatch(update[0], /severity|root_cause|resolved_at|monitoring_until|recovery_evidence_ref/);
  const board = await TOOLS["incident-board"].handler(fake, agent, { state: "triaged" });
  assert.equal(board.by_state.detected || 0, 0);
  assert.equal(board.by_state.triaged, 1);
  assert.equal(board.incidents[0].business_impact, "unknown");
  const detail = await TOOLS["get-incident"].handler(fake, agent, { ref: before.ref });
  assert.equal(detail.incident.state, "triaged");
  assert.equal(detail.incident.next_action, after.next_action);
  assert.equal(detail.facts.length, 0, "a provisional impact is not written as an observed fact");
  assert.equal(detail.hypotheses.length, 0, "triage does not invent a causal hypothesis");
});

test("triage refuses missing assessment, missing row, and stale state without a second write", async () => {
  const args = { idempotency_key: "triage-2", ref: "INC-20260823-01",
    next_action: "inspect failed run", impact_assessment: "unknown" };
  const blank = new Fake({ "from tool_call where idempotency_key": [] });
  assert.equal((await refuse(() => TOOLS["triage-incident"].handler(blank, joe,
    { ...args, impact_assessment: "   " }))).error, "triage_details_required");
  assert.ok(!blank.sql.some(([sql]) => sql.startsWith("update ops.incident")));

  const stale = new Fake({ "from tool_call where idempotency_key": [],
    "select state from ops.incident where ref": [{ state: "investigating" }] });
  const conflict = await refuse(() => TOOLS["triage-incident"].handler(stale, joe, args));
  assert.deepEqual({ error: conflict.error, actual: conflict.actual },
    { error: "incident_state_conflict", actual: "investigating" });
  assert.ok(!stale.sql.some(([sql]) => sql.includes("insert into ops.incident_fact")));
  const missing = new Fake({ "from tool_call where idempotency_key": [] });
  assert.equal((await refuse(() => TOOLS["triage-incident"].handler(missing, joe, args))).error,
    "no_such_incident");
});

test("triage same-key retry replays the receipt before attempting another transition", async () => {
  const row = { id: "inc-1", ref: "INC-20260823-01", state: "triaged",
    next_action: "inspect failed run", business_impact: "unknown" };
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "update ops.incident set state = 'triaged'": [row],
  });
  const args = { idempotency_key: "triage-retry", ref: row.ref,
    next_action: row.next_action, impact_assessment: "unknown" };
  await TOOLS["triage-incident"].handler(fake, agent, args);
  const receipt = fake.sql.find(([sql]) => sql.startsWith("insert into tool_call"));
  assert.ok(receipt, "the first transition must leave an envelope receipt");
  const hash = receipt[1][3];
  const response = JSON.parse(receipt[1][4]);
  fake.plan["from tool_call where idempotency_key"] = [{ request_hash: hash, response }];
  fake.sql.length = 0;
  const replay = await TOOLS["triage-incident"].handler(fake, agent, args);
  assert.deepEqual(replay, { replayed: true, ...response });
  assert.ok(fake.sql[0][0].includes("pg_advisory_xact_lock"),
    "equal first calls serialize before envelope replay");
  assert.ok(!fake.sql.some(([sql]) => sql.startsWith("update ops.incident")));
});

test("the allocated 100th incident ref is accepted for triage", async () => {
  const allocator = new Fake({
    "from tool_call where idempotency_key": [],
    "from ops.service where key": [{ id: "svc-1", key: "carr-mcp" }],
    "coalesce(max(substring(ref": [{ day: "20260823", seq: 100 }],
    "insert into ops.incident (": [{ id: "inc-100" }],
    "as occurrences from ops.incident": [{ occurrences: 1 }],
  });
  const opened = await TOOLS["open-incident"].handler(allocator, agent, {
    idempotency_key: "open-100", service: "carr-mcp", environment: "production",
    operation: "run-100", failure_class: "exit_1",
  });
  const ref = opened.ref;
  assert.equal(ref, "INC-20260823-100", "the allocator grows past two digits at 100");
  const shape = new RegExp(TOOLS["triage-incident"].inputSchema.properties.ref.pattern);
  const linkShape = new RegExp(TOOLS["link-incident-work-request"].inputSchema.properties.incident_ref.pattern);
  assert.equal(shape.test("INC-20260823-01"), true);
  assert.equal(shape.test("INC-20260823-99"), true);
  assert.equal(shape.test(ref), true, "allocator pads to a minimum of two digits, not exactly two");
  assert.equal(linkShape.test(ref), true, "the adjacent incident-link door uses the same ref contract");
  for (const invalid of ["INC-20260823-00", "INC-20260823-001", "INC-20260823-1", "INC-20260823-100x"])
    assert.equal(shape.test(invalid), false, invalid);
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "update ops.incident set state = 'triaged'": [{ id: "inc-100", ref, state: "triaged",
      next_action: "inspect run", business_impact: "unknown" }],
  });
  const out = await TOOLS["triage-incident"].handler(fake, agent, {
    idempotency_key: "triage-100", ref, next_action: "inspect run", impact_assessment: "unknown",
  });
  assert.equal(out.ref, ref);
});

test("a repeat of an OPEN fingerprint attaches instead of minting a second row", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from ops.service where key": [{ id: "svc-1", key: "carr-mcp" }],
    "from ops.incident where signature": [{ id: "inc-1", ref: "INC-20260823-01",
      severity: "SEV-2", state: "monitoring", owner_actor: "joe" }],
    "as occurrences from ops.incident": [{ occurrences: 29 }],
  });
  const out = await TOOLS["open-incident"].handler(fake, joe, {
    idempotency_key: "k3", service: "carr-mcp", environment: "production",
    operation: "mcp:tools/call:search-doctrine", failure_class: "verb_internal_error",
    observed: "the verb timed out again",
  });
  assert.equal(out.opened, false);
  assert.equal(out.ref, "INC-20260823-01");
  assert.equal(out.occurrences, 29);
  assert.equal(out.state, "detected",
    "a new failure during monitoring must immediately stop reading as recovered");
  assert.equal(out.severity, "SEV-2", "an attach never re-reads severity off the caller's argument");
  assert.match(out.note, /already open/);

  const statements = fake.sql.map(([s]) => s);
  assert.ok(!statements.some((s) => s.startsWith("insert into ops.incident (")),
    "a repeat must not mint a second incident");
  assert.ok(statements.some((s) => s.includes("insert into ops.incident_fact")),
    "the occurrence still has to be recorded, or the repeat count never grows");
  assert.ok(statements.some((s) => s.includes("set observed_at = now()")),
    "an attach bumps freshness so the row does not read as stale");
  const recurrence = statements.find((s) => s.includes("set observed_at = now()"));
  assert.match(recurrence, /state = case when state = 'monitoring' then 'detected'/);
  assert.match(recurrence, /recovery_evidence_ref = case when state = 'monitoring' then null/);
  assert.match(recurrence, /monitoring_until = case when state = 'monitoring' then null/);
  assert.doesNotMatch(recurrence, /resolved_at|root_cause|then 'resolved'|then 'reviewed'/,
    "open-incident may invalidate recovery but remains structurally unable to close");
});

test("the same correlation observed twice records one occurrence, not two", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from ops.service where key": [{ id: "svc-1", key: "carr-mcp" }],
    "from ops.incident where signature": [{ id: "inc-1", ref: "INC-20260823-01",
      severity: "SEV-3", state: "detected", owner_actor: "joe" }],
    "from ops.incident_fact where incident_id": [{ "?column?": 1 }],
    "as occurrences from ops.incident": [{ occurrences: 4 }],
  });
  const out = await TOOLS["open-incident"].handler(fake, joe, {
    idempotency_key: "k4", service: "carr-mcp", environment: "production",
    operation: "mcp:tools/call:close-loop", failure_class: "verb_internal_error",
  });
  assert.equal(out.occurrence_recorded, false);
  assert.ok(!fake.sql.some(([s]) => s.includes("insert into ops.incident_fact")),
    "never grow the list on a retry — trace.js's rule, and the reason the count means anything");
  assert.ok(!fake.sql.some(([s]) => s.includes("update ops.incident set observed_at")),
    "the same replay boundary must also prevent invalidating recovery evidence");
});

test("open-incident takes the same ref-allocation lock the Python writer takes", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from ops.service where key": [{ id: "svc-1", key: "carr-mcp" }],
    "as occurrences from ops.incident": [{ occurrences: 1 }],
    "coalesce(max(substring(ref": [{ day: "20260823", seq: 7 }],
    "insert into ops.incident (": [{ id: "inc-new" }],
  });
  const out = await TOOLS["open-incident"].handler(fake, joe, {
    idempotency_key: "k5", service: "carr-mcp", environment: "production",
    operation: "mcp:tools/call:find", failure_class: "verb_internal_error",
  });
  assert.equal(out.opened, true);
  assert.equal(out.ref, "INC-20260823-07",
    "the day and the sequence both come from the query — a client-formatted date is how every " +
    "incident in a timezone gap ends up numbered 01");

  // DOUBLE-CHECKED: look, then lock, then look again. The lock is the same one
  // ops-record.py holds to transaction end, so an unconditional take would put
  // every attach behind the nightly assessment's whole run.
  const lock = fake.sql.findIndex(([s]) => s.includes("ops.incident.ref-allocation"));
  const looks = fake.sql.map(([s], i) => [s, i])
    .filter(([s]) => s.includes("from ops.incident where signature")).map(([, i]) => i);
  assert.ok(lock >= 0, "without the lock two writers both observe absence and both mint");
  assert.equal(looks.length, 2, "a miss must re-ask under the lock, or the lock protects nothing");
  assert.ok(looks[0] < lock && lock < looks[1],
    "the first look is unlocked, the second is the one the lock protects");
});

test("an attach never takes the global mint lock", async () => {
  // The liveness half. ops-record.py's assess holds this lock for its whole
  // transaction; an unattended run recording a repeat must not queue behind it
  // for a row it was never going to insert.
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from ops.service where key": [{ id: "svc-1", key: "carr-mcp" }],
    "from ops.incident where signature": [{ id: "inc-1", ref: "INC-20260823-01",
      severity: "SEV-3", state: "detected", owner_actor: "joe" }],
    "as occurrences from ops.incident": [{ occurrences: 2 }],
  });
  await TOOLS["open-incident"].handler(fake, joe, {
    idempotency_key: "k9", service: "carr-mcp", environment: "production",
    operation: "mcp:tools/call:find", failure_class: "verb_internal_error",
  });
  assert.ok(!fake.sql.some(([s]) => s.includes("ops.incident.ref-allocation")),
    "the attach path must not serialize itself against the nightly collector");
});

test("a partner's close writes the outcome and refuses to leave the row bare", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from ops.incident i left join": [{ id: "inc-1", ref: "INC-20260823-01", state: "monitoring",
      recovery_evidence_ref: "ops.run:green", monitoring_until: "2026-08-22T00:00:00.000Z",
      duplicate_of: null, db_now: NOW }],
  });
  const out = await TOOLS["close-incident"].handler(fake, joe, {
    idempotency_key: "k6", ref: "INC-20260823-01",
    root_cause: "the credential rotated and the job kept the old one",
  });
  assert.equal(out.state, "resolved");
  assert.equal(out.next_action, "review and record a followup disposition");
  const update = fake.sql.find(([s]) => s.includes("update ops.incident set state = 'resolved'"));
  assert.ok(update, "the close has to actually write");
  assert.ok(update[0].includes("state not in ('resolved','reviewed')"),
    "the update is guarded so a race cannot close an already-closed row a second time");
});

test("a partner's close is still refused by the same guard the CLI uses", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from ops.incident i left join": [{ id: "inc-1", ref: "INC-20260823-01", state: "detected",
      recovery_evidence_ref: null, monitoring_until: null, duplicate_of: null, db_now: NOW }],
  });
  const payload = await refuse(() => TOOLS["close-incident"].handler(fake, joe, {
    idempotency_key: "k7", ref: "INC-20260823-01", root_cause: "it stopped",
  }));
  assert.equal(payload.error, "close_refused");
  assert.match(payload.reason, /no recovery evidence/);
  assert.match(payload.hint, /same refusal tools\/ops-record\.py resolve gives/,
    "partner authority buys the door, never a weaker guard behind it");
  assert.ok(!fake.sql.some(([s]) => s.includes("update ops.incident")));
});

// ── 10. THE DISPATCHER'S OWN GATES, not just the handler's ──────────────────
//
// The handler tests above prove requirePartner refuses. These prove the two
// gates ABOVE it refuse too, which matters because they are the ones that fire
// for a caller who never reaches a handler: executeRegisteredTool's humanOnly
// check (every machine door), and callTool's allowedIn check (a narrowed
// profile). A boundary held by exactly one mechanism is a boundary one refactor
// from being held by none.

test("the registry gate no longer refuses any door on authority", async () => {
  // INVERTED (Joe's ruling 2026-08-26, decision dc57f62d). This used to prove
  // the dispatcher refused the probe and local-token doors before a handler ran.
  // It now proves the opposite for every door, sponsored or not, because that
  // is what the ruling bought and it deserves a test rather than a comment.
  //
  // WHAT STILL GUARDS close-incident, and why removing this is not the same as
  // removing the protection: requirePartner inside the handler still refuses,
  // the database grants still withhold ops.incident.resolved_at and root_cause
  // from the job role, and the receipted break-glass path is untouched. The
  // dispatcher's authority check was the outermost of several, not the only one.
  for (const machine of [agent, probe, localMachine]) {
    const payload = await refuse(() => executeRegisteredTool(new Fake(), machine, "close-incident",
      { idempotency_key: "k", ref: "INC-20260823-01", root_cause: "x" }));
    assert.notEqual(payload.error, "human_only", `${machine.slug} must not be refused on authority`);
  }
});

test("a narrowed profile refuses the close by name, and still allows the open", async () => {
  const blocked = await refuse(() => callTool({}, joe, "close-incident",
    { idempotency_key: "k", ref: "INC-1", root_cause: "x" }, "capture"));
  assert.equal(blocked.error, "not_in_profile");
  assert.equal(blocked.verb, "close-incident");

  // The positive half. open-incident must clear the profile gate — if it did
  // not, an unattended run could not file what it saw, which is the whole
  // reason it is in that profile. It fails LATER, on the absent writer
  // credential, and that is the proof it got past the gate.
  let reached = false;
  try {
    await callTool({}, joe, "open-incident", { idempotency_key: "k", service: "carr-mcp",
      environment: "production", operation: "x", failure_class: "y" }, "capture");
  } catch (e) {
    reached = !(e instanceof ToolError && e.payload?.error === "not_in_profile");
  }
  assert.ok(reached, "open-incident must clear the capture profile's gate");
});

test("no incident verb accepts a caller-claimed authority field", async () => {
  for (const name of ["incident-board", "open-incident", "close-incident", "adjudicate-incident"]) {
    const payload = await refuse(() => executeRegisteredTool(new Fake(), joe, name,
      { idempotency_key: "k", ref: "INC-1", sponsor: "joe" }));
    assert.equal(payload.error, "caller_authority_field_forbidden", name);
  }
});

// ── 11. THE THREE DEFECTS THE LIVE REPLAY FOUND (2026-08-23) ────────────────
//
// Every case here failed against the real ledger before it passed. Written
// after, deliberately kept: each one is a shape of "the code did something
// defensible and told the reader something false", which is the class this
// whole ledger exists to prevent.

test("a stated reason for an early close is never silently dropped", () => {
  // FOUND LIVE: an incident opened by open-incident carries expires_at but no
  // monitoring_until, so `inWindow` was false and the partner's typed reason
  // went nowhere. The close succeeded and the record said nothing about why.
  const r = closePreconditions(
    { ref: "INC-1", state: "detected", monitoring_until: null },
    { rootCause: "a probe", evidence: "the replay", allowEarly: "a probe never goes green", now: NOW });
  assert.equal(r.ok, true);
  assert.equal(r.facts.length, 1, "the reason must reach the incident");
  assert.match(r.facts[0], /a probe never goes green/);
  assert.match(r.facts[0], /no monitoring window was open/,
    "and the sentence must be TRUE — 'closed before its window elapsed' about a row with no " +
    "window is a false statement in the ledger, which is worse than a missing one");
});

test("a duplicate close records why its evidence was another incident, window or not", () => {
  // FOUND LIVE: the waiver fact was conditional on an open window, so a
  // duplicate closed outside one left no trace of the reasoning at all.
  const outside = closePreconditions(
    { ref: "INC-2", state: "detected", duplicate_of_ref: "INC-1", monitoring_until: null },
    { rootCause: "same event", now: NOW });
  assert.equal(outside.ok, true);
  assert.match(outside.facts[0], /duplicate of INC-1/);
  assert.ok(!/monitoring window/.test(outside.facts[0]),
    "there was no window, so the fact must not claim one was waived");

  const inside = closePreconditions(
    { ref: "INC-2", state: "detected", duplicate_of_ref: "INC-1", monitoring_until: LATER },
    { rootCause: "same event", now: NOW });
  assert.match(inside.facts[0], /inside its monitoring window/);
  assert.match(inside.facts[0], /carries the investigation and the watch/);
});

test("an incident that carries its OWN evidence does not claim to be closed as a duplicate", () => {
  // The pointer can be set on a row that also recovered for real. The fact
  // should describe what the close actually stood on.
  const r = closePreconditions(
    { ref: "INC-2", state: "monitoring", duplicate_of_ref: "INC-1",
      recovery_evidence_ref: "ops.run:green", monitoring_until: "2026-08-01T00:00:00.000Z" },
    { rootCause: "same event", now: NOW });
  assert.equal(r.ok, true);
  assert.equal(r.fields.recovery_evidence_ref, "ops.run:green");
  assert.deepEqual(r.facts, [], "it closed on its own green run, not on the other incident");
});

test("both exceptions on one close each get their own line", () => {
  const r = closePreconditions(
    { ref: "INC-2", state: "detected", duplicate_of_ref: "INC-1", monitoring_until: LATER },
    { rootCause: "same event", allowEarly: "and the window cannot apply anyway", now: NOW });
  assert.equal(r.facts.length, 2, "two different reasons are two entries in the history");
});

test("the refusal itself is unchanged by any of that", () => {
  const r = closePreconditions(
    { ref: "INC-1", state: "monitoring", recovery_evidence_ref: "ops.run:green",
      monitoring_until: LATER },
    { rootCause: "x", now: NOW });
  assert.equal(r.ok, false, "an open window with no duplicate and no reason still refuses");
});

test("get-incident counts correlations from the whole fact table, not from the page it returned", async () => {
  // FOUND LIVE: get-incident with fact_limit:3 on a row carrying 87 recurrences
  // reported "correlations 2/2" — a cap notice computed truthfully from the
  // wrong set. A count derived from a page is a count of the page.
  const fake = new Fake({
    "from ops.incident i left join": [{ id: "inc-1", ref: "INC-1", state: "detected",
      correlation_id: "00000000-0000-0000-0000-000000000000", db_now: NOW, duplicate_of: null }],
    "order by recorded_at desc limit": [{ text: "one", source_ref: "ops.run:1", recorded_at: NOW }],
    "group by 1 order by newest desc": Array.from({ length: 40 }, (_, i) =>
      ({ correlation_id: `1111111${i % 10}-2222-3333-4444-5555555555${String(i).padStart(2, "0")}` })),
    "count(*)::int as n from ops.incident_fact": [{ n: 173 }],
  });
  const out = await TOOLS["get-incident"].handler(fake, joe, { ref: "INC-1", fact_limit: 1 });
  assert.ok(out.correlations_total > out.facts.length,
    "the correlation count must not be bounded by the fact page size");
  assert.equal(out.correlations_followed, 25, "and the cap is the cap, applied to the real list");
  assert.ok(out.correlations_total > out.correlations_followed,
    "a cap that is never reported is a silent truncation");
});
