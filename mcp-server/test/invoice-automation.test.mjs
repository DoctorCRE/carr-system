import test from 'node:test';
import assert from 'node:assert/strict';
import {planInvoiceCloses,invoiceAutomation} from '../src/invoice-automation.js';
import {planLeadMoves} from '../src/lead-automation.js';
const now='2026-10-04T12:00:00Z', mailbox='invoices@example.test';
const invoice={id:'invoice-example',status:'captured',deal_name:'Synthetic Lease',client_name:'Synthetic Practice',
  occurred_at:'2026-10-03T18:00:00-05:00',email_date:'2026-10-03',from_address:mailbox,evidence_ref:'local-mail:synthetic-invoice'};
const deal={id:'deal-example',name:'Synthetic Lease',client_name:'Synthetic Practice',phase:'legal',version:4,
  invoiced_on:null,property_addresses:['123 Example Avenue, Suite 2']};
const plan=(i=invoice,d=[deal],m=mailbox)=>planInvoiceCloses([i],d,m,now)[0];
test('invoice closes require exact name and independent client or full premises match',()=>{
  assert.equal(plan().status,'applied');assert.equal(plan().reason,'Invoice received 2026-10-03');
  assert.equal(plan().evidence_ref,invoice.evidence_ref);assert.equal(plan().invoiced_on,'2026-10-03');
  assert.equal(plan({...invoice,client_name:null,property_address:'123 Example Avenue, Suite 2'}).status,'applied');
  assert.equal(plan({...invoice,client_name:null}).status,'proposed');
  assert.equal(plan({...invoice,client_name:'Other Practice'}).status,'proposed');
  assert.equal(plan({...invoice,deal_name:'Synthetic'}).status,'proposed');
  assert.equal(plan({...invoice,property_address:'123 Example Avenue, Suite 9'}).status,'proposed');
});
test('ambiguous, unrelated mailbox, future and conflicting invoice date observations remain proposals',()=>{
  assert.equal(plan(invoice,[deal,{...deal,id:'deal-other'}]).needs_confirmation,'Multiple deals match');
  assert.equal(plan({...invoice,from_address:'other@example.test'}).status,'proposed');
  assert.equal(plan(invoice,[deal],'').status,'proposed');
  assert.equal(plan({...invoice,occurred_at:'2027-01-01'}).status,'proposed');
  assert.equal(plan(invoice,[{...deal,invoiced_on:'2026-10-02'}]).status,'proposed');
  assert.equal(plan(invoice,[{...deal,invoiced_on:new Date('2026-10-03T00:00:00Z')}]).status,'applied');
  assert.deepEqual(planInvoiceCloses([{...invoice,status:'undone'}],[deal],mailbox,now),[]);
});
test('shared names are resolved by second field, normalized only for case and whitespace',()=>{
  const r=plan({...invoice,client_name:' synthetic   practice '},[{...deal,id:'other',client_name:'Other'},deal]);
  assert.equal(r.deal_id,deal.id);assert.equal(r.status,'applied');
});
test('all permanent exit reasons are proposed even with exact evidence; archived rows never move',()=>{
  const l={id:'lead-example',party_id:'party-example',stage:'nurture_drip',version:1,created_at:'2026-09-01',stage_since:'2026-10-01'};
  for(const archive_reason of ['retired','sold_to_platform','another_broker']) {
    const a={id:'activity-example',lead_id:l.id,kind:'email_in',source:'local_mail',occurred_at:'2026-10-03T12:00:00Z',
      detail:JSON.stringify({match:'exact',party_id:l.party_id,evidence_ref:'local-mail:synthetic-exit',automated:false,lead_stage_signal:'archived',archive_reason})};
    const r=planLeadMoves([l],[a],[],now)[0];assert.equal(r.status,'proposed');assert.equal(r.to_stage,'archived');assert.match(r.reason,/2026-10-03/);
    assert.deepEqual(planLeadMoves([{...l,stage:'archived'}],[a],[],now),[]);
  }
});
test('archive evidence takes precedence over an otherwise automatic forward move',()=>{
  const l={id:'lead',party_id:'party',stage:'new',version:1,created_at:'2026-09-01',event_confidence:'high',est_lease_event:'2027-01-01'};
  const a={id:'first',lead_id:l.id,kind:'email_in',source:'local_mail',occurred_at:'2026-10-02T12:00:00Z',
    detail:JSON.stringify({match:'exact',party_id:l.party_id,evidence_ref:'local-mail:synthetic',automated:false})};
  const exit={...a,id:'exit',occurred_at:'2026-10-03T12:00:00Z',detail:JSON.stringify({...JSON.parse(a.detail),lead_stage_signal:'archived',archive_reason:'retired'})};
  assert.equal(planLeadMoves([l],[a,exit],[],now)[0].to_stage,'archived');
});
test('capture preserves offset email date and refuses malformed local evidence',async()=>{
  const calls=[],events=[];const db={query:async(sql,p)=>{
    calls.push([sql,p]);if(sql==='select now() as now')return {rows:[{now}]};
    if(sql.startsWith('insert into deal_invoice_email'))return {rows:[{id:'invoice-example'}]};
    throw new Error(sql);
  }};
  const tools=invoiceAutomation({withEnvelope:async(c,a,v,args,f)=>f(),writeEvent:async(...a)=>events.push(a),ToolError:class extends Error{constructor(v){super(v.error);}}}).tools;
  const args={native_ref:invoice.evidence_ref,from_address:mailbox,deal_name:invoice.deal_name,client_name:invoice.client_name,occurred_at:'2026-10-03T23:30:00-05:00'};
  await tools['record-deal-invoice'].handler(db,{id:'synthetic'},args);
  assert.equal(calls.find(([s])=>s.startsWith('insert'))[1][6],'2026-10-03');assert.equal(events.length,1);
  for(const bad of [{native_ref:'remote-mail:synthetic'},{occurred_at:'2026-02-30T12:00:00Z'},{from_address:'bad'},{occurred_at:'2030-01-01T12:00:00Z'}])
    await assert.rejects(()=>tools['record-deal-invoice'].handler(db,{id:'synthetic'},{...args,...bad}),/invalid_invoice/);
});
test('ordered invoice planning simulates closes without mutating caller snapshots',()=>{
  const second={...invoice,id:'second',email_date:'2026-10-04',occurred_at:now};
  const batch=planInvoiceCloses([invoice,second],[deal],mailbox,now);
  assert.equal(batch[1].status,'proposed');
  assert.equal(batch[1].needs_confirmation,'Deal has a different invoice date');
  assert.equal(batch[1].from_phase,'closed');
  assert.equal(deal.phase,'legal');assert.equal(deal.version,4);
});
