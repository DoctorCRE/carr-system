import { z } from 'zod';
import { envelope, rangeSchema } from './evidence.mjs';
import { D, dollars, id, usd } from './runway.mjs';

export const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(s => {
  const d = new Date(`${s}T00:00:00Z`); return Number.isFinite(d.getTime()) && d.toISOString().slice(0, 10) === s;
}, 'Use a valid ISO date');
const condition = z.strictObject({ status: z.enum(['verified', 'unverified', 'unknown']), evidence_ref: id.nullable(), verified_on: date.nullable(),
  value: z.union([z.number().finite().nonnegative(), z.boolean()]).nullable(), units: z.enum(['A', 'V', 'cfm', 'tons', 'gpm', 'SF', 'boolean']),
  voltage: z.number().positive().nullable(), phase: z.enum(['single', 'three']).nullable() });
const requirement = z.strictObject({ id, category: z.enum(['power', 'plumbing', 'hvac', 'accessibility', 'fire', 'structure', 'use', 'specialty']),
  applicability: z.enum(['yes', 'no', 'unknown']), mandatory: z.boolean(), type: z.enum(['numeric_minimum', 'boolean', 'explicit_prohibition']),
  required_value: z.union([z.number().finite().nonnegative(), z.boolean()]).nullable(), units: condition.shape.units,
  voltage: condition.shape.voltage, phase: condition.shape.phase,
  evidence_ref: id.nullable(), requirement_verified_on: date.nullable(), jurisdiction_ref: id.nullable(), edition_ref: id.nullable(),
  existing: condition, remediation_scope_ids: z.array(id).max(20),
});
const scope = z.strictObject({ id, category: z.enum(['construction', 'equipment', 'professional_fees', 'permit_utility_fees', 'tax', 'other']),
  shared_scope_group: id, quantity: z.number().positive().max(1e6).optional(), unit: z.enum(['each', 'SF', 'hours']).optional(),
  low_unit_usd: usd.optional(), high_unit_usd: usd.optional(), low_lump_usd: usd.optional(), high_lump_usd: usd.optional(),
  tax_inclusive: z.boolean(), separate_tax_scope_id: id.nullable(), quote_date: date, evidence_classification: z.enum(['owner_input', 'planning_assumption']),
});
export const conversionInput = z.strictObject({
  as_of_date: date, jurisdiction_resolved: z.boolean(), jurisdiction_ref: id.nullable().describe('Reference to reviewed governing jurisdiction/edition; no street address or parcel data.'),
  intended_services: z.array(z.enum(['routine_exams', 'dental', 'imaging', 'surgery', 'sedation', 'animal_housing', 'exercise', 'other'])).min(1).max(8),
  building_type: z.enum(['office', 'retail', 'standalone', 'medical_office']), proposed_occupancy: z.enum(['outpatient', 'ambulatory', 'veterinary', 'exercise', 'unknown']),
  usable_sf: z.number().positive().max(100000), rentable_sf: z.number().positive().max(200000), providers: z.number().int().nonnegative().max(100), treatment_rooms: z.number().int().min(1).max(100),
  equipment: z.array(z.strictObject({ equipment_ref: id, specification_ref: id, quantity: z.number().int().positive().max(100), clearance_reviewed: z.boolean() })).max(50),
  requirements: z.array(requirement).min(1).max(50), scope_lines: z.array(scope).max(100), construction_contingency_fraction: z.number().min(0).max(1),
  required_scope_ids: z.array(id).max(100), quote_age_days: z.number().int().min(1).max(365).default(90).describe('Product requote threshold; not a legal deadline.'),
  tenant_allowance: z.strictObject({ maximum_usd: usd, eligible_scope_ids: z.array(id).max(100), confirmed: z.boolean(), evidence_ref: id.nullable(), reimbursement_month: z.number().int().min(1).max(120) }).optional(),
});
export const conversionResults = z.strictObject({
  currency: z.literal('USD'), value_type: z.literal('calculated example'),
  outcome: z.enum(['BLOCKED', 'NEEDS_VERIFICATION', 'REMEDIATION_REQUIRED', 'NO_IDENTIFIED_GAP']),
  requirements: z.array(z.strictObject({ id, status: z.enum(['NOT_APPLICABLE', 'UNKNOWN', 'HARD_STOP', 'DEFICIENCY', 'MET']), deficit: z.number().nonnegative().nullable(),
    units: condition.shape.units, evidence_classification: z.enum(['verified_requirement', 'planning_assumption']), evidence_ref: id.nullable() })),
  line_costs: z.array(z.strictObject({ id, cost_usd: rangeSchema, evidence_classification: z.enum(['owner_input', 'planning_assumption']) })),
  known_cost_subtotal_usd: rangeSchema, total_cost_usd: rangeSchema.nullable(), tenant_economic_cost_usd: rangeSchema.nullable(),
  confirmed_allowance_applied_usd: rangeSchema, unconfirmed_allowance_usd: usd.nullable(), reimbursement_month: z.number().int().nullable(),
  cost_per_usable_sf_usd: rangeSchema.nullable(), unpriced_scope_ids: z.array(id), confirmations: z.array(z.string()),
});
const unique = xs => new Set(xs).size === xs.length;
export function screenConversion(a) {
  if (!unique(a.requirements.map(x => x.id)) || !unique(a.scope_lines.map(x => x.id)) || !unique(a.scope_lines.map(x => x.shared_scope_group)) || !unique(a.required_scope_ids)) throw new Error('duplicates');
  if (a.rentable_sf < a.usable_sf) throw new Error('area bases');
  const ids = new Set(a.scope_lines.map(x => x.id)), unpriced = new Set(a.required_scope_ids.filter(x => !ids.has(x))), warnings = [];
  const unresolvedClearances = a.equipment.filter(e => !e.clearance_reviewed)
    .map(e => `Equipment clearance review: ${e.equipment_ref}; specification: ${e.specification_ref}`);
  if (unresolvedClearances.length) warnings.push('Equipment clearances remain unreviewed; layout and remediation scope need verification.');
  const scopeById = new Map(a.scope_lines.map(x => [x.id, x]));
  const rows = a.requirements.map(r => {
    let status = 'UNKNOWN', deficit = null;
    const proof = a.jurisdiction_resolved && a.jurisdiction_ref && r.jurisdiction_ref === a.jurisdiction_ref && r.edition_ref && r.evidence_ref && r.requirement_verified_on && r.requirement_verified_on <= a.as_of_date;
    if (r.applicability === 'no' && proof) status = 'NOT_APPLICABLE';
    else if (r.applicability === 'yes' && proof) {
      if (r.type === 'explicit_prohibition') { if (typeof r.required_value !== 'boolean') throw new Error('prohibition type'); status = r.required_value ? 'HARD_STOP' : 'MET'; }
      else if (r.existing.status === 'verified' && r.existing.evidence_ref && r.existing.verified_on && r.existing.verified_on <= a.as_of_date && r.existing.units === r.units && r.existing.voltage === r.voltage && r.existing.phase === r.phase) {
        if (r.type === 'numeric_minimum') {
          if (typeof r.required_value !== 'number' || typeof r.existing.value !== 'number' || r.units === 'boolean') throw new Error('numeric type');
          if (r.units === 'A' && (!r.voltage || !r.phase)) throw new Error('electrical basis missing');
          deficit = Math.max(0, r.required_value - r.existing.value); status = deficit > 0 ? 'DEFICIENCY' : 'MET';
        } else {
          if (typeof r.required_value !== 'boolean' || typeof r.existing.value !== 'boolean' || r.units !== 'boolean') throw new Error('boolean type');
          status = r.required_value === r.existing.value ? 'MET' : 'DEFICIENCY';
        }
      }
    }
    if (r.mandatory && status === 'DEFICIENCY') {
      if (!r.remediation_scope_ids.length) unpriced.add(r.id);
      for (const x of r.remediation_scope_ids) if (!ids.has(x)) unpriced.add(x);
    }
    return { id: r.id, status, deficit, units: r.units, evidence_classification: proof ? 'verified_requirement' : 'planning_assumption', evidence_ref: r.evidence_ref };
  });
  let low = D(0), high = D(0), constructionLow = D(0), constructionHigh = D(0);
  const preciseCosts = new Map();
  const lines = a.scope_lines.map(s => {
    const unitMode = [s.quantity, s.unit, s.low_unit_usd, s.high_unit_usd].some(x => x !== undefined);
    const lumpMode = s.low_lump_usd !== undefined || s.high_lump_usd !== undefined;
    if (unitMode === lumpMode || (unitMode && [s.quantity, s.unit, s.low_unit_usd, s.high_unit_usd].some(x => x === undefined)) || (lumpMode && (s.low_lump_usd === undefined || s.high_lump_usd === undefined))) throw new Error('pricing mode');
    const lo = unitMode ? D(s.quantity).mul(s.low_unit_usd) : D(s.low_lump_usd), hi = unitMode ? D(s.quantity).mul(s.high_unit_usd) : D(s.high_lump_usd);
    if (lo.gt(hi) || s.quote_date > a.as_of_date) throw new Error('cost range/date');
    const taxLine = scopeById.get(s.separate_tax_scope_id);
    if (!s.tax_inclusive && (!taxLine || taxLine.id === s.id || taxLine.category !== 'tax' || !taxLine.tax_inclusive)) { unpriced.add(s.id); warnings.push('Applicable tax is excluded and unpriced.'); }
    if ((Date.parse(a.as_of_date) - Date.parse(s.quote_date)) / 86400000 > a.quote_age_days) warnings.push(`REQUOTE_RECOMMENDED: ${s.id}; product threshold only.`);
    low = low.plus(lo); high = high.plus(hi);
    if (s.category === 'construction') { constructionLow = constructionLow.plus(lo); constructionHigh = constructionHigh.plus(hi); }
    preciseCosts.set(s.id, { low: lo, high: hi });
    return { id: s.id, cost_usd: { low: dollars(lo), high: dollars(hi) }, evidence_classification: s.evidence_classification };
  });
  low = low.plus(constructionLow.mul(a.construction_contingency_fraction)); high = high.plus(constructionHigh.mul(a.construction_contingency_fraction));
  const allowance = a.tenant_allowance;
  if (allowance && (!unique(allowance.eligible_scope_ids) || allowance.eligible_scope_ids.some(x => !ids.has(x)) || (allowance.confirmed && !allowance.evidence_ref))) throw new Error('allowance evidence');
  const applied = { low: D(0), high: D(0) };
  if (allowance?.confirmed) for (const bound of ['low', 'high']) {
    const eligible = allowance.eligible_scope_ids.reduce((n, x) => n.plus(preciseCosts.get(x)[bound]), D(0));
    applied[bound] = D(allowance.maximum_usd).lt(eligible) ? D(allowance.maximum_usd) : eligible;
  }
  const unknownRequirements = rows.some((r, i) => a.requirements[i].mandatory && r.status === 'UNKNOWN');
  const unknown = unknownRequirements || unresolvedClearances.length > 0;
  const outcome = rows.some(r => r.status === 'HARD_STOP') ? 'BLOCKED' : unknown ? 'NEEDS_VERIFICATION'
    : rows.some((r, i) => a.requirements[i].mandatory && r.status === 'DEFICIENCY') ? 'REMEDIATION_REQUIRED' : 'NO_IDENTIFIED_GAP';
  // Unknown mandatory scope cannot produce a complete total even if all entered lines are priced.
  const complete = !unpriced.size && !unknown;
  if (!complete) warnings.push('Incomplete scope: total and cost per usable SF are unknown; the known subtotal is not a full budget.');
  return envelope(a, { currency: 'USD', value_type: 'calculated example', outcome, requirements: rows, line_costs: lines,
    known_cost_subtotal_usd: { low: dollars(low), high: dollars(high) }, total_cost_usd: complete ? { low: dollars(low), high: dollars(high) } : null,
    tenant_economic_cost_usd: complete ? { low: dollars(low.minus(applied.low)), high: dollars(high.minus(applied.high)) } : null,
    confirmed_allowance_applied_usd: { low: dollars(applied.low), high: dollars(applied.high) }, unconfirmed_allowance_usd: allowance && !allowance.confirmed ? allowance.maximum_usd : null,
    reimbursement_month: allowance?.reimbursement_month ?? null, cost_per_usable_sf_usd: complete ? { low: dollars(low.div(a.usable_sf)), high: dollars(high.div(a.usable_sf)) } : null,
    unpriced_scope_ids: [...unpriced], confirmations: ['Landlord: verify physical capacities, usable/rentable measurements and parking rights.', 'Designer/engineers/equipment supplier: confirm dimensions, utilities, ventilation and priced remediation.', 'Permitting authority: confirm governing jurisdiction, adopted editions, intended use and applicability.'],
  }, { warnings, missing_inputs: [...unpriced, ...unresolvedClearances, ...(unknownRequirements ? ['Mandatory requirement evidence or verified compatible capacity'] : [])],
    assumptions: ['Evidence is owner-supplied; the tool does not authenticate documents or independently inspect the building.', 'Gross funding need includes construction contingency. Confirmed allowance reimbursement reduces economic cost, not up-front funding need.'],
    limitations: ['NO_IDENTIFIED_GAP is limited to inspected requirements. No code-compliance or engineering approval is issued.', 'Only synthetic references are accepted; identification and controlling documents remain outside this public tool.'] });
}
