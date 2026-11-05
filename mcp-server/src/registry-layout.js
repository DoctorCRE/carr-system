// Validate authored registry inputs shared by Node discovery and Worker assembly.
const legacyFamilyIds = ['census-inventory', 'practice-origination', 'schedule-board', 'vendor-meetings'];
export function orderFamilyIds(ids) {
  if (new Set(ids).size !== ids.length) throw new Error('duplicate family ID');
  for (const id of legacyFamilyIds) if (!ids.includes(id)) throw new Error(`missing family: ${id}`);
  return [...legacyFamilyIds, ...ids.filter(id => !legacyFamilyIds.includes(id)).sort()];
}
export function validateFamily(family, id) {
  if (!family || Object.keys(family).sort().join(',') !== 'createTools,source' ||
      typeof family.createTools !== 'function' ||
      !/^mcp-server\/src\/[\w./-]+\.(?:js|ts)$/.test(family.source))
    throw new Error(`invalid family descriptor: ${id}`);
  return family;
}
const fields = ['ingress_key','source_locator','schema_digest','write','human_only','human_caller_only','authority_only','delegates_to'].sort().join(',');
export function composeOperations(entries) {
  if (!entries.length) throw new Error('empty operation contracts');
  const operations = {};
  for (const [name,row] of entries) {
    if (Object.hasOwn(operations,name)) throw new Error(`duplicate operation: ${name}`);
    if (!/^[a-z][a-z0-9-]*$/.test(name) || !row || Object.keys(row).sort().join(',') !== fields ||
        row.ingress_key !== `mcp-tool:${name}` ||
        !/^mcp-server\/src\/[\w./-]+\.(?:js|ts)$/.test(row.source_locator) ||
        !/^[a-f0-9]{64}$/.test(row.schema_digest) ||
        !['write','human_only','human_caller_only','authority_only'].every(k=>typeof row[k] === 'boolean') ||
        !Array.isArray(row.delegates_to) || !row.delegates_to.every(x=>typeof x === 'string' && x.length))
      throw new Error(`invalid operation contract: ${name}`);
    operations[name] = row;
  }
  return Object.freeze(operations);
}
