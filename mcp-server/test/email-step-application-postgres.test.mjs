import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('email commitments apply atomically and undo refuses newer work', async t => {
  const fixture = await acquireDisposablePostgres({prefix:'email-step-',snapshot:true});
  const c = new pg.Client(fixture.connection); await c.connect();

  try {
    const actor = {...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
    const command = async (verb,args,db=c) => {
      await db.query('begin');
      try { await db.query('set local role carr_writer'); const r=await executeRegisteredTool(db,actor,verb,args); await db.query('commit'); return r; }
      catch(e) {await db.query('rollback');throw e;}
    };
    const make = async ({deal=true,owner='joe',due='2026-12-01',body,context}={}) => {
      const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic commitment',$1,$1) returning id",[actor.id])).rows[0].id;
      const client=(await c.query('insert into client(party_id,created_by,updated_by) values($1,$2,$2) returning id',[party,actor.id])).rows[0].id;
      const target=deal ? (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic commitment',$1,'purchase','site_selection','joe',$2,$2) returning id",[client,actor.id])).rows[0].id : null;
      const text=body ?? `I will review terms by ${due}.`;
      const activity=(await c.query(`insert into activity(kind,summary,detail,client_id,deal_id,actor_id,updated_by,occurred_at,email_context)
        values('email_out','Synthetic commitment',$1,$2,$3,$4,$4,now()-interval '60 days',$5) returning id`,[text,client,target,actor.id,
        JSON.stringify(context ?? {body_kind:'full',body_completeness:'complete',body_outcome:'complete'})])).rows[0].id;
      if (target) await c.query("select public.enqueue_email_filing($1,$2,'fixture','inherited')",[activity,target]);
      const stored=await command('record-email-proposal',{idempotency_key:randomUUID(),kind:'next_step',activity_id:activity,
        ...(target ? {deal:target}:{}),text:'Review terms',next_date:due,owner,evidence_quote:text,confidence:0.99});
      return {id:stored.proposal_id,activity,target,client};
    };
    const apply=x=>command('record-email-proposal',{idempotency_key:randomUUID(),mode:'apply_next_step',proposal_id:x.id});
    const undo=(x,version)=>command('record-email-proposal',{idempotency_key:randomUUID(),mode:'undo_next_step',proposal_id:x.id,...(version ? {base_version:version}:{})});
    await t.test('old durable pending source applies once, changed key retry and listing retain binding',async()=>{
      const x=await make();
      assert.ok((await command('email-router-queue',{purpose:'next_steps'})).proposals.some(p=>p.proposal_id===x.id));
      const first=await apply(x); assert.equal(first.email_application.state,'applied');
      assert.equal((await apply(x)).duplicate_suppressed,true);
      assert.equal((await c.query("select count(*)::int n from next_action where subject_id=$1",[x.target])).rows[0].n,1);
      let shown=(await command('list-email-proposals',{deal:x.target,status:'confirmed'})).proposals[0].email_application;
      assert.equal(shown.undo_eligible,true); assert.equal(shown.source_changed,false);
      await c.query("update activity set email_context=jsonb_set(email_context,'{lifecycle}','{\"state\":\"deleted\"}') where id=$1",[x.activity]);
      shown=(await command('list-email-proposals',{deal:x.target,status:'confirmed'})).proposals[0].email_application;
      assert.equal(shown.source_terminal,true); assert.equal(shown.source_changed,true);
      await assert.rejects(undo(x,first.email_application.subject_version),e=>e.payload?.error==='email_not_live');
    });
    await t.test('live plan undo uses shared call restoration',async()=>{
      const x=await make(), first=await apply(x), receipt=first.email_application;
      assert.equal((await undo(x,receipt.subject_version)).email_application.state,'undone');
      assert.equal((await c.query('select status from next_action where id=$1',[receipt.action_id])).rows[0].status,'dropped');
      assert.equal((await c.query('select next_date from deal where id=$1',[x.target])).rows[0].next_date,null);
      assert.equal((await command('get-deal-room',{deal:x.target})).next_step,null);
    });
    await t.test('newer partner edit blocks undo without restoring stale plan',async()=>{
      const x=await make(), first=await apply(x), receipt=first.email_application;
      await c.query('update deal set next_date=$2 where id=$1',[x.target,'2026-12-03']);
      const version=(await c.query('select version from deal where id=$1',[x.target])).rows[0].version;
      await assert.rejects(undo(x,version),e=>e.payload?.error==='newer_change_exists');
      assert.equal((await c.query('select next_date::text as d from deal where id=$1',[x.target])).rows[0].d,'2026-12-03');
      assert.equal((await c.query('select status from next_action where id=$1',[receipt.action_id])).rows[0].status,'open');
    });
    await t.test('contact task applies and guarded undo drops only its own action',async()=>{
      const x=await make({deal:false}), first=await apply(x);
      assert.equal(first.email_application.state,'applied');
      assert.equal(first.email_application.subject_type,'client');
      assert.equal((await undo(x)).email_application.state,'undone');
    });
    await t.test('existing sibling task is held unchanged',async()=>{
      const x=await make();
      const sibling=await command('set-next-action',{idempotency_key:randomUUID(),ref:x.target,description:'Independent partner task',owner:'dell',due_on:'2026-12-04'});
      assert.equal((await apply(x)).email_application.reason,'existing_plan');
      assert.equal((await c.query('select status from next_action where id=$1',[sibling.next_action_id])).rows[0].status,'open');
    });
    await t.test('incomplete, undated and recalled source remain visible holds or refusals',async()=>{
      for (const options of [{context:{body_kind:'preview'}},{due:null,body:'I will review the terms.'}]) {
        const x=await make(options); assert.equal((await apply(x)).email_application.state,'review');
      }
      const x=await make(); await c.query("update activity set email_context=jsonb_set(email_context,'{lifecycle}','{\"state\":\"recalled\"}') where id=$1",[x.activity]);
      await assert.rejects(apply(x),e=>e.payload?.error==='email_not_live');
    });
    await t.test('changed captured evidence cannot authorize old extracted work',async()=>{
      const x=await make();
      await c.query("update activity set detail=detail||' Revised.' where id=$1",[x.activity]);
      assert.equal((await apply(x)).email_application.reason,'source_revision');
    });
    await t.test('held proposal survives expiry and age',async()=>{
      const x=await make({due:null,body:'I will review terms.'});
      await apply(x);
      await c.query("update email_proposal set created_at=now()-interval '60 days' where id=$1",[x.id]);
      await command('record-email-proposal',{mode:'expire_blocked',idempotency_key:randomUUID()});
      assert.equal((await c.query('select status from email_proposal where id=$1',[x.id])).rows[0].status,'pending');
    });
    await t.test('concurrent independent clients create exactly one application',async()=>{
      const x=await make(), other=new pg.Client(fixture.connection); await other.connect();
      try {
        const results=await Promise.all([apply(x),command('record-email-proposal',{mode:'apply_next_step',proposal_id:x.id,idempotency_key:randomUUID()},other)]);
        assert.equal(results.filter(r=>r.duplicate_suppressed).length,1);
        assert.equal((await c.query('select count(*)::int n from next_action where subject_id=$1',[x.target])).rows[0].n,1);
      } finally {await other.end();}
    });
    await t.test('rollback after canonical write leaves no effect and replay creates one task',async()=>{
      const x=await make();
      await c.query(`create function pg_temp.fail_email_receipt() returns trigger language plpgsql as $$begin
        if new.correction_provenance ? 'email_application' then raise exception 'synthetic_receipt_crash'; end if; return new; end$$`);
      await c.query('create trigger synthetic_receipt_crash before update on email_proposal for each row execute function pg_temp.fail_email_receipt()');
      await assert.rejects(apply(x),/synthetic_receipt_crash/);
      assert.equal((await c.query('select count(*)::int n from next_action where subject_id=$1',[x.target])).rows[0].n,0);
      await c.query('drop trigger synthetic_receipt_crash on email_proposal');
      assert.equal((await apply(x)).email_application.state,'applied');
      assert.equal((await apply(x)).duplicate_suppressed,true);
    });
  } finally {await c.end();await fixture.close();}
});
