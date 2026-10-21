// A compiled projection of the cited doctrine, never an independently editable
// task ledger. A changed or retired source disables the projection until reviewed.
import { isCalendarDate } from './calendar-date.js';
export const CHECKLIST_SOURCES = Object.freeze([
  { document: 'deal-room-kit', section_id: '5f8a3cbd-fe89-4557-b3f4-5b0bbc53276c', version: 1, content_hash: 'fe11a8c2bf371dcab98a0fba969594df46ee64c6a8be047dba9b6befc3dc6424' },
  { document: 'deal-room-kit', section_id: '7e263aa0-9823-477b-86e3-bd8cf0c32003', version: 1, content_hash: '6d1a03dacdff6d1ae8b5b0ca333f4fd98bcb0f380fa57f99ccaff8cff4a8d14e' },
  { document: 'diligence-and-valuation', section_id: '6c8354dd-576c-4dfe-8a56-791215172cd4', version: 1, content_hash: '656f92cd4c998a1296657d039e471f4811ba135c95b5e2fe22e180b757306f34' },
  { document: 'diligence-and-valuation', section_id: '13b3a132-36df-433e-b23e-4231ff35c753', version: 1, content_hash: '2fbd0c3894eee7c0c32b39d4ff1f4294df0f84fdc62a5b26c991959319155f2a' },
  { document: 'diligence-and-valuation', section_id: '20689402-d87e-4bb6-a048-4cfc03cf2c70', version: 1, content_hash: '86db20b306896890d675190438c6d6a038992a888843645eb349c6f373d78ca3' },
]);

const PHASES = [
  ['research', 'Research'], ['site_selection', 'Site selection'],
  ['negotiation', 'Negotiation / LOI'], ['legal', 'Legal / lease'],
  ['closing', 'Closing'], ['invoice', 'Invoice'],
];
// id, label, phase, applicable deal types, source index. Null types apply to all.
const TEMPLATE = [
  ['representation', 'Signed representation agreement', 'research', null, 0],
  ['requirements', 'Completed intake and requirements', 'research', null, 0],
  ['current_lease_abstraction', 'Current lease abstraction', 'research', ['renewal'], 0],
  ['purchase_lease_analysis', 'Purchase versus lease analysis', 'research', ['purchase', 'land'], 0],
  ['financing_prework', 'Lender and financing pre-work', 'research', ['purchase', 'land'], 0],
  ['market_survey', 'Market survey and alternatives', 'site_selection', null, 0],
  ['tour_notes', 'Tours and tour notes', 'site_selection', ['lease', 'purchase', 'land'], 1],
  ['renewal_analysis', 'Renewal versus relocation analysis', 'site_selection', ['renewal'], 0],
  ['loi_submission', 'RFP / LOI versions and submission', 'negotiation', null, 1],
  ['loi_grid', 'Live terms comparison and counters', 'negotiation', null, 0],
  ['business_terms', 'Agreed business terms', 'negotiation', null, 0],
  ['contract_draft', 'Lease / PSA draft to attorney', 'legal', null, 0],
  ['attorney_comments', 'Attorney review and comments', 'legal', null, 1],
  ['legal_review', 'Healthcare lease review', 'legal', ['lease', 'renewal'], 0],
  ['ti_scope', 'TI scope and bids versus allowance', 'legal', ['lease', 'renewal'], 1],
  ['earnest_money', 'Earnest money / escrow', 'legal', ['purchase', 'land'], 2],
  ['title_commitment', 'Title commitment and review', 'legal', ['purchase', 'land'], 2],
  ['inspections', 'Client inspections and objections', 'legal', ['purchase', 'land'], 2],
  ['survey', 'ALTA survey', 'legal', ['purchase', 'land'], 2],
  ['environmental', 'Environmental review and Phase I / II as needed', 'legal', ['purchase', 'land'], 2],
  ['insurance_restrictions', 'Insurance, restrictions and seller documents', 'legal', ['purchase', 'land'], 2],
  ['appraisal', 'Appraisal', 'legal', ['purchase', 'land'], 2],
  ['financing_commitment', 'Financing commitment', 'legal', ['purchase', 'land'], 2],
  ['critical_dates_document', 'Attorney critical dates document', 'legal', ['purchase', 'land'], 2],
  ['commission_agreement', 'Seller commission agreement', 'legal', ['purchase', 'land'], 4],
  ['feasibility', 'Soil and site feasibility', 'legal', ['land'], 3],
  ['entitlements', 'Plans, utilities and city approvals', 'legal', ['land'], 3],
  ['executed_lease', 'Executed lease / amendment', 'closing', ['lease', 'renewal'], 0],
  ['purchase_close', 'Purchase closing and executed documents', 'closing', ['purchase', 'land'], 0],
  ['commencement_letter', 'Commencement letter and possession dates', 'closing', ['lease', 'renewal'], 1],
  ['executed_comp', 'Executed comp recorded', 'closing', null, 0],
  ['expiration_recorded', 'Lease expiration recorded for renewal', 'closing', ['lease', 'renewal'], 0],
  ['entity_structure', 'Entity / lease-back structure for CPA', 'closing', ['purchase', 'land'], 0],
  ['move_in', 'Move-in', 'closing', ['lease'], 0],
  ['check_in_90_day', '90-day check-in', 'closing', ['lease'], 0],
  // Invoice milestones are requested by Joe's task; existing record verbs own facts.
  ['invoice_recorded', 'Invoice recorded', 'invoice', null, null],
  ['commission_received', 'Commission receipt recorded', 'invoice', null, null],
];

function dealType(type) {
  if (['startup', 'relocation', 'expansion', 'additional_office', 'lease'].includes(type)) return 'lease';
  return ['renewal', 'purchase', 'land'].includes(type) ? type : null;
}

export function deriveDealChecklist(record, sources, today) {
  const type = dealType(record.type);
  const current = CHECKLIST_SOURCES.every(source => sources.some(row =>
    row.id === source.section_id && row.status === 'active' && row.body_hash === source.content_hash));
  const result = { schema_version: 'deal-checklist.v1', template_state: current ? 'current' : 'source_changed',
    deal_type: type, sources: CHECKLIST_SOURCES, observed_on: today, phases: [], due_items: [] };
  if (!current) return result;
  result.template_state = type ? 'current' : 'deal_type_unknown';
  result.phases = PHASES.map(([id, label]) => ({ id, label, items: TEMPLATE
    .filter(([, , phase, types]) => phase === id && (!types || types.includes(type)))
    .map(([itemId, itemLabel, , , source]) => ({ id: itemId, label: itemLabel,
      status: 'not_recorded', due_on: null, evidence_ref: null,
      source: source === null ? { request: 'per-deal standard checklist', record_verb: itemId === 'invoice_recorded' ? 'record-deal-invoice' : 'record-commission-receipt' } : CHECKLIST_SOURCES[source],
    })) }));
  for (const date of record.critical_dates || []) {
    if (date.status !== 'open' || !date.source?.trim() || !isCalendarDate(date.due_on)) continue;
    const deadline = { id: `critical-date:${date.id}`, label: date.kind.replaceAll('_', ' '),
      status: date.due_on < today ? 'overdue' : date.due_on === today ? 'due' : 'upcoming',
      due_on: date.due_on, evidence_ref: date.source, source: { record_verb: 'add-critical-date' } };
    result.phases.find(phase => phase.id === 'legal').items.push(deadline);
  }
  const items = result.phases.flatMap(phase => phase.items);
  const complete = (id, evidence) => {
    const item = items.find(item => item.id === id);
    if (item && evidence) Object.assign(item, { status: 'complete', evidence_ref: evidence });
  };
  if (record.lease?.status === 'current' && record.lease.evidence_ref) {
    complete('executed_lease', isCalendarDate(record.lease.executed_on) && record.lease.evidence_ref);
    complete('expiration_recorded', isCalendarDate(record.lease.expiration_on) && record.lease.evidence_ref);
  }
  complete('invoice_recorded', isCalendarDate(record.invoiced_on) && `deal:${record.id}:invoiced_on:${record.invoiced_on}`);
  const commissions = record.commissions || [];
  complete('commission_received', commissions.length > 0 && commissions.every(row =>
    row.status === 'received' && isCalendarDate(row.received_on)) && commissions.map(row => `commission:${row.commission_id}`).join(', '));
  result.due_items = items
    .filter(item => ['due', 'overdue'].includes(item.status))
    .sort((a, b) => a.due_on.localeCompare(b.due_on) || a.id.localeCompare(b.id));
  return result;
}

let storageUnavailableLogged = false;

export async function readDealChecklist(client, record, now = new Date()) {
  let sources, commissions;
  try {
    sources = await client.query(
      `select id, status, body_hash from doctrine_section where id = any($1::uuid[]) /* checklist:doctrine */`,
      [CHECKLIST_SOURCES.map(source => source.section_id)]);
    commissions = await client.query(
      `select commission_id, status, to_jsonb(received_on)#>>'{}' as received_on
         from v_deal_room_commission where deal_id=$1 order by commission_id /* checklist:commissions */`, [record.id]);
  } catch (error) {
    if (error.code !== '42P01') throw error;
    if (!storageUnavailableLogged) {
      storageUnavailableLogged = true;
      console.warn('Deal checklist storage unavailable.');
    }
    return { schema_version: 'deal-checklist.v1', template_state: 'unavailable', phases: [], due_items: [] };
  }
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Chicago', year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
  return deriveDealChecklist({ ...record, commissions: commissions.rows }, sources.rows, today);
}
