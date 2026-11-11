import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool, object, array, string, required } from './helpers/legacy-test-registry.ts';

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
        const r = object(await executeRegisteredTool(c,actor,'lead-board',{workspace:'leads',lead_id:lead}));
        await c.query('commit');
        const detail = object(r.detail);
        const board = required(array(r.leads).map(object).find(row=>row.id===lead));
        assert.deepEqual(detail.opportunity_timing,board.opportunity_timing);
        assert.equal(r.last_search_at,null);
        const t = object(detail.opportunity_timing);
        assert.equal(t.contract,'lead-opportunity-timing.v1');
        assert.equal(Date.parse(string(t.record_added_at)),Date.parse('2026-11-01T12:00:00Z'));
        assert.ok(t.status === 'supported' || t.status === 'unknown');
        return {status:t.status, events:array(t.events).map(object)};
      } catch(error) { await c.query('rollback'); throw error; }
    };
    assert.equal((await timing()).status,'unknown');
    assert.deepEqual((await timing()).events,[]);
    const ingest = async (date:string,basis:string) => {
      const coverage = object(await executeRegisteredTool(c,actor,'list-practice-origination',{}));
      await executeRegisteredTool(c,actor,'record-practice-origination-feed',{
        idempotency_key:randomUUID(),source:'nppes',expected_checkpoint:required(array(coverage.coverage).map(object).find(f=>f.source==='nppes')).checkpoint,
        checkpoint:randomUUID(),published_on:'2026-11-02',status:'available',detail:'Synthetic timing evidence',
        records:[{record_key:npi,npi,name:'Synthetic timing practice',city:'Pensacola',state:'FL',
          evidence_date:date,date_basis:basis,source_url:`https://npiregistry.cms.hhs.gov/provider-view/${npi}`,facts:{}}],
      });
    };
    await ingest('2026-10-01','enumeration');
    let t = await timing();
    assert.equal(t.status,'supported'); assert.equal(t.events.length,1);
    const original = required(t.events[0]);
    assert.equal(original.date,'2026-10-01'); assert.equal(original.date_type,'date');
    assert.equal(original.date_basis,'enumeration'); assert.equal(original.meaning,'NPI enumerated');
    assert.equal(original.source,'nppes'); assert.equal(original.record_key,npi);
    assert.match(string(original.uncertainty),/opening.*unverified/);
    const first = original.revision;
    await ingest('2026-10-02','registry_update');
    t = await timing();
    const corrected = required(t.events[0]);
    assert.equal(t.events.length,1); assert.equal(corrected.date,'2026-10-02');
    assert.notEqual(corrected.revision,first);
    const canonical = (await c.query('select *,evidence_date::text as day from practice_origination_event where id=$1',[corrected.revision])).rows[0];
    assert.equal(corrected.date,canonical.day);
    assert.equal(corrected.date_basis,canonical.date_basis);
    assert.equal(corrected.source_url,canonical.source_url);
    assert.equal(Date.parse(string(corrected.observed_at)),canonical.recorded_at.getTime());
    assert.equal(corrected.published_on,'2026-11-02');
    const coverage = object(await executeRegisteredTool(c,actor,'list-practice-origination',{}));
    await executeRegisteredTool(c,actor,'record-practice-origination-feed',{
      idempotency_key:randomUUID(),source:'pecos',
      expected_checkpoint:required(array(coverage.coverage).map(object).find(f=>f.source==='pecos')).checkpoint,
      checkpoint:randomUUID(),published_on:'2026-11-02',status:'available',detail:'Synthetic conflicting source day',
      records:[{record_key:`${npi}|SYNTHETIC`,npi,name:'Synthetic timing practice',city:'Pensacola',state:'FL',
        evidence_date:'2026-11-02',date_basis:'publication',source_url:'https://data.cms.gov/synthetic',facts:{enrollment_id:'SYNTHETIC'}}],
    });
    t = await timing();
    assert.equal(t.events.length,2);
    assert.deepEqual(t.events.map(e=>e.date),['2026-10-02','2026-11-02']);
    assert.equal(required(t.events[1]).meaning,'Public PECOS enrollment observed');
    const duplicate = randomUUID();
    await c.query(`insert into party(id,kind,name,npi,created_by,updated_by) values($1,'org','Synthetic duplicate',$2,$3,$3)`,[duplicate,npi,actor.id]);
    assert.equal((await timing()).status,'unknown');
    await c.query('update party set deleted_at=now() where id=$1',[duplicate]);
    assert.equal((await timing()).status,'supported');
    await c.query("update practice_origination_feed set status='blocked' where source in ('nppes','pecos')");
    assert.equal((await timing()).status,'unknown');
    await c.query("update practice_origination_feed set status='unavailable' where source in ('nppes','pecos')");
    assert.equal((await timing()).status,'unknown');
    await c.query("update practice_origination_feed set status='available' where source in ('nppes','pecos')");
    await c.query('update party set npi=null where id=$1',[party]);
    assert.equal((await timing()).status,'unknown');
  } finally { await c.end(); }
});
