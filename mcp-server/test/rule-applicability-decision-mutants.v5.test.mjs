// V5-F05 planted-bug mutants for the coverage receipt's `decision`.
//
// The hazard: a receipt whose consequential write is BLOCKED used to read
// `decision: "allow"`. The one admission consumer reads the write gate and was
// safe; a future consumer testing `decision === "allow"`, the idiom used
// elsewhere in this codebase, would have read a blocked receipt as permission.
//
// Each mutant is KILLED the honest way, as in rule-context-runtime-mutants:
// the same behavioural expectation is run against the real module, where it
// must pass, and against the mutated module, where it must fail. Source
// anchors are exact and unique, so a refactor that turns a mutant into a no-op
// fails here instead of reporting a meaningless green run.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import * as real from "../src/rule-applicability.v5.js";

const SOURCE = new URL("../src/rule-applicability.v5.js", import.meta.url);
const SOURCE_DIR = fileURLToPath(new URL("../src/", import.meta.url));
const WORK = mkdtempSync(join(tmpdir(), "v5-f05-decision-mutants-"));

function mutate(id, anchor, replacement) {
  const source = readFileSync(SOURCE, "utf8");
  assert.equal(source.split(anchor).length - 1, 1,
    `mutant ${id}: anchor must occur exactly once`);
  const path = join(WORK, `${id}-${Date.now()}.mjs`);
  const relinked = source.replace(anchor, replacement).replace(
    /from "\.\/([^"]+)"/g,
    (_, file) => `from "${pathToFileURL(join(SOURCE_DIR, file)).href}"`,
  );
  writeFileSync(path, relinked);
  return import(`${pathToFileURL(path).href}?mutant=${id}-${Date.now()}`);
}

const NOW = "2026-09-26T07:00:00Z";
const provenance = (id, ch) => ({
  source_record_id: `rule:${id}`, source_version: 1,
  source_content_digest: `sha256:${ch.repeat(64)}`, retrieved_at: "2026-09-26T06:00:00Z",
});
const rule = (overrides = {}) => ({
  rule_id: "worktree", version: 1, rule_class: "workflow", scope: "shared", owner: "joe",
  mandatory: true, trigger: { action: ["repo.commit"] },
  control_effect: { control_key: "isolation", effect: "require" },
  binding_text: "Use an isolated worktree before changing tracked source.",
  tests: ["check:isolation"], retirement: { behavior: "permanent_until_superseded" },
  provenance: provenance("worktree", "a"), ...overrides,
});
// A forbid on the same control under a different trigger: the compiler cannot
// see the overlap, the facts can, so the conflict surfaces at derive time.
const conflicting = rule({
  rule_id: "no-isolation", trigger: { risk_tier: ["consequential"] },
  control_effect: { control_key: "isolation", effect: "forbid" },
  binding_text: "Do not isolate during the freeze.", tests: ["check:no-isolation"],
  provenance: provenance("no-isolation", "b"),
});
const policy = (rules, completeness = "complete_authoritative_universe") => ({
  schema_version: real.V5_F05_UNIVERSE_SCHEMA_VERSION, universe_version: 1,
  tenant: ORGANIZATION_TENANT_ID, completeness,
  declared_actions: ["repo.commit"], declared_resource_classes: ["repository"], rules,
});
const FACTS = {
  action: "repo.commit", actor_class: "sponsored_agent", audience: "internal",
  environment: "isolated_worktree", lifecycle_transition: "create",
  resource_class: "repository", risk_tier: "consequential",
};

function derive(module, rules, { completeness, facts = FACTS } = {}) {
  return module.deriveRuleApplicability({
    tenant: ORGANIZATION_TENANT_ID,
    universe: module.compileRuleUniverse(policy(rules, completeness)),
    facts, now: NOW,
  });
}

const EXPECTATIONS = {
  // Main suite: "`decision` agrees with the write gate on every receipt".
  blockedReceiptsNeverReadAllow(module) {
    const { action, ...noAction } = FACTS;
    const receipts = [
      derive(module, [rule()]),
      derive(module, [rule()], { facts: noAction }),
      derive(module, [rule()], { completeness: "partial_unknown_coverage" }),
      derive(module, [rule(), conflicting]),
    ];
    assert.deepEqual(receipts.map(r => r.decision), ["allow", "read_only", "read_only", "refuse"]);
    for (const receipt of receipts) {
      assert.equal(receipt.decision === "allow", receipt.consequential_action_permitted === true);
    }
  },
  // Main suite: "a re-digested receipt whose `decision` contradicts its write gate is refused".
  forgedAllowOnBlockedRefused(module) {
    const { action, ...noAction } = FACTS;
    const blocked = derive(module, [rule()], { facts: noAction });
    assert.equal(blocked.consequential_action_permitted, false);
    const { receipt_digest, effects, ...rest } = blocked;
    const forgedBody = { ...rest, decision: "allow" };
    const forged = { ...forgedBody, receipt_digest: digest(forgedBody), effects };
    assert.throws(() => module.verifyCoverageReceipt(forged),
      error => error?.code === "coverage_receipt_decision_inconsistent");
  },
};

async function assertKilled(mutant, expectation) {
  await EXPECTATIONS[expectation](real);
  await assert.rejects(async () => EXPECTATIONS[expectation](mutant),
    `expectation ${expectation} must fail on the mutant, or the mutant survives`);
}

test("D1 killed: the old two-valued decision reads allow on a blocked receipt", async () => {
  const mutant = await mutate("D1-two-valued-decision",
    "    decision: coverageDecision({ consequential_action_permitted, read_only_exploration_permitted }),",
    "    decision: refused ? \"refuse\" : \"allow\",");
  await assertKilled(mutant, "blockedReceiptsNeverReadAllow");
});

test("D2 killed: the decision rule maps read-only exploration to allow", async () => {
  const mutant = await mutate("D2-read-only-reads-allow",
    "  if (read_only_exploration_permitted === true) return \"read_only\";",
    "  if (read_only_exploration_permitted === true) return \"allow\";");
  await assertKilled(mutant, "blockedReceiptsNeverReadAllow");
});

test("D3 killed: the verifier stops checking decision against the gates", async () => {
  const mutant = await mutate("D3-verifier-skips-decision",
    "  if (receipt.decision !== implied) {",
    "  if (false) {");
  await assertKilled(mutant, "forgedAllowOnBlockedRefused");
});
