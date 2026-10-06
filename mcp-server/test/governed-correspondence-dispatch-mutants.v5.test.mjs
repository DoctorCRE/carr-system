// V5-J103: mutants proving no draft or proposal path can dispatch.
//
// A test that is green proves nothing unless breaking the property turns it red.
// Each mutant below copies mcp-server/src into a temporary tree, applies ONE
// edit that would open a dispatch path — a draft that says it is dispatchable,
// a provider operation on a result, a destination admitted, a dispatch field
// let through, a send-shaped verb or export added, a write operation made
// consentable — and runs the suite that guards that property against the
// mutated tree. The mutant must be KILLED: the suite must exit non-zero.
//
// Every mutation is asserted to apply exactly once, so a refactor that moves the
// target text fails here loudly instead of leaving a mutant that silently
// mutates nothing and "survives" for the wrong reason. The database half has its
// own proof (governed-correspondence-store-postgres.sql); these are the
// JavaScript paths.

import test from "node:test";
import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync, mkdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");

const MUTANTS = [
  // --- the #983 kernel's draft path -----------------------------------------
  {
    id: "kernel-draft-dispatchable",
    file: "governed-correspondence.v5.js",
    find: "    dispatchable: false,\n    provider_operation: null,\n    requires_human_send: true,",
    replace: "    dispatchable: true,\n    provider_operation: null,\n    requires_human_send: true,",
    suite: "governed-correspondence.v5.test.mjs",
  },
  {
    id: "kernel-draft-provider-operation",
    file: "governed-correspondence.v5.js",
    find: "    dispatchable: false,\n    provider_operation: null,\n    requires_human_send: true,",
    replace: "    dispatchable: false,\n    provider_operation: \"send_mail_message\",\n    requires_human_send: true,",
    suite: "governed-correspondence.v5.test.mjs",
  },
  {
    id: "kernel-draft-skips-human",
    file: "governed-correspondence.v5.js",
    find: "    dispatchable: false,\n    provider_operation: null,\n    requires_human_send: true,",
    replace: "    dispatchable: false,\n    provider_operation: null,\n    requires_human_send: false,",
    suite: "governed-correspondence.v5.test.mjs",
  },
  {
    id: "kernel-admits-routable-address",
    file: "governed-correspondence.v5.js",
    find: "    if (ROUTABLE_ADDRESS.test(value)) {\n      fail(\"routable_address_refused\",",
    replace: "    if (false && ROUTABLE_ADDRESS.test(value)) {\n      fail(\"routable_address_refused\",",
    suite: "governed-correspondence.v5.test.mjs",
  },
  {
    id: "kernel-admits-dispatch-field",
    file: "governed-correspondence.v5.js",
    find: "function assertNoDispatchFields(object, path) {\n",
    replace: "function assertNoDispatchFields(object, path) {\n  return;\n",
    suite: "governed-correspondence.v5.test.mjs",
  },
  {
    id: "kernel-imports-a-network-capability",
    file: "governed-correspondence.v5.js",
    find: "import { canonicalJson, digest } from \"./artifact-trust.js\";\n",
    replace: "import { canonicalJson, digest } from \"./artifact-trust.js\";\nimport \"./google-oidc.js\";\n",
    suite: "governed-correspondence.v5.test.mjs",
  },
  // --- the c4..c8 proposal paths --------------------------------------------
  {
    id: "journey-result-dispatchable",
    file: "governed-correspondence-journey.v5.js",
    find: "  dispatchable: false,\n  provider_operation: null,",
    replace: "  dispatchable: true,\n  provider_operation: null,",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "journey-result-provider-operation",
    file: "governed-correspondence-journey.v5.js",
    find: "  dispatchable: false,\n  provider_operation: null,",
    replace: "  dispatchable: false,\n  provider_operation: \"send_mail_message\",",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "journey-automatic-internal-update",
    file: "governed-correspondence-journey.v5.js",
    find: "  automatic_internal_update: false,\n  internal_update_gate: {",
    replace: "  automatic_internal_update: true,\n  internal_update_gate: {",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "journey-admits-dispatch-field",
    file: "governed-correspondence-journey.v5.js",
    find: "      if (dispatch) fail(\"dispatch_field_refused\"",
    replace: "      if (false && dispatch) fail(\"dispatch_field_refused\"",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "journey-admits-routable-address",
    file: "governed-correspondence-journey.v5.js",
    find: "    if (ROUTABLE_ADDRESS.test(value)) {\n      fail(\"routable_address_refused\"",
    replace: "    if (false && ROUTABLE_ADDRESS.test(value)) {\n      fail(\"routable_address_refused\"",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "journey-exports-a-send",
    file: "governed-correspondence-journey.v5.js",
    find: "export function evaluateConsumerCircuit(request) {",
    replace: "export function sendProposal(p) { return p; }\nexport function evaluateConsumerCircuit(request) {",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "journey-confirms-document",
    file: "governed-correspondence-journey.v5.js",
    find: "      document_state: \"document_pending\",\n      basis: signal.signal_basis,",
    replace: "      document_state: route === \"route_to_lifecycle_confirmation\" ? \"document_confirmed\" : \"document_pending\",\n      basis: signal.signal_basis,",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "journey-ceiling-stamped-before-body",
    file: "governed-correspondence-journey.v5.js",
    find: "    ...body,\n    ...CEILING,\n",
    replace: "    ...CEILING,\n    ...body,\n",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "journey-touch-ignores-status",
    file: "governed-correspondence-journey.v5.js",
    find: "  if (temporality === \"in_progress\" || ev.status === \"cancelled\") return null;\n  if (temporality === \"past\" && ev.status !== \"confirmed\") return null;",
    replace: "  if (temporality === \"in_progress\") return null;",
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  // --- the store's verbs ------------------------------------------------------
  {
    id: "store-registers-a-send-verb",
    file: "governed-correspondence-store.v5.js",
    find: "    \"correspondence-readiness\": {\n      write: false,",
    replace: "    \"send-correspondence-draft\": { write: true, description: \"x\", inputSchema: { type: \"object\", additionalProperties: false, properties: {} }, handler: async () => ({}) },\n    \"correspondence-readiness\": {\n      write: false,",
    suite: "governed-correspondence-store.v5.test.mjs",
  },
  {
    id: "store-consents-a-write-operation",
    file: "governed-correspondence-store.v5.js",
    find: "            if (!V5_F10_READ_OPERATIONS.includes(op)) {",
    replace: "            if (false && !V5_F10_READ_OPERATIONS.includes(op)) {",
    suite: "governed-correspondence-store.v5.test.mjs",
  },
  {
    id: "store-schema-takes-a-recipient",
    file: "governed-correspondence-store.v5.js",
    find: "          consent_id: { type: \"string\" },\n          human_quote: { type: \"string\" },",
    replace: "          consent_id: { type: \"string\" },\n          recipient: { type: \"string\" },\n          human_quote: { type: \"string\" },",
    suite: "governed-correspondence-store.v5.test.mjs",
  },
  {
    id: "journey-past-boundary-strict",
    file: "governed-correspondence-journey.v5.js",
    find: '  const temporality = start > now ? "scheduled" : end <= now ? "past" : "in_progress";',
    replace: '  const temporality = start > now ? "scheduled" : end < now ? "past" : "in_progress";',
    suite: "governed-correspondence-journey.v5.test.mjs",
  },
  {
    id: "store-consent-reverts-to-deny-list",
    file: "governed-correspondence-store.v5.js",
    find: "        if (!partnerOwnsMailbox(partner, args.account)) {",
    replace: "        if (Object.entries(V5_J103_PARTNER_MAILBOXES).some(([p, l]) => p !== partner && l.includes(String(args.account).trim().toLowerCase()))) {",
    suite: "governed-correspondence-store.v5.test.mjs",
  },
  {
    id: "store-consent-drops-partner-from-pair",
    file: "governed-correspondence-store.v5.js",
    find: "  const list = Object.hasOwn(V5_J103_PARTNER_MAILBOXES, partner) ? V5_J103_PARTNER_MAILBOXES[partner] : [];",
    replace: "  const list = Object.values(V5_J103_PARTNER_MAILBOXES).flat();",
    suite: "governed-correspondence-store.v5.test.mjs",
  },
  {
    id: "store-consent-check-removed",
    file: "governed-correspondence-store.v5.js",
    find: "        if (!partnerOwnsMailbox(partner, args.account)) {",
    replace: "        if (false) {",
    suite: "governed-correspondence-store.v5.test.mjs",
  },
  {
    id: "store-read-marks-dispatchable",
    file: "governed-correspondence-store.v5.js",
    find: "const CEILING = Object.freeze({\n  dispatchable: false,",
    replace: "const CEILING = Object.freeze({\n  dispatchable: true,",
    suite: "governed-correspondence-store.v5.test.mjs",
  },
];

// A `node --test` spawned from inside a `node --test` run inherits
// NODE_TEST_CONTEXT and reports to its parent instead of exiting non-zero, so a
// failing mutated suite would look green. The child gets a clean environment.
function childEnv() {
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  return env;
}

function applyOnce(text, find, replace, id) {
  const first = text.indexOf(find);
  assert.notEqual(first, -1, `mutant ${id}: target text not found; the mutant would mutate nothing`);
  assert.equal(text.indexOf(find, first + 1), -1, `mutant ${id}: target text is not unique`);
  return text.slice(0, first) + replace + text.slice(first + find.length);
}

function runMutant(m) {
  const dir = mkdtempSync(join(tmpdir(), `j103-mutant-${m.id}-`));
  try {
    cpSync(join(ROOT, "src"), join(dir, "src"), { recursive: true });
    mkdirSync(join(dir, "test"));
    cpSync(join(ROOT, "test", m.suite), join(dir, "test", m.suite));
    cpSync(join(ROOT, "package.json"), join(dir, "package.json"));
    symlinkSync(join(ROOT, "node_modules"), join(dir, "node_modules"));
    const target = join(dir, "src", m.file);
    writeFileSync(target, applyOnce(readFileSync(target, "utf8"), m.find, m.replace, m.id));
    return spawnSync(process.execPath, ["--test", join("test", m.suite)], {
      cwd: dir, encoding: "utf8", timeout: 120_000, env: childEnv(),
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("the unmutated tree is green for every guarding suite", () => {
  for (const suite of new Set(MUTANTS.map(m => m.suite))) {
    const run = spawnSync(process.execPath, ["--test", join("test", suite)], { cwd: ROOT, encoding: "utf8", timeout: 120_000, env: childEnv() });
    assert.equal(run.status, 0, `${suite} is red before any mutation:\n${run.stdout.slice(-2000)}`);
  }
});

for (const m of MUTANTS) {
  test(`mutant ${m.id} is killed by ${m.suite}`, () => {
    const run = runMutant(m);
    assert.notEqual(run.status, 0, `mutant ${m.id} SURVIVED: ${m.suite} stayed green with a dispatch path opened`);
  });
}
