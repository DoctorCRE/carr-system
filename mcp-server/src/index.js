import INGEST_TRANSPORT from "./ingest-transport.v1.json" with { type: "json" };

// CARR MCP server — Worker entrypoint.
//
// The Worker's fetch IS an OAuthProvider (Cloudflare's workers-oauth-provider).
// It is the OAuth 2.1 authorization server to the Claude apps, and it wraps the
// MCP verb surface at /mcp as its protected API route. Everything else — the
// dead-man probe, the ingest socket, and the Google sign-in leg — goes to the
// default handler and is NOT behind an access token.
//
//   /mcp        API route for humans: a provider-issued OAuth token, validated
//               by the provider, with the actor arriving as ctx.props. The
//               legacy PARTNER_TOKENS bearer that used to share this route was
//               retired 2026-08-03 (#111b).
//               A SECOND, much narrower door onto the same route was added
//               2026-08-06 (loop #192), for the smoke suite only: a
//               PROBE_TOKENS bearer, checked in THIS file BEFORE the request
//               ever reaches the OAuthProvider (see "probe token" below). It
//               maps to one locked-profile machine actor, never a human, and
//               its profile can never be widened by the request — the OAuth
//               path above is otherwise completely untouched.
//               A THIRD, equally narrow door was added the same day for the
//               Automatic Review Council's Codex lane (see "review token"
//               below): a REVIEW_TOKENS bearer, checked immediately after the
//               probe-token check and on the exact same before-the-provider
//               pattern. It maps a reviewer slug (e.g. 'codex-reviewer') to
//               the 'reviewer' capability profile locked in mcp.js: ALL reads
//               + EXACTLY record-finding. Nothing about the OAuth path or the
//               probe-token path changes because it exists.
//               A FOURTH door (see "local token" below), Phase 1 2026-08-13,
//               decision 97e76a2f: a LOCAL_TOKENS bearer for this Mac's own
//               `run.sh call` terminal (tools/call-verb.py ->
//               mcp-server/local-verb.mjs), which used to bypass this Worker
//               with a direct database connection. Resolves to the machine's
//               own actor: 'joe-local' sponsored to 'joe', or 'dell-local'
//               sponsored to 'dell'. Both are human:false — full verb parity
//               minus humanOnly, same as the agent-token door, never a wider
//               grant than that.
//   /pipeline/changes  OAuth-protected Deal Room event cursor + live presence.
//   /doc/mcp    Partner-only Doc brokerage tools, behind the same OAuth
//               provider. The resource path pins a closed capability profile;
//               request parameters cannot expand it. No machine-token door.
//   /authorize  Google sign-in starts (our code — see google-oidc.js)
//   /callback   Google returns; identity verified; allow-list applied; issue
//   /token      implemented by the provider
//   /register   implemented by the provider (RFC 7591 dynamic client registration)
//   /.well-known/oauth-authorization-server        provider (RFC 8414)
//   /.well-known/oauth-protected-resource[/path]   provider (RFC 9728)
//   /health     unchanged, unauthenticated dead-man probe
//   /release    unauthenticated, added Phase 1 2026-08-13 (deploy-provenance
//               gap): what code this deploy is running — Git SHA (stamped at
//               deploy time, see release.js), the schema_migrations ledger's
//               claim, doctrine generation, live verb count. No secret in
//               the response, same posture as /health.
//   /ingest     unchanged, INGEST_TOKENS bearer — deliberately OUTSIDE the OAuth wrap
//   /capture/*  capture rig bridge, CAPTURE_TOKENS claim then opaque session token
//
// NO SEND CAPABILITY EXISTS OR WILL EXIST IN THIS WORKER.
//
// CORRELATION (Program 3, Gap A, 2026-08-14). Every route above — every one —
// flows through the single default export at the bottom of this file, so that
// is where wrapWithCorrelation (correlation.js) wraps once: it resolves
// x-correlation-id (a caller's own valid UUID is echoed back verbatim; a
// missing or garbage one is replaced with a fresh one, never passed through),
// sets env.CORRELATION_ID for anything downstream to read, echoes the id back
// as x-correlation-id on every response, and turns an uncaught throw into a
// structured log line plus a 500 whose body carries the id — see
// correlation.js's own header for the full reasoning.
//
// TRACING THE FAILURE (Program 4, Gap A2, 2026-08-14, defect cae5be2e).
// correlation.js stays exactly what its own header says it is — no database
// driver, no ops.* write, Node-testable on its own. The database half lives in
// trace.js's withFailureRecording, composed HERE, one layer inside
// wrapWithCorrelation: `wrapWithCorrelation(withFailureRecording(routeRequest))`.
// That ordering matters — wrapWithCorrelation sets env.CORRELATION_ID BEFORE
// calling its handler argument, so by the time withFailureRecording runs, the
// id it needs is already there. It records exactly two of the three classes
// trace.js's header defines (any HTTP 5xx this Worker returns, and an
// uncaught throw before it becomes wrapWithCorrelation's own 500) by wrapping
// routeRequest's call and checking the response status; the third class
// (mcpApiHandler's/protectedApiHandler's own actor-unresolved 401) is recorded
// at its own call site below, since a 401 is invisible to a >=500 check.

import { OAuthProvider, getOAuthApi } from "@cloudflare/workers-oauth-provider";
import { neon, Pool } from "@neondatabase/serverless";
import { mcpApiHandler, dispatch, dispatchEngineeringController, canonicalOwnershipExecutionHost } from "./mcp.js";
import { engineeringControllerActorForToken } from "./authenticated-canonical-ownership.js";
import { handleAuthorize, handleCallback, handleConsent } from "./google-oidc.js";
export { OAuthConsentState } from "./oauth-consent-state.js";
import { sharedOAuthFetch, mcpOriginRefusal, isSharedOAuthPath } from "./oauth-policy.js";
import { agentActorForToken, authenticatedIdentity, continuityActorForTokenMaps,
         serveReviewRequest,
         hermesActorForTokenMaps, hermesCosActorForToken } from "./identity.js";
import { pipelineChanges } from "./dealroom.js";
import { authorizeProgram6Action, createDealroomHandler, isDealroomRequest, isLegacyDealroomRequest } from "./dealroom-web.js";
import { createProgram6RoutineController } from "./program6-routine-controller.js";
import { appendRoomTurn, DEFAULT_ROOM, OBSERVATORY_ROOM, readRoomLatest, readRoomQueue, readRoomTurns } from "./partner-room.js";
import { createCaptureHandler } from "./capture.js";
import { TOOLS } from "./tools.js";
import { buildRelease } from "./release.js";
import { readCommandCenterSummary } from "./workspace-command-center.js";
import { wrapWithCorrelation } from "./correlation.js";
import { withFailureRecording, scheduleFailureRecord, actorUnresolvedFailureClass } from "./trace.js";
import { createTourInternalWebHandler } from "./tour-internal-web.js";
import { createReportsWebHandler, isReportsHostRequest } from "./reports-web.js";
import { createReportsRuntimeAdapters, createTourRuntimeAdapters } from "./tour-runtime.js";

const JSON_HEADERS = { "content-type": "application/json" };
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

const ACCESS_TOKEN_TTL = 3600; // 1 hour (design: access tokens short)
const REFRESH_TOKEN_TTL = 7776000; // 90 days (design: refresh tokens long)

// ---------- health + ingest (unchanged behavior, unchanged auth) ----------

async function health(env) {
  try {
    const sql = neon(env.DATABASE_URL_READER);
    const rows = await sql`select line from v_integrity_digest`;
    return json({ ok: true, digest_lines: rows.length, ts: new Date().toISOString() });
  } catch (e) {
    const suspended = /suspend|quota|compute/i.test(String(e));
    return json(
      {
        ok: false,
        reason: suspended
          ? "Database compute is suspended (Neon budget). Not an emergency: boards render from last exports. Runbook: DNA/Deal Management/record-layer/runbook.md step 2."
          : "Database unreachable: " + String(e).slice(0, 200),
      },
      503,
    );
  }
}

// ---------- release (Phase 1, 2026-08-13 — closes the deploy-provenance gap)
//
// Unauthenticated, deliberately, same reasoning as /health: it exposes no
// secret, only the identity of the code that is running (a Git SHA, a
// migration filename, a generation number, a verb count). Anyone who can
// already read this repo can already see all of that; the value here is
// that a SESSION can ask the deployed Worker directly instead of trusting a
// local marker file that can go stale silently (mcp-server/.last-deployed-
// verb-count did exactly that on 2026-08-13 — see bin/deploy-worker.sh).
// See release.js for why the payload-building logic lives in its own file
// and how each field degrades honestly when it cannot be read.
async function release(env) {
  const sql = neon(env.DATABASE_URL_READER);
  const body = await buildRelease({ env, sql, verbCount: Object.keys(TOOLS).length });
  return json(body);
}

async function ingest(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  let tokens;
  try {
    tokens = JSON.parse(env.INGEST_TOKENS || "{}");
  } catch {
    tokens = {};
  }
  // INGEST_TOKENS_EXTRA (Phase 1, 2026-08-13): additive second map, merged over
  // the first. `wrangler secret put` REPLACES a secret wholesale and no secret
  // can be read back, so growing the original map means re-pasting every live
  // token — a silent-kill risk for any lane whose value isn't at hand. New
  // sources go in THIS map instead, one key at a time, zero risk to the old
  // ones. Same shape ({source: token}), same one-token-per-source rule.
  try {
    tokens = { ...tokens, ...JSON.parse(env.INGEST_TOKENS_EXTRA || "{}") };
  } catch {
    // a malformed extra map never takes down the primary lanes
  }
  const source = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!source) return json({ error: "unauthorized" }, 401);
  const len = parseInt(request.headers.get("content-length") || "0", 10);
  if (len > INGEST_TRANSPORT.max_body_bytes) return json({ error: "payload_too_large" }, 413);
  let payload;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const externalId = payload.external_id || request.headers.get("x-external-id") || null;
  try {
    const sql = neon(env.DATABASE_URL_WRITER);
    const rows = await sql`
      insert into ingest_inbox (source, external_id, payload)
      values (${source}, ${externalId}, ${JSON.stringify(payload)}::jsonb)
      on conflict (source, external_id) do update set status = ingest_inbox.status
      returning id, (xmax <> 0) as was_duplicate`;
    return json({ ok: true, id: rows[0].id, duplicate: rows[0].was_duplicate });
  } catch (e) {
    return json({ ok: false, error: String(e).slice(0, 200) }, 503);
  }
}

// ---------- default handler: everything that is not the protected API ----------

const defaultHandler = {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    // /healthz is deliberately NOT /health: it touches no secret, no env
    // binding and no database, so it answers even when the Worker's boot
    // itself is what is in question (a module-scope throw during import --
    // e.g. a Node-only API called at load time -- kills every route
    // including /health before any handler runs). CI's Worker-boot check
    // (bin/worker-boot-check.sh) asks this route specifically so a defect
    // like that fails CI instead of only surfacing on the next real deploy.
    if (url.pathname === "/healthz") return json({ ok: true });
    if (url.pathname === "/health") return health(env);
    if (url.pathname === "/release") return release(env);
    if (url.pathname === "/ingest" && request.method === "POST") return ingest(request, env);
    if (url.pathname === "/authorize") return handleAuthorize(request, env);
    if (url.pathname === "/callback") return handleCallback(request, env);
    if (url.pathname === "/consent") return handleConsent(request, env);
    return json({ service: "carr-mcp", surfaces: ["/healthz", "/health", "/release", "/ingest", "/mcp", "/pipeline/changes", "/authorize", "/callback"] }, 404);
  },
};

// Protected routes receive the same provider-verified ctx.props. The
// pipeline function itself accepts an actor and query client, so the Deal Room
// session-cookie gate mounts it without changing its contract.
async function pipelineApi(request, env, actor) {
  const sql = neon(env.DATABASE_URL_READER);
  const client = { query: async (text, params = []) => ({ rows: await sql.query(text, params) }) };
  try {
    return await pipelineChanges(request, client, actor);
  } catch (e) {
    // Surface the error class, not data: a blind 503 cost a live debugging
    // round on day one (the real fault was invisible from the client).
    return json({ error: "database_unavailable", detail: String(e && e.message || e).slice(0, 200) }, 503);
  }
}

function captureHandler(env) {
  return createCaptureHandler({
    withWriter: async (fn) => {
      const pool = new Pool({ connectionString: env.DATABASE_URL_WRITER });
      const client = await pool.connect();
      try {
        await client.query("begin");
        const result = await fn(client);
        await client.query("commit");
        return result;
      } catch (error) {
        await client.query("rollback").catch(() => {});
        throw error;
      } finally {
        client.release();
        await pool.end();
      }
    },
  });
}

const protectedApiHandler = {
  async fetch(request, env, ctx) {
    const pathname = new URL(request.url).pathname;
    if (pathname === "/mcp" || pathname === "/doc/mcp") return mcpApiHandler.fetch(request, env, ctx);
    if (pathname !== "/pipeline/changes") return json({ error: "not_found" }, 404);
    // THE GRANT DOOR, WITH THE SERVER'S WITNESS. `actorFromProps` is no longer
    // exported (amendment 8, fourth correction round): a grant's props are an
    // ordinary object, so an exported builder was a brander taking caller bytes.
    // The witness is the OAuth client secret this same Worker already holds;
    // identity.js brands only when the bytes match what it read from the
    // server's environment at initialisation, and returns the same actor either
    // way, so a missing secret costs a receipt identity and never a session.
    const actor = authenticatedIdentity.connectionForGrant(
      ctx.props, env.CARR_NATIVE_AGENT_OAUTH_CLIENTS, env.GOOGLE_CLIENT_SECRET);
    if (!actor) {
      // Same reasoning as mcpApiHandler's identical check (mcp.js) — a
      // provider-validated grant with no resolvable actor, not a routine
      // unauthenticated 401. See trace.js's file header, class 3.
      scheduleFailureRecord(env, ctx, {
        routeKey: "pipeline/changes:unauthorized",
        failureClass: actorUnresolvedFailureClass(),
        detail: null,
      });
      return json({ error: "unauthorized" }, 401);
    }
    return pipelineApi(request, env, actor);
  },
};

// ---------- probe token (loop #192, 2026-08-06) ----------
//
// A narrow, locked-profile machine identity for the smoke suite, re-credited
// after the PARTNER_TOKENS retirement (#111b) took the suite's old bearer with
// it. Modeled directly on the INGEST_TOKENS check in ingest() above: same
// shape (a JSON map of name -> secret, one Worker secret, replaced whole),
// same lookup style. The differences are deliberate and narrow the blast
// radius further than a second INGEST_TOKENS-style socket would:
//
//   - PROBE_TOKENS authenticates ONLY /mcp, and only a request whose bearer
//     matches. Checked in the wrapper below, BEFORE the request ever reaches
//     the OAuthProvider, so it is never confused with a provider-issued
//     grant, and the whole OAuth path is untouched for every other request —
//     including a /mcp request whose bearer does NOT match PROBE_TOKENS,
//     which falls straight through to oauthProvider.fetch exactly as before.
//   - The matched map key becomes the actor slug. Exactly one is expected to
//     exist — 'smoke-probe' — but the map shape leaves room for a second
//     narrow probe identity later without another code change.
//   - actor.probe = true is the ONLY thing that can put mcp.js's dispatch()
//     into the 'probe' capability profile (see the check there). ?profile=
//     is read for every other caller but is IGNORED here on purpose: the
//     whole point of this token is that it cannot be asked for more than it
//     was provisioned for, no matter what the request says.
//   - human: false, always. A probe actor is a machine and is never eligible
//     for a humanOnly verb (teach, retire-rule, confirm-merge, reassign-deal,
//     …), independent of whatever verbs the profile Set does or does not name.
//   - The actor must exist as a row in `actor` (kind='automation') before any
//     write verb will run — mcp.js's callTool() looks it up by slug inside the
//     write transaction and refuses actor_not_provisioned if it is missing,
//     exactly as it does for every other actor. See
//     pipelines/provision-smoke-probe.sql (prepared, not yet run).
//
// NOT a rebuild of the retired PARTNER_TOKENS bearer: that token authenticated
// as a full human actor (joe or dell) on the full profile. This token
// authenticates as its own machine actor, locked to three write verbs, and
// nothing about the humans' OAuth path changes because it exists.
function probeActorFor(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  if (!token) return null;
  let tokens;
  try {
    tokens = JSON.parse(env.PROBE_TOKENS || "{}");
  } catch {
    tokens = {};
  }
  const slug = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!slug) return null;
  return { slug, display: `Probe (${slug})`, human: false, probe: true, via: "probe-token", client_id: null };
}

// ---------- review token (Automatic Review Council, Codex lane, 2026-08-06) ----------
//
// A narrow, locked-profile machine identity for the Deal Council's automated
// Codex reviewer, built directly on the PROBE_TOKENS pattern above — same
// shape (a JSON map of slug -> secret, one Worker secret, replaced whole),
// same lookup style, same "checked before the OAuthProvider ever sees the
// request" placement. The differences mirror why PROBE_TOKENS exists as its
// own thing rather than widening INGEST_TOKENS or PARTNER_TOKENS:
//
//   - REVIEW_TOKENS authenticates ONLY /mcp, and only a request whose bearer
//     matches. A /mcp request whose bearer matches neither PROBE_TOKENS nor
//     REVIEW_TOKENS falls straight through to oauthProvider.fetch exactly as
//     before — this door adds nothing to that path.
//   - The matched map key becomes the actor slug. TWO are expected to hold a
//     live secret as of the 2026-08-06 scope extension: 'codex-reviewer'
//     (the original frozen scope) and 'grok-reviewer' (added the same day
//     once Grok's subscription-covered headless path — OAuth device flow,
//     no XAI_API_KEY — was live-verified; decision 65468572's Codex-only
//     cost override was superseded by that verification, not overridden by
//     assumption). Nothing in this file names either slug specially — this
//     code path is, and was designed to be, generic: adding a THIRD reviewer
//     later is a secret update and an actor row (pipelines/provision-review-
//     council.sql), never another code change here.
//   - actor.review = true is the ONLY thing that can put mcp.js's dispatch()
//     into the 'reviewer' capability profile (see the check there). ?profile=
//     is read for every other caller but is IGNORED here on purpose, exactly
//     like the probe path: the token cannot be asked for more than it was
//     provisioned for, no matter what the request says.
//   - human: false, always. A reviewer actor is a machine and is never
//     eligible for a humanOnly verb, independent of whatever the profile Set
//     does or does not name.
//   - The actor must exist as a row in `actor` (kind='automation') before
//     record-finding — the ONE write this profile allows — will run; mcp.js's
//     callTool() looks it up by slug inside the write transaction and refuses
//     actor_not_provisioned if it is missing, exactly as it does for every
//     other actor. See pipelines/provision-review-council.sql. PROVISIONED
//     AND ACTIVE as of 2026-08-06 (verified live by the #214 audit: both
//     reviewer actor rows exist, active=t) — the refusal backstop below is
//     therefore already open; the profile lock is the operative control.
//
// Checked AFTER probeActorFor so a token that happens to collide with both
// maps (it never will in practice — they are separate secrets) resolves
// deterministically to probe first; in practice a caller only ever holds one
// of the two tokens.
//
// THE MATCHING LOGIC MOVED TO identity.js ON 2026-09-12 (PR 1013) and THE DOOR
// ITSELF FOLLOWED IT on 2026-09-14 (amendment 9, fifth correction round). There
// is no `reviewActorFor(request)` here any more, because there is no exported
// function to delegate to: `reviewActorForToken` minted a branded actor and
// `dispatchFor` returned a callable that entered a context, and a probe that
// composed the two ran its own code as `review_agent`. Both are module-private
// inside identity.js now.
//
// WHAT THIS FILE CALLS INSTEAD is `serveReviewRequest`, in the /mcp route below:
// it matches the request's own Authorization header against the REVIEW_TOKENS
// map identity.js read from `process.env` at initialisation — which wrangler
// populates from this Worker's secrets (nodejs_compat, 2026-07-01 compatibility
// date) — and serves the request as what it matched, through the same dispatch
// this file uses for every other door. No bearer match answers null and the next
// door gets its turn.
//
// IT ESTABLISHES NO AUTHENTICATED CALL (sixth correction round, 2026-09-15). The
// previous shape passed this file's dispatch INTO identity.js as a callback so
// the whole request ran inside a receipt context, and a callback parameter on an
// exported door is an exported context entry — which is what amendment 8
// forbids. The context is now reached only by NAME, through
// `serveAuthenticatedCall`, over a frozen map of the server's own entries.
// ---------- hermes token (R0 runtime evaluation, 2026-08-16) ----------
//
// The fifth door, built on the PROBE_TOKENS/REVIEW_TOKENS pattern above and
// differing from them in exactly one way: its locked profile has an EMPTY
// write set. A Hermes runtime authenticated here reads everything and writes
// nothing, which is the 2026-08-12 frontier council's R0 disposition rendered
// as a server control rather than an instruction.
//
// WHY A FIFTH DOOR RATHER THAN REUSING AGENT_TOKENS. AGENT_TOKENS is the
// outside-model CLI door (codex, grok — loop #227/#239), and an actor arriving
// through it takes profileFor(request), so it can pass ?profile=capture and
// write. That is correct for a CLI a partner is driving turn by turn with a
// human reading every result. Hermes is not that: it is a persistent daemon
// with its own memory, scheduler and messaging channels, and its restraint is
// not a control. Widening AGENT_TOKENS to cover it would either hand Hermes
// the CLI's write path or take that path away from Codex and Grok. A separate
// secret, separate slug and separate lock keeps both true at once, and keeps
// `via` legible in tool_call rows.
//
// WHAT THIS DOOR IS NOT PROTECTING AGAINST. Not confidentiality. Joe closed
// that on 2026-08-12 ("the added exposure is minimal") and every frontier
// council seat, Grok included, has read entire CARR doctrines. The boundary is
// held credentials and autonomous authority (rule d7f74c93). The evidence it
// is warranted arrived the day Hermes was installed: within minutes it had
// seeded itself a Copilot credential off the gh CLI without being asked.
//
// The actor carries no sponsoring_human_slug, so identity.js resolves it to
// `unsponsored_agent` with shared-only scope and no personal brain — the R0
// fixture's cross-brain and unsponsored-metadata cases fall out of that
// existing rule rather than needing anything new here. humanOnly verbs refuse
// on human:false, as they do for every machine actor.
//
// Checked AFTER probeActorFor and reviewActorFor, matching their ordering
// reasoning: separate secrets never collide in practice, and a deterministic
// order is worth more than an equally arbitrary one.
// The lookup itself lives in identity.js (hermesActorForToken) for the same
// reason agentActorFor's does: this file imports from `cloudflare:` and cannot
// be loaded by node --test, so logic left in here is logic nothing can prove
// before a deploy. A boundary that can only be tested by deploying is not a
// boundary anyone should trust.
function hermesActorFor(request, env) {
  return hermesActorForTokenMaps(request.headers.get("authorization"),
    env.HERMES_TOKENS, env.HERMES_TOKENS_EXTRA);
}

// The CoS capability is a separate server-controlled secret map. Check it
// first so a credential explicitly provisioned for the bounded grant cannot
// fall through to the ordinary Hermes/projector profile.
function hermesCosActorFor(request, env) {
  return hermesCosActorForToken(request.headers.get("authorization"), env.HERMES_COS_TOKENS);
}

// ---------- agent tokens (outside-model CLIs at full scope, loop #227/#239) ----------
//
// WHY A THIRD DOOR. Codex reaches this Worker over OAuth and, since the loop
// #227 deploy, attributes correctly to its own actor (grant metadata reads
// "joe via codex"). Grok cannot: its CLI has no per-MCP-server login command,
// its headless path performs no dynamic client registration, and its TUI
// reports "not authenticated" against an OAuth server. Verified live
// 2026-08-09, three ways. There is no client_name to add to
// AGENT_CLIENT_NAMES because Grok never registers a client at all.
//
// So Grok authenticates the only way it can — `grok mcp add --header
// "Authorization: Bearer ..."` — and this map is the door that bearer opens.
//
// WHAT THIS IS NOT: it is NOT a revival of PARTNER_TOKENS, retired 2026-08-03.
// That secret authenticated as a full HUMAN actor (joe or dell), which meant it
// carried the humanOnly verbs — teach, retire-rule, confirm-merge, new-deal,
// reassign-deal — on a credential sitting in a config file. This one resolves
// to the tool's OWN actor with human:false, so mcp.js's humanOnly gate refuses
// every one of those by construction, not by a list someone has to maintain.
// That is exactly the parity decision 5dd21310 asked for: every verb EXCEPT
// humanOnly.
//
// WHY NO PROFILE LOCK, unlike probe and reviewer. Those two exist to pin a
// narrow surface (probe: reads; reviewer: reads + record-finding) and they set
// actor.probe / actor.review precisely so ?profile= cannot widen them. An agent
// token is the opposite case: full parity IS the grant, so it takes
// profileFor(request) like any other caller. A session that wants to run itself
// narrower can still pass ?profile=capture; it can never run wider, because
// human:false is set here and nothing on the wire can change it.
//
// The slug must be a known actor (identity.js DISPLAY) AND must exist as an
// `actor` row before any write runs — mcp.js's callTool() refuses
// actor_not_provisioned otherwise, same as every other path. codex and grok
// were provisioned by migration 0074_outside_model_actors.
//
// Checked AFTER probe and reviewer so a token colliding across maps resolves
// to the narrowest door first. In practice a caller holds exactly one.
// The lookup itself lives in identity.js (agentActorForToken) so node --test can
// prove it: this file imports from `cloudflare:` and cannot be loaded outside the
// Worker runtime, so anything implemented here is unprovable before a deploy.
function agentActorFor(request, env) {
  return agentActorForToken(request.headers.get("authorization"), env.AGENT_TOKENS);
}

// A separate secret map and a separate dispatcher are intentional: this actor
// cannot reach the ordinary MCP profile, where every non-full profile retains
// read verbs.  Only the exact four ownership operations below are listable or
// callable, and their plan/executor authority is re-read in mcp.js.
function engineeringControllerActorFor(request, env) {
  return engineeringControllerActorForToken(request.headers.get("authorization"),
    env.ENGINEERING_CONTROLLER_TOKENS, agentActorForToken);
}

// ---------- local token (Phase 1, 2026-08-13, decision 97e76a2f) ----------
//
// THE DEFECT THIS CLOSES. `run.sh call <verb>` (tools/call-verb.py ->
// mcp-server/local-verb.mjs) used to open its OWN direct connection to the
// production database and run a verb's handler code locally — bypassing this
// Worker entirely, and with it the identity derivation above, the profile
// gate in mcp.js, and the read-call instrumentation (0108's tool_read_call).
// Proven live 2026-08-13: a standing-context call through that path left no
// tool_read_call row; the same verb through this door does. local-verb.mjs is
// now a thin HTTPS client of THIS Worker's /mcp endpoint, carrying the bearer
// below — every verb it calls runs through dispatch() exactly like any other
// caller on this page, with one exception (see NOT A REBUILT PARTNER_TOKENS).
//
// WHY A FOURTH DOOR RATHER THAN REUSING AGENT_TOKENS. `wrangler secret put`
// REPLACES a secret wholesale and no secret can be read back — widening
// AGENT_TOKENS for this would mean re-pasting Codex's and Grok's live tokens
// from memory, a silent-kill risk for both on a typo (secrets-inventory.md's
// INGEST_TOKENS_EXTRA note is the same lesson, hit once already). LOCAL_TOKENS
// is its own Worker secret, additive and structurally identical to
// PROBE_TOKENS / REVIEW_TOKENS / AGENT_TOKENS: a JSON map of slug -> secret,
// checked here before the OAuthProvider ever sees the request.
//
// THE ACTORS: 'joe-local' and 'dell-local', both human:false. Each machine
// holds only its own key. Unlike the outside-model CLIs above, each carries a
// server-derived sponsor: identity.js's LOCAL_SPONSOR map ties joe-local to
// Joe and dell-local to Dell, so personal-scope reads and writes land with the
// partner who owns that terminal. That resolution is entirely identity.js's
// job (agentActorForToken, extended rather than duplicated — see its own
// comment); nothing about it lives in this file.
//
// EVERY VERB EXCEPT partner-authority FALLS OUT OF EXISTING MECHANISMS, ON PURPOSE.
// No profile lock here (unlike probe/reviewer): full parity minus partner authority
// IS the grant, exactly like the agent-token door above, because joe-local and
// dell-local are not approved native Codex/Claude runtime identities and therefore refuse
// teach / retire-rule / confirm-merge / reassign-deal / new-deal / … by
// construction — never a list this file has to maintain. A credential sitting
// in a 600 file on a Mac must not be able to teach a rule that binds both
// partners, retire one, confirm a merge, or reassign a deal; if that is ever
// truly needed with no interactive session available, it is a deliberate,
// receipted break-glass act (mcp-server/local-verb.mjs), never this door.
//
// NOT A REBUILT PARTNER_TOKENS (retired 2026-08-03, see the note above
// agentActorForToken in identity.js): that secret authenticated as a full
// HUMAN actor, which is exactly what carried the humanOnly verbs on a
// credential in a config file. This one resolves to human:false with a
// server-derived sponsor, which is a different, narrower shape — sponsored
// but never eligible for partner authority merely because a sponsor exists.
//
// The machine's actor row (kind='automation') must exist before any write verb
// runs, exactly like every other machine actor above. Joe's runbook is
// pipelines/provision-local-client.sql; Dell's is
// pipelines/provision-dell-local-client.sql. The steps that touch either
// secret remain human-only, same division of labor as PROBE_TOKENS.
//
// Checked last among the token doors: probe and reviewer are the narrowest
// (a locked profile), agent and local are both full-parity-minus-humanOnly,
// and a token colliding across maps resolves deterministically to whichever
// is checked first — in practice a caller holds exactly one.
function localActorFor(request, env) {
  const actor = agentActorForToken(
    request.headers.get("authorization"), env.LOCAL_TOKENS, "local-token");
  // Compatibility exists only for the staged production cutover. Deploy the
  // Worker in compat, provision both isolated maps, rotate the Codex hook to
  // its new client profile, then promote this server-side flag to required.
  // In required mode the shared local token carries no continuity surface and
  // both continuity families refuse it.
  // DECORATED IN PLACE, never copied: identity.js's brand is object identity
  // (amendment 8), so `{ ...actor }` here would hand the dispatch an actor that
  // authenticates as nobody. The object is built per request by the door above.
  return actor && env.CONTINUITY_SURFACE_ENFORCEMENT === "compat"
    ? Object.assign(actor, { continuity_surface: "codex", continuity_surface_compat: true })
    : actor;
}

function continuityActorFor(request, env) {
  return continuityActorForTokenMaps(
    request.headers.get("authorization"),
    env.CODEX_CONTINUITY_TOKENS,
    env.CLAUDE_CONTINUITY_TOKENS,
  );
}

// ---------- the provider ----------

const oauthOptions = {
  apiRoute: ["/mcp", "/doc/mcp", "/pipeline/changes"],
  apiHandler: protectedApiHandler,
  defaultHandler,

  authorizeEndpoint: "/authorize",
  tokenEndpoint: "/token",
  clientRegistrationEndpoint: "/register",

  accessTokenTTL: ACCESS_TOKEN_TTL,
  refreshTokenTTL: REFRESH_TOKEN_TTL,

  // The 2026-07-28 MCP spec revision prefers Client ID Metadata Documents over
  // dynamic client registration; both are on, so clients on either side of that
  // migration work. CIMD needs the global_fetch_strictly_public compatibility
  // flag (set in wrangler.toml) for SSRF protection.
  clientIdMetadataDocumentEnabled: true,

  // PKCE hardening (2026-08-06, un-gated the night Dell's phone was confirmed).
  // The library default ALLOWED plain PKCE (allowPlainPKCE !== false), and live
  // discovery advertised ["plain","S256"]. Held from 2026-08-03 until both
  // partners' phones were verified on the connector, because the check runs at
  // /authorize — tightening breaks nothing live, only the NEXT reconnect of a
  // client that uses plain. Both phones confirmed (Joe 2026-07-31 test 2,
  // Dell 2026-08-06 per Joe): S256 only from here.
  allowPlainPKCE: false,

  // The MIGRATION-ONLY resolveExternalToken option lived here until 2026-08-03.
  // It let a legacy PARTNER_TOKENS bearer authenticate while the OAuth connectors
  // were being rolled out, and it was retired on exactly the terms it was written
  // under. /mcp now accepts a provider-issued token OR the narrow probe-token
  // path below, and nothing else.

  onError({ code, description, status }) {
    console.warn(`OAuth error response: ${status} ${code} - ${description}`);
  },
};
const oauthProvider = new OAuthProvider(oauthOptions);

// Same verb and cursor implementations as the bearer-token surface; only the
// authentication adapter differs. The cookie session already resolved to the
// exact actor shape dispatch() and pipelineChanges() accept.
const program6RoutineController = createProgram6RoutineController({ authorizeAction: authorizeProgram6Action });
const tourInternalHandler = createTourInternalWebHandler(createTourRuntimeAdapters());
const reportsHandler = createReportsWebHandler(createReportsRuntimeAdapters());
const dealroomHandler = createDealroomHandler({
  mcpHandler: (request, env, ctx, actor) => dispatch(request, env, ctx, actor),
  pipelineHandler: (request, env, _ctx, actor) => pipelineApi(request, env, actor),
  program6Handler: (request, env, ctx, actor, session) =>
    program6RoutineController.fetch(request, env, ctx, actor, session),
  tourHandler: tourInternalHandler,
  commandCenterReader: async (env, actor, correlationId) => {
    const sql = neon(env.DATABASE_URL_READER);
    const client = { query: async (text, params = []) => ({ rows: await sql.query(text, params) }) };
    return readCommandCenterSummary({ client, actor, correlationId: correlationId || env.CORRELATION_ID });
  },
  // The Model Room observatory's doors onto the room wire. Turns are read and
  // posted in OBSERVATORY_ROOM, the room the local bridge polls, so a browser
  // post reaches the queue; the queue projection is read from DEFAULT_ROOM where
  // Hermes writes it. All call the SAME functions the read-room / add-room-turn
  // verbs call (partner-room.js) —
  // these two adapters supply a connection and nothing else, so the panel can
  // never read a different wire than a desk does.
  roomReadFn: (env, params) => {
    const sql = neon(env.DATABASE_URL_READER);
    const client = { query: async (text, values = []) => ({ rows: await sql.query(text, values) }) };
    return readRoomTurns(client, { room: OBSERVATORY_ROOM, ...params });
  },
  roomLatestFn: (env, params) => {
    const sql = neon(env.DATABASE_URL_READER);
    const client = { query: async (text, values = []) => ({ rows: await sql.query(text, values) }) };
    return readRoomLatest(client, { room: OBSERVATORY_ROOM, ...params });
  },
  queueReadFn: (env, params) => {
    const sql = neon(env.DATABASE_URL_READER);
    const client = { query: async (text, values = []) => ({ rows: await sql.query(text, values) }) };
    return readRoomQueue(client, { room: DEFAULT_ROOM, ...params });
  },
  roomWriteFn: async (env, params) => {
    const pool = new Pool({ connectionString: env.DATABASE_URL_WRITER });
    const client = await pool.connect();
    try {
      return await appendRoomTurn(client, { room: OBSERVATORY_ROOM, ...params });
    } finally {
      client.release();
      await pool.end();
    }
  },
});

// The staging workers.dev origin carries both the browser and provider API.
// Narrow machine-token MCP doors therefore run before the Deal Room predicate;
// browser-cookie MCP/pipeline and exact browser assets run next; everything
// else (/release, /health, OAuth endpoints and provider bearer calls) remains
// owned by OAuthProvider/defaultHandler.
async function routeRequest(request, env, ctx) {
  const url = new URL(request.url);
  // Validate before machine-token, browser-cookie and provider routes alike.
  if (isSharedOAuthPath(url.pathname)) {
    const refused = mcpOriginRefusal(request, env);
    if (refused) return refused;
  }
  // The confidential reports host is an isolated leaf. Unknown paths close as
  // report-surface 404s and can never alias MCP, OAuth, capture, or Deal Room.
  if (isReportsHostRequest(request)) return reportsHandler.fetch(request, env, ctx);
  // The old Deal Room hostname is a browser bookmark bridge only. Keep it
  // ahead of machine-token doors so /mcp and other API paths fail closed on
  // that hostname instead of reaching an unrelated machine surface.
  if (isLegacyDealroomRequest(request, env)) return dealroomHandler.fetch(request, env, ctx);
  if (url.pathname.startsWith("/capture/")) {
    return captureHandler(env).fetch(request, env, ctx);
  }
  if (url.pathname === "/mcp") {
    const controllerActor = engineeringControllerActorFor(request, env);
    if (controllerActor) {
      // This is deploy metadata, never an MCP parameter.  The controller skips
      // ordinary dispatch(), so attach the same server-derived host binding it
      // would otherwise receive there before the ownership context is minted.
      Object.assign(controllerActor, { execution_host_id: canonicalOwnershipExecutionHost(env) });
      return dispatchEngineeringController(request, env, ctx, controllerActor);
    }
    const probeActor = probeActorFor(request, env);
    if (probeActor) return dispatch(request, env, ctx, probeActor);
    // THE REVIEW COUNCIL'S DOOR. The bearer is matched inside identity.js and
    // the request is served by what it matched, with no callback crossing the
    // boundary in either direction and no authenticated call established. `null`
    // means this was not a review-council request at all.
    const reviewServed = await serveReviewRequest(request, env, ctx);
    if (reviewServed !== null) return reviewServed;
    const hermesCosActor = hermesCosActorFor(request, env);
    if (hermesCosActor) return dispatch(request, env, ctx, hermesCosActor);
    const hermesActor = hermesActorFor(request, env);
    if (hermesActor) return dispatch(request, env, ctx, hermesActor);
    const agentActor = agentActorFor(request, env);
    if (agentActor) return dispatch(request, env, ctx, agentActor);
    const continuityActor = continuityActorFor(request, env);
    if (continuityActor) return dispatch(request, env, ctx, continuityActor);
    const localActor = localActorFor(request, env);
    if (localActor) return dispatch(request, env, ctx, localActor);
  }
  if (isDealroomRequest(request, env)) return dealroomHandler.fetch(request, env, ctx);
  return sharedOAuthFetch(oauthProvider, request, {
    ...env, OAUTH_PROVIDER: getOAuthApi(oauthOptions, env),
  }, ctx);
}

// Top-level export. wrapWithCorrelation covers every route above in one
// place — see the CORRELATION note at the top of this file and
// correlation.js's own header. withFailureRecording is composed INSIDE it
// (see the TRACING THE FAILURE note at the top of this file for why that
// ordering is load-bearing) so every route's >=500 responses and uncaught
// throws are covered the same single-wrap way.
export default {
  fetch: wrapWithCorrelation(withFailureRecording(routeRequest)),
};
