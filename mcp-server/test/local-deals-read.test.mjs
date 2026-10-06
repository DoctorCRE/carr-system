import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { assertRegisteredOperation } from '../src/mutation-registry.js';
import { TOOLS } from '../src/tools.js';

test('deal-board exposes parking fields and excludes invoiced deals in its executed read', async () => {
  let query;
  const rows=[{id:'demo-1',operating_state:'parked',parking_note:'unverified import',invoiced_on:null}];
  const result = await TOOLS['deal-board'].handler({query:async sql => {query=sql;return {rows};}});
  assert.deepEqual(result.deals,rows);
  assert.match(query,/d\.operating_state, d\.parking_note/);
  assert.match(query,/join v_deal_room_board d on d\.id=b\.id/);
  assert.doesNotMatch(query,/join deal\b|invoiced_on is null/);
});

test('deal-room-board returns exact phase event identity and invoice marker in the same snapshot', async () => {
  let boardQuery;
  const row={id:'demo-1', operating_state:'active',phase_change:{event_id:'demo-event',prior_phase:'research',phase:'negotiation',automatic:true,reason:'LOI submitted',evidence_date:'2026-10-01'},invoiced_on:null};
  const result=await TOOLS['deal-room-board'].handler({query:async (sql,args) => {
    if(sql.includes('dealroom:board-field-base')) {boardQuery=sql;assert.equal(args[0],'team');return {rows:[row]};}
    return {rows:[]};
  }},{slug:'joe'},{workspace:'team'});
  assert.deepEqual(result.deals,[row]);
  assert.match(boardQuery,/v_deal_room_phase_change pc where pc\.deal_id=b\.id/);
  assert.match(boardQuery,/b\.invoiced_on/);
});

test('phase evidence projection selects the latest change, dates evidence independently and excludes undo automation',async () => {
  const sql=await readFile(new URL('../../migrations/0771_local_deal_board_evidence.sql',import.meta.url),'utf8');
  assert.match(sql,/distinct on \(e\.subject_id\)/);
  assert.match(sql,/order by e\.subject_id, e\.recorded_at desc, e\.id desc/);
  assert.match(sql,/e\.occurred_at/);
  assert.match(sql,/e\.verb <> 'revert-deal-field'/);
  assert.match(sql,/where d\.invoiced_on is null;/);
  assert.match(sql,/grant select on v_deal_room_phase_change to carr_reader, carr_writer/);
});

test('invoice read additions preserve sealed verb input schemas and authority flags', async () => {
  for (const name of ['find', 'deal-board', 'deal-room-board', 'get-deal-room', 'read-deal-reconciliation']) {
    const args = name === 'find' ? {query: 'Synthetic'} : name.endsWith('board') ? {} : {deal: 'aa000000-0000-4000-8000-000000000004'};
    const sealed = await assertRegisteredOperation(name, TOOLS[name], args);
    assert.equal(sealed.write, false, name);
    assert.equal(sealed.human_only, false, name);
    assert.equal(sealed.authority_only, false, name);
  }
});
