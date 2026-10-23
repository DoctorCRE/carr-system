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
//   cause_recorded       a linked defect already closed with close-defect
//   remediation_receipt  a linked defect's resolution receipt, a linked complete
//                        deployment, or a merged pull request naming the
//                        incident, that is NOT the recovery evidence itself
//
// or it is adjudicated as a duplicate of an incident already closed. SEV-0,
// SEV-1 and security-class incidents never take this route, and a recurrence
// between the read and the write refuses the close. Nothing a caller says
// reaches the predicate, and the partner route (root_cause given) is unchanged
// for everything the predicate cannot settle.
//
// Run with: node --test mcp-server/test/incident-evidence-close.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { incidentEvidenceVerdict, receiptIdentity } from "../src/incident.js";
import { TOOLS, ToolError } from "../src/tools.js";

const NOW = "2026-10-12T12:00:00.000Z";
const BEFORE = "2026-10-11T12:00:00.000Z";
const AFTER = "2026-10-13T12:00:00.000Z";
const RUN = "ops.run:8c9e3adf-a3d5-46e6-b134-eee5a60d1b80";

const agent = { id: "10000000-0000-0000-0000-000000000009", slug: "codex", human: false,
  via: "oauth-google", sponsoring_human_slug: "joe", native_agent_verified: true };
const probe = { id: "10000000-0000-0000-0000-000000000008", slug: "smoke-probe",
  human: false, probe: true, via: "probe-token" };

const incident = (over = {}) => ({ ref: "INC-20260901-03", state: "monitoring", severity: "SEV-3",
  title: "nightly export job failed", fingerprint: "exporter|production|nightly-export|exit_1",
  recovery_evidence_ref: RUN, monitoring_until: BEFORE, duplicate_of: null, ...over });
const defect = (resolution) => ({ id: "6b1d4c2e-0000-4000-8000-000000000001",
  defect_class: "stale-credential-cache", claimed: "the job reloads credentials",
  actual: "it cached them for the process lifetime", resolution });
const fixedByPr = (n = "1700") => defect({ id: "7c2e5d3f-0000-4000-8000-000000000002",
  evidence_kind: "pull_request", evidence_ref: n });
const complete = (over = {}) => ({
  incident: incident(), now: NOW, linkedDefects: [fixedByPr()],
  completedDeployments: [], mergedPullRequests: [], canonical: null, ...over });

// ── 1. THE PREDICATE ────────────────────────────────────────────────────────

test("complete evidence closes, and the root cause comes from the resolved linked defect", () => {
  const v = incidentEvidenceVerdict(complete());
  assert.equal(v.complete, true);
  assert.deepEqual(v.missing, []);
  assert.match(v.root_cause, /stale-credential-cache/);
  assert.match(v.root_cause, /cached them for the process lifetime/);
  assert.match(v.followup_disposition, /defect-resolution:7c2e5d3f-0000-4000-8000-000000000002/);
  assert.equal(v.evidence_ref, RUN);
});

test("each missing piece of evidence is named, and any one of them holds the close", () => {
  const cases = [
    ["evidence_attached", complete({ incident: incident({ recovery_evidence_ref: null }) })],
    ["watch_elapsed", complete({ incident: incident({ monitoring_until: AFTER }) })],
    ["watch_elapsed", complete({ incident: incident({ state: "detected" }) })],
    ["watch_elapsed", complete({ incident: incident({ monitoring_until: null }) })],
    ["cause_recorded", complete({ linkedDefects: [], completedDeployments: ["d-1"] })],
    ["remediation_receipt", complete({ incident: incident({ recovery_evidence_ref: "pr:1700" }) })],
  ];
  for (const [missing, snapshot] of cases) {
    const v = incidentEvidenceVerdict(snapshot);
    assert.equal(v.complete, false, missing);
    assert.deepEqual(v.missing, [missing], `${missing} must be the only thing named`);
    assert.equal(v.root_cause, null, "an incomplete verdict carries no root cause to write");
  }
});

test("an open linked defect is a suspicion, not a recorded cause", () => {
  const v = incidentEvidenceVerdict(complete({ linkedDefects: [defect(null)], completedDeployments: ["d-1"] }));
  assert.equal(v.complete, false);
  assert.deepEqual(v.missing, ["cause_recorded"],
    "cause_recorded must point at a RESOLVED linked defect, not any linked defect");
});

test("the recovery evidence and the remediation receipt must be two distinct receipts", () => {
  // One PR as both: the recovery says pr:1700, the defect was closed on PR 1700
  // and the only merged-PR fact is the same PR. That is one receipt, not two.
  const one = incidentEvidenceVerdict(complete({ incident: incident({ recovery_evidence_ref: "pr:1700" }),
    mergedPullRequests: ["recovery evidence attached: PR #1700"] }));
  assert.equal(one.complete, false);
  assert.deepEqual(one.missing, ["remediation_receipt"]);
  // The defect closed on the very run that is the recovery evidence: same receipt.
  const sameRun = incidentEvidenceVerdict(complete({ linkedDefects: [defect({ id: "r-1",
    evidence_kind: "receipt", evidence_ref: RUN.toUpperCase().replace("OPS.RUN", "ops.run") })] }));
  assert.deepEqual(sameRun.missing, ["remediation_receipt"], "case does not make a receipt distinct");
  // A second, different receipt makes it complete.
  const two = incidentEvidenceVerdict(complete({ incident: incident({ recovery_evidence_ref: "pr:1700" }),
    completedDeployments: ["d-1"] }));
  assert.equal(two.complete, true);
  assert.match(two.followup_disposition, /ops\.deployment:d-1/);
  assert.doesNotMatch(two.followup_disposition, /1700/, "the recovery receipt is not listed as the fix");
  assert.equal(receiptIdentity("#1700"), receiptIdentity("PR-1700"));
});

test("a merged pull request naming the incident is a remediation receipt", () => {
  const v = incidentEvidenceVerdict(complete({ linkedDefects: [defect({ id: "r-2",
    evidence_kind: "receipt", evidence_ref: RUN })], mergedPullRequests: ["recovery evidence attached: PR #1701"] }));
  assert.equal(v.complete, true);
  assert.match(v.followup_disposition, /PR #1701/);
});

test("SEV-0, SEV-1 and security-class incidents keep human review", () => {
  for (const severity of ["SEV-0", "SEV-1"]) {
    const v = incidentEvidenceVerdict(complete({ incident: incident({ severity }) }));
    assert.equal(v.complete, false, severity);
    assert.deepEqual(v.missing, ["human_review_required"]);
    assert.match(v.human_review_reason, new RegExp(severity));
  }
  for (const over of [{ title: "credential leak in the exporter" },
                      { fingerprint: "exporter|production|nightly-export|permission_denied" }]) {
    const v = incidentEvidenceVerdict(complete({ incident: incident(over) }));
    assert.deepEqual(v.missing, ["human_review_required"], JSON.stringify(over));
    assert.match(v.human_review_reason, /security/);
  }
  assert.equal(incidentEvidenceVerdict(complete({ incident: incident({ severity: "SEV-2" }) })).complete, true,
    "SEV-2 and below may still close on evidence");
  const dup = incidentEvidenceVerdict(complete({ incident: incident({ severity: "SEV-1", duplicate_of: "INC-20260830-01" }),
    canonical: { ref: "INC-20260830-01", state: "reviewed", root_cause: "disk filled" } }));
  assert.equal(dup.complete, false, "a SEV-1 duplicate is still a SEV-1");
});

test("a duplicate of an incident already closed closes on the canonical's record", () => {
  const v = incidentEvidenceVerdict(complete({
    incident: incident({ state: "detected", recovery_evidence_ref: null, monitoring_until: null,
      duplicate_of: "INC-20260830-01" }),
    linkedDefects: [],
    canonical: { ref: "INC-20260830-01", state: "reviewed", root_cause: "disk filled" } }));
  assert.equal(v.complete, true);
  assert.equal(v.duplicate, true);
  assert.equal(v.evidence_ref, "ops.incident:INC-20260830-01");
  assert.match(v.root_cause, /INC-20260830-01/);
  assert.match(v.root_cause, /disk filled/);
  const open = incidentEvidenceVerdict(complete({
    incident: incident({ duplicate_of: "INC-20260830-01" }), linkedDefects: [],
    canonical: { ref: "INC-20260830-01", state: "investigating", root_cause: null } }));
  assert.equal(open.complete, false, "a duplicate of a live incident waits for that incident");
});

test("an incident already closed is never complete again", () => {
  for (const state of ["resolved", "reviewed"]) {
    const v = incidentEvidenceVerdict(complete({ incident: incident({ state }) }));
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

const defectRow = { id: "6b1d4c2e-0000-4000-8000-000000000001", defect_class: "stale-credential-cache",
  claimed: "the job reloads credentials", actual: "it cached them for the process lifetime",
  resolution_id: "7c2e5d3f-0000-4000-8000-000000000002", evidence_kind: "pull_request", evidence_ref: "1700" };
const loaded = (over = {}, plan = {}) => ({
  "from tool_call where idempotency_key": [],
  ...plan,
  "from ops.incident i left join": [{ id: "inc-1", ...incident(), duplicate_of_id: null, db_now: NOW, ...over }],
  "join public.defect d": [defectRow],
});
