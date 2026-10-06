// Bounded record-backed continuity for native Claude sessions. Transcript
// bodies remain local; the Worker stores semantic checkpoints and immutable
// lifecycle receipts bound to an authenticated surface principal and leaf.
import { organizationTenantForActor } from "./identity.js";
import { canonicalJson } from "./artifact-trust.js";

const RUNTIME = "claude";
const MAX_VERSION = Number.MAX_SAFE_INTEGER;
const STATE_BYTES = 24000, CURSOR_BYTES = 2000, TELEMETRY_BYTES = 4000, CAPSULE_BYTES = 3200, TEXT = 4000;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const EVENT_TYPES = new Set(["user_prompt_submit", "post_tool_use", "pre_compact", "stop"]);
const STATE_FIELDS = new Set([
  "objective", "acceptance", "latest_corrections", "constraints", "decisions",
  "completed_work", "unresolved_defects", "artifacts", "verification",
  "pending_external_effects", "next_action", "focus_file_ids",
  "provenance_receipts", "source_observed_at", "source_cursor",
]);
const LIST_FIELDS = new Set([...STATE_FIELDS].filter(field =>
  !["objective", "next_action", "source_observed_at", "source_cursor"].includes(field)));
const ITEM_FIELDS = new Set(["text", "why", "refs"]);
const itemSchema = { type: "object", additionalProperties: false, properties: {
  text: { type: "string" }, why: { type: "string" },
  refs: { type: "array", items: { type: "string" } },
}, required: ["text"] };
const citedItemSchema = { ...itemSchema, properties: { ...itemSchema.properties,
  refs: { type: "array", minItems: 1, items: { type: "string", minLength: 1, maxLength: 500, pattern: "\\S" } },
}, required: ["text", "refs"] };
const decisionSchema = { ...citedItemSchema, properties: { ...citedItemSchema.properties,
  why: { type: "string", minLength: 1, maxLength: TEXT, pattern: "\\S" },
}, required: ["text", "why", "refs"] };
const stateSchema = { type: "object", additionalProperties: false, properties: {
  objective: { type: "string" }, acceptance: { type: "array", items: itemSchema },
  latest_corrections: { type: "array", items: citedItemSchema }, constraints: { type: "array", items: itemSchema },
  decisions: { type: "array", items: decisionSchema }, completed_work: { type: "array", items: itemSchema },
  unresolved_defects: { type: "array", items: itemSchema }, artifacts: { type: "array", items: itemSchema },
  verification: { type: "array", items: itemSchema }, pending_external_effects: { type: "array", items: citedItemSchema },
  next_action: { type: "string" }, focus_file_ids: { type: "array", items: itemSchema },
  provenance_receipts: { type: "array", items: citedItemSchema },
  source_observed_at: { type: "string" }, source_cursor: { type: "object" },
}, required: ["objective", "next_action", "source_observed_at", "source_cursor"] };

function fail(ToolError, error, extra = {}) { throw new ToolError({ error, ...extra }); }
function text(value, field, ToolError, limit = TEXT) {
  if (typeof value !== "string" || !value.trim() || value.length > limit)
    fail(ToolError, "claude_continuity_field_invalid", { field, max_length: limit });
  return value.trim();
}
function optionalText(value, field, ToolError, limit = TEXT) {
  return value === undefined || value === null ? null : text(value, field, ToolError, limit);
}
function optionalId(value, field, ToolError) {
  const normalized = optionalText(value, field, ToolError, 200);
  if (normalized !== null && !ID.test(normalized))
    fail(ToolError, "claude_native_identity_invalid", { field });
  return normalized;
}
function digest(value, field, ToolError) {
  const normalized = text(value, field, ToolError, 64);
  if (!DIGEST.test(normalized)) fail(ToolError, "claude_continuity_digest_invalid", { field });
  return normalized;
}
function boundedJson(value, field, ToolError, limit, nullable = false) {
  if (nullable && (value === undefined || value === null)) return null;
  if (!value || typeof value !== "object" || Array.isArray(value))
    fail(ToolError, "claude_continuity_json_invalid", { field });
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > limit)
    fail(ToolError, "claude_continuity_payload_too_large", { field, max_bytes: limit });
  return value;
}
function validateState(value, ToolError) {
  const state = boundedJson(value, "state", ToolError, STATE_BYTES);
  const unknown = Object.keys(state).filter(field => !STATE_FIELDS.has(field));
  if (unknown.length) fail(ToolError, "claude_checkpoint_field_unknown", { fields: unknown.sort() });
  for (const field of ["objective", "next_action", "source_observed_at"]) text(state[field], field, ToolError);
  boundedJson(state.source_cursor, "source_cursor", ToolError, CURSOR_BYTES);
  for (const field of LIST_FIELDS) {
    if (state[field] === undefined) continue;
    if (!Array.isArray(state[field]) || state[field].length > 100 || state[field].some(item => {
      if (!item || typeof item !== "object" || Array.isArray(item) || Object.keys(item).some(key => !ITEM_FIELDS.has(key)) ||
          typeof item.text !== "string" || !item.text.trim() || item.text.length > TEXT) return true;
      if (item.why !== undefined && (typeof item.why !== "string" || item.why.length > TEXT)) return true;
      if (item.refs !== undefined && (!Array.isArray(item.refs) || item.refs.some(ref => typeof ref !== "string" || !ref.trim() || ref.length > 500))) return true;
      if (["latest_corrections", "pending_external_effects", "provenance_receipts"].includes(field) && !item.refs?.length) return true;
      return field === "decisions" && (!item.why?.trim() || !item.refs?.length);
    })) fail(ToolError, "claude_checkpoint_field_invalid", { field });
  }
  return state;
}
function expectedVersion(value, ToolError) {
  if (!Number.isSafeInteger(value) || value < 0) fail(ToolError, "claude_checkpoint_expected_version_invalid");
  return value;
}
function version(value, ToolError) {
  const normalized = typeof value === "string" && /^[1-9][0-9]*$/.test(value) ? Number(value) : value;
  if (!Number.isSafeInteger(normalized) || normalized < 1 || normalized > MAX_VERSION)
    fail(ToolError, "claude_checkpoint_version_invalid");
  return normalized;
}
function requireNativeClaude(actor, ToolError) {
  if (!(actor?.slug === "claude" && actor.human === false && actor.native_agent_verified === true &&
        actor.continuity_surface === "claude" && typeof actor.sponsoring_human_slug === "string" && actor.sponsoring_human_slug.trim()))
    fail(ToolError, "claude_native_principal_required", { hint: "Claude continuity requires the server-derived Claude surface principal" });
}
function common(args, ToolError) {
  if (args.runtime !== RUNTIME) fail(ToolError, "claude_runtime_required");
  const sessionId = text(args.session_id, "session_id", ToolError, 200);
  if (!ID.test(sessionId)) fail(ToolError, "claude_session_id_invalid");
  return {
    sessionId, transcriptPathDigest: digest(args.transcript_path_digest, "transcript_path_digest", ToolError),
    projectAffinity: text(args.project_affinity, "project_affinity", ToolError, 500),
    cwd: optionalText(args.cwd, "cwd", ToolError, 1000),
    parentSessionId: optionalId(args.parent_session_id, "parent_session_id", ToolError),
    nativeAgentId: optionalId(args.native_agent_id, "native_agent_id", ToolError),
    modelId: optionalText(args.model_id, "model_id", ToolError, 200),
  };
}
function canonicalDbJson(value) { return canonicalJson(JSON.parse(JSON.stringify(value))); }
async function lockLeaf(c, tenant, actor, owner, key) {
  await c.query("select pg_advisory_xact_lock(hashtextextended($1,0))", [
    `${tenant}:${actor.slug}:${owner}:${key.sessionId}:${key.transcriptPathDigest}`]);
}
async function actorId(c, actor, ToolError) {
  if (typeof actor.id === "string" && actor.id) return actor.id;
  const result = await c.query("select id from actor where slug=$1", [actor.slug]);
  const id = result.rows[0]?.id;
  if (typeof id !== "string" || !id) fail(ToolError, "claude_continuity_actor_unavailable");
  actor.id = id;
  return id;
}
async function readLeaf(c, tenant, principalActorId, owner, key, ToolError, forUpdate = false) {
  const result = await c.query(
    `select l.* from claude_continuity_leaf l
      where l.organization_tenant_id=$1 and l.surface_principal_actor_id=$2
        and l.owner_actor_id=(select id from actor where slug=$3)
        and l.session_id=$4 and l.transcript_path_digest=$5${forUpdate ? " for update" : ""}`,
    [tenant, principalActorId, owner, key.sessionId, key.transcriptPathDigest]);
  const leaf = result.rows[0] || null;
  if (leaf && (leaf.project_affinity !== key.projectAffinity || leaf.parent_session_id !== key.parentSessionId ||
      leaf.native_agent_id !== key.nativeAgentId))
    fail(ToolError, "claude_continuity_binding_conflict");
  return leaf;
}
async function bindLeaf(c, tenant, principalActorId, owner, key, ToolError) {
  await c.query(`insert into claude_continuity_leaf
    (organization_tenant_id,surface_principal_actor_id,owner_actor_id,session_id,transcript_path_digest,
     project_affinity,parent_session_id,native_agent_id,latest_cwd,latest_model_id)
    values ($1,$2,(select id from actor where slug=$3),$4,$5,$6,$7,$8,$9,$10)
    on conflict (organization_tenant_id,surface_principal_actor_id,owner_actor_id,session_id,transcript_path_digest)
    do nothing`, [tenant, principalActorId, owner, key.sessionId, key.transcriptPathDigest, key.projectAffinity,
      key.parentSessionId, key.nativeAgentId, key.cwd, key.modelId]);
  const leaf = await readLeaf(c, tenant, principalActorId, owner, key, ToolError, true);
  if (!leaf) fail(ToolError, "claude_continuity_leaf_unavailable");
  await c.query(`update claude_continuity_leaf set latest_cwd=$2,latest_model_id=$3,updated_at=now()
    where id=$1`, [leaf.id, key.cwd, key.modelId]);
  return leaf;
}
async function readBinding(c, leaf, ToolError, forUpdate = false) {
  const result = await c.query(
    `select c.id,l.session_id,l.transcript_path_digest,l.project_affinity,l.parent_session_id,
            l.native_agent_id,l.latest_cwd as cwd,l.latest_model_id as model_id,c.state,c.cursor,c.transcript_digest,c.source_observed_at,
            c.checkpoint_version,c.compaction_generation,c.updated_at
       from claude_continuity_checkpoint c join claude_continuity_leaf l on l.id=c.leaf_id
      where c.leaf_id=$1${forUpdate ? " for update of c" : ""}`, [leaf.id]);
  const row = result.rows[0];
  if (!row) return null;
  return { ...row, checkpoint_version: version(row.checkpoint_version, ToolError) };
}
function utf8(value) { return new TextEncoder().encode(value).byteLength; }
function byteSlice(value, limit) {
  let out = "";
  for (const char of String(value)) {
    if (utf8(out + char) > limit) break;
    out += char;
  }
  return out;
}
function itemLines(items) {
  return (Array.isArray(items) ? items : []).map(item =>
    `- ${item.text}${item.why ? ` — ${item.why}` : ""}${item.refs?.length ? ` [${item.refs.join(", ")}]` : ""}`);
}
function boundedSection(label, lines, budget) {
  const heading = `${label}:`;
  if (!lines.length) return heading + "\n- none recorded";
  let out = heading;
  for (const line of lines) {
    const candidate = `${out}\n${line}`;
    if (utf8(candidate) <= budget) { out = candidate; continue; }
    const remaining = budget - utf8(out + "\n- … [section truncated]");
    if (remaining > 4) out += `\n${byteSlice(line, remaining)}…`;
    return byteSlice(out + "\n- … [section truncated]", budget);
  }
  return out;
}
function capsuleSections(checkpoint) {
  const state = checkpoint.state || {};
  const mandatory = [
    boundedSection("Source", [
      `- record version ${checkpoint.checkpoint_version}; cursor ${JSON.stringify(state.source_cursor)}; observed ${state.source_observed_at}`,
      "- Current user instructions govern. Later verified source cursor and observed time govern overlap; surface equal or incomparable conflicts before an irreversible action. A precompact checkpoint predates the native summary from that boundary.",
    ], 450),
    boundedSection("Objective", [`- ${state.objective}`], 450),
    boundedSection("Current corrections", itemLines(state.latest_corrections), 650),
    boundedSection("Current constraints", itemLines(state.constraints), 500),
    boundedSection("Pending external effects (verify; never replay)", itemLines(state.pending_external_effects), 650),
    boundedSection("Next action", [`- ${state.next_action}`], 450),
  ];
  const optional = [];
  for (const [label, items] of [
    ["Decisions", state.decisions], ["Acceptance", state.acceptance], ["Completed", state.completed_work], ["Unresolved defects", state.unresolved_defects],
    ["Verification", state.verification], ["Artifacts", state.artifacts],
  ]) {
    if (!Array.isArray(items) || !items.length) continue;
    optional.push(`${label}:`, ...itemLines(items));
  }
  return { mandatory, optional };
}
export function buildClaudeRecoveryCapsule(checkpoint) {
  const { mandatory, optional } = capsuleSections(checkpoint);
  let out = mandatory.join("\n");
  for (const line of optional) {
    const candidate = `${out}\n${line}`;
    if (utf8(candidate) > CAPSULE_BYTES) {
      const suffix = "\n[Optional detail truncated at the Worker byte boundary.]";
      return utf8(out + suffix) <= CAPSULE_BYTES ? out + suffix : out;
    }
    out = candidate;
  }
  return out;
}
const commonProperties = {
  runtime: { type: "string", enum: [RUNTIME] }, session_id: { type: "string" },
  transcript_path_digest: { type: "string", pattern: "^[0-9a-f]{64}$" }, project_affinity: { type: "string" },
  cwd: { type: "string" },
  parent_session_id: { type: "string", maxLength: 200, pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$" },
  native_agent_id: { type: "string", maxLength: 200, pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$" },
  model_id: { type: "string" },
};
const commonRequired = ["runtime", "session_id", "transcript_path_digest", "project_affinity"];

export function claudeContinuityTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }) {
  const guard = args => assertNoCallerAuthorityFields?.(args);
  return {
    "claude-checkpoint": {
      write: true,
      description: "Persist a bounded model-issued Claude milestone checkpoint with explicit CAS. Authenticated surface, sponsor, session, and transcript leaf form the immutable binding; model and cwd are telemetry.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...commonProperties,
        idempotency_key: { type: "string" }, expected_version: { type: "integer", minimum: 0 }, state: stateSchema,
        cursor: { type: "object" }, transcript_digest: { type: "string" }, compaction_generation: { type: "integer", minimum: 0 },
      }, required: [...commonRequired, "idempotency_key", "expected_version", "state"] },
      handler: async (c, actor, args) => {
        guard(args); requireNativeClaude(actor, ToolError); const key = common(args, ToolError);
        const expected = expectedVersion(args.expected_version, ToolError), state = validateState(args.state, ToolError);
        const cursor = boundedJson(args.cursor ?? state.source_cursor, "cursor", ToolError, CURSOR_BYTES);
        const transcriptDigest = args.transcript_digest == null ? null : digest(args.transcript_digest, "transcript_digest", ToolError);
        const generation = args.compaction_generation ?? 0;
        if (!Number.isSafeInteger(generation) || generation < 0) fail(ToolError, "claude_compaction_generation_invalid");
        return withEnvelope(c, actor, "claude-checkpoint", args, async () => {
          const tenant = organizationTenantForActor(actor), owner = actor.sponsoring_human_slug;
          const principalActorId = await actorId(c, actor, ToolError);
          await lockLeaf(c, tenant, actor, owner, key);
          const leaf = await bindLeaf(c, tenant, principalActorId, owner, key, ToolError);
          const existing = await readBinding(c, leaf, ToolError, true);
          if (existing && generation < Number(existing.compaction_generation))
            fail(ToolError, "claude_compaction_generation_regressed", {
              current_generation: Number(existing.compaction_generation), requested_generation: generation,
            });
          let row;
          if (!existing) {
            if (expected !== 0) fail(ToolError, "claude_checkpoint_version_conflict", { current_version: 0 });
            row = (await c.query(`insert into claude_continuity_checkpoint
              (leaf_id,state,cursor,transcript_digest,source_observed_at,compaction_generation)
              values ($1,$2::jsonb,$3::jsonb,$4,$5::timestamptz,$6) returning *`,
              [leaf.id, JSON.stringify(state), JSON.stringify(cursor), transcriptDigest,
                state.source_observed_at, generation])).rows[0];
          } else {
            if (existing.checkpoint_version !== expected) fail(ToolError, "claude_checkpoint_version_conflict", { current_version: existing.checkpoint_version, expected_version: expected });
            if (expected === MAX_VERSION) fail(ToolError, "claude_checkpoint_version_exhausted", { current_version: expected });
            const changed = await c.query(`update claude_continuity_checkpoint set state=$3::jsonb,cursor=$4::jsonb,
              transcript_digest=$5,source_observed_at=$6::timestamptz,compaction_generation=$7,
              checkpoint_version=checkpoint_version+1,updated_at=now()
              where id=$1 and leaf_id=$2 and checkpoint_version=$8 returning *`,
              [existing.id, leaf.id, JSON.stringify(state), JSON.stringify(cursor), transcriptDigest,
               state.source_observed_at, generation, expected]);
            if (!changed.rows.length) fail(ToolError, "claude_checkpoint_version_conflict");
            row = changed.rows[0];
          }
          row = { ...row, checkpoint_version: version(row.checkpoint_version, ToolError) };
          await c.query(`insert into claude_continuity_revision
            (checkpoint_id,checkpoint_version,state,cursor,transcript_digest,source_observed_at,compaction_generation,created_by_actor_id)
            values ($1,$2,$3::jsonb,$4::jsonb,$5,$6::timestamptz,$7,$8)`,
            [row.id, row.checkpoint_version, JSON.stringify(state), JSON.stringify(cursor), transcriptDigest,
             state.source_observed_at, generation, actor.id]);
          await writeEvent(c, actor, "claude-checkpoint", "claude_continuity_checkpoint", row.id,
            { field: "checkpoint_version", new: { version: row.checkpoint_version }, idempotency_key: args.idempotency_key });
          return { ok: true, checkpoint: row };
        });
      },
    },
    "claude-read-recovery": {
      description: "Read the exact bound Claude leaf and a Worker-capped recovery capsule. Pending effects are displayed for verification and never replayed.",
      inputSchema: { type: "object", additionalProperties: false, properties: commonProperties, required: commonRequired },
      handler: async (c, actor, args) => {
        guard(args); requireNativeClaude(actor, ToolError); const key = common(args, ToolError);
        const principalActorId = await actorId(c, actor, ToolError);
        const leaf = await readLeaf(c, organizationTenantForActor(actor), principalActorId,
          actor.sponsoring_human_slug, key, ToolError);
        if (!leaf) return { ok: true, found: false, checkpoint: null, capsule: null, capsule_bytes: 0 };
        const checkpoint = await readBinding(c, leaf, ToolError);
        if (!checkpoint) return { ok: true, found: false, checkpoint: null, capsule: null, capsule_bytes: 0 };
        const capsule = buildClaudeRecoveryCapsule(checkpoint);
        return { ok: true, found: true, checkpoint, capsule,
          capsule_bytes: new TextEncoder().encode(capsule).byteLength,
          conflict_policy: "current user instruction; otherwise later verified source cursor and observed_at; surface equal or incomparable conflict before irreversible action" };
      },
    },
    "claude-record-event": {
      write: true,
      description: "Record one immutable idempotent Claude lifecycle receipt. Telemetry never infers semantic completion or replays an external effect.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...commonProperties,
        idempotency_key: { type: "string" }, event_type: { type: "string", enum: [...EVENT_TYPES] },
        cursor: { type: "object" }, transcript_digest: { type: "string" }, observed_at: { type: "string" },
        telemetry: { type: "object" }, checkpoint_version: { type: "integer", minimum: 0 },
      }, required: [...commonRequired, "idempotency_key", "event_type", "cursor", "observed_at"] },
      handler: async (c, actor, args) => {
        guard(args); requireNativeClaude(actor, ToolError); const key = common(args, ToolError);
        if (!EVENT_TYPES.has(args.event_type)) fail(ToolError, "claude_event_type_invalid");
        const cursor = boundedJson(args.cursor, "cursor", ToolError, CURSOR_BYTES);
        const telemetry = boundedJson(args.telemetry, "telemetry", ToolError, TELEMETRY_BYTES, true);
        const observedAt = text(args.observed_at, "observed_at", ToolError, 100);
        const transcriptDigest = args.transcript_digest == null ? null : digest(args.transcript_digest, "transcript_digest", ToolError);
        const checkpointVersion = args.checkpoint_version ?? 0;
        if (!Number.isSafeInteger(checkpointVersion) || checkpointVersion < 0) fail(ToolError, "claude_event_checkpoint_version_invalid");
        return withEnvelope(c, actor, "claude-record-event", args, async () => {
          const tenant = organizationTenantForActor(actor), owner = actor.sponsoring_human_slug;
          const principalActorId = await actorId(c, actor, ToolError);
          await lockLeaf(c, tenant, actor, owner, key);
          const leaf = await bindLeaf(c, tenant, principalActorId, owner, key, ToolError);
          const params = [tenant, principalActorId, leaf.id, args.event_type, JSON.stringify(cursor), transcriptDigest,
            observedAt, telemetry == null ? null : JSON.stringify(telemetry), checkpointVersion, args.idempotency_key];
          const inserted = await c.query(`insert into claude_continuity_event
            (organization_tenant_id,surface_principal_actor_id,leaf_id,event_type,cursor,transcript_digest,
             observed_at,telemetry,checkpoint_version,idempotency_key)
            values ($1,$2,$3,$4,$5::jsonb,$6,$7::timestamptz,$8::jsonb,$9,$10)
            on conflict (organization_tenant_id,surface_principal_actor_id,idempotency_key) do nothing returning *`, params);
          let event = inserted.rows[0];
          if (!event) {
            event = (await c.query(`select * from claude_continuity_event where organization_tenant_id=$1
              and surface_principal_actor_id=$2 and idempotency_key=$3`, [tenant, principalActorId, args.idempotency_key])).rows[0];
            const same = event && event.leaf_id === leaf.id && event.event_type === args.event_type &&
              canonicalDbJson(event.cursor) === canonicalDbJson(cursor) && canonicalDbJson(event.telemetry) === canonicalDbJson(telemetry) &&
              event.transcript_digest === transcriptDigest && Number(event.checkpoint_version) === checkpointVersion;
            if (!same) fail(ToolError, "claude_event_key_conflict");
          }
          await writeEvent(c, actor, "claude-record-event", "claude_continuity_event", event.id,
            { field: "event_type", new: { event_type: args.event_type }, idempotency_key: args.idempotency_key });
          return { ok: true, event };
        });
      },
    },
  };
}
