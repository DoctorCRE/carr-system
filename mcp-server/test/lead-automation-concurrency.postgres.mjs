// Real handler/transaction regressions; synthetic data on disposable PG only.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { TOOLS } from '../src/tools.js';
const url=process.env.CARR_CI_DATABASE_URL || process.env.DATABASE_URL;
if (!url || !['localhost','127.0.0.1'].includes(new URL(url).hostname)) throw new Error('disposable_loopback_database_required');
const clients=await Promise.all([0,1,2].map(async()=>{const c=new pg.Client({connectionString:url});await c.connect();return c;}));
const [a,b,observer]=clients;
const actor={id:randomUUID(),slug:'joe',human:true,via:'synthetic-test'};
const parties=[],leads=[];
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
async function fixture(stage='new',strong=true) {
  const party=randomUUID(),lead=randomUUID(),activity=randomUUID(),ref='L-SYNTH-'+randomUUID();parties.push(party);leads.push(lead);
  await observer.query("insert into party(id,kind,name,email,created_by,updated_by) values($1,'person','Synthetic concurrency','contact@example.test',$2,$2)",[party,actor.id]);
  await observer.query(`insert into lead(id,registry_ref,party_id,stage,event_confidence,est_lease_event,created_at,created_by,updated_by)
    values($1,$2,$3,$4,$5,current_date+90,now()-interval '7 days',$6,$6)`,[lead,ref,party,stage,strong?'high':'low',actor.id]);
  await observer.query(`insert into activity(id,occurred_at,actor_id,kind,summary,detail,lead_id,source)
    values($1,now()-interval '1 hour',$2,'email_in','Synthetic', $3,$4,'local_mail')`,[activity,actor.id,JSON.stringify({match:'exact',party_id:party,evidence_ref:'local-mail:'+activity,automated:false}),lead]);
  return {party,lead,activity,ref};
}
async function draft(f) {
  return (await observer.query(`insert into lead_contact_draft(lead_id,party_id,subject,body,scheduled_for,time_zone,created_by)
    values($1,$2,'Synthetic','Synthetic',now(),'America/Chicago',$3) returning *`,[f.lead,f.party,actor.id])).rows[0];
}
async function transaction(c,verb,args) {
  await c.query('begin');
  try {const result=await TOOLS[verb].handler(c,actor,{idempotency_key:randomUUID(),...args});await c.query('commit');return result;}
  catch(error){await c.query('rollback');throw error;}
}
function pauseAfter(c,match) {
  const reached=deferred(),resume=deferred(),query=c.query.bind(c);let used=false;
  c.query=async(sql,...args)=>{const result=await query(sql,...args);if(!used&&match(sql)){used=true;reached.resolve();await resume.promise;}return result;};
  return {reached:reached.promise,resume:()=>resume.resolve(),restore:()=>{c.query=query;}};
}
async function blockedOrFinished(pid,finished) {
  const deadline=Date.now()+3000;
  while(!finished()&&Date.now()<deadline) {
    if((await observer.query('select cardinality(pg_blocking_pids($1))>0 as blocked',[pid])).rows[0].blocked)return true;
    await new Promise(r=>setTimeout(r,10));
  }
  return false;
}
try {
  await observer.query("insert into actor(id,slug,kind,display_name)values($1,$2,'human','Synthetic concurrency')",[actor.id,'synthetic-'+actor.id]);
  // Suppression/retirement must serialize with every eligibility mutation.
  for(const mode of ['move','draft','approve-draft','approve-move']) {
    const f=await fixture(mode==='draft'||mode==='approve-draft'?'qualified':'new',mode!=='approve-move');
    let verb='advance-leads',args={},match=sql=>sql.includes('select l.*, (p.merged_into');
    if(mode==='draft')match=sql=>sql.includes('select l.id,l.party_id,l.owner_id');
    if(mode==='approve-draft'){const d=await draft(f);verb='approve-lead-draft';args={draft_id:d.id};match=sql=>sql.includes('select d.* from lead_contact_draft d join lead');}
    if(mode==='approve-move') {
      const m=(await observer.query(`insert into lead_stage_move(lead_id,from_stage,to_stage,activity_id,evidence_ref,strength,status,created_by,reason)
        values($1,'new','qualified',$2,'local-mail:synthetic','weak','proposed',$3,'Synthetic reply received') returning id`,[f.lead,f.activity,actor.id])).rows[0];
      verb='approve-lead-move';args={move_id:m.id,base_version:1};match=sql=>sql.includes('select m.*,l.version,l.stage');
    }
    const pause=pauseAfter(a,match);
    const work=transaction(a,verb,args);work.catch(()=>{});await pause.reached;
    let finished=false;
    const suppress=transaction(b,'advance-leads',{dry_run:true}).then(async()=>{
      await b.query("update party set contact_state='do_not_contact' where id=$1",[f.party]);
    }).finally(()=>{finished=true;});suppress.catch(()=>{});
    try {assert.equal(await blockedOrFinished(b.processID,()=>finished),true,`${mode}: party change must wait for eligibility mutation`);}
    finally {pause.resume();const outcomes=await Promise.allSettled([work,suppress]);pause.restore();for(const result of outcomes)assert.equal(result.status,'fulfilled',result.reason?.message);}
  }
  for(const verb of ['approve-lead-draft','approve-lead-move','record-lead-contact','advance-leads']) {
    const f=await fixture(verb==='approve-lead-draft'?'qualified':'new',verb!=='approve-lead-move');
    let args={idempotency_key:randomUUID()};
    if(verb==='approve-lead-draft')args.draft_id=(await draft(f)).id;
    if(verb==='approve-lead-move') {
      args.move_id=(await observer.query(`insert into lead_stage_move(lead_id,from_stage,to_stage,activity_id,evidence_ref,strength,status,created_by,reason)
        values($1,'new','qualified',$2,'local-mail:synthetic','weak','proposed',$3,'Synthetic reply received') returning id`,[f.lead,f.activity,actor.id])).rows[0].id;
      args.base_version=1;
    }
    if(verb==='record-lead-contact')args={...args,lead:f.ref,native_ref:'local-mail:overlap-'+randomUUID(),counterparty_address:'contact@example.test',kind:'email_in',occurred_at:new Date(Date.now()-10000).toISOString(),automated:false};
    const pause=pauseAfter(a,sql=>sql.startsWith('select request_hash, response'));
    const first=transaction(a,verb,args);first.catch(()=>{});await pause.reached;
    let finished=false;const second=transaction(b,verb,args).finally(()=>{finished=true;});second.catch(()=>{});
    await blockedOrFinished(b.processID,()=>finished);
    pause.resume();const results=await Promise.allSettled([first,second]);pause.restore();
    for(const result of results)assert.equal(result.status,'fulfilled',`${verb}: ${result.reason?.message}`);
    const values=results.map(r=>r.value);
    assert.equal(values.filter(v=>v.replayed).length,1,`${verb}: one overlapping call replays`);
    const {replayed,...replay}=values.find(v=>v.replayed);
    assert.deepEqual(replay,values.find(v=>!v.replayed));
    assert.equal((await observer.query('select count(*)::int n from tool_call where idempotency_key=$1',[args.idempotency_key])).rows[0].n,1);
  }
  console.log('db-gate-proof: lead concurrency — party suppression serializes with moves, drafts and both approvals');
} finally {
  for(const c of [a,b])await c.query('rollback');
  await observer.query('delete from tool_call where actor_id=$1',[actor.id]);
  await observer.query('delete from event where actor_id=$1',[actor.id]);
  await observer.query('delete from lead_stage_move where lead_id=any($1::uuid[])',[leads]);
  await observer.query('delete from lead_contact_draft where lead_id=any($1::uuid[])',[leads]);
  await observer.query('delete from activity where actor_id=$1',[actor.id]);
  await observer.query('delete from lead where id=any($1::uuid[])',[leads]);
  await observer.query('delete from party where id=any($1::uuid[])',[parties]);
  await observer.query('delete from actor where id=$1',[actor.id]);
  await Promise.all(clients.map(c=>c.end()));
}
