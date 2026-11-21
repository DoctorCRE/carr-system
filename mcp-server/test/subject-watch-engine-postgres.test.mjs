import assert from 'node:assert/strict';
import test from 'node:test';
import pg from 'pg';
import { sweepSubjectWatches } from '../src/subject-watch-scheduler.ts';
import { snapshotDatabase } from './helpers/postgres-database.mjs';

const dsn = await snapshotDatabase('subject-watch-engine-');
const writer = new URL(dsn);
writer.searchParams.set('options', '-c role=carr_writer');

test('engine intake stays off by default and retries an overlapping sweep without duplicate or paused receipts', async t => {
  const c = new pg.Client({ connectionString: dsn });
  await c.connect();
  t.after(() => c.end());
  const { id: joe } = (await c.query("select id from actor where slug='joe'")).rows[0];
  const { id: party } = (await c.query(
    "insert into party(kind,name,created_by,updated_by) values('person','Synthetic engine client',$1,$1) returning id", [joe],
  )).rows[0];
  await c.query("insert into client_status(slug,label) values('active','Active') on conflict do nothing");
  const { id: client } = (await c.query(
    "insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [party, joe],
  )).rows[0];
  const { id: deal } = (await c.query(
    "insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic engine deal','purchase','research','joe',$2,$2) returning id", [client, joe],
  )).rows[0];
  await c.query(`
    insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state,subscribed_at)
    select id,'deal',$1,'{}','{"change_kinds":["reply_arrived"],"include_self_changes":false}',
      case slug when 'joe' then 'watching' else 'paused' end,'2027-02-01T11:50:00Z'
    from actor where slug in ('joe','dell')`, [deal]);

  const change = {
    change_id: '9007199254740993', field: 'phase', old_value: 'research', new_value: 'negotiation',
    actor: 'dell', for_user: null, changed_at: '2027-02-01T12:03:00Z',
  };
  const calls = [];
  let upstreamFails = true;
  let pauseDuringRead = false;
  t.mock.method(globalThis, 'fetch', async (target, request) => {
    calls.push(target.searchParams.get('since'));
    assert.equal(target.pathname, '/jobs/deal-watches/deal/' + deal);
    assert.equal(target.searchParams.get('subscriber'), 'joe');
    assert.equal(request.headers['Engine-Job'], 'agent');
    if (upstreamFails) return new Response(null, { status: 503 });
    if (pauseDuringRead) await c.query("update ops.subject_watch_v2_subscription set state='paused',lifecycle_version=2 where actor_id=$1", [joe]);
    return Response.json({ kind: 'deal', id: deal, deal_id: deal, changes: [
      change, change, { ...change, change_id: '2', actor: 'agent', for_user: 'joe' },
      { ...change, change_id: '3', changed_at: '2027-02-01T12:12:00Z', new_value: 'closed' },
    ] });
  });
  const sweep = (minute, switched = {}) => sweepSubjectWatches({ scheduledTime: Date.UTC(2027, 1, 1, 12, minute) }, {
    DATABASE_URL_WRITER: writer.href, ENGINE_URL: 'https://engine.example.test',
    ENGINE_ACCESS_CLIENT_ID: 'test-client', ENGINE_ACCESS_CLIENT_SECRET: 'test-secret', ...switched,
  }, pg.Pool);
  const on = { ENGINE_DEAL_WATCH_CHANGES: 'true' };
  const cursor = async () => (await c.query('select scheduled_at, after_watch_id from ops.subject_watch_sweep')).rows[0];
  const receipts = async () => (await c.query(`
    select a.slug, semantic_key, changes, disposition from ops.subject_watch_change c join actor a on a.id=c.actor_id
    where c.subject_id=$1 order by semantic_key`, [deal])).rows;

  await sweep(0);
  assert.deepEqual(calls, []);
  assert.deepEqual(await receipts(), []);
  const before = await cursor();
  assert.equal(before.scheduled_at.toISOString(), '2027-02-01T12:00:00.000Z');

  await assert.rejects(sweep(5, on));
  assert.deepEqual(await cursor(), before);
  assert.deepEqual(await receipts(), []);
  upstreamFails = false;
  await sweep(5, on);
  await sweep(10, on);
  assert.deepEqual(calls.map(value => new Date(value).toISOString()), [
    '2027-02-01T11:50:00.000Z', '2027-02-01T11:50:00.000Z', '2027-02-01T12:00:00.000Z',
  ]);
  const expected = [{
    disposition: 'emitted', slug: 'joe', semantic_key: 'engine:9007199254740993:phase',
    changes: [{ field: 'phase', before: 'research', after: 'negotiation', reason_code: 'value_changed' }],
  }];
  assert.deepEqual(await receipts(), expected);
  assert.equal((await cursor()).scheduled_at.toISOString(), '2027-02-01T12:10:00.000Z');
  pauseDuringRead = true;
  await sweep(15, on);
  assert.deepEqual(await receipts(), expected);
  assert.equal(calls.length, 4);
});

test('only the scheduler writer can execute the bounded engine watch functions', async t => {
  const c = new pg.Client({ connectionString: writer.href });
  await c.connect();
  t.after(() => c.end());
  assert.deepEqual((await c.query("select ops.read_subject_watch_engine_batch('2027-01-01Z',4) result")).rows[0].result, []);
  await assert.rejects(c.query("select ops.read_subject_watch_engine_batch('2027-01-01Z',5)"), /watch_batch_limit/);
  for (const table of ['subject_watch', 'subject_watch_v2_subscription', 'subject_watch_delivery', 'subject_watch_sweep', 'subject_watch_change', 'subject_watch_engine_targets']) {
    await assert.rejects(c.query('select * from ops.' + table), { code: '42501' });
  }
  for (const name of ['read_subject_watch_engine_batch', 'sweep_subject_watches']) {
    const { rows } = await c.query(`
      select coalesce(r.rolname,'public') as role
      from pg_proc p cross join lateral aclexplode(p.proacl) a left join pg_roles r on r.oid=a.grantee
      where p.pronamespace='ops'::regnamespace and p.proname=$1
        and ($1<>'sweep_subject_watches' or p.pronargs=3) and a.grantee<>p.proowner`, [name]);
    assert.deepEqual(rows, [{ role: 'carr_writer' }]);
  }
});
