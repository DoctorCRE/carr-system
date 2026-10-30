// Audio lives only in RAM. A timer prevents idle hibernation while Studio polls.
const TTL_MS = 15 * 60 * 1000;
const CAPACITY = 50 * 1024 * 1024;
const reply = (body, status = 200) => Response.json(body, {
  status, headers: { 'cache-control': 'no-store' },
});

export class PhoneAudioHandoff {
  constructor() {
    this.pending = new Map();
  }

  expire() {
    const now = Date.now();
    for (const [id, entry] of this.pending) {
      if (entry.expiresAt <= now) this.remove(id);
    }
  }

  remove(id) {
    clearTimeout(this.pending.get(id)?.timer);
    this.pending.delete(id);
  }

  async fetch(request) {
    this.expire();
    const id = new URL(request.url).pathname.slice(1);
    if (request.method === 'GET' && !id)
      return reply({ sessions: [...this.pending.values()].filter(e => e.audio).map(e => e.metadata) });
    const entry = this.pending.get(id);
    if (request.method === 'GET') {
      if (!entry?.audio) return reply({ error: 'not_found' }, 404);
      return new Response(new ReadableStream({ start(controller) {
        for (const chunk of entry.audio) controller.enqueue(chunk);
        controller.close();
      } }), { headers: {
        'content-type': entry.metadata.mime_type, 'content-length': String(entry.metadata.byte_count),
        'cache-control': 'no-store', 'x-carr-call': JSON.stringify(entry.metadata),
      } });
    }
    if (request.method === 'DELETE') {
      this.remove(id);
      return reply({ ok: true });
    }
    if (request.method !== 'PUT') return reply({ error: 'method_not_allowed' }, 405);
    if (entry) return reply({ queued: !!entry.audio }, entry.audio ? 202 : 409);
    const metadata = JSON.parse(request.headers.get('x-carr-call'));
    const size = metadata.byte_count;
    const reserved = [...this.pending.values()].reduce((sum, e) => sum + e.metadata.byte_count, 0);
    if (!Number.isSafeInteger(size) || size <= 0 || reserved + size > CAPACITY)
      return reply({ error: 'handoff_full' }, 429);
    const incoming = { metadata, audio: null, expiresAt: Date.now() + TTL_MS,
      timer: setTimeout(() => this.remove(id), TTL_MS) };
    this.pending.set(id, incoming);
    const reader = request.body?.getReader();
    const timeout = setTimeout(() => { reader?.cancel().catch(() => {}); }, 120000);
    try {
      const chunks = [];
      let length = 0;
      if (!reader) throw new Error('missing_audio');
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > size) { await reader.cancel(); break; }
        chunks.push(value);
      }
      if (length !== size || incoming.expiresAt <= Date.now()) throw new Error('length_mismatch');
      incoming.audio = chunks;
      return reply({ queued: true }, 202);
    } catch {
      this.remove(id);
      return reply({ error: 'invalid_audio' }, 400);
    } finally {
      clearTimeout(timeout);
      reader?.releaseLock();
    }
  }
}

export function audioHandoff(env, suffix = '', options) {
  const binding = env.PHONE_AUDIO_HANDOFF;
  return binding.get(binding.idFromName('phone-audio-v1')).fetch(new Request(
    `https://phone-audio.internal/${suffix}`, options));
}
