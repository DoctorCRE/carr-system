// CARR-native learning memory kernel (Phase 1).
// Memory is evidence-backed context, never authority. Candidates can be
// observed by an agent; promotion remains a partner-authority act. Personal
// scope is resolved from the verified sponsor, never from caller claims.

import { personalScopeForActor, organizationTenantForActor } from "./identity.js";
import { canExercisePartnerAuthority } from "./partner-authority.js";

const KINDS = ["preference", "fact", "episodic", "procedural"];
const SCOPES = ["shared", "personal"];
const FORBIDDEN_PASSPORT_FIELDS = ["work_request", "work_request_version", "job_attempt_id", "source_state"];
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function sponsorFor(actor, ToolError) {
  const scope = personalScopeForActor(actor);
  if (scope.status === "error") throw new ToolError({ error: scope.error });
  return scope;
}

async function planAnchor(c, actor, planId, ToolError) {
  if (planId === undefined || planId === null || planId === "") return null;
  const r = await c.query(
    `select * from public.resolve_memory_plan_anchor($1::uuid,$2::text)`,
    [planId, organizationTenantForActor(actor)]);
  if (!r.rows.length) throw new ToolError({ error: "memory_plan_not_found_or_forbidden", plan_id: planId });
  return r.rows[0];
}

// RETIRED 2026-08-26 BY JOE'S RULING (decision dc57f62d), same as the gate in
// tools.js — see that comment for what the refusal did and what giving it up
// costs. Kept as a no-op rather than deleted so the three call sites below
// still read as the deliberate transitions they are: promote, correct and
// forget a memory are the writes that rewrite what the system believes about
// itself, and a future ruling that wants a check back has one place to put it.
function humanOnlyDirect(_actor, _ToolError) {}

function requireUuid(value, field, ToolError) {
  if (typeof value !== "string" || !UUID_RE.test(value))
    throw new ToolError({ error: `${field}_invalid`, field });
  return value;
}

function requireReason(value, ToolError) { return validText(value, "reason", ToolError); }

function recallLimit(value, ToolError) {
  if (value === undefined) return 20;
  if (!Number.isInteger(value) || value < 1 || value > 100)
    throw new ToolError({ error: "memory_limit_invalid", hint: "limit must be an integer from 1 through 100" });
  return value;
}

function validText(value, field, ToolError) {
  if (typeof value !== "string" || !value.trim())
    throw new ToolError({ error: "memory_field_required", field });
  return value.trim();
}

export function memoryTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }) {
  const guard = args => { if (assertNoCallerAuthorityFields) assertNoCallerAuthorityFields(args); };
  return {
    "observe-memory": {
      write: true,
      description: "Record an evidence-backed memory candidate. Scope is shared or the authenticated partner's personal scope; actor and sponsor are server-derived. Observation never grants authority and does not promote the memory.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, kind: { type: "string", enum: KINDS },
        statement: { type: "string" }, context: { type: "string" }, scope: { type: "string", enum: SCOPES },
        evidence: { type: "object" }, confidence: { type: "number" },
        plan_id: { type: "string" },
      }, required: ["idempotency_key", "kind", "statement", "scope", "evidence"] },
      handler: async (c, actor, args) => { guard(args);
        if (FORBIDDEN_PASSPORT_FIELDS.some(field => Object.prototype.hasOwnProperty.call(args, field)))
          throw new ToolError({ error: "memory_plan_anchor_only", hint: "Phase 1 accepts only plan_id; execution attempts and deviations belong to the next envelope slice" });
        if (args.plan_id !== undefined) requireUuid(args.plan_id, "plan_id", ToolError);
        return withEnvelope(c, actor, "observe-memory", args, async () => {
        guard(args);
        if (!KINDS.includes(args.kind)) throw new ToolError({ error: "memory_kind_invalid", allowed: KINDS });
        if (!SCOPES.includes(args.scope)) throw new ToolError({ error: "memory_scope_invalid", allowed: SCOPES });
        const text = validText(args.statement, "statement", ToolError);
        const evidence = args.evidence;
        if (!evidence || typeof evidence !== "object") throw new ToolError({ error: "memory_evidence_required" });
        const scope = sponsorFor(actor, ToolError);
        const ownerSlug = args.scope === "personal" ? scope.sponsor : null;
        if (args.scope === "personal" && !ownerSlug)
          throw new ToolError({ error: "personal_memory_scope_unavailable", hint: "a personal memory requires a verified partner sponsor" });
        // memory_item.owner_actor_id is a uuid FK to actor(id), NOT a slug.
        // Until 2026-09-11 the sponsor SLUG was bound straight into it, so
        // every personal-scope observation died at the driver with
        // 'invalid input syntax for type uuid: "joe"' and personal scope was
        // unreachable for both partners. The sibling column on this same
        // insert always resolved its slug; this one never did. Resolved here
        // rather than as an inline sub-select so a sponsor with no actor row
        // names itself instead of returning null and tripping the
        // scope/owner CHECK with a constraint error that explains nothing.
        let owner = null;
        if (ownerSlug !== null) {
          const ownerRow = await c.query("select id from actor where slug=$1", [ownerSlug]);
          if (!ownerRow.rows.length)
            throw new ToolError({ error: "actor_not_provisioned", slug: ownerSlug,
              hint: "the sponsoring partner has no actor row, so a personal memory cannot be owned" });
          owner = ownerRow.rows[0].id;
        }
        const confidence = args.confidence === undefined ? 0.5 : Number(args.confidence);
        if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)
          throw new ToolError({ error: "memory_confidence_invalid" });
        const tenant = organizationTenantForActor(actor);
        const anchor = await planAnchor(c, actor, args.plan_id, ToolError);
        const item = await c.query(
          `insert into memory_item (organization_tenant_id, work_request_id, work_request_version, plan_id, kind, statement, context, scope, owner_actor_id, observed_by_actor_id, confidence)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,(select id from actor where slug=$10),$11)
           returning id, kind, statement, context, scope, owner_actor_id, status, confidence, version, created_at`,
          [tenant, anchor?.work_request_id || null, anchor?.work_request_version || null, anchor?.plan_id || null,
           args.kind, text, args.context ? String(args.context).trim() : null, args.scope, owner, actor.slug, confidence]);
        const row = item.rows[0];
        const ev = await c.query(
          `insert into memory_evidence (memory_id, source_type, source_ref, observation, human_quote, observed_by_actor_id, provenance)
           values ($1,$2,$3,$4,$5,$6,$7) returning id`,
          [row.id, validText(evidence.source_type, "evidence.source_type", ToolError), evidence.source_ref ? String(evidence.source_ref) : null,
           validText(evidence.observation, "evidence.observation", ToolError), evidence.human_quote ? String(evidence.human_quote) : null,
           actor.id, JSON.stringify(evidence.provenance || {})]);
        await writeEvent(c, actor, "observe-memory", "memory_item", row.id,
          { new: { kind: row.kind, scope: row.scope, evidence_id: ev.rows[0].id, status: row.status }, idempotency_key: args.idempotency_key });
        return { ok: true, memory: row, evidence_id: ev.rows[0].id };
      }) },
    },

    "recall-memory": {
      // Runs on the writer connection so the sponsor setting is present. Both
      // partners may READ every memory (0579), but recall is where memory is
      // APPLIED, so the query below keeps it to shared rows and the caller's
      // own personal rows.
      writerConnection: true,
      description: "Recall promoted memories relevant to a context, combining shared memories with the authenticated partner's personal scope. Candidates require review and are never returned by autonomous recall; memory is context only and never authority.",
      inputSchema: { type: "object", properties: {
        query: { type: "string" }, context: { type: "string" }, limit: { type: "integer" },
      }, required: ["query"] },
      handler: async (c, actor, args) => {
        guard(args);
        const limit = recallLimit(args.limit, ToolError);
        const query = validText(args.query, "query", ToolError);
        const scope = sponsorFor(actor, ToolError);
        const r = await c.query(
          `select id, kind, statement, context, scope, confidence, status, version, created_at,
                  ts_rank(search_vector, plainto_tsquery('english',$1)) as relevance
             from memory_item
            where organization_tenant_id=$3 and status='promoted'
              and (scope='shared' or (scope='personal' and owner_actor_id=public.retrieval_visibility_actor_id($2)))
              and search_vector @@ plainto_tsquery('english',$1)
            order by (ts_rank(search_vector, plainto_tsquery('english',$1)) +
                      0.5 * ts_rank(to_tsvector('english', coalesce(context,'')), plainto_tsquery('english',coalesce($4,'')))) desc,
                     confidence desc, created_at desc limit $5`,
          [query, scope.sponsor, organizationTenantForActor(actor), args.context || "", limit]);
        return { ok: true, query: args.query, context: args.context || null, memories: r.rows, count: r.rows.length };
      },
    },

    "review-memory": {
      // Both partners may review any memory (Joe, 2026-09-24, migration 0579);
      // owner_actor_id says whose a personal memory is. Changing one stays
      // owner-only in promote/correct/forget and in the database.
      writerConnection: true,
      description: "Review one memory and its complete evidence/provenance before promotion. Either partner may review the other's personal memories (owner_actor_id names whose); only the owner can promote, correct or forget them. Tenant-scoped.",
      inputSchema: { type: "object", properties: { memory_id: { type: "string" } }, required: ["memory_id"] },
      handler: async (c, actor, args) => {
        guard(args);
        requireUuid(args.memory_id, "memory_id", ToolError);
        sponsorFor(actor, ToolError); // still refuses an unidentified caller
        const r = await c.query(
          `select m.*,
                  coalesce(jsonb_agg(to_jsonb(e) order by e.observed_at desc) filter (where e.id is not null),'[]'::jsonb) as evidence
             from memory_item m left join memory_evidence e on e.memory_id=m.id
            where m.id=$1 and m.organization_tenant_id=$2
            group by m.id`, [args.memory_id, organizationTenantForActor(actor)]);
        if (!r.rows.length) throw new ToolError({ error: "memory_not_found_or_forbidden" });
        const memory = r.rows[0]; const evidence = memory.evidence || []; delete memory.evidence;
        return { ok: true, memory, evidence, count: evidence.length };
      },
    },

    "promote-memory": {
      write: true,
      description: "Promote one candidate memory after a human confirms it. Requires a fresh memory version; promotion changes recall eligibility, never authority.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, memory_id: { type: "string" }, base_version: { type: "integer" }, reason: { type: "string" },
      }, required: ["idempotency_key", "memory_id", "base_version", "reason"] },
      handler: async (c, actor, args) => { guard(args); humanOnlyDirect(actor, ToolError); requireUuid(args.memory_id, "memory_id", ToolError); requireReason(args.reason, ToolError); return withEnvelope(c, actor, "promote-memory", args, async () => {
        const scope = sponsorFor(actor, ToolError);
        const r = await c.query(
          `update memory_item set status='promoted', version=version+1, promoted_by_actor_id=$4, promoted_at=now(), updated_at=now()
             where id=$1 and version=$2 and organization_tenant_id=$3 and status='candidate'
               and (scope='shared' or (scope='personal' and owner_actor_id=(select id from actor where slug=$5)))
           returning id, status, version`, [args.memory_id, args.base_version, organizationTenantForActor(actor), actor.id, scope.sponsor]);
        if (!r.rows.length) throw new ToolError({ error: "memory_version_conflict_or_not_candidate", hint: "read the memory again; promotion requires candidate status and the current version" });
        await writeEvent(c, actor, "promote-memory", "memory_item", args.memory_id, { new: { reason: args.reason, status: "promoted" }, idempotency_key: args.idempotency_key });
        return { ok: true, memory: r.rows[0] };
      }) },
    },

    "correct-memory": {
      write: true,
      description: "Correct a memory without rewriting history. The prior row becomes corrected and a new version carries the replacement statement.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, memory_id: { type: "string" }, base_version: { type: "integer" }, statement: { type: "string" }, reason: { type: "string" },
      }, required: ["idempotency_key", "memory_id", "base_version", "statement", "reason"] },
      handler: async (c, actor, args) => { guard(args); humanOnlyDirect(actor, ToolError); requireUuid(args.memory_id, "memory_id", ToolError); validText(args.statement, "statement", ToolError); requireReason(args.reason, ToolError); return withEnvelope(c, actor, "correct-memory", args, async () => {
        const text = validText(args.statement, "statement", ToolError);
        const scope = sponsorFor(actor, ToolError);
        const tenant = organizationTenantForActor(actor);
        const r = await c.query(
          `update memory_item set status='corrected', corrected_by_actor_id=$3, correction_reason=$4, corrected_at=now(), version=version+1, updated_at=now()
             where id=$1 and version=$2 and organization_tenant_id=$5 and status in ('candidate','promoted')
               and (scope='shared' or (scope='personal' and owner_actor_id=(select id from actor where slug=$6))) returning id, organization_tenant_id, kind, context, scope, owner_actor_id, work_request_id, work_request_version, plan_id, version`,
          [args.memory_id, args.base_version, actor.id, validText(args.reason, "reason", ToolError), tenant, scope.sponsor]);
        if (!r.rows.length) throw new ToolError({ error: "memory_version_conflict" });
        const old = r.rows[0];
        const successor = await c.query(
          `insert into memory_item (organization_tenant_id, work_request_id, work_request_version, plan_id, kind, statement, context, scope, owner_actor_id, observed_by_actor_id, confidence, predecessor_id, lineage_root_id)
           select organization_tenant_id,work_request_id,work_request_version,plan_id,$2,$3,context,scope,owner_actor_id,$4,confidence,id,coalesce(lineage_root_id,id)
             from memory_item where id=$1 returning id, kind, statement, context, scope, owner_actor_id, status, confidence, version, predecessor_id`,
          [args.memory_id, old.kind, text, actor.id]);
        const correctionEvidence = await c.query(
          `insert into memory_evidence (memory_id, source_type, source_ref, observation, observed_by_actor_id, provenance)
           values ($1,'human_correction',$2,$3,$4,$5) returning id`,
          [successor.rows[0].id, args.memory_id, args.reason, actor.id,
           JSON.stringify({ predecessor_id: args.memory_id, predecessor_version: old.version })]);
        await writeEvent(c, actor, "correct-memory", "memory_item", args.memory_id, { old: { statement: "preserved_in_prior_revision", status: old.status }, new: { successor_id: successor.rows[0].id, reason: args.reason, evidence_id: correctionEvidence.rows[0]?.id }, cause: "human_correction", idempotency_key: args.idempotency_key });
        return { ok: true, memory: successor.rows[0], predecessor_id: args.memory_id, evidence_id: correctionEvidence.rows[0]?.id };
      }) },
    },

    "forget-memory": {
      write: true,
      description: "Forget a memory by suppressing it from recall while retaining its evidence and audit history. This is not a DELETE.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, memory_id: { type: "string" }, base_version: { type: "integer" }, reason: { type: "string" },
      }, required: ["idempotency_key", "memory_id", "base_version", "reason"] },
      handler: async (c, actor, args) => { guard(args); humanOnlyDirect(actor, ToolError); requireUuid(args.memory_id, "memory_id", ToolError); requireReason(args.reason, ToolError); return withEnvelope(c, actor, "forget-memory", args, async () => {
        const scope = sponsorFor(actor, ToolError);
        const r = await c.query(
          `update memory_item set status='forgotten', forgotten_by_actor_id=$4, forget_reason=$5, forgotten_at=now(), version=version+1, updated_at=now()
             where id=$1 and version=$2 and organization_tenant_id=$3 and status in ('candidate','promoted','corrected')
               and (scope='shared' or (scope='personal' and owner_actor_id=(select id from actor where slug=$6))) returning id, status, version`,
          [args.memory_id, args.base_version, organizationTenantForActor(actor), actor.id, validText(args.reason, "reason", ToolError), scope.sponsor]);
        if (!r.rows.length) throw new ToolError({ error: "memory_version_conflict" });
        await writeEvent(c, actor, "forget-memory", "memory_item", args.memory_id, { new: { reason: args.reason, status: "forgotten" }, idempotency_key: args.idempotency_key });
        return { ok: true, memory: r.rows[0] };
      }) },
    },
  };
}
