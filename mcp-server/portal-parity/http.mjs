// Gate probes over plain HTTP, sign-in, and the Stage 0 source-copy hash check.
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { digest, relativeTo } from './observe.mjs';
import { neutralize } from './probes.mjs';

const HEADERS = ['allow', 'content-type', 'cache-control', 'x-content-type-options', 'x-robots-tag', 'referrer-policy'];
const CROSS_ORIGIN = 'https://cross-origin.invalid';

function target(base, where) {
  if (where === '@bare') return base.replace(/\/$/, '');
  if (where === '@root') return new URL('/', base).href;
  return new URL(where, base).href;
}

function headerValue(value, origin) {
  return value === '@origin' ? origin : value === '@cross-origin' ? CROSS_ORIGIN : value;
}

function bodyOf(spec) {
  if (spec === undefined) return undefined;
  if (typeof spec === 'string') return spec;
  if (spec.repeat) return spec.repeat.text.repeat(spec.repeat.times);
  if (spec.json !== undefined) return JSON.stringify(spec.json);
  if (spec.bytes) return Buffer.from(spec.bytes);
  throw new Error('unknown body spec');
}

// Cookie attributes only: names differ by design (AD2) and values are secrets.
function cookieAttributes(setCookies) {
  return setCookies.map(line => line.split(';').slice(1).map(part => part.trim().replace(/^max-age=\d+$/i, m => m.toLowerCase()))
    .map(part => part.toLowerCase()).sort().join('; ')).join(' | ');
}

export async function signIn(base, password) {
  const response = await fetch(base, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', Origin: new URL(base).origin },
    body: new URLSearchParams({ password }).toString(),
  });
  const cookie = response.headers.getSetCookie().map(line => line.split(';')[0]).find(Boolean);
  if (response.status >= 400 || !cookie) throw new Error(`sign-in refused with status ${response.status}`);
  return cookie;
}

async function csrfToken(base, cookie) {
  const response = await fetch(new URL('api/selection', base), { headers: { Cookie: cookie } });
  const body = await response.json().catch(() => ({}));
  return body.csrf_token ?? '';
}

export async function observeHttp(base, probe, aliases, { cookie, password } = {}) {
  const origin = new URL(base).origin;
  const facts = {};
  for (const [index, request] of probe.requests.entries()) {
    const headers = Object.fromEntries(Object.entries(request.headers ?? {}).map(([k, v]) => [k, headerValue(v, origin)]));
    let body = bodyOf(request.body);
    if (request.sign_in) {
      if (!password) return { unrun: 'needs a password' };
      Object.assign(headers, { 'Content-Type': 'application/x-www-form-urlencoded', Origin: origin });
      body = new URLSearchParams({ password }).toString();
    }
    if (request.session) {
      if (!cookie) return { unrun: 'needs a session; pass a password' };
      headers.Cookie = cookie;
      if (request.csrf) headers[aliases['csrf-header']] = await csrfToken(base, cookie);
    }
    const response = await fetch(target(base, request.path), {
      method: request.method, redirect: 'manual', headers, body,
    });
    const received = Buffer.from(await response.arrayBuffer());
    const key = `r${index}`;
    facts[`${key}.status`] = response.status;
    for (const name of HEADERS) facts[`${key}.${name}`] = response.headers.get(name) ?? '';
    facts[`${key}.csp`] = response.headers.has('content-security-policy');
    const location = response.headers.get('location');
    facts[`${key}.location`] = location ? neutralize(relativeTo(base, new URL(location, target(base, request.path)).href), aliases) : '';
    facts[`${key}.cookie`] = cookieAttributes(response.headers.getSetCookie());
    facts[`${key}.empty`] = received.length === 0;
  }
  return { facts };
}

async function* sourceFiles(root, dir = '') {
  for (const entry of await readdir(path.join(root, dir), { withFileTypes: true })) {
    const name = path.posix.join(dir, entry.name);
    if (entry.name.startsWith('.')) continue;
    if (entry.isDirectory()) yield* sourceFiles(root, name);
    else if (entry.isFile()) yield name;
  }
}

// The reference Worker's generated route manifest (`export default new Map([...])`
// mapping each served path to its encrypted blob), read as data and never imported.
// The live router releases a file only when its path is a key of this map.
export function routeManifestPaths(text) {
  const match = /export default new Map\((\[[\s\S]*\])\);?\s*$/.exec(text);
  const entries = match ? JSON.parse(match[1]) : null;
  if (!Array.isArray(entries) || !entries.every(entry => Array.isArray(entry) && typeof entry[0] === 'string')) {
    throw new Error('--manifest is not a route manifest');
  }
  return entries.map(([served]) => served);
}

async function liveDigest(url, cookie) {
  const response = await fetch(url, { headers: { Cookie: cookie }, redirect: 'manual' });
  return { status: response.status, live: response.status === 200 ? digest(Buffer.from(await response.arrayBuffer())) : `status-${response.status}` };
}

const encodePath = served => served.split('/').map(encodeURIComponent).join('/');

// Stage 0: every manifest path, fetched from the live site through a session,
// must hash equal to its file in the read-only source copy (a directory route
// releases its index.html). A source file outside the manifest must not be
// served: a 404 is UNSERVED, counted so the partner can match it against the
// inventory (V-A34). Entries carry ordinals, verdicts and hashes only.
export async function sourceFidelity(base, sourceDir, cookie, manifestPaths) {
  const root = new URL(base).pathname;
  const entries = [], covered = new Set();
  for (const [index, served] of manifestPaths.entries()) {
    if (!served.startsWith(root)) throw new Error(`manifest path ${index + 1} is outside the reference`);
    const rel = served.slice(root.length);
    const name = rel === '' || rel.endsWith('/') ? `${rel}index.html` : rel;
    covered.add(name);
    const source = await readFile(path.join(sourceDir, name)).then(digest, () => 'missing');
    const { live } = await liveDigest(new URL(encodePath(served), base), cookie);
    entries.push({ verdict: live === source ? 'PASS' : 'FAIL', source, live });
  }
  for await (const name of sourceFiles(sourceDir)) {
    if (covered.has(name)) continue;
    const source = digest(await readFile(path.join(sourceDir, name)));
    const { status, live } = await liveDigest(new URL(encodePath(name), base), cookie);
    entries.push({ verdict: status === 404 ? 'UNSERVED' : 'FAIL', source, live });
  }
  const files = entries.map((entry, index) => ({ ordinal: index + 1, ...entry }));
  return {
    files,
    lines: files.map(f => `FILE ${String(f.ordinal).padStart(3, '0')} ${f.verdict} source=${f.source.slice(0, 16)} live=${f.live.slice(0, 16)}`),
    total: files.length,
    failed: files.filter(f => f.verdict === 'FAIL').length,
    unserved: files.filter(f => f.verdict === 'UNSERVED').length,
  };
}
