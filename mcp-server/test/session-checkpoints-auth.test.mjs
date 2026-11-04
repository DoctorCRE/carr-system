import test from 'node:test';
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
