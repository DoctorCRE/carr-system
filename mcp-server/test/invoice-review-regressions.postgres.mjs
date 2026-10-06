// Production handlers and real competing transactions, synthetic loopback DB only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {TOOLS} from '../src/tools.js';
const url=process.env.CARR_CI_DATABASE_URL||process.env.DATABASE_URL;
if(!url||!['localhost','127.0.0.1'].includes(new URL(url).hostname))throw Error('disposable_loopback_database_required');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
async function fixture(t) {
  const clients=await Promise.all([0,1,2].map(async()=>{const c=new pg.Client({connectionString:url});await c.connect();return c;}));
  const [a,b,o]=clients;
  const actor={id:randomUUID(),slug:'joe',human:true,via:'synthetic-test'};
  const party=randomUUID(),client=randomUUID(),deal=randomUUID(),building=randomUUID(),space=randomUUID(),premises=randomUUID();
  t.after(async()=>{
    try {
      for(const c of clients)await c.query('rollback');
      await o.query('begin');
      await o.query('delete from deal_invoice_email where created_by=$1',[actor.id]);
      await o.query('delete from tool_call where actor_id=$1',[actor.id]);
      await o.query('delete from event where actor_id=$1',[actor.id]);
      await o.query('delete from lead_stage_move where created_by=$1',[actor.id]);
      await o.query('delete from lead_contact_draft where created_by=$1',[actor.id]);
      await o.query('delete from activity where actor_id=$1',[actor.id]);
      await o.query('delete from lead where created_by=$1',[actor.id]);
      await o.query('delete from premises_space where premises_id=$1',[premises]);
      await o.query('delete from premises where id=$1',[premises]);
      await o.query('delete from space where id=$1',[space]);
      await o.query('delete from building where id=$1',[building]);
      await o.query('delete from deal where client_id=$1',[client]);
      await o.query('delete from client where id=$1',[client]);
      await o.query('delete from party where id=$1',[party]);
      await o.query('delete from actor where id=$1',[actor.id]);
      await o.query('commit');
    } finally {await Promise.all(clients.map(c=>c.end()));}
  });
  const name='Synthetic Review '+deal;
  await o.query("insert into actor(id,slug,kind,display_name)values($1,$2,'human','Synthetic Review')",[actor.id,'synthetic-'+actor.id]);
  await o.query("insert into party(id,kind,name,created_by,updated_by)values($1,'person','Synthetic Practice',$2,$2)",[party,actor.id]);
  await o.query("insert into client(id,party_id,roster_ref,status,created_by,updated_by)values($1,$2,$3,'active_deal',$4,$4)",[client,party,'C-SYNTH-'+client,actor.id]);
  async function addDeal(c,id=deal){await c.query("insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by)values($1,$2,$3,'other','legal',$4,$4)",[id,client,name,actor.id]);}
  await addDeal(o);
  await o.query("insert into building(id,address,city,state,zip,created_by,updated_by)values($1,'123 Example Avenue','Pensacola','FL','32501',$2,$2)",[building,actor.id]);
  await o.query("insert into space(id,building_id,suite,created_by,updated_by)values($1,$2,'Suite 2',$3,$3)",[space,building,actor.id]);
  await o.query("insert into premises(id,deal_id,label,created_by)values($1,$2,'Synthetic Suite 2',$3)",[premises,deal,actor.id]);
  await o.query('insert into premises_space(premises_id,space_id)values($1,$2)',[premises,space]);
  const call=(c,verb,args={})=>TOOLS[verb].handler(c,actor,{idempotency_key:randomUUID(),...args});
  async function capture(extra={}){return call(o,'record-deal-invoice',{native_ref:'local-mail:'+randomUUID(),from_address:'invoices@example.test',deal_name:name,client_name:'Synthetic Practice',occurred_at:new Date(Date.now()-86400000).toISOString(),...extra});}
  async function run(){await a.query('begin');try{const r=await call(a,'advance-leads');await a.query('commit');return r;}catch(e){await a.query('rollback');throw e;}}
  function pause(match){const reached=deferred(),resume=deferred(),query=a.query.bind(a);let used=false;a.query=async(sql,...args)=>{const r=await query(sql,...args);if(!used&&match(sql)){used=true;reached.resolve();await resume.promise;}return r;};return {reached:reached.promise,resume:()=>resume.resolve()};}
  return {a,b,o,actor,party,client,deal,building,space,premises,call,capture,run,pause,addDeal};
}
async function blocked(f,finished){const deadline=Date.now()+3000;while(!finished()&&Date.now()<deadline){if((await f.o.query('select cardinality(pg_blocking_pids($1))>0 blocked',[f.b.processID])).rows[0].blocked)return true;await new Promise(r=>setTimeout(r,10));}return false;}
test('review 1: newly matching deal between snapshots prevents an ambiguous close',async t=>{
  const f=await fixture(t),i=await f.capture(),p=f.pause(sql=>sql.startsWith('select d.id,d.name')&&!sql.includes('for update'));
  const work=f.run();work.catch(()=>{});await p.reached;
  try{await f.addDeal(f.b,randomUUID());}finally{p.resume();}
  const m=(await work).invoice_closes.find(m=>m.invoice_id===i.invoice_id);
  assert.equal(m.status,'proposed');assert.equal(m.needs_confirmation,'Multiple deals match');
  assert.equal((await f.o.query('select phase from deal where id=$1',[f.deal])).rows[0].phase,'legal');
});
for(const [label,sql,key] of [
  ['candidate insertion',null,null],
  ['party name','update party set name=\'Changed Practice\' where id=$1','party'],
  ['party retirement','update party set deleted_at=now() where id=$1','party'],
  ['client retirement','update client set merged_into=$1 where id=$1','client'],
  ['premises membership','delete from premises_space where premises_id=$1','premises'],
  ['premises deal','update premises set deal_id=null where id=$1','premises'],
  ['space suite','update space set suite=\'Suite 9\' where id=$1','space'],
  ['building address','update building set address=\'Other Avenue\' where id=$1','building'],
  ['building retirement','update building set merged_into=$1 where id=$1','building'],
])test('review 1/2: final matching snapshot protects '+label+' through close',async t=>{
  const f=await fixture(t),i=await f.capture(),p=f.pause(sql=>sql.startsWith('select d.id,d.name')&&sql.includes('for update'));
  const work=f.run();work.catch(()=>{});await p.reached;let finished=false;
  const edit=(sql?f.b.query(sql,[f[key]]):f.addDeal(f.b,randomUUID())).finally(()=>{finished=true;});edit.catch(()=>{});
  let waited;try{waited=await blocked(f,()=>finished);}finally{p.resume();await Promise.all([work,edit]);}
  assert.equal(waited,true,label+' must wait until close commits');
  assert.equal((await f.o.query('select status from deal_invoice_email where id=$1',[i.invoice_id])).rows[0].status,'applied');
});
test('review 3: SQL projection matches full premises identity, refuses street and wrong suite',async t=>{
  const f=await fixture(t);
  const full=await f.capture({client_name:undefined,property_address:'123 Example Avenue, Suite 2, Pensacola, FL 32501'});
  const street=await f.capture({client_name:undefined,property_address:'123 Example Avenue'});
  const other=await f.capture({client_name:undefined,property_address:'123 Example Avenue, Suite 9, Pensacola, FL 32501'});
  const preview=(await f.call(f.o,'lead-stage-preview')).invoice_closes;
  assert.equal(preview.find(m=>m.invoice_id===full.invoice_id).status,'applied');
  for(const i of [street,other])assert.equal(preview.find(m=>m.invoice_id===i.invoice_id).status,'proposed');
});
test('review 4: ordered preview and apply agree on transitions, prior values and conflicts',async t=>{
  const f=await fixture(t);
  const first=await f.capture({occurred_at:new Date(Date.now()-172800000).toISOString()});
  const second=await f.capture();
  const ids=new Set([first.invoice_id,second.invoice_id]);
  const preview=(await f.call(f.o,'lead-stage-preview')).invoice_closes.filter(m=>ids.has(m.invoice_id));
  const applied=(await f.run()).invoice_closes.filter(m=>ids.has(m.invoice_id));
  assert.deepEqual(preview,applied);
  assert.equal(preview[1].status,'proposed');assert.equal(preview[1].from_phase,'closed');
  const prior=(await f.o.query('select prior_phase,prior_invoiced_on from deal_invoice_email where id=$1',[first.invoice_id])).rows[0];
  assert.deepEqual(prior,{prior_phase:preview[0].from_phase,prior_invoiced_on:null});
});
test('review 5: migrated predecessor approval can be undone, forged associations and later edits cannot',async t=>{
  const f=await fixture(t),lead=randomUUID(),activity=randomUUID(),move=randomUUID();
  await f.o.query("insert into lead(id,registry_ref,party_id,stage,created_by,updated_by)values($1,$2,$3,'new',$4,$4)",[lead,'L-SYNTH-'+lead,f.party,f.actor.id]);
  await f.o.query("insert into activity(id,occurred_at,actor_id,kind,summary,lead_id,source)values($1,now(),$2,'email_in','Synthetic predecessor',$3,'local_mail')",[activity,f.actor.id,lead]);
  await f.o.query('begin');
  await f.o.query("update lead set stage='qualified' where id=$1",[lead]);
  await f.o.query("insert into lead_stage_move(id,lead_id,from_stage,to_stage,activity_id,evidence_ref,strength,status,approved_by,approved_at,created_by,reason)values($1,$2,'new','qualified',$3,'local-mail:predecessor','weak','applied',$4,now(),$4,'Reply received')",[move,lead,activity,f.actor.id]);
  const event=(await f.o.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause)values(now(),$1,'approve-lead-move','lead',$2,'stage',$3,$4,'human_stated') returning id",[f.actor.id,lead,{stage:'new'},{stage:'qualified',evidence_ref:'local-mail:predecessor',activity_id:activity}])).rows[0].id;
  await f.o.query('commit');
  const version=(await f.o.query('select version from lead where id=$1',[lead])).rows[0].version;
  // Alter each association fact under a savepoint; the immutable history is restored afterwards.
  for(const sql of ["update event set verb='update-lead' where id=$1","update event set occurred_at=occurred_at+interval '1 second' where id=$1","update event set old_value='{\"stage\":\"engaged\"}' where id=$1","update event set new_value=jsonb_set(new_value,'{activity_id}','\"wrong\"') where id=$1"]){
    await f.o.query('begin');await f.o.query(sql,[event]);
    await assert.rejects(()=>f.call(f.o,'undo-lead-move',{move_id:move,base_version:version}),/newer_stage/);await f.o.query('rollback');
  }
  await f.o.query('begin');
  await f.o.query("insert into event(occurred_at,recorded_at,actor_id,verb,subject_type,subject_id,field,new_value,cause)values(now(),clock_timestamp(),$1,'update-lead','lead',$2,'stage','{\"stage\":\"qualified\"}','human_stated')",[f.actor.id,lead]);
  await assert.rejects(()=>f.call(f.o,'undo-lead-move',{move_id:move,base_version:version}),/newer_stage/);await f.o.query('rollback');
  const result=await f.call(f.o,'undo-lead-move',{move_id:move,base_version:version});
  assert.equal(result.stage,'new');
  assert.deepEqual((await f.o.query('select new_value from event where id=$1',[event])).rows[0].new_value,{stage:'qualified',evidence_ref:'local-mail:predecessor',activity_id:activity});
});
