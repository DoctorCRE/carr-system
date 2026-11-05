import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { createMarketStudyPreparation, packageDigest } from '../src/market-study-preparation.js';

const fixture = JSON.parse(readFileSync(new URL('../../client-portal/fixtures/example-health.market-study.json', import.meta.url)));
const actor = { id: 'synthetic-actor' };
const args = { deal_id: 'deal-a', research_package_id: 'package-a', research_version: 1, renderer_version: 'carr.market-study.v1' };
const target = { deal_id: args.deal_id, client_id: 'client-a' };
const envelope = data => ({ ...target, data: structuredClone(data) });
function assets(value) {
  if (!value || typeof value !== 'object') return [];
  if (value.sha256 && value.name && value.type) return [envelope(value)];
  return Object.values(value).flatMap(assets);
}
async function setup(count = 2) {
  const research = structuredClone(fixture);
  for (const group of ['client', 'prepared', 'properties']) delete research[group];
  const snapshot = {
    ...target,
    research: { ...target, id: 'package-a', version: 1, sha256: await packageDigest(research), data: research },
    reviewed: { client: envelope(fixture.client), prepared: envelope(fixture.prepared), properties: Array.from({ length: count }, (_, i) => {
      const row = envelope(fixture.properties[i % 2]);
      if (i > 1) row.data.id = `property-${i}`;
      return row;
    }) },
    assets: assets(fixture),
    revisions: { deal: 1, client: 1, property_membership: 1, reviewed_client: 1, reviewed_prepared: 1,
      reviewed_properties: 1, asset_membership: 1, publication: 0 },
  };
  const calls = { authorize: 0, snapshot: 0, catalog: 0 };
  let now = 100000;
  const key = await webcrypto.subtle.generateKey({ name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
  const deps = {
    signingKey: key, clock: () => now,
    authorize: async (_actor, deal) => { calls.authorize++; assert.equal(deal, args.deal_id); if (_actor.id !== actor.id) throw Error('denied'); return 'partner:synthetic'; },
    readSnapshot: async () => { calls.snapshot++; return structuredClone(snapshot); },
    listPackages: async (_actor, selector) => { calls.catalog++; return Array.from({ length: selector.limit }, (_, i) => ({ id: `p-${i}`, version: 1, sha256: 'a'.repeat(64), title: `Study ${i}` })); },
  };
  return { service: createMarketStudyPreparation(deps), snapshot, calls, deps, advance: () => { now += 300001; } };
}
const rejects = (fn, code) => assert.rejects(fn, e => e.payload?.error === code);

test('rejects foreign package and foreign assets before issuing a preview', async () => {
  const f = await setup();
  f.snapshot.research.deal_id = 'foreign';
  await rejects(() => f.service.prepare(actor, args), 'research_identity_mismatch');
  f.snapshot.research.deal_id = args.deal_id;
  f.snapshot.assets[0].deal_id = 'foreign';
  await rejects(() => f.service.prepare(actor, args), 'composition_invalid');
});
test('rejects invalid research and unsupported renderer', async () => {
  const f = await setup();
  f.snapshot.research.data.market = {};
  f.snapshot.research.sha256 = await packageDigest(f.snapshot.research.data);
  await rejects(() => f.service.prepare(actor, args), 'composition_invalid');
  await rejects(() => f.service.prepare(actor, { ...args, renderer_version: 'unknown' }), 'unsupported_renderer');
});
test('unauthorized actor never reads catalog or snapshot', async () => {
  const f = await setup();
  await assert.rejects(f.service.prepare({ id: 'foreign' }, args), /denied/);
  await assert.rejects(f.service.list({ id: 'foreign' }, { deal_id: args.deal_id }), /denied/);
  assert.equal(f.calls.snapshot + f.calls.catalog, 0);
});
test('rejects expired, tampered and actor-substituted tokens', async () => {
  const f = await setup();
  const result = await f.service.prepare(actor, args);
  await rejects(() => f.service.verify(actor, args, result.preview_token + 'x'), 'preview_invalid');
  const [payload, signature] = result.preview_token.split('.');
  const altered = JSON.parse(Buffer.from(payload, 'base64url'));
  altered.actor_id = 'foreign';
  await rejects(() => f.service.verify(actor, args, Buffer.from(JSON.stringify(altered)).toString('base64url') + '.' + signature), 'preview_invalid');
  const other = createMarketStudyPreparation({ ...f.deps, authorize: async () => 'partner:synthetic' });
  await rejects(() => other.verify({ id: 'foreign' }, args, result.preview_token), 'preview_stale');
  f.advance();
  await rejects(() => f.service.verify(actor, args, result.preview_token), 'preview_expired');
});

for (const count of [2, 20]) {
  test(`prepares and verifies ${count} properties without publication effects`, async () => {
    const f = await setup(count);
    const before = structuredClone(f.snapshot);
    const result = await f.service.prepare(actor, args);
    assert.equal(result.package.properties.length, count);
    assert.equal(result.binding.package_sha256, await packageDigest(result.package));
    assert.equal(result.binding.research_sha256, await packageDigest(f.snapshot.research.data));
    assert.equal(result.binding.overlay_sha256, await packageDigest(f.snapshot.reviewed));
    assert.equal(result.binding.asset_membership_sha256, await packageDigest(f.snapshot.assets));
    assert.deepEqual((await f.service.verify(actor, args, result.preview_token)).package, result.package);
    assert.deepEqual(f.snapshot, before);
    assert.deepEqual(f.calls, { authorize: 2, snapshot: 2, catalog: 0 });
    assert.equal(f.snapshot.revisions.publication, 0);
  });
}
for (const revision of ['deal', 'client', 'property_membership', 'reviewed_client', 'reviewed_prepared', 'reviewed_properties', 'asset_membership', 'publication', 'additional_input']) {
  test(`changing canonical revision ${revision} invalidates the candidate`, async () => {
    const f = await setup();
    const result = await f.service.prepare(actor, args);
    f.snapshot.revisions[revision] = 2;
    await rejects(() => f.service.verify(actor, args, result.preview_token), 'preview_stale');
  });
}
for (const [name, change] of [
  ['client', s => { s.reviewed.client.data.display_name = 'New canonical name'; }],
  ['prepared', s => { s.reviewed.prepared.data.for = 'New canonical contact'; }],
  ['property', s => { s.reviewed.properties[0].data.number = 42; }],
  ['membership', s => { s.assets.push(envelope({ sha256: 'c'.repeat(64), name: 'new.pdf', type: 'application/pdf' })); }],
]) {
  test(`changing consumed ${name} bytes invalidates binding even with unchanged revisions`, async () => {
    const f = await setup();
    const result = await f.service.prepare(actor, args);
    change(f.snapshot);
    await rejects(() => f.service.verify(actor, args, result.preview_token), 'preview_stale');
  });
}
test('changing research bytes and digest invalidates the candidate', async () => {
  const f = await setup();
  const result = await f.service.prepare(actor, args);
  f.snapshot.research.data.market.name = 'New canonical research';
  f.snapshot.research.sha256 = await packageDigest(f.snapshot.research.data);
  await rejects(() => f.service.verify(actor, args, result.preview_token), 'preview_stale');
});
test('changing authorization scope invalidates the candidate', async () => {
  const f = await setup();
  const result = await f.service.prepare(actor, args);
  const other = createMarketStudyPreparation({ ...f.deps, authorize: async () => 'partner:changed' });
  await rejects(() => other.verify(actor, args, result.preview_token), 'preview_stale');
});
test('missing signing binding and short keys fail closed', async () => {
  const f = await setup();
  for (const signingKey of [undefined, await webcrypto.subtle.generateKey({ name: 'HMAC', hash: 'SHA-256', length: 128 }, false, ['sign', 'verify'])]) {
    await rejects(() => createMarketStudyPreparation({ ...f.deps, signingKey }).prepare(actor, args), 'signing_unavailable');
  }
  assert.equal(f.calls.snapshot, 0);
});
test('caller cannot supply membership, reviewed data or revisions', async () => {
  const f = await setup();
  for (const field of ['membership', 'reviewed', 'revisions', 'actor_id', 'client_id']) {
    await rejects(() => f.service.prepare(actor, { ...args, [field]: {} }), 'invalid_selector');
  }
  assert.equal(f.calls.authorize, 0);
});
test('catalog pages are bounded and contain only immutable identities', async () => {
  const f = await setup();
  const result = await f.service.list(actor, { deal_id: args.deal_id, limit: 2 });
  assert.equal(result.packages.length, 2);
  assert.deepEqual(result.next, { id: 'p-1', version: 1 });
  await f.service.list(actor, { deal_id: args.deal_id, limit: 2, after: result.next });
  assert.equal(f.calls.catalog, 2);
  for (const limit of [0, -1, 51, 1.5, '2']) {
    await rejects(() => f.service.list(actor, { deal_id: args.deal_id, limit }), 'invalid_selector');
  }
  const tooMany = createMarketStudyPreparation({ ...f.deps, listPackages: async () => Array(52).fill({}) });
  await rejects(() => tooMany.list(actor, { deal_id: args.deal_id }), 'catalog_limit');
});
for (const [name, change, error] of [
  ['research digest', s => { s.research.sha256 = '0'.repeat(64); }, 'research_digest_mismatch'],
  ['missing revision', s => { delete s.revisions.client; }, 'revision_map_invalid'],
  ['too many revisions', s => { for (let i = 0; i < 201; i++) s.revisions['extra-' + i] = 1; }, 'revision_map_invalid'],
  ['too many properties', s => { s.reviewed.properties = Array(21).fill(s.reviewed.properties[0]); }, 'snapshot_limit'],
  ['too many assets', s => { s.assets = Array(201).fill(s.assets[0]); }, 'snapshot_limit'],
  ['oversized bytes', s => { s.research.data.market.name = 'x'.repeat(2097153); }, 'payload_limit'],
  ['deep nesting', s => { let v = s; for (let i = 0; i < 65; i++) { v.nested = {}; v = v.nested; } }, 'payload_limit'],
  ['foreign snapshot', s => { s.deal_id = 'foreign'; }, 'snapshot_unavailable'],
]) {
  test(`refuses ${name}`, async () => {
    const f = await setup(); change(f.snapshot);
    await rejects(() => f.service.prepare(actor, args), error);
  });
}
test('canonical digest is independent of object key insertion order', async () => {
  assert.equal(await packageDigest({ a: 1, b: { c: 2 } }), await packageDigest({ b: { c: 2 }, a: 1 }));
  await rejects(() => packageDigest({ x: Infinity }), 'invalid_json');
});
test('adapter failures do not retry or return partial candidates', async () => {
  const f = await setup();
  let calls = 0;
  const service = createMarketStudyPreparation({ ...f.deps, readSnapshot: async () => { calls++; throw Error('unavailable'); } });
  await assert.rejects(service.prepare(actor, args), /unavailable/);
  assert.equal(calls, 1);
});

test('binding pins exact composer, validator and schema bytes', async () => {
  const f = await setup();
  const { binding } = await f.service.prepare(actor, args);
  for (const [key, path] of [
    ['composer_sha256', '../src/market-study-composer.js'],
    ['validator_sha256', '../src/market-study-package.js'],
    ['package_schema_sha256', '../../client-portal/market-study.v1.schema.json'],
  ]) {
    const bytes = readFileSync(new URL(path, import.meta.url));
    const digest = Buffer.from(await webcrypto.subtle.digest('SHA-256', bytes)).toString('hex');
    assert.equal(binding[key], digest);
  }
});
test('oversized binding is refused instead of issuing an unverifiable token', async () => {
  const f = await setup();
  for (let i = 0; i < 190; i++) f.snapshot.revisions[(i + '-').padEnd(200, 'x')] = 'a'.repeat(64);
  await rejects(() => f.service.prepare(actor, args), 'preview_limit');
});
test('invalid clock refuses preparation and verification', async () => {
  const f = await setup();
  const prepared = await f.service.prepare(actor, args);
  const service = createMarketStudyPreparation({ ...f.deps, clock: () => NaN });
  await rejects(() => service.prepare(actor, args), 'clock_unavailable');
  await rejects(() => service.verify(actor, args, prepared.preview_token), 'preview_invalid');
});
