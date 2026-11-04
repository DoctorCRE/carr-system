import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { readMigration } from './migration-source.testhelper.mjs';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { clientTourProposalTools, pendingClientTourProposals } from '../src/client-tour-proposals.js';

function definition(file,name) {
  const sql=readMigration(file);
  const start=Math.max(sql.lastIndexOf(`create or replace function ops.${name}(`),sql.lastIndexOf(`create function ops.${name}(`));
  assert(start>=0);
  return sql.slice(start,sql.indexOf('$$;',sql.indexOf('as $$',start))+3);
}

test('proposal_accept_is_the_only_path_that_changes_the_shortlist in PostgreSQL', async ()=>{
  const db=await postgresDatabase('tour-proposals-');
  const c=db.client;
  try {
    for (const file of ['0001_init.sql','0008_client_merged_into.sql']) await c.query(readMigration(file));
    await c.query('create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create role carr_jobs; create role dot_reader; create extension pgcrypto');
    for (const file of ['0975_client_portal.sql','0976_client_portal_binding.sql','0981_client_portal_writer_denials.sql','1013_register_client_portal.sql']) await c.query(readMigration(file));
    await c.query(`alter table deal add column owner text default 'joe';
      create table ops.tour(id uuid primary key,organization_tenant_id text,deal_id uuid references deal(id),unique(organization_tenant_id,id));
      create table ops.tour_property(id uuid primary key,organization_tenant_id text,property_status text);
      create table ops.tour_selection_cart_version(id uuid primary key default gen_random_uuid(),organization_tenant_id text,tour_id uuid,selection_version integer,base_selection_version_id uuid,property_ids jsonb,selection_digest text,created_by_actor_id text,created_at timestamptz default now(),unique(organization_tenant_id,tour_id,selection_version));`);
    await c.query(definition('0429_tour_domain_route_cheat_sheet.sql','tour_server_actor_id'));
    await c.query(definition('0430_tour_delivery_data_plane.sql','append_tour_selection_cart_version'));
    await c.query(definition('1142_tour_deal_access.sql','tour_deal_visible'));
    if (!process.argv.includes('--baseline')) await c.query(readMigration('1148_client_tour_proposals.sql'));
    const actor=randomUUID(),party=randomUUID(),client=randomUUID(),deal=randomUUID(),tour=randomUUID();
    const a=randomUUID(),b=randomUUID(),x=randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner')",[actor]);
    await c.query("insert into client_status(slug,label) values('active','Active'); insert into deal_phase(slug,label,sort) values('research','Research',1)");
    await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic client',$2,$2)",[party,actor]);
    await c.query("insert into client(id,party_id,status,created_by,updated_by) values($1,$2,'active',$3,$3)",[client,party,actor]);
    await c.query("insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by) values($1,$2,'Synthetic deal','lease','research',$3,$3)",[deal,client,actor]);
    await c.query("select ops.register_client_portal($1,'synthetic','test')",[deal]);
    await c.query("insert into ops.tour values($1,'carr-internal',$2)",[tour,deal]);
    for (const id of [a,b,x]) await c.query("insert into ops.tour_property values($1,'carr-internal','active')",[id]);
    await c.query(`grant usage on schema ops to carr_writer; grant select on deal,client,party,actor to carr_writer;
      grant select on ops.tour_selection_cart_version to carr_writer;
      grant execute on function ops.append_tour_selection_cart_version(text,uuid,uuid,jsonb,integer,text) to carr_writer;
      set session authorization carr_writer; select set_config('carr.acting_actor_slug','joe',false)`);
    const before=(await c.query("select ops.append_tour_selection_cart_version('carr-internal',$1,null,$2,0,$3) id",[tour,JSON.stringify([a]),'sha256:'+'a'.repeat(64)])).rows[0].id;
    let revision=1,items=[{carr_property_id:b}];
    const requests=[],events=[];
    const tools=clientTourProposalTools({withEnvelope:async (_c,_a,_n,_args,fn)=>fn(),writeEvent:async (...args)=>events.push(args),fetchFn:async (url,init)=>{
      requests.push({url,init}); return Response.json({schema_version:'client-tour-selection.v1',portal_slug:'synthetic',revision,items});
    }});
    const who={slug:'joe',human:true};
    const invoke=async (verb,args,actor=who)=>{
      await c.query(verb==='list-deal-tour-proposals'?'begin read only':'begin');
      try {const result=await tools[verb].handler(c,actor,args); await c.query('commit'); return result;}
      catch (error) {await c.query('rollback'); throw error;}
    };
    const pull=()=>invoke('pull-client-tour-proposal',{deal_id:deal,tour_id:tour,idempotency_key:randomUUID()});
    const list=()=>invoke('list-deal-tour-proposals',{deal_id:deal});
    const cart=async ()=>(await c.query('select * from ops.tour_selection_cart_version order by selection_version desc limit 1')).rows[0];
    const snapshot = async () => {
      await c.query('reset session authorization');
      const rows = [];
      for (const table of ['client_tour_proposal', 'client_tour_proposal_receipt', 'tour_selection_cart_version']) {
        rows.push((await c.query(`select coalesce(jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text),'[]') data from ops.${table} t`)).rows[0].data);
      }
      await c.query('set session authorization carr_writer');
      return rows;
    };
    const summary = async (sponsor='joe') => {
      const before = await snapshot();
      await c.query('reset session authorization; set session authorization carr_reader; begin read only');
      let result;
      try {
        result = await pendingClientTourProposals(c, sponsor);
        await c.query('commit');
      } catch (error) { await c.query('rollback'); throw error; }
      finally { await c.query('reset session authorization; set session authorization carr_writer'); }
      assert.deepEqual(await snapshot(), before, 'summary reads change no proposal, receipt or cart rows');
      return result;
    };
    assert.equal((await summary()).state,'empty');
    const first=await pull();
    assert.equal((await cart()).id,before);
    assert.equal((await summary()).items[0].properties_changed,2);
    assert.equal((await summary('dell')).items.length,0);
    assert.equal((await summary('portal')).items.length,0);
    assert.equal((await list()).pending_count,1);
    assert.deepEqual((await list()).proposals[0].difference,{add:[b],remove:[a]});
    assert.equal((await pull()).proposal.id,first.proposal.id);
    assert.equal((await cart()).id,before);
    assert.equal(new URL(requests[0].url).pathname,'/synthetic/api/selection');
    assert.equal(requests[0].init.method,'GET');
    items=[{carr_property_id:randomUUID()}]; revision=2;
    await assert.rejects(pull(),e=>e.payload?.error==='proposal_unresolved_property'||/proposal_unresolved_property/.test(e.message));
    assert.equal((await list()).pending_count,1);
    items=[{carr_property_id:b},{carr_property_id:x}];
    const second=await pull();
    assert.equal((await list()).proposals.find(p=>p.id===first.proposal.id).state,'superseded');
    const args={deal_id:deal,proposal_id:second.proposal.id,expected_selection_version_id:before,mode:'per_property',choices:{[b]:true},idempotency_key:randomUUID()};
    const accept=(value=args,actor=who)=>invoke('accept-client-tour-proposal',value,actor);
    for (const actor of [{slug:'joe',human:false},{slug:'portal',human:false},{slug:'outsider',human:true}]) await assert.rejects(accept(args,actor));
    assert.equal((await cart()).id,before);
    const partial=await accept();
    assert.equal(partial.receipt.state,'partially_accepted');
    assert.equal((await summary()).items[0].properties_changed,2);
    assert.deepEqual(new Set((await cart()).property_ids),new Set([a,b]));
    const remoteReads=requests.length;
    await assert.rejects(invoke('pull-client-tour-proposal',{deal_id:randomUUID(),tour_id:tour,idempotency_key:randomUUID()}));
    await assert.rejects(invoke('pull-client-tour-proposal',{deal_id:deal,tour_id:randomUUID(),idempotency_key:randomUUID()}));
    assert.equal(requests.length,remoteReads);
    assert.deepEqual(await accept(),partial);
    assert.equal((await cart()).selection_version,2);
    await assert.rejects(accept({...args,choices:{[x]:true}}));
    await assert.rejects(accept({...args,idempotency_key:randomUUID()}));
    const full=await accept({...args,idempotency_key:randomUUID(),mode:'use_theirs',choices:undefined,expected_selection_version_id:(await cart()).id});
    assert.equal(full.receipt.state,'accepted');
    assert.deepEqual(new Set((await cart()).property_ids),new Set([b,x]));
    assert.equal((await list()).pending_count,0);
    assert.equal((await summary()).state,'empty');
    revision=3; items=[]; const third=await pull();
    await invoke('dismiss-client-tour-proposal',{deal_id:deal,proposal_id:third.proposal.id,idempotency_key:randomUUID()});
    assert.equal((await cart()).selection_version,3);
    revision=4; items=[{carr_property_id:a}]; const fourth=await pull();
    revision=5; items=[{carr_property_id:b}]; await pull();
    await assert.rejects(accept({...args,proposal_id:fourth.proposal.id,idempotency_key:randomUUID(),expected_selection_version_id:(await cart()).id}));
    assert.equal((await cart()).selection_version,3);
    revision=4; await assert.rejects(pull());
    revision=6;items=[{carr_property_id:a}];const sixth=await pull();
    await accept({...args,proposal_id:sixth.proposal.id,idempotency_key:randomUUID(),choices:{[a]:true},expected_selection_version_id:(await cart()).id});
    revision=7;items=[{carr_property_id:b}];const seventh=await pull();
    assert.equal((await list()).proposals.find(p=>p.id===sixth.proposal.id).state,'superseded');
    await accept({...args,proposal_id:seventh.proposal.id,idempotency_key:randomUUID(),choices:{[a]:false},expected_selection_version_id:(await cart()).id});
    await invoke('dismiss-client-tour-proposal',{deal_id:deal,proposal_id:seventh.proposal.id,idempotency_key:randomUUID()});
    assert.equal((await list()).proposals.find(p=>p.id===seventh.proposal.id).state,'dismissed');
    items=[{carr_property_id:x}];await assert.rejects(pull());
    revision=8;const eighth=await pull();
    const other=new pg.Client(c.connectionParameters);
    await other.connect();
    try {
      await other.query("set session authorization carr_writer; select set_config('carr.acting_actor_slug','joe',false)");
      const expected=(await cart()).id;
      const race=async connection=>{
        await connection.query('begin');
        try {
          const result=await tools['accept-client-tour-proposal'].handler(connection,who,{deal_id:deal,proposal_id:eighth.proposal.id,idempotency_key:randomUUID(),mode:'use_theirs',expected_selection_version_id:expected});
          await connection.query('commit');return result;
        } catch(error) {await connection.query('rollback');throw error;}
      };
      const outcomes=await Promise.allSettled([race(c),race(other)]);
      assert.equal(outcomes.filter(o=>o.status==='fulfilled').length,1);
      assert.deepEqual((await cart()).property_ids,[x]);
    } finally {await other.end();}
    revision=9;items=[{carr_property_id:a},{carr_property_id:b}];const ninth=await pull();
    for (const [id,selected,state] of [[a,true,'partially_accepted'],[b,true,'partially_accepted'],[x,false,'accepted']]) {
      const response=await accept({...args,proposal_id:ninth.proposal.id,idempotency_key:randomUUID(),choices:{[id]:selected},expected_selection_version_id:(await cart()).id});
      assert.equal(response.receipt.state,state);
    }
    assert.deepEqual(new Set((await cart()).property_ids),new Set([a,b]));
    await c.query('reset session authorization; set role carr_reader');
    await assert.rejects(c.query('select * from ops.client_tour_proposal'));
    await c.query('reset role; set role dot_reader');
    await assert.rejects(c.query('select * from ops.client_tour_proposal'));
    await c.query('reset role');
    await c.query(`insert into ops.client_tour_proposal(portal_slug,deal_id,tour_id,portal_revision,property_ids,base_property_ids)
      select 'synthetic',$1,$2,100+n,'[]','[]' from generate_series(1,1000-(select count(*)::integer from ops.client_tour_proposal)) n`,[deal,tour]);
    const bounded=await list();
    assert.equal(bounded.proposals.length,50);
    assert(bounded.pending_count>50);
    revision=9999;await assert.rejects(pull(),e=>e.payload?.error==='proposal_capacity');
    await c.query("update ops.client_portal set disabled_at=now() where slug='synthetic'");
    await assert.rejects(pull());
    assert.equal((await summary()).state,'empty','disabled portals do not produce brief lines');
    await c.query("reset session authorization; update ops.client_portal set disabled_at=null where slug='synthetic'");
    await c.query("update deal set owner='dell' where id=$1",[deal]);
    assert.equal((await summary()).state,'empty','former owner cannot see proposals');
    assert.equal((await summary('dell')).items.length,1,'new owner sees the deal');
    await c.query('reset session authorization');
    for (let i=0;i<22;i++) {
      const id=randomUUID(),tid=randomUUID(),slug=`cap-${i}`;
      await c.query("insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by,owner) values($1,$2,$3,'lease','research',$4,$4,'joe')",[id,client,`Cap ${i.toString().padStart(2,'0')}`,actor]);
      await c.query("select ops.register_client_portal($1,$2,'test')",[id,slug]);
      await c.query("insert into ops.tour values($1,'carr-internal',$2)",[tid,id]);
      await c.query("insert into ops.client_tour_proposal(portal_slug,deal_id,tour_id,portal_revision,property_ids,base_property_ids) values($1,$2,$3,1,'[]','[]')",[slug,id,tid]);
    }
    await c.query('set session authorization carr_reader; begin read only');
    assert.equal((await c.query("select * from ops.pending_client_tour_proposals('joe')")).rows.length,21,'database source is bounded before formatting');
    await c.query('commit; reset session authorization');
    const capped=await summary();
    assert.equal(capped.items.length,20);
    assert.equal(capped.truncated,true);
    assert.equal(capped.state,'partial');
    assert.equal(capped.items[0].deal_name,'Cap 00');
    assert.equal(capped.items[19].deal_name,'Cap 19');
    assert.equal(capped.items[0].properties_changed,0);
    await c.query('reset session authorization');
    const targetClient=randomUUID();
    await c.query("insert into client(id,party_id,status,created_by,updated_by) values($1,$2,'active',$3,$3)",[targetClient,party,actor]);
    await c.query('update client set merged_into=$1 where id=$2',[targetClient,client]);
    assert.equal((await summary()).state,'empty','merged client tombstones stay out of counts');
    assert(events.length>0);
  } finally {await db.close();}
});
