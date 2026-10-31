// Run the real CARR Worker on loopback, as staging, over a disposable migrated
// PostgreSQL. App journey tests point their CARR service binding here.
//
// Only the platform is substituted: the Neon driver reaches the local cluster
// through its documented fetchFunction / webSocketConstructor hooks, and KV,
// rate limits and Durable Objects are in-memory. Routing, identity, verbs,
// grants and SQL are the shipped code. Requests arrive as if at the staging app
// host, so the staging-only /auth/e2e-session door signs a browser in as the
// synthetic partner exactly as tests/staging/auth.setup.ts does on staging.
import { createServer } from 'node:http';
import { registerHooks } from 'node:module';
import net from 'node:net';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { neonConfig } from '@neondatabase/serverless';
import { acquireDisposablePostgres } from './disposable-postgres.mjs';
import { callContext, openCallSession, seedSyntheticBook } from './call-rehearsal.mjs';
import { APP_STAGING_HOST } from '../../src/staging-browser.ts';
import { TOOLS, executeRegisteredTool } from '../../src/tools.js';

const STAGING_ORIGIN = `https://${APP_STAGING_HOST}`;

// The OAuth provider library's only workerd import; the Worker never calls it
// as an RPC entrypoint on these routes.
const WORKERS_SHIM = `data:text/javascript,${encodeURIComponent(
  'export class WorkerEntrypoint { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }')}`;
registerHooks({ resolve: (specifier, context, next) => specifier === 'cloudflare:workers'
  ? { url: WORKERS_SHIM, shortCircuit: true } : next(specifier, context) });

// Neon's SQL-over-HTTP protocol (raw text output, array mode) answered by pg.
const textTypes = { getTypeParser: () => value => value };
const pools = new Map();
const poolFor = dsn => {
  if (!pools.has(dsn)) pools.set(dsn, new pg.Pool({ connectionString: dsn, max: 4, types: textTypes }));
  return pools.get(dsn);
};
const shaped = result => ({ command: result.command, rowCount: result.rowCount,
  fields: result.fields.map(({ name, dataTypeID }) => ({ name, dataTypeID })), rows: result.rows });
const ERROR_FIELDS = ['code', 'detail', 'hint', 'position', 'internalPosition', 'internalQuery',
  'where', 'schema', 'table', 'column', 'dataType', 'constraint', 'file', 'line', 'routine', 'severity'];
async function neonFetch(_url, { body, headers }) {
  const request = JSON.parse(body);
  const client = await poolFor(headers['Neon-Connection-String']).connect();
  const run = ({ query, params }) => client.query({ text: query, values: params, rowMode: 'array' }).then(shaped);
  try {
    if (!request.queries) return Response.json(await run(request));
    const isolation = headers['Neon-Batch-Isolation-Level'];
    await client.query(`begin${isolation ? ` isolation level ${isolation.replace(/([a-z])([A-Z])/g, '$1 $2')}` : ''}`
      + (headers['Neon-Batch-Read-Only'] === 'true' ? ' read only' : ''));
    const results = [];
    for (const query of request.queries) results.push(await run(query));
    await client.query('commit');
    return Response.json({ results });
  } catch (error) {
    if (request.queries) await client.query('rollback').catch(() => {});
    return Response.json(Object.fromEntries([['message', error.message],
      ...ERROR_FIELDS.map(field => [field, error[field]])]), { status: 400 });
  } finally { client.release(); }
}

// The driver's Pool speaks the PostgreSQL wire protocol over a WebSocket; this
// one is a plain socket to the cluster.
class LoopbackWebSocket extends EventTarget {
  constructor(url) {
    super();
    this.socket = net.connect(JSON.parse(new URL(url).searchParams.get('target')));
    this.socket.on('connect', () => this.dispatchEvent(new Event('open')));
    this.socket.on('data', data => this.dispatchEvent(new MessageEvent('message', { data })));
    this.socket.on('error', () => this.dispatchEvent(new Event('error')));
    this.socket.on('close', () => this.dispatchEvent(new Event('close')));
  }
  send(data) { this.socket.write(data); }
  close() { this.socket.end(); }
}

class MemoryKv {
  constructor() { this.values = new Map(); }
  async get(key, options) {
    const value = this.values.get(key);
    if (value === undefined) return null;
    const type = typeof options === 'string' ? options : options?.type;
    return type === 'json' ? JSON.parse(value) : value;
  }
  async put(key, value) { this.values.set(key, value); }
  async delete(key) { this.values.delete(key); }
}

// Durable Objects run their shipped classes over in-memory storage.
function memoryStorage() {
  const values = new Map();
  const storage = {
    get: async key => structuredClone(values.get(key)),
    put: async (key, value) => { values.set(key, structuredClone(value)); },
    delete: async key => values.delete(key),
    setAlarm: async () => {}, getAlarm: async () => null, deleteAlarm: async () => {},
    transaction: fn => fn(storage),
  };
  return storage;
}
function memoryNamespace(DurableObject, env) {
  const objects = new Map();
  return {
    idFromName: name => name,
    get: id => {
      if (!objects.has(id)) objects.set(id, new DurableObject({ id, storage: memoryStorage(),
        blockConcurrencyWhile: fn => fn() }, env));
      return objects.get(id);
    },
  };
}

/**
 * `assets` answers the Worker's ASSETS binding, which in a deployment is the
 * DoctorCRE app release; an app journey passes its own built site.
 */
export async function startLocalCarr({ port = 0, assets = async () => new Response('not found', { status: 404 }) } = {}) {
  const started = performance.now();
  // A local cluster on its Unix socket, or a database cloned on the server
  // CI's PostgreSQL service over TCP.
  const database = await acquireDisposablePostgres({ prefix: 'local-carr-', snapshot: true });
  const dsn = new URL(database.dsn);
  const socketDirectory = dsn.searchParams.get('host');
  const clusterPort = Number(dsn.port || 5432);
  // pg hands a Unix socket to the driver as one path, which its proxy hook
  // never sees; there is one database, so every connection goes to it.
  const target = JSON.stringify(socketDirectory ? { path: `${socketDirectory}/.s.PGSQL.${clusterPort}` }
    : { host: dsn.hostname, port: clusterPort });
  neonConfig.fetchFunction = neonFetch;
  neonConfig.webSocketConstructor = LoopbackWebSocket;
  neonConfig.wsProxy = () => `loopback/?target=${encodeURIComponent(target)}`;
  neonConfig.useSecureWebSocket = false;
  neonConfig.pipelineConnect = false;
  const admin = new pg.Client({ connectionString: database.dsn });
  await admin.connect();
  // The Worker's reader and writer DSNs log in as the shipped roles, so grants
  // hold here as in production. The fixture restores role attributes on close.
  const password = randomBytes(16).toString('hex');
  const dsnFor = role => { const url = new URL(dsn); url.username = role; url.password = password; return url.href; };
  await admin.query(`alter role carr_reader login password '${password}'; alter role carr_writer login password '${password}'`);

  const joe = { slug: 'joe', human: true, via: 'oauth',
    id: (await admin.query("select id from actor where slug='joe'")).rows[0].id };
  const deals = await seedSyntheticBook({ query: (sql, params) => admin.query(sql, params), actorId: joe.id,
    setNextStep: async (id, text, next_date) => executeRegisteredTool(admin, joe, 'set-next-step', { idempotency_key: randomUUID(),
      deal: id, text, next_date, base_version: (await admin.query('select version from deal where id=$1', [id])).rows[0].version }) });

  const { unstable_readConfig } = await import('wrangler');
  const config = unstable_readConfig({ config: fileURLToPath(new URL('../../wrangler.toml', import.meta.url)), env: 'staging' });
  const localToken = randomBytes(24).toString('hex');
  const e2eSecret = randomBytes(24).toString('hex');
  const deviceToken = randomBytes(24).toString('hex');
  const env = {
    ...config.vars,
    DATABASE_URL_READER: dsnFor('carr_reader'),
    DATABASE_URL_WRITER: dsnFor('carr_writer'),
    E2E_SESSION_SECRET: e2eSecret,
    LOCAL_TOKENS: JSON.stringify({ 'joe-local': localToken }),
    CAPTURE_TOKENS: JSON.stringify({ 'quill-joe-mac': deviceToken }),
    OAUTH_KV: new MemoryKv(),
    ASSETS: { fetch: assets },
  };
  const exports = await import('../../src/index.js');
  for (const { name, class_name } of config.durable_objects.bindings)
    env[name] = memoryNamespace(exports[class_name], env);
  for (const { name } of config.ratelimits) env[name] = { limit: async () => ({ success: true }) };
  const worker = exports.default;

  const server = createServer(async (incoming, outgoing) => {
    const chunks = [];
    for await (const chunk of incoming) chunks.push(chunk);
    const headers = new Headers();
    for (const [name, value] of Object.entries(incoming.headers)) {
      if (name === 'host') continue;
      const values = Array.isArray(value) ? value : [value];
      for (const item of values) headers.append(name, name === 'origin' || name === 'referer'
        ? item.replace(/^https?:\/\/[^/]+/u, STAGING_ORIGIN) : item);
    }
    const body = chunks.length && !['GET', 'HEAD'].includes(incoming.method) ? Buffer.concat(chunks) : undefined;
    const pending = [];
    const response = await worker.fetch(new Request(`${STAGING_ORIGIN}${incoming.url}`, { method: incoming.method, headers, body }),
      env, { waitUntil: promise => pending.push(promise), passThroughOnException() {} });
    const out = {};
    for (const [name, value] of response.headers) {
      if (name === 'set-cookie') continue;
      out[name] = name === 'location' ? value.replace(STAGING_ORIGIN, '') : value;
    }
    const cookies = response.headers.getSetCookie();
    if (cookies.length) out['set-cookie'] = cookies;
    outgoing.writeHead(response.status, out);
    outgoing.end(Buffer.from(await response.arrayBuffer()));
    await Promise.allSettled(pending);
  });
  await new Promise(resolve => server.listen(port, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;

  // A record-layer verb as the local machine actor, through the Worker's /mcp.
  async function mcp(name, args = {}) {
    const response = await fetch(`${url}/mcp`, { method: 'POST', headers: { authorization: `Bearer ${localToken}`,
      'content-type': 'application/json', accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name,
      arguments: TOOLS[name]?.write ? { idempotency_key: randomUUID(), ...args } : args } }) });
    const { result, error } = await response.json();
    const payload = result ? JSON.parse(result.content[0].text) : null;
    if (!response.ok || error || result.isError) throw new Error(`${name} -> ${response.status} ${JSON.stringify(error || payload)}`);
    return payload;
  }

  const calls = [], roots = [];
  /**
   * File the synthetic weekly call through Call Mode: claim, Deal Room context,
   * written transcript, then the companion's post-call worker, whose verbs reach
   * this Worker through tools/call-verb.py exactly as on the Studio. Resolves
   * once the call is filed and applied, or, for a failing distiller, once the
   * companion reports the extraction blocked. The call starts at a fixed
   * instant, the rehearsal's, so its call date never depends on the runner.
   */
  async function fileCall({ distiller = 'stand-in', started = '2026-10-27T15:00:00Z' } = {}) {
    const root = mkdtempSync(path.join(tmpdir(), 'local-carr-call-'));
    roots.push(root);
    const call = await openCallSession({ root, started, captureUrl: url, deviceToken, distiller,
      env: { CARR_MCP_URL: `${url}/mcp`, CARR_MCP_LOCAL_TOKEN: localToken, CARR_MCP_ENV: path.join(root, 'no-token-file') } });
    calls.push(call);
    const { deals: index } = await mcp('get-call-context', { deal_ids: Object.values(deals) });
    call.writeTranscript();
    await call.request('/api/call-context', callContext(call.session, started, index));
    await call.waitFor(distiller === 'failing' ? review => review.status?.state === 'blocked'
      : review => review.report?.automatic?.finished_at);
    return call;
  }

  return {
    url, e2eSecret, deals, mcp, fileCall,
    setupMs: Math.round(performance.now() - started),
    async close() {
      for (const call of calls) call.stop();
      for (const root of roots) rmSync(root, { recursive: true, force: true });
      await new Promise(resolve => server.close(resolve));
      await Promise.allSettled([...pools.values()].map(pool => pool.end()));
      pools.clear();
      await admin.end();
      await database.close();
    },
  };
}
