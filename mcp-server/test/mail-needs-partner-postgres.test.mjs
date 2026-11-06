import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { readMailNeedsPartner } from '../src/mail-needs-partner.ts';

test('Postgres mailbox isolation, lifecycle exclusion, priority and stable cursor', async t => {
  const fixture=await postgresDatabase('mail-needs-partner-');t.after(()=>fixture.close());const c=fixture.client;
  await c.query(`create table activity(id uuid primary key,actor_id uuid,kind text,email_source jsonb,email_context jsonb,occurred_at timestamptz,version integer);
    create table email_proposal(id uuid primary key,activity_id uuid,version integer,kind text,status text,disposition text,deferred_until timestamptz,owner text,next_date date)`);
  const actors={joe:{id:randomUUID(),slug:'joe',human:true},dell:{id:randomUUID(),slug:'dell',human:true}};
  const add=async (owner,day,extra={})=>{
    const a=randomUUID(),id=randomUUID();
    await c.query(`insert into activity values($1,$2,$3,$4::jsonb,$5::jsonb,$6,7)`,[a,actors[owner].id,extra.source_kind??'email_in',JSON.stringify({mailbox_owner:owner}),JSON.stringify({lifecycle:{state:extra.lifecycle??'live'}}),extra.instant??'2020-01-01T00:00:00Z']);
    await c.query('insert into email_proposal values($1,$2,3,\'next_step\',$3,$4,$5,$6,$7)',[id,a,extra.status??'pending',extra.disposition??null,extra.defer??null,owner,day]);return id;
  };
  const late=await add('joe','2020-01-01');const soon=await add('joe','2099-01-01');const unknown=await add('joe',null);
  const dell=await add('dell','2020-01-01');
  for(const extra of [{status:'sent'},{status:'dismissed'},{status:'confirmed',disposition:'handled'},{defer:'2099-01-01'},{source_kind:'email_out'},{lifecycle:'removed'}])await add('joe','2010-01-01',extra);
  const expired=await add('joe',null,{defer:'2000-01-01'});
  const invalid=await add('joe','infinity',{instant:'infinity'});
  const result=await readMailNeedsPartner(c,actors.joe,{});
  assert.equal(result.state,'ready');assert.deepEqual(result.items.slice(0,2).map(x=>x.proposal_id),[late,soon]);
  assert.deepEqual(new Set(result.items.slice(2).map(x=>x.proposal_id)),new Set([unknown,expired,invalid]));
  assert.deepEqual(result.items.slice(0,2).map(x=>x.reason_codes[0]),['recorded_overdue','recorded_upcoming']);
  assert.deepEqual((await readMailNeedsPartner(c,actors.dell,{})).items.map(x=>x.proposal_id),[dell]);
  const first=await readMailNeedsPartner(c,actors.joe,{limit:1});
  await c.query("update email_proposal set status='confirmed' where id=$1",[late]);
  const second=await readMailNeedsPartner(c,actors.joe,{limit:1,cursor:first.next_cursor});assert.equal(second.items[0].proposal_id,soon);
  assert.equal(second.items[0].source.version,7);
  const invalidItem=result.items.find(item=>item.proposal_id===invalid);
  assert.equal(invalidItem.decision_date,null);assert.equal(invalidItem.source.instant_status,'unknown');
  const tied=await add('joe','2099-01-01',{instant:'2021-01-01'});
  const tied2=await add('joe','2099-01-01',{instant:'2021-01-01'});
  const ordered=(await readMailNeedsPartner(c,actors.joe,{})).items.map(item=>item.proposal_id);
  assert.ok(ordered.indexOf(soon)<ordered.indexOf(tied));
  assert.deepEqual(ordered.filter(id=>[tied,tied2].includes(id)),[tied,tied2].sort());
});
