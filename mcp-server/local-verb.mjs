// local-verb.mjs — run ONE registered verb from this Mac.
//
// PHASE 1, 2026-08-13, DECISION 97e76a2f — THE BYPASS THIS CLOSES. Until this
// change, EVERY call through this file opened its own direct connection to
// the production database and ran the verb's handler code locally — entirely
// bypassing the deployed Worker's identity derivation (mcp-server/src/
// identity.js), its profile gate (mcp.js's callTool), and its read-call
// instrumentation (migration 0108's tool_read_call). Proven live 2026-08-13:
// a standing-context call through this path left no tool_read_call row; the
// same verb through the real /mcp endpoint does.
//
// THE DEFAULT PATH NOW (no DATABASE_URL set — the ordinary case): a thin
// HTTPS client of the deployed Worker's /mcp endpoint, carrying a LOCAL_TOKENS
// bearer. Every verb call runs through mcp.js's dispatch() exactly like any
// other caller — the OAuth path, the probe/review/agent-token doors, and this
// one all converge on the same code. See mcp-server/src/index.js's "local
// token" comment and wrangler.toml's secrets header for the server side.
//
// THE ACTOR is machine-specific: 'joe-local' sponsored to Joe, or 'dell-local'
// sponsored to Dell (identity.js DISPLAY and LOCAL_SPONSOR). Both are
// human:false with full verb parity minus humanOnly verbs (teach, retire-rule,
// confirm-merge, reassign-deal, …), which refuse by construction wherever
// `tool.humanOnly && !actor.human` is checked. That is a FEATURE, not a gap: a
// credential sitting in a 600 file on a Mac must never be able to teach a rule
// that binds both partners, retire one, confirm a merge, or reassign a deal on
// its own say-so. A humanOnly verb attempted this way fails with a message
// naming the interactive OAuth route instead.
//
// BREAK-GLASS, NOT A FALLBACK (DATABASE_URL set): the direct-database path
// described above still exists, for the case a rehearsal branch needs proving
// before it ships, or the deployed Worker is genuinely unreachable — but it
// is now an explicit, receipted act, never a silent default. It requires
// CARR_BREAK_GLASS=1 AND a non-empty CARR_BREAK_GLASS_REASON, mirroring
// tools/db-tap.py's envelope exactly, including its receipts-log convention
// (out/break-glass-receipts.log, one shared audit trail for every break-glass
// act in the repo). tools/call-verb.py --reason "..." sets both of those and
// derives the DSN; a bare `DATABASE_URL=... node local-verb.mjs <verb> ...`
// invocation that skips call-verb.py is refused by the same check run here,
// not only by the wrapper, so there is no way to reach the direct-database
// path without a stated reason and a logged receipt. On a genuine Worker
// outage the DEFAULT path fails visibly (a network/HTTP error, printed
// plainly) and never silently reverts to this direct connection — reverting
// is a human's deliberate choice (set CARR_BREAK_GLASS=1, state why), not
// something this file decides on its own.
//
// Because break-glass still derives identity from ~/.config/carr/
// local-actor.json (bin/set-local-actor.sh) rather than from a server-issued
// token, it retains HUMAN semantics for whichever partner set up this Mac —
// so a humanOnly verb with no interactive session available is possible only
// through break-glass, deliberately, with a reason and a receipt. That is the
// intended shape, not an oversight: see THE ACTOR paragraph above for why the
// default path cannot do this at all.
//
//   node local-verb.mjs <verb> '<json args>'                         # default: HTTPS to the deployed Worker
//   DATABASE_URL=<url> CARR_BREAK_GLASS=1 CARR_BREAK_GLASS_REASON="..." \
//     node local-verb.mjs <verb> '<json args>'                       # break-glass: direct database
//
// In practice always invoked through tools/call-verb.py (`run.sh call`),
// which derives the DSN and sets the break-glass env only when its own
// --reason flag and CARR_BREAK_GLASS=1 are both present — see that file.
//
// IDENTITY, Phase 1 (2026-08-13, earlier same day). This used to take the
// actor as a THIRD ARGV SLUG, defaulting to "joe" when omitted — any local
// caller could claim any identity, and every historical receipt written
// through this path is unverifiable because of it. There is no argv slug
// anymore. On the default path the actor is server-derived from the
// LOCAL_TOKENS bearer (identity.js), never from anything this Mac asserts.
// Two bounded internal callers select credentials distinct from an ordinary
// local-machine bearer. The queue projector sets
// CARR_MCP_CLIENT_PROFILE=hermes-projector so this client reads the separate
// CARR_HERMES_MCP_TOKEN from the same 600-mode token file. That selector is a
// client purpose, not an actor claim: the Worker still derives hermes-pilot by
// matching the bearer against HERMES_TOKENS. No ordinary run.sh call sets it.
// The chief-of-staff client selects hermes-cos and reads only
// CARR_HERMES_COS_MCP_TOKEN; the Worker must match it against the wholly
// separate HERMES_COS_TOKENS map. Neither selector falls through to the other.
// On the break-glass path the actor is still derived from ONE place,
// ~/.config/carr/local-actor.json, written once per machine by
// bin/set-local-actor.sh — see resolveIdentity() below for why break-glass
// reads are not exempted either.
//
// THE HONEST LIMIT (stated again at set-local-actor.sh, and at
// tools/db-tap.py for the break-glass envelope): none of this stops a
// DELIBERATE local attacker — any process running as this Mac's own user can
// read the LOCAL_TOKENS bearer from the token file, or overwrite
// local-actor.json, exactly as this script does. That boundary is the
// server side's job (the actor table on write, the OAuth-issued token or the
// LOCAL_TOKENS bearer on the real MCP path) — this file only makes the
// ordinary, unintentional case impossible on the default path, and makes the
// deliberate direct-database case receipted and visible rather than silent.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { humanOnlyGuidance, isHumanOnlyError } from "./human-only-hint.mjs";
import {
  LOCAL_CLIENT_PROFILE_NAMES, selectLocalClientCredential, tokenFileSecurityIssue,
} from "./local-client-auth.mjs";

// ZERO-INSTALL DEFAULT PATH. The ordinary `run.sh call` route is only an HTTPS
// client of the deployed Worker. It must therefore boot from a clean checkout
// with no mcp-server/node_modules at all. Loading the database driver, ws, and
// the full local tool registry at module scope made that path die before its
// first fetch whenever the canonical runtime cache was absent or mid-refresh.
// Those packages and the registry are direct-database break-glass machinery,
// so load them only after DATABASE_URL has selected that explicitly receipted
// path. This also keeps a broken optional recovery runtime from taking the
// normal STORE front door down with it.
async function loadBreakGlassRuntime() {
  const [{ Pool, neonConfig }, { default: ws }, tools] = await Promise.all([
    import("@neondatabase/serverless"),
    import("ws"),
    import("./src/tools.js"),
  ]);
  // The `ws` package, NOT Node's built-in WebSocket: under Node 26 the native
  // constructor makes the driver die with an unhandled ErrorEvent before any
  // query runs (measured 2026-08-06, first real use of this harness).
  neonConfig.webSocketConstructor = ws;
  return {
    Pool,
    TOOLS: tools.TOOLS,
    ToolError: tools.ToolError,
    executeRegisteredTool: tools.executeRegisteredTool,
  };
}

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const cliArgs = process.argv.slice(2);
const [verb, rawArgs = "{}"] = cliArgs;
if (!verb) {
  console.error("usage: node local-verb.mjs <verb> '<json args>'  (default: HTTPS to the deployed Worker)");
  console.error("       DATABASE_URL=... CARR_BREAK_GLASS=1 CARR_BREAK_GLASS_REASON=\"...\" node local-verb.mjs <verb> '<json args>'  (break-glass)");
  process.exit(2);
}
// The old third argv (actor slug) is GONE, not silently ignored: a caller
// still passing one is almost always stale muscle memory or an unfixed
// wrapper, and swallowing it quietly would hide exactly the impersonation
// path a prior Phase 1 change closed. Fail loud and say why.
if (cliArgs.length > 2) {
  console.error(
    `local-verb.mjs no longer takes an actor-slug argv (got extra arg "${cliArgs[2]}"). ` +
    "The default path derives identity from the LOCAL_TOKENS bearer server-side; break-glass " +
    "derives it from ~/.config/carr/local-actor.json — see bin/set-local-actor.sh <slug> if that " +
    "file does not exist yet."
  );
  process.exit(2);
}
// '-' keeps multi-page record content off argv and avoids the OS argument cap.
const args = JSON.parse(rawArgs === "-" ? fs.readFileSync(0, "utf8") : rawArgs);

// ---------------------------------------------------------------------------
// DEFAULT PATH — HTTPS client of the deployed Worker.
// ---------------------------------------------------------------------------

const CARR_MCP_URL = process.env.CARR_MCP_URL || "https://api.doctorcre.com/mcp";
const MCP_TOKENS_ENV = process.env.CARR_MCP_ENV ||
  path.join(os.homedir(), ".config", "carr", "mcp-tokens.env");
async function runViaWorker(verbName, verbArgs) {
  let tokenFile = "";
  try {
    const stat = fs.lstatSync(MCP_TOKENS_ENV);
    const issue = tokenFileSecurityIssue({
      mode: stat.mode, uid: stat.uid, isFile: stat.isFile(),
      isSymbolicLink: stat.isSymbolicLink(),
    }, process.getuid?.());
    if (issue) {
      console.error(`refusing MCP token file ${MCP_TOKENS_ENV}: ${issue}`);
      process.exit(2);
    }
    tokenFile = fs.readFileSync(MCP_TOKENS_ENV, "utf8");
  } catch (e) {
    if (e?.code !== "ENOENT") {
      console.error(`could not securely read MCP token file ${MCP_TOKENS_ENV}: ${e.message}`);
      process.exit(2);
    }
  }
  let credential;
  try {
    credential = selectLocalClientCredential(process.env, tokenFile);
  } catch (e) {
    console.error(`${e.message}; supported profiles: ${LOCAL_CLIENT_PROFILE_NAMES.join(", ")}`);
    process.exit(2);
  }
  if (!credential.token) {
    console.error(
      `no MCP token for client profile ${credential.profile} — set ${credential.tokenVariable} ` +
      `in ${MCP_TOKENS_ENV} (600, outside the repo) or export ${credential.tokenVariable} directly. ` +
      `Local-machine provisioning: pipelines/provision-local-client.sql (Joe) or ` +
      `pipelines/provision-dell-local-client.sql (Dell). Hermes-projector provisioning uses the ` +
      `existing HERMES_TOKENS door. Hermes-cos uses its separately provisioned bearer and never ` +
      `reuses the projector token. Then update the matching Worker secret with ` +
      `\"wrangler secret put ${credential.workerSecret}\" ` +
      "— see mcp-server/wrangler.toml's secrets header.\n" +
      "This is the default path failing VISIBLY, per design — it does not fall back to a direct " +
      "database connection. That path exists only as explicit break-glass; see this file's header."
    );
    process.exit(2);
  }
  console.error(`local-verb identity -> ${credential.identityNotice} -> ${CARR_MCP_URL}`);

  let res;
  try {
    res = await fetch(CARR_MCP_URL, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${credential.token}` },
      body: JSON.stringify({
        jsonrpc: "2.0", id: 1, method: "tools/call",
        params: { name: verbName, arguments: verbArgs },
      }),
    });
  } catch (e) {
    console.error(
      `could not reach the deployed Worker at ${CARR_MCP_URL}: ${e.message}\n` +
      "The default path fails here rather than silently opening a direct database connection. " +
      "For a genuine outage, break glass explicitly — see this file's header for the envelope."
    );
    process.exit(1);
  }

  const bodyText = await res.text();
  let body;
  try {
    body = JSON.parse(bodyText);
  } catch {
    console.error(`non-JSON response (HTTP ${res.status}) from ${CARR_MCP_URL}: ${bodyText.slice(0, 500)}`);
    process.exit(1);
  }
  if (!res.ok && body?.error === undefined && body?.result === undefined) {
    console.error(`HTTP ${res.status} from ${CARR_MCP_URL}: ${JSON.stringify(body)}`);
    process.exit(1);
  }
  if (body.error) {
    // JSON-RPC transport-level error (unknown method, malformed request, …),
    // distinct from a ToolError, which arrives as result.isError below.
    console.error("RPC ERROR " + JSON.stringify(body.error, null, 2));
    process.exit(1);
  }
  const result = body.result || {};
  const text = result.content?.[0]?.text ?? "null";
  if (result.isError) {
    let payload;
    try { payload = JSON.parse(text); } catch { payload = { error: "unparseable_tool_error", raw: text }; }
    console.error("TOOL ERROR " + JSON.stringify(payload, null, 2));
    // The server's hint names the two paths but not the thing a human at this
    // prompt needs first: that being human here does not satisfy the gate.
    if (isHumanOnlyError(payload)) console.error(humanOnlyGuidance(verbName));
    process.exit(1);
  }
  let out;
  try { out = JSON.parse(text); } catch { out = text; }
  console.log(JSON.stringify(out, null, 2));
}

// ---------------------------------------------------------------------------
// BREAK-GLASS PATH — direct database connection, receipted.
// ---------------------------------------------------------------------------

const IDENTITY_PATH = path.join(os.homedir(), ".config", "carr", "local-actor.json");
const SETUP_COMMAND = `bin/set-local-actor.sh <slug>  (run from the repo root; use "joe" on this Mac)`;
const RECEIPT_LOG = path.join(REPO, "out", "break-glass-receipts.log");
const PRODUCTION_ENDPOINT = "ep-restless-resonance-awbp35k3"; // measured, see tools/db-tap.py's dsn()

function resolveIdentity() {
  // Reads AND writes both require this file on the break-glass path, same
  // discipline the old default-path code applied unconditionally: personal-
  // scope reads (personal_to, personal-scope filters) depend on WHICH human is
  // asking just as much as a write does, so a warn-and-guess read would leak
  // or hide rows for the wrong person instead of failing loudly.
  let raw;
  try {
    raw = fs.readFileSync(IDENTITY_PATH, "utf8");
  } catch (e) {
    if (e.code === "ENOENT") {
      console.error(
        `no local identity set — this Mac has never run set-local-actor.sh.\n` +
        `Run this once:\n\n    ${SETUP_COMMAND}\n`
      );
      process.exit(2);
    }
    console.error(`could not read ${IDENTITY_PATH}: ${e.message}`);
    process.exit(2);
  }
  let identity;
  try {
    identity = JSON.parse(raw);
  } catch (e) {
    console.error(`${IDENTITY_PATH} is not valid JSON (${e.message}). Re-run:\n\n    ${SETUP_COMMAND} --force\n`);
    process.exit(2);
  }
  const actorSlug = identity && identity.actor_slug;
  if (!actorSlug || typeof actorSlug !== "string") {
    console.error(`${IDENTITY_PATH} is missing "actor_slug". Re-run:\n\n    ${SETUP_COMMAND} --force\n`);
    process.exit(2);
  }
  return actorSlug;
}

function appendReceipt(actor, mode, target, host, reason) {
  fs.mkdirSync(path.dirname(RECEIPT_LOG), { recursive: true });
  const line = `${new Date().toISOString()} actor=${actor} mode=${mode} target=${target} ` +
    `host=${host} reason="${String(reason).trim()}"\n`;
  fs.appendFileSync(RECEIPT_LOG, line);
}

async function runBreakGlass(verbName, verbArgs, url) {
  const { Pool, TOOLS, ToolError, executeRegisteredTool } =
    await loadBreakGlassRuntime();
  const engaged = process.env.CARR_BREAK_GLASS === "1" &&
    Boolean(process.env.CARR_BREAK_GLASS_REASON && process.env.CARR_BREAK_GLASS_REASON.trim());
  const host = (url.match(/@([^/?]+)/) || [])[1] || "(unparsed)";
  if (!engaged) {
    console.error(
      "BREAK-GLASS REFUSED: the direct-database path requires CARR_BREAK_GLASS=1 and a non-empty " +
      "CARR_BREAK_GLASS_REASON.\n" +
      '  Run through tools/call-verb.py --reason "..." (which sets both and derives the DSN) — see ' +
      "that file's header and tools/db-tap.py's identical envelope.\n" +
      "  The default path (no DATABASE_URL set) talks to the deployed Worker instead and is what " +
      "every ordinary call should use; it does not silently fall back to this path on its own."
    );
    process.exit(2);
  }
  if (!/ep-|neon\.tech/.test(url)) {
    console.error("DATABASE_URL does not look like a Neon url");
    process.exit(2);
  }

  const slug = resolveIdentity();
  const reason = process.env.CARR_BREAK_GLASS_REASON;

  console.error("=".repeat(72));
  console.error("BREAK-GLASS ENGAGED — direct database connection, bypassing the deployed Worker.");
  console.error(`  actor:  ${slug}`);
  console.error(`  reason: ${reason.trim()}`);
  console.error(`  verb:   ${verbName}`);
  console.error(`  host:   ${host}`);
  console.error("=".repeat(72));
  // Logged BEFORE the target ever runs — the attempt is the event this
  // receipt records, not success. Same ordering as tools/db-tap.py.
  appendReceipt(slug, "call-verb", verbName, host, reason);

  const tool = TOOLS[verbName];
  if (!tool) {
    console.error(`unknown verb ${verbName}; known: ${Object.keys(TOOLS).join(", ")}`);
    process.exit(2);
  }
  if (tool.write && url.includes(PRODUCTION_ENDPOINT)) {
    console.error(`⚠ PRODUCTION WRITE — ${verbName} is about to change live records on ${host}.`);
  }

  const pool = new Pool({ connectionString: url });
  const client = await pool.connect();
  try {
    if (!tool.write) {
      // Through executeRegisteredTool, NOT tool.handler directly (loop 353,
      // 2026-08-13, rule a8c55a47): the one choke point that also applies
      // argument type coercion and raw-DB-error translation.
      const out = await executeRegisteredTool(client, { slug, human: true, kind: "human", via: "break-glass/local-verb" }, verbName, verbArgs);
      console.log(JSON.stringify(out, null, 2));
    } else {
      await client.query("begin");
      const a = await client.query("select id, kind from actor where slug=$1", [slug]);
      if (!a.rows.length) throw new Error(`no actor ${slug}`);
      const actor = { slug, human: a.rows[0].kind === "human", kind: a.rows[0].kind, id: a.rows[0].id, via: "break-glass/local-verb" };
      if (tool.humanOnly && !actor.human) throw new Error("human_only verb");
      const out = await executeRegisteredTool(client, actor, verbName, verbArgs);
      await client.query("commit");
      console.log(JSON.stringify(out, null, 2));
    }
  } catch (e) {
    await client.query("rollback").catch(() => {});
    if (e instanceof ToolError) { console.error("TOOL ERROR " + JSON.stringify(e.payload, null, 2)); process.exit(1); }
    console.error(e);
    process.exit(1);
  } finally {
    client.release();
    await pool.end();
  }
}

// ---------------------------------------------------------------------------
// Mode selection: DATABASE_URL present means break-glass was deliberately
// requested (only tools/call-verb.py sets it, and only under its own
// CARR_BREAK_GLASS + --reason gate) — its absence means the ordinary,
// default, HTTPS-to-the-Worker path.
// ---------------------------------------------------------------------------

if (process.env.DATABASE_URL) {
  await runBreakGlass(verb, args, process.env.DATABASE_URL);
} else {
  await runViaWorker(verb, args);
}
