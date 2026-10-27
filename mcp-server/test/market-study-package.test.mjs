// carr.market-study.v1: the synthetic fixture validates, partial mode waives only
// the record-layer groups, and every refusal names the field path.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateMarketStudyPackage, RECORD_LAYER_GROUPS } from '../src/market-study-package.js';

const fixture = JSON.parse(readFileSync(new URL('../../client-portal/fixtures/example-health.market-study.json', import.meta.url), 'utf8'));
const copy = () => structuredClone(fixture);

function researchPackage() {
  const pkg = copy();
  for (const group of RECORD_LAYER_GROUPS) delete pkg[group];
  return pkg;
}

function paths(result) {
  return result.errors.map(error => error.path);
}

test('the synthetic fixture validates in full mode', () => {
  const result = validateMarketStudyPackage(fixture);
  assert.deepEqual(result.errors, []);
  assert.equal(result.ok, true);
});

test('the synthetic fixture turns on every optional module', () => {
  assert.ok(fixture.client.logo && fixture.client.website && fixture.client.current_practice);
  assert.ok(fixture.map.home_extent_points);
  assert.ok(fixture.properties.some(p => p.role === 'market-context'));
  assert.ok(fixture.properties.some(p => p.ownership && p.notes && p.floorplan && p.locator?.preferred_offset && p.review_point));
  assert.ok(fixture.purchases.table && fixture.purchases.market_context_note);
  assert.ok(fixture.developments.some(d => d.overlay && d.locator && d.copy));
  assert.ok(fixture.context_anchors?.length);
  assert.ok(fixture.demographics.micro_zones?.zones.length);
  assert.ok(fixture.demographics.competition?.offices.length);
  assert.ok(fixture.demographics.income_bands.combined);
  assert.ok(fixture.ownership);
  const basemaps = new Set([fixture.map.basemap.kind]);
  const other = copy();
  other.map.basemap = { kind: 'vector-style', style_url: 'https://tiles.example.com/style.json',
    attribution_link: { label: 'Example Tiles', url: 'https://tiles.example.com/' } };
  assert.equal(validateMarketStudyPackage(other).ok, true, 'the other basemap kind validates');
  basemaps.add(other.map.basemap.kind);
  assert.deepEqual([...basemaps].sort(), ['local-layers', 'vector-style']);
});

test('record-layer groups are exactly client, prepared and properties', () => {
  assert.deepEqual([...RECORD_LAYER_GROUPS].sort(), ['client', 'prepared', 'properties']);
});

test('a research package passes in partial mode and fails in full mode', () => {
  const pkg = researchPackage();
  assert.deepEqual(validateMarketStudyPackage(pkg, { mode: 'partial' }).errors, []);
  const full = validateMarketStudyPackage(pkg, { mode: 'full' });
  assert.equal(full.ok, false);
  for (const group of RECORD_LAYER_GROUPS) assert.ok(paths(full).includes(`/${group}`), `full mode names /${group}`);
});

test('partial mode still requires every non-record-layer group', () => {
  const pkg = researchPackage();
  delete pkg.demographics;
  const result = validateMarketStudyPackage(pkg, { mode: 'partial' });
  assert.equal(result.ok, false);
  assert.deepEqual(paths(result), ['/demographics']);
});

test('partial mode still checks a record-layer group that is present', () => {
  const pkg = researchPackage();
  pkg.client = { display_name: '<b>Example</b>', wordmark: { line1: 'EXAMPLE', line2: 'HEALTH GROUP' } };
  const result = validateMarketStudyPackage(pkg, { mode: 'partial' });
  assert.ok(paths(result).includes('/client/display_name'));
});

test('an unknown mode is a programming error', () => {
  assert.throws(() => validateMarketStudyPackage(fixture, { mode: 'loose' }), /mode/);
});

// Each named invalid fixture is the valid fixture with one defect, and the
// refusal must name the field path of that defect.
const INVALID = [
  ['missing required group', pkg => { delete pkg.market; }, '/market'],
  ['raw HTML in rich text', pkg => { pkg.sources.sections[0].body[0].text = 'See <a href="https://example.com">here</a>'; }, '/sources/sections/0/body/0/text'],
  ['raw HTML in a plain text field', pkg => { pkg.properties[0].summary = '<script>alert(1)</script>'; }, '/properties/0/summary'],
  ['non-https URL', pkg => { pkg.properties[0].sources[0].url = 'http://example.com/listing'; }, '/properties/0/sources/0/url'],
  ['unknown property role', pkg => { pkg.properties[0].role = 'favorite'; }, '/properties/0/role'],
  ['bad asset hash', pkg => { pkg.properties[0].photo.sha256 = 'not-a-hash'; }, '/properties/0/photo/sha256'],
  ['slug carried in the package', pkg => { pkg.slug = 'example-health'; }, '/slug'],
  ['wrong schema version', pkg => { pkg.schema = 'carr.market-study.v2'; }, '/schema'],
  ['snapshot not three figures', pkg => { pkg.market.snapshot.pop(); }, '/market/snapshot'],
  ['latitude out of range', pkg => { pkg.map.center[1] = 123.4; }, '/map/center/1'],
  ['unknown basemap kind', pkg => { pkg.map.basemap.kind = 'raster'; }, '/map/basemap/kind'],
  ['micro-zone without a radius', pkg => { delete pkg.demographics.micro_zones.zones[0].geography.radius_miles; }, '/demographics/micro_zones/zones/0/geography/radius_miles'],
  ['unknown field inside a group', pkg => { pkg.market.slug = 'example-health'; }, '/market/slug'],
];

for (const [name, mutate, path] of INVALID) {
  test(`invalid fixture refused with its field path: ${name}`, () => {
    const pkg = copy();
    mutate(pkg);
    const result = validateMarketStudyPackage(pkg);
    assert.equal(result.ok, false);
    assert.ok(paths(result).includes(path), `expected ${path} in ${JSON.stringify(paths(result))}`);
  });
}

test('a research package carrying a slug is refused in partial mode too', () => {
  const pkg = researchPackage();
  pkg.slug = 'example-health';
  assert.deepEqual(paths(validateMarketStudyPackage(pkg, { mode: 'partial' })), ['/slug']);
});
