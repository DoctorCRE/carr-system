// Tests vendor meeting reads, contact suggestions, and access controls.
import test, { before, after } from 'node:test';
import { existsSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import assert from 'node:assert/strict';
import { appKeysValid, vendorRowRequired, vendorRowOptional, vendorDetailRequired, vendorDetailOptional } from './fixtures/vendor-directory-app-keys.mjs';
import { TOOLS } from '../src/tools.js';
import { parseBusinessQuery, parseBusinessRecordQuery, readBusinessList, readBusinessRecord } from '../src/workspace-business-read.js';

test('vendor meetings exposes a read-only versioned contract', () => {
  assert.ok(TOOLS['read-vendor-meetings'], 'V01 vendor meetings reader is registered');
  assert.equal(TOOLS['read-vendor-meetings'].write, false);
});

// No street word, so the fixture reads as synthetic to security/leak_guard.py.
const OFFICE_STREET='Synthetic Commons, Unit 100';
let fixture, admin, database, c, actor, sequence=0;
before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin, connection;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    const name = `vendor_meetings_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${name}" template template0`);
    database = name;
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'vendor-meetings-', pgCtl: `${bin}/pg_ctl` });
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  }
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname];
  if (ciDsn) await promisify(execFile)('psql', [connection.connectionString, ...restore], { maxBuffer: 2 * 1024 * 1024 });
  else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => {
  try { await c?.end(); } finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

async function candidate({name='Synthetic vendor contact', office=true}={}) {
 const company=(await c.query("insert into party(kind,name,created_by,updated_by) values('org',$1,$2,$2) returning id",['Synthetic lender '+(++sequence),actor.id])).rows[0].id;
 const contact=(await c.query("insert into party(kind,name,org_id,created_by,updated_by) values('person',$1,$2,$3,$3) returning id",[name,company,actor.id])).rows[0].id;
 await c.query("insert into vendor_category(slug,label,sort) values('fixture_lender','Synthetic lender',999) on conflict do nothing");
 const vendor=(await c.query("insert into vendor(party_id,category,category_slug,offers,created_by,updated_by) values($1,'Synthetic legacy text','fixture_lender','Discuss financing for a synthetic dental office',$2,$2) returning id",[company,actor.id])).rows[0].id;
 if(office) await finding(contact);
 return {company,contact,vendor};
}
async function finding(contact, value={}, columns={}) {
 const data={found:true,epistemic_status:'source_backed',location_type:'office',street_address:OFFICE_STREET,city:'Synthetic Bay',state:'AL',...value};
 return (await c.query("insert into record_flag(subject_type,subject_id,kind,value,source,observed_at,expires_on,created_by) values('party',$1,'address',$2,'https://example.invalid/team',coalesce($3::timestamptz,now()),$4,$5) returning id",[contact,JSON.stringify(data),columns.observed_at ?? null, columns.expires_on ?? null,actor.id])).rows[0].id;
}
async function read(args={}, who=actor) {
 await c.query('begin read only');
 try {await c.query('set local role carr_reader');return await TOOLS['read-vendor-meetings'].handler(c,who,args);}
 finally {await c.query('rollback');}
}
test('named contact with sourced office is suggested even without email or phone',async()=>{
 const f=await candidate();
 const result=await read();
 assert.equal(result.schema,'vendor-meetings.v1');
 const item=result.items.find(i=>i.vendor_id===f.vendor);
 assert.ok(item,'named contact and sourced office appears in suggested meetings');
 assert.equal(item.contact.id,f.contact);
 assert.equal(item.company.id,f.company);
 assert.equal(item.contact.email,null);
 assert.equal(item.contact.phone,null);
 assert.equal(item.office.street_address,OFFICE_STREET);
 assert.equal(item.category,'fixture_lender');
 assert.equal(item.category_label,'Synthetic lender');
 assert.equal(item.reason,'Discuss financing for a synthetic dental office');
 assert.deepEqual(item.missing,[]);
});

test('Research retains missing people and offices without borrowing a company address',async()=>{
 const f=await candidate({office:false});
 await finding(f.company);
 const suggested=await read();
 assert.equal(suggested.items.some(i=>i.vendor_id===f.vendor),false);
 const research=await read({view:'research',q:'Synthetic lender '+sequence});
 const row=research.items.find(i=>i.vendor_id===f.vendor);
 assert.deepEqual(row.missing,['verified_office']);
 assert.equal(row.office,null);
 assert.equal(row.research.status,'not_researched');
 await c.query('update party set deleted_at=now() where id=$1',[f.contact]);
 const withoutContact=(await read({view:'research'})).items.find(i=>i.vendor_id===f.vendor);
 assert.equal(withoutContact.contact,null);
 assert.deepEqual(withoutContact.missing,['named_contact','verified_office']);
});

test('latest unusable office finding cannot resurrect an older verified location',async()=>{
 for(const [value,columns] of [
  [{found:false},{}], [{found:'yes'},{}], [{found:'nonsense'},{}], [{epistemic_status:'disputed'},{}], [{internal:true},{}],
  [{proposes_correction:{field:'address',proposed:'Other office'}},{}],
  [{location_type:'home'},{}], [{street_address:' '},{}],
  [{},{expires_on:'2000-01-01'}], [{},{observed_at:'2099-01-01'}],
 ]) {
  const f=await candidate();
  await finding(f.contact,value,columns);
  assert.equal((await read()).items.some(i=>i.vendor_id===f.vendor),false);
  const row=(await read({view:'research'})).items.find(i=>i.vendor_id===f.vendor);
  assert.equal(row.office,null);
  assert.equal(row.research.status,'needs_review');
  assert.deepEqual(row.missing,['verified_office']);
 }
});

test('holds and vendor or lead restrictions stay out of Suggested',async()=>{
 for(const role of ['company','contact']) {
  const f=await candidate();
  await c.query("update party set contact_state='do_not_contact' where id=$1",[f[role]]);
  assert.equal((await read()).items.some(i=>i.vendor_id===f.vendor),false);
  assert.deepEqual((await read({view:'research'})).items.find(i=>i.vendor_id===f.vendor).missing,['contact_permission']);
 }
 const f=await candidate();
 await c.query("update vendor set disposition='avoid' where id=$1",[f.vendor]);
 assert.equal((await read()).items.some(i=>i.vendor_id===f.vendor),false);
 const suppressed=await candidate();
 await c.query("insert into lead_stage(slug,label,sort) values('fixture_stage','Synthetic stage',999) on conflict do nothing");
 await c.query("insert into lead(party_id,stage,suppressed,created_by,updated_by) values($1,'fixture_stage',true,$2,$2)",[suppressed.contact,actor.id]);
 assert.equal((await read()).items.some(i=>i.vendor_id===suppressed.vendor),false);
});

test('both partners traverse exact contacts and scoped Unicode searches without duplicated pages',async(t)=>{
 t.mock.timers.enable({apis:['Date'],now:Date.parse('2026-01-01T00:00:00Z')});
 const query='界'.repeat(80);
 const f=await candidate({name:query});
 const second=(await c.query("insert into party(kind,name,org_id,created_by,updated_by) values('person',$1,$2,$3,$3) returning id",[query+' second',f.company,actor.id])).rows[0].id;
 await finding(second);
 const first=await read({q:query,limit:1});
 assert.equal(first.total,2);
 assert.equal(first.has_more,true);
 const next=await read({q:query,limit:1,cursor:first.next_cursor},{slug:'dell'});
 assert.equal(next.has_more,false);
 assert.equal(next.next_cursor,null);
 assert.deepEqual([first.items[0].contact.id,next.items[0].contact.id].sort(),[f.contact,second].sort());
 assert.deepEqual(await read({q:query,limit:1},{slug:'dell'}),first);
 await assert.rejects(read({view:'research',q:query,cursor:first.next_cursor}),e=>e.payload.error==='vendor_meetings_query_invalid');
 await assert.rejects(read({q:'Other',cursor:first.next_cursor}),e=>e.payload.error==='vendor_meetings_query_invalid');
});

test('audience and query refusals happen before any database read',async()=>{
 const c={query:()=>{throw new Error('must not query');}};
 const tool=TOOLS['read-vendor-meetings'];
 for(const identity of [null,{slug:'external'},{slug:'codex'}]) await assert.rejects(tool.handler(c,identity,{}),e=>e.payload.error==='AUTHORIZATION_REFUSED');
 for(const args of [{limit:0},{limit:51},{limit:'2'},{q:42},{q:'x'.repeat(81)},{view:'all'},{cursor:'invalid'},{actor:'joe'}])
  await assert.rejects(tool.handler(c,{slug:'joe'},args),e=>e.payload.error==='vendor_meetings_query_invalid');
 await assert.rejects(tool.handler({query:()=>{throw new Error('private database error');}},{slug:'joe'},{}),e=>e.payload.error==='vendor_meetings_unavailable'&&!e.message.includes('private'));
});

test('blank names and missing recorded meeting context remain Research',async()=>{
 for(const [field,value,missing] of [['name',' ','named_contact'],['offers',' ','meeting_reason']]) {
  const f=await candidate();
  if(field==='name') await c.query('update party set name=$1 where id=$2',[value,f.contact]);
  else await c.query(`update vendor set ${field}=$1 where id=$2`,[value,f.vendor]);
  assert.equal((await read()).items.some(i=>i.vendor_id===f.vendor),false);
  assert.ok((await read({view:'research'})).items.find(i=>i.vendor_id===f.vendor).missing.includes(missing));
 }
});

test('known email and phone come through on a Suggested row',async()=>{
 const f=await candidate();
 await c.query("update party set email='contact@example.invalid',phone='251-155-0100' where id=$1",[f.contact]);
 const item=(await read()).items.find(i=>i.vendor_id===f.vendor);
 assert.ok(item,'a contact with known email and phone is still suggested');
 assert.equal(item.contact.email,'contact@example.invalid');
 assert.equal(item.contact.phone,'251-155-0100');
 assert.equal(item.contact.cell,null);
});

test('an uncategorised vendor stays in Research even with legacy free text',async()=>{
 const f=await candidate();
 await c.query("update vendor set category_slug=null,category='Target (not yet met)' where id=$1",[f.vendor]);
 assert.equal((await read()).items.some(i=>i.vendor_id===f.vendor),false);
 const row=(await read({view:'research'})).items.find(i=>i.vendor_id===f.vendor);
 assert.deepEqual(row.missing,['category']);
 assert.equal(row.category,null);
 assert.equal(row.category_label,null);
});

test('not-workable and out-of-market vendors stay in Research, never Suggested',async()=>{
 const parked=await candidate();
 await c.query("update vendor set disposition='parked' where id=$1",[parked.vendor]);
 const outside=await candidate();
 await c.query('update vendor set out_of_market=true where id=$1',[outside.vendor]);
 const suggested=await read();
 for(const f of [parked,outside]) assert.equal(suggested.items.some(i=>i.vendor_id===f.vendor),false);
 const research=(await read({view:'research'})).items;
 assert.deepEqual(research.find(i=>i.vendor_id===parked.vendor).missing,['workable_vendor']);
 assert.deepEqual(research.find(i=>i.vendor_id===outside.vendor).missing,['in_market']);
 const avoided=await candidate();
 await c.query("update vendor set disposition='avoid' where id=$1",[avoided.vendor]);
 assert.ok((await read({view:'research'})).items.find(i=>i.vendor_id===avoided.vendor).missing.includes('workable_vendor'));
});

async function directory(vendor, contract = 'vendor-directory.v2') {
 await c.query('begin read only');
 try {
  await c.query('set local role carr_reader');
  const options = {client:c, actor, dataset:'vendors', correlationId:'synthetic-directory'};
  const detail = await readBusinessRecord({...options,id:vendor,...parseBusinessRecordQuery(new URLSearchParams(contract ? {contract} : {}),'joe')});
  const list = await readBusinessList({...options,query:parseBusinessQuery('vendors',new URLSearchParams({q:detail.record.name,...(contract ? {contract} : {})}),'joe')});
  return {detail:detail.record,list};
 } finally {await c.query('rollback');}
}

test('directory list and detail expose named people and verified offices without requiring phone or email',async()=>{
 const f=await candidate();
 const result=await directory(f.vendor);
 const person=result.detail.people[0];
 assert.equal(person.contact.id,f.contact);
 assert.equal(person.contact.email,null);
 assert.equal(person.contact.phone,null);
 assert.equal(person.company.id,f.company);
 assert.equal(person.office.street_address,OFFICE_STREET);
 assert.equal(person.office.source,'https://example.invalid/team');
 assert.equal(person.research.status,'office_verified');
 assert.deepEqual(result.list.rows.find(row=>row.id===f.vendor).people,[person]);
 const legacy=await directory(f.vendor,null);
 assert.equal(Object.hasOwn(legacy.detail,'people'),false);
 assert.equal(Object.hasOwn(legacy.list.rows.find(row=>row.id===f.vendor),'people'),false);
});

test('directory research follows the newest finding and never inherits an organization office',async()=>{
 const f=await candidate({office:false});
 await finding(f.company);
 let person=(await directory(f.vendor)).detail.people[0];
 assert.equal(person.office,null);
 assert.equal(person.research.status,'not_researched');
 await finding(f.contact);
 assert.equal((await directory(f.vendor)).detail.people[0].research.status,'office_verified');
 for(const [value,columns] of [
  [{found:false},{}], [{found:'yes'},{}], [{epistemic_status:'disputed'},{}], [{internal:true},{}],
  [{proposes_correction:{field:'address',proposed:'Other office'}},{}],
  [{location_type:'home'},{}], [{street_address:' '},{}], [{city:42},{}],
  [{},{expires_on:'2000-01-01'}], [{},{observed_at:'2099-01-01'}],
 ]) {
  await finding(f.contact,value,columns);
  person=(await directory(f.vendor)).detail.people[0];
  assert.equal(person.office,null);
  assert.equal(person.research.status,'needs_review');
  assert.ok(person.research.finding_id);
  assert.ok(person.research.observed_at);
 }
});

test('directory nests exact live named people without multiplying vendor counts',async()=>{
 const f=await candidate();
 const other=(await c.query("insert into party(kind,name,org_id,created_by,updated_by) values('person','Synthetic second contact',$1,$2,$2) returning id",[f.company,actor.id])).rows[0].id;
 let result=await directory(f.vendor);
 assert.equal(result.list.total,1);
 assert.equal(result.list.rows.length,1);
 assert.deepEqual(result.detail.people.map(p=>p.contact.id),[f.contact,other].sort());
 assert.deepEqual(result.list.rows[0].people,result.detail.people);
 const direct=(await c.query("insert into vendor(party_id,category,created_by,updated_by) values($1,'fixture_lender',$2,$2) returning id",[f.contact,actor.id])).rows[0].id;
 assert.deepEqual((await directory(direct)).detail.people.map(p=>p.contact.id),[f.contact]);
 await c.query("update party set deleted_at=now() where id=$1",[other]);
 await c.query("update party set name=' ' where id=$1",[f.contact]);
 result=await directory(f.vendor);
 assert.deepEqual(result.detail.people,[]);
 assert.deepEqual(result.list.rows[0].people,[]);
});


test('v1 directory keys equal the main contract and satisfy strict app row and detail rules',async()=>{
 const f=await candidate();
 const result=await directory(f.vendor,'vendor-directory.v1');
 const row=result.list.rows.find(row=>row.id===f.vendor);
 // Main's v1 emits every optional directory field. These fixtures also pin its exact keys.
 for(const [value,required,optional] of [
  [row,vendorRowRequired,vendorRowOptional],
  [result.detail,vendorDetailRequired,vendorDetailOptional],
 ]) {
  assert.equal(Object.hasOwn(value,'people'),false);
  assert.deepEqual(Object.keys(value).sort(),[...required,...optional].sort());
  assert.equal(appKeysValid(value,required,optional),true);
  assert.equal(appKeysValid({...value,people:[]},required,optional),false,'app refuses leaked people');
 }
});
