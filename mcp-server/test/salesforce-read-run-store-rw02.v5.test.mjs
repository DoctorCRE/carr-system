// V5-RW02 run-outcome ledger, counter read and consent revocation: the store
// module against a fake database that models ops.rw02_* exactly as migration
// 0733 defines them. The real SQL is proved on PostgreSQL by
// salesforce-read-run-store-rw02-postgres.test.mjs. Synthetic data only.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import * as real from "../src/salesforce-read-run-store-rw02.v5.js";

/** A fake of the 0733 functions: append-only rows, count after the last unclean run. */
export class FakeLedgerDb {
  constructor({ pageStops = [], partner = "joe", decisions = [] } = {}) {
    this.rows = []; this.pageStops = pageStops; this.partner = partner; this.decisions = decisions;
    this.revocations = []; this.queries = [];
  }
  count(kind) {
    const rows = this.rows.filter(r => r.action_kind === kind);
    let n = 0;
    for (const r of rows) n = r.outcome === "clean" ? n + 1 : 0;
    return n;
  }
  async query(sql, params = []) {
    this.queries.push(sql);
    if (sql.includes("ops.rw02_record_run(")) {
      const [action_kind, run_ref, outcome, stop_class, reason_id] = params;
      const prior = this.rows.find(r => r.action_kind === action_kind && r.run_ref === run_ref);
      if (prior) {
        if (prior.outcome !== outcome || prior.stop_class !== stop_class || prior.reason_id !== reason_id)
          throw new Error("rw02_run_outcome_conflict");
        return { rows: [{ outcome: { ...prior, replayed: true, consecutive_clean: this.count(action_kind) } }] };
      }
      if (outcome === "clean" && this.pageStops.some(k => k.startsWith(`rw02-read:${run_ref}:`)))
        throw new Error("rw02_clean_run_contradicted");
      const row = { action_kind, run_ref, outcome, stop_class, reason_id, recorded_by: "joe",
        recorded_at: "2026-09-27T00:00:00.000Z", execution_mode: "attended" };
      this.rows.push(row);
      return { rows: [{ outcome: { ...row, replayed: false, consecutive_clean: this.count(action_kind) } }] };
    }
    if (sql.includes("ops.rw02_attended_runs(")) {
      return { rows: this.rows.filter(r => r.action_kind === params[0])
        .map(({ run_ref, action_kind, outcome, execution_mode }) => ({ run_ref, action_kind, outcome, execution_mode })) };
    }
    if (sql.includes("ops.rw02_consecutive_clean(")) return { rows: [{ n: this.sqlCountOverride ?? this.count(params[0]) }] };
    if (sql.includes("ops.rw02_revoke_consent(")) {
      if (!["joe", "dell"].includes(this.partner)) throw new Error("rw02_verified_partner_required");
      if (!this.decisions.includes(params[0])) throw new Error("rw02_consent_decision_unknown");
      const row = { revocation_id: "11111111-1111-4111-8111-111111111111", decision_id: params[0],
        revoked_by: this.partner, revoked_at: "2026-09-27T00:00:00.000Z" };
      this.revocations.push(row);
      return { rows: [{ revocation: JSON.stringify(row) }] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }
}

const KIND = "presence_membership_reconciliation";
const outcome = (n, o = "clean", extra = {}) => ({ idempotency_key: `rw02-run-outcome:${String(n).padStart(24, "0")}`,
  run_ref: String(n).padStart(24, "0"), action_kind: KIND, outcome: o, ...extra });
const STOP = { stop_class: "auth_challenge", reason_id: "captcha" };

async function checkCounterResetsServerSide(mod) {
  const db = new FakeLedgerDb();
  const store = mod.createSalesforceReadRunStore({ db });
  for (const n of [1, 2, 3, 4]) await store.recordRunOutcome(outcome(n));
  const stopped = await store.recordRunOutcome(outcome(5, "stopped", STOP));
  assert.equal(stopped.counter.consecutive_clean, 0);
  assert.equal(stopped.counter.reset_by_this_run, true);
  let last;
  for (const n of [6, 7, 8, 9]) last = await store.recordRunOutcome(outcome(n));
  assert.equal(last.counter.consecutive_clean, 4);
  assert.equal(last.counter.threshold_met, false, "four clean after a reset is not five");
  const four = await store.readAutonomyCounter({ action_kind: KIND });
  assert.equal(four.consecutive_clean, 4);
  assert.equal(four.threshold_met, false);
  const five = await store.recordRunOutcome(outcome(10));
  assert.equal(five.counter.consecutive_clean, 5);
  assert.equal(five.counter.threshold_met, true);
  assert.equal(five.autonomy_active, false);
  assert.equal(five.promotion, "not_performed_in_this_slice");
  const read = await store.readAutonomyCounter({ action_kind: KIND });
  assert.equal(read.consecutive_clean, 5);
  assert.equal(read.threshold_met, true);
  for (const bad of ["failed", "refused", "corrected"]) {
    const r = await store.recordRunOutcome(outcome(`${bad}1`, bad));
    assert.equal(r.counter.consecutive_clean, 0, bad);
  }
}

async function checkStopFieldsTyped(mod) {
  const store = mod.createSalesforceReadRunStore({ db: new FakeLedgerDb() });
  const bad = [
    [outcome(1, "stopped"), "stop_class_required"],
    [outcome(1, "stopped", { stop_class: "ui_drift", reason_id: "captcha" }), "stop_reason_mismatch"],
    [outcome(1, "stopped", { stop_class: "auth_challenge", reason_id: "made_up" }), "stop_reason_mismatch"],
    [outcome(1, "clean", STOP), "stop_fields_on_unstopped_run"],
    [outcome(1, "mostly_clean"), "unknown_outcome"],
    [{ ...outcome(1), action_kind: "etl_protected_send" }, "unknown_action_kind"],
    [{ ...outcome(1), run_ref: "bad ref" }, "invalid_run_ref"],
    [{ ...outcome(1), execution_mode: "unattended" }, "unknown_field"],
    [{ ...outcome(1), consecutive_clean: 5 }, "unknown_field"],
  ];
  for (const [args, code] of bad)
    await assert.rejects(store.recordRunOutcome(args), e => e.code === code, code);
}

test("the server-side counter: 5 clean in a row meets the threshold; any unclean run resets to 0", async () => {
  await checkCounterResetsServerSide(real);
});

test("a stop names a registered class and a reason of that class; nothing else carries one", async () => {
  await checkStopFieldsTyped(real);
});

test("a run claimed clean after it recorded a page stop is refused by the record layer", async () => {
  const db = new FakeLedgerDb({ pageStops: [`rw02-read:${"7".padStart(24, "0")}:page-stop:0`] });
  const store = real.createSalesforceReadRunStore({ db });
  await assert.rejects(store.recordRunOutcome(outcome(7)), e => e.code === "clean_run_contradicted");
  assert.equal(db.rows.length, 0);
});

test("a replay answers the stored run; the same run with a different outcome is refused", async () => {
  const store = real.createSalesforceReadRunStore({ db: new FakeLedgerDb() });
  const first = await store.recordRunOutcome(outcome(3));
  const again = await store.recordRunOutcome(outcome(3));
  assert.equal(first.replayed, false);
  assert.equal(again.replayed, true);
  assert.equal(again.effects.database_writes, 0);
  await assert.rejects(store.recordRunOutcome(outcome(3, "stopped", STOP)), e => e.code === "run_outcome_conflict");
});

test("the counter read refuses when the record layer and the evaluator disagree", async () => {
  const db = new FakeLedgerDb();
  const store = real.createSalesforceReadRunStore({ db });
  await store.recordRunOutcome(outcome(1));
  db.sqlCountOverride = 3;
  await assert.rejects(store.readAutonomyCounter({ action_kind: KIND }), e => e.code === "database_contract_violation");
});

test("consent revocation: a verified partner revokes a known decision; anything else is refused", async () => {
  const decision = "bf194d7d-4b33-4683-a320-6b5a8c05766d";
  const ok = real.createSalesforceReadRunStore({ db: new FakeLedgerDb({ decisions: [decision], partner: "dell" }) });
  const r = await ok.revokeConsent({ idempotency_key: "22222222-2222-4222-8222-222222222222",
    decision_id: decision, human_quote: "I withdraw my OK for the Salesforce reads." });
  assert.equal(r.decision, "revoked");
  assert.equal(r.revoked_by, "dell");
  assert.equal(r.consent_in_force, false);
  const nobody = real.createSalesforceReadRunStore({ db: new FakeLedgerDb({ decisions: [decision], partner: null }) });
  await assert.rejects(nobody.revokeConsent({ idempotency_key: "22222222-2222-4222-8222-222222222222",
    decision_id: decision, human_quote: "stop it now" }), e => e.code === "verified_partner_required");
  const unknown = real.createSalesforceReadRunStore({ db: new FakeLedgerDb({ decisions: [] }) });
  await assert.rejects(unknown.revokeConsent({ idempotency_key: "22222222-2222-4222-8222-222222222222",
    decision_id: decision, human_quote: "stop it now" }), e => e.code === "consent_decision_unknown");
  for (const [args, code] of [
    [{ idempotency_key: "k", decision_id: decision, human_quote: "stop it now" }, "invalid_idempotency_key"],
    [{ idempotency_key: "22222222-2222-4222-8222-222222222222", decision_id: "bf194d7d", human_quote: "stop" },
      "invalid_decision_id"],
    [{ idempotency_key: "22222222-2222-4222-8222-222222222222", decision_id: decision, human_quote: "" },
      "invalid_human_quote"],
    [{ idempotency_key: "22222222-2222-4222-8222-222222222222", decision_id: decision, human_quote: "ok",
      partner: "dell" }, "unknown_field"],
  ]) await assert.rejects(ok.revokeConsent(args), e => e.code === code, code);
});

test("the three verbs are registered with their flags: one write, one read, one humanOnly write", async () => {
  const tools = real.salesforceReadRunStoreTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    ToolError: class extends Error { constructor(p) { super(p.error); this.payload = p; } } });
  assert.deepEqual(Object.keys(tools).sort(), [...real.V5_RW02_RUN_STORE_VERBS]);
  assert.equal(tools["record-salesforce-run-outcome"].write, true);
  assert.equal(tools["read-salesforce-autonomy-counter"].write, false);
  assert.equal(tools["revoke-salesforce-read-consent"].write, true);
  assert.equal(tools["revoke-salesforce-read-consent"].humanOnly, true);
  assert.equal(tools["record-salesforce-run-outcome"].humanOnly, undefined);
  await assert.rejects(tools["record-salesforce-run-outcome"].handler(new FakeLedgerDb(), {}, outcome(1, "nope")),
    e => e.payload?.error === "unknown_outcome");
});

// --- Planted bugs ----------------------------------------------------------

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const FILE = "salesforce-read-run-store-rw02.v5.js";
const WORK = mkdtempSync(join(tmpdir(), "rw02-run-store-mutants-"));
test.after(() => rmSync(WORK, { recursive: true, force: true }));
let serial = 0;
async function mutant(anchor, replacement) {
  let source = readFileSync(join(SRC, FILE), "utf8");
  assert.equal(source.split(anchor).length - 1, 1, `unique mutant anchor: ${anchor}`);
  source = source.replace(anchor, replacement).replace(/from\s+"\.\/([^"]+)"/g, (_, file) =>
    `from "${pathToFileURL(join(SRC, file)).href}"`);
  const path = join(WORK, `${++serial}.mjs`);
  writeFileSync(path, source);
  return import(pathToFileURL(path).href);
}
async function killed(check, anchor, replacement) {
  await check(real);
  await assert.rejects(Promise.resolve().then(async () => check(await mutant(anchor, replacement))));
}

test("MUTANT C1 a stopped run's reason is not checked against its class", () => killed(checkStopFieldsTyped,
  "if (V5_RW02_SAFE_STOP_REASONS[reason_id] !== stop_class) fail(", "if (false) fail("));

test("MUTANT C2 a clean run may carry stop fields", () => killed(checkStopFieldsTyped,
  "} else if (stop_class !== null || reason_id !== null) {", "} else if (false) {"));

test("MUTANT C3 the counter read trusts a reset-free count", () => killed(checkCounterResetsServerSide,
  "threshold_met: row.consecutive_clean >= V5_RW02_AUTONOMY_THRESHOLD,",
  "threshold_met: row.consecutive_clean >= V5_RW02_AUTONOMY_THRESHOLD - 1,"));
