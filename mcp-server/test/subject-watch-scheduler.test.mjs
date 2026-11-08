// Verify bounded scheduling, rollback, and connection cleanup.
import test from 'node:test';
import assert from 'node:assert/strict';
import { sweepSubjectWatches } from '../src/subject-watch-scheduler.js';
for(const failure of [null,'connect','begin','select','commit']) {
 test(`scheduled sweep ${failure ?? 'four batches'} cleans its connection`,async()=>{
  const calls=[]; let batches=0;
  class Pool {
   async connect(){calls.push('connect');if(failure==='connect')throw Error('offline');return this;}
   async query(input){const sql=typeof input==='string'?input:input.text;calls.push(sql);if(sql.startsWith(failure??'!'))throw Error('injected');
    if(sql.startsWith('select')){batches++;return {rows:[{result:{scanned:50,emitted:0}}]};}}
   release(destroy){assert.equal(destroy,true);calls.push('release');} async end(){calls.push('end');}
  }
  const action=sweepSubjectWatches({scheduledTime:Date.UTC(2027,1,1)},{DATABASE_URL_WRITER:'synthetic'},Pool);
  if(failure) await assert.rejects(action); else {await action;assert.equal(batches,4);}
  assert.equal(calls.at(-1),'end');if(failure!=='connect') assert.ok(calls.includes('release'));
  if(['select','commit'].includes(failure))assert.ok(calls.includes('rollback'));
 });
}
