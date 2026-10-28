import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const checker = new URL('../check-ai-boundary.mjs', import.meta.url);

test('CI rejects direct, optional, aliased, destructured and computed AI binding access', () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'ai-boundary-'));
  try {
    for (const source of [
      'env.AI.run(model, input)', 'env?.AI?.run(model, input)',
      'const binding = env["AI"]; binding.run(model,input)',
      'const { AI: binding } = env; binding.run(model,input)',
      'const other = env; other.AI.run(model,input)',
      'env[`AI`].run(model,input)',
      'env["A" + "I"].run(model,input)',
      'const bindingName = "A" + "I"; env[bindingName].run(model,input)',
      'env[`A${"I"}`].run(model,input)',
    ]) {
      writeFileSync(path.join(directory, 'bypass.ts'), source);
      const result = spawnSync(process.execPath, [checker.pathname, directory], { encoding: 'utf8' });
      assert.equal(result.status, 1, result.stdout + result.stderr);
      assert.match(result.stdout + result.stderr, /bypass.ts.*AI binding/);
    }
    writeFileSync(path.join(directory, 'bypass.ts'), '// env.AI is forbidden\nclient.ai.run(model,input)');
    const result = spawnSync(process.execPath, [checker.pathname, directory], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stdout + result.stderr);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('all shipped Worker AI calls use the budget wrapper', () => {
  const result = spawnSync(process.execPath, [checker.pathname], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
