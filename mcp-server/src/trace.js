// trace.js — Program 4, Gap A2 (defect cae5be2e-267c-40ba-9424-b4618845e905,
// 2026-08-14). correlation.js mints and echoes an x-correlation-id on every
// route and sets env.CORRELATION_ID, but nothing wrote it anywhere a failed
// request could later be found by it. An outage drill proved the gap live:
// an unauthenticated /mcp call to staging returned a truthful 401 carrying the
// id, and ops.v_trace on staging had nothing for it. See migrations/
// 0122_worker_trace.sql for the schema half of this fix (tool_call.correlation_id
// and event.correlation_id — the MATERIAL WRITE half, wired in mcp.js's
// dispatch() and tools.js's auditIdentity()/withEnvelope()/writeEvent()) and
// that migration's header for why FAILURES land in ops.incident rather than a
// new ops.run row per request.
//
// LIKE correlation.js, THE CLASSIFIER HALF OF THIS FILE IS DB-FREE AND PURE —
// httpFailureClass, rpcInternalErrorFailureClass, and incidentSignature take
// no client, no env, no cloudflare: import, so node --test exercises them
// directly. recordWorkerFailure touches a database only through an injected
// `query` function (text, params) => Promise<{rows}>, the exact convention
// mcp.js's readCallInsertSQL/recordReadCall already use — so ITS logic is also
// unit-testable with a fake. THE ONE REMAINING SEAM, wrapNeonRows, is the
// adapter between that internal {rows} contract and what neon(dsn).query()
// ACTUALLY returns (a bare array — see its own comment for the production
// incident this caused), and it is exported and unit-tested on its own for
// exactly that reason: it is the one place a correct-looking internal contract
// met a real driver and disagreed, silently, in production.
//
// ── THE INCLUSION RULE, STATED ONCE, DEFENDED HERE ─────────────────────────
// Record exactly two things a failed request can be. Never a row per request —
// see the migration header for why that would flood ops.run specifically, and
// the same "signal, not traffic" reasoning applies here to ops.incident too:
// a hot loop of undifferentiated rows is what 0116's signature/dedup exists to
// prevent, and this recorder leans on that mechanism rather than writing
// around it.
//
//   1. httpFailureClass(status) — any HTTP response the Worker itself returns
//      with status >= 500. This covers /health, /ingest and /pipeline/changes'
//      explicit 503s (database unreachable) and the top-level 500 correlation.js's
//      own wrapWithCorrelation produces for an uncaught throw anywhere in the
//      stack. These are unambiguous: the Worker is telling a caller it failed.
//
//   2. rpcInternalErrorFailureClass(code, context) — the /mcp JSON-RPC transport's own
//      "internal error" envelope, code -32603. THIS ROUTE NEEDS ITS OWN RULE
//      because it deliberately returns HTTP 200 for a JSON-RPC-level error (the
//      MCP spec's isError-in-body convention — see mcp.js's `reply`/`rpcError`,
//      neither of which ever passes a non-200 status). An uncaught exception
//      inside a tool call, a raw database error tools.js's pgConstraintError()
//      does not recognise, or any other unhandled throw during dispatch() all
//      surface as this ONE code, on this ONE route, at HTTP 200 — treating 200
//      as "nothing to record" would hide every one of them, which is exactly
//      the "unhandled throw / database error / verb fails mid-write" class the
//      brief names. Every OTHER JSON-RPC error code is a ToolError — a refusal
//      the system got right (not_in_profile, missing_idempotency_key,
//      version_conflict, key_reuse, actor_not_provisioned, invalid_field_value,
//      …) — and recording those would flood the ledger with the system doing
//      its job, not with the system malfunctioning. -32603 is the one code
//      considered for this route: dispatch() passes the verb and thrown error
//      so a narrow, verb-specific allowlist can exclude known raw
//      policy/admission raises while preserving unexpected exceptions and
//      runtime/database faults as incidents.
//
//   3. actorUnresolvedFailureClass — mcpApiHandler's and protectedApiHandler's
//      own `if (!actor) return json({error:"unauthorized"},401)` (index.js): a
//      request whose OAuth grant the provider ALREADY validated, decrypted,
//      and handed to our code as ctx.props — but whose props do not map to a
//      known actor. That is a real identity/config problem (a stale grant, an
//      actor row missing, code drift in identity.js), not a caller mistake.
//
// EXPLICITLY EXCLUDED, ON PURPOSE: any 401/403 the OAuthProvider library itself
// returns before either of the two routes above ever runs — "a routine 401 on
// an unauthenticated probe is noise" (the brief's own words, and the exact
// case the outage drill's own unauthenticated call produced). This code
// structurally cannot see WHY the library refused a request — no token, wrong
// token, and an expired grant are all indistinguishable from here — so nothing
// this file could record about them would be signal rather than a guess
// dressed as one. Also excluded: every ToolError (ordinary business-logic
// refusals returned at HTTP 200 with isError:true) and actor_not_provisioned
// specifically (a well-handled, well-messaged refusal with its own hint — not
// an unhandled anything).

import { neon } from "@neondatabase/serverless";
import { logLine } from "./correlation.js";

const SERVICE_KEY = "carr-mcp";
const DEFAULT_SEVERITY = "SEV-3"; // contained: one request failed, not the whole service —
// see the migration header for why this is deliberately NOT
// assess()'s SEVERITY_BY_CRITICALITY (that mapping answers
// "is the whole service down"; this answers "one request failed").
const MONITORING_HOURS = 24; // same window assess() uses; keeps a human's read of
// "how stale can this get before it's ignored" consistent
// regardless of which collector opened the incident.
const VALID_ENVIRONMENTS = new Set(["local", "rehearsal", "staging", "production"]);

export const RPC_INTERNAL_ERROR_CODE = -32603;

// These are durable policy/admission refusals from stored procedures. They are
// expected outcomes of the verb's business guard, not failures of the Worker
// or database runtime. Keep this allowlist narrow and verb-specific: a matching
// phrase on another verb, a different database error, or an unexpected TypeError
// must remain an incident.
const EXPECTED_POLICY_REFUSALS = Object.freeze({
  "set-work-shape-disposition": [
    /^sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged or triaged-to-ready transitions$/,
    /^sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, or triaged-to-ready transitions$/,
  ],
  "activate-context-bundle": [
    /^context compilation tenant must match authenticated tenant context$/,
  ],
  "issue-execution-envelope": [
    /^execution envelope requires an active conformance-passed environment provider binding$/,
  ],
  "read-attempt-reliability": [
    /^attempt reliability is not visible to tenant$/,
  ],
  "approve-rule": [
    /^rule approval refused: exact enforcement is not installed; missing\s+\S/,
    /^exact registered controls must be implemented before approval$/,
  ],
});

/** Pure. A stored-procedure refusal that the named verb is expected to surface. */
export function isExpectedPolicyRefusal(verb, error) {
  const message = String(error?.message || "");
  return (EXPECTED_POLICY_REFUSALS[verb] || []).some((pattern) => pattern.test(message));
}

/** Pure. status >= 500 is unambiguous Worker-reported failure; everything else
 * (including every 4xx) is out of scope for THIS classifier — see the actor-
 * unresolved 401 case, which is deliberately its own explicit call site rather
 * than folded in here, because this function has no way to know WHY a 401
 * happened and the brief is explicit that most 401s are noise. */
export function httpFailureClass(status) {
  return typeof status === "number" && status >= 500 ? "http_5xx" : null;
}

/** Pure. See the file header for why -32603 is the only JSON-RPC error code
 * considered here; known expected policy refusals are excluded. */
export function rpcInternalErrorFailureClass(code, { verb, error } = {}) {
  if (code !== RPC_INTERNAL_ERROR_CODE) return null;
  return isExpectedPolicyRefusal(verb, error) ? null : "verb_internal_error";
}

/** Pure. A provider-validated grant with no resolvable actor — see the file
 * header's item 3. No input: this failure class has exactly one cause. */
export function actorUnresolvedFailureClass() {
  return "actor_unresolved";
}

/** Pure. Same shape tools/ops-record.py's assess() already uses —
 * service|environment|run_key|failure_class — so a live-request incident and a
 * batch job/check incident interleave under one dedup rule (0116's partial
 * unique index) rather than two incompatible ones. routeKey plays run_key's
 * role: the path for a generic failure, or `mcp:<verb>` where the verb is
 * known (a more specific signature than the bare route gives).
 *
 * THE FOURTH FIELD IS NOT NORMALIZED HERE, AND DOES NOT NEED TO BE (0293).
 * ops-record.py rewrites the bare `exit_<n>` shape before it lands in this
 * column, because bin/nightly.sh passes wrapper exit codes through and exit_1
 * and exit_2 from one step are one problem. Every class this file can produce
 * — http_5xx, verb_internal_error, actor_unresolved — is already a name, so
 * the two writers agree by construction rather than by a rule restated in a
 * third language. That agreement holds only while that stays true, which is
 * why trace.test.mjs asserts it directly. A new class here shaped like
 * `exit_<n>` would silently fingerprint differently from the same failure
 * recorded by a job. */
export function incidentSignature({ serviceKey, environment, routeKey, failureClass }) {
  return `${serviceKey}|${environment}|${routeKey}|${failureClass}`;
}

/** Pure. One redacted line: route, outcome, correlation id — never a payload,
 * an argument value, or client content. Callers pass an already-truncated
 * `detail` (the same short string, if any, the response already carries — this
 * never adds a NEW leak surface beyond what the caller already sent back). */
export function incidentFactText({ routeKey, failureClass, correlationId, detail }) {
  const base = `${routeKey} failed (${failureClass}), correlation ${correlationId}`;
  return detail ? `${base} — ${String(detail).slice(0, 200)}` : base;
}

// ── the recorder ─────────────────────────────────────────────────────────────
//
// NEVER THROWS. Every branch below is inside one outer try/catch that swallows
// everything — a recording failure must never become a request failure, and
// this is called exclusively via ctx.waitUntil, after the response the caller
// is waiting on has already been decided. Not transactional: each statement is
// its own round trip against the HTTP-based neon() driver, and correctness
// under a concurrent duplicate is enforced by 0116's own partial unique index
// (a 23505 on it is caught below and treated as "another writer already opened
// this incident", not as an error) rather than by an application-level lock.
//
// A genuinely NEW occurrence may invalidate a recovery watch: monitoring goes
// back to detected and its recovery evidence/window are cleared.  The exact
// correlation source_ref is the idempotency boundary, so a replay does none of
// that.  This writer NEVER writes resolved_at or root_cause and can only move
// state toward detected, never toward resolved/reviewed. carr_writer has broad
// table UPDATE, so these structural limits and their tests are the boundary;
// closing an incident remains a human's call, always.
export async function recordWorkerFailure(query, {
  environment, routeKey, failureClass, correlationId, detail,
  severity = DEFAULT_SEVERITY, serviceKey = SERVICE_KEY,
}) {
  try {
    if (!VALID_ENVIRONMENTS.has(environment)) return; // an unlabelled deploy is never guessed at
    if (!failureClass || !routeKey || !correlationId) return;

    const svc = await query("select id from ops.service where key=$1 and retired_at is null", [serviceKey]);
    if (!svc.rows.length) return; // an unregistered service is never invented — see ops-record.py's own posture

    const signature = incidentSignature({ serviceKey, environment, routeKey, failureClass });
    const factSourceRef = `correlation:${correlationId}`;
    const factText = incidentFactText({ routeKey, failureClass, correlationId, detail });

    let incidentId = await findOpenIncident(query, signature);

    if (!incidentId) {
      incidentId = await openIncident(query, {
        environment, routeKey, failureClass, severity, signature, correlationId, serviceKey,
      });
    }
    if (!incidentId) return; // opening lost a race and the re-check (inside openIncident) still found nothing

    await appendFactIfNew(query, incidentId, factSourceRef, factText);
  } catch (e) {
    // SWALLOWED, NEVER SILENT (2026-08-14, defect cae5be2e, second finding).
    // The first build of this file swallowed with NO log line at all, and that
    // is exactly what let a real bug (see wrapNeonRows below) run in production
    // for hours with zero trace anywhere: every single call reached this catch,
    // and nothing said so. A recording failure must never change the response
    // the caller is waiting on — that discipline stays — but "never surfaces to
    // the caller" is not the same promise as "never observable at all". One
    // structured line, correlation id + error name/message only — no query
    // text, no params, no client content — so the NEXT failure is diagnosable
    // from `wrangler tail` instead of requiring a live probe plus a database
    // archaeology pass to find, the way this one did.
    logLine("error", "worker_failure_record_error", {
      correlation_id: correlationId || null,
      route_key: routeKey || null,
      failure_class: failureClass || null,
      error_name: (e && e.name) || typeof e,
      error_message: String((e && e.message) || e).slice(0, 300),
    });
  }
}

async function findOpenIncident(query, signature) {
  const r = await query(
    "select id from ops.incident where signature=$1 and state not in ('resolved','reviewed') limit 1",
    [signature]);
  return r.rows[0]?.id || null;
}

async function nextIncidentRef(query) {
  // SAME QUERY SHAPE tools/ops-record.py's _next_incident_ref uses, so refs
  // from both writers share one numbering space per day rather than two
  // sequences that could collide (rule a8c55a47: the same job, done from two
  // runtimes, stays one piece of logic — here expressed as one shared query
  // shape rather than one shared process, since a Cloudflare Worker cannot
  // invoke Python).
  //
  // BOTH halves come from the query, and this writer formats no date of its
  // own. The old shape counted against to_char(now(), ...) — the SERVER's
  // timezone — and then stamped the ref from `new Date()`, the CLIENT's. A
  // Worker is always UTC and Neon is always UTC, so the two coincided in
  // production by luck; on any non-UTC server they diverge for the length of
  // the offset, every incident in that window is numbered 01, and the second
  // one dies on incident_ref_key. Keeping the split here while ops-record.py
  // pins UTC would be worse than the original defect: the two writers would
  // count in one numbering space and label in two.
  const r = await query(
    `select to_char(now() at time zone 'UTC', 'YYYYMMDD') as day,
            coalesce(max(substring(ref from '[0-9]+$')::int), 0) + 1 as seq
       from ops.incident
      where ref like 'INC-'
                     || to_char(now() at time zone 'UTC', 'YYYYMMDD')
                     || '-%'`,
    []);
  const row = r.rows[0];
  // An aggregate select always returns exactly one row, so a missing day means
  // the query did not run as written. Fail loudly rather than falling back to
  // a client-side date, which is the very split this function just removed.
  if (!row?.day) throw new Error("incident numbering: day/sequence query returned no row");
  return `INC-${row.day}-${String(row.seq || 1).padStart(2, "0")}`;
}

async function openIncident(query, { environment, routeKey, failureClass, severity, signature, correlationId, serviceKey }) {
  const ref = await nextIncidentRef(query);
  const title = `${routeKey} failed on ${serviceKey} (${environment})`;
  try {
    const r = await query(
      `insert into ops.incident
           (ref, correlation_id, title, severity, state, environment, owner_actor,
            next_action, detected_source, detected_at, source_kind, source_ref,
            signature, observed_at, expires_at)
         values ($1,$2,$3,$4,'detected',$5,'joe',
                 $6,'carr-mcp-worker', now(), 'collector','mcp-server/src/trace.js',
                 $7, now(), now() + interval '${MONITORING_HOURS} hours')
         returning id`,
      [ref, correlationId, title, severity, environment,
       `read the trace: tools/ops-record.py trace ${correlationId}`, signature]);
    return r.rows[0]?.id || null;
  } catch (e) {
    // 0116's partial unique index refused a second OPEN incident on this
    // signature — another concurrent failure beat this one to it. That is
    // success, not an error: fall back to finding the incident it just opened.
    if (e && e.code === "23505") return findOpenIncident(query, signature);
    throw e;
  }
}

async function appendFactIfNew(query, incidentId, sourceRef, text) {
  const dup = await query(
    "select 1 from ops.incident_fact where incident_id=$1 and source_ref=$2 limit 1",
    [incidentId, sourceRef]);
  if (dup.rows.length) return false; // replay: never grow the list or invalidate recovery twice
  await query(
    "insert into ops.incident_fact (incident_id, text, source_ref) values ($1,$2,$3)",
    [incidentId, text, sourceRef]);
  // The old values drive every CASE expression in PostgreSQL's one UPDATE, so
  // a monitoring row loses both recovery markers in the same transition that
  // returns it to detected. A row already under investigation keeps its state.
  await query(
    `update ops.incident set observed_at = now(),
            expires_at = now() + interval '${MONITORING_HOURS} hours',
            state = case when state = 'monitoring' then 'detected' else state end,
            recovery_evidence_ref = case when state = 'monitoring'
                                       then null else recovery_evidence_ref end,
            monitoring_until = case when state = 'monitoring'
                                    then null else monitoring_until end,
            next_action = case when state = 'monitoring'
              then 'failed again during its watch — read the newest correlation fact'
              else next_action end
       where id = $1`,
    [incidentId]);
  return true;
}

// ── THE ROOT CAUSE OF THE FIRST DEPLOY (2026-08-14, defect cae5be2e, live
// diagnosis) ─────────────────────────────────────────────────────────────
// PR #148 shipped, migration 0122 applied clean, grants were correct
// (carr_writer already held select/insert/update on ops.incident/
// ops.incident_fact and select on ops.service — verified again below), and
// the recorder STILL never wrote a row. Root-caused live against production,
// not hypothesised:
//
//   1. Induced a real failure through the deployed Worker (probe token,
//      read-loop with a malformed uuid) — got back the exact -32603 envelope
//      this file's classifier targets, with a real x-correlation-id.
//   2. `tools/ops-record.py trace <that id>` answered "no trace". Direct read
//      of ops.incident on production: no new row at all, not even a failed
//      attempt visible anywhere — because nothing WAS visible anywhere; see
//      finding 2 below.
//   3. THE DECISIVE CHECK: tool_read_call — mcp.js's recordReadCall, a
//      DIFFERENT function, scheduled through the IDENTICAL ctx.waitUntil,
//      against the IDENTICAL env.DATABASE_URL_WRITER, via the IDENTICAL
//      neon() driver — recorded that EXACT probe request
//      (verb=read-loop, actor_slug=smoke-probe, ok=false,
//      error_kind=internal_error, at the same timestamp). That proves
//      ctx.waitUntil fires, DATABASE_URL_WRITER authenticates, and INSERT
//      succeeds through this exact pipeline — ruling out a missing grant
//      (candidate a) and an unreached waitUntil (candidate c) at once.
//   4. So the difference had to be THIS file's own code, not the wiring
//      around it. recordReadCall never reads anything off its insert's
//      result; every function in THIS file does (`svc.rows.length`,
//      `r.rows[0]?.id`, ...). @neondatabase/serverless's own documented
//      contract for `neon(dsn).query(text, params)`: "the query function
//      returns database rows directly" — e.g.
//      `await sql.query("SELECT ...", [...])  // -> [ { greeting: "..." } ]`,
//      a BARE ARRAY, never `{rows: [...]}` unless `fullResults: true` is
//      passed (it is not, here). scheduleFailureRecord's old `query` was
//      `(text, params) => neon(env.DATABASE_URL_WRITER).query(text, params)`
//      — the bare array, unwrapped. recordWorkerFailure's very FIRST query
//      (`select id from ops.service...`) returned that bare array, `svc.rows`
//      read `undefined`, `.length` threw a TypeError, and recordWorkerFailure's
//      own outer catch swallowed it — on every single call, with (before this
//      fix) no log line to say so either. THE SAME MISTAKE THIS FILE'S OWN
//      DESIGN NOTE WARNED ABOUT AVOIDING FOR TOOL_READ_CALL never touched this
//      new code, because it was never written through the one place that
//      already got the shape right: mcp.js's own read-branch client object,
//      `{ query: async (text, params) => ({ rows: await sql.query(text, params) }) }`.
//
// wrapNeonRows below is that same adapter, extracted and named so it is
// independently unit-testable — the unit test that WOULD have caught this
// (trace.test.mjs) mocked `query` returning `{rows:[...]}` directly, which
// is the shape THIS file's internals correctly expect, but is NOT the shape
// the real driver returns. The mock was internally consistent and wrong
// about the one boundary that mattered.
export function wrapNeonRows(sqlLike) {
  return async (text, params) => ({ rows: await sqlLike.query(text, params) });
}

/** Production wiring: builds the injected `query` from env.DATABASE_URL_WRITER
 * and schedules recordWorkerFailure via ctx.waitUntil so it never adds latency
 * to the response the caller is waiting on — the exact pattern mcp.js's
 * recordReadCall wiring already uses for tool_read_call. A no-op, not a throw,
 * when the writer credential is absent (mirrors that same precedent).
 *
 * neon(dsn) IS CALLED HERE, SYNCHRONOUSLY, AND MUST BE GUARDED SEPARATELY FROM
 * recordWorkerFailure's OWN try/catch — a bug this exact fix introduced and
 * its own tests caught before it shipped. neon() throws synchronously on a
 * malformed connection string (proven in this file's history: the -32603 test
 * below relies on the identical behavior for a DIFFERENT reason). Before this
 * guard, `wrapNeonRows(neon(env.DATABASE_URL_WRITER))` ran eagerly in THIS
 * function's own body — outside recordWorkerFailure's try/catch, since that
 * only wraps code that runs AFTER being handed to ctx.waitUntil — so a
 * malformed DATABASE_URL_WRITER would throw straight out of
 * scheduleFailureRecord and INTO withFailureRecording / dispatch() /
 * mcpApiHandler, exactly the "a recorder must never change a response"
 * failure this whole file exists to prevent. */
export function scheduleFailureRecord(env, ctx, { routeKey, failureClass, detail }) {
  if (!failureClass || !env || !env.DATABASE_URL_WRITER || !ctx || typeof ctx.waitUntil !== "function") return;
  let query;
  try {
    query = wrapNeonRows(neon(env.DATABASE_URL_WRITER));
  } catch (e) {
    logLine("error", "worker_failure_record_error", {
      correlation_id: env.CORRELATION_ID || null,
      route_key: routeKey || null,
      failure_class: failureClass || null,
      error_name: (e && e.name) || typeof e,
      error_message: String((e && e.message) || e).slice(0, 300),
    });
    return;
  }
  ctx.waitUntil(recordWorkerFailure(query, {
    environment: env.CARR_ENV || null,
    routeKey,
    failureClass,
    correlationId: env.CORRELATION_ID || null,
    detail,
  }));
}

function safeRouteKey(request) {
  try {
    return new URL(request.url).pathname || "/";
  } catch {
    return "/unparseable";
  }
}

/** The second wrapping layer, composed INSIDE wrapWithCorrelation in index.js:
 * `wrapWithCorrelation(withFailureRecording(routeRequest))`. Covers inclusion-
 * rule class 1 (httpFailureClass) two ways — a route that itself returns a
 * >=500 Response (health/ingest/pipeline's explicit 503s), and a handler that
 * throws, which is re-thrown UNCHANGED after scheduling the record so
 * wrapWithCorrelation's own catch still builds the exact 500 body it already
 * builds and every existing correlation.test.mjs assertion about that shape
 * keeps holding. Never awaits the recording itself — ctx.waitUntil schedules
 * it, the response (or throw) proceeds immediately. */
export function withFailureRecording(handler) {
  return async function tracedFetch(request, env, ctx) {
    let response;
    try {
      response = await handler(request, env, ctx);
    } catch (e) {
      scheduleFailureRecord(env, ctx, {
        routeKey: safeRouteKey(request),
        failureClass: httpFailureClass(500),
        detail: String((e && e.message) || e).slice(0, 200),
      });
      throw e;
    }
    const failureClass = response && httpFailureClass(response.status);
    if (failureClass) {
      scheduleFailureRecord(env, ctx, {
        routeKey: safeRouteKey(request),
        failureClass,
        detail: null, // the response body may carry detail; never re-read/duplicate it here
      });
    }
    return response;
  };
}
