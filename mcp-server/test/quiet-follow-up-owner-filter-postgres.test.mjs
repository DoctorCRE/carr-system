import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import pg from 'pg';
import { types, neon, neonConfig } from '../src/postgres.ts';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.ts';
import { assertRecordingProducer, recordingProvenance } from './helpers/recording-provenance.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';
import { SCAC_MUTATION_OPERATIONS } from '../src/scac-mutation-registry.current.generated.ts';

function registryPin(registry = SCAC_MUTATION_OPERATIONS) {
  return createHash('sha256').update(JSON.stringify(registry['quiet-follow-ups'])).digest('hex');
}
function recordingRegistryMatches(registry) {
  const recording = JSON.parse(readFileSync(new URL('../../tools/fixtures/quiet-follow-up-owner-filter.json', import.meta.url)));
  assert.equal(recording.provenance.registry_entry_sha256, registryPin(registry));
}
test('recording accepts an unrelated registry entry', () => {
  recordingRegistryMatches({ ...SCAC_MUTATION_OPERATIONS,
    'synthetic-unrelated-verb': { write: false } });
  recordingRegistryMatches({ ...SCAC_MUTATION_OPERATIONS,
    'accept-outcome-feedback': { ...SCAC_MUTATION_OPERATIONS['accept-outcome-feedback'], write: false } });
});

test('recording rejects a quiet-follow-ups contract change', () => {
  const entry = SCAC_MUTATION_OPERATIONS['quiet-follow-ups'];
  for (const key of Object.keys(entry)) {
    const registry = { ...SCAC_MUTATION_OPERATIONS,
      'quiet-follow-ups': { ...entry, [key]: key === 'delegates_to' ? ['synthetic-verb'] : !entry[key] } };
    assert.throws(() => recordingRegistryMatches(registry), { code: 'ERR_ASSERTION' });
  }
  const changed = { ...SCAC_MUTATION_OPERATIONS,
    'quiet-follow-ups': { ...entry, ingress_key: 'mcp-tool:quiet-follow-ups-changed' } };
  assert.throws(() => recordingRegistryMatches(changed), { code: 'ERR_ASSERTION' });
});

let fixture, c, actor, other;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'quiet-owner-', snapshot: true });
  c = new pg.Client({ ...fixture.connection, types });
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
  if (kind === 'lead') await c.query(`insert into lead(id,party_id,registry_ref,stage,last_touch,owner_id,created_by,updated_by)
    values($1,$2,'L-'||$1::uuid::text,'new',carr_business_date()-$4::int,$3,$5,$5)`, [id,party,owner,days,actor.id]);
  else await c.query(`insert into vendor(id,party_id,vendor_ref,category,stage,disposition,last_touch,owner_id,created_by,updated_by)
    values($1,$2,'V-'||$1::uuid::text,'broker',(select slug from vendor_stage order by sort limit 1),'active',carr_business_date()-$4::int,$3,$5,$5)`,
    [id,party,owner,days,actor.id]);
  return { id, party };
}

test('PostgreSQL date columns remain calendar dates while timestamp types keep their decoding', async () => {
  const query = `select '2000-02-29'::date leap_day, '2026-03-08'::date spring_day,
    '2026-11-01'::date fall_day, null::date absent,
    '2026-03-08 12:34:56.123'::timestamp wall_clock,
    '2026-03-08 12:34:56.123+00'::timestamptz instant`;
  for (const zone of ['UTC', 'America/Chicago', 'Asia/Tokyo']) {
    await c.query("select set_config('TimeZone',$1,false)", [zone]);
    const result = await c.query(query);
    const row = result.rows[0];
    const serialized = JSON.parse(JSON.stringify(row));
    for (const field of result.fields.filter(field => field.dataTypeID === 1082 && row[field.name] !== null)) {
      assert.equal(typeof row[field.name], 'string', field.name);
      assert.match(serialized[field.name], /^\d{4}-\d{2}-\d{2}$/);
    }
    assert.deepEqual([serialized.leap_day, serialized.spring_day, serialized.fall_day, serialized.absent],
      ['2000-02-29', '2026-03-08', '2026-11-01', null]);
    const original = await new Promise((resolve, reject) => {
      c.query({ text: query, types: pg.types }, (error, value) => error ? reject(error) : resolve(value));
    });
    for (const field of result.fields.filter(field => [1114, 1184].includes(field.dataTypeID)))
      assert.deepEqual(row[field.name], original.rows[0][field.name], field.name);
  }
});

test('HTTP query decoding preserves PostgreSQL dates and both timestamp types', async () => {
  const text = "select '2000-02-29'::date calendar_day, '2026-03-08 12:34:56.123'::timestamp wall_clock, '2026-03-08 12:34:56.123+00'::timestamptz instant";
  const wire = await c.query({ text, rowMode: 'array', types: { getTypeParser: () => value => value } });
  const original = neonConfig.fetchFunction;
  neonConfig.fetchFunction = async () => new Response(JSON.stringify({
    fields: wire.fields, rows: wire.rows, rowCount: wire.rowCount, command: wire.command,
  }), { status: 200 });
  try {
    const rows = await neon('postgresql://fixture@localhost/fixture').query(text);
    assert.equal(rows[0].calendar_day, '2000-02-29');
    assert.ok(rows[0].wall_clock instanceof Date);
    assert.ok(rows[0].instant instanceof Date);
    for (const field of wire.fields.filter(field => [1114,1184].includes(field.dataTypeID)))
      assert.deepEqual(rows[0][field.name], pg.types.getTypeParser(field.dataTypeID)(wire.rows[0][wire.fields.indexOf(field)]));
  } finally { neonConfig.fetchFunction = original; }
});

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
    writeFileSync(process.env.QUIET_OWNER_FIXTURE_OUTPUT, JSON.stringify({
      provenance: { transport: 'executeRegisteredTool/disposable-postgres',
        producer_revision: execFileSync('git', ['rev-parse','HEAD'], { encoding: 'utf8' }).trim(),
        ...recordingProvenance(['quiet-follow-ups']),
        registry_entry_sha256: registryPin(),
        query: { verb: 'quiet-follow-ups', all: {}, owned: { owner_slug: actor.slug }, unassigned: { owner_slug: null } } },
      owner_slug: actor.slug, all, owned, unassigned,
    }, null, 2) + '\n');
  }
});

test('registered date-bearing verbs serialize real PostgreSQL responses', async () => {
  await c.query('begin');
  try {
    const lead = await subject('lead', actor.id);
    await c.query("update lead set est_lease_event='2000-02-29',next_action_date='2000-03-01',last_touch='2000-01-01' where id=$1", [lead.id]);
    await c.query("insert into activity(occurred_at,actor_id,kind,summary,lead_id) values('2000-01-01T18:00:00Z',$1,'meeting','Synthetic date serialization',$2)", [actor.id,lead.id]);
    const responses = {}, date_columns = {};
    for (const verb of ['morning-brief', 'deal-board', 'lead-hot', 'lead-board', 'claim-card', 'stale-records', 'quiet-follow-ups']) {
      const dates = new Map();
      const db = { query: async (...args) => {
        const result = await c.query(...args);
        for (const field of result.fields || []) if (field.dataTypeID === 1082) {
          dates.set(field.name, null);
          for (const row of result.rows) if (row[field.name] !== null) {
            assert.match(row[field.name], /^\d{4}-\d{2}-\d{2}$/, `${verb}.${field.name}`);
            dates.set(field.name, row[field.name]);
          }
        }
        return result;
      } };
      responses[verb] = JSON.parse(JSON.stringify(await executeRegisteredTool(db, actor, verb, {})));
      date_columns[verb] = [...dates.keys()].sort();
      if (['lead-hot','lead-board'].includes(verb)) {
        const row = responses[verb].leads.find(row => row.id === lead.id);
        assert.equal(row.est_lease_event, '2000-02-29');
        assert.equal(row.next_action_date, '2000-03-01');
        assert.equal(row.last_touch, '2000-01-01');
      }
    }
    if (process.env.PG_DATE_FIXTURE_OUTPUT) writeFileSync(process.env.PG_DATE_FIXTURE_OUTPUT,
      JSON.stringify({ provenance: { transport: 'executeRegisteredTool/disposable-postgres',
        ...recordingProvenance(Object.keys(responses)),
        driver_sha256: createHash('sha256').update(readFileSync(new URL('../src/postgres.ts', import.meta.url))).digest('hex') }, date_columns, responses }, null, 2) + '\n');
  } finally { await c.query('rollback'); }
});

test('date verb recording is bound to the real producer and calendar contract', () => {
  const recording = JSON.parse(readFileSync(new URL('../../tools/fixtures/postgres-date-verbs.json', import.meta.url)));
  assert.equal(recording.provenance.transport, 'executeRegisteredTool/disposable-postgres');
  assertRecordingProducer(recording);
  assert.equal(recording.provenance.driver_sha256, createHash('sha256').update(
    readFileSync(new URL('../src/postgres.ts', import.meta.url))).digest('hex'));
  assert.deepEqual(Object.keys(recording.responses).sort(),
    ['morning-brief','deal-board','lead-hot','lead-board','claim-card','stale-records','quiet-follow-ups'].sort());
  for (const verb of ['lead-hot','lead-board']) {
    const lead = recording.responses[verb].leads.find(row => row.est_lease_event === '2000-02-29');
    assert.ok(lead, verb);
    assert.equal(lead.next_action_date, '2000-03-01');
    assert.equal(lead.last_touch, '2000-01-01');
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
  for (const zone of ['UTC', 'America/Chicago', 'Asia/Tokyo']) {
    await c.query("select set_config('TimeZone',$1,false)", [zone]);
    await c.query('set datestyle to SQL, DMY');
    try {
      for (const [i, kind] of ['lead', 'vendor'].entries()) {
        await c.query(`update ${kind} set last_touch='2000-02-29' where id=$1`, [subjects[i].id]);
      }
      await check('2000-02-29', { lead: '2000-04-14', vendor: '2000-05-29' });
      for (const [i, kind] of ['lead', 'vendor'].entries()) {
        await c.query(`update ${kind} set last_touch=null where id=$1`, [subjects[i].id]);
      }
      await check('2000-01-01', { lead: '2000-02-15', vendor: '2000-03-31' });
    } finally { await c.query('set datestyle to ISO, MDY'); }
  }
  for (const [i, kind] of ['lead', 'vendor'].entries()) {
    await c.query(`insert into activity(occurred_at,actor_id,kind,summary,${kind}_id)
      values('2000-07-02T00:30:00Z',$1,'meeting','Synthetic near-midnight contact',$2)`, [actor.id,subjects[i].id]);
  }
  await check('2000-07-01', { lead: '2000-08-15', vendor: '2000-09-29' });
  await c.query("update lead set next_action_date='2000-07-04' where id=$1", [subjects[0].id]);
  await check('2000-07-01', { lead: '2000-07-04', vendor: '2000-09-29' });
  await c.query("set time zone 'America/Chicago'");
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
  await c.query('update lead set next_action_date=carr_business_date()-2 where id=$1', [row.id]);
  const has = async () => (await read({ owner_slug: actor.slug })).proposals.some(p => p.subject_id === row.id);
  assert.equal(await has(), true);
  await c.query('update lead set next_action_date=carr_business_date()+2 where id=$1', [row.id]);
  assert.equal(await has(), false);
  await c.query('update lead set next_action_date=carr_business_date()-2,suppressed=true where id=$1', [row.id]);
  assert.equal(await has(), false);
  await c.query("update lead set suppressed=false,stage='closed_lost' where id=$1", [row.id]);
  assert.equal(await has(), false);
  await c.query("update lead set stage='new' where id=$1", [row.id]);
  await c.query("update party set contact_state='do_not_contact' where id=$1", [row.party]);
  assert.equal(await has(), false);
  await c.query("update party set contact_state='active' where id=$1", [row.party]);
  await c.query(`insert into next_action(subject_type,subject_id,owner_id,due_on,description,created_by,updated_by)
    values('lead',$1,$2,carr_business_date()+30,'Synthetic future open task',$2,$2)`, [row.id,actor.id]);
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
  assertRecordingProducer(recording);
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
    select id,'new',carr_business_date()-200,$1,$2,$2 from party where name like 'Synthetic overflow %'`, [other.id,actor.id]);
  await assert.rejects(() => read(), e => e.payload.error === 'quiet_follow_up_limit_exceeded');
  await assert.rejects(() => read({ owner_slug: other.slug }), e => e.payload.error === 'quiet_follow_up_limit_exceeded');
  assert.ok((await read({ owner_slug: actor.slug })).proposals.length > 0);
  assert.ok((await read({ owner_slug: null })).proposals.every(p => p.owner_id === null));
});
