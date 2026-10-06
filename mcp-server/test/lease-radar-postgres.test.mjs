import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import pg from 'pg';
import {LEASE_RADAR_SQL, readLeaseRadar} from '../src/workspace-business-read.js';
let bin;
try{bin=execFileSync('pg_config',['--bindir'],{encoding:'utf8'}).trim();}catch{}
if(!bin || !existsSync(path.join(bin,'postgres'))) for(const candidate of ['/opt/homebrew/opt/postgresql@17/bin','/usr/lib/postgresql/17/bin','/usr/lib/postgresql/16/bin']) if(existsSync(path.join(candidate,'postgres'))){bin=candidate;break;}
const available=bin && existsSync(path.join(bin,'postgres'));
const id=n=>`aa000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
test('real PostgreSQL projection covers horizon, missing dates, tombstones, holds and reader-only view grant',{skip:!available && 'PostgreSQL binaries unavailable'},async()=>{
  let postgresFixture, dir, c;
  const releaseBudget = await acquirePostgresFixtureGroup();
  try{
    postgresFixture = await acquireDisposablePostgres({ prefix: 'lease-radar-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
    dir = postgresFixture.root;
    await postgresFixture.run(path.join(bin,'initdb'), ['-D',dir,'-U','fixture','--auth=trust','--no-locale']);
    await postgresFixture.run(path.join(bin,'pg_ctl'), ['-D',dir,'-l',path.join(dir,'server.log'),'-o',`-k ${dir} -h ''`,'-w','start']);
    c=new pg.Client({host:dir,user:'fixture',database:'postgres'});await c.connect();
    const schema=readFileSync(new URL('../../db/schema.sql',import.meta.url),'utf8');
    for(const name of ['actor','client','party','client_status','lease','next_action','critical_date']){
      const table=schema.match(new RegExp(`CREATE TABLE public.${name} \\([\\s\\S]*?\\n\\);`))?.[0];assert.ok(table,name);await c.query(table);
    }
    await c.query('create role carr_reader; grant usage on schema public to carr_reader;');
    await c.query(readFileSync(new URL('../../migrations/0814_lease_radar_read.sql',import.meta.url),'utf8'));
    await c.query("insert into actor(id,slug,display_name,kind) values ($1,'joe','Demo Broker','human')",[id(1)]);
    await c.query("insert into client_status(slug,label,sort) values ('past_client','Past client',1),('active_deal','Active deal',2)");
    for(let n=1;n<=9;n++){
      await c.query("insert into party(id,name,kind,created_by,updated_by,contact_state) values ($1,$2,'person',$3,$3,$4)",[id(10+n),`Demo Practice ${n}`,id(1),n===7?'do_not_contact':n===8?'paused':'active']);
      await c.query("insert into client(id,party_id,status,created_by,updated_by,owner_id,merged_into) values ($1,$2,$3,$4,$4,$4,$5)",[id(30+n),id(10+n),n===2?'active_deal':'past_client',id(1),n===6?id(31):null]);
      const expiration=["today","today + interval '24 months'","today + interval '24 months 1 day'","today - 1","null","today + 1","today + 2","today + 3","today + 4"][n-1];
      await c.query(`insert into lease(id,client_id,created_by,status,expiration_on) select $1,$2,$3,$4,${expiration} from (select (now() at time zone 'America/Chicago')::date as today) clock`,[id(50+n),id(30+n),id(1),n===9?'superseded':'legacy_unverified']);
      await c.query("insert into next_action(id,subject_type,subject_id,owner_id,created_by,updated_by,status,description,due_on) values ($1,'client',$2,$3,$3,$3,'open','Demo renewal review',(now() at time zone 'America/Chicago')::date)",[id(70+n),id(30+n),id(1)]);
    }
    await c.query('set role carr_reader');
    const read=(await c.query(LEASE_RADAR_SQL)).rows[0];
    assert.deepEqual(read.leases.map(r=>r.id).sort(),[1,2,5,7,8].map(n=>id(50+n)).sort());
    assert.equal(read.leases.find(r=>r.id===id(55)).expiration_on,null);
    assert.equal(read.leases.find(r=>r.id===id(52)).expiration_on,read.ends_on);
    assert.equal(read.leases.find(r=>r.id===id(51)).touch_eligible,true);
    for(const n of [2,7,8]) assert.equal(read.leases.find(r=>r.id===id(50+n)).touch_eligible,false);
    await assert.rejects(c.query('select * from public.lease'),e=>e.code==='42501');
    await assert.rejects(c.query('delete from public.v_client_lease_radar'),e=>e.code==='42501' || e.code==='55000');
    await c.query('reset role');
    // A future hold must be removed before LIMIT 1, for client and deal actions.
    await c.query("update next_action set due_on=(now() at time zone 'America/Chicago')::date - 1, hold_until=(now() at time zone 'America/Chicago')::date + 30 where id=$1",[id(71)]);
    await c.query('set role carr_reader');
    const heldOnly=(await c.query(LEASE_RADAR_SQL)).rows[0].leases.find(r=>r.id===id(51));
    assert.equal(heldOnly.touch_id,null,'held-only client has no permitted touch');
    assert.notEqual(heldOnly.touch_eligible,true);
    await c.query('reset role');
    await c.query("insert into next_action(id,subject_type,subject_id,owner_id,created_by,updated_by,status,description,due_on) values ($1,'client',$2,$3,$3,$3,'open','Permitted renewal review',(now() at time zone 'America/Chicago')::date)",[id(90),id(31),id(1)]);
    await c.query('set role carr_reader');
    const permitted=(await c.query(LEASE_RADAR_SQL)).rows[0].leases.find(r=>r.id===id(51));
    assert.equal(permitted.touch_id,id(90),'unheld touch wins over earlier held touch');
    assert.equal(permitted.touch_eligible,true);
    await c.query('reset role');
    await c.query('update lease set deal_id=$1 where id=$2',[id(100),id(51)]);
    await c.query("update next_action set subject_type='deal',subject_id=$1 where id=$2",[id(100),id(71)]);
    await c.query('set role carr_reader');
    assert.equal((await c.query(LEASE_RADAR_SQL)).rows[0].leases.find(r=>r.id===id(51)).touch_id,id(90),'held deal action cannot hide client touch');
    for(const offset of [-1,0]){
      await c.query('reset role');
      await c.query("update next_action set hold_until=(now() at time zone 'America/Chicago')::date + $1::int where id=$2",[offset,id(71)]);
      await c.query('set role carr_reader');
      const released=(await c.query(LEASE_RADAR_SQL)).rows[0].leases.find(r=>r.id===id(51));
      assert.equal(released.touch_id,id(71),`hold at offset ${offset} is released`);
      assert.equal(released.touch_eligible,true);
    }
  } finally {
    try {
      try { await c?.end(); } finally {
        await postgresFixture?.close();
      }
    } finally { await releaseBudget(); }
  }
});

test('database horizon changes both reported coverage and membership, including an empty ledger',{skip:!available && 'PostgreSQL binaries unavailable'},async()=>{
  let postgresFixture, dir, c;
  const releaseBudget = await acquirePostgresFixtureGroup();
  try{
    postgresFixture = await acquireDisposablePostgres({ prefix: 'lease-radar-policy-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
    dir = postgresFixture.root;
    await postgresFixture.run(path.join(bin,'initdb'), ['-D',dir,'-U','fixture','--auth=trust','--no-locale']);
    await postgresFixture.run(path.join(bin,'pg_ctl'), ['-D',dir,'-l',path.join(dir,'server.log'),'-o',`-k ${dir} -h ''`,'-w','start']);
    c=new pg.Client({host:dir,user:'fixture',database:'postgres'});await c.connect();
    const schema=readFileSync(new URL('../../db/schema.sql',import.meta.url),'utf8');
    for(const name of ['actor','client','party','client_status','lease','next_action','critical_date']){
      const table=schema.match(new RegExp(`CREATE TABLE public.${name} \\([\\s\\S]*?\\n\\);`))?.[0];assert.ok(table,name);await c.query(table);
    }
    await c.query('create role carr_reader; grant usage on schema public to carr_reader;');
    const migration=readFileSync(new URL('../../migrations/0814_lease_radar_read.sql',import.meta.url),'utf8');
    await c.query(migration);
    await c.query("insert into actor(id,slug,display_name,kind) values ($1,'joe','Demo Broker','human')",[id(1)]);
    await c.query("insert into party(id,name,kind,created_by,updated_by) values ($1,'Demo Practice','person',$2,$2)",[id(11),id(1)]);
    await c.query("insert into client(id,party_id,status,created_by,updated_by) values ($1,$2,'past_client',$3,$3)",[id(31),id(11),id(1)]);
    await c.query("insert into lease(id,client_id,created_by,status,expiration_on) values ($1,$2,$3,'legacy_unverified',((now() at time zone 'America/Chicago')::date + interval '18 months')::date)",[id(51),id(31),id(1)]);
    const expected=(await c.query("select (now() at time zone 'America/Chicago')::date::text as starts_on, ((now() at time zone 'America/Chicago')::date + interval '12 months')::date::text as ends_on")).rows[0];
    const read=()=>readLeaseRadar({client:c,actor:{slug:'joe'},correlationId:'policy-fixture'});
    await c.query('set role carr_reader');
    assert.equal((await read()).leases.length,1,'baseline 24-month policy includes 18-month lease');
    await c.query('reset role');
    // Change only the database policy. The unchanged HTTP reader must follow it.
    assert.match(migration,/interval '24 months'/);
    await c.query(migration.replace('create view','create or replace view').replace("interval '24 months'","interval '12 months'"));
    await c.query('set role carr_reader');
    const shorter=await read();
    assert.deepEqual(shorter.window,expected,'coverage follows database policy');
    assert.deepEqual(shorter.leases,[],'membership follows database policy');
    await c.query('reset role');
    await c.query('delete from lease');
    await c.query('set role carr_reader');
    const empty=await read();
    assert.deepEqual(empty.window,expected,'empty ledger still reports database coverage');
    assert.deepEqual(empty.leases,[]);
  } finally {
    try {
      try { await c?.end(); } finally {
        await postgresFixture?.close();
      }
    } finally { await releaseBudget(); }
  }
});
