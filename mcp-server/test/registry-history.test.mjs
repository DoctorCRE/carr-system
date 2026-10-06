import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {registryChain} from '../../ops/registry-chain.mjs';
import {materializeRegistry} from '../../ops/registry-history.mjs';

test('every historical artifact materializes from patches and verifies its byte pin', () => {
  for (const row of registryChain.versions) {
    const bytes = materializeRegistry(row.number);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), row.artifact_sha256, row.version);
    assert.ok(bytes.includes(row.digest.slice(7)), row.version);
  }
});
test('a modified historical byte pin refuses materialization', () => {
  const chain = structuredClone(registryChain);
  chain.versions[0].artifact_sha256 = '0'.repeat(64);
  assert.throws(() => materializeRegistry(1, {chain}), /artifact pin/);
});
