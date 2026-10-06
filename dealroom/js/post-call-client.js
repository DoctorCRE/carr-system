// Dev-only: the standalone post-call processor listens on this machine. No
// hosted page imports this client, and it refuses to fetch unless the PAGE itself
// is on a loopback host, so a production origin never attempts a loopback request
// (and the Worker CSP's connect-src does not allow one).
const DEFAULT_LOOPBACK = 'http://127.0.0.1:4682';
const LOOPBACK_PAGE_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

/** True only when the page hostname is a loopback host. Absent location fails closed. */
export function loopbackAllowed(hostname = globalThis.location?.hostname) {
  return LOOPBACK_PAGE_HOSTS.has(String(hostname || '').toLowerCase());
}
const DEFAULT_HEADER = { 'X-CARR-Call-Mode': 'deal-room-v1' };

async function payload(response, fallback) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || fallback);
    error.status = response.status;
    error.payload = body;
    throw error;
  }
  return body;
}

/** Narrow client for the loopback-only post-call review surface. */
export function createPostCallClient(options = {}) {
  const loopback = (options.loopbackUrl || DEFAULT_LOOPBACK).replace(/\/$/u, '');
  const fetchImpl = options.fetchImpl || fetch;
  const allowed = loopbackAllowed(options.pageHostname ?? globalThis.location?.hostname);
  const postHeaders = { 'content-type': 'application/json', ...DEFAULT_HEADER,
    ...(options.postHeaders || {}) };

  async function loopbackFetch(path, init = {}) {
    if (!allowed) {
      const error = new Error('Call Mode runs only from a local page; this host never contacts the local processor.');
      error.code = 'loopback_not_permitted';
      throw error;
    }
    try {
      return await fetchImpl(`${loopback}${path}`, { targetAddressSpace: 'loopback', ...init });
    } catch (cause) {
      const error = new Error('Call Mode could not reach the local post-call processor.');
      error.cause = cause;
      error.permission = true;
      throw error;
    }
  }

  return {
    async publishCallContext(context) {
      const response = await loopbackFetch('/api/call-context', {
        method: 'POST', headers: postHeaders, body: JSON.stringify(context),
      });
      return payload(response, 'Call Mode could not prepare the weekly agenda context.');
    },

    async getStatus(session) {
      const response = await loopbackFetch(`/api/post-call?session=${encodeURIComponent(session)}`, {
        headers: { ...DEFAULT_HEADER },
      });
      return payload(response, 'The post-call report could not be loaded.');
    },

    async syncStatus(session) {
      const response = await loopbackFetch('/api/post-call/sync', {
        method: 'POST', headers: postHeaders, body: JSON.stringify({ session }),
      });
      return payload(response, 'Call Mode could not verify the post-call review state.');
    },

    async createOutlookDraft(session, draftId, approvedContentHash) {
      const response = await loopbackFetch(
        `/api/post-call/drafts/${encodeURIComponent(draftId)}/create`, {
          method: 'POST', headers: postHeaders,
          body: JSON.stringify({ session, approved_content_hash: approvedContentHash }),
        });
      return payload(response, 'Outlook could not create this draft.');
    },
  };
}
