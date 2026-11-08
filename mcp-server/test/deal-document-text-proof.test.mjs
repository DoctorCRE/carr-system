import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { checkProof } from '../../tools/check-deal-document-text-proof.mjs';

const responsePath = new URL('../../docs/contracts/deal-document-text.v1/produced.json', import.meta.url);
const response = JSON.parse(readFileSync(responsePath));
const original = Buffer.from('synthetic original PDF bytes');
test('proof validates recorded contract, original hash and exact text', () => {
  assert.equal(checkProof({ response, original, expectedText: 'first' }).ok, true);
  assert.throws(() => checkProof({ response, original: Buffer.from('changed'), expectedText: 'first' }), /original_hash_mismatch/);
  assert.throws(() => checkProof({ response, original, expectedText: 'first extra' }), /text_mismatch/);
  assert.throws(() => checkProof({ response: { ok: true }, original, expectedText: 'first' }), /response_contract/);
});
test('proof CLI succeeds and fails using recorded registered response', () => {
  const folder = mkdtempSync(path.join(tmpdir(), 'document-proof-'));
  try {
    writeFileSync(path.join(folder, 'original'), original);
    for (const [text, status] of [['first', 0], ['wrong', 1]]) {
      writeFileSync(path.join(folder, 'expected'), text);
      const result = spawnSync(process.execPath, [new URL('../../tools/check-deal-document-text-proof.mjs', import.meta.url).pathname,
        responsePath.pathname, path.join(folder, 'original'), path.join(folder, 'expected')], { encoding: 'utf8' });
      assert.equal(result.status, status, result.stderr);
    }
  } finally { rmSync(folder, { recursive: true, force: true }); }
});
