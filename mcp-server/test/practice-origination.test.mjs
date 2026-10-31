// Verify dated practice evidence through the registered import and read contracts.
import test from 'node:test';
import assert from 'node:assert/strict';
import { practiceOriginationTools } from '../src/practice-origination.ts';

const tools = practiceOriginationTools({withEnvelope: async (_c,_a,_v,_args,fn) => fn()});
const actor = {id:'00000000-0000-4000-8000-000000000001'};
test('coverage explicitly reports disabled formation and recorder feeds', async () => {
  const db = {query:async () => ({rows:[]})};
  const result = await tools['list-practice-origination'].handler(db,actor,{});
  assert.equal(result.contract,'practice-origination.v1');
  assert.equal(result.coverage.find(x=>x.source==='recorder-baldwin-al').status,'unavailable');
  assert.equal(result.coverage.find(x=>x.source==='recorder-other').status,'uncovered');
  assert.equal(result.coverage.find(x=>x.source==='pecos').status,'unavailable');
});
