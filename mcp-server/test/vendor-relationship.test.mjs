import test from 'node:test';
import assert from 'node:assert/strict';
import { computedTrust, enrichRelationship, trustedOverride, dealEvidenceEntries, mergeRelationshipFields } from '../src/vendor-relationship.js';
import { parseBusinessQuery, readBusinessList } from '../src/workspace-business-read.js';
const now='2026-10-01T12:00:00Z';
const proven={coverage_verified_at:now,deals_referred:5,deals_worked:8,won:7,lost:1,first_worked_at:'2024-09-01T12:00:00Z',last_contacted_at:'2026-09-01T12:00:00Z'};
test('trust tiers require all four criteria and never convert absent history to zero',()=>{
 assert.equal(computedTrust(proven,now),'Proven');assert.equal(computedTrust({...proven,deals_worked:3,won:2,lost:1,first_worked_at:'2025-09-01'},now),'Established');
 for(const update of [{deals_worked:2},{won:0,lost:8},{first_worked_at:'2026-09-01'},{last_contacted_at:'2025-09-01'},{last_contacted_at:'2027-09-01'}])assert.equal(computedTrust({...proven,...update},now),'Trial');
 assert.equal(computedTrust({...proven,coverage_verified_at:null},now),'Unrated');assert.equal(computedTrust(null,now),'Unrated');
 const unknown=enrichRelationship({...proven,coverage_verified_at:null},now);assert.equal(unknown.deals_referred,null);assert.equal(unknown.deals_worked,null);assert.equal(unknown.win_rate,null);
 assert.equal(enrichRelationship({...proven,won:0,lost:0},now).win_rate,null);assert.equal(enrichRelationship(proven,now).win_rate,7/8);
});
test('override keeps computed tier and stamps verified actor and reason; clients cannot provide authority',()=>{
 const override=trustedOverride({tier:'Trial',reason:' Synthetic review '},{slug:'dell',human:true},now);
 assert.deepEqual(override,{tier:'Trial',reason:'Synthetic review',recorded_by:'dell',recorded_at:now});
 const result=enrichRelationship({...proven,override},now);assert.equal(result.computed_tier,'Proven');assert.equal(result.override.tier,'Trial');
 for(const value of [{tier:'Core',reason:'x'},{tier:'Trial',reason:' '},{tier:'Trial',reason:'x',recorded_by:'joe'}])assert.throws(()=>trustedOverride(value,{slug:'joe',human:true}));
 assert.throws(()=>trustedOverride({tier:'Trial',reason:'x'},{slug:'other'}));assert.throws(()=>trustedOverride(null,{slug:'joe',human:false}));assert.equal(trustedOverride(null,{slug:'joe',human:true}),null);
});
test('deal associations require exact IDs, explicit role, date and sourced evidence; duplicates refuse',()=>{
 const entry={deal_id:'00000000-0000-4000-8000-000000000001',role:'worked',occurred_at:now,evidence_kind:'salesforce',evidence_ref:'synthetic-row-1'};
 assert.deepEqual(dealEvidenceEntries([entry]),[{...entry,occurred_at:new Date(now).toISOString()}]);
 for(const update of [{deal_id:'near name'},{role:'works_with'},{evidence_kind:'guess'},{evidence_ref:''},{occurred_at:'yesterday'}])assert.throws(()=>dealEvidenceEntries([{...entry,...update}]));
 assert.throws(()=>dealEvidenceEntries([entry,entry]));
});

test('evidence rejects impossible calendars and coercible non-timestamps and canonicalizes ISO values', () => {
 const entry={deal_id:'abcdefab-cdef-4abc-8abc-abcdefabcdef',role:'worked',occurred_at:now,evidence_kind:'entry',evidence_ref:'synthetic'};
 for(const occurred_at of [0,'1','2026-02-30','2026-02-31T12:00:00Z','2025-02-29','2026-04-31','2026-13-01','2026-01-01T24:00:00Z','2026-01-01T12:60:00Z','2026-01-01T12:00:00'])
  assert.throws(()=>dealEvidenceEntries([{...entry,occurred_at}]),e=>e.code==='deal_evidence_invalid',String(occurred_at));
 for(const [occurred_at,expected] of [['2024-02-29','2024-02-29T00:00:00.000Z'],['2026-10-01T12:00:00+02:00','2026-10-01T10:00:00.000Z']])
  assert.equal(dealEvidenceEntries([{...entry,occurred_at}])[0].occurred_at,expected);
});

test('deal UUID spelling is canonical before duplicate detection for both roles', () => {
 for(const role of ['worked','referred']) {
  const entry={deal_id:'abcdefab-cdef-4abc-8abc-abcdefabcdef',role,occurred_at:now,evidence_kind:'entry',evidence_ref:'synthetic'};
  assert.throws(()=>dealEvidenceEntries([entry,{...entry,deal_id:entry.deal_id.toUpperCase()}]),e=>e.code==='deal_evidence_duplicate');
  assert.equal(dealEvidenceEntries([{...entry,deal_id:entry.deal_id.toUpperCase()}])[0].deal_id,entry.deal_id);
 }
});

test('merge compares evidence values independent of object key order and refuses an oversized union', () => {
 const entry={deal_id:'abcdefab-cdef-4abc-8abc-abcdefabcdef',role:'worked',occurred_at:now,evidence_kind:'entry',evidence_ref:'synthetic'};
 const survivor={deal_evidence:[entry],deal_history_verified_at:now};
 const reordered=Object.fromEntries(Object.entries(entry).reverse());
 const merged=mergeRelationshipFields(survivor,{...survivor,deal_evidence:[reordered]});
 assert.deepEqual(merged.conflicts,[]);assert.equal(merged.filled.deal_history_verified_at,now);
 const entries=Array.from({length:100},(_,i)=>({...entry,deal_id:`00000000-0000-4000-8000-${String(i).padStart(12,'0')}`}));
 assert.throws(()=>mergeRelationshipFields({deal_evidence:entries},{deal_evidence:[entry]}),e=>e.code==='deal_evidence_invalid');
});

test('relationship authority has one scoped implementation used by override and attestation', async () => {
 const {readFile}=await import('node:fs/promises');
 const relationship=await readFile(new URL('../src/vendor-relationship.js',import.meta.url),'utf8');
 const tools=await readFile(new URL('../src/tools.js',import.meta.url),'utf8');
 const writer=tools.slice(tools.indexOf('"update-vendor":'),tools.indexOf('"update-lead":'));
 assert.doesNotMatch(writer,/actor\.human|includes\(actor\.slug\)/);
 assert.match(relationship,/requireRelationshipPartner/);
});
test('all sorts and owner/territory are server queries with deterministic ties and parameterized values',async()=>{
 for(const dataset of ['clients','vendors'])for(const sort of ['name','vertical','deal_type','last_deal_desc','last_deal_asc',...(dataset==='vendors'?['territory']:[])]){
  const query=parseBusinessQuery(dataset,new URLSearchParams({sort,owner:'dell',...(dataset==='vendors'?{territory:'Demo North'}:{})}),'joe');const calls=[];
  const client={async query(sql,values){calls.push({sql,values});return {rows:sql.startsWith('with filtered')?[{total_count:0,rows:[],viewer_owner_resolved:true}]:[{}]};}};
  await readBusinessList({client,actor:{slug:'joe',human:true},query,correlationId:'synthetic',now:()=>new Date(now)});
  assert.ok(calls[0].values.includes('dell'));assert.doesNotMatch(calls[0].sql,/Demo North/);assert.match(calls[0].sql,/f\.id/);
  if(dataset==='vendors'){assert.ok(calls[0].values.includes('Demo North'));assert.match(calls[0].sql,/v\.deal_evidence/);assert.match(calls[0].sql,/e\.outcome='won'/);assert.match(calls[0].sql,/e\.outcome='lost'/);assert.match(calls[0].sql,/public\.party_link_deal r/);assert.match(calls[0].sql,/dc\.merged_into is null/);}
 }
});

test('expanded revision is opt-in; legacy consumers retain their exact payload shape',async()=>{
 const row={id:'demo',relationship:{coverage_verified_at:null,deals_referred:0,deals_worked:0,won:0,lost:0,first_worked_at:null,last_contacted_at:null,last_contact_note:null,override:null,recent_entries:[],introductions:[]},vertical:'dental',deal_type:'Banking',last_deal_at:null,territory:'Demo North'};
 const client={async query(sql){return {rows:sql.startsWith('with filtered')?[{total_count:1,rows:[row],viewer_owner_resolved:true}]:[{categories:[],stages:[],dispositions:[],territories:[{slug:'Demo North',label:'Demo North'}]}]};}};
 const read=contract=>readBusinessList({client,actor:{slug:'joe'},query:parseBusinessQuery('vendors',new URLSearchParams(contract?{contract}:{}),'joe'),correlationId:'demo'});
 const legacy=await read(null);assert.equal(Object.hasOwn(legacy.rows[0],'relationship'),false);assert.equal(Object.hasOwn(legacy.facets,'territories'),false);
 const expanded=await read('vendor-directory.v1');assert.equal(expanded.rows[0].relationship.computed_tier,'Unrated');assert.equal(expanded.facets.territories[0].slug,'Demo North');
 assert.throws(()=>parseBusinessQuery('vendors',new URLSearchParams({contract:'unknown'}),'joe'));
});
