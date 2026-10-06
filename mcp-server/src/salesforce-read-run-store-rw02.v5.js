// DoctorCRE v5 V5-RW02 — the server-side half of the safe-stop slice.
//
// Three verbs over the ledgers migration 0733 installs, and no others:
//
//   record-salesforce-run-outcome   write      one attended run's outcome, per
//                                              kind; the consecutive-clean count
//                                              is derived from these rows
//   read-salesforce-autonomy-counter read      the count for one kind, from the
//                                              stored history
//   revoke-salesforce-read-consent  humanOnly  a partner withdraws the consent
//                                              decision Dell-account reads need
//
// THE COUNTER LIVES ON THE SERVER (decision 493de438). A kind of update may be
// considered for autonomy only after 5 CONSECUTIVE clean attended runs of that
// kind; ANY run that is not clean resets the count to zero. The rows are
// append-only, one per (kind, run), and the count is computed from them by the
// record layer and again here with the adapter's pure evaluator; a mismatch
// refuses the read. A run claimed clean after it recorded a page stop is
// refused by the record layer. Meeting the threshold promotes nothing.
//
// A REVOCATION IS A RECORD, NOT A FLAG. The adapter re-reads the consent
// decision on every run through ops.rw02_consent_record; a revocation row for
// that decision refuses the next run before the browser is touched. The
// partner who revokes is the verified human the server sets for a humanOnly
// act, never an argument.

import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_RW02_AUTONOMY_KINDS,
  V5_RW02_AUTONOMY_THRESHOLD,
  V5_RW02_RUN_OUTCOMES,
  V5_RW02_SAFE_STOP_CLASSES,
  V5_RW02_SAFE_STOP_REASONS,
  evaluateAutonomyCounter,
} from "./salesforce-browser-read-rw02.v5.js";

export const V5_RW02_RUN_STORE_SCHEMA_VERSION = "doctorcre-v5-rw02-run-store.v1";

export const V5_RW02_RUN_STORE_VERBS = Object.freeze([
  "read-salesforce-autonomy-counter",
  "record-salesforce-run-outcome",
  "revoke-salesforce-read-consent",
]);

const RUN_REF = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$/;
const KEY = /^[A-Za-z0-9][A-Za-z0-9._:+-]{7,199}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UNSAFE_TEXT = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁩﻿]/u;

export class V5RW02RunStoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5RW02RunStoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) { throw new V5RW02RunStoreError(code, message, detail); }

function plain(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}
function freeze(value) {
  if (Array.isArray(value)) { value.forEach(freeze); return Object.freeze(value); }
  if (plain(value)) { Object.values(value).forEach(freeze); return Object.freeze(value); }
  return value;
}
function closedArgs(args, allowed, required) {
  if (!plain(args)) fail("invalid_shape", "arguments must be an object");
  for (const key of Object.keys(args))
    if (!allowed.includes(key)) fail("unknown_field", "unknown argument", { allowed: [...allowed] });
  for (const key of required)
    if (!(key in args)) fail("missing_field", `${key} is required`, { path: key });
  return args;
}
function one(result, name) {
  if (!result || !Array.isArray(result.rows) || result.rows.length !== 1)
    fail("database_contract_violation", `${name} must return exactly one row`);
  return result.rows[0];
}
function json(value) { return typeof value === "string" ? JSON.parse(value) : value; }

const SQL_REFUSALS = Object.freeze({
  rw02_actor_unknown: "unauthenticated_actor",
  rw02_run_outcome_conflict: "run_outcome_conflict",
  rw02_clean_run_contradicted: "clean_run_contradicted",
  rw02_verified_partner_required: "verified_partner_required",
  rw02_revocation_conflict: "revocation_conflict",
  rw02_consent_decision_unknown: "consent_decision_unknown",
});
function typedSqlRefusal(error) {
  const text = `${error?.message ?? ""}`;
  for (const [marker, code] of Object.entries(SQL_REFUSALS))
    if (text.includes(marker)) return new V5RW02RunStoreError(code, "the record layer refused this RW02 write",
      { sql_refusal: marker });
  return null;
}
async function guarded(fn) {
  try { return await fn(); }
  catch (error) {
    if (error instanceof V5RW02RunStoreError) throw error;
    throw typedSqlRefusal(error) ?? error;
  }
}

/** Pure validation of one run outcome. Returns the normalized fields. */
export function normalizeRunOutcome(args) {
  closedArgs(args, ["idempotency_key", "run_ref", "action_kind", "outcome", "stop_class", "reason_id"],
    ["idempotency_key", "run_ref", "action_kind", "outcome"]);
  const { run_ref, action_kind, outcome } = args;
  if (typeof args.idempotency_key !== "string" || !KEY.test(args.idempotency_key))
    fail("invalid_idempotency_key", "idempotency_key must be 8-200 stable characters");
  if (typeof run_ref !== "string" || !RUN_REF.test(run_ref)) fail("invalid_run_ref", "run_ref must be a stable id");
  if (!V5_RW02_AUTONOMY_KINDS.includes(action_kind)) fail("unknown_action_kind", "action_kind is not registered",
    { registered: [...V5_RW02_AUTONOMY_KINDS] });
  if (!V5_RW02_RUN_OUTCOMES.includes(outcome)) fail("unknown_outcome", "outcome is not registered",
    { registered: [...V5_RW02_RUN_OUTCOMES] });
  const stop_class = args.stop_class ?? null;
  const reason_id = args.reason_id ?? null;
  if (outcome === "stopped") {
    if (!V5_RW02_SAFE_STOP_CLASSES.includes(stop_class)) fail("stop_class_required",
      "a stopped run names a registered stop class");
    if (V5_RW02_SAFE_STOP_REASONS[reason_id] !== stop_class) fail("stop_reason_mismatch",
      "a stopped run names a registered reason of that class");
  } else if (stop_class !== null || reason_id !== null) {
    fail("stop_fields_on_unstopped_run", "only a stopped run carries a stop class and reason");
  }
  return { run_ref, action_kind, outcome, stop_class, reason_id };
}

export function createSalesforceReadRunStore({ db } = {}) {
  if (!db || typeof db.query !== "function") fail("database_unavailable", "a database handle is required");

  async function recordRunOutcome(args) {
    const run = normalizeRunOutcome(args ?? {});
    return guarded(async () => {
      const row = json(one(await db.query(
        "select ops.rw02_record_run($1::text,$2::text,$3::text,$4::text,$5::text) as outcome",
        [run.action_kind, run.run_ref, run.outcome, run.stop_class, run.reason_id]), "rw02 record run").outcome);
      if (!plain(row) || row.run_ref !== run.run_ref || row.action_kind !== run.action_kind ||
          row.outcome !== run.outcome || !Number.isSafeInteger(row.consecutive_clean))
        fail("database_contract_violation", "the stored run does not match the request");
      return freeze({ schema_version: V5_RW02_RUN_STORE_SCHEMA_VERSION, ok: true, decision: "recorded",
        run: { run_ref: row.run_ref, action_kind: row.action_kind, outcome: row.outcome,
          stop_class: row.stop_class ?? null, reason_id: row.reason_id ?? null, recorded_by: row.recorded_by,
          recorded_at: row.recorded_at },
        replayed: row.replayed === true,
        counter: { action_kind: row.action_kind, consecutive_clean: row.consecutive_clean,
          threshold: V5_RW02_AUTONOMY_THRESHOLD,
          threshold_met: row.consecutive_clean >= V5_RW02_AUTONOMY_THRESHOLD,
          reset_by_this_run: row.outcome !== "clean" },
        promotion: "not_performed_in_this_slice", autonomy_active: false,
        effects: { ...V5_NO_EFFECTS, database_writes: row.replayed === true ? 0 : 1 } });
    });
  }

  async function readAutonomyCounter(args) {
    closedArgs(args ?? {}, ["action_kind"], ["action_kind"]);
    const { action_kind } = args;
    if (!V5_RW02_AUTONOMY_KINDS.includes(action_kind)) fail("unknown_action_kind", "action_kind is not registered",
      { registered: [...V5_RW02_AUTONOMY_KINDS] });
    const runs = (await db.query(
      "select run_ref, action_kind, outcome, execution_mode from ops.rw02_attended_runs($1::text)",
      [action_kind])).rows ?? [];
    const sqlCount = Number(one(await db.query("select ops.rw02_consecutive_clean($1::text) as n",
      [action_kind]), "rw02 consecutive clean").n);
    const reading = evaluateAutonomyCounter({ action_kind,
      runs: runs.map(r => ({ run_ref: r.run_ref, action_kind: r.action_kind, outcome: r.outcome,
        execution_mode: r.execution_mode })) });
    if (reading.consecutive_clean !== sqlCount) fail("database_contract_violation",
      "the record layer and the evaluator disagree on the count");
    return freeze({ schema_version: V5_RW02_RUN_STORE_SCHEMA_VERSION, ok: true, ...reading,
      runs_recorded: runs.length, source: "ops.rw02_attended_run", effects: V5_NO_EFFECTS });
  }

  async function revokeConsent(args) {
    closedArgs(args ?? {}, ["idempotency_key", "decision_id", "human_quote"],
      ["idempotency_key", "decision_id", "human_quote"]);
    if (typeof args.idempotency_key !== "string" || !UUID.test(args.idempotency_key))
      fail("invalid_idempotency_key", "idempotency_key must be a uuid");
    if (typeof args.decision_id !== "string" || !UUID.test(args.decision_id))
      fail("invalid_decision_id", "decision_id must be the consent decision's uuid");
    const quote = args.human_quote;
    if (typeof quote !== "string" || quote.trim().length < 3 || quote.length > 2000 || UNSAFE_TEXT.test(quote))
      fail("invalid_human_quote", "human_quote is the partner's literal words, 3-2000 characters");
    return guarded(async () => {
      const row = json(one(await db.query(
        "select ops.rw02_revoke_consent($1::uuid,$2::text,$3::uuid) as revocation",
        [args.decision_id, quote, args.idempotency_key]), "rw02 revoke consent").revocation);
      if (!plain(row) || row.decision_id !== args.decision_id.toLowerCase())
        fail("database_contract_violation", "the stored revocation does not match the request");
      return freeze({ schema_version: V5_RW02_RUN_STORE_SCHEMA_VERSION, ok: true, decision: "revoked",
        revocation_id: row.revocation_id, decision_id: row.decision_id, revoked_by: row.revoked_by,
        revoked_at: row.revoked_at, consent_in_force: false,
        next_run: "refused before the browser is touched (dell_consent_revoked)",
        effects: { ...V5_NO_EFFECTS, database_writes: 1 } });
    });
  }

  return freeze({ recordRunOutcome, readAutonomyCounter, revokeConsent });
}

export function salesforceReadRunStoreTools({ withEnvelope, ToolError,
  createStore = createSalesforceReadRunStore } = {}) {
  if (typeof withEnvelope !== "function" || typeof ToolError !== "function")
    fail("tool_wiring_incomplete", "shared tool wiring is required");
  const run = async (client, method, args) => {
    try { return await createStore({ db: client })[method](args ?? {}); }
    catch (error) {
      if (error instanceof V5RW02RunStoreError) throw new ToolError({ error: error.code,
        message: error.message, detail: error.detail });
      throw error;
    }
  };
  return {
    "record-salesforce-run-outcome": {
      write: true,
      description: "record-salesforce-run-outcome: V5-RW02 records ONE attended Salesforce run's outcome for one kind (clean, corrected, failed, refused, stopped; a stop names its registered class and reason). The consecutive-clean count per kind is derived from these append-only rows and any run that is not clean resets it (decision 493de438). A run claimed clean after it recorded a page stop is refused. Promotes nothing; no provider call.",
      inputSchema: { type: "object", additionalProperties: false,
        properties: { idempotency_key: { type: "string" }, run_ref: { type: "string" },
          action_kind: { type: "string", enum: [...V5_RW02_AUTONOMY_KINDS] },
          outcome: { type: "string", enum: [...V5_RW02_RUN_OUTCOMES] },
          stop_class: { type: "string", enum: [...V5_RW02_SAFE_STOP_CLASSES] },
          reason_id: { type: "string" } },
        required: ["idempotency_key", "run_ref", "action_kind", "outcome"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-salesforce-run-outcome", args,
        () => run(c, "recordRunOutcome", args)),
    },
    "read-salesforce-autonomy-counter": {
      write: false,
      description: "read-salesforce-autonomy-counter: V5-RW02 consecutive clean attended runs for one kind, read from the server-side run ledger (decision 493de438: 5 in a row makes a kind eligible for a human promotion review; any unclean run resets to 0). Read-only; grants nothing.",
      inputSchema: { type: "object", additionalProperties: false,
        properties: { action_kind: { type: "string", enum: [...V5_RW02_AUTONOMY_KINDS] } },
        required: ["action_kind"] },
      handler: async (c, _actor, args) => run(c, "readAutonomyCounter", args),
    },
    "revoke-salesforce-read-consent": {
      write: true, humanOnly: true,
      description: "HUMAN-ONLY. Withdraw the partner consent decision that V5-RW02's browser reads of Dell's Salesforce depend on. The partner is the verified human the server sets for this act, never an argument; human_quote is your literal words. Append-only and final for that decision: the next read refuses before the browser is touched. To resume, log a new consent decision and have it pinned in review.",
      inputSchema: { type: "object", additionalProperties: false,
        properties: { idempotency_key: { type: "string" }, decision_id: { type: "string" },
          human_quote: { type: "string" } },
        required: ["idempotency_key", "decision_id", "human_quote"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "revoke-salesforce-read-consent", args,
        () => run(c, "revokeConsent", args)),
    },
  };
}
