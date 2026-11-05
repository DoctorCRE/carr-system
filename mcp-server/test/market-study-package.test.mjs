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

const identityOverlay = JSON.parse(readFileSync(new URL('../../client-portal/fixtures/same-name-geographies.v1.1.json', import.meta.url), 'utf8'));
function identityPackage() {
  const pkg = copy();
  pkg.schema = identityOverlay.schema;
  Object.assign(pkg.demographics, structuredClone(identityOverlay.demographics));
  return pkg;
}
const coreRows = pkg => [pkg.demographics.growth.geographies, pkg.demographics.age.geographies,
  pkg.demographics.median_income.geographies, pkg.demographics.income_bands.areas];

test('old schema refuses source-assigned identities rather than reinterpreting v1', () => {
  const pkg = identityPackage();
  pkg.schema = 'carr.market-study.v1';
  assert.equal(validateMarketStudyPackage(pkg).ok, false);
});

test('v1.1 accepts same names and independent row order in full and partial mode', () => {
  const pkg = identityPackage();
  assert.deepEqual(validateMarketStudyPackage(pkg), { ok: true, errors: [] });
  for (const group of RECORD_LAYER_GROUPS) delete pkg[group];
  assert.deepEqual(validateMarketStudyPackage(pkg, { mode: 'partial' }), { ok: true, errors: [] });
});

for (let section = 0; section < 4; section++) {
  for (const [name, mutate, field] of [
    ['missing', rows => { delete rows[0].geography_id; }, 'geography_id'],
    ['empty', rows => { rows[0].geography_id = ''; }, 'geography_id'],
    ['duplicate', rows => { rows[1].geography_id = rows[0].geography_id; }, 'geography_id'],
    ['missing label', rows => { delete rows[0].geography_label; }, 'geography_label'],
  ]) {
    test(`v1.1 refuses ${name} identity in section ${section}`, () => {
      const pkg = identityPackage();
      mutate(coreRows(pkg)[section]);
      const result = validateMarketStudyPackage(pkg);
      assert.equal(result.ok, false);
      assert.ok(paths(result).some(path => path.endsWith(`/${field}`)), JSON.stringify(result));
    });
  }
}

for (let section = 1; section < 4; section++) {
  test(`v1.1 refuses unknown, omitted and mislabeled references in section ${section}`, () => {
    for (const mutate of [rows => { rows[0].geography_id = 'src:unknown'; }, rows => rows.pop(),
      rows => { rows[0].geography_label = 'Wrong Springfield'; }]) {
      const pkg = identityPackage();
      mutate(coreRows(pkg)[section]);
      assert.equal(validateMarketStudyPackage(pkg).ok, false);
    }
  });
}

test('v1.1 refuses indistinguishable labels for distinct identities', () => {
  const pkg = identityPackage();
  for (const rows of coreRows(pkg)) for (const row of rows) row.geography_label = 'Springfield';
  assert.equal(validateMarketStudyPackage(pkg).ok, false);
});

test('v1.1 preserves IDs, values and sources through serialization and replacement', () => {
  const pkg = identityPackage();
  const before = structuredClone(pkg);
  assert.equal(validateMarketStudyPackage(pkg).ok, true);
  assert.deepEqual(pkg, before);
  const wire = JSON.parse(JSON.stringify(pkg));
  assert.deepEqual(wire, pkg);
  for (const rows of coreRows(wire)) rows.reverse();
  assert.equal(validateMarketStudyPackage(wire).ok, true);
  const population = new Map(wire.demographics.growth.geographies.map(row => [row.geography_id, row]));
  const ages = new Map(wire.demographics.age.geographies.map(row => [row.geography_id, row]));
  const incomes = new Map(wire.demographics.median_income.geographies.map(row => [row.geography_id, row.value]));
  assert.equal(population.get('src:9f32a').series[0].value, 10000);
  assert.equal(population.get('src:b817c').series[0].value, 20000);
  assert.equal(ages.get('src:9f32a').under_18_pct, 20);
  assert.equal(ages.get('src:b817c').under_18_pct, 30);
  assert.equal(incomes.get('src:9f32a'), 65000);
  assert.equal(incomes.get('src:b817c'), 95000);
  assert.equal(ages.get('src:9f32a').source_url, 'https://example.com/illinois/age');
  for (const rows of coreRows(wire)) for (const row of rows) row.geography_id = `replacement:${row.geography_id}`;
  assert.equal(validateMarketStudyPackage(wire).ok, true);
  assert.equal(validateMarketStudyPackage(pkg).ok, true);
});

test('v1.1 retains optional micro-zone behavior and rejects unsupported versions', () => {
  const pkg = identityPackage();
  delete pkg.demographics.micro_zones;
  assert.equal(validateMarketStudyPackage(pkg).ok, true);
  pkg.schema = 'carr.market-study.v99';
  assert.equal(validateMarketStudyPackage(pkg).ok, false);
});

test('v1.1 refuses blank labels and malformed source IDs without throwing', () => {
  for (const value of [' ', 'a'.repeat(129), '<id>', null, 9]) {
    const pkg = identityPackage();
    pkg.demographics.growth.geographies[0].geography_id = value;
    assert.equal(validateMarketStudyPackage(pkg).ok, false);
  }
  const pkg = identityPackage();
  for (const rows of coreRows(pkg)) rows[0].geography_label = '   ';
  assert.equal(validateMarketStudyPackage(pkg).ok, false);
  pkg.demographics = null;
  assert.equal(validateMarketStudyPackage(pkg).ok, false);
});

test('v1.1 validates ten times the fixture rows with isolated identities', () => {
  const pkg = identityPackage();
  for (const rows of coreRows(pkg)) {
    const original = structuredClone(rows);
    rows.length = 0;
    for (let batch = 0; batch < 10; batch++) {
      rows.push(...original.map(row => ({ ...row, geography_id: `${row.geography_id}:${batch}`,
        geography_label: `${row.geography_label} study ${batch}` })));
    }
  }
  assert.equal(validateMarketStudyPackage(pkg).ok, true);
  assert.equal(validateMarketStudyPackage(null).ok, false);
});
