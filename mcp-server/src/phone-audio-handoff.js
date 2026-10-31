// Keep queued phone audio in transactional object storage until acknowledgement or alarm expiry.
const TTL_MS = 15 * 60 * 1000;
const UPLOAD_TIMEOUT_MS = 2 * 60 * 1000;
const CAPACITY = 50 * 1024 * 1024;
const CHUNK_BYTES = 1_900_000;
const reply = (body, status = 200) => Response.json(body, {
  status, headers: { 'cache-control': 'no-store' },
});
const chunkKeys = (id, entry) => Array.from({ length: entry.chunks || 0 }, (_, i) => `audio:${id}:${i}`);

export class PhoneAudioHandoff {
  constructor(ctx) {
    this.storage = ctx.storage;
  }

  async schedule(tx, pending) {
    const deadlines = Object.values(pending).map(e => e.expiresAt);
    if (deadlines.length) await tx.setAlarm(Math.min(...deadlines));
    else await tx.deleteAlarm();
  }

  async alarm() {
    await this.storage.transaction(async tx => {
      const pending = await tx.get('pending') || {};
      for (const [id, entry] of Object.entries(pending)) {
        if (entry.expiresAt > Date.now()) continue;
        await tx.delete(chunkKeys(id, entry));
        await tx.put(`expired:${id}`, true);
        delete pending[id];
      }
      await tx.put('pending', pending);
      await this.schedule(tx, pending);
    });
  }

  async fetch(request) {
    const url = new URL(request.url);
    const id = url.pathname.slice(1);
    if (request.method === 'GET') {
      return this.storage.transaction(async tx => {
        const pending = await tx.get('pending') || {};
        const entry = pending[id];
        if (!id) return reply({ sessions: Object.values(pending)
          .filter(e => e.state === 'queued' && e.expiresAt > Date.now()).map(e => e.metadata) });
        if (url.searchParams.has('status')) return reply({
          state: entry ? (entry.expiresAt <= Date.now() ? 'expired' : entry.state)
            : await tx.get(`expired:${id}`) ? 'expired' : 'pending',
          ...(entry ? { sha256: entry.metadata.sha256, byte_count: entry.metadata.byte_count } : {}),
        });
        if (entry?.state !== 'queued' || entry.expiresAt <= Date.now()) return reply({ error: 'not_found' }, 404);
        const chunks = [];
        for (const key of chunkKeys(id, entry)) chunks.push(await tx.get(key, { noCache: true }));
        return new Response(new ReadableStream({ start(controller) {
          for (const chunk of chunks) controller.enqueue(chunk);
          controller.close();
        } }), { headers: {
          'content-type': entry.metadata.mime_type, 'content-length': String(entry.metadata.byte_count),
          'cache-control': 'no-store', 'x-carr-call': JSON.stringify(entry.metadata),
        } });
      });
    }
    if (request.method === 'DELETE') {
      await this.storage.transaction(async tx => {
        const pending = await tx.get('pending') || {};
        if (pending[id]) await tx.delete(chunkKeys(id, pending[id]));
        delete pending[id];
        await tx.delete(`expired:${id}`);
        await tx.put('pending', pending);
        await this.schedule(tx, pending);
      });
      return reply({ ok: true });
    }
    if (request.method !== 'PUT') return reply({ error: 'method_not_allowed' }, 405);
    const metadata = JSON.parse(request.headers.get('x-carr-call'));
    const size = metadata.byte_count;
    if (!Number.isSafeInteger(size) || size <= 0 || size > CAPACITY)
      return reply({ error: 'handoff_full' }, 429);
    const reservation = crypto.randomUUID();
    const admitted = await this.storage.transaction(async tx => {
      const pending = await tx.get('pending') || {};
      let entry = pending[id];
      const abandoned = entry?.state === 'uploading' &&
        (entry.startedAt ?? entry.expiresAt - TTL_MS) + UPLOAD_TIMEOUT_MS <= Date.now();
      if (entry && entry.expiresAt > Date.now() && !abandoned) {
        if (entry.metadata.sha256 !== metadata.sha256 || entry.metadata.byte_count !== size)
          return reply({ error: 'recording_mismatch' }, 409);
        return reply({ queued: entry.state === 'queued' }, entry.state === 'queued' ? 202 : 409);
      }
      const rateKey = `uploads:${metadata.owner}`;
      const recent = (await tx.get(rateKey) || []).filter(time => time > Date.now() - 60000);
      if (recent.length >= 3) return Response.json({ error: 'upload_rate_limit' }, {
        status: 429, headers: { 'cache-control': 'no-store', 'retry-after': String(Math.ceil((recent[0] + 60000 - Date.now()) / 1000)) },
      });
      if (abandoned) {
        if (entry.chunks) await tx.delete(chunkKeys(id, entry));
        delete pending[id];
        entry = undefined;
      }
      if (entry?.expiresAt <= Date.now()) {
        await tx.delete(chunkKeys(id, entry));
        delete pending[id];
        await tx.put(`expired:${id}`, true);
        await tx.put('pending', pending);
        await this.schedule(tx, pending);
        entry = undefined;
      }
      const reserved = Object.values(pending).reduce((sum, e) => sum + e.metadata.byte_count, 0);
      if (reserved + size > CAPACITY) return reply({ error: 'handoff_full' }, 429);
      await tx.put(rateKey, [...recent, Date.now()]);
      pending[id] = { metadata, state: 'uploading', reservation, startedAt: Date.now(), expiresAt: Date.now() + TTL_MS };
      await tx.put('pending', pending);
      if (await tx.get(`expired:${id}`)) await tx.delete(`expired:${id}`);
      await this.schedule(tx, pending);
      return null;
    });
    if (admitted) return admitted;
    const reader = request.body?.getReader();
    const timeout = setTimeout(() => { reader?.cancel().catch(() => {}); }, 120000);
    try {
      const audio = new Uint8Array(size);
      let length = 0;
      if (!reader) throw new Error('missing_audio');
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (length + value.byteLength > size) { await reader.cancel(); throw new Error('length_mismatch'); }
        audio.set(value, length);
        length += value.byteLength;
      }
      if (length !== size) throw new Error('length_mismatch');
      await this.storage.transaction(async tx => {
        const pending = await tx.get('pending') || {};
        const entry = pending[id];
        if (entry?.reservation !== reservation || entry.expiresAt <= Date.now()) throw new Error('expired');
        entry.chunks = Math.ceil(size / CHUNK_BYTES);
        for (let i = 0; i < entry.chunks; i++)
          await tx.put(`audio:${id}:${i}`, audio.slice(i * CHUNK_BYTES, (i + 1) * CHUNK_BYTES), { noCache: true });
        entry.state = 'queued';
        await tx.put('pending', pending);
      });
      return reply({ queued: true }, 202);
    } catch {
      await this.storage.transaction(async tx => {
        const pending = await tx.get('pending') || {};
        if (pending[id]?.reservation !== reservation) return;
        if (pending[id].chunks) await tx.delete(chunkKeys(id, pending[id]));
        delete pending[id];
        await tx.put('pending', pending);
        await this.schedule(tx, pending);
      });
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
