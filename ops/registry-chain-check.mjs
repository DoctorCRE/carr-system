import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {basename} from 'node:path';
import {registryChain, preservesRegistryChainHistory} from './registry-chain.mjs';
import {historicalRows, materializeRegistry} from './registry-history.mjs';
const hash = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(key => [key,canonical(value[key])])) : value;
const digest = value => 'sha256:'+hash(JSON.stringify(canonical(value)));
const fail = message => { throw new Error('registry chain '+message); };

export function checkRegistryChain({chain=registryChain, before,
  readMigration=path => readFileSync(new URL('../'+path, import.meta.url),'utf8')} = {}) {
  if (chain.schema !== 'scac-registry-chain.v1' || !chain.versions.length) fail('continuity: unsupported or empty chain');
  for (const [i,row] of chain.versions.entries())
    if (row.number!==i+1 || row.version!==`scac-mutation-registry.v${i+1}` || row.predecessor!==(i ? chain.versions[i-1].version : null)) fail('continuity drifted');
  for (const group of chain.atomic_groups) {
    if (!group.length || new Set(group).size !== group.length || group.some((name,i) => i && name <= group[i-1]))
      fail('atomic pair order drifted');
  }
  const historyPreserved = !before || (before.versions.length <= chain.versions.length &&
    before.versions.every((row,index) => JSON.stringify(row)===JSON.stringify(chain.versions[index])));
  if (before && historyPreserved && !preservesRegistryChainHistory(before, chain)) fail('policy preservation drifted');
  for (const group of chain.atomic_groups) {
    if (!(chain.inactive_atomic_groups || []).some(item=>JSON.stringify(item.group)===JSON.stringify(group)))
      for (const name of group) readMigration('migrations/'+name);
  }
  for (const group of chain.strict_atomic_groups)
    if (!chain.atomic_groups.some(candidate => JSON.stringify(candidate)===JSON.stringify(group))) fail('strict atomic pair absent');
  let predecessor = null;
  for (const [index, row] of chain.versions.entries()) {
    if (row.number !== index+1 || row.version !== `scac-mutation-registry.v${index+1}` || row.predecessor !== predecessor)
      fail('continuity drifted');
    const bytes = materializeRegistry(row.number, {chain});
    const rows = historicalRows(row.number);
    const sourceSet = row.number === 1 ? null : 'sha256:'+hash(rows.map(digest).sort().join(','));
    const catalog = row.number === 1 ? null : digest(row.catalog);
    if (row.source_set_digest !== sourceSet) fail('source_set_digest drifted: '+row.version);
    if (row.catalog_digest !== catalog) fail('catalog_digest drifted: '+row.version);
    if (rows.length !== row.source_count || row.entry_count !== rows.length +
      ['secdef_execute','relation_dml','column_dml'].reduce((n,key)=>n+row.catalog[key].count,0)) fail('count drifted: '+row.version);
    if (digest({schema_version:row.version,rows,db_catalog_baseline:row.catalog}) !== row.digest || !bytes.includes(row.digest.slice(7))) fail('digest drifted: '+row.version);
    const sql = readMigration(row.migration);
    if (hash(sql)!==row.migration_sha256) fail('migration pin drifted: '+row.migration);
    if (!sql.includes(row.version) || !sql.includes(row.digest.slice(7))) fail('migration digest binding absent: '+row.version);
    for (const dependency of row.dependencies)
      if (dependency >= basename(row.migration)) fail('atomic dependency order drifted: '+row.version);
    if (row.atomic_pair.length && !chain.atomic_groups.some(group=>JSON.stringify(group)===JSON.stringify(row.atomic_pair))) fail('atomic pair absent: '+row.version);
    if (row.strict_atomic && !chain.strict_atomic_groups.some(group=>JSON.stringify(group)===JSON.stringify(row.atomic_pair))) fail('strict atomic pair absent: '+row.version);
    predecessor = row.version;
  }
  if (!historyPreserved) fail('history preservation drifted');
  const current = chain.versions.at(-1);
  const runtime = readFileSync(new URL('../mcp-server/src/scac-mutation-registry.current.generated.js',import.meta.url),'utf8');
  if (hash(runtime)!==current.artifact_sha256) fail('current artifact pin drifted');
  const seals = JSON.parse(readFileSync(new URL('./config/scac-registry-full-entry-set-seals.json',import.meta.url),'utf8'));
  for (const row of chain.versions) if (row.entry_set_digest !== seals[row.version]) fail('entry-set seal drifted: '+row.version);
  return {versions:chain.versions.length,current:current.version,atomic_groups:chain.atomic_groups.length};
}
