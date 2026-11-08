import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { registeredOperation } from '../src/mutation-registry.js';
test('exact-card v2 is registered separately from v1', () => {
  assert.equal(typeof TOOLS['resolve-action-link-v2']?.handler, 'function');
  assert.equal(typeof TOOLS['resolve-action-link'].handler, 'function');
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
