// Verify independent registration and strict committed operation records.
import test from 'node:test';
import assert from 'node:assert/strict';
import { composeOperations, orderFamilyIds } from '../src/registry-layout.js';

test('legacy initialization order precedes bytewise new descriptor IDs', () => {
  assert.deepEqual(orderFamilyIds(['z','schedule-board','vendor-meetings','census-inventory','A','practice-origination']),
    ['census-inventory','practice-origination','schedule-board','vendor-meetings','A','z']);
  assert.throws(() => orderFamilyIds(['census-inventory']), /missing/);
  assert.throws(() => orderFamilyIds(['census-inventory','census-inventory']), /duplicate/);
});
const row = {ingress_key:'mcp-tool:example',source_locator:'mcp-server/src/example.js',schema_digest:'a'.repeat(64),
  write:false,human_only:false,human_caller_only:false,authority_only:false,delegates_to:[]};
test('operation records validate identity, closed shape and authority types', () => {
  const operations=composeOperations([['example',row]]);
  assert.ok(Object.isFrozen(operations));
  assert.deepEqual(operations.example,row);
  for (const changed of [{...row,write:'false'},{...row,ingress_key:'mcp-tool:other'},{...row,schema_digest:'bad'},{...row,extra:true}])
    assert.throws(()=>composeOperations([['example',changed]]),/invalid/);
  assert.throws(()=>composeOperations([['example',row],['example',row]]),/duplicate/);
  assert.throws(()=>composeOperations([]),/empty/);
});

test('real admission refuses changed schema and every authority flag before invocation', async () => {
  const {TOOLS} = await import('../src/tools.js');
  const {assertRegisteredOperation} = await import('../src/mutation-registry.js');
  const tool = TOOLS['add-loop'];
  assert.ok(Object.isFrozen(TOOLS));
  assert.ok(Object.isFrozen(tool));
  assert.ok(Object.isFrozen(tool.inputSchema));
  for (const flag of ['write','humanOnly','humanCallerOnly','authorityOnly']) {
    const changed = {...tool,registrySource:tool.registrySource,[flag]:!(tool[flag] === true)};
    await assert.rejects(assertRegisteredOperation('add-loop',changed,{}), {error:'mutation_contract_mismatch'});
  }
  await assert.rejects(assertRegisteredOperation('add-loop',{...tool,registrySource:tool.registrySource,inputSchema:{type:'object'}},{}), {error:'mutation_contract_mismatch'});
});
