import { readMigration, migrationNames as listMigrationNames } from "./migration-source.testhelper.mjs";
import test from 'node:test';



const migration=readMigration(new URL('../../migrations/0814_lease_radar_read.sql',import.meta.url));
test('lease migration remains in the logical migration history', () => {
  assert.equal(listMigrationNames().filter(name => name === '0814_lease_radar_read.sql').length, 1);
});
import assert from 'node:assert/strict';
import { readLeaseRadar, parseBusinessApiPath, LEASE_RADAR_SQL } from '../src/workspace-business-read.js';
const actor = { slug: 'joe' };
const clock = () => new Date('2026-10-01T18:00:00Z');
const row = { id: 'demo-lease', client_id: 'demo-client', client_name: 'Demo Practice', expiration_on: null };
const args = (head = { leases: [row], starts_on: '2026-10-01', ends_on: '2028-10-01' }) => ({actor, correlationId: 'demo-request', now: clock, client: { query: async () => ({rows: [head]}) }});
test('lease projection uses existing authenticated HTTP business route only', () => {
  assert.deepEqual(parseBusinessApiPath('/api/v1/business/leases'), {dataset:'leases',id:null});
  assert.equal(parseBusinessApiPath('/api/v1/business/leases/demo'),null);
});
test('complete projection preserves missing dates and empty means verified empty', async () => {
  assert.deepEqual((await readLeaseRadar(args())).leases,[row]);
  const empty = await readLeaseRadar(args({leases:[],starts_on:'2026-10-01',ends_on:'2028-10-01'}));
  assert.deepEqual(empty.leases,[]); assert.equal(empty.observed_at,clock().toISOString());
  assert.equal(empty.schema_version,'lease-radar.v1');
});
test('audience and tenant refusal run before query; missing data never turns into empty', async () => {
  for (const override of [{actor:{slug:'guest'}},{tenant:'other'}]) await assert.rejects(readLeaseRadar({...args(),...override}));
  await assert.rejects(readLeaseRadar(args({})),e=>e.code==='FRESHNESS_UNKNOWN');
  await assert.rejects(readLeaseRadar({...args(),client:{query:async()=>{throw {code:'42501'};}}}),e=>e.code==='DEPENDENCY_NOT_PROVISIONED');
});
test('query includes full calendar horizon, every client status, null gaps, current lease versions and existing touch/notice facts', () => {
  assert.match(migration,/interval '24 months'/);
  assert.doesNotMatch(LEASE_RADAR_SQL,/interval|America\/Chicago|now\(/);
  assert.match(migration,/expiration_on is null/);
  assert.match(migration,/l.status <> 'superseded'/);
  assert.match(migration,/c.merged_into is null/);
  assert.match(migration,/p.deleted_at is null/);
  assert.match(migration,/c.status='past_client'/);
  assert.match(LEASE_RADAR_SQL,/public.v_client_lease_radar/);
  assert.doesNotMatch(LEASE_RADAR_SQL,/public.lease\b/);
  assert.match(migration,/n.status='open'/);
  assert.match(migration,/cd.kind='option_window'/);
  assert.doesNotMatch(LEASE_RADAR_SQL,/\b(insert|update|delete)\b/i);
});
