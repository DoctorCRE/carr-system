import { createHash } from "node:crypto";
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { leadAutomationTools, planLeadMoves, nextMorning } from "../src/lead-automation.js";
import { TOOLS } from "../src/tools.js";

const NOW="2026-10-01T18:00:00Z";
const lead={id:"lead-example",party_id:"party-example",stage:"new",version:3,suppressed:false,party_merged:false,
  created_at:"2026-09-01T12:00:00Z",stage_since:"2026-09-30T12:00:00Z",event_confidence:"high",est_lease_event:"2027-01-01"};
const contact={id:"activity-example",lead_id:lead.id,kind:"email_in",source:"local_mail",occurred_at:"2026-10-01T12:00:00Z",owed:null,
  detail:JSON.stringify({match:"exact",party_id:lead.party_id,evidence_ref:"local-mail:synthetic-001",automated:false})};
const change=(a,fields)=>({...a,detail:JSON.stringify({...JSON.parse(a.detail),...fields})});
const plan=(l=lead,a=contact,d=[])=>planLeadMoves([l],[a],d,NOW);

test("exact reply plus verified lease evidence qualifies with provenance",()=>{
  assert.deepEqual(plan()[0],{lead_id:lead.id,party_id:lead.party_id,from_stage:"new",to_stage:"qualified",base_version:3,
    reason:"Reply received 2026-10-01",activity_id:contact.id,evidence_ref:"local-mail:synthetic-001",strength:"strong",status:"applied"});
});
test("weak, unbound and missing lease evidence produces proposals",()=>{
  for(const a of [change(contact,{match:"domain"}),change(contact,{party_id:"other"}),change(contact,{evidence_ref:null}),{...contact,owed:"identity"}])
    assert.equal(plan(lead,a)[0].status,"proposed");
  for(const l of [{...lead,event_confidence:"low"},{...lead,est_lease_event:null}]) assert.equal(plan(l)[0].status,"proposed");
});
test("suppression, retired identity, unrelated, future, pre-lead and automated evidence cannot advance",()=>{
  for(const l of [{...lead,suppressed:true},{...lead,party_merged:true},{...lead,party_suppressed:true},{...lead,stage:"do_not_contact"},{...lead,stage:"opportunity"}]) assert.deepEqual(plan(l),[]);
  for(const a of [{...contact,lead_id:"other"},{...contact,occurred_at:"2028-01-01"},{...contact,occurred_at:"bad"},
    {...contact,occurred_at:"2020-01-01"},{...contact,source:"stated"},change(contact,{automated:true}),change(contact,{automated:undefined})]) assert.deepEqual(plan(lead,a),[]);
});
test("calendar requires attendance and an ended event",()=>{
  const meeting=change({...contact,kind:"meeting",source:"calendar"},{attended:true,ended_at:"2026-10-01T13:00:00Z"});
  assert.equal(plan(lead,meeting)[0].status,"applied");
  for(const m of [{attended:false},{ended_at:"2028-01-01"},{ended_at:null},{ended_at:"2026-10-01T11:00:00Z"}]) assert.deepEqual(plan(lead,change(meeting,m)),[]);
});
test("only matched, approved, due first-contact mail proves Outreach Active",()=>{
  const l={...lead,stage:"qualified"};
  const sent=change({...contact,kind:"email_out"},{first_contact_draft_id:"draft-example",draft_body_sha256:createHash("sha256").update("Synthetic draft body").digest("hex")});
  const d={body:"Synthetic draft body",id:"draft-example",lead_id:lead.id,party_id:lead.party_id,approved_at:"2026-09-30T12:00:00Z",scheduled_for:"2026-10-01T11:00:00Z"};
  assert.equal(plan(l,sent,[d])[0].to_stage,"outreach_active");
  assert.equal(plan(l,sent,[{...d,body:"Different content"}])[0].status,"proposed");
  for(const draft of [{...d,approved_at:null},{...d,party_id:"other"},{...d,id:"other"},{...d,approved_at:"2026-10-01T13:00:00Z"},{...d,scheduled_for:"2026-10-02T11:00:00Z"}]) assert.deepEqual(plan(l,sent,[draft]),[]);
  assert.deepEqual(plan(l,contact,[d]),[],"approval and inbound mail alone are not a send");
});
test("engagement needs fresh two-way evidence, nurture/opportunity semantics remain proposed",()=>{
  assert.equal(plan({...lead,stage:"outreach_active"})[0].to_stage,"engaged");
  assert.deepEqual(plan({...lead,stage:"outreach_active",stage_since:NOW}),[]);
  for(const signal of ["nurture_drip","opportunity"]){
    const result=plan({...lead,stage:"engaged"},change(contact,{lead_stage_signal:signal}))[0];
    assert.equal(result.to_stage,signal);assert.equal(result.status,"proposed");
  }
  assert.deepEqual(plan({...lead,stage:"engaged"}),[],"silence is never nurture");
});
test("next local day 06:00 handles clock boundaries and both DST changes",()=>{
  for(const [now,tz,expected] of [
    [NOW,"America/Chicago","2026-10-02T11:00:00.000Z"],
    ["2026-03-07T23:00:00Z","America/Chicago","2026-03-08T11:00:00.000Z"],
    ["2026-10-31T23:00:00Z","America/Chicago","2026-11-01T12:00:00.000Z"],
    ["2026-10-01T02:00:00Z","America/Chicago","2026-10-01T11:00:00.000Z"],
    [NOW,"Asia/Kolkata","2026-10-02T00:30:00.000Z"]]) assert.equal(nextMorning(now,tz),expected);
  assert.throws(()=>nextMorning(NOW,"invalid/zone"));
});

class Fake {
  constructor(){this.sql=[];this.moves=[];this.drafts=[];this.events=[];this.l={...lead};}
  async query(text,p=[]){
    const sql=text.replace(/\s+/g," ").trim();this.sql.push(sql);
    if(/^(savepoint|release savepoint|rollback to savepoint)/.test(sql))return {rows:[]};
    if(sql==="select now() as now")return {rows:[{now:NOW}]};
    if(sql.startsWith("select id from lead order"))return {rows:[{id:lead.id}]};
    if(sql.startsWith("select l.*, (p.merged_into"))return {rows:[this.l]};
    if(sql.startsWith("select a.* from activity"))return {rows:[contact]};
    if(sql.startsWith("select * from lead_contact_draft"))return {rows:this.drafts};
    if(sql.startsWith("insert into lead_stage_move")){
      if(this.moves.length)return {rows:[]};this.moves.push(p);return {rows:[{id:"move-example"}]};
    }
    if(sql.startsWith("update lead set stage")){this.l.stage=p[0];return {rows:[{id:lead.id}],rowCount:1};}
    if(sql.startsWith("select l.id,l.party_id,l.owner_id"))return {rows:this.l.stage==="qualified"&&!this.drafts.length?[{id:lead.id,party_id:lead.party_id,name:"Example Practice",owner_id:null}]:[]};
    if(sql.startsWith("insert into lead_contact_draft")){this.drafts.push({id:"draft-example",lead_id:p[0],party_id:p[1],scheduled_for:p[5]});return {rows:[{id:'draft-example'}],rowCount:1};}
    if(sql.startsWith("select d.* from lead_contact_draft d join lead"))return {rows:this.drafts};
    if(sql.startsWith("update lead_contact_draft")){this.drafts[0].approved_at=NOW;return {rows:[]};}
    if(sql.startsWith("select d.*,l.registry_ref"))return {rows:this.drafts};
    if(sql.startsWith("select m.*,l.version as base_version,l.registry_ref"))return {rows:this.moves};
    if(sql.startsWith("select m.*,l.version"))return {rows:this.proposal?[this.proposal]:[]};
    if(sql.startsWith("update lead_stage_move"))return {rows:[]};
    if(sql.startsWith("select l.id,l.party_id,p.email"))return {rows:[{...lead,email:"contact@example.test"}]};
    if(sql.startsWith("insert into activity")){this.capture=p;return {rows:[{id:"captured-example"}]};}
    if(sql.startsWith("select s.key as search_key"))return {rows:this.search?[this.search]:[]};
    throw new Error(`unexpected fake query ${sql}`);
  }
}
const human={id:"actor-example",human:true};
const tools=leadAutomationTools({invoices:{preview:async()=>[],apply:async()=>[]},withEnvelope:async(c,a,v,args,f)=>f(),writeEvent:async(c,...args)=>c.events.push(args),ToolError:class extends Error{constructor(value){super(value.error);}}});
test("dry runs issue SELECT only, use the same planner and never prepare drafts",async()=>{
  for(const [name,args] of [["lead-stage-preview",{}],["advance-leads",{idempotency_key:"synthetic-preview",dry_run:true}]]){
    const db=new Fake();const r=await tools[name].handler(db,human,args);
    assert.equal(r.moves[0].to_stage,"qualified");assert.ok(db.sql.every(s=>s.startsWith("select")));assert.deepEqual(db.drafts,[]);assert.deepEqual(db.events,[]);
  }
});
test("job writes provenance and one draft; a repeated run does not recreate either",async()=>{
  const db=new Fake();const r=await tools["advance-leads"].handler(db,human,{idempotency_key:"synthetic-job"});
  assert.equal(r.sent,false);assert.equal(db.l.stage,"qualified");assert.equal(db.drafts[0].scheduled_for,"2026-10-02T11:00:00.000Z");
  assert.equal(db.events[0][4].new.evidence_ref,"local-mail:synthetic-001");
  await tools["advance-leads"].handler(db,human,{idempotency_key:"synthetic-job-2"});assert.equal(db.moves.length,1);assert.equal(db.drafts.length,1);
  assert.ok(db.sql.some(s=>s.startsWith("select l.*")&&s.includes("for update of l,p")));
});
test("existing applied evidence is not returned as a newly applied move",async()=>{
  const db=new Fake();db.moves=[['already-applied']];
  const result=await tools['advance-leads'].handler(db,human,{idempotency_key:'synthetic-conflict'});
  assert.deepEqual(result.moves,[]);assert.deepEqual(db.events,[]);assert.equal(db.l.stage,'new');
});
test("draft approval is human-only, makes no stage move and cannot send",async()=>{
  const db=new Fake();db.drafts=[{id:"draft-example",lead_id:lead.id}];
  await assert.rejects(()=>tools["approve-lead-draft"].handler(db,{...human,human:false},{draft_id:"draft-example"}),/human_approval/);
  const r=await tools["approve-lead-draft"].handler(db,human,{draft_id:"draft-example"});assert.equal(r.sent,false);assert.equal(db.l.stage,"new");
  await assert.rejects(()=>tools["approve-lead-draft"].handler(db,human,{draft_id:"draft-example"}),/not_pending/);
});
test("proposal approval rejects stale state/version and records its original reference",async()=>{
  const db=new Fake();db.proposal={id:"move-example",lead_id:lead.id,from_stage:"engaged",to_stage:"nurture_drip",version:4,stage:"engaged",activity_id:contact.id,evidence_ref:"local-mail:synthetic-002"};
  await assert.rejects(()=>tools["approve-lead-move"].handler(db,human,{move_id:"move-example",base_version:3}),/stale/);
  const r=await tools["approve-lead-move"].handler(db,human,{move_id:"move-example",base_version:4});assert.equal(r.stage,"nurture_drip");assert.equal(r.evidence_ref,db.proposal.evidence_ref);
});
test("local capture derives exact matching from the stored party address and omits address from activity/event",async()=>{
  const args={lead:"L-EXAMPLE",native_ref:"local-mail:synthetic-003",counterparty_address:"CONTACT@example.test",kind:"email_in",occurred_at:"2026-10-01T12:00:00Z",automated:false};
  const db=new Fake();const r=await tools["record-lead-contact"].handler(db,human,args);assert.equal(r.match,"exact");assert.equal(db.capture[7],"local_mail");
  assert.equal(JSON.stringify(db.capture).includes("@"),false);assert.equal(JSON.stringify(db.events).includes("@"),false);
  const weak=new Fake();assert.equal((await tools["record-lead-contact"].handler(weak,human,{...args,counterparty_address:"other@example.test"})).match,"unconfirmed");
  await assert.rejects(()=>tools["record-lead-contact"].handler(new Fake(),human,{...args,occurred_at:"2030-01-01"}),/invalid_contact/);
});
test("approval queue and last-search read are registered; never-run and failed-run outcomes differ",async()=>{
  const db=new Fake();assert.deepEqual(await tools["lead-approval-queue"].handler(db,human,{}),{contract:"lead-automation.v1",drafts:[],moves:[]});
  assert.equal((await tools["last-new-lead-search"].handler(db)).last_run,null);
  db.search={timestamp:NOW,outcome:"failed",search_key:"npi-sweep-weekly",receipt_ref:"job:synthetic"};
  assert.equal((await tools["last-new-lead-search"].handler(db)).last_run.outcome,"failed");
  for(const name of Object.keys(tools))assert.ok(TOOLS[name],name);
  assert.equal(TOOLS["approve-lead-draft"].humanOnly,true);
});
test("database migration has draft-only constraints and finite forward transitions",()=>{
  const sql=readFileSync(new URL("../../migrations/0811_lead_stage_automation.sql",import.meta.url),"utf8");
  assert.match(sql,/check \(requires_human_send\)/);assert.match(sql,/check \(not dispatchable\)/);
  assert.match(sql,/unique\(lead_id,from_stage,to_stage,activity_id\)/);assert.doesNotMatch(sql,/grant.*delete/i);
});


test("older strong evidence is not hidden by a newer weak match",()=>{
  const weak=change({...contact,id:"weak-example",occurred_at:"2026-10-01T17:00:00Z"},{match:"unconfirmed"});
  const moves=planLeadMoves([lead],[weak,contact],[],NOW);
  assert.equal(moves[0].status,"applied");assert.equal(moves[0].activity_id,contact.id);
});

test("dated inbound deferral and held opportunity tour provide strong terminal moves",()=>{
  const l={...lead,stage:"engaged"};
  const defer=change(contact,{lead_stage_signal:"nurture_drip",follow_up_after:"2027-01-01"});
  assert.equal(plan(l,defer)[0].status,"applied");
  assert.equal(plan(l,change(defer,{follow_up_after:"2020-01-01"}))[0].status,"proposed");
  assert.equal(plan(l,change(defer,{match:"unconfirmed"}))[0].status,"proposed");
  const tour=change({...contact,kind:"tour",source:"calendar"},{lead_stage_signal:"opportunity",attended:true,ended_at:"2026-10-01T13:00:00Z"});
  assert.equal(plan(l,tour)[0].status,"applied");
  assert.equal(plan(l,change(tour,{attended:false}))[0].status,"proposed");
});

test("malformed contact metadata cannot break the whole job",()=>{
  for(const detail of ['null','[]','1','"text"','invalid']) assert.deepEqual(plan(lead,{...contact,detail}),[]);
});

// A merge may advance the registry, but may never replace a sealed predecessor.
test("lead successor preserves human-only party merges and all v105 MCP contracts", async () => {
  const { frozenInventory, boundInventoryRows, CURRENT_REGISTRY_VERSION } = await import("../../ops/scac-mutation-inventory.mjs");
  const { SCAC_MUTATION_REGISTRY_VERSION, registeredOperation } = await import("../src/mutation-registry.js");
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, CURRENT_REGISTRY_VERSION);
  const before = frozenInventory("scac-mutation-registry.v105");
  const after = frozenInventory("scac-mutation-registry.v106");
  const byKey = new Map(boundInventoryRows(after).map(row => [row.ingress_key, row]));
  for (const row of boundInventoryRows(before).filter(row => row.ingress_key.startsWith("mcp-tool:"))) assert.deepEqual(byKey.get(row.ingress_key), row);
  // The workspace extends contact evidence; automation authority stays sealed.
  const current = new Map(boundInventoryRows(frozenInventory(CURRENT_REGISTRY_VERSION)).map(row => [row.ingress_key, row]));
  for (const name of ["advance-leads", "approve-lead-draft", "approve-lead-move"])
    assert.deepEqual(current.get(`mcp-tool:${name}`), byKey.get(`mcp-tool:${name}`));
  const { schema_digest: beforeSchema, ...beforeContact } = byKey.get("mcp-tool:record-lead-contact");
  const { schema_digest: currentSchema, ...currentContact } = current.get("mcp-tool:record-lead-contact");
  assert.deepEqual(currentContact, beforeContact);
  assert.notEqual(currentSchema, beforeSchema);
  assert.equal(registeredOperation("confirm-merge").human_only, true);
  for (const name of ["advance-leads", "approve-lead-draft", "approve-lead-move"])
    assert.ok(registeredOperation(name), `${name} must remain registered`);
});
