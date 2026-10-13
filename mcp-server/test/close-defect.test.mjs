// close-defect.test.mjs — the defect log's close door (decision cbe91287).
//
// A defect must ACCUMULATE (record-defect's own contract), so closing one is
// never a delete and never a state on the defect row. It is one append-only
// resolution receipt naming the fix. The two failures this suite is aimed at:
//
//   1. A close with no evidence. A pile cleared by hand-waving LOOKS handled,
//      which is worse than a pile. Every refusal below happens before the
//      database is touched, so nothing half-written can follow.
//   2. A second close that quietly rewrites the first. The receipt is what a
//      reader stands on; a later call with different evidence is refused, and
//      the same evidence twice is the same act, not a second row.
//   3. A close on evidence the server has never seen. The receipt is later
//      bound as Work Request and incident evidence, so a well-formed sha or PR
//      number is not enough: ops.defect_resolution_evidence_receipt must
//      resolve it to a row (a merged and shipped release slice member, a
//      succeeded run, a complete deployment, a resolved incident) first.
//
// Run with: node --test mcp-server/test/close-defect.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { resolutionEvidence } from "../src/defect-resolution.js";
import { TOOLS, ToolError } from "../src/tools.js";
import { PROFILES } from "../src/mcp.js";

const DEFECT = "6b1d4c2e-0000-4000-8000-000000000001";
const RESOLUTION = "7c2e5d3f-0000-4000-8000-000000000002";
const MEMBER = "release-slice-member:8d3f6e40-0000-4000-8000-000000000003";
const RESOLVES = { "ops.defect_resolution_evidence_receipt": [{ receipt: MEMBER }] };
const joeLocal = { id: "10000000-0000-0000-0000-000000000007", slug: "joe-local",
  human: false, agent: true, via: "local-token", sponsoring_human_slug: "joe",
  native_agent_verified: true };

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

const close = (fake, args) => TOOLS["close-defect"].handler(fake, joeLocal,
  { idempotency_key: "k-close-1", defect_id: DEFECT, ...args });

// ── 1. THE EVIDENCE IS THE CLOSE ────────────────────────────────────────────

test("close-defect without evidence refuses before touching the database", async () => {
  for (const args of [{}, { evidence_kind: "commit" }, { evidence_ref: "abc1234" },
                      { evidence_kind: "commit", evidence_ref: "   " }]) {
    const fake = new Fake();
    const payload = await refuse(() => close(fake, args));
    assert.equal(payload.error, "resolution_evidence_required", JSON.stringify(args));
    assert.ok(!fake.sql.some(([s]) => s.includes("defect_resolution")),
      "a refused close must not have written anything");
  }
});

test("each evidence kind has one shape, and anything else is refused by name", () => {
  assert.deepEqual(resolutionEvidence({ evidence_kind: "commit", evidence_ref: " ABC1234 " }),
    { evidence_kind: "commit", evidence_ref: "abc1234" });
  for (const ref of ["#1711", "PR-1711", "pr:1711", "1711",
                     "https://github.com/jbookout/carr-system/pull/1711"])
    assert.deepEqual(resolutionEvidence({ evidence_kind: "pull_request", evidence_ref: ref }),
      { evidence_kind: "pull_request", evidence_ref: "1711" }, ref);
  assert.deepEqual(resolutionEvidence({ evidence_kind: "receipt",
    evidence_ref: "ops.run:8c9e3adf-a3d5-46e6-b134-eee5a60d1b80" }),
    { evidence_kind: "receipt", evidence_ref: "ops.run:8c9e3adf-a3d5-46e6-b134-eee5a60d1b80" });
  for (const [kind, ref] of [["commit", "abc"], ["commit", "not-a-sha"], ["pull_request", "0"],
                             ["pull_request", "the fix"], ["receipt", "fixed it"],
                             ["receipt", "no-colon"], ["memory", "abc1234"]]) {
    assert.throws(() => resolutionEvidence({ evidence_kind: kind, evidence_ref: ref }),
      (e) => e instanceof ToolError && e.payload.error === "invalid_resolution_evidence",
      `${kind}:${ref} must be refused`);
  }
});

test("a malformed defect id is refused before the database is asked", async () => {
  const fake = new Fake();
  const payload = await refuse(() => close(fake, { defect_id: "D-12",
    evidence_kind: "commit", evidence_ref: "abc1234" }));
  assert.equal(payload.error, "invalid_defect_id");
  assert.ok(!fake.sql.some(([s]) => s.includes("public.defect")));
});

// ── 2. THE SERVER MUST HOLD THE EVIDENCE ────────────────────────────────────

const unresolved = (extra = {}) => new Fake({
  "from tool_call where idempotency_key": [],
  "from public.defect where id": [{ id: DEFECT, defect_class: "x" }],
  "ops.defect_resolution_evidence_receipt": [{ receipt: null }], ...extra });

test("a bare commit sha the server holds no record of is refused, and nothing is written", async () => {
  const fake = unresolved();
  const payload = await refuse(() => close(fake, { evidence_kind: "commit", evidence_ref: "abcdef0" }));
  assert.equal(payload.error, "unresolved_resolution_evidence");
  assert.equal(payload.evidence_ref, "abcdef0");
  const asked = fake.sql.find(([s]) => s.includes("ops.defect_resolution_evidence_receipt"));
  assert.deepEqual(asked[1], ["commit", "abcdef0"], "the server resolves the evidence, not the caller");
  assert.ok(!fake.sql.some(([s]) => s.includes("insert into public.defect_resolution")));
  assert.ok(!fake.sql.some(([s]) => s.startsWith("insert into event")));
});

test("a pull request number nobody recorded as merged is refused", async () => {
  const fake = unresolved();
  const payload = await refuse(() => close(fake, { evidence_kind: "pull_request", evidence_ref: "#999999" }));
  assert.equal(payload.error, "unresolved_resolution_evidence");
  assert.match(payload.hint, /bare sha or PR number is not evidence/);
  assert.ok(!fake.sql.some(([s]) => s.includes("insert into public.defect_resolution")));
});

test("a receipt the server resolves is accepted, and an uppercase uuid is the same receipt", async () => {
  const run = "ops.run:8c9e3adf-a3d5-46e6-b134-eee5a60d1b80";
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from public.defect where id": [{ id: DEFECT, defect_class: "x" }],
    "ops.defect_resolution_evidence_receipt": (params) => [{ receipt: params[1] === run ? run : null }],
    "insert into public.defect_resolution": [{ id: RESOLUTION, created_at: "2026-10-12T12:00:00.000Z" }],
  });
  const out = await close(fake, { evidence_kind: "receipt", evidence_ref: run.toUpperCase().replace("OPS.RUN", "ops.run") });
  assert.equal(out.closed, true);
  assert.equal(out.evidence_ref, run, "stored in the one spelling every matcher uses");
  assert.equal(out.resolved_to, run);
  const insert = fake.sql.find(([s]) => s.includes("insert into public.defect_resolution"));
  assert.deepEqual(insert[1].slice(0, 3), [DEFECT, "receipt", run]);
});

test("a defect that does not exist is refused, and nothing is written", async () => {
  const fake = new Fake({ "from tool_call where idempotency_key": [] });
  const payload = await refuse(() => close(fake, { evidence_kind: "commit", evidence_ref: "abc1234" }));
  assert.equal(payload.error, "no_such_defect");
  assert.ok(!fake.sql.some(([s]) => s.includes("insert into public.defect_resolution")));
});

// ── 3. THE CLOSE ITSELF ─────────────────────────────────────────────────────

test("a close with evidence appends one resolution receipt and an event", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from public.defect where id": [{ id: DEFECT, defect_class: "success-signal-from-the-wrong-function" }],
    ...RESOLVES,
    "insert into public.defect_resolution": [{ id: RESOLUTION, created_at: "2026-10-12T12:00:00.000Z" }],
  });
  const out = await close(fake, { evidence_kind: "pull_request", evidence_ref: "#1711",
    note: "the gate now reads the right function" });
  assert.equal(out.closed, true);
  assert.equal(out.already_closed, false);
  assert.equal(out.receipt_ref, `defect-resolution:${RESOLUTION}`,
    "the receipt must be addressable, because incidents and Work Requests bind to it");
  assert.equal(out.evidence_ref, "1711");
  assert.equal(out.resolved_to, MEMBER, "the close names the server row it stands on");
  const insert = fake.sql.find(([s]) => s.includes("insert into public.defect_resolution"));
  assert.ok(insert[0].includes("on conflict (defect_id) do nothing"),
    "two racing closes must meet at the constraint, not both write");
  assert.deepEqual(insert[1].slice(0, 3), [DEFECT, "pull_request", "1711"]);
  assert.ok(fake.sql.some(([s, p]) => s.startsWith("insert into event") && p.includes("close-defect")),
    "the close must show up in catch-me-up like every other write");
  assert.ok(!fake.sql.some(([s]) => /update public\.defect\b|delete from public\.defect\b/.test(s)),
    "the defect row itself never changes: defects accumulate");
});

test("closing an already-closed defect with the same evidence returns the first receipt", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from public.defect where id": [{ id: DEFECT, defect_class: "x" }],
    ...RESOLVES,
    "insert into public.defect_resolution": [],
    "from public.defect_resolution r": [{ id: RESOLUTION, evidence_kind: "commit",
      evidence_ref: "abc1234", created_at: "2026-10-11T00:00:00.000Z", resolved_by: "codex" }],
  });
  const out = await close(fake, { evidence_kind: "commit", evidence_ref: "abc1234" });
  assert.equal(out.already_closed, true);
  assert.equal(out.receipt_ref, `defect-resolution:${RESOLUTION}`);
  assert.ok(!fake.sql.some(([s]) => s.startsWith("insert into event")),
    "the same act twice writes no second event");
});

test("closing an already-closed defect with different evidence is refused, naming the first", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "from public.defect where id": [{ id: DEFECT, defect_class: "x" }],
    ...RESOLVES,
    "insert into public.defect_resolution": [],
    "from public.defect_resolution r": [{ id: RESOLUTION, evidence_kind: "commit",
      evidence_ref: "abc1234", created_at: "2026-10-11T00:00:00.000Z", resolved_by: "codex" }],
  });
  const payload = await refuse(() => close(fake, { evidence_kind: "commit", evidence_ref: "def5678" }));
  assert.equal(payload.error, "defect_already_closed");
  assert.equal(payload.existing.evidence_ref, "abc1234");
  assert.equal(payload.existing.receipt_ref, `defect-resolution:${RESOLUTION}`);
});

// ── 4. THE REGISTRY ENTRY ───────────────────────────────────────────────────

test("close-defect is a keyed write that any session holding the full surface may call", () => {
  const tool = TOOLS["close-defect"];
  assert.equal(tool.write, true);
  assert.ok(!tool.humanOnly, "the point of the verb is that closing needs evidence, not Joe");
  assert.ok(!tool.authorityOnly);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual([...tool.inputSchema.required].sort(),
    ["defect_id", "evidence_kind", "evidence_ref", "idempotency_key"]);
  assert.deepEqual(tool.inputSchema.properties.evidence_kind.enum, ["commit", "pull_request", "receipt"]);
  assert.equal(PROFILES.full, null, "full is every verb, close-defect included");
});
