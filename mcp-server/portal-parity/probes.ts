// Load the probe file, expand neutral aliases, and resolve package field paths.
import { readFile } from "node:fs/promises";

type Aliases = Record<string, string>;
type Probe = Record<string, unknown> & {
  id: string;
  kind: "dom" | "interaction" | "link" | "media" | "replaced" | "http";
  td: "T" | "D" | "T+D";
  fields?: string[];
  allowed?: string[];
};
type ProbeDocument = { aliases: Aliases; probes: Probe[]; allowed_differences?: Record<string, unknown[]> };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item: unknown) => typeof item === "string");
}
function aliasMap(value: unknown): value is Aliases {
  return isObject(value) && Object.values(value).every((item) => typeof item === "string");
}

const KINDS = Object.freeze(["dom", "interaction", "link", "media", "replaced", "http"]);
const SITES = Object.freeze({ A: "REF-A", V: "REF-V" });
const ALIAS = /@\{([a-z0-9-]+)\}/g;

export const siteOf = (id: string) => (id.startsWith("A") ? SITES.A : id.startsWith("V") ? SITES.V : undefined);

function fail(id: unknown, message: string): never {
  throw new Error(`probe ${id}: ${message}`);
}

function checkProbe(probe: Record<string, unknown>, aliases: Aliases): asserts probe is Probe {
  const { id, kind } = probe;
  if (typeof id !== "string" || !/^[AV]-[GSTLA]\d{2}$/.test(id)) fail(id, "bad id");
  if (typeof kind !== "string" || !KINDS.includes(kind)) fail(id, `unknown kind ${kind}`);
  if (typeof probe.td !== "string" || !["T", "D", "T+D"].includes(probe.td)) fail(id, "td must be T, D or T+D");
  if (probe.retired && probe.retired !== "AD8") fail(id, "only AD8 rows are retired");
  for (const key of ["fields", "allowed"])
    if (probe[key] !== undefined && !stringList(probe[key])) fail(id, `${key} must list strings`);
  const fields = stringList(probe.fields) ? probe.fields : [];
  if (probe.td !== "T" && !probe.retired && !fields.length) fail(id, "D and T+D rows list package field paths");
  for (const field of fields) if (!field.startsWith("/")) fail(id, `field path ${field} is not a pointer`);
  for (const name of JSON.stringify(probe).matchAll(ALIAS)) {
    if (!((name[1] ?? "") in aliases)) fail(id, `undeclared alias ${name[1]}`);
  }
  const has = (key: string) => probe[key] !== undefined;
  if (probe.retired) return;
  if (kind === "dom" && !has("target")) fail(id, "dom needs target");
  if (
    kind === "interaction" &&
    !(Array.isArray(probe.steps) && probe.steps.length && Array.isArray(probe.observe) && probe.observe.length)
  )
    fail(id, "interaction needs steps and observe");
  if (kind === "link" && !has("target") && !has("navigate")) fail(id, "link needs target or navigate");
  if (kind === "media" && !["target", "font_face", "request_match", "path"].some(has))
    fail(id, "media needs a locator");
  if (kind === "replaced" && !(Array.isArray(probe.feeds) && probe.feeds.length)) fail(id, "replaced needs feeds");
  if (kind === "http" && !(Array.isArray(probe.requests) && probe.requests.length)) fail(id, "http needs requests");
}

export async function loadProbes(file: string | URL): Promise<ProbeDocument> {
  const doc: unknown = JSON.parse(await readFile(file, "utf8"));
  if (!isObject(doc)) throw new Error("probe file must be an object");
  if (doc.schema !== "carr.portal-parity-probes.v1")
    throw new Error("probe file schema must be carr.portal-parity-probes.v1");
  const allowed_differences: Record<string, unknown[]> = {};
  for (const [id, aspects] of Object.entries(isObject(doc.allowed_differences) ? doc.allowed_differences : {})) {
    if (!/^AD\d+$/.test(id) || !Array.isArray(aspects))
      throw new Error(`allowed difference ${id} must list the aspects it may differ in`);
    allowed_differences[id] = aspects;
  }
  if (!aliasMap(doc.aliases) || !Array.isArray(doc.probes)) throw new Error("probe file needs aliases and probes");
  const aliases = doc.aliases;
  const probes: Probe[] = [];
  const seen = new Set<string>();
  for (const probe of doc.probes) {
    if (!isObject(probe)) fail(undefined, "must be an object");
    if (probe.allowed !== undefined && !stringList(probe.allowed)) fail(probe.id, "allowed must list strings");
    for (const id of [...(stringList(probe.allowed) ? probe.allowed : []), ...(probe.retired ? [probe.retired] : [])]) {
      if (typeof id !== "string" || !allowed_differences[id])
        fail(probe.id, `allowed difference ${id} is not declared with its aspects`);
    }
    if (typeof probe.id === "string" && seen.has(probe.id)) fail(probe.id, "duplicate id");
    checkProbe(probe, aliases);
    seen.add(probe.id);
    probes.push(probe);
  }
  return { ...doc, aliases, probes, ...(doc.allowed_differences === undefined ? {} : { allowed_differences }) };
}

// One side's alias map: the probe file's neutral values, overridden by a local
// file (kept outside the repo) for a reference site whose code uses other names.
export function sideAliases(doc: { aliases: Aliases }, site: string, overrides?: Record<string, Aliases>) {
  return { ...doc.aliases, ...(overrides?.[site] ?? {}) };
}

export function expand(value: unknown, aliases: Aliases): unknown {
  if (typeof value === "string") return value.replace(ALIAS, (_, name: string) => String(aliases[name]));
  if (Array.isArray(value)) return value.map((item) => expand(item, aliases));
  if (value && typeof value === "object")
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, expand(v, aliases)]));
  return value;
}

// Replace each side's alias values with their tokens, longest first, so code
// identifiers that differ only by alias compare equal.
export function neutralize(text: unknown, aliases: Aliases) {
  let out = String(text);
  for (const [name, value] of Object.entries(aliases).sort((a, b) => b[1].length - a[1].length)) {
    if (value) out = out.split(value).join(`@{${name}}`);
  }
  return out;
}

// A field path is a JSON Pointer; `*` matches any array item. It resolves when
// at least one concrete path reaches a value that is not null or empty.
export function resolveField(root: unknown, pointer: string): boolean {
  const parts = pointer
    .split("/")
    .slice(1)
    .map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
  const walk = (node: unknown, index: number): boolean => {
    if (index === parts.length)
      return node !== undefined && node !== null && node !== "" && !(Array.isArray(node) && !node.length);
    if (node === null || typeof node !== "object") return false;
    const part = parts[index];
    if (part === undefined) return false;
    if (part === "*") return Array.isArray(node) && node.some((item) => walk(item, index + 1));
    return isObject(node)
      ? Object.hasOwn(node, part) && walk(node[part], index + 1)
      : Array.isArray(node) && Object.hasOwn(node, part) && walk(Reflect.get(node, part), index + 1);
  };
  return walk(root, 0);
}

// Whether the schema declares a field path (array indices and `*` both step into items).
export function schemaHasField(schema: unknown, pointer: string): boolean {
  const defs = isObject(schema) && isObject(schema.$defs) ? schema.$defs : {};
  const deref = (node: unknown) => {
    for (let hops = 0; isObject(node) && typeof node.$ref === "string" && node.$ref && hops < 20; hops++) {
      const key = node.$ref.split("/").at(-1);
      node = key === undefined ? undefined : defs[key];
    }
    return node;
  };
  const walk = (value: unknown, parts: string[]): boolean => {
    const node = deref(value);
    if (!node) return false;
    if (!isObject(node)) return !parts.length;
    if (!parts.length) return true;
    const alternatives = [
      ...(Array.isArray(node.oneOf) ? node.oneOf : []),
      ...(Array.isArray(node.anyOf) ? node.anyOf : []),
      ...(Array.isArray(node.allOf) ? node.allOf : []),
    ];
    if (alternatives.some((alt) => walk(alt, parts))) return true;
    const [part, ...rest] = parts;
    if (part === undefined) return false;
    if (part === "*" || /^\d+$/.test(part)) return Boolean(node.items) && walk(node.items, rest);
    if (isObject(node.properties) && Object.hasOwn(node.properties, part)) return walk(node.properties[part], rest);
    if (isObject(node.patternProperties))
      return Object.entries(node.patternProperties).some(([re, sub]) => new RegExp(re).test(part) && walk(sub, rest));
    return false;
  };
  return walk(schema, pointer.split("/").slice(1));
}
