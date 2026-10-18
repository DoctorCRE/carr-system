import { z } from 'zod';
import { allowanceSource, envelope, rangeSchema, numberType, evidenceClass, publicSource } from './evidence.mjs';

const source = document => ({ id: document, source_class: 'CARR agent training', publisher: 'CARR', document,
  url: null, jurisdiction: 'Industry heuristic; local adoption unverified', publication_date: '2026-07-07',
  retrieved_at: '2026-10-01', units: 'Usable SF and labeled planning quantities',
  reuse_status: 'Owner-useful industry facts only; internal material excluded.' });
const dentalRooms = ['Sterilization with dirty-to-clean workflow', 'Reception and waiting', 'Private checkout',
  'Imaging if selected', 'Lab or CAD/CAM if selected', 'Compressor/vacuum equipment', 'Clean supplies',
  'Staff/admin', 'Toilets', 'Housekeeping', 'IT'];
const sharedChecks = {
  power: ['Ask for documented service voltage, phase, panel capacity and spare circuits; have the engineer compare the selected equipment schedules.'],
  plumbing: ['Ask for water, waste connections, permitted slab penetrations and accessible toilet routes; verify fixtures and workflow with the designer.'],
  hvac: ['Ask for a project-specific commercial load and outdoor-air calculation, humidity control, zoning and after-hours operation. Mechanical capacity unverified.'],
  parking: ['Ask which spaces the practice can legally use, shared-parking restrictions, accessible routes and peak staff/patient/companion demand. Confirm the adopted ratio and its area denominator locally.'],
  code: ['Ask the permitting authority about intended use, occupancy, accessibility, egress, fire protection and licensing before committing to the site.'],
};
const dentalChecks = {
  power: ['Confirm chair, vacuum, compressor, autoclave and imaging circuits from manufacturer sheets; single-phase CBCT does not establish a need for three-phase service.'],
  plumbing: ['Confirm sinks near treatment positions, vacuum, compressed air, water and medical-gas routes if used.'],
  hvac: ['Confirm clinical/business/sterilization zones. NOT COVERED: a universal independent steam-sterilization exhaust requirement is not established; identify the sterilizer/process and design heat, moisture and airflow accordingly.'],
  code: ['If nitrous is used, confirm scavenging with outdoor discharge away from air intakes, equipment compatibility, measured flow, leaks and exposure monitoring.',
    'DISAGREE with automatic sprinkler determination from sedation alone. Sedation and recovery are an early fire-review trigger; confirm patient self-preservation, floor/exit conditions and adopted codes with the fire authority.'],
};
const build = (label, document, sizing, rooms, checks = {}, parking = null, warnings = [], supplementalRooms = []) => ({
  label, source: source(document), sizing, rooms, parking, warnings, supplementalRooms,
  checklist: Object.fromEntries(Object.keys(sharedChecks).map(k => [k, [...sharedChecks[k], ...(checks[k] || [])]])),
});
const ops = { kind: 'operatories', sf: 400, type: 'CARR estimate', rule: 'Operatories × 400 usable SF; whole-office heuristic already includes support/circulation.' };
const band = (low, high, rule) => ({ kind: 'single_provider_band', low, high, type: 'CARR estimate', rule });
const five = { spaces_per_1000_sf: 5, type: 'CARR estimate', source_class: 'CARR agent training', basis: 'Usable SF for preliminary calculation; adopted local denominator remains unverified.' };
const ptBenchmark = publicSource('pt-2017', 'Defense Health Agency', 'Physical Therapy space planning, private treatment and exercise area',
  'https://www.wbdg.org/FFC/DOD/MHSSC/spaceplanning_healthfac_390_2017.pdf', 'Net SF; military program design reference only', '2017-07-01');
export const VERTICALS = {
  dental_gp: build('General dentistry', 'dental-vertical-guide', ops, ['Treatment and hygiene operatories', ...dentalRooms], dentalChecks, five),
  dental_pedo: build('Pediatric dentistry', 'dental-vertical-guide', ops, ['Open treatment bay', 'Quiet treatment rooms', 'Centralized imaging', 'Companion waiting', ...dentalRooms], dentalChecks, five),
  dental_ortho: build('Orthodontics', 'dental-vertical-guide', band(2000, 2500, 'Single-practitioner starting band, not a per-chair formula.'), ['Open adjustment bay', 'Consult rooms', 'Pan/Ceph and scanning', 'Larger waiting/play area', ...dentalRooms.filter(r => !/Lab/.test(r))], {
    ...dentalChecks, plumbing: ['Open adjustment chairs are electrical-only in the training model; confirm sinks and any separately plumbed procedures.'],
    parking: ['Frequent short visits and companions increase parking turnover; the dental ratio may understate peak demand.'],
  }, five, ['NOT COVERED: exact orthodontic total is not independently validated; CARR estimate. Confirm chair count and peak throughput.']),
  dental_endo: build('Endodontics', 'dental-vertical-guide', band(1500, 1800, 'Single practitioner with three microscope operatories in the training model.'), ['Microscope operatories', 'Sterilization', 'Reception/waiting', 'Equipment', 'Staff/admin', 'Supplies', 'Toilets', 'Housekeeping/IT'], {
    ...dentalChecks, power: [...dentalChecks.power, 'Confirm exact microscope reach, mounting/support and chair/imaging clearances.'],
  }, five, ['NOT COVERED: exact endodontic range is not independently validated; CARR estimate. No separate consult, lab or hygiene area assumed.']),
  dental_perio: build('Periodontics', 'dental-vertical-guide', ops, ['Treatment and hygiene operatories', ...dentalRooms], dentalChecks, five),
  dental_prostho: build('Prosthodontics', 'dental-vertical-guide', ops, ['Treatment operatories', 'Larger denture fabrication lab', ...dentalRooms], dentalChecks, five),
  dental_oms: build('Oral and maxillofacial surgery', 'dental-vertical-guide', band(1800, 2400, 'Single-practitioner specialty starting band; surgical program needs separate confirmation.'), ['Exam rooms', 'Larger surgery rooms', 'Recovery', 'Discreet wheelchair exit', ...dentalRooms], dentalChecks, five,
    ['NOT COVERED: oral-surgery total and room dimensions are CARR estimates, not surgical-room requirements. Anesthesia, rescue access, staffing and recovery control the program.']),
  medical: build('Primary care medical', 'medical-vertical-guide', { kind: 'doctors', first: 1500, additional_low: 500, additional_high: 1000, type: 'CARR estimate', rule: 'First doctor: 1,500 usable SF; each additional doctor: 500–1,000 usable SF.' },
    ['Exam rooms with sinks', 'Nursing work area', 'Medication storage', 'Clean supplies', 'Soiled utility', 'Lab if selected', 'Specimen bathroom away from lobby', 'Reception/waiting', 'Staff/admin', 'Toilets', 'Housekeeping/IT'], {
      power: ['Identify imaging, lasers and refrigeration before choosing service capacity; obtain actual device schedules.'],
      plumbing: ['Confirm a sink in each exam room and a non-lobby specimen bathroom near the lab where samples are collected.'],
      hvac: ['Confirm equipment heat loads and zones, process ventilation, biologic storage and refrigeration alarms.'],
      code: ['Imaging, medical gases, lasers and procedures trigger specialist design/code review. Primary-care sizing does not cover oncology, surgery centers or other large/procedural specialties.'],
    }, five, ['Training lists a separate typical single-doctor band; the formula is a screening rule and does not guarantee the exam-room program fits.'],
    ['Nursing work area', 'Medication storage', 'Clean supplies', 'Soiled utility', 'Reception/waiting', 'Staff/admin', 'Toilets', 'Housekeeping/IT']),
  veterinary: build('Small-animal veterinary', 'vet-vertical-guide', band(1500, 2500, 'Small-animal starting band; animal census/services, not provider multiplication, control the program.'),
    ['Dual-access exam rooms', 'Open treatment and wash/prep', 'Surgery and recovery', 'Separate feline and canine housing', 'Isolation', 'Imaging', 'Lab/pharmacy', 'Comfort room', 'Reception/scale', 'Food and supplies', 'Laundry', 'Staff/admin', 'Toilets', 'Discreet rear/side access and freezer'], {
      power: ['Confirm radiography, freezer, laundry and gas equipment specifications; fixed CT requires a separate equipment/utility review.'],
      plumbing: ['Confirm treatment wash area, drains, laundry and animal-waste handling.'],
      hvac: ['Confirm separate animal/isolation exhaust, pressure zoning, conditioned makeup air and odor/noise mitigation; size to census and services.'],
      code: ['Ask about animal housing, isolation, waste, radiation shielding and veterinary licensing; do not import human patient-care electrical requirements automatically.'],
    }, null, ['NOT COVERED: no parking ratio is stated in this guide. Emergency overnight holding, boarding, large animals and multispecialty hospitals need separate programs.']),
  vision: build('Optometry', 'vision-vertical-guide', band(1500, 2000, 'Single-practitioner optometry starting band; no multi-doctor scaling formula supplied.'),
    ['Exam lanes', 'Pre-test', 'Frame display', 'Fitting area', 'Optical lab', 'Reception', 'Staff/admin', 'Toilets'], {
      plumbing: ['Confirm sinks in or near each exam lane and optical-lab connections.'],
      power: ['Confirm dedicated optical-lab circuit per the chosen edger and the other device schedules.'],
      code: ['Confirm chart optical distance and mirror configuration with the selected equipment; eye surgery and vision-therapy gyms need separate programs.'],
    }, null, ['NOT COVERED: no parking ratio stated in the guide. Training exam-lane dimensions are CARR estimates, not clearances or code minima.']),
  chiropractic: build('Chiropractic', 'other-healthcare-vertical-guide', { kind: 'chiro_allowance', type: 'calculated example', rule: 'NOT COVERED: training provides no sizing formula. Proposed allowance: treatment rooms × 100–120 net SF, plus 700–1,000 net SF support; multiply once by 1.30–1.45 for suite usable planning.' },
    ['Exam/adjustment rooms or bays', 'Reception/waiting', 'Imaging if selected', 'Staff/admin', 'Toilets', 'Equipment/storage'], {
      power: ['Ask for the selected x-ray generator circuit, disconnect, voltage and phase; do not assume every digital x-ray needs three-phase service.'],
      code: ['Confirm shielding, radiation registration and accessible equipment layout when imaging is selected.'],
      parking: ['Short visits create high turnover; ask for simultaneous patient/staff counts.'],
    }, null, ['NOT COVERED: sizing and parking ratio are absent from CARR training; the area result is a proposed planning allowance.']),
  therapy: build('Physical/occupational therapy', 'other-healthcare-vertical-guide', { kind: 'therapy_allowance', type: 'calculated example', rule: 'NOT COVERED: training provides no sizing formula. Proposed private-room example: treatment rooms × 150 net SF, plus 420 net SF gym and 700–1,000 net SF support; multiply once by 1.30–1.45. Open-station workflows require a separate program.' },
    ['Private assessment/treatment', 'Open exercise/gait area', 'Equipment storage', 'Changing area', 'Reception/larger waiting', 'Clean/soiled support', 'Laundry if selected', 'Staff/admin', 'Accessible toilets'], {
      power: ['Confirm laundry and hydrotherapy circuits if selected; wall-plug treatment devices do not establish overall service adequacy.'],
      hvac: ['Confirm gym outdoor-air and latent-load calculations, humidity control and separate treatment zones.'],
      plumbing: ['Confirm laundry/hydrotherapy connections and short accessible toilet routes.'],
      parking: ['Long sessions, mobility limitations and accompanying drivers can increase simultaneous parking demand.'],
      code: ['Confirm gym occupancy/ventilation classification, accessible circulation and any pool/hydrotherapy requirements locally.'],
    }, null, ['NOT COVERED: training provides no sizing or parking formula. Public military PT benchmarks are design references, not private-clinic adopted requirements.']),
};
export const practiceSchema = z.enum(['dental', ...Object.keys(VERTICALS)]);
export const canonical = value => value === 'dental' ? 'dental_gp' : value;
export const spaceInput = z.strictObject({ practice_type: practiceSchema,
  providers: z.number().int().min(1).max(50).describe('Simultaneous doctors; fixed specialty bands apply to one provider only.'),
  operatories: z.number().int().min(0).max(100).describe('Dental equipped treatment positions, including open-bay positions.'),
  exam_rooms: z.number().int().min(0).max(100).describe('Medical/vet/vision exam rooms or chiro/therapy private treatment rooms.'),
  rentable_to_usable_factor: z.number().finite().min(1).max(2).optional().describe('Owner-supplied verified property ratio. Omit when unknown; no default load factor.'),
});
export const checklistInput = z.strictObject({ practice_type: practiceSchema });
const checklistSchema = z.strictObject(Object.fromEntries(Object.keys(sharedChecks).map(k => [k, z.array(z.string())])));
export const checklistResults = z.strictObject({ practice_type: practiceSchema, source_class: z.literal('CARR agent training'), checklist: checklistSchema, mechanical_capacity: z.literal('unverified') });
export const spaceResults = z.strictObject({
  practice_type: practiceSchema, source_class: z.literal('CARR agent training'), sizing_rule: z.string(),
  usable_square_feet: rangeSchema, usable_area_type: numberType, usable_area_classification: evidenceClass,
  net_room_square_feet: rangeSchema.nullable(), rentable_square_feet: rangeSchema.nullable(),
  number_types: z.strictObject({ usable_square_feet: numberType, net_room_square_feet: numberType, rentable_square_feet: numberType, parking_ratio: numberType, parking_spaces: numberType }),
  public_benchmarks: z.array(z.strictObject({ name: z.string(), net_square_feet: z.number(), source_class: z.literal('published public benchmark'), evidence_classification: z.literal('public_benchmark'), type: numberType, applicability: z.string() })),
  rooms: z.array(z.strictObject({ room: z.string(), source_class: z.enum(['CARR agent training', 'proposed planning allowance']) })),
  parking: z.strictObject({ ratio: z.number().nullable(), spaces_needed: rangeSchema.nullable(), type: numberType,
    source_class: z.string(), area_basis: z.string(), status: z.literal('preliminary; local requirement unverified') }),
  due_diligence: checklistSchema, mechanical_capacity: z.literal('unverified'),
});
export function getChecklist(a) {
  const v = VERTICALS[canonical(a.practice_type)];
  return envelope(a, { practice_type: a.practice_type, source_class: 'CARR agent training', checklist: v.checklist, mechanical_capacity: 'unverified' }, {
    sources: [v.source], warnings: v.warnings,
    assumptions: ['MEP tonnage heuristics are unverified budget assumptions, never sizing or pass/fail rules. No capacity verdict is returned.'],
    missing_inputs: ['Selected equipment', 'Project-specific engineering', 'Adopted local requirements', 'Verified parking rights'],
  });
}
export function planSpace(a) {
  const v = VERTICALS[canonical(a.practice_type)], s = v.sizing;
  const dental = canonical(a.practice_type).startsWith('dental_');
  if (dental ? a.operatories < 1 || a.exam_rooms !== 0 : a.exam_rooms < 1 || a.operatories !== 0) throw new Error('incompatible room counts');
  if (s.kind === 'single_provider_band' && a.providers !== 1) throw new Error('no validated multi-provider formula');
  let usable, net = null;
  if (s.kind === 'operatories') usable = { low: a.operatories * s.sf, high: a.operatories * s.sf };
  else if (s.kind === 'doctors') usable = { low: s.first + (a.providers - 1) * s.additional_low, high: s.first + (a.providers - 1) * s.additional_high };
  else if (s.kind === 'single_provider_band') usable = { low: s.low, high: s.high };
  else {
    net = s.kind === 'chiro_allowance' ? { low: a.exam_rooms * 100 + 700, high: a.exam_rooms * 120 + 1000 }
      : { low: a.exam_rooms * 150 + 420 + 700, high: a.exam_rooms * 150 + 420 + 1000 };
    usable = { low: Math.ceil(net.low * 1.30), high: Math.ceil(net.high * 1.45) };
  }
  const parkingRatio = v.parking?.spaces_per_1000_sf;
  const missing = ['Equipment clearances and complete room program', 'Project-specific MEP and adopted code review', 'Peak parking demand and legal rights'];
  if (!a.rentable_to_usable_factor) missing.push('Verified rentable-to-usable area factor');
  if (!parkingRatio) missing.push('Parking ratio absent from training; obtain adopted ratio and operating demand');
  return envelope(a, {
    practice_type: a.practice_type, source_class: 'CARR agent training', sizing_rule: s.rule,
    usable_square_feet: usable, usable_area_type: s.type, usable_area_classification: 'planning_assumption', net_room_square_feet: net,
    number_types: { usable_square_feet: s.type, net_room_square_feet: 'calculated example', rentable_square_feet: 'calculated example', parking_ratio: 'CARR estimate', parking_spaces: 'calculated example' },
    public_benchmarks: s.kind === 'therapy_allowance' ? [
      { name: 'Private treatment reference', net_square_feet: 150, source_class: 'published public benchmark', evidence_classification: 'public_benchmark', type: 'calculated example', applicability: 'Input to this proposed example from the military program; not an adopted private-clinic minimum.' },
      { name: 'Exercise-area reference', net_square_feet: 420, source_class: 'published public benchmark', evidence_classification: 'public_benchmark', type: 'calculated example', applicability: 'Input to this proposed example; equipment-specific allowances remain additional and unverified.' },
    ] : [],
    rentable_square_feet: a.rentable_to_usable_factor ? { low: Math.ceil(usable.low * a.rentable_to_usable_factor), high: Math.ceil(usable.high * a.rentable_to_usable_factor) } : null,
    rooms: v.rooms.map(room => ({ room, source_class: v.supplementalRooms.includes(room) ? 'proposed planning allowance' : 'CARR agent training' })),
    parking: { ratio: parkingRatio ?? null, spaces_needed: parkingRatio ? { low: Math.ceil(usable.low * parkingRatio / 1000), high: Math.ceil(usable.high * parkingRatio / 1000) } : null,
      type: parkingRatio ? 'CARR estimate' : 'calculated example', source_class: parkingRatio ? 'CARR agent training' : 'not stated in training',
      area_basis: 'Usable SF for heuristic only; confirm local denominator', status: 'preliminary; local requirement unverified' },
    due_diligence: v.checklist, mechanical_capacity: 'unverified',
  }, { sources: [v.source, ...(net || v.supplementalRooms.length ? [allowanceSource] : []), ...(s.kind === 'therapy_allowance' ? [ptBenchmark] : [])], missing_inputs: missing,
    warnings: [...v.warnings, ...(s.kind === 'single_provider_band' ? ['Reference band is not scaled to the entered room count; confirm the room/equipment program before treating it as a space target.'] : [])],
    assumptions: ['Whole-office training estimates already include support and circulation; do not add a room grossing factor again.',
      'Derived parking and rentable-area numbers are calculated examples; the parking ratio is a CARR estimate. No code minima are inferred.',
      'All input counts are owner inputs. Listed functions must be programmed; the area heuristic does not prove they fit.',
      'HVAC tonnage is an unverified budget assumption, never a sizing or pass/fail rule.'],
    limitations: ['Specialty bands are single-provider starting ranges; extrapolation requires a separate clinical program.'], version: 'verticals-1.0.0' });
}
