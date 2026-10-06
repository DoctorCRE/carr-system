import test from 'node:test';
import assert from 'node:assert/strict';
import { projectNetwork,bindReferralDeal,networkStatement } from '../src/relationship-network.js';
import { isBusinessApiPath, readRelationshipNetwork } from '../src/workspace-business-read.js';
const fixture=()=>({nodes:[{id:'a',name:'Demo vendor',contact_state:'active'},{id:'b',name:'Demo client',contact_state:'active'},{id:'c',name:'Demo broker',contact_state:'active'}],edges:[{id:'demo',from:'a',to:'b',via:'c',kind:'can_introduce',when:'2026-10-01',summary:'Demo offered access',detail:'Original demo entry'}],referrals:[{node_id:'a',deals:3,won:1,lost:1}]});
const now='2026-10-02T12:00:00Z';
test('versioned snapshot refuses unauthorized audiences and selectors before querying',async()=>{
 const actor={slug:'joe'},params=new URLSearchParams({contract:'relationship-network.v1'});let calls=0;
 const client={query:async()=>{calls++;return {rows:[{snapshot:fixture()}]};}};
 for(const args of [{actor:{slug:'demo-external'}},{tenant:'demo-tenant'},{params:new URLSearchParams()},{params:new URLSearchParams('contract=relationship-network.v1&contract=relationship-network.v1')},{params:new URLSearchParams('contract=relationship-network.v1&q=demo')}]) await assert.rejects(readRelationshipNetwork({client,actor,params,...args}));
 assert.equal(calls,0);assert.equal((await readRelationshipNetwork({client,actor,params,now:()=>new Date(now)})).schema,'carr-relationship-network.v1');assert.equal(calls,1);
 await assert.rejects(readRelationshipNetwork({client:{query:async()=>{throw Object.assign(new Error('private SQL'),{code:'42501'});}},actor,params}),e=>e.code==='DEPENDENCY_NOT_PROVISIONED');
});
test('offers retain reason, exact endpoints and resolved-only win denominator',()=>{const r=projectNetwork(fixture(),now);assert.equal(r.suggestions[0].reason,'Demo offered access');assert.equal(r.suggestions[0].via,'c');assert.equal(r.referrals[0].win_rate,.5);assert.equal(r.valid_until,'2026-10-02T12:01:00.000Z');});
test('holds, restrictions, completed/requested intros, missing endpoints and absent reasons suppress offers',()=>{
 for(const mutate of [s=>s.nodes[1].contact_state='hold',s=>s.nodes[0].restricted=true,s=>s.nodes[2].contact_state='do_not_contact',s=>s.edges.push({...s.edges[0],id:'other',kind:'introduced'}),s=>s.edges.push({...s.edges[0],id:'other',kind:'intro_requested'}),s=>s.edges[0].via='unknown',s=>s.edges[0].detail='']){const s=fixture();mutate(s);assert.equal(projectNetwork(s,now).suggestions.length,0);}
 for(const kind of ['introduced','intro_requested']){const s=fixture();s.edges.push({...s.edges[0],id:'reverse',kind,from:'b',to:'a'});assert.equal(projectNetwork(s,now).suggestions.length,0,`reverse ${kind} still suggested`);}
 const s=fixture();s.nodes[1].contact_state='hold';s.nodes[1].contact_state_until='2026-10-01';assert.equal(projectNetwork(s,now).suggestions.length,1);s.referrals[0].won=0;s.referrals[0].lost=0;assert.equal(projectNetwork(s,now).referrals[0].win_rate,null);
});
test('route is admitted and snapshot never guesses an attribution',()=>{assert.equal(isBusinessApiPath('/api/v1/business/relationships'),true);assert.equal(isBusinessApiPath('/api/v1/business/relationships/anything'),false);assert.throws(()=>projectNetwork({nodes:[],edges:[]},now));assert.doesNotMatch(networkStatement,/\b(insert|update|delete)\s/i);});
test('referral binding checks exact destination before writing and deduplicates',async()=>{
 const calls=[],client={query:async(text,args)=>{calls.push({text,args});return {rows:text.startsWith('select')?[{id:'demo'}]:[{deal_id:'00000000-0000-4000-8000-000000000001'}]};}};
 const args={deal_id:'00000000-0000-4000-8000-000000000001',note:'Demo exact attribution'},ends={to_party:'00000000-0000-4000-8000-000000000002'},link={id:'link',referred_by:'00000000-0000-4000-8000-000000000003',occurred_on:'2026-01-05'};
 for (const deal_id of ['',false,0,'invalid']) await assert.rejects(bindReferralDeal(client,{id:'actor'},{...args,deal_id},ends,'referral',link),e=>e.code==='referral_deal_invalid');
 assert.deepEqual(await bindReferralDeal(client,{id:'actor'},args,ends,'referral',link),{deal_id:args.deal_id});assert.deepEqual(calls[0].args,[args.deal_id,ends.to_party]);
 const insert=calls.find(call=>call.text.startsWith('insert into public.party_link_deal'));
 assert.match(insert.text,/on conflict do nothing/);assert.deepEqual(insert.args,['link',args.deal_id,'actor',args.note,link.referred_by,link.occurred_on]);
 await assert.rejects(bindReferralDeal({query:async()=>({rows:[]})},{id:'actor'},args,ends,'referral',link),e=>e.code==='referral_deal_target_mismatch');await assert.rejects(bindReferralDeal(client,{id:'actor'},args,ends,'knows',link),e=>e.code==='referral_deal_invalid');
});
