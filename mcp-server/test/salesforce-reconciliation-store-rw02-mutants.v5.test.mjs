// One planted bug per catalog guard. Each mutant is loaded from a scratch copy.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const FILE = "salesforce-reconciliation-store-rw02.v5.js";
const WORK = mkdtempSync(join(tmpdir(), "rw02-store-mutants-"));
test.after(() => rmSync(WORK, { recursive: true, force: true }));
let serial = 0;

function relink(source) {
  return source.replace(/from\s+"\.\/([^"]+)"/g, (_, file) =>
    `from "${pathToFileURL(join(SRC, file)).href}"`);
}
async function mutant(anchor, replacement) {
  const source = readFileSync(join(SRC, FILE), "utf8");
  assert.equal(source.split(anchor).length - 1, 1, `unique mutant anchor: ${anchor}`);
  const path = join(WORK, `${++serial}.mjs`);
  writeFileSync(path, relink(source.replace(anchor, replacement)));
  return import(pathToFileURL(path).href);
}

class Db {
  constructor(evidence = []) { this.evidence = evidence; this.writes = 0; this.digests = []; }
  async query(text, params = []) {
    if (["BEGIN", "COMMIT", "ROLLBACK"].includes(text)) return { rows: [] };
    if (text.includes("ops.f01_principal()")) return { rows: [{ principal: {
      actor_slug: "joe", human: true, authorization_class: "verified_partner" },
      server_now: "2026-09-26T05:00:00.000Z" }] };
    if (text.includes("ops.rw02_replay(")) return { rows: [{ outcome: null }] };
    if (text.includes("ops.rw02_action_evidence(")) return { rows: this.evidence.map(record => ({ record })) };
    if (text.includes("ops.rw02_record(")) { this.writes++; this.digests.push(params[2]);
      return { rows: [{ outcome: { ...JSON.parse(params[5]), operation: params[0], actor_slug: "joe",
        record_digest: `sha256:${"a".repeat(64)}`, action_kind: params[3], step_key: params[4] } }] }; }
    throw new Error(text);
  }
}
const ctx = { actor: { slug: "joe", human: true, via: "oauth-google" } };
const cleanPage = { execution_mode: "attended",
  binding: { expected_origin: "https://synthetic.invalid", expected_org_id: "00Dsynthetic0001",
    expected_account_ref: "seat", expected_ui_contract_digest: `sha256:${"1".repeat(64)}` },
  observation: { origin: "https://synthetic.invalid", org_id: "00Dsynthetic0001",
    signed_in_account_ref: "seat", ui_contract_digest: `sha256:${"1".repeat(64)}`,
    challenge: "none", result_consistency: "consistent" } };

test("MUTANT CD1: deleting the stop-only guard makes a clean page writable", async () => {
  const mod = await mutant(
    'if (evaluation.decision !== "stop") fail("page_not_stopped",',
    'if (false) fail("page_not_stopped",',
  );
  const db = new Db();
  await mod.createSalesforceReconciliationStore({ db }).recordPageStop(
    { idempotency_key: "mutant-page", page: cleanPage }, ctx);
  assert.equal(db.writes, 1, "the planted bug must make the forbidden write observable");
});

test("MUTANT CD2: dropping payload from request digest collapses distinct observations", async () => {
  const mod = await mutant(
    'return digest({ schema_version: V5_RW02_STORE_SCHEMA_VERSION, operation, actor_slug, payload });',
    'return digest({ schema_version: V5_RW02_STORE_SCHEMA_VERSION, operation, actor_slug });',
  );
  const db = new Db();
  const store = mod.createSalesforceReconciliationStore({ db });
  const base = { tenant: "carr-internal", case: { workflow_ref: "case-1", deal_ref: "deal-1" },
    intent_ordinal: 1, page: cleanPage };
  await store.recordDuplicateCheck({ ...base, idempotency_key: "mutant-a",
    search: { completeness: "complete", candidates: [] } }, ctx);
  await store.recordDuplicateCheck({ ...base, idempotency_key: "mutant-b",
    search: { completeness: "truncated", candidates: [] } }, ctx);
  assert.equal(db.digests[0], db.digests[1], "the planted bug must collapse the replay identity");
});

test("MUTANT CD3: deleting the foreign-action row guard lets mixed evidence reach the window evaluator", async () => {
  const mod = await mutant(
    'if (record.action_kind !== action_kind) fail("foreign_action_evidence",',
    'if (false) fail("foreign_action_evidence",',
  );
  const db = new Db([{ action_kind: "opportunity_phase_update" }]);
  let called = false;
  const store = mod.createSalesforceReconciliationStore({ db, evaluators: {
    evaluateActionTrustWindow: () => { called = true; return { decision: "window_read", autonomy_active: false }; },
  } });
  await store.readActionEvidence({ action_kind: "opportunity_create" }, ctx);
  assert.equal(called, true, "the planted bug must pass foreign evidence to the evaluator");
});
