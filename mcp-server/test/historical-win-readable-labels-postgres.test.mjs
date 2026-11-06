import test from 'node:test';
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('registered history preserves eligibility, counts and ordered recorded names', async () => {
  const fixture = await acquireDisposablePostgres({ prefix: 'historical-win-labels-', snapshot: true });
  const c = new pg.Client(fixture.connection);
  try {
    await c.connect();
    const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
    const party = '64700000-0000-4000-8000-000000000001', client = '64700000-0000-4000-8000-000000000002';
    await c.query(`insert into party(id,kind,name,state,created_by,updated_by) values($1,'org','Synthetic labels','TX',$2,$2)`, [party,actor.id]);
    await c.query(`insert into client(id,party_id,vertical,status,created_by,updated_by) values($1,$2,'Synthetic labels','engaged',$3,$3)`, [client,party,actor.id]);
    const names = ['Recorded renewal','Recorded renewal','   ','<img src=x onerror=alert(1)>'];
    for (let i = 0; i < 8; i++) {
      await c.query(`insert into deal(id,client_id,name,deal_type,phase,outcome,closed_on,created_by,updated_by)
        values($1,$2,$3,'renewal',$4,$5,$6,$7,$7)`,
      [`64700000-0000-4000-8000-${String(i + 10).padStart(12, '0')}`,client,names[i] || 'Excluded',
        i === 7 ? 'research' : 'closed',i === 7 ? null : i === 6 ? 'lost' : 'won',i === 4 ? null : i === 5 ? '2999-01-01' : '2020-02-29',actor.id]);
    }
    async function read(args = {}) {
      await c.query('begin read only');
      try {
        await c.query('set local role carr_reader');
        const result = await executeRegisteredTool(c, actor, 'ideal-client-profile', args);
        await c.query('commit'); return result;
      } catch (error) { await c.query('rollback'); throw error; }
    }
    const result = await read({ vertical: 'Synthetic labels' });
    if (process.env.HISTORICAL_LABEL_RECORDING) await writeFile(process.env.HISTORICAL_LABEL_RECORDING, JSON.stringify(result, null, 2) + '\n');
    assert.equal(result.schema_version, 'ideal-client-profile.v1');
    assert.equal(result.coverage, 'recorded_closed_won_only');
    assert.equal(result.profiles.length, 1);
    const row = result.profiles[0];
    assert.equal(row.won_deals, 4); assert.equal(row.clients, 1);
    assert.deepEqual(row.evidence.map(entry => entry.deal_name), names);
    assert.deepEqual(row.evidence.map(entry => entry.closed_on), Array(4).fill('2020-02-29'));
    assert.deepEqual(row.evidence.map(entry => entry.deal_id), names.map((_, i) => `64700000-0000-4000-8000-${String(i + 10).padStart(12, '0')}`));
    const empty = await read({ vertical: 'No synthetic match' });
    assert.deepEqual(empty.profiles, []);
    if (process.env.HISTORICAL_EMPTY_RECORDING) await writeFile(process.env.HISTORICAL_EMPTY_RECORDING, JSON.stringify(empty, null, 2) + '\n');
    const otherParty = '64700000-0000-4000-8000-000000000003', otherClient = '64700000-0000-4000-8000-000000000004';
    await c.query(`insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic merge destination',$2,$2)`, [otherParty,actor.id]);
    await c.query(`insert into client(id,party_id,status,created_by,updated_by) values($1,$2,'engaged',$3,$3)`, [otherClient,otherParty,actor.id]);
    for (const [table, id, destination] of [['client',client,otherClient],['party',party,otherParty]]) {
      await c.query(`update ${table} set merged_into=$2 where id=$1`, [id,destination]);
      assert.deepEqual((await read({ vertical: 'Synthetic labels' })).profiles, [], `merged ${table} excluded`);
      await c.query(`update ${table} set merged_into=null where id=$1`, [id]);
    }
    await c.query('update party set deleted_at=now() where id=$1', [party]);
    assert.deepEqual((await read({ vertical: 'Synthetic labels' })).profiles, []);
    await c.query('update party set deleted_at=null where id=$1', [party]);
    await c.query(`insert into deal(client_id,name,deal_type,phase,outcome,closed_on,city,created_by,updated_by)
      select $1,'Synthetic cap','renewal','closed','won','2020-02-29','Synthetic city '||i,$2,$2 from generate_series(1,1001) i`, [client,actor.id]);
    await assert.rejects(() => read({ vertical: 'Synthetic labels' }), error => error.payload.error === 'client_profile_limit_exceeded');
    assert.equal((await read({ vertical: 'Synthetic labels', city: 'Synthetic city 1' })).profiles[0].won_deals, 1);
  } finally { await c.end(); await fixture.close(); }
});
