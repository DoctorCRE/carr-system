import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { publicError } from '../src/public-errors.ts';

let fixture, c, actor;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'scope-append-wr-', snapshot: true });
  c = new pg.Client(fixture.connection);
  await c.connect();
  await c.query('begin');
  await c.query(readFileSync(new URL('../../migrations/1121_append_work_request_scope.sql', import.meta.url), 'utf8'));
  await c.query('commit');
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

async function command(name, args, role = 'carr_writer') {
  await c.query('begin');
  try {
    await c.query(`set local role ${role}`);
    const result = await executeRegisteredTool(c, actor, name, args);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}

const criteria = n => JSON.stringify(Array.from({ length: n }, (_, i) => ({ id: `AC-${i + 1}`, text: `Synthetic criterion ${i + 1}` })));
let doctrine;
async function sourcedRequest(ref, count = 1) {
  if (!doctrine) {
    const doc = (await c.query(`insert into public.doctrine_document(slug,title,content_class,visibility)
      values('scope-append-test','Synthetic scope','reference','shared') returning id`)).rows[0];
    const section = (await c.query(`insert into public.doctrine_section(document_id,section_key,title,ordinal,status,current_version)
      values($1,'origin','Synthetic origin',1,'active',1) returning id`, [doc.id])).rows[0];
    const revision = (await c.query(`insert into public.doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash)
      values($1,1,$2,'{"text":"Synthetic"}','Synthetic',$3) returning id`, [section.id, actor.id, 'c'.repeat(64)])).rows[0];
    await c.query('update public.doctrine_section set current_revision_id=$1 where id=$2', [revision.id, section.id]);
    doctrine = { section: section.id, revision: revision.id };
  }
  await c.query(`insert into ops.work_request(ref,title,desired_outcome,requester_actor,owner_actor,acceptance_criteria,
    capture_idempotency_key,organization_tenant_id,doctrine_section_id,doctrine_revision_id,sourced_capture_sequence,origin_ref)
    values($1,'Synthetic sourced scope','Synthetic outcome.','joe','builder',$2,$3,'carr-internal',$4,$5,$6,'doctrine:scope-append-test#origin')`,
    [ref, criteria(count), randomUUID(), doctrine.section, doctrine.revision, Number(ref.slice(3))]);
}
const append = (ref, extra = {}) => ({
  idempotency_key: randomUUID(), human_ref: ref, base_version: 1,
  criteria: [{ id: 'BF-1', text: 'Synthetic appended criterion' }],
  source_ref: 'record:ROW-0000000000000001', ...extra,
});
const row = async ref => (await c.query('select state,version,desired_outcome,acceptance_criteria from ops.work_request where ref=$1', [ref])).rows[0];

test('append-work-request-scope appends after existing criteria, bumps version and replays', async () => {
  await sourcedRequest('WR-998001', 2);
  const args = append('WR-998001', { outcome_addendum: 'Also covers the synthetic backfill row.' });
  const out = await command('append-work-request-scope', args);
  assert.deepEqual({ ...out }, { ok: true, human_ref: 'WR-998001', state: 'captured', version: 2,
    appended: ['BF-1'], criteria_count: 3 });
  const after = await row('WR-998001');
  assert.deepEqual(after.acceptance_criteria.map(x => x.id), ['AC-1', 'AC-2', 'BF-1']);
  assert.equal(after.desired_outcome, 'Synthetic outcome.\n\nAlso covers the synthetic backfill row.');
  assert.deepEqual(await command('append-work-request-scope', args), { ...JSON.parse(JSON.stringify(out)), replayed: true });
  assert.equal((await row('WR-998001')).version, 2);
  const events = (await c.query('select new_value from event where idempotency_key=$1', [args.idempotency_key])).rows;
  assert.equal(events.length, 1);
  assert.equal(events[0].new_value.source_ref, 'record:ROW-0000000000000001');
});

test('an addendum on a request with no desired outcome becomes the whole outcome', async () => {
  await c.query(`insert into ops.work_request(ref,title,requester_actor,owner_actor,acceptance_criteria)
    values('WR-998003','Synthetic unsourced scope','joe','builder',$1)`, [criteria(1)]);
  await command('append-work-request-scope', append('WR-998003', { outcome_addendum: 'Added.' }));
  assert.equal((await row('WR-998003')).desired_outcome, 'Added.');
});

// A receipt forged by the database owner, so the guard's other checks are tested alone.
async function receipt(ref, appended) {
  const w = (await c.query('select id,version from ops.work_request where ref=$1', [ref])).rows[0];
  await c.query(`insert into event(occurred_at,actor_id,verb,subject_type,subject_id,field,new_value,cause,idempotency_key)
    values(now(),$1,'append-work-request-scope','ops_work_request',$2,'acceptance_criteria',$3,'system',$4)`,
    [actor.id, w.id, { version: w.version + 1, appended, outcome_addendum: null }, randomUUID()]);
}

test('the sourced guard refuses a direct append with no matching receipt', async () => {
  await sourcedRequest('WR-998004', 1);
  const append1 = `update ops.work_request set version=version+1,
    acceptance_criteria=acceptance_criteria || '[{"id":"BF-1","text":"New"}]' where ref='WR-998004'`;
  await c.query('begin');
  try {
    await c.query('set local role carr_writer');
    await assert.rejects(c.query(append1), /sourced Program 6 Work Requests permit only/);
  } finally { await c.query('rollback'); }
  await receipt('WR-998004', [{ id: 'BF-1', text: 'Different' }]);
  await assert.rejects(c.query(append1), /sourced Program 6 Work Requests permit only/);
  assert.equal((await row('WR-998004')).version, 1);
});

for (const [label, bad] of [['numeric text', { id: 'BF-1', text: 123 }], ['object text', { id: 'BF-1', text: { a: 1 } }], ['numeric id', { id: 7, text: 'Seven' }]]) {
  test(`the sourced guard refuses ${label} even with a receipt`, async () => {
    const ref = { 'numeric text': 'WR-998005', 'object text': 'WR-998006', 'numeric id': 'WR-998007' }[label];
    await sourcedRequest(ref, 1);
    await receipt(ref, [bad]);
    await assert.rejects(c.query(`update ops.work_request set version=version+1,
      acceptance_criteria=acceptance_criteria || $2::jsonb where ref=$1`, [ref, JSON.stringify([bad])]), /sourced Program 6 Work Requests permit only/);
  });
}

test('the sourced guard refuses a direct write that rewrites an existing criterion', async () => {
  await sourcedRequest('WR-998002', 2);
  await assert.rejects(c.query(`update ops.work_request set version=version+1,
    acceptance_criteria='[{"id":"AC-1","text":"Rewritten"},{"id":"AC-2","text":"Synthetic criterion 2"},{"id":"BF-1","text":"New"}]'
    where ref='WR-998002'`), /sourced Program 6 Work Requests permit only/);
  await assert.rejects(c.query(`update ops.work_request set version=version+1, title='Renamed',
    acceptance_criteria=acceptance_criteria || '[{"id":"BF-1","text":"New"}]' where ref='WR-998002'`), /sourced Program 6 Work Requests permit only/);
  assert.equal((await row('WR-998002')).version, 1);
});

for (const [label, ref, setup, modify, expected] of [
  ['unknown reference', 'WR-998099', null, () => {}, 'work_request_not_found'],
  ['stale version', 'WR-998010', null, a => { a.base_version = 2; }, 'version_conflict'],
  ['declined request', 'WR-998011', "update ops.work_request set state='declined',exit_reason='Synthetic',closed_at=now() where ref=$1", () => {}, 'work_request_terminal_state'],
  ['ready request', 'WR-998012', "update ops.work_request set state='ready' where ref=$1", () => {}, 'work_request_scope_frozen'],
  ['twelve-criteria cap', 'WR-998013', null, a => { a.criteria = [{ id: 'BF-1', text: 'One' }, { id: 'BF-2', text: 'Two' }]; }, 'criteria_cap_exceeded'],
  ['existing criterion id', 'WR-998014', null, a => { a.criteria = [{ id: 'AC-1', text: 'Duplicate' }]; }, 'duplicate_criterion_id'],
  ['repeated criterion id', 'WR-998015', null, a => { a.criteria = [{ id: 'BF-1', text: 'A' }, { id: 'BF-1', text: 'B' }]; }, 'duplicate_criterion_id'],
  ['malformed criterion id', 'WR-998016', null, a => { a.criteria = [{ id: 'bf-1', text: 'Lower' }]; }, 'invalid_scope_append'],
  ['missing source', 'WR-998017', null, a => { delete a.source_ref; }, 'missing_required'],
]) {
  test(`append-work-request-scope refuses ${label} and changes nothing`, async () => {
    if (label !== 'unknown reference') {
      if (setup) {
        await c.query(`insert into ops.work_request(ref,title,requester_actor,owner_actor,acceptance_criteria)
          values($1,'Synthetic unsourced scope','joe','builder',$2)`, [ref, criteria(1)]);
        await c.query(setup, [ref]);
      } else await sourcedRequest(ref, label === 'twelve-criteria cap' ? 11 : 1);
    }
    const before = label === 'unknown reference' ? null : await row(ref);
    const args = append(ref); modify(args);
    await assert.rejects(command('append-work-request-scope', args), error => {
      assert.equal(error.payload?.error, expected);
      assert.equal(publicError(error).error, expected);
      return true;
    });
    if (before) assert.deepEqual(await row(ref), before);
    assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1', [args.idempotency_key])).rows[0].count, '0');
  });
}
