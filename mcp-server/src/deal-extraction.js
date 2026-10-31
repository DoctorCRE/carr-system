// Bind proposal-only extraction jobs to stored deal attachments and review batches.
import { authorizationClassForActor, organizationTenantForActor } from './identity.js';
import { factObservationTools, validateFactObservations } from './fact-observations.ts';
import { ToolError } from './tool-error.ts';

const uuid = { type: 'string', pattern: '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' };
const text = maxLength => ({ type: 'string', minLength: 1, maxLength });
const hash = { type: 'string', pattern: '^[0-9a-f]{64}$' };
const factKeys = ['base_rent_per_sf', 'landlord_ti_per_sf', 'term_months', 'free_rent_months', 'rent_schedule'];
const contract = { idempotency_key: uuid, entity_ref: uuid, document_ref: uuid,
  document_type: { enum: ['loi', 'lease', 'counter', 'amendment'] }, model: { enum: ['haiku', 'sonnet'] }, extractor_version: { enum: ['deal-text.v1', 'deal-regions.v2'] } };
const object = properties => ({ type: 'object', additionalProperties: false, properties, required: Object.keys(properties) });
const money = { type: 'string', pattern: '^(0|[1-9][0-9]*)(\\.[0-9]+)?$' };
const array = (items, maxItems, minItems = 0) => ({ type: 'array', items, minItems, maxItems });
const source = object({ document_ref: uuid, entity_ref: uuid, organization_tenant_id: { const: 'carr-internal' },
  content_hash: hash, document_family_id: text(200), lineage_verified: { const: false } });
const receiptSchema = object({ schema_version: { enum: ['deal-extraction.v1', 'deal-extraction.v2'] }, contract: object(contract), source,
  passes: array(object({ phase: { enum: ['extract', 'ground'] }, output_sha256: hash,
    model: { anyOf: [text(300), { type: 'null' }] } }), 2, 2),
  attempts: array(object({ phase: { enum: ['extract', 'ground'] }, attempt: { type: 'integer', minimum: 1, maximum: 2 },
    status: { enum: ['completed', 'failed'] }, cost_usd: { anyOf: [money, { type: 'null' }] } }), 4),
  cost_usd: money, cost_complete: { type: 'boolean' }, grounding_changes: array({ enum: factKeys }, 5),
  abstentions: array(object({ fact_key: { enum: factKeys }, status: { enum: ['not_present', 'unreadable'] },
    reason: { enum: ['absent', 'no_text_layer', 'unsupported_schedule', 'unsupported_ocr', 'ambiguous', 'ungrounded', 'over_budget', 'model_failed', 'ocr_disagreement'] } }), 5),
  proposed_effects: array({ enum: factKeys }, 5), canonical: { const: false } });

function jobOnly(actor) {
  if (['verified_partner', 'probe_agent', 'review_agent'].includes(authorizationClassForActor(actor)))
    throw new ToolError({ error: 'not_in_profile' });
}
function result(out) {
  if (!out.ok) throw new ToolError({ error: out.error });
  return out;
}
function jobContract(args) {
  return Object.fromEntries(Object.keys(contract).map(key => [key, args[key]]));
}

export function dealExtractionTools() {
  return {
    'read-deal-extraction-source': {
      write: false,
      description: 'Read the exact undeleted stored PDF attachment binding for a proposal-only extraction job, including tenant, deal, content hash and object locator. An authenticated R2 read must verify the bytes separately. Source family is conservative byte identity; derivative lineage is unverified. Returns the prior receipt for the same document, hash, extractor version and model. No bytes, model call, writes or promotions.',
      inputSchema: object(contract),
      handler: async (c, actor, args) => {
        jobOnly(actor);
        return result((await c.query('select ops.read_deal_extraction_source($1,$2,$3::jsonb) r',
          [organizationTenantForActor(actor), actor.id, JSON.stringify(args)])).rows[0].r);
      },
    },
    'record-deal-extraction': {
      write: true,
      description: 'Atomically retain one typed regional extraction job receipt and record its scalar and schedule proposals in the fact review queue. Rebinds stored document, deal, tenant and hash; overwrites source family from byte identity and refuses auto-accept and foreign sources. Version 2 accounts for five fields which must be accounted for by proposals or distinct abstentions. Retains model/cost/retry/pass digests, grounding changes and proposed effects. Reruns return the first completed receipt for the same document, hash, extractor version and model. Citation spans are capped at 600 characters and must contain the value; source byte grounding still requires the authenticated client. Never promotes facts. OCR never qualifies for automatic promotion. Schedules retain periods, expense bases, escalations and field provenance.',
      inputSchema: object({ ...contract, content_hash: hash, receipt: receiptSchema,
        observations: array(factObservationTools()['record-fact-observations'].inputSchema.properties.observations.items, 5) }),
      handler: async (c, actor, args) => {
        jobOnly(actor);
        const invalid = () => { throw new ToolError({ error: 'validation_error' }); };
        const request = jobContract(args);
        if (Object.keys(contract).some(key => args.receipt.contract[key] !== request[key])) invalid();
        const accounted = [...args.observations.map(o => o.fact_key), ...args.receipt.abstentions.map(a => a.fact_key)];
        if (accounted.length !== (args.extractor_version === 'deal-text.v1' ? 4 : 5) || new Set(accounted).size !== accounted.length || accounted.some(key => !(args.extractor_version === 'deal-text.v1' ? factKeys.slice(0, 4) : factKeys).includes(key))) invalid();
        if (args.receipt.abstentions.some(a => (a.status === 'not_present') !== (a.reason === 'absent'))) invalid();
        if (args.observations.length) validateFactObservations({ entity_ref: args.entity_ref, observations: args.observations });
        if (args.receipt.passes[0].phase !== 'extract' || args.receipt.passes[1].phase !== 'ground') invalid();
        if (args.receipt.proposed_effects.length !== args.observations.length
          || args.observations.some(o => !args.receipt.proposed_effects.includes(o.fact_key))) invalid();
        for (const o of args.observations) {
          const citation = o.source.cited_text;
          if (o.fact_key !== 'rent_schedule' && (typeof citation !== 'string' || citation.length > 600
            || !(citation.replaceAll(',', '').match(/[0-9]+(?:\.[0-9]+)?/g) || []).includes(o.value))) invalid();
          if (o.confidence_tier !== 'human_review' || !['text_layer', 'ocr'].includes(o.method) || o.source.document_ref !== args.document_ref
            || o.source.content_hash !== args.content_hash || o.extractor_version !== args.extractor_version
            || o.extraction?.model !== args.model || o.extraction?.pass !== 'ground') invalid();
        }
        return result((await c.query('select ops.record_deal_extraction($1,$2,$3::jsonb) r',
          [organizationTenantForActor(actor), actor.id, JSON.stringify(args)])).rows[0].r);
      },
    },
  };
}
