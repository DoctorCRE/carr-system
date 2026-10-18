import { z } from 'zod';
import { envelope } from './evidence.mjs';
import { id, usd } from './runway.mjs';
import { date } from './conversion.mjs';

const yesNo = z.enum(['yes', 'no', 'unknown']);
const fact = z.strictObject({ value: yesNo, evidence_ref: id.nullable() });
export const saleInput = z.strictObject({
  as_of_date: date, expected_closing_date: date, base_term_end_exclusive: date.describe('First day not covered by the base lease. Convert inclusive last day by adding one day.'),
  assignment_status: z.enum(['permitted', 'consent_obtained', 'consent_pending', 'prohibited', 'unknown']), assignment_evidence_ref: id.nullable(),
  lender_required_months: z.number().int().min(1).max(600).nullable(), lender_evidence_ref: id.nullable(), lender_verified_on: date.nullable(),
  documents_complete: z.boolean(), documents_conflict: z.boolean(),
  renewal_options: z.array(z.strictObject({ id, start_date: date, end_exclusive: date,
    transferable_to_buyer: fact, exercisable_by_buyer: fact, conditions_satisfied: fact, notice_deadline: date, notice_delivered: fact })).max(20)
    .describe('Input order is immaterial. Options are resolved chronologically; overlapping periods are ambiguous and rejected.'),
  termination_rights: z.array(z.strictObject({ id, type: z.enum(['demolition', 'relocation', 'recapture', 'other']), applicable: fact,
    earliest_date: date.nullable(), effect: z.enum(['ends_occupancy', 'no_effect', 'unknown']), counsel_reviewed: z.boolean(), evidence_ref: id.nullable() })).max(20),
  personal_guarantee_release: fact, defaults_resolved: fact, permitted_use_match: fact, lender_waiver: fact, required_consents_obtained: fact,
  lender_waiver_applicable: fact.default({ value: 'unknown', evidence_ref: null })
    .describe('Document whether a lender coverage waiver is required. An unnecessary waiver does not need delivery evidence; omitted applicability remains unresolved.'),
  lender_waiver_terms: z.strictObject({ waived_requirement: z.literal('coverage_months'), required_months: z.number().int().min(1).max(600),
    evidence_ref: id, verified_on: date }).optional()
    .describe('Documented replacement lender coverage threshold only. Never waives assignment, notice, defaults, use, guarantee release or consents.'),
  base_rent_usd_month: usd.optional(), rent_escalations: z.array(z.strictObject({ effective_date: date, monthly_usd: usd })).max(50),
});
export const saleResults = z.strictObject({
  status: z.enum(['DOCUMENT_CONFLICT', 'BLOCKED', 'ACTION_REQUIRED', 'REVIEW_REQUIRED', 'NO_IDENTIFIED_OBSTACLE']),
  value_type: z.literal('calculated example'), verified_control_end_exclusive: date.nullable(), conditional_control_end_exclusive: date.nullable(), required_coverage_end: date.nullable(),
  coverage: z.enum(['PASSES_CONFIGURED_CHECK', 'SHORTFALL', 'COVERAGE_NOT_ASSESSED']), shortfall_months: z.number().int().nonnegative().nullable(),
  excluded_options: z.array(z.strictObject({ id, reason: z.string() })), deadlines: z.array(z.strictObject({ id, days: z.number().int(), urgency: z.enum(['overdue', 'due_soon', 'future']) })),
  flags: z.array(z.string()), questions: z.array(z.string()),
});
const days = (a, b) => Math.round((Date.parse(a) - Date.parse(b)) / 86400000);
export function addMonths(s, n) {
  const [y, m, day] = s.split('-').map(Number), total = y * 12 + m - 1 + n;
  const year = Math.floor(total / 12), month = total % 12;
  const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return new Date(Date.UTC(year, month, Math.min(day, last))).toISOString().slice(0, 10);
}
const known = f => f.value !== 'unknown' && f.evidence_ref !== null;
export function checkSale(a) {
  if (a.expected_closing_date < a.as_of_date || (a.assignment_status !== 'unknown' && !a.assignment_evidence_ref)) throw new Error('date/evidence');
  if (a.lender_required_months !== null && (!a.lender_evidence_ref || !a.lender_verified_on || a.lender_verified_on > a.as_of_date)) throw new Error('lender evidence');
  const optionIds = a.renewal_options.map(o => o.id);
  if (new Set(optionIds).size !== optionIds.length || new Set(a.termination_rights.map(o => o.id)).size !== a.termination_rights.length) throw new Error('duplicate ids');
  const options = [...a.renewal_options].sort((a, b) => a.start_date.localeCompare(b.start_date));
  for (const [i, o] of options.entries()) {
    if (o.end_exclusive <= o.start_date || (i > 0 && o.start_date < options[i - 1].end_exclusive)) throw new Error('ambiguous option dates/overlap');
  }
  const assignment = ['permitted', 'consent_obtained'].includes(a.assignment_status);
  const documentsTrusted = a.documents_complete && !a.documents_conflict;
  let verified = assignment && documentsTrusted ? a.base_term_end_exclusive : null;
  let conditional = a.base_term_end_exclusive, stop = !verified, conditionalStop = false;
  let action = false, review = !assignment;
  const excluded = [], deadlines = [], flags = [];
  for (const o of options) {
    const delta = days(o.notice_deadline, a.as_of_date);
    deadlines.push({ id: o.id, days: delta, urgency: delta < 0 ? 'overdue' : delta <= 90 ? 'due_soon' : 'future' });
    const conditions = [o.transferable_to_buyer, o.exercisable_by_buyer, o.conditions_satisfied];
    const missed = delta < 0 && known(o.notice_delivered) && o.notice_delivered.value === 'no';
    const noticeBeforeClosing = !missed && known(o.notice_delivered) && o.notice_delivered.value === 'no'
      && o.notice_deadline <= a.expected_closing_date;
    const uncertainNotice = !known(o.notice_delivered);
    const denied = conditions.some(f => known(f) && f.value === 'no') || missed;
    const unresolved = conditions.some(f => !known(f)) || uncertainNotice || noticeBeforeClosing;
    const contiguous = o.start_date === verified;
    let reason = stop ? 'Prior option excluded or buyer control not established' : !contiguous ? 'Gap in coverage' : denied ? 'Condition not satisfied or notice missed' : noticeBeforeClosing ? 'Required notice is undelivered before/on expected closing' : unresolved ? 'Option evidence unresolved' : null;
    if (reason) { excluded.push({ id: o.id, reason }); stop = true; }
    else verified = o.end_exclusive;
    if (missed || denied || reason === 'Gap in coverage') action = true;
    if (noticeBeforeClosing) { action = true; flags.push(`Required renewal notice before/on closing: ${o.id}; deadline ${o.notice_deadline}; expected closing ${a.expected_closing_date}`); }
    if (unresolved) review = true;
    if (!conditionalStop && o.start_date === conditional && !denied) conditional = o.end_exclusive;
    else conditionalStop = true;
  }
  let terminationBeforeClosing = false;
  for (const r of a.termination_rights) {
    if (!known(r.applicable) || (r.applicable.value === 'yes' && (!r.counsel_reviewed || !r.evidence_ref || r.effect === 'unknown'))) { review = true; flags.push(`Termination effect needs counsel review: ${r.id}`); }
    else if (r.applicable.value === 'yes' && r.effect === 'ends_occupancy') {
      if (!r.earliest_date) throw new Error('termination date');
      if (verified && r.earliest_date < verified) verified = r.earliest_date;
      if (r.earliest_date < conditional) conditional = r.earliest_date;
      if (r.earliest_date <= a.expected_closing_date) terminationBeforeClosing = true;
    }
  }
  if (assignment && verified && verified <= a.expected_closing_date) action = true;
  for (const [key, f] of Object.entries({ defaults_resolved: a.defaults_resolved, permitted_use_match: a.permitted_use_match, required_consents_obtained: a.required_consents_obtained })) {
    if (!known(f)) { review = true; flags.push(`Evidence needed: ${key}`); }
    else if (f.value === 'no') { action = true; flags.push(`Unmet documented condition: ${key}`); }
  }
  if (!known(a.personal_guarantee_release)) { review = true; flags.push('Guarantee release needs review'); }
  else if (a.personal_guarantee_release.value === 'no') { action = true; flags.push('SELLER_CONTINUING_LIABILITY'); }
  let requiredMonths = a.lender_required_months;
  if (a.lender_waiver_terms && (a.lender_waiver_terms.verified_on > a.as_of_date || !known(a.lender_waiver_applicable)
      || a.lender_waiver_applicable.value !== 'yes' || !known(a.lender_waiver) || a.lender_waiver.value !== 'yes'
      || requiredMonths === null)) throw new Error('waiver applicability/evidence');
  if (!known(a.lender_waiver_applicable)) { review = true; flags.push('Evidence needed: lender waiver applicability'); }
  else if (a.lender_waiver_applicable.value === 'yes') {
    if (!known(a.lender_waiver)) { review = true; flags.push('Evidence needed: lender_waiver'); }
    else if (a.lender_waiver.value === 'no') { action = true; flags.push('Unmet documented condition: lender_waiver'); }
    else if (!a.lender_waiver_terms) { review = true; flags.push('Evidence needed: specific lender waiver coverage terms'); }
    else { requiredMonths = a.lender_waiver_terms.required_months; flags.push('Documented lender coverage exception applied; other conditions remain required.'); }
  }
  const required = requiredMonths === null ? null : addMonths(a.expected_closing_date, requiredMonths);
  if (!required) review = true;
  const coverage = !required || !verified ? 'COVERAGE_NOT_ASSESSED' : verified >= required ? 'PASSES_CONFIGURED_CHECK' : 'SHORTFALL';
  let shortfall = null;
  if (coverage === 'SHORTFALL') {
    action = true; shortfall = 0;
    while (addMonths(verified, shortfall) < required && shortfall < 1200) shortfall++;
  }
  const status = !a.documents_complete || a.documents_conflict ? 'DOCUMENT_CONFLICT'
    : a.assignment_status === 'prohibited' || terminationBeforeClosing ? 'BLOCKED'
      : action ? 'ACTION_REQUIRED' : review ? 'REVIEW_REQUIRED' : 'NO_IDENTIFIED_OBSTACLE';
  return envelope(a, { status, value_type: 'calculated example', verified_control_end_exclusive: verified,
    conditional_control_end_exclusive: conditional, required_coverage_end: required, coverage, shortfall_months: shortfall,
    excluded_options: excluded, deadlines, flags, questions: ['Counsel: reconcile all amendments, assignment, renewal conditions, notice evidence and termination rights.', 'Lender: confirm the program-specific term requirement and applicable waivers.', 'Seller/counsel: obtain documented guarantee release and all required consents.'],
  }, { missing_inputs: [...(!a.documents_complete ? ['Controlling lease/amendments'] : []), ...(!required ? ['Documented lender coverage threshold'] : [])],
    assumptions: ['All evidence classifications are owner-supplied references, not independent legal interpretations.', 'Conditional coverage is a possible term only; it does not establish buyer control or lender acceptance.', 'Notice urgency uses a product flag, not a legal grace period.'],
    limitations: ['NO_IDENTIFIED_OBSTACLE means only configured checks passed; it is not legal approval or financing eligibility.', 'No lease text, names, addresses or personal data are required or accepted.'] });
}
