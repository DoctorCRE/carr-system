// incident-evidence-close.test.mjs — close-incident's evidence route
// (decision cbe91287: the system runs without Joe micromanaging it).
//
// A sponsored agent may close AND adjudicate an incident (resolved, reviewed,
// followup disposition recorded) without typing a root cause, but only when
// a deterministic predicate over the incident's OWN rows says the evidence is
// complete:
//
//   evidence_attached    the incident carries recovery evidence a collector wrote
//   watch_elapsed        it is monitoring and its window has run out
//   cause_recorded       a confirmed hypothesis, or a linked defect, states the cause
//   remediation_receipt  a linked defect's resolution receipt, a linked complete
//                        deployment, or a merged pull request naming the incident
//
// or it is adjudicated as a duplicate of an incident already closed. Nothing a
// caller says reaches the predicate, and the partner route (root_cause given)
// is unchanged for everything the predicate cannot settle.
//
// Run with: node --test mcp-server/test/incident-evidence-close.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { incidentEvidenceVerdict } from "../src/incident.js";
import { TOOLS, ToolError } from "../src/tools.js";

const NOW = "2026-10-12T12:00:00.000Z";
const BEFORE = "2026-10-11T12:00:00.000Z";
const AFTER = "2026-10-13T12:00:00.000Z";

const agent = { id: "10000000-0000-0000-0000-000000000009", slug: "codex", human: false,
  via: "oauth-google", sponsoring_human_slug: "joe", native_agent_verified: true };
const probe = { id: "10000000-0000-0000-0000-000000000008", slug: "smoke-probe",
  human: false, probe: true, via: "probe-token" };

const incident = (over = {}) => ({ ref: "INC-20260901-03", state: "monitoring",
  recovery_evidence_ref: "ops.run:8c9e3adf-a3d5-46e6-b134-eee5a60d1b80",
  monitoring_until: BEFORE, duplicate_of: null, ...over });
const complete = (over = {}) => ({
  incident: incident(), now: NOW,
  confirmedHypotheses: ["the rotated credential was never reloaded by the job"],
  linkedDefects: [], completedDeployments: [], mergedPullRequests: [], canonical: null, ...over });

// ── 1. THE PREDICATE ────────────────────────────────────────────────────────

test("complete evidence closes, and the root cause comes from the incident's own rows", () => {
  const v = incidentEvidenceVerdict(complete({ completedDeployments: ["d-1"] }));
  assert.equal(v.complete, true);
  assert.deepEqual(v.missing, []);
  assert.match(v.root_cause, /rotated credential was never reloaded/);
  assert.match(v.followup_disposition, /ops\.deployment:d-1/);
  assert.equal(v.evidence_ref, "ops.run:8c9e3adf-a3d5-46e6-b134-eee5a60d1b80");
});

test("each missing piece of evidence is named, and any one of them holds the close", () => {
  const remediated = { completedDeployments: ["d-1"] };
  const cases = [
    ["evidence_attached", complete({ ...remediated, incident: incident({ recovery_evidence_ref: null }) })],
    ["watch_elapsed", complete({ ...remediated, incident: incident({ monitoring_until: AFTER }) })],
    ["watch_elapsed", complete({ ...remediated, incident: incident({ state: "detected" }) })],
    ["watch_elapsed", complete({ ...remediated, incident: incident({ monitoring_until: null }) })],
    ["cause_recorded", complete({ ...remediated, confirmedHypotheses: [] })],
    ["remediation_receipt", complete()],
  ];
  for (const [missing, snapshot] of cases) {
    const v = incidentEvidenceVerdict(snapshot);
    assert.equal(v.complete, false, missing);
    assert.deepEqual(v.missing, [missing], `${missing} must be the only thing named`);
    assert.equal(v.root_cause, null, "an incomplete verdict carries no root cause to write");
  }
});

test("a linked defect states the cause, and its resolution receipt is the remediation", () => {
  const defect = { id: "6b1d4c2e-0000-4000-8000-000000000001", defect_class: "stale-credential-cache",
    claimed: "the job reloads credentials", actual: "it cached them for the process lifetime",
    resolution: null };
  const unresolved = incidentEvidenceVerdict(complete({ confirmedHypotheses: [], linkedDefects: [defect] }));
  assert.deepEqual(unresolved.missing, ["remediation_receipt"],
    "a defect is a recorded cause, not a fix: without its close receipt nothing was remediated");
  const fixed = incidentEvidenceVerdict(complete({ confirmedHypotheses: [], linkedDefects: [{
    ...defect, resolution: { id: "7c2e5d3f-0000-4000-8000-000000000002",
      evidence_kind: "commit", evidence_ref: "abc1234" } }] }));
  assert.equal(fixed.complete, true);
  assert.match(fixed.root_cause, /stale-credential-cache/);
  assert.match(fixed.root_cause, /cached them for the process lifetime/);
  assert.match(fixed.followup_disposition, /defect-resolution:7c2e5d3f-0000-4000-8000-000000000002/);
});

test("a merged pull request naming the incident is a remediation receipt", () => {
  const v = incidentEvidenceVerdict(complete({ mergedPullRequests: ["recovery evidence attached: PR #1700"] }));
  assert.equal(v.complete, true);
  assert.match(v.followup_disposition, /PR #1700/);
});

test("a duplicate of an incident already closed closes on the canonical's record", () => {
  const v = incidentEvidenceVerdict(complete({
    incident: incident({ state: "detected", recovery_evidence_ref: null, monitoring_until: null,
      duplicate_of: "INC-20260830-01" }),
    confirmedHypotheses: [],
    canonical: { ref: "INC-20260830-01", state: "reviewed", root_cause: "disk filled" } }));
  assert.equal(v.complete, true);
  assert.equal(v.evidence_ref, "ops.incident:INC-20260830-01");
  assert.match(v.root_cause, /INC-20260830-01/);
  assert.match(v.root_cause, /disk filled/);
  const open = incidentEvidenceVerdict(complete({
    incident: incident({ duplicate_of: "INC-20260830-01" }), confirmedHypotheses: [],
    canonical: { ref: "INC-20260830-01", state: "investigating", root_cause: null } }));
  assert.equal(open.complete, false, "a duplicate of a live incident waits for that incident");
});

test("an incident already closed is never complete again", () => {
  for (const state of ["resolved", "reviewed"]) {
    const v = incidentEvidenceVerdict(complete({ completedDeployments: ["d-1"], incident: incident({ state }) }));
    assert.equal(v.complete, false);
    assert.deepEqual(v.missing, ["open_incident"]);
  }
});

// ── 2. THE DOOR ─────────────────────────────────────────────────────────────

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

const loaded = (over = {}) => ({
  "from tool_call where idempotency_key": [],
  "from ops.incident i left join": [{ id: "inc-1", ...incident(), duplicate_of_id: null, db_now: NOW, ...over }],
  "from ops.incident_hypothesis": [{ text: "the rotated credential was never reloaded by the job" }],
});

test("close-incident refuses while the evidence is incomplete, and names what is missing", async () => {
  const fake = new Fake(loaded());
  const payload = await refuse(() => TOOLS["close-incident"].handler(fake, agent,
    { idempotency_key: "k-ev-1", ref: "INC-20260901-03" }));
  assert.equal(payload.error, "evidence_incomplete");
  assert.deepEqual(payload.missing, ["remediation_receipt"]);
  assert.match(payload.hint, /root_cause/, "the human route stays open and must be named");
  assert.ok(!fake.sql.some(([s]) => s.includes("update ops.incident")), "a refused close writes nothing");
});

test("close-incident closes and adjudicates on complete evidence, with no story from the caller", async () => {
  const fake = new Fake({ ...loaded(),
    "join ops.deployment d": [{ id: "d-1" }] });
  const out = await TOOLS["close-incident"].handler(fake, agent,
    { idempotency_key: "k-ev-2", ref: "INC-20260901-03" });
  assert.equal(out.state, "reviewed");
  assert.equal(out.closed_on, "complete_evidence");
  assert.match(out.root_cause, /rotated credential/);
  const update = fake.sql.find(([s]) => s.includes("update ops.incident set state = 'reviewed'"));
  assert.ok(update, "the close has to write the reviewed state");
  assert.ok(update[0].includes("state not in ('resolved','reviewed')"),
    "the update is guarded so a race cannot close a closed row twice");
  assert.ok(update[0].includes("followup_disposition") && update[0].includes("reviewed_at"),
    "the adjudication half: the followup disposition is recorded, not left for a person");
  assert.ok(fake.sql.some(([s, p]) => s.includes("insert into ops.incident_fact") &&
    String(p[1]).startsWith("closed on complete evidence")),
    "the reason the row closed belongs on the incident");
});

test("the evidence route takes no root cause, evidence or exception from its caller", async () => {
  for (const extra of [{ evidence: "trust me" }, { allow_early: "in a hurry" }]) {
    const fake = new Fake(loaded());
    const payload = await refuse(() => TOOLS["close-incident"].handler(fake, agent,
      { idempotency_key: "k-ev-3", ref: "INC-20260901-03", ...extra }));
    assert.equal(payload.error, "evidence_route_takes_no_story");
    assert.ok(!fake.sql.some(([s]) => s.includes("update ops.incident")));
  }
});

test("the evidence route is still partner authority: an unsponsored runtime is refused first", async () => {
  const fake = new Fake(loaded());
  const payload = await refuse(() => TOOLS["close-incident"].handler(fake, probe,
    { idempotency_key: "k-ev-4", ref: "INC-20260901-03" }));
  assert.equal(payload.error, "partner_authority_required");
  assert.deepEqual(fake.sql, []);
});

test("root_cause is optional in the schema; giving it selects the partner route", () => {
  const schema = TOOLS["close-incident"].inputSchema;
  assert.deepEqual([...schema.required].sort(), ["idempotency_key", "ref"]);
  assert.match(TOOLS["close-incident"].description, /complete evidence/i);
});
