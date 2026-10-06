import test from 'node:test';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const migration=readFileSync(new URL('../../migrations/0814_lease_radar_read.sql',import.meta.url),'utf8');
test('lease migration loads through the repository runner with a unique number and runner-owned transaction', () => {
  execFileSync('python3', ['-c', "import sys,runpy;sys.path.insert(0,'tools');runner=runpy.run_path('tools/migrate.py');migrations=runner['load_migrations']();assert any(name == sys.argv[1] for name,sql,digest in migrations), 'lease migration missing from runner'", '0814_lease_radar_read.sql'], {cwd:fileURLToPath(new URL('../../',import.meta.url))});
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
