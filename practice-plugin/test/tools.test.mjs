import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import Ajv from 'ajv';
import { TOOLS, callTool, INFORMATIONAL_PAGE, createTools } from '../src/tools.mjs';
import cases from './review-cases.json' with { type: 'json' };

const ajv = new Ajv({ strict: true });
const byName = Object.fromEntries(TOOLS.map(t => [t.name, t]));
const result = c => callTool(c.tool, c.input);
const urlPattern = /https?:\/\/[^\s"<>]+/g;
const strings = value => typeof value === 'string' ? [value]
  : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : [];

for (const c of cases) {
  test(`review golden: ${c.name}`, () => {
    const r = result(c);
    assert.equal(Boolean(r.isError), c.error);
    assert.deepEqual(r.structuredContent, c.expected);
    assert.equal(r.content[0].text, JSON.stringify(c.expected));
  });
}

for (const tool of TOOLS.filter(t => cases.some(c => c.tool === t.name))) {
  test(`${tool.name}: closed input and output schemas`, () => {
    const input = ajv.compile(tool.inputSchema);
    const output = ajv.compile(tool.outputSchema);
    const c = cases.find(c => c.tool === tool.name && !c.error);
    assert.ok(c, 'positive schema fixture required');
    assert.ok(input(c.input), JSON.stringify(input.errors));
    assert.ok(output(result(c).structuredContent), JSON.stringify(output.errors));
    assert.equal(input({ ...c.input, contact: 'synthetic-forbidden-field' }), false);
    assert.equal(input({}), false);
    assert.equal(output({ ...result(c).structuredContent, arbitrary: 1 }), false);
    assert.deepEqual(tool.annotations, {
      readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false,
    });
    assert.match(tool.description, /educational|informational/i);
  });
}

test('factual source links are separate from optional informational action links', () => {
  for (const c of cases) {
    const urls = strings(result(c)).flatMap(value => value.match(urlPattern) || []);
    assert.ok(urls.every(url => url === 'https://www.downtownmobile.org/uploads/pdf/OfficeMarketReport20247.1.2025.pdf'), c.name);
    if (c.tool !== 'estimate_occupancy_cost' || c.error) assert.equal(urls.length, 0);
  }
  const hostile = callTool('https://synthetic.invalid', { target: 'https://synthetic.invalid' });
  assert.equal((JSON.stringify(hostile).match(urlPattern) || []).length, 0);
  assert.match(byName.get_broker_search_help.description, /explicit.*actual.*search/i);
});

test('space planner rejects incompatible and unbounded rooms', () => {
  const base = cases[0].input;
  for (const input of [
    { ...base, providers: 0 }, { ...base, providers: 1.5 }, { ...base, providers: Infinity },
    { ...base, operatories: -1 }, { ...base, operatories: 101 },
    { ...base, operatories: 0 }, { ...base, exam_rooms: 1 },
    { ...base, practice_type: 'medical' }, { ...base, practice_type: 'veterinary' },
  ]) assert.equal(callTool('plan_practice_space', input).isError, true);
  const medical = callTool('plan_practice_space', { practice_type: 'medical', providers: 3, operatories: 0, exam_rooms: 8 }).structuredContent.results;
  assert.deepEqual(medical.usable_square_feet, { low: 2500, high: 3500 });
  assert.equal(medical.rentable_square_feet, null);

});

test('occupancy components sum and lease types change allocations', () => {
  for (const lease_type of ['full_service', 'modified_gross', 'triple_net']) {
    const r = callTool('estimate_occupancy_cost', { ...cases.find(c => c.tool === 'estimate_occupancy_cost' && !c.error).input, lease_type }).structuredContent.results;
    for (const side of ['low', 'high']) {
      assert.equal(r.annual_cost[side], r.components.reduce((n, c) => n + c.annual_cost[side], 0));
      assert.equal(r.monthly_cost[side], Math.round(r.annual_cost[side] / 12 * 100) / 100);
    }
    assert.match(r.source.method, /assum|sensitivity/i);
    assert.equal(r.currency, 'USD');
    assert.ok(r.source.data_date < r.source.retrieved_date);
  }
  for (const square_feet of [0, -1, 100001, NaN, '2000']) {
    assert.equal(callTool('estimate_occupancy_cost', { ...cases[1].input, square_feet }).isError, true);
  }
});

test('lease/buy checks zero-interest, all-cash, long holds and finite arithmetic', () => {
  const base = cases.find(c => c.tool === 'compare_lease_buy' && !c.error).input;
  const zero = callTool('compare_lease_buy', { ...base, interest_rate_percent: 0 }).structuredContent.results;
  assert.equal(zero.monthly_loan_payment, 2000);
  const cash = callTool('compare_lease_buy', { ...base, down_payment_percent: 100 }).structuredContent.results;
  assert.equal(cash.monthly_loan_payment, 0);
  assert.equal(cash.remaining_loan_balance, 0);
  const long = callTool('compare_lease_buy', { ...base, holding_years: 40 }).structuredContent.results;
  assert.equal(long.remaining_loan_balance, 0);
  for (const field of ['down_payment_percent', 'interest_rate_percent', 'closing_cost_percent', 'sale_cost_percent']) {
    assert.equal(callTool('compare_lease_buy', { ...base, [field]: -1 }).isError, true);
  }
});

test('source table is dated, source cited and contains no licensed data rows', () => {
  const data = JSON.parse(fs.readFileSync(new URL('../data/rates.json', import.meta.url)));
  assert.match(data.source.url, /^https:\/\/www\.downtownmobile\.org\//);
  assert.match(data.source.authorization, /aggregate factual/i);
  assert.equal(data.source.observed_annual_rate, 21.1);
  assert.match(data.source.data_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.match(data.source.retrieved_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(!Object.hasOwn(data, 'listings'));
  for (const row of Object.values(data.lease_types)) {
    for (const component of row) {
      assert.ok(component.basis);
      assert.ok(component.annual_rate.low <= component.annual_rate.high);
    }
  }
});


test('broker help has no link without a verified configured informational destination', () => {
  assert.equal(INFORMATIONAL_PAGE, null);
  assert.equal(callTool('get_broker_search_help', { request: 'actual_search_help' }).structuredContent.results.informational_page, null);
  for (const config of [null, {url:'https://example.com/info', verified_informational:true, verified_at:'2026-10-01'}, {url:'https://information.test/info'}, {url:'http://information.test/info', verified_informational:true, verified_at:'2026-10-01'}]) {
    assert.equal(callTool('get_broker_search_help', {request:'actual_search_help'}, createTools(config)).structuredContent.results.informational_page, null);
  }
  const tools = createTools({url:'https://information.test/info', verified_informational:true, verified_at:'2026-10-01'});
  assert.equal(callTool('get_broker_search_help', {request:'actual_search_help'}, tools).structuredContent.results.informational_page, 'https://information.test/info');
});
