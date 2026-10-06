import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from 'ajv';
import { callTool, TOOLS } from '../src/tools.mjs';
import { addMonths } from '../src/sale-readiness.mjs';

import { runwayFixture, conversionFixture, saleFixture, fact } from './fixtures.mjs';
const invoke = (name, args) => {
  const response = callTool(name, args); assert.ok(!response.isError, JSON.stringify(response)); return response.structuredContent.results;
};
const runway = a => invoke('calculate_practice_runway_v1', a);
const conversion = a => invoke('screen_practice_conversion_v1', a);
const sale = a => invoke('check_lease_sale_readiness_v1', a);

for (const missingProof of ['unresolved', 'mismatch', 'future']) {
  test(`conversion: non-applicability requires proof (${missingProof})`, () => {
    const a = conversionFixture(); a.requirements[0].applicability = 'no';
    if (missingProof === 'unresolved') {
      a.jurisdiction_resolved = false; a.jurisdiction_ref = null;
      a.requirements[0].evidence_ref = null; a.requirements[0].requirement_verified_on = null;
    } else if (missingProof === 'mismatch') a.requirements[0].jurisdiction_ref = 'other';
    else a.requirements[0].requirement_verified_on = '2026-10-02';
    const response = callTool('screen_practice_conversion_v1', a); assert.ok(!response.isError);
    const r = response.structuredContent.results;
    assert.equal(r.requirements[0].status, 'UNKNOWN', missingProof);
    assert.equal(r.outcome, 'NEEDS_VERIFICATION'); assert.equal(r.total_cost_usd, null);
    assert.ok(response.structuredContent.missing_inputs.length);
  });
}
test('conversion: documented non-applicability preserves complete costs', () => {
  const documented = conversionFixture(); documented.requirements[0].applicability = 'no';
  assert.equal(conversion(documented).requirements[0].status, 'NOT_APPLICABLE');
  assert.equal(conversion(documented).outcome, 'NO_IDENTIFIED_GAP');
  assert.deepEqual(conversion(documented).total_cost_usd, { low: 118000, high: 140000 });
});

test('conversion: unreviewed equipment clearances retain unresolved scope', () => {
  const a = conversionFixture(); a.requirements[0].existing.value = 400;
  a.equipment = [{ equipment_ref: 'device', specification_ref: 'spec', quantity: 1, clearance_reviewed: false }];
  const response = callTool('screen_practice_conversion_v1', a); assert.ok(!response.isError);
  const r = response.structuredContent.results;
  assert.equal(r.outcome, 'NEEDS_VERIFICATION'); assert.equal(r.total_cost_usd, null);
  assert.equal(r.tenant_economic_cost_usd, null); assert.equal(r.cost_per_usable_sf_usd, null);
  assert.deepEqual(r.known_cost_subtotal_usd, { low: 118000, high: 140000 });
  assert.match(response.structuredContent.missing_inputs.join(' '), /clearance.*device.*spec/i);
  assert.match(response.structuredContent.warnings.join(' '), /clearance/i);
  a.equipment[0].clearance_reviewed = true;
  const reviewed = callTool('screen_practice_conversion_v1', a).structuredContent;
  assert.equal(reviewed.results.outcome, 'NO_IDENTIFIED_GAP');
  assert.deepEqual(reviewed.results.total_cost_usd, { low: 118000, high: 140000 });
  assert.deepEqual(reviewed.missing_inputs, []);
});

for (const deadline of ['2026-12-01', '2027-01-01']) {
  test(`sale: undelivered notice due by closing is conditional (${deadline})`, () => {
    const a = saleFixture();
    for (const o of a.renewal_options) { o.notice_delivered = fact('no'); o.notice_deadline = deadline; }
    const r = sale(a);
    assert.equal(r.verified_control_end_exclusive, '2032-01-01');
    assert.equal(r.conditional_control_end_exclusive, '2042-01-01');
    assert.equal(r.coverage, 'SHORTFALL'); assert.equal(r.status, 'ACTION_REQUIRED');
    assert.match(r.flags.join(' '), /notice.*closing/i);
    assert.match(r.excluded_options[0].reason, /notice/i);
  });
}
test('sale: deadline day is still an unperformed notice dependency', () => {
  const a = saleFixture(); a.as_of_date = '2026-12-01';
  for (const o of a.renewal_options) o.notice_delivered = fact('no');
  const r = sale(a);
  assert.equal(r.deadlines[0].days, 0); assert.equal(r.status, 'ACTION_REQUIRED');
  assert.equal(r.verified_control_end_exclusive, '2032-01-01');
  assert.equal(r.conditional_control_end_exclusive, '2042-01-01');
});
test('sale: future buyer notice route requires documented exercise rights', () => {
  const a = saleFixture();
  for (const o of a.renewal_options) { o.notice_delivered = fact('no'); o.notice_deadline = '2027-01-02'; }
  assert.equal(sale(a).status, 'NO_IDENTIFIED_OBSTACLE');
  assert.equal(sale(a).verified_control_end_exclusive, '2042-01-01');
  a.renewal_options[0].exercisable_by_buyer = fact('unknown');
  assert.equal(sale(a).verified_control_end_exclusive, '2032-01-01');
  assert.equal(sale(a).conditional_control_end_exclusive, '2042-01-01');
  assert.equal(sale(a).coverage, 'SHORTFALL');
});

test('sale: renewal input permutations preserve chronological buyer coverage', () => {
  const a = saleFixture(), expected = sale(a); a.renewal_options.reverse();
  assert.deepEqual(sale(a), expected);
  assert.equal(a.renewal_options[0].id, 'second');
});
test('sale: an actual gap excludes the later chain in any input order', () => {
  const a = saleFixture(); a.renewal_options[1].start_date = '2037-02-01';
  const expected = sale(a); a.renewal_options.reverse();
  assert.deepEqual(sale(a), expected);
  assert.equal(expected.verified_control_end_exclusive, '2037-01-01');
  assert.equal(expected.status, 'ACTION_REQUIRED');
  assert.match(expected.excluded_options[0].reason, /gap/i);
});
for (const reverse of [false, true]) {
  test(`sale: ambiguous overlapping options refuse (${reverse})`, () => {
    const a = saleFixture(); a.renewal_options[1].start_date = '2036-01-01';
    if (reverse) a.renewal_options.reverse();
    assert.ok(callTool('check_lease_sale_readiness_v1', a).isError);
  });
}

for (const value of ['no', 'unknown']) {
  test(`sale: an unnecessary lender waiver may be ${value}`, () => {
    const a = saleFixture(); a.lender_waiver_applicable = fact('no'); a.lender_waiver = fact(value);
    const r = sale(a);
    assert.equal(r.status, 'NO_IDENTIFIED_OBSTACLE'); assert.equal(r.coverage, 'PASSES_CONFIGURED_CHECK');
    assert.ok(!r.flags.some(f => /waiver/i.test(f)));
  });
}
test('sale: a required lender waiver needs documented scoped terms', () => {
  const a = saleFixture(); a.lender_waiver_applicable = fact(); a.lender_waiver = fact('no');
  assert.equal(sale(a).status, 'ACTION_REQUIRED');
  a.lender_waiver = fact('unknown'); assert.equal(sale(a).status, 'REVIEW_REQUIRED');
  a.lender_waiver = fact(); assert.equal(sale(a).status, 'REVIEW_REQUIRED');
  a.lender_waiver_terms = { waived_requirement: 'coverage_months', required_months: 60, evidence_ref: 'lender_exception', verified_on: '2026-10-01' };
  a.renewal_options = [];
  const waived = sale(a); assert.equal(waived.status, 'NO_IDENTIFIED_OBSTACLE');
  assert.equal(waived.required_coverage_end, '2032-01-01'); assert.equal(waived.coverage, 'PASSES_CONFIGURED_CHECK');
  a.defaults_resolved = fact('no'); assert.equal(sale(a).status, 'ACTION_REQUIRED');
  a.defaults_resolved = fact(); a.required_consents_obtained = fact('no'); assert.equal(sale(a).status, 'ACTION_REQUIRED');
  a.required_consents_obtained = fact(); a.lender_waiver_terms.required_months = 120;
  assert.equal(sale(a).coverage, 'SHORTFALL');
  a.lender_waiver_terms.verified_on = '2026-10-02';
  assert.ok(callTool('check_lease_sale_readiness_v1', a).isError);
});
test('sale: unknown or unproven waiver applicability requires review', () => {
  const a = saleFixture(); a.lender_waiver_applicable = fact('unknown');
  assert.equal(sale(a).status, 'REVIEW_REQUIRED');
  assert.match(sale(a).flags.join(' '), /waiver.*applicab/i);
  a.lender_waiver_applicable = { value: 'no', evidence_ref: null };
  assert.equal(sale(a).status, 'REVIEW_REQUIRED');
  delete a.lender_waiver_applicable;
  assert.equal(sale(a).status, 'REVIEW_REQUIRED');
});
test('sale: unnecessary waiver never forgives a coverage shortfall', () => {
  const a = saleFixture(); a.lender_waiver_applicable = fact('no'); a.lender_waiver = fact('no'); a.renewal_options = [];
  const r = sale(a); assert.equal(r.coverage, 'SHORTFALL'); assert.equal(r.shortfall_months, 60);
  assert.equal(r.status, 'ACTION_REQUIRED');
});

test('A1 case 1: exact cash ledger and reserve funding', () => {
  const r = runway(runwayFixture()).base;
  assert.deepEqual(r.ledger.map(x => x.closing_cash_usd), [60000, 50000, 60000]);
  assert.equal(r.minimum_cash_usd, 50000); assert.equal(r.required_initial_cash_usd, 60000); assert.equal(r.additional_funding_needed_usd, 0);
});
test('A1 case 2: delayed receipts keep fixed-calendar costs; zero is not exhaustion', () => {
  const a = runwayFixture(); a.initial_cash_usd = 10000; a.reserve_floor_usd = 0; a.delay_months = 1; a.one_time_costs = []; a.recurring_costs[0].monthly_usd = 10000; a.receipts.monthly_usd = [20000, 20000, 20000];
  const r = runway(a); assert.deepEqual(r.delayed.ledger.map(x => x.closing_cash_usd), [0, 10000, 20000]);
  assert.equal(r.delayed.first_exhaustion_month, null); assert.equal(r.delayed.required_initial_cash_usd, 10000); assert.equal(r.base.required_initial_cash_usd, 0); assert.equal(r.delayed.receipts_beyond_horizon_usd, 20000);
});
test('A1 case 3: collection fraction and lag convolution', () => {
  const a = runwayFixture(); a.horizon_months = 2; a.one_time_costs = []; a.recurring_costs = [];
  a.receipts = { mode: 'billings', monthly_usd: [10000, 0], collection_fraction: .8, lag_weights: [.25, .75], opening_receivables_usd: [] };
  assert.deepEqual(runway(a).base.ledger.map(x => x.receipts_usd), [2000, 6000]);
});
test('A1 case 4: zero-rate debt and annual rentable-SF rent', () => {
  const a = runwayFixture(); a.one_time_costs = []; a.recurring_costs = [{ id: 'rent', start_month: 1, end_month: 3, timing: 'fixed_calendar', rent: { annual_usd_per_rentable_sf: 30, rentable_sf: 2000 } }];
  a.loan = { principal_usd: 120000, annual_rate_pct: 0, amortization_months: 12, funding_month: 1, payment_start_month: 1 };
  const r = runway(a).base; assert.equal(r.loan_payment_usd, 10000); assert.ok(r.ledger.every(x => x.costs_usd === 15000));
});
test('A1 case 5: rejects negative cash, bad lag weights and dual rent modes', () => {
  for (const mutate of [a => { a.initial_cash_usd = -1; }, a => { a.receipts = { mode: 'billings', monthly_usd: [10000, 0, 0], collection_fraction: 1, lag_weights: [.9], opening_receivables_usd: [] }; }, a => { a.recurring_costs[0].rent = { annual_usd_per_rentable_sf: 30, rentable_sf: 2000 }; }]) {
    const a = runwayFixture(); mutate(a); const r = callTool('calculate_practice_runway_v1', a); assert.ok(r.isError); assert.ok(r.structuredContent.invalid_fields.length);
  }
});
test('runway: exact decimal accumulation, payroll, shifted expense and incomplete horizon', () => {
  const a = runwayFixture(); a.one_time_costs = []; a.recurring_costs[0].monthly_usd = .1; a.receipts.monthly_usd = [.2, .2, .2];
  assert.deepEqual(runway(a).base.ledger.map(x => x.net_flow_usd), [.1, .1, .1]);
  delete a.recurring_costs[0].monthly_usd; a.recurring_costs[0].payroll = { hourly_usd: 20, paid_hours_per_month: 100, headcount: 2, employer_load_fraction: .25 };
  assert.equal(runway(a).base.ledger[0].costs_usd, 5000);
  a.one_time_costs = [{ id: 'shifted', month: 0, timing: 'opening_relative', amount_usd: 100 }]; a.delay_months = 1;
  assert.equal(runway(a).delayed.ledger[1].costs_usd, 5100);
  a.one_time_costs[0].month = 3; const r = runway(a).delayed; assert.equal(r.status, 'incomplete_model'); assert.equal(r.required_initial_cash_usd, null);
});
test('runway: funded loan stays in the ledger when first payment is beyond the horizon', () => {
  const a = runwayFixture(); a.loan = { principal_usd: 120000, annual_rate_pct: 0, amortization_months: 12, funding_month: 1, payment_start_month: 4 };
  const r = runway(a).base;
  assert.equal(r.ledger[0].injections_usd, 120000);
  assert.equal(r.ledger[0].closing_cash_usd, 180000);
  assert.equal(r.status, 'incomplete_model'); assert.equal(r.required_initial_cash_usd, null);
});
test('A2 case 1: construction-only contingency and reimbursement economics', () => {
  const r = conversion(conversionFixture()); assert.deepEqual(r.total_cost_usd, { low: 118000, high: 140000 });
  assert.deepEqual(r.tenant_economic_cost_usd, { low: 88000, high: 110000 }); assert.equal(r.reimbursement_month, 6);
});
test('A2 case 2: compatible verified power deficit', () => {
  const r = conversion(conversionFixture()); assert.equal(r.outcome, 'REMEDIATION_REQUIRED'); assert.equal(r.requirements[0].deficit, 200);
});
test('A2 case 3: unknown capacity never creates a guessed deficit', () => {
  const a = conversionFixture(); a.requirements[0].existing.status = 'unknown'; const r = conversion(a);
  assert.equal(r.outcome, 'NEEDS_VERIFICATION'); assert.equal(r.requirements[0].deficit, null); assert.equal(r.total_cost_usd, null);
});
test('A2 case 4: documented official use prohibition outranks physical suitability', () => {
  const a = conversionFixture(); Object.assign(a.requirements[0], { category: 'use', type: 'explicit_prohibition', required_value: true, units: 'boolean' });
  assert.equal(conversion(a).outcome, 'BLOCKED');
});
test('A2 case 5: required unpriced plumbing keeps total null with known subtotal', () => {
  const a = conversionFixture(); a.scope_lines = [{ ...a.scope_lines[0], low_lump_usd: 60000, high_lump_usd: 60000 }]; a.construction_contingency_fraction = 0;
  a.required_scope_ids = ['construction', 'plumbing']; a.requirements[0].remediation_scope_ids = ['plumbing'];
  const r = conversion(a); assert.equal(r.total_cost_usd, null); assert.deepEqual(r.known_cost_subtotal_usd, { low: 60000, high: 60000 }); assert.ok(r.unpriced_scope_ids.includes('plumbing')); assert.equal(r.cost_per_usable_sf_usd, null);
});
test('conversion: tax, mismatched units, stale quotes, allowance and duplicate scope', () => {
  const a = conversionFixture(); a.scope_lines[0].tax_inclusive = false; assert.equal(conversion(a).total_cost_usd, null);
  a.scope_lines[0].tax_inclusive = true; a.requirements[0].existing.phase = 'three'; assert.equal(conversion(a).requirements[0].status, 'UNKNOWN');
  a.requirements[0].existing.phase = 'single'; a.tenant_allowance.confirmed = false; assert.deepEqual(conversion(a).confirmed_allowance_applied_usd, { low: 0, high: 0 });
  a.scope_lines[0].quote_date = '2026-01-01'; assert.match(callTool('screen_practice_conversion_v1', a).structuredContent.warnings.join(' '), /REQUOTE/);
  a.scope_lines[1].shared_scope_group = a.scope_lines[0].shared_scope_group; assert.ok(callTool('screen_practice_conversion_v1', a).isError);
});
test('A3 case 1: contiguous verified options establish buyer coverage', () => {
  const r = sale(saleFixture()); assert.equal(r.verified_control_end_exclusive, '2042-01-01'); assert.equal(r.required_coverage_end, '2037-01-01'); assert.equal(r.coverage, 'PASSES_CONFIGURED_CHECK'); assert.equal(r.status, 'NO_IDENTIFIED_OBSTACLE');
});
test('A3 case 2: nontransferable first option prevents jumping to the second', () => {
  const a = saleFixture(); a.renewal_options[0].transferable_to_buyer = fact('no'); const r = sale(a);
  assert.equal(r.verified_control_end_exclusive, '2032-01-01'); assert.equal(r.excluded_options.length, 2); assert.equal(r.shortfall_months, 60); assert.equal(r.status, 'ACTION_REQUIRED');
});
test('A3 case 3: unresolved option gives conditional coverage, never a pass', () => {
  const a = saleFixture(); a.renewal_options = [a.renewal_options[0]]; a.renewal_options[0].transferable_to_buyer = fact('unknown');
  const r = sale(a); assert.equal(r.verified_control_end_exclusive, '2032-01-01'); assert.equal(r.conditional_control_end_exclusive, '2037-01-01'); assert.equal(r.coverage, 'SHORTFALL');
});
test('A3 case 4: notice overdue by one calendar day excludes option', () => {
  const a = saleFixture(); a.as_of_date = '2026-12-02'; a.renewal_options[0].notice_delivered = fact('no'); const r = sale(a);
  assert.equal(r.deadlines[0].days, -1); assert.equal(r.deadlines[0].urgency, 'overdue'); assert.equal(r.status, 'ACTION_REQUIRED');
});
test('A3 case 5: prohibited assignment blocks buyer control', () => {
  const a = saleFixture(); a.assignment_status = 'prohibited'; const r = sale(a); assert.equal(r.status, 'BLOCKED'); assert.equal(r.verified_control_end_exclusive, null); assert.equal(r.coverage, 'COVERAGE_NOT_ASSESSED');
});
test('sale: calendar clamp, missing lender, document conflict, termination and seller liability', () => {
  assert.equal(addMonths('2028-01-31', 1), '2028-02-29'); assert.equal(addMonths('2027-01-31', 1), '2027-02-28');
  const a = saleFixture(); a.lender_required_months = null; assert.equal(sale(a).coverage, 'COVERAGE_NOT_ASSESSED');
  a.documents_conflict = true; assert.equal(sale(a).status, 'DOCUMENT_CONFLICT'); assert.equal(sale(a).verified_control_end_exclusive, null); a.documents_conflict = false;
  a.personal_guarantee_release = fact('no'); assert.ok(sale(a).flags.includes('SELLER_CONTINUING_LIABILITY'));
  a.termination_rights = [{ id: 'demolition', type: 'demolition', applicable: fact(), earliest_date: '2026-12-31', effect: 'ends_occupancy', counsel_reviewed: true, evidence_ref: 'counsel' }]; assert.equal(sale(a).status, 'BLOCKED');
});
test('sale: expired base term and missing amendments never produce a pass', () => {
  const a = saleFixture(); a.documents_complete = false; assert.equal(sale(a).coverage, 'COVERAGE_NOT_ASSESSED');
  a.documents_complete = true; a.base_term_end_exclusive = '2026-12-01'; a.renewal_options = [];
  assert.equal(sale(a).status, 'ACTION_REQUIRED'); assert.equal(sale(a).coverage, 'SHORTFALL');
});
test('conversion: separate tax must be an explicitly priced tax line', () => {
  const a = conversionFixture(); a.scope_lines[0].tax_inclusive = false; a.scope_lines[0].separate_tax_scope_id = 'equipment';
  assert.equal(conversion(a).total_cost_usd, null);
  a.scope_lines.push({ ...a.scope_lines[2], id: 'taxes', category: 'tax', shared_scope_group: 'taxes', low_lump_usd: 1000, high_lump_usd: 1200 });
  a.scope_lines[0].separate_tax_scope_id = 'taxes'; assert.deepEqual(conversion(a).total_cost_usd, { low: 119000, high: 141200 });
});
const ajv = new Ajv({ strict: true });
for (const [name, fixture] of [['calculate_practice_runway_v1', runwayFixture], ['screen_practice_conversion_v1', conversionFixture], ['check_lease_sale_readiness_v1', saleFixture]]) {
  test(`${name}: closed JSON input/output schema and rejected private fields`, () => {
    const tool = TOOLS.find(t => t.name === name), input = ajv.compile(tool.inputSchema), output = ajv.compile(tool.outputSchema);
    const a = fixture(); assert.ok(input(a), JSON.stringify(input.errors)); const r = callTool(name, a); assert.ok(!r.isError); assert.ok(output(r.structuredContent), JSON.stringify(output.errors));
    assert.equal(input({ ...a, personal_data: 'synthetic' }), false); assert.equal(output({ ...r.structuredContent, extra: 1 }), false);
    assert.ok(callTool(name, { ...a, personal_data: 'synthetic' }).isError);
  });
}
