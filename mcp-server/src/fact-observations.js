const CONTRACTS = {
  base_rent_per_sf: { units: { 'USD/SF/year': 1, 'USD/SF/month': 12 }, unit: 'USD/SF/year', expenseBasis: true },
  landlord_ti_per_sf: { units: { 'USD/SF': 1 }, unit: 'USD/SF' },
  term_months: { units: { months: 1, years: 12 }, unit: 'months' },
  free_rent_months: { units: { months: 1 }, unit: 'months' },
};
const EXPENSE_BASES = ['nnn', 'modified_gross', 'full_service_gross', 'gross'];
const DECIMAL = /^(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?$/;
const text = maxLength => ({ type: 'string', minLength: 1, maxLength });
const sourceSchema = { type: 'object', additionalProperties: false, properties: {
  document_ref: text(200), page_start: { type: 'integer', minimum: 1, maximum: 100000 },
  page_end: { type: 'integer', minimum: 1, maximum: 100000 }, cited_text: text(4000),
}, required: ['document_ref', 'page_start', 'page_end', 'cited_text'] };
const observationProperties = {
  id: text(100), fact_key: { type: 'string', enum: Object.keys(CONTRACTS) },
  value: { type: 'string', pattern: DECIMAL.source, maxLength: 49 },
  unit: { type: 'string', enum: [...new Set(Object.values(CONTRACTS).flatMap(c => Object.keys(c.units)))] },
  expense_basis: { type: 'string', enum: EXPENSE_BASES }, source: sourceSchema,
  method: { type: 'string', enum: ['text_layer', 'ocr'] }, extractor_version: text(100),
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

function validate(args, ToolError) {
  const invalid = () => { throw new ToolError({ error: 'validation_error' }); };
  const object = (value, allowed, required = allowed) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some(k => !allowed.includes(k))
      || required.some(k => !Object.hasOwn(value, k))) invalid();
  };
  const string = (value, max) => {
    if (typeof value !== 'string' || !value.trim() || value.length > max) invalid();
  };
  object(args, ['entity_ref', 'observations']);
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
    object(o.source, sourceSchema.required);
    string(o.source.document_ref, 200);
    string(o.source.cited_text, 4000);
    for (const page of [o.source.page_start, o.source.page_end])
      if (!Number.isInteger(page) || page < 1 || page > 100000) invalid();
    if (o.source.page_start > o.source.page_end) invalid();
    string(o.extractor_version, 100);
    if (!observationProperties.method.enum.includes(o.method)
      || !observationProperties.confidence_tier.enum.includes(o.confidence_tier)) invalid();
  }
}

export function factObservationTools({ ToolError }) {
  return {
    'compare-fact-observations': {
      write: false,
      description: 'Compare supplied economic fact observations using exact decimal normalization. Preserves supplied citations but does not verify sources, read stored records, establish independent evidence, promote facts, or write records.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        entity_ref: { ...text(200), description: 'Caller-asserted entity for this comparable set. No stored identity or document binding is checked.' }, observations: { type: 'array', minItems: 1, maxItems: 100, items: observationSchema },
      }, required: ['entity_ref', 'observations'] },
      handler: async (_client, _actor, args) => {
        validate(args, ToolError);
        const observations = args.observations.map(input => ({ input,
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
        return { ok: true, schema_version: 'fact-observation-comparison.v1', entity_ref: args.entity_ref,
          canonical: false, records_written: 0, source_verification: 'unverified', entity_binding: 'caller_asserted',
          evidence_independence: 'not_assessed', observations,
          fields, conflicts: fields.filter(f => f.status === 'conflict').map(({ fact_key, observation_ids }) => ({ fact_key, observation_ids })),
        };
      },
    },
  };
}
