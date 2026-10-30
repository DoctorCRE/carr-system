// Phone intake stores metadata in CARR; the Studio pulls transient audio.
import { audioHandoff } from './phone-audio-handoff.js';
import { bearerCredential } from './bearer.ts';

export const PHONE_CALL_PREFIX = '/api/v1/call-sessions';
export const PHONE_STUDIO_PREFIX = '/capture/phone/v1/call-sessions';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MIME_TYPES = new Set(['audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/m4a', 'audio/x-m4a', 'audio/mpeg', 'audio/webm']);
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
});
const hex = bytes => [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2, '0')).join('');
const sha = async text => hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));

async function boundedText(request, maximum) {
  if (Number(request.headers.get('content-length')) > maximum) return null;
  if (!request.body) return '';
  const reader = request.body.getReader();
  const chunks = [];
  let length = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > maximum) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(bytes);
}

async function sessionToken(env, id) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(env.CALL_STUDIO_TOKEN),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`phone-call-v1:${id}`)));
}

function limit(env, name, fallback) {
  const value = Number(env[name] ?? fallback);
  if (!Number.isSafeInteger(value) || value < 1) throw new Error('invalid_phone_limit');
  return value;
}

export const phoneCallByteLimit = env => limit(env, 'CALL_MAX_BYTES', 50 * 1024 * 1024);

function validAdmission(body, env) {
  const fields = ['sha256', 'byte_count', 'duration_seconds', 'mime_type', 'started_at', 'consent_announced_at'];
  return body && Object.keys(body).length === fields.length && fields.every(k => Object.hasOwn(body, k)) &&
    /^[0-9a-f]{64}$/.test(body.sha256) && MIME_TYPES.has(body.mime_type) &&
    Number.isSafeInteger(body.byte_count) && body.byte_count > 0 &&
    body.byte_count <= phoneCallByteLimit(env) &&
    Number.isFinite(body.duration_seconds) && body.duration_seconds > 0 &&
    body.duration_seconds <= limit(env, 'CALL_MAX_SECONDS', 3600) &&
    ['started_at', 'consent_announced_at'].every(k => typeof body[k] === 'string' &&
      /(?:Z|[+-]\d{2}:\d{2})$/.test(body[k]) && Number.isFinite(Date.parse(body[k])));
}

function view(row) {
  return { schema_version: 1, session_id: row.id,
    state: row.state === 'done' ? 'filed' : row.projection ? 'ready_review' : row.transferred_at ? 'transferred' : 'pending',
    filed: row.state === 'done', transferred: !!row.transferred_at };
}

async function handoffStatus(env, id) {
  const response = await audioHandoff(env, `${id}?status`);
  if (!response.ok) throw new Error('handoff_unavailable');
  return response.json();
}

async function readSession(client, id, owner) {
  const result = await client.query(`select p.*, s.state from phone_call_session p
    join capture_session s on s.id=p.id where p.id=$1 and ($2::uuid is null or p.owner_id=$2)
    /* phone:read */`, [id, owner]);
  return result.rows[0];
}

export function createPhoneCallHandler({ withWriter }) {
  return {
    async studioFetch(request, env) {
      const supplied = bearerCredential(request.headers.get('authorization'));
      if (!env.CALL_STUDIO_TOKEN || !supplied || await sha(supplied) !== await sha(env.CALL_STUDIO_TOKEN))
        return json({ error: 'unauthorized' }, 401);
      const path = new URL(request.url).pathname;
      if (path === PHONE_STUDIO_PREFIX && request.method === 'GET') {
        try { return await audioHandoff(env); }
        catch { return json({ error: 'phone_intake_unavailable' }, 503); }
      }
      const match = path.slice(PHONE_STUDIO_PREFIX.length).match(/^\/([^/]+)\/(report|lease|audio|ack)$/);
      if (!path.startsWith(PHONE_STUDIO_PREFIX + '/') || !match || !UUID.test(match[1])) return json({ error: 'not_found' }, 404);
      if (match[2] === 'audio' && request.method === 'GET') {
        try { return await audioHandoff(env, match[1]); }
        catch { return json({ error: 'phone_intake_unavailable' }, 503); }
      }
      if (request.method !== 'POST' || match[2] === 'audio') return json({ error: 'method_not_allowed' }, 405);
      if (match[2] === 'ack') {
        const text = await boundedText(request, 256);
        if (text === null) return json({ error: 'receipt_too_large' }, 413);
        let receipt;
        try { receipt = JSON.parse(text); } catch { return json({ error: 'invalid_receipt' }, 400); }
        try {
          const row = await withWriter(client => readSession(client, match[1], null));
          if (!row || receipt?.sha256 !== row.sha256) return json({ error: 'receipt_mismatch' }, 409);
          await withWriter(client => client.query('update phone_call_session set transferred_at=coalesce(transferred_at,now()) where id=$1 /* phone:transferred */', [match[1]]));
          const removed = await audioHandoff(env, match[1], { method: 'DELETE' });
          if (!removed.ok) throw new Error('handoff_unavailable');
          return json({ ok: true });
        } catch { return json({ error: 'phone_intake_unavailable' }, 503); }
      }
      if (match[2] === 'lease') {
        try {
          return await withWriter(async client => {
            if (!await readSession(client, match[1], null)) return json({ error: 'not_found' }, 404);
            const token = await sessionToken(env, match[1]);
            await client.query(`update capture_session set session_token_hash=$2,
              expires_at=now()+interval '30 days' where id=$1 /* phone:renew */`, [match[1], await sha(token)]);
            return json({ session_token: token });
          });
        } catch { return json({ error: 'phone_intake_unavailable' }, 503); }
      }
      const text = await boundedText(request, 16000);
      if (text === null) return json({ error: 'report_too_large' }, 413);
      let report;
      try { report = JSON.parse(text); } catch { return json({ error: 'invalid_json' }, 400); }
      if (!report || Object.keys(report).sort().join(',') !== 'decisions,open_questions,summary' ||
          typeof report.summary !== 'string' || report.summary.length > 8000 ||
          !['decisions', 'open_questions'].every(k => Array.isArray(report[k]) && report[k].length <= 50 &&
            report[k].every(s => typeof s === 'string' && s.length <= 1000)))
        return json({ error: 'invalid_narrative_projection' }, 400);
      try {
        return await withWriter(async client => {
          const row = await readSession(client, match[1], null);
          if (!row) return json({ error: 'not_found' }, 404);
          await client.query('update phone_call_session set projection=$2::jsonb where id=$1 /* phone:projection */',
            [match[1], JSON.stringify(report)]);
          return json({ ok: true, session_id: match[1] });
        });
      } catch { return json({ error: 'phone_intake_unavailable' }, 503); }
    },
    async fetch(request, env, actor) {
      if (!actor?.human || !['joe', 'dell'].includes(actor.slug)) return json({ error: 'unauthorized' }, 401);
      const path = new URL(request.url).pathname;
      try {
        if (path === PHONE_CALL_PREFIX && request.method === 'POST') {
          if (!env.CALL_STUDIO_TOKEN) return json({ error: 'studio_not_configured' }, 503);
          const text = await boundedText(request, 4096);
          if (text === null) return json({ error: 'admission_too_large' }, 413);
          let body;
          try { body = JSON.parse(text); } catch { return json({ error: 'invalid_json' }, 400); }
          if (!validAdmission(body, env)) return json({ error: 'invalid_admission' }, 400);
          const result = await withWriter(async client => {
            await client.query('select pg_advisory_xact_lock(hashtextextended($1,0)) /* phone:owner-lock */', [actor.id]);
            const previous = await client.query('select p.*, s.state from phone_call_session p join capture_session s on s.id=p.id where p.owner_id=$1 and p.sha256=$2 /* phone:duplicate */', [actor.id, body.sha256]);
            if (previous.rows.length) return { row: previous.rows[0], duplicate: true };
            const count = await client.query(`select count(*)::int as count from phone_call_session
              where owner_id=$1 and admitted_on=(now() at time zone 'America/Chicago')::date /* phone:daily */`, [actor.id]);
            if (count.rows[0].count >= limit(env, 'CALL_DAILY_ADMISSIONS', 5)) return { limited: true };
            const id = crypto.randomUUID();
            const tokenHash = await sha(await sessionToken(env, id));
            await client.query(`insert into capture_session
              (id,nonce,device_id,actor_id,mode,started_at,consent_announced_at,session_token_hash,expires_at,state,state_at,post_call)
              values ($1::uuid,$1::text,'phone-import',$2,'meeting',$3::timestamptz,$4::timestamptz,$5,
                now()+interval '30 days','recording',now(),true) /* phone:capture */`,
            [id, actor.id, body.started_at, body.consent_announced_at, tokenHash]);
            const inserted = await client.query(`insert into phone_call_session
              (id,owner_id,sha256,byte_count,duration_seconds,mime_type,started_at,consent_announced_at)
              values ($1,$2,$3,$4,$5,$6,$7::timestamptz,$8::timestamptz) returning *, 'recording'::text as state /* phone:admit */`,
            [id, actor.id, body.sha256, body.byte_count, body.duration_seconds, body.mime_type, body.started_at, body.consent_announced_at]);
            return { row: inserted.rows[0], duplicate: false };
          });
          if (result.limited) return json({ error: 'daily_admission_limit' }, 429);
          return json({ ...view(result.row), duplicate: result.duplicate }, result.duplicate ? 200 : 201);
        }
        const match = path.slice(PHONE_CALL_PREFIX.length).match(/^\/([^/]+)(?:\/(transfer|report))?$/);
        if (!path.startsWith(PHONE_CALL_PREFIX + '/') || !match || !UUID.test(match[1])) return json({ error: 'not_found' }, 404);
        const [, id, operation] = match;
        const row = await withWriter(client => readSession(client, id, actor.id));
        if (!row) return json({ error: 'not_found' }, 404);
        if (request.method === 'GET' && !operation) {
          const result = view(row);
          if (result.state === 'pending' && env.PHONE_AUDIO_HANDOFF) {
            const status = await handoffStatus(env, id);
            if (['queued', 'expired'].includes(status.state)) result.state = status.state;
          }
          return json(result);
        }
        if (request.method === 'GET' && operation === 'report') {
          const candidates = await withWriter(async client => {
            const actions = await client.query(`select id,kind,deal_id,assignee_slug as assignee,
              action_description as action,due_on::text,status,resulting_ref from capture_post_call_candidate
              where session_id=$1 and kind='assigned_action' order by created_at,id /* phone:actions */`, [id]);
            const updates = await client.query(`select id,kind,payload,status,resulting_ref from capture_candidate
              where session_id=$1 order by created_at,id /* phone:updates */`, [id]);
            return { actions: actions.rows, deal_updates: updates.rows };
          });
          return json({ ...view(row), report: row.projection || null, ...candidates });
        }
        if (request.method !== 'PUT' || operation !== 'transfer') return json({ error: 'method_not_allowed' }, 405);
        if (row.transferred_at) return json(view(row));
        if (request.headers.get('content-type') !== row.mime_type) return json({ error: 'unsupported_media_type' }, 415);
        if (Number(request.headers.get('content-length')) !== Number(row.byte_count)) return json({ error: 'length_mismatch' }, 400);
        if (row.byte_count > phoneCallByteLimit(env) ||
            row.duration_seconds > limit(env, 'CALL_MAX_SECONDS', 3600)) return json({ error: 'call_limit' }, 413);
        if (!env.PHONE_AUDIO_HANDOFF || !env.CALL_STUDIO_TOKEN) return json(view(row), 202);
        const status = await handoffStatus(env, id);
        if (status.state === 'queued') {
          if (status.sha256 !== row.sha256 || status.byte_count !== Number(row.byte_count))
            return json({ error: 'recording_mismatch' }, 409);
          return json({ ...view(row), state: 'queued', queued: true, retry_after_seconds: 30 }, 202);
        }
        const metadata = { schema_version: 1, session_id: id, owner: actor.slug,
          sha256: row.sha256, byte_count: Number(row.byte_count), duration_seconds: Number(row.duration_seconds),
          mime_type: row.mime_type, started_at: row.started_at, consent_announced_at: row.consent_announced_at,
          session_token: await sessionToken(env, id) };
        const response = await audioHandoff(env, id, {
          method: 'PUT', headers: { 'x-carr-call': JSON.stringify(metadata) },
          body: request.body, duplex: 'half',
        });
        if (!response.ok) return json({ ...view(row), error: 'audio_handoff_unavailable' }, response.status);
        return json({ ...view(row), state: 'queued', queued: true, retry_after_seconds: 30 }, 202);
      } catch { return json({ error: 'phone_intake_unavailable' }, 503); }
    },
  };
}
