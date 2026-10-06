import test from 'node:test';
import assert from 'node:assert/strict';
import { invoiceTrackerTools } from '../src/invoice-tracker.js';
import { TOOLS } from '../src/tools.js';
import { assertAdmits } from './helpers/registry-admission.mjs';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
class ToolError extends Error { constructor(payload) { super(payload.error); this.payload=payload; } }
function harness(patch={}) {
  const row={id:'demo-commission',deal_id:'demo-deal',status:'invoiced',version:4,invoiced_on:'2026-09-01',received_on:null,today:'2026-10-02',...patch};
  const calls=[],events=[];const tools=invoiceTrackerTools({ToolError,withEnvelope:async(_c,_a,_v,_args,fn)=>fn(),writeEvent:async(...args)=>events.push(args)});
  const c={query:async(sql,args)=>{calls.push({sql,args}); if(sql.includes('for update'))return{rows:patch.missing?[]:[row]}; if(sql.includes('update commission'))return{rows:[{id:row.id,base_version:5,received_on:args[1]}]};return{rows:[row]};}};
  return{row,calls,events,c,tools};
}
const actor={id:'demo-actor',slug:'joe'};const args={commission_id:'demo-commission',base_version:4,received_on:'2026-10-01',idempotency_key:'demo-receipt'};
test('read returns lifecycle dates and explicit commission facts in a versioned single snapshot',async()=>{
 const h=harness();const result=await h.tools['read-invoice-tracker'].handler(h.c,actor,{});
 assert.equal(result.schema_version,'invoice-tracker.v1');assert.equal(result.actor,'joe');assert.equal(result.entries[0],h.row);
 for(const field of ['closed_on','invoiced_on','lane','outcome','gross_amount','received_on','due_on'])assert.match(h.calls[0].sql,new RegExp(field));
 assert.doesNotMatch(h.calls[0].sql,/won_value|sf_commission|source_row/);
});
test('paid date changes only one commission and records its attributed deal event',async()=>{
 const h=harness();assert.equal((await h.tools['record-commission-receipt'].handler(h.c,actor,args)).base_version,5);
 assert.equal(h.calls.length,2);assert.match(h.calls[0].sql,/for update/);assert.equal(h.events.length,1);
 assert.equal(h.events[0][3],'deal');assert.equal(h.events[0][4],'demo-deal');assert.equal(h.events[0][5].new.received_on,'2026-10-01');
 assert.doesNotMatch(h.calls[1].sql,/gross_amount|deal set|won_value/);
});
test('invalid dates, future dates, pre-invoice dates, stale versions and expected/received entries never write',async()=>{
 for(const [patch,change,error] of [[{},{received_on:'2026-02-30'},'invalid_received_on'],[{},{received_on:'2026-10-03'},'payment_date_out_of_range'],[{},{received_on:'2026-08-31'},'payment_date_out_of_range'],[{},{base_version:3},'version_conflict'],[{status:'expected'},{},'invoice_not_unpaid'],[{status:'received'},{},'invoice_not_unpaid'],[{invoiced_on:null},{},'invoice_not_unpaid'],[{missing:true},{},'invoice_not_found']]){
  const h=harness(patch);await assert.rejects(h.tools['record-commission-receipt'].handler(h.c,actor,{...args,...change}),e=>e.payload.error===error);assert.ok(h.calls.every(c=>!c.sql.includes('update commission')));assert.equal(h.events.length,0);
 }
});
test('registered paid action has a closed schema and no outbound provider authority',async()=>{
 await assertAdmits(['read-invoice-tracker','record-commission-receipt']);
 assert.equal(TOOLS['record-commission-receipt'].write,true);assert.equal(TOOLS['record-commission-receipt'].humanOnly,true);
 assert.deepEqual(Object.keys(TOOLS['record-commission-receipt'].inputSchema.properties),['idempotency_key','commission_id','base_version','received_on']);
});

test('all deal reads expose the four lifecycle fields without inventing a missing invoice date',async()=>{
 for(const name of ['deal-board','deal-room-board','get-deal-room','read-deal-reconciliation']){
  const sql=[];const row={id:'demo-deal',deal_id:'demo-deal',invoiced_on:null,closed_on:'2026-09-01',lane:'territory',outcome:'won'};
  const c={query:async(text)=>{sql.push(text);return{rows:[row]};}};
  const result=await TOOLS[name].handler(c,actor,{deal:'demo-deal',deal_ids:['demo-deal'],ids:['demo-deal'],limit:10});
  const lifecycle=sql.find(text=>text.includes('closed_on') && text.includes('invoiced_on'));
  assert.ok(lifecycle,`${name} reads lifecycle dates`);assert.match(lifecycle,/lane/);assert.match(lifecycle,/outcome/);
  assert.ok(JSON.stringify(result).includes('"invoiced_on":null'),`${name} preserves null`);
 }
});




test('shipped invoice read-fields migration retains its filename and bytes',()=>{
 const sql=readFileSync(new URL('../../migrations/0782_deal_invoice_read_fields.sql',import.meta.url));
 assert.equal(createHash('sha256').update(sql).digest('hex'),'feb7748c3ce6f07afeb48507c145928a353dab8c5a8e96fb34fd69c7b32c7a0b');
});
