import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { assertAdmits } from './helpers/registry-admission.mjs';
import { TOOLS } from '../src/tools.js';
const id=n=>`30000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const human={id:id(900),slug:'joe',display:'Example Partner',human:true,via:'mcp',client_id:'test'};
class Fake {
 constructor(){this.row={id:id(1),stage:'new',version:1,base_version:1,suppressed:false,contact_eligible:true,live_party:true,owner_id:null,client_id:null};this.events=[];this.calls=new Map();this.queries=[];this.evidence=[{id:id(10),occurred_at:'2026-10-01T10:00:00Z'}];this.last={event_id:id(20),automatic:true,stage:'new',prior_stage:'outreach_active'};}
 async query(text,p=[]){const s=text.replace(/\s+/g,' ').trim();this.queries.push({s,p});
 if(s.startsWith('select pg_advisory'))return{rows:[]};
 if(s.startsWith('select request_hash'))return{rows:this.calls.has(p[0])?[this.calls.get(p[0])]:[]};
 if(s.startsWith('insert into tool_call')){this.calls.set(p[0],{request_hash:p[3],response:JSON.parse(p[4])});return{rows:[]}}
 if(s.includes("from v_ref_index where subject_type='lead'"))return{rows:[{subject_id:id(1)}]};
 if(s.startsWith('select version from lead'))return{rows:[{version:this.row.version}]};
 if(s.startsWith('select created_at from lead'))return{rows:[{created_at:'2026-09-01T00:00:00Z'}]};
 if(s.includes('from event e join actor'))return{rows:[]};
 if(s.startsWith('select 1 from lead_stage'))return{rows:[{}]};
 if(s.includes('from activity where lead_id'))return{rows:this.evidence.filter(e=>!p[1]||p[1].includes(e.id))};
 if(s.includes("select id,cause,old_value->>'stage'"))return{rows:[this.last]};
 if(s.startsWith('select id,party_id from client'))return{rows:[{id:id(800),party_id:id(801)}]};
 if(s.startsWith('select p.id from party'))return{rows:[]};
 if(s.includes('from v_lead_workspace_lifecycle'))return{rows:[{...this.row,contact_eligible:this.row.contact_eligible&&!this.row.suppressed}]};
 if(s.startsWith('select id,slug,display_name from actor'))return{rows:[{id:human.id,slug:'joe',display_name:human.display}]};
 if(s.includes('from v_lead_stage_transition'))return{rows:[this.last]};
 if(s.startsWith('with board as materialized'))return{rows:[{leads:[this.row],detail:p[0]===this.row.id?{...this.row,notes:'Synthetic original entry',correspondence:this.evidence,correspondence_coverage:'captured_entries_only'}:null}]};
 if(s.startsWith('select cl.id from client'))return{rows:p[0]===id(800)?[{id:id(800)}]:[]};
 if(s.startsWith('select l.stage')||s.startsWith('select l.client_id')||s.includes('from lead where id=$1'))return{rows:[this.row]};
 if(s.startsWith('update lead set')){this.updated={s,p};return{rows:[]}};
 if(s.startsWith('insert into event')){this.events.push({verb:p[2],field:p[5],old:JSON.parse(p[6]),new:JSON.parse(p[7]),cause:s.match(/'(human_stated|human_correction|automation_job)'/)[1],quote:p[8],reason:p[9]});return{rows:[]}};
 if(s.includes('from v_lead_board b'))return{rows:[{...this.row,party_id:id(2)}]};
 if(s.startsWith('select p.phone'))return{rows:[{phone:'+1-555-010-0000',email:'example@example.test',notes:'Synthetic original entry'}]};
 if(s.includes('from event where subject_type'))return{rows:[{event_id:id(20),prior_stage:'new',stage:'qualified'}]};
 throw new Error(`unhandled synthetic query: ${s}`);
 }
}
const args=(extra={})=>({idempotency_key:'synthetic-key',lead:'L-1',base_version:1,expected_actor:'joe',...extra});
test('workspace projection is optional/versioned, exact lifecycle flags, current evidence, no invented search time',async()=>{
 const db=new Fake(),result=await TOOLS['lead-board'].handler(db,human,{workspace:'leads',lead_id:id(1)});
 assert.equal(result.schema_version,'lead-workspace.v1');assert.equal(result.last_search_at,null);assert.equal(result.search_run_coverage,'unavailable');assert.equal(result.detail.correspondence[0].id,id(10));assert.equal(result.detail.correspondence_coverage,'captured_entries_only');assert.equal(result.detail.notes,'Synthetic original entry');
 assert.equal(db.queries.length,1);
});
test('workspace detail refuses absent lead without any contact query',async()=>{const db=new Fake();const r=await TOOLS['lead-board'].handler(db,human,{workspace:'leads',lead_id:id(404)});assert.equal(r.detail,null);assert.equal(db.queries.length,1)});
test('stage review validates exact lead evidence and records derived evidence date and literal answer',async()=>{
 const db=new Fake();const a=args({fields:{stage:'engaged'},stage_review:{reason:'Reply received',evidence_ids:[id(10)],human_quote:'Synthetic confirmation'}});await TOOLS['update-lead'].handler(db,human,a);
 assert.equal(db.events[0].new.stage_review.evidence_date,'2026-10-01T10:00:00.000Z');assert.equal(db.events[0].quote,'Synthetic confirmation');assert.equal(db.events[0].reason,'Reply received');assert.equal(db.events[0].cause,'human_stated');
 assert.equal((await TOOLS['update-lead'].handler(db,human,a)).replayed,true);assert.equal(db.events.length,1);
});
test('cross-lead evidence rejected before mutation',async()=>{const db=new Fake();await assert.rejects(()=>TOOLS['update-lead'].handler(db,human,args({fields:{stage:'engaged'},stage_review:{reason:'Example',evidence_ids:[id(404)]}})),e=>e.payload.error==='stage_evidence_mismatch');assert.equal(db.updated,undefined)});
test('Undo records correction only for latest exact move and prior stage',async()=>{
 for(const [stage,event,valid] of [['outreach_active',id(20),true],['new',id(20),false],['outreach_active',id(404),false]]){const db=new Fake();const run=()=>TOOLS['update-lead'].handler(db,human,args({fields:{stage},stage_review:{reason:'Undo automatic stage move',evidence_ids:[],undo_event_id:event,human_quote:'Restore previous stage'}}));if(valid){await run();assert.equal(db.events[0].cause,'human_correction');assert.equal(db.events[0].new.stage_review.undo_event_id,id(20))}else{await assert.rejects(run,e=>e.payload.error==='undo_changed');assert.equal(db.updated,undefined)}}
});
test('Link exact client is human-only, versioned, audited and replayable; no party merge',async()=>{
 const db=new Fake();const a=args({client_id:id(800),confirmed:true});await TOOLS['link-lead-client'].handler(db,human,a);assert.equal(db.updated.p[0],id(800));assert.equal(db.events[0].new.client_id,id(800));assert.equal((await TOOLS['link-lead-client'].handler(db,human,a)).replayed,true);assert.equal(db.events.length,1);
 for(const fields of [{live_party:false},{is_client:true},{suppressed:true},{client_id:id(7)},{linked_client:true}]){const bad=new Fake();Object.assign(bad.row,fields);await assert.rejects(()=>TOOLS['link-lead-client'].handler(bad,human,a),e=>e.payload.error==='lead_not_linkable');assert.equal(bad.updated,undefined)}
 await assert.rejects(()=>TOOLS['link-lead-client'].handler(new Fake(),{...human,human:false},a),e=>e.payload.error==='human_confirmation_required');
});
test('Claim authenticated human on unowned New preserves stage; refuses claimed, suppressed, converted and stale leads',async()=>{
 const db=new Fake();await TOOLS['claim-lead'].handler(db,human,args());assert.equal(db.updated.p[0],human.id);assert.equal(db.events[0].field,'owner_id');assert.doesNotMatch(db.updated.s,/stage=/);
 for(const fields of [{live_party:false},{stage:'qualified'},{owner_id:id(2)},{client_id:id(2)},{is_client:true},{linked_client:true},{suppressed:true}]){const bad=new Fake();Object.assign(bad.row,fields);await assert.rejects(()=>TOOLS['claim-lead'].handler(bad,human,args()),e=>e.payload.error==='lead_not_claimable');assert.equal(bad.updated,undefined)}
 const stale=new Fake();stale.row.version=2;await assert.rejects(()=>TOOLS['claim-lead'].handler(stale,human,args()),e=>e.payload.error==='version_conflict');
});
test('Archived stage migration is explicit and never rewrites history',async()=>{const s=await readFile(new URL('../../migrations/0845_lead_archived_stage.sql',import.meta.url),'utf8');assert.match(s,/'archived'/);assert.match(s,/on conflict/);assert.doesNotMatch(s,/update (?:lead|event)\s|delete from/i)});

test('actor switch between read and command refuses all three writes before mutation',async()=>{for(const verb of ['claim-lead','link-lead-client','update-lead']){const db=new Fake();await assert.rejects(()=>TOOLS[verb].handler(db,human,args({expected_actor:'dell',client_id:id(800),confirmed:true,fields:{stage:'qualified'}})),e=>e.payload.error==='account_changed');assert.equal(db.updated,undefined)}});

test('lead and human-only merge contracts remain admitted', async()=>{
 await assertAdmits(['confirm-merge','claim-lead','link-lead-client','update-lead','lead-board']);
});
