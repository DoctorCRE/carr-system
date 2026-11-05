// Validate a versioned market study in full or partial (research) mode.
import { Validator } from '@cfworker/json-schema';
import schema from '../../client-portal/market-study.v1.schema.json' with { type: 'json' };
import identitySchema from '../../client-portal/market-study.v1.1.schema.json' with { type: 'json' };

// The groups the package builder overlays from the record layer. A research
// package omits them; partial mode waives only these.
export const RECORD_LAYER_GROUPS = Object.freeze(['client', 'prepared', 'properties']);

const validators = new Map([schema, identitySchema].map(contract => [contract.properties.schema.const,
  Object.fromEntries(['full', 'partial'].map(mode => {
    const selected = mode === 'full' ? contract : { ...contract,
      required: contract.required.filter(group => !RECORD_LAYER_GROUPS.includes(group)) };
    const validator = new Validator(selected, '2020-12', false);
    if (contract === identitySchema) validator.addSchema(schema);
    return [mode, validator];
  })),
]));

function geographyErrors(demographics) {
  const sections = [
    ['growth/geographies', demographics.growth.geographies],
    ['age/geographies', demographics.age.geographies],
    ['median_income/geographies', demographics.median_income.geographies],
    ['income_bands/areas', demographics.income_bands.areas],
  ];
  const identities = new Map(sections[0][1].map(row => [row.geography_id, row.geography_label]));
  const labels = new Set();
  const errors = [];
  for (const [section, rows] of sections) {
    const seen = new Set();
    rows.forEach((row, index) => {
      const path = `/demographics/${section}/${index}`;
      if (seen.has(row.geography_id)) errors.push({ path: `${path}/geography_id`, message: 'Duplicate geography ID.' });
      seen.add(row.geography_id);
      if (!identities.has(row.geography_id)) errors.push({ path: `${path}/geography_id`, message: 'Unknown growth geography ID.' });
      else if (identities.get(row.geography_id) !== row.geography_label) {
        errors.push({ path: `${path}/geography_label`, message: 'Geography label must match the growth row.' });
      }
      if (section === 'growth/geographies') {
        const label = row.geography_label.trim().toLowerCase();
        if (!label || labels.has(label)) errors.push({ path: `${path}/geography_label`, message: 'Geography labels must distinguish identities.' });
        labels.add(label);
      }
    });
    if (seen.size !== identities.size) errors.push({ path: `/demographics/${section}`, message: 'Every growth geography must appear exactly once.' });
  }
  return errors;
}

// Paths are JSON Pointers to the offending field. A missing or unexpected
// property is reported at that property's own path, not at its parent.
function fieldPath({ instanceLocation, keyword, error }) {
  const base = instanceLocation.replace(/^#/, '');
  const named = keyword === 'required' ? /required property "([^"]+)"/.exec(error)
    : keyword === 'additionalProperties' ? /^Property "([^"]+)" does not match/.exec(error) : null;
  return named ? `${base}/${named[1]}` : base || '/';
}

// The output-unit keywords only restate a child's failure one level up.
const SUMMARY_KEYWORDS = new Set(['properties', 'items', 'prefixItems', 'allOf', '$ref']);

export function validateMarketStudyPackage(pkg, { mode = 'full' } = {}) {
  if (!['full', 'partial'].includes(mode)) throw new TypeError(`unknown validation mode: ${mode}`);
  const validator = validators.get(pkg?.schema)?.[mode];
  if (!validator) return { ok: false, errors: [{ path: '/schema', message: 'Unsupported market study schema.' }] };
  const { valid, errors } = validator.validate(pkg);
  const detail = errors.filter(error => !SUMMARY_KEYWORDS.has(error.keyword));
  const unique = new Map();
  for (const error of detail) {
    const path = fieldPath(error);
    if (!unique.has(path)) unique.set(path, { path, message: error.error });
  }
  if (valid && pkg.schema === 'carr.market-study.v1.1') {
    for (const error of geographyErrors(pkg.demographics)) unique.set(error.path, error);
  }
  return { ok: valid && unique.size === 0, errors: [...unique.values()] };
}
