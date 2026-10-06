import { captureRuntimeError, errorStore, ERROR_RESPONSE } from './runtime-errors.js';

export const RUNTIME_ERROR_PATH = '/api/v1/runtime-errors';
const json = (body, status = 200) => Response.json(body, { status, headers: { 'cache-control': 'no-store' } });

export async function runtimeErrorWeb(request, env) {
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
  if (request.headers.get('origin') !== new URL(request.url).origin) return json({ error: 'forbidden' }, 403);
  if (!request.headers.get('content-type')?.startsWith('application/json')) return json({ error: 'json_required' }, 415);
  const reader = request.body?.getReader();
  if (!reader) return json({ error: 'invalid_report' }, 400);
  const chunks = []; let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 12000) { await reader.cancel(); return json({ error: 'report_too_large' }, 413); }
    chunks.push(value);
  }
  let input;
  try { const bytes = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; } input = JSON.parse(new TextDecoder().decode(bytes)); }
  catch { return json({ error: 'invalid_report' }, 400); }
  if (!input || typeof input !== 'object' || Array.isArray(input)) return json({ error: 'invalid_report' }, 400);
  return json(await captureRuntimeError(env, 'browser', input), 202);
}

export async function runtimeErrorControl(request, env) {
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
  const path = new URL(request.url).pathname;
  if (['/_runtime-errors/ack', '/_runtime-errors/prepare', '/_runtime-errors/refresh', '/_runtime-errors/defer'].includes(path)) return errorStore(env).fetch(`https://runtime-errors/${path.split('/').pop()}`, request);
  if (path !== '/_runtime-errors/plan') return json({ error: 'not_found' }, 404);
  const input = await request.json();
  let appRelease = null;
  try {
    if (!env.DOCTORCRE_APP_HOST) throw new Error('runtime_error_app_host_unset');
    const response = await fetch(`https://${env.DOCTORCRE_APP_HOST}/app-release`, { signal: AbortSignal.timeout(5000) });
    const release = response.ok ? await response.json() : {};
    if (release.service === 'doctorcre-app' && release.environment === env.CARR_ENV && /^[a-f0-9]{40}$/.test(release.source_commit || '')) appRelease = { sha: release.source_commit, created_at: release.provider_version_created_at };
  } catch { /* Unknown release cannot clear an error. */ }
  const response = await errorStore(env).fetch('https://runtime-errors/plan', { method: 'POST', body: JSON.stringify({ observe_only: input.observe_only === true, releases: { 'carr-worker': { sha: env.GIT_SHA, created_at: env.CF_VERSION_METADATA?.timestamp }, 'app-worker': appRelease, browser: appRelease } }) });
  if (!response.ok) return json({ error: 'runtime_error_store_unavailable', health: `UNAVAILABLE runtime errors · ${ERROR_RESPONSE}` }, 503);
  return response;
}
