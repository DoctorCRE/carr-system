import assert from 'node:assert/strict';
import {TOOLS} from '../../src/tools.js';
import {assertRegisteredOperation} from '../../src/mutation-registry.js';

export async function assertAdmits(verbs) {
  for (const name of verbs) {
    const tool = TOOLS[name];
    const admitted = await assertRegisteredOperation(name, tool, {});
    assert.equal(admitted.ingress_key, `mcp-tool:${name}`);
    assert.equal(admitted.write, tool.write === true);
    assert.equal(admitted.human_only, tool.humanOnly === true);
    assert.equal(admitted.authority_only, tool.authorityOnly === true);
  }
}
