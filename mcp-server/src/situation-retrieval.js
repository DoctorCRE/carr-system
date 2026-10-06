// Deterministic situation-language retrieval for WR-AI-006.
//
// The database owns the one production ranking path.  The small pure scorer
// below mirrors its open arithmetic for property tests and offline scorecard
// inspection; it never rewrites a query or generates answer text.

import { personalScopeForActor } from "./identity.js";
import { ToolError } from "./tool-error.js";

const POLICIES = new Set(["lexical-dominant-v1", "coequal-normalized-v1"]);


export function normalizeSituationPhrase(value) {
  return String(value ?? "").trim().toLowerCase().replace(/\s+/g, " ");
}


function bounded(value) {
  const number = Number(value || 0);
  return Math.max(0, Math.min(1, Number.isFinite(number) ? number : 0));
}


function rounded(value) {
  return Number(value.toFixed(9));
}


export function rankSituationCandidates(candidates, policyId) {
  if (!POLICIES.has(policyId)) throw new Error(`unknown retrieval policy: ${policyId}`);
  const prepared = candidates.map(candidate => {
    const lexical = bounded(candidate.lexical_score);
    const contributors = [...(candidate.concept_matches || [])].map(row => ({
      phrase_id: String(row.phrase_id),
      concept_id: String(row.concept_id),
      mapping_id: String(row.mapping_id),
      phrase_strength: bounded(row.phrase_strength),
      phrase_weight: bounded(row.phrase_weight),
      mapping_weight: bounded(row.mapping_weight),
      contribution: rounded(bounded(row.phrase_strength) * bounded(row.phrase_weight) * bounded(row.mapping_weight)),
    })).sort((a, b) => b.contribution - a.contribution ||
      a.phrase_id.localeCompare(b.phrase_id) || a.mapping_id.localeCompare(b.mapping_id));
    const concept = contributors.reduce((maximum, row) => Math.max(maximum, row.contribution), 0);
    return { candidate, lexical, concept, contributors };
  });
  const maxLexical = Math.max(0, ...prepared.map(row => row.lexical));
  const maxConcept = Math.max(0, ...prepared.map(row => row.concept));
  const hits = prepared.map(({ candidate, lexical, concept, contributors }) => {
    let final;
    if (policyId === "lexical-dominant-v1") {
      final = 0.75 * lexical + 0.25 * concept;
    } else {
      const normLexical = maxLexical ? lexical / maxLexical : 0;
      const normConcept = maxConcept ? concept / maxConcept : 0;
      final = normLexical + normConcept + (lexical > 0 && concept > 0 ? 0.15 : 0);
    }
    return {
      ...candidate,
      lexical_score: rounded(lexical),
      concept_score: rounded(concept),
      final_score: rounded(final),
      provenance: {
        policy_id: policyId,
        lexical_score: rounded(lexical),
        concept_score: rounded(concept),
        final_score: rounded(final),
        contributors,
      },
    };
  });
  return hits.sort((a, b) => b.final_score - a.final_score ||
    b.concept_score - a.concept_score || b.lexical_score - a.lexical_score ||
    String(a.section_key).localeCompare(String(b.section_key)));
}


function nonEmpty(value) {
  return typeof value === "string" && value.trim().length > 0;
}


function validateKey(value, ToolError) {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(String(value || "")))
    throw new ToolError({ error: "retrieval_concept_key_invalid",
      hint: "concept_key is stable kebab-case human language" });
}


function validatePhrase(value, ToolError) {
  const normalized = normalizeSituationPhrase(value);
  if (normalized.split(" ").filter(Boolean).length < 2)
    throw new ToolError({ error: "retrieval_phrase_too_short",
      hint: "one-word phrases are too ambiguous; use at least two real-world words" });
  return normalized;
}


async function insertProposal(c, actor, args, proposalType, payload) {
  const result = await c.query(
    `insert into retrieval_proposal
       (proposal_type, payload, reason, proposer_id, status, idempotency_key)
     values ($1, $2::jsonb, $3, $4, 'pending', $5::uuid)
     on conflict (idempotency_key) do nothing
     returning id, proposal_type, status, version, created_at`,
    [proposalType, JSON.stringify(payload), args.reason.trim(), actor.id, args.idempotency_key]);
  if (result.rows.length) return { ok: true, proposal: result.rows[0] };
  const prior = await c.query(
    `select id, proposal_type, status, version, created_at
       from retrieval_proposal where idempotency_key=$1::uuid`, [args.idempotency_key]);
  return { ok: true, proposal: prior.rows[0], replay: true };
}


export async function retrievalVisibilityActorId(c, actor) {
  const scope = personalScopeForActor(actor);
  if (scope.status === "error")
    throw new ToolError({ error: "retrieval_scope_refused", reason: scope.error });
  if (scope.status === "none") return null;

  // The runtime actor and the human whose personal brain it may read are
  // separate identities. joe-local/codex/hermes UUIDs must never become the
  // visibility selector. The sponsor was derived by the authenticated server
  // door; resolve that exact human row here and nowhere from tool arguments.
  // Through a definer function, NOT the table. carr_reader has no grant on
  // public.actor and must not get one; this query ran directly on the read
  // connection from 2026-08-17 and returned "permission denied for table actor"
  // for every doctrine search until migration 0223. The function hard-filters
  // to active humans, so the reader resolves a sponsor without being able to
  // read, enumerate, or select anything else from the identity table.
  const found = await c.query(
    `select retrieval_visibility_actor_id($1) as id where retrieval_visibility_actor_id($1) is not null`,
    [scope.sponsor],
  );
  // Names the row count, because 0 and 2 are different production faults and a
  // bare throw made both arrive as "internal error" with nothing to act on.
  if (found.rows.length !== 1)
    throw new ToolError({
      error: "retrieval_scope_refused",
      reason: "sponsor_not_active_human",
      sponsor: scope.sponsor,
      matching_rows: found.rows.length,
    });
  return found.rows[0].id;
}


export async function searchDoctrineSituations(c, actor, args) {
  const actorId = await retrievalVisibilityActorId(c, actor);
  const policy = args.policy_id || null;
  if (policy && !POLICIES.has(policy)) throw new Error(`unknown retrieval policy: ${policy}`);
  // The live verb always allows the zero-hit fallback (loop 518): when the
  // strict every-word pass finds nothing, the ranker retries with any-word
  // matching and marks each such row's provenance with fallback:true. The
  // argument defaults to OFF in the database, so the golden gate and every
  // other five-argument caller keep exact strict semantics — the deliberate
  // negative cases (expect-no-hits, near-miss) are gate properties of the
  // strict lane, not of this best-effort lane.
  const result = await c.query(
    `select * from search_doctrine_situations($1, $2::uuid, $3::text[], $4, $5, $6)`,
    [args.q, actorId, args.content_classes || null, args.limit || 20, policy, true]);
  const policyId = result.rows[0]?.provenance?.policy_id || policy || "default";
  const fallbackUsed = result.rows.length > 0 && result.rows.every(r => r.provenance?.fallback === true);

  // The query log is written HERE, on the write credential, and never awaited.
  // It used to ride inside search_doctrine_situations as a data-modifying CTE,
  // which made this read a write and killed every call wherever writes are
  // refused (migration 0223). The log is worth keeping — curation reads it to
  // find golden misses — but it is worth strictly less than the answer, so a
  // failure to log must never reach the caller.
  if (c.sideWrite) {
    const bands = { high: 0, medium: 0, low: 0 };
    for (const row of result.rows) {
      const score = Number(row.final_score);
      if (score >= 0.75) bands.high += 1;
      else if (score >= 0.25) bands.medium += 1;
      else bands.low += 1;
    }
    // A query only answered by the fallback pass was still a strict miss, and
    // the miss index is what curation mines — logging it as a hit would hide
    // exactly the queries the phrase-proposal loop exists to learn from.
    c.sideWrite(
      "select log_retrieval_query($1, $2, $3::uuid[], $4::jsonb, $5, $6, $7)",
      [args.q, result.rows.length, result.rows.map(r => r.section_id),
       JSON.stringify(bands), policyId,
       result.rows[0]?.provenance?.policy_version ?? null,
       result.rows.length > 0 && !fallbackUsed]);
  }

  return {
    ok: true,
    query: args.q,
    policy_id: policyId,
    fallback: fallbackUsed,
    hits: result.rows,
    total: result.rows.length,
    generated_text: false,
  };
}


export function situationRetrievalTools({ withEnvelope, writeEvent, ToolError }) {
  const proposalBase = {
    write: true,
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, reason: { type: "string" },
    }, required: ["idempotency_key", "reason"] },
  };
  return {
    "propose-retrieval-concept": {
      ...proposalBase,
      description: "Propose a human-named operating-situation concept. Machine-callable; approval remains human-only.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, reason: { type: "string" },
        concept_key: { type: "string" }, label: { type: "string" },
        definition: { type: "string" }, review_after: { type: "string" },
      }, required: ["idempotency_key", "reason", "concept_key", "label", "definition"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "propose-retrieval-concept", args, async () => {
        validateKey(args.concept_key, ToolError);
        if (!nonEmpty(args.label) || !nonEmpty(args.definition) || !nonEmpty(args.reason))
          throw new ToolError({ error: "retrieval_proposal_incomplete" });
        return insertProposal(c, actor, args, "concept", {
          concept_key: args.concept_key, label: args.label.trim(),
          definition: args.definition.trim(), review_after: args.review_after || null,
        });
      }),
    },
    "propose-retrieval-phrase": {
      ...proposalBase,
      description: "Propose an approved real-world phrase for an existing retrieval concept.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, reason: { type: "string" },
        concept_key: { type: "string" }, phrase: { type: "string" },
        match_mode: { type: "string", enum: ["exact", "fts", "trgm"] },
        min_similarity: { type: "number" }, weight: { type: "number" },
        source: { type: "string", enum: ["golden_miss", "no_hit_log", "session_proposal", "manual"] },
        source_ref: { type: "string" },
      }, required: ["idempotency_key", "reason", "concept_key", "phrase", "match_mode", "weight", "source", "source_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "propose-retrieval-phrase", args, async () => {
        validateKey(args.concept_key, ToolError);
        validatePhrase(args.phrase, ToolError);
        if (!nonEmpty(args.reason) || !nonEmpty(args.source_ref))
          throw new ToolError({ error: "retrieval_proposal_incomplete" });
        return insertProposal(c, actor, args, "phrase", {
          concept_key: args.concept_key, phrase: args.phrase.trim(),
          match_mode: args.match_mode, min_similarity: args.min_similarity ?? 0.35,
          weight: args.weight, source: args.source, source_ref: args.source_ref.trim(),
        });
      }),
    },
    "propose-retrieval-mapping": {
      ...proposalBase,
      description: "Propose a concept-to-current-doctrine-section mapping with a required rationale.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, reason: { type: "string" },
        concept_key: { type: "string" }, section_address: { type: "string" },
        role: { type: "string", enum: ["governs", "supports"] },
        weight: { type: "number" }, rationale: { type: "string" },
      }, required: ["idempotency_key", "reason", "concept_key", "section_address", "role", "weight", "rationale"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "propose-retrieval-mapping", args, async () => {
        validateKey(args.concept_key, ToolError);
        if (!/^.+#[^#]+$/.test(args.section_address) || !nonEmpty(args.rationale))
          throw new ToolError({ error: "retrieval_mapping_incomplete",
            hint: "section_address is doc-slug#section-key and rationale is required" });
        return insertProposal(c, actor, args, "mapping", {
          concept_key: args.concept_key, section_address: args.section_address,
          role: args.role, weight: args.weight, rationale: args.rationale.trim(),
        });
      }),
    },
    "propose-retrieval-retirement": {
      ...proposalBase,
      description: "Propose retirement of a concept, phrase, or mapping without mutating active curation.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, reason: { type: "string" },
        target_type: { type: "string", enum: ["concept", "phrase", "mapping"] },
        target_id: { type: "string" }, base_version: { type: "integer" },
      }, required: ["idempotency_key", "reason", "target_type", "target_id", "base_version"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "propose-retrieval-retirement", args, async () =>
        insertProposal(c, actor, args, "retire", {
          target_type: args.target_type, target_id: args.target_id, base_version: args.base_version,
        })),
    },
    "approve-retrieval-proposals": {
      write: true,
      description: "HUMAN-ONLY atomic batch approval. Promotes pending rows, reruns the golden suite, and rolls back on any regression.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        proposal_ids: { type: "array", items: { type: "string" }, maxItems: 50 },
        base_versions: { type: "object" }, golden_suite_digest: { type: "string" },
      }, required: ["idempotency_key", "proposal_ids", "base_versions", "golden_suite_digest"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "approve-retrieval-proposals", args, async () => {
        const selected = await c.query(
          `select * from retrieval_proposal where id = any($1::uuid[])
            order by case proposal_type when 'concept' then 1 when 'phrase' then 2
                         when 'mapping' then 3 else 4 end, created_at, id
            for update`, [args.proposal_ids]);
        if (selected.rows.length !== args.proposal_ids.length)
          throw new ToolError({ error: "retrieval_proposal_not_found" });
        for (const proposal of selected.rows) {
          if (proposal.status !== "pending" || Number(args.base_versions[proposal.id]) !== Number(proposal.version))
            throw new ToolError({ error: "version_conflict", proposal_id: proposal.id,
              current_version: Number(proposal.version) });
          await c.query(`select promote_retrieval_proposal($1::uuid, $2::uuid)`, [proposal.id, actor.id]);
        }
        const gate = await c.query(`select * from assert_situation_retrieval_golden($1)`, [args.golden_suite_digest]);
        const failed = gate.rows.filter(row => row.status !== "pass");
        if (failed.length) throw new ToolError({ error: "retrieval_golden_regression", failed });
        await writeEvent(c, actor, "approve-retrieval-proposals", "retrieval_proposal", args.proposal_ids[0], {
          proposal_ids: args.proposal_ids, golden_suite_digest: args.golden_suite_digest,
          idempotency_key: args.idempotency_key,
        });
        return { ok: true, approved: args.proposal_ids, golden_cases: gate.rows.length };
      }),
    },
    "retire-retrieval-curation": {
      write: true,
      description: "HUMAN-ONLY guarded retirement of approved retrieval curation.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, target_type: { type: "string", enum: ["concept", "phrase", "mapping"] },
        target_id: { type: "string" }, base_version: { type: "integer" }, reason: { type: "string" },
        golden_suite_digest: { type: "string" },
      }, required: ["idempotency_key", "target_type", "target_id", "base_version", "reason", "golden_suite_digest"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "retire-retrieval-curation", args, async () => {
        if (!nonEmpty(args.reason)) throw new ToolError({ error: "retirement_reason_required" });
        const result = await c.query(`select * from retire_retrieval_curation($1, $2::uuid, $3, $4::uuid, $5)`,
          [args.target_type, args.target_id, args.base_version, actor.id, args.reason.trim()]);
        if (!result.rows.length) throw new ToolError({ error: "version_conflict" });
        const gate = await c.query(`select * from assert_situation_retrieval_golden($1)`, [args.golden_suite_digest]);
        const failed = gate.rows.filter(row => row.status !== "pass");
        if (failed.length) throw new ToolError({ error: "retrieval_golden_regression", failed });
        return { ok: true, retired: result.rows[0] };
      }),
    },
  };
}
