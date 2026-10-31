import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, mkdir, rm, rename, readdir, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { discoverSuites } from '../suite-discovery.mjs';

test('discovery enforces its checked-in floor and diagnoses renamed or nested suites', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'suite-discovery-'));
  try {
    await Promise.all(Array.from({ length: 407 }, (_, n) => writeFile(path.join(directory, `${n}.test.mjs`), '')));
    assert.equal((await discoverSuites(directory, { expected: 407 })).length, 407);
    await rm(path.join(directory, '0.test.mjs'));
    await assert.rejects(discoverSuites(directory, { expected: 407 }), /found 406, expected at least 407; drop 1/);
    await mkdir(path.join(directory, 'nested'));
    await rename(path.join(directory, '1.test.mjs'), path.join(directory, 'nested/1.test.mjs'));
    await assert.rejects(discoverSuites(directory, { expected: 407 }), /omit nested\/1.test.mjs/);
    await rm(path.join(directory, 'nested'), { recursive: true });
    await writeFile(path.join(directory, 'renamed.mjs'), "import test from 'node:test'; test('lost', () => {});");
    await assert.rejects(discoverSuites(directory, { expected: 407 }), /omit renamed.mjs/);
    await rm(path.join(directory, 'renamed.mjs'));
    await writeFile(path.join(directory, 'unsupported.test.ts'), '');
    await assert.rejects(discoverSuites(directory, { expected: 407 }), /omit unsupported.test.ts/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});


test('the checked-in floor holds at this head and adding a suite needs no edit', async () => {
  const inventory = JSON.parse(await readFile(new URL('../suite-inventory.json', import.meta.url), 'utf8'));
  const suites = (await readdir(new URL('./', import.meta.url))).filter(name => /\.test\.(?:js|mjs)$/.test(name));
  assert.ok(inventory.expected <= suites.length, 'the checked-in floor must not exceed this head');
  const directory = await mkdtemp(path.join(tmpdir(), 'real-suite-inventory-'));
  try {
    await Promise.all(suites.map(name => writeFile(path.join(directory, name), '')));
    assert.equal((await discoverSuites(directory, inventory)).length, suites.length);
    await writeFile(path.join(directory, 'extra.test.mjs'), '');
    assert.equal((await discoverSuites(directory, inventory)).length, suites.length + 1);
    await rm(path.join(directory, 'extra.test.mjs'));
    const floor = { ...inventory, expected: suites.length };
    for (const name of suites) {
      await rename(path.join(directory, name), path.join(directory, name + '.disabled'));
      await assert.rejects(discoverSuites(directory, floor),
        { message: new RegExp(`found ${suites.length - 1}, expected at least ${suites.length}; drop 1`) }, name);
      await rename(path.join(directory, name + '.disabled'), path.join(directory, name));
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});
