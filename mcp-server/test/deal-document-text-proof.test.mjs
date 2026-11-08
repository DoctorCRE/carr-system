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

test('release-aware proof admits equal and descendant, refuses older unrelated and unknown, and checks content', () => {
  const folder = mkdtempSync(path.join(tmpdir(), 'document-release-proof-'));
  const git = (...args) => {
    const result = spawnSync('git', args, { cwd: folder, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout.trim();
  };
  try {
    git('init', '--quiet');
    git('config', 'user.email', 'synthetic@example.test');
    git('config', 'user.name', 'Synthetic');
    const commit = name => { git('-c', 'core.hooksPath=/dev/null', 'commit', '--quiet', '--allow-empty', '-m', name); return git('rev-parse', 'HEAD'); };
    const older = commit('older');
    const expected = commit('merge');
    const descendant = commit('descendant');
    git('checkout', '--quiet', '--orphan', 'unrelated');
    const unrelated = commit('unrelated');
    writeFileSync(path.join(folder, 'original'), original);
    for (const [revision, text, status] of [[expected, 'first', 0], [descendant, 'first', 0],
      [older, 'first', 78], [unrelated, 'first', 78], ['0'.repeat(40), 'first', 78],
      [expected, 'wrong', 1], [expected, 'first extra', 1], [null, 'first', 78]]) {
      writeFileSync(path.join(folder, 'release.json'), JSON.stringify({ ok: true, env: { value: 'production' }, git_sha: { value: revision } }));
      writeFileSync(path.join(folder, 'expected'), text);
      const result = spawnSync(process.env.CARR_CI_PYTHON || 'python3', [
        new URL('../../tools/prove-deal-document-text.py', import.meta.url).pathname,
        '--expected-merge', expected, '--release', path.join(folder, 'release.json'), '--repository', folder,
        '--response', responsePath.pathname, '--original', path.join(folder, 'original'),
        '--expected-text', path.join(folder, 'expected')], { encoding: 'utf8' });
      assert.equal(result.status, status, result.stdout + result.stderr);
      assert.equal(JSON.parse(result.stdout).status, status === 0 ? 'passed' : status === 1 ? 'assertion_failure' : 'insufficient_evidence');
    }
  } finally { rmSync(folder, { recursive: true, force: true }); }
});
