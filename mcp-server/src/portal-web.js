export const PORTAL_ORIGIN = 'https://share.doctorcre.com';
const TTL_SECONDS = 604800;
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
const TOKEN = /^[a-f0-9]{64}$/;
const encoder = new TextEncoder();
const cookieName = slug => `__Host-portal-${slug}`;
const cookie = (slug, token, age) => `${cookieName(slug)}=${token}; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=${age}`;
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);

/** Select only the canonical client-portal.v1 fields, never internal row fields. */
export function safePortalProjection(portal, slug) {
  if (!record(portal) || portal.slug !== slug || !record(portal.roadmap) ||
      !Array.isArray(portal.roadmap.phases) || !Array.isArray(portal.materials) || portal.materials.length > 100)
    throw new TypeError('invalid_projection');
  const phases = portal.roadmap.phases.map(phase => {
    if (!record(phase) || typeof phase.slug !== 'string' || typeof phase.label !== 'string') throw new TypeError('invalid_phase');
    return {slug: phase.slug, label: phase.label};
  });
  if (portal.roadmap.current_phase != null && typeof portal.roadmap.current_phase !== 'string') throw new TypeError('invalid_phase');
  const materials = portal.materials.map(material => {
    if (!record(material) || typeof material.kind !== 'string' || typeof material.title !== 'string' || !('content' in material))
      throw new TypeError('invalid_material');
    return {kind: material.kind, title: material.title, content: material.content};
  });
  return {slug, roadmap: {current_phase: portal.roadmap.current_phase ?? null, phases}, materials};
}

function response(body, status = 200, headers = {}, html = false) {
  return new Response(html ? body : JSON.stringify(body), {status, headers: {
    'content-type': html ? 'text/html; charset=utf-8' : 'application/json; charset=utf-8',
    'cache-control': 'private, no-store',
    'content-security-policy': "default-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'",
    'strict-transport-security': 'max-age=31536000; includeSubDomains',
    'x-content-type-options': 'nosniff', 'x-frame-options': 'DENY',
    'referrer-policy': 'no-referrer', 'cross-origin-resource-policy': 'same-origin', ...headers
  }});
}
function page(slug, projection, headers = {}) {
  const content = projection
    ? `<h1>Your portal</h1><pre>${escapeHtml(JSON.stringify(projection, null, 2))}</pre><form method="post" action="/${slug}/logout"><button>Log out</button></form>`
    : `<h1>Open your portal</h1><form method="post" action="/${slug}/login"><label>Password <input name="password" type="password" required maxlength="1024" autocomplete="current-password"></label><button>Open portal</button></form>`;
  return response(`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Client portal</title><body>${content}</body></html>`, 200, headers, true);
}
async function digest(token) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(token)));
  return [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
function newToken() {
  return [...crypto.getRandomValues(new Uint8Array(32))].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
function readCookie(request, slug) {
  const matches = (request.headers.get('cookie') || '').split(';').map(x => x.trim()).filter(x => x.startsWith(`${cookieName(slug)}=`));
  if (matches.length !== 1) return null;
  const token = matches[0].slice(cookieName(slug).length + 1);
  return TOKEN.test(token) ? token : null;
}

class HttpError extends Error {
  constructor(status, code) { super(code); this.status = status; }
}

async function formBody(request, deadlineMs, needsPassword) {
  if (!needsPassword && !request.body) return null;
  if (!/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get('content-type') || '')) throw new HttpError(415, 'unsupported_media_type');
  const length = request.headers.get('content-length');
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > 8192)) throw new HttpError(413, 'body_too_large');
  const reader = request.body?.getReader();
  if (!reader) throw new HttpError(400, 'invalid_password');
  let size = 0, text = '', timer;
  const decoder = new TextDecoder('utf-8', {fatal: true});
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => {
    void reader.cancel().catch(() => {}); reject(new HttpError(408, 'body_timeout'));
  }, deadlineMs); });
  try {
    const read = async () => {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > 8192) throw new HttpError(413, 'body_too_large');
        text += decoder.decode(chunk.value, {stream: true});
      }
      text += decoder.decode();
      const params = new URLSearchParams(text), keys = [...params.keys()];
      if (!needsPassword) {
        if (keys.length) throw new HttpError(400, 'invalid_logout');
        return null;
      }
      const password = params.get('password');
      if (keys.length !== 1 || keys[0] !== 'password' || !password || [...password].length > 1024 || password.includes('\0'))
        throw new HttpError(400, 'invalid_password');
      return password;
    };
    return await Promise.race([read(), timeout]);
  } finally { clearTimeout(timer); void reader.cancel().catch(() => {}); }
}

/**
 * Dependencies resolve committed contract results, including denied/locked.
 * exchange({slug,password,digest,signal}), read/revoke({slug,digest,signal}).
 * read MUST use a read-write transaction. No Request, cookie or MCP forwarding.
 * signal receives fixed operational codes only. No credentials are logged.
 */
export function createPortalWebHandler(options = {}) {
  const config = {now: Date.now, safeProjection: safePortalProjection,
    signal: event => console.error('portal_web', event.code), deadlineMs: 3000,
    maxConcurrent: 8, maxRequests: 128, loginLimit: 10, actionLimit: 120,
    maxRateBuckets: 1024, ...options};
  for (const key of ['deadlineMs','maxConcurrent','maxRequests','loginLimit','actionLimit','maxRateBuckets'])
    if (!Number.isSafeInteger(config[key]) || config[key] < 1) throw new TypeError(`invalid_${key}`);
  let active = 0, requests = 0;
  const breakers = Object.fromEntries(['exchange','read','revoke','projection'].map(name =>
    [name, {failures: 0, openUntil: 0, probing: false}]));
  const rates = new Map();
  const emit = code => { try { config.signal({code}); } catch { /* Observability cannot grant access. */ } };
  const unavailable = code => { emit(code); return new HttpError(503, 'portal_unavailable'); };

  async function call(name, operation) {
    const breaker = breakers[name];
    const now = config.now();
    if (active >= config.maxConcurrent) throw unavailable('dependency_capacity');
    if (breaker.openUntil && (now < breaker.openUntil || breaker.probing)) throw unavailable('circuit_open');
    if (breaker.openUntil) breaker.probing = true;
    active++;
    const controller = new AbortController();
    let timer;
    const work = Promise.resolve().then(() => operation(controller.signal));
    work.then(() => active--, () => active--);
    try {
      const result = await Promise.race([work, new Promise((_, reject) => {
        timer = setTimeout(() => { controller.abort(); reject(new Error('deadline')); }, config.deadlineMs);
      })]);
      breaker.failures = 0; breaker.openUntil = 0;
      return result;
    } catch {
      breaker.failures++;
      if (breaker.failures >= 3 || breaker.probing) breaker.openUntil = config.now() + 30000;
      throw unavailable('dependency_failure');
    } finally { clearTimeout(timer); breaker.probing = false; }
  }

  function rate(slug, login) {
    const now = config.now();
    for (const [key, bucket] of rates) if (bucket.until <= now) rates.delete(key);
    const key = `${slug}:${login ? 'login' : 'action'}`;
    if (!rates.has(key)) {
      if (rates.size >= config.maxRateBuckets) throw unavailable('rate_capacity');
      rates.set(key, {count: 0, until: now + 60000});
    }
    if (++rates.get(key).count > (login ? config.loginLimit : config.actionLimit)) throw new HttpError(429, 'rate_limited');
  }

  async function handle(request) {
    const url = new URL(request.url);
    if (url.origin !== PORTAL_ORIGIN || url.username || url.password) throw new HttpError(404, 'not_found');
    if (request.method !== 'GET' && (request.headers.get('origin') !== PORTAL_ORIGIN ||
        ['cross-site','same-site'].includes(request.headers.get('sec-fetch-site')))) throw new HttpError(403, 'same_origin_required');
    if (url.search || /[%\\]/.test(url.pathname)) throw new HttpError(400, 'invalid_path');
    const match = /^\/([^/]+)(?:\/(login|status|logout|packages|files|selections)?)?$/.exec(url.pathname);
    if (!match || !SLUG.test(match[1])) throw new HttpError(404, 'not_found');
    const [, slug, action] = match;
    rate(slug, action === 'login');
    if (['packages','files','selections'].includes(action)) throw new HttpError(501, 'portal_feature_not_available');
    const method = action === 'login' || action === 'logout' ? 'POST' : 'GET';
    if (request.method !== method) return response({error:'method_not_allowed'},405,{allow:method});
    if (url.pathname === `/${slug}`) return response({},308,{location:`/${slug}/`});
    if (action === 'login') {
      let password = await formBody(request, config.deadlineMs, true);
      const token = newToken(), sessionDigest = await digest(token);
      let result;
      try {
        result = await call('exchange', async signal => {
          const value = await config.exchange({slug, password, digest:sessionDigest, signal});
          if (value?.status === 'denied') return value;
          if (value?.status === 'locked' && Number.isFinite(Date.parse(value.retry_at))) return value;
          if (value?.status === 'success' && Date.parse(value.expires_at) > config.now()) return value;
          throw new Error('invalid_exchange_result');
        });
      } finally { password = null; }
      if (result.status === 'locked') return response({error:'portal_locked',message:`Too many password attempts. Try again at ${result.retry_at}.`,retry_at:result.retry_at},429,{'retry-after':String(Math.max(1,Math.ceil((Date.parse(result.retry_at)-config.now())/1000)))});
      if (result.status === 'denied') return response({error:'portal_access_denied'},401);
      const age = Math.min(TTL_SECONDS, Math.floor((Date.parse(result.expires_at)-config.now())/1000));
      if (age < 1) throw unavailable('invalid_expiry');
      return response({},303,{location:`/${slug}/`,'set-cookie':cookie(slug,token,age)});
    }
    const token = readCookie(request, slug);
    const clear = {'set-cookie':cookie(slug,'',0)};
    if (action === 'logout') {
      await formBody(request, config.deadlineMs, false);
      if (token) await call('revoke', async signal => {
        const result = await config.revoke({slug,digest:await digest(token),signal});
        if (!['success','denied'].includes(result?.status)) throw new Error('invalid_revoke_result');
      });
      return response({},303,{location:`/${slug}/`,...clear});
    }
    if (!token) return action === 'status' ? response({authenticated:false},401,clear) : page(slug,null,clear);
    const result = await call('read', async signal => {
      const value = await config.read({slug,digest:await digest(token),signal});
      if (value?.status === 'denied') return value;
      if (value?.status !== 'success' || value.schema_version !== 'client-portal.v1' || value.portal?.slug !== slug)
        throw new Error('invalid_read_result');
      return value;
    });
    if (result.status === 'denied') return action === 'status' ? response({authenticated:false},401,clear) : page(slug,null,clear);
    if (action === 'status') return response({authenticated:true});
    const projection = await call('projection', async () => {
      const safe = await config.safeProjection(result.portal,slug);
      const serialized = JSON.stringify(safePortalProjection(safe,slug));
      if (encoder.encode(serialized).byteLength > 1048576) throw new Error('projection_too_large');
      return JSON.parse(serialized);
    });
    return page(slug,projection);
  }

  return {fetch: async request => {
    if (requests >= config.maxRequests) { emit('request_capacity'); return response({error:'portal_unavailable'},503); }
    requests++;
    try { return await handle(request); }
    catch (error) {
      if (error instanceof HttpError) return response({error:error.message},error.status);
      emit('request_failure'); return response({error:'portal_unavailable'},503);
    } finally { requests--; }
  }};
}
