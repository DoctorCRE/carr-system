import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';

let fixture, c, actor, party, lead;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'lead-follow-up-', snapshot: true });
  c = new pg.Client(fixture.connection);
  await c.connect();
  actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  party = randomUUID(); lead = randomUUID();
  await c.query(`insert into party(id,kind,name,specialty,city,county,state,created_by,updated_by)
    values($1,'org','Synthetic Dental','Dental','Demo Bay','Demo County','FL',$2,$2)`, [party, actor.id]);
  await c.query(`insert into lead(id,party_id,stage,created_by,updated_by) values($1,$2,'new',$3,$3)`, [lead, party, actor.id]);
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });
async function read(verb, args = {}) {
  await c.query('begin read only');
  try {
    await c.query(TOOLS[verb]?.writerConnection ? 'set local role carr_writer' : 'set local role carr_reader');
    const result = await executeRegisteredTool(c, actor, verb, args);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}

test('every lead has safe handling advice and deeper strategy, including suppressed and terminal leads', async () => {
  for (const stage of ['new','qualified','engaged','nurture_drip','opportunity','active_deal','closed_won','closed_lost','archived','do_not_contact']) {
    await c.query('update lead set stage=$2,suppressed=$3 where id=$1', [lead, stage, stage === 'do_not_contact']);
    const result = await read('lead-board', stage === 'do_not_contact' ? {} : { workspace: 'leads', lead_id: lead });
    const row = result.leads.find(row => row.id === lead);
    assert.equal(typeof row.handling_advice, 'string');
    assert.ok(row.handling_advice.length > 20);
    assert.equal(typeof row.strategy.objective, 'string');
    assert.ok(row.strategy.discovery.length >= 3);
    assert.equal(row.strategy.sent, false);
    if (result.detail) assert.equal(result.detail.handling_advice, row.handling_advice);
    if (stage === 'opportunity' || stage === 'active_deal') assert.match(row.handling_advice, /Coordinate with the linked client and deal/);
    if (stage === 'closed_won') assert.match(row.handling_advice, /closed won/i);
    if (stage === 'closed_lost') assert.match(row.handling_advice, /closed lost/i);
    if (stage === 'do_not_contact' || stage === 'archived') assert.match(row.handling_advice, /Do not contact/);
  }
  await c.query("update lead set stage='new',suppressed=false where id=$1", [lead]);
  const legacy = await read('lead-board');
  assert.ok(legacy.leads.find(row => row.id === lead).strategy);
});

test('prospect research checks handed records before work, preserving contacts and expired or conflicting evidence', async () => {
  const owner = randomUUID();
  await c.query(`insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic site owner',$2,$2)`, [owner, actor.id]);
  const before = (await c.query('select count(*) n from lead')).rows[0].n;
  const contact = await read('prospect-research', { subject: owner });
  assert.equal(contact.relationship, 'contact');
  assert.equal(contact.research.state, 'needs_research');
  assert.equal((await c.query('select count(*) n from lead')).rows[0].n, before);
  const add = async (kind, value, expiry) => c.query(`insert into record_flag(subject_type,subject_id,kind,value,source,expires_on,created_by)
    values('party',$1,$2,$3,'https://example.test/registry/1',$4,$5)`, [party, kind, value, expiry, actor.id]);
  await add('verified', { found: true, checked: ['identity'] }, '2000-01-01');
  assert.equal((await read('prospect-research', { subject: lead })).research.state, 'needs_research');
  await add('verified', { found: true, checked: ['identity','practice'] }, '2099-01-01');
  const current = await read('prospect-research', { subject: lead });
  assert.equal(current.research.state, 'verified');
  assert.ok(current.research.evidence.some(e => e.source === 'https://example.test/registry/1' && !e.expired));
  await add('discrepancy', { found: true, note: 'Synthetic conflicting practice affiliation' }, '2099-01-01');
  assert.equal((await read('prospect-research', { subject: lead })).research.state, 'needs_review');
  const detail = (await read('lead-board', { workspace: 'leads', lead_id: lead })).detail;
  assert.equal(detail.research.state, 'needs_review');
  await assert.rejects(() => read('prospect-research', { subject: randomUUID() }), e => e.payload.error === 'prospect_not_found');
});

test('practice map groups canonical practice snapshots by vertical and geography without minting leads', async () => {
  const duplicate = randomUUID(), other = randomUUID();
  await c.query(`insert into party(id,kind,name,specialty,city,state,created_by,updated_by,merged_into)
    values($1,'org','Synthetic old practice','Dental','Demo Bay','FL',$3,$3,$4),
      ($2,'org','Synthetic Vision','Optometry','Demo Bay','FL',$3,$3,null)`, [duplicate, other, actor.id, party]);
  const before = (await c.query('select count(*) n from lead')).rows[0].n;
  const result = await read('market-practice-map', { vertical: 'Dental', state: 'FL' });
  assert.equal(result.coverage, 'recorded_practices_only');
  assert.deepEqual(result.practices.map(p => p.id), [party]);
  assert.equal(result.practices[0].vertical, 'Dental');
  assert.equal(result.practices[0].city, 'Demo Bay');
  assert.equal(result.practices[0].evidence_ref, `party:${party}`);
  assert.ok(result.practices[0].research.evidence);
  assert.equal((await c.query('select count(*) n from lead')).rows[0].n, before);
  const empty = await read('market-practice-map', { vertical: 'No synthetic match' });
  assert.deepEqual(empty.practices, []);
});

test('ideal client profile mines only closed-won deals with distinct client counts and traceable evidence', async () => {
  const client = randomUUID();
  await c.query(`insert into client(id,party_id,vertical,status,created_by,updated_by)
    values($1,$2,'Dental','engaged',$3,$3)`, [client, party, actor.id]);
  const won = [randomUUID(),randomUUID()], lost = randomUUID(), open = randomUUID();
  for (const [id, phase, outcome] of [[won[0],'closed','won'],[won[1],'closed','won'],[lost,'closed','lost'],[open,'research',null]]) {
    await c.query(`insert into deal(id,client_id,name,deal_type,phase,outcome,closed_on,city,segment,created_by,updated_by)
      values($1,$2,'Synthetic deal','renewal',$3,$4,case when $3='closed' then current_date end,'Demo Bay','Dental',$5,$5)`, [id,client,phase,outcome,actor.id]);
  }
  const profile = await read('ideal-client-profile', { vertical: 'Dental', city: 'Demo Bay' });
  assert.equal(profile.coverage, 'recorded_closed_won_only');
  assert.equal(profile.profiles.length, 1);
  assert.equal(profile.profiles[0].won_deals, 2);
  assert.equal(profile.profiles[0].clients, 1);
  assert.deepEqual(profile.profiles[0].evidence.map(e => e.deal_id).sort(), won.sort());
  assert.equal(profile.profiles[0].hypothesis, 'Historical fit, not a prediction or qualification rule.');
  assert.deepEqual((await read('ideal-client-profile', { vertical: 'No synthetic match' })).profiles, []);
});

test('territory board reports unknown, dated sourced conditions and conflicts without treating stale evidence as current', async () => {
  const query = { city: 'Demo Bay', state: 'FL' };
  const board = async () => (await read('territory-state-board', query)).submarkets[0];
  assert.equal((await board()).condition, 'unknown');
  const add = async (condition, expiry) => c.query(`insert into record_flag(subject_type,subject_id,kind,value,source,expires_on,created_by)
    values('party',$1,'submarket_state',$2,'https://example.test/market/snapshot',$3,$4)`,
    [party, { found: true, condition, city: 'Demo Bay', state: 'FL' }, expiry, actor.id]);
  await add('tight', '2000-01-01');
  assert.equal((await board()).condition, 'unknown');
  await add('soft', '2099-01-01');
  const sourced = await board();
  assert.equal(sourced.condition, 'soft');
  assert.equal(sourced.evidence.length, 2);
  assert.ok(sourced.evidence.every(e => e.flag_id && e.source && e.observed_at));
  assert.equal(sourced.evidence.filter(e => !e.expired).length, 1);
  await add('tight', '2099-01-01');
  assert.equal((await board()).condition, 'conflicting');
  assert.deepEqual((await read('territory-state-board', { city: 'No synthetic match' })).submarkets, []);
});

test('quiet lead and vendor cadence proposes dated follow-ups but respects existing tasks, suppression and cold clients', async () => {
  const vendorParty = randomUUID(), vendor = randomUUID();
  await c.query(`insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic broker',$2,$2)`, [vendorParty,actor.id]);
  await c.query(`insert into vendor(id,party_id,category,stage,disposition,last_touch,owner_id,created_by,updated_by)
    values($1,$2,'broker',(select slug from vendor_stage order by sort limit 1),'active',current_date-100,$3,$3,$3)`, [vendor,vendorParty,actor.id]);
  await c.query('update lead set last_touch=current_date-60,owner_id=$2 where id=$1', [lead,actor.id]);
  const queue = () => read('quiet-follow-ups', { lead_days: 45, vendor_days: 90 });
  let result = await queue();
  assert.equal(result.sent, false);
  assert.equal(result.proposals.find(row => row.subject_id === lead).days_overdue, 15);
  assert.equal(result.proposals.find(row => row.subject_id === vendor).days_overdue, 10);
  const defaults = await read('quiet-follow-ups');
  assert.deepEqual(defaults.cadence, { lead_days: 45, vendor_days: 90 });
  assert.deepEqual(defaults.proposals, result.proposals);
  await c.query('update lead set next_action_date=current_date+20 where id=$1', [lead]);
  assert.ok(!(await queue()).proposals.some(row => row.subject_id === lead));
  await c.query('update lead set next_action_date=null where id=$1', [lead]);
  const action = randomUUID();
  await c.query(`insert into next_action(id,subject_type,subject_id,owner_id,due_on,description,created_by,updated_by)
    values($1,'lead',$2,$3,current_date+20,'Synthetic existing promise',$3,$3)`, [action,lead,actor.id]);
  assert.ok(!(await queue()).proposals.some(row => row.subject_id === lead));
  await c.query('delete from next_action where id=$1', [action]);
  await c.query('update lead set suppressed=true where id=$1', [lead]);
  assert.ok(!(await queue()).proposals.some(row => row.subject_id === lead));
  await c.query('update lead set suppressed=false,client_id=(select id from client where party_id=$2 limit 1) where id=$1', [lead,party]);
  if (!(await c.query('select client_id from lead where id=$1', [lead])).rows[0].client_id) {
    const client = randomUUID();
    await c.query(`insert into client(id,party_id,status,created_by,updated_by) values($1,$2,'cold',$3,$3)`, [client,party,actor.id]);
    await c.query('update lead set client_id=$2 where id=$1', [lead,client]);
  }
  await c.query("update client set status='cold' where party_id=$1", [party]);
  assert.ok(!(await queue()).proposals.some(row => row.subject_id === lead));
  await c.query("update party set contact_state='do_not_contact' where id=$1", [vendorParty]);
  assert.ok(!(await queue()).proposals.some(row => row.subject_id === vendor));
});

test('legacy board advice respects party-level contact restrictions even when lead stage is active', async () => {
  await c.query("update party set contact_state='do_not_contact' where id=$1", [party]);
  try {
    const result = await read('lead-board');
    assert.match(result.leads.find(row => row.id === lead).handling_advice, /Do not contact/);
  } finally { await c.query("update party set contact_state='active' where id=$1", [party]); }
});


test('party contact state controls advice on both boards and quiet lead and vendor proposals', async () => {
  const vendor = randomUUID();
  await c.query(`insert into vendor(id,party_id,category,stage,disposition,last_touch,created_by,updated_by)
    values($1,$2,'broker',(select slug from vendor_stage order by sort limit 1),'active',current_date-100,$3,$3)`, [vendor,party,actor.id]);
  await c.query("update lead set stage='nurture_drip',suppressed=false,client_id=null,last_touch=current_date-60 where id=$1", [lead]);
  try {
    for (const state of ['nurture','paused','do_not_contact','active']) {
      await c.query('update party set contact_state=$2 where id=$1', [party,state]);
      const contactable = state === 'active' || state === 'nurture';
      for (const args of [{}, { workspace: 'leads', lead_id: lead }]) {
        const result = await read('lead-board', args);
        const row = result.leads.find(row => row.id === lead);
        assert.equal(row.contact_eligible, contactable, state);
        assert.match(row.handling_advice, contactable ? /Respect the requested timing and return with useful, relevant information/ : /Do not contact/, state);
        if (result.detail) assert.equal(result.detail.handling_advice, row.handling_advice);
      }
      const quiet = await read('quiet-follow-ups');
      for (const id of [lead,vendor]) assert.equal(quiet.proposals.some(row => row.subject_id === id), contactable, state);
    }
  } finally { await c.query("update party set contact_state='active' where id=$1", [party]); }
});
