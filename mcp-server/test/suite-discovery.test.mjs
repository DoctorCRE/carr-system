import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, mkdir, rm, rename } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { discoverSuites } from '../suite-discovery.mjs';

test('discovery enforces its checked-in floor and diagnoses renamed or nested suites', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'suite-discovery-'));
  try {
    await Promise.all(Array.from({ length: 407 }, (_, n) => writeFile(path.join(directory, `${n}.test.mjs`), '')));
    assert.equal((await discoverSuites(directory, { minimum: 407 })).length, 407);
    await rm(path.join(directory, '0.test.mjs'));
    await assert.rejects(discoverSuites(directory, { minimum: 407 }), /dropped to 406.*minimum 407, drop 1/);
    await mkdir(path.join(directory, 'nested'));
    await rename(path.join(directory, '1.test.mjs'), path.join(directory, 'nested/1.test.mjs'));
    await assert.rejects(discoverSuites(directory, { minimum: 407 }), /omit nested\/1.test.mjs/);
    await rm(path.join(directory, 'nested'), { recursive: true });
    await writeFile(path.join(directory, 'renamed.mjs'), "import test from 'node:test'; test('lost', () => {});");
    await assert.rejects(discoverSuites(directory, { minimum: 407 }), /omit renamed.mjs/);
    await rm(path.join(directory, 'renamed.mjs'));
    await writeFile(path.join(directory, 'unsupported.test.ts'), '');
    await assert.rejects(discoverSuites(directory, { minimum: 407 }), /omit unsupported.test.ts/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
