// Server-side Jev call log: the Worker makes the TypeSafe (Jev) call itself and
// appends a server-timestamped receipt before it hands the answers back.
//
// WHY THE WORKER AND NOT THE CALLER. Jev enforcement gates used to credit
// evidence the gated model could write: the session transcript and
// out/jev-calls.jsonl. The model shares one OS user with the hooks, so any
// local log or local credential is forgeable. The Worker appends the receipt
// in its writer transaction, and recorded_at is the database clock. A paid
// call from outside the Worker carries a shared admission reservation signed
// with JEV_ADMISSION_SECRET, which only the Worker and the local reservation
// owner hold (never the vendor key); the pending attempt ledger consumes each
// signed reservation once. An in-Worker app_runtime caller (Deal Room) is
// admitted here instead: ingress cannot select that class.
//
// DETECTABLE, NOT PREVENTED. This store raises the cost of forging a Jev call;
// it does not make forgery impossible. The model's OS user can read the
// database owner credential, and with it can insert, backdate or delete rows
// directly, or disable the append-only trigger. What makes that DETECTABLE:
//   - read-jev-call-receipts credits a receipt only when the envelope ledger
//     (public.tool_call) holds the matching ask-jev row -- same idempotency
//     key, same server-derived actor, and a stored response naming this
//     receipt_id. A row inserted straight into ops.jev_call_receipt has no
//     such partner and is not credited.
//   - read-jev-call-receipt-integrity (wired into ./run.sh health) counts
//     receipts with no matching tool_call row (ask-jev-attempt for pending
//     reservations, ask-jev for answers) and reports whether the
//     append-only triggers are enabled right now. A disable-then-re-enable
//     between two polls is not seen.
//
// THE VENDOR CALL HAPPENS OUTSIDE THE WRITER TRANSACTION. mcp.js validates an
// ask-jev request and calls Jev (prefetchJevAnswer) BEFORE it connects the
// writer pool or opens a transaction, then hands the result to the handler as
// client.jevPrefetched. The handler only appends the receipt, in a short
// transaction; it never calls the vendor. On an envelope replay the prefetched
// answer is discarded and the stored response returned. Break-glass
// (local-verb.mjs) calls executeRegisteredTool directly with no prefetched
// answer, so that door refuses as jev_proxy_unconfigured instead of reading a
// key locally.
//
// THREE VERBS, ONE STORE (migrations/0587_jev_call_receipt.sql):
//   ask-jev                            write: record the Worker's Jev call, return answers
//   read-jev-call-receipts             read:  the caller's credited receipts for a session
//   read-jev-call-receipt-integrity    read:  the detection audit
//
// THE KEY NEVER LEAVES THIS MODULE. It is read from env once per binding, sent
// only in the Authorization header, and never echoed, logged, returned, or put
// in an error. An upstream error body is truncated and scrubbed of the key
// string before it can reach a ToolError.

import requestContract from "./jev-request-contract.v1.json" with { type: "json" };
import { ToolError as LeafToolError } from "./tool-error.js";
import { judgeBinding, providerFor } from "./judge-provider.js";

const ENDPOINT = "https://api.typesafe.ai/v1/systemone";
const USER_AGENT = "carr-worker-jev-proxy/1.0";
const DEFAULT_MODEL = "jev-latest";
// One reserved vendor attempt, with a total upstream budget. The local
// client budgets ~14s for the whole path under a 20s UserPromptSubmit hook.
const TOTAL_BUDGET_MS = 10000;
const MAX_ERROR_BODY_CHARS = 300;
const JEV_CACHE_SECONDS = 60;
const MAX_STATE_CHARS = 96000;
const MAX_QUESTIONS = requestContract.max_questions;
const MAX_SESSION_ID_CHARS = 200;
const READ_LIMIT_DEFAULT = 200;
const READ_LIMIT_MAX = 500;

export const JEV_PURPOSES = Object.freeze(["call", "build_advisory"]);
export const JEV_QUESTION_TYPES = Object.freeze(Object.keys(requestContract.question_types));
export const JEV_FACETS = Object.freeze([
  "architecture_or_design", "semantic_creation", "diagnosis",
  "verification_selection", "evidence_matching", "next_action_priority",
]);

// Python's sort_keys orders by code point; JS's default sort orders by UTF-16
// code unit. The two disagree only when an astral character (surrogate pair)
// meets a BMP character at or above U+E000, but a digest that must equal
// hashlib's has to agree there too.
function compareCodePoints(left, right) {
  const a = Array.from(left), b = Array.from(right);
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i].codePointAt(0), y = b[i].codePointAt(0);
    if (x !== y) return x - y;
  }
  return a.length - b.length;
}

// Canonical JSON: recursively sorted object keys, no whitespace, JSON.stringify
// string escaping. Equal, byte for byte, to Python's
//   json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=False)
// for strings, objects, arrays, integers, booleans and null. (Floats are not
// promised: the two languages print some of them differently, e.g. 1e-05.)
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") {
    const encoded = JSON.stringify(value);
    return encoded === undefined ? "null" : encoded;
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const keys = Object.keys(value).filter(key => value[key] !== undefined).sort(compareCodePoints);
  return `{${keys.map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function canonicalSha256(value) {
  return sha256Hex(canonicalJson(value));
}

// Expiry only bounds an unused proof; single use is the attempt ledger's job.
// No upper bound: the signer holds the secret and a clock ahead of the
// Worker's is not a forgery.
async function verifyAdmission(secret, token, session, now) {
  const parts = typeof token === "string" ? token.split(".") : [];
  if (typeof secret !== "string" || secret.trim() === "" ||
      parts.length !== 3 || parts[0] !== "jev1" || !/^[a-f0-9]{64}$/.test(parts[2]))
    throw new LeafToolError({ error: "jev_admission_required" });
  try {
    const signer = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
      { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
    const signature = Uint8Array.from(parts[2].match(/../g), byte => parseInt(byte, 16));
    const valid = await crypto.subtle.verify("HMAC", signer, signature, new TextEncoder().encode(parts[1]));
    const payload = JSON.parse(new TextDecoder().decode(Uint8Array.from(
      atob(parts[1].replaceAll("-", "+").replaceAll("_", "/")), c => c.charCodeAt(0))));
    if (!valid || !Array.isArray(payload) || payload.length !== 5 || payload[0] !== 1 ||
        !Number.isInteger(payload[1]) || payload[1] * 1000 <= now ||
        typeof payload[2] !== "string" || !payload[2] || typeof payload[3] !== "string" || !payload[3] ||
        payload[4] !== session)
      throw new Error("invalid admission");
  } catch { throw new LeafToolError({ error: "jev_admission_required" }); }
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// THE FULL DISTRIBUTION, NOT JUST THE PICK. Joe's ruling is that code decides
// and Jev only judges, with thresholds calibrated per action. A pick cannot be
// calibrated after the fact; the distribution it came from can. These mirror
// answer_distribution() in ops/typesafe_client.py exactly, and both suites run
// ops/fixtures/jev-calibration/distribution-vectors.v1.json. Entropy is in bits
// over the renormalized distribution, null unless every probability is valid
// and they sum to one within the tolerance (numeric sanity for rounded vendor
// probabilities, not a decision boundary). Nothing here is a threshold.
export const PROBABILITY_SUM_TOLERANCE = 0.02;

export function modelIsPinned(model) {
  return typeof model === "string" && model.trim() !== "" && !/(?:^|-)latest$/.test(model.trim());
}

function isProbability(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

export function answerDistribution(question, answer) {
  const q = isPlainObject(question) ? question : {};
  const a = isPlainObject(answer) ? answer : {};
  const kind = q.type ?? a.type ?? null;
  let distribution = null;
  let top = null;
  if (kind === "noul") {
    if (isProbability(a.noul)) distribution = { true: a.noul, false: 1 - a.noul };
  } else if (kind === "choice") {
    const offered = isPlainObject(q.criteria) ? Object.keys(q.criteria)
      : Array.isArray(q.choices) ? q.choices.map(String) : [];
    top = typeof a.choice === "string" ? a.choice : null;
    if (isPlainObject(a.probabilities)) {
      distribution = {};
      for (const key of [...offered, ...Object.keys(a.probabilities).filter(k => !offered.includes(k))])
        distribution[key] = Object.hasOwn(a.probabilities, key) ? a.probabilities[key] : 0;
    }
  } else if (kind === "score") {
    const levels = Array.isArray(q.criteria) ? q.criteria : [];
    if (isPlainObject(a.probabilities)) {
      const raw = a.probabilities;
      const count = Math.max(levels.length,
        ...Object.keys(raw).filter(k => /^\d+$/.test(k)).map(k => Number(k) + 1));
      distribution = {};
      for (let index = 0; index < count; index++) {
        const level = index < levels.length ? levels[index] : null;
        distribution[String(index)] = level !== null && Object.hasOwn(raw, level) ? raw[level]
          : Object.hasOwn(raw, String(index)) ? raw[String(index)] : 0;
      }
    }
  }
  let complete = false, total = null, entropy = null, topProbability = null;
  if (distribution) {
    const values = Object.values(distribution);
    if (values.length > 0 && values.every(isProbability)) {
      total = values.reduce((sum, v) => sum + v, 0);
      complete = Math.abs(total - 1) <= PROBABILITY_SUM_TOLERANCE;
      if (complete && total > 0)
        entropy = values.reduce((sum, v) => v > 0 ? sum - (v / total) * Math.log2(v / total) : sum, 0) + 0;
      const best = Math.max(...values);
      top = Object.keys(distribution).find(key => distribution[key] === best);
      topProbability = best;
    }
  }
  return { type: kind, distribution, distribution_complete: complete, probability_sum: total,
    entropy_bits: entropy, top, top_probability: topProbability };
}

// What a later calibration needs about one Worker call. It rides in the
// ask-jev response, so the envelope ledger (public.tool_call.response) keeps
// it server-side beside the receipt, and every number in it is recomputable
// from the receipt's own stored answers, model and state digest.
// Recording must never turn a committed receipt into a failed ask-jev.
function safeCalibrationBlock(...args) {
  try { return calibrationBlock(...args); } catch { return null; }
}

export function calibrationBlock(questions, answered, modelRequested, stateSha) {
  const answers = isPlainObject(answered?.answers) ? answered.answers : {};
  const perQuestion = {};
  for (const key of Object.keys(answers).sort(compareCodePoints))
    perQuestion[key] = answerDistribution(questions?.[key], answers[key]);
  return { schema: "carr.jev-calibration.v1", model_requested: modelRequested,
    model_answered: answered?.model ?? null, model_pinned: modelIsPinned(modelRequested),
    state_sha256: stateSha, questions: perQuestion };
}

function upstreamFailure(status, reason, body, key) {
  let text = typeof body === "string" ? body : "";
  // An upstream that echoes the request back must not carry the key out.
  if (key) text = text.split(key).join("[redacted]");
  text = text.slice(0, MAX_ERROR_BODY_CHARS);
  return new LeafToolError({ error: "jev_upstream_failed", status: status ?? null, reason,
    ...(text ? { body: text } : {}) });
}

// Returns the Worker's Jev caller, or null when the Worker holds no key.
// mcp.js calls it before the writer transaction opens (prefetchJevAnswer).
// Production supplies reserveAttempt; tests also inject time and transport.
// The second argument is the in-process call context ({ workClass }), never
// request data, so a caller cannot claim the Worker-admitted runtime class.
export function jevAskBinding(env, fetchImpl = fetch, options = {}) {
  const key = env?.TYPESAFE_API_KEY;
  if (typeof key !== "string" || key.trim() === "") return null;
  const now = options.now ?? (() => Date.now());
  const budgetMs = options.budgetMs ?? TOTAL_BUDGET_MS;
  const cache = options.cache ?? (typeof caches !== "undefined" ? caches.default : null);
  const reserveAttempt = options.reserveAttempt;
  const cacheKeyFor = async body =>
    new Request(`https://jev-cache-v2.invalid/${await sha256Hex(`${key}\n${body}`)}`);
  const jevAsk = async function ({ state, model, questions, transport_mode, idempotency_key, session_id },
    { workClass } = {}) {
    const body = JSON.stringify({ state, model, questions });
    // The Cache API is shared across Worker isolates in a colo. Only the
    // answer is cached; the key contains digests of the request and account.
    // A hit has no billable usage and still gets a server receipt as a replay.
    let cacheKey;
    if (cache && transport_mode !== "paid_once") {
      cacheKey = await cacheKeyFor(body);
      try {
        const hit = await cache.match(cacheKey);
        if (hit) {
          const prior = await hit.json();
          if (isPlainObject(prior) && isPlainObject(prior.answers) &&
              typeof prior.model === "string" && prior.model.trim())
            return { model: prior.model, answers: prior.answers, usage: null, cache_hit: true };
        }
      } catch { /* A broken cache must not hide the vendor's answer. */ }
    }
    // A cache probe never reserves or spends, even when caching is unavailable.
    // The capped client reserves locally before selecting paid_once; that mode
    // skips cache reads and permits exactly one vendor attempt.
    if (transport_mode === "cache_only")
      throw new LeafToolError({ error: "jev_cache_miss" });
    if (workClass !== "app_runtime")
      await verifyAdmission(env.JEV_ADMISSION_SECRET, idempotency_key, session_id, now());
    if (!reserveAttempt) throw new LeafToolError({ error: "jev_receipt_store_unavailable" });
    let reservedAttempt;
    try { reservedAttempt = await reserveAttempt(); }
    catch { throw new LeafToolError({ error: "jev_receipt_store_unavailable" }); }
    if (typeof reservedAttempt?.key !== "string" || !reservedAttempt.key ||
        typeof reservedAttempt?.receipt_id !== "string" || !reservedAttempt.receipt_id)
      throw new LeafToolError({ error: "jev_receipt_store_unavailable" });
    if (budgetMs <= 0) throw upstreamFailure(null, "timeout", "", key);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), budgetMs);
    let response;
    try {
      response = await fetchImpl(ENDPOINT, {
        method: "POST",
        headers: {
          authorization: `Bearer ${key}`,
          "content-type": "application/json",
          "user-agent": USER_AGENT,
        },
        body,
        signal: controller.signal,
      });
    } catch (error) {
      clearTimeout(timer);
      // Never the error's own message: a fetch failure can quote its request.
      throw upstreamFailure(null, controller.signal.aborted || error?.name === "AbortError"
        ? "timeout" : "network", "", key);
    }
    try {
      if (!response.ok) {
        const text = await response.text().catch(() => "");
        throw upstreamFailure(response.status, "http_status", text, key);
      }
      let parsed;
      try { parsed = await response.json(); }
      catch { throw upstreamFailure(response.status, controller.signal.aborted ? "timeout" : "invalid_json", "", key); }
      if (!isPlainObject(parsed) || !isPlainObject(parsed.answers) || typeof parsed.model !== "string" ||
          parsed.model.trim() === "")
        throw upstreamFailure(response.status, "invalid_answer_shape", "", key);
      const answer = {
        model: parsed.model,
        answers: parsed.answers,
        usage: isPlainObject(parsed.usage) ? parsed.usage : null,
        attempt: reservedAttempt,
      };
      return answer;
    } catch (error) {
      if (error instanceof LeafToolError) throw error;
      throw upstreamFailure(response?.status ?? null,
        controller.signal.aborted ? "timeout" : "network", "", key);
    } finally {
      clearTimeout(timer);
    }
  };
  // Only the caller that committed the matching receipt may promote an answer.
  jevAsk.cacheAfterCommit = async (request, answer) => {
    if (!cache || !answer || answer.cache_hit === true) return;
    try {
      const cacheKey = await cacheKeyFor(JSON.stringify(request));
      await cache.put(cacheKey, new Response(JSON.stringify({
        model: answer.model, answers: answer.answers,
      }), { headers: { "cache-control": `max-age=${JEV_CACHE_SECONDS}` } }));
    } catch { /* A cache failure cannot change a committed receipt. */ }
  };
  return jevAsk;
}

// Reserve the receipt and its pending ledger partner atomically before fetch.
// Vendor failure leaves linked unknown spend, never an orphan or credited answer.
export async function reserveJevCallAttempt(client, actor, args) {
  const { stateJson, questions, facets, model } = validateAskJevArgs(args);
  const key = `jev-attempt:${args.idempotency_key}`;
  await client.query("begin");
  try {
    const row = (await client.query(
      `select r.receipt_id from ops.record_jev_call_receipt($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) r`,
      [args.session_id, "call", Object.keys(questions).sort(compareCodePoints), facets,
        model, "jev-attempt-pending", await sha256Hex(stateJson),
        await canonicalSha256(questions), await canonicalSha256({}), null,
        JSON.stringify({}), null, actor.id, actor.slug, key],
    )).rows[0];
    if (!row?.receipt_id) throw new LeafToolError({ error: "jev_receipt_store_unavailable" });
    await client.query(
      `insert into tool_call (idempotency_key, verb, actor_id, request_hash, response)
       values ($1,'ask-jev-attempt',$2,$3,$4)`,
      [key, actor.id, await sha256Hex(key),
        JSON.stringify({ receipt_id: row.receipt_id, cache_hit: false })],
    );
    await client.query("commit");
    return { key, receipt_id: row.receipt_id };
  } catch (error) {
    await client.query("rollback");
    throw error;
  }
}

function validateSessionId(ToolError, value) {
  if (typeof value !== "string" || value.length < 1 || value.length > MAX_SESSION_ID_CHARS)
    throw new ToolError({ error: "jev_session_id_invalid",
      hint: `session_id must be a string of 1..${MAX_SESSION_ID_CHARS} characters` });
}

function validateQuestions(ToolError, questions) {
  if (!isPlainObject(questions))
    throw new ToolError({ error: "jev_questions_invalid" });
  const entries = Object.entries(questions);
  if (entries.length < requestContract.min_questions || entries.length > MAX_QUESTIONS)
    throw new ToolError({ error: "jev_questions_invalid", reason: "question_count_invalid" });
  for (const [index, [id, question]] of entries.entries()) {
    const refuse = reason => { throw new ToolError({ error: "jev_questions_invalid",
      question_index: index, reason }); };
    if (!id || !isPlainObject(question)) refuse("question_invalid");
    const rule = typeof question.type === "string" && Object.hasOwn(requestContract.question_types, question.type)
      ? requestContract.question_types[question.type] : null;
    if (!rule) refuse("type_invalid");
    if (typeof question.instructions !== "string" || !question.instructions.trim())
      refuse("instructions_invalid");
    const criteria = question.criteria;
    if (criteria == null && rule.optional) continue;
    const rightShape = rule.criteria === "object" ? isPlainObject(criteria) : Array.isArray(criteria);
    if (!rightShape || Object.keys(criteria).length < rule.min_items) refuse("criteria_invalid");
  }
}

// Every ask-jev input check that does not need the database. Run by the
// handler and, before any vendor call, by prefetchJevAnswer -- so an invalid
// request never reaches Jev.
export function validateAskJevArgs(args, ToolError = LeafToolError) {
  validateSessionId(ToolError, args?.session_id);
  if (args.transport_mode !== undefined && !["cache_only", "paid_once"].includes(args.transport_mode))
    throw new ToolError({ error: "jev_transport_mode_invalid" });
  if (!JEV_PURPOSES.includes(args.purpose))
    throw new ToolError({ error: "jev_purpose_invalid", allowed: [...JEV_PURPOSES] });
  const state = args.state;
  if (typeof state !== "string" && !isPlainObject(state))
    throw new ToolError({ error: "jev_state_invalid", hint: "state must be a string or an object" });
  const stateJson = canonicalJson(state);
  if (stateJson.length > MAX_STATE_CHARS)
    throw new ToolError({ error: "jev_state_too_large", limit: MAX_STATE_CHARS, got: stateJson.length });
  if (args.purpose === "build_advisory" &&
      (!isPlainObject(state) || typeof state.partner_request !== "string"))
    throw new ToolError({ error: "jev_build_advisory_state_invalid",
      hint: "purpose build_advisory needs state to be an object with a string partner_request" });
  const questions = args.questions;
  validateQuestions(ToolError, questions);
  const facets = args.facets === undefined || args.facets === null ? [] : args.facets;
  if (!Array.isArray(facets) || facets.some(facet => !JEV_FACETS.includes(facet)))
    throw new ToolError({ error: "jev_facets_invalid", allowed: [...JEV_FACETS] });
  const model = args.model === undefined || args.model === null ? DEFAULT_MODEL : args.model;
  if (typeof model !== "string" || model.trim() === "" || model.length > 200)
    throw new ToolError({ error: "jev_model_invalid" });
  return { state, stateJson, questions, facets: [...facets], model };
}

// Called by mcp.js BEFORE the writer transaction: validate, then ask Jev.
// Validation failures throw; an upstream failure is returned (not thrown) so
// the handler can raise it inside the envelope, where a same-key replay still
// returns the stored response instead.
export async function prefetchJevAnswer(args, ask, workClass = "system_work") {
  const { state, model, questions } = validateAskJevArgs(args);
  providerFor(workClass);
  try {
    return { ok: true, result: await judgeBinding(ask, workClass)({ state, model, questions,
      idempotency_key: args.idempotency_key, session_id: args.session_id,
      ...(args.transport_mode !== undefined ? { transport_mode: args.transport_mode } : {}) },
      { workClass }) };
  } catch (error) {
    if (error instanceof LeafToolError) return { ok: false, error: error.payload };
    return { ok: false, error: { error: "jev_upstream_failed", status: null, reason: "network" } };
  }
}

export function jevCallReceiptTools({ withEnvelope, ToolError }) {
  return {
    "ask-jev": {
      write: true,
      // mcp.js asks Jev for tools carrying this flag before it opens the writer
      // transaction, and hands the answer over as client.jevPrefetched.
      jevProxy: true,
      description: "Ask Jev (TypeSafe) through the Worker and record a server-timestamped, append-only receipt of the call before the answers are returned. Paid calls require the shared client reservation proof in idempotency_key; cache-only calls remain free. purpose 'call' is an ordinary Jev question set; purpose 'build_advisory' requires state.partner_request (a string) and also records prompt_sha256. Returns the answers, the answered model, usage, the receipt id, the server's recorded_at, and a calibration block (each answer's full distribution and entropy in bits, the requested and answered model, whether the requested model is pinned, and the state digest). Refuses jev_proxy_unconfigured when the Worker holds no key, jev_upstream_failed when Jev does not answer within 10 seconds.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          session_id: { type: "string", minLength: 1, maxLength: MAX_SESSION_ID_CHARS },
          purpose: { type: "string", enum: [...JEV_PURPOSES] },
          state: { type: ["string", "object"] },
          questions: { type: "object" },
          facets: { type: "array", items: { type: "string", enum: [...JEV_FACETS] } },
          model: { type: "string" },
          transport_mode: { type: "string", enum: ["cache_only", "paid_once"] },
        },
        required: ["idempotency_key", "session_id", "purpose", "state", "questions"],
      },
      handler: async (c, actor, args) => {
        const { state, stateJson, questions, facets, model } = validateAskJevArgs(args, ToolError);
        return withEnvelope(c, actor, "ask-jev", args, async () => {
          const prefetched = c.jevPrefetched;
          if (!prefetched)
            throw new ToolError({ error: "jev_proxy_unconfigured",
              hint: "the Worker holds no TYPESAFE_API_KEY secret; Joe binds it by hand" });
          if (prefetched.ok !== true) throw new ToolError(prefetched.error);
          const answered = prefetched.result;
          if (!actor?.id || !actor?.slug)
            throw new ToolError({ error: "jev_call_receipt_actor_unresolved" });
          const stateSha = await sha256Hex(stateJson);
          const questionsSha = await canonicalSha256(questions);
          const answersSha = await canonicalSha256(answered.answers);
          const promptSha = args.purpose === "build_advisory"
            ? await canonicalSha256(state.partner_request) : null;
          const questionIds = Object.keys(questions).sort(compareCodePoints);
          // actor id and slug are the server-authenticated actor's, never a
          // caller argument; the door re-checks them against the actor table.
          const row = (await c.query(
            `select r.receipt_id, to_jsonb(r.recorded_at)#>>'{}' as recorded_at
               from ops.record_jev_call_receipt($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) r`,
            [args.session_id, args.purpose, questionIds, facets, model, answered.model,
              stateSha, questionsSha, answersSha, promptSha,
              JSON.stringify(answered.answers), answered.usage ? JSON.stringify(answered.usage) : null,
              actor.id, actor.slug, args.idempotency_key],
          )).rows[0];
          if (!row?.receipt_id) throw new ToolError({ error: "jev_call_receipt_refused" });
          if (answered.attempt) {
            const settlement = await c.query(
              `update tool_call set response = $4::jsonb
                where idempotency_key = $1 and verb = 'ask-jev-attempt'
                  and actor_id = $2 and response->>'receipt_id' = $3
                  and response->>'cache_hit' = 'false'
                returning idempotency_key`,
              [answered.attempt.key, actor.id, answered.attempt.receipt_id,
                JSON.stringify({ receipt_id: answered.attempt.receipt_id,
                  cache_hit: true, settled_by: row.receipt_id })],
            );
            if (settlement.rows.length !== 1)
              throw new ToolError({ error: "jev_attempt_settlement_mismatch" });
          }
          return {
            ok: true,
            receipt_id: row.receipt_id,
            recorded_at: row.recorded_at,
            purpose: args.purpose,
            session_id: args.session_id,
            model: answered.model,
            answers: answered.answers,
            usage: answered.usage ?? null,
            cache_hit: answered.cache_hit === true,
            state_sha256: stateSha,
            prompt_sha256: promptSha,
            calibration: safeCalibrationBlock(questions, answered, model, stateSha),
          };
        });
      },
    },

    "read-jev-call-receipts": {
      write: false,
      description: "Read the calling actor's credited Jev call receipts for one session, oldest first, from `since` (default: 24 hours before the server's clock). A receipt is credited only when the envelope ledger holds its matching ask-jev call by the same actor. Returns at most `limit` (default 200, max 500) receipts -- the OLDEST ones from since -- and truncated: true when more exist. Each receipt carries receipt_id, the server's recorded_at, purpose, question_ids, facets, model, state_sha256, prompt_sha256, and answers only for purpose build_advisory (null for call). No state text is stored or returned. server_now is the database clock at read time.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          session_id: { type: "string", minLength: 1, maxLength: MAX_SESSION_ID_CHARS },
          since: { type: "string" },
          limit: { type: "integer", minimum: 1, maximum: READ_LIMIT_MAX },
        },
        required: ["session_id"],
      },
      handler: async (c, actor, args) => {
        validateSessionId(ToolError, args.session_id);
        let since = null;
        if (args.since !== undefined && args.since !== null) {
          if (typeof args.since !== "string" || !/^\d{4}-\d{2}-\d{2}/.test(args.since) ||
              !Number.isFinite(Date.parse(args.since)))
            throw new ToolError({ error: "jev_since_invalid", hint: "since must be an ISO-8601 timestamp" });
          since = args.since;
        }
        const limit = args.limit === undefined || args.limit === null ? READ_LIMIT_DEFAULT : args.limit;
        if (!Number.isInteger(limit) || limit < 1 || limit > READ_LIMIT_MAX)
          throw new ToolError({ error: "jev_limit_invalid", hint: `limit must be an integer 1..${READ_LIMIT_MAX}` });
        if (!actor?.slug) throw new ToolError({ error: "jev_call_receipt_actor_unresolved" });
        const row = (await c.query(
          "select ops.read_jev_call_receipts($1, $2::timestamptz, $3, $4) as result",
          [args.session_id, since, limit, actor.slug],
        )).rows[0];
        const result = typeof row?.result === "string" ? JSON.parse(row.result) : row?.result;
        if (!isPlainObject(result) || !Array.isArray(result.receipts))
          throw new ToolError({ error: "jev_call_receipts_unavailable" });
        return { ok: true, session_id: args.session_id, since: result.since,
          server_now: result.server_now, truncated: result.truncated === true,
          receipts: result.receipts };
      },
    },

    "read-jev-call-receipt-integrity": {
      write: false,
      description: "Audit the server-side Jev call log for tampering that the store detects but cannot prevent: how many receipts exist, how many have no matching ask-jev row in the envelope ledger (count plus up to 20 receipt ids), and whether the append-only triggers are enabled right now. A disable-then-re-enable between two audits is not seen. ./run.sh health runs this.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c) => {
        const row = (await c.query("select ops.jev_call_receipt_integrity() as result")).rows[0];
        const result = typeof row?.result === "string" ? JSON.parse(row.result) : row?.result;
        if (!isPlainObject(result) || !isPlainObject(result.receipts_without_tool_call))
          throw new ToolError({ error: "jev_call_receipt_integrity_unavailable" });
        return { ok: true, ...result };
      },
    },
  };
}
