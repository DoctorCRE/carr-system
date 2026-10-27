// WR-000529 — the remaining operations whose handler or store requires a LITERAL
// human now say so in their registration, so registered dispatch refuses a
// machine before the database instead of admitting it and failing later.
//
// Audit F4/F5 (PR #2183): each verb below published humanOnly as "partner
// authority" (which sponsored agents and the unattended runner may exercise) or
// published authorityOnly alone, while its handler or store refused every
// identity but a verified human partner. That was fail-closed (no escalation),
// but the contract promised an act the operation then refused, and admitted
// the machine as far as the partner's authority connection first.
//
// The policy chosen for each is the one its handler, store and description
// already enforce: a person's own act. Nothing here widens a grant: no
// handler or store check is relaxed, and every flag moves toward narrower.

import test from "node:test";
import assert from "node:assert/strict";

import { TOOLS, executeRegisteredTool, UNATTENDED_AUTHORITY_VERBS } from "../src/tools.js";
import { registeredOperation } from "../src/mutation-registry.js";

const LITERAL_HUMAN_VERBS = Object.freeze({
  // Records Joe's answer AS Joe (work-request-intake.js human check).
  "answer-work-request-for-joe": { authorityOnly: true },
  // Tour human receipts: the verifier / handler requires a verified human.
  "append-tour-entrance-verification-receipt": { authorityOnly: true },
  "record-tour-map-promotion-receipt": { authorityOnly: true },
  "record-tour-pdf-human-review": { authorityOnly: true },
  // F01 store: humanOnly operations require principal.human.
  "register-record-source-authority-policy": { authorityOnly: true },
  "record-artifact-preservation-hold": { authorityOnly: true },
  // J102 store: humanOnly requires a human; authorityOnly requires verified_partner.
  "record-lifecycle-correction": { authorityOnly: true },
  "record-evidence-subject-link": { authorityOnly: true },
  "record-partner-lifecycle-fact": { authorityOnly: true },
});

const id = "10000000-0000-4000-8000-0000000529aa";
const sponsored = (slug, sponsor, extra = {}) => ({ id, slug, human: false, agent: true,
  native_agent_verified: true, sponsoring_human_slug: sponsor, human_slug: sponsor,
  via: slug.endsWith("-local") ? "local-token" : "oauth-agent", ...extra });
const DENIED = [
  ["joe-local, verified-sponsored by Joe", sponsored("joe-local", "joe")],
  ["codex, verified-sponsored by Joe", sponsored("codex", "joe")],
  ["claude, verified-sponsored by Dell", sponsored("claude", "dell")],
  ["the unattended runner", sponsored("joe-local", "joe", { unattended: true, via: "unattended-token" })],
  ["an unsponsored machine", { id, slug: "codex", human: false, agent: true, via: "agent-token" }],
  ["a reviewer", { id, slug: "codex-reviewer", human: false, review: true }],
];

// A database whose first statement proves the gate admitted the caller.
const ADMITTED = "admitted-past-the-registered-gate";
const sentinelDb = () => ({ query: async () => { throw Object.assign(new Error(ADMITTED), { admitted: true }); } });
const refusedBeforeDatabase = () => ({ query: async () => assert.fail("a refused machine reached the database") });

test("no literal-human verb is on the unattended runner's allowlist", () => {
  for (const verb of Object.keys(LITERAL_HUMAN_VERBS))
    assert.equal(UNATTENDED_AUTHORITY_VERBS.includes(verb), false, verb);
});

for (const [verb, { authorityOnly }] of Object.entries(LITERAL_HUMAN_VERBS)) {
  test(`${verb} declares and publishes literal-human admission without changing its connection`, () => {
    const tool = TOOLS[verb];
    assert.equal(tool.humanCallerOnly, true);
    assert.equal(tool.humanOnly, true);
    assert.equal(tool.authorityOnly === true, authorityOnly, "the authority route did not move");
    const row = registeredOperation(verb);
    assert.equal(row.human_only, true);
    assert.equal(row.human_caller_only, true);
    assert.equal(row.authority_only, authorityOnly);
  });

  for (const [label, actor] of DENIED) {
    test(`${verb} refuses ${label} at registered dispatch before database access`, async () => {
      await assert.rejects(executeRegisteredTool(refusedBeforeDatabase(), { ...actor }, verb, {}),
        error => error.payload?.error === "human_only_verb_requires_verified_partner" ||
          error.payload?.error === "unattended_runner_authority_not_admitted");
    });
  }

  for (const slug of ["joe", "dell"]) {
    test(`${verb} admits ${slug}, the verified human partner, past the registered gate`, async () => {
      // Whatever the operation answers next (input validation or the database),
      // the human was not refused for authority.
      await assert.rejects(executeRegisteredTool(sentinelDb(), { id, slug, human: true, via: "oauth-google" }, verb, {}),
        error => !["human_only_verb_requires_verified_partner", "unattended_runner_authority_not_admitted"]
          .includes(error.payload?.error));
    });
  }
}

test("registry parity attests humanCallerOnly, so a dropped flag is a contract mismatch", async () => {
  const { assertRegisteredOperation } = await import("../src/mutation-registry.js");
  const verb = "answer-work-request-for-joe";
  const { humanCallerOnly: _dropped, ...weakened } = TOOLS[verb];
  await assert.rejects(assertRegisteredOperation(verb, weakened, {}),
    error => error.error === "mutation_contract_mismatch");
  await assertRegisteredOperation(verb, TOOLS[verb], {});
});
