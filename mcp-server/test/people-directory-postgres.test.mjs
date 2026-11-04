import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { readFileSync } from 'node:fs';
import { Validator } from '@cfworker/json-schema';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

test('party_search_pages_by_name_without_email', async t => {
  assert.ok(TOOLS['search-parties-v1'],'registered name search must exist');
  const fixture = await acquireDisposablePostgres({prefix:'people-directory-',snapshot:true});
  const c = new pg.Client(fixture.connection);
  await c.connect();
  t.after(async () => { try { await c.end(); } finally { await fixture.close(); } });
  const actor = {...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
  const ids = [randomUUID(),randomUUID(),randomUUID()].sort();
  const contract = JSON.parse(readFileSync(new URL('../contracts/people-directory.v1.json',import.meta.url),'utf8'));
  for (const [name,shape] of Object.entries(contract.verbs)) assert.deepEqual(TOOLS[name].inputSchema,shape.request);
  for (const id of ids) await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic Picker Name',$2,$2)",[id,actor.id]);
  const call = async (name,args={},who=actor,tenant='carr-internal') => {
    await c.query('begin read only');
    try {
      await c.query("select set_config('carr.organization_tenant_id',$1,true)",[tenant]);
      await c.query('set local role carr_reader');
      let queries=0;
      const reader={query:(...params)=>{ queries++; return c.query(...params); }};
      const result=await executeRegisteredTool(reader,who,name,args);
      assert.equal(queries,2,'directory cost does not grow with returned rows');
      assert.equal(new Validator(contract.verbs[name].response).validate(result).valid,true);
      await c.query('commit'); return result;
    } catch(e) { await c.query('rollback'); throw e; }
  };
  const search=args=>call('search-parties-v1',{query:'synthetic picker',...args});
  const first=await search({limit:2});
  assert.equal(first.schema_version,'party-search.v1');
  assert.deepEqual(first.parties.map(p=>p.id),ids.slice(0,2));
  assert.deepEqual(Object.keys(first.parties[0]).sort(),['display_name','id','kind']);
  const last=await search({limit:2,cursor:first.next_cursor});
  assert.deepEqual(last.parties.map(p=>p.id),ids.slice(2));
  assert.equal(last.next_cursor,null);
  assert.deepEqual(await search({limit:2}),first);
  await t.test('merged and deleted parties are excluded; paging survives deletion',async()=>{
    await c.query('update party set merged_into=$1 where id=$2',[ids[2],ids[0]]);
    await c.query('update party set deleted_at=now() where id=$1',[ids[1]]);
    assert.deepEqual((await search({})).parties.map(p=>p.id),[ids[2]]);
    assert.deepEqual((await search({cursor:first.next_cursor})).parties.map(p=>p.id),[ids[2]]);
  });
  await t.test('actor roster returns active canonical actors without contact data',async()=>{
    const inactive=randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name,active) values($1,$2,'human','Synthetic inactive',false)",[inactive,'inactive-'+inactive]);
    const roster=await call('read-actor-roster-v1');
    assert.equal(roster.schema_version,'actor-roster.v1');
    assert.ok(roster.actors.some(a=>a.id===actor.id));
    assert.ok(roster.actors.some(a=>a.slug==='dell'));
    assert.ok(!roster.actors.some(a=>a.id===inactive));
    assert.deepEqual(Object.keys(roster.actors[0]).sort(),['display_name','id','kind','slug']);
  });
  await t.test('partner authority and tenant refuse unauthorized reads',async()=>{
    for (const name of ['search-parties-v1','read-actor-roster-v1']) {
      const args=name==='search-parties-v1'?{query:'synthetic'}:{};
      await assert.rejects(call(name,args,{id:randomUUID(),slug:'system',human:false}),e=>e.payload?.error==='partner_required');
      await assert.rejects(call(name,args,actor,'foreign-tenant'),e=>e.payload?.error==='directory_tenant_refused');
      await assert.rejects(call(name,{...args,tenant:'foreign-tenant'}));
    }
  });
  await t.test('page size cap, cursor binding and literal prefix',async()=>{
    await assert.rejects(search({limit:101}));
    await assert.rejects(search({limit:0}));
    await assert.rejects(search({query:'other',cursor:first.next_cursor}));
    await assert.rejects(search({cursor:{}}));
    assert.deepEqual((await search({query:'synthetic%'})).parties,[]);
    assert.deepEqual((await search({query:'synthetic_'})).parties,[]);
    await c.query(`insert into party(kind,name,created_by,updated_by)
      select 'person','Synthetic Cap '||n,$1,$1 from generate_series(1,120) n`,[actor.id]);
    const capped=await search({query:'synthetic cap',limit:100});
    assert.equal(capped.parties.length,100);
    assert.equal((await search({query:'synthetic cap',limit:100,cursor:capped.next_cursor})).parties.length,20);
    assert.equal((await search({query:'synthetic cap'})).parties.length,25);
  });
  await t.test('index supports prefix seek; reads run in read only transactions',async()=>{
    await c.query('set enable_seqscan=off');
    const plan=await c.query(`explain select id from party where merged_into is null and deleted_at is null
      and lower(name) collate "C" like 'synthetic%' order by lower(name) collate "C",id limit 101`);
    assert.match(plan.rows.map(r=>r['QUERY PLAN']).join('\n'),/party_directory_name_idx/);
    await c.query('reset enable_seqscan');
    for (const name of ['search-parties-v1','read-actor-roster-v1']) assert.equal(TOOLS[name].write,false);
  });
  await t.test('roster refuses overflow rather than truncating actors',async()=>{
    await c.query(`insert into actor(slug,kind,display_name)
      select 'directory-cap-'||n,'system','Synthetic cap actor' from generate_series(1,1001) n`);
    await assert.rejects(call('read-actor-roster-v1'),e=>e.payload?.error==='actor_roster_capacity_exceeded');
  });
});
