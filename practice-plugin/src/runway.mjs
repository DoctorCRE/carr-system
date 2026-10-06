import { z } from 'zod';
import Decimal from 'decimal.js';
import { envelope, PlanningInputError } from './evidence.mjs';

Decimal.set({ precision: 40 });
export const usd = z.number().finite().nonnegative().max(1e12);
export const id = z.string().regex(/^[a-z][a-z0-9_]{0,47}$/).describe('Synthetic evidence/line identifier only; no names, addresses, document text or personal data.');
export const D = value => new Decimal(value);
export const dollars = value => D(value).toDecimalPlaces(2, Decimal.ROUND_HALF_UP).toNumber();
const month = z.number().int().min(1).max(120);
const timing = z.enum(['fixed_calendar', 'opening_relative']);
const relative = z.number().int().min(-120).max(240);
const rent = z.strictObject({ annual_usd_per_rentable_sf: usd, rentable_sf: z.number().positive().max(100000) });
const payroll = z.strictObject({ hourly_usd: usd, paid_hours_per_month: z.number().nonnegative().max(1000), headcount: z.number().int().nonnegative().max(1000), employer_load_fraction: z.number().min(0).max(1) });
export const runwayInput = z.strictObject({
  start_month: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/), horizon_months: month, opening_month: month,
  delay_months: z.number().int().min(0).max(120), initial_cash_usd: usd, reserve_floor_usd: usd,
  cash_injections: z.array(z.strictObject({ id, month, amount_usd: usd, type: z.enum(['equity', 'loan', 'grant']) })).max(120),
  one_time_costs: z.array(z.strictObject({ id, amount_usd: usd, month: relative, timing })).max(120),
  recurring_costs: z.array(z.strictObject({ id, monthly_usd: usd.optional(), rent: rent.optional(), payroll: payroll.optional(), start_month: relative, end_month: relative, timing })).max(120),
  receipts: z.union([
    z.strictObject({ mode: z.literal('cash'), monthly_usd: z.array(usd).min(1).max(120) }),
    z.strictObject({ mode: z.literal('billings'), monthly_usd: z.array(usd).min(1).max(120), collection_fraction: z.number().min(0).max(1),
      lag_weights: z.array(z.number().min(0).max(1)).min(1).max(25), opening_receivables_usd: z.array(usd).max(120) }),
  ]),
  loan: z.strictObject({ principal_usd: usd.refine(n => n > 0), annual_rate_pct: z.number().min(0).max(100), amortization_months: z.number().int().min(1).max(600), funding_month: month, payment_start_month: month }).optional(),
  missing_cost_categories: z.array(z.enum(['rent', 'cam', 'insurance', 'payroll', 'utilities', 'debt', 'draws', 'taxes', 'equipment', 'other'])).max(10),
});
const ledgerRow = z.strictObject({ month: z.number().int(), calendar_month: z.string(), receipts_usd: usd, injections_usd: usd, costs_usd: usd, net_flow_usd: z.number(), closing_cash_usd: z.number() });
const scenarioSchema = z.strictObject({ status: z.enum(['complete', 'incomplete_model']), ledger: z.array(ledgerRow), minimum_cash_usd: z.number(), minimum_cash_month: z.number().int(),
  first_exhaustion_month: z.number().int().nullable(), first_reserve_breach_month: z.number().int().nullable(), required_initial_cash_usd: usd.nullable(), additional_funding_needed_usd: usd.nullable(),
  receipts_beyond_horizon_usd: usd, loan_payment_usd: usd.nullable(), omitted_cost_ids: z.array(id) });
export const runwayResults = z.strictObject({ currency: z.literal('USD'), value_type: z.literal('calculated example'), evidence_classification: z.literal('planning_assumption'), base: scenarioSchema, delayed: scenarioSchema });

function payment(loan) {
  const p = D(loan.principal_usd), r = D(loan.annual_rate_pct).div(1200), n = loan.amortization_months;
  return r.isZero() ? p.div(n) : p.mul(r).div(D(1).minus(D(1).plus(r).pow(-n)));
}
const calendar = (start, offset) => {
  const [y, m] = start.split('-').map(Number), total = y * 12 + m - 1 + offset;
  return `${Math.floor(total / 12).toString().padStart(4, '0')}-${(total % 12 + 1).toString().padStart(2, '0')}`;
};
export function calculateRunway(a) {
  const ids = [...a.cash_injections, ...a.one_time_costs, ...a.recurring_costs].map(x => x.id);
  if (new Set(ids).size !== ids.length) throw new PlanningInputError(['cash_injections.id', 'one_time_costs.id', 'recurring_costs.id']);
  if (a.opening_month > a.horizon_months || a.receipts.monthly_usd.length !== a.horizon_months) throw new PlanningInputError(['opening_month', 'horizon_months', 'receipts.monthly_usd']);
  if (a.receipts.mode === 'billings' && D(a.receipts.lag_weights.reduce((sum, x) => sum.plus(x), D(0))).minus(1).abs().gt('0.000001')) throw new PlanningInputError(['receipts.lag_weights']);
  for (const c of a.recurring_costs) {
    if ([c.monthly_usd, c.rent, c.payroll].filter(x => x !== undefined).length !== 1 || c.end_month < c.start_month) throw new PlanningInputError(['recurring_costs.monthly_usd', 'recurring_costs.rent', 'recurring_costs.payroll', 'recurring_costs.start_month', 'recurring_costs.end_month']);
  }
  if (a.loan && (a.cash_injections.some(x => x.type === 'loan') || a.loan.payment_start_month < a.loan.funding_month)) throw new PlanningInputError(['loan', 'cash_injections']);
  const amount = c => c.monthly_usd !== undefined ? D(c.monthly_usd) : c.rent ? D(c.rent.annual_usd_per_rentable_sf).mul(c.rent.rentable_sf).div(12)
    : D(c.payroll.hourly_usd).mul(c.payroll.paid_hours_per_month).mul(c.payroll.headcount).mul(D(1).plus(c.payroll.employer_load_fraction));
  const loanPayment = a.loan ? payment(a.loan) : null;
  function scenario(delay) {
    const h = a.horizon_months, cost = Array.from({ length: h }, () => D(0)), injections = cost.map(() => D(0));
    const omitted = new Set();
    const resolve = (n, basis) => basis === 'fixed_calendar' ? n : a.opening_month + delay + n;
    for (const c of a.one_time_costs) {
      const m = resolve(c.month, c.timing);
      if (m < 1 || m > h) omitted.add(c.id); else cost[m - 1] = cost[m - 1].plus(c.amount_usd);
    }
    for (const c of a.recurring_costs) {
      const start = resolve(c.start_month, c.timing), end = resolve(c.end_month, c.timing);
      if (start < 1 || end > h) omitted.add(c.id);
      for (let m = Math.max(1, start); m <= Math.min(h, end); m++) cost[m - 1] = cost[m - 1].plus(amount(c));
    }
    for (const c of a.cash_injections) { if (c.month > h) omitted.add(c.id); else injections[c.month - 1] = injections[c.month - 1].plus(c.amount_usd); }
    if (a.loan) {
      if (a.loan.funding_month > h || a.loan.payment_start_month > h) omitted.add('loan_schedule');
      if (a.loan.funding_month <= h) injections[a.loan.funding_month - 1] = injections[a.loan.funding_month - 1].plus(a.loan.principal_usd);
      for (let m = a.loan.payment_start_month; m <= Math.min(h, a.loan.payment_start_month + a.loan.amortization_months - 1); m++) cost[m - 1] = cost[m - 1].plus(loanPayment);
    }
    const receipts = new Map();
    const addReceipt = (m, v) => receipts.set(m, (receipts.get(m) || D(0)).plus(v));
    a.receipts.monthly_usd.forEach((v, i) => {
      const m = a.opening_month + delay + i;
      if (a.receipts.mode === 'cash') addReceipt(m, v);
      else a.receipts.lag_weights.forEach((weight, lag) => addReceipt(m + lag, D(v).mul(a.receipts.collection_fraction).mul(weight)));
    });
    if (a.receipts.mode === 'billings') a.receipts.opening_receivables_usd.forEach((v, i) => addReceipt(i + 1, v));
    let cash = D(a.initial_cash_usd), minCash = cash, minMonth = 0, cumulative = D(0), minFlow = D(0), exhaustion = null, breach = cash.lt(a.reserve_floor_usd) ? 0 : null;
    const ledger = [];
    for (let m = 1; m <= h; m++) {
      const receipt = receipts.get(m) || D(0), flow = receipt.plus(injections[m - 1]).minus(cost[m - 1]);
      cumulative = cumulative.plus(flow); minFlow = Decimal.min(minFlow, cumulative); cash = cash.plus(flow);
      if (cash.lt(minCash)) { minCash = cash; minMonth = m; }
      if (cash.lt(0) && exhaustion === null) exhaustion = m;
      if (cash.lt(a.reserve_floor_usd) && breach === null) breach = m;
      ledger.push({ month: m, calendar_month: calendar(a.start_month, m - 1), receipts_usd: dollars(receipt), injections_usd: dollars(injections[m - 1]), costs_usd: dollars(cost[m - 1]), net_flow_usd: dollars(flow), closing_cash_usd: dollars(cash) });
    }
    const complete = !omitted.size && !a.missing_cost_categories.length;
    const required = Decimal.max(0, D(a.reserve_floor_usd).minus(minFlow));
    return { status: complete ? 'complete' : 'incomplete_model', ledger, minimum_cash_usd: dollars(minCash), minimum_cash_month: minMonth,
      first_exhaustion_month: exhaustion, first_reserve_breach_month: breach, required_initial_cash_usd: complete ? dollars(required) : null,
      additional_funding_needed_usd: complete ? dollars(Decimal.max(0, required.minus(a.initial_cash_usd))) : null,
      receipts_beyond_horizon_usd: dollars([...receipts].filter(([m]) => m > h).reduce((n, [, v]) => n.plus(v), D(0))),
      loan_payment_usd: loanPayment === null ? null : dollars(loanPayment), omitted_cost_ids: [...omitted] };
  }
  const base = scenario(0), delayed = scenario(a.delay_months);
  return envelope(a, { currency: 'USD', value_type: 'calculated example', evidence_classification: 'planning_assumption', base, delayed }, {
    assumptions: ['Opening-relative costs and revenue shift; fixed-calendar rent and debt do not.', 'Receipts array is an opening-relative ramp. Opening receivables are an explicit fixed-calendar cash schedule.', 'Initial cash excludes the optional modeled loan; benefits/taxes included in payroll load must not be duplicated elsewhere.'],
    warnings: base.status !== 'complete' || delayed.status !== 'complete' ? ['Incomplete model: missing costs or timing outside the horizon; no funding recommendation.'] : [],
    missing_inputs: a.missing_cost_categories, limitations: ['No exhaustion within the horizon does not mean unlimited runway. Revenue and timing are owner assumptions.', 'Fixed fully amortizing loans only; other debt requires explicit cash schedules.'] });
}
