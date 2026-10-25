import { randomString } from "./google-oidc.js";
import { publicError } from "./public-errors.js";

const JSON_HEADERS = { "content-type": "application/json" };
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

export const CAPTURE_SESSION_TTL_SECONDS = 24 * 60 * 60;
const STATES = ["recording", "transcribing", "distilling", "done"];
const KINDS = new Set(["phase_move", "next_step", "new_deal", "activity", "meeting_record"]);
const POST_CALL_KINDS = new Set(["assigned_action", "email_draft"]);

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.keys(value).sort().reduce((result, key) => {
      result[key] = canonical(value[key]);
      return result;
    }, {});
  return value;
}

function bearer(request) {
  return (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
}

function captureDevice(request, env) {
  const token = bearer(request);
  let tokens;
  try { tokens = JSON.parse(env.CAPTURE_TOKENS || "{}"); }
  catch { tokens = {}; }
  return Object.keys(tokens).find(slug => tokens[slug] && tokens[slug] === token) || null;
}

async function requestBody(request) {
  try { return await request.json(); }
  catch { return null; }
}

function validTime(value) {
  return typeof value === "string" && value.trim() && Number.isFinite(Date.parse(value));
}

function wordCount(value) {
  const trimmed = String(value ?? "").trim();
  return trimmed ? trimmed.split(/\s+/u).length : 0;
}

function hasForbiddenCaptureKey(value) {
  if (Array.isArray(value)) return value.some(hasForbiddenCaptureKey);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(([key, nested]) =>
    /transcript|human_quote|verbatim|raw_text|audio|email_body|draft_body|html_body/iu.test(key) || hasForbiddenCaptureKey(nested));
}

function payloadStringsTooLarge(value) {
  if (typeof value === "string") return value.length > 4000;
  if (Array.isArray(value)) return value.some(payloadStringsTooLarge);
  if (!value || typeof value !== "object") return false;
  return Object.values(value).some(payloadStringsTooLarge);
}

function isUuid(value) {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu.test(value);
}

function isSha256(value) {
  return typeof value === "string" && /^[0-9a-f]{64}$/u.test(value);
}

function errorResponse(error, env) {
  if (error?.captureStatus) return json(error.payload, error.captureStatus);
  return json(publicError(error, { env, fallback: "database_unavailable" }), 503);
}

function refuse(status, payload) {
  const error = new Error(payload.error);
  error.captureStatus = status;
  error.payload = payload;
  throw error;
}

async function claim(request, env, dependencies) {
  // Authentication is intentionally first, before body size, JSON, nonce, or
  // consent checks, matching the ingest socket's information boundary.
  const device = captureDevice(request, env);
  if (!device) return json({ error: "unauthorized" }, 401);
  const body = await requestBody(request);
  if (!body) return json({ error: "invalid_json" }, 400);
  if (hasForbiddenCaptureKey(body)) return json({ error: "forbidden_capture_field" }, 400);
  if (!body.nonce || body.device_id !== device || body.mode !== "meeting" || !validTime(body.started_at) ||
      (body.workflow != null && body.workflow !== "post_call"))
    return json({ error: "invalid_claim" }, 400);
  if (!validTime(body.consent?.announced_at)) return json({ error: "consent_required" }, 400);

  let opaque;
  try {
    await dependencies.withWriter(async client => {
      const replay = await client.query(
        "select id from capture_session where nonce=$1 /* capture:claim-nonce */", [body.nonce]);
      if (replay.rows.length) refuse(409, { error: "nonce_replayed" });
      const actor = await client.query("select id from actor where slug=$1 /* capture:device-actor */", [device]);
      if (!actor.rows.length) refuse(503, { error: "device_actor_not_provisioned", device_id: device });
      opaque = dependencies.randomStringFn(32);
      const tokenHash = await sha256(opaque);
      await client.query(
        `insert into capture_session
           (nonce, device_id, actor_id, mode, started_at, consent_announced_at,
            session_token_hash, expires_at, state, state_at, post_call)
         values ($1,$2,$3,$4,$5::timestamptz,$6::timestamptz,$7,
                 now() + ($8 * interval '1 second'),'recording',$5::timestamptz,$9)
         /* capture:claim-insert */`,
        [body.nonce, device, actor.rows[0].id, body.mode, body.started_at,
         body.consent.announced_at, tokenHash, CAPTURE_SESSION_TTL_SECONDS,
         body.workflow === "post_call"]);
    });
    return json({ session_token: opaque, ttl_seconds: CAPTURE_SESSION_TTL_SECONDS });
  } catch (error) {
    if (error?.code === "23505") return json({ error: "nonce_replayed" }, 409);
    return dependencies.errorResponse(error);
  }
}

async function authenticatedSession(client, opaque, lock = false) {
  if (typeof opaque !== "string" || !opaque) refuse(401, { error: "unauthorized" });
  const tokenHash = await sha256(opaque);
  const result = await client.query(
    `select id, state, post_call, to_jsonb(state_at)#>>'{}' as state_at
       from capture_session
      where session_token_hash=$1 and expires_at > now()
      ${lock ? "for update" : ""} /* capture:session-auth */`, [tokenHash]);
  if (!result.rows.length) refuse(401, { error: "unauthorized" });
  return result.rows[0];
}

async function status(request, dependencies) {
  const body = await requestBody(request);
  if (!body) return json({ error: "invalid_json" }, 400);
  if (hasForbiddenCaptureKey(body)) return json({ error: "forbidden_capture_field" }, 400);
  if (![...STATES, "failed"].includes(body.state) || !validTime(body.at))
    return json({ error: "invalid_status" }, 400);
  if (body.detail != null && (typeof body.detail !== "string" || body.detail.length > 500))
    return json({ error: "invalid_status_detail" }, 400);
  try {
    const result = await dependencies.withWriter(async client => {
      const session = await authenticatedSession(client, body.session_token, true);
      if (session.state === "done" || session.state === "failed")
        refuse(409, { error: "terminal_state", state: session.state });
      const current = STATES.indexOf(session.state);
      const requested = STATES.indexOf(body.state);
      if (body.state !== "failed" && requested < current)
        refuse(409, { error: "backward_transition", state: session.state, requested: body.state });
      if (body.state === "done" && session.post_call) {
        const readiness = await client.query(
          `select count(c.id) filter (where c.status='pending')::int as pending,
                  count(c.id)::int as total,
                  exists(select 1 from capture_post_call_report r where r.session_id=$1) as report_filed,
                  (select r.candidate_count from capture_post_call_report r where r.session_id=$1) as report_candidate_count
             from (
               select id,status from capture_candidate where session_id=$1
               union all
               select id,status from capture_post_call_candidate where session_id=$1
             ) c
             /* capture:post-call-finalize-check */`, [session.id]);
        const gate = readiness.rows[0];
        if (gate.pending !== 0 || !gate.report_filed || gate.report_candidate_count !== gate.total)
          refuse(409, { error: "post_call_not_ready", pending: gate.pending,
            total: gate.total, report_filed: gate.report_filed,
            report_candidate_count: gate.report_candidate_count || null });
      }
      const updated = await client.query(
        `update capture_session set state=$2, state_at=$3::timestamptz, state_detail=$4
          where id=$1 returning state, to_jsonb(state_at)#>>'{}' as state_at
          /* capture:status-update */`,
        [session.id, body.state, body.at, body.detail || null]);
      return updated.rows[0];
    });
    return json({ ok: true, state: result.state, at: result.state_at });
  } catch (error) { return dependencies.errorResponse(error); }
}

function validateCandidates(body) {
  if (!body || typeof body.idempotency_key !== "string" || !body.idempotency_key ||
      !Array.isArray(body.items) || body.items.length === 0 || body.items.length > 100)
    return { error: "invalid_candidates" };
  for (const item of body.items) {
    if (!item || !KINDS.has(item.kind) || !item.payload || typeof item.payload !== "object" || Array.isArray(item.payload) ||
        typeof item.evidence_quote !== "string" || typeof item.confidence !== "number" ||
        !Number.isFinite(item.confidence) || item.confidence < 0 || item.confidence > 1)
      return { error: "invalid_candidate" };
    if (hasForbiddenCaptureKey(item.payload))
      return { error: "transcript_field_forbidden" };
    if (JSON.stringify(item.payload).length > 8192 || payloadStringsTooLarge(item.payload))
      return { error: "candidate_payload_too_large" };
    if (wordCount(item.evidence_quote) > 15) return { error: "evidence_quote_too_long", max_words: 15 };
  }
  return null;
}

function validatePostCallLegacyItems(items) {
  for (const item of items) {
    if (item.kind !== "activity")
      return { error: "post_call_legacy_kind_forbidden", allowed: ["activity"] };
    if (Object.keys(item).some(key => !["kind", "payload", "evidence_quote", "confidence"].includes(key)) ||
        Object.keys(item.payload).some(key => !["ref", "kind", "summary", "occurred_at"].includes(key)) ||
        !isUuid(item.payload.ref) || item.payload.kind !== "note" ||
        typeof item.payload.summary !== "string" || !item.payload.summary.trim() ||
        item.payload.summary.length > 500 || item.evidence_quote.length > 500 ||
        (item.payload.occurred_at != null && !validTime(item.payload.occurred_at)))
      return { error: "invalid_post_call_activity" };
  }
  return null;
}

function validatePostCallCandidates(body) {
  if (!body || typeof body.idempotency_key !== "string" || !body.idempotency_key ||
      !Array.isArray(body.items) || body.items.length === 0 || body.items.length > 100 ||
      Object.keys(body).some(key => !["session_token", "idempotency_key", "items"].includes(key)))
    return { error: "invalid_candidates" };
  for (const item of body.items) {
    if (!item || !POST_CALL_KINDS.has(item.kind) || !isUuid(item.deal_id) ||
        typeof item.evidence_quote !== "string" || typeof item.confidence !== "number" ||
        !Number.isFinite(item.confidence) || item.confidence < 0 || item.confidence > 1)
      return { error: "invalid_post_call_candidate" };
    if (item.evidence_quote.length > 500 || wordCount(item.evidence_quote) > 15)
      return { error: "evidence_quote_too_long", max_words: 15 };
    if (item.kind === "assigned_action") {
      if (Object.keys(item).some(key => !["kind", "deal_id", "assignee", "action", "due_on", "evidence_quote", "confidence"].includes(key)))
        return { error: "invalid_assigned_action" };
      if (!['joe', 'dell'].includes(item.assignee) || typeof item.action !== "string" ||
          !item.action.trim() || item.action.length > 500 ||
          (item.due_on != null && !/^\d{4}-\d{2}-\d{2}$/u.test(item.due_on)))
        return { error: "invalid_assigned_action" };
    }
    if (item.kind === "email_draft") {
      if (Object.keys(item).some(key => !["kind", "deal_id", "recipient_party_id", "recipient_ref", "subject", "body_sha256", "evidence_quote", "confidence"].includes(key)))
        return { error: "invalid_email_draft_metadata" };
      if (!isUuid(item.recipient_party_id) || typeof item.recipient_ref !== "string" ||
          !/^P-\d+$/iu.test(item.recipient_ref) || typeof item.subject !== "string" ||
          !item.subject.trim() || item.subject.length > 200 || !isSha256(item.body_sha256))
        return { error: "invalid_email_draft_metadata" };
    }
  }
  return null;
}

function postCallPayload(item) {
  if (item.kind === "assigned_action") return {
    deal_id: item.deal_id, assignee: item.assignee, action: item.action.trim(),
    due_on: item.due_on || null,
  };
  return { deal_id: item.deal_id, recipient_party_id: item.recipient_party_id,
    recipient_ref: item.recipient_ref, subject: item.subject.trim(), body_sha256: item.body_sha256 };
}

async function candidates(request, dependencies) {
  const body = await requestBody(request);
  if (!body) return json({ error: "invalid_json" }, 400);
  if (hasForbiddenCaptureKey(body)) return json({ error: "forbidden_capture_field" }, 400);
  const invalid = validateCandidates(body);
  if (invalid) return json(invalid, 400);
  const batchHash = await sha256(JSON.stringify(canonical(body.items)));
  try {
    const result = await dependencies.withWriter(async client => {
      const session = await authenticatedSession(client, body.session_token, true);
      if (session.post_call) {
        const postCallInvalid = validatePostCallLegacyItems(body.items);
        if (postCallInvalid) refuse(postCallInvalid.error === "post_call_legacy_kind_forbidden" ? 409 : 400,
          postCallInvalid);
        const filed = await client.query(
          `select 1 from capture_post_call_report where session_id=$1
            /* capture:post-call-legacy-report-lock */`, [session.id]);
        if (filed.rows.length) refuse(409, { error: "post_call_report_filed" });
        const refs = body.items.map(item => item.payload.ref);
        const exact = await client.query(
          `select deal_id from capture_call_context($1::uuid[])
            /* capture:post-call-legacy-context */`, [refs]);
        const active = new Set(exact.rows.map(row => String(row.deal_id)));
        const missing = [...new Set(refs)].filter(id => !active.has(id));
        if (missing.length) refuse(400, { error: "invalid_post_call_activity_deals", deal_ids: missing });
      }
      const prior = await client.query(
        `select batch_hash from capture_candidate
          where session_id=$1 and idempotency_key=$2 limit 1
          /* capture:candidates-prior */`, [session.id, body.idempotency_key]);
      if (prior.rows.length && prior.rows[0].batch_hash !== batchHash)
        refuse(409, { error: "key_reuse" });
      await client.query(
        `insert into capture_candidate
           (session_id, idempotency_key, batch_hash, item_index, kind, payload, evidence_quote, confidence)
         select $1, $2, $3, item.ordinality - 1, item.value->>'kind', item.value->'payload',
                item.value->>'evidence_quote', (item.value->>'confidence')::numeric
           from jsonb_array_elements($4::jsonb) with ordinality as item(value, ordinality)
         on conflict (session_id, idempotency_key, item_index) do nothing
         /* capture:candidates-insert */`,
        [session.id, body.idempotency_key, batchHash, JSON.stringify(body.items)]);
      return client.query(
        `select id, item_index from capture_candidate
          where session_id=$1 and idempotency_key=$2 order by item_index
          /* capture:candidates-result */`, [session.id, body.idempotency_key]);
    });
    return json({ ok: true, candidate_ids: result.rows.map(row => row.id) });
  } catch (error) { return dependencies.errorResponse(error); }
}

async function postCallCandidates(request, dependencies) {
  const body = await requestBody(request);
  if (!body) return json({ error: "invalid_json" }, 400);
  if (hasForbiddenCaptureKey(body)) return json({ error: "forbidden_capture_field" }, 400);
  const invalid = validatePostCallCandidates(body);
  if (invalid) return json(invalid, 400);
  const items = body.items.map(item => ({ kind: item.kind, payload: postCallPayload(item),
    evidence_quote: item.evidence_quote, confidence: item.confidence }));
  const batchHash = await sha256(JSON.stringify(canonical(items)));
  try {
    const result = await dependencies.withWriter(async client => {
      const session = await authenticatedSession(client, body.session_token, true);
      if (!session.post_call) refuse(409, { error: "not_post_call_session" });
      const filed = await client.query(
        `select 1 from capture_post_call_report where session_id=$1
          /* capture:post-call-candidates-report-lock */`, [session.id]);
      if (filed.rows.length) refuse(409, { error: "post_call_report_filed" });
      const refs = await client.query(
        `select deal_id,participant_party_id as party_id,participant_party_ref as party_ref
           from capture_call_context($1::uuid[])
          /* capture:post-call-context */`, [body.items.map(item => item.deal_id)]);
      const knownDeals = new Set(refs.rows.map(row => String(row.deal_id)));
      for (const item of body.items) {
        if (!knownDeals.has(item.deal_id))
          refuse(400, { error: "unknown_deal_id", deal_id: item.deal_id });
        if (item.kind === "email_draft" && !refs.rows.some(row =>
          String(row.deal_id) === item.deal_id && String(row.party_id) === item.recipient_party_id &&
          row.party_ref === item.recipient_ref))
          refuse(400, { error: "participant_ref_mismatch", deal_id: item.deal_id,
            party_id: item.recipient_party_id, party_ref: item.recipient_ref });
      }
      const prior = await client.query(
        `select batch_hash from capture_post_call_candidate where session_id=$1 and idempotency_key=$2 limit 1
          /* capture:post-call-candidates-prior */`, [session.id, body.idempotency_key]);
      if (prior.rows.length && prior.rows[0].batch_hash !== batchHash)
        refuse(409, { error: "key_reuse" });
      await client.query(
        `insert into capture_post_call_candidate
           (session_id,idempotency_key,batch_hash,item_index,kind,deal_id,assignee_slug,
            action_description,due_on,recipient_party_id,recipient_ref,email_subject,body_sha256,
            evidence_quote,confidence)
         select $1,$2,$3,item.ordinality - 1,item.value->>'kind',(item.value->'payload'->>'deal_id')::uuid,
                item.value->'payload'->>'assignee',item.value->'payload'->>'action',
                (item.value->'payload'->>'due_on')::date,(item.value->'payload'->>'recipient_party_id')::uuid,
                item.value->'payload'->>'recipient_ref',item.value->'payload'->>'subject',
                item.value->'payload'->>'body_sha256',item.value->>'evidence_quote',
                (item.value->>'confidence')::numeric
           from jsonb_array_elements($4::jsonb) with ordinality as item(value, ordinality)
         on conflict (session_id,idempotency_key,item_index) do nothing
         /* capture:post-call-candidates-insert */`,
        [session.id, body.idempotency_key, batchHash, JSON.stringify(items)]);
      return client.query(
        `select id,item_index from capture_post_call_candidate where session_id=$1 and idempotency_key=$2 order by item_index
          /* capture:post-call-candidates-result */`, [session.id, body.idempotency_key]);
    });
    return json({ ok: true, candidate_ids: result.rows.map(row => row.id) });
  } catch (error) { return dependencies.errorResponse(error); }
}

async function callContext(request, dependencies) {
  const url = new URL(request.url);
  const dealIds = url.searchParams.getAll("deal_id");
  if (!dealIds.length || dealIds.length > 50 || dealIds.some(id => !isUuid(id)) ||
      new Set(dealIds).size !== dealIds.length)
    return json({ error: "invalid_call_context_deals" }, 400);
  try {
    const result = await dependencies.withWriter(async client => {
      await authenticatedSession(client, bearer(request));
      return client.query(
        `select deal_id,deal_name,owner,operating_state,participant_party_id,participant_party_ref,
                participant_name,participant_email,participant_role
           from capture_call_context($1::uuid[])
          order by deal_name,deal_id,participant_role,participant_name
          /* capture:call-context */`, [dealIds]);
    });
    if (new Set(result.rows.map(row => String(row.deal_id))).size !== dealIds.length)
      return json({ error: "invalid_call_context_deals" }, 400);
    const byDeal = new Map();
    for (const row of result.rows) {
      const deal = byDeal.get(row.deal_id) || { id: row.deal_id, name: row.deal_name,
        owner: row.owner, operating_state: row.operating_state, participants: [] };
      if (row.participant_role) deal.participants.push({ party_id: row.participant_party_id || null,
        ref: row.participant_party_ref || null, name: row.participant_name || null,
        email: row.participant_email || null, role: row.participant_role });
      byDeal.set(row.deal_id, deal);
    }
    return json({ deals: [...byDeal.values()] });
  } catch (error) { return dependencies.errorResponse(error); }
}

async function postCallReport(request, dependencies) {
  const body = await requestBody(request);
  if (!body) return json({ error: "invalid_json" }, 400);
  if (hasForbiddenCaptureKey(body) || Object.keys(body).some(key => !["session_token", "idempotency_key", "report_sha256", "candidate_count"].includes(key)) ||
      typeof body.idempotency_key !== "string" || !body.idempotency_key ||
      !isSha256(body.report_sha256) || !Number.isInteger(body.candidate_count) || body.candidate_count < 0)
    return json({ error: "invalid_post_call_report" }, 400);
  try {
    const result = await dependencies.withWriter(async client => {
      const session = await authenticatedSession(client, body.session_token, true);
      if (!session.post_call) refuse(409, { error: "not_post_call_session" });
      const totals = await client.query(
        `select count(*)::int as total, count(*) filter (where status='pending')::int as pending
           from (
             select status from capture_candidate where session_id=$1
             union all
             select status from capture_post_call_candidate where session_id=$1
           ) c /* capture:post-call-report-count */`, [session.id]);
      const { total, pending } = totals.rows[0];
      if (pending !== 0) refuse(409, { error: "post_call_candidates_pending", pending, total });
      if (total !== body.candidate_count)
        refuse(409, { error: "candidate_count_mismatch", expected: total, received: body.candidate_count });
      const prior = await client.query(
        `select report_sha256,idempotency_key,candidate_count from capture_post_call_report where session_id=$1
          /* capture:post-call-report-prior */`, [session.id]);
      if (prior.rows.length) {
        const report = prior.rows[0];
        if (report.idempotency_key !== body.idempotency_key || report.report_sha256 !== body.report_sha256 ||
            report.candidate_count !== body.candidate_count)
          refuse(409, { error: "report_already_filed" });
        return { filed: true, already: true, candidate_count: report.candidate_count };
      }
      await client.query(
        `insert into capture_post_call_report (session_id,idempotency_key,report_sha256,candidate_count)
         values ($1,$2,$3,$4) /* capture:post-call-report-insert */`,
        [session.id, body.idempotency_key, body.report_sha256, body.candidate_count]);
      return { filed: true, already: false, candidate_count: total };
    });
    return json({ ok: true, ...result });
  } catch (error) { return dependencies.errorResponse(error); }
}

async function session(request, dependencies) {
  try {
    const result = await dependencies.withWriter(async client => {
      const authenticated = await authenticatedSession(client, bearer(request));
      return client.query(
        `select s.state,s.post_call,
                count(c.id) filter (where c.status='pending')::int as pending,
                count(c.id) filter (where c.status='confirmed')::int as confirmed,
                count(c.id) filter (where c.status='skipped')::int as skipped,
                max(c.resulting_ref) filter
                  (where c.kind='meeting_record' and c.status='confirmed') as meeting_record,
                (select jsonb_build_object('filed',true,'sha256',r.report_sha256,
                  'candidate_count',r.candidate_count)
                   from capture_post_call_report r where r.session_id=s.id) as post_call_report,
                coalesce(jsonb_agg(jsonb_build_object('id',c.id,'kind',c.kind,'status',c.status,
                  'resulting_ref',c.resulting_ref,'source',c.source))
                  filter (where c.id is not null), '[]'::jsonb) as candidate_statuses,
                coalesce(jsonb_agg(jsonb_build_object('id',c.id,'kind',c.kind,'status',c.status,
                  'resulting_ref',c.resulting_ref))
                  filter (where c.source='post_call'), '[]'::jsonb) as post_call_candidates
           from capture_session s left join (
             select id,session_id,kind,status,resulting_ref,'legacy'::text as source from capture_candidate
             union all
             select id,session_id,kind,status,resulting_ref,'post_call'::text as source from capture_post_call_candidate
           ) c on c.session_id=s.id
          where s.id=$1 group by s.id, s.state /* capture:session-read */`, [authenticated.id]);
    });
    const row = result.rows[0];
    return json({ state: row.state, post_call: row.post_call, candidates: {
      pending: row.pending, confirmed: row.confirmed, skipped: row.skipped,
    }, post_call_candidates: row.post_call_candidates || [],
    candidate_statuses: row.candidate_statuses || [],
    post_call_report: row.post_call_report || { filed: false },
    meeting_record: row.post_call ? null : (row.meeting_record || null) });
  } catch (error) { return dependencies.errorResponse(error); }
}

export function createCaptureHandler(overrides = {}) {
  const configured = { randomStringFn: randomString, ...overrides };
  return {
    async fetch(request, env) {
      const dependencies = { ...configured, errorResponse: error => errorResponse(error, env) };
      const path = new URL(request.url).pathname;
      if (path === "/capture/claim" && request.method === "POST") return claim(request, env, dependencies);
      if (path === "/capture/status" && request.method === "POST") return status(request, dependencies);
      if (path === "/capture/candidates" && request.method === "POST") return candidates(request, dependencies);
      if (path === "/capture/post-call/candidates" && request.method === "POST") return postCallCandidates(request, dependencies);
      if (path === "/capture/post-call/report" && request.method === "POST") return postCallReport(request, dependencies);
      if (path === "/capture/call-context" && request.method === "GET") return callContext(request, dependencies);
      if (path === "/capture/session" && request.method === "GET") return session(request, dependencies);
      return json({ error: "not_found" }, 404);
    },
  };
}
