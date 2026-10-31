// Partner review of stored deal terms and their evidence.
import { authorizationClassForActor, organizationTenantForActor } from './identity.js';
import { CONTRACTS, humanFactSchema, validateFactObservations } from './fact-observations.ts';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs } from './verb-contract.ts';

const uuid = { type: 'string', pattern: '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' };
const entity = { type: 'string', minLength: 1, maxLength: 200 };
const fact = { type: 'string', enum: Object.keys(CONTRACTS) };
const resolutionProperties = {
  idempotency_key: uuid, entity_ref: entity, fact_key: fact,
  base_version: { type: 'integer', minimum: 0, maximum: 0x7ffffffe },
  review_version: { type: 'string', pattern: '^[0-9a-f]{64}$' },
  decision: { type: 'string', enum: ['approve', 'correct', 'reject'] },
  observation_id: uuid, correction: humanFactSchema, reason: { type: 'string', minLength: 1, maxLength: 2000 },
};
const resolutionRequired = ['idempotency_key', 'entity_ref', 'fact_key', 'base_version', 'review_version', 'decision', 'reason'];

function partner(actor: Actor) {
  if (authorizationClassForActor(actor) !== 'verified_partner')
    throw new ToolError({ error: 'human_only_verb_requires_verified_partner' });
}
function validateHuman(entity_ref: string, observation: VerbInputs['record-human-fact']['observation']) {
  validateFactObservations({ entity_ref, observations: [{ ...observation, id: 'human', method: 'text_layer',
    extractor_version: 'human-entry.v1', confidence_tier: 'human_review' }] });
}
function result(out: Record<string, unknown>) {
  if (!out.ok) throw new ToolError({ error: typeof out.error === 'string' ? out.error : 'fact_review_refused', current_version: out.current_version });
  return { ...out, schema_version: 'fact-review.v1' };
}
export function factReviewTools() {
  return {
    'draft-terms-comparison': {
      write: false,
      description: 'Draft a comparison of canonical lease terms for two or more entities. Refuses every missing or pending field. Sends and stores nothing.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        entity_refs: { type: 'array', minItems: 2, maxItems: 20, uniqueItems: true, items: entity },
      }, required: ['entity_refs'] },
      handler: async (c: Db, actor: Actor, args: { entity_refs: string[] }) => {
        const rows = (await c.query(`select e.entity_ref, ops.list_fact_review($1,e.entity_ref,100,null,true) result
          from unnest($2::text[]) with ordinality e(entity_ref,ordinal) order by e.ordinal`,
          [organizationTenantForActor(actor), args.entity_refs])).rows;
        const issues: { entity_ref: string; fact_key: string; reason: string }[] = [];
        const deals = rows.map(({ entity_ref, result }: any) => {
          const fields = Object.keys(CONTRACTS).map(fact_key => {
            const item = result.items.find((entry: any) => entry.fact_key === fact_key);
            const canonical = item?.canonical_fact;
            if (item?.pending) issues.push({ entity_ref, fact_key, reason: item.reasons.includes('conflict') ? 'open_conflict' : 'pending_review' });
            if (!canonical || (canonical.normalized_value == null && canonical.normalized_schedule == null))
              issues.push({ entity_ref, fact_key, reason: 'missing_canonical' });
            if (!canonical) return null;
            const citations = item.observations.filter((observation: any) => canonical.observation_ids.includes(observation.id)).map((observation: any) => observation.source);
            if (citations.length !== canonical.observation_ids.length) {
              issues.push({ entity_ref, fact_key, reason: 'missing_citation' });
              return null;
            }
            return { fact_key, value: canonical.normalized_schedule ?? canonical.normalized_value,
              unit: canonical.canonical_unit, expense_basis: canonical.expense_basis,
              fact_id: canonical.id, version: canonical.version, citations };
          });
          return { entity_ref, fields };
        });
        if (issues.length) throw new ToolError({ error: 'unresolved_facts', facts: issues });
        const text = deals.map(({ entity_ref, fields }: any) => [entity_ref, ...fields.map((field: any) =>
          `${field.fact_key}: ${JSON.stringify(field.value)} ${field.unit}${field.expense_basis ? ` (${field.expense_basis})` : ''} [fact ${field.fact_id} v${field.version}; sources ${JSON.stringify(field.citations)}]`)].join('\n')).join('\n\n');
        return { ok: true, schema_version: 'terms-comparison-draft.v1', draft: true, text, deals };
      },
    },
    'record-human-fact': {
      write: true, humanOnly: true,
      description: 'Record a distinct human-authored term from an existing, undeleted deal attachment. entity_ref is the deal UUID and source.document_ref is its attachment UUID. The server binds the stored hash and author; supplied text is a human transcription, not verified document content. Returns the observation without promotion. The extraction recording verb remains job-only.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: uuid, entity_ref: entity, observation: humanFactSchema,
      }, required: ['idempotency_key', 'entity_ref', 'observation'] },
      handler: async (c: Db, actor: Actor, args: VerbInputs['record-human-fact']) => {
        partner(actor);
        validateHuman(args.entity_ref, args.observation);
        return result((await c.query('select ops.record_human_fact($1,$2,$3,$4,$5::jsonb) r',
          [organizationTenantForActor(actor), actor.id, args.idempotency_key, args.entity_ref, JSON.stringify(args.observation)])).rows[0].r);
      },
    },
    'list-fact-review': {
      write: false,
      description: 'Read one durable review item per entity-field needing review for conflict, verify-band, OCR or human review. Returns all cited observations, canonical history, review history, canonical_version and review_version. include_resolved also returns settled fields for term/history display. Excerpts expire to null with a retained digest. Keyset cursor binds entity filter and include_resolved; concurrent arrivals before the cursor appear on a fresh scan.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        entity_ref: entity, limit: { type: 'integer', minimum: 1, maximum: 100 },
        cursor: { type: 'string', minLength: 1, maxLength: 2000 }, include_resolved: { type: 'boolean' },
      } },
      handler: async (c: Db, actor: Actor, args: VerbInputs['list-fact-review']) => {
        if (args.cursor) {
          try { JSON.parse(args.cursor); } catch { throw new ToolError({ error: 'invalid_cursor' }); }
        }
        return result((await c.query('select ops.list_fact_review($1,$2,$3,$4::jsonb,$5) r',
          [organizationTenantForActor(actor), args.entity_ref ?? null, args.limit ?? 50,
            args.cursor ?? null, args.include_resolved ?? false])).rows[0].r);
      },
    },
    'resolve-fact-review': {
      write: true, humanOnly: true,
      description: 'Approve one stored observation, correct with a distinct human-authored observation from a stored deal attachment, or reject the current field review with a reason. Preserves all evidence and append-only decision and canonical histories. Pass both canonical base_version and review_version from list-fact-review; stale evidence or decisions refuse. Reject settles the displayed evidence set without changing canonical. New observations reopen review. Retries reuse the idempotency key.',
      inputSchema: { type: 'object', additionalProperties: false, properties: resolutionProperties,
        required: resolutionRequired, anyOf: ['approve', 'correct', 'reject'].map(decision => ({
          type: 'object', additionalProperties: false, properties: {
            ...Object.fromEntries(Object.entries(resolutionProperties).filter(([key]) =>
              key !== 'observation_id' && key !== 'correction'
              || key === 'observation_id' && decision === 'approve'
              || key === 'correction' && decision === 'correct')),
            decision: { type: 'string', enum: [decision] },
          }, required: [...resolutionRequired, ...(decision === 'approve' ? ['observation_id'] : []),
            ...(decision === 'correct' ? ['correction'] : [])],
        })),
      },
      handler: async (c: Db, actor: Actor, args: VerbInputs['resolve-fact-review']) => {
        partner(actor);
        if (!args.reason.trim()) throw new ToolError({ error: 'validation_error' });
        if (args.decision === 'correct' && args.correction) {
          validateHuman(args.entity_ref, args.correction);
          if (args.correction.fact_key !== args.fact_key) throw new ToolError({ error: 'validation_error' });
        }
        return result((await c.query('select ops.resolve_fact_review($1,$2,$3::jsonb) r',
          [organizationTenantForActor(actor), actor.id, JSON.stringify(args)])).rows[0].r);
      },
    },
  };
}
