import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { randomUUID } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { networkStatement, projectNetwork } from '../src/relationship-network.js';
import { readBusinessRecord } from '../src/workspace-business-read.js';

function loopback(t,flag){
  const dsn=process.env.DATABASE_URL;
  if(!dsn){assert.notEqual(process.env[flag],'1');t.skip('disposable PostgreSQL required');return null;}
  const url=new URL(dsn),db=new pg.Client({connectionString:dsn});
  assert.ok(['postgres:','postgresql:'].includes(url.protocol)&&['localhost','127.0.0.1'].includes(url.hostname)&&url.port&&!url.search&&!url.hash&&db.connectionParameters.host===url.hostname&&db.connectionParameters.port===Number(url.port),'loopback database with explicit port required');
  return db;
}
// Synthetic fixtures. `prefix` pins a UUID's sort position or forces hex letters.
async function fixtures(db){
  const actor={...(await db.query("select id,slug from public.actor where slug='joe'")).rows[0],human:true};
  const id=(prefix='')=>prefix+randomUUID().slice(prefix.length);
  const party=async(label,pid=id())=>(await db.query("insert into public.party(id,kind,name,city,created_by,updated_by) values ($1,'org',$2,'Demo North',$3,$3) returning id",[pid,'Demo '+label+' '+randomUUID(),actor.id])).rows[0].id;
  const client=async p=>(await db.query("insert into public.client(party_id,roster_ref,created_by,updated_by,vertical,owner_id) values($1,$2,$3,$3,'dental',$3) returning id",[p,'C-DEMO-'+randomUUID(),actor.id])).rows[0].id;
  const deal=async(c,outcome)=>(await db.query("insert into public.deal(client_id,name,deal_type,phase,outcome,created_by,updated_by) values($1,$2,'lease',$3,$4,$5,$5) returning id",[c,'Demo deal '+randomUUID(),outcome?'closed':'site_selection',outcome,actor.id])).rows[0].id;
  const vendor=async(p,{vid=id(),disposition='active',evidence=[]}={})=>(await db.query("insert into public.vendor(id,party_id,vendor_ref,category,territory,owner_id,disposition,deal_evidence,created_by,updated_by) values($1,$2,$3,'banker','Demo North',$4,$5,$6,$4,$4) returning id",[vid,p,'V-DEMO-'+randomUUID(),actor.id,disposition,JSON.stringify(evidence)])).rows[0].id;
  const lead=async(p,{lid=id(),suppressed=false}={})=>(await db.query("insert into public.lead(id,party_id,stage,suppressed,created_by,updated_by) values($1,$2,(select slug from public.lead_stage where slug<>'do_not_contact' order by slug limit 1),$3,$4,$4) returning id",[lid,p,suppressed,actor.id])).rows[0].id;
  return {actor,id,party,client,deal,vendor,lead};
}
const link=async(db,actor,args)=>{await db.query('savepoint action');await db.query('set local role carr_writer');try{const r=await TOOLS['link-parties'].handler(db,actor,{idempotency_key:randomUUID(),...args});await db.query('release savepoint action');return r;}catch(e){await db.query('rollback to savepoint action');throw e;}finally{await db.query('reset role');}};
const network=async db=>{await db.query('savepoint read');await db.query('set local role carr_reader');try{return projectNetwork((await db.query(networkStatement)).rows[0].snapshot,new Date().toISOString());}finally{await db.query('rollback to savepoint read');await db.query('reset role');}};
const referredDeals=(snapshot,party)=>snapshot.edges.filter(e=>e.kind==='referred'&&e.from==='party:'+party&&e.to.startsWith('deal:')).map(e=>e.to).sort();

test('W14 PostgreSQL: coverage and exact attachments serialize in both commit orders', async t => {
  const setup=loopback(t,'CARR_RELATIONSHIP_DB_REQUIRED');if(!setup)return;
  const attachment=new pg.Client({connectionString:process.env.DATABASE_URL}),verification=new pg.Client({connectionString:process.env.DATABASE_URL});
  await setup.connect();await attachment.connect();await verification.connect();
  try {
    const f=await fixtures(setup);
    const directory=async vid=>(await readBusinessRecord({client:setup,actor:f.actor,dataset:'vendors',id:vid,contract:'vendor-directory.v1',correlationId:'coverage-race'})).record.relationship;
    const pending=(conn,work)=>{const state={done:false,conn};state.result=work().then(ok=>({ok}),error=>({error})).finally(()=>{state.done=true;});return state;};
    const parked=async state=>{
      const pid=(await setup.query('select pid from pg_stat_activity where pid=$1',[state.conn.processID])).rows[0].pid;
      for(let i=0;i<400;i++){
        if(state.done)return 'done';
        if((await setup.query('select wait_event_type from pg_stat_activity where pid=$1',[pid])).rows[0]?.wait_event_type==='Lock')return 'waiting';
        await new Promise(r=>setTimeout(r,25));
      }
      throw new Error('coverage caller neither finished nor waited');
    };
    for(const order of ['verification first','attachment first'])await t.test(order,async()=>{
      const vp=await f.party('coverage vendor'),source=await f.party('coverage source'),target=await f.party('coverage target');
      const vid=await f.vendor(vp),secondVid=await f.vendor(vp),deal=await f.deal(await f.client(target),'won');
      const args={from_party:source,to_party:target,via_party:vp,kind:'referred',deal_id:deal,note:'Demo coverage ordering',idempotency_key:randomUUID()};
      const verifyArgs={vendor:vid,base_version:(await setup.query('select version from public.vendor where id=$1',[vid])).rows[0].version,fields:{verify_deal_history:true},idempotency_key:randomUUID()};
      await attachment.query('begin');await attachment.query('select transaction_timestamp()');
      await setup.query('update public.vendor set deal_history_verified_at=clock_timestamp() where id=$1',[secondVid]);
      await attachment.query('set local role carr_writer');
      await verification.query('begin');await verification.query('set local role carr_writer');
      let release,reached;
      const gate=new Promise(r=>release=r),atGate=new Promise(r=>reached=r);
      const leader=order==='verification first'?verification:attachment;
      const gated={query:async(sql,values)=>{
        const result=await leader.query(sql,values);
        if(order==='verification first'?sql==='select version from vendor where id=$1 for update':sql.includes('insert into public.party_link_deal')){reached();await gate;}
        return result;
      }};
      let first,second;
      try {
        first=pending(leader,()=>order==='verification first'?TOOLS['update-vendor'].handler(gated,f.actor,verifyArgs):TOOLS['link-parties'].handler(gated,f.actor,args));
        await Promise.race([atGate,first.result.then(r=>{throw r.error||new Error('caller finished without reaching ordering gate');})]);
        second=pending(order==='verification first'?attachment:verification,()=>order==='verification first'?TOOLS['link-parties'].handler(attachment,f.actor,args):TOOLS['update-vendor'].handler(verification,f.actor,verifyArgs));
        const secondState=await parked(second);
        release();
        const firstResult=await first.result;await leader.query(firstResult.ok?'commit':'rollback');
        const secondResult=await second.result;await second.conn.query(secondResult.ok?'commit':'rollback');
        assert.ok(firstResult.ok,firstResult.error?.message);
        assert.equal((await directory(vid)).coverage_verified_at,null);
        assert.equal((await directory(vid)).deals_referred,null);
        assert.equal(secondState,'waiting','verification and attachment must share a row lock');
        if(order==='verification first')assert.ok(secondResult.ok,secondResult.error?.message);
        else assert.equal(secondResult.error?.payload?.error,'version_conflict','an attestation of the old set is refused');
        assert.ok((await setup.query('select deal_history_verified_at from public.vendor where party_id=$1',[vp])).rows.every(r=>r.deal_history_verified_at===null),'every live role row loses coverage');
        assert.equal((await setup.query('select count(*)::int n from public.party_link_deal where deal_id=$1',[deal])).rows[0].n,1);
        // Fresh attestations cover the new set. A duplicate attachment must not
        // invalidate it, and rollback must restore coverage with the association.
        for(const id of [vid,secondVid]){
          await setup.query('begin');
          try {
            await TOOLS['update-vendor'].handler(setup,f.actor,{...verifyArgs,vendor:id,base_version:(await setup.query('select version from public.vendor where id=$1',[id])).rows[0].version,idempotency_key:randomUUID()});
            await setup.query('commit');
          } catch(e){await setup.query('rollback');throw e;}
        }
        assert.equal((await directory(vid)).deals_referred,1);
        await setup.query('begin');await link(setup,f.actor,{...args,idempotency_key:randomUUID()});await setup.query('commit');
        assert.equal((await directory(vid)).deals_referred,1,'duplicate keeps coverage');
        const other=await f.deal((await setup.query('select client_id from public.deal where id=$1',[deal])).rows[0].client_id,'won');
        await setup.query('begin');await link(setup,f.actor,{...args,deal_id:other,idempotency_key:randomUUID()});await setup.query('rollback');
        assert.equal((await directory(vid)).deals_referred,1,'rolled back attachment keeps coverage');
      } finally {
        release();
        await first?.result;await leader.query('rollback');
        await second?.result;await (leader===attachment?verification:attachment).query('rollback');
      }
    });
  } finally {await attachment.end();await verification.end();await setup.end();}
});

test('W14 PostgreSQL: a vendor role created after an empty lookup waits for the attachment', async t => {
  const setup=loopback(t,'CARR_RELATIONSHIP_DB_REQUIRED');if(!setup)return;
  const attachment=new pg.Client({connectionString:process.env.DATABASE_URL}),creation=new pg.Client({connectionString:process.env.DATABASE_URL});
  await setup.connect();await attachment.connect();await creation.connect();
  let release=()=>{},attached,created;
  try {
    const f=await fixtures(setup);
    const vp=await f.party('future vendor'),source=await f.party('empty source'),target=await f.party('empty target');
    const deal=await f.deal(await f.client(target),'won');
    // Pause the attachment after its vendor lookup finds no role row.
    let reached;const gate=new Promise(r=>release=r),atLookup=new Promise(r=>reached=r);
    const gated={query:async(sql,values)=>{const r=await attachment.query(sql,values);if(sql.includes('from public.vendor where party_id=$1')){reached();await gate;}return r;}};
    await attachment.query('begin');await attachment.query('set local role carr_writer');
    attached=TOOLS['link-parties'].handler(gated,f.actor,{from_party:source,to_party:target,via_party:vp,kind:'referred',deal_id:deal,note:'Demo empty-row ordering',idempotency_key:randomUUID()}).then(ok=>({ok}),error=>({error}));
    await Promise.race([atLookup,attached.then(r=>{throw r.error||new Error('attachment finished without reaching its vendor lookup');})]);
    await creation.query('begin');
    const state={done:false};
    created=creation.query("insert into public.vendor(party_id,vendor_ref,category,territory,owner_id,created_by,updated_by) values($1,$2,'banker','Demo North',$3,$3,$3) returning id",[vp,'V-DEMO-'+randomUUID(),f.actor.id]).then(r=>r.rows[0].id).finally(()=>{state.done=true;});
    let waited=false;
    for(let i=0;i<400&&!state.done;i++){
      if((await setup.query('select wait_event_type from pg_stat_activity where pid=$1',[creation.processID])).rows[0]?.wait_event_type==='Lock'){waited=true;break;}
      await new Promise(r=>setTimeout(r,25));
    }
    release();
    const result=await attached;await attachment.query(result.ok?'commit':'rollback');
    assert.ok(result.ok,result.error?.message);
    const vid=await created;await creation.query('commit');
    assert.ok(waited,'a new vendor role must wait for an uncommitted attachment on its party');
    // A verification after both commits attests the set that includes the deal.
    await setup.query('begin');
    await TOOLS['update-vendor'].handler(setup,f.actor,{vendor:vid,base_version:(await setup.query('select version from public.vendor where id=$1',[vid])).rows[0].version,fields:{verify_deal_history:true},idempotency_key:randomUUID()});
    await setup.query('commit');
    const relationship=(await readBusinessRecord({client:setup,actor:f.actor,dataset:'vendors',id:vid,contract:'vendor-directory.v1',correlationId:'empty-row-race'})).record.relationship;
    assert.equal(relationship.deals_referred,1);
  } finally {
    release();await attached;await attachment.query('rollback');await created?.catch(()=>{});await creation.query('rollback');
    await attachment.end();await creation.end();await setup.end();
  }
});

test('W14 PostgreSQL: a concurrent attachment never credits a deal to the other caller\'s broker', async t => {
  const setup=loopback(t,'CARR_RELATIONSHIP_DB_REQUIRED');if(!setup)return;
  const first=new pg.Client({connectionString:process.env.DATABASE_URL}),second=new pg.Client({connectionString:process.env.DATABASE_URL});
  await setup.connect();await first.connect();await second.connect();
  try {
    // Committed fixture: both transactions must see the same existing edge.
    const f=await fixtures(setup);
    const source=await f.party('race source'),target=await f.party('race target'),brokerOne=await f.party('race broker one'),brokerTwo=await f.party('race broker two');
    const tc=await f.client(target),dealOne=await f.deal(tc,'won'),dealTwo=await f.deal(tc,'won');
    await setup.query("insert into public.party_link(from_party,to_party,kind,note,source,created_by) values($1,$2,'referred','Demo existing referral','stated',$3)",[source,target,f.actor.id]);
    // Pause the first caller immediately after its existing-edge read, then let
    // the second run as far as it can before releasing the first.
    let release,reached;const gate=new Promise(r=>release=r),atRead=new Promise(r=>reached=r);
    const gated={query:async(text,values)=>{const r=await first.query(text,values);if(/from party_link where from_party=\$1 and to_party=\$2 and kind=\$3/.test(text)){reached();await gate;}return r;}};
    const attach=async(db,deal,via)=>{await db.query('begin');await db.query('set local role carr_writer');return TOOLS['link-parties'].handler(db,f.actor,{idempotency_key:randomUUID(),from_party:source,to_party:target,via_party:via,kind:'referred',deal_id:deal,note:'Demo race entry'});};
    const run=(db,conn,deal,via)=>{const s={done:false};s.result=attach(db,deal,via).then(ok=>({ok}),error=>({error})).finally(()=>{s.done=true;});s.conn=conn;return s;};
    const pid=async conn=>(await conn.query('select pg_backend_pid() pid')).rows[0].pid;
    const pids=new Map([[first,await pid(first)],[second,await pid(second)]]);
    // Settled, or blocked on a lock held by the other transaction.
    const parked=async s=>{for(let i=0;i<400;i++){if(s.done)return 'done';const w=await setup.query('select wait_event_type from pg_stat_activity where pid=$1',[pids.get(s.conn)]);if(w.rows[0]?.wait_event_type==='Lock')return 'waiting';await new Promise(r=>setTimeout(r,25));}throw new Error('caller neither finished nor waited');};
    const a=run(gated,first,dealOne,brokerOne);await atRead;
    const b=run(second,second,dealTwo,brokerTwo);await parked(b);
    release();
    const outcomes=new Map();
    for(const s of [a,b]) if(await parked(s)==='done'){outcomes.set(s,await s.result);await s.conn.query(outcomes.get(s).ok?'commit':'rollback');}
    for(const s of [a,b]) if(!outcomes.has(s)){outcomes.set(s,await s.result);await s.conn.query(outcomes.get(s).ok?'commit':'rollback');}
    const refused=[...outcomes.values()].filter(o=>o.error);
    assert.equal(refused.length,1,'exactly one of two different brokers may be accepted');
    assert.equal(refused[0].error.payload?.error,'referral_broker_mismatch');
    const accepted=outcomes.get(a).ok?{deal_id:dealOne,referred_by:brokerOne}:{deal_id:dealTwo,referred_by:brokerTwo};
    const rows=(await setup.query('select r.deal_id,r.referred_by from public.party_link_deal r join public.party_link l on l.id=r.link_id where l.from_party=$1',[source])).rows;
    assert.deepEqual(rows,[accepted]);
    assert.equal((await setup.query("select via_party from public.party_link where from_party=$1 and kind='referred'",[source])).rows[0].via_party,accepted.referred_by);
    const audit=(await setup.query("select e.new_value from public.event e where e.verb='link-parties' and e.new_value->>'deal_id'=$1",[accepted.deal_id])).rows;
    assert.equal(audit.length,1);assert.equal(audit[0].new_value.referred_by,accepted.referred_by);
  } finally {await first.end();await second.end();await setup.end();}
});

test('W14 PostgreSQL: per-deal attribution, every role row, and one association rule for both reads', async t => {
  const db=loopback(t,'CARR_RELATIONSHIP_DB_REQUIRED');if(!db)return;
  await db.connect();await db.query('begin');
  try {
    const f=await fixtures(db),{actor}=f;
    const asOf=()=>new Date('2026-10-04T12:00:00Z');
    const directory=async vid=>(await readBusinessRecord({client:db,actor,dataset:'vendors',id:vid,contract:'vendor-directory.v1',correlationId:'w14-review',now:asOf})).record.relationship;

    await t.test('a later broker backfill never rewrites an earlier exact deal',async()=>{
      const source=await f.party('direct source'),target=await f.party('direct target'),broker=await f.party('late broker');
      const tc=await f.client(target),d1=await f.deal(tc,'won'),d2=await f.deal(tc,'lost');
      await link(db,actor,{from_party:source,to_party:target,kind:'referred',deal_id:d1,note:'Demo direct referral',occurred_on:'2026-01-05'});
      await link(db,actor,{from_party:source,to_party:target,via_party:broker,kind:'referred',deal_id:d2,note:'Demo brokered referral',occurred_on:'2026-03-09'});
      const s=await network(db);
      assert.deepEqual(referredDeals(s,source),['deal:'+d1]);
      assert.deepEqual(referredDeals(s,broker),['deal:'+d2]);
      assert.equal(new Date(s.edges.find(e=>e.kind==='referred'&&e.to==='deal:'+d1).when).toISOString(),'2026-01-05T00:00:00.000Z');
    });

    await t.test('an alternate spelling of the stored broker UUID is the same broker',async()=>{
      const source=await f.party('case source'),target=await f.party('case target'),broker=await f.party('case broker',f.id('abcdef'));
      const tc=await f.client(target),d1=await f.deal(tc,'won'),d2=await f.deal(tc,'won');
      await link(db,actor,{from_party:source,to_party:target,via_party:broker,kind:'referred',deal_id:d1,note:'Demo first'});
      await link(db,actor,{from_party:source,to_party:target,via_party:broker.toUpperCase(),kind:'referred',deal_id:d2,note:'Demo second'});
      assert.deepEqual(referredDeals(await network(db),broker),['deal:'+d1,'deal:'+d2].sort());
    });

    await t.test('a restriction on any role row holds the party',async()=>{
      const subject=await f.party('lead restricted'),peer=await f.party('lead peer');
      await f.lead(subject,{lid:f.id('00000000')});await f.lead(subject,{lid:f.id('ffffffff'),suppressed:true});
      const avoided=await f.party('vendor avoided');
      await f.vendor(avoided,{vid:f.id('00000000')});await f.vendor(avoided,{vid:f.id('ffffffff'),disposition:'avoid'});
      const offerA=await link(db,actor,{from_party:peer,to_party:subject,kind:'can_introduce',note:'Demo offer to a suppressed lead'});
      const offerB=await link(db,actor,{from_party:peer,to_party:avoided,kind:'can_introduce',note:'Demo offer to an avoided vendor'});
      const s=await network(db);
      assert.equal(s.nodes.find(n=>n.id==='party:'+subject).restricted,true);
      assert.equal(s.nodes.find(n=>n.id==='party:'+avoided).restricted,true);
      assert.ok(!s.suggestions.some(o=>[offerA.link_id,offerB.link_id].map(x=>'link:'+x).includes(o.id)));
    });

    await t.test('evidence on every live vendor row reaches the snapshot',async()=>{
      const vp=await f.party('two vendor rows'),tc=await f.client(await f.party('two rows client')),won=await f.deal(tc,'won');
      await f.vendor(vp,{vid:f.id('00000000')});
      await f.vendor(vp,{vid:f.id('ffffffff'),evidence:[{deal_id:won,role:'referred',occurred_at:'2026-02-01T00:00:00Z',evidence_kind:'entry',evidence_ref:'Demo second row'}]});
      const s=await network(db);
      assert.deepEqual(referredDeals(s,vp),['deal:'+won]);
      assert.deepEqual(s.referrals.find(r=>r.node_id==='party:'+vp),{node_id:'party:'+vp,deals:1,won:1,lost:0,win_rate:1});
    });

    await t.test('network and directory count the same exact referral',async()=>{
      const vp=await f.party('consistent vendor'),target=await f.party('consistent target'),source=await f.party('consistent source');
      const vid=await f.vendor(vp),won=await f.deal(await f.client(target),'won');
      await db.query('update public.vendor set deal_history_verified_at=$2 where id=$1',[vid,'2026-09-01T00:00:00Z']);
      assert.equal((await directory(vid)).deals_referred,0);
      await link(db,actor,{from_party:source,to_party:target,via_party:vp,kind:'referred',deal_id:won,note:'Demo vendor sent this practice'});
      assert.equal(referredDeals(await network(db),vp).length,1);
      assert.equal((await directory(vid)).deals_referred,null,'a new exact association invalidates verified coverage');
      await db.query('update public.vendor set deal_history_verified_at=now()+interval \'1 second\' where id=$1',[vid]);
      assert.equal((await directory(vid)).deals_referred,1);
    });

    await t.test('directory introductions exclude a deleted or merged broker',async()=>{
      for(const tombstone of ['deleted_at=now()','merged_into=$2']){
        const vp=await f.party('intro vendor'),other=await f.party('intro other'),broker=await f.party('intro broker'),survivor=await f.party('intro survivor');
        const vid=await f.vendor(vp);
        await link(db,actor,{from_party:vp,to_party:other,via_party:broker,kind:'can_introduce',note:'Demo brokered offer'});
        assert.equal((await directory(vid)).introductions.length,1);
        await db.query(`update public.party set ${tombstone} where id=$1`,tombstone.includes('$2')?[broker,survivor]:[broker]);
        assert.deepEqual((await directory(vid)).introductions,[],tombstone);
      }
    });
  } finally {await db.query('rollback');await db.end();}
});

test('W14 PostgreSQL: exact referrals, deduplication, broker attribution, holds and reader grants', async t => {
  const dsn=process.env.DATABASE_URL;
  if(!dsn){assert.notEqual(process.env.CARR_RELATIONSHIP_DB_REQUIRED,'1');t.skip('disposable PostgreSQL required');return;}
  const url=new URL(dsn),db=new pg.Client({connectionString:dsn});
  assert.ok(['postgres:','postgresql:'].includes(url.protocol)&&['localhost','127.0.0.1'].includes(url.hostname)&&url.port&&!url.search&&!url.hash&&db.connectionParameters.host===url.hostname&&db.connectionParameters.port===Number(url.port),'loopback database with explicit port required');
  await db.connect();await db.query('begin');
  try {
    const actor={...(await db.query("select id,slug from public.actor where slug='joe'")).rows[0],human:true};
    const suffix=randomUUID();
    const party=async label=>(await db.query("insert into public.party(kind,name,city,created_by,updated_by) values ('org',$1,'Demo North',$2,$2) returning id",['Demo '+label+' '+suffix,actor.id])).rows[0].id;
    const vp=await party('vendor'),cp=await party('client'),target=await party('target'),broker=await party('broker');
    const client=async p=>(await db.query("insert into public.client(party_id,roster_ref,created_by,updated_by,vertical,owner_id) values($1,$2,$3,$3,'dental',$3) returning id",[p,'C-DEMO-'+randomUUID(),actor.id])).rows[0].id;
    const ci=await client(cp),ti=await client(target);
    const vendor=(await db.query("insert into public.vendor(party_id,vendor_ref,category,territory,verticals,owner_id,created_by,updated_by) values($1,$2,'banker','Demo North',array['dental'],$3,$3,$3) returning id",[vp,'V-DEMO-'+suffix,actor.id])).rows[0].id;
    const deal=async(c,outcome)=>(await db.query("insert into public.deal(client_id,name,deal_type,phase,outcome,created_by,updated_by) values($1,$2,'lease',$3,$4,$5,$5) returning id",[c,'Demo deal '+randomUUID(),outcome?'closed':'site_selection',outcome,actor.id])).rows[0].id;
    const won=await deal(ti,'won'),lost=await deal(ti,'lost'),open=await deal(ti,null),unattributed=await deal(ti,'won'),wrong=await deal(ci,'won');
    const call=async args=>{await db.query('savepoint action');try{const r=await TOOLS['link-parties'].handler(db,actor,args);await db.query('release savepoint action');return r;}catch(e){await db.query('rollback to savepoint action');throw e;}};
    const base={from_party:broker,to_party:target,via_party:cp,kind:'referred',note:'Demo exact referral entry',occurred_on:'2026-10-01'};
    const first={...base,deal_id:won,idempotency_key:randomUUID()};
    await db.query('set local role carr_writer');
    const result=await call(first);assert.equal((await call(first)).replayed,true);
    await call({...base,deal_id:lost,idempotency_key:randomUUID()});await call({...base,deal_id:open,idempotency_key:randomUUID()});
    await call({...base,deal_id:won,idempotency_key:randomUUID()});
    await assert.rejects(call({...base,deal_id:wrong,idempotency_key:randomUUID()}),e=>e.payload?.error==='referral_deal_target_mismatch');
    await assert.rejects(call({...base,via_party:vp,deal_id:won,idempotency_key:randomUUID()}),e=>e.payload?.error==='referral_broker_mismatch');
    await db.query('reset role');
    assert.equal((await db.query('select count(*)::int n from public.party_link_deal where link_id=$1',[result.link_id])).rows[0].n,3);
    await db.query('update public.vendor set deal_evidence=$2 where id=$1',[vendor,JSON.stringify([{deal_id:won,role:'worked',occurred_at:'2026-10-01T00:00:00Z',evidence_ref:'Demo original work entry'},{deal_id:won,role:'referred',occurred_at:'2026-10-01T00:00:00Z',evidence_ref:'Demo vendor referral'}])]);
    const offer=await call({from_party:vp,to_party:target,kind:'can_introduce',note:'Demo offer to connect',idempotency_key:randomUUID()});
    const read=async()=>{await db.query('savepoint read');await db.query('set local role carr_reader');try{return projectNetwork((await db.query(networkStatement)).rows[0].snapshot,new Date().toISOString());}catch(e){await db.query('rollback to savepoint read');throw e;}finally{await db.query('reset role');}};
    let network=await read();
    const row=network.referrals.find(r=>r.node_id==='party:'+cp);assert.deepEqual(row,{node_id:'party:'+cp,deals:3,won:1,lost:1,win_rate:.5});
    assert.equal(network.referrals.find(r=>r.node_id==='party:'+vp).deals,1);
    assert.ok(network.edges.some(e=>e.from==='party:'+vp&&e.to==='deal:'+won&&e.kind==='worked'));
    assert.ok(!network.edges.some(e=>e.kind==='referred'&&e.to==='deal:'+unattributed));
    assert.equal(network.nodes.find(n=>n.id==='party:'+vp).owner,'joe');
    assert.ok(network.suggestions.some(s=>s.id==='link:'+offer.link_id&&s.reason==='Demo offer to connect'));
    await db.query("update public.party set contact_state='paused' where id=$1",[target]);network=await read();assert.ok(!network.suggestions.some(s=>s.id==='link:'+offer.link_id));
    await db.query('update public.party set merged_into=$2 where id=$1',[vp,broker]);network=await read();assert.ok(!network.nodes.some(n=>n.id==='party:'+vp));assert.ok(network.edges.every(e=>network.nodes.some(n=>n.id===e.from)&&network.nodes.some(n=>n.id===e.to)));
    await db.query('set local role carr_reader');await assert.rejects(db.query('insert into public.party_link_deal(link_id,deal_id,created_by,note) values($1,$2,$3,$4)',[result.link_id,wrong,actor.id,'Demo refused']),e=>e.code==='42501');
  } finally {await db.query('rollback');await db.end();}
});
