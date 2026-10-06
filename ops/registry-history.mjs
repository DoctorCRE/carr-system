import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {registryChain, registryVersion, renderRuntimeProjection} from './registry-chain.mjs';
const fixture = JSON.parse(readFileSync(new URL('./config/scac-registry-source-inventory-fixtures.v1.json', import.meta.url), 'utf8'));
const hash = value => createHash('sha256').update(value).digest('hex');

export function historicalRows(number, source = fixture) {
  const key = `v${number}`;
  if (number === 1) {
    const sql = readFileSync(new URL('../'+registryVersion(1).migration, import.meta.url), 'utf8');
    const seed = sql.match(/with seed as \(select value as contract from jsonb_array_elements\('([^\n]+)'::jsonb\)\)/);
    if (!seed) throw new Error('legacy registry seed is absent');
    return JSON.parse(seed[1].replaceAll("''", "'")).map(({entry_digest, ...row}) => row);
  }
  const rows = new Map(source.base.rows.map(row => [row.ingress_key, row]));
  let expected = source.base;
  if (!source.base.versions.includes(key)) {
    let found = false;
    for (const patch of source.patches) {
      for (const ingress of patch.remove) rows.delete(ingress);
      for (const row of patch.upsert) rows.set(row.ingress_key, row);
      for (const [ingress, replacement] of Object.entries(patch.row_replacements || {})) {
        if (!rows.has(ingress)) throw new Error('historical row replacement has no predecessor');
        rows.set(ingress, {...rows.get(ingress), ...replacement});
      }
      for (const [locator, digest] of Object.entries(patch.source_digest_replacements || {}))
        for (const [ingress, row] of rows) if (row.source_locator === locator) rows.set(ingress, {...row, source_digest: digest});
      expected = patch;
      if (patch.version === key) { found = true; break; }
    }
    if (!found) throw new Error(`historical patch absent: ${key}`);
  }
  const result = [...rows.values()].sort((a,b) => a.ingress_key.localeCompare(b.ingress_key));
  if (result.length !== expected.expected_count || hash(JSON.stringify(result)) !== expected.expected_sha256)
    throw new Error(`historical patch pin drifted: ${key}`);
  return result;
}

export function materializeRegistry(number, {chain = registryChain, source = fixture} = {}) {
  const pin = registryVersion(number, chain);
  let bytes = renderRuntimeProjection(historicalRows(pin.number, source), {version: pin.version, dbCatalogBaseline: pin.catalog});
  if (pin.number === 1) bytes = bytes.replace(/^export const SCAC_MUTATION_(?:SOURCE_CONTRACT_SET_DIGEST|DB_CATALOG_BASELINE_DIGEST) = .*;\n/gm, '');
  if (hash(bytes) !== pin.artifact_sha256) throw new Error(`registry artifact pin drifted: ${pin.version}`);
  return bytes;
}

// Historical artifact paths are read interfaces; no history is installed in the runtime tree.
export function readRegistryArtifact(path, encoding) {
  const filename = basename(path instanceof URL ? fileURLToPath(path) : String(path));
  const match = filename.match(/^scac-mutation-registry(?:\.v([1-9][0-9]*))?\.generated\.js$/);
  if (!match) return readFileSync(path, encoding);
  const bytes = materializeRegistry(Number(match[1] || 1));
  return encoding ? bytes : Buffer.from(bytes);
}
