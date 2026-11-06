import test from 'node:test';
import assert from 'node:assert/strict';
import { readMailNeedsPartner } from '../src/mail-needs-partner.ts';
test('empty mailbox differs from source unavailable', async () => {
  const actor = { id: '11111111-1111-4111-8111-111111111111', slug: 'joe', human: true };
  assert.equal((await readMailNeedsPartner({ query: async () => ({ rows: [] }) }, actor, {})).state, 'empty');
  assert.equal((await readMailNeedsPartner({ query: async () => { throw Error('outage'); } }, actor, {})).state, 'unavailable');
});
import { parseMailNeedsPartnerQuery, parseMailNeedsPartnerResponse } from '../src/mail-needs-partner.ts';
import { TOOLS } from '../src/tools.js';
const joe = { id: '11111111-1111-4111-8111-111111111111', slug: 'joe', human: true };
const row = { id:'22222222-2222-4222-8222-222222222222', version:3, kind:'next_step', activity_id:'33333333-3333-4333-8333-333333333333', source_version:2, next_date:null, occurred_at:null, priority:2 };
test('malformed arguments and cursors need input without reading', async () => {
  const c = {query: async () => assert.fail('must not read')};
  for (const args of [{owner:'dell'}, {limit:0}, {limit:51}, {limit:'2'}, {cursor:''}, {cursor:'bad'}, {cursor:btoa(JSON.stringify({v:1,owner:'dell',day:'2026-01-01',id:row.id}))}])
    await assert.rejects(readMailNeedsPartner(c,joe,args), /arguments_invalid/);
  assert.equal(parseMailNeedsPartnerQuery({limit:50}).limit,50);
});
test('unknown subjects are unauthorized without reading', async () => {
  for (const actor of [{slug:'stranger',human:true}, {slug:'codex',human:false,sponsoring_human_slug:'joe'}])
    await assert.rejects(readMailNeedsPartner({query:async()=>assert.fail('read')},actor,{}), /unauthorized/);
});
test('ready preserves versions, reasons, exact command and unknowns with bounded paging', async () => {
  let values;
  const c = {query:async (_sql,args) => { values=args;return {rows:[row,{...row,id:'44444444-4444-4444-8444-444444444444'}]}; }};
  const first = await TOOLS['read-mail-needs-partner'].handler(c,joe,{limit:1});
  assert.equal(TOOLS['read-mail-needs-partner'].write,false);
  assert.equal(first.state,'ready');assert.equal(values[4],2);assert.equal(values[0],'joe');
  assert.equal(first.items[0].source.instant_status,'unknown');
  assert.equal(first.items[0].action.verb,'confirm-email-next-step');assert.equal(first.items[0].action.args.base_version,3);
  assert.deepEqual(first.items[0].reason_codes,['decision_date_unknown']);
  await readMailNeedsPartner(c,joe,{limit:1,cursor:first.next_cursor});assert.equal(values[3],row.id);
  await readMailNeedsPartner(c,{...joe,slug:'dell'},{});assert.equal(values[0],'dell');
  assert.throws(()=>parseMailNeedsPartnerResponse({...first,items:[{...first.items[0],status:'sent'}]}));
  assert.throws(()=>parseMailNeedsPartnerResponse({...first,items:[{...first.items[0],source:{}}]}));
});
