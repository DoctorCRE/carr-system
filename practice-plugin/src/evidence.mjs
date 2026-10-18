import { z } from 'zod';

export const NOTICE = 'Preliminary screening only. A qualified designer and the permitting authority must confirm the program and applicable requirements. Not legal, financial, or design advice.';
export class PlanningInputError extends Error {
  constructor(fields) { super('Incompatible declared inputs'); this.fields = fields; }
}
export const sourceSchema = z.strictObject({
  id: z.string(), source_class: z.enum(['CARR agent training', 'published public benchmark', 'proposed planning allowance', 'owner input']),
  publisher: z.string(), document: z.string(), url: z.string().nullable(), jurisdiction: z.string(),
  publication_date: z.string().nullable(), retrieved_at: z.string().nullable(), units: z.string(), reuse_status: z.string(),
});
export const evidenceClass = z.enum(['owner_input', 'public_benchmark', 'verified_requirement', 'planning_assumption']);
export const numberType = z.enum(['CARR estimate', 'calculated example', 'manufacturer guidance', 'adopted requirement']);
export const rangeSchema = z.strictObject({ low: z.number().finite(), high: z.number().finite() });
export const envelopeShape = {
  schema_version: z.literal('practice-planning/v1'), calculation_version: z.string(),
  inputs_used: z.record(z.string(), z.unknown()), input_classification: z.literal('owner_input'),
  assumptions: z.array(z.string()), warnings: z.array(z.string()), missing_inputs: z.array(z.string()),
  sources: z.array(sourceSchema), limitations: z.array(z.string()), notice: z.literal(NOTICE),
};
export const publicSource = (id, publisher, document, url, units, publication_date = null) => ({
  id, source_class: 'published public benchmark', publisher, document, url, jurisdiction: 'Design reference; local adoption unverified',
  publication_date, retrieved_at: '2026-10-01', units,
  reuse_status: 'Factual citation and link only; no source prose, floorplans, or bulk dataset redistributed.',
});
export const allowanceSource = {
  id: 'planning-allowances', source_class: 'proposed planning allowance', publisher: 'Practice Owner Planning',
  document: 'Preliminary allowances', url: null, jurisdiction: 'No code status', publication_date: '2026-10-01',
  retrieved_at: null, units: 'As labeled per value', reuse_status: 'Original planning assumptions.',
};
const ownerSource = {
  id: 'owner-input', source_class: 'owner input', publisher: 'Owner-supplied inputs', document: 'Declared tool inputs',
  url: null, jurisdiction: 'As supplied; no independent authentication', publication_date: null, retrieved_at: null,
  units: 'Explicit field units; SF, USD, calendar dates, months and equipment capacity as applicable', reuse_status: 'Returned for this calculation only; no application storage.',
};
export function envelope(inputs, results, { sources = [], assumptions = [], warnings = [], missing_inputs = [], limitations = [], version = '1.0.0' } = {}) {
  return { schema_version: 'practice-planning/v1', calculation_version: version,
    inputs_used: inputs, input_classification: 'owner_input', assumptions, warnings, missing_inputs, results,
    limitations, notice: NOTICE, sources: [ownerSource, ...sources] };
}
