import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';

const dsn = await snapshotDatabase('lead-timing-');
test('registered workspace separates Added from exact current source evidence', async () => {
  const c = new pg.Client({connectionString:dsn}); await c.connect();
  try {
    const actor = {...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
    const party = randomUUID(), lead = randomUUID(), npi = '1999999911';
    await c.query(`insert into party(id,kind,name,npi,city,state,created_by,updated_by)
      values($1,'org','Synthetic timing practice',$2,'Pensacola','FL',$3,$3)`,[party,npi,actor.id]);
    await c.query(`insert into lead(id,party_id,stage,created_at,created_by,updated_by)
      values($1,$2,'new','2026-11-01T12:00:00Z',$3,$3)`,[lead,party,actor.id]);
    const timing = async () => {
      await c.query('begin');
      try {
        await c.query('set local role carr_reader');
        const r = await executeRegisteredTool(c,actor,'lead-board',{workspace:'leads',lead_id:lead});
        await c.query('commit');
        assert.deepEqual(r.detail.opportunity_timing,r.leads.find((row:{id:string})=>row.id===lead).opportunity_timing);
        assert.equal(r.last_search_at,null);
        const t = r.detail.opportunity_timing;
        assert.equal(t.contract,'lead-opportunity-timing.v1');
        assert.equal(Date.parse(t.record_added_at),Date.parse('2026-11-01T12:00:00Z'));
        return t;
      } catch(error) { await c.query('rollback'); throw error; }
    };
    assert.equal((await timing()).status,'unknown');
    assert.deepEqual((await timing()).events,[]);
    const ingest = async (date:string,basis:string) => {
      const coverage = await executeRegisteredTool(c,actor,'list-practice-origination',{});
      await executeRegisteredTool(c,actor,'record-practice-origination-feed',{
        idempotency_key:randomUUID(),source:'nppes',expected_checkpoint:coverage.coverage.find((f:{source:string})=>f.source==='nppes').checkpoint,
        checkpoint:randomUUID(),published_on:'2026-11-02',status:'available',detail:'Synthetic timing evidence',
        records:[{record_key:npi,npi,name:'Synthetic timing practice',city:'Pensacola',state:'FL',
          evidence_date:date,date_basis:basis,source_url:`https://npiregistry.cms.hhs.gov/provider-view/${npi}`,facts:{}}],
      });
    };
    await ingest('2026-10-01','enumeration');
    let t = await timing();
    assert.equal(t.status,'supported'); assert.equal(t.events.length,1);
    assert.equal(t.events[0].date,'2026-10-01'); assert.equal(t.events[0].date_type,'date');
    assert.equal(t.events[0].date_basis,'enumeration'); assert.equal(t.events[0].meaning,'NPI enumerated');
    assert.equal(t.events[0].source,'nppes'); assert.equal(t.events[0].record_key,npi);
    assert.match(t.events[0].uncertainty,/opening.*unverified/);
    const first = t.events[0].revision;
    await ingest('2026-10-02','registry_update');
    t = await timing();
    assert.equal(t.events.length,1); assert.equal(t.events[0].date,'2026-10-02');
    assert.notEqual(t.events[0].revision,first);
    const canonical = (await c.query('select * from practice_origination_event where id=$1',[t.events[0].revision])).rows[0];
    assert.equal(t.events[0].source_url,canonical.source_url);
    assert.equal(Date.parse(t.events[0].observed_at),canonical.recorded_at.getTime());
    assert.equal(t.events[0].published_on,'2026-11-02');
    const duplicate = randomUUID();
    await c.query(`insert into party(id,kind,name,npi,created_by,updated_by) values($1,'org','Synthetic duplicate',$2,$3,$3)`,[duplicate,npi,actor.id]);
    assert.equal((await timing()).status,'unknown');
    await c.query('update party set deleted_at=now() where id=$1',[duplicate]);
    assert.equal((await timing()).status,'supported');
    await c.query("update practice_origination_feed set status='unavailable' where source='nppes'");
    assert.equal((await timing()).status,'unknown');
    await c.query("update practice_origination_feed set status='available' where source='nppes'");
    await c.query('update party set npi=null where id=$1',[party]);
    assert.equal((await timing()).status,'unknown');
  } finally { await c.end(); }
});
