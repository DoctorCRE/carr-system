import {
  readRegistryArtifact
} from '../../ops/registry-history.mjs';
import assert from "node:assert/strict";
import {
  execFileSync
} from "node:child_process";
import fs from "node:fs";
import {
  tmpdir
} from "node:os";
import path from "node:path";
import test from "node:test";
import {
  fileURLToPath
} from "node:url";

import {
  CURRENT_REGISTRY_VERSION,
  assertCurrentSourceInventoryMatchesFixture,
  boundInventoryRows,
  MCP_TOOL_BOUND_FIELDS,
  sourceInventoryFixtureDigest,
  assertGeneratedFrontierMatchesCommitted,
  assertLegacyLaunchdSource,
  canonicalize,
  DB_CATALOG_BASELINE,
  discoverScriptEntrypoints,
  frozenInventory,
  fullInventory,
  isScriptEntrypoint,
  jobDefinitionInventory,
  JOB_DEFINITION_BASELINE,
  mcpInventory,
  parsePlistXml,
  parseGitIndexEntries,
  REGISTRY_V25_VERSION,
  REGISTRY_V26_VERSION,
  REGISTRY_V28_VERSION,
  REGISTRY_V29_VERSION,
  REGISTRY_V30_VERSION,
  renderV5ScheduledJobAdmissionForwardRegistrySql,
  renderGateZeroOutcomeAdmissionForwardRegistrySql,
  assertV5ScheduledJobAdmissionV25TrustRoot,
  assertGateZeroOutcomeAdmissionV26TrustRoot,
  renderFoundationAssuranceForwardRegistrySql,
  renderDealFieldProvenanceForwardRegistrySql,
  assertDealFieldProvenanceV28TrustRoot,
  renderProgramControllerForwardRegistrySql,
  assertProgramControllerV29TrustRoot,
  assertProducerTrioV30TrustRoot,
  renderProducerTrioForwardRegistrySql,
  REGISTRY_V31_VERSION,
  assertDocConversationWriteDoorsV31TrustRoot,
  renderDocConversationWriteDoorsForwardRegistrySql,
  REGISTRY_V32_VERSION,
  REGISTRY_V33_VERSION,
  REGISTRY_V34_VERSION,
  REGISTRY_V35_VERSION,
  assertDocConversationListV32TrustRoot,
  renderDocConversationListForwardRegistrySql,
  assertNotificationPreferencesV33TrustRoot,
  renderNotificationPreferencesForwardRegistrySql,
  assertSessionIdentityV34TrustRoot,
  renderSessionIdentityForwardRegistrySql,
  assertDispatchSpineV35TrustRoot,
  renderDispatchSpineForwardRegistrySql,
  renderReadyPlanAmendmentForwardRegistrySqlClosed,
  renderReadyPlanAmendmentMeasurementProbeSqlClosed,
  assertFoundationAssuranceV27TrustRoot,
  gateZeroOutcomeAdmissionProvenance,
  v5ScheduledJobAdmissionProvenance,
  isDefinitionOnlyLaunchd,
  replaceExactlyOnce,
  renderGeneratedFrontier,
  sha256,
  SIEP12_DB_CATALOG_BASELINE,
  validateLaunchdAuthorityCatalogs,
  workflowDefinitionInventory
} from "../../ops/scac-mutation-inventory.mjs";
import {
  assertClosedTopLevel,
  assertRegisteredOperation,
  MutationRegistryRefusal,
  registeredOperation,
  SCAC_MUTATION_REGISTRY_VERSION
} from "../src/mutation-registry.js";
import {
  TOOLS
} from "../src/tools.js";

const migration = readRegistryArtifact(
  new URL("../../migrations/0454_siep11_mutation_registry.sql", import.meta.url), "utf8");
const generated = readRegistryArtifact(
  new URL("../src/scac-mutation-registry.generated.js", import.meta.url), "utf8");
const successorMigration = readRegistryArtifact(
  new URL("../../migrations/0455_siep12_policy_epoch.sql", import.meta.url), "utf8");
const v26Migration = readRegistryArtifact(
  new URL("../../migrations/0503_gate_zero_outcome_and_scac_successor.sql",
    import.meta.url), "utf8");
const v29DomainMigration = readRegistryArtifact(
  new URL("../../migrations/0517_program_controller_seams.sql", import.meta.url), "utf8");
const siep18MonitorMigration = readRegistryArtifact(
  new URL("../../migrations/0467_siep18_atomic_db_monitor_grants.sql", import.meta.url), "utf8");
const directRegistryRedefinitions = [
  "0460_siep15_device_enrollment.sql",
  "0465_siep17_token_challenge_authority.sql",
  "0467_siep18_atomic_db_monitor_grants.sql",
  "0470_source_merge_authority_projection.sql",
].map(name => [name, readRegistryArtifact(new URL(`../../migrations/${name}`, import.meta.url), "utf8")]);

test("successor generation refuses absent or ambiguous predecessor markers", () => {
  assert.equal(replaceExactlyOnce("before marker after", "marker", "successor", "unit"),
    "before successor after");
  assert.throws(() => replaceExactlyOnce("before after", "marker", "successor", "unit"),
    /unit marker count must be exactly one/);
  assert.throws(() => replaceExactlyOnce("marker and marker", "marker", "successor", "unit"),
    /unit marker count must be exactly one/);
});

test("reviewed MCP inventory is an exact immutable projection of the assembled registry", () => {
  const rows = mcpInventory(TOOLS);
  // B09 adds one read to the role-store tool surface; B11 Meeting Mode adds
  // seven writes and one read.
  // Tour property registration (0565) adds one authority-only write.
  // Answering Joe (0575) adds one human-only, authority-only write.
  // DoctorCRE V5-UX-C02/C06 (0579) adds one read and one write:
  // read-resource-dashboard and record-resource-observation.
  // The server-side Jev call log (0587) adds one write and two reads:
  // ask-jev, read-jev-call-receipts and read-jev-call-receipt-integrity.
  // DoctorCRE V5-R02 (0602) adds eight writes and two reads: open/advance/
  // cancel/retire-workflow-cutover-plan, register-slice-checkable-done,
  // mark-slice-completion, record-workflow-caller, mark-slice-progress,
  // workflow-cutover-board, read-slice-completion.
  // DoctorCRE V5-M01's Journey 1 clock door (0614) adds one write and one
  // read: advance-journey-one-clock and read-journey-one-clock.
  // V5-A05 delivery cadence (0617) adds three: cadence-status (a read on the
  // writer connection), record-cadence-receipt and raise-delivery-cadence-alert.
  // DoctorCRE V5-S01's global boundaries door (0625) adds one read:
  // read-global-boundaries.
  // DoctorCRE V5-F01 (0626) adds eight writes and one read: the
  // record-source-authority door's nine verbs.
  // The slice done-record (0628) adds nine: seven writes
  // (register-slice-criteria-from-catalog, bind/rebind-slice-criterion-evidence,
  // record-release-slice-members, propose-slice-completion,
  // confirm-slice-completions, set-slice-mark-hold) and two reads
  // (list-shipped-releases, pending-slice-completion-proposals).
  // DoctorCRE V5-J103's governed correspondence store (0700) adds two humanOnly
  // writes and two reads: record- and revoke-correspondence-adapter-consent,
  // correspondence-readiness and read-correspondence-thread. No send verb.
  // DoctorCRE V5-J102 (0704) adds twenty writes and one read: the CRE
  // lifecycle door's twenty-one verbs.
  // amend-closed-loop (defect a2c04ffa, loop c7265238) adds one write.
  // V5-D01 action-class-successor-registry (0708) adds one write
  // (register-action-class-successor) and two reads
  // (read-action-class-successors, read-action-class-gate).
  // V5-A01 assurance-health evidence store (0717) adds one write
  // (record-assurance-health-evidence) and one read (read-assurance-health).
  // DoctorCRE V5-A03 (0719) adds five append-only writes and one authoritative
  // read for the independent complete-set review cycle.
  // V5-A02 (0721) adds one read and one Joe-authority-only write.
  // DoctorCRE V5-F05 (0724) adds one actor-scoped read (read-action-context)
  // and one authority-only typed contract binder (bind-rule-context-contract).
  // V5-RW02 (0726) adds three durable evidence writers and one per-action
  // evidence read; none of them carries a provider effect.
  // V5-RW02's safe-stop run store (0733) adds the autonomy-counter read, the
  // run-outcome writer and the human-only consent-revocation writer.
  // Incident triage (0737) adds one bounded write.
  assert.deepEqual(boundInventoryRows(rows),
    boundInventoryRows(frozenInventory(CURRENT_REGISTRY_VERSION)
      .filter(row => row.ingress_kind === "mcp_tool")));
  assert.deepEqual(rows.map(row => row.operation), Object.keys(TOOLS).sort());
  assert.equal(Object.isFrozen(TOOLS), true);
  assert.equal(Object.isFrozen(TOOLS["add-loop"]), true);
  assert.equal(Object.isFrozen(TOOLS["add-loop"].inputSchema), true);
  assert.throws(() => { TOOLS["add-loop"].handler = async () => ({ ok: true }); }, TypeError);
  assert.equal(new Set(rows.map(row => row.ingress_key)).size, rows.length);
  assert.equal(new Set(rows.map(row => row.schema_digest)).has(undefined), false);
  assert.equal(rows.find(row => row.operation === "append-tour-rights-receipt").source_locator,
    "mcp-server/src/tour-rights-projection.js");
  assert.equal(rows.find(row => row.operation === "record-tour-map-promotion-receipt").source_locator,
    "mcp-server/src/tour-map-promotion.js");
  assert.equal(rows.find(row => row.operation === "request-tour-pdf-render").source_locator,
    "mcp-server/src/tour-artifacts.js");
  const governanceQueue = rows.find(row => row.operation === "governance-queue");
  assert.ok(governanceQueue);
  assert.equal(governanceQueue.write, false);
  assert.equal(governanceQueue.human_only, false);
  assert.equal(governanceQueue.authority_only, false);
  assert.equal(governanceQueue.effect_class, "audit_side_effect");
  assert.equal(governanceQueue.classification_authorizing, false);
});















// A test-only variant of ops/scac-mutation-inventory.mjs has to keep resolving
// its sibling ops modules and its ops/config fixtures, so the isolated tree is
// a symlink shadow of ops/ rather than a bare directory. Writing the variants
// outside ops/ is the point: a stray .mjs beside the real one is exactly the
// fixture leak the v3 correction was written to avoid.
function linkOpsTree(repoRoot, isolatedRoot) {
  const opsRoot = path.join(isolatedRoot, "ops");
  fs.mkdirSync(opsRoot);
  for (const entry of fs.readdirSync(path.join(repoRoot, "ops"), { withFileTypes: true }))
    fs.symlinkSync(path.join(repoRoot, "ops", entry.name), path.join(opsRoot, entry.name),
      entry.isDirectory() ? "dir" : "file");
  return opsRoot;
}












// The v30 successor is the ONE seal the three accepted producer plans share.
// Its three domain migrations ride the same reviewed atomic group, so the
// generated preflight has to name all three ledger receipts rather than one.

// The v31 successor seals the WR-000114 Doc conversation write doors. Its ONE
// domain migration rides the same reviewed atomic group, and -- unlike v30 --
// mcp-server/src/tools.js is not edited at all, because the three verbs join a
// family file tools.js already registers.

// THE DIFFERENCE FROM EVERY PREDECESSOR, asserted rather than assumed: the
// three verbs join mcp-server/src/doc-conversation.js, which tools.js ALREADY
// registers, so not one mcp-tool row sourced from tools.js re-digests. If a
// derived v31 overlay ever contains those rows, something edited tools.js and
// this case is where that shows up.
test("the Doc conversation write doors add exactly three ingresses and move no tools.js row", () => {
  const v30Rows = frozenInventory(REGISTRY_V30_VERSION);
  const v31Rows = frozenInventory(REGISTRY_V31_VERSION);
  const v30ByKey = new Map(v30Rows.map(row => [row.ingress_key, row]));
  const v31Keys = new Set(v31Rows.map(row => row.ingress_key));
  assert.deepEqual([...v31Keys].filter(key => !v30ByKey.has(key)).sort(), [
    "mcp-tool:create-doc-conversation",
    "mcp-tool:rename-doc-conversation",
    "mcp-tool:share-doc-conversation",
  ]);
  assert.deepEqual([...v30ByKey.keys()].filter(key => !v31Keys.has(key)), [],
    "a seal may admit an ingress; it may never drop one");
  // NOT ONE row whose source_locator is tools.js moved.
  const movedFromToolsJs = v31Rows.filter(row =>
    row.source_locator === "mcp-server/src/tools.js" &&
    JSON.stringify(v30ByKey.get(row.ingress_key)) !== JSON.stringify(row));
  assert.deepEqual(movedFromToolsJs, [],
    "tools.js is not edited by this build, so no row sourced from it may re-digest");
  // The two siblings that DO move share doc-conversation.js, whose bytes moved.
  const movedFromFamily = v31Rows.filter(row =>
    row.source_locator === "mcp-server/src/doc-conversation.js" &&
    v30ByKey.has(row.ingress_key) &&
    JSON.stringify(v30ByKey.get(row.ingress_key)) !== JSON.stringify(row))
    .map(row => row.ingress_key).sort();
  assert.deepEqual(movedFromFamily,
    ["mcp-tool:add-doc-conversation-turn", "mcp-tool:read-doc-conversation"]);
  assert.equal(v31Rows.length, 863);
});

// The v32 successor seals the WR-000115 Doc conversation LIST door. Its ONE
// domain migration rides the same reviewed atomic group, and -- as at v31 --
// mcp-server/src/tools.js is not edited at all, because the verb joins a family
// file tools.js already registers. Unlike v31 it owes NO completion-evidence
// gate entry: `list` is not a write prefix and the verb writes nothing.




// THE SAME SHAPE v34's assertion took, extended by the two rows this build may
// add. tools.js is edited again, so every row sourced from it re-digests its
// SOURCE together and not one may move its SCHEMA.
test("the dispatch spine adds exactly two write ingresses and moves no tools.js SCHEMA digest", () => {
  const v34Rows = frozenInventory(REGISTRY_V34_VERSION);
  const v35Rows = frozenInventory(REGISTRY_V35_VERSION);
  const v34ByKey = new Map(v34Rows.map(row => [row.ingress_key, row]));
  const v35Keys = new Set(v35Rows.map(row => row.ingress_key));
  assert.deepEqual([...v35Keys].filter(key => !v34ByKey.has(key)).sort(), [
    "mcp-tool:acknowledge-dispatch",
    "mcp-tool:record-dispatch-link",
  ]);
  assert.deepEqual([...v34ByKey.keys()].filter(key => !v35Keys.has(key)), [],
    "a seal may admit an ingress; it may never drop one");
  const toolsJsRows = v35Rows.filter(row => row.source_locator === "mcp-server/src/tools.js");
  assert.equal(toolsJsRows.length, 100,
    "registering a NEW FAMILY adds no inline verb to tools.js itself");
  const movedSchemaFromToolsJs = toolsJsRows.filter(row =>
    v34ByKey.get(row.ingress_key).schema_digest !== row.schema_digest).map(row => row.ingress_key);
  assert.deepEqual(movedSchemaFromToolsJs, [],
    "no tools.js-sourced verb changed its inputSchema, so no schema digest may move");
  const movedSourceFromToolsJs = toolsJsRows.filter(row =>
    v34ByKey.get(row.ingress_key).source_digest !== row.source_digest);
  assert.equal(movedSourceFromToolsJs.length, 100,
    "this build edited the file, so every row sourced from it re-digests together");
  // THE TWO ROWS THAT MAY BE NEW, NAMED, and sourced from the new family file
  // and from nowhere else.
  const newFamilyRows = v35Rows.filter(row =>
    row.source_locator === "mcp-server/src/dispatch-spine.js")
    .map(row => row.ingress_key).sort();
  assert.deepEqual(newFamilyRows, [
    "mcp-tool:acknowledge-dispatch",
    "mcp-tool:record-dispatch-link",
  ]);
  // BOTH ARE WRITES -- the OPPOSITE of the v34 pair, and the reason the
  // completion-evidence gate is edited by this build and was not by that one.
  assert.deepEqual(v35Rows.filter(row => newFamilyRows.includes(row.ingress_key))
    .map(row => row.write), [true, true]);
  // THE WR-000117 PAIR'S TWO ROWS re-digest their SOURCE (the read shaper now
  // carries the per-dispatch fields) and NOT their schema: read-dispatch-history
  // keeps its inputSchema, which is what AC-DS-HISTORY requires.
  for (const key of ["mcp-tool:read-dispatch-history", "mcp-tool:read-session-identity"]) {
    const before = v34ByKey.get(key);
    const after = v35Rows.find(row => row.ingress_key === key);
    assert.equal(after.schema_digest, before.schema_digest, key);
    assert.notEqual(after.source_digest, before.source_digest, key);
  }
  // THE HOOK ROW DID MOVE, and exactly one did: `acknowledge` is deliberately
  // not a write prefix, so acknowledge-dispatch is owed an EXACT entry.
  const movedFromGate = v35Rows.filter(row =>
    row.source_locator === "hooks/completion-evidence-gate.py" &&
    JSON.stringify(v34ByKey.get(row.ingress_key)) !== JSON.stringify(row))
    .map(row => row.ingress_key);
  assert.deepEqual(movedFromGate, ["script-entrypoint:hooks/completion-evidence-gate.py"],
    "the gate is edited by this build, so its own row re-digests and nothing else does");
  assert.equal(v35Rows.length, 870);
});

// THE INVERSE of the assertion v32 and v33 each carried. Those two joined an
// ALREADY-registered family file, so NOT ONE mcp-tool row sourced from tools.js
// could re-digest. This pair adds a NEW family file, so tools.js IS edited and
// every row sourced from it re-digests together -- and the two rows that may be
// NEW are named here exactly, so a third one appearing is a failure rather than
// a surprise.
test("the session identity pair adds exactly two ingresses and moves no tools.js SCHEMA digest", () => {
  const v33Rows = frozenInventory(REGISTRY_V33_VERSION);
  const v34Rows = frozenInventory(REGISTRY_V34_VERSION);
  const v33ByKey = new Map(v33Rows.map(row => [row.ingress_key, row]));
  const v34Keys = new Set(v34Rows.map(row => row.ingress_key));
  assert.deepEqual([...v34Keys].filter(key => !v33ByKey.has(key)).sort(), [
    "mcp-tool:read-dispatch-history",
    "mcp-tool:read-session-identity",
  ]);
  assert.deepEqual([...v33ByKey.keys()].filter(key => !v34Keys.has(key)), [],
    "a seal may admit an ingress; it may never drop one");
  // TOOLS.JS IS EDITED BY THIS BUILD, which is the structural difference from
  // v31, v32 and v33. Every row sourced from it therefore re-digests its
  // SOURCE, and not one of them may move its SCHEMA: the file gained an import,
  // a source-map entry and a registration call, not a verb. A source_digest
  // that moves is a file edit; a schema_digest that moves is a contract change.
  const toolsJsRows = v34Rows.filter(row => row.source_locator === "mcp-server/src/tools.js");
  assert.equal(toolsJsRows.length, 100,
    "registering a NEW FAMILY adds no inline verb to tools.js itself");
  const movedSchemaFromToolsJs = toolsJsRows.filter(row =>
    v33ByKey.get(row.ingress_key).schema_digest !== row.schema_digest).map(row => row.ingress_key);
  assert.deepEqual(movedSchemaFromToolsJs, [],
    "no tools.js-sourced verb changed its inputSchema, so no schema digest may move");
  const movedSourceFromToolsJs = toolsJsRows.filter(row =>
    v33ByKey.get(row.ingress_key).source_digest !== row.source_digest);
  assert.equal(movedSourceFromToolsJs.length, 100,
    "this build edited the file, so every row sourced from it re-digests together");
  // THE TWO ROWS THAT MAY BE NEW, NAMED. Both are sourced from the new family
  // file and from nowhere else.
  const newFamilyRows = v34Rows.filter(row =>
    row.source_locator === "mcp-server/src/session-identity.js")
    .map(row => row.ingress_key).sort();
  assert.deepEqual(newFamilyRows, [
    "mcp-tool:read-dispatch-history",
    "mcp-tool:read-session-identity",
  ]);
  // BOTH ARE READS, so neither may carry the write flag.
  assert.deepEqual(v34Rows.filter(row => newFamilyRows.includes(row.ingress_key))
    .map(row => row.write), [false, false]);
  // NO HOOK ROW MOVED: `read` is in neither of the completion-evidence gate's
  // two collections, so the pair owes no gate entry and that file is not edited.
  const movedFromGate = v34Rows.filter(row =>
    row.source_locator === "hooks/completion-evidence-gate.py" &&
    JSON.stringify(v33ByKey.get(row.ingress_key)) !== JSON.stringify(row));
  assert.deepEqual(movedFromGate, [],
    "`read` is in neither collection, so the gate owes no edit and may not re-digest");
  assert.equal(v34Rows.length, 868);
});

// THE SAME DIFFERENCE v32 asserted, asserted again rather than assumed: the two
// new verbs join mcp-server/src/notifications.js, which tools.js ALREADY
// registers, so not one mcp-tool row sourced from tools.js re-digests. If a
// derived v33 overlay ever contains those rows, something edited tools.js and
// this case is where that shows up.
test("the notification preference pair adds exactly two ingresses and moves no tools.js schema digest", () => {
  const v32Rows = frozenInventory(REGISTRY_V32_VERSION);
  const v33Rows = frozenInventory(REGISTRY_V33_VERSION);
  const v32ByKey = new Map(v32Rows.map(row => [row.ingress_key, row]));
  const v33Keys = new Set(v33Rows.map(row => row.ingress_key));
  assert.deepEqual([...v33Keys].filter(key => !v32ByKey.has(key)).sort(), [
    "mcp-tool:read-notification-preferences",
    "mcp-tool:set-notification-preference",
  ]);
  assert.deepEqual([...v32ByKey.keys()].filter(key => !v33Keys.has(key)), [],
    "a seal may admit an ingress; it may never drop one");
  // TOOLS.JS IS NOT EDITED BY THIS BUILD, BUT TRUNK EDITED IT. b64b4c5a
  // ("Judge the code as it is written", #1090) added an enum-validation helper
  // to mcp-server/src/tools.js, so every row sourced from that file carries a
  // new source_digest after the merge. That is trunk's byte change, not this
  // build's: what this build must prove is that NO ROW SOURCED FROM tools.js
  // MOVED ITS schema_digest, which is the contract, and that the file gained
  // no verb. A source_digest that moves is a file edit; a schema_digest that
  // moves is a contract change, and only the latter would be this pair's doing.
  const toolsJsRows = v33Rows.filter(row => row.source_locator === "mcp-server/src/tools.js");
  assert.equal(toolsJsRows.length, 100,
    "trunk's tools.js change registered no new verb");
  const movedSchemaFromToolsJs = toolsJsRows.filter(row => {
    const before = v32ByKey.get(row.ingress_key);
    return !before || before.schema_digest !== row.schema_digest;
  }).map(row => row.ingress_key);
  assert.deepEqual(movedSchemaFromToolsJs, [],
    "no tools.js-sourced verb changed its inputSchema, so no schema digest may move");
  const movedSourceFromToolsJs = toolsJsRows.filter(row =>
    v32ByKey.get(row.ingress_key).source_digest !== row.source_digest);
  assert.equal(movedSourceFromToolsJs.length, 100,
    "trunk edited the file, so every row sourced from it re-digests together");
  // The two siblings that DO move share notifications.js, whose bytes moved.
  const movedFromFamily = v33Rows.filter(row =>
    row.source_locator === "mcp-server/src/notifications.js" &&
    v32ByKey.has(row.ingress_key) &&
    JSON.stringify(v32ByKey.get(row.ingress_key)) !== JSON.stringify(row))
    .map(row => row.ingress_key).sort();
  assert.deepEqual(movedFromFamily, [
    "mcp-tool:acknowledge-notification",
    "mcp-tool:notification-feed",
  ]);
  // THE CHEAPEST POSSIBLE PROOF THAT THE FEED CHANGE STAYED OUTPUT-ONLY:
  // notification-feed's source_digest MOVED and its schema_digest did NOT. The
  // pure shaper gained a field per row; the inputSchema did not move a byte.
  const feedBefore = v32ByKey.get("mcp-tool:notification-feed");
  const feedAfter = v33Rows.find(row => row.ingress_key === "mcp-tool:notification-feed");
  assert.notEqual(feedAfter.source_digest, feedBefore.source_digest,
    "the projection changed, so the source digest must move");
  assert.equal(feedAfter.schema_digest, feedBefore.schema_digest,
    "the inputSchema did not change, so the schema digest must NOT move");
  // NO HOOK ROW MOVED EITHER: `set` is already a write prefix, so the write
  // verb owes no completion-evidence gate entry and that file is not edited.
  const movedFromGate = v33Rows.filter(row =>
    row.source_locator === "hooks/completion-evidence-gate.py" &&
    JSON.stringify(v32ByKey.get(row.ingress_key)) !== JSON.stringify(row));
  assert.deepEqual(movedFromGate, [],
    "`set` is already a write prefix, so the gate owes no edit and may not re-digest");
  assert.equal(v33Rows.length, 866);
});

// THE SAME DIFFERENCE v31 asserted, asserted again rather than assumed: the one
// new verb joins mcp-server/src/doc-conversation.js, which tools.js ALREADY
// registers, so not one mcp-tool row sourced from tools.js re-digests. If a
// derived v32 overlay ever contains those rows, something edited tools.js and
// this case is where that shows up.
test("the Doc conversation list adds exactly one ingress and moves no tools.js row", () => {
  const v31Rows = frozenInventory(REGISTRY_V31_VERSION);
  const v32Rows = frozenInventory(REGISTRY_V32_VERSION);
  const v31ByKey = new Map(v31Rows.map(row => [row.ingress_key, row]));
  const v32Keys = new Set(v32Rows.map(row => row.ingress_key));
  assert.deepEqual([...v32Keys].filter(key => !v31ByKey.has(key)).sort(), [
    "mcp-tool:list-doc-conversations",
  ]);
  assert.deepEqual([...v31ByKey.keys()].filter(key => !v32Keys.has(key)), [],
    "a seal may admit an ingress; it may never drop one");
  // NOT ONE row whose source_locator is tools.js moved.
  const movedFromToolsJs = v32Rows.filter(row =>
    row.source_locator === "mcp-server/src/tools.js" &&
    JSON.stringify(v31ByKey.get(row.ingress_key)) !== JSON.stringify(row));
  assert.deepEqual(movedFromToolsJs, [],
    "tools.js is not edited by this build, so no row sourced from it may re-digest");
  // The five siblings that DO move share doc-conversation.js, whose bytes moved.
  const movedFromFamily = v32Rows.filter(row =>
    row.source_locator === "mcp-server/src/doc-conversation.js" &&
    v31ByKey.has(row.ingress_key) &&
    JSON.stringify(v31ByKey.get(row.ingress_key)) !== JSON.stringify(row))
    .map(row => row.ingress_key).sort();
  assert.deepEqual(movedFromFamily, [
    "mcp-tool:add-doc-conversation-turn",
    "mcp-tool:create-doc-conversation",
    "mcp-tool:read-doc-conversation",
    "mcp-tool:rename-doc-conversation",
    "mcp-tool:share-doc-conversation",
  ]);
  // NO HOOK ROW MOVED EITHER: a read verb owes no completion-evidence gate
  // entry, so hooks/completion-evidence-gate.py is not edited by this build.
  const movedFromHooks = v32Rows.filter(row =>
    String(row.source_locator).startsWith("hooks/") &&
    JSON.stringify(v31ByKey.get(row.ingress_key)) !== JSON.stringify(row));
  assert.deepEqual(movedFromHooks, [],
    "a read verb owes no gate entry, so no hooks/ row may re-digest");
  assert.equal(v32Rows.length, 864);
});

// The producer trio registers FOUR new verbs and adds NO new inventoried
// script entrypoint: the two new source modules are libraries tools.js
// imports, and the three new postgres proofs are run BY ops/ci.sh rather than
// being entrypoints of their own. The key SETS are compared, because an equal
// count with a different membership would still be a new ingress.
test("the producer trio adds exactly the four new verb ingresses", () => {
  const v29Keys = new Set(frozenInventory(REGISTRY_V29_VERSION).map(row => row.ingress_key));
  const v30Rows = frozenInventory(REGISTRY_V30_VERSION);
  const v30Keys = new Set(v30Rows.map(row => row.ingress_key));
  assert.deepEqual([...v30Keys].filter(key => !v29Keys.has(key)).sort(), [
    "mcp-tool:acknowledge-notification",
    "mcp-tool:add-doc-conversation-turn",
    "mcp-tool:notification-feed",
    "mcp-tool:read-doc-conversation",
  ]);
  assert.deepEqual([...v29Keys].filter(key => !v30Keys.has(key)), []);
  assert.equal(v30Rows.length, v29Keys.size + 4);
});

// F02-NO-INGRESS. WR-000110 adds two source modules and five test files, and
// NONE of them may become an inventoried ingress: the census module is a
// library the runtime imports, not a script anything runs. The key SETS are
// compared rather than the totals, because an equal count with a different
// membership would still be a new ingress.
test("the WR-000110 program-controller modules add no inventoried ingress", () => {
  const v28Keys = new Set(frozenInventory(REGISTRY_V28_VERSION).map(row => row.ingress_key));
  const v29Rows = frozenInventory(REGISTRY_V29_VERSION);
  const v29Keys = new Set(v29Rows.map(row => row.ingress_key));
  assert.deepEqual([...v29Keys].filter(key => !v28Keys.has(key)), []);
  assert.deepEqual([...v28Keys].filter(key => !v29Keys.has(key)), []);
  assert.equal(v29Rows.length, v28Keys.size);
  for (const fragment of ["program-controller-census", "program-controller-release-state-holder"])
    assert.equal([...v29Keys].some(key => key.includes(fragment)), false, fragment);
  // The domain migration is the OTHER half of the atomic group and carries the
  // only new SECURITY DEFINER surface this work request installs. Its two
  // explicit grantees are what moved the secdef_execute receipt by two.
  assert.match(v29DomainMigration, /grant execute on function ops\.record_program_controller_fact/);
});







test("the v26 admission provenance is measured from the row sets and binds every prose layer", () => {
  // THE DEFECT THIS EXISTS FOR (PR #1006 review 2). The frontier moved from
  // four admitted ingresses to five; the row sets, the migration and the tests
  // followed; two prose layers did not. The fixture's reason still said
  // "835 to 839", "four new rows" and "131 reviewed ingresses" against an
  // 840-row overlay carrying 132 reviewed rows, and the generator's
  // catalog-baseline comment still said one script where the migration said
  // two. Nothing was red because nothing compared a sentence to the rows.
  //
  // So this test recomputes the delta from the two frozen row sets ITSELF --
  // deliberately not by reading the provenance object's own numbers back to
  // it -- and then requires every layer to say what the recomputation says.
  // POINTED AT THE LIVE FRONTIER, which is v26. The invariant is about the
  // CURRENT admission's prose binding the current rows; v25's own numbers are
  // sealed history now and are asserted by the v25 migration test above.
  const before = frozenInventory(REGISTRY_V25_VERSION);
  const after = frozenInventory(REGISTRY_V26_VERSION);
  const beforeKeys = new Set(before.map(row => row.ingress_key));
  const afterKeys = new Set(after.map(row => row.ingress_key));
  const admitted = after.filter(row => !beforeKeys.has(row.ingress_key));
  const removed = before.filter(row => !afterKeys.has(row.ingress_key));
  const words = ["zero", "one", "two", "three", "four", "five", "six", "seven"];
  // v26 admits a VERB rather than agents and scripts, so the composition is
  // recomputed by kind here rather than assuming last generation's two buckets.
  const launchAgents = admitted.filter(row => row.ingress_kind === "workflow_entrypoint" &&
    row.source_locator.startsWith("ops/launchd/")).length;
  const scripts = admitted.filter(row => row.ingress_kind === "script_entrypoint").length;
  const mcpTools = admitted.filter(row => row.ingress_kind === "mcp_tool").length;
  assert.equal(launchAgents + scripts + mcpTools, admitted.length);

  const provenance = gateZeroOutcomeAdmissionProvenance();
  assert.equal(provenance.previous_frontier_count, before.length);
  assert.equal(provenance.frontier_count, after.length);
  assert.equal(provenance.admitted_count, admitted.length);
  assert.deepEqual([...provenance.admitted_ingress_keys],
    admitted.map(row => row.ingress_key).sort((left, right) => left.localeCompare(right)));
  assert.deepEqual([...provenance.removed_ingress_keys], removed.map(row => row.ingress_key));
  // DERIVED FROM THE COMPOSITION, not from last generation's sentence shape.
  // The buckets are recomputed above and joined here in the module's own noun
  // order — LaunchAgents, then script entrypoints, then MCP tools — so the
  // sentence is checked against the ROWS rather than against a phrase anybody
  // typed. It reads "one script entrypoint and one MCP tool" at this head, and
  // it will read whatever the next composition is without this line moving.
  const phrase = [
    launchAgents > 0 && `${words[launchAgents]} LaunchAgent ` +
      `${launchAgents === 1 ? "definition" : "definitions"}`,
    scripts > 0 && `${words[scripts]} script ${scripts === 1 ? "entrypoint" : "entrypoints"}`,
    mcpTools > 0 && `${words[mcpTools]} MCP ${mcpTools === 1 ? "tool" : "tools"}`,
  ].filter(Boolean);
  const joined = phrase.length === 1 ? phrase[0]
    : `${phrase.slice(0, -1).join(", ")} and ${phrase.at(-1)}`;
  assert.equal(provenance.admitted_description, joined);
  assert.equal(launchAgents, 0, "v26 admits no agent");

  // The fixture is read as bytes here, not through the module that also
  // renders the paragraph: the file on disk is the artifact a reviewer reads.
  const fixture = JSON.parse(readRegistryArtifact(
    new URL("../../ops/config/scac-registry-source-inventory-fixtures.v1.json",
      import.meta.url), "utf8"));
  const review = fixture.historical_artifact_replay_review;
  const patch = fixture.patches.find(entry => entry.version === "v26");
  assert.equal(provenance.reviewed_ingress_count, review.upsert.length);

  // THE REVIEWED NUMBER IS THE EFFECTIVE OVERLAY'S NUMBER, derived twice here.
  // PR #1006 review 4 found the successor report claiming 132 reviewed
  // ingresses in one paragraph and 138 in another, because both were typed.
  // The count is only honest if every overlay row actually supersedes the
  // frozen row it names -- a redundant row identical to the frontier would
  // raise the number without moving a single digest -- so the effective
  // overlay is recomputed against the frozen rows and asserted to be the whole
  // overlay.
  const frozenByKey = new Map(after.map(row => [row.ingress_key, row]));
  const canonicalJson = value => JSON.stringify(canonicalize(value));
  const effectiveOverlay = review.upsert.filter(row =>
    canonicalJson(row) !== canonicalJson(frozenByKey.get(row.ingress_key)));
  assert.equal(effectiveOverlay.length, review.upsert.length);
  assert.equal(provenance.reviewed_ingress_count, effectiveOverlay.length);

  assert.equal(provenance.patch_redigested_count,
    patch.upsert.filter(row => beforeKeys.has(row.ingress_key)).length);
  assert.equal(patch.upsert.length, admitted.length + provenance.patch_redigested_count);

  // THE COUNTS IN THE REASON ARE THE OVERLAY'S REAL COUNTS. Every number in
  // the closing paragraph is asserted against the recomputation above, and the
  // paragraph is asserted to be the end of the reason, so a count typed into
  // the fixture by hand cannot survive either check.
  const paragraph = provenance.review_reason_paragraph;
  assert.ok(review.reason.endsWith(paragraph), review.reason.slice(-600));
  assert.ok(paragraph.includes(`grows from ${before.length} to ${after.length} rows`), paragraph);
  assert.ok(paragraph.includes(
    `admitting ${words[admitted.length]} new ${admitted.length === 1 ? "ingress" : "ingresses"}`), paragraph);
  assert.ok(paragraph.includes(provenance.admitted_description), paragraph);
  assert.ok(paragraph.includes(`plus ${words[provenance.patch_redigested_count] ??
    provenance.patch_redigested_count} already-known rows`), paragraph);
  assert.ok(paragraph.includes(`${review.upsert.length} reviewed ingresses`), paragraph);
  assert.ok(paragraph.includes("and removing none"), paragraph);
  for (const key of provenance.admitted_ingress_keys) assert.ok(paragraph.includes(key), key);

  // THE BRANCH-MOVED LIST IS DERIVED, NOT TYPED (PR #1006 review 4, finding 3).
  // A row this branch re-digested after the v25 patch was cut shows up as an
  // overlay row whose bytes differ from the patch row of the same key -- which
  // is how correction 3's canary-gate re-digest went unnamed for a whole round
  // while a hand-written sentence listed three other files. Recomputed from the
  // fixture bytes and asserted against both the provenance object and the
  // paragraph a reviewer reads.
  const patchByKey = new Map(patch.upsert.map(row => [row.ingress_key, row]));
  const resealed = review.upsert
    .filter(row => patchByKey.has(row.ingress_key) &&
      canonicalJson(patchByKey.get(row.ingress_key)) !== canonicalJson(row))
    .map(row => row.ingress_key)
    .sort((left, right) => left.localeCompare(right));
  assert.deepEqual([...provenance.overlay_resealed_ingress_keys], resealed);
  for (const key of resealed) assert.ok(paragraph.includes(key), key);

  // And no OTHER sentence in the accreted reason may claim a frontier
  // transition: the superseded "835 to 839" was exactly that shape, sitting in
  // a paragraph nobody re-read. The frontier check enforces the same rule, so
  // this fails in ops/ci.sh --only gates as well as here.
  assert.deepEqual([...review.reason.matchAll(/\b\d{3} to \d{3}\b/g)].map(match => match[0]),
    [`${before.length} to ${after.length}`]);

  // The migration says the same measured thing, and the generator states no
  // count of its own in prose any more -- a comment cannot be derived, so it
  // must not carry the number that drifted.
  const generator = readRegistryArtifact(
    new URL("../../ops/scac-mutation-inventory.mjs", import.meta.url), "utf8");
  const comments = generator.split("\n").filter(line => line.trim().startsWith("//"));
  for (const line of comments)
    assert.doesNotMatch(line, /(three|four|five) LaunchAgent definitions/, line.trim());
  // AND THE MIGRATION COMMENT IS THE SAME MEASURED SENTENCE, number agreement
  // included. v26 admits ONE ingress, so the sentence reads "is admitted as a
  // new ingress"; a fixed plural here would be a fourth place describing a
  // delta it did not measure, which is the failure this whole test exists for.
  const admissionVerb = admitted.length === 1
    ? "is admitted as a new ingress" : "are admitted as new ingresses";
  assert.ok(v26Migration.replaceAll("\n-- ", " ").includes(
    `${provenance.admitted_description} ${admissionVerb}`),
    v26Migration.slice(0, 700));
});

test("the Gate Zero canary agent passes only arguments the wrapper's own parser accepts", () => {
  // THE DEFECT THIS EXISTS FOR (PR #1006 review 1): the plist passed a retired
  // `evidence ref file` option, and bin/run-scheduled.sh treats an unrecognised
  // flag as the FIRST POSITIONAL argument rather than refusing it. The service
  // key would have been the flag, the run key the path, and the command the
  // word after it. Nothing was red, because nothing ran the two files together.
  //
  // The recognised options are read OUT OF THE WRAPPER, not restated here: a
  // future option added there is covered without editing this test, and an
  // option removed there turns a plist that still passes it red.
  const wrapper = readRegistryArtifact(
    new URL("../../bin/run-scheduled.sh", import.meta.url), "utf8");
  const loop = wrapper.slice(wrapper.indexOf('while [ "$#" -gt 0 ]; do'),
    wrapper.indexOf("done", wrapper.indexOf('while [ "$#" -gt 0 ]; do')));
  const recognised = [...loop.matchAll(/^\s{4}(-{1,2}[a-z-]*)\)/gm)].map(match => match[1]);
  assert.deepEqual(recognised.sort(), ["--", "--also-heartbeat", "--heartbeat-interval"]);

  const plist = readRegistryArtifact(
    new URL("../../ops/launchd/com.carr.gate-zero-canary.plist", import.meta.url), "utf8");
  const start = plist.indexOf("<key>ProgramArguments</key>");
  assert.ok(start > 0);
  const array = plist.slice(plist.indexOf("<array>", start), plist.indexOf("</array>", start));
  const argv = [...array.matchAll(/<string>([^<]*)<\/string>/g)].map(match => match[1]);
  assert.deepEqual(argv, [
    "/bin/zsh",
    "{{REPO}}/bin/run-scheduled.sh",
    "gate-zero-canary",
    "gatezero.canary",
    "/bin/zsh",
    "{{REPO}}/bin/gate-zero-canary.sh",
  ]);
  // Anything option-shaped after the wrapper has to be one the loop above
  // recognises. Today there is none, and that is the assertion: a positional
  // shape cannot be misread as an option, and an option cannot be misread as a
  // positional.
  for (const argument of argv.slice(2)) {
    if (argument.startsWith("-")) assert.ok(recognised.includes(argument), argument);
  }
  assert.equal(plist.includes("evidence-ref-file"), false);

  // And the child is a no-op: one statement, no file, no output. A canary that
  // writes something can fail at writing it, and a failed canary row says the
  // scheduler is broken about a scheduler that just proved it works.
  const canary = readRegistryArtifact(
    new URL("../../bin/gate-zero-canary.sh", import.meta.url), "utf8");
  const statements = canary.split("\n")
    .filter(line => line.trim() && !line.startsWith("#"));
  assert.deepEqual(statements, ["exit 0"]);

  // The end-to-end proof that all of this actually produces the row card 12
  // reads lives where a database exists: ops/gate-zero-scheduler-canary-gate.py,
  // which the migration class runs on its disposable Postgres. Its `# ci:
  // db-gate` marker is what wires it, so the marker is asserted here.
  const gate = readRegistryArtifact(
    new URL("../../ops/gate-zero-scheduler-canary-gate.py", import.meta.url), "utf8");
  assert.match(gate, /^# ci: db-gate$/m);
  assert.match(gate, /readSchedulerCanaryEvidence/);
  assert.match(gate, /receipt_binding/);
  assert.match(gate, /observation_after_dispatch/);
});

test("the v25-v30 public surfaces admit no caller input under any shape", () => {
  // THE MANDATORY SWEEP of the 2026-09-11 standing rule, for every export this
  // branch adds -- the renderer, the trust root and the provenance, each named
  // in the loop at the end of this test rather than counted here, because the
  // count in this comment went stale the moment the third one arrived (PR
  // #1006 review 4). The renderer and the trust root were plain functions
  // reading caller-supplied values until PR #1006 review 1: the renderer took
  // `rows` and a `predecessorArtifacts`
  // holder, so a Proxy get trap threw the caller's own value back out and a
  // caller-supplied row carrying a privileged word landed in the returned SQL.
  const canonical = renderV5ScheduledJobAdmissionForwardRegistrySql();
  const canonicalProvenance = v5ScheduledJobAdmissionProvenance();
  const canonicalV26 = renderGateZeroOutcomeAdmissionForwardRegistrySql();
  const canonicalProvenanceV26 = gateZeroOutcomeAdmissionProvenance();
  const canonicalV27 = renderFoundationAssuranceForwardRegistrySql();
  const canonicalV28 = renderDealFieldProvenanceForwardRegistrySql();
  const canonicalV29 = renderProgramControllerForwardRegistrySql();
  const canonicalV30 = renderProducerTrioForwardRegistrySql();
  const canonicalV31 = renderDocConversationWriteDoorsForwardRegistrySql();
  const canonicalV32 = renderDocConversationListForwardRegistrySql();
  const canonicalV33 = renderNotificationPreferencesForwardRegistrySql();
  const canonicalV34 = renderSessionIdentityForwardRegistrySql();
  const canonicalV35 = renderDispatchSpineForwardRegistrySql();
  const marker = "HOSTILEMARKERTEXT";
  const privileged = [
    "allow", "commit", "prompt", "suppress", "release", "read", "covered",
    "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
    "satisfied", "complete", "admitted", "resumed", "attended", "verified",
    "present", "equivalent", "operational", "active", "green", "joins_exactly",
    "coverage_complete", "favorable", "would_", "_if_authoritative",
  ];
  const hostileRow = {
    ingress_key: `mcp-tool:${marker}`, source_locator: `${marker}/allow.js`,
    source_digest: "0".repeat(64), implementation_state: "passing",
    entry_digest: `sha256:${"f".repeat(64)}`,
  };
  const throwingProxy = new Proxy({}, {
    get() { throw marker; },
    has() { throw marker; },
    getPrototypeOf() { throw marker; },
  });
  const inputs = [
    [], [undefined], [null], [[hostileRow]], [[hostileRow], { secdef_execute: { count: 1, digest: marker } }],
    [[hostileRow], undefined, { migration: marker, runtime: marker }],
    [[hostileRow], undefined, throwingProxy],
    [throwingProxy], [throwingProxy, throwingProxy, throwingProxy],
    [{ length: 1, 0: hostileRow }], ["allow"], [Symbol.iterator], [() => [hostileRow]],
  ];
  for (const argv of inputs) {
    const label = `input ${JSON.stringify(argv.map(value => typeof value))}`;
    const rendered = renderV5ScheduledJobAdmissionForwardRegistrySql(...argv);
    assert.equal(rendered, canonical, label);
    assert.equal(rendered.includes(marker), false, label);
    assert.equal(assertV5ScheduledJobAdmissionV25TrustRoot(...argv), undefined, label);
    // The v26 tail's renderer and trust root take the same sweep, input for
    // input: a successor renderer that started reading a caller would be the
    // PR 985 defect arriving one generation later.
    const renderedV26 = renderGateZeroOutcomeAdmissionForwardRegistrySql(...argv);
    assert.equal(renderedV26, canonicalV26, label);
    assert.equal(renderedV26.includes(marker), false, label);
    assert.equal(assertGateZeroOutcomeAdmissionV26TrustRoot(...argv), undefined, label);
    assert.deepEqual(gateZeroOutcomeAdmissionProvenance(...argv), canonicalProvenanceV26, label);
    const renderedV27 = renderFoundationAssuranceForwardRegistrySql(...argv);
    assert.equal(renderedV27, canonicalV27, label);
    assert.equal(renderedV27.includes(marker), false, label);
    assert.equal(assertFoundationAssuranceV27TrustRoot(...argv), undefined, label);
    // The v28 tail arrives on the same sweep the generation it succeeds did.
    const renderedV28 = renderDealFieldProvenanceForwardRegistrySql(...argv);
    assert.equal(renderedV28, canonicalV28, label);
    assert.equal(renderedV28.includes(marker), false, label);
    assert.equal(assertDealFieldProvenanceV28TrustRoot(...argv), undefined, label);
    // The v29 tail arrives on the same sweep. It matters more here than for any
    // predecessor: this renderer is the first to read a SECOND artifact off
    // disk (the 0517 domain migration), so a caller that could steer it could
    // steer which DDL the seal claims to cover.
    const renderedV29 = renderProgramControllerForwardRegistrySql(...argv);
    assert.equal(renderedV29, canonicalV29, label);
    assert.equal(renderedV29.includes(marker), false, label);
    assert.equal(assertProgramControllerV29TrustRoot(...argv), undefined, label);
    // The v30 tail arrives on the same sweep, and it raises the v29 stake: this
    // renderer reads THREE domain migrations off disk rather than one, so a
    // caller that could steer it could steer which of the trio's DDL the seal
    // claims to cover.
    const renderedV30 = renderProducerTrioForwardRegistrySql(...argv);
    assert.equal(renderedV30, canonicalV30, label);
    assert.equal(renderedV30.includes(marker), false, label);
    assert.equal(assertProducerTrioV30TrustRoot(...argv), undefined, label);
    // The v31 tail arrives on the same sweep.
    const renderedV31 = renderDocConversationWriteDoorsForwardRegistrySql(...argv);
    assert.equal(renderedV31, canonicalV31, label);
    assert.equal(renderedV31.includes(marker), false, label);
    assert.equal(assertDocConversationWriteDoorsV31TrustRoot(...argv), undefined, label);
    // The v32 tail arrives on the same sweep.
    const renderedV32 = renderDocConversationListForwardRegistrySql(...argv);
    assert.equal(renderedV32, canonicalV32, label);
    assert.equal(renderedV32.includes(marker), false, label);
    assert.equal(assertDocConversationListV32TrustRoot(...argv), undefined, label);
    // The v33 tail arrives on the same sweep.
    const renderedV33 = renderNotificationPreferencesForwardRegistrySql(...argv);
    assert.equal(renderedV33, canonicalV33, label);
    assert.equal(renderedV33.includes(marker), false, label);
    assert.equal(assertNotificationPreferencesV33TrustRoot(...argv), undefined, label);
    // The provenance export is on this sweep too: it MEASURES a delta and
    // renders the sentences three other files carry, so a caller that could
    // steer it could steer the provenance of the seal itself.
    assert.deepEqual(v5ScheduledJobAdmissionProvenance(...argv), canonicalProvenance, label);
  }
  // The canonical artifact carries no privileged word of its own as a bare
  // token either, beyond the SQL vocabulary the migration is written in. This
  // asserts the narrower thing the sweep is actually for: nothing a CALLER can
  // name reaches the output, so the only occurrences are this repository's own.
  assert.equal(privileged.some(word => canonical.includes(`${marker}${word}`)), false);

  // Amendment 2's closed shape for an exported callable, on each export the
  // loop below names -- the list, not a count, is what has to stay true: not
  // constructable, no prototype, and an own Symbol.hasInstance data property
  // that answers without touching the left operand.
  const closedSurface = [
    ["renderV5ScheduledJobAdmissionForwardRegistrySql", renderV5ScheduledJobAdmissionForwardRegistrySql],
    ["assertV5ScheduledJobAdmissionV25TrustRoot", assertV5ScheduledJobAdmissionV25TrustRoot],
    ["v5ScheduledJobAdmissionProvenance", v5ScheduledJobAdmissionProvenance],
    // The v26 tail's three, swept on exactly the same terms.
    ["renderGateZeroOutcomeAdmissionForwardRegistrySql", renderGateZeroOutcomeAdmissionForwardRegistrySql],
    ["assertGateZeroOutcomeAdmissionV26TrustRoot", assertGateZeroOutcomeAdmissionV26TrustRoot],
    ["gateZeroOutcomeAdmissionProvenance", gateZeroOutcomeAdmissionProvenance],
    ["renderFoundationAssuranceForwardRegistrySql", renderFoundationAssuranceForwardRegistrySql],
    ["assertFoundationAssuranceV27TrustRoot", assertFoundationAssuranceV27TrustRoot],
    ["renderDealFieldProvenanceForwardRegistrySql", renderDealFieldProvenanceForwardRegistrySql],
    ["assertDealFieldProvenanceV28TrustRoot", assertDealFieldProvenanceV28TrustRoot],
    ["renderProgramControllerForwardRegistrySql", renderProgramControllerForwardRegistrySql],
    ["assertProgramControllerV29TrustRoot", assertProgramControllerV29TrustRoot],
    ["renderProducerTrioForwardRegistrySql", renderProducerTrioForwardRegistrySql],
    ["assertProducerTrioV30TrustRoot", assertProducerTrioV30TrustRoot],
    ["renderDocConversationWriteDoorsForwardRegistrySql", renderDocConversationWriteDoorsForwardRegistrySql],
    ["assertDocConversationWriteDoorsV31TrustRoot", assertDocConversationWriteDoorsV31TrustRoot],
    ["renderDocConversationListForwardRegistrySql", renderDocConversationListForwardRegistrySql],
    ["assertDocConversationListV32TrustRoot", assertDocConversationListV32TrustRoot],
    ["renderNotificationPreferencesForwardRegistrySql", renderNotificationPreferencesForwardRegistrySql],
    ["assertNotificationPreferencesV33TrustRoot", assertNotificationPreferencesV33TrustRoot],
    ["renderSessionIdentityForwardRegistrySql", renderSessionIdentityForwardRegistrySql],
    ["assertSessionIdentityV34TrustRoot", assertSessionIdentityV34TrustRoot],
    ["renderDispatchSpineForwardRegistrySql", renderDispatchSpineForwardRegistrySql],
    ["assertDispatchSpineV35TrustRoot", assertDispatchSpineV35TrustRoot],
    ["renderReadyPlanAmendmentForwardRegistrySqlClosed", renderReadyPlanAmendmentForwardRegistrySqlClosed],
    ["renderReadyPlanAmendmentMeasurementProbeSqlClosed", renderReadyPlanAmendmentMeasurementProbeSqlClosed],
  ];

  // A FOURTH CLOSED EXPORT CANNOT ARRIVE UNSWEPT. Two comments counted this
  // surface instead of naming it and both went stale when the provenance
  // export landed; the durable fix is that the LIST is now checked against the
  // module's own text, so an export added without a line in this sweep fails
  // here rather than in a reviewer's diff.
  const generatorSource = readRegistryArtifact(
    new URL("../../ops/scac-mutation-inventory.mjs", import.meta.url), "utf8");
  assert.deepEqual(
    [...generatorSource.matchAll(/^export const (\w+) =\s*\n\s*closedExport\(/gm)]
      .map(match => match[1]).sort(),
    closedSurface.map(([name]) => name).sort());

  for (const [name, exported] of closedSurface) {
    assert.equal(typeof exported, "function", name);
    assert.equal(Object.hasOwn(exported, "prototype"), false, name);
    assert.throws(() => Reflect.construct(exported, []), TypeError, name);
    const descriptor = Object.getOwnPropertyDescriptor(exported, Symbol.hasInstance);
    assert.equal(descriptor.writable, false, name);
    assert.equal(descriptor.configurable, false, name);
    assert.equal(typeof descriptor.value, "function", name);
    // The left operand is never touched: a Proxy whose getPrototypeOf throws
    // would otherwise carry its own thrown value out of an export.
    assert.equal(throwingProxy instanceof exported, false, name);
  }
});




test("a definition-only LaunchAgent is exempt from service closure and refused if it claims one", () => {
  // The repo-hygiene agent must stay out of ops/config/services.json: it is a
  // reviewed definition, not a deployment. The exemption is derived from the
  // artifact -- no trigger and no load-time start means launchd has no moment
  // to fire it -- so it cannot drift away from what the file actually says.
  const agent = readRegistryArtifact(
    new URL("../../ops/launchd/com.carr.repo-hygiene-janitor.plist", import.meta.url), "utf8");
  const plist = parsePlistXml(agent);
  assert.equal(plist.RunAtLoad, false);
  assert.ok(isDefinitionOnlyLaunchd(plist));
  for (const key of ["StartCalendarInterval", "StartInterval", "WatchPaths", "KeepAlive"])
    assert.equal(plist[key], undefined, key);
  assert.ok(!JSON.stringify(plist.ProgramArguments).includes("--execute"));

  // Every deployed agent is still held to closure: a plist with a trigger is
  // not definition-only, so the exemption cannot be reached by accident.
  assert.equal(isDefinitionOnlyLaunchd({ RunAtLoad: true }), false);
  assert.equal(isDefinitionOnlyLaunchd({ StartInterval: 60 }), false);
  assert.equal(isDefinitionOnlyLaunchd({}), true);

  const services = { services: [] };
  const legacy = { surfaces: [] };
  const agentPath = "ops/launchd/com.carr.repo-hygiene-janitor.plist";
  // Exempt: closure passes with no service entry at all.
  assert.doesNotThrow(() =>
    validateLaunchdAuthorityCatalogs([agentPath], services, legacy, [agentPath]));
  // Not exempt: the same path without the exemption still demands a mechanism.
  assert.throws(() => validateLaunchdAuthorityCatalogs([agentPath], services, legacy, []),
    /launchd ops\.service catalog closure mismatch missing=/);
  // THE CONVERSE, which is the half that keeps this from being a hole: a
  // definition-only agent that DOES claim a deploy mechanism is a contradiction.
  const claimed = { services: [{ key: "repo-hygiene-janitor", environments: [
    { environment: "production", deploy_mechanism: agentPath }] }] };
  assert.throws(() =>
    validateLaunchdAuthorityCatalogs([agentPath], claimed, legacy, [agentPath]),
    /definition-only launchd agent claims a deploy mechanism/);
});








test("source-only migration diagnostics preserve the sealed runtime frontier", () => {
  const sealed = frozenInventory(SCAC_MUTATION_REGISTRY_VERSION);
  const confirmMerge = sealed.find(row => row.ingress_key === "mcp-tool:confirm-merge");
  assert.equal(confirmMerge.human_only, true);
  assert.equal(confirmMerge.principal_mode, "server_verified_human");
  const fixture = JSON.parse(readRegistryArtifact(new URL("../../ops/config/scac-registry-source-inventory-fixtures.v1.json", import.meta.url), "utf8"));
  const reviewVersion = Object.keys(fixture.current_source_reviews)
    .sort((left, right) => Number(left.split('.v')[1]) - Number(right.split('.v')[1])).at(-1);
  const review = fixture.current_source_reviews[reviewVersion];
  for (const locator of ["bin/migrate-prod.sh", "tools/migrate-prod-support.py"]) {
    const row = review?.upsert.find(row => row.source_locator === locator) || sealed.find(row => row.source_locator === locator);
    const previous = sealed.find(row => row.source_locator === locator);
    assert.ok(previous, "reviewed administration script already exists in the seal");
    const digest = sha256(readRegistryArtifact(new URL(`../../${locator}`, import.meta.url), "utf8"));
    assert.equal(row.schema_digest, digest);
    assert.equal(row.handler_digest, digest);
  }
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS), true);
});

test("the complete source-only frontier is byte-reproducible from frozen inputs", () => {
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS, CURRENT_REGISTRY_VERSION), true);
  // The push toll calls the bare API; its default must follow the newest frontier.
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS), true);
  const paths = assertGeneratedFrontierMatchesCommitted();
  const migrations = paths.filter(path => path.startsWith("migrations/")).sort();
  assert.equal(migrations.length, 118);
  assert.deepEqual(migrations.map(path => path.match(/migrations\/(\d{4})_/)[1]),
    [...Array.from({ length: 18 }, (_, index) => String(454 + index).padStart(4, "0")), "0481", "0486", "0487", "0488", "0489", "0490", "0491", "0492", "0493", "0494", "0495", "0496", "0497", "0498", "0501", "0503", "0512", "0516", "0518", "0522", "0524", "0526", "0528", "0530", "0532", "0541", "0543", "0545", "0547", "0548", "0549", "0550", "0551", "0552", "0553", "0555", "0557", "0558", "0559", "0560", "0561", "0562", "0563", "0564", "0566", "0567", "0568", "0569", "0570", "0572", "0576", "0578", "0581", "0582", "0584", "0585", "0588", "0589", "0600", "0603", "0609", "0614", "0618", "0625", "0627", "0629", "0701", "0705", "0707", "0709", "0718", "0720", "0722", "0723", "0725", "0727", "0730", "0731", "0734", "0737", "0739", "0741", "0743", "0745", "0748", "0750", "0755", "0763", "0767", "0768", "0786", "0787", "0807", "0812", "0825", "0827", "0840", "0842", "0844", "0846"]);
  assert.equal(paths.filter(path => path.endsWith(".generated.js")).length, 109);
  assert.equal(paths.length, 227);
  // 0502 IS DELIBERATELY ABSENT FROM THIS LIST. It is a hand-authored domain
  // migration under its own review, not a generated artifact, so nothing here
  // reproduces it byte for byte and it must not appear among the frontier's
  // outputs. A generator that started emitting it would be claiming authorship
  // of the Gate Zero record itself.
  assert.equal(paths.some(path => path.includes("0502_")), false);
});

test("the complete frontier renders when every generated target is absent", () => {
  const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
  const isolatedRoot = fs.realpathSync(fs.mkdtempSync(path.join(tmpdir(), "carr-frontier-targetless-")));
  const outputRoot = path.join(isolatedRoot, "generated");
  const frontier = renderGeneratedFrontier();
  const frontierPaths = Object.keys(frontier);
  const frontierSet = new Set(frontierPaths);
  try {
    // Coverage discovery must never walk a concurrently copied fixture tree.
    assert.ok(path.relative(repoRoot, isolatedRoot).startsWith(`..${path.sep}`),
      "targetless fixture must be outside the source tree");
    const trackedPaths = parseGitIndexEntries(execFileSync("git", ["ls-files", "--stage", "-z"], {
      cwd: repoRoot,
      encoding: "buffer",
    })).map(entry => entry.path);
    for (const trackedPath of new Set([
      ...trackedPaths,
      "migrations/0511_foundation_assurance_minimum_outcome.sql",
      "ops/registry-chain.mjs",
      "ops/registry-history.mjs",
      "ops/config/scac-registry-chain.json",
      "mcp-server/src/scac-mutation-registry.current.generated.js",
    ])) {
      if (frontierSet.has(trackedPath)) continue;
      const source = path.join(repoRoot, trackedPath);
      const target = path.join(isolatedRoot, trackedPath);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.copyFileSync(source, target);
    }
    assert.equal(frontierPaths.filter(target => fs.existsSync(path.join(isolatedRoot, target))).length, 0);
    const stdout = execFileSync(process.execPath, [
      path.join(isolatedRoot, "ops/scac-mutation-inventory.mjs"),
      "--write-generated-frontier",
      outputRoot,
    ], {
      cwd: isolatedRoot,
      encoding: "utf8",
      env: {
        ...process.env,
        GIT_DIR: path.join(repoRoot, ".git"),
        GIT_WORK_TREE: isolatedRoot,
      },
    });
    // Derived from the frontier itself rather than pinned to a literal that
    // goes stale every time the frontier grows. The real assertion is the
    // byte-for-byte comparison below; this only confirms the renderer reported
    // the whole set rather than a subset.
    assert.match(stdout, new RegExp(`\\(${frontierPaths.length} artifacts\\)`));
    const verifyExports = () => {
      for (const [target, expected] of Object.entries(frontier))
        assert.equal(fs.readFileSync(path.join(outputRoot, target), "utf8"), expected, target);
    };
    verifyExports();
    const historical = Object.keys(frontier).find(target => /registry\.v35\.generated\.js$/.test(target));
    assert.ok(historical, 'exercise an exported historical runtime');
    const exported = path.join(outputRoot, historical);
    fs.writeFileSync(exported, 'corrupt historical output\n');
    assert.throws(verifyExports, /AssertionError/);
    fs.unlinkSync(exported);
    assert.throws(verifyExports, /ENOENT/);
    fs.writeFileSync(exported, frontier[historical]);
    verifyExports();
  } finally {
    fs.rmSync(isolatedRoot, { recursive: true, force: true });
  }
});

test("every direct catalog redefinition preserves the portable role census", () => {
  for (const [name, sql] of directRegistryRedefinitions) {
    assert.match(sql, /rolname~'\^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper/, name);
    assert.match(sql, /mem\.rolname~'\^carr_' and \(g\.rolsuper or g\.rolname~'\^\(neon_\|pg_\)'\)/, name);
    assert.match(sql, /return observed_count=12 and observed_digest='sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648'/, name);
    assert.doesNotMatch(sql, /observed_count=95|082b8570b428c33296c801871177f6bfb34e9c070513d4b1db23007f4edecafb/, name);
  }
  assert.equal((siep18MonitorMigration.match(/a\.grantee<>c\.relowner/g) || []).length, 8);
});

test("unknown, changed, and open operation contracts refuse deterministically", async () => {
  const source = TOOLS["add-loop"];
  await assert.rejects(
    assertRegisteredOperation("not-reviewed", source, {}),
    error => error instanceof MutationRegistryRefusal && error.error === "unregistered_operation",
  );
  await assert.rejects(
    assertRegisteredOperation("add-loop", { ...source, write: !source.write }, {}),
    error => error instanceof MutationRegistryRefusal && error.error === "mutation_contract_mismatch",
  );
  assert.throws(
    () => assertClosedTopLevel("add-loop", source, { text: "safe", actor: "joe" }),
    error => error instanceof MutationRegistryRefusal && error.error === "unregistered_operation_fields",
  );
  assert.equal(registeredOperation("not-reviewed"), null);
});

test("the sealed v11 registry admits the real Codex checkpoint contract", async () => {
  const sealedDigest =
    "c19344c49e59fb799584ccdd2829053c7b43b8a072d72fd90b2c759a9e17b760";
  assert.equal(sha256(TOOLS["codex-checkpoint"].inputSchema), sealedDigest);
  const operation = await assertRegisteredOperation(
    "codex-checkpoint",
    TOOLS["codex-checkpoint"],
    {
      idempotency_key: "00000000-0000-4000-8000-000000000001",
      runtime: "codex",
      native_task_id: "task-1",
      project_id: "project-1",
      cwd: "/repo",
      expected_version: 0,
      state: { objective: "continue", next_action: "verify" },
    },
  );
  assert.equal(operation.schema_digest, sealedDigest);
});

test("composites expose exact reviewed edges and generic dispatch stays default deny", () => {
  assert.deepEqual(registeredOperation("stamp-touch").delegates_to, ["log-activity"]);
  assert.deepEqual(registeredOperation("resolve-candidate").delegates_to,
    ["log-activity", "new-deal", "patch-deal-field", "set-next-step"]);
  assert.deepEqual(registeredOperation("find-and-catch-up").delegates_to, ["catch-me-up", "find"]);
  assert.deepEqual(registeredOperation("prepare-conversation").delegates_to,
    ["find-and-catch-up", "who-do-we-know"]);
  assert.deepEqual(registeredOperation("morning-brief").delegates_to,
    ["claim-card", "deal-room-board", "loop-board", "today-triage"]);
  assert.deepEqual(registeredOperation("call-verb").delegates_to, ["*registered_operation"]);
  assert.match(readRegistryArtifact(new URL("../src/tools.js", import.meta.url), "utf8"),
    /executeRegisteredTool\(c, actor, "log-activity"/);
});

test("migration is read-only at runtime and preserves the SIEP-18 boundary", () => {
  assert.match(migration, /security definer set search_path=pg_catalog,ops/);
  assert.match(migration, /revoke all on ops\.scac_mutation_registry_version,ops\.scac_mutation_registry_entry from public,carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.match(migration, /grant execute on function ops\.scac_mutation_registration\(text,text\) to carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.doesNotMatch(migration, /grant (?:insert|update|delete|all) on ops\.scac_mutation_registry/i);
  assert.match(migration, /atomic_database_mediation_operational boolean not null check \(not atomic_database_mediation_operational\)/);
  assert.match(migration, /mcp_default_deny_source_guarded boolean not null/);
  assert.match(migration, /db_metadata_authority boolean not null check \(db_metadata_authority\)/);
  assert.match(migration, /runtime_projection_authorizing boolean not null check \(not runtime_projection_authorizing\)/);
  assert.match(migration, /non_mcp_default_deny_operational boolean not null check \(not non_mcp_default_deny_operational\)/);
  assert.match(migration, /direct_database_grant_cutover boolean not null check \(not direct_database_grant_cutover\)/);
  assert.match(migration, /production_enforcement_active boolean not null check \(not production_enforcement_active\)/);
  assert.match(migration, /before insert or update or delete on ops\.scac_mutation_registry_entry/);
});

test("reviewed non-MCP source locators resolve and remain explicitly non-authorizing", () => {
  const rows = fullInventory(TOOLS).filter(row => !["mcp_tool", "job_definition", "workflow_entrypoint"].includes(row.ingress_kind));
  // 543 before this branch; the DoctorCRE portfolio tail adds exactly one
  // reviewed non-MCP source, ops/work-portfolio-local-pg-gate.py. The count is
  // pinned rather than derived on purpose, so a source file that appears
  // without review has to be noticed here. It moves only once the file is
  // TRACKED: the inventory enumerates git, so an untracked new gate is
  // invisible to this assertion and the count shifts at `git add`, not at
  // save.
  // 545 before the v25 registry successor; admitting the Gate Zero canary adds
  // two reviewed non-MCP sources, bin/gate-zero-canary.sh and the acceptance
  // gate that runs it end to end, ops/gate-zero-scheduler-canary-gate.py. The
  // three new LaunchAgents are workflow_entrypoint rows and are filtered out
  // above.
  // 547 before origin/v5-producer-step-a merged in, 548 while its build-time
  // sealer still carried a shebang, and 547 again now. Step A's final
  // correction made mcp-server/bin/seal-candidate-manifest.mjs a LIBRARY --
  // bin/deploy-worker.sh imports sealCandidateManifest in one evaluation
  // instead of executing the file -- so it carries no shebang and no
  // command-line main and the predicate no longer admits it. The number went
  // up and came back down, which is exactly the movement a pinned count exists
  // to make visible.
  // WR95 adds two reviewed administrative entrypoints: the live evidence
  // sealer and the bounded candidate rehearsal. Both are tracked before the
  // v27 frontier is frozen, so the pinned count advances by exactly two.
  // WR126 adds one reviewed administrative entrypoint: the credential-safe
  // canonical-ownership issuer provisioner.
  // WR130 adds the tracked release-readiness DB acceptance gate as one
  // reviewed administrative entrypoint; it also has a CLI shebang.
  // v40 adds the tracked private snapshot connection helper as one reviewed
  // script ingress; it carries no runtime authorization.
  // Decision 05e144eb (2026-09-24): a new script no longer reseals the
  // registry, so this is a floor at the last sealed frontier (v61), not a pin.
  // Pull-request review notices a new source; the floor notices mass loss.
  assert.ok(rows.length >= 553, `non-MCP rows fell below the v61 frontier: ${rows.length}`);
  for (const row of rows) {
    assert.equal(fs.existsSync(new URL(`../../${row.source_locator}`, import.meta.url)), true,
      `${row.source_locator} must resolve`);
    assert.equal(row.classification_authorizing, false);
    assert.equal(row.implementation_state, "inventoried_not_atomically_mediated");
  }
  const scripts = discoverScriptEntrypoints();
  // 534 before the portfolio tail, 536 before v25; two new executables,
  // bin/gate-zero-canary.sh and ops/gate-zero-scheduler-canary-gate.py. It went
  // to 539 while Step A's build-time candidate sealer carried a shebang and is
  // back to 538 now that Step A's final correction made that sealer a library
  // bin/deploy-worker.sh IMPORTS rather than a file it executes.
  // WR95's evidence sealer and candidate rehearsal are both intentional
  // command-line entrypoints, so discovery advances by the same exact two.
  // WR126 adds the canonical-ownership issuer provisioner.
  // v40 adds the private snapshot connection helper; B09 adds its local PG gate.
  assert.ok(scripts.length >= 544, `discovered scripts fell below the v61 frontier: ${scripts.length}`);
  // AND THE SEALER IS ASSERTED ABSENT, because a shebang put back on it is an
  // ingress this branch's registry successor does not seal, and the whole point
  // of the predicate is that intent does not enter it.
  assert.equal(scripts.some(path => path === "mcp-server/bin/seal-candidate-manifest.mjs"), false);
  assert.equal(scripts.some(path => path === "ops/rule-delivery-cutover.py"), true);
  assert.equal(scripts.some(path => path === "ops/release-readiness-gate.py"), true);
  assert.equal(scripts.some(path => path === "ops/schema-snapshot-connection.py"), true);
  assert.equal(scripts.some(path => path === "ops/control-plane-scheduler-cutover.py"), true);
  assert.equal(scripts.some(path => path === "run.sh"), true);
  assert.equal(scripts.some(path => path === "mcp-server/local-verb.mjs"), true);
  assert.equal(scripts.some(path => path === "hooks/scheduled-run-record.py"), true);
  assert.equal(scripts.some(path => path === "pipelines/backfill_lease_event.py"), true);
  assert.equal(scripts.some(path => path === "pipelines/import_brokers.py"), true);
  assert.equal(scripts.some(path => path === "ops/sync_control_catalog.py"), true);
  assert.equal(scripts.some(path => path === "ops/githooks/pre-commit"), true);
  assert.equal(scripts.some(path => path === "ops/githooks/pre-push"), true);
  assert.equal(scripts.some(path => path === "ops/githooks/commit-msg"), true);
  for (const path of [
    "ops/capture-verb-reachability.py",
    "ops/delegation-telemetry-report.py",
    "ops/gate-lifecycle-report.py",
    "ops/rule-triage-apply.py",
    "ops/rule-triage-report.py",
    "ops/zz-engineering-controller-concurrency-gate.py",
    "hooks/canonical-edit-gate.py",
    "ops/untracked-anomaly-report.py",
  ]) assert.equal(scripts.includes(path), true, `${path} must be registered`);
  /* canonical-edit-gate.py moved to the PRESENCE list above when Joe ruled
     REINSTATE (decision 7f48abf6, 2026-09-01; Repo Hygiene Program R02). The
     assertion itself stays: it is the record of PR #722's decision, and these
     two gates are still retired -- R02 reinstated one of the three. */
  for (const path of [
    "hooks/git-writer-gate.py",
    "hooks/staging-attribution-gate.py",
  ]) assert.equal(scripts.includes(path), false, `${path} was retired upstream`);
  assert.equal(scripts.some(path => path.includes("selftest") || path.includes("/test/")), false);
  assert.deepEqual(rows.filter(row => ["script_entrypoint", "external_admin", "break_glass"].includes(row.ingress_kind))
    .map(row => row.source_locator).sort(), scripts);
  assert.equal(rows.find(row => row.source_locator === "tools/db-tap.py").ingress_kind, "break_glass");
  assert.equal(rows.find(row => row.source_locator === "tools/call-verb.py").ingress_kind, "break_glass");
  assert.equal(rows.find(row => row.source_locator === "tools/run-breakglass.py").ingress_kind, "break_glass");
  assert.equal(rows.find(row => row.source_locator === "tools/migrate-prod-support.py").ingress_kind, "external_admin");
  assert.deepEqual(rows.find(row => row.source_locator === "run.sh").delegates_to,
    ["*registered_script_entrypoint"]);
  assert.deepEqual(rows.find(row => row.source_locator === "mcp-server/local-verb.mjs").delegates_to,
    ["*registered_mcp_tool"]);
});

test("script discovery is bound to tracked index paths and index executable modes", () => {
  const parsed = parseGitIndexEntries(Buffer.from(
    "100755 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 0\tbin/tracked-entry\0" +
    "100644 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 0\tops/tracked.py\0" +
    "100755 cccccccccccccccccccccccccccccccccccccccc 1\tbin/unmerged\0",
  ));
  assert.deepEqual(parsed, [
    { path: "bin/tracked-entry", executable: true },
    { path: "ops/tracked.py", executable: false },
  ]);
  assert.equal(parsed.some(({ path }) => path === "tmp/untracked.py"), false,
    "ambient untracked files cannot enter an index-derived census");
  assert.equal(isScriptEntrypoint("bin/tracked-entry", true, "#!/bin/sh\nexit 0\n"), true);
  assert.equal(isScriptEntrypoint("bin/tracked-entry", false, "#!/bin/sh\nexit 0\n"), false,
    "extensionless entrypoints use the Git index mode, never host stat bits");
  assert.equal(isScriptEntrypoint("ops/tracked.py", false,
    "if __name__ == '__main__':\n    main()\n"), true);
});

test("job definitions and live DB capabilities have exact reviewed baselines", () => {
  const jobs = jobDefinitionInventory();
  assert.equal(jobs.length, JOB_DEFINITION_BASELINE.count);
  assert.equal(jobs.every(row => row.ingress_kind === "job_definition" && row.entrypoint), true);
  assert.deepEqual(DB_CATALOG_BASELINE, {
    projection_version: "scac-db-catalog-projection.v1",
    secdef_execute: { count: 290, digest: "sha256:92d1347b45ee669c97a8b21712684651ee67aa3a2af363fca7c3f3a25436a0b6" },
    relation_dml: { count: 285, digest: "sha256:53d12ebf83db4661b0e55eb81f91ab510c34828424a2b945b66c0286134b0b0b" },
    column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
  });
  assert.match(migration, /ingress_kind','db_function_acl'/);
  assert.match(migration, /ingress_kind','db_relation_acl'/);
  assert.match(migration, /ingress_kind','db_column_acl'/);
  assert.match(migration, /entry_set_digest/);
  assert.match(migration, /ops\.scac_canonical_json\(contract\)/);
  assert.match(migration, /for category,kind in values \('secdef_execute','db_function_acl'\)/);
  assert.match(migration, /actual_digest<>expected->>'digest'/);
  assert.deepEqual(SIEP12_DB_CATALOG_BASELINE.role_authority, {
    count: 12,
    digest: "sha256:eb650de73032466b46787f4a5826b60b100591657489a7990d9161e2d6588648",
  });
  assert.match(successorMigration, /pg_auth_members/);
});

test("GitHub and launchd workflow entrances bind exact triggers, permissions, and delegates", () => {
  const workflows = workflowDefinitionInventory();
  const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
  const trackedPaths = parseGitIndexEntries(execFileSync("git", ["ls-files", "--stage", "-z"], {
    cwd: repoRoot, encoding: "buffer",
  })).map(entry => entry.path);
  const githubPaths = trackedPaths.filter(p => p.startsWith(".github/workflows/") && /\.ya?ml$/.test(p));
  const launchdPaths = trackedPaths.filter(p => p.startsWith("ops/launchd/") && p.endsWith(".plist"));
  assert.deepEqual(workflows.map(row => row.source_locator).sort(), [...githubPaths, ...launchdPaths].sort());
  const github = workflows.filter(row => row.source_locator.startsWith(".github/workflows/"));
  assert.deepEqual(github.map(row => row.source_locator).sort(), githubPaths.sort());
  assert.equal(github.every(row => row.ingress_kind === "workflow_entrypoint" &&
    row.trigger_contract_digest && row.permissions_contract_digest && row.classification_authorizing === false), true);
  const automerge = workflows.find(row => row.source_locator === ".github/workflows/automerge-pilot.yml");
  assert.equal(automerge.delegates_to.includes("script:ops/automerge_pilot.py"), true);
  const backup = workflows.find(row => row.source_locator === ".github/workflows/backup-nightly.yml");
  assert.equal(backup.delegates_to.includes("shell:aws-s3api-put-object"), true);
  const dbAcceptance = workflows.find(row => row.source_locator === ".github/workflows/db-acceptance.yml");
  assert.equal(dbAcceptance.delegates_to.includes("script:ops/local-pg-ci.py"), true);
  const launchd = workflows.filter(row => row.source_locator.startsWith("ops/launchd/"));
  assert.deepEqual(launchd.map(row => row.source_locator).sort(), launchdPaths.sort());
  // Every agent is fully identified and carries SOME physical authority ref;
  // only a DEPLOYED agent's is a service environment. Collapsing those two into
  // one clause is what would let a definition-only agent either slip through
  // unidentified or be forced to claim a deployment it must not have.
  assert.equal(launchd.every(row => row.launchd_label && row.trigger_contract_digest &&
    row.program_arguments_digest && row.physical_authority_refs.length > 0 &&
    row.classification_authorizing === false), true);
  const deployedLaunchd = launchd.filter(row =>
    !row.physical_authority_refs.includes("ops.definition_only_launchd:not_deployed"));
  assert.equal(deployedLaunchd.every(row =>
    row.physical_authority_refs.some(ref => ref.startsWith("ops.service_environment:"))), true);
  const services = JSON.parse(readRegistryArtifact(new URL("../../ops/config/services.json", import.meta.url), "utf8"));
  const expectedServiceRefs = services.services.flatMap(service => service.environments
    .filter(environment => launchdPaths.includes(environment.deploy_mechanism))
    .map(environment => `${environment.deploy_mechanism}|ops.service_environment:${service.key}:${environment.environment}`));
  const actualServiceRefs = launchd.flatMap(row => row.physical_authority_refs
    .filter(ref => ref.startsWith("ops.service_environment:"))
    .map(ref => `${row.source_locator}|${ref}`));
  assert.deepEqual(actualServiceRefs.sort(), expectedServiceRefs.sort());
  assert.deepEqual(deployedLaunchd.map(row => row.source_locator).sort(),
    [...new Set(expectedServiceRefs.map(ref => ref.split("|")[0]))].sort());
  assert.equal(launchd.find(row => row.launchd_label === "com.carr.rules-refresh")
    .physical_authority_refs.includes("ops.service_environment:rules-refresh:production"), true);
  // The definition-only agent carries an explicit non-deployed authority ref in
  // its OWN namespace. It is a registered row a reviewer can see, and it does
  // not inflate the deployed-environment total above.
  assert.deepEqual(launchd.filter(row =>
    row.physical_authority_refs.includes("ops.definition_only_launchd:not_deployed"))
    .map(row => row.launchd_label), ["com.carr.repo-hygiene-janitor", "com.carr.resource-collector"]);
  assert.equal(launchd.find(row => row.launchd_label === "com.carr.repo-hygiene-janitor")
    .physical_authority_refs.some(ref => ref.startsWith("ops.service_environment:")), false);
  // The three agents the v25 registry successor admitted. THE CANARY IS NOT
  // DEFINITION-ONLY BY THIS PREDICATE and must not be pinned as if it were:
  // isDefinitionOnlyLaunchd asks whether the plist carries any trigger at all,
  // and the canary carries a real hourly StartInterval. What KEPT it uninstalled
  // until 2026-09-12 was the DEFINITION_ONLY list now in lib/launchd_hold.py, which is
  // a different mechanism in a different file, so that is where this asserts it.
  for (const label of ["com.carr.canonical-fast-forward", "com.carr.canonical-dirty-watchdog",
    "com.carr.gate-zero-canary"]) {
    const row = launchd.find(entry => entry.launchd_label === label);
    assert.ok(row, label);
    assert.ok(row.physical_authority_refs.some(ref => ref.startsWith("ops.service_environment:")), label);
    assert.ok(Object.keys(row.trigger_contract || {}).length > 0 ||
      row.trigger_contract_digest, label);
  }
  const launchdHold = readRegistryArtifact(
    new URL("../../lib/launchd_hold.py", import.meta.url), "utf8");
  const definitionOnlyBlock = launchdHold.slice(
    launchdHold.indexOf("DEFINITION_ONLY: dict[str, str] = {"),
    launchdHold.indexOf("\n}\n", launchdHold.indexOf("DEFINITION_ONLY: dict[str, str] = {")));
  // THE CANARY'S SCHEDULE IS STARTED, so this clause is the mirror of what it
  // was until 2026-09-12: the plist must NOT be a key of DEFINITION_ONLY any
  // more. Joe's blanket approval (decision idempotency
  // 5e2b8c1a-9f47-4d63-b0e5-7a3d1c9f2e84) and his 2026-09-13 ruling that the
  // orchestrator runs activation commands itself are the deliberate removal the
  // hold's own reason asked for, and only launchd firing on its own can answer
  // step:scheduler-active-receipt -- a hand dispatch through the wrapper proves
  // the wrapper, never the scheduler. The janitor key is asserted PRESENT in
  // the same breath so a block that was mis-sliced to nothing cannot satisfy
  // the doesNotMatch clauses vacuously.
  assert.match(definitionOnlyBlock, /"com\.carr\.repo-hygiene-janitor\.plist":/);
  assert.doesNotMatch(definitionOnlyBlock, /"com\.carr\.gate-zero-canary\.plist"/);
  assert.doesNotMatch(definitionOnlyBlock, /"com\.carr\.canonical-fast-forward\.plist"/);
  assert.doesNotMatch(definitionOnlyBlock, /"com\.carr\.canonical-dirty-watchdog\.plist"/);
  assert.deepEqual(launchd.flatMap(row => row.physical_authority_refs)
    .filter(ref => ref.startsWith("ops.legacy_schedule_launchd_contract:")).sort(), [
      "ops.legacy_schedule_launchd_contract:calendar-fetch-daily.launchd.v1",
      "ops.legacy_schedule_launchd_contract:nightly-record-layer.launchd.v1",
      "ops.legacy_schedule_launchd_contract:notes-sweep-hourly.launchd.v1",
    ]);
  const rules = launchd.find(row => row.launchd_label === "com.carr.rules-refresh");
  assert.equal(rules.delegates_to.includes("script:bin/run-scheduled.sh"), true);
  assert.equal(rules.delegates_to.includes("script:bin/refresh-rules.sh"), true);
  const partnerPing = launchd.find(row => row.launchd_label === "com.carr.partner-ping");
  assert.equal(partnerPing.delegates_to.includes("script:pipelines/partner_ping.py"), true);
  const callMode = launchd.find(row => row.launchd_label === "com.carr.call-mode");
  assert.equal(callMode.delegates_to.includes("script:tools/dictation-rig/bin/call-mode.py"), true);
  const scripts = new Set(discoverScriptEntrypoints());
  for (const delegate of launchd.flatMap(row => row.delegates_to).filter(value => value.startsWith("script:")))
    assert.equal(scripts.has(delegate.slice("script:".length)), true, `${delegate} must resolve`);
  const rulesSource = readRegistryArtifact(new URL("../../ops/launchd/com.carr.rules-refresh.plist", import.meta.url), "utf8");
  const reviewedPlist = parsePlistXml(rulesSource);
  const changedPlist = parsePlistXml(rulesSource.replace("<integer>8</integer>", "<integer>88</integer>"));
  assert.equal(reviewedPlist.StartCalendarInterval.length, 14);
  assert.equal(reviewedPlist.StartCalendarInterval[1].Hour, 8);
  assert.equal(changedPlist.StartCalendarInterval[1].Hour, 88);
  assert.notDeepEqual(reviewedPlist.StartCalendarInterval, changedPlist.StartCalendarInterval);
  assert.match(migration, /'workflow_entrypoint'/);
});

test("launchd physical-authority catalogs are bidirectionally closed and source-exact", () => {
  const launchdPaths = fs.readdirSync(new URL("../../ops/launchd/", import.meta.url))
    .filter(name => name.endsWith(".plist")).map(name => `ops/launchd/${name}`).sort();
  const services = JSON.parse(readRegistryArtifact(
    new URL("../../ops/config/services.json", import.meta.url), "utf8"));
  const legacy = JSON.parse(readRegistryArtifact(
    new URL("../../ops/config/control-plane-scheduler-cutover.v1.json", import.meta.url), "utf8"));
  // The real caller derives its definition-only set from the artifacts, so this
  // test does the same rather than hard-coding a name: an agent with no trigger
  // and no load-time start is exempt from closure, and every other agent is not.
  const definitionOnly = launchdPaths.filter(path => isDefinitionOnlyLaunchd(
    parsePlistXml(readRegistryArtifact(new URL(`../../${path}`, import.meta.url), "utf8"))));
  assert.deepEqual(definitionOnly, [
    "ops/launchd/com.carr.repo-hygiene-janitor.plist",
    "ops/launchd/com.carr.resource-collector.plist",
  ]);
  assert.doesNotThrow(() =>
    validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy, definitionOnly));
  // Without the exemption the same set still refuses, so closure is intact for
  // every deployed agent and the exemption is doing exactly one thing.
  assert.throws(() => validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy),
    /catalog closure mismatch/);

  const missingService = structuredClone(services);
  const rules = missingService.services.find(service => service.key === "rules-refresh");
  rules.environments = rules.environments.filter(environment =>
    environment.deploy_mechanism !== "ops/launchd/com.carr.rules-refresh.plist");
  assert.throws(() => validateLaunchdAuthorityCatalogs(launchdPaths, missingService, legacy, definitionOnly),
    /catalog closure mismatch/);

  const orphanService = structuredClone(services);
  orphanService.services[0].environments.push({
    environment: "local", deploy_mechanism: "ops/launchd/com.carr.orphan.plist",
  });
  assert.throws(() => validateLaunchdAuthorityCatalogs(launchdPaths, orphanService, legacy, definitionOnly),
    /orphan=ops\/launchd\/com\.carr\.orphan\.plist/);

  const duplicateLegacy = structuredClone(legacy);
  duplicateLegacy.surfaces.push({
    ...duplicateLegacy.surfaces.find(surface => surface.scheduler_kind === "launchd"),
    surface_id: "duplicate.launchd.v1",
  });
  assert.throws(() => validateLaunchdAuthorityCatalogs(launchdPaths, services, duplicateLegacy, definitionOnly),
    /duplicate launchd legacy path/);

  const legacySurface = legacy.surfaces.find(surface =>
    surface.repo_plist_relpath === "ops/launchd/com.carr.rules-refresh.plist") ||
    legacy.surfaces.find(surface => surface.scheduler_kind === "launchd");
  const plist = parsePlistXml(readRegistryArtifact(
    new URL(`../../${legacySurface.repo_plist_relpath}`, import.meta.url), "utf8"));
  assert.doesNotThrow(() => assertLegacyLaunchdSource(legacySurface, legacySurface.repo_plist_relpath, plist));
  assert.throws(() => assertLegacyLaunchdSource(
    { ...legacySurface, canonical_program_arguments: [...legacySurface.canonical_program_arguments, "--forged"] },
    legacySurface.repo_plist_relpath, plist), /legacy source mismatch/);
});

test("only verb-contract changes and new write entrances hold a pull request to the frozen frontier", () => {
  // Decision 05e144eb: scripts, workflows and launchd plists never reseal;
  // worker routes reseal only when a NEW one appears; everything else is
  // compared whole.
  const base = [
    { ingress_key: "mcp-tool:add-loop", source_locator: "mcp-server/src/tools.js", source_digest: "a",
      schema_digest: "a", write: true, human_only: false, authority_only: false },
    { ingress_key: "script-entrypoint:hooks/lint-gate.py", source_digest: "a" },
    { ingress_key: "github-workflow:.github/workflows/ci.yml", source_digest: "a" },
    { ingress_key: "launchd-workflow:com.carr.nightly", source_digest: "a" },
    { ingress_key: "worker-sidewrite:tool-read-call", handler_digest: "a" },
  ];
  const digest = rows => sourceInventoryFixtureDigest(boundInventoryRows(rows));
  const edit = (key, field) => base.map(row => row.ingress_key === key ? { ...row, [field]: "b" } : row);
  assert.equal(digest(edit("script-entrypoint:hooks/lint-gate.py", "source_digest")), digest(base));
  assert.equal(digest(edit("github-workflow:.github/workflows/ci.yml", "source_digest")), digest(base));
  assert.equal(digest(edit("launchd-workflow:com.carr.nightly", "source_digest")), digest(base));
  assert.equal(digest(edit("worker-sidewrite:tool-read-call", "handler_digest")), digest(base));
  assert.equal(digest([...base, { ingress_key: "script-entrypoint:ops/new.py", source_digest: "c" }]), digest(base));
  assert.notEqual(digest([...base, { ingress_key: "worker-route:new-write", handler_digest: "c" }]), digest(base));
  assert.notEqual(digest(edit("mcp-tool:add-loop", "schema_digest")), digest(base));
  // The whole-file digest of a verb's source is not what the runtime checks.
  assert.equal(digest(edit("mcp-tool:add-loop", "source_digest")), digest(base));
  // Every flag the runtime compares still binds.
  for (const flag of ["write", "human_only", "authority_only"]) {
    const flipped = base.map(row => row.ingress_key === "mcp-tool:add-loop" ? { ...row, [flag]: !row[flag] } : row);
    assert.notEqual(digest(flipped), digest(base), `${flag} must still bind`);
  }
  assert.notEqual(digest(edit("mcp-tool:add-loop", "source_locator")), digest(base));
  assert.notEqual(digest([...base, { ingress_key: "mcp-tool:new-verb", schema_digest: "c" }]), digest(base));
});

test("the bound MCP fields are exactly what the runtime admission check compares", () => {
  // If mutation-registry.js starts comparing another field, this list must
  // grow with it, or a change the server would refuse could merge unsealed.
  const source = readRegistryArtifact(new URL("../src/mutation-registry.js", import.meta.url), "utf8");
  const body = source.slice(source.indexOf("export async function assertRegisteredOperation"));
  const actual = body.slice(body.indexOf("const actual = {"), body.indexOf("};"));
  const compared = [...actual.matchAll(/^\s+([a-z_]+):/gm)].map(match => match[1]).sort();
  assert.deepEqual(compared, MCP_TOOL_BOUND_FIELDS.filter(field => field !== "ingress_key").sort());
});

// PR 865: a source-only credential-tool edit must bind the reviewed bytes
// while preserving the sealed predecessor and all ingress authority fields.
test("credential rotation source review matches the live frontier", () => {
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS), true);
});


test("credential rotation source review cannot widen authority or admit an ingress", () => {
  const probe = `
    import assert from "node:assert/strict";
    import fs from "node:fs";
    import {
  syncBuiltinESMExports
} from "node:module";
    const read = fs.readFileSync;
    const fixturePath = "ops/config/scac-registry-source-inventory-fixtures.v1.json";
    const fixture = JSON.parse(read(fixturePath, "utf8"));
    const reviewVersion = Object.keys(fixture.current_source_reviews)
      .sort((left, right) => Number(left.split('.v')[1]) - Number(right.split('.v')[1])).at(-1);
    const review = fixture.current_source_reviews[reviewVersion];
    const variant = process.argv[1];
    const errors = {
      authority: /changed an ingress contract/,
      locator: /changed an ingress contract/,
      ingress: /unknown ingress/,
      digest: /current source-inventory review drifted/,
      base: /malformed or bound to the wrong frontier/,
      broad: /must bind each changed row explicitly/,
    };
    if (variant === "authority") review.upsert[0].authority_only = false;
    if (variant === "locator") review.upsert[0].source_locator = "other.py";
    if (variant === "ingress") review.upsert[0].ingress_key = "external-admin:new.py";
    if (variant === "digest") review.expected_sha256 = "0".repeat(64);
    if (variant === "base") review.base_version = "v103";
    if (variant === "broad") review.source_digest_replacements = {"other.py": "0".repeat(64)};
    fs.readFileSync = (path, ...args) => String(path).endsWith(fixturePath)
      ? JSON.stringify(fixture) : read(path, ...args);
    syncBuiltinESMExports();
    const { assertCurrentSourceInventoryMatchesFixture } = await import("./ops/scac-mutation-inventory.mjs");
    const { TOOLS } = await import("./mcp-server/src/tools.js");
    assert.throws(() => assertCurrentSourceInventoryMatchesFixture(TOOLS, reviewVersion), errors[variant]);
  `;
  for (const variant of ["authority", "locator", "ingress", "digest", "base", "broad"])
    execFileSync(process.execPath, ["--input-type=module", "-e", probe, variant],
      { cwd: fileURLToPath(new URL("../../", import.meta.url)), stdio: "pipe" });
});


test("Dell receipt source review binds its live bytes without changing the sealed frontier", () => {
  const fixture = JSON.parse(readRegistryArtifact(new URL(
    "../../ops/config/scac-registry-source-inventory-fixtures.v1.json", import.meta.url), "utf8"));
  const sealed = frozenInventory("scac-mutation-registry.v103");
  const key = "external-admin:bin/migrate-dell.sh";
  const review = fixture.current_source_reviews["scac-mutation-registry.v103"];
  const reviewed = review.upsert.find(row => row.ingress_key === key);
  const current = fullInventory(TOOLS).find(row => row.ingress_key === key);
  assert.deepEqual(reviewed, current);
  assert.notEqual(sealed.find(row => row.ingress_key === key).handler_digest, reviewed.handler_digest);
  const contract = row => Object.fromEntries(Object.entries(row)
    .filter(([field]) => !["schema_digest", "handler_digest", "source_digest"].includes(field)));
  assert.deepEqual(contract(reviewed), contract(sealed.find(row => row.ingress_key === key)));
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS), true);
});
