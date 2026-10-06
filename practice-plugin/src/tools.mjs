import { z } from 'zod';
import rates from '../data/rates.json' with { type: 'json' };
import { NOTICE, envelope, envelopeShape, publicSource, allowanceSource, PlanningInputError } from './evidence.mjs';
import { practiceSchema as practice, spaceInput, spaceResults, planSpace, checklistInput, checklistResults, getChecklist } from './verticals.mjs';
import { runwayInput, runwayResults, calculateRunway } from './runway.mjs';
import { conversionInput, conversionResults, screenConversion } from './conversion.mjs';
import { saleInput, saleResults, checkSale } from './sale-readiness.mjs';

// No destination is configured until an informational page has been independently verified.
export const INFORMATIONAL_PAGE = null;
const range = z.strictObject({ low: z.number().finite(), high: z.number().finite() });
const money = z.number().finite().nonnegative();
const textList = z.array(z.string());
const common = { limitations: textList, notice: z.literal(NOTICE) };
const occupancyInput = z.strictObject({
  square_feet: z.number().min(100).max(100000).describe('Rentable SF, never net room area or usable SF'), market: z.literal('mobile_downtown'),
  lease_type: z.enum(['full_service', 'modified_gross', 'triple_net']),
});
const buyInput = z.strictObject({
  square_feet: z.number().min(100).max(100000).describe('Rentable SF, never net room area or usable SF'), annual_lease_rate: z.number().min(0).max(200).describe('USD per rentable SF per year; include comparable tenant costs, not monthly rent'),
  purchase_price: z.number().min(1000).max(100000000),
  down_payment_percent: z.number().min(0).max(100), interest_rate_percent: z.number().min(0).max(30),
  loan_term_years: z.number().int().min(1).max(40), holding_years: z.number().int().min(1).max(40),
  annual_owner_cost: z.number().min(0).max(10000000).describe('Total USD per year for property tax, insurance, maintenance, utilities and comparable operating costs; excludes debt and upfront costs modeled separately'),
  closing_cost_percent: z.number().min(0).max(20), sale_cost_percent: z.number().min(0).max(20),
});

const round = n => Math.round(n * 100) / 100;
const scaled = (r, n) => ({ low: round(r.low * n), high: round(r.high * n) });
const summed = rows => ({
  low: round(rows.reduce((sum, r) => sum + r.low, 0)),
  high: round(rows.reduce((sum, r) => sum + r.high, 0)),
});
function estimateOccupancy(a) {
  const components = rates.lease_types[a.lease_type].map(c => ({
    ...c, annual_cost: scaled(c.annual_rate, a.square_feet),
  }));
  const annual = summed(components.map(c => c.annual_cost));
  // Factual source citations are kept separate from optional action links.
  const { title, data_date, retrieved_date, observed_annual_rate, method } = rates.source;
  return {
    ...a, components, annual_cost: annual, monthly_cost: scaled(annual, 1 / 12),
    source: { title, data_date, retrieved_date, observed_annual_rate, method },
    assumptions: [
      'Every range is an estimate in USD per rentable square foot per year, or USD for the stated period.',
      'The report is mixed general-office asking rent; 81.22% of reported asking rates are full service.',
      'Lease-type offsets and utilities are scenario assumptions, not observed healthcare expense data.',
    ], limitations: [
      'Source data are dated 2024-12-31; this is not a current quote or a medical-office market survey.',
      'Only Downtown Mobile is supported; other Gulf Coast markets need authorized dated sources.',
      'Excludes build-out, TI financing, equipment, deposits, moving costs, parking and practice operations; confirm lease inclusions.',
    ], notice: NOTICE,
  };
}

function compareBuy(a) {
  const down = a.purchase_price * a.down_payment_percent / 100;
  const principal = a.purchase_price - down;
  const n = a.loan_term_years * 12;
  const months = Math.min(a.holding_years * 12, n);
  const rate = a.interest_rate_percent / 1200;
  // log1p/expm1 avoid cancellation for very small positive interest rates.
  const payment = principal === 0 ? 0 : rate === 0 ? principal / n
    : principal * rate / -Math.expm1(-n * Math.log1p(rate));
  const growth = Math.expm1(months * Math.log1p(rate));
  const balance = months === n || principal === 0 ? 0 : Math.max(0,
    rate === 0 ? principal - payment * months : principal * (1 + growth) - payment * growth / rate);
  const upfront = down + a.purchase_price * a.closing_cost_percent / 100;
  const buyCash = upfront + payment * months + a.annual_owner_cost * a.holding_years;
  const proceeds = a.purchase_price * (1 - a.sale_cost_percent / 100) - balance;
  return {
    assumptions: a, monthly_loan_payment: round(payment), upfront_buy_cash: round(upfront),
    lease_total_cash: round(a.square_feet * a.annual_lease_rate * a.holding_years),
    buy_total_cash_before_sale: round(buyCash), remaining_loan_balance: round(balance),
    net_sale_proceeds: round(proceeds), buy_net_cost_after_sale: round(buyCash - proceeds),
    limitations: [
      'Scenario comparison only; neither option is recommended automatically.',
      'Assumes level lease rent, fixed-rate fully amortizing debt, no appreciation and sale at the input purchase price.',
      'Ignores tax effects, opportunity cost, inflation, major repairs, build-out and financing eligibility; compare equivalent lease/owner cost inclusions.',
    ], notice: NOTICE,
  };
}

function leaseChecklist(a) {
  const items = [
    { topic: 'Tenant improvements', questions: [
      'What TI allowance is available, when is it paid, and who covers overruns?',
      'Who approves plans, owns improvements and handles restoration at lease end?',
    ] },
    { topic: 'Exclusivity', questions: ['Can the landlord agree to a defined healthcare-use exclusivity, and what are its exceptions and remedies?'] },
    { topic: 'Permitted use', questions: ['Does the use clause cover the intended services and future practice changes, subject to zoning and licensing?'] },
    { topic: 'Assignment and practice sale', questions: [
      'Can the lease transfer with a practice sale or ownership change, and when is consent required?',
      'Is the original tenant or guarantor released after an approved assignment?',
    ] },
    { topic: 'HVAC and plumbing', questions: [
      'Can HVAC capacity, after-hours service, plumbing, electrical loads and equipment connections support the proposed medical use?',
      'Who pays for upgrades, maintenance, repairs and replacement?',
    ] },
    { topic: 'Timing and recurring costs', questions: [
      'When does rent start relative to delivery, permits and build-out?',
      'Which operating costs, tax increases, utilities and capital expenses can pass through, with what caps and audit rights?',
    ] },
    { topic: 'Access and exit', questions: ['What parking, accessibility, signage, renewal, casualty and early-exit terms are negotiable?'] },
  ];
  if (a.practice_type === 'dental' || a.practice_type.startsWith('dental_')) items.push({ topic: 'Dental equipment', questions: [
    'Can suction, compressed air, sterilization and chair plumbing be installed with required permits?',
  ] });
  if (a.practice_type === 'veterinary') items.push({ topic: 'Veterinary operations', questions: [
    'Are animal holding, waste handling, noise control and ventilation compatible with the permitted use?',
  ] });
  return { practice_type: a.practice_type, items, limitations: [
    'Every term is negotiable; the checklist does not determine enforceability or replace a qualified attorney.',
    'Verify zoning, permits, licensing and physical suitability with the relevant professionals.',
  ], notice: NOTICE };
}

function definition(name, description, input, output, calculate) {
  const json = (schema, io) => {
    const { $schema, ...body } = z.toJSONSchema(schema, { target: 'draft-7', io });
    return body;
  };
  return { name, description, inputSchema: json(input, 'input'), outputSchema: json(output, 'output'),
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    input, output, calculate };
}

const rateSource = publicSource('office-rent', 'Downtown Mobile Alliance', rates.source.title, rates.source.url, 'USD/rentable SF/year', rates.source.publication_date);
function legacyResult(a, calculate, sources = []) {
  const { assumptions, limitations, notice, ...results } = calculate(a);
  return envelope(a, { ...results, currency: 'USD', value_type: 'calculated example', evidence_classification: 'planning_assumption' }, {
    sources, assumptions: Array.isArray(assumptions) ? assumptions : ['All numeric values supplied by owner; no hidden market defaults.'], limitations,
  });
}
const wrapped = results => z.strictObject({ ...envelopeShape, results });
const financialTags = { currency: z.literal('USD'), value_type: z.literal('calculated example'), evidence_classification: z.literal('planning_assumption') };
const checklistResult = z.strictObject({ practice_type: practice,
  items: z.array(z.strictObject({ topic: z.string(), questions: textList })),
});
// Configuration is a reviewed static deployment input, never a user-supplied tool argument.
function informationalPage(config) {
  if (!config || config.verified_informational !== true || !config.verified_at) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(config.verified_at)) return null;
  try {
    const u = new URL(config.url);
    if (u.protocol !== 'https:' || u.username || u.password || u.search || u.hash ||
      /(^|\.)(example\.(com|org|net)|localhost)$|\.invalid$/.test(u.hostname)) return null;
    return u.href;
  } catch { return null; }
}
export function createTools(config = null) {
  const page = informationalPage(config);
  return [
    definition('plan_practice_space',
      'Estimate preliminary educational usable space, room functions, parking and building due diligence from per-vertical CARR agent training. Dental subtypes, primary care, small-animal veterinary, optometry, chiropractic and physical therapy. Missing training rules remain explicit; not a code or fit verdict.',
      spaceInput, wrapped(spaceResults), planSpace),
    definition('get_practice_building_checklist',
      'Return a read-only educational building due-diligence checklist for a healthcare vertical: questions about power, plumbing, HVAC zoning, parking and code triggers. No landlord contact or suitability certification.',
      checklistInput, wrapped(checklistResults), getChecklist),
    definition('estimate_occupancy_cost',
      'Estimate educational annual and monthly occupancy ranges in USD using rentable SF and a bundled dated Downtown Mobile general-office survey. Not a current healthcare quote.',
      occupancyInput, wrapped(z.strictObject({ ...occupancyInput.shape, ...financialTags,
        components: z.array(z.strictObject({ component: z.string(), annual_rate: range, annual_cost: range, basis: z.string() })),
        annual_cost: range, monthly_cost: range,
        source: z.strictObject({ title: z.string(), data_date: z.string(), retrieved_date: z.string(), observed_annual_rate: money, method: z.string() }),
      })), a => legacyResult(a, estimateOccupancy, [rateSource, allowanceSource])),
    definition('compare_lease_buy',
      'Calculate an educational USD lease-versus-buy cash scenario. Lease rate is USD per rentable SF per year; annual owner costs include comparable operating costs. No tax, appreciation or financing recommendation.',
      buyInput, wrapped(z.strictObject({ ...financialTags, monthly_loan_payment: money, upfront_buy_cash: money,
        lease_total_cash: money, buy_total_cash_before_sale: money, remaining_loan_balance: money,
        net_sale_proceeds: z.number().finite(), buy_net_cost_after_sale: z.number().finite(),
      })), a => legacyResult(a, compareBuy)),
    definition('get_healthcare_lease_checklist',
      'Provide an educational checklist of negotiable healthcare lease questions. Not a lease sale-readiness determination or legal advice.',
      checklistInput, wrapped(checklistResult), a => {
        const { notice, limitations, ...results } = leaseChecklist(a); return envelope(a, results, { limitations });
      }),
    definition('calculate_practice_runway_v1',
      'Calculate an educational monthly USD cash ledger, funding gap and base/delayed opening scenarios from owner assumptions. Fixed-calendar costs stay fixed. No revenue forecast or financing approval; synthetic line identifiers only.',
      runwayInput, wrapped(runwayResults), calculateRunway),
    definition('screen_practice_conversion_v1',
      'Screen educational conversion requirements and USD costs using owner-supplied evidence and compatible capacity units. Unknown or unpriced mandatory scope keeps totals incomplete. No code certification; use synthetic references, never addresses or document text.',
      conversionInput, wrapped(conversionResults), screenConversion),
    definition('check_lease_sale_readiness_v1',
      'Check educational lease sale-readiness from documented dates, assignment, contiguous buyer-controlled options, notices and lender terms. Evidence references are synthetic; no lease text. Not legal approval.',
      saleInput, wrapped(saleResults), checkSale),
    definition('get_broker_search_help',
      'Return a plain CARR description and a verified informational page, if configured, only after an explicit user request for help with an actual property search. Do not use for general education or unsolicited referrals. No contact capture or search.',
      z.strictObject({ request: z.literal('actual_search_help') }), wrapped(z.strictObject({
        description: z.string(), informational_page: z.string().nullable(),
      })), a => envelope(a, {
        description: 'CARR is a commercial real estate brokerage that represents healthcare tenants and buyers.', informational_page: page,
      }, { missing_inputs: page ? [] : ['Verified informational page URL is not configured'],
        limitations: ['No property search, contact capture or message is performed by this tool.'] })),
  ];
}
export const TOOLS = createTools();

export function callTool(name, args, tools = TOOLS) {
  const tool = tools.find(t => t.name === name);
  if (!tool) return refusal('Unknown planning tool.');
  const parsed = tool.input.safeParse(args);
  if (!parsed.success) {
    if (name === 'get_broker_search_help') return refusal('Use this informational tool only after an explicit request for help with an actual property search.');
    if (name === 'estimate_occupancy_cost') return refusal('Unsupported planning input. Only the dated Downtown Mobile table is available; do not supply contact or listing data.');
    const fields = [...new Set(parsed.error.issues.map(issue => issue.path.length ? issue.path.join('.') : 'declared_fields'))];
    return refusal('Invalid planning input. Use only the declared fields and supported numeric ranges; do not supply personal or listing data.', fields);
  }
  try {
    const result = tool.output.parse(tool.calculate(parsed.data));
    return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result };
  } catch (error) {
    if (error instanceof PlanningInputError) return refusal('Invalid planning input. Check the named declared fields.', error.fields);
    return refusal('Invalid planning input. Check compatible room counts and numeric assumptions; professional review is required.');
  }
}

function refusal(error, invalid_fields) {
  // Never reflect raw input, schema errors, URLs, identity or diagnostics.
  const result = { error, ...(invalid_fields ? { invalid_fields } : {}) };
  return { isError: true, content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result };
}
