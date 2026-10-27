// Load the probe file, expand neutral aliases, and resolve package field paths.
import { readFile } from 'node:fs/promises';

const KINDS = Object.freeze(['dom', 'interaction', 'link', 'media', 'replaced', 'http']);
const SITES = Object.freeze({ A: 'REF-A', V: 'REF-V' });
const ALIAS = /@\{([a-z0-9-]+)\}/g;

export const siteOf = id => SITES[id[0]];

function fail(id, message) { throw new Error(`probe ${id}: ${message}`); }

function checkProbe(probe, aliases) {
  const { id, kind } = probe;
  if (!/^[AV]-[GSTLA]\d{2}$/.test(id ?? '')) fail(id, 'bad id');
  if (!KINDS.includes(kind)) fail(id, `unknown kind ${kind}`);
  if (!['T', 'D', 'T+D'].includes(probe.td)) fail(id, 'td must be T, D or T+D');
  if (probe.retired && !['AD8'].includes(probe.retired)) fail(id, 'only AD8 rows are retired');
  if (probe.td !== 'T' && !probe.retired && !probe.fields?.length) fail(id, 'D and T+D rows list package field paths');
  for (const field of probe.fields ?? []) if (!field.startsWith('/')) fail(id, `field path ${field} is not a pointer`);
  for (const name of JSON.stringify(probe).matchAll(ALIAS)) {
    if (!(name[1] in aliases)) fail(id, `undeclared alias ${name[1]}`);
  }
  const has = key => probe[key] !== undefined;
  if (probe.retired) return;
  if (kind === 'dom' && !has('target')) fail(id, 'dom needs target');
  if (kind === 'interaction' && !(probe.steps?.length && probe.observe?.length)) fail(id, 'interaction needs steps and observe');
  if (kind === 'link' && !has('target') && !has('navigate')) fail(id, 'link needs target or navigate');
  if (kind === 'media' && !['target', 'font_face', 'request_match', 'path'].some(has)) fail(id, 'media needs a locator');
  if (kind === 'replaced' && !probe.feeds?.length) fail(id, 'replaced needs feeds');
  if (kind === 'http' && !probe.requests?.length) fail(id, 'http needs requests');
}

export async function loadProbes(file) {
  const doc = JSON.parse(await readFile(file, 'utf8'));
  if (doc.schema !== 'carr.portal-parity-probes.v1') throw new Error('probe file schema must be carr.portal-parity-probes.v1');
  const seen = new Set();
  for (const probe of doc.probes) {
    if (seen.has(probe.id)) fail(probe.id, 'duplicate id');
    seen.add(probe.id);
    checkProbe(probe, doc.aliases);
  }
  return doc;
}

// One side's alias map: the probe file's neutral values, overridden by a local
// file (kept outside the repo) for a reference site whose code uses other names.
export function sideAliases(doc, site, overrides) {
  return { ...doc.aliases, ...(overrides?.[site] ?? {}) };
}

export function expand(value, aliases) {
  if (typeof value === 'string') return value.replace(ALIAS, (_, name) => aliases[name]);
  if (Array.isArray(value)) return value.map(item => expand(item, aliases));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, expand(v, aliases)]));
  return value;
}

// Replace each side's alias values with their tokens, longest first, so code
// identifiers that differ only by alias compare equal.
export function neutralize(text, aliases) {
  let out = String(text);
  for (const [name, value] of Object.entries(aliases).sort((a, b) => b[1].length - a[1].length)) {
    if (value) out = out.split(value).join(`@{${name}}`);
  }
  return out;
}

// A field path is a JSON Pointer; `*` matches any array item. It resolves when
// at least one concrete path reaches a value that is not null or empty.
export function resolveField(root, pointer) {
  const parts = pointer.split('/').slice(1).map(part => part.replace(/~1/g, '/').replace(/~0/g, '~'));
  const walk = (node, index) => {
    if (index === parts.length) return node !== undefined && node !== null && node !== '' && !(Array.isArray(node) && !node.length);
    if (node === null || typeof node !== 'object') return false;
    const part = parts[index];
    if (part === '*') return Array.isArray(node) && node.some(item => walk(item, index + 1));
    return Object.hasOwn(node, part) && walk(node[part], index + 1);
  };
  return walk(root, 0);
}

// Whether the schema declares a field path (array indices and `*` both step into items).
export function schemaHasField(schema, pointer) {
  const defs = schema.$defs ?? {};
  const deref = node => {
    for (let hops = 0; node?.$ref && hops < 20; hops++) node = defs[node.$ref.split('/').at(-1)];
    return node;
  };
  const walk = (node, parts) => {
    node = deref(node);
    if (!node) return false;
    if (!parts.length) return true;
    const alternatives = [...(node.oneOf ?? []), ...(node.anyOf ?? []), ...(node.allOf ?? [])];
    if (alternatives.some(alt => walk(alt, parts))) return true;
    const [part, ...rest] = parts;
    if (part === '*' || /^\d+$/.test(part)) return Boolean(node.items) && walk(node.items, rest);
    if (node.properties && Object.hasOwn(node.properties, part)) return walk(node.properties[part], rest);
    if (node.patternProperties) return Object.entries(node.patternProperties).some(([re, sub]) => new RegExp(re).test(part) && walk(sub, rest));
    return false;
  };
  return walk(schema, pointer.split('/').slice(1));
}
