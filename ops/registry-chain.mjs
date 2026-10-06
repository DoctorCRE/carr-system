import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { basename } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
}

export const registryChain = JSON.parse(readFileSync(new URL('./config/scac-registry-chain.json', import.meta.url), 'utf8'));
const digest = value => createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(canonicalize(value))).digest('hex');

export function registryVersion(version, chain = registryChain) {
  const found = chain.versions.find(row => row.version === version || row.number === version);
  if (!found) throw new Error(`unknown registry version: ${version}`);
  return found;
}

export function preservesRegistryChainHistory(before, after) {
  const same = (left, right) => JSON.stringify(canonicalize(left)) === JSON.stringify(canonicalize(right));
  const policy = chain => Object.fromEntries(Object.entries(chain).filter(([key]) =>
    !['versions', 'atomic_groups', 'strict_atomic_groups'].includes(key)));
  if (!same(policy(before), policy(after)) || after.versions.length < before.versions.length ||
      !same(after.versions.slice(0, before.versions.length), before.versions)) return false;
  const appended = after.versions.slice(before.versions.length);
  if (appended.some((row, i) => row.number !== before.versions.length + i + 1 ||
      row.version !== `scac-mutation-registry.v${row.number}` ||
      row.predecessor !== after.versions[row.number - 2]?.version)) return false;
  return same(after.atomic_groups, [...before.atomic_groups, ...appended.filter(row => row.atomic_pair.length).map(row => row.atomic_pair)]) &&
    same(after.strict_atomic_groups, [...before.strict_atomic_groups, ...appended.filter(row => row.strict_atomic).map(row => row.atomic_pair)]);
}

export function appendSuccessor({ rows, domainMigration, catalog, entrySetDigest, chain = registryChain }) {
  const predecessor = chain.versions.at(-1);
  const domains = Array.isArray(domainMigration) ? domainMigration : [domainMigration];
  const domain = domains.at(-1);
  domainMigration = domain.filename;
  if (typeof domain.sql !== 'string' || !domain.sql.trim()) throw new Error('domain migration bytes are required');
  if (!predecessor || !Array.isArray(rows) || !rows.length) throw new Error('successor requires source rows and a predecessor');
  if (!/^\d{4}[a-z]?_[a-z0-9_]+\.sql$/.test(basename(domainMigration))) throw new Error('invalid domain migration');
  if (!/^sha256:[0-9a-f]{64}$/.test(entrySetDigest)) throw new Error('successor requires a measured full entry-set digest');
  const number = predecessor.number + 1;
  const version = `scac-mutation-registry.v${number}`;
  const baseline = { ...catalog, projection_version: `scac-db-catalog-projection.v${number}` };
  const migration = domain.successor_filename || basename(domainMigration).replace(/^\d{4}[a-z]?_/, `${String(Number(basename(domainMigration).slice(0, 4)) + 1).padStart(4, '0')}_`).replace(/\.sql$/, '_scac_successor.sql');
  if (new Set(rows.map(row => row.ingress_key)).size !== rows.length) throw new Error('duplicate source ingress');
  if (basename(domainMigration) <= basename(predecessor.migration)) throw new Error('domain migration must follow its predecessor');
  const runtime = renderRuntimeProjection(rows, { version, dbCatalogBaseline: baseline });
  const current = {
    number, version, predecessor: predecessor.version,
    digest: `sha256:${digest({ schema_version: version, rows, db_catalog_baseline: baseline })}`,
    source_set_digest: `sha256:${digest(rows.map(row => `sha256:${digest(row)}`).sort().join(','))}`,
    catalog_digest: `sha256:${digest(baseline)}`, artifact_sha256: digest(runtime),
    path: 'mcp-server/src/scac-mutation-registry.current.generated.js', catalog: baseline,
    commit: execFileSync('git', ['rev-parse', 'HEAD'], {cwd: fileURLToPath(new URL('../', import.meta.url)), encoding: 'utf8'}).trim(),
    source_count: rows.length, entry_count: rows.length + ['secdef_execute', 'relation_dml', 'column_dml'].reduce((sum, key) => sum + baseline[key].count, 0),
    migration: `migrations/${migration}`, entry_set_digest: entrySetDigest,
    atomic_pair: [...domains.map(item => basename(item.filename)), migration], strict_atomic: true,
    dependencies: [basename(predecessor.migration), ...domains.map(item => basename(item.filename))],
    snapshot: {include_current_entry_set: true, catalog_function: `ops.scac_mutation_catalog_v${number}_current()`},
  };
  const template = readFileSync(new URL('../' + predecessor.migration, import.meta.url), 'utf8');
  if (digest(template) !== predecessor.migration_sha256) throw new Error('predecessor migration pin drifted');
  const request = { template, predecessor: { number: predecessor.number, digest: predecessor.digest,
    entry_count: predecessor.entry_count, source_count: predecessor.source_count, catalog: predecessor.catalog,
    entry_set: predecessor.entry_set_digest }, rows, baseline, entry_set: entrySetDigest,
    dependencies: [{ filename: basename(predecessor.migration), sql: template }, ...domains] };
  const sql = execFileSync('python3', ['-c',
    'import json,sys; from successor_generation import render_sql; r=json.load(sys.stdin); print(render_sql(r["template"],r["predecessor"],r["rows"],r["baseline"],r["entry_set"],r["dependencies"]),end="")'],
    { cwd: fileURLToPath(new URL('./', import.meta.url)), input: JSON.stringify(request), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  current.migration_sha256 = digest(sql);
  const fixture = JSON.parse(readFileSync(new URL('./config/scac-registry-source-inventory-fixtures.v1.json', import.meta.url), 'utf8'));
  const previous = new Map(fixture.base.rows.map(row => [row.ingress_key, row]));
  for (const patch of fixture.patches) {
    for (const key of patch.remove) previous.delete(key);
    for (const row of patch.upsert) previous.set(row.ingress_key, row);
    for (const [key, replacement] of Object.entries(patch.row_replacements || {})) previous.set(key, { ...previous.get(key), ...replacement });
    for (const [locator, hash] of Object.entries(patch.source_digest_replacements || {}))
      for (const [key, row] of previous) if (row.source_locator === locator) previous.set(key, { ...row, source_digest: hash });
    if (patch.version === `v${predecessor.number}`) break;
  }
  const keys = new Set(rows.map(row => row.ingress_key));
  fixture.patches.push({ version: `v${number}`, reason: 'Successor generated from source rows and measured disposable catalog.',
    remove: [...previous.keys()].filter(key => !keys.has(key)).sort(),
    upsert: rows.filter(row => JSON.stringify(previous.get(row.ingress_key)) !== JSON.stringify(row)),
    expected_count: rows.length, expected_sha256: createHash('sha256').update(JSON.stringify(rows)).digest('hex') });
  const seals = Object.fromEntries(chain.versions.filter(row => row.entry_set_digest).map(row => [row.version, row.entry_set_digest]));
  seals[version] = entrySetDigest;
  return { current, runtime, sql, fixture, seals, chain: { ...chain, versions: [...chain.versions, current],
    atomic_groups: [...chain.atomic_groups, current.atomic_pair], strict_atomic_groups: [...chain.strict_atomic_groups, current.atomic_pair] } };
}

export function renderRuntimeProjection(rows, {
  version = "scac-mutation-registry.v1",
  dbCatalogBaseline = registryVersion(1).catalog,
} = {}) {
  if (!/^scac-mutation-registry\.v[1-9][0-9]*$/.test(version) || Number(version.split('.v')[1]) > registryChain.versions.at(-1).number + 1)
    throw new Error(`unsupported SCAC mutation registry version: ${version}`);
  const registryDigest = digest({ schema_version: version, rows, db_catalog_baseline: dbCatalogBaseline });
  const sourceSetDigest = digest(rows.map(row => `sha256:${digest(row)}`).sort().join(","));
  const catalogBaselineDigest = digest(dbCatalogBaseline);
  const projection = Object.fromEntries(rows.filter(row => row.ingress_kind === "mcp_tool").map(row => [row.operation, {
    ingress_key: row.ingress_key,
    source_locator: row.source_locator,
    source_digest: row.source_digest,
    schema_digest: row.schema_digest,
    write: row.write,
    human_only: row.human_only,
    authority_only: row.authority_only,
    delegates_to: row.delegates_to,
  }]));
  return `// GENERATED by ops/scac-mutation-inventory.mjs. Review changes; never hand-edit.\n` +
    `// This is a non-authorizing source/build guard. The sealed DB registry is SIEP-11's sole metadata authority; SIEP-18 owns atomic admission.\n` +
    `export const SCAC_MUTATION_REGISTRY_VERSION = ${JSON.stringify(version)};\n` +
    `export const SCAC_MUTATION_REGISTRY_DIGEST = ${JSON.stringify(registryDigest)};\n` +
    `export const SCAC_MUTATION_SOURCE_CONTRACT_SET_DIGEST = ${JSON.stringify(sourceSetDigest)};\n` +
    `export const SCAC_MUTATION_DB_CATALOG_BASELINE_DIGEST = ${JSON.stringify(catalogBaselineDigest)};\n` +
    `export const SCAC_MUTATION_DB_METADATA_AUTHORITY = true;\n` +
    `export const SCAC_MUTATION_RUNTIME_PROJECTION_AUTHORIZING = false;\n` +
    `export const SCAC_MUTATION_OPERATIONS = Object.freeze(${JSON.stringify(projection, null, 2)});\n`;
}
