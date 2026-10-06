import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import { registryChain, appendSuccessor } from '../../ops/registry-chain.mjs';

test('append owns the successor seal, runtime projection and atomic migration pair', () => {
  const before = structuredClone(registryChain);
  const current = before.versions.at(-1);
  const rows = [{ ingress_key: 'mcp-tool:example', ingress_kind: 'mcp_tool', operation: 'example',
    source_locator: 'example.js', source_digest: 'a'.repeat(64), schema_digest: 'b'.repeat(64),
    write: false, human_only: false, authority_only: false, delegates_to: [] }];
  const catalog = { ...current.catalog, secdef_execute: { count: 2, digest: 'sha256:' + 'c'.repeat(64) },
    relation_dml: { count: 3, digest: 'sha256:' + 'd'.repeat(64) }, column_dml: { count: 0, digest: 'sha256:' + 'e'.repeat(64) } };
  const result = appendSuccessor({ rows, domainMigration: { filename: '0900_example.sql', sql: 'select 1;' }, catalog,
    entrySetDigest: 'sha256:' + 'f'.repeat(64), chain: before });
  assert.equal(result.current.number, current.number + 1);
  assert.equal(result.current.predecessor, current.version);
  assert.equal(result.current.source_count, 1);
  assert.equal(result.current.entry_count, 6);
  assert.deepEqual(result.current.atomic_pair, ['0900_example.sql', '0901_example_scac_successor.sql']);
  assert.match(result.sql, new RegExp(`scac_mutation_registry_v${current.number + 1}_seal_available`));
  assert.match(result.sql, /Successor dependency drifted: 0900_example.sql/);
  assert.match(result.runtime, /mcp-tool:example/);
  assert.match(result.runtime, new RegExp(result.current.digest.slice(7)));
  assert.deepEqual(result.chain.versions.slice(0, -1), before.versions);
  assert.deepEqual(before, registryChain, 'append does not mutate historical pins');
});

test('append handles the complete source set through the same interface', async () => {
  const {historicalRows} = await import('../../ops/registry-history.mjs');
  const current = registryChain.versions.at(-1);
  const rows = historicalRows(current.number);
  const result = appendSuccessor({rows, domainMigration:{filename:'0900_complete.sql',sql:'select 1;'},
    catalog:current.catalog, entrySetDigest:current.entry_set_digest});
  assert.ok(result.sql.length > 1024*1024, 'exercise the complete SQL seed across the compiler adapter');
  assert.equal(result.current.source_count, rows.length);
  assert.equal(result.fixture.patches.at(-1).expected_count, rows.length);
});

test('historical renderer migration aliases derive from the manifest pins', () => {
  const row = registryChain.versions[0];
  const changed = '0'.repeat(64);
  const result = execFileSync(process.execPath, ['--input-type=module', '-e', `
import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
const read = fs.readFileSync;
fs.readFileSync = (path, ...args) => {
  const bytes = read(path, ...args);
  if (!String(path).endsWith('scac-registry-chain.json')) return bytes;
  const chain = JSON.parse(bytes);
  chain.versions[0].migration_sha256 = ${JSON.stringify(changed)};
  return JSON.stringify(chain);
};
syncBuiltinESMExports();
const {HISTORICAL_REGISTRY_ARTIFACT_SHA256} = await import('./ops/scac-mutation-inventory.mjs');
process.stdout.write(HISTORICAL_REGISTRY_ARTIFACT_SHA256[${JSON.stringify(row.migration)}]);
`], {cwd: new URL('../../', import.meta.url), encoding: 'utf8'});
  assert.equal(result, changed);
});
