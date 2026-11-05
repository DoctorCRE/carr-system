import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { composeMarketStudyPackage } from '../src/market-study-composer.js';
import { RECORD_LAYER_GROUPS, validateMarketStudyPackage } from '../src/market-study-package.js';

const fixture = JSON.parse(readFileSync(new URL('../../client-portal/fixtures/example-health.market-study.json', import.meta.url), 'utf8'));
const target = { deal_id: 'synthetic-deal', client_id: 'synthetic-client' };
const wrap = data => ({ ...target, data: structuredClone(data) });
function inputs() {
  const research = structuredClone(fixture);
  for (const group of RECORD_LAYER_GROUPS) delete research[group];
  return { research, reviewed: {
    client: wrap(fixture.client), prepared: wrap(fixture.prepared),
    properties: fixture.properties.map(wrap),
  } };
}
function assetEnvelopes(value) {
  if (!value || typeof value !== 'object') return [];
  if (value.sha256 && value.name && value.type) return [wrap(value)];
  return Object.values(value).flatMap(assetEnvelopes);
}
const evidence = () => ({ research: { ...target }, assets: assetEnvelopes(fixture) });
const compose = ({ research, reviewed, membership = evidence() }) => composeMarketStudyPackage(research, reviewed, target, membership);
function refuses(input, stage, path) {
  const result = compose(input);
  assert.equal(result.ok, false);
  assert.equal(result.stage, stage);
  assert.ok(result.errors.some(error => error.path === path), JSON.stringify(result));
  assert.equal(Object.hasOwn(result, 'package'), false);
  assert.deepEqual(compose(input), result);
}

test('composes only reviewed record groups and preserves every research value and asset', () => {
  const input = inputs();
  const before = structuredClone(input);
  const result = compose(input);
  assert.deepEqual(result, { ok: true, package: fixture, errors: [] });
  assert.equal(validateMarketStudyPackage(result.package).ok, true);
  assert.deepEqual(input, before);
  result.package.sources.sections[0].heading = 'Changed';
  result.package.properties[0].photo.name = 'changed.jpg';
  assert.deepEqual(input, before, 'output shares no mutable references with inputs');
});

test('replaces present record groups completely without merging old client fields', () => {
  const input = inputs();
  Object.assign(input.research, structuredClone(fixture));
  delete input.reviewed.client.data.website;
  input.reviewed.client.data.display_name = 'Reviewed client';
  const result = compose(input);
  assert.equal(result.ok, true);
  assert.equal(result.package.client.display_name, 'Reviewed client');
  assert.equal(Object.hasOwn(result.package.client, 'website'), false);
});

test('validates research before overlay even when reviewed data would repair it', () => {
  const input = inputs();
  input.research.client = { internal_notes: 'private' };
  refuses(input, 'research', '/client/internal_notes');
});

for (const group of RECORD_LAYER_GROUPS) {
  test(`requires an explicit reviewed ${group} group`, () => {
    const input = inputs();
    delete input.reviewed[group];
    refuses(input, 'reviewed', `/${group}`);
  });
}
for (const field of ['deal_id', 'client_id']) {
  for (const group of ['client', 'prepared', 'properties']) {
    test(`rejects mismatched ${field} on ${group}`, () => {
      const input = inputs();
      const envelope = group === 'properties' ? input.reviewed.properties[1] : input.reviewed[group];
      envelope[field] = 'another-record';
      refuses(input, 'reviewed', `/${group}${group === 'properties' ? '/1' : ''}/${field}`);
    });
  }
  test(`requires target ${field}`, () => {
    const input = inputs();
    const scope = { ...target };
    delete scope[field];
    const result = composeMarketStudyPackage(input.research, input.reviewed, scope, evidence());
    assert.equal(result.ok, false);
    assert.equal(result.stage, 'target');
    assert.ok(result.errors.some(error => error.path === `/${field}`));
  });
}

test('rejects duplicate canonical property IDs', () => {
  const input = inputs();
  input.reviewed.properties[1].data.id = input.reviewed.properties[0].data.id;
  refuses(input, 'composed', '/properties/1/id');
});

test('refuses an ownership default pointing outside the reviewed property set', () => {
  const input = inputs();
  input.research.ownership.default_property = 'another-property';
  refuses(input, 'composed', '/ownership/default_property');
});

for (const [name, mutate, stage, path] of [
  ['missing research', i => { i.research = null; }, 'research', '/'],
  ['missing reviewed input', i => { i.reviewed = null; }, 'reviewed', '/'],
  ['non-record overlay', i => { i.reviewed.market = fixture.market; }, 'reviewed', '/market'],
  ['empty property set', i => { i.reviewed.properties = []; }, 'composed', '/properties'],
  ['missing envelope identity', i => { delete i.reviewed.properties[0].client_id; }, 'reviewed', '/properties/0/client_id'],
  ['missing envelope data', i => { delete i.reviewed.client.data; }, 'reviewed', '/client/data'],
  ['unknown envelope field', i => { i.reviewed.client.internal_notes = 'private'; }, 'reviewed', '/client/internal_notes'],
  ['private property field', i => { i.reviewed.properties[0].data.internal_notes = 'private'; }, 'composed', '/properties/0/internal_notes'],
  ['stored financials', i => { i.reviewed.properties[0].data.deal_financials = {}; }, 'composed', '/properties/0/deal_financials'],
  ['client uploads', i => { i.reviewed.client.data.client_uploads = []; }, 'composed', '/client/client_uploads'],
  ['private GeoJSON metadata', i => { i.research.developments[0].overlay.features[0].properties.internal_notes = 'private'; }, 'research', '/developments/0/overlay/features/0/properties/internal_notes'],
  ['invalid full data', i => { i.reviewed.prepared.data.on = 'invalid'; }, 'composed', '/prepared/on'],
]) {
  test(`returns deterministic errors for ${name}`, () => {
    const input = inputs();
    mutate(input);
    const before = structuredClone(input);
    refuses(input, stage, path);
    assert.deepEqual(input, before);
  });
}

test('composition is repeatable and independent across ten times the fixture property count', () => {
  const input = inputs();
  input.reviewed.properties = Array.from({ length: 20 }, (_, index) => {
    const row = wrap(fixture.properties[index % 2]);
    row.data.id = index === 0 ? fixture.properties[0].id : `synthetic-property-${index}`;
    row.data.number = index + 1;
    return row;
  });
  assert.equal(compose(input).ok, true);
  assert.deepEqual(compose(input), compose(input));
  assert.deepEqual(compose(inputs()).package, fixture);
});

for (const field of ['partner_only', 'partner_notes', 'financials', 'uploads']) {
  test(`drops unapproved GeoJSON metadata ${field}`, () => {
    const input = inputs();
    input.research.developments[0].overlay.features[0].properties[field] = { synthetic_private: 'private deal material' };
    const before = structuredClone(input);
    const result = compose(input);
    assert.equal(result.ok, true);
    assert.deepEqual(result.package.developments[0].overlay, fixture.developments[0].overlay);
    assert.deepEqual(input, before);
  });
}
for (const [label, path, mutate] of [
  ['report', '/demographics/micro_zones/zones/0/study_pdf', i => { i.research.demographics.micro_zones.zones[0].study_pdf = foreignAsset; }],
  ['document', '/properties/0/floorplan', i => { i.reviewed.properties[0].data.floorplan = foreignAsset; }],
]) {
  test(`refuses another deal's ${label}`, () => {
    const input = inputs();
    input.membership = evidence();
    input.membership.assets.push({ deal_id: 'deal-b', client_id: 'client-b', data: foreignAsset });
    mutate(input);
    const result = compose(input);
    assert.equal(result.ok, false);
    assert.ok(result.errors.some(e => e.path === path && e.reason === 'asset_identity_mismatch'), JSON.stringify(result));
    assert.equal(Object.hasOwn(result, 'package'), false);
  });
}
const foreignAsset = { sha256: 'b'.repeat(64), name: 'deal-b-report.pdf', type: 'application/pdf' };
