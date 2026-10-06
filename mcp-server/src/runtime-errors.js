import contract from './runtime-errors.v1.json' with { type: 'json' };
const TYPES = new Set(contract.types);
const ROUTES = new Set(contract.route_segments);
export const ERROR_RESPONSE = contract.response_action.replace('{quiet_hours}', String(contract.auto_clear.quiet_ms / 3600000));
export const QUIET_MS = contract.auto_clear.quiet_ms;
const WINDOW_MS = contract.spike.window_ms;
const SHA = /^[a-f0-9]{40}$/;

export function scrubRoute(input, { server = false } = {}) {
  const raw = String(input || '/').split(/[?#]/)[0];
  if (server && /^mcp:tools\/call:[a-z][a-z0-9-]*$/.test(raw)) return raw;
  const route = raw.split('/').slice(0, 8)
    .map(part => !part || ROUTES.has(part) || (server && SERVER_SEGMENTS.has(part)) ? part : ':value').join('/');
  return route.startsWith('/') ? route : '/:value';
}

const SERVER_SEGMENTS = new Set(['token', 'authorize', 'register', 'oauth', 'callback', 'consent',
  '.well-known', 'oauth-authorization-server', 'oauth-protected-resource', 'openid-configuration',
  'dealroom', 'session', 'challenge', 'login', 'reauth', 'signout', 'ingest', 'doc', '_runtime-errors',
  'plan', 'prepare', 'ack', 'refresh']);

export function scrubError(input = {}) {
  const type = TYPES.has(input.type) ? input.type : 'Error';
  const text = typeof input.message === 'string' ? input.message.slice(0, 1000) : '';
  const message = /^Cannot read properties of /.test(text) ? 'Cannot read properties of [redacted]'
    : /^\S+ is not defined/.test(text) ? '[redacted] is not defined'
    : /^(Failed to fetch|Load failed|NetworkError|Script error\.?)$/.test(text) ? text
    : type === 'HTTP5xx' ? 'HTTP 5xx response' : '[redacted]';
  const route = scrubRoute(input.route);
  const stack = String(input.stack || '').slice(0, 8000).split('\n').slice(0, 20)
    .flatMap(line => {
      if (!/^\s*(?:at\s|asset:)|^[^\s:]*@(?:https?:\/\/|\/)/.test(line)) return [];
      const match = line.match(/:(\d{1,7}):(\d{1,7})\)?\s*$/);
      return match ? [`asset:${match[1]}:${match[2]}`] : [];
    }).join('\n');
  return { type, message, stack, route: route.startsWith('/') ? route : '/:value',
    release_sha: SHA.test(input.release_sha || '') ? input.release_sha : null };
}

async function fingerprint(error, source) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify([source, error.type, error.message, error.route])));
  return [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function evidence(group) {
  return `Runtime error in ${group.source} ${group.route}. ${group.type}: ${group.message}. ` +
    `Fingerprint ${group.fingerprint}. Count ${group.count}; first ${new Date(group.first_seen).toISOString()}; last ${new Date(group.last_seen).toISOString()}; release ${group.release_sha || 'unknown'}. ` +
    `Stack ${group.stack || 'unavailable'}. ${ERROR_RESPONSE}`;
}

export class RuntimeErrorStore {
  constructor(state) { this.state = state; }
  async fetch(request) {
    return this.state.blockConcurrencyWhile(async () => {
      const path = new URL(request.url).pathname;
      const input = request.method === 'POST' ? await request.json() : {};
      const now = Date.now();
      if (path === '/capture') {
        const source = ['browser', 'app-worker', 'carr-worker'].includes(input.source) ? input.source : 'browser';
        const error = scrubError(input);
        const key = await fingerprint(error, source);
        const previous = await this.state.storage.get(key);
        if (!previous) {
          const groups = await this.state.storage.list();
          if ([...groups.values()].filter(group => group.source === source).length >= contract.limits.groups_per_source)
            return Response.json({ error: 'runtime_error_group_limit' }, { status: 429 });
        }
        const group = previous || { fingerprint: key, source, count: 0, first_seen: now, notified_count: 0 };
        Object.assign(group, error, { count: group.count + 1, last_seen: now });
        group.cleared = false;
        if (!group.window_start || now - group.window_start >= WINDOW_MS) { group.window_start = now; group.window_count = 0; }
        group.window_count++;
        await this.state.storage.put(key, group);
        return Response.json({ ok: true, fingerprint: key });
      }
      if (path === '/ack') {
        const group = await this.state.storage.get(input.fingerprint);
        if (!group || !group.pending || group.pending.key !== input.key) return Response.json({ error: 'stale_ack' }, { status: 409 });
        if (group.pending.verb === 'close-loop') { group.loop_id = null; group.cleared = group.count === group.pending.count; group.cleared_at = now; }
        else {
          if (!/^[a-f0-9-]{36}$/.test(input.loop_id || '')) return Response.json({ error: 'invalid_loop_id' }, { status: 400 });
          group.loop_id = input.loop_id; group.cleared = false;
          group.managed_version = group.pending.verb === 'add-loop' ? 1 : group.pending.args.base_version + 1;
        }
        group.notified_count = group.pending.count;
        group.notified_window_start = group.pending.window_start;
        group.notified_window_count = group.pending.window_count;
        group.pending = null;
        await this.state.storage.put(input.fingerprint, group);
        return Response.json({ ok: true });
      }
      if (path === '/defer') {
        const group = await this.state.storage.get(input.fingerprint);
        if (!group?.pending || group.pending.key !== input.key) return Response.json({ error: 'stale_operation' }, { status: 409 });
        group.pending.last_attempt = now;
        await this.state.storage.put(input.fingerprint, group);
        return Response.json({ ok: true });
      }
      if (path === '/prepare' || path === '/refresh') {
        const group = await this.state.storage.get(input.fingerprint);
        if (!group?.pending || group.pending.key !== input.key) return Response.json({ error: 'stale_operation' }, { status: 409 });
        if (path === '/refresh') {
          if (input.reason === 'loop_not_open') { group.loop_id = null; group.managed_version = null; group.manual = false; }
          else if (['version_conflict', 'human_edit'].includes(input.reason)) {
            group.manual = true;
            group.notified_count = group.pending.count;
            group.notified_window_start = group.pending.window_start;
            group.notified_window_count = group.pending.window_count;
          }
          else return Response.json({ error: 'invalid_refresh_reason' }, { status: 400 });
          group.pending = null;
        }
        else if (!Number.isInteger(group.pending.args.base_version)) {
          if (!Number.isInteger(input.base_version)) return Response.json({ error: 'invalid_version' }, { status: 400 });
          group.pending.args.base_version = input.base_version;
        }
        await this.state.storage.put(input.fingerprint, group);
        return Response.json({ ok: true, operation: group.pending });
      }
      if (path !== '/plan') return Response.json({ error: 'not_found' }, { status: 404 });
      const groups = await this.state.storage.list();
      const operations = [];
      let active = 0;
      const ordered = [...groups].sort((a, b) => (a[1].pending?.last_attempt || 0) - (b[1].pending?.last_attempt || 0));
      for (const [key, group] of ordered) {
        if (!input.observe_only && group.cleared && group.cleared_at <= now - contract.limits.cleared_retention_ms) {
          await this.state.storage.delete(key); continue;
        }
        const release = input.releases?.[group.source];
        const releaseTime = Date.parse(release?.created_at || '');
        const quiet = group.last_seen <= now - QUIET_MS && group.release_sha &&
          SHA.test(release?.sha || '') && release.sha !== group.release_sha &&
          Number.isFinite(releaseTime) && releaseTime <= now && releaseTime > group.last_seen;
        const spike = group.window_count >= contract.spike.count && (group.notified_window_start !== group.window_start || group.window_count >= Math.max(contract.spike.count, (group.notified_window_count || 0) * 2));
        const changed = group.count > group.notified_count;
        let dirty = false;
        if (!input.observe_only && !group.pending && operations.length < contract.limits.operations_per_plan && ((changed && !group.loop_id) || (changed && (spike || group.manual)) || (group.loop_id && quiet && !group.manual))) {
          const verb = quiet && group.loop_id ? 'close-loop' : group.loop_id ? 'update-loop' : 'add-loop';
          const args = verb === 'close-loop' ? { loop_id: group.loop_id, resolution: 'done', outcome: `No recurrence for ${QUIET_MS / 3600000}h; newer release ${release.sha} observed after the last error. This records quiet observation, not proof of a repair.` }
            : group.loop_id ? { loop_id: group.loop_id, source_note: evidence(group) }
              : { kind: 'open_loop', owner: 'claude', domain: 'system', marker: 'none', blocker: 'other_lane', blocker_detail: 'The orchestrator repair lane must reproduce and fix this runtime fingerprint.', body: evidence(group) };
          group.pending = { fingerprint: key, key: crypto.randomUUID(), verb, args, expected_version: group.managed_version, count: group.count, window_start: group.window_start, window_count: group.window_count };
          dirty = true;
        }
        if (!group.cleared) active++;
        if (!input.observe_only && group.pending && operations.length < contract.limits.operations_per_plan) operations.push(group.pending);
        if (dirty) await this.state.storage.put(key, group);
      }
      return Response.json({ ok: true, operations, health: `${active ? 'WARN' : 'OK'} runtime errors — ${active} active fingerprint(s) · ${ERROR_RESPONSE}` });
    });
  }
}

export function errorStore(env) {
  if (!env.RUNTIME_ERRORS) throw new Error('runtime_error_store_unavailable');
  return env.RUNTIME_ERRORS.get(env.RUNTIME_ERRORS.idFromName('runtime-errors.v1'));
}

export async function captureRuntimeError(env, source, input) {
  const error = scrubError(input);
  const response = await errorStore(env).fetch('https://runtime-errors/capture', { method: 'POST', body: JSON.stringify({ ...error, source }) });
  if (!response.ok) throw new Error('runtime_error_capture_failed');
  return response.json();
}

export function withRuntimeErrors(handler) {
  return async (request, env, ctx) => {
    const report = error => {
      const input = { type: error?.name || 'HTTP5xx', message: error?.message, stack: error?.stack,
        route: new URL(request.url).pathname, release_sha: env.GIT_SHA };
      const pending = captureRuntimeError(env, 'carr-worker', input).catch(() => console.error(JSON.stringify({ event: 'runtime_error_capture_failed', ...scrubError(input) })));
      ctx?.waitUntil?.(pending);
    };
    try {
      const response = await handler(request, env, ctx);
      if (response.status >= 500) report(null);
      return response;
    } catch (error) { report(error); throw error; }
  };
}
