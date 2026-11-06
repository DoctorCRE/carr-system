"""Exercise meeting preparation through its exported JavaScript interface."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
JS = r'''
import assert from 'node:assert/strict';
import { addCalendarMeetingPrep } from './mcp-server/src/calendar-meeting-prep.js';
const mode = process.argv[1];
const section = {state:'ready', sources:{}, notes:[], items:[{title:'Synthetic meeting',
 possible_deals:[{id:'one',name:'Possible lease'},{id:'two',name:'Other lease'}]}]};
if(mode==='empty') section.items[0].possible_deals=[];
let active=false;
let releaseFailed=false;
const queries=[];
const client={inTransaction: !mode.startsWith('nontransaction'), query:async(sql,params)=>{
 if(sql==='savepoint calendar_meeting_prep') {
  if(mode==='savepoint-failure') throw Error('savepoint unavailable');
  assert.equal(active,false);active=true;queries.push('savepoint');
 }
 else if(sql==='rollback to savepoint calendar_meeting_prep') {
  queries.push('rollback'); assert.equal(active,true,'rollback requires live savepoint');
  if(mode==='recovery-failure') throw Error('unrecoverable transaction');
 } else if(sql==='release savepoint calendar_meeting_prep') {
  queries.push('release');assert.equal(active,true);
  if(mode==='release-error'&&!releaseFailed){releaseFailed=true;throw Error('private release error');}
  active=false;
 } else if(sql==='select 1') {queries.push('subsequent');assert.equal(active,false);return {rows:[{ok:1}]};}
 else {
  queries.push('select'); assert.deepEqual(params,[['one','two'],'joe']);
  assert.match(sql,/lower\(owner\)=\$2/);assert.match(sql,/limit 1000/);
  if(mode==='sql-error'||mode==='recovery-failure') throw Error('private database detail');
  if(mode==='projection'||mode==='nontransaction-projection') return {rows:null};
  const rows=[{id:'one',phase:'legal',next_step:'Review',next_date:null,last_touch:null,market:'Test'},
   {id:'two',phase:'research',next_step:null,next_date:null,last_touch:null,market:null}];
  if(mode==='mid-projection') Object.defineProperty(rows[1],'phase',{get(){throw Error('private projection detail');}});
  return {rows:mode==='missing'?rows.slice(0,1):rows};
 }
 return {rows:[]};
}};
if(mode==='savepoint-failure') {
 await assert.rejects(addCalendarMeetingPrep(client,section,'joe'),/savepoint unavailable/);
 assert.deepEqual(queries,[]);
} else if(mode==='recovery-failure') {
 await assert.rejects(addCalendarMeetingPrep(client,section,'joe'),/unrecoverable transaction/);
 assert.deepEqual(queries,['savepoint','select','rollback']);
} else {
 await addCalendarMeetingPrep(client,section,'joe');
 assert.equal(section.items[0].title,'Synthetic meeting');
 if(['projection','mid-projection','sql-error','nontransaction-projection','release-error'].includes(mode)) {
  assert.equal(section.state,'partial'); assert.deepEqual(section.sources.meeting_prep,{state:'unavailable'});
  for(const deal of section.items[0].possible_deals) assert.deepEqual(deal.prep,{state:'unavailable'});
  assert.equal(section.items[0].possible_deals[0].name,'Possible lease');
  assert.deepEqual(section.notes,['Meeting deal preparation could not be checked.']);
  assert.equal(JSON.stringify(section).includes('private'),false);
  assert.deepEqual(queries,mode==='release-error'?['savepoint','select','release','rollback','release']:
   client.inTransaction?['savepoint','select','rollback','release']:['select']);
 } else if(mode==='empty') {assert.deepEqual(queries,[]);assert.equal(section.sources.meeting_prep,undefined);}
 else {
  assert.deepEqual(queries,client.inTransaction?['savepoint','select','release']:['select']);
  assert.equal(section.items[0].possible_deals[0].prep.phase,'legal');
  assert.equal(section.sources.meeting_prep.state,mode==='missing'?'partial':'ready');
  if(mode==='missing') {
   assert.equal(section.state,'partial');assert.deepEqual(section.items[0].possible_deals[1].prep,{state:'unavailable'});
   assert.deepEqual(section.notes,['Preparation is unavailable for some possible deals. Check the deal record.']);
  }
 }
 await client.query('select 1');
}
console.log(JSON.stringify({mode,queries}));
'''


class SavepointLifetimeTests(unittest.TestCase):
    def run_mode(self, mode):
        result = subprocess.run(['node', '--input-type=module', '-e', JS, mode], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_projection_failure_retains_meeting_and_recovers(self):
        self.run_mode('projection')

    def test_mid_projection_resets_all_preparation(self):
        self.run_mode('mid-projection')

    def test_healthy_releases_once_and_continues(self):
        self.run_mode('healthy')

    def test_missing_deal_retains_partial_preparation(self):
        self.run_mode('missing')

    def test_sql_error_recovers_without_private_details(self):
        self.run_mode('sql-error')

    def test_recovery_failure_propagates(self):
        self.run_mode('recovery-failure')

    def test_savepoint_creation_failure_propagates(self):
        self.run_mode('savepoint-failure')

    def test_release_error_recovers_and_resets_preparation(self):
        self.run_mode('release-error')

    def test_no_deals_needs_no_savepoint(self):
        self.run_mode('empty')

    def test_nontransactional_projection_failure(self):
        self.run_mode('nontransaction-projection')

    def test_nontransactional_success(self):
        self.run_mode('nontransaction-healthy')


if __name__ == '__main__':
    unittest.main()
