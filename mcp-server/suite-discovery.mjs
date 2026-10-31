// Discover the unit contract and reject tests that the runner would otherwise omit.
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

export async function discoverSuites(directory, { expected, external = [] }) {
  const files = [];
  const omitted = [];
  async function visit(relative = '') {
    for (const entry of await readdir(path.join(directory, relative), { withFileTypes: true })) {
      const name = path.posix.join(relative, entry.name);
      if (entry.isDirectory()) { await visit(name); continue; }
      if (!entry.isFile() || !/\.(?:[cm]?[jt]sx?)$/.test(name)) continue;
      if (!relative && /\.test\.(?:mjs|js)$/.test(name)) { files.push(name); continue; }
      // Separate database acceptance scripts are owned by ops/ci-gates.py.
      if (external.includes(name)) continue;
      const source = await readFile(path.join(directory, name), 'utf8');
      if (/\.(?:test|spec)\.(?:[cm]?[jt]sx?)$/.test(name)
          || (/['"]node:test['"]/.test(source) && /\b(?:test|describe|it)\s*(?:\.\s*(?:skip|only|todo))?\s*\(/.test(source)))
        omitted.push(name);
    }
  }
  await visit();
  if (omitted.length) throw new Error(`Unit discovery would omit ${omitted.sort().join(', ')}; put unit suites directly in test/ and name them *.test.mjs or *.test.js.`);
  if (!Number.isInteger(expected) || expected < 1) throw new Error('Invalid checked-in unit suite count');
  if (files.length !== expected)
    throw new Error(`Unit suite inventory mismatch: found ${files.length}, expected ${expected}; ${files.length < expected ? 'drop' : 'increase'} ${Math.abs(expected - files.length)}. Check deleted, renamed or nested suites and update the checked-in count deliberately.`);
  return files.sort();
}
