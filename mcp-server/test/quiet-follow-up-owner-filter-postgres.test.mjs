import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';
import { SCAC_MUTATION_OPERATIONS } from '../src/scac-mutation-registry.current.generated.js';

const registrySource = readFileSync(new URL('../src/scac-mutation-registry.current.generated.js', import.meta.url), 'utf8');
function registryPin(registry = SCAC_MUTATION_OPERATIONS) {
  return createHash('sha256').update(JSON.stringify(registry['quiet-follow-ups'])).digest('hex');
}
function recordingRegistryMatches(registry) {
  const recording = JSON.parse(readFileSync(new URL('../../tools/fixtures/quiet-follow-up-owner-filter.json', import.meta.url)));
  assert.equal(recording.provenance.registry_entry_sha256, registryPin(registry));
}
async function registryCopy(source) {
  const module = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  return module.SCAC_MUTATION_OPERATIONS;
}

test('recording accepts an unrelated registry entry', async () => {
  const copy = registrySource.replace('Object.freeze({', 'Object.freeze({"synthetic-unrelated-verb": {"write": false},');
  assert.notEqual(copy, registrySource);
  recordingRegistryMatches(await registryCopy(copy));
  recordingRegistryMatches({ ...SCAC_MUTATION_OPERATIONS,
    'accept-outcome-feedback': { ...SCAC_MUTATION_OPERATIONS['accept-outcome-feedback'], write: false } });
});

test('recording rejects a quiet-follow-ups contract change', async () => {
  const entry = SCAC_MUTATION_OPERATIONS['quiet-follow-ups'];
  for (const key of Object.keys(entry)) {
    const registry = { ...SCAC_MUTATION_OPERATIONS,
      'quiet-follow-ups': { ...entry, [key]: key === 'delegates_to' ? ['synthetic-verb'] : !entry[key] } };
    assert.throws(() => recordingRegistryMatches(registry), { code: 'ERR_ASSERTION' });
  }
  const copy = registrySource.replace('"ingress_key": "mcp-tool:quiet-follow-ups"',
    '"ingress_key": "mcp-tool:quiet-follow-ups-changed"');
  assert.notEqual(copy, registrySource);
  const changed = await registryCopy(copy);
  assert.throws(() => recordingRegistryMatches(changed), { code: 'ERR_ASSERTION' });
});

let fixture, c, actor, other;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'quiet-owner-', snapshot: true });
  c = new pg.Client(fixture.connection);
  await c.connect();
  actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  other = (await c.query("select id,slug from actor where slug='dell'")).rows[0];
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });
async function read(args = {}) {
  await c.query('begin read only');
  try {
    await c.query('set local role carr_writer');
    const value = await executeRegisteredTool(c, actor, 'quiet-follow-ups', args);
    await c.query('commit');
    return JSON.parse(JSON.stringify(value));
  } catch (error) { await c.query('rollback'); throw error; }
}
async function subject(kind, owner, days = 200) {
  const party = randomUUID(), id = randomUUID();
  await c.query(`insert into party(id,kind,name,created_by,updated_by)
    values($1::uuid,'org','Synthetic quiet owner '||$1::uuid::text,$2,$2)`, [party, actor.id]);
  if (kind === 'lead') await c.query(`insert into lead(id,party_id,stage,last_touch,owner_id,created_by,updated_by)
    values($1,$2,'new',current_date-$4::int,$3,$5,$5)`, [id,party,owner,days,actor.id]);
  else await c.query(`insert into vendor(id,party_id,category,stage,disposition,last_touch,owner_id,created_by,updated_by)
    values($1,$2,'broker',(select slug from vendor_stage order by sort limit 1),'active',current_date-$4::int,$3,$5,$5)`,
    [id,party,owner,days,actor.id]);
  return { id, party };
}

test('owner modes preserve exact ordered subsets and registered response recordings', async () => {
  for (const kind of ['lead','vendor']) for (const owner of [actor.id, other.id, null]) await subject(kind, owner);
  const all = await read();
  const owned = await read({ owner_slug: actor.slug });
  const unassigned = await read({ owner_slug: null });
  assert.deepEqual(owned.proposals, all.proposals.filter(row => row.owner === actor.slug));
  assert.deepEqual(unassigned.proposals, all.proposals.filter(row => row.owner_id === null));
  assert.ok(owned.proposals.some(row => row.subject_type === 'lead'));
  assert.ok(owned.proposals.some(row => row.subject_type === 'vendor'));
  for (const value of [all, owned, unassigned]) {
    assert.equal(value.schema_version, 'quiet-follow-ups.v1');
    assert.deepEqual(value.cadence, { lead_days: 45, vendor_days: 90 });
    assert.equal(value.sent, false); assert.equal(value.tasks_created, false);
  }
  assert.deepEqual((await read({ owner_slug: 'unknown-owner' })).proposals, []);
  assert.deepEqual((await read({ owner_slug: "joe' OR true --" })).proposals, []);
  assert.deepEqual((await read({ owner_slug: 'JOE' })).proposals, []);
  const custom = await read({ owner_slug: actor.slug, lead_days: 30, vendor_days: 60 });
  assert.deepEqual(custom.cadence, { lead_days: 30, vendor_days: 60 });
  if (process.env.QUIET_OWNER_FIXTURE_OUTPUT) {
    const source = readFileSync(new URL('../src/lead-follow-up.ts', import.meta.url));
    writeFileSync(process.env.QUIET_OWNER_FIXTURE_OUTPUT, JSON.stringify({
      provenance: { transport: 'executeRegisteredTool/disposable-postgres',
        producer_revision: execFileSync('git', ['rev-parse','HEAD'], { encoding: 'utf8' }).trim(),
        producer_sha256: createHash('sha256').update(source).digest('hex'),
        registry_entry_sha256: registryPin(),
        query: { verb: 'quiet-follow-ups', all: {}, owned: { owner_slug: actor.slug }, unassigned: { owner_slug: null } } },
      owner_slug: actor.slug, all, owned, unassigned,
    }, null, 2) + '\n');
  }
});

test('serialized quiet proposals preserve calendar dates through PostgreSQL decoding', async () => {
  const subjects = [];
  for (const kind of ['lead', 'vendor']) {
    const row = await subject(kind, actor.id);
    subjects.push(row);
    await c.query(`update ${kind} set last_touch='2000-02-29',created_at='2000-01-02T00:30:00Z' where id=$1`, [row.id]);
  }
  const check = async (anchor, due) => {
    const payload = await read({ owner_slug: actor.slug });
    for (const { id } of subjects) {
      const row = payload.proposals.find(p => p.subject_id === id);
      assert.equal(row.anchor_on, anchor);
      assert.equal(row.due_on, due[row.subject_type]);
    }
  };
  await check('2000-02-29', { lead: '2000-04-14', vendor: '2000-05-29' });
  for (const [i, kind] of ['lead', 'vendor'].entries()) {
    await c.query(`update ${kind} set last_touch=null where id=$1`, [subjects[i].id]);
  }
  await check('2000-01-01', { lead: '2000-02-15', vendor: '2000-03-31' });
});

test('invalid owner input refuses before database work', async () => {
  for (const owner_slug of ['', '   ', '\n\t', 'x'.repeat(101), 2, [], {}]) {
    const client = { query() { assert.fail('invalid owner reached database'); } };
    await assert.rejects(() => executeRegisteredTool(client, actor, 'quiet-follow-ups', { owner_slug }),
      e => e.payload?.error === 'invalid_owner_slug');
  }
  assert.deepEqual(TOOLS['quiet-follow-ups'].inputSchema.properties.owner_slug.type, ['string','null']);
});

test('owner filtering preserves eligibility and next action override without writes', async () => {
  const row = await subject('lead', actor.id, 1);
  await c.query('update lead set next_action_date=current_date-2 where id=$1', [row.id]);
  const has = async () => (await read({ owner_slug: actor.slug })).proposals.some(p => p.subject_id === row.id);
  assert.equal(await has(), true);
  await c.query('update lead set next_action_date=current_date+2 where id=$1', [row.id]);
  assert.equal(await has(), false);
  await c.query('update lead set next_action_date=current_date-2,suppressed=true where id=$1', [row.id]);
  assert.equal(await has(), false);
  await c.query("update lead set suppressed=false,stage='closed_lost' where id=$1", [row.id]);
  assert.equal(await has(), false);
  await c.query("update lead set stage='new' where id=$1", [row.id]);
  await c.query("update party set contact_state='do_not_contact' where id=$1", [row.party]);
  assert.equal(await has(), false);
  await c.query("update party set contact_state='active' where id=$1", [row.party]);
  await c.query(`insert into next_action(subject_type,subject_id,owner_id,due_on,description,created_by,updated_by)
    values('lead',$1,$2,current_date+30,'Synthetic future open task',$2,$2)`, [row.id,actor.id]);
  assert.equal(await has(), false);
  const vendor = await subject('vendor', actor.id);
  await c.query("update vendor set disposition=(select slug from vendor_disposition where slug<>'active' order by slug limit 1) where id=$1", [vendor.id]);
  assert.ok(!(await read({ owner_slug: actor.slug })).proposals.some(p => p.subject_id === vendor.id));
});

test('missing actor joins retain assigned rows only in all-owner mode', async () => {
  await c.query('create temp view actor as select id,slug from public.actor where false');
  await c.query('begin read only');
  try {
    const all = await executeRegisteredTool(c, actor, 'quiet-follow-ups', {});
    assert.ok(all.proposals.some(p => p.owner_id !== null && p.owner === null));
    const owned = await executeRegisteredTool(c, actor, 'quiet-follow-ups', { owner_slug: actor.slug });
    assert.deepEqual(owned.proposals, []);
    const unassigned = await executeRegisteredTool(c, actor, 'quiet-follow-ups', { owner_slug: null });
    assert.deepEqual(unassigned.proposals, all.proposals.filter(p => p.owner_id === null));
  } finally {
    await c.query('rollback');
    await c.query('drop view pg_temp.actor');
  }
});

test('recorded responses replay through production proof response contract', () => {
  const recording = JSON.parse(readFileSync(new URL('../../tools/fixtures/quiet-follow-up-owner-filter.json', import.meta.url)));
  assert.equal(recording.provenance.producer_sha256, createHash('sha256').update(
    readFileSync(new URL('../src/lead-follow-up.ts', import.meta.url))).digest('hex'));
  recordingRegistryMatches(SCAC_MUTATION_OPERATIONS);
  const python = process.env.CARR_CI_PYTHON || 'python3';
  const output = execFileSync(python, ['tools/prove-quiet-follow-up-owner-filter.py', '--fixture',
    'tools/fixtures/quiet-follow-up-owner-filter.json'], { encoding: 'utf8', timeout: 10000, cwd: new URL('../..', import.meta.url) });
  assert.equal(JSON.parse(output).status, 'passed');
});

test('owner predicate precedes selected subset overflow and retains missing joins', async () => {
  await c.query(`insert into party(id,kind,name,created_by,updated_by)
    select gen_random_uuid(),'org','Synthetic overflow '||g,$1,$1 from generate_series(1,1001) g`, [actor.id]);
  await c.query(`insert into lead(party_id,stage,last_touch,owner_id,created_by,updated_by)
    select id,'new',current_date-200,$1,$2,$2 from party where name like 'Synthetic overflow %'`, [other.id,actor.id]);
  await assert.rejects(() => read(), e => e.payload.error === 'quiet_follow_up_limit_exceeded');
  await assert.rejects(() => read({ owner_slug: other.slug }), e => e.payload.error === 'quiet_follow_up_limit_exceeded');
  assert.ok((await read({ owner_slug: actor.slug })).proposals.length > 0);
  assert.ok((await read({ owner_slug: null })).proposals.every(p => p.owner_id === null));
});
