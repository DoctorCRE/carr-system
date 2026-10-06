// Durable continuity for native Codex tasks.  This module stores bounded
// semantic checkpoints and cursor/event receipts; native transcript bodies
// remain on the Codex machine and are read through the local adapter.

import { organizationTenantForActor, personalScopeForActor } from "./identity.js";
import { canonicalJson, digest } from "./artifact-trust.js";
import {
  CONTINUITY_STORAGE_CONTRACT, REFERENCE_MANIFEST_LIMIT, SEMANTIC_STATE_LIMIT, hydrateReferenceManifest,
  referenceManifestSummary, splitReferenceManifest,
} from "../continuity-reference-manifest.mjs";

const RUNTIME = "codex";
const TASK_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const TEXT_LIMIT = 4000;
const STATE_LIMIT = SEMANTIC_STATE_LIMIT;
const CURSOR_LIMIT = 2000;
const RECOVERY_TURN_LIMIT = 25;
const MAX_CHECKPOINT_VERSION = Number.MAX_SAFE_INTEGER;
const UNRESOLVED_REFERENCE_RE = /\{REF[0-9]+\}/i;
const REVISION_SCHEMA_VERSION = "codex-continuity-revision.v1";
const REVISION_DIGEST_RE = /^sha256:[0-9a-f]{64}$/;

const STATE_FIELDS = new Set([
  "objective", "acceptance", "latest_corrections", "constraints", "decisions",
  "progress", "blockers", "hypotheses", "verified_evidence", "artifacts",
  "pending_operations", "receipts", "next_action",
]);
const STATE_LIST_FIELDS = new Set([...STATE_FIELDS].filter(key => !["objective", "next_action"].includes(key)));
const STATE_ITEM_FIELDS = new Set(["text", "why", "refs"]);

const stateItemSchema = {
  type: "object", additionalProperties: false,
  properties: {
    text: { type: "string" }, why: { type: "string" },
    refs: { type: "array", items: { type: "string" } },
  }, required: ["text"],
};
const citedStateItemSchema = {
  type: "object", additionalProperties: false,
  properties: {
    text: { type: "string" }, why: { type: "string" },
    refs: { type: "array", minItems: 1,
      items: { type: "string", minLength: 1, maxLength: 500, pattern: "\\S" } },
  }, required: ["text", "refs"],
};
const decisionStateItemSchema = {
  type: "object", additionalProperties: false,
  properties: {
    text: { type: "string" },
    why: { type: "string", minLength: 1, maxLength: TEXT_LIMIT, pattern: "\\S" },
    refs: { type: "array", minItems: 1,
      items: { type: "string", minLength: 1, maxLength: 500, pattern: "\\S" } },
  }, required: ["text", "why", "refs"],
};
const stateSchema = {
  type: "object", additionalProperties: false,
  properties: {
    objective: { type: "string" }, acceptance: { type: "array", items: stateItemSchema },
    latest_corrections: { type: "array", items: citedStateItemSchema },
    constraints: { type: "array", items: stateItemSchema },
    decisions: { type: "array", items: decisionStateItemSchema }, progress: { type: "array", items: stateItemSchema },
    blockers: { type: "array", items: stateItemSchema }, hypotheses: { type: "array", items: stateItemSchema },
    verified_evidence: { type: "array", items: stateItemSchema }, artifacts: { type: "array", items: stateItemSchema },
    pending_operations: { type: "array", items: stateItemSchema }, receipts: { type: "array", items: stateItemSchema },
    next_action: { type: "string" },
  }, required: ["objective", "next_action"],
};

function validateStateShape(value, ToolError, allowUnresolvedReferences = false) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new ToolError({ error: "codex_continuity_json_invalid", field: "state" });
  const unknown = Object.keys(value).filter(key => !STATE_FIELDS.has(key)).sort();
  if (unknown.length)
    throw new ToolError({ error: "codex_checkpoint_field_unknown", fields: unknown });
  if (typeof value.objective !== "string" || !value.objective.trim() || value.objective.length > TEXT_LIMIT ||
      typeof value.next_action !== "string" || !value.next_action.trim() || value.next_action.length > TEXT_LIMIT)
    throw new ToolError({ error: "codex_checkpoint_required_field_invalid", fields: ["objective", "next_action"] });
  for (const field of STATE_LIST_FIELDS) {
    if (value[field] === undefined) continue;
    if (!Array.isArray(value[field]) || value[field].length > 100 || value[field].some(item => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return true;
      if (Object.keys(item).some(key => !STATE_ITEM_FIELDS.has(key))) return true;
      if (typeof item.text !== "string" || !item.text.trim() || item.text.length > TEXT_LIMIT) return true;
      if (item.why !== undefined && (typeof item.why !== "string" || item.why.length > TEXT_LIMIT)) return true;
      if (item.refs !== undefined && (!Array.isArray(item.refs) || item.refs.some(ref =>
        typeof ref !== "string" || ref.length > 500))) return true;
      if (field === "latest_corrections" && (!Array.isArray(item.refs) || !item.refs.length ||
          item.refs.some(ref => !ref.trim()))) return true;
      if (field === "decisions" && (typeof item.why !== "string" || !item.why.trim() ||
          !Array.isArray(item.refs) || !item.refs.length || item.refs.some(ref => !ref.trim()))) return true;
      return false;
    }))
      throw new ToolError({ error: "codex_checkpoint_field_invalid", field,
        hint: "checkpoint list fields contain bounded objects; corrections require refs and decisions require why plus refs" });
  }
  const referenceIssues = [];
  for (const field of ["objective", "next_action"])
    if (UNRESOLVED_REFERENCE_RE.test(value[field])) referenceIssues.push(field);
  for (const field of STATE_LIST_FIELDS) {
    if (!Array.isArray(value[field])) continue;
    value[field].forEach((item, itemIndex) => {
      for (const key of ["text", "why"])
        if (typeof item[key] === "string" && UNRESOLVED_REFERENCE_RE.test(item[key]))
          referenceIssues.push(`${field}[${itemIndex}].${key}`);
      if (Array.isArray(item.refs)) item.refs.forEach((ref, refIndex) => {
        if (UNRESOLVED_REFERENCE_RE.test(ref))
          referenceIssues.push(`${field}[${itemIndex}].refs[${refIndex}]`);
      });
    });
  }
  if (!allowUnresolvedReferences && referenceIssues.length)
    throw new ToolError({ error: "codex_checkpoint_reference_invalid",
      paths: referenceIssues.slice(0, 25),
      hint: "replace unresolved {REF<number>} tokens from canonical evidence before writing" });
}

function text(value, field, ToolError, limit = TEXT_LIMIT) {
  if (typeof value !== "string" || !value.trim() || value.length > limit)
    throw new ToolError({ error: "codex_continuity_field_invalid", field, max_length: limit });
  return value.trim();
}

function taskId(value, ToolError) {
  return text(value, "native_task_id", ToolError, 200).match(TASK_RE)
    ? value.trim() : (() => { throw new ToolError({ error: "codex_native_task_id_invalid" }); })();
}

function boundedJson(value, field, ToolError, limit) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new ToolError({ error: "codex_continuity_json_invalid", field });
  const keys = Object.keys(value);
  if (field === "state" && keys.some(key => !STATE_FIELDS.has(key)))
    throw new ToolError({ error: "codex_checkpoint_field_unknown", fields: keys.filter(k => !STATE_FIELDS.has(k)).sort() });
  if (field === "state") validateStateShape(value, ToolError);
  const encoded = JSON.stringify(value);
  if (new TextEncoder().encode(encoded).byteLength > limit)
    throw new ToolError({ error: "codex_continuity_payload_too_large", field, max_bytes: limit });
  return value;
}

function canonicalDbJson(value) {
  // JSONB discards undefined properties and canonicalizes object key order;
  // compare that representation so a transport retry has database semantics.
  return canonicalJson(JSON.parse(JSON.stringify(value)));
}

function cursor(value, ToolError) {
  if (value === undefined || value === null) return null;
  return boundedJson(value, "cursor", ToolError, CURSOR_LIMIT);
}

function dbCursor(value) {
  return value === null ? null : JSON.stringify(value);
}

function requireNativeCodex(actor, ToolError) {
  // Surface and native-agent verification are both derived by authentication.
  // runtime in a request is metadata and can never select this authority.
  const localSponsored = (actor?.via === "codex-continuity-token" ||
    (actor?.via === "local-token" && actor?.continuity_surface_compat === true)) &&
    actor.continuity_surface === "codex" && actor.native_agent_verified === true &&
    typeof actor.sponsoring_human_slug === "string";
  const nativeOAuth = actor?.slug === "codex" && actor.human === false &&
    actor.native_agent_verified === true && actor.continuity_surface === "codex";
  if (!(nativeOAuth || localSponsored))
    throw new ToolError({ error: "codex_native_principal_required",
      hint: "continuity writes require a server-verified native Codex principal" });
  if (typeof actor.sponsoring_human_slug !== "string" || !actor.sponsoring_human_slug.trim())
    throw new ToolError({ error: "codex_continuity_owner_required" });
  return actor;
}

function ownerSlug(actor) {
  // Codex OAuth and the local Codex hook can authenticate through different
  // server doors.  Their shared verified sponsor is the durable owner key, so
  // a transport change does not fork one native task into two checkpoints.
  return actor.sponsoring_human_slug;
}

function requireRuntime(runtime, ToolError) {
  if (runtime !== RUNTIME)
    throw new ToolError({ error: "codex_runtime_required", runtime: runtime || null });
}

function common(args, ToolError) {
  requireRuntime(args.runtime, ToolError);
  return {
    nativeTaskId: taskId(args.native_task_id, ToolError),
    projectId: text(args.project_id, "project_id", ToolError, 500),
    cwd: text(args.cwd, "cwd", ToolError, 1000),
  };
}

function expectedVersion(value, ToolError) {
  if (!Number.isSafeInteger(value) || value < 0)
    throw new ToolError({ error: "codex_checkpoint_expected_version_invalid" });
  return value;
}

function checkpointVersion(value, ToolError) {
  const normalized = typeof value === "string" && /^[1-9][0-9]*$/.test(value)
    ? Number(value) : value;
  if (!Number.isSafeInteger(normalized) || normalized < 1 ||
      normalized > MAX_CHECKPOINT_VERSION)
    throw new ToolError({ error: "codex_checkpoint_version_invalid" });
  return normalized;
}

function recoveryVersion(value, ToolError) {
  if (value === undefined || value === null) return null;
  try {
    return checkpointVersion(value, ToolError);
  } catch (error) {
    if (error?.payload?.error === "codex_checkpoint_version_invalid")
      throw new ToolError({ error: "codex_recovery_version_invalid" });
    throw error;
  }
}

function recoveryExpectedDigest(value, ToolError) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || !REVISION_DIGEST_RE.test(value))
    throw new ToolError({ error: "codex_recovery_expected_digest_invalid" });
  return value;
}

function revisionDigest(row, ToolError) {
  const version = checkpointVersion(row?.checkpoint_version, ToolError);
  return digest({
    schema_version: REVISION_SCHEMA_VERSION,
    checkpoint_id: row.checkpoint_id,
    checkpoint_version: version,
    state: hydrateState(row, ToolError),
    cursor: row.cursor ?? null,
  });
}

function manifestError(error, ToolError) {
  if (error?.code === "codex_reference_manifest_invalid")
    throw new ToolError({ error: "codex_reference_manifest_invalid", reason: error.message });
  throw error;
}

function hydrateState(row, ToolError) {
  try {
    const state = hydrateReferenceManifest(row?.state, row?.reference_manifest);
    validateStateShape(state, ToolError, true);
    return state;
  } catch (error) {
    manifestError(error, ToolError);
  }
}

function packedState(logicalState, ToolError) {
  try {
    validateStateShape(logicalState, ToolError);
    const packed = splitReferenceManifest(logicalState);
    if (packed.semantic_state_bytes > STATE_LIMIT)
      throw new ToolError({ error: "codex_continuity_payload_too_large", field: "state",
        max_bytes: STATE_LIMIT, stored_bytes: packed.semantic_state_bytes });
    if (packed.reference_manifest_bytes > REFERENCE_MANIFEST_LIMIT)
      throw new ToolError({ error: "codex_reference_manifest_too_large",
        max_bytes: REFERENCE_MANIFEST_LIMIT, stored_bytes: packed.reference_manifest_bytes,
        reference_count: packed.reference_count });
    return packed;
  } catch (error) {
    manifestError(error, ToolError);
  }
}

function manifestSummary(row, ToolError) {
  try {
    return referenceManifestSummary(row?.state, row?.reference_manifest);
  } catch (error) {
    manifestError(error, ToolError);
  }
}

function storageContract() {
  return { ...CONTINUITY_STORAGE_CONTRACT };
}

function checkpointOutput(row, ToolError) {
  const checkpoint = checkpointRow(row, ToolError);
  if (!Object.hasOwn(checkpoint, "state")) return checkpoint;
  const state = hydrateState(checkpoint, ToolError);
  const summary = manifestSummary(checkpoint, ToolError);
  const { reference_manifest, ...withoutManifest } = checkpoint;
  return { ...withoutManifest, state, reference_manifest_summary: summary,
    storage_contract: storageContract() };
}

function revisionArchiveRef(row, version, digestValue) {
  return `codex-revision:${row.checkpoint_id}:${version}:${digestValue}`;
}

function checkpointRow(row, ToolError) {
  return { ...row, checkpoint_version: checkpointVersion(row?.checkpoint_version, ToolError) };
}

function bindingConflict(error, ToolError) {
  throw new ToolError({ error,
    hint: "native task identity is immutably bound to the first lifecycle event or checkpoint project and cwd" });
}

async function lockTask(c, tenant, owner, nativeTaskId) {
  // Checkpoint and event writers deliberately share this exact lock key.  It
  // makes the first committed lifecycle write the immutable binding even when
  // an event and the first checkpoint race each other.
  await c.query("select pg_advisory_xact_lock(hashtextextended($1, 0))", [
    `${tenant}:${owner}:${nativeTaskId}`]);
}

async function readTaskBindings(c, tenant, owner, nativeTaskId, ToolError,
  checkpointForUpdate = false) {
  const checkpoint = await c.query(
    `select id,native_task_id,project_id,cwd,checkpoint_version${checkpointForUpdate ? "" : ",state,cursor,reference_manifest,updated_at"}
       from codex_continuity_checkpoint
      where organization_tenant_id=$1 and owner_actor_id=(select id from actor where slug=$2)
        and native_task_id=$3${checkpointForUpdate ? " for update" : ""}`,
    [tenant, owner, nativeTaskId]);
  const firstEvent = await c.query(
    `select project_id,cwd from codex_continuity_event
      where organization_tenant_id=$1 and owner_actor_id=(select id from actor where slug=$2)
        and native_task_id=$3
      order by created_at asc,id asc limit 1`,
    [tenant, owner, nativeTaskId]);
  return { checkpoint: checkpoint.rows[0] ? checkpointRow(checkpoint.rows[0], ToolError) : null,
    firstEvent: firstEvent.rows[0] || null };
}

function assertBinding(bindings, key, error, ToolError) {
  for (const bound of [bindings.checkpoint, bindings.firstEvent]) {
    if (bound && (bound.project_id !== key.projectId || bound.cwd !== key.cwd))
      bindingConflict(error, ToolError);
  }
}

export function codexContinuityTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }) {
  const guard = args => assertNoCallerAuthorityFields?.(args);
  return {
    "list-my-codex-sessions": {
      description: "List bounded first-hand native Codex checkpoint bindings for the authenticated human sponsor. Availability means a checkpoint was recorded; the local native host must be checked separately before opening.",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
      handler: async (c, actor, args) => {
        guard(args);
        if (Object.keys(args || {}).length)
          throw new ToolError({ error: "codex_session_list_arguments_invalid" });
        const scope = personalScopeForActor(actor);
        if (scope.status !== "personal")
          throw new ToolError({ error: "codex_sponsor_required" });
        const result = await c.query(
          `select native_task_id,checkpoint_version,updated_at
             from codex_continuity_checkpoint
            where organization_tenant_id=$1 and owner_actor_id=(select id from actor where slug=$2)
            order by updated_at desc,native_task_id asc limit 100`,
          [organizationTenantForActor(actor), scope.sponsor]);
        return { ok: true, sessions: result.rows.map(row => ({
          native_session_id: row.native_task_id,
          host: "codex_desktop",
          availability: "checkpoint_recorded",
          checkpoint_version: checkpointVersion(row.checkpoint_version, ToolError),
          updated_at: row.updated_at instanceof Date ? row.updated_at.toISOString() : row.updated_at,
        })) };
      },
    },
    "codex-checkpoint": {
      write: true,
      description: "Persist one bounded semantic checkpoint for one server-verified native Codex task. CAS uses expected_version; every accepted snapshot appends a revision. Transcript bodies remain on the native Codex machine.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, runtime: { type: "string", enum: [RUNTIME] },
        native_task_id: { type: "string" }, project_id: { type: "string" }, cwd: { type: "string" },
        expected_version: { type: "integer", minimum: 0 }, state: stateSchema, cursor: { type: "object" },
      }, required: ["idempotency_key", "runtime", "native_task_id", "project_id", "cwd", "expected_version", "state"] },
      handler: async (c, actor, args) => { guard(args); requireNativeCodex(actor, ToolError); const key = common(args, ToolError);
        const expected = expectedVersion(args.expected_version, ToolError);
        const packed = packedState(args.state, ToolError);
        const state = packed.state;
        const referenceManifest = packed.reference_manifest;
        const cur = cursor(args.cursor, ToolError);
        return withEnvelope(c, actor, "codex-checkpoint", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const owner = ownerSlug(actor);
          await lockTask(c, tenant, owner, key.nativeTaskId);
          const bindings = await readTaskBindings(c, tenant, owner, key.nativeTaskId,
            ToolError, true);
          assertBinding(bindings, key, "codex_checkpoint_binding_conflict", ToolError);
          const existing = bindings.checkpoint;
          let row;
          if (!existing) {
            if (expected !== 0) throw new ToolError({ error: "codex_checkpoint_version_conflict", current_version: 0 });
            const inserted = await c.query(
              `insert into codex_continuity_checkpoint
                (organization_tenant_id,owner_actor_id,native_task_id,project_id,cwd,state,cursor,reference_manifest)
               values ($1,(select id from actor where slug=$2),$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb)
               returning id, native_task_id, project_id, cwd, state, cursor, reference_manifest, checkpoint_version, updated_at`,
              [tenant, owner, key.nativeTaskId, key.projectId, key.cwd, JSON.stringify(state), dbCursor(cur), JSON.stringify(referenceManifest)]);
            row = checkpointRow(inserted.rows[0], ToolError);
          } else {
            const current = existing.checkpoint_version;
            if (current !== expected)
              throw new ToolError({ error: "codex_checkpoint_version_conflict", current_version: current, expected_version: expected });
            if (current === MAX_CHECKPOINT_VERSION)
              throw new ToolError({ error: "codex_checkpoint_version_exhausted",
                current_version: current });
            const updated = await c.query(
              `update codex_continuity_checkpoint set state=$4::jsonb,cursor=$5::jsonb,reference_manifest=$6::jsonb,
                 checkpoint_version=checkpoint_version+1,updated_at=now()
               where id=$1 and organization_tenant_id=$2 and owner_actor_id=(select id from actor where slug=$3) and checkpoint_version=$7
               returning id, native_task_id, project_id, cwd, state, cursor, reference_manifest, checkpoint_version, updated_at`,
              [existing.id, tenant, owner, JSON.stringify(state), dbCursor(cur), JSON.stringify(referenceManifest), expected]);
            if (!updated.rows.length)
              throw new ToolError({ error: "codex_checkpoint_version_conflict", current_version: expected + 1 });
            row = checkpointRow(updated.rows[0], ToolError);
          }
          await c.query(
            `insert into codex_continuity_revision
              (checkpoint_id,checkpoint_version,state,cursor,reference_manifest,created_by_actor_id)
             values ($1,$2,$3::jsonb,$4::jsonb,$5::jsonb,$6)`,
            [row.id, row.checkpoint_version, JSON.stringify(state), dbCursor(cur), JSON.stringify(referenceManifest), actor.id]);
          await writeEvent(c, actor, "codex-checkpoint", "codex_continuity_checkpoint", row.id,
            { field: "checkpoint_version", new: { version: row.checkpoint_version }, idempotency_key: args.idempotency_key });
          return { ok: true, checkpoint: checkpointOutput(row, ToolError),
            storage_contract: storageContract() };
        });
      },
    },

    "codex-read-recovery": {
      description: "Read the current bounded recovery checkpoint or exactly one historical revision for one native Codex task, scoped to the authenticated actor and tenant.",
      inputSchema: { type: "object", properties: {
        runtime: { type: "string", enum: [RUNTIME] }, native_task_id: { type: "string" },
        project_id: { type: "string" }, cwd: { type: "string" },
        checkpoint_version: { type: "integer", minimum: 1 },
        expected_digest: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" },
      }, required: ["runtime", "native_task_id", "project_id", "cwd"] },
      handler: async (c, actor, args) => { guard(args); requireNativeCodex(actor, ToolError); const key = common(args, ToolError);
        const requestedVersion = recoveryVersion(args.checkpoint_version, ToolError);
        const expectedDigest = recoveryExpectedDigest(args.expected_digest, ToolError);
        if (expectedDigest && requestedVersion === null)
          throw new ToolError({ error: "codex_recovery_historical_version_required" });
        const tenant = organizationTenantForActor(actor);
        const owner = ownerSlug(actor);
        const bindings = await readTaskBindings(c, tenant, owner, key.nativeTaskId,
          ToolError);
        assertBinding(bindings, key, "codex_recovery_binding_conflict", ToolError);
        const checkpoint = bindings.checkpoint;
        if (requestedVersion !== null) {
          if (!checkpoint) return {
            ok: true, found: false, historical: true, historical_archive: true, archive_ref: null,
            revision: null, integrity: "not_found", storage_contract: storageContract(),
          };
          const historical = await c.query(
            `select r.id as revision_id,r.checkpoint_id,r.checkpoint_version,r.state,r.cursor,r.reference_manifest,r.created_at
               from codex_continuity_revision r
               join codex_continuity_checkpoint c on c.id=r.checkpoint_id
              where c.id=$1 and c.organization_tenant_id=$2
                and c.owner_actor_id=(select id from actor where slug=$3)
                and c.native_task_id=$4 and c.project_id=$5 and c.cwd=$6
                and r.checkpoint_version=$7
              limit 1`,
            [checkpoint.id, tenant, owner, key.nativeTaskId, key.projectId, key.cwd, requestedVersion]);
          const row = historical.rows[0];
          if (!row) return {
            ok: true, found: false, historical: true, historical_archive: true, archive_ref: null,
            revision: null, integrity: "not_found", storage_contract: storageContract(),
          };
          const revisionVersion = checkpointVersion(row.checkpoint_version, ToolError);
          const actualDigest = revisionDigest(row, ToolError);
          if (expectedDigest && expectedDigest !== actualDigest)
            throw new ToolError({ error: "codex_recovery_revision_digest_mismatch",
              expected_digest: expectedDigest, actual_digest: actualDigest });
          const archiveRef = revisionArchiveRef(row, revisionVersion, actualDigest);
          const revision = {
            revision_id: row.revision_id,
            checkpoint_id: row.checkpoint_id,
            checkpoint_version: revisionVersion,
            state: hydrateState(row, ToolError),
            cursor: row.cursor ?? null,
            reference_manifest_summary: manifestSummary(row, ToolError),
            created_at: row.created_at,
            digest: actualDigest,
            archive_ref: archiveRef,
            integrity: expectedDigest ? "verified" : "computed_unanchored",
          };
          return { ok: true, found: true, historical: true, historical_archive: true,
            archive_ref: archiveRef, revision, integrity: revision.integrity,
            storage_contract: storageContract() };
        }
        const highwaterResult = await c.query(
          `select cursor from codex_continuity_event
            where organization_tenant_id=$1 and owner_actor_id=(select id from actor where slug=$2)
              and native_task_id=$3 and project_id=$4 and cwd=$5 and cursor is not null
            order by created_at desc,id desc limit 1`,
          [tenant, owner, key.nativeTaskId, key.projectId, key.cwd]);
        const currentVersion = checkpoint?.checkpoint_version ?? null;
        const turnsResult = await c.query(
          `with prompts as (
             select event_type,cursor,transcript_ref,created_at,id,
                    jsonb_typeof(cursor->'checkpoint_version')='number'
                      and (cursor->>'checkpoint_version') ~ '^[0-9]+$' as version_known
               from codex_continuity_event
              where organization_tenant_id=$1 and owner_actor_id=(select id from actor where slug=$2)
                and native_task_id=$3 and project_id=$4 and cwd=$5 and event_type='user_prompt_submit'
           ), pending as (
             select * from prompts
              where $6::bigint is null or not version_known
                 or case when version_known
                         then (cursor->>'checkpoint_version')::numeric >= $6::bigint
                         else true end
           ), selected as (
             select event_type,cursor,transcript_ref,created_at,id from pending
              order by created_at desc,id desc limit $7
           )
           select coalesce((select jsonb_agg(jsonb_build_object(
                    'event_type',event_type,'cursor',cursor,'transcript_ref',transcript_ref,'created_at',created_at)
                    order by created_at asc,id asc) from selected),'[]'::jsonb) as turns,
                  greatest((select count(*) from pending)-(select count(*) from selected),0)::integer as omitted,
                  coalesce((select bool_and(version_known) from prompts),true) as coverage_known`,
          [tenant, owner, key.nativeTaskId, key.projectId, key.cwd, currentVersion, RECOVERY_TURN_LIMIT]);
        const recovery = turnsResult.rows[0] || { turns: [], omitted: 0, coverage_known: false };
        return {
          ok: true,
          found: Boolean(checkpoint),
          checkpoint: checkpoint ? checkpointOutput(checkpoint, ToolError) : null,
          source_highwater: highwaterResult.rows[0]?.cursor || checkpoint?.cursor || null,
          unincorporated_user_turns: recovery.turns || [],
          unincorporated_user_turns_omitted: Number(recovery.omitted || 0),
          source_coverage: recovery.coverage_known === true ? "known" : "unknown",
          storage_contract: storageContract(),
        };
      },
    },

    "codex-record-event": {
      write: true,
      description: "Record a separate idempotent native Codex lifecycle/cursor receipt. Events never replace semantic checkpoint meaning and are safe to retry with the same key.",
      inputSchema: { type: "object", properties: { idempotency_key: { type: "string" }, runtime: { type: "string", enum: [RUNTIME] }, native_task_id: { type: "string" }, project_id: { type: "string" }, cwd: { type: "string" }, event_type: { type: "string" }, cursor: { type: "object" }, transcript_ref: { type: "string" } }, required: ["idempotency_key", "runtime", "native_task_id", "project_id", "cwd", "event_type"] },
      handler: async (c, actor, args) => { guard(args); requireNativeCodex(actor, ToolError); const key = common(args, ToolError); const eventType = text(args.event_type, "event_type", ToolError, 100); const cur = cursor(args.cursor, ToolError);
        return withEnvelope(c, actor, "codex-record-event", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const owner = ownerSlug(actor);
          await lockTask(c, tenant, owner, key.nativeTaskId);
          const bindings = await readTaskBindings(c, tenant, owner, key.nativeTaskId,
            ToolError);
          assertBinding(bindings, key, "codex_event_binding_conflict", ToolError);
          const ref = args.transcript_ref ? text(args.transcript_ref, "transcript_ref", ToolError, 1000) : null;
          const r = await c.query(
            `insert into codex_continuity_event
              (organization_tenant_id,owner_actor_id,native_task_id,project_id,cwd,event_type,cursor,transcript_ref,idempotency_key)
             values ($1,(select id from actor where slug=$2),$3,$4,$5,$6,$7::jsonb,$8,$9)
             on conflict (organization_tenant_id,owner_actor_id,native_task_id,idempotency_key)
             do nothing
             returning id,native_task_id,project_id,cwd,event_type,cursor,transcript_ref,created_at`,
            [tenant, owner, key.nativeTaskId, key.projectId, key.cwd, eventType, dbCursor(cur), ref, args.idempotency_key]);
          let event = r.rows[0];
          if (!event) {
            const prior = await c.query(
              `select id,native_task_id,project_id,cwd,event_type,cursor,transcript_ref,created_at
                 from codex_continuity_event where organization_tenant_id=$1
                  and owner_actor_id=(select id from actor where slug=$2)
                  and native_task_id=$3 and idempotency_key=$4`,
              [tenant, owner, key.nativeTaskId, args.idempotency_key]);
            event = prior.rows[0];
            const same = event && event.project_id === key.projectId && event.cwd === key.cwd &&
              event.event_type === eventType &&
              canonicalDbJson(event.cursor || null) === canonicalDbJson(cur) && event.transcript_ref === ref;
            if (!same) throw new ToolError({ error: "codex_event_key_conflict",
              hint: "reuse an idempotency key only for the identical event payload" });
          }
          await writeEvent(c, actor, "codex-record-event", "codex_continuity_event", event.id,
            { field: "event_type", new: { event_type: eventType }, idempotency_key: args.idempotency_key });
          return { ok: true, event };
        });
      },
    },
  };
}
