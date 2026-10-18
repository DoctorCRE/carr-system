import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import Ajv from 'ajv';
import { VERTICALS } from '../src/verticals.mjs';
import { callTool, TOOLS } from '../src/tools.mjs';
import { runwayFixture, conversionFixture, saleFixture } from './fixtures.mjs';

const ajv = new Ajv({ strict: true });
const spaceTool = TOOLS.find(t => t.name === 'plan_practice_space');
const checklistTool = TOOLS.find(t => t.name === 'get_practice_building_checklist');
const inputSchema = ajv.compile(spaceTool.inputSchema), outputSchema = ajv.compile(spaceTool.outputSchema);
// Expected values are independently calculated from the stated training rules.
// Chiro/PT have no training formula: these cases verify the explicitly separate proposed allowance.
const cases = [
  ['dental_gp', 1, 5, 0, 5 * 400, 5 * 400], ['dental_pedo', 1, 6, 0, 6 * 400, 6 * 400],
  ['dental_ortho', 1, 5, 0, 2000, 2500], ['dental_endo', 1, 3, 0, 1500, 1800],
  ['dental_perio', 1, 5, 0, 5 * 400, 5 * 400], ['dental_prostho', 1, 4, 0, 4 * 400, 4 * 400],
  ['dental_oms', 1, 3, 0, 1800, 2400], ['medical', 3, 0, 8, 1500 + 2 * 500, 1500 + 2 * 1000],
  ['veterinary', 1, 0, 3, 1500, 2500], ['vision', 1, 0, 3, 1500, 2000],
  ['chiropractic', 1, 0, 3, Math.ceil((3 * 100 + 700) * 1.30), Math.ceil((3 * 120 + 1000) * 1.45)],
  ['therapy', 2, 0, 2, Math.ceil((2 * 150 + 420 + 700) * 1.30), Math.ceil((2 * 150 + 420 + 1000) * 1.45)],
];
const example = c => ({ practice_type: c[0], providers: c[1], operatories: c[2], exam_rooms: c[3] });
for (const c of cases) {
  test(`${c[0]}: guide sizing, schema and read-only building checklist`, () => {
    const a = example(c); assert.ok(inputSchema(a), JSON.stringify(inputSchema.errors));
    const result = callTool('plan_practice_space', a); assert.ok(!result.isError); const r = result.structuredContent;
    assert.ok(outputSchema(r), JSON.stringify(outputSchema.errors));
    assert.deepEqual(r.results.usable_square_feet, { low: c[4], high: c[5] });
    assert.equal(r.results.rentable_square_feet, null);
    assert.equal(r.results.source_class, 'CARR agent training'); assert.ok(r.sources.some(s => s.source_class === 'CARR agent training'));
    assert.ok(r.results.rooms.length); assert.equal(r.results.mechanical_capacity, 'unverified');
    assert.match(r.notice, /Preliminary screening.*designer.*permitting authority/);
    const checklist = callTool('get_practice_building_checklist', { practice_type: c[0] });
    assert.ok(ajv.compile(checklistTool.outputSchema)(checklist.structuredContent));
    assert.deepEqual(checklist.structuredContent.results.checklist, r.results.due_diligence);
    assert.ok(Object.values(r.results.due_diligence).every(x => x.length > 0));
    assert.equal(outputSchema({ ...r, arbitrary: 1 }), false);
  });
}
test('every catalog vertical has an independent sizing case', () => {
  assert.deepEqual(cases.map(c => c[0]).sort(), Object.keys(VERTICALS).sort());
});
test('primary-care support supplements are separate from training room facts', () => {
  const r = callTool('plan_practice_space', example(cases.find(c => c[0] === 'medical'))).structuredContent;
  assert.equal(r.results.rooms.find(x => x.room === 'Nursing work area').source_class, 'proposed planning allowance');
  assert.equal(r.results.rooms.find(x => x.room === 'Exam rooms with sinks').source_class, 'CARR agent training');
  assert.ok(r.sources.some(x => x.source_class === 'proposed planning allowance'));
});
test('whole-office dental rule has no duplicated circulation and explicit area conversion', () => {
  const a = example(cases[0]), r = callTool('plan_practice_space', { ...a, rentable_to_usable_factor: 1.2 }).structuredContent.results;
  assert.deepEqual(r.usable_square_feet, { low: 2000, high: 2000 }); assert.equal(r.net_room_square_feet, null);
  assert.deepEqual(r.rentable_square_feet, { low: 2400, high: 2400 }); assert.deepEqual(r.parking.spaces_needed, { low: 10, high: 10 });
  assert.equal(r.parking.ratio, 5); assert.equal(r.parking.type, 'CARR estimate');
  assert.ok(callTool('plan_practice_space', { ...a, rentable_to_usable_factor: .8 }).isError);
});
test('missing training formulas and specialty caveats remain visible', () => {
  for (const c of cases.filter(c => ['dental_endo', 'dental_ortho', 'dental_oms', 'chiropractic', 'therapy'].includes(c[0]))) {
    const r = callTool('plan_practice_space', example(c)).structuredContent; assert.match(r.warnings.join(' '), /NOT COVERED/);
  }
  for (const name of ['veterinary', 'vision', 'chiropractic', 'therapy']) {
    const r = callTool('plan_practice_space', example(cases.find(c => c[0] === name))).structuredContent;
    assert.equal(r.results.parking.spaces_needed, null); assert.match(r.missing_inputs.join(' '), /Parking ratio absent/);
  }
  const r = callTool('get_practice_building_checklist', { practice_type: 'dental_gp' }).structuredContent;
  assert.match(JSON.stringify(r), /DISAGREE.*automatic sprinkler/); assert.match(JSON.stringify(r), /NOT COVERED.*sterilization/);
  assert.match(r.assumptions.join(' '), /unverified budget assumption.*never sizing or pass\/fail/);
  assert.ok(callTool('plan_practice_space', { ...example(cases[3]), providers: 2 }).isError);
});
test('forbidden training material and client references never enter source or tool outputs', () => {
  const excludedHeadings = [['Pitch', 'to', 'Landlord'].join(' '), ['Sample', 'Layout'].join(' ')];
  const forbidden = new RegExp([...excludedHeadings, '\\bC-\\s*\\d+\\b'].join('|'), 'i');
  for (const directory of ['src', 'test', 'ui']) {
    for (const f of fs.readdirSync(new URL(`../${directory}/`, import.meta.url))) {
      assert.doesNotMatch(fs.readFileSync(new URL(`../${directory}/${f}`, import.meta.url), 'utf8'), forbidden, f);
    }
  }
  const args = {
    plan_practice_space: example(cases[0]), get_practice_building_checklist: { practice_type: 'medical' },
    estimate_occupancy_cost: { square_feet: 2000, market: 'mobile_downtown', lease_type: 'full_service' },
    compare_lease_buy: { square_feet: 2000, annual_lease_rate: 30, purchase_price: 600000, down_payment_percent: 20, interest_rate_percent: 0, loan_term_years: 20, holding_years: 5, annual_owner_cost: 10000, closing_cost_percent: 2, sale_cost_percent: 5 },
    get_healthcare_lease_checklist: { practice_type: 'medical' }, get_broker_search_help: { request: 'actual_search_help' },
    calculate_practice_runway_v1: runwayFixture(), screen_practice_conversion_v1: conversionFixture(), check_lease_sale_readiness_v1: saleFixture(),
  };
  for (const t of TOOLS) {
    assert.doesNotMatch(JSON.stringify(t), forbidden); const r = callTool(t.name, args[t.name]); assert.ok(!r.isError);
    assert.doesNotMatch(JSON.stringify(r), forbidden);
    const rejected = callTool(t.name, { ...args[t.name], prohibited: ['C-' + '999', ...excludedHeadings].join(' ') });
    assert.ok(rejected.isError); assert.doesNotMatch(JSON.stringify(rejected), forbidden);
  }
  for (const c of cases) assert.doesNotMatch(JSON.stringify(callTool('plan_practice_space', example(c))), forbidden);
});
