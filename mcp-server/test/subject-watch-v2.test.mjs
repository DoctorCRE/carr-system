import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync, execFileSync } from 'node:child_process';
import { sweepSubjectWatches } from '../src/subject-watch-scheduler.js';
import { Validator } from '@cfworker/json-schema';
import { subjectWatchV2Tools, parseSubjectWatchV2 } from '../src/subject-watches.js';
import { subjectWatchPolicySchema, validSubjectWatchPolicy } from '../contracts/subject-watch.v2.js';

test('recorded registered response passes the production parser; malformed policies refuse',()=>{
  const fixture=JSON.parse(readFileSync(new URL('../contracts/subject-watch.v2.fixture.json',import.meta.url)));
  assert.equal(parseSubjectWatchV2(fixture).schema_version,'subject-watch.v2');
  for(const mutate of [
    value=>value.watch.actor_id='dell',
    value=>value.watch.lifecycle_version=0,
    value=>value.watch.policy.change_kinds=['unknown'],
    value=>delete value.watch.policy.include_self_changes,
    value=>value.watch.policy.lease_threshold_days=[-1],
  ]) {
    const invalid=structuredClone(fixture);mutate(invalid);
    assert.throws(()=>parseSubjectWatchV2(invalid),/invalid_subject_watch_v2/);
  }
  assert.throws(()=>parseSubjectWatchV2({ok:true,schema_version:'subject-watch.v2'}));
});

test('policy requires explicit lease thresholds and defaults self changes in the producer',()=>{
  const validator=new Validator(subjectWatchPolicySchema);
  for(const policy of [{change_kinds:[]},
    {change_kinds:['reply_arrived','reply_arrived']},{change_kinds:['reply_arrived'],actor_id:'joe'}])
    assert.equal(validator.validate(policy).valid,false);
  assert.equal(validator.validate({change_kinds:['reply_arrived']}).valid,true);
  assert.equal(validSubjectWatchPolicy({change_kinds:['lease_threshold_crossed']}),false);
  assert.equal(validSubjectWatchPolicy({change_kinds:['lease_threshold_crossed'],lease_threshold_months:[12]}),true);
  assert.equal(validSubjectWatchPolicy({change_kinds:['lease_threshold_crossed'],lease_threshold_months:[121]}),false);
});

test('v2 schemas exclude actor overrides and require generation for lifecycle mutations',()=>{
  const tools=subjectWatchV2Tools({});
  assert.equal(Object.keys(tools).length,7);
  for(const [name,verb] of Object.entries(tools)) {
    assert.equal(verb.inputSchema.additionalProperties,false);
    assert.equal(Object.hasOwn(verb.inputSchema.properties,'actor_id'),false);
    if(verb.write) assert.ok(verb.inputSchema.required.includes('idempotency_key'));
    if(verb.write && name!=='watch-subject-v2')
      assert.ok(verb.inputSchema.required.includes('subscription_id') && verb.inputSchema.required.includes('base_version'));
  }
});

test('change read declares and enforces verified human authority', async()=>{
  const { TOOLS, executeRegisteredTool } = await import('../src/tools.js');
  const tool=TOOLS['read-subject-watch-changes-v1'];
  assert.ok(tool);
  assert.equal(tool.humanOnly,true);
  assert.equal(tool.humanCallerOnly,true);
  let queries=0;
  await assert.rejects(executeRegisteredTool({query:async()=>{queries++;}},
    {slug:'joe-local',human:false},'read-subject-watch-changes-v1',
    {subject_type:'deal',subject_id:'00000000-0000-4000-8000-000000000001',subscription_id:'00000000-0000-4000-8000-000000000002'}));
  assert.equal(queries,0);
});

test('change response fixture uses the production parser and refuses malformed receipts',async()=>{
  const {parseSubjectWatchChanges}=await import('../contracts/subject-watch-change.v1.js');
  const schema=JSON.parse(readFileSync(new URL('../contracts/subject-watch-change.v1.json',import.meta.url)));
  const fixture=JSON.parse(readFileSync(new URL('../contracts/subject-watch-change.v1.fixture.json',import.meta.url)));
  assert.equal(new Validator(schema).validate(fixture).valid,true);
  assert.equal(parseSubjectWatchChanges(fixture),fixture);
  const malformed=structuredClone(fixture);malformed.changes[0].disposition='delivered';
  assert.throws(()=>parseSubjectWatchChanges(malformed),/invalid_subject_watch_change_v1/);
});

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

const proof=fileURLToPath(new URL('../../ops/verify-subject-watch-delivery-proof.py',import.meta.url));
test('proof CLI passes exact scheduled evidence and refuses duplicates and insufficient evidence',t=>{
 const sample=JSON.parse(readFileSync(new URL('../contracts/subject-watch-scheduled-proof.v1.fixture.json',import.meta.url)));
 const root=mkdtempSync(join(tmpdir(),'watch-proof-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 for (const [name,mutate,status] of [
  ['pass',()=>{},0],['duplicate',v=>v.sample.notifications=2,1],
  ['business mutation',v=>v.sample.business_mutations=1,1],
  ['missing retry',v=>v.sample.scheduled_at=v.sample.first_delivery_at,78],
  ['unknown release',v=>v.release.git_sha.value=null,78],
  ['older release',v=>v.release.git_sha.value='b'.repeat(40),78],
 ]) {
  const fixture=structuredClone(sample);mutate(fixture);const path=join(root,name+'.json');writeFileSync(path,JSON.stringify(fixture));
  const result=spawnSync(process.env.CARR_CI_PYTHON??'python3',[proof,'--fixture',path,'--expected-merge','a'.repeat(40),'--watch-id',sample.sample.watch_id],{encoding:'utf8'});
  assert.equal(result.status,status,name+' '+result.stdout+' '+result.stderr);
 }
});

test('release containment accepts descendants and rejects older and unrelated commits',t=>{
 const root=mkdtempSync(join(tmpdir(),'watch-proof-git-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 const gitEnv={...process.env};
 for(const key of ['GIT_SHALLOW_FILE','GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE']) delete gitEnv[key];
 const git=(...args)=>execFileSync('git',['-C',root,...args],{encoding:'utf8',env:gitEnv,stdio:['ignore','pipe','pipe']}).trim();
 git('init','--initial-branch=main');git('config','user.name','Synthetic proof');git('config','user.email','synthetic@example.test');
 const message=join(root,'message');writeFileSync(message,'synthetic proof commit');
 const commit=()=>{git('commit','--allow-empty','-F',message);return git('rev-parse','HEAD');};
 const older=commit(),expected=commit(),descendant=commit();
 git('switch','--orphan','unrelated');const unrelated=commit();
 const base=JSON.parse(readFileSync(new URL('../contracts/subject-watch-scheduled-proof.v1.fixture.json',import.meta.url)));
 for(const [observed,status] of [[expected,0],[descendant,0],[older,78],[unrelated,78]]) {
  const fixture=structuredClone(base);fixture.release.git_sha.value=observed;const path=join(root,'fixture.json');writeFileSync(path,JSON.stringify(fixture));
  const result=spawnSync(process.env.CARR_CI_PYTHON??'python3',[proof,'--fixture',path,'--expected-merge',expected,'--watch-id',fixture.sample.watch_id],{cwd:root,encoding:'utf8',env:gitEnv});
  assert.equal(result.status,status,result.stdout+result.stderr);
 }
});
