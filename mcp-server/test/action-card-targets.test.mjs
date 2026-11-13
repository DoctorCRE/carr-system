import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { registeredOperation } from '../src/mutation-registry.ts';
import { buildActionCardRef } from '../src/action-card-targets.ts';
import { readFile } from 'node:fs/promises';
test('exact-card v2 is registered separately from v1', () => {
  assert.equal(typeof TOOLS['resolve-action-link-v2']?.handler, 'function');
  assert.equal(typeof TOOLS['resolve-action-link'].handler, 'function');
});

test('an original push reference shows resolved after canonical review completes', async () => {
  const [recorded] = JSON.parse(await readFile(new URL('../contracts/fixtures/action-card-targets.v2.json',import.meta.url),'utf8'));
  const version = 'b'.repeat(32);
  const c = {query:async()=>({rows:[{target:{state:'resolved',source_version:version,
    detail:{...recorded.card.detail,reviewed:true}}}]})};
  const actor = {id:'20000000-0000-4000-8000-000000000001',slug:'joe',human:true};
  const result = await executeRegisteredTool(c,actor,'resolve-action-link-v2',{ref:recorded.ref});
  assert.equal(result.card.state,'resolved');
  assert.equal(result.card.source_version,version);
  assert.deepEqual(result.card.actions,[]);
});

test('new kinds retain the bounded address format and refuse on the v1 resolver', async () => {
  const actor = {id:'20000000-0000-4000-8000-000000000001',slug:'joe',human:true};
  for (const kind of ['post_call_review','feedback_event']) {
    const ref = buildActionCardRef({kind,id:actor.id,version:'a'.repeat(32)});
    assert.match(ref,/^al1_[A-Za-z0-9_-]+$/);
    assert.ok(ref.length <= 124);
    await assert.rejects(executeRegisteredTool({query:()=>assert.fail('new kind reached v1 database')},actor,'resolve-action-link',{ref}),
      e=>e.payload.error==='action_link_not_found');
  }
});

test('malformed and unsupported v2 references refuse without loading detail', async () => {
  const c = {query: () => assert.fail('malformed reference queried source')};
  const actor = {id:'20000000-0000-4000-8000-000000000001',slug:'joe',human:true};
  for (const ref of ['bad', 'al2_post_call_review.bad.1', 'al2_feedback_event.20000000-0000-4000-8000-000000000001.bad/version'])
    await assert.rejects(executeRegisteredTool(c,actor,'resolve-action-link-v2',{ref}),e => e.payload.error === 'action_link_not_found');
});

test('exact-card guard and published authority agree before database access', async () => {
  const tool = TOOLS['resolve-action-link-v2'];
  const metadata = registeredOperation('resolve-action-link-v2');
  assert.equal(metadata.human_only, tool.humanOnly);
  assert.equal(metadata.human_caller_only, tool.humanCallerOnly);
  assert.equal(metadata.write, false);
  const c = { query: () => assert.fail('unauthorized detail read') };
  for (const actor of [{}, {id:'synthetic',slug:'joe-local',human:false,unattended:true},
    {id:'synthetic',slug:'codex',human:false,sponsoring_human_slug:'joe',native_agent_verified:true}]) {
    await assert.rejects(executeRegisteredTool(c, actor, 'resolve-action-link-v2', {ref:'bad'}),
      e => e.payload.error === 'human_only_verb_requires_verified_partner');
    await assert.rejects(tool.handler(c, actor, {ref:'bad'}), e => e.payload.error === 'human_only_verb_requires_verified_partner');
  }
});
