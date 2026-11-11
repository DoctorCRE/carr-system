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
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { authorizationClassForActor } from "../src/identity.js";

const humanOnlyVerbs = Object.keys(TOOLS).filter((name) => TOOLS[name].humanOnly === true).sort();
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
const WATCH_V2_VERBS = [
  "list-subject-watches-v2",
  "pause-subject-watch-v2",
  "read-subject-watch-v2",
  "replace-subject-watch-policy-v2",
  "resume-subject-watch-v2",
  "unwatch-subject-v2",
  "watch-subject-v2",
];
const watchV2Migration = readFileSync(new URL("../../migrations/1162_subject_watch_v2.sql", import.meta.url), "utf8");
const watchActorMigration = readFileSync(new URL("../../migrations/1154_subject_watches.sql", import.meta.url), "utf8");

const forbiddenClient = {
  query: async (text) => {
    throw new Error(`the human-only gate let a call reach the database: ${String(text).slice(0, 80)}`);
  },
};

test("the registry still carries the humanOnly verbs this gate was built for", () => {
  assert.ok(humanOnlyVerbs.length > 0, "no humanOnly verb is declared anywhere in the registry");
  // The full set as of this commit. A change here is not a failure to paper
  // over: it means a verb gained or lost the flag, and that is a decision
  // somebody should see in a diff.
  assert.deepEqual(humanOnlyVerbs, [
    "abandon-deal-deliverable",
    "accept-outcome-feedback",

    "accept-ready-plan",
    "accept-ready-plan-amendment",
    // WR-000562 S4: the one door from a convention proposal to an active rule.
    "adopt-rule",

    "answer-board-question",
    "answer-work-request-for-joe",
    // WR-000529: a human entrance verification, previously authorityOnly alone.
    "append-tour-entrance-verification-receipt",
    "approve-lead-draft",
    "approve-lead-drafts",
    "approve-lead-move",
    "approve-retrieval-proposals",
    "backfill-deal-items",
    "cancel-pending-deal",
    "claim-lead",
    "clear-the-deck-apply",
    "commit-winning-property",

    "complete-deal-item",
    "complete-one-action",
    "confirm-email-deal-progress", "confirm-email-filing", "confirm-email-next-step",
    "confirm-merge",
    "confirm-requirement-reply",
    "decide-doc-suggestion",
    "dismiss-email-proposal",
    "drop-action",
    "edit-email-draft",
    "edit-lead-draft",
    "finalize-deal-deliverable",
    "handle-email-proposal",
    "instruct-deal-item",
    "invalidate-web-push-session-v1",
    "link-lead-client",
    "propose-doc-correction",
    "read-deal-document-text",
    "read-deal-space-searches-v1",
    "read-home-review",
    "read-web-push-config-v1", "read-web-push-delivery-status-v1", "read-web-push-subscriptions-v1",
    // V5-F01: installing the field-authority/retention policy and appending a
    // preservation hold are partner acts on the authority connection.
    "record-artifact-preservation-hold",
    // DoctorCRE V5-J103: a partner's own consent for the local mail and
    // calendar adapter to READ their own carr.us mailbox, and its withdrawal.
    "record-commission-receipt",
    "record-correspondence-adapter-consent",
    "record-deal-closing",
    "record-deal-item",
    // WR-000529: J102 partner statements the store already refuses to machines.
    "record-evidence-subject-link",
    "record-human-fact",
    // V5-J102: a lifecycle correction rewrites recorded history, so it is a
    // partner act on the authority connection.
    "record-lifecycle-correction",
    "record-partner-lifecycle-fact",
    "record-tour-map-promotion-receipt",
    "record-tour-pdf-human-review",
    "register-client-portal",
    "register-record-source-authority-policy",
    "register-web-push-subscription-v1",
    "reject-requirement-reply",
    "reopen-one-action",
    "resolve-action-link-v2",
    "resolve-critical-date",
    "resolve-fact-review",
    "resolve-post-call-candidate",
    "retire-retrieval-curation",
    "review-and-triage",
    "review-call-outcome",
    "review-deal-update",
    "revoke-correspondence-adapter-consent",
    // V5-RW02: only a verified partner revokes Dell's Salesforce read consent.
    "revoke-salesforce-read-consent",
    "revoke-web-push-subscription-v1",
    "run-deal-space-search-v1",
    "save-deal-space-search-v1",
    "set-deal-deliverable-visibility",
    "set-prospect-engagement-status",
    "snooze-email-proposal",
    "start-deal-deliverable",
    "submit-doc-request",
    "submit-email-draft",
    "undo-call",
    "undo-email-thread-filing",
    "undo-invoice-close",
    "undo-lead-move",
    ...WATCH_V2_VERBS,
    "read-subject-watch-changes-v1",
  ].sort());
});

test("the actor classes this gate refuses are the classes identity.js derives", () => {
  for (const [label, actor, expected] of REFUSED_ACTORS)
    assert.equal(authorizationClassForActor(actor), expected, label);
  assert.equal(authorizationClassForActor(joe), "verified_partner");
});

// THE BLOCKING FINDING ITSELF, named on its own so a failure reads as the
// release defect it is rather than as one row of a loop.

test("verified sponsor-bound agents pass partner-authority gates except literal-human acts", async () => {
  for (const verb of humanOnlyVerbs.filter(name => TOOLS[name].humanCallerOnly !== true)) {
    for (const [label, actor] of SPONSORED_PARTNER_ACTORS) {
      const error = await executeRegisteredTool(forbiddenClient, actor, verb, {})
        .then(() => null, (e) => e);
      assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner", `${verb}: ${label}`);
    }
  }
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

// A verb WITHOUT the flag is untouched by this change. Joe's 2026-08-26 ruling
// (decision dc57f62d) is about those 220 verbs, and re-enforcing humanOnly must
// not quietly widen back into them.
test("a verb that is not humanOnly is unchanged for a sponsored agent", async () => {
  assert.equal(TOOLS["report-problem"].humanOnly === true, false);
  const error = await executeRegisteredTool(forbiddenClient, joeLocal, "report-problem", {})
    .then(() => null, (e) => e);
  assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner");
});

const pushVerbs = ['register-web-push-subscription-v1', 'revoke-web-push-subscription-v1',
  'read-web-push-delivery-status-v1', 'read-web-push-subscriptions-v1', 'invalidate-web-push-session-v1'];
test('push browser guards and registry authority metadata agree', async () => {
  for (const name of pushVerbs) {
    assert.equal(TOOLS[name].humanOnly, true, name);
    assert.equal(TOOLS[name].humanCallerOnly, true, name);
    assert.ok(humanOnlyVerbs.includes(name), name);
    for (const [, actor] of SPONSORED_PARTNER_ACTORS) {
      const error = await executeRegisteredTool(forbiddenClient, actor, name, {}).catch(e => e);
      assert.equal(error.payload.error, 'human_only_verb_requires_verified_partner', name);
      await assert.rejects(TOOLS[name].handler(forbiddenClient,
        {...actor, slug: 'joe', web_push_session: 'a'.repeat(64)}, {}),
        e => e.payload.error === 'push_session_required', name);
    }
    await assert.rejects(TOOLS[name].handler(forbiddenClient, joe, {}),
      e => e.payload.error === 'push_session_required', name);
  }
});
const documentTextProducers = {
  "record-deal-document-text": {
    document_ref: "20000000-0000-4000-8000-000000000001",
    entity_ref: "20000000-0000-4000-8000-000000000002",
    content_hash: "a".repeat(64), text_format_version: "page-text.v1",
    status: "produced", pages: ["Synthetic retained text"],
  },
  "list-deal-document-text-sources": { limit: 1 },
};

for (const [verb, args] of Object.entries(documentTextProducers)) {
  test(`${verb} automation metadata matches its registered handler guard`, async () => {
    assert.notEqual(TOOLS[verb].humanOnly, true);
    assert.notEqual(TOOLS[verb].authorityOnly, true);
    assert.notEqual(TOOLS[verb].humanCallerOnly, true);
    assert.equal(humanOnlyVerbs.includes(verb), false);
    for (const actor of [joe, probe, reviewer]) {
      await assert.rejects(executeRegisteredTool(forbiddenClient, actor, verb, args), error => {
        assert.ok(error instanceof ToolError);
        assert.equal(error.payload.error, "not_in_profile", actor.slug);
        return true;
      });
    }
    const reachedDatabase = new Error("automation producer reached the database");
    const client = { query: async () => { throw reachedDatabase; } };
    for (const actor of [joeLocal, claudeForJoe, stranger]) {
      await assert.rejects(executeRegisteredTool(client, actor, verb, args),
        error => error === reachedDatabase, actor.slug);
    }
  });
}

for (const verb of WATCH_V2_VERBS) {
  test(`${verb} metadata matches the literal-human store guard`, () => {
    assert.match(watchV2Migration, /a uuid:=ops\.subject_watch_actor\(\)/);
    assert.match(watchActorMigration, /kind='human' and slug in \('joe','dell'\)/);
    assert.equal(TOOLS[verb].humanOnly, true, verb);
    assert.equal(TOOLS[verb].humanCallerOnly, true, verb);
  });
  test(`${verb} refuses registered unattended dispatch before database access`, async () => {
    const error = await executeRegisteredTool(forbiddenClient, { ...joeLocal, unattended: true, via: "unattended-token" }, verb, {}).then(() => null, e => e);
    assert.ok(error instanceof ToolError);
    assert.equal(error.payload.error, "human_only_verb_requires_verified_partner");
    assert.equal(error.payload.verb, verb);
    assert.equal(error.payload.actor_class, "sponsored_agent");
  });
}
