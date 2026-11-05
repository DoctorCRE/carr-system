"""Verify recorded comps names, proposal dates and record provenance."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import assert from 'node:assert/strict';
import {recordedCompsTable, validateAnswerBlocks} from './mcp-server/src/answer-blocks.js';
class ToolError extends Error {}
const ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222'];
let calls=0;
const c={query:async(sql,args)=>{
 calls++;
 assert.match(sql,/join public\.deal/);
 assert.match(sql,/order by n.proposed_on desc, n.id limit 200/);
 assert.deepEqual(args,[ids]);


 assert.match(sql,/any\(\$1::uuid\[\]\)/);
 return {rows:[{missing:false,comps:ids.map((deal_id,i)=>({id:ids[1-i],deal_id,deal_name:'Synthetic Harbor',proposed_on:i?null:'2026-11-01',side:i?'tenant':'landlord',rate_norm_sf_yr:'28.5'}))}]};
}};
const table=await recordedCompsTable(c,ids,ToolError);
assert.equal(calls,1);
assert.equal(table.rows[0].deal.value,'Synthetic Harbor');
assert.deepEqual(table.rows.map(r=>r.deal.source.record_id),ids);
assert.equal(table.columns.find(c=>c.key==='proposed_on').type,'date');
assert.equal(table.columns.find(c=>c.key==='proposed_on').label,'Proposed on');
assert.equal(table.rows[0].proposed_on.value,'2026-11-01');
assert.equal(table.rows[1].proposed_on.value,null);
assert.equal(table.rows[0].rent.value,28.5);
assert.equal(table.rows[0].rent.source.record_id,ids[1]);
assert.equal(table.rows[1].side.value,'tenant');
validateAnswerBlocks([table],ToolError);
assert.deepEqual((await recordedCompsTable({query:async()=>({rows:[{missing:false,comps:[]}]})},[],ToolError)).rows,[]);
await assert.rejects(()=>recordedCompsTable({query:async()=>({rows:[{missing:true,comps:[]}]})},ids,ToolError),ToolError);
console.log(JSON.stringify(table));
'''

class RecordedCompsTest(unittest.TestCase):
    def test_projection_and_query_contract(self):
        result = subprocess.run(['node', '--input-type=module', '-e', SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['rows']), 2)

if __name__ == '__main__':
    unittest.main()
