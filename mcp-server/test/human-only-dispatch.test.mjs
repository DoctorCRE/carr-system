// THE PARTNER-AUTHORITY DISPATCH GATE.
//
// What this pins: `humanOnly: true` is a refusal on the deployed path, not a
// label. It now means the verified partner OR a server-verified native/local
// agent whose sponsor and sponsor-scoped authority connection are both derived
// by the server. It does not mean "open a second chat and repeat yourself."
//
// THE ENUMERATION IS FROM THE REGISTRY, never a hand-written list: a thirteenth
// humanOnly verb added tomorrow is covered the moment it is declared, and the
// list assertion below fails loudly if the set changes without the change being
// noticed.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { authorizationClassForActor } from "../src/identity.js";

const humanOnlyVerbs = Object.keys(TOOLS).filter((name) => TOOLS[name].humanOnly === true).sort();
const foundationAssuranceMigrationPresent = existsSync(
  new URL("../../migrations/0508_foundation_assurance_minimum_receipt.sql", import.meta.url),
);

// The exact reproduction principal from the release review: a verified NONHUMAN
// local actor that partner-authority.js resolves to sponsor 'joe', i.e. the one
// that already holds the authority connection.
const joeLocal = {
  id: "10000000-0000-0000-0000-000000000010", slug: "joe-local", display: "Joe (local)",
  human: false, sponsoring_human_slug: "joe", native_agent_verified: true,
  via: "local-token", client_id: "local-verb",
};
const claudeForJoe = {
  id: "10000000-0000-0000-0000-000000000011", slug: "claude", display: "Claude",
  human: false, sponsoring_human_slug: "joe", native_agent_verified: true,
  via: "oauth-agent", client_id: "claude-client",
};
const probe = {
  id: "10000000-0000-0000-0000-000000000012", slug: "probe", display: "Probe",
  human: false, probe: true, via: "probe-token",
};
const reviewer = {
  id: "10000000-0000-0000-0000-000000000013", slug: "reviewer", display: "Reviewer",
  human: false, review: true, via: "review-token",
};
const stranger = {
  id: "10000000-0000-0000-0000-000000000014", slug: "grok", display: "Grok",
  human: false, via: "agent-token",
};
const joe = {
  id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth",
};

const REFUSED_ACTORS = [
  ["probe_agent", probe, "probe_agent"],
  ["review_agent", reviewer, "review_agent"],
  ["unsponsored_agent", stranger, "unsponsored_agent"],
];

const SPONSORED_PARTNER_ACTORS = [
  ["sponsored_agent (joe-local)", joeLocal],
  ["sponsored_agent (connector Claude for Joe)", claudeForJoe],
];

// A client that answers nothing. Reaching it at all means the gate let the call
// through, and the test then fails on this error instead of quietly passing.
const forbiddenClient = {
  query: async (text) => {
    throw new Error(`the human-only gate let a call reach the database: ${String(text).slice(0, 80)}`);
  },
};

test("the registry still carries the humanOnly verbs this gate was built for", () => {
  assert.ok(humanOnlyVerbs.length > 0, "no humanOnly verb is declared anywhere in the registry");
  assert.ok(humanOnlyVerbs.includes("accept-portfolio-revision"),
    "accept-portfolio-revision must stay humanOnly — it records the v5 constitution acceptance");
  // The full set as of this commit. A change here is not a failure to paper
  // over: it means a verb gained or lost the flag, and that is a decision
  // somebody should see in a diff.
  assert.deepEqual(humanOnlyVerbs, [
    ...(foundationAssuranceMigrationPresent ? ["accept-benchmark-manifest-draft"] : []),
    "accept-outcome-feedback",
    "accept-portfolio-revision",
    "accept-ready-plan",
    "accept-ready-plan-amendment",
    "adjudicate-incident",
    "answer-board-question",
    "answer-work-request-for-joe",
    "approve-lead-draft",
    "approve-lead-move",
    "assign-execution-route",
    "attest-attempt-evaluation",
    "attest-execution-environment-conformance",
    "claim-lead",
    "close-incident",
    "confirm-merge",
    "decide-doc-suggestion",
    "link-lead-client",
    "propose-doc-correction",
    // V5-F01: installing the field-authority/retention policy and appending a
    // preservation hold are partner acts on the authority connection.
    "record-artifact-preservation-hold",
    // DoctorCRE V5-J103: a partner's own consent for the local mail and
    // calendar adapter to READ their own carr.us mailbox, and its withdrawal.
    "record-commission-receipt",
    "record-correspondence-adapter-consent",
    // V5-J102: a lifecycle correction rewrites recorded history, so it is a
    // partner act on the authority connection.
    "record-lifecycle-correction",
    "record-tour-map-promotion-receipt",
    "record-tour-pdf-human-review",
    // V5-D01: registering an action-class successor permanently fixes its
    // owner and activation_predicate (unique, append-only, immutable), so the
    // first registration is a partner act on the authority connection.
    "register-action-class-successor",
    "register-record-source-authority-policy",
    "review-and-triage",
    "revoke-correspondence-adapter-consent",
    // V5-RW02: only a verified partner revokes Dell's Salesforce read consent.
    "revoke-salesforce-read-consent",
    "transition-execution-environment-provider",
    "undo-invoice-close",
    "undo-lead-move",
  ]);
});

test("the actor classes this gate refuses are the classes identity.js derives", () => {
  for (const [label, actor, expected] of REFUSED_ACTORS)
    assert.equal(authorizationClassForActor(actor), expected, label);
  assert.equal(authorizationClassForActor(joe), "verified_partner");
});

// THE BLOCKING FINDING ITSELF, named on its own so a failure reads as the
// release defect it is rather than as one row of a loop.
test("accept-portfolio-revision admits the verified nonhuman joe-local principal", async () => {
  const receiptId = "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d";
  const client = {
    query: async (text) => {
      if (text.includes("from tool_call where idempotency_key")) return { rows: [] };
      if (text.includes("ops.portfolio_accept_revision")) return { rows: [{ id: receiptId }] };
      if (text.includes("insert into tool_call") || text.includes("insert into event")) return { rows: [] };
      throw new Error(`unexpected SQL: ${text}`);
    },
  };
  const out = await executeRegisteredTool(client, joeLocal, "accept-portfolio-revision", {
    idempotency_key: "9f1d0c2e-6a7b-4c8d-9e0f-1a2b3c4d5e6f",
    revision_id: "2d3e4f50-6172-4839-8a9b-0c1d2e3f4a5b",
    accepted_digest: `sha256:${"a".repeat(64)}`,
    review_id: "3e4f5061-7283-494a-9b0c-1d2e3f4a5b6c",
  });
  assert.equal(out.ok, true);
  assert.equal(out.receipt_id, receiptId);
});

test("verified sponsor-bound agents pass partner-authority gates except human identity merges", async () => {
  for (const verb of humanOnlyVerbs.filter(name => name !== "confirm-merge")) {
    for (const [label, actor] of SPONSORED_PARTNER_ACTORS) {
      const error = await executeRegisteredTool(forbiddenClient, actor, verb, {})
        .then(() => null, (e) => e);
      assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner", `${verb}: ${label}`);
    }
  }
});

test("an unverified actor cannot self-assert Joe as sponsor", async () => {
  const unverified = { ...joeLocal, native_agent_verified: false };
  const error = await executeRegisteredTool(forbiddenClient, unverified, "accept-portfolio-revision", {
    idempotency_key: "9f1d0c2e-6a7b-4c8d-9e0f-1a2b3c4d5e6f",
    revision_id: "2d3e4f50-6172-4839-8a9b-0c1d2e3f4a5b",
    accepted_digest: `sha256:${"a".repeat(64)}`,
    review_id: "3e4f5061-7283-494a-9b0c-1d2e3f4a5b6c",
  }).then(() => null, (e) => e);
  assert.ok(error instanceof ToolError, `expected a ToolError, got ${error}`);
  assert.equal(error.payload.error, "human_only_verb_requires_verified_partner");
  assert.equal(error.payload.verb, "accept-portfolio-revision");
  assert.equal(error.payload.actor_class, "sponsored_agent");
});

// EVERY humanOnly verb, EVERY nonhuman class. Arguments are deliberately empty:
// the identity gate runs ahead of registry and schema validation, so a refusal
// that names a missing field instead of the actor class is itself a failure —
// it would mean the gate sits behind something a caller can influence.
for (const verb of humanOnlyVerbs) {
  for (const [label, actor, expectedClass] of REFUSED_ACTORS) {
    test(`${verb} refuses ${label}`, async () => {
      const error = await executeRegisteredTool(forbiddenClient, actor, verb, {})
        .then(() => null, (e) => e);
      assert.ok(error instanceof ToolError, `expected a ToolError, got ${error}`);
      assert.equal(error.payload.error, "human_only_verb_requires_verified_partner");
      assert.equal(error.payload.verb, verb);
      assert.equal(error.payload.actor_class, expectedClass);
    });
  }
}

// The other half of a gate: it has to let the right caller through. This asserts
// the PASS, not the whole verb — a verified partner reaches the existing
// behaviour, and the existing behaviour is what the rest of the suite covers.
test("a verified human partner passes the gate into the existing behaviour", async () => {
  const receiptId = "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d";
  const seen = [];
  const client = {
    query: async (text, params = []) => {
      seen.push(text);
      if (text.includes("from tool_call where idempotency_key")) return { rows: [] };
      if (text.includes("ops.portfolio_accept_revision")) return { rows: [{ id: receiptId }] };
      if (text.includes("insert into tool_call")) return { rows: [] };
      if (text.includes("insert into event")) return { rows: [] };
      throw new Error(`unexpected SQL: ${text} ${JSON.stringify(params)}`);
    },
  };
  const out = await executeRegisteredTool(client, joe, "accept-portfolio-revision", {
    idempotency_key: "1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9",
    revision_id: "2d3e4f50-6172-4839-8a9b-0c1d2e3f4a5b",
    accepted_digest: `sha256:${"b".repeat(64)}`,
    review_id: "3e4f5061-7283-494a-9b0c-1d2e3f4a5b6c",
  });
  assert.equal(out.ok, true);
  assert.equal(out.receipt_id, receiptId);
  assert.equal(out.accepted, true);
  assert.ok(seen.some((t) => t.includes("ops.portfolio_accept_revision")),
    "the human call must reach the acceptance function, not stop at the gate");
});

// A verb WITHOUT the flag is untouched by this change. Joe's 2026-08-26 ruling
// (decision dc57f62d) is about those 220 verbs, and re-enforcing humanOnly must
// not quietly widen back into them.
test("a verb that is not humanOnly is unchanged for a sponsored agent", async () => {
  assert.equal(TOOLS["report-problem"].humanOnly === true, false);
  const error = await executeRegisteredTool(forbiddenClient, joeLocal, "report-problem", {})
    .then(() => null, (e) => e);
  assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner");
});
