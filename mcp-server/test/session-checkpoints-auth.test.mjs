import test from 'node:test';
import { randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { profileForActor, PROFILES } from '../src/mcp.js';

test('neutral credential cannot escape its profile through full or doc paths',()=>{
 const actor={via:'session-checkpoint-token'};
 for(const path of ['/mcp?profile=full','/doc/mcp?profile=full','/mcp?profile=capture'])
  assert.equal(profileForActor(actor,new Request('https://example.invalid'+path)),'session-checkpoint-v1');
 assert.deepEqual([...PROFILES['session-checkpoint-v1']],['write-session-checkpoint-v1','read-session-checkpoint-v1','archive-session-checkpoint-v1']);
 assert.equal(PROFILES['codex-continuity'].size,0);
});
test('credential authority derives only from the server map and never provider attribution',()=>{
 const map=JSON.stringify([{token:'synthetic-checkpoint-credential',sponsor:'joe',authority_session:'889196a6-1926-4274-94ec-0d89c647c27e',binding:{provider:'grok',native_session_id:'synthetic-native'}}]);
 const script=`import assert from 'node:assert/strict';
import { sessionCheckpointActorForToken } from './src/identity.js';
const raw=process.env.SESSION_CHECKPOINT_TOKENS;
const actor=sessionCheckpointActorForToken('Bearer synthetic-checkpoint-credential',raw);
assert.equal(actor.slug,'joe-local');assert.equal(actor.via,'session-checkpoint-token');
assert.equal(actor.session_checkpoint_authority.binding.provider,'grok');
assert.equal(sessionCheckpointActorForToken('Bearer wrong',raw),null);
assert.equal(sessionCheckpointActorForToken('Bearer synthetic-checkpoint-credential',raw.replace('joe','dell')),null);
assert.equal(sessionCheckpointActorForToken(null,raw),null);
console.log('verified server-held scoped identity');`;
 const output=execFileSync(process.execPath,['--input-type=module','-e',script],{cwd:new URL('../',import.meta.url),env:{...process.env,SESSION_CHECKPOINT_TOKENS:map},encoding:'utf8'});
 assert.match(output,/verified server-held/);
});

import { dispatch, callTool } from '../src/mcp.js';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { SESSION_CHECKPOINT_VERBS, sessionCheckpointTools } from '../src/session-checkpoints.js';

test('registered dispatcher discovers exactly the neutral credential contract', async()=>{
 const request=new Request('https://example.invalid/doc/mcp?profile=full',{
  method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/list'}),
 });
 const body=await (await dispatch(request,{}, {},{slug:'joe-local',via:'session-checkpoint-token'})).json();
 assert.deepEqual(body.result.tools.map(t=>t.name).sort(),[...SESSION_CHECKPOINT_VERBS].sort());
 const direct=sessionCheckpointTools();
 for(const tool of body.result.tools){
  assert.deepEqual(tool.inputSchema,direct[tool.name].inputSchema);
  assert.equal(tool.annotations.readOnlyHint,tool.name==='read-session-checkpoint-v1');
  assert.equal(TOOLS[tool.name].writerConnection,true);
 }
});

test('neutral credential refuses business reads and writes before any connection',async()=>{
 for(const name of ['find','add-loop']) {
  const args=name==='find'?{query:'synthetic'}:{title:'synthetic',idempotency_key:randomUUID()};
  for(const profile of ['full','session-checkpoint-v1'])
   await assert.rejects(callTool({}, {slug:'joe-local',via:'session-checkpoint-token'},name,args,profile),
    e=>e.payload?.error==='not_in_profile');
  await assert.rejects(executeRegisteredTool({}, {slug:'joe-local',via:'session-checkpoint-token'},name,args),
   e=>e.payload?.error==='not_in_profile');
 }
});
