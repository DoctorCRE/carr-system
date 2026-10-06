// V5-F01 — the read runs on a connection whose database actor is the caller.
//
// THE LIVE FAILURE THIS PINS (2026-09-27, after 0732 released): on the Studio,
// `./run.sh call read-record-source-authority {"selector":{"kind":"current_policy"}}`
// as joe-local refused
//   actor_context_mismatch {handler_actor: "joe-local", database_actor: "carr-reader"}.
//
// WHY. mcp.js routes a verb that is neither `write` nor `writerConnection` to
// the READER connection: neon HTTP on the reader login, one statement per
// request, no transaction and no setWriterActorContext. Since 0732,
// ops.f01_context_actor_slug() classifies that login as the carr_reader bundle
// and returns the FIXED slug 'carr-reader' — never the caller, by design, since
// nothing on that connection names one. The F01 store's openOperation then
// requires ops.f01_principal().actor_slug to equal the handler's slug, and it
// cannot. read-cre-lifecycle does not hit this because the J102 door declares
// `writerConnection: true` on every verb, the read included, so mcp.js opens it
// `begin read only` on the writer login and sets carr.acting_actor_slug first.
//
// WHAT THESE CASES DO. They run the verb tools.js actually registers, on the
// route connectionRouteForTool actually picks for it, against a client that
// models the two 0732 derivations: the reader bundle answers 'carr-reader', the
// writer bundle answers whatever the real setWriterActorContext set. The fix
// must reach `allow` by giving the read the caller's actor context, never by
// loosening the equality check — so the strictness cases below still refuse.
//
// The same proof against real roles is in record-source-authority-live-pg.v5.test.mjs.

import assert from "node:assert/strict";
import test from "node:test";

import { TOOLS } from "../src/tools.js";
import { connectionRouteForTool, setWriterActorContext } from "../src/mcp.js";
import { V5_F01_OPERATIONS, v5F01StoreOperationSchemas } from "../src/record-source-authority-store.v5.js";

const READ = "read-record-source-authority";
const SERVER_NOW = "2026-09-27T12:00:00.000Z";

// The machine credential `./run.sh call` presents on the Studio (identity.js
// agentActorForToken, local-token door) — the actor of the live failure.
const JOE_LOCAL = () => ({ id: "00000000-0000-4000-8000-00000000f01a", slug: "joe-local",
  display: "Agent (joe-local)", human: false, agent: true, via: "local-token", client_id: null,
  sponsoring_human_slug: "joe", human_slug: "joe", sponsor_required: false,
  native_agent_verified: true });

const POLICY_BODY = Object.freeze({ kind: "current_policy", policy: null });

/**
 * A database client for one route, modelling 0732's ops.f01_context_actor_slug:
 *   reader            -> the carr_reader bundle: slug 'carr-reader', always;
 *   writer[_read_only] -> the carr_writer bundle: slug = carr.acting_actor_slug,
 *                         which only setWriterActorContext sets.
 * A write statement inside `begin read only` fails as PostgreSQL would.
 */
function routeClient(route) {
  const state = { route, actingSlug: null, began: null, statements: [] };
  const bundle = route === "reader" ? "carr_reader" : "carr_writer";
  const client = {
    state,
    async query(text, params = []) {
      state.statements.push(text);
      const sql = String(text).trim();
      if (/^begin/i.test(sql)) { state.began = sql.toLowerCase(); return { rows: [] }; }
      if (/^(commit|rollback)/i.test(sql)) return { rows: [] };
      if (/set_config\('carr\.acting_actor_slug'/.test(sql)) {
        state.actingSlug = params[0] || null;
        return { rows: [{}] };
      }
      if (/ops\.f01_principal\(\)/.test(sql)) {
        const slug = bundle === "carr_reader" ? "carr-reader" : state.actingSlug;
        if (!slug) throw Object.assign(new Error("f01_no_authenticated_actor"), { code: "28000" });
        return { rows: [{
          principal: { actor_slug: slug, human: false, authorization_class: "sponsored_agent",
            derived_by: "authenticated_database_principal" },
          server_now: SERVER_NOW,
        }] };
      }
      if (/ops\.f01_read\(/.test(sql)) return { rows: [{ body: POLICY_BODY }] };
      if (/^\s*(insert|update|delete)\b/i.test(sql) && state.began === "begin read only") {
        throw Object.assign(new Error("cannot execute in a read-only transaction"), { code: "25006" });
      }
      throw new Error(`routeClient: unexpected statement: ${sql.slice(0, 120)}`);
    },
  };
  return client;
}

/**
 * Run one registered verb the way mcp.js's callTool does for its route: the
 * reader route gets no transaction and no actor context; a writer route opens
 * `begin read only` for a declared read and sets the actor context with the
 * real setWriterActorContext before the handler runs.
 */
async function callOnRoute(route, name, actor, args) {
  const tool = TOOLS[name];
  const client = routeClient(route);
  if (route !== "reader") {
    await client.query(tool.writerConnection && !tool.write ? "begin read only" : "begin");
    await setWriterActorContext(client, actor, { partnerAuthorityAct: tool.humanOnly === true });
  }
  const answer = await tool.handler(client, actor, args);
  return { answer, client };
}

async function refusedWith(promise, code, detail) {
  await assert.rejects(promise, error => {
    const payload = error?.payload ?? {};
    assert.equal(payload.error, code, `expected ${code}, got ${payload.error ?? error?.message}`);
    if (detail) for (const [k, v] of Object.entries(detail)) assert.equal(payload.detail?.[k], v, k);
    return true;
  });
}

test("F01-READER-ROUTE: the reader connection's database actor is 'carr-reader', so the strict check refuses there (the live failure)", async () => {
  await refusedWith(
    callOnRoute("reader", READ, JOE_LOCAL(), { selector: { kind: "current_policy" } }),
    "actor_context_mismatch",
    { handler_actor: "joe-local", database_actor: "carr-reader" });
});

test("F01-READER-ROUTE: read-record-source-authority allows for joe-local on the route mcp.js actually picks for it", async () => {
  const route = connectionRouteForTool(TOOLS[READ]);
  const { answer, client } = await callOnRoute(route, READ, JOE_LOCAL(),
    { selector: { kind: "current_policy" } });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.ok, true);
  assert.equal(answer.actor_slug, "joe-local", "the read is attributed to the caller, not to a bundle");
  assert.equal(answer.effects.database_writes, 0);
  assert.equal(client.state.began, "begin read only",
    "the read runs in a read-only transaction: the writer login gives it an actor, never a write");
  assert.equal(client.state.actingSlug, "joe-local");
});

test("F01-READER-ROUTE: the F01 door routes like the J102 door — reads writer_read_only, writes writer, authority verbs authority", () => {
  const schemas = v5F01StoreOperationSchemas();
  for (const name of V5_F01_OPERATIONS) {
    const tool = TOOLS[name];
    assert.ok(tool, `${name} is registered`);
    const expected = schemas[name].authorityOnly ? "authority"
      : schemas[name].write ? "writer" : "writer_read_only";
    assert.equal(connectionRouteForTool(tool), expected, name);
  }
  // The lifecycle read this mirrors.
  assert.equal(connectionRouteForTool(TOOLS["read-cre-lifecycle"]), "writer_read_only");
  // The helper itself is unchanged: an undeclared read still goes to the reader.
  assert.equal(connectionRouteForTool({ write: false }), "reader");
});

test("F01-READER-ROUTE: the equality check is not relaxed — a writer transaction naming a different actor still refuses the read", async () => {
  const tool = TOOLS[READ];
  const client = routeClient("writer_read_only");
  await client.query("begin read only");
  await setWriterActorContext(client, { ...JOE_LOCAL(), slug: "codex" });
  await refusedWith(
    tool.handler(client, JOE_LOCAL(), { selector: { kind: "current_policy" } }),
    "actor_context_mismatch",
    { handler_actor: "joe-local", database_actor: "codex" });
});
