// THE AUTHENTICATED PROBE, RUN UNDER THE PRODUCTION CONDITION (amendment 9).
//
// Not a suite: a one-shot probe that answers one question out loud — does the
// Gate Zero write verb record an outcome on a tree with NO repository anywhere
// above it, the way a deployed Worker runs? It copies a staged candidate tree
// OUT of the repository into a scratch directory whose ancestors hold no `.git`
// at all, asserts that, and then dispatches the registered verb through the real
// review door against a recording client.
//
//   node mcp-server/test/gate-zero-no-git-probe.mjs <scratch dir>
import assert from "node:assert/strict";
import { cpSync, existsSync, mkdtempSync, symlinkSync } from "node:fs";
import { join, dirname, parse } from "node:path";
import { pathToFileURL } from "node:url";

import {
  cleanupStagedTrees, inServedReview, moduleOfTree, stageTree, withStamps,
} from "./gate-zero-candidate-tree.testhelper.mjs";

const VERB = "record-gate-zero-read-only-outcome";
const scratch = process.argv[2];
assert.ok(scratch, "pass a scratch directory outside the repository");

const { base, stamps } = stageTree({});
const probeBase = mkdtempSync(join(scratch, "gate-zero-no-git-probe-"));
cpSync(base, probeBase, { recursive: true });
cleanupStagedTrees();

// THE DEPENDENCY DIRECTORY IS LINKED, NOT COPIED, and it is the one thing here
// that points back at the checkout. A Worker bundle carries its dependencies
// too; what the production condition is about is the absence of a REPOSITORY,
// which the assertion below checks over the probe's own ancestors.
symlinkSync(new URL("../node_modules", import.meta.url).pathname,
  join(probeBase, "mcp-server", "node_modules"));

// NO REPOSITORY ANYWHERE ABOVE IT, asserted rather than assumed — every
// ancestor up to the filesystem root, which is the difference between this
// probe and a staged tree living under the repo's own node_modules.
for (let at = probeBase; at !== parse(at).root; at = dirname(at))
  assert.equal(existsSync(join(at, ".git")), false, `${at} holds a .git`);
assert.equal(existsSync(join(probeBase, ".git")), false);

const target = join(probeBase, "mcp-server", "src");
const tools = await moduleOfTree(target, "tools.js");
// THE RECORD LAYER IS STOOD IN FOR, DELIBERATELY AND ONLY HERE. What this probe
// asks is whether the PRODUCER answers with no repository present; that the
// DATABASE admits only the seat connection, and agrees about both digests, is
// proved against a real PostgreSQL in the migration class by
// gate-zero-outcome-role-boundary.test.mjs and the race proof. So the stand-in
// answers the two statements the handler issues, computing the digests with the
// probe tree's OWN store module rather than with a constant.
const store = await moduleOfTree(target, "gate-zero-outcome-store.v5.js");
const ROW_ID = "00000000-0000-4000-8000-000000000000";
const recorded = { receipt: null };
const client = {
  query: async (sql, params = []) => {
    if (sql.includes("gate_zero_record_read_only_outcome")) {
      recorded.receipt = JSON.parse(params[1]);
      return { rows: [{ id: ROW_ID }] };
    }
    if (sql.includes("from ops.gate_zero_read_only_outcome"))
      return { rows: [{ id: ROW_ID, receipt: recorded.receipt,
        outcome_digest: store.gateZeroOutcomeDigest(recorded.receipt),
        candidate_scoped_digest: store.gateZeroOutcomeCandidateDigest(recorded.receipt),
        candidate_digest: recorded.receipt.candidate_digest,
        status: recorded.receipt.status,
        observed_at: recorded.receipt.observed_at,
        recorded_at: recorded.receipt.observed_at,
        producing_seat_ref: recorded.receipt.producer_identity.actor_id }] };
    return { rows: [] };
  },
  seatConnection: async run => run({ query: (sql, params = []) => client.query(sql, params) }),
};

const served = await withStamps(stamps, () => inServedReview(target, {},
  actor => tools.executeRegisteredTool(client, actor, VERB,
    { idempotency_key: "3d2f8a16-5c47-4b90-a1e2-7f6b0c84d9e5" })));

assert.equal(served.served, true, "the recorded review bearer was not served");
const receipt = recorded.receipt;
assert.ok(receipt, "the verb reached no writer");
process.stdout.write(
  `probe root            ${probeBase}\n` +
  `.git anywhere above   none (asserted to the filesystem root)\n` +
  `served                ${served.served}\n` +
  `verb result           ok=${served.answered.ok} status=${served.answered.status}\n` +
  `receipt status        ${receipt.status}\n` +
  `candidate_digest      ${receipt.candidate_digest}\n` +
  `subject_digest        ${receipt.subject_digest}\n` +
  `fixture_set_digest    ${receipt.fixture_set_digest}\n` +
  `build stamps read     ${Object.keys(stamps).join(", ")}\n` +
  `producer_identity     ${JSON.stringify(receipt.producer_identity)}\n` +
  `subject_maker         ${JSON.stringify(receipt.subject_maker_identity)}\n`);
