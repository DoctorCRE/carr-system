import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import pg from 'pg';
import {acquireDisposablePostgres,acquirePostgresFixtureGroup} from './helpers/disposable-postgres.ts';
import {readClientAnniversaries} from '../src/client-anniversary-read.ts';
let bin;
try{bin=execFileSync('pg_config',['--bindir'],{encoding:'utf8'}).trim();}catch{}
for(const candidate of ['/opt/homebrew/opt/postgresql@17/bin','/usr/lib/postgresql/17/bin','/usr/lib/postgresql/16/bin']) if((!bin||!existsSync(path.join(bin,'postgres')))&&existsSync(path.join(candidate,'postgres')))bin=candidate;
test('canonical Postgres closing read excludes merged clients and deleted parties',{skip:!bin&&'Postgres unavailable'},async()=>{
 const release=await acquirePostgresFixtureGroup();let fixture,c;
 try{
 fixture=await acquireDisposablePostgres({prefix:'client-anniversary-',pgCtl:path.join(bin,'pg_ctl'),dataName:'.'});
 const dir=fixture.root;
 await fixture.run(path.join(bin,'initdb'),['-D',dir,'-U','fixture','--auth=trust','--no-locale']);
 await fixture.run(path.join(bin,'pg_ctl'),['-D',dir,'-l',path.join(dir,'server.log'),'-o',`-k ${dir} -h ''`,'-w','start']);
 c=new pg.Client({host:dir,user:'fixture',database:'postgres'});await c.connect();
 const schema=readFileSync(new URL('../../db/schema.sql',import.meta.url),'utf8');
 for(const name of ['deal','client','party'])await c.query(schema.match(new RegExp(`CREATE TABLE public.${name} \\([\\s\\S]*?\\n\\);`))[0]);
 const id=n=>`aa000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
 for(let n=1;n<=7;n++){
 await c.query("insert into party(id,name,kind,created_by,updated_by,deleted_at) values($1,'Synthetic client','person',$2,$2,$3)",[id(n),id(99),n===7?'2026-01-01':null]);
 await c.query('insert into client(id,party_id,created_by,updated_by,merged_into) values($1,$2,$3,$3,$4)',[id(n+10),id(n),id(99),n===6?id(11):null]);
 await c.query("insert into deal(id,client_id,name,deal_type,phase,outcome,closed_on,created_by,updated_by) values($1,$2,'Synthetic deal','lease',$3,$4,$5,$6,$6)",[id(n+20),id(n+10),'closed',n===3?'lost':'won',n===2?null:n===4?'2028-02-28':'2020-02-29',id(99)]);
 }
 await c.query('create role carr_reader; grant usage on schema public to carr_reader');
 const grants=readFileSync(new URL('../../migrations/0500_workspace_business_reader_columns.sql',import.meta.url),'utf8');
 for(const table of ['client','party']) await c.query(grants.split('\n').find(line=>line.endsWith(`ON TABLE public.${table} TO carr_reader;`)));
 await c.query('grant select on public.deal to carr_reader');
 await c.query('grant select (version, org_id) on public.party to carr_reader');
 await c.query('set role carr_reader');
 const read=()=>readClientAnniversaries({client:c,actor:{slug:'joe'},now:()=>new Date('2027-02-27T18:00:00Z')});
 const r=await read();assert.equal(r.state,'ready');assert.deepEqual(r.rows.map(r=>r.deal_id),[id(21),id(25)]);assert.equal(r.coverage.skipped_invalid,1);
 assert.equal(r.rows[0].deal_version,1);
 await assert.rejects(c.query('delete from public.deal'),e=>e.code==='42501');
 await c.query('reset role');await c.query('drop table deal');assert.equal((await read()).state,'unavailable');
 }finally{try{await c?.end();await fixture?.close();}finally{await release();}}
});
