// Validate a carr.market-study.v1 data package, in full or partial (research) mode.
import { Validator } from '@cfworker/json-schema';
import schema from '../../client-portal/market-study.v1.schema.json' with { type: 'json' };

// The groups the package builder overlays from the record layer. A research
// package omits them; partial mode waives only these.
export const RECORD_LAYER_GROUPS = Object.freeze(['client', 'prepared', 'properties']);

const partialSchema = { ...schema, required: schema.required.filter(group => !RECORD_LAYER_GROUPS.includes(group)) };
const validators = {
  full: new Validator(schema, '2020-12', false),
  partial: new Validator(partialSchema, '2020-12', false),
};

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
  const validator = validators[mode];
  if (!validator) throw new TypeError(`unknown validation mode: ${mode}`);
  const { valid, errors } = validator.validate(pkg);
  const detail = errors.filter(error => !SUMMARY_KEYWORDS.has(error.keyword));
  const unique = new Map();
  for (const error of detail) {
    const path = fieldPath(error);
    if (!unique.has(path)) unique.set(path, { path, message: error.error });
  }
  return { ok: valid, errors: [...unique.values()] };
}
