import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
