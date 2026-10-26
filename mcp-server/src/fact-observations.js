import { authorizationClassForActor, organizationTenantForActor } from './identity.js';

export const CONTRACTS = {
  base_rent_per_sf: { units: { 'USD/SF/year': 1, 'USD/SF/month': 12 }, unit: 'USD/SF/year', expenseBasis: true },
  landlord_ti_per_sf: { units: { 'USD/SF': 1 }, unit: 'USD/SF' },
  term_months: { units: { months: 1, years: 12 }, unit: 'months' },
  free_rent_months: { units: { months: 1 }, unit: 'months' },
};
const EXPENSE_BASES = ['nnn', 'modified_gross', 'full_service_gross', 'gross'];
const DECIMAL = /^(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?$/;
const CONTENT_HASH = /^[a-fA-F0-9]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const text = maxLength => ({ type: 'string', minLength: 1, maxLength });
const uuid = { type: 'string', pattern: UUID.source, minLength: 36, maxLength: 36 };
const sourceSchema = { type: 'object', additionalProperties: false, properties: {
  document_ref: text(200), page_start: { type: 'integer', minimum: 1, maximum: 100000 },
  page_end: { type: 'integer', minimum: 1, maximum: 100000 }, cited_text: text(4000),
  content_hash: { type: 'string', pattern: CONTENT_HASH.source, minLength: 64, maxLength: 64 },
  document_family_id: text(200),
  derivation_chain: { type: 'array', maxItems: 20, items: text(200),
    description: 'Caller-asserted upstream document-family references, not verified lineage.' },
}, required: ['document_ref', 'page_start', 'page_end', 'cited_text'] };
const observationProperties = {
  id: text(100), fact_key: { type: 'string', enum: Object.keys(CONTRACTS) },
  value: { type: 'string', pattern: DECIMAL.source, maxLength: 49 },
  unit: { type: 'string', enum: [...new Set(Object.values(CONTRACTS).flatMap(c => Object.keys(c.units)))] },
  expense_basis: { type: 'string', enum: EXPENSE_BASES }, source: sourceSchema,
  method: { type: 'string', enum: ['text_layer', 'ocr'] }, extractor_version: text(100),
  extraction: { type: 'object', additionalProperties: false,
    description: 'Caller-asserted extraction pass and model, not verified failure-domain diversity.',
    properties: { pass: text(100), model: text(100) }, required: ['pass', 'model'] },
  confidence_tier: { type: 'string', enum: ['auto_accept', 'verify_band', 'human_review'] },
};
const observationRequired = ['id', 'fact_key', 'value', 'unit', 'source', 'method', 'extractor_version', 'confidence_tier'];
const observationSchema = { anyOf: Object.entries(CONTRACTS).map(([key, contract]) => {
  const { expense_basis, ...properties } = observationProperties;
  return { type: 'object', additionalProperties: false, properties: { ...properties,
    fact_key: { type: 'string', enum: [key] }, unit: { type: 'string', enum: Object.keys(contract.units) },
    ...(contract.expenseBasis ? { expense_basis } : {}),
  }, required: [...observationRequired, ...(contract.expenseBasis ? ['expense_basis'] : [])] };
}) };

function normalize(value, multiplier) {
  const [whole, fractional = ''] = value.split('.');
  const scaled = (BigInt(whole + fractional) * BigInt(multiplier)).toString().padStart(fractional.length + 1, '0');
  if (!fractional.length) return scaled;
  return `${scaled.slice(0, -fractional.length)}.${scaled.slice(-fractional.length)}`.replace(/0+$/, '').replace(/\.$/, '');
}

function validate(args, ToolError, extra = []) {
  const invalid = () => { throw new ToolError({ error: 'validation_error' }); };
  const object = (value, allowed, required = allowed) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some(k => !allowed.includes(k))
      || required.some(k => !Object.hasOwn(value, k))) invalid();
  };
  const string = (value, max) => {
    if (typeof value !== 'string' || !value.trim() || value.length > max) invalid();
  };
  object(args, ['entity_ref', 'observations', ...extra]);
  for (const key of extra) if (!UUID.test(args[key])) invalid();
  string(args.entity_ref, 200);
  if (!Array.isArray(args.observations) || !args.observations.length || args.observations.length > 100) invalid();
  const ids = new Set();
  for (const o of args.observations) {
    object(o, Object.keys(observationProperties), observationRequired);
    string(o.id, 100);
    if (ids.has(o.id)) invalid();
    ids.add(o.id);
    if (typeof o.fact_key !== 'string' || !Object.hasOwn(CONTRACTS, o.fact_key)) invalid();
    const contract = CONTRACTS[o.fact_key];
    if (typeof o.unit !== 'string' || !Object.hasOwn(contract.units, o.unit) || typeof o.value !== 'string' || !DECIMAL.test(o.value)) invalid();
    if (contract.expenseBasis ? !EXPENSE_BASES.includes(o.expense_basis) : Object.hasOwn(o, 'expense_basis')) invalid();
    object(o.source, Object.keys(sourceSchema.properties), sourceSchema.required);
    string(o.source.document_ref, 200);
    string(o.source.cited_text, 4000);
    if (Object.hasOwn(o.source, 'content_hash') && (typeof o.source.content_hash !== 'string'
      || o.source.content_hash.length !== 64 || !CONTENT_HASH.test(o.source.content_hash))) invalid();
    if (Object.hasOwn(o.source, 'document_family_id')) string(o.source.document_family_id, 200);
    if (Object.hasOwn(o.source, 'derivation_chain')) {
      if (!Array.isArray(o.source.derivation_chain) || o.source.derivation_chain.length > 20) invalid();
      for (const ref of o.source.derivation_chain) string(ref, 200);
    }
    for (const page of [o.source.page_start, o.source.page_end])
      if (!Number.isInteger(page) || page < 1 || page > 100000) invalid();
    if (o.source.page_start > o.source.page_end) invalid();
    string(o.extractor_version, 100);
    if (Object.hasOwn(o, 'extraction')) {
      object(o.extraction, ['pass', 'model']);
      string(o.extraction.pass, 100);
      string(o.extraction.model, 100);
    }
    if (!observationProperties.method.enum.includes(o.method)
      || !observationProperties.confidence_tier.enum.includes(o.confidence_tier)) invalid();
  }
}

export function factObservationTools({ ToolError }) {
  return {
    'compare-fact-observations': {
      write: false,
      description: 'Compare supplied economic fact observations using exact decimal normalization and project review items by field with their competing observations, supplied citations, normalized values, and routing tiers. Optional caller-asserted source hashes, document families, and upstream family references expose conservative shared-source groups, not verified lineage. Optional extraction pass and model expose shared-extraction groups for identical pairs, not verified failure domains. Each review item includes its field-scoped shared-source and shared-extraction groups beside the cited observations. Missing metadata or empty group lists do not establish independence. Effective confidence tiers are conservative routing labels, not verified confidence. OCR always requires human review because dual-engine agreement is not proven. Preserves supplied citations but does not verify sources, read stored records, establish independent evidence, promote facts, or write records.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        entity_ref: { ...text(200), description: 'Caller-asserted entity for this comparable set. No stored identity or document binding is checked.' }, observations: { type: 'array', minItems: 1, maxItems: 100, items: observationSchema },
      }, required: ['entity_ref', 'observations'] },
      handler: async (_client, _actor, args) => {
        validate(args, ToolError);
        const observations = args.observations.map(input => ({ input,
          effective_confidence_tier: input.method === 'ocr' ? 'human_review' : input.confidence_tier,
          normalized: { value: normalize(input.value, CONTRACTS[input.fact_key].units[input.unit]),
            unit: CONTRACTS[input.fact_key].unit,
            ...(CONTRACTS[input.fact_key].expenseBasis ? { expense_basis: input.expense_basis } : {}) },
        }));
        const groups = new Map();
        for (const observation of observations) {
          const key = observation.input.fact_key;
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(observation);
        }
        const fields = [...groups].map(([fact_key, group]) => ({ fact_key,
          status: group.length === 1 ? 'single_observation'
            : new Set(group.map(o => JSON.stringify(o.normalized))).size === 1 ? 'agreement' : 'conflict',
          observation_ids: group.map(o => o.input.id),
        }));
        const shared_source_groups = [...groups].flatMap(([fact_key, group]) => ['document_ref', 'content_hash', 'document_family_id'].flatMap(basis => {
          const sources = new Map();
          for (const { input } of group) {
            const source = input.source;
            const refs = basis === 'document_family_id' ? [source.document_family_id, ...(source.derivation_chain || [])]
              : [basis === 'content_hash' ? source.content_hash?.toLowerCase() : source.document_ref];
            for (const ref of new Set(refs.filter(value => value !== undefined))) {
              if (!sources.has(ref)) sources.set(ref, []);
              sources.get(ref).push(input.id);
            }
          }
          return [...sources].filter(([, ids]) => ids.length > 1)
            .map(([source_ref, observation_ids]) => ({ fact_key, basis, source_ref, observation_ids }));
        }));
        const shared_extraction_groups = [...groups].flatMap(([fact_key, group]) => {
          const domains = new Map();
          for (const { input } of group) {
            if (!input.extraction) continue;
            const { pass, model } = input.extraction;
            const key = JSON.stringify([pass, model]);
            if (!domains.has(key)) domains.set(key, { fact_key, pass, model, observation_ids: [] });
            domains.get(key).observation_ids.push(input.id);
          }
          return [...domains.values()].filter(domain => domain.observation_ids.length > 1);
        });
        const review_items = fields.flatMap(field => {
          const tiers = new Set(groups.get(field.fact_key).map(o => o.effective_confidence_tier));
          const reasons = [
            ...(field.status === 'conflict' ? ['conflict'] : []),
            ...['human_review', 'verify_band'].filter(tier => tiers.has(tier)),
            ...(groups.get(field.fact_key).some(o => o.input.method === 'ocr') ? ['ocr'] : []),
          ];
          return reasons.length ? [{ entity_ref: args.entity_ref, fact_key: field.fact_key,
            priority: field.status === 'conflict' || tiers.has('human_review') ? 'high' : 'normal',
            observation_ids: field.observation_ids, observations: groups.get(field.fact_key), reasons,
            shared_source_groups: shared_source_groups.filter(group => group.fact_key === field.fact_key),
            shared_extraction_groups: shared_extraction_groups.filter(group => group.fact_key === field.fact_key),
          }] : [];
        }).sort((a, b) => Number(b.priority === 'high') - Number(a.priority === 'high'));
        return { ok: true, schema_version: 'fact-observation-comparison.v1', entity_ref: args.entity_ref,
          canonical: false, records_written: 0, source_verification: 'unverified', entity_binding: 'caller_asserted',
          evidence_independence: 'not_assessed', observations, review_items, shared_source_groups, shared_extraction_groups,
          fields, conflicts: fields.filter(f => f.status === 'conflict').map(({ fact_key, observation_ids }) => ({ fact_key, observation_ids })),
        };
      },
    },
    'record-fact-observations': {
      write: true,
      description: 'Store extracted economic fact observations for one entity as immutable rows with their source document, page span, cited text, method, extractor version, optional document lineage and extraction pass, and confidence tier. The extraction-job door: verified partners, probes, and reviewers are refused, and human values reach canonical only through promote-fact human review. Recording never promotes anything. Idempotent per idempotency_key; reusing a key with different input is refused. Cited text is kept 365 days for review, then purged to null while its SHA-256 remains.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { ...uuid, description: 'A fresh UUID per intended batch; retries reuse the same key.' },
        entity_ref: { ...text(200), description: 'The entity these observations describe, such as a deal or lease reference.' },
        observations: { type: 'array', minItems: 1, maxItems: 100, items: observationSchema },
      }, required: ['idempotency_key', 'entity_ref', 'observations'] },
      handler: async (c, actor, args) => {
        if (['verified_partner', 'probe_agent', 'review_agent'].includes(authorizationClassForActor(actor)))
          throw new ToolError({ error: 'not_in_profile' });
        validate(args, ToolError, ['idempotency_key']);
        const out = (await c.query('select ops.record_fact_observations($1,$2,$3,$4,$5::jsonb) as r',
          [organizationTenantForActor(actor), actor.id, args.idempotency_key, args.entity_ref, JSON.stringify(args.observations)])).rows[0].r;
        if (!out.ok) throw new ToolError({ error: out.error });
        return { ...out, schema_version: 'fact-observation-record.v1', canonical: false };
      },
    },
    'promote-fact': {
      write: true,
      description: 'Promote one entity-field to its next canonical version from stored observations. basis independent_agreement is the deterministic kernel rule: at least two agreeing auto-accept text-layer observations, no disagreeing observation on the field, and at least one pair proven independent by a different document, different content hash, disjoint document family and derivation chain, and a different extraction pass or model. A single AI observation never promotes by rule. basis human_review promotes exactly one observation and requires a verified partner. base_version is the current canonical version (0 when none); a stale value is refused with current_version. Every promotion is an append-only receipt naming the basis, actor, and exact observation ids. Refusals return ok false with the reason.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { ...uuid, description: 'A fresh UUID per intended promotion; retries reuse the same key.' },
        entity_ref: text(200), fact_key: { type: 'string', enum: Object.keys(CONTRACTS) },
        basis: { type: 'string', enum: ['independent_agreement', 'human_review'] },
        observation_ids: { type: 'array', minItems: 1, maxItems: 100, uniqueItems: true, items: uuid },
        base_version: { type: 'integer', minimum: 0, maximum: 0x7ffffffe },
      }, required: ['idempotency_key', 'entity_ref', 'fact_key', 'basis', 'observation_ids', 'base_version'] },
      handler: async (c, actor, args) => {
        if (args.basis === 'human_review' && authorizationClassForActor(actor) !== 'verified_partner')
          throw new ToolError({ error: 'human_only_verb_requires_verified_partner' });
        const out = (await c.query('select ops.promote_fact($1,$2,$3,$4,$5,$6,$7::uuid[],$8) as r',
          [organizationTenantForActor(actor), actor.id, args.idempotency_key, args.entity_ref, args.fact_key,
            args.basis, args.observation_ids, args.base_version])).rows[0].r;
        if (['version_conflict', 'key_reuse', 'not_found'].includes(out.error))
          throw new ToolError({ error: out.error, current_version: out.current_version });
        return { ...out, schema_version: 'fact-promotion.v1' };
      },
    },
    'list-fact-conflicts': {
      write: false,
      description: 'List unresolved fact conflicts: entity-fields whose stored observations normalize to more than one value (expense basis included), newest first, each with every competing observation, its cited source, lineage, tier, and the current canonical version to pass as base_version. A conflict closes only when a human-review promotion is recorded after its newest observation. Expired cited text reads as null; its SHA-256 remains.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        entity_ref: { ...text(200), description: 'Limit to one entity.' },
        limit: { type: 'integer', minimum: 1, maximum: 100, default: 50 },
      } },
      handler: async (c, actor, args) => {
        const out = (await c.query('select ops.list_fact_conflicts($1,$2,$3) as r',
          [organizationTenantForActor(actor), args.entity_ref ?? null, args.limit ?? 50])).rows[0].r;
        return { ...out, schema_version: 'fact-conflicts.v1' };
      },
    },
  };
}
