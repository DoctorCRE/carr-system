// DoctorCRE v5 V5-RW02 — durable runtime evidence around the reviewed pure
// reconciliation kernel. This module records observations and decisions; it
// never contacts Salesforce and never grants an outward effect.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_RW02_ACTION_KIND_KEYS,
  evaluateActionTrustWindow,
  evaluateDuplicateSearch,
  evaluatePageObservation,
  evaluateWriteReadback,
} from "./salesforce-reconciliation-rw02.v5.js";

export const V5_RW02_STORE_SCHEMA_VERSION = "doctorcre-v5-rw02-runtime-store.v1";
export const V5_RW02_STORE_OPERATIONS = Object.freeze([
  "record-salesforce-page-stop",
  "record-salesforce-duplicate-check",
  "record-salesforce-write-readback",
  "read-salesforce-action-evidence",
]);

export class V5RW02StoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5RW02StoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) { throw new V5RW02StoreError(code, message, detail); }
function plain(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}
function object(value, path) {
  if (!plain(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}
function freeze(value) {
  if (Array.isArray(value)) { value.forEach(freeze); return Object.freeze(value); }
  if (plain(value)) { Object.values(value).forEach(freeze); return Object.freeze(value); }
  return value;
}

const DERIVED = Object.freeze(["tenant", "actor_slug", "recorded_at", "server_now", "decision",
  "reason_id", "record_digest", "request_digest", "evidence", "evaluation"]);

function closed(value, allowed, required, path = "payload") {
  const raw = object(value, path);
  for (const key of Object.keys(raw)) {
    if (allowed.includes(key)) continue;
    if (DERIVED.includes(key.toLowerCase())) fail("caller_derived_field_refused",
      `${path}.${key} is derived by the handler or record layer`, { path: `${path}.${key}` });
    fail("unknown_field", `unknown field at ${path}`, { path, allowed });
  }
  for (const key of required) if (!(key in raw)) fail("missing_field", `${path}.${key} is required`,
    { path: `${path}.${key}` });
  return raw;
}

function idempotency(value) {
  if (typeof value !== "string" || value.length < 8 || value.length > 200)
    fail("invalid_idempotency_key", "payload.idempotency_key must be 8-200 characters",
      { path: "payload.idempotency_key" });
  return value;
}

function principal(context) {
  const actor = object(context?.actor, "context.actor");
  if (typeof actor.slug !== "string" || !actor.slug || typeof actor.human !== "boolean")
    fail("unauthenticated_actor", "an authenticated actor is required");
  return actor;
}

function requestDigest(operation, actor_slug, payload) {
  return digest({ schema_version: V5_RW02_STORE_SCHEMA_VERSION, operation, actor_slug, payload });
}

function effects(writes) {
  return freeze({ ...V5_NO_EFFECTS, database_writes: writes ? "rw02_record_layer_rows_only" : 0 });
}

function requireDb(db) {
  if (!db || typeof db.query !== "function") fail("database_unavailable", "a database handle is required");
  return db;
}

function one(result, name) {
  if (!result || !Array.isArray(result.rows) || result.rows.length !== 1)
    fail("database_contract_violation", `${name} must return exactly one row`);
  return result.rows[0];
}

export function createSalesforceReconciliationStore({ db, evaluators = {} } = {}) {
  const handle = requireDb(db);
  const judge = {
    evaluatePageObservation,
    evaluateDuplicateSearch,
    evaluateWriteReadback,
    evaluateActionTrustWindow,
    ...evaluators,
  };

  async function transaction(fn) {
    if (typeof handle.transaction === "function") return handle.transaction(fn);
    await handle.query("BEGIN");
    try {
      const answer = await fn(handle);
      await handle.query("COMMIT");
      return answer;
    } catch (error) {
      try { await handle.query("ROLLBACK"); } catch { /* preserve original */ }
      throw error;
    }
  }

  // The reader bundle's fixed database identity (ops.f01_context_actor_slug,
  // 0626/0732). A read verb runs on the reader connection, where the database
  // cannot know the handler's actor and always answers this slug; the handler
  // actor is then the server-authenticated one. A WRITE must still match the
  // database actor exactly, and the reader slug never satisfies a write.
  const READER_PRINCIPAL = "carr-reader";

  async function open(client, actor, { read = false } = {}) {
    const row = one(await client.query(
      "SELECT ops.f01_principal() AS principal, ops.f01_now_text() AS server_now"), "rw02 principal");
    const dbPrincipal = typeof row.principal === "string" ? JSON.parse(row.principal) : row.principal;
    const dbActor = dbPrincipal?.actor_slug;
    const admitted = dbActor === actor.slug || (read && dbActor === READER_PRINCIPAL);
    if (!admitted) fail("actor_context_mismatch",
      "database and handler actor do not match", { handler_actor: actor.slug,
        database_actor: dbPrincipal?.actor_slug ?? null });
    return row.server_now;
  }

  // ONE projection for the first call and every replay, read off the row the
  // record layer stored, so a replay answers exactly what the first call did.
  // Only `replayed` and the write count differ, because a replay writes nothing.
  function recorded(stored, replayed) {
    if (!plain(stored) || !plain(stored.evaluation) || typeof stored.record_digest !== "string")
      fail("database_contract_violation", "stored RW02 outcome is malformed");
    return freeze({ schema_version: V5_RW02_STORE_SCHEMA_VERSION, operation: stored.operation,
      decision: "recorded", reason_id: "runtime_observation_recorded",
      actor_slug: stored.actor_slug, recorded_at: stored.recorded_at,
      record_digest: stored.record_digest, action_kind: stored.action_kind ?? null,
      step_key: stored.step_key ?? null, evaluation: stored.evaluation,
      replayed, effects: effects(!replayed) });
  }

  // A refusal the record layer raises by name becomes the same typed store
  // error a handler refusal would be, never a raw database exception.
  const SQL_REFUSALS = Object.freeze({
    rw02_idempotency_conflict: "idempotency_conflict",
    rw02_evidence_binding_mismatch: "evidence_binding_mismatch",
    rw02_evidence_invalid: "evidence_invalid",
    rw02_evidence_counted_twice: "evidence_counted_twice",
    rw02_runtime_record_evidence_once: "evidence_counted_twice",
    rw02_runtime_record_success_effect_once: "evidence_counted_twice",
    rw02_duplicate_binding_invalid: "duplicate_binding_invalid",
  });
  function typedSqlRefusal(error) {
    const text = `${error?.message ?? ""} ${error?.constraint ?? ""}`;
    for (const [marker, code] of Object.entries(SQL_REFUSALS))
      if (text.includes(marker)) return new V5RW02StoreError(code,
        "the record layer refused this RW02 write", { sql_refusal: marker });
    return null;
  }

  async function persist(operation, payload, actor, evaluation, action_kind = null, step_key = null) {
    const request_digest = requestDigest(operation, actor.slug, payload);
    try {
      return await transaction(async client => {
        const recorded_at = await open(client, actor);
        const replay = one(await client.query(
          "SELECT ops.rw02_replay($1::text,$2::text,$3::text) AS outcome",
          [operation, payload.idempotency_key, request_digest]), "rw02 replay").outcome;
        if (replay?.conflict === true) fail("idempotency_conflict",
          "this idempotency_key was already used for a different RW02 request",
          { path: "payload.idempotency_key" });
        if (replay) return recorded(replay, true);
        const stored = one(await client.query(
          "SELECT ops.rw02_record($1::text,$2::text,$3::text,$4::text,$5::text,$6::jsonb) AS outcome",
          [operation, payload.idempotency_key, request_digest, action_kind, step_key,
            JSON.stringify({ schema_version: V5_RW02_STORE_SCHEMA_VERSION, operation,
              tenant: ORGANIZATION_TENANT_ID, actor_slug: actor.slug, recorded_at, evaluation })]),
        "rw02 record").outcome;
        return recorded(stored, false);
      });
    } catch (error) {
      if (error instanceof V5RW02StoreError) throw error;
      throw typedSqlRefusal(error) ?? error;
    }
  }

  async function recordPageStop(input, context) {
    const payload = closed(input, ["idempotency_key", "page"], ["idempotency_key", "page"]);
    idempotency(payload.idempotency_key);
    const actor = principal(context);
    const evaluation = judge.evaluatePageObservation(payload.page);
    if (evaluation.decision !== "stop") fail("page_not_stopped",
      "only a fail-closed page stop is a durable RW02 page record", { decision: evaluation.decision });
    return persist("record-salesforce-page-stop", payload, actor, evaluation);
  }

  async function recordDuplicateCheck(input, context) {
    const payload = closed(input, ["idempotency_key", "tenant", "case", "intent_ordinal", "page", "search"],
      ["idempotency_key", "tenant", "case", "intent_ordinal", "page", "search"]);
    idempotency(payload.idempotency_key);
    const actor = principal(context);
    const { idempotency_key: _key, ...observation } = payload;
    const evaluation = judge.evaluateDuplicateSearch(observation);
    return persist("record-salesforce-duplicate-check", payload, actor, evaluation,
      "opportunity_create", evaluation.step_key ?? null);
  }

  async function recordWriteReadback(input, context) {
    const payload = closed(input, ["idempotency_key", "observation"], ["idempotency_key", "observation"]);
    idempotency(payload.idempotency_key);
    const actor = principal(context);
    const evaluation = judge.evaluateWriteReadback(payload.observation);
    if (!plain(evaluation.evidence)) fail("readback_evidence_absent",
      "only a readback decision carrying sealed per-action evidence can be recorded",
      { decision: evaluation.decision, reason_id: evaluation.reason_id });
    if (evaluation.evidence.action_kind !== evaluation.action_kind ||
        evaluation.evidence.step_key !== evaluation.step_key) fail("evidence_binding_mismatch",
      "readback evidence is not bound to its evaluated action and step");
    return persist("record-salesforce-write-readback", payload, actor, evaluation,
      evaluation.action_kind, evaluation.step_key);
  }

  async function readActionEvidence(input, context) {
    const payload = closed(input, ["action_kind"], ["action_kind"]);
    const actor = principal(context);
    const action_kind = payload.action_kind;
    if (!V5_RW02_ACTION_KIND_KEYS.includes(action_kind)) fail("unknown_action_kind",
      "payload.action_kind is not registered", { path: "payload.action_kind" });
    return transaction(async client => {
      await open(client, actor, { read: true });
      const result = await client.query("SELECT record FROM ops.rw02_action_evidence($1::text)",
        [action_kind]);
      const evidence = (result.rows ?? []).map((row, index) => {
        const record = typeof row.record === "string" ? JSON.parse(row.record) : row.record;
        if (!plain(record)) fail("database_contract_violation", "stored evidence row is malformed", { index });
        if (record.action_kind !== action_kind) fail("foreign_action_evidence",
          "the record layer returned evidence for another action", { index });
        return record;
      });
      const reading = judge.evaluateActionTrustWindow({ tenant: ORGANIZATION_TENANT_ID,
        action_kind, evidence, trust_scope: "per_action" });
      // The rows are durable and attributed to an authenticated recorder, but
      // WHO may authenticate provider evidence is an open human ruling, so
      // this read stays fail-closed: evidence_authenticated is never true here.
      return freeze({ ...reading, evidence_authenticated: false,
        evidence_origin: "rw02_record_layer_readback",
        evidence_authentication: "open_human_ruling", autonomy_active: false,
        activation_review_eligibility: "unavailable", effects: effects(false) });
    });
  }

  return freeze({ recordPageStop, recordDuplicateCheck, recordWriteReadback, readActionEvidence });
}

export function v5Rw02StoreToolRegistrations() {
  return [
    { name: "record-salesforce-page-stop", write: true, method: "recordPageStop" },
    { name: "record-salesforce-duplicate-check", write: true, method: "recordDuplicateCheck" },
    { name: "record-salesforce-write-readback", write: true, method: "recordWriteReadback" },
    { name: "read-salesforce-action-evidence", write: false, method: "readActionEvidence" },
  ];
}

export function salesforceReconciliationStoreTools({ withEnvelope, ToolError,
  createStore = createSalesforceReconciliationStore } = {}) {
  if (typeof withEnvelope !== "function" || typeof ToolError !== "function")
    fail("tool_wiring_incomplete", "shared tool wiring is required");
  const tools = {};
  for (const { name, write, method } of v5Rw02StoreToolRegistrations()) {
    const run = async (client, actor, args) => {
      // withEnvelope already runs inside the request transaction. Advertising that
      // transaction to the store prevents a nested BEGIN while keeping the store
      // independently transactional when it is used outside the MCP envelope.
      const db = { query: (...queryArgs) => client.query(...queryArgs),
        transaction: fn => fn(client) };
      try { return await createStore({ db })[method](args ?? {}, { actor }); }
      catch (error) {
        if (error instanceof V5RW02StoreError) throw new ToolError({ error: error.code,
          message: error.message, detail: error.detail });
        throw error;
      }
    };
    tools[name] = {
      write,
      description: `${name}: V5-RW02 attended Salesforce reconciliation record-layer seam; no provider call.`,
      inputSchema: name === "record-salesforce-page-stop"
        ? { type: "object", additionalProperties: false,
          properties: { idempotency_key: { type: "string" }, page: { type: "object" } },
          required: ["idempotency_key", "page"] }
        : name === "record-salesforce-duplicate-check"
          ? { type: "object", additionalProperties: false,
            properties: { idempotency_key: { type: "string" }, tenant: { type: "string" },
              case: { type: "object" }, intent_ordinal: { type: "integer" },
              page: { type: "object" }, search: { type: "object" } },
            required: ["idempotency_key", "tenant", "case", "intent_ordinal", "page", "search"] }
          : name === "record-salesforce-write-readback"
            ? { type: "object", additionalProperties: false,
              properties: { idempotency_key: { type: "string" }, observation: { type: "object" } },
              required: ["idempotency_key", "observation"] }
            : { type: "object", additionalProperties: false,
              properties: { action_kind: { type: "string", enum: [...V5_RW02_ACTION_KIND_KEYS] } },
              required: ["action_kind"] },
      handler: write ? async (c, actor, args) => withEnvelope(c, actor, name, args,
        () => run(c, actor, args)) : run,
    };
  }
  return tools;
}
