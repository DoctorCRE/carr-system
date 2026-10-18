import test from 'node:test';
import assert from 'node:assert/strict';
import { featureEnabled, requireFeature } from '../src/feature-switches.js';

test('default, explicit off and audiences are evaluated on the server', () => {
  const flag = { name:'doc-suggestion-actions', default_enabled:true, enabled:null, audience:'joe', retired_at:null };
  assert.equal(featureEnabled(flag, 'joe'), true);
  assert.equal(featureEnabled(flag, 'dell'), false);
  assert.equal(featureEnabled({ ...flag, audience:'team' }, 'dell'), true);
  assert.equal(featureEnabled({ ...flag, audience:'team' }, 'codex'), false);
  assert.equal(featureEnabled({ ...flag, audience:'everyone' }, 'codex'), true);
  assert.equal(featureEnabled({ ...flag, enabled:false }, 'joe'), false);
  assert.equal(featureEnabled({ ...flag, retired_at:'2026-10-05' }, 'joe'), false);
  assert.equal(featureEnabled(null, 'joe'), false);
});

test('worker reads the switch anew and refuses a disabled verb with its name', async () => {
  let enabled = false;
  const c = { query:async () => ({ rows:[{ name:'doc-suggestion-actions', enabled, audience:'everyone' }] }) };
  const actor = { slug:'joe', human:true };
  await assert.rejects(requireFeature(c, actor, 'doc-suggestion-actions'), error =>
    error.payload.error === 'feature_disabled' && error.payload.message.includes('doc-suggestion-actions') && error.payload.hint === error.payload.message);
  enabled = true;
  await requireFeature(c, actor, 'doc-suggestion-actions');
  enabled = false;
  await assert.rejects(requireFeature(c, actor, 'doc-suggestion-actions'), /feature_disabled/);
});


import { executeRegisteredTool, TOOLS } from '../src/tools.js';
test('retirement tool does not advertise unused delegation metadata', () => {
  assert.equal(Object.hasOwn(TOOLS['check-feature-switches'], 'delegatesTo'), false);
});
test('worker dispatch refuses both suggestion writes before their handlers run',async()=>{
  const c={query:async(sql)=>{assert.match(sql,/from feature_switch/);return {rows:[]};}};
  const actor={slug:'joe',human:true,via:'dealroom-cookie',client_id:'dealroom-pwa'};
  for(const [name,args] of [
    ['decide-doc-suggestion',{idempotency_key:'test',suggestion_id:'test',base_version:1,choice:'discuss'}],
    ['propose-doc-correction',{idempotency_key:'test',suggestion_id:'test',base_version:1,proposed_text:'Correction',source_conversation_id:'test',source_sequence:1}],
  ]) await assert.rejects(executeRegisteredTool(c,actor,name,args),error=>error.payload.error==='feature_disabled');
});
